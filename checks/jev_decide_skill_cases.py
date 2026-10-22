import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "jev-decide" / "scripts" / "decide.py"
SPEC = importlib.util.spec_from_file_location("jev_decide_script", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class Handler(BaseHTTPRequestHandler):
    seen = None
    attempts = 0
    fail_remaining = 0
    authorization = None
    audit_id = None
    user_agent = None

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.reply({"protocol": "jev27-bare-v1"})

    def do_POST(self):
        cls = self.__class__
        cls.attempts += 1
        cls.authorization = self.headers.get("Authorization")
        cls.audit_id = self.headers.get("X-JEV-Audit-ID")
        cls.user_agent = self.headers.get("User-Agent")
        cls.seen = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if cls.fail_remaining:
            cls.fail_remaining -= 1
            self.reply({"error": "temporary"}, status=503)
            return
        self.reply(
            {
                "kind": cls.seen["kind"],
                "options": cls.seen["options"],
                "probabilities": [0.1, 0.9],
                "choice_index": 1,
                "choice": cls.seen["options"][1],
            }
        )

    def reply(self, value, status=200):
        data = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class DecideScriptTest(unittest.TestCase):
    def setUp(self):
        Handler.seen = None
        Handler.attempts = 0
        Handler.fail_remaining = 0
        Handler.authorization = None
        Handler.audit_id = None
        Handler.user_agent = None

    def start_server(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def run_client(self, args, env=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            env={**os.environ, **(env or {})},
            text=True,
            capture_output=True,
            check=False,
        )

    def test_endpoint_normalization(self):
        self.assertEqual(
            MOD.endpoint("https://host/prefix/v1"), "https://host/prefix/v1/decide"
        )
        self.assertEqual(
            MOD.endpoint("https://host/v1/decide", True), "https://host/v1/decide/info"
        )
        with self.assertRaises(MOD.ClientError):
            MOD.endpoint("https://user:pass@host/v1")

    def test_validation_and_retry_bounds(self):
        MOD.validate({"kind": "noul", "question": "Is it true?"})
        with self.assertRaises(MOD.ClientError):
            MOD.validate({"kind": "choice", "question": "Pick", "options": ["one"]})
        with self.assertRaises(MOD.ClientError):
            MOD.validate({"kind": "score", "question": "Score", "model": "wrong"})
        old = os.environ.get("JEV_MAX_RETRIES")
        try:
            os.environ["JEV_MAX_RETRIES"] = "6"
            with self.assertRaises(MOD.ClientError):
                MOD.env_number("JEV_MAX_RETRIES", 2, 0, 5, integer=True)
        finally:
            if old is None:
                os.environ.pop("JEV_MAX_RETRIES", None)
            else:
                os.environ["JEV_MAX_RETRIES"] = old

    def test_roundtrip_audit_hashes_permissions_and_secret_redaction(self):
        server, thread = self.start_server()
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            state_path.write_text('{"tests":"passed"}', encoding="utf-8")
            audit_path = Path(tmp) / "state" / "audit.jsonl"
            env = {
                "JEV_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "JEV_API_KEY": "do-not-log-this-token",
                "JEV_AUDIT_LOG": str(audit_path),
                "JEV_MAX_RETRIES": "0",
            }
            try:
                result = self.run_client(
                    [
                        "--kind",
                        "choice",
                        "--question",
                        "Deploy?",
                        "--option",
                        "hold",
                        "--option",
                        "proceed",
                        "--state-file",
                        str(state_path),
                    ],
                    env,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            self.assertEqual(output["choice"], "proceed")
            self.assertEqual(
                output["_jev_client"]["decision_authority"], "advisory_only"
            )
            self.assertEqual(output["_jev_client"]["attempts"], 1)
            self.assertEqual(Handler.seen["state"], {"tests": "passed"})
            self.assertEqual(Handler.authorization, "Bearer do-not-log-this-token")
            self.assertEqual(Handler.audit_id, output["_jev_client"]["audit_id"])
            self.assertEqual(Handler.user_agent, "jev-decide-skill/0.3.1")

            audit_text = audit_path.read_text(encoding="utf-8")
            self.assertNotIn("do-not-log-this-token", audit_text)
            event = json.loads(audit_text)
            self.assertEqual(event["request"], Handler.seen)
            self.assertEqual(event["response"]["choice"], "proceed")
            self.assertEqual(event["previous_event_hash"], "0" * 64)
            self.assertEqual(
                event["evidence"][0]["sha256"],
                hashlib.sha256(state_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(stat.S_IMODE(audit_path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(audit_path.parent.stat().st_mode), 0o700)

    def test_retries_are_bounded_and_recorded(self):
        Handler.fail_remaining = 2
        server, thread = self.start_server()
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.jsonl"
            env = {
                "JEV_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "JEV_AUDIT_LOG": str(audit_path),
                "JEV_MAX_RETRIES": "2",
                "JEV_RETRY_BACKOFF_SECONDS": "0",
            }
            try:
                result = self.run_client(
                    [
                        "--kind",
                        "choice",
                        "--question",
                        "Route?",
                        "--option",
                        "review",
                        "--option",
                        "route",
                    ],
                    env,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(Handler.attempts, 3)
            self.assertEqual(json.loads(result.stdout)["_jev_client"]["attempts"], 3)
            self.assertEqual(json.loads(audit_path.read_text())["attempts"], 3)

    def test_retry_exhaustion_is_nonzero_and_audited(self):
        Handler.fail_remaining = 99
        server, thread = self.start_server()
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.jsonl"
            env = {
                "JEV_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "JEV_AUDIT_LOG": str(audit_path),
                "JEV_MAX_RETRIES": "1",
                "JEV_RETRY_BACKOFF_SECONDS": "0",
            }
            try:
                result = self.run_client(
                    [
                        "--kind",
                        "choice",
                        "--question",
                        "Pick",
                        "--option",
                        "a",
                        "--option",
                        "b",
                    ],
                    env,
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(Handler.attempts, 2)
            error = json.loads(result.stderr)
            self.assertEqual(error["_jev_client"]["attempts"], 2)
            event = json.loads(audit_path.read_text())
            self.assertEqual(event["outcome"], "error")
            self.assertEqual(event["attempts"], 2)

    def test_audit_chain_across_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit_path = Path(tmp) / "audit.jsonl"
            old = os.environ.get("JEV_AUDIT_LOG")
            os.environ["JEV_AUDIT_LOG"] = str(audit_path)
            try:
                _, first = MOD.append_audit({"audit_id": "first"})
                _, second = MOD.append_audit({"audit_id": "second"})
            finally:
                if old is None:
                    os.environ.pop("JEV_AUDIT_LOG", None)
                else:
                    os.environ["JEV_AUDIT_LOG"] = old
            self.assertEqual(second["previous_event_hash"], first["event_hash"])
            self.assertEqual(len(audit_path.read_text().splitlines()), 2)


if __name__ == "__main__":
    unittest.main()
