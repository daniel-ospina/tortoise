"""Epic 903-C3 (#1241) — stale-first window scheduler + operator-set dedup
+ per-pass budget (DE2E-2).

Covers DE2E-2 (plan Substep 7): per-pass ``budget_used ≤ B`` (distinct
operators after dedup — overlapping windows share operator sets, union not
recompute); stalest region first each pass (null ranks STALEST; deterministic
id tie-break); retained-dirty root included despite being outside top-N
(union); eventual full coverage within a bounded number of passes; all-null
graph → single pass. Boundaries: budget=0 → no-op; budget ≥ graph → single
pass; budget=None → default 200-op selector cap respected (no unbounded
interpretation). D2-3 index sub-assertion: the ``:Point(lastDreamedAt)``
index (composite ``:Point(is_operator, lastDreamedAt)`` on docker/server)
exists at init and the ranking query's null-inclusion semantics are pinned
(nulls rankable as stalest). Determinism: fixed seeds, fixed ISO fixture
stamps — never wall-clock state manufacturing (fixtures rule #1250).

Hermetic embedded pattern per ``tests/epic903_fixtures.py`` (F2 builder +
fresh_sdk + _make_claim); single-SDK threading only (#432 note).
"""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: F401, I001

from tests.epic903_fixtures import (
    FIXED_SEED,
    STAMP_MEDIUM,
    STAMP_FRESH,
    f2_staleness_regions,
    fresh_sdk,
    _make_claim,
)
from tortoise.analyze import _bfs_select_operators, _stale_first_claims
from tortoise.dream import DEFAULT_WINDOW_BUDGET, Dreamer

I6_KEYS = {"mode", "batches", "converged", "converged_all",
           "operators_deduped", "budget_used", "affected_claims"}


def _stamp(proj, point_id: str):
    row = proj.g.query(
        "MATCH (n:Point {id:$id}) RETURN n.lastDreamedAt",
        params={"id": point_id},
    ).result_set
    return row[0][0] if row else None


def _region_by_name(f, name):
    return next(r for r in f.regions if r.name == name)


def _all_claim_ids(f) -> list[str]:
    return [c for r in f.regions for c in r.claims] + [f.isolated_claim]


def _region_names(f, ids) -> list[str]:
    out = []
    for cid in ids:
        for r in f.regions:
            if cid in r.claims:
                out.append(r.name)
                break
        else:
            out.append("isolated" if cid == f.isolated_claim else "?")
    return out


# ── DE2E-2 — bounded, staleness-ranked, deduped ─────────────────────


