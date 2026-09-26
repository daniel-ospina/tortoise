"""#3060 (P0) — the capture path must never block the event loop.

The outage this file pins: the v2 extraction ran SYNCHRONOUSLY inside the
``async def`` /v1/sessions handler. On a stalled provider call it blocked in
``extractor_v2._call_once``'s ``t.join(timeout=deadline_s)`` — up to the
token-scaled ``_scaled_deadline(600, max_tokens)`` (~819s at a 16K budget,
per retry). That froze the SINGLE event loop, so:

1. ``/health`` went unanswered → Fly's HTTP check went critical
   (``context deadline exceeded``);
2. ``tortoise-y4mjjq`` runs one machine, so the proxy found no healthy
   candidate and dropped ALL traffic
   (``[PR01] no known healthy instances found for route tcp/443``);
3. every dashboard boot call (``/v1/user/identity``, ``/v1/organizations``,
   ``/v1/onboarding/state``) failed together → the user-visible
   "Failed to fetch".

One slow model call took down the whole product — not just the capture.

Diagnosis evidence (issue #3060): ``py-spy dump`` showed the event-loop
thread itself parked in ``_wait_for_tstate_lock`` ← ``join`` ← ``_call_once``
← … ← ``capture_session``; while hung the process was idle (0% CPU, 3.0 GB
RAM free, 21/10240 fds, 8% disk) and TCP :8000 still accepted, so it was the
app, not the proxy or the network.

The tests below assert the INVARIANT (other requests keep being served while
a capture is stalled) — a future refactor that reintroduces a blocking call on
the loop fails here regardless of shape. (One deliberate exception pins the
implementation the invariant depends on: the capture pool's thread-name prefix
in the mechanism test. The second exception this file used to carry — the
`/health` probe actually running in the liveness test — was REMOVED by #2850,
which made the handler collect NO request-path I/O; asserting the probe ran
would now pin the superseded design rather than the guarantee. See that test.)
"""
from __future__ import annotations

import asyncio
import itertools
import os
import threading
import time
from contextlib import suppress

import httpx
import pytest

# Hosted-surface tests reuse test_hosted_api's authenticated TestClient
# fixture (temp-DB SDK patching + the session-recording consent seed), which
# also installs the module-level env (pepper/encryption key) on import.
from tests.test_hosted_api import (
    TEST_ORG_ID,
)
from tests.test_hosted_api import (
    client as client,
)
from tortoise.sdk import TortoiseSDK

# A stall long enough to be unambiguous, short enough to keep CI quick.
STALL_S = 4.0
# Secondary signal: a blocked extraction shows up as one ~STALL_S interval
# inside the stall window. Deliberately loose — the PRIMARY signal (tick count)
# is what discriminates; a red CI from a >2s scheduler/GC gap would cost far
# more than this redundancy is worth (review finding).
LOOP_BUDGET_S = 3.0
# Primary signal: ticks the loop managed INSIDE the stall window. A blocked
# loop yields 0 (the next tick can only happen once the freeze releases); a
# free loop yields ~STALL_S/0.05 ≈ 80.
MIN_TICKS_IN_STALL = 10
# Bound on the wait for the fake to report that the capture entered its stall,
# before the /health probe is issued (the liveness test below). Generous and
# only reached on the failure path: the endpoint's pre-stall synchronous setup
# is legitimately slow on a loaded runner (measured ~4.75s for a max-size
# 500-turn capture, #3086), and a capture that never starts must fail on the
# `"entered" in state` assertion rather than hang the suite.
STALL_START_WAIT_S = 60.0
# NOTE: this endpoint ALSO does bounded synchronous graph work on the event
# loop (turn upserts, session MERGE, tenant-vocab build). That is a SEPARATE,
# tracked defect — measured at ~4.75s for a max-size 500-turn capture (#3086)
# — and is deliberately not what this file measures: these tests pin the
# extraction (unbounded, provider-dependent) and the liveness-pool isolation.

_CONV = [
    {"role": "user",
     "content": "The database schema needs normalization before the release."},
    {"role": "assistant",
     "content": "Agreed — I'll write the migration this week."},
]

# A second, distinct session (the capacity-limit test must not be a replay).
_OTHER_CONV = [
    {"role": "user",
     "content": "We should split the billing service out of the monolith."},
    {"role": "assistant",
     "content": "I'll draft the extraction boundary next week."},
]

# A harness name makes the team-visible last-error key exist (no-harness
# captures register none), so the capacity test can prove a 429 is not
# recorded as a team capture failure.
_HARNESS = "claude"


@pytest.fixture(autouse=True)
def llm_extraction_provider(monkeypatch):
    """Offline mock seam (#822) — no network; the v2 seam is patched per test."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)


@pytest.fixture(autouse=True)
def _reset_capture_in_flight():
    """The in-flight counter is a module global: a leak in one test would make
    the NEXT test's first capture 429 (cross-test ordering coupling)."""
    import tortoise.hosted_api as ha_mod

    ha_mod._CAPTURE_IN_FLIGHT = 0
    ha_mod._CAPTURE_SESSIONS.clear()
    yield
    ha_mod._CAPTURE_IN_FLIGHT = 0
    ha_mod._CAPTURE_SESSIONS.clear()


@pytest.mark.parametrize("mode", ["v2", "m2"])
def test_capture_extraction_runs_off_the_event_loop(client, monkeypatch, mode):
    """The mechanism: the extraction runs in a capture worker, not the loop.

    Both extractor branches are covered — the m2 branch
    (`TORTOISE_SESSION_EXTRACTOR=m2`) had no coverage in this file's first
    revision, so a signature/argument regression there would have gone
    unnoticed.

    A worker thread has no running event loop, so `asyncio.get_running_loop()`
    inside the extraction is the discriminator; the thread NAME additionally
    pins that it ran on the dedicated capture pool rather than the shared
    default executor (the #3060-review starvation path).
    """
    seen: dict[str, object] = {}

    if mode == "m2":
        monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
        target = "_extract_session_llm"
    else:
        monkeypatch.delenv("TORTOISE_SESSION_EXTRACTOR", raising=False)
        target = "_extract_session_v2"

    def _fake_extract(_self, windowed, session_id, now, **kw):
        try:
            asyncio.get_running_loop()
            seen["ran_on_loop"] = True
        except RuntimeError:
            seen["ran_on_loop"] = False
        seen["thread"] = threading.current_thread().name
        return [], {}

    monkeypatch.setattr(TortoiseSDK, target, _fake_extract)
    r = client.post("/v1/sessions", json={"conversation": _CONV})

    assert "ran_on_loop" in seen, (
        f"[{mode}] the extraction never ran ({r.status_code}: {r.text[:200]})")
    assert seen["ran_on_loop"] is False, (
        f"[{mode}] the capture extraction ran ON the event loop — a stalled "
        f"model call would freeze /health and take the whole service down "
        f"(#3060)")
    assert str(seen["thread"]).startswith("capture-extract"), (
        f"[{mode}] the extraction ran on {seen['thread']!r}, not the dedicated "
        f"capture pool — long stalls there would occupy the SHARED default "
        f"executor's workers and starve every other ``to_thread`` caller out "
        f"of it (the auth middleware's abuse hooks among them) (#3060)")


