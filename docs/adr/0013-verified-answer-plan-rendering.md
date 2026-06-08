# ADR 0013: Verified AnswerPlan rendering for agent prose

- **Status:** Accepted
- **Date:** 2026-06-05
- **Related:** [0003](0003-citation-stable-id-jsonpath.md),
  [0008](0008-vertical-slice-api-and-citation-excerpt.md),
  [0011](0011-public-endpoint-threat-model.md),
  [0012](0012-agent-runtime-port.md)

## Context

The pricing agent is unauthenticated and model-facing. Earlier hardening added
XML trust zones, strict tool args, and a final-answer policy that checked
free-form prose after the model wrote it. That caught obvious fabricated prices
and internal leaks, but it still left a weak seam: the model could write fluent
text that was only loosely tied to the structured `compare` result.

The citation contract is stronger than that. Every quoted price, candidate, age,
and citation has a source row. If the model is allowed to author the final prose
directly, the backend has to infer claims out of text and hope the regex policy
matched what the human read.

## Decision

The model no longer writes final user-facing prose for pricing answers. It emits
a strict `AnswerPlan` JSON object:

- `price_claims[]` bind each quoted price to a `source_result_index` and copy the
  provider, instance type, native region, price, snapshot age, and public
  citation fields from the latest tool result.
- `candidate_claims[]` bind ranked or expanded candidate mentions to the same
  tool result rows, including snapshot age for every candidate price.
- `unmet_requirements[]` bind missing-data refusals to the tool result's
  `unmet_requirements`.
- refusal answers carry only a constrained `refusal_reason`.

Backend policy validates the plan against the structured tool result and renders
final prose by interpolation. The rendered prose still passes the older
final-answer policy as defense in depth before it reaches the UI.

## Consequences

**Good.** Citation correctness is now a structured binding problem, not a prose
extraction problem. Evals can replay the model's JSON, validate exact source-row
bindings, and assert that rendered prose is a deterministic consequence of the
plan. A future model or runtime swap cannot weaken the citation contract unless
it changes this backend policy code.

**Cost.** The prose is intentionally more template-like. The renderer says
"Cheapest is ..." for the current ranking-oriented v0 surface and does not yet
produce rich explanation paragraphs. This is accepted because v0 optimizes for
verifiable pricing claims over literary quality.

**Known limitation.** The current plan handles the compare/ranking surface. A
future `lookup`-specific UI or richer equivalence explanation should extend the
plan schema and renderer rather than returning to free-form final prose.

**Known limitation.** Model-visible citations use the public wire shape:
`source_url`, `json_path`, `age_hours`, and logical `snapshot` refs. They do not
include `store_path`; ADR-0008 deliberately removed local filesystem paths from
the agent/UI surface. Human verification goes through the citation excerpt
endpoint or internal snapshot ref resolution, not through model-emitted local
paths.

## Alternatives considered

- **Keep free-form prose plus regex checks.** Rejected: the backend would still
  infer claims from rendered text, and a fluent answer could pass casual review
  while drifting from tool rows.
- **Ask the model for claims plus prose and compare them.** Rejected for v0: two
  model-authored outputs can disagree, and deciding which one the user saw
  recreates the original problem.
- **Render a citation JSON block under the prose.** Deferred: the frontend tool
  result already carries structured citation data. Duplicating that block in
  final text makes the chat noisier without strengthening the binding.
