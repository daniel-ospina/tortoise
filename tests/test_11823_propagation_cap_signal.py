"""#11823 — the propagation cap is VISIBLE: graph-scoped reach + belief signal.

The issue measured two silent instruments on embedded throwaway graphs:

* `dream()`'s own `coverage` divides by the pass's radius
  (`affected / len(_window_closure(window, max_hops))`), so it reads ≈1.0 for
  any pass that consumed its window — a 6-of-20 pass reads as converged;
* `dream_health_check()`'s `coverage_pct` counts `lastDreamedAt` STAMPS over a
  population that includes claims EP can never compute, so it can read 1.0 over
  a claim with no belief at all.

These tests pin the fix: a `propagation` block whose denominator is the graph's
EP-reachable population (which the pass does not choose), whose numerator uses
the repo's SHARED belief-presence predicate, and which says so per pass.

Each test is written to go RED if the behaviour it guards is removed — the
mutation proof in the PR body names which.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.dream import TRIVIAL_STAMP_PREDICATE
from tortoise.live import EPISTEMIC_LABELS
from tortoise.sdk import (
    DEFAULT_PROPAGATION_CENSUS_MAX_POINTS,
    TortoiseSDK,
)
from tortoise.search_engine import ep_has_belief_cypher


@pytest.fixture
def sdk():
    d = tempfile.mkdtemp(prefix="tortoise_11823_")
    s = TortoiseSDK(os.path.join(d, "t.db"))
    yield s
    s.close()
    shutil.rmtree(d, ignore_errors=True)


def _claim(sdk, content):
    return sdk.create_point("statement", content, dedup=False, status="live")["id"]


def _chain(sdk, n=3):
    """n claims in an IMPL chain + a baseline, so EP has real factors."""
    ids = [_claim(sdk, f"chain {i}") for i in range(n)]
    for i in range(1, n):
        sdk.create_operator("IMPL", ids[i], [ids[i - 1]])
    sdk.set_point_baseline(ids[-1], 8.0, 2.0)
    return ids


def _islands(sdk, n=2):
    return [_claim(sdk, f"island {i}") for i in range(n)]


def _prop(sdk):
    return sdk.dream_health_check()["propagation"]


# ── the denominator and the numerator are the SAME population ───────────────


def test_counts_match_an_independent_query(sdk):
    """Every population the block publishes is pinned against a query written
    independently here — `ep_reachable_claims`, `belief_present` and
    `points_edged` included.

    #12138 §2: the RANGE assertions in
    `test_no_belief_stays_inside_its_invariants` cannot see a WIDER denominator
    (the ratio falls, `no_belief` rises, BOTH stay in range), so an inflated
    `ep_reachable_claims` would pass them. Pinning each count against its own
    query is what reddens on one.
    """
    _chain(sdk, 3)
    _islands(sdk, 4)
    p = _prop(sdk)
    g = sdk._get_proj().g
    live = int(g.query(
        "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "AND (n.status IS NULL OR n.status <> 'draft') RETURN count(n)"
    ).result_set[0][0])
    assert p["graph_live_claims"] == live == 7
    # The denominator's rule, spelled from the LABEL LIST rather than from
    # `epistemic_disjunction`'s own text, so this is an independent statement of
    # it. The presence half uses `ep_has_belief_cypher` ON PURPOSE: #11702 makes
    # that predicate the ONE definition, and re-spelling it here would pin the
    # test to a private copy instead of to the shared gate.
    labels = "(" + " OR ".join(f"m:{label}" for label in EPISTEMIC_LABELS) + ")"
    edged = int(g.query(
        "MATCH (n:Point)-[:IMPL|NAND]-() "
        "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "AND (n.status IS NULL OR n.status <> 'draft') "
        "WITH DISTINCT n RETURN count(n)"
    ).result_set[0][0])
    reach = g.query(
        "MATCH (n:Point)-[:IMPL|NAND]-(m) "
        "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "AND (n.status IS NULL OR n.status <> 'draft') "
        "AND (m.status IS NULL OR m.status <> 'draft') "
        f"AND {labels} "
        f"WITH DISTINCT n, ({ep_has_belief_cypher('n')}) AS has_belief "
        "RETURN count(n), sum(CASE WHEN has_belief THEN 1 ELSE 0 END)"
    ).result_set[0]
    assert p["ep_reachable_claims"] == int(reach[0]) == 3
    assert p["belief_present"] == int(reach[1]) == 1
    assert p["operator_less_claims"] == live - edged == 4
    assert p["unclassified_claims"] == edged - int(reach[0]) == 0
    assert p["unavailable"] is None


def test_no_belief_stays_inside_its_invariants(sdk):
    """RANGES catch ONE direction of a mixed population, not both (#12138 §2).

    A NARROWER (or label-blind) denominator is the direction a range CAN see:
    the numerator can exceed it, so `no_belief` goes negative (measured -8.0)
    or the ratio exceeds 1.0.

    The direction this fixture is about is the WIDER one, and it is INVISIBLE
    to the ranges: with `belief_present` fixed, a larger denominator makes the
    ratio FALL and `no_belief` RISE, and both stay inside
    `0 <= no_belief <= ep_reachable_claims` / `0 <= pct <= 1` (measured: a
    denominator inflated by 4 gives `pct 0.2`, `no_belief +4` — in range).

    So the ranges are a cheap lower bound here, not the control: an inflated
    denominator is caught by pinning both counts against independently written
    queries in `test_counts_match_an_independent_query`. The identity asserted
    below (`belief_present + no_belief == ep_reachable_claims`) holds by
    construction and cannot discriminate either direction.
    """
    _chain(sdk, 3)
    _islands(sdk, 4)
    # A live Point edged only to a DRAFT far endpoint — the benign non-zero this
    # instrument exists to over-report. ⛔ #12138: it IS reachable
    # (`create_operator` routes the edge through an operator node, so the claim
    # enters the denominator: measured 3 → 4), and it is not in the
    # trivial-stamp population because it has an edge. So the fixture adds one
    # belief-less claim INSIDE the denominator rather than beside it.
    live = _claim(sdk, "edged only to a draft")
    draft = sdk.create_point("statement", "draft neighbour", dedup=False,
                             status="draft")["id"]
    sdk.create_operator("IMPL", live, [draft])
    p = _prop(sdk)
    assert 0 <= p["no_belief"] <= p["ep_reachable_claims"]
    assert 0.0 <= p["belief_coverage_pct"] <= 1.0
    assert p["belief_present"] + p["no_belief"] == p["ep_reachable_claims"]


def test_operator_less_claims_are_not_in_the_denominator(sdk):
    """EP cannot reach an operator-less claim, so it must not inflate the
    denominator — it is reported beside it (the `_trivial_stamp` population).

    ⚠️ The identity `ep_reachable + operator_less == graph_live` holds on THIS
    fixture only. It is asserted here as fixture-shaped evidence, not as a
    general law: a Point whose only `IMPL|NAND` edge reaches a DRAFT far
    endpoint is reachable-or-not by a different rule than operator-less, so
    `operator_less` is its own count rather than `graph_live - ep_reachable`.
    """
    _chain(sdk, 3)
    _islands(sdk, 4)
    p = _prop(sdk)
    assert p["ep_reachable_claims"] == 3
    assert p["operator_less_claims"] == 4
    assert (p["ep_reachable_claims"] + p["operator_less_claims"]
            == p["graph_live_claims"])


def test_a_stamped_claim_with_no_belief_is_reported(sdk):
    """THE mutation target: `coverage_pct` reads 100 % (every claim is stamped)
    while the operator-less islands have no belief at all. Before #11823 no
    surface said so.
    """
    _chain(sdk, 3)
    _islands(sdk, 4)
    sdk.dream(mode="full", require_calibration=False)
    health = sdk.dream_health_check()
    assert health["coverage_pct"] == 1.0        # stamps: all 7
    assert health["propagation"]["stamped"] == 7
    # …yet the 4 operator-less claims have no belief and are reported beside
    # the reachable denominator rather than being invisible.
    assert health["propagation"]["operator_less_claims"] == 4
    assert health["propagation"]["belief_present"] == 3
    assert health["propagation"]["no_belief"] == 0   # reachable set is covered


def test_a_zero_pair_is_not_a_belief(sdk):
    """The SHARED predicate (`ep_has_belief_cypher`): a stored (0,0) pair is
    not a belief. A private `posterior_alpha IS NOT NULL` would count it.
    """
    ids = _chain(sdk, 2)
    sdk.dream(mode="full", require_calibration=False)
    sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE n.id = $id "
        "SET n.posterior_alpha = 0.0, n.posterior_beta = 0.0, n.ep_alpha = 0.0, "
        "    n.ep_beta = 0.0, n.confidence = null",
        params={"id": ids[0]},
    )
    p = _prop(sdk)
    assert p["belief_present"] == 1
    assert p["no_belief"] == 1


def test_a_baseline_prior_is_a_belief(sdk):
    """…and a declared #2199 baseline IS one (same predicate)."""
    ids = _chain(sdk, 2)
    assert _prop(sdk)["belief_present"] == 1   # only the chain head has one
    sdk.set_point_baseline(ids[0], 7.0, 3.0)
    p = _prop(sdk)
    assert p["belief_present"] == 2
    assert p["no_belief"] == 0


def test_the_denominator_is_the_graph_not_the_last_pass(sdk):
    """No `max_hops`, no window: the denominator is the same for every pass
    shape and does not move when a pass runs."""
    _chain(sdk, 3)
    _islands(sdk, 2)
    before = _prop(sdk)["ep_reachable_claims"]
    sdk.dream(mode="local", max_hops=1, require_calibration=False)
    after = _prop(sdk)["ep_reachable_claims"]
    assert before == after == 3


def test_reachable_claim_count_is_unchanged(sdk, monkeypatch):
    """`_edged_claim_ids()` was extracted out of `_reachable_claim_count`;
    the closure intersection must stay AT THE CALL SITE.

    #12116 review P3: the earlier version compared two numbers that are equal
    on any real fixture (the function anchors at EVERY non-operator Point), so
    a version returning `len(self._edged_claim_ids())` — closure term dropped —
    still passed. The discriminator is a monkeypatched edged set containing one
    id that cannot be in any closure.
    """
    _chain(sdk, 3)
    real = sdk._edged_claim_ids()
    assert real
    monkeypatch.setattr(
        sdk, "_edged_claim_ids", lambda: set(real) | {"no-such-point-id"})
    assert sdk._reachable_claim_count(2) == len(real)


def _chain_ids(sdk):
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point)-[:IMPL|NAND]-(:Point) WHERE n.content STARTS WITH 'chain' "
        "RETURN n.id").result_set
    return [r[0] for r in rows]


