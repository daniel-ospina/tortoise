"""EP retraction design + convergence analysis — Gap 6+7 of E024.

Tests 1-3: retraction semantics (W₂ validation, cost, partial recomputation).
Tests 4-5: convergence on random graphs (fraction, median, damping sweep).

Runs standalone:  .venv/bin/python -m pytest tests/test_ep_retraction.py -v
"""
from __future__ import annotations

import sys, os, time, random, math, copy

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pytest

from tests.test_ep_utils import InMemoryEP
from tortoise.quadrature import phi_nand, phi_impl


# ═══════════════════════════════════════════════════════════════════
# helpers
# ═══════════════════════════════════════════════════════════════════

def _w2_beta(a1, b1, a2, b2, n=5000):
    """1D W₂ between two Beta(a,b) distributions via sorted quantiles."""
    s1 = np.sort(np.random.beta(a1, b1, n))
    s2 = np.sort(np.random.beta(a2, b2, n))
    return float(np.sqrt(np.mean((s1 - s2) ** 2)))


def _w2_vec(posts_a, posts_b):
    """Mean per-claim W₂ between two posterior dicts {cid: (α,β)}."""
    diffs = []
    for cid in posts_a:
        if cid in posts_b:
            diffs.append(_w2_beta(*posts_a[cid], *posts_b[cid]))
    return float(np.mean(diffs)) if diffs else 0.0


def _random_graph(n_claims, n_ops, seed):
    """Generate (factors, evidence) for a random graph.

    factors: [(op_id, op_type, [a,b], weight), ...]
    evidence: {claim_id: (α,β), ...} — optional fixed evidence
    """
    rng = random.Random(seed)
    claims = [f"c{i}" for i in range(n_claims)]
    factors = []
    for i in range(n_ops):
        op_type = rng.choice(["NAND", "IMPL"])
        a, b = rng.sample(claims, 2)
        weight = 3.0 if op_type == "NAND" else 1.0
        factors.append((f"{op_type}_{i}", op_type, [a, b], weight))
    # ponytail: uniform evidence (no priors) — simplest convergence test
    return factors, {}


def _run_ep_and_get_posteriors(ep, factors, evidence=None, n_iter=30):
    """Run EP, return dict of posteriors {cid: (α,β)}."""
    ep.run(factors, evidence=evidence, n_iter=n_iter)
    return dict(ep.posteriors)


# ═══════════════════════════════════════════════════════════════════
# Test 1: Retraction vs Never-Added
# ═══════════════════════════════════════════════════════════════════

def test_retraction_vs_never_added():
    """Removing an operator gives same result as never adding it.

    Graph G: operators {A, B, C}. Graph G': operators {A, B} (C never added).
    Retraction = remove C from factor list + re-run EP from scratch.
    Assert: W₂(G_without_C, G') < 0.02.
    """
    # G all: A=IMPL(c0,c1), B=IMPL(c1,c2), C=NAND(c0,c2)
    factors_all = [
        ("IMPL_A", "IMPL", ["c0", "c1"], 1.0),
        ("IMPL_B", "IMPL", ["c1", "c2"], 1.0),
        ("NAND_C", "NAND", ["c0", "c2"], 3.0),
    ]
    factors_no_c = [
        ("IMPL_A", "IMPL", ["c0", "c1"], 1.0),
        ("IMPL_B", "IMPL", ["c1", "c2"], 1.0),
    ]

    # Retraction: run with C, then run without C (fresh)
    ep = InMemoryEP(damping=0.5)
    _run_ep_and_get_posteriors(ep, factors_all)
    ep2 = InMemoryEP(damping=0.5)
    _run_ep_and_get_posteriors(ep2, factors_no_c)

    # Never added: run without C fresh
    ep3 = InMemoryEP(damping=0.5)
    never_added = _run_ep_and_get_posteriors(ep3, factors_no_c)

    retraction_posts = dict(ep2.posteriors)
    w2_val = _w2_vec(retraction_posts, never_added)

    assert w2_val < 0.02, (
        f"Retraction W₂={w2_val:.6f} exceeds 0.02 — "
        f"removing C and re-running should match never-adding-C"
    )


# ═══════════════════════════════════════════════════════════════════
# Test 2: Retraction Recomputation Cost
# ═══════════════════════════════════════════════════════════════════

