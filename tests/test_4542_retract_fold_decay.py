"""#4542 — the pure fold's ``PointRetracted`` arm must apply the same BELIEF
decay the graph arm does, or ``fold()`` and ``rebuild_all()`` disagree on every
retract.

Live ``retract_point`` CASes the status tombstone, ``updatedAt`` AND the
``decay_clause`` belief half (``confidence=0.5``, ``posterior_alpha=1.0``,
``posterior_beta=1.0``) in one statement; the graph fold's ``_retract`` writes
the same three via ``decay_clause``. ``_apply_one`` — the function the module
header declares "the single source of fold semantics" so that an incremental
backend and ``fold()`` "can never diverge" — wrote **only** the status, so a
retracted point kept its pre-retract belief in the pure fold while the graph
held the vacuous one.

Pinned contracts here:
  (a) ``fold()`` decays the belief on a retract — with a non-vacuous
      pre-retract belief AND with none (the graph arm SETs the three props
      unconditionally, so the pure fold must too);
  (b) the decay values are the ONE declaration ``decay_clause`` renders —
      a second hand-maintained copy is exactly how they drifted;
  (b2) the one copy that can be pinned without an overlapping change stays
      pinned to the DECLARATION: ``consistency._DECAY`` (the journal side of the
      divergence detector) is asserted equal to ``VACUITY_BELIEF`` in case (e),
      so moving the declaration without moving that copy reds. Two hand-declared
      copies of the triple remain besides ``VACUITY_BELIEF`` — that one (pinned)
      and the ``assess_source`` payload in ``tortoise/sdk.py`` (held to the
      contract by values only); neither is edited here.
  (c) ``fold()`` and ``rebuild_all()`` agree on a retract produced by the REAL
      emitters (``create_point`` + ``retract_point``), which is also what makes
      the synthetic shapes above reachable rather than invented;
  (d) the decay lands at the retract's own journal position: a retract followed
      by a hard delete + recreate must leave the FRESH incarnation alone. This
      one is a guard against the wrong fix (a post-hoc sweep re-applying the
      decay over the finished fold — the defect #2884 A3 removed), not a
      pre-fix RED.

Run (embedded carve-out lane; case (c) needs a graph):
  PYTHONPATH=$PWD TORTOISE_TEST_CARVE_OUT=1 .venv/bin/python -m pytest \
      tests/test_4542_retract_fold_decay.py -x -q
"""
from __future__ import annotations

import json
import os

import pytest

from tortoise.live import VACUITY_BELIEF, decay_clause
from tortoise.projection import fold
from tortoise.sdk import TortoiseSDK

BELIEF = ("confidence", "posterior_alpha", "posterior_beta")

#: The contract as the LIVE writer states it in Cypher (``retract_point``'s CAS,
#: ``_retract``, ``decay_clause``). Written as a LITERAL on purpose: asserting
#: the fold against ``VACUITY_BELIEF`` alone would move with the declaration and
#: could never red on a wrong constant.
VACUITY_LITERAL = {"confidence": 0.5, "posterior_alpha": 1.0,
                   "posterior_beta": 1.0}


@pytest.fixture
def journaled(tmp_path):
    """(db, events_dir, sdk) with the JSONL journal wired."""
    db = os.path.join(str(tmp_path), "retract4542.db")
    events = tmp_path / "events"
    events.mkdir()
    sdk = TortoiseSDK(db, event_log_path=str(events / "events.jsonl"))
    yield db, events, sdk
    sdk.close()


def _journal(events) -> list[dict]:
    path = events / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def _graph_belief(sdk, pid: str) -> dict:
    """The three belief props as PERSISTED (no read-path coalescing)."""
    row = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) "
        "RETURN n.confidence, n.posterior_alpha, n.posterior_beta",
        params={"id": pid},
    ).result_set[0]
    return {"confidence": row[0], "posterior_alpha": row[1],
            "posterior_beta": row[2]}


def _clause_belief() -> dict:
    """The values ``decay_clause`` renders, parsed back out of the Cypher."""
    out: dict = {}
    for part in decay_clause("n").split(", "):
        key, _, value = part.partition("=")
        out[key.split(".", 1)[1]] = float(value)
    return out


@pytest.mark.parametrize("pre_belief", [
    # A non-vacuous pre-retract belief (the EP/dream write-back shape): pre-fix
    # `fold()` kept it (0.9 / 9.0 / 5.0) while the graph held 0.5 / 1.0 / 1.0.
    {"confidence": 0.9, "posterior_alpha": 9.0, "posterior_beta": 5.0},
    # No belief props at all — the shape `create_point` alone emits. The graph
    # arm's `SET n.confidence=0.5, …` creates all three unconditionally, so the
    # pure fold must materialize them too; pre-fix it left them ABSENT.
    {},
], ids=["non_vacuous_belief", "no_belief_props"])
def test_4542_pure_fold_retract_applies_the_vacuity_decay(pre_belief):
    """`fold()` on `PointAdded → PointRetracted` must end at the graph's
    decayed belief, not at the pre-retract value and not absent.

    (1) Fails on the pre-fix arm: `fold()` returns
        ``confidence=0.9, posterior_alpha=9.0, posterior_beta=5.0`` for the
        first state and the keys MISSING for the second, while `rebuild_all`
        (and live `retract_point`) hold ``0.5 / 1.0 / 1.0``.
    (2) Reachable: case (c) below produces the identical shape through the real
        emitters, and `sdk.py::retract_point` journals exactly
        ``{"type": "PointRetracted", "id": id}``.
    """
    point = {"id": "p1", "content": "c1", "pointKind": "", "status": "live"}
    point.update(pre_belief)
    pts = fold([
        {"type": "PointAdded", "point": point},
        {"type": "PointRetracted", "id": "p1"},
    ])
    assert pts["p1"]["status"] == "retracted"
    assert {k: pts["p1"].get(k) for k in BELIEF} == VACUITY_LITERAL


