# cloud-finops-decision-agent

Citation-backed cloud pricing for agent-driven infrastructure decisions.

Ask:

```text
cheapest 4 vCPU 8 GB box across the big 3 in EU
```

The agent answers from a deterministic normalization layer and renders a
comparison table. Every price links back to:

- the provider snapshot,
- the JSON path inside that snapshot,
- the upstream source URL,
- the snapshot age.

The goal is simple: every pricing claim should be checkable.

> [!WARNING]
> ## ⚠️ Work in progress, experimental, not for production
>
> This is an **actively-developed learning project**. It is **experimental**,
> the design and APIs change without notice, and there is **no released,
> production-ready software yet**. The current state is an iteration in
> progress: working v0 components, design docs, architecture decisions, and an
> accepted v0 plan. **Do not use this for production FinOps governance,
> purchasing decisions, compliance, or security.** It provides no guarantees.
> The agent hardening here is defense in depth around a narrow read-only
> pricing tool, not a security boundary for general autonomous agents. See
> `SECURITY.md` and ADR-0014 for the current threat register and residual risks.

## Why this exists

Cloud pricing comparison is mostly solved for humans. Existing tools and sites
help people read, compare, or monitor prices:

- comparison sites such as CloudPrice, getdeploying, and VPSBenchmarks,
- FinOps tools such as OpenCost, Kubecost, Vantage, CloudZero, and Infracost.

This repo explores a different space:

- pre-purchase deployment decisions,
- agent-callable pricing data,
- open, source-backed answers,
- pricing claims that can be verified by re-fetching provider catalogs.

Cloud pricing is the testbed because provider APIs are the source of truth.
Full product framing lives in `PRD.md`.

There is a larger experiment here too.

The ingest modules are prefilters. They decide:

- what to fetch,
- which fields to keep,
- which regions to normalize,
- which provider-specific shapes count as comparable.

A model that only sees the normalized view inherits those choices silently. The
open question is whether an agent with raw snapshots, citation tools, and
exploration freedom can notice something the prefilter missed.

We do not have that answer yet. The repo is building the precondition for
trying: visible traces, auditable citations, and claims that can be checked
instead of trusted. Evidence belongs in `FINDINGS.md`.

## How it works

Three layers live in one repo. `SPEC.md` owns their contracts.

### Ingest

`backend/src/ingest/` fetches provider pricing catalogs.

Each provider module:

- fetches a compute pricing catalog,
- writes a timestamped snapshot under `store/<provider>/<ISO>/`,
- prints a JSON receipt to stdout.

Shared HTTP behavior, including polite retry handling, lives in
`backend/src/ingest/_shared.py`.

### Normalize

`backend/src/normalize/` is the deterministic pricing layer.

It provides:

- a Python module,
- a FastAPI wrapper,
- a `python -m normalize` CLI.

It reads local snapshots, applies JSON taxonomies for family and region, and
answers:

- `compare(vcpu, ram_gb, region, family)`,
- `lookup(provider, instance_type, region)`.

The match policy is closest-larger: candidate vCPU and RAM must be greater than
or equal to the requested shape. Results are ranked cheapest-first and carry
full citations.

### Agent Runtime

The agent runs server-side in FastAPI behind a framework-neutral runtime port
(ADR-0012).

Current adapters:

- `langchain`, the default runtime,
- OpenAI Agents SDK, optional.

Both adapters call the same in-process pricing tool. The model provider is an
OpenAI-compatible `.env` setting, not a hardcoded vendor.

Safety-critical behavior is deterministic:

- user and assistant text are XML-escaped into untrusted trust-zone tags,
- the model emits `AnswerPlan` JSON,
- backend policy validates every price/citation claim,
- final prose is rendered from verified structured claims.

### Frontend

`frontend/` is a Next.js + `assistant-ui` app.

It renders the agent stream and holds no agent logic or model keys. The shipped
custom tool component is `ComparisonTable`; a single-instance `PriceCard` is
captured for v1.

### Citation Contract

The citation contract is the enforcement layer.

Every quoted price carries:

- `source_url`,
- `store_path` internally, translated to public snapshot refs at API boundaries,
- `json_path`,
- `fetched_at`,
- `age_hours`.

If a snapshot is over 24 hours old, the agent marks the answer stale and offers
a refetch.

