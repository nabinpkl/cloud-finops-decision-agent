# SPEC

Technical contract for cloud-finops-decision-agent. Three layers, one repo. PRD.md covers product intent and roadmap; AGENTS.md covers agent behavior rules; this file owns data shapes, API contracts, and the eval rubric.

## Architecture

Three layers, each independently defensible. If the frontend fails, the normalization layer still answers questions over its API and CLI. If the agent's judgment diverges from the normalization layer's ranking, that divergence is what eval surfaces. Per ADR-0009 the agent runtime is server-side, inside the FastAPI process, not in the browser tier.

```
                    ┌──────────────────────────────────┐
                    │  Frontend (web/)                 │
                    │  Next.js + assistant-ui.         │
                    │  Frontend-only: renders the      │
                    │  chat stream and the generative  │
                    │  tool components (ComparisonTable│
                    │  PriceCard). No agent logic,     │
                    │  no model keys.                  │
                    └────────────────┬─────────────────┘
                                     │ HTTP (chat stream)
                    ┌────────────────▼─────────────────┐
                    │  Backend (api/ + normalize/)     │
                    │  FastAPI hosts two concerns:     │
                    │  (1) agent runtime on the Python │
                    │      OpenAI Agents SDK, model via│
                    │      an OpenAI-compatible base   │
                    │      URL; tools call normalize   │
                    │      in-process.                 │
                    │  (2) deterministic query API     │
                    │      (compare/lookup/excerpt) +  │
                    │      the normalize/ module + CLI.│
                    │  Reads store/<provider>/<ISO>/   │
                    │  snapshots and taxonomy JSON,    │
                    │  returns ranked candidates with  │
                    │  citation blocks.                │
                    └────────────────┬─────────────────┘
                                     │ filesystem
                    ┌────────────────▼─────────────────┐
                    │  Gates (gates/)                  │
                    │  Per-provider fetchers writing   │
                    │  timestamped snapshots.          │
                    │  Already in place.               │
                    └──────────────────────────────────┘

                    Eval (eval/) runs scenarios end-to-end through the agent
                    runtime, with an LLM-as-judge scoring two lanes: citation
                    correctness and staleness / refusal behavior.
```

## Normalization layer

Single implementation in Python under `normalize/`, callable three ways: imported as a module, hit over HTTP via FastAPI, or run as `python -m normalize` from the shell. Same input schema, same output schema, same citation block.

### Python module

```python
from normalize import compare, lookup

result = compare(
    vcpu=4,
    ram_gb=8,
    region="eu-central",            # canonical, or provider-native
    family="general-purpose",        # optional; defaults to "any"
    providers=None,                  # optional; default all 7
    expand="cheapest",               # "cheapest" or "full"
)

result = lookup(
    provider="aws",
    instance_type="m5.xlarge",
    region="eu-central-1",
)
```

### FastAPI

```
POST /compare
{
  "vcpu": 4,
  "ram_gb": 8,
  "region": "eu-central",
  "family": "general-purpose",
  "providers": null,
  "expand": "cheapest"
}

GET /lookup?provider=aws&instance_type=m5.xlarge&region=eu-central-1
```

### CLI

```
python -m normalize compare --vcpu 4 --ram 8 --region eu-central --family general-purpose
python -m normalize lookup --provider aws --instance-type m5.xlarge --region eu-central-1
```

### Match policy

Closest-larger across both dimensions: the chosen candidate satisfies `vcpu >= requested_vcpu` AND `ram_gb >= requested_ram_gb`, picking the smallest instance that meets both. The response always reports both the ask and the actual vCPU/RAM delivered, so the agent's prose can flag "you asked 8 GB, all candidates ship 16 GB."

When no candidate satisfies the request (e.g. user asked 256 vCPU but no provider has it in that family), the per-provider entry is null and the response notes `unmet_requirement`.

### Response shape (`compare`)

