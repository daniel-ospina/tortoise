"""#10137 — does the belief layer enter the ORDER a query returns?

Measured on this tree before the fix: the EP posterior is read by
``GraphRanker`` only when a caller passes ``order_by="graph"``. The DEFAULT
path (``order_by="relevance"`` — what ``tortoise_search`` uses and what the
answer lane's ``retrieve``/assembly calls use, since none of them pass an
``order_by``) never reads ``posterior_alpha``/``ep_alpha`` at all, while it
still SHIPS each hit's ``ep`` block (``confidence_mean``, ``has_ep``,
``measured``, ``baseline``). A consumer reading a top-1 hit with
``confidence_mean: 0.9091`` therefore has no way to tell "belief ranked this"
from "belief was never consulted" — the same shape that lets a published
number read as evidence for a layer that did not participate.

The contract under test: every returned hit that carries an ``ep`` block says
``ep.informed_order`` — ``"consulted"`` when the ordering read the belief, and
``"not_consulted"`` when it did not.

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
