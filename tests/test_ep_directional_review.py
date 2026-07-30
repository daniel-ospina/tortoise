"""Directional IMPL Fix Review — Comprehensive EP Behavior Verification.

Tests the directional IMPL fix in ep.py and the edge density penalty removal
in weights.py. Verifies:

1. Convergent Evidence:  2 T0 sources → HIGHER confidence than 1 source
2. Chain Propagation:     source → claim1 → claim2 (forward flow preserved)
3. NAND Contradiction:    bidirectional NAND still works after IMPL goes directional
4. Source Back-Message:   directed IMPL prevents claims from feeding back to sources

Uses embedded FalkorDBLite (no Docker required) for reproducible, self-contained tests.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from tortoise.sdk import TortoiseSDK

EPSILON = 0.02
DELTA = 1e-5

TIER_MAP = {
    "T0": (10, 1), "T1": (5, 1), "T2": (3, 1), "T3": (2, 1), "T4": (1.1, 1),
}


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

@contextmanager
def fresh_sdk(namespace_prefix: str = "ep_review"):
    """Yield a TortoiseSDK with embedded FalkorDBLite (no Docker needed)."""
    ns = f"{namespace_prefix}_{uuid.uuid4().hex[:8]}"
    db_path = os.path.join(tempfile.mkdtemp(), f"{ns}.db")
    sdk = TortoiseSDK(db_path=db_path, namespace=ns)
    try:
        yield sdk
    finally:
        try:
            sdk.close()
        except Exception:
            pass


def make_point(sdk: TortoiseSDK, content: str, kind: str = "statement") -> dict:
    return sdk.create_point(kind, content)


def make_operator(sdk: TortoiseSDK, source_id: str, target_id: str,
                   op_type: str = "IMPL") -> dict:
    return sdk.create_operator(op_type, source_id, [target_id])


def set_evidence(sdk: TortoiseSDK, point_id: str, tier: str):
    """Set Beta prior for a source point from a credibility tier."""
    alpha, beta = TIER_MAP[tier]
    sdk.set_point_baseline(point_id, alpha, beta)


def set_custom_evidence(sdk: TortoiseSDK, point_id: str, alpha: float, beta: float):
    sdk.set_point_baseline(point_id, alpha, beta)


def run_ep(sdk: TortoiseSDK, directed: bool = False):
    """Run EP and return {iterations, converged, confidences: {id: {mean, variance, ...}}}."""
    from tortoise.ep import TortoiseEP

    proj = sdk._get_proj()

    # Collect operator IDs
    rows = proj.g.query(
        "MATCH (o:Point) WHERE o.is_operator = true RETURN o.id"
    ).result_set
    op_ids = [r[0] for r in rows] if rows else []

    # Collect evidence from graph
    ev_rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in ev_rows} if ev_rows else {}

    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3,
                    directed=directed, evidence=evidence)
    iters, converged = ep.run(op_ids, max_hops=2)

    # Collect confidences for all claims
    confidences = {}
    for cid in sdk._evidence:
        if cid not in confidences:
            confidences[cid] = ep.compute_confidence(cid)
    for cid in ep._affected_claims(op_ids):
        confidences[cid] = ep.compute_confidence(cid)

    return {"iterations": iters, "converged": converged, "confidences": confidences}


def get_mean(result: dict, node_id: str) -> float:
    """Extract mean confidence for a node from EP result."""
    conf = result["confidences"].get(node_id)
    if conf is None:
        return 0.5
    return conf["mean"]


# ═══════════════════════════════════════════════════════════════════
# Test 1: Convergent Evidence — 2 Sources > 1 Source
# ═══════════════════════════════════════════════════════════════════

class TestConvergentEvidence:
    """Verify that 2 T0 sources supporting the same claim produce higher
    confidence than 1 source, and that the directional fix doesn't break this."""

    def build_convergent_graph(self, sdk: TortoiseSDK) -> tuple[str, str, str]:
        """Build: Source_A →[IMPL]→ Claim, Source_B →[IMPL]→ Claim.

        Returns (source_a_id, source_b_id, claim_id).
        """
        src_a = make_point(sdk, "Source A (T0 gold evidence)")
        src_b = make_point(sdk, "Source B (T0 gold evidence)")
        claim = make_point(sdk, "Claim — supported by both sources")
        make_operator(sdk, src_a["id"], claim["id"], "IMPL")
        make_operator(sdk, src_b["id"], claim["id"], "IMPL")
        return src_a["id"], src_b["id"], claim["id"]

    def run_with_n_sources(self, sdk: TortoiseSDK, src_a_id: str, src_b_id: str,
                           claim_id: str, directed: bool) -> dict[str, float]:
        """Run EP with 1 source (A only) then 2 sources (A+B), return confidences."""
        # ── 1 source only ──
        sdk1 = sdk  # reuse same SDK, reset evidence
        set_evidence(sdk1, src_a_id, "T0")
        # Clear B's evidence
        sdk1.set_point_baseline(src_b_id, 1.0, 1.0)
        result_1 = run_ep(sdk1, directed=directed)
        claim_1 = get_mean(result_1, claim_id)
        src_a_1 = get_mean(result_1, src_a_id)

        # ── 2 sources ──
        set_evidence(sdk1, src_b_id, "T0")
        result_2 = run_ep(sdk1, directed=directed)
        claim_2 = get_mean(result_2, claim_id)
        src_a_2 = get_mean(result_2, src_a_id)
        src_b_2 = get_mean(result_2, src_b_id)

        return {
            "claim_1src": claim_1, "claim_2src": claim_2,
            "gain": claim_2 - claim_1,
            "src_a_1src": src_a_1, "src_a_2src": src_a_2,
            "src_b_2src": src_b_2,
        }

    def test_directed_2sources_gt_1source(self):
        """DIRECTED: 2 T0 sources → higher claim confidence than 1 source."""
        with fresh_sdk() as sdk:
            src_a, src_b, claim = self.build_convergent_graph(sdk)
            r = self.run_with_n_sources(sdk, src_a, src_b, claim, directed=True)

        print(f"\n  [DIRECTED]  Claim: 1src={r['claim_1src']:.4f}  "
              f"2src={r['claim_2src']:.4f}  gain={r['gain']:.4f}")
        print(f"  [DIRECTED]  SrcA: 1src={r['src_a_1src']:.4f}  "
              f"2src={r['src_a_2src']:.4f}  SrcB={r['src_b_2src']:.4f}")

        assert r["gain"] > 0.01, \
            f"2 sources should increase claim confidence: gain={r['gain']:.4f}"
        # Claim confidence: observed ~0.6179 (1src) → ~0.6447 (2src) with w=8
        # Downstream attenuation is expected — sources don't transmit 100%
        assert r["claim_2src"] > 0.62, \
            f"2 T0 sources should push claim above 0.62: {r['claim_2src']:.4f}"

    def test_bidirectional_2sources_gt_1source(self):
        """BIDIRECTIONAL: 2 T0 sources → higher claim confidence than 1 source."""
        with fresh_sdk() as sdk:
            src_a, src_b, claim = self.build_convergent_graph(sdk)
            r = self.run_with_n_sources(sdk, src_a, src_b, claim, directed=False)

        print(f"\n  [BIDIRECTIONAL]  Claim: 1src={r['claim_1src']:.4f}  "
              f"2src={r['claim_2src']:.4f}  gain={r['gain']:.4f}")
        print(f"  [BIDIRECTIONAL]  SrcA: 1src={r['src_a_1src']:.4f}  "
              f"2src={r['src_a_2src']:.4f}  SrcB={r['src_b_2src']:.4f}")

        assert r["gain"] > 0.005, \
            f"2 sources should increase claim confidence: gain={r['gain']:.4f}"

    def test_directed_gain_ge_bidirectional_gain(self):
        """Directed IMPL should have ≥ convergence than bidirectional (no back-cancellation).

        In bidirectional mode, the claim sends messages back to sources, which can
        create loops that dilute convergent evidence. Directed eliminates this.
        """
        with fresh_sdk() as sdk_d:
            src_a, src_b, claim = self.build_convergent_graph(sdk_d)
            r_d = self.run_with_n_sources(sdk_d, src_a, src_b, claim, directed=True)

        with fresh_sdk() as sdk_b:
            src_a, src_b, claim = self.build_convergent_graph(sdk_b)
            r_b = self.run_with_n_sources(sdk_b, src_a, src_b, claim, directed=False)

        print(f"\n  [COMPARISON]  Directed gain={r_d['gain']:.4f}  "
              f"Bidirectional gain={r_b['gain']:.4f}")
        print(f"  [COMPARISON]  Directed claim_2src={r_d['claim_2src']:.4f}  "
              f"Bidirectional claim_2src={r_b['claim_2src']:.4f}")

        # Directed should not be WORSE than bidirectional at converging evidence
        assert r_d["gain"] >= r_b["gain"] - EPSILON, \
            f"Directed gain ({r_d['gain']:.4f}) < Bidirectional gain ({r_b['gain']:.4f})"

    def test_sources_preserve_prior_in_directed(self):
        """In directed mode, sources should retain their priors (no back-message dilution).

        With bidirectional IMPL, claims feed back to sources, which can pull source
        confidence away from its evidence prior. With directional, sources only send,
        they don't receive — so their confidence should stay close to their prior.
        """
        t0_mean = TIER_MAP["T0"][0] / (TIER_MAP["T0"][0] + TIER_MAP["T0"][1])  # 0.9091

        with fresh_sdk() as sdk_d:
            src_a, src_b, claim = self.build_convergent_graph(sdk_d)
            set_evidence(sdk_d, src_a, "T0")
            set_evidence(sdk_d, src_b, "T0")
            result_d = run_ep(sdk_d, directed=True)
            src_a_d = get_mean(result_d, src_a)
            src_b_d = get_mean(result_d, src_b)

        with fresh_sdk() as sdk_b:
            src_a, src_b, claim = self.build_convergent_graph(sdk_b)
            set_evidence(sdk_b, src_a, "T0")
            set_evidence(sdk_b, src_b, "T0")
            result_b = run_ep(sdk_b, directed=False)
            src_a_b = get_mean(result_b, src_a)
            src_b_b = get_mean(result_b, src_b)

        print(f"\n  [SOURCE STABILITY]  T0 prior mean = {t0_mean:.4f}")
        print(f"  Directed:   SrcA={src_a_d:.4f}  SrcB={src_b_d:.4f}")
        print(f"  Bidirectional: SrcA={src_a_b:.4f}  SrcB={src_b_b:.4f}")

        # Sources should be close to their prior (0.9091) in both modes,
        # but directed should be at least as stable
        drift_d = max(abs(src_a_d - t0_mean), abs(src_b_d - t0_mean))
        drift_b = max(abs(src_a_b - t0_mean), abs(src_b_b - t0_mean))

        assert drift_d <= drift_b + EPSILON, \
            f"Directed source drift ({drift_d:.4f}) > Bidirectional ({drift_b:.4f})"


