"""#3718 residual 2 — the REST graph-READ handlers must not run sync FalkorDB I/O on the loop.

The defect these tests pin (the read half of #3718; the write half shipped in
PR #3772): the read handlers in ``tortoise/hosted_api.py`` are ``async def``
routes that called the SYNCHRONOUS TortoiseSDK in their coroutine bodies.
``TortoiseSDK`` → ``FalkorProjection`` → ``falkordb`` 1.6.2 is a blocking
socket client, and the app runs ONE uvicorn worker, so one slow graph read
froze the process's single event loop — and with it every concurrent request,
the capture path included. The registry reads (``_get_registry().query``) are
the same class: they share the data-plane connection (see
``TortoiseSDK._get_registry``'s docstring).

Measured analogues for the writes (recorded on #3718): 5 inline writes produced
ONE loop tick (max gap 231.4 ms); the identical calls off-loaded produced 120
ticks (p50 1.42 ms). A read is the same ~35 ms embedded round trip.

These tests assert the INVARIANT, not the mechanism: while the read is blocked,
the loop keeps ticking AND a concurrent trivial request is answered INSIDE the
read's blocking window. A future refactor that puts the read back on the loop
fails here regardless of how it is written. The ``ran_on_loop`` assertion is
the direct mechanism check — the call's own view of the loop (a worker thread
has no running loop), not a source grep for ``to_thread``.

Mechanism under test (same as the write handlers): the short (~35 ms) sync
FalkorDB read goes through ``await asyncio.to_thread(...)``, the file's house
style (the ``/v1/search`` / ``/v1/topics/{t}/summary`` precedent), with the
``_get_proj()`` / ``_get_registry()`` attach riding the SAME hand-off so the
first (connect) request is not the one that blocks.

WHY THE PROBE IS REQUEST-SCOPED — ``_get_proj`` is not a request-only seam: the
boot sweeps run it on a daemon worker thread (``run_on_daemon_worker``,
``tortoise-boot-sweep``), and patching the CLASS method naively picked up those
calls, corrupting the measured window (reproduced: ``thread='tortoise-boot-sweep'``
in the recorded state). The probe below therefore records only SDK instances the
REQUEST created on the main thread (by wrapping ``_data_sdk`` / ``_make_sdk``),
and delegates every other call untouched.

SCOPE — the handlers converted in this change (the read-only FalkorDB class):
``list_points``, ``get_point``, ``org_info``, ``list_sessions``,
``get_session_detail``, ``dream_health`` (graph) and ``list_api_keys``,
``list_members``, ``list_pending_invites_for_me`` (registry), plus the hot
onboarding PATCH's read (``patch_onboarding_state``) and the public invite-info
read (``invite_info``, ``GET /v1/invites/info``). ``invite_info``'s reads are
off-loaded too — the registry legs via ``asyncio.to_thread``, the Supabase
control-plane legs via ``_cp_offload`` — so it is no longer in
``_KNOWN_INLINE_ROUTE_RESIDUAL``. EVERY OTHER async body in
the module that still runs sync FalkorDB I/O inline is enumerated in
``_KNOWN_INLINE_ROUTE_RESIDUAL`` / ``_KNOWN_INLINE_HELPER_RESIDUAL`` below —
that is the declared, non-silent residual (it includes the per-request
``get_current_org`` auth dependency and ``_capture_session_impl``). The AST pin
fails on a NEW inline site, which is the "the class cannot silently return"
guard; the residual is burned down under #3718 (kept open via #2924).

#4660 — the pin used to see ONE class of seam. ``_GRAPH_SEAM_CALLEES`` covers
the graph reads/attaches, but NOT SDK CONSTRUCTION, which is likewise blocking:
in embedded mode ``_make_sdk`` runs ``_anchor_usable``'s probe query and, on an
anchor miss, an eager ``TortoiseSDK(...)`` connect that holds the socket. The pin
therefore passed while four bodies that PR #4645 claimed to have off-loaded still
built their SDK on the loop — a reviewer reading the module was what caught it.
The scan now reports BOTH classes (``_inline_seam_hits``) as separate lists, and
the construction residual is declared in
``_OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION``. The behavioural half
(``sdk_built_on_loop``) lives in the dataplane file, because that probe is
window-scoped and so can see the construction a sync wrapper hides from any
source scan.

FLAKE NOTE — the embedded-redislite fixture is not hermetic under this harness
(#3653/#3685, the same note the write-responsiveness file carries): a
redislite server's tempdir can vanish while a live client still holds it, so a
run can die with ``redis.ConnectionError: .../redis.socket: No such file``.
Re-run before treating such a failure as a regression.
"""
from __future__ import annotations

import ast
import asyncio
import threading
import time
from pathlib import Path

import httpx
import pytest

# Shared nested-def-on-loop walkers (#4625 review) — one home for both guards,
# so Guard A's blindness to an invoked local closure cannot drift back in.
from tests._loop_ast import callee_name as _callee_name
from tests._loop_ast import nested_defs_invoked_on_loop as _nested_defs_invoked_on_loop
from tests._loop_ast import offload_boundary_eager_children as _offload_boundary_eager_children

# The authenticated TestClient fixture (temp-DB SDK patching + the module-level
# env the app needs on import). Imported for the fixture, not for the tests.
from tests.test_hosted_api import TEST_ORG_ID
from tests.test_hosted_api import client as client
from tortoise.sdk import TortoiseSDK

HOSTED_API = Path(__file__).resolve().parent.parent / "tortoise" / "hosted_api.py"

# A stall long enough to be unambiguous, short enough to keep CI quick.
STALL_S = 2.0
# The loop's own tick period while the read is blocked.
TICK_S = 0.02
# Ticks completed INSIDE the blocking window. A blocked loop yields 0 (the
# next tick can only run once the freeze releases); a free loop yields
# ~STALL_S/TICK_S = 100. Loose on purpose — see the write file's rationale.
MIN_TICKS_IN_STALL = 10

_SEEDED_POINT = "read-loop-3718-point"
_SEEDED_SESSION = "read-loop-3718-session"
# The public invite-info read (#3718): its registry lookup iterates pending
# ``Invitation`` rows and verifies the presented token against ``token_hash``,
# so a seeded pending invite + its Team is what makes the handler reach its
# read at all.
_SEEDED_INVITE_TOKEN = "read-loop-3718-invite-token"
_SEEDED_INVITE_ORG = "read-loop-3718-invite-org"


