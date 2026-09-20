"""#2988 — /health/ready must never block the event loop.

Production, 2026-09-11: every route in the process (``/openapi.json`` included)
timed out for 15 minutes while ``/proc/loadavg`` was 0.01 and the database
answered PING in 0.38s — an idle process blocked on I/O with the loop held.
Cause: ``health_ready`` ran both plane probes as SYNCHRONOUS calls in the
coroutine body, so one stalled socket froze the whole process, and every deploy
curls this endpoint (a self-inflicted outage vector).

Two complementary guards:

* the AST pins — structural, so the old shape cannot come back through a
  refactor that keeps the observable response intact (a behavioural test alone
  cannot see the difference: the response is identical when nothing stalls);
* the behavioural tests — prove the added indirection actually works, and that
  a hung probe is REPORTED (503) instead of waited out.
"""

from __future__ import annotations

import ast
import asyncio
import math
import re
import time
import tomllib
from pathlib import Path

import pytest
from fastapi import HTTPException

REPO = Path(__file__).resolve().parent.parent
HOSTED_API = REPO / "tortoise" / "hosted_api.py"
SELFHOST = REPO / "tortoise" / "selfhost.py"
SUPABASE_CONTROL = REPO / "tortoise" / "supabase_control.py"


def _handler(name: str, source: Path = HOSTED_API) -> ast.AsyncFunctionDef:
    tree = ast.parse(source.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {source.name}")


def _parents(node: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)}


def _walk_own_body(node: ast.AST):
    """Walk the coroutine's OWN statements, not the bodies of functions it
    dispatches. A probe nested in the handler is exactly where the synchronous
    call is supposed to live — flagging it would make the pin unsatisfiable and
    would push the real call back onto the loop.
    """
    for stmt in getattr(node, "body", []):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        yield from ast.walk(stmt)


# ── structural pins ────────────────────────────────────────────────────────


