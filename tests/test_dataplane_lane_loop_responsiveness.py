"""#3718 residual 3 — the DATA-plane seam left outside the REST-converted bodies.

The defect (issue #3718): the async handlers in ``tortoise/hosted_api.py`` run
the SYNCHRONOUS TortoiseSDK in their coroutine bodies. ``TortoiseSDK`` ->
``FalkorProjection`` -> ``falkordb`` is a blocking socket client and the app
runs ONE uvicorn worker on Fly, so one inline graph call freezes the process's
single event loop — and with it every concurrent request, for every tenant.

Three earlier PRs off-loaded the REST surfaces (#3772 writes, #4455 reads,
#4578 invite-info; #3773 the ``_data_sdk`` construction). What remained is the
DATA-plane seam outside those bodies: the session lane (``commit_session``,
``delete_session``), the demo seed, the DR/backup lane (7 handlers) and the two
lifecycle/background sites (``_lifespan`` boot drill-GC, ``_run_indexing``
backfill). ``tests/test_read_routes_loop_responsiveness.py`` now declares those
12 bodies in ``_OFFLOADED_ASYNC_BODIES`` — that file owns the AST inventory and
fails if any of them goes back inline.

This file adds the BEHAVIOURAL half, in the same style as that file: the
seam's OWN view of the loop (``asyncio.get_running_loop()`` succeeds only on
the loop thread — a worker thread has no running loop), which is a mechanism
assertion rather than a source grep. The AST pin covers all 12 bodies; the
probe below covers one representative per lane, because that is what can be
driven without a full backup/R2/drill fixture — and two cases cover the calls
NO guard can see (see the next paragraph).

MECHANISM UNDER TEST. Every off-load here is ONE ``asyncio.to_thread`` hand-off
of the body's synchronous region. Where the region was a single no-``await``
sequence on the loop (``commit_session``, ``delete_session``), the hand-off is
deliberately the WHOLE sequence, not one call per query: a per-query hand-off
would ADD interleaving points between statements the original ran
uninterleaved. The behavioural test at the bottom is the guard for that.

⚠️ ONE HAND-OFF IS NOT MUTUAL EXCLUSION. The pre-#3718 inline code was
ACCIDENTALLY atomic: the single-threaded loop serialized every check-then-act
for free. ``asyncio.to_thread`` submits to the loop's SHARED pool, so two
concurrent requests run the closure in two threads and the check-then-act is
genuinely concurrent. Two of the converted bodies had one — ``public_demo``'s
sentinel read → seed, and ``commit_session``'s record read → write phase →
record stamp — and each now takes an explicit lock across the hand-off. This
file carries a two-party-barrier test per body, measuring MAX CONCURRENT
entries into the region (not a sleep, and not wall-clock time), so a missing
lock fails here and a busy pool does not.

⚠️ A ``:CommitRecord`` MERGE IS NOT SUFFICIENT FOR THE COMMIT LOCK. It makes
the WRITE atomic; it does not make the loser WAIT. The winner leaves the record
``partial`` while it writes, and ``plan_commit`` returns ``duplicate=False`` for
a ``partial`` record, so the loser would re-plan against pre-winner graph state
and re-run the write phase. That is what the round-3 review reproduced.

⚠️ HELPER-MEDIATED WORK IS INVISIBLE TO BOTH GUARDS, SO TWO CASES HERE PROVE IT
DIRECTLY. ``count_data_nodes`` takes a raw ``db`` (not a ``_get_proj()`` seam)
and lives in a *sync* helper, so the attach probe and the AST scan both miss it
— the pin's "no inline seam" check passes VACUOUSLY for any body whose graph
work is helper-mediated. ``_lifespan``'s ``_control_plane_source`` is the same
shape one level down (``_registry_sdk`` -> eager ``_make_sdk`` + ``_get_proj``,
plus a probe-retry sleep). Round 3 found both still on the loop despite their
bodies being declared off-loaded; ``test_backups_rebaseline_counts_on_a_worker``
and ``test_lifespan_resolves_the_control_plane_off_loop`` pin them
behaviourally, because no inventory can.

WHY THE PROBE IS WINDOW-SCOPED — ``_get_proj`` is not a request-only seam: the
boot sweeps and the backup watcher run it on daemon worker threads
(``run_on_daemon_worker`` / ``WatcherThread``). Patching the CLASS method
naively picks those up and corrupts the measured window, so the probe records
only SDK instances built through ``_data_sdk`` / ``_make_sdk`` while the request
window is open (``state["active"]``).

WHY NOT THE SIBLING FILE'S MAIN-THREAD GATE — that gate works there because the
read handlers build their SDK on the loop. Here ``_data_sdk_offloaded`` runs
``_data_sdk`` on the graph pool, so a main-thread filter captured NOTHING and
every case failed with "the seam was never reached" (reproduced on the first
revision). The window gate captures the request's SDK wherever it is built. Its
own limit, stated: a BACKGROUND SDK built during the ~1-2 s window would also
be owned — acceptable here because the watcher and the hourly sweeps are
disabled in this lane (no backup config; boot-only sweeps), and because a stray
off-loop call can only ever make ``ran_on_loop`` False, never True.

FLAKE NOTE — the embedded-redislite fixture is not hermetic under this harness
(#3653/#3685, the same note the sibling responsiveness files carry): a
redislite server's tempdir can vanish while a live client still holds it, so a
run can die with ``redis.ConnectionError: .../redis.socket: No such file``.
Re-run before treating such a failure as a regression.
"""
from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest

# The authenticated TestClient fixture (temp-DB SDK patching + the module-level
# env the app needs on import). Imported for the fixture, not for the tests.
from tests.test_hosted_api import TEST_ORG_ID
from tests.test_hosted_api import client as client
from tortoise.sdk import TortoiseSDK

# A stall long enough to be unambiguous, short enough to keep CI quick.
STALL_S = 2.0
# The loop's own tick period while the read is blocked.
TICK_S = 0.02
# Ticks completed INSIDE the blocking window. A blocked loop yields 0 (the
# next tick cannot run until the freeze releases); a free loop yields
# ~STALL_S/TICK_S = 100. Loose on purpose — see the write file's rationale.
MIN_TICKS_IN_STALL = 10

_INTERNAL_KEY = "loop-responsiveness-internal-key"
_SEEDED_SESSION = "dataplane-loop-3718-session"


def _seed_session():
    """Seed the Session ``delete_session`` needs to reach its deletes."""
    import tortoise.hosted_api as ha_mod

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    try:
        sdk._get_proj().g.query(
            "CREATE (s:Session {id:$id, created_at:'2026-01-01T00:00:00Z', "
            "turn_count:0})",
            params={"id": _SEEDED_SESSION},
        )
    finally:
        sdk.close()


class _RequestScopedProbe:
    """A ``TortoiseSDK._get_proj`` probe that only sees the REQUEST's own SDKs.

    ``_get_proj`` is the graph ATTACH every body under test reaches, and the
    registry attaches reach it too (``_get_registry`` calls it on first use),
    so it is the one seam that discriminates off-loop from on-loop for the
    attach without patching each handler's query shape. It is also used by
    background daemon-thread sweeps, so the probe scopes itself to the SDK
    instances built while ``state["active"]`` is set (see the module docstring
    for why the sibling file's main-thread gate cannot be used here).

    LIMIT, stated rather than implied: this observes the ATTACH. Helper-mediated
    graph work inside these bodies (`_record_write_op` — tracked by #4451 —
    `_reconcile_capture_receipts`, `_update_onboarding_state`, `_seed_demo_graph`
    on the demo path's pre-offload shape) is outside both this probe and the
    AST scan; it is the declared one-level-down residual, `_seed_demo_graph`
    excepted (this change off-loads that call itself).
    """

    def __init__(self, monkeypatch, ha_mod, state: dict, *, block: bool):
        self._state = state
        self._block = block
        self._owned: list = state.setdefault("sdks", [])
        self._real_get_proj = TortoiseSDK._get_proj

        real_data_sdk = ha_mod._data_sdk
        real_make_sdk = ha_mod._make_sdk

        def _capture(real):
            def _wrapped(*args, **kwargs):
                sdk = real(*args, **kwargs)
                # Only SDKs built while the request window is open are owned —
                # `_seed_session` and the fixture's boot seeding run before it.
                if self._state.get("active"):
                    self._owned.append(sdk)
                return sdk
            return _wrapped

        monkeypatch.setattr(ha_mod, "_make_sdk", _capture(real_make_sdk))
        monkeypatch.setattr(ha_mod, "_data_sdk", _capture(real_data_sdk))
        # A plain FUNCTION (not a bound method) is required: a function set on
        # the class is a descriptor, so `sdk._get_proj()` passes the SDK as the
        # first argument. A bound method would not, and would raise missing-arg.
        monkeypatch.setattr(TortoiseSDK, "_get_proj", self._make_probe_fn())

    def _make_probe_fn(self):
        def _probe(self_sdk, *args, **kwargs):
            if not any(self_sdk is owned for owned in self._owned):
                return self._real_get_proj(self_sdk, *args, **kwargs)
            try:
                asyncio.get_running_loop()
                on_loop = True
            except RuntimeError:
                on_loop = False
            # AGGREGATED, not last-call-wins: a handler making several calls
            # must not let a later off-loop call erase an on-loop first one.
            self._state["ran_on_loop"] = (
                self._state.get("ran_on_loop", False) or on_loop)
            self._state["thread"] = threading.current_thread().name
            self._state["calls"] = self._state.get("calls", 0) + 1
            if self._block:
                self._state["entered"] = time.perf_counter()
                try:
                    time.sleep(STALL_S)  # stand-in for a slow FalkorDB round trip
                finally:
                    self._state["exited"] = time.perf_counter()
            return self._real_get_proj(self_sdk, *args, **kwargs)
        return _probe


