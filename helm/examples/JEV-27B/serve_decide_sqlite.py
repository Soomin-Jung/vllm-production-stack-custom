#!/usr/bin/env python3
"""Run AutoTrust's JEV decision server with bounded SQLite usage logging.

This wrapper deliberately leaves the model repository's ``serve_decide.py``
untouched.  It removes its own CLI flags, imports the upstream module, wraps
the already-registered ``POST /v1/decide`` FastAPI route, and then starts the
normal vLLM launcher.

Custom flags:

* ``--jev-sqlite-path PATH``: enable logging and create the DB if necessary.
* ``--jev-sqlite-input-mode metadata|question|full``: control input retention.
* ``--jev-sqlite-busy-timeout-ms N``: SQLite lock wait (default: 5000).
* ``--jev-upstream-script PATH``: upstream server path.

Equivalent environment variables are ``JEV_DECIDE_SQLITE_PATH``,
``JEV_DECIDE_SQLITE_INPUT_MODE``, ``JEV_DECIDE_SQLITE_BUSY_TIMEOUT_MS``, and
``JEV_DECIDE_UPSTREAM``.  CLI values take precedence.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import logging
import math
import os
import sqlite3
import stat
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

LOGGER = logging.getLogger("jev.sqlite")
SCHEMA_VERSION = 1
INPUT_MODES = {"metadata", "question", "full"}
DEFAULT_UPSTREAM = "/models/JEV-27B/serve_decide.py"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _take_option(argv: list[str], name: str, default: str | None = None) -> str | None:
    """Remove one custom option from argv and return its value."""
    found: str | None = None
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg == name:
            if i + 1 >= len(argv) or argv[i + 1].startswith("--"):
                raise SystemExit(f"{name} requires a value")
            if found is not None:
                raise SystemExit(f"{name} may only be supplied once")
            found = argv[i + 1]
            del argv[i : i + 2]
            continue
        if arg.startswith(name + "="):
            if found is not None:
                raise SystemExit(f"{name} may only be supplied once")
            found = arg.split("=", 1)[1]
            del argv[i]
            continue
        i += 1
    return found if found is not None else default


@dataclass(frozen=True)
class Config:
    path: Path | None
    input_mode: str
    busy_timeout_ms: int
    upstream_script: Path


def parse_custom_options(argv: list[str]) -> Config:
    raw_path = _take_option(
        argv, "--jev-sqlite-path", os.environ.get("JEV_DECIDE_SQLITE_PATH")
    )
    input_mode = _take_option(
        argv,
        "--jev-sqlite-input-mode",
        os.environ.get("JEV_DECIDE_SQLITE_INPUT_MODE", "metadata"),
    )
    raw_timeout = _take_option(
        argv,
        "--jev-sqlite-busy-timeout-ms",
        os.environ.get("JEV_DECIDE_SQLITE_BUSY_TIMEOUT_MS", "5000"),
    )
    upstream = _take_option(
        argv,
        "--jev-upstream-script",
        os.environ.get("JEV_DECIDE_UPSTREAM", DEFAULT_UPSTREAM),
    )

    if input_mode not in INPUT_MODES:
        raise SystemExit(
            "--jev-sqlite-input-mode must be one of: metadata, question, full"
        )
    try:
        timeout = int(raw_timeout or "5000")
    except ValueError as exc:
        raise SystemExit("--jev-sqlite-busy-timeout-ms must be an integer") from exc
    if not 0 <= timeout <= 60000:
        raise SystemExit("--jev-sqlite-busy-timeout-ms must be between 0 and 60000")
    if not upstream:
        raise SystemExit("--jev-upstream-script cannot be empty")
    return Config(
        path=Path(raw_path).expanduser().resolve() if raw_path else None,
        input_mode=input_mode,
        busy_timeout_ms=timeout,
        upstream_script=Path(upstream).expanduser().resolve(),
    )


def _state_has_image(value: Any) -> bool:
    if isinstance(value, dict):
        return (
            "image" in value
            or value.get("type") == "image_url"
            or any(_state_has_image(v) for v in value.values())
        )
    if isinstance(value, list):
        return any(_state_has_image(v) for v in value)
    return False


def _response_payload(response: Any) -> tuple[int, dict[str, Any]]:
    status = int(getattr(response, "status_code", 200))
    if isinstance(response, dict):
        return status, response
    body = getattr(response, "body", b"")
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(body) if body else {}
    except json.JSONDecodeError:
        payload = {"raw_body": body}
    return status, payload if isinstance(payload, dict) else {"body": payload}


class DatabaseLogger:
    """Small, process-local SQLite sink for JEV decision usage events."""

    def __init__(self, path: Path, input_mode: str, busy_timeout_ms: int = 5000):
        self.path = path
        self.input_mode = input_mode
        self.busy_timeout_ms = busy_timeout_ms
        self._lock = threading.Lock()
        self._connection: sqlite3.Connection | None = None
        self._open()

    def _open(self) -> None:
        parent_existed = self.path.parent.exists()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not parent_existed:
            os.chmod(self.path.parent, 0o700)
        if self.path.exists():
            mode = self.path.lstat().st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise RuntimeError(f"SQLite path is not a regular file: {self.path}")
        conn = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            check_same_thread=False,
        )
        os.chmod(self.path, 0o600)
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        self._connection = conn
        self._create_schema()

    def _create_schema(self) -> None:
        assert self._connection is not None
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS jev_schema (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS decision_events (
                event_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                client_audit_id TEXT,
                request_sha256 TEXT NOT NULL,
                question_sha256 TEXT NOT NULL,
                state_sha256 TEXT NOT NULL,
                options_sha256 TEXT NOT NULL,
                kind TEXT NOT NULL,
                strategy TEXT,
                thinking_requested TEXT,
                system2_only INTEGER NOT NULL,
                state_type TEXT NOT NULL,
                state_chars INTEGER NOT NULL,
                question_chars INTEGER NOT NULL,
                option_count INTEGER NOT NULL,
                has_image INTEGER NOT NULL,
                input_mode TEXT NOT NULL,
                question TEXT,
                options_json TEXT,
                request_json TEXT,
                http_status INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                model TEXT,
                protocol TEXT,
                effective_kind TEXT,
                adaptation TEXT,
                choice_index INTEGER,
                choice TEXT,
                probabilities_json TEXT,
                max_probability REAL,
                entropy REAL,
                thinking_used INTEGER,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                num_model_requests INTEGER,
                elapsed_seconds REAL,
                server_elapsed_seconds REAL NOT NULL,
                response_json TEXT,
                error TEXT,
                user_agent TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_jev_events_created_at
                ON decision_events(created_at);
            CREATE INDEX IF NOT EXISTS idx_jev_events_kind_created
                ON decision_events(kind, created_at);
            CREATE INDEX IF NOT EXISTS idx_jev_events_question_hash
                ON decision_events(question_sha256);
            CREATE INDEX IF NOT EXISTS idx_jev_events_outcome_created
                ON decision_events(outcome, created_at);

            CREATE VIEW IF NOT EXISTS jev_usage_daily AS
            SELECT
                substr(created_at, 1, 10) AS day_utc,
                kind,
                count(*) AS requests,
                sum(CASE WHEN outcome = 'success' THEN 1 ELSE 0 END) AS successes,
                avg(server_elapsed_seconds) AS avg_server_seconds,
                avg(prompt_tokens) AS avg_prompt_tokens,
                avg(max_probability) AS avg_max_probability,
                sum(CASE WHEN thinking_used = 1 THEN 1 ELSE 0 END) AS thinking_requests
            FROM decision_events
            GROUP BY substr(created_at, 1, 10), kind;

            CREATE VIEW IF NOT EXISTS jev_question_frequency AS
            SELECT
                question_sha256,
                kind,
                min(question) AS sample_question,
                count(*) AS requests,
                max(created_at) AS last_used_at
            FROM decision_events
            GROUP BY question_sha256, kind;
            """
        )
        self._connection.execute(
            "INSERT OR IGNORE INTO jev_schema(version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, _utc_now()),
        )
        self._connection.commit()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def record(
        self,
        request: dict[str, Any],
        response: dict[str, Any],
        *,
        started_at: str,
        completed_at: str,
        server_elapsed_seconds: float,
        http_status: int,
        client_audit_id: str | None,
        user_agent: str | None,
        exception: str | None = None,
    ) -> str:
        state = request.get("state", "")
        question = str(request.get("question", ""))
        options = request.get("options") or []
        probabilities = response.get("probabilities")
        if not isinstance(probabilities, list):
            probabilities = None
        max_probability = max(probabilities) if probabilities else None
        entropy = None
        if probabilities:
            entropy = -sum(float(p) * math.log(float(p)) for p in probabilities if p)
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        thinking = (
            response.get("thinking")
            if isinstance(response.get("thinking"), dict)
            else {}
        )
        error = exception
        if error is None and http_status >= 400:
            error = _canonical(response.get("error", response))[:4096]

        keep_question = self.input_mode in {"question", "full"}
        keep_full = self.input_mode == "full"
        choice = response.get("choice") if keep_question else None
        row = (
            str(uuid.uuid4()),
            started_at,
            completed_at,
            (client_audit_id or "")[:128] or None,
            _sha256(request),
            _sha256(question),
            _sha256(state),
            _sha256(options),
            str(request.get("kind", "unknown")),
            request.get("strategy"),
            request.get("thinking"),
            int(bool(request.get("system2_only"))),
            type(state).__name__,
            len(_canonical(state)),
            len(question),
            len(options),
            int(_state_has_image(state)),
            self.input_mode,
            question if keep_question else None,
            _canonical(options) if keep_question else None,
            _canonical(request) if keep_full else None,
            http_status,
            "success" if http_status < 400 and exception is None else "error",
            response.get("model"),
            response.get("protocol"),
            response.get("effective_kind"),
            response.get("adaptation"),
            response.get("choice_index"),
            choice,
            _canonical(probabilities) if probabilities is not None else None,
            max_probability,
            entropy,
            int(bool(thinking.get("used"))) if thinking else 0,
            usage.get("prompt_tokens"),
            usage.get("completion_tokens"),
            usage.get("total_tokens"),
            response.get("num_model_requests"),
            response.get("elapsed_seconds"),
            server_elapsed_seconds,
            _canonical(response) if keep_full else None,
            (error or "")[:4096] or None,
            (user_agent or "")[:512] or None,
        )
        sql = """
            INSERT INTO decision_events (
                event_id, created_at, completed_at, client_audit_id,
                request_sha256, question_sha256, state_sha256, options_sha256,
                kind, strategy, thinking_requested, system2_only,
                state_type, state_chars, question_chars, option_count, has_image,
                input_mode, question, options_json, request_json,
                http_status, outcome, model, protocol, effective_kind, adaptation,
                choice_index, choice, probabilities_json, max_probability, entropy,
                thinking_used, prompt_tokens, completion_tokens, total_tokens,
                num_model_requests, elapsed_seconds, server_elapsed_seconds,
                response_json, error, user_agent
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        with self._lock:
            if self._connection is None:
                raise RuntimeError("SQLite logger is closed")
            self._connection.execute(sql, row)
            self._connection.commit()
        return row[0]


async def _record_safely(
    sink: DatabaseLogger,
    request: dict[str, Any],
    response: dict[str, Any],
    **kwargs: Any,
) -> None:
    try:
        await asyncio.to_thread(sink.record, request, response, **kwargs)
    except Exception:
        LOGGER.exception("JEV SQLite logging failed; inference response is unaffected")


def _load_upstream(path: Path) -> ModuleType:
    if not path.is_file():
        raise SystemExit(f"JEV upstream script not found: {path}")
    if path.resolve() == Path(__file__).resolve():
        raise SystemExit("--jev-upstream-script must not point to this wrapper")
    spec = importlib.util.spec_from_file_location("jev_decide_upstream", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load JEV upstream script: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _patch_routes(
    module: ModuleType, config: Config, sink: DatabaseLogger | None
) -> None:
    decide_route = next(
        (
            route
            for route in module.router.routes
            if getattr(route, "path", None) == "/v1/decide"
            and "POST" in getattr(route, "methods", set())
        ),
        None,
    )
    info_route = next(
        (
            route
            for route in module.router.routes
            if getattr(route, "path", None) == "/v1/decide/info"
            and "GET" in getattr(route, "methods", set())
        ),
        None,
    )
    if decide_route is None or info_route is None:
        raise SystemExit(
            "upstream JEV routes were not found; check serve_decide.py compatibility"
        )

    decide_dependency = getattr(decide_route, "depend" + "ant")
    info_dependency = getattr(info_route, "depend" + "ant")
    original_decide = decide_dependency.call
    original_info = info_dependency.call

    async def logged_decide(req: Any, raw: Any) -> Any:
        request_body = req.model_dump(mode="json")
        started_at = _utc_now()
        started_clock = asyncio.get_running_loop().time()
        response: Any = None
        try:
            response = await original_decide(req=req, raw=raw)
            return response
        except Exception as exc:
            if sink is not None:
                await _record_safely(
                    sink,
                    request_body,
                    {},
                    started_at=started_at,
                    completed_at=_utc_now(),
                    server_elapsed_seconds=(
                        asyncio.get_running_loop().time() - started_clock
                    ),
                    http_status=500,
                    client_audit_id=raw.headers.get("x-jev-audit-id"),
                    user_agent=raw.headers.get("user-agent"),
                    exception=f"{type(exc).__name__}: {exc}",
                )
            raise
        finally:
            if response is not None and sink is not None:
                status, payload = _response_payload(response)
                await _record_safely(
                    sink,
                    request_body,
                    payload,
                    started_at=started_at,
                    completed_at=_utc_now(),
                    server_elapsed_seconds=(
                        asyncio.get_running_loop().time() - started_clock
                    ),
                    http_status=status,
                    client_audit_id=raw.headers.get("x-jev-audit-id"),
                    user_agent=raw.headers.get("user-agent"),
                )

    async def logged_info() -> Any:
        result = await original_info()
        result["sqlite_logging"] = {
            "enabled": sink is not None,
            "path": str(config.path) if config.path else None,
            "input_mode": config.input_mode,
            "schema_version": SCHEMA_VERSION,
        }
        return result

    # APIRoute's built request handler calls the dependency target. Replacing
    # that target and the endpoint also keeps route introspection accurate.
    decide_dependency.call = logged_decide
    decide_route.endpoint = logged_decide
    info_dependency.call = logged_info
    info_route.endpoint = logged_info


def main() -> None:
    config = parse_custom_options(sys.argv)
    sink = (
        DatabaseLogger(config.path, config.input_mode, config.busy_timeout_ms)
        if config.path
        else None
    )
    if sink:
        LOGGER.info(
            "JEV SQLite logging enabled: path=%s input_mode=%s",
            config.path,
            config.input_mode,
        )
    upstream = _load_upstream(config.upstream_script)
    _patch_routes(upstream, config, sink)
    upstream.entry.main()


if __name__ == "__main__":
    main()
