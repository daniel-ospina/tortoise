"""#6239 — a ``PointPromoted`` after a ``PointSuperseded`` must fold to ONE
status on every replay engine.

The defect (measured on the review of PR #6071, head ``cb8122a84``):
``rebuild_all`` folds the supersede in its trailing pass-1b sweep, *after* the
inline ``PointPromoted`` ``_upsert``, so it kept ``status='superseded'``; the
chronological ``apply()``-based engines (``rebuild(EventLog)`` /
``recover_from_log`` / the backup JSONL restore) fold the supersede at its own
seq and then re-apply the later promote snapshot, whose ``_upsert`` re-livened
``status='live'``. Same "trailing sweep vs chronologically-inline" split #3305
closed for the terminalizer folds.

The second, live-REACHABLE ordering of the same root (measured on the review of
PR #7960, head ``f05b6e9bf``):
``create(status='live') -> supersede_point -> update_entity(status='draft')
-> promote_point`` — the ``#3311`` re-fold keys only on the id's surviving
supersede and compares only ``EntityMutated`` seqs, so it clobbered the later
``PointPromoted`` and ``rebuild_all`` produced a stale ``draft`` where live (and
the apply() arm) produced ``live``.

Decision (this suite is its cross-engine parity test): a supersede is
status-TERMINAL and live's promote emitters are gated on a ``draft``/status-less
Point, so a ``PointPromoted`` replayed over a status the promote could not have
overwritten is a status NO-OP — the RECORD still folds (content/embedding), only
the ``status`` column is withheld. Where a later ``restatus`` makes the promote
legal again live, the promote DOES write ``live``. Both engines share the one
predicate (``promote_status_applies``).

Runnable with:
  TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
      tests/test_6239_promote_after_supersede.py -q
"""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import pytest

