"""Unit tests for the panel-grounding view-context channel (S8).

The current dashboard view forwarded by the client is UNTRUSTED. It is
re-validated through CompareQueryArgs and dropped if malformed; valid views
become a structurally-constrained, trust-zone-wrapped grounding Turn.
"""

from __future__ import annotations

from api.assistant_transport.state import view_context_turn


def _view(**over: object) -> dict:
    base: dict[str, object] = {
        "vcpu": 4,
        "ram_gb": 8,
        "family": "general-purpose",
        "region": "eu-central",
    }
    base.update(over)
    return {"currentView": base}


def test_valid_view_builds_wrapped_grounding_turn():
    turn = view_context_turn(_view())
    assert turn is not None
    assert turn.role == "user"
    # dedicated trust-zone tag, not the user-request zone
    assert turn.content.startswith("<current_view_context>")
    assert turn.content.rstrip().endswith("</current_view_context>")
    assert "4 vCPU" in turn.content
    assert "general-purpose" in turn.content
    assert "eu-central" in turn.content
    # instructs the agent to still issue its own compare call so quoted prices
    # stay bound to a validated tool result (ADR-0013)
    assert "compare tool call" in turn.content


def test_malformed_family_is_dropped():
    # the mock's old short label is not a backend FamilyName literal
    assert view_context_turn(_view(family="compute-opt")) is None


def test_region_injection_is_dropped():
    # plain-selector forbids / \ .. < > — an injection attempt never grounds
    assert view_context_turn(_view(region="eu<inject>")) is None
    assert view_context_turn(_view(region="../etc")) is None


def test_out_of_bounds_spec_is_dropped():
    assert view_context_turn(_view(vcpu=0)) is None
    assert view_context_turn(_view(ram_gb=-1)) is None


def test_missing_or_nondict_inputs_drop_to_none():
    assert view_context_turn(None) is None
    assert view_context_turn({}) is None
    assert view_context_turn({"currentView": "nope"}) is None
    assert view_context_turn({"currentView": {"vcpu": 4}}) is None
