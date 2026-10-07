"""#4028 — the search read surface's OPT-IN vector-leg relevance floor.

Defect (measured on a copy of the real local store): 17 test-residue Points
carried the only stored embeddings, so the default ``tortoise_search`` surface
answered **any** query with vector-leg hits — ``quantum flux capacitor
calibration`` returned 10 hits, all ``match_source="vector"``, and a freshly
captured turn was absent at ``limit=10`` (rank 18/18 at ``limit=60``).

The fix has two parts:
  (1) DATA — ``tools/purge_test_residue.py`` removes the residue (the issue's
      own ask #2). This is what makes the captured turn surface at the default
      weights.
  (2) an OPT-IN ranking lever — ``TORTOISE_VECTOR_MIN_SIMILARITY`` drops
      sub-relevance near neighbours. It is **off by default** because the
      query->document relevant and unrelated bands OVERLAP for bge-small: a
      real relevant pair scores 0.536, below the 0.580 unrelated ceiling, so a
      default-on floor empties real reads (it fails
      ``tests/test_longmem_runner.py::test_vector_strategy_verified_in_eval_path``).
      ``test_real_relevant_pair_survives_by_default`` is the guard for that.

Hermetic: the query embedder is stubbed, the store is a per-test embedded file.
No provider keys, no network.
"""
from __future__ import annotations

import numpy as np
import pytest

from tortoise import search_engine as se
from tortoise.embeddings import VECTOR_RELEVANCE_FLOOR
from tortoise.search_engine import BELOW_RELEVANCE_FLOOR, run_vector_query

DIM = 384
#: query direction
_Q = np.zeros(DIM)
_Q[0] = 1.0
#: noise direction at cosine 0.5 to _Q (sub-floor)
_NOISE = np.zeros(DIM)
_NOISE[0] = 0.5
_NOISE[1] = 0.75 ** 0.5
#: the real-relevant-pair direction measured on LongMemEval at cosine 0.536
_GOLD = np.zeros(DIM)
_GOLD[0] = 0.536
_GOLD[1] = (1.0 - 0.536 ** 2) ** 0.5


class _FakeModel:
    """Stub embedder returning a fixed query vector."""

    def __init__(self, vec):
        self.vec = np.asarray(vec, dtype=float)

    def encode(self, texts, batch_size=32, show_progress_bar=False):
        return np.asarray([self.vec for _ in texts], dtype=float)


@pytest.fixture(autouse=True)
def _reset_breakers():
    se.reset_circuit_breakers()
    yield
    se.reset_circuit_breakers()


@pytest.fixture
def stub_embedder(monkeypatch):
    from tortoise.embeddings import EmbeddingModel

    monkeypatch.setattr(EmbeddingModel, "get",
                        classmethod(lambda cls, load_timeout=None: _FakeModel(_Q)))


def _set_embedding(graph, pid, vec):
    # ``create_point`` embeds on its own; ``SET n.embedding`` on an existing
    # vectorf32 property is a silent no-op on falkordblite, so REMOVE first.
    graph.query("MATCH (n:Point {id:$id}) REMOVE n.embedding", params={"id": pid})
    graph.query("MATCH (n:Point {id:$id}) SET n.embedding = vecf32($v)",
                params={"id": pid, "v": list(vec)})


def _build_noise_store(sdk, *, noise: int = 12,
                       gold_content: str = "embedder absent on runners"):
    """Noise Points embedded at cosine 0.5 to the query, plus an FTS-only turn.

    The gold turn models a keyless capture: it has NO embedding (the vector
    leg cannot see it), only a lexical hit.
    """
    graph = sdk._get_proj().g
    noise_ids = []
    for i in range(noise):
        p = sdk.create_point("statement", f"noise alpha bravo charlie {i}")
        noise_ids.append(p["id"])
    gold = sdk.create_point("statement", gold_content)
    graph.query("MATCH (n:Point) REMOVE n.embedding")
    for pid in noise_ids:
        _set_embedding(graph, pid, _NOISE)
    return gold["id"]


def _sdk(tmp_path):
    from tortoise.sdk import TortoiseSDK

    return TortoiseSDK(str(tmp_path / "4028.db"))


# ── the DEFAULT must stay floor-free (the measured regression guard) ────────

@pytest.mark.embedded_only  # brute-force vector lane; skips on a URI lane
def test_default_has_no_floor(tmp_path, stub_embedder):
    """Default: the vector leg is byte-identical to pre-#4028 (sub-floor kept)."""
    sdk = _sdk(tmp_path)
    try:
        graph = sdk._get_proj().g
        p = sdk.create_point("statement", "noise alpha bravo charlie")
        _set_embedding(graph, p["id"], _NOISE)
        hits = sdk.tortoise_fts_query("quantum flux capacitor calibration",
                                      entity_type="point", limit=10)
        assert [h["id"] for h in hits] == [p["id"]]
        assert hits[0]["match_source"] == "vector"
    finally:
        sdk.close()