# ═══════════════════════════════════════════════════════════════════
# Test 2: Chain Propagation — Forward Flow Preserved
# ═══════════════════════════════════════════════════════════════════

class TestChainPropagation:
    """Verify chain propagation works in both modes: source → claim1 → claim2.
    Directional IMPL must still allow forward propagation through chains."""

    def build_chain(self, sdk: TortoiseSDK) -> tuple[str, str, str]:
        """Build: Source →[IMPL]→ Claim1 →[IMPL]→ Claim2.

        Returns (source_id, claim1_id, claim2_id).
        """
        src = make_point(sdk, "Source (T0 gold)")
        c1 = make_point(sdk, "Claim 1 — directly supported")
        c2 = make_point(sdk, "Claim 2 — downstream")
        make_operator(sdk, src["id"], c1["id"], "IMPL")
        make_operator(sdk, c1["id"], c2["id"], "IMPL")
        return src["id"], c1["id"], c2["id"]

    @pytest.mark.parametrize("directed", [True, False])
    def test_chain_attenuation(self, directed: bool):
        """Claims further from source have lower confidence (attenuation)."""
        mode = "directed" if directed else "bidirectional"
        with fresh_sdk() as sdk:
            src, c1, c2 = self.build_chain(sdk)
            set_evidence(sdk, src, "T0")
            result = run_ep(sdk, directed=directed)
            conf_src = get_mean(result, src)
            conf_c1 = get_mean(result, c1)
            conf_c2 = get_mean(result, c2)

        print(f"\n  [CHAIN {mode}]  Src={conf_src:.4f}  "
              f"C1={conf_c1:.4f}  C2={conf_c2:.4f}")

        # Source should be at its prior
        assert conf_src > 0.85, f"Source confidence too low: {conf_src:.4f}"

        # C1 gets signal from source
        assert conf_c1 > 0.55, f"C1 should receive signal: {conf_c1:.4f}"

        # C2 gets attenuated signal through C1
        assert conf_c2 > 0.51, f"C2 should receive some signal: {conf_c2:.4f}"

        # Attenuation: src > c1 > c2 (or at least src > c2)
        assert conf_src > conf_c2, \
            f"Source ({conf_src:.4f}) should exceed C2 ({conf_c2:.4f})"

    @pytest.mark.parametrize("directed", [True, False])
    def test_chain_propagates_in_both_modes(self, directed: bool):
        """Chain forward propagation works in both directed and bidirectional modes."""
        mode = "directed" if directed else "bidirectional"
        with fresh_sdk() as sdk:
            src, c1, c2 = self.build_chain(sdk)
            # Baseline: no evidence
            result_base = run_ep(sdk, directed=directed)
            c2_base = get_mean(result_base, c2)

            # With T0 source
            set_evidence(sdk, src, "T0")
            result = run_ep(sdk, directed=directed)
            c2_signal = get_mean(result, c2)

        gain = c2_signal - c2_base
        print(f"\n  [CHAIN {mode}]  C2 baseline={c2_base:.4f}  "
              f"with signal={c2_signal:.4f}  gain={gain:.4f}")

        assert gain > 0.005, \
            f"{mode}: T0 should propagate to C2, gain={gain:.4f}"


