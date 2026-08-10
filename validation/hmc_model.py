"""NumPyro HMC validation model for Tortoise belief propagation.

Defines a 10-claim test graph with NAND/IMPL operators as differentiable
factor potentials. HMC (NUTS) provides ground-truth posterior samples
for validating EP, Beta Conjugate, and future SVBP implementations.

Claims have continuous confidence c ∈ [0,1]. To sample with HMC,
we work in unconstrained logit space: y = logit(c), then transform
back via sigmoid for factor evaluation.

Usage:
    python -m tortoise.validation.hmc_model
"""
import jax
import jax.numpy as jnp
import jax.random as jrandom
import numpyro
import numpyro.distributions as dist
from numpyro.infer import MCMC, NUTS


# ── Test graph definition ─────────────────────────────────────────

N_CLAIMS = 10

# Which claims are NAND-linked (pairs)
NAND_PAIRS = [(0, 1), (2, 3)]  # A-B, C-D
# #855: EP NAND base weight is 8.0 (weights.py NAND_BASE_WEIGHT) — the HMC
# reference must match so EP-vs-HMC W2 calibration compares like distributions.
NAND_WEIGHT = 8.0

# Which claims are IMPL-linked (source → target)
IMPL_PAIRS = [(4, 5), (6, 7), (8, 9)]  # E→F, G→H, I→J
IMPL_WEIGHT = 1.0

