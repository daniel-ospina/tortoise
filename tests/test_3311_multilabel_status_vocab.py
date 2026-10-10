"""#3311 — a status write on a node carrying the `:Point` label must be
validated and journaled through the generic entity surface.

A node can carry two canonical labels at once — the `:Point:Object` shape.
This surface had no Object status guard to escape: ``_update_entity`` applied
the caller's props as given, and the ``:Point`` arm matched first. What
distinguished the arms was JOURNALING, not validation — the ``:Point`` arm
wrote with a live ``SET n += $p`` and emitted no state record, while the
``:Object`` arm journaled a ``restatus`` against ITS label. Measured on
``origin/main`` via the documented tenant route ``update_entity(id, status=...)``:

    status='live'                 ACCEPTED  stored='live'
    status='draft'                ACCEPTED  stored='draft'
    status='not-a-status-word'    ACCEPTED  stored='not-a-status-word'
    status='deprecated'           ACCEPTED  stored='deprecated'
    status=''                     ACCEPTED  stored=''

Two defects:

  1. **No vocabulary validation.** Any text was written, including ``''`` and
     the legacy ``'deprecated'`` (deliberately outside ``POINT_STATUS_VALUES``
     — ``tortoise/live.py``'s ``LEGACY_NON_CURRENT_STATUS_VALUES``). The
     accepted vocabulary was never consulted for the labels the node carries.
  2. **No replayable journal line.** The ``:Point`` arm wrote with a live
     ``SET n += $p`` and emitted no state record; the ``:Object`` arm journaled
     a ``restatus`` against ITS label. No production path mints a
     ``:Point:Object``, so the node re-materializes as ``:Point`` and that
     record cannot fold — ``rebuild_all`` refused with ``state-op-miss`` and
     the status reverted to ``'draft'``.

The fix routes the write through the journaled lifecycle path — the ONE
``EntityMutated`` builder ``_journal_entity_mutation(..., "restatus")``, which
already handles the op — rather than adding a union vocabulary. A union
vocabulary alone would close defect 1 and leave defect 2: the requirement to
journal is what decides the mechanism.

Runnable (docker lane):
    PYTHONPATH=$PWD TORTOISE_DB_URI='docker://:falkordb@localhost:6379/tortoise_t3311' \\
        .venv/bin/python -m pytest tests/test_3311_multilabel_status_vocab.py -q
"""
from __future__ import annotations

import pytest

from tortoise.log import EventLog
from tortoise.sdk import TortoiseSDK

_REC = "EntityMutated"


@pytest.fixture
def env(tmp_path):
    """``(sdk, events_dir)`` with the JSONL journal wired."""
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(str(tmp_path / "ml3311.db"),
                      event_log_path=str(events / "events.jsonl"))
    yield sdk, events
    sdk.close()


def _multilabel_point(sdk, *, status: str = "draft") -> str:
    """Mint a `:Point:Object` node.

    No production path creates one (the design plan says so and the repository
    confirms it: only hand-written test fixtures do), so the shape is built the
    only way it can be — create a Point, then add the sibling label.
    """
    pid = sdk.create_point("statement", "multi-label claim",
                           dedup=False, status=status)["id"]
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n:Object", params={"id": pid})
    return pid


def _labels(sdk, pid: str) -> set[str]:
    return set(sdk._get_proj().g.query(
        "MATCH (n {id:$id}) RETURN labels(n)", params={"id": pid}
    ).result_set[0][0])


def _status(sdk, pid: str):
    return sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.status", params={"id": pid}
    ).result_set[0][0]


def _mutations(events, pid: str) -> list[dict]:
    return [e for e in EventLog(str(events / "events.jsonl")).read_all()
            if e.get("type") == _REC and e.get("id") == pid]


# ── Acceptance 1: an unrecognised status is refused, and writes nothing ───

