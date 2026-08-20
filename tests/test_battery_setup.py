"""Task 5 tests — RoundTripCounter + batcher + naive baseline + equivalence
(S1: ≤2 DB round-trips/scenario at the query boundary; batch==naive graph
state; negative-path validation parity; idempotency; scale)."""
from __future__ import annotations

import os
import tempfile  # noqa: F401
from pathlib import Path

import pytest

from battery.config import load_corpus
from battery.runner.setup import (
    RoundTripCounter,
    batch_setup,
    derive_scenario_graph,
    graph_state_equivalence,
    naive_setup,
    scenario_entity_id,
)
from tests._embedded import skip_if_no_falkor

CONFIG = Path(__file__).parent.parent / "battery" / "config"
GOLDS = CONFIG.parent / "golds"

FIXED_EMBEDDING = [0.1, 0.2, 0.3]


def _embedding_fn(content: str) -> list[float] | None:
    """Fixed deterministic embedding stub (monkeypatch seam — no model)."""
    return FIXED_EMBEDDING if content else None


def _scenarios():
    return load_corpus(CONFIG / "corpus.yaml", gold_base=GOLDS)


@pytest.fixture()
def fresh_db(tmp_path):
    from tortoise.sdk import TortoiseSDK
    if skip_if_no_falkor():
        pytest.skip("embedded FalkorDBLite unavailable")
    os.environ.setdefault("TORTOISE_DB_URI", "")
    sdk = TortoiseSDK(str(tmp_path / "battery.db"))
    yield sdk
    sdk.close()


def _normalize_operators(state):
    """Key operators by (op_type, direction, inputs) — ids normalized
    (operator ULIDs are time-based on the naive path)."""
    return {
        (v["op_type"], v["direction"], tuple(i["id"] for i in v["inputs"]))
        for v in state["operators"].values()
    }


class TestRoundTripCounter:
    def test_counts_every_query(self, fresh_db):
        proj = fresh_db._get_proj()
        counter = RoundTripCounter(proj.g)
        proj.g = counter
        counter.query("RETURN 1")
        counter.query("RETURN 2")
        assert counter.count == 2


class TestBatchSetup:
    def test_two_round_trips_per_scenario(self, fresh_db):
        proj = fresh_db._get_proj()
        counter = RoundTripCounter(proj.g)
        proj.g = counter
        scenarios = _scenarios()
        rounds = batch_setup(proj, scenarios, embedding_fn=_embedding_fn,
                             counter=counter)
        for sid, n in rounds.items():
            assert n <= 2, f"{sid} took {n} round trips"
        # ≤2 per scenario (operator-less scenarios legitimately take 1 —
        # the #1407 production corpus has 21 operator-bearing of 134).
        assert counter.count <= 2 * len(scenarios)

    def test_batch_is_idempotent(self, fresh_db):
        proj = fresh_db._get_proj()
        counter = RoundTripCounter(proj.g)
        proj.g = counter
        scenarios = _scenarios()
        batch_setup(proj, scenarios, embedding_fn=_embedding_fn, counter=counter)
        before = graph_state_equivalence(proj, scenarios)
        batch_setup(proj, scenarios, embedding_fn=_embedding_fn, counter=counter)
        after = graph_state_equivalence(proj, scenarios)
        # no duplicate nodes AND no duplicate edges (MERGE edges keep counts)
        assert len(before["points"]) == len(after["points"])
        assert (sum(len(v["inputs"]) for v in before["operators"].values())
                == sum(len(v["inputs"]) for v in after["operators"].values()))

    def test_negative_path_missing_endpoint(self, fresh_db):
        proj = fresh_db._get_proj()  # noqa: F841
        scenarios = _scenarios()
        graph = derive_scenario_graph(scenarios[0])
        # corrupt: add an operator referencing a nonexistent point
        graph.operators.append({
            "id": "nand-corrupt", "op_type": "NAND", "direction": "unidirectional",
            "inputs": [{"id": "missing-point", "idx": 0}], "source_id": "missing-point",
        })
        from battery.runner.setup import _validate_operator_endpoints
        with pytest.raises(ValueError):
            _validate_operator_endpoints(graph)


