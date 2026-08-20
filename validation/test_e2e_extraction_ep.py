"""End-to-end test: extraction → FalkorProjection → TortoiseEP → verify posteriors.

Issue #6900 — validates the FULL production pipeline.

Pipeline:
  1. LLMExtractor (MockModel) reads transcript → Points + Operators via EventAPI
  2. FalkorProjection builds FalkorDB graph incrementally
  3. TortoiseEP runs on all extracted operators
  4. Verify posteriors: convergence, validity, NAND/IMPL behavior, no BFS triggered
"""
import os
import shutil
import sys
import tempfile
from collections import defaultdict

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from tortoise.extractor import LLMExtractor, MockModel  # noqa: I001
from tortoise.api import EventAPI
from tortoise.projection import FalkorProjection
from tortoise.ep import TortoiseEP
from tortoise.log import EventLog

# ── helpers ────────────────────────────────────────────────────────────────

TRANSCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), "..", "tests", "gold", "0323_excerpt.txt"
)

MINIMAL_TRANSCRIPT = """\
Alice: Cats are better than dogs because they are independent.
Bob: However, dogs are more loyal than cats.
Alice: That's true, so both have merits.
Bob: But independence means less maintenance, therefore cats win for busy people.
"""


def _has_falkor() -> bool:
    try:
        from redislite.falkordb_client import FalkorDB  # noqa: F401
        return True
    except ImportError:
        return False


def _read_transcript() -> str:
    # ponytail: minimal transcript by default (fast, deterministic).
    # Set TORTOISE_E2E_GOLD=1 to use the full gold transcript.
    if os.environ.get("TORTOISE_E2E_GOLD") and os.path.exists(TRANSCRIPT_PATH):
        with open(TRANSCRIPT_PATH) as f:
            return f.read()
    return MINIMAL_TRANSCRIPT


def _setup_pipeline(transcript: str | None = None):
    """Set up the full pipeline and run extraction. Returns (ep, proj, tmpdir)."""
    tmpdir = tempfile.mkdtemp(prefix="tortoise_e2e_")
    log_path = os.path.join(tmpdir, "events.jsonl")
    db_path = os.path.join(tmpdir, "graph.db")

    log = EventLog(log_path)
    proj = FalkorProjection(db_path, graph_name="tortoise")
    api = EventAPI(log, initiated_by="extractor", agent_id="test", projection=proj)

    extractor = LLMExtractor(
        MockModel("mock-pt"), MockModel("mock-rel"), prompt_version="test"
    )
    text = transcript if transcript is not None else _read_transcript()
    extractor.run(text, "test-transcript", api, max_utterances=10)

    ep = TortoiseEP(proj, max_iter=50, tol=1e-3)
    return ep, proj, tmpdir


def _get_operator_ids(proj: FalkorProjection) -> list[str]:
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.is_operator = true RETURN n.id"
    ).result_set
    return [r[0] for r in rows]


def _get_claim_posteriors(proj: FalkorProjection) -> dict[str, tuple[float, float]]:
    rows = proj.g.query(
        "MATCH (n:Point) "
        "WHERE n.is_operator IS NULL OR n.is_operator = false "
        "RETURN n.id, coalesce(n.ep_alpha, 1.0), coalesce(n.ep_beta, 1.0)"
    ).result_set
    return {r[0]: (float(r[1]), float(r[2])) for r in rows}


def _posterior_mean(alpha: float, beta: float) -> float:
    total = alpha + beta
    return alpha / total if total > 0 else 0.5


def _operator_claim_pairs(proj: FalkorProjection, op_type: str) -> defaultdict[str, list[str]]:
    """Return {operator_id: [claim_id, ...]} for operators of given type."""
    rows = proj.g.query(
        f"MATCH (op:Point {{is_operator: true, op_type: $t}})-[:{op_type}]->(c:Point) "
        "RETURN op.id, c.id ORDER BY op.id, c.id",
        params={"t": op_type},
    ).result_set
    pairs: defaultdict[str, list[str]] = defaultdict(list)
    for op_id, claim_id in rows:
        pairs[op_id].append(claim_id)
    return pairs


# ── tests ──────────────────────────────────────────────────────────────────

