"""The RECORD projection on its own loop — a write acks, the fold follows (#14185).

WHY THIS MODULE EXISTS
======================
`journal_event` is an append-only log and the record is a **fold over it**
(`docs/architecture/STORAGE-ARCHITECTURE.md` §3: `derived = replay(the journal)`).
Both halves already existed: the log is appended by :mod:`tortoise.live_write`,
and the fold is :func:`tortoise.record_projection.replay`, driven live by
:func:`tortoise.record_writer.write_live_record`.

What was missing is that the fold ran **inside the request**. §2.5 of the
scaling analysis (#14185) is the comparison: Mem0 acknowledges a memory add in
**< 50 ms** and does its distillation on a queue; Zep states that new facts
appear **after a short processing delay**. The cost of the inline shape is
measured in `write_live_record`'s own docstring — **18.9–20.1 ms** per record
before #9558, **3.82–3.91 ms** after it — and every one of those milliseconds is
stolen from the caller's ack, on the same thread, after the journal has already
committed. The journal append is the COMMIT POINT (#7951): once the line exists
the write has landed, so everything after it is bookkeeping that need not be
awaited.

So: this module owns the fold's **loop**. `live_write` hands the just-appended
record over and stops waiting; the worker drains at its own rate and the lag it
accumulates is **exported as a metric** rather than inferred.

WHAT THIS MODULE IS NOT
=======================
* **Not a second fold.** Every statement still comes from
  :func:`tortoise.record_projection.replay`, through the caller's own
  ``project`` callable — normally ``record_writer.write_live_record``. This
  module chooses *when* a fold runs and never *what* it does, so a fourth write
  door cannot get a fourth answer to "what does this event mean in Postgres".
* **Not a store.** The queue is in memory and is not durable by design: the
  journal is the durable copy of everything queued here, and a process that dies
  with a non-empty queue loses nothing a replay cannot restore
  (``tools/record_runner.py apply``). Nothing is ever DROPPED to make room —
  see *BACKPRESSURE* below.
* **Not the cache fold.** `live_write`'s ``projection.apply`` (record →
  FalkorDB) still runs inline. Deferring the record is safe because the record
  has an independent, always-available reader and a journal that heals it; the
  cache is what the read path actually serves from today, so moving it behind a
  loop is a **retrieval-freshness** change with its own evidence, not this
  issue's. The boundary is stated here so it cannot be mistaken for an oversight.
* **Not on a read path, ever.** No reader may call :func:`drain` or
  :meth:`ProjectionWorker.wait_for_drain`; that would re-create exactly the
  coupling this module removes.

THE FOUR PROPERTIES THE ISSUE ASKS FOR, AND WHERE EACH LIVES
============================================================
1. **The projection consumes the log at its own rate.** One daemon thread per
   process, one FIFO queue, drained by the caller-supplied ``project``. Order is
   preserved because there is exactly ONE consumer (two consumers would let two
   records for one tenant overtake each other; the record's writes are absolute
   per row, so that is a correctness hazard, not only a delay).
2. **A write's ack excludes projection time.** :func:`submit` appends to a deque
   under a lock that is held for an append and nothing else — the ``project``
   call always runs **outside** it. The write returns as soon as the append has
   committed and the item is queued.
3. **Lag is a metric.** :meth:`ProjectionWorker.stats` reports the queue depth,
   the count of journal POSITIONS not yet in the record, and the age of the
   OLDEST pending item; :mod:`tortoise.monitoring` exports them as gauges.
4. **A stalled projection cannot slow a read.** The worker holds no lock while
   folding and holds no resource a reader needs. A reader that races the
   projection is not *blocked* — it is told, by the existing readers, that the
   record is behind: `record_projection.journal_coverage` counts the positions
   the record is short of, `python -m tortoise record-reconcile --journal`
   reports `unprojected_journal`, and `tools/record_runner.py status` names the
   resume point. ⚠️ **That is the DECLARATION the lag owes (#2952's class)**: a
   deployment that arms this worker accepts that a read issued immediately after
   a write may not see that write in the RECORD yet, and every one of those
   readers says so rather than returning a confident short answer.

BACKPRESSURE — NEVER DROP, NEVER BLOCK FOREVER
==============================================
When the queue is at :data:`QUEUE_MAX_ENV`'s bound (default
:data:`DEFAULT_QUEUE_MAX`), :func:`submit` DECLINES and the caller folds
**inline**, on its own thread. Two reasons that is the right shape and not a
compromise:

* **A dropped item would be a silently short record.** The line is already in
  the journal, so a drop is not data loss — but it is an ``unprojected``
  position that only a human or a scheduler reading the metric would notice,
  which is the silent-green shape this repo refuses elsewhere.
* **Blocking the producer instead would move the stall, not remove it.** A
  wedged projection must degrade the WRITE (which is visible, counted, and
  bounded by the queue size) and must never degrade a READ (which has nothing
  to do with it) — the trade the issue itself asks for.

The inline fallback is counted (``inline_fallbacks{reason="queue_full"}``), so
"the worker is not keeping up" is a number rather than an inference.

WHAT MAKES A WRITE DEFERRABLE AT ALL
====================================
Five conditions, all of them the caller's (`live_write`), because only the
chokepoint holds the facts. The three that are CORRECTNESS rather than
configuration:

* ⛔ **A record-only write is never deferred** (``journal_skipped``, #14002).
  On a cut-over graph there is no journal line — the record's own commit IS the
  write's durability. Deferring it would acknowledge a write that exists in
  neither store for the length of the lag.
* ⛔ **A write whose outcome a door CONSUMES is never deferred**
  (``record_sink``, #10000/#10021). The capture writer branches on
  ``RecordWrite.applied`` to decide whether the turn lives in the record or must
  be written to the graph. A deferred write has not applied yet, so handing it
  that answer would put the turn in the graph *and* the record.
* ⛔ **A write whose TENANT is not carried by value is never deferred**
  (``tenant is None``). ⚠️ A ``threading.Thread`` does not inherit the caller's
  contextvars — this is the one way a hand-off can CHANGE what a write means
  rather than only when it lands. A tenant resolved ambivalently at fold time
  (``carried > scope > env pair``) is resolved AGAIN on the worker, where a
  ``contextvars``-carried pair is ABSENT, leaving the env pair to answer: a
  ``no_tenant`` refusal on a multi-tenant box, or a row under the WRONG tenant on
  a single-tenant one (#9192's class, reached through a thread). `live_write`
  therefore defers only what arrives with ``tenant=`` supplied; a door that
  relies on the ambient carrier keeps folding inline, where its resolution
  already worked.

The other two are the flag itself and the queue bound. Read with
:func:`tortoise.env_truthy.env_flag`, like every other operational knob, so a
typo leaves the worker OFF rather than half-armed.
"""
from __future__ import annotations

