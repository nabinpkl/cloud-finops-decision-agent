# REFACTOR.md

Review date: 2026-06-04.

Update 2026-06-05: the Python backend has been moved to a `src/` layout.
Historical path references below that start with `api/`, `ingest/`, `normalize/`,
or `scripts/` now resolve under `src/`. The current FastAPI shape is
`src/api/main.py` as the ASGI entry point, `src/api/app.py` for app assembly,
and `src/api/routes/` for deterministic HTTP routes.

Implementation update 2026-06-05: the follow-up clustering pass split the
assistant transport into `src/api/assistant_transport/`, observability into
`src/api/observability/`, IBM's gate into `src/ingest/ibm/`, index-build support
into `src/normalize/index_*`, and larger provider row parsing into
`src/normalize/builders/{azure_rows,gcp_rows}.py`.

Scope: review the current codebase against the combined standard from the repo
rules, idiomatic Python, and the reference expectations from
`agent-workspace-ide`: read actual files, use literal concepts, fail loud, keep
one source of truth, prefer typed structured models, avoid speculative
abstractions, and keep docs aligned with code.

## Findings

### 1. Project docs are stale against the implemented runtime

Severity: high.

The live code and `pyproject.toml` say the default runtime is LangChain via the
`deepagents` selector, with OpenAI Agents SDK optional:

- `pyproject.toml:11` to `pyproject.toml:24` makes `langchain` and
  `langchain-openai` core dependencies.
- `pyproject.toml:27` to `pyproject.toml:33` moves `openai-agents` to an
  optional extra.
- `api/config.py:42` to `api/config.py:48` sets `agent_runtime` default to
  `deepagents`.
- `architecture-decisions/0012-agent-runtime-port.md` records this as the
  accepted amendment.

But the user-facing docs still describe OpenAI Agents SDK as the runtime:

- `README.md:21` and `README.md:62` say the server-side runtime is OpenAI
  Agents SDK.
- `README.md:74` says the agent runtime and frontend are still next work,
  although `api/transport.py`, `api/runtime/*`, and
  `web/components/tools/comparison-table.tsx` exist.
- `PRD.md:12` and `PRD.md:65` still lock the v0 agent runtime to OpenAI Agents
  SDK.
- `SPEC.md:297` to `SPEC.md:314` describes the stream bridge as OpenAI Agents
  SDK work.
- `TASKS.md:36` to `TASKS.md:38` still lists the compare tool, stream bridge,
  and `ComparisonTable` as pending; the code now has `api/tools_core.py`,
  `api/transport.py`, `api/runtime/deepagents.py`, and
  `web/components/tools/comparison-table.tsx`.

This is now more than cosmetic drift. The docs are the first surface another
agent will load before editing, so stale runtime docs push work back toward the
wrong framework.

Recommended change:

- Update `README.md`, `PRD.md`, `SPEC.md`, and `TASKS.md` to make ADR-0012 the
  current runtime contract: neutral runtime port, default `deepagents`,
  optional `openai_agents`.
- Flip `TASKS.md` statuses for work that has actually landed, and move the next
  real work to the top. If the slice has already moved past R8/R9, make the
  ledger say that plainly.

### 2. The PRD invariant "No LLM in the server" is no longer true

Severity: high.

`PRD.md:21` and `PRD.md:52` say the server contains no model and that the
calling agent is outside the server. The implemented architecture now runs the
agent loop inside FastAPI:

- `api/transport.py:1` to `api/transport.py:36` documents `/assistant` as the
  server-side assistant transport endpoint.
- `api/runtime/types.py` defines the neutral server-side runtime port.
- `api/runtime/deepagents.py` and `api/runtime/openai_agents.py` run model
  calls behind that port.

That is a real product thesis change. The repo can still be a deterministic
normalization bench, but the PRD currently describes a different trust boundary
than the code implements.

Recommended change:

- Rewrite the PRD invariants around the actual architecture: deterministic data
  layer plus server-hosted agent runtime, with budget controls and traces
  constraining the model surface.
- If the original "external calling agent only" thesis still matters, record
  the change as an ADR or PRD amendment instead of leaving both claims active.

### 3. Structured response models are dataclasses with manual dict serializers

Severity: medium-high.

The local Python rule says structured data should use `pydantic`, not bare dicts
or ad hoc serializers. The most important response and report shapes are still
dataclasses with `to_dict()` / `as_record()`:

- `normalize/query.py:41` to `normalize/query.py:130`: `CitationBlock`,
  `CompositeCitationEntry`, `CompositeCitation`, and `CompareResult`.
- `normalize/schema.py:47` to `normalize/schema.py:90`: `IndexRow.as_record()`.
- `normalize/schema.py:107` to `normalize/schema.py:151`:
  `CitationVerification` and `IndexReport.to_dict()`.
