#!/usr/bin/env python3
"""Test directional IMPL fix for Tortoise EP (Issue #86).

Validates:
  1. IMPL with addresses/supports (or no label): directional.
     When conclusion B is NAND'd, premise A is NOT dragged down.
  2. IMPL with hasPart label: bidirectional.
     Composition hierarchies propagate both ways.
  3. NAND: remains symmetric — contradiction attacks both directions.
  4. Existing EP tests continue to pass (no NAND/hasPart regressions).

MUST run against a live FalkorDB. Uses test-prefixed isolated namespaces.
"""
from __future__ import annotations  # noqa: I001

import os
import sys
import pytest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# Isolated test graph — never the production graph.
# NOTE: the docker URI is set INSIDE the autouse fixture (per-test, restored
# after) — NOT at import time. An import-time set leaks into every later test
# file (#176 contamination).

from tortoise.sdk import TortoiseSDK  # noqa: I001
from tortoise.ep import TortoiseEP
from tortoise.weights import compute_operator_weight  # noqa: F401

# Requires live FalkorDB (Docker). Skip gracefully when unavailable so the
# no-Docker embedded suite stays green (AGENTS.md). Probe targets the DOCKER
# URI explicitly (embedded is "available" but lacks docker graph semantics).
_DB_URI = "docker://:falkordb@localhost:6379/tortoise_test_dir_impl"
FALKORDB_AVAILABLE = False
_OLD_URI = os.environ.get("TORTOISE_DB_URI")
try:
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    from tortoise.sdk import TortoiseSDK as _ProbeSDK
    _probe = _ProbeSDK()
    _probe._get_proj().g.query("RETURN 1")
    _probe.close()
    FALKORDB_AVAILABLE = True
except Exception:
    pass
finally:
    if _OLD_URI is not None:
        os.environ["TORTOISE_DB_URI"] = _OLD_URI
    else:
        os.environ.pop("TORTOISE_DB_URI", None)

pytestmark = pytest.mark.skipif(
    not FALKORDB_AVAILABLE, reason="Live FalkorDB (Docker) not available")


@pytest.fixture(autouse=True)
def _isolated_db_uri():
    """Set the isolated docker URI for THIS test, restore after (env-leak guard, #176)."""
    _old = os.environ.get("TORTOISE_DB_URI")
    os.environ["TORTOISE_DB_URI"] = _DB_URI
    yield
    if _old is not None:
        os.environ["TORTOISE_DB_URI"] = _old
    else:
        os.environ.pop("TORTOISE_DB_URI", None)



# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

TIER_MAP = {
    "T0": (10, 1), "T1": (5, 1), "T2": (3, 1), "T3": (2, 1), "T4": (1.1, 1),
}

T0_MEAN = 10.0 / 11.0  # ~0.9091


def fresh_sdk(graph_name=None):
    """SDK isolated to a unique test graph per call.

    Namespace is ignored when TORTOISE_DB_URI is set (db_uri wins over
    namespace), so we bake a unique graph name into the URI instead —
    otherwise tests accumulate operators in a shared graph and pollute
    each other's EP runs (#86).
    """
    gname = graph_name or f"tortoise_test_dir_{uuid.uuid4().hex[:8]}"
    # Point at the real FalkorDB (16379) but a uniquely-named test graph
    sdk = TortoiseSDK(db_path=None, namespace=None)
    sdk._db_uri = f"docker://:@localhost:16379/{gname}"
    sdk._proj = None  # force re-init on first use
    return sdk


def run_ep(sdk):
    """Run TortoiseEP on all operators in the current graph."""
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (o:Point) WHERE o.is_operator = true RETURN o.id"
    ).result_set
    op_ids = [r[0] for r in rows] if rows else []

    # Hydrate evidence from graph-persisted baselines (matches sdk.compute_confidence)
    ev_rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in ev_rows} if ev_rows else {}

    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3,
                    evidence=evidence)
    ep.run(op_ids, max_hops=2)
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.confidence IS NOT NULL "
        "RETURN n.id, n.confidence, n.ep_alpha, n.ep_beta"
    ).result_set
    return {
        r[0]: {"mean": r[1], "alpha": r[2], "beta": r[3]} for r in rows
    } if rows else {}


