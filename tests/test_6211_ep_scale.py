"""#6211 — the cap and the timed-out hop AT THE GRAPH'S OWN SCALE.

The issue's measurement was taken on a 3,353-point graph with 2,073 ``ep_dirty``
claims, and its "what would count as fixed" asks for the cap to be visible in
the RESULT so a caller can refuse to rank on it. This file builds a graph at
that size — and, as the brief insists, DENSE in the operator subgraph rather
than merely large, because density (operators per claim) is what makes the
selection exceed the 200-operator cap and what makes a hop frontier unaffordable
— and asserts both mechanisms fire there:

1. ``compute_confidence(anchors=..., max_hops=2)`` on the scale fixture returns
   ``truncated is True`` with ``operators_seen > 200`` and
   ``operators_used == 200`` (the REAL shipped cap).
2. A hop query that the engine aborts raises ``EPSubgraphHopError`` naming the
   hop AND the real frontier size the pass was expanding from (here > 100
   claims, the shape an operator must bound).

WHY THE TIMEOUT IS INJECTED, NOT PRODUCED. A genuine ``Query timed out``
requires the ENGINE's own per-query bound (``GRAPH.CONFIG SET TIMEOUT``), which
is container-global: setting it inside a test would arm it for every peer test
in the same process — and the lane container deliberately runs the canonical
posture (``TIMEOUT=0``, ``tools/test_lane.py`` ENGINE PARITY), so no test can
time out legitimately. The failure was reproduced for real against a
``TIMEOUT=1000`` engine on a 3,353-claim / 160,000-edge graph (measured: the
hop aborted after 1.00 s over a 188-257-claim frontier; recorded on #6211) —
the INJECTION here stands in for that engine reply, and everything downstream
of it (which hop, which phase, which frontier size, how it composes with the
BFS) is the real code path.

Scale cost (measured, whole file): ~10.4 s (graph build plus four EP passes),
so this file runs in the FAST ``core``/``ep`` surfaces rather than the
``test-slow`` leg — its ``durations`` row in ``config/ci-surfaces.yml``
(11.0 s) is what keeps the shard balancer honest about it, and the file is
named in the PR body so a reviewer knows the merge gate carries it. It is NOT
in ``slow_files``: 10 seconds that prove both mechanisms on every PR is worth
its 10 seconds, and moving it to the slow leg would take the #6211 regression
guard out of the lane where #6211's code lands.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import redis.exceptions as _re

from tortoise.ep import EPSubgraphHopError, TortoiseEP
from tortoise.sdk import TortoiseSDK

#: The size the issue measured its failures at.
SCALE_CLAIMS = 3_353
#: The shipped operator-selection cap (``analyze._bfs_select_operators``).
DEFAULT_OP_CAP = 200
#: Operator-dense zone: ONE hub claim wired through more operators than the
#: cap, each with a DISTINCT second input. Two consequences, both required:
#:   * a hop from the hub collects `HUB_OPS` operators — more than the cap, so
#:     the selection truncates;
#:   * the hub's seed NEIGHBOURHOOD is `HUB_OPS` distinct claims, so the first
#:     hop expands from a frontier of that width — the shape that makes a hop
#:     query unaffordable on a real graph.
HUB_OPS = 300
#: Background operators so the operator subgraph has real volume at this size
#: (a graph with 3.3k claims and 300 operators is not "dense", it is small).
BACKGROUND_OPS = 4_000

_HUB = "h0"
_FILLERS = [f"f{i}" for i in range(HUB_OPS)]
_BACKGROUND = [f"b{i}" for i in range(SCALE_CLAIMS - 1 - len(_FILLERS))]


def _build_scale_graph(sdk: TortoiseSDK) -> None:
    """3,353 claims + a dense operator zone + a background operator field.

    Shape (deterministic, batched Cypher — per-node SDK writes would cost tens
    of thousands of round-trips at this size):
      * ``HUB_OPS`` operators all pointing at ONE hub claim, each with a
        DISTINCT second input — the dense zone a single-anchor hop fans out
        through, and the reason a hop frontier is hundreds of claims wide;
      * ``BACKGROUND_OPS`` operators over the tail claims (2 inputs each) —
        real operator volume, so the hop is measured on a graph of this size.
    """
    proj = sdk._get_proj()
    proj.g.query("MATCH (n) DETACH DELETE n")
    claims = [_HUB, *_FILLERS, *_BACKGROUND]
    for s in range(0, len(claims), 2_000):
        part = claims[s : s + 2_000]
        proj.g.query(
            "UNWIND $ids AS id CREATE (n:Point) SET n.id = id, "
            "n.is_operator = false, n.status = 'live', n.confidence = 0.5",
            params={"ids": part},
        )
    hub_ops = [{"op": f"hop{i}", "target": _HUB, "filler": _FILLERS[i]} for i in range(HUB_OPS)]
    bg_ops = [
        {
            "op": f"bop{i}",
            "target": _BACKGROUND[i % len(_BACKGROUND)],
            "filler": _BACKGROUND[(i * 7 + 3) % len(_BACKGROUND)],
        }
        for i in range(BACKGROUND_OPS)
    ]
    ops = [r["op"] for r in hub_ops + bg_ops]
    for s in range(0, len(ops), 2_000):
        part = ops[s : s + 2_000]
        proj.g.query(
            "UNWIND $ids AS id CREATE (n:Point) SET n.id = id, "
            "n.is_operator = true, n.op_type = 'IMPL', n.status = 'live', "
            "n.direction = 'bidirectional', n.confidence = 0.5",
            params={"ids": part},
        )
    for rows in (hub_ops, bg_ops):
        for s in range(0, len(rows), 2_000):
            part = rows[s : s + 2_000]
            proj.g.query(
                "UNWIND $rows AS row "
                "MATCH (o:Point {id: row.op}) "
                "MATCH (t:Point {id: row.target}) "
                "MATCH (f:Point {id: row.filler}) "
                "CREATE (o)-[:IMPL {direction: 'bidirectional'}]->(t), "
                "       (o)-[:IMPL {direction: 'bidirectional'}]->(f)",
                params={"rows": part},
            )


@pytest.fixture(scope="module")
def scale_graph():
    # ``TemporaryDirectory`` rather than ``mkdtemp``: the embedded store lives in
    # this tree, and a bare ``mkdtemp`` left it behind for the process's lifetime
    # — which ``test_tmpdir_hygiene_4096`` flags as a leaking fixture (#4096),
    # reddening `test (f)` for every lane that inherits this file.
    with tempfile.TemporaryDirectory(prefix="tt6211s_") as td:
        sdk = TortoiseSDK(os.path.join(td, "t.db"))
        try:
            _build_scale_graph(sdk)
            yield sdk
        finally:
            sdk.close()


def test_the_fixture_is_at_the_size_the_issue_measured(scale_graph):
    """Guard the fixture itself: a shrunken fixture would weaken both tests."""
    proj = scale_graph._get_proj()
    claims = proj.g.query("MATCH (n:Point) WHERE n.is_operator = false RETURN count(n)").result_set[
        0
    ][0]
    operators = proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator = true RETURN count(n)"
    ).result_set[0][0]
    assert claims == SCALE_CLAIMS, (
        f"the fixture must be at the scale #6211 measured ({SCALE_CLAIMS} claims); got {claims}"
    )
    assert operators >= HUB_OPS + BACKGROUND_OPS
    # Density is the mechanism, not size: the dense zone alone exceeds the cap
    # from a single hub anchor's hop.
    assert HUB_OPS > DEFAULT_OP_CAP


def test_the_operator_cap_fires_at_scale_and_the_result_says_so(scale_graph):
    """#6211 A at scale: a capped pass is distinguishable in the RESULT."""
    result = scale_graph.compute_confidence(anchors=[_HUB], max_hops=2, require_calibration=False)
    assert result["truncated"] is True, (
        f"a capped pass over a {SCALE_CLAIMS}-claim graph must say so; "
        f"result keys: {sorted(result)}"
    )
    assert result["operators_seen"] > DEFAULT_OP_CAP
    assert result["operators_used"] == DEFAULT_OP_CAP
    # The pre-#6211 signal still said "converged" — which is exactly why the
    # cap had to move into the result.
    assert result["converged"] is True
    assert result["confidences"], "the capped pass still writes what it ran"


