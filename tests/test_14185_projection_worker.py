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
4. the four correctness conditions that decide whether a write may be deferred at
   all — a journal line exists, no door reads the outcome, the log is a journal,
   and the loop is armed — each of which must answer INLINE when it fails.

DB-free by design: the fold is represented by a callable, which is exactly the
boundary this module owns (it decides *when* a fold runs, never *what* it does).
The live-Postgres falsifier — one write acknowledged and then read back from the
record after the queue drained — is recorded in the PR, because a test that needs
Postgres cannot run in every lane.
"""
from __future__ import annotations

import os
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


# ── 1. the ack does not include the projection ─────────────────────────────


def test_a_write_ack_excludes_the_projection_time(armed):
    """THE issue's first target: a write is ACKNOWLEDGED fast, projected behind.

    The projector is wedged for the whole assertion, so the only way this can
    pass is if the ack never waited for it. The bound is generous (250 ms against
    a 30 s stall) because this asserts an ORDER OF MAGNITUDE — "awn't waiting" —
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


# ── 4. the four correctness conditions ──────────────────────────────────────


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

    write = live_write.write({"type": "PointAdded"}, log=_Log(), project=project)
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

    write = live_write.write(
        {"type": "PointAdded"}, log=_Log(), record_sink=sink,
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
    write = live_write.write({"type": "PointAdded"}, log=_Log(durable=False),
                             project=_fast_projector())
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
    """⛔ #14222 review cycle 2, NEW-3: an inherited high-water mark is not work.

    The counter form (`projected + failures >= submitted`) was an approximation a
    forked child can never satisfy — it inherits the parent's counters with an
    EMPTY queue, so a drained worker reported undrained and every child's exit
    drain burned the full timeout. `empty AND nothing in flight` is exact, because
    the loop pops and clears the in-flight item under this same lock.
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


def test_a_forked_worker_republishes_its_gauges(armed):
    """⛔ #14222 review cycle 3, P2-2: a child must not inherit the parent's lag.

    The fork reset empties the queue and zeroes the counters — and the three
    GAUGES are per-process readings too. Leaving them at the parent's last
    published value made a preforked child advertise a PERMANENT backlog it does
    not have, while `stats()` reported 0: the gauges and `stats()` disagreeing is
    exactly the invariant cycle 1's P1-1 was about.
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

    stats = projection_worker.stats()               # triggers the fork reset
    gauges = monitoring.projection_worker_gauges()
    assert stats.pending_positions == 0
    assert gauges["pending_positions"] == 0, (
        "a child must not export the parent's phantom backlog")
    assert gauges["queue_depth"] == stats.queue_depth

    slow.gate.set()
    assert projection_worker.wait_for_drain(10) is True
    projection_worker.shutdown(drain=True, timeout=5)


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

    def exploding_submit(**kwargs):
        raise RuntimeError("the queue is gone")

    monkeypatch.setattr(projection_worker, "submit", exploding_submit)

    called = []
    write = live_write.write(
        {"type": "PointAdded"}, log=_Log(),
        project=lambda e, s, journal_skipped=False: (
            called.append(s),
            types.SimpleNamespace(applied=True, refusal=None,
                                  outcome="applied"))[1])
    assert write.deferred is False
    assert called == [1], "an unimportable loop must fold inline, not skip the fold"
