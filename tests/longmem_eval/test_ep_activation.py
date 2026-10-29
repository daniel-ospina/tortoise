"""Track C (#3011) — EP activation tests.

Hermetic: no DB, no network. ``read_confidence`` is pure; ``activate_beliefs``
is driven through a stub SDK that records the Cypher it receives and the EP
call it is asked to make.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.longmem_eval import ep_activation as epa  # noqa: E402, RUF100
from tools.longmem_eval import measurement_posture as mp  # noqa: E402, RUF100

# ── read_confidence ────────────────────────────────────────────────────────

def test_read_confidence_uses_posterior():
    p = {"posterior_alpha": 3.0, "posterior_beta": 1.0}
    assert epa.read_confidence(p) == pytest.approx(0.75)


def test_read_confidence_absent_is_none_not_half():
    """#2598: an unmeasured point must NEVER render a fabricated 0.5."""
    assert epa.read_confidence({}) is None
    assert epa.read_confidence({}) != 0.5
    # explicit-None values are also absent (the property dict shape a graph
    # read returns when the props were never written)
    assert epa.read_confidence(
        {"posterior_alpha": None, "posterior_beta": None,
         "ep_alpha": None, "ep_beta": None}) is None


def test_read_confidence_prefers_posterior_over_ep():
    p = {"posterior_alpha": 3.0, "posterior_beta": 1.0,
         "ep_alpha": 9.0, "ep_beta": 1.0}
    # posterior 3/4 wins over prior mean 9/10
    assert epa.read_confidence(p) == pytest.approx(0.75)


def test_read_confidence_prefers_falsy_posterior_too():
    """Preference is by PRESENCE, not truthiness — a 0.0 posterior must not
    silently fall back to the ep_* prior (which would overstate belief)."""
    p = {"posterior_alpha": 0.0, "posterior_beta": 4.0,
         "ep_alpha": 9.0, "ep_beta": 1.0}
    assert epa.read_confidence(p) == pytest.approx(0.0)


def test_read_confidence_ep_only():
    """Only ep_* present (pre-EP or baseline'd shape) is a valid read."""
    p = {"ep_alpha": 1.0, "ep_beta": 3.0}
    assert epa.read_confidence(p) == pytest.approx(0.25)


def test_read_confidence_per_field_fallback():
    """A missing posterior_beta falls back to ep_beta, keeping the measured
    posterior_alpha — the per-field coalesce the canonical read uses."""
    p = {"posterior_alpha": 3.0, "ep_beta": 1.0, "ep_alpha": 99.0}
    assert epa.read_confidence(p) == pytest.approx(0.75)


def test_read_confidence_partial_state_is_none():
    """EP persists alpha+beta together; a lone parameter is malformed state,
    not a measurement — returning alpha/(alpha+0)=1.0 would fabricate."""
    assert epa.read_confidence({"posterior_alpha": 3.0}) is None
    assert epa.read_confidence({"ep_beta": 2.0}) is None
    assert epa.read_confidence({"posterior_alpha": 3.0,
                                "posterior_beta": None,
                                "ep_beta": None}) is None


def test_read_confidence_degenerate_and_garbage():
    assert epa.read_confidence({"posterior_alpha": 0.0,
                                "posterior_beta": 0.0}) is None
    assert epa.read_confidence({"posterior_alpha": "n/a",
                                "posterior_beta": 1.0}) is None
    assert epa.read_confidence(None) is None


# ── activate_beliefs (stub SDK) ────────────────────────────────────────────

class _Result:
    def __init__(self, result_set):
        self.result_set = result_set


class _FakeGraph:
    """Records every Cypher and answers by matching the (normalized) query."""

    def __init__(self, draft_ids, op_map, total, with_ep, operators=0,
                 factor_edges=None, ep_rows=None):
        self.draft_ids = list(draft_ids)
        self.op_map = dict(op_map)
        self.total = total
        self.with_ep = with_ep
        # #10137: graph operator census + direct factor-edge census (the two
        # cells the STRUCTURAL verdict is corroborated from), and the persisted
        # EP state of every claim that carries any (the off-neutral census's
        # input). ``factor_edges=None`` models a census the stub cannot read —
        # the UNKNOWN shape, never a fabricated 0.
        self.operators = operators
        self.factor_edges = factor_edges
        self.ep_rows = list(ep_rows or [])
        self.queries: list[tuple[str, dict]] = []

    def query(self, cypher, params=None):
        params = params or {}
        self.queries.append((cypher, params))
        c = " ".join(cypher.split())
        if "SET n.status = 'live'" in c:
            return _Result([])
        if "SET o.status = 'live'" in c:
            return _Result([])
        if "RETURN DISTINCT o.id" in c:
            out = []
            for cid in params.get("ids", []):
                for oid in self.op_map.get(cid, []):
                    out.append([oid])
            return _Result(out)
        if "RETURN count(o)" in c:            # #10137 COUNT_OPERATORS
            return _Result([[self.operators]])
        if "RETURN count(r)" in c:            # #10137 COUNT_FACTOR_EDGES
            return _Result([[self.factor_edges]])
        if "n.posterior_alpha, n.posterior_beta" in c:   # #10137 states
            return _Result(self.ep_rows)
        if "count(n)" in c and "posterior_alpha IS NOT NULL" in c:
            return _Result([[self.with_ep]])
        if "count(n)" in c:
            return _Result([[self.total]])
        if "RETURN n.id" in c and "is_operator IS NULL" in c:
            return _Result([[pid] for pid in self.draft_ids])
        return _Result([])

    def matching(self, needle):
        return [(c, p) for c, p in self.queries if needle in c]


