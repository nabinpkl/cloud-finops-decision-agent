# TASKS: v0 agent UI vertical slice (with v1 captured)

`R<N>` = remaining, `D<N>` = done. The number is stable; flip the letter once the change lands and tests are green.

## Position

The normalize layer and its HTTP surface are done. What is left in v0 is the agent UI that calls them and the eval that scores it. Per ADR-0008 we build one query type (`compare`) end-to-end through every layer before going wide, not FastAPI-then-Next.js as separate horizontal layers. Reasons:

- A thin end-to-end slice surfaces integration bugs (tool-call wiring, citation shape) on the first real query, not after both halves are built.
- The eval lane replays scenarios through the rendered slice, so it cannot start until the slice renders.

Ordering edges:

- Slice (Phase 2) before citation depth (Phase 3): a rendered table with one citation widget proves the chain; composite and excerpt rendering are depth on a working spine.
- Agent prose tuning (Phase 4) after the components render, because tuning needs real rendered output to judge against.
- Eval (Phase 5) last: it scores the finished slice.

Config note: the API's CORS origins and port are literals in `api/main.py` today, acceptable as the first consumer. The moment `web/` lands (R7) it is the second consumer, so R7 moves them to `.env.example` knobs per the no-hardcoded-config rule.

## Phase 0: readiness ADR (gate) [done]

- D1. ADR `architecture-decisions/0008-vertical-slice-api-and-citation-excerpt.md`. Locks the slice scope, the snapshot-ref citation shape (`store_path` dropped at the API boundary), and serve-time lazy excerpts.

## Phase 1: HTTP surface and test spine [done]

- D2. `api/main.py`: `POST /compare`, `GET /lookup`, `GET /citation/excerpt`, `GET /health`. CORS for localhost:3000; `store_path` to snapshot-ref translation; excerpt path-traversal guards.
- D3. `normalize/citation_excerpt.py`: serve-time hunk builder (LRU load by path+mtime, jsonpath resolve, pretty-print the matched value's parent container, windowed line numbers, minimal fallback for oversized parents).
- D4. `tests/` two lanes. Mocked integration: `compare` synthesis math against the real `flex_rules.gcp.n2`, closest-larger ranking, `data_quality` 24h boundary, excerpt branches, the `store_path`-leak guard. Real-file e2e: closes the citation loop, the excerpt's `matched_value` must equal the quoted price.
- D5. `just check` = ruff + ty + pytest; `just test-e2e` separate (needs a store). Cleared the ty and ruff debt the gate surfaced (`emit` to `NoReturn`, IBM id narrowing, schema annotation).
- D6. `/health` carries the `data_quality` envelope: per-provider freshness plus a `broken` rollup when a provider has no usable snapshot.

## Phase 2: the vertical slice (compare end-to-end in a browser)

- R7. Scaffold `web/`: Next.js app-router, assistant-ui chat shell, Vercel AI SDK on the Anthropic provider. One bare chat route round-tripping to the model. Move `api/main.py` CORS origins and port to `.env.example` knobs (second-consumer trigger). [ADR-0008]
- R8. `web/app/api/chat/route.ts`: define a `compare` tool whose execute fetches `POST {NORMALIZE_API_URL}/compare` and returns the JSON tool result. Verify the agent calls it and the raw result lands in the chat.
- R9. `web/components/ComparisonTable.tsx`: assistant-ui Tool component rendering the ranked results. Discriminated union on `synthesized` for atomic vs composite rows. One `AtomicCitation` widget per row (age badge, `json_path`, `source_url` link).
- R10. Slice smoke test: in a browser, "cheapest 4 vCPU 8 GB general-purpose in EU" renders the ranked table with citations. State explicitly if the UI cannot be exercised; do not claim success otherwise.

## Phase 3: citation depth

- R11. `web/components/CompositeCitation.tsx`: render the synthesis formula plus one collapsible sub-row per constituent (`rate x quantity = contribution`, each with its own `json_path` and `source_url`).
- R12. Excerpt-on-click: a citation action calls `GET /citation/excerpt` with the snapshot ref and `json_path`, renders the returned hunk as a monospace block with line numbers and the matched line highlighted. Label it a canonical rendering, not the upstream file's own line numbers.
- R13. Staleness banner: when `data_quality.overall_status != "ok"` render a banner; when `stale`, surface a refetch affordance that names `just fetch-force <provider>`.

## Phase 4: agent prose

- R14. System prompt and tool description so the agent paraphrases `data_quality.human_summary`, appends `(snapshot Xh old)` inline per AGENTS.md, and discloses `dimensions_not_normalized` for synthesized results. Tune over 5 to 10 queries against the rendered slice.

## Phase 5: eval

- R15. `eval/v0.jsonl`: hand-written scenarios across cheapest-ranking, single-lookup, stale-data, and out-of-taxonomy-refusal lanes. State the assertion per scenario.
- R16. `eval/` runner: replay scenarios through the slice, LLM judge scores citation correctness (excerpt resolves to quote) plus staleness and refusal behavior. Assert composite `contribution_usd` sum equals `hourly_usd` per ADR-0007.

(R17 to R19 reserved so v1 cross-references survive.)

---

## v1 scope (captured, not started)

- R20. lookup UI: `web/components/PriceCard.tsx` for single-instance answers.
- R21. `expand=full` rendering: "show me what else was considered" surfaces the `considered[]` candidate list.
- R22. Excerpt performance: byte-offset index or precomputed excerpt store to remove the first-hit ~1s parse of the 200 MB AWS region file (ADR-0008 negative consequence).
- R23. Deploy story: FastAPI behind Cloud Run or Fly, `web/` on Vercel, auth plus rate limiting. v0 is localhost-only.
- R24. `propose_equivalence`: queue agent-derived equivalences for PR review (SPEC.md).

### Alternative arc (rejected for now, captured)

- Build FastAPI fully, then Next.js fully (horizontal layers). Rejected because it defers integration risk and leaves an API surface no client exercises. Revisit only if the UI and the API end up owned by different people working in parallel.

## Promotion log

```
D1   2026-05-28  committed   ADR 0008 vertical slice + citation excerpt
D2   2026-05-28  committed   FastAPI compare/lookup/excerpt/health
D3   2026-05-28  committed   citation_excerpt serve-time hunk builder
D4   2026-05-28  committed   mocked + real-file test lanes
D5   2026-05-28  committed   just check = ruff+ty+pytest, debt cleared
D6   2026-05-28  committed   /health carries data_quality envelope
R7   pending     pending     web/ scaffold + CORS config extraction
R8   pending     pending     compare tool wiring
R9   pending     pending     ComparisonTable component
R10  pending     pending     slice smoke test
R11  pending     pending     CompositeCitation component
R12  pending     pending     excerpt-on-click hunk UI
R13  pending     pending     staleness banner + refetch
R14  pending     pending     agent prose tuning
R15  pending     pending     eval/v0.jsonl scenarios
R16  pending     pending     eval runner + LLM judge
```