def get_mean(result, point_id):
    return result.get(point_id, {"mean": 0.5})["mean"]


def make_point(sdk, content, kind="statement"):
    # #943: create_point defaults to status='draft'; the #780 draft filter
    # strips draft inputs, degenerating IMPL factors (<2 live inputs) → zero
    # cascade (#992). Mirror of the #1000/#1004 sweep applied to
    # test_ep_directional (#1382) — target conclusions here are never
    # operator sources, so mark live explicitly.
    return sdk.create_point(kind, content, status="live")


def make_operator(sdk, source_id, target_id, op_type="IMPL", label=None, direction=None):
    kwargs = {"label": label}
    if direction is not None:
        kwargs["direction"] = direction
    return sdk.create_operator(op_type, source_id, [target_id], **kwargs)


# ═══════════════════════════════════════════════════════════════════
# TEST 1: IMPL directional — refuted conclusion does NOT drag down premise
# ═══════════════════════════════════════════════════════════════════

def test_impl_directional_refuted_conclusion():
    """
    Core test for Issue #86: when a conclusion B is refuted by a NAND,
    its supporting premise A should NOT be dragged down.

    Graph:
        A (T0) --IMPL--> B (baseline) <--NAND-- C (T0)

    Expected:
      - A stays near T0 prior (~0.9091) — directional IMPL prevents back-flow
      - B is pulled down from baseline — the NAND attacks the conclusion
      - C stays reasonably high (NAND is symmetric, but C starts at T0)
    """
    print()
    print("=" * 70)
    print("  TEST 1: IMPL Directional — Refuted Conclusion Doesn't Drag Premise")
    print("=" * 70)

    sdk = fresh_sdk()

    # Create nodes
    premise_a = make_point(sdk, "Premise A: strong evidence for B")
    conclusion_b = make_point(sdk, "Conclusion B: the claim being evaluated")
    defeater_c = make_point(sdk, "Defeater C: evidence contradicting B")

    # Set baselines
    sdk.set_point_baseline(premise_a["id"], *TIER_MAP["T0"])
    sdk.set_point_baseline(defeater_c["id"], *TIER_MAP["T0"])
    # conclusion B gets no baseline (starts at uniform)

    # Create operators (IMPL is unidirectional — explicit flag)
    op_impl = make_operator(sdk, premise_a["id"], conclusion_b["id"], "IMPL", direction="unidirectional")
    op_nand = make_operator(sdk, defeater_c["id"], conclusion_b["id"], "NAND")

    # Verify operator properties
    proj = sdk._get_proj()  # noqa: F841
    print(f"  IMPL operator: id={op_impl['id']}, label={op_impl.get('label', 'none')}")
    print(f"  NAND operator: id={op_nand['id']}, label={op_nand.get('label', 'none')}")

    result = run_ep(sdk)

    a_mean = get_mean(result, premise_a["id"])
    b_mean = get_mean(result, conclusion_b["id"])
    c_mean = get_mean(result, defeater_c["id"])

    print(f"\n  Premise A:    mean={a_mean:.4f}")
    print(f"  Conclusion B: mean={b_mean:.4f}")
    print(f"  Defeater C:   mean={c_mean:.4f}")

    # KEY ASSERTION: Premise A must NOT be dragged down by the refuted conclusion
    # With directional IMPL, A should stay at its T0 prior (~0.9091)
    assert abs(a_mean - T0_MEAN) < 0.03, (
        f"❌ Premise A dragged down: {a_mean:.4f} (expected ~{T0_MEAN:.4f})"
    )
    print(f"  ✅ Premise A at T0 prior: {a_mean:.4f} ≈ {T0_MEAN:.4f}")

    # Conclusion B should be pulled down by the NAND
    assert b_mean < 0.80, (
        f"❌ Conclusion B not pulled down by NAND: {b_mean:.4f}"
    )
    print(f"  ✅ Conclusion B refuted: {b_mean:.4f} < 0.80")

    # Conclusion B should still be above a non-degeneracy floor. Post-#855/#871
    # the engine does not boost NAND messages to weakly-evidenced claims, so a
    # T0 NAND on an unevidenced claim settles ~0.20 even with T0 IMPL support
    # (measured 0.1955; pure-support reference 0.6533). Band, not floor-only:
    # the 0.5 draft-default prior would pass a bare >0.05 floor, silently
    # re-introducing the #943/#780 regression this sweep eliminates (#1382).
    assert 0.05 < b_mean < 0.45, (
        f"❌ Conclusion B outside contested range: {b_mean:.4f}"
    )
    print(f"  ✅ Conclusion B refuted but not degenerate: {b_mean:.4f}")

    sdk.close()
    print("  ✅ TEST 1 PASSED")


