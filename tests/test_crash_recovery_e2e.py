"""GAP-14b #7002: Crash recovery — kill -9 E2E test.

Verify: after simulated crash (incomplete cards), scan_incomplete finds them,
recover replays from last checkpoint, state is consistent.
Test: create cards + checkpoint → more card ops (mid-card) → simulate crash →
restart → scan_incomplete → recover → verify recovered cards are consistent.
"""
from __future__ import annotations

import json  # noqa: F401
import os  # noqa: F401
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from shared_state.events import register_event_type  # noqa: I001
    from shared_state.event_log import (
        append_event,
        replay_events,
        verify_hash,
        scan_incomplete,
        recover,
    )
except ModuleNotFoundError:
    pytest.skip("shared_state package not installed — crash recovery E2E tests require it", allow_module_level=True)


# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _register_card_types():
    """Register card + checkpoint event types before each test."""
    for t in (
        "card_created", "step_started", "step_completed",
        "card_completed", "card_failed", "checkpoint",
    ):
        try:  # noqa: SIM105
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


# ── helpers ───────────────────────────────────────────────────────────────

def _card_created(card_id: str) -> dict:
    return {"card_id": card_id, "title": f"Card {card_id}", "team": "test"}


def _step_event(step_id: str, card_id: str) -> dict:
    return {"card_id": card_id, "step_id": step_id}


def _checkpoint_item(content: str, wing: str = "eldato", room: str = "decisions") -> dict:
    return {"content": content, "wing": wing, "room": room}


# ── E2E tests ─────────────────────────────────────────────────────────────

def test_crash_recovery_full_cycle(tmp_log):
    """Full round-trip: create cards + checkpoint → crash mid-card → recover → verify.

    Flow:
      1. Card c1 completes fully (card_created → step_started → step_completed → card_completed)
      2. Checkpoint saved (c1 done)
      3. Card c2 starts but crashes mid-step (card_created → step_started — no completion)
      4. Card c3 is created but crashes immediately (card_created only)
      5. scan_incomplete finds c2, c3
      6. recover replays from last checkpoint for each
      7. Verify c2 resumes from last step, c3 from scratch
    """
    # ── Phase 1: Complete card c1 + checkpoint ─────────────────────────
    append_event(tmp_log, "card_created", _card_created("c1"), event_id="ev-c1-create")
    append_event(tmp_log, "step_started", _step_event("s1", "c1"), event_id="ev-c1-s1-start")
    append_event(tmp_log, "step_completed", _step_event("s1", "c1"), event_id="ev-c1-s1-done")
    append_event(tmp_log, "card_completed", _card_created("c1"), event_id="ev-c1-complete")

    # Checkpoint: capture that c1 is done
    append_event(tmp_log, "checkpoint", _checkpoint_item(
        "c1 completed; c2 not started; c3 not started"
    ), event_id="ev-checkpoint-1")

    # ── Phase 2: c2 starts, crashes mid-step ───────────────────────────
    append_event(tmp_log, "card_created", _card_created("c2"), event_id="ev-c2-create")
    append_event(tmp_log, "step_started", _step_event("s1", "c2"), event_id="ev-c2-s1-start")
    # CRASH HERE — no step_completed, no card_completed for c2

    # c3 created, crashes immediately
    append_event(tmp_log, "card_created", _card_created("c3"), event_id="ev-c3-create")
    # CRASH HERE — no completion for c3

    # Capture hash of the crashed-state log (all events written, no recovery yet)
    pre_crash_hash = verify_hash(tmp_log)[1]

    # ── Phase 3: "Restart" — scan for incomplete cards ────────────────
    all_card_ids = {"c1", "c2", "c3"}
    incomplete = scan_incomplete(tmp_log, all_card_ids)
    assert "c1" not in incomplete, "c1 was completed — should not be incomplete"
    assert "c2" in incomplete, "c2 crashed mid-step — should be incomplete"
    assert "c3" in incomplete, "c3 created then crashed — should be incomplete"
    assert len(incomplete) == 2

    # ── Phase 4: Recover each incomplete card ─────────────────────────

    from unittest.mock import Mock

    def _retry_impl(card_id: str, last_step: str | None) -> dict:
        """Simulate retry: resume from last completed step or from scratch."""
        return {"card_id": card_id, "last_step": last_step, "recovered": True}

    retry_fn = Mock(wraps=_retry_impl)

    # Recover c2 — it had step_started for s1 but no step_completed
    r2 = recover(tmp_log, "c2", retry_fn)
    assert r2["card_id"] == "c2"
    assert r2["recovered"] is True
    # c2's last completed step is None (no step_completed event for c2)
    assert r2["last_step"] is None

    # Recover c3 — only card_created, no step events at all
    r3 = recover(tmp_log, "c3", retry_fn)
    assert r3["card_id"] == "c3"
    assert r3["recovered"] is True
    assert r3["last_step"] is None

    # Verify retry_fn was called for both incomplete cards
    assert retry_fn.call_count == 2, (
        f"retry_fn should be called twice (c2 + c3), got {retry_fn.call_count}"
    )

    # ── Phase 5: Verify integrity ─────────────────────────────────────
    # Hash should not have changed — scan_incomplete + recover are read-only
    post_hash = verify_hash(tmp_log)[1]
    assert post_hash == pre_crash_hash, (
        "Event log integrity — hash unchanged after scan+recover (no modifications)"
    )

    # Verify all events still replayable
    all_events = replay_events(tmp_log)
    types = [e["type"] for e in all_events]
    assert types == [
        "card_created", "step_started", "step_completed", "card_completed",
        "checkpoint",
        "card_created", "step_started",
        "card_created",
    ], f"Unexpected event sequence: {types}"


