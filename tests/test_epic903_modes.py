"""Epic 903-C6 (#1244) — SDK dream() mode router: precedence, per-mode
return shapes, auto-selection, budget boundaries (DE2E-1 split + DE2E-3).

Hermetic harness per tests/epic903_fixtures.py (fresh_sdk disables the
calibration gate; live claims; fixed seeds — never wall-clock).
"""
from __future__ import annotations

import random

import pytest

from tests.epic903_fixtures import FIXED_SEED, f1_corpus, f2_staleness_regions, fresh_sdk

LOCAL_KEYS = {"mode", "iterations", "converged", "affected_claims",
              "budget_used", "coverage"}
STALE_KEYS = {"mode", "batches", "converged_all", "converged",
              "affected_claims", "budget_used", "coverage"}
FULL_KEYS = {"mode", "batches", "total_affected", "converged_all",
             "budget_used", "coverage", "scanned_count"}


class TestPrecedenceMatrix:
    """I1 precedence table (surface 1 conditional guards)."""

    def test_explicit_mode_wins_over_full_sugar(self):
        """mode="local" + full=True → local (I1 rule 1)."""
        f = f1_corpus()
        try:
            random.seed(FIXED_SEED)
            r = f.sdk.dream(full=True, mode="local")
            assert r["mode"] == "local"
            assert set(r.keys()) == LOCAL_KEYS, sorted(r.keys())
        finally:
            f.sdk.close()

    def test_full_true_maps_to_full_only_when_mode_none(self):
        """full=True + mode=None → full (I1 rule 2)."""
        f = f1_corpus()
        try:
            random.seed(FIXED_SEED)
            r = f.sdk.dream(full=True)
            assert r["mode"] == "full"
            assert set(r.keys()) == FULL_KEYS
        finally:
            f.sdk.close()

    def test_unknown_mode_raises_value_error(self):
        f = f1_corpus()
        try:
            with pytest.raises(ValueError):
                f.sdk.dream(mode="quantum")
        finally:
            f.sdk.close()

    def test_explicit_stale_first_overrides_dirty_context(self):
        """mode="stale-first" + dirty roots → stale-first (not local)."""
        f = f2_staleness_regions()
        try:
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="stale-first")
            assert r["mode"] == "stale-first"
            assert set(r.keys()) == STALE_KEYS
        finally:
            f.sdk.close()

    def test_write_context_auto_selects_local(self):
        """dirty roots present → auto-select local (W1/D2-5)."""
        f = f2_staleness_regions()
        try:
            f.sdk._mark_dirty([f.isolated_claim])
            random.seed(FIXED_SEED)
            r = f.sdk.dream()  # mode=None, full=False
            assert r["mode"] == "local"
            assert set(r.keys()) == LOCAL_KEYS
        finally:
            f.sdk.close()


class TestAutoSelection:
    """I1 rule 3 auto-select branches."""

    def test_small_graph_auto_selects_full(self):
        """No dirty roots + small graph (< threshold) → full (J3)."""
        f = f1_corpus()  # F1 is below the 50-operator threshold
        try:
            f.sdk._dirty_roots.clear()
            random.seed(FIXED_SEED)
            r = f.sdk.dream()
            assert r["mode"] == "full"
            assert set(r.keys()) == FULL_KEYS
        finally:
            f.sdk.close()

    def test_large_graph_auto_selects_local(self):
        """No dirty roots + graph above the operator threshold → local
        (the safe bounded default; scheduled stale-first is explicit)."""
        # Manufacture a graph above the 50-operator threshold with 51 ops.
        sdk, _ = fresh_sdk(prefix="tortoise_epic903_auto_")
        try:
            from tortoise.sdk import _ep_require_calibration_default  # noqa
            claims = [sdk.create_point("statement", f"c{i}", dedup=False,
                                       status="live") for i in range(52)]
            for i in range(51):
                sdk.create_operator("IMPL", claims[i]["id"],
                                    [claims[i + 1]["id"]])
            sdk._dirty_roots.clear()  # P2-review: create_point marks dirty —
            # the ≥50-operator threshold branch must be exercised WITHOUT
            # dirty roots (otherwise the dirty-roots→local rule, not the
            # threshold, is what passes the test)
            random.seed(FIXED_SEED)
            r = sdk.dream()
            assert r["mode"] == "local"
        finally:
            sdk.close()

    def test_noop_budget_zero_exact_keys(self):
        """budget=0 → no-op with the mode's EXACT key-set (I1)."""
        f = f1_corpus()
        try:
            for mode, keys in (("local", LOCAL_KEYS),
                               ("stale-first", STALE_KEYS),
                               ("full", FULL_KEYS)):
                r = f.sdk.dream(mode=mode, budget=0)
                assert r["mode"] == mode
                assert set(r.keys()) == keys
                assert r["budget_used"] == 0
                assert r["coverage"] == 0.0
        finally:
            f.sdk.close()


