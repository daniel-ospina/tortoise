# DEPRECATED: SVBP algorithm replaced by Expectation Propagation (ep.py). Kept for reference only.
"""SVBP (Stein Variational Belief Propagation) for Tortoise.

Per-factor Stein updates with cavity messages. Like EP but with
SVGD particles instead of Gauss-Jacobi quadrature for the tilt step.
Handles multimodality under NAND constraints (Gate 1 confirmed camps form).

Architecture mirrors tortoise/ep.py:
  Cavity: remove factor message from claim posterior
  Tilt: run SVGD on cavity × factor potential
  Project: fit Beta to particles → natural params → new messages
  Damp: weighted average with previous message

ponytail: particles live in-memory per claim. No FalkorDB storage yet.
"""
from __future__ import annotations

import jax.numpy as jnp
import jax
import jax.random as jrandom
import numpy as np


# ═══════════════════════════════════════════════════════════════════
# SVGD primitives (same as svbp_gate1.py)
# ═══════════════════════════════════════════════════════════════════

def sigmoid(x):
    return 1.0 / (1.0 + jnp.exp(-x))

@jax.jit
def rbf_kernel(x, h):
    n, d = x.shape
    diff = x[:, None, :] - x[None, :, :]
    sqdist = jnp.sum(diff ** 2, axis=-1)
    K = jnp.exp(-sqdist / (2 * h * h + 1e-8))
    grad_K = -K[:, :, None] * diff / (h * h + 1e-8)
    return K, grad_K

@jax.jit
def median_heuristic(x):
    n = x.shape[0]
    diff = x[:, None, :] - x[None, :, :]
    sqdist = jnp.sum(diff ** 2, axis=-1)
    triu = sqdist[jnp.triu_indices(n, k=1)]
    return jnp.sqrt(jnp.median(triu) / 2 + 1e-8)

@jax.jit
def svgd_update(x, grad_log_p, h):
    n, d = x.shape
    K, grad_K = rbf_kernel(x, h)
    term1 = jnp.dot(K, grad_log_p) / n
    term2 = jnp.sum(grad_K, axis=0) / n
    return term1 + term2


# ═══════════════════════════════════════════════════════════════════
# Beta parameter fitting from particles
# ═══════════════════════════════════════════════════════════════════

def moments_to_beta_params(m1, m2):
    """Convert E[c] and E[c²] to Beta(α, β). Min clamp at 0.01.

    Guards against NaN/Inf inputs (returns uniform fallback).
    """
    # Guard: NaN or Inf moments → uniform fallback
    if not (jnp.isfinite(m1) and jnp.isfinite(m2)):
        return (1.0, 1.0)
    var = max(m2 - m1 * m1, 1e-12)
    if var <= 1e-12:  # ponytail: near-zero variance = degenerate → uniform
        return (1.0, 1.0)
    if var >= m1 * (1 - m1) * 0.999:
        return (1.0, 1.0)  # fallback to uniform
    total = m1 * (1 - m1) / var - 1
    if total <= 0:
        return (1.0, 1.0)
    alpha = max(total * m1, 0.01)
    beta = max(total * (1 - m1), 0.01)
    return (float(alpha), float(beta))


# ═══════════════════════════════════════════════════════════════════
# TortoiseSVBP
# ═══════════════════════════════════════════════════════════════════

@jax.jit
def _tilt_log_prob(y_pair, cav_alpha_a, cav_beta_a, cav_alpha_b, cav_beta_b, is_nand, weight):
    """Log-prob for one particle pair in tilted distribution.
    All params are explicit (no closures) so JAX traces once.
    """
    c_a = sigmoid(y_pair[0])
    c_b = sigmoid(y_pair[1])
    eps = 1e-12
    lp = (cav_alpha_a * jnp.log(c_a + eps) + cav_beta_a * jnp.log(1 - c_a + eps)
          + cav_alpha_b * jnp.log(c_b + eps) + cav_beta_b * jnp.log(1 - c_b + eps))
    # is_nand: 1.0 for NAND, 0.0 for IMPL
    nand_term = -weight * c_a * c_b
    impl_term = -weight * (c_a - c_b) ** 2
    lp = lp + is_nand * nand_term + (1 - is_nand) * impl_term
    return lp

# Batch gradient: (n,2) and scalars → (n,2)
_tilt_grad_fn = jax.grad(_tilt_log_prob)  # (2,) + scalars → (2,)
_tilt_grad_batch = jax.jit(jax.vmap(_tilt_grad_fn, in_axes=(0, None, None, None, None, None, None)))

