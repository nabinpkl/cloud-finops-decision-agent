# Contributing

Thanks for taking the time to improve `cloud-finops-decision-agent`. This repo is a Python/uv/just project for citation-backed cloud pricing. The important split is:

- `backend/src/ingest/`: provider fetchers that write raw timestamped snapshots.
- `backend/src/normalize/`: deterministic index building, taxonomy loading, queries, and citations.
- `backend/src/api/`: FastAPI app and server-side agent runtime.
- `frontend/`: frontend-only Next.js UI.

Read `README.md`, `SPEC.md`, and `AGENTS.md` before larger changes. `SPEC.md` owns data/API contracts. `AGENTS.md` owns agent behavior and citation rules.

## Setup

```sh
uv sync --project backend
cp .env.example .env
just check
```

Fill `.env` from `.env.example` only with local values. Do not commit `.env`, API keys, provider credentials, trace files, SQLite budget databases, or local snapshots from `store/`.

## Development Checks

Run the full local gate before opening a PR:

```sh
just check
```

That runs:

- `just lint` (`ruff check .`)
- `just typecheck` (`ty check`)
- `just test` (`pytest -m "not e2e"`)

End-to-end tests are separate because they require populated provider snapshots:

```sh
just test-e2e
```

## Provider Ingest Changes

Provider fetchers live in `backend/src/ingest/`. Keep ingest deterministic: fetch provider data, write raw snapshot files plus `receipt.json`, and print the receipt. Do not put model calls or semantic judgment in ingest code.

Use the shared fetch/storage helpers where possible, respect the freshness behavior, and document any provider-specific narrowing in `README.md`, `SPEC.md`, or an ADR when it changes the data contract.

Useful commands:

```sh
just fetch aws
just fetch-force gcp
just fetch-all
```

## Normalize Builder Changes

Normalize builders turn raw provider snapshots into comparable indexed rows. Keep provider-specific translation close to the relevant builder under `backend/src/normalize/builders/`.

When changing builders:

- Preserve citation fields: `source_url`, `store_path`, `json_path`, `fetched_at`, and `age_hours`.
- Keep prices traceable to a specific snapshot file and JSON path.
- Update taxonomy JSON under `backend/src/normalize/taxonomy/` through reviewable diffs.
- Rebuild the affected provider index with `just index <provider>` or `just index-force <provider>`.
- Add or update tests for parsing, citations, and query behavior.

Useful commands:

```sh
just index aws
just index-force azure
just compare 4 8 eu-central general-purpose
just lookup aws m5.xlarge eu-central-1
```

## Tests

Prefer focused tests that cover the changed contract. Mocked tests should not require a populated `store/`. Use `e2e` only when a test intentionally verifies behavior against real snapshots.

For bug fixes, add a regression test when the bug is observable through a public function, CLI command, API route, or provider builder.

## Pull Requests

Before opening a PR:

- Run `just check`.
- Mention whether `just test-e2e` was run.
- Explain any data-shape, citation, taxonomy, or provider-scope changes.
- Include examples for user-visible behavior changes.
- Confirm no secrets, local `.env`, traces, budget DBs, or large generated snapshots are included.

Contributions are accepted under the Apache License 2.0 in `LICENSE`.