def test_handler_makes_no_direct_query_call():
    """``.query(...)`` is synchronous network I/O — it belongs in the module
    level probes, never in the coroutine body. This is the exact line that
    caused the outage (``sdk._get_proj().g.query("RETURN 1")``), and its
    control-plane sibling."""
    node = _handler("health_ready")
    offenders = [
        n.lineno
        for n in _walk_own_body(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "query"
    ]
    assert not offenders, (
        f"health_ready calls .query(...) directly at line(s) {offenders} — "
        "that runs synchronous network I/O on the event loop and freezes every "
        "other request (#2988)"
    )


# ── #3498: NAME-BASED control-plane offload inventory ──────────────────────
#
# The #2988 pin above matches ``ast.Attribute`` / ``attr == "query"`` inside
# ONE handler. It cannot see a bare ``Name`` call (``user_memberships(cp, uid)``),
# it cannot see a middleware body, and it cannot see any handler but
# ``health_ready`` — which is exactly how the #3498 auth/REST blockers survived
# it. This pin is NAME-BASED: every ``AsyncFunctionDef`` body (a Starlette
# middleware ``dispatch`` is one) is scanned for a direct call to a declared
# blocking control-plane seam helper. Such a call is allowed only inside an
# offload boundary — the #3498 ``_cp_offload`` / ``monitoring.run_control_plane_call``
# seam, or the pre-existing ``run_on_daemon_worker`` / ``asyncio.to_thread``.
#
# BOUNDARY, stated rather than implied: the inventory is the DECLARED
# auth/REST seam of #3498 (§A1 of the design review) plus the session/DI and
# key-write helpers this change routes — not every blocking call in the file.
# The data-plane FalkorDB ``.query(...)`` sites are #3086's lane; the remaining
# on-loop control-plane helper calls in other endpoints (invitations, members,
# identity linking, agent signup) are #4350; and the in-lock mint calls in
# ``_session_key_supabase`` cannot await under the synchronous ``_org_mint_lock``.
# A pin claiming to cover all of them would have to enumerate ~50 call sites and
# restructure the mint lock — a rewrite, not a guard. What this pin DOES do is
# fail on the *next* call site that uses one of these seam helpers — the way
# this defect regrew three times (#2988, #3035, #3086).
CONTROL_PLANE_OFFLOAD_INVENTORY = frozenset({
    "resolve_api_key",          # key-auth: 2-3 dependent PostgREST round-trips
    "update_last_used",         # key-auth: the last_used_at PATCH (best-effort)
    "user_memberships",         # session lane: membership rows
    "membership_for_user_org",  # session/DI + login/claim lanes
    "_orgs_row_fail_soft",      # session/DI lane: the orgs additive ladder
    "org_by_id",                # session/DI + invite/onboarding lanes
    "_org_node_sync_limits",    # session/DI lane: org limit props (org_by_id)
    "api_key_by_id",            # key-write lanes: the key lookup
    "set_dashboard_key_login",  # dashboard-login + provisioning flag write
    "_resolve_signup_token",    # recovery lane: signup-token resolution
    "_track_analytics_event",   # analytics lane: fresh httpx.Client per event
    "_github_repos_count",      # github_status: blocking api.github.com call
})

#: The §A1-confirmed seam helpers that must never be silently dropped from the
#: inventory (a subset assertion, so the pin cannot shrink to nothing).
_A1_CONFIRMED_SEAMS = frozenset({
    "resolve_api_key", "update_last_used", "user_memberships",
    "_orgs_row_fail_soft", "_track_analytics_event", "_github_repos_count",
})

#: Helpers this change ROUTES in addition to §A1 (the session/DI seams
#: ``_membership_org`` / ``_org_node`` / ``_require_owner_admin`` and the
#: key-write/login/claim lanes). If a name leaves the inventory its routed
#: sites lose their regression guard, so the pin asserts they stay.
_ROUTED_SESSION_SEAMS = frozenset({
    "membership_for_user_org", "org_by_id", "api_key_by_id",
    "set_dashboard_key_login", "_org_node_sync_limits",
    "_resolve_signup_token",
})

#: Callees that OFFLOAD their argument — a call nested inside one of these is
#: not on the loop, so the walk does not descend into it.
OFFLOAD_BOUNDARY_CALLEES = frozenset({
    "_cp_offload", "run_control_plane_call", "run_on_daemon_worker", "to_thread",
})


def _callee_name(func: ast.expr) -> str | None:
    """The bare name of a call target — ``Name.id``, or the attribute for
    ``asyncio.to_thread`` / ``monitoring.run_control_plane_call``."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _module_aliases(tree: ast.AST) -> dict[str, str]:
    """Module-level ``from ... import X as Y`` aliases (Y → X)."""
    aliases: dict[str, str] = {}
    for stmt in getattr(tree, "body", []):
        if isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name
    return aliases


def _unoffloaded_calls(node: ast.AST,
                       module_aliases: dict[str, str] | None = None) -> list[tuple[str, ast.Call]]:
    """Every ``ast.Call`` in ``node``'s OWN body that is not itself an offload
    boundary and is not nested inside one, as ``(resolved_callee, call)``.

    The callee name is RESOLVED through ``from ... import X as Y`` aliases
    (module-level and local to the body), so ``_sb_memberships(...)`` (an alias
    of ``user_memberships``) is seen for what it is — an alias is exactly how
    the session lane smuggles one of these calls past a naive name match.

    Nested ``def``/``async def``/``class`` bodies are skipped: a blocking call
    in a nested function belongs to that function's own inventory entry (and
    the pre-existing #2988 pin covers the probe case deliberately).
    """
    aliases = dict(module_aliases or {})
    for stmt in getattr(node, "body", []):
        if isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name

    def resolved(func: ast.expr) -> str | None:
        name = _callee_name(func)
        if name is None:
            return None
        return aliases.get(name, name)

    found: list[tuple[str, ast.Call]] = []

    def rec(parent: ast.AST) -> None:
        for child in ast.iter_child_nodes(parent):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(child, ast.Call):
                if _callee_name(child.func) in OFFLOAD_BOUNDARY_CALLEES:
                    continue  # offloaded — do NOT descend into the argument
                name = resolved(child.func)
                if name is not None:
                    found.append((name, child))
            rec(child)

    for stmt in getattr(node, "body", []):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        rec(stmt)
    return found


def _async_bodies(tree: ast.AST):
    """Every async function/method — Starlette middleware ``dispatch`` included."""
    return [n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)]


def test_control_plane_seam_calls_are_all_offloaded():
    """#3498 item 3: a NAME-BASED inventory over EVERY async body.

    This is the pin the #2988 guard should have been: it sees
    ``user_memberships(cp, uid)`` (a bare ``Name``, invisible to the
    ``attr == "query"`` match), it sees middleware bodies, and it sees every
    handler — so the #3498 shape cannot reappear on the next call site.
    """
    tree = ast.parse(HOSTED_API.read_text())
    bodies = _async_bodies(tree)
    aliases = _module_aliases(tree)
    assert len(bodies) > 50, (
        f"only {len(bodies)} async bodies parsed — the scan is not seeing the "
        "hosted surface it is supposed to guard"
    )
    offenders = [
        (node.name, call.lineno, name)
        for node in bodies
        for name, call in _unoffloaded_calls(node, aliases)
        if name in CONTROL_PLANE_OFFLOAD_INVENTORY
    ]
    assert not offenders, (
        "synchronous control-plane call(s) made directly from an async body "
        f"(name, line, callee): {offenders} — route them through _cp_offload / "
        "run_control_plane_call so PostgREST I/O never runs on the event loop "
        "(#3498)"
    )


def test_no_async_body_builds_a_synchronous_httpx_client():
    """The analytics lane's shape: a fresh ``httpx.Client(timeout=5)`` built
    inside the calling coroutine. Any async body that constructs a sync client
    is doing blocking I/O on the loop."""
    tree = ast.parse(HOSTED_API.read_text())
    aliases = _module_aliases(tree)
    offenders = [
        (node.name, call.lineno)
        for node in _async_bodies(tree)
        for _name, call in _unoffloaded_calls(node, aliases)
        if isinstance(call.func, ast.Attribute)
        and call.func.attr == "Client"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "httpx"
    ]
    assert not offenders, (
        f"async body constructs a synchronous httpx.Client at {offenders} — "
        "that runs blocking I/O on the event loop (#3498)"
    )


def test_offload_inventory_names_still_exist():
    """A rename or deletion must fail HERE, not silently vacate the pin."""
    defined = {
        n.name
        for path in (SUPABASE_CONTROL, HOSTED_API)
        for n in ast.walk(ast.parse(path.read_text()))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = CONTROL_PLANE_OFFLOAD_INVENTORY - defined
    assert not missing, (
        f"the #3498 offload inventory names {sorted(missing)}, which no longer "
        "exist — a rename must update the inventory, or the pin silently "
        "guards nothing"
    )
    assert _A1_CONFIRMED_SEAMS <= CONTROL_PLANE_OFFLOAD_INVENTORY, (
        "a §A1-confirmed seam helper was dropped from the offload inventory"
    )
    assert _ROUTED_SESSION_SEAMS <= CONTROL_PLANE_OFFLOAD_INVENTORY, (
        "a session/DI seam helper this change routed was dropped from the "
        "offload inventory"
    )


def test_detector_flags_a_bare_name_call_and_ignores_an_offloaded_one():
    """Self-test of the detector (a pin that can never fail is not a pin).

    The first call is the exact #3498 shape the attribute-based guard missed;
    the second is the same helper behind the offload seam; the third is the
    SAME call under a ``from ... import ... as`` alias — the session lane's
    ``user_memberships as _sb_memberships``.
    """
    src = (
        "from tortoise.supabase_control import user_memberships as _sb\n"
        "async def handler():\n"
        "    rows = user_memberships(cp, uid)\n"
        "    more = await _cp_offload(lambda: user_memberships(cp, uid))\n"
        "    alias = _sb(cp, uid)\n"
    )
    node = ast.parse(src).body[1]
    aliases = _module_aliases(ast.parse(src))
    hits = [
        (call.lineno, name)
        for name, call in _unoffloaded_calls(node, aliases)
        if name in CONTROL_PLANE_OFFLOAD_INVENTORY
    ]
    assert hits == [(3, "user_memberships"), (5, "user_memberships")], (
        f"detector must flag the un-offloaded bare-Name AND aliased calls, got {hits}"
    )


def test_detector_scans_a_middleware_dispatch_body():
    """The inventory must cover a middleware body — the issue's other stated
    gap in the #2988 pin."""
    src = (
        "class M:\n"
        "    async def dispatch(self, request, call_next):\n"
        "        _track_analytics_event('', 'x')\n"
        "        return await call_next(request)\n"
    )
    bodies = _async_bodies(ast.parse(src))
    assert [b.name for b in bodies] == ["dispatch"]
    hits = [
        name
        for name, _call in _unoffloaded_calls(bodies[0])
        if name in CONTROL_PLANE_OFFLOAD_INVENTORY
    ]
    assert hits == ["_track_analytics_event"]


def test_both_probes_are_dispatched_through_their_coordinators():
    """#2850 x #2988 — ``health_ready`` must not run either plane probe inline.

    The #2988 guard pinned ``asyncio.to_thread(_probe_db)``. #2850 replaced that
    with dedicated single-flight coordinators on a private daemon worker, which
    is strictly stronger: ``to_thread`` rides the SHARED default executor, so a
    timed-out probe leaks a worker out of the pool every other request depends
    on, and the submission queue is unbounded. The INVARIANT this pins is
    unchanged — the handler must not perform the synchronous network I/O itself
    — so the mechanism pin moves with the mechanism instead of being dropped.
    """
    node = _handler("health_ready")
    dispatched = {
        call.func.value.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "run"
        and isinstance(call.func.value, ast.Name)
    }
    assert {"_READY_PROBE", "_CONTROL_PLANE_PROBE"} <= dispatched, (
        f"probes not dispatched through their coordinators (found {sorted(dispatched)})"
    )
    # No to_thread fallback for the plane probes may creep back in.
    inline = [
        arg.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "to_thread"
        for arg in call.args
        if isinstance(arg, ast.Name)
    ]
    assert "_probe_db" not in inline and "_probe_control_plane" not in inline, (
        f"a plane probe is dispatched via the SHARED default executor ({sorted(inline)}) — "
        "use the dedicated HealthProbe coordinators instead"
    )


# Repo-owned POLICY ceiling for /health/ready's SEQUENTIAL worst case
# (``DB_PROBE_HARD_TIMEOUT + CONTROL_PLANE_HARD_TIMEOUT``).
#
# This is deliberately NOT derived from fly.toml. The historical proxy was that
# file's 15s ``[[services.http_checks]] timeout`` — removed by #2850 (2026-09-10)
# because the /health HTTP check flapped and de-registered the sole machine. Its
# successor is a TCP check whose 5s timeout times a KERNEL accept and is
# documented as "generous headroom, not a latency budget", and deploy-hosted.yml
# curls /health/ready with no ``--max-time`` at all. So there is no longer ANY
# external quantity this can be compared against.
#
# Deleting the assertion instead would have silently unbounded the sum: the
# per-plane asserts below compare each bound to its own inner total, so doubling
# CONTROL_PLANE_HARD_TIMEOUT would pass every remaining assertion. Keeping an
# explicit policy constant preserves that tripwire without pretending the number
# is deploy-derived. 15.0s is the budget the old routing check implied; revisit
# when a real deadline exists (add ``--max-time`` to that curl — #2850 follow-up).
READY_WORST_CASE_BUDGET_S = 15.0


def _fly_check_budget_proxy_s() -> float | None:
    """fly.toml's configured ``[[services.http_checks]] timeout``, in seconds.

    NOTE this check targeted ``/health`` — pure in-memory — NOT ``/health/ready``.
    It was borrowed only as a coarse, repo-owned BUDGET PROXY for the readiness
    surface: ``deploy-hosted.yml`` curls ``/health/ready`` with no
    ``--max-time``, so there is no real deadline for it anywhere.

    Returns ``None`` ONLY when fly.toml genuinely configures no ``http_checks``
    at all (the deliberate #2850 migration). EVERY other state that leaves the
    budget configured-but-unusable RAISES.

    Collapsing those into ``None`` would silently disarm the ceiling: the
    caller's ``else`` branch passes whenever ``tcp_checks`` is present, so
    "budget absent" and "budget unreadable" must never share a return value.
    A typo like ``timeout = "15x"`` — or a non-finite ``inf``, which would make
    ``ready_worst_case < ceiling`` trivially true — has to FAIL LOUDLY rather
    than quietly skip the assertion this test exists to make.
    """
    path = REPO / "fly.toml"
    assert path.exists(), (
        f"fly.toml is missing at {path} — cannot read the health budget proxy"
    )
    try:
        services = tomllib.loads(path.read_text())["services"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AssertionError(
            f"fly.toml has no readable services block ({exc!r}) — the "
            "cross-endpoint ceiling cannot be evaluated"
        ) from exc
    # This proxy reads ``services[0]``. If fly.toml ever grows a second
    # [[services]] block carrying the http_checks budget, services[0] would
    # have none, the guard below would return None, and the caller's else
    # branch (which only asks services[0] for tcp_checks) would pass — the
    # same silent disarm, reached a different way. Pin the single-service
    # assumption so an unexpected shape fails loudly instead.
    assert isinstance(services, list) and len(services) == 1, (
        "fly.toml must define exactly ONE [[services]] block for the "
        f"cross-endpoint budget proxy to be meaningful; found {services!r}"
    )
    svc = services[0]
    assert isinstance(svc, dict), (
        f"fly.toml services[0] is not a table ({svc!r}) — the cross-endpoint "
        "ceiling cannot be evaluated"
    )
    # #2850 (2026-09-10) removed [[services.http_checks]] deliberately: the
    # /health HTTP check flapped and de-registered the sole machine, costing
    # ~35 min of unreachability while the process was alive on loopback. It
    # was replaced by [[services.tcp_checks]], whose ``timeout`` (5s) is
    # documented in fly.toml as "generous headroom, not a latency budget" —
    # it times a KERNEL accept, not an application response, so borrowing it
    # as a /health/ready budget proxy would be a different quantity entirely
    # (and smaller than the ready worst case, so it cannot serve as a ceiling).
    # There is consequently NO HTTP-check budget left to compare against here.
    # The top-level ``[checks.loop_liveness]`` (shipped #3447) does NOT restore
    # one: it is a loop-liveness check (fly.toml documents its timeout as 5s,
    # below the ~11.6s sum), it is a TOP-LEVEL ``[checks]`` entry rather than a
    # ``services[0].http_checks`` one, and this reader does not consume it. The
    # cross-endpoint bound now lives in ``READY_WORST_CASE_BUDGET_S``, and the
    # caller's else branch MODELS that one entry explicitly (identity, bounds,
    # and that it does not feed the ceiling) while still failing closed on any
    # OTHER top-level ``[checks]`` entry.
    if "http_checks" not in svc:
        return None
    try:
        raw = svc["http_checks"][0]["timeout"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AssertionError(
            "fly.toml configures http_checks but its [0].timeout is unreadable "
            f"({svc['http_checks']!r}) — the cross-endpoint ceiling is "
            "being silently disarmed; fix the reader or the config"
        ) from exc
    try:
        raw_text = str(raw).strip()
        # Strip a SINGLE trailing unit; only an exact one is a Fly duration.
        if raw_text.endswith("s"):
            raw_text = raw_text[:-1]
        # Reject anything that is not a plain decimal. The old
        # ``float(str(raw).rstrip("s"))`` was far too lenient: ``rstrip`` strips
        # a CHARACTER SET, so "15ss" -> "15", and ``float`` also accepts
        # "1_000" and "1e3". Leading/trailing whitespace IS still trimmed
        # before this check (deliberate normalization of a TOML string); the
        # point of the regex is the numeric shape, not the padding.
        assert re.fullmatch(r"\d+(?:\.\d+)?", raw_text), (
            f"fly.toml http_check timeout {raw!r} is not a plain duration — the "
            "cross-endpoint ceiling is being silently disarmed"
        )
        value = float(raw_text)
    except AssertionError:
        raise
    except ValueError as exc:
        raise AssertionError(
            f"fly.toml http_check timeout {raw!r} is not a duration ({exc!r}) — "
            "the cross-endpoint ceiling is being silently disarmed"
        ) from exc
    # A non-finite budget is WORSE than a malformed one: ``ready_worst_case <
    # inf`` is trivially true, so the ceiling would pass while bounding nothing.
    # ``nan`` happens to fail loudly on the comparison, but reject both rather
    # than depend on which side of the operator it lands.
    assert math.isfinite(value), (
        f"fly.toml http_check timeout {raw!r} parses to a non-finite value "
        f"({value!r}) — the cross-endpoint ceiling would be silently disarmed"
    )
    return value


def test_every_plane_probe_is_hard_bounded_and_fail_closed():
    """The bound is what makes the endpoint ANSWER when a plane black-holes.

    The bound now lives on each ``HealthProbe`` rather than in a per-handler
    ``wait_for``, so pin BOTH the single-source-of-truth equality (the signature
    default IS the shared module constant) AND the ordering property (it clears
    the DB probes' loose outer-alignment bound — ``PROBE_DB_TOTAL_TIMEOUT`` is
    deliberately an OVER-ESTIMATE of the probes' real total, NOT that total —
    plus the nominal SDK-acquisition budget), plus each plane's fail-closed flag
    and the liveness refresher's alignment. ``_READY_PROBE_TIMEOUT_S`` is gone
    with the mechanism it bounded; a reintroduced per-handler literal would be
    an unreasoned second source of truth.
    """
    import inspect

    import tortoise.hosted_api as mod
    from tortoise.monitoring import (
        PROBE_DB_TOTAL_TIMEOUT,
        PROBE_HARD_TIMEOUT,
        PROBE_SDK_ACQUISITION_BUDGET,
    )

    default = inspect.signature(mod.HealthProbe.__init__).parameters["timeout"].default
    # (1) SINGLE-SOURCE-OF-TRUTH PIN. The signature default must BE the shared
    # module constant, not a hand-typed literal: a safe-LOOKING literal (a
    # reviewer proved ``6.0``) satisfies the ordering property below while
    # silently diverging from the constant the module derives and documents.
    assert default == PROBE_HARD_TIMEOUT, (
        "HealthProbe's default wall bound must be the shared module constant "
        f"PROBE_HARD_TIMEOUT ({PROBE_HARD_TIMEOUT}s), not a hand-typed literal "
        f"(got {default}s)"
    )
    # (2) ORDERING PROPERTY. The default must clear the DB probes' loose
    # outer-alignment bound (``PROBE_DB_TOTAL_TIMEOUT`` — deliberately an
    # OVER-ESTIMATE of probe_db's real total, NOT the exact inner total — plus
    # the nominal SDK-acquisition budget). Necessary but NOT sufficient: the
    # embedded acquisition prefix is unbounded, so this is a best-effort
    # alignment, not a proven invariant (see monitoring.PROBE_MAX_SUPERSEDES).
    inner_total = PROBE_DB_TOTAL_TIMEOUT + PROBE_SDK_ACQUISITION_BUDGET
    assert default > inner_total, (
        f"HealthProbe's default wall bound ({default}s) does not clear the DB "
        f"probes' loose outer-alignment bound ({inner_total}s — an OVER-ESTIMATE, "
        "not the exact total) — an omitted timeout "
        "strands a worker thread on every timeout (#2988)"
    )
    assert "_READY_PROBE_TIMEOUT_S" not in HOSTED_API.read_text(), (
        "the superseded per-handler readiness bound is back — the bound belongs "
        "to HealthProbe (one reasoned place)"
    )
    for name in ("_READY_PROBE", "_CONTROL_PLANE_PROBE"):
        probe = getattr(mod, name)
        assert probe._timeout > 0, f"{name} has no usable bound"
        assert probe._fresh_only is True, (
            f"{name} must be fresh_only=True — readiness is a FAIL-CLOSED gate and "
            "must never answer 200 from a verdict older than its read budget (#1384/#2850)"
        )
    # ``_HEALTH_PROBE`` is the NON-fresh refresher that feeds /health
    # (``fresh_only`` is False BY DESIGN), so it cannot join the loop above —
    # assert its ordering SEPARATELY, and pin the identity too. The ordering
    # check alone is satisfied by the class DEFAULT, so it cannot catch an
    # accidental drop of ``timeout=DB_PROBE_HARD_TIMEOUT`` from the
    # construction. Only _READY_PROBE used to be pinned at all.
    assert mod._HEALTH_PROBE._timeout == mod.DB_PROBE_HARD_TIMEOUT, (
        "the /health refresher must pass the explicit DB_PROBE_HARD_TIMEOUT — "
        "the ordering assertion below is satisfied by the class default too, "
        "so it cannot catch a dropped timeout= on its own"
    )
    assert mod._HEALTH_PROBE._timeout > inner_total, (
        f"/health's refresher bound ({mod._HEALTH_PROBE._timeout}s) must clear "
        f"_probe_db's loose outer-alignment bound ({inner_total}s = the "
        f"over-estimate PROBE_DB_TOTAL_TIMEOUT {PROBE_DB_TOTAL_TIMEOUT}s + the "
        f"{PROBE_SDK_ACQUISITION_BUDGET}s nominal SDK-acquisition budget), or it "
        "abandons a live worker on every timeout (#2988). This is alignment, "
        "not proof: the embedded acquisition prefix is unbounded."
    )


def test_each_plane_bound_sits_above_its_own_client_timeout(monkeypatch):
    """The #2988 layered-timeout ALIGNMENT, expressed PER PLANE.

    Abandoning a probe does not stop its thread — ``wait_for`` cancels the
    awaitable, not the worker (CPython #87185), so the worker stays parked in
    its socket read. The defence is ordering: keep the outer bound ABOVE the
    probe's loose inner figure (``PROBE_DB_TOTAL_TIMEOUT`` — deliberately an
    OVER-ESTIMATE, not the exact inner total), so the inner bound normally fires
    first and the thread returns by itself. This is a best-effort ALIGNMENT
    that reduces stranding, NOT a proven invariant — the inner worst case is
    unbounded (httpx's ``read`` is per-read; the embedded acquisition prefix
    runs real queries bounded by the redis read timeout).

    #2850 initially INVERTED this (2s outer vs a 5s inner on the control plane)
    and leaned on ``PROBE_MAX_SUPERSEDES`` instead. That rationale was
    overstated: the supersede counter RESETS on any live completion, so it caps
    a single wedge episode rather than the process lifetime. Both properties
    are now asserted at once — each bound is above its own loose inner figure
    (an OVER-ESTIMATE of the inner total, not the exact one) — AND the probe
    still runs on a dedicated coordinator rather than the shared default pool.

    This test is THE canonical per-plane tripwire (the structural test above
    deliberately does not duplicate these predicates): raise a PHASE in
    ``CONTROL_PLANE_PROBE_PHASES`` above its plane's bound and it fails. (It
    does NOT cover the client-level ``SupabaseControlPlane(timeout=...)``
    default — that is per-phase too, which is why the probe overrides it
    per-request rather than relying on it.)

    2026-09-13 (#3458): the cross-endpoint ceiling is asserted in TWO parts.
    (1) UNCONDITIONALLY, ``ready_worst_case < READY_WORST_CASE_BUDGET_S`` — a
    repo-owned POLICY constant (15.0s, the budget fly.toml's old routing check
    implied). This is the part that keeps the SUM bounded: the per-plane
    asserts above each compare a bound to its own inner total, so without it a
    change doubling ``CONTROL_PLANE_HARD_TIMEOUT`` would pass everything.
    (2) Additionally, ``ready_worst_case < fly.toml's http_check timeout`` WHEN
    fly.toml exposes one. #2850 removed ``[[services.http_checks]]`` (the
    /health HTTP check flapped and de-registered the sole machine), so today
    the only external bound is the TCP replacement — whose 5s timeout is
    documented as headroom, not a latency budget, and cannot serve as a
    ceiling. When no http_check budget exists the test asserts instead that the
    documented replacement (``[[services.tcp_checks]]``) IS present, that the
    top-level ``[checks.loop_liveness]`` entry this repo now ships IS present
    and carries its own documented bounds (and is NOT the readiness ceiling),
    and that no OTHER top-level ``[checks]`` entry has appeared — so removing,
    altering or shadowing the checks block reds here rather than silently
    passing.
    """
    import httpx

    import tortoise.hosted_api as mod
    import tortoise.supabase_control as sc
    from tortoise.monitoring import PROBE_DB_TOTAL_TIMEOUT, PROBE_TIMEOUT

    # Data plane: since #3143 probe_db's PLATFORM shape (no explicit
    # allowance) has ONE caller deadline of PROBE_TIMEOUT — the #1565 retry
    # rides the REMAINDER instead of taking a second bound — so its real total
    # is ~PROBE_TIMEOUT. PROBE_DB_TOTAL_TIMEOUT (2 x PROBE_TIMEOUT + the retry
    # delay) is now deliberately a LOOSE OVER-ESTIMATE kept as the
    # outer-alignment figure a coordinator is sized ABOVE, NOT the exact inner
    # total. The assertion still targets that loose figure on purpose: it is
    # the STRICTER check (the readiness bound must clear the over-estimate
    # too), so it cannot pass while the real ~1.5s total is unguarded. Bounding
    # against the bare per-attempt figure instead is what produced the
    # historical inversion (2.0 > 1.5 while the then-real total was ~3.1s,
    # before #3143 made the retry ride the remainder).
    assert mod._READY_PROBE._timeout > PROBE_DB_TOTAL_TIMEOUT, (
        f"the FalkorDB readiness bound ({mod._READY_PROBE._timeout}s) must exceed "
        f"probe_db's loose outer-alignment bound ({PROBE_DB_TOTAL_TIMEOUT}s = 2 x "
        f"{PROBE_TIMEOUT}s + the retry delay) or the outer bound wins the race and "
        "strands a worker thread per timeout (#2988)"
    )

    # Control plane: the probe request carries its OWN composed per-request
    # timeout whose phases SUM to CONTROL_PLANE_PROBE_TOTAL_S. Assert against
    # that composed TOTAL — NOT SupabaseControlPlane's 5.0s constructor default,
    # which httpx applies PER PHASE (connect/read/write/pool) and is therefore
    # not a deadline at all: a multi-phase stall could run ~15-20s and outrun
    # the outer bound.
    assert mod.CONTROL_PLANE_HARD_TIMEOUT > mod.CONTROL_PLANE_PROBE_TOTAL_S, (
        f"the control-plane bound ({mod.CONTROL_PLANE_HARD_TIMEOUT}s) must exceed the "
        f"probe request's composed total ({mod.CONTROL_PLANE_PROBE_TOTAL_S}s) or the "
        "outer bound wins the race and strands a worker thread (#2988)"
    )
    assert sum(mod.CONTROL_PLANE_PROBE_PHASES.values()) == mod.CONTROL_PLANE_PROBE_TOTAL_S, (
        "DRIFT GUARD (NOT a bound): CONTROL_PLANE_PROBE_TOTAL_S must remain the "
        "sum of the request's per-phase timeouts"
    )
    assert mod._CONTROL_PLANE_PROBE._timeout == mod.CONTROL_PLANE_HARD_TIMEOUT, (
        "the control-plane coordinator must use the derived bound"
    )

    # Behavioural half: the composed timeout must actually reach the request.
    # A constant nothing passes is dead code, and the client would silently keep
    # its per-phase default.
    seen: dict = {}

    class _CapturingControlPlane:
        def query(self, table, *, select=None, filters=None, method="GET",
                  json_body=None, order=None, limit=None, timeout=None):
            seen["timeout"] = timeout
            seen["table"] = table
            return []

    monkeypatch.setattr(sc, "get_control_plane", lambda: _CapturingControlPlane())
    assert mod._probe_control_plane()["ok"] is True
    passed = seen["timeout"]
    assert isinstance(passed, httpx.Timeout), (
        f"the control-plane probe passed {passed!r} — it must pass a composed "
        "httpx.Timeout, or the per-phase default stays in force"
    )
    assert (passed.connect + passed.read + passed.write + passed.pool) \
        == mod.CONTROL_PLANE_PROBE_TOTAL_S, (
        "the probe request's phases must sum to CONTROL_PLANE_PROBE_TOTAL_S"
    )

    # /health/ready runs the two planes SEQUENTIALLY, so its worst case is the
    # sum of the two bounds. That sum is bounded UNCONDITIONALLY against the
    # repo-owned policy ceiling below — the per-plane asserts above bound each
    # phase against its own inner total, which does NOT bound the sum (doubling
    # CONTROL_PLANE_HARD_TIMEOUT would pass all of them).
    #
    # Historically the sum was compared to fly.toml's 15s http_check timeout.
    # #2850 (2026-09-10) removed that check, so the external proxy is gone and
    # READY_WORST_CASE_BUDGET_S re-homes the same bound as an explicit policy
    # constant rather than deleting the assertion. When fly.toml DOES expose an
    # http_check budget it is checked too, as a stricter additional bound.
    ready_worst_case = mod.DB_PROBE_HARD_TIMEOUT + mod.CONTROL_PLANE_HARD_TIMEOUT
    assert ready_worst_case < READY_WORST_CASE_BUDGET_S, (
        f"/health/ready's sequential worst case ({ready_worst_case}s) must stay "
        f"under the repo-owned policy ceiling ({READY_WORST_CASE_BUDGET_S}s). "
        "This is a POLICY constant, not deploy-derived: /health/ready is curled "
        "by deploy-hosted.yml with no --max-time, and fly.toml's 15s http_check "
        "was removed by #2850. Raise the constant deliberately if the bound "
        "genuinely needs to grow; do not delete the assertion."
    )
    ceiling = _fly_check_budget_proxy_s()
    if ceiling is not None:
        # Stricter, when fly.toml happens to configure an HTTP check budget.
        assert ready_worst_case < ceiling, (
            f"/health/ready's sequential worst case ({ready_worst_case}s) must stay "
            f"under fly.toml's http_check timeout ({ceiling}s), borrowed ONLY as a "
            "coarse budget proxy for /health/ready (that check actually targets "
            "/health, and deploy-hosted.yml curls /health/ready with no --max-time)"
        )
    else:
        # No HTTP-check budget in fly.toml (the #2850 state). Make that ABSENCE a
        # positive assertion rather than a silent skip: it may only mean the
        # documented HTTP->TCP migration, so the SERVICES replacement
        # ([[services.tcp_checks]]) must be present — deleting THAT block reds
        # immediately below. (The separate top-level [checks] block has its own
        # fail-closed absence assertion at the end of this branch; this paragraph
        # covers the services check only, not `[checks]`.)
        #
        # 2026-09-17 (#3447): the top-level `[checks.loop_liveness]` entry the
        # old guard failed closed on has LANDED. A flat `"checks" not in _cfg`
        # would now forbid the shipped config outright; widening it to "any
        # top-level table is fine" would delete the guard. Instead MODEL the
        # known entry explicitly: assert its identity and its bounds, and assert
        # it cannot feed the readiness ceiling — any OTHER top-level check still
        # reds, so a future unmodelled `[checks]` entry cannot escape this
        # analysis.
        #
        # It is NOT wired into READY_WORST_CASE_BUDGET_S (the message's other
        # offered resolution) because it is a DIFFERENT quantity: 9090/healthz
        # is a dedicated listener off the client's request path, and the check's
        # 5s timeout times loop liveness, not a request — it would TIGHTEN the
        # 11.6s readiness worst case to a value about a different surface, i.e.
        # silently disarm the ceiling it is supposed to guard.
        _cfg = tomllib.loads((REPO / "fly.toml").read_text())
        assert _cfg["services"][0].get("tcp_checks"), (
            "fly.toml exposes NEITHER an http_check budget proxy NOR the "
            "tcp_checks that replaced it (#2850) — the services checks block "
            "was removed or altered without the documented migration"
        )
        checks = _cfg.get("checks")
        if checks is not None:
            assert isinstance(checks, dict) and set(checks) == {"loop_liveness"}, (
                "fly.toml carries an unmodelled top-level [checks] entry: "
                f"{sorted(checks) if isinstance(checks, dict) else checks!r}. Only "
                "the loop-liveness check (#3447) is modelled here. A new top-level "
                "check also gates flyctl's deploy wait and may bound the readiness "
                "surface — add it to this model (identity + bounds + whether it "
                "feeds READY_WORST_CASE_BUDGET_S) rather than letting it escape "
                "the cross-endpoint analysis."
            )
            ll = checks["loop_liveness"]
            assert isinstance(ll, dict), (
                f"[checks.loop_liveness] is not a table ({ll!r}) — its bounds "
                "cannot be evaluated"
            )
            assert ll.get("type") == "http" and ll.get("path") == "/healthz", (
                "[checks.loop_liveness] must probe the app's dedicated HTTP "
                f"/healthz listener; got type={ll.get('type')!r} "
                f"path={ll.get('path')!r}"
            )
            assert ll.get("port") == 9090, (
                "[checks.loop_liveness] must target the dedicated 9090 listener, "
                "NOT the client-facing 8000 plane — its whole point is to be off "
                f"the request path; got port={ll.get('port')!r}"
            )
            assert ll.get("method", "get") == "get", (
                "[checks.loop_liveness] must be a GET — the handler 405s anything "
                "else (monitoring.py _method_not_allowed), and flyctl's deploy "
                "wait requires every reported check to pass, so a non-GET fails "
                f"every deploy; got {ll.get('method')!r}"
            )
            # Its OWN documented bounds (fly.toml §6.4): pin them so a silent
            # edit to the interval/timeout/grace cannot pass through the model.
            interval, timeout, grace = "15s", "5s", "180s"
            assert (ll.get("interval"), ll.get("timeout"), ll.get("grace_period")) \
                == (interval, timeout, grace), (
                    "[checks.loop_liveness] bounds drifted from the documented "
                    f"{interval}/{timeout}/{grace}: got interval={ll.get('interval')!r} "
                    f"timeout={ll.get('timeout')!r} grace_period={ll.get('grace_period')!r}"
                )
            # NOT a readiness budget. Its 5s timeout sits BELOW /health/ready's
            # sequential worst case, so borrowing it as a ceiling would be a
            # silent DISARM; and _fly_check_budget_proxy_s — the only reader —
            # consumes services[0].http_checks, never a top-level check (which is
            # exactly why it returned None and put this branch in force).
            loop_timeout_s = float(timeout[:-1])
            assert loop_timeout_s < ready_worst_case, (
                "[checks.loop_liveness] timeout must stay BELOW /health/ready's "
                f"sequential worst case ({ready_worst_case}s) — it bounds loop "
                "liveness, not a readiness request, so it cannot serve as the "
                "cross-endpoint ceiling"
            )
            assert loop_timeout_s != READY_WORST_CASE_BUDGET_S, (
                "[checks.loop_liveness] timeout must not BE the readiness policy "
                "ceiling (READY_WORST_CASE_BUDGET_S) — the policy constant is "
                "independent of this check, not borrowed from it"
            )
        else:
            # Failure #3447 exists to catch, stated as the branch's own contract:
            # an ABSENT top-level [checks] block is not the unmodelled case above
            # and must not skip the model. In the #2850 state this block is the
            # only application-level probe in the file — every remaining check is
            # kernel-served (services.tcp_checks) and therefore blind to a
            # stalled-but-running event loop. Deleting it is exactly the mistake
            # that needs a mandatory post-merge `flyctl checks list`, so the
            # tripwire has to red here rather than delegate that to the operator.
            raise AssertionError(
                "fly.toml has NO top-level [checks] block: got "
                f"{checks!r}. Absence is NOT modelled — in the #2850 state "
                "[checks.loop_liveness] is the only check that can see a STALLED "
                "event loop (services.tcp_checks is kernel-served and cannot). "
                "Restore [checks.loop_liveness] or record its removal AND its "
                "replacement here deliberately."
            )


def test_db_probe_bound_covers_the_sdk_acquisition_prefix():
    """The DB probes' bound is aligned above the whole worker as far as the
    acquisition cost can be computed, not just ``probe_db``.

    ``_probe_db`` is ``_probe_sdk()`` THEN ``probe_db(sdk)``. In URI mode
    (the hosted steady state) the acquisition prefix is ~free — the SDK's
    projection is LAZY and the connect happens inside ``probe_db``'s own
    per-attempt bound. On the EMBEDDED path (cold ``_probe_sdk`` cache /
    ``_probe_sdk_reset()``) the anchor path connects EAGERLY and runs real
    queries bounded by the redis READ timeout, which the budget does NOT
    cover. The bound is DERIVED as ``PROBE_DB_TOTAL_TIMEOUT`` +
    ``PROBE_SDK_ACQUISITION_BUDGET`` (best-effort) — pin both the budget's
    source (so it cannot drift from projection) and the derivation (so a
    ``PROBE_TIMEOUT`` change propagates instead of leaving a stale hand-typed
    literal).
    """
    import tortoise.hosted_api as mod
    from tortoise.monitoring import (
        PROBE_DB_TOTAL_TIMEOUT,
        PROBE_HARD_TIMEOUT,
        PROBE_SDK_ACQUISITION_BUDGET,
    )
    from tortoise.projection import _DB_CONNECT_TIMEOUT_DEFAULT

    assert PROBE_SDK_ACQUISITION_BUDGET == _DB_CONNECT_TIMEOUT_DEFAULT, (
        "the SDK-acquisition budget must track the redis client's default "
        "socket_connect_timeout (projection._DB_CONNECT_TIMEOUT_DEFAULT) — a "
        "drift makes the DB probe's bound dishonest"
    )
    # The bound is the SINGLE shared derived constant (hosted_api aliases it),
    # so a PROBE_TIMEOUT change propagates instead of leaving a stale literal.
    assert mod.DB_PROBE_HARD_TIMEOUT == PROBE_HARD_TIMEOUT, (
        "DB_PROBE_HARD_TIMEOUT must be the shared derived bound, not a second "
        "hand-typed literal"
    )
    # STRICTLY above the LOOSE outer-alignment figure (``PROBE_DB_TOTAL_TIMEOUT``
    # — deliberately an OVER-ESTIMATE of probe_db's real total, NOT the exact
    # inner total — PLUS the nominal acquisition budget) — equality would still
    # be a race. This does NOT cover the embedded acquisition prefix.
    assert mod.DB_PROBE_HARD_TIMEOUT > \
        PROBE_DB_TOTAL_TIMEOUT + PROBE_SDK_ACQUISITION_BUDGET, (
        "DB_PROBE_HARD_TIMEOUT must sit strictly above PROBE_DB_TOTAL_TIMEOUT + "
        "the SDK-acquisition budget, or the outer bound can win the race"
    )


# ── the selfhost twin of the same defect ───────────────────────────────────


def test_selfhost_ready_does_not_probe_on_the_loop():
    """``tortoise/selfhost.py::health_ready`` had the identical bug: it built the
    SDK and touched the DB inline. ``publish-selfhost.yml`` curls this endpoint
    on every publish, so it is the same outage vector in the other image."""
    node = _handler("health_ready", SELFHOST)
    offenders = [
        n.lineno
        for n in _walk_own_body(node)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"_get_proj", "query"}
    ]
    assert not offenders, (
        f"selfhost health_ready calls DB-touching code directly at line(s) {offenders} — "
        "synchronous DB work on the event loop (#2988)"
    )
    off_loop = {
        arg.id
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "to_thread"
        for arg in call.args
        if isinstance(arg, ast.Name)
    }
    assert off_loop, "selfhost health_ready dispatches nothing with asyncio.to_thread"


def test_selfhost_probe_is_bounded():
    node = _handler("health_ready", SELFHOST)
    assert any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "wait_for"
        for n in ast.walk(node)
    ), "selfhost health_ready's probe is unbounded — a black-holed DB would hang it"
    assert re.search(r"_READY_PROBE_TIMEOUT_S = ([\d.]+)", SELFHOST.read_text()), (
        "selfhost.py must own its bound as a module constant"
    )


# ── behavioural proof ──────────────────────────────────────────────────────


def _run(coro):
    return asyncio.run(coro)


def test_loop_stays_responsive_while_probes_are_slow(monkeypatch):
    """The decisive test: with a probe that BUSY-WAITS, a blocking
    implementation starves the ticker. Deterministic by construction — the
    probe signals when it is actually running and the assertion is about ticks
    that happened WHILE it was pending, so scheduler jitter cannot flip it.

    (The first version of this test asserted a fixed tick count against a
    sleeping probe and false-failed ~1 run in 5 under the docker lane: the loop
    was merely descheduled, not blocked. A flaky guard in a registered CI
    surface is worse than no guard.)
    """
    import threading

    import tortoise.hosted_api as mod
    import tortoise.supabase_control as sc

    entered = threading.Event()
    release = threading.Event()

    def slow_probe():
        entered.set()
        release.wait(30)
        return {"ok": True, "latency_ms": 1.0, "error": None}

    monkeypatch.setattr(mod, "_probe_db", slow_probe)
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: False)

    async def scenario():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        try:
            ready_task = asyncio.create_task(mod.health_ready())
            # The probe is dispatched before the first await completes; wait for
            # it to be inside its thread, then count ticks while it is pending.
            while not entered.is_set():
                await asyncio.sleep(0.01)
            before = ticks
            deadline = time.time() + 5.0
            while ticks - before < 3 and time.time() < deadline:
                await asyncio.sleep(0.01)
            pending_ticks = ticks - before
            release.set()
            result = await ready_task
        finally:
            release.set()
            ticker_task.cancel()
        return result, pending_ticks

    result, pending_ticks = _run(scenario())
    assert result["status"] == "ok"
    assert pending_ticks >= 3, (
        f"the event loop ticked {pending_ticks} times while the probe was pending — "
        "a blocking implementation freezes it at 0 (#2988)"
    )