def test_stalled_capture_does_not_freeze_the_event_loop(client, monkeypatch):
    """The invariant: while a capture is stalled, the API keeps answering.

    Every signal is order-independent — each reads observed state (tick
    timestamps, or the fake's own entry/exit events) against the stall window
    the fake records, so none can pass by accident of scheduling:

    * ``ticks_in_stall`` — the PRIMARY signal: ticks the loop completed while
      the extraction was stalled. A blocked loop yields 0, because the next
      tick can only run once the freeze releases. This is immune to the
      sub-second synchronous graph work the endpoint legitimately does.
    * the worst tick interval INSIDE the stall window. Only intervals fully
      inside are considered: setup work before the capture reached the
      extraction is legitimately slow (seconds) on a loaded runner and would
      otherwise be misread as a freeze. A call that blocks for part of the
      stall still shows up here as one long interval.
    * the ``/health`` request is answered while the stall is still OPEN. The
      probe is fired only after the fake signals ENTRY (a shared event, not a
      fixed sleep, asserted at the point it matters — so a run whose probe
      would precede the stall fails instead of proving nothing), and the
      answer is read against the fake's EXIT event. This signal needs no
      cross-thread clock ordering (#3581) — unlike the tick filters above,
      which compare the worker's ``state`` timestamps with loop-sampled ticks
      and are sound because ``time.perf_counter()`` is a process-wide
      monotonic clock read only after the worker completed. The probe must
      still complete inside the stall, whose ``STALL_S`` budget is orders of
      magnitude above the in-memory /health path; a probe that merely queues
      behind a blocking capture fails. The endpoint's pre-stall synchronous
      setup can run for seconds on a loaded runner (and #4304 lengthens it),
      so the probe is issued only after the fake reports the stall has
      STARTED — waiting on the fake's own ENTRY event, not a fixed sleep,
      puts it inside the freeze window by construction and keeps both signals
      measuring what they claim to.

    Mutation check (must stay true): calling the extraction inline
    (`return fn(*args, **kwargs)` instead of dispatching to the pool) makes the
    primary assertion fail (0 ticks in the window); with the tick guard
    neutralized, the health-in-stall guard fails too (measured, #3581) — no
    single signal can be satisfied by a blocking capture. The narrower
    regression of moving it back to the SHARED pool via `asyncio.to_thread`
    still runs off-loop and passes HERE — it is pinned by the pool-name
    assertion in `test_capture_extraction_runs_off_the_event_loop`, which
    fails for both branches (measured).
    """
    from tortoise.hosted_api import app

    state: dict[str, float] = {}
    # Shared entry/exit flags (#3581): observed by the event loop, set by the
    # worker thread running the fake. Used to OPEN the window deterministically
    # and to read whether /health was answered while it was still open — never
    # to compare two clocks sampled in different execution contexts.
    entered_evt = threading.Event()
    exited_evt = threading.Event()

    def _stalled_extract(_self, windowed, session_id, now, **kw):
        state["entered"] = time.perf_counter()
        entered_evt.set()
        time.sleep(STALL_S)  # stand-in for a wedged provider call
        state["exited"] = time.perf_counter()
        exited_evt.set()
        return [], {}

    monkeypatch.setattr(TortoiseSDK, "_extract_session_v2", _stalled_extract)

    async def _run():
        ticks: list[float] = []
        stop = {"done": False}

        async def _ticker():
            ticks.append(time.perf_counter())
            while not stop["done"]:
                await asyncio.sleep(0.05)
                ticks.append(time.perf_counter())

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            tick = asyncio.create_task(_ticker())
            # Tick FIRST: the ticker must already be mid-sleep when the
            # capture blocks, otherwise a frozen loop would not register.
            await asyncio.sleep(0.05)
            capture = asyncio.create_task(
                ac.post("/v1/sessions", json={"conversation": _CONV}))
            # Issue the probe only once the capture is INSIDE its stall. The
            # endpoint does bounded synchronous setup BEFORE the extraction
            # starts (seconds on a loaded runner — and #4304 lengthens it — the
            # very interval the tick-interval check above excludes). Probing
            # concurrently races that setup: on a slow runner the probe is
            # already answered before the stall begins, so the run proves
            # nothing about liveness (#3060). Waiting on the fake's own ENTRY
            # event (not a fixed sleep, and not the worker's ``state`` dict, so
            # no cross-thread clock ordering is read — #3581) makes the probe
            # land inside the freeze window by construction. Bounded, and it
            # also stops as soon as the capture has SETTLED without reaching
            # the extraction (a fast endpoint error), so a failure here stays
            # fast instead of burning the whole bound before the guard below
            # reports it.
            _stall_deadline = time.perf_counter() + STALL_START_WAIT_S
            while (not entered_evt.is_set()
                   and not capture.done()
                   and time.perf_counter() < _stall_deadline):
                await asyncio.sleep(0.05)
            # Asserted HERE, before the probe: on an exhausted wait the request
            # below would be served BEFORE the stall opened and the run would
            # pass vacuously on the exit-event read (#3581 review) — the
            # unconditional run-validity guard the old ordering assert carried.
            assert entered_evt.is_set(), (
                "the capture never reached the extraction within "
                f"{STALL_START_WAIT_S:.0f}s — the stall window never opened, "
                "so this run proves nothing (#3060)"
                + (
                    " — the capture finished without entering the extraction: "
                    f"{capture.exception() or capture.result()!r}"
                    if capture.done()
                    else f" — the capture is still pending after "
                         f"{STALL_START_WAIT_S:.0f}s"
                ))
            health = await ac.get("/health")
            # The invariant, read the moment the response is in hand: the stall
            # must still be OPEN. No clocks compared.
            health_served_in_stall = not exited_evt.is_set()
            cap = await capture
            stop["done"] = True
            await tick
        return ticks, health, health_served_in_stall, cap

    ticks, health, health_served_in_stall, cap = asyncio.run(_run())

    assert health.status_code == 200, health.text

    entered, exited = state["entered"], state["exited"]

    in_stall = [t for t in ticks if entered < t < exited]
    assert len(in_stall) >= MIN_TICKS_IN_STALL, (
        f"the event loop completed only {len(in_stall)} tick(s) during a "
        f"{STALL_S:.1f}s capture stall (need {MIN_TICKS_IN_STALL}) — the "
        f"capture is blocking the event loop, so /health goes unanswered, "
        f"Fly drops the machine and the proxy returns nothing at all for "
        f"EVERY request (#3060)")

    # A blocking call also shows up as a long tick interval INSIDE the window.
    # Only intervals fully inside are considered: the synchronous setup this
    # endpoint does before the extraction (bounded graph work, tracked
    # separately — see the module note) can take seconds on a loaded runner,
    # and an interval that merely *starts* before the window would misread
    # that as a freeze.
    inside = [(a, b) for a, b in itertools.pairwise(ticks)
              if a >= entered and b <= exited]
    if inside:
        worst = max(b - a for a, b in inside)
        assert worst < LOOP_BUDGET_S, (
            f"the event loop stalled for {worst:.2f}s inside the stall window "
            f"(budget {LOOP_BUDGET_S}s) while a capture was stalled for "
            f"{STALL_S:.1f}s — a blocked event loop means /health goes "
            f"unanswered, Fly drops the machine and the proxy returns nothing "
            f"at all for EVERY request (#3060)")

    assert health_served_in_stall, (
        f"the /health request did not return inside the {STALL_S:.1f}s stall "
        f"even though the event loop kept ticking ({len(in_stall)} ticks) — "
        "the liveness handler's own request path is blocking or queued behind "
        "the capture, not the event loop (#3060)")

    assert cap.status_code == 200, cap.text


