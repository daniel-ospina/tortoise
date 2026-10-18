"""#6211 — the EP factor-selection report and the typed hop error.

The issue measured three failures at the graph's own scale (3,353 points,
2,073 ``ep_dirty``). This file pins the two that changed here:

1. **The 200-operator cap is REPORTED in the result, not just stderr.**
   ``analyze._bfs_select_operators`` truncates the selected operator set with a
   ``logging.warning`` and returned a bare 2-tuple, so
   ``sdk.compute_confidence`` returned ``{iterations, converged, confidences}``
   either way — a caller could not tell a full-graph ranking from one computed
   over a truncated topology, and a published ranking (margins of 0.0067)
   came from a capped pass with nothing in the result saying so. The result
   now always carries ``truncated`` / ``operators_seen`` / ``operators_used``.

2. **A hop the engine aborts on its own timeout is TYPED.**
   ``_affected_claims``' two-hop bridge died as a bare
   ``redis.exceptions.ResponseError: Query timed out``
   (measured, reproduces at a >1 s hop). It is now
   ``tortoise.ep.EPSubgraphHopError`` carrying the hop index, the phase and the
   frontier size — and it still IS a ``ResponseError``, so existing ``except``
   clauses (and ``tortoise/retry.py``'s classifier) are unaffected.

The two acceptance cases — "the cap must fire" and "the cap must NOT fire" —
are both asserted against the REAL default (200) and never by raising it. Two
tests below DO drive ``op_cap`` deliberately (``op_cap=2``, ``op_cap=default+100``),
for the selector's own report semantics across a reused dict and an opt-in
``stats`` argument — neither stands in for the acceptance case.
``test_6211_ep_scale.py`` carries the scale fixture; this file is the fast
contract.
"""

from __future__ import annotations

import os
import sys
import tempfile
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import redis.exceptions as _re

from tortoise.analyze import _bfs_select_operators
from tortoise.ep import EPSubgraphHopError, TortoiseEP
from tortoise.sdk import TortoiseSDK

#: The shipped operator-selection cap (`analyze._bfs_select_operators`'s
#: default). Named here so a test that expected a truncation cannot silently
#: pass by observing a DIFFERENT default.
DEFAULT_OP_CAP = 200


# ── Fixtures ────────────────────────────────────────────────────────


def _fresh_sdk(prefix: str = "tt6211_") -> TortoiseSDK:
    """A fresh SDK (embedded file, or the lane's live graph under a URI)."""
    td = tempfile.mkdtemp(prefix=prefix)
    return TortoiseSDK(os.path.join(td, "t.db"))


def _hub(sdk: TortoiseSDK, n_ops: int) -> tuple[str, str]:
    """Two live claims wired through ``n_ops`` binary operators.

    Every operator's inputs are the SAME two claims, so a single anchor
    selects every operator in ONE hop — the cheapest shape that can exceed the
    operator cap (which is what #6211 is about), with an EP run over 2 claims
    and ``n_ops`` binary factors that converges in a couple of iterations.
    """
    proj = sdk._get_proj()
    proj.g.query("MATCH (n) DETACH DELETE n")
    proj.g.query(
        "UNWIND $ids AS id CREATE (n:Point) "
        "SET n.id = id, n.is_operator = false, n.status = 'live', "
        "    n.confidence = 0.5",
        params={"ids": ["c0", "c1"]},
    )
    proj.g.query(
        "UNWIND $ids AS id CREATE (n:Point) "
        "SET n.id = id, n.is_operator = true, n.op_type = 'IMPL', "
        "    n.status = 'live', n.direction = 'bidirectional', "
        "    n.confidence = 0.5",
        params={"ids": [f"op{i}" for i in range(n_ops)]},
    )
    proj.g.query(
        "MATCH (o:Point) WHERE o.is_operator = true "
        "MATCH (a:Point {id:'c0'}), (b:Point {id:'c1'}) "
        "CREATE (o)-[:IMPL {direction: 'bidirectional'}]->(a), "
        "       (o)-[:IMPL {direction: 'bidirectional'}]->(b)"
    )
    return "c0", "c1"


# ── 1. The cap is visible in the RESULT ────────────────────────────


