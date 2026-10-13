"""Pure-math tests for tortoise.source_credibility (issue #398).

Verifies the pinned log-scale aggregation model, decay, tier resolution, and
assessment factor — no graph, no EP, no Docker (embedded-safe).
"""
from __future__ import annotations

import math
import os
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.exceptions import CalibrationError
from tortoise.source_credibility import (
    AUTHOR_ASSERTION_WEAK_PC,
    FACTOR_MAX,
    FACTOR_MIN,
    SOURCE_KIND_DEFAULTS,
    TIER_PRIORS,
    aggregate_prior,
    assessment_factor,
    author_support_pc,
    decay_factor,
    derive_reliability,
    mean_from_beta,
    pc_base,
    register_source_kind_default,
    resolve_source_tier,
    resolve_tier,
    session_support_pc,
    weak_support_pc,
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
        now = datetime(2024, 6, 1, tzinfo=timezone.utc)  # noqa: UP017
        old_evidence = decay_factor("2020-01-01T00:00:00+00:00", "2024-05-01T00:00:00+00:00", now=now)
        recent_evidence = decay_factor("2024-05-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", now=now)
        assert old_evidence < recent_evidence

    def test_timezone_naive_interpreted_utc(self):
        """Naive timestamps are UTC (#153) — matches test_event_provenance."""
        now = datetime(2024, 1, 2, tzinfo=timezone.utc)  # noqa: UP017
        naive = decay_factor("2024-01-01T00:00:00", None, now=now, recency_decay=0.5)
        aware = decay_factor("2024-01-01T00:00:00+00:00", None, now=now, recency_decay=0.5)
        assert naive == pytest.approx(aware, rel=1e-12)

    def test_formula_matches_legacy(self):
        """alpha' = 1 + (alpha-1)*decay — exact #122 formula at N=1."""
        now = datetime(2024, 1, 2, tzinfo=timezone.utc)  # noqa: UP017
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

    def test_meeting_capture_kinds_registered_neutral(self):
        """#2726: both meeting-capture kinds resolve NEUTRAL (tier deferred).

        Membership is asserted explicitly — ``.get()`` returns None for an
        ABSENT key too, so the tier check alone cannot tell registered-NEUTRAL
        from never-registered.
        """
        for kind in ("meeting_transcript", "meeting_minutes"):
            assert kind in SOURCE_KIND_DEFAULTS
            assert SOURCE_KIND_DEFAULTS[kind] is None
            assert resolve_source_tier(kind) is None
            assert resolve_tier(None, kind) is None

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
            f = assessment_factor(list(zip(reps, scores)))  # noqa: B905
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
        now = datetime(2024, 1, 2, tzinfo=timezone.utc)  # noqa: UP017
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

import tempfile  # noqa: E402

from tortoise.sdk import TortoiseSDK  # noqa: E402


@pytest.fixture
def sdk():
    """Fresh embedded SDK (no Docker — db_path pattern)."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_cred_"), "test.db")
    s = TortoiseSDK(db_path)
    yield s
    s.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


def _set_source_tier_raw(sdk, url, tier, ingested_at="2024-01-01T00:00:00+00:00"):
    sdk._get_proj().g.query(
        "MATCH (s:Source {url: $url}) SET s.credibilityTier = $t, s.ingestedAt = $ts",
        params={"url": url, "t": tier, "ts": ingested_at},
    )


def _simulate_replay_outcome(sdk, pid: str) -> None:
    """Reproduce the state a `rebuild_all` leaves on a Point.

    `credibility` and `baseline_system_default` are DECLARED Point props and
    replay (`_POINT_DECLARED_PROPS`), while `ep_alpha` / `ep_beta` /
    `baseline_set` / `baseline_source` sit in `_POINT_DENY` and do NOT — so a
    rebuilt word-bearing Point holds its label with NO baseline.
    """
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha = null, n.ep_beta = null, "
        "n.baseline_set = null, n.baseline_source = null",
        params={"id": pid},
    )


class TestBaselineProvenance:
    def test_explicit_default_persists_explicit(self, sdk):
        """Default provenance token is set-by-author (#2199 rename of 'explicit')."""
        p = sdk.create_point("statement", "explicit claim")
        sdk.set_point_baseline(p["id"], 5.0, 1.0)
        row = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN n.baseline_source", params={"id": p["id"]}
        ).result_set
        assert row[0][0] == "set-by-author"

    def test_inherited_source_persists_inherited(self, sdk):
        p = sdk.create_point("statement", "inherited claim")
        sdk.set_point_baseline(p["id"], 5.0, 1.0, source="inherited-from-source")
        row = sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) RETURN n.baseline_source", params={"id": p["id"]}
        ).result_set
        assert row[0][0] == "inherited-from-source"

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

    def test_legacy_pre_2199_token_rows_are_frozen_against_recompute(self, sdk):
        """#6151 review round 6 P2 — the pre-#2199 token spellings are frozen.

        `set_point_baseline` REFUSES to write 'explicit'/'inherited' (they were
        renamed in #2199), but a graph the one-time rename migration has not
        been run against still carries them — a state `calibrate_summary`
        anticipates and reports with the fix path. The freeze predicate's old
        allow-list (`bl_src in (set-by-author, system-default)`) matched neither
        spelling, so a legacy `'explicit'` row — label-less and AUTHORED, the
        exact #844 class this guard exists for — fell through to the recompute
        and was silently overwritten (measured: 0.4/'explicit' -> 5.0/
        'inherited-from-source'), while origin/main kept it. The polarity is now
        "freeze everything that is not a derivation": a token that is not the
        CURRENT `inherited-from-source` spelling — including an unrecognised
        one — is frozen, so no legacy or future spelling can silently overwrite
        a belief.

        The re-baseline loop below is the NON-VACUITY control: the source edge is
        live (the same row with `inherited-from-source` recomputes to 5.0), so
        the freeze is the token class, not a dead edge.
        """
        url = "https://legacy-token.example"
        sdk.create_source(url, "document", tier="T1", sourceDate=FRESH)
        pid = sdk.create_point("statement", "legacy token row",
                               extractedFrom=url)["id"]

        def _recompute_with_token(token):
            sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) SET n.ep_alpha=0.4, n.ep_beta=1.0, "
                "n.baseline_set=true, n.baseline_source=$tok",
                params={"id": pid, "tok": token},
            )
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
            return sdk.get_point(pid)

        for legacy_token in ("explicit", "inherited", "banana"):
            pt = _recompute_with_token(legacy_token)
            assert pt["ep_alpha"] == pytest.approx(0.4), (legacy_token, pt)
            assert pt["baseline_source"] == legacy_token, (legacy_token, pt)

        # CONTROL — the derived spelling is NOT frozen and does recompute.
        pt = _recompute_with_token("inherited-from-source")
        assert pt["ep_alpha"] == pytest.approx(5.0), pt
        assert pt["baseline_source"] == "inherited-from-source", pt

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
        p1 = sdk.create_point("statement", "first", extractedFrom=url)  # materializes Source  # noqa: F841
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
        assert pt.get("baseline_source") != "inherited-from-source"

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

    def test_baseline_system_default_cannot_be_forged_via_props(self, sdk):
        """#6151 review P3 — the recorded #2199 birth fact is SERVER-owned.

        `_apply_source_inheritance` re-injects the +2.0 default ONLY on a point
        carrying `baseline_system_default`, so a caller able to set that prop
        could self-grant the default on a point that was never born live. It is
        refused at the props boundary (`_sanitize_props`, shared by
        `create_point` and `update_point`) — the same hard-reject mechanism as
        `embedding_verbatim`, chosen over strip-and-ignore because there is no
        legitimate caller intent to substitute, so a silent strip would let an
        attempted self-grant pass unnoticed.
        """
        with pytest.raises(ValueError, match="baseline_system_default"):
            sdk.create_point("option", "forged default", status="draft",
                             baseline_system_default=True)
        # A legitimately-created explicit-status part carries NO such marker.
        p = sdk.create_point("option", "plain draft", status="draft")["id"]
        assert sdk.get_point(p).get("baseline_system_default") is None
        with pytest.raises(ValueError, match="baseline_system_default"):
            sdk.update_point(p, baseline_system_default=True)
        assert sdk.get_point(p).get("baseline_system_default") is None, (
            "a rejected update must not have written the marker")

    def test_credibility_is_not_a_generic_property(self, sdk):
        """#7951 — ``credibility`` is a documented WORD INPUT, not a generic
        prop.

        On the generic bag it bypassed the authored-assertion accounting
        (measured: an INERT label on a born-live decide part, frozen at 3.0
        even under a real T1 source; a label with no baseline and no body; an
        unvalidated/uncanonicalised value stored verbatim). It is refused on
        every generic surface — ``update_point`` / ``create_entity`` /
        ``update_entity`` — with a pointer at the documented input, and nothing
        is written.
        """
        p = sdk.create_point("statement", "no word")["id"]
        assert sdk.get_point(p).get("credibility") is None
        with pytest.raises(ValueError, match="credibility"):
            sdk.update_point(p, credibility="gold")
        with pytest.raises(ValueError, match="credibility"):
            sdk.update_entity(p, credibility="banana")
        with pytest.raises(ValueError, match="credibility"):
            sdk.create_entity("subject", "generic cred", credibility="gold")
        assert sdk.get_point(p).get("credibility") is None, (
            "a rejected generic write must not have stored the label")

    def test_baseline_fields_are_not_generic_properties(self, sdk):
        """#6151 review P3-2 / #7951 / round 6 P1 — the generic bag refuses the
        BASELINE fields too, not only ``credibility``.

        ``ep_alpha``/``ep_beta``/``baseline_set``/``baseline_source`` belong to
        the dedicated writers (``set_point_baseline``, the inheritance
        recompute), which write them together and keep the #398 label in sync.
        Through a generic bag they bypassed that: ``ep_alpha=0.4`` on a
        word-bearing ``set-by-author`` row left the label intact and wrote a
        "frozen" value the recompute then replaced (measured 0.4 -> 5.1), and
        ``baseline_source='system-default'`` forged the #2199 provision.

        ROUND 6 P1: this reject is UNGATED (it used to sit behind
        `reject_belief_props`, which `create_point` never passes). The create
        map is built as `_create_map.update(_baseline_create_fields())` BEFORE
        the caller's props loop, so a caller's `ep_alpha`/`baseline_source`
        OVERRODE the writer's own baseline values at birth — measured as a
        FROZEN 10.0 a real T1 source could never move. The `create_point`
        cases below fail on the pre-fix code (they were accepted and stored).
        """
        p = sdk.create_point("statement", "no generic baseline")["id"]
        for key, val in (("ep_alpha", 0.4), ("ep_beta", 1.0),
                         ("baseline_set", True),
                         ("baseline_source", "set-by-author")):
            with pytest.raises(ValueError, match=key):
                sdk.update_point(p, **{key: val})
            # #6151 round 6 P1: the SAME keys are refused at birth, where the
            # caller's value would beat `_baseline_create_fields`.
            with pytest.raises(ValueError, match=key):
                sdk.create_point("statement", f"forged {key} birth", **{key: val})
        with pytest.raises(ValueError, match="baseline_source"):
            sdk.update_entity(p, baseline_source="system-default")
        with pytest.raises(ValueError, match="ep_alpha"):
            sdk.create_entity("subject", "generic baseline", ep_alpha=0.4)
        # The whole quartet at once (the measured forgery) and the forged
        # #2199 provision token must both be refused before any write.
        with pytest.raises(ValueError, match="baseline_set"):
            sdk.create_point(
                "statement", "forged strong birth", status="live",
                ep_alpha=10.0, ep_beta=1.0, baseline_set=True,
                baseline_source="set-by-author")
        with pytest.raises(ValueError, match="baseline_source"):
            sdk.create_point(
                "statement", "forged sysdefault birth", status="live",
                ep_alpha=3.0, ep_beta=1.0, baseline_set=True,
                baseline_source="system-default")
        # The reviewer's third probe: a WORD plus a forged strong prior — the
        # ruling's own symptom (`credibility='T0'` on a mean-0.9091 prior)
        # reappearing through a non-word key.
        with pytest.raises(ValueError, match="ep_alpha"):
            sdk.create_point(
                "statement", "word and forged prior", status="live",
                credibility="gold", ep_alpha=10.0, ep_beta=1.0)
        pt = sdk.get_point(p)
        assert pt.get("baseline_set") in (None, False), (
            "a rejected generic write must not have stored a baseline")
        assert pt.get("ep_alpha") is None, pt

    def test_mcp_boundary_refuses_the_belief_quartet_at_birth(self):
        """#6151 review round 6 P1 — the MCP prop boundary refuses the quartet.

        `tortoise_create_point` does `merged = dict(props or {})` and splats it
        into `sdk.create_point(**merged)`. The SDK reject is the fail-closed
        backstop; `_SERVER_MANAGED_PROPS` is the boundary check IN FRONT of it,
        matching the `embedding_*`/`rawState` pattern. `credibility` stays out
        of the set — it is a documented `create_point`/`mitigate_operator`
        input the SDK validates and accounts.
        """
        from tortoise import mcp_server
        for key in ("ep_alpha", "ep_beta", "baseline_set", "baseline_source",
                    "baseline_system_default"):
            assert key in mcp_server._SERVER_MANAGED_PROPS, (
                f"{key} left the MCP server-managed boundary")
            assert mcp_server._reject_server_managed_props({key: 1}), (
                f"{key} was not rejected at the MCP boundary")
        assert "credibility" not in mcp_server._SERVER_MANAGED_PROPS, (
            "credibility is a documented create_point/mitigate_operator input "
            "and must stay accepted at the boundary")
        assert not mcp_server._reject_server_managed_props(
            {"credibility": "gold"}), "the word was refused at the boundary"

    def test_system_default_token_cannot_forge_the_2199_provision(self, sdk):
        """#6151 review P1(b) — the ``baseline_system_default`` marker is
        granted ONLY by the birth path, never inferred from a caller-supplied
        ``baseline_source`` token.

        The dedup path used to read ``baseline_source == 'system-default'`` as
        proof of the #2199 provision. That token is writeable through
        ``set_point_baseline(source=…)``, so a plain (never-born-live)
        statement with a T4 source could park itself there, take a dedup word,
        and collect +2.0 unearned (measured 1.2 -> 3.2). The token is no longer
        trusted, so the forged point stays frozen at the value the caller
        actually set, with no marker.
        """
        sdk.create_source("https://p1b-t4.example", "document", tier="T4",
                          sourceDate=FRESH)
        honest = sdk.create_point("statement", "honest p1b", status="live",
                                  extractedFrom="https://p1b-t4.example")["id"]
        sdk.create_point("statement", "honest p1b", dedup=True,
                         credibility="gold")
        forged = sdk.create_point("statement", "forged p1b", status="live",
                                  extractedFrom="https://p1b-t4.example")["id"]
        # The forgery: claim the #2199 provision through the provenance token.
        sdk.set_point_baseline(forged, 1.0, 1.0, source="system-default")
        sdk.create_point("statement", "forged p1b", dedup=True,
                         credibility="gold")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        h, f = sdk.get_point(honest), sdk.get_point(forged)
        assert h["ep_alpha"] == pytest.approx(1.2), h  # T4 + the weak word
        assert f.get("baseline_system_default") is None, f
        assert f["ep_alpha"] == pytest.approx(1.0), f  # no unearned +2.0
        # The generic surface refuses the same provenance outright.
        with pytest.raises(ValueError, match="baseline_source"):
            sdk.update_point(forged, baseline_source="system-default",
                             baseline_set=True, ep_alpha=1.0, ep_beta=1.0)

    def test_label_bearing_point_keeps_its_prior_when_sources_are_removed(
            self, sdk):
        """#6151 review P3-1 — the ``credibility IS NULL`` guard on the REVERT
        pass is load-bearing, and before this test it was UNFALSIFIED: removing
        it left every other test green.

        A word-bearing Point is an authored assertion, so it is never reverted
        to neutral even when ALL of its source edges are deleted — its own weak
        body keeps a prior. Without the guard the revert REMOVEs
        ``ep_alpha``/``ep_beta``/``baseline_*`` and the point becomes
        uncalibrated.
        """
        url = "https://p31.example"
        p = sdk.create_point("statement", "worded, then orphaned",
                             status="live", credibility="gold",
                             extractedFrom=url)["id"]
        sdk.create_source(url, "document", tier="T1", sourceDate=FRESH)
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        assert sdk.get_point(p)["ep_alpha"] == pytest.approx(5.1)  # T1 + word
        # Delete the ONLY source edge — the revert pass would otherwise fire.
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$pid})-[e:extractedFrom]->(s:Source {url:$url}) "
            "DELETE e",
            params={"pid": p, "url": url},
        )
        sdk._invalidate_inheritance_gate([p])
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        pt = sdk.get_point(p)
        # NOT reverted to neutral: the authored assertion is still a source.
        assert pt["ep_alpha"] == pytest.approx(5.1), pt
        assert pt["credibility"] == "T0", pt