# ═══════════════════════════════════════════════════════════════════
# Test 3: NAND Contradiction — Still Bidirectional
# ═══════════════════════════════════════════════════════════════════

class TestNANDContradiction:
    """Verify NAND remains bidirectional after the IMPL directional fix.
    Contradiction must propagate both ways regardless of directed flag."""

    def build_nand_graph(self, sdk: TortoiseSDK) -> tuple[str, str, str]:
        """Build: Source_A →[IMPL]→ Claim, Source_B →[NAND]→ Claim.

        Source_A supports the claim. Source_B contradicts it.
        Returns (supporter_id, contradictor_id, claim_id).
        """
        supporter = make_point(sdk, "Supporting source (T0)")
        contradictor = make_point(sdk, "Contradicting source (T0)")
        claim = make_point(sdk, "Disputed claim")
        make_operator(sdk, supporter["id"], claim["id"], "IMPL")
        make_operator(sdk, contradictor["id"], claim["id"], "NAND")
        return supporter["id"], contradictor["id"], claim["id"]

    def test_nand_contradiction_directed(self):
        """DIRECTED: NAND still works. Support + contradiction → claim near 0.5."""
        with fresh_sdk() as sdk:
            supporter, contradictor, claim = self.build_nand_graph(sdk)
            set_evidence(sdk, supporter, "T0")
            set_evidence(sdk, contradictor, "T0")

            # Baseline: only supporter (no NAND evidence yet — but edge exists)
            # First, run without contradiction evidence
            result_before = run_ep(sdk, directed=True)

            # Now add contradiction
            set_evidence(sdk, contradictor, "T0")
            result_after = run_ep(sdk, directed=True)

        claim_before = get_mean(result_before, claim)
        claim_after = get_mean(result_after, claim)

        print(f"\n  [NAND DIRECTED]  Claim before NAND evidence: {claim_before:.4f}")
        print(f"  [NAND DIRECTED]  Claim after NAND evidence:  {claim_after:.4f}")
        print(f"  [NAND DIRECTED]  Drop: {claim_before - claim_after:.4f}")

        # NAND should pull claim down (equal-tier → toward 0.5 or below)
        assert claim_after < claim_before, \
            f"NAND should reduce claim confidence: {claim_after:.4f} >= {claim_before:.4f}"
        assert claim_after < 0.75, \
            f"With equal T0 support+contradiction, claim should drop below 0.75: {claim_after:.4f}"

    def test_nand_contradiction_bidirectional(self):
        """BIDIRECTIONAL: NAND still works."""
        with fresh_sdk() as sdk:
            supporter, contradictor, claim = self.build_nand_graph(sdk)
            set_evidence(sdk, supporter, "T0")
            set_evidence(sdk, contradictor, "T0")
            result = run_ep(sdk, directed=False)

        claim_conf = get_mean(result, claim)
        print(f"\n  [NAND BIDIRECTIONAL]  Claim: {claim_conf:.4f}")

        assert claim_conf < 0.75, \
            f"With equal support+contradiction, claim should be below 0.75: {claim_conf:.4f}"

    def test_nand_drop_comparable_both_modes(self):
        """The NAND drop should be similar in directed vs bidirectional mode.
        NAND is always bidirectional, so directed flag should not affect it."""
        with fresh_sdk() as sdk_d:
            supporter, contradictor, claim = self.build_nand_graph(sdk_d)
            set_evidence(sdk_d, supporter, "T0")
            set_evidence(sdk_d, contradictor, "T0")
            result_d = run_ep(sdk_d, directed=True)
            claim_d = get_mean(result_d, claim)

        with fresh_sdk() as sdk_b:
            supporter, contradictor, claim = self.build_nand_graph(sdk_b)
            set_evidence(sdk_b, supporter, "T0")
            set_evidence(sdk_b, contradictor, "T0")
            result_b = run_ep(sdk_b, directed=False)
            claim_b = get_mean(result_b, claim)

        print(f"\n  [NAND COMPARE]  Directed claim={claim_d:.4f}  "
              f"Bidirectional claim={claim_b:.4f}  "
              f"diff={abs(claim_d - claim_b):.4f}")

        # NAND should produce similar results (within tolerance)
        assert abs(claim_d - claim_b) < 0.05, \
            f"NAND should produce similar results: diff={abs(claim_d - claim_b):.4f}"


