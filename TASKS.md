# TASKS: v0 agent UI vertical slice (with v1 captured)

`R<N>` = remaining, `D<N>` = done. The number is stable; flip the letter once the change lands and tests are green.

## Position

The normalize layer and its HTTP surface are done. What is left in v0 is the agent runtime, the frontend that renders it, and the eval that scores it. Per ADR-0008 we build one query type (`compare`) end-to-end through every layer before going wide, not FastAPI-then-Next.js as separate horizontal layers. ADR-0009 fixes where the agent runs: the loop is server-side in FastAPI on the Python OpenAI Agents SDK, `web/` is a frontend-only client that renders the stream, and the model provider is an OpenAI-compatible base-URL knob (not a hardcoded vendor). Reasons:

- A thin end-to-end slice surfaces integration bugs (tool-call wiring, citation shape) on the first real query, not after both halves are built.
- The eval lane replays scenarios through the rendered slice, so it cannot start until the slice renders.

Ordering edges:

- Stream bridge (R8) before any rendering: the frontend (D7) uses assistant-ui's `useAssistantTransportRuntime`, which POSTs to `/assistant` (proxied to the backend). assistant-ui ships a first-party Python package (`assistant-stream`) and a reference `assistant-transport-backend`, so the backend has an emit helper; the bounded work is bridging the OpenAI Agents SDK's `Runner.run_streamed()` events into assistant-stream state updates. Still the first integration risk of the ADR-0009 architecture: prove a real tool call round-trips before R9 renders.
- Slice (Phase 2) before citation depth (Phase 3): a rendered table with one citation widget proves the chain; composite and excerpt rendering are depth on a working spine.
- Agent prose tuning (Phase 4) after the components render, because tuning needs real rendered output to judge against.
- Eval (Phase 5) last: it scores the finished slice.

Config note: the API's CORS origins and port are literals in `api/main.py` today, acceptable as the first consumer. R7 lands `web/` (a second consumer of the origin/port) and the agent runtime (a consumer of the provider knobs), so R7 moves both to `.env.example`: `API_PORT`, `CORS_ALLOWED_ORIGINS`, plus the agent's `PROVIDER_BASE_URL`, `PROVIDER_API_KEY`, `MODEL_NAME`. Per the no-hardcoded-config rule, `api/main.py` reads them from a central config module rather than `process.env`-style scattered reads.

## Phase 0: readiness ADR (gate) [done]

- D1. ADR `architecture-decisions/0008-vertical-slice-api-and-citation-excerpt.md`. Locks the slice scope, the snapshot-ref citation shape (`store_path` dropped at the API boundary), and serve-time lazy excerpts.

## Phase 1: HTTP surface and test spine [done]

- D2. `api/main.py`: `POST /compare`, `GET /lookup`, `GET /citation/excerpt`, `GET /health`. CORS for localhost:3000; `store_path` to snapshot-ref translation; excerpt path-traversal guards.
- D3. `normalize/citation_excerpt.py`: serve-time hunk builder (LRU load by path+mtime, jsonpath resolve, pretty-print the matched value's parent container, windowed line numbers, minimal fallback for oversized parents).
- D4. `tests/` two lanes. Mocked integration: `compare` synthesis math against the real `flex_rules.gcp.n2`, closest-larger ranking, `data_quality` 24h boundary, excerpt branches, the `store_path`-leak guard. Real-file e2e: closes the citation loop, the excerpt's `matched_value` must equal the quoted price.
- D5. `just check` = ruff + ty + pytest; `just test-e2e` separate (needs a store). Cleared the ty and ruff debt the gate surfaced (`emit` to `NoReturn`, IBM id narrowing, schema annotation).
- D6. `/health` carries the `data_quality` envelope: per-provider freshness plus a `broken` rollup when a provider has no usable snapshot.

## Phase 2: the vertical slice (compare end-to-end in a browser)

- D7. Two parts, both landed. (a) Backend: `openai-agents` dependency added; `api/config.py` central settings (`API_PORT`, `CORS_ALLOWED_ORIGINS`, `PROVIDER_BASE_URL`, `PROVIDER_API_KEY`, `MODEL_NAME`); `api/agent.py` `build_agent()` builds an `Agent` on `OpenAIChatCompletionsModel(AsyncOpenAI(base_url, api_key))` (Chat Completions, not Responses, so non-OpenAI compat endpoints do not 404) and guards on missing creds; CORS/port lifted out of `api/main.py`. (b) Frontend: `web/` scaffolded from the assistant-ui `with-assistant-transport` example, frontend-only (no `app/api/*`). `useAssistantTransportRuntime` POSTs to same-origin `/assistant`, which `next.config.js` rewrites to `BACKEND_ORIGIN` (env knob, no CORS round-trip, no backend URL in client). Demo cruft stripped; an unreachable `"indicator"` part case removed from the generated `thread.tsx` to clear a `latest`-version type drift. Renders on :3000 (verified). `just web` recipe added. [ADR-0009]
- R8. The `compare` tool + the stream bridge (backend-only; the frontend is wired). Define a Python `compare` tool whose body calls `normalize.compare()` in-process (no HTTP self-hop) and returns the result dict (wire-translated: `store_path` dropped for a `snapshot` ref, as `api/main.py` already does). Add `POST /assistant` implementing the assistant-transport protocol with assistant-ui's Python `assistant-stream` package, running the agent via `Runner.run_streamed()` and bridging its events into assistant-stream state. Reference: assistant-ui's `python/assistant-transport-backend`. Verify end-to-end: a real tool call round-trips and the raw result reaches the browser. Prove the bridge before R9. The frontend's scaffold-derived LangChain message converter (`@assistant-ui/react-langgraph` in `web/`) is replaced here once the emitted state shape is fixed.
- R9. `web/components/ComparisonTable.tsx`: assistant-ui Tool component rendering the ranked results streamed from FastAPI. Discriminated union on `synthesized` for atomic vs composite rows. One `AtomicCitation` widget per row (age badge, `json_path`, `source_url` link).
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
D7   2026-05-29  uncommitted agent runtime (OpenAI Agents SDK) + config knobs + frontend-only web/ scaffold
R8   pending     pending     in-process compare tool + POST /assistant (assistant-stream bridge)
R9   pending     pending     ComparisonTable component
R10  pending     pending     slice smoke test
R11  pending     pending     CompositeCitation component
R12  pending     pending     excerpt-on-click hunk UI
R13  pending     pending     staleness banner + refetch
R14  pending     pending     agent prose tuning
R15  pending     pending     eval/v0.jsonl scenarios
R16  pending     pending     eval runner + LLM judge
```
