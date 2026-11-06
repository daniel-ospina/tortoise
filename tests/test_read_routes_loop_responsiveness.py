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
REQUEST created WHILE ITS WINDOW WAS OPEN (``state["active"]``, on whichever
thread does the work — the shape the dataplane sibling has used since #4660),
plus main-thread builds, and delegates every other call untouched. Ownership by
main thread alone would stop observing exactly the bodies the #4660 construction
burn-down moves to a worker (#3718), and their attach assertions would pass
vacuously.

A WINDOW ALONE IS NOT ENOUGH, though: the sweeps are armed from ``_lifespan``
at fixture entry and ``_sweep_events`` / ``_sweep_invite_ghosts`` construct AND
attach as ADJACENT statements on their daemon thread, so one firing inside an
open window would be owned — which re-admits exactly the corruption recorded
above for the one caller the probe was scoped to exclude. The window half is
therefore CLOSED to the background sweep lanes by thread name
(``_BACKGROUND_SDK_THREAD_PREFIXES``) and stays open to every thread the
request itself works on (``asyncio.to_thread``'s executor, the ``graph`` pool).
``test_probe_does_not_own_a_background_sweep_built_inside_the_window`` pins
both halves.

SCOPE — the handlers converted in this change (the read-only FalkorDB class):
``list_points``, ``get_point``, ``org_info``, ``list_sessions``,
``get_session_detail``, ``dream_health`` (graph) and ``list_api_keys``,
``list_members``, ``list_pending_invites_for_me`` (registry), plus the hot
onboarding PATCH's read (``patch_onboarding_state``) and the public invite-info
read (``invite_info``, ``GET /v1/invites/info``). ``invite_info``'s reads are
off-loaded too — the registry legs via ``asyncio.to_thread``, the Supabase
control-plane legs via ``_cp_offload`` — so it is no longer in
``_KNOWN_INLINE_ROUTE_RESIDUAL``. ``list_my_orgs`` (``GET /v1/organizations``,
the switcher every client reads on app load) joined it in the #3718 burn-down,
for its LISTING half only: the per-org graph listings now run OFF the loop in
chunks on the MODE's lane — registry → ``_graph_offload``,
``_GRAPH_LIST_OFFLOAD_CHUNK`` orgs per submission, reading the
process-lifetime ``_registry_anchor()`` (no per-org construction in the
worker); Supabase → ``_cp_offload``, ``_GRAPH_LIST_CONTROL_PLANE_CHUNK`` orgs,
a PostgREST read that builds NO graph handle at all. So the route body left the
declared residual for ``_OFFLOADED_ASYNC_BODIES``. The one anchor build on the
REGISTRY path belongs to ``_org_node``, which stays inline and declared here.
Its membership scan and per-org Team read stay inline — #2446 records a
thread-dependence MEASURED on `_membership_role`'s read, and these two are the
same registry-read class by inference (the relay there marks it) — so
``_user_memberships`` and ``_org_node`` REMAIN in
``_KNOWN_INLINE_HELPER_RESIDUAL``. EVERY OTHER async body in
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
source scan — and, since #3718's construction burn-down, in THIS file too
(``test_read_route_construction_is_offloaded``), so every read body converted
**in this #3718 construction burn-down** is pinned behaviourally as well as
structurally (``len(_CONSTRUCTION_OFFLOADED_READS)`` — the count is derived,
not restated here). This is NOT every off-loaded read construction in the
module: ``events_poll`` and the two context-subscription reads also submit
their construction to the ``graph`` pool and sit in none of this file's
declared sets. ``events_poll`` HAS a behavioural pin — construction thread
affinity — in
``tests/test_graph_write_loop_responsiveness.py::test_write_preamble_graph_helpers_run_off_the_loop``
(its ``events`` endpoint). The two context-subscription reads lack a
BEHAVIOURAL (thread-affinity) pin; their construction is still covered
STRUCTURALLY — both are absent from every declared residual set, so
``test_graph_io_is_offloaded`` reds if either goes back inline. See the
`_graph_unavailable` docstring in ``tortoise/hosted_api.py``.

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
# The switcher's seeded default graph — asserted on the response's
# `default_graph_id`, which is the ONE field the N+1→batched lookup rewrite
# feeds per row (#3718).
_SEEDED_DEFAULT_GRAPH_ID = f"{_SEEDED_INVITE_ORG}-default"
# Enough extra orgs that the listing spans MORE than one
# `_GRAPH_LIST_OFFLOAD_CHUNK` submission, so the chunk loop is actually walked.
MULTI_ORG_COUNT = 9


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
    # #3718 (construction burn-down, second batch): four more DATA-plane read
    # routes that opened their SDK on the loop. `search` and `topic_summary`
    # already off-loaded their ROUND TRIP, so only the construction moved;
    # `session_context` and `issue_insight` called the SDK INLINE, so those two
    # move the round trip as well.
    ("search", "search",
     lambda: {"method": "GET", "url": "/v1/search?q=seed"}),
    ("topic-summary", "topic_summary",
     lambda: {"method": "GET", "url": "/v1/topics/release/summary"}),
    ("session-context", "session_context",
     lambda: {"method": "GET", "url": "/v1/context"}),
    ("issue-insight", "issue_insight",
     lambda: {"method": "GET", "url": "/v1/issue-insight?title=seed"}),
]


#: The read cases whose SDK CONSTRUCTION is off the loop (#3718 burn-down of
#: the #4660 construction residual). Named as a set so the membership assertion
#: below has something to compare against: this class can only GROW by
#: off-loading another body, and the AST pin's residual set is the other half.
_CONSTRUCTION_OFFLOADED_READS = frozenset({
    "points-list", "point-get", "dream-health", "team",
    "sessions-list", "session-get",
    "search", "topic-summary", "session-context", "issue-insight",
})

#: Derived, never re-typed: the request kwargs come from ``_READ_CASES`` so a
#: renamed route cannot leave a stale duplicate here.
_CONSTRUCTION_CASES = [c for c in _READ_CASES
                       if c[0] in _CONSTRUCTION_OFFLOADED_READS]


#: Daemon worker THREAD-name prefixes whose SDK builds are NOT the request's
#: work (#3718 review P2). ``run_on_daemon_worker`` names BOTH sweep lanes
#: ``tortoise-boot-sweep`` — the one-shot ``_run_boot_sweeps`` and the periodic
#: ``_event_retention_loop`` — and ``_sweep_events`` (``sdk = _make_sdk(...)``
#: then ``proj = sdk._get_proj()``) / ``_sweep_invite_ghosts`` construct-and-
#: attach adjacently on that thread, on a schedule the test does not control.
#:
#: Why name, not time: a WINDOW-ONLY ownership rule owns a sweep's SDK whenever
#: the sweep fires while the window is open, which (a) satisfies the new
#: construction test's ``sdk_builds > 0`` guard — whose whole job is "the run
#: actually built a REQUEST SDK, so a short-circuited request cannot pass" —
#: with work the request never did, and (b) in the ``block=True`` freeze test
#: lets that SDK's ``_get_proj`` overwrite ``entered`` / ``exited`` (last-write-
#: wins, ``_make_probe_fn``), rotating the measured stall window. The window is
#: load-bearing for the request's own WORKER-built SDK (#3718 moves the
#: construction there), so it cannot simply be dropped — it is closed to these
#: threads instead. ``workers == 1`` keeps the thread name exact; a multi-worker
#: lane is suffixed (``<name>-<i>``), hence the prefix match.
_BACKGROUND_SDK_THREAD_PREFIXES = ("tortoise-boot-sweep",)


def _is_background_sdk_thread() -> bool:
    """Is the current thread a background lane whose SDK build is not ours?"""
    name = threading.current_thread().name
    return any(name.startswith(p) for p in _BACKGROUND_SDK_THREAD_PREFIXES)


class _RequestScopedProbe:
    """A ``TortoiseSDK._get_proj`` probe that only sees REQUEST-owned SDKs.

    ``_get_proj`` is the graph ATTACH every read handler under test reaches
    (the registry reads get there through ``_get_registry``; ``dream_health``
    reaches it through ``dream_health_check`` -> ``_hydrate_dirty_roots``),
    which makes it the one seam that discriminates off-loop from on-loop for
    the attach without patching each handler's query shape. But it is also used
    by background daemon-thread sweeps, so the probe scopes itself to the SDK
    instances the REQUEST created: those built while its WINDOW was open
    (``state["active"]``, on whichever thread does the work — since #3718 the
    request's own SDK is built on a ``graph``-pool worker), plus main-thread
    builds (the pre-#3718 shape). Owning by the main thread ALONE would stop
    observing exactly the worker-built SDK the #3718 conversion introduces, and
    every attach assertion below would then pass vacuously. A daemon-thread
    sweep owns nothing — with no window open, and by NAME even when a window is
    open (``_BACKGROUND_SDK_THREAD_PREFIXES``: the sweeps construct-and-attach
    adjacently on their own schedule, so a window alone would own them) — and
    is delegated untouched. The window plus that name exclusion, not the
    thread, is the owner.

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
                # #3718: "this build is the REQUEST's work" — a build inside the
                # window AND not on a background sweep lane. Attribution gates
                # BOTH the construction record below and ownership: a sweep's
                # SDK must not satisfy `sdk_builds > 0` (the short-circuit
                # guard) nor become `_get_proj`-observable.
                request_work = (
                    bool(self._state.get("active"))
                    and not _is_background_sdk_thread())
                # #3718: record the CONSTRUCTION's own view of the loop. This is
                # the behavioural half of the AST pin's construction class — the
                # pin sees `_make_sdk` / `_data_sdk` in an async body, this sees
                # whether the call actually ran on the loop thread.
                if request_work:
                    try:
                        asyncio.get_running_loop()
                        built_on_loop = True
                    except RuntimeError:
                        built_on_loop = False
                    # AGGREGATED, not last-call-wins: a body building several
                    # SDKs must not let a later off-loop construction erase an
                    # on-loop first one.
                    self._state["sdk_built_on_loop"] = (
                        self._state.get("sdk_built_on_loop", False)
                        or built_on_loop)
                    self._state["sdk_builds"] = (
                        self._state.get("sdk_builds", 0) + 1)
                    if built_on_loop:
                        self._state.setdefault("sdk_build_threads", []).append(
                            threading.current_thread().name)
                sdk = real(*args, **kwargs)
                # Owned = built as the request's own work (`request_work`: inside
                # the window and not on a background sweep lane), or on the main
                # thread (the pre-#3718 shape). The window is what keeps the
                # probe honest once a body builds its SDK on a WORKER thread:
                # owning by main thread alone would silently stop observing the
                # SDK the request is about to use, and every attach assertion
                # would pass vacuously. The sweep exclusion is what stops the
                # window from owning a sweep's own SDK when the two overlap
                # (`_BACKGROUND_SDK_THREAD_PREFIXES`).
                if (request_work
                        or threading.current_thread() is threading.main_thread()):
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
            state["active"] = True
            try:
                return await ac.request(**request_kwargs())
            finally:
                state["active"] = False

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


@pytest.mark.parametrize("label,handler,request_kwargs", _CONSTRUCTION_CASES,
                         ids=[c[0] for c in _CONSTRUCTION_CASES])
def test_read_route_construction_is_offloaded(client, monkeypatch, label,
                                              handler, request_kwargs):
    """#3718 (construction half of #4660): building the per-request SDK must not
    run on the event loop either.

    The read's ROUND TRIP was already off the loop (#4455) — but the SDK that
    owns it was still built inline, and in embedded mode ``_make_sdk`` probes
    the keepalive anchor (``_anchor_usable``) and may eagerly connect with the
    socket held, so the read route froze the loop on its FIRST statement. This
    is the same defect class the write lane closed in #3773; the reads were left
    in ``_OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION``.

    Mechanism assertion, not a source grep: ``asyncio.get_running_loop()``
    succeeds only on the loop thread. Two sides, both required:

    * ``sdk_builds > 0`` — the run actually constructed a request SDK, so a
      route that short-circuited earlier cannot pass this vacuously;
    * ``sdk_built_on_loop is not True`` — the construction did NOT run on the
      loop thread.
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
            state["active"] = True
            try:
                return await ac.request(**request_kwargs())
            finally:
                state["active"] = False

    response = asyncio.run(_run())

    assert state.get("sdk_builds", 0) > 0, (
        f"[{label}] {handler} built no SDK while the request was live "
        f"({response.status_code}: {response.text[:200]}) — this run proves "
        f"nothing about the construction off-load (#3718)")
    assert state.get("sdk_built_on_loop") is not True, (
        f"[{label}] {handler} CONSTRUCTED its SDK on the event loop "
        f"(threads={state.get('sdk_build_threads')!r}) — in embedded mode "
        f"`_make_sdk` probes the anchor and may eagerly connect with the "
        f"socket held, so the construction freezes every concurrent request "
        f"exactly as the attach did. Route it through `_data_sdk_offloaded` / "
        f"`_graph_offload` (#3773, #4660)")
    assert response.status_code == 200, (
        f"[{label}] the off-loaded construction changed the response "
        f"({response.status_code}: {response.text[:200]})")


def test_probe_does_not_own_a_background_sweep_built_inside_the_window(
        monkeypatch):
    """#3718 review: a sweep's SDK built INSIDE the window is NOT request-owned.

    The ownership rule is "built while the request window was open, on whichever
    thread does the request's work" — but a WINDOW IS NOT A REQUEST. The boot
    sweeps are armed from ``_lifespan`` at fixture entry and ``_sweep_events``
    builds its SDK and attaches it as ADJACENT statements on the
    ``tortoise-boot-sweep`` daemon thread (``_make_sdk`` then
    ``sdk._get_proj()``), so whenever a sweep fires inside an open window a
    window-only rule owns it. That re-admits the very corruption this probe was
    scoped to exclude (header: ``thread='tortoise-boot-sweep'``), and it breaks
    two guards rather than one:

    * ``sdk_builds > 0`` — the construction test's "the run built a REQUEST SDK"
      half — becomes satisfiable by a sweep, so a request that short-circuited
      before its own construction could pass;
    * in the ``block=True`` freeze test that SDK's ``_get_proj`` overwrites
      ``entered`` / ``exited`` (last-write-wins), rotating the measured stall
      window.

    Pre-fix this is RED on both halves: the sweep build increments
    ``sdk_builds`` and lands in ``state["sdks"]``. Post-fix the window keeps
    owning the request's own worker build (``asyncio_0`` here — the shape
    ``asyncio.to_thread`` / the graph pool produce) and refuses the sweep lane.
    """
    import tortoise.hosted_api as ha_mod

    built: list = []

    def _stub_make_sdk(*_args, **_kwargs):
        sdk = object()          # distinct identity per build, so ownership is
        built.append(sdk)       # observable without constructing a real SDK
        return sdk

    monkeypatch.setattr(ha_mod, "_make_sdk", _stub_make_sdk)
    state: dict = {}
    _RequestScopedProbe(monkeypatch, ha_mod, state, block=False)

    state["active"] = True      # the request's window is OPEN ...

    def _build_from(thread_name: str):
        """Construct an SDK from a thread named like the named lane would."""
        done = threading.Event()

        def _run():
            try:
                # Guard-passing namespace, and provably inert here: the SDK is
                # stubbed, and the probe's ownership rule keys on the request
                # WINDOW plus a thread-name exclusion for the sweep lane — never
                # on the namespace (see this test's docstring). `test_markers`'
                # census refuses an un-routed `org-*` literal in a test file, and
                # the hyphenated `test-*` family is SDK-normalized by
                # `_derive_graph_name` (tortoise/sdk.py).
                ha_mod._make_sdk(namespace="test-org-3718")
            finally:
                done.set()

        worker = threading.Thread(target=_run, name=thread_name, daemon=True)
        worker.start()
        assert done.wait(10), f"the {thread_name!r} builder never ran"
        worker.join(10)
        assert not worker.is_alive(), f"the {thread_name!r} builder hung"

    _build_from("tortoise-boot-sweep")     # a background sweep, mid-window
    _build_from("asyncio_0")               # the request's own worker build

    sweep_sdk, request_sdk = built

    assert state["sdks"] == [request_sdk], (
        f"the probe owned {len(state['sdks'])} SDK(s) for a background sweep "
        f"that built one INSIDE the request window (owned={state['sdks']!r}, "
        f"sweep={sweep_sdk!r}) — a sweep's SDK is not the request's work, and "
        f"owning it re-opens the measured `tortoise-boot-sweep` corruption "
        f"(#3718)")
    assert state.get("sdk_builds") == 1, (
        f"the request's construction count is {state.get('sdk_builds')!r}, not "
        f"1 — a background sweep's build was attributed to the request, so "
        f"`sdk_builds > 0` no longer proves the REQUEST built an SDK (#3718)")
    assert state.get("sdk_built_on_loop") is False, (
        f"neither build ran on a loop thread, yet the probe recorded "
        f"sdk_built_on_loop={state.get('sdk_built_on_loop')!r} (#3718)")


class _BrokenSDK:
    """An SDK whose every graph attach fails — the ``#1591`` broken-STORE shape.

    Stands in for ``TortoiseSDK`` in the capacity test below: the construction
    is what that test controls, and its only requirement of the returned object
    is that a later read refuses (so a route that IS allowed to reach its read
    degrades down the fail-soft path).
    """

    def _get_proj(self):
        raise RuntimeError("graph unavailable")

    def _get_registry(self):
        raise RuntimeError("graph unavailable")

    def close(self):
        return None


#: The READ routes for which the #3718 construction off-load adds a
#: fail-CLOSED mode that must be pinned: a saturated/bound-missed ``graph``
#: pool now refuses the request, where the inline construction merely delayed
#: it. This is NOT "the routes with a soft shape" — only some members have one.
#: The third element is that route's own soft-shape keys (a 200 carrying an
#: EMPTY body), which is what would make a refusal indistinguishable from a
#: real empty result; it is the EMPTY tuple for a route with no soft shape.
#: Per-route rather than one shared key list, because the routes do not share a
#: body shape — a list that only matched ``sessions`` would pass a
#: ``/v1/search`` soft body vacuously.
#:
#: ``list_points`` / ``get_point`` / ``dream_health`` were already
#: fail-closed on their read, so the 503 is not a contract change there (and
#: their response has no soft shape to confuse).
_FAIL_SOFT_CONTRACT_CASES = [
    ("team", "/v1/team", ("point_count",)),
    ("sessions-list", "/v1/sessions", ("sessions", "session")),
    ("session-get", f"/v1/sessions/{_SEEDED_SESSION}", ("sessions", "session")),
    # #3718 second construction batch. `search`'s soft shape IS a clean empty
    # (``{"results": [], "count": 0}``), so its capacity refusal is the case
    # the #3892 rule exists for: a busy pool must not answer "nothing matched".
    ("search", "/v1/search?q=seed", ("results", "count")),
    # ``session_context`` / ``issue_insight`` have no soft shape (their failure
    # arm is a 500 with a generic detail), so there is no key to forbid — what
    # these two pin is that a CAPACITY refusal is not served as the route's own
    # 200 body either.
    ("session-context", "/v1/context", ()),
    ("issue-insight", "/v1/issue-insight?title=seed", ()),
    # `topic_summary` is the same 500→503 transition as the two above (its arm
    # is `except Exception → 500 "Topic summary failed"`) — included for the
    # same reason: without a row, a later change could wrap its construction in
    # a swallowing arm and answer 500/`{}` on pool capacity with the suite still
    # green, which is the shape #3892 forbids.
    ("topic-summary", "/v1/topics/release/summary", ()),
]


@pytest.mark.parametrize("label,url,soft_keys", _FAIL_SOFT_CONTRACT_CASES,
                         ids=[c[0] for c in _FAIL_SOFT_CONTRACT_CASES])
def test_graph_pool_capacity_is_fail_closed_not_a_silent_empty(
        client, monkeypatch, label, url, soft_keys):
    """#3718: a graph-pool CAPACITY refusal must never become the soft shape.

    Moving the per-request SDK construction onto the ``graph`` pool gives each
    route in this set a failure mode it did not have: the offload can REFUSE a
    submission (saturated backlog) or miss its wait bound. For the ``#1591``
    FAIL-SOFT routes (``team`` / ``sessions-list`` / ``session-get``) that
    contract is for a MISSING/BROKEN graph — a STORE condition — and
    the empty shape is the honest answer there (``point_count: 0`` /
    ``sessions: []`` / ``session: None``). It is NOT the honest answer for our
    own capacity: serving it would report real data as absent, which is exactly
    the clean-empty-for-a-store-we-could-not-reach shape ``#3892`` forbids. So
    the refusal is fail-CLOSED — a retryable 503 ``graph_unavailable`` — and
    the fail-soft arm below the construction still covers the broken store
    (``test_hosted_api.py::test_sessions_fail_soft_when_graph_unavailable`` &c,
    unchanged). The contract is recorded at each route's construction site; this
    is its behavioural pin.

    The other FOUR members (``search`` / ``session-context`` /
    ``issue-insight`` / ``topic-summary``) carry no ``#1591`` arm; what their
    rows pin is narrower
    and stated by ``soft_keys`` — a capacity refusal is never served as that
    route's own 200 body (``search``'s clean-empty shape is the live ``#3892``
    case here) — and an empty ``soft_keys`` means the route has no soft shape to
    forbid, so the row asserts the 503 alone.

    RED on the pre-#3718 shape, and for the right reason: with the construction
    INLINE the lane's bound is never armed (the slow construction merely delays
    the request) and each of the three ``#1591`` rows returns 200 with its soft
    body. The other FOUR RED on the status code ALONE: ``_BrokenSDK`` defines
    only ``_get_proj`` / ``_get_registry`` / ``close``, so the pre-change inline
    path reaches ``sdk.tortoise_fts_query`` / ``sdk.session_context`` /
    ``sdk.issue_insight`` / ``sdk.topic_summarize``, raises ``AttributeError`` and
    lands in each route's generic 500 arm. So for those four the pre-change
    observation is a 500, not
    a soft 200 — the status CODE is the whole difference. ``search`` still owns
    the soft-shape hazard its ``soft_keys`` row pins (a 200 clean empty is its
    REAL post-change body, which is why that key list is not empty); the 500 is
    only what the pre-change shape happens to show when ``_BrokenSDK`` is what
    got constructed.
    """
    import tortoise.hosted_api as ha_mod

    # The graph lane's own bound, lowered to a bound miss — the lever
    # ``tests/test_control_plane_offload_3498.py`` uses for this pool.
    monkeypatch.setattr(ha_mod, "graph_offload_timeout_s", lambda: 0.05)

    def _slow_data_plane(real):
        def _wrapped(*args, **kwargs):
            # The REGISTRY plane (auth, membership, invite lookups) is not what
            # this test is about — it runs inline today and must keep working,
            # or the request would 500 before reaching the route's own
            # construction.
            if kwargs.get("namespace") == "registry":
                return real(*args, **kwargs)
            time.sleep(0.4)
            return _BrokenSDK()
        return _wrapped

    monkeypatch.setattr(ha_mod, "_data_sdk",
                        _slow_data_plane(ha_mod._data_sdk))
    monkeypatch.setattr(ha_mod, "_make_sdk",
                        _slow_data_plane(ha_mod._make_sdk))

    response = client.get(url)

    assert response.status_code == 503, (
        f"[{label}] a graph-pool bound miss returned {response.status_code} "
        f"({response.text[:200]}) — #3718's capacity contract is a retryable "
        f"503, never the #1591 soft shape, which is reserved for a BROKEN "
        f"STORE (#3892: no clean empty for a store we could not reach)")
    body = response.json()
    assert body.get("detail", {}).get("error_code") == "graph_unavailable", body
    leaked = [k for k in soft_keys if k in body]
    assert not leaked, (
        f"[{label}] the capacity refusal came back with the route's soft-body "
        f"key(s) {leaked} ({body}) — a warm pool would then be "
        f"indistinguishable from an empty result")


@pytest.mark.parametrize("label,handler,request_kwargs", _CONSTRUCTION_CASES,
                         ids=[c[0] for c in _CONSTRUCTION_CASES])
def test_read_routes_submit_one_graph_pool_submission(
        client, monkeypatch, label, handler, request_kwargs):
    """#3718 makes the ``graph`` pool a READ lane — pin its per-request occupancy.

    The pool's occupancy disclosure (``tortoise/monitoring.py``,
    ``CONTROL_PLANE_GRAPH_*`` — the #3669 "not hidden" rule) now states that
    each read request in this set submits here exactly ONCE, for its SDK
    construction, and that its graph READS do not come here at all (they ride
    the house ``asyncio.to_thread`` default executor, ``dream_health``'s pass on
    ``_DREAM_EXECUTOR``). The count is ``len(_CONSTRUCTION_CASES)``, derived
    rather than restated. That number is the rule's content, so it is pinned
    here rather than left to prose: a second submission per read changes the
    pool's read pressure, and a ZERO means the construction went back inline.

    RED on the pre-#3718 shape — the inline construction submitted nothing to
    this pool, which is why the disclosure block had no read-plane line at all.
    """
    import tortoise.hosted_api as ha_mod
    from tortoise.hosted_api import app

    _seed_read_surface()
    _arm_session_endpoints(monkeypatch)

    real_call = ha_mod.run_control_plane_call
    pools: list[str] = []

    async def _recorder(fn, *, op, timeout=None, pool="auth",
                        cancel_on_timeout=True):
        pools.append(pool)
        return await real_call(fn, op=op, pool=pool, timeout=timeout,
                               cancel_on_timeout=cancel_on_timeout)

    monkeypatch.setattr(ha_mod, "run_control_plane_call", _recorder)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            return await ac.request(**request_kwargs())

    response = asyncio.run(_run())

    assert response.status_code == 200, (
        f"[{label}] the request did not complete "
        f"({response.status_code}: {response.text[:200]})")
    graph_submissions = [p for p in pools if p == "graph"]
    assert len(graph_submissions) == 1, (
        f"[{label}] {handler} made {len(graph_submissions)} `graph`-pool "
        f"submission(s) (all pools: {pools!r}) — the occupancy disclosure in "
        f"tortoise/monitoring.py declares exactly ONE per read request (the SDK "
        f"construction). ZERO means the construction went back on the loop; "
        f"more than one means the pool's read pressure changed and the "
        f"disclosure block has to move with it (#3718, #3669)")


def test_construction_offloaded_reads_are_the_declared_ones():
    """The behavioural class and the AST residual set must name the SAME bodies.

    Two guards, one class: the AST pin (``_OFFLOADED_ASYNC_BODIES`` /
    ``_OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION``) and this file's
    behavioural cases. If they can drift, one of them is decoration — a body
    could be converted and still sit in the residual, or leave the residual with
    no behavioural proof that it is off the loop.
    """
    assert len(_CONSTRUCTION_CASES) == len(_CONSTRUCTION_OFFLOADED_READS), (
        "a label in `_CONSTRUCTION_OFFLOADED_READS` has no `_READ_CASES` row — "
        f"the behavioural case was renamed or dropped: "
        f"{sorted(_CONSTRUCTION_OFFLOADED_READS - {c[0] for c in _READ_CASES})}")
    assert frozenset({
        "points-list", "point-get", "dream-health", "team",
        "sessions-list", "session-get",
        "search", "topic-summary", "session-context", "issue-insight",
    }) == _CONSTRUCTION_OFFLOADED_READS, (
        "the construction-off-loaded read set changed shape — re-derive which "
        "read bodies are actually off the loop before trusting the AST pin "
        "(#3718)")
    assert {c[1] for c in _CONSTRUCTION_CASES} <= set(
        _OFFLOADED_ASYNC_BODIES), (
        "a behaviourally-proven off-loaded read body is not in the AST pin's "
        "fully-off-loaded set — the pin would let its construction regress "
        "silently (#3718)")
    assert not ({c[1] for c in _CONSTRUCTION_CASES}
                & _OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION), (
        "a read body is declared BOTH fully off-loaded and a construction "
        "residual — the two declarations disagree (#3718)")


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
            # The request WINDOW owns the probe's SDKs (#3718): with the
            # construction off the loop the SDK is built on a worker thread, so
            # a main-thread-only ownership rule would silently stop observing
            # the attach this test exists to watch — and the blocking stand-in
            # below would never fire.
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


# ── the org switcher (GET /v1/organizations), #3718 ───────────────────────
#
# The switcher is the FIRST read every client makes (app load). At the shape
# this change started from it issued 2N+1 sync registry reads on the loop for N
# memberships: the membership scan, one Team read per membership (``_org_node``)
# and one ``graph_list`` per membership. The LISTING half is burned down here
# (``ceil(N / the lane's chunk)`` off-loop submissions); the two registry reads
# stay inline as declared residual because #2446 records a thread-dependence
# MEASURED on `_membership_role`'s read, with these two in the same class by
# inference.
# All three reach the registry through ``_get_registry()``,
# so ONE probe on that seam sees the whole path — no per-handler
# instrumentation, and no reliance on how each read is spelled.
#
# BOUNDARY, stated rather than implied (the module header's rule): this pin is
# MECHANISM-scoped, not request-ownership-scoped. ``_RequestScopedProbe`` above
# owns SDKs built during the request WINDOW or on the main thread;
# the listing's registry read is
# off-loaded — it runs on a worker thread through the process-lifetime
# ``_registry_anchor()`` (no construction of its own; in Supabase mode no graph
# handle at all) — so the switcher cannot be a ``_READ_CASES`` entry and cannot
# use the tick-counting harness: the blocking read never happens on the loop, so
# adding it there would fail with "graph seam was never reached" for the correct
# implementation. ``_RegistryReadProbe`` therefore patches ``_get_registry`` at
# CLASS level and records, per query, whether the caller had a running loop.
# That class-level patch also sees daemon-thread sweeps
# (``tortoise-boot-sweep``) — the corruption the header records for
# ``_get_proj`` — but for THIS seam it is harmless: a sweep thread has no
# running loop, so its reads are recorded ``on_loop=False`` and the assertion
# ("the listing did not run on the loop") cannot be softened by them.


def _seed_org_switcher():
    """Seed an active Membership + a default Graph node for the session user.

    Runs after ``_seed_read_surface()``: the Team for ``_SEEDED_INVITE_ORG``
    is already there, so the switcher's per-membership ``_org_node`` resolves
    and the ``graph_list`` leg is actually reached.

    The Graph node uses the CANONICAL writer's shape, ``id:`` (not
    ``graph_id:``) — ``graph_list`` maps ``graph_id = props.get("id")``
    (sdk.py's ``create_graph``), so seeding ``graph_id`` would hand the handler
    a ``graph_id=None`` row and silently make ``default_graph_id``
    unassertable.
    """
    import tortoise.hosted_api as ha_mod

    reg = ha_mod._make_sdk(namespace="registry")
    try:
        g = reg._get_registry()  # warm the registry graph + its indexes
        g.query(
            "CREATE (m:Membership {user_id:$uid, org_id:$oid, "
            "role:'owner', status:'active'})",
            params={"uid": "read-loop-3718-user", "oid": _SEEDED_INVITE_ORG},
        )
        g.query(
            "CREATE (g:Graph {id:$gid, org_id:$oid, name:'main', "
            "kind:'default', status:'active'})",
            params={"gid": _SEEDED_DEFAULT_GRAPH_ID,
                    "oid": _SEEDED_INVITE_ORG},
        )
    finally:
        reg.close()


def _seed_many_orgs(org_ids: list[str]):
    """Seed one Team + Membership + default Graph per id, for the same user.

    Used to walk the switcher's CHUNK loop: with more orgs than
    ``_GRAPH_LIST_OFFLOAD_CHUNK``, a chunk result that OVERWRITES instead of
    accumulating loses every org but the last chunk's (#3718).
    """
    import tortoise.hosted_api as ha_mod

    reg = ha_mod._make_sdk(namespace="registry")
    try:
        g = reg._get_registry()
        for org_id in org_ids:
            g.query(
                "CREATE (t:Team {id:$oid, name:$oid})",
                params={"oid": org_id},
            )
            g.query(
                "CREATE (m:Membership {user_id:$uid, org_id:$oid, "
                "role:'owner', status:'active'})",
                params={"uid": "read-loop-3718-user", "oid": org_id},
            )
            g.query(
                "CREATE (g:Graph {id:$gid, org_id:$oid, name:'main', "
                "kind:'default', status:'active'})",
                params={"gid": f"{org_id}-default", "oid": org_id},
            )
    finally:
        reg.close()


class _RegistryReadProbe:
    """Record every registry graph READ the request makes, and whether it ran
    ON the loop.

    ``asyncio.get_running_loop()`` succeeds only when the call executes on the
    loop thread; a worker thread has no running loop. This is the direct
    mechanism assertion — behavioural, not a source-grep for ``to_thread`` — so
    a refactor that puts any of these reads back on the loop fails here
    however it is written.
    """

    def __init__(self, monkeypatch):
        self.reads: list[tuple[str, bool]] = []
        probe = self
        real = TortoiseSDK._get_registry

        class _RecordingGraph:
            def __init__(self, graph):
                self._graph = graph

            def query(self, statement, *args, **kwargs):
                try:
                    asyncio.get_running_loop()
                    on_loop = True
                except RuntimeError:
                    on_loop = False
                probe.reads.append((statement, on_loop))
                return self._graph.query(statement, *args, **kwargs)

            def __getattr__(self, item):
                return getattr(self._graph, item)

        def _get_registry(self_sdk, *args, **kwargs):
            return _RecordingGraph(real(self_sdk, *args, **kwargs))

        monkeypatch.setattr(TortoiseSDK, "_get_registry", _get_registry)


def test_org_switcher_listing_runs_off_the_loop(client, monkeypatch):
    """#3718: the switcher's per-org graph LISTING must run off the loop.

    Scope, stated rather than implied: this test covers the LISTING half of the
    switcher only. Its membership scan and its per-org Team read stay INLINE as
    declared residual under #2446 — a thread-dependence MEASURED on
    `_membership_role`'s registry read (off-loading THAT produced an EMPTY
    member list in `test_invites_http.py::TestExpiredInviteReMint`), with these
    two helpers in the same read class by inference — so they are asserted
    REACHED here but deliberately NOT asserted off-loop. Pretending otherwise
    would let this pin certify a behaviour the #2446 note records as broken.

    Measured at ``main``: the listing ran ON the loop, once per membership, so
    the first request of every client issued N blocking registry round trips on
    the thread that serves every other request.
    """
    from tortoise.hosted_api import app

    _seed_read_surface()
    _seed_org_switcher()
    _arm_session_endpoints(monkeypatch)
    probe = _RegistryReadProbe(monkeypatch)

    async def _run():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            return await ac.get("/v1/organizations")

    response = asyncio.run(_run())

    assert response.status_code == 200, response.text[:300]
    rows = response.json()
    assert any(r.get("org_id") == _SEEDED_INVITE_ORG for r in rows), (
        f"the seeded org is missing from the switcher's answer ({rows!r}) — "
        f"the per-org reads did not run, so this proves nothing (#3718)")
    assert any(r.get("graph_count") == 1 for r in rows), (
        f"the seeded Graph node is not in the answer ({rows!r}) — the "
        f"graph_list leg did not run (#3718)")
    assert any(r.get("default_graph_id") == _SEEDED_DEFAULT_GRAPH_ID
               for r in rows), (
        f"the default graph id is not in the answer ({rows!r}) — the batched "
        f"listing lost the per-row `graph_id` the switcher resolves its "
        f"default from (#3718)")

    # Non-vacuity: the two inline legs must still have RUN, or a short-circuit
    # upstream would make the listing assertion below pass for the wrong reason.
    for label, needle in {"membership scan": "MATCH (m:Membership",
                          "Team read (_org_node)": "MATCH (t:Team"}.items():
        assert [r for r in probe.reads if needle in r[0]], (
            f"the {label} was never executed — the request short-circuited "
            f"before it (or the statement changed), so this run proves "
            f"nothing (#3718)")

    listings = [r for r in probe.reads if "MATCH (g:Graph" in r[0]]
    assert listings, (
        "the graph_list read was never executed — the request short-circuited "
        "before it, so this run proves nothing about the off-load (#3718)")
    on_loop = [stmt for stmt, loop in listings if loop]
    assert not on_loop, (
        f"the graph listing ran ON the event loop ({len(on_loop)} of "
        f"{len(listings)} call(s)) — one slow registry round trip freezes every "
        f"concurrent request; GET /v1/organizations is the first read every "
        f"client makes (the /v1/search precedent off-loads it; #3718)")


def test_org_switcher_listing_is_chunked_and_loses_no_row(client, monkeypatch):
    """#3718: the listing batch is CHUNKED, and every chunk's rows survive.

    The chunk loop exists because one submission must not carry an unbounded
    fan-out. That shape is only reachable for a user with MORE orgs than
    ``_GRAPH_LIST_OFFLOAD_CHUNK``, so a one-membership fixture cannot see it —
    and neither the "last chunk overwrites the earlier ones" regression nor a
    reverted single unbounded submission would fail the single-org pin above.
    """
    import tortoise.hosted_api as ha_mod

    org_ids = [f"read-loop-3718-multi-{i:02d}" for i in range(MULTI_ORG_COUNT)]
    _seed_read_surface()
    _seed_org_switcher()
    _seed_many_orgs(org_ids)
    _arm_session_endpoints(monkeypatch)

    real = ha_mod._graph_offload
    ops: list[str] = []

    async def _counting(fn, *, op, **kwargs):
        ops.append(op)
        return await real(fn, op=op, **kwargs)

    monkeypatch.setattr(ha_mod, "_graph_offload", _counting)

    async def _run():
        transport = httpx.ASGITransport(app=ha_mod.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as ac:
            return await ac.get("/v1/organizations")

    response = asyncio.run(_run())

    assert response.status_code == 200, response.text[:300]
    expected_chunks = -(-(MULTI_ORG_COUNT + 1)
                        // ha_mod._GRAPH_LIST_OFFLOAD_CHUNK)
    # The expectation is derived from the constant under test, so it moves WITH
    # it — assert the PREMISE too, or raising the constant to "bigger than any
    # real user" would make this pin certify the very unbounded submission it
    # exists to forbid (expected == actual == 1).
    assert expected_chunks >= 2, (
        f"this pin's premise is broken: {MULTI_ORG_COUNT + 1} orgs span only "
        f"{expected_chunks} chunk(s) of {ha_mod._GRAPH_LIST_OFFLOAD_CHUNK} — "
        f"raise MULTI_ORG_COUNT above the chunk size (or lower the chunk "
        f"size) so the loop is actually walked (#3718)")
    listings = ops.count("graph_list")
    assert listings == expected_chunks, (
        f"the listing used {listings} submission(s) for "
        f"{MULTI_ORG_COUNT + 1} healthy orgs, expected {expected_chunks} "
        f"chunk(s) of {ha_mod._GRAPH_LIST_OFFLOAD_CHUNK} — one unbounded "
        f"submission puts an uncapped fan-out under a single wait bound "
        f"(#3718); ops seen: {ops!r}")

    rows = response.json()
    assert len(rows) == MULTI_ORG_COUNT + 1, (
        f"the switcher returned {len(rows)} of {MULTI_ORG_COUNT + 1} orgs "
        f"({rows!r}) (#3718)")
    lost = [r["org_id"] for r in rows
            if r.get("graph_count") != 1
            or r.get("default_graph_id") != f"{r['org_id']}-default"]
    assert not lost, (
        f"these orgs lost their graph row: {lost!r} — a chunk's result was "
        f"overwritten instead of accumulated, so the switcher resolves no "
        f"default graph for them (#3718)")


def test_org_switcher_listing_never_builds_a_graph_handle_in_supabase_mode(
        monkeypatch):
    """#3718: the listing's mode branch must be at the CALL SITE.

    ``_registry_anchor()`` builds (and eagerly attaches) a FalkorDB handle
    BEFORE ``graph_list`` would consult the mode. Leaving the branch to the
    callee therefore made ``GET /v1/organizations`` — the first read every
    client makes — need a live graph server to resolve orgs that live entirely
    in PostgREST, and raise a raw ``ConnectionError`` (a 500, not the lane's
    503) when that server was down. Measured on the anchor shape with
    ``TORTOISE_CONTROL_PLANE=supabase`` and an unreachable graph URI.
    """
    import tortoise.hosted_api as ha_mod
    import tortoise.supabase_control as sb

    monkeypatch.setattr(sb, "is_supabase_enabled", lambda: True)
    monkeypatch.setattr(sb, "get_control_plane", lambda: "cp")
    monkeypatch.setattr(
        sb, "graph_metadata",
        lambda cp, org_id: [{"graph_id": f"{org_id}-pg"}])

    def _explode():
        raise AssertionError(
            "the listing built a FalkorDB registry handle in Supabase mode — "
            "the mode branch is missing at the call site (#3718)")

    monkeypatch.setattr(ha_mod, "_registry_anchor", _explode)

    assert ha_mod._graph_list_for("org-x") == [{"graph_id": "org-x-pg"}]

    monkeypatch.setattr(
        sb, "user_memberships",
        lambda cp, uid: [{"org_id": "org-x", "role": "owner"}])
    monkeypatch.setattr(
        sb, "org_by_id",
        lambda cp, org_id: {"id": org_id, "name": "X", "tier": "free"})
    monkeypatch.setattr(
        sb, "graph_metadata",
        lambda cp, org_id: [{"graph_id": f"{org_id}-pg", "kind": "default"}])

    # ROUTE level, not just the helper: the contract this pins is the endpoint's
    # ("never touches the registry in Supabase mode"). `_org_node` and
    # `_user_memberships` branch on the mode themselves, and the submission lane
    # is chosen on the loop, so the whole path must stay FalkorDB-free.
    #
    # BOTH lanes are patched with RECORDERS, not one passthrough: `_graph_offload`
    # delegates to `_cp_offload(pool="graph")`, so replacing only the latter
    # makes the graph lane indistinguishable from the control-plane lane at this
    # seam — and the wrong-lane regression this docstring is about (the round-5
    # P2: Supabase listings riding the graph lane under its cold-projection
    # bound) would keep this pin green. Measured: with the lane decision forced
    # to `_graph_offload`, the row assertion alone passes.
    lanes = {"cp": 0, "graph": 0}

    async def _cp_rec(fn, *, op, **_kwargs):
        lanes["cp"] += 1
        return fn()

    async def _graph_rec(fn, *, op, **_kwargs):
        lanes["graph"] += 1
        return fn()

    monkeypatch.setattr(ha_mod, "_cp_offload", _cp_rec)
    monkeypatch.setattr(ha_mod, "_graph_offload", _graph_rec)

    rows = asyncio.run(ha_mod.list_my_orgs({"user_id": "u-3718"}))

    assert [(r["org_id"], r["graph_count"], r["default_graph_id"])
            for r in rows] == [("org-x", 1, "org-x-pg")], (
        f"the Supabase-mode switcher did not answer from the control plane: "
        f"{rows!r} (#3718)")
    # 1 membership scan + 1 Team read (`_org_node`) + 1 listing submission.
    # The scan and the Team read are on THIS lane too (they branch on the mode
    # and enumerate no registry), which is why the count is 3 and not 1.
    assert lanes == {"cp": 3, "graph": 0}, (
        f"the Supabase switcher did not run on the control-plane lane only: "
        f"{lanes!r} (#3718)")

    # And the CHUNK is sized for THIS lane's bound, not the graph lane's: the
    # Supabase read is 3 PostgREST round trips per org under a 10 s bound, so
    # the same count the registry lane uses (8) would budget 24 against it.
    org_count = 9
    monkeypatch.setattr(
        sb, "user_memberships",
        lambda cp, uid: [{"org_id": f"org-{i}", "role": "owner"}
                         for i in range(org_count)])
    monkeypatch.setattr(
        sb, "org_by_id",
        lambda cp, org_id: {"id": org_id, "name": org_id, "tier": "free"})
    monkeypatch.setattr(
        sb, "graph_metadata",
        lambda cp, org_id: [{"graph_id": f"{org_id}-pg", "kind": "default"}])
    lanes.update(cp=0, graph=0)

    rows = asyncio.run(ha_mod.list_my_orgs({"user_id": "u-3718"}))

    expected_chunks = -(-org_count // ha_mod._GRAPH_LIST_CONTROL_PLANE_CHUNK)
    assert expected_chunks >= 2, (
        f"this pin's premise is broken: {org_count} orgs span only "
        f"{expected_chunks} chunk(s) (#3718)")
    assert lanes == {"cp": 1 + org_count + expected_chunks, "graph": 0}, (
        f"{org_count} Supabase orgs used lanes {lanes!r}, expected "
        f"{1 + org_count + expected_chunks} control-plane call(s) (1 scan + "
        f"{org_count} Team reads + {expected_chunks} chunks) and no graph-lane "
        f"call — the lane or the per-lane chunk size regressed (#3718)")
    assert len(rows) == org_count, (rows,)


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
_GRAPH_SEAM_CALLEES = frozenset({"_get_proj", "_get_registry", "dream_health_check",
                           # #3718: the org switcher's listing seam. Sync by
                           # design (it returns the rows), and its FalkorDB I/O
                           # is reached through the cached ``_registry_anchor()``
                           # — a name this scan does not otherwise know. Without
                           # this member the switcher's listing was invisible to
                           # the inventory: reverting it to an inline per-org
                           # call left ``_has_inline_graph_io(list_my_orgs)``
                           # EMPTY, so its ``_OFFLOADED_ASYNC_BODIES`` entry
                           # asserted nothing (measured).
                           "_graph_list_for"})
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
_SDK_CONSTRUCTION_SEAMS = frozenset({
    "_make_sdk", "_data_sdk",
    # #10180: the default-graph resolvers are CONSTRUCTION seams too. They
    # build the SDK through `_make_sdk`, so a body that opens its SDK through
    # one of them constructs on the loop exactly as an inline `_make_sdk` does
    # — scanning only the two spellings below would report the CORRECT shape
    # as clean and make this class blind to every lane the default-graph
    # resolver converted (`patch_onboarding_state` is the one that caught it).
    "_org_sdk", "_org_sdk_for_credential", "_org_sdk_from_name",
})
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
    # #4709: the index job's OWN-pool hand-off (``github_indexer``). Same
    # UNBOUNDED semantics as ``_run_off_loop``, but a dedicated pool instead
    # of the loop's shared default executor.
    "_run_index_off_loop",
    "_cp_offload", "_oauth_offload", "run_control_plane_call",
    "run_on_daemon_worker", "_graph_offload",
    # #3718: the per-MODE lane alias in ``list_my_orgs``
    # (``offload = _cp_offload if is_supabase_enabled() else _graph_offload``).
    # The scan sees the call through the local name, so the alias itself must be
    # declared here for the listing's ``_graph_list_for`` to be recognised as
    # OFF-loop — otherwise the correct shape is reported as a violation (the same
    # failure mode ``_graph_offload`` was added for in #4660).
    "offload",
})

#: Async bodies this change OFF-LOADS. A subset assertion: a rename or a
#: refactor that drops one back onto the loop fails `test_graph_io_is_offloaded`.
#: NOTE: the assertion is about the SEAMS THE SCAN SEES. ``patch_onboarding_state``
#: has them off-loaded — the ``_node_sdk._get_proj()`` read inside the nested
#: ``_read_node`` closure (reached because the closure is INVOKED on the loop)
#: and the ``_graph_has_org_namespace`` probe handed to ``to_thread`` by
#: reference — but its later onboarding WRITES still run through sync helpers
#: the scan cannot see; those are part of the residual, not covered by this set.
#: ⚠️ The same caveat is why ``_run_indexing`` is NOT here. #4709 took its
#: WALK and its two ``_update_onboarding_state`` writes off the loop (the walk
#: inside ``index_repo`` via ``_project_items`` — which also carries the O(file)
#: journal registration read as its first statement — the writes via
#: ``_run_off_loop(_INDEX_EXECUTOR, ...)`` + ``_echo=False``), but the
#: ``_make_sdk`` construction, the attach handed to ``backfill_legacy_closed``
#: and that scan ITSELF still run in the body,
#: and an attach that moved to a worker would resolve the embedded per-test
#: store from a reused pool thread's STALE stem (tortoise#7500, the defect that
#: reverted #4645's hand-offs). This set asserts NO construction and NO graph
#: seam, so membership would still be VACUOUS — the declaration the round-4
#: review removed. It stays in ``_KNOWN_INLINE_HELPER_RESIDUAL`` and
#: ``test_run_indexing_residual_is_named_head_on`` pins its exact remaining
#: sites so the claim cannot drift.
#: Async bodies whose sync FalkorDB I/O is off-loaded with NO residuals the
#: scan can see — neither a graph seam NOR an SDK construction on the loop.
#:
#: ⚠️ ``list_points``, ``get_point``, ``org_info``, ``list_sessions``,
#: ``get_session_detail``, ``dream_health``, ``list_api_keys``, ``list_members``,
#: ``list_pending_invites_for_me``, ``patch_onboarding_state`` and
#: ``invite_info`` were members of the residual until #4660 widened the scan:
#: their graph READS are off-loaded, but their ``_data_sdk`` /
#: ``_make_sdk(namespace="registry")`` CONSTRUCTION ran on the loop. #3718
#: burned down the six DATA-plane bodies of that group — ``list_points``,
#: ``get_point``, ``org_info``, ``list_sessions``, ``get_session_detail`` and
#: ``dream_health`` — and they are members of THIS set because their
#: construction is now off the loop too (``_data_sdk_offloaded`` / the raw
#: ``_graph_offload`` seam). The five that remain construction residuals —
#: ``list_api_keys``, ``list_members``, ``list_pending_invites_for_me``,
#: ``patch_onboarding_state``, ``invite_info`` — live in
#: ``_OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION`` below, each with a
#: recorded reason (registry-plane #2446 / worker-owned close), NOT in this set.
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
    # #3718: the org switcher's LISTING half. On the REGISTRY path its per-org
    # listings build NO handle of their own (they reuse the process-lifetime
    # ``_registry_anchor()``); on the SUPABASE path the read is PostgREST and
    # builds no graph handle at all. Either way the body reaches its FalkorDB
    # seam through the per-mode lane alias (``offload``, declared as a boundary
    # above) and through ``_graph_list_for`` (declared as a graph seam), so the
    # set's "no seam, no construction the scan can see" holds for the right
    # reason — and would fail if either went back inline (verified both ways).
    # The one anchor build on the REGISTRY request path is ``_org_node``'s, and
    # that helper stays INLINE under #2446 — which is why `_user_memberships` /
    # `_org_node` are in ``_KNOWN_INLINE_HELPER_RESIDUAL`` and NOT here.
    # Behavioural pins:
    # ``test_org_switcher_listing_runs_off_the_loop``,
    # ``test_org_switcher_listing_is_chunked_and_loses_no_row`` and
    # ``test_org_switcher_listing_never_builds_a_graph_handle_in_supabase_mode``.
    "list_my_orgs",
    # #3718 (construction half of #4660): the DATA-plane read handlers built
    # their per-request SDK on the loop while already off-loading the read it
    # serves. `_data_sdk_offloaded` (the #3773 twin) returns the identical SDK,
    # so these are one-thread-hand-off conversions with no semantic change;
    # `org_info` builds directly via `_make_sdk`, so it uses the raw
    # `_graph_offload` seam. Behavioural pins:
    # ``test_read_route_construction_is_offloaded`` — the count is
    # ``len(_CONSTRUCTION_CASES)``, derived, not restated here — each one RED on
    # the inline shape (`sdk_built_on_loop` True, thread MainThread).
    "list_points", "get_point", "org_info", "list_sessions",
    "get_session_detail", "dream_health",
    # #3718 (second construction batch): four more DATA-plane read routes whose
    # SDK was built on the loop — NOT the last ones, and this block's examples
    # are illustrations, not the inventory (see ``_KNOWN_INLINE_ROUTE_RESIDUAL``
    # and ``_OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION`` for that).
    # `list_packs`
    # (`_make_sdk(namespace=org_id)`) and `volunteer_context` (`_data_sdk(org)`)
    # still construct inline with an already-`to_thread` round trip, and stay in
    # ``_KNOWN_INLINE_ROUTE_RESIDUAL``; the registry-plane reads (``list_api_keys``,
    # ``list_members``, ``list_pending_invites_for_me``, ``invite_info``) stay in
    # ``_OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION``. `search` (the
    # primary read
    # path) and `topic_summary` already off-loaded the ROUND TRIP; their
    # construction did not. `session_context` (`GET /v1/context`, the memory
    # digest every HOSTED agent session-start hook reads — a local/embedded hook
    # calls `TortoiseSDK.session_context()` directly, `tortoise/__main__.py:3013`) and `issue_insight` called
    # the SDK INLINE, so their round trip moved too. Behavioural pins:
    # ``test_read_route_construction_is_offloaded`` — membership is
    # ``_CONSTRUCTION_OFFLOADED_READS``, derived, so nothing here is restated by
    # position — plus ``test_read_route_offloads_graph_io``, which REDs on the
    # inline shape only for ``session_context`` / ``issue_insight`` (their round
    # trip moved here) and is the ANTI-REGRESSION pin for ``search`` /
    # ``topic_summary`` (their round trip was already off the loop, so they
    # PASS pre-change — do not read that row as a failed conversion).
    "search", "topic_summary", "session_context", "issue_insight",
})

#: Async bodies whose GRAPH I/O is off-loaded but whose SDK CONSTRUCTION is
#: still on the loop (#4660). Declared, visible residual — NOT a claim that
#: these bodies are clean.
#:
#: #3718 burned down the six DATA-plane read bodies that used to be here
#: (``list_points``, ``get_point``, ``org_info``, ``list_sessions``,
#: ``get_session_detail``, ``dream_health``) — they now live in
#: ``_OFFLOADED_ASYNC_BODIES``. The five below are a DIFFERENT class and were
#: deliberately left, each for a recorded reason rather than for effort:
#:
#: * ``list_api_keys`` / ``list_members`` / ``list_pending_invites_for_me`` /
#:   ``invite_info`` construct on the REGISTRY plane (``_make_sdk(namespace=
#:   "registry")``), and registry reads carry the #2446 thread-dependence
#:   measurement: off-loading them made
#:   ``test_invites_http.py::TestExpiredInviteReMint`` see an EMPTY member list
#:   (inline passes 4/4), i.e. an auth-path read whose result depends on which
#:   thread runs it. Reproduce that measurement for THESE queries before
#:   converting them — the reference case lives in
#:   ``_KNOWN_INLINE_HELPER_RESIDUAL`` (``_membership_role``).
#: * ``patch_onboarding_state`` constructs inside a WORKER-OWNED close: its read
#:   closure closes the SDK in the worker's own ``finally`` (CPython #87185), so
#:   moving the construction means moving the close's subject too — a separate
#:   unit with its own proof, not a one-line swap.
#:
#: Two assertions hold for the membership, so this set can only shrink by
#: actually off-loading the construction:
#: * every member is FLAGGED by the widened scan — dropping a name without
#:   converting the body REDS ``test_graph_io_is_offloaded``;
#: * every member's GRAPH-seam hits are empty — re-inlining a read/attach in one
#:   of these bodies is still a failure, not an upgrade to "construction-only".
_OFFLOADED_ASYNC_BODIES_WITH_ON_LOOP_SDK_CONSTRUCTION = frozenset({
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
    # `search`, `topic_summary`, `session_context` and `issue_insight` left
    # this set in the #3718 second construction batch — their SDK construction
    # (and, for the latter two, their round trip) is off the loop now, so they
    # live in ``_OFFLOADED_ASYNC_BODIES`` where a regression reds instead of
    # passing as declared debt.
    "accept_invite_by_id", "agent_token_revoke", "create_graph", "create_org",
    "create_org_graph", "list_graphs", "list_invites",
    "list_packs", "list_trash", "rescind_invite",
    "upload_pack_manifest", "volunteer_context",
})

#: Non-route async bodies with inline sync FalkorDB I/O — the per-request auth
#: dependency `get_current_org` (6 sites, the single highest-traffic one), the
#: membership/owner gates, the capture path (`_capture_session_impl`) and the
#: registry/backup helpers. `_lifespan` was a member until #3718 residual 3
#: off-loaded its `_control_plane_source` resolve.
#:
#: `_run_indexing` is here (not in `_OFFLOADED_ASYNC_BODIES`) because, after
#: #4709, sites in its OWN body still run on the loop, and they are declared
#: residuals rather than omissions. Two are the tortoise#7500 constraint: the
#: `_make_sdk` construction, and the `org_sdk._get_proj()` attach passed to
#: `backfill_legacy_closed`. A worker-thread construction/attach resolves the
#: embedded per-test store path from a reused pool thread's STALE inherited
#: stem (epic #1686's currency check then fails closed to the non-per-test
#: path), so the job writes to a store the test's reader never looks at —
#: measured as 3 deterministic reds under #4645, whose hand-offs were
#: reverted for it. The attach can move only once #7500 lands.
#: `_relink_sessions_after_index` (the completion relink pass) is a third,
#: same constraint — a sync helper that builds its own SDK.
#: `backfill_legacy_closed`'s OWN one-time SCAN is the fourth and is NOT
#: #7500-bound: it takes the same `proj` handle, so it CAN move to the index
#: pool; it stays inline because the `github_legacy_backfill_done` marker
#: bounds it to ONE run per org. The WALK (now `index_repo._project_items`,
#: another module — it also carries the O(file) journal registration read as
#: its first statement) and the single cursor persist
#: (`_update_onboarding_state`, shared by the `completed` and `failed` exits,
#: on the index job's own pool — `_run_off_loop(_INDEX_EXECUTOR, ...)` — plus
#: `_echo=False`, and written BEFORE the terminal status)
#: ARE off the loop as of #4709. `test_run_indexing_residual_is_named_head_on`
#: pins exactly which calls may still sit inline here, so the walk or a write
#: cannot silently come back.
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
    # #2446: the membership read the owner/admin gate has always performed,
    # extracted into `_membership_role` by the own-key slice. Same read and same
    # class as `_membership_org` / `_require_owner_admin` above, so it belongs
    # here rather than in a new exemption. Off-loading it is measurably unsafe
    # today: both `asyncio.to_thread` and `_graph_offload` made
    # `test_invites_http.py::TestExpiredInviteReMint` see an EMPTY member list
    # (the inline form passes 4/4), i.e. an auth-path read whose result depends on
    # which thread runs it. Declared debt with that evidence attached beats a
    # silent behaviour change on an authorization path.
    "_membership_role",
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
    assert frozenset({"_make_sdk", "_data_sdk", "_org_sdk",
                      "_org_sdk_for_credential", "_org_sdk_from_name"}
                     ) == _SDK_CONSTRUCTION_SEAMS, (
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


def test_lane_alias_is_only_ever_bound_to_a_real_offload_seam():
    """#3718: the bare name ``offload`` in the boundary set must stay honest.

    ``offload`` is the switcher's per-MODE lane alias and the only
    non-underscore, non-imported member of ``_OFFLOAD_BOUNDARY_CALLEES``.
    Membership is matched on the call's NAME alone, with no scope, so if any
    future body in ``hosted_api`` binds ``offload`` to something that is NOT an
    offload seam, that body's sync I/O is skipped by both scans for free — a
    silent hole in the guard the alias exists to keep honest. Pin the binding
    sites instead of trusting the reader.
    """
    assert "offload" in _OFFLOAD_BOUNDARY_CALLEES, (
        "the switcher's per-mode lane alias is no longer declared a boundary — "
        "its `_graph_list_for` call now reads as on-loop I/O (#3718)")
    seams = {"_cp_offload", "_graph_offload"}
    assigned: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(HOSTED_API.read_text())):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value:
            targets, value = [node.target], node.value
        else:
            continue
        # Target names, POSITIONALLY: the alias is assigned in a tuple
        # (`offload, chunk_size = _cp_offload, <constant>`), so pairing by
        # index is what tells the seam from the chunk constant beside it.
        if len(targets) == 1 and isinstance(targets[0], (ast.Tuple, ast.List)):
            tgt_names = [e.id if isinstance(e, ast.Name) else None
                         for e in targets[0].elts]
        else:
            tgt_names = [t.id if isinstance(t, ast.Name) else None
                         for t in targets]
        if "offload" not in tgt_names:
            continue
        values = (value.elts if isinstance(value, (ast.Tuple, ast.List))
                  else [value])
        if len(values) != len(tgt_names):
            values = [value] * len(tgt_names)
        for name, v in zip(tgt_names, values, strict=True):
            if name == "offload":
                assigned.append((node.lineno, _callee_name(v)))
    assert assigned, (
        "the `offload` alias is gone from hosted_api — if the lane choice was "
        "rewritten, re-derive this pin rather than deleting it (#3718)")
    bad = [(line, name) for line, name in assigned if name not in seams]
    assert not bad, (
        f"`offload` is bound to something that is not an offload seam at "
        f"{bad!r} — the boundary set matches on the bare name, so that body's "
        f"sync I/O would be skipped by BOTH scans (#3718)")


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


# ── #4709 leg: the index walk + the onboarding writes ─────────────────────
#
# ``_run_indexing`` stays in ``_KNOWN_INLINE_HELPER_RESIDUAL``, so
# ``test_graph_io_is_offloaded`` cannot see a FIXED leg regress inside it —
# that allowlist is exactly why the walk could sit declared while running on
# the loop (#4709's preamble calls the old pass VACUOUS). The pins below are
# per-CALLEE, so they can.

INDEXER = (Path(__file__).resolve().parent.parent
           / "tortoise" / "indexer" / "github_indexer.py")

#: UNBOUNDED worker seams. A bound (`asyncio.wait_for` around the hand-off)
#: cancels only the AWAIT, never the worker thread (CPython #87185), so on a
#: MUTATING region it abandons a mid-write worker and claims a retryable state
#: it cannot observe (#2863) — the reason #4709 pins its walk and its
#: onboarding writes to one of these.
_UNBOUNDED_OFFLOAD_SEAMS = frozenset({
    "to_thread", "_run_off_loop", "_run_index_off_loop"})


def _offload_boundaries(node, callable_name: str) -> list[str]:
    """Names of offload boundaries carrying `callable_name` as a CALLABLE arg.

    Scanned at any position, so ``to_thread`` (callable at 0) and
    ``run_in_executor`` (callable at 1) both match.
    """
    out: list[str] = []
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        boundary = _callee_name(call.func)
        if boundary not in _OFFLOAD_BOUNDARY_CALLEES:
            continue
        for arg in call.args:
            if isinstance(arg, (ast.Name, ast.Attribute)) \
                    and _callee_name(arg) == callable_name:
                out.append(boundary)
    return out


def _boundary_calls_for(node, callable_name: str) -> list[ast.Call]:
    """The offload-boundary CALL nodes that carry `callable_name`."""
    return [
        call for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and _callee_name(call.func) in _OFFLOAD_BOUNDARY_CALLEES
        and any(isinstance(a, (ast.Name, ast.Attribute))
                and _callee_name(a) == callable_name for a in call.args)
    ]


def _carries_index_state_updates(call: ast.Call) -> bool:
    """True for a hand-off passing ``**_index_state_updates(...)`` (#4709).

    That ``**`` expansion is what identifies the CURSOR persist (the single
    ``_update_onboarding_state`` hand-off inside the walk try's ``finally``,
    shared by the `completed`, `failed` and `BaseException` exits). The other
    ``_update_onboarding_state`` hand-off passes the legacy-backfill MARKER
    instead, so it must not be allowed to satisfy the ordering assertion: it
    always precedes the status write, making a "some write is before
    `completed`" check true by construction.
    """
    return any(
        k.arg is None and isinstance(k.value, ast.Call)
        and _callee_name(k.value.func) == "_index_state_updates"
        for k in call.keywords)


#: Timeout wrappers that BOUND a seam. Membership in
#: ``_UNBOUNDED_OFFLOAD_SEAMS`` is by CALLEE NAME only, so
#: ``asyncio.wait_for(_run_off_loop(...), timeout=...)`` reads as the unbounded
#: seam. That is the exact shape the recorded NO-BOUND decision forbids, so the
#: pins check the seam's ANCESTORS too. ``asyncio.timeout`` is an async context
#: manager, so its real shape is ``async with asyncio.timeout(N): await seam`` —
#: the seam is in the ``with`` BODY, never in the call's arguments — and both
#: shapes are detected by ``_bound_wrapper_around`` below (#4709 review P3).
_BOUND_WRAPPER_CALLEES = frozenset({"wait_for", "timeout"})


def _bound_wrapper_around(call: ast.Call, node: ast.AST) -> str | None:
    """The timeout-wrapper callee enclosing ``call``, if any.

    Walks the PARENT chain (AST has no parent pointers) and reports either
    shape a bound can take:

    * a CALL wrapper whose arguments contain ``call`` —
      ``asyncio.wait_for(seam, timeout=...)``; and
    * a ``with``/``async with`` whose context expression is a bound callee and
      whose BODY contains ``call`` — ``async with asyncio.timeout(N): await
      seam`` (``asyncio.timeout`` is a context manager, so the seam is never an
      argument of its call and callee-name membership alone cannot see it).

    A wrapper in the same expression that does NOT enclose the call (e.g. a
    bound on some other await) is ignored.
    """
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(node):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    current: ast.AST = call
    while current in parents:
        parent = parents[current]
        if isinstance(parent, ast.Call) \
                and _callee_name(parent.func) in _BOUND_WRAPPER_CALLEES:
            args = [*parent.args, *(k.value for k in parent.keywords)]
            if any(current in ast.walk(a) for a in args):
                return _callee_name(parent.func)
        if isinstance(parent, (ast.With, ast.AsyncWith)) \
                and any(current in ast.walk(stmt) for stmt in parent.body):
            for item in parent.items:
                if isinstance(item.context_expr, ast.Call) \
                        and _callee_name(item.context_expr.func) \
                        in _BOUND_WRAPPER_CALLEES:
                    return _callee_name(item.context_expr.func)
        current = parent
    return None


def test_bound_wrapper_detects_both_timeout_shapes():
    """#4709 review P3: the NO-BOUND pin must see BOTH bound shapes.

    ``asyncio.wait_for(seam, timeout=...)`` puts the seam in the wrapper's
    ARGUMENTS; ``async with asyncio.timeout(N): await seam`` puts it in the
    ``with`` BODY. The pins' failure text names both, so both must be
    detectable — the pre-fix helper matched only the argument shape and left
    the context-manager spelling (``asyncio.timeout``, the modern one) green
    over a real bound (#2863/CPython #87185).
    """
    module = ast.parse(
        "async def f(seam):\n"
        "    await asyncio.wait_for(seam(), timeout=30)\n"
        "    async with asyncio.timeout(30):\n"
        "        await seam()\n"
        "    await seam()\n"
    )
    func = module.body[0]
    seam_calls = [
        c for c in ast.walk(func)
        if isinstance(c, ast.Call) and _callee_name(c.func) == "seam"]
    assert len(seam_calls) == 3, seam_calls
    assert {
        _bound_wrapper_around(c, func) for c in seam_calls
    } == {"wait_for", "timeout", None}, (
        "the `asyncio.wait_for(seam, ...)` ARGUMENT shape and the `async with "
        "asyncio.timeout(...): await seam` BODY shape must both be detected, "
        "and an unbound `await seam()` must still report None — pre-fix the "
        "`timeout` shape returned None, so the NO-BOUND pin stayed green "
        "over a real bound (#4709 review P3)")


def test_run_indexing_residual_is_named_head_on():
    """#4709: the walk AND the onboarding writes are off the loop, on a
    NO-BOUND seam; whatever is still inline is pinned by name.

    ``_run_indexing`` is a declared residual body, so the inventory scan is
    blind to a fixed leg regressing inside it. This pin is not.
    """
    node = _async_body_named("_run_indexing")

    # (1) the onboarding writes: OFF the loop, NOT behind a bound, and the
    # cursor persist ordered BEFORE the terminal (`completed`/`failed`) status.
    assert _inline_calls_to(
        node, frozenset({"_update_onboarding_state"})) == [], (
        "#4709 regressed: `_run_indexing` writes onboarding state INLINE — a "
        "mutating jsonb compare-and-set on the event loop (the leg #4625 "
        "measured); off-load it on the index job's own pool "
        "(`_run_off_loop(_INDEX_EXECUTOR, ...)`)")
    write_seams = _offload_boundaries(node, "_update_onboarding_state")
    assert len(write_seams) == 2, (
        f"#4709: expected TWO `_update_onboarding_state` hand-offs (the "
        f"legacy-backfill marker; the single cursor persist shared by the "
        f"`completed` and `failed` exits), found {len(write_seams)}")
    assert all(s in _UNBOUNDED_OFFLOAD_SEAMS for s in write_seams), (
        f"#4709: an onboarding write rides a BOUNDED seam {write_seams} — a "
        f"bound cancels only the await, never the worker (CPython #87185), so "
        f"it abandons a worker MID-WRITE (#2863); the recorded decision is "
        f"NO BOUND")
    # `_UNBOUNDED_OFFLOAD_SEAMS` matches by CALLEE NAME, so a bound wrapped
    # AROUND the seam (`asyncio.wait_for(_run_off_loop(...), timeout=...)`)
    # reads as the unbounded seam and passes the check above. Check the
    # seam's ANCESTORS too, or the NO-BOUND decision it claims is unenforced.
    for _write_call in _boundary_calls_for(node, "_update_onboarding_state"):
        _wrapper = _bound_wrapper_around(_write_call, node)
        assert _wrapper is None, (
            f"#4709: an onboarding write is wrapped in `{_wrapper}` — the "
            f"recorded decision is NO BOUND (a bound cancels only the AWAIT, "
            f"never the worker — CPython #87185 — so it abandons a worker "
            f"MID-WRITE, #2863)")

    # #4709 ordering guarantee: the single cursor persist must run BEFORE the
    # terminal status write, for `completed` AND `failed`. Persisting only in
    # the `finally` meant the status was visible to a poller before the cursor
    # was durable — masked while the write ran on the loop (it blocked the
    # poller's GET), a real window once the write is off the loop. A client
    # reading state on a terminal status (the dashboard, and
    # `test_github_index_lifecycle.py`) then sees the PREVIOUS cursor and
    # re-walks the whole backlog.
    completed_lines = [
        c.lineno for c in ast.walk(node)
        if isinstance(c, ast.Call) and _callee_name(c.func) == "_job"
        and any(k.arg == "status" and getattr(k.value, "value", None)
                == "completed" for k in c.keywords)
    ]
    assert len(completed_lines) == 1, (
        f"#4709: expected ONE `_job(status='completed', ...)` write, found "
        f"{len(completed_lines)}")
    # `_echo=False` is part of the off-load, not a style choice: the return is
    # DISCARDED here, and the echo's registry probes are the expensive half of
    # the same loop-blocking leg (#4625).
    write_calls = _boundary_calls_for(node, "_update_onboarding_state")
    assert write_calls and all(
        any(k.arg == "_echo" and getattr(k.value, "value", None) is False
            for k in call.keywords) for call in write_calls), (
        "#4709: an off-loaded onboarding write does not pass `_echo=False` — "
        "the return is discarded, and the echo is "
        "`_get_onboarding_projection`'s blocking registry read (#4625)")
    # #4709 ordering guarantee, pinned on the NAMED calls. It must NOT be
    # satisfiable by the other hand-off: the legacy-backfill marker always
    # precedes the status write, so a "SOME write is before the status" check
    # is true by construction and cannot fail for the regression it names.
    # There is exactly ONE `**_index_state_updates(...)` persist — the cursor
    # one — and it must precede BOTH terminal status writes.
    state_persist_calls = [
        c for c in write_calls if _carries_index_state_updates(c)]
    assert len(state_persist_calls) == 1, (
        f"#4709: expected ONE `_update_onboarding_state(..., "
        f"**_index_state_updates(...))` persist shared by BOTH terminal "
        f"exits, found {len(state_persist_calls)} — a second site means the "
        f"persist was duplicated (or re-split across the `finally`), so the "
        f"completion/failed paths can diverge again")
    persist_line = state_persist_calls[0].lineno
    failed_lines = [
        c.lineno for c in ast.walk(node)
        if isinstance(c, ast.Call) and _callee_name(c.func) == "_job"
        and any(k.arg == "status" and getattr(k.value, "value", None)
                == "failed" for k in c.keywords)
    ]
    assert failed_lines, (
        "#4709: no `_job(status='failed', ...)` write found — the failure "
        "arms regressed or moved somewhere the ordering pin cannot see")
    assert persist_line < completed_lines[0], (
        "#4709 ordering regressed: the CURSOR persist "
        "(`_update_onboarding_state(..., **_index_state_updates(...))`) does "
        "NOT run BEFORE `_job(status='completed', ...)` — the job reports "
        "`completed` while the cursor is still only in the tail, so a "
        "client that reads state on `completed` sees the previous cursor and "
        "re-walks the whole backlog")
    assert persist_line < max(failed_lines), (
        "#4709 ordering regressed: the CURSOR persist runs AFTER the tail "
        "`_job(status='failed', ...)` write — the failure path reports "
        "`failed` before the now-off-loop persist lands, so a poller that "
        "reads state on `failed` (test_midwalk_fetch_error_stamps_indexed_at) "
        "observes the PREVIOUS cursor and re-walks the whole backlog; "
        "pre-#4709 this window was masked because the persist ran ON the loop")

    # (2) what stays on the loop is pinned as an EXACT count so no site can
    # appear or leave unnoticed. Most are the tortoise#7500 constraint (the SDK
    # construction, the attach handed to `backfill_legacy_closed`, the relink
    # pass); `backfill_legacy_closed`'s OWN one-time scan is a declared
    # residual rather than a #7500 site — it can be off-loaded (same `proj`
    # handle) and is only left inline because the
    # `github_legacy_backfill_done` marker bounds it to one run per org.
    # Off-loading or re-siting any of these fails here: update the pin WITH the
    # residual AND the docstring in `hosted_api._run_indexing`.
    inline = _inline_calls_to(node, frozenset({
        "_make_sdk", "_data_sdk", "_org_sdk_from_name", "_get_proj",
        "_relink_sessions_after_index", "backfill_legacy_closed",
    }))
    assert inline.count("_org_sdk_from_name") == 1, (
        f"#4709/#10180: `_run_indexing` has "
        f"{inline.count('_org_sdk_from_name')} on-loop SDK construction(s); "
        "there must be exactly ONE, and it must be CONSTRUCTED here — the "
        "documented tortoise#7500 residual. The #10180 default-graph NAME "
        "read may (and does) run in a worker via `_cp_offload`, but the "
        "construction may not: in a pooled worker `_make_sdk` resolves the "
        "per-test store from a stale pool-thread stem and the job writes "
        "where the test's reader never looks (#7500). A count of 0 means the "
        "construction was off-loaded; 2 means a second site appeared")
    assert inline.count("_make_sdk") == 0, (
        "#4709/#10180: `_run_indexing` builds its SDK inline again — go "
        "through `_org_sdk_from_name` so the default-graph decision and the "
        "#7500 construction constraint stay in ONE place")
    assert inline.count("_data_sdk") == 0, (
        "#4709: `_run_indexing` must not build its SDK through `_data_sdk` — "
        "that is the ROUTE tenancy resolver, not the job's construction seam")
    assert inline.count("_get_proj") == 1, (
        f"#4709: `_run_indexing` has {inline.count('_get_proj')} inline graph "
        "attach(es); the single one is the `backfill_legacy_closed` attach — "
        "the tortoise#7500 residual. The WALK's attach happens inside "
        "`index_repo`; a second attach here means the walk came back")
    assert inline.count("_relink_sessions_after_index") == 1, (
        "#4709: the completion relink pass is still a declared inline "
        "residual (it builds its own SDK — same #7500 constraint); if it was "
        "off-loaded, record that here")
    assert inline.count("backfill_legacy_closed") == 1, (
        f"#4709: `_run_indexing` has {inline.count('backfill_legacy_closed')} "
        "inline `backfill_legacy_closed` scan(s); the single one is a declared "
        "on-loop residual (two full-:Event reads plus a journaled mint per row, "
        "`indexer/github_indexer.py`), bounded to ONE run per org by the "
        "`github_legacy_backfill_done` marker. It is NOT a #7500 site — it "
        "takes the `proj` handle, so it CAN move to the index pool; if it was "
        "off-loaded (or a second site appeared), record that here and in the "
        "`hosted_api._run_indexing` docstring")


def test_index_repo_projection_walk_is_offloaded():
    """#4709: `index_repo` hands its synchronous projection walk to ONE
    unbounded worker seam, and the per-item loop is not in the async body.

    The walk is the dominant graph work #4709 was opened for; it lives one
    module down, where the hosted AST scan cannot see it — hence this pin.
    """
    tree = ast.parse(INDEXER.read_text())
    index_repo = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "index_repo")
    walk = next((n for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef)
                 and not isinstance(n, ast.AsyncFunctionDef)
                 and n.name == "_project_items"), None)
    assert walk is not None, (
        "#4709: the walk's sync half (`_project_items`) is missing or is an "
        "`async def` — handing an async def to a worker seam returns an "
        "un-awaited coroutine, so the walk would silently never run")

    assert _offload_boundaries(
        index_repo, "_project_items") == ["_run_index_off_loop"], (
        "#4709 regressed: `_project_items` is not handed to the index job's "
        "own UNBOUNDED pool (`_run_index_off_loop`) — the projection walk runs "
        "on the event loop (the 600-item fixture cannot complete inside the "
        "10 s transport bound), rides a BOUNDED seam that would abandon a "
        "mid-write worker (#2863), or is back on the SHARED default executor "
        "(`asyncio.to_thread`), where it starves the pool's other tenants")
    walk_calls = _boundary_calls_for(index_repo, "_project_items")
    assert walk_calls, (
        "#4709: no offload boundary carries `_project_items` — the bound "
        "check below would be vacuous")
    for _walk_call in walk_calls:
        _wrapper = _bound_wrapper_around(_walk_call, index_repo)
        assert _wrapper is None, (
            f"#4709: the projection walk is wrapped in `{_wrapper}` — "
            f"`_UNBOUNDED_OFFLOAD_SEAMS` matches by CALLEE NAME, so a bound "
            f"around the seam is invisible to it; the recorded decision is "
            f"NO BOUND (#2863/CPython #87185)")
    assert _inline_calls_to(index_repo, frozenset({"_project_items"})) == [], (
        "#4709: `_project_items` is also INVOKED inline in `index_repo` — the "
        "walk would run twice, once on the loop")
    assert _inline_calls_to(
        index_repo, frozenset({"_project_issue", "_project_pr"})) == [], (
        "#4709 regressed: the per-item projection loop is back in the ASYNC "
        "body of `index_repo` — it must live in the sync `_project_items`")
    # #7951 P2-a / #4709: the journal registration read (`EventLog.read_all()`
    # over the WHOLE, unbounded journal) used to run inline on the loop just
    # above the hand-off. It feeds the walk's entity door, so it rides the
    # SAME worker hand-off — pinned both ways (absent from the async body,
    # present in the sync walk) so a re-siting is visible.
    assert _inline_calls_to(
        index_repo, frozenset({"_journal_registered_entities"})) == [], (
        "#4709 regressed: `index_repo` reads the whole journal "
        "(`_journal_registered_entities` -> `EventLog.read_all()`, O(file)) "
        "INLINE on the event loop — it must ride the walk hand-off as the "
        "first statement of the sync `_project_items`")
    assert _inline_calls_to(
        walk, frozenset({"_journal_registered_entities"})) == [
            "_journal_registered_entities"], (
        "#4709: the sync walk no longer reads the journal registrations — the "
        "entity door's pre-fix-binary backfill (#7951 P2-a) would silently "
        "degrade, or the read moved somewhere neither scan can see")
    in_walk = _inline_calls_to(
        walk, frozenset({"_project_issue", "_project_pr"}))
    assert sorted(set(in_walk)) == ["_project_issue", "_project_pr"], (
        f"#4709: the sync walk does not carry the projection calls "
        f"({sorted(set(in_walk))}) — this pin would be vacuous")


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
    * ZERO inline ``_data_sdk``: the leg-72 (``count_org_usage``) site was the
      last one, declared a residual because the required #4282 collision
      pre-flight returned COLLISION. It now reuses the SDK leg 62 already
      opened off-loop and rides ``_graph_offload``, so the residual is closed
      and this pin tightened from ``== 1`` to ``== 0`` — reverting that leg
      re-reds HERE as well as in the behavioural case
      (``test_capture_loop_responsiveness.py::
      test_capture_points_estimate_reads_off_the_event_loop``).
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
    assert callees.count("_data_sdk") == 0, (
        "#4625: `_capture_session_impl` has "
        f"{callees.count('_data_sdk')} inline `_data_sdk` call(s); leg 62 must "
        "use `_data_sdk_offloaded` and leg 72 must reuse the SDK that opened, "
        "not re-resolve tenancy on the loop (the leg-72 residual is closed)"
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