def test_a_capped_selection_is_distinguishable_in_the_result():
    """#6211 A: a capped pass reports the cap; ``converged`` does not.

    The assertion is on the RESULT of the documented call — the shape the
    issue says was unusable — not on the log line.
    """
    sdk = _fresh_sdk()
    try:
        anchor, _other = _hub(sdk, DEFAULT_OP_CAP + 60)
        result = sdk.compute_confidence(anchors=[anchor], max_hops=1, require_calibration=False)
        assert result["truncated"] is True, (
            f"a pass whose operator selection hit the cap must say so; result was {sorted(result)}"
        )
        assert result["operators_seen"] > DEFAULT_OP_CAP, (
            "operators_seen must report what the BFS COLLECTED before the cap "
            f"(the real default is {DEFAULT_OP_CAP}); got "
            f"{result['operators_seen']}"
        )
        assert result["operators_used"] == DEFAULT_OP_CAP, (
            "operators_used must report what the run actually consumed — the "
            f"real shipped cap, never a value the test raised; got "
            f"{result['operators_used']}"
        )
        assert result["operators_used"] < result["operators_seen"]
        # The pre-#6211 signal could not distinguish these: the pass converged.
        assert result["converged"] is True
    finally:
        sdk.close()


def test_the_noarg_dirty_path_reports_its_dream_selections_cap():
    """The no-arg path's priors come from a dream that caps at the same 200.

    #6211 review round 3: ``compute_confidence()`` (no args) runs a bounded
    fail-safe ``dream`` FIRST, over the same 200-operator default, and that
    pass flushes its posteriors — which the exact pass then loads as priors. So
    the result was certifying a capped derivation as complete with
    ``truncated: False``, on the very path the issue measured. The dream's
    selection report is folded into the result now.
    """
    sdk = _fresh_sdk()
    try:
        _hub(sdk, DEFAULT_OP_CAP + 60)
        sdk._mark_dirty(["c0", "c1"])
        assert sdk._dirty_roots, "precondition: the roots are dirty"
        result = sdk.compute_confidence(require_calibration=False)
        assert result["truncated"] is True, (
            "the dream pass that shaped these priors selected "
            f"{DEFAULT_OP_CAP} of >{DEFAULT_OP_CAP} operators; result was "
            f"truncated={result['truncated']!r}, seen="
            f"{result['operators_seen']!r}, used={result['operators_used']!r}"
        )
        assert result["operators_seen"] > DEFAULT_OP_CAP
        assert result["operators_used"] == DEFAULT_OP_CAP
    finally:
        sdk.close()


def test_an_uncapped_selection_reports_truncated_false():
    """#6211 A (other half): a complete selection says so, explicitly.

    Against the REAL default cap — a small graph, no cap touched — and with
    both numbers present, so a caller never has to read an absent key as
    "no truncation".
    """
    sdk = _fresh_sdk()
    try:
        anchor, _other = _hub(sdk, 2)
        result = sdk.compute_confidence(anchors=[anchor], max_hops=1, require_calibration=False)
        assert result["truncated"] is False, (
            f"an uncapped pass must report truncated=False; got {sorted(result)}"
        )
        assert result["operators_seen"] == 2
        assert result["operators_used"] == 2
        assert result["operators_seen"] == result["operators_used"]
        assert result["confidences"], "the small-graph pass still writes beliefs"
    finally:
        sdk.close()


def test_the_explicit_factors_path_reports_its_own_selection():
    """A caller-supplied factor set is never capped — and says so."""
    sdk = _fresh_sdk()
    try:
        _anchor, _other = _hub(sdk, 3)
        result = sdk.compute_confidence(factors=["op0", "op1"], require_calibration=False)
        assert result["truncated"] is False
        assert result["operators_seen"] == 2
        assert result["operators_used"] == 2
    finally:
        sdk.close()


def test_the_selector_stats_out_parameter_is_opt_in():
    """``_bfs_select_operators`` keeps its 2-tuple return contract.

    Every call site (the Dreamer, the diagnostics scripts, the calibration
    scope probe) destructures the tuple; the report is an opt-in dict, so no
    existing caller changes.
    """
    sdk = _fresh_sdk()
    try:
        anchor, _other = _hub(sdk, DEFAULT_OP_CAP + 5)
        proj = sdk._get_proj()
        plain = _bfs_select_operators(proj, [anchor], max_hops=1)
        assert isinstance(plain, tuple) and len(plain) == 2
        assert len(plain[0]) == DEFAULT_OP_CAP
        stats: dict = {}
        ops, _anchors = _bfs_select_operators(proj, [anchor], max_hops=1, stats=stats)
        assert len(ops) == DEFAULT_OP_CAP
        assert stats["truncated"] is True
        assert stats["operators_seen"] == DEFAULT_OP_CAP + 5
        assert stats["operators_used"] == DEFAULT_OP_CAP
        assert stats["op_cap"] == DEFAULT_OP_CAP
    finally:
        sdk.close()