def test_health_answers_while_the_default_executor_is_saturated(
        client, monkeypatch):
    """#3060 review finding: liveness must not queue behind the shared pool.

    The first revision moved the extraction to `asyncio.to_thread`, which uses
    the loop's SHARED default executor — at the time, also the pool /health's DB
    probe (and the auth middleware's abuse hooks) rode. Six-plus concurrent
    stalls (prod runs 2 vCPU → `min(32, cpu+4)` workers) would therefore leave
    that probe queued for minutes, miss Fly's 15s check timeout, and drop the
    machine exactly as in the original outage — one level down, with nothing
    blocking the loop at all.

    This test occupies EVERY default-executor worker with a blocking task and
    then requires /health to answer within a short budget. `wait_for` (rather
    than a bare await) is the assertion: if the response is blocked on the
    saturated pool it never arrives, so this fails instead of the suite hanging.

    #2850 (P0) then removed the last request-path I/O: the handler now reads an
    in-memory `_HEALTH_PROBE.snapshot()` and returns, so "does the handler probe
    on the request path?" is the WRONG question — the design answer is "never".
    This test previously asserted the opposite (`assert probed`: the request must
    have run the probe), which pinned the pre-#2850 design and now fails by
    construction. The contract that survives, and is asserted below, is the one
    that actually matters: liveness answers within budget while every shared
    executor worker is saturated. The snapshot's own self-heal probe runs on a
    single bounded daemon thread and is pinned off for this window (see the
    quiesce note below), so it cannot mask or counterfeit a regression here.

    The contract asserted is not "the probe runs on the request path" (the
    pre-#2850 assertion) but "the request path takes no DB I/O and no executor
    hand-off that reaches either witnessed seam, inside the request window".
    Three assertions guard that contract; none covers every shape:

    * BUDGET (``elapsed < HEALTH_BUDGET_S``, via ``wait_for``). Catches a probe
      that runs SYNCHRONOUSLY on the loop and an UNBOUNDED
      ``await asyncio.to_thread(_probe_db)``: both keep the handler from
      returning, so the request times out before either witness below is ever
      consulted.
    * INVOCATION WITNESS (``probe_calls``). ``_HEALTH_PROBE``'s probe function
      resolves ``ha_mod._probe_db`` at CALL time, so replacing that symbol means
      any probe that reaches this seam is recorded, then blocks far past the
      budget and raises. Catches hand-offs that START A WORKER THREAD — the
      module's own ``_HEALTH_PROBE.wait()``/``begin()`` (a raw
      ``HEALTH_PROBE_THREAD_NAME`` thread), a thread that spoofs that name, or a
      direct ``executor.submit(_probe_db)`` on a dedicated executor — because
      the record is taken before the sleep and no thread name is filtered. It is
      NOT timing-independent: a bounded hand-off that times out before its
      worker ever starts leaves no record (see the submission witness, which
      covers that case). The record is only sound because the background
      refresher is QUIESCED for the window (below), so an invocation can only
      come from the request path.
    * SUBMISSION WITNESS (``submissions``). Records every
      ``asyncio.BaseEventLoop.run_in_executor`` submission for the request
      window, whichever executor it targets (the SHARED default pool or a
      DEDICATED one). This is the one that catches the documented pre-#2850
      shape re-introduced with a cached fallback:
      ``await asyncio.wait_for(asyncio.to_thread(_probe_db), timeout=...)``
      (and its ``loop.run_in_executor(None, _probe_db)`` twin) submits to the
      SHARED, fully saturated pool, so the worker NEVER STARTS, the invocation
      witness never fires, the timeout falls back to the cached snapshot, and
      the request still answers inside budget. A dedicated-executor hand-off is
      caught here too, at submit time, before any worker scheduling. The
      submission is recorded at the submit call itself, so it does not depend
      on the worker ever running — which is what makes it independent of how
      promptly that worker would have been scheduled.

    OUT OF SCOPE (not "gaps" in the guard, just its edge): I/O or a hand-off
    that reaches neither seam and does not block the handler — a direct
    ``_get_proj().g.query(...)``, a callable doing its own DB I/O handed to a
    raw thread or a separate executor, or any submission made after the request
    window (the patch is uninstalled). The budget catches those only if the
    handler blocks. The deferred ``loop.call_later(0.5, ...)`` shape escapes
    because its timer does not fire before the loop closes; a post-window
    submit that DOES fire is caught by the INVOCATION witness once the pooled
    hogs are released — an accident of test lifetime, not a guarantee. The
    submission witness records by ENTRY POINT, not callable identity, so a NEW
    callable routed through ``to_thread``/``run_in_executor`` IS caught.

    Recording every caller (rather than
    filtering out the refresher's thread NAME) is deliberate: the one name a
    filter excludes is exactly the worker the module's own ``wait()``/``run()``
    start, so a ``_HEALTH_PROBE.wait()`` in the handler — or a hand-off that
    simply names its thread ``HEALTH_PROBE_THREAD_NAME`` — walked straight
    through the previous revision.

    Finally the response is tied to the PRIMED snapshot by a distinctive
    sentinel (below): a handler returning a hardcoded healthy payload passes
    neither the sentinel nor ``db.ok``.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import _HEALTH_PROBE, app

    # Prime the process-global `_HEALTH_PROBE` with a SENTINEL result BEFORE
    # saturating the pool. The sentinel does two jobs:
    #
    # 1. It makes the response provably the snapshot. `_view_locked` returns a
    #    COPY (`dict(self._result)`) of whatever the probe produced, so a
    #    marker key on that result survives the read and appears verbatim under
    #    `db` in the response. Without it, a handler returning a hardcoded
    #    `{"status":"ok","db":{"ok":True,...},"probe":{...,
    #    "result_age_s":0.0,...}}` satisfies every other assertion while never
    #    touching the probe at all (review finding).
    # 2. It makes the run deterministic rather than order-dependent.
    #    `snapshot()` deliberately NEVER waits (that is its whole point,
    #    #2850), so without a completed result `db.ok` and
    #    `probe.result_age_s` would depend on whether some earlier test in the
    #    file happened to warm the shared probe. Observed directly: a full-file
    #    run answered `{"status":"degraded","db":{"ok":false,
    #    "error":"probe in flight (0.4s)"}}` while the identical test passed in
    #    isolation. `wait()` is the module's own blocking entry point,
    #    documented "for non-async probes/tests".
    #
    # `reset()` first, so a probe the lifespan refresher started with the REAL
    # `_probe_db` cannot be the result `wait()` joins (it would lack the
    # sentinel). `reset()` abandons any such worker as a daemon thread and
    # invalidates its write by sequence, so the `wait()` below is the probe
    # that produces the completed result.
    _PRIMED_SENTINEL = "primed-snapshot-sentinel-3458"

    def _sentinel_probe() -> dict:
        return {"ok": True, "latency_ms": 0.0, "error": None,
                "primed_sentinel": _PRIMED_SENTINEL}

    monkeypatch.setattr(ha_mod, "_probe_db", _sentinel_probe)
    _HEALTH_PROBE.reset()
    primed = _HEALTH_PROBE.wait()
    assert primed.get("ok") is True, primed
    assert primed.get("primed_sentinel") == _PRIMED_SENTINEL, primed

    HOG_WAIT_S = 30.0
    # Derived from the probe's OWN documented worst case — ``PROBE_TIMEOUT``
    # bounds ONE attempt and a transient connect failure retries once after
    # ``PROBE_RETRY_DELAY``, i.e. ``2 * PROBE_TIMEOUT + PROBE_RETRY_DELAY`` —
    # plus 1s slack, so a slow-but-healthy probe can never red the suite and
    # the bound stays honest if those symbols change (review finding: a
    # hardcoded 4.0 sat 0.9s above the design's own worst case). Below the
    # production probe budget (5.6s), so the verdict comes from design, not
    # timer ordering.
    from tortoise.monitoring import (
        PROBE_RETRY_DELAY,
        PROBE_TIMEOUT,
    )
    HEALTH_BUDGET_S = 2 * PROBE_TIMEOUT + PROBE_RETRY_DELAY + 1.0

    # #2850's contract is stronger than "answers fast here": the request path
    # must perform NO I/O and NO thread hand-off at all. Two records guard the
    # seams this test can actually see — an INVOCATION record on the
    # coordinator's `_probe_db` seam, and a SUBMISSION record on the event
    # loop's executor entry point — because neither alone covers every shape
    # (the catch split is stated in the docstring).
    #
    # The invocation record comes from replacing `ha_mod._probe_db`: the
    # coordinator's lambda resolves that symbol at CALL time, so any probe that
    # reaches THIS seam is recorded (every invocation, no thread-name filter),
    # then blocks far past the budget and raises. It does NOT cover DB I/O on
    # the request path that reaches the database by another route (see the
    # docstring's OUT OF SCOPE paragraph).
    #
    # QUIESCE THE REFRESHER — verified, not assumed. This test enters the
    # `client` fixture, and that fixture wraps the app in `TestClient(app)`,
    # which RUNS the lifespan: `_lifespan` arms `_health_probe_loop` as
    # `app.state._health_probe_task`. Instrumented on this very test, that task
    # is live and pending (`<Task pending ... coro=<_health_probe_loop()>>`)
    # throughout the body — so the background refresher IS armed, and its
    # `run()` calls `begin()` UNCONDITIONALLY (`snapshot()`'s read path is the
    # gated one). Neutralise that entry point and pin the refresh budget, so no
    # legitimate non-request caller can start a probe in the window at ANY
    # configured interval. The request path uses `snapshot()` and the prime used
    # `wait()` — never `run()` — so neither is affected. With the refresher
    # quiesced and `primed` above just completing a probe, nothing but the
    # request path can reach the seam, so an empty record is a real guarantee.
    #
    # The stub returns `{}` DELIBERATELY: `_health_probe_loop` discards `run()`'s
    # return value, so `{}` is a sentinel meaning "neutralised", not a probe
    # result. A mutant that surfaces `run()`'s return into the response would
    # serve that `{}` — no `ok`, no sentinel — and is caught by the
    # `db.get("ok")` / sentinel assertions below, which use `.get` so it fails
    # on a described assertion rather than an incidental `KeyError: 'ok'`.
    probe_calls: list[str] = []

    async def _no_refresher_probe(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(ha_mod._HEALTH_PROBE, "run", _no_refresher_probe)
    # Pin the refresh budget far above this window: `snapshot()`'s
    # `begin(if_stale=True)` self-heal must not start a probe either, which at
    # a small operator period it otherwise legitimately could.
    monkeypatch.setattr(ha_mod, "_health_probe_interval", lambda: 3600.0)

    def _blocking_probe(*args, **kwargs):
        # INVOCATION WITNESS — record EVERY invocation BEFORE the sleep. A
        # bounded hand-off that the handler times out of leaves its
        # AssertionError stranded on an unretrieved Future, so the raise alone
        # cannot prove the request path was clean; this record can. No
        # thread-name filter (the refresher is quiesced above), so a hand-off
        # that names its thread `HEALTH_PROBE_THREAD_NAME` is still recorded.
        probe_calls.append(threading.current_thread().name)
        # Any request-path probe that reaches this seam now blocks far past the
        # budget.
        time.sleep(HEALTH_BUDGET_S * 2)
        raise AssertionError(
            "the /health REQUEST PATH invoked the DB probe "
            "(#2850: it must not)")
    monkeypatch.setattr(ha_mod, "_probe_db", _blocking_probe)

    async def _run():
        release = threading.Event()

        def _hog():
            release.wait(timeout=HOG_WAIT_S)

        loop = asyncio.get_running_loop()
        # More hogs than the pool has workers, so every worker is taken.
        workers = max(4, (os.cpu_count() or 1) + 4)
        hogs = [loop.run_in_executor(None, _hog) for _ in range(workers + 4)]

        # SUBMISSION WITNESS — record every executor submission made during the
        # REQUEST window (the hogs above go through this same entry point and
        # must not be recorded, so the patch goes on AFTER they are submitted,
        # and is restored in a `finally` so it can never leak to another test).
        submissions: list[str] = []
        _orig_run_in_executor = asyncio.BaseEventLoop.run_in_executor

        def _record_submission(_self, _ex, _fn, *a, **k):
            submissions.append(getattr(_fn, "__name__", repr(_fn)))
            return _orig_run_in_executor(_self, _ex, _fn, *a, **k)

        try:
            await asyncio.sleep(0.3)  # let the hogs claim every worker
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as ac:
                asyncio.BaseEventLoop.run_in_executor = _record_submission
                try:
                    started = time.perf_counter()
                    try:
                        r = await asyncio.wait_for(ac.get("/health"),
                                                   timeout=HEALTH_BUDGET_S)
                    except TimeoutError:
                        waited = time.perf_counter() - started
                        raise AssertionError(
                            f"/health did not answer within {HEALTH_BUDGET_S}s "
                            f"(still waiting after {waited:.2f}s) while every "
                            f"default-executor worker was busy — /health must "
                            f"serve from the in-memory `_HEALTH_PROBE.snapshot()` "
                            f"and take NO thread hand-off (#2850), so a saturated "
                            f"shared pool must not delay it; a delay here means "
                            f"liveness has been put back on the shared executor, "
                            f"the shape that dropped the machine in #3060"
                        ) from None
                    return r, time.perf_counter() - started, submissions
                finally:
                    asyncio.BaseEventLoop.run_in_executor = _orig_run_in_executor
        finally:
            release.set()
            await asyncio.gather(*hogs, return_exceptions=True)

    r, elapsed, submissions = asyncio.run(_run())

    assert r.status_code == 200, r.text
    # Served from the `_HEALTH_PROBE` snapshot rather than a trivial stub: the
    # probe was primed above, so the response must carry its observability
    # metadata WITH A REAL AGE (`HealthProbe.info()`) AND the sentinel that
    # snapshot's probe result carried.
    #
    # Deliberately stronger than key presence. `info()` emits a `probe` dict
    # whose `result_age_s` stays None until a probe completes, so this cannot be
    # satisfied by a `{"status": "ok", "db": {"ok": True}}` stub with no
    # `probe` key, by the pre-#2850 response shape (which carried no `probe` key
    # at all), or by a `probe` dict that is present but never completed
    # (`result_age_s is None`). A bare `"probe" in r.json()` check would pass
    # the last of those. The SENTINEL is the additional, stronger tie: only a
    # response that echoes `_HEALTH_PROBE`'s completed result can carry
    # `primed_sentinel`, so a handler with a hardcoded healthy payload fails
    # here even with a fabricated `probe` block. `.get` is used so a miss fails
    # on this described assertion, not an incidental `KeyError`.
    assert r.json()["probe"].get("result_age_s") is not None, r.text
    assert r.json()["db"].get("primed_sentinel") == _PRIMED_SENTINEL, r.text
    assert r.json()["db"].get("ok") is True, r.text
    assert not probe_calls, (
        f"/health invoked the DB probe on the request path: {probe_calls} "
        f"(#2850: the request path must take no I/O and no thread hand-off)")
    assert not submissions, (
        f"the /health request path submitted work to an executor: {submissions} "
        f"— the pre-#2850 shape (asyncio.to_thread(_probe_db) or "
        f"run_in_executor(None, _probe_db) with a bounded timeout and a "
        f"cached-snapshot fallback) queues on the SHARED pool, so its worker "
        f"never starts and the invocation witness cannot see it (#3060)")
    assert elapsed < HEALTH_BUDGET_S, (
        f"/health took {elapsed:.2f}s (budget {HEALTH_BUDGET_S}s) while every "
        f"default-executor worker was busy — /health must serve from the "
        f"in-memory `_HEALTH_PROBE.snapshot()` and take NO thread hand-off "
        f"(#2850), so a saturated shared pool must not delay it; a delay here "
        f"means liveness has been put back on the shared executor, the shape "
        f"that dropped the machine in #3060")


def test_capture_capacity_limit_fails_fast_instead_of_queueing(
        client, monkeypatch):
    """#3060 review: a bounded pool with an UNBOUNDED queue is its own outage.

    Four stalled extractions park every capture worker for the token-scaled
    deadline (~800s, retried). Without a cap, every later capture would wait on
    the pool indefinitely while holding its stored-window transcript (MBs) —
    up to fly.toml's hard_limit of connections on a 4GB VM, i.e. an OOM kill,
    which is the same outage class. At capacity the request must therefore fail
    fast (429 + Retry-After), never enqueue without bound.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    monkeypatch.setattr(ha_mod, "_CAPTURE_MAX_IN_FLIGHT", 1)
    release = threading.Event()
    entered = threading.Event()

    def _stalled(_self, windowed, session_id, now, **kw):
        entered.set()
        release.wait(timeout=30)
        return [], {}

    monkeypatch.setattr(TortoiseSDK, "_extract_session_v2", _stalled)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            first = asyncio.create_task(
                ac.post("/v1/sessions", json={"conversation": _CONV}))
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert entered.is_set(), "the first capture never reached the extraction"
            try:
                # A different session, so this cannot be a replay of the first.
                second = await asyncio.wait_for(
                    ac.post("/v1/sessions",
                            json={"conversation": _OTHER_CONV,
                                  "harness": _HARNESS}),
                    timeout=10.0)
            except TimeoutError:
                raise AssertionError(
                    "a capture beyond the in-flight cap never returned within "
                    "10s — it queued on the pool instead of failing fast with "
                    "429 (an unbounded queue holds one transcript per waiting "
                    "request: the #3060 OOM path)") from None
            finally:
                release.set()
            first_resp = await first
            return second, first_resp

    second, first_resp = asyncio.run(_run())

    assert second.status_code == 429, (
        f"a capture beyond the in-flight cap returned {second.status_code}, not "
        f"429 — it queued instead of failing fast ({second.text[:200]})")
    assert "Retry-After" in second.headers, second.headers
    assert first_resp.status_code == 200, first_resp.text
    # No leaked slot: the counter must return to zero once the work is done.
    assert ha_mod._CAPTURE_IN_FLIGHT == 0, ha_mod._CAPTURE_IN_FLIGHT
    # A capacity 429 is a SERVER condition, not the team's capture failing: it
    # must not land in the team-visible last-error slot (the dashboard sub-line
    # reads it, and the advertised retry would then clear it — misreporting
    # capacity as a team fault and masking any genuine prior error).
    state = ha_mod._get_onboarding_state(TEST_ORG_ID)
    assert state.get(f"session_capture_last_error_{_HARNESS}") in (None, ""), state