# ═══════════════════════════════════════════════════════════════════
# TEST 2: hasPart bidirectional — composition hierarchies propagate both ways
# ═══════════════════════════════════════════════════════════════════

def test_haspart_bidirectional():
    """
    hasPart-labeled IMPL operators must be bidirectional.

    Graph:
        Whole W (T0) --IMPL (hasPart)--> Part P (baseline)

    Expected:
      - Part P receives confidence from Whole W (forward propagation)
      - Whole W should be affected by Part P's state (back-propagation)
      - This differs from unlabeled IMPL where back-propagation would NOT happen
    """
    print()
    print("=" * 70)
    print("  TEST 2: hasPart Bidirectional — Composition Hierarchy")
    print("=" * 70)

    sdk = fresh_sdk()

    whole = make_point(sdk, "Whole: the composite entity")
    part = make_point(sdk, "Part: a sub-component of the whole")

    # Set whole to strong evidence, part starts at baseline
    sdk.set_point_baseline(whole["id"], *TIER_MAP["T0"])

    # Create hasPart operator
    op = make_operator(sdk, whole["id"], part["id"], "IMPL", label="hasPart")

    proj = sdk._get_proj()  # noqa: F841
    print(f"  hasPart operator: id={op['id']}, label={op.get('label', 'none')}")

    result = run_ep(sdk)

    w_mean = get_mean(result, whole["id"])
    p_mean = get_mean(result, part["id"])

    print(f"\n  Whole: mean={w_mean:.4f}  (T0 prior ~{T0_MEAN:.4f})")
    print(f"  Part:  mean={p_mean:.4f}")

    # Forward: Part should receive significant confidence from Whole
    assert p_mean > 0.60, (
        f"❌ Part not receiving from Whole: {p_mean:.4f}"
    )
    print(f"  ✅ Part receives forward signal: {p_mean:.4f} > 0.60")

    # Backward: With bidirectional hasPart, the Whole should NOT be exactly
    # at T0 prior — the part's lower baseline pulls back on the whole slightly.
    # NOTE: This back-propagation is subtle — the Whole starts at T0 (very
    # strong) and the Part starts at uniform, so the back-effect is small.
    # But the key property: Whole should deviate from T0 prior more than with
    # unidirectional IMPL (tested by comparison).

    # The key check: Whole is affected by bidirectional coupling.
    # With directional-only, the Whole would stay at exactly T0 prior.
    # With hasPart bidirectional, Whole may deviate slightly.
    # We verify by checking that the Whole receives a back-message
    # (i.e., its posterior differs from its prior).

    # Actually, let's test this more cleanly by having the Part be
    # NAND'd, then checking if Whole is affected (bidirectional) vs not.
    # We'll do that in test 3.

    sdk.close()
    print("  ✅ TEST 2 PASSED")


# ═══════════════════════════════════════════════════════════════════
# TEST 3: hasPart bidirectional with NAND — whole dragged by part's defeat
# ═══════════════════════════════════════════════════════════════════