@pytest.mark.skipif(not _has_falkor(), reason="redislite FalkorDB not installed")
class TestE2EExtractionEP:
    """Full pipeline: extract → project → EP → verify."""

    def test_e2e_extraction_produces_operators(self):
        """Extract from a known transcript. Assert >= 2 operators (NAND or IMPL)."""
        _, proj, tmpdir = _setup_pipeline()
        try:
            ops = _get_operator_ids(proj)
            assert len(ops) >= 2, f"Expected >= 2 operators, got {len(ops)}"

            op_types = proj.g.query(
                "MATCH (n:Point) WHERE n.is_operator = true RETURN n.op_type"
            ).result_set
            types = {r[0] for r in op_types}
            assert types & {"NAND", "IMPL"}, \
                f"Expected NAND or IMPL operators, got {types}"
        finally:
            proj.close()
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_e2e_ep_runs_on_extracted_graph(self):
        """Run TortoiseEP on extracted operators.

        Assert: EP converges within 50 iterations.
        All claims have valid posteriors (mean in [0.01, 0.99], variance > 0).
        """
        ep, proj, tmpdir = _setup_pipeline()
        try:
            op_ids = _get_operator_ids(proj)
            if not op_ids:
                pytest.skip("No operators extracted")

            iterations, converged = ep.run(op_ids)
            assert converged, \
                f"EP did not converge within {iterations} iterations"
            assert iterations <= 50, \
                f"EP took {iterations} > 50 iterations"

            posteriors = _get_claim_posteriors(proj)
            assert len(posteriors) >= 2, \
                f"Expected >= 2 claims, got {len(posteriors)}"

            for cid, (alpha, beta) in posteriors.items():
                mean = _posterior_mean(alpha, beta)
                total = alpha + beta
                variance = (alpha * beta) / (total * total * (total + 1)) if total > 0 else 0

                assert 0.01 <= mean <= 0.99, \
                    f"Claim {cid}: mean={mean:.4f} not in [0.01, 0.99]"
                assert variance > 0, \
                    f"Claim {cid}: variance must be > 0, got {variance:.6f}"
        finally:
            proj.close()
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_e2e_nand_behavior(self):
        """NAND-connected claims: at least one claim per pair has mean < 0.5."""
        ep, proj, tmpdir = _setup_pipeline()
        try:
            op_ids = _get_operator_ids(proj)
            if not op_ids:
                pytest.skip("No operators extracted")

            ep.run(op_ids)
            posteriors = _get_claim_posteriors(proj)
            nand_pairs = _operator_claim_pairs(proj, "NAND")

            if not nand_pairs:
                pytest.skip("No NAND operators in extracted graph")

            for op_id, claim_ids in nand_pairs.items():  # noqa: B007
                if len(claim_ids) < 2:
                    continue
                for i in range(len(claim_ids)):
                    for j in range(i + 1, len(claim_ids)):
                        mi = _posterior_mean(*posteriors.get(claim_ids[i], (1.0, 1.0)))
                        mj = _posterior_mean(*posteriors.get(claim_ids[j], (1.0, 1.0)))
                        assert mi < 0.5 or mj < 0.5, (
                            f"NAND pair ({claim_ids[i][:8]}:{mi:.3f}, "
                            f"{claim_ids[j][:8]}:{mj:.3f}): both >= 0.5"
                        )
        finally:
            proj.close()
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_e2e_impl_behavior(self):
        """IMPL-connected claims are pulled toward each other.

        Assert: |mean_A - mean_B| < 0.3 for all IMPL pairs.
        """
        ep, proj, tmpdir = _setup_pipeline()
        try:
            op_ids = _get_operator_ids(proj)
            if not op_ids:
                pytest.skip("No operators extracted")

            ep.run(op_ids)
            posteriors = _get_claim_posteriors(proj)
            impl_pairs = _operator_claim_pairs(proj, "IMPL")

            if not impl_pairs:
                pytest.skip("No IMPL operators in extracted graph")

            for op_id, claim_ids in impl_pairs.items():  # noqa: B007
                if len(claim_ids) < 2:
                    continue
                for i in range(len(claim_ids)):
                    for j in range(i + 1, len(claim_ids)):
                        mi = _posterior_mean(*posteriors.get(claim_ids[i], (1.0, 1.0)))
                        mj = _posterior_mean(*posteriors.get(claim_ids[j], (1.0, 1.0)))
                        diff = abs(mi - mj)
                        assert diff < 0.3, (
                            f"IMPL pair ({claim_ids[i][:8]}:{mi:.3f}, "
                            f"{claim_ids[j][:8]}:{mj:.3f}): diff={diff:.3f} >= 0.3"
                        )
        finally:
            proj.close()
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_e2e_no_bfs_triggered(self):
        """propagate_shock is NEVER called — EP replaces BFS completely."""
        ep, proj, tmpdir = _setup_pipeline()
        try:
            op_ids = _get_operator_ids(proj)
            if not op_ids:
                pytest.skip("No operators extracted")

            original_ps = proj.propagate_shock

            def _guard(*args, **kwargs):
                pytest.fail("propagate_shock was called! EP should replace BFS.")

            proj.propagate_shock = _guard
            try:
                iterations, converged = ep.run(op_ids)
                assert converged, \
                    f"EP did not converge ({iterations} iterations)"
            finally:
                proj.propagate_shock = original_ps
        finally:
            proj.close()
            shutil.rmtree(tmpdir, ignore_errors=True)