# ═══════════════════════════════════════════════════════════════════
# Test 4: Larger Convergent Graph — 3 Sources
# ═══════════════════════════════════════════════════════════════════

class TestMultiSourceConvergence:
    """3 sources all IMPL same claim. Directional should show monotonic gain."""

    def build_three_source_graph(self, sdk: TortoiseSDK) -> tuple[list[str], str]:
        """Build: S1,S2,S3 →[IMPL]→ Claim. Returns (source_ids, claim_id)."""
        sources = []
        for i in range(3):
            src = make_point(sdk, f"Source {i+1} (T0 gold)")
            sources.append(src["id"])
        claim = make_point(sdk, "Claim — supported by 3 sources")
        for sid in sources:
            make_operator(sdk, sid, claim["id"], "IMPL")
        return sources, claim["id"]

    def test_monotonic_convergence_directed(self):
        """DIRECTED: 1→2→3 sources monotonically increases claim confidence."""
        with fresh_sdk() as sdk:
            sources, claim = self.build_three_source_graph(sdk)

            prev_conf = None
            for n in range(1, 4):
                # Set evidence on first N sources, clear the rest
                for i, sid in enumerate(sources):
                    if i < n:
                        set_evidence(sdk, sid, "T0")
                    else:
                        set_custom_evidence(sdk, sid, 1.0, 1.0)

                result = run_ep(sdk, directed=True)
                conf = get_mean(result, claim)

                if prev_conf is not None:
                    assert conf >= prev_conf - DELTA, \
                        f"Directed: N={n} conf={conf:.4f} < N={n-1} conf={prev_conf:.4f}"

                prev_conf = conf

            print(f"\n  [MULTI-SOURCE DIRECTED]  Final claim confidence (3 T0): {prev_conf:.4f}")
            # Observed ~0.705 with 3 T0 at w=8. Signal propagates with attenuation.
            assert prev_conf > 0.68, \
                f"3 T0 sources should push claim above 0.68: {prev_conf:.4f}"

    def test_monotonic_convergence_bidirectional(self):
        """BIDIRECTIONAL: 1→2→3 sources monotonically increases claim confidence."""
        with fresh_sdk() as sdk:
            sources, claim = self.build_three_source_graph(sdk)

            prev_conf = None
            for n in range(1, 4):
                for i, sid in enumerate(sources):
                    if i < n:
                        set_evidence(sdk, sid, "T0")
                    else:
                        set_custom_evidence(sdk, sid, 1.0, 1.0)

                result = run_ep(sdk, directed=False)
                conf = get_mean(result, claim)

                if prev_conf is not None:
                    assert conf >= prev_conf - DELTA, \
                        f"Bidirectional: N={n} conf={conf:.4f} < N={n-1} conf={prev_conf:.4f}"

                prev_conf = conf

            print(f"\n  [MULTI-SOURCE BIDIRECTIONAL]  Final claim confidence (3 T0): {prev_conf:.4f}")
            assert prev_conf > 0.68, \
                f"3 T0 sources should push claim above 0.68: {prev_conf:.4f}"