class TestDe2e2StaleFirstScheduler:
    def _setup(self):
        f = f2_staleness_regions()
        f.sdk._dirty_roots.clear()  # fixture construction marks dirty — isolate ranking
        random.seed(FIXED_SEED)
        return f

    def test_return_shape_matches_i6_contract(self):
        f = self._setup()
        r = f.sdk._get_dreamer().dream_window(budget=2)
        assert set(r.keys()) == I6_KEYS, (
            f"I6 key-set must be exact, got {sorted(r.keys())}"
        )
        assert r["mode"] == "stale-first"

    def test_stalest_region_first_and_bounded(self):
        """Null ranks STALEST — the never-dreamed region drains first, and
        per-pass budget_used ≤ B (distinct operators after dedup)."""
        f = self._setup()
        proj = f.sdk._get_proj()
        r = f.sdk._get_dreamer().dream_window(budget=2)
        assert r["batches"] == 1
        assert r["budget_used"] <= 2
        assert r["operators_deduped"] == r["budget_used"] <= 2
        assert r["converged"] is True and r["converged_all"] is True
        affected = set(r["affected_claims"])
        null = _region_by_name(f, "null")
        med = _region_by_name(f, "medium")
        fresh = _region_by_name(f, "fresh")
        assert set(null.claims) <= affected, (
            "never-dreamed (null) region must drain first"
        )
        assert set(med.claims).isdisjoint(affected)
        assert set(fresh.claims).isdisjoint(affected)
        # stamps: null region got a pass stamp; untouched regions unchanged
        for cid in null.claims:
            assert _stamp(proj, cid) is not None
        assert _stamp(proj, med.claims[0]) == STAMP_MEDIUM
        assert _stamp(proj, fresh.claims[0]) == STAMP_FRESH

    def test_ranking_null_first_and_deterministic_id_tiebreak(self):
        """Shared helper pins the ranking contract: nulls first, then
        ascending stamps, ids ascending within a stamp group."""
        f = self._setup()
        ranked = _stale_first_claims(f.sdk._get_proj(), limit=None)
        assert len(ranked) == 13  # 12 region claims + 1 isolated
        seq = _region_names(f, ranked)
        assert seq == (["null"] * 3 + ["isolated"] + ["old"] * 3
                       + ["medium"] * 3 + ["fresh"] * 3), (
            f"ranking must be null-block, then stamp-ascending: {seq}"
        )
        # deterministic tie-break: ids ascending inside each stamp group
        assert ranked[:3] == sorted(ranked[:3])
        # limit = top-N
        assert _stale_first_claims(f.sdk._get_proj(), limit=2) == ranked[:2]

    def test_retained_dirty_root_union_outside_top_n(self):
        """The retained-dirty root is unioned into the window even though it
        ranks outside the top-N (W2/W4: non-converged regions reselect)."""
        f = self._setup()
        fresh = _region_by_name(f, "fresh")
        dirty = fresh.claims[0]  # ranks ~11th; top-6 is null×3+isolated+old×2
        f.sdk._dirty_roots.add(dirty)
        r = f.sdk._get_dreamer().dream_window(budget=6)
        affected = set(r["affected_claims"])
        assert dirty in affected, (
            "dirty root outside top-N must be unioned into the window"
        )
        assert r["budget_used"] <= 6
        assert set(_region_by_name(f, "null").claims) <= affected
        assert set(_region_by_name(f, "old").claims) <= affected

    def test_eventual_full_coverage_bounded_passes(self):
        """Repeated budget=2 passes drain the whole F2 graph (all 13 claims)
        within a bounded number of passes."""
        f = self._setup()
        proj = f.sdk._get_proj()  # noqa: F841
        all_claims = set(_all_claim_ids(f))
        covered: set[str] = set()
        passes = 0
        dreamer = f.sdk._get_dreamer()
        while passes < 30:
            random.seed(FIXED_SEED)
            r = dreamer.dream_window(budget=2)
            assert r["budget_used"] <= 2, "every pass respects the budget"
            new = set(r["affected_claims"]) - covered
            covered |= set(r["affected_claims"])
            passes += 1
            if not new:
                break
        assert all_claims <= covered, (
            f"eventual full coverage failed; missing "
            f"{sorted(all_claims - covered)}"
        )
        # Observed: 6 passes (4 regions + isolated + 1 terminating re-run).
        # Theoretical worst case (1 new claim per pass): 13 + 1. Bounded.
        assert passes <= 14, f"coverage must be bounded, took {passes} passes"

    def test_all_null_graph_single_pass(self):
        """All-null graph (nothing ever dreamed — first deploy / legacy /
        crash recovery) → window = whole graph, single pass, full coverage."""
        sdk, _db = fresh_sdk(prefix="tortoise_epic903_allnull_")
        c1 = _make_claim(sdk, "allnull claim 1")["id"]
        c2 = _make_claim(sdk, "allnull claim 2")["id"]
        c3 = _make_claim(sdk, "allnull claim 3")["id"]
        sdk.create_operator("IMPL", c1, [c2])
        sdk.create_operator("IMPL", c2, [c3])
        iso = _make_claim(sdk, "allnull isolated")["id"]
        sdk._dirty_roots.clear()
        random.seed(FIXED_SEED)
        proj = sdk._get_proj()
        r = sdk._get_dreamer().dream_window(budget=2)
        assert r["batches"] == 1, "all-null graph must be a single pass"
        affected = set(r["affected_claims"])
        assert {c1, c2, c3} <= affected
        assert iso in affected  # operator-less → window-scoped trivial stamp
        assert r["budget_used"] == 2  # the 2 region operators, no truncation
        for cid in (c1, c2, c3, iso):
            assert _stamp(proj, cid) is not None

    def test_empty_graph_noop(self):
        sdk, _db = fresh_sdk(prefix="tortoise_epic903_empty_")
        r = sdk._get_dreamer().dream_window(budget=2)
        assert r["batches"] == 0
        assert r["budget_used"] == 0
        assert r["affected_claims"] == []
        assert r["converged"] is True

    def test_dream_window_does_not_clear_dirty_roots(self):
        """I6 boundary: the Dreamer does not clear dirty roots — the SDK
        adapter maps operators_deduped → budget_used and drives the dirty
        logic from `converged`."""
        f = self._setup()
        f.sdk._dirty_roots = {"nonexistent-dirty-root"}
        r = f.sdk._get_dreamer().dream_window(budget=2)
        assert r["converged"] is True
        assert f.sdk._dirty_roots == {"nonexistent-dirty-root"}


# ── Budget boundaries ───────────────────────────────────────────────


