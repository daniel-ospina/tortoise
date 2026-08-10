"""validate_tortoise_ep.py — TortoiseEP vs HMC ground truth on NAND+IMPL graphs.

Runs the PRODUCTION TortoiseEP (tortoise.ep.TortoiseEP) against a live FalkorDBLite
projection, then compares posteriors against NumPyro HMC (NUTS) reference samples
from hmc_model.py.

Usage:
    cd tortoise && .venv/bin/python validation/validate_tortoise_ep.py
    # or: .venv/bin/python -m pytest validation/validate_tortoise_ep.py -v

Requires: pip install jax jaxlib numpyro falkordblite (all in tortoise .venv)
"""
from __future__ import annotations

import itertools
import math
import os
import random
import sys
import time
import tempfile
import functools

# Ensure tortoise package is importable
_syspath = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _syspath not in sys.path:
    sys.path.insert(0, _syspath)

import jax
import jax.numpy as jnp
import jax.random as jrandom
import numpy as np
import numpyro
from numpyro.infer import MCMC, NUTS

from tortoise.api import EventAPI, provenance
from tortoise.ep import TortoiseEP
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection

from hmc_model import (
    NAND_PAIRS, IMPL_PAIRS, NAND_WEIGHT, IMPL_WEIGHT,
    EVIDENCE_ALPHA, EVIDENCE_BETA, N_CLAIMS, tortoise_model,
)


# ═══════════════════════════════════════════════════════════════════
# helpers
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


@functools.lru_cache(maxsize=1)
def _get_hmc_samples():
    """Run HMC once, return c_samples array (n_chains × n_samples × N_CLAIMS)."""
    numpyro.set_host_device_count(1)
    kernel = NUTS(tortoise_model, dense_mass=True)
    mcmc = MCMC(kernel, num_warmup=1000, num_samples=2000, num_chains=1,
                progress_bar=False)
    mcmc.run(jrandom.PRNGKey(42))
    samples = mcmc.get_samples()
    c_samples = jax.nn.sigmoid(samples["logit_c"])
    return c_samples


def _hmc_marginal(c_samples, claim_idx):
    if c_samples.ndim == 3:
        return c_samples[:, :, claim_idx].flatten()
    return c_samples[:, claim_idx]


def _hmc_mean_std(c_samples, claim_idx):
    c = _hmc_marginal(c_samples, claim_idx)
    return float(jnp.mean(c)), float(jnp.std(c))


# ═══════════════════════════════════════════════════════════════════
# graph builder: 10-claim graph matching hmc_model.py
# ═══════════════════════════════════════════════════════════════════

def _build_10claim_graph():
    """Build a 10-claim + 5-operator graph via EventAPI + FalkorProjection.

    Returns (projection, claim_ids, operator_ids, temp_dir).
    The caller MUST call projection.close() when done.
    """
    tmpdir = tempfile.mkdtemp(prefix="tortoise_ep_validation_")
    db_path = os.path.join(tmpdir, "falkor.db")
    log_path = os.path.join(tmpdir, "events.jsonl")

    log = EventLog(log_path)
    proj = FalkorProjection(db_path, graph_name="tortoise_test")
    api = EventAPI(log, initiated_by="extractor", agent_id="test",
                   projection=proj)

    # Create claims c0..c9
    claim_ids = []
    prov = provenance("test_graph", [0, 1], "", extracted_by="test@0")
    for i in range(N_CLAIMS):
        cid = api.add_point(f"claim_{i}", f"ctx_{i}", prov)
        claim_ids.append(cid)

    # Seed evidence: set ep_alpha/ep_beta on nodes with prior evidence.
    # Matches EVIDENCE_ALPHA/BETA from hmc_model.py.
    for i in range(N_CLAIMS):
        if EVIDENCE_ALPHA[i] > 1.0 or EVIDENCE_BETA[i] > 1.0:
            proj.g.query(
                "MATCH (n:Point {id:$id}) "
                "SET n.ep_alpha=$a, n.ep_beta=$b",
                params={"id": claim_ids[i], "a": EVIDENCE_ALPHA[i],
                        "b": EVIDENCE_BETA[i]},
            )

    # Create NAND operators
    nand_op_ids = []
    for a, b in NAND_PAIRS:
        op_id = api.add_operator(
            "NAND", [claim_ids[a], claim_ids[b]],
            "criteria-tensions", prov,
            content=f"NAND(c{a},c{b})",
        )
        nand_op_ids.append(op_id)

    # Create IMPL operators
    impl_op_ids = []
    for src, tgt in IMPL_PAIRS:
        op_id = api.add_operator(
            "IMPL", [claim_ids[src], claim_ids[tgt]],
            "logical-implication", prov,
            content=f"IMPL(c{src}→c{tgt})",
        )
        impl_op_ids.append(op_id)

    all_op_ids = nand_op_ids + impl_op_ids
    return proj, claim_ids, all_op_ids, tmpdir


