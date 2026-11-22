"""#14185 — the record's projection on its own loop.

WHAT THIS SUITE PINS, and why each leg is a separate test rather than a case in
one: the issue's four requirements are four INDEPENDENT properties, and a suite
that asserted them together would stay green while one of them regressed.

1. a write's ACK excludes the projection's time (`test_a_write_ack_excludes_...`);
2. the projection drains the log at its own rate, with lag EXPORTED as a metric
   (`test_the_lag_is_measured_in_positions_and_in_time`);
3. a deliberately STALLED projection does not slow a read
   (`test_a_stalled_projection_does_not_slow_a_read`), and the read DECLARES the
   lag instead of returning a confident short answer (#2952's class);
4. the four CORRECTNESS conditions that decide whether a write may be deferred at
   all — a journal line exists, no door reads the outcome, the log is a durable
   journal, and the tenant is carried BY VALUE — plus the RECORD's own arming,
   which is a SWITCH rather than a correctness rule (`_record_armed`, condition 5
   of six). Each must answer INLINE when it fails. ⛔ This list previously named
   "the loop is armed" as one of the four and omitted the tenant condition — the
   same membership drift `.env.example` carried (cycle-2 review, finding 2).

DB-free by design: the fold is represented by a callable, which is exactly the
boundary this module owns (it decides *when* a fold runs, never *what* it does).
The live-Postgres falsifier — one write acknowledged and then read back from the
record after the queue drained — is recorded in the PR, because a test that needs
Postgres cannot run in every lane.
"""
from __future__ import annotations

import collections
import os
import signal
import sys
import threading
import time
import types

import pytest

from tortoise import live_write, monitoring, projection_worker, record_writer

#: The tenant every DEFERRAL-EXPECTING write carries BY VALUE. It is not
#: decoration: a `threading.Thread` does not inherit the caller's contextvars, so
#: a tenant resolved from the ambient carrier at fold time is resolved again in
#: the worker, where the carrier is ABSENT. `live_write` therefore never defers a
#: write that does not carry its tenant — see
#: `test_a_write_without_a_carried_tenant_is_never_deferred`.
_TENANT = ("org-14185", "graph-14185")

#: A record DSN that is never CONNECTED to — `record_dsn()` validates its shape
#: only, and every projector in this file is a double. Present because deferral
#: requires the record to be armed as well as the loop (#14222 review P1-3).
_RECORD_DSN = "postgresql://user:pass@localhost:5432/tortoise_record_test"


# ── doubles ────────────────────────────────────────────────────────────────


class _Log:
    """An `EventLog`-like double: reports the ordinal its append produced."""

    durable = True

    def __init__(self, durable: bool = True):
        self.seq = 0
        self.durable = durable
        self.appended: list = []

    def append(self, event):
        self.seq += 1
        self.appended.append(event)
        return self.seq


class _IterationCountingDeque(collections.deque):
    """A deque that RECORDS every iteration it serves.

    #14222 review, P2. The producer's critical section had grown a full walk of
    the queue (the position count), which no wall-clock assertion can see — 10 000
    iterations cost ~0.5 ms against the ack's 250 ms bound. Making the scan
    OBSERVABLE is what turns "the lock is held for an append and nothing else"
    into a property a mutation can red: `iteration_count` must stay 0 on every
    path that runs under `_cond`.
    """

    def __init__(self, items=()):
        super().__init__(items)
        self.iteration_count = 0

    def __iter__(self):
        self.iteration_count += 1
        return super().__iter__()


class _SlowProjector:
    """A projection that blocks until released, and reports what it folded.

    The gate is an `Event`, not a sleep, so the test controls the stall exactly:
    "wedged" is a state the test can assert is STILL true after the reads
    returned, which a `sleep` cannot offer (a sleep long enough to be safe is
    also long enough to make the suite slow, and a short one is flaky).
    """

    def __init__(self):
        self.gate = threading.Event()
        self.calls: list = []
        self.in_flight = threading.Event()
        self.finished = threading.Event()

    def __call__(self, event, seq, *, journal_skipped=False):
        self.calls.append((event, seq))
        self.in_flight.set()
        self.gate.wait(30)
        self.finished.set()
        return types.SimpleNamespace(applied=True, refusal=None,
                                     outcome="applied")


@pytest.fixture
def armed(monkeypatch):
    """Arm the loop, and give every test a FRESH process worker.

    The worker is a process singleton for good reasons (one queue, one ordered
    consumer), so a test that changed `ENV_QUEUE_MAX` would otherwise inherit the
    previous test's bound — the singleton is constructed once. `shutdown()` clears
    it, which is what makes the bound below take effect.
    """
    monkeypatch.setenv(projection_worker.ENV_WORKER, "1")
    # ⛔ BOTH SWITCHES. `TORTOISE_PROJECTION_WORKER` arms the LOOP; the record is
    # armed by its own DSN, and `live_write` refuses to defer a write the record
    # was never going to take (#14222 review P1-3). The DSN is never connected
    # to here — every projector in this file is a double — it is only what
    # `record_dsn()` validates.
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, _RECORD_DSN)
    monkeypatch.delenv(projection_worker.ENV_QUEUE_MAX, raising=False)
    projection_worker.shutdown(drain=False)
    yield projection_worker
    projection_worker.shutdown(drain=True, timeout=5)


@pytest.fixture
def unarmed(monkeypatch):
    """The default deployment: no flag, so the fold is inline (pre-#14185)."""
    monkeypatch.delenv(projection_worker.ENV_WORKER, raising=False)
    projection_worker.shutdown(drain=False)
    yield projection_worker
    projection_worker.shutdown(drain=True, timeout=5)


def _fast_projector(applied: bool = True):
    def project(event, seq, *, journal_skipped=False):
        return types.SimpleNamespace(applied=applied, refusal=None,
                                     outcome="applied")
    return project


def _pending_item(seq: int, project=None) -> projection_worker.PendingProjection:
    """One queued item, built the way the chokepoint builds it."""
    return projection_worker.PendingProjection(
        record={"type": "PointAdded"}, seq=seq, tenant=_TENANT,
        journal_skipped=False, project=project or _fast_projector(),
        enqueued_at=time.monotonic())


# ── 1. the ack does not include the projection ─────────────────────────────


def test_a_write_ack_excludes_the_projection_time(armed):
    """THE issue's first target: a write is ACKNOWLEDGED fast, projected behind.

    The projector is wedged for the whole assertion, so the only way this can
    pass is if the ack never waited for it. The bound is generous (250 ms against
    a 30 s stall) because this asserts an ORDER OF MAGNITUDE — "ain't waiting" —
    not a latency budget that a loaded CI box could falsify.
    """
    slow = _SlowProjector()
    log = _Log()

    start = time.perf_counter()
    write = live_write.write({"type": "PointAdded"}, log=log, project=slow,
                         tenant=_TENANT)
    ack = time.perf_counter() - start

    assert write.deferred is True, "the write was not handed to the loop"
    assert ack < 0.25, f"the ack waited {ack * 1000:.1f} ms on a wedged fold"
    assert slow.in_flight.wait(5), "the fold never started"
    assert not slow.finished.is_set(), "the fold finished — the ack could not have excluded it"

    slow.gate.set()
    assert projection_worker.wait_for_drain(10) is True
    assert slow.finished.is_set()
    assert [c[0]["type"] for c in slow.calls] == ["PointAdded"]


def test_the_ack_never_WALKS_the_queue_it_is_acked_behind(armed, monkeypatch):
    """⛔ THE ACK'S CRITICAL SECTION IS O(1) AT THE BOUND THIS MODULE PERMITS.

    #14222 review, P2. Property 2 says ``submit`` holds ``_cond`` "for an append
    and nothing else", and that was FALSE: the snapshot stamped under the lock
    computed the position count by WALKING the deque
    (``sum(1 for i in self._pending …)``). Two consequences, both the reviewer's:
    the ack is O(queue depth) at the very bound
    ``TORTOISE_PROJECTION_QUEUE_MAX`` exists to allow (10 000 by default), on the
    same lock the consumer needs to POP — so a deep backlog both floors the ack
    and slows the drain that would shorten it.

    ⛔ THIS LEG PINS BEHAVIOUR, NOT A CLOCK. A wall-clock assertion cannot see it
    (10 000 iterations is ~0.5 ms against the 250 ms ack bound, which is why the
    review predicted the existing ack legs would stay green either way). Instead
    the queue is replaced with a deque that RECORDS every iteration, so a single
    scan is a hard failure. The counter's own correctness is pinned beside it:
    if the increment at the append is dropped, the count is one short.
    """
    instance = projection_worker.worker()
    bound = instance.max_queue
    assert bound >= 1000, (
        "this leg measures the DEFAULT bound's critical section; if the default "
        f"is ever lowered the deque walk stops mattering — bound={bound}")

    project = _fast_projector()

    counting = _IterationCountingDeque(
        [_pending_item(seq, project) for seq in range(1, bound)])
    instance._pending = counting
    instance._pending_positioned = len(counting)
    # ⛔ The consumer must not run: a live loop pops items and moves every value
    # under assertion. The pin is about the PRODUCER's critical section, and
    # `submit` is called exactly as the chokepoint calls it.
    monkeypatch.setattr(instance, "_ensure_started", lambda: None)

    try:
        assert instance.submit(_pending_item(bound, project)) is True, (
            "the queue was full at bound-1")

        assert counting.iteration_count == 0, (
            "the ACK iterated the queue while holding `_cond` — the ack is O(queue "
            "depth), and it is the consumer's own lock (property 2 says otherwise)")
        assert instance._pending_positions_locked() == bound, (
            "the maintained position count disagrees with what was enqueued")
        assert len(counting) == bound
        stats = instance.stats()
        assert (stats.pending_positions, stats.queue_depth) == (bound, bound)
    finally:
        # ⛔ PUT THE SINGLETON BACK, or the fixture's `shutdown(drain=True)`
        # spends its whole 5 s timeout folding a synthetic backlog this leg
        # built and no production path would ever queue (#14222 review round 9,
        # the category-B observation: wall-clock paid by every later run).
        # The queue is a process singleton, so a leg that leaves 9 999 items in
        # it is a cost every test after it inherits.
        instance._pending = collections.deque()
        instance._pending_positioned = 0


def test_the_settled_projection_is_reported_as_armed_not_as_an_unarmed_write(armed):
    """A deferred write is LANDED, and its outcome may not read as "unarmed".

    ``record is None`` is the seam's "nothing was armed" answer, so a deferred
    write that reused it would tell a caller the record is switched off. The
    separate `deferred` flag is what keeps the two apart — and `unprojected` is
    ``1`` because the line is one the record has not taken YET (not a loss: the
    loop's own ``failures`` counter is the loss signal).
    """
    slow = _SlowProjector()
    write = live_write.write({"type": "PointAdded"}, log=_Log(), project=slow,
                             tenant=_TENANT)

    assert write.deferred is True
    assert write.record is None
    assert write.armed is True, "a deferred write must not read as unarmed"
    assert write.applied is False, "the record has not applied it yet"
    assert write.unprojected == 1
    assert write.refusal is None
    assert write.as_dict()["deferred"] is True

    slow.gate.set()
    projection_worker.wait_for_drain(10)


