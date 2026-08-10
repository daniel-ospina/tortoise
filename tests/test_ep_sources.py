"""#341 — Mathematical validation: source priors are monotonic + directionally correct.

Embedded real-path suite (no Docker). T1: exact prior-level monotonicity theorem
for aggregate_prior. T2a: real-path prior ordering via _apply_source_inheritance +
get_point (NOT compute_confidence — its _flush_cache overwrites ep_alpha with
posteriors). T2b: EP directional audit via compute_confidence() confidences.

Deliberate overlap with test_source_inheritance_own.py (corroboration/anti-Sybil):
those assertions are re-derived here under the issue's situation numbering.

Documented spec corrections (see docs/plans/2026-08-08-ep-source-validation-proof.md):
  - The issue's "log curve flattens (10->100 adds less than 1->10)" is FALSE for
    decade totals (log2(101)-log2(11)=3.199 > log2(11)-log2(2)=2.459); true only
    per-source marginal (log2 concavity).
  - "10 T4 ~ 1 T2" is a 5.8x gap (pc 0.346 vs 2.0) — ordering asserted, not equality.
  - NAND -> beta pseudo-count model is fictional; production inheritance is
    positive-only (beta always 1.0); NAND lives in EP's factor domain (audit).
"""
from __future__ import annotations

import math
import os
import random
import sys
import tempfile
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK
from tortoise.source_credibility import TIER_PRIORS, aggregate_prior

FRESH = "2024-01-01T00:00:00+00:00"
DELTA = 1e-6          # exact prior math
EPSILON = 0.02        # EP convergence tolerance (directional)

TIER_MAP = dict(TIER_PRIORS)
TIER_PC = {tier: alpha - 1.0 for tier, (alpha, beta) in TIER_MAP.items()}


def log_aggregate_pc(base_pc: float, n_sources: int) -> float:
    """pc = base_pc * log2(N+1) — the issue's log-scale law."""
    return base_pc * math.log2(n_sources + 1)


def production_pc(groups: list[tuple]) -> float:
    """total_pc from the PRODUCTION aggregate_prior (uniform factor, decay 1.0).

    Bridges the formula (log_aggregate_pc) to the implementation so the T1
    monotonicity/ordering sweeps are falsifiable against real production code.
    """
    alpha, _ = aggregate_prior(groups, recency_decay=1.0)
    return alpha - 1.0


def beta_mean(alpha: float, beta: float) -> float:
    """Mean of Beta(alpha, beta). Note: the issue's alpha_eff = mean*(pc+2)
    form equals the implemented (1+pc, 1) ONLY at N=1 (see TestT1Theorem)."""
    return alpha / (alpha + beta)


@contextmanager
def fresh_sdk():
    with tempfile.TemporaryDirectory(prefix="tt_341_") as td:
        db_path = os.path.join(td, "test.db")
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


def link_tiered_source(sdk, pid: str, url: str, tier: str) -> None:
    """Link an additional tiered source to an existing point."""
    sdk._get_proj()._link_source(pid, url)
    sdk._get_proj().g.query(
        "MATCH (s:Source {url:$url}) SET s.credibilityTier = $t, s.ingestedAt = $ts",
        params={"url": url, "t": tier, "ts": FRESH},
    )


def inherited_alpha(sdk, pid: str) -> float | None:
    return sdk.get_point(pid).get("ep_alpha")


def make_point(sdk: TortoiseSDK, content: str, kind: str = "statement") -> dict:
    return sdk.create_point(kind, content)


def make_operator(sdk: TortoiseSDK, source_id: str, target_id: str,
                  op_type: str = "IMPL", direction: str = "bidirectional") -> dict:
    return sdk.create_operator(op_type, source_id, [target_id], direction=direction)


def build_scenario_a(sdk) -> tuple[str, str]:
    """Linear chain: Point_A ->[IMPL]-> Claim_B."""
    a = make_point(sdk, "Point A: evidence aggregation point")
    b = make_point(sdk, "Claim B: conclusion")
    make_operator(sdk, a["id"], b["id"], "IMPL")
    return a["id"], b["id"]