def _build_nand_strong_graph():
    """Strong NAND: #855 NAND base weight (8.0) drives the constraint.
    (Stale pre-#855: 'resolution-event context → w=3.0' — that context
    multiplier system was removed; EP NAND is now a flat 8.0.)

    The EP weight is graph-derived (not hardcoded). resolution-event context
    gives the highest single multiplier (3.0×). With both claims at Beta(1,1)
    (uniform prior, mean=0.5), w=8.0 NAND pushes confidence down measurably.
    """
    tmpdir = tempfile.mkdtemp(prefix="tortoise_ep_nand_strong_")
    db_path = os.path.join(tmpdir, "falkor.db")
    log_path = os.path.join(tmpdir, "events.jsonl")

    log = EventLog(log_path)
    proj = FalkorProjection(db_path, graph_name="tortoise_test")
    api = EventAPI(log, initiated_by="extractor", agent_id="test",
                   projection=proj)
    prov = provenance("test", [0, 1], "", extracted_by="test@0")

    c0 = api.add_point("claim_a", "ctx", prov)
    c1 = api.add_point("claim_b", "ctx", prov)
    op_id = api.add_operator("NAND", [c0, c1], "resolution-event", prov,
                             content="NAND(c0,c1)")

    return proj, [c0, c1], [op_id], tmpdir


def _build_frustrated_cycle_graph():
    """NAND(c0,c1) + IMPL(c1,c2) + IMPL(c2,c0) — frustrated 3-cycle."""
    tmpdir = tempfile.mkdtemp(prefix="tortoise_ep_frustrated_")
    db_path = os.path.join(tmpdir, "falkor.db")
    log_path = os.path.join(tmpdir, "events.jsonl")

    log = EventLog(log_path)
    proj = FalkorProjection(db_path, graph_name="tortoise_test")
    api = EventAPI(log, initiated_by="extractor", agent_id="test",
                   projection=proj)
    prov = provenance("test", [0, 1], "", extracted_by="test@0")

    c0 = api.add_point("claim_0", "ctx", prov)
    c1 = api.add_point("claim_1", "ctx", prov)
    c2 = api.add_point("claim_2", "ctx", prov)
    op_nand = api.add_operator("NAND", [c0, c1], "ctx", prov, content="NAND(c0,c1)")
    op_impl1 = api.add_operator("IMPL", [c1, c2], "ctx", prov, content="IMPL(c1→c2)")
    op_impl2 = api.add_operator("IMPL", [c2, c0], "ctx", prov, content="IMPL(c2→c0)")

    return proj, [c0, c1, c2], [op_nand, op_impl1, op_impl2], tmpdir


# ═══════════════════════════════════════════════════════════════════
# Test 1: TortoiseEP W₂ vs HMC on 10-claim graph
# ═══════════════════════════════════════════════════════════════════

