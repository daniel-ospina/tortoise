"""Algorithm comparison tests: SVBP vs EP (Expectation Propagation).

Compares Stein-based and quadrature-based moment projection
on NAND (multimodal) and IMPL (unimodal) factor graphs.
Tests particle count and SVGD step quality scaling.
"""
import sys, os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import random
import jax.numpy as jnp
import numpy as np
from tortoise.svbp import TortoiseSVBP, sigmoid
from tortoise.quadrature import tilted_moments, moments_to_beta, phi_nand, phi_impl


# ═══════════════════════════════════════════════════════════════════
# W₂ helper
# ═══════════════════════════════════════════════════════════════════

def _wasserstein_2_1d(a, b):
    """1D W₂ via sorted quantile matching."""
    a_s = jnp.sort(jnp.asarray(a).flatten())
    b_s = jnp.sort(jnp.asarray(b).flatten())
    n = min(len(a_s), len(b_s))
    if len(a_s) > n:
        idx = jnp.linspace(0, len(a_s) - 1, n, dtype=jnp.int32)
        a_s = a_s[idx]
    if len(b_s) > n:
        idx = jnp.linspace(0, len(b_s) - 1, n, dtype=jnp.int32)
        b_s = b_s[idx]
    return float(jnp.sqrt(jnp.mean((a_s - b_s) ** 2)))


# ═══════════════════════════════════════════════════════════════════
# In-memory EP solver (no FalkorDB)
# ═══════════════════════════════════════════════════════════════════

def _run_ep(factors, *, evidence=None, max_iter=80, damping=0.5, tol=1e-3):
    """Minimal EP solver.  Returns {claim_id: (alpha, beta)} posteriors."""
    messages: dict[tuple, tuple[float, float]] = {}
    posteriors: dict[str, tuple[float, float]] = {}

    # evidence priors
    ev_prior = dict(evidence) if evidence else {}

    all_cids = set(ev_prior.keys())
    for _, _, input_ids, _ in factors:
        all_cids.update(input_ids)

    def _nat(a, b):
        return (a - 1, b - 1)

    def _beta(e1, e2):
        return (max(e1 + 1, 0.01), max(e2 + 1, 0.01))

    def _msg(op_id, cid, rel):
        return messages.get((op_id, cid, rel), (0.0, 0.0))

    def _update_post(cid):
        e1, e2 = 0.0, 0.0
        # start from evidence
        if cid in ev_prior:
            ea, eb = ev_prior[cid]
            et1, et2 = _nat(ea, eb)
            e1 += et1
            e2 += et2
        for (oid, c, rel), (m1, m2) in messages.items():
            if c == cid:
                e1 += m1
                e2 += m2
        posteriors[cid] = _beta(e1, e2)

    # Initialize
    for cid in all_cids:
        ea, eb = ev_prior.get(cid, (1.0, 1.0))
        e1, e2 = _nat(ea, eb)
        posteriors[cid] = _beta(e1, e2)

    prev = dict(posteriors)

    for _ in range(max_iter):
        random.shuffle(factors)
        for op_id, op_type, input_ids, weight in factors:
            if len(input_ids) != 2:
                continue
            id_a, id_b = input_ids

            pa = posteriors.get(id_a, (1.0, 1.0))
            pb = posteriors.get(id_b, (1.0, 1.0))
            pe_a = _nat(*pa)
            pe_b = _nat(*pb)
            me_a = _msg(op_id, id_a, op_type)
            me_b = _msg(op_id, id_b, op_type)

            cav_a = _beta(pe_a[0] - me_a[0], pe_a[1] - me_a[1])
            cav_b = _beta(pe_b[0] - me_b[0], pe_b[1] - me_b[1])

            phi = phi_nand if op_type == "NAND" else phi_impl
            mom_a, mom_b = tilted_moments(*cav_a, *cav_b, weight, phi)
            new_a, new_b = moments_to_beta(*mom_a), moments_to_beta(*mom_b)
            ne_a = _nat(*new_a)
            ne_b = _nat(*new_b)

            ca = _nat(*cav_a)
            cb = _nat(*cav_b)
            raw_a = (ne_a[0] - ca[0], ne_a[1] - ca[1])
            raw_b = (ne_b[0] - cb[0], ne_b[1] - cb[1])

            d = damping
            oa = _msg(op_id, id_a, op_type)
            ob = _msg(op_id, id_b, op_type)
            da = (d * raw_a[0] + (1 - d) * oa[0], d * raw_a[1] + (1 - d) * oa[1])
            db = (d * raw_b[0] + (1 - d) * ob[0], d * raw_b[1] + (1 - d) * ob[1])
            clamp = 1000
            da = (max(min(da[0], clamp), -clamp), max(min(da[1], clamp), -clamp))
            db = (max(min(db[0], clamp), -clamp), max(min(db[1], clamp), -clamp))

            messages[(op_id, id_a, op_type)] = da
            messages[(op_id, id_b, op_type)] = db
            _update_post(id_a)
            _update_post(id_b)

        max_change = 0.0
        for cid in posteriors:
            na, nb = posteriors[cid]
            oa, ob = prev.get(cid, (1.0, 1.0))
            c = max(abs(na - oa) / max(oa, 1e-6), abs(nb - ob) / max(ob, 1e-6))
            max_change = max(max_change, c)
            prev[cid] = (na, nb)
        if max_change < tol:
            break

    return posteriors