def test_capture_admission_is_reserved_before_the_extraction(client, monkeypatch):
    """#3060: the cap must bound ADMISSION, not just running extractions.

    Reserving a slot at admission (rather than only checking the in-flight
    count) is what stops a concurrent burst from all passing the gate and then
    queueing on the pool without bound — the exact post-outage retry-storm
    shape. The first request is parked INSIDE the pipeline but BEFORE any
    extraction (awaiting, so the loop stays free), with the cap at 1: a second
    request must already be refused. A check-only gate admits both.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    monkeypatch.setattr(ha_mod, "_CAPTURE_MAX_IN_FLIGHT", 1)
    entered = threading.Event()
    release = threading.Event()

    async def _parked_impl(body, request, team, slot=None, state=None):
        entered.set()
        await asyncio.to_thread(release.wait, 30)
        return {"ok": True}

    monkeypatch.setattr(ha_mod, "_capture_session_impl", _parked_impl)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            first = asyncio.create_task(
                ac.post("/v1/sessions", json={"conversation": _CONV}))
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert entered.is_set(), "the first capture never reached the pipeline"
            try:
                try:
                    second = await asyncio.wait_for(
                        ac.post("/v1/sessions",
                                json={"conversation": _OTHER_CONV}),
                        timeout=10.0)
                except TimeoutError:
                    raise AssertionError(
                        "a concurrent capture was ADMITTED while the first "
                        "still held its reservation (it parked instead of "
                        "429ing) — a check-only gate lets a burst all pass and "
                        "then queue without bound (#3060)") from None
            finally:
                release.set()
            first_resp = await first
            return second, first_resp

    second, first_resp = asyncio.run(_run())

    assert second.status_code == 429, (
        f"a concurrent capture was admitted ({second.status_code}) while the "
        f"first still held its reservation — the cap would not bound the queue "
        f"({second.text[:200]})")
    assert first_resp.status_code == 200, first_resp.text
    assert ha_mod._CAPTURE_IN_FLIGHT == 0, ha_mod._CAPTURE_IN_FLIGHT


def test_capture_slot_is_held_until_the_worker_finishes(client, monkeypatch):
    """#3060 review: the slot must track REAL work, not the awaiting task.

    The cap only bounds anything if a cancellation (client disconnect, request
    timeout) does not release the slot while the worker is still parked —
    otherwise the cap is silently exceeded and the unbounded queue returns,
    which is the whole point of the bound. The release is attached to the
    CONCURRENT future and the await is a plain `asyncio.wrap_future(cfut)`: its
    cancellation cannot cancel a running worker, so only `worker_done` frees
    the slot (no shield is used or needed).
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    release = threading.Event()
    entered = threading.Event()

    def _stalled(_self, windowed, session_id, now, **kw):
        entered.set()
        release.wait(timeout=30)
        return [], {}

    monkeypatch.setattr(TortoiseSDK, "_extract_session_v2", _stalled)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            task = asyncio.create_task(
                ac.post("/v1/sessions", json={"conversation": _CONV}))
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert entered.is_set(), "the capture never reached the extraction"
            while_running = ha_mod._CAPTURE_IN_FLIGHT
            task.cancel()  # client goes away mid-extraction
            with suppress(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.2)  # let the cancellation settle
            after_cancel = ha_mod._CAPTURE_IN_FLIGHT
            release.set()
            for _ in range(200):  # the worker finishes and releases
                if ha_mod._CAPTURE_IN_FLIGHT == 0:
                    break
                await asyncio.sleep(0.05)
            return while_running, after_cancel, ha_mod._CAPTURE_IN_FLIGHT

    while_running, after_cancel, after_release = asyncio.run(_run())

    assert while_running == 1, while_running
    assert after_cancel == 1, (
        f"the capture slot was released on cancellation ({while_running} → "
        f"{after_cancel}) while the worker was still running — the cap would "
        f"then be silently exceeded and the unbounded queue returns (#3060)")
    assert after_release == 0, after_release


def test_capture_slot_is_released_when_the_extraction_fails(client, monkeypatch):
    """#3060: an extraction that RAISES must still release its reservation.

    Error paths are where counter leaks hide: the slot is owned by the
    extraction's CONCURRENT future, so a raising worker must run the
    done-callback exactly like a returning one. A leak here ratchets the cap
    down to a permanent 429 after enough provider errors — precisely the
    post-outage retry-storm shape (and on prod the pool is 4 workers, so four
    failed captures would wedge every later one).
    """
    import tortoise.hosted_api as ha_mod

    def _boom(_self, windowed, session_id, now, **kw):
        raise ValueError("provider stalled then failed (simulated)")

    monkeypatch.setattr(TortoiseSDK, "_extract_session_v2", _boom)
    r = client.post("/v1/sessions",
                    json={"conversation": _CONV, "harness": _HARNESS})

    assert r.status_code >= 400, f"{r.status_code}: {r.text[:200]}"
    assert ha_mod._CAPTURE_IN_FLIGHT == 0, (
        f"the capture slot leaked on a FAILED extraction "
        f"(_CAPTURE_IN_FLIGHT={ha_mod._CAPTURE_IN_FLIGHT}) — repeated provider "
        f"errors would ratchet the cap down to a permanent 429 (#3060)")


