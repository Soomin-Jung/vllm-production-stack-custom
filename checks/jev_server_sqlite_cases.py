import importlib.util
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "helm" / "examples" / "JEV-27B" / "serve_decide_sqlite.py"
SPEC = importlib.util.spec_from_file_location("serve_decide_sqlite", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


REQUEST = {
    "kind": "choice",
    "state": {"tests": "passed", "risk": "low"},
    "question": "Which route should the agent take?",
    "options": ["review", "proceed"],
    "strategy": "auto",
    "thinking": "off",
    "system2_only": False,
}
RESPONSE = {
    "kind": "choice",
    "effective_kind": "choice",
    "options": ["review", "proceed"],
    "probabilities": [0.1, 0.9],
    "choice_index": 1,
    "choice": "proceed",
    "adaptation": "native",
    "protocol": "jev27-bare-v1",
    "model": "autotrust/JEV-27B",
    "usage": {"prompt_tokens": 32, "completion_tokens": 1, "total_tokens": 33},
    "num_model_requests": 1,
    "elapsed_seconds": 0.12,
}


class OptionTests(unittest.TestCase):
    def test_custom_options_are_removed_before_vllm_parser(self):
        argv = [
            "server.py",
            "--model",
            "/models/JEV-27B",
            "--jev-sqlite-path=/tmp/jev.sqlite3",
            "--jev-sqlite-input-mode",
            "question",
            "--jev-sqlite-busy-timeout-ms",
            "2500",
        ]
        config = MODULE.parse_custom_options(argv)
        self.assertEqual(config.path, Path("/tmp/jev.sqlite3"))
        self.assertEqual(config.input_mode, "question")
        self.assertEqual(config.busy_timeout_ms, 2500)
        self.assertEqual(argv, ["server.py", "--model", "/models/JEV-27B"])

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(SystemExit):
            MODULE.parse_custom_options(
                ["server.py", "--jev-sqlite-input-mode", "everything"]
            )


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "nested" / "jev.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def record(self, mode="metadata", **overrides):
        sink = MODULE.DatabaseLogger(self.db, mode)
        values = {
            "started_at": now(),
            "completed_at": now(),
            "server_elapsed_seconds": 0.13,
            "http_status": 200,
            "client_audit_id": "audit-123",
            "user_agent": "jev-decide-skill/0.3.0",
        }
        values.update(overrides)
        event_id = sink.record(REQUEST, RESPONSE, **values)
        sink.close()
        return event_id

    def row(self):
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM decision_events").fetchone()
        conn.close()
        return row

    def test_database_and_schema_are_created(self):
        event_id = self.record()
        self.assertTrue(self.db.exists())
        row = self.row()
        self.assertEqual(row["event_id"], event_id)
        self.assertEqual(row["kind"], "choice")
        self.assertEqual(row["choice_index"], 1)
        self.assertAlmostEqual(row["max_probability"], 0.9)
        self.assertEqual(json.loads(row["probabilities_json"]), [0.1, 0.9])

    def test_metadata_mode_does_not_retain_text(self):
        self.record("metadata")
        row = self.row()
        self.assertIsNone(row["question"])
        self.assertIsNone(row["options_json"])
        self.assertIsNone(row["request_json"])
        self.assertIsNone(row["response_json"])
        self.assertIsNone(row["choice"])
        self.assertEqual(len(row["question_sha256"]), 64)

    def test_question_mode_retains_question_but_not_state(self):
        self.record("question")
        row = self.row()
        self.assertEqual(row["question"], REQUEST["question"])
        self.assertEqual(json.loads(row["options_json"]), REQUEST["options"])
        self.assertEqual(row["choice"], "proceed")
        self.assertIsNone(row["request_json"])
        self.assertIsNone(row["response_json"])

    def test_full_mode_is_reproducible(self):
        self.record("full")
        row = self.row()
        self.assertEqual(json.loads(row["request_json"]), REQUEST)
        self.assertEqual(json.loads(row["response_json"]), RESPONSE)

    def test_error_is_logged(self):
        self.record(
            http_status=503,
            exception="RuntimeError: engine unavailable",
        )
        row = self.row()
        self.assertEqual(row["outcome"], "error")
        self.assertEqual(row["http_status"], 503)
        self.assertIn("engine unavailable", row["error"])

    def test_daily_and_question_views(self):
        self.record("question")
        conn = sqlite3.connect(self.db)
        daily = conn.execute(
            "SELECT kind, requests, successes FROM jev_usage_daily"
        ).fetchone()
        freq = conn.execute(
            "SELECT sample_question, requests FROM jev_question_frequency"
        ).fetchone()
        conn.close()
        self.assertEqual(daily, ("choice", 1, 1))
        self.assertEqual(freq, (REQUEST["question"], 1))

    def test_concurrent_writes_are_serialized(self):
        sink = MODULE.DatabaseLogger(self.db, "metadata")
        errors = []

        def write_one(i):
            try:
                sink.record(
                    {**REQUEST, "question": f"question-{i}"},
                    RESPONSE,
                    started_at=now(),
                    completed_at=now(),
                    server_elapsed_seconds=0.1,
                    http_status=200,
                    client_audit_id=None,
                    user_agent=None,
                )
            except Exception as exc:  # pragma: no cover - assertion captures it
                errors.append(exc)

        threads = [threading.Thread(target=write_one, args=(i,)) for i in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        sink.close()
        conn = sqlite3.connect(self.db)
        count = conn.execute("SELECT count(*) FROM decision_events").fetchone()[0]
        conn.close()
        self.assertEqual(errors, [])
        self.assertEqual(count, 20)


if __name__ == "__main__":
    unittest.main()
