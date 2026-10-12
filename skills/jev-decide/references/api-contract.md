# JEV /v1/decide contract

This skill calls the configured self-hosted endpoint directly. It does not load the model, proxy through MCP, discover another provider, or follow redirects.

## Request

| Field | Type | Notes |
| --- | --- | --- |
| `kind` | `choice`, `noul`, `score` | Required |
| `question` | string | Required, non-empty |
| `state` | string, object, list | Optional evidence |
| `options` | string array | Required only for `choice`; 2-256 entries |
| `strategy` | `auto`, `single`, `tournament`, `permute` | Optional |
| `thinking` | `default`, `off`, `auto`, `on` | Optional |
| `threshold` | finite number or null | Optional |
| `think_budget` | integer or null | Optional |
| `reasoning_effort` | string or null | Optional |
| `chat_template_kwargs` | object or null | Optional |
| `return_reasoning` | boolean | Optional |
| `debug` | boolean | Optional |
| `system2_only` | boolean | Optional |

Do not send a `model` field. The server owns model selection. Advanced fields depend on the deployed `serve_decide.py` revision; query `--info` and record that revision.

The client validates required fields and option count and rejects unknown request fields. It does not assert every deployment-specific advanced-field constraint.

## Response

| Kind | Ordered options | Selected value |
| --- | --- | --- |
| `choice` | Request option order | Maximum-probability option |
| `noul` | `["false", "true"]` | `"false"` or `"true"` |
| `score` | `["0", "1", "2", "3", "4", "5"]` | Modal score string |

The client preserves backend fields and adds `_jev_client`:

```json
{
  "client_version": "0.3.0",
  "audit_id": "UUID",
  "audit_log": "/home/user/.local/state/jev-decide/audit.jsonl",
  "request_sha256": "hex SHA-256 or null",
  "response_sha256": "hex SHA-256",
  "attempts": 1,
  "decision_authority": "advisory_only",
  "final_authorization": "external_approval_or_waiver_required"
}
```

The response hash covers the canonical raw backend JSON before `_jev_client` is added.

## Script interface

```text
decide.py --kind KIND --question TEXT [--state-json JSON | --state-file PATH]
          [--option TEXT ...] [--thinking MODE] [--strategy MODE]
          [--evidence-file PATH ...]
decide.py --request-file PATH [--evidence-file PATH ...]
decide.py --request-file - [--evidence-file PATH ...]
decide.py --info
```

Successful responses are JSON on stdout. Validation, HTTP, TLS, timeout, redirect, audit, or malformed-JSON errors are JSON on stderr and return a nonzero exit code.

Endpoint normalization accepts a base URL with no suffix, `/v1`, or `/v1/decide`. A URL containing credentials, query parameters, or fragments is rejected. Proxy environment variables are ignored.

## Retry policy

The loop is structurally bounded to `1 + JEV_MAX_RETRIES` attempts.

| Variable | Default | Accepted range |
| --- | ---: | ---: |
| `JEV_TIMEOUT_SECONDS` | 60 | 0.001-3600 |
| `JEV_MAX_RETRIES` | 2 | integer 0-5 |
| `JEV_RETRY_BACKOFF_SECONDS` | 0.25 | 0-10 |

Retry only connection failures and HTTP `408`, `425`, `429`, `500`, `502`, `503`, and `504`. Use exponential backoff capped at 10 seconds. Do not retry request validation failures, non-retryable HTTP statuses, redirects, invalid JSON, or non-object responses.

## Audit log

The default path is `$XDG_STATE_HOME/jev-decide/audit.jsonl` when `XDG_STATE_HOME` is set, otherwise `~/.local/state/jev-decide/audit.jsonl`. Override it with `JEV_AUDIT_LOG`.

Each JSONL event contains:

- UTC timestamp, client version, operation, endpoint, and arguments;
- full canonical request and raw backend response when available;
- request and response SHA-256 hashes;
- evidence path, byte size, role, and SHA-256 manifest;
- attempt count and success/error outcome;
- previous event hash and current event hash.

The client creates the leaf directory with mode `0700` and the file with mode `0600`, locks during append where supported, and calls `fsync` before returning. It never records `JEV_API_KEY`.

The log is reproducibility evidence, not a cryptographic authorization boundary. A same-user process can rewrite it. Use [signoff-integrity.md](signoff-integrity.md) for external approval/waiver enforcement.

## JEV-27B boundaries

- `noul` and `score` use fixed canonical option sets; only `choice` has free options.
- Up to 16 choice labels were represented during training. Labels beyond 16 have limited calibration evidence.
- Option order can affect probabilities and selection. Keep ordering stable; use `permute` only when supported and worth the extra requests.
- `thinking: "off"` is the System 1 path. `auto` and `on` may invoke the base model and additional reasoning.
- Context capacity does not prove difficult long-document reasoning quality.
- The training corpus is English-centric.
- Do not use the base weights for memory-relevance scoring without separate validation or fine-tuning.
- Validate confidence thresholds on the deployed model and local data.
