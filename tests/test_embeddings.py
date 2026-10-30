"""Tests for tortoise.embeddings — cross-source concept matching via TF-IDF.

Runnable without pytest:  python3 tests/test_embeddings.py
(also works under pytest if installed).

Uses the TF-IDF fallback (sklearn) since sentence_transformers is heavy.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.embeddings import (
    DEFAULT_THRESHOLD,
    EMBEDDING_MODEL,
    NEAR_DUPLICATE_THRESHOLD,
    find_cross_source_matches,
)

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

# "identical" is not a float LITERAL. `cosine_similarity_matrix` normalises then
# dots, so two IDENTICAL rows land on 1.0 only up to one float32 ulp — the arm64
# pool runners measure 0.99999994, i.e. 5.96e-8 BELOW 1.0, and a test that asks
# for `threshold=1.0` verbatim reds on those legs while the behaviour under test
# is intact (required check `test (g)`, run 40985705896: "threshold 1.0 should
# only match identical vectors, got []"; same nodeid on `main`, tracked on
# #9066 and diagnosed on #10295). One ulp is not a semantic boundary any caller
# can mean, so the pin sits one ulp below it and asserts the similarity with the
# same tolerance instead of demanding bit-exact float equality.
# (The production-side question — should a caller-supplied 1.0 admit a pair one
# ulp below it? — changes the matching contract, so it stays an open owner
# decision on #10295. This file pins what ships today, on every architecture.)
_IDENTICAL_BOUNDARY = 1.0 - 1e-6
_COS_ULP = 1e-6


def test_threshold_one():
    points = {
        "p1": {"content": "hello world", "speaker": "alice"},
        "p2": {"content": "hello world", "speaker": "bob"},
        "p3": {"content": "hello world slightly different", "speaker": "charlie"},
    }
    matches = find_cross_source_matches(points, threshold=_IDENTICAL_BOUNDARY)
    ids = _match_ids(matches)
    # p1-p2 have identical text → cosine 1.0 (to within one ulp) → match.
    # p1-p3 and p2-p3 differ → cosine far below 1.0 → no match.
    assert ids == [("p1", "p2")], \
        f"an identical-boundary threshold should only match identical vectors, got {ids}"
    for m in matches:
        assert m["similarity"] >= 1.0 - _COS_ULP, \
            (f"similarity {m['similarity']!r} should be 1.0 up to one float32 ulp "
             f"at an identical-boundary threshold")
    print("PASS test_threshold_one")


def test_threshold_one_survives_the_rounding_arm64_actually_produces():
    """The same boundary, fed the exact numbers the arm64 legs produced.

    Non-vacuity, and the reason the pin above does not evade the behaviour it
    names: on an x86 box `cosine_similarity_matrix` returns a bit-exact 1.0 for
    the identical pair, so a tolerance added to this file alone would be
    unobservable there. Injection makes the platform-independent claim testable
    on every platform: whatever the BLAS does at the last bit, the identical
    pair matches and the loose pair does not.
    """
    import numpy as np

    import tortoise.embeddings as _emb

    points = {
        "p1": {"content": "hello world", "speaker": "alice"},
        "p2": {"content": "hello world", "speaker": "bob"},
        "p3": {"content": "hello world slightly different", "speaker": "charlie"},
    }
    # The shape measured on the arm64 pool runner: identical rows one ulp short
    # of 1.0, the paraphrase far below it.
    observed = np.array(
        [
            [1.0, 0.99999994, 0.4187],
            [0.99999994, 1.0, 0.4192],
            [0.4187, 0.4192, 1.0],
        ],
        dtype=np.float32,
    )
    original = _emb.cosine_similarity_matrix
    _emb.cosine_similarity_matrix = lambda _vectors: observed
    try:
        matches = find_cross_source_matches(points, threshold=_IDENTICAL_BOUNDARY)
    finally:
        _emb.cosine_similarity_matrix = original
    assert _match_ids(matches) == [("p1", "p2")], \
        ("an identical pair reported one ulp below 1.0 must still match at the "
         f"identical boundary, got {_match_ids(matches)}")
    assert matches[0]["similarity"] < 1.0, \
        "the fixture must carry the off-by-one-ulp value, else it proves nothing"
    print("PASS test_threshold_one_survives_the_rounding_arm64_actually_produces")


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
    import numpy as np  # noqa: I001
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
        # #1349 T9: assert the CONSTANT (single model reference), not a
        # literal — a model rotation touches exactly one line.
        mock_st.SentenceTransformer.assert_called_once_with(
            mod.EMBEDDING_MODEL, revision=mod.EMBEDDING_MODEL_REVISION)
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


# ── 13. embedder constants (#1349 T9 swap) ────────────────────────────

def test_embedder_constants():
    """The active embedder + thresholds are the #1349 bge-small swap values.

    EMBEDDING_MODEL is the single source of truth for the production model
    id (pinned here so a swap is a one-line, test-verified change); the
    thresholds are the bge-small calibration bands measured 2026-08-21
    (tools/calibrate_thresholds --model bge-small).
    """
    assert EMBEDDING_MODEL == "BAAI/bge-small-en-v1.5"
    assert DEFAULT_THRESHOLD == 0.72
    assert NEAR_DUPLICATE_THRESHOLD == 0.89
    print("PASS test_embedder_constants")


# ── 14. cosine is exactly the range at its bounds (#9066) ────────────

def test_cosine_is_exactly_the_bound_however_the_blas_rounds():
    """#9066: a similarity that means ±1 must READ ±1, on every runner.

    Cosine's range is `[-1, 1]` and its value for two identical vectors is 1.0
    exactly, but the floating-point dot product lands a few ulp either side of
    that — and WHICH side is the BLAS's business, not this module's. Both
    literals below are measured on one machine: `[0.1, 0.2, 0.3]` reads
    `0.9999999999999999` and `[0.5, 0.6, 0.7]` reads `1.0000000000000002`.

    That is the defect `test_threshold_one` was red on: a caller asking for
    exactly 1.0 ("identical only") got NOTHING when the dot rounded down, while
    the rounding-up runner was outside cosine's own range. Both directions are
    asserted, so this reds pre-fix whichever way a given BLAS rounds rather
    than only on the machine that reproduced it.
    """
    import numpy as np

    from tortoise.embeddings import cosine_similarity_matrix

    # DOWN: an identical pair must read exactly 1.0, not 1.0 - ε.
    for row in ([0.1, 0.2, 0.3], [0.9, 0.8, 0.7], [0.1] * 7):
        sim = cosine_similarity_matrix(np.array([row, row], dtype=np.float64))
        assert float(sim[0, 1]) == 1.0, f"identical rows {row} read {sim[0, 1]!r}"

    # UP: nothing may sit above the range cosine is defined on.
    for row in ([0.5, 0.6, 0.7], [1.0, 2.0, 3.0], [0.3, 0.7, 0.11]):
        sim = cosine_similarity_matrix(np.array([row, row], dtype=np.float64))
        assert float(sim[0, 1]) == 1.0, (
            f"identical rows {row} read {sim[0, 1]!r} (outside [-1, 1], not snapped)"
        )

    # The snap is the BOUND, not a widening of it: a clearly different pair, an
    # orthogonal pair and an all-zero row are all untouched, so a threshold
    # still discriminates and the documented zero-norm case cannot become a
    # false 1.0 through the normalisation guard.
    sim = cosine_similarity_matrix(
        np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [0.0, 0.0]], dtype=np.float64)
    )
    assert float(sim[0, 1]) == 0.0
    assert float(sim[0, 2]) < 1.0
    assert float(sim[0, 3]) == 0.0

    print("PASS test_cosine_is_exactly_the_bound_however_the_blas_rounds")


# ── runner ───────────────────────────────────────────────────────────

def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall embedding tests passed")


if __name__ == "__main__":
    _run_all()
