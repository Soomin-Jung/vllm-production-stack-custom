# AutoTrust JEV-27B deployment

This example deploys `autotrust/JEV-27B` with its repository-provided
`serve_decide.py`. It exposes the normal OpenAI-compatible vLLM routes and the
additional `POST /v1/decide` route from one engine.

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
| `/models/JEV-27B/serve_decide.py` | vLLM server wrapper that adds `/v1/decide` |
| `/models/JEV-27B/adapter_vllm` | Named System 1 LoRA and decision-head metadata |
| `/models/JEV-27B/calibration.json` | Per-kind calibration used by the wrapper |

Download the complete model repository rather than copying only safetensors.
The repository must be visible at `/models/JEV-27B`, and the profile must be
visible at `/profiles/k8s-jev-27b-h100-tp1.yaml`. The repository's
`global-values.yaml` already demonstrates `/models` and `/profiles` hostPath
mounts; confirm the node-side paths before deployment.

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

## Operational boundary

The endpoint returns a probability distribution over supplied options. It does
not provide final approval or waiver authority. Keep deterministic gates and
external signoff enforcement outside the model process, as documented by the
`jev-decide` skill.