def _arm_dependencies(monkeypatch, *, internal: bool = False):
    """Let the data-plane endpoints run in this harness.

    The shared ``client`` fixture overrides only ``get_current_org``; the
    session / gated surfaces carry their own dependency, so override those too
    (the fixture clears overrides at teardown). ``internal`` sets the lazy
    ``FASTAPI_INTERNAL_KEY`` ``_check_internal`` reads (#880).
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import (
        app,
        get_current_org_gated,
        get_current_org_session_ungated,
    )

    team = _team_dict()
    app.dependency_overrides[get_current_org_gated] = lambda: dict(team)
    app.dependency_overrides[get_current_org_session_ungated] = lambda: dict(team)

    if internal:
        # `monkeypatch.setenv`, NOT a raw `os.environ[...] =`: the raw form is
        # never restored, so after any internal case ran the whole pytest
        # session kept this key and the sibling modules that pin the shared
        # secret at import time (`test_pack_state`, `test_writer_inventory`)
        # 401'd on their own internal calls — order-dependent and it broke a
        # registry-isolation test. (#3718 code-review P1, reproduced in one
        # process.)
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
        monkeypatch.setenv("TORTOISE_BACKUP_STORAGE", "memory")
        # A REAL config object, not a stub: `_backup_mirror_storage` and
        # `_alert_store_from` read fields off it, and a stub without them
        # raised AttributeError before the handler reached its own seam.
        monkeypatch.setattr(ha_mod, "_backup_config_safe", _stub_backup_config)


def _team_dict() -> dict:
    """The authenticated org dict — a legacy full-access key dict.

    Mirrors ``tests.test_hosted_api.TEST_TEAM``'s shape: the write gates read
    ``legacy_full_access`` and the quota enforcement reads the limits keys, so
    a hand-built dict missing them 403s / 500s before the handler's own seam.
    """
    from tests.test_hosted_api import TEST_TEAM

    team = dict(TEST_TEAM)
    team["legacy_full_access"] = True
    return team


def _stub_backup_config():
    """A real ``BackupConfig`` with the sweep on and the geo-mirror off.

    Only the fields the handlers read need real values; the R2/telegram/GitHub
    ones are placeholders because ``TORTOISE_BACKUP_STORAGE=memory`` (set by
    ``_arm_dependencies``) short-circuits ``_backup_storage`` before they are
    used.
    """
    from tortoise.backup_config import BackupConfig

    return BackupConfig(
        enabled=True,
        backup_key=b"0" * 32,
        registry_stream_key=b"1" * 32,
        r2_account_id="acct",
        r2_access_key_id="key",
        r2_secret_access_key="secret",
        r2_bucket="bucket",
        telegram_bot_token="",
        telegram_chat_id="",
        github_issues_pat="",
        alert_assignee="",
        gh_repo="premise-labs/tortoise",
    )


def _delete_session_kwargs():
    return {"method": "DELETE", "url": f"/v1/sessions/{_SEEDED_SESSION}"}


def _public_demo_kwargs():
    return {"method": "POST", "url": "/v1/demo"}


def _commit_session_kwargs():
    """The ``commit_session`` request for the round-3 serialization test
    (:func:`test_concurrent_commit_same_payload_applies_once`). Not a
    ``_CASES`` entry — see the note under ``_CASES``."""
    from tests.test_commit_endpoint import _finalize, _raw_payload

    body = _finalize(_raw_payload(
        1, session_id="dataplane-loop-3718-commit"))
    return {"method": "POST", "url": "/v1/sessions/commit", "json": body}


def _backups_rebaseline_kwargs():
    return {
        "method": "POST",
        "url": "/v1/internal/backups/re-baseline",
        "json": {"org_id": TEST_ORG_ID, "graph_id": "default"},
        "headers": {"Authorization": f"Bearer {_INTERNAL_KEY}"},
    }


def _backups_sweep_kwargs():
    return {
        "method": "POST",
        "url": "/v1/internal/backups/sweep",
        "headers": {"Authorization": f"Bearer {_INTERNAL_KEY}"},
    }


# label, handler, kwargs factory, expected status, expected response-body key
# (``None`` = "not asserted").
#
# ⚠️ The status is asserted for EVERY case. An earlier revision left the two
# internal DR cases at ``None``, which let a handler that 500s *after* reaching
# its graph attach satisfy the probe — the seam was reached, so the off-load
# assertion passed, and nothing looked at what happened next (#3718 round-3
# review). The body key is the cheap second half: it proves the handler
# completed its own success path, not merely that it got past the attach.
_CASES = [
    ("session-delete", "delete_session", _delete_session_kwargs, 200, None),
    ("public-demo", "public_demo", _public_demo_kwargs, 200, None),
    ("backups-rebaseline", "backups_rebaseline", _backups_rebaseline_kwargs,
     200, "status"),
    ("backups-sweep", "backups_sweep", _backups_sweep_kwargs,
     200, "graph_totals"),
]

# NOT driven behaviourally for its OFF-LOAD: ``commit_session``. Its whole
# synchronous block is off-loaded as ONE hand-off (the guard for that is the AST
# pin — its body declares no inline seam), but a real commit costs ~32 s in this
# embedded redislite lane (measured), which is past the harness's 10 s transport
# wait bound and far too expensive to spend inside a responsiveness test. The
# lane's ATTACH is covered by ``delete_session`` (the session lane's other half,
# same mechanism) plus the pin. Separately, ``commit_session``'s SERIALIZATION is
# covered behaviourally by ``test_concurrent_commit_same_payload_applies_once``
# below, which replaces only the write phase so it stays inside the bound.

# The two cases that need the internal-key env + a memory backup store.
_INTERNAL_CASES = {"backups-rebaseline", "backups-sweep"}


@pytest.mark.parametrize("label,handler,request_kwargs,expected,body_key",
                         _CASES, ids=[c[0] for c in _CASES])
def test_dataplane_handler_offloads_graph_io(client, monkeypatch, label, handler,
                                             request_kwargs, expected, body_key):
    """#3718 residual 3: the handler's sync graph ATTACH must not run on the loop.

    ``asyncio.get_running_loop()`` succeeds only when the call executes ON the
    loop thread; a worker thread has no running loop. This is the direct
    mechanism assertion, and it is behavioural — a refactor that puts the
    attach back on the loop fails here regardless of how it is written.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    _arm_dependencies(monkeypatch, internal=label in _INTERNAL_CASES)
    if label == "session-delete":
        _seed_session()

    state: dict = {}
    _RequestScopedProbe(monkeypatch, ha_mod, state, block=False)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            state["active"] = True
            try:
                return await ac.request(**request_kwargs())
            finally:
                state["active"] = False

    response = asyncio.run(_run())

    assert "ran_on_loop" in state, (
        f"[{label}] {handler}'s graph seam was never reached — the request "
        f"short-circuited before it (or errored earlier: "
        f"{response.status_code} {response.text[:200]}), so this run proves "
        f"nothing about the off-load (#3718)")
    assert state.get("ran_on_loop") is False, (
        f"[{label}] {handler} ran its synchronous FalkorDB attach ON the event "
        f"loop (thread={state.get('thread')!r}) — one slow graph round trip "
        f"freezes every concurrent request (#3718)")
    if expected is not None:
        assert response.status_code == expected, (
            f"[{label}] the off-loaded handler did not return its real response "
            f"({response.status_code}: {response.text[:200]})")
    if body_key is not None:
        assert body_key in response.json(), (
            f"[{label}] the handler returned {response.status_code} without its "
            f"own success payload ({body_key!r} missing from "
            f"{response.text[:200]}) — it failed AFTER reaching the graph "
            f"attach, which the off-load assertion alone cannot see (#3718)")


