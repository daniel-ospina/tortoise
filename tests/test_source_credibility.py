"""Pure-math tests for tortoise.source_credibility (issue #398).

Verifies the pinned log-scale aggregation model, decay, tier resolution, and
assessment factor — no graph, no EP, no Docker (embedded-safe).
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.source_credibility import (
    FACTOR_MAX,
    FACTOR_MIN,
    SOURCE_KIND_DEFAULTS,
    TIER_PRIORS,
    aggregate_prior,
    assessment_factor,
    decay_factor,
    derive_reliability,
    mean_from_beta,
    pc_base,
    register_source_kind_default,
    resolve_source_tier,
    resolve_tier,
)

FRESH = "2024-01-01T00:00:00+00:00"


# ═══════════════════════════════════════════════════════════════════════
# Tier model + pc_base contract
# ═══════════════════════════════════════════════════════════════════════

class TestTierModel:
    def test_tier_priors_match_experiment_doc(self):
        """Validated priors from docs/ep-source-credibility-experiment.md §1.1."""
        assert TIER_PRIORS == {
            "T0": (10.0, 1.0), "T1": (5.0, 1.0), "T2": (3.0, 1.0),
            "T3": (2.0, 1.0), "T4": (1.1, 1.0),
        }

    def test_pc_base_is_alpha_minus_one(self):
        """pc_base := alpha - 1 (excess over neutral Beta(1,1)) — pinned contract."""
        for tier, (alpha, beta) in TIER_PRIORS.items():
            assert pc_base(tier) == alpha - 1.0
            assert beta == 1.0  # all tier betas neutral — no negative pc today

    def test_n1_identity(self):
        """aggregate_prior(tier, 1) == TIER_PRIORS[tier] exactly (N=1 degeneracy)."""
        for tier, prior in TIER_PRIORS.items():
            assert aggregate_prior([(tier, FRESH, FRESH, 1.0)], recency_decay=1.0) == prior


# ═══════════════════════════════════════════════════════════════════════
# Aggregation math
# ═══════════════════════════════════════════════════════════════════════

class TestAggregation:
    def test_diminishing_returns(self):
        """Each additional same-weight source adds less pc than the previous."""
        gains = []
        prev = 0.0
        for n in range(1, 10):
            pc = aggregate_prior(
                [("T1", FRESH, FRESH, 1.0)] * n, recency_decay=1.0
            )[0] - 1.0
            if prev:
                gains.append(pc - prev)
            prev = pc
        for i in range(len(gains) - 1):
            assert gains[i] > gains[i + 1], f"gain {i} not > gain {i+1}"

    def test_anti_sybil_1000_t4_approx_t3(self):
        """1000 x T4 pc ~= 0.997 < 1 x T3 pc = 1.0 (quality beats quantity)."""
        pc_1000t4 = aggregate_prior([("T4", FRESH, FRESH, 1.0)] * 1000, recency_decay=1.0)[0] - 1.0
        pc_1t3 = aggregate_prior([("T3", FRESH, FRESH, 1.0)], recency_decay=1.0)[0] - 1.0
        assert pc_1000t4 < pc_1t3
        assert abs(pc_1000t4 - pc_1t3) < 0.1

    def test_100_t4_below_1_t2(self):
        pc_100t4 = aggregate_prior([("T4", FRESH, FRESH, 1.0)] * 100, recency_decay=1.0)[0] - 1.0
        pc_1t2 = aggregate_prior([("T2", FRESH, FRESH, 1.0)], recency_decay=1.0)[0] - 1.0
        assert pc_100t4 < pc_1t2

    def test_mixed_tier_sums(self):
        """Mixed tiers sum per-tier contributions (log per tier, additive across)."""
        a, b = aggregate_prior(
            [("T0", FRESH, FRESH, 1.0), ("T4", FRESH, FRESH, 1.0), ("T4", FRESH, FRESH, 1.0)],
            recency_decay=1.0,
        )
        expected = 1.0 + 9.0 * math.log2(2) + 0.1 * math.log2(3)
        assert a == pytest.approx(expected, rel=1e-9)
        assert b == 1.0

    def test_prior_mean_monotonic_in_n_uniform(self):
        """Uniform-weight addition: mean increases monotonically."""
        prev = 0.0
        for n in [1, 2, 3, 5, 10, 100]:
            a, _b = aggregate_prior([("T3", FRESH, FRESH, 1.0)] * n, recency_decay=1.0)
            m = mean_from_beta(a, 1.0)
            assert m > prev
            prev = m

    def test_ancient_same_tier_addition_does_not_decrease_pc(self):
        """Tier-most-recent decay: adding an ANCIENT same-tier source cannot lower pc."""
        fresh_pc = aggregate_prior([("T1", FRESH, FRESH, 1.0)], recency_decay=0.95)[0] - 1.0
        mixed_pc = aggregate_prior(
            [("T1", FRESH, FRESH, 1.0), ("T1", "1984-01-01T00:00:00+00:00", None, 1.0)],
            recency_decay=0.95,
        )[0] - 1.0
        assert mixed_pc > fresh_pc  # monotone — age-suppression attack blocked

    def test_prior_never_exceeds_1_mean(self):
        for n in [1, 10, 100, 1000]:
            a, b = aggregate_prior([("T0", FRESH, FRESH, 1.0)] * n, recency_decay=1.0)
            assert mean_from_beta(a, b) < 1.0

    def test_unknown_tier_excluded_from_counts(self):
        """Malformed tiers are excluded — they cannot inflate N for other tiers."""
        a, _ = aggregate_prior(
            [("T4", FRESH, FRESH, 1.0), ("T9", FRESH, FRESH, 1.0), ("t1", FRESH, FRESH, 1.0)],
            recency_decay=1.0,
        )
        # Only T4 counts: pc = 0.1 * log2(2) = 0.1 (T9/t1 excluded, N=1 for T4)
        assert a == pytest.approx(1.1, rel=1e-9)


# ═══════════════════════════════════════════════════════════════════════
# Decay
# ═══════════════════════════════════════════════════════════════════════

class TestDecay:
    def test_future_source_date_clamps_to_one(self):
        assert decay_factor("2099-01-01T00:00:00+00:00", None) == 1.0

    def test_pre_epoch_heavy_decay_no_exception(self):
        """Ancient dates decay heavily; no exception (max(0, years) clamp)."""
        assert decay_factor("0001-01-01T00:00:00+00:00", None) < 1.0

    def test_malformed_dates_no_decay(self):
        assert decay_factor("not-a-date", None) == 1.0
        assert decay_factor(None, None) == 1.0

    def test_t0_exempt(self):
        assert decay_factor("2000-01-01T00:00:00+00:00", None, tier="T0") == 1.0

    def test_source_date_preferred_over_ingested(self):
        now = datetime(2024, 6, 1, tzinfo=timezone.utc)
        old_evidence = decay_factor("2020-01-01T00:00:00+00:00", "2024-05-01T00:00:00+00:00", now=now)
        recent_evidence = decay_factor("2024-05-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", now=now)
        assert old_evidence < recent_evidence

    def test_timezone_naive_interpreted_utc(self):
        """Naive timestamps are UTC (#153) — matches test_event_provenance."""
        now = datetime(2024, 1, 2, tzinfo=timezone.utc)
        naive = decay_factor("2024-01-01T00:00:00", None, now=now, recency_decay=0.5)
        aware = decay_factor("2024-01-01T00:00:00+00:00", None, now=now, recency_decay=0.5)
        assert naive == pytest.approx(aware, rel=1e-12)

    def test_formula_matches_legacy(self):
        """alpha' = 1 + (alpha-1)*decay — exact #122 formula at N=1."""
        now = datetime(2024, 1, 2, tzinfo=timezone.utc)
        a, _b = aggregate_prior(
            [("T1", "2024-01-01T00:00:00+00:00", None, 1.0)],
            recency_decay=0.95, now=now,
        )
        expected = 1.0 + (5.0 - 1.0) * 0.95 ** (1.0 / 365.25)
        assert a == pytest.approx(expected, rel=1e-9)


# ═══════════════════════════════════════════════════════════════════════
# Tier resolution + registry
# ═══════════════════════════════════════════════════════════════════════

class TestResolveTier:
    def test_precedence_explicit_credibility_tier(self):
        assert resolve_tier("T1", "github_issue") == "T1"

    def test_sourcekind_tier_form_fallback(self):
        assert resolve_tier(None, "T3") == "T3"

    def test_registry_default(self):
        register_source_kind_default("substack", "T2")
        try:
            assert resolve_tier(None, "substack") == "T2"
            assert resolve_source_tier("substack") == "T2"
        finally:
            SOURCE_KIND_DEFAULTS.pop("substack", None)

    def test_unknown_kind_neutral(self):
        assert resolve_tier(None, "some_new_kind") is None
        assert resolve_source_tier("some_new_kind") is None

    def test_malformed_tiers_neutral(self):
        for bad in ("T9", "t1", "T1 ", "", "T-1"):
            assert resolve_tier(bad, None) is None
            assert resolve_tier(None, bad) is None

    def test_registry_defaults_identity_and_none_only(self):
        """Legacy kinds must be None (neutral) — no auto-mapping (c=0.447)."""
        assert all(v is None for k, v in SOURCE_KIND_DEFAULTS.items() if k not in TIER_PRIORS)
        assert SOURCE_KIND_DEFAULTS["T0"] == "T0"
        assert SOURCE_KIND_DEFAULTS["document"] is None

    def test_register_invalid_tier_raises(self):
        with pytest.raises(ValueError):
            register_source_kind_default("bad", "T9")
        with pytest.raises(ValueError):
            register_source_kind_default("bad", "not-a-tier")


# ═══════════════════════════════════════════════════════════════════════
# Assessment factor
# ═══════════════════════════════════════════════════════════════════════

class TestAssessmentFactor:
    def test_no_assessments_neutral(self):
        assert assessment_factor([]) == 1.0

    def test_zero_track_record_neutral(self):
        """rep=0.5 contributes 0 — a no-track-record assessor cannot move reliability."""
        assert assessment_factor([(0.5, 0.9), (0.5, 0.1)]) == 1.0

    def test_single_assessor_swing_bounded(self):
        """k=1.0: single assessor swing is +-0.25 → factor in [0.75, 1.25]."""
        down = assessment_factor([(1.0, 0.0)])
        up = assessment_factor([(1.0, 1.0)])
        assert down == pytest.approx(0.75)
        assert up == pytest.approx(1.25)

    def test_four_extreme_assessors_reach_clamps(self):
        assert assessment_factor([(1.0, 0.0)] * 4) == FACTOR_MIN
        assert assessment_factor([(1.0, 1.0)] * 4) == FACTOR_MAX

    def test_nan_inf_handled(self):
        assert assessment_factor([(float("nan"), 1.0)]) == 1.0
        assert assessment_factor([(1.0, float("inf"))]) == 1.0
        assert assessment_factor([("bad", 1.0)]) == 1.0

    def test_factor_bounds_never_invert(self):
        """Clamped [0.1, 2.0] — pc_eff >= 0, prior never inverts."""
        for reps, scores in [([1.0] * 10, [0.0] * 10), ([1.0] * 10, [1.0] * 10)]:
            f = assessment_factor(list(zip(reps, scores)))
            assert FACTOR_MIN <= f <= FACTOR_MAX


# ═══════════════════════════════════════════════════════════════════════
# Reliability derivation
# ═══════════════════════════════════════════════════════════════════════

class TestDeriveReliability:
    def test_tiered_matches_modulated_prior_mean(self):
        rel, comp = derive_reliability(
            tier="T1", source_date=FRESH, ingested_at=FRESH, recency_decay=1.0,
        )
        assert rel == pytest.approx(mean_from_beta(1.0 + pc_base("T1"), 1.0))
        assert comp["reason"] is None

    def test_tiered_with_decay(self):
        now = datetime(2024, 1, 2, tzinfo=timezone.utc)
        rel, comp = derive_reliability(
            tier="T1", source_date="2024-01-01T00:00:00+00:00", ingested_at=None,
            recency_decay=0.95, now=now,
        )
        expected = mean_from_beta(1.0 + pc_base("T1") * 0.95 ** (1.0 / 365.25), 1.0)
        assert rel == pytest.approx(expected, rel=1e-9)
        assert comp["decay"] < 1.0

    def test_tiered_with_assessments(self):
        rel, comp = derive_reliability(
            tier="T2", source_date=FRESH, ingested_at=FRESH, recency_decay=1.0,
            assessments=[(1.0, 0.0), (1.0, 0.0), (1.0, 0.0), (1.0, 0.0)],
        )
        # 4 x max-down (score 0) → factor = 1 - 4*0.25 = 0.0 → clamped 0.1
        # pc = 2.0*0.1 = 0.2 → mean = 1.2/2.2 ≈ 0.545
        assert rel == pytest.approx(1.2 / 2.2, rel=1e-9)
        assert comp["factor"] == FACTOR_MIN

    def test_untiered_no_assessments_null(self):
        rel, comp = derive_reliability(tier=None, source_date=None, ingested_at=None)
        assert rel is None
        assert comp["reason"] == "untiered"

    def test_untiered_with_assessments_display_only(self):
        rel, comp = derive_reliability(
            tier=None, source_date=None, ingested_at=None,
            assessments=[(1.0, 0.9), (0.5, 0.5)],
        )
        # reputation-weighted mean of scores — display-only, never EP
        assert rel == pytest.approx((1.0 * 0.9 + 0.5 * 0.5) / (1.0 + 0.5))
        assert comp["reason"] == "untiered; assessment-only"


# ═══════════════════════════════════════════════════════════════════════
# Baseline provenance marker + recompute gate (Task 2 — embedded SDK)
# ═══════════════════════════════════════════════════════════════════════

import tempfile

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """Fresh embedded SDK (no Docker — db_path pattern)."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_cred_"), "test.db")
    s = TortoiseSDK(db_path)
    yield s
    s.close()


def _set_source_tier_raw(sdk, url, tier, ingested_at="2024-01-01T00:00:00+00:00"):
    sdk._get_proj().g.query(
        "MATCH (s:Source {url: $url}) SET s.credibilityTier = $t, s.ingestedAt = $ts",
        params={"url": url, "t": tier, "ts": ingested_at},
    )


class TestBaselineProvenance:
    def test_explicit_default_persists_explicit(self, sdk):
        p = sdk.create_point("statement", "explicit claim")
        sdk.set_point_baseline(p["id"], 5.0, 1.0)
        row = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN n.baseline_source", params={"id": p["id"]}
        ).result_set
        assert row[0][0] == "explicit"

    def test_inherited_source_persists_inherited(self, sdk):
        p = sdk.create_point("statement", "inherited claim")
        sdk.set_point_baseline(p["id"], 5.0, 1.0, source="inherited")
        row = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN n.baseline_source", params={"id": p["id"]}
        ).result_set
        assert row[0][0] == "inherited"

    def test_legacy_baseline_set_true_no_marker_never_clobbered(self, sdk):
        """Legacy NULL+true baselines are explicit — inheritance never overwrites."""
        url = "https://legacy.example"
        p = sdk.create_point("statement", "legacy explicit", extractedFrom=url)
        _set_source_tier_raw(sdk, url, "T0")
        # Simulate a legacy explicit baseline (pre-marker era)
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.ep_alpha=9.0, n.ep_beta=1.0, n.baseline_set=true",
            params={"id": p["id"]},
        )
        sdk._apply_source_inheritance(recency_decay=1.0)
        pt = sdk.get_point(p["id"])
        assert pt["ep_alpha"] == 9.0  # untouched (explicit)
        assert pt["ep_beta"] == 1.0

    def test_inherited_recomputes_when_source_ages(self, sdk):
        """Dynamic decay: aging the sourceDate lowers the inherited prior (interval=0)."""
        url = "https://aging.example"
        p = sdk.create_point("statement", "aging claim", extractedFrom=url)
        _set_source_tier_raw(sdk, url, "T1", ingested_at="2024-01-01T00:00:00+00:00")
        sdk._apply_source_inheritance(recency_decay=0.95, recompute_interval=0)
        alpha1 = sdk.get_point(p["id"])["ep_alpha"]
        # Age the source by 10 years
        sdk._get_proj().g.query(
            "MATCH (s:Source {url:$url}) SET s.sourceDate = $ts",
            params={"url": url, "ts": "2014-01-01T00:00:00+00:00"},
        )
        sdk._apply_source_inheritance(recency_decay=0.95, recompute_interval=0)
        alpha2 = sdk.get_point(p["id"])["ep_alpha"]
        assert alpha2 < alpha1  # decay increased

    def test_time_gate_skips_within_interval(self, sdk):
        """Two calls within the interval → no rewrite (no dirty churn)."""
        url = "https://gated.example"
        p = sdk.create_point("statement", "gated claim", extractedFrom=url)
        _set_source_tier_raw(sdk, url, "T1")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
        alpha1 = sdk.get_point(p["id"])["ep_alpha"]
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
        alpha2 = sdk.get_point(p["id"])["ep_alpha"]
        assert alpha2 == alpha1  # gated — no rewrite

    def test_new_point_inherits_immediately_within_interval(self, sdk):
        """A point created from a tiered source inherits NOW (no hour-long neutral window)."""
        url = "https://newpoint.example"
        p1 = sdk.create_point("statement", "first", extractedFrom=url)  # materializes Source
        _set_source_tier_raw(sdk, url, "T0")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
        # Second point created AFTER the first compute — must inherit immediately
        p2 = sdk.create_point("statement", "second", extractedFrom=url)
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
        pt2 = sdk.get_point(p2["id"])
        assert pt2.get("ep_alpha") == 10.0  # T0, no decay

    def test_edge_deletion_dirty_marks_revert(self, sdk):
        """Deleting the last extractedFrom edge reverts to neutral within the interval."""
        url = "https://revert.example"
        p = sdk.create_point("statement", "revert claim", extractedFrom=url)
        _set_source_tier_raw(sdk, url, "T0")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
        assert sdk.get_point(p["id"]).get("ep_alpha") == 10.0
        # Delete the edge
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$pid})-[e:extractedFrom]->(s:Source {url:$url}) DELETE e",
            params={"pid": p["id"], "url": url},
        )
        sdk._invalidate_inheritance_gate([p["id"]])
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
        # No eligible sources → inherited baseline removed, back to neutral
        pt = sdk.get_point(p["id"])
        assert pt.get("ep_alpha") in (None, 1.0), f"expected neutral, got {pt.get('ep_alpha')}"
        assert pt.get("baseline_source") != "inherited"

    def test_two_sdk_instances_dedupe_within_interval(self, sdk):
        """Graph-persisted gate: a second SDK instance within the interval dedupes."""
        url = "https://multi.example"
        p = sdk.create_point("statement", "multi-instance claim", extractedFrom=url)
        _set_source_tier_raw(sdk, url, "T1")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
        alpha1 = sdk.get_point(p["id"])["ep_alpha"]

        # Second SDK instance on the SAME db file
        db_path = sdk._db_path
        sdk2 = TortoiseSDK(db_path)
        try:
            sdk2._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
            alpha2 = sdk2.get_point(p["id"])["ep_alpha"]
            assert alpha2 == alpha1
            # And the gate held — inherited_at present means no rewrite needed
            row = sdk2._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN n.inherited_at IS NOT NULL",
                params={"id": p["id"]},
            ).result_set
            assert row[0][0] is True
        finally:
            sdk2.close()

    def test_epsilon_guard_no_rewrite_on_unchanged(self, sdk):
        """interval=0 recompute of unchanged values → no ep_alpha rewrite churn."""
        url = "https://epsilon.example"
        p = sdk.create_point("statement", "epsilon claim", extractedFrom=url)
        _set_source_tier_raw(sdk, url, "T2")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        alpha1 = sdk.get_point(p["id"])["ep_alpha"]
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        alpha2 = sdk.get_point(p["id"])["ep_alpha"]
        assert alpha2 == alpha1


class TestLowFactorFloodBound:
    def test_low_factor_flood_bounded(self):
        """A flood of heavily-downweighted sources can't erase evidence below the
        single-source contribution (pinned behavior — documented design tension
        from code review: the factor mean is bounded by the [0.1, 2.0] clamp and
        the log2 growth, so the tier contribution stays O(1) and never zeroes)."""
        base = aggregate_prior([("T3", FRESH, FRESH, 1.0)], recency_decay=1.0)[0] - 1.0
        # 100 sources with the 0.1 clamp floor factor
        flooded = aggregate_prior(
            [("T3", FRESH, FRESH, 0.1)] * 100, recency_decay=1.0
        )[0] - 1.0
        # log2(101) * 0.1 * 1.0 = 6.66 * 0.1 = 0.666 (not zero; bounded by floor)
        assert flooded > 0.5
        # Single strong source still beats the diluted flood
        assert base >= flooded * 0.9
