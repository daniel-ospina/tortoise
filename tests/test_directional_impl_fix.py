#!/usr/bin/env python3
"""Test directional IMPL fix for Tortoise EP (#6841).

Validates:
  1. Convergent: 2 T0 sources → same claim produces HIGHER confidence
     than 1 source (verifies directional IMPL fix + edge density penalty removed)
  2. Chain: source → middle → conclusion properly attenuates
  3. NAND: bidirectional — contradiction source and target both affected
  4. directed vs. undirected comparison: regressions and improvements

MUST run against a live FalkorDB. Uses fresh isolated namespaces.
"""
from __future__ import annotations

import os
import sys
import pytest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Isolated test graph — never the production graph. Set inside the probe
# and per-test via autouse fixture — NEVER at module level (#493: a
# collection-time env set leaked docker:// into the whole suite, breaking
# tests that read TORTOISE_DB_URI at call time, e.g. test_agent_signup).
_TEST_URI = "docker://:falkordb@localhost:6379/tortoise_test_dir_impl_fix"

from tortoise.sdk import TortoiseSDK
from tortoise.ep import TortoiseEP
from tortoise.weights import compute_operator_weight

# Requires live FalkorDB (Docker). Skip gracefully when unavailable so the
# no-Docker embedded suite stays green (AGENTS.md). Mirrors the probe pattern
# in tests/test_integration_search.py.
FALKORDB_AVAILABLE = False
try:
    from tortoise.sdk import TortoiseSDK as _ProbeSDK
    _old_uri = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = _TEST_URI
    _probe = _ProbeSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    FALKORDB_AVAILABLE = False
finally:
    if _old_uri is not None:
        os.environ["TORTOISE_DB_URI"] = _old_uri
    else:
        os.environ.pop("TORTOISE_DB_URI", None)


@pytest.fixture(autouse=True)
def _set_test_uri(monkeypatch):
    """Point SDK constructions at the isolated docker test graph per-test."""
    monkeypatch.setenv("TORTOISE_DB_URI", _TEST_URI)

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")



# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

TIER_MAP = {
    "T0": (10, 1), "T1": (5, 1), "T2": (3, 1), "T3": (2, 1), "T4": (1.1, 1),
}

EPSILON = 0.02


def fresh_sdk(graph_name=None):
    ns = graph_name or f"test_dirfix_{uuid.uuid4().hex[:8]}"
    return TortoiseSDK(db_path=None, namespace=ns)


def run_ep_directed(sdk):
    """Run TortoiseEP. Directionality is now controlled per-operator via
    the `direction` property set at create_operator time (ONTOLOGY v3.1 #189).
    IMPL defaults to bidirectional; pass direction="unidirectional" for
    source→target-only propagation."""
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (o:Point) WHERE o.is_operator = true RETURN o.id"
    ).result_set
    op_ids = [r[0] for r in rows] if rows else []

    ev_rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in ev_rows} if ev_rows else {}

    # directed flag is no longer wired — IMPL always directional, NAND always bidirectional.
    # hasPart-labeled IMPL operators are bidirectional (handled by _update_factor).
    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3,
                    evidence=evidence)
    ep.run(op_ids, max_hops=2)
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.confidence IS NOT NULL RETURN n.id, n.confidence, n.ep_alpha, n.ep_beta"
    ).result_set
    return {
        r[0]: {"mean": r[1], "alpha": r[2], "beta": r[3]} for r in rows
    } if rows else {}


def get_conf(result, point_id):
    return result.get(point_id, {"mean": 0.5})["mean"]


def make_point(sdk, content, kind="statement"):
    return sdk.create_point(kind, content)


def make_operator(sdk, source_id, target_id, op_type="IMPL", direction=None):
    kwargs = {}
    if direction is not None:
        kwargs["direction"] = direction
    return sdk.create_operator(op_type, source_id, [target_id], **kwargs)


# ═══════════════════════════════════════════════════════════════════
# TEST 1: Convergent — 2 T0 sources > 1 T0 source
# ═══════════════════════════════════════════════════════════════════

