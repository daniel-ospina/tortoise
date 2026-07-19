"""Tests for event_log — append, replay, hash, crash recovery.

Covers: dedup, SHA-256 integrity, scan_incomplete, recover.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from shared_state.events import register_event_type
    from shared_state.event_log import (
        append_event,
        replay_events,
        verify_hash,
        scan_incomplete,
        recover,
    )
except ModuleNotFoundError:
    pytest.skip("shared_state package not installed — event log tests require it", allow_module_level=True)


@pytest.fixture(autouse=True)
def _register_types():
    """Ensure event types are registered before each test."""
    for t in (
        "card_created", "step_started", "step_completed",
        "gate_passed", "gate_blocked", "card_completed", "card_failed",
    ):
        try:
            register_event_type(t)
        except ValueError:
            pass  # already registered


@pytest.fixture
def tmp_log():
    d = Path(tempfile.mkdtemp())
    log = d / "events.jsonl"
    yield log
    import shutil
    shutil.rmtree(d, ignore_errors=True)


class TestAppendAndReplay:
    def test_append_single_event(self, tmp_log):
        assert append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        events = replay_events(tmp_log)
        assert len(events) == 1
        assert events[0]["type"] == "card_created"
        assert events[0]["card_id"] == "c1"
        assert events[0]["event_id"] == "ev-1"

    def test_dedup_by_event_id(self, tmp_log):
        append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        # Second call with same event_id should be idempotent
        assert append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        events = replay_events(tmp_log)
        assert len(events) == 1

    def test_dedup_different_event_ids(self, tmp_log):
        append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-2")
        events = replay_events(tmp_log)
        assert len(events) == 2

    def test_multiple_event_types(self, tmp_log):
        append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        append_event(tmp_log, "step_started", {"card_id": "c1", "step_id": "s1"}, event_id="ev-2")
        append_event(tmp_log, "step_completed", {"card_id": "c1", "step_id": "s1"}, event_id="ev-3")
        events = replay_events(tmp_log)
        assert len(events) == 3
        types = [e["type"] for e in events]
        assert types == ["card_created", "step_started", "step_completed"]

    def test_no_event_id_no_dedup(self, tmp_log):
        append_event(tmp_log, "card_created", {"card_id": "c1"})
        append_event(tmp_log, "card_created", {"card_id": "c1"})
        events = replay_events(tmp_log)
        assert len(events) == 2  # no event_id = no dedup


class TestIntegrity:
    def test_verify_hash_empty_log(self, tmp_log):
        ok, h = verify_hash(tmp_log)
        assert ok
        assert len(h) == 64  # SHA-256 hex

    def test_verify_hash_deterministic(self, tmp_log):
        append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        ok1, h1 = verify_hash(tmp_log)
        ok2, h2 = verify_hash(tmp_log)
        assert h1 == h2  # same content → same hash

    def test_verify_hash_changes_with_content(self, tmp_log):
        append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        _, h1 = verify_hash(tmp_log)
        append_event(tmp_log, "card_completed", {"card_id": "c1"}, event_id="ev-2")
        _, h2 = verify_hash(tmp_log)
        assert h1 != h2  # different content → different hash

    def test_verify_hash_with_expected(self, tmp_log):
        append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        _, h = verify_hash(tmp_log)
        ok, _ = verify_hash(tmp_log, expected_sha256=h)
        assert ok

    def test_verify_hash_wrong_expected(self, tmp_log):
        append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        ok, _ = verify_hash(tmp_log, expected_sha256="0" * 64)
        assert not ok


class TestCrashRecovery:
    def test_scan_incomplete_all_complete(self, tmp_log):
        append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        append_event(tmp_log, "card_completed", {"card_id": "c1"}, event_id="ev-2")
        assert scan_incomplete(tmp_log, {"c1"}) == []

    def test_scan_incomplete_missing(self, tmp_log):
        append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        append_event(tmp_log, "card_completed", {"card_id": "c1"}, event_id="ev-2")
        append_event(tmp_log, "card_created", {"card_id": "c2"}, event_id="ev-3")
        # c2 has no completion
        assert scan_incomplete(tmp_log, {"c1", "c2"}) == ["c2"]

    def test_scan_incomplete_failed_card(self, tmp_log):
        append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        append_event(tmp_log, "card_failed", {"card_id": "c1"}, event_id="ev-2")
        # card_failed is also a completion
        assert scan_incomplete(tmp_log, {"c1"}) == []

    def test_recover_no_events(self, tmp_log):
        def retry(cid, last_step):
            return {"card_id": cid, "last_step": last_step}

        result = recover(tmp_log, "c1", retry)
        assert result["card_id"] == "c1"
        assert result["last_step"] is None

    def test_recover_from_last_completed_step(self, tmp_log):
        append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        append_event(tmp_log, "step_started", {"card_id": "c1", "step_id": "s1"}, event_id="ev-2")
        append_event(tmp_log, "step_completed", {"card_id": "c1", "step_id": "s1"}, event_id="ev-3")

        def retry(cid, last_step):
            return {"card_id": cid, "last_step": last_step}

        result = recover(tmp_log, "c1", retry)
        assert result["last_step"] == "s1"

    def test_recover_ignores_other_cards(self, tmp_log):
        append_event(tmp_log, "card_created", {"card_id": "c1"}, event_id="ev-1")
        append_event(tmp_log, "step_completed", {"card_id": "c1", "step_id": "s1"}, event_id="ev-2")
        append_event(tmp_log, "card_created", {"card_id": "c2"}, event_id="ev-3")
        append_event(tmp_log, "step_completed", {"card_id": "c2", "step_id": "other"}, event_id="ev-4")

        def retry(cid, last_step):
            return {"card_id": cid, "last_step": last_step}

        result = recover(tmp_log, "c1", retry)
        assert result["last_step"] == "s1"  # c1's step, not c2's
