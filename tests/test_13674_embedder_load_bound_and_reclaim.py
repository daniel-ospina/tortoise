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
