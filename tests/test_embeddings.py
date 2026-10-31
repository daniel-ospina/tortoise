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


# ── 14. #10296: the intra-op thread pin ───────────────────────────────
#
# torch's intra-op pool defaults to nproc, and for the short-text calls this
# module makes that default is ~35x SLOWER per call than a single thread (the
# table is on ``EMBED_DEFAULT_INTRA_OP_THREADS``). On the contended self-hosted
# pool it reached 40-90s per ``tests/test_capture_session.py`` item, and the
# REQUIRED ``test-slow (e)`` leg spent its whole 40m watchdog inside the
# embedding forward pass.
#
# These are the pins for the DECISION (one thread by default), the OPERATOR
# LEVER (``TORTOISE_EMBED_THREADS``), and the WIRING (the load applies it —
# a helper nobody calls would leave every one of them green).

def _saved_env(names):
    return {name: os.environ.get(name) for name in names}


def _restore_env(saved):
    for name, value in saved.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


def _embed_module():
    import tortoise.embeddings as mod
    return mod


def test_embed_threads_default_is_the_measured_single_thread():
    """One thread is the default BECAUSE it was measured, not assumed."""
    mod = _embed_module()
    saved = _saved_env([mod.EMBED_THREADS_ENV])
    os.environ.pop(mod.EMBED_THREADS_ENV, None)
    try:
        assert mod.EMBED_DEFAULT_INTRA_OP_THREADS == 1, (
            "the measured default is one intra-op thread; widening it re-opens "
            "#10296 (a single short call is 126x slower at nproc)")
        assert mod.resolve_embed_threads() == (1, "default")
    finally:
        _restore_env(saved)


def test_embed_threads_env_is_read_per_call_and_refuses_junk():
    """The operator lever works, is re-read each time, and fails CLOSED to the
    default rather than to ``nproc`` for an unparseable value."""
    mod = _embed_module()
    saved = _saved_env([mod.EMBED_THREADS_ENV])
    try:
        os.environ[mod.EMBED_THREADS_ENV] = "4"
        assert mod.resolve_embed_threads() == (4, "env")
        os.environ[mod.EMBED_THREADS_ENV] = "2"
        assert mod.resolve_embed_threads() == (2, "env"), "the pin is read per call"
        for junk in ("0", "-2", "abc", "1.5", "  "):
            os.environ[mod.EMBED_THREADS_ENV] = junk
            assert mod.resolve_embed_threads() == (1, "default"), (
                f"{junk!r} must fall back to the measured default, not to nproc")
    finally:
        _restore_env(saved)


def test_pin_embed_threads_bounds_torch_and_is_observable():
    """``pin_embed_threads`` bounds the REAL torch pool and records it.

    The record is what ``EmbeddingModel.status()`` publishes (#10296 asks for
    the pin to be observable) — a pin that nobody can read is the state that
    made this a 3-day mystery on a required leg.
    """
    try:
        import torch
    except Exception:  # noqa: BLE001 — the designed zero-dependency path
        print("SKIP test_pin_embed_threads_bounds_torch_and_is_observable")
        return
    mod = _embed_module()
    saved = _saved_env([mod.EMBED_THREADS_ENV, *mod._EMBED_THREAD_ENV_VARS])
    saved_threads = torch.get_num_threads()
    try:
        for name in [mod.EMBED_THREADS_ENV, *mod._EMBED_THREAD_ENV_VARS]:
            os.environ.pop(name, None)
        start = 2 if (os.cpu_count() or 1) >= 2 else 1
        torch.set_num_threads(start)
        record = mod.pin_embed_threads()
        assert record["applied"] is True, record
        assert record["threads"] == 1 and record["source"] == "default", record
        assert record["previous"] == start, (
            f"the pin must record what it replaced, got {record!r}")
        assert torch.get_num_threads() == 1, (
            "torch's intra-op pool is still unbounded — every short embedding "
            "pays the barrier cost (#10296)")
        assert mod.embed_thread_pin() == record
        assert mod.EmbeddingModel.status()["threads"] == record, (
            "status() must publish the applied pin")
        for name in mod._EMBED_THREAD_ENV_VARS:
            assert os.environ.get(name) == "1", (
                f"{name} must be bounded too — it sizes the BLAS pools and any "
                f"child process, which torch.set_num_threads cannot reach")
        # Second call is idempotent, not a re-pin of something else.
        again = mod.pin_embed_threads()
        assert again["threads"] == 1 and again["previous"] == 1, again
    finally:
        torch.set_num_threads(saved_threads)
        _restore_env(saved)


