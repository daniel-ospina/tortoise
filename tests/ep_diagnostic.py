#!/usr/bin/env python3
"""Diagnostic: trace EP messages for convergent (single vs two source) case.

Investigates why two T0 sources produce LOWER confidence than one source.
Hypothesis: edge density penalty (1/log₂(n+1)) reduces per-operator weight
so aggressively that 2 × 0.631 < 1 × 1.0 effective coupling.
"""

import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TORTOISE_DB_URI"] = "docker://:falkordb@localhost:6379/tortoise"

from tortoise.sdk import TortoiseSDK
from tortoise.weights import compute_operator_weight


def analyze_density_penalty():
    """Analyze how edge density penalty affects effective weight."""
    print("=" * 70)
    print("  EDGE DENSITY PENALTY ANALYSIS")
    print("=" * 70)

    import math
    for n in range(1, 11):
        factor = 1.0 / max(math.log2(n + 1), 1.0)
        total = n * factor
        print(f"  {n} edges → per-edge w={factor:.4f}, total effective={total:.4f} "
              f"({'✅ >1.0' if total > 1.0 else '⚠️ <1.0 (2 sources < 1 source!)' if n == 2 and total < 1.0 else ''}{'⚠️ 1→2 drops!' if n == 2 else ''})")

    print()
    print("  For n=2: each operator weight = 1/log₂(3) = 0.631")
    print("  Total effective = 2 × 0.631 = 1.262")
    print("  But EP messages are NON-linear in weight — phi = exp(w * ca * cb)")
    print("  2 weak messages (w=0.631) ≠ 1 strong message (w=1.262)")


def trace_convergent_ep():
    """Trace EP messages for convergent scenario with edge analysis."""
    print()
    print("=" * 70)
    print("  CONVERGENT CASE — OPERATOR WEIGHT TRACE")
    print("=" * 70)

    sdk = TortoiseSDK(namespace="ep_diag_conv")

    source_a = sdk.create_point("observation", "Source A", credibility="T0")
    source_b = sdk.create_point("observation", "Source B", credibility="T0")
    claim = sdk.create_point("hypothesis", "Claim")

    op_a = sdk.create_operator("IMPL", source_a["id"], [claim["id"]])
    op_b = sdk.create_operator("IMPL", source_b["id"], [claim["id"]])

    proj = sdk._get_proj()

    # Check edge counts
    for name, nid in [("SourceA", source_a["id"]), ("SourceB", source_b["id"]), ("Claim", claim["id"])]:
        edge_count = proj.g.query(
            "MATCH (c:Point {id:$cid})-[r:IMPL|NAND]-() RETURN count(r)",
            params={"cid": nid},
        ).result_set[0][0]
        print(f"  {name} ({nid[:12]}...): {edge_count} IMPL/NAND edges")

    w_a = compute_operator_weight(proj, op_a["id"])
    w_b = compute_operator_weight(proj, op_b["id"])
    print(f"\n  opA weight: {w_a:.4f}")
    print(f"  opB weight: {w_b:.4f}")
    print(f"  Total effective weight: {w_a + w_b:.4f}")

    # Run EP
    result = sdk.compute_confidence()
    print(f"\n  EP: {result['iterations']} iters, converged={result['converged']}")

    claim_c = sdk.get_confidence(claim["id"])
    src_a_c = sdk.get_confidence(source_a["id"])
    src_b_c = sdk.get_confidence(source_b["id"])
    print(f"  Claim: {claim_c['mean']:.4f}")
    print(f"  SrcA:  {src_a_c['mean']:.4f}")
    print(f"  SrcB:  {src_b_c['mean']:.4f}")

    # Now delete opB and re-run
    sdk.delete_point(op_b["id"])
    print(f"\n  ── After removing opB ──")
    result2 = sdk.compute_confidence()
    claim_c2 = sdk.get_confidence(claim["id"])
    print(f"  EP: {result2['iterations']} iters, converged={result2['converged']}")
    print(f"  Claim: {claim_c2['mean']:.4f}")
    print(f"  Change: {claim_c2['mean'] - claim_c['mean']:+.4f} {'(⬆️ with FEWER sources!)' if claim_c2['mean'] > claim_c['mean'] else ''}")

    sdk.close()


