# ADR 0007: Rate rows and composite citations for resource-priced providers

- **Status:** Accepted
- **Date:** 2026-05-27
- **Supersedes:** N/A
- **Related:** [0003](0003-citation-stable-id-jsonpath.md), [0004](0004-schema-drift-detection.md), [0006](0006-flex-rules-over-shape-catalog.md)

## Context

ADR 0006 commits v0 to fetching GCP and Oracle pricing as-is (per-resource SKUs)
and synthesizing instance results at query time using `flex_rules.json`. That
decision leaves an open question about the parquet schema: how do we represent
a priced SKU when the SKU is `N2 Instance Core in Frankfurt` (per vCPU/hour),
not `n2-standard-4 in Frankfurt` (per instance/hour)?

Two clean options:

- **Composite rows.** Pre-materialize one parquet row per (family, region,
  canonical size). The row's `hourly_usd` is the composite price computed at
  index-build time. The row carries multiple citation `json_path` entries
  (one for the vCPU SKU, one for the RAM SKU).
- **Rate rows.** Keep parquet rows at the atomic SKU level (one per priced
  thing in the upstream catalog). A "rate row" carries the per-unit price and
  declares its unit (per vCPU/hour, per GB-RAM/hour). compare() composes
  instance results at query time from the rate rows plus the flex_rules.

The third option (synthesize at build, store as instance rows with a single
representative json_path) was rejected during ADR 0006 because it loses
verification integrity: the recorded composite price would not equal the value
at any single json_path, so the citation round-trip in `normalize/verifier.py`
(ADR 0003) cannot mechanically confirm the row.

## Decision

Rate rows. Parquet preserves the atomic shape of the upstream catalog;
synthesis happens at query time in `normalize/query.py`. Composite citations
live in the **response shape**, not in the parquet schema.

### Parquet schema additions

The schema in `normalize/schema.py` (`INDEX_SCHEMA`) gains two new columns and
relaxes the types on two existing columns:

```
row_kind:    pl.String                   "instance" | "rate"
rate_unit:   pl.String   (nullable)      "per_vcpu_hour" | "per_gb_ram_hour" | "per_ocpu_hour"
                                          null when row_kind == "instance"
vcpu:        pl.Int32    (nullable)      null when row_kind == "rate"
ram_gb:      pl.Float64  (nullable)      null when row_kind == "rate"
```

The existing `hourly_usd` and `monthly_usd` columns are unchanged:

- For `row_kind == "instance"`, they carry the per-instance price (existing
  semantics; the 5 v0 builders are unaffected).
- For `row_kind == "rate"`, they carry the per-unit price (e.g. cost for 1 vCPU
  for 1 hour, or cost for 1 GB-RAM for 1 hour). The column meaning depends on
  `rate_unit`.

`cited_price_kind` gains a `"rate_hourly"` value alongside the existing
`"hourly"` and `"monthly"`, used by the verifier to confirm rate row
round-trips correctly.

### Response shape (composite citations)

A compare() result that came from synthesis carries:

```json
{
  "provider":      "gcp",
  "instance_type": "n2-custom-4-8192",
  "region_native": "europe-west3",
  "vcpu_actual":   4,
  "ram_gb_actual": 8,
  "monthly_usd":   148.92,
  "hourly_usd":    0.2040,
  "synthesized":   true,
  "citation": {
    "composite": [
      {
        "kind":       "rate",
        "rate_unit":  "per_vcpu_hour",
        "rate":       0.0306,
        "quantity":   4,
        "contribution_usd": 0.1224,
        "source_url": "https://cloudbilling.googleapis.com/...",
        "store_path": "store/gcp/<ISO>/skus.json",
        "json_path":  "$.skus[?(@.skuId=='ABCD-1234-EFGH')].pricingInfo[0].pricingExpression.tieredRates[0].unitPrice",
        "fetched_at": "2026-05-27T03:00:00Z",
        "age_hours":  3.1
      },
      {
        "kind":       "rate",
        "rate_unit":  "per_gb_ram_hour",
        "rate":       0.01020,
        "quantity":   8,
        "contribution_usd": 0.0816,
        "...":        "..."
      }
    ],
    "synthesis": {
      "rule": "flex_rules.gcp.n2",
      "formula": "vcpu * vcpu_rate + ram_gb * ram_rate"
    }
  }
}
```