def test_pin_env_leaves_an_operator_value_alone():
    """``setdefault`` semantics: an explicit env is the operator's call."""
    mod = _embed_module()
    saved = _saved_env(list(mod._EMBED_THREAD_ENV_VARS))
    try:
        os.environ["OMP_NUM_THREADS"] = "2"
        for name in mod._EMBED_THREAD_ENV_VARS[1:]:
            os.environ.pop(name, None)
        record = mod.pin_embed_threads()
        assert os.environ["OMP_NUM_THREADS"] == "2", (
            "an operator-set OMP_NUM_THREADS must not be overwritten")
        assert "OMP_NUM_THREADS" not in record["env_vars"]
        for name in mod._EMBED_THREAD_ENV_VARS[1:]:
            assert os.environ.get(name) == str(record["threads"])
    finally:
        _restore_env(saved)


def test_the_embedder_load_applies_the_pin():
    """The WIRING: the pin is applied by ``EmbeddingModel``'s load, not only by
    the helper. A load path that forgot to call it leaves every test above
    green and CI exactly as slow as before."""
    mod = _embed_module()
    try:
        import torch
    except Exception:  # noqa: BLE001
        print("SKIP test_the_embedder_load_applies_the_pin")
        return
    saved = _saved_env([mod.EMBED_THREADS_ENV, *mod._EMBED_THREAD_ENV_VARS])
    saved_threads = torch.get_num_threads()
    real_import = mod.import_sentence_transformer
    seen = {"imported": False}

    def _raise_import_error():
        seen["imported"] = True
        raise ImportError("test double — do not load the real encoder")

    try:
        for name in [mod.EMBED_THREADS_ENV, *mod._EMBED_THREAD_ENV_VARS]:
            os.environ.pop(name, None)
        mod.EmbeddingModel._reset()
        mod._embed_thread_pin = None
        torch.set_num_threads(2 if (os.cpu_count() or 1) >= 2 else 1)
        mod.import_sentence_transformer = _raise_import_error
        mod.EmbeddingModel(load_timeout=5.0)
        assert seen["imported"], (
            "the load never reached the encoder import — this pin is vacuous")
        assert mod.embed_thread_pin() is not None, (
            "EmbeddingModel's load did not pin the intra-op pool (#10296)")
        # ⛔ The load runs on a WORKER thread, and torch's per-thread override
        # survives: this thread still reads its own count. That is why every
        # encode site re-asserts the pin — assert the re-assert here, not the
        # load thread's private view.
        mod.ensure_embed_threads()
        assert torch.get_num_threads() == 1, (
            "the encode path left torch at its default thread count")
    finally:
        mod.import_sentence_transformer = real_import
        torch.set_num_threads(saved_threads)
        _restore_env(saved)
        # The failed load armed the negative cache; clear it so later tests in
        # this file (and this file's own no-pytest runner) still see a real
        # embedder.
        mod.EmbeddingModel._reset()
        mod._embed_thread_pin = None

def test_ensure_embed_threads_rebounds_a_thread_that_set_its_own_count():
    """The trap ``pin_embed_threads`` alone does not close.

    ``torch.set_num_threads`` is not fully process-global: a thread that set its
    own count keeps it (measured — a worker's ``set_num_threads(1)`` is visible
    to that worker while the main thread still reads ``2``). The load runs on a
    worker thread, and the forward pass runs on whichever thread calls
    ``encode`` — so bounding only at load time leaves the actual encode
    unbounded, which is exactly a fix that is applied but not where it matters.
    """
    try:
        import torch
    except Exception:  # noqa: BLE001
        print("SKIP test_ensure_embed_threads_rebounds_a_thread_that_set_its_own_count")
        return
    mod = _embed_module()
    saved = _saved_env([mod.EMBED_THREADS_ENV, *mod._EMBED_THREAD_ENV_VARS])
    saved_threads = torch.get_num_threads()
    try:
        os.environ.pop(mod.EMBED_THREADS_ENV, None)
        if (os.cpu_count() or 1) >= 2:
            torch.set_num_threads(2)
            assert torch.get_num_threads() == 2, "precondition: this thread is wide"
        mod.ensure_embed_threads()
        assert torch.get_num_threads() == 1, (
            "ensure_embed_threads must re-bound THIS thread before a forward "
            "pass, not rely on the load thread's private view (#10296)")
        mod.ensure_embed_threads()  # idempotent, still 1
        assert torch.get_num_threads() == 1
    finally:
        torch.set_num_threads(saved_threads)
        _restore_env(saved)


# ── runner ───────────────────────────────────────────────────────────
#
# LAST in the file, on purpose (#10010 review F3): ``_run_all`` discovers the
# tests from ``globals()`` AT CALL TIME, so a guard placed above a later test
# block silently runs none of it — the standalone mode this module's docstring
# promises would print "all embedding tests passed" having executed only the
# tests above the guard. Keeping the guard at EOF is what makes adding a test
# below it impossible.

def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\nall embedding tests passed")


if __name__ == "__main__":
    _run_all()