def _beta_variance(alpha, beta):
    """Var[Beta(α, β)]."""
    t = alpha + beta
    return (alpha * beta) / (t * t * (t + 1))


# ═══════════════════════════════════════════════════════════════════
# Test 1: SVBP vs EP on NAND — particle variance > Beta variance
# ═══════════════════════════════════════════════════════════════════

def test_svbp_vs_ep_nand():
    """Overconstrained NAND graph: c0 connected to c1 AND c2 via NAND.

    SVBP particles explore the full 3-claim joint space.  The actual
    particle distribution for a NAND-constrained claim is multimodal
    (particles split into high/low camps).  EP's moment-matched Beta
    fits a single-mode distribution — it compensates with higher
    variance, but the raw particle variance should exceed it because
    a Beta cannot represent two distinct modes.

    Assert: SVBP particle variance ≥ EP fitted Beta variance for NAND-ed claims.
            At least 2 claims show particle variance > 1.5× EP variance.
    """
    factors = [
        ("NAND_01", "NAND", ["c0", "c1"], 4.0),
        ("NAND_02", "NAND", ["c0", "c2"], 4.0),
        ("NAND_03", "NAND", ["c1", "c2"], 4.0),
        ("NAND_04", "NAND", ["c2", "c3"], 4.0),
        ("NAND_05", "NAND", ["c3", "c4"], 4.0),
    ]

    # ── SVBP ──────────────────────────────────────────────────────
    random.seed(42)
    svbp = TortoiseSVBP(n_particles=100, n_svgd_steps=20, svgd_lr=0.01,
                        damping=0.5, max_iter=80, tol=5e-3, seed=42)
    svbp.run(factors)

    # Particle variance (actual samples, pre-Beta-projection)
    svbp_particle_var = {}
    for cid in [f"c{i}" for i in range(5)]:
        if svbp._has_particles(cid):
            y = svbp._particles[cid]
            c_vals = sigmoid(y)
            svbp_particle_var[cid] = float(jnp.var(c_vals))
        else:
            # ponytail: fall back to Beta posterior variance if compressed
            svbp_particle_var[cid] = svbp.compute_confidence(cid)["variance"]

    # ── EP ────────────────────────────────────────────────────────
    random.seed(42)
    ep_posts = _run_ep(factors)

    ep_var = {}
    for cid in [f"c{i}" for i in range(5)]:
        a, b = ep_posts.get(cid, (1.0, 1.0))
        ep_var[cid] = _beta_variance(a, b)

    # ── Assertions ────────────────────────────────────────────────
    ratio_count = 0
    for cid in svbp_particle_var:
        pv = svbp_particle_var[cid]
        ev = ep_var[cid]
        assert pv >= ev, \
            f"{cid}: SVBP particle var ({pv:.5f}) < EP var ({ev:.5f}) — multimodality not captured"
        if pv > 1.5 * ev:
            ratio_count += 1

    assert ratio_count >= 2, \
        f"Only {ratio_count}/5 claims have particle var > 1.5× EP Beta var (need ≥ 2)"