@pytest.mark.embedded_only
def test_real_relevant_pair_survives_by_default(tmp_path, stub_embedder):
    """The LongMemEval 0.536 relevant pair must NOT be dropped at the default.

    This is the guard against re-defaulting the floor on: a real relevant
    query->document pair scores below the unrelated ceiling, so a default-on
    floor empties real reads (tests/test_longmem_runner.py).
    """
    sdk = _sdk(tmp_path)
    try:
        graph = sdk._get_proj().g
        gold = sdk.create_point("statement", "Elixir side projects")
        _set_embedding(graph, gold["id"], _GOLD)
        hits = sdk.tortoise_fts_query("which language for coding",
                                      entity_type="point", limit=10)
        assert gold["id"] in [h["id"] for h in hits], (
            "a real relevant semantic hit at cosine 0.536 must survive at the "
            "DEFAULT weights"
        )
    finally:
        sdk.close()


# ── the OPT-IN lever ───────────────────────────────────────────────────────

@pytest.mark.embedded_only
def test_floor_enabled_unrelated_query_returns_empty(tmp_path, stub_embedder,
                                                     monkeypatch):
    """No lexical support and no relevant neighbour → EMPTY, not 10 hits."""
    monkeypatch.setenv("TORTOISE_VECTOR_MIN_SIMILARITY",
                       str(VECTOR_RELEVANCE_FLOOR))
    sdk = _sdk(tmp_path)
    try:
        _build_noise_store(sdk)
        hits = sdk.tortoise_fts_query("quantum flux capacitor calibration",
                                      entity_type="point", limit=10)
        assert hits == [], (
            "with the floor enabled an unrelated query must not be answered "
            f"by sub-relevance vector hits; got {[h.get('id') for h in hits]}"
        )
    finally:
        sdk.close()


@pytest.mark.embedded_only
def test_floor_enabled_relevant_captured_turn_within_limit_10(
        tmp_path, stub_embedder, monkeypatch):
    """With the floor on, the captured turn is returned within limit=10."""
    monkeypatch.setenv("TORTOISE_VECTOR_MIN_SIMILARITY",
                       str(VECTOR_RELEVANCE_FLOOR))
    sdk = _sdk(tmp_path)
    try:
        gold_id = _build_noise_store(sdk)
        hits = sdk.tortoise_fts_query("embedder absent on runners",
                                      entity_type="point", limit=10)
        ids = [h["id"] for h in hits]
        assert gold_id in ids, (
            f"the captured turn must be in the top-10; got {ids}"
        )
    finally:
        sdk.close()


@pytest.mark.embedded_only
def test_floor_enabled_leg_trace_declares_the_floor(tmp_path, stub_embedder,
                                                    monkeypatch):
    """The trace says WHY the vector leg is empty (not `empty_results`)."""
    monkeypatch.setenv("TORTOISE_VECTOR_MIN_SIMILARITY",
                       str(VECTOR_RELEVANCE_FLOOR))
    sdk = _sdk(tmp_path)
    try:
        _build_noise_store(sdk)
        trace: list = []
        hits = sdk.tortoise_fts_query("quantum flux capacitor calibration",
                                      entity_type="point", limit=10,
                                      leg_trace=trace)
        assert hits == []
        vector = [e for e in trace if e["leg"] == "vector"]
        assert vector and vector[0]["reason"] == BELOW_RELEVANCE_FLOOR
        assert vector[0]["ran"] is True and vector[0]["degraded"] is False
        # the surface must NOT answer a floor-empty read from the TF-IDF
        # fallback (which returns hits for any query)
        assert not any(e["leg"] == "fallback" and e["ran"] for e in trace)
    finally:
        sdk.close()


@pytest.mark.embedded_only
def test_floor_env_zero_is_off(tmp_path, stub_embedder, monkeypatch):
    monkeypatch.setenv("TORTOISE_VECTOR_MIN_SIMILARITY", "0")
    sdk = _sdk(tmp_path)
    try:
        graph = sdk._get_proj().g
        p = sdk.create_point("statement", "noise alpha bravo charlie")
        _set_embedding(graph, p["id"], _NOISE)
        hits = sdk.tortoise_fts_query("quantum flux capacitor calibration",
                                      entity_type="point", limit=10)
        assert [h["id"] for h in hits] == [p["id"]]
    finally:
        sdk.close()


# ── the floor itself (unit) ─────────────────────────────────────────────────

class _Res:
    def __init__(self, rows):
        self.result_set = rows