class TestAuthorAsSourceAndDifference:
    """#398 + #6151 — an authored assertion is a SOURCE and the aggregation
    keeps the difference SHAPE, but is SUPPORT-ONLY today.

    The author's declared word is ONE evidence body at the weakest registered
    weight, the SAME whoever wrote it (owner ruling 2026-10-12): the word is a
    validated LABEL, not a strength. Contradiction stays EP's domain
    (`phi_nand`), so a NAND edge never enters this prior — folding it here is
    the rejected alternative (#6151).
    """

    def test_support_only_prior_is_the_pre_change_output(self):
        """No source-level dissent signal exists -> the default return is the
        positive-only prior (beta = 1), byte-for-byte."""
        assert aggregate_prior([("T1", FRESH, FRESH, 1.0)], recency_decay=1.0) == \
            TIER_PRIORS["T1"]

    def test_difference_shape_is_retained_for_a_future_dissent_signal(self):
        """The ``(1 + max(net, 0), 1 + max(-net, 0))`` shape still subtracts a
        negative net (kept for a future SOURCE-LEVEL dissent input), and no
        ``nc_base(tier)`` negative pseudo-count is defined anywhere."""
        alpha, beta = aggregate_prior([], recency_decay=1.0, extra_support_pc=-4.0)
        assert (alpha, beta) == (1.0, 5.0)

    def test_author_prior_has_no_attacks_parameter(self):
        """A NAND edge can never enter this prior: the signature exposes no
        such input — folding it is the rejected alternative (#6151)."""
        import inspect
        params = inspect.signature(aggregate_prior).parameters
        assert "attacks" not in params
        assert "extra_support_pc" in params

    def test_superseded_claim_takes_its_own_source_strength(self):
        """(c) a newer reading from the SAME source does not invent or erase
        strength — it is the source's own tier."""
        alpha, beta = aggregate_prior([("T1", FRESH, FRESH, 1.0)], recency_decay=1.0)
        assert (alpha, beta) == TIER_PRIORS["T1"]
        assert mean_from_beta(alpha, beta) == pytest.approx(5.0 / 6.0)

    def test_word_combines_with_a_source_not_a_ceiling(self):
        """The word is ONE weak body that combines with the claim's sources —
        a stronger source can carry the claim above what the author declared.
        weak (0.1) + one T1 source (pc 4.0) -> 1 + 0.1 + 4.0 = 5.1."""
        weak = author_support_pc("gold")
        assert weak == AUTHOR_ASSERTION_WEAK_PC
        alpha, _beta = aggregate_prior(
            [("T1", FRESH, FRESH, 1.0)], recency_decay=1.0,
            extra_support_pc=weak,
        )
        assert alpha == pytest.approx(5.1)
        assert mean_from_beta(alpha, 1.0) > mean_from_beta(*TIER_PRIORS["T3"])

    def test_word_weight_is_one_constant_the_same_for_both_authors(self):
        """The rule: one weak weight, the same whoever wrote it. The word is
        a validated LABEL — it does not select a weight, and it is the weakest
        REGISTERED weight (T4's pc), so no word outranks any real source."""
        assert author_support_pc("gold") == AUTHOR_ASSERTION_WEAK_PC
        assert author_support_pc("low") == AUTHOR_ASSERTION_WEAK_PC
        assert author_support_pc("gold") == author_support_pc("unverified")
        assert pytest.approx(pc_base("T4")) == AUTHOR_ASSERTION_WEAK_PC
        alpha, _beta = aggregate_prior(
            [], recency_decay=1.0,
            extra_support_pc=author_support_pc("gold"),
        )
        assert alpha == pytest.approx(TIER_PRIORS["T4"][0])  # 1.1 -> 0.5238

    def test_unknown_word_is_rejected_not_weighted(self):
        """The ladder vocabulary is still VALIDATED (kept as a label): a value
        outside it is rejected (None), never silently given a weight."""
        assert author_support_pc("very-sure") is None
        assert author_support_pc("T9") is None
        assert author_support_pc(None) is None

    def test_originating_agent_session_weak_carrying_zero(self):
        """#398 carrier rule: originating -> weak, carrying -> provenance (zero)."""
        assert session_support_pc(carries_attached_source=False) == \
            AUTHOR_ASSERTION_WEAK_PC
        assert session_support_pc(carries_attached_source=True) == 0.0

    def test_weak_flood_grows_sublinearly(self):
        """Same log2(N+1) anti-Sybil law as a tier: 1000 stacked assertions
        still lose to a single low-tier (T3) source."""
        flood = weak_support_pc(1000)
        assert flood < pc_base("T3")
        assert flood > weak_support_pc(1)  # still a real, growing contribution
        a_flood, _ = aggregate_prior([], recency_decay=1.0,
                                     extra_support_pc=flood)
        a_t3, _ = aggregate_prior([("T3", FRESH, FRESH, 1.0)],
                                  recency_decay=1.0)
        assert mean_from_beta(a_flood, 1.0) < mean_from_beta(a_t3, 1.0)

    def test_no_attacks_preserves_the_positive_only_prior(self):
        """Default call is byte-for-byte the pre-change output (beta = 1)."""
        assert aggregate_prior([("T1", FRESH, FRESH, 1.0)], recency_decay=1.0) == \
            TIER_PRIORS["T1"]


