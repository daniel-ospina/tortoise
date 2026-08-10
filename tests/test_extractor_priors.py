"""Test extractor confidence wired as EP evidence priors (#6901).

TortoiseEP.run() accepts evidence dict {claim_id: (alpha, beta)}.
Extractor confidence values (0.0-1.0) are calibrated to Beta priors:
  - confidence 0.5 → Beta(1,1) uniform (no information)
  - confidence 0.8 → Beta(1+0.8k, 1+0.2k) with k=2 (mild prior)
  - confidence 0.3 → Beta(1+0.3k, 1+0.7k) — weaker than uniform?
    DESIGN CHOICE: low confidence (<0.5) ALSO receives a prior —
    it's weaker evidence toward the truth value the extractor saw.
    The prior pulls toward the extractor's belief, but more weakly.
    A confidence of 0.3 means "I'm 30% sure this claim is true"
    → prior Beta(1.6, 2.4) with mean 0.4, still informative.

Tests:
  1. test_ep_accepts_evidence — EP uses evidence dict as prior
  2. test_high_confidence_narrower — confidence 0.9 → lower variance than 0.5
  3. test_low_confidence_wider — confidence 0.3 → wider posterior than 0.9
  4. test_no_evidence_fallback — no evidence → Beta(1,1) uniform
"""

import math
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

from tortoise.ep import TortoiseEP
from tortoise.projection import FalkorProjection

# ── FalkorDB availability ─────────────────────────────────────────

try:
    from redislite.falkordb_client import FalkorDB  # noqa: F401
    HAS_FALKOR = True
except ImportError:
    HAS_FALKOR = False

needs_falkor = pytest.mark.skipif(not HAS_FALKOR, reason="FalkorDB not available")


# ── Helpers ───────────────────────────────────────────────────────

def _tmp(name):
    d = tempfile.mkdtemp(prefix="tortoise_ep_")
    return os.path.join(d, name), d


def _build_graph(proj, claim_ids, operators):
    for cid in claim_ids:
        proj._upsert({"id": cid, "content": cid, "context": "test"})
    for op_id, op_type, inputs in operators:
        proj._upsert({
            "id": op_id, "content": op_type, "context": "test",
            "operator": {"op_type": op_type, "inputs": inputs},
        })


def _run_ep(claim_ids, operators, *, evidence=None, damping=0.3):
    """Run TortoiseEP on a temp FalkorDB, return dict of {cid: compute_confidence()}. """
    db_path, tmpdir = _tmp("g_ep.db")
    try:
        proj = FalkorProjection(db_path, graph_name="test")
        try:
            _build_graph(proj, claim_ids, operators)
            ep = TortoiseEP(proj, damping=damping, n_quad=8,
                            max_iter=100, tol=1e-4)
            op_ids = [op[0] for op in operators]
            ep.run(op_ids, evidence=evidence)
            return {cid: ep.compute_confidence(cid) for cid in claim_ids}
        finally:
            proj.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
# Test 1: EP accepts evidence
# ═══════════════════════════════════════════════════════════════════

@needs_falkor
def test_ep_accepts_evidence():
    """TortoiseEP.run() with evidence dict. Assert EP uses evidence as prior.

    On an isolated claim (no operators), the posterior = evidence prior.
    No messages → no updates → output should match input evidence.
    """
    claim_ids = ["c0"]
    operators = []  # isolated — no factors

    evidence = {"c0": (3.0, 1.0)}  # strong positive prior
    confs = _run_ep(claim_ids, operators, evidence=evidence)

    c = confs["c0"]
    # Isolated claim with evidence (3,1): posterior ≈ (3,1)
    assert abs(c["alpha"] - 3.0) < 0.01, \
        f"alpha should match evidence: {c['alpha']:.4f} != 3.0"
    assert abs(c["beta"] - 1.0) < 0.01, \
        f"beta should match evidence: {c['beta']:.4f} != 1.0"
    assert abs(c["mean"] - 0.75) < 0.01, \
        f"mean should be 3/(3+1)=0.75, got {c['mean']:.4f}"


# ═══════════════════════════════════════════════════════════════════
# Test 2: High confidence → narrower posterior (lower variance)
# ═══════════════════════════════════════════════════════════════════

@needs_falkor
def test_high_confidence_narrower():
    """Claim with extractor confidence 0.9 gets narrower EP posterior
    (lower variance) than claim with 0.5 (uniform prior).

    Two isolated claims, same graph. The high-confidence claim's
    evidence prior Beta(2.8, 1.2) is more concentrated than
    Beta(1,1) → lower posterior variance.
    """
    claim_ids = ["c_high", "c_neutral"]
    operators = []  # isolated

    # Convert confidences to Beta priors
    ev_high = TortoiseEP.confidence_to_prior(0.9)
    ev_neutral = TortoiseEP.confidence_to_prior(0.5)

    evidence = {
        "c_high": ev_high,       # Beta(2.8, 1.2), mean=0.7
        "c_neutral": ev_neutral,  # Beta(1, 1), uniform
    }

    confs = _run_ep(claim_ids, operators, evidence=evidence)

    var_high = confs["c_high"]["variance"]
    var_neutral = confs["c_neutral"]["variance"]

    # Uniform Beta(1,1) variance = 1/(4*3) = 0.0833
    # Beta(2.8, 1.2) variance ≈ (2.8*1.2)/(4²*5) = 3.36/80 = 0.042
    assert var_high < var_neutral, (
        f"High confidence should give lower variance: "
        f"var_high={var_high:.5f} >= var_neutral={var_neutral:.5f}")