# ── 2. the lag is a metric ──────────────────────────────────────────────────


def test_the_lag_is_measured_in_positions_and_in_time(armed):
    """Lag exported, not inferred: depth, POSITIONS, and the age of the head.

    Positions alone cannot tell a burst of fast folds from one wedged fold, and
    age alone cannot say how far behind the record is, so both are asserted —
    and both are read from the module AFTER the queue was filled, with the fold
    still wedged.
    """
    slow = _SlowProjector()
    log = _Log()
    for _ in range(3):
        live_write.write({"type": "PointAdded"}, log=log, project=slow,
                         tenant=_TENANT)
    assert slow.in_flight.wait(5), "the loop never picked up the first item"

    stats = projection_worker.stats()
    assert stats.running is True
    assert stats.pending_positions == 3, (
        "every one of the three journal positions is still un-folded — including "
        "the one IN FLIGHT, or a wedged fold reports a caught-up record")
    assert stats.queue_depth == 3
    assert stats.oldest_pending_seconds > 0

    gauges = monitoring.projection_worker_gauges()
    assert gauges["pending_positions"] == 3
    assert gauges["queue_depth"] == 3
    assert gauges["oldest_pending_seconds"] > 0

    # …and it CONVERGES: the lag is a live reading, not a high-water mark.
    slow.gate.set()
    assert projection_worker.wait_for_drain(10) is True
    settled = projection_worker.stats()
    assert settled.pending_positions == 0
    assert settled.projected == 3
    assert settled.failures == 0


def test_the_TIME_gauge_keeps_growing_while_the_fold_is_WEDGED(armed):
    """⛔ #14222 review cycle 5, ISSUE 1: the family that names the stall must move.

    The trio is exported to answer "is the record behind, and by how much". Depth
    and positions do that for a BACKLOG; only the age distinguishes 500 quick folds
    from 500 records stuck behind one statement. It was event-published — at
    enqueue and after a completed fold — so the ONE state it exists to report
    (a quiescent wedge: one item in flight, no new writes, no publishes) froze it
    at the enqueue value and an ``oldest_pending_seconds > 30`` alert could never
    fire. It is now computed when the metric is collected.

    The assertion is two reads ~0.25 s apart with the fold STILL wedged: an
    event-published age answers the same number twice, a live one grows.
    """
    slow = _SlowProjector()
    live_write.write({"type": "PointAdded"}, log=_Log(), project=slow,
                     tenant=_TENANT)
    assert slow.in_flight.wait(5), "the loop never picked the item up"

    first = monitoring.projection_worker_gauges()["oldest_pending_seconds"]
    time.sleep(0.25)
    second = monitoring.projection_worker_gauges()["oldest_pending_seconds"]

    assert not slow.finished.is_set(), "the fold finished — nothing was wedged"
    assert second > first, (
        f"the time gauge read {first!r} then {second!r} while the fold was wedged: "
        "a pushed age freezes at its last publish, which is the one reading this "
        "family exists to make impossible")

    slow.gate.set()
    assert projection_worker.wait_for_drain(10) is True


def test_the_depth_gauge_is_read_AFTER_the_fold_not_at_pop(armed):
    """⛔ #14222 review P1-1: a stale depth is a gate that cannot do its job.

    The pop-time depth counts the item that is about to be folded, so publishing
    it after the fold reports "1 owed" for a DRAINED queue and "1 owed" for a
    backlog of 100 — an alert on `depth > 0` fires forever on a caught-up record
    and stays quiet through a real one, while `stats()` says the opposite. Both
    readings below are taken from the exported GAUGE, not from `stats()`, because
    `stats()` was already correct when the gauge was not.
    """
    slow = _SlowProjector()
    log = _Log()
    for _ in range(4):
        live_write.write({"type": "PointAdded"}, log=log, project=slow,
                         tenant=_TENANT)
    assert slow.in_flight.wait(5)

    mid = monitoring.projection_worker_gauges()
    assert mid["queue_depth"] == 4, (
        "the mid-backlog gauge must count the in-flight item AND the queued ones")
    assert mid["queue_depth"] == projection_worker.stats().queue_depth

    slow.gate.set()
    assert projection_worker.wait_for_drain(10) is True
    settled = monitoring.projection_worker_gauges()
    assert settled["queue_depth"] == 0, (
        "a drained queue owes nothing — a stale pop-time depth would say 1 here")
    assert settled["pending_positions"] == 0
    assert settled["queue_depth"] == projection_worker.stats().queue_depth


def test_an_OLDER_snapshot_never_overwrites_a_newer_one(armed):
    """⛔ #14222 review cycle 4, P1: a lock orders the PUBLISHES, not the READS.

    The snapshot is captured under `_cond` and applied under `_publish_lock`.
    A lock around only the apply orders two publishes by when they QUEUED, so a
    thread descheduled between the capture and the apply can write an older
    value LAST — gauges reading 1 while the queue holds 2, and `stats()` saying 2.
    The sequence number stamped with the values is what makes the older one
    DROPPED instead. Built directly here because the interleaving needs to be
    constructed, not raced for.
    """
    worker = projection_worker.worker()
    item = projection_worker.PendingProjection(
        record={}, seq=1, tenant=None, journal_skipped=False,
        project=lambda *a, **k: None, enqueued_at=time.monotonic())

    with worker._cond:
        older = worker._stamp_locked()          # (seq=1, depth=0, …)
        worker._pending.append(item)
        newer = worker._stamp_locked()          # (seq=2, depth=1, …)

    worker._publish(newer)
    assert monitoring.projection_worker_gauges()["queue_depth"] == 1

    worker._publish(older)                       # arrives late; must be dropped
    assert monitoring.projection_worker_gauges()["queue_depth"] == 1, (
        "an older snapshot overwrote a newer one — the gauge went backwards")
    assert (monitoring.projection_worker_gauges()["queue_depth"]
            == worker.stats().queue_depth)

    # ⛔ PUT THE SINGLETON BACK (#14222 review cycle 5, ISSUE 6). The item was
    # appended to the PROCESS worker's own deque and nothing consumes it — no
    # thread is started by `worker()` and `wait_for_drain` does not start one — so
    # the `armed` fixture's teardown (`shutdown(drain=True, timeout=5)`) waited the
    # FULL timeout and returned False on every run of this file. The assertions
    # above need the item still queued; the clean-up belongs after them.
    with worker._cond:
        worker._pending.clear()


def test_a_snapshot_from_BEFORE_a_fork_reset_cannot_undo_it(armed, monkeypatch):
    """⛔ #14222 review cycle 5, P2-1: the snapshot numbers are PROCESS-LOCAL.

    The reset stamps its own snapshot, which makes the reset's number the SMALLEST
    — so a snapshot captured before the reset compared as NEWER and undid the reset,
    restoring the other process's gauges and freezing them until the sequence
    climbed past the stale number. The epoch makes every pre-reset number
    unmatchable.

    ⛔ THIS LEG READS THE PUBLISH CALLS, NOT THE GAUGE (#14222, round 10). A value
    assertion cannot tell "the reset republished 0" from "the stale snapshot
    overwrote it with 0", so the publish boundary is what is asserted: the reset
    publishes ONCE (its own zeros), and the stale snapshot produces no second call.
    """
    slow = _SlowProjector()
    live_write.write({"type": "A"}, log=_Log(), project=slow, tenant=_TENANT)
    assert slow.in_flight.wait(5)

    calls = []
    monkeypatch.setattr(monitoring, "record_projection_queue_state",
                        lambda **kw: calls.append(kw))

    worker = projection_worker.worker()
    with worker._cond:
        stale = worker._stamp_locked()          # captured BEFORE the reset
    worker._cond_pid = os.getpid() + 1_000_000  # a fork is observed…
    calls.clear()
    worker.stats()                              # …and the reset runs
    assert [(c["depth"], c["pending_positions"]) for c in calls] == [(0, 0)], (
        "the reset must republish the CHILD's own empty queue (cycle 3's P2-2 — a "
        f"preforked child may not advertise the parent's backlog): {calls}")

    calls.clear()
    worker._publish(stale)                      # arrives late, from the old epoch
    assert calls == [], (
        "a pre-reset snapshot reached the metrics backend and restored the other "
        f"process's backlog: {calls}")

    slow.gate.set()
    assert projection_worker.wait_for_drain(10) is True
    projection_worker.shutdown(drain=True, timeout=5)


def _runs_within(seconds: float, fn) -> bool:
    """Did *fn* return inside *seconds*? Runs it on a daemon thread so a BLOCKED
    path fails the assertion instead of hanging the suite (a blocked lock is not
    an exception — that is the whole point of the test below)."""
    done = threading.Event()

    def runner():
        try:
            fn()
        finally:
            done.set()

    threading.Thread(target=runner, daemon=True).start()
    return done.wait(seconds)


def _require_prometheus_lock(holder) -> threading.Lock:
    """The REAL ``_lock`` on a prometheus object, or a red that names the rename.

    ⛔ Review round 11, P3: these legs PLANT a fresh ``_lock`` on the object and
    then assert the repair replaced it. Under a prometheus_client that renames the
    attribute that plant is vacuous in the worst way — the leg creates a ``_lock``
    nothing in the library touches, the repair "replaces" the plants, every
    assertion holds, and the REAL lock is still inherited, so the forever-hang is
    back while the suite is green (reproduced against ``prometheus_client`` 0.26.0
    with ``_lock`` renamed to ``_mutex``). Reading the real attribute FIRST turns
    that into a red with a sentence that says what happened.

    ``pyproject.toml`` pins ``prometheus-client>=0.16`` with no upper bound, so a
    rename arrives on a dependency move rather than on a code change: whoever bumps
    it gets this message instead of a silently dead repair.
    """
    lock = getattr(holder, "_lock", None)
    assert lock is not None, (
        f"{type(holder).__name__} has no `_lock` — prometheus_client moved or "
        "renamed it, so (a) this leg no longer pins the fork repair and (b) "
        "`monitoring.ensure_projection_metrics_fork_safe` is now replacing nothing, "
        "which restores the inherited-lock wedge silently. Read the real attribute "
        "name off the installed package and update BOTH the repair and this helper.")
    return lock