def test_delete_session_does_not_freeze_the_event_loop(client, monkeypatch):
    """#3718 residual 3: a blocked graph attach must not stall the loop.

    The loop-responsiveness half of the invariant on the session-delete lane —
    the handler with the most statements in the converted set (an existence
    read, three provenance reads and four DETACH DELETEs), which this change
    moved into ONE worker hand-off precisely so the sequence stays
    uninterleaved. Two assertions that fail on the inline shape:

    * ``ticks_in_stall >= MIN_TICKS_IN_STALL`` — the loop kept scheduling while
      the attach was blocked (inline yields 0: the ticker's next wake-up cannot
      run until the freeze releases).
    * ``/health`` answered INSIDE the blocking window — the user-visible
      consequence: a concurrent client is served DURING the graph call, not
      queued behind it. /health is pure in-memory (#2850), so a delay in it can
      only come from the loop being blocked, never from a saturated executor.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    _arm_dependencies(monkeypatch)
    _seed_session()
    state: dict = {}
    _RequestScopedProbe(monkeypatch, ha_mod, state, block=True)
    request_kwargs = _delete_session_kwargs()

    async def _drive():
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
            await asyncio.sleep(TICK_S * 2)  # ticker mid-sleep before the read
            state["active"] = True
            read = asyncio.create_task(ac.request(**request_kwargs))
            for _ in range(2000):  # let the read reach the blocking call
                if "entered" in state:
                    break
                await asyncio.sleep(TICK_S / 2)
            assert "entered" in state, (
                "the request's graph call was never reached — this run proves "
                "nothing")
            # Sampled BEFORE the probe: the assertion below rests on /health
            # being answered while the read is STILL in flight.
            read_in_flight = not read.done()
            health = await ac.get("/health")
            health_done = time.perf_counter()
            response = await read
            state["active"] = False
            stop["done"] = True
            await tick
        return ticks, health, health_done, response, read_in_flight

    ticks, health, health_done, response, read_in_flight = asyncio.run(_drive())

    assert state.get("ran_on_loop") is False, (
        "delete_session ran its synchronous FalkorDB attach ON the event loop "
        "(#3718)")

    entered, exited = state["entered"], state["exited"]
    in_stall = [t for t in ticks if entered < t < exited]
    assert len(in_stall) >= MIN_TICKS_IN_STALL, (
        f"the event loop completed only {len(in_stall)} tick(s) during a "
        f"{STALL_S:.1f}s blocked graph call (need {MIN_TICKS_IN_STALL}) — the "
        f"call is blocking the event loop, so every concurrent request waits "
        f"for the graph round trip (#3718)")

    assert health.json()["status"] in {"ok", "degraded"}, health.text
    assert health_done < exited, (
        "the /health probe was NOT served while the graph call was blocked "
        "(answered %.0fms AFTER the block released) — a concurrent request "
        "queued behind the graph call instead of being served (#3718)"
        % ((health_done - exited) * 1000))
    assert read_in_flight, (
        "the request had already returned before /health was issued, so this "
        "run proves nothing about a request served DURING the blocking window "
        "(#3718)")
    assert response.status_code == 200, response.text[:200]


def test_public_demo_seed_is_serialized_per_org(client, monkeypatch):
    """#3718: one worker hand-off is NOT mutual exclusion for ``public_demo``.

    ``asyncio.to_thread`` submits the seed region to the loop's SHARED pool, so
    two concurrent POST /v1/demo requests run it in two threads. The pre-#3718
    inline code was serialized by the single-threaded loop — the sentinel read
    could not interleave with another request's seed. Off-loading the region
    removes that accident, so the check-then-act needs the explicit per-org
    lock. Without it both requests enter ``_seed_demo_graph``, and since the
    sentinel is written with CREATE (not MERGE) that means two sentinel nodes
    and two metered write ops.

    Round-2 review reproduced exactly that with a two-party barrier; this is
    that probe as a permanent test. It measures MAX CONCURRENT seed entries, so
    it reddens only if the lock is gone — not merely because the pool is busy.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    _arm_dependencies(monkeypatch)
    # Fresh lock map: an ``asyncio.Lock`` binds to the loop that first contends
    # for it, and this test drives its own loop.
    monkeypatch.setattr(ha_mod, "_DEMO_SEED_LOCKS", {})

    gate = threading.Lock()
    state = {"concurrent": 0, "max": 0}
    # A real two-party barrier, not a sleep: it releases only when BOTH
    # requests are inside the seed simultaneously, so the reading is not a
    # timing guess. The timeout is what makes a LOCKED run fail fast instead of
    # hanging — the second party never arrives, so ``wait`` raises.
    entered = threading.Barrier(2, timeout=3)

    def _slow_seed(org_id):
        with gate:
            state["concurrent"] += 1
            state["max"] = max(state["max"], state["concurrent"])
        try:
            entered.wait()
        except threading.BrokenBarrierError:
            pass
        finally:
            with gate:
                state["concurrent"] -= 1
        return {"status": "demo_created", "points": 12}

    monkeypatch.setattr(ha_mod, "_seed_demo_graph", _slow_seed)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            return await asyncio.gather(ac.post("/v1/demo"),
                                        ac.post("/v1/demo"))

    responses = asyncio.run(_run())

    assert state["max"] >= 1, (
        "neither concurrent POST /v1/demo reached the seeder, so this run "
        f"proves nothing (statuses={[r.status_code for r in responses]})")
    assert state["max"] == 1, (
        f"two concurrent POST /v1/demo both ran the seed (max concurrent "
        f"entries={state['max']}) — the per-org lock is gone, so the sentinel "
        f"check is a check-then-act across a thread hand-off (#3718)")


