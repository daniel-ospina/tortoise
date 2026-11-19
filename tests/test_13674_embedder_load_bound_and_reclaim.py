"""#13674 — the embedder's load BOUND, and RECLAIM before retrying.

MEASURED 2026-11-19 on the production box (`tortoise-y4mjjq`, cpu_kind=shared,
2 cpus, 4096 MB):

    import torch ............................................ 24.1 s
    from sentence_transformers import SentenceTransformer ... 132.4 s
    imports alone, before one weight is read ................ 156.5 s
    full construct, WARM page cache .......................... 39.3 s

Three things follow, and this file pins each:

1. **The bound must exceed the measured COLD cost** (156.5 + 39.3 = 195.8 s).
   The pre-fix value was 90.0 s — 2.2x below it — so every cold load timed out.
   `test_the_bound_clears_the_measured_cold_cost_with_stated_margin` pins the
   derivation, so a future tidy back toward 90 fails here.

2. **A failed attempt must be RECLAIMED before a retry allocates.** The 3.66 GB
   that got the hosted process OOM-killed was NOT the model (432 MB measured) —
   it was CONCURRENT attempts: the join timed out, `get()` cleared `_instance`,
   the cooldown expired, and a second loader started while the first was still
   importing. Two tests pin the repair by MECHANISM, not by reading the code:
   `..._never_starts_a_second_attempt` (no second allocation) and
   `..._releases_its_model` (the abandoned attempt drops the model it built, so
   RSS returns to baseline and a weakref to it is dead).

3. **The bound and the elapsed load are OBSERVABLE**, so a slow load is visible
   before it becomes a timeout (`status()` fields), and the timeout log names the
   bound that ACTUALLY fired — it used to interpolate the class default, so a
   300 s join reported "exceeded 90.0s" (that misdirection is
   `test_the_timeout_log_names_the_bound_that_fired`).

Nothing here loads the real model: `import_sentence_transformer` is replaced at
the module seam the loader itself calls, so these are fast and hermetic.
"""
from __future__ import annotations

import gc
import hashlib
import os
import time
import weakref

import numpy as np
import pytest

import tortoise.embeddings as embeddings_mod
from tortoise.embeddings import EmbeddingModel
from tortoise.search_engine import (
    RETRIEVAL_HYBRID,
    RETRIEVAL_KEYWORD_ONLY,
    RETRIEVAL_NOT_APPLICABLE,
    RETRIEVAL_UNDECLARED,
    declared_retrieval_kind,
)

#: The measurement, in one place. Provenance: #13674's research comment, taken
#: on the production box with the revision the image bakes.
_MEASURED_TORCH_IMPORT_S = 24.1
_MEASURED_ST_IMPORT_S = 132.4
_MEASURED_WARM_CONSTRUCT_S = 39.3
_MEASURED_COLD_LOAD_S = (_MEASURED_TORCH_IMPORT_S + _MEASURED_ST_IMPORT_S
                         + _MEASURED_WARM_CONSTRUCT_S)

#: The pre-fix bound, kept as a NAMED value so "must clear the measurement"
#: cannot be satisfied by drifting back to it.
_PREFIX_BOUND_S = 90.0


def _rss_mb() -> float:
    """This process's resident set, in MiB (Linux; the only lane that measures)."""
    with open("/proc/self/status", encoding="ascii") as fh:
        for line in fh:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024.0
    raise AssertionError("no VmRSS in /proc/self/status")


def _touch(buf: bytearray) -> bytearray:
    """Force the pages resident — a lazily-mapped allocation would prove nothing.

    ``bytearray(n)`` is already zero-filled in CPython, but writing every 4 KiB
    page makes the measurement independent of allocator policy.
    """
    for offset in range(0, len(buf), 4096):
        buf[offset] = 1
    return buf


class _Sentinel:
    """A stand-in for "the model was built": big, identifiable, collectable."""

    def __init__(self, mb: int):
        self.buf = _touch(bytearray(mb * 1024 * 1024))
        self.mb = mb


class _Frame:
    """Mutable box so the fake factory can hand state to the test."""

    def __init__(self):
        self.calls = 0
        self.ref: weakref.ref | None = None
        self.built_at: list[float] = []


