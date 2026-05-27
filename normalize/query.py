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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

import polars as pl

from gates._shared import PROJECT_ROOT
from normalize.data_quality import compute_envelope
from normalize.loader import load_latest

DEFAULT_PROVIDERS = ["aws", "azure", "ibm", "linode", "vultr", "gcp", "oracle"]
ANY_FAMILY = "any"
HOURS_PER_MONTH = 730.0

FLEX_RULES_PATH = PROJECT_ROOT / "normalize" / "taxonomy" / "flex_rules.json"

# Oracle's list price is global; rate rows carry region_canonical=null. compare()
# treats those rates as available in any canonical region.
GLOBAL_RATE_PROVIDERS = {"oracle"}


@dataclass
class CitationBlock:
    source_url: str
    store_path: str
    json_path: str
    fetched_at: str
    age_hours: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_url": self.source_url,
            "store_path": self.store_path,
            "json_path":  self.json_path,
            "fetched_at": self.fetched_at,
            "age_hours":  self.age_hours,
        }


@dataclass
class CompositeCitationEntry:
    kind: str               # "rate"
    rate_unit: str          # "per_vcpu_hour" | "per_ocpu_hour" | "per_gb_ram_hour"
    rate: float
    quantity: float
    contribution_usd: float
    source_url: str
    store_path: str
    json_path: str
    fetched_at: str
    age_hours: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind":             self.kind,
            "rate_unit":        self.rate_unit,
            "rate":             self.rate,
            "quantity":         self.quantity,
            "contribution_usd": self.contribution_usd,
            "source_url":       self.source_url,
            "store_path":       self.store_path,
            "json_path":        self.json_path,
            "fetched_at":       self.fetched_at,
            "age_hours":        self.age_hours,
        }


@dataclass
class CompositeCitation:
    constituents: list[CompositeCitationEntry]
    rule: str
    formula: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "composite":  [c.to_dict() for c in self.constituents],
            "synthesis": {"rule": self.rule, "formula": self.formula},
        }


@dataclass
class CompareResult:
    provider: str
    instance_type: str
    region_native: str
    vcpu_actual: int
    ram_gb_actual: float
    hourly_usd: float | None
    monthly_usd: float | None
    considered_count: int
    citation: CitationBlock | CompositeCitation
    considered: list[dict[str, Any]] = field(default_factory=list)  # populated when expand="full"
    synthesized: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "provider":         self.provider,
            "instance_type":    self.instance_type,
            "region_native":    self.region_native,
            "vcpu_actual":      self.vcpu_actual,
            "ram_gb_actual":    self.ram_gb_actual,
            "hourly_usd":       self.hourly_usd,
            "monthly_usd":      self.monthly_usd,
            "considered_count": self.considered_count,
            "citation":         self.citation.to_dict(),
        }
        if self.synthesized:
            out["synthesized"] = True
        if self.considered:
            out["considered"] = self.considered
        return out


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
    unmet: list[dict[str, Any]] = []

    for provider in providers:
        loaded = load_latest(provider)
        if loaded is None:
            unmet.append({"provider": provider, "reason": "no usable index"})
            continue
        df, snapshot_dir = loaded
        receipt = _read_receipt(snapshot_dir)

        # Atomic-instance path: filter by closest-larger and rank.
        candidates = _filter_instance_rows(
            df=df,
            region=region,
            family=family,
            vcpu=vcpu,
            ram_gb=ram_gb,
            provider=provider,
        )
        instance_winner: CompareResult | None = None
        if not candidates.is_empty():
            ranked = candidates.sort(
                ["vcpu", "ram_gb", "monthly_usd", "hourly_usd"],
                descending=[False, False, False, False],
                nulls_last=True,
            )
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
                citation=_build_citation(winner, receipt),
            )
            if expand == "full":
                instance_winner.considered = [
                    _candidate_brief(r) for r in ranked.iter_rows(named=True)
                ]

        # Rate-row synthesis path: walk rate rows + flex_rules to compose
        # custom-shape results for resource-priced providers.
        synthesized = _synthesize_rate_results(
            df=df,
            provider=provider,
            region=region,
            family=family,
            vcpu=vcpu,
            ram_gb=ram_gb,
            receipt=receipt,
        )
        # Cheapest among instance winner + synthesized candidates becomes the
        # provider's representative.
        per_provider: list[CompareResult] = []
        if instance_winner is not None:
            per_provider.append(instance_winner)
        per_provider.extend(synthesized)
        if not per_provider:
            unmet.append({"provider": provider, "reason": "no candidate meets vcpu and ram_gb"})
            continue
        per_provider.sort(key=lambda r: (r.monthly_usd or float("inf"), r.hourly_usd or float("inf")))
        results.append(per_provider[0])

    results.sort(key=lambda r: (r.monthly_usd or float("inf"), r.hourly_usd or float("inf")))

    return {
        "request": {
            "vcpu":     vcpu,
            "ram_gb":   ram_gb,
            "region":   region,
            "family":   family,
            "providers": providers,
        },
        "results":      [r.to_dict() for r in results],
        "ranked_by":    "monthly_usd",
        "unmet_requirements": unmet,
        "data_quality": compute_envelope(providers),
    }


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
    if loaded is None:
        return {
            "request": {"provider": provider, "instance_type": instance_type, "region": region},
            "result":  None,
            "data_quality": compute_envelope([provider]),
            "unmet_requirements": [{"provider": provider, "reason": "no usable index"}],
        }
    df, snapshot_dir = loaded
    receipt = _read_receipt(snapshot_dir)

    region_filter = (pl.col("region_canonical") == region) | (pl.col("region_native") == region)
    matches = df.filter(
        (pl.col("instance_type") == instance_type)
        & region_filter
        & (pl.col("row_kind") == "instance")
    )

    if matches.is_empty():
        return {
            "request": {"provider": provider, "instance_type": instance_type, "region": region},
            "result":  None,
            "data_quality": compute_envelope([provider]),
            "unmet_requirements": [
                {"provider": provider, "reason": f"no priced row for {instance_type} in {region}"}
            ],
        }

    row = matches.row(0, named=True)
    citation = _build_citation(row, receipt)
    return {
        "request": {"provider": provider, "instance_type": instance_type, "region": region},
        "result": {
            "provider":      provider,
            "instance_type": row["instance_type"],
            "family":        row["family"],
            "region_native": row["region_native"],
            "vcpu":          int(row["vcpu"]),
            "ram_gb":        float(row["ram_gb"]),
            "hourly_usd":    row["hourly_usd"],
            "monthly_usd":   row["monthly_usd"],
            "citation":      citation.to_dict(),
        },
        "data_quality": compute_envelope([provider]),
        "unmet_requirements": [],
    }