class TestPerModeReturnShapes:
    """I1 per-mode key-sets + coverage denominators."""

    def test_local_shape_and_coverage(self):
        f = f2_staleness_regions()
        try:
            f.sdk._mark_dirty([f.regions[0].claims[0]])
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="local")
            assert set(r.keys()) == LOCAL_KEYS
            assert 0.0 <= r["coverage"] <= 1.0
            assert r["converged"] in (True, False)
        finally:
            f.sdk.close()

    def test_stale_first_shape_maps_operators_deduped(self):
        f = f2_staleness_regions()
        try:
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="stale-first", budget=2)
            assert set(r.keys()) == STALE_KEYS
            # budget_used is the SDK-side mapping of the scheduler's
            # operators_deduped (I6→I1) — the key-set assert pins the shape.
            assert 0.0 <= r["coverage"] <= 1.0
        finally:
            f.sdk.close()

    def test_full_shape_with_scanned_count(self):
        f = f1_corpus()
        try:
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="full")
            assert set(r.keys()) == FULL_KEYS
            assert r["scanned_count"] >= 0
            assert r["total_affected"] >= 0
        finally:
            f.sdk.close()

    def test_zero_operator_negative(self):
        """DE2E-1 split: zero-operator graph → no-op full result, no crash,
        no stamps (owner: 903-C6; stamping/scan covered by 903-C2)."""
        sdk, _ = fresh_sdk(prefix="tortoise_epic903_zeroop_")
        try:
            sdk.create_point("statement", "lonely", dedup=False,
                             status="live")
            random.seed(FIXED_SEED)
            r = sdk.dream(mode="full")
            assert r["mode"] == "full"
            assert r["total_affected"] == 0  # no EP ran — no operators
            assert r["converged_all"] is True
            # The trivial-scan path (#1240) MAY stamp the operator-less claim
            # — that is by design; the EP no-op is what this negative pins.
            assert r["scanned_count"] in (0, 1)
        finally:
            sdk.close()


class TestBudgetBoundaries:
    def test_budget_exceeded_full_raises(self):
        """An explicit budget a full pass cannot satisfy raises
        BudgetExceededError (full is complete-in-one-pass)."""
        f = f2_staleness_regions()
        try:
            from tortoise.exceptions import BudgetExceededError
            with pytest.raises(BudgetExceededError):
                f.sdk.dream(mode="full", budget=1)
        finally:
            f.sdk.close()

    def test_stale_first_never_raises_on_budget(self):
        """stale-first defers instead of raising (truncation is its design)."""
        f = f2_staleness_regions()
        try:
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="stale-first", budget=1)
            assert r["budget_used"] <= 1
        finally:
            f.sdk.close()

    def test_budget_none_default_cap_respected(self):
        f = f2_staleness_regions()
        try:
            random.seed(FIXED_SEED)
            r = f.sdk.dream(mode="stale-first")
            assert r["budget_used"] <= 200  # default selector cap
        finally:
            f.sdk.close()


class TestWritePathStructural:
    """D2-5: write context → local mode ONLY — window/full unreachable from
    the write path (carries the 500ms-latency risk mitigation)."""

    def test_write_path_never_selects_window_or_full(self):
        f = f2_staleness_regions()
        try:
            # Auto-select with dirty roots present must always be local —
            # regardless of graph size (even a tiny graph stays local in the
            # write path; the small-graph→full rule only applies with NO
            # dirty roots). Re-mark before each iteration: a converged local
            # dream clears the affected roots, so a fresh write must mark
            # again — exactly the W1 write→local loop.
            for _ in range(3):
                f.sdk._mark_dirty([f.regions[0].claims[0]])
                assert f.sdk._dirty_roots, "write must mark dirty roots"
                r = f.sdk.dream()
                assert r["mode"] == "local"
        finally:
            f.sdk.close()