def _patch_loader(monkeypatch, frame: _Frame, *, mb: int,
                  hold_s: float = 0.0) -> None:
    """Install a fake ``SentenceTransformer`` at the loader's OWN seam.

    Two patches, both at seams the loader itself calls:

    * ``import_sentence_transformer`` — the model construct. ``mb`` is the
      simulated model size: it is ALLOCATED FIRST, so the memory is resident
      before the join can time out (a lazily-built sentinel would prove
      nothing about reclaim), and it is what the factory RETURNS, so the loader
      holds the only strong reference.
    * ``pin_embed_threads`` — a no-op here, so the test does not pay a real
      torch import and the timing assertions are about the bound, not the box.

    ``hold_s`` keeps the loader alive past the join, which is the ABANDONED
    state under test.
    """

    monkeypatch.setattr(embeddings_mod, "pin_embed_threads", lambda: None)

    def _fake_factory():
        def _build(model, revision=None):
            frame.calls += 1
            built = _Sentinel(mb)
            frame.ref = weakref.ref(built)
            frame.built_at.append(time.monotonic())
            if hold_s:
                time.sleep(hold_s)
            return built

        return _build

    monkeypatch.setattr(
        embeddings_mod, "import_sentence_transformer", _fake_factory)


@pytest.fixture
def clean_embedder(monkeypatch):
    """Reset the class state around every test, and join any stub loader thread."""
    EmbeddingModel._reset()
    EmbeddingModel._load_attempt_thread = None
    yield
    thread = EmbeddingModel._load_attempt_thread
    if thread is not None and thread.is_alive():
        thread.join(timeout=30)
    EmbeddingModel._reset()
    EmbeddingModel._load_attempt_thread = None


class TestTheBoundIsDerivedFromTheMeasurement:
    """(B) — the bound must clear the measured COLD cost, with stated margin."""

    def test_the_bound_clears_the_measured_cold_cost_with_stated_margin(self):
        bound = EmbeddingModel._LOAD_TIMEOUT_S
        assert _MEASURED_COLD_LOAD_S == pytest.approx(195.8, abs=0.05), (
            "the measured cold cost is 24.1 + 132.4 + 39.3 — if a component is "
            "re-measured, restate it at the top of this file"
        )
        assert bound >= _MEASURED_COLD_LOAD_S * 1.25, (
            f"a {bound}s bound cannot carry a {_MEASURED_COLD_LOAD_S:.1f}s "
            "measured COLD load with margin (#13674)"
        )
        assert bound > _PREFIX_BOUND_S * 2, (
            f"{_PREFIX_BOUND_S}s is the pre-fix value that made every cold load "
            "time out (#13674) — the bound must be a different number, derived "
            "from the measurement"
        )

    def test_the_bound_is_the_one_the_hosted_prewarm_uses(self):
        """ONE declared bound — the pre-warm must not pass a second number.

        Before #13674 the hosted pre-warm passed ``load_timeout=300.0`` beside
        a product default of 90.0, so two numbers described one load and the
        timeout log named the wrong one.
        """
        source = open(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "tortoise", "hosted_api.py"), encoding="utf-8").read()
        assert "EmbeddingModel.get(load_timeout=300.0)" not in source, (
            "the hosted pre-warm re-introduced a second declared bound; it must "
            "take the product default so there is ONE number (#13674)"
        )


class TestReclaimBeforeRetry:
    """(C) — the OOM mechanism, proved by mechanism rather than by reading code."""

    def test_a_retry_never_starts_a_second_attempt(self, monkeypatch, clean_embedder):
        """While an abandoned attempt is alive, `get()` must NOT allocate again.

        This is the whole defect in one assertion: the pre-fix path cleared
        `_instance` on timeout and, after the cooldown, constructed a SECOND
        `EmbeddingModel` — a second torch + a second model beside the abandoned
        importer. Measured model RSS is 432 MB; three such attempts is the
        3.66 GB kill.
        """
        frame = _Frame()
        _patch_loader(monkeypatch, frame, mb=8, hold_s=2.0)

        assert EmbeddingModel.get(load_timeout=0.1) is None
        assert EmbeddingModel._last_failure_kind == "load_timeout"
        assert EmbeddingModel.load_attempt_in_flight() is True, (
            "the abandoned attempt must still be tracked, or the guard below "
            "has nothing to refuse"
        )
        # Wait for the abandoned attempt to have actually STARTED building, so
        # "no second attempt" is a statement about a live loader and not about a
        # race the test happened to win.
        deadline = time.monotonic() + 15.0
        while frame.calls < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert frame.calls == 1, "the fake loader never started"
        assert EmbeddingModel.load_attempt_in_flight() is True

        # Simulate the cooldown EXPIRING while the abandoned attempt is still
        # running — the exact state the pre-fix code turned into a second load.
        EmbeddingModel._last_failed_at = time.monotonic() - 3600.0

        assert EmbeddingModel.get(load_timeout=0.1) is None
        assert frame.calls == 1, (
            "a second load attempt was constructed while the first was still "
            "running — concurrent attempts are the 3.66 GB, not the 432 MB "
            "model (#13674)"
        )
        thread = EmbeddingModel._load_attempt_thread
        assert thread is not None and thread.is_alive()

    @pytest.mark.skipif(not os.path.exists("/proc/self/status"),
                        reason="needs /proc for the RSS measurement")
    def test_an_abandoned_attempt_releases_its_model(self, monkeypatch,
                                                     clean_embedder):
        """A model built by an ABANDONED attempt is released, not retained.

        Proved twice over, because either alone is arguable: the weakref to the
        built object is DEAD after the abandoned thread exits (nothing retains
        it), and process RSS returns to its pre-attempt level while it peaked
        above it during the attempt (the allocation was real, and it is gone).
        """
        payload_mb = 120
        frame = _Frame()
        _patch_loader(monkeypatch, frame, mb=payload_mb, hold_s=1.0)

        baseline_mb = _rss_mb()
        # A bound far below the simulated construct: the join abandons the
        # loader while the sentinel is already resident. Poll for residency so
        # the peak assertion is about memory, not about a scheduling race.
        assert EmbeddingModel.get(load_timeout=0.05) is None
        deadline = time.monotonic() + 15.0
        while frame.ref is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert frame.ref is not None, "the fake factory never built anything"
        peak_mb = _rss_mb()
        assert peak_mb > baseline_mb + payload_mb * 0.6, (
            f"the simulated model was not resident during the attempt "
            f"({baseline_mb:.0f} MB -> {peak_mb:.0f} MB): the reclaim assertion "
            "below would pass vacuously"
        )
        assert frame.ref is not None, "the fake factory never built anything"

        thread = EmbeddingModel._load_attempt_thread
        assert thread is not None
        thread.join(timeout=60)
        assert not thread.is_alive(), "the fake loader never finished"

        gc.collect()
        assert frame.ref() is None, (
            "the abandoned attempt RETAINED the model it built — that retained "
            "copy is what turns a missed bound into an OOM (#13674)"
        )
        settled_mb = _rss_mb()
        assert settled_mb < baseline_mb + payload_mb * 0.6, (
            f"RSS stayed at {settled_mb:.0f} MB after an abandoned attempt "
            f"(baseline {baseline_mb:.0f} MB): the dead model was not reclaimed"
        )
        assert EmbeddingModel.load_attempt_in_flight() is False