def test_the_fork_reset_replaces_PROMETHEUS_own_locks_so_no_path_can_hang(armed, monkeypatch):
    """⛔ ROUND 10'S P2 + two P3s: the wedge is prometheus_client's OWN lock.

    `prometheus_client` (0.26.0) gives every `MutexValue` its own plain
    `threading.Lock`, and a metric FAMILY one only when it is LABELLED —
    `_is_parent()` (`self._labelnames and not self._labelvalues`) gates
    `self._lock = Lock()` in `MetricWrapperBase.__init__`, so an unlabelled
    `Gauge`/`Counter`/`Histogram`/`Summary` family carries NONE (the three
    unlabelled gauges this module repairs are exactly that case; `Info`/`Enum` are
    the exception and this module defines neither). A child that forks while a PEER
    THREAD holds one inherits a lock with no owner, so its next
    `set`/`get`/`labels`/`collect` blocks FOREVER — and `contextlib.suppress`
    cannot help, because a blocked lock is not an exception.

    That is not fixable by moving the replay, and an earlier revision of this fix
    tried: it dropped the publish from the reset (the READ path) and thereby moved
    the wedge onto the WRITE path (`submit`'s ACK publish) and the COLLECT path
    (`projection_worker_gauges()`, which is what `monitoring.metrics()` serves for a
    health read). The reset now replaces those third-party locks instead — the same
    reasoning it already applied to its own two.

    This leg reproduces the fork EXACTLY: it HOLDS the prometheus locks across the
    reset, then requires all three paths to return — a read, a health read, and a
    write — instead of blocking. ⛔ If the replacement is removed, this test does
    not fail slowly: those three calls never return, which is why each runs on a
    daemon thread with a deadline.
    """
    worker = projection_worker.worker()
    gauge = monitoring.PROJECTION_QUEUE_DEPTH
    positions = monitoring.PROJECTION_PENDING_POSITIONS

    # ⛔ THE OBJECTS THAT ACTUALLY CARRY A LOCK: every ``MutexValue``, plus the
    # LABELLED families — here the two ``Counter``s. ⛔ THE DISCRIMINATOR IS
    # ``labelnames``, NOT THE METRIC TYPE (measured on prometheus-client 0.26.0):
    # ``MetricWrapperBase.__init__`` takes ``self._lock`` only under
    # ``if self._is_parent():``, i.e. ``self._labelnames and not
    # self._labelvalues`` — so a LABELLED ``Gauge`` carries one and an UNLABELLED
    # ``Counter`` carries none. The three unlabelled ``Gauge``s this module repairs
    # therefore have nothing here to inherit, and planting a lock on one (as an
    # earlier revision did) is what made this leg vacuous under a rename.
    held = []
    for holder in (gauge._value, positions._value,
                   monitoring.PROJECTION_WORKER_FAILURE_COUNT,
                   monitoring.PROJECTION_INLINE_FALLBACK_COUNT):
        _require_prometheus_lock(holder)     # the REAL attribute, or a red naming it
        lock = threading.Lock()
        lock.acquire()               # the peer thread's lock, held at `fork()`
        holder._lock = lock
        held.append(lock)
    assert all(lock.locked() for lock in held)

    # A fork is now observed by BOTH readers of the pid.
    worker._cond_pid = os.getpid() + 1_000_000
    monkeypatch.setattr(monitoring, "_PROJECTION_METRICS_PID", os.getpid() + 1_000_000)
    try:
        assert _runs_within(10, worker.stats), (
            "a READ path blocked on a prometheus lock inherited across fork — the "
            "reset must replace it, not move the wedge onto another path")
        assert _runs_within(10, monitoring.projection_worker_gauges), (
            "the HEALTH read (monitoring.metrics() -> projection_worker_gauges) "
            "blocked on an inherited prometheus lock")
        assert _runs_within(10, lambda: monitoring.record_projection_queue_state(
            depth=1, pending_positions=1)), (
            "the PUBLISH blocked on an inherited prometheus lock")
    finally:
        for lock in held:
            if lock.locked():
                lock.release()

    # …and the reset really did replace them: the gauges read the child's values.
    assert monitoring.projection_worker_gauges()["queue_depth"] == 1


def test_the_RESET_ITSELF_replaces_prometheus_locks_not_a_publisher_behind_it(
        armed, monkeypatch):
    """⛔ FINDING 1 (#14222 review): the reset's docstring names a call, so the
    reset is what must MAKE it — the leg above cannot tell the two apart.

    MEASURED ON THE CODE THAT SHIPPED WITHOUT THIS LEG: `worker.stats()` triggers
    the reset, and the snapshot the reset returns is published by its CALLER
    through `monitoring.record_projection_queue_state`, whose first line is
    `ensure_projection_metrics_fork_safe()`. So every end-to-end path outside the
    reset reaches the replacement by itself, and a reset that made NO call at all
    still passed the test above — the docstring's claim ("the reset therefore also
    calls ... this is what makes the publish below safe") was carried by prose,
    not by a mechanism, and deleting the call would have red nothing.

    So this leg calls the RESET ALONE — no publish, no read, no health probe — and
    requires the inherited locks to be gone afterwards.
    """
    worker = projection_worker.worker()
    holders = (                       # the objects that really carry a `_lock`
        monitoring.PROJECTION_QUEUE_DEPTH._value,
        monitoring.PROJECTION_PENDING_POSITIONS._value,
        monitoring.PROJECTION_WORKER_FAILURE_COUNT,
        monitoring.PROJECTION_INLINE_FALLBACK_COUNT,
    )
    held = []
    for holder in holders:
        _require_prometheus_lock(holder)     # the REAL attribute, or a red naming it
        lock = threading.Lock()
        lock.acquire()               # the peer thread's lock, held at `fork()`
        holder._lock = lock
        held.append(lock)

    # A fork is observed by the metrics module too, so the pid gate cannot answer
    # "already valid here" and short-circuit the replacement.
    monkeypatch.setattr(monitoring, "_PROJECTION_METRICS_PID", os.getpid() + 1_000_000)
    try:
        worker._reset_for_fork_locked()          # THE RESET ALONE
        survivors = [holder for holder, lock in zip(holders, held, strict=True)
                     if getattr(holder, "_lock", None) is lock]
        assert not survivors, (
            "the fork reset returned without replacing prometheus's inherited "
            f"locks ({len(survivors)} survived) — the reset makes that call "
            "itself as the belt to the `os.register_at_fork` hook (which a test "
            "cannot fire by moving the pid), so a reverted call must red HERE "
            "and not only on a path that reaches the same function through "
            "`monitoring`, or through the hook")
    finally:
        for lock in held:
            if lock.locked():
                lock.release()


def test_a_fold_outcome_with_NO_READABLE_applied_is_NOT_counted_as_a_projection():
    """⛔ Round-17 P3: `_classify_fold_outcome`'s default was `True`, so an object
    with no readable `applied` fell through as a WRITTEN ROW — contradicting its own
    docstring ("an outcome that could not be read … nothing was observed") and
    `live_write`'s fail-closed `getattr(outcome, "applied", False)`.

    The raising-PROPERTY shape was already covered by the caller's `try`; this is
    the ABSENT-attribute shape, which no leg reached — a mutation swapping the
    sentinel back to `True` left the suite green. Latent today (both production
    doors answer `RecordWrite | None`), which is why it is pinned now: an
    unreadable outcome must count as "nothing observed", never as a projection,
    because a `projected` count is what makes an operator read "caught up".
    """
    assert projection_worker._classify_fold_outcome(object(), 1) == "record_no_outcome"
    assert projection_worker._classify_fold_outcome(
        types.SimpleNamespace(), 1) == "record_no_outcome"
    # …and the readable arms keep their answers: a written row is still a projection.
    assert projection_worker._classify_fold_outcome(
        types.SimpleNamespace(applied=True, refusal=None), 1) is None
    assert projection_worker._classify_fold_outcome(None, 1) == "record_no_outcome"


def test_an_INDETERMINATE_applied_is_REFUSED_not_counted_as_a_projection():
    """⛔ Round-18 P3: the sentinel covered `applied` ABSENT; `applied`
    INDETERMINATE (`None`, `0`, any falsy non-`bool`) matched NEITHER arm — not the
    `_absent` sentinel, not `applied is False` — and fell through to
    `return None`, i.e. COUNTED AS A WRITTEN ROW.

    `live_write` reads the SAME input fail-closed on both of its surfaces
    (`not getattr(outcome, "applied", False)` at :745, and `LiveWrite.applied` =
    `bool(getattr(self.record, "applied", False))`), so identity here was the last
    divergence from the polarity the round-17 finding was filed about: its own
    summary says the absent case must answer what `live_write` answers "on the
    same input".

    Reachability today is none — both production doors answer `RecordWrite |
    None`, whose `applied` is a real `bool` — so no existing row reds if the arm
    reverts to identity. That is precisely why the shape is pinned rather than
    assumed: a `projected` count is what makes an operator read "caught up", and
    "caught up" is the false green this module grades P1 elsewhere.
    """
    for falsy in (None, 0, 0.0, ""):
        assert projection_worker._classify_fold_outcome(
            types.SimpleNamespace(applied=falsy, refusal="circuit_open"),
            1) == "projection_refused", (
                f"an outcome whose `applied` is the falsy non-bool {falsy!r} was "
                "counted as a written row; `live_write` reads the same value as "
                "NOT applied, so identity-vs-truthiness is still divergent")

    # …and a TRUTHY non-bool is still a written row: the rule is truthiness, not
    # `is True`, matching `live_write`, which asks only `not getattr(..., False)`.
    assert projection_worker._classify_fold_outcome(
        types.SimpleNamespace(applied=1, refusal=None), 1) is None

    # The ONE refusal that is not a loss survives the widened arm (#9231): a
    # broader refuser must not swallow the seam's healthy decline.
    decline = projection_worker._non_durable_journal_refusal()
    assert decline, (
        "the seam's healthy-decline token could not be read, so this leg cannot "
        "distinguish a widened arm from a correct one")
    assert projection_worker._classify_fold_outcome(
        types.SimpleNamespace(applied=None, refusal=decline), 1) is None
    assert projection_worker._classify_fold_outcome(
        types.SimpleNamespace(applied=False, refusal=decline), 1) is None


def test_a_RENAMED_prometheus_lock_REDS_the_guard_instead_of_passing_vacuously():
    """The guard on the legs above must fire, and on a REAL object.

    ⛔ Review round 11, P3: the legs plant a ``_lock`` and assert it was replaced, so
    under a prometheus_client that renames ``_lock`` they would pin nothing.
    ``_require_prometheus_lock`` reads the real attribute; this leg pins its RED
    path on a holder that genuinely does not carry one.

    ⛔ NOT a ``Gauge`` family (review round 14, P3). A family's ``_lock`` depends on
    ``labelnames``, NOT on its type — ``MetricWrapperBase.__init__`` takes
    ``self._lock`` only under ``if self._is_parent():``, measured on 0.26.0 as a
    labelled ``Gauge`` HAVING one and an unlabelled ``Counter`` having none.
    Pinning this on ``PROJECTION_QUEUE_DEPTH`` therefore made the leg depend on that
    family staying UNLABELLED: a benign label added there would restore the real
    ``_lock`` and this leg would red with "DID NOT RAISE" while the fork repair was
    perfectly fine. A dependency-free holder pins the one reason that matters — the
    attribute is absent — and keeps that family's shape out of the gate.
    """
    with pytest.raises(AssertionError, match="prometheus_client moved or renamed"):
        _require_prometheus_lock(types.SimpleNamespace())


