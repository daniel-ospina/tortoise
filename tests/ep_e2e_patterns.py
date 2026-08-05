#!/usr/bin/env python3
"""End-to-end EP tests for Tortoise argument patterns.

Tests the five argument patterns from how-to-use-tortoise:
  1. Chain: T0 → Middle → Conclusion (attenuation)
  2. Convergent: Two T0 sources → same claim (parallel support, removal)
  3. Undercutter: T0 → Claim, with mitigation (edge weakening)
  4. Defeater: T0 IMPL + T0 NAND on same claim (balanced ~50%)
  5. Linked premises: Three T2 sources → bottleneck → Claim (partial drop)

Each test uses an isolated namespace to avoid cross-contamination.
"""

import os
import sys
from pathlib import Path

# Ensure we can import tortoise
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Isolated test graph — never the production graph
os.environ["TORTOISE_DB_URI"] = "docker://:falkordb@localhost:6379/tortoise_test_ep_e2e_patterns"

from tortoise.sdk import TortoiseSDK


def fmt(conf):
    """Format confidence dict as readable string."""
    return f"mean={conf['mean']:.4f} (α={conf['alpha']:.2f}, β={conf['beta']:.2f}, var={conf['variance']:.6f})"


def header(title):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def subheader(title):
    print(f"\n  ── {title} ──")


# ─────────────────────────────────────────────────────────────────
# SCENARIO 1: Chain of Implication
# ─────────────────────────────────────────────────────────────────

def test_chain():
    """T0 source → middle → conclusion. Check attenuation per hop."""
    header("SCENARIO 1: Chain — T0 source → Middle → Conclusion")

    sdk = TortoiseSDK(namespace="ep_test_chain")

    # Create points
    source = sdk.create_point("observation",
        "T0 Source: Meta-analysis of 50 studies confirms X",
        credibility="T0")
    middle = sdk.create_point("statement",
        "Middle: If X is confirmed, then mechanism Y is likely")
    conclusion = sdk.create_point("hypothesis",
        "Conclusion: Therefore, outcome Z is probable")

    print(f"  Source:     {source['id']}")
    print(f"  Middle:     {middle['id']}")
    print(f"  Conclusion: {conclusion['id']}")

    # Create operators (chain)
    op1 = sdk.create_operator("IMPL", source["id"], [middle["id"]])
    op2 = sdk.create_operator("IMPL", middle["id"], [conclusion["id"]])
    print(f"  Op1 (src→mid): {op1['id']}")
    print(f"  Op2 (mid→con): {op2['id']}")

    # Run EP
    result = sdk.compute_confidence()
    print(f"\n  EP: {result['iterations']} iters, converged={result['converged']}")

    source_conf = sdk.get_confidence(source["id"])
    middle_conf = sdk.get_confidence(middle["id"])
    conclusion_conf = sdk.get_confidence(conclusion["id"])

    print(f"\n  Source:     {fmt(source_conf)}")
    print(f"  Middle:     {fmt(middle_conf)}")
    print(f"  Conclusion: {fmt(conclusion_conf)}")

    # Sense checks
    issues = []
    if not (0.85 < source_conf["mean"] < 0.95):
        issues.append(f"Source mean {source_conf['mean']:.4f} outside expected 0.85-0.95 for T0")
    if not (source_conf["mean"] > middle_conf["mean"] > conclusion_conf["mean"]):
        issues.append(
            f"Attenuation violation: source={source_conf['mean']:.4f}, "
            f"middle={middle_conf['mean']:.4f}, conclusion={conclusion_conf['mean']:.4f}"
        )
    if middle_conf["mean"] > source_conf["mean"]:
        issues.append("Middle confidence exceeds source (should attenuate, not amplify)")

    if issues:
        print(f"\n  ⚠️  ISSUES ({len(issues)}):")
        for i in issues:
            print(f"     - {i}")
    else:
        print("\n  ✅ All chain checks pass")

    sdk.close()
    return {"source": source_conf, "middle": middle_conf, "conclusion": conclusion_conf,
            "issues": issues}


# ─────────────────────────────────────────────────────────────────
# SCENARIO 2: Convergent Arguments
# ─────────────────────────────────────────────────────────────────