# ═══════════════════════════════════════════════════════════════════
# Test 2: SVBP vs EP on IMPL chain (unimodal agreement)
# ═══════════════════════════════════════════════════════════════════

def test_svbp_vs_ep_impl():
    """5 IMPL factors: c0→c1→c2→c3→c4, anchored by evidence at ends.

    Evidence: c0 ~ Beta(5,2) (high), c4 ~ Beta(2,5) (low).
    The IMPL chain interpolates between them.  Both SVBP and EP
    should agree on these unimodal target posteriors.
    W₂ < 0.03 for all claims.
    """
    factors = [
        ("IMPL_01", "IMPL", ["c0", "c1"], 2.0),
        ("IMPL_12", "IMPL", ["c1", "c2"], 2.0),
        ("IMPL_23", "IMPL", ["c2", "c3"], 2.0),
        ("IMPL_34", "IMPL", ["c3", "c4"], 2.0),
    ]
    evidence = {"c0": (5.0, 2.0), "c4": (2.0, 5.0)}

    # ── SVBP ──────────────────────────────────────────────────────
    random.seed(42)
    svbp = TortoiseSVBP(n_particles=50, n_svgd_steps=15, svgd_lr=0.01,
                        damping=0.5, max_iter=100, tol=5e-3, seed=42)
    svbp.run(factors, evidence=evidence)

    # ── EP ────────────────────────────────────────────────────────
    random.seed(42)
    ep_posts = _run_ep(factors, evidence=evidence, max_iter=100)

    # ── Assertions ────────────────────────────────────────────────
    for cid in [f"c{i}" for i in range(5)]:
        sv_a, sv_b = svbp._get_posterior(cid)
        ep_a, ep_b = ep_posts.get(cid, (1.0, 1.0))
        n_samples = 3000
        sv_samples = np.random.beta(sv_a, sv_b, n_samples)
        ep_samples = np.random.beta(ep_a, ep_b, n_samples)
        w2 = _wasserstein_2_1d(sv_samples, ep_samples)
        assert w2 < 0.03, \
            f"{cid}: W₂ = {w2:.4f} ≥ 0.03 "
        f"(SVBP Beta({sv_a:.2f},{sv_b:.2f}) vs EP Beta({ep_a:.2f},{ep_b:.2f}))"


# ═══════════════════════════════════════════════════════════════════
# Test 3: particle count → variance scaling
# ═══════════════════════════════════════════════════════════════════

_NAND_GRAPH_3 = [
    ("NAND_01", "NAND", ["c0", "c1"], 4.0),
    ("NAND_02", "NAND", ["c0", "c2"], 4.0),
    ("NAND_03", "NAND", ["c1", "c3"], 4.0),
    ("NAND_04", "NAND", ["c2", "c3"], 4.0),
    ("NAND_05", "NAND", ["c2", "c4"], 4.0),
    ("NAND_06", "NAND", ["c3", "c5"], 4.0),
    ("NAND_07", "NAND", ["c4", "c5"], 4.0),
]