def test_tortoise_ep_nand_w3():
    """TortoiseEP vs HMC on 10-claim graph (2 NAND w=3, 3 IMPL).

    W₂ < 0.20 for IMPL claims (Beta approximation, limited by quadrature resolution).
    W₂ < 0.35 for NAND claims (EP unimodal Beta inherently has higher W₂ vs
      HMC's bimodal posterior — this is a structural limitation, not a bug).
    """
    proj, claim_ids, op_ids, tmpdir = _build_10claim_graph()
    try:
        ep = TortoiseEP(proj, damping=0.5, n_quad=8, max_iter=50, tol=1e-3)
        n_iter, converged = ep.run(op_ids, max_hops=3)

        assert converged, f"EP did not converge after {n_iter} iterations"
        assert n_iter > 0, "EP returned 0 iterations"

        # Collect EP posteriors
        ep_post: dict[int, tuple[float, float]] = {}
        for i, cid in enumerate(claim_ids):
            result = ep.compute_confidence(cid)
            ep_post[i] = (result["alpha"], result["beta"])

        # HMC reference
        c_samples = _get_hmc_samples()

        # W₂ for each claim
        n_samples = 3000
        np.random.seed(42)
        for i in range(N_CLAIMS):
            a, b = ep_post[i]
            ep_samples = np.random.beta(a, b, n_samples)
            hmc_samples = _hmc_marginal(c_samples, i)
            w2 = _wasserstein_2_1d(ep_samples, hmc_samples)

            is_nand = any(i in p for p in NAND_PAIRS)
            # NAND threshold 0.35:
            # EP approximates the posterior as unimodal Beta, but NAND constraints
            # create bimodal HMC posteriors (both claims low, or one high/one low).
            # A single Beta cannot capture this bimodality → structural W₂ floor.
            # 0.35 is the empirical ceiling across 10+ runs with w=3 NAND.
            # IMPL threshold 0.20:
            # IMPL constraints produce unimodal posteriors that Beta fits well,
            # but quadrature discretization (n_quad=8) and EP moment matching
            # introduce small systematic error. 0.20 is the empirical ceiling.
            if is_nand:
                assert w2 < 0.35, \
                    f"claim c{i} (NAND) W₂={w2:.4f} exceeds NAND threshold 0.35"
            else:
                assert w2 < 0.20, \
                    f"claim c{i} (IMPL/free) W₂={w2:.4f} exceeds IMPL threshold 0.20"
    finally:
        proj.close()
        # cleanup
        for f in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)


# ═══════════════════════════════════════════════════════════════════
# Test 2: strong NAND (w=8.0) — constraint is working
# ═══════════════════════════════════════════════════════════════════

def test_tortoise_ep_nand_strong():
    """Strong NAND (w=8.0, #855 NAND base weight).

    The NAND constraint pushes both claims below the default 0.5 prior.
    Verify: max confidence < 0.45 (NAND working), no NaN.
    """
    proj, claim_ids, op_ids, tmpdir = _build_nand_strong_graph()
    try:
        ep = TortoiseEP(proj, damping=0.5, n_quad=8, max_iter=80, tol=1e-3)
        n_iter, converged = ep.run(op_ids, max_hops=3)

        # Collect confidences
        confs = []
        for cid in claim_ids:
            result = ep.compute_confidence(cid)
            mean = result["mean"]
            assert not np.isnan(mean), f"NaN confidence for {cid}"
            assert not np.isnan(result["alpha"]), f"NaN alpha for {cid}"
            assert not np.isnan(result["beta"]), f"NaN beta for {cid}"
            confs.append(mean)

        # Both pushed below default 0.5 prior (NAND constraint working)
        max_conf = max(confs)
        assert max_conf < 0.45, \
            f"NAND should push conf below 0.45 (default prior is 0.5), got max={max_conf:.4f}"
        print(f"  (NAND strong: means={[f'{c:.4f}' for c in confs]}, max={max_conf:.4f})")
    finally:
        proj.close()
        for f in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)


# ═══════════════════════════════════════════════════════════════════
# Test 3: frustrated 3-cycle — converges or reasonable
# ═══════════════════════════════════════════════════════════════════

def test_tortoise_ep_mixed_frustrated():
    """NAND(c0,c1) + IMPL(c1,c2) + IMPL(c2,c0) — frustrated cycle.

    Run 100 iterations. Assert: converges OR posteriors reasonable
    (no NaN, means in [0.01, 0.99]).
    """
    proj, claim_ids, op_ids, tmpdir = _build_frustrated_cycle_graph()
    try:
        ep = TortoiseEP(proj, damping=0.5, n_quad=8, max_iter=100, tol=1e-4)
        n_iter, converged = ep.run(op_ids, max_hops=3)

        means = []
        for cid in claim_ids:
            result = ep.compute_confidence(cid)
            m = result["mean"]
            assert not np.isnan(m), f"NaN for {cid}"
            means.append(m)

        # All means in (0.01, 0.99) — not degenerate
        for i, m in enumerate(means):
            assert 0.01 < m < 0.99, \
                f"claim {i} mean={m:.6f} is degenerate (at boundary)"

        if not converged:
            # Frustrated cycles may not converge; that's OK if means are reasonable
            print(f"  (frustrated cycle: {n_iter} iters, converged={converged}, "
                  f"means={[f'{m:.4f}' for m in means]})")
        else:
            print(f"  (frustrated cycle: converged in {n_iter} iters, "
                  f"means={[f'{m:.4f}' for m in means]})")
    finally:
        proj.close()
        for f in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)


