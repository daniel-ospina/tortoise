"""Unit tests for tools/embedder_probe.py — the #1349 probe injection seam.

The probe monkeypatches the ``SentenceTransformer`` symbol that
``tortoise.embeddings.EmbeddingModel._load()`` resolves at call time (the
function-local import at embeddings.py:108), so the harness can run any
candidate embedder without touching production code.

Mechanism under test (mirrors the proven pattern at
tests/test_embeddings.py:258-269): tests seed ``sys.modules["sentence_transformers"]``
with a fake module whose ``SentenceTransformer`` factory returns deterministic
fake models — except the real-model integration test (all-MiniLM-L6-v2, which
IS cached) patches the real module. Non-MiniLM candidates are never needed
offline; their coverage is the registry + mock-injection tests.
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import embedder_probe as probe  # noqa: E402
from tortoise.embeddings import EmbeddingModel, _encode  # noqa: E402

FAKE_COMMIT = "a" * 40  # recorded resolved revision on fake models


class FakeST:
    """Deterministic fake SentenceTransformer: 384-dim vectors keyed by model id.

    Vectors differ per model id (seed derived from the id) so the
    discriminating warm-process swap check is meaningful.
    """

    def __init__(self, model_name_or_path, revision=None, **kwargs):
        self.model_name_or_path = str(model_name_or_path)
        self.revision = revision
        self.kwargs = dict(kwargs)
        self._commit_hash = FAKE_COMMIT

    def get_embedding_dimension(self):
        return 384

    def encode(self, sentences, **kwargs):
        texts = list(sentences)
        seed = int(hashlib.sha1(self.model_name_or_path.encode()).hexdigest()[:8], 16)
        rng = np.random.RandomState(seed)
        return rng.rand(len(texts), 384).astype(np.float32)


class Dim768ST(FakeST):
    """Wrong-dimension fake — must trip the 384-dim HARD FAIL."""

    def get_embedding_dimension(self):
        return 768


class FailingST:
    """Factory whose construction always fails — must trip the load HARD FAIL."""

    def __init__(self, model_name_or_path, **kwargs):
        raise RuntimeError("simulated model load failure")


class FailingEncodeST(FakeST):
    """Loads fine but encode() always fails — must trip the encode HARD FAIL."""

    def encode(self, sentences, **kwargs):
        raise RuntimeError("simulated encode failure")


class ConstantST(FakeST):
    """Every model id returns the SAME vector — the stale-singleton case where
    the warm-process swap must HARD FAIL (identical pre/post vectors)."""

    def encode(self, sentences, **kwargs):
        texts = list(sentences)
        return np.ones((len(texts), 384), dtype=np.float32) / np.sqrt(384.0)


def _seed_st(factory=FakeST):
    """Seed sys.modules with a fake sentence_transformers module; returns
    (fake_module, previous_entry) for restoration."""
    fake = SimpleNamespace(SentenceTransformer=factory)
    prev = sys.modules.get("sentence_transformers")
    sys.modules["sentence_transformers"] = fake
    return fake, prev


def _restore_st(prev):
    if prev is None:
        sys.modules.pop("sentence_transformers", None)
    else:
        sys.modules["sentence_transformers"] = prev


@pytest.fixture(autouse=True)
def _clean_probe_state():
    """Every test starts/ends with the probe reset and singleton cleared."""
    probe.reset()
    yield
    probe.reset()


def test_probe_models_registry_spec_locked():
    """The registry maps the 4 candidate short names to their HF ids."""
    assert probe.PROBE_MODELS == {
        "minilm": "sentence-transformers/all-MiniLM-L6-v2",
        "arctic-xs": "snowflake/snowflake-arctic-embed-xs",
        "arctic-s": "snowflake/snowflake-arctic-embed-s",
        "bge-small": "BAAI/bge-small-en-v1.5",
    }


def test_inject_sets_singleton_and_routes_encode():
    fake, prev = _seed_st()
    try:
        state = probe.inject_model("minilm")
        assert state["name"] == "minilm"
        assert state["hf_id"] == "sentence-transformers/all-MiniLM-L6-v2"
        assert state["dim"] == 384

        # Singleton is set and serves the candidate, not the default literal.
        model = EmbeddingModel.get()
        assert isinstance(model, FakeST)
        assert model.model_name_or_path == "sentence-transformers/all-MiniLM-L6-v2"
        assert EmbeddingModel.get() is EmbeddingModel.get()  # same singleton

        # _encode routes through the injected candidate, never degrades.
        vecs, degraded = _encode(["hello world", "another text"])
        assert degraded is False
        assert vecs.shape == (2, 384)

        # The wrapper routed the default literal to the candidate id.
        assert fake.SentenceTransformer is not FakeST  # wrapped while active
    finally:
        _restore_st(prev)


def test_inject_unknown_model_raises():
    with pytest.raises(KeyError):
        probe.inject_model("nope")


def test_dim_mismatch_raises_hard_fail():
    fake, prev = _seed_st(Dim768ST)
    try:
        with pytest.raises(probe.EmbedderProbeError, match="dim 768"):
            probe.inject_model("minilm")
        # Failure leaves no recorded state and a clean singleton.
        assert probe.get_state() is None
        assert EmbeddingModel._instance is None
    finally:
        _restore_st(prev)


def test_load_failure_raises_never_degrades():
    fake, prev = _seed_st(FailingST)
    try:
        # Candidate fails to construct → EmbeddingModel returns None → the
        # probe HARD FAILS instead of letting _encode fall through to TF-IDF.
        with pytest.raises(probe.EmbedderProbeError, match="FAILED TO LOAD"):
            probe.inject_model("bge-small")
        assert probe.get_state() is None
        # Cooldown cleared: the singleton can retry immediately.
        assert EmbeddingModel._last_failed_at is None
    finally:
        _restore_st(prev)


def test_encode_failure_raises_hard_fail():
    fake, prev = _seed_st(FailingEncodeST)
    try:
        # Model loads at the right dim but fails to encode — the warm-process
        # discriminating check must HARD FAIL rather than accept a broken model.
        with pytest.raises(probe.EmbedderProbeError, match="failed to encode"):
            probe.inject_model("minilm")
        assert probe.get_state() is None
        assert EmbeddingModel._instance is None
    finally:
        _restore_st(prev)


def test_reset_restores_original_state():
    fake, prev = _seed_st()
    try:
        probe.inject_model("minilm")
        assert probe.get_state() is not None
        assert fake.SentenceTransformer is not FakeST

        probe.reset()

        assert probe.get_state() is None
        # Symbol restored to the original factory.
        assert fake.SentenceTransformer is FakeST
        # Singleton cleared: the next get() reloads the DEFAULT literal via
        # the restored symbol (proving the default path works again).
        model = EmbeddingModel.get()
        assert isinstance(model, FakeST)
        assert model.model_name_or_path == "all-MiniLM-L6-v2"
    finally:
        _restore_st(prev)


def test_warm_process_swap_is_genuine():
    fake, prev = _seed_st()
    try:
        # Warm the singleton with the DEFAULT model first (no injection).
        warm = EmbeddingModel.get()
        assert isinstance(warm, FakeST)
        assert warm.model_name_or_path == "all-MiniLM-L6-v2"
        ref = np.asarray(warm.encode([probe._DISCRIMINATOR_TEXT]))[0]

        # Inject a DIFFERENT candidate over the warm singleton.
        state = probe.inject_model("bge-small")
        assert state["hf_id"] == "BAAI/bge-small-en-v1.5"

        new_model = EmbeddingModel.get()
        assert new_model.model_name_or_path == "BAAI/bge-small-en-v1.5"
        new_vec = np.asarray(new_model.encode([probe._DISCRIMINATOR_TEXT]))[0]
        # Discriminating check: vectors must genuinely differ post-swap.
        assert probe._cosine(new_vec, ref) < probe.WARM_SWAP_COSINE_TOL
    finally:
        _restore_st(prev)


def test_warm_same_model_reinject_is_ok():
    fake, prev = _seed_st()
    try:
        probe.inject_model("minilm")
        # Re-injecting the same candidate skips the discriminating check
        # (vectors would legitimately be identical) — must not raise.
        state = probe.inject_model("minilm")
        assert state["hf_id"] == "sentence-transformers/all-MiniLM-L6-v2"
        assert EmbeddingModel.get().model_name_or_path == state["hf_id"]
    finally:
        _restore_st(prev)


def test_active_injection_overridden_by_different_model():
    fake, prev = _seed_st()
    try:
        probe.inject_model("minilm")
        # Re-inject a DIFFERENT candidate over the active injection: the old
        # wrapper is dropped first, the warm discriminating check runs against
        # the minilm vectors, and the singleton genuinely serves arctic-xs.
        state = probe.inject_model("arctic-xs")
        assert state["hf_id"] == "snowflake/snowflake-arctic-embed-xs"
        assert EmbeddingModel.get().model_name_or_path == state["hf_id"]
        assert probe.get_state()["hf_id"] == state["hf_id"]
    finally:
        _restore_st(prev)


def test_warm_process_stale_singleton_hard_fails():
    fake, prev = _seed_st(ConstantST)
    try:
        # Warm the singleton, then inject a "different" candidate whose vectors
        # are identical to the old model's — the discriminating check must HARD
        # FAIL: the singleton would serve a stale model under the new key.
        assert EmbeddingModel.get() is not None
        with pytest.raises(probe.EmbedderProbeError, match="did not genuinely swap"):
            probe.inject_model("arctic-s")
        assert probe.get_state() is None
        assert EmbeddingModel._instance is None
    finally:
        _restore_st(prev)


def test_query_prompt_threaded_to_model():
    fake, prev = _seed_st()
    try:
        state = probe.inject_model("arctic-xs", query_prompt="query")
        assert state["query_prompt"] == "query"
        model = EmbeddingModel.get()
        # Threaded as prompt_name (arctic vendor-config re-validation path).
        assert model.kwargs.get("prompt_name") == "query"
    finally:
        _restore_st(prev)


def test_resolved_revision_recorded():
    fake, prev = _seed_st()
    try:
        state = probe.inject_model("minilm")
        assert state["resolved_revision"] == FAKE_COMMIT
    finally:
        _restore_st(prev)


def test_split_pin_parses_id_and_commit():
    assert probe._split_pin("snowflake/snowflake-arctic-embed-xs@abc123") == (
        "snowflake/snowflake-arctic-embed-xs",
        "abc123",
    )
    assert probe._split_pin("sentence-transformers/all-MiniLM-L6-v2") == (
        "sentence-transformers/all-MiniLM-L6-v2",
        None,
    )


def test_pinned_revision_reaches_constructor():
    fake, prev = _seed_st()
    try:
        prev_entry = probe.PROBE_MODELS["minilm"]
        probe.PROBE_MODELS["minilm"] = (
            "sentence-transformers/all-MiniLM-L6-v2@1110a243fdf4706b"
        )
        try:
            state = probe.inject_model("minilm")
            assert state["hf_id"] == "sentence-transformers/all-MiniLM-L6-v2"
            model = EmbeddingModel.get()
            assert model.revision == "1110a243fdf4706b"
        finally:
            probe.PROBE_MODELS["minilm"] = prev_entry
    finally:
        _restore_st(prev)


def _minilm_cached() -> bool:
    """True when all-MiniLM-L6-v2 loads offline (HF cache present)."""
    try:
        from sentence_transformers import SentenceTransformer
        SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
        return True
    except Exception:
        return False


@pytest.mark.skipif(
    not _minilm_cached(),
    reason="all-MiniLM-L6-v2 not in HF cache (HF_HUB_OFFLINE in CI)",
)
def test_real_minilm_injection_routes_encode():
    """Real-model integration: injection loads actual all-MiniLM-L6-v2 (384-dim)
    and _encode routes through it with the resolved revision recorded."""
    probe.reset()
    try:
        state = probe.inject_model("minilm")
        assert state["dim"] == 384
        assert state["hf_id"] == "sentence-transformers/all-MiniLM-L6-v2"
        assert state["resolved_revision"] is not None

        model = EmbeddingModel.get()
        assert model is not None
        vecs, degraded = _encode(["tortoise is a live epistemic graph engine"])
        assert degraded is False
        assert vecs.shape == (1, 384)
        assert np.isfinite(vecs).all()
    finally:
        probe.reset()
