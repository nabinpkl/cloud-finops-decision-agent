# ADR 0019: Prices stay in the currency published; GPU types are their own dimension

- **Status:** Accepted
- **Date:** 2026-10-17

## Context

The first seven providers all publish USD, and the index had one pair of price
columns, `hourly_usd` and `monthly_usd`. The next providers do not fit that:

- Scaleway publishes only EUR. OVHcloud publishes per subsidiary: USD for its US
  regions, EUR for Europe and Asia.
- RunPod prices GPUs, not machines: a price per GPU-hour with no vCPU or RAM
  figure. Scaleway, OVHcloud and Exoscale publish GPU types beside their CPU types.
- OVHcloud publishes prices as integers in 1e-8 of a currency unit, and Scaleway
  prices some GPU types per minute, so the number a citation points at is not
  always the number quoted.

Converting EUR to USD needs an exchange-rate source and a policy (which rate,
which day), and the owner has not chosen one. A vCPU and RAM comparison that
admits GPU types ranks a cheap old GPU box as the cheapest general-purpose
machine.

## Decision

1. **A price is stored in the currency the provider published, never converted.**
   USD sources fill `hourly_usd`/`monthly_usd`; EUR-only sources fill
   `hourly_eur`/`monthly_eur`. A row carries one currency (`IndexRow` refuses
   both). `cited_price_kind` gains `hourly_eur` and `monthly_eur`, and the
   verifier checks the column each kind names.
2. **compare and lookup answer in USD.** EUR rows are indexed and their citations
   verified, but never ranked against USD. A provider or region with only EUR
   prices is listed in `unmet_requirements` ("only EUR prices match; not ranked
   against USD prices"); lookup of an EUR-only type says so instead of answering.
3. **GPU types carry `gpu_model`, `gpu_count` and `gpu_memory_gb`** (per GPU, as
   the provider names them; null when the source does not say). A type in the
   taxonomy's `gpu` family, or one that names GPUs, never answers a vCPU and RAM
   question. A GPU-priced type may have null `vcpu` and `ram_gb`; lookup says it
   does not serve GPU prices yet.
4. **`cited_price_scale`** records how the value at `json_path` becomes the cited
   column (`column = value * scale`): 1 for most sources, 1e-8 for OVHcloud, 60
   for a Scaleway per-minute price. A citation whose scale is not 1 carries
   `price_scale`, and an answer plan citing it must copy it.

## Consequences

- EUR providers and GPU types are on disk, verified and queryable from the
  parquet, but not yet in compare or lookup answers. Choosing a conversion policy,
  or ranking within EUR, is a later decision that needs no refetch: `just
  index-force` rebuilds from the stored responses.
- A GPU compare (by model, count and GPU memory) is a new query shape for the
  agent tools and answer rendering, left for its own change.
- The first seven providers' GPU types are in the `gpu` family by prefix but do
  not yet fill the GPU columns. Leaving them out of CPU compare changes answers
  only where a GPU type had been a provider's cheapest match.