# ═══════════════════════════════════════════════════════════════════
# Test 4: latency — wall-clock time < 1s for 10-claim graph
# ═══════════════════════════════════════════════════════════════════

def test_tortoise_ep_latency():
    """Measure wall-clock time for TortoiseEP.run() on 10-claim graph.

    Assert < 1s (quadrature-based EP should be fast).
    """
    proj, claim_ids, op_ids, tmpdir = _build_10claim_graph()
    try:
        ep = TortoiseEP(proj, damping=0.5, n_quad=8, max_iter=50, tol=1e-3)

        t0 = time.perf_counter()
        n_iter, converged = ep.run(op_ids, max_hops=3)
        elapsed = time.perf_counter() - t0

        assert converged, f"EP did not converge (needed for latency test)"
        assert elapsed < 1.0, \
            f"TortoiseEP.run() took {elapsed:.3f}s, exceeding 1s budget"
        print(f"  (10-claim EP: {n_iter} iters, {elapsed*1000:.1f}ms)")
    finally:
        proj.close()
        for f in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)


# ═══════════════════════════════════════════════════════════════════
# Test 5: determinism — bit-exact repeatable posteriors
# ═══════════════════════════════════════════════════════════════════

def test_tortoise_ep_determinism():
    """Run TortoiseEP twice on same graph. Assert identical posteriors."""
    # Build graph once
    tmpdir = tempfile.mkdtemp(prefix="tortoise_ep_det_")
    db_path = os.path.join(tmpdir, "falkor.db")
    log_path = os.path.join(tmpdir, "events.jsonl")

    log = EventLog(log_path)
    proj = FalkorProjection(db_path, graph_name="tortoise_test")
    api = EventAPI(log, initiated_by="extractor", agent_id="test",
                   projection=proj)
    prov = provenance("test", [0, 1], "", extracted_by="test@0")

    c0 = api.add_point("a", "ctx", prov)
    c1 = api.add_point("b", "ctx", prov)
    c2 = api.add_point("c", "ctx", prov)
    op_n = api.add_operator("NAND", [c0, c1], "ctx", prov)
    op_i = api.add_operator("IMPL", [c1, c2], "ctx", prov)
    op_ids = [op_n, op_i]
    claim_ids = [c0, c1, c2]

    try:
        # Run 1
        ep1 = TortoiseEP(proj, damping=0.5, n_quad=8, max_iter=50, tol=1e-3)
        ep1.run(op_ids, max_hops=3)
        post1 = {cid: (ep1.compute_confidence(cid)["alpha"],
                       ep1.compute_confidence(cid)["beta"])
                 for cid in claim_ids}

        # Reset graph state by rebuilding
        proj2 = FalkorProjection(db_path, graph_name="tortoise_test")
        api2 = EventAPI(EventLog(log_path), initiated_by="extractor",
                        agent_id="test", projection=proj2)
        proj2.rebuild(api2.log)

        # Run 2
        ep2 = TortoiseEP(proj2, damping=0.5, n_quad=8, max_iter=50, tol=1e-3)
        ep2.run(op_ids, max_hops=3)
        post2 = {cid: (ep2.compute_confidence(cid)["alpha"],
                       ep2.compute_confidence(cid)["beta"])
                 for cid in claim_ids}

        for cid in claim_ids:
            a1, b1 = post1[cid]
            a2, b2 = post2[cid]
            assert math.isclose(a1, a2, rel_tol=1e-12), \
                f"alpha mismatch for {cid}: {a1} != {a2}"
            assert math.isclose(b1, b2, rel_tol=1e-12), \
                f"beta mismatch for {cid}: {b1} != {b2}"

        proj2.close()
    finally:
        proj.close()
        for f in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)


# ═══════════════════════════════════════════════════════════════════
# Test 6: complex graph — 20 claims, 5 NAND + 8 IMPL, mixed evidence
# ═══════════════════════════════════════════════════════════════════