def test_the_selector_stats_describe_the_current_call_only():
    """A REUSED stats dict must not leak an earlier call's truncation.

    #6211 review round 1: deciding whether to write the final block by reading
    the caller's dict back (``stats.get("truncated")``) let a dict that once
    saw a cap SKIP that write, so a later UNCAPPED selection on the same dict
    kept the earlier call's ``truncated: True`` and its numbers — a report that
    describes a different call than the one the caller is asking about.
    """
    sdk = _fresh_sdk()
    try:
        anchor, _other = _hub(sdk, DEFAULT_OP_CAP + 5)
        proj = sdk._get_proj()
        stats: dict = {}
        _bfs_select_operators(proj, [anchor], max_hops=1, op_cap=2, stats=stats)
        assert stats["truncated"] is True, "precondition: the dict saw a cap"
        assert stats["operators_seen"] == DEFAULT_OP_CAP + 5
        ops, _anchors = _bfs_select_operators(
            proj, [anchor], max_hops=1,
            op_cap=DEFAULT_OP_CAP + 100, stats=stats)
        assert len(ops) == DEFAULT_OP_CAP + 5
        assert stats["truncated"] is False, (
            "the report must describe THIS call; a stale truncated=True from "
            f"the reused dict is wrong; got {stats!r}"
        )
        assert stats["operators_seen"] == DEFAULT_OP_CAP + 5
        assert stats["operators_used"] == DEFAULT_OP_CAP + 5
    finally:
        sdk.close()


# ── 2. The timed-out hop is TYPED ──────────────────────────────────

#: A fragment unique to the operator-mediated bridge query in
#: ``_affected_claims``' HOP LOOP (the hop the issue's traceback names). The
#: seed-neighbourhood query in ``_live_neighbors_many`` shares the pattern but
#: projects ``m.id`` only, so the two-hop return list is what distinguishes them.
_BRIDGE_FRAGMENT = "RETURN DISTINCT n.id, m.id"

#: A fragment unique to the operator-less direct-edge query in the hop loop
#: (the seed-phase sibling projects ``b.id`` only).
_DIRECT_FRAGMENT = "RETURN DISTINCT a.id, b.id"


class _FaultyGraph:
    """A graph proxy that fails the hop query named by a fragment."""

    def __init__(self, real, fragment: str, message: str):
        self._real = real
        self._fragment = fragment
        self._message = message
        self.seen: list[str] = []

    def query(self, cypher, *args, **kwargs):
        self.seen.append(cypher)
        if self._fragment in cypher:
            raise _re.ResponseError(self._message)
        return self._real.query(cypher, *args, **kwargs)


def _ep_over(faulty: _FaultyGraph) -> TortoiseEP:
    return TortoiseEP(types.SimpleNamespace(g=faulty))


def test_a_timed_out_hop_raises_the_typed_error_with_the_hop_identified():
    """#6211 B: a hop that cannot complete says WHICH hop, in a typed error."""
    sdk = _fresh_sdk()
    try:
        anchor, _other = _hub(sdk, 2)
        faulty = _FaultyGraph(sdk._get_proj().g, _BRIDGE_FRAGMENT, "Query timed out")
        with pytest.raises(EPSubgraphHopError) as excinfo:
            _ep_over(faulty)._affected_claims([anchor], 2)
        err = excinfo.value
        assert err.hop == 1, f"the FIRST hop is the one that failed; got {err.hop}"
        assert err.phase == "operator_bridge"
        assert err.frontier_size == 1, (
            "the frontier size is what makes the failure addressable — here "
            "the fixture's single seed-neighbourhood claim (`affected` holds "
            "the operator's OTHER input, not the seed); got "
            f"{err.frontier_size}"
        )
        assert "hop 1" in str(err) and "operator_bridge" in str(err)
        assert "1-claim frontier" in str(err)
        assert "Query timed out" in str(err), "the engine's own words are kept"
    finally:
        sdk.close()


def test_the_typed_error_is_still_a_ResponseError():
    """Not a breaking change: every existing ``except ResponseError`` catches it.

    ``tortoise/retry.py`` classifies ``ResponseError`` arms, and callers
    (``hosted_api``, the CLI) catch the redis class — a sibling class would
    have silently turned a handled failure into a crash.
    """
    sdk = _fresh_sdk()
    try:
        anchor, _other = _hub(sdk, 2)
        faulty = _FaultyGraph(sdk._get_proj().g, _BRIDGE_FRAGMENT, "Query timed out")
        with pytest.raises(_re.ResponseError):
            _ep_over(faulty)._affected_claims([anchor], 2)
    finally:
        sdk.close()