def test_haspart_nand_affects_whole():
    """
    hasPart bidirectional + NAND on part → whole is affected.

    Graph:
        Whole W (T0) --IMPL (hasPart)--> Part P (baseline) <--NAND-- Defeater D (T0)

    Expected:
      - Part P is pulled down by NAND
      - Whole W is also dragged down (hasPart is bidirectional)
      - Defeater D stays reasonably high

    This is the key difference from unidirectional IMPL:
    with addresses/supports, the Whole would stay at T0 prior.
    """
    print()
    print("=" * 70)
    print("  TEST 3: hasPart Bidirectional — NAND on Part Affects Whole")
    print("=" * 70)

    sdk = fresh_sdk()

    whole = make_point(sdk, "Whole: composite entity")
    part = make_point(sdk, "Part: sub-component")
    defeater = make_point(sdk, "Defeater: part is broken")

    sdk.set_point_baseline(whole["id"], *TIER_MAP["T0"])
    sdk.set_point_baseline(defeater["id"], *TIER_MAP["T0"])

    op_haspart = make_operator(sdk, whole["id"], part["id"], "IMPL", label="hasPart")
    op_nand = make_operator(sdk, defeater["id"], part["id"], "NAND")  # noqa: F841

    print(f"  hasPart operator: label={op_haspart.get('label', 'none')}")

    result = run_ep(sdk)

    w_mean = get_mean(result, whole["id"])
    p_mean = get_mean(result, part["id"])
    d_mean = get_mean(result, defeater["id"])

    print(f"\n  Whole W:    mean={w_mean:.4f}  (T0 prior ~{T0_MEAN:.4f})")
    print(f"  Part P:     mean={p_mean:.4f}")
    print(f"  Defeater D: mean={d_mean:.4f}  (T0 prior ~{T0_MEAN:.4f})")

    # Part should be pulled down by NAND
    assert p_mean < 0.80, (
        f"❌ Part not pulled down by NAND: {p_mean:.4f}"
    )
    print(f"  ✅ Part refuted: {p_mean:.4f} < 0.80")

    # Whole should be reduced by the hasPart back-message. The reduction
    # uses the NAND contradiction potential phi_nand = exp(-w*ca*cb) which
    # penalizes agreement — at weight=1.0 (default operator weight),
    # T0-vs-T0 coupling is ~0.44 so the whole settles just at/under its
    # prior rather than cratering. Assert the DIRECTIONAL effect: without the
    # back-message the whole stays at ~0.914 (weak positive agreement push);
    # with it, it is pulled down to/under the prior (~0.910). The regression
    # guard is that it must NOT be ABOVE 0.914 (the no-back-message value).
    assert w_mean < 0.9135, (
        f"❌ Whole not reduced by hasPart back-message: {w_mean:.4f} "
        f"(without back-message it would sit ~0.9142)\n"
        f"     hasPart backward propagation is not working!"
    )
    print(f"  ✅ Whole reduced by bidirectional hasPart back-message: "
          f"{w_mean:.4f} < 0.9135 (was ~0.9142 without fix)")

    sdk.close()
    print("  ✅ TEST 3 PASSED")


# ═══════════════════════════════════════════════════════════════════
# TEST 4: NAND remains symmetric — contradiction attacks both directions
# ═══════════════════════════════════════════════════════════════════

def test_nand_symmetric():
    """
    NAND must remain symmetric. Two T0 claims that NAND each other
    should both be pulled toward 50%.

    Graph:
        Claim A (T0) --NAND--> Claim B (T0)

    Expected:
      - Both A and B are pulled toward ~50% (mutual contradiction)
      - NAND is inherently symmetric regardless of label
    """
    print()
    print("=" * 70)
    print("  TEST 4: NAND Symmetric — Contradiction Both Ways")
    print("=" * 70)

    sdk = fresh_sdk()

    claim_a = make_point(sdk, "Claim A: position X is correct")
    claim_b = make_point(sdk, "Claim B: position X is wrong")

    sdk.set_point_baseline(claim_a["id"], *TIER_MAP["T0"])
    sdk.set_point_baseline(claim_b["id"], *TIER_MAP["T0"])

    op = make_operator(sdk, claim_a["id"], claim_b["id"], "NAND")  # noqa: F841

    result = run_ep(sdk)

    a_mean = get_mean(result, claim_a["id"])
    b_mean = get_mean(result, claim_b["id"])

    print(f"\n  Claim A: mean={a_mean:.4f}")
    print(f"  Claim B: mean={b_mean:.4f}")

    # Both should show symmetric NAND coupling. phi_nand = exp(-w*ca*cb) is
    # a contradiction potential — it penalizes agreement (both high) and
    # is compatible with disagreement (one high, one low). With weight=1.0
    # (default operator weight) and EP damping, symmetric NAND drives both
    # claims downward from their T0 priors while preserving symmetry.
    # Symmetry is the regression guard for #86.
    assert abs(a_mean - b_mean) < 0.01, (
        f"❌ NAND not symmetric: A={a_mean:.4f} B={b_mean:.4f}"
    )
    print(f"  ✅ NAND symmetric: A={a_mean:.4f} B={b_mean:.4f} (Δ={abs(a_mean - b_mean):.4f})")

    # Both should be above 35% (not zeroed out)
    assert a_mean > 0.35, f"❌ Claim A too low: {a_mean:.4f}"
    assert b_mean > 0.35, f"❌ Claim B too low: {b_mean:.4f}"
    print(f"  ✅ Both claims in reasonable range: > 0.35")  # noqa: F541

    # Symmetry: A and B should be roughly equal (within tolerance)
    assert abs(a_mean - b_mean) < 0.05, (
        f"❌ NAND asymmetric: |A-B| = {abs(a_mean - b_mean):.4f}"
    )
    print(f"  ✅ NAND is symmetric: |A-B| = {abs(a_mean - b_mean):.4f} < 0.05")

    sdk.close()
    print("  ✅ TEST 4 PASSED")