`AGENTS.md` owns agent behavior. `SPEC.md` owns data shapes.

### Providers

Seven providers are in v0:

- AWS,
- GCP,
- Azure,
- Oracle,
- Vultr,
- Linode,
- IBM.

Why these providers:

- AWS, GCP, and Azure stress the normalization layer with complex schemas.
- Oracle adds global list pricing and OCPU+memory split pricing.
- Vultr and Linode add smaller independent-provider catalogs.
- Linode adds explicit per-region override pricing.
- IBM adds a hierarchical three-hop catalog walk to reach real regional prices.

Deferred providers:

- Hetzner: needs an auth token.
- DigitalOcean: API returns an account-filtered slice, not the public catalog.
- Fly: no published pricing API.

The full contract that drives agent behavior is in `AGENTS.md`.

## Modes

The project's `AGENTS.md` has two modes the agent operates in:

1. **Coding agent mode** when you are building or modifying the project. Universal rules and Python conventions apply.
2. **Price / cloud agent mode** when you are asking the agent for a pricing answer. The citation contract applies.

Post-v0, the price / cloud rules should move into their own space so coding
sessions are not weighted down by runtime pricing rules.

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

AWS, Azure, Oracle, Vultr, Linode, and IBM expose the v0 pricing data through
public endpoints with no auth.

Only GCP requires an API key for quota tracking, even though the catalog data is
public. Set `GCP_API_KEY` in `.env` per `.env.example`.

## Layout

- `backend/src/ingest/`: per-provider fetchers.
- `backend/src/ingest/_shared.py`: timestamps, freshness checks, env loading, and retry-aware HTTP.
- `store/<provider>/<ISO>/`: timestamped raw snapshot directories plus `receipt.json`.
- `backend/src/normalize/`: query layer, FastAPI wrapper, CLI, and citation-backed ranking.
- `backend/src/normalize/taxonomy/`: editable JSON taxonomies for family and region equivalence.
- `backend/src/api/`: FastAPI app, middleware, deterministic routes, and assistant transport.
- `backend/src/agent/`: runtime port, LangChain/OpenAI adapters, prompts, policies, and tool bodies.
- `frontend/`: frontend-only Next.js + `assistant-ui` client.
- `prompts/`: prompt source files and rendered runtime prompt.
- `EVALS.md`: eval plan and current eval coverage.
- `cloud-providers.json`: provider registry.
- `AGENTS.md`: agent behavior contract.
- `SPEC.md`: technical contract.
- `PRD.md`: product intent, scope, and roadmap.

## Status

Built:

- ingest modules for all 7 v0 providers,
- IBM's three-hop walk to per-region compute pricing,
- parquet indexes per provider,
- citation-verified prices,
- schema drift detection by fingerprint and coverage report,
- `compare()` and `lookup()` query APIs,
- composite price synthesis for GCP and Oracle resource-rate rows,
- FastAPI endpoints for `/compare`, `/lookup`, `/citation/excerpt`, and `/health`,
- server-side `/assistant`,
- LangChain and OpenAI Agents SDK runtime adapters,
- budget controls for the model surface,
- frontend rendering for `ComparisonTable`,
- offline evals wired into `just check`.

Example:

```bash
just compare 4 8 eu-central general-purpose
```

That returns all 7 providers ranked by monthly cost, with citation blocks and a
`data_quality` envelope.

Remaining v0 work:

- citation-depth UI,
- prose tuning,
- browser smoke verification.

## Security and Local Data

This repo is experimental and pre-release. Security fixes land on the default
branch until tagged releases exist. See `SECURITY.md` for reporting guidance.

Local runtime artifacts are intentionally excluded from source control.

Keep these private:

- `.env`,
- Infisical state,
- `store/` snapshots,
- `var/` traces,
- SQLite budget databases.

Snapshots can be re-fetched from provider APIs. Traces and budget state may
contain sensitive operational data.

The public assistant is unauthenticated and read-only.

Current hardening includes:

- request, body, history, and turn limits,
- per-route rate limits,
- XML trust-zone wrapping for external text,
- strict compare-tool schemas,
- provider allowlists,
- private trace files,
- deterministic final-answer validation.

The prompt is not treated as a secret security boundary.

## License

Apache-2.0; see `LICENSE`.
