#!/usr/bin/env python3
"""E024 Stage 6: Validate — compare 6 algorithm variants on 10-claim benchmark.

Run: python -m validation.compare_algorithms
"""
import sys, os, time, random
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import jax.numpy as jnp
import jax
import numpy as np
from tortoise.svbp import TortoiseSVBP, sigmoid
from tortoise.quadrature import tilted_moments, moments_to_beta, phi_nand, phi_impl

# ── Benchmark graph ───────────────────────────────────────────────
NAND_F = [("NAND_01", "NAND", ["c0", "c1"], 3.0),
          ("NAND_23", "NAND", ["c2", "c3"], 3.0)]
IMPL_F = [("IMPL_45", "IMPL", ["c4", "c5"], 1.0),
          ("IMPL_67", "IMPL", ["c6", "c7"], 1.0),
          ("IMPL_89", "IMPL", ["c8", "c9"], 1.0)]
ALL_F  = NAND_F + IMPL_F
EVID   = {"c0": (4.0, 1.0), "c1": (2.0, 1.0)}
CIDS   = [f"c{i}" for i in range(10)]
HMC_VAR_C0 = 0.030


# ═══════════════════════════════════════════════════════════════════
# InMemoryEP (from test_svbp_hybrid.py)
# ═══════════════════════════════════════════════════════════════════
class InMemoryEP:
    """Deterministic EP — handles IMPL factors only."""

    def __init__(self, damping=0.5, n_quad=8):
        self.damping = damping
        self.n_quad = n_quad
        self.messages: dict = {}
        self.posteriors: dict = {}

    @staticmethod
    def _nat(a, b): return (a - 1, b - 1)

    @staticmethod
    def _beta(e1, e2): return (max(e1 + 1, 0.01), max(e2 + 1, 0.01))

    def run(self, impl_factors, evidence=None, n_iter=30):
        if evidence:
            for cid, (a, b) in evidence.items():
                self.posteriors[cid] = (a, b)
        for _ in range(n_iter):
            for op_id, op_type, inputs, weight in impl_factors:
                if len(inputs) != 2: continue
                id_a, id_b = inputs
                post_a = self.posteriors.get(id_a, (1.0, 1.0))
                post_b = self.posteriors.get(id_b, (1.0, 1.0))
                msg_a = self.messages.get((op_id, id_a, "IMPL"), (0.0, 0.0))
                msg_b = self.messages.get((op_id, id_b, "IMPL"), (0.0, 0.0))
                pa_e1, pa_e2 = self._nat(*post_a)
                pb_e1, pb_e2 = self._nat(*post_b)
                cav_a = self._beta(pa_e1 - msg_a[0], pa_e2 - msg_a[1])
                cav_b = self._beta(pb_e1 - msg_b[0], pb_e2 - msg_b[1])
                phi_fn = phi_nand if op_type == "NAND" else phi_impl
                mom_a, mom_b = tilted_moments(*cav_a, *cav_b, weight, phi_fn, n_quad=self.n_quad)
                new_a, new_b = moments_to_beta(*mom_a), moments_to_beta(*mom_b)
                raw_a = (self._nat(*new_a)[0] - self._nat(*cav_a)[0],
                         self._nat(*new_a)[1] - self._nat(*cav_a)[1])
                raw_b = (self._nat(*new_b)[0] - self._nat(*cav_b)[0],
                         self._nat(*new_b)[1] - self._nat(*cav_b)[1])
                d = self.damping
                oa = self.messages.get((op_id, id_a, "IMPL"), (0.0, 0.0))
                ob = self.messages.get((op_id, id_b, "IMPL"), (0.0, 0.0))
                self.messages[(op_id, id_a, "IMPL")] = (d * raw_a[0] + (1 - d) * oa[0],
                                                        d * raw_a[1] + (1 - d) * oa[1])
                self.messages[(op_id, id_b, "IMPL")] = (d * raw_b[0] + (1 - d) * ob[0],
                                                        d * raw_b[1] + (1 - d) * ob[1])
                for cid in [id_a, id_b]:
                    ea, eb = evidence.get(cid, (1.0, 1.0)) if evidence else (1.0, 1.0)
                    e1, e2 = self._nat(ea, eb)
                    for (_, c, _), (m1, m2) in self.messages.items():
                        if c == cid: e1 += m1; e2 += m2
                    self.posteriors[cid] = self._beta(e1, e2)

    def conf(self, cid):
        a, b = self.posteriors.get(cid, (1.0, 1.0))
        t = a + b
        return {"mean": a / t, "variance": (a * b) / (t * t * (t + 1))}


# ═══════════════════════════════════════════════════════════════════
# Metrics helpers
# ═══════════════════════════════════════════════════════════════════