class TestAuthorAsSourceIntegration:
    """The ruled weights through the real graph path (`_apply_source_inheritance`)."""

    def test_unsourced_assertion_alone_is_the_weak_body(self, sdk):
        """#398 owner ruling 2026-10-12: a person-typed `gold` with NO source
        starts at ONE weak body (0.523), not the 10:1 T0 prior (0.909); the
        word is kept as a LABEL on the Point."""
        p = sdk.create_point("statement", "person typed gold", status="live",
                             credibility="gold")["id"]
        pt = sdk.get_point(p)
        assert pt["ep_alpha"] == pytest.approx(TIER_PRIORS["T4"][0])  # 1.1
        assert pt["ep_beta"] == pytest.approx(1.0)
        assert pt["credibility"] == "T0"  # the declared word, kept as a label
        assert pt["baseline_source"] == "set-by-author"

    def test_originating_agent_session_is_weak(self, sdk):
        sdk.create_source("session:orig", "agentSession")
        p = sdk.create_point("statement", "agent reasoned it",
                             extractedFrom="session:orig")
        sdk._apply_source_inheritance(recency_decay=1.0)
        pt = sdk.get_point(p["id"])
        assert pt["ep_alpha"] == pytest.approx(1.0 + weak_support_pc(1))
        assert pt["ep_beta"] == 1.0
        assert pt["ep_alpha"] == pytest.approx(TIER_PRIORS["T4"][0])  # weakest rung

    def test_carrying_agent_session_contributes_zero(self, sdk):
        """A session that `references` an attached document is provenance."""
        sdk.create_source("https://doc.example", "document", tier="T1")
        sdk.create_source("session:carrier", "agentSession")
        sdk._get_proj().g.query(
            "MATCH (a:Source {url:$a}),(b:Source {url:$b}) "
            "CREATE (a)-[:references]->(b)",
            params={"a": "session:carrier", "b": "https://doc.example"},
        )
        p = sdk.create_point("statement", "carried document claim",
                             extractedFrom="https://doc.example")
        sdk._get_proj()._link_source(p["id"], "session:carrier")
        sdk._apply_source_inheritance(recency_decay=1.0)
        pt = sdk.get_point(p["id"])
        # T1 document only — the carrying session adds nothing
        assert pt["ep_alpha"] == pytest.approx(TIER_PRIORS["T1"][0])

    @pytest.mark.parametrize("session_kind", ["carrier", "originator"])
    def test_no_session_kind_unfreezes_an_explicit_baseline(
            self, sdk, session_kind):
        """#844 immutability — an explicit `set_point_baseline` survives ANY
        session, CARRIER or ORIGINATOR.

        The #2199/#844 guard now has EXACTLY ONE exception: the #398
        `credibility` label (a word-bearing point is an authored assertion
        whose weak body must be displaceable by a real source). An agent
        session is evidence of a DIFFERENT write path, and letting EITHER kind
        re-open an explicit baseline silently replaced the author's value
        (measured `0.4 -> 1.1`, `set-by-author -> inherited-from-source`): the
        carrying case was the round-2 regression this test first pinned, the
        originating case the round-5 one. Both must now stay
        0.4 / 'set-by-author' / no label.
        """
        sdk.create_source("https://explicitdoc.example", "document", tier="T1",
                          sourceDate=FRESH)
        sess = f"session:explicit-{session_kind}"
        sdk.create_source(sess, "agentSession")
        if session_kind == "carrier":
            sdk._get_proj().g.query(
                "MATCH (a:Source {url:$a}),(b:Source {url:$b}) "
                "CREATE (a)-[:references]->(b)",
                params={"a": sess, "b": "https://explicitdoc.example"},
            )
        p = sdk.create_point("statement", f"explicit baseline + {session_kind}",
                             status="live",
                             extractedFrom="https://explicitdoc.example")["id"]
        # The author's explicit belief — #844 says this is immutable evidence.
        sdk.set_point_baseline(p, 0.4, 1.0, source="set-by-author")
        sdk._get_proj()._link_source(p, sess)
        before = sdk.get_point(p)
        assert before["ep_alpha"] == pytest.approx(0.4), before
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        after = sdk.get_point(p)
        assert after["ep_alpha"] == pytest.approx(0.4), after
        assert after["ep_beta"] == pytest.approx(1.0), after
        assert after["baseline_source"] == "set-by-author", after
        assert after.get("credibility") is None, after  # no word ⇒ no exception

    def test_word_is_a_source_combined_with_the_claim_sources(self, sdk):
        """An authored gold word + a T1 source is the SOURCE aggregate
        (1 + 0.1 + 4 = 5.1) — the word is one weak body, not the claim's own
        frozen strength, and the real source displaces it."""
        sdk.create_source("https://pw.example", "document", tier="T1",
                          sourceDate=FRESH)
        p = sdk.create_point("statement", "gold word + T1 source", status="live",
                             credibility="gold", extractedFrom="https://pw.example")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        pt = sdk.get_point(p["id"])
        assert pt["ep_alpha"] == pytest.approx(5.1)
        assert pt["baseline_source"] == "inherited-from-source"

    def test_agent_word_is_weak_not_the_declared_tier(self, sdk):
        """An agent-authored gold claim: the agentSession IS the evidence body
        (weak); the word is not a T0 source, so the prior collapses from 0.909
        to the weakest registered weight, exactly like a person's word."""
        sdk.create_source("session:agent-gold", "agentSession")
        p = sdk.create_point("statement", "agent asserts gold", status="live",
                             credibility="gold",
                             extractedFrom="session:agent-gold")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        pt = sdk.get_point(p["id"])
        assert pt["ep_alpha"] == pytest.approx(1.0 + weak_support_pc(1))
        assert pt["ep_alpha"] == pytest.approx(TIER_PRIORS["T4"][0])

    def test_nand_edge_never_changes_the_prior(self, sdk):
        """#6151 rejected alternative: the prior reads NO NAND edge. The same
        T1 topology gives Beta(5,1) whether or not the refutes edge exists."""
        sdk.create_source("https://na.example", "document", tier="T1",
                          sourceDate=FRESH)
        sdk.create_source("https://nb.example", "document", tier="T1",
                          sourceDate=FRESH)
        a = sdk.create_point("statement", "A", status="live",
                             extractedFrom="https://na.example")["id"]
        b = sdk.create_point("statement", "B", status="live",
                             extractedFrom="https://nb.example")["id"]
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        assert sdk.get_point(a)["ep_alpha"] == pytest.approx(5.0)
        sdk.create_operator("NAND", b, [a])
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        assert sdk.get_point(a)["ep_alpha"] == pytest.approx(5.0)
        assert sdk.get_point(a)["ep_beta"] == pytest.approx(1.0)

    def test_equal_t1_pair_under_nand_settles_669_post_ep(self, sdk):
        """(a) POST-EP: the symmetric ``phi_nand`` makes BOTH sides pay, so an
        equal T1 pair settles at ~0.6689, not the prior-level 0.5. Expected and
        correct under this design — contradiction stays EP's domain."""
        sdk.create_source("https://ea.example", "document", tier="T1",
                          sourceDate=FRESH)
        sdk.create_source("https://eb.example", "document", tier="T1",
                          sourceDate=FRESH)
        a = sdk.create_point("statement", "A", status="live",
                             extractedFrom="https://ea.example")["id"]
        b = sdk.create_point("statement", "B", status="live",
                             extractedFrom="https://eb.example")["id"]
        sdk.create_operator("NAND", b, [a])
        res = sdk.compute_confidence(anchors=[a, b], max_hops=2,
                                     recency_decay=1.0,
                                     require_calibration=False)
        ca = res["confidences"][a]["mean"]
        cb = res["confidences"][b]["mean"]
        assert ca == pytest.approx(0.6689, abs=0.005)
        assert cb == pytest.approx(0.6689, abs=0.005)

    def test_weak_agent_claim_sinks_below_half_under_a_weak_refuter(self, sdk):
        """(b) POST-EP: an agent-authored claim has no self-declared strength to
        defend (prior 0.5238), so a weak T4 refutation pushes it below 0.5.
        Identical to the person case — the weight no longer varies by author."""
        sdk.create_source("session:agent-b", "agentSession")
        c = sdk.create_point("statement", "agent reasoned it", status="live",
                             credibility="gold",
                             extractedFrom="session:agent-b")["id"]
        sdk.create_source("https://weak.example", "document", tier="T4",
                          sourceDate=FRESH)
        w = sdk.create_point("statement", "weak refuter", status="live",
                             extractedFrom="https://weak.example")["id"]
        sdk.create_operator("NAND", w, [c])
        res = sdk.compute_confidence(anchors=[c, w], max_hops=2,
                                     recency_decay=1.0,
                                     require_calibration=False)
        assert res["confidences"][c]["mean"] < 0.5

    def test_person_word_claim_sinks_below_half_under_a_weak_refuter(self, sdk):
        """THE KEY MEASUREMENT (owner ruling 2026-10-12): a person-typed `gold`
        with NO source must land BELOW 0.5 when refuted by a weak T4 source.
        Before the ruling a person's word carried the T0 prior, so the same
        refutation only moved 0.9091 -> 0.8992."""
        c = sdk.create_point("statement", "person typed gold", status="live",
                             credibility="gold")["id"]
        sdk.create_source("https://weak.example", "document", tier="T4",
                          sourceDate=FRESH)
        w = sdk.create_point("statement", "weak refuter", status="live",
                             extractedFrom="https://weak.example")["id"]
        sdk.create_operator("NAND", w, [c])
        res = sdk.compute_confidence(anchors=[c, w], max_hops=2,
                                     recency_decay=1.0,
                                     require_calibration=False)
        assert res["confidences"][c]["mean"] < 0.5

    def test_cancellation_through_a_chain(self, sdk):
        """(d) cancellation through a chain is POST-EP, not prior-level: a
        refuted premise keeps its source-derived prior and a downstream claim
        sees the contradiction only through EP. Pinned: the priors stay
        Beta(5,1) and the chain settles at the balanced 0.6689."""
        sdk.create_source("https://cha.example", "document", tier="T1",
                          sourceDate=FRESH)
        sdk.create_source("https://chb.example", "document", tier="T1",
                          sourceDate=FRESH)
        a = sdk.create_point("statement", "chain A", status="live",
                             extractedFrom="https://cha.example")["id"]
        b = sdk.create_point("statement", "chain B", status="live",
                             extractedFrom="https://chb.example")["id"]
        sdk.create_operator("NAND", b, [a])
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        # the contradiction is NOT in the prior
        assert sdk.get_point(a)["ep_alpha"] == pytest.approx(5.0)
        assert sdk.get_point(b)["ep_alpha"] == pytest.approx(5.0)
        res = sdk.compute_confidence(anchors=[a, b], max_hops=2,
                                     recency_decay=1.0,
                                     require_calibration=False)
        assert res["confidences"][a]["mean"] == pytest.approx(0.6689, abs=0.005)
        assert res["confidences"][b]["mean"] == pytest.approx(0.6689, abs=0.005)


    def test_explicit_status_part_gets_no_2199_default_in_recompute(self, sdk):
        """#6151 Defect 2 — the #2199 default is injected ONLY where #2199
        actually grants one: a decide part BORN live (no explicit status). An
        explicit-status part keeps full manual control, so a word there adds its
        OWN weak body and nothing more — never an unearned +2.1.

        Measured before the defect fix: an explicit-status draft option with a
        word + a T4 source landed at 0.7619 (the word acting as if #2199 had
        granted it a 2.0 default). After: the word's one weak body + the T4
        source -> 1.2 (0.5455), versus the same part with NO word -> 1.1
        (0.5238); the labelled claim stays >= the unlabelled one. The born-live
        part really did receive the provision and keeps it (3.0 + word + T4).
        """
        sdk.create_source("https://d2t4.example", "document", tier="T4",
                          sourceDate=FRESH)
        word = sdk.create_point("option", "draft word", status="draft",
                                credibility="gold",
                                extractedFrom="https://d2t4.example")["id"]
        plain = sdk.create_point("option", "draft no word", status="draft",
                                 extractedFrom="https://d2t4.example")["id"]
        live_word = sdk.create_point("option", "live word",
                                     credibility="gold",
                                     extractedFrom="https://d2t4.example")["id"]
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        w, p, lw = (sdk.get_point(i) for i in (word, plain, live_word))
        # The explicit-status part recorded NO provision, so the recompute must
        # not invent one: the word is its own 0.1 weak body, over the T4 0.1.
        assert w.get("baseline_system_default") is None, w
        assert w["ep_alpha"] == pytest.approx(1.2), w
        assert p["ep_alpha"] == pytest.approx(1.1), p
        assert w["ep_alpha"] >= p["ep_alpha"]  # labelled >= unlabelled
        # The born-live part DID receive the #2199 provision and keeps it.
        assert lw.get("baseline_system_default") is True, lw
        assert lw["ep_alpha"] == pytest.approx(3.0 + 0.1 + 0.1), lw
        assert lw["ep_alpha"] >= 3.0  # never below the unlabelled frozen default

    def test_carrying_agent_session_does_not_suppress_the_word(self, sdk):
        """#6151 review P2 — an agent's declared word must NOT be dropped
        merely because its session CARRIES an attached source.

        A carrying session contributes ZERO (provenance, not evidence), so
        there is nothing to double-count; the word is the claim's only extra
        body and must count. Measured before the fix: person 5.1 vs
        agent-with-carrying-session 5.0 (the word silently suppressed).
        """
        sdk.create_source("https://carrydoc.example", "document", tier="T1",
                          sourceDate=FRESH)
        sdk.create_source("session:carrier", "agentSession")
        sdk._get_proj().g.query(
            "MATCH (a:Source {url:$a}),(b:Source {url:$b}) "
            "CREATE (a)-[:references]->(b)",
            params={"a": "session:carrier", "b": "https://carrydoc.example"},
        )
        person = sdk.create_point("statement", "person word + T1",
                                  status="live", credibility="gold",
                                  extractedFrom="https://carrydoc.example")["id"]
        agent = sdk.create_point("statement", "agent word + carried T1",
                                 status="live", credibility="gold",
                                 extractedFrom="https://carrydoc.example")["id"]
        sdk._get_proj()._link_source(agent, "session:carrier")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        pv, av = sdk.get_point(person), sdk.get_point(agent)
        assert pv["ep_alpha"] == pytest.approx(5.1), pv  # word + T1
        assert av["ep_alpha"] == pytest.approx(5.1), av
        assert av["ep_alpha"] == pytest.approx(pv["ep_alpha"]), (
            "a carrying session must not suppress the declared weak body")

    def test_mitigation_word_rises_when_a_real_source_lands(self, sdk):
        """#6151 review P2 — the mitigation CREATE path must persist the word
        LABEL, or the recompute guard skips the mitigation for ever.

        Before the fix `mitigate_operator` wrote the corrected value (3.1) but
        no `credibility` prop, so a real T1 source left `ep_alpha` at 3.1 — a
        self-declaration no refutation could move. After: the label + the
        recorded #2199 marker are persisted, and the T1 source raises it to
        1 + default(2.0) + word(0.1) + T1(4.0) = 7.1.
        """
        a = sdk.create_point("statement", "mit input", status="live")["id"]
        b = sdk.create_point("statement", "mit output", status="live")["id"]
        op = sdk.create_operator("IMPL", a, [b])["id"]
        mid = sdk.mitigate_operator(op, "reduces impact", strength=0.3,
                                    credibility="low")["id"]
        before = sdk.get_point(mid)
        assert before["ep_alpha"] == pytest.approx(
            3.0 + AUTHOR_ASSERTION_WEAK_PC), before
        assert before["credibility"] == "T3", before  # 'low' ⇒ T3, as a label
        assert before["baseline_system_default"] is True, before
        assert before["baseline_source"] == "set-by-author", before
        # Attach a REAL T1 source and run the recompute.
        sdk.create_source("https://mit-t1.example", "document", tier="T1",
                          sourceDate=FRESH)
        sdk._get_proj()._link_source(mid, "https://mit-t1.example")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        after = sdk.get_point(mid)
        assert after["ep_alpha"] == pytest.approx(7.1), after
        assert after["ep_alpha"] > before["ep_alpha"], (
            "a real source must displace the mitigation's weak authored body")

    def test_mitigation_idempotent_path_persists_label_and_marker(self, sdk):
        """#6151 review P2 — the idempotent (re-mitigation) path persists the
        same label + marker the create path does, so a re-mitigated word is
        also displaceable by a real source."""
        a = sdk.create_point("statement", "mit2 input", status="live")["id"]
        b = sdk.create_point("statement", "mit2 output", status="live")["id"]
        op = sdk.create_operator("IMPL", a, [b])["id"]
        first = sdk.mitigate_operator(op, "first", strength=0.3)  # no word
        second = sdk.mitigate_operator(op, "second", strength=0.4,
                                       credibility="high")
        assert second["id"] == first["id"], "still the idempotent path"
        mid = second["id"]
        pt = sdk.get_point(mid)
        assert pt["credibility"] == "T1", pt
        assert pt["baseline_system_default"] is True, pt
        assert pt["ep_alpha"] == pytest.approx(3.0 + AUTHOR_ASSERTION_WEAK_PC), pt
        sdk.create_source("https://mit2-t1.example", "document", tier="T1",
                          sourceDate=FRESH)
        sdk._get_proj()._link_source(mid, "https://mit2-t1.example")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        after = sdk.get_point(mid)
        assert after["ep_alpha"] == pytest.approx(7.1), after

    def test_carrying_a_neutral_source_is_not_originating(self, sdk):
        """#6151 review P3 — the `attached` set must include UNTIERED
        (neutral) sources, or a session that merely CARRIES one is misread as
        ORIGINATING and adds a weak authored body it should not.

        The scenario: a WORDLESS Point with an attached NEUTRAL source (a
        `document` with no `credibilityTier`; `resolve_tier` returns None for
        it) plus an `agentSession` that `references` that source. The session
        delivered the source, so it is provenance and contributes ZERO; the
        point therefore keeps no baseline at all. The pre-broadening `attached`
        query matched only TIERED sources, so the neutral source was invisible,
        `carries` was False, and the session was misread as ORIGINATING — the
        point wrongly acquired the weak body (1.1 instead of neutral). This is
        the falsifier the review asked for: reverting the query to tiered-only
        turns it red (probe recorded in the land report).
        """
        sdk.create_source("https://neutral-carried.example", "document")
        sdk.create_source("session:neutral-carrier", "agentSession")
        sdk._get_proj().g.query(
            "MATCH (a:Source {url:$a}),(b:Source {url:$b}) "
            "CREATE (a)-[:references]->(b)",
            params={"a": "session:neutral-carrier",
                    "b": "https://neutral-carried.example"},
        )
        p = sdk.create_point("statement", "wordless carried neutral",
                             extractedFrom="https://neutral-carried.example")["id"]
        sdk._get_proj()._link_source(p, "session:neutral-carrier")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        pt = sdk.get_point(p)
        # Carrying contributes NOTHING: the point is never recomputed and
        # stays neutral. A misread ORIGINATING session would write 1.1.
        assert pt.get("ep_alpha") in (None, 1.0), pt

    def test_dedup_word_on_an_inherited_baseline_is_added(self, sdk):
        """#6151 review P2 — a documented word must be ADDED (weak body +
        label) even when the existing baseline is DERIVED
        (``inherited-from-source``).

        The drop branch treated derived evidence like an explicit belief, so a
        dedup word produced no body and no label (measured: an inherited 5.0
        point + dedup word stayed 5.0 with ``credibility`` None). The word is
        one more weak body on top of the derived baseline; the next recompute
        re-derives it from the source PLUS the word.
        """
        sdk.create_source("https://p2-t1.example", "document", tier="T1",
                          sourceDate=FRESH)
        p = sdk.create_point("statement", "inherited then word",
                             status="live",
                             extractedFrom="https://p2-t1.example")["id"]
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        assert sdk.get_point(p)["ep_alpha"] == pytest.approx(5.0)
        dup = sdk.create_point("statement", "inherited then word",
                               dedup=True, credibility="gold")["id"]
        assert dup == p
        after = sdk.get_point(p)
        assert after["credibility"] == "T0", after  # the label IS written
        assert after["ep_alpha"] == pytest.approx(5.1), after  # + weak body
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        recomputed = sdk.get_point(p)
        assert recomputed["ep_alpha"] == pytest.approx(5.1), recomputed
        assert recomputed["credibility"] == "T0", recomputed

    def test_explicitly_tiered_agent_session_is_still_weak(self, sdk):
        """#6151 review P3-3 — an ``agentSession`` is WEAK for what it
        ORIGINATES, whatever tier a caller stamped on it.

        ``resolve_tier``'s documented explicit-tier precedence used to win, so
        a session with ``credibilityTier='T0'`` entered the tier groups as a
        normal source (measured: ``ep_alpha`` 10.0) — an agent's own assertion
        carrying a strong body, which the #398 ruling forbids. The belief path
        now routes ``agentSession`` to the carrier rule BEFORE tier resolution.
        """
        sdk.create_source("session:tiered-p33", "agentSession", tier="T0")
        p = sdk.create_point("statement", "tiered session", status="live",
                             extractedFrom="session:tiered-p33")["id"]
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        pt = sdk.get_point(p)
        assert pt["ep_alpha"] == pytest.approx(TIER_PRIORS["T4"][0])  # 1.1
        assert pt["ep_alpha"] == pytest.approx(1.0 + weak_support_pc(1))

    def test_explicitly_tiered_carrying_session_is_still_zero(self, sdk):
        """#6151 review P3-3 — a tiered ``agentSession`` that CARRIES an
        attached source is still provenance (ZERO), not a T0 body."""
        sdk.create_source("https://p33-doc.example", "document", tier="T1",
                          sourceDate=FRESH)
        sdk.create_source("session:p33-carrier", "agentSession", tier="T0")
        sdk._get_proj().g.query(
            "MATCH (a:Source {url:$a}),(b:Source {url:$b}) "
            "CREATE (a)-[:references]->(b)",
            params={"a": "session:p33-carrier", "b": "https://p33-doc.example"},
        )
        p = sdk.create_point("statement", "tiered carrier", status="live",
                             extractedFrom="https://p33-doc.example")["id"]
        sdk._get_proj()._link_source(p, "session:p33-carrier")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        # T1 document only — the tiered carrying session adds nothing.
        assert sdk.get_point(p)["ep_alpha"] == pytest.approx(TIER_PRIORS["T1"][0])