def trace_bottleneck():
    """Trace linked premises to see why bottleneck < premises."""
    print()
    print("=" * 70)
    print("  LINKED PREMISES — BOTTLENECK ACCUMULATION TRACE")
    print("=" * 70)

    sdk = TortoiseSDK(namespace="ep_diag_btl")

    prem_a = sdk.create_point("observation", "Premise A", credibility="T2")
    prem_b = sdk.create_point("observation", "Premise B", credibility="T2")
    prem_c = sdk.create_point("observation", "Premise C", credibility="T2")
    bottleneck = sdk.create_point("statement", "Bottleneck")
    conclusion = sdk.create_point("hypothesis", "Conclusion")

    op_a = sdk.create_operator("IMPL", prem_a["id"], [bottleneck["id"]])
    op_b = sdk.create_operator("IMPL", prem_b["id"], [bottleneck["id"]])
    op_c = sdk.create_operator("IMPL", prem_c["id"], [bottleneck["id"]])
    op_btl = sdk.create_operator("IMPL", bottleneck["id"], [conclusion["id"]])

    proj = sdk._get_proj()

    for name, nid in [("PremA", prem_a["id"]), ("PremB", prem_b["id"]),
                       ("PremC", prem_c["id"]), ("Btlneck", bottleneck["id"]),
                       ("Conclusion", conclusion["id"])]:
        ec = proj.g.query(
            "MATCH (c:Point {id:$cid})-[r:IMPL|NAND]-() RETURN count(r)",
            params={"cid": nid},
        ).result_set[0][0]
        print(f"  {name}: {ec} edges")

    for name, op_id in [("opA", op_a["id"]), ("opB", op_b["id"]),
                         ("opC", op_c["id"]), ("opBtl", op_btl["id"])]:
        w = compute_operator_weight(proj, op_id)
        print(f"  {name} weight: {w:.4f}")

    total_prem_weight = sum(compute_operator_weight(proj, o) for o in [op_a["id"], op_b["id"], op_c["id"]])
    print(f"\n  Total premise→bottleneck effective weight: {total_prem_weight:.4f}")
    print(f"  Each T2 premise prior: Beta(3,1) → mean 0.7500")
    print(f"  Bottleneck starts at Beta(1,1) → mean 0.5000")
    print(f"  With w=0.5 per edge, each message is φ=exp(0.5*cp*cb)")
    print(f"  Expected: bottleneck should accumulate ABOVE 0.75 with 3 T2 sources")

    result = sdk.compute_confidence()
    print(f"\n  EP result: {result['iterations']} iters")

    for name, nid in [("PremA", prem_a["id"]), ("Btlneck", bottleneck["id"]),
                       ("Conclusion", conclusion["id"])]:
        c = sdk.get_confidence(nid)
        print(f"  {name}: {c['mean']:.4f} (α={c['alpha']:.2f}, β={c['beta']:.2f})")

    sdk.close()


def test_single_source_baseline():
    """What does a single T0→Claim give? (Reference point)"""
    print()
    print("=" * 70)
    print("  BASELINE — Single T0 → Claim")
    print("=" * 70)

    sdk = TortoiseSDK(namespace="ep_diag_baseline")

    src = sdk.create_point("observation", "T0 Source", credibility="T0")
    claim = sdk.create_point("hypothesis", "Claim")

    op = sdk.create_operator("IMPL", src["id"], [claim["id"]])

    proj = sdk._get_proj()
    w = compute_operator_weight(proj, op["id"])
    print(f"  Operator weight: {w:.4f} (claim has 1 edge)")

    result = sdk.compute_confidence()
    print(f"\n  Source: {sdk.get_confidence(src['id'])['mean']:.4f}")
    print(f"  Claim:  {sdk.get_confidence(claim['id'])['mean']:.4f}")
    print(f"\n  This is the reference: single T0 source → claim at ~78%")

    sdk.close()


def test_chain_trace():
    """Trace chain middle vs conclusion weights."""
    print()
    print("=" * 70)
    print("  CHAIN — MIDDLE VS CONCLUSION WEIGHT TRACE")
    print("=" * 70)

    sdk = TortoiseSDK(namespace="ep_diag_chain")

    src = sdk.create_point("observation", "T0 Source", credibility="T0")
    mid = sdk.create_point("statement", "Middle")
    con = sdk.create_point("hypothesis", "Conclusion")

    op1 = sdk.create_operator("IMPL", src["id"], [mid["id"]])
    op2 = sdk.create_operator("IMPL", mid["id"], [con["id"]])

    proj = sdk._get_proj()

    # Check middle's edge count
    mid_edges = proj.g.query(
        "MATCH (c:Point {id:$cid})-[r:IMPL|NAND]-() RETURN count(r)",
        params={"cid": mid["id"]},
    ).result_set[0][0]
    con_edges = proj.g.query(
        "MATCH (c:Point {id:$cid})-[r:IMPL|NAND]-() RETURN count(r)",
        params={"cid": con["id"]},
    ).result_set[0][0]

    print(f"  Middle has {mid_edges} edges → op1 w = 1/log₂({mid_edges}+1) = {1.0/max(__import__('math').log2(mid_edges+1), 1):.4f}")
    print(f"  Conclusion has {con_edges} edges → op2 w = 1/log₂({con_edges}+1) = {1.0/max(__import__('math').log2(con_edges+1), 1):.4f}")

    w1 = compute_operator_weight(proj, op1["id"])
    w2 = compute_operator_weight(proj, op2["id"])
    print(f"\n  op1 (src→mid) weight: {w1:.4f}")
    print(f"  op2 (mid→con) weight: {w2:.4f}")
    print(f"\n  ⚠️  op1 is PENALIZED because middle has 2 edges (from op1+op2)")
    print(f"  op2 is NOT penalized because conclusion has only 1 edge")
    print(f"  So: middle gets a WEAK message from source (w={w1:.4f})")
    print(f"  And: conclusion gets a STRONG message from middle (w={w2:.4f})")
    print(f"  Net: conclusion can end up HIGHER than middle — attenuation reversed!")

    result = sdk.compute_confidence()
    print(f"\n  Results:")
    print(f"  Source:     {sdk.get_confidence(src['id'])['mean']:.4f}")
    print(f"  Middle:     {sdk.get_confidence(mid['id'])['mean']:.4f}")
    print(f"  Conclusion: {sdk.get_confidence(con['id'])['mean']:.4f}")

    sdk.close()


if __name__ == "__main__":
    analyze_density_penalty()
    test_single_source_baseline()
    trace_convergent_ep()
    trace_bottleneck()
    test_chain_trace()
