"""Tests for tortoise.embeddings — cross-source concept matching via TF-IDF.

Runnable without pytest:  python3 tests/test_embeddings.py
(also works under pytest if installed).

Uses the TF-IDF fallback (sklearn) since sentence_transformers is heavy.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.embeddings import find_cross_source_matches  # noqa: E402


# ── helpers ──────────────────────────────────────────────────────────

def _match_ids(matches):
    """Return sorted (src, dst) tuples for easy comparison."""
    return sorted((m["src"], m["dst"]) for m in matches)


# ── 1. empty points ─────────────────────────────────────────────────

def test_empty_points():
    """Empty dict — sklearn raises ValueError (empty vocabulary)."""
    try:
        result = find_cross_source_matches({})
        # If it ever gets patched to return [], that's also correct.
        assert result == [], f"expected [] or ValueError, got {result!r}"
    except ValueError:
        pass  # current sklearn TfidfVectorizer behaviour on empty input
    print("PASS test_empty_points")


# ── 2. single point ─────────────────────────────────────────────────

def test_single_point():
    points = {"p1": {"content": "hello world", "speaker": "alice"}}
    matches = find_cross_source_matches(points)
    assert matches == [], f"expected [], got {matches}"
    print("PASS test_single_point")


# ── 3. same speaker ─────────────────────────────────────────────────

def test_same_speaker():
    points = {
        "p1": {"content": "the cat sat on the mat", "speaker": "alice"},
        "p2": {"content": "the cat sat on the mat", "speaker": "alice"},
    }
    matches = find_cross_source_matches(points)
    assert matches == [], f"same-speaker pairs must be excluded, got {matches}"
    print("PASS test_same_speaker")


# ── 4. different speakers, low similarity ────────────────────────────

def test_different_speakers_low_similarity():
    points = {
        "p1": {"content": "quantum physics research papers", "speaker": "alice"},
        "p2": {"content": "chocolate chip cookie recipes", "speaker": "bob"},
    }
    matches = find_cross_source_matches(points, threshold=0.75)
    assert matches == [], f"disjoint vocab should be below threshold, got {matches}"
    print("PASS test_different_speakers_low_similarity")


# ── 5. different speakers, high similarity ───────────────────────────

def test_different_speakers_high_similarity():
    points = {
        "p1": {"content": "the cat sat on the mat", "speaker": "alice"},
        "p2": {"content": "the cat sat on a mat", "speaker": "bob"},
    }
    matches = find_cross_source_matches(points, threshold=0.75)
    assert len(matches) == 1, f"expected 1 match, got {len(matches)}"
    m = matches[0]
    assert m["similarity"] >= 0.75, f"similarity {m['similarity']} < 0.75"
    assert set(m["speakers"]) == {"alice", "bob"}
    # src/dst are the two point ids in either order
    assert {m["src"], m["dst"]} == {"p1", "p2"}
    print("PASS test_different_speakers_high_similarity")


# ── 6. custom threshold ─────────────────────────────────────────────

def test_custom_threshold():
    points = {
        "p1": {"content": "the cat sat on the mat", "speaker": "alice"},
        "p2": {"content": "the cat sat on a mat", "speaker": "bob"},
        "p3": {"content": "dog runs in the park", "speaker": "charlie"},
    }
    # Low threshold (0.3) — should catch p1-p2 (similar) and
    # possibly p1-p3 / p2-p3 depending on TF-IDF scores.
    low = find_cross_source_matches(points, threshold=0.3)
    # High threshold (0.99) — only near-identical vectors.
    high = find_cross_source_matches(points, threshold=0.99)
    # Low threshold must find more (or equal) matches than high.
    assert len(low) >= len(high), \
        f"low threshold ({len(low)}) should find >= high threshold ({len(high)})"
    # p1-p2 are very similar and must appear in the low-threshold set.
    low_ids = _match_ids(low)
    assert ("p1", "p2") in low_ids, f"p1-p2 should match at threshold 0.3, got {low_ids}"
    print("PASS test_custom_threshold")


# ── 7. threshold zero ────────────────────────────────────────────────

def test_threshold_zero():
    points = {
        "p1": {"content": "aaa xyz", "speaker": "alice"},
        "p2": {"content": "bbb pqr", "speaker": "bob"},
        "p3": {"content": "ccc stu", "speaker": "charlie"},
    }
    matches = find_cross_source_matches(points, threshold=0.0)
    ids = _match_ids(matches)
    # All cross-speaker pairs: alice-bob, alice-charlie, bob-charlie
    expected = [("p1", "p2"), ("p1", "p3"), ("p2", "p3")]
    assert ids == expected, f"threshold 0.0 should match all cross-speaker pairs, got {ids}"
    # Verify no same-speaker pairs (there are none with 3 different speakers anyway).
    for m in matches:
        assert m["speakers"][0] != m["speakers"][1], \
            f"same-speaker pair should not appear: {m}"
    print("PASS test_threshold_zero")


# ── 8. threshold one ─────────────────────────────────────────────────

def test_threshold_one():
    points = {
        "p1": {"content": "hello world", "speaker": "alice"},
        "p2": {"content": "hello world", "speaker": "bob"},
        "p3": {"content": "hello world slightly different", "speaker": "charlie"},
    }
    matches = find_cross_source_matches(points, threshold=1.0)
    ids = _match_ids(matches)
    # p1-p2 have identical text → cosine 1.0 → match.
    # p1-p3 and p2-p3 differ → cosine < 1.0 → no match.
    assert ids == [("p1", "p2")], \
        f"threshold 1.0 should only match identical vectors, got {ids}"
    for m in matches:
        assert m["similarity"] >= 1.0, \
            f"similarity {m['similarity']} should be exactly 1.0 at threshold 1.0"
    print("PASS test_threshold_one")


# ── 9. multiple points mixed ─────────────────────────────────────────

def test_multiple_points_mixed():
    points = {
        "p1": {"content": "the cat sat on the mat", "speaker": "alice"},
        "p2": {"content": "the cat sat on a mat", "speaker": "bob"},
        "p3": {"content": "the cat sat on the mat", "speaker": "alice"},
        "p4": {"content": "dog runs in the park", "speaker": "charlie"},
    }
    matches = find_cross_source_matches(points, threshold=0.75)
    ids = _match_ids(matches)

    # p1-p2: high similarity, different speakers → match
    assert ("p1", "p2") in ids, "p1-p2 should match"

    # p2-p3: high similarity (p3 == p1), different speakers (bob vs alice) → match
    assert ("p2", "p3") in ids, "p2-p3 should match"

    # p1-p3: same speaker (alice) → excluded
    assert ("p1", "p3") not in ids, "p1-p3 same speaker, must be excluded"

    # p4 vs anyone: low similarity with all others → excluded
    for other in ("p1", "p2", "p3"):
        pair = tuple(sorted((other, "p4")))
        assert pair not in ids, f"{pair} should not match (low sim)"

    # No other unexpected pairs.
    assert ids == sorted([("p1", "p2"), ("p2", "p3")]), \
        f"expected exactly p1-p2 and p2-p3, got {ids}"
    print("PASS test_multiple_points_mixed")


# ── 10. no speaker field ─────────────────────────────────────────────

def test_no_speaker_field():
    points = {
        "p1": {"content": "hello world", "speaker": "alice"},
        "p2": {"content": "hello world"},  # no speaker → "unknown"
    }
    matches = find_cross_source_matches(points, threshold=0.7)
    # alice != unknown → should match (content is identical).
    assert len(matches) == 1, f"expected 1 match, got {len(matches)}"
    assert matches[0]["speakers"] == ["alice", "unknown"], \
        f"expected ['alice', 'unknown'], got {matches[0]['speakers']}"
    print("PASS test_no_speaker_field")

    # Both missing speaker → both "unknown" → same speaker → no match.
    points2 = {
        "p1": {"content": "hello world"},
        "p2": {"content": "hello world"},
    }
    matches2 = find_cross_source_matches(points2, threshold=0.7)
    assert matches2 == [], \
        f"both 'unknown' → same speaker → must be excluded, got {matches2}"
    print("PASS test_no_speaker_field (both missing)")


# ── 11. speaker name variation ───────────────────────────────────────

def test_speaker_name_variation():
    """Verify speaker field access uses .get() correctly — different
    speaker names produce different identities, identical names match."""
    points = {
        "p1": {"content": "the cat sat on the mat", "speaker": "Dr. Alice"},
        "p2": {"content": "the cat sat on the mat", "speaker": "Dr. Bob"},
        "p3": {"content": "the cat sat on the mat", "speaker": "Dr. Alice"},
    }
    matches = find_cross_source_matches(points, threshold=0.9)
    ids = _match_ids(matches)

    # p1-p3: same speaker "Dr. Alice" → excluded.
    assert ("p1", "p3") not in ids, \
        f"identical speaker names should be excluded, got {ids}"

    # p1-p2: different speakers → match.
    assert ("p1", "p2") in ids, "Dr. Alice vs Dr. Bob should match"

    # p2-p3: different speakers → match.
    assert ("p2", "p3") in ids, "Dr. Bob vs Dr. Alice should match"
    print("PASS test_speaker_name_variation")


# ── 12. sentence_transformers import path ─────────────────────────────

def test_sentence_transformers_path():
    """Force the sentence_transformers import to succeed via mock —
    covers lines 30-31 that the TF-IDF fallback skips."""
    import numpy as np
    from unittest.mock import MagicMock

    mock_st = MagicMock()
    mock_model = MagicMock()
    # Return two normalised-like vectors so cosine similarity is sensible.
    mock_model.encode.return_value = np.array(
        [[1.0, 0.0], [0.9, 0.1]], dtype=np.float64
    )
    mock_st.SentenceTransformer.return_value = mock_model

    # Inject the mock before the function runs its internal import.
    # The function does `from sentence_transformers import SentenceTransformer`
    # so we seed sys.modules with a fake module that has that attribute.
    import tortoise.embeddings as mod
    # #399 (D9b): the singleton refactor warms the real model during tests #1-11;
    # reset so the worker-thread load picks up the seeded mock below.
    mod.EmbeddingModel._reset()
    original = sys.modules.get("sentence_transformers")
    sys.modules["sentence_transformers"] = mock_st
    try:
        # Invalidate any cached import inside the module (it uses a local
        # import, so nothing is cached at module level — safe to just call).
        matches = find_cross_source_matches(
            {
                "p1": {"content": "hello world", "speaker": "alice"},
                "p2": {"content": "hello world", "speaker": "bob"},
            },
            threshold=0.7,
        )
        assert len(matches) == 1
        assert matches[0]["similarity"] >= 0.7
        mock_st.SentenceTransformer.assert_called_once_with("all-MiniLM-L6-v2")
        mock_model.encode.assert_called_once()
    finally:
        # #399 (D9b): clear the singleton — it may hold the MOCK model loaded
        # during this test; leaving it warm poisons later search_points calls
        # (mock.encode returns a fixed 2x2 array for any input).
        mod.EmbeddingModel._reset()
        if original is None:
            del sys.modules["sentence_transformers"]
        else:
            sys.modules["sentence_transformers"] = original
    print("PASS test_sentence_transformers_path")


# ── runner ───────────────────────────────────────────────────────────

def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall embedding tests passed")


if __name__ == "__main__":
    _run_all()