def test_particle_count_quality():
    """Run same NAND-heavy graph with n_particles ∈ {10, 20, 40, 80},
    average over 3 seeds per count to reduce stochastic noise.

    More particles → better representation of multimodal uncertainty
    → higher raw particle variance.  Assert v80 > 1.2 × v10 for at least
    3 claims.
    """
    particle_counts = [10, 20, 40, 80]
    seeds = [42, 123, 999]
    runs: dict[int, dict[str, float]] = {}

    for np_c in particle_counts:
        # Average variance across seeds
        claim_vars: dict[str, list[float]] = {}
        for s in seeds:
            random.seed(s)
            svbp = TortoiseSVBP(n_particles=np_c, n_svgd_steps=20, svgd_lr=0.01,
                                damping=0.5, max_iter=100, tol=5e-3, seed=s)
            svbp.run(_NAND_GRAPH_3)
            for cid in [f"c{i}" for i in range(6)]:
                if svbp._has_particles(cid):
                    y = svbp._particles[cid]
                    v = float(jnp.var(sigmoid(y)))
                else:
                    v = svbp.compute_confidence(cid)["variance"]
                claim_vars.setdefault(cid, []).append(v)
        runs[np_c] = {cid: float(np.mean(vals)) for cid, vals in claim_vars.items()}

    # Assert: v80 > 1.2 × v10 for at least 3 claims
    qualified = 0
    for cid in [f"c{i}" for i in range(6)]:
        v10 = runs[10][cid]
        v80 = runs[80][cid]
        if v10 <= 0:
            continue
        if v80 > 1.2 * v10:
            qualified += 1

    assert qualified >= 3, \
        f"Only {qualified}/6 claims show v80 > 1.2× v10 (expected ≥ 3)"


# ═══════════════════════════════════════════════════════════════════
# Test 4: SVGD steps → convergence speed
# ═══════════════════════════════════════════════════════════════════

_IMPL_CHAIN = [
    ("IMPL_01", "IMPL", ["c0", "c1"], 2.0),
    ("IMPL_12", "IMPL", ["c1", "c2"], 2.0),
    ("IMPL_23", "IMPL", ["c2", "c3"], 2.0),
    ("IMPL_34", "IMPL", ["c3", "c4"], 2.0),
    ("IMPL_45", "IMPL", ["c4", "c5"], 2.0),
    ("IMPL_56", "IMPL", ["c5", "c6"], 2.0),
]


def test_svgd_steps_vs_quality():
    """Run same IMPL chain with n_svgd_steps ∈ {3, 6, 12, 24}.

    IMPL converges to a unique fixpoint (no camp ambiguity), so more
    inner SVGD steps per factor should directly reduce outer EP
    iterations.  Assert: 24-step convergence iteration count ≤ 0.7×
    the 3-step count.
    """
    step_counts = [3, 6, 12, 24]
    iterations = {}

    for n_steps in step_counts:
        random.seed(42)
        svbp = TortoiseSVBP(n_particles=30, n_svgd_steps=n_steps, svgd_lr=0.01,
                            damping=0.5, max_iter=300, tol=5e-3, seed=42)
        n_iter, converged = svbp.run(_IMPL_CHAIN, evidence={"c0": (5.0, 2.0)})
        assert converged, f"{n_steps}-step did not converge within {n_iter} iters"
        iterations[n_steps] = n_iter

    ratio = iterations[24] / iterations[3]
    assert ratio <= 0.70, \
        f"24-step ({iterations[24]} iters) / 3-step ({iterations[3]} iters) = {ratio:.2f}, expected ≤ 0.70"


# ═══════════════════════════════════════════════════════════════════
# CLI runner
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    tests = [
        ("SVBP vs EP NAND (particle var)", test_svbp_vs_ep_nand),
        ("SVBP vs EP IMPL chain", test_svbp_vs_ep_impl),
        ("Particle count → variance scaling", test_particle_count_quality),
        ("SVGD steps → convergence speed", test_svgd_steps_vs_quality),
    ]
    passed = 0
    for name, fn in tests:
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")
        try:
            fn()
            print(f"  ✓ PASS")
            passed += 1
        except AssertionError as e:
            print(f"  ✗ FAIL — {e}")
        except Exception as e:
            import traceback

            traceback.print_exc()
            print(f"  ✗ ERROR — {type(e).__name__}: {e}")
    print(f"\n{passed}/{len(tests)} passed")
