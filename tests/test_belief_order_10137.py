"""#10137 — does the belief layer enter the ORDER a query returns?

Measured on this tree before the fix: the EP posterior is read **for ordering**
by ``GraphRanker`` on ``order_by in ("graph", "confidence")`` and by
``StateRanker`` inside ``recall_state``. The DEFAULT path
(``order_by="relevance"`` — what ``tortoise_search`` uses and what the answer
lane's ``retrieve``/assembly calls use, since none of them pass an
``order_by``) never reads ``posterior_alpha``/``ep_alpha`` **for ordering**,
while every ordering (the default included) still ANNOTATES each hit's ``ep``
block from those same columns (``confidence_mean``, ``has_ep``,
``measured``, ``baseline``). A consumer reading a top-1 hit with
``confidence_mean: 0.9091`` therefore has no way to tell "belief ranked this"
from "belief was never consulted" — the same shape that lets a published
number read as evidence for a layer that did not participate.

The contract under test has THREE states, not two: a hit that carries an ``ep``
block says ``ep.informed_order == "consulted"`` when the ordering read the
belief, ``"not_consulted"`` when it did not, and carries **no key at all** when
the stage that produced the order cannot say (a ranker whose read is not
observable). Absence is the honest third answer, never a guess.

MUST run against a live FalkorDB (Docker). Test-prefixed isolated graph.
"""
from __future__ import annotations

import contextlib
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tests import _live_utils
from tortoise.sdk import TortoiseSDK

_GRAPH = f"tortoise_test_10137bord_{uuid.uuid4().hex[:8]}"
_DB_URI = _live_utils.docker_uri(_GRAPH)
FALKORDB_AVAILABLE = False
_OLD_URI = os.environ.get("TORTOISE_DB_URI")
try:
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    _probe = TortoiseSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    pass
finally:
    if _OLD_URI is not None:
        os.environ["TORTOISE_DB_URI"] = _OLD_URI
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")

_TEXTS = (
    "the deploy pipeline pushes the release to staging every afternoon",
    "the deploy pipeline pushes the release to production every night",
    "the deploy pipeline runs the release tests before the push",
    "the deploy pipeline stores the release artifact in the registry",
)
_QUERY = "deploy pipeline release"


@pytest.fixture(autouse=True)
def _isolated_db_uri():
    _old = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    yield
    if _old is not None:
        os.environ["TORTOISE_DB_URI"] = _old
    else:
        os.environ.pop("TORTOISE_DB_URI", None)


@pytest.fixture
def sdk():
    sdk = TortoiseSDK()
    try:
        yield sdk
    finally:
        with contextlib.suppress(Exception):
            sdk._get_proj().g.query(
                "MATCH (n) WHERE n.id IS NOT NULL DETACH DELETE n")
        sdk.close()


def _seed(sdk) -> list[str]:
    """4 claims, one carrying strong evidence that IMPL carries forward."""
    pids = [sdk.create_point("statement", t, status="live")["id"] for t in _TEXTS]
    sdk.set_point_baseline(pids[0], 10.0, 1.0)
    for pid in pids[1:]:
        sdk.set_point_baseline(pid, 1.0, 1.0)
    for pid in pids[1:]:
        sdk.create_operator("IMPL", pids[0], [pid])
    sdk.compute_confidence()
    return pids


def _strip_belief(sdk) -> None:
    sdk._get_proj().g.query(
        "MATCH (n:Point) REMOVE n.posterior_alpha, n.posterior_beta, "
        "n.ep_alpha, n.ep_beta, n.confidence, n.baseline_set")


