"""Azure index builder.

Azure publishes per-region snapshot files (eastus.json, westeurope.json,
southeastasia.json) filtered to `serviceName=Virtual Machines` + `priceType=
Consumption`. Spot and Low-Priority rows are mixed in under Consumption and
must be post-filtered by meterName. Windows rows are filtered out (Linux/no-OS
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

from ingest._shared import PROJECT_ROOT
from normalize.builders import BuilderOutput
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow

from .rows import load_specs, rows_for_region

PROVIDER = "azure"


def build(snapshot_dir: Path) -> BuilderOutput:
    specs = load_specs()
    snapshot_iso = snapshot_dir.name

    rows: list[IndexRow] = []
    source_files: list[str] = []
    # Combined fingerprint across all per-region files so drift detection sees
    # the whole snapshot, not just one region.
    fp_acc: list[list[str]] = []

    for region_file in sorted(snapshot_dir.glob("*.json")):
        if region_file.name in ("receipt.json", "index_report.json", "schema_fingerprint.json"):
            continue
        doc = orjson.loads(region_file.read_bytes())
        store_path = _relpath(region_file)
        source_files.append(store_path)
        rows.extend(
            rows_for_region(
                doc=doc,
                specs=specs,
                snapshot_iso=snapshot_iso,
                store_path=store_path,
            )
        )
        fp_acc.extend(fp_walk(doc))

    # Dedup fingerprint entries (same path may appear in each region file).
    fp_dedup = sorted({tuple(e) for e in fp_acc})
    fingerprint = [list(e) for e in fp_dedup]

    return BuilderOutput(rows=rows, fingerprint=fingerprint, source_files=source_files)


def _relpath(path: Path) -> str:
    return str(path.relative_to(PROJECT_ROOT))
