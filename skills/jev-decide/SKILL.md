---
name: jev-decide
version: "0.3.0"
description: "Delegate bounded, repeatable semantic judgments to a self-hosted AutoTrust JEV-27B /v1/decide endpoint to reduce frontier-model generation and repeated scanning. Use for evidence-grounded classification/routing, proposition checks, defined 0-5 scoring, explicit candidate selection, pointwise relevance ranking, and confidence-gated second opinions. Use only when explicit evidence and options determine the answer, a concrete workflow branch consumes it, and a safe fallback exists. Treat every JEV result as advisory: never use it as final approval, waiver, security bypass, failure suppression, or other signoff."
---

# JEV Decide

Use JEV-27B as a small typed judgment layer. Let the main agent gather evidence, compute consequences, generate artifacts, and own execution. Let JEV select among explicit alternatives and return a probability distribution.

Run `scripts/decide.py` directly. Do not start an MCP server.

## Apply the delegation gate

Call JEV only when every condition is true:

1. The answer is `noul`, one explicit `choice`, or an ordered 0-5 `score`.
2. Supplied evidence and option descriptions are sufficient to judge the answer.
3. The call replaces repeated frontier-model generation, repeated semantic scanning, or a brittle heuristic.
4. Each result maps to a known branch such as route, include, exclude, review, escalate, or select.
5. An uncertain result or tool failure has a safe fallback.

Delegate before spending a long main-model reasoning pass. Skip JEV for a single obvious judgment when the call does not reduce work.

## Use supported patterns

- Classify or route homogeneous tickets, failures, files, findings, messages, or records.
- Check whether supplied evidence supports one stable proposition.
- Score severity, priority, urgency, risk, or quality after defining all six 0-5 anchors.
- Select one candidate after tools or code have supplied the relevant outcome of every option.
- Filter or rank retrieved candidates with one relevance `noul` call per candidate and sort by `P(true)`.
- Add a cheap semantic second opinion before host policy routes a case to auto-handling or review.
- Screen untrusted text against explicit categories while deterministic security controls remain authoritative.

Read [references/patterns.md](references/patterns.md) before building repeated classification, ranking, or policy-gated workflows.

## Reject unsupported or unsafe uses

- Do not request prose, code, summaries, explanations, free-form extraction, or ideation.
- Do not replace `rg`, parsers, compilers, tests, schema validators, database queries, calculators, or API lookups.
- Do not ask JEV to plan, simulate, predict hidden state, or invent future tool outcomes.
- Do not infer difficult long-document reasoning quality from context capacity.
- Do not use the base model for memory-relevance scoring.
- Do not automate multilingual production decisions without local validation.
- Do not use JEV output to approve, waive, deploy, delete, suppress an alert, ignore a required failure, or bypass a deterministic control.

## Preserve evidence integrity

Treat the agent-authored question, state, option wording, option order, and selected excerpts as untrusted inputs to the decision process.

1. Pass raw deciding evidence and explicit rules. Do not replace evidence with a persuasive summary.
2. Avoid conclusions such as `"likely infrastructure failure"` in state.
3. Include `insufficient_evidence` or a review branch when evidence can be incomplete.
4. Bind every source file with `--evidence-file`; `--state-file` and file-based `--request-file` are bound automatically.
5. Compare the recorded evidence hashes with hashes produced by a trusted workflow. A local hash proves which bytes were evaluated, not that those bytes were authentic.
6. Keep deterministic gate results authoritative. A JEV disagreement never turns a failed required test into a pass.

## Enforce the signoff boundary

JEV is never a signer or approver. Its response and probabilities are advisory evidence only.

For any branch that requires final approval or an exception:

1. Obtain an external `approve` or `waive` record from SkillHub, CI, an approval service, or an authorized person outside the agent-controlled workspace.
2. Require that record to bind the exact `audit_id`, `request_sha256`, `response_sha256`, evidence manifest, scope, and expiry when applicable.
3. Verify signer identity, authority, signature, scope, expiry, and every bound hash before acting.
4. Reject missing, stale, mismatched, unsigned, or unauthorized records.
5. Never create, edit, reinterpret, or self-issue the approval/waiver record. The agent may only request one and pass it to the host verifier.

