# AutoTrust JEV-27B deployment

This example deploys `autotrust/JEV-27B` through the repository's
`serve_decide_sqlite.py` wrapper. The wrapper loads AutoTrust's original
`serve_decide.py`, preserves its OpenAI-compatible and `POST /v1/decide`
behavior, and records server-side usage events in SQLite.

## Why the entrypoint is overridden

`/v1/decide` is not a stock vLLM endpoint. The model repository's
`serve_decide.py` wraps the standard vLLM OpenAI server, initializes the JEV
decision assets, and attaches the extra route. A normal `vllm serve` process
can serve the base model and named LoRA, but it does not expose this server-side
route.

The non-Ray deployment template therefore supports optional model-level
`command` and `args`:

- Without `modelSpec.command`, preserve the existing `vllm serve` command and
  `vllmConfig.extraArgs` behavior.
- With a non-empty `modelSpec.command`, render it as the container command.
- Render `modelSpec.args` only inside that custom-command branch.
- Supplying `args` without `command` has no effect.

The example renders the following process:

```text
python3 /models/JEV-27B/serve_decide.py \
  --config /profiles/k8s-jev-27b-h100-tp1.yaml
```

Use ASCII double hyphens (`--`). A typographic em dash such as `—config` is not
a valid CLI option.

## Files and mounts

| File | Runtime role |
| --- | --- |
| `deploy-jev.yaml` | Kubernetes placement plus command/args override |
| `k8s-jev-27b-h100-tp1.yaml` | vLLM/JEV runtime options for one H100 and TP1 |
| `serve_decide_sqlite.py` | Local wrapper that adds bounded SQLite usage logging |
| `/models/JEV-27B/serve_decide.py` | AutoTrust's original `/v1/decide` implementation |
| `/models/JEV-27B/adapter_vllm` | Named System 1 LoRA and decision-head metadata |
| `/models/JEV-27B/calibration.json` | Per-kind calibration used by the wrapper |

Download the complete model repository rather than copying only safetensors.
The repository must be visible at `/models/JEV-27B`. Copy this directory's
`serve_decide_sqlite.py` to
`/user/dtgpt/vllm-production-stack/profiles/JEV-27B/serve_decide_sqlite.py` and
keep the profile at `/profiles/k8s-jev-27b-h100-tp1.yaml`. The repository's
`global-values.yaml` mounts those host directories as `/models`, `/profiles`,
and `/vllm_cache`; confirm the node-side paths before deployment.

The configured image must include a vLLM build compatible with the model
repository's `serve_decide.py`, Qwen3.8, `lm_head` LoRA, processed logprobs, and
the script's server integration points. The example uses the downstream
`v0.30.0-cu129-torch-family` image tag; validate that exact internal image
before promoting it.

## LoRA role and request path

One engine loads two logical paths:

1. Normal OpenAI generation uses the pristine Qwen3.8-27B base weights and
   original `lm_head` (System 2).
2. `/v1/decide` selects the startup-registered `jev-decision` adapter. The
   adapter contains the trained backbone LoRA and the 24-slot decision head
   expressed as an `lm_head` LoRA. The server constrains the next token to the
   option tokens, performs a one-token readout, and converts processed
   log-probabilities into the returned distribution (System 1).

`enable-lora`, `max-lora-rank: 32`, and
`lora-modules: jev-decision=.../adapter_vllm` are therefore mandatory for
`/v1/decide`. The shipped vLLM adapter is zero-padded to rank 32 even though the
trained backbone LoRA uses rank 16. `logprobs-mode: processed_logprobs` is also
required so the decision readout observes the distribution after vLLM's
processing rules.

This is startup registration, not runtime adapter mutation. The example
overrides `VLLM_ALLOW_RUNTIME_LORA_UPDATING` to `0`.

## Profile mapping

The profile is the YAML equivalent of the model-card quickstart:

```bash
python3 JEV-27B/serve_decide.py \
  --model JEV-27B \
  --served-model-name autotrust/JEV-27B \
  --enable-lora \
  --max-lora-rank 32 \
  --lora-modules jev-decision=JEV-27B/adapter_vllm \
  --logprobs-mode processed_logprobs \
  --max-model-len 32768
```

The container profile changes relative paths to the mounted absolute paths and
adds the H100 TP1 placement/runtime values. `32768` is 32K tokens. The model
supports a longer context, but increasing it consumes additional KV-cache
memory and must be validated separately on the target H100.

## Deploy and verify

```bash
helm upgrade --install llm \
  -n inference \
  ./helm \
  -f ./helm/values.yaml \
  -f ./global-values.yaml \
  -f ./helm/examples/JEV-27B/deploy-jev.yaml
```