def _seed_read_surface():
    """Seed a Point, a Session, a pending invite + its Team, and WARM the
    embedded anchors.

    Warming matters only for EMBEDDED-SERVER liveness: ``_make_sdk`` eagerly
    connects a brand-new keepalive anchor so the redislite server survives
    between requests. That eager connect is NOT observable by the probe below —
    the anchor is a direct ``TortoiseSDK(...)`` inside ``_make_sdk``, never the
    object ``_make_sdk`` returns, so the request-scoped ownership gate already
    excludes it. Warming both namespaces simply keeps the embedded daemon up.
    """
    import tortoise.hosted_api as ha_mod

    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (p:Point {id:$id, content:'seed', pointKind:'statement'})",
            params={"id": _SEEDED_POINT},
        )
        proj.g.query(
            "CREATE (s:Session {id:$id, created_at:'2026-01-01T00:00:00Z', "
            "turn_count:0})",
            params={"id": _SEEDED_SESSION},
        )
    finally:
        sdk.close()
    reg = ha_mod._make_sdk(namespace="registry")
    try:
        reg._get_registry()  # warm the registry graph + its indexes
        from tortoise.auth import hash_api_key as _hash
        reg._get_registry().query(
            "CREATE (t:Team {id:$tid, name:$tname})",
            params={"tid": _SEEDED_INVITE_ORG,
                    "tname": "read-loop-3718-team"},
        )
        reg._get_registry().query(
            "CREATE (i:Invitation {id:'read-loop-3718-invite', org_id:$tid, "
            "email:'invitee@example.com', role:'member', token_hash:$th, "
            "inviter_email:'owner@example.com', expires_at:null, "
            "accepted_at:null, status:'pending'})",
            params={"tid": _SEEDED_INVITE_ORG,
                    "th": _hash(_SEEDED_INVITE_TOKEN)},
        )
    finally:
        reg.close()


# label, request kwargs — one case per converted READ handler (the onboarding
# PATCH is asserted by the AST pin; its request path needs a full onboarding
# state fixture, a different test's lane). `url` (not `path`) is the httpx kwarg.
_READ_CASES = [
    ("points-list", "list_points",
     lambda: {"method": "GET", "url": "/v1/points"}),
    ("point-get", "get_point",
     lambda: {"method": "GET", "url": f"/v1/points/{_SEEDED_POINT}"}),
    ("dream-health", "dream_health",
     lambda: {"method": "GET", "url": "/v1/dream/health"}),
    ("team", "org_info",
     lambda: {"method": "GET", "url": "/v1/team"}),
    ("sessions-list", "list_sessions",
     lambda: {"method": "GET", "url": "/v1/sessions"}),
    ("session-get", "get_session_detail",
     lambda: {"method": "GET", "url": f"/v1/sessions/{_SEEDED_SESSION}"}),
    ("team-keys", "list_api_keys",
     lambda: {"method": "GET", "url": "/v1/team/keys"}),
    ("members", "list_members",
     lambda: {"method": "GET",
              "url": f"/v1/organizations/{TEST_ORG_ID}/members"}),
    ("invites-pending", "list_pending_invites_for_me",
     lambda: {"method": "GET", "url": "/v1/invites/pending"}),
    ("invite-info", "invite_info",
     lambda: {"method": "GET",
              "url": f"/v1/invites/info?token={_SEEDED_INVITE_TOKEN}"}),
]


class _RequestScopedProbe:
    """A ``TortoiseSDK._get_proj`` probe that only sees REQUEST-owned SDKs.

    ``_get_proj`` is the graph ATTACH every read handler under test reaches
    (the registry reads get there through ``_get_registry``; ``dream_health``
    reaches it through ``dream_health_check`` -> ``_hydrate_dirty_roots``),
    which makes it the one seam that discriminates off-loop from on-loop for
    the attach without patching each handler's query shape. But it is also used
    by background daemon-thread sweeps, so the probe scopes itself to the SDK
    instances the REQUEST created on the main thread.

    LIMIT, stated rather than implied: this observes the ATTACH, not the query
    round trip. A read reverted to inline on an ALREADY-ATTACHED projection
    (e.g. ``get_session_detail``'s four ``proj.g.query(...)`` calls after its
    one off-loaded ``_get_proj``) is NOT caught behaviourally — the AST pin's
    ``.g.query`` rule is the guard for that (and it fails on exactly that
    revert). Reads made through a SYNC helper are outside both gates (the AST
    scan walks async bodies only); those are part of the declared residual.
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
                # Only the request's own construction (main thread + the
                # in-flight loop) is owned; a daemon-thread sweep's SDK is not.
                if threading.current_thread() is threading.main_thread():
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
            # AGGREGATED, not last-call-wins: a retry added later must not let
            # a second, off-loop call erase an on-loop first one.
            self._state["ran_on_loop"] = (
                self._state.get("ran_on_loop", False) or on_loop)
            self._state["thread"] = threading.current_thread().name
            if self._block:
                self._state["entered"] = time.perf_counter()
                try:
                    time.sleep(STALL_S)  # stand-in for a slow FalkorDB round trip
                finally:
                    self._state["exited"] = time.perf_counter()
            return self._real_get_proj(self_sdk, *args, **kwargs)
        return _probe


def _arm_session_endpoints(monkeypatch):
    """Let the session-authed read endpoints run in this harness.

    ``list_members`` / ``list_pending_invites_for_me`` are ``get_current_user``
    session-authed (the shared ``client`` fixture only overrides
    ``get_current_org``), so override it here — the fixture clears overrides.

    ``list_members`` also runs ``_require_owner_admin`` (a registry read)
    BEFORE its own off-loaded read. That helper is a declared on-loop residual
    of this change (``_KNOWN_INLINE_HELPER_RESIDUAL``): leaving it in would put
    a SECOND graph call on the loop and mask the handler's own state. Gate it
    out — this test measures the HANDLER's read, not the auth helper.
    """
    from tortoise.hosted_api import app, get_current_user
    app.dependency_overrides[get_current_user] = lambda: {
        "user_id": "read-loop-3718-user",
        "email": "read-loop-3718@example.com",
    }

    async def _allow(*_a, **_k):
        return None

    import tortoise.hosted_api as ha_mod
    monkeypatch.setattr(ha_mod, "_require_owner_admin", _allow)


@pytest.mark.parametrize("label,handler,request_kwargs", _READ_CASES,
                         ids=[c[0] for c in _READ_CASES])
def test_read_route_offloads_graph_io(client, monkeypatch, label, handler,
                                      request_kwargs):
    """#3718 residual 2: the handler's sync FalkorDB read must not run on the loop.

    ``asyncio.get_running_loop()`` succeeds only when the call executes ON the
    loop thread; a worker thread has no running loop. This is the direct
    mechanism assertion and it is behavioural (the call's own view of the
    loop), not a source-grep for ``to_thread`` — a refactor that puts the read
    back on the loop fails here regardless of how it is written.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    _seed_read_surface()
    _arm_session_endpoints(monkeypatch)

    state: dict = {}
    _RequestScopedProbe(monkeypatch, ha_mod, state, block=False)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            return await ac.request(**request_kwargs())

    response = asyncio.run(_run())

    assert "ran_on_loop" in state, (
        f"[{label}] {handler}'s graph seam was never reached — the request "
        f"short-circuited before its read (or errored earlier), so this run "
        f"proves nothing about the off-load (#3718)")
    assert state.get("ran_on_loop") is False, (
        f"[{label}] {handler} ran its synchronous FalkorDB read ON the event "
        f"loop (thread={state.get('thread')!r}) — one slow graph round trip "
        f"freezes every concurrent request (the /v1/search precedent "
        f"off-loads it; #3718)")
    assert response.status_code == 200, (
        f"[{label}] the off-loaded read did not return a real response "
        f"({response.status_code}: {response.text[:200]})")