def test_tortoise_ep_complex_graph():
    """20-claim graph with 5 NAND + 8 IMPL, mixed evidence on 4 claims.

    Run TortoiseEP. Run HMC on a subset of 5 claims (HMC is expensive).
    Assert W₂ < 0.20 for the 5 HMC-validated claims.
    Assert all 20 posteriors are valid (no NaN, means in [0.01, 0.99]).
    """
    import itertools

    tmpdir = tempfile.mkdtemp(prefix="tortoise_ep_complex_")
    db_path = os.path.join(tmpdir, "falkor.db")
    log_path = os.path.join(tmpdir, "events.jsonl")

    log = EventLog(log_path)
    proj = FalkorProjection(db_path, graph_name="tortoise_test")
    api = EventAPI(log, initiated_by="extractor", agent_id="test",
                   projection=proj)
    prov = provenance("complex_graph", [0, 1], "", extracted_by="test@0")

    n_claims = 20
    claim_ids = []
    for i in range(n_claims):
        cid = api.add_point(f"claim_{i}", f"ctx_{i % 4}", prov)
        claim_ids.append(cid)

    # Mixed evidence: strong on c0,c5, moderate on c10, weak on c15
    evidence = {}
    for cid in claim_ids:
        evidence[cid] = (1.0, 1.0)  # default uniform
    evidence[claim_ids[0]] = (8.0, 1.0)   # c0: strong support (mean≈0.89)
    evidence[claim_ids[5]] = (1.0, 6.0)   # c5: strong oppose (mean≈0.14)
    evidence[claim_ids[10]] = (5.0, 2.0)  # c10: moderate support (mean≈0.71)
    evidence[claim_ids[15]] = (3.0, 3.0)  # c15: uncertain (mean=0.5, high n)

    for cid, (a, b) in evidence.items():
        if a > 1.0 or b > 1.0:
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.ep_alpha=$a, n.ep_beta=$b",
                params={"id": cid, "a": a, "b": b},
            )

    # 5 NAND pairs (on claims 0-9)
    nand_pairs = [(0, 1), (2, 3), (4, 5), (6, 7), (8, 9)]
    nand_op_ids = []
    for a, b in nand_pairs:
        op_id = api.add_operator(
            "NAND", [claim_ids[a], claim_ids[b]],
            "criteria-tensions", prov,
            content=f"NAND(c{a},c{b})",
        )
        nand_op_ids.append(op_id)

    # 8 IMPL operators (on claims 10-19, plus cross-connections)
    impl_pairs = [(10, 11), (11, 12), (12, 13), (13, 14),
                  (15, 16), (16, 17), (17, 18), (18, 19)]
    impl_op_ids = []
    for src, tgt in impl_pairs:
        op_id = api.add_operator(
            "IMPL", [claim_ids[src], claim_ids[tgt]],
            "logical-implication", prov,
            content=f"IMPL(c{src}→c{tgt})",
        )
        impl_op_ids.append(op_id)

    all_op_ids = nand_op_ids + impl_op_ids

    try:
        ep = TortoiseEP(proj, damping=0.5, n_quad=8, max_iter=80, tol=1e-3)
        n_iter, converged = ep.run(all_op_ids, max_hops=3)
        assert converged, f"EP did not converge after {n_iter} iterations"

        # All 20 posteriors valid
        for i, cid in enumerate(claim_ids):
            result = ep.compute_confidence(cid)
            m = result["mean"]
            assert not np.isnan(m), f"NaN mean for claim {i}"
            assert not np.isnan(result["alpha"]), f"NaN alpha for claim {i}"
            assert not np.isnan(result["beta"]), f"NaN beta for claim {i}"
            assert 0.01 < m < 0.99, \
                f"claim {i} mean={m:.6f} degenerate (at boundary)"

        # ── HMC on a 5-claim subset (claims 10-14: IMPL chain with evidence on c10) ──
        # Build NumPyro model for just those 5 claims
        hmc_claim_indices = [10, 11, 12, 13, 14]
        hmc_claim_ids = [claim_ids[i] for i in hmc_claim_indices]

        # Map global idx → local idx (0..4)
        local_map = {g: l for l, g in enumerate(hmc_claim_indices)}

        # Subset operators: only those connecting within the 5-claim subset
        sub_impl_pairs = [(10, 11), (11, 12), (12, 13), (13, 14)]
        sub_nand_pairs = []  # no NAND in this subset
        sub_n = len(hmc_claim_indices)

        # Subset evidence
        sub_evid_alpha = np.ones(sub_n)
        sub_evid_beta = np.ones(sub_n)
        for gi, li in local_map.items():
            sub_evid_alpha[li] = evidence[claim_ids[gi]][0]
            sub_evid_beta[li] = evidence[claim_ids[gi]][1]

        def sub_model():
            logit_c = numpyro.sample(
                'logit_c',
                numpyro.distributions.Normal(0.0, 2.0).expand([sub_n])
            )
            c = jax.nn.sigmoid(logit_c)
            for src, tgt in sub_impl_pairs:
                ls, lt = local_map[src], local_map[tgt]
                numpyro.factor(f'impl_{src}_{tgt}', -1.0 * (c[ls] - c[lt]) ** 2)
            for i in range(sub_n):
                if sub_evid_alpha[i] > 1.0 or sub_evid_beta[i] > 1.0:
                    numpyro.factor(
                        f'evidence_{i}',
                        sub_evid_alpha[i] * jnp.log(c[i] + 1e-12)
                        + sub_evid_beta[i] * jnp.log(1 - c[i] + 1e-12),
                    )
            return c

        numpyro.set_host_device_count(1)
        kernel = NUTS(sub_model, dense_mass=True)
        mcmc = MCMC(kernel, num_warmup=500, num_samples=500, num_chains=1,
                    progress_bar=False)
        mcmc.run(jrandom.PRNGKey(123))
        c_samples = jax.nn.sigmoid(mcmc.get_samples()["logit_c"])

        # W₂ for each of the 5 HMC-validated claims
        np.random.seed(42)
        n_samples = 2000
        for li, gi in enumerate(hmc_claim_indices):
            result = ep.compute_confidence(claim_ids[gi])
            a, b = result["alpha"], result["beta"]
            ep_draws = np.random.beta(a, b, n_samples)
            hmc_draws = np.asarray(c_samples[:, li]).flatten()
            w2 = _wasserstein_2_1d(ep_draws, hmc_draws)
            assert w2 < 0.20, \
                f"claim c{gi} W₂={w2:.4f} exceeds HMC threshold 0.20"

        print(f"  (complex 20-claim graph: {n_iter} iters, converged={converged})")
    finally:
        proj.close()
        for f in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)


