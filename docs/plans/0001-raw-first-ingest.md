# Plan 0001: raw-first ingest, parse offline

Status: planned (2026-10-15). Nothing below is built yet.

## Why

Six of the seven ingest modules do not store what the provider sent. GCP, Azure, Oracle,
Vultr, Linode and IBM decode each response, keep one array, wrap it in our own envelope and
write it back with `json.dumps(indent=2)`. Only AWS writes `resp.content` unchanged. That
costs three things:

- Fields the provider adds outside the kept array are dropped without a trace (Azure's
  `Count`, `BillingCurrency` and `CustomerEntity*` already are).
- `receipt.json` hashes our re-encoding, so it cannot prove what the provider sent.
- Paged fetches (GCP, Azure, IBM) hold every page in memory and write once at the end; a
  failure on the last page saves nothing and a retry starts from page 1.

A read-only review on 2026-10-15 also found gaps in the fetch and index path:

1. `fetch_polite` (`backend/src/ingest/_shared.py`) retries 429 and 5xx but not transport
   errors. A connection reset or read timeout fails the whole provider; AWS, three ~481 MB
   downloads in parallel, is the most exposed.
2. `fetch_polite` parses `Retry-After` with `float()`. The HTTP-date form raises
   `ValueError`, which only `ingest/aws.py` catches; the others exit with a traceback
   instead of their JSON error.
3. `normalize/indexing/build.py` writes `index.parquet` before the hard gates run, and
   `normalize/query/loader.py` serves the newest snapshot that has a parquet. An index that
   fails its gates is served.
4. Builders skip records whose fields are missing or renamed, and the only failing gate is a
   row drop of 50% or more. The schema fingerprint diff only warns.
5. IBM stores a failed per-plan pricing call as `{"error": ...}` and still writes a
   successful snapshot (`ingest/ibm/catalog.py`).
6. AWS holds each ~481 MB response in memory (about 1.4 GB for three regions).
7. `just fetch-all` does not index, so a fetched snapshot can sit unindexed (Oracle's
   2026-05-29 snapshot did, and answers still use 2026-05-25).

Upstream APIs were probed the same day: all seven endpoints answer with the fields the
builders read (GCP not probed; it needs the key). So this is a shape change on our side, not
a forced repair.

## Target shape

One snapshot directory per fetch, `store/<provider>/<ISO>/`:

```
store/gcp/2026-10-15T08-00-00Z/
  raw/skus-0001.json          # response body, byte for byte
  raw/skus-0002.json
  ...
  receipt.json                # written last; its presence means the fetch completed
  index.parquet               # written by normalize, only after the gates pass
  index_report.json
  schema_fingerprint.json
```

Per provider, the raw files are:

| Provider | Raw files | Next-request input read by the fetcher |
|---|---|---|
| aws | `region_index.json`, `<region>.json` | `regions.<r>.currentVersionUrl` |
| gcp | `skus-NNNN.json` | `nextPageToken` |
| azure | `<region>-NNNN.json` | `NextPageLink` |
| oracle | `products.json` | none |
| vultr | `plans.json` | none |
| linode | `types-NNNN.json` | `page`, `pages` |
| ibm | `catalog-NNNN.json`, `children/<service>-NNNN.json`, `pricing/<plan_id>.json` | `next`, child plan ids |

`receipt.json` keeps today's summary fields and gains `files[]`: name, request URL (API keys
never included), status, `Date`, `ETag`/`Last-Modified`, rate-limit headers when present,
size, and sha256 of the bytes received. Provider-specific counts (sku_count, regions) move
out of the fetcher: the builder computes them offline and records them in
`index_report.json`.

Citations keep the ADR 0003 contract: `store_path` names one raw file, `json_path` resolves
inside it. Paths change only where the envelope renamed things: Azure `$.items` becomes
`$.Items`, Linode `$.types` becomes `$.data`, IBM's `compute.<service>.pricing.<plan>`
becomes the `pricing/<plan_id>.json` file itself.

## Steps