from tortoise.consistency import check_consistency
from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sup(tmp_path):
    """(db, events_dir, sdk) with the journal wired."""
    db = os.path.join(str(tmp_path), "ps.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    yield db, events, sdk
    sdk.close()


def _rebuild(sdk, events_dir) -> None:
    sdk._get_proj().rebuild_all(str(events_dir), confirm_destructive=True)


def _apply_replay(sdk, events_dir) -> None:
    sdk._get_proj().rebuild(
        EventLog(str(events_dir / "events.jsonl")), confirm_destructive=True)


def _point_state(sdk, pid: str) -> dict:
    p = sdk.get_point(pid) or {}
    return {k: p.get(k) for k in
            ("status", "outdated", "content", "reviewed")}


def _consistency(sdk, events_dir) -> dict:
    return check_consistency(
        str(events_dir / "events.jsonl"), sdk._get_proj(), record_state=False)


def _append_raw_promote(sdk, events_dir, pid: str, *,
                        content: str = "PROMOTED", status: str = "live") -> None:
    """Journal a PASS 1b-faithful ``PointPromoted`` the way a raw producer
    would: the node's own ``get_point`` snapshot (so embedding/createdAt/… ride
    along) with ``content``/``status`` overridden. A snapshot stripped of the
    derived props would red ``check_consistency`` on ``embedding`` for reasons
    that have nothing to do with this issue."""
    snap = dict(sdk.get_point(pid) or {})
    snap["id"] = pid
    snap["content"] = content
    snap["status"] = status
    with open(events_dir / "events.jsonl", "a") as fh:
        fh.write(json.dumps({
            "event_id": sdk.ulid(),
            "ts": datetime.now(UTC).isoformat(),
            "type": "PointPromoted",
            "initiated_by": "raw-producer",
            "projection_version": 2,
            "point": snap,
        }) + "\n")


# ══════════════════════════════════════════════════════════════════════
# The shared gate
# ══════════════════════════════════════════════════════════════════════

def test_promote_status_applies_only_over_draft_or_absent():
    """The ONE predicate both engines share. ``None`` covers both "no status"
    (the #2256 promote-only journal) and "not materialized yet"; every other
    value must refuse the status write.

    Imported INSIDE the test on purpose: this module must COLLECT on the
    pre-#6239 tree, so the behavioural tests below can be measured failing
    against it without the new symbol aborting the whole file.
    """
    from tortoise.projection.entities import promote_status_applies

    assert promote_status_applies(None) is True
    assert promote_status_applies("draft") is True
    for terminal in ("superseded", "retracted", "archived", "", "live",
                     "deprecated"):
        assert promote_status_applies(terminal) is False, terminal


# ══════════════════════════════════════════════════════════════════════
# Ordering A — PointPromoted after PointSuperseded (raw-producer shape)
# ══════════════════════════════════════════════════════════════════════

def test_raw_promote_after_supersede_is_terminal_on_the_apply_arm(sup):
    """The issue's measured divergence: the apply() arm must NOT re-liven a
    Point that live's promote could never have touched. ``rebuild_all`` held
    ``superseded`` while ``rebuild(EventLog)`` produced ``live``."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "OLD", status="live")["id"]
    succ = sdk.create_point("statement", "SUCC", status="live")["id"]
    sdk.supersede_point(a, succ)
    _append_raw_promote(sdk, events, a)

    _apply_replay(sdk, events)
    post = _point_state(sdk, a)
    assert post["status"] == "superseded", (
        f"the apply() arm re-livened a promoted-after-superseded Point: {post}")
    assert post["outdated"] is True
    # The RECORD still folds: only the status column is withheld.
    assert post["content"] == "PROMOTED"


def test_raw_promote_after_supersede_agrees_across_engines(sup):
    """The cross-engine parity contract: ``rebuild_all`` and ``rebuild(EventLog)``
    must produce ONE state, and it must be a journal the reference fold accepts
    (``check_consistency`` green on the replayed graph)."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "OLD", status="live")["id"]
    succ = sdk.create_point("statement", "SUCC", status="live")["id"]
    sdk.supersede_point(a, succ)
    _append_raw_promote(sdk, events, a)

    _rebuild(sdk, events)
    via_all = _point_state(sdk, a)
    assert via_all["status"] == "superseded"
    assert via_all["content"] == "PROMOTED"
    rep_all = _consistency(sdk, events)
    assert rep_all["ok"] is True, rep_all.get("divergent_points")

    _apply_replay(sdk, events)
    via_apply = _point_state(sdk, a)
    rep_apply = _consistency(sdk, events)
    assert rep_apply["ok"] is True, rep_apply.get("divergent_points")
    assert via_apply == via_all, (
        f"apply() replay != rebuild_all: {via_apply} != {via_all}")


# ══════════════════════════════════════════════════════════════════════
# Ordering B — supersede → restatus(draft) → promote (LIVE-REACHABLE)
# ══════════════════════════════════════════════════════════════════════

def test_restatus_then_promote_after_supersede_matches_live(sup):
    """Every step here is a public SDK call, so live is the oracle: the
    ``restatus`` makes the promote legal again, and the promote re-livens the
    Point. Before #6239 ``rebuild_all``'s re-fold clobbered it to ``draft``."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "OLD", status="live")["id"]
    succ = sdk.create_point("statement", "SUCC", status="live")["id"]
    sdk.supersede_point(a, succ)
    sdk.update_entity(a, status="draft")
    promoted = sdk.promote_point(a)
    assert promoted["promoted"] is True

    live = _point_state(sdk, a)
    assert live["status"] == "live"
    rep_live = _consistency(sdk, events)
    assert rep_live["ok"] is True, rep_live.get("divergent_points")

    _rebuild(sdk, events)
    via_all = _point_state(sdk, a)
    rep_all = _consistency(sdk, events)
    assert rep_all["ok"] is True, rep_all.get("divergent_points")
    assert via_all == live, f"rebuild_all drifted from live: {via_all} != {live}"

    _apply_replay(sdk, events)
    via_apply = _point_state(sdk, a)
    assert via_apply == live, (
        f"apply() replay drifted from live: {via_apply} != {live}")


def test_later_restatus_after_the_promote_still_wins(sup):
    """Ordering within the re-fold must stay chronological: a ``restatus`` the
    journal places AFTER the promote is the LAST status writer and must win on
    both engines (the re-fold's whole reason to exist)."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "OLD", status="live")["id"]
    succ = sdk.create_point("statement", "SUCC", status="live")["id"]
    sdk.supersede_point(a, succ)
    sdk.update_entity(a, status="draft")
    sdk.promote_point(a)
    sdk.update_entity(a, status="archived")

    live = _point_state(sdk, a)
    assert live["status"] == "archived"

    _rebuild(sdk, events)
    via_all = _point_state(sdk, a)
    assert via_all == live, f"rebuild_all drifted from live: {via_all} != {live}"
    _apply_replay(sdk, events)
    via_apply = _point_state(sdk, a)
    assert via_apply == live, (
        f"apply() replay drifted from live: {via_apply} != {live}")


# ══════════════════════════════════════════════════════════════════════
# No over-correction
# ══════════════════════════════════════════════════════════════════════

def test_ordinary_draft_promote_still_reaches_live(sup):
    """A promote over the ``draft`` status live itself requires is untouched —
    the gate must not freeze a legitimate promote."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "DRAFT", status="draft")["id"]
    sdk.promote_point(a)
    live = _point_state(sdk, a)
    assert live["status"] == "live"

    _rebuild(sdk, events)
    assert _point_state(sdk, a) == live
    _apply_replay(sdk, events)
    assert _point_state(sdk, a) == live


def test_promote_before_the_supersede_is_still_superseded(sup):
    """The other order: a promote that PRECEDES the supersede is legal, and the
    supersede that follows is the last status writer — both engines keep it."""
    _, events, sdk = sup
    a = sdk.create_point("statement", "DRAFT", status="draft")["id"]
    succ = sdk.create_point("statement", "SUCC", status="live")["id"]
    sdk.promote_point(a)
    sdk.supersede_point(a, succ)

    live = _point_state(sdk, a)
    assert live["status"] == "superseded"

    _rebuild(sdk, events)
    assert _point_state(sdk, a) == live
    _apply_replay(sdk, events)
    assert _point_state(sdk, a) == live


def test_promote_only_journal_still_materializes_live(sup):
    """#2256: a ``PointPromoted`` can be a node's ONLY durable record (the
    capture lane writes the live graph without journaling the creation). The
    gate reads "no status" as promote-legal, so that replay still materializes
    the node ``live`` — this is the no-over-correction pin for ``None``."""
    _db, events, sdk = sup
    # A live snapshot to journal, taken from a throwaway Point in the same
    # graph so the payload is a real ``get_point`` shape.
    src = sdk.create_point("statement", "CAPTURED", status="draft")["id"]
    snap = dict(sdk.get_point(src) or {})
    snap["status"] = "live"
    snap["reviewed"] = True
    # The promote-only journal: NO PointAdded for this id.
    pid = "capture-only-6239"
    snap["id"] = pid
    with open(events / "events.jsonl", "w") as fh:
        fh.write(json.dumps({
            "event_id": sdk.ulid(),
            "ts": datetime.now(UTC).isoformat(),
            "type": "PointPromoted",
            "initiated_by": "capture",
            "projection_version": 2,
            "point": snap,
        }) + "\n")

    _rebuild(sdk, events)
    assert _point_state(sdk, pid)["status"] == "live"
    _apply_replay(sdk, events)
    assert _point_state(sdk, pid)["status"] == "live"
