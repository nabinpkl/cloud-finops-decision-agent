# TASKS: agentic comparison UI (agent-decided views over deterministic data)

`R<N>` = remaining, `D<N>` = done. The number is stable; flip the letter and tick
the box once the change lands and tests are green.

## Position

This plan supersedes the v0 vertical-slice TASKS plan, whose core shipped
(normalize layer, HTTP surface, server-side agent runtime, assistant-transport
bridge, first `ComparisonTable` render, and the hardening register). The shipped
detail lives in git history; a compact archive is at the bottom of this file.

The forward direction settled in design: the product is a **multi-vendor pricing
comparison with two surfaces over one shared state**. A deterministic comparison
table the human can drive manually, and a sidebar agent that operates the same
table. The agent decides columns and views; it never invents values. Every value
is bound to a citation. Inspiration is the embedded console cost-agent pattern
(ask -> tool call -> generated table), but scope is **prospective public-catalog
comparison**, not retrospective resource/usage/spend tracking.

The load-bearing contract: **the agent owns the view, the deterministic layer
owns every value.** The agent picks layout, grouping, sort, and which columns to
show from a registered, citeable vocabulary; the policy layer guarantees every
price-bearing cell binds to a validated `compare()`/`lookup()` result.

Ordering edges:

- R1 (product shape) before R2 (shared view-state) before R3 (co-driver rule):
  the state contract is the make-or-break for corroboration, and the mutation
  rule rides on it.
- R5 (column registry) before R6 (view-spec validation) and R7 (Tier-3 refusal):
  the registry is the vocabulary both enforce against.
- R9 (AG-UI vs assistant-transport) and R10 (query-shape mapping) are open
  decisions; capture each as an ADR before building against it.

## Decisions settled (2026-06-11)

| Area | Decision |
|---|---|
| State authority (R2) | Backend-authoritative. FastAPI owns view-state; frontend renders. |
| Transport (R9) | Adopt AG-UI now. FastAPI becomes an AG-UI server. See ADR-0016. |
| Frontend client (R9) | Keep assistant-ui via `@assistant-ui/react-ag-ui`; no CopilotKit. `ComparisonTable` preserved. See ADR-0016. |
| Query shapes (R10) | Agent composes multiple `compare()`/`lookup()` calls; validated layer stitches; no aggregation primitives in normalize. See ADR-0017. |
| View spec (R5/R6) | Agent emits a declarative view-spec (columns + layout + bindings). |
| Column registry (R5) | New `columns.json` JSON taxonomy beside `families.json`/`regions.json`. |
| Validation fail (R6) | Reject whole plan and re-prompt the agent on any unregistered column or unbindable cell. |
| Agent tools (R3) | `compare`/`lookup` (data) + `set_view` (declarative spec) + `select`/`highlight` (annotate). Backend writes state only from validated results. |
| Tier-3 ask (R7) | On an explicit unbackable-dimension ask, agent explains it's not in the snapshot and offers closest cited columns. Graceful, not a hard reject. |
| Trust tiers (R4) | Separate zones + badges. Table = verified zone; agent-derived claims badged in the sidebar. |
| Citation depth (R8) | Full depth: contract fields + outbound link + excerpt-on-click + composite sub-rows. |

Open implementation detail to resolve before R6: whether the `set_view` view-spec
is a section of the `AnswerPlan` (one validator covers both) or a parallel
validated object. Lean toward folding into `AnswerPlan`.

## Build order (transport spine first)

1. [done] Transport spine: backend is an AG-UI server, frontend on
   `@assistant-ui/react-ag-ui`, backend-authoritative state, hardening green. (R9 -> R2)
2. [done] Co-driver tools: `set_view`/`select` alongside `compare`/`lookup`. (R3)
3. [done] Generative-view contract: `columns.json` registry + declarative
   view-spec + reject-and-retry validation. (R5 -> R6, R7)
4. [partial] Trust + citation depth: badges + citation fields + excerpt +
   composite sub-rows + stale badge done; Region column + dimensions_not_normalized
   remain (R8), and the UI is browser-unverified (R12). (R4, R8)
5. [partial] Docs/ADRs: ADR-0016, ADR-0017 done; PRD non-goals guardrail
   remains (R11). (R1, R11)

All merged to `main` at 83051fb. Backward-compat cleanup (legacy commands path
removed, single ViewSpec concept, 422-on-oversize) landed in the same merge.
Remaining: R8 (Region column + dimensions surfacing), R11 (PRD guardrail),
R12 (browser verification), and the carried-forward R26/R35.

