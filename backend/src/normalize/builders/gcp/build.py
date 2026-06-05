"""GCP index builder.

GCP's Cloud Billing Catalog prices Compute Engine per resource: one SKU per
(family, region) for CPU at a per-vCPU/hour rate, plus a sibling SKU for RAM at
a per-GB/hour rate. Predefined shapes (n2-standard-4 etc.) use these same
rates. There is no per-instance SKU in the pricing API.

Per ADR 0007 we emit rate rows: one parquet row per (family, region,
resource_group). compare() composes per-instance prices at query time using
normalize/taxonomy/flex_rules.json.

Filter:
- category.resourceFamily == "Compute"
- category.resourceGroup IN ("CPU", "RAM")
- category.usageType == "OnDemand"
- description: NOT containing "Sole Tenancy", "Custom", "DWS Defined Duration",
  "Reserved", "Spot", "Sustained Use Discount", "Preemptible"
- description matches a known family from flex_rules.json

Per ADR 0003 json_path uses the stable skuId.
"""

from __future__ import annotations

from pathlib import Path

import orjson

from ingest._shared import PROJECT_ROOT
from normalize.builders import BuilderOutput
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow

from .rows import family_regex, load_gcp_rules, maybe_rate_row

PROVIDER = "gcp"
SKUS_FILE = "skus.json"


def build(snapshot_dir: Path) -> BuilderOutput:
    skus_path = snapshot_dir / SKUS_FILE
    doc = orjson.loads(skus_path.read_bytes())
    snapshot_iso = snapshot_dir.name
    store_path = _relpath(skus_path)
    source_url = doc.get(
        "source_url",
        "https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus",
    )

    skus = doc.get("skus", [])
    rules = load_gcp_rules()
    family_re = family_regex(rules)
    rows: list[IndexRow] = []

    for sku in skus:
        row = maybe_rate_row(
            sku=sku,
            family_re=family_re,
            rules=rules,
            snapshot_iso=snapshot_iso,
            source_url=source_url,
            store_path=store_path,
        )
        if row is not None:
            rows.append(row)

    return BuilderOutput(rows=rows, fingerprint=fp_walk(doc), source_files=[store_path])


def _relpath(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))