def test_the_default_order_says_belief_was_not_consulted(sdk):
    """The default path ships an ``ep`` block and never read it — it must say so.

    This is the reproduction: with the layer ON vs OFF the default order and
    its scores are byte-identical, so the belief block on each hit describes
    something the ORDER did not use.
    """
    _seed(sdk)
    on = sdk.tortoise_fts_query(_QUERY, order_by="relevance", limit=10)
    _strip_belief(sdk)
    off = sdk.tortoise_fts_query(_QUERY, order_by="relevance", limit=10)

    assert [r["id"] for r in on] == [r["id"] for r in off]
    assert [r.get("scores", {}).get("rrf") for r in on] == [
        r.get("scores", {}).get("rrf") for r in off]

    assert on and off, "fixture must return hits — an empty page proves nothing"
    assert all("ep" in r for r in on), "fixture must ship the ep block"
    for r in on:
        assert r["ep"].get("informed_order") == "not_consulted", (
            "the default path presents the belief block but its order was "
            "relevance-only; the hit must say so")


def test_the_graph_order_says_belief_was_consulted(sdk):
    """``order_by='graph'`` DOES read the posterior — measured, and labelled."""
    _seed(sdk)
    on = sdk.tortoise_fts_query(_QUERY, order_by="graph", limit=10)
    graphed = [r for r in on if "graph_ranking" in r]
    assert graphed, "the graph path must expose its breakdown"
    for r in graphed:
        assert r["ep"].get("informed_order") == "consulted"

    on_scores = [r["graph_ranking"]["final_score"] for r in graphed]
    _strip_belief(sdk)
    off = sdk.tortoise_fts_query(_QUERY, order_by="graph", limit=10)
    off_scores = [r["graph_ranking"]["final_score"] for r in off
                  if "graph_ranking" in r]
    assert len(on_scores) == len(off_scores) > 0
    assert on_scores != off_scores, (
        "anti-inert control: the belief layer must move the graph score when "
        "it is connected — otherwise the whole census measures nothing")


def test_the_confidence_order_also_says_it_was_consulted(sdk):
    """``order_by='confidence'`` sorts by the belief mean — same label.

    Discriminating by construction (review P1): the high-belief claim is the
    LEAST relevant one, so a dead confidence branch — falling through to RRF
    relevance — returns the opposite order and this test reds on the ids, not
    only on the label.
    """
    # created FIRST so the relevance path's recency tie-break puts it LAST
    believed = sdk.create_point(
        "statement", "unicorn deploy", status="live")["id"]
    relevant = sdk.create_point(
        "statement", "the deploy pipeline pushes the release", status="live")["id"]
    sdk.set_point_baseline(relevant, 1.0, 1.0)
    sdk.set_point_baseline(believed, 10.0, 1.0)

    rel_order = [r["id"] for r in sdk.tortoise_fts_query(
        _QUERY, order_by="relevance", limit=10)]
    conf_rows = sdk.tortoise_fts_query(_QUERY, order_by="confidence", limit=10)
    conf_order = [r["id"] for r in conf_rows]

    assert conf_order != rel_order, (
        "confidence ordering must not be the relevance order — that is the "
        "symptom of the branch being unreachable")
    assert conf_order.index(believed) < conf_order.index(relevant)
    means = [r["ep"]["confidence_mean"] for r in conf_rows]
    assert means == sorted(means, reverse=True), means
    for r in conf_rows:
        assert r["ep"].get("informed_order") == "consulted"


def test_a_failed_signal_read_is_not_labelled_consulted(sdk, monkeypatch):
    """#10137 review P1 — the label follows the READ, not the ranker's config.

    ``GraphRanker._fetch_signals`` deliberately swallows a failed read. When it
    does, every confidence key stays at the neutral 0.5 (confidence path) and
    every ``graph_boost`` is 0.0 (graph path) — belief contributed nothing, so
    labelling those rows ``consulted`` would mint the exact false claim this
    change exists to remove.
    """
    from tortoise.ranking import GraphRanker

    _seed(sdk)
    monkeypatch.setattr(GraphRanker, "_fetch_point_signals",
                        lambda self, ids: (_ for _ in ()).throw(RuntimeError("boom")))

    graph_rows = sdk.tortoise_fts_query(_QUERY, order_by="graph", limit=10)
    assert graph_rows
    for r in graph_rows:
        assert r["ep"].get("informed_order") == "not_consulted"
        assert r["graph_ranking"]["graph_boost"] == 0.0

    conf_rows = sdk.tortoise_fts_query(_QUERY, order_by="confidence", limit=10)
    assert conf_rows
    for r in conf_rows:
        assert r["ep"].get("informed_order") == "not_consulted"