def test_hung_data_plane_returns_503_within_the_bound(monkeypatch):
    """A black-holed probe must be REPORTED, not waited out.

    The latency is measured INSIDE the coroutine: ``asyncio.run`` joins the
    default executor at shutdown (``loop.shutdown_default_executor``), so wall
    time around it includes the still-running probe thread — in production the
    loop is long-lived and there is no such join.
    """
    import threading

    import tortoise.hosted_api as mod

    release = threading.Event()
    monkeypatch.setattr(mod._READY_PROBE, "_timeout", 0.2)
    mod._READY_PROBE.reset()
    monkeypatch.setattr(mod, "_probe_db", lambda: release.wait(30))

    async def scenario():
        started = time.time()
        try:
            with pytest.raises(HTTPException) as excinfo:
                await mod.health_ready()
        finally:
            release.set()
        return time.time() - started, excinfo.value

    elapsed, exc = _run(scenario())
    assert exc.status_code == 503
    assert elapsed < 1.2, (
        f"the endpoint waited {elapsed:.2f}s for a hung probe — the bound is not applied"
    )


def test_hung_control_plane_returns_503_within_the_bound(monkeypatch):
    import threading

    import tortoise.hosted_api as mod
    import tortoise.supabase_control as sc

    release = threading.Event()
    monkeypatch.setattr(mod._CONTROL_PLANE_PROBE, "_timeout", 0.2)
    mod._CONTROL_PLANE_PROBE.reset()
    monkeypatch.setattr(mod, "_probe_db", lambda: {"ok": True, "latency_ms": 1.0, "error": None})
    monkeypatch.setattr(mod, "_probe_control_plane", lambda: release.wait(30))
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)

    async def scenario():
        started = time.time()
        try:
            with pytest.raises(HTTPException) as excinfo:
                await mod.health_ready()
        finally:
            release.set()
        return time.time() - started, excinfo.value

    elapsed, exc = _run(scenario())
    assert exc.status_code == 503
    assert "Control plane" in str(exc.detail)
    assert elapsed < 1.2, f"waited {elapsed:.2f}s for a hung control plane"