import atexit
import collections
import copy
import dataclasses
import logging
import os
import threading
import time
from typing import Any, Callable, Mapping

from .env_truthy import env_flag

LOGGER = logging.getLogger(__name__)

#: The arming flag. OFF by default and read with
#: :func:`tortoise.env_truthy.env_flag`, so an unset, blank, typo'd or garbage
#: value leaves the worker OFF — the deployment then behaves byte-identically to
#: the pre-#14185 inline fold. This mirrors :data:`tortoise.record_writer.
#: RECORD_ALLOCATE_ENV`: a writer switch is rolled by an operator who has read
#: what it changes, never by a half-typed line in a process manager.
ENV_WORKER = "TORTOISE_PROJECTION_WORKER"

#: How many queued projections the bound allows before :func:`submit` starts
#: declining (and the caller folds inline). Sized so a normal burst is absorbed
#: and a wedged fold is bounded in MEMORY: one item holds one record, so the
#: ceiling is a few MB at the default. Read as an integer; an unparseable or
#: non-positive value falls back to :data:`DEFAULT_QUEUE_MAX` rather than
#: disabling the worker (a typo must not silently change the mode).
ENV_QUEUE_MAX = "TORTOISE_PROJECTION_QUEUE_MAX"

#: The default bound — see :data:`ENV_QUEUE_MAX`.
DEFAULT_QUEUE_MAX = 10_000