def test_a_non_timeout_response_error_is_not_rewritten():
    """Only the engine's timeout is re-typed; other ResponseErrors pass through.

    A WRONGTYPE/permission refusal is a different failure with a different
    remedy, and ``retry.py`` classifies some of them into retry arms — a
    rewriting wrapper would hide that behind a new message.
    """
    sdk = _fresh_sdk()
    try:
        anchor, _other = _hub(sdk, 2)
        faulty = _FaultyGraph(
            sdk._get_proj().g,
            _BRIDGE_FRAGMENT,
            "WRONGTYPE Operation against a key holding the wrong kind of value",
        )
        with pytest.raises(_re.ResponseError) as excinfo:
            _ep_over(faulty)._affected_claims([anchor], 2)
        assert not isinstance(excinfo.value, EPSubgraphHopError)
        assert "WRONGTYPE" in str(excinfo.value)
    finally:
        sdk.close()


def test_the_direct_edge_hop_is_typed_too():
    """The operator-less hop is a hop: it is named as its own phase."""
    sdk = _fresh_sdk()
    try:
        anchor, _other = _hub(sdk, 2)
        faulty = _FaultyGraph(sdk._get_proj().g, _DIRECT_FRAGMENT, "Query timed out")
        with pytest.raises(EPSubgraphHopError) as excinfo:
            _ep_over(faulty)._affected_claims([anchor], 2)
        assert excinfo.value.phase == "direct_edge"
        assert excinfo.value.hop == 1
        assert excinfo.value.frontier_size == 1
    finally:
        sdk.close()


def test_the_seed_neighbourhood_hop_is_typed_as_hop_zero():
    """The pre-BFS seed expansion is the first query that can die.

    It runs BEFORE the loop's hop 1, so it is hop 0 — without this the
    measured failure would still escape as a bare ResponseError on a dense
    graph (the seed neighbourhood is the same two-hop pattern).
    """
    sdk = _fresh_sdk()
    try:
        anchor, _other = _hub(sdk, 2)
        faulty = _FaultyGraph(sdk._get_proj().g, "RETURN DISTINCT m.id", "Query timed out")
        with pytest.raises(EPSubgraphHopError) as excinfo:
            _ep_over(faulty)._affected_claims([anchor], 2)
        assert excinfo.value.hop == 0
        assert excinfo.value.phase == "seed_neighbourhood"
        assert excinfo.value.frontier_size == 1
    finally:
        sdk.close()


@pytest.mark.parametrize(("seed", "fragment", "phase"), [
    ("c0", "RETURN n.id, (n.is_operator = true), n.status", "seed_lookup"),
    ("op0", "RETURN DISTINCT c.id", "seed_operators"),
    ("c0", "RETURN DISTINCT b.id", "seed_direct_edge"),
])
def test_every_seed_phase_query_is_typed_as_hop_zero(seed, fragment, phase):
    """The WHOLE seed phase is hop 0, not just its two-hop neighbour query.

    #6211 review round 1: the label-resolution sweep, the operator-seed
    # expansion and the plain-seed direct-edge expansion each take the caller's
    # seed set as their operand, so on the issue's dense graph they are the
    # first queries that can exceed the engine's timeout — leaving them bare
    # would have kept the old hop-less ``ResponseError`` on exactly the paths
    # the issue measured.
    """
    sdk = _fresh_sdk()
    try:
        _hub(sdk, 2)
        faulty = _FaultyGraph(sdk._get_proj().g, fragment, "Query timed out")
        with pytest.raises(EPSubgraphHopError) as excinfo:
            _ep_over(faulty)._affected_claims([seed], 2)
        assert excinfo.value.hop == 0
        assert excinfo.value.phase == phase
        assert excinfo.value.frontier_size == 1
    finally:
        sdk.close()


def test_a_draft_sweep_inside_the_hop_loop_names_that_hop():
    """The loop's OWN draft sweep is hop-scoped, driven through the real caller.

    #6211 review round 3: asserting the callee forwards a ``hop`` argument does
    not prove the production caller SUPPLIES one — reverting
    ``_filter_draft_ids(set(frontier), hop=hops)`` to a bare call kept every
    test green while a hop-scoped timeout would be misreported as pass-level.
    This drives the fault through ``_affected_claims`` itself.
    """
    sdk = _fresh_sdk()
    try:
        anchor, _other = _hub(sdk, 2)
        faulty = _FaultyGraph(sdk._get_proj().g, "AND n.status = 'draft'",
                              "Query timed out")
        with pytest.raises(EPSubgraphHopError) as excinfo:
            _ep_over(faulty)._affected_claims([anchor], 2)
        assert excinfo.value.phase == "draft_filter"
        assert excinfo.value.hop is not None and excinfo.value.hop >= 1, (
            "the loop asked this sweep, so it must name its hop — reporting "
            f"pass-level would hide which hop was unaffordable; got "
            f"hop={excinfo.value.hop!r}"
        )
    finally:
        sdk.close()


