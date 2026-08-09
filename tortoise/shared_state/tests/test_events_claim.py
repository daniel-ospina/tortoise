"""Tests for the #432 claim/graph event type registrations in EventCodec.

Registration only (plan-review P2): encode/decode wiring is deferred to the
first real upcaster task. ClaimStateChanged must NOT be registered
(plan-review P1) — every claim transition maps to one of the five concrete
types; challenged is derived from NAND-edge presence.
"""
from __future__ import annotations

import pytest

from tortoise.shared_state.events import (
    EventCodec,
    event_types,
    register_claim_event_types,
    register_event_type,
)

CLAIM_TYPES = ("PointAdded", "OperatorAdded", "PointRetracted", "PointSuperseded", "OperatorAnnotated")


@pytest.fixture(autouse=True)
def _clear_registry():
    """Isolate tests from global state (mirrors test_events.py)."""
    import tortoise.shared_state.events as _ev
    _ev._event_types.clear()


@pytest.fixture(autouse=True)
def _register_claims(_clear_registry):
    register_claim_event_types()


class TestClaimRegistration:
    def test_five_claim_types_registered_v1(self):
        types = event_types()
        for t in CLAIM_TYPES:
            assert t in types, f"{t} not registered"
            assert types[t] == 1, f"{t} should be version 1"

    def test_claim_state_changed_absent(self):
        """plan-review P1: ClaimStateChanged is NOT a registered type."""
        assert "ClaimStateChanged" not in event_types()

    def test_coexists_with_pm_types(self):
        """The claim types share the EventCodec registry, not a parallel set.

        (PM/card types are registered by the packs/PM domain in the eldato
        ecosystem; here we register one directly to prove coexistence.)"""
        register_event_type("cardCreated")
        types = event_types()
        assert "cardCreated" in types
        for t in CLAIM_TYPES:
            assert t in types


class TestClaimRoundTrip:
    def test_encode_decode_roundtrip_all_five(self):
        for t in CLAIM_TYPES:
            ev = EventCodec.encode(t, {"id": "p1", "note": "x"})
            assert ev["type"] == t
            assert ev["version"] == 1
            assert "timestamp" in ev
            decoded = EventCodec.decode(ev)
            assert decoded["type"] == t
            assert decoded["id"] == "p1"

    def test_encode_unregistered_claim_type_raises(self):
        with pytest.raises(KeyError, match="Unknown event type"):
            EventCodec.encode("ClaimStateChanged", {})

    def test_upcaster_chain_applies_on_old_version(self):
        """A future v2 upcaster migrates a v1 record of a claim type."""
        register_event_type("PointArchived_v2", upcasters=[lambda e: {**e, "migrated": True}])
        ev = EventCodec.encode("PointArchived_v2", {"id": "p1"})
        assert ev["version"] == 2
        old_v1 = {"type": "PointArchived_v2", "version": 1, "id": "p1"}
        decoded = EventCodec.decode(old_v1)
        assert decoded["version"] == 2
        assert decoded.get("migrated") is True

    def test_decode_unknown_type_passthrough(self):
        assert EventCodec.decode({"type": "WhateverLegacy", "x": 1}) == {"type": "WhateverLegacy", "x": 1}
