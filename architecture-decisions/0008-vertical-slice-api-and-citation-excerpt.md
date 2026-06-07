# ADR 0008: Vertical slice for the agent UI, and serve-time citation excerpts

- **Status:** Accepted (agent-runtime location and the fixed-Anthropic provider line superseded by [0009](0009-agent-runtime-in-fastapi-openai-agents-sdk.md); citation snapshot-ref and serve-time excerpt decisions stand)
- **Date:** 2026-05-27
- **Supersedes:** N/A
- **Related:** [0001](0001-parquet-index.md), [0003](0003-citation-stable-id-jsonpath.md), [0007](0007-rate-rows-composite-citations.md), [0009](0009-agent-runtime-in-fastapi-openai-agents-sdk.md)

## Context

The normalize layer is complete: `compare()` and `lookup()` answer pricing
queries over the parquet indexes with full citation blocks and a `data_quality`
envelope. What remains in v0 is the surface the user actually touches: a FastAPI
wrapper and a Next.js + assistant-ui app where an agent calls the layer as a
tool and renders the result.

Two questions need deciding before code:

1. **How wide do we build before going end-to-end?** Building all of FastAPI,
   then all of Next.js, risks an API surface no client exercises and integration
   bugs found late.
2. **What does a citation look like once it leaves the process?** The internal
   `store_path` (`store/aws/<ISO>/eu-central-1.json`) is a filesystem path. It
   means nothing to a browser, leaks our on-disk layout, and points at files up
   to ~200 MB. The user must still be able to verify a quoted price.

## Decision

### 1. Vertical slice, not horizontal layers

Build one query type end-to-end through every layer before going wide. The
slice: a user types a comparison question in the browser, an agent calls
`compare()` over HTTP, and a `ComparisonTable` renders the ranked result with
per-row citations. Everything else (`lookup` UI / `PriceCard`, drift banners,
`expand=full` rendering, the eval lane) comes after the slice proves the chain.

Slice scope:

- `compare` ships end-to-end. `lookup` gets an API endpoint but no dedicated UI
  component in the slice.
- LLM provider: superseded by [0009](0009-agent-runtime-in-fastapi-openai-agents-sdk.md).
  The provider is no longer fixed to Anthropic; it is an OpenAI-compatible
  base-URL knob, and the agent loop runs in FastAPI, not the Next.js layer.
- API and frontend run on `localhost` (`uvicorn` on 8000, `next dev` on 3000). The
  production deploy story is a v1 conversation.
- No auth, no rate limiting. The citation contract is the trust layer: every
  number is verifiable, every refusal is honest.

### 2. The citation does not leak `store_path`

At the API boundary the internal `store_path` is dropped. In its place the
citation carries a structured **snapshot ref**:

```json
{
  "source_url": "https://pricing.us-east-1.amazonaws.com/.../eu-central-1/index.json",
  "json_path":  "$.terms.OnDemand['M5XLAR9K8H8WVZBC'].*.priceDimensions.*.pricePerUnit.USD",
  "fetched_at": "2026-05-27T07:32:08.467260Z",
  "age_hours":  6.21,
  "snapshot":   {"provider": "aws", "snapshot_iso": "2026-05-27T07-32-08Z", "filename": "eu-central-1.json"}
}
```

The ref is a logical identifier, not a filesystem path: no `store/` prefix, no
absolute path. It is the minimum the excerpt endpoint needs to locate the file,
and it is what the UI passes back when a user wants to see the cited lines.

`normalize/` is unchanged. `CitationBlock` and `CompositeCitationEntry` keep
`store_path` internally (the CLI, tests, and verifier need the filesystem path).
The API layer translates `store_path` to a `snapshot` ref and omits the raw
path. The translation is one function.

### 3. Verification is a serve-time, lazy excerpt, not a file download

The trust signal in prose is the age badge (`(snapshot 6h old)`). Deeper
verification is a code-excerpt view: the cited value rendered in context, with
line numbers, like a diff hunk. The user sees the price surrounded by its
sibling keys without downloading a 200 MB file.

The excerpt is computed **at serve time, on demand**, for only the citations a
user actually opens. It is **not** precomputed into the parquet at build time,
and it is not cached by user question. The durable artifact is the citation
pointer (`snapshot` ref + `json_path`); the excerpt is an ephemeral window peek
over that pointer.