# ── the read is bounded, and an unreadable graph is not a zero ──────────────


def test_the_propagation_read_is_three_aggregate_counts(sdk, monkeypatch):
    """The polled `GET /v1/dream/health` must not grow a per-vertex
    materialisation: three aggregate reads, nothing else.

    ⚠️ #12116 review P2: this COUNT control cannot detect cost — the first
    version passed it while the census measured 135 ms per 1,000 live claims.
    The budget now also has the BOUND (`test_the_census_refuses_above_its_bound`)
    and a recorded measurement (plan R7); this test only guards the SHAPE.
    """
    _chain(sdk, 2)
    inner = sdk._get_proj()
    queries: list[str] = []

    class _G:
        def __init__(self, g):
            self._g = g

        def query(self, q, **kw):
            queries.append(q)
            return self._g.query(q, **kw)

    class _P:
        def __init__(self, p):
            self._p = p
            self.g = _G(p.g)

    monkeypatch.setattr(sdk, "_get_proj", lambda: _P(inner))
    sdk._propagation_counts()
    assert len(queries) == 3
    for q in queries:
        assert "count(" in q.lower()
        assert "RETURN DISTINCT n.id" not in q


def test_unreadable_propagation_is_reported_not_zeroed(sdk, monkeypatch):
    """`0` and "I could not read it" must never collapse
    (BOUNDED-OPERATION-CONTRACT §1a)."""
    _chain(sdk, 2)

    def boom(*_a, **_kw):
        raise RuntimeError("graph went away")

    monkeypatch.setattr(sdk, "_propagation_counts", boom)
    p = sdk.dream_health_check()["propagation"]
    assert p["unavailable"] and "graph went away" in p["unavailable"]
    assert p["no_belief"] is None      # NOT 0
    assert p["belief_coverage_pct"] is None