def test_concurrent_commit_same_payload_applies_once(client, monkeypatch):
    """#3718 round 3: one worker hand-off is NOT mutual exclusion for
    ``commit_session`` — and the ``:CommitRecord`` MERGE does not make the
    loser wait.

    Pre-#3718 the whole synchronous block was a single no-``await`` sequence on
    the loop, so two POSTs with the SAME ``client_commit_id`` were serialized
    for free by the single-threaded loop. ``asyncio.to_thread`` runs the closure
    on the loop's SHARED pool, so both now run the check-then-act concurrently —
    and the loser IS reachable: ``store.acquire`` only sets fields ``ON
    CREATE`` (so the winner's record stays ``partial`` while it writes) and
    ``plan_commit`` returns ``duplicate=False`` for a ``partial`` record, so the
    loser re-plans against pre-winner graph state and re-runs the write phase.
    That applies ``commit_count + 1`` and ``value_nodes_created + $created``
    twice and bills two write-ops for one logical payload.

    This is the round-3 review's reproduction as a permanent test, in the
    ``public_demo`` serialization test's shape: a two-party
    ``threading.Barrier`` (not a sleep) counts MAX CONCURRENT write-phase
    entries, so it reddens only if the per-``(org, client_commit_id)`` lock is
    gone — not merely because the pool is busy. The REAL write phase is
    replaced by the barrier stand-in (a real commit measures ~32 s in this
    embedded lane, past the harness's 10 s transport wait bound), so this
    asserts the SERIALIZATION; the off-load itself is pinned by the AST
    inventory in ``tests/test_read_routes_loop_responsiveness.py``.

    Mutation proof (round 3): with the lock removed, ``max`` is 2 and the
    metering row reads ``write_ops=2``; with it, ``max`` is 1 and ``write_ops``
    is 1.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    _arm_dependencies(monkeypatch)
    # Fresh lock maps: an ``asyncio.Lock`` binds to the loop that first contends
    # for it, and this test drives its own loop (the entry's refcount is dropped
    # at release, but a lock left behind by an earlier loop would still be
    # reused by this one).
    monkeypatch.setattr(ha_mod, "_COMMIT_LOCKS", {})
    monkeypatch.setattr(ha_mod, "_COMMIT_LOCK_REFS", {})

    gate = threading.Lock()
    state = {"concurrent": 0, "max": 0}
    # Releases only when BOTH requests are inside the write phase at once, so
    # the reading is not a timing guess; the timeout is what makes a LOCKED run
    # fail fast instead of hanging (the second party never arrives).
    entered = threading.Barrier(2, timeout=3)

    def _slow_writes(sdk, payload, plan):
        with gate:
            state["concurrent"] += 1
            state["max"] = max(state["max"], state["concurrent"])
        try:
            entered.wait()
        except threading.BrokenBarrierError:
            pass
        finally:
            with gate:
                state["concurrent"] -= 1

    monkeypatch.setattr(ha_mod, "_execute_commit_writes", _slow_writes)
    request_kwargs = _commit_session_kwargs()

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            return await asyncio.gather(ac.request(**request_kwargs),
                                        ac.request(**request_kwargs))

    responses = asyncio.run(_run())

    assert state["max"] >= 1, (
        "neither concurrent commit reached the write phase, so this run proves "
        f"nothing (statuses={[r.status_code for r in responses]})")
    # The CONSEQUENCE first, so a mutation run names the double-apply itself
    # rather than only the mechanism: one logical payload must be metered once.
    reg = ha_mod._make_sdk(namespace="registry")._get_registry()
    rows = reg.query(
        "MATCH (m:MeteringRecord {org_id:$tid}) "
        "RETURN m.write_ops, m.nodes_written",
        params={"tid": TEST_ORG_ID}).result_set
    assert rows and rows[0][0] == 1, (
        f"one logical commit metered {rows!r} — the losing request re-ran the "
        f"write phase and billed a write-op too (the counter double-apply) "
        f"(#3718)")

    bodies = [r.json() for r in responses]
    assert sorted(bool(b["duplicate"]) for b in bodies) == [False, True], (
        "exactly one of the two requests must win (duplicate=False) and the "
        f"other must replay (duplicate=True); got {bodies}")
    assert state["max"] == 1, (
        f"two concurrent commits with the SAME client_commit_id both ran the "
        f"write phase (max concurrent entries={state['max']}) — the "
        f"per-(org, client_commit_id) lock is gone, so the Session counters and "
        f"the write-op meter are applied twice for one logical payload (#3718)")


def test_lifespan_resolves_the_control_plane_off_loop(monkeypatch):
    """#3718 round 3: ``_lifespan``'s control-plane resolve must not run on the
    loop — the pin could never see it.

    ``_control_plane_source`` is SYNC, and its registry lane is not cheap:
    ``_registry_sdk()`` constructs the registry SDK and EAGERLY attaches
    (``_make_sdk`` + ``sdk._get_proj()``), with a possible
    ``time.sleep(PROBE_RETRY_DELAY)`` retry — a blocking graph round trip AND a
    sleep, on the loop, at boot. It is INVISIBLE to the AST pin by construction
    (the scan walks async bodies; this is a sync helper one level down), so
    ``_lifespan``'s membership in ``_OFFLOADED_ASYNC_BODIES`` proved nothing
    about it — the vacuous pass the round-3 review named. This is the
    behavioural half.

    The REAL lifespan is driven with the REAL watcher branch armed (a real
    ``BackupConfig``), because the resolve sits inside ``if cfg and not
    _watcher_disabled`` — an un-armed boot would never reach the call and the
    test would pass for the wrong reason, so ``state['branch']`` is asserted.
    ``asyncio.get_running_loop()`` succeeds only on the loop thread; a worker
    thread has no running loop, which is the mechanism assertion the sibling
    cases use.

    Stubbed, and only stubbed: the two background boot tasks
    (``_first_contact_prewarm`` — Supabase-land network work, inert in registry
    mode — and ``_run_boot_sweeps`` — retention churn) and ``WatcherThread``,
    whose real threads start an initial 60 s poll delay and would outlive this
    test. The resolve under test is reached before any of them.
    """
    import contextlib
    import os
    import tempfile

    import tortoise.backup_watcher as backup_watcher
    import tortoise.hosted_api as ha_mod
    from tests._http_fixtures import patched_tortoise_sdk
    from tortoise.hosted_api import app

    state: dict = {}

    class _StubWatcherThread:
        def __init__(self, watcher, interval_seconds=600):
            state["branch"] = True

        def start(self):
            pass

        def stop(self):
            pass

    class _StubMcpApp:
        def lifespan(self, app):
            @contextlib.asynccontextmanager
            async def _cm():
                yield
            return _cm()

    async def _noop(*args, **kwargs):
        return None

    real_source = ha_mod._control_plane_source

    def _probe():
        try:
            asyncio.get_running_loop()
            state["on_loop"] = True
        except RuntimeError:
            state["on_loop"] = False
        state["calls"] = state.get("calls", 0) + 1
        return real_source()

    prior_watcher = ha_mod._WATCHER
    with (
        tempfile.TemporaryDirectory() as tmpdir,
        patched_tortoise_sdk(os.path.join(tmpdir, "test.db")),
    ):
        monkeypatch.setenv("TORTOISE_BACKUP_STORAGE", "memory")
        monkeypatch.delenv("BACKUP_WATCHER_DISABLED", raising=False)
        monkeypatch.setattr(ha_mod, "_backup_config_safe",
                            _stub_backup_config)
        monkeypatch.setattr(ha_mod, "mcp_http_app", _StubMcpApp())
        monkeypatch.setattr(ha_mod, "_first_contact_prewarm", _noop)
        monkeypatch.setattr(ha_mod, "_run_boot_sweeps", _noop)
        monkeypatch.setattr(backup_watcher, "WatcherThread",
                            _StubWatcherThread)
        monkeypatch.setattr(ha_mod, "_control_plane_source", _probe)
        try:
            async def _boot():
                async with app.router.lifespan_context(app):
                    pass

            asyncio.run(_boot())
        finally:
            ha_mod._WATCHER = prior_watcher

    assert state.get("calls") == 1, (
        "_lifespan did not reach its control-plane resolve, so this run proves "
        f"nothing (watcher branch taken={state.get('branch')!r})")
    assert state.get("branch") is True, (
        "the watcher branch was not armed, so the resolve under test was never "
        "reached (#3718)")
    assert state.get("on_loop") is False, (
        "_lifespan resolved the control plane ON the event loop — in registry "
        "mode that constructs the registry SDK and attaches EAGERLY "
        "(_make_sdk + _get_proj, plus a probe-retry sleep), so boot holds the "
        "loop for a blocking graph round trip (#3718)")


def test_backups_rebaseline_counts_on_a_worker(client, monkeypatch):
    """#3718 round 3: ``backups_rebaseline``'s node count must run off-loop.

    ``count_data_nodes(db, graph_name)`` does
    ``db.select_graph(...).query(...).result_set`` — a blocking round trip in a
    body DECLARED off-loaded. It was invisible to BOTH guards by construction:
    it takes a raw ``db`` (not a ``_get_proj()`` seam, so the attach probe and
    the AST pin's seam callees never see it) and it lives in a *sync* helper
    (so the scan, which walks async bodies, never walks it). The pin's "no
    inline seam" check therefore passed VACUOUSLY for this body — the
    declaration-fidelity gap the round-3 review named.

    The module attribute is patched, not the module-scope name: the handler
    imports ``count_data_nodes`` INSIDE its body (``from tortoise.hosted_backup
    import ...``), so the local binding is read from the module at call time.
    One call, off the loop, and the handler still completes its own success
    path (200 + ``status``) — a post-attach 500 no longer passes.
    """
    import tortoise.hosted_backup as hosted_backup
    from tortoise.hosted_api import app

    _arm_dependencies(monkeypatch, internal=True)
    state: dict = {}
    real_count = hosted_backup.count_data_nodes

    def _probe(db, graph_name):
        try:
            asyncio.get_running_loop()
            state["on_loop"] = True
        except RuntimeError:
            state["on_loop"] = False
        state["calls"] = state.get("calls", 0) + 1
        return real_count(db, graph_name)

    monkeypatch.setattr(hosted_backup, "count_data_nodes", _probe)
    request_kwargs = _backups_rebaseline_kwargs()

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            return await ac.request(**request_kwargs)

    response = asyncio.run(_run())

    assert state.get("calls") == 1, (
        "the re-baseline never counted a graph, so this run proves nothing "
        f"({response.status_code}: {response.text[:200]})")
    assert state.get("on_loop") is False, (
        "backups_rebaseline ran count_data_nodes ON the event loop — a blocking "
        "select_graph().query().result_set while the body is declared "
        "off-loaded (#3718)")
    assert response.status_code == 200, response.text[:200]
    assert response.json().get("status") == "rebaselined", response.text[:200]
