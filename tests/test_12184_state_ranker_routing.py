"""#12184 R4 — ``StateRanker`` routes EVERY entity label to the ENTITY read, not just ``object``.

THE DEFECT THIS PINS (review at `9d99d4bf423b`, FINDING 2, P2 — the claim was UNPINNED)
--------------------------------------------------------------------------------------
R4 of this PR's Review Scope Contract says ``StateRanker`` routes through the SAME
label → family resolution ``GraphRanker`` uses, and refuses a label it has no signal
set for. The refusal half was pinned (`test_state_ranker_refuses_a_label_it_has_no_signal_set_for`);
the ROUTING half was not. Measured by the reviewer: mutating ``StateRanker.rerank`` to

    if et != "object":
        family = "point"          # validation left intact, so refusals still fire

left **all 12** pure assertions in ``tests/test_12184_ranker_entity_signals.py``
GREEN. The defect class #12184 exists for — an entity silently scored with a POINT's
signals, which answers ``confidence: None`` / ``degree: 0`` for every entity row and
is indistinguishable from "the graph knows nothing" — was restored for ``subject``,
``source`` and ``document``, and nothing reddened.

⚠️ Why it is worth a pin even though no product path reaches it TODAY: ``recall_state``'s
pool is `points + objects` only (``sdk.py``'s ``recall_state``), so ``outcome: none
today``. It becomes a live silent-wrong-signals defect the day that pool widens, which
is exactly the shape the issue was filed about.

NO GRAPH AND NO DOCKER: both fetches are replaced on the INSTANCE, so what is observed
is the routing decision itself, not a query result. A tree that stops routing ``subject``
to the entity read reds here without a database.
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tortoise.ranking import StateRanker, UnsupportedEntitySignals

#: A projection that is merely TRUTHY — `rerank` only tests `is not None` before
#: fetching, and both fetches are replaced, so nothing ever reaches a graph.
_SENTINEL_PROJECTION = object()


def _row(rid: str, entity_type: str) -> dict:
    return {"id": rid, "entity_type": entity_type, "scores": {"rrf": 0.9}}


def _ranking_ranker(monkeypatch):
    """A ``StateRanker`` whose fetches record which family each label reached."""
    r = StateRanker(projection=_SENTINEL_PROJECTION)
    calls: list[tuple[str, str, tuple[str, ...]]] = []

    def _point(ids, *args):
        calls.append(("point", "", tuple(ids)))
        return {}

    def _entity(ids, entity_type):
        calls.append(("entity", entity_type, tuple(ids)))
        return {}

    monkeypatch.setattr(r, "_fetch_point_signals", _point)
    monkeypatch.setattr(r, "_fetch_entity_signals", _entity)
    return r, calls


@pytest.mark.parametrize("entity_type", ["object", "subject", "source", "document"])
def test_every_entity_label_reaches_the_entity_read(monkeypatch, entity_type):
    """The mutant (`if et != "object": family = "point"`) reds all of these but `object`."""
    r, calls = _ranking_ranker(monkeypatch)
    r.rerank([_row("x", entity_type)])
    assert calls == [("entity", entity_type, ("x",))], (
        f"StateRanker routed {entity_type!r} to {calls} — every label in "
        f"ENTITY_SIGNAL_SPECS must reach the entity read, or the row is scored "
        f"with point signals and reads as 'the graph knows nothing'")


@pytest.mark.parametrize("entity_type", ["point", "operator"])
def test_point_and_operator_still_reach_the_point_read(monkeypatch, entity_type):
    """The other direction: the entity pin must not swallow the point family."""
    r, calls = _ranking_ranker(monkeypatch)
    r.rerank([_row("x", entity_type)])
    assert calls == [("point", "", ("x",))], f"{entity_type!r} dispatched to {calls}"


def test_a_MIXED_pool_routes_each_label_by_its_own_type(monkeypatch):
    """`recall_state` hands one pool of points AND entity rows — per-row routing.

    Pinned separately from the per-label cases because a single `family` computed
    once for the whole batch would satisfy those and fail this: the batch must
    dispatch per `entity_type`, not per call.
    """
    r, calls = _ranking_ranker(monkeypatch)
    r.rerank([
        _row("p1", "point"),
        _row("s1", "subject"),
        _row("o1", "object"),
        _row("src1", "source"),
        _row("p2", "point"),
    ])
    # The POINT family is batched (one call carrying both point ids); each entity
    # label gets its own call, because the fetch is per-label by signature.
    assert sorted(calls) == sorted([
        ("point", "", ("p1", "p2")),
        ("entity", "subject", ("s1",)),
        ("entity", "object", ("o1",)),
        ("entity", "source", ("src1",)),
    ]), f"a mixed pool dispatched to {calls}"


def test_an_event_still_refuses_instead_of_borrowing_point_signals(monkeypatch):
    """The refusal arm, next to the routing arms so one edit cannot quietly drop it."""
    r, calls = _ranking_ranker(monkeypatch)
    with pytest.raises(UnsupportedEntitySignals):
        r.rerank([_row("e1", "event")])
    assert calls == [], "a refused label must not reach either fetch"
