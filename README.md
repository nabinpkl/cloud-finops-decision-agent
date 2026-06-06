# cloud-finops-decision-agent

Citation-backed cloud pricing where the agent draws the UI from a deterministic normalization layer. Ask it "cheapest 4 vCPU 8 GB box across the big 3 in EU" and the agent renders a comparison table whose every row links to the snapshot file the number came from, the JSON path inside that snapshot, and the upstream URL. You verify by clicking through. No trust required.

## Why this exists

Cloud pricing comparison is a solved problem for humans (CloudPrice, getdeploying, VPSBenchmarks). It is not a solved problem for agents. Existing comparison sites are static, ad-supported, and read with eyes. Existing FinOps tooling (OpenCost, Kubecost, Vantage, CloudZero, Infracost) targets the post-deployment spend question, not the greenfield "where should I deploy" decision. The empty quadrant is pre-purchase, agent-callable, and open. That is the space this repo lives in.

The deeper question underneath: where do agents actually help that deterministic ingestion does not. Cloud pricing is the testbed because the provider's API is the truth, so any claim the agent makes can be checked by re-fetch. Full framing in `PRD.md`.

A second, larger experiment rides on top, and we do not have answers yet. The deterministic ingest modules are themselves prefilters. Someone chose what to fetch, what fields to keep, what regions to include, what to normalize. A model handed only the post-ingest view inherits those decisions silently and treats them as neutral ground truth. The open question is whether a model given the raw snapshots, the citation tools, and the freedom to explore can surface insights the prefilter would have missed, and whether it can notice when reality has shifted under a stable workflow. This is closer to post-training or agent-training in spirit than to a single-query benchmark. Self-improvement and surprise-noticing are the most valuable agent behaviors precisely because they cannot be hand-coded; when the world changes but the workflow stays the same, the only thing that matters is whether the model catches it. We do not have a way to measure either yet. What we have is the precondition for trying: every trace visible, every citation auditable, so any noticing the model claims can be checked rather than fabricated. The findings log (`FINDINGS.md`) is where evidence for or against this accumulates.

## How it works

Three layers in one repo. SPEC.md owns the contracts between them.

**Ingest** (`backend/src/ingest/`) is the deterministic provider-fetch layer. Each module fetches a provider's compute pricing catalog and saves a timestamped snapshot under `store/<provider>/<ISO>/`. The shared polite, 429-aware HTTP layer lives in `backend/src/ingest/_shared.py`. Ingest modules print a JSON receipt to stdout.

**Normalization layer** (`backend/src/normalize/`) is the deterministic baseline. A Python module + FastAPI wrapper + `python -m normalize` CLI. Reads the snapshots, applies a family + region taxonomy stored as JSON, and answers `compare(vcpu, ram_gb, region, family)` and `lookup(provider, instance_type, region)`. Match policy is closest-larger (≥vCPU and ≥RAM). Output is cheapest-per-provider ranked, each result carrying a full citation block.

**Agent runtime** runs server-side in FastAPI behind a framework-neutral port (ADR-0012). The default adapter is the LangChain-backed `langchain` selector; the OpenAI Agents SDK remains an optional runtime. Both call the normalization layer in-process through the same strict tool body, on a model wired to an OpenAI-compatible base URL (the provider is a `.env` knob, not a fixed vendor). User and assistant text are XML-escaped into untrusted trust-zone tags before reaching the model; final prose is buffered until deterministic citation/leakage checks pass. **Frontend** (`frontend/`) is a frontend-only Next.js app using `assistant-ui` as the chat shell; it renders the agent's stream and holds no agent logic or model keys. The shipped custom component is `ComparisonTable`; single-instance `PriceCard` stays captured for v1.

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
uv sync --project backend   # one-time: create backend/.venv and install deps
cp .env.example .env        # one-time: fill in GCP_API_KEY (see below)