def test_read_route_does_not_freeze_the_event_loop(client, monkeypatch):
    """#3718 residual 2: a blocked graph read must not stall the loop.

    The loop-responsiveness half of the invariant, on one representative read
    (``GET /v1/points`` — the endpoint the issue's measured reproduction used).
    Two assertions that fail on the inline shape:

    * ``ticks_in_stall >= MIN_TICKS_IN_STALL`` — the loop kept scheduling while
      the read was blocked (inline yields 0: the ticker's next wake-up cannot
      run until the freeze releases).
    * ``/health`` answered INSIDE the blocking window — the user-visible
      consequence: a concurrent client is served DURING the read, not queued
      behind it. /health is pure in-memory (#2850), so a delay in it can only
      come from the loop being blocked, never from a saturated executor.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    _seed_read_surface()
    state: dict = {}
    _RequestScopedProbe(monkeypatch, ha_mod, state, block=True)
    request_kwargs = {"method": "GET", "url": "/v1/points"}

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
            stop["done"] = True
            await tick
        return ticks, health, health_done, response, read_in_flight

    ticks, health, health_done, response, read_in_flight = asyncio.run(_drive())

    assert state.get("ran_on_loop") is False, (
        "list_points ran its synchronous FalkorDB read ON the event loop "
        "(#3718)")

    entered, exited = state["entered"], state["exited"]
    in_stall = [t for t in ticks if entered < t < exited]
    assert len(in_stall) >= MIN_TICKS_IN_STALL, (
        f"the event loop completed only {len(in_stall)} tick(s) during a "
        f"{STALL_S:.1f}s blocked read (need {MIN_TICKS_IN_STALL}) — the read "
        f"is blocking the event loop, so every concurrent request waits for "
        f"the graph round trip (#3718)")

    assert health.json()["status"] in {"ok", "degraded"}, health.text
    assert health_done < exited, (
        "the /health probe was NOT served while the read was blocked "
        "(answered %.0fms AFTER the block released) — a concurrent request "
        "queued behind the graph read instead of being served (#3718)"
        % ((health_done - exited) * 1000))
    assert read_in_flight, (
        "the read had already returned before /health was issued, so this run "
        "proves nothing about a request served DURING the blocking window "
        "(#3718)")
    assert response.status_code == 200, response.text[:200]


# ── AST inventory pin: the sync-FalkorDB-on-the-loop class ─────────────────
#
# The #2988 pin in `test_health_ready_nonblocking.py` matches one handler and
# one attribute (`attr == "query"`); the #3498 pin is name-based but covers the
# declared CONTROL-PLANE seam helpers. Neither sees the graph seam this change
# is about: `sdk._get_proj()` / `sdk._get_registry()` and any call on the graph
# handle they return (`proj.g.query(...)`, `reg.query(...)`). This pin is
# NAME-BASED over EVERY async body, so a new route (or helper) that runs sync
# FalkorDB I/O inline fails here — which is the way this defect regrew three
# times (#2988, #3035, #3086) before anyone noticed.
#
# BOUNDARY, stated rather than implied: the scanned names are the DECLARED
# FalkorDB seams — `_get_proj`, `_get_registry`, `dream_health_check`, and any
# `.g.query` on a graph handle. A blocking call reached through some sync helper
# (e.g. `_graph_has_org_namespace` -> `_registry_existing_graphs().list_graphs()`,
# or the onboarding writers) is NOT visible here — the scan walks async bodies
# and a sync helper's own graph I/O is one level down. Those helper-mediated
# sites are part of the declared residual below. Calls inside an offload
# boundary (`asyncio.to_thread`, `run_in_executor`, `_run_off_loop`,
# `_run_with_close`, `_run_dream_on_pool`, `_cp_offload`, ...) are not on the
# loop and are skipped — including a callable REFERENCE argument at any
# position (the callable is arg 0 for `to_thread`, arg 1 for `run_in_executor`).
# A locally defined function is scanned only when it is INVOKED on the loop;
# one handed to a boundary as a reference (possibly wrapped in
# `functools.partial`) runs in the worker and is skipped.
#
# #4660 — a SECOND class of scanned name. The construction seams
# (`_make_sdk` / `_data_sdk`) are scanned by the same walk but RECORDED
# separately (`_inline_seam_hits` returns two lists), because "this body builds
# an SDK on the loop" and "this body runs a graph read/attach on the loop" are
# different defects with different fixes. What the widened scan still does NOT
# see, stated rather than implied: SDK construction reached through a sync
# wrapper (`_open_org_graph_sdk`, `_registry_sdk` — both construct via
# `_make_sdk` one level down), which is the same helper-mediated boundary the
# paragraph above names, and which the behavioural probe extension in
# `tests/test_dataplane_lane_loop_responsiveness.py` (#4660) is the guard for.
_GRAPH_SEAM_CALLEES = frozenset({"_get_proj", "_get_registry", "dream_health_check"})
#: SDK CONSTRUCTION seams (#4660) — kept SEPARATE from the graph seams above so
#: the two classes of violation stay distinguishable in the failure message
#: (``test_graph_io_is_offloaded``) instead of merging into one count that hides
#: which of the two regressed.
#:
#: Building an SDK is NOT free, and it is not free of the loop: ``_make_sdk``
#: (which ``_data_sdk`` wraps) runs in embedded mode — no ``TORTOISE_DB_URI``,
#: the fly.toml default —
#:
#: * ``_anchor_usable(anchor, db_path)``, a PROBE QUERY against the keepalive
#:   anchor, and
#: * on an anchor miss, ``with _KEEPALIVE_LOCK: ... anchor = TortoiseSDK(...)``
#:   plus its eager ``_get_proj()`` — an eager connect that HOLDS the socket.
#:
#: Both are blocking FalkorDB socket I/O on whatever thread calls them. This is
#: the gap that let #4645's round-1 P1 through: four of the twelve bodies the PR
#: claimed to have off-loaded still built their SDK on the loop, and BOTH guards
#: passed because this seam set did not exist and the behavioural probe only
#: observed ``_get_proj``. It cost a reviewer reading the module to catch it.
_SDK_CONSTRUCTION_SEAMS = frozenset({"_make_sdk", "_data_sdk"})
#: Calls whose callable argument runs in a WORKER. ``_graph_offload`` was the
#: missing member (#4660): it is the #3498 bounded graph-pool seam
#: (``_graph_offload`` -> ``_cp_offload(pool="graph")``) and is how
#: ``_data_sdk_offloaded`` already runs ``_data_sdk`` off the loop — but it was
#: not declared here, so `_data_sdk_offloaded`'s own (correct, off-loop)
#: ``_data_sdk`` call was reported as an on-loop construction. A boundary that
#: is not declared reports the CORRECT shape as a violation, which is how a
#: real finding gets dismissed as noise.
_OFFLOAD_BOUNDARY_CALLEES = frozenset({
    "to_thread", "_run_off_loop", "_submit_off_loop", "_run_dream_on_pool",
    "_run_with_close", "run_in_executor",
    "_cp_offload", "_oauth_offload", "run_control_plane_call",
    "run_on_daemon_worker", "_graph_offload",
})

#: Async bodies this change OFF-LOADS. A subset assertion: a rename or a
#: refactor that drops one back onto the loop fails `test_graph_io_is_offloaded`.
#: NOTE: the assertion is about the SEAMS THE SCAN SEES. ``patch_onboarding_state``
#: has them off-loaded — the ``_node_sdk._get_proj()`` read inside the nested
#: ``_read_node`` closure (reached because the closure is INVOKED on the loop)
#: and the ``_graph_has_org_namespace`` probe handed to ``to_thread`` by
#: reference — but its later onboarding WRITES still run through sync helpers
#: the scan cannot see; those are part of the residual, not covered by this set.
#: ⚠️ The same caveat is why ``_run_indexing`` is NOT here: most of its
#: graph work (``indexer.index_repo``'s projection walk, the ``_make_sdk``
#: construction and the ``_update_onboarding_state`` writes) is one level down
#: in another module or in sync helpers, so membership here was a VACUOUS pass
#: (round-4 review). Its brief worker hand-offs were reverted too: from a
#: background task they resolved the embedded per-test DB path from a reused
#: pool thread and wrote to the wrong store (see the ``_run_indexing``
#: docstring). It is declared in ``_KNOWN_INLINE_HELPER_RESIDUAL`` under #4709
#: instead — that declaration IS what its visible seam requires: the guard
#: computes ``inline - declared``, so membership (not the seam) keeps it green,
#: and dropping the name REDS it.
#: Async bodies whose sync FalkorDB I/O is off-loaded with NO residuals the
#: scan can see — neither a graph seam NOR an SDK construction on the loop.
#:
#: ⚠️ ``list_points``, ``get_point``, ``org_info``, ``list_sessions``,
#: ``get_session_detail``, ``dream_health``, ``list_api_keys``, ``list_members``,
#: ``list_pending_invites_for_me``, ``patch_onboarding_state`` and
#: ``invite_info`` were members until #4660 widened the scan: their graph READS
#: are off-loaded, but their ``_data_sdk`` / ``_make_sdk(namespace="registry")``
#: CONSTRUCTION still runs on the loop. They now live in
#: ``_OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION`` below — a decided,
#: visible residual rather than a vacuous declaration.
_OFFLOADED_ASYNC_BODIES = frozenset({
    # #3718 residual 3 — the DATA-plane seam outside the REST-converted
    # bodies. These were ``_KNOWN_INLINE_*_RESIDUAL`` until this change
    # off-loaded their projection attach / registry attach / graph round
    # trips. ``commit_session`` and ``delete_session`` each had a whole
    # no-`await` SYNC sequence and move as ONE worker hand-off, so no
    # interleaving point is added; the other ten off-load each sync call at
    # its own site (so they DO gain await points), and `public_demo` — the one
    # whose check-then-act had to stay atomic — is a single hand-off too.
    # Behavioural coverage for the lane lives in
    # ``tests/test_dataplane_lane_loop_responsiveness.py``.
    "_lifespan", "public_demo",
    "commit_session", "delete_session",
    "backups_create", "backups_restore", "backups_sweep",
    "backups_purge", "backups_rebaseline", "backups_drill",
    "backups_drill_scheduled",
})

#: Async bodies whose GRAPH I/O is off-loaded but whose SDK CONSTRUCTION is
#: still on the loop (#4660). Declared, visible residual — NOT a claim that
#: these bodies are clean.
#:
#: Why they are not simply off-loaded here: off-loading the construction of a
#: handled read is a behavioural change on the hottest read path (11 routes plus
#: the hot onboarding PATCH), mixed into what is otherwise a pure guard/inventory
#: change — exactly the mixing #4660 was deferred out of #4645 to avoid. The
#: burn-down runs under #3718 (kept open via #2924); ``_data_sdk_offloaded``
#: (#3773) is the twin the data-plane ones would move onto.
#:
#: Two assertions hold for the membership, so this set can only shrink by
#: actually off-loading the construction:
#: * every member is FLAGGED by the widened scan — dropping a name without
#:   converting the body REDS ``test_graph_io_is_offloaded``;
#: * every member's GRAPH-seam hits are empty — re-inlining a read/attach in one
#:   of these bodies is still a failure, not an upgrade to "construction-only".
_OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION = frozenset({
    "list_points", "get_point", "org_info", "list_sessions",
    "get_session_detail", "dream_health",
    "list_api_keys", "list_members", "list_pending_invites_for_me",
    "patch_onboarding_state",
    # The public invite-info read: its READS are off-loaded — the registry legs
    # via ``asyncio.to_thread`` (the sibling registry reads' house style) and the
    # Supabase control-plane legs via ``_cp_offload`` — but its TWO
    # ``_make_sdk(namespace="registry")`` attaches are built on the loop.
    "invite_info",
})

#: FastAPI route handlers STILL running sync FalkorDB I/O inline. Declared,
#: reviewed residual — burn down under #3718 (kept open via #2924). A NAME
#: leaving this set without the handler being off-loaded fails
#: `test_graph_io_is_offloaded`; a NEW inline route fails the same assertion.
_KNOWN_INLINE_ROUTE_RESIDUAL = frozenset({
    "provision_tenant", "register_user", "create_api_key", "revoke_api_key",
    "toggle_api_key_enabled", "delete_graph", "invite_to_org", "accept_invite",
    "invite_otp", "resend_invite", "expire_invite", "decline_invite",
    "remove_member", "change_member_role", "import_org", "reconcile",
    "agent_signup", "session_key", "github_callback", "webhooks_stripe",
    # #4355: the replacement-aware rotate route. Same declared residual as its
    # siblings create_api_key / revoke_api_key — its READS are off-loaded
    # (_rotatable_key_row, api_key_occupies_slot, _claim_key_revocation via
    # asyncio.to_thread) but _mint_key is inline exactly as create_api_key's
    # is, so the mint stays atomic under the same all-sync critical section.
    "rotate_api_key",
    # #4660: the routes the WIDENED scan (SDK-construction seams) newly sees.
    # Their graph I/O was already declared residual by the graph-seam scan —
    # what is new is that their INLINE ``_make_sdk`` / ``_data_sdk`` is now
    # visible and named here rather than passing silently. Same burn-down
    # (#3718 via #2924); the inventory is posted on #3718.
    "accept_invite_by_id", "agent_token_revoke", "create_graph", "create_org",
    "create_org_graph", "issue_insight", "list_graphs", "list_invites",
    "list_my_orgs", "list_packs", "list_trash", "rescind_invite", "search",
    "session_context", "topic_summary", "upload_pack_manifest",
    "volunteer_context",
})

#: Non-route async bodies with inline sync FalkorDB I/O — the per-request auth
#: dependency `get_current_org` (6 sites, the single highest-traffic one), the
#: membership/owner gates, the capture path (`_capture_session_impl`) and the
#: registry/backup helpers. `_lifespan` was a member until #3718 residual 3
#: off-loaded its `_control_plane_source` resolve.
#:
#: `_run_indexing` is here (not in `_OFFLOADED_ASYNC_BODIES`) because most of
#: its graph work is helper-mediated and therefore INVISIBLE to the scan:
#: `indexer.index_repo` (a method in another module — its own `_get_proj()`
#: attach + a synchronous per-item projection loop), the `_make_sdk`
#: construction, the `_relink_sessions_after_index` relink pass and two
#: `_update_onboarding_state` writes. One seam IS visible — the
#: `org_sdk._get_proj()` passed to `backfill_legacy_closed` — and membership
#: here is what keeps that hit declared: the guard computes
#: `inline - declared`, so removing this name REDS it. Round-4 review found
#: the name was a vacuous off-load declaration; it is tracked under #4709.
#: The three calls it briefly off-loaded (`_make_sdk`,
#: `backfill_legacy_closed`, `_relink_sessions_after_index`) were REVERTED to
#: inline — from a background task they resolved the embedded per-test DB
#: path from a reused pool thread and wrote to the wrong store (see the
#: `_run_indexing` docstring).
#:
#: Same declared residual; same burn-down. Scanned rather than ignored because a
#: dependency body is still ON the loop.
_KNOWN_INLINE_HELPER_RESIDUAL = frozenset({
    "get_current_org", "_capture_session_impl", "_user_memberships",
    "_membership_org", "_org_node", "_count_active_free_memberships",
    "_owned_free_org_ids", "_create_org_registry_lane",
    "_apply_graph_recording_override", "_apply_graph_rename", "_graph_row_probe",
    "_rollback_restore_name_race", "_trash_name_conflict",
    "_require_owner_admin", "_require_owner", "_registry_mismatch_accept_v2",
    "_registry_accept_by_id", "_quarantine_import",
    "_run_indexing",
    # #4660: the helper bodies the WIDENED scan newly sees. Same class as the
    # route block above — inline SDK construction that was invisible to the
    # graph-seam set.
    #
    # ``_run_docs_indexing`` is the concrete instance #4660's thread called out:
    # it keeps an inline ``_make_sdk(namespace=org_id)`` on the loop (two sites)
    # and surfaced only because a round-2 reviewer followed ``_run_indexing``'s
    # sync helper by hand. ``_accept_mismatch_v2`` / ``_create_onboarding_org_lane``
    # / ``_graph_quota_gate`` / ``_restore_trash_graph_locked`` /
    # ``_agent_recover_flow`` are the same shape.
    "_accept_mismatch_v2", "_agent_recover_flow",
    "_create_onboarding_org_lane", "_graph_quota_gate",
    "_restore_trash_graph_locked", "_run_docs_indexing",
    # #4355: the mint's #528 analytics actor resolution, extracted verbatim out
    # of create_api_key so the rotate route shares it (one implementation). It
    # was inline-on-the-loop before the extraction and still is — same residual,
    # now named.
    "_key_created_analytics",
})


def _graph_bound_names(node: ast.AST) -> set[str]:
    """Names bound to a graph handle — when the assigned expression uses a
    ``_get_proj()`` / ``_get_registry()`` / ``dream_health_check`` seam either as
    a CALL (``proj = sdk._get_proj()``) or as a bare ATTRIBUTE REFERENCE
    (``proj = await asyncio.to_thread(sdk._get_proj)``, the shape
    ``get_session_detail`` uses, where the seam is the callable argument and has
    no call parens of its own). The first revision matched only the Call form,
    so the offload-wrapped handle was invisible to the ``base.id in bound`` rule
    and a later inline ``proj.query(...)`` (no ``.g``) could not be flagged
    (#3718 review, reproduced with a mutation).
    """
    names: set[str] = set()
    for n in ast.walk(node):
        if not isinstance(n, ast.Assign):
            continue
        if any(
            (isinstance(sub, ast.Call)
             and _callee_name(sub.func) in _GRAPH_SEAM_CALLEES)
            or (isinstance(sub, ast.Attribute)
                and sub.attr in _GRAPH_SEAM_CALLEES)
            for sub in ast.walk(n.value)
        ):
            for target in n.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _inline_seam_hits(node: ast.AsyncFunctionDef) -> tuple[list[int], list[int]]:
    """``(graph-seam lines, SDK-construction lines)`` NOT inside an offload boundary.

    Two lists, not one (#4660): an SDK built on the loop is a different defect
    from a graph read run on the loop, and a caller that merged them could not
    tell which class a failure was, nor report the counts separately.
    """
    bound = _graph_bound_names(node)
    hits: list[int] = []
    construction: list[int] = []

    # Nested (locally defined) functions. A nested def's body is ON the loop
    # only when it is INVOKED there; one that is merely handed to an offload
    # boundary as a callable REFERENCE runs in the worker and is not scanned.
    # The earlier revision skipped EVERY nested def in both loops, which made
    # this guard vacuous for any handler whose seams sit inside a closure:
    # `patch_onboarding_state`'s `_read_node` is the one that mattered — reverting
    # its offload to a bare `_read_node()` left this function returning []
    # (#4455 review P1, mutation-reproduced).
    nested_names = {
        sub.name
        for sub in ast.walk(node)
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
        and sub is not node
    }
    on_loop_nested = _nested_defs_invoked_on_loop(
        node, nested_names, _OFFLOAD_BOUNDARY_CALLEES)

    def visit(current: ast.AST) -> None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef)):
            if getattr(current, "name", None) in on_loop_nested:
                for stmt in current.body:
                    visit(stmt)
            return
        if isinstance(current, ast.Call):
            if _callee_name(current.func) in _OFFLOAD_BOUNDARY_CALLEES:
                for child in _offload_boundary_eager_children(current):
                    visit(child)
                return
            name = _callee_name(current.func)
            if name in _SDK_CONSTRUCTION_SEAMS:
                construction.append(current.lineno)
            elif name in _GRAPH_SEAM_CALLEES:
                hits.append(current.lineno)
            elif isinstance(current.func, ast.Attribute):
                base = current.func.value
                if (name == "query" and isinstance(base, ast.Attribute)
                        and base.attr == "g") or (isinstance(base, ast.Name) and base.id in bound):
                    hits.append(current.lineno)
        for child in ast.iter_child_nodes(current):
            visit(child)

    for stmt in getattr(node, "body", []):
        visit(stmt)
    return hits, construction


def _has_inline_graph_io(node: ast.AsyncFunctionDef) -> list[int]:
    """Line numbers of sync-FalkorDB seams NOT inside an offload boundary.

    BOTH classes (graph seams and SDK construction) merged, ascending — the
    shape the mutation tests assert. Callers that must tell the classes apart
    use :func:`_inline_seam_hits`.
    """
    hits, construction = _inline_seam_hits(node)
    return sorted(hits + construction)


def _inline_construction_lines() -> dict[str, list[int]]:
    """Body name -> on-loop SDK-construction lines only (#4660)."""
    tree = ast.parse(HOSTED_API.read_text())
    out: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            _hits, construction = _inline_seam_hits(node)
            if construction:
                out[node.name] = construction
    return out


def _inline_graph_io_bodies() -> dict[str, list[int]]:
    """Body name -> ALL on-loop seam lines (both classes, ascending)."""
    tree = ast.parse(HOSTED_API.read_text())
    return {
        node.name: lines
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and (lines := _has_inline_graph_io(node))
    }


def _inline_graph_seam_lines() -> dict[str, list[int]]:
    """Body name -> on-loop GRAPH-seam lines only (class 1 of two, #4660)."""
    tree = ast.parse(HOSTED_API.read_text())
    out: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef):
            hits, _construction = _inline_seam_hits(node)
            if hits:
                out[node.name] = hits
    return out


def test_graph_handle_binding_rule_detects_offload_wrapped_binding():
    """The offload-wrapped handle binding must be recorded, so a later INLINE
    call on it that is not spelled ``.g.query`` (e.g. ``proj.query(...)`` or
    ``proj.db.list_graphs()``) is still flagged — the round-1 revision matched
    only the Call form and missed it (#3718 review, mutation-reproduced).
    """
    node = ast.parse(
        "async def _probe():\n"
        "    proj = await asyncio.to_thread(sdk._get_proj)\n"
        "    _rows = proj.query('MATCH (n) RETURN n')\n"
    ).body[0]
    assert _graph_bound_names(node) == {"proj"}, (
        "the offload-wrapped `_get_proj` binding was not recorded as a graph "
        "handle — a later inline call on it would be invisible (#3718 review)")
    assert _has_inline_graph_io(node) == [3], (
        "an inline `proj.query(...)` on an offload-wrapped binding was not "
        "flagged (#3718 review)")

    # The direct and lambda-wrapped forms must keep working.
    direct = ast.parse(
        "async def _probe():\n"
        "    proj = sdk._get_proj()\n"
        "    _rows = proj.query('MATCH (n) RETURN n')\n"
    ).body[0]
    assert _graph_bound_names(direct) == {"proj"}
    # The direct form ALSO has the seam call itself inline (line 2); the
    # offload forms have it inside a boundary, so only the read is flagged.
    assert _has_inline_graph_io(direct) == [2, 3]
    wrapped = ast.parse(
        "async def _probe():\n"
        "    proj = await asyncio.to_thread(lambda: sdk._get_proj())\n"
        "    _rows = proj.g.query('MATCH (n) RETURN n')\n"
    ).body[0]
    assert _graph_bound_names(wrapped) == {"proj"}
    assert _has_inline_graph_io(wrapped) == [3]


def test_nested_closure_invocation_rule():
    """A nested def is scanned only when it is INVOKED on the loop.

    The round-2 revision scans a locally defined function's body when it is
    called bare (``_read()`` — on the loop) and skips it when it is only
    handed to an offload boundary as a callable REFERENCE
    (``asyncio.to_thread(_read)``, ``run_in_executor(pool, _read)``, or a
    ``functools.partial`` wrapping) — which runs in the worker. Before this,
    ``patch_onboarding_state`` returned ``[]`` either way, so its off-load
    assertion was vacuous (#4455 review P1, mutation-reproduced).
    """
    invoked = ast.parse(
        "async def _probe():\n"
        "    def _read():\n"
        "        return sdk._get_proj()\n"
        "    node = _read()\n"
    ).body[0]
    assert _has_inline_graph_io(invoked) == [3], (
        "a nested def invoked bare on the loop was not scanned (#4455 review)")

    offloaded = ast.parse(
        "async def _probe():\n"
        "    def _read():\n"
        "        return sdk._get_proj()\n"
        "    node = await asyncio.to_thread(_read)\n"
    ).body[0]
    assert _has_inline_graph_io(offloaded) == [], (
        "a nested def handed to an offload boundary as a reference was scanned "
        "— the off-load assertion would be wrong (#4455 review)")

    executor = ast.parse(
        "async def _probe():\n"
        "    def _read():\n"
        "        return sdk._get_proj()\n"
        "    node = await loop.run_in_executor(pool, _read)\n"
    ).body[0]
    assert _has_inline_graph_io(executor) == [], (
        "a nested def passed at arg 1 of run_in_executor was scanned as inline "
        "(#4455 review)")

    partial = ast.parse(
        "async def _probe():\n"
        "    def _read():\n"
        "        return sdk._get_proj()\n"
        "    node = await _run_with_close(\n"
        "        _DREAM_EXECUTOR, functools.partial(ctx.run, _read), timeout=1)\n"
    ).body[0]
    assert _has_inline_graph_io(partial) == [], (
        "a nested def wrapped in functools.partial for an offload boundary was "
        "scanned as inline (#4455 review)")

    seam_free = ast.parse(
        "async def _probe():\n"
        "    def _clean():\n"
        "        return 1\n"
        "    return _clean()\n"
    ).body[0]
    assert _has_inline_graph_io(seam_free) == [], (
        "a seam-free nested def invoked on the loop produced a false positive "
        "(#4455 review)")


def test_sdk_construction_seam_is_scanned_and_reported_separately():
    """#4660: an SDK built on the loop is flagged, as its OWN class.

    The pre-#4660 seam set was graph-only, so ``_make_sdk`` / ``_data_sdk`` —
    which is where the embedded-mode anchor probe and eager connect live — were
    INVISIBLE: a body could build its SDK on the loop and both guards stayed
    green (the #4645 round-1 P1). The class must also stay distinguishable from
    the graph-seam class, so a failure says which one regressed.
    """
    inline = ast.parse(
        "async def _probe():\n"
        "    sdk = _data_sdk(org)\n"
        "    return sdk\n"
    ).body[0]
    graph_lines, construction_lines = _inline_seam_hits(inline)
    assert construction_lines == [2], (
        "an inline `_data_sdk(...)` was not reported as an SDK-construction "
        "violation — the #4660 seam set is not wired in")
    assert graph_lines == [], (
        "an inline `_data_sdk(...)` was mis-classified as a GRAPH-seam hit; "
        "the two classes must stay apart so the failure message says which "
        "one regressed (#4660)")
    assert _has_inline_graph_io(inline) == [2], (
        "the merged view must still report the construction site, or every "
        "existing caller silently loses the new class (#4660)")

    # Off-loaded construction is NOT a violation — the `_graph_offload`
    # boundary was the missing member that made `_data_sdk_offloaded` itself
    # report as an on-loop construction (#4660).
    offloaded = ast.parse(
        "async def _probe():\n"
        "    return await _graph_offload(lambda: _data_sdk(org), op='data_sdk')\n"
    ).body[0]
    assert _inline_seam_hits(offloaded) == ([], []), (
        "`_graph_offload` is not declared as an offload boundary — the "
        "CORRECT off-loop shape (`_data_sdk_offloaded`) reports as a "
        "violation, and a real finding gets dismissed as noise (#4660)")

    # A construction seam must not be treated as a graph HANDLE binding either:
    # `sdk = _make_sdk(...)` does not make `sdk.query(...)` a graph-seam hit.
    handle = ast.parse(
        "async def _probe():\n"
        "    sdk = _make_sdk(namespace='registry')\n"
        "    return sdk.query('MATCH (n) RETURN n')\n"
    ).body[0]
    assert _inline_seam_hits(handle) == ([], [2]), (
        "the construction seam leaked into the graph-handle binding rule "
        "(#4660: only `_get_proj` / `_get_registry` / `dream_health_check` "
        "bind a graph handle)")


def test_async_body_inventory_is_visible():
    """The scan must be seeing the surface it claims to guard."""
    bodies = [n for n in ast.walk(ast.parse(HOSTED_API.read_text()))
              if isinstance(n, ast.AsyncFunctionDef)]
    assert len(bodies) > 150, (
        f"only {len(bodies)} async bodies parsed — the scan is not seeing the "
        "hosted surface it is supposed to guard")


def test_graph_io_is_offloaded():
    """Every async body with inline sync FalkorDB I/O is either OFF-LOADED or
    an explicitly declared residual — and a declared residual can only shrink
    by actually converting the body.

    The two CLASSES are reported separately (#4660): graph seams
    (``_get_proj`` / ``_get_registry`` / ``dream_health_check`` / ``.g.query``)
    and SDK construction (``_make_sdk`` / ``_data_sdk``). A merged count would
    say how many sites are wrong but not which of the two regressed.
    """
    inline = _inline_graph_io_bodies()
    construction = _inline_construction_lines()
    graph_inline = _inline_graph_seam_lines()
    declared = (_KNOWN_INLINE_ROUTE_RESIDUAL | _KNOWN_INLINE_HELPER_RESIDUAL
                | _OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION)

    # Class 1 — the graph seams. A member of EITHER off-loaded set may not
    # carry one: the graph off-load is the part those bodies DO have, so a
    # graph-seam hit here is a regression for both sets (#4660).
    still_inline = sorted(
        (_OFFLOADED_ASYNC_BODIES
         | _OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION) & set(graph_inline))
    assert not still_inline, (
        f"{still_inline} are declared OFF-LOADED but still run sync FalkorDB "
        f"GRAPH i/o inline at {[(n, graph_inline[n]) for n in still_inline]} — "
        f"the read/attach off-load regressed (#3718)")

    # Class 2 — SDK construction. Only the fully off-loaded set must be free
    # of it; the construction-residual set is exactly the declared exception.
    construction_regressed = sorted(
        _OFFLOADED_ASYNC_BODIES & set(construction))
    assert not construction_regressed, (
        f"{construction_regressed} are declared fully OFF-LOADED but build an "
        f"SDK on the event loop at "
        f"{[(n, construction[n]) for n in construction_regressed]} — in "
        f"embedded mode `_make_sdk` probes the anchor and may eagerly connect, "
        f"which is blocking FalkorDB socket I/O (#4660). Off-load it (the "
        f"`_graph_offload` / `_data_sdk_offloaded` seam) or move the body to "
        f"`_OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION`")

    # The construction-residual set can only shrink by actually off-loading: a
    # name whose body no longer builds an SDK on the loop is stale and must come
    # OUT, the same way the fully-off-loaded set is asserted above.
    stale = sorted(_OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION
                   - set(construction))
    assert not stale, (
        f"{stale} are declared as ON-LOOP SDK-construction residual but no "
        f"longer construct an SDK on the loop — remove them from "
        f"`_OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION` (and from "
        f"this residual entirely if their graph seams are clean too) (#4660)")

    undeclared = sorted(set(inline) - declared)
    assert not undeclared, (
        f"async body/bodies {[(n, inline[n]) for n in undeclared]} run "
        f"synchronous FalkorDB I/O (a graph seam or an SDK construction) on "
        f"the event loop and are NOT in the declared residual — off-load them "
        f"with `await asyncio.to_thread(...)` (short calls), the dedicated "
        f"pool (long/stallable work) or `_graph_offload` (SDK construction), or "
        f"add them to the residual with a tracking note (#3718, #4660)")


def test_on_loop_sdk_construction_residual_is_named_head_on():
    """#4660: the class the pin was blind to is now DECLARED, not silent.

    The failure this guards is not "a site exists" — the sites are the tracked
    residual — it is that a future refactor could stop seeing the class
    entirely (empty seam set, boundary swallowing it) and every assertion above
    would go vacuously green.
    """
    assert frozenset({"_make_sdk", "_data_sdk"}) == _SDK_CONSTRUCTION_SEAMS, (
        "the #4660 construction seam set changed shape — re-derive the "
        "inventory before trusting this pin")
    assert "_graph_offload" in _OFFLOAD_BOUNDARY_CALLEES, (
        "`_graph_offload` is no longer declared an offload boundary — "
        "`_data_sdk_offloaded` will report as an on-loop construction and bury "
        "the real findings (#4660)")
    construction = _inline_construction_lines()
    assert len(construction) >= 30, (
        f"the construction scan now sees only {len(construction)} bodies — it "
        f"is not seeing the surface it claims to guard (#4660)")
    assert set(_OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION) <= set(construction), (
        "a declared on-loop SDK-construction body is not flagged by the scan "
        "at all — the declaration has gone vacuous (#4660)")


def test_residual_names_still_exist():
    """A rename or deletion must fail HERE, not silently vacate the pin."""
    tree = ast.parse(HOSTED_API.read_text())
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    declared = (_OFFLOADED_ASYNC_BODIES
                | _OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION
                | _KNOWN_INLINE_ROUTE_RESIDUAL
                | _KNOWN_INLINE_HELPER_RESIDUAL)
    ghosts = sorted(declared - defined)
    assert not ghosts, (
        f"{ghosts} are declared residual but no longer defined in "
        f"hosted_api.py — the residual list has rotted (#3718)")


# ── #4625 leg 62: the capture read leg, narrowed out of the residual ────────
#
# ``_capture_session_impl`` is a declared residual BODY
# (``_KNOWN_INLINE_HELPER_RESIDUAL``), so ``test_graph_io_is_offloaded`` cannot
# see a FIXED leg regress inside it — that allowlist is exactly why the capture
# read legs could regrow with this guard green (#4625 work order §3). The pins
# below are per-CALLEE, so they can. Leg 12 (the control-plane/onboarding
# helper) is pinned the same way in ``test_health_ready_nonblocking.py``.


def _async_body_named(name: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(HOSTED_API.read_text())
    return next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef) and n.name == name)


def _inline_calls_to(node: ast.AsyncFunctionDef,
                     names: frozenset[str]) -> list[str]:
    """Callee names from ``names`` called in ``node`` NOT behind an offload.

    Same boundary semantics as ``_has_inline_graph_io``: a nested def handed to
    an offload boundary as a callable reference runs in the worker and is
    skipped; a nested def INVOKED on the loop is scanned.
    """
    nested_names = {
        sub.name for sub in ast.walk(node)
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
        and sub is not node
    }
    on_loop_nested = _nested_defs_invoked_on_loop(
        node, nested_names, _OFFLOAD_BOUNDARY_CALLEES)
    found: list[str] = []

    def visit(current: ast.AST) -> None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef)):
            if getattr(current, "name", None) in on_loop_nested:
                for stmt in current.body:
                    visit(stmt)
            return
        if isinstance(current, ast.Call):
            if _callee_name(current.func) in _OFFLOAD_BOUNDARY_CALLEES:
                for child in _offload_boundary_eager_children(current):
                    visit(child)
                return
            name = _callee_name(current.func)
            if name in names:
                found.append(name)
        for child in ast.iter_child_nodes(current):
            visit(child)

    for stmt in getattr(node, "body", []):
        visit(stmt)
    return found