Verify server metadata and one decision through the engine service or the
configured gateway:

```bash
curl -s http://<jev-service>/v1/decide/info

curl -s http://<jev-service>/v1/decide \
  -H 'Content-Type: application/json' \
  -d '{
    "kind": "choice",
    "state": "All required tests passed; the rollout is reversible.",
    "question": "Which route should the workflow take?",
    "options": ["review", "proceed"]
  }'
```

Also check `/health` and `/v1/models`. A successful `/v1/models` response alone
does not prove that `serve_decide.py`, the named LoRA, and `/v1/decide` are
working; exercise the decision route explicitly.

## SQLite usage logging

The wrapper consumes its custom flags before invoking vLLM, so they do not
reach the upstream vLLM argument parser:

| Option | Behavior |
| --- | --- |
| `--jev-sqlite-path PATH` | Enables logging. Parent directories and the database are created when absent. Without it, logging is disabled. |
| `--jev-sqlite-input-mode metadata` | Stores hashes, input sizes, decision type, probabilities, token usage, latency, and outcome. It stores no question, option, state, or full response text. This is the default. |
| `--jev-sqlite-input-mode question` | Additionally stores question text, option labels, and the selected option. It does not store `state`. The deployment example uses this mode for practical usage analysis. |
| `--jev-sqlite-input-mode full` | Stores complete request and response JSON for reproduction. Use only with approved retention and access controls. |
| `--jev-sqlite-busy-timeout-ms N` | SQLite lock wait from 0 to 60000 ms; default 5000. |
| `--jev-upstream-script PATH` | AutoTrust server implementation to load; default `/models/JEV-27B/serve_decide.py`. |

The same settings can be supplied through `JEV_DECIDE_SQLITE_PATH`,
`JEV_DECIDE_SQLITE_INPUT_MODE`, `JEV_DECIDE_SQLITE_BUSY_TIMEOUT_MS`, and
`JEV_DECIDE_UPSTREAM`. CLI values win.

The database uses WAL mode, a process-local write lock, a bounded busy timeout,
and file mode `0600`. Writes run outside the event loop. A logging failure is
reported in the server log but never changes a valid inference response. The
DB is node-local because this example uses the existing `/vllm_cache`
hostPath. Do not let multiple pods on different nodes point at one SQLite file
through NFS; use one DB per pod/node or move aggregation to a server database.

`GET /v1/decide/info` reports whether SQLite logging is enabled, the configured
input mode, and schema version. The client skill sends its local audit UUID as
`X-JEV-Audit-ID`, which is stored only for correlation and is not an approval
or authentication claim.

### Included analytics

The `decision_events` table records:

- UTC start/end time, success/error status, HTTP status, and bounded error text
- `kind`, strategy, requested/used thinking, System 2 usage, option count, and image presence
- request/question/state/options SHA-256 plus character counts
- selected index, probability vector, maximum confidence, and entropy
- prompt/completion/total tokens, model-request count, model latency, and server latency
- optional question/options or full request/response according to input mode

Two views are created automatically:

- `jev_usage_daily`: requests, successes, average latency/tokens/confidence, and thinking usage by UTC day and kind
- `jev_question_frequency`: exact-question frequency keyed by question hash, with readable samples in `question` and `full` modes

Example queries:

```sql
-- Usage volume and operational cost indicators.
SELECT * FROM jev_usage_daily ORDER BY day_utc DESC, kind;

-- Most repeated decision questions.
SELECT kind, sample_question, requests, last_used_at
FROM jev_question_frequency
ORDER BY requests DESC
LIMIT 30;

-- Low-confidence calls that may need prompt/option redesign or escalation.
SELECT created_at, kind, question, max_probability, entropy,
       thinking_used, server_elapsed_seconds
FROM decision_events
WHERE outcome = 'success' AND max_probability < 0.70
ORDER BY created_at DESC;

-- Failure rate and p95 should be computed in the reporting layer; this gives
-- the raw latency/error series.
SELECT created_at, kind, outcome, http_status, server_elapsed_seconds, error
FROM decision_events
ORDER BY created_at DESC;
```

SQLite is an operational analytics store, not a trusted signoff ledger. A pod
with write permission can modify it. Back it up or export aggregates before
node replacement, define retention externally, and restrict access especially
when `question` or `full` mode is enabled.

## Operational boundary

The endpoint returns a probability distribution over supplied options. It does
not provide final approval or waiver authority. Keep deterministic gates and
external signoff enforcement outside the model process, as documented by the
`jev-decide` skill.
