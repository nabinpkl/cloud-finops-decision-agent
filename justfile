set dotenv-load := true

default:
    @just --list

# Fetch one provider's full compute pricing catalog into store/<provider>/.
fetch provider:
    uv run python -m gates.{{provider}}

# Fetch every provider in the v0 set.
fetch-all: (fetch "aws") (fetch "gcp") (fetch "azure") (fetch "oracle") (fetch "vultr") (fetch "linode") (fetch "ibm")

# Force a refetch ignoring the 24h freshness rule.
fetch-force provider:
    uv run python -m gates.{{provider}} --force

# Build the parquet index for one provider's latest snapshot.
index provider:
    uv run python -m normalize.index {{provider}}

# Force-rebuild the index even if index.parquet already exists.
index-force provider:
    uv run python -m normalize.index {{provider}} --force

# Build indexes for every provider that has a supported builder.
index-all: (index "linode") (index "vultr") (index "azure") (index "ibm") (index "aws") (index "gcp") (index "oracle")

# Compare cheapest matching instance across providers.
# Example: just compare 4 8 eu-central general-purpose
compare vcpu ram region family="any":
    uv run python -m normalize compare --vcpu {{vcpu}} --ram {{ram}} --region {{region}} --family {{family}}

# Look up a single instance type's price.
# Example: just lookup aws m5.xlarge eu-central-1
lookup provider instance_type region:
    uv run python -m normalize lookup --provider {{provider}} --instance-type {{instance_type}} --region {{region}}

# Run the FastAPI app (compare/lookup/citation excerpt + agent). Port from API_PORT (.env), default 8000.
api:
    uv run uvicorn api.main:app --reload --port ${API_PORT:-8000}

# Run the frontend dev server (Next.js + assistant-ui) on localhost:3000.
web:
    pnpm --dir web dev

# Wrap `just dev` with `infisical run` so secrets come from Infisical (one-time: `infisical login` + `infisical init`). INFISICAL_ENV picks the slug, default dev.
infcl-dev:
    infisical run --env=${INFISICAL_ENV:-dev} -- just dev

# Run API (8000) + frontend (3000) together, freeing both ports first. Ctrl-C stops both.
dev:
    #!/usr/bin/env bash
    set -uo pipefail
    for port in "${API_PORT:-8000}" 3000; do
      pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
      if [ -n "$pids" ]; then echo "freeing port $port (pids: $pids)"; kill $pids 2>/dev/null || true; fi
    done
    sleep 1
    trap 'kill $(jobs -p) 2>/dev/null' INT TERM EXIT
    just api &
    just web &
    wait

# Drive one live agent turn and print per-call context/token deltas. Expects creds in env (use `infisical run -- just smoke`).
smoke:
    uv run python -m scripts.agent_smoke

# Lint with ruff.
lint:
    uv run ruff check .

# Type-check with ty.
typecheck:
    uv run ty check

# Run the mocked integration tests (fast, no store/ dependency).
test:
    uv run pytest -m "not e2e"

# Run the real-file end-to-end tests (needs a populated store/).
test-e2e:
    uv run pytest -m e2e

# Full gate: lint, type-check, mocked tests. e2e is separate (needs a store).
check: lint typecheck test