- `normalize/data_quality.py:27` to `normalize/data_quality.py:50`:
  `ProviderQuality.to_dict()`.
- `api/budgets.py:50` to `api/budgets.py:71`: `SessionUsage` and `BudgetBlock`.

The issue is not that dataclasses are broken. The issue is that these are
contract-bearing structures, and the current pattern spreads serialization
rules across manual methods. That weakens validation, makes literal values like
`row_kind`, `rate_unit`, and `BudgetBlock.reason` stringly typed, and keeps
return types at `dict[str, Any]`.

Recommended change:

- Introduce literal, grep-friendly Pydantic models for contract shapes:
  `CitationBlock`, `CompositeCitationEntry`, `CompositeCitation`,
  `CompareResult`, `CompareResponse`, `LookupResponse`, `IndexRow`,
  `IndexReport`, `ProviderQuality`, and `BudgetBlock`.
- Use `model_dump()` at the boundary instead of manual `to_dict()` methods.
- Use `Literal` or enums for constrained fields such as `row_kind`,
  `rate_unit`, `ranked_by`, provider quality status, and budget block reason.

### 4. `normalize/query.py` is a god module for the normalization query surface

Severity: medium-high.

`normalize/query.py` is 520 lines and owns too many concepts:

- Response and citation shapes: `normalize/query.py:41` to
  `normalize/query.py:130`.
- Public query orchestration: `normalize/query.py:133` to
  `normalize/query.py:230`.
- Lookup response construction: `normalize/query.py:238` onward.
- Flex-rate synthesis: `normalize/query.py:295` to `normalize/query.py:425`.
- Flex-rule validation and loading: `normalize/query.py:435` to
  `normalize/query.py:458`.
- Receipt reading and snapshot age calculation: `normalize/query.py:486` to
  `normalize/query.py:509`.

The module still works, but it violates the literal-concept standard. It mixes
result models, ranking, Polars filtering, flex synthesis, citation construction,
and time parsing. That makes future pricing changes harder to review because a
small provider-specific or citation change lands in the same file as the public
query API.

Recommended change:

- Keep `normalize/query.py` as the public facade exposing `compare()` and
  `lookup()`.
- Move models to `normalize/models.py` or a more precise name such as
  `normalize/query_models.py`.
- Move citation construction to `normalize/citations.py`.
- Move flex-rate synthesis to `normalize/flex_synthesis.py`.
- Move closest-larger instance filtering/ranking to `normalize/instance_ranking.py`.
- Keep behavior unchanged and back it with the existing integration tests before
  changing any logic.

### 5. Snapshot age parsing is duplicated instead of being one source of truth

Severity: medium.

Timezone-aware snapshot age is a load-bearing citation rule. It appears in more
than one place:

- `normalize/query.py:501` to `normalize/query.py:502`.
- `normalize/data_quality.py:132` to `normalize/data_quality.py:134`.
- `ingest/_shared.py:56` to `ingest/_shared.py:60` parses receipts for freshness.

The implementations are currently correct, but duplication around this rule is
risky because one future edit can reintroduce naive local time.

Recommended change:

- Create a single helper, for example `normalize/time.py` or
  `normalize/snapshot_time.py`, with `parse_fetched_at()` and
  `snapshot_age_hours()`.
- Use it in `normalize/query.py`, `normalize/data_quality.py`, and
  `ingest/_shared.py`.
- Preserve the existing tests that assert the 24h boundary and add one direct
  unit test for a trailing-`Z` timestamp.

### 6. Budget enforcement mixes identity, SQLite persistence, checks, and cost view

Severity: medium.

`api/budgets.py` explicitly states that three concerns live together
(`api/budgets.py:4` to `api/budgets.py:24`). The file is now 375 lines and
contains:

- SQLite schema and init: `api/budgets.py:77` to `api/budgets.py:140`.
- Identity and hashing: `api/budgets.py:146` to `api/budgets.py:167`.
- Session reads and transactional usage writes: `api/budgets.py:173` to
  `api/budgets.py:264`.
- Budget checks: `api/budgets.py:270` to `api/budgets.py:361`.
- Cost rendering: `api/budgets.py:367` to `api/budgets.py:375`.

This is not a call for a new abstraction layer. It is a concrete split along
literal concepts that already exist. The file is crossing the "growing and
mixed concerns" threshold from the reference rules.

Recommended change:

- Split into small literal modules such as:
  - `api/budget_identity.py` for `hashed_client_id()`,
    `new_session_id()`, and `session_id_fingerprint()`.
  - `api/budget_store.py` for schema, connection, reads, and writes.
  - `api/budget_policy.py` for `check_global_daily()`,
    `check_client_rate()`, and `check_session_cap()`.
  - `api/budget_cost_view.py` for `tokens_to_usd_view()`.