class _FakeProj:
    def __init__(self, graph):
        self.g = graph


class _FakeSDK:
    def __init__(self, graph, namespace="eval-ns"):
        self._namespace = namespace
        self._proj = _FakeProj(graph)
        self.events: list[tuple[str, dict]] = []
        self.dream_calls: list[dict] = []
        self.dirty_calls: list[list[str]] = []

    def _get_proj(self):
        return self._proj

    def get_point(self, pid):
        return {"id": pid, "status": "live"}

    def _emit_event(self, type_, **kwargs):
        self.events.append((type_, kwargs))

    def _mark_dirty(self, point_ids):
        self.dirty_calls.append(list(point_ids))

    def dream(self, **kwargs):
        self.dream_calls.append(kwargs)
        return {"mode": "full"}


def _make(draft_ids, op_map=None, total=None, with_ep=0, namespace="eval-ns",
          operators=0, factor_edges=None, ep_rows=None):
    graph = _FakeGraph(draft_ids, op_map or {},
                       total=len(draft_ids) if total is None else total,
                       with_ep=with_ep, operators=operators,
                       factor_edges=factor_edges,
                       ep_rows=ep_rows)
    return _FakeSDK(graph, namespace=namespace), graph


def test_activate_promotes_points_and_operators_then_runs_ep():
    sdk, graph = _make(["c1", "c2", "c3"],
                       op_map={"c1": ["op1"], "c2": ["op1"], "c3": ["op2"]},
                       with_ep=3)

    manifest = epa.activate_beliefs(sdk, namespace="eval-ns")

    # promotion query issued for the draft points + each draft operator
    assert len(graph.matching("SET n.status = 'live'")) == 1
    assert len(graph.matching("SET o.status = 'live'")) == 2
    # EP invoked through the whole-graph write surface
    assert len(sdk.dream_calls) == 1
    assert sdk.dream_calls[0]["mode"] == "full"
    assert sdk.dream_calls[0]["require_calibration"] is False
    # rebuild-durable events emitted for every promotion
    kinds = [e[0] for e in sdk.events]
    assert kinds.count("PointPromoted") == 3
    assert kinds.count("OperatorPromoted") == 2
    # dirty-marking mirrors the capture block
    assert sdk.dirty_calls == [["c1", "c2", "c3"]]
    assert manifest["points_promoted"] == 3
    assert manifest["operators_promoted"] == 2
    assert manifest["ep_ran"] is True
    assert manifest["ep_engine"] == "dream:full"
    assert manifest["points_with_ep"] == 3
    assert manifest["points_unmeasured"] == 0
    assert isinstance(manifest["duration_ms"], int)


def test_activate_batches_promotion_queries():
    """Thousands of eval points must not ride one Cypher statement."""
    sdk, graph = _make([f"c{i}" for i in range(5)], with_ep=5)
    epa.activate_beliefs(sdk, batch_size=2)
    # ceil(5/2) = 3 point-promotion batches
    assert len(graph.matching("SET n.status = 'live'")) == 3


def test_activate_operator_deduped_across_batches():
    sdk, graph = _make(["c1", "c2", "c3"],
                       op_map={"c1": ["op1"], "c2": ["op1"], "c3": ["op1"]})
    manifest = epa.activate_beliefs(sdk, batch_size=1)
    # op1 is incident to all three chunks but goes live exactly once
    assert len(graph.matching("SET o.status = 'live'")) == 1
    assert manifest["operators_promoted"] == 1


def test_activate_counts_unmeasured():
    sdk, _ = _make(["c1", "c2", "c3", "c4"], total=4, with_ep=1)
    manifest = epa.activate_beliefs(sdk)
    assert manifest["points_with_ep"] == 1
    assert manifest["points_unmeasured"] == 3


def test_activate_no_drafts_still_runs_ep():
    """An already-promoted graph is re-activated idempotently — no promotion
    query is issued, but EP still runs (posteriors may be stale/absent)."""
    sdk, graph = _make([], total=10, with_ep=0)
    manifest = epa.activate_beliefs(sdk)
    assert manifest["points_promoted"] == 0
    assert graph.matching("SET n.status = 'live'") == []
    assert len(sdk.dream_calls) == 1
    assert manifest["points_unmeasured"] == 10


