"""Ingest stall guard — bound a stalled FalkorDB ingest (#2969).

The v2 ingest lane collapsed from ~25 min/question to >4 h/question on the
dedicated ``falkordb-eval`` container, with the client at 0% CPU blocked in a
FalkorDB socket read: no exception, no progress, no timeout. The stall is
SERVER-side (FalkorDB active-defrag loop / MERGE lock), so nothing on the
client could distinguish "stalled" from "slow".

Two complementary bounds, both wired into the eval's EXISTING retry channel
(``retryable_transient`` / ``ingest:retries_exhausted`` / ``--retry-failed``)
rather than a new error vocabulary:

1. **Socket read bound (hard).** The graph client is constructed with a
   bounded ``socket_timeout`` / ``socket_connect_timeout`` — see
   ``tortoise/projection/__init__.py`` (``TORTOISE_DB_SOCKET_TIMEOUT`` /
   ``TORTOISE_DB_SOCKET_CONNECT_TIMEOUT``; the eval lane raises the read
   bound to ``DEFAULT_EVAL_SOCKET_TIMEOUT_S``). A stalled read therefore
   raises ``redis.exceptions.TimeoutError``, which ``retryable_transient``
   already classifies as a retryable transient — it reaches the write-stage
   retry loop (R1) and, if exhausted, the ``ingest:retries_exhausted``
   failure entry.

2. **No-progress budget (grind).** A single read is not the only stall
   shape: the measured symptom was a ~8.6 s/point GRIND that never blocks
   long enough to trip a socket timeout but never finishes either. The
   :class:`Heartbeat` records a liveness timestamp at every ingest
   stage/session boundary and :meth:`Heartbeat.check` raises
   :class:`IngestStallTimeout` when no progress happened for longer than the
   budget — aborting the QUESTION instead of grinding for hours.

Why a boundary check and not a watchdog thread: a thread blocked in
``recv()`` cannot be interrupted from another thread, so the only reliable
hard bound is the socket timeout itself. The heartbeat adds the
*cumulative* bound plus the visible liveness signal the issue asked for
("log file byte-identical for 4+ minutes").

Env knobs (all optional, fail-loud on malformed values):

  ``TORTOISE_EVAL_INGEST_STALL_TIMEOUT``
      Per-question no-progress budget in seconds (default
      ``DEFAULT_STALL_TIMEOUT_S``). ``none`` / ``off`` / ``0`` disables the
      budget (the socket bound still applies).
  ``TORTOISE_DB_SOCKET_TIMEOUT`` / ``TORTOISE_DB_SOCKET_CONNECT_TIMEOUT``
      Resolved by the projection (product side). The eval lane defaults them
      to ``DEFAULT_EVAL_SOCKET_TIMEOUT_S`` / ``DEFAULT_EVAL_SOCKET_CONNECT_TIMEOUT_S``
      unless an operator set them explicitly.
"""
from __future__ import annotations

import errno
import os
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

#: Graph socket READ bound the eval lane applies (seconds). Generous on
#: purpose: it must never trip a healthy long ingest write on a loaded /
#: defragging FalkorDB, only a real stall. The product default (10s, see
#: ``tortoise/projection/__init__.py``) is too tight for the eval's
#: multi-hundred-KB MERGE writes, so the eval RAISES it here.
DEFAULT_EVAL_SOCKET_TIMEOUT_S = 120.0
#: Graph socket CONNECT bound the eval lane applies (seconds).
DEFAULT_EVAL_SOCKET_CONNECT_TIMEOUT_S = 10.0
#: Per-question no-progress budget (seconds). ~10-60x the measured
#: per-session ingest cost (~15-90 s), so a healthy session never trips it.
DEFAULT_STALL_TIMEOUT_S = 900.0
#: Minimum seconds between emitted heartbeat lines (a 500-session question
#: must not flood the log; a 4-minute silence must still be impossible).
DEFAULT_HEARTBEAT_INTERVAL_S = 30.0

ENV_SOCKET_TIMEOUT = "TORTOISE_DB_SOCKET_TIMEOUT"
ENV_SOCKET_CONNECT_TIMEOUT = "TORTOISE_DB_SOCKET_CONNECT_TIMEOUT"
ENV_STALL_TIMEOUT = "TORTOISE_EVAL_INGEST_STALL_TIMEOUT"

#: Values that mean "no bound" for a seconds-valued knob.
_UNBOUNDED = frozenset({"none", "off", "no", "0", "0.0", "-1"})


class IngestStallTimeout(TimeoutError):
    """The ingest made no progress for longer than the stall budget.

    Deliberately a ``TimeoutError`` carrying ``errno.ETIMEDOUT``: the eval
    then needs NO new error channel —

    - ``tortoise.retry.retryable_transient`` is True (network errno on an
      ``OSError`` subclass),
    - ``tortoise.model_adapters.classify_llm_error`` is TRANSIENT,
    - ``tools.longmem_eval.errors.eval_failure_class`` therefore grades the
      failure entry ``ingest:retries_exhausted`` — which is exactly what
      ``--retry-failed`` re-attempts.
    """

    def __init__(self, message: str, *, stalled_for: float = 0.0,
                 stage: str = "", beats: int = 0) -> None:
        # ETIMEDOUT (not a bare local timeout): the retry predicate requires
        # transport evidence, and a no-progress stall IS a transport stall.
        super().__init__(errno.ETIMEDOUT, message)
        self.stalled_for = stalled_for
        self.stage = stage
        self.beats = beats