# ═══════════════════════════════════════════════════════════════════
# Test 3: Low confidence → wider posterior than high confidence
# ═══════════════════════════════════════════════════════════════════

@needs_falkor
def test_low_confidence_wider():
    """Claim with extractor confidence 0.3 gets wider posterior than 0.9.

    DESIGN CHOICE: Low confidence (<0.5) ALSO receives an informative
    prior — it's evidence that the extractor is moderately confident
    the claim is FALSE. Beta(1.6, 2.4) has mean 0.4, pulling the
    posterior slightly toward "false" while remaining wider than a
    high-confidence prior. Confidence 0.5 = uniform Beta(1,1) = no
    information — the boundary between "somewhat true" and "somewhat
    false" priors.

    Assert: var(conf=0.3) < var(conf=0.5) — it IS informative (pulls
    toward false), but var(conf=0.3) > var(conf=0.9) — it's weaker
    evidence than the high-confidence prior.
    """
    claim_ids = ["c_low", "c_high", "c_neutral"]
    operators = []

    evidence = {
        "c_low": TortoiseEP.confidence_to_prior(0.3),
        "c_high": TortoiseEP.confidence_to_prior(0.9),
        "c_neutral": TortoiseEP.confidence_to_prior(0.5),
    }

    confs = _run_ep(claim_ids, operators, evidence=evidence)

    var_low = confs["c_low"]["variance"]
    var_high = confs["c_high"]["variance"]
    var_neutral = confs["c_neutral"]["variance"]

    # Low confidence IS informative (lower variance than uniform)
    # confidence 0.3 → Beta(1.6, 2.4) → var ≈ (1.6*2.4)/(4²*5) = 3.84/80 = 0.048
    # uniform Beta(1,1) → var = 1/(4*3) = 0.0833
    assert var_low < var_neutral, (
        f"Low confidence IS informative (narrower than uniform): "
        f"var_low={var_low:.5f} >= var_neutral={var_neutral:.5f}")

    # But weaker than high confidence
    assert var_low > var_high, (
        f"Low confidence should be wider than high confidence: "
        f"var_low={var_low:.5f} <= var_high={var_high:.5f}")

    # Mean should reflect direction
    mean_low = confs["c_low"]["mean"]
    mean_high = confs["c_high"]["mean"]
    assert mean_low < mean_high, (
        f"Low confidence claim should have lower mean than high: "
        f"mean_low={mean_low:.4f} >= mean_high={mean_high:.4f}")


# ═══════════════════════════════════════════════════════════════════
# Test 4: No evidence → Beta(1,1) uniform fallback
# ═══════════════════════════════════════════════════════════════════

@needs_falkor
def test_no_evidence_fallback():
    """If no evidence provided, EP uses Beta(1,1) uniform (backward compat).

    Isolated claim with no evidence and no operators → posterior = Beta(1,1).
    """
    claim_ids = ["c0"]
    operators = []

    confs = _run_ep(claim_ids, operators, evidence=None)

    c = confs["c0"]
    assert abs(c["alpha"] - 1.0) < 0.01, \
        f"No evidence → alpha=1.0, got {c['alpha']:.4f}"
    assert abs(c["beta"] - 1.0) < 0.01, \
        f"No evidence → beta=1.0, got {c['beta']:.4f}"
    assert abs(c["mean"] - 0.5) < 0.01, \
        f"Uniform prior → mean=0.5, got {c['mean']:.4f}"
    # Variance of Beta(1,1) = 1/(12) ... wait, let's check.
    # Var = (α*β)/((α+β)²*(α+β+1)) = 1/(4*3) = 1/12 ≈ 0.0833
    expected_var = 1.0 / 12.0
    assert abs(c["variance"] - expected_var) < 0.01, \
        f"Uniform prior variance should be ~0.0833, got {c['variance']:.4f}"


# ═══════════════════════════════════════════════════════════════════
# Test 5: evidence via run() overrides constructor evidence
# ═══════════════════════════════════════════════════════════════════

@needs_falkor
def test_run_evidence_overrides():
    """Evidence passed to run() takes precedence over constructor evidence."""
    db_path, tmpdir = _tmp("g_override.db")
    try:
        proj = FalkorProjection(db_path, graph_name="test")
        try:
            _build_graph(proj, ["c0"], [])
            # Constructor: evidence for c0 = (3,1) (strong positive)
            ep = TortoiseEP(proj, evidence={"c0": (3.0, 1.0)})
            # run(): evidence for c0 = (1,3) (strong negative)
            ep.run([], evidence={"c0": (1.0, 3.0)})

            c = ep.compute_confidence("c0")
            # Should use run() evidence: Beta(1,3) → mean = 0.25
            assert abs(c["alpha"] - 1.0) < 0.01, \
                f"run() evidence: alpha=1.0, got {c['alpha']:.4f}"
            assert abs(c["beta"] - 3.0) < 0.01, \
                f"run() evidence: beta=3.0, got {c['beta']:.4f}"
        finally:
            proj.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