def test_a_metrics_failure_never_escapes_into_the_loop(armed, monkeypatch):
    """⛔ #14222 review cycles 5 and 6: the observability channel cannot kill the fold.

    `_run` calls `_publish` OUTSIDE its own `try`, so anything that raises on that
    path kills the consumer thread. The escalation across three review rounds is
    what makes this leg worth reading:

    * cycle 5: the WRITER was made to raise — but the writer call was already
      inside `contextlib.suppress`, so that leg was green on the broken code;
    * cycle 6: the arm that actually regressed is the LOOKUP (`_meter()` sat
      outside the guard and had just gained a WARNING), so `_meter` is what raises
      here.
    """
    worker = projection_worker.worker()
    worker.start()                      # a running consumer, before anything fails
    consumer = worker._thread

    def exploding(*args, **kwargs):
        raise RuntimeError("the metrics module is unreachable")

    # ⛔ THE DIRECT ASSERTION: `_publish` must not raise. A raise here is the
    # consumer thread's death — and because it happens AFTER the counters are
    # updated, an integration assertion alone cannot see it: the fold already
    # looks done, and the next `submit` would silently start a replacement thread.
    monkeypatch.setattr(projection_worker, "_meter", exploding)
    with worker._cond:
        snapshot = worker._stamp_locked()
    projection_worker.ProjectionWorker._publish(worker, snapshot)   # must not raise

    # ⛔ AND THE OBSERVABLE HALF: the SAME consumer keeps folding. Identity, not
    # just liveness — a dead thread is replaced on the next submit, so `running`
    # alone would pass over the churn the fix exists to prevent.
    before = projection_worker.stats().projected
    live_write.write({"type": "A"}, log=_Log(), project=_fast_projector(),
                     tenant=_TENANT)
    assert projection_worker.wait_for_drain(10) is True
    assert worker._thread is consumer, "the consumer thread was replaced"
    assert projection_worker.stats().running is True
    assert projection_worker.stats().projected == before + 1


def test_the_metrics_surface_carries_the_lag(armed):
    """`metrics()` names the lag — an operator reads it without the module."""
    metrics = monitoring.metrics()
    assert "record_projection_worker" in metrics
    block = metrics["record_projection_worker"]
    assert block["armed"] is True
    for key in ("queue_depth", "pending_positions", "oldest_pending_seconds",
                "failures", "inline_fallbacks"):
        assert key in block, f"the lag metric must publish {key}"


def test_an_unarmed_deployment_says_so_in_the_metrics_reading(monkeypatch):
    """`armed: false` is stated, so a stale gauge is not read as a live one."""
    monkeypatch.delenv(projection_worker.ENV_WORKER, raising=False)
    block = monitoring.metrics()["record_projection_worker"]
    assert block["armed"] is False


# ── 3. a stalled projection does not slow a read ────────────────────────────


def test_a_stalled_projection_does_not_slow_a_read(armed):
    """THE issue's third target, and the #2952 half of it.

    Two claims, and the second is the one that makes the first safe:

    * the READ is not slowed — it returns in bounded time while the fold is
      wedged, because the loop holds no lock the reader needs and no reader waits
      for the queue to drain;
    * the read DECLARES the lag — it does not answer "caught up" from a record
      that is behind. The declaration is the module's own `pending_positions`,
      which is the process-side reading of the same quantity
      `record_projection.journal_coverage` measures against the record (and which
      `record-reconcile --journal` reports as `unprojected_journal`).
    """
    slow = _SlowProjector()
    log = _Log()
    write = live_write.write({"type": "PointAdded"}, log=log, project=slow,
                         tenant=_TENANT)
    assert write.deferred is True
    assert slow.in_flight.wait(5), "the fold never started, so nothing is stalled"

    # The read path: a reader asking the lag question. It must NOT block on the
    # fold, which is the only reason it can be called here at all.
    start = time.perf_counter()
    readings = []
    for _ in range(50):
        stats = projection_worker.stats()
        readings.append((stats.pending_positions, stats.oldest_pending_seconds))
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, f"50 lag reads took {elapsed:.2f} s behind a wedged fold"
    assert not slow.finished.is_set(), "the fold finished — nothing was stalled"
    assert all(positions == 1 for positions, _ in readings), (
        "every reading must DECLARE the un-folded journal position; a reader that "
        "answered 0 reported a caught-up record while the record was behind")
    assert all(age > 0 for _, age in readings)

    slow.gate.set()
    assert projection_worker.wait_for_drain(10) is True


def test_a_drain_wait_reports_UNDRAINED_when_the_fold_is_wedged(armed):
    """⛔ #14222 review P1-2: an empty deque is what a WEDGED fold leaves behind.

    The loop pops its item and then blocks inside `project`, so `not pending` is
    TRUE for exactly the state a drain wait exists to report. Answering "drained"
    there calls a wedged projection finished — and every
    `assert wait_for_drain(N) is True` in this file means "the fold has finished".
    """
    slow = _SlowProjector()
    live_write.write({"type": "PointAdded"}, log=_Log(), project=slow,
                     tenant=_TENANT)
    assert slow.in_flight.wait(5), "the loop never picked the item up"

    assert projection_worker.wait_for_drain(0.3) is False, (
        "the queue is empty because the fold is IN FLIGHT — that is not drained")
    assert not slow.finished.is_set()

    slow.gate.set()
    assert projection_worker.wait_for_drain(10) is True


def test_a_wedged_stop_does_not_let_a_SECOND_consumer_start(armed):
    """⛔ #14222 review P2-2: two consumers is a correctness hazard, not a delay.

    `stop()` used to clear its thread even when the join TIMED OUT, so the next
    `submit`'s `_ensure_started` saw "no thread" and started a second consumer on
    the same deque — two folds for one tenant can land out of order, and the
    record's mutations are absolute per row. Keeping the thread set makes
    `_ensure_started` a no-op instead, and a submit then DECLINES (folding inline,
    which is always safe) with its own reason.
    """
    slow = _SlowProjector()
    live_write.write({"type": "A"}, log=_Log(), project=slow, tenant=_TENANT)
    assert slow.in_flight.wait(5)

    worker = projection_worker.worker()
    worker.stop(drain=False, timeout=0.05)
    assert slow.in_flight.is_set() and not slow.finished.is_set()

    inline_calls = []

    def inline_project(event, seq, *, journal_skipped=False):
        inline_calls.append(seq)
        return types.SimpleNamespace(applied=True, refusal=None,
                                     outcome="applied")

    stopping_before = monitoring.projection_inline_fallback_counts().get(
        "worker_stopping", 0)
    write = live_write.write({"type": "B"}, log=_Log(), project=inline_project,
                             tenant=_TENANT)
    assert write.deferred is False, "a wedged loop must decline, not queue"
    assert inline_calls == [1]

    live = [t for t in threading.enumerate()
            if t.name == "tortoise-projection-worker"]
    assert len(live) <= 1, f"a second consumer started: {live!r}"
    assert (monitoring.projection_inline_fallback_counts().get(
        "worker_stopping", 0) - stopping_before) == 1

    slow.gate.set()
    projection_worker.shutdown(drain=True, timeout=10)


def test_a_write_is_NOT_blocked_by_a_shutdown_IN_FLIGHT(armed):
    """⛔ #14222 review cycle 5, ISSUE 7: `shutdown()` must not hold the write gate.

    Round 4 held `_WORKER_LOCK` across the drain + join to keep two consumers
    from existing, and `worker()` takes that same lock on the WRITE path — so a
    concurrent `live_write.write()` blocked for the whole drain timeout, against
    the module's property 2 ("a write's ack excludes projection time"). The two
    hazards are closed by marking the instance CLOSED instead: the slot stays
    occupied for the whole stop, so no fresh instance is built, and a submit that
    arrives meanwhile DECLINES (`worker_stopping`) instead of restarting the
    instance the slot is about to drop.
    """
    slow = _SlowProjector()
    live_write.write({"type": "A"}, log=_Log(), project=slow, tenant=_TENANT)
    assert slow.in_flight.wait(5), "the loop never picked the item up"

    stopper = threading.Thread(
        target=lambda: projection_worker.shutdown(drain=True, timeout=2.0),
        name="shutdown-in-flight", daemon=True)
    stopper.start()
    time.sleep(0.2)                       # let it reach the drain wait on the wedge

    start = time.perf_counter()
    write = live_write.write({"type": "B"}, log=_Log(), project=_fast_projector(),
                             tenant=_TENANT)
    elapsed = time.perf_counter() - start

    assert write.deferred is False, "a stopping worker must decline, not queue"
    assert elapsed < 0.5, (
        f"the write waited {elapsed:.2f} s on a shutdown that is still draining — "
        "the singleton lock is being held across the stop")

    slow.gate.set()
    stopper.join(10)
    assert not stopper.is_alive()
    assert projection_worker.wait_for_drain(10) is True


def test_the_queued_record_is_a_SNAPSHOT_of_what_was_written(armed):
    """⛔ #14222 review P2-4: the fold now reads the dict AFTER the caller resumed.

    Inline, the record fold and the cache fold both read the caller's dict while
    the caller was still inside the write. Deferred, the record fold reads it
    later — and the SDK's envelope shares its NESTED values with the caller, so an
    in-place mutation between the ack and the fold would fold the record from
    content the cache never saw, undetectably. A deep copy at the queue boundary
    is what makes the item the write's own content.
    """
    slow = _SlowProjector()
    event = {"type": "PointAdded", "point": {"id": "p-1", "content": "original"}}
    write = live_write.write(event, log=_Log(), project=slow, tenant=_TENANT)
    assert write.deferred is True

    # The caller mutates its own nested value AFTER the ack — the shape the
    # envelope's sharing makes reachable.
    event["point"]["content"] = "mutated after the ack"

    slow.gate.set()
    assert projection_worker.wait_for_drain(10) is True
    folded = slow.calls[0][0]
    assert folded["point"]["content"] == "original", (
        "the fold read the caller's live dict instead of the write's content")
    assert event["point"]["content"] == "mutated after the ack"


def test_the_write_path_stays_live_while_the_fold_is_wedged(armed):
    """A wedged fold must degrade WRITES (visibly) and never the READ side.

    The queue keeps accepting, so writes keep acking; the depth is what grows,
    and it is visible. This is the backpressure contract in its normal regime —
    the bound is only reached if the fold is wedged for long enough (next test).
    """
    slow = _SlowProjector()
    log = _Log()
    acks = []
    start = time.perf_counter()
    for index in range(10):
        acks.append(live_write.write({"type": "PointAdded", "i": index},
                                     log=log, project=slow, tenant=_TENANT))
    elapsed = time.perf_counter() - start

    assert all(w.deferred is True for w in acks)
    assert elapsed < 2.0, f"10 acks took {elapsed:.2f} s behind a wedged fold"
    assert projection_worker.stats().pending_positions == 10

    slow.gate.set()
    assert projection_worker.wait_for_drain(10) is True
    assert projection_worker.stats().projected == 10


# ── 4. the four correctness conditions (+ the armed switch) ────────────────


