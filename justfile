set dotenv-load := true

backend := "backend"
backend_just := "backend/justfile"

default:
    @just --list

# List backend recipes.
backend:
    just -f {{backend_just}} -d {{backend}} --list

# Fetch one provider's full compute pricing catalog into store/<provider>/.
fetch provider:
    just -f {{backend_just}} -d {{backend}} fetch {{provider}}

# Fetch every provider in the v0 set.
fetch-all:
    just -f {{backend_just}} -d {{backend}} fetch-all

# Force a refetch ignoring the 24h freshness rule.
fetch-force provider:
    just -f {{backend_just}} -d {{backend}} fetch-force {{provider}}

# Build the parquet index for one provider's latest snapshot.
index provider:
    just -f {{backend_just}} -d {{backend}} index {{provider}}

# Force-rebuild the index even if index.parquet already exists.
index-force provider:
    just -f {{backend_just}} -d {{backend}} index-force {{provider}}

# Build indexes for every provider that has a supported builder.
index-all:
    just -f {{backend_just}} -d {{backend}} index-all

# Compare cheapest matching instance across providers.
compare vcpu ram region family="any":
    just -f {{backend_just}} -d {{backend}} compare {{vcpu}} {{ram}} {{region}} {{family}}

# Look up a single instance type's price.
lookup provider instance_type region:
    just -f {{backend_just}} -d {{backend}} lookup {{provider}} {{instance_type}} {{region}}

# Run the FastAPI app.
api:
    just -f {{backend_just}} -d {{backend}} api

# Run the frontend dev server (Next.js + assistant-ui) on localhost:3000.
frontend:
    pnpm --dir frontend dev

# Wrap `just dev` with Infisical. INFISICAL_ENV picks the slug, default dev.
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
    just frontend &
    wait

# Drive one live backend agent turn and print token deltas.
smoke:
    just -f {{backend_just}} -d {{backend}} smoke

# Run offline prompt/tool/citation evals. No model credentials or snapshots required.
eval:
    just -f {{backend_just}} -d {{backend}} eval

# Lint backend Python.
lint:
    just -f {{backend_just}} -d {{backend}} lint

# Type-check backend Python.
typecheck:
    just -f {{backend_just}} -d {{backend}} typecheck

# Run backend mocked integration tests.
test:
    just -f {{backend_just}} -d {{backend}} test

# Run backend real-file end-to-end tests.
test-e2e:
    just -f {{backend_just}} -d {{backend}} test-e2e

# Full gate: backend lint/type/test/eval. Frontend build remains separate.
check:
    just -f {{backend_just}} -d {{backend}} check