class TortoiseSVBP:
    """Stein Variational Belief Propagation for Tortoise factor graphs.

    Parameters:
        n_particles: SVGD particles per factor update.
        n_svgd_steps: SVGD iterations per factor update (inner loop).
        svgd_lr: step size for per-factor SVGD.
        damping: message damping factor, 0 < λ ≤ 1.
        max_iter: max outer EP iterations.
        tol: convergence threshold.
    """

    def __init__(self, *, n_particles=50, n_svgd_steps=20, svgd_lr=0.01,
                 damping=0.5, max_iter=50, tol=1e-3, seed=42,
                 compress_after=5):
        self.n_particles = n_particles
        self.n_svgd_steps = n_svgd_steps
        self.svgd_lr = svgd_lr
        self.damping = damping
        if not (0 < damping <= 1):
            raise ValueError(
                f"damping must be in (0, 1], got {damping}. "
                f"d=0 freezes messages; d>1 causes oscillatory divergence."
            )
        self.max_iter = max_iter
        self.tol = tol
        self.compress_after = compress_after
        self.key = jrandom.PRNGKey(seed)

        # Messages: {(op_id, claim_id, rel_type): (msg_alpha, msg_beta)}
        self.messages: dict[tuple[str, str, str], tuple[float, float]] = {}
        # Fixed evidence priors (never updated during message passing)
        self.evidence_prior: dict[str, tuple[float, float]] = {}
        # Claim posteriors: {claim_id: (alpha, beta)} = evidence + sum(messages)
        self.posteriors: dict[str, tuple[float, float]] = {}
        # Gate 3: particle storage + compression
        self._particles: dict[str, jnp.ndarray] = {}  # active particles (logit space)
        self._summaries: dict[str, tuple[float, float]] = {}  # Beta summaries for compressed claims
        self._stale: dict[str, int] = {}  # iterations since last update per claim

    # ── Natural parameter helpers ─────────────────────────────────

    @staticmethod
    def _natural_from_beta(alpha, beta):
        """Beta(α,β) → (η₁=α-1, η₂=β-1)."""
        return (alpha - 1, beta - 1)

    @staticmethod
    def _beta_from_natural(eta1, eta2):
        """Natural params → Beta. Clamp ≥ 0.01."""
        return (max(eta1 + 1, 0.01), max(eta2 + 1, 0.01))

    # ── Message read/write ────────────────────────────────────────

    def _get_message(self, op_id, claim_id, rel_type="IMPL"):
        """Get cavity message (natural params), default (0,0)."""
        return self.messages.get((op_id, claim_id, rel_type), (0.0, 0.0))

    def _set_message(self, op_id, claim_id, msg_alpha, msg_beta, rel_type="IMPL"):
        self.messages[(op_id, claim_id, rel_type)] = (msg_alpha, msg_beta)

    def _get_posterior(self, claim_id):
        """Get claim posterior (α, β), default Beta(1,1)."""
        return self.posteriors.get(claim_id, (1.0, 1.0))

    def _set_posterior(self, claim_id, alpha, beta):
        self.posteriors[claim_id] = (alpha, beta)

    # ── Cavity distribution ───────────────────────────────────────

    def _cavity(self, claim_id, op_id, rel_type):
        """Posterior minus factor message = cavity (in natural params)."""
        post_alpha, post_beta = self._get_posterior(claim_id)
        post_eta = self._natural_from_beta(post_alpha, post_beta)
        msg_eta = self._get_message(op_id, claim_id, rel_type)
        cav_eta = (post_eta[0] - msg_eta[0], post_eta[1] - msg_eta[1])
        return self._beta_from_natural(*cav_eta)

    # ── Gate 3: particle lifecycle ────────────────────────────────

    def _init_particles(self, claim_id, alpha, beta):
        """Sample n_particles from Beta(α,β), store in logit space."""
        self.key, subkey = jrandom.split(self.key)
        c = jrandom.beta(subkey, alpha, beta, (self.n_particles,))
        y = jnp.log(c + 1e-8) - jnp.log(1 - c + 1e-8)
        self._particles[claim_id] = y
        self._stale[claim_id] = 0
        return y

    def _get_particles(self, claim_id, alpha, beta):
        """Get or initialize particles. Re-expands from summary if compressed."""
        if claim_id in self._particles:
            self._stale[claim_id] = 0  # mark active
            return self._particles[claim_id]
        if claim_id in self._summaries:
            # Re-expand from stored Beta summary
            summ_a, summ_b = self._summaries[claim_id]
            return self._init_particles(claim_id, summ_a, summ_b)
        return self._init_particles(claim_id, alpha, beta)

    def _maybe_compress(self, claim_id):
        """Compress particles to Beta summary if stale > threshold."""
        if claim_id not in self._particles:
            return
        self._stale[claim_id] = self._stale.get(claim_id, 0) + 1
        if self._stale[claim_id] >= self.compress_after:
            y = self._particles.pop(claim_id)
            c = sigmoid(y)
            m1 = float(jnp.mean(c))
            m2 = float(jnp.mean(c ** 2))
            self._summaries[claim_id] = moments_to_beta_params(m1, m2)
            del self._stale[claim_id]

    def _has_particles(self, claim_id):
        """Check if claim has active particles (not compressed)."""
        return claim_id in self._particles

    def compress_all(self):
        """Force-compress all active particles to Beta summaries."""
        for cid in list(self._particles.keys()):
            y = self._particles.pop(cid)
            c = sigmoid(y)
            m1 = float(jnp.mean(c))
            m2 = float(jnp.mean(c ** 2))
            self._summaries[cid] = moments_to_beta_params(m1, m2)
            self._stale.pop(cid, None)

    def expand_all(self):
        """Re-expand all compressed claims from summaries."""
        for cid, (a, b) in list(self._summaries.items()):
            self._init_particles(cid, a, b)
            del self._summaries[cid]

    # ── Per-factor SVGD tilt ──────────────────────────────────────

    def _tilt(self, y_a, y_b, cav_alpha_a, cav_beta_a,
              cav_alpha_b, cav_beta_b, op_type, weight):
        """Run SVGD on the tilted distribution for one factor."""
        is_nand = 1.0 if op_type == "NAND" else 0.0
        # Pass cavity params explicitly so JAX can trace once
        for _ in range(self.n_svgd_steps):
            y = jnp.stack([y_a, y_b], axis=-1)
            grad_lp = _tilt_grad_batch(
                y, cav_alpha_a, cav_beta_a, cav_alpha_b, cav_beta_b,
                is_nand, weight
            )
            h = median_heuristic(y) + 0.1
            phi = svgd_update(y, grad_lp, h)
            y = y + self.svgd_lr * phi
            y_a, y_b = y[:, 0], y[:, 1]
        return y_a, y_b

    # ── Single-factor SVBP update ─────────────────────────────────

    def _update_factor(self, op_id, op_type, input_ids, weight=1.0):
        """One SVBP iteration for a single factor.

        Steps: cavity → tilt → project → damp → write back.
        """
        if len(input_ids) < 2:
            return
        if len(input_ids) > 2:
            # ponytail: pairwise decomposition with unique sub-operator IDs
            # to avoid message key collision (each pair gets its own messages).
            for i in range(len(input_ids)):
                for j in range(i + 1, len(input_ids)):
                    sub_op_id = f"{op_id}_{i}_{j}"
                    self._update_factor(sub_op_id, op_type,
                                        [input_ids[i], input_ids[j]], weight)
            return

        id_a, id_b = input_ids

        # 1. Cavity
        cav_a = self._cavity(id_a, op_id, op_type)
        cav_b = self._cavity(id_b, op_id, op_type)

        # 2. Get/init particles from cavity
        y_a = self._get_particles(id_a, *cav_a)
        y_b = self._get_particles(id_b, *cav_b)

        # 3. Tilt: SVGD on cavity × factor
        y_a_new, y_b_new = self._tilt(
            y_a, y_b, *cav_a, *cav_b, op_type, weight
        )

        # Store updated particles
        self._particles[id_a] = y_a_new
        self._particles[id_b] = y_b_new
        self._stale[id_a] = 0
        self._stale[id_b] = 0

        # 4. Project: fit Beta to particles
        c_a = sigmoid(y_a_new)
        c_b = sigmoid(y_b_new)
        m1_a = float(jnp.mean(c_a))
        m2_a = float(jnp.mean(c_a ** 2))
        m1_b = float(jnp.mean(c_b))
        m2_b = float(jnp.mean(c_b ** 2))

        new_post_a = moments_to_beta_params(m1_a, m2_a)
        new_post_b = moments_to_beta_params(m1_b, m2_b)

        # 5. New message = tilted posterior - cavity (in natural params)
        new_post_eta_a = self._natural_from_beta(*new_post_a)
        cav_eta_a = self._natural_from_beta(*cav_a)
        raw_eta_a = (new_post_eta_a[0] - cav_eta_a[0],
                     new_post_eta_a[1] - cav_eta_a[1])

        new_post_eta_b = self._natural_from_beta(*new_post_b)
        cav_eta_b = self._natural_from_beta(*cav_b)
        raw_eta_b = (new_post_eta_b[0] - cav_eta_b[0],
                     new_post_eta_b[1] - cav_eta_b[1])

        # 6. Damp
        d = self.damping
        old_eta_a = self._get_message(op_id, id_a, op_type)
        old_eta_b = self._get_message(op_id, id_b, op_type)

        damped_a = (d * raw_eta_a[0] + (1 - d) * old_eta_a[0],
                    d * raw_eta_a[1] + (1 - d) * old_eta_a[1])
        damped_b = (d * raw_eta_b[0] + (1 - d) * old_eta_b[0],
                    d * raw_eta_b[1] + (1 - d) * old_eta_b[1])

        # Clamp to prevent drift
        clamp = 1000
        damped_a = (max(min(damped_a[0], clamp), -clamp),
                    max(min(damped_a[1], clamp), -clamp))
        damped_b = (max(min(damped_b[0], clamp), -clamp),
                    max(min(damped_b[1], clamp), -clamp))

        # 7. Write back
        self._set_message(op_id, id_a, *damped_a, op_type)
        self._set_message(op_id, id_b, *damped_b, op_type)

        # 8. Update claim posteriors
        self._update_claim_posterior(id_a)
        self._update_claim_posterior(id_b)

    def _update_claim_posterior(self, claim_id):
        """Sum all incoming messages + fixed evidence prior → posterior."""
        # Start from evidence prior (natural params)
        ev_alpha, ev_beta = self.evidence_prior.get(claim_id, (1.0, 1.0))
        total_eta1, total_eta2 = self._natural_from_beta(ev_alpha, ev_beta)
        for (op_id, cid, rel_type), (ma, mb) in self.messages.items():
            if cid == claim_id:
                total_eta1 += ma
                total_eta2 += mb
        alpha, beta = self._beta_from_natural(total_eta1, total_eta2)
        self._set_posterior(claim_id, alpha, beta)

    # ── Public API ────────────────────────────────────────────────

    def compute_confidence(self, claim_id):
        """Return {mean, variance, alpha, beta} for a claim."""
        a, b = self._get_posterior(claim_id)
        total = a + b
        return {
            "mean": a / total,
            "variance": (a * b) / (total * total * (total + 1)),
            "alpha": a,
            "beta": b,
        }

    @property
    def stats(self):
        """Return compression/memory stats for Gate 3 monitoring."""
        return {
            "active_particles": len(self._particles),
            "compressed": len(self._summaries),
            "total_claims": len(self.posteriors),
            "particle_bytes": sum(y.nbytes for y in self._particles.values()),
            "summary_bytes": len(self._summaries) * 16,  # 2 floats × 8 bytes
        }

    def run(self, factors, evidence=None, warm_start=False):
        """Run SVBP on a list of factors.

        Args:
            factors: list of (op_id, op_type, [input_ids], weight).
            evidence: dict of {claim_id: (alpha, beta)} for evidence priors.
            warm_start: if True, reuse existing particles/summaries.
                        if False (cold start), clear all state first.
        Returns:
            (iterations, converged).
        """
        if not warm_start:
            self.messages.clear()
            self.posteriors.clear()
            self._particles.clear()
            self._summaries.clear()
            self._stale.clear()

        # Set fixed evidence priors (never overwritten)
        if evidence:
            self.evidence_prior = dict(evidence)

        if warm_start:
            # Recompute posteriors from evidence + existing messages
            # (don't reset to evidence-only — that would discard message-passing work)
            all_cids = set(self.evidence_prior.keys()) | {cid for (_, cid, _) in self.messages}
            for cid in all_cids:
                self._update_claim_posterior(cid)
        else:
            # Cold start: posteriors = evidence priors only
            for cid, (alpha, beta) in self.evidence_prior.items():
                self._set_posterior(cid, alpha, beta)

        prev = {cid: self._get_posterior(cid) for cid in self.posteriors}

        import random
        for iteration in range(self.max_iter):
            random.shuffle(factors)
            for op_id, op_type, input_ids, weight in factors:
                self._update_factor(op_id, op_type, input_ids, weight)

            # Gate 3: compress stale claims
            for op_id, op_type, input_ids, weight in factors:
                for cid in input_ids:
                    self._maybe_compress(cid)

            # Convergence check (only active posteriors)
            max_change = 0.0
            for cid in self.posteriors:
                new_a, new_b = self._get_posterior(cid)
                old_a, old_b = prev.get(cid, (1.0, 1.0))
                change = max(
                    abs(new_a - old_a) / max(old_a, 1e-6),
                    abs(new_b - old_b) / max(old_b, 1e-6),
                )
                max_change = max(max_change, change)
                prev[cid] = (new_a, new_b)

            if max_change < self.tol:
                return iteration + 1, True

        return self.max_iter, False
