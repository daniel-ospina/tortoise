"""Issue #1411 — Tier-2 streams: AC gates (L1 ≥0.85/≤0.5 + ≥5× + provenance,
L2 ≥30% reduction + rising reuse + held-out baseline, L3 slope >0 vs ≈0,
L4 100% by N+1 + latency trend, L5 ≥90%/≥80%/≤10%, L6 ≥0.95 + no dropped
pairs)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.streams.l1_interdependent import L1InterdependentStream
from battery.streams.l2_pseudo_evolution import L2PseudoEvolutionStream
from battery.streams.l3_quality_trajectory import L3QualityTrajectoryStream
from battery.streams.l4_cross_session import L4CrossSessionStream
from battery.streams.l5_drift import L5DecisionDriftStream
from battery.streams.l6_distillation import L6DistillationStream


class TestL1:
    def test_interdependent_success_gate(self):
        st = L1InterdependentStream()
        sessions = [{"subtask_success": True}] * 9 + [{"subtask_success": False}]
        r = st.score(sessions, None, 0.85)
        assert r.value == 0.9 and r.passed

    def test_recall_before_rederive(self):
        st = L1InterdependentStream()
        s = [{"recalled_before_rederive": True}] * 9 + \
            [{"recalled_before_rederive": False}]
        assert st.recall_before_rederive_rate(s) >= 0.90

    def test_rederivation_ratio_5x(self):
        st = L1InterdependentStream()
        assert st.rederivation_ratio(5, 25) >= 5.0

    def test_provenance_criterion(self):
        st = L1InterdependentStream()
        assert st.provenance_ok({"provenance_citations": [
            {"point_id": "p-1", "confidence": 0.9}]})
        assert not st.provenance_ok({"provenance_citations": [
            {"point_id": "p-1"}]})
        assert not st.provenance_ok({"provenance_citations": [
            {"point_id": "p-1", "confidence": 1.5}]})


class TestL2:
    def _sessions(self):
        # family f1: rep1=100 tokens, rep2=80, rep3=60 → 40% reduction
        return [
            {"family": "f1", "rep": 1, "tokens": 100,
             "strategy_reuse_rate": 0.1},
            {"family": "f1", "rep": 2, "tokens": 80,
             "strategy_reuse_rate": 0.4},
            {"family": "f1", "rep": 3, "tokens": 60,
             "strategy_reuse_rate": 0.7},
        ]

    def test_token_reduction_30pct(self):
        st = L2PseudoEvolutionStream()
        r = st.score(self._sessions(), None, 0.30)
        assert r.value == pytest.approx(0.40)
        assert r.passed
        assert "provisional" in r.evidence[0]

    def test_flat_tokens_fail(self):
        st = L2PseudoEvolutionStream()
        flat = [{"family": "f1", "rep": 1, "tokens": 100},
                {"family": "f1", "rep": 3, "tokens": 100}]
        assert not st.score(flat, None, 0.30).passed  # pseudo-evolution

    def test_reuse_rising(self):
        assert L2PseudoEvolutionStream().strategy_reuse_trend(self._sessions())

    def test_held_out_baseline(self):
        assert L2PseudoEvolutionStream().held_out_baseline(0.6, 0.5)
        assert not L2PseudoEvolutionStream().held_out_baseline(0.4, 0.5)


class TestL3:
    def test_positive_slope(self):
        st = L3QualityTrajectoryStream()
        sessions = [{"wave": 0, "quality_index": 0.5},
                    {"wave": 1, "quality_index": 0.6},
                    {"wave": 2, "quality_index": 0.7}]
        r = st.score(sessions, None, 0.0)
        assert r.value > 0 and r.passed

    def test_flat_control(self):
        st = L3QualityTrajectoryStream()
        sessions = [{"wave": 0, "quality_index": 0.5},
                    {"wave": 1, "quality_index": 0.51},
                    {"wave": 2, "quality_index": 0.5}]
        r = st.score(sessions, None, 0.05)
        assert not r.passed  # ≈0 slope for control


class TestL4:
    def test_surfaced_by_query(self):
        st = L4CrossSessionStream()
        sessions = [{"planted_session": 1, "surfaced_session": 6,
                     "query_session": 6, "resolved_via_supersede": True}] * 5
        assert st.score(sessions, None, 1.0).passed

    def test_missed_contradiction_fails(self):
        st = L4CrossSessionStream()
        sessions = [{"planted_session": 1, "surfaced_session": None,
                     "query_session": 6, "resolved_via_supersede": False}]
        assert not st.score(sessions, None, 1.0).passed


class TestL5:
    def test_drift_gates(self):
        st = L5DecisionDriftStream()
        sessions = [{"decision_matches": True, "rationale_matches": True,
                     "hallucinated_rationale": False}] * 9 + \
                   [{"decision_matches": False, "rationale_matches": False,
                     "hallucinated_rationale": True}]
        assert st.score(sessions, None, 0.90).passed
        assert st.rationale_consistency(sessions) >= 0.80
        assert st.hallucinated_rationale_rate(sessions) <= 0.10


class TestL6:
    def test_fidelity_095(self):
        st = L6DistillationStream()
        sessions = [{"distilled_score": 0.97, "raw_score": 1.0,
                     "contradiction_dropped": False}] * 5
        r = st.score(sessions, None, 0.95)
        assert r.value == pytest.approx(0.97)
        assert r.passed

    def test_dropped_pair_fails(self):
        st = L6DistillationStream()
        sessions = [{"distilled_score": 0.99, "raw_score": 1.0,
                     "contradiction_dropped": True}]
        assert not st.score(sessions, None, 0.95).passed