class TestExplicitBaselineSupersedesTheWord:
    """#6151 review P1 — an explicit `set_point_baseline` SUPERSEDES the word.

    The `credibility` label is the recompute guard's ONLY exception: it keeps a
    word-bearing `set-by-author` baseline recomputable so a real source can
    displace the weak authored body. While the label stayed in place an
    EXPLICIT `set_point_baseline(...)` on such a point was still treated as an
    authored assertion and silently replaced. Measured before the fix:
    `set_point_baseline(0.4)` then a recompute -> 5.1 with a T1 source attached
    and 1.1 with an originating session, with `baseline_source` flipped to
    `inherited-from-source`. An explicit baseline is not an authored assertion,
    so the write must clear the label and let the #844 freeze apply.
    """

    @pytest.mark.parametrize("carrier", ["t1-source", "originating-session"])
    def test_explicit_baseline_survives_carrier(self, sdk, carrier):
        if carrier == "t1-source":
            sdk.create_source("https://p1doc.example", "document", tier="T1",
                              sourceDate=FRESH)
            attached = "https://p1doc.example"
        else:
            sdk.create_source("session:p1-origin", "agentSession")
            attached = "session:p1-origin"
        # Born with an authored word: label + one weak body.
        p = sdk.create_point("statement", f"word then explicit ({carrier})",
                             status="live", credibility="gold",
                             extractedFrom=attached)["id"]
        assert sdk.get_point(p)["credibility"] == "T0"
        # The author's explicit belief — supersedes the word.
        sdk.set_point_baseline(p, 0.4, 1.0, source="set-by-author")
        after_write = sdk.get_point(p)
        assert after_write["ep_alpha"] == pytest.approx(0.4), after_write
        assert after_write["baseline_source"] == "set-by-author", after_write
        assert after_write.get("credibility") is None, after_write
        # Any attached evidence (real source or originating session) must not
        # re-open the explicit baseline.
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        after = sdk.get_point(p)
        assert after["ep_alpha"] == pytest.approx(0.4), after
        assert after["ep_beta"] == pytest.approx(1.0), after
        assert after["baseline_source"] == "set-by-author", after
        assert after.get("credibility") is None, after

    def test_ruled_word_path_still_displaced_by_a_real_source(self, sdk):
        """The fix must not break the ruled word path: a written word is still
        ONE weak body + label (0.5238), and a real T1 source still displaces
        it (5.1 -> 0.8361).
        """
        sdk.create_source("https://wordpath.example", "document", tier="T1",
                          sourceDate=FRESH)
        p = sdk.create_point("statement", "word keeps its body",
                             status="live", credibility="gold",
                             extractedFrom="https://wordpath.example")["id"]
        born = sdk.get_point(p)
        assert born["ep_alpha"] == pytest.approx(TIER_PRIORS["T4"][0])  # 1.1
        assert born["credibility"] == "T0"
        assert born["baseline_source"] == "set-by-author"
        assert mean_from_beta(born["ep_alpha"], born["ep_beta"]) == \
            pytest.approx(0.5238, abs=1e-4)
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        after = sdk.get_point(p)
        assert after["ep_alpha"] == pytest.approx(5.1), after
        assert mean_from_beta(after["ep_alpha"], after["ep_beta"]) == \
            pytest.approx(0.8361, abs=1e-4)
        assert after["credibility"] == "T0", after  # label survives the recompute

    def test_explicit_baseline_clears_the_2199_marker(self, sdk):
        """#6151 review P1(a) — the explicit write must clear the #2199
        ``baseline_system_default`` marker WITH the label, or a later
        word-bearing recompute re-injects +2.0 OVER the explicit baseline.

        Chain measured before the fix: a born-live decide part + word → 3.1 with
        the marker; ``set_point_baseline(0.4)`` → 0.4 but the marker SURVIVED;
        a dedup word re-opened the label; the recompute returned 7.1. After the
        fix the marker is cleared in the same statement, the dedup word is
        refused by the #844 explicit-baseline rule, and 0.4 survives.
        """
        p = sdk.create_point("option", "p1a decide word",
                             credibility="gold")["id"]
        assert sdk.get_point(p)["baseline_system_default"] is True
        sdk.set_point_baseline(p, 0.4, 1.0)
        after_write = sdk.get_point(p)
        assert after_write["ep_alpha"] == pytest.approx(0.4), after_write
        assert after_write.get("credibility") is None, after_write
        assert after_write.get("baseline_system_default") is None, after_write
        # The word-bearing recompute: a dedup hit with a word, then a real T1
        # source. Before the fix this re-injected the default (7.1).
        dup = sdk.create_point("option", "p1a decide word", dedup=True,
                               credibility="low")["id"]
        assert dup == p
        sdk.create_source("https://p1a.example", "document", tier="T1",
                          sourceDate=FRESH)
        sdk._get_proj()._link_source(p, "https://p1a.example")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        after = sdk.get_point(p)
        assert after["ep_alpha"] == pytest.approx(0.4), after
        assert after.get("baseline_system_default") is None, after
        assert after.get("credibility") is None, after


