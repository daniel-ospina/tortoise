"""#10893 — a probe START must not be paid on the event loop.

WHAT THIS FILE PINS, AND WHY IT IS A #10893 TEST AND NOT A MONITORING ONE
------------------------------------------------------------------------
#10893 is *"the primary retrieval verb cannot return inside its own budget"*.
The stage instrument that names the culprit (``tortoise/read_stages.py``)
reported, on the live production box, stages summing to ~10.2 s against a
19.7 s wall, with ``pre_handler`` alone at **4.5 s** — i.e. most of the budget
went to time that was not this request's retrieval at all: it was the request
WAITING FOR THE EVENT LOOP.

``/health``'s ``loop_blocks.last`` named what held the loop::

    frames: [..., "hosted_api.py:8767 in health_ready",
             "monitoring.py:5477 in run", "monitoring.py:5450 in begin",
             "monitoring.py:5354 in _start_locked",
             "threading.py:111 in sentry_start", "threading.py:999 in start",
             "threading.py:655 in wait"]
    loop_age_s: 7.441      workload_in_flight: 26

Innermost frame last: CPython reads that chain as
``HealthProbe._start_locked`` -> ``Thread.start()`` -> ``Event.wait()``.
**``Thread.start()`` blocks its caller** until the NEW thread's bootstrap has
run (``self._started.wait()`` in 3.12), and ``_start_locked`` was reaching it
from ``begin()`` — i.e. from the event loop, on every probe start, for every
coordinator (``_HEALTH_PROBE``, ``_READY_PROBE``, ``_CONTROL_PLANE_PROBE``).

Measured on a CPU-saturated box (2x cpus spinning, n=30): ``Thread.start()``
costs **0.49 s at p50 and 1.09 s at p95** — the wait is the new thread being
scheduled, so it grows with exactly the load that makes a request late. On the
production box (2 vCPU, 26 requests in flight) the loop thread was parked in it
for 7.4 s. A ~6 s read (embed 2.1 s + retrieval 3.0 s + annotation 0.5 s) plus a
4.5 s ``pre_handler`` is over the 10 s transport bound — hence the 504.

So the fix is NOT a wider budget (#10893's brief forbids that explicitly) and
NOT a smaller graph: it is that **starting a worker thread is not a request's
work**, and the loop must not wait for it. These tests fail on the pre-fix tree
and pin the three properties the fix must preserve:

* the loop keeps ticking while a start is slow (`test_a_probe_start_...`),
* a concurrent bounded read keeps its budget through that same window
  (`test_a_budgeted_read_...` — the #10893 regression test),
* the coordinator's state machine is unchanged: the probe still runs, still
  single-flights while a start is pending, and still reports honestly when a
  start is refused (`test_*`).
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from tortoise import monitoring

#: The simulated "saturated box" thread-start cost. Deliberately a whole number
#: of tenths, well above any scheduling jitter on a CI box, and BELOW the poll
#: interval of every assertion so a pass cannot be a coincidence.
SLOW_START_S = 0.40

#: Start cost simulated by the LOOP-STARVATION row, separated from the bound that
#: row asserts (review round 2, P2 note 5). One constant cannot do both jobs: the
#: BOUND must tolerate a scheduling stall on a loaded CI box (this PR measured
#: ~0.5 s stalls on a saturated one), while the COST must stay far enough above
#: the bound that the mutant's single gap is unmistakable. At `SLOW_START_S` for
#: both, the mutant lands on the threshold — 0.40 s of block against a 0.40 s
#: bound — which is the one reading that can pass VACUOUSLY.
STARVED_START_S = 1.0


def _slow_thread_start(monkeypatch, seconds: float = SLOW_START_S):
    """Make every ``Thread.start()`` cost ``seconds``, as it does under load.

    This is the measured mechanism, not an arbitrary sleep: the cost of
    ``Thread.start()`` is bounded below by the new thread being SCHEDULED, and
    on a CPU-starved box that is hundreds of milliseconds (p50 0.49 s / p95
    1.09 s, measured) — rising to seconds on the production box (7.4 s captured).
    """
    real_start = threading.Thread.start

    def slow_start(self, *args, **kwargs):
        time.sleep(seconds)
        return real_start(self, *args, **kwargs)

    monkeypatch.setattr(threading.Thread, "start", slow_start)


def _fresh_starter():
    """Drop the process-wide probe STARTER so this test owns its lifecycle."""
    reset = getattr(monitoring, "reset_probe_starter", None)
    if reset is not None:
        reset()


class _Ticker:
    """Records the largest gap between event-loop turns."""

    def __init__(self) -> None:
        self.gaps: list[float] = []
        self._stop = False

    async def run(self) -> None:
        last = time.monotonic()
        while not self._stop:
            await asyncio.sleep(0.005)
            now = time.monotonic()
            self.gaps.append(now - last)
            last = now

    def stop(self) -> None:
        self._stop = True

    @property
    def max_gap(self) -> float:
        return max(self.gaps) if self.gaps else 0.0


def test_a_probe_start_does_not_starve_the_event_loop(monkeypatch):
    """The defect itself: a probe START must not occupy the loop.

    Pre-fix, ``HealthProbe.run()`` -> ``begin()`` -> ``_start_locked()`` ->
    ``Thread.start()`` blocks the loop for the whole start cost, so the ticker
    records a single gap of ``STARVED_START_S`` (1.0 s). After the fix the wait
    is paid on the starter worker and the loop keeps its 5 ms turn.

    ⛔ THE BOUND IS ``SLOW_START_S`` (0.40 s), NOT HALF OF IT (review round 2,
    P2 note 5). The row defends against a loop block, so its bound has to
    tolerate the scheduling stall of a loaded CI box — the same load that makes
    ``Thread.start()`` cost 0.49 s p50 by this PR's own measurement — while
    still being far below the mutant. It was ``SLOW_START_S / 2`` = 0.20 s,
    i.e. inside the noise it is measuring. The three numbers are now separated:
    mutant gap 1.0 s, bound 0.40 s, tick 0.005 s. RESIDUAL, stated rather than
    implied: a stall longer than 0.40 s would still flake this row, which is
    inherent to any wall-clock assertion in a required lane; 0.40 s of
    tolerance is the honest ceiling before the row stops discriminating.
    """
    _slow_thread_start(monkeypatch, seconds=STARVED_START_S)
    _fresh_starter()

    probe = monitoring.HealthProbe(
        lambda: {"ok": True, "latency_ms": 1.0}, timeout=5.0)

    async def scenario() -> float:
        ticker = _Ticker()
        task = asyncio.create_task(ticker.run())
        await asyncio.sleep(0.05)      # let the ticker establish a baseline
        ticker.gaps.clear()
        await probe.run()
        # ⛔ Let the ticker RESUME before reading its gaps: the tick completes
        # its `sleep` only on the next turn, and a gap is recorded when the
        # sleep RETURNS. Reading immediately after `await probe.run()` reads a
        # list the blocked stretch has not been appended to yet — which is how
        # a first draft of this test passed against the very defect it names.
        await asyncio.sleep(0.02)
        max_gap = ticker.max_gap
        ticker.stop()
        await task
        return max_gap

    max_gap = asyncio.run(scenario())
    assert max_gap < SLOW_START_S, (
        f"the event loop was blocked for {max_gap:.3f}s by a "
        f"{STARVED_START_S:.2f}s Thread.start() (bound {SLOW_START_S:.2f}s) — a "
        "probe start is being paid ON THE LOOP, which is exactly what inflates "
        "another request's `pre_handler` (#10893)")


def test_a_budgeted_read_keeps_its_budget_while_a_probe_starts(monkeypatch):
    """THE REGRESSION TEST for the 504 (#10893's measured sequence).

    A read that owes ~0.06 s of work must finish inside its budget while a
    concurrent health poll starts a probe on a box whose ``Thread.start()``
    costs ``SLOW_START_S``. Pre-fix the read is serialised behind the start and
    blows a 0.2 s budget — the measured production shape is the same arithmetic
    at production scale (a ~6 s read + a 4.5 s ``pre_handler`` > the 10 s bound).
    """
    _slow_thread_start(monkeypatch)
    _fresh_starter()

    probe = monitoring.HealthProbe(
        lambda: {"ok": True, "latency_ms": 1.0}, timeout=5.0)
    budget_s = 0.20

    async def scenario() -> tuple[float, bool]:
        async def budgeted_read() -> float:
            started = time.monotonic()
            for _ in range(6):          # the read's legs, ~0.06 s of work
                await asyncio.sleep(0.01)
            return time.monotonic() - started

        # The READ is already in flight (the production ordering: a request is
        # mid-retrieval when the platform's readiness poll arrives — it polls
        # every 15 s regardless of what else is live). The poll's probe start
        # then has to be paid while this read is suspended mid-flight.
        read = asyncio.create_task(budgeted_read())
        await asyncio.sleep(0.001)
        poll = asyncio.create_task(probe.run())
        elapsed = await read
        await poll
        return elapsed, (elapsed <= budget_s)

    elapsed, within_budget = asyncio.run(scenario())
    assert within_budget, (
        f"a {elapsed:.3f}s read exceeded its {budget_s:.2f}s budget while a "
        "concurrent probe start held the loop — this is the #10893 504 shape")


def test_the_probe_still_runs_and_reports_its_result(monkeypatch):
    """The fix defers only the START; the probe still runs and lands its result."""
    _slow_thread_start(monkeypatch, seconds=0.05)
    _fresh_starter()

    calls = {"n": 0}

    def probe_fn():
        calls["n"] += 1
        return {"ok": True, "latency_ms": 2.0, "observed": True}

    probe = monitoring.HealthProbe(probe_fn, timeout=5.0)
    result = asyncio.run(probe.run())
    assert result["ok"] is True
    assert result["latency_ms"] == 2.0
    assert calls["n"] == 1
    assert probe.info()["in_flight"] is False


def test_reads_inside_the_start_window_still_coalesce(monkeypatch):
    """Single-flight must survive the window before the new thread is alive.

    ``Thread.is_alive()`` is False for a thread whose ``start()`` has been
    handed to the starter but has not run yet. If the coordinator trusted
    ``is_alive()`` alone, a second read in that window would start a SECOND
    probe — the accumulation #2850 item 2 exists to prevent.

    ⛔ THE DISCRIMINATOR IS **HAND-OFFS**, NOT ``probe_fn`` CALLS (review round
    4, F1). ``calls["n"] == 1`` alone is GREEN under the gate mutant: dropping
    ``self._starting or`` from ``begin()``'s gate makes all six reads supersede
    one another (``_seq`` 1..6), and ``_run``'s generation guard then discards
    generations 1–5 BEFORE ``probe_fn`` — so the count stays 1 whether
    single-flight held or six starts were thrown away. A row must vary under the
    mutation of the behaviour it names, or it is dead weight that reads as
    coverage. Counting hand-offs makes the mutant red by 6; the ``probe_fn``
    count is kept because it proves the probe still RAN, which is a different
    invariant (already separately pinned by
    ``test_an_abandoned_generation_does_not_clear_the_successors_start_bit``).
    """
    _slow_thread_start(monkeypatch, seconds=0.30)
    _fresh_starter()

    calls = {"n": 0}
    handoffs = {"n": 0}

    def probe_fn():
        calls["n"] += 1
        time.sleep(0.02)
        return {"ok": True, "latency_ms": 20.0, "observed": True}

    probe = monitoring.HealthProbe(probe_fn, timeout=5.0)

    # The seam the behaviour actually lives in: `_start_locked` hands every
    # start to this module-level function (resolved at call time), so counting
    # its invocations counts the STARTS the coordinator issued.
    real_handoff = monitoring._start_probe_worker_off_caller

    def counting_handoff(worker, _real=real_handoff):
        handoffs["n"] += 1
        return _real(worker)

    monkeypatch.setattr(monitoring, "_start_probe_worker_off_caller",
                        counting_handoff)

    async def scenario():
        return await asyncio.gather(*[probe.run() for _ in range(6)])

    results = asyncio.run(scenario())
    assert handoffs["n"] == 1, (
        f"{handoffs['n']} probe STARTS were handed off for 6 reads inside the "
        "start window — single-flight broke, which is the accumulation #2850 "
        "item 2 forbids (five of the workers are then discarded by `_run`'s "
        "generation guard, so a `probe_fn` count cannot see this)")
    assert calls["n"] == 1, (
        f"{calls['n']} probes ran for 6 reads — the start window broke "
        "single-flight")
    assert all(r["ok"] is True for r in results)


def test_a_cancelled_start_is_reconciled_without_raising(monkeypatch):
    """A CANCELLED handed-off start must clear the state, not raise (#10893).

    ``Future.exception()`` raises ``concurrent.futures.CancelledError`` for a
    cancelled future, so the pre-fix order — read the exception, THEN clear
    ``_running``/``_starting``/``_worker`` — escaped this read path (it is
    reached from ``_view_locked()``, i.e. the unauthenticated ``GET /health``
    read ``snapshot()``/``peek()``) and left the coordinator describing a probe
    that will never run. Its ``"cancelled"`` cause string was unreachable.

    No in-tree caller cancels a start today (measured: nothing calls
    ``.cancel()`` on a start future), so this drives the arm through the starter
    seam the way a future caller — a pool shutdown, a supervisor reaping the
    hand-off — would.
    """
    _fresh_starter()

    import concurrent.futures

    pending: list[concurrent.futures.Future] = []

    class _PendingStarter:
        def submit(self, fn):
            future: concurrent.futures.Future = concurrent.futures.Future()
            pending.append(future)
            return future

    probe = monitoring.HealthProbe(
        lambda: {"ok": True, "latency_ms": 1.0}, timeout=1.0)
    monkeypatch.setattr(monitoring, "_probe_starter", _PendingStarter)

    async def scenario() -> dict:
        async def cancel_the_start():
            await asyncio.sleep(0.05)
            pending[0].cancel()

        cancelling = asyncio.create_task(cancel_the_start())
        result = await probe.run()      # pre-fix: CancelledError escapes here
        await cancelling
        return result

    result = asyncio.run(scenario())
    assert result["ok"] is False
    assert "cancelled" in (result.get("error") or ""), (
        f"a cancelled start is not declared as cancelled: "
        f"{result.get('error')!r}")
    info = probe.info()
    assert info["in_flight"] is False, (
        "a cancelled start left the probe 'in flight' forever")
    assert info["starting"] is False


def test_a_hand_off_failure_is_not_reported_as_a_backlog_refusal(monkeypatch):
    """``None`` from the hand-off is a START we could not MAKE, not a refusal.

    ``_start_probe_worker_off_caller`` returns ``None`` only from its
    ``except Exception`` around ``_probe_starter()``/``submit`` — pool
    construction or the submit itself raised (``RuntimeError: can't start new
    thread`` under ``RLIMIT_NPROC``, the resource-exhaustion regime this change
    exists for). A backlog refusal returns an ALREADY-FAILED future carrying
    ``_WorkerBacklogFull`` and is reconciled with its own message, so reporting
    ``None`` as "the starter's bounded backlog is full" sends the operator to
    recover the wrong thing (review round 4, P3-a).
    """
    _fresh_starter()

    probe = monitoring.HealthProbe(
        lambda: {"ok": True, "latency_ms": 1.0}, timeout=0.3)

    def broken_starter():
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(monitoring, "_probe_starter", broken_starter)

    result = asyncio.run(probe.run())
    error = result.get("error") or ""
    assert result["ok"] is False, "a probe that never ran must not report ok"
    assert "handed off" in error, (
        f"the hand-off failure is not named: {error!r}")
    assert "backlog" not in error, (
        f"a hand-off failure was reported as a starter backlog refusal — the "
        f"wrong operator action: {error!r}")
    assert probe.info()["in_flight"] is False


def test_a_refused_start_is_reported_and_does_not_block(monkeypatch):
    """A wedged starter must degrade HONESTLY, never fall back to blocking.

    The pre-fix code had no failure mode here (it blocked instead). The fix
    must not reintroduce one: when the starter cannot accept the start, the
    coordinator says "no probe is running" so the caller fails closed — it does
    not quietly block the loop to keep the old liveness behaviour.
    """
    _slow_thread_start(monkeypatch, seconds=0.05)
    _fresh_starter()

    import concurrent.futures

    class _RefusingStarter:
        def submit(self, fn):   # mimics _SingleSlotWorker.submit
            future: concurrent.futures.Future = concurrent.futures.Future()
            future.set_exception(
                monitoring._WorkerBacklogFull("starter backlog full"))
            return future

    probe = monitoring.HealthProbe(
        lambda: {"ok": True, "latency_ms": 1.0}, timeout=0.3)
    monkeypatch.setattr(monitoring, "_probe_starter", _RefusingStarter)
    # ⛔ APPLIED AFTER the pool is built and the probe constructed: the only
    # thing that could pay this cost is the forbidden inline fallback, which is
    # exactly what the elapsed-time assertion below names. (At 0.05 s the
    # assertion could never fire for its own named failure — review finding.)
    _slow_thread_start(monkeypatch, seconds=1.5)

    started = time.monotonic()
    result = asyncio.run(probe.run())
    elapsed = time.monotonic() - started

    assert elapsed < 0.5, (
        f"a refused start blocked for {elapsed:.2f}s — an inline fallback "
        "start() was attempted")
    assert result["ok"] is False, "a probe that never ran must not report ok"
    assert "starter refused" in (result.get("error") or ""), (
        f"the refusal is not declared: {result.get('error')!r}")
    assert probe.info()["in_flight"] is False, (
        "the coordinator still believes a probe it could not start is running")


def test_a_start_that_fails_asynchronously_is_reconciled_and_can_recover(
        monkeypatch):
    """An ACCEPTED start that then raises must not wedge the coordinator.

    ``can't start new thread`` (``RLIMIT_NPROC``/``EAGAIN``) is raised on the
    STARTER thread, not inside ``begin()`` — the exact resource-exhaustion
    regime this change exists for. Left unreconciled that reports
    ``in_flight: true`` forever: the probe is never served, readiness fails
    closed for the life of the process, and only a restart recovers.
    """
    _fresh_starter()

    import concurrent.futures

    accepted: list[concurrent.futures.Future] = []

    class _AcceptThenFail:
        def submit(self, fn):
            future: concurrent.futures.Future = concurrent.futures.Future()
            accepted.append(future)
            return future

    class _Healthy:
        def submit(self, fn):
            future: concurrent.futures.Future = concurrent.futures.Future()
            try:
                future.set_result(fn())
            except BaseException as exc:  # pragma: no cover - start cannot raise
                future.set_exception(exc)
            return future

    monkeypatch.setattr(monitoring, "_probe_starter", _AcceptThenFail)
    probe = monitoring.HealthProbe(
        lambda: {"ok": True, "latency_ms": 58.0}, timeout=2.0)

    async def scenario() -> dict:
        async def fail_the_start():
            await asyncio.sleep(0.05)
            accepted[0].set_exception(RuntimeError("can't start new thread"))

        failing = asyncio.create_task(fail_the_start())
        result = await probe.run()
        await failing
        return result

    result = asyncio.run(scenario())
    assert result["ok"] is False
    assert "can't start new thread" in (result.get("error") or ""), (
        f"the failure is not declared: {result.get('error')!r}")
    info = probe.info()
    assert info["in_flight"] is False, (
        "a start that failed on the starter left the probe 'in flight' forever")
    assert info["starting"] is False

    # ⛔ And the coordinator RECOVERS: a later read starts a fresh generation.
    monkeypatch.setattr(monitoring, "_probe_starter", _Healthy)
    again = asyncio.run(probe.run())
    assert again["ok"] is True, f"the coordinator never recovered: {again!r}"
    assert probe.info()["start_error"] is None


def test_a_failed_start_stays_visible_while_the_retry_is_in_flight(monkeypatch):
    """/health must not report a start failure as "probe in flight" (round 2, P2).

    The `/health` read sequence is ``snapshot()`` -> ``begin()`` (which
    reconciles the previous attempt, then starts ``_start_locked``) -> the view.
    ``_start_locked`` cleared ``_start_error`` BEFORE the hand-off was known to
    succeed, so within ONE read the cause was recorded and then wiped and the
    read fell through to the "probe in flight" branch: measured 5/5 reads at
    ``db.error='probe in flight (0.0s)'``, ``start_error=None`` — the exact
    signature this change exists to remove, on the surface an operator reads.

    The failure must survive until a start LANDS (``_run`` clears it), so a retry
    in flight cannot mask the failure that caused it.
    """
    _fresh_starter()

    import concurrent.futures

    class _AcceptThenFail:
        def __init__(self) -> None:
            self.calls = 0

        def submit(self, fn):
            self.calls += 1
            future: concurrent.futures.Future = concurrent.futures.Future()
            if self.calls == 1:
                future.set_exception(RuntimeError("can't start new thread"))
            # 2nd+ : never settles -> the retry legitimately stays "starting"
            return future

    stub = _AcceptThenFail()
    monkeypatch.setattr(monitoring, "_probe_starter", lambda: stub)
    probe = monitoring.HealthProbe(
        lambda: {"ok": True, "latency_ms": 1.0}, timeout=0.3)

    first = probe.wait()
    assert "can't start new thread" in (first.get("error") or "")

    # This is the /health read: it inherits the failure AND starts a retry.
    snapshot = probe.snapshot()
    info = probe.info()

    assert stub.calls == 2, "the retry was not handed off"
    assert info["starting"] is True, "the retry should be in flight"
    assert info["start_error"], (
        "/health dropped the start failure as soon as a retry was in flight — it "
        "reads as a healthy in-flight probe")
    assert "start" in (snapshot.get("error") or ""), (
        f"the read reported {snapshot.get('error')!r} instead of the start "
        "failure it inherited")


def test_the_sync_wait_path_reconciles_a_failed_start_too(monkeypatch):
    """``wait()`` is ``run()``'s non-async twin and must reconcile as well.

    It is what the selfhost probe loop and most tests call, and it had no row
    covering a failed start at all: a reconcile removed from it burns the whole
    bound on every call while reporting the same terminal state, so only the
    ELAPSED assertion can see it.
    """
    _fresh_starter()

    import concurrent.futures

    class _AcceptThenFail:
        def submit(self, fn):
            future: concurrent.futures.Future = concurrent.futures.Future()
            future.set_exception(RuntimeError("can't start new thread"))
            return future

    monkeypatch.setattr(monitoring, "_probe_starter", _AcceptThenFail)
    probe = monitoring.HealthProbe(
        lambda: {"ok": True, "latency_ms": 1.0}, timeout=2.0)

    started = time.monotonic()
    result = probe.wait()
    elapsed = time.monotonic() - started

    assert result["ok"] is False
    assert "can't start new thread" in (result.get("error") or "")
    assert elapsed < probe._timeout / 2, (
        f"wait() took {elapsed:.2f}s of a {probe._timeout:g}s bound after a "
        "failed start — the failure was only reconciled at the deadline")
    assert probe.info()["in_flight"] is False


def test_constructing_a_probe_warms_the_starter_off_the_loop():
    """The ONE remaining blocking start() is paid at CONSTRUCTION, not on a read.

    Every production coordinator is constructed at module import, so this warm-up
    is what keeps a ``begin()`` on the event loop from ever building the pool —
    i.e. it is the load-bearing half of the fix, and nothing pinned it: removing
    it left every row green, because ``reset_probe_starter()`` pre-creates the
    pool and so masked the gap in tests while production has no reset.
    """
    _fresh_starter()
    with monitoring._PROBE_STARTER_LOCK:
        monitoring._PROBE_STARTER_CACHE = None
        monitoring._DAEMON_WORKERS.pop(monitoring.PROBE_STARTER_NAME, None)
    assert monitoring._PROBE_STARTER_CACHE is None

    monitoring.HealthProbe(lambda: {"ok": True, "latency_ms": 1.0}, timeout=0.2)

    assert monitoring._PROBE_STARTER_CACHE is not None, (
        "the starter was left to be built on the first READ path instead of at "
        "construction — that is the loop block this change removes")


def test_the_starter_hatch_drops_the_cached_pool_too():
    """``reset_daemon_worker(PROBE_STARTER_NAME)`` must not be a silent no-op.

    The coordinator caches the pool (lock-free hot path), so dropping only the
    registry entry left the cached pool alive: the operator pulls the documented
    ops hatch and gets no effect and no signal.
    """
    _fresh_starter()
    before = monitoring._probe_starter()
    assert monitoring._PROBE_STARTER_CACHE is before

    monitoring.reset_daemon_worker(monitoring.PROBE_STARTER_NAME)

    assert monitoring._PROBE_STARTER_CACHE is None, (
        "the starter cache survived the documented reset hatch — the hatch is a "
        "no-op while the cached pool keeps being reused")
    after = monitoring._probe_starter()
    assert after is not before, "the reset handed back the pool it was asked to drop"


def test_the_starter_is_its_own_pool():
    """The start must not queue behind the probe QUERY (#10893 design rule).

    Sharing ``_PROBE_WORKER`` would make ``worker.start`` wait behind a stalled
    ``probe_db`` socket read: probes would stop starting for the whole DB stall
    and readiness would 503 by way of the query stall — the exact confusion the
    design comment forbids. It would also silently no-op
    ``reset_probe_starter()``.
    """
    _fresh_starter()
    starter = monitoring._probe_starter()
    assert starter is not monitoring._probe_worker()
    assert any(t.name == monitoring.PROBE_STARTER_NAME
               for t in starter._threads), (
        "the starter pool's threads are not named for it")


def test_an_abandoned_generation_does_not_clear_the_successors_start_bit():
    """A superseded worker begins AFTER its successor was handed off (#10893).

    That is exactly what superseding a wedged probe DOES, so the older
    generation's first act must not clear the ``_starting`` bit its successor
    owns: ``begin()`` would then read ``_starting=False`` + ``is_alive()=False``
    for the successor and start a THIRD probe per poll, defeating
    ``PROBE_MAX_SUPERSEDES``.

    Driven by calling ``_run`` for the STALE generation directly (synchronously,
    no threads) so the assertion is about the invariant, not about scheduling.
    """
    probe = monitoring.HealthProbe(
        lambda: {"ok": True, "latency_ms": 1.0}, timeout=0.2)
    with probe._cv:
        probe._running = True
        probe._starting = True
        probe._seq = 7              # the CURRENT generation owns the bit

    probe._run(6)                   # the abandoned generation finally begins

    assert probe.info()["starting"] is True, (
        "the superseded generation cleared the CURRENT one's start bit — the "
        "next begin() will start a third probe")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