class _Graph:
    """Minimal fake returning the brute-force (id, 1/(1+distance)) rows."""

    def __init__(self, rows):
        self._rows = list(rows)

    def query(self, cypher, params=None, timeout=None):
        return _Res(list(self._rows))


def test_run_vector_query_drops_sub_floor_hits():
    """0.52 == cosine 0.575 (below 0.60) is dropped; 0.80 (cosine 0.968) kept."""
    rows = [("noise", 0.52), ("good", 0.80)]
    out = run_vector_query(_Graph(rows), _Q.tolist(), is_embedded=True,
                           min_similarity=0.60)
    assert out == [("good", 0.80)]


def test_run_vector_query_default_has_no_floor():
    """The leg is byte-identical for callers that pass no floor."""
    rows = [("noise", 0.52), ("good", 0.80)]
    out = run_vector_query(_Graph(rows), _Q.tolist(), is_embedded=True)
    assert out == [("noise", 0.52), ("good", 0.80)]


def test_run_vector_query_all_below_floor_records_reason():
    trace: list = []
    out = run_vector_query(_Graph([("noise", 0.52)]), _Q.tolist(),
                           is_embedded=True, min_similarity=0.60,
                           leg_trace=trace)
    assert out == []
    assert [e["reason"] for e in trace] == [BELOW_RELEVANCE_FLOOR]


# ── the lever's resolution ──────────────────────────────────────────────────

def test_resolve_min_similarity_default_off_env_and_garbage(monkeypatch):
    from tortoise.sdk import _resolve_vector_min_similarity

    monkeypatch.delenv("TORTOISE_VECTOR_MIN_SIMILARITY", raising=False)
    assert _resolve_vector_min_similarity() is None      # DEFAULT: off
    monkeypatch.setenv("TORTOISE_VECTOR_MIN_SIMILARITY", "0.42")
    assert _resolve_vector_min_similarity() == 0.42
    # a typo, an out-of-range value, and 0 must all be OFF — never a silent
    # floor that empties real reads.
    for bad in ("not-a-number", "1.5", "0", "-0.2", ""):
        monkeypatch.setenv("TORTOISE_VECTOR_MIN_SIMILARITY", bad)
        assert _resolve_vector_min_similarity() is None


# ── the residue purge tool ──────────────────────────────────────────────────

def test_purge_tool_matches_residue_and_spares_genuine(tmp_path, stub_embedder):
    from tools.purge_test_residue import find_residue, is_residue

    assert is_residue("guard-remove-test")
    assert is_residue("dedup test sw_0e8ffedd")
    assert is_residue("  test point  ")          # whitespace-tolerant
    # genuine content that merely CONTAINS a residue phrase must not match
    assert not is_residue("the guard-remove-test path was fixed in #4028")
    assert not is_residue("Premise Labs inbound intake system should "
                          "auto-investigate all Sentry alerts")

    sdk = _sdk(tmp_path)
    try:
        sdk.create_point("statement", "guard-remove-test")
        sdk.create_point("statement", "test point")
        genuine = sdk.create_point("statement", "Premise Labs inbound intake")
        found = find_residue(sdk._get_proj().g)
        ids = {f["id"] for f in found}
        assert len(found) == 2
        assert genuine["id"] not in ids
    finally:
        sdk.close()


def test_purge_tool_expect_guard_aborts_before_deleting(tmp_path, stub_embedder):
    """--expect must gate BEFORE any delete, not after it (#4028 review P1)."""
    from tools import purge_test_residue as ptr

    db = str(tmp_path / "4028.db")
    sdk = _sdk(tmp_path)
    try:
        sdk.create_point("statement", "guard-remove-test")
    finally:
        sdk.close()

    with pytest.raises(SystemExit):
        ptr.main(["--db", db, "--apply", "--expect", "5"])

    sdk = _sdk(tmp_path)
    try:
        still = sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.content = 'guard-remove-test' "
            "RETURN p.id").result_set
        assert len(still) == 1, "--expect mismatch must delete NOTHING"
    finally:
        sdk.close()


def test_purge_tool_dry_run_deletes_nothing(tmp_path, stub_embedder):
    from tools.purge_test_residue import purge

    sdk = _sdk(tmp_path)
    try:
        sdk.create_point("statement", "guard-remove-test")
        report = purge(sdk, apply=False)
        assert report["found"] == 1 and report["deleted"] == 0
        # still there
        assert len(sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.content = 'guard-remove-test' "
            "RETURN p.id").result_set) == 1
        report = purge(sdk, apply=True)
        assert report["deleted"] == 1
        assert len(sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.content = 'guard-remove-test' "
            "RETURN p.id").result_set) == 0
    finally:
        sdk.close()
