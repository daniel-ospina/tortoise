"""SVBP Gate 2: Factor-graph SVBP validation against HMC.

Validates that per-factor Stein updates (with cavity messages) match
HMC ground truth on the 10-claim test graph. Unlike Gate 1 (global
SVGD), this runs proper belief propagation with message passing.

Usage:
    python -m tortoise.validation.svbp_gate2
"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import jax.numpy as jnp
import jax
import numpy as np
import numpyro
from numpyro.infer import MCMC, NUTS

from tortoise.svbp import TortoiseSVBP
from validation.hmc_model import (
    N_CLAIMS, NAND_PAIRS, NAND_WEIGHT, IMPL_PAIRS, IMPL_WEIGHT,
    tortoise_model,
)

# Evidence (see validation/hmc_model.py)
EVIDENCE_ALPHA = [4.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
EVIDENCE_BETA  = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]


# ═══════════════════════════════════════════════════════════════════
# Metrics (same as Gate 1)
# ═══════════════════════════════════════════════════════════════════

def wasserstein_2_1d(a, b):
    a_sorted = jnp.sort(a.flatten())
    b_sorted = jnp.sort(b.flatten())
    n = min(len(a_sorted), len(b_sorted))
    if len(a_sorted) > n:
        idx = jnp.linspace(0, len(a_sorted) - 1, n, dtype=jnp.int32)
        a_sorted = a_sorted[idx]
    if len(b_sorted) > n:
        idx = jnp.linspace(0, len(b_sorted) - 1, n, dtype=jnp.int32)
        b_sorted = b_sorted[idx]
    return jnp.sqrt(jnp.mean((a_sorted - b_sorted) ** 2))


# ═══════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════

def main():
    CLAIM_NAMES = list("ABCDEFGHIJ")

    print("=" * 72)
    print("SVBP Gate 2: Factor-Graph SVBP vs HMC")
    print("=" * 72)
    print(f"  Graph: {N_CLAIMS} claims, {len(NAND_PAIRS)} NAND, {len(IMPL_PAIRS)} IMPL")
    print(f"  SVBP: 25 particles, 15 SVGD steps/factor, damping=0.5")

    # ── Build factor list ─────────────────────────────────────────
    factors = []
    for op_type, pairs, weight in [("NAND", NAND_PAIRS, NAND_WEIGHT),
                                    ("IMPL", IMPL_PAIRS, IMPL_WEIGHT)]:
        for a, b in pairs:
            op_id = f"{op_type}_{a}_{b}"
            factors.append((op_id, op_type, [f"c{a}", f"c{b}"], float(weight)))

    # Evidence as prior Beta parameters
    evidence = {}
    for i in range(N_CLAIMS):
        if EVIDENCE_ALPHA[i] > 1.0 or EVIDENCE_BETA[i] > 1.0:
            evidence[f"c{i}"] = (float(EVIDENCE_ALPHA[i]), float(EVIDENCE_BETA[i]))

    # ── HMC ground truth ──────────────────────────────────────────
    print()
    print("Running HMC (NUTS)...")
    numpyro.set_host_device_count(1)
    kernel = NUTS(tortoise_model)
    mcmc = MCMC(kernel, num_warmup=500, num_samples=500, num_chains=1, progress_bar=False)
    mcmc.run(jax.random.PRNGKey(42))
    c_hmc = jax.nn.sigmoid(mcmc.get_samples()['logit_c'])
    # (samples, claims) for 1 chain
    c_hmc_flat = c_hmc

    # ── SVBP ──────────────────────────────────────────────────────
    print("Running SVBP...")
    import time
    t0 = time.time()
    svbp = TortoiseSVBP(n_particles=25, n_svgd_steps=15, svgd_lr=0.01,
                        damping=0.5, max_iter=40, tol=5e-3, seed=42)
    n_iter, converged = svbp.run(factors, evidence=evidence)
    elapsed = time.time() - t0

    print(f"  Converged: {converged} in {n_iter} iterations ({elapsed:.1f}s)")
    print()

    # ── Per-claim comparison ──────────────────────────────────────
    print("=" * 72)
    print("Per-Claim Results")
    print("=" * 72)
    print(f"{'Claim':<6} {'Type':<10} {'HMC mean':>10} {'HMC std':>8} "
          f"{'SVBP mean':>10} {'SVBP std':>8} {'W₂':>8}")
    print("-" * 72)

    w2_values = []

    for i in range(N_CLAIMS):
        hmc_i = c_hmc_flat[:, i]
        cid = f"c{i}"
        svbp_result = svbp.compute_confidence(cid)
        svbp_mean = svbp_result["mean"]
        svbp_std = np.sqrt(svbp_result["variance"])

        hmc_mean = float(jnp.mean(hmc_i))
        hmc_std = float(jnp.std(hmc_i))

        # For W₂, sample from fitted Beta for comparison
        key = jax.random.PRNGKey(42 + i)
        a_val, b_val = svbp_result["alpha"], svbp_result["beta"]
        svbp_samples = jax.random.beta(key, a_val, b_val, (2000,))

        w2 = float(wasserstein_2_1d(hmc_i, svbp_samples))
        w2_values.append(w2)

        # Type
        is_nand = any(i in p for p in NAND_PAIRS)
        is_impl = any(i in [p[0], p[1]] for p in IMPL_PAIRS)
        has_evid = EVIDENCE_ALPHA[i] > 1.0
        ctype = ""
        if is_nand: ctype += "NAND"
        if is_impl: ctype += "IMPL" if not ctype else "+IMPL"
        if has_evid: ctype += "+evid" if ctype else "evid"
        if not ctype: ctype = "free"

        print(f"{CLAIM_NAMES[i]:<6} {ctype:<10} {hmc_mean:>10.4f} {hmc_std:>8.4f} "
              f"{svbp_mean:>10.4f} {svbp_std:>8.4f} {w2:>8.4f}")

    print("-" * 72)
    w2_mean = np.mean(w2_values)
    w2_max = np.max(w2_values)
    print(f"  W₂ mean: {w2_mean:.4f}  max: {w2_max:.4f}")

    # ── Verdict ───────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("GATE 2 VERDICT")
    print("=" * 72)

    w2_pass = w2_max < 0.10  # relaxed target from Gate 2 spec
    speed_pass = elapsed < 5.0
    conv_pass = converged

    print(f"  W₂ < 0.10 (all claims):  {'✓ PASS' if w2_pass else '✗ FAIL'} (max={w2_max:.4f})")
    print(f"  Speed < 5s (M1 CPU):     {'✓ PASS' if speed_pass else '✗ FAIL'} ({elapsed:.1f}s)")
    print(f"  Converged:               {'✓' if conv_pass else '✗'} in {n_iter} iters (informational)")

    if w2_pass and speed_pass:
        print()
        print("  ★ GATE 2 PASSES ★ — Factor-graph SVBP matches HMC within W₂ < 0.10")
        print("    Proceed to Gate 3 (Smart Particle Storage).")
    else:
        print()
        print("  ⚠️ GATE 2 NEEDS WORK — see failures above.")

    return {
        "w2_mean": w2_mean, "w2_max": w2_max,
        "speed": elapsed, "converged": converged, "n_iter": n_iter,
    }


if __name__ == "__main__":
    main()