def build_scenario_b(sdk) -> tuple[str, str, str]:
    """Loopy cluster: A->B->C->A (unidirectional IMPL cycle)."""
    a = make_point(sdk, "Point A: loopy cluster")
    b = make_point(sdk, "Point B: loopy cluster")
    c = make_point(sdk, "Point C: loopy cluster")
    make_operator(sdk, a["id"], b["id"], "IMPL", direction="unidirectional")
    make_operator(sdk, b["id"], c["id"], "IMPL", direction="unidirectional")
    make_operator(sdk, c["id"], a["id"], "IMPL", direction="unidirectional")
    return a["id"], b["id"], c["id"]


def run_ep(sdk: TortoiseSDK, seed: int = 42, anchors: list[str] | None = None) -> dict:
    """Deterministic EP run.

    - recency_decay=1.0 passed EXPLICITLY through the whole chain — otherwise
      compute_confidence internally re-applies env default 0.95 (only masked by
      the 3600s inheritance gate today).
    - anchors=[...] skips the uncontrolled self.dream(dirty_only=True) branch
      (compute_confidence with no factors/anchors dreams first) and scopes the
      BFS subgraph — deterministic and side-effect-free.
    """
    random.seed(seed)
    kwargs = {"recency_decay": 1.0}
    if anchors is not None:
        kwargs["anchors"] = anchors
        kwargs["max_hops"] = 2
    return sdk.compute_confidence(**kwargs)


def get_conf(result: dict, node_id: str) -> float:
    return result["confidences"][node_id]["mean"]


@pytest.fixture(autouse=True)
def _seed_random():
    random.seed(42)


@pytest.fixture(autouse=True)
def _pin_ep_env(monkeypatch):
    """Pin decay + recompute env so tests pass BECAUSE of the pin, not the
    3600s gate. Prevents silent env-dependent drift (CI speed-ups etc.)."""
    monkeypatch.setenv("TORTOISE_EP_RECENCY_DECAY", "1.0")
    monkeypatch.setenv("TORTOISE_EP_REINHERIT_INTERVAL", "0")


# ═══════════════════════════════════════════════════════════════════
# T1 — Exact prior-level monotonicity theorem (pure function, no EP)
# ═══════════════════════════════════════════════════════════════════

