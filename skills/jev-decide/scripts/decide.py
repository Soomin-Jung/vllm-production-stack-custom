#!/usr/bin/env python3
"""Audited direct client for a self-hosted JEV /v1/decide endpoint."""

import argparse
import hashlib
import json
import math
import os
import ssl
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fallback
    fcntl = None

VERSION = "0.3.0"
FIELDS = {
    "kind",
    "state",
    "question",
    "options",
    "strategy",
    "thinking",
    "threshold",
    "think_budget",
    "reasoning_effort",
    "chat_template_kwargs",
    "return_reasoning",
    "debug",
    "system2_only",
}
KINDS = {"choice", "noul", "score"}
RETRYABLE_HTTP_STATUSES = {408, 425, 429, 500, 502, 503, 504}
ZERO_HASH = "0" * 64


class ClientError(Exception):
    """A safe error that can be returned to the caller."""

    def __init__(self, message, attempts=0):
        super().__init__(message)
        self.attempts = attempts


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Turn redirects into HTTP errors instead of following them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def canonical_json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def sha256_json(value):
    if value is None:
        return None
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def endpoint(base_url, info=False):
    if not base_url:
        raise ClientError("JEV_BASE_URL is required")
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ClientError("JEV_BASE_URL must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ClientError(
            "JEV_BASE_URL must not contain credentials, query, or fragment"
        )
    path = parsed.path.rstrip("/")
    if path.endswith("/v1/decide"):
        path = path[: -len("/v1/decide")]
    elif path.endswith("/v1"):
        path = path[: -len("/v1")]
    suffix = "/v1/decide/info" if info else "/v1/decide"
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, path + suffix, "", "")
    )


def load_json(text, source):
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClientError(f"{source} is not valid JSON: {exc.msg}") from exc


def validate(body):
    if not isinstance(body, dict):
        raise ClientError("request must be a JSON object")
    unknown = sorted(set(body) - FIELDS)
    if unknown:
        raise ClientError("unknown request fields: " + ", ".join(unknown))
    kind = body.get("kind")
    if kind not in KINDS:
        raise ClientError("kind must be choice, noul, or score")
    if not isinstance(body.get("question"), str) or not body["question"].strip():
        raise ClientError("question must be a non-empty string")
    options = body.get("options")
    if kind == "choice":
        if (
            not isinstance(options, list)
            or not 2 <= len(options) <= 256
            or any(not isinstance(item, str) or not item for item in options)
        ):
            raise ClientError("choice requires 2-256 non-empty string options")
    elif options is not None:
        raise ClientError("options is valid only for choice")
    threshold = body.get("threshold")
    if threshold is not None and (
        not isinstance(threshold, (int, float))
        or isinstance(threshold, bool)
        or not math.isfinite(threshold)
    ):
        raise ClientError("threshold must be a finite number or null")
    return body


def ssl_context():
    bundle = os.environ.get("JEV_CA_BUNDLE")
    return (
        ssl.create_default_context(cafile=bundle)
        if bundle
        else ssl.create_default_context()
    )


def opener():
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl_context()),
        urllib.request.HTTPHandler(),
    )


def env_number(name, default, minimum, maximum, integer=False):
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw) if integer else float(raw)
    except ValueError as exc:
        raise ClientError(f"{name} must be numeric") from exc
    if isinstance(value, float) and not math.isfinite(value):
        raise ClientError(f"{name} must be finite")
    if not minimum <= value <= maximum:
        raise ClientError(f"{name} must be between {minimum} and {maximum}")
    return value