def test_a_record_only_write_is_never_deferred(armed, monkeypatch):
    """⛔ #14002: on a cut-over graph there is NO journal line.

    The record's own commit is the write's only durable copy, so deferring it
    would acknowledge a write that exists in neither store for the length of the
    lag. This arm must fold INLINE even with the loop armed.
    """
    from tortoise import record_writer

    # The chokepoint asks THIS question through `record_writer` (#14002), so
    # answering it here is answering the real predicate, not a stand-in for it.
    monkeypatch.setattr(record_writer, "record_owns_position",
                        lambda **kwargs: True)
    seen = []

    def project(event, seq, *, journal_skipped=False):
        seen.append((seq, journal_skipped))
        return types.SimpleNamespace(applied=True, refusal=None,
                                     outcome="applied")

    # ⛔ THE TENANT IS SUPPLIED ON PURPOSE (#14222 review cycle 5, ISSUE 2). The
    # gate order in `live_write._submitted_to_worker` is: condition 1
    # (`journal_skipped`) and the `durable`/`record_sink` clauses FIRST, in one
    # `if`, and the tenant gate SECOND — so a write with no `tenant=` is declined
    # by a LATER gate, which MASKS this one. Deleting condition 1 would then
    # leave the tenant gate answering `False` anyway and this row would pass even
    # with the behaviour reverted. Every deferral-expecting write in this file
    # carries `_TENANT`; a condition test that does not is testing the tenant
    # gate instead. (Cycle-7 review, finding 4: an earlier revision of this
    # comment stated the order backwards — tenant BEFORE `journal_skipped` — which
    # is not what the code does.)
    write = live_write.write({"type": "PointAdded"}, log=_Log(), project=project,
                             tenant=_TENANT)
    assert write.appended is False, "the append should have been skipped"
    assert write.deferred is False, "a record-only write must never be deferred"
    assert seen == [(None, True)], "the fold did not run inline"

    # …and prove the loop really was not asked: arm a wedged projector-shaped
    # worker and assert its queue is empty.
    assert projection_worker.stats().submitted == 0


def test_a_write_whose_outcome_a_door_reads_is_never_deferred(armed):
    """⛔ #10000/#10021: the capture writer branches on `RecordWrite.applied`.

    A deferred write has not applied, so handing that consumer a deferred outcome
    would put the turn in the graph AND the record (the store decision is made
    BEFORE the write, and the after-the-write arm re-checks `applied`). The sink
    must therefore receive a real `RecordWrite` and the fold must run inline.
    """
    sink: list = []
    outcome = types.SimpleNamespace(applied=True, refusal=None, outcome="applied")

    # `tenant=` as well: without it the `tenant is None` gate would decline the
    # write too, so the `record_sink` condition (in the FIRST gate, the
    # `journal_skipped or record_sink is not None or not durable` one) would never
    # be the reason and this row would pass with it deleted (ISSUE 2). ⛔ Cited by
    # gate TEXT, not `file:line`: both numbers here were wrong by five lines.
    write = live_write.write(
        {"type": "PointAdded"}, log=_Log(), record_sink=sink, tenant=_TENANT,
        project=lambda e, s, journal_skipped=False: outcome)

    assert write.deferred is False
    assert sink == [outcome], "the sink must receive a REAL outcome, never None"
    assert projection_worker.stats().submitted == 0


def test_a_write_without_a_carried_tenant_is_never_deferred(armed):
    """⛔ The one condition that could change WHAT a write means, not just WHEN.

    A `threading.Thread` does not inherit the caller's contextvars, so a tenant
    the fold would have resolved from the ambient carrier (`use_record_tenant`)
    is simply absent on the worker — leaving `record_tenant()`'s env fallback to
    answer, which is the #9192 mis-scoping class reached through a thread instead
    of a parameter. `api.EventAPI._emit` is the live shape: it passes no `tenant=`
    and relies on the ambient carrier, so it must keep folding inline.
    """
    called = []
    write = live_write.write(
        {"type": "PointAdded"}, log=_Log(),
        project=lambda e, s, journal_skipped=False: (
            called.append(s),
            types.SimpleNamespace(applied=True, refusal=None,
                                  outcome="applied"))[1])
    assert write.deferred is False
    assert called == [1], "the fold must run inline, in the tenant's own thread"
    assert projection_worker.stats().submitted == 0


def test_a_deferred_write_requires_the_RECORD_to_be_armed_too(armed, monkeypatch):
    """⛔ TWO SWITCHES, and the second one is not decoration (#14222 review P1-3).

    `TORTOISE_PROJECTION_WORKER` arms the LOOP; the record is armed by its own
    DSN. With the loop on and the record off, `write_live_record` answers "nothing
    was armed" — so a deferred write could only report either `armed=False` (a lie
    about a queued write) or `armed=True, unprojected=1` (a loss claim for a write
    nothing was ever going to take, which
    `tools/claim_dense_leg_13732.py` counts as an unprojected position). Asking
    the record's own arming keeps that deployment byte-identical instead: it folds
    inline and reports `armed=False, unprojected=0`, exactly as before this loop.
    """
    monkeypatch.delenv(record_writer.RECORD_DSN_ENV, raising=False)
    called = []
    write = live_write.write(
        {"type": "PointAdded"}, log=_Log(), tenant=_TENANT,
        project=lambda e, s, journal_skipped=False: (
            called.append(s),
            types.SimpleNamespace(applied=True, refusal=None,
                                  outcome="applied"))[1])

    assert write.deferred is False, "the record was not armed — nothing to defer to"
    assert called == [1], "the fold must run inline"
    assert write.unprojected == 0, "an unarmed record owes no position"
    assert projection_worker.stats().submitted == 0


def test_a_scratch_log_is_never_deferred(armed):
    """#9231: a scratch buffer is declined by the seam, so queueing it is pointless."""
    # `tenant=` as well, or the tenant gate masks `durable=False` (ISSUE 2).
    write = live_write.write({"type": "PointAdded"}, log=_Log(durable=False),
                             project=_fast_projector(), tenant=_TENANT)
    assert write.deferred is False
    assert projection_worker.stats().submitted == 0


def test_an_unarmed_deployment_folds_inline_exactly_as_before(unarmed):
    """THE default: no flag, no loop, byte-identical to the pre-#14185 write."""
    called = []
    write = live_write.write(
        {"type": "PointAdded"}, log=_Log(),
        project=lambda e, s, journal_skipped=False: (
            called.append(s),
            types.SimpleNamespace(applied=True, refusal=None,
                                  outcome="applied"))[1])
    assert write.deferred is False
    assert called == [1]
    assert projection_worker.armed() is False


def test_the_gate_does_not_parse_the_record_DSN_when_the_loop_is_OFF(monkeypatch):
    """(#14222 review, finding 8) The CHEAPEST switch is asked first.

    `record_writer.record_dsn()` is UNCACHED — it reads the environment, checks
    the scheme and applies the host rule — while the loop's own arming is a bare
    `env_flag` read. The chokepoint asked the expensive one first, so the DEFAULT
    deployment (loop off, record armed) paid a DSN parse on every ACK for a fold it
    was never going to defer. The conjunction is unchanged; only its cheapest test
    moved in front.

    ⛔ THIS LEG EXISTS BECAUSE THE FIRST CUT OF THE FIX WAS IN THE WRONG PLACE —
    the `armed()` check was added BELOW `_record_armed()` and the whole suite
    stayed green, because nothing pinned the ORDER. Spying on `record_dsn` here is
    what makes the order a control rather than a comment.
    """
    monkeypatch.delenv(projection_worker.ENV_WORKER, raising=False)
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    parses = []
    real = record_writer.record_dsn
    monkeypatch.setattr(
        record_writer, "record_dsn",
        lambda *a, **kw: (parses.append(1), real(*a, **kw))[1])

    deferred = live_write._submitted_to_worker(
        event={"type": "PointAdded"}, seq=1, project=_fast_projector(),
        durable=True, journal_skipped=False, tenant=_TENANT, record_sink=None)

    assert deferred is False, "the loop is off — nothing to defer to"
    assert parses == [], (
        "the loop's flag is off, so the gate declined before asking anything —"
        " an uncached `record_dsn()` parse here is the per-ACK cost this reorder"
        f" removes (parses={len(parses)})"
    )


def test_the_gate_still_asks_the_record_when_the_loop_is_ON(armed, monkeypatch):
    """...and the DSN IS consulted once the loop is armed (#14222 finding 8).

    The reorder must not have turned the record's arming into an unreachable
    condition: with the loop on, the same call reaches `_record_armed()` — so the
    two-switch conjunction is proven from the spy's side, not only from the
    outcome.
    """
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, "postgresql://u:p@127.0.0.1:1/record")
    parses = []
    real = record_writer.record_dsn
    monkeypatch.setattr(
        record_writer, "record_dsn",
        lambda *a, **kw: (parses.append(1), real(*a, **kw))[1])

    live_write._submitted_to_worker(
        event={"type": "PointAdded"}, seq=1, project=_fast_projector(),
        durable=True, journal_skipped=False, tenant=_TENANT, record_sink=None)

    assert parses == [1], (
        "with the loop armed the gate must still ask whether the RECORD is armed —"
        f" exactly once (parses={len(parses)})"
    )


def test_the_queue_bound_falls_back_to_inline_and_counts_it(armed, monkeypatch):
    """A FULL queue never drops a record and never blocks forever.

    ⛔ A drop would be a silently short record — the item's journal line exists and
    only a metric-reading human would notice the gap — so the bound's visible cost
    is a SLOWER ACK (the caller folds inline, as it did before #14185) plus a
    counter that names the reason.
    """
    monkeypatch.setenv(projection_worker.ENV_QUEUE_MAX, "1")
    projection_worker.shutdown(drain=False)  # the bound is read at construction

    slow = _SlowProjector()
    log = _Log()
    first = live_write.write({"type": "A"}, log=log, project=slow, tenant=_TENANT)
    assert first.deferred is True
    assert slow.in_flight.wait(5), "the loop never picked up the first item"
    # B is QUEUED (the deque holds 1 = the bound) and cannot be picked up: the
    # loop is wedged on A. That is what makes C's refusal deterministic rather
    # than a race with how fast the loop empties the deque.
    second = live_write.write({"type": "B"}, log=log, project=slow, tenant=_TENANT)
    assert second.deferred is True
    assert projection_worker.stats().queue_depth == 2

    inline_calls = []
    before = monitoring.projection_inline_fallback_counts().get("queue_full", 0)

    def inline_project(event, seq, *, journal_skipped=False):
        inline_calls.append(seq)
        return types.SimpleNamespace(applied=True, refusal=None,
                                     outcome="applied")

    third = live_write.write({"type": "C"}, log=log, project=inline_project,
                             tenant=_TENANT)
    assert third.deferred is False, "the bound must fall back to inline"
    assert inline_calls == [3], "the fallback must actually FOLD, not drop"
    # ⛔ A DELTA: these families are process-global and never reset (a total that
    # can reset cannot be alerted on), so an absolute assertion here would be
    # measuring every earlier test in the file.
    assert (monitoring.projection_inline_fallback_counts().get("queue_full", 0)
            - before) == 1

    slow.gate.set()
    assert projection_worker.wait_for_drain(10) is True
    stats = projection_worker.stats()
    assert stats.submitted == 2
    assert stats.projected == 2
    assert stats.failures == 0
    assert stats.inline_fallbacks == 1


