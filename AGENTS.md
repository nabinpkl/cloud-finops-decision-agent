
# Agent instructions

This project has two modes the agent operates in:

1. **Coding agent mode**: building the project itself (gates, scripts, exploration code). Rules: universal-AGENTS.md and python.md. Nothing project-specific beyond those.
2. **Price / cloud agent mode**: answering pricing queries the user puts to the agent inside this project. Rules: the citation contract below.

The two modes coexist in this file in v0. Post-v0, the price / cloud rules move into their own space (likely `price-agent/AGENTS.md` loaded via slash command or workspace switch) so a coding session in this repo is not weighted down by runtime rules it never uses.

## Coding Agent mode
In coding agent mode load these first before editing code if not already loaded into your context.

**At session start, and again after any context compaction, read these files if they are not already in context. Do not skip. Do not assume they are loaded.**
These files do not live in this repo. They are referenced by absolute path from the canonical location. If a path no longer resolves, stop and surface the failure instead of inventing rules.

1. `/Users/nabin/projects/atc-agent-traffic-control/taste/library/universal-AGENTS.md` cross-cutting worker rules. Authoritative for voice, scope discipline, output format, coding rules, and how to write for other agents.
2. `/Users/nabin/projects/atc-agent-traffic-control/taste/library/iteration/TASKS-MD-Guidelines.md` the shape of `TASKS.md` for any iteration that opens.
3. `/Users/nabin/projects/atc-agent-traffic-control/taste/library/languages/python.md` Python conventions. Authoritative when writing or reviewing Python in this repo.


## Price / cloud agent mode

This project is a citation-backed cloud pricing tool. The agent is the major player. Every claim about a price or an equivalence carries a citation that another agent or a human can verify by re-fetching the source. Prices quoted without a citation, equivalences asserted without a snapshot to back them, or rankings that hide the candidates considered all violate the contract.

**SPEC.md owns data shapes** (normalization API, taxonomy file formats, citation block schema). This file owns agent behavior rules. When the two reference the same structure (e.g. the citation block), SPEC.md is canonical; the behavior rules here describe how to use it, not how to define it.

### Calling the normalization layer

The agent's primary tool for pricing answers is the normalization layer (`normalize.compare`, `normalize.lookup`). Per ADR-0009 the runtime agent lives inside the FastAPI process (Python OpenAI Agents SDK), so its tools call `normalize.compare` / `normalize.lookup` **in-process** (a direct Python import, no HTTP self-hop). The FastAPI HTTP endpoints remain for external consumers (the CLI, other services) and are not the path the in-repo agent uses to reach its own data layer. The normalization layer encapsulates the snapshot walk, taxonomy lookup, match policy (closest-larger), and citation block construction. The agent quotes its output and surfaces the citation in prose; it does not re-do the deterministic work.

Direct snapshot walking is still allowed when (a) the normalization layer does not cover the question (e.g. "are there any Power Systems prices in IBM's catalog?"), or (b) the user explicitly asks the agent to explore the raw data. In those cases the agent constructs the citation block by hand per SPEC.md.

### Fetching prices

Provider price catalogs live as timestamped snapshot directories at `store/<provider>/<ISO>/`. Seven providers in v0: `aws`, `gcp`, `azure`, `oracle`, `vultr`, `linode`, `ibm`. Each has a gate under `src/gates/` that fetches the catalog, writes one or more data files plus a `receipt.json`, and prints the receipt to stdout. Most gates are single modules; IBM is a package because its catalog walk has multiple steps.

Before answering any pricing question:

1. Check `store/<provider>/` for the latest snapshot directory.
2. If the latest snapshot is under 24 hours old, use it. Do not re-fetch.
3. Otherwise run `uv run python -m gates.<provider>`. The gate enforces the freshness rule itself; calling it on a fresh store is a no-op that returns the existing receipt.
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

The citation block schema (the five JSON fields `source_url`, `store_path`, `json_path`, `fetched_at`, `age_hours`) is defined in SPEC.md. This section governs how the agent surfaces it.

- Every price in a response carries a citation block AND has its age surfaced inline in prose. The user must never have to dig into a JSON block to learn how old the data is.
- Inline prose format: append `(snapshot Xh old)` to every price the agent quotes. The full JSON citation lives below the prose answer (or inside the rendered tool component's Source primitive in the agent UI).
- If `age_hours` exceeds 24 for any snapshot used, mark that answer as stale in prose and prompt the user about re-fetching with `--force`.

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

Equivalence between provider instance types (claiming AWS `m5.xlarge` and GCP `n2-standard-4` are interchangeable for a "4 vCPU general purpose" spec) is captured in `src/normalize/taxonomy/families.json` for the cases v0 has hand-seeded. When quoting from the taxonomy, surface the `dimensions_matched` and `dimensions_not_normalized` fields in prose so the equivalence basis is visible.

When the user's question reaches a spec not covered by the taxonomy, the equivalence is judgment, not lookup. Cite the snapshot fields used to justify it, name the dimensions the equivalence holds on (vCPU count, RAM, CPU class) and the dimensions it does not (CPU generation, baseline performance, network bandwidth, included storage), and flag that the equivalence is agent-derived rather than from the taxonomy. v1 will queue agent-derived equivalences for PR review (`propose_equivalence`); for v0, surfacing them transparently in prose is sufficient.

### Ranking

When returning "cheapest" or any ranking, list all candidates considered, not just the winner. The reader must see the comparison set was not cherry-picked.

### Worked example

User: "cheapest 4 vCPU 8 GB general-purpose VM across the big 3 in EU?"

Agent response opens with:

> Cheapest is AWS `m5.xlarge` at $140.16/mo (eu-central-1, snapshot 6h old). Close runners: GCP `n2-standard-4` at $148.92/mo (europe-west3, snapshot just fetched), Azure `Standard_D4s_v5` at $154.20/mo (westeurope, snapshot just fetched). Equivalence basis: vCPU and RAM exact, all general-purpose class (excluded compute- and memory-optimized SKUs). Dimensions not normalized: CPU generation, network bandwidth, attached storage.

Followed by one JSON citation block per result in the shape above. Each `store_path` points at the specific file the number came from (e.g. `store/aws/<ISO>/eu-central-1.json`, `store/gcp/<ISO>/skus.json`, `store/azure/<ISO>/westeurope.json`).

Verification path for the user: open each listed `store_path`, walk to `json_path`, confirm the number. Or hit `source_url` directly and compare against the live response.
