"""GCP index builder.

GCP's Cloud Billing Catalog, stored as raw pages (`skus-NNNN.json`, each
`{skus: [...], nextPageToken}`), prices Compute Engine per resource: one SKU per
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

from normalize.builders import BuilderOutput, SkipTally, merged_fingerprint
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.snapshot_files import snapshot_files

from .rows import family_regex, load_gcp_rules, maybe_rate_row

PROVIDER = "gcp"
PAGE_PREFIX = "skus-"


def build(snapshot_dir: Path) -> BuilderOutput:
    snapshot_iso = snapshot_dir.name
    rules = load_gcp_rules()
    family_re = family_regex(rules)
    tally = SkipTally()
    rows: list[IndexRow] = []
    source_files: list[str] = []
    fingerprints: list[list[list[str]]] = []

    for page in snapshot_files(snapshot_dir):
        if not page.name.startswith(PAGE_PREFIX):
            continue
        doc = orjson.loads(page.path.read_bytes())
        source_files.append(page.store_path)
        fingerprints.append(fp_walk(doc))
        for sku in doc.get("skus", []):
            row = maybe_rate_row(
                sku=sku,
                family_re=family_re,
                rules=rules,
                snapshot_iso=snapshot_iso,
                source_url=page.url,
                store_path=page.store_path,
                tally=tally,
            )
            if row is not None:
                rows.append(row)

    return tally.output(rows=rows, fingerprint=merged_fingerprint(fingerprints), source_files=source_files)