```json
{
  "request": {
    "vcpu": 4,
    "ram_gb": 8,
    "region": "eu-central",
    "family": "general-purpose",
    "providers": ["aws", "azure", "ibm", "linode", "vultr"]
  },
  "results": [
    {
      "provider": "vultr",
      "instance_type": "vc2-4c-8gb",
      "region_native": "fra",
      "vcpu_actual": 4,
      "ram_gb_actual": 8.0,
      "monthly_usd": 40.00,
      "hourly_usd": 0.055,
      "considered_count": 21,
      "citation": {
        "source_url": "https://api.vultr.com/v2/plans",
        "store_path": "store/vultr/2026-05-25T21-00-46Z/plans.json",
        "json_path": "$.plans[?(@.id=='vc2-4c-8gb')].monthly_cost",
        "fetched_at": "2026-05-25T21:00:46.211738Z",
        "age_hours": 6.2
      }
    },
    {
      "provider": "aws",
      "instance_type": "a1.xlarge",
      "region_native": "eu-central-1",
      "vcpu_actual": 4,
      "ram_gb_actual": 8.0,
      "monthly_usd": 84.97,
      "hourly_usd": 0.1164,
      "considered_count": 208,
      "citation": {
        "source_url": "https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonEC2/current/eu-central-1/index.json",
        "store_path": "store/aws/2026-05-25T04-23-36Z/eu-central-1.json",
        "json_path": "$.terms.OnDemand['4AQFBS47E7Y26RTA'].*.priceDimensions.*.pricePerUnit.USD",
        "fetched_at": "2026-05-25T04:23:36.466029Z",
        "age_hours": 8.4
      }
    }
  ],
  "ranked_by": "monthly_usd",
  "unmet_requirements": [],
  "data_quality": {
    "overall_status": "ok",
    "per_provider": {
      "aws":   {"status": "ok", "snapshot_age_hours": 8.4,  "flags": [], "human_summary": "aws index built clean: 2880 rows.", "report_path": "store/aws/2026-05-25T04-23-36Z/index_report.json", "snapshot_iso": "2026-05-25T04-23-36Z"},
      "vultr": {"status": "ok", "snapshot_age_hours": 6.2,  "flags": [], "human_summary": "vultr index built clean: 271 rows.", "report_path": "store/vultr/2026-05-25T21-00-46Z/index_report.json", "snapshot_iso": "2026-05-25T21-00-46Z"}
    }
  }
}
```

When `expand=full`, each result entry also carries `considered: [...]` — the full list of candidates that matched the family + vCPU + RAM filter, in the order they were ranked, so the agent can quote the full comparison set when answering.

Under the closest-larger match policy, a 4 vCPU 8 GB ask picks AWS `a1.xlarge` (4 vCPU 8 GB at $0.1164/hr) rather than `m5.xlarge` (4 vCPU 16 GB at $0.230/hr) because `a1.xlarge` meets the spec at lower cost. The agent's prose surfaces the actual vCPU/RAM delivered so the user sees what was selected and why.

### Citation block

The citation block on every result entry is the project's only ground-truth contract. AGENTS.md governs how the agent surfaces it in prose. This section defines the shape.

```json
{
  "source_url": "string, the upstream URL the snapshot came from",
  "store_path": "string, relative path to the file inside store/<provider>/<ISO>/",
  "json_path": "string, JSONPath into the file pointing at the price node",
  "fetched_at": "string, ISO 8601 UTC with trailing Z",
  "age_hours": "number, computed at response time"
}
```

`age_hours` is computed as `(now_utc() - parse(fetched_at)).total_seconds() / 3600`. AGENTS.md spells out the UTC-aware parsing pitfall; the normalization layer is the canonical implementation.

### Composite citation (resource-priced providers)

GCP and Oracle price compute per resource (per vCPU or OCPU + per GB RAM) rather than per named bundle. Per ADR 0007 the parquet stores atomic rate rows for these providers; compare() synthesizes composite results at query time. A composite result's citation block has the shape:

```json
{
  "composite": [
    {
      "kind":              "rate",
      "rate_unit":         "per_vcpu_hour",
      "rate":              0.0306,
      "quantity":          4,
      "contribution_usd":  0.1224,
      "source_url":        "https://cloudbilling.googleapis.com/...",
      "store_path":        "store/gcp/<ISO>/skus.json",
      "json_path":         "$.skus[?(@.skuId=='ABCD-1234-EFGH')].pricingInfo[0].pricingExpression.tieredRates[0].unitPrice",
      "fetched_at":        "2026-05-27T03:00:00Z",
      "age_hours":         3.1
    },
    {
      "kind":              "rate",
      "rate_unit":         "per_gb_ram_hour",
      "rate":              0.0102,
      "quantity":          8,
      "contribution_usd":  0.0816,
      "...":               "..."
    }
  ],
  "synthesis": {
    "rule":    "flex_rules.gcp.n2",
    "formula": "vcpu * vcpu_rate + ram_gb * ram_rate"
  }
}
```