def test_two_sources_higher_than_one():
    """
    Core test: 2 T0 sources IMPL same claim should produce HIGHER
    confidence than 1 source. This validates the directional IMPL fix
    and edge density penalty removal.

    Before the fix (undirected), bidirectional back-messages from the
    claim to each source would dilute the convergent signal, and the
    edge density penalty in weights.py would further reduce per-operator
    weight for hub nodes. Together these caused 2 sources to produce
    ≤ 1 source confidence.

    After the fix (directed), IMPL messages only flow source→target,
    so each source boosts the claim without back-coupling. The density
    penalty is also removed.
    """
    print()
    print("=" * 70)
    print("  TEST 1: 2 T0 Sources > 1 T0 Source (Convergent)")
    print("=" * 70)

    # ── Directed mode ──────────────────────────────────────────
    print("\n  ── Directed mode (IMPL source→target only) ──")

    sdk = fresh_sdk()
    source_a = make_point(sdk, "T0 Source A — clinical trial result")
    source_b = make_point(sdk, "T0 Source B — independent replication")
    claim = make_point(sdk, "Claim: The treatment is effective")

    sdk.set_point_baseline(source_a["id"], *TIER_MAP["T0"])
    sdk.set_point_baseline(source_b["id"], *TIER_MAP["T0"])

    op_a = make_operator(sdk, source_a["id"], claim["id"], "IMPL", direction="unidirectional")
    op_b = make_operator(sdk, source_b["id"], claim["id"], "IMPL", direction="unidirectional")

    # Check weights
    proj = sdk._get_proj()
    w_a = compute_operator_weight(proj, op_a["id"])
    w_b = compute_operator_weight(proj, op_b["id"])
    print(f"  Operator weights: op_a={w_a:.4f}, op_b={w_b:.4f}")
    print(f"  Edge density penalty check: both should be ~1.0 (removed)")
    assert abs(w_a - 1.0) < 0.01, f"op_a weight {w_a:.4f} — density penalty should be removed!"
    assert abs(w_b - 1.0) < 0.01, f"op_b weight {w_b:.4f} — density penalty should be removed!"

    # Run EP with both sources (directed)
    result_both = run_ep_directed(sdk)
    claim_both = result_both[claim["id"]]
    src_a_both = result_both[source_a["id"]]
    src_b_both = result_both[source_b["id"]]

    print(f"\n  Both sources present:")
    print(f"    Source A:  mean={src_a_both['mean']:.4f}  (α={src_a_both['alpha']:.2f}, β={src_a_both['beta']:.2f})")
    print(f"    Source B:  mean={src_b_both['mean']:.4f}  (α={src_b_both['alpha']:.2f}, β={src_b_both['beta']:.2f})")
    print(f"    Claim:     mean={claim_both['mean']:.4f}  (α={claim_both['alpha']:.2f}, β={claim_both['beta']:.2f})")

    # Remove source B's operator, re-run
    sdk.delete_point(op_b["id"])
    result_a_only = run_ep_directed(sdk)
    claim_a = result_a_only[claim["id"]]

    print(f"\n  Source A only:")
    print(f"    Claim:     mean={claim_a['mean']:.4f}  (α={claim_a['alpha']:.2f}, β={claim_a['beta']:.2f})")

    drop = claim_both["mean"] - claim_a["mean"]
    print(f"\n  Drop from removing B: {drop:+.4f}")

    # CRITICAL ASSERTIONS
    assert drop > 0, \
        f"❌ CONFIDENCE DROPPED: 2 sources ({claim_both['mean']:.4f}) ≤ 1 source ({claim_a['mean']:.4f})"
    print(f"  ✅ 2 sources ({claim_both['mean']:.4f}) > 1 source ({claim_a['mean']:.4f})")

    # Also verify the claim is materially above a baseline (uniform) prior.
    # NOTE: with directional IMPL the claim settles below its T0 sources'
    # own confidence (~0.78 vs source ~0.91) — that is correct EP behavior:
    # sources are evidence, not certainty, and the cavity excludes each
    # source's own message. The regression guard is `drop > 0` above (more
    # support → more confidence), not exceeding the sources' prior (#86).
    assert claim_both["mean"] > 0.5, \
        f"Claim should be above uniform prior: got {claim_both['mean']:.4f}"
    print(f"  ✅ Claim with 2 T0 sources ({claim_both['mean']:.4f}) > uniform prior (0.5)")

    sdk.close()

    # ── Undirected mode (legacy, for comparison) ────────
    print("\n  ── Undirected mode (bidirectional IMPL, for comparison) ──")

    sdk2 = fresh_sdk()
    s_a = make_point(sdk2, "T0 Source A")
    s_b = make_point(sdk2, "T0 Source B")
    cl = make_point(sdk2, "Claim")
    sdk2.set_point_baseline(s_a["id"], *TIER_MAP["T0"])
    sdk2.set_point_baseline(s_b["id"], *TIER_MAP["T0"])
    op1 = make_operator(sdk2, s_a["id"], cl["id"], "IMPL")
    op2 = make_operator(sdk2, s_b["id"], cl["id"], "IMPL")

    result_undirected_both = run_ep_directed(sdk2)
    cl_ub = result_undirected_both[cl["id"]]

    sdk2.delete_point(op2["id"])
    result_undirected_a = run_ep_directed(sdk2)
    cl_ua = result_undirected_a[cl["id"]]

    drop_undir = cl_ub["mean"] - cl_ua["mean"]
    print(f"  Both sources (undirected): {cl_ub['mean']:.4f}")
    print(f"  Source A only (undirected): {cl_ua['mean']:.4f}")
    print(f"  Drop (undirected): {drop_undir:+.4f}")

    # The directed mode should produce a LARGER or equal drop than undirected
    # (directed prevents back-coupling that dilutes convergent evidence)
    if drop > drop_undir + 0.001:
        print(f"  ✅ Directed drop ({drop:+.4f}) > undirected drop ({drop_undir:+.4f}) — improvement confirmed")
    else:
        print(f"  ℹ️  Directed drop ({drop:+.4f}) ≈ undirected drop ({drop_undir:+.4f})")

    sdk2.close()

    return {
        "claim_both": claim_both,
        "claim_a": claim_a,
        "drop": drop,
        "drop_undirected": drop_undir,
    }


