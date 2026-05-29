"""Citation wire translation, shared by the HTTP endpoints and the agent's tools.

The internal `store_path` (`store/<provider>/<ISO>/<file>`) is a filesystem path:
it means nothing to a browser, leaks the on-disk layout, and points at files up
to ~200 MB. At every boundary that leaves the process (the HTTP responses and the
agent tool results) it is dropped and replaced with a logical `snapshot` ref that
the excerpt endpoint can resolve lazily (ADR-0008). This is the one place that
translation lives; callers import it rather than re-implement it.
"""

from __future__ import annotations

from typing import Any


def wire_response(result: dict[str, Any]) -> dict[str, Any]:
    """Rewrite every citation in a query-layer response: drop store_path, add a
    snapshot ref. Returns a shallow copy; the query layer's dict is not mutated."""
    out = dict(result)
    if "results" in out:
        out["results"] = [_wire_result(r) for r in out["results"]]
    if out.get("result") is not None:
        out["result"] = _wire_result(out["result"])
    return out


def _wire_result(r: dict[str, Any]) -> dict[str, Any]:
    out = dict(r)
    if "citation" in out:
        out["citation"] = _wire_citation(out["citation"])
    return out


def _wire_citation(c: dict[str, Any]) -> dict[str, Any]:
    if "composite" in c:
        return {
            **{k: v for k, v in c.items() if k != "composite"},
            "composite": [_wire_entry(e) for e in c["composite"]],
        }
    return _wire_entry(c)


def _wire_entry(e: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in e.items() if k != "store_path"}
    ref = store_path_to_ref(e.get("store_path", ""))
    if ref is not None:
        out["snapshot"] = ref
    return out


def store_path_to_ref(store_path: str) -> dict[str, str] | None:
    """store/<provider>/<iso>/<filename> -> {provider, snapshot_iso, filename}."""
    parts = store_path.split("/")
    if len(parts) < 4 or parts[0] != "store":
        return None
    return {"provider": parts[1], "snapshot_iso": parts[2], "filename": parts[-1]}