def test_convergent():
    """Two T0 sources → same claim. Compare with-one vs with-both."""
    header("SCENARIO 2: Convergent — Two T0 sources → same Claim")

    sdk = TortoiseSDK(namespace="ep_test_conv")

    # Create points
    source_a = sdk.create_point("observation",
        "T0 Source A: Clinical trial shows treatment is effective",
        credibility="T0")
    source_b = sdk.create_point("observation",
        "T0 Source B: Independent replication confirms effectiveness",
        credibility="T0")
    claim = sdk.create_point("hypothesis",
        "Claim: The treatment is effective for the target population")

    # Create BOTH operators
    op_a = sdk.create_operator("IMPL", source_a["id"], [claim["id"]])
    op_b = sdk.create_operator("IMPL", source_b["id"], [claim["id"]])

    # Run EP with both sources
    result_both = sdk.compute_confidence()
    claim_both = sdk.get_confidence(claim["id"])
    source_a_conf = sdk.get_confidence(source_a["id"])
    source_b_conf = sdk.get_confidence(source_b["id"])

    print(f"\n  Sources: A={source_a['id'][:12]}..., B={source_b['id'][:12]}...")
    print(f"  Claim:   {claim['id'][:12]}...")
    print(f"  EP (both sources): {result_both['iterations']} iters, converged={result_both['converged']}")
    print(f"\n  Source A:  {fmt(source_a_conf)}")
    print(f"  Source B:  {fmt(source_b_conf)}")
    print(f"  Claim (A+B): {fmt(claim_both)}")

    # Now remove source B's operator
    subheader("After removing Source B's operator")
    sdk.delete_point(op_b["id"])
    result_a_only = sdk.compute_confidence()
    claim_a_only = sdk.get_confidence(claim["id"])

    print(f"  EP (source A only): {result_a_only['iterations']} iters, converged={result_a_only['converged']}")
    print(f"  Claim (A only): {fmt(claim_a_only)}")

    # Sense checks
    issues = []
    drop = claim_both["mean"] - claim_a_only["mean"]
    if drop < 0:
        issues.append(f"Confidence INCREASED after removing a source ({claim_both['mean']:.4f} → {claim_a_only['mean']:.4f})")
    if drop == 0:
        issues.append(f"No drop after removing source B — both sources should contribute")
    if claim_a_only["mean"] < 0.5:
        issues.append(f"Claim with single T0 source dropped below 50%: {claim_a_only['mean']:.4f}")

    print(f"\n  Drop from removing B: {drop:+.4f} ({'expected' if 0 < drop < 0.3 else 'unexpected magnitude'})")
    if claim_both["mean"] > claim_a_only["mean"]:
        print(f"  Two sources > one source: ✅ (both contribute)")
    else:
        print(f"  Two sources ≤ one source: ❌")

    if issues:
        print(f"  ⚠️  ISSUES ({len(issues)}):")
        for i in issues:
            print(f"     - {i}")
    else:
        print("  ✅ All convergent checks pass")

    sdk.close()
    return {"claim_both": claim_both, "claim_a_only": claim_a_only,
            "drop": drop, "issues": issues}


# ─────────────────────────────────────────────────────────────────
# SCENARIO 3: Undercutter (Mitigation)
# ─────────────────────────────────────────────────────────────────