# ═══════════════════════════════════════════════════════════════════
# Test 7: dense NAND — 6 claims, all 15 NAND pairs
# ═══════════════════════════════════════════════════════════════════

def test_tortoise_ep_dense_nand():
    """6 claims, all 15 NAND pairs (complete NAND graph).

    Run TortoiseEP 100 iterations. Assert: converges or posteriors
    reasonable. Verify not all collapse to 0 — at least 2 claims
    have mean > 0.3.
    """
    import itertools

    tmpdir = tempfile.mkdtemp(prefix="tortoise_ep_dense_nand_")
    db_path = os.path.join(tmpdir, "falkor.db")
    log_path = os.path.join(tmpdir, "events.jsonl")

    log = EventLog(log_path)
    proj = FalkorProjection(db_path, graph_name="tortoise_test")
    api = EventAPI(log, initiated_by="extractor", agent_id="test",
                   projection=proj)
    prov = provenance("dense_nand", [0, 1], "", extracted_by="test@0")

    n = 6
    claim_ids = []
    for i in range(n):
        cid = api.add_point(f"claim_{i}", "ctx", prov)
        claim_ids.append(cid)

    # All 15 NAND pairs
    all_pairs = list(itertools.combinations(range(n), 2))
    assert len(all_pairs) == 15, f"expected 15 pairs, got {len(all_pairs)}"

    op_ids = []
    for a, b in all_pairs:
        op_id = api.add_operator(
            "NAND", [claim_ids[a], claim_ids[b]],
            "criteria-tensions", prov,
            content=f"NAND(c{a},c{b})",
        )
        op_ids.append(op_id)

    try:
        ep = TortoiseEP(proj, damping=0.5, n_quad=8, max_iter=100, tol=1e-4)
        n_iter, converged = ep.run(op_ids, max_hops=3)

        means = []
        for i, cid in enumerate(claim_ids):
            result = ep.compute_confidence(cid)
            m = result["mean"]
            assert not np.isnan(m), f"NaN for claim {i}"
            assert 0.01 < m < 0.99, f"claim {i} mean={m:.6f} degenerate"
            means.append(m)

        # At least 2 claims have mean > 0.3 (NAND doesn't kill everything)
        above_threshold = sum(1 for m in means if m > 0.3)
        assert above_threshold >= 2, \
            f"Only {above_threshold} claims above 0.3 (means={[f'{m:.4f}' for m in means]})"

        print(f"  (dense NAND: {n_iter} iters, converged={converged}, "
              f"means={[f'{m:.4f}' for m in means]}, above_0.3={above_threshold})")
    finally:
        proj.close()
        for f in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)