class TestT1Theorem:
    """T1: exact prior-level monotonicity of the log-scale law."""

    def test_log2_strictly_increasing(self):
        for n in range(1, 100):
            assert math.log2(n + 2) > math.log2(n + 1)

    def test_per_source_marginal_decreases(self):
        """log2 concavity: 1->2 adds more than 10->11, per tier."""
        for tier in TIER_PC:
            base = TIER_PC[tier]
            m12 = base * (math.log2(3) - math.log2(2))
            m1011 = base * (math.log2(12) - math.log2(11))
            assert m12 > m1011

    def test_issue_decade_claim_corrected(self):
        """Issue's '10->100 adds less than 1->10' is FALSE for totals;
        true only per-source. Assert the correct statement."""
        g_1_10 = math.log2(11) - math.log2(2)     # 2.459
        g_10_100 = math.log2(101) - math.log2(11)  # 3.199
        assert g_10_100 > g_1_10  # decade totals grow (documented correction)
        m_9_10 = math.log2(11) - math.log2(10)
        m_99_100 = math.log2(101) - math.log2(100)
        assert m_9_10 > m_99_100  # per-source marginal shrinks

    def test_anti_sybil_1m_t4_lt_2_t0(self):
        pc_1m_t4 = 0.1 * math.log2(1_000_001)
        pc_2_t0 = 9.0 * math.log2(3)
        assert pc_1m_t4 < pc_2_t0

    def test_10_t4_gt_1_t4(self):
        assert production_pc([("T4", FRESH, FRESH, 1.0)] * 10) \
            > production_pc([("T4", FRESH, FRESH, 1.0)])

    def test_1000_t4_lt_1_t2(self):
        assert production_pc([("T4", FRESH, FRESH, 1.0)] * 1000) \
            < production_pc([("T2", FRESH, FRESH, 1.0)])

    def test_monotone_in_n_all_tiers(self):
        for tier in TIER_PC:
            prev = 0.0  # n=0: no sources -> total_pc 0
            for n in range(1, 21):
                cur = production_pc([(tier, FRESH, FRESH, 1.0)] * n)
                assert cur > prev
                prev = cur

    def test_reparameterization_identity_holds_at_n1_only(self):
        """Two readings of the issue's alpha_eff = mean*(pc_eff+2) form:
        (a) constant-mean reading (mean = the tier's FIXED base mean): identical
            to implemented (1+pc, 1) only at N=1; diverges at N>1 — the
            implementation (1+pc, 1) pushes mean toward 1 (confidence rises),
            the constant-mean form holds mean fixed (variance shrinks only).
        (b) dynamic-mean reading (mean = (1+pc)/(2+pc)): identical for all N
            (research doc's symbolic identity — verified).
        This test pins reading (a), which is the issue formula's literal
        interpretation, so the divergence cannot silently change."""
        for tier in TIER_PC:
            base_alpha, _b = TIER_MAP[tier]
            mean = base_alpha / (base_alpha + 1.0)
            # N=1: exact identity
            a_eff = mean * (TIER_PC[tier] + 2)
            assert a_eff == pytest.approx(1.0 + TIER_PC[tier], rel=1e-9)
            # N>1: issue formula mean stays FIXED; implemented mean rises
            pc_10 = log_aggregate_pc(TIER_PC[tier], 10)
            a_issue = mean * (pc_10 + 2)
            a_impl = 1.0 + pc_10
            assert a_issue < a_impl  # divergence pinned (implementation stronger)
        # concrete: 10 T4 → issue 1.229 vs implemented 1.346
        m_t4 = TIER_MAP["T4"][0] / (TIER_MAP["T4"][0] + 1.0)
        pc_10_t4 = log_aggregate_pc(TIER_PC["T4"], 10)
        assert m_t4 * (pc_10_t4 + 2) == pytest.approx(1.2287, rel=1e-3)
        assert 1.0 + pc_10_t4 == pytest.approx(1.3459, rel=1e-3)

    def test_aggregate_prior_matches_formula(self):
        """Real aggregate_prior (uniform factor, decay 1.0) == formula."""
        # 2 x T0 same-tier: pc = 9 * log2(3)
        a, b = aggregate_prior(
            [("T0", FRESH, FRESH, 1.0), ("T0", FRESH, FRESH, 1.0)],
            recency_decay=1.0,
        )
        assert a == pytest.approx(1.0 + 9.0 * math.log2(3), rel=1e-9)
        assert b == pytest.approx(1.0, rel=1e-9)
        # 10 x T4: pc = 0.1 * log2(11)
        a2, b2 = aggregate_prior(
            [("T4", FRESH, FRESH, 1.0)] * 10,
            recency_decay=1.0,
        )
        assert a2 == pytest.approx(1.0 + 0.1 * math.log2(11), rel=1e-9)
        assert b2 == pytest.approx(1.0, rel=1e-9)


# ═══════════════════════════════════════════════════════════════════
# T2a — Real-path prior ordering (deterministic, NO EP run)
# ═══════════════════════════════════════════════════════════════════

