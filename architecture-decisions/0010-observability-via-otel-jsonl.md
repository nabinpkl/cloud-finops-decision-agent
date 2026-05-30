# ADR 0010: Observability via OpenTelemetry, JSONL file exporter in v0

- **Status:** Accepted
- **Date:** 2026-05-30
- **Related:** [0009](0009-agent-runtime-in-fastapi-openai-agents-sdk.md)

## Context

R8 left the agent runtime working end to end but blind. No record of what any
request cost, how many model turns a question took, where the tool result size
sat, or whether a model call dominated input tokens. The 2026 industry baseline
for an agent at this maturity is a four-layer budget stack (per-call, per-turn,
per-session $, per-user/day $) sitting on a traces-first observability floor.
This ADR locks the floor; enforcement is deferred to a follow-up.

## Decision

### 1. OpenTelemetry is the observability primitive

The agent runtime emits OTel spans. `gen_ai.*` semantic conventions where they
exist (`gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`,
`gen_ai.tool.name`, `gen_ai.operation.name`), and a `finops.*` prefix for
project-specific data (`finops.cost_usd`, `finops.cost.estimate`,
`finops.tool.args_size_bytes`, etc.).

OTel is the industry-standard schema and is portable. Any backend (Phoenix,
Tempo, Jaeger, Langfuse via OTLP receiver, Datadog) can ingest the same data
without changing the instrumentation. Choosing a vendor-specific SDK would
recreate the lock-in trap ADR-0009 eliminated for the model provider.

### 2. The exporter writes JSON lines to a local file in v0

`var/traces/traces.jsonl`, one OTel span per line, using the SDK's built-in
JSON representation (`ReadableSpan.to_json(indent=None)`). The same encoding
the upstream `ConsoleSpanExporter` uses by default.

JSONL on disk wins over OTLP-to-collector for v0 because:

- no collector to run, deploy, or babysit on a single-developer setup,
- the file is replayable into any OTel-aware backend later (collector with a
  file receiver, or a one-shot script), so we are not painting ourselves out
  of the production path,
- it is greppable and `jq`-able from the same shell that runs the tests.

The exporter is one file (`api/observability.py:JsonlSpanExporter`) and is
swap-in-place for `OTLPSpanExporter` the day we want push to a collector. That
switch is one config knob away.

### 3. Traces only in v0; metrics and logs come later

Metrics (counters/histograms) are aggregates of the same data a trace already
carries. Until the trace shape stabilizes and we have dashboards that want
pre-aggregated series, traces answer all the v0 questions: "what did this
question cost?", "where did the tokens go?", "did the tool result blow up the
post-tool context?".

OTel logs are a separate signal type with their own exporter plumbing. We have
uvicorn's stdout logs today; adding OTel logs is a no-yield change at this size.

### 4. Content capture is off by default

Span attributes carry token counts, durations, model name, tool name. Not the
user prompt text and not the assistant prose. Opt-in via
`OTEL_GENAI_CAPTURE_MESSAGE_CONTENT=true` (matches OTel's standard env name).

On-disk traces in a developer checkout end up in backups, shells, and grep
history. The default has to be no PII; the developer turning on content
capture is choosing to accept that.

### 5. Budget enforcement is deliberately not in this change

The trace data lights up the seam where a `RunHooks` enforcer reads cost from
`compute_cost_usd` in the same file and short-circuits past a `$X/turn`
ceiling. The seam ships in this change (the price table and the cost
primitive); the gate does not. Keeping enforcement out keeps the change
scoped to "see what is happening" before "block what is too expensive".

## Consequences

- All v0 traces are local. Production deploys (when they happen) will add an
  `OTLPSpanExporter` behind an env knob alongside the JSONL one; the bridge
  processor does not change.
- The `gen_ai.*` semantic conventions are still incubating; we treat their
  literal string values as the wire contract and the `_incubating` import as a
  source-only detail. If OTel renames a key, we read it from the SDK constant.
- The hardcoded `PRICE_TABLE` will drift as providers move prices. Acceptable
  for v0 because cost is best-effort and tagged `finops.cost.estimate=true`;
  the truth source remains the provider's billing dashboard. Move to a
  pulled-from-pricing-feed table when a real billing reconciliation question
  surfaces.
- The JSONL file grows unbounded; rotation and retention come with the first
  real deploy, not now.

## Alternatives considered

- **OTLP push to a local OTel collector in v0.** Adds an operated process for
  a single-developer setup. Rejected as premature.
- **Custom JSONL schema (not OTel).** Cheaper to write today but every dashboard
  and every backend would have to be hand-fed. Rejected: the lock-in equivalent
  of ADR-0009's vendor lock.
- **Langfuse / Helicone SDK directly.** Both speak OTel out of the box; better
  to emit OTel and let a future deploy point either tool at the same stream
  than to lock the instrumentation to a vendor.