def request_json(url, method, body=None):
    timeout = env_number("JEV_TIMEOUT_SECONDS", 60, 0.001, 3600)
    max_retries = env_number("JEV_MAX_RETRIES", 2, 0, 5, integer=True)
    backoff = env_number("JEV_RETRY_BACKOFF_SECONDS", 0.25, 0, 10)
    headers = {"Accept": "application/json"}
    key = os.environ.get("JEV_API_KEY")
    if key:
        headers["Authorization"] = "Bearer " + key
    data = None
    if body is not None:
        data = canonical_json(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    attempts = 0
    for retry_index in range(max_retries + 1):
        attempts += 1
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with opener().open(req, timeout=timeout) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code not in RETRYABLE_HTTP_STATUSES or retry_index == max_retries:
                raise ClientError(
                    f"upstream HTTP {exc.code}: {detail[:2000]}", attempts
                ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if retry_index == max_retries:
                reason = getattr(exc, "reason", exc)
                raise ClientError(
                    f"upstream connection failed: {reason}", attempts
                ) from exc
        else:
            try:
                result = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ClientError("upstream returned invalid JSON", attempts) from exc
            if not isinstance(result, dict):
                raise ClientError(
                    "upstream response must be a JSON object", attempts
                )
            return result, attempts

        if backoff:
            time.sleep(min(backoff * (2**retry_index), 10))

    raise AssertionError("bounded retry loop exited unexpectedly")


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", action="version", version=VERSION)
    p.add_argument("--info", action="store_true")
    p.add_argument("--request-file")
    p.add_argument("--kind", choices=sorted(KINDS))
    p.add_argument("--question")
    p.add_argument("--state-json")
    p.add_argument("--state-file")
    p.add_argument(
        "--evidence-file",
        action="append",
        default=[],
        help="additional file to bind by SHA-256 in the audit event",
    )
    p.add_argument("--option", action="append", dest="options")
    p.add_argument("--strategy", choices=["auto", "single", "tournament", "permute"])
    p.add_argument("--thinking", choices=["default", "off", "auto", "on"])
    return p


def read_file(path_value, label):
    path = Path(path_value).expanduser().resolve()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ClientError(f"cannot read {label}: {exc}") from exc
    manifest = {
        "role": label,
        "path": str(path),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    return data, manifest


def build_body(args):
    evidence = []
    if args.request_file:
        if args.request_file == "-":
            text = sys.stdin.read()
        else:
            raw, entry = read_file(args.request_file, "request_file")
            evidence.append(entry)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ClientError("request file must be UTF-8") from exc
        body = validate(load_json(text, "request file"))
    else:
        if not args.kind or args.question is None:
            raise ClientError("--kind and --question are required")
        if args.state_json is not None and args.state_file is not None:
            raise ClientError("use only one of --state-json and --state-file")
        body = {"kind": args.kind, "question": args.question}
        if args.state_json is not None:
            body["state"] = load_json(args.state_json, "--state-json")
        elif args.state_file is not None:
            raw, entry = read_file(args.state_file, "state_file")
            evidence.append(entry)
            try:
                state_text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ClientError("state file must be UTF-8") from exc
            body["state"] = load_json(state_text, "--state-file")
        if args.options is not None:
            body["options"] = args.options
        if args.strategy is not None:
            body["strategy"] = args.strategy
        if args.thinking is not None:
            body["thinking"] = args.thinking
        body = validate(body)

    seen = {item["path"] for item in evidence}
    for path_value in args.evidence_file:
        _, entry = read_file(path_value, "evidence_file")
        if entry["path"] not in seen:
            evidence.append(entry)
            seen.add(entry["path"])
    return body, evidence


def audit_log_path():
    configured = os.environ.get("JEV_AUDIT_LOG")
    if configured:
        return Path(configured).expanduser().resolve()
    state_root = os.environ.get("XDG_STATE_HOME")
    root = Path(state_root).expanduser() if state_root else Path.home() / ".local/state"
    return (root / "jev-decide" / "audit.jsonl").resolve()


def append_audit(record):
    path = audit_log_path()
    try:
        parent_existed = path.parent.exists()
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not parent_existed:
            os.chmod(path.parent, 0o700)
        elif stat.S_IMODE(path.parent.stat().st_mode) & 0o077:
            raise ClientError(
                f"audit directory permits group/other access: {path.parent}"
            )
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        file_stat = os.fstat(fd)
        if not stat.S_ISREG(file_stat.st_mode):
            os.close(fd)
            raise ClientError(f"audit log is not a regular file: {path}")
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "r+b", buffering=0) as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            last_line = None
            for line in handle:
                if line.strip():
                    last_line = line
            previous_hash = ZERO_HASH
            if last_line is not None:
                try:
                    previous = json.loads(last_line.decode("utf-8"))
                    previous_hash = previous["event_hash"]
                except (UnicodeDecodeError, json.JSONDecodeError, KeyError) as exc:
                    raise ClientError(
                        f"audit log is malformed and was not appended: {path}"
                    ) from exc
            event = dict(record)
            event["previous_event_hash"] = previous_hash
            event["event_hash"] = hashlib.sha256(
                canonical_json(event).encode("utf-8")
            ).hexdigest()
            handle.seek(0, os.SEEK_END)
            handle.write((canonical_json(event) + "\n").encode("utf-8"))
            os.fsync(handle.fileno())
    except ClientError:
        raise
    except OSError as exc:
        raise ClientError(f"cannot write audit log {path}: {exc}") from exc
    return path, event


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def public_metadata(audit_id, request_hash, response_hash, attempts, log_path):
    return {
        "client_version": VERSION,
        "audit_id": audit_id,
        "audit_log": str(log_path),
        "request_sha256": request_hash,
        "response_sha256": response_hash,
        "attempts": attempts,
        "decision_authority": "advisory_only",
        "final_authorization": "external_approval_or_waiver_required",
    }


def audit_record(
    audit_id,
    operation,
    url,
    request_body,
    response_body,
    evidence,
    attempts,
    error,
):
    return {
        "schema_version": 1,
        "client_version": VERSION,
        "timestamp": utc_now(),
        "audit_id": audit_id,
        "operation": operation,
        "endpoint": url,
        "argv": sys.argv[1:],
        "request": request_body,
        "request_sha256": sha256_json(request_body),
        "response": response_body,
        "response_sha256": sha256_json(response_body),
        "evidence": evidence,
        "attempts": attempts,
        "outcome": "error" if error else "success",
        "error": error,
        "decision_authority": "advisory_only",
        "final_authorization": "external_approval_or_waiver_required",
    }


def main():
    args = parser().parse_args()
    audit_id = str(uuid.uuid4())
    operation = "info" if args.info else "decide"
    url = None
    request_body = None
    response_body = None
    evidence = []
    attempts = 0

    try:
        base = os.environ.get("JEV_BASE_URL", "")
        if args.info:
            if (
                args.request_file
                or args.kind
                or args.question
                or args.state_json
                or args.state_file
                or args.evidence_file
            ):
                raise ClientError("--info cannot be combined with decision inputs")
            url = endpoint(base, info=True)
            response_body, attempts = request_json(url, "GET")
        else:
            request_body, evidence = build_body(args)
            url = endpoint(base)
            response_body, attempts = request_json(url, "POST", request_body)

        record = audit_record(
            audit_id,
            operation,
            url,
            request_body,
            response_body,
            evidence,
            attempts,
            None,
        )
        log_path, _ = append_audit(record)
        result = dict(response_body)
        result["_jev_client"] = public_metadata(
            audit_id,
            record["request_sha256"],
            record["response_sha256"],
            attempts,
            log_path,
        )
        json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except (ClientError, OSError) as exc:
        attempts = max(attempts, getattr(exc, "attempts", 0))
        original_error = str(exc)
        record = audit_record(
            audit_id,
            operation,
            url,
            request_body,
            response_body,
            evidence,
            attempts,
            original_error,
        )
        try:
            log_path, _ = append_audit(record)
            message = original_error
        except ClientError as audit_exc:
            log_path = audit_log_path()
            message = f"{original_error}; audit logging also failed: {audit_exc}"
        payload = {
            "error": message,
            "_jev_client": public_metadata(
                audit_id,
                record["request_sha256"],
                record["response_sha256"],
                attempts,
                log_path,
            ),
        }
        json.dump(payload, sys.stderr, ensure_ascii=False, separators=(",", ":"))
        sys.stderr.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