Migration caveat: the AG-UI swap must carry the hardening surface across, not
around it. The XML trust-zone wrapping, body/turn limits, mandatory input judge
(ADR-0015), and verified `AnswerPlan` rendering (ADR-0013) stay in force; step 1
re-verifies the security and budget suites, not just the happy path.

## Phase A: dual-surface foundation

- [x] R1. Dual-surface product shape (merged 83051fb). Deterministic table + sidebar agent
  co-driver over one shared state. Not a standalone manual dashboard, not
  chat-only. Record the product shape in `PRD.md` and `SPEC.md`. Blocks R2.
- [x] R2. Shared view-state contract (merged 83051fb). One canonical
  view-state object, two writers (human form + agent). Single snapshot and single
  citation / `data_quality` set shared by both surfaces so they never drift.
  Define snapshot-selection ownership, how an agent tool call writes back to the
  visible filters, and the shape of the state object. Agent mutates the view via
  typed deltas (AG-UI `STATE_DELTA`-style). Blocked by R1, blocks R3.
- [x] R3. Agent-as-co-driver rule (merged 83051fb). The agent's only legal state mutations are:
  request a deterministic `compare()`/`lookup()` with given args, and
  select/highlight/annotate a verified result row. The agent cannot write any
  price, citation, or instance match into state. Extends AnswerPlan validation
  (ADR-0013) to the state-mutation channel. Blocked by R2.

## Phase B: the generative-view contract

- [x] R5. Column registry for agent-decided views (merged 83051fb, `columns.json` + loader). Three tiers:
  - Tier 1, cited source columns: direct fields from a `compare()`/`lookup()`
    result row (provider, instance_type, region canonical + native, vcpu_actual,
    ram_gb_actual, monthly_usd, hourly_usd, considered_count, snapshot age,
    data_quality status).
  - Tier 2, derived columns: deterministic functions of cited fields ($/vCPU,
    $/GB-RAM, RAM overshoot, delta-vs-cheapest), each carrying its formula plus
    cited inputs (reuse the ADR-0007 composite-citation provenance pattern).
  - Tier 3, uncited columns: `dimensions_not_normalized` (network bandwidth, cpu
    generation, included storage). Refused, never filled.
  Start with a registered derived-formula whitelist, not an expression language;
  open up only if real queries demand it. Blocks R6 and R7.
- [x] R6. Extend AnswerPlan validation to the view spec (merged 83051fb; one `validate_view_spec_fields` on both the AnswerPlan and state-mutating paths). Every chosen column must
  resolve to a registry entry (Tier-1 field or Tier-2 registered formula), and
  every price-bearing cell must bind to a validated tool-result row. Columns
  outside the registry fail validation and do not render. This makes
  "agent-decided but not agent-invented" enforceable. Blocked by R5.
- [x] R7. Tier-3 column refusal UX (merged 83051fb; `refused_columns` on the unified ViewSpec). A column the snapshot cannot back is shown as
  an explicit refusal ("not normalized / unavailable") or omitted with an
  explanation, never fabricated. Column-level expression of Lane 2 refusal
  behavior; add a matching eval case. Blocked by R5.
- [x] R4. Two visual trust tiers (merged 83051fb, verified vs `derived` badge; browser-unverified, see R12). Verified tier (numbers/citations from taxonomy
  + snapshots, the `Source` primitive, checkable) vs agent-derived tier (judgment
  equivalences not in `families.json`, visually distinct, citing the snapshot
  fields used and naming `dimensions_not_normalized`). Design against authority
  transfer; a glance must tell checkable apart from agent-reasoning.

## Phase C: contract fixes carried from the mockup review

- [ ] R8. Fix citation/contract gaps (PARTIAL, merged 83051fb). DONE: real
  contract fields (`source_url`, `json_path`, `age_hours`, `snapshot` ref) are
  rendered, excerpt-on-click hunk, composite sub-rows, and a stale-snapshot badge
  (`data_quality.overall_status == "stale"`). REMAINING: a per-row Region column
  (canonical + native, per the ComparisonTable spec) is missing (region only
  shows as a table subtitle); `dimensions_not_normalized` is not surfaced in the
  comparison view. USD-only confirmed.

## Phase D: open decisions (capture as ADRs)

