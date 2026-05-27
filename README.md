# cloud-finops-decision-agent

Citation-backed cloud pricing where the agent draws the UI from a deterministic normalization layer. Ask it "cheapest 4 vCPU 8 GB box across the big 3 in EU" and the agent renders a comparison table whose every row links to the snapshot file the number came from, the JSON path inside that snapshot, and the upstream URL. You verify by clicking through. No trust required.

## Why this exists

Cloud pricing comparison is a solved problem for humans (CloudPrice, getdeploying, VPSBenchmarks). It is not a solved problem for agents. Existing comparison sites are static, ad-supported, and read with eyes. Existing FinOps tooling (OpenCost, Kubecost, Vantage, CloudZero, Infracost) targets the post-deployment spend question, not the greenfield "where should I deploy" decision. The empty quadrant is pre-purchase, agent-callable, and open. That is the space this repo lives in.

The deeper question underneath: where do agents actually help that deterministic gates do not. Cloud pricing is the testbed because the provider's API is the truth, so any claim the agent makes can be checked by re-fetch. Full framing in `DRAFT-PRD.md`.

A second, larger experiment rides on top, and we do not have answers yet. The deterministic gates are themselves prefilters. Someone chose what to fetch, what fields to keep, what regions to include, what to normalize. A model handed only the post-gate view inherits those decisions silently and treats them as neutral ground truth. The open question is whether a model given the raw snapshots, the citation tools, and the freedom to explore can surface insights the prefilter would have missed, and whether it can notice when reality has shifted under a stable workflow. This is closer to post-training or agent-training in spirit than to a single-query benchmark. Self-improvement and surprise-noticing are the most valuable agent behaviors precisely because they cannot be hand-coded; when the world changes but the workflow stays the same, the only thing that matters is whether the model catches it. We do not have a way to measure either yet. What we have is the precondition for trying: every trace visible, every citation auditable, so any noticing the model claims can be checked rather than fabricated. The findings log (`FINDINGS.md`) is where evidence for or against this accumulates.

## How it works

Three layers in one repo. SPEC.md owns the contracts between them.

**Gates** (`gates/`) are deterministic Python scripts that fetch a provider's compute pricing catalog and save a timestamped snapshot under `store/<provider>/<ISO>/`. One gate per provider, with a polite 429-aware HTTP layer in `gates/_shared.py`. They print a JSON receipt to stdout.

**Normalization layer** (`normalize/`) is the deterministic baseline. A Python module + FastAPI wrapper + `python -m normalize` CLI. Reads the snapshots, applies a family + region taxonomy stored as JSON, and answers `compare(vcpu, ram_gb, region, family)` and `lookup(provider, instance_type, region)`. Match policy is closest-larger (≥vCPU and ≥RAM). Output is cheapest-per-provider ranked, each result carrying a full citation block.

**Agent UI** (`web/`) is a Next.js app using `assistant-ui` as the chat shell. The agent calls the normalization layer over HTTP as a tool and decides which custom component to render: `ComparisonTable` for ranking queries, `PriceCard` for single-instance lookups. The agent's prose handles staleness (`(snapshot 6h old)`) and equivalence-dimension disclosure.

The citation contract is the only enforcement layer. Every price the agent quotes carries `source_url`, `store_path`, `json_path`, `fetched_at`, and `age_hours`. If the cited snapshot is over 24 hours old, the agent marks the answer stale and offers a refetch. AGENTS.md is the agent behavior contract; SPEC.md is the data shape contract.

Seven providers in v0: AWS, GCP, Azure, Oracle, Vultr, Linode, IBM. The big three have the gnarliest pricing schemas in the industry, which makes them the right stress test for the equivalence judgment layer. Oracle widens the schema further with globally-published list pricing and OCPU+memory split for modern shapes. Vultr and Linode bring the indie scale into the bench (~100 plans each) and Linode contributes a fifth distinct schema shape via explicit `region_prices[]` overrides. IBM contributes a sixth shape, hierarchical and three-hop: the catalog enumerates ~319 services, the compute slice (VPC virtual servers, bare metal, dedicated hosts) is reached by following each entry's `children_url` to its plans, and real per-region prices live one more hop deeper at `/{plan_id}/pricing/deployment`. The plain `/pricing` endpoint returns $0 for base instance-hours because base price is fully regional. Hetzner, DigitalOcean, and Fly remain on the v1 list: Hetzner needs an auth token, DigitalOcean's API returns an account-filtered slice rather than the published catalog, and Fly publishes no pricing API at all.

The full contract that drives agent behavior is in `AGENTS.md`.

## Modes

The project's `AGENTS.md` has two modes the agent operates in:

1. **Coding agent mode** when you are building or modifying the project. Universal rules and Python conventions apply.
2. **Price / cloud agent mode** when you are asking the agent for a pricing answer. The citation contract applies.

Post-v0 the price / cloud rules split into their own space (likely `price-agent/AGENTS.md` loaded via slash command or workspace switch) so coding sessions are not weighted down by runtime rules they never use.

## Running it

```
uv sync                     # one-time: create .venv and install deps
cp .env.example .env        # one-time: fill in GCP_API_KEY (see below)

just fetch-all              # fetch fresh snapshots for all providers
just fetch aws              # fetch one provider
just fetch-force gcp        # bypass the 24h freshness rule
```

Then open a Claude Code session in this repo and ask a pricing question naturally. The contract in `AGENTS.md` drives the rest.

### One credential to provision

AWS, Azure, Oracle, Vultr, Linode, and IBM all expose their pricing through public endpoints with no auth. Only GCP gates its catalog behind an API key for quota tracking, even though the data is public. Set `GCP_API_KEY` in `.env` (gitignored) per the instructions in `.env.example`. Provisioning is free and takes about two minutes in the GCP console.

## Layout

- `gates/<provider>.py`: per-provider fetchers (aws, gcp, azure, oracle, vultr, linode, ibm in v0)
- `gates/_shared.py`: timestamps, freshness check, `.env` loader, 429-aware HTTP helper
- `store/<provider>/<ISO>/`: timestamped snapshot directories holding raw data files plus `receipt.json`
- `normalize/`: Python module + FastAPI + CLI; reads snapshots, applies taxonomy, returns ranked candidates with citations
- `normalize/taxonomy/families.json`, `regions.json`: cross-provider equivalence, hand-seeded, editable in PRs
- `web/`: Next.js + assistant-ui app where the agent draws the comparison surface
- `eval/v0.jsonl`: hand-written scenarios scored by an LLM judge on citation correctness + staleness/refusal
- `cloud-providers.json`: provider registry
- `AGENTS.md`: agent behavior contract (citation contract, mode switching)
- `SPEC.md`: technical contract (normalization API, taxonomy formats, citation schema, UI surface, eval rubric)
- `PRD.md`: product intent, scope, roadmap

## Status

Gates ship for all 7 providers, including IBM's three-hop walk to per-region compute pricing. The normalization layer is complete: parquet indexes per provider with citation-verified prices, drift detection via fingerprint plus coverage report, and a query layer (`compare()`, `lookup()`) that synthesizes composite results from per-resource rate rows for GCP and Oracle. `python -m normalize compare --vcpu 4 --ram 8 --region eu-central --family general-purpose` returns all 7 providers ranked by monthly cost with full citation blocks and a `data_quality` envelope.

Next per SPEC.md's build sequence: FastAPI wrapper over `compare()`/`lookup()`, the Next.js plus assistant-ui agent UI, and the LLM-judge eval over `eval/v0.jsonl`.
