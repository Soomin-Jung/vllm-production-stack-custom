"""Exercise rendered guardian code against simulated Kubernetes API failures.

Run with Python 3.10+ and Helm on PATH; only stdlib dependencies are required.
"""

import contextlib
import io
import json
import os
import ssl
import subprocess
import tempfile
import types
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = subprocess.check_output(
    [
        "helm",
        "template",
        "diagnostic-test",
        str(ROOT / "helm"),
        "-n",
        "inference",
        "-f",
        str(ROOT / "helm/examples/pd-cell-values.yaml"),
        "--show-only",
        "templates/configmap-pd-cell-guardian.yaml",
    ],
    text=True,
)
CODE = MANIFEST.split("  guardian.py: |\n", 1)[1]
# The rendered block is the final YAML scalar in this ConfigMap.
CODE = "\n".join(line[4:] for line in CODE.splitlines()) + "\n"
PREFIX, MAIN = CODE.split("if not TARGETS:\n", 1)
MAIN = "if not TARGETS:\n" + MAIN


class StopGuardian(Exception):
    pass


def container(name, count=0, ready=True, terminated=None, current=False):
    return {
        "name": name,
        "restartCount": count,
        "ready": ready,
        "state": {"terminated": terminated} if current else {"running": {}},
        "lastState": {"terminated": terminated} if terminated and not current else {},
    }


def pod(*statuses):
    return {
        "metadata": {"uid": "uid-test"},
        "spec": {"nodeName": "h200-node-3"},
        "status": {"phase": "Running", "containerStatuses": list(statuses)},
    }


class GuardianDiagnosticsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        environment = {
            "POD_NAME": "cell-abc",
            "POD_NAMESPACE": "inference",
            "POD_UID": "uid-test",
            "NODE_NAME": "h200-node-3",
            "PD_GUARDIAN_TARGETS": "pd-router,prefill-0,decode-0",
            "PD_GUARDIAN_LOG_DIR": self.directory.name,
        }
        self.scope = {}
        with (
            patch.dict(os.environ, environment),
            patch.object(ssl, "create_default_context"),
        ):
            exec(compile(PREFIX, "guardian.py", "exec"), self.scope)
        self.calls = []
        self.output = io.StringIO()
        self.redirect = contextlib.redirect_stdout(self.output)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)
        self.scope["request"] = self.request

    def request(self, method, payload=None, **kwargs):
        self.calls.append((method, payload, kwargs))
        if "/log?" in kwargs.get("url", ""):
            return b"2026-10-03T12:00:00Z CUDA out of memory\n"
        if "/events?" in kwargs.get("url", ""):
            return {
                "items": [
                    {
                        "involvedObject": {
                            "uid": "uid-test",
                            "fieldPath": "spec.containers{decode-0}",
                        },
                        "reason": "Killing",
                        "message": "Container failed liveness probe, will be restarted",
                        "lastTimestamp": "2026-10-03T12:00:00Z",
                    },
                    {"involvedObject": {"uid": "other-pod"}, "reason": "Unrelated"},
                ]
            }
        return {}

    def records(self):
        path = Path(self.directory.name) / "inference_cell-abc_uid-test.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()]

    def terminate(self, reason="OOMKilled", code=137):
        return {
            "reason": reason,
            "exitCode": code,
            "signal": 9,
            "finishedAt": "2026-10-03T12:00:00Z",
            "containerID": "containerd://old",
        }

    def run_loop(self, pods, stop_after=100):
        snapshots = iter(pods)
        sleeps = 0

        def sleep(seconds):
            nonlocal sleeps
            sleeps += 1
            if seconds == 3600 or sleeps >= stop_after:
                raise StopGuardian()

        self.scope["get_pod"] = lambda: next(snapshots)
        self.scope["time"] = types.SimpleNamespace(sleep=sleep, monotonic=lambda: 0)
        with self.assertRaises(StopGuardian):
            exec(compile(MAIN, "guardian.py", "exec"), self.scope)

    def test_all_failed_containers_are_persisted_once_with_previous_logs(self):
        snapshot = pod(
            container("decode-0", 1, terminated=self.terminate()),
            container("prefill-0", 2, terminated=self.terminate("Error", 1)),
            container("pd-router"),
        )
        self.scope["diagnose_failures"](snapshot)
        self.scope["diagnose_failures"](snapshot)
        records = self.records()
        failures = [r for r in records if r["event"] == "container_failure_detected"]
        self.assertEqual({r["container"] for r in failures}, {"decode-0", "prefill-0"})
        self.assertEqual(len(failures), 2)
        self.assertEqual(failures[0]["reason"], "OOMKilled")
        self.assertEqual(failures[0]["exit_code"], 137)
        self.assertTrue(
            all(r["node"] == "h200-node-3" and r["pod"] == "cell-abc" for r in records)
        )
        logs = [r for r in records if r["event"] == "container_log_snapshot"]
        self.assertEqual(len(logs), 2)
        self.assertIn("CUDA out of memory", logs[0]["log_tail"])
        for _, _, kwargs in self.calls[:2]:
            self.assertIn("previous=true", kwargs["url"])
            self.assertIn("tailLines=200", kwargs["url"])
            self.assertIn("limitBytes=65536", kwargs["url"])
            self.assertIn("timestamps=true", kwargs["url"])
        events = next(r for r in records if r["event"] == "pod_event_snapshot")
        self.assertEqual(len(events["events"]), 1)
        self.assertIn("liveness probe", events["events"][0]["message"])
        self.assertIn('"event":"container_failure_detected"', self.output.getvalue())

    def test_current_terminated_instance_and_truncation(self):
        self.scope["request"] = lambda *a, **k: (
            b"x" * 70000 if "/log?" in k.get("url", "") else {"items": []}
        )
        self.scope["diagnose_failures"](
            pod(
                container(
                    "decode-0", ready=False, terminated=self.terminate(), current=True
                )
            )
        )
        record = next(
            r for r in self.records() if r["event"] == "container_log_snapshot"
        )
        self.assertEqual(record["log_source"], "current")
        self.assertEqual(len(record["log_tail"]), 65536)
        self.assertTrue(record["at_limit"])

    def test_startup_crashloop_is_logged_without_recycle_or_duplicate_capture(self):
        snapshot = pod(
            container("pd-router"),
            container("prefill-0"),
            container("decode-0", 1, False, self.terminate()),
        )
        self.run_loop([snapshot, snapshot], stop_after=2)
        self.assertFalse(any(method == "DELETE" for method, _, _ in self.calls))
        self.assertEqual(
            sum(r["event"] == "container_failure_detected" for r in self.records()), 1
        )

    def test_log_errors_do_not_block_armed_recycle_and_uid_precondition(self):
        original = self.request

        def fail_logs(method, payload=None, **kwargs):
            if "/log?" in kwargs.get("url", ""):
                self.calls.append((method, payload, kwargs))
                raise urllib.error.HTTPError(kwargs["url"], 403, "Forbidden", {}, None)
            return original(method, payload, **kwargs)

        self.scope["request"] = fail_logs
        healthy = pod(
            container("pd-router"), container("prefill-0"), container("decode-0")
        )
        failed = pod(
            container("pd-router"),
            container("prefill-0"),
            container("decode-0", 1, False, self.terminate()),
        )
        self.run_loop([healthy, failed])
        deletion = next(
            payload for method, payload, _ in self.calls if method == "DELETE"
        )
        self.assertEqual(deletion["preconditions"], {"uid": "uid-test"})
        self.assertEqual(self.calls[-1][0], "DELETE")
        record = next(
            r for r in self.records() if r["event"] == "container_log_unavailable"
        )
        self.assertEqual(record["http_status"], 403)
        recycle = next(
            r for r in self.records() if r["event"] == "whole_cell_recycle_requested"
        )
        self.assertEqual(recycle["trigger_container"], "decode-0")
        self.assertEqual(recycle["pod"], "cell-abc")

    def test_budget_exhaustion_records_unavailability_and_still_deletes(self):
        healthy = pod(
            container("pd-router"), container("prefill-0"), container("decode-0")
        )
        failed = pod(
            container("pd-router"),
            container("prefill-0"),
            container("decode-0", 1, True, self.terminate()),
        )
        self.scope["get_pod"] = iter([healthy, failed]).__next__

        def sleep(seconds):
            if seconds == 3600:
                raise StopGuardian()

        clock = iter([0, 11, 11])
        self.scope["time"] = types.SimpleNamespace(
            sleep=sleep, monotonic=lambda: next(clock)
        )
        with self.assertRaises(StopGuardian):
            exec(compile(MAIN, "guardian.py", "exec"), self.scope)
        self.assertEqual(self.calls[-1][0], "DELETE")
        self.assertIn(
            "budget exhausted",
            next(
                r for r in self.records() if r["event"] == "container_log_unavailable"
            )["error"],
        )

    def test_dirty_startup_recycles_only_after_full_readiness(self):
        starting = pod(
            container("pd-router"),
            container("prefill-0"),
            container("decode-0", 1, False, self.terminate()),
        )
        recovered = pod(
            container("pd-router"),
            container("prefill-0"),
            container("decode-0", 1, True, self.terminate()),
        )
        self.run_loop([starting, recovered])
        records = self.records()
        self.assertEqual(
            sum(r["event"] == "container_failure_detected" for r in records), 1
        )
        self.assertFalse(any(r["event"] == "guardian_armed" for r in records))
        recycle = next(
            r for r in records if r["event"] == "whole_cell_recycle_requested"
        )
        self.assertIn("before initial full readiness", recycle["reason"])
        self.assertEqual(self.calls[-1][0], "DELETE")

    def test_replacement_uid_is_never_diagnosed_or_deleted(self):
        replaced = pod(container("decode-0", 1, False, self.terminate()))
        replaced["metadata"]["uid"] = "replacement-uid"
        self.scope["get_pod"] = lambda: replaced
        with self.assertRaises(SystemExit) as raised:
            exec(compile(MAIN, "guardian.py", "exec"), self.scope)
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(self.calls, [])
        self.assertTrue(any(r["event"] == "pod_uid_changed" for r in self.records()))

    def test_healthy_pod_does_not_fetch_logs_or_events(self):
        self.scope["diagnose_failures"](
            pod(container("decode-0"), container("pd-router"))
        )
        self.assertEqual(self.calls, [])


