"""#5275 — ``TORTOISE_EMBEDDING_MODEL_REQUIRED`` must survive the READ path.

#4861 shipped the flag and the ``EmbedderUnavailableError`` that carries it, and
``tests/test_embedding_model_required.py`` pins the raise at
``EmbeddingModel.get``. The guarantee was still defeated one layer up:
``TortoiseSDK.tortoise_fts_query`` — the entry point every read surface routes
through (``mcp_server``, ``assembly``, ``ask_lane``, ``extractor_v2``) — resolved
its query vector inside a blanket ``except Exception`` that converted the named
refusal into ``model = None`` → ``_vec_reason = "no_embedder"``. An ARMED process
therefore received keyword-only (FTS) results with **no error and no signal**,
and the ``failure_kind`` attribution the exception exists to carry was recorded
as the *designed-absence* reason — the conflation the adjacent R3 (#1542) D4
comment forbids.

This module is the removal test for that swallow. The load-bearing cases are
:func:`test_required_read_path_raises_not_fts_only` (delete the
``except EmbedderUnavailableError: raise`` clause in ``tortoise/sdk.py`` and the
read returns FTS rows instead of raising) and
:func:`test_required_fallback_tfidf_propagates` (the same restoration in
``tortoise/search_engine.py::fallback_tfidf`` makes an ARMED outage an empty
result again).

The default is pinned here too (flag off ⇒ the historical keyword-only degrade),
so the fix cannot turn the product's always-degrade read into an always-raise
read.
"""
from __future__ import annotations

import time

import pytest

from tortoise.embeddings import EmbeddingModel
from tortoise.exceptions import EmbedderUnavailableError
from tortoise.search_engine import fallback_tfidf

REQUIRED_ENV = "TORTOISE_EMBEDDING_MODEL_REQUIRED"


def _arm_unavailable(monkeypatch, kind: str = "not_installed",
                     err: str = "sentence-transformers is not installed") -> None:
    """Put the singleton in the post-failure negative-cache state.

    This is the state a runner-down or missing-extra process is actually in: a
    load already failed and ``get()`` is inside the cooldown window. No model is
    loaded, so the path is reachable deterministically (mirrors
    ``tests/test_embedding_model_required.py::_arm_negative_cache``; kept local
    so this module carries no cross-test import).
    """
    monkeypatch.setattr(EmbeddingModel, "_instance", None)
    monkeypatch.setattr(EmbeddingModel, "_last_failed_at", time.monotonic())
    monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", kind)
    monkeypatch.setattr(EmbeddingModel, "_last_error", err)


# --------------------------------------------------------------------------
# The defect — the read path must RAISE, not return FTS-only rows.
# --------------------------------------------------------------------------

def test_required_read_path_raises_not_fts_only(sdk_factory, monkeypatch):
    """REQUIRED + embedder unavailable ⇒ ``tortoise_fts_query`` raises.

    #4861 AC #5, the test the issue asks for by name. Before the fix the call
    returned a list (FTS-only) because ``except Exception`` swallowed the
    refusal. It must raise ``EmbedderUnavailableError`` instead — carrying the
    real ``failure_kind``, never ``no_embedder``.
    """
    sdk = sdk_factory()
    try:
        monkeypatch.setenv(REQUIRED_ENV, "1")
        _arm_unavailable(monkeypatch, kind="load_failed", err="torch OOM")
        with pytest.raises(EmbedderUnavailableError) as ei:
            # A plain text query classifies to the vector strategy, so the read
            # path actually asks for the embedder (classify_query → vector=True).
            sdk.tortoise_fts_query("alpha beta gamma", limit=3)
        assert ei.value.failure_kind == "load_failed"
        assert ei.value.context  # attributable without reading code
    finally:
        sdk.close()