def test_mcp_capture_path_reserves_admission(client, monkeypatch):
    """#3060 review (P1): the MCP twin must RESERVE the same admission slot.

    ``tortoise_session_capture`` dispatches into the same
    ``_capture_session_impl``/``_CAPTURE_EXECUTOR`` as POST /v1/sessions (the
    MCP app is mounted in the same process), so a path that skips the
    reservation leaves the cap unbound there: the reviewer measured
    ``_CAPTURE_MAX_IN_FLIGHT=2`` with FOUR concurrent extractions when MCP
    called the impl with ``slot=None`` — the unbounded-queue path the cap
    exists to close.

    Behavioral on purpose. A first revision asserted the call's presence in
    the SOURCE and passed under mutation for a commented-out call, a renamed
    callee, and a reserve-then-release refactor (all measured), while only
    re-testing the shared primitive. Here phase A proves an MCP capture HOLDS
    a slot for as long as its extraction runs, and phase B proves it is
    REFUSED when another surface holds the last one.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app
    from tortoise.mcp_auth import (
        _current_graph_id,
        _current_graph_namespace,
        _current_legacy_full_access,
        _current_org_id,
        _current_org_limits,
        _current_scopes,
    )
    from tortoise.mcp_server import tortoise_session_capture

    entered = threading.Event()
    release = threading.Event()
    out: dict = {}

    def _stalled(_self, windowed, session_id, now, **kw):
        entered.set()
        release.wait(timeout=30)
        return [], {}

    monkeypatch.setattr(TortoiseSDK, "_extract_session_v2", _stalled)

    def _call_mcp(session_id: str) -> None:
        # The MCP tool reads the RESOLVED team from ContextVars (mcp_auth); a
        # fresh thread starts with an empty context, so set them there (same
        # shape as the delivery-tenancy MCP test).
        ctx_vars = [_current_org_id, _current_org_limits, _current_graph_id,
                    _current_graph_namespace, _current_scopes,
                    _current_legacy_full_access]
        toks = [v.set(val) for v, val in zip(
            ctx_vars,
            [TEST_ORG_ID, {"max_points": 100000, "max_sessions": None},
             None, None, ["graphs:read", "graphs:write"],
             False],
            strict=True)]
        try:
            out["result"] = tortoise_session_capture(
                conversation=_CONV, harness=_HARNESS, session_id=session_id)
        except Exception as e:  # pragma: no cover - surfaced by assertions
            out["exc"] = repr(e)
        finally:
            for var, tok in zip(ctx_vars, toks, strict=True):
                var.reset(tok)

    async def _run():
        # Phase A (cap 2): an MCP capture ALONE must hold a slot while its
        # extraction runs — the half that catches "reserve then release",
        # a commented-out call, or a renamed callee.
        monkeypatch.setattr(ha_mod, "_CAPTURE_MAX_IN_FLIGHT", 2)
        a = threading.Thread(target=_call_mcp, args=("mcp-holds-a",))
        a.start()
        for _ in range(400):
            if entered.is_set():
                break
            await asyncio.sleep(0.05)
        assert entered.is_set(), "the MCP capture never reached the extraction"
        held_mcp = ha_mod._CAPTURE_IN_FLIGHT
        release.set()
        for _ in range(400):
            if not a.is_alive():
                break
            await asyncio.sleep(0.05)
        a.join(timeout=5)
        first_result = dict(out.get("result") or {})

        # Phase B (cap 1): a REST capture holds the only slot, so the MCP twin
        # must be refused instead of queueing on the shared pool.
        entered.clear()
        release.clear()
        monkeypatch.setattr(ha_mod, "_CAPTURE_MAX_IN_FLIGHT", 1)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            rest = asyncio.create_task(
                ac.post("/v1/sessions", json={"conversation": _CONV}))
            for _ in range(400):
                if entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert entered.is_set(), "the REST capture never reached the extraction"
            b = threading.Thread(target=_call_mcp, args=("mcp-refused-b",))
            b.start()
            for _ in range(400):
                if not b.is_alive():
                    break
                await asyncio.sleep(0.05)
            refused_alive = b.is_alive()
            release.set()
            rest_resp = await rest
        b.join(timeout=5)
        return held_mcp, first_result, dict(out.get("result") or {}), \
            refused_alive, rest_resp

    held_mcp, mcp_ok, mcp_refused, refused_alive, rest_resp = asyncio.run(_run())

    assert not mcp_ok.get("error"), mcp_ok
    assert held_mcp == 1, (
        f"an MCP capture did not hold an admission slot while its extraction "
        f"ran (_CAPTURE_IN_FLIGHT={held_mcp}) — the MCP surface shares the "
        f"capture pool, so the #3060 cap would not bind it and captures would "
        f"queue without bound (measured before the fix: cap=2 with FOUR "
        f"concurrent extractions)")
    assert not refused_alive, (
        "an MCP capture queued instead of being refused while another "
        "surface held the last admission slot — the cap does not bind the "
        "MCP path (#3060)")
    assert mcp_refused.get("status") == 429, mcp_refused
    assert mcp_refused.get("error"), mcp_refused
    assert rest_resp.status_code == 200, rest_resp.text
    assert ha_mod._CAPTURE_IN_FLIGHT == 0, ha_mod._CAPTURE_IN_FLIGHT


def test_same_session_retry_during_an_in_flight_capture_is_refused(
        client, monkeypatch):
    """#3129: a retry for a session ALREADY being captured must not claim 200.

    Moving the extraction off the event loop (#3060) made a window reachable
    that the synchronous handler had closed by construction: a second request
    for the same ``session_id`` is now served WHILE the first extraction is
    parked. It reads ``capture_ok = NULL`` (written only at the very end of a
    successful capture), the replay branch treats NULL as "presumed captured",
    and answers **200 + a success receipt with 0 turns extracted** — for a
    capture whose only real attempt then fails. Silent data loss, and the
    client has been told to retry by the very ``Retry-After`` the capacity
    gate advertises.

    The refusal must happen AT ADMISSION (before anything is written), like
    the capacity gate: a later rejection would itself leave a half-created
    Session behind.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    entered = threading.Event()
    release = threading.Event()

    def _stalled(_self, windowed, session_id, now, **kw):
        entered.set()
        release.wait(timeout=30)
        return [], {}

    monkeypatch.setattr(TortoiseSDK, "_extract_session_v2", _stalled)
    payload = {"conversation": _CONV, "session_id": "s-inflight-3129",
               "harness": _HARNESS}
    receipt_key = f"session_capture_receipt_{_HARNESS}"

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            first = asyncio.create_task(ac.post("/v1/sessions", json=payload))
            for _ in range(400):
                if entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert entered.is_set(), "the first capture never reached the extraction"
            # The registry must be keyed by TENANT+session, not the bare
            # client-chosen session_id (reviewer finding): otherwise one
            # tenant's in-flight capture refuses another tenant's unrelated
            # capture that happens to use the same generic harness name.
            in_flight_keys = list(ha_mod._CAPTURE_SESSIONS)
            assert in_flight_keys == [f"team-001:default:{payload['session_id']}"], (
                f"the in-flight session registry is not tenant-scoped: "
                f"{in_flight_keys} (#3129)")
            try:
                try:
                    second = await asyncio.wait_for(
                        ac.post("/v1/sessions", json=payload), timeout=10.0)
                except TimeoutError:
                    raise AssertionError(
                        "a same-session request did not return within 10s "
                        "while the first capture was still in flight — it "
                        "must be refused at admission, not queued (#3129)"
                    ) from None
                # Sampled BEFORE the first capture is released, so a receipt
                # written by the second request is unambiguous evidence.
                receipt_during = ha_mod._get_onboarding_state(
                    TEST_ORG_ID).get(receipt_key)
            finally:
                release.set()
            first_resp = await first
            return second, first_resp, receipt_during

    second, first_resp, receipt_during = asyncio.run(_run())

    assert second.status_code == 409, (
        f"a retry for a session that is STILL BEING CAPTURED returned "
        f"{second.status_code} ({second.text[:200]}) — a 2xx here means the "
        f"replay branch claimed success for a capture that had not finished "
        f"(and may still fail): the dashboard gets a receipt + 0 extracted "
        f"turns while the graph stays empty (#3129)")
    assert "Retry-After" in second.headers, second.headers
    assert receipt_during in (None, ""), (
        f"the refused request still wrote a capture receipt ({receipt_during!r}) "
        f"— a receipt for a capture that has not completed is the silent "
        f"data-loss signal (#3129)")
    assert first_resp.status_code == 200, first_resp.text
    assert ha_mod._CAPTURE_IN_FLIGHT == 0, ha_mod._CAPTURE_IN_FLIGHT
    assert ha_mod._CAPTURE_SESSIONS == {}, (
        f"the in-flight session registry leaked: {ha_mod._CAPTURE_SESSIONS} — "
        f"a stale entry would refuse every later retry for that session")


def test_in_flight_session_key_outlives_the_extraction(client, monkeypatch):
    """#3129 (reviewer finding): the key must outlive the EXTRACTION, not just it.

    ``capture_ok`` is written only after the event mint, the audit record and
    the receipt — well after the extraction returns. Releasing the session key
    with the extraction future alone left a residual window in which a
    concurrent same-session request was admitted and served 200 + a success
    receipt with 0 turns extracted: the same silent data loss as the primary
    bug, just narrower (reviewer-measured). The key therefore requires BOTH
    the worker and the request's own teardown.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    extraction_entered = threading.Event()
    extraction_release = threading.Event()
    audit_entered = threading.Event()
    audit_release = threading.Event()

    def _stalled_extract(_self, windowed, session_id, now, **kw):
        extraction_entered.set()
        extraction_release.wait(timeout=30)
        return [], {}

    async def _stalled_audit(*_a, **_kw):
        # A post-extraction await: the extraction future has COMPLETED here,
        # but the capture has not (capture_ok is still NULL).
        audit_entered.set()
        await asyncio.to_thread(audit_release.wait, 30)

    monkeypatch.setattr(TortoiseSDK, "_extract_session_v2", _stalled_extract)
    monkeypatch.setattr(ha_mod, "_async_audit", _stalled_audit)
    payload = {"conversation": _CONV, "session_id": "s-postextract-3129",
               "harness": _HARNESS}

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            first = asyncio.create_task(ac.post("/v1/sessions", json=payload))
            for _ in range(400):
                if extraction_entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert extraction_entered.is_set(), "capture never reached the extraction"
            extraction_release.set()
            for _ in range(400):
                if audit_entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert audit_entered.is_set(), (
                "the capture never reached its post-extraction audit — the "
                "residual window this test pins was not reached")
            keys_during = list(ha_mod._CAPTURE_SESSIONS)
            try:
                second = await asyncio.wait_for(
                    ac.post("/v1/sessions", json=payload), timeout=10.0)
            finally:
                audit_release.set()
            first_resp = await first
            return second, first_resp, keys_during

    second, first_resp, keys_during = asyncio.run(_run())

    assert keys_during, (
        "the in-flight session key was released when the EXTRACTION finished, "
        "not when the CAPTURE finished — a concurrent same-session request "
        "in this window is served 200 + a receipt for a capture that may "
        "still fail (#3129)")
    assert second.status_code == 409, (
        f"a same-session request during a capture that had finished "
        f"extracting but was still completing returned {second.status_code} "
        f"({second.text[:200]}) — the #3129 silent-data-loss window (#3129)")
    assert first_resp.status_code == 200, first_resp.text
    assert ha_mod._CAPTURE_IN_FLIGHT == 0, ha_mod._CAPTURE_IN_FLIGHT
    assert ha_mod._CAPTURE_SESSIONS == {}, ha_mod._CAPTURE_SESSIONS


def test_cancelled_after_extraction_marks_the_attempt_failed(client, monkeypatch):
    """#3129 (reviewer P1, cycle 3): abandonment covers the WHOLE attempt.

    The first version of this guard hooked only the extraction await, so a
    cancellation delivered at the two post-extraction awaits (`_async_audit`,
    `_abuse_record_points` — both real `asyncio.to_thread` suspensions before
    the `capture_ok` write) fell outside it. The reviewer measured the
    consequence end-to-end: the session stayed at `capture_ok = NULL`, the key
    drained, and the next same-session POST returned 200 +
    `extraction_mode=replayed` + `extracted=0`, permanently. This is that exact
    scenario — the extraction COMPLETES, the cancel lands in the audit, and the
    outcome must still be recorded.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    extraction_entered = threading.Event()
    extraction_release = threading.Event()
    audit_entered = threading.Event()
    audit_release = threading.Event()

    def _stalled_extract(_self, windowed, session_id, now, **kw):
        extraction_entered.set()
        extraction_release.wait(timeout=30)
        return [], {}

    async def _stalled_audit(*_a, **_kw):
        audit_entered.set()
        await asyncio.to_thread(audit_release.wait, 30)

    monkeypatch.setattr(TortoiseSDK, "_extract_session_v2", _stalled_extract)
    monkeypatch.setattr(ha_mod, "_async_audit", _stalled_audit)
    payload = {"conversation": _CONV, "session_id": "s-postextract-cancel-3129",
               "harness": _HARNESS}

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            task = asyncio.create_task(ac.post("/v1/sessions", json=payload))
            for _ in range(400):
                if extraction_entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert extraction_entered.is_set(), "capture never reached the extraction"
            extraction_release.set()
            for _ in range(400):
                if audit_entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert audit_entered.is_set(), (
                "the capture never reached its post-extraction audit — the "
                "window this test pins was not reached")
            task.cancel()  # client goes away AFTER the extraction returned
            with suppress(asyncio.CancelledError):
                await task
            audit_release.set()
            await asyncio.sleep(0.2)
            for _ in range(400):  # the abandoned request's teardown releases
                if not ha_mod._CAPTURE_SESSIONS:
                    break
                await asyncio.sleep(0.05)
            return list(ha_mod._CAPTURE_SESSIONS)

    drained = asyncio.run(_run())
    assert drained == [], drained

    rows = ha_mod._make_sdk(namespace=TEST_ORG_ID)._get_proj().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.capture_ok, s.capture_extractor",
        params={"sid": "s-postextract-cancel-3129"}).result_set
    assert rows, "the capture never merged its Session row"
    capture_ok, lane = rows[0][0], rows[0][1]
    assert capture_ok is False, (
        f"a capture cancelled at the post-extraction audit left the session at "
        f"capture_ok={capture_ok!r} — NULL is read by the replay rule as "
        f"\"presumed captured\", so the next same-session request gets 200 + a "
        f"success receipt with 0 turns extracted, permanently (#3129)")
    assert lane == "v2", lane


