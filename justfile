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

# Placeholder until ruff and pytest land.
check:
    @echo "no checks defined yet"