def test_capture_session_leg62_probe_is_offloaded():
    """#4625 leg 62: the capture's graph attach must not run inline.

    * ``_get_proj`` — the projection attach must be gone from the loop (it now
      rides the off-loop wrapper);
    * ``_capture_session_probe_off_loop`` must be the one caller doing it;
    * the wrapper's OWN SDK open must ride ``_data_sdk_offloaded`` (review F2:
      reverting it to ``_data_sdk`` used to leave every test green — the
      behavioural probe records only ``_get_proj``, and this pin scanned only
      ``_capture_session_impl``);
    * exactly ONE inline ``_data_sdk`` may remain: the leg-72
      (``count_org_usage``) site, which is a DECLARED residual because the
      required #4282 collision pre-flight returned COLLISION, so that leg is
      skipped in this unit.
    """
    node = _async_body_named("_capture_session_impl")
    callees = _inline_calls_to(node, frozenset({
        "_data_sdk", "_get_proj", "_capture_session_probe_off_loop",
    }))
    assert callees.count("_capture_session_probe_off_loop") == 1, (
        "#4625 leg 62: `_capture_session_impl` must open the SDK/projection "
        "through `_capture_session_probe_off_loop`"
    )
    assert callees.count("_get_proj") == 0, (
        "#4625 leg 62 regressed: `_capture_session_impl` attaches the "
        "projection inline — it must ride the off-loop wrapper"
    )
    assert callees.count("_data_sdk") == 1, (
        "#4625: `_capture_session_impl` has "
        f"{callees.count('_data_sdk')} inline `_data_sdk` call(s); the leg-62 "
        "open must use `_data_sdk_offloaded` and the ONE remaining site is the "
        "declared leg-72 residual (count_org_usage)"
    )

    probe = _async_body_named("_capture_session_probe_off_loop")
    probe_callees = _inline_calls_to(
        probe, frozenset({"_data_sdk", "_data_sdk_offloaded"}))
    assert probe_callees.count("_data_sdk_offloaded") == 1, (
        "#4625 leg 62: `_capture_session_probe_off_loop` must open the SDK "
        "through `_data_sdk_offloaded` — its tenancy resolver (``_make_sdk`` "
        "+ the ownership query) blocks the loop otherwise"
    )
    assert probe_callees.count("_data_sdk") == 0, (
        "#4625 leg 62 regressed: `_capture_session_probe_off_loop` opens the "
        "SDK inline with `_data_sdk` instead of the off-loop twin"
    )


