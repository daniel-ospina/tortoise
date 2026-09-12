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
3. every dashboard boot call (``/v1/user/identity``, ``/v1/teams``,
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
the loop fails here regardless of shape. (Two deliberate exceptions pin the
implementation the invariant depends on: the capture pool's thread-name prefix
in the mechanism test, and the `/health` probe actually running in the liveness
test — both are noted where they appear.)
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
    TEST_TEAM_ID,
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
        f"capture pool — long stalls there can starve /health's probe and the "
        f"auth middleware out of the shared default executor (#3060)")


def test_stalled_capture_does_not_freeze_the_event_loop(client, monkeypatch):
    """The invariant: while a capture is stalled, the API keeps answering.

    Both assertions are order-independent — they compare observed timestamps
    against the stall window recorded by the fake itself, so neither can pass
    by accident of scheduling:

    * ``ticks_in_stall`` — the PRIMARY signal: ticks the loop completed while
      the extraction was stalled. A blocked loop yields 0, because the next
      tick can only run once the freeze releases. This is immune to the
      sub-second synchronous graph work the endpoint legitimately does.
    * the worst tick interval INSIDE the stall window. Only intervals fully
      inside are considered: setup work before the capture reached the
      extraction is legitimately slow (seconds) on a loaded runner and would
      otherwise be misread as a freeze. A call that blocks for part of the
      stall still shows up here as one long interval.
    * the ``/health`` request completes BEFORE the stall ends AND AFTER the
      extraction entered it, i.e. the API answered DURING the freeze window
      rather than queued behind it (or served before it began).

    Mutation check (must stay true): calling the extraction inline
    (`return fn(*args, **kwargs)` instead of dispatching to the pool) makes the
    primary assertion fail (0 ticks in the window). The narrower regression of
    moving it back to the SHARED pool via `asyncio.to_thread` still runs
    off-loop and passes HERE — it is pinned by the pool-name assertion in
    `test_capture_extraction_runs_off_the_event_loop`, which fails for both
    branches (measured).
    """
    from tortoise.hosted_api import app

    state: dict[str, float] = {}

    def _stalled_extract(_self, windowed, session_id, now, **kw):
        state["entered"] = time.perf_counter()
        time.sleep(STALL_S)  # stand-in for a wedged provider call
        state["exited"] = time.perf_counter()
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
            await asyncio.sleep(0.05)  # let the capture reach the extraction
            health = await ac.get("/health")
            health_done = time.perf_counter()
            cap = await capture
            stop["done"] = True
            await tick
        return ticks, health, health_done, cap

    ticks, health, health_done, cap = asyncio.run(_run())

    assert "entered" in state, "the capture never reached the extraction"
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

    assert health_done < exited, (
        "no /health response was served while the capture was stalled — the "
        "API was mute for the whole stall (#3060)")
    assert health_done > entered, (
        "/health answered BEFORE the capture entered its stall, so this run "
        "proves nothing about liveness under load (#3060) — the answer must "
        "be served inside the stall window")

    assert cap.status_code == 200, cap.text