class TestEquivalence:
    def test_batch_equals_naive(self, fresh_db, tmp_path, monkeypatch):
        # Embedding parity: fixed deterministic stub applied to BOTH paths
        # (monkeypatches tortoise.embeddings.compute_embedding — the SDK
        # imports it in-function, so the seam is live on the naive path).
        import tortoise.embeddings as emb_mod
        monkeypatch.setattr(emb_mod, "compute_embedding", _embedding_fn)
        scenarios = _scenarios()
        # ── batch on fresh DB ──
        sdk_b = fresh_db
        proj_b = sdk_b._get_proj()
        counter_b = RoundTripCounter(proj_b.g)
        proj_b.g = counter_b
        batch_setup(proj_b, scenarios, counter=counter_b)
        state_b = graph_state_equivalence(proj_b, scenarios)
        batch_rounds = counter_b.count - 2  # minus the 2 state-read queries

        # ── naive on a SEPARATE fresh DB ──
        from tortoise.sdk import TortoiseSDK
        sdk_n = TortoiseSDK(str(tmp_path / "naive.db"))
        try:
            proj_n = sdk_n._get_proj()
            counter_n = RoundTripCounter(proj_n.g)
            proj_n.g = counter_n
            for s in scenarios:
                naive_setup(sdk_n, s)
            state_n = graph_state_equivalence(proj_n, scenarios)
            naive_rounds = counter_n.count - 2
        finally:
            sdk_n.close()

        # same point ids (deterministic id scheme) + same props
        assert set(state_b["points"]) == set(state_n["points"])
        for pid in state_b["points"]:
            b, n = state_b["points"][pid], state_n["points"][pid]
            assert b["content_hash"] == n["content_hash"]
            assert b["pointKind"] == n["pointKind"]
            assert b["status"] == n["status"]  # incl. promote_source → live
            assert b["has_embedding"] == n["has_embedding"]  # embedding parity
        # operators normalized by (type, direction, inputs)
        assert _normalize_operators(state_b) == _normalize_operators(state_n)

        # N+1 proof: batch ≪ naive (2·N vs 4+ per item)
        n_items = sum(len(derive_scenario_graph(s).points) for s in scenarios)
        assert batch_rounds <= 2 * len(scenarios)
        assert naive_rounds > 4 * n_items

    def test_promote_source_live(self, fresh_db):
        proj = fresh_db._get_proj()
        scenarios = _scenarios()
        batch_setup(proj, scenarios, embedding_fn=_embedding_fn)
        state = graph_state_equivalence(proj, scenarios)
        statuses = {v["status"] for v in state["points"].values()}
        assert "live" in statuses  # contradiction sources promoted

    def test_nand_unidirectional_both_paths(self, fresh_db, tmp_path):
        from tortoise.sdk import TortoiseSDK
        scenarios = _scenarios()
        proj = fresh_db._get_proj()
        batch_setup(proj, scenarios, embedding_fn=_embedding_fn)
        state_b = graph_state_equivalence(proj, scenarios)
        sdk_n = TortoiseSDK(str(tmp_path / "n.db"))
        try:
            for s in scenarios:
                naive_setup(sdk_n, s)
            state_n = graph_state_equivalence(sdk_n._get_proj(), scenarios)
        finally:
            sdk_n.close()
        assert all(v["direction"] == "unidirectional"
                   for v in state_b["operators"].values())
        assert _normalize_operators(state_b) == _normalize_operators(state_n)


class TestScale:
    @pytest.mark.slow
    def test_50_scenarios_batch_2n_vs_naive(self, tmp_path):
        """Structural proof: batch = 2·N total; naive ≫ 2·N at ≥50 scenarios."""
        from tortoise.sdk import TortoiseSDK
        scenarios = _scenarios()
        big = scenarios * 25  # 50 scenarios (~500 items)
        assert len(big) >= 50
        sdk_b = TortoiseSDK(str(tmp_path / "scale_batch.db"))
        try:
            proj = sdk_b._get_proj()
            counter = RoundTripCounter(proj.g)
            proj.g = counter
            batch_setup(proj, big, embedding_fn=_embedding_fn, counter=counter)
            assert counter.count <= 2 * len(big) + 2
        finally:
            sdk_b.close()
        sdk_n = TortoiseSDK(str(tmp_path / "scale_naive.db"))
        try:
            counter_n = RoundTripCounter(sdk_n._get_proj().g)
            sdk_n._get_proj().g = counter_n
            for s in big[:5]:  # naive is ~10-20x more per item; 5 suffice
                naive_setup(sdk_n, s)
            assert counter_n.count > 2 * len(big[:5]) * 3
        finally:
            sdk_n.close()


def test_scenario_entity_id_deterministic():
    a = scenario_entity_id("statement", "same content")
    b = scenario_entity_id("statement", "same content")
    c = scenario_entity_id("statement", "other")
    assert a == b and a != c


@pytest.mark.slow
class TestRealEmbeddingPath:
    """Scope DD5 pin: a tagged test covers the REAL compute_embedding path
    (both paths produce embeddings — equality-or-both-None). Skips when no
    embedding model is installed (compute_embedding returns None)."""

    def test_real_embedding_present_on_both_paths(self, fresh_db, tmp_path):
        import tortoise.embeddings as emb_mod
        from tortoise.sdk import TortoiseSDK
        real = emb_mod.compute_embedding("probe content for embedding")
        if real is None:
            pytest.skip("no embedding model installed — both paths are None")
        scenarios = _scenarios()
        proj = fresh_db._get_proj()
        batch_setup(proj, scenarios)  # call-time import → REAL embedding fn
        state_b = graph_state_equivalence(proj, scenarios)
        sdk_n = TortoiseSDK(str(tmp_path / "real_naive.db"))
        try:
            for s in scenarios:
                naive_setup(sdk_n, s)
            state_n = graph_state_equivalence(sdk_n._get_proj(), scenarios)
        finally:
            sdk_n.close()
        # embeddings present (and non-None) on both paths
        assert all(v["has_embedding"] for v in state_b["points"].values())
        assert all(v["has_embedding"] for v in state_n["points"].values())
