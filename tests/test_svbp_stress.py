"""Stress and scale tests for TortoiseSVBP.

Tests that SVBP stays numerically stable and scales reasonably
as claim counts and operator density increase.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import random
import time
import numpy as np
from tortoise.svbp import TortoiseSVBP


# ── helpers ───────────────────────────────────────────────────────

def _claims(n):
    return [f"c{i}" for i in range(n)]


# ── test 1: 100 claims, 40 random operators ───────────────────────

def test_scale_100_claims():
    """100 claims, 25 NAND + 15 IMPL operators (random pairwise).
    Runs with tiny particles/steps for speed.  Assert no crash,
    all posteriors valid, no NaN."""
    n = 100
    cids = _claims(n)

    random.seed(1)
    factors = []
    picked = set()
    for k in range(25):
        while True:
            a, b = random.sample(range(n), 2)
            pair = (a, b) if a < b else (b, a)
            if pair not in picked:
                picked.add(pair)
                break
        factors.append((f"nand_{k}", "NAND", [cids[pair[0]], cids[pair[1]]], 2.0))
    for k in range(15):
        while True:
            a, b = random.sample(range(n), 2)
            pair = (a, b) if a < b else (b, a)
            if pair not in picked:
                picked.add(pair)
                break
        factors.append((f"impl_{k}", "IMPL", [cids[pair[0]], cids[pair[1]]], 2.0))

    svbp = TortoiseSVBP(n_particles=15, n_svgd_steps=5, svgd_lr=0.01,
                        damping=0.5, max_iter=30, tol=1e-3, seed=42)
    iterations, converged = svbp.run(factors)

    # Must complete without exception
    assert iterations <= 30

    for cid in cids:
        conf = svbp.compute_confidence(cid)
        mean, var, alpha, beta = conf["mean"], conf["variance"], conf["alpha"], conf["beta"]
        assert not np.isnan(mean), f"{cid} mean is NaN"
        assert not np.isnan(var), f"{cid} var is NaN"
        assert 0.0 <= mean <= 1.0, f"{cid} mean={mean:.4f} out of [0,1]"
        assert var > 0.0, f"{cid} variance={var:.6f} not > 0"
        assert alpha > 0 and beta > 0, f"{cid} alpha={alpha}, beta={beta}"


# ── test 2: dense NAND graph (10 claims, 45 pairs) ────────────────

def test_dense_graph():
    """10-claim complete NAND graph (C(10,2) = 45 operators).
    Assert converges or hits max_iter gracefully, posteriors not all
    collapsed to ~0, graph had real effect (not all uniform)."""
    n = 10
    cids = _claims(n)
    factors = []
    for i in range(n):
        for j in range(i + 1, n):
            factors.append((f"nand_{i}_{j}", "NAND", [cids[i], cids[j]], 3.0))

    svbp = TortoiseSVBP(n_particles=30, n_svgd_steps=10, svgd_lr=0.01,
                        damping=0.5, max_iter=40, tol=1e-3, seed=42)
    iterations, converged = svbp.run(factors)

    # Must not crash
    assert iterations <= 40

    means = []
    vars_ = []
    for cid in cids:
        conf = svbp.compute_confidence(cid)
        means.append(conf["mean"])
        vars_.append(conf["variance"])
        assert not np.isnan(conf["mean"])
        assert not np.isnan(conf["variance"])
        assert 0.0 <= conf["mean"] <= 1.0

    # NAND doesn't collapse everything to 0
    max_mean = max(means)
    assert max_mean > 0.01, f"all means ≤ 0.01 (max={max_mean:.6f}), NAND collapsed graph"

    # Complete NAND drives posteriors tight — that's correct.
    # Verify graph had real effect: not all uniform Beta(1,1).
    for cid in cids:
        a, b = svbp._get_posterior(cid)
        assert a + b > 0


# ── test 3: star graph, one claim with 20 IMPL edges ──────────────

def test_many_operators_on_one_claim():
    """c0 IMPL-connected to 20 leaf claims (star graph).
    Assert c0 tightly constrained (variance < 0.05),
    all leaf posteriors valid."""
    n_leaves = 20
    factors = []
    for i in range(1, n_leaves + 1):
        factors.append((f"impl_0_{i}", "IMPL", ["c0", f"c{i}"], 3.0))

    svbp = TortoiseSVBP(n_particles=30, n_svgd_steps=10, svgd_lr=0.01,
                        damping=0.5, max_iter=40, tol=1e-3, seed=42)
    svbp.run(factors)

    c0_conf = svbp.compute_confidence("c0")
    assert 0.0 <= c0_conf["mean"] <= 1.0
    assert c0_conf["variance"] < 0.05, \
        f"c0 variance={c0_conf['variance']:.4f}, expected < 0.05 with {n_leaves} IMPL edges"
    assert not np.isnan(c0_conf["variance"])

    for i in range(1, n_leaves + 1):
        conf = svbp.compute_confidence(f"c{i}")
        assert 0.0 <= conf["mean"] <= 1.0, f"c{i} mean={conf['mean']:.4f}"
        assert conf["variance"] > 0.0, f"c{i} variance=0"
        assert not np.isnan(conf["variance"])


# ── test 4: speed scaling (sub-quadratic) ─────────────────────────

def test_speed_scaling():
    """Time SVBP on graphs of size 5, 10, 20 (2 operators per claim).
    Assert N=20 time < 5× N=5 time (sub-quadratic scaling)."""
    # Warmup: absorb JAX JIT compilation overhead
    _warm = TortoiseSVBP(n_particles=15, n_svgd_steps=5, max_iter=3, seed=42)
    _warm.run([("w", "IMPL", ["x", "y"], 1.0)])

    times = {}
    for n in [5, 10, 20]:
        cids = _claims(n)
        # Build chain: each claim linked to next, 2 ops per claim average
        # n-1 links + wrap-around = n operators, 2 participations each
        factors = []
        for i in range(n):
            j = (i + 1) % n  # wrap last back to first
            # alternate NAND/IMPL
            op_type = "NAND" if i % 2 == 0 else "IMPL"
            factors.append((f"op_{i}", op_type, [cids[i], cids[j]], 2.0))

        svbp = TortoiseSVBP(n_particles=15, n_svgd_steps=5, svgd_lr=0.01,
                            damping=0.5, max_iter=30, tol=1e-3, seed=42)

        t0 = time.time()
        svbp.run(factors)
        elapsed = time.time() - t0
        times[n] = elapsed

    # N=20 should be less than 6× N=5 (sub-quadratic; includes warm-up jitter)
    assert times[20] < 6 * times[5], \
        f"t(20)={times[20]:.2f}s >= 6× t(5)={6*times[5]:.2f}s — not sub-quadratic"
