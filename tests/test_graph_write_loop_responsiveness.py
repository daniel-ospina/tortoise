"""#3718 — the REST graph-write handlers must not run sync FalkorDB I/O on the loop.

The defect these tests pin: ``/v1/points``, ``/v1/objects``, ``/v1/subjects``,
``/v1/events`` and ``/v1/dream`` are ``async def`` handlers that called the SDK
directly in the coroutine body. The SDK is **synchronous** — ``TortoiseSDK`` →
``FalkorProjection`` → ``falkordb`` 1.6.2 (a blocking socket client) — so one
slow graph round trip froze the SINGLE event loop, and with it every
concurrent request on the process (one uvicorn worker on Fly), the capture
path and the request that triggered the write. ``_dream_worker`` (the queue
drain that ``create_point`` enqueues into) had the same inline shape, so a
user write could enqueue further on-loop work.

Measured on the recorded reproduction: 5 inline writes produced **one** loop
tick (max gap 231.4 ms); the identical calls off-loaded produced 120 ticks
(p50 1.42 ms). Per-write p50 ≈ 35 ms embedded, so a 20-write burst ≈ 0.67 s of
frozen loop and a 100-write burst ≈ 3.4 s.

These tests assert the INVARIANT, not the mechanism: while a graph write is
blocked, the loop keeps ticking AND a concurrent trivial request is answered
INSIDE the write's blocking window. A future refactor that puts the call back
on the loop fails here regardless of how it is written — the one deliberate
exception is the ``ran_on_loop`` assertion, which discriminates "off the loop
entirely" from "off the loop via the executor" (both are accepted; what is
rejected is running it on the loop at all).

Mechanism under test (post-review, #3718): the SHORT graph calls (`~35ms`
class — `create_point`, `create_object`, `create_subject`, `events_poll`) go
through `asyncio.to_thread`, the file's house style — the same pattern
`/v1/search` uses for the same class of short sync graph work. The LONG call
(`sdk.dream`, seconds) and the `_dream_worker` drain go through the dedicated
`_DREAM_EXECUTOR` via `_run_dream_on_pool`: the module's own #3060 criterion
reserves a dedicated pool for long/stallable work, because the shared default
executor also carries the auth middleware's per-request abuse hooks. WHICH
executor the short calls land on is not asserted (that would freeze the
trade-off into the suite); what IS asserted is that no call runs on the loop,
and — for the dream sites only — that the pass is not on the SHARED default
pool (the `asyncio_*` worker threads the abuse hooks ride), which is a real
regression the first revision of this change introduced.

FLAKE NOTE — the embedded-redislite test fixture is NOT hermetic under this
harness: a redislite server's tempdir can be removed while a live client still
holds it (its `FalkorDB.close()` is a server TEARDOWN, not a pool disconnect),
so a run can die with `redis.ConnectionError: .../redis.socket: No such file`
from a request or even from a fixture setup. That is pre-existing and
tracked (#3653, #3685): the untouched sibling
`tests/test_capture_loop_responsiveness.py` reproduces it at the same rate
(2/8 and 1/5 consecutive runs) with NO dream passes in the file, so it is
harness noise, not a property of the off-load these tests pin. Re-run before
treating such a failure as a regression.

SCOPE — #3773 closed the residual this note used to record: the #3718
handlers' PRE-WRITE graph helpers (`_data_sdk`'s SDK open/connect — including
the graph-bound ownership probe and the embedded keepalive anchor's probe
query — and `_check_org_limit`'s per-org count query) now go through the #3498
bounded offload seam (`_graph_offload` → `_cp_offload` → the dedicated `graph`
pool), and `_dream_worker` builds its SDK inside the off-loaded dream-pool item
(`_run_dream_on_pool`'s factory — off the loop, but deliberately NOT the
fail-closed request-path seam: a background drain has no client to fail closed
to, and an offload failure there would drop its already-drained roots).
`test_write_preamble_graph_helpers_run_off_the_loop` and the `_make_sdk`
assertion in the worker test pin that at the thread level. Still OUT of scope
here (separate, tracked residuals — all filed as #4451): the post-write
`_record_write_op(org, ...)` metering MERGE still runs on the loop in
create_object/create_subject/create_point (and elsewhere); the sibling READ
handlers (`GET /v1/points`, `GET /v1/points/{id}`, `/v1/dream/health`, the
session and registry reads) were off-loaded in the #3718 residual-2 change —
their behavioural and AST-inventory guards live in
`tests/test_read_routes_loop_responsiveness.py` — while ~30 other `async def`
routes in this file still run sync FalkorDB I/O inline (the named residual in
that file's `_KNOWN_INLINE_ROUTE_RESIDUAL`); and the capture path's
`_apply_capture_ingest_ep` still runs a `sdk.dream(mode="local")` pass on the
loop (tracked by #3086) — which is also why the production `_dream_lock`
serializes only the two POOLED pass sites. So a tick count here certifies the
OFF-LOADED CALL, not the whole request.
"""
from __future__ import annotations

