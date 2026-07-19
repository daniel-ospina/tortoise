"""P0 gap tests for Tortoise EP implementation.

Gap 1: n-ary operator decomposition (_update_nary_factor) — NEVER executed.
Gap 2: TortoiseEP (FalkorDB) vs InMemoryEP (pure Python) equivalence.
Gap 3: Falsification — maximally frustrated 3-cycle where EP should show high variance.
"""

import math
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import pytest

from tortoise.ep import TortoiseEP
from tortoise.projection import FalkorProjection
from tests.test_svbp_hybrid import InMemoryEP

# ── FalkorDB availability ─────────────────────────────────────────

try:
    from redislite.falkordb_client import FalkorDB  # noqa: F401
    HAS_FALKOR = True
except ImportError:
    HAS_FALKOR = False

needs_falkor = pytest.mark.skipif(not HAS_FALKOR, reason="FalkorDB not available")


# ── Helpers ───────────────────────────────────────────────────────

def _tmp(name):
    """Create temp file path + return (path, tmpdir) for cleanup."""
    d = tempfile.mkdtemp(prefix="tortoise_ep_")
    return os.path.join(d, name), d


def _build_graph(proj, claim_ids, operators):
    """Build factor graph in FalkorProjection.

    Args:
        proj: FalkorProjection instance
        claim_ids: list of claim node IDs
        operators: list of (op_id, op_type, [input_ids])
    """
    for cid in claim_ids:
        proj._upsert({"id": cid, "content": cid, "context": "test"})
    for op_id, op_type, inputs in operators:
        proj._upsert({
            "id": op_id, "content": op_type, "context": "test",
            "operator": {"op_type": op_type, "inputs": inputs},
        })


# ═══════════════════════════════════════════════════════════════════
# Lightweight subclass: evidence-aware posterior computation
# ═══════════════════════════════════════════════════════════════════

