"""Property-based fuzz testing for TortoiseSVBP.

Generates random factor graphs deterministically from seeds 0-99 and
verifies invariants that must hold across all inputs:
  1. No NaN, bounded posteriors, positive variance
  2. Message components clamped within ±1000
  3. Deterministic: same seed, same graph, same result
  4. Convergence rate ≥ 25/30 on random graphs

Uses fast params (n_particles=15, n_svgd_steps=5, max_iter=20) so each
test runs under 30s.
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import math
import random

import numpy as np
import jax.numpy as jnp

from tortoise.svbp import TortoiseSVBP


# ── Deterministic random graph generator ──────────────────────────

_OP_TYPES = ["NAND", "IMPL"]


def generate_random_graph(seed: int):
    """Generate a random factor graph deterministically from a seed.

    Returns:
        factors: list of (op_id, op_type, [input_ids], weight)
        evidence: dict of {claim_id: (alpha, beta)} or None
        claim_ids: list of all claim IDs in the graph
    """
    rng = random.Random(seed)

    n_claims = rng.randint(2, 15)
    claim_ids = [f"c{i}" for i in range(n_claims)]

    n_factors = rng.randint(1, 20)
    factors = []
    for i in range(n_factors):
        op_type = rng.choice(_OP_TYPES)
        a = rng.randrange(n_claims)
        b = rng.randrange(n_claims)
        while a == b:
            b = rng.randrange(n_claims)
        input_ids = [claim_ids[a], claim_ids[b]]
        weight = round(rng.uniform(0.1, 10.0), 2)
        factors.append((f"f{i}", op_type, input_ids, weight))

    # Evidence for a random subset of claims
    n_evidence = rng.randint(0, min(n_claims, 5))
    evidence = None
    if n_evidence > 0:
        evidence_claims = rng.sample(claim_ids, n_evidence)
        evidence = {}
        for cid in evidence_claims:
            alpha = round(rng.uniform(0.5, 10.0), 2)
            beta = round(rng.uniform(0.5, 10.0), 2)
            evidence[cid] = (alpha, beta)

    return factors, evidence, claim_ids


def run_svbp(seed: int, graph_seed: int):
    """Build a random graph from graph_seed and run SVBP with the given
    JAX/random seed. Returns (svbp, factors, claim_ids, n_iter, converged)."""
    factors, evidence, claim_ids = generate_random_graph(graph_seed)

    random.seed(graph_seed)  # controls shuffle order inside run()
    svbp = TortoiseSVBP(
        n_particles=15, n_svgd_steps=5, svgd_lr=0.01,
        damping=0.5, max_iter=20, tol=5e-3, seed=seed,
    )
    n_iter, converged = svbp.run(factors, evidence=evidence)
    return svbp, factors, evidence, claim_ids, n_iter, converged


# ── Test 1: No NaN, bounded posteriors, positive variance ─────────

def test_fuzz_no_nan():
    """20 random graphs. Assert: no NaN in any posterior; all means in
    [0.01, 0.99]; all variances > 0."""
    for graph_seed in range(20):
        svbp, factors, evidence, claim_ids, n_iter, converged = run_svbp(
            seed=graph_seed, graph_seed=graph_seed,
        )
        for cid in svbp.posteriors:
            conf = svbp.compute_confidence(cid)
            assert not math.isnan(conf["mean"]), \
                f"seed={graph_seed}: {cid} mean is NaN"
            assert not math.isnan(conf["variance"]), \
                f"seed={graph_seed}: {cid} variance is NaN"
            assert 0.01 < conf["mean"] < 0.99, \
                f"seed={graph_seed}: {cid} mean={conf['mean']:.6f} ∉ (0.01, 0.99)"
            assert conf["variance"] > 0, \
                f"seed={graph_seed}: {cid} variance={conf['variance']:.6f} ≤ 0"


# ── Test 2: Message component clamping ───────────────────────────

def test_fuzz_message_clamp():
    """20 random graphs. Assert: all message components within ±1000."""
    for graph_seed in range(20):
        svbp, factors, evidence, claim_ids, n_iter, converged = run_svbp(
            seed=graph_seed, graph_seed=graph_seed,
        )
        for key, (ma, mb) in svbp.messages.items():
            assert -1000 <= ma <= 1000, \
                f"seed={graph_seed}: message {key} alpha={ma:.2f} outside ±1000"
            assert -1000 <= mb <= 1000, \
                f"seed={graph_seed}: message {key} beta={mb:.2f} outside ±1000"


# ── Test 3: Deterministic same-seed reproduction ──────────────────

def test_fuzz_deterministic():
    """10 random seeds. For each seed, generate a graph and run SVBP twice.
    Assert posteriors identical to 1e-6."""
    for graph_seed in range(10):
        factors, evidence, claim_ids = generate_random_graph(graph_seed)

        # Run 1
        random.seed(graph_seed)
        svbp1 = TortoiseSVBP(
            n_particles=15, n_svgd_steps=5, svgd_lr=0.01,
            damping=0.5, max_iter=20, tol=5e-3, seed=graph_seed,
        )
        svbp1.run(factors, evidence=evidence)

        # Run 2
        random.seed(graph_seed)
        svbp2 = TortoiseSVBP(
            n_particles=15, n_svgd_steps=5, svgd_lr=0.01,
            damping=0.5, max_iter=20, tol=5e-3, seed=graph_seed,
        )
        svbp2.run(factors, evidence=evidence)

        all_cids = set(svbp1.posteriors) | set(svbp2.posteriors)
        for cid in all_cids:
            a1, b1 = svbp1._get_posterior(cid)
            a2, b2 = svbp2._get_posterior(cid)
            assert abs(a1 - a2) < 1e-6, \
                f"seed={graph_seed}: {cid} alpha {a1} ≠ {a2} (diff={abs(a1-a2):.2e})"
            assert abs(b1 - b2) < 1e-6, \
                f"seed={graph_seed}: {cid} beta {b1} ≠ {b2} (diff={abs(b1-b2):.2e})"


# ── Test 4: Convergence rate ─────────────────────────────────────

def test_fuzz_converges():
    """30 random graphs. Assert ≥ 25/30 converge. Non-convergent graphs
    must still have valid posteriors (no NaN, means in [0.01, 0.99])."""
    converged_count = 0
    for graph_seed in range(30):
        svbp, factors, evidence, claim_ids, n_iter, converged = run_svbp(
            seed=graph_seed, graph_seed=graph_seed,
        )
        if converged:
            converged_count += 1

        # Non-convergent graphs must still have valid posteriors
        for cid in svbp.posteriors:
            conf = svbp.compute_confidence(cid)
            assert not math.isnan(conf["mean"]), \
                f"seed={graph_seed}: {cid} mean is NaN (converged={converged})"
            assert 0.01 < conf["mean"] < 0.99, \
                f"seed={graph_seed}: {cid} mean={conf['mean']:.6f} ∉ (0.01,0.99) (converged={converged})"
            assert conf["variance"] > 0, \
                f"seed={graph_seed}: {cid} variance ≤ 0 (converged={converged})"

    assert converged_count >= 25, \
        f"Only {converged_count}/30 converged (need ≥ 25)"
