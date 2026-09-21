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
onboarding PATCH's read (``patch_onboarding_state``). EVERY OTHER async body in
the module that still runs sync FalkorDB I/O inline is enumerated in
``_KNOWN_INLINE_ROUTE_RESIDUAL`` / ``_KNOWN_INLINE_HELPER_RESIDUAL`` below —
that is the declared, non-silent residual (it includes the per-request
``get_current_org`` auth dependency and ``_capture_session_impl``). The AST pin
fails on a NEW inline site, which is the "the class cannot silently return"
guard; the residual is burned down under #3718 (kept open via #2924).

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


def _seed_read_surface():
    """Seed one Point + one Session and WARM the embedded anchors.

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
_GRAPH_SEAM_CALLEES = frozenset({"_get_proj", "_get_registry", "dream_health_check"})
_OFFLOAD_BOUNDARY_CALLEES = frozenset({
    "to_thread", "_run_off_loop", "_submit_off_loop", "_run_dream_on_pool",
    "_run_with_close", "run_in_executor",
    "_cp_offload", "_oauth_offload", "run_control_plane_call",
    "run_on_daemon_worker",
})

#: Async bodies this change OFF-LOADS. A subset assertion: a rename or a
#: refactor that drops one back onto the loop fails `test_graph_io_is_offloaded`.
#: NOTE: the assertion is about the SEAMS THE SCAN SEES. ``patch_onboarding_state``
#: has its detected seams off-loaded (the ``read_onboarding_node`` read and the
#: ``_graph_has_org_namespace`` probe), but its later onboarding WRITES still run
#: through sync helpers the scan cannot see — those are part of the residual, not
#: covered by this set.
_OFFLOADED_ASYNC_BODIES = frozenset({
    "list_points", "get_point", "org_info", "list_sessions",
    "get_session_detail", "dream_health",
    "list_api_keys", "list_members", "list_pending_invites_for_me",
    "patch_onboarding_state",
})

#: FastAPI route handlers STILL running sync FalkorDB I/O inline. Declared,
#: reviewed residual — burn down under #3718 (kept open via #2924). A NAME
#: leaving this set without the handler being off-loaded fails
#: `test_graph_io_is_offloaded`; a NEW inline route fails the same assertion.
_KNOWN_INLINE_ROUTE_RESIDUAL = frozenset({
    "provision_tenant", "register_user", "create_api_key", "revoke_api_key",
    "toggle_api_key_enabled", "commit_session", "delete_session",
    "delete_graph", "invite_to_org", "accept_invite", "invite_otp",
    "resend_invite", "expire_invite", "decline_invite", "remove_member",
    "change_member_role", "import_org", "reconcile", "agent_signup",
    "session_key", "public_demo", "github_callback", "backups_create",
    "backups_restore", "backups_sweep", "backups_purge", "backups_rebaseline",
    "backups_drill", "backups_drill_scheduled", "webhooks_stripe",
})

#: Non-route async bodies with inline sync FalkorDB I/O — the per-request auth
#: dependency `get_current_org` (6 sites, the single highest-traffic one), the
#: membership/owner gates, the capture path (`_capture_session_impl`) and the
#: lifecycle/backup helpers. Same declared residual; same burn-down. Scanned
#: rather than ignored because a dependency body is still ON the loop.
_KNOWN_INLINE_HELPER_RESIDUAL = frozenset({
    "_lifespan", "get_current_org", "_capture_session_impl", "_user_memberships",
    "_membership_org", "_org_node", "_count_active_free_memberships",
    "_owned_free_org_ids", "_create_org_registry_lane",
    "_apply_graph_recording_override", "_apply_graph_rename", "_graph_row_probe",
    "_rollback_restore_name_race", "_trash_name_conflict",
    "_require_owner_admin", "_require_owner", "_registry_mismatch_accept_v2",
    "_registry_accept_by_id", "_quarantine_import", "_run_indexing",
})


def _callee_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _graph_bound_names(node: ast.AST) -> set[str]:
    """Names bound to a graph handle via a ``_get_proj()`` / ``_get_registry()``
    (or ``dream_health_check``) call ANYWHERE in the assigned expression —
    including the offload-wrapped shape ``proj = await asyncio.to_thread(
    sdk._get_proj)`` that ``get_session_detail`` uses. Matching the Call only
    when it is the direct ``Assign.value`` (the first revision) missed that
    shape, so a later inline call on the handle was invisible (#3718 review).
    """
    names: set[str] = set()
    for n in ast.walk(node):
        if not isinstance(n, ast.Assign):
            continue
        if any(isinstance(sub, ast.Call)
               and _callee_name(sub.func) in _GRAPH_SEAM_CALLEES
               for sub in ast.walk(n.value)):
            for target in n.targets:
                if isinstance(target, ast.Name):
                    names.add(target.id)
    return names


def _has_inline_graph_io(node: ast.AsyncFunctionDef) -> list[int]:
    """Line numbers of sync-FalkorDB seams NOT inside an offload boundary."""
    bound = _graph_bound_names(node)
    hits: list[int] = []

    def visit(current: ast.AST) -> None:
        if isinstance(current, ast.Call):
            if _callee_name(current.func) in _OFFLOAD_BOUNDARY_CALLEES:
                # An argument that is a callable REFERENCE (Lambda / Name /
                # Attribute) runs on the worker — skip it, at ANY position (the
                # callable sits at index 0 for ``to_thread`` but at index 1 for
                # ``run_in_executor`` / ``_run_with_close``). Every other
                # argument (a Call, a comprehension, ...) is evaluated EAGERLY
                # on the loop, so it must still be scanned.
                for arg in current.args:
                    if isinstance(arg, (ast.Lambda, ast.Name, ast.Attribute)):
                        continue
                    visit(arg)
                for kw in current.keywords:
                    if isinstance(kw.value,
                                  (ast.Lambda, ast.Name, ast.Attribute)):
                        continue
                    visit(kw.value)
                return
            name = _callee_name(current.func)
            if name in _GRAPH_SEAM_CALLEES:
                hits.append(current.lineno)
            elif isinstance(current.func, ast.Attribute):
                base = current.func.value
                if (name == "query" and isinstance(base, ast.Attribute)
                        and base.attr == "g") or (isinstance(base, ast.Name) and base.id in bound):
                    hits.append(current.lineno)
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef,
                                  ast.ClassDef)):
                continue
            visit(child)

    for stmt in getattr(node, "body", []):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        visit(stmt)
    return hits


def _inline_graph_io_bodies() -> dict[str, list[int]]:
    tree = ast.parse(HOSTED_API.read_text())
    return {
        node.name: lines
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef)
        and (lines := _has_inline_graph_io(node))
    }


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
    by actually converting the body."""
    inline = _inline_graph_io_bodies()
    declared = _KNOWN_INLINE_ROUTE_RESIDUAL | _KNOWN_INLINE_HELPER_RESIDUAL

    still_inline = sorted(_OFFLOADED_ASYNC_BODIES & set(inline))
    assert not still_inline, (
        f"{still_inline} are declared OFF-LOADED but still run sync FalkorDB "
        f"I/O inline at {[(n, inline[n]) for n in still_inline]} — the read "
        f"off-load regressed (#3718)")

    undeclared = sorted(set(inline) - declared)
    assert not undeclared, (
        f"async body/bodies {[(n, inline[n]) for n in undeclared]} run "
        f"synchronous FalkorDB I/O on the event loop and are NOT in the "
        f"declared residual — off-load them with `await asyncio.to_thread(...)` "
        f"(short calls) or the dedicated pool (long/stallable work), or add "
        f"them to the residual with a tracking note (#3718)")


def test_residual_names_still_exist():
    """A rename or deletion must fail HERE, not silently vacate the pin."""
    tree = ast.parse(HOSTED_API.read_text())
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    declared = (_OFFLOADED_ASYNC_BODIES | _KNOWN_INLINE_ROUTE_RESIDUAL
                | _KNOWN_INLINE_HELPER_RESIDUAL)
    ghosts = sorted(declared - defined)
    assert not ghosts, (
        f"{ghosts} are declared residual but no longer defined in "
        f"hosted_api.py — the residual list has rotted (#3718)")