class TestWordPointRebaselinedAfterRebuild:
    """#6151 review P2(b) — a word-bearing Point that LOST its baseline (the
    replay outcome) must be RE-BASELINED by the dedup retry, not silently
    no-op'd.

    `credibility` is a DECLARED prop and replays; `ep_alpha`/`baseline_set`/
    `baseline_source` are in `_POINT_DENY` and do not. Keying first-writer-wins
    on the label alone therefore turned the routine same-word retry (dedup is
    the MCP/self-host DEFAULT) into a complete no-op on the rebuilt point: no
    body, no `_drop_note`, the point uncalibrated forever. Before the fix these
    tests fail on the healed state (ep_alpha stays None / CalibrationError),
    and the decide part additionally loses the #2199 marker because
    `set_point_baseline` clears it and the re-stamp must re-assert it.
    """

    def test_same_word_retry_rebaselines_a_rebuilt_point(self, sdk):
        p = sdk.create_point("statement", "rebuilt word point",
                             status="live", credibility="gold")["id"]
        assert sdk.get_point(p)["ep_alpha"] == pytest.approx(1.1)
        _simulate_replay_outcome(sdk, p)
        rebuilt = sdk.get_point(p)
        assert rebuilt.get("credibility") == "T0"   # declared — replays
        assert rebuilt.get("baseline_set") is None   # denied — does not
        with pytest.raises(CalibrationError):
            sdk.compute_confidence(require_calibration=True)
        # The retry with the SAME word heals the point (pre-fix: a no-op).
        dup = sdk.create_point("statement", "rebuilt word point", dedup=True,
                               credibility="gold")["id"]
        assert dup == p
        healed = sdk.get_point(p)
        assert healed["ep_alpha"] == pytest.approx(1.1), healed
        assert healed["baseline_set"] is True, healed
        assert healed["baseline_source"] == "set-by-author", healed
        assert healed["credibility"] == "T0", healed
        summary = sdk.calibrate_summary()
        assert next(i for i in summary if i["id"] == p)["calibrated"] is True
        sdk.compute_confidence(require_calibration=True)  # no longer raises

    def test_same_word_retry_keeps_the_2199_provision_of_a_rebuilt_part(self, sdk):
        p = sdk.create_point("option", "rebuilt decide word",
                             credibility="gold")["id"]
        born = sdk.get_point(p)
        assert born["ep_alpha"] == pytest.approx(3.1), born
        assert born["baseline_system_default"] is True, born
        _simulate_replay_outcome(sdk, p)
        rebuilt = sdk.get_point(p)
        assert rebuilt.get("baseline_set") is None, rebuilt
        # The marker is a BIRTH fact in the original PointAdded snapshot, so it
        # replays even though the baseline does not. `set_point_baseline` on
        # the healing write CLEARS it, so the re-stamp must put it back —
        # without `_record_default` the recorded #2199 provision is LOST and a
        # later recompute can never re-inject the +2.0.
        assert rebuilt.get("baseline_system_default") is True, rebuilt
        dup = sdk.create_point("option", "rebuilt decide word", dedup=True,
                               credibility="gold")["id"]
        assert dup == p
        healed = sdk.get_point(p)
        assert healed["ep_alpha"] == pytest.approx(1.1), healed  # weak body
        assert healed["baseline_system_default"] is True, healed
        assert healed["credibility"] == "T0", healed
        # With a real source the word plus the re-asserted provision recompute
        # above the neutral weak body (the +2.0 is re-injected).
        sdk.create_source("https://rebuilt.example", "document", tier="T1",
                          sourceDate=FRESH)
        sdk._get_proj()._link_source(p, "https://rebuilt.example")
        sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
        after = sdk.get_point(p)
        assert after["baseline_system_default"] is True, after
        assert after["ep_alpha"] > 3.0, after  # provision + weak body + source


class TestNestedWordNeverLeaksIntoTheDedupUpdate:
    def test_both_word_forms_on_a_dedup_hit_do_not_raise(self, sdk):
        """#6151 review P3-1: the explicit kwarg wins, and the nested
        `props={"credibility": …}` must be popped even when the kwarg is set.
        Otherwise the dedup path forwards the residual key to `update_point`,
        which HARD-REJECTS `credibility` from a generic bag.
        """
        p = sdk.create_point("statement", "both word forms")["id"]
        dup = sdk.create_point("statement", "both word forms", dedup=True,
                               credibility="gold",
                               props={"credibility": "low"})
        assert dup["id"] == p
        # The explicit kwarg wins over the nested bag (as documented).
        assert sdk.get_point(p)["credibility"] == "T0"


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
