# PRD — Agent-Consumable Cloud Price Bench


## 1. Why this exists (the real problem)

The problem here is **not** "developers can't compare cloud prices." That is solved (see §4). The problem is that I don't yet have a *falsifiable* answer to where an agent actually earns its cost inside a real task — which layers need a model, and which are plumbing that is cheaper, faster, and more deterministic without one.

Cloud VM pricing is the chosen bench because it has **authoritative ground truth**: the provider's API *is* the truth, so "did the agent's judgment hold" is checkable by re-fetch. That property is rare, and it makes pricing a clean specimen for *testing* the claim rather than asserting it.

## 2. What it is

- An open, **agent-consumable** comparator that answers "cheapest VM across providers for spec X, right now, with citations." Three layers in one repo: deterministic **ingest modules** that snapshot per-provider catalogs, a deterministic **normalization layer** (Python module + FastAPI + CLI) that maps a spec to ranked candidates with citations, and a server-hosted **agent runtime** that draws the comparison surface from the normalization layer's output. The agent runtime runs in FastAPI behind a framework-neutral port (ADR-0012): `langchain` is the default adapter and the OpenAI Agents SDK is optional. A **frontend** (`frontend/`, Next.js + assistant-ui) renders the stream. SPEC.md owns the contracts.
- A **test bench** for one falsifiable claim (§6), instrumented so that "where did the agent earn its cost" is *observable*, not asserted. The normalization layer is the deterministic baseline; the agent is the system under test; eval scores the divergence.
- Scoped to the **greenfield moment** — choosing where to deploy a fresh project — for the audience that can actually act on the answer.

## 3. What it is NOT

- Not a product. No growth target, no users to acquire, no revenue.
- Not another comparison website. Not trying to beat or replace CloudPrice / getdeploying.
- Not a spend-management / FinOps dashboard for resources already running.
- **Prospective only, never retrospective.** The sidebar agent is a *pre-purchase* comparison advisor over public catalog prices. It does not connect to a cloud account, and does not track the user's own resources, usage, or spend. The verb is "if I deploy this spec, what would it cost where, and prove it," never "what are my resources costing me." Wiring the agent to account/usage data would slide it into the OpenCost / Kubecost / Vantage / CloudZero space this bench deliberately excludes (above) and would break the citation contract, which only holds for re-fetchable public catalogs. (The AWS console cost-agent pattern inspired the dual-surface UX shape, not its retrospective data direction.)
- Not a model-first wrapper. The model surface is server-hosted, budgeted, traced, and limited to the semantic-judgment layer; the ingest and normalization layer remain deterministic.
- Not a forever-research project — it has a defined v0 and a finding to produce (§8–9).

## 4. What already exists (prior art)

Two crowded categories, one empty quadrant.