def test_a_failing_deferred_fold_is_counted_and_the_loop_survives(armed):
    """One raise must not kill the loop or lose the NEXT write.

    The journal holds every queued line, so a failed fold is a named gap a replay
    heals (`tools/record_runner.py apply`) — never a reason to stop draining. The
    counter is the loop's own; it is NOT `record_write_failures`, which the seam
    writes for its own refusals (double-counting one record there would make the
    two channels disagree).
    """
    attempts = []
    before = monitoring.projection_worker_failure_counts().get(
        "projection_raised", 0)

    def project(event, seq, *, journal_skipped=False):
        attempts.append(seq)
        if seq == 1:
            raise RuntimeError("the record went away")
        return types.SimpleNamespace(applied=True, refusal=None,
                                     outcome="applied")

    log = _Log()
    live_write.write({"type": "A"}, log=log, project=project, tenant=_TENANT)
    live_write.write({"type": "B"}, log=log, project=project, tenant=_TENANT)
    assert projection_worker.wait_for_drain(10) is True

    stats = projection_worker.stats()
    assert attempts == [1, 2], "the loop stopped after the failure"
    assert stats.failures == 1
    assert stats.projected == 1
    assert (monitoring.projection_worker_failure_counts().get(
        "projection_raised", 0) - before) == 1


def test_a_fold_whose_OUTCOME_cannot_be_read_is_counted_not_absorbed(armed):
    """⛔ #14222 round-13 review, P3: the classifier sat OUTSIDE the fold's `try`.

    `_classify_fold_outcome` asks the door's return value what happened
    (`getattr(result, "applied", True)` / `getattr(result, "refusal", None)`), so
    a `project` callable returning an object with a RAISING property raised there —
    outside the guard around `item.project`, and BEFORE `_inflight` was cleared.
    That kills the consumer thread while `_inflight` still names an item, so every
    lag gauge reads a permanent phantom +1, `oldest_pending_seconds` grows without
    bound, `wait_for_drain` never answers True, and the next `submit` silently
    starts a replacement consumer — a gauge that disagrees with reality forever.

    The leg drives exactly that shape: a first fold whose outcome object raises,
    then a second fold that must still run. ⛔ If the classifier is moved back
    outside its guard, `wait_for_drain` below never returns True (the assertion
    fails on the drain, not on a phantom gauge), which is the observable the
    defect produces.
    """
    attempts = []

    class _OutcomeThatCannotBeRead:
        @property
        def applied(self):
            raise RuntimeError("the door's outcome is unreadable")

    def project(event, seq, *, journal_skipped=False):
        attempts.append(seq)
        if seq == 1:
            return _OutcomeThatCannotBeRead()
        return types.SimpleNamespace(applied=True, refusal=None,
                                     outcome="applied")

    before = monitoring.projection_worker_failure_counts().get(
        "record_no_outcome", 0)
    log = _Log()
    live_write.write({"type": "A"}, log=log, project=project, tenant=_TENANT)
    live_write.write({"type": "B"}, log=log, project=project, tenant=_TENANT)

    assert projection_worker.wait_for_drain(10) is True, (
        "the consumer did not finish the queue: an outcome the classifier could "
        "not read must be COUNTED, not able to kill the loop with `_inflight` set")
    stats = projection_worker.stats()
    assert attempts == [1, 2], "the loop stopped on the unreadable outcome"
    assert stats.failures == 1, (
        "an unclassifiable fold must be counted as a failure, never assumed to "
        "have written a row")
    # ⛔ THE OPERATOR-VISIBLE COUNTER, not only the in-process stat (#14222
    # round-14 review, P3). `stats.failures` is the loop's own tally; a change
    # that dropped `_publish_failure` on this arm would leave every assertion
    # above green while the metric an operator actually reads stayed flat. The
    # sibling leg above pins `projection_raised` in exactly this shape.
    assert (monitoring.projection_worker_failure_counts().get(
        "record_no_outcome", 0) - before) == 1
    assert stats.projected == 1
    assert stats.queue_depth == 0 and stats.pending_positions == 0


def test_the_fold_runs_in_journal_order(armed):
    """ONE consumer, in order — a second consumer is a correctness hazard.

    Two concurrent folds for one tenant can land out of order, and the record's
    mutations are absolute per row, so ordering is not only a delay question.
    """
    order: list = []
    release = threading.Event()

    def project(event, seq, *, journal_skipped=False):
        if seq == 1:
            release.wait(10)
        order.append(seq)
        return types.SimpleNamespace(applied=True, refusal=None,
                                     outcome="applied")

    log = _Log()
    for index in range(5):
        live_write.write({"type": "PointAdded", "i": index}, log=log,
                         project=project, tenant=_TENANT)
    release.set()
    assert projection_worker.wait_for_drain(10) is True
    assert order == [1, 2, 3, 4, 5]


# ── 4b. the loop's own bookkeeping tells the truth ───────────────────────────


def test_a_fold_that_wrote_NOTHING_is_a_failure_not_a_projection(armed):
    """⛔ #14222 review cycle 2, NEW-2: the loop's success counter must not lie.

    `write_live_record` returns ``None`` ONLY when the record is unarmed — and on
    this arm `live_write` PROVED it armed before queueing, so the answer means the
    arming changed between the ack and the fold (a lane that scopes
    `TORTOISE_RECORD_DSN` for one run and restores it in a `finally` is the live
    shape). Counting that as `projected` reported a fold that wrote nothing, and
    the seam's unarmed arm is deliberately SILENT — no counter, no log — so the
    loop is the only surface that could see it.
    """
    before = monitoring.projection_worker_failure_counts().get(
        "record_no_outcome", 0)

    def project(event, seq, *, journal_skipped=False):
        return None  # exactly what `write_live_record` answers when unarmed

    live_write.write({"type": "PointAdded"}, log=_Log(), project=project,
                     tenant=_TENANT)
    assert projection_worker.wait_for_drain(10) is True

    stats = projection_worker.stats()
    assert stats.projected == 0, "a fold that wrote nothing is not a projection"
    assert stats.failures == 1, "…and it is the loss signal the caller reads"
    assert (monitoring.projection_worker_failure_counts().get(
        "record_no_outcome", 0) - before) == 1


def test_an_ARMED_REFUSAL_is_a_failure_not_a_projection(armed):
    """⛔ #14222 review cycle 3, P2-3: `circuit_open` is the realistic outage arm.

    `write_live_record` returns a `RecordWrite` for EVERY armed path, refusals
    included — so during a record outage the breaker opens and every deferred fold
    is refused without a socket. Classifying on `result is None` alone counted
    those as `projected`, so the loop's channel read
    `armed: true, failures: {}` — "caught up" — through a total outage. The
    outcome decides now.
    """
    before = monitoring.projection_worker_failure_counts().get(
        "projection_refused", 0)

    def project(event, seq, *, journal_skipped=False):
        return types.SimpleNamespace(applied=False, refusal="circuit_open",
                                     outcome="refused")

    live_write.write({"type": "PointAdded"}, log=_Log(), project=project,
                     tenant=_TENANT)
    assert projection_worker.wait_for_drain(10) is True

    stats = projection_worker.stats()
    assert stats.projected == 0, "a refused fold wrote nothing"
    assert stats.failures == 1
    assert (monitoring.projection_worker_failure_counts().get(
        "projection_refused", 0) - before) == 1


def test_the_seams_healthy_decline_is_not_counted_as_a_loss(armed):
    """#9231's `no_durable_journal` is a DECLINE, not a loss — the seam's own rule."""
    before = monitoring.projection_worker_failure_counts()

    def project(event, seq, *, journal_skipped=False):
        return types.SimpleNamespace(applied=False,
                                     refusal="no_durable_journal",
                                     outcome="refused")

    live_write.write({"type": "PointAdded"}, log=_Log(), project=project,
                     tenant=_TENANT)
    assert projection_worker.wait_for_drain(10) is True
    assert projection_worker.stats().failures == 0, (
        "nothing a replay could restore is missing — this is not a loss")
    assert monitoring.projection_worker_failure_counts() == before


def test_a_drain_predicate_is_exact_even_after_a_fork(armed):
    """⛔ #14222 review cycle 2, NEW-3: a high-water mark is not work.

    `empty AND nothing in flight` is the queue's OWN state, and it is exact
    because the loop pops and clears the in-flight item under this same lock.
    The counter form (`projected + failures >= submitted`) is a DERIVED reading
    of two counters instead, so a stale value of either decides a drain.
    ⛔ THE REASON ORIGINALLY GIVEN HERE IS WITHDRAWN (round 17), because it is
    false at this head: it said the counter form is one a forked child could
    never satisfy, since the child inherits the parent's high-water marks —
    `_reset_for_fork_locked` ZEROES `_submitted`, `_projected` and `_failures`,
    so `0 + 0 >= 0` holds and no inherited mark survives to spoil it.
    ⛔ SO THIS LEG PINS THE RESET, NOT THE PREDICATE: it also sets a foreign
    `_cond_pid`, so the first `_ensure_not_inherited()` inside `wait_for_drain`
    resets and zeroes the counters BEFORE the drain predicate is ever evaluated —
    no mark is left to evaluate under — and restoring the counter form leaves this
    leg green (measured). Read it as evidence that the child's counters are its
    own, never as evidence that a mutation of the comparison would red.
    """
    worker = projection_worker.worker()
    worker._submitted, worker._projected = 9, 1   # an inherited high-water mark
    worker._pending.clear()
    worker._inflight = None
    worker._cond_pid = os.getpid() + 1_000_000     # pretend another process

    start = time.perf_counter()
    assert projection_worker.wait_for_drain(2) is True
    assert (time.perf_counter() - start) < 0.2, (
        "a drained worker must not wait out the timeout")
    assert projection_worker.stats().submitted == 0, (
        "the child's counters are its own — the parent's are not its work")