def beta_mean_var(a, b, scale=1.0):
    """(mean, variance) from Beta params, with optional scale on variance."""
    t = a + b
    return a / t, (a * b) / (t * t * (t + 1)) * scale


def w2_beta(key, a1, b1, a2, b2, n=2000):
    """W₂ between two Beta distributions via sorted samples."""
    k = jax.random.PRNGKey(key)
    s1 = jnp.sort(jax.random.beta(k, a1, b1, (n,)))
    s2 = jnp.sort(jax.random.beta(k, a2, b2, (n,)))
    return float(jnp.sqrt(jnp.mean((s1 - s2) ** 2)))


def camp_frac_from_particles(particles, a, b):
    """Fraction in minority camp for NAND pair. None if no particles."""
    if a not in particles or b not in particles:
        return None
    ca = sigmoid(particles[a])
    cb = sigmoid(particles[b])
    med_a, med_b = float(jnp.median(ca)), float(jnp.median(cb))
    hl = int(jnp.sum((ca > med_a) & (cb <= med_b)))
    lh = int(jnp.sum((ca <= med_a) & (cb > med_b)))
    return min(hl, lh) / len(ca)


def get_params(result, cid):
    """Extract (alpha, beta, scale) from any variant result."""
    scale = result.get("scale", 1.0)
    if "svbp" in result:
        c = result["svbp"].compute_confidence(cid)
        return c["alpha"], c["beta"], scale
    if "ep" in result:
        c = result["ep"].conf(cid)
        a, b = result["ep"].posteriors.get(cid, EVID.get(cid, (1.0, 1.0)))
        return a, b, scale
    a, b = result.get("posteriors", {}).get(cid, EVID.get(cid, (1.0, 1.0)))
    return a, b, scale


def max_w2_between(results, cids=None):
    """Max W₂ across claims between two result dicts."""
    if cids is None:
        cids = CIDS
    max_w = 0.0
    for i, cid in enumerate(cids):
        a1, b1, _ = get_params(results[0], cid)
        a2, b2, _ = get_params(results[1], cid)
        w = w2_beta(100 + i, a1, b1, a2, b2)
        max_w = max(max_w, w)
    return max_w


# ═══════════════════════════════════════════════════════════════════
# Variant runners — each returns dict with keys:
#   posteriors | svbp, particles, elapsed, [scale], [triggered]
# ═══════════════════════════════════════════════════════════════════

def _shuffle_factors(factors, seed):
    """Return a shuffled copy of factors. Deterministic given seed."""
    rng = random.Random(seed)
    f = list(factors)
    rng.shuffle(f)
    return f


def run_a(seed, factors=None):
    """A. EP-only"""
    ep = InMemoryEP()
    t0 = time.time()
    ep.run(factors or IMPL_F, evidence=EVID)
    return {"ep": ep, "elapsed": time.time() - t0, "particles": {}}


def run_b(seed, factors=None):
    """B. SVBP-only (25 particles, 15 steps)"""
    svbp = TortoiseSVBP(n_particles=25, n_svgd_steps=15, seed=seed)
    t0 = time.time()
    svbp.run(factors or ALL_F, evidence=EVID)
    return {"svbp": svbp, "elapsed": time.time() - t0,
            "particles": dict(svbp._particles)}


def run_c(seed, factors=None):
    """C. EP+SVBP: EP on IMPL, SVBP on NAND from EP init."""
    ep = InMemoryEP()
    ep.run(IMPL_F, evidence=EVID)
    # Build evidence from EP posteriors
    ep_ev = dict(EVID)
    for cid, (a, b) in ep.posteriors.items():
        ep_ev[cid] = (a, b)
    svbp = TortoiseSVBP(n_particles=25, n_svgd_steps=15, seed=seed)
    t0 = time.time()
    svbp.run(NAND_F, evidence=ep_ev)
    return {"svbp": svbp, "elapsed": time.time() - t0,
            "particles": dict(svbp._particles)}


def run_d(seed, factors=None):
    """D. EP+scale: EP then multiply variance by 2.0 post-hoc."""
    ep = InMemoryEP()
    t0 = time.time()
    ep.run(factors or IMPL_F, evidence=EVID)
    return {"ep": ep, "elapsed": time.time() - t0, "particles": {}, "scale": 2.0}


def run_e(seed, factors=None):
    """E. SVBP-100: 100 particles, 20 steps."""
    svbp = TortoiseSVBP(n_particles=100, n_svgd_steps=20, seed=seed)
    t0 = time.time()
    svbp.run(factors or ALL_F, evidence=EVID)
    return {"svbp": svbp, "elapsed": time.time() - t0,
            "particles": dict(svbp._particles)}


