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
* **Not on a read path, ever.** No reader may call :meth:`ProjectionWorker.wait_for_drain`
  (nor its module-level twin :func:`wait_for_drain`); that would re-create exactly the
  coupling this module removes.

THE FOUR PROPERTIES THE ISSUE ASKS FOR, AND WHERE EACH LIVES
============================================================
1. **The projection consumes the log at its own rate.** One daemon thread per
   process, one FIFO queue, drained by the caller-supplied ``project``. Order is
   preserved **within the queue**, because there is exactly ONE consumer (two
   consumers would let two records for one tenant overtake each other; the
   record's writes are absolute per row, so that is a correctness hazard, not
   only a delay). ⛔ **THE INLINE FALLBACK IS OUTSIDE THAT ORDER, and that is
   stated rather than implied** (#14222 review cycle 5, ISSUE 3; widened in
   round 13, P2, because the trigger was understated): when a write does not go
   through the queue, the caller folds its OWN position on its own thread, at
   ``live_write``'s inline call, while an EARLIER position may still be queued —
   so one writer's row can be overwritten by its predecessor. ⚠️ **THERE ARE THREE
   FAMILIES OF TRIGGER, AND ONLY ONE OF THEM IS THE BOUND.** (a) A DECLINE by the
   bound or by a stopping worker (``queue_full`` / ``worker_stopping``), which is
   what ``TORTOISE_PROJECTION_QUEUE_MAX`` controls — with the bound at ``1``, or
   once the default saturates. (b) The STRUCTURAL arms, which fold inline on EVERY
   write at ANY depth, including an idle queue: ``tenant is None`` (the
   ``api.EventAPI._emit`` / ``ConversationMiner._emit_event`` shape, whose doors
   pass no tenant) and every ``record_sink`` door. (c) An inline fold the bound
   cannot reach at all: an UNCOPYABLE record (the deep snapshot in :func:`submit`
   fails, so a write that would otherwise already be queued folds inline instead)
   and a ``submit`` FAULT (the ``queue_fault`` arm in :func:`submit`, published
   before it propagates). So raising the bound does NOT remove this residual — it only
   removes arm (a); a same-tenant queued write can be overtaken by a structural-arm
   fold at zero depth, or by a (c) fold at any depth. Nothing detects it: both
   positions are present, so ``journal_coverage`` / ``record-reconcile`` read
   green. The alternative to that residual is a caller that WAITS on the queue —
   which is the coupling this module exists to remove — so the honest form is the
   declared one: the bound's visible cost is a slower ACK **and** a possible
   same-tenant reorder, from either arm. Closing it needs an ordering token at
   the fold seam, which is a change to ``record_projection``, not to this module.
   ⚠️ ``.env.example`` states the same residual where the bound is documented.
2. **A write's ack excludes projection time.** :func:`submit` appends to a deque
   under a lock that is held for an append and nothing else — the ``project``
   call always runs **outside** it. The write returns as soon as the append has
   committed and the item is queued. ⛔ **"An append and nothing else" is literal,
   and it was false until #14222's review found it**: the snapshot stamped under
   that lock used to COUNT the deque (``sum(1 for i in self._pending …)``), so the
   ack was O(queue depth) at the bound this module permits. The position count is
   now maintained at the deque's two ends, so every statement inside the critical
   section is O(1) — see :meth:`ProjectionWorker._pending_positions_locked`.
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
When the queue is at :data:`ENV_QUEUE_MAX`'s bound (default
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
"the worker is not keeping up" is a number rather than an inference. The
DECLINES that never reach the queue are counted on the same family, because they
are the same fact — this ACK paid the fold — and a silent one would read as a
caught-up idle loop: ``uncopyable`` (the deep snapshot could not be made, so the
item is folded inline rather than aliased) and ``queue_fault`` (``submit`` raised
before it could queue, e.g. ``thread.start()`` under thread exhaustion).