@pytest.mark.parametrize("bad", ["not-a-status-word", "", "deprecated", None])
def test_unrecognised_status_is_refused_and_writes_nothing(env, bad):
    """``''`` and the legacy ``'deprecated'`` are not vocabulary values.

    ``deprecated`` is deliberately absent from ``POINT_STATUS_VALUES`` (only
    legacy graphs carry it), so the generic surface must refuse it exactly as
    it refuses arbitrary text.
    """
    sdk, events = env
    pid = _multilabel_point(sdk)
    assert _labels(sdk, pid) == {"Point", "Object"}, \
        "the fixture must build the multi-label shape or this test is vacuous"
    before = _mutations(events, pid)

    with pytest.raises(ValueError, match="Invalid status"):
        sdk.update_entity(pid, status=bad)

    assert _status(sdk, pid) == "draft", (
        "a refused status must not be written — validation must precede the "
        "write")
    assert _mutations(events, pid) == before, \
        "a refused status must not be journaled"


# ── Acceptance 2: the write is journaled and survives a rebuild ───────────

def test_valid_status_is_journaled_against_point_and_survives_rebuild(env):
    """The record must name the label the node REPLAYS as.

    A ``restatus`` against the sibling ``:Object`` label cannot fold: the node
    is re-materialized as ``:Point`` (no production path mints the second
    label), so ``rebuild_all`` refuses with ``state-op-miss`` and the status
    reverts. Journaling against ``:Point`` is what makes the change durable.
    """
    sdk, events = env
    pid = _multilabel_point(sdk)

    sdk.update_entity(pid, status="archived")
    assert _status(sdk, pid) == "archived"

    recs = _mutations(events, pid)
    assert [r["op"] for r in recs] == ["restatus"], \
        f"expected exactly one restatus record, got {recs!r}"
    assert recs[0]["label"] == "Point", (
        "the record must name the label the node replays as; a record against "
        "the sibling :Object label cannot fold and fails the rebuild")
    assert recs[0]["state"] == {"status": "archived"}

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _status(sdk, pid) == "archived", (
        "the status change did not survive rebuild_all (defect 2)")


def test_reference_fold_agrees_with_the_journaled_point_status(env):
    """The pure/in-memory reference fold must fold the new record too.

    ``check_consistency`` compares the graph against a replay through
    ``_apply_one``/``fold``. A record the graph fold applies but the reference
    fold ignores makes it report a content divergence on a faithful journal
    (#330 parity; #5048's rule that every graph-folded write also rides
    ``_apply_one``) — so this pins the reference arm, not just ``rebuild_all``.
    """
    from tortoise.consistency import check_consistency

    sdk, events = env
    pid = _multilabel_point(sdk)
    sdk.update_entity(pid, status="archived")

    result = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert result["ok"], (
        "the reference fold disagrees with the graph on a faithful journal: "
        f"{result.get('divergent_points')}")


# ── #3311 review P1: the journal's LAST status writer must survive replay ─

def test_status_write_after_supersede_wins_on_rebuild(env):
    """A status write the journal places AFTER a supersede must survive.

    ``_fold_point_superseded`` writes ``status='superseded'`` UNCONDITIONALLY
    from the deferred pass-1b sweep, which ran after the inline ``EntityMutated``
    ``restatus`` fold — so a later status write was reverted on ``rebuild_all``
    while live and the chronological ``apply()``/``check_consistency`` arm kept
    it. The point-side twin of the #4743 Object re-fold: the re-fold must run
    AFTER the point sweep and be gated on the supersede's journal seq.
    """
    from tortoise.consistency import check_consistency

    sdk, events = env
    old = sdk.create_point("statement", "old claim", dedup=False,
                           status="live")["id"]
    new = sdk.create_point("statement", "new claim", dedup=False,
                           status="live")["id"]
    sdk.supersede_point(old, new)
    sdk.update_entity(old, status="archived")
    assert _status(sdk, old) == "archived"

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _status(sdk, old) == "archived", (
        "the deferred PointSuperseded sweep clobbered a LATER journaled status "
        "write — live/replay divergence")

    result = check_consistency(str(events / "events.jsonl"), sdk._get_proj())
    assert result["ok"], result.get("divergent_points")