#: How long the drain loop waits on the condition before re-checking its own
#: state. It is NOT a latency bound (a notify wakes it immediately); it exists so
#: a stop request is seen even if a notify were somehow missed.
IDLE_WAIT_SECONDS = 0.5

#: How long :func:`shutdown` (and the ``atexit`` hook) waits for the queue to
#: empty. Bounded on purpose: a process that is exiting must not hang on a
#: wedged projection — the journal holds every queued line, and
#: ``tools/record_runner.py apply`` heals whatever the drain did not reach. The
#: number is small because this runs on the interpreter's exit path.
DRAIN_ON_EXIT_SECONDS = 5.0


@dataclasses.dataclass(frozen=True)
class PendingProjection:
    """One just-appended journal record waiting for its fold.

    ``project`` is the CALLER's own record half (normally
    ``record_writer.write_live_record``) and is invoked with the same signature
    `live_write` would have called it inline with — the worker must not be a
    second spelling of that boundary.

    ``seq`` is the journal position the append reported (#9190), kept here for
    ONE purpose: counting the positions the record is short of. It is never used
    to number anything — the fold reads its ordinal from the caller's call.

    ⛔ ``tenant`` IS GATE EVIDENCE AND NOT A FOLD PARAMETER (#14222 review P2-1),
    and the distinction has to be stated because the field's presence invites the
    other reading. It records the tenant the write CARRIED, which is the fact
    `live_write` requires before it will defer at all — a write resolved from the
    AMBIENT carrier is never queued, because a thread does not inherit the
    caller's contextvars and the fold would re-resolve to the env pair instead
    (#9192's class). What it does NOT do is force the fold to use it: the
    caller's ``project`` callable resolves the tenant its own way, exactly as it
    does on the inline path, and the SDK door reads ``self._record_tenant``
    there. That is sound because the gate proves a carried tenant EXISTED and a
    per-request SDK's binding cannot move, but it is a RESIDUAL rather than a
    proof: a shared SDK re-bound between the enqueue and the fold would widen
    the two-read window `sdk._bind_record_tenant`'s docstring already names — from
    one call frame to the queue's lag. Passing the pair through to the callable
    would close it, and is not done here because it changes the ``project``
    protocol every door and every double implements.
    """
    record: Mapping[str, Any]
    seq: int | None
    tenant: tuple[str, str] | None
    journal_skipped: bool
    project: Callable[..., Any]
    enqueued_at: float

    def age_seconds(self, *, now: float | None = None) -> float:
        return max(0.0, (time.monotonic() if now is None else now)
                   - self.enqueued_at)


@dataclasses.dataclass(frozen=True)
class ProjectionStats:
    """What the loop is doing — the lag, in positions and in seconds.

    ``pending_positions`` is the number of queued items that carry a journal
    ordinal, i.e. the positions the record has NOT taken yet. It is the same
    quantity `record_projection.JournalCoverage.unprojected` measures against
    the record, read from the other side: coverage asks the record what it
    holds, this asks the queue what it still owes. ⚠️ `None` ordinals
    (a journal that could not prove its position, #9307) are queued and folded
    but are NOT counted here — there is no position to be short of.
    """
    running: bool
    queue_depth: int
    pending_positions: int
    oldest_pending_seconds: float
    submitted: int
    projected: int
    failures: int
    inline_fallbacks: int

    @property
    def lagging(self) -> bool:
        """Is the record behind the journal right now?"""
        return self.pending_positions > 0