def test_undercutter():
    """T0 source → claim, then mitigate the edge. Check if mitigation affects EP."""
    header("SCENARIO 3: Undercutter — T0 source → Claim with mitigation")

    sdk = TortoiseSDK(namespace="ep_test_under")

    source = sdk.create_point("observation",
        "T0 Source: Observational study finds correlation X-Y",
        credibility="T0")
    claim = sdk.create_point("hypothesis",
        "Claim: X causes Y")

    op = sdk.create_operator("IMPL", source["id"], [claim["id"]])

    # Run EP BEFORE mitigation
    result_before = sdk.compute_confidence()
    claim_before = sdk.get_confidence(claim["id"])
    source_conf = sdk.get_confidence(source["id"])

    print(f"\n  Source: {source['id'][:12]}...")
    print(f"  Claim:  {claim['id'][:12]}...")
    print(f"  Operator: {op['id'][:12]}...")
    print(f"  EP (no mitigation): {result_before['iterations']} iters, converged={result_before['converged']}")
    print(f"\n  Source:     {fmt(source_conf)}")
    print(f"  Claim (before mitigation): {fmt(claim_before)}")

    # Mitigate the operator
    subheader("After mitigating the operator (strength=0.3)")
    mitigation = sdk.mitigate_operator(
        op["id"],
        reason="Study has no control group — correlation, not causation",
        strength=0.3
    )
    print(f"  Mitigation created: {mitigation['id'][:12]}...")
    print(f"  Mitigation strength: {mitigation.get('mitigation_strength', 'N/A')}")

    # Check operator weight after mitigation
    from tortoise.weights import compute_operator_weight
    op_weight = compute_operator_weight(sdk._get_proj(), op["id"])
    print(f"  Operator weight (computed): {op_weight:.4f}")

    # Run EP AFTER mitigation
    result_after = sdk.compute_confidence()
    claim_after = sdk.get_confidence(claim["id"])

    print(f"\n  EP (after mitigation): {result_after['iterations']} iters, converged={result_after['converged']}")
    print(f"  Claim (after mitigation): {fmt(claim_after)}")

    # Also test with annotate_operator (bias=0.9 = high hidden stake)
    subheader("After annotating operator (bias=0.9, precision=0.3)")
    sdk.annotate_operator(op["id"], bias=0.9, precision=0.3, consistency=0.5, directness=0.4)
    op_weight_annotated = compute_operator_weight(sdk._get_proj(), op["id"])
    print(f"  Operator weight (annotated): {op_weight_annotated:.4f}")

    result_annotated = sdk.compute_confidence()
    claim_annotated = sdk.get_confidence(claim["id"])
    print(f"  Claim (after annotation): {fmt(claim_annotated)}")

    # Sense checks
    issues = []
    change = claim_before["mean"] - claim_after["mean"]
    if abs(change) < 1e-6:
        issues.append(
            f"Mitigation had NO effect on claim confidence "
            f"(before={claim_before['mean']:.4f}, after={claim_after['mean']:.4f}). "
            f"GAP: mitigation_strength is not wired into compute_operator_weight"
        )
    if abs(claim_after["mean"] - claim_annotated["mean"]) < 1e-6:
        issues.append(
            f"Annotation had NO effect on claim confidence. "
            f"GAP: annotation dimensions are archived (annotation_factor=1.0 hardcoded)"
        )

    if issues:
        print(f"\n  ⚠️  ISSUES ({len(issues)}):")
        for i in issues:
            print(f"     - {i}")
    else:
        print("\n  ✅ All undercutter checks pass")

    sdk.close()
    return {"claim_before": claim_before, "claim_after": claim_after,
            "claim_annotated": claim_annotated, "change": change, "issues": issues}


# ─────────────────────────────────────────────────────────────────
# SCENARIO 4: Defeater (NAND)
# ─────────────────────────────────────────────────────────────────

def test_defeater():
    """T0 IMPL + T0 NAND on same claim. Check balance near ~50%."""
    header("SCENARIO 4: Defeater — T0 IMPL + T0 NAND on same Claim")

    sdk = TortoiseSDK(namespace="ep_test_defeat")

    source = sdk.create_point("observation",
        "T0 Source: Study finds drug reduces symptoms by 40%",
        credibility="T0")
    defeater = sdk.create_point("observation",
        "T0 Defeater: Independent review finds results not replicable in comorbid populations",
        credibility="T0")
    claim = sdk.create_point("hypothesis",
        "Claim: The drug is effective for the general population")

    op_impl = sdk.create_operator("IMPL", source["id"], [claim["id"]])
    op_nand = sdk.create_operator("NAND", defeater["id"], [claim["id"]])

    print(f"\n  Source:   {source['id'][:12]}... (IMPL)")
    print(f"  Defeater: {defeater['id'][:12]}... (NAND)")
    print(f"  Claim:    {claim['id'][:12]}...")
    print(f"  Op IMPL:  {op_impl['id'][:12]}...")
    print(f"  Op NAND:  {op_nand['id'][:12]}...")

    result = sdk.compute_confidence()
    source_conf = sdk.get_confidence(source["id"])
    defeater_conf = sdk.get_confidence(defeater["id"])
    claim_conf = sdk.get_confidence(claim["id"])

    print(f"\n  EP: {result['iterations']} iters, converged={result['converged']}")
    print(f"\n  Source:     {fmt(source_conf)}")
    print(f"  Defeater:   {fmt(defeater_conf)}")
    print(f"  Claim:      {fmt(claim_conf)}")

    # Sense checks
    issues = []
    mean = claim_conf["mean"]
    if not (0.35 < mean < 0.65):
        issues.append(
            f"Claim mean {mean:.4f} outside expected 0.35-0.65 for balanced T0 IMPL+NAND. "
            f"Equal-quality support+contradiction should converge near 50%"
        )
    # Variance should be high — contested claim
    var = claim_conf["variance"]
    if var < 0.02:
        issues.append(
            f"Claim variance {var:.6f} too low for a contested claim — "
            f"should show high uncertainty"
        )

    if issues:
        print(f"\n  ⚠️  ISSUES ({len(issues)}):")
        for i in issues:
            print(f"     - {i}")
    else:
        print("\n  ✅ All defeater checks pass")

    sdk.close()
    return {"source": source_conf, "defeater": defeater_conf, "claim": claim_conf, "issues": issues}


