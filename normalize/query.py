"""compare() and lookup() query layer over the parquet indexes.

Per SPEC.md these are the agent's primary tools. compare() answers
"cheapest N vCPU M GB <family> in <region> across these providers" with full
citation blocks. lookup() answers "what is <instance_type> in <region> on
<provider>" the same way.

Atomic providers (aws/azure/ibm/linode/vultr) contribute instance rows that
compare() filters with the closest-larger policy. Resource-priced providers
(gcp/oracle) contribute rate rows that compare() synthesizes into custom-shape
results using normalize/taxonomy/flex_rules.json, per ADR 0006 and ADR 0007.
Synthesized results carry composite citations with one entry per constituent
SKU (CPU/OCPU rate + RAM rate)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import polars as pl

from normalize.citations import build_citation
from normalize.data_quality import compute_envelope
from normalize.flex_synthesis import load_flex_rules
from normalize.flex_synthesis import synthesize_rate_results
from normalize.instance_ranking import (
    candidate_brief,
    filter_instance_rows,
    rank_instance_rows,
)
from normalize.loader import load_latest
from normalize.query_models import (
    CompareRequest,
    CompareResponse,
    CompareResult,
    LookupRequest,
    LookupResponse,
    LookupResult,
    UnmetRequirement,
)

DEFAULT_PROVIDERS = ["aws", "azure", "ibm", "linode", "vultr", "gcp", "oracle"]
ANY_FAMILY = "any"
HOURS_PER_MONTH = 730.0


def compare(
    *,
    vcpu: int,
    ram_gb: float,
    region: str,
    family: str = ANY_FAMILY,
    providers: list[str] | None = None,
    expand: str = "cheapest",
) -> dict[str, Any]:
    """Return cheapest-per-provider results meeting the closest-larger policy.

    Match policy (per SPEC.md): the chosen candidate satisfies
    vcpu_actual >= vcpu AND ram_gb_actual >= ram_gb, smallest first, ties broken
    by lower monthly_usd.
    """
    providers = providers or DEFAULT_PROVIDERS
    results: list[CompareResult] = []
    unmet: list[UnmetRequirement] = []

    for provider in providers:
        loaded = load_latest(provider)
        if loaded is None:
            unmet.append(UnmetRequirement(provider=provider, reason="no usable index"))
            continue
        df, snapshot_dir = loaded
        receipt = _read_receipt(snapshot_dir)

        # Atomic-instance path: filter by closest-larger and rank.
        candidates = filter_instance_rows(
            df=df,
            region=region,
            family=family,
            vcpu=vcpu,
            ram_gb=ram_gb,
            provider=provider,
            any_family=ANY_FAMILY,
        )
        instance_winner: CompareResult | None = None
        if not candidates.is_empty():
            ranked = rank_instance_rows(candidates)
            winner = ranked.row(0, named=True)
            instance_winner = CompareResult(
                provider=provider,
                instance_type=winner["instance_type"],
                region_native=winner["region_native"],
                vcpu_actual=int(winner["vcpu"]),
                ram_gb_actual=float(winner["ram_gb"]),
                hourly_usd=winner["hourly_usd"],
                monthly_usd=winner["monthly_usd"],
                considered_count=ranked.height,
                citation=build_citation(winner, receipt),
            )
            if expand == "full":
                instance_winner.considered = [
                    candidate_brief(r) for r in ranked.iter_rows(named=True)
                ]

        # Rate-row synthesis path: walk rate rows + flex_rules to compose
        # custom-shape results for resource-priced providers.
        synthesized = synthesize_rate_results(
            df=df,
            provider=provider,
            region=region,
            family=family,
            vcpu=vcpu,
            ram_gb=ram_gb,
            receipt=receipt,
            any_family=ANY_FAMILY,
            hours_per_month=HOURS_PER_MONTH,
        )
        # Cheapest among instance winner + synthesized candidates becomes the
        # provider's representative.
        per_provider: list[CompareResult] = []
        if instance_winner is not None:
            per_provider.append(instance_winner)
        per_provider.extend(synthesized)
        if not per_provider:
            unmet.append(
                UnmetRequirement(
                    provider=provider,
                    reason="no candidate meets vcpu and ram_gb",
                )
            )
            continue
        per_provider.sort(key=lambda r: (r.monthly_usd or float("inf"), r.hourly_usd or float("inf")))
        results.append(per_provider[0])

    results.sort(key=lambda r: (r.monthly_usd or float("inf"), r.hourly_usd or float("inf")))

    return CompareResponse(
        request=CompareRequest(
            vcpu=vcpu,
            ram_gb=ram_gb,
            region=region,
            family=family,
            providers=providers,
        ),
        results=results,
        unmet_requirements=unmet,
        data_quality=compute_envelope(providers),
    ).to_public_dict()


def lookup(
    *,
    provider: str,
    instance_type: str,
    region: str,
) -> dict[str, Any]:
    """Return a single instance's price for a specific region.

    `region` accepts either a canonical bucket (us-east) or a provider-native
    code (us-east-1). Match is exact on instance_type.
    """
    loaded = load_latest(provider)
    request = LookupRequest(
        provider=provider,
        instance_type=instance_type,
        region=region,
    )
    if loaded is None:
        return LookupResponse(
            request=request,
            result=None,
            data_quality=compute_envelope([provider]),
            unmet_requirements=[
                UnmetRequirement(provider=provider, reason="no usable index")
            ],
        ).to_public_dict()
    df, snapshot_dir = loaded
    receipt = _read_receipt(snapshot_dir)

    region_filter = (pl.col("region_canonical") == region) | (pl.col("region_native") == region)
    matches = df.filter(
        (pl.col("instance_type") == instance_type)
        & region_filter
        & (pl.col("row_kind") == "instance")
    )

    if matches.is_empty():
        return LookupResponse(
            request=request,
            result=None,
            data_quality=compute_envelope([provider]),
            unmet_requirements=[
                UnmetRequirement(
                    provider=provider,
                    reason=f"no priced row for {instance_type} in {region}",
                )
            ],
        ).to_public_dict()

    row = matches.row(0, named=True)
    citation = build_citation(row, receipt)
    return LookupResponse(
        request=request,
        result=LookupResult(
            provider=provider,
            instance_type=row["instance_type"],
            family=row["family"],
            region_native=row["region_native"],
            vcpu=int(row["vcpu"]),
            ram_gb=float(row["ram_gb"]),
            hourly_usd=row["hourly_usd"],
            monthly_usd=row["monthly_usd"],
            citation=citation,
        ),
        data_quality=compute_envelope([provider]),
        unmet_requirements=[],
    ).to_public_dict()


# ---------- helpers ----------


def _read_receipt(snapshot_dir: Path) -> dict[str, Any]:
    receipt_path = snapshot_dir / "receipt.json"
    if not receipt_path.exists():
        return {}
    return json.loads(receipt_path.read_text())


def _load_flex_rules() -> dict[str, Any]:
    """Compatibility seam for tests that intentionally exercise real flex rules."""
    return load_flex_rules()
