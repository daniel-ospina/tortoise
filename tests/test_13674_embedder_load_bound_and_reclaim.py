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
import logging
import os
import subprocess
import threading
import time
import weakref
from pathlib import Path
from typing import ClassVar

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


class _RecordingLock:
    """A lock wrapper that records how many times it is currently held."""

    def __init__(self, real):
        self.real = real
        self.held = 0

    def __enter__(self):
        self.real.acquire()
        self.held += 1
        return self

    def __exit__(self, *exc):
        self.held -= 1
        self.real.release()
        return False


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


def _shipped_load_bound_s() -> float:
    """`EmbeddingModel._LOAD_TIMEOUT_S` as the SHIPPED SOURCE declares it.

    ⛔ Not `EmbeddingModel._LOAD_TIMEOUT_S`: `tests/conftest.py:288` assigns
    ``270.0`` to that attribute session-wide for the test lane (#6960), so the
    runtime attribute is the LANE's override whatever the source says. Grading
    it pins nothing — a "tidy" back to 90.0 leaves it at 270.0 (review F2). The
    derivation #13674 asks for lives in the class body, so that is what is read;
    parsing the AST (not a regex) means a comment or a docstring quoting the
    number cannot satisfy the guard.
    """
    import ast

    source_path = Path(__file__).resolve().parent.parent / "tortoise" / "embeddings.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "EmbeddingModel":
            continue
        for stmt in node.body:
            targets = (stmt.targets if isinstance(stmt, ast.Assign) else [])
            if not any(isinstance(t, ast.Name) and t.id == "_LOAD_TIMEOUT_S"
                       for t in targets):
                continue
            value = stmt.value
            if (isinstance(value, ast.Constant)
                    and isinstance(value.value, (int, float))
                    and not isinstance(value.value, bool)):
                return float(value.value)
            raise AssertionError(
                "`_LOAD_TIMEOUT_S` is no longer a literal in the shipped class "
                f"body ({ast.dump(value)[:120]}) — re-state how this pin reads it")
    raise AssertionError(
        "`EmbeddingModel._LOAD_TIMEOUT_S` was not found in "
        f"{source_path} — the bound moved or was renamed")


