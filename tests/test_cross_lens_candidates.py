"""Tests for the cross-lens candidate-exposure surface (#438, Slice 1-3).

Covers the SDK method ``get_cross_lens_candidates`` and the MCP tool
``tortoise_find_cross_lens_candidates`` (bring-your-own-agent surface):

  - candidate payload shape (lens pair, similarity, point context, routing)
  - read-only guarantee (no graph writes)
  - #901 routing field semantics (truth|relevance) + neutral payload (no op_type)
  - D4 cost cap (<= 200 candidates/cycle, hard clamp)
  - D3 tier gate (registered sourceKind of any tier; unregistered excluded)
  - dedup vs existing operators
  - D8 empty-results-not-errors
  - MCP tool registration + delegation

Deterministic via an injected fake embedding model (patched onto
EmbeddingModel.get) so ``create_point`` stores reproducible embeddings; the
discovery path (vector pull + exact cosine recompute) runs against the real
embedded FalkorDBLite store.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.embeddings import EmbeddingModel  # noqa: E402
from tortoise.sdk import TortoiseSDK  # noqa: E402
from tortoise.source_credibility import SOURCE_KIND_DEFAULTS  # noqa: E402


# ── deterministic embedding model ────────────────────────────────────
# Fixed vectors (same convention as tests/test_cross_lens.py): contents
# sharing a near-vector are "similar"; orthogonal vectors are noise.
_V = {
    "alpha one": np.array([1.0, 0.0, 0.0]),
    "beta two": np.array([0.9, 0.1, 0.0]),
    "gamma three": np.array([0.0, 1.0, 0.0]),
    "delta four": np.array([0.0, 0.0, 1.0]),
    "theta five": np.array([0.0, 0.9, 0.1]),
}


class _FakeEmbedder:
    """Deterministic stand-in for the active embedding model (384-dim)."""

    def encode(self, texts, batch_size=32, show_progress_bar=False):
        return np.stack([_V.get(t, np.array([0.0, 0.1, 0.2])) for t in texts])


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    """Fresh embedded SDK with a deterministic embedding model."""
    monkeypatch.setattr(EmbeddingModel, "get", lambda: _FakeEmbedder())
    sdk = TortoiseSDK(db_path=str(tmp_path / "candidates.db"))
    yield sdk
    sdk.close()


def _seed_two_streams(sdk, n: int = 1) -> list[str]:
    """Seed two separately-ingested streams (sources s1/s2) with similar
    points. Returns the created point ids (stream A then stream B)."""
    sdk.create_source(url="https://s1", sourceKind="document")
    sdk.create_source(url="https://s2", sourceKind="T2")
    ids = []
    for _ in range(n):
        ids.append(sdk.create_point(kind="statement", content="alpha one",
                                    extractedFrom="https://s1")["id"])
        ids.append(sdk.create_point(kind="statement", content="beta two",
                                    extractedFrom="https://s2")["id"])
    return ids


def _node_count(sdk) -> int:
    return sdk._get_proj().g.query("MATCH (n) RETURN count(n)").result_set[0][0]


# ── 1. candidate shape + cross-lens semantics ─────────────────────────

def test_candidate_shape_and_cross_lens(sdk):
    _seed_two_streams(sdk)
    # same-lens noise pair (both from s1, orthogonal to everything)
    sdk.create_point(kind="statement", content="gamma three",
                     extractedFrom="https://s1")
    res = sdk.get_cross_lens_candidates()
    assert res["count"] == 1
    assert res["cap"] == 200
    assert res["truncated"] is False
    assert res["routing"] == "truth"
    c = res["candidates"][0]
    # payload keys (Slice 1 + 2)
    assert set(c) == {
        "src", "dst", "similarity", "lenses", "sourceKinds",
        "src_content", "dst_content", "src_source", "dst_source", "routing",
    }
    # lens pair: alpha (s1) <-> beta (s2) — DIFFERENT sources
    assert c["lenses"][0] != c["lenses"][1]
    assert c["lenses"] == ["https://s1", "https://s2"]
    assert c["sourceKinds"] == ["document", "T2"]
    assert c["similarity"] >= 0.9  # measured 0.994
    assert c["src_content"] == "alpha one"
    assert c["dst_content"] == "beta two"
    # neutral contract: no op_type / suggested_relation hint (D2)
    assert "op_type" not in c
    assert "suggested_relation" not in c
    assert c["routing"] == "truth"


def test_sorted_by_similarity_desc(sdk):
    _seed_two_streams(sdk)
    sdk.create_point(kind="statement", content="theta five",
                     extractedFrom="https://s1")
    sdk.create_point(kind="statement", content="delta four",
                     extractedFrom="https://s2")
    res = sdk.get_cross_lens_candidates(threshold=0.3)
    sims = [c["similarity"] for c in res["candidates"]]
    assert sims == sorted(sims, reverse=True)


# ── 2. read-only guarantee ────────────────────────────────────────────

def test_read_only_no_writes(sdk):
    _seed_two_streams(sdk, n=2)
    n_before = _node_count(sdk)
    res = sdk.get_cross_lens_candidates()
    assert res["count"] >= 1
    assert _node_count(sdk) == n_before, "candidate discovery must not write"


# ── 3. routing field (#901, D2) ───────────────────────────────────────

def test_routing_field_values(sdk):
    _seed_two_streams(sdk)
    res = sdk.get_cross_lens_candidates(routing="relevance")
    assert res["routing"] == "relevance"
    assert all(c["routing"] == "relevance" for c in res["candidates"])
    res2 = sdk.get_cross_lens_candidates(routing="truth")
    assert res2["routing"] == "truth"


def test_routing_invalid_raises(sdk):
    _seed_two_streams(sdk)
    with pytest.raises(ValueError, match="routing"):
        sdk.get_cross_lens_candidates(routing="evidence")


def test_threshold_filters(sdk):
    _seed_two_streams(sdk)
    # alpha-beta cosine 0.994 passes 0.99; fails 0.999
    assert sdk.get_cross_lens_candidates(threshold=0.99)["count"] == 1
    assert sdk.get_cross_lens_candidates(threshold=0.999)["count"] == 0


# ── 4. cost cap (D4: <= 200/cycle, hard clamp) ────────────────────────

def test_cost_cap_hard_clamp(sdk):
    # 30 pairs of similar cross-source points -> 30*30 = 900 candidate
    # pairs before capping; requesting more than the cap must still clamp.
    _seed_two_streams(sdk, n=30)
    res = sdk.get_cross_lens_candidates(max_candidates=10_000)
    assert res["cap"] == 200
    assert res["count"] <= 200
    assert len(res["candidates"]) <= 200
    # lower requests are honored (not raised to the cap)
    res2 = sdk.get_cross_lens_candidates(max_candidates=5)
    assert res2["count"] <= 5


def test_cost_cap_truncated_flag(sdk):
    _seed_two_streams(sdk, n=30)
    # top_k=100 makes the whole 60-point pool reachable per point (the
    # default 20 is the cost-bound recall; same-source near-duplicates — the
    # D5-excluded territory — can crowd a small top-k), so the >200 pair
    # space is visible and the hard cap actually truncates.
    res = sdk.get_cross_lens_candidates(max_candidates=10_000, top_k=100)
    assert res["count"] == 200
    assert res["truncated"] is True
    res_small = sdk.get_cross_lens_candidates(max_candidates=10_000, threshold=0.999)
    assert res_small["count"] == 0
    assert res_small["truncated"] is False


def test_top_k_hard_clamp(sdk, monkeypatch):
    # conf 75: top_k is hard-clamped to CROSS_LENS_ANN_TOP_K_MAX (100) so an
    # agent cannot inflate the per-cycle recall budget — same D4 hard-cap
    # philosophy as max_candidates.
    _seed_two_streams(sdk, n=30)
    # 10_000 would (unclamped) pull the whole index per point; clamped to
    # 100 it still makes the full 60-point pool reachable — same result as
    # top_k=100, proving the clamp is applied and silent (no error).
    res = sdk.get_cross_lens_candidates(max_candidates=10_000, top_k=10_000)
    assert res["count"] == 200
    assert res["truncated"] is True
    res2 = sdk.get_cross_lens_candidates(max_candidates=10_000, top_k=100)
    assert res2["count"] == res["count"]
    # values at/below the clamp edge are honored, not lowered: top_k=1 must
    # shrink the result vs the top_k=100 run (low values flow through
    # end-to-end — the clamp is a cap, not a floor).
    res3 = sdk.get_cross_lens_candidates(max_candidates=10_000, top_k=1)
    assert res3["count"] < res["count"]
    # the limit actually handed to the vector index is the clamped top_k
    # (+1 for self-exclusion), never the raw agent-supplied value.
    from tortoise import search_engine
    from tortoise.sdk import CROSS_LENS_ANN_TOP_K_MAX

    limits: list[int] = []
    real = search_engine.run_vector_query

    def spy(g, query_vec, limit, is_embedded=True, **kwargs):
        limits.append(limit)
        return real(g, query_vec, limit=limit, is_embedded=is_embedded, **kwargs)

    monkeypatch.setattr(search_engine, "run_vector_query", spy)
    sdk.get_cross_lens_candidates(max_candidates=10_000, top_k=10_000)
    assert limits, "run_vector_query was never called"
    assert max(limits) <= CROSS_LENS_ANN_TOP_K_MAX + 1
    # lower bound still validated
    with pytest.raises(ValueError, match="top_k"):
        sdk.get_cross_lens_candidates(top_k=0)


# ── 5. tier gate (D3: registered sourceKind, any tier) ─────────────────

def test_registered_sourcekind_gate(sdk):
    sdk.create_source(url="https://s1", sourceKind="document")  # registered (neutral)
    sdk.create_source(url="https://s2", sourceKind="T2")        # registered tier form
    sdk.create_source(url="https://s3", sourceKind="custom_kind_xyz")  # NOT registered
    pa = sdk.create_point(kind="statement", content="alpha one",
                          extractedFrom="https://s1")["id"]
    pb = sdk.create_point(kind="statement", content="beta two",
                          extractedFrom="https://s2")["id"]
    pc = sdk.create_point(kind="statement", content="alpha one",
                          extractedFrom="https://s3")["id"]
    pd = sdk.create_point(kind="statement", content="beta two")  # no source
    res = sdk.get_cross_lens_candidates()
    assert res["count"] == 1  # only the registered-kind pair
    for c in res["candidates"]:
        assert c["src"] not in (pc, pd) and c["dst"] not in (pc, pd)
    assert pa in {c["src"], c["dst"]}
    assert pb in {c["src"], c["dst"]}


def test_registered_kinds_are_registry_keys(sdk):
    # any-tier semantics: T0-T4 identity kinds are registered by default
    for tier in ("T0", "T1", "T2", "T3", "T4"):
        assert tier in SOURCE_KIND_DEFAULTS
    assert "custom_kind_xyz" not in SOURCE_KIND_DEFAULTS


# ── 6. dedup vs existing operators (Slice 1) ──────────────────────────

def test_dedup_vs_existing_operators(sdk):
    ids = _seed_two_streams(sdk)
    pa, pb = ids[0], ids[1]
    assert sdk.get_cross_lens_candidates()["count"] == 1
    sdk.create_operator("IMPL", source_id=pa, target_ids=[pb])
    res = sdk.get_cross_lens_candidates()
    assert res["count"] == 0, "operator-connected pair must be deduped"


# ── 7. D8: empty results, not errors ──────────────────────────────────


def test_two_cycle_cross_stream_e2e(sdk):
    """Cross-stream discovery E2E (scoping test plan): separately ingested
    streams produce candidates, with path-provenance on the payload."""
    # Cycle 1: only stream A ingested — nothing cross-lens to see.
    sdk.create_source(url="https://stream-a", sourceKind="document")
    pa = sdk.create_point(kind="statement", content="alpha one",
                          extractedFrom="https://stream-a")["id"]
    res1 = sdk.get_cross_lens_candidates()
    assert res1["count"] == 0
    # Cycle 2: stream B ingested separately — cross-stream candidates appear.
    sdk.create_source(url="https://stream-b", sourceKind="T1")
    pb = sdk.create_point(kind="statement", content="beta two",
                          extractedFrom="https://stream-b")["id"]
    res2 = sdk.get_cross_lens_candidates()
    assert res2["count"] == 1
    c = res2["candidates"][0]
    assert {c["src"], c["dst"]} == {pa, pb}
    # path-provenance: lens pair = the two source urls, kinds = the two tiers
    assert set(c["lenses"]) == {"https://stream-a", "https://stream-b"}
    assert c["sourceKinds"] == ["document", "T1"]
    assert c["src_source"] != c["dst_source"]
    assert c["similarity"] >= 0.9


def test_empty_results_not_error(sdk):
    res = sdk.get_cross_lens_candidates()  # empty graph
    assert res == {"candidates": [], "count": 0, "cap": 200,
                   "truncated": False, "routing": "truth"}


def test_single_stream_no_cross_lens(sdk):
    sdk.create_source(url="https://s1", sourceKind="document")
    sdk.create_point(kind="statement", content="alpha one",
                     extractedFrom="https://s1")
    sdk.create_point(kind="statement", content="beta two",
                     extractedFrom="https://s1")
    res = sdk.get_cross_lens_candidates()
    assert res["count"] == 0, "same-source pairs are not cross-lens"


# ── 8. MCP surface ────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _transport_context():
    """MCP tools require an initialized transport mode (#236 auth gate)."""
    from tortoise.mcp_auth import _transport_mode
    _transport_mode.set("stdio")
    yield
    _transport_mode.set(None)


def test_mcp_tool_registered_readonly():
    from tortoise.tool_registry import TOOL_REGISTRY, GROUP_BY_NAME
    td = next(t for t in TOOL_REGISTRY
              if t.name == "tortoise_find_cross_lens_candidates")
    assert td.sdk_method == "get_cross_lens_candidates"
    assert td.annotations.readOnlyHint is True
    assert td.annotations.destructiveHint is False
    assert td.http_policy is True
    assert GROUP_BY_NAME[td.name] == "reasoning"


def test_mcp_handler_delegates(sdk, monkeypatch):
    from tortoise import mcp_server
    captured = {}

    def fake_sdk_method(**kw):
        captured.update(kw)
        return {"candidates": [], "count": 0, "cap": 200,
                "truncated": False, "routing": kw.get("routing")}

    monkeypatch.setattr(sdk, "get_cross_lens_candidates", fake_sdk_method)
    monkeypatch.setattr(mcp_server, "_get_team_sdk", lambda: sdk)
    out = mcp_server.tortoise_find_cross_lens_candidates(
        threshold=0.5, max_candidates=50, routing="relevance", top_k=7)
    assert captured == {"threshold": 0.5, "max_candidates": 50,
                        "routing": "relevance", "top_k": 7}
    assert out["routing"] == "relevance"


def test_mcp_tool_integration(sdk, monkeypatch):
    """MCP tool end-to-end against the real embedded SDK (read-only)."""
    from tortoise import mcp_server
    _seed_two_streams(sdk)
    monkeypatch.setattr(mcp_server, "_get_team_sdk", lambda: sdk)
    n_before = _node_count(sdk)
    out = mcp_server.tortoise_find_cross_lens_candidates()
    assert out["count"] == 1
    c = out["candidates"][0]
    assert c["lenses"][0] != c["lenses"][1]
    assert c["routing"] == "truth"
    assert _node_count(sdk) == n_before, "MCP tool must be read-only"


def test_mcp_tool_error_surfaces(sdk, monkeypatch):
    from tortoise import mcp_server
    monkeypatch.setattr(sdk, "get_cross_lens_candidates",
                        lambda **kw: (_ for _ in ()).throw(ValueError("boom")))
    monkeypatch.setattr(mcp_server, "_get_team_sdk", lambda: sdk)
    out = mcp_server.tortoise_find_cross_lens_candidates()
    assert isinstance(out, dict) and "error" in out and "boom" in out["error"]
