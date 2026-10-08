#!/usr/bin/env python3
"""Direct client for a self-hosted JEV /v1/decide endpoint."""

import argparse
import json
import math
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

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


class ClientError(Exception):
    pass


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
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ClientError(f"{source} is not valid JSON: {exc.msg}") from exc
    return value


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
            or any(not isinstance(item, str) for item in options)
        ):
            raise ClientError("choice requires 2-256 string options")
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
        urllib.request.HTTPSHandler(context=ssl_context()),
        urllib.request.HTTPHandler(),
    )


def request_json(url, method, body=None):
    headers = {"Accept": "application/json"}
    key = os.environ.get("JEV_API_KEY")
    if key:
        headers["Authorization"] = "Bearer " + key
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        headers["Content-Type"] = "application/json"
    try:
        timeout = float(os.environ.get("JEV_TIMEOUT_SECONDS", "60"))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError
    except ValueError as exc:
        raise ClientError(
            "JEV_TIMEOUT_SECONDS must be a positive finite number"
        ) from exc
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with opener().open(req, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise ClientError(f"upstream HTTP {exc.code}: {detail[:2000]}") from exc
    except urllib.error.URLError as exc:
        raise ClientError(f"upstream connection failed: {exc.reason}") from exc
    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClientError("upstream returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise ClientError("upstream response must be a JSON object")
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--info", action="store_true")
    p.add_argument("--request-file")
    p.add_argument("--kind", choices=sorted(KINDS))
    p.add_argument("--question")
    p.add_argument("--state-json")
    p.add_argument("--state-file")
    p.add_argument("--option", action="append", dest="options")
    p.add_argument("--strategy", choices=["auto", "single", "tournament", "permute"])
    p.add_argument("--thinking", choices=["default", "off", "auto", "on"])
    return p


def build_body(args):
    if args.request_file:
        text = (
            sys.stdin.read()
            if args.request_file == "-"
            else Path(args.request_file).read_text()
        )
        return validate(load_json(text, "request file"))
    if not args.kind or args.question is None:
        raise ClientError("--kind and --question are required")
    if args.state_json is not None and args.state_file is not None:
        raise ClientError("use only one of --state-json and --state-file")
    body = {"kind": args.kind, "question": args.question}
    if args.state_json is not None:
        body["state"] = load_json(args.state_json, "--state-json")
    elif args.state_file is not None:
        body["state"] = load_json(Path(args.state_file).read_text(), "--state-file")
    if args.options is not None:
        body["options"] = args.options
    if args.strategy is not None:
        body["strategy"] = args.strategy
    if args.thinking is not None:
        body["thinking"] = args.thinking
    return validate(body)


def main():
    args = parser().parse_args()
    try:
        base = os.environ.get("JEV_BASE_URL", "")
        if args.info:
            if args.request_file or args.kind or args.question:
                raise ClientError("--info cannot be combined with a decision request")
            result = request_json(endpoint(base, info=True), "GET")
        else:
            result = request_json(endpoint(base), "POST", build_body(args))
        json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
        return 0
    except (ClientError, OSError) as exc:
        json.dump({"error": str(exc)}, sys.stderr, ensure_ascii=False)
        sys.stderr.write("\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