# ── "did a pass run" is recorded on every arm ──────────────────────────────


def test_no_pass_observed_by_this_sdk_is_null_not_zero(sdk):
    """Absent process state is `null` + a reason, never a reassuring zero.

    #12116 review C-P2: the reason names what is OBSERVED (`this sdk`), not
    "this process" — the hosted door runs the pass on a different per-request
    SDK, so "no pass ran in this process" would be a false statement.
    """
    p = _prop(sdk)
    assert p["last_pass"] is None
    assert p["last_pass_reason"] == "no_pass_observed_by_this_sdk"


def test_last_pass_names_the_cap(sdk):
    _chain(sdk, 3)
    sdk.dream(mode="local", max_hops=2, require_calibration=False)
    last = _prop(sdk)["last_pass"]
    assert last["mode"] == "local" and last["max_hops"] == 2


def test_last_pass_never_subtracts_across_populations(sdk):
    """The sentence the issue asks for is "6 of 20, 14 uncovered".

    #12116 review B-P2: `affected` is the WINDOW a pass consumed and includes
    the operator-less ids `_trivial_stamp` unions in, which are NOT in
    `ep_reachable_claims`. Subtracting the two published `uncovered: 0` over a
    fixture where `no_belief` was 6 (measured) — a false green on the one number
    this change exists to surface. So the deficit is never derived: `no_belief`
    is read from the graph AFTER the pass and IS the uncovered count.
    """
    ids = _chain(sdk, 6)
    sdk.dream(mode="full", require_calibration=False)
    sdk._dirty_roots = {ids[0]}
    sdk.dream(mode="local", max_hops=1, require_calibration=False)
    p = _prop(sdk)
    last = p["last_pass"]
    assert last["graph_ep_reachable"] == p["ep_reachable_claims"]
    assert "uncovered" not in last          # no cross-population subtraction
    # the pair of facts the sentence is built from, each in its own population
    assert p["belief_present"] + p["no_belief"] == p["ep_reachable_claims"]
    # and `affected` may legitimately exceed the denominator — the reason a
    # subtraction was wrong rather than merely imprecise
    assert last["affected"] <= last["window_reachable"]