class ProjectionWorker:
    """One bounded FIFO queue and one daemon thread, folding as fast as it can.

    Constructible directly (the tests do, with an injected ``project``-carrying
    item), but production reaches it through :func:`worker`, the process
    singleton.
    """

    def __init__(self, *, max_queue: int = DEFAULT_QUEUE_MAX,
                 name: str = "tortoise-projection-worker") -> None:
        self.max_queue = max(1, int(max_queue))
        self._name = name
        self._cond = threading.Condition()
        self._pending: collections.deque[PendingProjection] = collections.deque()
        #: The item the loop is folding RIGHT NOW. It is counted as pending lag on
        #: purpose: a position being folded is still a position the record has not
        #: taken, and on a wedged fold it is the ONLY pending item — reporting 0
        #: there would make a stalled projection read as a caught-up record.
        self._inflight: PendingProjection | None = None
        self._thread: threading.Thread | None = None
        self._pid: int | None = None
        self._stopping = False
        self._submitted = 0
        self._projected = 0
        self._failures = 0
        self._inline_fallbacks = 0
        self._atexit_installed = False

    # ── the write side ─────────────────────────────────────────────────────

    def submit(self, item: PendingProjection) -> bool:
        """Queue one projection. ``True`` queued, ``False`` ⇒ fold it inline.

        ⛔ NEVER RAISES. This is called on the write path after the journal has
        committed, so a failure here would hand the producer an exception for a
        write that persisted — and would leave the record short with no inline
        fallback. Thread start failures are therefore answered as ``False``
        (the caller folds inline), which is the same verdict a full queue gets.
        """
        self._ensure_started()
        with self._cond:
            if self._stopping or len(self._pending) >= self.max_queue:
                self._inline_fallbacks += 1
                declined = "worker_stopping" if self._stopping else "queue_full"
            else:
                self._pending.append(item)
                self._submitted += 1
                declined = False
                queued = self._depth_locked()
                positions = self._pending_positions_locked()
                oldest = self._oldest_age_locked()
                # ⛔ PUBLISHED UNDER THE LOCK THE VALUES WERE READ UNDER (#14222
                # review cycle 2, NEW-1). Publishing after the release let a
                # slower thread overwrite a NEWER snapshot with an OLDER one — a
                # backlog of 2 exporting 1, or a drained queue exporting 1, until
                # the next transition. That is the same lie cycle 1's P1-1 was
                # filed for, reachable from the other side. `Gauge.set` takes only
                # prometheus' own value lock and no path here holds that while
                # acquiring this Condition, so the order is safe.
                _publish_queue_state(self._depth_locked(), positions, oldest)
                self._cond.notify()
        if declined:
            _publish_inline_fallback(declined)
            return False
        return True

    # ── the read side (never blocks on the fold) ───────────────────────────

    def stats(self) -> ProjectionStats:
        """A consistent snapshot for the metrics surface. Holds NO lock the fold holds."""
        self._ensure_not_inherited()
        with self._cond:
            return ProjectionStats(
                running=self._thread is not None and self._thread.is_alive(),
                queue_depth=self._depth_locked(),
                pending_positions=self._pending_positions_locked(),
                oldest_pending_seconds=self._oldest_age_locked(),
                submitted=self._submitted,
                projected=self._projected,
                failures=self._failures,
                inline_fallbacks=self._inline_fallbacks,
            )

    def wait_for_drain(self, timeout: float | None = None) -> bool:
        """Block until the queue is empty. ⛔ FOR TESTS AND SHUTDOWN ONLY.

        A READ PATH MUST NEVER CALL THIS. Waiting here is precisely the coupling
        the worker exists to remove: it makes a reader's latency a function of
        the fold's, which is the pre-#14185 shape wearing a new name. The only
        two legitimate callers are :func:`shutdown` (and its ``atexit`` hook)
        and a test that needs the fold to have finished.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            self._ensure_not_inherited()
            with self._cond:
                # ⛔ "EMPTY AND NOTHING IN FLIGHT" IS THE EXACT PREDICATE
                # (#14222 review cycle 2, NEW-3). The counter form
                # (`projected + failures >= submitted`) was an APPROXIMATION that
                # a forked child could never satisfy — it inherits the parent's
                # high-water marks with an empty queue, so its exit drain burned
                # the full timeout on a worker that had nothing to do. The loop
                # pops and clears `_inflight` under this same lock, so this is
                # exact rather than approximate.
                if not self._pending and self._inflight is None:
                    return True
                remaining = (IDLE_WAIT_SECONDS if deadline is None
                             else deadline - time.monotonic())
                if remaining <= 0:
                    # ⛔ NOT `not self._pending` (#14222 review P1-2). An empty
                    # deque is exactly the state a WEDGED fold leaves behind — it
                    # popped its item and is inside `project` — so the old
                    # expression answered "drained" for the one case a call that
                    # waits for the fold exists to report. A timeout is a "not
                    # drained" verdict; the success predicate above is the only
                    # honest one.
                    return False
                self._cond.wait(min(IDLE_WAIT_SECONDS, remaining))

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the loop if it is not running (idempotent, fork-aware)."""
        self._ensure_started()

    def stop(self, *, drain: bool = True,
             timeout: float = DRAIN_ON_EXIT_SECONDS) -> bool:
        """Stop the loop, optionally letting the queue empty first.

        RETURNS whether the consumer is GONE, and the answer is load-bearing at
        the one production caller :func:`shutdown` — see its own note. ``False``
        means a fold is still running on a thread that would not join, so this
        object must be kept: replacing it would put a SECOND consumer beside the
        wedged one.
        """
        self._ensure_not_inherited()
        if drain:
            self._quietly(lambda: self.wait_for_drain(timeout))
        with self._cond:
            self._stopping = True
            thread = self._thread
            self._cond.notify_all()
        # ⛔ LIVENESS DECIDES, NEVER A DEFAULT (#14222 review cycle 2, NEW-5). The
        # `joined = True` default was wrong on the REENTRANT path — a `project`
        # callable that stops its own worker skips the join entirely and then
        # cleared `_thread` while the consumer (itself) was still alive, which is
        # the double-consumer hazard this method was just fixed for, back again.
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        gone = thread is None or not thread.is_alive()
        if not gone:
            # ⛔ A TIMED-OUT JOIN LEAVES THE CONSUMER ALIVE (#14222 review P2-2).
            # Clearing `_thread` anyway let the next `submit`'s `_ensure_started`
            # start a SECOND consumer on the same deque — the exact shape the
            # module docstring calls a correctness hazard (two folds for one
            # tenant can land out of order, and the record's mutations are
            # absolute per row). Keeping `_thread` set makes `_ensure_started` a
            # no-op instead; submits then DECLINE and fold inline, which is the
            # honest answer for a loop that is still wedged.
            return False
        with self._cond:
            self._thread = None
            self._pid = None
        return True

    def _ensure_started(self) -> None:
        while True:
            cond = self._cond
            with cond:
                # ⛔ A PEER REPLACED THE CONDITION WHILE THIS THREAD WAITED: it is
                # holding the old one, which nobody else can see, so the only
                # correct move is to re-enter on the current object (#14222
                # review cycle 2, NEW-4).
                if cond is not self._cond:
                    continue
                if self._inherited_locked():
                    self._reset_for_fork_locked()
                    continue
                if self._thread is not None and self._thread.is_alive():
                    return
                self._thread = None
                self._pid = os.getpid()
                self._stopping = False
                thread = threading.Thread(target=self._run, name=self._name,
                                          daemon=True)
                self._thread = thread
                thread.start()
                if not self._atexit_installed:
                    self._atexit_installed = True
                    self._quietly(lambda: atexit.register(_drain_at_exit, self))
                return

    def _ensure_not_inherited(self) -> None:
        """Reset this object for a NEW PROCESS, before anyone takes its lock.

        ⛔ EVERY ENTRY POINT CALLS THIS, not only :meth:`_ensure_started`
        (#14222 review cycle 2, NEW-7). A forked child inherits the parent's
        Condition — and a Condition inherited while the parent's consumer held it
        is a lock with no owner here, so `stats()`, `wait_for_drain()` and the
        `atexit` drain would block on it forever, wedging the child's exit path
        or a health read. A child that never performs a deferred write never
        reached `_ensure_started`, which is why the reset cannot live only there.
        """
        while True:
            cond = self._cond
            with cond:
                if cond is not self._cond:
                    continue
                if self._inherited_locked():
                    self._reset_for_fork_locked()
                    continue
                if self._pid is None:
                    # Stopped (or never started) IN THIS PROCESS: adopt the pid
                    # so `stats()`/`stop()` stop re-checking, and leave the
                    # counters alone — they are this process's observations and a
                    # stop does not unmake them.
                    self._pid = os.getpid()
                return

    def _inherited_locked(self) -> bool:
        """Was this object inherited from ANOTHER process?

        ⚠️ ``_pid is None`` (a worker that was stopped, or never started) is NOT
        "not inherited": the Condition is still the parent's object. It is
        therefore treated as stale and reset — the reset is idempotent and cheap,
        and guessing the other way leaves the inherited lock in place.
        """
        return self._pid is not None and self._pid != os.getpid()

    def _reset_for_fork_locked(self) -> None:
        """Replace the state a fork copies, holding the lock being replaced.

        The parent still owns the queue, so the child must not fold those items a
        second time; the in-flight item can never be cleared by the child, so
        leaving it would report one permanently pending position and a
        monotonically growing lag; and the COUNTERS go too, because they are
        per-process observations and an inherited `submitted=3, projected=1` with
        an empty queue made `wait_for_drain` unsatisfiable (NEW-3).
        """
        self._cond = threading.Condition()
        self._pending = collections.deque()
        self._inflight = None
        self._thread = None
        self._stopping = False
        self._submitted = 0
        self._projected = 0
        self._failures = 0
        self._pid = os.getpid()

    # ── the loop ───────────────────────────────────────────────────────────

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._pending and not self._stopping:
                    self._cond.wait(IDLE_WAIT_SECONDS)
                if self._stopping and not self._pending:
                    return
                item = self._pending.popleft()
                self._inflight = item
                self._cond.notify_all()
            # ⛔ THE FOLD RUNS WITH NO LOCK HELD. That single fact is what makes
            # `stats()` / `submit()` — and therefore any reader that asks the
            # lag question — independent of how long this projection takes.
            failure = None
            try:
                result = item.project(item.record, item.seq,
                                      journal_skipped=item.journal_skipped)
            except Exception as exc:  # noqa: BLE001 — the loop must survive
                failure = "projection_raised"
                LOGGER.warning(
                    "projection_worker: the deferred record projection RAISED "
                    "(%s: %s) for journal position %s — the journal holds the "
                    "line and the record does not. This is the named "
                    "disagreement `record_projection.journal_coverage` counts; "
                    "`tools/record_runner.py apply` heals it idempotently "
                    "(#14185)",
                    type(exc).__name__, exc, item.seq)
            else:
                # ⛔ A FOLD THAT DID NOTHING IS NOT A PROJECTION (#14222 review
                # cycle 2, NEW-2). `write_live_record` returns ``None`` ONLY when
                # the record is unarmed — and on this arm `live_write` PROVED it
                # armed before queueing, so the answer means the arming changed
                # between the ack and the fold (an in-process lane that scopes
                # `TORTOISE_RECORD_DSN` for one run and restores it in a
                # `finally` does exactly that). Counting it as `projected` made
                # the loop's own success counter report a fold that wrote
                # nothing, and the seam's unarmed arm is deliberately silent (no
                # counter, no log — §2.0's byte-identical deployment), so this is
                # the only surface that could see it.
                if result is None:
                    failure = "record_unarmed_at_fold"
                    LOGGER.warning(
                        "projection_worker: the deferred record projection for "
                        "journal position %s did NOTHING — the record was armed "
                        "when the write was acknowledged and is NOT armed now, so "
                        "no row was written and no error was raised. The journal "
                        "holds the line; `tools/record_runner.py apply` folds it "
                        "idempotently once the record is armed again (#14185)",
                        item.seq)
            # ⛔ ALL THREE GAUGES ARE READ HERE, AFTER `_inflight` IS CLEARED, and
            # never from a value captured at POP time (#14222 review P1-1). The
            # pop-time depth counts the item that is about to be folded, so
            # publishing it after the fold reports "1 owed" for a DRAINED queue
            # and "1 owed" for a backlog of 100 — an alert on `depth > 0` would
            # fire forever on a caught-up record and stay quiet through a real
            # one, while `stats()` said the opposite.
            with self._cond:
                self._inflight = None
                if failure is not None:
                    self._failures += 1
                else:
                    self._projected += 1
                _publish_queue_state(self._depth_locked(),
                                     self._pending_positions_locked(),
                                     self._oldest_age_locked())
                self._cond.notify_all()
            if failure is not None:
                _publish_failure(failure)

    # ── internals ──────────────────────────────────────────────────────────

    def _depth_locked(self) -> int:
        return len(self._pending) + (1 if self._inflight is not None else 0)

    def _pending_positions_locked(self) -> int:
        in_flight = 1 if (self._inflight is not None
                          and self._inflight.seq is not None) else 0
        return in_flight + sum(1 for i in self._pending if i.seq is not None)

    def _oldest_age_locked(self) -> float:
        if self._inflight is not None:
            return self._inflight.age_seconds()
        if not self._pending:
            return 0.0
        return self._pending[0].age_seconds()

    @staticmethod
    def _quietly(fn: Callable[[], Any]) -> None:
        try:
            fn()
        except Exception:  # noqa: BLE001 — shutdown must never raise
            pass


