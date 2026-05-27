# Architecture Decisions

This directory holds ADRs for `cloud-finops-decision-agent`. It sits at the repo root, not under `docs/`, because the decisions captured here are load-bearing context for anyone (human or agent) reading this codebase. They are read alongside `SPEC.md`, `AGENTS.md`, and `PRD.md`, not buried behind a docs hop.

Each ADR captures one decision: the context that forced it, what we chose, what we gave up.

Format follows Michael Nygard's original: Context, Decision, Status, Consequences. We keep them short. If an ADR runs past two screens it should probably be split.

## Index

| Number | Title | Status |
|---|---|---|
| [0001](0001-python-parquet-index.md) | Python plus cached parquet index, not Rust, not raw-JSON-per-query | Accepted |
| [0002](0002-index-lives-in-normalize.md) | Index builder lives in `normalize/`, not in gates | Accepted |
| [0003](0003-citation-stable-id-jsonpath.md) | Citations use stable-ID JSONPath, verified at index build | Accepted |
| [0004](0004-schema-drift-detection.md) | Schema drift detection via fingerprint plus coverage report | Accepted |
| [0005](0005-data-quality-envelope.md) | `data_quality` envelope on normalize responses | Accepted |
| [0006](0006-flex-rules-over-shape-catalog.md) | Flex-rules JSON over a fetched shape catalog for GCP and Oracle (v0) | Accepted |
| [0007](0007-rate-rows-composite-citations.md) | Rate rows and composite citations for resource-priced providers | Accepted |

## When to write an ADR

When a decision (a) closes off other reasonable options, (b) future-you might second-guess without the context, or (c) crosses a layer boundary in the architecture. Routine implementation choices do not need ADRs. Naming conventions, lint rules, and code style do not need ADRs.

## When to supersede

When a later ADR overturns an earlier one, set the earlier one's status to `Superseded by NNNN` and add a `Supersedes: NNNN` line to the new one. Do not delete the old ADR. The audit trail is the point.