def test_activate_ep_failure_is_fail_open_and_reported():
    sdk, _ = _make(["c1"])

    def boom(**kwargs):
        raise RuntimeError("EP exploded")

    sdk.dream = boom  # type: ignore[assignment]
    manifest = epa.activate_beliefs(sdk)
    assert manifest["ep_ran"] is False
    # promotion writes still committed — the manifest is the surface that
    # tells the caller the pass failed
    assert manifest["points_promoted"] == 1
    # #10137 review 14 (P2): the DIAGNOSIS rides on the manifest. It used to
    # reach only the log, so the run summary's `failed`/`errors` cells — keyed
    # on `error`, the key `run.py`'s outer handler already writes — read a run
    # where every pass raised as `failed: 0` / `errors: []`. The two failure
    # shapes are now one key.
    assert manifest["error"] == "RuntimeError: EP exploded"


def test_a_successful_pass_carries_no_error_cell_value():
    """The success path sets `error: None`, not an absent key.

    Both matter: `_classify_question` reads `manifest.get("error")` (absent and
    `None` are both falsy, so either would classify right), but the KEY being
    present on every manifest is what makes `{"error": …}` a shape a reader can
    rely on rather than a key only the failure path has.
    """
    sdk, _ = _make(["c1"])
    manifest = epa.activate_beliefs(sdk)
    assert manifest["ep_ran"] is True
    assert "error" in manifest
    assert manifest["error"] is None


def test_activate_namespace_mismatch_refuses():
    sdk, _ = _make(["c1"], namespace="graph-a")
    with pytest.raises(ValueError, match="namespace mismatch"):
        epa.activate_beliefs(sdk, namespace="graph-b")


def test_activate_namespace_none_skips_guard():
    sdk, _ = _make(["c1"], namespace="graph-a")
    manifest = epa.activate_beliefs(sdk, namespace=None)
    assert manifest["points_promoted"] == 1


def test_activate_rejects_bad_batch_size():
    sdk, _ = _make(["c1"])
    with pytest.raises(ValueError, match="batch_size"):
        epa.activate_beliefs(sdk, batch_size=0)


# ── #10137: the CONTRIBUTION census (coverage is not contribution) ─────────
#
# Coverage (``points_with_ep``) answers "does this claim carry EP state". It
# cannot answer "did EP MOVE it": an IMPL-only graph over the unmeasured
# Beta(1,1) prior is a fixed point at exactly 0.50, so ``points_with_ep ==
# points`` while the belief layer contributed nothing. These four cells are
# that second half, and they are what makes the population measurable rather
# than assertable.

@pytest.mark.parametrize("row, off", [
    # a genuine move (the #10137 reproduction's baseline'd source)
    ((("c1"), 6.0, 2.0, None, None), True),
    # the neutral fixed point of IMPL over Beta(1,1) — 0.5 exactly
    (("c2", 1.5, 1.5, None, None), False),
    # floating-point noise around the fixed point is still neutral
    (("c3", 1.2627, 1.2627, None, None), False),
    # NAND over uniform priors moves OFF 0.5 (a real measurement of conflict)
    (("c4", 0.4923, 1.0077, None, None), True),
    # ep_* only (a baseline'd point with no posterior yet) — the canonical
    # read uses it, so the census must too
    (("c5", None, None, 1.0, 3.0), True),
    # no usable state at all is UNMEASURED, not a contribution
    (("c6", None, None, None, None), False),
    # a lone/degenerate parameter is malformed state, not a measurement
    (("c7", 2.0, None, None, None), False),
    (("c8", 0.0, 0.0, None, None), False),
])
def test_off_neutral_counts_by_the_canonical_read(row, off):
    assert epa.points_off_neutral([list(row)]) == (1 if off else 0)


def test_off_neutral_agrees_with_read_confidence_over_a_population():
    """The census is derived FROM ``read_confidence``, not re-implemented in
    Cypher — so the two can never disagree on the per-field coalesce, the
    malformed pair, or the degenerate (alpha+beta<=0) refusal."""
    rows = [
        ["c1", 6.0, 2.0, None, None],
        ["c2", 1.2627, 1.2627, None, None],
        ["c3", None, None, 3.0, 1.0],
        ["c4", 2.0, None, 9.0, 1.0],
        ["c5", 0.0, 0.0, None, None],
        ["c6", None, None, None, None],
        ["c7", "n/a", 1.0, None, None],
    ]
    expected = sum(
        1 for r in rows
        if (m := epa.read_confidence(
            {"posterior_alpha": r[1], "posterior_beta": r[2],
             "ep_alpha": r[3], "ep_beta": r[4]})) is not None
        and abs(m - epa.NEUTRAL_MEAN) > epa.NEUTRAL_EPS)
    assert epa.points_off_neutral(rows) == expected
    # sanity: the population really does contain both sides (c1 0.75, c3 0.75,
    # c4's per-field coalesce 2/3 — and c2 stays neutral)
    assert epa.points_off_neutral(rows) == 3