# Evidence: pseudo-observations as Beta parameters
# A has 3 supports → Beta(4, 1) prior → mean 0.8
# B has 1 support → Beta(2, 1) prior → mean 0.67
# Others: Beta(1, 1) uniform
# ponytail: Python lists, not JAX arrays, so `if alpha[i] > 1` works under tracing.
EVIDENCE_ALPHA = [4.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
EVIDENCE_BETA  = [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]


# ── HMC Model ─────────────────────────────────────────────────────

def tortoise_model():
    """Tortoise belief graph as a NumPyro probabilistic model.

    Latent variables: logit-transformed confidences y_i ∈ ℝ.
    Factors: NAND exclusion (penalizes both being high),
             IMPL agreement (penalizes disagreement).
    Evidence: Beta pseudo-observations.
    """
    # Latent confidences in logit space (unconstrained for HMC)
    logit_c = numpyro.sample(
        'logit_c',
        dist.Normal(0.0, 2.0).expand([N_CLAIMS])
    )
    c = jax.nn.sigmoid(logit_c)  # map to [0,1]

    # NAND factors: φ = exp(-w × c_a × c_b)
    for a, b in NAND_PAIRS:
        numpyro.factor(f'nand_{a}_{b}', -NAND_WEIGHT * c[a] * c[b])

    # IMPL factors: φ = exp(-w × (c_a - c_b)²)
    for src, tgt in IMPL_PAIRS:
        numpyro.factor(f'impl_{src}_{tgt}', -IMPL_WEIGHT * (c[src] - c[tgt]) ** 2)

    # Evidence as pseudo-counts: α supports, β opposes.
    # Factor: c_i^α × (1-c_i)^β → log: α log(c_i) + β log(1-c_i)
    # This pushes c_i toward α/(α+β) — e.g., α=4,β=1 → mode at 0.8.
    # Used as numpyro.factor so evidence influences the latent c_i.
    for i in range(N_CLAIMS):
        if EVIDENCE_ALPHA[i] > 1.0 or EVIDENCE_BETA[i] > 1.0:
            numpyro.factor(
                f'evidence_{i}',
                EVIDENCE_ALPHA[i] * jnp.log(c[i] + 1e-12)
                + EVIDENCE_BETA[i] * jnp.log(1 - c[i] + 1e-12),
            )


# ── Run HMC ───────────────────────────────────────────────────────

def run_hmc(num_warmup=1000, num_samples=1000, num_chains=4, seed=42):
    """Run NUTS on the Tortoise model. Returns MCMC object with samples."""
    numpyro.set_host_device_count(num_chains)
    kernel = NUTS(tortoise_model)
    mcmc = MCMC(
        kernel,
        num_warmup=num_warmup,
        num_samples=num_samples,
        num_chains=num_chains,
        progress_bar=False,
    )
    rng_key = jrandom.PRNGKey(seed)
    mcmc.run(rng_key)
    return mcmc


# ── Results ───────────────────────────────────────────────────────

def summarize(mcmc):
    """Print diagnostic summary for the HMC run."""
    samples = mcmc.get_samples()
    logit_c = samples['logit_c']
    c_samples = jax.nn.sigmoid(logit_c)

    print("=== HMC Diagnostics ===")
    # R-hat and effective sample size
    summary = numpyro.diagnostics.summary(samples, prob=0.9)
    for i in range(N_CLAIMS):
        key = f'logit_c[{i}]' if f'logit_c[{i}]' in summary else f'logit_c'
        if key in summary:
            stats = summary[key]
            r_hat = float(stats['r_hat']) if hasattr(stats['r_hat'], '__float__') else stats['r_hat']
            print(f"  claim {i}: R-hat={r_hat:.4f}")

    print()
    print("=== Marginal Summary (confidence in [0,1]) ===")
    claim_names = list("ABCDEFGHIJ")
    for i in range(N_CLAIMS):
        ci = c_samples[:, :, i].flatten() if c_samples.ndim == 3 else c_samples[:, i]
        mean = float(jnp.mean(ci))
        std = float(jnp.std(ci))
        q05 = float(jnp.quantile(ci, 0.05))
        q95 = float(jnp.quantile(ci, 0.95))
        nand_flag = "NAND" if any(i in p for p in NAND_PAIRS) else ""
        impl_flag = "IMPL" if any(i in [p[0], p[1]] for p in IMPL_PAIRS) else ""
        evid_flag = f"evid(α={EVIDENCE_ALPHA[i]:.0f})" if EVIDENCE_ALPHA[i] > 1 else ""
        flags = " ".join(filter(None, [nand_flag, impl_flag, evid_flag]))
        print(f"  {claim_names[i]} ({flags:25s}): "
              f"mean={mean:.4f}  std={std:.4f}  [{q05:.4f}, {q95:.4f}]")

    return c_samples


# ── Main ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Running HMC on 10-claim Tortoise graph...")
    print(f"  {len(NAND_PAIRS)} NAND pairs (w={NAND_WEIGHT})")
    print(f"  {len(IMPL_PAIRS)} IMPL pairs (w={IMPL_WEIGHT})")
    print()

    mcmc = run_hmc(num_warmup=500, num_samples=500, num_chains=1, seed=42)
    c_samples = summarize(mcmc)

    # Quick validation checks
    print()
    print("=== Validation Checks ===")
    ci_a = c_samples[:, :, 0].flatten() if c_samples.ndim == 3 else c_samples[:, 0]
    ci_b = c_samples[:, :, 1].flatten() if c_samples.ndim == 3 else c_samples[:, 1]
    ci_e = c_samples[:, :, 4].flatten() if c_samples.ndim == 3 else c_samples[:, 4]
    ci_f = c_samples[:, :, 5].flatten() if c_samples.ndim == 3 else c_samples[:, 5]
    ci_j = c_samples[:, :, 9].flatten() if c_samples.ndim == 3 else c_samples[:, 9]

    # NAND check: A has evidence → should be higher than B
    mean_a = float(jnp.mean(ci_a))
    mean_b = float(jnp.mean(ci_b))
    print(f"  NAND(A,B): A(mean={mean_a:.4f}) > B(mean={mean_b:.4f})? "
          f"{'✓' if mean_a > mean_b else '✗'}")

    # IMPL check: E and F should be correlated
    corr_ef = float(jnp.corrcoef(
        jnp.stack([ci_e, ci_f])
    )[0, 1])
    print(f"  IMPL(E→F): corr(E,F)={corr_ef:.4f} > 0? {'✓' if corr_ef > 0 else '✗'}")

    # Free claim check: J should be near 0.5
    mean_j = float(jnp.mean(ci_j))
    print(f"  Free claim J: mean={mean_j:.4f} ≈ 0.5? "
          f"{'✓' if 0.4 < mean_j < 0.6 else '✗'}")

    print()
    print("Done. HMC samples available in `c_samples`.")