def test_a_projectionless_ranker_is_not_labelled_consulted(sdk):
    """A caller-supplied ranker with no graph reads nothing — and says so."""
    from tortoise.ranking import GraphRanker

    _seed(sdk)
    rows = sdk.tortoise_fts_query(_QUERY, order_by="graph", limit=10,
                                  graph_ranker=GraphRanker(None))
    assert rows
    for r in rows:
        assert r["ep"].get("informed_order") == "not_consulted"
        assert r["graph_ranking"]["graph_boost"] == 0.0


def test_recall_state_relabels_the_order_it_re_ranked(sdk):
    """Review round 3, P1 — the label belongs to the LAST re-order.

    `recall_state` builds its candidate pool with `tortoise_fts_query` at the
    default (belief-blind) ordering, so every pool row is stamped
    ``not_consulted`` — and then `StateRanker` scores
    ``relevance ** a × confidence ** b`` over the EP belief mean, so the order
    the caller receives DID consult the layer. Pre-fix, the shipped rows kept
    the pool's ``not_consulted``: a FALSE label on a public MCP surface, minted
    by the field added to remove exactly that ambiguity.
    """
    believed = sdk.create_point("statement", "unicorn deploy", status="live")["id"]
    relevant = sdk.create_point(
        "statement", "the deploy pipeline pushes the release", status="live")["id"]
    sdk.set_point_baseline(believed, 10.0, 1.0)
    sdk.set_point_baseline(relevant, 1.0, 1.0)

    rows = sdk.recall_state(query=_QUERY, limit=10)
    assert rows
    by_id = {r["id"]: r for r in rows}
    assert by_id[believed]["ep"]["informed_order"] == "consulted", (
        "recall_state re-ordered by the belief mean — the row must not keep "
        "the pool's not_consulted label")
    assert by_id[relevant]["ep"]["informed_order"] == "consulted"


def test_an_unobservable_ranker_makes_no_claim(sdk):
    """A duck-typed ranker's read cannot be observed — so no label at all.

    ``consulted=None`` leaves the key ABSENT rather than guessing ``False``:
    the same rule that keeps an ``ep``-less row from carrying a claim.
    """
    class _OpaqueRanker:
        def __init__(self):
            self.calls = []

        def rerank(self, rows, *, entity_type="point", query=None):
            out = list(reversed(rows))
            self.calls.append(([r["id"] for r in rows],
                               [r["id"] for r in out]))
            return out

    _seed(sdk)
    opaque = _OpaqueRanker()
    rows = sdk.tortoise_fts_query(_QUERY, order_by="graph", limit=10,
                                  graph_ranker=opaque)
    assert rows
    # Differential, not vacuous (#10137 review round 6, P2): prove the ranker
    # RAN — its `reversed` order is what the caller received. Without this the
    # "no label" half would pass on a path that never re-ordered at all.
    assert len(opaque.calls) == 1 and opaque.calls[0][0], opaque.calls
    assert opaque.calls[0][1] == [r["id"] for r in rows], (
        "the opaque ranker's order must be the one shipped, or the label "
        "assertion below certifies nothing")

    # The twin (#10137 review round 7, P2): the SAME data through an OBSERVABLE
    # ranker must carry the claim and return the same order. Without it, "no
    # key" is also satisfied by `origin/main`, where the key never exists — the
    # control has to be a claim the pre-fix tree cannot make.
    from tortoise.ranking import GraphRanker
    twin = sdk.tortoise_fts_query(_QUERY, order_by="graph", limit=10,
                                  graph_ranker=GraphRanker(sdk._get_proj()))
    assert {r["id"] for r in twin} == {r["id"] for r in rows} and twin
    assert any(r["ep"].get("informed_order") == "consulted" for r in twin), (
        "the observable ranker must claim 'consulted' on the same population")


    for r in rows:
        assert "informed_order" not in r["ep"]