def test_an_absent_result_set_is_unknown_not_a_measured_zero(monkeypatch):
    """RED for the ABSENT-ROW arm of ``_scalar_or_none``, which no other test
    reaches: both unread-census tests make the query RAISE (the ``except`` arm),
    and the stub's ``factor_edges=None`` is a falsy CELL (the ``TypeError``
    arm), so returning ``0`` for an empty result set left every test green
    (measured: 48 passed).

    The consequence is the one the module exists to prevent: ``operators: 0``
    plus ``factor_edges: 0`` off a census that was never read fabricates the
    STRUCTURAL ``no_operators`` verdict — the single reason in
    ``INERT_REASONS_STRUCTURAL``, which tells a lane that extraction is the
    fault and nothing the arm does can ever help.
    """
    sdk, graph = _make(["c1"], total=3, with_ep=0)
    real_query = graph.query

    def _query(cypher, params=None):
        flat = " ".join(cypher.split())
        if "RETURN count(o)" in flat or "RETURN count(r)" in flat:
            return _Result([])            # no row, WITHOUT raising
        return real_query(cypher, params)

    graph.query = _query
    manifest = epa.activate_beliefs(sdk)
    assert manifest["operators"] is None
    assert manifest["factor_edges"] is None

    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": manifest}],
        requested=True)
    assert summary["inert_questions"][0]["reason"] == mp.INERT_UNMEASURED
    assert summary["inert_questions"][0]["reason"] \
        not in mp.INERT_REASONS_STRUCTURAL


def test_a_non_finite_posterior_is_never_a_contribution():
    """``abs(nan - 0.5) > 1e-6`` is ``False``, so an undefined mean is kept out
    of the off-neutral (STATE) population only by the shape of that comparison.
    The explicit finiteness guard makes the exclusion a property of the VALUE,
    so a change to ``NEUTRAL_MEAN``/``NEUTRAL_EPS`` cannot silently reclassify a
    malformed posterior as an off-neutral claim.

    Reachable through the canonical read: ``inf/inf`` is ``nan``.

    ⚠️ Residual, tracked in #10210: this does NOT remove such a claim from the
    per-QUESTION ``neutral_questions`` population, which is derived from
    ``points_off_neutral == 0`` with ``points_with_ep > 0`` — "covered, and
    moved nothing" — because the census has no "state present but unusable"
    fact to report. That needs a new cell, not a predicate.
    """
    assert math.isnan(epa.read_confidence({"posterior_alpha": float("inf"),
                                           "posterior_beta": float("inf")}))
    rows = [["c1", float("nan"), 1.0, None, None],
            ["c2", float("inf"), float("inf"), None, None]]
    assert epa.off_neutral_ids(rows) == set()
    assert epa.points_off_neutral(rows) == 0


def test_an_id_less_row_cannot_be_counted_as_a_contribution():
    """RED: the ``if not pid`` arm had no pin — deleting it left the whole
    suite green (measured). An id-less row cannot be DIFFERENCED, so counting
    it publishes a contribution for a claim that cannot be named, and it then
    reconciles with nothing: ``""`` or ``"None"`` present after the pass but
    not before is credited to ``points_moved_off_neutral``.

    ``None`` and ``""`` both arrive from a projection that returned a short or
    null id cell, so both spellings are pinned.
    """
    rows = [["", 6.0, 2.0, None, None],
            [None, 6.0, 2.0, None, None],
            ["c1", 6.0, 2.0, None, None]]
    assert epa.off_neutral_ids(rows) == {"c1"}
    assert epa.points_off_neutral(rows) == 1
    # a row that is not even a sequence is skipped, not fatal (driver shape)
    assert epa.points_off_neutral([None, [], ["c1", 6.0, 2.0, None, None]]) == 1


def test_a_boolean_manifest_cell_is_unknown_never_a_count():
    """RED: the ``isinstance(value, bool)`` arm of ``_optional_int`` had no pin
    in either module — dropping it left 111 tests green (measured). Without it
    a JSON ``true`` publishes ``operators: 1``, a fabricated measurement in the
    one cell whose value decides the structural ``no_operators`` verdict, and
    ``False`` publishes a fabricated ``0`` that is worse: it asserts "this
    graph mints no operators" off a boolean.

    Both module copies are pinned, since each sums its own census cells.
    """
    assert epa._optional_int(True) is None
    assert epa._optional_int(False) is None
    assert mp._optional_int(True) is None
    assert mp._optional_int(False) is None
    # ...and the UNKNOWN reaches the run-level cell, with the unread count that
    # says the sum is partial.
    summary = mp.summarize_belief_activation(
        [{"question_id": "q1", "belief_activation": {
            "ep_ran": True, "points_with_ep": 0, "points_unmeasured": 1,
            "operators": True, "factor_edges": False}}],
        requested=True)
    assert summary["operators"] is None
    assert summary["operators_unread"] == 1
    assert summary["factor_edges"] is None
    assert summary["factor_edges_unread"] == 1