# ─────────────────────────────────────────────────────────────────
# SCENARIO 5: Linked Premises
# ─────────────────────────────────────────────────────────────────

def test_linked_premises():
    """Three T2 sources → bottleneck → claim. Invalidate one, check partial drop."""
    header("SCENARIO 5: Linked Premises — Three T2 sources → Bottleneck → Claim")

    sdk = TortoiseSDK(namespace="ep_test_linked")

    # Three T2 premises
    prem_a = sdk.create_point("observation",
        "T2 Premise A: Expert consensus favors approach",
        credibility="T2")
    prem_b = sdk.create_point("observation",
        "T2 Premise B: Case studies show positive results",
        credibility="T2")
    prem_c = sdk.create_point("observation",
        "T2 Premise C: Historical data supports the pattern",
        credibility="T2")

    # Bottleneck: joint support claim
    bottleneck = sdk.create_point("statement",
        "Bottleneck: A, B, C jointly support the conclusion")

    # Conclusion
    conclusion = sdk.create_point("hypothesis",
        "Conclusion: The approach is sound and should be adopted")

    # Operators: premises → bottleneck
    op_a = sdk.create_operator("IMPL", prem_a["id"], [bottleneck["id"]])
    op_b = sdk.create_operator("IMPL", prem_b["id"], [bottleneck["id"]])
    op_c = sdk.create_operator("IMPL", prem_c["id"], [bottleneck["id"]])

    # Operator: bottleneck → conclusion
    op_btl = sdk.create_operator("IMPL", bottleneck["id"], [conclusion["id"]])

    print(f"\n  Premises: A, B, C (T2 credibility)")
    print(f"  Bottleneck: {bottleneck['id'][:12]}...")
    print(f"  Conclusion: {conclusion['id'][:12]}...")

    # Run EP with all three premises
    result_all = sdk.compute_confidence()
    prem_a_conf_all = sdk.get_confidence(prem_a["id"])
    prem_b_conf_all = sdk.get_confidence(prem_b["id"])
    prem_c_conf_all = sdk.get_confidence(prem_c["id"])
    bottleneck_all = sdk.get_confidence(bottleneck["id"])
    conclusion_all = sdk.get_confidence(conclusion["id"])

    print(f"\n  EP (all 3 premises): {result_all['iterations']} iters, converged={result_all['converged']}")
    print(f"\n  Premise A:   {fmt(prem_a_conf_all)}")
    print(f"  Premise B:   {fmt(prem_b_conf_all)}")
    print(f"  Premise C:   {fmt(prem_c_conf_all)}")
    print(f"  Bottleneck:  {fmt(bottleneck_all)}")
    print(f"  Conclusion:  {fmt(conclusion_all)}")

    # Remove premise C's operator
    subheader("After invalidating Premise C (delete its operator)")
    sdk.delete_point(op_c["id"])
    result_two = sdk.compute_confidence()
    bottleneck_two = sdk.get_confidence(bottleneck["id"])
    conclusion_two = sdk.get_confidence(conclusion["id"])

    print(f"  EP (2 premises): {result_two['iterations']} iters, converged={result_two['converged']}")
    print(f"  Bottleneck:  {fmt(bottleneck_two)}")
    print(f"  Conclusion:  {fmt(conclusion_two)}")

    # Sense checks
    issues = []
    drop_btl = bottleneck_all["mean"] - bottleneck_two["mean"]
    drop_con = conclusion_all["mean"] - conclusion_two["mean"]

    if drop_btl < 0:
        issues.append(f"Bottleneck confidence INCREASED after removing a premise ({bottleneck_all['mean']:.4f} → {bottleneck_two['mean']:.4f})")
    if drop_con < 0:
        issues.append(f"Conclusion confidence INCREASED after removing a premise ({conclusion_all['mean']:.4f} → {conclusion_two['mean']:.4f})")
    if drop_btl == 0:
        issues.append("No drop in bottleneck after removing premise C — all premises should contribute")
    if bottleneck_all["mean"] < prem_a_conf_all["mean"]:
        issues.append(f"Bottleneck ({bottleneck_all['mean']:.4f}) lower than weakest premise ({prem_a_conf_all['mean']:.4f}) — accumulation should increase confidence")

    print(f"\n  Bottleneck drop: {drop_btl:+.4f}")
    print(f"  Conclusion drop:  {drop_con:+.4f}")

    # When all 3 premises support the bottleneck, bottleneck should be ≥ max premise
    max_prem = max(prem_a_conf_all["mean"], prem_b_conf_all["mean"], prem_c_conf_all["mean"])
    if bottleneck_all["mean"] >= max_prem:
        print(f"  Bottleneck ≥ max premise: ✅ ({bottleneck_all['mean']:.4f} ≥ {max_prem:.4f})")
    else:
        print(f"  Bottleneck < max premise: ❌ ({bottleneck_all['mean']:.4f} < {max_prem:.4f})")

    if drop_btl > 0:
        print(f"  Partial drop (not collapse): ✅ (bottleneck drop={drop_btl:+.4f})")
    else:
        print(f"  No partial drop: ❌")

    if issues:
        print(f"  ⚠️  ISSUES ({len(issues)}):")
        for i in issues:
            print(f"     - {i}")
    else:
        print("  ✅ All linked premises checks pass")

    sdk.close()
    return {"bottleneck_all": bottleneck_all, "conclusion_all": conclusion_all,
            "bottleneck_two": bottleneck_two, "conclusion_two": conclusion_two,
            "drop_btl": drop_btl, "drop_con": drop_con, "issues": issues}