Each step ends with `just check` green. Provider steps also end with a live fetch and index
of that provider and a comparison of `rows_written` against the May snapshot.

1. **ADR 0018: ingest stores provider bytes unchanged.** Amends ADR 0002 (what "raw" means)
   and ADR 0003 (citation paths point at raw page files). Update SPEC.md's snapshot and
   citation sections and the AGENTS.md "Snapshot layout per provider" section to the target
   shape in the same step.
2. **`fetch_polite` fixes (gaps 1 and 2).** Retry `httpx.TransportError` (connect, read
   timeout, protocol errors) on the same backoff as 5xx. Parse `Retry-After` as seconds or
   HTTP date; cap the wait at a new `INGEST_HTTP_MAX_RETRY_AFTER_SECONDS` and fail loud
   above it. Add jitter. Tests with `httpx.MockTransport`: 429 with seconds, 429 with a
   date, 503 then 200, `ConnectError` then 200, retries exhausted.
3. **Shared raw writer.** One `RawSnapshot` in `backend/src/ingest/_shared.py` (grep first;
   extend `FileRecord` rather than adding a second record type): opens or resumes a
   directory, `write(name, response)` streams the body to a temp file, hashes it and renames
   it, `finish(summary)` writes `receipt.json`. Resume: the newest directory with no
   receipt continues from its last complete file, reading only that file's next-request
   field. Tests for write, resume and the receipt.
4. **Single-response providers: Oracle, Vultr.** Fetcher writes the body through
   `RawSnapshot`; builder and citation paths unchanged apart from `store_path`. Small,
   proves the writer end to end.
5. **Linode.** Raw `types-NNNN.json` with paging honoured (today it assumes one page);
   builder reads `data`, citations `$.data[...]`.
6. **AWS (gap 6).** Stream each region to disk instead of `resp.content`; already raw, so
   the builder only changes how it finds files.
7. **Azure.** Page files per region, resume per region; builder reads `Items` across pages;
   citations `$.Items[...]` with the page file as `store_path`. Fix the stale "OnDemand
   only" comment: the filter keeps spot meters, the builder drops them.
8. **GCP.** Page files; builder reads `skus` across pages. Needs `GCP_API_KEY`; the key
   stays out of `files[].url`.
9. **IBM (gap 5).** Catalog pages, child pages and one pricing file per plan. A failed call
   is retried and, if it still fails, the fetch fails without a receipt; resume picks up the
   missing plans. Builder walks the three file sets.
10. **Gated promotion (gaps 3 and 4).** `build.py` writes `index.parquet.tmp`, runs the
    gates, then renames to `index.parquet` only on pass; a failed build keeps
    `index_report.json` with the failure. `data_quality` reports a rejected newer snapshot
    against the one still served. Each builder counts records skipped per reason (missing
    field, unparsed SKU, filtered by policy) into `index_report.json`, and the gate fails
    when a "missing field" count crosses a configured share of candidate records. Tests: a
    gutted snapshot keeps the previous index served.
11. **`just refresh <provider>` and `just refresh-all` (gap 7):** fetch then index, so a
    fetch is never left unindexed.
12. **Live end to end.** `just refresh-all`, then `just compare 4 16 us-east` and one
    `lookup` per provider; open a cited `store_path` and walk its `json_path` by hand for
    one row per provider.

## Old snapshots

The May snapshots use the envelope shape, which the new builders will not read. They are
five months stale and the APIs answer today, so step 12 refetches rather than converting.
`store/` is gitignored local data: after step 12 succeeds, move the May directories out of
`store/` to an archive folder and ask the owner before deleting anything. The first new
index per provider compares row counts against the May `index_report.json` and will flag a
fingerprint change once, expected from the shape change.

## Out of scope

Keeping spot rows for GCP and Azure (the raw files already hold them; that is a builder and
taxonomy change for a later plan), new providers or regions, scheduling fetches.

## Done when

All seven providers fetch into raw files with a per-file receipt, a killed paged fetch
resumes, a gate failure leaves the previous index served, `just check` is green, and the
live comparison in step 12 cites raw files that resolve.