# ── the process singleton ──────────────────────────────────────────────────


_WORKER: ProjectionWorker | None = None
_WORKER_LOCK = threading.Lock()


def armed() -> bool:
    """Is the worker switched on for this process? Unset/typo ⇒ ``False``."""
    return env_flag(ENV_WORKER, False)


def queue_max() -> int:
    """The configured bound, with a typo answered as the DEFAULT (not as "off")."""
    raw = os.environ.get(ENV_QUEUE_MAX)
    if not raw:
        return DEFAULT_QUEUE_MAX
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        LOGGER.warning(
            "projection_worker: %s is not an integer (%r) — using the default "
            "bound of %d rather than changing the mode (#14185)",
            ENV_QUEUE_MAX, raw, DEFAULT_QUEUE_MAX)
        return DEFAULT_QUEUE_MAX
    if value < 1:
        LOGGER.warning(
            "projection_worker: %s must be >= 1 (got %r) — using the default "
            "bound of %d (#14185)", ENV_QUEUE_MAX, raw, DEFAULT_QUEUE_MAX)
        return DEFAULT_QUEUE_MAX
    return value


def worker() -> ProjectionWorker:
    """The process-wide worker, created on first use. Never starts a thread here."""
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is None:
            _WORKER = ProjectionWorker(max_queue=queue_max())
        return _WORKER