def test_an_operator_less_pass_does_not_report_full_coverage(sdk):
    """#12116 review P3 — the earlier version of this test could not fail.

    Its discriminating assertion was an `or` whose second disjunct was the
    healthy value, and its fixture passed the CHAIN ids as the dirty roots, so
    no operator-less claim was ever in `affected`. This one puts ONLY
    operator-less claims in the window: the pass consumes them (their stamps
    land), and every EP-reachable claim stays belief-less — so the block must
    show a non-zero `no_belief`, and `last_pass` must carry no derived
    "uncovered" that could mask it.
    """
    ids = _chain(sdk, 3)                      # 3 EP-reachable claims
    islands = [_claim(sdk, f"island {i}") for i in range(5)]
    sdk._dirty_roots = set(islands)            # ONLY operator-less claims
    sdk.dream(mode="local", max_hops=1, require_calibration=False)
    p = _prop(sdk)
    last = p["last_pass"]
    assert last["affected"] >= 1               # the pass really ran over them
    assert p["ep_reachable_claims"] == 3
    assert p["operator_less_claims"] == 5
    assert p["unclassified_claims"] == 0
    # the reachable claims are still belief-less (except the chain head, which
    # carries the declared baseline), and the block says so
    assert p["belief_present"] == 1
    assert p["no_belief"] == 2
    assert "uncovered" not in last
    assert ids


def test_the_census_refuses_above_its_bound(sdk, monkeypatch):
    """#12116 review P2 — the census is O(points + edges) on a POLLED endpoint
    (measured ~65 ms per 1,000 live claims), so it is bounded and refuses with
    a NAMED reason instead of holding the dream executor.
    """
    _chain(sdk, 3)
    monkeypatch.setenv("TORTOISE_PROPAGATION_CENSUS_MAX_POINTS", "2")
    p = _prop(sdk)
    assert p["unavailable"] and "PropagationCensusRefused" in p["unavailable"]
    assert "TORTOISE_PROPAGATION_CENSUS_MAX_POINTS" in p["unavailable"]
    assert p["no_belief"] is None      # refused, NOT zero
    monkeypatch.setenv("TORTOISE_PROPAGATION_CENSUS_MAX_POINTS", "1000")
    assert _prop(sdk)["no_belief"] == 2      # the census runs again


