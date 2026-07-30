"""E026 Experiment 5: Head-to-head benchmark of camp detection methods.

Compares 4 methods on 30 random graphs + 10 negative controls:
  A. Beta mixture EP — fit 2-component mixture, |μ₁-μ₂| > 0.15
  B. Mini-SVBP (8 particles, 5 steps) — camp_frac from particles
  C. One-shot probe — ||repulsion||/||drift|| ratio
  D. Full SVBP (50 particles, 20 steps) — ground truth camp_frac

Measures: precision, recall, F1, runtime per graph.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import time
import numpy as np
import pytest
from scipy.stats import norm as scipy_norm

import jax.numpy as jnp
import jax
import jax.random as jrandom

from tortoise.svbp import (
    TortoiseSVBP, sigmoid,
    rbf_kernel, median_heuristic,
    _tilt_grad_batch,
)
from tortoise.quadrature import tilted_moments, moments_to_beta, phi_nand, phi_impl
from test_svbp_hybrid import InMemoryEP


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def _camp_frac(particles_a, particles_b):
    """Fraction of particles in the smaller off-diagonal quadrant (median-split)."""
    c_a = sigmoid(particles_a)
    c_b = sigmoid(particles_b)
    med_a = float(jnp.median(c_a))
    med_b = float(jnp.median(c_b))
    hl = int(jnp.sum((c_a > med_a) & (c_b <= med_b)))
    lh = int(jnp.sum((c_a <= med_a) & (c_b > med_b)))
    return min(hl, lh) / float(len(c_a))


def _random_graph(seed, n_claims, n_nand, nand_weight_range, evidence_strength):
    """Generate (nand_factors, impl_factors, claim_ids, nand_pairs).

    nand_pairs is list of (cid_a, cid_b) for each NAND factor for labeling.
    """
    rng = np.random.default_rng(seed)
    claim_ids = [f"c{i}" for i in range(n_claims)]

    # Chain IMPL to keep graph connected
    impl_factors = []
    for i in range(n_claims - 1):
        w = float(rng.uniform(0.5, 2.0))
        impl_factors.append((f"IMPL_{i}_{i+1}", "IMPL", [claim_ids[i], claim_ids[i + 1]], w))

    # Random NAND edges (no self-loops, no duplicates)
    nand_factors = []
    nand_pairs = []
    pairs = set()
    while len(nand_factors) < n_nand:
        a, b = int(rng.integers(0, n_claims)), int(rng.integers(0, n_claims))
        if a == b or (a, b) in pairs or (b, a) in pairs:
            continue
        pairs.add((a, b))
        w = float(rng.uniform(*nand_weight_range))
        nand_factors.append(
            (f"NAND_{len(nand_factors)}", "NAND", [claim_ids[a], claim_ids[b]], w)
        )
        nand_pairs.append((claim_ids[a], claim_ids[b]))

    # Evidence: alternating strong/weak on a subset of claims
    evidence = {}
    ev_cids = claim_ids[:min(3, n_claims)]
    for i, cid in enumerate(ev_cids):
        if i % 2 == 0:
            evidence[cid] = (evidence_strength, 1.0)
        else:
            evidence[cid] = (1.0, evidence_strength)

    return nand_factors, impl_factors, claim_ids, nand_pairs, evidence


def _negative_control_graph(seed):
    """Generate IMPL-only graph with synthetic zero-weight NAND probes.

    The NAND factors have w=0.0 (no-op, exp(0)=1) so no camps should form.
    Methods process these as normal NAND factors and MUST predict 'no camps'.
    """
    rng = np.random.default_rng(seed)
    n_claims = int(rng.integers(3, 6))
    claim_ids = [f"c{i}" for i in range(n_claims)]

    # IMPL chain
    impl_factors = [
        (f"IMPL_{i}_{i+1}", "IMPL", [claim_ids[i], claim_ids[i + 1]], 1.0)
        for i in range(n_claims - 1)
    ]

    # Zero-weight NAND probes: methods see NAND factors but no anti-correlation exists
    nand_factors = []
    n_nand = 2
    pairs = set()
    while len(nand_factors) < n_nand:
        a, b = int(rng.integers(0, n_claims)), int(rng.integers(0, n_claims))
        if a == b or (a, b) in pairs or (b, a) in pairs:
            continue
        pairs.add((a, b))
        nand_factors.append(
            (f"NAND_{len(nand_factors)}", "NAND", [claim_ids[a], claim_ids[b]], 0.0)
        )

    evidence = {cid: (3.0, 3.0) for cid in claim_ids[:2]}

    return nand_factors, impl_factors, claim_ids, evidence


def _compute_ground_truth(seed, nand_factors, impl_factors, claim_ids, evidence):
    """Run full SVBP (50 particles, 20 steps) and return camp_frac per NAND pair."""
    all_factors = nand_factors + impl_factors
    svbp = TortoiseSVBP(
        n_particles=50, n_svgd_steps=20, svgd_lr=0.005,
        damping=0.5, max_iter=50, tol=5e-3, seed=seed,
    )
    t0 = time.monotonic()
    svbp.run(all_factors, evidence=evidence)
    runtime = time.monotonic() - t0

    camp_fracs = {}
    for cid_a, cid_b in [(f["inputs"][0], f["inputs"][1]) for _, _, f_inputs, _ in nand_factors]:
        key_tuple = (cid_a, cid_b)
        # Find the factor inputs tuple — need to use the actual factor list
        pass
    # Use NAND factor list directly
    for op_id, op_type, inputs, weight in nand_factors:
        id_a, id_b = inputs
        if id_a in svbp._particles and id_b in svbp._particles:
            cf = _camp_frac(svbp._particles[id_a], svbp._particles[id_b])
            camp_fracs[(id_a, id_b)] = cf
        else:
            camp_fracs[(id_a, id_b)] = 0.0
    return camp_fracs, runtime


def _compute_ground_truth_from_graph(nand_factors, impl_factors, claim_ids, evidence, seed):
    """Run full SVBP (50 particles, 20 steps) → camp_frac per NAND pair + runtime."""
    all_factors = list(nand_factors) + list(impl_factors)
    svbp = TortoiseSVBP(
        n_particles=50, n_svgd_steps=20, svgd_lr=0.005,
        damping=0.5, max_iter=50, tol=5e-3, seed=seed,
    )
    t0 = time.monotonic()
    svbp.run(all_factors, evidence=evidence)
    runtime = time.monotonic() - t0

    camp_fracs = {}
    for _, _, inputs, _ in nand_factors:
        id_a, id_b = inputs
        if id_a in svbp._particles and id_b in svbp._particles:
            cf = _camp_frac(svbp._particles[id_a], svbp._particles[id_b])
            camp_fracs[(id_a, id_b)] = cf
        else:
            camp_fracs[(id_a, id_b)] = 0.0
    return camp_fracs, runtime


# ═══════════════════════════════════════════════════════════════════
# Method A: Beta Mixture EP
# ═══════════════════════════════════════════════════════════════════

def _tilted_marginal_moments_k(alpha_a, beta_a, alpha_b, beta_b, w, n_quad=12, k_max=4):
    """First k_max raw moments of c_a marginal under NAND-tilted distribution.

    Uses Gauss-Jacobi quadrature over both variables.
    """
    from tortoise.quadrature import gauss_jacobi_01
    x_a, w_a = gauss_jacobi_01(n_quad, alpha_a, beta_a)
    x_b, w_b = gauss_jacobi_01(n_quad, alpha_b, beta_b)
    moments = np.zeros(k_max)
    Z = 0.0
    for i in range(n_quad):
        ca = x_a[i]
        ca_pow = np.array([ca ** k for k in range(1, k_max + 1)])
        for j in range(n_quad):
            wt = w_a[i] * w_b[j] * np.exp(-w * ca * x_b[j])
            Z += wt
            moments += wt * ca_pow
    if Z < 1e-30:
        return np.array([np.sum(w_a * x_a ** k) for k in range(1, k_max + 1)])
    return moments / Z


def _method_a_beta_mixture(nand_factors, impl_factors, claim_ids, evidence):
    """Fit 2-component Beta mixture to EP posterior, detect camps via |μ₁-μ₂| > 0.15.

    Returns: dict {(id_a, id_b): has_camps (bool)}, runtime.
    """
    from scipy.optimize import minimize
    t0 = time.monotonic()

    # Run EP on all factors
    ep = InMemoryEP()
    ep.run(nand_factors + impl_factors, evidence=evidence)

    def _beta_moment(alpha, beta, k):
        """E[X^k] for X ~ Beta(α,β)."""
        if k == 0:
            return 1.0
        val = 1.0
        for i in range(int(k)):
            val *= (alpha + i) / (alpha + beta + i)
        return val

    def _fit_mixture(target_moments):
        """Fit 2-component Beta mixture (w=0.5) to 4 moments. Returns |μ₁-μ₂|."""
        def mixture_moments(params):
            a1, b1, a2, b2 = params
            return np.array([
                0.5 * _beta_moment(a1, b1, k) + 0.5 * _beta_moment(a2, b2, k)
                for k in range(1, 5)
            ])

        def err(params):
            pred = mixture_moments(params)
            # Relative error to handle small moments
            rel = (pred - target_moments) / (np.abs(target_moments) + 1e-12)
            return float(np.sum(rel ** 2))

        inits = [
            [0.3, 3.0, 5.0, 0.5], [5.0, 0.5, 0.3, 3.0],
            [2.0, 8.0, 8.0, 2.0], [1.0, 4.0, 4.0, 1.0],
            [0.5, 0.5, 10.0, 1.0], [3.0, 7.0, 0.5, 0.3],
        ]
        bounds = [(0.01, 50.0)] * 4
        best_sep = 0.0
        for init in inits:
            res = minimize(err, init, method='L-BFGS-B', bounds=bounds,
                          options={'maxiter': 1000, 'ftol': 1e-12})
            a1, b1, a2, b2 = res.x
            sep = abs(a1 / (a1 + b1) - a2 / (a2 + b2))
            if sep > best_sep:
                best_sep = sep
        return best_sep

    results = {}
    for _, _, inputs, weight in nand_factors:
        id_a, id_b = inputs
        post_a = ep.posteriors.get(id_a, (1.0, 1.0))
        post_b = ep.posteriors.get(id_b, (1.0, 1.0))
        # Tilted marginal moments of c_a
        target = _tilted_marginal_moments_k(
            post_a[0], post_a[1], post_b[0], post_b[1], weight, n_quad=14, k_max=4
        )
        sep = _fit_mixture(target)
        results[(id_a, id_b)] = sep > 0.15

    runtime = time.monotonic() - t0
    return results, runtime


# ═══════════════════════════════════════════════════════════════════
# Method B: Mini-SVBP (8 particles, 5 steps)
# ═══════════════════════════════════════════════════════════════════

def _method_b_mini_svbp(nand_factors, impl_factors, claim_ids, evidence, seed):
    """Mini-SVBP (8 particles, 5 steps) → camp_frac per NAND pair.

    Returns: dict {(id_a, id_b): has_camps (bool)}, runtime.
    """
    t0 = time.monotonic()
    all_factors = list(nand_factors) + list(impl_factors)
    svbp = TortoiseSVBP(
        n_particles=8, n_svgd_steps=5, svgd_lr=0.01,
        damping=0.5, max_iter=30, tol=0.01, seed=seed,
    )
    svbp.run(all_factors, evidence=evidence)

    results = {}
    for _, _, inputs, _ in nand_factors:
        id_a, id_b = inputs
        if id_a in svbp._particles and id_b in svbp._particles:
            cf = _camp_frac(svbp._particles[id_a], svbp._particles[id_b])
            results[(id_a, id_b)] = cf >= 0.25
        else:
            results[(id_a, id_b)] = False
    runtime = time.monotonic() - t0
    return results, runtime


# ═══════════════════════════════════════════════════════════════════
# Method C: One-shot probe (||repulsion||/||drift||)
# ═══════════════════════════════════════════════════════════════════

def _method_c_oneshot_probe(nand_factors, impl_factors, claim_ids, evidence):
    """One-shot SVGD probe: differential spread increase (NAND vs IMPL).

    For each NAND pair, sample particles from EP cavity, run one SVGD step
    for NAND and one for IMPL. If Δ_spread(NAND) > Δ_spread(IMPL) + margin,
    camps are forming (NAND pushes particles apart more than IMPL does).

    Returns: dict {(id_a, id_b): has_camps (bool)}, runtime.
    """
    t0 = time.monotonic()
    n_p = 25
    lr = 0.01

    ep = InMemoryEP()
    ep.run(nand_factors + impl_factors, evidence=evidence)

    def _pairwise_dist_variance(y):
        n = y.shape[0]
        diff = y[:, None, :] - y[None, :, :]
        dists = jnp.sqrt(jnp.sum(diff ** 2, axis=-1) + 1e-12)
        triu = dists[jnp.triu_indices(n, k=1)]
        return float(jnp.var(triu))

    def _one_step_spread_delta(y_init, cav_a, cav_b, is_nand_val, w):
        grad_lp = _tilt_grad_batch(
            y_init, cav_a[0], cav_a[1], cav_b[0], cav_b[1],
            is_nand_val, w,
        )
        h = median_heuristic(y_init) + 0.1
        K, grad_K = rbf_kernel(y_init, h)
        n = y_init.shape[0]
        term1 = jnp.dot(K, grad_lp) / n
        term2 = jnp.sum(grad_K, axis=0) / n
        y_new = y_init + lr * (term1 + term2)
        return _pairwise_dist_variance(y_new) - _pairwise_dist_variance(y_init)

    results = {}
    key = jrandom.PRNGKey(42)
    for _, _, inputs, weight in nand_factors:
        id_a, id_b = inputs
        post_a = ep.posteriors.get(id_a, (1.0, 1.0))
        post_b = ep.posteriors.get(id_b, (1.0, 1.0))

        # Sample particles from EP posterior
        key, sk_a, sk_b = jrandom.split(key, 3)
        c_a = jrandom.beta(sk_a, post_a[0], post_a[1], (n_p,))
        c_b = jrandom.beta(sk_b, post_b[0], post_b[1], (n_p,))
        y_init = jnp.stack([
            jnp.log(c_a + 1e-8) - jnp.log(1 - c_a + 1e-8),
            jnp.log(c_b + 1e-8) - jnp.log(1 - c_b + 1e-8),
        ], axis=-1)

        cav = (post_a[0], post_a[1], post_b[0], post_b[1])
        delta_nand = _one_step_spread_delta(
            y_init, (post_a[0], post_a[1]), (post_b[0], post_b[1]), 1.0, weight
        )
        delta_impl = _one_step_spread_delta(
            y_init, (post_a[0], post_a[1]), (post_b[0], post_b[1]), 0.0, weight
        )
        # NAND should push particles apart more than IMPL
        results[(id_a, id_b)] = (delta_nand - delta_impl) > 1e-6

    runtime = time.monotonic() - t0
    return results, runtime


# ═══════════════════════════════════════════════════════════════════
# Metrics
# ═══════════════════════════════════════════════════════════════════

def _prf(pred_labels, true_labels):
    """Precision, recall, F1."""
    tp = sum(1 for p, t in zip(pred_labels, true_labels) if p and t)
    fp = sum(1 for p, t in zip(pred_labels, true_labels) if p and not t)
    fn = sum(1 for p, t in zip(pred_labels, true_labels) if not p and t)
    p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f = 2 * p * r / (p + r) if (p + r) > 0 else 0.0
    return p, r, f


def _wilson_ci(p, n, z=1.96):
    """Wilson score confidence interval for binomial proportion."""
    if n == 0:
        return 0.0, 0.0
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    margin = z * np.sqrt((p * (1 - p) + z**2 / (4 * n)) / n) / denom
    lo = max(0.0, center - margin)
    hi = min(1.0, center + margin)
    return lo, hi


# ═══════════════════════════════════════════════════════════════════
# Benchmark runner
# ═══════════════════════════════════════════════════════════════════

class CampBenchmark:
    """Runs all 4 methods across 30 random graphs + 10 negative controls."""

    def __init__(self):
        self.results = {}  # method → {precision, recall, f1, runtimes[]}
        self.neg_fpr = {}  # method → false_positive_rate
        self.all_pred_true = []  # (method, pred, true) pairs for head-to-head

    def run(self, n_graphs=30, n_neg=10):
        for seed in range(n_graphs):
            self._run_one_graph(seed)

        for seed in range(100, 100 + n_neg):
            self._run_one_negative(seed)

    def _run_one_graph(self, seed):
        rng = np.random.default_rng(seed)
        n_claims = int(rng.integers(3, 8))
        n_nand = int(rng.integers(1, min(4, n_claims - 1) + 1))
        nand_weight_range = (float(rng.uniform(1.0, 3.0)), float(rng.uniform(3.0, 8.0)))
        evidence_strength = float(rng.uniform(2.0, 6.0))

        nand_f, impl_f, cids, nand_pairs, ev = _random_graph(
            seed, n_claims, n_nand, nand_weight_range, evidence_strength
        )

        # Ground truth (Method D): full SVBP
        gt_camp_fracs, gt_runtime = _compute_ground_truth_from_graph(
            nand_f, impl_f, cids, ev, seed + 1000
        )
        gt_labels = [(gt_camp_fracs.get((id_a, id_b), 0.0) >= 0.25)
                     for _, _, (id_a, id_b), _ in nand_f]

        # Method A: Beta mixture EP
        a_pred, a_rt = _method_a_beta_mixture(nand_f, impl_f, cids, ev)
        a_labels = [a_pred.get((id_a, id_b), False) for _, _, (id_a, id_b), _ in nand_f]
        self._record("A_beta_mix", a_labels, gt_labels, a_rt)

        # Method B: Mini-SVBP
        b_pred, b_rt = _method_b_mini_svbp(nand_f, impl_f, cids, ev, seed + 2000)
        b_labels = [b_pred.get((id_a, id_b), False) for _, _, (id_a, id_b), _ in nand_f]
        self._record("B_mini_svbp", b_labels, gt_labels, b_rt)

        # Method C: One-shot probe
        c_pred, c_rt = _method_c_oneshot_probe(nand_f, impl_f, cids, ev)
        c_labels = [c_pred.get((id_a, id_b), False) for _, _, (id_a, id_b), _ in nand_f]
        self._record("C_oneshot", c_labels, gt_labels, c_rt)

        # Method D: Full SVBP runtime (already measured)
        self.results.setdefault("D_full_svbp", {"runtimes": []})
        self.results["D_full_svbp"]["runtimes"].append(gt_runtime)

    def _run_one_negative(self, seed):
        nand_f, impl_f, cids, ev = _negative_control_graph(seed)
        pairs = [(id_a, id_b) for _, _, (id_a, id_b), _ in nand_f]
        if not pairs:
            return

        # Method A
        a_pred, _ = _method_a_beta_mixture(nand_f, impl_f, cids, ev)
        a_labels = [a_pred.get(p, False) for p in pairs]
        self._record_neg("A_beta_mix", a_labels)

        # Method B
        b_pred, _ = _method_b_mini_svbp(nand_f, impl_f, cids, ev, seed + 3000)
        b_labels = [b_pred.get(p, False) for p in pairs]
        self._record_neg("B_mini_svbp", b_labels)

        # Method C
        c_pred, _ = _method_c_oneshot_probe(nand_f, impl_f, cids, ev)
        c_labels = [c_pred.get(p, False) for p in pairs]
        self._record_neg("C_oneshot", c_labels)

    def _record(self, method, preds, truths, runtime):
        r = self.results.setdefault(method, {"preds": [], "truths": [], "runtimes": []})
        r["preds"].extend(preds)
        r["truths"].extend(truths)
        r["runtimes"].append(runtime)
        for p, t in zip(preds, truths):
            self.all_pred_true.append((method, p, t))

    def _record_neg(self, method, preds):
        r = self.neg_fpr.setdefault(method, [])
        r.extend(preds)

    def f1_scores(self):
        scores = {}
        for method in ["A_beta_mix", "B_mini_svbp", "C_oneshot"]:
            d = self.results.get(method, {"preds": [], "truths": []})
            p, r, f = _prf(d["preds"], d["truths"])
            n = len(d["preds"])
            ci_lo, ci_hi = _wilson_ci(f, n) if n > 0 else (0.0, 0.0)
            scores[method] = {"precision": p, "recall": r, "f1": f,
                             "f1_ci": (ci_lo, ci_hi), "n": n}
        return scores

    def runtime_stats(self):
        stats = {}
        for method in self.results:
            rts = self.results[method]["runtimes"]
            if rts:
                stats[method] = {
                    "mean": np.mean(rts), "std": np.std(rts),
                    "min": np.min(rts), "max": np.max(rts),
                }
        return stats

    def false_positive_rates(self):
        rates = {}
        for method, preds in self.neg_fpr.items():
            if preds:
                fpr = sum(preds) / len(preds)
                ci_lo, ci_hi = _wilson_ci(fpr, len(preds))
                rates[method] = {"fpr": fpr, "ci": (ci_lo, ci_hi), "n": len(preds)}
        return rates


# ═══════════════════════════════════════════════════════════════════
# Fixture: run once, share across tests
# ═══════════════════════════════════════════════════════════════════

_BENCHMARK_CACHE = None

def _get_benchmark():
    global _BENCHMARK_CACHE
    if _BENCHMARK_CACHE is None:
        bm = CampBenchmark()
        bm.run(n_graphs=30, n_neg=10)
        _BENCHMARK_CACHE = bm
    return _BENCHMARK_CACHE


# ═══════════════════════════════════════════════════════════════════
# TESTS
# ═══════════════════════════════════════════════════════════════════

def test_benchmark_f1():
    """Best method achieves F1 ≥ 0.70 vs full SVBP ground truth."""
    bm = _get_benchmark()
    scores = bm.f1_scores()
    best_method = max(scores, key=lambda m: scores[m]["f1"])
    best_f1 = scores[best_method]["f1"]
    ci_lo, ci_hi = scores[best_method]["f1_ci"]

    print(f"\n  F1 Scores (vs full SVBP ground truth):")
    for method in ["A_beta_mix", "B_mini_svbp", "C_oneshot"]:
        s = scores[method]
        lo, hi = s["f1_ci"]
        print(f"    {method}: P={s['precision']:.3f} R={s['recall']:.3f} "
              f"F1={s['f1']:.3f} [95% CI: {lo:.3f}–{hi:.3f}] n={s['n']}")

    assert best_f1 >= 0.70, (
        f"Best method {best_method} F1={best_f1:.3f} [CI: {ci_lo:.3f}–{ci_hi:.3f}] < 0.70"
    )
    print(f"  ✓ Best: {best_method} F1={best_f1:.3f}")


def test_benchmark_speed():
    """Best method runtime < 0.1s per graph (vs full SVBP ~2s)."""
    bm = _get_benchmark()
    stats = bm.runtime_stats()

    print(f"\n  Runtime per graph (seconds):")
    for method in ["D_full_svbp", "A_beta_mix", "B_mini_svbp", "C_oneshot"]:
        if method in stats:
            s = stats[method]
            print(f"    {method}: μ={s['mean']:.4f}s σ={s['std']:.4f}s "
                  f"range=[{s['min']:.4f}, {s['max']:.4f}]")

    # Method B should be fastest — only 8 particles, 5 steps
    b_stats = stats.get("B_mini_svbp", {"mean": 999})
    assert b_stats["mean"] < 0.1, (
        f"Mini-SVBP runtime {b_stats['mean']:.4f}s ≥ 0.1s"
    )
    # Full SVBP should be clearly slower
    d_mean = stats.get("D_full_svbp", {"mean": 0})["mean"]
    speedup = d_mean / b_stats["mean"] if b_stats["mean"] > 0 else 0
    print(f"    Speedup over full SVBP: {speedup:.1f}×")


def test_negative_controls():
    """All methods have false positive rate ≤ 0.20 on no-camp graphs."""
    bm = _get_benchmark()
    rates = bm.false_positive_rates()

    print(f"\n  False positive rates on negative control graphs:")
    all_ok = True
    for method in ["A_beta_mix", "B_mini_svbp", "C_oneshot"]:
        if method in rates:
            r = rates[method]
            ci_lo, ci_hi = r["ci"]
            print(f"    {method}: FPR={r['fpr']:.3f} [95% CI: {ci_lo:.3f}–{ci_hi:.3f}] "
                  f"n={r['n']}")
            if r["fpr"] > 0.20:
                all_ok = False

    assert all_ok, "One or more methods exceed FPR ≤ 0.20 on negative controls"


def test_beta_mixture_vs_mini_svbp():
    """Direct head-to-head: which wins on precision? recall? speed?"""
    bm = _get_benchmark()
    scores = bm.f1_scores()
    stats = bm.runtime_stats()

    a = scores["A_beta_mix"]
    b = scores["B_mini_svbp"]
    a_rt = stats.get("A_beta_mix", {"mean": 0})["mean"]
    b_rt = stats.get("B_mini_svbp", {"mean": 0})["mean"]

    print(f"\n  Head-to-head: Beta Mixture EP vs Mini-SVBP")
    print(f"    {'':20s}  {'Precision':>10s}  {'Recall':>10s}  {'F1':>10s}  {'Runtime':>10s}")
    print(f"    {'A (Beta mix)':20s}  {a['precision']:10.3f}  {a['recall']:10.3f}  "
          f"{a['f1']:10.3f}  {a_rt:10.4f}s")
    print(f"    {'B (Mini-SVBP)':20s}  {b['precision']:10.3f}  {b['recall']:10.3f}  "
          f"{b['f1']:10.3f}  {b_rt:10.4f}s")

    # Precision winner
    if a["precision"] > b["precision"]:
        print(f"    Precision winner: A (Beta mix) {a['precision']:.3f} > {b['precision']:.3f}")
    else:
        print(f"    Precision winner: B (Mini-SVBP) {b['precision']:.3f} ≥ {a['precision']:.3f}")

    # Recall winner
    if a["recall"] > b["recall"]:
        print(f"    Recall winner: A (Beta mix) {a['recall']:.3f} > {b['recall']:.3f}")
    else:
        print(f"    Recall winner: B (Mini-SVBP) {b['recall']:.3f} ≥ {a['recall']:.3f}")

    # Speed winner
    if a_rt < b_rt:
        print(f"    Speed winner: A (Beta mix) {a_rt:.4f}s < {b_rt:.4f}s")
    else:
        print(f"    Speed winner: B (Mini-SVBP) {b_rt:.4f}s ≤ {a_rt:.4f}s")

    # At least one method should achieve F1 ≥ 0.60 (weaker bar for head-to-head)
    assert max(a["f1"], b["f1"]) >= 0.60, \
        f"Neither method achieves F1 ≥ 0.60: A={a['f1']:.3f}, B={b['f1']:.3f}"


# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("E026 Exp 5: Camp Detection Benchmark (30 graphs + 10 neg controls)")
    print("=" * 70)

    test_benchmark_f1()
    test_benchmark_speed()
    test_negative_controls()
    test_beta_mixture_vs_mini_svbp()

    print(f"\n{'=' * 70}")
    print("All benchmarks complete.")
    print(f"{'=' * 70}")
