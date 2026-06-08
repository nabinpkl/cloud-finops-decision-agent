# ADR 0006: Flex-rules JSON over a fetched shape catalog for GCP and Oracle (v0)

- **Status:** Accepted
- **Date:** 2026-05-27
- **Supersedes:** N/A

## Context

Five of the seven v0 providers (AWS, Azure, Linode, Vultr, IBM) publish pricing
per named instance type with vCPU and RAM as attributes on the priced rows
themselves. One fetch covers both pricing and specs. The per-provider builders
described in ADR 0002 handle each of these in a uniform way: read the snapshot,
emit one parquet row per priced shape.

GCP and Oracle break this pattern. Both price compute per resource (per vCPU
or OCPU plus per GB RAM) rather than per named bundle:

- GCP's Cloud Billing Catalog SKUs are "N2 Instance Core in Frankfurt" (per vCPU
  rate) and "N2 Instance Ram in Frankfurt" (per GB rate). There is no row
  anywhere in the pricing API that says `n2-standard-4 = $X`.
- Oracle's modern Flex shapes (E3+, A1+, X9+, X12) similarly price OCPU and
  memory as separate part numbers. The Price List API has no shape catalog.

So to answer "cheapest 4 vCPU 8 GB general-purpose VM in EU" for these two
providers, we need three things:

1. The per-resource rates (already in the pricing snapshot).
2. The mapping from a (vCPU, RAM) ask to a legal configuration (vCPU
   constraints, OCPU-to-vCPU conversion, valid RAM-per-vCPU range).
3. A name for the resulting configuration so the agent can quote it back.

The authoritative source for (2) and (3) is each provider's compute API
(GCP `compute.googleapis.com/.../machineTypes`, Oracle Compute Shapes API).
Both are auth-gated more heavily than what the v0 gate stack supports today:

- GCP machineTypes requires OAuth or a scoped API key. The current GCP_API_KEY
  is unscoped and works only for the billing catalog.
- Oracle Shapes requires OCI signed requests (tenancy OCID, user OCID, key
  fingerprint, private key). No other gate in v0 uses OCI auth.

We have three real options. (A) Fetch the shape catalog as a second gate per
provider, accepting the auth lift. (B) Maintain a small hand-curated
`flex_rules.json` capturing the per-family pricing rule (rate-unit conversion,
constraints, name template), and synthesize composite results at query time.
(C) Skip composite pricing for GCP and Oracle entirely in v0; they appear in
the comparison surface only when a named shape happens to exist (Oracle's older
X5 / X7 / B1 fixed shapes), otherwise the agent refuses politely.

## Decision

For v0: option (B). Land a small `normalize/taxonomy/flex_rules.json` and
synthesize composite pricing at query time in `normalize/compare.py`. v1 picks
up option (A) once we are willing to absorb the auth cost.

The rules file lives next to `families.json` and `regions.json`. Schema:

```json
{
  "gcp": {
    "n2": {
      "vcpu_per_unit": 1,
      "vcpu_constraint": "1 or even",
      "ram_per_vcpu_gb": {"min": 0.5, "max": 8.0},
      "custom_name_template": "n2-custom-{vcpu}-{ram_mb}",
      "notes": "Intel Cascade Lake. Custom Machine Types supported."
    },
    "c3": { ... }
  },
  "oracle": {
    "E5": {
      "vcpu_per_unit": 2,
      "vcpu_constraint": "integer 2..188 (1..94 OCPU, 2 vCPU per OCPU)",
      "ram_per_unit_gb": {"min": 1.0, "max": 64.0},
      "custom_name_template": "VM.Standard.E5.Flex",
      "notes": "AMD EPYC Genoa. Flex shape with caller-chosen OCPU and memory."
    },
    "A2": { ... }
  }
}
```

The GCP and Oracle builders emit one row per (family, region) carrying the
per-unit rates as parquet columns (`vcpu_rate_hourly_usd`, `ram_rate_hourly_usd`)
instead of a single `hourly_usd` for a specific shape. `normalize/compare.py`
detects flex-rate rows at query time, applies the rule, synthesizes a composite
result with the custom-name template, and emits a composite citation block
(two `json_path` entries per result, one for the vCPU SKU and one for the RAM
SKU, per ADR 0003).

