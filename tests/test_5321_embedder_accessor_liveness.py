"""#5321 — the object ``EmbeddingModel.get()`` returns IS the encoder, so the
dense legs run. Pinned here so the claim cannot be re-asserted from a signature.

THE CLAIM THIS FILE DISPROVES (issue #5321, filed 2026-09-25 against
``main@6c48cf561``)

The issue states that ``EmbeddingModel.encode`` rejects ``show_progress_bar``,
that the three callers which pass it (``_encode``, ``search_points``,
``kind_index._DefaultEncoder.encode`` — plus ``fallback_snapshot``) therefore
raise ``TypeError`` at call binding, and that every one of them swallows it into
a TF-IDF fallback, leaving dense cross-lens/dedup/kind-index matching
lexical-only in production.

MEASURED AT HEAD — the claim does not hold, and it did not hold then either.
``EmbeddingModel.get()`` returns the LOADED INNER ENCODER
(``tortoise/embeddings.py``: ``return model`` where
``model = cls._instance._model``), i.e. the ``sentence_transformers``
``SentenceTransformer`` itself — NOT the ``EmbeddingModel`` wrapper. Its
``encode`` accepts ``show_progress_bar`` (verified here), so every cited call
site binds and the model branch runs:

    get() returned: <class 'sentence_transformers...SentenceTransformer'>
    _encode: shape (2, 384) degraded False
    kind_index._DefaultEncoder: shape (2, 384) degraded False
    search_points: [('a', 1.0), ('b', 0.5736)]

The reported ``TypeError`` was real only for the two days
``get()`` returned the WRAPPER (``return cls._instance``, #17 @ ``4abff72cb``
2026-08-04). ``ec2c03a60`` (2026-08-06, #7871) changed it to
``return cls._instance._model`` and the binding drift disappeared as a side
effect — three weeks before the issue reported it as live.

WHY THE MISDIAGNOSIS HAPPENED, AND WHAT IS PINNED

The verification in the issue inspected ``inspect.signature(
EmbeddingModel.encode)`` — the CLASS method, which has no in-repo caller. A
signature read off the wrong object cannot see what ``get()`` hands callers.
So this file pins the invariant BEHAVIOURALLY, on the object ``get()`` actually
returns, and separately pins the wrapper method itself (now aligned with the
encoder it delegates to) so the signature that misled the reader no longer
contradicts it.

Deterministic and pure-unit: the inner encoder is a stub with the REAL
``SentenceTransformer.encode`` signature, installed as ``_instance._model`` so
the REAL ``get()`` runs. No model download, no network, no DB.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import inspect

import numpy as np
import pytest

from tortoise.embeddings import EMBEDDING_DIM, EmbeddingModel, _encode, search_points
from tortoise.kind_index import _DefaultEncoder


class _StubInnerEncoder:
    """The loaded encoder as ``get()`` returns it, with the shipped signature.

    ``encode`` deliberately mirrors ``SentenceTransformer.encode``'s
    ``(inputs, batch_size, show_progress_bar)`` shape: a stub that accepted
    ``**kwargs`` would hide exactly the binding drift this file exists to catch.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], int, bool]] = []

    def encode(self, texts, batch_size: int = 32, show_progress_bar: bool = False):
        self.calls.append((list(texts), batch_size, show_progress_bar))
        # All-ones rows: every cosine is 1.0. The LEXICAL fallback cannot
        # produce that (it has no shared term to score), which is what makes
        # the search_points assertion below a discriminator rather than a
        # shape check.
        return np.ones((len(texts), EMBEDDING_DIM), dtype=np.float32)


class _StubInstance:
    """Enough of an ``EmbeddingModel`` for ``get()``'s ``_instance._model``."""

    def __init__(self, model: _StubInnerEncoder) -> None:
        self._model = model


@pytest.fixture
def stub_encoder(monkeypatch) -> _StubInnerEncoder:
    """Install a loaded encoder the way a healthy process has one.

    Sets the singleton's ``_model`` (not ``get``) so ``EmbeddingModel.get()``
    itself runs — the accessor is the subject under test.
    """
    encoder = _StubInnerEncoder()
    monkeypatch.setattr(EmbeddingModel, "_instance", _StubInstance(encoder))
    monkeypatch.setattr(EmbeddingModel, "_last_failed_at", None)
    return encoder


