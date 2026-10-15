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
4. **Retries cover 429, 5xx and transport errors, and the host slows down as a
   whole.** Every request of a run goes through one pacer per host: request
   starts are at least `INGEST_MIN_REQUEST_INTERVAL_SECONDS` (0.2) apart, a 429
   or 5xx pauses every request to that host (not just the one that was told),
   and a response whose `RateLimit-Remaining` reaches 0 pauses the host until
   its `RateLimit-Reset` (seconds or epoch). `Retry-After` is read as seconds or
   an HTTP date and never waits less than our own backoff, so `Retry-After: 0`
   is not a burst; a wait over `INGEST_HTTP_MAX_RETRY_AFTER_SECONDS` (300) fails
   the fetch rather than stalling it. The pause is applied before a request
   gives up (last attempt, or a wait over the cap), and a request already
   waiting its turn rechecks for a pause that landed while it slept. A
   transport error or an attempt past its whole-attempt deadline
   (`INGEST_HTTP_ATTEMPT_DEADLINE_SECONDS`, 600; 3600 for AWS region files)
   pauses the host for the backoff too. httpx.LocalProtocolError, our own
   malformed request, is never retried. Backoff has jitter. Settings are
   bounded at load, so a zero backoff or a negative retry count fails startup.
5. **Paging cannot run away, and requests stay on the provider's hosts.** Every
   paging loop refuses a next token or link it has seen before and stops past
   `INGEST_MAX_PAGES` (1000). The client sends requests only over https to the
   provider's own hosts, so a redirect or a provider-supplied next link that
   points elsewhere fails the fetch before anything (GCP's `X-goog-api-key`
   header included, which httpx keeps on cross-host redirects) is sent, and a
   redirect chain longer than `INGEST_HTTP_MAX_REDIRECTS` (3) fails. Header
   credentials are registered as secrets and scrubbed from every log line and
   emitted JSON; GCP's key is trimmed and checked for header-safe characters
   before use.
6. **Only whole data counts as a finished file.** Only a 2xx with a non-empty,
   non-HTML body is stored; one download per file name runs at a time, so a
   name requested twice (IBM listing a plan twice) is fetched once. A stored
   page that is not JSON is taken off the progress log and deleted when read,
   so the next run fetches it again rather than resuming past it. Resume
   clears leftover `.part` files, and an unreadable progress log fails with a
   pointer to `--fresh`. Error bodies are read only up to 500 bytes.
7. **Only an accepted index is served.** The build writes `index.parquet.part`,
   runs the gates, and renames it to `index.parquet` only on pass; a rejected
   build is kept as `index.rejected.parquet`, the previous accepted snapshot
   keeps serving, and the data-quality envelope says so with
   `index_rebuild_failed_fell_back`. Row-count and fingerprint baselines come
   only from accepted snapshots.
8. **Builders account for what they skip.** A *candidate* is a record the
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
- The schema fingerprint (ADR 0004) now tells records from maps: an object keyed
  by data (AWS SKUs and offer codes, IBM plan UUIDs, region codes, or more than
  64 keys) collapses to one `{*}` path with a few values walked in sorted order.
  The AWS fingerprint went from ~1.5 GB per snapshot, different with every new
  SKU, to 88 entries in 0.1 s, walking 8 levels so the AWS price leaf (`pricePerUnit.USD`) is covered. Fingerprints carry `version: 2`; an older one is
  not compared, and is skipped after reading its first bytes.
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