# ═══════════════════════════════════════════════════════════════════
# Test 8: IMPL chain — evidence propagation through 10 hops
# ═══════════════════════════════════════════════════════════════════

def test_tortoise_ep_chain_propagation():
    """IMPL chain of 10 claims: c0→c1→...→c9.

    Strong evidence on c0 (α=10,β=1). Assert: confidence decays along
    chain (c0 > c1 > c2). Assert c9 > 0.5 (evidence propagates through
    9 hops, albeit weakly).
    """
    tmpdir = tempfile.mkdtemp(prefix="tortoise_ep_chain_")
    db_path = os.path.join(tmpdir, "falkor.db")
    log_path = os.path.join(tmpdir, "events.jsonl")

    log = EventLog(log_path)
    proj = FalkorProjection(db_path, graph_name="tortoise_test")
    api = EventAPI(log, initiated_by="extractor", agent_id="test",
                   projection=proj)
    prov = provenance("chain", [0, 1], "", extracted_by="test@0")

    n = 10
    claim_ids = []
    for i in range(n):
        cid = api.add_point(f"claim_{i}", "ctx", prov)
        claim_ids.append(cid)

    # Strong evidence on c0
    proj.g.query(
        "MATCH (n:Point {id:$id}) SET n.ep_alpha=$a, n.ep_beta=$b",
        params={"id": claim_ids[0], "a": 10.0, "b": 1.0},
    )

    # IMPL chain: c0→c1, c1→c2, ..., c8→c9
    # #855: plain NAND carries the 8.0 base weight, so evidence
    # propagates measurably through the chain.
    op_ids = []
    for i in range(n - 1):
        op_id = api.add_operator(
            "IMPL", [claim_ids[i], claim_ids[i + 1]],
            "resolution-event", prov,
            content=f"IMPL(c{i}→c{i + 1})",
        )
        op_ids.append(op_id)

    try:
        ep = TortoiseEP(proj, damping=0.5, n_quad=8, max_iter=80, tol=1e-3)
        n_iter, converged = ep.run(op_ids, max_hops=3)
        assert converged, f"EP did not converge after {n_iter} iterations"

        means = []
        for i, cid in enumerate(claim_ids):
            result = ep.compute_confidence(cid)
            m = result["mean"]
            assert not np.isnan(m), f"NaN for claim {i}"
            assert 0.01 < m < 0.99, f"claim {i} mean={m:.6f} degenerate"
            means.append(m)

        # Confidence decays: c0 > c1 > c2
        assert means[0] > means[1], \
            f"c0 ({means[0]:.4f}) should be > c1 ({means[1]:.4f})"
        assert means[1] > means[2], \
            f"c1 ({means[1]:.4f}) should be > c2 ({means[2]:.4f})"

        # Evidence propagates through 9 hops — c9 > 0.5
        assert means[9] > 0.5, \
            f"c9 ({means[9]:.4f}) should be > 0.5 (evidence propagated)"

        print(f"  (chain: {n_iter} iters, means={[f'{m:.4f}' for m in means]})")
    finally:
        proj.close()
        for f in os.listdir(tmpdir):
            os.unlink(os.path.join(tmpdir, f))
        os.rmdir(tmpdir)


# ═══════════════════════════════════════════════════════════════════
# Test 9: random graphs — basic fuzz test
# ═══════════════════════════════════════════════════════════════════

