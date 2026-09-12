"""#2952 (B)+(C) — embedder warm-up + declared degraded (single-leg) reads.

Covers the approved (B) "explicit warm-up" + (C) "declared degraded read"
decision for the retrieval vector leg:

* (B) ``EmbeddingModel.warm_up`` / ``start_warm_up`` / ``status`` — a load
  failure is surfaced once at engine init, best-effort and non-fatal.
* (C1) ``declared_degraded_read`` — the explicit ``vector_leg_unavailable``
  marker derived from the R3 #1542 D4 ``leg_trace``.
* (C2) ``require_hybrid_read`` / ``HybridReadUnavailableError`` — the
  fail-loud capability a real-lane measurement uses to REFUSE to label a
  keyword-only read as the product's hybrid retrieval (#2985 / PR #3005).
* Hard invariant: with a HEALTHY embedder the ranking output is
  byte-identical whether or not the new (observational) plumbing is used.
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading

import numpy as np
import pytest

from tortoise.embeddings import EmbeddingModel
from tortoise.exceptions import HybridReadUnavailableError
from tortoise.sdk import TortoiseSDK
from tortoise.search_engine import (
    VECTOR_LEG_UNAVAILABLE,
    declared_degraded_read,
    require_hybrid_read,
)


def _leg(leg, ran, degraded, reason, count=0):
    return {"leg": leg, "ran": ran, "degraded": degraded,
            "reason": reason, "count": count}


#: A healthy hybrid read (the shape ``tortoise_fts_query`` + the chain emit).
HEALTHY = [
    _leg("fts", True, False, "ok", 3),
    _leg("vector", True, False, "ok", 2),
    _leg("structural", False, True, "breaker_open", 0),
]
#: The embedded extra absent — the vector strategy is never submitted.
NO_EMBEDDER = [
    _leg("vector", False, True, "no_embedder", 0),
    _leg("fts", True, False, "ok", 3),
]
#: Vector leg ran but the graph has no embeddings (no_embeddings degrade).
NO_EMBEDDINGS = [
    _leg("fts", True, False, "ok", 3),
    _leg("vector", True, True, "no_embeddings", 0),
]


class TestDeclaredDegradedRead:
    """(C1) — the marker is derived from the trace, never assumed."""

    def test_healthy_read_is_not_declared(self):
        assert declared_degraded_read(HEALTHY) is None

    def test_empty_healthy_vector_run_is_not_declared(self):
        trace = [_leg("fts", True, False, "ok", 1),
                 _leg("vector", True, False, "empty_results", 0)]
        assert declared_degraded_read(trace) is None

    def test_no_embedder_is_declared(self):
        marker = declared_degraded_read(NO_EMBEDDER)
        assert marker is not None
        assert marker[VECTOR_LEG_UNAVAILABLE] is True
        assert marker["degraded_read"] is True
        assert marker["hybrid"] is False
        assert marker["missing_legs"] == ["vector"]
        assert marker["reason"] == "no_embedder"

    def test_degraded_vector_run_is_declared(self):
        marker = declared_degraded_read(NO_EMBEDDINGS)
        assert marker is not None
        assert marker["reason"] == "no_embeddings"

    def test_absent_trace_is_not_a_declaration(self):
        # leg_trace=None is "no claim", not a declaration (a caller wanting
        # fail-closed reaches for require_hybrid_read).
        assert declared_degraded_read(None) is None

    def test_empty_trace_fails_closed(self):
        marker = declared_degraded_read([])
        assert marker is not None
        assert marker["reason"] == "leg_trace_unavailable"

    def test_structural_only_scan_is_not_declared(self):
        # query=None full scan — a single-leg read BY DESIGN, not a degraded
        # hybrid attempt.
        trace = [_leg("structural", True, False, "ok", 4)]
        assert declared_degraded_read(trace) is None

    def test_missing_vector_entry_with_fts_present_is_leg_absent(self):
        marker = declared_degraded_read([_leg("fts", True, False, "ok", 2)])
        assert marker is not None
        assert marker["reason"] == "leg_absent"

    def test_object_centric_one_healthy_entry_proves_the_leg(self):
        # recall_state(object_centric=True) appends one vector entry per
        # query (Point + Object) — one healthy entry is enough.
        trace = [_leg("vector", False, True, "no_embedder", 0),
                 _leg("vector", True, False, "ok", 1)]
        assert declared_degraded_read(trace) is None

    def test_object_centric_all_degraded_is_declared(self):
        trace = [_leg("vector", False, True, "no_embedder", 0),
                 _leg("vector", False, True, "no_embedder", 0)]
        marker = declared_degraded_read(trace)
        assert marker is not None and marker["reason"] == "no_embedder"

    def test_fallback_only_trace_is_declared(self):
        # a TF-IDF fallback is a text read whose rows are keyword-only
        trace = [_leg("fallback", True, True, "tfidf_snapshot", 2)]
        marker = declared_degraded_read(trace)
        assert marker is not None and marker["reason"] == "tfidf_fallback"

    def test_results_bearing_fallback_is_declared_despite_healthy_vector(self):
        # P1 review fix: the fallback only runs when every primary leg
        # returned zero rows, so a fallback with rows proves the read is
        # keyword-only — a healthy-but-empty vector entry must not launder it.
        trace = [
            _leg("fts", True, False, "empty_results", 0),
            _leg("vector", True, False, "empty_results", 0),
            _leg("structural", True, False, "empty_results", 0),
            _leg("fallback", True, True, "tfidf_snapshot", 3),
        ]
        marker = declared_degraded_read(trace)
        assert marker is not None and marker["reason"] == "tfidf_fallback"
        # the vector leg RAN (healthy-but-empty) — the marker must not claim
        # the embedder was unavailable
        assert marker[VECTOR_LEG_UNAVAILABLE] is False
        assert marker["missing_legs"] == []

    def test_zero_count_fallback_is_not_declared(self):
        trace = [
            _leg("fts", True, False, "ok", 1),
            _leg("vector", True, False, "ok", 1),
            _leg("fallback", True, True, "no_fallback_applicable", 0),
        ]
        assert declared_degraded_read(trace) is None

    def test_non_dict_entries_are_ignored(self):
        assert declared_degraded_read(["junk", None]) is not None

    def test_unknown_or_malformed_leg_trace_is_declared(self):
        # P2 review fix: only a genuine structural-only trace earns the
        # "by design" escape hatch — unknown/malformed legs fail closed.
        assert declared_degraded_read([_leg("newleg", True, False, "ok", 1)]) is not None
        assert declared_degraded_read([{}]) is not None


class TestRequireHybridRead:
    """(C2) — fail loud instead of mislabelling a single-leg read."""

    def test_healthy_read_passes(self):
        out = require_hybrid_read(HEALTHY)
        assert out == {"hybrid": True, VECTOR_LEG_UNAVAILABLE: False}

    def test_single_leg_read_refuses(self):
        with pytest.raises(HybridReadUnavailableError) as ei:
            require_hybrid_read(NO_EMBEDDER, lane="real_tortoise")
        exc = ei.value
        assert exc.reason == "no_embedder"
        assert exc.lane == "real_tortoise"
        assert exc.marker[VECTOR_LEG_UNAVAILABLE] is True
        assert exc.marker == declared_degraded_read(NO_EMBEDDER)
        assert "#2952" in str(exc)

    def test_missing_leg_trace_refuses_closed(self):
        with pytest.raises(HybridReadUnavailableError) as ei:
            require_hybrid_read(None)
        assert ei.value.reason == "leg_trace_unavailable"

    def test_empty_leg_trace_refuses_closed(self):
        with pytest.raises(HybridReadUnavailableError) as ei:
            require_hybrid_read([])
        assert ei.value.reason == "leg_trace_unavailable"

    def test_structural_only_trace_refuses(self):
        # P1 review fix: an absent marker must not pass a non-hybrid trace.
        with pytest.raises(HybridReadUnavailableError) as ei:
            require_hybrid_read([_leg("structural", True, False, "ok", 4)])
        assert ei.value.reason == "leg_absent"

    def test_fallback_only_trace_refuses(self):
        trace = [_leg("fallback", True, True, "tfidf_snapshot", 2)]
        with pytest.raises(HybridReadUnavailableError) as ei:
            require_hybrid_read(trace)
        assert ei.value.reason == "tfidf_fallback"

    def test_results_bearing_fallback_refuses(self):
        trace = [
            _leg("vector", True, False, "empty_results", 0),
            _leg("fallback", True, True, "tfidf_legacy", 2),
        ]
        with pytest.raises(HybridReadUnavailableError) as ei:
            require_hybrid_read(trace)
        assert ei.value.reason == "tfidf_fallback"


class _FakeInst:
    def __init__(self, model):
        self._model = model


class TestWarmUp:
    """(B) — explicit, best-effort, non-fatal engine-init warm-up."""

    @pytest.fixture(autouse=True)
    def _fresh_state(self):
        EmbeddingModel._reset()
        yield
        EmbeddingModel._reset()

    def _stub_get(self, monkeypatch, result=None, raises=None, calls=None):
        def _get(cls, load_timeout=None):
            if calls is not None:
                calls.append(load_timeout)
            if raises is not None:
                raise raises
            return result
        monkeypatch.setattr(EmbeddingModel, "get", classmethod(_get))

    def test_warm_up_true_when_model_available(self, monkeypatch):
        EmbeddingModel._reset()
        sentinel = object()
        self._stub_get(monkeypatch, result=sentinel)
        monkeypatch.setattr(EmbeddingModel, "_instance", _FakeInst(sentinel))
        assert EmbeddingModel.warm_up() is True
        st = EmbeddingModel.status()
        assert st["available"] is True
        assert st["state"] == "ready"
        assert st["last_error"] is None

    def test_warm_up_false_and_non_fatal_without_model(self, monkeypatch):
        EmbeddingModel._reset()
        self._stub_get(monkeypatch, result=None)
        assert EmbeddingModel.warm_up() is False  # never raises
        st = EmbeddingModel.status()
        assert st["available"] is False
        assert st["state"] == "unavailable"
        assert st["last_error"] == "model_unavailable"

    def test_warm_up_swallows_get_exception(self, monkeypatch):
        EmbeddingModel._reset()
        self._stub_get(monkeypatch, raises=RuntimeError("torch exploded"))
        assert EmbeddingModel.warm_up() is False
        assert "torch exploded" in (EmbeddingModel.status()["last_error"] or "")

    def test_warm_up_logs_info_for_designed_absence(self, monkeypatch, caplog):
        import logging
        self._stub_get(monkeypatch, result=None)
        monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", "not_installed")
        with caplog.at_level(logging.INFO, logger="tortoise.embeddings"):
            assert EmbeddingModel.warm_up() is False
        recs = [r for r in caplog.records if "embedder warm-up" in r.getMessage()]
        assert recs and all(r.levelno == logging.INFO for r in recs)
        assert EmbeddingModel.status()["failure_kind"] == "not_installed"

    def test_warm_up_logs_warning_for_real_failure(self, monkeypatch, caplog):
        import logging
        self._stub_get(monkeypatch, result=None)
        monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", "load_failed")
        with caplog.at_level(logging.INFO, logger="tortoise.embeddings"):
            assert EmbeddingModel.warm_up() is False
        recs = [r for r in caplog.records if "embedder warm-up" in r.getMessage()]
        assert recs and any(r.levelno == logging.WARNING for r in recs)

    def test_start_warm_up_disabled_by_env(self, monkeypatch):
        calls: list = []
        self._stub_get(monkeypatch, result=object(), calls=calls)
        monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "0")
        assert EmbeddingModel.start_warm_up() is None
        assert calls == []

    def test_start_warm_up_enabled_by_default(self, monkeypatch):
        calls: list = []
        self._stub_get(monkeypatch, result=object(), calls=calls)
        monkeypatch.delenv("TORTOISE_EMBEDDER_WARMUP", raising=False)
        thread = EmbeddingModel.start_warm_up()
        assert thread is not None
        thread.join(timeout=10)
        assert calls == [None]

    def test_start_warm_up_is_non_blocking(self, monkeypatch):
        gate = threading.Event()
        reached = threading.Event()

        def _blocking_get(cls, load_timeout=None):
            reached.set()
            gate.wait(timeout=10)
            return object()

        monkeypatch.setattr(EmbeddingModel, "get", classmethod(_blocking_get))
        monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
        thread = EmbeddingModel.start_warm_up()
        assert thread is not None and thread.is_alive()
        assert reached.wait(timeout=5)  # worker entered the (blocked) load
        assert thread.is_alive()  # start_warm_up returned before it finished
        gate.set()
        thread.join(timeout=10)
        assert not thread.is_alive()

    def test_start_warm_up_is_once_per_process(self, monkeypatch):
        calls: list = []
        self._stub_get(monkeypatch, result=object(), calls=calls)
        monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
        thread = EmbeddingModel.start_warm_up()
        assert thread is not None
        thread.join(timeout=10)
        assert calls == [None]
        assert EmbeddingModel.start_warm_up() is None  # already done
        assert calls == [None]

    def test_loader_classifies_missing_package_as_not_installed(self, monkeypatch):
        import importlib.util
        monkeypatch.setattr(importlib.util, "find_spec",
                            lambda name, package=None: None)
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        assert EmbeddingModel(load_timeout=5.0)._model is None
        assert EmbeddingModel._last_failure_kind == "not_installed"

    def test_loader_classifies_import_failure_as_load_failed(self, monkeypatch):
        import importlib.util
        monkeypatch.setattr(importlib.util, "find_spec",
                            lambda name, package=None: object())
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        assert EmbeddingModel(load_timeout=5.0)._model is None
        assert EmbeddingModel._last_failure_kind == "load_failed"

    def test_concurrent_get_during_warm_up_loads_once(self, monkeypatch):
        """P1 review fix: a first query racing the warm-up load must not start
        a SECOND load (the in-lock negative-cache re-check)."""
        entered = threading.Event()
        release = threading.Event()
        constructions: list = []

        def _slow_fail_init(self, load_timeout=None):
            constructions.append(1)
            entered.set()
            release.wait(timeout=10)
            self._model = None

        monkeypatch.setattr(EmbeddingModel, "__init__", _slow_fail_init)
        monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
        warm = EmbeddingModel.start_warm_up()
        assert warm is not None and entered.wait(timeout=5)
        # the query's OUTER negative-cache check passes (no failure recorded
        # yet), then it blocks on the load lock the warm-up holds
        query = threading.Thread(target=EmbeddingModel.get)
        query.start()
        release.set()
        warm.join(timeout=10)
        query.join(timeout=10)
        assert len(constructions) == 1
        assert EmbeddingModel.status()["state"] == "cooldown"

    def test_cooldown_is_preserved_and_expires(self, monkeypatch):
        """The 60s negative cache is preserved (option D is forbidden) and
        never becomes sticky-off: within the window a retry is
        short-circuited; past it a fresh load is attempted."""
        constructions: list = []

        def _fail_init(self, load_timeout=None):
            constructions.append(load_timeout)
            self._model = None

        monkeypatch.setattr(EmbeddingModel, "__init__", _fail_init)
        assert EmbeddingModel.warm_up() is False
        assert EmbeddingModel._last_failed_at is not None
        assert EmbeddingModel.status()["state"] == "cooldown"
        # negative cache: within the cooldown the next get() does NOT rebuild
        assert EmbeddingModel.get() is None
        assert len(constructions) == 1
        # past the cooldown the load is RETRIED (never sticky-off)
        future = EmbeddingModel._last_failed_at + EmbeddingModel._FAIL_COOLDOWN_S + 1.0
        monkeypatch.setattr("tortoise.embeddings.time.monotonic", lambda: future)
        assert EmbeddingModel.get() is None
        assert len(constructions) == 2

    def test_reset_clears_warm_up_state(self, monkeypatch):
        self._stub_get(monkeypatch, result=object())
        monkeypatch.setenv("TORTOISE_EMBEDDER_WARMUP", "1")
        thread = EmbeddingModel.start_warm_up()
        assert thread is not None
        thread.join(timeout=10)
        EmbeddingModel._reset()
        assert EmbeddingModel._warm_up_started is False
        assert EmbeddingModel._last_error is None
        assert EmbeddingModel.status()["state"] == "unavailable"


class _FakeEmbedder:
    """Deterministic 384-dim encoder — a healthy embedder with no HF load."""

    DIM = 384

    def encode(self, texts, batch_size=32, show_progress_bar=False):
        rows = []
        for text in texts:
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            raw = (digest * (self.DIM // len(digest) + 1))[:self.DIM]
            rows.append(np.frombuffer(raw, dtype=np.uint8).astype(np.float64))
        return np.asarray(rows)


@pytest.fixture
def fake_embedder(monkeypatch):
    """Pin a HEALTHY, deterministic embedder for the ranking-invariant tests."""
    embedder = _FakeEmbedder()
    monkeypatch.setattr(
        EmbeddingModel, "get",
        classmethod(lambda cls, load_timeout=None: embedder))
    EmbeddingModel._reset()
    yield embedder


def _canon(rows) -> str:
    return json.dumps(rows, sort_keys=True, default=str)


class TestHealthyRankingByteIdentical:
    """Hard invariant (#2952): healthy embedder ⇒ identical ranking output."""

    def test_leg_trace_is_observational_only(self, sdk_factory, fake_embedder):
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma", id="p1")
            sdk.create_point("statement", "delta epsilon zeta", id="p2")
            base = sdk.tortoise_fts_query(
                "alpha beta", limit=5, _elevated_timeout_ms=5000)
            trace: list[dict] = []
            traced = sdk.tortoise_fts_query(
                "alpha beta", limit=5, leg_trace=trace,
                _elevated_timeout_ms=5000)
            assert _canon(base) == _canon(traced)
            # and the healthy read is NOT declared degraded
            assert declared_degraded_read(trace) is None
            assert next(e for e in trace if e["leg"] == "vector")["ran"] is True
        finally:
            sdk.close()

    def test_explicit_warm_up_does_not_change_ranking(self, sdk_factory,
                                                      fake_embedder):
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma", id="p1")
            before = sdk.tortoise_fts_query(
                "alpha beta", limit=5, _elevated_timeout_ms=5000)
            # (B) warm-up on the SAME healthy embedder is a no-op for ranking
            assert EmbeddingModel.warm_up() is True
            after = sdk.tortoise_fts_query(
                "alpha beta", limit=5, _elevated_timeout_ms=5000)
            assert _canon(before) == _canon(after)
        finally:
            sdk.close()

    def test_recall_state_leg_trace_forwarded(self, sdk_factory, fake_embedder):
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma", id="p1")
            trace: list[dict] = []
            sdk.recall_state("alpha beta", limit=5, leg_trace=trace)
            assert any(e["leg"] == "fts" for e in trace)
            # forwarding proof: the vector-leg entry is recorded (its
            # degradation is asserted by the helper unit tests, not here —
            # the 500ms collective cap makes a healthy assertion flaky)
            assert any(e["leg"] == "vector" for e in trace)
            # default (leg_trace=None) call still works — byte-identical
            plain = sdk.recall_state("alpha beta", limit=5)
            assert isinstance(plain, list) and plain
        finally:
            sdk.close()

    def test_engine_init_triggers_warm_up(self, sdk_factory, monkeypatch):
        calls: list = []
        monkeypatch.setattr(
            EmbeddingModel, "start_warm_up",
            classmethod(lambda cls, load_timeout=None: calls.append(1)))
        monkeypatch.delenv("TORTOISE_EMBEDDER_WARMUP", raising=False)
        sdk = sdk_factory()
        try:
            sdk._get_proj()
            assert calls  # engine init invokes the (B) warm-up
        finally:
            sdk.close()


class _LegStub:
    """Minimal recall_state surface for retrieval_legs (no DB)."""

    def __init__(self, entries):
        self._entries = entries
        self.calls: list = []

    def recall_state(self, query, *, kind=None, limit=10, leg_trace=None):
        self.calls.append({"query": query, "kind": kind, "limit": limit})
        if leg_trace is not None:
            leg_trace.extend(self._entries)
        return []


class TestRetrievalLegs:
    """(C) — the SDK probe: declare the legs, optionally refuse."""

    @pytest.fixture(autouse=True)
    def _clean_embedder_state(self, monkeypatch):
        monkeypatch.setattr(EmbeddingModel, "_instance", None, raising=False)
        monkeypatch.setattr(EmbeddingModel, "_last_failed_at", None, raising=False)
        monkeypatch.setattr(EmbeddingModel, "_last_error", None, raising=False)

    def test_probe_declares_healthy_hybrid(self):
        stub = _LegStub(HEALTHY)
        out = TortoiseSDK.retrieval_legs(stub, "alpha beta", limit=3)
        assert out["hybrid"] is True
        assert out["declared_degraded_read"] is None
        assert out["hybrid_refusal_reason"] is None
        assert out["legs"] == HEALTHY
        assert stub.calls == [{"query": "alpha beta", "kind": None, "limit": 3}]
        assert "state" in out["embedder"]

    def test_probe_structural_only_is_not_hybrid(self):
        out = TortoiseSDK.retrieval_legs(
            _LegStub([_leg("structural", True, False, "ok", 4)]), "")
        # C1 declaration stays None (structural-only read by design); the
        # gate-derived hybrid flag is False and names its refusal reason.
        assert out["declared_degraded_read"] is None
        assert out["hybrid"] is False
        assert out["hybrid_refusal_reason"] == "leg_absent"

    def test_probe_declares_single_leg_read(self):
        out = TortoiseSDK.retrieval_legs(_LegStub(NO_EMBEDDER), "alpha")
        assert out["hybrid"] is False
        assert out["declared_degraded_read"]["reason"] == "no_embedder"
        assert out["hybrid_refusal_reason"] == "no_embedder"

    def test_require_hybrid_refuses_single_leg(self):
        with pytest.raises(HybridReadUnavailableError) as ei:
            TortoiseSDK.retrieval_legs(
                _LegStub(NO_EMBEDDER), "alpha", lane="real_tortoise",
                require_hybrid=True)
        assert ei.value.reason == "no_embedder"
        assert ei.value.lane == "real_tortoise"

    def test_require_hybrid_refuses_when_no_legs_reported(self):
        with pytest.raises(HybridReadUnavailableError) as ei:
            TortoiseSDK.retrieval_legs(
                _LegStub([]), "alpha", require_hybrid=True)
        assert ei.value.reason == "leg_trace_unavailable"

    def test_require_hybrid_passes_healthy(self):
        out = TortoiseSDK.retrieval_legs(
            _LegStub(HEALTHY), "alpha", require_hybrid=True)
        assert out["hybrid"] is True
