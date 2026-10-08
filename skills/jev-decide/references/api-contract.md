# JEV /v1/decide contract

This skill calls the configured self-hosted endpoint directly. It does not load the model, proxy through MCP, retry, follow redirects, or discover another provider.

## Request

| Field | Type | Notes |
| --- | --- | --- |
| `kind` | `choice`, `noul`, `score` | Required |
| `question` | string | Required, non-empty |
| `state` | string, object, list | Optional evidence |
| `options` | string array | Required only for `choice`; 2–256 entries |
| `strategy` | `auto`, `single`, `tournament`, `permute` | Optional |
| `thinking` | `default`, `off`, `auto`, `on` | Optional |
| `threshold` | finite number or null | Optional |
| `think_budget` | integer or null | Optional |
| `reasoning_effort` | string or null | Optional |
| `chat_template_kwargs` | object or null | Optional |
| `return_reasoning` | boolean | Optional |
| `debug` | boolean | Optional |
| `system2_only` | boolean | Optional |

Do not send a `model` field. The deployed server owns model selection. Advanced fields depend on the deployed `serve_decide.py` revision; query `--info` and record that revision.

The script validates required fields and the `choice` option count, then preserves all supported request fields. Unknown fields in a request file are rejected to catch spelling mistakes.

## Response

| Kind | Ordered options | Selected value |
| --- | --- | --- |
| `choice` | Request option order | Maximum-probability option |
| `noul` | `["false", "true"]` | `"false"` or `"true"` |
| `score` | `["0", "1", "2", "3", "4", "5"]` | Modal score string |

Preserve backend fields such as `effective_kind`, `adaptation`, `protocol`, `model`, `usage`, `elapsed_seconds`, `num_model_requests`, and `thinking`. The script does not rewrite or recalibrate the response.

## Script interface

```text
decide.py --kind KIND --question TEXT [--state-json JSON | --state-file PATH]
          [--option TEXT ...] [--thinking MODE] [--strategy MODE]
decide.py --request-file PATH
decide.py --request-file -
decide.py --info
```

Successful responses are JSON on stdout. Validation, HTTP, TLS, timeout, redirect, or malformed-JSON errors are JSON on stderr and return a nonzero exit code.

Endpoint normalization accepts a base URL with no suffix, `/v1`, or `/v1/decide`. A URL containing credentials, query parameters, or fragments is rejected. Proxy environment variables are ignored.