def test_a_forked_child_REPUBLISHES_its_own_state_on_any_path(armed, monkeypatch):
    """⛔ #14222 review cycle 3, P2-2: a child must not inherit the parent's LAG.

    The fork reset empties the queue and zeroes the counters — and the three GAUGES
    are per-process readings too. Leaving them at the parent's last published value
    made a preforked child advertise a PERMANENT backlog it does not have, while
    `stats()` reported 0; the gauges and `stats()` disagreeing is exactly the
    invariant cycle 1's P1-1 was about.

    ⛔ AN EARLIER REVISION OF THIS FIX BROKE THAT ON PURPOSE AND WAS WRONG (rounds
    9/10). It stopped the reset publishing, on the theory that a READ path must not
    reach the metrics backend — which is true, but the backend's lock is what had
    to be FIXED (`test_the_fork_reset_replaces_PROMETHEUS_own_locks_so_no_path_can_hang`
    covers that), not avoided. Not publishing left a read-only child advertising the
    parent's backlog AND, in the same `metrics()` dict, an age computed live as
    `0.0` — a combination that cannot occur in any live process.

    So the child republishes from the reset, and the epoch keeps the parent's
    snapshots from landing on top of it.
    """
    slow = _SlowProjector()
    log = _Log()
    for _ in range(3):
        live_write.write({"type": "PointAdded"}, log=log, project=slow,
                         tenant=_TENANT)
    assert slow.in_flight.wait(5)
    assert monitoring.projection_worker_gauges()["pending_positions"] == 3

    worker = projection_worker.worker()
    worker._cond_pid = os.getpid() + 1_000_000     # pretend this was inherited
    # ⛔ THE FOURTH COUNTER, and the one the reset used to forget (review cycle 7,
    # ISSUE 2). It is the parent's attribution, not the child's work — the same
    # "reporting another process's failures as ours" defect `_reset_for_fork_locked`
    # exists for, on the field its own body omitted.
    worker._inline_fallbacks = 7

    stats = projection_worker.stats()               # triggers the fork reset
    assert stats.pending_positions == 0
    assert stats.inline_fallbacks == 0, (
        "a child reports its OWN inline fallbacks — an inherited count is another "
        f"process's work claimed as its own: {stats.inline_fallbacks}")
    assert monitoring.projection_worker_gauges()["pending_positions"] == 0, (
        "a child must not export the parent's phantom backlog — cycle 3's P2-2")
    assert monitoring.projection_worker_gauges()["oldest_pending_seconds"] == 0.0

    slow.gate.set()
    assert projection_worker.wait_for_drain(10) is True
    projection_worker.shutdown(drain=True, timeout=5)


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")   # fork() with peers
def test_the_MODULE_lock_is_replaced_in_a_forked_child():
    """⛔ #14222 review, finding 1: `_WORKER_LOCK` is MODULE-level, so the
    per-instance fork reset cannot reach it.

    `_reset_for_fork_locked` replaces the two locks an INSTANCE owns — `_cond`
    and `_publish_lock` — and says "BOTH LOCKS", which is how many the instance
    has. The module has a THIRD, `_WORKER_LOCK`, taken by `worker()` on the
    deferred write's ACK path (once per deferred write) and twice by
    `shutdown()`. A child forked while a peer thread held it blocked inside
    `worker()` FOREVER, and `live_write._submitted_to_worker`'s
    `except Exception` cannot answer a hang — so "every doubt folds inline" was
    unreachable exactly where it is claimed. The `after_in_child` hook replaces
    it.

    Forked probe, in-suite: the parent holds `_WORKER_LOCK` while it forks; the
    child asks for the singleton and must answer. ⛔ WITH THE HOOK REMOVED this
    child does NOT answer 0 — it blocks on the inherited lock until its own
    alarm fires. Measured on this box, the alarm's disposition is converted, so
    the mutant reports exit **4** (the `except BaseException` arm) rather than a
    raw `SIGALRM`; both assertions below therefore fail on the mutant and neither
    depends on which arm the platform takes.
    """
    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with projection_worker._WORKER_LOCK:
            holding.set()
            release.wait(10)

    holder = threading.Thread(target=hold, name="worker-lock-holder", daemon=True)
    holder.start()
    assert holding.wait(5), "the peer never took the module lock"

    pid = os.fork()
    if pid == 0:                                   # ── child ──
        code = 3
        try:
            signal.alarm(10)
            projection_worker.worker()             # takes `_WORKER_LOCK`
            code = 0
        except BaseException:                      # noqa: BLE001, RUF100
            code = 4
        finally:
            os._exit(code)                         # never unwind into pytest

    release.set()
    holder.join(5)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, (
        "the forked child did not reach the singleton: "
        f"exited={os.WIFEXITED(status)} status="
        f"{os.WEXITSTATUS(status) if os.WIFEXITED(status) else 'signalled'} — it "
        "was still blocked on `_WORKER_LOCK`, the MODULE lock its peer held at "
        "fork(), so the `after_in_child` hook never replaced it")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")   # fork() with peers
def test_the_fork_hook_replaces_prometheus_locks_before_the_METRICS_SCRAPE():
    """⛔ #14222 review round 11, P2-1: the repair was on CALL SITES, and the
    scrape path is not one of them.

    `ensure_projection_metrics_fork_safe()` was reached from every projection-metric
    helper that publishes or reads these families (⛔ no count is stated: the
    number moves whenever a helper is added, and a stale count beside a coverage
    argument certifies something false — round-13 review, P3), which covers the
    publish arm, the health read and
    that read's `collect()`. It did NOT cover the TEXT exposition: `/metrics`
    calls `generate_latest(REGISTRY)` directly (`monitoring._Handler.do_GET`), and
    that walk takes the same inherited locks — `_child_samples` ->
    `MutexValue.get` -> `with self._lock` for an unlabelled family, and
    `_multi_samples`' own `with self._lock` for a labelled one. So the first
    scrape from a child forked while a peer held one of these locks blocked
    FOREVER, on the one surface Prometheus actually reads.

    The repair is now an `os.register_at_fork(after_in_child=...)` hook, so the
    locks are replaced before any of our code runs in the child and NO path —
    scrape included — can reach an inherited one.

    Forked probe, in-suite: the parent's peer thread HOLDS
    `PROJECTION_QUEUE_DEPTH._value._lock` across the `fork()`; the child must
    produce the exposition. ⛔ WITH THE HOOK REMOVED the child does not answer 0:
    it blocks inside `MutexValue.get` until its own alarm fires, and the
    `except BaseException` arm reports exit **4** (never a raw `SIGALRM`), so the
    assertion below reds on the mutant without depending on the platform's
    disposition.
    """
    gauge = monitoring.PROJECTION_QUEUE_DEPTH
    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with _require_prometheus_lock(gauge._value):   # the REAL lock, not a plant
            holding.set()
            release.wait(10)

    holder = threading.Thread(target=hold, name="prometheus-value-holder",
                              daemon=True)
    holder.start()
    assert holding.wait(5), "the peer never took the prometheus value lock"

    pid = os.fork()
    if pid == 0:                                   # ── child ──
        code = 3
        try:
            signal.alarm(10)
            monitoring.generate_latest()           # the `/metrics` path, verbatim
            code = 0
        except BaseException:                      # noqa: BLE001, RUF100
            code = 4
        finally:
            os._exit(code)                         # never unwind into pytest

    release.set()
    holder.join(5)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, (
        "the forked child could not produce the /metrics exposition: "
        f"exited={os.WIFEXITED(status)} status="
        f"{os.WEXITSTATUS(status) if os.WIFEXITED(status) else 'signalled'} — it "
        "was still blocked in `MutexValue.get` on the prometheus lock its peer "
        "held at fork(), so no `after_in_child` hook replaced it")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")   # fork() with peers
def test_the_fork_hook_replaces_the_REGISTRY_lock_the_scrape_takes_FIRST():
    """⛔ #14222 review round 12, P3: `registry.collect()` takes the REGISTRY's own
    lock BEFORE it iterates a single collector.

    The repair replaced the locks of the five projection families — ⛔ NOT "every
    family this module's exposure path touches": `_handle_endpoint` touches
    `REQUEST_COUNT` / `REQUEST_LATENCY` BEFORE `generate_latest`, and neither is
    repaired (see `monitoring`'s residual), which is why the leg ABOVE holds a
    projection family and THIS leg holds the registry. What those
    five did not cover is the registry: `CollectorRegistry.collect()` opens with
    `with self._lock:` (a lock that is
    neither a family's nor a `MutexValue`'s) to copy `_collector_to_names`, so a
    child that forked while a peer held the REGISTRY lock blocked on the scrape's
    FIRST statement and never reached a family at all. The family repair was
    irrelevant to that wedge, which is why the registry lock is now replaced in the
    same pass.

    Forked probe, in-suite: the parent's peer thread HOLDS `REGISTRY._lock` across
    the `fork()`; the child must produce the exposition. ⛔ WITH THE REGISTRY
    REPLACEMENT REMOVED the child blocks in `registry.collect()` until its own alarm
    fires and reports exit **4**, so this leg reds on that mutant while the
    family-level leg stays GREEN — that separation is the point of a second leg.
    """
    registry = monitoring.REGISTRY
    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with registry._lock:
            holding.set()
            release.wait(10)

    holder = threading.Thread(target=hold, name="registry-lock-holder", daemon=True)
    holder.start()
    assert holding.wait(5), "the peer never took the registry lock"

    pid = os.fork()
    if pid == 0:                                   # ── child ──
        code = 3
        try:
            signal.alarm(10)
            monitoring.generate_latest()           # `registry.collect()` first
            code = 0
        except BaseException:                      # noqa: BLE001, RUF100
            code = 4
        finally:
            os._exit(code)                         # never unwind into pytest

    release.set()
    holder.join(5)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, (
        "the forked child could not produce the /metrics exposition: "
        f"exited={os.WIFEXITED(status)} status="
        f"{os.WEXITSTATUS(status) if os.WIFEXITED(status) else 'signalled'} — it "
        "blocked in `registry.collect()` on the registry lock its peer held at "
        "fork(), before any family lock was touched")


@pytest.mark.skipif(not hasattr(os, "fork"), reason="needs os.fork")
@pytest.mark.filterwarnings("ignore::DeprecationWarning")   # fork() with peers
def test_the_fork_reset_does_not_wait_on_the_lock_it_REPLACES(armed):
    """⛔ #14222 review cycle 5, ISSUE 4: decide BEFORE you take the lock.

    `_ensure_not_inherited` opened with `with cond:` — the very Condition it
    exists to replace — so in the window its own docstring names (a peer thread
    HOLDING `_cond` at `fork()` time) the child blocked on a lock no thread in that
    process owns, and never reached the question. `_cond_pid` is now read
    UNSYNCHRONISED, before any acquire, so the child replaces the lock instead of
    waiting for it.

    Forked probe, in-suite: the parent holds `_cond` while it forks; the child
    takes the ordinary lag reading and must answer. A child that hangs is killed
    by its OWN alarm (a SIGNAL, which the assertions name) rather than hanging the
    runner.
    """
    worker = projection_worker.worker()
    holding = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with worker._cond:
            holding.set()
            release.wait(10)

    holder = threading.Thread(target=hold, name="cond-holder", daemon=True)
    holder.start()
    assert holding.wait(5), "the peer never took the lock"

    pid = os.fork()
    if pid == 0:                                   # ── child ──
        code = 3
        try:
            signal.alarm(10)
            projection_worker.stats()              # the fork-aware READ path
            code = 0
        except BaseException:                      # noqa: BLE001, RUF100
            code = 4
        finally:
            os._exit(code)                         # never unwind into pytest

    release.set()
    holder.join(5)
    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status), (
        "the forked child was SIGNALLED, not exited: it was still blocked on the "
        "Condition its peer held at fork() — the reset never reached its decision")
    assert os.WEXITSTATUS(status) == 0, (
        "the forked child could not take a fork-aware reading (exit "
        f"{os.WEXITSTATUS(status)})")


def test_a_STOPPED_worker_still_refreshes_its_condition(armed):
    """⛔ #14222 review cycle 3, P2-1: `stop()` leaves `_pid = None`, and a fork
    taken after it inherits a Condition a peer thread may be HOLDING.

    The predicate used to read `_pid is not None and _pid != os.getpid()`, so a
    stopped worker looked "not inherited" and the child blocked on the parent's
    lock forever — the exact failure `_ensure_not_inherited` exists to prevent,
    left open by the one call that clears `_pid`. The mechanism is pinned here
    (the Condition is replaced) without forking: a real fork in a multi-threaded
    test process is the hazard being tested, not something to reproduce under
    pytest.
    """
    worker = projection_worker.worker()
    assert worker.stop(drain=False, timeout=5) is True

    # Simulate the child: the Condition in hand was created by another process.
    worker._cond_pid = os.getpid() + 1_000_000
    inherited = worker._cond
    worker.stats()
    assert worker._cond is not inherited, (
        "an inherited Condition must be re-created, not reused")
    assert worker._cond_pid == os.getpid()
    assert worker.stats().queue_depth == 0

    # …and a fork after a STOP is detected too, which `_pid is None` could not
    # distinguish from "this process, stopped".
    worker.stop(drain=False, timeout=5)
    worker._cond_pid = os.getpid() + 1_000_000
    stopped_cond = worker._cond
    worker.stats()
    assert worker._cond is not stopped_cond


