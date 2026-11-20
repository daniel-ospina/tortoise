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
    records a single gap equal to ``SLOW_START_S``. After the fix the wait is
    paid on the starter worker and the loop never misses its 5 ms turn.
    """
    _slow_thread_start(monkeypatch)
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
    assert max_gap < SLOW_START_S / 2, (
        f"the event loop was blocked for {max_gap:.3f}s by a {SLOW_START_S:.2f}s "
        "Thread.start() — a probe start is being paid ON THE LOOP, which is "
        "exactly what inflates another request's `pre_handler` (#10893)")


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
    """
    _slow_thread_start(monkeypatch, seconds=0.30)
    _fresh_starter()

    calls = {"n": 0}

    def probe_fn():
        calls["n"] += 1
        time.sleep(0.02)
        return {"ok": True, "latency_ms": 20.0, "observed": True}

    probe = monitoring.HealthProbe(probe_fn, timeout=5.0)

    async def scenario():
        return await asyncio.gather(*[probe.run() for _ in range(6)])

    results = asyncio.run(scenario())
    assert calls["n"] == 1, (
        f"{calls['n']} probes ran for 6 reads — the start window broke "
        "single-flight")
    assert all(r["ok"] is True for r in results)


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