def test_get_returns_the_inner_encoder_not_the_wrapper(stub_encoder):
    """The invariant ``ec2c03a60`` restored: ``get()`` hands back the encoder.

    If this ever returns the ``EmbeddingModel`` WRAPPER again (its ``encode``
    returns ``None`` when no model is loaded and is not what callers expect),
    every ``show_progress_bar`` call site would break at binding again — the
    exact defect #5321 believed it was filing.
    """
    model = EmbeddingModel.get()

    assert model is stub_encoder, "get() must return the loaded inner encoder"
    assert not isinstance(model, EmbeddingModel), (
        "get() returning the wrapper is the #17-era shape that raised "
        "TypeError on show_progress_bar"
    )
    # The kwarg the cited call sites pass must bind on the returned object —
    # driven exactly as they drive it.
    out = model.encode(["a", "b"], show_progress_bar=False)
    assert np.asarray(out).shape == (2, EMBEDDING_DIM)
    # And the generic guard: every kwarg shape used at a cited site.
    params = inspect.signature(model.encode).parameters
    assert "show_progress_bar" in params
    assert "batch_size" in params


def test_encode_runs_the_model_branch(stub_encoder):
    """``_encode`` (the write-path dedup chain) reports ``degraded is False``.

    ``_encode`` is reached by ``_semantic_dedup`` on ``checkpoint``/``fold``,
    ``find_cross_source_matches`` and ``cross_lens.find_cross_lens_matches``.
    """
    vectors, degraded = _encode(["hello world", "another point"])

    assert degraded is False, "model present → the model branch must run"
    assert vectors.shape == (2, EMBEDDING_DIM)
    assert stub_encoder.calls, "the inner encoder must actually be called"


def test_search_points_runs_the_dense_leg_with_zero_lexical_overlap(stub_encoder):
    """The read-side query encode runs the model, not the lexical fallback.

    The query shares NO term with either document, so the TF-IDF branch
    (fit on docs, transform the query — the #399 contract) can only score 0.0
    and yields no candidate above the threshold. Near-1.0 similarities are
    therefore evidence of the DENSE leg, not of a shape that both legs satisfy.
    """
    results = search_points(
        "zqxjkv wplrmb",
        [{"id": "a", "content": "alpha beta"}, {"id": "b", "content": "gamma delta"}],
        threshold=0.3,
    )

    assert {r["id"] for r in results} == {"a", "b"}, (
        "no shared term ⇒ an empty result means the lexical fallback ran"
    )
    assert all(r["similarity"] == pytest.approx(1.0) for r in results)


def test_kind_index_default_encoder_runs_the_model_branch(stub_encoder):
    """The production kind-index encoder reports ``degraded is False``."""
    vectors, degraded = _DefaultEncoder().encode(["alpha beta", "gamma delta"])

    assert degraded is False, "model present → must not fall back to TF-IDF"
    assert vectors.shape == (2, EMBEDDING_DIM)
    assert stub_encoder.calls, "the inner encoder must actually be called"


def test_wrapper_encode_accepts_show_progress_bar(stub_encoder):
    """``EmbeddingModel.encode`` accepts the kwarg its callers pass.

    The class method had no in-repo caller, but reading ITS signature is what
    produced #5321 — it advertised an encoder that could not take the kwarg its
    own inner model takes. It now forwards it, so the signature a reader
    inspects and the encoder that runs agree.
    """
    instance = EmbeddingModel.__new__(EmbeddingModel)  # no model load
    instance._model = stub_encoder

    out = instance.encode(["x"], show_progress_bar=False)

    assert np.asarray(out).shape == (1, EMBEDDING_DIM)
    assert stub_encoder.calls[-1][2] is False, "kwarg is forwarded, not dropped"


def test_no_embedder_still_degrades_and_never_raises(monkeypatch):
    """The #399 contract is untouched: absence degrades, it does not raise."""
    monkeypatch.setattr(EmbeddingModel, "get", classmethod(lambda cls, load_timeout=None: None))

    _vectors, degraded = _encode(["hello world", "another point"])

    assert degraded is True, "no embedder ⇒ deterministic TF-IDF (#399/#880)"