class TestBudgetBoundaries:
    def test_budget_zero_noop(self):
        f = f2_staleness_regions()
        f.sdk._dirty_roots.clear()
        proj = f.sdk._get_proj()
        before = {cid: _stamp(proj, cid) for cid in _all_claim_ids(f)}
        r = f.sdk._get_dreamer().dream_window(budget=0)
        assert r["mode"] == "stale-first"
        assert r["batches"] == 0
        assert r["budget_used"] == 0 and r["operators_deduped"] == 0
        assert r["affected_claims"] == []
        assert r["converged"] is True and r["converged_all"] is True
        for cid, s in before.items():
            assert _stamp(proj, cid) == s, (
                "budget=0 must be a no-op — no stamps written"
            )

    def test_budget_geq_graph_single_pass(self):
        f = f2_staleness_regions()
        f.sdk._dirty_roots.clear()
        random.seed(FIXED_SEED)
        proj = f.sdk._get_proj()  # noqa: F841
        r = f.sdk._get_dreamer().dream_window(budget=13)
        assert r["batches"] == 1, "budget ≥ graph → single pass"
        assert set(_all_claim_ids(f)) <= set(r["affected_claims"])
        assert r["budget_used"] == 8  # all 8 region operators, no truncation

    def test_budget_one_caps_operator_selection(self):
        """budget < a region's operator count → the SELECTED operator set is
        truncated deterministically (budget_used == 1 ≤ B). Selector-cap
        semantics (the same as the existing 200-op cap in dream()): budget
        bounds the operator selection; the EP closure still covers the whole
        region — claims reachable only through truncated ops are the
        next-pass's work, but in-region closure covers them here."""
        f = f2_staleness_regions()
        f.sdk._dirty_roots.clear()
        random.seed(FIXED_SEED)
        proj = f.sdk._get_proj()
        r = f.sdk._get_dreamer().dream_window(budget=1)
        assert r["budget_used"] == 1 <= 1
        assert r["operators_deduped"] == 1
        null = _region_by_name(f, "null")
        affected = set(r["affected_claims"])
        # EP closure of the retained operator covers the region (max_hops=2).
        assert set(null.claims) <= affected
        for cid in null.claims:
            assert _stamp(proj, cid) is not None
        # untouched regions keep their fixture stamps.
        assert (_stamp(proj, _region_by_name(f, "medium").claims[0])
                == STAMP_MEDIUM)

    def test_budget_none_default_200_cap(self):
        """budget=None → the existing 200-op _bfs_select_operators cap — a
        >200-operator graph hits the cap exactly (no unbounded
        interpretation)."""
        assert DEFAULT_WINDOW_BUDGET == 200
        # F2 equivalence: budget=None ≡ budget=200 on a small graph.
        f = f2_staleness_regions()
        f.sdk._dirty_roots.clear()
        random.seed(FIXED_SEED)
        r_default = f.sdk._get_dreamer().dream_window()
        assert r_default["budget_used"] <= DEFAULT_WINDOW_BUDGET
        assert r_default["batches"] == 1
        # 201-operator fan-out: the default cap truncates to exactly 200.
        sdk, _db = fresh_sdk(prefix="tortoise_epic903_cap_")
        src = _make_claim(sdk, "cap source")["id"]
        targets = [_make_claim(sdk, f"cap target {i}")["id"]
                   for i in range(201)]
        for t in targets:
            sdk.create_operator("IMPL", src, [t])
        sdk._dirty_roots.clear()
        random.seed(FIXED_SEED)
        r = sdk._get_dreamer().dream_window()  # budget=None
        assert r["budget_used"] == DEFAULT_WINDOW_BUDGET, (
            "default cap must be respected, not unbounded"
        )
        assert r["batches"] == 1


# ── D2-3 index sub-assertion ────────────────────────────────────────


class TestIndexSubAssertion:
    def test_index_exists_and_ranking_null_inclusion(self):
        f = f2_staleness_regions()
        proj = f.sdk._get_proj()
        # Presence: a :Point index covering lastDreamedAt exists at init
        # (embedded: plain :Point(lastDreamedAt); docker/server: composite
        # :Point(is_operator, lastDreamedAt) — #1240, replay-safe).
        rows = proj.g.query("CALL db.indexes()").result_set
        point_idx = {r[0]: r[1] for r in rows if r[0] == "Point"}
        assert "lastDreamedAt" in str(point_idx), (
            f":Point(lastDreamedAt) index must exist at init: {point_idx}"
        )
        # Behavioral null-inclusion: the ranking query ranks nulls STALEST.
        # (EXPLAIN on embedded redislite echoes result rows rather than a
        # plan graph — the plan's D2-3 allowance is presence OR query-plan;
        # presence + pinned ranking behavior used here.)
        ranked = _stale_first_claims(proj, limit=None)
        null = _region_by_name(f, "null")
        old = _region_by_name(f, "old")
        assert all(cid in ranked[:4] for cid in null.claims), (
            "never-dreamed claims must rank first (null-as-stalest)"
        )
        assert old.claims[0] not in ranked[:4]