Why not build time: the AWS region file is ~200 MB with hundreds of thousands of
priced rows. Computing and storing an excerpt for every row would balloon build
cost and either double `store/` size (a canonical pretty copy per snapshot) or
freeze the context window at build. Both lose to a lazy endpoint that pays the
cost only when a human clicks. Because this endpoint is public and
unauthenticated, it must not keep whole parsed source documents resident after
serving a small preview.

The excerpt endpoint:

```
GET /citation/excerpt
    ?provider=aws
    &snapshot_iso=2026-05-27T07-32-08Z
    &filename=eu-central-1.json
    &path=<url-encoded json_path>
    &context=4
```

returns a hunk:

```json
{
  "json_path":     "$.terms.OnDemand['M5XLAR9K8H8WVZBC'].*.priceDimensions.*.pricePerUnit.USD",
  "matched_value": "0.192000",
  "match_line":    5,
  "rendering":     "canonical (indent=2); line numbers are within the cited container, not the raw upstream file",
  "lines": [
    {"n": 1, "text": "{"},
    {"n": 2, "text": "  \"USD\": \"0.192000\"", "match": true},
    {"n": 3, "text": "}"}
  ]
}
```

Implementation (`normalize/citation_excerpt.py`):

1. Load the snapshot file request-locally with `orjson`; do not cache the
   parsed whole document.
2. Resolve `json_path` with `jsonpath_ng.ext`. Take the first match.
3. Pretty-print the matched value's **immediate parent container** with
   `indent=2`. Across all seven providers the leaf is a dict field (`USD`,
   `value`, `monthly_cost`, `price`, `unitPrice`, `monthly`), so the immediate
   parent is small (a price object, a plan object), never the giant top-level
   container. A size guard falls back to a minimal rendering if a parent is
   unexpectedly large.
4. Window `context` lines either side of the matched line; mark the match.

### Line numbers are canonical, not upstream

AWS bulk JSON and GCP `skus.json` arrive minified (one physical line). There is
no authoritative upstream line numbering to honor, so the excerpt is numbered
against our canonical `indent=2` rendering of the cited container, and the
`rendering` field says so. The UI copy must not imply these are the provider's
own line numbers.

## Consequences

### Positive

- `compare()` / `lookup()` stay parquet-only and fast. The expensive raw-file
  read happens only on an explicit excerpt request and the parsed document is
  released after the request.
- No parquet schema change, no verifier change. The excerpt feature is purely
  additive: one new module, one new endpoint.
- The browser never sees a filesystem path. The audit story survives: snapshot
  ref + `json_path` + the rendered excerpt is enough to confirm any number.
- Integration risk is found early. The slice exercises every layer on the first
  end-to-end query.

### Negative

- Each excerpt request into a large AWS/GCP/Azure file can pay an orjson parse
  of a large source file. That is acceptable for v0 because the route is
  file-size-bounded and rate-limited; a v1 byte-offset index or provider-aware
  streaming peek can remove it if needed.
- The excerpt's line numbers are not the upstream file's line numbers. Mitigated
  by labeling the rendering explicitly. For minified sources there is no
  alternative.
- A second endpoint and a second trust surface (the excerpt) to keep correct.
  Eval lane 1 should assert excerpt `matched_value` equals the quoted price.

### Neutral

- If v1 wants expandable context ("show more lines"), the endpoint already takes
  a `context` param; widening it re-renders from the cached parent. Going beyond
  the parent container to true whole-file line numbers would require the
  canonical-pretty-copy path this ADR declined, and is a v1 decision.

## Alternatives considered

- **Serve the whole snapshot file** (`GET /snapshot/.../file.json`). Rejected:
  200 MB downloads for AWS, and a path-serving route is a larger traversal
  surface than a single resolved-path excerpt.
- **Strip `store_path`, show only `json_path` text, no excerpt.** Considered and
  initially chosen, then widened: a code-excerpt with line numbers is the
  product thesis ("you verify by clicking through") made concrete, and is cheap
  enough as a lazy endpoint to include in v0.
- **Precompute excerpts into the parquet at build time** (new `cited_excerpt`
  and `cited_match_line` columns, populated by the verifier). Rejected: the AWS
  file size makes per-row build-time excerpting slow and forces either a
  storage doubling or a frozen context window. The lazy endpoint dominates.