def test_the_finiteness_guard_is_load_bearing_for_an_infinite_mean(
        monkeypatch):
    """RED for the GUARD, not for NaN's comparison shape.

    ``abs(nan - 0.5) > eps`` is already ``False``, so the NaN rows above are
    excluded whether or not ``math.isfinite`` is there — deleting the guard
    left every test green (measured). An INFINITE mean is the case that
    discriminates: ``abs(inf - 0.5) > eps`` is ``True``, so only the guard keeps
    it out of the CONTRIBUTION population. ``read_confidence`` cannot return
    ``inf`` for a reachable stored state today (``a/(a+b)`` with ``a+b > 0``;
    ``inf/inf`` is NaN), which is exactly why the guard needs a pinned case
    rather than an argument: it is the invariant against a future numeric
    change, and an unobservable guard is one a refactor deletes.
    """
    monkeypatch.setattr(epa, "read_confidence", lambda _props: float("inf"))
    rows = [["c1", 6.0, 2.0, None, None]]     # would read off-neutral at ±inf
    assert epa.off_neutral_ids(rows) == set()
    assert epa.points_off_neutral(rows) == 0


def test_a_claim_state_read_that_saw_no_rows_is_unknown_not_a_zero():
    """RED: a state read that matched NOTHING published "the belief layer ran
    and moved nothing" on every graph.

    ``_SELECT_CLAIM_EP_STATES`` and ``_COUNT_POINTS_WITH_EP`` carry the SAME
    WHERE clause, so a state read returning ZERO rows while the coverage census
    counted claims carrying EP state is provably a BROKEN read (a wrong
    predicate, a projection that dropped the cells) — never a measured zero. It
    is the silent-zero hazard this census exists to close, one layer below the
    counts, where ``_scalar_or_none`` already guards the single-cell reads.
    """
    sdk, _graph = _make(["c1"], operators=2, with_ep=3, ep_rows=[])
    manifest = epa.activate_beliefs(sdk)
    assert manifest["points_with_ep"] == 3          # coverage WAS measured
    assert manifest["points_off_neutral"] is None   # the state read was not
    assert manifest["points_moved_off_neutral"] is None


def test_a_broken_pre_pass_baseline_never_credits_the_pass_with_a_prior():
    """RED: the PRE-pass baseline read had no coverage cross-check, so a
    non-raising EMPTY result set there made every off-neutral claim look NEWLY
    off-neutral — publishing ``points_moved_off_neutral: 1`` for a claim that
    was already off the neutral mean before the pass ran.

    That is the defect the cell exists to prevent ("a bare off-neutral count
    would credit the pass with a prior it did not create") in the opposite
    direction, and it is invisible downstream: the post-pass guard cannot see
    it, because the post-pass read is intact. The baseline is the read where a
    silent zero is worst, so it gets the same rule.

    Measured on the previous head: honest reads → ``(1, 0)``; broken first read
    → ``(1, 1)``.
    """
    rows = [["pre", 6.0, 2.0, None, None]]   # off-neutral BEFORE the pass
    sdk, graph = _make(["pre"], with_ep=1, ep_rows=rows)
    honest = epa.activate_beliefs(sdk)
    assert honest["points_off_neutral"] == 1
    assert honest["points_moved_off_neutral"] == 0

    sdk, graph = _make(["pre"], with_ep=1, ep_rows=rows)
    real_query = graph.query
    calls = {"n": 0}

    def _query(cypher, params=None):
        flat = " ".join(cypher.split())
        if "n.posterior_alpha, n.posterior_beta" in flat:
            calls["n"] += 1
            if calls["n"] == 1:            # the PRE-pass baseline only
                return _Result([])          # empty WITHOUT raising
        return real_query(cypher, params)

    graph.query = _query
    broken = epa.activate_beliefs(sdk)
    assert broken["points_with_ep"] == 1              # coverage WAS read
    assert broken["points_off_neutral"] == 1          # post-pass read intact
    assert broken["points_moved_off_neutral"] is None  # baseline UNKNOWN