# ── Operator-set dedup + single-BFS-per-batch ───────────────────────


class TestDedupAndSingleBfsPerBatch:
    def test_single_bfs_per_batch(self, monkeypatch):
        """The dream_all double-BFS fix: one _bfs_select_operators call per
        batch — the cap-check and the EP run share a single selection."""
        f = f2_staleness_regions()
        f.sdk._dirty_roots.clear()
        real = _bfs_select_operators
        counter: list = []

        def spy(*args, **kwargs):
            counter.append(args)
            return real(*args, **kwargs)

        monkeypatch.setattr("tortoise.analyze._bfs_select_operators", spy)
        random.seed(FIXED_SEED)
        r = f.sdk._get_dreamer().dream_window(budget=None)
        assert r["batches"] == 1
        assert len(counter) == 1, (
            f"exactly one BFS per batch, got {len(counter)}"
        )

    def test_overlapping_windows_union_operator_sets(self, monkeypatch):
        """Union, not recompute: with a tiny claim chunk size the pass spans
        multiple batches whose windows overlap (null region shares operators
        across chunks) — the dedup union skips the re-run and each batch
        still does exactly one BFS."""
        f = f2_staleness_regions()
        f.sdk._dirty_roots.clear()
        real_bfs = _bfs_select_operators
        real_run = Dreamer._ep_run_batch
        bfs_calls: list = []
        run_calls: list = []

        def spy_bfs(*args, **kwargs):
            bfs_calls.append(args)
            return real_bfs(*args, **kwargs)

        def spy_run(self, *args, **kwargs):
            run_calls.append(args)
            return real_run(self, *args, **kwargs)

        monkeypatch.setattr("tortoise.analyze._bfs_select_operators", spy_bfs)
        monkeypatch.setattr("tortoise.dream.WINDOW_CLAIM_BATCH", 2)
        monkeypatch.setattr(Dreamer, "_ep_run_batch", spy_run)
        random.seed(FIXED_SEED)
        r = f.sdk._get_dreamer().dream_window(budget=6)
        # 6-claim window in chunks of 2 → 3 batches, 3 BFS calls (≤1 each)
        assert r["batches"] == 3
        assert len(bfs_calls) == 3, (
            f"per-batch BFS query count must be ≤ 1, got {len(bfs_calls)}"
        )
        # chunk 2 (null c3 + isolated) re-discovers null op2 — deduped: no EP
        # re-run. Only chunks 1 and 3 ran EP.
        assert len(run_calls) == 2, (
            f"overlapping operators must not be recomputed, "
            f"got {len(run_calls)} EP runs"
        )
        assert r["budget_used"] == 4, "distinct operators after dedup"
        assert r["operators_deduped"] == r["budget_used"] <= 6


# ── Determinism ─────────────────────────────────────────────────────


class TestDeterminism:
    def test_ranking_structure_deterministic(self):
        """Fixed ISO fixture stamps → structurally identical ranking across
        fresh fixtures (no wall-clock state manufacturing)."""
        seqs = []
        for _ in range(2):
            f = f2_staleness_regions()
            ranked = _stale_first_claims(f.sdk._get_proj(), limit=None)
            seqs.append(_region_names(f, ranked))
        expected = ["null"] * 3 + ["isolated"] + ["old"] * 3 \
            + ["medium"] * 3 + ["fresh"] * 3
        assert seqs[0] == seqs[1] == expected

    def test_pass_result_deterministic_across_fixtures(self):
        f1 = f2_staleness_regions()
        f1.sdk._dirty_roots.clear()
        f2 = f2_staleness_regions()
        f2.sdk._dirty_roots.clear()
        results = []
        for f in (f1, f2):
            random.seed(FIXED_SEED)
            r = f.sdk._get_dreamer().dream_window(budget=2)
            results.append((
                sorted(_region_names(f, r["affected_claims"])),
                r["budget_used"],
                r["converged"],
                r["batches"],
            ))
        assert results[0] == results[1], (
            "fixed seed + fixed ISO stamps must give identical pass results"
        )
