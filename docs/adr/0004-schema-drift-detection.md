# ADR 0004: Schema drift detection via fingerprint plus coverage report

- **Status:** Accepted
- **Date:** 2026-05-26
- **Supersedes:** N/A

## Context

Pricing scrapers rot silently. The failure modes we have to design against:

1. Field disappears (e.g. AWS drops `instanceFamilyCategory`).
2. Field renamed (e.g. GCP `serviceRegions` becomes `regions`).
3. Type change (e.g. Vultr `monthly_cost` becomes a string).
4. New entity class (e.g. Oracle ships an `X13` shape that no taxonomy prefix matches).
5. Semantics change (e.g. Azure `unitPrice` was hourly, now monthly). The numbers look right, the money is wrong.
6. Path moves (e.g. IBM restructures the children_url payload).

#5 is the dangerous one: silent. The others crash somewhere in the pipeline. We need detection that catches the silent class without flooding the operator with false positives on benign changes.

## Decision

Two layers of detection, both inside the indexer (`normalize/index.py`), produced on every index build:

### Layer 1: schema fingerprint

Walk the raw file. Emit a fingerprint = sorted set of `(json_path_prefix, leaf_type)` tuples up to depth 4 or 5, sampled across rows. Store as `store/<provider>/<ISO>/schema_fingerprint.json`. Diff against the previous snapshot's fingerprint. Any change is recorded in the receipt as:

```json
"schema_drift": {
  "added":        [...],
  "removed":      [...],
  "type_changed": [...]
}
```

For nested providers (IBM especially) the fingerprint is layered: one per nesting level (`services.json` shape, `compute.<service>.plans[]` shape, `compute.<service>.pricing.<plan>.resources[].metrics[].amounts[]` shape). Drift at any layer cascades visibly.

### Layer 2: coverage report

The indexer writes `index_report.json` alongside the parquet with these fields:

```
rows_written:                    <int>
rows_by_provider:                {provider: count}
rows_by_family:                  {family: count}
unclassified_rows:               <int>           # rows that priced VMs but matched no family
unclassified_samples:            [{provider, instance_type, ...}]
families_with_zero_coverage:     [family]        # family where provider should have prefixes but yielded 0
row_count_delta_pct:             <float>         # vs previous snapshot, per provider
median_price_delta_pct:          {family: <float>}  # median monthly_usd shift vs previous snapshot
citation_verification:           {sampled: 500, passed: 500, failed: 0}
fingerprint_diff:                {added: [...], removed: [...]}
```

`unclassified_rows > 0` catches #4 (new shapes). `row_count_delta_pct` below the threshold catches the silent-skip class. `median_price_delta_pct > 2x` catches #5 (semantics change) since the numbers themselves shift even when every parser succeeds. `citation_verification` running the round-trip on a row sample catches #1, #2, #3, #6 at the path-resolution level.

### Hard fail vs soft warn

| Signal | Action |
|---|---|
| `KeyError` or builder exception | Hard fail. Index not built. Receipt records the error. Previous index stays usable. |
| `json_path` verification fails on >1% of sampled rows | Hard fail. |
| Row count drops >50% vs previous snapshot | Hard fail. |
| Fingerprint diff non-empty | Soft warn. Index built. Receipt and report flagged. |
| Unclassified rows appear | Soft warn. Index built. Rows logged for human review. |
| Median family price shifts >2x | Soft warn. Build proceeds. Receipt flagged. |

Hard fail keeps yesterday's index usable so the agent does not go dark while the human investiingest. This is important: total outage is worse than degraded service with an honest warning.

### Flag taxonomy

A small, stable enum the indexer emits and the response envelope (ADR 0005) carries:

```
schema_drift                       fingerprint changed
new_unclassified_shapes            priced rows matched no family
family_coverage_gap                family lost a provider's rows entirely
row_count_drop                     >30% rows missing vs previous snapshot
price_shift_detected               median family price shifted >2x
citation_verification_partial      some sampled json_paths failed to resolve
snapshot_stale                     age_hours > 24
index_rebuild_failed_fell_back     today's snapshot unparseable; previous in use
provider_unavailable               no usable index for this provider at all
```

These are the only drift identifiers the system uses. Free-text goes in `human_summary` per ADR 0005; flags are enum-like so eval can assert on them.

## Consequences

### Positive

- Silent rot is hard. Numbers, structures, and per-family medians are all checked on every build.
- Eval lane 2 (staleness / refusal) extends naturally to drift. A new lane 3 (drift detection) is straightforward: feed a doctored snapshot with a renamed field, assert the appropriate flag fires.
- The audit trail (fingerprint + report per snapshot) accumulates over time and supports retrospective "when did this start changing" forensics.
- Operator gets one artifact to read (`index_report.json`) rather than a log file.

### Negative

- First snapshot per provider has no baseline to diff against. Layer 1 (fingerprint) is dormant until snapshot N+1. Acceptable: we already have multiple snapshots on disk for IBM, and the rest accumulate quickly.
- Layer 2 thresholds (>30%, >50%, >2x) are operator judgement, not derived. They will need tuning over the first few months. We err on the side of more warnings now and tighten later.
- Schema fingerprinting samples; it does not exhaustively walk huge files like AWS. A drift that only affects rows we did not sample escapes layer 1. Layer 2's citation_verification sample is the backstop.

### Neutral

- Drift never auto-fixes. The recovery path is always: human reads report, edits `families.json` or the per-provider builder, rebuilds the index. Automation of taxonomy proposals is out of v0 (`propose_equivalence` is v1).

## Alternatives considered

- **JSON Schema per provider, validated at fetch time.** Heavier. Would block fetches on schema changes that are benign (new field added). Rejected.
- **Statistical anomaly detection on price distributions.** Tempting but over-engineered for v0. Median shift threshold is the 80/20 version.
- **No drift detection, rely on eval to catch issues.** Rejected: eval runs against fixed scenarios. A drift that does not happen to touch a scenario goes undetected. Index-build-time detection runs on every snapshot.