# ─────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    all_results = {}
    all_issues = {}

    tests = [
        ("chain", test_chain),
        ("convergent", test_convergent),
        ("undercutter", test_undercutter),
        ("defeater", test_defeater),
        ("linked_premises", test_linked_premises),
    ]

    for name, fn in tests:
        try:
            all_results[name] = fn()
            all_issues[name] = all_results[name].pop("issues", [])
        except Exception as e:
            print(f"\n  ❌ {name.upper()} FAILED with exception: {e}")
            import traceback
            traceback.print_exc()
            all_results[name] = {"error": str(e)}
            all_issues[name] = [f"Exception: {e}"]

    # ── Summary ──
    header("SUMMARY")
    total_issues = 0
    for name, _fn in tests:
        test_issues = all_issues.get(name, [])
        total_issues += len(test_issues)
        if test_issues:
            print(f"\n  ⚠️  {name.upper()}: {len(test_issues)} issue(s)")
            for i in test_issues:
                print(f"     - {i}")
        else:
            print(f"\n  ✅ {name.upper()}: All checks passed")

    print(f"\n{'=' * 70}")
    if total_issues == 0:
        print("  🎉 ALL TESTS PASSED — EP confidence values make common sense")
    else:
        print(f"  ⚠️  {total_issues} TOTAL ISSUES across {sum(1 for v in all_issues.values() if v)} scenarios")
        print(f"\n  Issues are either:")
        print(f"    1. EP behavior that doesn't match common-sense expectations")
        print(f"    2. Gaps in the SDK (mitigation/annotation not wired to EP weights)")
    print(f"{'=' * 70}\n")
