"""Azure index builder.

Azure snapshots hold the Retail Prices API's raw pages per region
(`eastus-0001.json`, `westeurope-0002.json`, ...; each `{Items: [...],
NextPageLink, ...}`), filtered to `serviceName=Virtual Machines` + `priceType=
Consumption`. Spot and Low-Priority rows are mixed in under Consumption: spot
meters become `pricing="spot"` rows, low-priority meters are dropped. Windows rows are filtered out (Linux/no-OS
only for v0 to avoid licensing-bundled prices).

Unlike Linode/Vultr/IBM/AWS, Azure pricing rows do NOT carry vCPU or RAM. The
v0 builder parses vCPU from the documented armSkuName naming convention and
derives RAM from per-family ratios in normalize/taxonomy/azure_specs.json.
Families outside the v0 coverage set (D, E, F, L) are skipped, not flagged as
unclassified, because the absence is deliberate.

Per ADR 0003 the citation json_path uses the stable meterId (UUID) in a filter
expression."""

from __future__ import annotations

from pathlib import Path

import orjson

from normalize.builders import BuilderOutput, SkipTally, merged_fingerprint
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.snapshot_files import snapshot_files

from .rows import load_specs, rows_for_page

PROVIDER = "azure"


def build(snapshot_dir: Path) -> BuilderOutput:
    specs = load_specs()
    snapshot_iso = snapshot_dir.name
    tally = SkipTally()

    rows: list[IndexRow] = []
    source_files: list[str] = []
    fingerprints: list[list[list[str]]] = []
    seen_by_region: dict[str, set[tuple[str, str, str]]] = {}

    for page in snapshot_files(snapshot_dir):
        region_native = page.name.rsplit("-", 1)[0]
        doc = orjson.loads(page.path.read_bytes())
        source_files.append(page.store_path)
        rows.extend(
            rows_for_page(
                items=doc.get("Items", []),
                region_native=region_native,
                specs=specs,
                snapshot_iso=snapshot_iso,
                source_url=page.url,
                store_path=page.store_path,
                seen=seen_by_region.setdefault(region_native, set()),
                tally=tally,
            )
        )
        fingerprints.append(fp_walk(doc))

    return tally.output(rows=rows, fingerprint=merged_fingerprint(fingerprints), source_files=source_files)