@pytest.mark.parametrize(("fragment", "phase"), [
    # Batch 1 of factor extraction (operator discovery over the affected set).
    ("RETURN DISTINCT o.id, o.op_type, o.label, o.direction",
     "factor_extraction"),
    # Batch 3 (operator-less direct-edge factors).
    ("RETURN a.id, b.id, type(r)", "factor_extraction"),
    # Batch 2 (an operator's inputs, by idx).
    ("RETURN o.id, c.id, r.idx", "factor_extraction"),
    # The draft sweep: pass-level when asked with no hop.
    ("AND n.status = 'draft'", "draft_filter"),
    # The #395 degeneration guard's sizing query.
    ("RETURN count(n)", "claim_count"),
])
def test_the_pass_level_queries_are_typed_too(fragment, phase):
    """Every id-set query of the pass is typed, not just the expansions.

    #6211 review round 2: factor extraction walks the FINISHED affected set
    (thousands of ids on the issue's graph), so it is the largest query of the
    pass and can be the one the engine aborts. Typing only the two-hop
    expansions left the issue's own symptom (a bare ''Query timed out'')
    reproducible on it. These are ``hop=None``: they walk a set, not a hop
    frontier, so there is no hop index to name.
    """
    sdk = _fresh_sdk()
    try:
        _hub(sdk, 2)
        faulty = _FaultyGraph(sdk._get_proj().g, fragment, "Query timed out")
        ep = _ep_over(faulty)
        with pytest.raises(EPSubgraphHopError) as excinfo:
            if phase == "claim_count":
                ep._graph_claim_count()
            elif phase == "draft_filter":
                ep._filter_draft_ids({"c0", "c1"})
            else:
                ep._affected_factors({"c0", "c1"})
        assert excinfo.value.phase == phase
        assert excinfo.value.hop is None, (
            "a pass-level query has no hop index — reporting 0 would claim "
            f"it failed in the seed phase; got hop={excinfo.value.hop!r}"
        )
        assert "pass-level" in str(excinfo.value)
    finally:
        sdk.close()


def test_the_epoch_read_is_typed_and_has_no_frontier():
    """The ``:EpMeta`` epoch read is a pass-level query with no id set."""
    sdk = _fresh_sdk()
    try:
        _hub(sdk, 2)
        faulty = _FaultyGraph(sdk._get_proj().g, "MATCH (m:EpMeta)",
                              "Query timed out")
        with pytest.raises(EPSubgraphHopError) as excinfo:
            _ep_over(faulty)._graph_ep_version()
        assert excinfo.value.phase == "ep_epoch"
        assert excinfo.value.hop is None
        assert excinfo.value.frontier_size is None
    finally:
        sdk.close()


def test_a_draft_sweep_asked_by_a_hop_names_that_hop():
    """The same query is hop-scoped when a hop asked it."""
    sdk = _fresh_sdk()
    try:
        _hub(sdk, 2)
        faulty = _FaultyGraph(sdk._get_proj().g, "AND n.status = 'draft'",
                              "Query timed out")
        with pytest.raises(EPSubgraphHopError) as excinfo:
            _ep_over(faulty)._filter_draft_ids({"c0", "c1"}, hop=2)
        assert excinfo.value.phase == "draft_filter"
        assert excinfo.value.hop == 2
        assert excinfo.value.frontier_size == 2
    finally:
        sdk.close()


def test_a_small_graph_still_converges_without_the_typed_error():
    """The pre-existing path is untouched: no timeout, no error, real beliefs."""
    sdk = _fresh_sdk()
    try:
        anchor, _other = _hub(sdk, 2)
        ep = TortoiseEP(sdk._get_proj())
        affected = ep._affected_claims([anchor], 2)
        assert {"c0", "c1"} <= affected
        result = sdk.compute_confidence(anchors=[anchor], max_hops=1, require_calibration=False)
        assert result["converged"] is True
        assert result["confidences"]
        assert result["truncated"] is False
    finally:
        sdk.close()