# ═══════════════════════════════════════════════════════════════════
# TEST 5: addresses directional — comparison with hasPart
# ═══════════════════════════════════════════════════════════════════

def test_addresses_directional():
    """
    addresses-labeled IMPL must be directional (same as unlabeled IMPL).

    Graph:
        Need N (T0) --IMPL (addresses)--> Feature F (baseline) <--NAND-- Defeater D (T0)

    Expected:
      - Feature F is pulled down by NAND
      - Need N stays at T0 prior (addresses is directional)
      - This contrasts with hasPart bidirectional behavior
    """
    print()
    print("=" * 70)
    print("  TEST 5: addresses Directional — Need Unaffected by Feature Defeat")
    print("=" * 70)

    sdk = fresh_sdk()

    need = make_point(sdk, "Need N: users need export functionality")
    feature = make_point(sdk, "Feature F: CSV export feature")
    defeater = make_point(sdk, "Defeater D: CSV export has critical security bug")

    sdk.set_point_baseline(need["id"], *TIER_MAP["T0"])
    sdk.set_point_baseline(defeater["id"], *TIER_MAP["T0"])

    op_addr = make_operator(sdk, need["id"], feature["id"], "IMPL", label="addresses", direction="unidirectional")
    op_nand = make_operator(sdk, defeater["id"], feature["id"], "NAND")  # noqa: F841

    print(f"  addresses operator: label={op_addr.get('label', 'none')}")

    result = run_ep(sdk)

    n_mean = get_mean(result, need["id"])
    f_mean = get_mean(result, feature["id"])
    d_mean = get_mean(result, defeater["id"])

    print(f"\n  Need N:       mean={n_mean:.4f}  (T0 prior ~{T0_MEAN:.4f})")
    print(f"  Feature F:    mean={f_mean:.4f}")
    print(f"  Defeater D:   mean={d_mean:.4f}  (T0 prior ~{T0_MEAN:.4f})")

    # Feature should be pulled down by NAND
    assert f_mean < 0.85, (
        f"❌ Feature not pulled down: {f_mean:.4f}"
    )
    print(f"  ✅ Feature refuted: {f_mean:.4f} < 0.85")

    # KEY ASSERTION: Need must NOT be dragged down (addresses is directional)
    assert abs(n_mean - T0_MEAN) < 0.03, (
        f"❌ Need dragged down by refuted feature: {n_mean:.4f} vs {T0_MEAN:.4f}\n"
        f"     addresses should be directional!"
    )
    print(f"  ✅ Need at T0 prior: {n_mean:.4f} ≈ {T0_MEAN:.4f} "
          f"(addresses is directional)")

    sdk.close()
    print("  ✅ TEST 5 PASSED")


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    failures = 0

    tests = [
        ("IMPL Directional (refuted conc.)", test_impl_directional_refuted_conclusion),
        ("hasPart Bidirectional", test_haspart_bidirectional),
        ("hasPart + NAND affects whole", test_haspart_nand_affects_whole),
        ("NAND Symmetric", test_nand_symmetric),
        ("addresses Directional", test_addresses_directional),
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
        print("  🎉 ALL 5 TESTS PASSED — Directional IMPL with label-aware routing is sound")
    else:
        print(f"  ⚠️  {failures} TEST(S) FAILED")
    print("=" * 70)
    print()
