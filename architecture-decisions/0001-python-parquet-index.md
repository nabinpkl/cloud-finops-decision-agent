# ADR 0001: Python plus cached parquet index, not Rust, not raw-JSON-per-query

- **Status:** Accepted
- **Date:** 2026-05-26
- **Supersedes:** N/A

## Context

The normalization layer answers queries (`compare`, `lookup`) against on-disk snapshots. The heaviest providers are AWS (~1.2 GB across three regions of raw JSON) and IBM (~340 MB of `compute.json` from the three-hop walk). Everything else is small (GCP 42 MB, Azure 18 MB total, Oracle/Vultr/Linode KB-scale).

Two questions came up:

1. Is Python fast enough, or do we need Rust for ingest?
2. Should we re-parse the raw JSON on every query, or cache a normalized intermediate?

The bottleneck is JSON parsing at cold start, not query-time compute. Once parsed, the data is tabular: AWS eu-central-1 is ~94k product rows, IBM is 237 plans. Filtering by family prefix plus closest-larger vCPU/RAM is a single dict pass. No matrix math, no embeddings, no concurrency requirements beyond one agent at a time in v0.

## Decision

1. Stay on Python for the entire normalization layer.
2. Use `orjson` for parsing raw snapshots (2 to 5 times faster than stdlib `json` on the AWS files).
3. Build a cached parquet index per snapshot. The indexer parses raw JSON once, emits one row per priced SKU into `store/<provider>/<ISO>/index.parquet`, and the query layer reads parquet from then on.
4. Use Polars for in-process dataframe operations on the parquet. DuckDB is a future option if cross-provider SQL becomes attractive; not needed for v0.
5. Do not use numpy directly. The data is sparse heterogeneous attributes, not numeric arrays; numpy would be wrapped in pandas/polars-shaped code anyway.

## Consequences

### Positive

- Cold start (first query after a fresh fetch) pays one-time parquet build: roughly 5 to 10 seconds for AWS, sub-second for the rest. Warm queries are sub-100ms.
- The parquet schema is the natural integrity point. Schema drift surfaces at build time (see ADR 0004), not at query time.
- Memory footprint stays low: parquet on disk is roughly 20 to 50 MB total across all providers, loaded into Polars at need.
- One language across ingest, normalize, and eval. Smaller stack, faster iteration, no FFI.
- Aligns with the existing Python conventions doc (`python.md`).

### Negative

- We pay disk for the parquet alongside raw snapshots. ~50 MB per snapshot directory is negligible given AWS raw is already ~1.2 GB.
- Polars adds a dependency. Acceptable: it is a single wheel, well-maintained, and used for exactly the workload it is designed for.
- If we ever serve concurrent multi-user pricing queries with strict p99 latency, Python plus parquet may not be enough. That is not v0.

### Neutral

- Future move to Rust or to DuckDB is not foreclosed. The parquet artifact is portable and either could read it without touching the ingest.

## Alternatives considered

- **Re-parse raw JSON per query.** Rejected: AWS file alone takes 2 to 4 seconds to parse with orjson and would dominate every response. Caching is mandatory.
- **Sqlite instead of parquet.** Viable but heavier than needed. Parquet is columnar, queryable directly by Polars and DuckDB, and trivial to inspect with one-off scripts.
- **Rust ingest layer.** Rejected as scope creep. Would buy nothing at v0 scale; would introduce FFI and a second toolchain.
- **In-memory only (no parquet on disk).** Rejected: every restart of the FastAPI process would re-parse 1.5 GB of raw JSON. Build-once, read-many is the right shape.