def test_an_unread_coverage_count_is_not_consent_for_an_empty_baseline():
    """RED: the baseline guard's cross-check read could itself fail, and an
    UNKNOWN count is falsy — so ``and _census(...)`` read "could not read the
    coverage" as "there is no coverage", the guard did not fire, and the empty
    baseline stood. The published ``points_moved_off_neutral`` then credited
    this pass with EVERY pre-existing off-neutral claim.

    A census that cannot be read proves NOTHING, so it is the same verdict as a
    disagreement — the baseline is unproven and the contribution is UNKNOWN.
    The post-pass read applies the same rule for an unread ``with_ep``.
    """
    rows = [["pre", 6.0, 2.0, None, None]]    # off-neutral BEFORE the pass
    sdk, graph = _make(["pre"], with_ep=1, ep_rows=rows)
    real_query = graph.query
    state = {"rows": 0, "cov": 0}

    def _query(cypher, params=None):
        flat = " ".join(cypher.split())
        if "n.posterior_alpha, n.posterior_beta" in flat:
            state["rows"] += 1
            if state["rows"] == 1:            # the PRE-pass baseline only
                return _Result([])            # empty WITHOUT raising
        if "count(n)" in flat and "posterior_alpha IS NOT NULL" in flat:
            state["cov"] += 1
            if state["cov"] == 1:             # the baseline's own cross-check
                raise RuntimeError("connection reset")
        return real_query(cypher, params)

    graph.query = _query
    manifest = epa.activate_beliefs(sdk)
    assert manifest["points_off_neutral"] == 1          # post-pass read intact
    assert manifest["points_moved_off_neutral"] is None  # NOT credited


def test_an_unread_coverage_count_is_not_consent_for_an_empty_state_read():
    """The POST-pass arm of the rule above, pinned SEPARATELY because it is a
    separate expression: an UNREAD ``with_ep`` is falsy, so a guard spelled
    ``and with_ep`` reads "could not read the coverage" as "there is no
    coverage" and publishes a fabricated off-neutral 0 for a state read that
    saw no rows — the same silent zero, on the read that DRIVES the
    per-question verdict.

    Both contribution cells go UNKNOWN. Reverting the guard to ``and with_ep``
    reds this test with ``assert 0 is None`` on ``points_off_neutral``.
    """
    sdk, graph = _make(["c1"], with_ep=3, ep_rows=[])
    real_query = graph.query

    def _query(cypher, params=None):
        flat = " ".join(cypher.split())
        if "count(n)" in flat and "posterior_alpha IS NOT NULL" in flat:
            raise RuntimeError("connection reset")   # unread on EVERY read
        return real_query(cypher, params)

    graph.query = _query
    manifest = epa.activate_beliefs(sdk)
    assert manifest["points_with_ep"] is None          # the coverage was NOT read
    assert manifest["points_off_neutral"] is None      # 0 rows is therefore unproven
    assert manifest["points_moved_off_neutral"] is None


def test_a_measured_empty_graph_still_baselines_at_zero():
    """The control for both guards above: a graph with NO EP state at all has a
    legitimately empty baseline, and the pass moving nothing is a MEASURED 0 —
    the guards must not turn every empty read into UNKNOWN."""
    sdk, _graph = _make(["c1"], with_ep=0, ep_rows=[])
    manifest = epa.activate_beliefs(sdk)
    assert manifest["points_with_ep"] == 0
    assert manifest["points_off_neutral"] == 0
    assert manifest["points_moved_off_neutral"] == 0


def test_manifest_carries_the_contribution_cells():
    """A COVERED graph that moved nothing: every claim has EP state and every
    posterior is the neutral mean — the exact shape that could otherwise be
    published as belief-activated."""
    sdk, _ = _make(["c1", "c2", "c3"], operators=2, with_ep=3,
                   ep_rows=[["c1", 1.2627, 1.2627, None, None],
                            ["c2", 1.2627, 1.2627, None, None],
                            ["c3", 1.2627, 1.2627, None, None]])
    manifest = epa.activate_beliefs(sdk)
    assert manifest["points_with_ep"] == 3      # covered
    assert manifest["points_off_neutral"] == 0  # ...and inert
    assert manifest["operators"] == 2           # the factors EXIST


def test_manifest_carries_the_moving_population():
    sdk, _ = _make(["c1", "c2"], operators=2, with_ep=2,
                   ep_rows=[["c1", 6.0, 2.0, None, None],
                            ["c2", 1.2627, 1.2627, None, None]])
    manifest = epa.activate_beliefs(sdk)
    assert manifest["points_off_neutral"] == 1


def test_operatorless_graph_reads_zero_operators_not_unknown():
    """``operators == 0`` is a MEASUREMENT ("the extraction minted no
    IMPL/NAND"), and it is the structural cause of an EP-inert question — so
    the SDK must report it as 0 rather than omit it."""
    sdk, graph = _make([], total=5, with_ep=0)
    manifest = epa.activate_beliefs(sdk)
    assert manifest["operators"] == 0
    assert manifest["points_off_neutral"] == 0
    # the census query really was issued (a silent skip would also read 0)
    assert graph.matching("RETURN count(o)")


