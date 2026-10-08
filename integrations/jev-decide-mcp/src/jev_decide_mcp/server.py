"""MCP tools over stdio or authenticated Streamable HTTP."""

import argparse
import asyncio
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, Tool, ToolAnnotations
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from .client import BackendError, DecideClient, Settings
from .contract import DecideRequest

INSTRUCTIONS = (
    "Use jev_decide for a bounded decision over supplied evidence: classify a CI failure, "
    "check a claim against evidence, select a known candidate, or score with a defined 0–5 rubric. "
    "Supply evidence as state and a precise question; include mutually exclusive choice options "
    "and an uncertain/other option when appropriate. Start with thinking='off' for System 1. "
    "Preserve option order. noul probabilities are [P(false), P(true)]; score probabilities are "
    "for 0..5; choice is the modal score, not the expected score. Call jev_decide_info to inspect "
    "backend capabilities. Low confidence means review/escalate, not guaranteed correctness. "
    "This tool makes a decision only; the host owns execution and policy."
)


def result(data: dict[str, Any], *, error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[
            TextContent(
                type="text", text=json.dumps(data, ensure_ascii=False, allow_nan=False)
            )
        ],
        structuredContent=data,
        isError=error,
    )


def create_server(client: DecideClient) -> Server:
    server = Server("jev-decide-mcp", version="0.1.0", instructions=INSTRUCTIONS)
    annotations = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=True,
    )

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="jev_decide",
                description=(
                    "Ask self-hosted JEV /v1/decide. kind=choice selects among 2–256 options; "
                    "noul checks a proposition with [false,true]; score applies a 0–5 rubric. "
                    "Returns the backend JSON unchanged, including ordered probabilities, "
                    "choice_index, usage, latency and optional thinking. Omitted controls use "
                    "backend defaults. Use thinking=off for a fast System 1 decision."
                ),
                inputSchema=DecideRequest.model_json_schema(),
                annotations=annotations,
            ),
            Tool(
                name="jev_decide_info",
                description="Read /v1/decide/info: protocol, option limits and backend defaults.",
                inputSchema={
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                annotations=annotations,
            ),
        ]

    @server.call_tool(validate_input=False)
    async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
        try:
            if name == "jev_decide":
                return result(
                    await client.decide(DecideRequest.model_validate(arguments))
                )
            if name == "jev_decide_info":
                if arguments:
                    raise ValueError("jev_decide_info accepts no arguments")
                return result(await client.info())
            raise ValueError("Unknown tool")
        except ValidationError as exc:
            # Exclude the supplied evidence and non-serializable validator context.
            detail = [
                {"path": list(e["loc"]), "message": e["msg"]} for e in exc.errors()
            ]
            return result(
                {
                    "error": {
                        "code": "invalid_arguments",
                        "message": "Invalid JEV request",
                        "details": detail,
                    }
                },
                error=True,
            )
        except ValueError as exc:
            return result(
                {"error": {"code": "invalid_arguments", "message": str(exc)}},
                error=True,
            )
        except BackendError as exc:
            return result(exc.as_dict(), error=True)

    return server


class BearerAuth:
    def __init__(self, app: ASGIApp, token: str | None):
        self.app = app
        self.expected = f"Bearer {token}".encode() if token else None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and self.expected is not None:
            supplied = dict(scope.get("headers", [])).get(b"authorization", b"")
            if not hmac.compare_digest(supplied, self.expected):
                await JSONResponse(
                    {"error": "Unauthorized"},
                    status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )(scope, receive, send)
                return
        await self.app(scope, receive, send)


class MCPHandler:
    def __init__(self, manager: StreamableHTTPSessionManager):
        self.manager = manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.manager.handle_request(scope, receive, send)


def create_http_app(client: DecideClient, *, token: str | None = None) -> ASGIApp:
    hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    hosts += [
        v.strip()
        for v in os.environ.get("JEV_MCP_ALLOWED_HOSTS", "").split(",")
        if v.strip()
    ]
    origins = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]
    origins += [
        v.strip()
        for v in os.environ.get("JEV_MCP_ALLOWED_ORIGINS", "").split(",")
        if v.strip()
    ]
    manager = StreamableHTTPSessionManager(
        create_server(client),
        stateless=True,
        json_response=True,
        security_settings=TransportSecuritySettings(
            allowed_hosts=hosts, allowed_origins=origins
        ),
    )

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with client, manager.run():
            yield

    app = Starlette(
        routes=[Route("/mcp", MCPHandler(manager), methods=["GET", "POST", "DELETE"])],
        lifespan=lifespan,
    )
    return BearerAuth(app, token)


async def run_stdio(settings: Settings) -> None:
    async with DecideClient(settings) as client, stdio_server() as (read, write):
        server = create_server(client)
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--transport", choices=["stdio", "streamable-http"], default="stdio"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    # MCP JSON-RPC owns stdout. All operational logs go to stderr.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        parser.error(str(exc))
    if args.transport == "stdio":
        asyncio.run(run_stdio(settings))
    else:
        token = os.environ.get("JEV_MCP_TOKEN") or None
        if args.host not in ("127.0.0.1", "localhost", "::1") and not token:
            parser.error("JEV_MCP_TOKEN is required when listening beyond loopback")
        uvicorn.run(
            create_http_app(DecideClient(settings), token=token),
            host=args.host,
            port=args.port,
            access_log=False,
        )


if __name__ == "__main__":
    main()
