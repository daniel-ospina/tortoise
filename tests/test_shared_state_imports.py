"""Tests for the tortoise.shared_state package's public API.

#4221: this assertion lived in `tortoise/shared_state/tests/__init__.py`,
which pytest never collects (only `test_*.py` files match). Moved here so it
runs like every other test.
"""
from __future__ import annotations


def test_shared_state_imports():
    import tortoise.shared_state as shared_state
    assert hasattr(shared_state, "locked_append")
    assert hasattr(shared_state, "atomic_claim")
    assert hasattr(shared_state, "EventCodec")
    assert hasattr(shared_state, "register_event_type")
    assert hasattr(shared_state, "scan_incomplete_cards")
    assert hasattr(shared_state, "dedup_events")
    assert hasattr(shared_state, "find_last_checkpoint")
    assert hasattr(shared_state, "GoldenSignals")
    assert hasattr(shared_state, "collect_signals")
