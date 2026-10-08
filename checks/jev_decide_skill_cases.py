import importlib.util
import json
import os
import subprocess
import sys
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

    def log_message(self, *args):
        pass

    def do_GET(self):
        self.reply({"protocol": "jev27-bare-v1"})

    def do_POST(self):
        self.__class__.seen = json.loads(
            self.rfile.read(int(self.headers["Content-Length"]))
        )
        self.reply(
            {
                "kind": self.seen["kind"],
                "options": self.seen["options"],
                "probabilities": [0.1, 0.9],
                "choice_index": 1,
                "choice": self.seen["options"][1],
            }
        )

    def reply(self, value):
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class DecideScriptTest(unittest.TestCase):
    def test_endpoint_normalization(self):
        self.assertEqual(MOD.endpoint("https://host/prefix/v1"), "https://host/prefix/v1/decide")
        self.assertEqual(MOD.endpoint("https://host/v1/decide", True), "https://host/v1/decide/info")
        with self.assertRaises(MOD.ClientError):
            MOD.endpoint("https://user:pass@host/v1")

    def test_validation(self):
        MOD.validate({"kind": "noul", "question": "Is it true?"})
        with self.assertRaises(MOD.ClientError):
            MOD.validate({"kind": "choice", "question": "Pick", "options": ["one"]})
        with self.assertRaises(MOD.ClientError):
            MOD.validate({"kind": "score", "question": "Score", "model": "wrong"})

    def test_subprocess_roundtrip(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        env = {**os.environ, "JEV_BASE_URL": f"http://127.0.0.1:{server.server_port}"}
        try:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--kind",
                    "choice",
                    "--question",
                    "Deploy?",
                    "--option",
                    "hold",
                    "--option",
                    "proceed",
                    "--state-json",
                    '{"tests":"passed"}',
                ],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["choice"], "proceed")
        self.assertEqual(Handler.seen["state"], {"tests": "passed"})

    def test_failure_is_nonzero_json_stderr(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--kind", "choice", "--question", "Pick"],
            env={**os.environ, "JEV_BASE_URL": "http://127.0.0.1:1"},
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("error", json.loads(result.stderr))
        self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