# Test 6: confidence_to_prior calibration
# ═══════════════════════════════════════════════════════════════════

def test_confidence_to_prior_calibration():
    """Static method converts extractor confidence to Beta prior."""
    # confidence 0.5 → uniform
    a, b = TortoiseEP.confidence_to_prior(0.5)
    assert a == 1.0 and b == 1.0, f"0.5 → uniform, got ({a},{b})"

    # confidence 0.48 → within threshold → uniform
    a, b = TortoiseEP.confidence_to_prior(0.48)
    assert a == 1.0 and b == 1.0, f"0.48 → uniform, got ({a},{b})"

    # confidence 0.8 → Beta(2.6, 1.4)
    a, b = TortoiseEP.confidence_to_prior(0.8)
    assert abs(a - 2.6) < 0.01 and abs(b - 1.4) < 0.01, \
        f"0.8 → (2.6,1.4), got ({a},{b})"

    # confidence 0.2 → Beta(1.4, 2.6)
    a, b = TortoiseEP.confidence_to_prior(0.2)
    assert abs(a - 1.4) < 0.01 and abs(b - 2.6) < 0.01, \
        f"0.2 → (1.4,2.6), got ({a},{b})"

    # confidence 0.0 → Beta(1.0, 3.0) — extremely confident false
    a, b = TortoiseEP.confidence_to_prior(0.0)
    assert abs(a - 1.0) < 0.01 and abs(b - 3.0) < 0.01, \
        f"0.0 → (1,3), got ({a},{b})"


def test_confidence_to_prior_malformed_inputs():
    """Malformed confidence must fall back to uniform — never NaN/degenerate
    priors that silently zero downstream weights (#326)."""
    for bad in (None, True, False, float("nan"), float("inf"),
                float("-inf"), "not-a-number", object()):
        a, b = TortoiseEP.confidence_to_prior(bad)
        assert a == 1.0 and b == 1.0, f"{bad!r} → uniform, got ({a},{b})"

    # Out-of-range values are clamped to [0,1] before conversion.
    a, b = TortoiseEP.confidence_to_prior(3.0)
    assert abs(a - 3.0) < 0.01 and abs(b - 1.0) < 0.01, \
        f"3.0 clamps to 1.0 → (3,1), got ({a},{b})"
    a, b = TortoiseEP.confidence_to_prior(-2.0)
    assert abs(a - 1.0) < 0.01 and abs(b - 3.0) < 0.01, \
        f"-2.0 clamps to 0.0 → (1,3), got ({a},{b})"


# ═══════════════════════════════════════════════════════════════════
# Test 7: EP with evidence + IMPL chain (integration)
# ═══════════════════════════════════════════════════════════════════

@needs_falkor
def test_evidence_with_impl_chain():
    """Evidence prior propagates through IMPL chain.

    c0 has strong evidence (3,1) — confident it's true.
    c1 IMPL c2 — if source is true, destination should be pushed up.
    With uniform priors, c1 and c2 stay at 0.5.
    With evidence on c0, IMPL chain should propagate: c0→c1→c2.
    """
    claim_ids = ["c0", "c1", "c2"]
    operators = [
        ("IMPL_01", "IMPL", ["c0", "c1"]),
        ("IMPL_12", "IMPL", ["c1", "c2"]),
    ]

    evidence = {"c0": (3.0, 1.0)}  # c0 is confidently true

    confs = _run_ep(claim_ids, operators, evidence=evidence)

    # Without evidence, all means ≈ 0.5
    # With evidence c0 ≈ 0.75, IMPL pulls c1 up, then c2 up
    m0 = confs["c0"]["mean"]
    m1 = confs["c1"]["mean"]
    m2 = confs["c2"]["mean"]

    assert m0 > 0.6, f"c0 with evidence: mean={m0:.4f} should be >0.6"
    assert m1 > 0.5, f"c1 via IMPL from c0: mean={m1:.4f} should be >0.5"
    assert m2 > 0.5, f"c2 via IMPL chain: mean={m2:.4f} should be >0.5"

    # Evidence-dependent: the stronger the prior on c0, the more c2 is pulled
    # Run again without evidence
    confs_no_ev = _run_ep(claim_ids, operators, evidence=None)
    m2_no_ev = confs_no_ev["c2"]["mean"]
    assert m2 > m2_no_ev, (
        f"Evidence on c0 should push c2 higher: "
        f"with_ev={m2:.4f} <= no_ev={m2_no_ev:.4f}")


# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import pytest as _pytest
    sys.exit(_pytest.main([__file__, "-v"]))