def test_stopping_from_the_consumer_itself_does_not_clear_a_live_consumer(armed):
    """⛔ #14222 review cycle 2, NEW-5: liveness decides, never a default.

    A `project` callable that stops its own worker skips the join, and the old
    `joined = True` default then cleared `_thread` while the consumer (itself) was
    still running — so the next `submit` started a second consumer on the same
    deque. That is the ordering hazard the earlier fix removed, reachable on the
    reentrant path.
    """
    stopped: list = []
    worker = projection_worker.worker()

    def project(event, seq, *, journal_skipped=False):
        stopped.append(worker.stop(drain=False, timeout=0.05))
        return types.SimpleNamespace(applied=True, refusal=None,
                                     outcome="applied")

    live_write.write({"type": "A"}, log=_Log(), project=project, tenant=_TENANT)
    assert projection_worker.wait_for_drain(10) is True
    assert stopped == [False], (
        "stop() must report that the consumer it could not join is still alive")

    live = [t for t in threading.enumerate()
            if t.name == "tortoise-projection-worker"]
    assert len(live) <= 1, f"a second consumer started: {live!r}"


def test_shutdown_keeps_the_instance_whose_consumer_would_not_join(armed):
    """⛔ #14222 review cycle 2, NEW-6: don't drop the reference to a live consumer.

    `shutdown()` used to clear the singleton unconditionally, so the next
    `worker()` built a FRESH instance with its own thread and deque beside the
    wedged one — two consumers folding one tenant's records at once.
    """
    slow = _SlowProjector()
    live_write.write({"type": "A"}, log=_Log(), project=slow, tenant=_TENANT)
    assert slow.in_flight.wait(5)

    before = projection_worker.worker()
    projection_worker.shutdown(drain=False, timeout=0.05)
    assert projection_worker.worker() is before, (
        "a worker whose fold would not join must be kept, not replaced")

    slow.gate.set()
    assert projection_worker.wait_for_drain(10) is True
    projection_worker.shutdown(drain=True, timeout=10)


def test_a_record_that_cannot_be_copied_folds_inline_rather_than_aliasing(armed):
    """⛔ #14222 review cycle 2, NEW-8: the fallback must not reintroduce the bug.

    The deep snapshot exists because the fold reads the caller's dict AFTER the
    caller resumed, and the envelope shares nested values. Falling back to the
    reference on a copy failure would restore exactly the undetectable divergence
    the copy removes — so the honest answer is to decline and fold inline, which
    is byte-identical to the pre-#14185 write and has no window at all.
    """
    class _Uncopyable(dict):
        def __deepcopy__(self, memo):
            raise TypeError("this value refuses to be copied")

    called = []
    event = _Uncopyable({"type": "PointAdded"})
    write = live_write.write(
        event, log=_Log(), tenant=_TENANT,
        project=lambda e, s, journal_skipped=False: (
            called.append(s),
            types.SimpleNamespace(applied=True, refusal=None,
                                  outcome="applied"))[1])

    assert write.deferred is False, "an uncopyable record must fold inline"
    assert called == [1], "…and it must still be folded"
    assert projection_worker.stats().submitted == 0


def test_an_uncopyable_record_is_COUNTED_when_it_folds_inline(armed):
    """⛔ #14222 review cycle 10, P2: the decline must be a NUMBER, not a silence.

    Every other observable reads healthy when this arm fires: `submitted` never
    moves (the item never reached the queue), `pending_positions` is 0, and the
    fold happens on the caller's thread. Without this counter a process whose
    every record is uncopyable pays the pre-#14185 fold on every ACK while the
    exported channel reports a caught-up idle loop — the silent-green shape this
    module refuses elsewhere. Deleting the publish reds this.
    """
    class _Uncopyable(dict):
        def __deepcopy__(self, memo):
            raise TypeError("this value refuses to be copied")

    before = monitoring.projection_inline_fallback_counts().get("uncopyable", 0)
    assert projection_worker.submit(
        record=_Uncopyable({"type": "PointAdded"}), seq=1, tenant=_TENANT,
        journal_skipped=False, project=lambda *a, **k: None) is False
    assert monitoring.projection_inline_fallback_counts().get(
        "uncopyable", 0) == before + 1


def test_a_queue_fault_is_COUNTED_before_it_propagates(armed, monkeypatch):
    """⛔ #14222 review cycle 10, P2: the ONE raising arm is counted on its way out.

    `ProjectionWorker.submit` raises only from `_ensure_started` (thread/fd
    exhaustion in `thread.start()`), and `live_write._submitted_to_worker`
    answers it with a bare `except Exception: return False`. Fail-safe — the
    write still folds — but nothing recorded WHY, which is the same silent-green
    shape as the uncopyable arm: every write pays the inline fold, every gauge
    reads idle. The counter is emitted BEFORE the re-raise, and the raise still
    travels, so `live_write`'s answer is unchanged. Deleting the publish reds this.
    """
    before = monitoring.projection_inline_fallback_counts().get("queue_fault", 0)

    def _boom(self, item):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(projection_worker.ProjectionWorker, "submit", _boom)
    with pytest.raises(RuntimeError, match="can't start new thread"):
        projection_worker.submit(
            record={"type": "PointAdded"}, seq=1, tenant=_TENANT,
            journal_skipped=False, project=lambda *a, **k: None)
    assert monitoring.projection_inline_fallback_counts().get(
        "queue_fault", 0) == before + 1


def test_an_UNIMPORTABLE_loop_folds_inline_instead_of_breaking_the_write(
        monkeypatch):
    """The unimportable-loop arm — `except Exception` around the import — on its own.

    ⛔ A SEPARATE ROW FROM THE QUEUE FAULT (#14222 review cycle 5, ISSUE 2). The
    sibling test patches `submit` and therefore never reaches the import, so the
    arm had no assertion at all — deleting it reddened nothing. ⛔ THE CLAUSE IT
    CATCHES IS `except Exception`, NOT `except ImportError` (round-15 review, P2:
    an earlier title said ImportError and cited two line numbers that are not the
    arm). The breadth is deliberate — a partially-shipped wheel raising
    `AttributeError`/`RuntimeError` at import is the same hazard — and narrowing it
    would not make the failure escape the chokepoint, it would make the chokepoint
    ANSWER it wrongly: `write()`'s own `except Exception` catches the raise, reports
    it as a RAISED-after-append disagreement and SKIPS the fold, so the line is
    written but the derived store is left behind for `unprojected_journal` /
    `record_runner.py apply` to heal. "Written to NEITHER store" is the record-only
    arm (#14002) — a different arm, with no committed line to disagree with.
    An import that fails
    here is not a fault to report: the line has committed, the fold must happen,
    and folding inline is the pre-#14185 shape.

    Simulated the way the interpreter really fails it: the module is present on
    the package, `sys.modules` maps its key to ``None`` (the "import halted" arm)
    and the package attribute is removed so the from-import cannot be satisfied
    by `getattr` alone.
    """
    package = sys.modules["tortoise"]        # the package whose attribute we drop

    monkeypatch.setenv(projection_worker.ENV_WORKER, "1")
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, _RECORD_DSN)
    monkeypatch.delattr(package, "projection_worker", raising=False)
    monkeypatch.setitem(sys.modules, "tortoise.projection_worker", None)

    called = []
    write = live_write.write(
        {"type": "PointAdded"}, log=_Log(), tenant=_TENANT,
        project=lambda e, s, journal_skipped=False: (
            called.append(s),
            types.SimpleNamespace(applied=True, refusal=None,
                                  outcome="applied"))[1])

    assert write.deferred is False
    assert called == [1], "an unimportable loop must fold inline, not skip the fold"


# ── 5. the flag itself ──────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on", " on "])
def test_the_flag_reads_the_declared_truthy_vocabulary(monkeypatch, raw):
    """#4097: one truthiness contract, so `=true` in a process manager works."""
    monkeypatch.setenv(projection_worker.ENV_WORKER, raw)
    assert projection_worker.armed() is True


@pytest.mark.parametrize("raw", ["", "0", "false", "off", "nonsense"])
def test_a_typo_leaves_the_loop_off_rather_than_half_armed(monkeypatch, raw):
    monkeypatch.setenv(projection_worker.ENV_WORKER, raw)
    assert projection_worker.armed() is False


@pytest.mark.parametrize("raw", ["0", "-3", "not-a-number", ""])
def test_an_impossible_queue_bound_falls_back_to_the_default(monkeypatch, raw):
    """A bad bound must not DISABLE the loop — a typo changes the size, not the mode."""
    monkeypatch.setenv(projection_worker.ENV_QUEUE_MAX, raw)
    assert projection_worker.queue_max() == projection_worker.DEFAULT_QUEUE_MAX


def test_the_queue_bound_is_read_when_configured(monkeypatch):
    monkeypatch.setenv(projection_worker.ENV_QUEUE_MAX, "17")
    assert projection_worker.queue_max() == 17


# ── 6. a submitted fold never raises out of the write path ──────────────────


def test_a_queue_fault_folds_inline_instead_of_breaking_the_write(monkeypatch):
    """The write has already committed its journal line: a queue fault must fold inline.

    ⛔ NOTHING about the queue is worth raising for at this point. The line exists,
    so an exception here would hand the producer a failure for a write that
    persisted AND skip the fold — the write would be missing from the record with
    nothing queued to put it there. `live_write` answers every doubt as "fold
    inline", which is the pre-#14185 shape and therefore always safe.
    """
    monkeypatch.setenv(projection_worker.ENV_WORKER, "1")
    # ⛔ BOTH MASKING GATES (#14222 review cycle 5, ISSUE 2). `submit` is preceded by
    # five declines — the fused `journal_skipped / record_sink / durable` gate, the
    # unimportable-loop gate, `tenant is None`, `not projection_worker.armed()` and
    # `not _record_armed()` — so "the gates that precede `submit`" is not two of
    # anything. What this row must get PAST is the pair that masked it: without
    # `tenant=` and without the record's own arming it was declined by the
    # `tenant is None` gate and by the `not _record_armed()` gate, and this row then
    # passed even with the `submit` `except Exception` deleted — the queue fault was
    # never exercised. ⛔ Cited by GATE TEXT: the `:line` numbers that used to sit
    # here resolved to docstring prose and to a COMMENT (round-16, P2), which is
    # how a reader deletes the wrong line.
    monkeypatch.setenv(record_writer.RECORD_DSN_ENV, _RECORD_DSN)

    def exploding_submit(**kwargs):
        raise RuntimeError("the queue is gone")

    monkeypatch.setattr(projection_worker, "submit", exploding_submit)

    called = []
    write = live_write.write(
        {"type": "PointAdded"}, log=_Log(), tenant=_TENANT,
        project=lambda e, s, journal_skipped=False: (
            called.append(s),
            types.SimpleNamespace(applied=True, refusal=None,
                                  outcome="applied"))[1])
    assert write.deferred is False
    assert called == [1], "an unimportable loop must fold inline, not skip the fold"
