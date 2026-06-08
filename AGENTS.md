
# Agent instructions

This project has two modes the agent operates in:

1. **Coding agent mode**: building the project itself (ingest, scripts, exploration code). Rules: universal-AGENTS.md and python.md. Nothing project-specific beyond those.
2. **Price / cloud agent mode**: answering pricing queries the user puts to the agent inside this project. Rules: the citation contract below.

The two modes coexist in this file in v0. Post-v0, the price / cloud rules move into their own space (likely `price-agent/AGENTS.md` loaded via slash command or workspace switch) so a coding session in this repo is not weighted down by runtime rules it never uses.

## Coding Agent mode

In coding agent mode, read the repo-local context before editing:

1. `README.md` for project purpose, setup, and current status.
2. `PRD.md` for product scope and non-goals.
3. `SPEC.md` for technical contracts and data shapes.
4. `architecture-decisions/README.md` plus any ADR relevant to the files being changed.

Coding rules:

- Keep Python under `backend/src/` with absolute imports.
- Use type hints for new Python code.
- Prefer dataclasses or Pydantic models for structured data over untyped dict contracts.
- Use `uv` for Python dependencies and `just check` for local verification.
- Do not commit local runtime artifacts: `.env`, `.env.*`, `.infisical.json`, `store/`, `var/`, `.venv/`, caches, frontend build output, or `node_modules`.
- Do not put provider credentials, API keys, traces, local database files, or fetched pricing snapshots in git.
- Keep docs split by ownership: `README.md` for users, `PRD.md` for intent, `SPEC.md` for technical contracts, `AGENTS.md` for agent behavior.
- Write tests only for desired behavior of the current architecture; do not write tests that assert the absence of previous architectural patterns; add/refer to ADRs for historical context.”


## Price / cloud agent mode

This project is a citation-backed cloud pricing tool. The agent is the major player. Every claim about a price or an equivalence carries a citation that another agent or a human can verify by re-fetching the source. Prices quoted without a citation, equivalences asserted without a snapshot to back them, or rankings that hide the candidates considered all violate the contract.

**SPEC.md owns data shapes** (normalization API, taxonomy file formats, citation block schema). This file owns agent behavior rules. When the two reference the same structure (e.g. the citation block), SPEC.md is canonical; the behavior rules here describe how to use it, not how to define it.

### Calling the normalization layer

The agent's primary tool for pricing answers is the normalization layer (`normalize.compare`, `normalize.lookup`). Per ADR-0009 and ADR-0012 the runtime agent lives inside the FastAPI process behind a framework-neutral port, so its tools call `normalize.compare` / `normalize.lookup` **in-process** (a direct Python import, no HTTP self-hop). The FastAPI HTTP endpoints remain for external consumers (the CLI, other services) and are not the path the in-repo agent uses to reach its own data layer. The normalization layer encapsulates the snapshot walk, taxonomy lookup, match policy (closest-larger), and citation block construction. The agent quotes its output and surfaces the citation in prose; it does not re-do the deterministic work.

Direct snapshot walking is still allowed when (a) the normalization layer does not cover the question (e.g. "are there any Power Systems prices in IBM's catalog?"), or (b) the user explicitly asks the agent to explore the raw data. In those cases the agent constructs the citation block by hand per SPEC.md.

### Fetching prices

Provider price catalogs live as timestamped snapshot directories at `store/<provider>/<ISO>/`. Seven providers in v0: `aws`, `gcp`, `azure`, `oracle`, `vultr`, `linode`, `ibm`. Each has an ingest module under `backend/src/ingest/` that fetches the catalog, writes one or more data files plus a `receipt.json`, and prints the receipt to stdout. Most ingest modules are single files; IBM is a package because its catalog walk has multiple steps.

Before answering any pricing question:

1. Check `store/<provider>/` for the latest snapshot directory.
2. If the latest snapshot is under 24 hours old, use it. Do not re-fetch.
3. Otherwise run `uv run python -m ingest.<provider>`. The ingest module enforces the freshness rule itself; calling it on a fresh store is a no-op that returns the existing receipt.
4. If the user signals they want fresh data ("refetch", "live prices", "as of right now"), run with `--force`.

Never quote a price from training memory. Every number in the response must trace to a snapshot file that exists on disk.

### Snapshot layout per provider

Each provider's snapshot directory contains a `receipt.json` plus one or more data files. The structure differs by provider:

- **aws** (`store/aws/<ISO>/`): `region_index.json` plus one `<region>.json` per region in the v0 set (currently `us-east-1`, `eu-central-1`, `ap-southeast-1`). Each region file is the raw AWS Price List Bulk response for that region. Big (hundreds of MB per region).
- **gcp** (`store/gcp/<ISO>/`): single `skus.json` containing all Compute Engine SKUs across every GCP region merged into one list. Each SKU carries its own `serviceRegions` field. Smaller (~40 MB).
- **azure** (`store/azure/<ISO>/`): one `<region>.json` per region in the v0 set (currently `eastus`, `westeurope`, `southeastasia`). Each region file is narrowed by an OData filter (`serviceName eq 'Virtual Machines' and priceType eq 'Consumption'`). Spot pricing is mixed in under the Consumption taxonomy and must be post-filtered by `meterName` if OnDemand-only is intended. Reservation is excluded.
- **oracle** (`store/oracle/<ISO>/`): single `products.json` containing the full Oracle price list (~640 items) across every service category, not just compute. Oracle publishes one global list price per SKU, so there is no per-region split in the data; the receipt's `regions_included` is `["global"]` to make this explicit. Modern compute shapes (E3, E4, E5, X9, A1) price OCPU and memory as separate SKUs, so a "N vCPU M GB" answer combines two items. Each item carries `currencyCodeLocalizations[]` with prices per currency; filter to `currencyCode == "USD"` for the v0 comparison.
- **vultr** (`store/vultr/<ISO>/`): single `plans.json` with all Vultr plans (~100 items, ~70 KB). Each plan carries `vcpu_count, ram, disk, bandwidth, monthly_cost, hourly_cost, type` (vc2/vhf/vhp/etc.), and `locations[]`. Price is global per plan; `locations[]` is availability only, not a pricing dimension (same shape as Oracle and DigitalOcean).
- **linode** (`store/linode/<ISO>/`): single `types.json` with all Linode types (~75 items). Distinctive schema: each type has a base `price.{hourly,monthly}` that applies globally PLUS a `region_prices[]` list of explicit per-region overrides. The receipt's `regions_with_overrides` lists which regions actually deviate (currently `br-gru` and `id-cgk`). The agent MUST check `region_prices[]` for a region match before quoting; the base price is the fallback for any region not listed. No other v0 provider publishes per-region overrides this way.
- **ibm** (`store/ibm/<ISO>/`): two files. `services.json` is the full IBM Cloud Global Catalog (~319 services, ~6 MB) across every product line (Watson, Cloud Paks, IaaS, PaaS, databases). `compute.json` is the v0 compute slice, walked three-hop from the catalog: filter catalog by name in `{is.instance, is.bare-metal-server, is.dedicated-host}`, follow each one's `children_url` to enumerate plans, then call `/{plan_id}/pricing/deployment` per plan. That single deployment endpoint returns prices for every region the plan exists in (no client-side region filter; the receipt records the union of `deployment_region` values seen). Pricing data lives under `compute.<service_name>.pricing.<plan_id>.resources[].metrics[]` and is the only place real compute numbers exist for IBM: the plain `/pricing` endpoint at the service or plan level returns $0 for base instance-hours because base price is fully regional. Each metric carries amounts by country/currency; filter to `country == "USA"` and `currency == "USD"` for the v0 comparison, and pick the metric whose `metric_id` matches `part-is.instance-hours-<plan-name>` (excluding `-dh-` dedicated-host variants and `-sgx` enclaves unless that's what the user asked for). Power Systems (`power-iaas.pvm-instance`) and reservation SKUs are out of v0.