def test_required_read_path_refuses_with_opt_in_surfaces(sdk_factory, monkeypatch):
    """The raise survives BOTH opt-in read surfaces (trace + status sink).

    A refused read must not fabricate a ``no_embedder`` entry: that term names
    the *designed* absence, and this refusal is the opposite of it.
    """
    sdk = sdk_factory()
    try:
        monkeypatch.setenv(REQUIRED_ENV, "1")
        _arm_unavailable(monkeypatch, kind="load_timeout")
        trace: list[dict] = []
        out: dict = {}
        with pytest.raises(EmbedderUnavailableError):
            sdk.tortoise_fts_query(
                "alpha beta gamma", limit=3, leg_trace=trace,
                read_status_out=out)
        assert not [e for e in trace if e.get("reason") == "no_embedder"]
    finally:
        sdk.close()


# --------------------------------------------------------------------------
# The last-resort read tier — same class, same contract.
# --------------------------------------------------------------------------

def test_required_fallback_tfidf_propagates(monkeypatch):
    """``fallback_tfidf`` must not answer an ARMED outage with ``[]``.

    Before the fix the named refusal was logged and dropped, so a REQUIRED
    outage became an empty search result — the quietest possible outcome, and
    indistinguishable from "nothing matched".
    """
    monkeypatch.setenv(REQUIRED_ENV, "1")
    _arm_unavailable(monkeypatch, kind="not_installed")
    points = [{"id": "p1", "content": "alpha beta gamma",
               "pointKind": "statement"}]
    with pytest.raises(EmbedderUnavailableError):
        fallback_tfidf("alpha", points, limit=5)


# --------------------------------------------------------------------------
# The default is UNCHANGED — the fix cannot make every read fail.
# --------------------------------------------------------------------------

def test_unset_read_path_still_degrades_to_keyword_only(sdk_factory, monkeypatch):
    """Flag OFF + embedder unavailable ⇒ the historical degrade, not a raise.

    ``EmbeddingModel.get`` returns ``None`` when the flag is unset, and the read
    path records ``no_embedder`` exactly as before — the product's search must
    never depend on embeddings by default.
    """
    monkeypatch.delenv(REQUIRED_ENV, raising=False)
    sdk = sdk_factory()
    try:
        _arm_unavailable(monkeypatch, kind="not_installed")
        trace: list[dict] = []
        rows = sdk.tortoise_fts_query("alpha beta gamma", limit=3, leg_trace=trace)
        assert isinstance(rows, list)  # no raise
        vec = next(e for e in trace if e["leg"] == "vector")
        assert vec["ran"] is False and vec["degraded"] is True
        assert vec["reason"] == "no_embedder"
    finally:
        sdk.close()


def test_unset_fallback_tfidf_still_returns_rows(monkeypatch):
    """The fallback tier's own degrade is intact when the flag is off."""
    monkeypatch.delenv(REQUIRED_ENV, raising=False)
    _arm_unavailable(monkeypatch, kind="load_failed")
    points = [{"id": "p1", "content": "alpha beta gamma",
               "pointKind": "statement"}]
    assert isinstance(fallback_tfidf("alpha", points, limit=5), list)


# --------------------------------------------------------------------------
# Attribution — designed absence vs a broken load are now separable.
# --------------------------------------------------------------------------

class TestAbsentReason:
    """``EmbeddingModel.absent_reason`` — the trace term for a ``None``."""

    def test_no_recorded_failure_is_designed_absence(self, monkeypatch):
        monkeypatch.setattr(EmbeddingModel, "_last_failed_at", None)
        monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", None)
        assert EmbeddingModel.absent_reason() == "no_embedder"

    def test_not_installed_keeps_the_established_term(self, monkeypatch):
        monkeypatch.setattr(EmbeddingModel, "_last_failed_at", time.monotonic())
        monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", "not_installed")
        assert EmbeddingModel.absent_reason() == "no_embedder"

    @pytest.mark.parametrize("kind", ["load_failed", "load_timeout"])
    def test_broken_load_is_attributed(self, monkeypatch, kind):
        """A present-but-broken embedder is NOT the designed absence (#5275)."""
        monkeypatch.setattr(EmbeddingModel, "_last_failed_at", time.monotonic())
        monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", kind)
        assert EmbeddingModel.absent_reason() == kind
