---
name: jev-decide
description: "Delegate bounded semantic judgments to a self-hosted AutoTrust JEV-27B /v1/decide endpoint to reduce frontier-model generation and repeated manual scanning. Use when Codex or Claude must classify or route multiple items, check claims against supplied evidence, score severity/priority/quality on a defined 0–5 rubric, select among explicit candidates, filter or rank retrieved candidates by relevance, or make a confidence-gated second-opinion decision. Trigger only when the correct answer is evaluable from explicit state and options, the result maps to a concrete workflow branch, and shell/network access is available. Do not use for prose or code generation, exact facts settled by tools, arithmetic, hidden-state prediction, planning, simulation, memory relevance, or autonomous high-stakes approval."
---

# JEV Decide

Use JEV-27B as a fast typed judgment layer inside a larger task. Let the main agent gather evidence, simulate consequences, generate artifacts, and own execution. Let JEV choose among explicit alternatives and return a probability distribution.

Run the bundled `scripts/decide.py` directly. Do not start or require an MCP server.

## Apply the delegation gate

Call JEV only when every condition is true:

1. **Bounded output:** the answer is `noul`, one explicit `choice`, or an ordered 0–5 `score`.
2. **Evidence present:** the correct answer can be judged from the supplied state and option descriptions without inventing a future event or intermediate result.
3. **Useful replacement:** the call replaces repeated frontier-model generation, semantic scanning, or a brittle keyword heuristic. Skip a single obvious judgment when the call adds overhead without reducing work.
4. **Action mapping:** each result maps to a known branch such as route, include, exclude, review, escalate, or select.
5. **Safe fallback:** an uncertain result or tool failure can be handled by more evidence, a deterministic check, the main model, or a person.

Delegate early. Do not spend a long main-model reasoning pass to produce a conclusion and then ask JEV to confirm that conclusion.

## Prefer these use cases

- Classify or route several tickets, failures, files, findings, messages, or records into described classes.
- Check whether raw evidence supports a claim, whether a diff meets a stated rule, or whether a log matches a failure hypothesis.
- Score severity, priority, urgency, risk, or quality when all six 0–5 anchors are defined.
- Select one candidate action when code or tools have already supplied each candidate's relevant outcome.
- Filter or rank deterministic search results by asking one relevance `noul` per candidate, then sorting by `P(true)`.
- Add a cheap semantic second opinion before the host policy decides whether to auto-handle or review.
- Screen untrusted text against explicit categories, while keeping deterministic security controls authoritative.

Read [references/patterns.md](references/patterns.md) before designing a repeated classification, relevance ranking, or policy-gated workflow.

## Reject these use cases

- Prose, code, summaries, explanations, extraction of free-form values, or open-ended ideation.
- Facts that `rg`, a parser, compiler, test, schema validator, database query, calculator, or API lookup can settle.
- Planning or choosing a next step whose consequences have not been computed or described.
- Predicting hidden user intent, future system state, tool output, opponent behavior, or physical effects and choosing an action in the same call.
- Long-document reasoning merely because the context window accepts the text; hard long-document reasoning is not established.
- Memory-relevance scoring with the base JEV-27B weights; the model card identifies an uninformative training stream for that task.
- Automatic high-stakes approval, denial, deletion, deployment, security bypass, or alert suppression.
- Multilingual production automation without validation; the training corpus is English-centric.

## Choose the primitive

- Use `noul` for one stable proposition. Interpret probabilities as `[P(false), P(true)]`. Phrase the question so `true` always has the same operational meaning.
- Use `choice` for 2–256 explicit candidates. Add `insufficient_evidence` when evidence can be incomplete. Prefer at most 16 options when practical.
- Use `score` only for an ordinal 0–5 scale. Define all six anchors or unambiguous endpoint/intermediate rules. The returned `choice` is the modal score, not the expected score.

JEV-27B has no native `rank`, multi-item `classify`, or multi-question `ask` primitive. Build those patterns from individual `choice`, `noul`, or `score` calls as described in [references/patterns.md](references/patterns.md). Do not claim that multiple questions share one AutoTrust `/v1/decide` request.

## Construct unbiased state

1. Pass raw deciding evidence: relevant diff hunks, log excerpts, test output, record fields, rules, or retrieved passages.
2. Preserve structure in JSON and identify fields explicitly.
3. Avoid conclusions such as `"likely infrastructure failure"`; they leak the desired answer into the state.
4. Include class definitions, score anchors, and policy conditions in the question or state.
5. Make options mutually exclusive enough for the workflow. Include a catch-all or review option when necessary.
6. Keep labels semantically descriptive. Preserve option order when comparing runs.
7. Reduce context deterministically where possible: select matching log windows, changed hunks, or search excerpts without rewriting their meaning.

## Control cost, latency, and robustness

- Use `thinking: "off"` for normal delegation. This preserves the System 1 path and the reason for using JEV.
- Escalate an inconclusive result back to the main model or a person. Use `thinking: "auto"` or `"on"` only when intentionally invoking the backend base model and accepting additional generation.
- Prefer fewer, well-described options. Above 16 options, JEV-27B uses labels not seen in training and calibration evidence is limited.
- For order-sensitive or consequential choices, use `strategy: "permute"` if the deployed revision supports it and the extra model requests are acceptable.
- Do not assume probabilities are calibrated for the local workflow. Choose thresholds from representative labeled data and monitor coverage, error rate, latency, and token savings.
- Compare total work, not only output tokens: JEV minimizes generated reasoning text, but repeated calls and duplicated long state still consume input compute.

## Execute the request

Require `JEV_BASE_URL`. Read `JEV_API_KEY` only for Bearer authentication.

```bash
export JEV_BASE_URL="https://jev.example.internal"
export JEV_API_KEY="optional-token"
export JEV_DECIDE_SCRIPT="/path/to/jev-decide/scripts/decide.py"
```

Simple choice:

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

Use an exact JSON request for advanced controls:

```bash
python "$JEV_DECIDE_SCRIPT" --request-file - <<'JSON'
{
  "kind": "noul",
  "state": {
    "rule": "All required tests must pass",
    "test_summary": {"required": 81, "passed": 81, "failed": 0}
  },
  "question": "Is the supplied rule satisfied by the supplied test summary?",
  "thinking": "off"
}
JSON
```

Use `--info` before relying on deployment-specific capabilities. A nonzero exit means no decision was produced. Read [references/api-contract.md](references/api-contract.md) for the exact request and response contract.

## Interpret without overclaiming

1. Preserve the full ordered probability distribution.
2. Verify that `choice_index`, `choice`, and the option order agree.
3. For `score`, calculate `sum(index * probability[index])` only as a derived expected score and retain the raw distribution.
4. Treat the probabilities as this model's distribution over the provided options, not as guaranteed correctness.
5. Apply a workflow threshold only if validated for this exact question design and data distribution.
6. Prefer both top probability and top-two margin for auto-routing; otherwise route to review.
7. Treat `insufficient_evidence`, close probabilities, script errors, and out-of-distribution inputs as escalation signals.
8. Never authorize or execute the selected action solely from the JEV response.

## Separate simulation from evaluation

If a choice depends on what will happen after each action:

1. Use code, tools, the main model, or a simulator to compute or predict the missing consequence.
2. Add the consequence to the corresponding option or state.
3. Ask JEV to evaluate the now-explicit outcomes.
4. Keep the host workflow responsible for the final action.

Use JEV to judge what the input describes, not to silently invent the world that the decision depends on.