# ---------- helpers ----------


def _synthesize_rate_results(
    *,
    df: pl.DataFrame,
    provider: str,
    region: str,
    family: str,
    vcpu: int,
    ram_gb: float,
    receipt: dict[str, Any],
) -> list[CompareResult]:
    """For each (flex_family, region_native) in this provider's rate rows where
    both a compute-rate and a ram-rate exist, validate the user's ask against
    `flex_rules.json` and emit one CompareResult with a composite citation.

    Returns up to N results (one per family that the user's ask validates
    against). The caller picks the cheapest.
    """
    rules = _load_flex_rules().get(provider, {})
    if not rules:
        return []

    rate_rows = df.filter((pl.col("provider") == provider) & (pl.col("row_kind") == "rate"))
    if rate_rows.is_empty():
        return []

    if provider in GLOBAL_RATE_PROVIDERS:
        # Oracle rates are global; ignore the region filter for rate rows.
        pass
    else:
        rate_rows = rate_rows.filter(
            (pl.col("region_canonical") == region) | (pl.col("region_native") == region)
        )

    if family != ANY_FAMILY:
        rate_rows = rate_rows.filter(pl.col("family") == family)

    if rate_rows.is_empty():
        return []

    # The flex family slug lives as the prefix of instance_type, before the dot.
    rate_rows = rate_rows.with_columns(
        pl.col("instance_type").str.split(".").list.first().alias("_flex_family"),
    )

    results: list[CompareResult] = []
    fetched_at = receipt.get("fetched_at", "")
    age_hours = _age_hours(fetched_at)

    for (flex_family, region_native), group in rate_rows.group_by(
        ["_flex_family", "region_native"], maintain_order=True
    ):
        rule = rules.get(flex_family)
        if rule is None:
            continue
        if not _validate_ask(rule, vcpu, ram_gb):
            continue

        compute_row = _first_rate(group, ("per_vcpu_hour", "per_ocpu_hour"))
        ram_row = _first_rate(group, ("per_gb_ram_hour",))
        if compute_row is None or ram_row is None:
            continue

        compute_quantity = vcpu / float(rule["vcpu_per_unit"])
        compute_rate = float(compute_row["hourly_usd"])
        ram_rate = float(ram_row["hourly_usd"])
        compute_contribution = compute_quantity * compute_rate
        ram_contribution = ram_gb * ram_rate
        hourly = compute_contribution + ram_contribution
        if hourly <= 0:
            continue
        monthly = hourly * HOURS_PER_MONTH

        name = rule["custom_name_template"].format(
            vcpu=vcpu,
            ocpu=int(compute_quantity) if compute_quantity == int(compute_quantity) else compute_quantity,
            ram_gb=int(ram_gb) if ram_gb == int(ram_gb) else ram_gb,
            ram_mb=int(ram_gb * 1024),
        )

        citation = CompositeCitation(
            constituents=[
                CompositeCitationEntry(
                    kind="rate",
                    rate_unit=compute_row["rate_unit"],
                    rate=compute_rate,
                    quantity=compute_quantity,
                    contribution_usd=compute_contribution,
                    source_url=compute_row["source_url"],
                    store_path=compute_row["store_path"],
                    json_path=compute_row["json_path"],
                    fetched_at=fetched_at,
                    age_hours=age_hours,
                ),
                CompositeCitationEntry(
                    kind="rate",
                    rate_unit=ram_row["rate_unit"],
                    rate=ram_rate,
                    quantity=ram_gb,
                    contribution_usd=ram_contribution,
                    source_url=ram_row["source_url"],
                    store_path=ram_row["store_path"],
                    json_path=ram_row["json_path"],
                    fetched_at=fetched_at,
                    age_hours=age_hours,
                ),
            ],
            rule=f"flex_rules.{provider}.{flex_family}",
            formula="vcpu_quantity * compute_rate + ram_gb * ram_rate",
        )

        results.append(
            CompareResult(
                provider=provider,
                instance_type=name,
                region_native=str(region_native),
                vcpu_actual=vcpu,
                ram_gb_actual=ram_gb,
                hourly_usd=hourly,
                monthly_usd=monthly,
                considered_count=1,  # rewritten below after we know the total
                citation=citation,
                synthesized=True,
            )
        )

    # All synthesized candidates carry the same considered_count = number of
    # families that validated. The caller picks the cheapest representative; we
    # want considered_count to convey breadth-of-search, not per-row trivia.
    for r in results:
        r.considered_count = len(results)
    return results


