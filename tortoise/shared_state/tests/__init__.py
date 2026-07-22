"""Tests for shared_state package import."""
from __future__ import annotations


def test_shared_state_imports():
    import shared_state
    assert hasattr(shared_state, "locked_append")
    assert hasattr(shared_state, "atomic_claim")
    assert hasattr(shared_state, "EventCodec")
    assert hasattr(shared_state, "register_event_type")
    assert hasattr(shared_state, "scan_incomplete_cards")
    assert hasattr(shared_state, "dedup_events")
    assert hasattr(shared_state, "find_last_checkpoint")
    assert hasattr(shared_state, "GoldenSignals")
    assert hasattr(shared_state, "collect_signals")
