---
name: jev-decide
description: Use the self-hosted JEV decision MCP tools to make fast bounded judgments over supplied evidence. Trigger for classification, yes/no claim or rule checks, 0–5 rubric scoring, candidate selection, CI triage, routing, moderation, evidence review, or deciding whether to escalate to slower reasoning. Use only when the available state and explicit options are sufficient to evaluate; do not use JEV to invent plans, simulate missing future events, generate prose, or execute the selected action.
---

# JEV Decide

Use `jev_decide_info` once when backend capabilities or defaults are unknown. Use `jev_decide` for bounded evaluation.

## Choose the primitive

- Use `noul` for one proposition. Read `probabilities` as `[P(false), P(true)]`.
- Use `choice` for 2–256 explicit candidates. Keep options mutually exclusive and collectively useful. Add an `uncertain` or `insufficient_evidence` option when evidence may be incomplete.
- Use `score` for an ordinal 0–5 judgment. Define every score or clear anchors in the question/state. Treat `choice` as the modal score, not the expected value.

Do not emulate `rank`, free-form generation, numeric calculation, search, or tool execution through repeated decisions unless the user explicitly requests an evaluated workflow and the intermediate state is supplied by code.

## Build the request

1. Gather the smallest sufficient evidence before calling the tool: log, diff, rule, ticket, document excerpt, or candidate descriptions.
2. Put facts and evidence in `state`. Preserve structure with JSON when fields have operational meaning.
3. Ask one precise evaluative question in `question`. State the rule, target, timeframe, and polarity explicitly.
4. For `choice`, describe when each option applies. Preserve option order between retries and comparisons.
5. Set `thinking: "off"` for the fast System 1 path unless the task explicitly needs the backend's adaptive/System 2 feature.
6. Send advanced controls only after `jev_decide_info` or deployment documentation confirms support.

Example:

```json
{
  "kind": "choice",
  "state": {
    "job": "unit-tests",
    "log": "Connection refused to test database before any test ran"
  },
  "question": "Which category best explains this failure?",
  "options": [
    "test_failure: assertion or expected-result mismatch",
    "infrastructure: network, runner, storage, database, or service outage",
    "uncertain: evidence is insufficient"
  ],
  "thinking": "off"
}
```

## Understand inputs

`jev_decide` mirrors `POST /v1/decide`:

- Required: `kind`, `question`.
- Conditional: `options` is required only for `choice` and must contain 2–256 strings; the deployed backend can expose a lower limit.
- Evidence: `state` accepts a string, JSON object, or JSON list. Images require a vision-capable JEV backend; a list alone does not add vision.
- Decision controls: `strategy`, `thinking`, `threshold`.
- Reasoning controls: `think_budget`, `reasoning_effort`, `chat_template_kwargs`, `return_reasoning`, `debug`, `system2_only`.

Do not add a `model` field. The configured JEV server owns the served model and decision adapter.

Omit controls when the backend default should apply. Do not assume omitted and explicit `null` are identical for every future backend.

## Understand outputs

Use these fields without renaming or recalibrating them:

- `options`: ordered labels corresponding one-to-one with `probabilities`.
- `probabilities`: model distribution over those options.
- `choice_index`: zero-based argmax index.
- `choice`: selected option string.
- `protocol`: normally `jev27-bare-v1` for the System 1 response.
- `adaptation`: native or the strategy used for a larger choice set.
- `model`: backend-served name; it is descriptive, not a client routing input.
- `usage`, `elapsed_seconds`, `num_model_requests`: cost and latency evidence.
- `thinking`: present when adaptive/System 2 details are returned.

For `score`, compute an expected score only when the user needs it:

```text
expected_score = sum(index * probability[index] for index in 0..5)
```

Keep the raw distribution visible alongside any derived value.

## Act on the result

1. Report `choice` and the full probability distribution.
2. Distinguish model probability from workflow confidence or correctness.
3. Apply only thresholds validated for the specific task. Do not invent a universal 0.8 or 0.9 cutoff.
4. Escalate to detailed model reasoning, more evidence, deterministic checks, or a person when the uncertain option wins, probabilities are close, the tool errors, or the consequence exceeds the validated policy.
5. Never execute a command, approve a change, or suppress an alert solely because JEV selected an option. The host workflow owns authorization and execution.

If `isError=true`, treat the call as failed. Do not convert HTTP, timeout, malformed response, or contract errors into a decision.

## Use JEV for evaluation, not missing simulation

JEV works best when the correct option can be judged from supplied state. If a decision depends on a predicted future state, hidden tool result, calculation, search, or multi-step plan:

1. Have code or the main model produce that missing state.
2. Add the result to `state`.
3. Ask JEV to evaluate the now-explicit alternatives.

Do not ask JEV to both invent the intermediate world state and judge it in one call.

## Common workflows

- CI triage: classify a supplied failure log into defined operational categories.
- Rule check: use `noul` with the exact rule and diff; phrase the proposition so `true` has one stable policy meaning.
- Routing: choose among teams, queues, runbooks, or tools described in the options.
- Evidence review: judge whether a supplied claim is supported, contradicted, or insufficiently addressed.
- Severity: use `score` only with an explicit 0–5 rubric.
- Confidence-gated escalation: try `thinking: "off"`, then invoke a slower path when the validated policy says the distribution is inconclusive.

For exact field behavior and compatibility caveats, read `../../docs/contract.md`. For client registration and project instructions, read `../../docs/clients.md`. For deployment, smoke tests, and impact measurement, read `../../docs/operations.md`.