class TestTheBoundIsDerivedFromTheMeasurement:
    """(B) — the bound must clear the measured COLD cost, with stated margin."""

    def test_the_bound_clears_the_measured_cold_cost_with_stated_margin(self):
        """Pins the DERIVATION IN THE SOURCE, not this lane's runtime override.

        Reverting `tortoise/embeddings.py` to `_LOAD_TIMEOUT_S = 90.0` must red
        THIS row: it reads the shipped literal, which the conftest override
        cannot mask. The product default is independently covered from a fresh
        interpreter by
        `tests/test_read_status.py::test_the_test_lane_never_out_budgets_the_hosted_prewarm`.
        """
        bound = _shipped_load_bound_s()
        assert pytest.approx(195.8, abs=0.05) == _MEASURED_COLD_LOAD_S, (
            "the measured cold cost is 24.1 + 132.4 + 39.3 — if a component is "
            "re-measured, restate it at the top of this file"
        )
        assert bound >= _MEASURED_COLD_LOAD_S * 1.25, (
            f"the shipped {bound}s bound cannot carry a "
            f"{_MEASURED_COLD_LOAD_S:.1f}s measured COLD load with margin (#13674)"
        )
        assert bound > _PREFIX_BOUND_S * 2, (
            f"the shipped bound is {bound}s — {_PREFIX_BOUND_S}s is the pre-fix "
            "value that made every cold load time out (#13674): the bound must "
            "be a different number, derived from the measurement"
        )
        # `EmbeddingModel._LOAD_TIMEOUT_S` is deliberately NOT what is graded
        # above: the conftest override makes it 270.0 in-lane, so it is not
        # evidence about the shipped derivation (mutation-proved by reverting the
        # source literal to 90.0 — this row reds, the attribute does not move).

    def test_the_bound_is_the_one_the_hosted_prewarm_uses(self):
        """ONE declared bound — the pre-warm must not pass a second number.

        Before #13674 the hosted pre-warm passed ``load_timeout=300.0`` beside
        a product default of 90.0, so two numbers described one load and the
        timeout log named the wrong one.
        """
        source = (
            Path(__file__).resolve().parent.parent / "tortoise" / "hosted_api.py"
        ).read_text(encoding="utf-8")
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
        # ⛔ The WRITER, not just the reader: the F3 row below sets
        # `_load_attempt_timeout_s` by hand, so deleting this assignment from
        # `__init__` left the whole file green (#13819 review of the fix). The
        # attempt must RECORD the bound it runs under, or the reclaim warning
        # silently falls back to the class default.
        assert EmbeddingModel._load_attempt_timeout_s == 0.1, (
            "the attempt did not record its own bound — the reclaim warning "
            "will quote the class default (F3)"
        )
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

    def test_the_reclaim_refusal_decides_and_logs_from_one_reading(
            self, monkeypatch, clean_embedder, caplog):
        """The guard's CONDITION and its message must be the same read.

        `get()` called `load_attempt_in_flight()` and then
        `_describe_load_attempt()`, which reads the thread a SECOND time — so a
        loader dying between the two logged "a previous load attempt is STILL
        RUNNING (elapsed unknown, bound 300s)": liveness and an unknown elapsed
        asserted in one clause, on the diagnosis an operator reads during a cold
        boot (#13819 review of the fix — F1's class on the log path).

        The fake answers in-flight ONCE and then reports a dead thread; the call
        count pins that the guard asked only once.
        """
        reads = {"n": 0}

        def _once_then_dead(cls):
            reads["n"] += 1
            if reads["n"] == 1:
                return (True, 42.0, 300.0)
            return (False, None, 300.0)

        monkeypatch.setattr(EmbeddingModel, "load_attempt_snapshot",
                            classmethod(_once_then_dead))
        monkeypatch.setattr(EmbeddingModel, "_instance", None)
        monkeypatch.setattr(EmbeddingModel, "_last_failed_at", None)

        with caplog.at_level("WARNING", logger=embeddings_mod.logger.name):
            assert EmbeddingModel.get() is None

        lines = [r.getMessage() for r in caplog.records
                 if "STILL RUNNING" in r.getMessage()]
        assert lines, f"the reclaim guard never refused: {caplog.text!r}"
        assert "elapsed 42.0s" in lines[-1], (
            f"the guard decided on one reading and logged another: {lines[-1]!r}"
        )
        assert "elapsed unknown" not in lines[-1]
        assert reads["n"] == 1, (
            f"the guard took {reads['n']} readings of the attempt thread — its "
            "condition and its message can disagree"
        )

    @pytest.mark.skipif(not os.path.exists("/proc/self/status"),
                        reason="needs /proc for the RSS measurement")
    def test_an_abandoned_attempt_releases_its_model(self, monkeypatch,
                                                     clean_embedder, caplog):
        """The abandon-release BRANCH runs, and the model it built is not retained.

        ⛔ WHAT THIS TEST PROVES, STATED EXACTLY (#13674 review F3). The
        post-join facts it used to lean on — the weakref is dead, RSS settles —
        are true of the MUTANT as well: `result` is captured by the `_load`
        closure, that closure is the Thread's `_target`, and CPython's
        `_bootstrap_inner` calls `Thread._delete()` (setting `_target = None`)
        when the body returns. So post-join, nothing holds the sentinel in
        EITHER version, and those assertions cannot attribute anything to the
        release branch.

        What IS attributable is that the branch RAN: only the abandoned path
        logs "released rather than retained", so deleting the block at
        `embeddings.py`'s abort-release (or making it always publish) leaves that
        line unlogged and reds this test. The memory assertions are kept for the
        OTHER claim they do carry — the peak was real (the allocation happened
        and is resident) and RSS returns to baseline — not as proof of the drop.
        """
        payload_mb = 120
        frame = _Frame()
        _patch_loader(monkeypatch, frame, mb=payload_mb, hold_s=1.0)
        caplog.set_level(logging.INFO, logger="tortoise.embeddings")

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
            f"({baseline_mb:.0f} MB -> {peak_mb:.0f} MB): the allocation this "
            "test reasons about never happened"
        )

        thread = EmbeddingModel._load_attempt_thread
        assert thread is not None
        thread.join(timeout=60)
        assert not thread.is_alive(), "the fake loader never finished"

        gc.collect()
        # ── THE ATTRIBUTABLE ROW ──────────────────────────────────────────
        releases = [r for r in caplog.records
                    if "released rather than retained" in r.getMessage()]
        assert releases, (
            "an abandoned attempt finished WITHOUT taking the release branch — "
            "it published the model it built into a result dict nobody will "
            "read instead of dropping it (#13674). This is the only assertion "
            "here that the pre-fix/mutant code cannot satisfy."
        )
        assert any("OVERRUN" in r.getMessage() for r in releases)
        # ── The allocation was real and is gone (true either way; kept for
        # the memory claim, not as proof of the branch above).
        assert frame.ref() is None, "the abandoned attempt retained the model"
        settled_mb = _rss_mb()
        assert settled_mb < baseline_mb + payload_mb * 0.6, (
            f"RSS stayed at {settled_mb:.0f} MB after an abandoned attempt "
            f"(baseline {baseline_mb:.0f} MB): the model was not reclaimed"
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

    def test_a_first_load_reads_unavailable_while_it_is_still_in_flight(
            self, monkeypatch, clean_embedder):
        """THE OPERATOR CONTRACT THIS PIN EXISTS FOR (#13674 review round 4, F1).

        `state` has three values and is derived from `_instance`/`_model` alone,
        and `_instance` is assigned only AFTER the constructor returns — so the
        FIRST attempt of a process, which is the production cold-boot load this
        issue is about, reads `unavailable` with `failure_kind: null` while it
        is loading. That tuple does NOT mean "no attempt has run": the
        discriminator is `load_in_progress`. The runbook's table says exactly
        that, and this row is what makes it un-revertable — the in-flight row
        above samples the ABANDONED state, where `state` is already `cooldown`,
        so nothing pinned the live first-attempt window before this.
        """
        frame = _Frame()
        _patch_loader(monkeypatch, frame, mb=0, hold_s=1.5)

        # A caller that is still WAITING for the load: generous bound, own
        # thread, so the attempt is in flight while this test samples `status()`
        # (a short bound would abandon it and land in `cooldown` instead).
        waiter = threading.Thread(
            target=lambda: EmbeddingModel.get(load_timeout=30.0), daemon=True)
        waiter.start()
        try:
            deadline = time.monotonic() + 15.0
            while frame.calls < 1 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert frame.calls == 1, "the fake loader never started"

            status = EmbeddingModel.status()
            assert status["load_in_progress"] is True
            assert status["available"] is False
            assert status["failure_kind"] is None
            assert status["state"] == "unavailable", (
                f"a load IN FLIGHT reads state={status['state']!r}; the runbook "
                "tells the operator to read `unavailable` + `load_in_progress: "
                "true` as IN FLIGHT, and a fourth state would contradict it"
            )
            assert status["load_in_progress_s"] is not None
        finally:
            waiter.join(timeout=60)
        assert not waiter.is_alive(), "the stubbed load never finished"
        assert EmbeddingModel.status()["state"] == "ready"

    def test_status_reads_the_attempt_thread_once_not_twice(
            self, monkeypatch, clean_embedder):
        """ONE snapshot, so the pair cannot tear (#13819 review F1).

        The bug: `status()` read the thread once for `load_in_progress` and
        then AGAIN (through a helper) for `load_in_progress_s`. A loader that
        dies between the two reads published
        `load_in_progress: true` beside `load_in_progress_s: null` — a pair no
        state of the machine produces, on the surface #13674 exists to make
        readable.

        The discriminator is a thread whose `is_alive()` answers True then
        False: with a second read the two fields disagree, and the call count
        pins that only ONE read happened.
        """

        class _DiesOnSecondRead:
            def __init__(self):
                self.reads = 0

            def is_alive(self):
                self.reads += 1
                return self.reads == 1

            def join(self, timeout=None):  # the fixture's teardown joins it
                return None

        attempt = _DiesOnSecondRead()
        monkeypatch.setattr(EmbeddingModel, "_load_attempt_thread", attempt)
        monkeypatch.setattr(EmbeddingModel, "_load_started_at",
                            time.monotonic() - 12.0)

        status = EmbeddingModel.status()

        assert attempt.reads == 1, (
            f"status() read the attempt thread {attempt.reads} times — the two "
            "published fields can disagree, which is the torn pair this fixes"
        )
        assert status["load_in_progress"] is True
        assert status["load_in_progress_s"] is not None, (
            "`load_in_progress: true` beside `load_in_progress_s: null` — the "
            "torn pair is back (#13819 review F1)"
        )
        assert 11.0 < status["load_in_progress_s"] < 60.0

    def test_the_reclaim_warning_names_the_attempts_own_bound(
            self, monkeypatch, clean_embedder):
        """The refusal quotes the bound the ATTEMPT runs under (#13819 F3).

        `_describe_load_attempt` interpolated `cls._LOAD_TIMEOUT_S`, so an
        attempt a caller had bounded at 7 s was described as \"of its 300s
        bound\" — the same class-default misdirection the timeout log had
        (pinned above by `test_the_timeout_log_names_the_bound_that_fired`),
        in the other warning an operator reads during a cold boot. Latent on
        the shipped path (the lifespan pre-warm takes the default), wrong the
        moment a caller passes `load_timeout`.
        """

        class _Alive:
            def is_alive(self):
                return True

            def join(self, timeout=None):  # the fixture's teardown joins it
                return None

        monkeypatch.setattr(EmbeddingModel, "_load_attempt_thread", _Alive())
        monkeypatch.setattr(EmbeddingModel, "_load_started_at",
                            time.monotonic() - 3.0)
        monkeypatch.setattr(EmbeddingModel, "_load_attempt_timeout_s", 7.0)

        line = EmbeddingModel._describe_load_attempt()
        assert "7s bound" in line, (
            f"the reclaim warning names a bound the attempt did not use: {line!r}"
        )
        assert f"{EmbeddingModel._LOAD_TIMEOUT_S:.0f}s" not in line, (
            f"the pre-fix defect is back — the line names the CLASS default: "
            f"{line!r}"
        )

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
    HEALTHY_TRACE: ClassVar[list[dict]] = [
        {"leg": "fts", "ran": True, "degraded": False, "reason": "ok", "count": 3},
        {"leg": "vector", "ran": True, "degraded": False, "reason": "ok",
         "count": 2},
        {"leg": "structural", "ran": False, "degraded": True,
         "reason": "breaker_open", "count": 0},
    ]
    NO_EMBEDDER_TRACE: ClassVar[list[dict]] = [
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


# ─────────────────────────────────────────────────────────────────────────────
# (D) #13674 review F5 — the reclaim guard must not leak across `_reset()`
# ─────────────────────────────────────────────────────────────────────────────


class TestTheReclaimGuardDoesNotLeakAcrossResets:
    """`_reset()` must drop a FINISHED attempt's reference, and keep a live one.

    The guard reads `_load_attempt_thread`. Leaving a finished thread recorded
    is what F5 caught: `tests/test_cross_lens.py::test_failed_load_cooldown`
    abandons a loader and resets, and every later `get()` in that worker is then
    refused — `_require_model()` reads that `None` as "model unavailable" and
    SKIPS, so the three real-embedder tests below it silently stopped running.
    """

    def test_a_finished_attempt_does_not_survive_a_reset(self):
        EmbeddingModel._reset()
        finished = threading.Thread(target=lambda: None)
        finished.start()
        finished.join(30)
        assert not finished.is_alive()

        EmbeddingModel._load_attempt_thread = finished
        EmbeddingModel._reset()

        assert EmbeddingModel._load_attempt_thread is None, (
            "a FINISHED attempt's thread outlived `_reset()` — the reclaim guard "
            "reads that attribute, so the stale reference makes the next get() "
            "read as 'a previous attempt is still running' (#13674 F5)"
        )
        assert EmbeddingModel.load_attempt_in_flight() is False

    def test_the_attempt_clear_happens_under_the_publish_lock(
            self, monkeypatch, clean_embedder):
        """The clear must contend on the lock the PUBLISHER holds (#13841).

        `get()` publishes the attempt record while holding `cls._lock` (it holds
        it across the `cls(load_timeout=…)` call, i.e. across `__init__`'s
        write). Clearing outside that lock left a window in which a reset could
        read a published-but-not-yet-started thread (`is_alive()` False), drop
        the record, and leave a LIVE loader unreferenced: the reclaim guard
        disarmed, and `load_attempt_snapshot()` publishing
        `load_in_progress: true` beside `load_in_progress_s: null`.

        Nothing pinned it — re-indenting the block out of `with cls._lock:` left
        every row green. This row records whether the lock was HELD at the
        moment `is_alive()` is consulted.
        """
        proxy = _RecordingLock(EmbeddingModel._lock)
        seen = {}

        class _DeadStub:
            def is_alive(self):
                seen["held"] = proxy.held
                return False

        monkeypatch.setattr(EmbeddingModel, "_lock", proxy)
        monkeypatch.setattr(EmbeddingModel, "_load_attempt_thread", _DeadStub())

        EmbeddingModel._reset()

        assert seen.get("held") == 1, (
            "the attempt record was cleared with `_lock` NOT held — a reset can "
            "race the publisher (`get()` holds it across `__init__`'s write) and "
            "leave a live loader unreferenced, disarming the reclaim guard "
            f"(held={seen.get('held')!r})"
        )

    def test_a_reset_drops_the_dead_attempts_bound_too(
            self, monkeypatch, clean_embedder):
        """The attempt's own bound must not outlive the attempt (#13819 review).

        `_reset()` clears the record of a DEAD attempt; the bound recorded with
        that attempt is part of the record, or a later snapshot quotes a stale
        override that no longer applies. Nothing pinned the line, so deleting it
        left the whole file green — measured, not assumed.
        """
        monkeypatch.setattr(EmbeddingModel, "_load_attempt_thread", None)
        monkeypatch.setattr(EmbeddingModel, "_load_attempt_timeout_s", 7.0)

        EmbeddingModel._reset()

        _in_flight, elapsed, bound = EmbeddingModel.load_attempt_snapshot()
        assert (_in_flight, elapsed) == (False, None)
        assert bound == EmbeddingModel._LOAD_TIMEOUT_S, (
            f"a dead attempt's bound ({bound!r}s) outlived `_reset()` — the next "
            f"diagnosis would quote it as the bound in force"
        )

    def test_a_live_attempt_survives_a_reset(self):
        """The mirror of the assertion above: a live importer is not reset away.

        Clearing a LIVE thread's reference would hide an abandoned importer from
        the guard that refuses a second attempt — the exact allocation the fix
        exists to prevent.
        """
        release = threading.Event()
        live = threading.Thread(target=release.wait, daemon=True)
        live.start()
        try:
            deadline = time.monotonic() + 15.0
            while not live.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert live.is_alive(), "the stub thread never started"

            EmbeddingModel._reset()
            EmbeddingModel._load_attempt_thread = live
            EmbeddingModel._reset()

            assert EmbeddingModel._load_attempt_thread is live, (
                "`_reset()` dropped a LIVE attempt — an abandoned importer would "
                "then be invisible to the guard, and a retry would allocate a "
                "second copy (#13674)"
            )
            assert EmbeddingModel.load_attempt_in_flight() is True
        finally:
            release.set()
            live.join(30)
            EmbeddingModel._load_attempt_thread = None
            EmbeddingModel._reset()


# ─────────────────────────────────────────────────────────────────────────────
# (E) #13674 review F4 — the BOOT pre-warm (`entrypoint.sh::_prewarm_embedder`)
# ─────────────────────────────────────────────────────────────────────────────
#
# 155 added lines, the PR's biggest behavioural change, and before this class
# NOTHING in the suite referenced `_prewarm_embedder` at all. The shell is
# EXECUTED (lifted by brace matching, the way `test_boot_regressions.py` does it)
# with `timeout` and `python` stubbed, so these are hermetic and never import
# torch.

ENTRYPOINT = Path(__file__).resolve().parent.parent / "entrypoint.sh"

#: Stand-in for GNU `timeout`. Records the duration it was handed (which is what
#: the bound VALIDATION produces) and then runs the command unbounded. Argument
#: shape: `--signal=TERM --kill-after=15 <duration> <cmd ...>`.
_FAKE_TIMEOUT = """#!/bin/bash
while [ $# -gt 0 ]; do
  case "$1" in
    --signal=*|--kill-after=*) shift ;;
    *) break ;;
  esac
done
printf '%s' "$1" > "$FAKE_BOUND_FILE"
shift
exec "$@"
"""

#: Stand-in for the heredoc child: consume stdin (as `python -` would read the
#: heredoc), emit the line a real run prints, then exit with the configured rc.
_FAKE_PYTHON = """#!/bin/bash
cat > /dev/null
printf 'PREWARM {"model": "fake", "total_s": 0.0}\\n'
exit "${FAKE_PY_RC:-0}"
"""


def _prewarm_body() -> str:
    from tests.test_boot_regressions import _extract_shell_function

    return _extract_shell_function("_prewarm_embedder", ENTRYPOINT.read_text())


def _run_prewarm(tmp_path, env: dict | None = None, *, py_rc: str = "0"):
    """Execute the SHIPPED `_prewarm_embedder` under `set -euo pipefail`.

    Returns `(CompletedProcess, bound_or_None)` where `bound` is the duration the
    function actually handed `timeout` — i.e. the OUTPUT of its validation, not
    the env value it was given. `bound is None` means `timeout` was never
    invoked (the skip path).
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    for name, body in (("timeout", _FAKE_TIMEOUT), ("python", _FAKE_PYTHON)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)

    bound_file = tmp_path / "bound.txt"
    script = ("set -euo pipefail\n"
              + _prewarm_body()
              + '\n_prewarm_embedder\necho "RC=$?"\n')

    full_env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "FAKE_BOUND_FILE": str(bound_file),
        "FAKE_PY_RC": py_rc,
    }
    full_env.update(env or {})

    proc = subprocess.run(["bash", "-c", script], env=full_env,
                          capture_output=True, text=True, timeout=180)
    bound = bound_file.read_text() if bound_file.exists() else None
    return proc, bound


class TestTheBootPrewarm:
    """The boot pre-warm's switch, its bound validation, and its non-fatal contract."""

    @pytest.mark.parametrize("value", ["0", "off", "FALSE", "  No  "])
    def test_the_switch_disables_it_without_touching_the_bound(
            self, tmp_path, value):
        """`TORTOISE_EMBEDDER_WARMUP` honours `env_flag`'s falsy set.

        This is the switch production actually ran on while the embedder could
        not load, and the shell re-implements `env_flag`'s {0,false,no,off}
        comparison by hand — so it is pinned against the spellings an operator
        really types, including case and surrounding whitespace.
        """
        proc, bound = _run_prewarm(tmp_path,
                                   {"TORTOISE_EMBEDDER_WARMUP": value})
        assert proc.returncode == 0, proc.stderr
        assert "RC=0" in proc.stdout
        assert "SKIPPED" in proc.stdout, (
            f"TORTOISE_EMBEDDER_WARMUP={value!r} did not skip the pre-warm: "
            f"{proc.stdout!r}{proc.stderr!r}"
        )
        assert bound is None, (
            f"the pre-warm invoked timeout with {bound!r}s despite being "
            "disabled — it would have paid the cold import anyway"
        )

    @pytest.mark.parametrize("value,expected", [
        (None, "300"),   # unset -> the documented default
        ("45", "45"),    # a usable value passes through unchanged
        ("300", "300"),
    ])
    def test_the_bound_defaults_and_parses(self, tmp_path, value, expected):
        env = ({} if value is None
               else {"TORTOISE_BOOT_EMBEDDER_WARMUP_TIMEOUT_S": value})
        proc, bound = _run_prewarm(tmp_path, env)
        assert proc.returncode == 0, proc.stderr
        assert bound == expected, (
            f"TORTOISE_BOOT_EMBEDDER_WARMUP_TIMEOUT_S={value!r} produced a bound "
            f"of {bound!r}s, not {expected!r}s"
        )

    @pytest.mark.parametrize("value", ["0", "00", "000"])
    def test_zero_is_rejected_because_timeout_reads_it_as_no_bound(
            self, tmp_path, value):
        """#13674 review F6 — `timeout 0` means NO TIMEOUT, not "no bound".

        Measured on GNU coreutils: `timeout --kill-after=15 0 sleep 2` returns 0
        after the full 2 s. So the one value an operator types to mean "no
        bound" would DELETE the boot bound, and an unbounded boot overruns
        `grace_period` — the restart loop the function's own comment forbids.
        """
        proc, bound = _run_prewarm(
            tmp_path, {"TORTOISE_BOOT_EMBEDDER_WARMUP_TIMEOUT_S": value})
        assert proc.returncode == 0, proc.stderr
        assert bound == "300", (
            f"TORTOISE_BOOT_EMBEDDER_WARMUP_TIMEOUT_S={value!r} reached "
            f"timeout(1) as {bound!r} — timeout reads that as NO BOUND, so the "
            "boot became unbounded and would overrun grace_period (#13674 F6)"
        )
        assert "NO TIMEOUT" in proc.stderr, (
            "a 'no bound' value must be reported, not silently replaced: "
            f"{proc.stderr!r}"
        )

    @pytest.mark.parametrize("value", ["abc", "12.5", "-5", "9000"])
    def test_an_unusable_bound_falls_back_to_the_default(self, tmp_path, value):
        """Non-numeric AND over-ceiling values are replaced by 300, loudly."""
        proc, bound = _run_prewarm(
            tmp_path, {"TORTOISE_BOOT_EMBEDDER_WARMUP_TIMEOUT_S": value})
        assert proc.returncode == 0, proc.stderr
        assert bound == "300", (
            f"TORTOISE_BOOT_EMBEDDER_WARMUP_TIMEOUT_S={value!r} produced "
            f"{bound!r}s — an unusable value must fall back to 300"
        )
        assert proc.stderr.strip(), (
            f"the fallback for {value!r} was silent"
        )

    @pytest.mark.parametrize("py_rc,needle", [
        ("0", "pre-warm OK"),
        ("124", "hit its"),
        ("137", "hit its"),
        ("127", "could not run"),
        ("1", "FAILED"),
    ])
    def test_the_prewarm_never_kills_the_boot(self, tmp_path, py_rc, needle):
        """Every child outcome maps to a message and rc 0 (#545's contract).

        The failure #545 records was a pre-warm that exited 1 on a cold box, so
        `entrypoint.sh` died and uvicorn never started. This asserts the shipped
        function cannot do that under `set -euo pipefail`, for each arm of its
        `case`.
        """
        proc, bound = _run_prewarm(tmp_path, {}, py_rc=py_rc)
        assert bound == "300", "the pre-warm never ran"
        assert proc.returncode == 0, (
            f"the pre-warm's rc {py_rc} killed the entrypoint (shell rc "
            f"{proc.returncode}) — a pre-warm failure must never be fatal"
        )
        assert "RC=0" in proc.stdout, (
            f"`_prewarm_embedder` did not return 0 for rc {py_rc}: "
            f"{proc.stdout!r}{proc.stderr!r}"
        )
        combined = proc.stdout + proc.stderr
        assert needle in combined, (
            f"the rc={py_rc} arm did not report {needle!r}: {combined!r}"
        )

    def test_the_skip_message_does_not_claim_the_request_path_stops_loading(
            self, tmp_path):
        """#13674 review F3 — the switch gates WARM-UP; `get()` is NOT gated.

        `TORTOISE_EMBEDDER_WARMUP` gates exactly `start_warm_up`, `warm_up`, the
        write-path warm-up and the hosted lifespan pre-warm. `EmbeddingModel.get()`
        — the request path — is not gated, and `tortoise/mcp_auth.py` says so
        verbatim: "``TORTOISE_EMBEDDER_WARMUP`` does NOT gate this path, and that
        is the point". The pre-fix skip message claimed "the in-process load is
        disabled too, so there is nothing this would make cheap" — false on both
        halves, and it steered the operator at the very switch this PR hands
        back (`TORTOISE_EMBEDDER_WARMUP=0` on the deployed app is the parent's to
        flip): with the flag off the first request that needs an embedding still
        pays the cold load, which is exactly the cost the boot pre-warm removes.
        """
        proc, _bound = _run_prewarm(
            tmp_path, {"TORTOISE_EMBEDDER_WARMUP": "0"})
        assert proc.returncode == 0, proc.stderr
        msg = proc.stdout + proc.stderr
        assert "in-process load is disabled" not in msg, (
            "the skip arm claims the in-process LOAD is disabled — the switch "
            "gates warm-up only, `get()` still loads the model (#13674 review "
            f"F3): {msg!r}"
        )
        assert "request path" in msg and "cold" in msg, (
            "the skip arm must say the request path still loads the model, COLD, "
            "on first use — otherwise an operator reads a free trade where the "
            f"cost is only moved (#13674 review F3): {msg!r}"
        )

    def test_the_timeout_message_does_not_advertise_the_prewarm_bound_as_the_apps(
            self, tmp_path):
        """#13674 review F3 — `$_bound` bounds the PRE-WARM, not the app's load.

        The app applies `EmbeddingModel._LOAD_TIMEOUT_S`; the two numbers
        coincide only at the default. With `TORTOISE_BOOT_EMBEDDER_WARMUP_
        TIMEOUT_S=45` the pre-fix message advertised a 45 s APP bound that the
        app never applies.
        """
        proc, bound = _run_prewarm(
            tmp_path, {"TORTOISE_BOOT_EMBEDDER_WARMUP_TIMEOUT_S": "45"},
            py_rc="124")
        assert bound == "45", "the pre-warm never ran with the operator's bound"
        combined = proc.stdout + proc.stderr
        app_lines = [line for line in combined.splitlines() if "lazily" in line]
        assert app_lines, (
            "the timeout arm no longer tells the operator what the app does "
            f"next: {combined!r}"
        )
        assert all("45" not in line for line in app_lines), (
            "the timeout arm names the PRE-WARM's bound as the app's bound — the "
            f"app applies its own (#13674 review F3): {app_lines!r}"
        )

    def test_the_call_sits_before_the_topology_sentinel(self):
        """The call must precede `#1677`'s block, or gate tests run a real load.

        `tests/test_worker_topology.py` LIFTS AND EXECUTES everything between
        that sentinel and `exec "$@"`, so a call inside that slice would run a
        real embedder load in every gate test.
        """
        from tests.test_worker_topology import _BLOCK_SENTINEL

        source = ENTRYPOINT.read_text()
        call_site = source.index("\n    _prewarm_embedder\n")
        sentinel = source.index(_BLOCK_SENTINEL)
        assert call_site < sentinel, (
            "the pre-warm call moved BELOW the #1677 sentinel — the worker-gate "
            "tests lift and execute that block, so they would run a REAL "
            "embedder load (196 s) on every gate test"
        )


#: Every key `/health`'s `embedder` block must carry, on the healthy AND the
#: degraded arm. Written here (not imported from the app) so a rename in
#: `hosted_api` reds a test instead of silently moving the operator's key.
_EXPECTED_EMBEDDER_HEALTH_KEYS = {
    "model", "available", "state", "last_error", "failure_kind",
    "cooldown_remaining_s", "threads", "load_timeout_s", "load_elapsed_s",
    "load_in_progress", "load_in_progress_s",
}


def _health_body(monkeypatch) -> dict:
    """`GET /health`'s payload, with everything it reads besides the embedder
    neutralised (the `test_9548_record_arming_health.py` pattern): the DB verdict
    is a snapshot from a background refresher, so a cold probe would answer for
    the wrong reason and say nothing about this change.
    """
    import asyncio

    import tortoise.hosted_api as hosted_api

    verdict = {"ok": True, "latency_ms": 0.1, "error": None}
    health_probe = getattr(hosted_api, "_HEALTH_PROBE", None)
    if health_probe is not None:
        monkeypatch.setattr(health_probe, "snapshot", lambda: dict(verdict),
                            raising=False)
    monkeypatch.setattr(hosted_api, "_WATCHER", None, raising=False)
    monkeypatch.setattr(hosted_api, "_WATCHER_START_ERROR", None, raising=False)
    monkeypatch.setattr(hosted_api, "_WATCHER_EXPECTED", False, raising=False)
    return asyncio.run(hosted_api.health())


class TestTheBoundIsReadableFromTheProduct:
    """#13674 (B): an instrument with no reader is not an observable bound.

    `status()` was the whole of the "make the bound OBSERVABLE" requirement, and
    nothing in `tortoise/` read it — the product could be 290 s into a 300 s
    bound, or permanently on `cooldown`, with every published surface saying
    "ok" and no datum saying otherwise. These rows pin the READ, so a revert that
    drops the `/health` block fails here rather than in the next outage.
    """

    def test_status_publishes_how_far_into_the_bound_the_attempt_is(
            self, monkeypatch, clean_embedder):
        """`load_in_progress` is a BOOL; the operator's question is "how far in".

        A 4 s attempt and a 290 s attempt of the same 300 s bound render
        identically under a bool, and they call for opposite decisions.
        """
        frame = _Frame()
        _patch_loader(monkeypatch, frame, mb=0, hold_s=0.8)

        assert EmbeddingModel.get(load_timeout=0.05) is None
        assert EmbeddingModel.status()["load_in_progress"] is True

        first = EmbeddingModel.status()["load_in_progress_s"]
        assert first is not None, (
            "an attempt IS in flight but `load_in_progress_s` is None — the "
            "bound is not observable WHILE it runs (#13674)"
        )
        assert 0.0 <= first < EmbeddingModel._LOAD_TIMEOUT_S
        time.sleep(0.2)
        second = EmbeddingModel.status()["load_in_progress_s"]
        assert second > first, (
            f"the in-flight elapsed time did not advance ({first} -> {second}): "
            "it is reading a frozen timestamp, not the running attempt"
        )

    def test_a_finished_attempt_publishes_no_in_progress_time(
            self, monkeypatch, clean_embedder):
        """`_load_started_at` OUTLIVES the attempt — so it must not be trusted.

        Reading the timestamp alone would report a "load in progress" that grows
        forever after a successful load, which is the mirror defect: a surface
        that is always alarming is as unreadable as one that is always green.
        """
        frame = _Frame()
        _patch_loader(monkeypatch, frame, mb=0)

        assert EmbeddingModel.get() is not None
        status = EmbeddingModel.status()
        assert status["load_in_progress"] is False
        assert status["load_in_progress_s"] is None, (
            "a FINISHED load still reports an in-progress elapsed time"
        )

    def test_health_carries_the_embedder_block_with_the_declared_bound(
            self, monkeypatch, clean_embedder):
        """The block must be on the SURFACE, not only on the class.

        This is the row that would have caught the actual state of main: a
        complete, tested `status()` dict that no route published.
        """
        frame = _Frame()
        _patch_loader(monkeypatch, frame, mb=0)
        assert EmbeddingModel.get() is not None

        body = _health_body(monkeypatch)
        assert "embedder" in body, (
            "/health carries no `embedder` block — the load bound this P1 "
            f"raised is unreadable from outside the process: {sorted(body)}"
        )
        block = body["embedder"]
        assert set(block) == _EXPECTED_EMBEDDER_HEALTH_KEYS
        # ⛔ THE PRODUCER SIDE (#13819 review): `_embedder_health()` builds the
        # block as `{key: status.get(key) for key in _EMBEDDER_HEALTH_KEYS}`, so
        # the assertion above compares the app's own tuple with itself and
        # cannot notice a rename in `status()` — `/health` would publish a
        # permanently `null` key (e.g. the documented `threads`) while every row
        # here stayed green.
        assert set(EmbeddingModel.status()) == _EXPECTED_EMBEDDER_HEALTH_KEYS, (
            "`status()` and the published key set have drifted — the operator's "
            "`jq .embedder.<key>` now resolves to null"
        )
        assert block["load_timeout_s"] == EmbeddingModel._LOAD_TIMEOUT_S
        assert block["available"] is True
        assert block["state"] == "ready"
        assert block["model"] == embeddings_mod.EMBEDDING_MODEL

    def test_an_unavailable_embedder_is_reported_without_folding_into_status(
            self, monkeypatch, clean_embedder):
        """The state that mattered in production: the dense leg is OFF.

        Before this block, "hybrid retrieval is off" was invisible from every
        surface the product publishes; `state: "unavailable"` plus a
        `failure_kind` is the operator's cue. The 'must NOT 5xx' half of that
        contract is measured by `test_the_unavailable_response_is_a_200_not_a_5xx`
        — THIS row calls `health()` and reads the dict, so it cannot fail on a
        status code, and its name must not claim otherwise (#13819 review).
        """
        monkeypatch.setattr(EmbeddingModel, "_last_failed_at", time.monotonic())
        monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", "load_timeout")

        body = _health_body(monkeypatch)
        block = body["embedder"]
        assert block["available"] is False
        assert block["state"] == "cooldown"
        assert block["failure_kind"] == "load_timeout"
        assert block["cooldown_remaining_s"] > 0.0
        assert block["load_timeout_s"] == EmbeddingModel._LOAD_TIMEOUT_S
        # ── THE `status` ROW, ON THE STATE THAT CAN DISCRIMINATE IT (#13674
        # review F2). "Reported, NEVER folded into `status`" is only pinned by
        # a row where the two verdicts DIFFER: every other term is `ok` here
        # (the DB snapshot and the watcher are stubbed, the record is unarmed
        # — its documented, `ok` default), so the fold-into-`status` mutation
        # turns this into "degraded" and reds the line. Asserted next to the
        # degraded block rather than in the ready row, where both values would
        # have been "ok" and the assertion could never fail (#10185/#10620 is
        # the rule: one failed load must not pin the deployment degraded).
        assert body["status"] == "ok", (
            "an embedder on cooldown folded into `/health`'s `status` — a "
            "keyword-only deployment is a DECLARED degraded read (#2952), not "
            "a dying process, and one failed load must not pin it degraded "
            "for the life of the process (#10185/#10620)"
        )

    def test_the_degraded_arm_publishes_the_same_keys(self, monkeypatch):
        """An unreadable `status()` must not shorten the block (#12992/#12482).

        A dashboard doing `jq .embedder.load_timeout_s` on the arm an operator
        reaches during an incident is the exact path a short dict 500s.
        """
        import tortoise.hosted_api as hosted_api

        def _boom():
            raise RuntimeError("embeddings module half-written")

        monkeypatch.setattr(EmbeddingModel, "status", classmethod(lambda cls: _boom()))
        block = hosted_api._embedder_health()
        assert set(block) == _EXPECTED_EMBEDDER_HEALTH_KEYS | {"error"}
        assert "half-written" in block["error"]
        assert block["available"] is None

    def test_the_unavailable_response_is_a_200_not_a_5xx(
            self, monkeypatch, clean_embedder):
        """THE ROW ABOVE NEVER MEASURED A STATUS CODE (#13819 review F2).

        `_health_body` calls `health()` directly and inspects the dict, so the
        test named "does not 5xx" could not fail for the reason it names: the
        route could raise past the handler (a sibling arm, a middleware, a
        dependency) and the dict assertions would still pass on whatever
        `health()` returned.

        This row mounts the REAL handler on a bare app and reads the STATUS
        through the ASGI stack, on the state that mattered in production (the
        dense leg OFF). `verdict_client` is the repo's client seam, so a
        wait-bound refusal arrives as the contract's skip rather than a
        load-dependent failure.
        """
        from fastapi import FastAPI

        import tortoise.hosted_api as hosted_api
        from tests._verdict import verdict_client

        monkeypatch.setattr(EmbeddingModel, "_last_failed_at", time.monotonic())
        monkeypatch.setattr(EmbeddingModel, "_last_failure_kind", "load_timeout")
        # Apply the same neutralisation `_health_body` does (the DB snapshot and
        # the watcher), so the only variable left is the degraded embedder.
        _health_body(monkeypatch)

        probe = FastAPI()
        probe.get("/health")(hosted_api.health)
        with verdict_client(probe) as client:
            response = client.get("/health")

        assert response.status_code == 200, (
            "a DEGRADED embedder took /health off 200 — that fails the deploy "
            f"gate over a declared degraded read (#2952): {response.status_code} "
            f"{response.text[:200]}"
        )
        block = response.json()["embedder"]
        assert block["state"] == "cooldown"
        assert block["available"] is False
        assert response.json()["status"] == "ok", (
            "the degraded read must not fold into /health's status "
            f"(#10185/#10620): {response.json()['status']!r}"
        )
