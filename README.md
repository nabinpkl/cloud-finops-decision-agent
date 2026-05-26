# cloud-finops-decision-agent

A citation-backed cloud pricing tool where an agent does the work and every claim is verifiable. Ask it "cheapest 4 vCPU 8 GB box across the big 3 in EU" and the answer comes with the source URL, the snapshot file the number came from, and the path inside that snapshot. You verify by clicking the URL or opening the file. No trust required.

## Why this exists

Cloud pricing comparison is a solved problem for humans (CloudPrice, getdeploying, VPSBenchmarks). It is not a solved problem for agents. Existing comparison sites are static, ad-supported, and read with eyes. Existing FinOps tooling (OpenCost, Kubecost, Vantage, CloudZero, Infracost) targets the post-deployment spend question, not the greenfield "where should I deploy" decision. The empty quadrant is pre-purchase, agent-callable, and open. That is the space this repo lives in.

The deeper question underneath: where do agents actually help that deterministic gates do not. Cloud pricing is the testbed because the provider's API is the truth, so any claim the agent makes can be checked by re-fetch. Full framing in `DRAFT-PRD.md`.

A second, larger experiment rides on top, and we do not have answers yet. The deterministic gates are themselves prefilters. Someone chose what to fetch, what fields to keep, what regions to include, what to normalize. A model handed only the post-gate view inherits those decisions silently and treats them as neutral ground truth. The open question is whether a model given the raw snapshots, the citation tools, and the freedom to explore can surface insights the prefilter would have missed, and whether it can notice when reality has shifted under a stable workflow. This is closer to post-training or agent-training in spirit than to a single-query benchmark. Self-improvement and surprise-noticing are the most valuable agent behaviors precisely because they cannot be hand-coded; when the world changes but the workflow stays the same, the only thing that matters is whether the model catches it. We do not have a way to measure either yet. What we have is the precondition for trying: every trace visible, every citation auditable, so any noticing the model claims can be checked rather than fabricated. The findings log (`FINDINGS.md`) is where evidence for or against this accumulates.

## How it works

Two layers.

Gates are deterministic Python scripts that fetch a provider's full compute pricing catalog (across all regions) and save a timestamped snapshot under `store/<provider>/<ISO>.json`. One gate per provider. They print a JSON receipt to stdout so the calling agent has the citation fields it needs.

The agent (a Claude Code session in this repo) does the interpretive work: walking the snapshots, mapping a user spec to provider instance types, judging equivalence across heterogeneous schemas, ranking across providers, and returning answers with citations.

The citation contract is the project's only enforcement layer in v0. Every price the agent quotes carries a `source_url`, `store_path`, `json_path`, `fetched_at` timestamp, and `age_hours`. The age is surfaced inline in prose so the reader never has to dig into a JSON block to learn the data is 6 hours old. If the cited snapshot is over 24 hours old, the agent marks the answer stale and prompts to re-fetch.

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

- `gates/<provider>.py`: per-provider fetchers (one each for aws, gcp, azure, oracle, vultr, linode, ibm in v0)
- `store/<provider>/<ISO>/`: timestamped snapshot directories holding the raw data files plus a `receipt.json`
- `cloud-providers.json`: provider registry
- `AGENTS.md`: agent contract for both modes
- `DRAFT-PRD.md`: product intent, scope, v1 roadmap

## Status

Early. The PRD is a draft. The gates do not exist yet. v0 is being scoped through hands-on discovery against the actual provider APIs.