def test_session_key_is_held_until_the_marker_write_lands(client, monkeypatch):
    """#3129 (reviewer P2): the marker write is OFF-loop and the key outlives it.

    Two properties of the abandonment marker, neither visible from the state
    assertions: the write must not run on the event loop (it is a teardown-path
    graph call, and the projection's socket timeout is 10s — the #3060 shape on
    the liveness path), and the session's in-flight key must stay held until it
    lands, or a retry admitted in the gap would be served exactly the
    NULL→replay payload the marker exists to prevent.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    entered = threading.Event()
    release = threading.Event()
    marker_entered = threading.Event()
    marker_release = threading.Event()
    marker_threads: list = []

    def _stalled(_self, windowed, session_id, now, **kw):
        entered.set()
        release.wait(timeout=30)
        return [], {}

    def _slow_marker(_proj, session_id, lane):
        # Stands in for the graph write: it must be running OFF the loop thread.
        marker_threads.append(threading.current_thread().name)
        marker_entered.set()
        marker_release.wait(timeout=30)

    monkeypatch.setattr(TortoiseSDK, "_extract_session_v2", _stalled)
    monkeypatch.setattr(ha_mod, "_capture_abandoned_marker", _slow_marker)
    payload = {"conversation": _CONV, "session_id": "s-marker-hold-3129",
               "harness": _HARNESS}
    key = "team-001:default:s-marker-hold-3129"

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            # Independent ticker: proves the loop keeps running while the
            # marker is parked in its worker thread (the #3060 invariant).
            ticks = {"n": 0}

            async def _ticker():
                while True:
                    ticks["n"] += 1
                    await asyncio.sleep(0.02)

            ticker = asyncio.create_task(_ticker())
            task = asyncio.create_task(ac.post("/v1/sessions", json=payload))
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert entered.is_set(), "the capture never reached the extraction"
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            release.set()  # the parked worker finishes (its result is discarded)
            for _ in range(200):
                if marker_entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert marker_entered.is_set(), (
                "the abandonment marker never ran — an abandoned capture leaves "
                "the session at capture_ok=NULL and the next same-session "
                "request replays it (#3129)")
            held = list(ha_mod._CAPTURE_SESSIONS)
            before = ticks["n"]
            await asyncio.sleep(0.3)  # the marker is still parked
            grew = ticks["n"] - before
            try:
                while_marker_pending = await asyncio.wait_for(
                    ac.post("/v1/sessions", json=payload), timeout=10.0)
            except TimeoutError:
                while_marker_pending = None
            marker_release.set()
            for _ in range(400):
                if not ha_mod._CAPTURE_SESSIONS:
                    break
                await asyncio.sleep(0.05)
            ticker.cancel()
            with suppress(asyncio.CancelledError):
                await ticker
            return (grew, held, while_marker_pending,
                    list(ha_mod._CAPTURE_SESSIONS))

    grew, held, pending, drained = asyncio.run(_run())

    assert marker_threads and "capture-marker" in marker_threads[0], (
        f"the abandonment marker ran on {marker_threads} — it must run on the "
        f"dedicated off-loop pool, not the event loop (#3129 / #3060)")
    assert grew >= 5, (
        f"the event loop managed only {grew} ticks in 0.3s while the "
        f"abandonment marker was parked — the graph write is running on the "
        f"loop (#3060's shape; the marker must be off-loop)")
    assert held == [key], (
        f"the session key was released while the abandonment marker was still "
        f"pending (registry={held!r}) — a retry admitted in that gap is served "
        f"the NULL→replay payload the marker prevents (#3129)")
    assert pending is not None and pending.status_code == 409, (
        f"a same-session request while the marker was pending returned "
        f"{getattr(pending, 'status_code', 'a timeout')} (#3129)")
    assert drained == [], drained
    assert ha_mod._CAPTURE_IN_FLIGHT == 0, ha_mod._CAPTURE_IN_FLIGHT


def test_cancelled_capture_keeps_the_key_and_leaves_a_retryable_attempt(
        client, monkeypatch):
    """#3129 (reviewer P1 + P2): an ABANDONED capture must not become a replay.

    Two contracts, both reachable only by cancelling a session-bearing capture:

    1. **Both sides** (P2) — the in-flight key is released only when the worker
       AND the request teardown are done. A mutant that popped the key from the
       request side alone left this whole file green (reviewer-measured 12/12),
       because no other test cancelled a session-bearing capture.
    2. **No replay of an unfinalized attempt** (P1) — the worker kept running
       after the cancellation, but the impl coroutine was gone, so `capture_ok`
       was never written and the session stayed NULL — which the replay rule
       reads as "legacy, presumed captured". The next same-session request was
       then served 200 + a success receipt with 0 turns extracted, permanently
       (reviewer-measured end-to-end via the retry's own response). The
       endpoint's cancellation handler + `_capture_abandoned_marker` make it a
       FAILED attempt instead, so that retry takes the #2335 TRUE-retry lane
       (the sibling test above pins the post-extraction window; this one pins
       the cancellation DURING the extraction).

       Deliberately NOT extended to failures: a raise-shaped capture keeps its
       documented NULL→legacy-replay shape
       (test_hosted_api.py::TestSessionActorStamp2600 raise-shape (ii)), so a
       re-POST never re-extracts a failed session under another actor's key.

       This asserts the STATE that decides the lane (`capture_ok=False` +
       `capture_extractor=v2`), not a second full capture: the retry lane's own
       behaviour is already pinned by the hosted twin
       (test_hosted_api.py::test_capture_true_retry_failed_session_reattempts),
       and driving a real re-extraction here would pull the embedding model in
       and test the extractor, not this guard.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    entered = threading.Event()
    release = threading.Event()

    def _stalled(_self, windowed, session_id, now, **kw):
        entered.set()
        release.wait(timeout=30)
        return [], {}

    monkeypatch.setattr(TortoiseSDK, "_extract_session_v2", _stalled)
    payload = {"conversation": _CONV, "session_id": "s-cancel-3129",
               "harness": _HARNESS}
    key = "team-001:default:s-cancel-3129"

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            task = asyncio.create_task(ac.post("/v1/sessions", json=payload))
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert entered.is_set(), "the capture never reached the extraction"
            task.cancel()  # client goes away mid-extraction
            with suppress(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.2)  # let the cancellation settle
            held_after_cancel = list(ha_mod._CAPTURE_SESSIONS)
            try:
                while_parked = await asyncio.wait_for(
                    ac.post("/v1/sessions", json=payload), timeout=10.0)
            except TimeoutError:
                while_parked = None
            release.set()  # the worker finishes now
            for _ in range(400):
                if not ha_mod._CAPTURE_SESSIONS:
                    break
                await asyncio.sleep(0.05)
            return held_after_cancel, while_parked, list(ha_mod._CAPTURE_SESSIONS)

    held, while_parked, drained = asyncio.run(_run())

    assert held == [key], (
        f"the in-flight session key was released by the REQUEST teardown while "
        f"the worker was still running (registry={held!r}, expected {[key]!r}) "
        f"— a same-session request is then admitted and replayed for a capture "
        f"that has not finished (#3129)")
    assert while_parked is not None and while_parked.status_code == 409, (
        f"a same-session request while the abandoned capture's worker was still "
        f"parked returned "
        f"{getattr(while_parked, 'status_code', 'a timeout')} — it must be "
        f"refused at admission, not queued (#3129)")
    assert drained == [], drained
    rows = ha_mod._make_sdk(namespace=TEST_ORG_ID)._get_proj().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.capture_ok, s.capture_extractor",
        params={"sid": "s-cancel-3129"}).result_set
    assert rows, "the capture never merged its Session row"
    capture_ok, lane = rows[0][0], rows[0][1]
    assert capture_ok is False, (
        f"an ABANDONED capture left the session at capture_ok={capture_ok!r} — "
        f"a NULL there is read by the replay rule as \"legacy, presumed "
        f"captured\", so the next same-session request gets 200 + a success "
        f"receipt with 0 turns extracted, permanently (#3129)")
    assert lane == "v2", (
        f"the abandoned attempt recorded lane {lane!r} — the #2335 TRUE-retry "
        f"gate requires a v2 prior, and stamping the wrong lane either strands "
        f"the session (m2) or re-runs the non-convergent lane #2473 exists to "
        f"prevent (#3129)")
    assert ha_mod._CAPTURE_IN_FLIGHT == 0, ha_mod._CAPTURE_IN_FLIGHT
    assert ha_mod._CAPTURE_SESSIONS == {}, ha_mod._CAPTURE_SESSIONS