For atomic (instance-row) results the citation block is unchanged from SPEC.md
v0.1: a single `source_url + store_path + json_path + fetched_at + age_hours`.

The agent's prose treatment per AGENTS.md surfaces composite citations
transparently: "n2-custom-4-8192 is synthesized from two SKUs (vCPU at
$0.0306/hr, RAM at $0.0102/GB-hr)". The user can verify each constituent
independently by opening its `store_path` and walking its `json_path`.

### Verifier semantics

`normalize/verifier.py` continues to verify one row at a time. The
round-trip check is unchanged:

- For `row_kind == "instance"`: the resolved value at `json_path` equals
  `hourly_usd` or `monthly_usd` (selected by `cited_price_kind`).
- For `row_kind == "rate"` with `cited_price_kind == "rate_hourly"`: the
  resolved value at `json_path` equals `hourly_usd` (the per-unit rate).

Composite synthesis verification is a query-layer / eval-layer concern, not an
index-build concern. Eval lane 1 (citation correctness, SPEC.md) extends to
check that `composite[0].contribution_usd + composite[1].contribution_usd`
equals the response's `hourly_usd` within rounding.

### Drift flags

ADR 0004's flag taxonomy gains two flags (already named in ADR 0006):

```
flex_family_unknown                pricing snapshot has rate rows for a family that flex_rules.json does not cover
composite_price_synthesis_failed   query-time synthesis produced an implausible value
```

`flex_family_unknown` fires at index build (in the GCP and Oracle builders).
`composite_price_synthesis_failed` fires at query time (in `normalize/query.py`)
and bubbles up through the `data_quality` envelope of the response.

## Consequences

### Positive

- Parquet stays faithful to the source: every row is a priced thing in the
  upstream catalog. New SKU in upstream -> next index rebuild emits a row. No
  pre-materialization to maintain.
- Citation verification semantics carry over uncomplicated. The round-trip
  check at index build still has a single resolved value to compare against.
- Composite citations live in the response shape where they are the user's
  concern, not in the parquet where they would force an awkward list-typed
  column.
- compare() branches once on `row_kind` and applies the appropriate join. The
  branching is concentrated in one file.
- Adding a third resource-priced provider in v1 (e.g. Azure if we ever migrate
  off the SKU-name parser) reuses the rate-row plumbing.

### Negative

- Parquet schema has two new columns and two relaxed nullabilities. The 5
  existing builders set `row_kind="instance"`, `rate_unit=None`, and continue
  to populate `vcpu`/`ram_gb` non-null. Schema migration is one rebuild per
  provider; no data is lost.
- compare() must understand both row types and the flex_rules layer to
  synthesize. That logic lives in `normalize/query.py` and is the most complex
  branch in the query layer.
- A rate row's `hourly_usd` column means different things from an instance
  row's `hourly_usd`. Readers of the parquet who do not check `row_kind` will
  produce wrong totals (e.g. "average price across all rows" mixes per-unit
  and per-instance). This is the price of keeping the column singular. The
  `row_kind` column documents the discriminator.

### Neutral

- A future option to deprecate rate rows in favor of fetched shape catalogs
  (ADR 0006's v1 path) does not require a schema breakage. The
  `compute.googleapis.com/machineTypes` join would produce instance rows again,
  and rate rows would become a fallback for custom-shape queries.

## Alternatives considered

- **Composite parquet rows with multi-citation columns.** Rejected: forces a
  list-typed column (`composite_json_paths: list[str]`) and a parallel
  list-typed weight/contribution column. Verification semantics fork. The
  parquet becomes lossy w.r.t. the upstream atomic SKUs.
- **Two parquet files per provider.** One `instance_index.parquet` for
  pre-materialized composites, one `rate_index.parquet` for the atomic rates.
  Rejected: doubles the schema surface and the drift-detection apparatus.
- **Hand-curate a static set of composite specs** (e.g. n2-standard-2/4/8/16
  at default ratios) at build time, emit them as instance rows. Rejected:
  fragility ADR 0006 already argued against. The flex_rules file is the right
  source of truth for what's a valid composite shape; the synthesis is a
  query, not a build step.