The parquet schema gains two nullable columns (`vcpu_rate_hourly_usd`,
`ram_rate_hourly_usd`) and one nullable column carrying the family slug for
flex matching. For non-flex providers (the easy five) these stay null; for
GCP and Oracle, `hourly_usd` and `monthly_usd` are null instead.

Drift detection for the rules file adds two flags to ADR 0004's taxonomy:

- `flex_family_unknown`: pricing snapshot has CPU/RAM rates for a family slug
  that `flex_rules.json` does not cover. Same shape as `new_unclassified_shapes`.
- `composite_price_synthesis_failed`: the rule applied but yielded an
  implausible value (negative, NaN, or off by >2x vs the previous snapshot for
  the same (family, region, vcpu, ram) input). Bundles into ADR 0004's
  `price_shift_detected` mechanism.

v0 scope: composite pricing for GCP custom machine types and Oracle Flex
shapes. v0 explicitly does NOT answer "lookup `n2-standard-4` by name." The
agent's prose surfaces this gap when a user asks by name: "v0 does not enumerate
named GCP shapes; ask by spec instead."

v1 escape: add `ingest/gcp_shapes.py` and `ingest/oracle_shapes.py` that fetch
the machineTypes / Shapes APIs with proper auth, write
`store/gcp_shapes/<ISO>/` and `store/oracle_shapes/<ISO>/`, and the builders
join shapes x rates to emit one parquet row per named shape. `flex_rules.json`
stays useful in v1 because it still encodes per-family validity constraints
needed to check whether a user's (vCPU, RAM) ask is even a legal configuration.

## Consequences

### Positive

- v0 ships with all 7 providers contributing real pricing rows to the compare
  surface. No provider is dimmer than the others for the project's primary
  question ("cheapest N vCPU M GB across the big 3 in EU").
- The rules file is small (~75 lines) and stays in the same hybrid pattern as
  `families.json` and `regions.json` (identifiers from data, judgment from
  provider docs). Same drift discipline applies via two new flags.
- Recovery cost when a new GCP or Oracle family ships is identical to the
  existing recovery cost for a new family prefix: one JSON edit, rebuild, done.
- Citations stay honest. A composite result has two real `json_path` entries
  pointing at the actual vCPU and RAM SKUs in the pricing snapshot. The user can
  verify each component independently.
- v1 upgrade path is clean. Adding fetched shape ingest is additive; the
  rules file does not disappear.

### Negative

- v0 cannot answer "what is `n2-standard-4`?" by name. The agent has to either
  refuse with a clear message or restate the user's question in spec form
  ("you mean 4 vCPU 16 GB on n2? cheapest is..."). This is a real UX gap.
- The rules file is hand-curated content. It carries the same risk class as
  `families.json`, mitigated by the same drift detector pattern but never
  fully eliminated.
- The parquet schema gains two nullable rate columns plus a slug column.
  Builders for the easy five fill them with null; flex-rate builders fill
  `hourly_usd` / `monthly_usd` with null. The query layer has to branch on
  which set of columns to read.

### Neutral

- Provider-side drift in flex rules (e.g. GCP changes RAM-per-vCPU limits on
  n4) does NOT auto-update. A human has to notice and edit the file. The
  `composite_price_synthesis_failed` flag is the primary detector for this:
  if upstream changes the legal ranges, our synthesis starts producing prices
  that diverge from what the user can actually provision, which surfaces as
  a price shift vs the previous snapshot for the same input.

## Alternatives considered

- **(A) Fetch shape catalogs as second ingest per provider.** Structurally pure,
  symmetric with the rest of the project. Rejected for v0 because the auth lift
  (GCP OAuth/scoped key, Oracle full OCI signing) is materially larger than
  anything else in the v0 fetch stack. Holds for v1 as the natural upgrade.
- **(C) Skip composite pricing for GCP and Oracle entirely.** Cleaner scope but
  means the project's primary "cheapest across the big 3" question gets a
  weakened answer (no GCP row, no Oracle row for flex requests). v0 thesis is
  cross-provider comparison; that thesis is undermined if two of the seven are
  silently missing from comparable queries.
- **Full hand-curated shape catalog as JSON in repo** (e.g. ~150 GCP shape
  definitions). Rejected as compounding brittleness without proportional value
  vs the rules-file approach: it carries the same upstream-shape-drift risk
  while adding ~10x the file surface area.
