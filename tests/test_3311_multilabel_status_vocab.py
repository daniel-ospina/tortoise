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

    ⚠️ #9649: the fixture moved from ``archived`` to ``live`` because
    ``archived`` is not a DECLARED transition from ``draft``
    (``_ALLOWED_TRANSITIONS['draft']`` is ``{live, retracted, superseded}``;
    ONTOLOGY §5 records ``archived`` as *"reserved — no v1 write path"*), so the
    generic door now refuses it. The property this test owns is the LABEL the
    record names, not the value.
    """
    sdk, events = env
    pid = _multilabel_point(sdk)

    sdk.update_entity(pid, status="live")
    assert _status(sdk, pid) == "live"

    recs = _mutations(events, pid)
    assert [r["op"] for r in recs] == ["restatus"], \
        f"expected exactly one restatus record, got {recs!r}"
    assert recs[0]["label"] == "Point", (
        "the record must name the label the node replays as; a record against "
        "the sibling :Object label cannot fold and fails the rebuild")
    assert recs[0]["state"] == {"status": "live"}

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _status(sdk, pid) == "live", (
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
    sdk.update_entity(pid, status="live")   # #9649: a DECLARED transition

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

    ⚠️ **#9649: this shape is no longer MINTABLE through a live door.**
    ``update_entity`` refuses a lifecycle write on a terminal Point, because a
    ``superseded`` claim has no outgoing transition (``_ALLOWED_TRANSITIONS`` /
    ONTOLOGY §5) and re-statusing it resurrected a dead claim beside its closed
    window. The journal shape still EXISTS — every graph written before #9649
    holds it — and the fold-ordering property below is unchanged, so the
    fixture is built the way a pre-#9649 writer left it: the graph value AND the
    ``restatus`` record. This remains a REPLAY pin; the door's refusal is pinned
    in ``tests/test_9649_update_entity_point_lifecycle.py``.
    """
    from tortoise.consistency import check_consistency

    sdk, events = env
    old = sdk.create_point("statement", "old claim", dedup=False,
                           status="live")["id"]
    new = sdk.create_point("statement", "new claim", dedup=False,
                           status="live")["id"]
    sdk.supersede_point(old, new)
    # The pre-#9649 live write, reproduced: graph first, record second (this
    # door's own order), with the refusal replaced by the write it used to do.
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status=$st",
        params={"id": old, "st": "archived"})
    sdk._journal_entity_mutation("Point", old, "restatus",
                                 state={"status": "archived"})
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
                           status="draft")["id"]
    new = sdk.create_point("statement", "new claim", dedup=False,
                           status="live")["id"]
    # #9649: a DECLARED transition (draft → live). The pre-#9649 fixture used
    # live → draft, which ONTOLOGY §5 does not declare and the door now refuses.
    sdk.update_entity(old, status="live")
    sdk.supersede_point(old, new)
    assert _status(sdk, old) == "superseded"

    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert _status(sdk, old) == "superseded", (
        "a supersede journalled AFTER the status write must still terminalize "
        "the point on replay")


def test_supersede_status_then_delete_rebuild_does_not_miss(env):
    """The re-fold must not manufacture a fold-miss for a deleted Point.

    ``create -> supersede -> restatus -> delete``: the delete folds
    inline, so by the time the point re-fold runs the node is gone. Re-folding
    the status op then can only emit a spurious ``state-op-miss`` and fail the
    run (``NonFoldedEventsError``) for a journal every engine replays
    correctly — the same round-6 trap the Object re-fold's existence guard
    exists for. Pins that guard.

    ⚠️ #9649: the middle write is journalled directly, as a pre-#9649 writer
    left it — the live door now refuses a lifecycle write on a terminal Point.
    The node is deleted before the rebuild, so no graph value needs reproducing.
    """
    sdk, events = env
    old = sdk.create_point("statement", "old claim", dedup=False,
                           status="live")["id"]
    new = sdk.create_point("statement", "new claim", dedup=False,
                           status="live")["id"]
    sdk.supersede_point(old, new)
    sdk._journal_entity_mutation("Point", old, "restatus",
                                 state={"status": "archived"})
    sdk.delete(old)

    # Must not raise: the deleted Point's later status op is legitimately
    # unfolded (there is no node left), not a reconciliation failure.
    sdk._get_proj().rebuild_all(str(events), confirm_destructive=True)
    assert sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN count(n)",
        params={"id": old}).result_set[0][0] == 0, \
        "a hard-deleted point must not reappear on replay"


# ── The vocabulary values stay writable (owner ruling) ───────────────────

@pytest.mark.parametrize("good", ["draft", "live"])
def test_vocabulary_values_are_accepted_and_journaled(env, good):
    """The fix must refuse only NON-vocabulary values.

    The owner's state-model ruling on #3311 (comment 6091597170) requires
    ``draft`` to remain an explicit, honoured choice. A tightening to
    ``update_point``'s draft→live rule would be a behaviour change, not this fix.

    ⚠️ #9649: the parametrization narrowed to the transitions ONTOLOGY §5
    DECLARES from ``draft`` (``live``). ``archived`` and ``outdated`` are still
    vocabulary values — the status door must accept the WORD and refuse only
    the TRANSITION — so the test below pins that separation instead of losing it.
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


@pytest.mark.parametrize("word", ["outdated", "archived"])
def test_an_undeclared_transition_is_refused_as_a_TRANSITION(env, word):
    """#9649: a vocabulary word the lifecycle does not allow from ``draft``.

    The refusal must be about the TRANSITION — the word IS legal Point
    vocabulary, and ONTOLOGY §5 declares neither ``draft -> outdated`` nor
    ``draft -> archived`` (``archived`` is *"reserved — no v1 write path"*;
    ``outdated`` is written only by ``invalidate_point``/``supersede_point``).
    Refusing it as a bad WORD would be a regression of this issue's defect 1.
    """
    sdk, events = env
    pid = _multilabel_point(sdk)
    before = _mutations(events, pid)

    with pytest.raises(ValueError) as exc:
        sdk.update_entity(pid, status=word)
    text = str(exc.value)
    assert "transition" in text.lower(), (
        f"the refusal must be about the transition, not the vocabulary: {text!r}")
    assert "Invalid status" not in text, (
        f"{word!r} IS Point vocabulary — refusing it as a bad word would be a "
        f"regression of #3311's fix: {text!r}")
    assert _status(sdk, pid) == "draft"
    assert _mutations(events, pid) == before, (
        "a refused transition must not be journaled")


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