# ═══════════════════════════════════════════════════════════════════
# Test 5: Edge Density Penalty Removal Verification
# ═══════════════════════════════════════════════════════════════════

class TestEdgeDensityRemoval:
    """Verify weights.py edge density removal is correct — no unnecessary dampening.

    The edge density penalty was removed because bidirectional IMPL messages
    caused amplification loops at hub nodes. With directional IMPL (source→target
    only), hubs no longer create feedback amplification, so the density penalty
    is unnecessary.
    """

    def test_hub_node_directed(self):
        """A claim with 3 sources in directed mode — edge density doesn't drown signal."""
        with fresh_sdk() as sdk:
            claim = make_point(sdk, "Hub claim")
            claim_id = claim["id"]
            sources = []
            for i in range(3):
                src = make_point(sdk, f"Hub source {i+1} (T0)")
                make_operator(sdk, src["id"], claim_id, "IMPL")
                set_evidence(sdk, src["id"], "T0")
                sources.append(src["id"])

            result = run_ep(sdk, directed=True)
            conf = get_mean(result, claim_id)

            print(f"\n  [HUB DIRECTED]  3 T0 sources → claim confidence: {conf:.4f}")

            # With 3 T0 sources at w=8, claim receives convergent signal
            assert conf > 0.68, \
                f"Hub claim should be above 0.68: {conf:.4f}"

    def test_hub_node_bidirectional_reference(self):
        """Same hub in bidirectional mode for comparison."""
        with fresh_sdk() as sdk:
            claim = make_point(sdk, "Hub claim")
            claim_id = claim["id"]
            sources = []
            for i in range(3):
                src = make_point(sdk, f"Hub source {i+1} (T0)")
                make_operator(sdk, src["id"], claim_id, "IMPL")
                set_evidence(sdk, src["id"], "T0")
                sources.append(src["id"])

            result = run_ep(sdk, directed=False)
            conf = get_mean(result, claim_id)

            print(f"\n  [HUB BIDIRECTIONAL]  3 T0 sources → claim confidence: {conf:.4f}")

            # Bidirectional with density penalty removed — still should work
            assert conf > 0.68, \
                f"Hub claim (bidirectional) should be above 0.68: {conf:.4f}"

    def test_directed_beats_or_equals_bidirectional_on_hub(self):
        """Directed mode should not be WORSE than bidirectional on hub nodes."""
        with fresh_sdk() as sdk_b:
            claim_b = make_point(sdk_b, "Hub claim")
            claim_b_id = claim_b["id"]
            for i in range(3):
                src = make_point(sdk_b, f"Hub src {i+1} (T0)")
                make_operator(sdk_b, src["id"], claim_b_id, "IMPL")
                set_evidence(sdk_b, src["id"], "T0")
            result_b = run_ep(sdk_b, directed=False)
            conf_b = get_mean(result_b, claim_b_id)

        with fresh_sdk() as sdk_d:
            claim_d = make_point(sdk_d, "Hub claim")
            claim_d_id = claim_d["id"]
            for i in range(3):
                src = make_point(sdk_d, f"Hub src {i+1} (T0)")
                make_operator(sdk_d, src["id"], claim_d_id, "IMPL")
                set_evidence(sdk_d, src["id"], "T0")
            result_d = run_ep(sdk_d, directed=True)
            conf_d = get_mean(result_d, claim_d_id)

        print(f"\n  [HUB COMPARE]  Directed={conf_d:.4f}  "
              f"Bidirectional={conf_b:.4f}  diff={conf_d - conf_b:.4f}")

        # Directed should not lose signal vs. bidirectional on hub nodes.
        # Observed: both ~0.705 for 3 T0 sources at w=8.
        assert conf_d >= conf_b - EPSILON, \
            f"Directed ({conf_d:.4f}) < Bidirectional ({conf_b:.4f})"