Citations point at the specific file containing the price.

### Citation behavior

The internal citation block schema (`source_url`, `store_path`, `json_path`, `fetched_at`, `age_hours`) is defined in SPEC.md. The model-visible wire shape deliberately drops `store_path` and adds a logical `snapshot` ref per ADR-0008, so local filesystem paths are not exposed through the unauthenticated assistant surface. This section governs how the agent surfaces citations.

- Every price in a response carries a citation through the tool result AND has its age surfaced inline in prose. The user must never have to dig into JSON to learn how old the data is.
- Inline prose format: append `(snapshot Xh old)` to every price the agent quotes.
- The model emits an `AnswerPlan` JSON object, not final user-facing prose. Backend policy validates every plan claim against the latest tool result and renders the prose by interpolation per ADR-0013.
- If `age_hours` exceeds 24 for any snapshot used, the rendered answer marks the snapshot stale in prose and prompts the user about re-fetching with `--force`.

#### Computing `age_hours` correctly

`fetched_at` in every receipt is ISO 8601 UTC with a trailing `Z` (e.g. `"2026-05-25T07:32:08.467260Z"`). The `Z` is the timezone marker, equivalent to `+00:00`. The storage is unambiguous; do not strip it.

To compute age, parse `fetched_at` as timezone-aware UTC and compare against timezone-aware UTC now. The minimal Python:

```python
from datetime import datetime, timezone
parsed = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
age_hours = (datetime.now(timezone.utc) - parsed).total_seconds() / 3600
```