def test_a_partially_answered_read_does_not_certify_the_whole_page(sdk, monkeypatch):
    """Review round 4, P1 — the verdict is per ROW, never per page.

    A read that answers one id and not another leaves the unanswered row scored
    from a neutral default. Stamping that row ``consulted`` would tell a
    belief-free row its belief informed the order — so it must carry NO claim
    (the key is absent), exactly like the unobservable-ranker case.
    """
    from tortoise.ranking import GraphRanker

    _seed(sdk)
    real = GraphRanker._fetch_point_signals

    def _only_first(self, ids):
        got = real(self, ids)
        first = sorted(got)[0]
        return {first: got[first]}

    monkeypatch.setattr(GraphRanker, "_fetch_point_signals", _only_first)
    rows = sdk.tortoise_fts_query(_QUERY, order_by="graph", limit=10)
    labels = {r["id"]: r["ep"].get("informed_order") for r in rows}
    assert "consulted" in labels.values(), labels
    assert None in labels.values(), (
        "a row the read did not answer must make no claim, not borrow the "
        f"page's verdict: {labels}")
    for r in rows:
        if labels[r["id"]] == "consulted":
            assert r["graph_ranking"]["graph_boost"] != 0.0


def test_a_projectionless_state_ranker_still_labels_its_embedded_read(sdk):
    """The embedded `state_confidence` IS the belief the score multiplies in."""
    from tortoise.ranking import StateRanker

    ranker = StateRanker(None)
    rows = [{"id": "a", "similarity": 0.9, "state_confidence": 0.95},
            {"id": "b", "similarity": 0.9, "state_confidence": 0.05}]
    ranked = ranker.rerank(rows)
    assert [r["id"] for r in ranked] == ["a", "b"]
    from tortoise.ranking import belief_read_for
    consulted, answered = belief_read_for(ranker, ranked)
    assert consulted is True and answered == frozenset({"a", "b"})


def test_an_unobservable_re_ranker_cannot_inherit_the_pool_label(sdk):
    """Review round 5, P1 — the LAST re-order owns the claim, so it must CLEAR.

    `recall_state`'s pool is labelled `not_consulted` before `StateRanker`
    runs. When the caller passes a ranker whose read cannot be observed, the
    annotation makes NO claim — which must be an ABSENT key, not the pool's
    stale `not_consulted` left in place. Otherwise an unobservable re-rank
    ships a false `not_consulted` on a belief-ranked order.
    """
    believed = sdk.create_point(
        "statement", "unicorn deploy", status="live")["id"]
    other = sdk.create_point(
        "statement", "the deploy pipeline pushes the release", status="live")["id"]
    sdk.set_point_baseline(believed, 10.0, 1.0)
    sdk.set_point_baseline(other, 2.0, 1.0)

    from tortoise.ranking import StateRanker

    calls: list[tuple] = []

    class _OpaqueStateRanker:
        """Delegates to a real StateRanker — but is not one, so its read is
        unobservable to `belief_read_for` (a decorating wrapper's shape)."""

        def __init__(self, inner):
            self.inner = inner

        def rerank(self, rows, *, entity_type="point", query=None):
            out = self.inner.rerank(rows, entity_type=entity_type, query=query)
            calls.append(([r["id"] for r in rows], [r["id"] for r in out]))
            return out

    observed = sdk.recall_state(
        query=_QUERY, limit=10,
        state_ranker=_OpaqueStateRanker(StateRanker(sdk._get_proj())))
    assert observed

    # ⛔ Differential, not vacuous (#10137 review round 6, P2): the re-rank has
    # to have RUN, and the SAME call with an OBSERVABLE ranker has to carry the
    # claim — otherwise "no key" is satisfied by a path that never re-ordered
    # at all (which is exactly what this case could not distinguish).
    assert len(calls) == 1 and calls[0][0], (
        f"the opaque re-rank must have run over the pool, not been skipped: {calls}")
    assert calls[0][1] == [r["id"] for r in observed], (
        "the inner ranker's order must be the one the caller received")

    observed_rows = sdk.recall_state(query=_QUERY, limit=10,
                                     state_ranker=StateRanker(sdk._get_proj()))
    assert [r["id"] for r in observed_rows] == [r["id"] for r in observed], (
        "both rankers must return the same order — that equality is what makes "
        "the label difference below the ONLY difference")
    assert any(r["ep"].get("informed_order") == "consulted"
               for r in observed_rows), (
        "the observable ranker's re-order must claim the read, or the "
        "unobservable case's absent key proves nothing")

    for r in observed:
        assert "informed_order" not in r["ep"], (
            "the unobservable re-rank must clear the pool's claim, not inherit "
            "it")