def test_ready_when_both_planes_answer(monkeypatch):
    """The happy path keeps the exact response shape the deploy gates consume."""
    import tortoise.hosted_api as mod
    import tortoise.supabase_control as sc

    monkeypatch.setattr(mod, "_probe_db", lambda: {"ok": True, "latency_ms": 2.0, "error": None})
    called = []

    def _fake_control():
        # #2850: the control-plane probe now RETURNS its verdict (the
        # coordinator reads `{"ok": ...}`); under #2988 success was implied by
        # not raising, so this stub used to return None.
        called.append(1)
        return {"ok": True, "latency_ms": 1.0, "error": None}

    mod._CONTROL_PLANE_PROBE.reset()
    mod._READY_PROBE.reset()
    monkeypatch.setattr(mod, "_probe_control_plane", _fake_control)
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)

    assert _run(mod.health_ready()) == {
        "status": "ok",
        "db": "connected",
        "control_plane": "connected",
    }
    assert called == [1], "the control-plane probe did not run"


def test_dead_db_is_503_and_never_probes_the_control_plane(monkeypatch):
    import tortoise.hosted_api as mod
    import tortoise.supabase_control as sc

    monkeypatch.setattr(mod, "_probe_db", lambda: {"ok": False, "latency_ms": 0.0, "error": "down"})
    probed = []
    monkeypatch.setattr(mod, "_probe_control_plane", lambda: probed.append(1))
    monkeypatch.setattr(sc, "is_supabase_enabled", lambda: True)

    with pytest.raises(HTTPException) as excinfo:
        _run(mod.health_ready())
    assert excinfo.value.status_code == 503
    assert probed == []