- [x] R9. DECIDED in ADR-0016. Adopt AG-UI now: FastAPI becomes an AG-UI server;
  frontend keeps assistant-ui via `@assistant-ui/react-ag-ui` (no CopilotKit);
  state is backend-authoritative, streamed as `STATE_SNAPSHOT`/`STATE_DELTA`. The
  *implementation* of this decision is build-order step 1, not yet done.
- [x] R10. DECIDED in ADR-0017. Agent composes multiple `compare()`/`lookup()`
  calls; the validated layer stitches them, every cell cited; no aggregation
  primitives in `normalize`. Aggregate columns are Tier-2 registry formulas over
  cited cells. Snapshot-history questions ("what got pricier") are out of scope
  pending a snapshot-retention ADR. Implementation lands with the view-spec work.
- [ ] R11. Record the prospective-only guardrail in `PRD.md` non-goals. The
  sidebar agent is a pre-purchase comparison advisor over public catalog prices.
  It does not track the user's own resources, spend, or usage (the
  OpenCost/Kubecost/Vantage/CloudZero space the README excludes). Write this down
  so the agent does not drift into a usage/billing dashboard clone.

## Phase E: verification gate

- [ ] R12. Browser-verify the agentic comparison UI end to end. The merged
  AG-UI migration + co-driver + trust/citation UI is backend-green (183 tests,
  eval 294/0) but NOT browser-verified. Run backend + frontend locally and
  exercise: `ComparisonTable` render from a real query, `STATE_SNAPSHOT`
  view-state sync, the `set_view` co-driver flow, session-limit banner,
  excerpt-on-click fetch, composite sub-rows, and the verified-vs-derived trust
  badges. True done-gate for the UI side of R4 and R8.

## Carried-forward v0 items (still open, not subsumed)

- [ ] R26. Fix `backend/justfile` `api` and `smoke` recipes so they preserve
  `.env` model config instead of unsetting `PROVIDER_BASE_URL`,
  `PROVIDER_API_KEY`, and `MODEL_NAME`; verify a configured `/assistant` startup
  path still works.
- [ ] R35. Refresh `EVALS.md` to reflect the prompt split, the current `just
  eval` command, the implemented replay runner, and any still-missing
  live/LLM-judge eval scope.

---

## Shipped (v0 vertical slice) — archived, detail in git history

The v0 slice took `compare` end-to-end through every layer per ADR-0008/0012 and
landed the agent-hardening register. Completed work:

- [x] ADR-0008 vertical slice + serve-time citation excerpt.
- [x] FastAPI `POST /compare`, `GET /lookup`, `GET /citation/excerpt`, `GET
  /health` with `store_path`-to-snapshot-ref translation and excerpt traversal
  guards.
- [x] Serve-time citation excerpt hunk builder; mocked + real-file e2e test
  lanes; `just check` = ruff + ty + pytest + eval.
- [x] `/health` `data_quality` envelope with `broken` rollup.
- [x] `app_config` central settings; frontend-only `frontend/` scaffold;
  same-origin `/assistant` rewrite to `BACKEND_ORIGIN` (no CORS round-trip).
- [x] In-process `compare` tool (no HTTP self-hop) via `normalize.wire`; `POST
  /assistant` over assistant-stream; `agent.runtime.AgentRuntime` with langchain
  default and OpenAI Agents SDK optional (ADR-0012).
- [x] `ComparisonTable` Tool component rendering ranked results with snapshot age
  and source link; session-limit banner.
- [x] Agent hardening: threat register (ADR-0014), adversarial +
  AnswerPlan-binding eval coverage, public abuse runbook and dependency audit
  helpers, prompt manifest with rendered-runtime checks, unconditional budget
  enforcement, hardened deterministic route validation + body limits, public
  `report_path` translation, OpenAI-adapter tool-result escaping,
  latest-tool-result claim binding, OTel content-capture redaction, validated
  `X-Forwarded-For` identity, removed citation-excerpt parsed-document cache.

Reframed under the new plan rather than carried as-is:

- v0 R11/R12/R13 (composite-citation render, excerpt-on-click, staleness banner)
  are folded into R8 (contract fixes) and R4 (trust tiers).
- v0 R14 (agent prose tuning) and R15/R16 (eval suites + runner) are reframed by
  the agent-decided-view direction; rebuild against R5/R6 once the view contract
  exists, not against the fixed-component slice.
- v0 R10 (browser smoke) re-runs against the dual-surface UI from R1-R3.

Rejected and captured: build FastAPI fully then Next.js fully as horizontal
layers (defers integration risk). Decision-basket / cross-query pin parked as a
post-v0 idea.