WHAT MAKES A WRITE DEFERRABLE AT ALL
====================================
Six conditions, all of them the caller's (`live_write`), because only the
chokepoint holds the facts. The four that are CORRECTNESS rather than
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
* ⛔ **A write whose LOG is not a durable journal is never deferred**
  (``durable``, #9231). A scratch buffer is declined by the seam itself
  (``no_durable_journal``), so queueing it buys nothing and only risks the row
  being filed from a lane that a replay can never reproduce.
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

The other two are the two SWITCHES, and they are deliberately separate: whether a
record is armed at all — a queue on an unarmed record would report a loss
nothing was ever going to take — and the loop's own flag with its queue bound.
The loop flag is read with :func:`tortoise.env_truthy.env_flag`, like every other
operational knob, so a typo leaves the worker OFF rather than half-armed; the
record's arming is its own DSN (``TORTOISE_RECORD_DSN``) and is never inferred
from this flag.
"""
from __future__ import annotations

import atexit
import collections
import contextlib
import copy
import dataclasses
import logging
import os
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from .env_truthy import env_flag

LOGGER = logging.getLogger(__name__)

#: Resolved once by :func:`_meter`: the metrics module, or ``False`` when it
#: cannot be imported (so the failed import is not repeated per publish).
_METRICS: Any = None

def _non_durable_journal_refusal() -> str | None:
    """The seam's healthy-decline token, READ from the module that mints it.

    The same rule `live_write._is_durable_decline` follows: a second copy of a
    closed vocabulary is how a rename stops matching in silence. ``None`` when the
    seam cannot be imported, and then no outcome is dismissed as a decline — the
    fail-closed direction, since a loss reported as a decline is a silent gap.
    """
    try:
        from .record_writer import NON_DURABLE_JOURNAL_REFUSAL
    except Exception:
        return None
    return NON_DURABLE_JOURNAL_REFUSAL

#: The arming flag. OFF by default and read with
#: :func:`tortoise.env_truthy.env_flag`, so an unset, blank, typo'd or garbage
#: value leaves the worker OFF — the deployment then behaves byte-identically to
#: the pre-#14185 inline fold. This mirrors :data:`tortoise.record_writer.
#: RECORD_ALLOCATE_ENV`: a writer switch is rolled by an operator who has read
#: what it changes, never by a half-typed line in a process manager.
ENV_WORKER = "TORTOISE_PROJECTION_WORKER"

#: How many queued projections the bound allows before :func:`submit` starts
#: declining (and the caller folds inline). Sized so a normal burst is absorbed
#: and a wedged fold is bounded in MEMORY — ⛔ AND THE MEMORY FIGURE IS NOT "a few
#: MB" (round-13 review, P3): an item holds one record, ``submit`` DEEP-COPIES it,
#: and a record carrying a 1536-dimensional embedding measures **12.8 KiB** per
#: copy (tracemalloc, this branch), so the default 10,000 is a ceiling of roughly
#: **125 MiB**, not a few MB — and it scales with the embedding width and the
#: record's own size. An earlier revision of this comment said "a few MB", which
#: understated the ceiling by ~30x; use this figure for capacity planning.
#: Read as an integer; an unparseable or
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


def _classify_fold_outcome(result: Any, seq: int | None) -> str | None:
    """Did this deferred fold actually write a row? ``None`` = yes (#14185).

    ⛔ THE OUTCOME DECIDES, NOT ``result is None`` ALONE (#14222 review cycle 3,
    P2-3). Two independent errors came from asking only whether the value was
    ``None``:

    * **AN ARMED REFUSAL WROTE NOTHING AND WAS COUNTED AS A PROJECTION.**
      `write_live_record` returns a `RecordWrite` for every armed path, refusals
      included — and ``circuit_open`` is the realistic one: during a record outage
      the breaker opens and EVERY deferred fold is refused without a socket. The
      loop counted those as `projected`, so its channel read
      `armed: true, failures: {}` — "caught up" — while nothing landed. That is a
      false green on the one surface an operator reads for this loop.
    * **``None`` IS NOT ONLY "UNARMED".** It is `write_live_record`'s unarmed
      verdict, but a door's own ``project`` callable can return ``None`` for its
      own reasons (the SDK returns ``None`` when its record half cannot even be
      imported, #10000). Asserting "the record was armed at ack and is not armed
      now" would then state a fact nothing checked.

    So the reason names WHAT WAS OBSERVED:
    ``record_no_outcome`` (the callable answered nothing — or answered a value
    whose outcome could not be READ, which is the same fact: nothing was
    observed), ``projection_refused`` (an armed refusal — nothing was written),
    and the raise arm's ``projection_raised`` is minted by the caller.
    `no_durable_journal` is NOT a failure: it is the seam's healthy decline for a
    lane with no journal by design (#9231), and the gate here already refuses to
    queue a non-durable log.
    """
    if result is None:
        LOGGER.warning(
            "projection_worker: the deferred record projection for journal "
            "position %s answered NOTHING — no `RecordWrite` came back, so no "
            "row was written and no error was raised. The record was armed when "
            "the write was acknowledged, so either the arming moved or the "
            "door's own record half declined; the journal holds the line and "
            "`tools/record_runner.py apply` folds it idempotently (#14185)",
            seq)
        return "record_no_outcome"
    if getattr(result, "applied", True) is False:
        refusal = getattr(result, "refusal", None)
        if refusal is not None and refusal == _non_durable_journal_refusal():
            return None          # the seam's healthy decline, not a loss (#9231)
        LOGGER.warning(
            "projection_worker: the deferred record projection for journal "
            "position %s was REFUSED (%s) — nothing was written. The journal "
            "holds the line; `tools/record_runner.py apply` folds it idempotently "
            "when the record is healthy again (#14185)",
            seq, refusal or "no reason given")
        return "projection_refused"
    return None


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
        #: THE PID THAT CREATED ``_cond`` — the fork discriminator. ⛔ NOT the pid
        #: of the last run: `stop()` has to clear that, and clearing it made a fork
        #: taken AFTER a stop look "not inherited" (#14222 review cycle 3). This one
        #: changes only when a NEW Condition is made, which is exactly when the
        #: question "is this lock mine?" changes its answer.
        self._cond_pid = os.getpid()
        #: ⛔ ORDERING FOR THE GAUGE WRITES (#14222 review cycle 4, P1). The gauges
        #: must be applied in the order the snapshots were TAKEN — a slower thread
        #: overwriting a newer snapshot is the lie cycle 1's P1-1 was filed for —
        #: and the order a lock can give you is the order the LOCK was taken, which
        #: is not the same thing. `_publish_seq` is stamped under `_cond` with the
        #: values, and `_publish` drops any snapshot older than the last one
        #: applied. `_publish_lock` then only serialises the foreign call, so no
        #: `Gauge.set` (nor the ~50 ms first-call import of `monitoring`) ever runs
        #: under the queue lock the write ACK and `stats()` use.
        self._publish_lock = threading.Lock()
        self._publish_seq = 0
        self._published_seq = 0
        #: ⛔ THE SNAPSHOT NUMBERS ARE PROCESS-LOCAL, SO THEY CARRY AN EPOCH
        #: (#14222 review cycles 5 and 6). The reset zeroes the sequence and stamps
        #: its own snapshot, which makes the reset's number the SMALLEST — so a
        #: snapshot captured before the reset compared as NEWER and undid the reset
        #: by restoring the other process's gauges. Bumping the epoch on a reset
        #: makes a pre-reset number unmatchable.
        #:
        #: ⚠️ THE GUARANTEE IS EXACT UNDER THE ``fork()`` INVARIANT AND ONLY THERE:
        #: a forking child has ONE thread, so nothing can be mid-publish when the
        #: reset runs. A publisher ALREADY INSIDE `_publish` when the epoch bumps
        #: holds a lock object the reset has replaced, so no lock excludes it — its
        #: value can land after the reset's and the reset's own snapshot is then
        #: dropped as older, leaving the gauges at the pre-reset values until the
        #: next queue transition (transient, self-correcting, and unreachable
        #: without a fork that duplicates threads). Making it airtight would need
        #: the reset to take `_publish_lock` — which is exactly what it must NOT do
        #: (that lock may be held by a thread that does not exist here). The
        #: invariant is stated rather than engineered around.
        self._publish_epoch = 0
        self._pending: collections.deque[PendingProjection] = collections.deque()
        #: The item the loop is folding RIGHT NOW. It is counted as pending lag on
        #: purpose: a position being folded is still a position the record has not
        #: taken, and on a wedged fold it is the ONLY pending item — reporting 0
        #: there would make a stalled projection read as a caught-up record.
        self._inflight: PendingProjection | None = None
        #: HOW MANY QUEUED ITEMS CARRY A JOURNAL POSITION, maintained rather than
        #: counted (#14222 review, P2). The published lag used to be
        #: ``sum(1 for i in self._pending if i.seq is not None)`` — a full walk of
        #: the deque performed on the ACK path while holding ``_cond``, which
        #: contradicted property 2 below ("a lock held for an append and
        #: nothing else") and made the ACK O(queue depth) at the very bound
        #: ``TORTOISE_PROJECTION_QUEUE_MAX`` exists to permit, on the same lock the
        #: consumer needs to pop. It is now O(1) in every direction: incremented at
        #: the ONE append, decremented at the ONE pop, and zeroed by the fork
        #: reset. Any future `_pending` mutation must maintain it — the deque is
        #: not iterated here any more, so a stale counter would be invisible.
        self._pending_positioned = 0
        self._thread: threading.Thread | None = None
        self._stopping = False
        #: ⛔ TERMINAL, unlike ``_stopping``: a shutdown sets it so a concurrent
        #: ``submit`` cannot RESTART this instance through ``_ensure_started``
        #: while the slot is being cleared (#14222 review cycle 5, ISSUE 7).
        #: ``_stopping`` alone is not enough — ``_ensure_started`` clears it when it
        #: starts a thread, so a submit that arrived between ``stop()`` completing
        #: and the slot being cleared could start a consumer on an instance nothing
        #: holds a reference to any more (two consumers, one of them unreachable).
        self._closed = False
        self._submitted = 0
        self._projected = 0
        self._failures = 0
        self._inline_fallbacks = 0
        self._atexit_installed = False

    # ── the write side ─────────────────────────────────────────────────────

    def submit(self, item: PendingProjection) -> bool:
        """Queue one projection. ``True`` queued, ``False`` ⇒ fold it inline.

        ⛔ ONE ARM RAISES, AND THE PRECISE WORDING IS LOAD-BEARING (#14222 review
        findings 6 and cycle 2-1). It is called on the write path after the
        journal has committed, so a failure here would hand the producer an
        exception for a write that persisted, leaving the record short with no
        inline fallback. :meth:`_ensure_started` calls ``thread.start()``
        unguarded, so a ``RuntimeError: can't start new thread`` (fd/thread
        exhaustion) propagates from the line below; that is answered INLINE by
        :func:`tortoise.live_write._submitted_to_worker`, which wraps this call in
        ``except Exception: return False``.

        ⛔ ``_publish`` is NOT a raising arm, and an earlier revision of this
        docstring named it — that was wrong: ``_publish_queue_state`` reaches the
        metrics writer only through :func:`_emit`, whose
        ``contextlib.suppress(Exception)`` covers the module lookup AND the call.
        It is deliberately unable to raise.

        ⚠️ THE TWO HAZARDS ARE NOT THE SAME, which is why only one folds inline. An
        exception from ``_ensure_started`` happens BEFORE the item is enqueued, so
        the caller's inline fold is the ONLY fold. ``_publish`` runs AFTER the item
        is enqueued and counted, so folding inline there would be a SECOND fold of
        the same record — the two-consumer, out-of-order shape this module's
        docstring calls a correctness hazard — not the "record short" hazard
        above. The queue's own declines (a full bound, a stopping/closed instance)
        are the only verdicts returned as ``False`` from HERE.

        A future DIRECT caller must wrap this the same way, and must not read
        "never raises" into it.
        """
        self._ensure_started()
        with self._cond:
            unstartable = self._stopping or self._closed
            if unstartable or len(self._pending) >= self.max_queue:
                self._inline_fallbacks += 1
                declined = "worker_stopping" if unstartable else "queue_full"
            else:
                self._pending.append(item)
                if item.seq is not None:
                    self._pending_positioned += 1
                self._submitted += 1
                declined = False
                snapshot = self._stamp_locked()
                self._cond.notify()
        if declined:
            _publish_inline_fallback(declined)
            return False
        self._publish(snapshot)
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

    def oldest_pending_seconds(self) -> float:
        """Just the lag in TIME, read live at collect time (#14222 cycle 5, ISSUE 1).

        A read path: ``_cond`` is only ever held here for a critical section the
        fold never occupies, so this cannot block on a wedged projection.
        """
        self._ensure_not_inherited()
        with self._cond:
            return self._oldest_age_locked()

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
                # ⛔ "EMPTY AND NOTHING IN FLIGHT" IS THE PREDICATE
                # (#14222 review cycle 2, NEW-3), and it is EXACT because the loop
                # pops and clears `_inflight` under this same lock: depth alone is
                # the wrong question (a WEDGED fold leaves an empty deque, which
                # would read as drained — see the branch below), and the counter
                # form (`projected + failures >= submitted`) is a DERIVED reading
                # of two counters rather than the queue's own state.
                # ⛔ AN EARLIER REVISION OF THIS COMMENT GAVE A DIFFERENT REASON and
                # it is false at this head, so it is withdrawn rather than kept:
                # it said the counter form is one "a forked child could never
                # satisfy — it inherits the parent's high-water marks with an empty
                # queue". `_reset_for_fork_locked` ZEROES `_submitted`, `_projected`
                # and `_failures`, so `0 + 0 >= 0` holds and no inherited mark
                # survives to spoil it. (Round-14 review, P3.)
                # ⚠️ AND THE SUITE DOES NOT PIN THIS PREDICATE: restoring the
                # counter comparison leaves
                # `test_a_drain_predicate_is_exact_even_after_a_fork` and
                # `test_a_drain_wait_reports_UNDRAINED_when_the_fold_is_wedged`
                # green, because those legs pin the RESET and the WEDGE, not the
                # comparison. So read this comment as the reason the queue's own
                # state is asked directly, and do NOT read it as evidence that a
                # mutation here would red.
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
        # ⛔ COMPARE AND CLEAR UNDER ONE HOLD, AND RE-DERIVE THE VERDICT THERE
        # (#14222 review cycle 3, P1-2). The join above runs with the lock
        # RELEASED, so a concurrent `submit` can see the stopping consumer exit,
        # clear `_stopping` and start a REPLACEMENT thread in that window.
        # Clearing `_thread` from the captured object would then discard the
        # reference to a LIVE replacement, and the next submit would start a third
        # consumer — two folding one deque, and `stop()` returning `True` while a
        # consumer was alive (which is also what made `shutdown()`'s keep-or-drop
        # decision read the wrong fact). Re-reading `self._thread` makes the
        # answer about the object as it IS, not as it was when the join started.
        with self._cond:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._thread = None
        return True

    def _ensure_started(self) -> None:
        # ⛔ BEFORE ANY `with self._cond:` — this is the fork-aware reset, and it
        # must run before the lock it may be replacing is taken (#14222 review
        # cycle 5, ISSUE 4).
        self._ensure_not_inherited()
        while True:
            cond = self._cond
            with cond:
                # ⛔ A PEER REPLACED THE CONDITION WHILE THIS THREAD WAITED: it is
                # holding the old one, which nobody else can see, so the only
                # correct move is to re-enter on the current object (#14222
                # review cycle 2, NEW-4).
                if cond is not self._cond:
                    continue
                if self._closed:
                    # ⛔ A CLOSED INSTANCE NEVER RESTARTS (#14222 review cycle 5,
                    # ISSUE 7). `shutdown()` marks it before it stops the loop, so
                    # a write that arrives mid-shutdown DECLINES (`worker_stopping`)
                    # and folds inline instead of resurrecting a consumer the
                    # singleton is about to drop.
                    return
                if self._thread is not None and self._thread.is_alive():
                    return
                else:
                    self._thread = None
                    self._stopping = False
                    thread = threading.Thread(target=self._run, name=self._name,
                                              daemon=True)
                    self._thread = thread
                    thread.start()
                    if not self._atexit_installed:
                        self._atexit_installed = True
                        self._quietly(
                            lambda: atexit.register(_drain_at_exit, self))
                    return

    def _ensure_not_inherited(self) -> None:
        """Reset this object for a NEW PROCESS, before anyone takes its lock.

        ⛔ THE FORK DECISION IS MADE WITHOUT TAKING THE LOCK IT DECIDES ABOUT
        (#14222 review cycle 5, ISSUE 4). This method used to open with
        ``with cond:`` and only then ask whether ``cond`` was inherited — so in
        the one window it exists for, where a peer thread held that Condition at
        ``fork()`` time, the child blocked on a lock with no owner in this
        process and never reached the question (executed on 3.12.3: the child's
        ``acquire(timeout=0.5)`` answered ``False``). ``_inherited()`` is an
        unsynchronised read of an attribute the child owns alone — only the
        forking thread survives a fork — so it can be asked FIRST, and the reset
        replaces the lock instead of waiting for it.

        ⛔ EVERY ENTRY POINT CALLS THIS, not only :meth:`_ensure_started`
        (#14222 review cycle 2, NEW-7). A forked child inherits the parent's
        Condition — and a Condition inherited while the parent's consumer held it
        is a lock with no owner here, so `stats()`, `wait_for_drain()` and the
        `atexit` drain would block on it forever, wedging the child's exit path
        or a health read. A child that never performs a deferred write never
        reached `_ensure_started`, which is why the reset cannot live only there.
        """
        while True:
            if not self._inherited():
                return
            cond = self._cond
            if cond is not self._cond or not self._inherited():
                continue      # a peer reset it in between; nothing left to do
            snapshot = self._reset_for_fork_locked()
            if snapshot is not None:
                self._publish(snapshot)
            return

    def _inherited(self) -> bool:
        """Is the state in hand stale for THIS process? ⛔ ASK WITHOUT THE LOCK.

        This is the only form that is safe BEFORE acquiring ``_cond``: an
        inherited Condition may be held by a thread that did not survive the fork,
        so acquiring it first is how the reset becomes unreachable (#14222 review
        cycle 5, ISSUE 4). ``_cond_pid`` is written only when a Condition is
        created, so an unsynchronised read either sees this process's own pid (no
        reset needed) or a dead process's (reset without waiting).

        ⛔ THE DISCRIMINATOR IS THE CONDITION'S OWN PID, NOT THE LAST RUN'S
        (#14222 review cycles 3 and 4). ``stop()`` has to clear whatever records
        "which run is this", and anything it clears makes a fork taken AFTER a stop
        look "not inherited" — so the child keeps a Condition a peer thread may be
        HOLDING, and blocks on it forever. ``_cond_pid`` changes only when a new
        Condition is created, which is exactly when "is this lock mine?" changes
        its answer.
        """
        return self._cond_pid != os.getpid()

    def _reset_for_fork_locked(self) -> tuple[int, int, int, int]:
        """Replace the state another process left behind, WITHOUT holding any lock.

        ⛔ THE NAME IS THE ONE THING HERE THAT IS NOT LITERAL: it is
        ``_..._locked`` for the state it replaces (the locks, the queue, the
        counters), NOT because it runs under a lock. It MUST NOT: its only caller
        is :meth:`_ensure_not_inherited`, which takes no lock on purpose — the
        inherited ``_cond`` may be held by a thread that did not survive the
        ``fork()``, so acquiring it first is exactly how this method becomes
        unreachable (cycle 5, ISSUE 4; executed on 3.12.3, the child's
        ``acquire(timeout=0.5)`` answered ``False``). A reader who believed the
        old wording and "fixed" the caller by adding ``with cond:`` back would
        re-introduce that deadlock, which is what
        ``test_the_fork_reset_does_not_wait_on_the_lock_it_REPLACES`` exists to
        catch.

        Everything is per-process and everything goes:

        * the QUEUE and the IN-FLIGHT item — the other process still owns them, and
          the in-flight one can never be cleared here, so leaving it would report
          one permanently pending position and a monotonically growing lag;
        * the COUNTERS — inherited `submitted=3, projected=1` with an empty queue
          made `wait_for_drain` unsatisfiable (cycle 2, NEW-3), and reporting
          another process's failures as ours is the attribution defect cycle 4's
          P2 named. ⛔ **ALL FOUR, and `inline_fallbacks` is the one this
          paragraph used to leave out** (review cycle 7, ISSUE 2): it is the
          fourth counter on :class:`ProjectionStats`, so a child that only
          zeroed the other three reported the PARENT's inline fallbacks — the
          same attribution defect, surviving on the one field the reset forgot;
        * **BOTH LOCKS.** ``_cond`` because a Condition inherited while the other
          process's consumer held it is a lock with no owner here; ``_publish_lock``
          for exactly the same reason (cycle 4, P2-1);
        * the PUBLISH SEQUENCE AND EPOCH, since the numbers mean nothing across
          processes — and the EPOCH bump is what makes a SNAPSHOT FROM THE PARENT
          unmatchable;
        * **THE THIRD PARTY'S LOCKS TOO**, one layer down: ``prometheus_client``
          gives every ``MutexValue`` a ``_lock``, and a LABELLED metric family one of
          its own — ⛔ the discriminator is ``labelnames``, NOT the metric type
          (``_is_parent()`` is ``self._labelnames and not self._labelvalues``, so a
          labelled ``Gauge``/``Counter``/``Histogram``/``Summary`` carries one and an
          unlabelled one of those carries none; ``Info``/``Enum`` are the exception,
          setting ``self._lock`` unconditionally in their own ``_metric_init`` and
          this module defines neither),
          plain ``threading.Lock``s a child inherits from a peer that did not
          survive the ``fork()``. ``set``, ``inc``, ``get``, ``labels`` and
          ``collect`` all take one, and a blocked lock is not an exception, so the
          suppression in ``_emit`` cannot save the child. ``monitoring`` therefore
          REPLACES them — ``ensure_projection_metrics_fork_safe``, registered at
          import as an ``os.register_at_fork(after_in_child=...)`` hook — so they
          are already fresh before this child runs any of our code, and the SAME
          reasoning as the two locks above applies to the lock nobody here owns.
          ⛔ THIS METHOD ALSO CALLS IT (review cycle 3, ISSUE 1): an earlier
          revision of this bullet said it did NOT, because the HOOK's registration
          is what covers every child before our code runs — and the merge that
          produced this tree brought that sentence and this call into the same
          method, 29 lines apart, contradicting each other. Both are true, and they
          are DIFFERENT mechanisms: the hook repairs the locks in every child,
          including the paths no helper here reaches; this call is the BELT to that
          suspender, so the reset's own guarantee holds even where the hook is not
          registered (a platform without ``os.register_at_fork``, a hook someone
          unregisters). ``_emit`` swallows every failure, the replacement is
          lock-free, and it is idempotent by pid, so the belt costs one
          ``getpid()``. ⛔ This is what makes the publish below safe, and it
          is the part an earlier revision of this fix left out: dropping the
          publish from the read path only moved the wedge onto the write and the
          collect paths (measured: the child blocked in ``MutexValue.set`` from
          ``submit`` and in ``MutexValue.get`` from ``projection_worker_gauges``),
          and a hook is what covers the paths no helper here reaches — the
          ``/metrics`` scrape calls ``generate_latest`` directly;
        * the PUBLISH SNAPSHOT, which this method RETURNS rather than applying:
          the publish is a foreign call (cycle 4, P2-2) and the caller makes it
          once the reset has returned — after the prometheus locks above are
          valid, so it cannot wedge. A child that only ever reads is exactly the
          case the republish exists for (cycle 3, P2-2: a preforked child must not
          advertise the parent's permanent backlog), so it is NOT deferred to the
          first write.
        """
        # ⛔ THIS CALL IS PART OF THE RESET, not a courtesy (#14222 review cycle 3,
        # ISSUE 1). The HOOK is the primary mechanism and the docstring above says
        # so; this is the belt to it, so the reset does not depend on the hook being
        # registered at all. `_emit` swallows every failure (a metrics problem must
        # never break the fold), the replacement is lock-free, and it is idempotent
        # by pid — so on the ordinary path this is one `getpid()`.
        _emit("ensure_projection_metrics_fork_safe")
        self._cond = threading.Condition()
        self._cond_pid = os.getpid()
        self._publish_lock = threading.Lock()
        self._pending = collections.deque()
        self._pending_positioned = 0
        self._inflight = None
        self._thread = None
        self._stopping = False
        self._submitted = 0
        self._projected = 0
        self._failures = 0
        self._inline_fallbacks = 0
        self._publish_seq = 0
        self._published_seq = 0
        self._publish_epoch += 1
        self._closed = False
        return self._stamp_locked()

    # ── the loop ───────────────────────────────────────────────────────────

    def _run(self) -> None:
        while True:
            with self._cond:
                while not self._pending and not self._stopping:
                    self._cond.wait(IDLE_WAIT_SECONDS)
                if self._stopping and not self._pending:
                    return
                item = self._pending.popleft()
                if item.seq is not None:
                    self._pending_positioned -= 1
                self._inflight = item
                self._cond.notify_all()
            # ⛔ THE FOLD RUNS WITH NO LOCK HELD. That single fact is what makes
            # `stats()` / `submit()` — and therefore any reader that asks the
            # lag question — independent of how long this projection takes.
            failure = None
            try:
                result = item.project(item.record, item.seq,
                                      journal_skipped=item.journal_skipped)
            except Exception as exc:
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
                # ⛔ THE CLASSIFIER IS INSIDE ITS OWN GUARD (round-13 review, P3):
                # it asks the door's return value what happened, and a door whose
                # `project` returns an object with a RAISING property would raise
                # HERE — outside the `try` above and BEFORE `_inflight` is cleared
                # below. That kills the consumer thread with `_inflight` still set,
                # which makes every lag gauge read a permanent phantom +1,
                # `oldest_pending_seconds` grow without bound and `wait_for_drain`
                # never answer True, while `stats().running` correctly reads False
                # and the next `submit` silently starts a replacement — the exact
                # "gauge disagrees with reality forever" class this branch treats
                # as P1-1. An unclassifiable outcome is counted as a failure: the
                # item is not retried, and the journal line stands for a replay.
                try:
                    failure = _classify_fold_outcome(result, item.seq)
                except Exception as exc:      # noqa: BLE001, RUF100
                    failure = "record_no_outcome"
                    LOGGER.warning(
                        "projection_worker: the fold's OUTCOME could not be "
                        "classified (%s: %s) for journal position %s — counted "
                        "as a failure rather than assumed to have written "
                        "(#14185)",
                        type(exc).__name__, exc, item.seq)
            # ⛔ BOTH VALUE GAUGES ARE READ HERE, AFTER `_inflight` IS CLEARED, and
            # never from a value captured at POP time (#14222 review P1-1). ⛔ TWO,
            # not three (review round 11, P3): `_stamp_locked` carries epoch, seq,
            # depth and positions — NO AGE. The age is recomputed on `collect`
            # (`PROJECTION_OLDEST_PENDING_SECONDS.set_function`), so a reader
            # chasing a wrong age belongs in `monitoring`, not here. The
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
                snapshot = self._stamp_locked()
                self._cond.notify_all()
            self._publish(snapshot)
            if failure is not None:
                _publish_failure(failure)

    def _stamp_locked(self) -> tuple[int, int, int, int]:
        """Number this snapshot, in the critical section that READ it.

        The number — not the lock — is what orders the publishes: a snapshot that
        reaches `_publish` after a newer one is DROPPED, not applied (#14222
        review cycle 4, P1). A lock would only order the publishes by when they
        queued, which is exactly the inversion that was measured.

        ⛔ IT CARRIES NO AGE (#14222 review cycle 5, ISSUE 1). Depth and positions
        are EVENT-PUBLISHED — they are facts about a moment. The age is not: it is
        a function of the clock, so a value stamped here froze at the moment the
        loop was last able to publish — and the loop cannot publish while it is
        wedged INSIDE a fold, which is the only state that gauge exists to detect.
        It is therefore computed at COLLECT time instead (see
        :func:`oldest_pending_seconds`).
        """
        self._publish_seq += 1
        return (self._publish_epoch, self._publish_seq, self._depth_locked(),
                self._pending_positions_locked())

    def _publish(self, snapshot: tuple[int, int, int, int]) -> None:
        """Apply ONE gauge snapshot, unless a NEWER one is already applied."""
        epoch, seq, depth, positions = snapshot
        with self._publish_lock:
            if epoch != self._publish_epoch:
                return          # another process's reading: not ours to apply
            if seq <= self._published_seq:
                return          # superseded: applying it would go backwards
            self._published_seq = seq
            _publish_queue_state(depth, positions)

    # ── internals ──────────────────────────────────────────────────────────

    def _depth_locked(self) -> int:
        return len(self._pending) + (1 if self._inflight is not None else 0)

    def _pending_positions_locked(self) -> int:
        """Journal positions the record has not taken. **O(1) and no iteration.**

        ⛔ THIS MUST NOT WALK THE DEQUE (#14222 review, P2). It is called from
        :meth:`submit` while ``_cond`` is held, so a scan here is an O(depth)
        critical section on the ACK path — at the ``TORTOISE_PROJECTION_QUEUE_MAX``
        bound (10 000 by default) that is the ACK's latency floor, and it delays
        the consumer's pop on the same lock. The position count is therefore
        MAINTAINED at the deque's two ends (:meth:`submit` and :meth:`_run`) and
        reset on fork; the only walk left in this class is ``len`` (O(1)).
        """
        in_flight = 1 if (self._inflight is not None
                          and self._inflight.seq is not None) else 0
        return in_flight + self._pending_positioned

    def _oldest_age_locked(self) -> float:
        if self._inflight is not None:
            return self._inflight.age_seconds()
        if not self._pending:
            return 0.0
        return self._pending[0].age_seconds()

    @staticmethod
    def _quietly(fn: Callable[[], Any]) -> None:
        with contextlib.suppress(Exception):
            fn()


# ── the process singleton ──────────────────────────────────────────────────


_WORKER: ProjectionWorker | None = None
_WORKER_LOCK = threading.Lock()


def _reinit_module_lock_after_fork() -> None:
    """Replace the SINGLETON's lock in a forked child (#14222 review, finding 1).

    :meth:`ProjectionWorker._reset_for_fork_locked` replaces the two locks an
    INSTANCE owns, and its docstring says "BOTH LOCKS" because that is how many
    the instance has. The MODULE has a third, ``_WORKER_LOCK``, and no instance
    reset can reach it: it is taken by :func:`worker` — on the DEFERRED WRITE
    path, once per deferred write — and twice by :func:`shutdown`. A child
    forked while a peer thread held it would block in :func:`worker` FOREVER,
    and ``live_write._submitted_to_worker``'s ``except Exception`` cannot answer
    a hang, so the module's "every doubt folds inline" posture would be
    unreachable at exactly the point it is claimed.

    Replaced, not acquired, for the same reason the instance reset replaces
    rather than waits: the thread holding it did not survive the fork, so the
    lock has no owner in this process. POSIX only — Windows has no ``fork`` —
    and registered at import, the same shape ``tortoise/log.py`` and
    ``tortoise/embedded_lifecycle.py`` already use for this hazard.

    ⛔ ``_WORKER`` is deliberately NOT cleared. The inherited instance carries
    ``_closed``/``_stopping`` that a ``shutdown()`` may be relying on, and every
    entry point already calls ``_ensure_not_inherited()``, whose ``_cond_pid``
    check resets the instance itself.
    """
    global _WORKER_LOCK
    _WORKER_LOCK = threading.Lock()


if hasattr(os, "register_at_fork"):        # pragma: no branch - platform
    os.register_at_fork(after_in_child=_reinit_module_lock_after_fork)


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
    # every nested object shared. A copy that cannot be made is DECLINED by the
    # arm below, never aliased: the reference is the very thing this snapshot
    # exists to remove, and a write whose line has already committed must not be
    # failed to avoid it.
    try:
        snapshot = copy.deepcopy(record)
    except Exception:
        # ⛔ DECLINE, DO NOT FALL BACK TO THE REFERENCE (#14222 review cycle 2,
        # NEW-8). The reference is the very aliasing this copy exists to remove,
        # and taking it would restore the undetectable divergence SILENTLY. Fold
        # inline instead: byte-identical to the pre-#14185 write, with no window
        # at all, and the caller pays only the fold it always used to pay.
        # ⛔ AND COUNT IT (#14222 review cycle 10, P2). The decline is a LATENCY
        # event exactly like the queue's own, so it rides the same counter; an
        # uncounted one let a process whose every record is uncopyable read as
        # byte-identically healthy on every exported signal while every ACK paid
        # the fold this worker exists to remove.
        _publish_inline_fallback("uncopyable")
        return False
    try:
        return worker().submit(PendingProjection(
            record=snapshot, seq=seq, tenant=tenant,
            journal_skipped=journal_skipped, project=project,
            enqueued_at=time.monotonic()))
    except Exception:
        # ⛔ THE ONE RAISING ARM IS COUNTED BEFORE IT PROPAGATES (#14222 review
        # cycle 10, P2). :meth:`ProjectionWorker.submit` raises only from
        # ``_ensure_started`` (``thread.start()`` under thread/fd exhaustion), and
        # :func:`tortoise.live_write._submitted_to_worker` answers it with a bare
        # ``except Exception: return False`` — fail-safe, and UNCCOUNTED, so every
        # ACK folded inline while ``stats()`` and ``inline_fallbacks{}`` read
        # exactly like a caught-up idle loop. Publish the reason here and let the
        # raise travel: the caller's ``except`` still decides the write, and this
        # wrapper does not change the answer.
        _publish_inline_fallback("queue_fault")
        raise


def stats() -> ProjectionStats:
    """Snapshot of the process worker. Safe on a read path — it holds no fold lock."""
    return worker().stats()


def oldest_pending_seconds() -> float:
    """The age of the head of the queue, for the gauge to COMPUTE at collect time.

    ⛔ THE TIME GAUGE CANNOT BE EVENT-PUBLISHED, because the event that matters is
    the absence of one (#14222 review cycle 5, ISSUE 1): the loop publishes at
    enqueue and after a fold, so while it is wedged INSIDE ``project`` the
    exported age stayed at its enqueue value for the whole wedge — quiescent, no
    new writes, no new publishes — and an ``oldest_pending_seconds > 30`` alert
    never fired. It only grew when new writes kept arriving, i.e. exactly when it
    was least needed. A Prometheus gauge is REQUIRED to represent current state,
    so this is registered through ``Gauge.set_function``: the value is recomputed
    when the metric is collected.

    0.0 when no worker has been built in this process: nothing is queued, and this
    accessor must NOT create one (a metrics read is not a write path).
    """
    instance = _WORKER
    if instance is None:
        return 0.0
    try:
        return instance.oldest_pending_seconds()
    except Exception:      # noqa: BLE001, RUF100 — a metrics read must never raise
        return 0.0


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
    # ⛔ THE SINGLETON SLOT IS TAKEN TWICE, NEVER ACROSS THE STOP (#14222 review
    # cycle 5, ISSUE 7). Round 4 held `_WORKER_LOCK` across the drain + join, and
    # `worker()` takes that SAME lock on the WRITE path — so a concurrent
    # `live_write.write()` blocked for up to `timeout` + the join timeout, against
    # this module's property 2 ("a write's ack excludes projection time"). The two
    # hazards the wide lock was introduced for are closed by the CLOSED mark
    # instead: (a) a concurrent write can no longer build a fresh instance
    # mid-stop, because the slot is still occupied; (b) it cannot resurrect the
    # retained instance either, because `_closed` makes `_ensure_started` a no-op
    # and `submit` decline (`worker_stopping` → the caller folds inline, which is
    # always safe). Writers therefore only ever wait for the two short takes.
    with _WORKER_LOCK:
        current = _WORKER
        if current is None:
            return
        current._closed = True
    joined = current.stop(drain=drain, timeout=timeout)
    with _WORKER_LOCK:
        if _WORKER is not current:
            return            # a peer shut this one down and replaced the slot
        # ⛔ PUT IT BACK WHEN THE CONSUMER IS STILL RUNNING (#14222 review cycle 2,
        # NEW-6). `stop()` returns ``False`` when a fold would not join; dropping
        # the reference anyway made the next `worker()` build a fresh instance
        # with its own thread and deque — two consumers folding one tenant's
        # records at once. The retained instance DECLINES submits with
        # `worker_stopping` and the caller folds inline, which is the honest
        # answer while a consumer is wedged. It is REOPENED so that a later
        # submit can restart it once the wedged fold finally ends — a permanently
        # closed instance would push every write onto the inline path for the
        # life of the process.
        if joined:
            _WORKER = None
        else:
            current._closed = False
    # A stop that could not join keeps the instance (a live consumer must not be
    # replaced, NEW-6); a completed one clears the slot, which is what lets the
    # NEXT `worker()` honour a changed `ENV_QUEUE_MAX` — read ONCE at
    # construction.


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


def _meter():
    """The metrics module, resolved ONCE, or ``None`` if it cannot be imported.

    ``None`` is cached too: a wheel that cannot import `monitoring` must not pay a
    failing import on every publish.
    """
    global _METRICS
    if _METRICS is None:
        try:
            from . import monitoring
        except Exception as exc:
            _METRICS = False
            # ⛔ THE CHANNEL'S DEATH IS ITSELF OBSERVABLE (#14222 review cycle 4,
            # P2). Caching the failure is right — a partially-shipped wheel must not
            # pay a failing import per publish — but doing it SILENTLY froze the
            # lag gauges at their last values while `stats()` kept moving, which is
            # the gauges-disagree-with-stats class this whole module is about, with
            # the observability channel itself dead and nothing recording it.
            with contextlib.suppress(Exception):
                LOGGER.warning(
                    "projection_worker: the metrics module could not be "
                    "imported (%s: %s) — the projection lag gauges and counters "
                    "are DISABLED for this process. The worker still folds and "
                    "still declines correctly; `projection_worker.stats()` "
                    "remains the reading (#14185)", type(exc).__name__, exc)
        else:
            _METRICS = monitoring
    return _METRICS or None


def _emit(writer: str, *args, **kwargs) -> None:
    """Call one metrics writer, swallowing EVERY failure including the lookup.

    ⛔ THE RESOLUTION IS INSIDE THE GUARD (#14222 review cycle 5, P2-2).
    ``_meter()`` used to sit outside it, and it now WARNs on an unimportable
    metrics module — so a logging filter (or anything else in that path) could
    raise out of a publisher and kill the consumer thread, on the one path this
    section promises is safe: *a metrics failure must never be able to break the
    fold or the write that queued it*. `_run` calls `_publish` outside its own
    `try`, so an escape here is a dead loop, not a lost counter.
    """
    with contextlib.suppress(Exception):
        metrics = _meter()
        if metrics is None:
            return
        getattr(metrics, writer)(*args, **kwargs)


def _publish_queue_state(depth: int, pending_positions: int) -> None:
    """Publish the two EVENT facts. The AGE is computed at collect time instead.

    ⛔ The time gauge is NOT published here (#14222 review cycle 5, ISSUE 1): a
    published age is only as fresh as the last publish, and the loop cannot
    publish while it is wedged inside a fold — see
    :func:`oldest_pending_seconds` and `monitoring`'s `set_function` registration.
    """
    _emit("record_projection_queue_state", depth=depth,
          pending_positions=pending_positions)


def _publish_failure(reason: str) -> None:
    _emit("record_projection_worker_failure", reason)


def _publish_inline_fallback(reason: str) -> None:
    _emit("record_projection_inline_fallback", reason)