class TestTheBoundAndElapsedAreObservable:
    """(B) — a slow load must be visible BEFORE it becomes a timeout."""

    def test_status_publishes_the_bound_the_elapsed_and_the_attempt(
            self, monkeypatch, clean_embedder):
        frame = _Frame()
        _patch_loader(monkeypatch, frame, mb=0)

        EmbeddingModel.get()

        status = EmbeddingModel.status()
        assert status["load_timeout_s"] == EmbeddingModel._LOAD_TIMEOUT_S
        assert status["available"] is True
        assert status["load_in_progress"] is False
        assert status["load_elapsed_s"] is not None
        assert status["load_elapsed_s"] >= 0.0
        assert status["load_elapsed_s"] < EmbeddingModel._LOAD_TIMEOUT_S

    def test_status_reports_an_attempt_in_flight(self, monkeypatch, clean_embedder):
        frame = _Frame()
        _patch_loader(monkeypatch, frame, mb=0, hold_s=0.6)

        assert EmbeddingModel.get(load_timeout=0.05) is None
        assert EmbeddingModel.status()["load_in_progress"] is True

    def test_the_timeout_log_names_the_bound_that_fired(self, monkeypatch,
                                                       clean_embedder, caplog):
        """The log used to name the CLASS default whatever the join used.

        Production logged ``load exceeded 90.0s`` for a 300 s join, which sent
        the investigation at the wrong number (#13674).
        """
        frame = _Frame()
        _patch_loader(monkeypatch, frame, mb=0, hold_s=0.6)

        with caplog.at_level("WARNING", logger=embeddings_mod.logger.name):
            assert EmbeddingModel.get(load_timeout=0.05) is None

        timeout_lines = [r.getMessage() for r in caplog.records
                         if "load exceeded" in r.getMessage()]
        assert timeout_lines, f"no timeout line was logged: {caplog.text!r}"
        message = timeout_lines[-1]
        assert "0.1s" in message or "0.05s" in message, (
            f"the timeout line must name the bound that FIRED, not the class "
            f"default: {message!r}"
        )
        assert "90.0s" not in message, (
            f"the pre-fix defect is back — the line names a bound it did not "
            f"use: {message!r}"
        )