import asyncio
import threading
import time
from contextlib import suppress

import httpx
import pytest

# The authenticated TestClient fixture (temp-DB SDK patching + the module-level
# env the app needs on import). Imported for the fixture, not for the tests.
from tests.test_hosted_api import TEST_ORG_ID
from tests.test_hosted_api import client as client
from tortoise.sdk import TortoiseSDK

# A stall long enough to be unambiguous, short enough to keep CI quick.
STALL_S = 2.0
# The loop's own tick period while the write is blocked.
TICK_S = 0.02
# PRIMARY signal: ticks completed INSIDE the blocking window. A blocked loop
# yields 0 (the next tick can only run once the freeze releases); a free loop
# yields ~STALL_S/TICK_S = 100. The bound is loose on purpose — the red CI a
# >2s scheduler/GC gap would cause costs far more than this redundancy saves.
MIN_TICKS_IN_STALL = 10

# label, SDK method patched to block, the request to that endpoint.
# One case per changed HANDLER in #3718. Coverage of the SUB-calls is partial and
# is called out rather than implied: the worker's `_mark_dirty` IS covered (by
# `test_enqueued_dream_worker_does_not_freeze_the_event_loop`), the `about_edge`
# write is covered only FUNCTIONALLY (it rides the same worker hand-off as the
# `points` case's `create_point`, driven by the `about_object` field, and its edge
# is asserted by
# `tests/test_hosted_api.py::TestTeamInfo::test_point_with_about_object_wires_edge`),
# and the `/v1/dream` default branch's own queued-roots `_mark_dirty` is covered
# (without a stall — the observation is thread affinity, not tick count) by
# `test_dream_default_branch_marks_queued_roots_off_loop`, which pre-seeds a
# `_DREAM_QUEUES` root so the branch executes at all. `url` (not `path`) is the
# httpx request kwarg.
_ENDPOINTS = [
    ("points", "create_point",
     {"method": "POST", "url": "/v1/points",
      "json": {"content": "the release needs a normalization pass",
               # #1643: drives the create_about_edge sub-call in the same
               # worker hand-off, so the branch executes at all.
               "about_object": "3718-about-object"}}),
    ("objects", "create_object",
     {"method": "POST", "url": "/v1/objects",
      "json": {"name": "3718-object"}}),
    ("subjects", "create_subject",
     {"method": "POST", "url": "/v1/subjects",
      "json": {"name": "3718-subject"}}),
    ("events", "events_poll",
     {"method": "GET", "url": "/v1/events"}),
    ("dream", "dream",
     {"method": "POST", "url": "/v1/dream"}),
    # The other two /v1/dream branches (I1 precedence: an explicit mode wins
    # over full). A refactor that put only one branch back on the loop would
    # otherwise pass.
    ("dream-mode", "dream",
     {"method": "POST", "url": "/v1/dream", "params": {"mode": "local"}}),
    ("dream-full", "dream",
     {"method": "POST", "url": "/v1/dream", "params": {"full": "true"}}),
]

# The shared default executor `asyncio.to_thread` submits to names its worker
# threads `asyncio_<n>`; a dedicated pool names them by its own prefix. The
# dream sites must NOT be on the shared pool (the #3060 starvation path), so
# the isolation is asserted as a THREAD-NAME property rather than by pinning
# this module's own prefix, which would freeze the tuning.
_SHARED_POOL_THREAD_PREFIX = "asyncio_"