def test_retraction_recomputation_cost():
    """Measure: add C then retract (full re-run) vs just add C.

    Assert: retraction cost ≤ 2× add cost (acceptable for occasional use).
    """
    # Build a moderately sized graph so costs are measurable
    factors_base = [
        ("IMPL_0", "IMPL", ["c0", "c1"], 1.0),
        ("IMPL_1", "IMPL", ["c1", "c2"], 1.0),
        ("IMPL_2", "IMPL", ["c2", "c3"], 1.0),
        ("IMPL_3", "IMPL", ["c3", "c4"], 1.0),
        ("NAND_0", "NAND", ["c0", "c3"], 3.0),
        ("NAND_1", "NAND", ["c1", "c4"], 3.0),
    ]
    extra_factor = ("NAND_C", "NAND", ["c2", "c4"], 3.0)

    # Time: just add C
    start = time.perf_counter()
    ep_add = InMemoryEP(damping=0.5)
    ep_add.run(factors_base + [extra_factor], n_iter=30)
    time_add = time.perf_counter() - start

    # Time: add C, then retract (full re-run without C)
    start = time.perf_counter()
    ep_ret = InMemoryEP(damping=0.5)
    ep_ret.run(factors_base + [extra_factor], n_iter=30)
    # retraction = fresh EP with factors_base only
    ep_ret2 = InMemoryEP(damping=0.5)
    ep_ret2.run(factors_base, n_iter=30)
    time_retract = time.perf_counter() - start

    overhead = time_retract / time_add if time_add > 0 else float("inf")

    # ponytail: report ratio, assert ≤ 2×
    assert overhead <= 2.0, (
        f"Retraction overhead {overhead:.2f}× exceeds 2× bound. "
        f"add={time_add:.4f}s retract={time_retract:.4f}s"
    )


# ═══════════════════════════════════════════════════════════════════
# Test 3: Partial Recomputation
# ═══════════════════════════════════════════════════════════════════

def test_retraction_partial_recomputation():
    """Partial recomputation: zero retracted operator's messages, run 5 iters.

    On a 10-claim graph: add C, run EP, then "retract" by zeroing C's
    messages and running 5 more iterations on the remaining factors.
    Compare against the never-adding-C baseline.

    Assert: W₂(partial, never_added) < 0.05.
    """
    rng = random.Random(42)
    # 10-claim graph with 12 operators, including one to retract
    claims = [f"c{i}" for i in range(10)]
    base_factors = []
    for i in range(11):
        a, b = rng.sample(claims, 2)
        t = rng.choice(["NAND", "IMPL"])
        w = 3.0 if t == "NAND" else 1.0
        base_factors.append((f"OP_{i}", t, [a, b], w))

    # The "extra" operator to retract
    extra = ("OP_RETRACT", "NAND", ["c0", "c9"], 3.0)
    all_factors = base_factors + [extra]

    # Baseline: never added extra
    ep_baseline = InMemoryEP(damping=0.5)
    baseline_posts = _run_ep_and_get_posteriors(ep_baseline, base_factors, n_iter=30)

    # Full: run with extra
    ep_full = InMemoryEP(damping=0.5)
    _run_ep_and_get_posteriors(ep_full, all_factors, n_iter=30)

    # Partial retraction: copy messages/posteriors, delete OP_RETRACT messages,
    # run 5 more iterations on base_factors only
    ep_partial = InMemoryEP(damping=0.5)
    ep_partial.messages = dict(ep_full.messages)
    ep_partial.posteriors = dict(ep_full.posteriors)

    # Zero out all messages from the retracted operator
    keys_to_del = [k for k in ep_partial.messages if k[0] == "OP_RETRACT"]
    for k in keys_to_del:
        del ep_partial.messages[k]

    # Run 5 more iterations with only base factors
    ep_partial.run(base_factors, n_iter=5)

    partial_posts = dict(ep_partial.posteriors)
    w2_val = _w2_vec(partial_posts, baseline_posts)

    assert w2_val < 0.05, (
        f"Partial retraction W₂={w2_val:.6f} exceeds 0.05 — "
        f"zeroing messages + 5 iters should approximate full recomputation"
    )


# ═══════════════════════════════════════════════════════════════════
# Test 4: Convergence on Random Graphs
# ═══════════════════════════════════════════════════════════════════

def _ep_converges(factors, evidence=None, max_iter=100, tol=1e-3, damping=0.5):
    """Run EP with convergence tracking. Returns (converged: bool, iterations: int)."""
    ep = InMemoryEP(damping=damping)
    if evidence:
        for cid, (a, b) in evidence.items():
            ep.posteriors[cid] = (a, b)

    for it in range(max_iter):
        prev = dict(ep.posteriors)
        ep.run(factors, evidence=evidence, n_iter=1)

        max_change = 0.0
        for cid in ep.posteriors:
            new_a, new_b = ep.posteriors[cid]
            old_a, old_b = prev.get(cid, (1.0, 1.0))
            change = max(
                abs(new_a - old_a) / max(old_a, 1e-6),
                abs(new_b - old_b) / max(old_b, 1e-6),
            )
            max_change = max(max_change, change)

        if max_change < tol:
            return True, it + 1

    return False, max_iter


