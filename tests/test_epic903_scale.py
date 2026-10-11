"""#1338 — production-scale dreaming contracts (epic 903 follow-up).

The epic-903 machinery was validated at the F5 scale (12 operators). The
recorded production range is 5k-500k operators (product-owner data point,
``docs/epics/2026-08-13-903-dreaming-ep/06-diagnostics.md`` — the STALE-FIRST
decision). This file pins the SCALE-SENSITIVE contracts that data point moves:

1. **The default per-pass budget is configurable** (indicator 2) —
   ``TORTOISE_DREAM_WINDOW_BUDGET`` sets the effective default; an explicit
   ``budget`` still wins; a malformed / negative value falls back to the
   shipped default instead of silently becoming "unbounded" or "every
   scheduled pass is a no-op". The shipped VALUE is deliberately unchanged —
   the scale measurement (below) does not support a bigger window, so raising
   it is left to the operator as a deployment knob.
2. **Routing at scale** (indicator 3) — a production-sized graph with no dirty
   roots auto-selects ``local``, never the whole-graph pass; ``stale-first``
   is reachable ONLY explicitly (the scheduled path passes it explicitly).
3. **``dream_all``'s cap has DEFINED behaviour at the top of the range**
   (indicator 1) — a pass the 200_000-operator DoS cap truncates reports
   ``truncated``/``converged_all=False``, stamps nothing on the regions it
   never covered, and is NOT swept as a completed pass. Full mode is
   documented non-viable above the cap; the remedy is ``mode="stale-first"``.
4. **The staleness ranking scales** — the scheduler's ``LIMIT``-bounded window
   is a genuine PREFIX of the full ranking (the two are computed by different
   query shapes, so a drift between them would silently window the wrong
   claims), with NULL (never dreamed) ranked stalest.

The heavy half — measured ranking query time, warm-start γ-skip savings,
per-pass cost and coverage-cycle time at >=100k operators — is NOT asserted
here (a 100k-operator graph does not belong in the merge gate). It lives in
``graph-scripts/dream-scale-bench.py`` (an operator-run harness, like
``graph-scripts/graph-diagnostics.py``) and the numbers are recorded in
``06-diagnostics.md``.

Hermetic: every SDK here is a tempfile-backed embedded one via the epic-903
shared ``fresh_sdk`` (no TORTOISE_DB_URI, no network). The synthetic graph is
built with batched Cypher because ``create_point``/``create_operator`` per
node would cost hundreds of round-trips for a scale fixture.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from epic903_fixtures import fresh_sdk

from tortoise.dream import (
    DEFAULT_WINDOW_BUDGET,
    DREAM_WINDOW_BUDGET_ENV,
    resolve_window_budget,
)

#: Fixed ISO stamps for the synthetic staleness spread (never wall-clock —
#: a sub-second build would otherwise produce identical stamps).
_STAMP_OLD = "2026-01-15T00:00:00+00:00"
_STAMP_MID = "2026-06-01T00:00:00+00:00"
_STAMP_NEW = "2026-08-01T00:00:00+00:00"

#: Production FLOOR recorded in 06-diagnostics.md (5k-500k operators).
PRODUCTION_FLOOR_OPERATORS = 5_000

def _build_synthetic(sdk, n_ops: int, *, null_frac: float = 0.25) -> None:
    """Build a deterministic claim/operator graph via batched Cypher.

    Shape (one query per node family + one per edge family — a scale fixture
    cannot afford per-node SDK calls):

    - ``n_ops`` operators (``is_operator=true``, live), ``2 * n_ops`` live
      claims, ``2 * n_ops`` IMPL edges ``op_i -> c_{2i}, c_{2i+1}`` (each
      operator has the 2 live endpoints EP needs to be a real factor);
    - a staleness spread over the claims: the first ``null_frac`` share has
      NO ``lastDreamedAt`` (stalest — never dreamed), the rest get the fixed
      old/mid/new stamps by index parity.
    """
    proj = sdk._get_proj()
    n_claims = 2 * n_ops
    n_null = int(n_claims * null_frac)
    claim_rows = []
    for j in range(n_claims):
        if j < n_null:
            stamp = None
        elif j % 3 == 0:
            stamp = _STAMP_OLD
        elif j % 3 == 1:
            stamp = _STAMP_MID
        else:
            stamp = _STAMP_NEW
        claim_rows.append({"id": f"c_{j}", "content": f"claim {j}",
                           "stamp": stamp})
    op_rows = [{"id": f"op_{i}"} for i in range(n_ops)]
    edge_rows = []
    for i in range(n_ops):
        edge_rows.append({"op": f"op_{i}", "claim": f"c_{2 * i}"})
        edge_rows.append({"op": f"op_{i}", "claim": f"c_{2 * i + 1}"})

    proj.g.query(
        "UNWIND $rows AS row "
        "CREATE (n:Point) "
        "SET n.id = row.id, n.content = row.content, "
        "n.is_operator = false, n.status = 'live', n.confidence = 0.5, "
        "n.lastDreamedAt = row.stamp, n.createdAt = $ts",
        params={"rows": claim_rows, "ts": _STAMP_OLD},
    )
    proj.g.query(
        "UNWIND $rows AS row "
        "CREATE (n:Point) "
        "SET n.id = row.id, n.content = row.id, "
        "n.is_operator = true, n.status = 'live', n.createdAt = $ts",
        params={"rows": op_rows, "ts": _STAMP_OLD},
    )
    proj.g.query(
        "UNWIND $rows AS row "
        "MATCH (o:Point {id: row.op}), (c:Point {id: row.claim}) "
        "CREATE (o)-[:IMPL {direction: 'bidirectional', weight: 0.8}]->(c)",
        params={"rows": edge_rows},
    )


def _operator_count(sdk) -> int:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point {is_operator: true}) RETURN count(n)"
    ).result_set
    return int(rows[0][0])


def _stamped_count(sdk) -> int:
    rows = sdk._get_proj().g.query(
        "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
        "AND n.lastDreamedAt IS NOT NULL RETURN count(n)"
    ).result_set
    return int(rows[0][0])


# ── Indicator 2 — configurable, production-sane default ─────────────


class TestBudgetConfigurability:
    """The window default is a deployment knob, not a constant in the code."""

    def test_default_is_a_conservative_documented_bound(self):
        """#1338 indicator 2, measured half: the shipped default is the value
        the window-cost measurement supports.

        The issue proposed ~10_000 on coverage-cycle grounds. The measurement
        (``graph-scripts/dream-scale-bench.py`` on a 5,001-claim synthetic
        graph) says a 5,000-claim window reliably raises FalkorDB's
        ``Query timed out`` where windows <= 2,000 complete, so a 50x default
        buys fewer passes with a per-pass RELIABILITY regression. The default
        therefore stays where it was, and the knob is what changed.
        """
        assert DEFAULT_WINDOW_BUDGET > 0
        assert DEFAULT_WINDOW_BUDGET <= 2_000, (
            "the shipped default must stay inside the measured-safe window "
            "(<= 2,000 claims on a 5k-claim graph); a larger value needs a "
            "fresh dream-scale-bench.py measurement, not an assumption"
        )

    def test_env_raises_the_default_and_covers_the_production_range(self):
        """The coverage cycle is tunable without a code change: raising the
        budget divides the passes per cycle by the same factor. This is the
        trade-off the issue's '2,500 passes' note is about — the operator
        chooses it with their own numbers."""
        old = os.environ.get(DREAM_WINDOW_BUDGET_ENV)
        try:
            os.environ[DREAM_WINDOW_BUDGET_ENV] = "10000"
            raised = resolve_window_budget(None)
            assert raised == 10_000
            for operators in (PRODUCTION_FLOOR_OPERATORS, 500_000):
                assert (-(-operators // raised)) == max(
                    1, -(-operators // DEFAULT_WINDOW_BUDGET) // 50)
        finally:
            _restore_env(old)

    def test_env_sets_the_default_and_explicit_wins(self):
        old = os.environ.get(DREAM_WINDOW_BUDGET_ENV)
        try:
            # An ambient value IS the configuration this feature adds; clear it
            # before asserting the DEFAULT, or a developer's exported budget
            # legitimately wins and the assertion is a false red.
            os.environ.pop(DREAM_WINDOW_BUDGET_ENV, None)
            assert resolve_window_budget(None) == DEFAULT_WINDOW_BUDGET
            os.environ[DREAM_WINDOW_BUDGET_ENV] = "4242"
            assert resolve_window_budget(None) == 4242
            # an explicit budget — including the I1 no-op 0 — always wins
            assert resolve_window_budget(7) == 7
            assert resolve_window_budget(0) == 0
        finally:
            _restore_env(old)

    @pytest.mark.parametrize("bad", ["10k", "-1", "", "  ", "1.5", "none"])
    def test_malformed_env_falls_back_to_the_default(self, bad):
        """A typo must never silently mean 'unbounded' (-1) or 'every
        scheduled pass is a no-op' (0). Both fail silently at the comparison
        sites, so the resolver refuses them."""
        old = os.environ.get(DREAM_WINDOW_BUDGET_ENV)
        try:
            os.environ[DREAM_WINDOW_BUDGET_ENV] = bad
            assert resolve_window_budget(None) == DEFAULT_WINDOW_BUDGET
        finally:
            _restore_env(old)

    def test_env_default_is_what_a_pass_actually_uses(self):
        """The resolver is not decorative: a ``budget=None`` window pass is
        bounded by the env value."""
        sdk, _db = fresh_sdk(prefix="tortoise_epic903_scale_env_")
        try:
            _build_synthetic(sdk, 8)
            sdk._dirty_roots.clear()
            old = os.environ.get(DREAM_WINDOW_BUDGET_ENV)
            try:
                os.environ[DREAM_WINDOW_BUDGET_ENV] = "1"
                r = sdk._get_dreamer().dream_window()  # budget=None
                assert r["budget_used"] == 1 <= 1, (
                    "the env-configured default must bound the pass"
                )
            finally:
                _restore_env(old)
        finally:
            sdk.close()


def _restore_env(old: str | None) -> None:
    if old is None:
        os.environ.pop(DREAM_WINDOW_BUDGET_ENV, None)
    else:
        os.environ[DREAM_WINDOW_BUDGET_ENV] = old


# ── Indicator 3 — routing at production scale ───────────────────────


class TestRoutingAtScale:
    """Production graphs (5k-500k operators) must never auto-route to a
    whole-graph pass, and auto-select must never pick the window mode."""

    def test_production_sized_graph_auto_routes_to_local(self):
        sdk, _db = fresh_sdk(prefix="tortoise_epic903_scale_route_")
        try:
            n_ops = 120  # > 2x _AUTO_FULL_OPERATOR_THRESHOLD (50)
            _build_synthetic(sdk, n_ops)
            assert _operator_count(sdk) == n_ops > 2 * 50
            sdk._dirty_roots.clear()
            assert sdk._auto_dream_mode() == "local", (
                "a graph above the small-graph threshold must not auto-route "
                "to a whole-graph pass (the O(whole-graph) cost the epic "
                "exists to avoid)"
            )
            # write context (dirty roots present) is also local — W1
            sdk._dirty_roots.add("c_0")
            assert sdk._auto_dream_mode() == "local"
        finally:
            sdk.close()

    def test_auto_select_never_picks_the_window_mode(self):
        """``stale-first`` is reachable ONLY by an explicit ``mode`` — the
        scheduled path passes it explicitly (903-C8 wiring). A production
        graph with no dirty roots must NOT be silently windowed, and must not
        be silently full-passed either."""
        sdk, _db = fresh_sdk(prefix="tortoise_epic903_scale_route2_")
        try:
            _build_synthetic(sdk, 120)
            sdk._dirty_roots.clear()
            for _ in range(3):
                assert sdk._auto_dream_mode() in ("local", "full")
                assert sdk._auto_dream_mode() != "stale-first"
            # explicit mode is honoured (the scheduled path's shape)
            assert sdk._resolve_dream_mode("stale-first", False) == "stale-first"
            assert sdk._resolve_dream_mode(None, True) == "full"
        finally:
            sdk.close()

    def test_small_graph_still_auto_routes_to_full(self):
        """The J3 small-graph rule survives the scale change: below the
        threshold there is nothing worth windowing."""
        sdk, _db = fresh_sdk(prefix="tortoise_epic903_scale_route3_")
        try:
            _build_synthetic(sdk, 4)
            sdk._dirty_roots.clear()
            assert sdk._auto_dream_mode() == "full"
        finally:
            sdk.close()


# ── Indicator 1 — defined behaviour above the full-mode cap ─────────


class TestFullModeCapAtTopOfRange:
    """500k operators > the 200_000 cap: the pass must say so, not pretend."""

    def test_truncated_pass_is_incomplete_and_stamps_nothing(self):
        sdk, _db = fresh_sdk(prefix="tortoise_epic903_scale_trunc_")
        try:
            _build_synthetic(sdk, 40)  # 40 operators, 80 claims
            before = _stamped_count(sdk)  # 60 stamped by the fixture spread
            dreamer = sdk._get_dreamer()
            r = dreamer.dream_all(max_total_operators=10)
            assert r["truncated"] is True
            assert r["converged_all"] is False, (
                "a truncated pass is NOT a completed pass — reporting success "
                "here is the silent-partial bug (#1338)"
            )
            assert r["batches"] == 0
            assert r["budget_used"] == 40 > 10
            # The graph-wide operator-less trivial-stamp scan is gated on a
            # COMPLETE converged pass: running it on a partial pass would
            # stamp freshness onto regions the pass never covered, poisoning
            # the staleness ranking the production scheduler reads.
            assert _stamped_count(sdk) == before, (
                "a truncated full pass must not move any freshness stamp"
            )
        finally:
            sdk.close()

    def test_sdk_full_result_keeps_the_pinned_key_set_and_fails_closed(
            self, monkeypatch):
        """The SDK mapping: the scheduler-internal ``truncated`` flag is
        dropped (I1 key-set is pinned) and the failure is visible as
        ``converged_all=False`` — which is also what stops the dirty-root
        sweep from clearing a backlog the partial pass never covered."""
        from epic903_fixtures import _make_claim

        sdk, _db = fresh_sdk(prefix="tortoise_epic903_scale_trunc_sdk_")
        try:
            _build_synthetic(sdk, 40)
            # a dirty root the truncated pass must NOT sweep
            sdk._dirty_roots.clear()
            root = _make_claim(sdk, "scale truncation dirty root")["id"]
            sdk._dirty_roots.add(root)
            dreamer = sdk._get_dreamer()
            real = dreamer.dream_all

            def _capped(*a, **kw):
                kw["max_total_operators"] = 10  # forces the truncation branch
                return real(*a, **kw)

            monkeypatch.setattr(dreamer, "dream_all", _capped)
            r = sdk.dream(mode="full")
            assert set(r.keys()) == {
                "mode", "batches", "total_affected", "converged_all",
                "budget_used", "coverage", "scanned_count",
            }, sorted(r.keys())
            assert "truncated" not in r
            assert r["converged_all"] is False
            assert r["batches"] == 0
            assert root in sdk._dirty_roots, (
                "an incomplete full pass must not clear the dirty backlog"
            )
        finally:
            sdk.close()

    def test_explicit_budget_still_raises_instead_of_truncating(self):
        """The #1244 contract is unchanged: an EXPLICIT budget fails loudly
        (a full pass is complete-in-one-pass by contract); silent truncation
        is only legal for the default DoS-cap path, and now reports itself."""
        from tortoise.exceptions import BudgetExceededError

        sdk, _db = fresh_sdk(prefix="tortoise_epic903_scale_trunc_raise_")
        try:
            _build_synthetic(sdk, 40)
            with pytest.raises(BudgetExceededError) as exc:
                sdk._get_dreamer().dream_all(budget=10)
            assert exc.value.budget == 10
            assert exc.value.required > 10
        finally:
            sdk.close()


# ── Indicator 4 (correctness half) — the ranking scales ─────────────


class TestStalenessRankingPrefix:
    """The scheduler windows with ``LIMIT``; the staleness report (I2) reads
    the full ranking. They are different query shapes, so the window must be a
    genuine PREFIX of the full ranking or the scheduler windows the wrong
    claims — a drift that only appears once the graph is bigger than one
    window."""

    def test_limited_window_is_a_prefix_of_the_full_ranking(self):
        from tortoise.analyze import _stale_first_claims

        sdk, _db = fresh_sdk(prefix="tortoise_epic903_scale_rank_")
        try:
            _build_synthetic(sdk, 150)  # 300 claims, 75 never dreamed
            proj = sdk._get_proj()
            full = _stale_first_claims(proj, limit=None)
            assert len(full) == 300
            for limit in (1, 10, 75, 200, 300):
                assert _stale_first_claims(proj, limit=limit) == full[:limit]
        finally:
            sdk.close()

    def test_null_ranks_stalest_and_ids_break_ties(self):
        from tortoise.analyze import _stale_first_claims

        sdk, _db = fresh_sdk(prefix="tortoise_epic903_scale_rank2_")
        try:
            _build_synthetic(sdk, 100)  # n_null = 50 of 200 claims
            ranked = _stale_first_claims(sdk._get_proj(), limit=None)
            rows = sdk._get_proj().g.query(
                "MATCH (n:Point) WHERE n.id IN $ids "
                "RETURN n.id, n.lastDreamedAt, n.content",
                params={"ids": ranked},
            ).result_set
            stamps = {r[0]: r[1] for r in rows}
            null_head = ranked[:50]
            assert all(stamps[c] is None for c in null_head), (
                "never-dreamed claims must rank STALEST (null first)"
            )
            assert null_head == sorted(null_head), (
                "the null group must be id-ascending (deterministic tie-break)"
            )
            assert stamps[ranked[50]] == _STAMP_OLD
            assert stamps[ranked[-1]] == _STAMP_NEW
        finally:
            sdk.close()


# ── Coverage cycle — the passes a production graph actually needs ───


class TestCoverageCycleScalesWithTheBudget:
    """The coverage cycle is ``ceil(operators / budget)`` passes, and the
    budget is a deployment knob — the #1338 trade-off, pinned as ARITHMETIC
    rather than as a target the shipped default has to hit."""

    @pytest.mark.parametrize("n_ops,scale_name", [
        (PRODUCTION_FLOOR_OPERATORS, "production floor"),
        (500_000, "production top"),
    ])
    def test_passes_to_cover_the_production_range(self, n_ops, scale_name):
        shipped = -(-n_ops // DEFAULT_WINDOW_BUDGET)  # ceil
        assert shipped == (n_ops + DEFAULT_WINDOW_BUDGET - 1) // DEFAULT_WINDOW_BUDGET
        # the documented arithmetic in 06-diagnostics.md:
        #   5k operators  -> 25 passes at the default, 5 at 1,000, 1 at 10,000
        #   500k operators -> 2,500 at the default, 500 at 1,000, 50 at 10,000
        if n_ops == PRODUCTION_FLOOR_OPERATORS:
            assert (shipped, -(-n_ops // 1_000), -(-n_ops // 10_000)) == (25, 5, 1)
        else:
            assert (shipped, -(-n_ops // 1_000), -(-n_ops // 10_000)) == (2500, 500, 50), (
                f"{scale_name}: the documented coverage-cycle table drifted"
            )
