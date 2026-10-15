# ADR 0018: Ingest stores provider bytes unchanged; parsing happens offline

- **Status:** Accepted
- **Date:** 2026-10-15
- **Amends:** [0002](0002-index-lives-in-normalize.md) (what "raw" means),
  [0003](0003-citation-stable-id-jsonpath.md) (citations point at raw page files),
  [0004](0004-schema-drift-detection.md) (gates now decide what is served)

## Context

ADR 0002 split ingest (fetch) from normalize (build the index) and said ingest
writes "raw JSON". Six of seven ingest modules read that loosely: they decoded
each response, kept one array (`skus`, `Items`, `items`, `data`), wrapped it in
our own envelope and re-serialized it. Three things followed:

- Fields the provider added outside the kept array vanished without a trace
  (Azure's `Count`, `BillingCurrency` and `CustomerEntity*` already had).
- `receipt.json` hashed our re-encoding, so it proved nothing about what the
  provider sent.
- Paged fetches held every page in memory and wrote once at the end; a failure
  on the last page saved nothing.

A review on 2026-10-15 also found the fetch and index path fragile: transport
errors were not retried, an HTTP-date `Retry-After` crashed most fetchers, an
index that failed its own hard gates was still served (ADR 0004 said it would
not be), a renamed field only failed the build at a 50% row drop, IBM stored
failed pricing calls as a successful snapshot, and Vultr's fetcher read only
the first 100 of 138 plans because it ignored paging.

## Decision

1. **Every response is stored byte for byte**, one file per request or page,
   streamed to `<name>.part` and renamed into place when complete. Fetch code
   reads only what the next request needs (a page token, a next link, AWS's
   region index) and never filters, merges or re-serializes. File names are flat
   inside `store/<provider>/<ISO>/` because the citation excerpt route refuses a
   `filename` with a slash.
2. **`receipt.json` is written last** (`receipt_version: 2`) and lists each
   file's request URL (credentials stripped), status, kept headers (date, ETag,
   Last-Modified, content type, rate-limit) and sha256 of the bytes received. A
   directory without a receipt is an unfinished fetch. `fetch_progress.jsonl`
   is its checkpoint: run again within `INGEST_RESUME_WINDOW_HOURS` (6) and the
   fetch resumes after the last complete file; `--fresh` starts over.
3. **Builders read only the files a receipt lists** (`normalize/snapshot_files.py`),
   refuse an older receipt version, and check each file's size. Citations name
   the raw file (`store/gcp/<ISO>/skus-0003.json`) and a JSONPath inside the
   provider's own shape (`$.Items[...]` for Azure, `$.data[...]` for Linode,
   the plan's own `pricing-<plan_id>.json` for IBM). `source_url` is the request
   that fetched that file.
4. **Retries cover 429, 5xx and transport errors.** `Retry-After` is read as
   seconds or an HTTP date; a wait over `INGEST_HTTP_MAX_RETRY_AFTER_SECONDS`
   (300) fails the fetch rather than stalling it. Backoff has jitter.
5. **Only an accepted index is served.** The build writes `index.parquet.part`,
   runs the gates, and renames it to `index.parquet` only on pass; a rejected
   build is kept as `index.rejected.parquet`, the previous accepted snapshot
   keeps serving, and the data-quality envelope says so with
   `index_rebuild_failed_fell_back`. Row-count and fingerprint baselines come
   only from accepted snapshots.
6. **Builders account for what they skip.** A *candidate* is a record the
   builder's policy wants priced; a candidate missing a field it needs counts
   against that field. More than 1% missing flags `fields_missing`; more than 5%
   rejects the build. Zero candidates (`no_candidates`) or zero rows also reject
   it, which is how a renamed filter field shows up.

## Consequences

- A parser fix, a taxonomy change or an upstream rename is `just index-force
  <provider>` against files already on disk, never a refetch.
- A partial fetch is resumable and costs only the missing requests.
- Snapshots from before this ADR (May 2026) cannot be indexed; the builders
  refuse their receipts. They are refetched, not converted.
- The AWS schema fingerprint is still per SKU key and runs to ~1.5 GB per
  snapshot; ADR 0004's fingerprint needs map-key collapsing before it is useful
  for AWS. Not changed here.
- GCP and Azure spot prices are now in the stored pages; indexing them is a
  separate builder and taxonomy decision.

## Alternatives considered

- **Keep the envelope, add the missing fields.** Rejected: any field we do not
  know about is still lost, and the hash still covers our bytes.
- **Raw files in a `raw/` subfolder.** Rejected: the citation excerpt route's
  path guard refuses slashes in `filename`, and loosening a path guard to save a
  folder is the wrong trade.
- **A separate manifest file beside the receipt.** Rejected: one record of what
  was fetched (the receipt, with the progress log only while unfinished) is
  enough, and two would drift.