def test_capture_session_probe_attaches_off_the_loop(monkeypatch):
    """#4625 leg 62: the wrapper's attach runs off the event loop.

    The direct mechanism check — ``_get_proj``'s own view of the loop (a worker
    thread has no running loop). Every ``_get_proj`` in this flow must be
    off-loop: the SDK-open hand-off AND the probe hand-off. Reverting either
    puts one back on ``MainThread`` and fails here.
    """
    import tortoise.hosted_api as ha_mod

    seen: list[str] = []
    real_get_proj = TortoiseSDK._get_proj

    def _probe(sdk_self, *args, **kwargs):
        seen.append(threading.current_thread().name)
        return real_get_proj(sdk_self, *args, **kwargs)

    monkeypatch.setattr(TortoiseSDK, "_get_proj", _probe)

    sdk, _proj, row = asyncio.run(
        ha_mod._capture_session_probe_off_loop(
            {"org_id": TEST_ORG_ID}, "read-loop-4625-session"))
    try:
        assert seen, (
            "the projection attach was never reached — this run proves nothing "
            "(#4625 leg 62)"
        )
        assert all(name != "MainThread" for name in seen), (
            f"the capture probe attached the projection on the event loop: "
            f"{seen} — one slow attach freezes every concurrent request "
            f"(#4625 leg 62)"
        )
        assert row is not None
    finally:
        sdk.close()