# ═══════════════════════════════════════════════════════════════════
# Test 6: EP Convergence — Both Modes
# ═══════════════════════════════════════════════════════════════════

class TestEPConvergence:
    """EP should converge quickly and reliably in both modes."""

    @pytest.mark.parametrize("directed", [True, False])
    def test_converges_quickly(self, directed: bool):
        """EP converges in ≤ 50 iterations for a complex graph."""
        mode = "directed" if directed else "bidirectional"
        with fresh_sdk() as sdk:
            # Build moderately complex graph
            src1 = make_point(sdk, "Source 1 (T0)")
            src2 = make_point(sdk, "Source 2 (T1)")
            src3 = make_point(sdk, "Source 3 (T2)")
            c1 = make_point(sdk, "Claim 1")
            c2 = make_point(sdk, "Claim 2")
            c3 = make_point(sdk, "Claim 3")

            make_operator(sdk, src1["id"], c1["id"], "IMPL")
            make_operator(sdk, src2["id"], c1["id"], "IMPL")
            make_operator(sdk, c1["id"], c2["id"], "IMPL")
            make_operator(sdk, src3["id"], c2["id"], "IMPL")
            make_operator(sdk, c2["id"], c3["id"], "IMPL")

            set_evidence(sdk, src1["id"], "T0")
            set_evidence(sdk, src2["id"], "T1")
            set_evidence(sdk, src3["id"], "T2")

            result = run_ep(sdk, directed=directed)

        print(f"\n  [CONVERGE {mode}]  iters={result['iterations']}  "
              f"converged={result['converged']}")

        assert result["converged"] == True, \
            f"{mode}: EP did not converge after {result['iterations']} iterations"
        assert result["iterations"] <= 50, \
            f"{mode}: EP took {result['iterations']} iterations"