def test_factors_come_from_the_pass_not_the_health_surface():
    """``factors`` is what THIS pass selected, read from the dream result's
    ``budget_used``. The health surface's ``operator_counts`` is the
    Dreamer's ``_last_operator_count``, written PER BATCH by a full pass — so
    on a multi-batch pass it holds the LAST batch's selection, not the
    pass's."""
    sdk, _ = _make(["c1"], operators=40, with_ep=1,
                   ep_rows=[["c1", 6.0, 2.0, None, None]])
    sdk.dream = lambda **kw: {"mode": "full", "budget_used": 40,
                              "operator_counts": 3}   # last batch only
    sdk.dream_health_check = lambda: {"operator_counts": 3,
                                      "no_op_reason": None}
    manifest = epa.activate_beliefs(sdk)
    assert manifest["factors"] == 40
    assert manifest["operators"] == 40


def test_factors_unknown_when_the_sdk_does_not_report_it():
    """A stub/older SDK must degrade to UNKNOWN — never a fabricated 0."""
    sdk, _ = _make(["c1"])
    manifest = epa.activate_beliefs(sdk)   # stub dream returns {"mode": "full"}
    assert manifest["factors"] is None


def test_no_op_reason_is_read_from_the_health_surface():
    sdk, _ = _make([], total=4, with_ep=0)
    sdk.dream_health_check = lambda: {"no_op_reason": "no_ep_factors"}
    manifest = epa.activate_beliefs(sdk)
    assert manifest["no_op_reason"] == "no_ep_factors"


def test_no_op_reason_unknown_when_the_sdk_has_no_health_surface():
    sdk, _ = _make(["c1"])
    assert not hasattr(sdk, "dream_health_check")
    manifest = epa.activate_beliefs(sdk)
    assert manifest["no_op_reason"] is None


def test_no_op_reason_unknown_when_the_health_surface_raises():
    sdk, _ = _make(["c1"])

    def boom():
        raise RuntimeError("health surface exploded")

    sdk.dream_health_check = boom
    manifest = epa.activate_beliefs(sdk)
    # fail-open on the diagnosis, never on the census
    assert manifest["no_op_reason"] is None
    assert manifest["ep_ran"] is True


def test_no_op_reason_is_not_read_after_a_failed_pass():
    """A raised pass has no reason: reading the health cell then would report
    a STALE reason from an earlier pass as if it described this one."""
    sdk, _ = _make(["c1"])

    def dream_boom(**kwargs):
        raise RuntimeError("EP exploded")

    sdk.dream = dream_boom
    sdk.dream_health_check = lambda: {"no_op_reason": "no_ep_factors"}
    manifest = epa.activate_beliefs(sdk)
    assert manifest["ep_ran"] is False
    assert manifest["factors"] is None
    assert manifest["no_op_reason"] is None


# ── #10137 review fixes: UNKNOWN discipline, real contribution, corroboration ─

def test_the_operator_census_uses_the_engine_predicate():
    """The census must count what EP counts. `ep.py` treats a node as an
    operator on `is_operator = true OR op_type IS NOT NULL`, so a census that
    matches only the flag would report "no operators" for a graph whose
    operators are spelled `op_type`-only — while EP consumes them as factors."""
    sdk, graph = _make(["c1"])
    epa.activate_beliefs(sdk)
    (cypher,) = [c for c, _ in graph.matching("RETURN count(o)")]
    assert "is_operator = true" in cypher
    assert "op_type IS NOT NULL" in cypher


def test_the_structural_verdict_is_corroborated_by_a_factor_edge_census():
    """`operators == 0` alone does NOT prove structural inertness: an IMPL/NAND
    straight between two claims is a factor with no operator node (#888 W5).
    The manifest therefore publishes the edge census the verdict needs."""
    sdk, graph = _make(["c1"], operators=0, factor_edges=0)
    manifest = epa.activate_beliefs(sdk)
    assert manifest["operators"] == 0
    assert manifest["factor_edges"] == 0
    assert len(graph.matching("RETURN count(r)")) == 1


def test_an_unreadable_census_is_unknown_never_a_fabricated_zero():
    """A census the run could not read is UNKNOWN. `0` is a published FINDING
    here ("this graph mints no operators"), so collapsing a failed read into it
    would invent a structural cause."""
    sdk, graph = _make(["c1"], operators=0, factor_edges=0)

    real_query = graph.query

    def failing_query(cypher, params=None):
        if "RETURN count(o)" in cypher or "RETURN count(r)" in cypher:
            raise RuntimeError("census read blew up")
        return real_query(cypher, params)

    graph.query = failing_query
    manifest = epa.activate_beliefs(sdk)

    assert manifest["operators"] is None
    assert manifest["factor_edges"] is None