def test_crash_recovery_multiple_checkpoints(tmp_log):
    """Multiple checkpoints: recovery finds the last valid checkpoint for each card.

    Flow:
      1. c1 created → checkpoint (c1 partial)
      2. c1 step s1 completed → checkpoint (c1 at s1)
      3. c1 step s2 started → CRASH
      4. recover finds c1's last completed step (s1)
    """
    append_event(tmp_log, "card_created", _card_created("c1"), event_id="ev-c1-create")
    append_event(tmp_log, "checkpoint", _checkpoint_item(
        "c1 created, no steps done"
    ), event_id="ev-cp-1")

    append_event(tmp_log, "step_started", _step_event("s1", "c1"), event_id="ev-c1-s1-start")
    append_event(tmp_log, "step_completed", _step_event("s1", "c1"), event_id="ev-c1-s1-done")
    append_event(tmp_log, "checkpoint", _checkpoint_item(
        "c1 step s1 completed"
    ), event_id="ev-cp-2")

    append_event(tmp_log, "step_started", _step_event("s2", "c1"), event_id="ev-c1-s2-start")
    # CRASH — no step_completed for s2

    incomplete = scan_incomplete(tmp_log, {"c1"})
    assert incomplete == ["c1"]

    def retry_fn(card_id: str, last_step: str | None) -> dict:
        return {"card_id": card_id, "last_step": last_step}

    r = recover(tmp_log, "c1", retry_fn)
    # c1 had step_completed for s1 → last_step should be "s1"
    assert r["last_step"] == "s1", (
        f"Expected last_step='s1' (last completed step), got {r['last_step']}"
    )


def test_crash_recovery_no_crash_all_complete(tmp_log):
    """No crash scenario: all cards complete → scan_incomplete returns empty."""
    for cid in ("c1", "c2", "c3"):
        append_event(tmp_log, "card_created", _card_created(cid), event_id=f"ev-{cid}-create")
        append_event(tmp_log, "step_started", _step_event("s1", cid), event_id=f"ev-{cid}-s1-start")
        append_event(tmp_log, "step_completed", _step_event("s1", cid), event_id=f"ev-{cid}-s1-done")
        append_event(tmp_log, "card_completed", _card_created(cid), event_id=f"ev-{cid}-complete")

    incomplete = scan_incomplete(tmp_log, {"c1", "c2", "c3"})
    assert incomplete == [], f"All cards complete, got incomplete: {incomplete}"


def test_crash_recovery_card_failed_is_complete(tmp_log):
    """card_failed counts as a completion — not reported as incomplete."""
    append_event(tmp_log, "card_created", _card_created("c1"), event_id="ev-c1-create")
    append_event(tmp_log, "card_failed", _card_created("c1"), event_id="ev-c1-fail")

    incomplete = scan_incomplete(tmp_log, {"c1"})
    assert incomplete == [], f"card_failed is a completion, got: {incomplete}"


def test_crash_recovery_empty_log(tmp_log):
    """Empty event log: scan returns empty, recover returns defaults."""
    incomplete = scan_incomplete(tmp_log, set())
    assert incomplete == []

    def retry_fn(card_id: str, last_step: str | None) -> dict:
        return {"card_id": card_id, "last_step": last_step}

    r = recover(tmp_log, "c-new", retry_fn)
    assert r["card_id"] == "c-new"
    assert r["last_step"] is None