just fetch-all              # fetch fresh snapshots for all providers
just fetch aws              # fetch one provider
just fetch-force gcp        # bypass the 24h freshness rule
```

Then open a Claude Code session in this repo and ask a pricing question naturally. The contract in `AGENTS.md` drives the rest.

### One credential to provision

AWS, Azure, Oracle, Vultr, Linode, and IBM all expose their pricing through public endpoints with no auth. Only GCP requires an API key for quota tracking, even though the catalog data is public. Set `GCP_API_KEY` in `.env` (gitignored) per the instructions in `.env.example`. Provisioning is free and takes about two minutes in the GCP console.

## Layout

- `backend/src/ingest/`: per-provider fetchers (aws, gcp, azure, oracle, vultr, linode, ibm in v0; IBM is a package because its catalog walk has multiple steps)
- `backend/src/ingest/_shared.py`: timestamps, freshness check, `.env` loader, 429-aware HTTP helper
- `store/<provider>/<ISO>/`: timestamped snapshot directories holding raw data files plus `receipt.json`
- `backend/src/normalize/`: Python module + FastAPI + CLI; reads snapshots, applies taxonomy, returns ranked candidates with citations
- `backend/src/normalize/taxonomy/families.json`, `regions.json`: cross-provider equivalence, hand-seeded, editable in PRs
- `backend/src/api/`: FastAPI. `main.py` is the ASGI entry point, `app.py` assembles middleware and routers, `routes/` holds deterministic query endpoints (`compare`/`lookup`/`excerpt`/`health`), and `assistant_transport/` holds the streaming chat endpoint.
- `backend/src/agent/`: server-side agent runtime port, framework adapters (`langchain` default, OpenAI Agents SDK optional), prompt loading, and framework-neutral tool bodies.
- `frontend/`: frontend-only Next.js + assistant-ui app that renders the agent's stream
- `prompts/`: production prompts shared by all agent runtime adapters
- `EVALS.md`: planned offline and live eval suite for prompt/tool/citation behavior
- `cloud-providers.json`: provider registry
- `AGENTS.md`: agent behavior contract (citation contract, mode switching)
- `SPEC.md`: technical contract (normalization API, taxonomy formats, citation schema, UI surface, eval rubric)
- `PRD.md`: product intent, scope, roadmap

## Status

Ingest modules ship for all 7 providers, including IBM's three-hop walk to per-region compute pricing. The normalization layer is complete: parquet indexes per provider with citation-verified prices, drift detection via fingerprint plus coverage report, and a query layer (`compare()`, `lookup()`) that synthesizes composite results from per-resource rate rows for GCP and Oracle. `just compare 4 8 eu-central general-purpose` returns all 7 providers ranked by monthly cost with full citation blocks and a `data_quality` envelope.

The FastAPI query wrapper over `compare()`/`lookup()` (plus `/citation/excerpt` and `/health`) is built and tested. The server-side `/assistant` endpoint is implemented, the runtime port supports both `langchain` and OpenAI Agents SDK adapters, budget controls protect the model surface, and the frontend renders a `ComparisonTable` tool result. Remaining v0 work is citation depth, prose tuning, browser smoke verification, and the eval suite planned in `EVALS.md`.

## Security and Local Data

This repo is experimental and pre-release. Security fixes land on the default branch until tagged releases exist; see `SECURITY.md` for reporting guidance.

Local runtime artifacts are intentionally excluded from source control. Keep `.env`, Infisical state, `store/` snapshots, `var/` traces, and SQLite budget databases private. Snapshots can be re-fetched from provider APIs, while traces and budget state may contain sensitive operational data.

The public assistant is unauthenticated and read-only. Hardening is layered: request/body/history limits, per-route rate limits, XML trust-zone wrapping for external text, strict compare-tool schemas, provider allowlists, private trace files, and deterministic final-answer checks. The prompt is not treated as a secret security boundary.

## License

Apache-2.0; see `LICENSE`.
