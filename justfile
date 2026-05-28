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

# Run the FastAPI app (compare/lookup/citation excerpt) on localhost:8000.
api:
    uv run uvicorn api.main:app --reload --port 8000

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