def resolve_stall_timeout_s(explicit: float | None = None) -> float:
    """Resolve the no-progress budget (explicit > env > default).

    ``explicit`` wins when given (tests / programmatic callers); otherwise
    ``TORTOISE_EVAL_INGEST_STALL_TIMEOUT`` is read. ``none``/``off``/``0``
    resolve to ``0.0`` — the budget is DISABLED (the socket bound still
    applies). A malformed value raises ``ValueError`` (fail loud — a typo
    must never silently disable the guard).
    """
    if explicit is not None:
        value = float(explicit)
    else:
        raw = os.environ.get(ENV_STALL_TIMEOUT)
        if raw is None or not raw.strip():
            return DEFAULT_STALL_TIMEOUT_S
        token = raw.strip().lower()
        if token in _UNBOUNDED:
            return 0.0
        try:
            value = float(token)
        except ValueError:
            raise ValueError(
                f"{ENV_STALL_TIMEOUT}={raw!r} is not a number of seconds "
                f"(use e.g. '900', or 'none' to disable the stall budget)"
            ) from None
    if value < 0:
        raise ValueError(
            f"stall timeout must be >= 0 seconds, got {value!r} "
            f"(0 disables the budget)")
    return float(value)


def _default_emit(line: str) -> None:
    """Default heartbeat sink — stderr, matching the eval's progress lines."""
    print(line, file=sys.stderr)


class Heartbeat:
    """Liveness signal for one question's ingest (#2969).

    Thread-safe: ``beat`` / ``since`` / ``snapshot`` / ``check`` take a lock,
    so the session-parallel extraction path (#1749) can beat from worker
    threads. The emit callback runs OUTSIDE the lock (a slow sink must never
    serialize the ingest).

    ``clock`` is injectable so tests can drive a stall without sleeping.
    """

    def __init__(self, label: str = "", *,
                 stall_timeout_s: float = DEFAULT_STALL_TIMEOUT_S,
                 emit_interval_s: float = DEFAULT_HEARTBEAT_INTERVAL_S,
                 clock: Callable[[], float] = time.monotonic,
                 emit: Callable[[str], None] | None = None) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self.label = label
        self.stall_timeout_s = float(stall_timeout_s)
        self.emit_interval_s = float(emit_interval_s)
        self._emit = _default_emit if emit is None else emit
        now = self._clock()
        self._started = now
        self._last_progress = now
        self._last_emit = now
        self._stage = "start"
        self._beats = 0

    # ── signal ────────────────────────────────────────────────────────────
    def beat(self, stage: str, *, detail: str = "") -> None:
        """Record progress at ``stage`` (i.e. "work on this stage starts
        NOW"); emit a heartbeat line when the emit interval elapsed."""
        emit_line: str | None = None
        with self._lock:
            now = self._clock()
            self._last_progress = now
            self._stage = stage
            self._beats += 1
            if (self.emit_interval_s >= 0
                    and now - self._last_emit >= self.emit_interval_s):
                self._last_emit = now
                emit_line = self._line_locked(stage, detail)
        if emit_line is not None:
            self._emit(emit_line)

    def stage(self, stage: str, *, detail: str = "") -> None:
        """Boundary helper: enforce the budget for the stage that just
        FINISHED, then mark the next stage as started.

        This is the shape the ingest uses — ``beat`` marks the START of a
        stage, so ``check`` at the next boundary measures how long that
        stage actually ran. Beating only at the END of a stage would forgive
        every slow stage (progress would be recorded after the delay), which
        is precisely the #2969 grind.
        """
        self.check(stage)
        self.beat(stage, detail=detail)

    def line(self, stage: str | None = None, detail: str = "") -> str:
        """The current heartbeat line (no side effects)."""
        with self._lock:
            return self._line_locked(stage or self._stage, detail)

    def _line_locked(self, stage: str, detail: str) -> str:
        now = self._clock()
        suffix = f" {detail}" if detail else ""
        return (f"[longmem_eval] ingest heartbeat {self.label} "
                f"stage={stage} beats={self._beats} "
                f"idle={now - self._last_progress:.1f}s "
                f"total={now - self._started:.1f}s{suffix}")

    # ── liveness read ─────────────────────────────────────────────────────
    def since(self) -> float:
        """Seconds since the last recorded progress."""
        with self._lock:
            return self._clock() - self._last_progress

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = self._clock()
            return {"label": self.label, "stage": self._stage,
                    "beats": self._beats,
                    "idle_s": now - self._last_progress,
                    "total_s": now - self._started}

    # ── enforcement ───────────────────────────────────────────────────────
    def check(self, next_stage: str = "") -> None:
        """Raise :class:`IngestStallTimeout` if the budget is blown.

        ``next_stage`` is only for the error message (which stage was about
        to start when the stall was noticed).
        """
        budget = self.stall_timeout_s
        if budget <= 0:
            return
        idle = self.since()
        if idle <= budget:
            return
        snap = self.snapshot()
        raise IngestStallTimeout(
            f"ingest stalled: no progress for {idle:.1f}s "
            f"(budget {budget:.1f}s; last stage={snap['stage']!r}, "
            f"beats={snap['beats']}, next={next_stage!r}). The graph "
            f"socket read is bounded separately by {ENV_SOCKET_TIMEOUT}; "
            f"raise or disable the budget with {ENV_STALL_TIMEOUT}=none.",
            stalled_for=idle, stage=str(snap["stage"]), beats=int(snap["beats"]))
