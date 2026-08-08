"""Tests for tortoise.cross_lens — embedding-based cross-lens candidate generation (#399).

Deterministic via injected encode (fixed vectors); one real-embedder e2e test
guarded by sentence_transformers availability (model cached locally).

Runnable without pytest:  python3 tests/test_cross_lens.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.cross_lens import (  # noqa: E402
    DEFAULT_THRESHOLD,
    NEAR_DUPLICATE_THRESHOLD,
    find_cross_lens_matches,
)


# ── deterministic fake encoder ────────────────────────────────────────

# Fixed vectors: a≈b (cross-lens in-band), c≈p3 mid, x/p4 orthogonal noise.
# Content doubles as the point id; the map is the deterministic encoder.
_V = {
    "p1": np.array([1.0, 0.0, 0.0]),
    "p2": np.array([0.9, 0.1, 0.0]),
    "p3": np.array([0.0, 1.0, 0.0]),
    "p4": np.array([0.0, 0.0, 1.0]),
    "a": np.array([1.0, 0.0, 0.0]),
    "b": np.array([0.9, 0.1, 0.0]),
    "c": np.array([0.0, 1.0, 0.0]),
    "x": np.array([0.0, 0.0, 1.0]),
}


def _fake_encode(texts: list[str]) -> np.ndarray:
    return np.stack([_V[t] for t in texts])


def _pts(**kw):
    d = {
        "p1": {"content": "p1", "lens": "l1"},
        "p2": {"content": "p2", "lens": "l2"},
        "p3": {"content": "p3", "lens": "l2"},
        "p4": {"content": "p4", "lens": "l1"},
    }
    d.update(kw)
    return d


# ── 1. same-lens pairs excluded ──────────────────────────────────────

def test_same_lens_excluded():
    pts = _pts(p2={"content": "p2", "lens": "l1"})  # p1,p2 same lens, similar vecs
    cands = find_cross_lens_matches(pts, encode=_fake_encode)
    assert all({c["src"], c["dst"]} != {"p1", "p2"} for c in cands)
    print("PASS test_same_lens_excluded")


# ── 2. cross-lens pairing + candidate shape ───────────────────────────

def test_cross_lens_pairing_and_shape():
    cands = find_cross_lens_matches(_pts(), encode=_fake_encode)
    by = {(c["src"], c["dst"]): c for c in cands}
    assert ("p1", "p2") in by and by[("p1", "p2")]["similarity"] >= 0.9
    c = by[("p1", "p2")]
    assert set(c) == {"src", "dst", "similarity", "lenses", "speakers", "degraded"}
    assert c["lenses"] == ["l1", "l2"]
    assert c["degraded"] is False
    print("PASS test_cross_lens_pairing_and_shape")


# ── 3. threshold monotonicity ─────────────────────────────────────────

def test_threshold_monotonicity():
    low = find_cross_lens_matches(_pts(), threshold=0.1, encode=_fake_encode)
    high = find_cross_lens_matches(_pts(), threshold=0.999, encode=_fake_encode)
    assert len(low) >= len(high)
    # p1-p2 (0.9) must survive at 0.1 but not at 0.95.
    assert any({c["src"], c["dst"]} == {"p1", "p2"} for c in low)
    assert not any({c["src"], c["dst"]} == {"p1", "p2"} for c in high)  # cosine 0.994 < 0.999
    print("PASS test_threshold_monotonicity")


# ── 4. lens derivation chain ──────────────────────────────────────────

def test_lens_derivation_chain():
    # no lens field → source → provenance.source_id → speaker → unknown
    pts = {
        "a": {"content": "a", "source": "s1"},
        "b": {"content": "b", "provenance": {"source_id": "s2"}},
    }
    cands = find_cross_lens_matches(pts, encode=_fake_encode)
    assert cands and {tuple(c["lenses"]) for c in cands} == {("s1", "s2")}

    pts2 = {"a": {"content": "a", "speaker": "alice"},
            "b": {"content": "b", "speaker": "bob"}}
    cands2 = find_cross_lens_matches(pts2, encode=_fake_encode)  # speaker fallback
    assert cands2 and {tuple(c["lenses"]) for c in cands2} == {("alice", "bob")}

    pts3 = {"a": {"content": "a"}, "b": {"content": "b"}}  # both unknown → same lens
    assert find_cross_lens_matches(pts3, encode=_fake_encode) == []
    print("PASS test_lens_derivation_chain")


# ── 5. explicit lens_key ──────────────────────────────────────────────

def test_lens_key_explicit():
    pts = {"a": {"content": "a", "sector": "x"}, "b": {"content": "b", "sector": "x"}}
    assert find_cross_lens_matches(pts, lens_key="sector", encode=_fake_encode) == []
    pts2 = {"a": {"content": "a", "sector": "x"}, "b": {"content": "b", "sector": "y"}}
    assert find_cross_lens_matches(pts2, lens_key="sector", encode=_fake_encode)
    print("PASS test_lens_key_explicit")


# ── 6. sorted by similarity desc ──────────────────────────────────────

def test_sorted_by_similarity_desc():
    pts = {"a": {"content": "a", "lens": "l1"}, "b": {"content": "b", "lens": "l2"},
           "c": {"content": "c", "lens": "l1"}}
    # threshold 0.05 → TWO cross-lens candidates survive: a-b (cosine 0.994) and
    # b-c (cosine 0.110) — a-c is same-lens (excluded). Sort must be descending.
    cands = find_cross_lens_matches(pts, threshold=0.05, encode=_fake_encode)
    assert len(cands) == 2, f"expected 2 candidates, got {len(cands)}"
    sims = [c["similarity"] for c in cands]
    assert sims == sorted(sims, reverse=True)
    assert cands[0]["similarity"] >= cands[1]["similarity"]
    print("PASS test_sorted_by_similarity_desc")


# ── 7. empty / single-point inputs ────────────────────────────────────

def test_empty_and_single_point():
    assert find_cross_lens_matches({}, encode=_fake_encode) == []
    assert find_cross_lens_matches({"a": {"content": "a", "lens": "l1"}},
                                   encode=_fake_encode) == []
    print("PASS test_empty_and_single_point")


# ── 8. degraded flag on TF-IDF fallback ───────────────────────────────

def test_degraded_flag_on_tfidf_fallback():
    pytest.importorskip("sklearn")
    from unittest.mock import patch

    from tortoise.embeddings import EmbeddingModel
    # Near-identical texts so TF-IDF cosine clears the default threshold.
    pts = {"a": {"content": "the cat sat on the mat", "lens": "l1"},
           "b": {"content": "the cat sat on a mat", "lens": "l2"}}
    with patch.object(EmbeddingModel, "get", return_value=None):
        cands = find_cross_lens_matches(pts)
    assert cands and all(c["degraded"] for c in cands)
    print("PASS test_degraded_flag_on_tfidf_fallback")


# ── 9. encode param is honored ────────────────────────────────────────

def test_encode_param_used():
    from unittest.mock import MagicMock

    enc = MagicMock(side_effect=_fake_encode)
    find_cross_lens_matches(_pts(), encode=enc)
    enc.assert_called_once()
    print("PASS test_encode_param_used")


# ── 10. constants ─────────────────────────────────────────────────────

def test_constants():
    assert DEFAULT_THRESHOLD == 0.40
    assert NEAR_DUPLICATE_THRESHOLD == 0.75
    print("PASS test_constants")


# ── 11. singleton reuse (regression: per-call 90MB model reload) ──────

def test_singleton_reused_across_calls():
    """#399: _encode must route through ONE model instance — the per-call
    SentenceTransformer instantiation bug (embeddings.py) is fixed."""
    from unittest.mock import MagicMock

    from tortoise.embeddings import EmbeddingModel, _encode

    EmbeddingModel._reset()
    fake = MagicMock()
    fake.encode.return_value = np.array([[1.0, 0.0], [0.9, 0.1]])
    orig_get = EmbeddingModel.get
    EmbeddingModel.get = lambda: fake  # noqa: E731 — bypass threaded load for test
    try:
        v1, d1 = _encode(["a"])
        v2, d2 = _encode(["b"])
    finally:
        EmbeddingModel.get = orig_get
        EmbeddingModel._reset()
    assert fake.encode.call_count == 2, "two _encode calls must reuse ONE model"
    assert not d1 and not d2
    assert v1.shape == (2, 2)
    print("PASS test_singleton_reused_across_calls")


# ── real-embedder e2e (guarded; model cached locally) ─────────────────

def test_real_embedder_cross_vocab_in_band_and_noise():
    pytest.importorskip("sentence_transformers")
    _require_model()
    pts = {
        "p1": {"content": "Growth depends on distribution channels and partnerships",
               "lens": "contemporary"},
        "p2": {"content": "Winning requires strong go to market and channel partners",
               "lens": "practitioner"},
        "p3": {"content": "quantum physics research papers", "lens": "lens-a"},
        "p4": {"content": "chocolate chip cookie recipes", "lens": "lens-b"},
    }
    cands = find_cross_lens_matches(pts)  # default threshold 0.40
    pair12 = next(c for c in cands if {c["src"], c["dst"]} == {"p1", "p2"})
    assert pair12["similarity"] >= 0.35  # measured 0.448
    assert pair12["degraded"] is False
    assert not any({c["src"], c["dst"]} == {"p3", "p4"} for c in cands)  # 0.121 noise
    print("PASS test_real_embedder_cross_vocab_in_band_and_noise")


def test_real_embedder_motivating_pair_below_default():
    pytest.importorskip("sentence_transformers")
    _require_model()
    pts = {
        "a": {"content": "Cost inversion from fixed to variable", "lens": "contemporary"},
        "b": {"content": "MVP now costs ~$100", "lens": "practitioner"},
    }
    # 0.291 < 0.40 — recall-only boundary: candidates are recall-oriented,
    # verification decides. Topical similarity is NOT logical implication.
    assert find_cross_lens_matches(pts) == []
    print("PASS test_real_embedder_motivating_pair_below_default")


def test_real_embedder_near_duplicate_above_near_dup_threshold():
    pytest.importorskip("sentence_transformers")
    _require_model()
    pts = {
        "a": {"content": "Deployments must be automated for reliability", "lens": "l1"},
        "b": {"content": "Automating deployments is required for reliability", "lens": "l2"},
    }
    cands = find_cross_lens_matches(pts, threshold=NEAR_DUPLICATE_THRESHOLD)
    assert len(cands) == 1 and cands[0]["similarity"] >= 0.9  # measured 0.958
    print("PASS test_real_embedder_near_duplicate_above_near_dup_threshold")


# ── runner ────────────────────────────────────────────────────────────

def _require_model():
    """Skip unless the real embedder is actually available (CI has no HF cache)."""
    from tortoise.embeddings import EmbeddingModel

    if EmbeddingModel.get() is None:
        pytest.skip("all-MiniLM-L6-v2 unavailable — model load timed out")


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except pytest.skip.Exception:
                print(f"SKIP {name}")


if __name__ == "__main__":
    _run_all()