# ═══════════════════════════════════════════════════════════════════
# TEST 2: Chain Propagation
# ═══════════════════════════════════════════════════════════════════

def test_chain_propagation():
    """
    Chain: T0 → Middle → Conclusion.

    Verify:
    - Attenuation: source > middle > conclusion
    - Directed mode preserves chain behavior
    - Middle is not over-penalized (no edge density penalty)
    """
    print()
    print("=" * 70)
    print("  TEST 2: Chain Propagation — T0 → Middle → Conclusion")
    print("=" * 70)

    for mode_name, directed in [("Directed", True), ("Undirected", False)]:
        print(f"\n  ── {mode_name} mode ──")

        sdk = fresh_sdk()
        source = make_point(sdk, "T0 Source: meta-analysis")
        middle = make_point(sdk, "Middle: if X then mechanism Y")
        conclusion = make_point(sdk, "Conclusion: outcome Z is probable")

        sdk.set_point_baseline(source["id"], *TIER_MAP["T0"])

        impl_direction = "unidirectional" if directed else "bidirectional"
        op1 = make_operator(sdk, source["id"], middle["id"], "IMPL", direction=impl_direction)
        op2 = make_operator(sdk, middle["id"], conclusion["id"], "IMPL", direction=impl_direction)

        # Check weights
        proj = sdk._get_proj()
        w1 = compute_operator_weight(proj, op1["id"])
        w2 = compute_operator_weight(proj, op2["id"])
        print(f"  op1 (src→mid) weight: {w1:.4f}")
        print(f"  op2 (mid→con) weight: {w2:.4f}")

        # Both weights should be ~1.0 — no edge density penalty
        assert abs(w1 - 1.0) < 0.01, \
            f"op1 weight {w1:.4f} — density penalty wrongly applied!"
        assert abs(w2 - 1.0) < 0.01, \
            f"op2 weight {w2:.4f} — density penalty wrongly applied!"

        result = run_ep_directed(sdk)

        sc = result[source["id"]]
        mc = result[middle["id"]]
        cc = result[conclusion["id"]]

        print(f"  Source:     mean={sc['mean']:.4f}  α={sc['alpha']:.2f}  β={sc['beta']:.2f}")
        print(f"  Middle:     mean={mc['mean']:.4f}  α={mc['alpha']:.2f}  β={mc['beta']:.2f}")
        print(f"  Conclusion: mean={cc['mean']:.4f}  α={cc['alpha']:.2f}  β={cc['beta']:.2f}")

        # Attenuation: source > middle > conclusion
        assert sc["mean"] > mc["mean"] > cc["mean"], \
            f"❌ Attenuation broken: src={sc['mean']:.4f} > mid={mc['mean']:.4f} > con={cc['mean']:.4f}"
        print(f"  ✅ Attenuation: {sc['mean']:.4f} > {mc['mean']:.4f} > {cc['mean']:.4f}")

        # Middle should be above baseline (receives signal from source)
        assert mc["mean"] > 0.55, \
            f"❌ Middle too weak: {mc['mean']:.4f} (should receive source signal)"
        print(f"  ✅ Middle above baseline: {mc['mean']:.4f} > 0.55")

        # Conclusion should be above baseline
        assert cc["mean"] > 0.50 + EPSILON / 2, \
            f"❌ Conclusion not above baseline: {cc['mean']:.4f}"
        print(f"  ✅ Conclusion above baseline: {cc['mean']:.4f} > 0.50")

        sdk.close()