If no trusted verifier exists, stop at `review`; do not execute the signoff-gated branch. Read [references/signoff-integrity.md](references/signoff-integrity.md) when integrating a final gate.

## Choose the primitive

- Use `noul` for one proposition. Interpret probabilities as `[P(false), P(true)]` and keep `true` operationally consistent.
- Use `choice` for 2-256 explicit candidates. Prefer at most 16 options when practical.
- Use `score` only for an ordinal 0-5 scale. The selected `choice` is the modal score, not the expected score.

JEV-27B has no native `rank`, multi-item `classify`, or multi-question `ask` primitive. Compose those client-side from individual calls. Do not imply that `/v1/decide` accepts a batch of independent questions.

## Control cost and uncertainty

- Use `thinking: "off"` for normal delegation.
- Use `thinking: "auto"` or `"on"` only when intentionally accepting additional base-model generation.
- Use stable option ordering. Consider `strategy: "permute"` for consequential order-sensitive choices when the deployed revision supports it.
- Validate thresholds on representative labeled data; do not treat probabilities as calibrated correctness.
- Measure end-to-end accuracy, coverage, review rate, latency, JEV requests, and main-model token savings.
- Escalate close probabilities, low margin, uncertainty selections, out-of-distribution inputs, and client errors.

## Execute an audited request

Set the endpoint and optional Bearer token:

```bash
export JEV_BASE_URL="https://jev.example.internal"
export JEV_API_KEY="optional-token"
export JEV_DECIDE_SCRIPT="/path/to/jev-decide/scripts/decide.py"
```

The client writes full request/response audit events by default to `~/.local/state/jev-decide/audit.jsonl`. The directory and file modes are `0700` and `0600`. The log never records `JEV_API_KEY`, but requests and responses may contain sensitive evidence. Set `JEV_AUDIT_LOG` only to a user-controlled secure path and apply an appropriate retention policy.

```bash
python "$JEV_DECIDE_SCRIPT" \
  --kind choice \
  --question "Which category best explains this failure?" \
  --option test_failure \
  --option infrastructure \
  --option insufficient_evidence \
  --state-file /tmp/jev-state.json \
  --evidence-file /tmp/raw-test-output.txt \
  --thinking off
```

Use `--request-file -` for an exact JSON request. Use `--info` before relying on deployment-specific capabilities.

The client:

- retries only connection failures and HTTP `408`, `425`, `429`, `500`, `502`, `503`, and `504`;
- performs at most `1 + JEV_MAX_RETRIES` attempts, with `JEV_MAX_RETRIES` constrained to `0-5`;
- does not retry validation errors, other HTTP errors, or malformed JSON;
- records each success or failure in a hash-chained JSONL audit event before returning;
- emits `_jev_client` metadata containing the audit ID, hashes, attempt count, and advisory-only authority label.

Configure `JEV_TIMEOUT_SECONDS`, `JEV_MAX_RETRIES`, and `JEV_RETRY_BACKOFF_SECONDS` only within the limits documented in [references/api-contract.md](references/api-contract.md). A nonzero exit means no usable decision was produced.

## Interpret without overclaiming

1. Preserve the ordered probability distribution and verify option/index agreement.
2. Retain raw score probabilities when calculating a derived expected score.
3. Treat probabilities as the model's distribution over supplied options, not correctness guarantees.
4. Apply only thresholds validated for the exact question design and data distribution.
5. Route uncertain cases to review.
6. Use `audit_id` and recorded hashes to request external approval or waiver when required.
7. Never treat `_jev_client.final_authorization` as satisfied; it states that external authorization is still required.

## Separate simulation from evaluation

When a choice depends on consequences:

1. Compute or predict each consequence with code, tools, the main model, or a simulator.
2. Add the explicit outcome to its option or state.
3. Ask JEV to evaluate those supplied outcomes.
4. Keep the host workflow responsible for final authorization and action.