The corresponding result entry sets `synthesized: true` so the agent's prose can disclose the composition. Each `composite[]` entry is a fully-verifiable citation: open `store_path`, walk `json_path`, confirm the rate. Sum of `contribution_usd` equals the result's `hourly_usd` within rounding.

### data_quality envelope

Every response carries a `data_quality` block per ADR 0005. The shape:

```json
{
  "overall_status": "ok | warn | stale | broken",
  "per_provider": {
    "aws": {
      "status":             "ok | warn | stale | broken",
      "snapshot_age_hours": 8.4,
      "flags":              [],
      "human_summary":      "aws index built clean: 2880 rows.",
      "report_path":        "store/aws/<ISO>/index_report.json",
      "snapshot_iso":       "<ISO>",
      "evidence":           {"...": "..."}
    }
  }
}
```

`overall_status` is the worst of the per-provider statuses on the ordering `ok < warn < stale < broken`. `flags` carries the drift identifiers from ADR 0004's enum. `human_summary` is pre-composed prose the agent paraphrases. `report_path` points at the on-disk artifact for drill-down. `snapshot_stale` is appended automatically whenever `snapshot_age_hours > 24`.

## Taxonomies

Two JSON files in `normalize/taxonomy/`, editable in PRs, readable by the agent at runtime so it can cite the file when it explains an equivalence.

### `families.json`

```json
{
  "general-purpose": {
    "members": [
      {"provider": "aws", "prefix": ["m5", "m6i", "m7i"], "notes": "Intel/AMD x86, balanced compute/memory"},
      {"provider": "gcp", "prefix": ["n2-standard", "n4-standard"], "notes": "Intel Cascade Lake / Emerald Rapids"},
      {"provider": "azure", "prefix": ["Standard_D", "Standard_Dsv5"], "notes": "Dsv5 = Intel Ice Lake"},
      {"provider": "ibm", "prefix": ["bx2", "bx3d"], "notes": "bx3d adds local NVMe"},
      {"provider": "oracle", "prefix": ["VM.Standard.E5", "VM.Standard.E4"], "notes": "AMD EPYC, Flex shapes price OCPU and RAM separately"},
      {"provider": "vultr", "prefix": ["vc2"], "notes": "Cloud Compute, shared CPU"},
      {"provider": "linode", "prefix": ["g6-standard"], "notes": "Shared CPU"}
    ],
    "dimensions_matched": ["vcpu", "ram_gb"],
    "dimensions_not_normalized": ["cpu_generation", "network_bandwidth", "included_storage", "noisy_neighbor_class"]
  },
  "compute-optimized": { "...": "..." },
  "memory-optimized": { "...": "..." },
  "gpu": { "...": "..." },
  "bare-metal": { "...": "..." }
}
```

`dimensions_not_normalized` is the auditable record of what the equivalence claim does NOT hold on. The agent must surface this in prose when it makes a cross-provider comparison.

### `regions.json`

```json
{
  "us-east": {
    "label": "US East",
    "providers": {
      "aws": "us-east-1",
      "gcp": "us-east4",
      "azure": "eastus",
      "ibm": "us-east",
      "oracle": "us-ashburn-1",
      "vultr": "ewr",
      "linode": "us-east"
    }
  },
  "eu-central": { "...": "..." },
  "ap-southeast": { "...": "..." }
}
```

The normalization layer accepts either form on input. Output always carries the provider-native code in `region_native` so the agent can quote it back exactly.

## Agent runtime and UI surface

Per ADR-0009 the agent loop runs server-side in FastAPI on the Python OpenAI Agents SDK (`openai-agents`). The model is built against an OpenAI-compatible base URL (`OpenAIChatCompletionsModel` over `AsyncOpenAI(base_url, api_key)`, Chat Completions not Responses), so the provider is a `.env` knob (`PROVIDER_BASE_URL`, `PROVIDER_API_KEY`, `MODEL_NAME`), not a hardcoded vendor. The agent's tools call `normalize.compare` / `normalize.lookup` in-process; the wire translation (drop `store_path`, add a `snapshot` ref) is the same one `api/main.py` applies. FastAPI exposes a streaming chat endpoint.

