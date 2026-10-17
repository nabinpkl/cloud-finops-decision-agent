"""RunPod index builder.

`gpu-types.json` is the GraphQL answer listing every GPU type with a price per
GPU-hour for Secure Cloud and Community Cloud. A tier is indexed only when its
flag (`secureCloud`, `communityCloud`) says it is offered, because RunPod fills
an unoffered tier's price with a placeholder. Each offered tier is one row named
`<gpu id> (<tier>)`: one GPU, no vCPU or RAM figure (RunPod sizes the host per
pod), region `global` since the price does not vary by data center. Spot prices
are indexed when present; RunPod returns them only to callers with an API key."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import orjson

from normalize.builders import HOURS_PER_MONTH, BuilderOutput, SkipTally
from normalize.indexing.fingerprint import fingerprint as fp_walk
from normalize.schema import IndexRow
from normalize.snapshot_files import SnapshotError, snapshot_files
from normalize.taxonomy.loader import classify_family

PROVIDER = "runpod"
FILE = "gpu-types.json"
REGION = "global"
TIERS = ("secure", "community")


def build(snapshot_dir: Path) -> BuilderOutput:
    files = [f for f in snapshot_files(snapshot_dir) if f.name == FILE]
    if not files:
        raise SnapshotError(f"{snapshot_dir}: receipt lists no {FILE}")
    raw = files[0]
    doc = orjson.loads(raw.path.read_bytes())
    tally = SkipTally()
    rows: list[IndexRow] = []
    for gpu in ((doc.get("data") or {}).get("gpuTypes")) or []:
        rows.extend(_rows(gpu, snapshot_iso=snapshot_dir.name, source_url=raw.url, store_path=raw.store_path, tally=tally))
    return tally.output(rows=rows, fingerprint=fp_walk(doc), source_files=[raw.store_path])


def _rows(gpu: dict[str, Any], *, snapshot_iso: str, source_url: str, store_path: str, tally: SkipTally) -> list[IndexRow]:
    out: list[IndexRow] = []
    gpu_id = gpu.get("id") or ""
    memory = gpu.get("memoryInGb")
    for tier in TIERS:
        if gpu.get(f"{tier}Cloud") is not True:
            continue
        tally.candidate()
        if not gpu_id or "'" in gpu_id:
            tally.missing_field("id")
            continue
        markets: list[tuple[Literal["on_demand", "spot"], str]] = [("on_demand", f"{tier}Price"), ("spot", f"{tier}SpotPrice")]
        for market, field in markets:
            price = gpu.get(field)
            if not isinstance(price, int | float) or price <= 0:
                if market == "on_demand":
                    tally.missing_field(field)
                continue
            name = f"{gpu_id} ({tier})"
            out.append(
                IndexRow(
                    provider=PROVIDER,
                    snapshot_iso=snapshot_iso,
                    instance_type=name,
                    family=classify_family(PROVIDER, name),
                    region_native=REGION,
                    region_canonical=None,
                    hourly_usd=float(price),
                    monthly_usd=float(price) * HOURS_PER_MONTH,
                    pricing=market,
                    gpu_model=gpu.get("displayName") or gpu_id,
                    gpu_count=1,
                    gpu_memory_gb=float(memory) if isinstance(memory, int | float) and memory > 0 else None,
                    source_url=source_url,
                    store_path=store_path,
                    json_path=f"$.data.gpuTypes[?(@.id=='{gpu_id}')].{field}",
                    cited_price_kind="hourly",
                )
            )
    return out
