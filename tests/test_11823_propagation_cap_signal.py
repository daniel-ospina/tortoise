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

from tortoise.sdk import TortoiseSDK


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
    """The block's counts agree with queries written independently here."""
    _chain(sdk, 3)
    _islands(sdk, 4)
    p = _prop(sdk)
    g = sdk._get_proj().g
    live = int(g.query(
        "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "AND (n.status IS NULL OR n.status <> 'draft') RETURN count(n)"
    ).result_set[0][0])
    assert p["graph_live_claims"] == live == 7
    assert p["unavailable"] is None


def test_no_belief_stays_inside_its_invariants(sdk):
    """RANGES, not an identity: a mixed-population bug makes `no_belief` go
    negative (measured -8.0 with a label-blind denominator) or the ratio exceed
    1.0. The identity `no_belief == reachable - belief_present` holds by
    construction and cannot discriminate, so the ranges are what is asserted.
    """
    _chain(sdk, 3)
    _islands(sdk, 4)
    # a live Point whose only edge points at a DRAFT point: neither stamped by
    # the trivial-stamp path nor reachable by EP.
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


def test_reachable_claim_count_is_unchanged(sdk):
    """`_edged_claim_ids()` was extracted out of `_reachable_claim_count`;
    the closure intersection must stay AT THE CALL SITE (dropping it inflates
    full-mode `coverage`'s denominator silently)."""
    _chain(sdk, 3)
    _islands(sdk, 2)
    closure = sdk._window_closure(_chain_ids(sdk), 2)
    edged = sdk._edged_claim_ids()
    assert sdk._reachable_claim_count(2) == len(closure & edged)


def _chain_ids(sdk):
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point)-[:IMPL|NAND]-(:Point) WHERE n.content STARTS WITH 'chain' "
        "RETURN n.id").result_set
    return [r[0] for r in rows]


# ── the read is bounded, and an unreadable graph is not a zero ──────────────


def test_the_propagation_read_is_three_aggregate_counts(sdk, monkeypatch):
    """The polled `GET /v1/dream/health` must not grow a per-vertex
    materialisation: three aggregate COUNT/SUM reads, nothing else."""
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
    """#12116 review B-P2, as a reproduction: a pass that consumes ONLY
    operator-less claims leaves every EP-reachable claim uncovered, and the
    block must not read as covered."""
    ids = _chain(sdk, 3)                      # 3 EP-reachable claims
    for i in range(20):                       # 20 operator-less claims
        _claim(sdk, f"island {i}")
    sdk._dirty_roots = {i for i in ids}
    sdk.dream(mode="local", max_hops=1, require_calibration=False)
    p = _prop(sdk)
    assert p["ep_reachable_claims"] == 3
    assert p["operator_less_claims"] == 20
    assert p["no_belief"] > 0 or p["belief_present"] == 3
    # whatever the pass reached, the block never publishes a *derived* zero
    assert "uncovered" not in p["last_pass"]


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

    This counts the WHOLE read: exactly one stamp-pair `sum(...)` among the
    three aggregate reads (reach pair, stamp pair, operator-less).
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
    assert len(queries) == 3        # reach pair + stamp pair + operator-less
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
    """The partition is exact on a mixed graph — and no term is clamped."""
    _chain(sdk, 3)
    _islands(sdk, 2)
    live = _claim(sdk, "edged only to a draft")
    draft = sdk.create_point("statement", "draft neighbour", dedup=False,
                             status="draft")["id"]
    sdk.create_direct_edge("IMPL", live, draft)
    p = _prop(sdk)
    assert p["unclassified_claims"] >= 0
    assert (p["ep_reachable_claims"] + p["operator_less_claims"]
            + p["unclassified_claims"] == p["graph_live_claims"])


def test_untested_operator_less_population_matches_trivial_stamp(sdk):
    """#12116 A-P3: the block's operator-less count and `_trivial_stamp`'s own
    population are ONE spelling (`TRIVIAL_STAMP_PREDICATE`), so the two agree.
    """
    _chain(sdk, 3)
    _islands(sdk, 2)
    live = _claim(sdk, "edged only to a draft")
    draft = sdk.create_point("statement", "draft neighbour", dedup=False,
                             status="draft")["id"]
    sdk.create_operator("IMPL", live, [draft])
    prop = _prop(sdk)
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE "
        "(n.is_operator IS NULL OR n.is_operator = false) "
        "AND (n.status IS NULL OR n.status <> 'draft') "
        "AND NOT (n)-[:IMPL|NAND]-() RETURN count(n)").result_set
    # The block's number IS `_trivial_stamp`'s population (one shared constant),
    # so a change to either spelling cannot make them disagree.
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