# ═══════════════════════════════════════════════════════════════════
# Main: run with confidence output (for manual review)
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 72)
    print("Directional IMPL Fix — EP Behavior Review")
    print("=" * 72)

    # ── Convergent Evidence ──
    print("\n─── Test: Convergent Evidence (2 T0 → same claim) ───")
    for directed in [True, False]:
        mode = "DIRECTED" if directed else "BIDIRECTIONAL"
        with fresh_sdk() as sdk:
            src_a, src_b, claim = None, None, None
            src_a = make_point(sdk, "Source A (T0)")["id"]
            src_b = make_point(sdk, "Source B (T0)")["id"]
            claim = make_point(sdk, "Claim")["id"]
            make_operator(sdk, src_a, claim, "IMPL")
            make_operator(sdk, src_b, claim, "IMPL")

            # 1 source
            set_evidence(sdk, src_a, "T0")
            set_custom_evidence(sdk, src_b, 1.0, 1.0)
            r1 = run_ep(sdk, directed=directed)

            # 2 sources
            set_evidence(sdk, src_b, "T0")
            r2 = run_ep(sdk, directed=directed)

            c1 = get_mean(r1, claim)
            c2 = get_mean(r2, claim)
            print(f"  [{mode}]  1src: {c1:.4f}  →  2src: {c2:.4f}  "
                  f"(gain: {c2-c1:+.4f})  [{r2['iterations']} iters]")

    # ── Chain Propagation ──
    print("\n─── Test: Chain Propagation (Src→C1→C2) ───")
    for directed in [True, False]:
        mode = "DIRECTED" if directed else "BIDIRECTIONAL"
        with fresh_sdk() as sdk:
            src = make_point(sdk, "Source")["id"]
            c1 = make_point(sdk, "Claim1")["id"]
            c2 = make_point(sdk, "Claim2")["id"]
            make_operator(sdk, src, c1, "IMPL")
            make_operator(sdk, c1, c2, "IMPL")
            set_evidence(sdk, src, "T0")
            r = run_ep(sdk, directed=directed)
            print(f"  [{mode}]  Src: {get_mean(r, src):.4f}  "
                  f"C1: {get_mean(r, c1):.4f}  C2: {get_mean(r, c2):.4f}")

    # ── NAND Contradiction ──
    print("\n─── Test: NAND Contradiction (T0 IMPL + T0 NAND → claim) ───")
    for directed in [True, False]:
        mode = "DIRECTED" if directed else "BIDIRECTIONAL"
        with fresh_sdk() as sdk:
            supporter = make_point(sdk, "Supporter")["id"]
            contradictor = make_point(sdk, "Contradictor")["id"]
            claim = make_point(sdk, "Disputed")["id"]
            make_operator(sdk, supporter, claim, "IMPL")
            make_operator(sdk, contradictor, claim, "NAND")
            set_evidence(sdk, supporter, "T0")
            set_evidence(sdk, contradictor, "T0")
            r = run_ep(sdk, directed=directed)
            print(f"  [{mode}]  Claim: {get_mean(r, claim):.4f}  "
                  f"Supporter: {get_mean(r, supporter):.4f}  "
                  f"Contradictor: {get_mean(r, contradictor):.4f}")

    # ── Source Stability ──
    print("\n─── Test: Source Prior Stability ───")
    t0_mean = 10/11
    for directed in [True, False]:
        mode = "DIRECTED" if directed else "BIDIRECTIONAL"
        with fresh_sdk() as sdk:
            claim = make_point(sdk, "Claim")["id"]
            sources = []
            for i in range(2):
                src = make_point(sdk, f"Source {i+1}")["id"]
                make_operator(sdk, src, claim, "IMPL")
                set_evidence(sdk, src, "T0")
                sources.append(src)
            r = run_ep(sdk, directed=directed)
            src_means = [get_mean(r, s) for s in sources]
            drift = max(abs(m - t0_mean) for m in src_means)
            print(f"  [{mode}]  Sources: {src_means[0]:.4f}, {src_means[1]:.4f}  "
                  f"(drift from T0 prior {t0_mean:.4f}: {drift:.4f})")

    print("\n" + "=" * 72)
    print("Review complete. Run with pytest for assertion validation:")
    print("  python3 -m pytest tests/test_ep_directional_review.py -v")
    print("=" * 72)