Do not use `datetime.now()` without a `tz=` argument: it returns naive local time, and subtracting it from a UTC string yields a value off by the local UTC offset (e.g. -4h on a US Eastern machine, making a freshly-fetched snapshot look like it's from the future). Do not strip `Z` and parse the string as naive: same trap. Always work in UTC throughout the citation pipeline. The normalization layer is the canonical implementation of this computation; reach for it before re-implementing.

### Equivalence claims

Equivalence between provider instance types (claiming AWS `m5.xlarge` and GCP `n2-standard-4` are interchangeable for a "4 vCPU general purpose" spec) is captured in `backend/src/normalize/taxonomy/families.json` for the cases v0 has hand-seeded. When quoting from the taxonomy, surface the `dimensions_matched` and `dimensions_not_normalized` fields in prose so the equivalence basis is visible.

When the user's question reaches a spec not covered by the taxonomy, the equivalence is judgment, not lookup. Cite the snapshot fields used to justify it, name the dimensions the equivalence holds on (vCPU count, RAM, CPU class) and the dimensions it does not (CPU generation, baseline performance, network bandwidth, included storage), and flag that the equivalence is agent-derived rather than from the taxonomy. v1 will queue agent-derived equivalences for PR review (`propose_equivalence`); for v0, surfacing them transparently in prose is sufficient.

### Ranking

When returning "cheapest" or any ranking, list all candidates considered, not just the winner. The reader must see the comparison set was not cherry-picked.

### Worked example

User: "cheapest 4 vCPU 8 GB general-purpose VM across the big 3 in EU?"

Agent response opens with:

> Cheapest is AWS `m5.xlarge` at $140.16/mo (eu-central-1, snapshot 6h old). Close runners: GCP `n2-standard-4` at $148.92/mo (europe-west3, snapshot just fetched), Azure `Standard_D4s_v5` at $154.20/mo (westeurope, snapshot just fetched). Equivalence basis: vCPU and RAM exact, all general-purpose class (excluded compute- and memory-optimized SKUs). Dimensions not normalized: CPU generation, network bandwidth, attached storage.

Followed by one public citation entry per result in the tool result / UI Source primitive. Each public citation carries `source_url`, `json_path`, `age_hours`, and a logical `snapshot` ref. Internal verification resolves that snapshot ref back to the specific `store/<provider>/<ISO>/<file>` path.

Verification path for the user: open the Source primitive or citation excerpt for each result, confirm the `json_path` resolves to the quoted number, and compare against `source_url` if a live check is needed.


### Commit Hygiene (agentic era)
- **Atomic commits:** one logical change each. Split sweeping agent output into focused commits — keeps review, `bisect`, and rollback sane.
- **Subject = Conventional Commits**, body = the "why". Format: `type(scope): summary` (imperative, ≤72 chars, lowercase, no trailing period).
  - Types: `feat` (new capability), `fix` (bug), `refactor` (no behavior change), `test`, `docs`, `chore` (tooling/deps), `perf`, `style` (formatting), `build`, `ci`, `revert`.
  - Scope = the literal concept touched: `feat(action_classifier): add correction detection`, `fix(decision_log): handle missing file on load`.
  - Breaking change → `feat(action_classifier)!: ...` or a `BREAKING CHANGE:` footer.
- **Body captures the Decision Shadow:** why this change, constraints, rejected alternatives — the reasoning the diff can't show. Note what you verified ("tested manually with valid + invalid input").
- **Provenance in trailers, never the subject.** Attribution must be accurate, granular, and intentional — never auto-blanket. A wrong/false trailer is worse than none. This repo's `Co-Authored-By` trailer is the consensual, intentional kind — keep it.
- **Explore-many, commit-one:** scratch/exploration paths stay out of main history; land only the cleaned, chosen path.
- **Commit at the arc boundary, not the moment work looks done.** The commit unit is a logical *arc* (one fix, or one feature), not a turn or an instruction. Hold the change in the working tree; don't fire the instant it compiles — an immediate commit bakes in agentic bugs before any human has reacted.
  - **An arc spans as many turns as it needs:** the opening instruction plus the corrections that settle it. It stays uncommitted while open.
  - **On each new human instruction, classify it.** *Correction/refinement of an open arc* ("that's wrong", "also handle X there") → don't commit; fold it in, the arc stays open. *Forward/distinct work* → the arc the human just moved on from has cleared the strongest signal available (they saw enough to leave it); if it's coherent and the pre-commit hook passes, **autocommit + push that arc alone**, then start the new one.
  - **One instruction can span multiple arcs** ("fix X and start Y") → split into separate atomic commits; never merge a fix and a feature. **Multiple arcs can close in one turn** → multiple commits land that turn.
  - **Never commit half-done or hook-failing work**, even at a boundary — commit only the part that stands alone, or keep waiting. When unsure whether an instruction touches an open arc, **bias to holding** (a wrongful commit costs more to unwind than a delayed one).
  - **Surface, never silent:** report open/uncommitted arcs at session end or on any explicit stop/done signal, so nothing is lost. "Human moved on" is acceptance-by-proxy, not real review — this cuts premature commits, it does not guarantee correctness.
  - Explicit overrides win: "commit now" / "don't commit yet" is obeyed immediately, no classification.
- **Pushing to `main` is pre-approved (v0, single dev).** No branch or review gate yet — commit and `git push` directly. Don't ask permission each time. Revisit this rule the moment a second contributor joins.

### Output & Next Directions
- Be direct: state what was found, what's missing, and what change is needed. Don't narrate internal deliberation.
- After a substantive answer or change, append a short numbered list (`1.` / `2.` / `3.`) of plausible next directions so the user can reply by number.
- Offer real alternatives, not one padded path: at least the top next step in the current arc **plus** one genuine pivot. 2–3 entries, each one line naming a concrete surface.
- Never pad to a fixed count; never fill with housekeeping ("make the next commit", "open an issue"). If you can only think of one direction, think harder about the real alternatives.