def submit(*, record: Mapping[str, Any], seq: int | None,
           tenant: tuple[str, str] | None, journal_skipped: bool,
           project: Callable[..., Any]) -> bool:
    """Queue one record's projection. ``False`` ⇒ the caller must fold inline.

    ⛔ IT DOES NOT ASK WHETHER THE WRITE IS DEFERRABLE. That decision needs facts
    only the chokepoint holds — whether a journal line exists, and whether a door
    is going to read the outcome — and duplicating it here would be a second
    authority over a correctness rule (see the module docstring). `live_write`
    asks; this function only queues.
    """
    if not armed():
        return False
    # ⛔ THE QUEUED ITEM IS A SNAPSHOT, NOT A REFERENCE (#14222 review P2-4).
    # Before this loop existed the record fold and the cache fold both read the
    # caller's dict while the caller was still inside the write; now the record
    # fold reads it AFTER the caller resumed, and the SDK's envelope SHARES its
    # nested values with the caller (`event["point"] = clean`, `event.update(
    # extra)`). An in-place mutation of a nested value between the ack and the
    # fold would then fold the record from different content than the cache saw,
    # with nothing able to detect the divergence. `write_live_record` writes the
    # same dict it is handed, so the copy must be DEEP — a shallow `dict()` keeps
    # every nested object shared. A copy that cannot be made falls back to the
    # reference rather than failing a write whose line has already committed.
    try:
        snapshot = copy.deepcopy(record)
    except Exception:  # noqa: BLE001 — never fail a committed write over a copy
        # ⛔ DECLINE, DO NOT FALL BACK TO THE REFERENCE (#14222 review cycle 2,
        # NEW-8). The reference is the very aliasing this copy exists to remove,
        # and taking it would restore the undetectable divergence SILENTLY. Fold
        # inline instead: byte-identical to the pre-#14185 write, with no window
        # at all, and the caller pays only the fold it always used to pay.
        return False
    return worker().submit(PendingProjection(
        record=snapshot, seq=seq, tenant=tenant,
        journal_skipped=journal_skipped, project=project,
        enqueued_at=time.monotonic()))