**Post-deployment spend management** — OpenCost, Kubecost, Infracost, Komiser (open); Vantage, CloudZero, Finout, Amnic, Cloudchipr (commercial). These answer "where am I wasting money on what I *already run*." Every AI-native / agent / MCP offering found lives here, pointed at your existing bill (e.g. Vantage's FinOps agent and its MCP server).

**Pre-purchase comparison** — CloudPrice (cloudpriceindex.org; 14 providers, ~6-hour refresh from official APIs), getdeploying (does the instance-equivalence "closest config" mapping directly), VPSBenchmarks (adds performance). These already cover the solo-dev provider set (Hetzner, DigitalOcean, Vultr, Linode, OVH, Oracle + the big three) and already solve **freshness and normalization** — but as static, ad/affiliate-monetized sites you read with your eyes. None is agent-native or agent-queryable.

**Empty quadrant:** pre-purchase + agent-consumable + open. No MCP surface a coding agent can call for a cited cheapest-VM answer at deploy time. That emptiness is the bench's home — and the fact that the static baseline already exists is a *feature*: it is the non-agent **control group** to measure the agent layer against.

## 5. Target context (bench, not market)

Solo devs and small teams at project start. They can switch freely — switching cost is zero before anything is deployed — unlike enterprises held in place by egress costs, contracts, and committed-use discounts. **v0 user is me.**

## 6. What it's trying to solve (the goal)

Test this claim, stated so it can be broken:

> **Agents earn their cost only at the irreducible semantic-judgment layer. Memory turns a one-time probabilistic judgment into a deterministic, reusable asset. Fetch, diff, and store are plumbing — cheaper without an agent.**

**Falsification conditions** (how I'd know it's wrong):
- A layer where the agent reliably beats the deterministic alternative *without* doing irreducible judgment → breaks "everything else is plumbing."
- The judgment layer turns out to be formalizable (a rule set or lookup captures the equivalences) → then even that layer didn't need a model, and the honest move is to delete the agent.

**A second open question (no answer yet).** The claim above is about where the agent earns its cost on a single query. A larger experiment rides on top, and this repo is the substrate for it. Every ingest module in `backend/src/ingest/` is a prefilter. Someone chose what to fetch, what to drop, what to normalize. A model handed only the post-ingest view inherits those decisions silently and answers as if they were neutral. The open question is whether a model given the raw snapshots, citation tools, and exploration freedom can (1) surface insights deterministic ingestion would have prefiltered out and (2) notice when reality has shifted under a stable workflow. This is closer in spirit to post-training or agent-training than to a single-query bench. Self-improvement and surprise-noticing are the most valuable agent behaviors precisely because they cannot be hand-coded; when the world breaks but the workflow stays the same, the only thing that matters is whether the model catches it. We do not have a way to measure either yet. What we have is the precondition: every trace visible, every citation auditable, so any noticing the model claims can be checked rather than fabricated. The findings log (`FINDINGS.md`) is where evidence for or against this accumulates.

## 7. Design principles / invariants

1. **No model in the data layer.** Ingest modules, indexes, taxonomy loading, lookup, comparison, citations, freshness, and drift detection are deterministic plumbing.
2. **The server-hosted agent is the only judgment layer.** The model sees the normalization output through tools and turns it into an answer; it does not fetch prices directly or bypass citations.
3. **Memory freezes judgment.** An agent-derived equivalence, once human-approved, becomes a deterministic lookup. The probabilistic step happens once, not per query.
4. **Trust the data and the human, not the agent.** Every price carries provenance + an `as_of` timestamp + freshness. A citation is a claim about a *past* state, not a guarantee about the present. The human verifies only *novel* mappings.
5. **Review is demand-driven, not catalog-driven.** Only mappings a real query touches ever need eyes. Approved mappings stay trusted until a "surprise" re-opens them.
6. **Drift detection is a diff + cron, never a model.**
7. **The model surface is bounded.** `/assistant` is budgeted, rate-limited, traced, and selected through `AGENT_RUNTIME`; deterministic HTTP endpoints remain model-free.

## 8. Draft Scope

**v0 — in:**
- Per-provider deterministic ingest writing timestamped snapshots to `store/<provider>/<ISO>/` for AWS, GCP, Azure, Oracle, Vultr, Linode, IBM. Done.
- Normalization layer (`backend/src/normalize/`): Python module + FastAPI wrapper + `python -m normalize` CLI. `compare(vcpu, ram_gb, region?, family?)` -> cheapest-per-provider ranked, each result carrying provider, instance, monthly/hourly USD, citation block, and `considered_count`. `lookup(provider, instance_type, region)` for single-instance answers. Match policy is closest-larger (>=vCPU and >=RAM).
- Family taxonomy (`backend/src/normalize/taxonomy/families.json`) and region taxonomy (`backend/src/normalize/taxonomy/regions.json`) — JSON files that encode the cross-provider equivalence, hand-seeded, editable in PRs, citable by the agent when it makes equivalence claims. SPEC.md defines the file shape.
- Agent runtime in FastAPI: the loop calls `compare` as an in-process tool through a framework-neutral runtime port (ADR-0012). The default adapter is `langchain`; OpenAI Agents SDK is an optional adapter. The model uses an OpenAI-compatible base URL (provider is config, not a fixed vendor). Frontend (`frontend/`): Next.js + assistant-ui, frontend-only, renders the agent stream. The shipped v0 custom tool component is `ComparisonTable` for multi-provider ranking; `PriceCard` for single-instance lookup is captured for v1. Staleness handled inline in prose per AGENTS.md.
- Eval (`evals/cases/*.yaml`): hand-written behavior suites scored first by deterministic graders on citation correctness, staleness/refusal behavior, provider scoping, and candidate coverage. `python -m evals` runner.

**v0 — Azure narrowing (empirical):** Azure Retail Prices normalizes per `(SKU × region × priceType × OS)` rows. An unfiltered VM fetch is 500+ pages of 1000 items each and trips an unpublished rate limit (~470 sequential requests then HTTP 429 with `Retry-After=60`). v0 narrows to `priceType eq 'Consumption'` and three regions (`eastus`, `westeurope`, `southeastasia`) matching AWS's geographic coverage. The narrowed fetch is 26 pages, ~24k items, ~25s wall time. Spot SKUs are filed under the Consumption taxonomy in Azure's data model so they end up in the snapshot; the agent post-filters by `meterName` when OnDemand-only is intended. Reservation and Savings rates are out of v0; revisit when the agent answers commitment-shape questions. Also `$top` is silently ignored (page size is hard-capped at 1000), so the only lever to shrink the dataset is the OData filter.

**v0 — Oracle scope (empirical):** Oracle's public price list (`apexapps.oracle.com/pls/apex/cetools/api/v1/products/`) is no-auth, no-pagination, single-shot ~3 MB / ~640 items covering every service category. Compute SKUs live alongside everything else; the agent narrows by `serviceCategory` (e.g. `Compute - Virtual Machine`, `Compute - Bare Metal`, `Compute - GPU`). Two structural differences from the big three: (1) list price is published globally per SKU, so there is no per-region split in the data and the snapshot carries `regions_included: ["global"]`; the agent treats Oracle prices as region-invariant in v0 and flags this in the response. (2) Modern shapes (E3, E4, E5, X9, A1) price OCPU and memory as separate SKUs, so a "N vCPU M GB" comparison combines two items — different from AWS/GCP/Azure where one SKU carries the full instance price. Rate limits are not publicly documented; the single-shot fetch sidesteps the question. Each item carries `currencyCodeLocalizations[]` and the agent filters to USD for v0 comparisons.

**v0 — Indie + IBM addition (empirical):** Three more providers join v0 via no-auth public endpoints: Vultr (`/v2/plans`, ~100 items, ~70 KB), Linode (`/v4/linode/types`, ~75 items, ~42 KB), and IBM Cloud (`globalcatalog.cloud.ibm.com/api/v1?q=kind:service`, 319 services, ~6 MB across 7 paginated pages). Vultr matches the Oracle/DigitalOcean shape (global price per plan, `locations[]` is availability only). Linode contributes a fifth schema shape that no other v0 provider uses: a base `price.{hourly,monthly}` plus an explicit `region_prices[]` list of per-region overrides — currently only `br-gru` and `id-cgk` deviate from base. The agent must check `region_prices[]` before quoting a region-specific number. IBM's Global Catalog is the broadest catalog and is hierarchical: the catalog enumerates ~319 services, the compute slice (`is.instance`, `is.bare-metal-server`, `is.dedicated-host`) is reached by following each entry's `children_url` to its plans (~237 plans total), and real per-region prices live at `/{plan_id}/pricing/deployment` one hop further in. The plain `/pricing` endpoint returns $0 for base instance-hours because base price is fully regional. v0 walks all three hops and ships compute pricing alongside the catalog snapshot (`compute.json`, ~340 MB because each plan's response carries every region × every metric × every country/currency combo inline). DigitalOcean is deliberately not in v0: its `/v2/sizes` API returns an account-filtered slice of the published catalog rather than the full catalog, so the citation would point at a snapshot that is true for one account but not for the world. Hetzner and Fly remain on the v1 list (Hetzner needs an auth token; Fly publishes no pricing API at all).

**v0 — out:** monitoring, multi-user, auth, MCP surface, equivalence proposal/review queue, `propose_equivalence` / `list_pending` workflow (taxonomy edits happen in PRs in v0). DigitalOcean (account-filtered API), Hetzner (auth token required), Fly (no pricing API).

**v1 — later:** MCP surface over the normalization layer for IDE/Cursor/Claude Code consumption; "what changed" monitoring reading the v0 snapshots and eval runs; live fetchers ratcheted to higher cadence than 24h; broader catalog; tamper-resistant snapshot integrity (store the snapshot hash in an agent-inaccessible location, so local-integrity becomes externally verifiable rather than relying on "agent did not accidentally touch the store"). v0 keeps an in-store hash for accident-detection only; it is not a notarization claim about what the upstream API actually returned at fetch time. Equivalence proposal workflow (`propose_equivalence`, `list_pending`) returns when taxonomy edits outgrow PR review. Split the price / cloud agent rules out of the project `AGENTS.md` into their own space (likely `price-agent/AGENTS.md`, loaded via slash command or workspace switch) so coding sessions in this repo are not weighted down by runtime rules they never use. Indie providers (Hetzner, DigitalOcean, Fly) as the deploy-target expansion, downhill of the big-3 work once the equivalence model is proven on the harder schemas.

## 9. Success criteria (for a bench, not a product)

1. The agent UI answers a cheapest-VM-by-spec question end to end, rendering a ComparisonTable whose every row carries a citation that resolves to a real snapshot file on disk.
2. The bench produces a **finding** on the claim — *including* the case where the claim is falsified, which is a valid and publishable outcome.
3. The one layer where the agent earns its cost is observable and named, with the plumbing layers (ingest, normalization, taxonomy) demonstrably cheaper done deterministically.
4. The artifact is **legible**: repo + a real agent-interaction transcript + eval results, so it reads as a build, not a take.

## 10. Risks

- **Thought-leadership trap** — producing prose about the thesis instead of the artifact that backs it. *Mitigation:* the repo + transcript is the deliverable; any writeup is a report on the build, not a standalone argument.
- **Over-building** — reaching for the store / monitoring / multi-user machinery before the narrow bench exists. *Mitigation:* the v0 scope above is the line.
- **Seed-price staleness mistaken for a finding** — *Mitigation:* freshness is first-class; seeded values are flagged and never presented as live.
