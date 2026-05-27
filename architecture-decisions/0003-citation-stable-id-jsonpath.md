# ADR 0003: Citations use stable-ID JSONPath, verified at index build

- **Status:** Accepted
- **Date:** 2026-05-26
- **Supersedes:** N/A

## Context

The citation contract (see SPEC.md, AGENTS.md) requires every price in a response to carry `store_path` plus `json_path`, so a reader can open the raw file, walk the path, and confirm the number. The parquet index is the agent's query target, but the citation must point at the raw file.

Two questions:

1. What JSONPath form do we emit, given that some providers store data in dict-keyed maps (AWS `products[<sku>]`) and others in arrays (`skus[]`, `items[]`, `plans[]`, `types[]`)?
2. How do we keep the `json_path` honest as upstream providers reorder or restructure their responses?

Array indices (`$.items[42]`) are fragile: a provider may reorder rows between snapshots and break every citation. We need an addressing scheme that survives reordering and that we can verify mechanically.

## Decision

The parquet index carries a `json_path` column populated with a JSONPath expression that:

- Uses **stable provider-issued identifiers** in filter expressions, never array positions.
- Resolves to the price node (the leaf the agent will quote) and only to the price node. If the row also needs a product-attribute path, we carry it as a second column (`json_path_product`).
- Is **verified at index build time** by re-resolving it against the loaded raw JSON and asserting the value equals the price recorded. Verification failures fail the build (see ADR 0004).

Stable identifiers per provider:

| Provider | Stable key | Citation idiom |
|---|---|---|
| AWS | `sku` (16-char alphanumeric) | `$.terms.OnDemand['<sku>'].*.priceDimensions.*.pricePerUnit.USD` |
| GCP | `skuId` (`XXXX-XXXX-XXXX`) | `$.skus[?(@.skuId=='<id>')].pricingInfo[0].pricingExpression.tieredRates[0].unitPrice` |
| Azure | `meterId` (UUID) | `$.items[?(@.meterId=='<id>')].unitPrice` |
| Oracle | `partNumber` (`B`-prefixed) | `$.items[?(@.partNumber=='<id>')].currencyCodeLocalizations[?(@.currencyCode=='USD')].prices[0].value` |
| Vultr | `id` (plan slug) | `$.plans[?(@.id=='<id>')].monthly_cost` |
| Linode | `id` (type slug) plus optional region override id | `$.types[?(@.id=='<id>')].price.monthly` or `$.types[?(@.id=='<id>')].region_prices[?(@.id=='<region>')].monthly` |
| IBM | `plan_id` (UUID), `deployment_region`, `metric_id` | `$.compute['<service>'].pricing['<plan_id>'].resources[?(@.deployment_region=='<region>')].metrics[?(@.metric_id=='<metric>')].amounts[?(@.country=='USA' & @.currency=='USD')].prices[0].price` (single `&`, not `&&`: jsonpath_ng.ext's filter dialect does not accept the C-style double-ampersand) |

Oracle Flex shapes (E3+, A1+, X9+) price OCPU and memory as separate parts. We do NOT synthesize a single `json_path` for the composite. Instead the parquet keeps one row per atomic SKU (one for OCPU, one for memory), and the response carries a `composite: [citation_a, citation_b]` field on the result. The agent's prose flags this to the user (see SPEC.md).

## Consequences

### Positive

- The citation contract holds across snapshots even when upstream reorders. Filter-by-stable-key beats array-index in every realistic drift mode.
- Index-build verification (`jsonpath_ng.parse(json_path).find(loaded)`) is mechanical and runs on a sample of rows per build. This converts the citation contract into a build-time invariant rather than a runtime hope. Eval lane 1 (citation correctness, SPEC.md) is de-risked because the parquet cannot lie about a path it could not resolve.
- The parquet IS the citation index. No second source of truth. Schema for `json_path` is one string column per row.

### Negative

- Filter-expression JSONPath is verbose. IBM paths are ~200 chars. Across the AWS-eu-central-1 index that is roughly 40 MB of path strings, which parquet compresses to single-digit MB on disk. Acceptable.
- Filter-expression JSONPath requires a real JSONPath library on the verifier side (`jsonpath_ng`). Adds a dependency. Acceptable: the library is small and pure-Python.
- Two-citation results for Oracle Flex shapes need explicit handling in the response shape and in the agent's prose. Surfaced in SPEC.md.

### Neutral

- Future migration to a different addressing scheme (e.g. URL-fragments) does not require re-fetching raw snapshots; only the parquet rebuilds.

## Alternatives considered

- **Array-index paths** (`$.skus[42]`). Rejected: brittle to reorder, gives the citation contract no integrity guarantee.
- **Opaque locator objects** (`{kind: aws_sku, sku: "ABC123"}`) translated to JSONPath at response time. Rejected: pushes per-provider logic into the query layer and gives the human verifying the citation an extra hop. The parquet carrying the literal path is more transparent.
- **Skip JSONPath entirely, cite by `(file, line)`.** Rejected: raw JSON is often single-line minified, and line numbers do not survive any re-fetch where the upstream adds a row.
