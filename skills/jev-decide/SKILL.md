---
name: jev-decide
description: Call a self-hosted JEV /v1/decide endpoint directly from an agent workspace and interpret bounded decisions over supplied evidence. Use for explicit candidate selection, yes/no claim or rule checks, 0–5 rubric scoring, CI triage, routing, moderation, evidence review, or confidence-gated escalation. Use only when shell execution and network access to the configured JEV endpoint are available; do not use it to generate prose, invent missing facts, execute the selected action, or replace deterministic policy checks.
---

# JEV Decide

Run the bundled script to call the configured JEV endpoint. Do not start or require an MCP server.

## Configure

Require `JEV_BASE_URL`. Read `JEV_API_KEY` only when the backend requires Bearer authentication.

```bash
export JEV_BASE_URL="https://jev.example.internal"
export JEV_API_KEY="optional-token"
```

Optional settings:

- `JEV_TIMEOUT_SECONDS`: request timeout, default `60`.
- `JEV_CA_BUNDLE`: private CA PEM path.
- `JEV_DECIDE_SCRIPT`: explicit script path when the host does not expose this skill's directory.

Never print credentials. Do not follow redirects or automatically switch providers.

## Choose the primitive

- Use `noul` for one proposition. Interpret probabilities as `[P(false), P(true)]`.
- Use `choice` for 2–256 explicit candidates. Add `uncertain` or `insufficient_evidence` when the supplied evidence may not decide the question.
- Use `score` for an ordinal 0–5 judgment. Define the rubric in the question or state. Treat `choice` as the modal score, not an expected value.

Do not emulate ranking, free-form generation, search, arithmetic, or command execution through repeated calls.

## Build and send a request

1. Gather the smallest sufficient evidence.
2. Put facts, logs, diffs, rules, and measurements in `state`.
3. Ask one precise evaluative question.
4. Keep choice options mutually exclusive and preserve their order.
5. Prefer `thinking: "off"` for the fast path.
6. Run `scripts/decide.py`; use `JEV_DECIDE_SCRIPT` if configured, otherwise resolve the script relative to this file.
7. Parse JSON from stdout. A nonzero exit means no decision was produced.

Simple call:

```bash
python "$JEV_DECIDE_SCRIPT" \
  --kind choice \
  --question "Which category best explains this failure?" \
  --option test_failure \
  --option infrastructure \
  --option insufficient_evidence \
  --state-file /tmp/jev-state.json \
  --thinking off
```

For advanced or structured requests, write the exact JSON body and pass it through stdin:

```bash
python "$JEV_DECIDE_SCRIPT" --request-file - <<'JSON'
{
  "kind": "noul",
  "state": {"rule": "All tests must pass", "failed_tests": 0},
  "question": "Is the supplied rule satisfied?",
  "thinking": "off"
}
JSON
```

Use `--info` to query `GET /v1/decide/info` before relying on deployment-specific capabilities. Read [references/api-contract.md](references/api-contract.md) for fields, compatibility, and error behavior.

## Interpret the result

- Preserve `options` and `probabilities` order.
- Use `choice_index` as a zero-based argmax index and `choice` as its label.
- For `score`, calculate an expected score only when useful: `sum(index * probability[index])`. Keep the raw distribution.
- Treat probability as model output, not calibrated workflow correctness.
- Apply only task-specific thresholds validated with representative data.
- Escalate when an uncertainty option wins, probabilities are close, evidence is incomplete, the script fails, or consequences exceed the validated policy.
- Never execute a command, approve a change, or suppress an alert solely from the JEV result.

## Respect the evaluation boundary

Evaluate only supplied state. If the answer depends on a future event, hidden tool result, calculation, search, or plan, obtain that evidence first and add it to `state`. Do not ask JEV to invent the missing state and judge it in the same call.