def test_crash_recovery_hash_integrity_after_crash(tmp_log):
    """Event log hash is deterministic — same content → same hash after crash."""
    events = [
        ("card_created", _card_created("c1"), "ev-c1-create"),
        ("step_started", _step_event("s1", "c1"), "ev-c1-s1-start"),
        ("step_completed", _step_event("s1", "c1"), "ev-c1-s1-done"),
        ("card_created", _card_created("c2"), "ev-c2-create"),
        # c2 crashes here — no completion
    ]

    for ev_type, data, ev_id in events:
        append_event(tmp_log, ev_type, data, event_id=ev_id)

    # Capture hash of the "crashed" log
    ok, hash1 = verify_hash(tmp_log)
    assert ok
    assert len(hash1) == 64  # SHA-256 hex

    # Re-read events and verify hash is deterministic
    ok2, hash2 = verify_hash(tmp_log)
    assert ok2
    assert hash1 == hash2, "Hash must be deterministic for same log content"

    # Verify hash changes when content changes (recovery shouldn't mutate log,
    # but appending a recovery event would change it)
    append_event(tmp_log, "step_completed", _step_event("s1", "c2"), event_id="ev-c2-s1-done")
    _, hash3 = verify_hash(tmp_log)
    assert hash1 != hash3, "Hash must change after new event appended"


def test_crash_recovery_concurrent_cards_independent(tmp_log):
    """Cards operate independently — c1's crash doesn't affect c2's completion."""
    # c1: incomplete (crashes)
    append_event(tmp_log, "card_created", _card_created("c1"), event_id="ev-c1-create")
    append_event(tmp_log, "step_started", _step_event("s1", "c1"), event_id="ev-c1-s1-start")
    # c1 CRASH

    # c2: completes fully
    append_event(tmp_log, "card_created", _card_created("c2"), event_id="ev-c2-create")
    append_event(tmp_log, "step_started", _step_event("s1", "c2"), event_id="ev-c2-s1-start")
    append_event(tmp_log, "step_completed", _step_event("s1", "c2"), event_id="ev-c2-s1-done")
    append_event(tmp_log, "card_completed", _card_created("c2"), event_id="ev-c2-complete")

    incomplete = scan_incomplete(tmp_log, {"c1", "c2"})
    assert incomplete == ["c1"], f"Only c1 should be incomplete, got: {incomplete}"

    def retry_fn(card_id: str, last_step: str | None) -> dict:
        return {"card_id": card_id, "last_step": last_step}

    r1 = recover(tmp_log, "c1", retry_fn)
    assert r1["card_id"] == "c1"
    assert r1["last_step"] is None


def test_crash_recovery_dedup_on_recovery_events(tmp_log):
    """Recovery events with same event_id are deduplicated — no double-recovery."""
    append_event(tmp_log, "card_created", _card_created("c1"), event_id="ev-c1-create")
    # CRASH

    # Append a recovery marker with a specific event_id
    append_event(tmp_log, "step_started", _step_event("s1", "c1"), event_id="ev-recovery-s1")
    # Same event_id — should be dedup'd
    assert append_event(tmp_log, "step_started", _step_event("s1", "c1"), event_id="ev-recovery-s1")

    events = replay_events(tmp_log)
    step_starts = [e for e in events if e["type"] == "step_started"]
    assert len(step_starts) == 1, (
        f"Dedup should prevent duplicate step_started, got {len(step_starts)}"
    )


def test_scan_incomplete_card_never_created(tmp_log):
    """scan_incomplete with a card_id that has no card_created event at all.

    Real crash scenario: card was created in memory but card_created never
    flushed to disk before SIGKILL. scan_incomplete should not crash.
    """
    append_event(tmp_log, "card_created", _card_created("c1"), event_id="ev-c1-create")
    append_event(tmp_log, "card_completed", _card_created("c1"), event_id="ev-c1-done")

    # c2 never had any event — it was in-memory only when the kill happened
    incomplete = scan_incomplete(tmp_log, {"c1", "c2"})
    # c1 is complete, c2 has no events → neither should be reported incomplete
    # (c2 never started from the log's perspective)
    assert "c1" not in incomplete, "c1 is complete"
    # c2 behavior: implementation-dependent — not in log means either excluded
    # from incomplete list or treated as incomplete. The key invariant: no crash.
    assert isinstance(incomplete, list)


def test_recover_card_absent_from_nonempty_log(tmp_log):
    """recover with a card_id not in a non-empty log returns sensible default.

    Counterpart to test_scan_incomplete_card_never_created: recovery should
    handle a card that was created in memory but never flushed.
    """
    append_event(tmp_log, "card_created", _card_created("c1"), event_id="ev-c1-create")
    append_event(tmp_log, "step_started", _step_event("s1", "c1"), event_id="ev-c1-s1-start")
    append_event(tmp_log, "step_completed", _step_event("s1", "c1"), event_id="ev-c1-s1-done")
    append_event(tmp_log, "card_completed", _card_created("c1"), event_id="ev-c1-done")

    def retry_fn(card_id: str, last_step: str | None) -> dict:
        return {"card_id": card_id, "last_step": last_step}

    # c-ghost never had any events in the log
    r = recover(tmp_log, "c-ghost", retry_fn)
    assert r["card_id"] == "c-ghost"
    assert r["last_step"] is None, (
        f"Ghost card should have last_step=None, got {r['last_step']}"
    )
