# ADR 0017: Agent-composed query shapes over aggregation primitives in normalize

- **Status:** Accepted (implementation pending)
- **Date:** 2026-06-11
- **Related:** [0007](0007-rate-rows-composite-citations.md),
  [0013](0013-verified-answer-plan-rendering.md),
  [0016](0016-ag-ui-transport.md)

## Context

`normalize.compare` answers one shape: a single ranked cross-provider list for a
fixed `(vcpu, ram_gb, region, family)`. The sidebar agent invites questions that
are not that shape: "cheapest per family", "spread between cheapest and dearest
per provider", "which providers got more expensive since the last snapshot".
These are group-by / aggregate / multi-axis queries.

Two ways to serve them. Either grow the deterministic layer with grouping and
aggregation primitives that emit their own citations, or let the agent decompose
the question into several `compare()`/`lookup()` calls and have the validated
layer stitch the results, with every cell still bound to a citation from one of
those calls.

The repo already draws a line (ADR-0012, ADR-0013): `normalize` does
deterministic lookup; the agent does judgment and orchestration; the policy layer
validates every claim against tool results. Aggregation primitives would push
orchestration down into the deterministic core and force it to invent new
citation shapes for synthesized aggregate cells.

## Decision

**The agent composes multiple deterministic calls; the validated layer stitches
them. No aggregation primitives are added to `normalize` in v0.**

- A grouped or multi-axis question becomes N `compare()`/`lookup()` calls (e.g.
  one `compare` per family for "cheapest per family"). The agent decides the
  decomposition.
- Each result row carries the citation it already gets today. Stitching is
  presentation: the view-spec (ADR-0016 co-driver, validated per the AnswerPlan
  extension) arranges rows from several calls into one table. No new cell is
  invented; every price cell binds to a row from some validated call.
- Derived/aggregate columns that are pure functions of cited cells (delta,
  spread, min/max within a group) are Tier-2 registry columns, carrying their
  formula and pointing at the cited inputs, exactly as ADR-0007 composite
  citations do for synthesized rates. The deterministic layer is not asked to
  emit them.
- "What changed since the last snapshot" needs snapshot history, which v0 does
  not retain. It is out of scope here and noted for a future ADR on snapshot
  retention.

## Consequences

**Good.** The deterministic core stays small and its citation shapes unchanged.
Orchestration lives where judgment lives (the agent), consistent with the
existing split. Aggregate values remain verifiable because they are functions of
cited cells, not new opaque numbers.

**Cost.** More tool calls per grouped question, so more latency and more
token/budget spend; the per-turn cap and budget controls (ADR-0011) now bound a
multi-call turn. The agent must decompose correctly, which is a prompt and eval
concern. The validated stitch layer must reject a view that references a cell no
call produced.

**Bound.** Multi-call turns must respect `max_tool_calls` gates in evals; a
grouped question with too many groups is a refusal or a clarification, not an
unbounded fan-out.

## Alternatives considered

- **Aggregation primitives in `normalize`.** A single deterministic call for
  grouped queries, lower latency. Rejected for v0: it grows the deterministic
  surface, forces new aggregate citation shapes, and moves orchestration below
  the agent. Revisit if agent-composition latency or correctness proves
  inadequate at real query volume.
- **Forbid grouped questions (single compare only).** Smallest scope, but the
  sidebar cannot answer the realistic question set, defeating the co-driver
  value. Rejected.