class TestTheQueryDeclaresItsLeg:
    """The POSITIVE indicator — the mirror of #2952.

    #2952 says a keyword-only read must never be PRESENTED as hybrid. Its mirror
    is that a read must DECLARE the leg it actually used, so "hybrid" is a claim
    rather than an inference from the absence of a degrade marker.
    """

    #: The shape the read path emits for a healthy dense read (#2952's HEALTHY).
    HEALTHY_TRACE = [
        {"leg": "fts", "ran": True, "degraded": False, "reason": "ok", "count": 3},
        {"leg": "vector", "ran": True, "degraded": False, "reason": "ok",
         "count": 2},
        {"leg": "structural", "ran": False, "degraded": True,
         "reason": "breaker_open", "count": 0},
    ]
    NO_EMBEDDER_TRACE = [
        {"leg": "vector", "ran": False, "degraded": True,
         "reason": "no_embedder", "count": 0},
        {"leg": "fts", "ran": True, "degraded": False, "reason": "ok", "count": 3},
    ]

    def test_a_healthy_dense_leg_declares_hybrid(self):
        assert declared_retrieval_kind(self.HEALTHY_TRACE) == RETRIEVAL_HYBRID

    def test_a_healthy_but_empty_dense_run_still_declares_hybrid(self):
        trace = [{"leg": "fts", "ran": True, "degraded": False, "reason": "ok",
                  "count": 1},
                 {"leg": "vector", "ran": True, "degraded": False,
                  "reason": "empty_results", "count": 0}]
        assert declared_retrieval_kind(trace) == RETRIEVAL_HYBRID

    def test_a_missing_embedder_declares_keyword_only(self):
        assert (declared_retrieval_kind(self.NO_EMBEDDER_TRACE)
                == RETRIEVAL_KEYWORD_ONLY)

    def test_a_structural_only_read_is_not_applicable(self):
        trace = [{"leg": "structural", "ran": True, "degraded": False,
                  "reason": "ok", "count": 1}]
        assert declared_retrieval_kind(trace) == RETRIEVAL_NOT_APPLICABLE

    def test_no_trace_declares_nothing(self):
        assert declared_retrieval_kind(None) == RETRIEVAL_UNDECLARED

    def test_the_declaration_cannot_disagree_with_the_fail_loud_gate(self):
        """Same predicates, same order — the two must never contradict.

        `require_hybrid_read` refuses on a keyword-only trace; the declaration
        must therefore never say "hybrid" for a trace the gate refuses (a
        second, hand-written classifier is how #4199's laundering returns).
        """
        from tortoise.exceptions import HybridReadUnavailableError
        from tortoise.search_engine import require_hybrid_read

        for trace in (self.HEALTHY_TRACE, self.NO_EMBEDDER_TRACE,
                      self.HEALTHY_TRACE + self.NO_EMBEDDER_TRACE):
            try:
                require_hybrid_read(trace)
                gate_says_hybrid = True
            except HybridReadUnavailableError:
                gate_says_hybrid = False
            declared = declared_retrieval_kind(trace)
            if gate_says_hybrid:
                assert declared == RETRIEVAL_HYBRID, (
                    f"the gate certifies hybrid while the declaration says "
                    f"{declared!r}: {trace}"
                )
            else:
                assert declared != RETRIEVAL_HYBRID, (
                    f"the gate refuses this trace as keyword-only while the "
                    f"declaration calls it hybrid: {trace}"
                )


class _FakeEncoder:
    """Deterministic 384-dim encoder — a healthy embedder with no HF load."""

    DIM = 384

    def encode(self, texts, batch_size=32, show_progress_bar=False):
        rows = []
        for text in texts:
            digest = hashlib.sha256(str(text).encode("utf-8")).digest()
            raw = (digest * (self.DIM // len(digest) + 1))[:self.DIM]
            rows.append(np.frombuffer(raw, dtype=np.uint8).astype(np.float64))
        return np.asarray(rows)


class TestTheDeclarationReachesTheQueryPath:
    """The declaration must be on the READ, not only in a pure function."""

    def test_a_loaded_embedder_makes_a_query_declare_hybrid(
            self, sdk_factory, monkeypatch, clean_embedder):
        encoder = _FakeEncoder()
        monkeypatch.setattr(
            EmbeddingModel, "get",
            classmethod(lambda cls, load_timeout=None: encoder))
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            out: dict = {}
            sdk.tortoise_fts_query("alpha beta gamma", read_status_out=out,
                                   limit=5)
            assert out.get("retrieval") == RETRIEVAL_HYBRID, (
                "a query with a loaded embedder must DECLARE the dense leg it "
                f"used (#13674 / #2952's mirror); got {out!r}"
            )
        finally:
            sdk.close()

    def test_no_embedder_makes_a_query_declare_keyword_only(
            self, sdk_factory, monkeypatch, clean_embedder):
        monkeypatch.setattr(
            EmbeddingModel, "get",
            classmethod(lambda cls, load_timeout=None: None))
        sdk = sdk_factory()
        try:
            sdk.create_point("statement", "alpha beta gamma waves")
            out: dict = {}
            sdk.tortoise_fts_query("alpha beta gamma", read_status_out=out,
                                   limit=5)
            assert out.get("retrieval") == RETRIEVAL_KEYWORD_ONLY, (
                f"a keyword-only read must not leave the leg to be inferred; "
                f"got {out!r}"
            )
        finally:
            sdk.close()