def test_cancelled_m2_capture_records_the_m2_lane(client, monkeypatch):
    """#3129 (cycle-4 review): the marker records the ACTUAL lane, m2 included.

    The abandonment marker closes the retry hole on the v2 lane only: the
    #2335 TRUE-retry gate requires `prior_capture_extractor == "v2"` AND the
    retrying request to run v2 (#2473), so an abandoned m2 capture still
    replays on the next same-session POST — and so does an abandoned v2 capture
    re-POSTed after the deployment's lane was switched to m2. That is
    deliberate — re-running m2 over a failed attempt mints duplicate ULIDs, the
    hole #2473 closed. This pins the STATE the marker must leave
    (False + `m2`), i.e. that the fix does not falsely advertise v2 for an m2
    attempt (which would send the retry into the non-convergent re-run).
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    entered = threading.Event()
    release = threading.Event()

    def _stalled_m2(_self, windowed, session_id, now, **kw):
        entered.set()
        release.wait(timeout=30)
        return [], {}

    monkeypatch.setenv("TORTOISE_SESSION_EXTRACTOR", "m2")
    monkeypatch.setattr(TortoiseSDK, "_extract_session_llm", _stalled_m2)
    payload = {"conversation": _CONV, "session_id": "s-m2-cancel-3129",
               "harness": _HARNESS}
    key = "team-001:default:s-m2-cancel-3129"

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            task = asyncio.create_task(ac.post("/v1/sessions", json=payload))
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert entered.is_set(), "the m2 capture never reached the extraction"
            held = list(ha_mod._CAPTURE_SESSIONS)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            release.set()
            for _ in range(400):
                if not ha_mod._CAPTURE_SESSIONS:
                    break
                await asyncio.sleep(0.05)
            return held, list(ha_mod._CAPTURE_SESSIONS)

    held, drained = asyncio.run(_run())
    assert held == [key], held
    assert drained == [], drained

    rows = ha_mod._make_sdk(namespace=TEST_ORG_ID)._get_proj().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.capture_ok, s.capture_extractor",
        params={"sid": "s-m2-cancel-3129"}).result_set
    assert rows, "the m2 capture never merged its Session row"
    capture_ok, lane = rows[0][0], rows[0][1]
    assert capture_ok is False, (
        f"an abandoned m2 capture left capture_ok={capture_ok!r} — a NULL there "
        f"is the legacy-replay shape (#3129)")
    assert lane == "m2", (
        f"an abandoned m2 capture was stamped lane={lane!r} — stamping 'v2' "
        f"would route the retry into a NON-CONVERGENT re-extraction (duplicate "
        f"ULID claims, the hole #2473 closed) (#3129)")


def test_in_flight_session_keys_are_scoped_to_their_tenant():
    """#3129 (reviewer finding): the in-flight key is per-TENANT.

    Session ids are client-chosen and often generic, so a process-global bare
    ``session_id`` key would refuse an unrelated tenant's capture (measured
    409). Only the admission COUNTER is global — it bounds a server resource.
    """
    from fastapi import HTTPException

    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import _capture_session_key, _reserve_capture_slot

    team_a = {"org_id": "team-a", "graph_id": None}
    team_b = {"org_id": "team-b", "graph_id": None}
    key_a = _capture_session_key(team_a, "shared-id")
    key_b = _capture_session_key(team_b, "shared-id")
    key_a_g1 = _capture_session_key(
        {"org_id": "team-a", "graph_id": "g_1"}, "shared-id")
    assert _capture_session_key(team_a, None) is None
    assert len({key_a, key_b, key_a_g1}) == 3, (key_a, key_b, key_a_g1)

    baseline = ha_mod._CAPTURE_IN_FLIGHT
    slot_a = _reserve_capture_slot(key_a)
    try:
        slot_b = _reserve_capture_slot(key_b)  # another tenant: admitted
        slot_b.release()
        with pytest.raises(HTTPException) as excinfo:
            _reserve_capture_slot(key_a)  # same tenant: refused
        assert excinfo.value.status_code == 409, excinfo.value
    finally:
        slot_a.release()
    assert baseline == ha_mod._CAPTURE_IN_FLIGHT, ha_mod._CAPTURE_IN_FLIGHT
    assert ha_mod._CAPTURE_SESSIONS == {}, ha_mod._CAPTURE_SESSIONS


def test_cancelled_extraction_disabled_capture_records_the_disabled_lane(
        client, monkeypatch):
    """#4258 (+ #3129): the abandoned-capture marker is a THIRD writer of the
    Session `capture_extractor` lane. A capture abandoned while extraction is
    turned OFF must leave lane `"disabled"` — NEVER the keyless `"none"` — or
    the M2-replay disclosure later diagnoses a configured-key team as "stored
    WITHOUT a provider key".

    Mutation guard: setting either `state["lane"]` write to `"none"` (or to a
    literal other than the shared `_store_only_lane` derivation) REDs this test
    while leaving the completed-capture tests green — the marker is observable
    only on the cancellation path, which is why it is pinned here.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app
    from tortoise.sdk import _CAPTURE_EXTRACTOR_LANE_DISABLED

    ha_mod._update_onboarding_state(TEST_ORG_ID, capture_extract=False)

    entered = threading.Event()
    release = threading.Event()

    async def _stalled_audit(*a, **kw):
        # the disabled branch reaches the audit seam with `state["lane"]`
        # already set — parking HERE is the window the marker reads.
        entered.set()
        for _ in range(600):
            if release.is_set():
                break
            await asyncio.sleep(0.05)

    monkeypatch.setattr(ha_mod, "_async_audit", _stalled_audit)
    payload = {"conversation": _CONV, "session_id": "s-cancel-disabled",
               "harness": _HARNESS}

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            task = asyncio.create_task(ac.post("/v1/sessions", json=payload))
            for _ in range(200):
                if entered.is_set():
                    break
                await asyncio.sleep(0.05)
            assert entered.is_set(), "the capture never reached the audit seam"
            task.cancel()  # client goes away mid-capture
            with suppress(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.2)  # let the cancellation settle
            release.set()  # the parked audit finishes
            for _ in range(400):
                if not ha_mod._CAPTURE_SESSIONS:
                    break
                await asyncio.sleep(0.05)

    asyncio.run(_run())

    rows = ha_mod._make_sdk(namespace=TEST_ORG_ID)._get_proj().g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.capture_ok, s.capture_extractor",
        params={"sid": "s-cancel-disabled"}).result_set
    assert rows, "the capture never merged its Session row"
    capture_ok, lane = rows[0][0], rows[0][1]
    assert capture_ok is False, (
        f"an ABANDONED extraction-disabled capture left capture_ok="
        f"{capture_ok!r} (#3129)")
    assert lane == _CAPTURE_EXTRACTOR_LANE_DISABLED, (
        f"an abandoned extraction-disabled capture recorded lane {lane!r} — "
        f"the M2-replay disclosure would then diagnose a configured-key team "
        f"as keyless (#4258)")
    assert ha_mod._CAPTURE_IN_FLIGHT == 0, ha_mod._CAPTURE_IN_FLIGHT
    assert ha_mod._CAPTURE_SESSIONS == {}, ha_mod._CAPTURE_SESSIONS


# ── #3086: the capture WRITE path must not block the loop either ───────────
#
# The tests above pin the EXTRACTION off the loop and deliberately exclude the
# endpoint's own synchronous graph work (the module note at the top: "that is a
# SEPARATE, tracked defect — measured at ~4.75s for a max-size 500-turn
# capture (#3086)"). THESE tests pin that window.
#
# Before #3086 the per-turn store was a loop DUPLICATED between
# `tortoise/sdk.py` and `tortoise/hosted_api.py`, and each iteration issued TWO
# FalkorDB round-trips ON the event loop — a node `MERGE` then a `CONTAINS`
# edge `MERGE` for `{session_id}_t{i}` — i.e. ~1000 blocking calls for a
# 500-turn capture on a single-loop API. The fix is ONE shared writer
# (`_write_capture_turns`) that collapses the store to a single
# `UNWIND $turns` transaction, called from both lanes and run off the loop on
# the capture pool by the hosted lane.
#
# WHY THESE ASSERTIONS AND NOT A WALL-CLOCK GAP: a full-request loop-gap
# budget is not a valid discriminator on a shared/loaded runner. Measured on
# this box, the whole-request worst gap is 6-15s for a 2-TURN capture — the
# floor is dominated by per-request SDK/projection schema bootstraps (~15
# on-loop `_get_proj()` constructions, ~430 on-loop queries — the issue's own
# "~450 non-turn queries") plus runner scheduling, neither of which scales
# with the turn count and neither of which is this issue's seam. A gap budget
# therefore passes and fails the same way pre- and post-fix, which is exactly
# the "a green test that measures a different window is not evidence" trap.
# These tests instead measure the SAME whole-request window and assert the
# property that actually regressed: the work the capture puts ON the event
# loop must not scale with the number of turns.
CAPTURE_TURNS_LARGE = 500
CAPTURE_TURNS_SMALL = 50
# The on-loop query count is dominated by the fixed SDK/projection bootstraps
# (~430), identical for both sizes. The per-turn loop added ~2 queries per
# turn, so the pre-fix delta between these two sizes was ~900; a batched store
# adds a constant. 60 is generous for a constant and an order of magnitude
# below the per-row shape it must catch.
ON_LOOP_QUERY_DELTA_BUDGET = 60
# Same reasoning as a TIME bound: 900 on-loop round-trips at the ~2.6ms/query
# the issue measured is ~2.3s of hard blocking, versus a constant that is
# ~0 — but the COUNT is what this test asserts (see the note at the assertion
# on why an on-loop TIME budget is not a valid discriminator on this lane).