def test_health_answers_while_the_default_executor_is_saturated(
        client, monkeypatch):
    """#3060 review finding: liveness must not queue behind the shared pool.

    The first revision moved the extraction to `asyncio.to_thread`, which uses
    the loop's SHARED default executor — the same one /health's DB probe (and
    the auth middleware's abuse hooks) use. Six-plus concurrent stalls (prod
    runs 2 vCPU → `min(32, cpu+4)` workers) would therefore leave the probe
    queued for minutes, miss Fly's 15s check timeout, and drop the machine
    exactly as in the original outage — one level down, with nothing blocking
    the loop at all.

    This test occupies EVERY default-executor worker with a blocking task and
    then requires /health to answer within a short budget. `wait_for` (rather
    than a bare await) is the assertion: if the probe queues, the request does
    not answer at all and this fails, instead of the suite hanging.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    # Spy on the probe: latency + `db.ok` alone are satisfied by a /health that
    # never probes at all (review finding — proven by stubbing the submission
    # path), so assert the probe RAN before judging how fast the answer was.

    probed: list[int] = []
    _real_probe = ha_mod._probe_db

    def _spy_probe():
        probed.append(1)
        return _real_probe()

    monkeypatch.setattr(ha_mod, "_probe_db", _spy_probe)

    HOG_WAIT_S = 30.0
    # Derived from the probe's OWN documented worst case (1.5s timeout + 0.1s
    # retry delay + 1.5s retry, monitoring.py:24/31) plus 1s slack, so a
    # slow-but-healthy probe can never red the suite and the bound stays
    # honest if those constants change (review finding: a hardcoded 4.0 sat
    # 0.9s above the design's own worst case). Below the production probe
    # budget (5s), so the verdict comes from design, not timer ordering.
    from tortoise.monitoring import PROBE_RETRY_DELAY, PROBE_TIMEOUT
    HEALTH_BUDGET_S = 2 * PROBE_TIMEOUT + PROBE_RETRY_DELAY + 1.0

    async def _run():
        release = threading.Event()

        def _hog():
            release.wait(timeout=HOG_WAIT_S)

        loop = asyncio.get_running_loop()
        # More hogs than the pool has workers, so every worker is taken.
        workers = max(4, (os.cpu_count() or 1) + 4)
        hogs = [loop.run_in_executor(None, _hog) for _ in range(workers + 4)]
        try:
            await asyncio.sleep(0.3)  # let the hogs claim every worker
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as ac:
                started = time.perf_counter()
                try:
                    r = await asyncio.wait_for(ac.get("/health"),
                                               timeout=HEALTH_BUDGET_S)
                except TimeoutError:
                    raise AssertionError(
                        f"/health did not answer within {HEALTH_BUDGET_S}s "
                        f"while every default-executor worker was busy — the "
                        f"liveness probe is sharing a pool with long/stallable "
                        f"work, so a few stalled captures would starve it and "
                        f"Fly would drop the machine exactly as in #3060"
                    ) from None
                return r, time.perf_counter() - started
        finally:
            release.set()
            await asyncio.gather(*hogs, return_exceptions=True)

    r, elapsed = asyncio.run(_run())

    assert r.status_code == 200, r.text
    assert probed, (
        "the /health DB probe never ran — a short-circuiting liveness handler "
        "would otherwise pass this test")
    assert r.json()["db"]["ok"] is True, r.text
    assert elapsed < HEALTH_BUDGET_S, (
        f"/health took {elapsed:.2f}s while every default-executor worker was "
        f"busy — the liveness probe is sharing a pool with long/stallable "
        f"work, so a few stalled captures would starve it and Fly would drop "
        f"the machine exactly as in #3060")


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
    state = ha_mod._get_onboarding_state(TEST_TEAM_ID)
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

    async def _parked_impl(body, request, team, slot=None):
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
        _current_scopes,
        _current_team_id,
        _current_team_limits,
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
        ctx_vars = [_current_team_id, _current_team_limits, _current_graph_id,
                    _current_graph_namespace, _current_scopes,
                    _current_legacy_full_access]
        toks = [v.set(val) for v, val in zip(
            ctx_vars,
            [TEST_TEAM_ID, {}, None, None, ["graphs:read", "graphs:write"],
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
                    TEST_TEAM_ID).get(receipt_key)
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


def test_in_flight_session_keys_are_scoped_to_their_tenant():
    """#3129 (reviewer finding): the in-flight key is per-TENANT.

    Session ids are client-chosen and often generic, so a process-global bare
    ``session_id`` key would refuse an unrelated tenant's capture (measured
    409). Only the admission COUNTER is global — it bounds a server resource.
    """
    from fastapi import HTTPException

    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import _capture_session_key, _reserve_capture_slot

    team_a = {"team_id": "team-a", "graph_id": None}
    team_b = {"team_id": "team-b", "graph_id": None}
    key_a = _capture_session_key(team_a, "shared-id")
    key_b = _capture_session_key(team_b, "shared-id")
    key_a_g1 = _capture_session_key(
        {"team_id": "team-a", "graph_id": "g_1"}, "shared-id")
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