def test_tortoise_ep_random_graphs():
    """Generate 10 random graphs (3-8 claims, 2-8 random NAND/IMPL,
    random evidence). Run TortoiseEP. Assert: all 10 complete
    without NaN, all posteriors in [0.01, 0.99].
    """
    import itertools

    rng = random.Random(42)
    graphs: list[tuple[list[str], list[str], FalkorProjection, str]] = []

    for g_idx in range(10):
        n_claims = rng.randint(3, 8)
        n_ops = rng.randint(2, 8)

        tmpdir = tempfile.mkdtemp(prefix=f"tortoise_ep_fuzz_{g_idx}_")
        db_path = os.path.join(tmpdir, "falkor.db")
        log_path = os.path.join(tmpdir, "events.jsonl")

        log = EventLog(log_path)
        proj = FalkorProjection(db_path, graph_name="tortoise_test")
        api = EventAPI(log, initiated_by="extractor", agent_id="test",
                       projection=proj)
        prov = provenance(f"fuzz_{g_idx}", [0, 1], "", extracted_by="test@0")

        claim_ids = []
        for i in range(n_claims):
            cid = api.add_point(f"claim_{i}", "ctx", prov)
            claim_ids.append(cid)

        # Random evidence (50% chance per claim)
        for cid in claim_ids:
            if rng.random() < 0.5:
                a = rng.uniform(1.5, 8.0)
                b = rng.uniform(1.0, 5.0)
                proj.g.query(
                    "MATCH (n:Point {id:$id}) SET n.ep_alpha=$a, n.ep_beta=$b",
                    params={"id": cid, "a": a, "b": b},
                )

        # Generate random operators (NAND or IMPL on random claim pairs)
        all_pairs = list(itertools.combinations(range(n_claims), 2))
        seen_pairs: set[tuple[int, int, str]] = set()
        op_ids = []
        for _ in range(min(n_ops, len(all_pairs))):
            a, b = rng.choice(all_pairs)
            op_type = rng.choice(["NAND", "IMPL"])
            key = (a, b, op_type)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            contexts = {"NAND": "criteria-tensions", "IMPL": "logical-implication"}
            op_id = api.add_operator(
                op_type, [claim_ids[a], claim_ids[b]],
                contexts[op_type], prov,
                content=f"{op_type}(c{a},c{b})",
            )
            op_ids.append(op_id)

        graphs.append((claim_ids, op_ids, proj, tmpdir))

    # Run all 10 and collect results
    failures = []
    for g_idx, (claim_ids, op_ids, proj, tmpdir) in enumerate(graphs):
        try:
            if not op_ids:
                continue  # no operators to run
            ep = TortoiseEP(proj, damping=0.5, n_quad=8, max_iter=80, tol=1e-3)
            n_iter, converged = ep.run(op_ids, max_hops=3)

            for i, cid in enumerate(claim_ids):
                result = ep.compute_confidence(cid)
                m = result["mean"]
                if np.isnan(m):
                    failures.append(f"graph {g_idx} claim {i}: NaN")
                elif m <= 0.01 or m >= 0.99:
                    failures.append(f"graph {g_idx} claim {i}: mean={m:.6f} (boundary)")
                if np.isnan(result["alpha"]) or np.isnan(result["beta"]):
                    failures.append(f"graph {g_idx} claim {i}: NaN alpha/beta")

            print(f"  (fuzz graph {g_idx}: {len(claim_ids)} claims, {len(op_ids)} ops, "
                  f"{n_iter} iters, converged={converged})")
        finally:
            proj.close()
            for f in os.listdir(tmpdir):
                os.unlink(os.path.join(tmpdir, f))
            os.rmdir(tmpdir)

    assert not failures, f"{len(failures)} random graph failures:\n" + "\n".join(failures)
    print(f"  (fuzz: all 10 random graphs passed)")


# ═══════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("═══ TortoiseEP vs HMC Validation ═══\n")

    print("Test 1: NAND w=3 + IMPL (W₂ vs HMC)")
    test_tortoise_ep_nand_w3()
    print("  ✓ PASS\n")

    print("Test 2: Strong NAND (w=8.0)")
    test_tortoise_ep_nand_strong()
    print("  ✓ PASS\n")

    print("Test 3: Frustrated 3-cycle")
    test_tortoise_ep_mixed_frustrated()
    print("  ✓ PASS\n")

    print("Test 4: Latency (<1s)")
    test_tortoise_ep_latency()
    print("  ✓ PASS\n")

    print("Test 5: Determinism (bit-exact)")
    test_tortoise_ep_determinism()
    print("  ✓ PASS\n")

    print("Test 6: Complex graph (20 claims, 5 NAND + 8 IMPL, HMC subset)")
    test_tortoise_ep_complex_graph()
    print("  ✓ PASS\n")

    print("Test 7: Dense NAND (6 claims, 15 NAND pairs)")
    test_tortoise_ep_dense_nand()
    print("  ✓ PASS\n")

    print("Test 8: IMPL chain (10 hops, evidence propagation)")
    test_tortoise_ep_chain_propagation()
    print("  ✓ PASS\n")

    print("Test 9: Random graphs (10 fuzz tests)")
    test_tortoise_ep_random_graphs()
    print("  ✓ PASS\n")

    print("═══ All 9 validation tests passed ═══")