def test_supersede_after_status_write_still_ends_superseded(env):
    """The reverse order must NOT be inverted by the parity fix.

    A status write journalled BEFORE the supersede is the OLDER writer, so the
    deferred supersede fold legitimately wins. A fix that re-folded every state
    op (or that always re-applied the inline value) would flip this to the
    stale status — this is the guard that the ordering fix is a comparison and
    not an inversion.
    """
    sdk, events = env
    old = sdk.create_point("statement", "old claim", dedup=False,
                           status="live")["id"]
    new = sdk.create_point("statement", "new claim", dedup=False,
                           status="live")["id"]
    sdk.update_entity(old, status="draft")   # non-terminal: supersede is legal
    sdk.supersede_point(old, new)
    assert _status(sdk, old) == "superseded"

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _status(sdk, old) == "superseded", (
        "a supersede journalled AFTER the status write must still terminalize "
        "the point on replay")


def test_supersede_status_then_delete_rebuild_does_not_miss(env):
    """The re-fold must not manufacture a fold-miss for a deleted Point.

    ``create -> supersede -> update(status) -> delete``: the delete folds
    inline, so by the time the point re-fold runs the node is gone. Re-folding
    the status op then can only emit a spurious ``state-op-miss`` and fail the
    run (``NonFoldedEventsError``) for a journal every engine replays
    correctly — the same round-6 trap the Object re-fold's existence guard
    exists for. Pins that guard.
    """
    sdk, events = env
    old = sdk.create_point("statement", "old claim", dedup=False,
                           status="live")["id"]
    new = sdk.create_point("statement", "new claim", dedup=False,
                           status="live")["id"]
    sdk.supersede_point(old, new)
    sdk.update_entity(old, status="archived")
    sdk.delete(old)

    # Must not raise: the deleted Point's later status op is legitimately
    # unfolded (there is no node left), not a reconciliation failure.
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN count(n)",
        params={"id": old}).result_set[0][0] == 0, \
        "a hard-deleted point must not reappear on replay"


# ── The vocabulary values stay writable (owner ruling) ───────────────────

@pytest.mark.parametrize("good", ["draft", "live", "outdated", "archived"])
def test_vocabulary_values_are_accepted_and_journaled(env, good):
    """The fix must refuse only NON-vocabulary values.

    The owner's state-model ruling on #3311 (comment 6091597170) requires
    ``draft`` to remain an explicit, honoured choice, and #2977 records
    ``update_entity(<:Point:Object>, status='draft'|'outdated')`` as a working
    public call. A tightening to ``update_point``'s draft→live rule would be a
    behaviour change, not this fix.
    """
    sdk, events = env
    pid = _multilabel_point(sdk)
    sdk.update_entity(pid, status=good)
    assert _status(sdk, pid) == good
    recs = _mutations(events, pid)
    assert [r["op"] for r in recs] == ["restatus"]
    assert recs[0]["label"] == "Point", (
        "a sibling-label record cannot replay; this assertion makes the test "
        "discriminate the fix from the old :Object record")
    assert recs[0]["state"] == {"status": good}


# ── Acceptance 4: the dedicated point guard is untouched ─────────────────

def test_update_point_still_only_promotes_draft_to_live(env):
    """The generic-surface fix must not move ``update_point``'s own guard."""
    sdk, _ = env
    pid = _multilabel_point(sdk, status="draft")

    sdk.update_point(pid, status="live")
    assert _status(sdk, pid) == "live"

    for bad in ("archived", "retracted", "not-a-status-word", "", "deprecated"):
        with pytest.raises(ValueError, match=r"Invalid status|only promotes"):
            sdk.update_point(pid, status=bad)