- Keep a temporary `api/budgets.py` facade only if the import churn is too high
  for one commit; because this is v0, deleting the facade in the same arc is
  better once callers are moved.

### 7. `web/package.json` uses `latest` for core UI packages

Severity: medium.

`web/package.json:12` and `web/package.json:13` set
`@assistant-ui/react` and `@assistant-ui/react-markdown` to `latest`.

That conflicts with repeatable builds and with the code's own comments about
version-specific assistant-ui behavior, for example `web/lib/session-limit.ts`
documents a workaround for the current assistant-ui version. A fresh install
can silently pull a different assistant-ui API surface while leaving the source
comments and workarounds behind.

Recommended change:

- Pin `@assistant-ui/react` and `@assistant-ui/react-markdown` to the versions
  already resolved in `web/pnpm-lock.yaml`.
- If using "latest" is intentional during active exploration, state the revisit
  trigger in `TASKS.md`; otherwise treat this as dependency drift.

### 8. Frontend still carries scaffold residue instead of domain language

Severity: medium-low.

The chat shell still opens with generic assistant-ui copy:

- `web/components/assistant-ui/thread.tsx:113` to
  `web/components/assistant-ui/thread.tsx:123`: "Hello there!" and "How can I
  help you today?"
- `web/components/assistant-ui/thread.tsx:166` to
  `web/components/assistant-ui/thread.tsx:168`: generic "Send a message..."
  placeholder.
- `web/README.md:1` to `web/README.md:61`: generated "Assistant Transport
  Example" instructions, including env names that do not match the repo's
  `web/.env.example`.

This matters because the frontend is the first product surface. The repo is a
specific cloud-pricing bench, not a generic assistant demo.

Recommended change:

- Replace the welcome copy and placeholder with pricing-specific prompts.
- Replace `web/README.md` with a short project-specific note: `pnpm --dir web
  install`, `just web`, `BACKEND_ORIGIN`, and the no-model-keys-in-browser
  invariant.

### 9. The comparison table duplicates backend citation semantics in loose TypeScript types

Severity: medium-low.

`web/components/tools/comparison-table.tsx:13` to
`web/components/tools/comparison-table.tsx:52` hand-defines loose optional
types for the backend tool result. The same file then implements frontend
citation interpretation:

- `rowAgeHours()` chooses max composite age at
  `web/components/tools/comparison-table.tsx:81` to
  `web/components/tools/comparison-table.tsx:88`.
- `rowSource()` chooses the first composite source at
  `web/components/tools/comparison-table.tsx:90` to
  `web/components/tools/comparison-table.tsx:92`.

The current logic is understandable, but this is exactly the kind of contract
that should have one source of truth. Today the backend has Python dicts, the
frontend has optional TypeScript shapes, and the documented SPEC shape is a
third copy.

Recommended change:

- Once Python response models are Pydantic, generate or hand-maintain a small
  JSON Schema for the tool result and import a matching TypeScript type.
- Split citation rendering into `web/components/tools/citation-age.tsx` and
  `web/components/tools/citation-source.tsx` or similarly literal files once
  excerpt-on-click lands.
- Keep the table focused on table layout and ranking display.

### 10. The comparison table uses emoji medals despite the no-emoji rule

Severity: low.

`web/components/tools/comparison-table.tsx:55` defines medal emoji for ranking.
The repo guidance bans emoji in prose, code, comments, commit messages, and
docs. This is a small issue, but easy to fix and visible in the UI.

Recommended change:

- Replace medals with plain rank numbers or a non-emoji visual treatment in
  CSS.

## Suggested Refactor Order

1. Fix doc drift first: update `README.md`, `PRD.md`, `SPEC.md`, and
   `TASKS.md` to match ADR-0012 and the current implemented slice.
2. Convert the normalization response/report shapes to Pydantic models.
3. Split `normalize/query.py` along literal concepts without changing behavior.
4. Centralize snapshot time parsing and age calculation.
5. Split `api/budgets.py` once the model cleanup is stable.
6. Pin frontend dependencies and remove assistant-ui scaffold residue.

## What I Would Not Refactor Yet

- The runtime port itself is a good boundary. `api/runtime/types.py` keeps
  transport framework-neutral, and the adapters contain framework imports.
- Provider builders under `normalize/builders/` are not obviously over-split or
  under-split from this pass. They are literal by provider and should stay that
  way unless a third repeated parsing pattern appears.
- The direct Polars use in the normalization layer is appropriate. The issue is
  module cohesion and typed contracts, not the dataframe choice.