def test_a_neutral_default_is_not_a_consulted_belief(sdk):
    """Review round 6, P1 — a row with NO prior is not a belief measurement.

    `_fetch_point_signals` answers a ROW for a claim that has no persisted
    α/β, with the neutral Beta(1,1) mean 0.5. Keying "the read answered" on row
    membership therefore labelled a belief-free row ``consulted`` while the same
    hit said ``has_ep: False`` — two contradictory claims on one hit. The read
    supplies a MEASUREMENT or it does not; the label follows that.
    """
    _seed(sdk)
    before = sdk.tortoise_fts_query(_QUERY, order_by="graph", limit=10)
    assert any(r["ep"].get("informed_order") == "consulted" for r in before)

    _strip_belief(sdk)
    after = sdk.tortoise_fts_query(_QUERY, order_by="graph", limit=10)
    assert after
    for r in after:
        assert r["ep"]["has_ep"] is False
        assert r["ep"].get("informed_order") != "consulted", (
            "the belief read supplied only the neutral default, so belief did "
            "not inform this order")

    # the THIRD label site (#10137 review round 7, P1): `order_by="confidence"`
    # sorts on the same belief mean, so a neutral default is not a measurement
    # here either.
    conf = sdk.tortoise_fts_query(_QUERY, order_by="confidence", limit=10)
    assert conf
    for r in conf:
        assert r["ep"]["has_ep"] is False
        assert r["ep"].get("informed_order") != "consulted"

    state = sdk.recall_state(query=_QUERY, limit=10)
    assert state
    for r in state:
        assert r["ep"].get("informed_order") != "consulted"


def test_the_mirror_counts_only_where_the_read_consults_it(sdk):
    """Review round 8, P1 — a mirror is RANKING EVIDENCE only for the read that
    actually uses it.

    `GraphRanker` prefers the pre-#852 `n.confidence` mirror, so a mirror-only
    node IS a measurement there. `StateRanker` reads α/β only, so the same node
    is scored from the neutral default in `recall_state` and must NOT claim
    `consulted`. One predicate for both would put "belief informed this order"
    beside `confidence_source: "neutral"` on the same hit.
    """
    only_mirror = sdk.create_point(
        "statement", "the deploy pipeline mirror only", status="live")["id"]
    ordinary = sdk.create_point(
        "statement", "the deploy pipeline pushes the release", status="live")["id"]
    sdk.set_point_baseline(ordinary, 10.0, 1.0)
    # the legacy shape: the mirror, and no α/β columns at all
    sdk._get_proj().g.query(
        "MATCH (n:Point {id: $pid}) SET n.confidence = 0.95",
        {"pid": only_mirror})

    graph = {r["id"]: r for r in sdk.tortoise_fts_query(
        _QUERY, order_by="graph", limit=10)}
    assert graph[only_mirror]["ep"].get("informed_order") == "consulted", (
        "GraphRanker reads the mirror, so it IS a measurement there")

    state = {r["id"]: r for r in sdk.recall_state(query=_QUERY, limit=10)}
    assert state[ordinary]["ep"].get("informed_order") == "consulted"
    assert state[only_mirror]["ep"].get("informed_order") != "consulted", (
        "StateRanker does not read the mirror — the row was scored from the "
        "neutral default and must not claim a consulted belief")