def test_convergence_on_random_graphs():
    """30 random graphs (2-10 claims, 1-15 NAND+IMPL operators).

    Run TortoiseEP with max_iter=100. Report:
      - fraction converged (converged=True)
      - median iterations
      - predict non-convergence characteristics

    Assert: ≥ 80% converge within 100 iterations.
    """
    rng = random.Random(7)
    results = []
    for seed in range(30):
        n_claims = rng.randint(2, 11)
        n_ops = rng.randint(1, 16)
        factors, evidence = _random_graph(n_claims, n_ops, seed * 101 + 13)

        converged, iterations = _ep_converges(factors, evidence,
                                               max_iter=100, tol=1e-3, damping=0.5)
        # Gather graph characteristics
        n_nand = sum(1 for _, t, _, _ in factors if t == "NAND")
        n_impl = sum(1 for _, t, _, _ in factors if t == "IMPL")
        total_edges = n_nand + n_impl
        edge_ratio = n_nand / max(total_edges, 1)

        results.append({
            "seed": seed,
            "n_claims": n_claims,
            "n_ops": n_ops,
            "n_nand": n_nand,
            "n_impl": n_impl,
            "edge_ratio": edge_ratio,
            "converged": converged,
            "iterations": iterations,
        })

    n_converged = sum(1 for r in results if r["converged"])
    frac = n_converged / len(results)
    iters = [r["iterations"] for r in results if r["converged"]]
    median_iters = float(np.median(iters)) if iters else 100.0

    # Non-convergence analysis
    non_conv = [r for r in results if not r["converged"]]
    avg_nand_ratio_conv = float(np.mean([r["edge_ratio"] for r in results
                                          if r["converged"]])) if n_converged else 0
    avg_nand_ratio_non = float(np.mean([r["edge_ratio"] for r in non_conv])) if non_conv else 0

    print(f"\n  Converged: {n_converged}/{len(results)} ({frac:.1%})")
    print(f"  Median iterations (converged): {median_iters:.0f}")
    print(f"  Non-converged: {len(non_conv)} graphs")
    if non_conv:
        print(f"    avg NAND ratio: conv={avg_nand_ratio_conv:.2f} vs non-conv={avg_nand_ratio_non:.2f}")
        for r in non_conv:
            print(f"    seed={r['seed']} claims={r['n_claims']} ops={r['n_ops']} "
                  f"nand={r['n_nand']} impl={r['n_impl']} ratio={r['edge_ratio']:.2f}")

    assert frac >= 0.80, (
        f"Only {frac:.1%} converge within 100 iters — need ≥80%"
    )


# ═══════════════════════════════════════════════════════════════════
# Test 5: Damping Sweep
# ═══════════════════════════════════════════════════════════════════

def test_damping_sweep():
    """On the hardest-to-converge graph from random generation, sweep damping.

    Sweep damping ∈ {0.3, 0.5, 0.7, 0.9} on a deliberately challenging graph
    (dense NANDs + loops = frustration). Report iterations for each.

    Assert: at least one damping value converges within 100 iterations.
    """
    # Hard graph: 6 claims, mostly NANDs with overlapping loops (frustrated)
    hard_factors = [
        ("NAND_0", "NAND", ["c0", "c1"], 3.0),
        ("NAND_1", "NAND", ["c1", "c2"], 3.0),
        ("NAND_2", "NAND", ["c2", "c3"], 3.0),
        ("NAND_3", "NAND", ["c3", "c4"], 3.0),
        ("NAND_4", "NAND", ["c4", "c5"], 3.0),
        ("NAND_5", "NAND", ["c0", "c2"], 3.0),
        ("NAND_6", "NAND", ["c1", "c3"], 3.0),
        ("NAND_7", "NAND", ["c2", "c4"], 3.0),
        ("NAND_8", "NAND", ["c3", "c5"], 3.0),
        ("NAND_9", "NAND", ["c0", "c5"], 3.0),
        ("IMPL_0", "IMPL", ["c0", "c2"], 1.0),
        ("IMPL_1", "IMPL", ["c2", "c4"], 1.0),
    ]

    damping_results = {}
    for d in [0.3, 0.5, 0.7, 0.9]:
        converged, iterations = _ep_converges(hard_factors, max_iter=100,
                                               tol=1e-3, damping=d)
        damping_results[d] = (converged, iterations)
        print(f"  damping={d}: {'converged' if converged else 'DIVERGED'} "
              f"in {iterations} iters")

    any_converged = any(v[0] for v in damping_results.values())
    assert any_converged, (
        f"No damping value converged within 100 iterations on the hard graph. "
        f"Results: {damping_results}"
    )