class TestSituation1_NoSourceToT4:
    def test_t4_above_no_source(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s1.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha = inherited_alpha(sdk, pid)
        # Beta(1.1, 1) vs Beta(1,1) baseline — mean 0.5238 > 0.5
        assert alpha == pytest.approx(1.1, rel=1e-9)
        assert beta_mean(alpha, 1.0) > 0.5


class TestSituation2_TierProportional:
    def test_tier_ordering_exact(self):
        alphas = {}
        for tier in ["T4", "T3", "T2", "T1", "T0"]:
            with fresh_sdk() as sdk:
                pid = tier_source(sdk, f"https://{tier}.example", tier)
                sdk._apply_source_inheritance(recency_decay=1.0)
                alphas[tier] = inherited_alpha(sdk, pid)
                assert alphas[tier] == pytest.approx(TIER_MAP[tier][0], rel=1e-9)
        assert alphas["T4"] < alphas["T3"] < alphas["T2"] < alphas["T1"] < alphas["T0"]


class TestSituation3_CumulativeWeakSources:
    def test_each_addition_increases(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s0.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            prev = inherited_alpha(sdk, pid)
            for i in range(1, 10):
                link_tiered_source(sdk, pid, f"https://s{i}.example", "T4")
                sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
                cur = inherited_alpha(sdk, pid)
                assert cur > prev
                assert cur == pytest.approx(1.0 + 0.1 * math.log2(i + 2), rel=1e-9)
                prev = cur

    def test_10_t4_exact(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s0.example", "T4")
            for i in range(1, 10):
                link_tiered_source(sdk, pid, f"https://s{i}.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, pid) == pytest.approx(
                1.0 + 0.1 * math.log2(11), rel=1e-9)


class TestSituation4_AntiSybil:
    def test_10_t4_lt_1_t2(self):
        """Ordering, not equality — issue's '10 T4 ~ 1 T2' is a 5.8x gap."""
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s0.example", "T4")
            for i in range(1, 10):
                link_tiered_source(sdk, pid, f"https://s{i}.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            pc_10t4 = inherited_alpha(sdk, pid) - 1.0
        with fresh_sdk() as sdk:
            pid2 = tier_source(sdk, "https://t2.example", "T2")
            sdk._apply_source_inheritance(recency_decay=1.0)
            pc_1t2 = inherited_alpha(sdk, pid2) - 1.0
        assert pc_10t4 < pc_1t2
        assert pc_10t4 > 0.1  # and beats 1 T4


class TestSituation5_CeilingEffect:
    def test_2_gold_plus_t4_increases_slightly(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://g0.example", "T0")
            link_tiered_source(sdk, pid, "https://g1.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha_2gold = inherited_alpha(sdk, pid)
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://g0.example", "T0")
            link_tiered_source(sdk, pid, "https://g1.example", "T0")
            link_tiered_source(sdk, pid, "https://t4.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha_2gold_t4 = inherited_alpha(sdk, pid)
        gain = alpha_2gold_t4 - alpha_2gold
        assert gain > 0
        # adding a T4 must gain LESS than adding a 3rd gold T0 to 2 existing
        # golds (gold count 2->3 = 9*(log2(4)-log2(3)) ~ 3.735; T4 gain ~0.1)
        assert gain < 9.0 * (math.log2(4) - math.log2(3))


class TestSituation6_GoldPlusT4NoPullDown:
    def test_5_gold_plus_t4_not_below_5_gold(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://g0.example", "T0")
            for i in range(1, 5):
                link_tiered_source(sdk, pid, f"https://g{i}.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha_5gold = inherited_alpha(sdk, pid)
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://g0.example", "T0")
            for i in range(1, 5):
                link_tiered_source(sdk, pid, f"https://g{i}.example", "T0")
            link_tiered_source(sdk, pid, "https://t4.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            alpha_5gold_t4 = inherited_alpha(sdk, pid)
        assert alpha_5gold_t4 >= alpha_5gold  # regression guard: never pulls down
        assert alpha_5gold_t4 > alpha_5gold   # strictly up (pc_t4 > 0)


class TestT2aEdgeCases:
    def test_untiered_source_deletes_inherited_baseline(self):
        """Surprising-but-real semantic: mutating a sourced point's source to
        untiered drops it from eligibility -> revert path REMOVES the baseline."""
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s0.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, pid) == pytest.approx(1.1, rel=1e-9)
            sdk._get_proj().g.query(
                "MATCH (s:Source {url:$url}) REMOVE s.credibilityTier",
                params={"url": "https://s0.example"},
            )
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
            assert inherited_alpha(sdk, pid) is None

    def test_no_source_no_baseline(self):
        """Point with no extractedFrom never gets an inherited baseline."""
        with fresh_sdk() as sdk:
            p = sdk.create_point("statement", "orphan point")
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, p["id"]) is None


class TestSituation7_AddRemoveIdempotent:
    def test_remove_returns_to_baseline(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s0.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, pid) == pytest.approx(1.1, rel=1e-9)
            # remove the source edge (raw Cypher) + force recompute
            sdk._get_proj().g.query(
                "MATCH (n:Point {id:$pid})-[r:extractedFrom]->(s:Source {url:$url}) DELETE r",
                params={"pid": pid, "url": "https://s0.example"},
            )
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
            # no sources -> neutral (no ep_alpha on graph)
            assert inherited_alpha(sdk, pid) is None

    def test_remove_one_of_two_returns_to_single(self):
        with fresh_sdk() as sdk:
            pid = tier_source(sdk, "https://s0.example", "T4")
            link_tiered_source(sdk, pid, "https://s1.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, pid) == pytest.approx(
                1.0 + 0.1 * math.log2(3), rel=1e-9)
            sdk._get_proj().g.query(
                "MATCH (n:Point {id:$pid})-[r:extractedFrom]->(s:Source {url:$url}) DELETE r",
                params={"pid": pid, "url": "https://s1.example"},
            )
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
            assert inherited_alpha(sdk, pid) == pytest.approx(1.1, rel=1e-9)

    def test_revert_is_idempotent_through_ep_path(self):
        """Full-path idempotency: after revert, compute_confidence returns to
        neutral. Requires an operator so EP actually runs (no operators ->
        empty confidences -> this test would be vacuous).

        Regression test for #652: _apply_source_inheritance revert clears the
        stale (alpha, beta) from sdk._evidence so EP no longer re-applies
        the deleted prior."""
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            link_tiered_source(sdk, a_id, "https://s0.example", "T4")
            sdk._apply_source_inheritance(recency_decay=1.0)
            assert inherited_alpha(sdk, a_id) == pytest.approx(1.1, rel=1e-9)
            sdk._get_proj().g.query(
                "MATCH (n:Point {id:$pid})-[r:extractedFrom]->(s:Source {url:$url}) DELETE r",
                params={"pid": a_id, "url": "https://s0.example"},
            )
            sdk._apply_source_inheritance(recency_decay=1.0, recompute_interval=0)
            assert inherited_alpha(sdk, a_id) is None  # graph read is clean
            # EP path must also see the revert — no stale prior:
            res = sdk.compute_confidence(recency_decay=1.0, anchors=[a_id], max_hops=2)
            conf = get_conf(res, a_id)
            # #652 regression property: after revert the point carries NO
            # evidence — its confidence must reflect operator coupling only,
            # NOT the stale prior (0.7/0.3 → mean 0.7). With the bug, EP
            # resurrects the prior and reports ~0.70; fixed, it drops well
            # below neutral (coupling pulls it to ~0.39 on the current EP
            # baseline after #400/#651 — the absolute value is baseline-
            # dependent, which is why we assert the band, not exactly 0.5).
            assert 0.2 < conf < 0.55, (
                f"reverted point must not carry elevated confidence from the "
                f"stale prior; got {conf}")


# ═══════════════════════════════════════════════════════════════════
# T2b — EP directional audit (loose margins, seeded)
# ═══════════════════════════════════════════════════════════════════

class TestScenarioA_LinearChain:
    def test_b_rises_through_impl(self):
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            # no sources: baseline ~0.633, NOT 0.5 — the bidirectional IMPL
            # proportional boost (1+2/max(alpha+beta-1,1)) raises unevidenced
            # targets off neutral
            res0 = run_ep(sdk, anchors=[a_id])
            b0 = get_conf(res0, b_id)
            # attach T0 source DIRECTLY to A (extractedFrom on A — required for
            # EP: only operator factors are auto-extracted, orphan points inert)
            link_tiered_source(sdk, a_id, "https://s0.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            res1 = run_ep(sdk, anchors=[a_id])
            b1 = get_conf(res1, b_id)
        assert b1 > b0 + EPSILON  # B responds through IMPL (delta, robust to baseline shifts)

    def test_more_sources_rises_b(self):
        """0 vs 3 T0 sources on A (wide prior gap) so B's posterior response is
        comfortably above noise — measured margin ~0.088 (0.633 -> 0.721)."""
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            b0 = get_conf(run_ep(sdk, anchors=[a_id]), b_id)
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            for i in range(3):
                link_tiered_source(sdk, a_id, f"https://s{i}.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            b3 = get_conf(run_ep(sdk, anchors=[a_id]), b_id)
        assert b3 > b0 + EPSILON  # 0 vs 3 T0 sources (measured margin ~0.088 > 0.02)


class TestScenarioB_LoopySingleEntry:
    def test_cluster_rises_from_single_entry(self):
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            res0 = run_ep(sdk, anchors=[a_id])
            assert res0["converged"] is True  # precondition: loopy BP converged
            base = {"a": get_conf(res0, a_id), "b": get_conf(res0, b_id),
                    "c": get_conf(res0, c_id)}
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            link_tiered_source(sdk, a_id, "https://s0.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            res1 = run_ep(sdk, anchors=[a_id])
            assert res1["converged"] is True
            after = {"a": get_conf(res1, a_id), "b": get_conf(res1, b_id),
                     "c": get_conf(res1, c_id)}
        for role in ("a", "b", "c"):
            assert after[role] >= base[role] - EPSILON  # not-lower (loose)
        # strict rise on the entry node (pre-declared relaxation)
        assert max(after[r] - base[r] for r in after) > 0


class TestScenarioC_LoopyMultiEntry:
    def test_multi_entry_rises_more_than_single(self):
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            link_tiered_source(sdk, a_id, "https://s0.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            res_s = run_ep(sdk, anchors=[a_id])
            assert res_s["converged"] is True
            single = {"a": get_conf(res_s, a_id), "b": get_conf(res_s, b_id),
                      "c": get_conf(res_s, c_id)}
        with fresh_sdk() as sdk:
            a_id, b_id, c_id = build_scenario_b(sdk)
            link_tiered_source(sdk, a_id, "https://s0.example", "T0")
            link_tiered_source(sdk, b_id, "https://s1.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            res_m = run_ep(sdk, anchors=[a_id])
            assert res_m["converged"] is True
            multi = {"a": get_conf(res_m, a_id), "b": get_conf(res_m, b_id),
                     "c": get_conf(res_m, c_id)}
        for role in ("a", "b", "c"):
            assert multi[role] >= single[role] - EPSILON  # >= single-entry (loose)
        # at least one node clearly higher (pre-declared relaxation)
        assert max(multi[r] - single[r] for r in multi) > 0


class TestSituation10_ChainResponse:
    def test_source_on_a_moves_b(self):
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            # T2 source (mean 0.75) — robust margin for B's response; T4 would
            # be marginal at w=1.0 (prior mean 0.5238)
            link_tiered_source(sdk, a_id, "https://s0.example", "T2")
            sdk._apply_source_inheritance(recency_decay=1.0)
            res = run_ep(sdk, anchors=[a_id])
            a_conf = get_conf(res, a_id)
            b_conf = get_conf(res, b_id)
        assert a_conf > 0.5 + EPSILON
        assert b_conf > 0.5 + EPSILON  # B responds through IMPL + bidirectional EP
        assert b_conf <= a_conf + EPSILON  # attenuation: B not above A


class TestEdgeCaseInvariants:
    CONFIGS = [{"T4": 1}, {"T0": 1}, {"T4": 10}, {"T0": 1, "T4": 1}]

    def test_convergence_under_50(self):
        for cfg in self.CONFIGS:
            with fresh_sdk() as sdk:
                a_id, b_id = build_scenario_a(sdk)
                for i, (tier, n) in enumerate(cfg.items()):
                    for j in range(n):
                        link_tiered_source(sdk, a_id, f"https://cfg{i}-{j}.example", tier)
                sdk._apply_source_inheritance(recency_decay=1.0)
                res = run_ep(sdk, anchors=[a_id])
                assert res["converged"] is True
                # convergence CONTRACT is converged=True; the max_iter hard cap
                # (50) makes the iteration count structurally bounded, so no
                # separate speed assertion — not a coupling point for EP
                # internals (#326 may change dynamics)

    def test_confidence_bounds(self):
        for cfg in self.CONFIGS:
            with fresh_sdk() as sdk:
                a_id, b_id = build_scenario_a(sdk)
                for i, (tier, n) in enumerate(cfg.items()):
                    for j in range(n):
                        link_tiered_source(sdk, a_id, f"https://cfg{i}-{j}.example", tier)
                sdk._apply_source_inheritance(recency_decay=1.0)
                res = run_ep(sdk, anchors=[a_id])
                for cid in (a_id, b_id):
                    conf = get_conf(res, cid)
                    assert 0.0 <= conf <= 1.0

    def test_determinism_seeded(self):
        """Two fresh SDKs built identically → same confidence (anchored subgraph + EP
        path, 3-factor loopy topology — real random.shuffle draws, seed-pinned)."""
        confs = []
        for _ in range(2):
            with fresh_sdk() as sdk:
                a_id, b_id, c_id = build_scenario_b(sdk)  # 3 factors — real shuffle
                link_tiered_source(sdk, a_id, "https://s0.example", "T0")
                sdk._apply_source_inheritance(recency_decay=1.0)
                res = run_ep(sdk, anchors=[a_id], seed=42)
                confs.append(get_conf(res, a_id))
        assert abs(confs[0] - confs[1]) < 1e-9


# ═══════════════════════════════════════════════════════════════════
# Situations 8-9 — DOCUMENTED AUDIT (never silently encoded as expected)
# ═══════════════════════════════════════════════════════════════════

class TestSituation8_GoldPlusNand_Audit:
    def test_gold_alone_anchors_high(self):
        """Gold source alone anchors the claim high (the part that IS asserted)."""
        with fresh_sdk() as sdk:
            a_id, b_id = build_scenario_a(sdk)
            link_tiered_source(sdk, a_id, "https://g0.example", "T0")
            sdk._apply_source_inheritance(recency_decay=1.0)
            conf = get_conf(run_ep(sdk, anchors=[a_id]), a_id)
        assert conf >= 0.8  # T0 single source -> Beta(10,1) mean 0.909


class TestSituation9_Mitigation_Audit:
    def test_mitigation_weight_mechanics(self):
        """AUDIT (documented, not directional): real mitigation is
        compute_operator_weight (weights.py:9) — w *= 2.0 when an operator
        targets another operator. The issue's pc*0.5 mitigation model is
        fictional (old suite). This pins the MECHANIC (not NAND direction)."""
        import tempfile
        import tortoise.weights as weights_mod

        with tempfile.TemporaryDirectory(prefix="tt_341_") as td:
            sdk = TortoiseSDK(os.path.join(td, "test.db"))
            try:
                a = sdk.create_point("statement", "A")
                b = sdk.create_point("statement", "B")
                op = sdk.create_operator("IMPL", a["id"], [b["id"]])
                # plain operator -> w == 1.0 (no input operator)
                w_plain = weights_mod.compute_operator_weight(sdk._get_proj(), op["id"])
                assert w_plain == pytest.approx(1.0, rel=1e-9)
                # operator targeting an operator -> w == 2.0 for IMPL;
                # NAND base 8.0 (#855) × 2.0 mitigation = 16 → clamped 10.0
                op2 = sdk.create_operator("NAND", a["id"], [op["id"]])
                w_mit = weights_mod.compute_operator_weight(sdk._get_proj(), op2["id"])
                assert w_mit == pytest.approx(10.0, rel=1e-9)
            finally:
                sdk.close()
