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
    free_port() {
      local port="$1"
      local pids

      pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
      if [ -z "$pids" ]; then
        return 0
      fi

      echo "freeing port $port (pids: $pids)"
      kill $pids 2>/dev/null || true

      for attempt in {1..50}; do
        pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
        if [ -z "$pids" ]; then
          return 0
        fi

        if [ "$attempt" -eq 20 ]; then
          echo "port $port still busy; force killing pids: $pids"
          kill -KILL $pids 2>/dev/null || true
        fi

        sleep 0.1
      done

      pids=$(lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true)
      echo "port $port is still busy after cleanup (pids: $pids)" >&2
      return 1
    }

    free_port "${API_PORT:-8000}"
    free_port 3000

    child_pids=()

    collect_process_tree() {
      local pid="$1"
      local child

      if ! kill -0 "$pid" 2>/dev/null; then
        return 0
      fi

      echo "$pid"
      while read -r child; do
        if [ -n "$child" ]; then
          collect_process_tree "$child"
        fi
      done < <(pgrep -P "$pid" 2>/dev/null || true)
    }

    live_child_pids() {
      local pid

      for pid in "${child_pids[@]}"; do
        collect_process_tree "$pid"
      done | awk 'NF' | sort -rn | uniq
    }

    cleanup() {
      local pids
      trap - INT TERM EXIT

      pids=$(live_child_pids)
      if [ -z "$pids" ]; then
        return 0
      fi

      echo "stopping dev process tree (pids: $pids)"
      kill $pids 2>/dev/null || true

      for _ in {1..30}; do
        pids=$(live_child_pids)
        if [ -z "$pids" ]; then
          return 0
        fi
        sleep 0.1
      done

      pids=$(live_child_pids)
      if [ -n "$pids" ]; then
        echo "force stopping dev process tree (pids: $pids)"
        kill -KILL $pids 2>/dev/null || true
      fi
    }

    trap cleanup INT TERM EXIT
    just api &
    child_pids+=("$!")
    just frontend &
    child_pids+=("$!")

    while true; do
      for pid in "${child_pids[@]}"; do
        if ! kill -0 "$pid" 2>/dev/null; then
          wait "$pid"
          status="$?"
          cleanup
          exit "$status"
        fi
      done
      sleep 0.2
    done

# Drive one live backend agent turn and print token deltas.
smoke:
    just -f {{backend_just}} -d {{backend}} smoke

# Run offline prompt/tool/citation evals. No model credentials or snapshots required.
eval:
    just -f {{backend_just}} -d {{backend}} eval

# Render the canonical runtime system prompt from prompts/system/manifest.yaml.
render-prompt:
    just -f {{backend_just}} -d {{backend}} render-prompt

# Supply-chain review helper. Not part of `just check` because pnpm audit needs network access.
audit:
    just -f {{backend_just}} -d {{backend}} dep-tree
    pnpm --dir frontend audit --prod

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