def stats() -> ProjectionStats:
    """Snapshot of the process worker. Safe on a read path — it holds no fold lock."""
    return worker().stats()


def wait_for_drain(timeout: float | None = None) -> bool:
    """⛔ Tests and shutdown only — see :meth:`ProjectionWorker.wait_for_drain`."""
    return worker().wait_for_drain(timeout)


def shutdown(*, drain: bool = True,
             timeout: float = DRAIN_ON_EXIT_SECONDS) -> None:
    """Stop the process worker and CLEAR the singleton.

    Clearing it (rather than only stopping the thread) is what lets the next
    :func:`worker` honour a CHANGED :data:`ENV_QUEUE_MAX`: the bound is read once,
    at construction. A restart through :meth:`ProjectionWorker._ensure_started`
    would otherwise revive the old bound silently, which is precisely the class of
    "the flag I set did not take" this module's docs promise not to produce.
    """
    global _WORKER
    with _WORKER_LOCK:
        current = _WORKER
        _WORKER = None
    if current is None:
        return
    if not current.stop(drain=drain, timeout=timeout):
        # ⛔ PUT IT BACK (#14222 review cycle 2, NEW-6). `stop()` returns ``False``
        # when a fold would not join, so the consumer is STILL RUNNING. Dropping
        # the reference anyway made the next `worker()` build a fresh instance
        # with its own thread and deque — two consumers folding one tenant's
        # records at once, which is the ordering hazard this module names (the
        # record's mutations are absolute per row, so out-of-order folds are a
        # correctness problem, not a delay). The retained instance already
        # DECLINES submits with `worker_stopping` and the caller folds inline,
        # which is the honest answer while a consumer is wedged.
        with _WORKER_LOCK:
            _WORKER = current