class GpuReservationLogTest(unittest.TestCase):
    def test_reservation_start_ready_and_failure_identify_node(self):
        manifest = subprocess.check_output(
            [
                "helm",
                "template",
                "diagnostic-test",
                str(ROOT / "helm"),
                "-n",
                "inference",
                "-f",
                str(ROOT / "helm/examples/pd-cell-values.yaml"),
                "--show-only",
                "templates/deployment-pd-cell.yaml",
            ],
            text=True,
        )
        reservation = manifest.split("        - name: gpu-reservation", 1)[1].split(
            "        - name: pd-router", 1
        )[0]
        self.assertIn("fieldPath: spec.nodeName", reservation)
        script = reservation.split("            - |\n", 1)[1].split(
            "          env:", 1
        )[0]
        script = "\n".join(line[14:] for line in script.splitlines())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            smi = root / "nvidia-smi"
            smi.write_text(
                "#!/bin/bash\nprintf '%s\\n' '0000:02:00.0, GPU-B' '0000:01:00.0, GPU-A'\n"
            )
            smi.chmod(0o755)
            sleep = root / "sleep"
            sleep.write_text("#!/bin/bash\nexit 0\n")
            sleep.chmod(0o755)
            script = script.replace("/var/run/pd-gpu", str(root / "map"))
            environment = {
                **os.environ,
                "PATH": str(root) + ":" + os.environ["PATH"],
                "NODE_NAME": "actual-h200-host",
                "POD_NAME": "cell-abc",
                "POD_NAMESPACE": "inference",
                "POD_UID": "uid-test",
                "PD_GPU_TOTAL": "2",
            }
            result = subprocess.run(
                ["bash", "-ceu", script],
                env=environment,
                text=True,
                capture_output=True,
                timeout=5,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("reservation starting: node=actual-h200-host", result.stdout)
            self.assertIn("reservation ready: node=actual-h200-host", result.stdout)
            self.assertIn("pod=inference/cell-abc uid=uid-test", result.stdout)
            self.assertIn("gpus=GPU-A,GPU-B", result.stdout)
            environment["PD_GPU_TOTAL"] = "3"
            result = subprocess.run(
                ["bash", "-ceu", script],
                env=environment,
                text=True,
                capture_output=True,
                timeout=5,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("reservation starting: node=actual-h200-host", result.stdout)
            self.assertIn("reservation mismatch", result.stderr)


if __name__ == "__main__":
    unittest.main()