def _capture_conv(turns: int) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"turn {i} " + ("the database schema needs work " * 3)}
        for i in range(turns)
    ]


def _measure_on_loop_graph_work(monkeypatch, turns: int, sid: str):
    """Run one real hosted capture, returning the GRAPH work the loop did.

    The graph class's ``query`` is wrapped for the duration, so every
    round-trip is attributed to the thread that made it. Only ``MainThread``
    (the event loop — ``TestClient``'s lifespan portal and the monitoring
    threads have their own names) is counted: that is precisely the work a
    stalled loop cannot interleave.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    monkeypatch.setattr(
        TortoiseSDK, "_extract_session_v2",
        lambda _self, windowed, session_id, now, **kw: ([], {}))

    graph_cls = type(ha_mod._make_sdk(namespace="registry")._get_proj().g)
    orig_query = graph_cls.query
    records: list[tuple[str, float]] = []
    cyphers: list[str] = []
    writer_threads: list[str] = []
    orig_writer = ha_mod._write_capture_turns

    def _timed_query(self, cypher, *args, **kwargs):
        started = time.perf_counter()
        try:
            return orig_query(self, cypher, *args, **kwargs)
        finally:
            records.append((threading.current_thread().name,
                            time.perf_counter() - started))
            cyphers.append(" ".join(cypher.split()))

    def _wrapped_writer(*args, **kwargs):
        writer_threads.append(threading.current_thread().name)
        return orig_writer(*args, **kwargs)

    monkeypatch.setattr(graph_cls, "query", _timed_query)
    monkeypatch.setattr(ha_mod, "_write_capture_turns", _wrapped_writer)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            return await ac.post("/v1/sessions",
                                 json={"conversation": _capture_conv(turns),
                                       "session_id": sid})

    resp = asyncio.run(_run())
    monkeypatch.setattr(graph_cls, "query", orig_query)
    monkeypatch.setattr(ha_mod, "_write_capture_turns", orig_writer)
    on_loop = [d for name, d in records if name == "MainThread"]
    return resp, len(on_loop), sum(on_loop), writer_threads, cyphers


def test_capture_write_does_not_scale_loop_blocking_with_turns(
        client, monkeypatch):
    """#3086: the capture's ON-LOOP graph work is constant in turn count.

    Same whole-request window (setup included) for both sizes, so the fixed
    per-request cost — which is NOT this issue's seam — cancels in the delta
    and the remaining signal is exactly the per-turn store.

    Mutation check (must stay true): restoring the per-turn loop (node MERGE +
    CONTAINS MERGE per turn) ON the loop puts ~900 extra on-loop queries on the
    500-turn capture, failing the count delta; and
    keeping a per-row walk but merely moving it into an off-loop writer still
    fails the single-`UNWIND` assertion below (the batching win is lost even
    though the loop stops blocking). No WALL-CLOCK gap budget is asserted: on
    a shared/loaded runner the whole-request gap is dominated by the fixed
    per-request SDK/bootstrap cost (measured 6-15s even for a 2-TURN capture)
    and by runner scheduling, so a gap budget passes and fails identically
    pre- and post-fix — the "measures a different window" trap.
    """
    small_resp, small_q, _, _, _ = _measure_on_loop_graph_work(
        monkeypatch, CAPTURE_TURNS_SMALL, "loop-scale-small-3086")
    assert small_resp.status_code == 200, small_resp.text[:300]
    large_resp, large_q, _, writer_threads, cyphers = \
        _measure_on_loop_graph_work(
            monkeypatch, CAPTURE_TURNS_LARGE, "loop-scale-large-3086")
    assert large_resp.status_code == 200, large_resp.text[:300]

    assert writer_threads, (
        "the hosted capture never called the shared turn writer — the turn "
        "store is forked again or the write did not happen (#3086)")
    assert all(name.startswith("capture-extract") for name in writer_threads), (
        f"the turn writer ran on {writer_threads!r}, not the dedicated capture "
        f"pool — the per-turn graph work is back on the event loop (#3086)")

    # The store is ONE batched transaction whatever thread runs it: exactly one
    # `UNWIND $turns` statement and none of the old per-row node writes. This
    # is what a per-row walk moved off the loop would break (the batching win
    # would be silently lost).
    batched = [c for c in cyphers if "UNWIND $turns AS turn" in c]
    per_row = [c for c in cyphers if "MERGE (t:Point {id:$id})" in c]
    assert len(batched) == 1, (
        f"a {CAPTURE_TURNS_LARGE}-turn capture issued {len(batched)} "
        f"`UNWIND $turns` statement(s) — the turn store is no longer a single "
        f"batched transaction (#3086)")
    assert not per_row, (
        f"the per-row turn write is back ({len(per_row)} statement(s)) — "
        f"batching was reverted to one graph round-trip per turn (#3086)")

    q_delta = large_q - small_q
    assert q_delta < ON_LOOP_QUERY_DELTA_BUDGET, (
        f"a {CAPTURE_TURNS_LARGE}-turn capture put {large_q} graph queries on "
        f"the event loop vs {small_q} for {CAPTURE_TURNS_SMALL} turns "
        f"(delta {q_delta}, budget {ON_LOOP_QUERY_DELTA_BUDGET}) — the turn "
        f"store is per-row again: ~1000 blocking round-trips for 500 turns "
        f"freeze the single event loop and take /health down with it (#3086)")

    # The on-loop TIME delta is deliberately NOT asserted (it is measured and
    # reported in the message above for diagnostics). On this lane the on-loop
    # time is dominated by the fixed per-request SDK/bootstrap cost, which
    # varies by seconds run to run — measured +1.24s between a 50- and a
    # 500-turn capture with ZERO extra queries. A time threshold that reds on a
    # correctly-fixed tree is a bad gate; the COUNT delta is deterministic
    # (pre-fix +876, post-fix constant) and is what actually scales with turns.


def test_capture_turn_store_is_one_batched_implementation(client, monkeypatch):
    """#3086: the SDK and hosted lanes share ONE turn writer, and it is
    idempotent.

    * IDENTITY — the hosted module must call the SDK's writer (the same
      function object), not a private copy. A future re-fork fails here.
    * IDEMPOTENCY — a re-capture of the same session_id must leave exactly one
      turn Point per ``{session_id}_t{i}``. A single ``UNWIND $turns``
      statement means a partial failure can only be a partial BATCH, so
      per-row idempotency on the deterministic ids is what makes a retry
      converge instead of duplicating (#3086).
    """
    import tortoise.hosted_api as ha_mod
    from tortoise import sdk as sdk_mod
    from tortoise.hosted_api import app

    writer = getattr(sdk_mod, "_write_capture_turns", None)
    assert writer is not None, (
        "the shared batched turn writer (_write_capture_turns) is missing "
        "from tortoise/sdk.py (#3086)")
    assert getattr(ha_mod, "_write_capture_turns", None) is writer, (
        "the hosted capture lane does not call the SDK's turn writer — the "
        "per-turn store is forked again (the drift class #3086 deletes)")

    monkeypatch.setattr(
        TortoiseSDK, "_extract_session_v2",
        lambda _self, windowed, session_id, now, **kw: ([], {}))

    sid = "batched-3086"

    async def _post():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            return await ac.post(
                "/v1/sessions",
                json={"conversation": _capture_conv(CAPTURE_TURNS_SMALL),
                      "session_id": sid})

    first = asyncio.run(_post())
    assert first.status_code == 200, first.text[:300]
    second = asyncio.run(_post())
    assert second.status_code == 200, second.text[:300]

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    rows = sdk._get_proj().g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(t:Point) "
        "WHERE t.is_episodic = true RETURN count(t)",
        params={"sid": sid}).result_set
    turn_count = rows[0][0] if rows else 0
    assert turn_count == CAPTURE_TURNS_SMALL, (
        f"a re-capture of {CAPTURE_TURNS_SMALL} turns left {turn_count} turn "
        f"Points — the batched writer's per-row MERGE must be idempotent on "
        f"the deterministic {{sid}}_t{{i}} ids (#3086)")

    # The node shape the shared writer produces (the loop it replaced wrote
    # exactly these) — a re-capture that changed any of them would silently
    # break the read path.
    props = sdk._get_proj().g.query(
        "MATCH (t:Point {id:$tid}) RETURN t.content, t.pointKind, t.speaker, "
        "t.is_episodic, t.status, t.is_operator, t.content_hash IS NOT NULL",
        params={"tid": f"{sid}_t0"}).result_set
    assert props, "the shared writer created no turn node"
    content, point_kind, speaker, is_episodic, status, is_operator, has_hash = \
        props[0]
    assert point_kind == "event", point_kind
    assert speaker == "user", speaker
    assert is_episodic is True, is_episodic
    assert status == "draft", status
    assert is_operator is False, is_operator
    assert has_hash is True, "the turn carries no content_hash"
    assert content.startswith("[user] turn 0 "), content