def test_a_timed_out_hop_at_scale_names_the_real_frontier(scale_graph):
    """#6211 B at scale: the typed error carries the hop AND its frontier size.

    The hop that dies on a graph this size is expanding from a frontier of
    hundreds of claims — that number is what an operator needs in order to
    bound the pass, and it is what a bare ``ResponseError`` never said.
    """
    anchor = _HUB

    class _TimeoutGraph:
        def __init__(self, real):
            self._real = real

        def query(self, cypher, *args, **kwargs):
            if "RETURN DISTINCT n.id, m.id" in cypher:
                raise _re.ResponseError("Query timed out")
            return self._real.query(cypher, *args, **kwargs)

    ep = TortoiseEP(types.SimpleNamespace(g=_TimeoutGraph(scale_graph._get_proj().g)))
    with pytest.raises(EPSubgraphHopError) as excinfo:
        ep._affected_claims([anchor], 2)
    err = excinfo.value
    assert err.hop == 1
    assert err.phase == "operator_bridge"
    assert err.frontier_size > 100, (
        "at this scale the failed hop expands from a hundreds-of-claims "
        f"frontier; got {err.frontier_size}"
    )
    assert f"{err.frontier_size}-claim frontier" in str(err)


def test_the_uncapped_selection_reports_complete_at_scale(scale_graph):
    """The other direction at scale: a hop that fits reports ``truncated False``.

    ``max_hops=1`` from ONE anchor collects only that claim's operators, which
    the cap does not bind — so the report distinguishes "complete" from
    "capped" on the SAME graph, which a diagnostic keyed on graph size could
    not do.
    """
    result = scale_graph.compute_confidence(
        anchors=[_BACKGROUND[0]], max_hops=1, require_calibration=False
    )
    assert result["truncated"] is False
    assert result["operators_seen"] == result["operators_used"]
    assert result["operators_seen"] <= DEFAULT_OP_CAP