def test_a_failed_total_census_publishes_unmeasured_as_unknown():
    """``points_unmeasured`` is DERIVED (``total - with_ep``), and its
    ``else None`` arm had no pin at all: collapsing UNKNOWN to a fabricated
    ``0`` there left every test green (measured), which is the one rule the
    module states for every census cell — a census that failed is never
    published as a ``0``.

    ``points_unmeasured == 0`` is a FINDING here ("every claim carries a
    posterior"), so a failed total read must read UNKNOWN, not zero.
    """
    sdk, graph = _make(["c1"], with_ep=1, ep_rows=[])
    real_query = graph.query

    def _query(cypher, params=None):
        flat = " ".join(cypher.split())
        # the TOTAL census only — ``count(n)`` WITHOUT the posterior filter
        if "count(n)" in flat and "posterior_alpha IS NOT NULL" not in flat:
            raise RuntimeError("timeout reading the point total")
        return real_query(cypher, params)

    graph.query = _query
    manifest = epa.activate_beliefs(sdk)

    assert manifest["points_with_ep"] == 1     # the other census WAS read
    assert manifest["points_unmeasured"] is None


def test_a_claim_state_read_failure_still_publishes_the_pass():
    """The ROW-SET twin of `_census`: its ``except`` arm had no pin, so a raise
    could be narrowed to propagate out of ``activate_beliefs`` and discarding a
    pass whose promotion writes and posteriors are already committed — the
    fail-open handler would then turn a completed question into
    ``activation_failed``.

    A raise on the claim-state read is UNKNOWN contribution, never a lost pass.
    """
    sdk, graph = _make(["c1"], operators=1, with_ep=1,
                       ep_rows=[["c1", 6.0, 2.0, None, None]])
    real_query = graph.query

    def _query(cypher, params=None):
        flat = " ".join(cypher.split())
        if "n.posterior_alpha, n.posterior_beta" in flat:
            raise RuntimeError("connection reset")
        return real_query(cypher, params)

    graph.query = _query
    manifest = epa.activate_beliefs(sdk)

    assert manifest["ep_ran"] is True          # the pass was NOT discarded
    assert manifest["points_with_ep"] == 1     # coverage still measured
    assert manifest["points_off_neutral"] is None
    assert manifest["points_moved_off_neutral"] is None


def test_a_census_failure_does_not_discard_a_completed_pass():
    """A MEASUREMENT query failing must not retroactively invalidate a pass
    whose promotion writes and posteriors are already committed."""
    sdk, graph = _make(["c1"], with_ep=1)

    real_query = graph.query

    def failing_query(cypher, params=None):
        if "RETURN count(o)" in cypher:
            raise RuntimeError("census read blew up")
        return real_query(cypher, params)

    graph.query = failing_query
    manifest = epa.activate_beliefs(sdk)

    assert manifest["ep_ran"] is True          # the pass still counts
    assert manifest["points_with_ep"] == 1     # ...and its coverage survives
    assert manifest["operators"] is None       # only the unread cell is UNKNOWN


def test_a_failed_pass_publishes_no_contribution_number():
    """Contribution describes what THIS pass did. A pass that raised did
    nothing, so its contribution cells are UNKNOWN — never a number attributed
    to a pass that never ran."""
    sdk, _graph = _make(["c1"], operators=3, factor_edges=4,
                        ep_rows=[["c1", 6.0, 2.0, None, None]])

    def dream_boom(**kwargs):
        raise RuntimeError("EP exploded")

    sdk.dream = dream_boom
    manifest = epa.activate_beliefs(sdk)

    assert manifest["ep_ran"] is False
    assert manifest["points_moved_off_neutral"] is None
    # the graph SHAPE is a property of the graph, not of the pass
    assert manifest["operators"] == 3
    assert manifest["factor_edges"] == 4


def test_the_contribution_is_the_difference_from_the_pre_pass_baseline():
    """`points_off_neutral` is a STATE census: a claim given a non-uniform
    baseline sits off the neutral mean with EP never having run. The
    CONTRIBUTION is what the pass NEWLY moved, so a pre-existing off-neutral
    claim must not be counted as the pass's doing."""
    # `pre` is off-neutral BEFORE the pass; `post` becomes off-neutral during it.
    sdk, graph = _make(
        ["c1"], operators=2, factor_edges=2,
        ep_rows=[["pre", 6.0, 2.0, None, None]])   # the pre-pass baseline read

    def dream_then_move(**kwargs):
        graph.ep_rows = [["pre", 6.0, 2.0, None, None],
                         ["post", 6.0, 2.0, None, None]]
        return {"mode": "full"}

    sdk.dream = dream_then_move
    manifest = epa.activate_beliefs(sdk)

    assert manifest["points_off_neutral"] == 2          # state: both are off
    assert manifest["points_moved_off_neutral"] == 1    # contribution: only `post`