def _first_rate(group: pl.DataFrame, units: tuple[str, ...]) -> dict[str, Any] | None:
    sub = group.filter(pl.col("rate_unit").is_in(list(units)))
    if sub.is_empty():
        return None
    return sub.row(0, named=True)


def _validate_ask(rule: dict[str, Any], vcpu: int, ram_gb: float) -> bool:
    if vcpu < rule["vcpu_min"] or vcpu > rule["vcpu_max"]:
        return False
    step = int(rule.get("vcpu_step", 1))
    if step > 1:
        # Allow vcpu == vcpu_min even if step would otherwise exclude it (matches
        # GCP convention that the smallest size is always valid).
        if vcpu != rule["vcpu_min"] and (vcpu - rule["vcpu_min"]) % step != 0:
            return False
    units = vcpu / float(rule["vcpu_per_unit"])
    if units <= 0:
        return False
    ratio = ram_gb / units
    ram_min = rule["ram_per_unit_gb"]["min"]
    ram_max = rule["ram_per_unit_gb"]["max"]
    if ratio < ram_min or ratio > ram_max:
        return False
    return True


@lru_cache(maxsize=1)
def _load_flex_rules() -> dict[str, Any]:
    doc = json.loads(FLEX_RULES_PATH.read_text())
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def _filter_instance_rows(
    *,
    df: pl.DataFrame,
    region: str,
    family: str,
    vcpu: int,
    ram_gb: float,
    provider: str,
) -> pl.DataFrame:
    """Apply the closest-larger filter for one provider's instance rows."""
    region_filter = (pl.col("region_canonical") == region) | (pl.col("region_native") == region)
    out = df.filter(
        (pl.col("provider") == provider)
        & (pl.col("row_kind") == "instance")
        & region_filter
        & (pl.col("vcpu").is_not_null())
        & (pl.col("ram_gb").is_not_null())
        & (pl.col("vcpu") >= vcpu)
        & (pl.col("ram_gb") >= ram_gb)
    )
    if family != ANY_FAMILY:
        out = out.filter(pl.col("family") == family)
    return out


def _build_citation(row: dict[str, Any], receipt: dict[str, Any]) -> CitationBlock:
    fetched_at = receipt.get("fetched_at", "")
    age_hours = _age_hours(fetched_at)
    return CitationBlock(
        source_url=row.get("source_url", ""),
        store_path=row.get("store_path", ""),
        json_path=row.get("json_path", ""),
        fetched_at=fetched_at,
        age_hours=age_hours,
    )


def _age_hours(fetched_at: str) -> float:
    if not fetched_at:
        return float("nan")
    parsed = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - parsed).total_seconds() / 3600


def _read_receipt(snapshot_dir: Path) -> dict[str, Any]:
    receipt_path = snapshot_dir / "receipt.json"
    if not receipt_path.exists():
        return {}
    return json.loads(receipt_path.read_text())


def _candidate_brief(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "instance_type": row["instance_type"],
        "vcpu":          int(row["vcpu"]) if row.get("vcpu") is not None else None,
        "ram_gb":        float(row["ram_gb"]) if row.get("ram_gb") is not None else None,
        "region_native": row["region_native"],
        "hourly_usd":    row.get("hourly_usd"),
        "monthly_usd":   row.get("monthly_usd"),
    }