def _drain_at_exit(instance: ProjectionWorker) -> None:
    """Flush what the queue holds when the interpreter exits, bounded.

    Nothing is lost when it does not finish: the journal holds every queued
    line, and ``tools/record_runner.py apply`` folds it idempotently. The bound
    is what keeps a wedged projection from hanging a process's exit path.
    """
    instance._quietly(lambda: instance.stop(drain=True,
                                            timeout=DRAIN_ON_EXIT_SECONDS))


# ── metrics (the single-writer house shape, #501/#3677) ────────────────────
#
# These delegate to `tortoise.monitoring`, which owns every metric family. The
# imports are FUNCTION-LOCAL and guarded: `monitoring` is a large module, and a
# metrics failure must never be able to break the fold or the write that queued
# it.


def _publish_queue_state(depth: int, pending_positions: int,
                         oldest_age_seconds: float | None = None) -> None:
    try:
        from . import monitoring
    except Exception:  # noqa: BLE001 — a metrics fault must not stop the loop
        return
    try:
        monitoring.record_projection_queue_state(
            depth=depth, pending_positions=pending_positions,
            oldest_age_seconds=oldest_age_seconds)
    except Exception:  # noqa: BLE001
        pass


def _publish_failure(reason: str) -> None:
    try:
        from . import monitoring
    except Exception:  # noqa: BLE001
        return
    try:
        monitoring.record_projection_worker_failure(reason)
    except Exception:  # noqa: BLE001
        pass


def _publish_inline_fallback(reason: str) -> None:
    try:
        from . import monitoring
    except Exception:  # noqa: BLE001
        return
    try:
        monitoring.record_projection_inline_fallback(reason)
    except Exception:  # noqa: BLE001
        pass