# ═══════════════════════════════════════════════════════════════════
# TEST 3: NAND — Bidirectional Contradiction
# ═══════════════════════════════════════════════════════════════════

def test_nand_bidirectional():
    """
    NAND must remain bidirectional in both directed and undirected modes.

    T0 source IMPL claim, T0 defeater NAND claim.
    In directed mode:
    - IMPL is source→target only (source not affected by claim messages)
    - NAND is bidirectional (both nodes mutually contradict)
    """
    print()
    print("=" * 70)
    print("  TEST 3: NAND Bidirectional — Contradiction")
    print("=" * 70)

    for mode_name, directed in [("Directed", True), ("Undirected", False)]:
        print(f"\n  ── {mode_name} mode ──")

        sdk = fresh_sdk()
        source = make_point(sdk, "T0 Source: study finds drug effective")
        defeater = make_point(sdk, "T0 Defeater: review finds non-replicable")
        claim = make_point(sdk, "Claim: drug is effective")

        sdk.set_point_baseline(source["id"], *TIER_MAP["T0"])
        sdk.set_point_baseline(defeater["id"], *TIER_MAP["T0"])

        impl_direction = "unidirectional" if directed else "bidirectional"
        op_impl = make_operator(sdk, source["id"], claim["id"], "IMPL", direction=impl_direction)
        op_nand = make_operator(sdk, defeater["id"], claim["id"], "NAND")

        result = run_ep_directed(sdk)
        sc = result[source["id"]]
        dc = result[defeater["id"]]
        cc = result[claim["id"]]

        print(f"  Source:    mean={sc['mean']:.4f}  α={sc['alpha']:.2f}  β={sc['beta']:.2f}")
        print(f"  Defeater:  mean={dc['mean']:.4f}  α={dc['alpha']:.2f}  β={dc['beta']:.2f}")
        print(f"  Claim:     mean={cc['mean']:.4f}  α={cc['alpha']:.2f}  β={cc['beta']:.2f}")

        # Claim should be contested away from the strong-support fixed point.
        # With directional IMPL the T0 source pushes the claim strongly, and
        # the NAND contradiction potential phi_nand = exp(-w*ca*cb) penalizes
        # agreement — at weight=1.0 the T0-vs-T0 coupling is ~0.44, which
        # partially counters the IMPL support. The claim settles below the
        # pure-support case and above the pure-contradiction case (#86).
        assert 0.50 < cc["mean"] < 0.85, \
            f"❌ Claim outside contested range: {cc['mean']:.4f}"
        print(f"  ✅ Claim contested by NAND: {cc['mean']:.4f} (in 0.50-0.85)")

        # Source should remain high (IMPL is directional, not affected by claim's low confidence)
        assert sc["mean"] > 0.85, \
            f"❌ Source pulled down: {sc['mean']:.4f}"
        print(f"  ✅ Source remains high: {sc['mean']:.4f}")

        # ATTN — KEY CHECK: In undirected mode, source might get some back-coupling
        # from the claim. In directed mode, source should be strictly at T0 prior.
        if directed:
            assert abs(sc["mean"] - 0.9091) < 0.02, \
                f"⚠️  Directed: source deviated from T0 prior: {sc['mean']:.4f} vs 0.9091"
            print(f"  ✅ Directed: source at T0 prior ({sc['mean']:.4f} ≈ 0.9091)")
        else:
            # In undirected mode, source might deviate somewhat due to back-coupling
            print(f"  ℹ️  Undirected: source mean={sc['mean']:.4f} (may differ from T0 prior)")

        # Defeater should also remain high (NAND is bidirectional, but defeater starts strong)
        # Even with bidirectional NAND, the defeater starts at T0 and the claim starts
        # at uniform — the nett effect should keep defeater high
        assert dc["mean"] > 0.70, \
            f"❌ Defeater pulled too low: {dc['mean']:.4f}"
        print(f"  ✅ Defeater remains reasonable: {dc['mean']:.4f}")

        sdk.close()


# ═══════════════════════════════════════════════════════════════════
# TEST 4: Directed vs Undirected — Source Isolation
# ═══════════════════════════════════════════════════════════════════

