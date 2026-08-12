"""OWN real-path integration tests for source credibility inheritance (issue #398).

Exercises the FULL graph path: create_source/_link_source → extractedFrom →
_apply_source_inheritance → EP. Embedded (TortoiseSDK(db_path)) — no Docker.

Determinism rules (plan Task 3): distinct URLs per source (MERGE on url); per-call
recency_decay (never env default); fixed-epoch dates with runtime-clock expected
values; aggregation math asserted via ep_alpha/ep_beta through get_point; EP
confidences use loose inequalities (>= 0.02 abs margins, #341 EPSILON).
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.source_credibility import TIER_PRIORS, register_source_kind_default

FRESH = "2024-01-01T00:00:00+00:00"


@contextmanager
def fresh_sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tt_own_"), "test.db")
    sdk = TortoiseSDK(db_path)
    try:
        yield sdk
    finally:
        try:
            sdk.close()
        except Exception:
            pass


def tier_source(sdk, url: str, tier: str, source_date: str = FRESH) -> str:
    """Create a Point extracted from a tiered Source. Returns point id."""
    p = sdk.create_point("statement", f"claim from {url}", extractedFrom=url)
    sdk._get_proj().g.query(
        "MATCH (s:Source {url:$url}) SET s.credibilityTier = $t, s.sourceDate = $sd, "
        "s.ingestedAt = $sd",
        params={"url": url, "t": tier, "sd": source_date},
    )
    return p["id"]


def inherited_alpha(sdk, pid) -> float | None:
    return sdk.get_point(pid).get("ep_alpha")


class TestCorroboration:
    def test_2xT4_greater_than_1xT4(self):
        """Corroboration activates — 2 T4 sources > 1 T4 (fails under highest-tier-wins)."""
        with fresh_sdk() as sdk:
            p1 = tier_source(sdk, "https://a.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha_1 = inherited_alpha(sdk, p1)
        with fresh_sdk() as sdk:
            p = sdk.create_point("statement", "two T4 sources", extractedFrom="https://a.example")
            sdk._get_proj()._link_source(p["id"], "https://b.example")
            for u in ("https://a.example", "https://b.example"):
                sdk._get_proj().g.query(
                    "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T4', s.ingestedAt = $ts",
                    params={"url": u, "ts": FRESH},
                )
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha_2 = inherited_alpha(sdk, p["id"])
        assert alpha_2 > alpha_1
        # Exact: pc = 0.1 * log2(3) = 0.1585 → alpha 1.1585
        assert alpha_2 == pytest.approx(1.0 + 0.1 * __import__("math").log2(3), rel=1e-9)


class TestAntiSybil:
    def test_100_t4_below_1_t2(self):
        with fresh_sdk() as sdk:
            urls = [f"https://s{i}.example" for i in range(100)]
            pids = [tier_source(sdk, u, "T4") for u in urls]
            sdk._apply_source_inheritance(recency_decay=1.0)
            pc_100t4 = inherited_alpha(sdk, pids[0]) - 1.0
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://t2.example", "T2")
            sdk._apply_source_inheritance(recency_decay=1.0)
            pc_1t2 = inherited_alpha(sdk, pid) - 1.0
        assert pc_100t4 < pc_1t2

    def test_1000_t4_approx_1_t3(self):
        with fresh_sdk() as sdk:
            p = sdk.create_point("statement", "1000 T4 sources", extractedFrom="https://s0.example")
            for i in range(1, 1000):
                sdk._get_proj()._link_source(p["id"], f"https://s{i}.example")
            for i in range(1000):
                sdk._get_proj().g.query(
                    "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T4', s.ingestedAt = $ts",
                    params={"url": f"https://s{i}.example", "ts": FRESH},
                )
            sdk._apply_source_inheritance(recency_decay=1.0)
            pc_1000t4 = inherited_alpha(sdk, p["id"]) - 1.0
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://t3.example", "T3")
            sdk._apply_source_inheritance(recency_decay=1.0)
            pc_1t3 = inherited_alpha(sdk, pid) - 1.0
        assert pc_1000t4 < pc_1t3
        assert abs(pc_1000t4 - pc_1t3) < 0.05


class TestTierOrdering:
    def test_monotonic_single_source_tiers(self):
        """T4 < T3 < T2 < T1 < T0 with the exact validated priors."""
        confs = {}
        for tier in ["T4", "T3", "T2", "T1", "T0"]:
            with fresh_sdk() as sdk:
                pid = tier_source(sdk, f"https://{tier}.example", tier)
                sdk._apply_source_inheritance(recency_decay=1.0)
                alpha, beta = TIER_PRIORS[tier]
                confs[tier] = inherited_alpha(sdk, pid)
                assert confs[tier] == alpha
                assert sdk.get_point(pid).get("ep_beta") == beta
        ordered = ["T4", "T3", "T2", "T1", "T0"]
        for i in range(len(ordered) - 1):
            assert confs[ordered[i]] < confs[ordered[i + 1]]

    def test_t0_t4_gap_large(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://t0.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            a_t0 = inherited_alpha(sdk, pid)
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://t4.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            a_t4 = inherited_alpha(sdk, pid)
        mean_t0 = a_t0 / (a_t0 + 1.0)
        mean_t4 = a_t4 / (a_t4 + 1.0)
        assert mean_t0 - mean_t4 > 0.30


class TestTemporal:
    def test_ancient_same_tier_addition_does_not_decrease(self):
        """Tier-most-recent decay: adding an ancient same-tier source cannot lower pc."""
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://fresh.example", "T1")
            sdk._apply_source_inheritance(recency_decay=0.95)
            fresh_alpha = inherited_alpha(sdk, pid)
        with fresh_sdk() as sdk:
            p = sdk.create_point("statement", "fresh + ancient", extractedFrom="https://fresh.example")
            sdk._get_proj()._link_source(p["id"], "https://ancient.example")
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T1', "
                "s.sourceDate = $sd, s.ingestedAt = $sd",
                params={"url": "https://fresh.example", "sd": FRESH},
            )
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T1', "
                "s.sourceDate = $sd, s.ingestedAt = $sd",
                params={"url": "https://ancient.example", "sd": "1984-01-01T00:00:00+00:00"},
            )
            sdk._apply_source_inheritance(recency_decay=0.95)
            mixed_alpha = inherited_alpha(sdk, p["id"])
        # Most-recent (2024) drives decay → mixed > fresh (log2 growth dominates)
        assert mixed_alpha > fresh_alpha + 0.1

    def test_malformed_source_date_safe_no_decay(self):
        with fresh_sdk() as sdk:
            p = sdk.create_point("statement", "bad date", extractedFrom="https://bad.example")
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T1', s.sourceDate = 'garbage'",
                params={"url": "https://bad.example"},
            )
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, p["id"]) == 5.0  # malformed date → no crash


class TestLegacyActivation:
    def test_registry_default_activates_inheritance(self):
        """register_source_kind_default is the activation path for legacy kinds."""
        register_source_kind_default("github_issue", "T2")
        try:
            with fresh_sdk() as sdk:
                p = sdk.create_point("statement", "from github", extractedFrom="https://github.example")
                # Source has sourceKind='document' (default) — set it to github_issue
                sdk._get_proj().g.query(
                    "MATCH (s:Source {url:$url}) SET s.sourceKind = 'github_issue', s.ingestedAt = $ts",
                    params={"url": "https://github.example", "ts": FRESH},
                )
                sdk._apply_source_inheritance(recency_decay=1.0)
                # T2 via registry → Beta(3,1)
                assert inherited_alpha(sdk, p["id"]) == 3.0
        finally:
            from tortoise.source_credibility import SOURCE_KIND_DEFAULTS
            SOURCE_KIND_DEFAULTS.pop("github_issue", None)

    def test_unregistered_legacy_kind_stays_neutral(self):
        with fresh_sdk() as sdk:
            p = sdk.create_point("statement", "neutral", extractedFrom="https://neutral.example")
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.sourceKind = 'slack_message', s.ingestedAt = $ts",
                params={"url": "https://neutral.example", "ts": FRESH},
            )
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, p["id"]) in (None, 1.0)

    def test_malformed_tier_excluded_from_counts(self):
        with fresh_sdk() as sdk:
            p = sdk.create_point("statement", "multi-source", extractedFrom="https://t4.example")
            # Link a second source with a malformed tier to the SAME point
            sdk._get_proj()._link_source(p["id"], "https://t9.example")
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T4', s.ingestedAt = $ts",
                params={"url": "https://t4.example", "ts": FRESH},
            )
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T9', s.ingestedAt = $ts",
                params={"url": "https://t9.example", "ts": FRESH},
            )
            sdk._apply_source_inheritance(recency_decay=1.0)
            # Only the T4 source counts: pc = 0.1 (T9 excluded → N=1 for T4)
            assert inherited_alpha(sdk, p["id"]) == pytest.approx(1.1, rel=1e-9)


class TestNANDRealPath:
    def test_t0_operand_stronger_than_t4(self):
        """Operators from reliable sources weighted higher (issue target)."""
        def build(tier):
            with fresh_sdk() as sdk:
                a = tier_source(sdk, f"https://{tier}.example", tier)
                # #992: target must be live — draft inputs are stripped by the EP
                # draft filter (create_point defaults to draft since #943), making
                # the NAND operator degenerate and silently excluded from EP.
                b = sdk.create_point("statement", "contradiction target", status="live")
                op = sdk.create_operator("NAND", a, [b["id"]])
                sdk._apply_source_inheritance(recency_decay=1.0)
                result = sdk.compute_confidence()
                conf_a = result["confidences"][a]["mean"]
                return conf_a, sdk.get_point(a).get("ep_alpha")

        conf_t0, alpha_t0 = build("T0")
        conf_t4, alpha_t4 = build("T4")
        # The T0-sourced operand carries a materially stronger prior
        assert alpha_t0 > alpha_t4
        # And its EP confidence reflects it (loose margin — EP factor domain)
        assert conf_t0 > conf_t4 + 0.02


class TestAssessmentExclusion:
    def test_assessment_points_excluded_from_inheritance(self):
        with fresh_sdk() as sdk:
            url = "https://assessed.example"
            p = sdk.create_point("statement", "real claim", extractedFrom=url)
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T1', s.ingestedAt = $ts",
                params={"url": url, "ts": FRESH},
            )
            # An assessment point referencing the same source (no extractedFrom)
            sdk.create_point("assessment", "assessment of source",
                             props={"targetSource": url, "assessor": "alice", "score": 0.2})
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, p["id"]) == 5.0  # T1 exact — no assessment interference


class TestReliabilityAPI:
    def test_untiered_unassessed_null(self):
        with fresh_sdk() as sdk:
            url = "https://untiered.example"
            sdk.create_point("statement", "untiered claim", extractedFrom=url)
            r = sdk.get_source_reliability(url)
            assert r["reliability"] is None
            assert r["components"]["reason"] == "untiered"

    def test_tiered_reliability_matches_prior_mean(self):
        from datetime import datetime, timezone
        with fresh_sdk() as sdk:
            url = "https://tiered.example"
            sdk.create_point("statement", "tiered claim", extractedFrom=url)
            now = datetime.now(timezone.utc).isoformat()
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T1', s.sourceDate = $ts, "
                "s.ingestedAt = $ts",
                params={"url": url, "ts": now},
            )
            r = sdk.get_source_reliability(url)
            assert r["reliability"] == pytest.approx(5.0 / 6.0, rel=5e-4)  # mean Beta(5,1) @ decay~1
            assert r["components"]["tier"] == "T1"
            assert r["cache"] == "recomputed"
            # Second call serves the fresh cache (float round-trip → approx)
            r2 = sdk.get_source_reliability(url)
            assert r2["cache"] == "fresh"
            assert r2["reliability"] == pytest.approx(r["reliability"], rel=1e-6)

    def test_cache_consistency_checked_on_raw_write(self):
        """Raw graph writes bypassing the SDK cannot leave the cache stale forever."""
        with fresh_sdk() as sdk:
            url = "https://raw.example"
            sdk.create_point("statement", "raw claim", extractedFrom=url)
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T2', s.sourceDate = $ts, "
                "s.ingestedAt = $ts",
                params={"url": url, "ts": FRESH},
            )
            r1 = sdk.get_source_reliability(url)
            assert r1["components"]["tier"] == "T2"
            # Raw tier change (bypasses SDK) — old cache is stale on inputs
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T0'",
                params={"url": url},
            )
            r2 = sdk.get_source_reliability(url)
            assert r2["components"]["tier"] == "T0"
            assert r2["reliability"] == pytest.approx(10.0 / 11.0, rel=5e-4)

    def test_single_source_consistency_invariant(self):
        """reliability.mean == the inherited prior mean EP applied (single source)."""
        with fresh_sdk() as sdk:
            url = "https://invariant.example"
            p = sdk.create_point("statement", "invariant claim", extractedFrom=url)
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T3', s.sourceDate = $ts, "
                "s.ingestedAt = $ts",
                params={"url": url, "ts": FRESH},
            )
            rel = sdk.get_source_reliability(url)["reliability"]
            sdk._apply_source_inheritance(recency_decay=0.95)
            prior = sdk._compute_source_prior(url)
            ep_mean = prior["alpha"] / (prior["alpha"] + prior["beta"])
            assert rel == pytest.approx(ep_mean, rel=5e-4)
            # And the point's inherited prior matches the source reliability
            alpha = sdk.get_point(p["id"]).get("ep_alpha")
            assert alpha is not None
            assert alpha / (alpha + 1.0) == pytest.approx(rel, rel=5e-4)

    def test_untiered_with_assessment_points_display_only(self):
        """Untiered + assessed → assessment-only reliability (never feeds EP)."""
        with fresh_sdk() as sdk:
            url = "https://assessed-untiered.example"
            sdk.create_point("statement", "claim", extractedFrom=url)
            sdk.create_point("assessment", "rating",
                             props={"targetSource": url, "assessor": "alice",
                                    "score": 0.9, "assessorReputation": 1.0})
            sdk.create_point("assessment", "rating2",
                             props={"targetSource": url, "assessor": "bob",
                                    "score": 0.5, "assessorReputation": 0.5})
            r = sdk.get_source_reliability(url)
            assert r["reliability"] is not None
            assert r["components"]["reason"] == "untiered; assessment-only"
            # EP unaffected: untiered sources never feed EP — point stays neutral
            sdk._apply_source_inheritance(recency_decay=1.0)
            from tortoise.sdk import TortoiseSDK as _S
            pts = sdk._get_proj().g.query(
                "MATCH (p:Point) WHERE p.pointKind = 'statement' RETURN p.id"
            ).result_set
            for (pid,) in pts:
                pt = sdk.get_point(pid)
                assert pt.get("ep_alpha") in (None, 1.0)


def _give_reputation(sdk, agent: str) -> None:
    """Give an agent a strong track record (rep > 0.5) via event outcomes."""
    sdk._get_proj().g.query(
        "CREATE (s:Subject {id:$aid, name:$aid, subjectKind:'naturalPerson'}) "
        "CREATE (e:Event {eventId: $eid, eventKind:'review'}) "
        "CREATE (s)-[:performs]->(e) RETURN e.eventId",
        params={"aid": agent, "eid": f"e_{agent}"},
    ).result_set
    p_claim = sdk.create_point("statement", f"{agent}'s correct claim")
    sdk._get_proj().g.query(
        "MATCH (e:Event {eventId:$eid}), (n:Point {id:$id}) CREATE (e)-[:IMPL]->(n)",
        params={"eid": f"e_{agent}", "id": p_claim["id"]},
    ).result_set
    assert sdk.compute_reputation(agent)["mean"] > 0.5

class TestAssessSource:
    def test_creates_assessment_with_correct_props(self):
        with fresh_sdk() as sdk:
            url = "https://assess1.example"
            sdk.create_point("statement", "claim", extractedFrom=url)
            r = sdk.assess_source(url, "alice", 0.8, "well-sourced and verified")
            row = sdk._get_proj().g.query(
                "MATCH (p:Point {id:$id}) RETURN p.pointKind, p.targetSource, "
                "p.assessor, p.score, p.assessorReputation",
                params={"id": r["assessment_point_id"]},
            ).result_set
            kind, tsrc, assessor, score, rep = row[0]
            assert kind == "assessment"
            assert tsrc == url
            assert assessor == "alice"
            assert score == 0.8
            assert rep == 0.5  # no track record → neutral reputation snapshot

    def test_score_validation(self):
        with fresh_sdk() as sdk:
            url = "https://validate.example"
            sdk.create_point("statement", "claim", extractedFrom=url)
            with pytest.raises(ValueError):
                sdk.assess_source(url, "alice", 1.5, "too high")
            with pytest.raises(ValueError):
                sdk.assess_source(url, "alice", -0.1, "negative")
            with pytest.raises(ValueError):
                sdk.assess_source(url, "alice", "not-a-number", "bad type")
            with pytest.raises(ValueError):
                sdk.assess_source(url, "alice", 0.5, "  ")
            with pytest.raises(ValueError):
                sdk.assess_source(url, "", 0.5, "no assessor")

    def test_latest_wins_per_assessor(self):
        with fresh_sdk() as sdk:
            _give_reputation(sdk, "alice")  # rep ~0.67 → snapshot > 0.5
            url = "https://latest.example"
            p = sdk.create_point("statement", "claim", extractedFrom=url)
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T2', s.sourceDate = $ts, "
                "s.ingestedAt = $ts",
                params={"url": url, "ts": FRESH},
            )
            sdk.assess_source(url, "alice", 0.1, "first assessment")
            sdk.assess_source(url, "alice", 0.9, "revised assessment")
            # Latest-wins: only 0.9 counts → factor = 1 + (rep-0.5)*0.4 > 1
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
            alpha = sdk.get_point(p["id"]).get("ep_alpha")
            # rep 0.667: factor = 1 + 0.167*0.4 = 1.0667 → pc = 2.0*1.0667 = 2.133 → alpha 3.133
            # If BOTH counted (double-count bug): factor ~1.0 → alpha 3.0. Assert > 3.0 proves latest-wins.
            assert alpha > 3.0
            # The older assessment is marked outdated
            rows = sdk._get_proj().g.query(
                "MATCH (p:Point {pointKind:'assessment'}) WHERE p.targetSource = $url "
                "RETURN p.score, coalesce(p.outdated, false)",
                params={"url": url},
            ).result_set
            scores = {float(r[0]): bool(r[1]) for r in rows}
            assert scores == {0.1: True, 0.9: False}

    def test_reputation_weighting(self):
        """High-reputation assessor shifts reliability more than neutral at equal score."""
        with fresh_sdk() as sdk:
            url = "https://reputation.example"
            sdk.create_point("statement", "claim", extractedFrom=url)
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T2', s.sourceDate = $ts, "
                "s.ingestedAt = $ts",
                params={"url": url, "ts": FRESH},
            )
            # Neutral assessor (no track record): rep 0.5 → term 0 → factor 1.0
            sdk.assess_source(url, "neutral-agent", 0.1, "meh")
            rel_neutral = sdk.get_source_reliability(url)["reliability"]
            # Expert assessor with a strong track record FIRST (snapshot at write)
            _give_reputation(sdk, "expert-agent")
            sdk.assess_source(url, "expert-agent", 0.1, "unreliable")
            rel_expert = sdk.get_source_reliability(url)["reliability"]
            assert rel_expert < rel_neutral  # expert's low score carries more weight

    def test_reputation_snapshot_invariant(self):
        """Reputation changes after write never rewrite the assessment factor."""
        with fresh_sdk() as sdk:
            url = "https://snapshot.example"
            sdk.create_point("statement", "claim", extractedFrom=url)
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T2', s.sourceDate = $ts, "
                "s.ingestedAt = $ts",
                params={"url": url, "ts": FRESH},
            )
            # Assess with a zero-track-record assessor (rep snapshot 0.5)
            sdk.assess_source(url, "growing-agent", 0.1, "assessment before reputation")
            r_before = sdk.get_source_reliability(url)
            # Raise the assessor's reputation
            sdk._get_proj().g.query(
                "CREATE (s:Subject {id:'growing-agent', name:'growing-agent', "
                "subjectKind:'naturalPerson'}) "
                "CREATE (e:Event {eventId:'e2', eventKind:'review'}) "
                "CREATE (s)-[:performs]->(e) RETURN e.eventId"
            ).result_set
            p_claim = sdk.create_point("statement", "growing agent's claim")
            sdk._get_proj().g.query(
                "MATCH (e:Event {eventId:'e2'}), (n:Point {id:$id}) CREATE (e)-[:IMPL]->(n)",
                params={"id": p_claim["id"]},
            ).result_set
            assert sdk.compute_reputation("growing-agent")["mean"] > 0.5
            # Snapshot: factor unchanged despite reputation growth
            r_after = sdk.get_source_reliability(url)
            assert r_after["reliability"] == pytest.approx(r_before["reliability"], rel=1e-6)

    def test_assessment_invalidates_cache_and_gate(self):
        """assess_source refreshes reliability + dirty-marks the inheritance gate."""
        with fresh_sdk() as sdk:
            url = "https://invalidate.example"
            p = sdk.create_point("statement", "claim", extractedFrom=url)
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T2', s.sourceDate = $ts, "
                "s.ingestedAt = $ts",
                params={"url": url, "ts": FRESH},
            )
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
            alpha_before = sdk.get_point(p["id"]).get("ep_alpha")  # 3.0 (T2, no factor)
            _give_reputation(sdk, "alice")  # rep > 0.5 → snapshot at write
            sdk.assess_source(url, "alice", 0.0, "downweight")
            # rep ~0.667: factor = 1 + 0.167*(-0.5) = 0.9167 → pc = 2.0*0.9167 = 1.833
            # → alpha 2.833 < 3.0. Gate dirty-marked → recomputes NOW (interval 3600).
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
            alpha_after = sdk.get_point(p["id"]).get("ep_alpha")
            assert alpha_after < alpha_before
            # rep 0.667 → factor = 1 + (0.667-0.5)*(-0.5) = 0.9167 → pc = 2*0.9167 = 1.833
            assert alpha_after == pytest.approx(1.0 + 2.0 * 0.9167, rel=1e-3)


class TestSourceTierAPI:
    def test_create_source_with_tier_dual_write(self):
        with fresh_sdk() as sdk:
            r = sdk.create_source("https://kind.example", "github_issue", tier="T2")
            rows = sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) RETURN s.sourceKind, s.credibilityTier",
                params={"url": "https://kind.example"},
            ).result_set
            skind, ctier = rows[0]
            assert skind == "github_issue"  # type string preserved
            assert ctier == "T2"  # tier on credibilityTier

    def test_tier_form_sourcekind_mirrors(self):
        with fresh_sdk() as sdk:
            sdk.create_source("https://tierform.example", "T0")
            rows = sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) RETURN s.sourceKind, s.credibilityTier",
                params={"url": "https://tierform.example"},
            ).result_set
            assert rows[0] == ["T0", "T0"]  # canonical dual-write

    def test_url_collision_preserves_existing_sourcekind(self):
        """A Source auto-created by _link_source keeps its sourceKind; tier lands on credibilityTier."""
        with fresh_sdk() as sdk:
            p = sdk.create_point("statement", "from link", extractedFrom="https://collide.example")
            # Auto-created Source has sourceKind='document'
            sdk.create_source("https://collide.example", "T0", tier="T0")
            rows = sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) RETURN s.sourceKind, s.credibilityTier",
                params={"url": "https://collide.example"},
            ).result_set
            skind, ctier = rows[0]
            assert skind == "document"  # preserved — never overwritten
            assert ctier == "T0"

    def test_set_source_tier_never_touches_type_strings(self):
        with fresh_sdk() as sdk:
            sdk.create_source("https://type.example", "slack_message")
            sdk.set_source_tier("https://type.example", "T3")
            rows = sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) RETURN s.sourceKind, s.credibilityTier",
                params={"url": "https://type.example"},
            ).result_set
            assert rows[0] == ["slack_message", "T3"]

    def test_set_source_tier_invalidates_cache_and_gate(self):
        """set_source_tier refreshes reliability + dirty-marks the gate (deferred Task 4/6 tests)."""
        from datetime import datetime, timezone
        with fresh_sdk() as sdk:
            url = "https://retier.example"
            p = sdk.create_point("statement", "claim", extractedFrom=url)
            now = datetime.now(timezone.utc).isoformat()
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T2', s.sourceDate = $ts, "
                "s.ingestedAt = $ts",
                params={"url": url, "ts": now},
            )
            r1 = sdk.get_source_reliability(url)
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
            alpha_before = sdk.get_point(p["id"]).get("ep_alpha")  # 3.0
            # Re-tier to T0 via the sanctioned writer
            sdk.set_source_tier(url, "T0")
            r2 = sdk.get_source_reliability(url)
            assert r2["components"]["tier"] == "T0"  # cache invalidated → recomputed
            # Gate dirty-marked → recomputes within the interval
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
            alpha_after = sdk.get_point(p["id"]).get("ep_alpha")
            assert alpha_after == 10.0  # T0, no decay
            assert alpha_after != alpha_before

    def test_calibrate_summary_surfaces_untiered_with_actionable_suggestion(self):
        with fresh_sdk() as sdk:
            url = "https://untiered2.example"
            p = sdk.create_point("statement", "claim", extractedFrom=url)
            summary = sdk.calibrate_summary()
            item = next(i for i in summary if i["id"] == p["id"])
            assert item["calibrated"] is False
            assert "set_source_tier" in item["suggestion"]
            assert "untiered" in item["suggestion"]

    def test_calibrate_summary_registry_tiered_not_flagged(self):
        """A registry-tiered source is effectively tiered — not surfaced as untiered."""
        from tortoise.source_credibility import SOURCE_KIND_DEFAULTS
        SOURCE_KIND_DEFAULTS["substack_reg"] = "T2"
        try:
            with fresh_sdk() as sdk:
                url = "https://registry-tiered.example"
                p = sdk.create_point("statement", "claim", extractedFrom=url)
                sdk._get_proj().g.query(
                    "MATCH (s:Source {url:$url}) SET s.sourceKind = 'substack_reg', "
                    "s.ingestedAt = $ts",
                    params={"url": url, "ts": FRESH},
                )
                summary = sdk.calibrate_summary()
                item = next(i for i in summary if i["id"] == p["id"])
                # Effectively tiered (registry) → suggestion mentions inheritance, not untiered
                assert "untiered" not in item.get("suggestion", "")
        finally:
            SOURCE_KIND_DEFAULTS.pop("substack_reg", None)

    def test_list_sources_after_dual_write(self):
        with fresh_sdk() as sdk:
            sdk.create_source("https://list.example", "T1", tier="T1")
            sdk.create_source("https://list2.example", "github_issue", tier="T2")
            sources = sdk.list_sources()
            by_url = {s["url"]: s for s in sources}
            assert by_url["https://list.example"]["sourceKind"] == "T1"
            assert by_url["https://list2.example"]["sourceKind"] == "github_issue"


class TestMCPRegistration:
    def test_new_tools_registered_and_http_allowed(self):
        """New MCP tools are registered and present in the HTTP allowlist."""
        import asyncio
        from tortoise.mcp_server import mcp
        from tortoise.mcp_auth import HTTP_ALLOWED

        async def _names():
            tools = await mcp._list_tools()
            return {t.name for t in tools}

        tool_names = asyncio.run(_names())
        for name in ("tortoise_get_source_reliability", "tortoise_assess_source",
                     "tortoise_set_source_tier"):
            assert name in tool_names, f"{name} not registered"
            assert name in HTTP_ALLOWED, f"{name} missing from HTTP_ALLOWED"


# ═══════════════════════════════════════════════════════════════════════
# Test-review gate additions (P1/P2/P3 fixes from review)
# ═══════════════════════════════════════════════════════════════════════

class TestReviewGateFixes:
    def test_explicit_baseline_never_clobbered_real_path(self):
        """set_point_baseline(source='explicit') + strong source → untouched."""
        with fresh_sdk() as sdk:
            url = "https://explicit2.example"
            p = sdk.create_point("statement", "explicit claim", extractedFrom=url)
            sdk.set_point_baseline(p["id"], 2.0, 8.0, source="explicit")  # low prior, explicit
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T0', s.ingestedAt = $ts",
                params={"url": url, "ts": FRESH},
            )
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
            pt = sdk.get_point(p["id"])
            assert pt["ep_alpha"] == 2.0  # untouched despite strong T0 source
            assert pt["ep_beta"] == 8.0
            assert pt["baseline_source"] == "explicit"

    def test_multi_source_reliability_aggregation(self):
        """get_source_reliability on multi-source point asserts the aggregation formula."""
        import math as _math
        with fresh_sdk() as sdk:
            url = "https://multi-rel.example"
            p = sdk.create_point("statement", "multi", extractedFrom=url)
            sdk._get_proj()._link_source(p["id"], "https://multi-rel2.example")
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T1', s.ingestedAt = $ts",
                params={"url": "https://multi-rel.example", "ts": FRESH},
            )
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T3', s.ingestedAt = $ts",
                params={"url": "https://multi-rel2.example", "ts": FRESH},
            )
            # NOTE: reliability is per-SOURCE; each source's prior is its own tier
            r = sdk.get_source_reliability(url)
            assert r["reliability"] is not None
            prior = sdk._compute_source_prior(url)
            # The source-level prior uses the resolved tier of THIS source
            assert r["reliability"] == pytest.approx(
                prior["alpha"] / (prior["alpha"] + prior["beta"]), rel=5e-4)

    def test_calibrate_summary_excludes_assessment_points(self):
        with fresh_sdk() as sdk:
            url = "https://calib-excl.example"
            p = sdk.create_point("statement", "claim", extractedFrom=url)
            a = sdk.assess_source(url, "alice", 0.8, "an assessment")
            summary = sdk.calibrate_summary()
            ids = {item["id"] for item in summary}
            assert p["id"] in ids
            assert a["assessment_point_id"] not in ids  # assessments excluded

    def test_gate_stamp_not_refreshed_on_skip(self):
        """Within-interval skip does NOT refresh inherited_at (gate mechanism)."""
        with fresh_sdk() as sdk:
            url = "https://gate-stamp.example"
            p = sdk.create_point("statement", "claim", extractedFrom=url)
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T1', s.ingestedAt = $ts",
                params={"url": url, "ts": FRESH},
            )
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
            stamp1 = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN n.inherited_at",
                params={"id": p["id"]},
            ).result_set[0][0]
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=3600)
            stamp2 = sdk._get_proj().g.query(
                "MATCH (n:Point {id:$id}) RETURN n.inherited_at",
                params={"id": p["id"]},
            ).result_set[0][0]
            assert stamp1 == stamp2  # gate held — no refresh on skip

    def test_scaled_latest_wins_1000_assessments(self):
        """~1000 assessments (mixed outdated/active) → only the ACTIVE set counts."""
        with fresh_sdk() as sdk:
            url = "https://scaled.example"
            p = sdk.create_point("statement", "claim", extractedFrom=url)
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T2', s.ingestedAt = $ts",
                params={"url": url, "ts": FRESH},
            )
            # 20 HIGH-REP assessors assess 0.1 then 0.9 (older marked outdated)
            for i in range(20):
                _give_reputation(sdk, f"expert{i}")
                sdk.assess_source(url, f"expert{i}", 0.1, f"old {i}")
                sdk.assess_source(url, f"expert{i}", 0.9, f"new {i}")
            # 980 NEUTRAL assessors assess 0.9 once (rep 0.5 → contribute 0)
            for i in range(980):
                sdk.assess_source(url, f"neutral{i}", 0.9, f"assessment {i}")
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
            alpha = sdk.get_point(p["id"]).get("ep_alpha")
            # Only the 20 ACTIVE 0.9s (rep 2/3) count: factor = 1 + 20*0.1667*0.4 = 2.33 → clamped 2.0
            # → pc = 2.0*2.0 = 4.0 → alpha 5.0. If outdated 0.1s leaked (double-count),
            # their -0.4 terms cancel the +0.4 → factor ~1.0 → alpha ~3.0.
            assert alpha == pytest.approx(5.0, rel=1e-3)
            assert alpha > 3.0

    def test_assess_source_score_edges(self):
        """Inclusive score edges 0.0 and 1.0 are valid."""
        with fresh_sdk() as sdk:
            url = "https://edges.example"
            sdk.create_point("statement", "claim", extractedFrom=url)
            r1 = sdk.assess_source(url, "alice", 0.0, "floor")
            r2 = sdk.assess_source(url, "bob", 1.0, "ceiling")
            assert r1["score"] == 0.0
            assert r2["score"] == 1.0

    def test_set_source_tier_missing_source_raises(self):
        with fresh_sdk() as sdk:
            with pytest.raises(ValueError):
                sdk.set_source_tier("https://nope.example", "T1")

    def test_create_source_invalid_tier_raises(self):
        with fresh_sdk() as sdk:
            with pytest.raises(ValueError):
                sdk.create_source("https://bad-tier.example", "document", tier="T9")

    def test_get_source_reliability_unknown_url(self):
        with fresh_sdk() as sdk:
            r = sdk.get_source_reliability("https://unknown.example")
            assert r["reliability"] is None

    def test_crash_safe_latest_wins_double_active(self):
        """Two ACTIVE assessments (outdated flag absent) → only the latest counts."""
        with fresh_sdk() as sdk:
            _give_reputation(sdk, "crash-agent")
            url = "https://crash.example"
            p = sdk.create_point("statement", "claim", extractedFrom=url)
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) SET s.credibilityTier = 'T2', s.ingestedAt = $ts",
                params={"url": url, "ts": FRESH},
            )
            # Simulate a partial write: two active assessments, no outdated flag
            sdk.assess_source(url, "crash-agent", 0.1, "first")
            sdk.assess_source(url, "crash-agent", 0.9, "second")
            # Remove the outdated flag on the first (crash between create and mark)
            sdk._get_proj().g.query(
                "MATCH (p:Point {pointKind:'assessment'}) WHERE p.targetSource = $url "
                "AND p.score = 0.1 REMOVE p.outdated",
                params={"url": url},
            )
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
            alpha = sdk.get_point(p["id"]).get("ep_alpha")
            # Only the LATEST (0.9) counts: factor = 1 + (rep-0.5)*0.4 > 1 → alpha > 3.0.
            # If both counted: factor = 1 + (rep-0.5)*(-0.4+0.4) = 1.0 → alpha 3.0 exactly.
            assert alpha > 3.0


class TestMCPAnnotationsContract:
    def test_get_source_reliability_has_no_read_only_hint(self):
        """get_source_reliability writes the cache → must NOT be readOnlyHint.

        A regression adding readOnlyHint would silently auto-approve a
        cache-writing tool (plan Task 6 acceptance).
        """
        import asyncio
        from tortoise.mcp_server import mcp

        async def _get():
            tools = await mcp._list_tools()
            return {t.name: t for t in tools}

        tools = asyncio.run(_get())
        t = tools["tortoise_get_source_reliability"]
        # readOnlyHint must NOT be True (it writes the cache) — registered via
        # tool_registry with _rw() (readOnlyHint=False, destructiveHint=True).
        ro = getattr(t.annotations, "readOnlyHint", getattr(t.annotations, "read_only_hint", False))
        assert ro is not True, (
            "get_source_reliability writes the reliability cache — no readOnlyHint"
        )

    def test_assess_and_set_tier_are_destructive_hint(self):
        import asyncio
        from tortoise.mcp_server import mcp

        async def _get():
            tools = await mcp._list_tools()
            return {t.name: t for t in tools}

        tools = asyncio.run(_get())
        a = tools["tortoise_assess_source"].annotations
        b = tools["tortoise_set_source_tier"].annotations
        assert getattr(a, "destructiveHint", getattr(a, "destructive_hint", False)) is True
        assert getattr(b, "destructiveHint", getattr(b, "destructive_hint", False)) is True