`web/` is a Next.js app using `assistant-ui` as the chat shell, and it is frontend-only: it consumes the chat stream from FastAPI and renders the custom tool components. It holds no agent logic and no model keys. The agent decides which custom tool component to render based on the query shape; the frontend maps tool names to components.

### Custom tool components

v0 ships two:

- **`ComparisonTable`** — ranked multi-provider table. Each row contains provider, instance type, region (canonical + native), monthly/hourly USD, and an inline `Source` primitive (from assistant-ui) carrying the citation block. Used when the agent calls `compare`.
- **`PriceCard`** — single instance with full citation visible. Used when the agent calls `lookup`.

Staleness is surfaced inline in the agent's prose (`(snapshot 6h old)` per AGENTS.md), not as a separate component. Equivalence dimensions are surfaced in prose, not as a component. Both can become components post-v0 if the prose treatment isn't enough.

### Streaming and tool calls

assistant-ui's `Tool` primitive wraps each custom component. The OpenAI Agents SDK runs the tool-calling loop server-side; `Runner.run(..., stream=True)` produces the event stream. FastAPI emits that stream in a shape assistant-ui consumes, and the response JSON for each tool call is passed to the matching component for rendering.

The stream bridge is the one piece without a first-party helper: the JS Agents SDK ships `@openai/agents-extensions/ai-sdk-ui` to emit an assistant-ui-compatible UIMessage stream, but the Python SDK does not. FastAPI must produce a compatible stream (the AI SDK data-stream/SSE protocol, or an assistant-ui external-runtime shape) by hand, and that must round-trip a real tool call before any component renders (ADR-0009 negative, TASKS R8). No bespoke RSC streaming for v0.

## Eval

`eval/v0.jsonl` carries 20–30 hand-written scenarios. The `python -m eval` runner replays each scenario through the deployed agent UI (or the agent runtime directly), captures the full transcript including tool calls and rendered components, and asks an LLM judge to score it on two lanes:

### Lane 1: citation correctness

For every price the agent quotes, the judge confirms:
1. The cited `store_path` exists on disk.
2. The cited `json_path` resolves to a price node in that file.
3. The price reported in prose matches the price at that JSON path (within rounding).
4. `age_hours` agrees with `(now - fetched_at)`.

A single failed citation fails the lane for that scenario.

### Lane 2: staleness / refusal behavior

The judge confirms:
1. When any cited snapshot has `age_hours > 24`, the agent marks the answer stale and offers refetch.
2. When the user asks about a price the snapshot does not contain (e.g. Power Systems on IBM, GPU on Vultr), the agent refuses rather than fabricating.
3. When asked about a region not in the v0 set, the agent says so explicitly rather than guessing.

### Scenario shape

```jsonl
{"id": "eu-cheapest-4x8", "question": "Cheapest 4 vCPU 8 GB general-purpose VM in EU?", "expected": {"must_cite_providers": ["aws", "gcp", "azure"], "staleness_expected": false, "refusal_expected": false}}
{"id": "stale-snapshot", "question": "What does m5.xlarge cost in eu-central-1?", "setup": "force_snapshot_age=30h", "expected": {"staleness_expected": true}}
{"id": "absent-ibm-power", "question": "What's the cheapest IBM Power Systems shape?", "expected": {"refusal_expected": true, "refusal_reason_contains": "Power Systems"}}
```

### Running

```
python -m eval --judge claude-opus-4-7 --scenarios eval/v0.jsonl
```

Pass/fail per scenario per lane, plus a roll-up score. Reproducible across runs because scenarios are static; judge non-determinism is the only variance source and is tracked across runs in `eval/runs/`.

## Build sequence

1. `normalize/taxonomy/families.json` and `regions.json` — the load-bearing data shapes.
2. `normalize/` Python module + CLI — operates against snapshots already on disk.
3. FastAPI query wrapper (`compare`/`lookup`/`excerpt`/`health`) — thin layer over the module.
4. Agent runtime in FastAPI on the OpenAI Agents SDK: the `compare` tool over the in-process module, model on an OpenAI-compatible base URL, a streaming chat endpoint.
5. `web/` Next.js + assistant-ui frontend with the two custom tool components, consuming the chat stream.
6. `eval/v0.jsonl` + runner.

Each step is independently runnable. Steps 2 and 3 ship a usable comparator before the agent or UI exists; the frontend is the last product layer, not the first.