def test_source_isolation():
    """
    In directed mode, a T0 source IMPL a claim should have its confidence
    exactly equal to its prior (no back-coupling). In undirected mode,
    back-messages from the claim can affect the source.

    This test validates the core property of directed IMPL: sources are
    unaffected by downstream messages.
    """
    print()
    print("=" * 70)
    print("  TEST 4: Source Isolation — Directed vs Undirected")
    print("=" * 70)

    # ── Directed ──────────────────────────────────────────────
    print("\n  ── Directed mode — source should equal T0 prior ──")
    sdk = fresh_sdk()
    source = make_point(sdk, "T0 Source: evidence")
    claim = make_point(sdk, "Claim: conclusion")
    sdk.set_point_baseline(source["id"], *TIER_MAP["T0"])
    op = make_operator(sdk, source["id"], claim["id"], "IMPL", direction="unidirectional")

    result = run_ep_directed(sdk)
    sc = result[source["id"]]
    cc = result[claim["id"]]

    print(f"  Source: mean={sc['mean']:.4f} (expected ~0.9091)")
    print(f"  Claim:  mean={cc['mean']:.4f}")

    # Source should be at T0 prior in directed mode
    assert abs(sc["mean"] - 0.9091) < 0.02, \
        f"❌ Source deviated: {sc['mean']:.4f} vs expected 0.9091"
    print(f"  ✅ Source at T0 prior: {sc['mean']:.4f}")

    sdk.close()

    # ── Undirected ────────────────────────────────────────────
    print("\n  ── Undirected mode — source may deviate ──")
    sdk2 = fresh_sdk()
    s2 = make_point(sdk2, "T0 Source: evidence")
    c2 = make_point(sdk2, "Claim: conclusion")
    sdk2.set_point_baseline(s2["id"], *TIER_MAP["T0"])
    op2 = make_operator(sdk2, s2["id"], c2["id"], "IMPL", direction="bidirectional")

    result2 = run_ep_directed(sdk2)
    sc2 = result2[s2["id"]]
    cc2 = result2[c2["id"]]

    print(f"  Source: mean={sc2['mean']:.4f}")
    print(f"  Claim:  mean={cc2['mean']:.4f}")

    sdk2.close()
    print(f"  ℹ️  Undirected source may differ from T0 prior due to back-coupling")


# ═══════════════════════════════════════════════════════════════════
# TEST 5: Edge Density Penalty — Explicitly Removed
# ═══════════════════════════════════════════════════════════════════

def test_edge_density_penalty_removed():
    """
    Verify that weights.compute_operator_weight applies NO edge density
    penalty regardless of how many edges are incident to the target claim.

    The old penalty was `1 / max(log2(n_edges + 1), 1.0)` which caused
    2-operator hub nodes to be penalized ~0.63× each. With directional
    IMPL, this penalty is unnecessary and has been removed.
    """
    print()
    print("=" * 70)
    print("  TEST 5: Edge Density Penalty — Should Be Removed")
    print("=" * 70)

    # Test with varying numbers of operators converging on the same claim
    for n_sources in [1, 2, 5, 10]:
        sdk = fresh_sdk()
        sources = []
        claim = make_point(sdk, f"Claim with {n_sources} converging operators")
        for i in range(n_sources):
            src = make_point(sdk, f"Source {i+1}")
            sdk.set_point_baseline(src["id"], *TIER_MAP["T4"])  # weak sources
            op = make_operator(sdk, src["id"], claim["id"], "IMPL")
            sources.append((src, op))

        proj = sdk._get_proj()
        weights = []
        for _, op in sources:
            w = compute_operator_weight(proj, op["id"])
            weights.append(w)

        print(f"  {n_sources} sources: weights = {[f'{w:.4f}' for w in weights]}")
        total = sum(weights)
        print(f"      total effective weight: {total:.4f} "
              f"{'✅' if total > n_sources * 0.90 else '⚠️'}")

        for i, w in enumerate(weights):
            assert abs(w - 1.0) < 0.02, \
                f"❌ Source {i+1} weight {w:.4f} — penalty still active!"

        sdk.close()

    print(f"\n  ✅ All operator weights are ~1.0 — edge density penalty confirmed removed")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    failures = 0

    tests = [
        ("Convergent (2 > 1)", test_two_sources_higher_than_one),
        ("Chain Propagation", test_chain_propagation),
        ("NAND Bidirectional", test_nand_bidirectional),
        ("Source Isolation", test_source_isolation),
        ("Edge Density Penalty Removed", test_edge_density_penalty_removed),
    ]

    for name, fn in tests:
        try:
            fn()
        except AssertionError as e:
            print(f"\n  ❌ {name} FAILED: {e}")
            failures += 1
        except Exception as e:
            print(f"\n  ❌ {name} ERROR: {e}")
            import traceback
            traceback.print_exc()
            failures += 1

    # ── Summary ──
    print()
    print("=" * 70)
    if failures == 0:
        print("  🎉 ALL 5 TESTS PASSED — Directional IMPL fix is sound")
    else:
        print(f"  ⚠️  {failures} TEST(S) FAILED")
    print("=" * 70)
    print()