def run_f(seed, factors=None):
    """F. EP+SVBP-triggered: EP first, SVBP on NAND factors touching contested claims."""
    ep = InMemoryEP()
    ep.run(IMPL_F, evidence=EVID)
    # Detect contested claims (EP variance > 0.04)
    contested = set()
    for cid in CIDS:
        c = ep.conf(cid)
        if c["variance"] > 0.04:
            contested.add(cid)
    triggered_nand = [f for f in NAND_F
                      if any(cid in contested for cid in f[2])]
    ep_ev = dict(EVID)
    for cid, (a, b) in ep.posteriors.items():
        ep_ev[cid] = (a, b)
    svbp = TortoiseSVBP(n_particles=25, n_svgd_steps=15, seed=seed)
    t0 = time.time()
    svbp.run(triggered_nand, evidence=ep_ev)
    return {"svbp": svbp, "elapsed": time.time() - t0,
            "particles": dict(svbp._particles), "triggered": triggered_nand}


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

RUNNERS = [("A. EP-only", run_a),      ("B. SVBP-only", run_b),
           ("C. EP+SVBP", run_c),      ("D. EP+scale", run_d),
           ("E. SVBP-100", run_e),     ("F. EP+SVBP-trig", run_f)]


def run_all():
    results = {}
    for name, fn in RUNNERS:
        # Single run for metrics + latency
        t0 = time.time()
        r = fn(42)
        elapsed = time.time() - t0

        # Path independence: 3 runs with shuffled factor order
        seeds = [123, 456, 789]
        shuffled = []
        for s in seeds:
            if fn in (run_c, run_f):
                shuffled.append(fn(s))
            elif fn in (run_a, run_d):
                shuffled.append(fn(s, factors=_shuffle_factors(IMPL_F, s)))
            else:
                shuffled.append(fn(s, factors=_shuffle_factors(ALL_F, s)))
        # Determinism: seed 42 vs 123
        det_runs = [fn(42), fn(123)]

        path_w2 = max(max_w2_between([shuffled[i], shuffled[j]])
                      for i, j in [(0, 1), (0, 2), (1, 2)])
        det_w2 = max_w2_between(det_runs)
        results[name] = {"result": r, "path_w2": path_w2,
                         "det_w2": det_w2, "elapsed": elapsed}
    return results


def print_table(results):
    print("=" * 100)
    print("E024 Stage 6: Validation — 6 Algorithm Variants on 10-Claim Benchmark")
    print("=" * 100)
    print("Graph: 10 claims, 2 NAND + 3 IMPL, evidence c0=(4,1) c1=(2,1)")
    print(f"HMC reference: var(c0) ≈ {HMC_VAR_C0:.3f}")
    print()

    hdr = (f"{'Variant':<20} {'Camp':>6}  {'Mean c0':>7} {'Mean c1':>7} {'Mean c4':>7} "
           f"{'Var c0':>7} {'V/HMC':>7} {'Path W₂':>8} {'Det W₂':>8} {'Latency':>8} {'NaN':>4} {'Pole':>4}")
    sep = "-" * len(hdr)
    print(hdr)
    print(sep)

    for name, _ in RUNNERS:
        info = results[name]
        r = info["result"]

        camp = camp_frac_from_particles(r["particles"], "c0", "c1")
        camp_s = f"{camp:.3f}" if camp is not None else "   —"

        m0, v0 = beta_mean_var(*get_params(r, "c0"))
        m1, _  = beta_mean_var(*get_params(r, "c1"))
        m4, _  = beta_mean_var(*get_params(r, "c4"))
        ov = v0 / HMC_VAR_C0 if HMC_VAR_C0 > 0 else float("inf")

        # Numerical checks
        has_nan = False
        has_pole = False
        for cid in CIDS:
            a, b, _ = get_params(r, cid)
            mean, var = beta_mean_var(a, b)
            if np.isnan(mean) or np.isnan(var):
                has_nan = True
            if mean <= 0.001 or mean >= 0.999:
                has_pole = True
        nan_s = " ✗" if has_nan else "  —"
        pole_s = " ✗" if has_pole else "  —"

        print(f"{name:<20} {camp_s:>6}  {m0:>7.4f} {m1:>7.4f} {m4:>7.4f} "
              f"{v0:>7.4f} {ov:>7.2f} {info['path_w2']:>8.4f} {info['det_w2']:>8.4f} "
              f"{info['elapsed']:>7.3f}s {nan_s:>4} {pole_s:>4}")

    print(sep)
    print()
    print("Camp: minority-camp fraction for NAND pair (0,1). '—' = no particles (EP-only).")
    print("V/HMC: variance ratio vs HMC reference. <1 = overconfident (too little variance).")
    print("Path W₂: max W₂ spread across 3 shuffled-factor-order runs.")
    print("Det W₂: W₂ between runs with seed=42 vs seed=123.")
    print("Pole: any claim mean ≤0.001 or ≥0.999 (degenerate).")


if __name__ == "__main__":
    results = run_all()
    print_table(results)