def _install_blocking_graph_call(monkeypatch, method_name: str, state: dict,
                                 *, delegate: bool = True):
    """Make `TortoiseSDK.<method_name>` block, recording its loop/window.

    Records three things the assertions need:

    * ``ran_on_loop`` — ``asyncio.get_running_loop()`` succeeds only when the
      call executes ON the loop thread; a worker thread has no running loop.
      This is the discriminator between "off the loop" and "on the loop".
    * ``entered`` / ``exited`` — the exact window the call was blocked, so the
      tick count and the concurrent request are measured against it rather
      than against wall-clock guesses. ``exited`` is recorded in a ``finally``
      so it is set even if the caller is cancelled mid-stall.
    * ``thread`` — the executing thread's name, so the dream tests can assert
      the pass is NOT on the shared default executor (`asyncio_*`). For a site
      whose RESULT is not asserted (the dream worker) ``delegate=False`` returns
      a stub instead of running a real pass against a temp DB the fixture is
      about to tear down.
    """
    real = getattr(TortoiseSDK, method_name)

    def _blocking(self, *args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        # AGGREGATED, not last-call-wins (mirror of `_record_loop_thread_of`): the
        # pinned method is one call per request today, but a retry added later
        # would otherwise let a second, off-loop call erase an on-loop first one
        # and hide exactly the regression this test exists to catch.
        state["ran_on_loop"] = state.get("ran_on_loop", False) or on_loop
        state["thread"] = threading.current_thread().name
        state["entered"] = time.perf_counter()
        try:
            time.sleep(STALL_S)  # stand-in for a slow FalkorDB round trip
        finally:
            state["exited"] = time.perf_counter()
        if not delegate:
            return {"ok": True, "stubbed": True}
        return real(self, *args, **kwargs)

    monkeypatch.setattr(TortoiseSDK, method_name, _blocking)


def _record_loop_thread_of(monkeypatch, method_name: str, state: dict,
                           key: str) -> None:
    """Record whether ANY call to `TortoiseSDK.<method_name>` ran ON the loop.

    The non-blocking twin of the helper above, for a synchronous graph call
    that is expected to be off-loaded but whose own blocking cost is not what
    the test is measuring (the worker's ``_mark_dirty``). Delegates to the real
    method so behaviour is unchanged.

    AGGREGATES rather than last-call-wins: ``_mark_dirty`` is called more than
    once on this path (inside the off-loaded ``create_point`` and again by the
    worker), so a later off-loop call must not erase an earlier on-loop one.
    """
    real = getattr(TortoiseSDK, method_name)

    def _observe(self, *args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        state[key] = state.get(key, False) or on_loop
        return real(self, *args, **kwargs)

    monkeypatch.setattr(TortoiseSDK, method_name, _observe)


def _record_site(monkeypatch, name: str, records: list) -> None:
    """Record whether ANY call to ``ha.<name>`` ran ON the event loop (#3773).

    ``asyncio.get_running_loop()`` succeeds only on the loop thread; a worker
    thread has no running loop. That is the direct, BEHAVIOURAL discriminator
    for the handlers' per-request graph helpers (``_data_sdk`` /
    ``_check_org_limit`` / the dream worker's ``_make_sdk``) — a source-grep for
    ``_graph_offload`` would pass even if the call moved back onto the loop.
    Records every call ``(on_loop, thread_name)``: the aggregate is what makes
    the assertion strict (a later off-loop call must not erase an earlier
    on-loop one), and the thread name names the offender on failure.
    """
    import tortoise.hosted_api as ha_mod
    real = getattr(ha_mod, name)

    def _observe(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        records.append((on_loop, threading.current_thread().name))
        return real(*args, **kwargs)

    monkeypatch.setattr(ha_mod, name, _observe)


def test_offloaded_data_sdk_binds_the_actor_for_the_write(monkeypatch):
    """#3773/#2600: the offload must not lose the server-resolved actor.

    ``_data_sdk_offloaded`` binds the actor LOOP-side and runs ``_data_sdk``
    under a CONTEXT COPY (``_graph_offload``); the write's own
    ``asyncio.to_thread`` then copies the LOOP's context. So the actor must be
    visible on the loop AND inside the worker the write runs in — exactly the
    property a naive offload (which set the ContextVar only on a
    process-lifetime pool thread) would silently break, and would leak into
    the next request that worker served.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.sdk import _current_actor_user_id

    actor = "11111111-1111-4111-8111-111111111111"
    monkeypatch.setattr(ha_mod, "_data_sdk",
                        lambda org: {"opened": org["org_id"]})

    async def _run():
        _current_actor_user_id.set(None)  # clean slate for THIS loop's context
        sdk = await ha_mod._data_sdk_offloaded(
            {"org_id": "team-actor", "actor_user_id": actor})
        return (sdk, _current_actor_user_id.get(),
                await asyncio.to_thread(_current_actor_user_id.get))

    sdk, on_loop, in_worker = asyncio.run(_run())
    assert sdk == {"opened": "team-actor"}
    assert on_loop == actor, (
        "the #2600 actor was not bound on the LOOP — the off-loaded write's "
        "to_thread context copy would carry no actor")
    assert in_worker == actor, (
        "the actor did not reach the worker thread the write runs in")


def test_dream_pool_submit_failure_does_not_close_under_the_item(monkeypatch):
    """#3773 (code-review rounds 2-3): a submit failure MUST NOT close the SDK.

    ``ThreadPoolExecutor.submit`` enqueues the work item BEFORE
    ``_adjust_thread_count()`` can raise "can't start new thread", so a submit
    ``RuntimeError`` does NOT prove the item never ran. Closing the REST
    dream path's pre-built SDK from the submit-failure branch could therefore
    tear it down under a pass an existing worker had already picked up (the
    CPython #87185 class the design removes). The close is owned strictly by
    the work item; a pre-enqueue failure strands the SDK to GC — bounded and
    transient, the same lifecycle the sibling write handlers' SDKs have.
    """
    import tortoise.hosted_api as ha_mod

    class _Sdk:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    sdk = _Sdk()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(ha_mod, "_submit_off_loop", _boom)

    async def _run():
        await ha_mod._run_dream_on_pool(lambda _s: None, lambda: sdk)

    with pytest.raises(RuntimeError):
        asyncio.run(_run())
    assert sdk.closed == 0, (
        "a submit failure closed the SDK — submit can raise AFTER the item was "
        "enqueued, so this can tear it down under a live pass")


async def _quiesce_dream_drain() -> None:
    """Cancel and drop this org's dream drain INSIDE the loop that owns it.

    ``create_point`` enqueues a ``_dream_worker`` task via ``_enqueue_dream``,
    and the module-level ``_DREAM_QUEUES`` / ``_DREAM_TASKS`` dicts outlive
    each test's ``asyncio.run`` loop. Left alone, that task is destroyed
    mid-flight when the loop closes ("Task was destroyed but it is pending"),
    and the dicts keep pointing at a task from a dead loop — which then makes a
    LATER test's ``_enqueue_dream`` see a not-``done()`` task and skip creating
    a drain at all. Reviewer-measured as a 1-in-13 flake (a stale
    embedded-daemon ``redis.ConnectionError`` from a previous test's removed
    tempdir). Cancelling and awaiting it here removes the cross-test coupling.

    Harmless when nothing was enqueued: the pops are unconditional and the
    no-op case has no task to cancel.
    """
    import tortoise.hosted_api as ha_mod

    key = ha_mod._dream_key(TEST_ORG_ID, None)
    # Empty the queue BEFORE cancelling: `_dream_worker`'s own `finally`
    # reschedules when its (local) queue is non-empty, so popping the dict
    # entry alone would leave a fresh task behind on a loop that is about to
    # close — the leak this helper exists to prevent.
    q = ha_mod._DREAM_QUEUES.pop(key, None)
    if q is not None:
        while not q.empty():
            q.get_nowait()
    task = ha_mod._DREAM_TASKS.pop(key, None)
    if task is not None and not task.done():
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def _drive(request_kwargs: dict, state: dict):
    """Fire the write concurrently with a ticker and a /health probe.

    Ordering is what makes the measurement sound: the ticker is started first
    and given one period so it is mid-sleep when the write blocks; the write
    is then launched and awaited only far enough to observe it enter its
    blocking call; /health is requested while the write is parked.
    """
    from tortoise.hosted_api import app

    ticks: list[float] = []
    stop = {"done": False}

    async def _ticker():
        while not stop["done"]:
            ticks.append(time.perf_counter())
            await asyncio.sleep(TICK_S)

    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            tick = asyncio.create_task(_ticker())
            await asyncio.sleep(TICK_S * 2)  # ticker mid-sleep before the write
            write = asyncio.create_task(ac.request(**request_kwargs))
            for _ in range(2000):  # let the write reach the blocking call
                if "entered" in state:
                    break
                await asyncio.sleep(TICK_S / 2)
            assert "entered" in state, (
                "the graph call was never reached — the request failed before "
                "the SDK call, so this run proves nothing")
            # Sampled BEFORE the probe is issued: the property the assertions
            # below rest on is that /health was answered while the write was
            # STILL in flight, not after it had already returned.
            write_in_flight = not write.done()
            health = await ac.get("/health")
            health_done = time.perf_counter()
            response = await write
            stop["done"] = True
            await tick
        return ticks, health, health_done, response, write_in_flight
    finally:
        # The write enqueues a dream drain bound to THIS loop; cancel it here,
        # where it can still be awaited (see `_quiesce_dream_drain`).
        await _quiesce_dream_drain()


@pytest.mark.parametrize("label,method_name,request_kwargs", _ENDPOINTS,
                         ids=[e[0] for e in _ENDPOINTS])
def test_graph_write_does_not_freeze_the_event_loop(
        client, monkeypatch, label, method_name, request_kwargs):
    """#3718: a blocked graph write must not stall the loop or other requests.

    Three independent assertions, each of which fails on the inline shape:

    * ``ran_on_loop is False`` — the call did not execute on the loop thread.
      This is the direct mechanism assertion, and it is behavioral (the call's
      own view of the loop), not a source-grep for ``to_thread``.
    * ``ticks_in_stall >= MIN_TICKS_IN_STALL`` — the loop kept scheduling while
      the write was blocked. Inline yields 0: the ticker's next wake-up cannot
      run until the freeze releases.
    * ``/health`` answered INSIDE the blocking window (``entered < health_done
      < exited``) — the concrete user-visible consequence: a concurrent client
      is served *during* the write, not queued behind it. An inline write
      answers /health only after ``exited``, so both halves of the bound fail.

    /health is the right concurrent probe: it is pure in-memory (#2850) and
    takes no thread hand-off, so a delay in it can only come from the loop
    being blocked, never from a saturated executor.
    """
    state: dict = {}
    _install_blocking_graph_call(monkeypatch, method_name, state)
    # The write enqueues a dream drain (`_enqueue_dream` on create_point). Park
    # it far inside its debounce so the drain is ALWAYS cancelled before it can
    # submit a real pass against the temp DB the fixture is about to remove
    # (quiesce cancels the await; it cannot stop an already-submitted pool
    # thread). The worker test sets its own small debounce.
    import tortoise.hosted_api as ha_mod

    monkeypatch.setattr(ha_mod, "_DREAM_DEBOUNCE_S", 3600.0)

    ticks, health, health_done, response, write_in_flight = asyncio.run(
        _drive(request_kwargs, state))

    assert state.get("ran_on_loop") is False, (
        f"[{label}] {method_name} ran ON the event loop — its synchronous "
        f"FalkorDB socket I/O freezes every concurrent request for the round "
        f"trip (the /v1/search precedent off-loads it; #3718)")

    if label.startswith("dream"):
        # The long pass must not ride the SHARED default executor, whose
        # workers also carry the auth middleware's per-request abuse hooks
        # (`_abuse_post_auth`) — the #3060 starvation path the first review
        # cycle caught in this change.
        assert not str(state.get("thread")).startswith(
            _SHARED_POOL_THREAD_PREFIX), (
            f"[{label}] sdk.dream ran on {state.get('thread')!r} — the shared "
            f"default executor, whose workers the auth path's abuse hooks also "
            f"need: a burst of dream passes would park every worker and stall "
            f"unrelated tenants before their handlers, with /health still green "
            f"(#3060 / #3718)")

    entered, exited = state["entered"], state["exited"]

    in_stall = [t for t in ticks if entered < t < exited]
    assert len(in_stall) >= MIN_TICKS_IN_STALL, (
        f"[{label}] the event loop completed only {len(in_stall)} tick(s) "
        f"during a {STALL_S:.1f}s blocked {method_name} (need "
        f"{MIN_TICKS_IN_STALL}) — the write is blocking the event loop, so "
        f"every concurrent request waits for the graph round trip (#3718)")

    # NOTE (#3718 review P1): the "worst tick interval inside the window"
    # check that used to live here was DELETED. It was skipped entirely on the
    # full-inline RED shape (a blocked loop produces no two ticks inside the
    # window, so the guard never ran) while firing on unrelated scheduler/GC
    # load — reviewer-reproduced at worst=1.19s against a 1.0s bound on a run
    # where `ran_on_loop is False` and the tick count both passed. The tick
    # COUNT above and the /health-in-window assertion below are the signals
    # with teeth; this one only added flakiness.
    # The body — not the status — is the assertable part: `/health` catches each
    # internal step and returns `{"status": "ok"|"degraded", ...}`, so it never
    # 5xxes (hosted_api.py `health`). Asserting 200 alone could not fail.
    assert health.json()["status"] in {"ok", "degraded"}, health.text
    assert health_done < exited, (
        f"[{label}] /health was NOT served while {method_name} was blocked "
        f"(answered {(health_done - exited) * 1000:.0f}ms AFTER the block "
        f"released) — a concurrent request queued behind the graph write "
        f"instead of being served, which is the process-wide stall this "
        f"off-load exists to prevent (#3718)")
    assert write_in_flight, (
        f"[{label}] the write had already returned before /health was issued, "
        f"so this run proves nothing about a request served DURING the "
        f"blocking window (#3718)")

    assert response.status_code == 200, (
        f"[{label}] the off-loaded call did not return a real response "
        f"({response.status_code}: {response.text[:200]})")


# #3773: the handlers #3718 off-loaded still ran their per-request graph
# helpers INLINE on the loop before the off-loaded call. `label` selects the
# request; the first three also run the per-org quota count.
_PREAMBLE_ENDPOINTS = [
    ("points", {"method": "POST", "url": "/v1/points",
                "json": {"content": "the release needs a normalization pass"}}),
    ("objects", {"method": "POST", "url": "/v1/objects",
                 "json": {"name": "3773-object"}}),
    ("subjects", {"method": "POST", "url": "/v1/subjects",
                  "json": {"name": "3773-subject"}}),
    ("events", {"method": "GET", "url": "/v1/events"}),
    ("dream", {"method": "POST", "url": "/v1/dream"}),
]

#: The handlers whose preamble ALSO runs the per-org quota count.
_PREAMBLE_QUOTA_LABELS = frozenset({"points", "objects", "subjects"})


def _run_request(request_kwargs: dict):
    """Fire ONE request on a fresh loop, quiescing the enqueued dream drain.

    Mirrors ``_drive``'s loop ownership (the drain task is bound to THIS loop),
    without the tick/health instrumentation — this test measures thread
    affinity, not loop responsiveness.
    """
    from tortoise.hosted_api import app

    async def _run():
        transport = httpx.ASGITransport(app=app)
        try:
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as ac:
                return await ac.request(**request_kwargs)
        finally:
            await _quiesce_dream_drain()

    return asyncio.run(_run())


@pytest.mark.parametrize("label,request_kwargs", _PREAMBLE_ENDPOINTS,
                         ids=[e[0] for e in _PREAMBLE_ENDPOINTS])
def test_write_preamble_graph_helpers_run_off_the_loop(
        client, monkeypatch, label, request_kwargs):
    """#3773: the handlers' per-request graph helpers must leave the loop.

    #3718 off-loaded the pinned SDK call, but every handler still ran its
    per-request preamble INLINE: ``_data_sdk`` (the SDK open — and, for a
    graph-bound key, its ownership probe; in embedded mode the keepalive
    anchor's probe query) and, on the write handlers, ``_check_org_limit`` (a
    per-org count query). A blocked loop therefore still stalled a concurrent
    request for their duration — the same defect #3718 fixed, just smaller.

    This is the falsifier. ``_record_site`` wraps the module helper and asks
    ``asyncio.get_running_loop()``: a loop-thread call succeeds (``on_loop``
    True), a pool-worker call raises ``RuntimeError`` (``on_loop`` False).
    Remove the offload and every recorded call flips to True.
    """
    import tortoise.hosted_api as ha_mod

    # The write enqueues a dream drain bound to this loop; park it so
    # `_quiesce_dream_drain` cancels it before it can touch the temp DB.
    monkeypatch.setattr(ha_mod, "_DREAM_DEBOUNCE_S", 3600.0)
    data_records: list = []
    limit_records: list = []
    _record_site(monkeypatch, "_data_sdk", data_records)
    _record_site(monkeypatch, "_check_org_limit", limit_records)

    response = _run_request(request_kwargs)
    assert response.status_code == 200, (
        f"[{label}] the request failed before the assertions could run "
        f"({response.status_code}: {response.text[:200]})")

    assert data_records, (
        f"[{label}] _data_sdk was never reached — the request failed before "
        f"the handler's SDK open, so this run proves nothing")
    assert not any(on_loop for on_loop, _t in data_records), (
        f"[{label}] _data_sdk ran ON the event loop ({data_records}) — its "
        f"synchronous FalkorDB connect / ownership probe freezes every "
        f"concurrent request for the round trip (#3773)")

    if label in _PREAMBLE_QUOTA_LABELS:
        assert limit_records, (
            f"[{label}] _check_org_limit was never reached — the run proves "
            f"nothing about the quota count")
        assert not any(on_loop for on_loop, _t in limit_records), (
            f"[{label}] _check_org_limit ran ON the event loop "
            f"({limit_records}) — the per-org count query freezes every "
            f"concurrent request (#3773)")


def test_enqueued_dream_worker_does_not_freeze_the_event_loop(
        client, monkeypatch):
    """#3718: the write-enqueued dream drain must not run inline either.

    ``create_point`` calls ``_enqueue_dream``, which schedules
    ``_dream_worker`` — an ``async def`` that called ``sdk._mark_dirty`` and
    ``sdk.dream`` directly in its body. So ONE user write could enqueue a
    further on-loop graph pass after the handler had already returned: the
    write's own response would come back promptly and the loop would freeze
    later, with no request in flight to explain it.

    This drives the real path (a POST /v1/points, unpatched) and blocks the
    WORKER's ``sdk.dream``; the handler itself never calls ``dream``, so the
    blocking call observed here is unambiguously the worker's. The debounce is
    shortened only so the test does not spend it waiting.

    The worker's OTHER synchronous graph call (``sdk._mark_dirty``) is covered
    by an observer rather than a second stall: it is a separate call site, so
    leaving it inline would keep the defect alive for the reverse-BFS pair even
    with ``dream`` off-loaded.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    monkeypatch.setattr(ha_mod, "_DREAM_DEBOUNCE_S", 0.05)
    state: dict = {}
    _record_loop_thread_of(monkeypatch, "_mark_dirty", state,
                           "mark_dirty_on_loop")
    _install_blocking_graph_call(monkeypatch, "dream", state, delegate=False)
    # #3773: the drain's SDK build was the third on-loop graph call — record
    # whether ANY `_make_sdk` (the handler's `_data_sdk` open AND the worker's)
    # ran on the loop.
    make_records: list = []
    _record_site(monkeypatch, "_make_sdk", make_records)

    # The queue/task dicts are module globals shared across tests AND across
    # event loops. A task left pending by an earlier test in this file is not
    # ``done()`` from this loop's point of view, and ``_enqueue_dream`` skips
    # creating a task when one already exists — so without this reset the
    # write below would enqueue a root that nothing ever drains. Start from a
    # clean slate for this key.
    key = ha_mod._dream_key(TEST_ORG_ID, None)
    ha_mod._DREAM_QUEUES.pop(key, None)
    ha_mod._DREAM_TASKS.pop(key, None)

    async def _run():
        ticks: list[float] = []
        stop = {"done": False}

        async def _ticker():
            while not stop["done"]:
                ticks.append(time.perf_counter())
                await asyncio.sleep(TICK_S)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            tick = asyncio.create_task(_ticker())
            await asyncio.sleep(TICK_S * 2)
            point = await ac.post(
                "/v1/points",
                json={"content": "enqueue a dream drain for this write"})
            for _ in range(2000):  # the worker drains after the debounce
                if "entered" in state:
                    break
                await asyncio.sleep(TICK_S / 2)
            entered_or_none = state.get("entered")
            # Drain any worker still parked, so the loop is quiescent when the
            # ticker is stopped (and so `exited` is always recorded).
            for _ in range(2000):
                if "exited" in state:
                    break
                await asyncio.sleep(TICK_S / 2)
            await _quiesce_dream_drain()
            stop["done"] = True
            await tick
            return ticks, point, entered_or_none

    try:
        ticks, point, entered = asyncio.run(_run())
    finally:
        # Safety net: drop the key even if the loop never started.
        ha_mod._DREAM_QUEUES.pop(ha_mod._dream_key(TEST_ORG_ID, None), None)
        ha_mod._DREAM_TASKS.pop(ha_mod._dream_key(TEST_ORG_ID, None), None)
    assert point.status_code == 200, point.text
    assert entered is not None, (
        "the dream worker never reached sdk.dream — the write did not enqueue "
        "a drain (or the worker failed before the call), so this run proves "
        "nothing")
    assert state.get("mark_dirty_on_loop") is False, (
        "a _mark_dirty call ran ON the event loop — the worker's reverse-BFS "
        "pair is synchronous FalkorDB work, so it freezes every concurrent "
        "request just as the dream pass does (#3718)")
    assert make_records, (
        "_make_sdk was never reached — the request and its enqueued drain "
        "both failed before SDK construction, so this run proves nothing")
    assert not any(on_loop for on_loop, _t in make_records), (
        f"_make_sdk ran ON the event loop ({make_records}) — in embedded mode "
        f"its keepalive anchor probe is synchronous FalkorDB work, so the SDK "
        f"build freezes every concurrent request (#3773)")
    assert state.get("ran_on_loop") is False, (
        "_dream_worker's sdk.dream ran ON the event loop — one user write can "
        "enqueue a multi-second synchronous graph pass that freezes every "
        "concurrent request AFTER the write's own response was returned "
        "(#3718)")
    assert not str(state.get("thread")).startswith(
        _SHARED_POOL_THREAD_PREFIX), (
        f"the worker's sdk.dream ran on {state.get('thread')!r} — the shared "
        f"default executor, whose workers the auth path's abuse hooks also need "
        f"(#3060 / #3718)")

    entered, exited = state["entered"], state["exited"]
    in_stall = [t for t in ticks if entered < t < exited]
    assert len(in_stall) >= MIN_TICKS_IN_STALL, (
        f"the event loop completed only {len(in_stall)} tick(s) during the "
        f"{STALL_S:.1f}s dream drain (need {MIN_TICKS_IN_STALL}) — the worker's "
        f"graph pass is blocking the event loop (#3718)")


def test_dream_default_branch_marks_queued_roots_off_loop(client, monkeypatch):
    """#3718 residual 3: ``/v1/dream``'s queued-roots ``_mark_dirty`` is off-loop.

    The default branch (neither ``mode=`` nor ``full=``) drains
    ``_DREAM_QUEUES`` ON the loop (``asyncio.Queue`` is not thread-safe) and
    then calls ``sdk._mark_dirty(queued_roots)`` — a synchronous reverse-BFS
    graph write. It lives inside the closure handed to ``_run_dream_on_pool``,
    so it runs ON THE POOL, not the loop; but nothing asserted that, because
    the branch only executes when the queue is non-empty and no other case
    pre-seeds it. This one does.

    The pass itself (``sdk.dream``) is stubbed: this test pins the MARK's
    thread affinity, not a pass — a real pass against the temp DB is slow and
    is covered by the parametrized ``dream`` cases above.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    state: dict = {}
    real_mark = TortoiseSDK._mark_dirty

    def _observe_mark(self, *args, **kwargs):
        state["mark_called"] = True
        try:
            asyncio.get_running_loop()
            on_loop = True
        except RuntimeError:
            on_loop = False
        # AGGREGATED: a later off-loop mark must not erase an earlier on-loop
        # one (the same rationale as `_record_loop_thread_of`).
        state["mark_on_loop"] = state.get("mark_on_loop", False) or on_loop
        state["mark_thread"] = threading.current_thread().name
        return real_mark(self, *args, **kwargs)

    monkeypatch.setattr(TortoiseSDK, "_mark_dirty", _observe_mark)

    def _stub_dream(self, *args, **kwargs):
        return {"ok": True, "stubbed": True}

    monkeypatch.setattr(TortoiseSDK, "dream", _stub_dream)

    key = ha_mod._dream_key(TEST_ORG_ID, None)
    ha_mod._DREAM_QUEUES.pop(key, None)
    ha_mod._DREAM_TASKS.pop(key, None)

    async def _run():
        # The queue must be created on the loop that serves the request.
        q = asyncio.Queue()
        q.put_nowait("dream-queue-root-3718")
        ha_mod._DREAM_QUEUES[key] = q
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            return await ac.post("/v1/dream")

    try:
        response = asyncio.run(_run())
    finally:
        ha_mod._DREAM_QUEUES.pop(key, None)
        ha_mod._DREAM_TASKS.pop(key, None)

    assert response.status_code == 200, response.text
    assert state.get("mark_called") is True, (
        "the default branch never reached sdk._mark_dirty — the pre-seeded "
        "queue root was not drained, so this run proves nothing (#3718)")
    assert state.get("mark_on_loop") is False, (
        "sdk._mark_dirty ran ON the event loop "
        f"(thread={state.get('mark_thread')!r}) — the queued-roots reverse-BFS "
        "write is synchronous FalkorDB work, so it freezes every concurrent "
        "request (#3718 residual 3)")