def test_the_census_bound_cannot_be_disabled_by_a_junk_value(sdk, monkeypatch):
    """A malformed or non-positive override RESOLVES to the default bound — it
    does not remove it (#12138 §5).

    ⛔ The first version asserted only `unavailable is None`, so a mutation that
    turned a junk value into a DISABLING one (`sys.maxsize`) stayed green: the
    test never read `_propagation_census_cap()`, so the bound could be gone with
    nothing to notice. The resolved bound is therefore asserted BY VALUE for
    every junk spelling, and then asserted to be LIVE by refusing at a lowered
    cap — a `_propagation_census_cap()` that returned `sys.maxsize` fails both.
    """
    _chain(sdk, 2)
    env = "TORTOISE_PROPAGATION_CENSUS_MAX_POINTS"
    for junk in ("not-a-number", "0", "-5", "", "  "):
        monkeypatch.setenv(env, junk)
        assert (sdk._propagation_census_cap()
                == DEFAULT_PROPAGATION_CENSUS_MAX_POINTS), junk
        assert _prop(sdk)["unavailable"] is None, junk
    # …and the fallback did not disable the guard: above a lowered bound the
    # census still refuses.
    monkeypatch.setenv(env, "1")
    assert "PropagationCensusRefused" in (_prop(sdk)["unavailable"] or "")


def test_a_budget_zero_pass_is_recorded_not_absent(sdk):
    _chain(sdk, 2)
    sdk.dream(mode="local", budget=0, require_calibration=False)
    p = _prop(sdk)
    assert p["last_pass"] is not None
    assert p["last_pass_reason"] is None
    assert p["last_pass"]["affected"] == 0


def test_a_no_dirty_roots_pass_is_recorded_not_absent(sdk):
    _chain(sdk, 2)
    sdk.dream(mode="full", require_calibration=False)   # drains the backlog
    sdk.dream(mode="local", require_calibration=False)  # nothing left to do
    p = _prop(sdk)
    assert p["last_pass"] is not None
    assert p["last_pass"]["affected"] == 0


def test_a_silent_no_op_pass_records_its_reach(sdk, monkeypatch):
    """`_guard_dream_progress` records metrics and then RAISES on the silent
    no-op; recording reach after it would report "no pass ran" for the worst
    reach there is."""
    from tortoise.exceptions import DreamNoOpError

    _chain(sdk, 2)
    dreamer = sdk._get_dreamer()
    monkeypatch.setattr(sdk, "_count_ep_factors", lambda window: 1)
    # `dream()` re-stamps `_last_belief_write_count` itself, so the zeroing has
    # to happen AFTER the real run and BEFORE the guard reads it.
    real = type(dreamer).dream

    def wrapped(*a, **k):
        out = real(dreamer, *a, **k)
        dreamer._last_belief_write_count = 0
        return out

    monkeypatch.setattr(dreamer, "dream", wrapped)
    with pytest.raises(DreamNoOpError):
        sdk.dream(mode="local", require_calibration=False)
    p = _prop(sdk)
    assert p["last_pass"] is not None
    assert p["last_pass_reason"] is None
    assert p["last_pass"]["mode"] == "local"


def test_the_pass_reach_is_recorded_without_a_graph_read(sdk, monkeypatch):
    """ONE closure BFS per local pass, and no propagation read from inside
    `dream()`.

    #12116 review A-P1: an earlier version called `_window_closure` a SECOND
    time (leaving the original in place) — the pass then paid the most
    expensive read twice on the write path, against D2-5's ≤500 ms embedded
    budget. A "was a new helper called?" check could not see that, so this
    counts CALLS.
    """
    _chain(sdk, 3)
    calls = {"propagation": 0, "closure": 0}
    real_closure = sdk._window_closure

    def counting_closure(window, max_hops):
        calls["closure"] += 1
        return real_closure(window, max_hops)

    def counted():
        calls["propagation"] += 1
        raise AssertionError("dream() must not read the propagation block")

    monkeypatch.setattr(sdk, "_window_closure", counting_closure)
    monkeypatch.setattr(sdk, "_propagation_counts", counted)
    sdk.dream(mode="local", require_calibration=False)
    assert calls["propagation"] == 0
    assert calls["closure"] == 1
    assert sdk._dream_metrics["last_pass_reach"]["affected"] >= 1


