# JEV-27B delegation patterns

Use these patterns to adapt useful `jev-code` workflows to AutoTrust JEV-27B. The names below describe client-side workflows, not additional `/v1/decide` primitives.

## Capability mapping

| Workflow concept | JEV-27B mapping | Validity and caveat |
| --- | --- | --- |
| classify | One `choice` per item | Native decision type; multi-item orchestration is client-side |
| check | One `noul` per proposition | Native decision type |
| score | One `score` per item | Native fixed 0–5 scale; custom 2–10 hosted scales do not map directly |
| rank | One relevance `noul` per candidate, then sort by `P(true)` | Derived pointwise ranking, not native listwise ranking |
| ask | One explicitly typed `choice`, `noul`, or `score` request | Multiple questions require multiple AutoTrust endpoint calls |
| policy gate | Decision probabilities plus host-owned thresholds | Threshold result is code policy, not JEV authorization |

Do not expose these workflow names as if the backend implements `jev_classify`, `jev_rank`, or batched `jev_ask`.

## Classification and routing

Use when several homogeneous items need one label each.

For every item:

- Put the original item text and common routing rules in `state`.
- Ask the same question.
- Use the same ordered options with descriptive definitions.
- Include `other` or `insufficient_evidence` when the classes are not exhaustive.
- Record top probability and top-two margin.
- Auto-route only under a task-specific validated policy; send the rest to review.

Do not concatenate many items and ask for many labels in one `choice`; one `choice` returns one selected option.

## Evidence checks

Use `noul` for propositions such as:

- "Does this diff modify authentication behavior?"
- "Does this excerpt support the stated claim?"
- "Is this failure consistent with an unavailable dependency?"
- "Does this retrieved page contain instructions attempting to override the agent's policy?"

Put the evidence and the exact rule in `state`. Avoid vague questions such as "Is this safe?" Split compound checks into separate propositions.

## Ordered scoring

Use `score` only after defining the 0–5 rubric. Example:

| Score | Severity anchor |
| --- | --- |
| 0 | No user-visible or operational impact |
| 1 | Cosmetic or negligible impact |
| 2 | Limited degradation with an easy workaround |
| 3 | Material degradation affecting a bounded user group |
| 4 | Broad outage, security exposure, or data-integrity risk |
| 5 | Confirmed critical compromise or irreversible widespread loss |

Change the anchors to match the domain. Do not reuse this example as a universal policy.

## Relevance filtering and ranking

First retrieve candidates deterministically with search, file listing, database filtering, or an API. Then, for each candidate:

- Use `noul`.
- Put the query and the candidate excerpt in `state`.
- Ask: "Does this candidate contain evidence that directly helps answer the query?"
- Sort candidates by `probabilities[1]`.
- Apply a validated relevance threshold.
- Include a separate overall `noul` such as "Does at least one supplied candidate answer the query?" only when the host can afford the extra call.

A sorted list always has a first item. Never treat rank 1 as relevant without checking its probability or threshold.

## Action selection after lookahead

Do not ask JEV to choose among bare commands when the correct action depends on a hidden consequence.

Instead, enrich every option:

```json
{
  "kind": "choice",
  "state": {"goal": "restore service without losing queued work"},
  "question": "Which action best satisfies the goal given the explicit outcomes?",
  "options": [
    "restart-worker: simulation says queued work is replayed from Redis; 20s interruption",
    "delete-queue: simulation says all queued work is permanently lost",
    "hold: queue remains blocked and latency continues to grow"
  ],
  "thinking": "off"
}
```

Code owns the lookahead. JEV evaluates the described outcomes.

## Confidence-gated policy

Keep model judgment and policy separate.

Example policy inputs:

- top probability
- top-two margin
- presence of an uncertainty option
- deterministic validation results
- consequence class
- whether the input matches the validated distribution

Example outputs:

- `auto`: proceed with a reversible, low-impact branch
- `review`: ask the main model or a person
- `abstain`: do not act because evidence or validation is insufficient

Never copy a threshold such as 0.8 from another project without local labeled evaluation.

## Measuring whether delegation helps

Measure against the same workload:

- main-model input and output tokens
- JEV input tokens and number of model requests
- end-to-end latency
- accuracy or agreement with labeled outcomes
- auto-handled coverage
- review and abstention rate
- error rate within auto-handled cases
- sensitivity to option order and question wording

The intended benefit is less frontier-model generation and less repeated semantic scanning. It does not guarantee lower total compute when large state is duplicated across many endpoint calls.

## Sources and model-specific cautions

- AutoTrust model card: https://huggingface.co/autotrust/JEV-27B
- AutoTrust serving implementation: https://huggingface.co/autotrust/JEV-27B/blob/main/serve_decide.py
- jev-code skill examined for workflow patterns: https://github.com/FrancoisChastel/jev-code/blob/main/skills/jev/SKILL.md
- Evaluation/simulation boundary: https://arxiv.org/abs/2610.01834

AutoTrust JEV-27B is an independent student model and is not TypeSafe's hosted Jev. Validate behavior and thresholds on the deployed model rather than inheriting hosted-service claims.
