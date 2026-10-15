"""The schema fingerprint sees field names, not data used as keys."""

from __future__ import annotations

import json

from normalize.indexing.fingerprint import diff, fingerprint, read, write


def offer(skus: list[str], *, price_field: str = "pricePerUnit") -> dict:
    return {
        "products": {sku: {"sku": sku, "attributes": {"instanceType": "m5.large", "vcpu": "2"}} for sku in skus},
        "terms": {
            "OnDemand": {
                sku: {f"{sku}.JRTCKXETXF": {"priceDimensions": {f"{sku}.JRTCKXETXF.6YS6EN2CT7": {price_field: {"USD": "0.1"}}}}}
                for sku in skus
            }
        },
    }


def test_sku_keyed_maps_collapse_so_new_skus_are_not_drift():
    may = fingerprint(offer(["4AQFBS47E7Y26RTA", "9ZZFBS47E7Y26RTB"]))
    october = fingerprint(offer(["7QQFBS47E7Y26RTC", "8RRFBS47E7Y26RTD", "1AAFBS47E7Y26RTE"]))

    assert may == october
    assert ["$.products", "map"] in may
    assert ["$.products.{*}.attributes.vcpu", "string"] in may
    assert not any("4AQFBS47E7Y26RTA" in path for path, _ in may)


def test_a_renamed_field_under_a_map_is_drift():
    before = fingerprint(offer(["4AQFBS47E7Y26RTA"]))
    after = fingerprint(offer(["4AQFBS47E7Y26RTA"], price_field="unitPrice"))

    change = diff(before, after)

    assert any("unitPrice" in path for path, _ in change["added"])
    assert any("pricePerUnit" in path for path, _ in change["removed"])


def test_large_objects_are_maps_whatever_their_keys():
    doc = {"by_name": {f"plan_{chr(97 + n % 26)}{n}": {"price": 1} for n in range(100)}}

    fp = fingerprint(doc)

    assert ["$.by_name", "map"] in fp and len(fp) < 10


def test_a_fingerprint_from_another_walker_version_is_ignored(tmp_path):
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"version": 1, "entries": [["$.x", "string"]]}))
    current = tmp_path / "current.json"
    write([["$.x", "string"]], current)

    assert read(old) is None
    assert read(current) == [["$.x", "string"]]
