# ADR 0002: Index builder lives in `normalize/`, not in ingest

- **Status:** Accepted
- **Date:** 2026-05-26
- **Supersedes:** N/A

## Context

ADR 0001 establishes a parquet index per snapshot as the query target. The next question is where the build code lives: inside each ingest module (ingest produces both raw JSON and parquet) or inside the normalize layer (ingest only fetches; normalize builds and reads the index).

Two pressures point in different directions:

- Ingest modules already know their provider's source schema, so they are the natural place to emit the normalized form.
- The normalize layer also needs to know all seven schemas in order to query, so co-locating schema knowledge in one place reduces duplication.

## Decision

The index builder lives in `normalize/`, not in ingest. Concretely:

- `normalize/index.py` orchestrates index builds.
- One per-provider builder per file: `normalize/builders/{aws,gcp,azure,oracle,vultr,linode,ibm}.py`.
- Each builder takes a path to a snapshot directory and returns a list of typed rows.
- `normalize/index.py` writes the parquet to `store/<provider>/<ISO>/index.parquet` and emits `index_report.json` alongside (see ADR 0004).
- The build is triggered by the normalize CLI/FastAPI on startup or first query; ingest modules do not invoke it.
- Re-running an index build on a snapshot that already has a valid parquet is a no-op (driven by file presence plus a small fingerprint check).

## Consequences

### Positive

- Ingest modules stay single-purpose: fetch raw JSON, write receipt, exit. Easier to reason about, easier to test, easier to schedule.
- All schema knowledge lives in one directory tree. Onboarding a new provider means writing one fetcher in `ingest/` and one builder in `normalize/builders/`, and nothing else touches the rest of the system.
- The query layer (`compare`, `lookup`) imports from `normalize/builders/` indirectly via the parquet, so the type contract between builder output and query input is the parquet schema itself. The parquet schema is in one place, versioned, and inspectable.
- If a builder changes (e.g. we add a column), only `normalize/` changes. Ingest stays still.

### Negative

- Builders re-walk the raw JSON the indexer just read. We could fold the parse into the fetch and save one pass. The saved time is small (parsing AWS is 2 to 4 seconds; we do it once per 24h), and the architectural clarity is worth it.
- A failed builder (schema drift, see ADR 0004) does not block a fetch from completing. Operator has to look at two artifacts (receipt.json from the ingest module plus index_report.json from the indexer) to see the full picture. Mitigated by the data_quality envelope (ADR 0005) which surfaces both.

### Neutral

- Re-running an indexer over an existing snapshot is the natural recovery path when we ship a builder fix. No re-fetch is needed.

## Alternatives considered

- **Builders embedded in ingest.** Tighter coupling. Forces every ingest module to depend on parquet, polars, etc. Spreads schema knowledge across the codebase. Rejected.
- **A single monolithic indexer.** One `normalize/index.py` with a giant `if provider == "aws"` block. Easier to read end-to-end but harder to test in isolation. Rejected in favor of one file per provider.
- **Lazy index build at first query, no eager step.** Acceptable but means the first user-facing query pays the build cost. Eager build on startup (or as a CLI step the operator can run after `just fetch-all`) keeps queries snappy.
