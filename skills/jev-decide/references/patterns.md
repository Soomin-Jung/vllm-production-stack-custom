# JEV-27B delegation patterns

Use these patterns to adapt useful `jev-code` workflows to AutoTrust JEV-27B. These names describe client-side workflows, not additional `/v1/decide` primitives.

## Capability mapping

| Workflow concept | JEV-27B mapping | Boundary |
| --- | --- | --- |
| classify | One `choice` per item | Multi-item orchestration is client-side |
| check | One `noul` per proposition | Keep propositions atomic |
| score | One `score` per item | Fixed 0-5 scale only |
| rank | One relevance `noul` per candidate, then sort by `P(true)` | Pointwise, not listwise ranking |
| ask | One explicitly typed request | Multiple questions require multiple calls |
| policy gate | Probabilities plus host-owned thresholds | Not authorization or signoff |

Do not expose these names as native `jev_classify`, `jev_rank`, or batched `jev_ask` operations.

## Classification and routing

For every homogeneous item:

- Put original content and common routing rules in `state`.
- Ask the same question and use the same ordered options.
- Include `other` or `insufficient_evidence` when classes are not exhaustive.
- Record top probability and top-two margin.
- Auto-route only when an external host policy allows the reversible, low-impact branch.
- Send the rest to review.

Do not concatenate many items and ask for many labels in one `choice`; one request returns one selection.

## Evidence checks

Use `noul` for a single proposition such as whether a diff modifies authentication or whether an excerpt supports a claim. Put raw evidence and the exact rule in `state`. Avoid vague questions such as “Is this safe?” and split compound checks.

Never use an evidence check to override a deterministic failure. When the decision affects signoff, bind raw source files with `--evidence-file` and require an external approval/waiver record.

## Ordered scoring

Define every anchor before using `score`. Example:

| Score | Example severity anchor |
| --- | --- |
| 0 | No user-visible or operational impact |
| 1 | Cosmetic or negligible impact |
| 2 | Limited degradation with an easy workaround |
| 3 | Material degradation affecting a bounded user group |
| 4 | Broad outage, security exposure, or data-integrity risk |
| 5 | Confirmed critical compromise or irreversible widespread loss |

Replace these anchors for the domain; do not treat them as universal policy.

## Relevance filtering and ranking

Retrieve candidates deterministically, then for each candidate:

- use `noul`;
- supply the query and original candidate excerpt;
- ask whether it directly helps answer the query;
- sort by `probabilities[1]`;
- apply a locally validated relevance threshold.

A sorted list always has a first item. Never treat rank 1 as relevant without checking probability and threshold.

## Action selection after lookahead

Do not ask JEV to choose among bare commands when correctness depends on hidden consequences. Compute or predict each outcome first, attach it to the option, and ask JEV to evaluate the explicit outcomes. The host retains action authority.

## Confidence-gated policy

Keep judgment, policy, and authorization separate:

1. JEV produces a distribution over explicit options.
2. Host code applies validated thresholds, margin rules, deterministic checks, and consequence class.
3. Host policy yields `route`, `review`, or `abstain` for ordinary cases.
4. A trusted external authority yields `approve`, `waive`, or `reject` for signoff-gated actions.

A JEV result can never produce step 4. A threshold such as 0.8 copied from another project is not validation.

## Measuring whether delegation helps

Measure main-model and JEV input/output tokens, request count, latency, labeled accuracy, auto-handled coverage, review/abstention rate, errors within auto-handled cases, and sensitivity to option order and wording.

The intended benefit is less frontier-model generation and repeated semantic scanning. Duplicating large state across many calls can still increase total compute.

## Sources and cautions

- [AutoTrust model card](https://huggingface.co/autotrust/JEV-27B)
- [AutoTrust serving implementation](https://huggingface.co/autotrust/JEV-27B/blob/main/serve_decide.py)
- [jev-code workflow patterns](https://github.com/FrancoisChastel/jev-code/blob/main/skills/jev/SKILL.md)
- [Evaluation/simulation boundary](https://arxiv.org/abs/2610.01834)

AutoTrust JEV-27B is an independent student model, not TypeSafe's hosted Jev. Validate the deployed model instead of inheriting hosted-service claims.