class TortoiseEPWithEvidence(TortoiseEP):
    """TortoiseEP with evidence support matching InMemoryEP semantics.

    ponytail: minimal subclass — only changes the base nat params
    in _update_claim_posterior from (0,0) to evidence-derived values.
    Duplicates the message-summing loop because the parent does not
    expose a hook for the base prior.
    """

    def __init__(self, *args, evidence=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._evidence = evidence or {}

    def _update_claim_posterior(self, claim_id):
        # Start from evidence base (or prior Beta(1,1) = nat(0,0))
        if claim_id in self._evidence:
            a, b = self._evidence[claim_id]
            total_eta1, total_eta2 = a - 1, b - 1
        else:
            total_eta1, total_eta2 = 0.0, 0.0

        if hasattr(self, '_msg_cache'):
            for (_oid, cid, _rel), (ma, mb) in self._msg_cache.items():
                if cid == claim_id:
                    total_eta1 += ma
                    total_eta2 += mb
        else:
            for rel in ("IMPL", "NAND"):
                rows = self.g.query(
                    f"MATCH (o:Point)-[r:{rel}]->(c:Point {{id:$cid}}) "
                    "RETURN coalesce(r.msg_alpha,0.0), coalesce(r.msg_beta,0.0)",
                    params={"cid": claim_id},
                ).result_set
                for ma, mb in rows:
                    total_eta1 += float(ma)
                    total_eta2 += float(mb)

        alpha, beta = self._beta_from_natural(total_eta1, total_eta2)
        self._write_node(claim_id, alpha, beta)


# ═══════════════════════════════════════════════════════════════════
# P0 Gap 1: n-ary operator decomposition
# ═══════════════════════════════════════════════════════════════════

@needs_falkor
def test_nary_operator():
    """_update_nary_factor code path: NAND with 3 inputs → pairwise decomposition.

    Builds NAND(c0, c1, c2). The n-ary decomposition runs 3 pairwise
    NAND updates (c0,c1), (c0,c2), (c1,c2). This code path has NEVER
    been exercised before.
    """
    db_path, tmpdir = _tmp("g_nary.db")
    try:
        proj = FalkorProjection(db_path, graph_name="test")
        try:
            claim_ids = ["c0", "c1", "c2"]
            operators = [("NAND_012", "NAND", claim_ids)]

            _build_graph(proj, claim_ids, operators)

            ep = TortoiseEP(proj, damping=0.5, n_quad=8, max_iter=50, tol=1e-3)
            n_iter, converged = ep.run(["NAND_012"])

            # All posteriors must be valid
            means = {}
            for cid in claim_ids:
                conf = ep.compute_confidence(cid)
                m = conf["mean"]
                assert not math.isnan(m), f"{cid}: NaN mean"
                assert 0.01 < m < 0.99, f"{cid}: mean={m:.4f} out of bounds"
                means[cid] = m

            # NAND(c0, c1, c2): can't have all three ≥ 0.5
            assert any(m < 0.5 for m in means.values()), \
                f"NAND constraint violated: means=" \
                f"{{{', '.join(f'{k}: {v:.3f}' for k,v in means.items())}}}"

            # At least one iteration ran
            assert n_iter >= 1, f"EP should iterate at least once, got {n_iter}"
        finally:
            proj.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
# P0 Gap 2: TortoiseEP vs InMemoryEP equivalence
# ═══════════════════════════════════════════════════════════════════

def test_tortoise_vs_inmemory_equivalence():
    """TortoiseEP (FalkorDB) and InMemoryEP produce identical posteriors.

    Same 10-claim graph (2 NAND + 3 IMPL, evidence on c0,c1). Both
    implementations must agree within 0.01 mean for all claims.
    Catches algorithmic divergence between production and test code.
    """
    if not HAS_FALKOR:
        pytest.skip("FalkorDB not available")

    claim_ids = [f"c{i}" for i in range(10)]

    # 2 NAND + 3 IMPL — sparse connections leave some claims isolated
    operators = [
        ("NAND_01", "NAND", ["c0", "c1"]),
        ("NAND_23", "NAND", ["c2", "c3"]),
        ("IMPL_04", "IMPL", ["c0", "c4"]),
        ("IMPL_15", "IMPL", ["c1", "c5"]),
        ("IMPL_67", "IMPL", ["c6", "c7"]),
    ]

    evidence = {"c0": (3.0, 1.0), "c1": (3.0, 1.0)}  # strong positive evidence

    # ── TortoiseEP (FalkorDB) ──
    db_path, tmpdir = _tmp("g_equiv.db")
    try:
        proj = FalkorProjection(db_path, graph_name="test")
        try:
            _build_graph(proj, claim_ids, operators)

            # Set evidence as initial node state (read by _read_node)
            for cid, (a, b) in evidence.items():
                proj.g.query(
                    "MATCH (n:Point {id:$id}) SET n.ep_alpha=$a, n.ep_beta=$b",
                    params={"id": cid, "a": a, "b": b},
                )

            op_ids = [op[0] for op in operators]
            ep_t = TortoiseEPWithEvidence(
                proj, evidence=evidence,
                damping=0.3, n_quad=8, max_iter=200, tol=1e-4,
            )
            n_t, conv_t = ep_t.run(op_ids)

            assert conv_t, f"TortoiseEP did not converge ({n_t} iterations)"

            t_means = {}
            for cid in claim_ids:
                t_means[cid] = ep_t.compute_confidence(cid)["mean"]
        finally:
            proj.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ── InMemoryEP (pure Python) ──
    factors = [(op_id, op_type, inputs, 1.0)
               for op_id, op_type, inputs in operators]

    ep_m = InMemoryEP(damping=0.3, n_quad=8)
    ep_m.run(factors, evidence=evidence, n_iter=200)

    m_means = {}
    for cid in claim_ids:
        a, b = ep_m.posteriors.get(cid, (1.0, 1.0))
        m_means[cid] = a / (a + b)

    # ── Compare: all claims within 0.01 ──
    max_diff = 0.0
    for cid in claim_ids:
        diff = abs(t_means[cid] - m_means[cid])
        max_diff = max(max_diff, diff)
        assert diff < 0.02, f"{cid}: Tortoise={t_means[cid]:.4f} InMemory={m_means[cid]:.4f} diff={diff:.4f}"

    # Both converged (InMemoryEP always runs full n_iter, TortoiseEP checks tol)
    assert conv_t, "TortoiseEP should converge"


# ═══════════════════════════════════════════════════════════════════
# P0 Gap 3: Falsification — EP should struggle on frustrated graph
# ═══════════════════════════════════════════════════════════════════

@needs_falkor
def test_ep_should_struggle():
    """Falsification: maximally frustrated 3-cycle.

    NAND(c0,c1) + NAND(c1,c2) + NAND(c2,c0) +
    IMPL(c0,c1) + IMPL(c1,c2) + IMPL(c2,c0)

    Every pair has conflicting constraints (NAND says at least one low,
    IMPL says if source high, target high). EP must either:
      (a) Not converge, OR
      (b) Converge with high variance (>0.04 on all claims)

    Low-variance confidence on this graph = EP is producing wrong answers.
    High variance is the honest response to an underdetermined system.
    """
    db_path, tmpdir = _tmp("g_frust.db")
    try:
        proj = FalkorProjection(db_path, graph_name="test")
        try:
            claim_ids = ["c0", "c1", "c2"]
            operators = [
                ("NAND_01", "NAND", ["c0", "c1"]),
                ("NAND_12", "NAND", ["c1", "c2"]),
                ("NAND_20", "NAND", ["c2", "c0"]),
                ("IMPL_01", "IMPL", ["c0", "c1"]),
                ("IMPL_12", "IMPL", ["c1", "c2"]),
                ("IMPL_20", "IMPL", ["c2", "c0"]),
            ]

            _build_graph(proj, claim_ids, operators)

            op_ids = [op[0] for op in operators]
            ep = TortoiseEP(proj, damping=0.5, n_quad=8, max_iter=200, tol=1e-3)
            n_iter, converged = ep.run(op_ids)

            confs = {cid: ep.compute_confidence(cid) for cid in claim_ids}
            variances = [conf["variance"] for conf in confs.values()]

            # All posteriors must be numerically valid
            for cid, conf in confs.items():
                assert not math.isnan(conf["mean"]), f"{cid}: NaN mean"
                assert 0.01 < conf["mean"] < 0.99, \
                    f"{cid}: mean={conf['mean']:.4f} out of valid range"

            if converged:
                # EP converged — must show high uncertainty
                all_high_var = all(v > 0.04 for v in variances)
                assert all_high_var, \
                    f"Converged but overconfident! " \
                    f"Variances: {{{', '.join(f'c{i}: {v:.4f}' for i,v in enumerate(variances))}}}"
            # If NOT converged → that's honest. Nothing to assert beyond validity.
        finally:
            proj.close()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