def test_4542_fold_retract_decay_is_the_one_declaration_decay_clause_renders():
    """The pure fold and the Cypher writer must read the SAME three values, so
    they cannot re-drift the way they did. A re-inlined literal that differs
    from ``decay_clause`` reds here instead of silently diverging on replay.
    The declaration is pinned to the contract literal first, so a wrong value
    in ``VACUITY_BELIEF`` reds the first assertion rather than silently
    dragging both representations with it."""
    assert dict(VACUITY_BELIEF) == VACUITY_LITERAL
    pts = fold([
        {"type": "PointAdded",
         "point": {"id": "p1", "content": "c1", "confidence": 0.9}},
        {"type": "PointRetracted", "id": "p1"},
    ])
    assert {k: pts["p1"].get(k) for k in BELIEF} == _clause_belief()


def test_4542_fold_and_rebuild_all_agree_on_a_sdk_retract(journaled):
    """The REAL emitters, then the writer's own repair path.

    (1) Fails on the pre-fix arm: the live graph and the rebuilt graph hold
        ``0.5 / 1.0 / 1.0`` while ``fold(journal)`` holds ``None`` for all three
        (a `create_point` payload carries no belief props) — `fold()` is not
        `replay(journal)`.
    (2) Reachable by construction: the two records are written by
        ``create_point`` and ``retract_point``, and the rebuilt graph IS
        ``rebuild_all(journal)``.
    """
    _db, events, sdk = journaled
    pid = sdk.create_point("statement", "retract me", status="live")["id"]
    sdk.retract_point(pid)

    live = _graph_belief(sdk, pid)
    assert live == VACUITY_LITERAL, (
        f"premise: live retract_point must write the decay, got {live}")

    folded = fold(_journal(events))[pid]
    assert {k: folded.get(k) for k in BELIEF} == VACUITY_LITERAL, (
        f"fold() disagrees with the live graph: {folded}")

    sdk._get_proj().rebuild_all(str(events))
    rebuilt = _graph_belief(sdk, pid)
    assert {k: folded.get(k) for k in BELIEF} == rebuilt, (
        f"fold() != rebuild_all(): fold={folded} rebuild={rebuilt}")


def test_4542_fold_retract_before_a_recreate_leaves_the_fresh_incarnation_alone():
    """The decay belongs to the retract's OWN journal position.

    A retract, then a real hard delete + recreate: the fresh incarnation is
    created AFTER the retract and carries no belief of its own, so it must not
    be decayed. The ordered dict naturally gets this right — which is the
    reason the pure fold needs no `last_ann_drop_seq`-style gate (the graph
    fold does, because pass-1a hoists every `PointAdded` first).

    Guard against the WRONG fix, stated honestly: applying ``VACUITY_BELIEF``
    in a post-pass over the finished fold (rather than inside the arm) leaves
    ``confidence=0.5`` on the fresh incarnation and reds here. Pre-fix this
    case is green — its mutation target is the post-pass implementation, not
    the reported defect.
    """
    pts = fold([
        {"type": "PointAdded",
         "point": {"id": "p1", "content": "old", "status": "live",
                   "confidence": 0.9, "posterior_alpha": 9.0}},
        {"type": "PointRetracted", "id": "p1"},
        {"type": "EntityMutated", "op": "delete", "id": "p1", "label": "Point"},
        {"type": "PointAdded",
         "point": {"id": "p1", "content": "new", "status": "live"}},
    ])
    assert pts["p1"]["content"] == "new"
    assert {k: pts["p1"].get(k) for k in BELIEF} == {k: None for k in BELIEF}, (
        f"the pre-recreate retract decayed the FRESH incarnation: {pts['p1']}")


def test_4542_detector_journal_side_is_pinned_to_the_declaration():
    """(e) `tortoise/consistency.py::_DECAY` is one of the two hand-declared
    copies of the same triple that remain besides `VACUITY_BELIEF` (the other
    is the `assess_source` payload in `tortoise/sdk.py`), and it is the JOURNAL
    side of the divergence detector that measures this invariant — so a drift
    there would silently corrupt the verdict (`check_consistency` would report
    the writer's own graph as diverged, or a real divergence as clean). That
    module is not edited here, so the copy is PINNED to the DECLARATION instead:
    move `VACUITY_BELIEF` without moving this copy and this reds.

    (1) Fails if `_DECAY` stops following `VACUITY_BELIEF`.
    (2) Reachable: both objects are imported from the shipped modules. The
        declaration's own values are separately pinned to the contract literal
        by `test_4542_fold_retract_decay_is_the_one_declaration_decay_clause_renders`,
        so the chain is literal -> declaration -> copy.
    """
    from tortoise.consistency import _DECAY  # hand-declared copy of the triple
    from tortoise.live import VACUITY_BELIEF as _declaration
    assert dict(_DECAY) == dict(_declaration)