def test_stale_first_computes_the_closure_once(sdk, monkeypatch):
    """The same call-count control on the stale-first arm."""
    ids = _chain(sdk, 3)
    sdk._dirty_roots = {ids[0]}
    calls = {"closure": 0}
    real_closure = sdk._window_closure

    def counting_closure(window, max_hops):
        calls["closure"] += 1
        return real_closure(window, max_hops)

    monkeypatch.setattr(sdk, "_window_closure", counting_closure)
    sdk.dream(mode="stale-first", require_calibration=False)
    assert calls["closure"] == 1


def test_the_full_pass_computes_the_closure_once(sdk, monkeypatch):
    """The same call-count control on the full arm.

    #12116 review A-P1 named ``local`` and ``full``; full reaches its
    denominator through ``_reachable_claim_count`` -> ``_window_closure``, so a
    second closure for the reach record would double the most expensive read on
    the whole-graph write path — exactly what the ``local`` and ``stale-first``
    controls beside this one forbid on their arms.
    """
    _chain(sdk, 3)
    calls = {"closure": 0}
    real_closure = sdk._window_closure

    def counting_closure(window, max_hops):
        calls["closure"] += 1
        return real_closure(window, max_hops)

    monkeypatch.setattr(sdk, "_window_closure", counting_closure)
    sdk.dream(mode="full", require_calibration=False)
    assert calls["closure"] == 1


def test_the_stamp_ratio_and_the_block_share_one_query(sdk, monkeypatch):
    """#12116 A-P2: `coverage_pct` and the block's `stamped`/`graph_live_claims`
    come from ONE query (`_stamp_counts`), so they cannot drift apart."""
    _chain(sdk, 3)
    _islands(sdk, 2)
    h = sdk.dream_health_check()
    prop = h["propagation"]
    assert h["coverage_pct"] == pytest.approx(
        prop["stamped"] / prop["graph_live_claims"])
    inner = sdk._get_proj()
    queries: list[str] = []

    class _G:
        def __init__(self, g):
            self._g = g

        def query(self, q, **kw):
            queries.append(q)
            return self._g.query(q, **kw)

    class _P:
        def __init__(self, p):
            self._p = p
            self.g = _G(p.g)

    monkeypatch.setattr(sdk, "_get_proj", lambda: _P(inner))
    sdk._metrics_coverage()
    assert len(queries) == 1        # was TWO hand-copied queries before
    assert "sum(" in queries[0]


def test_the_health_read_computes_the_stamp_pair_once(sdk, monkeypatch):
    """#12116 review A-P2 (WHOLE-read half): ONE health call reads the
    live/stamped aggregate ONCE.

    The helper-level test above cannot see this class: `dream_health_check`
    called `_metrics_coverage` TWICE and the `propagation` block a THIRD time,
    so the polled `GET /v1/dream/health` computed one aggregate three times
    (measured 5 graph queries per call) while every helper test stayed green.
    R7's bound covered only the new helper, so the endpoint grew unnoticed.

    This counts the WHOLE read: exactly one stamp-pair `sum(... lastDreamedAt)`
    among the three aggregate reads (stamp pair, edged census, reach pair — the
    operator-less term is DERIVED from the census since round 2, not a fourth
    read).
    """
    _chain(sdk, 3)
    _islands(sdk, 2)
    inner = sdk._get_proj()
    queries: list[str] = []

    class _G:
        def __init__(self, g):
            self._g = g

        def query(self, q, **kw):
            queries.append(q)
            return self._g.query(q, **kw)

    class _P:
        def __init__(self, p):
            self._p = p
            self.g = _G(p.g)

    monkeypatch.setattr(sdk, "_get_proj", lambda: _P(inner))
    h = sdk.dream_health_check()
    # The stamp-pair aggregate is the one whose SUM keys on `lastDreamedAt` (the
    # reach query also carries a `sum(CASE … has_belief …)`, so `sum(` alone
    # would match both and could not discriminate).
    stamp_reads = [q for q in queries if "lastdreamedat" in q.lower()]
    assert len(stamp_reads) == 1, stamp_reads      # NOT 3
    assert len(queries) == 3        # stamp pair + edged census + reach pair
    # the single read is what BOTH surfaces report, so they cannot disagree
    prop = h["propagation"]
    assert h["coverage_pct"] == pytest.approx(
        prop["stamped"] / prop["graph_live_claims"])


