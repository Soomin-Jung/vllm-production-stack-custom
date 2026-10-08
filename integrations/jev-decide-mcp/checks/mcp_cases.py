import asyncio
import json
import os
import socket
import sys
import threading
from contextlib import asynccontextmanager
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import uvicorn
from contract_cases import decision
from jev_decide_mcp.client import DecideClient, Settings
from jev_decide_mcp.server import BearerAuth, create_http_app
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


class BackendHandler(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        assert self.path == "/v1/decide/info"
        self.reply(200, {"protocol": "jev27-bare-v1", "max_options": 256})

    def do_POST(self):
        assert self.path == "/v1/decide"
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        self.seen.append(body)
        if body["question"] == "backend-error":
            self.reply(422, {"error": {"message": "context too long"}})
        else:
            self.reply(200, decision(body))

    def reply(self, status, value):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


async def exercise_session(session):
    initialized = await session.initialize()
    assert initialized.serverInfo.name == "jev-decide-mcp"
    assert "supplied evidence" in initialized.instructions
    listed = await session.list_tools()
    assert {t.name for t in listed.tools} == {"jev_decide", "jev_decide_info"}
    schema = next(t.inputSchema for t in listed.tools if t.name == "jev_decide")
    assert "model" not in schema["properties"]
    assert "system2_only" in schema["properties"]
    info = await session.call_tool("jev_decide_info", {})
    assert not info.isError
    assert info.structuredContent["max_options"] == 256
    for kind in ("choice", "noul", "score"):
        body = {"kind": kind, "question": "q", "state": {"로그": "actual evidence"}}
        if kind == "choice":
            body["options"] = ["first", "last"]
        result = await session.call_tool("jev_decide", body)
        assert not result.isError
        assert result.structuredContent == decision(body)
        assert json.loads(result.content[0].text) == decision(body)
    bad = await session.call_tool(
        "jev_decide", {"kind": "choice", "question": "q", "options": ["one"]}
    )
    assert bad.isError
    assert bad.structuredContent["error"]["code"] == "invalid_arguments"
    assert (await session.call_tool("unknown", {})).isError
    assert (await session.call_tool("jev_decide_info", {"unexpected": 1})).isError


async def test_real_stdio_subprocess_roundtrip(capfd):
    BackendHandler.seen = []
    backend = ThreadingHTTPServer(("127.0.0.1", 0), BackendHandler)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "jev_decide_mcp.server"],
        env={**os.environ, "JEV_BASE_URL": f"http://127.0.0.1:{backend.server_port}"},
    )
    try:
        async with (
            stdio_client(parameters) as (read, write),
            ClientSession(read, write) as session,
        ):
            await exercise_session(session)
            failed = await session.call_tool(
                "jev_decide", {"kind": "noul", "question": "backend-error"}
            )
            assert failed.isError
            assert failed.structuredContent["error"]["http_status"] == 422
            assert (
                failed.structuredContent["error"]["upstream"]["error"]["message"]
                == "context too long"
            )
    finally:
        await asyncio.to_thread(backend.shutdown)
        backend.server_close()
        thread.join(timeout=2)
    assert len(BackendHandler.seen) == 4  # invalid inputs never reach HTTP
    captured = capfd.readouterr()
    assert "actual evidence" not in captured.err
    assert "context too long" not in captured.err
    assert not captured.out  # MCP stdout belongs to the SDK reader


@asynccontextmanager
async def running_http(app):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        async with asyncio.timeout(5):
            while not server.started:
                if task.done():
                    await task
                await asyncio.sleep(0.01)
        yield f"http://127.0.0.1:{sock.getsockname()[1]}/mcp"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        sock.close()


async def test_streamable_http_authenticated_roundtrip_and_dns_protection():
    def backend(req):
        if req.method == "GET":
            return httpx.Response(
                200, json={"protocol": "jev27-bare-v1", "max_options": 256}
            )
        return httpx.Response(200, json=decision(json.loads(req.content)))

    client = DecideClient(
        Settings("http://backend"), transport=httpx.MockTransport(backend)
    )
    async with running_http(create_http_app(client, token="test-token")) as url:
        async with httpx.AsyncClient(trust_env=False) as http:
            assert (await http.post(url, json={})).status_code == 401
            assert (
                await http.post(url, headers={"Authorization": "Bearer wrong"}, json={})
            ).status_code == 401
            blocked = await http.post(
                url,
                headers={
                    "Authorization": "Bearer test-token",
                    "Host": "evil.example",
                    "Accept": "application/json, text/event-stream",
                },
                json={},
            )
            assert blocked.status_code == 421
        async with httpx.AsyncClient(
            headers={"Authorization": "Bearer test-token"}, trust_env=False
        ) as http:
            async with (
                streamable_http_client(url, http_client=http) as (read, write, _),
                ClientSession(
                    read, write, read_timeout_seconds=timedelta(seconds=5)
                ) as session,
            ):
                await exercise_session(session)


async def test_auth_protects_all_http_methods():
    reached = []

    async def app(scope, receive, send):
        reached.append(scope)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=BearerAuth(app, "secret")),
        base_url="http://localhost",
    ) as http:
        for method in ("GET", "POST", "DELETE", "OPTIONS"):
            assert (await http.request(method, "/mcp")).status_code == 401
    assert reached == []