def test_a_failed_stamp_read_is_replayed_not_retried(sdk, monkeypatch):
    """One failing read, both postures, no second attempt.

    `coverage_pct` keeps its pre-existing 0.0 (it feeds the alarm) while the
    `propagation` block reports `unavailable` with the reason
    (BOUNDED-OPERATION-CONTRACT §1a). Hoisting the read must not collapse those
    two meanings into one — nor pay the failing read twice per poll.
    """
    _chain(sdk, 2)
    calls = {"n": 0}

    def boom():
        calls["n"] += 1
        raise RuntimeError("graph went away")

    monkeypatch.setattr(sdk, "_stamp_counts", boom)
    h = sdk.dream_health_check()
    assert calls["n"] == 1          # one attempt, reported twice
    assert h["coverage_pct"] == 0.0
    p = h["propagation"]
    assert p["unavailable"] and "graph went away" in p["unavailable"]
    assert p["graph_live_claims"] is None
    assert p["no_belief"] is None


def test_an_edged_but_unreachable_claim_is_not_invisible(sdk):
    """#12116 review C-P2: the three counts PARTITION the live :Point
    population. A live claim whose only IMPL edge lands on a DRAFT point is
    neither reachable nor operator-less; without `unclassified_claims` the block
    reported `no_belief: 0` for a graph whose only live claim had no belief and
    could never get one — an under-report its own docstring forbids.
    """
    live = _claim(sdk, "edged only to a draft")
    draft = sdk.create_point("statement", "draft neighbour", dedup=False,
                             status="draft")["id"]
    sdk.create_direct_edge("IMPL", live, draft)
    p = _prop(sdk)
    assert p["graph_live_claims"] == 1
    assert p["ep_reachable_claims"] == 0
    assert p["operator_less_claims"] == 0
    assert p["unclassified_claims"] == 1        # visible, not silently dropped
    assert (p["ep_reachable_claims"] + p["operator_less_claims"]
            + p["unclassified_claims"] == p["graph_live_claims"])


def test_the_three_counts_partition_the_live_points(sdk):
    """The partition is exact on a mixed graph — and no term is clamped.

    ⚠️ #12116 review P3: `operator_less = graph_live - points_edged` and
    `unclassified = points_edged - reachable`, so the identity alone is a
    TAUTOLOGY. The numbers must therefore be checked against queries written
    independently here, plus the non-negativity the derivation relies on.
    """
    _chain(sdk, 3)
    _islands(sdk, 2)
    live = _claim(sdk, "edged only to a draft")
    draft = sdk.create_point("statement", "draft neighbour", dedup=False,
                             status="draft")["id"]
    sdk.create_direct_edge("IMPL", live, draft)
    p = _prop(sdk)
    g = sdk._get_proj().g
    edged = int(g.query(
        "MATCH (n:Point)-[:IMPL|NAND]-() "
        "WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "AND (n.status IS NULL OR n.status <> 'draft') "
        "WITH DISTINCT n RETURN count(n)").result_set[0][0])
    assert p["operator_less_claims"] == p["graph_live_claims"] - edged
    assert p["unclassified_claims"] == edged - p["ep_reachable_claims"]
    assert p["operator_less_claims"] >= 0
    assert p["unclassified_claims"] >= 0
    assert (p["ep_reachable_claims"] + p["operator_less_claims"]
            + p["unclassified_claims"] == p["graph_live_claims"])


def test_untested_operator_less_population_matches_trivial_stamp(sdk):
    """#12116 A-P3: the block's operator-less count IS `_trivial_stamp`'s
    population — a VALUE agreement, checked against a query built from the
    CONSTANT ITSELF.

    ⛔ Two earlier versions were wrong in opposite directions. The first made
    this a shared SPELLING inside the census and was reverted in review P2:
    that constant's negated pattern was the most expensive read in the census.
    The second compared against a THIRD hand-copied literal (#12138 §6), so a
    drift in `TRIVIAL_STAMP_PREDICATE` left this GREEN. ⛔ And building the
    query from the constant is NOT sufficient by itself: a fixture whose only
    draft Point is operator-mediated cannot observe the STATUS conjunct at all
    (#12138 review P2 — measured: `'draft'` → `'archived'` and deleting the
    status conjunct both stayed green, because the draft is excluded by
    `NOT (n)-[:IMPL|NAND]-()` regardless of its status, and a fixture with no
    NAND edge cannot observe the NAND half either). So the fixture now carries
    the observables the constant's two vocabularies need: an UN-EDGED draft
    Point (status drift reddens) and a NAND-only-edged claim (narrowing
    `IMPL|NAND` to `IMPL` reddens).

    The census keeps DERIVING its number (`graph_live - points_edged`, a
    positive traversal): the negated pattern is a production cost, and a
    handful of nodes is the only place it is affordable.
    """
    _chain(sdk, 3)
    _islands(sdk, 2)
    live = _claim(sdk, "edged only to a draft")
    draft = sdk.create_point("statement", "draft neighbour", dedup=False,
                             status="draft")["id"]
    sdk.create_operator("IMPL", live, [draft])
    # The STATUS observable: a draft with NO edge, so only the status conjunct
    # can keep it out of the constant's population (a widened status vocabulary
    # would count it and redden the equality below).
    sdk.create_point("statement", "un-edged draft", dedup=False,
                     status="draft")
    # The NAND observable: a claim whose only `IMPL|NAND` edge is a NAND one, so
    # narrowing the constant's edge alternation to `IMPL` counts it and reddens.
    a, b = _islands(sdk, 2)
    sdk.create_operator("NAND", a, [b])
    prop = _prop(sdk)
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE "
        f"{TRIVIAL_STAMP_PREDICATE} "
        "RETURN count(n)").result_set
    # The block's number carries the SAME VALUE as `_trivial_stamp`'s own
    # population, measured through the constant's own text.
    assert prop["operator_less_claims"] == int(rows[0][0]) == 2
    # A draft-edged claim is still edged, so it is NOT in this population — the
    # count is not "claims with no edges at all" by accident.
    assert prop["operator_less_claims"] < prop["graph_live_claims"]


# ── nothing else moved ─────────────────────────────────────────────────────


def test_dream_key_set_is_unchanged_by_this_change(sdk):
    """I1: the local key-set is pinned exactly (`tests/test_epic903_modes.py`)."""
    _chain(sdk, 2)
    r = sdk.dream(mode="local", require_calibration=False)
    assert set(r.keys()) == {"mode", "iterations", "converged",
                             "affected_claims", "budget_used", "coverage"}


def test_stamp_coverage_keeps_its_value(sdk):
    """`coverage_pct` = stamped / live, EXACTLY (the alarm's input)."""
    _chain(sdk, 3)
    _islands(sdk, 1)
    sdk.dream(mode="full", require_calibration=False)
    assert sdk.dream_health_check()["coverage_pct"] == 1.0
    sdk.create_point("statement", "fresh island", dedup=False, status="live")
    assert sdk.dream_health_check()["coverage_pct"] == pytest.approx(4 / 5)


def test_stamp_coverage_and_belief_coverage_can_disagree(sdk):
    """The two numbers measure different things — which is exactly why the
    issue's "coverage=1.0" could not answer its own question."""
    _chain(sdk, 3)
    _islands(sdk, 4)
    sdk.dream(mode="full", require_calibration=False)
    h = sdk.dream_health_check()
    assert h["coverage_pct"] == 1.0
    assert h["propagation"]["belief_coverage_pct"] == 1.0
    # …and now break the agreement: an unstamped, belief-less live claim.
    sdk.create_point("statement", "brand new", dedup=False, status="live")
    h2 = sdk.dream_health_check()
    assert h2["coverage_pct"] < 1.0
    assert h2["propagation"]["belief_coverage_pct"] == 1.0  # reachable set covered


def test_the_alarm_verdict_is_unchanged_by_the_new_fields(sdk):
    """The propagation block must not move the alarm: a healthy idle graph
    stays calm, and a backlog with real output stays calm (the #3139
    positive-control rule)."""
    _chain(sdk, 3)
    sdk.dream(mode="full", require_calibration=False)
    h = sdk.dream_health_check()
    assert h["alarm_verdict"] is False
    assert h["propagation"]["no_belief"] == 0
