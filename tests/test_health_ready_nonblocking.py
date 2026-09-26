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

# Shared with the #3718 guard (#4625 review F1): one home for the "is this
# nested def invoked on the loop?" decision, so Guard A cannot stay blind to
# a locally defined closure the route calls while Guard B sees it.
from tests._loop_ast import nested_defs_invoked_on_loop as _nested_defs_invoked_on_loop

REPO = Path(__file__).resolve().parent.parent
HOSTED_API = REPO / "tortoise" / "hosted_api.py"
SELFHOST = REPO / "tortoise" / "selfhost.py"
SUPABASE_CONTROL = REPO / "tortoise" / "supabase_control.py"


def _ancestors(node: ast.AST, parents: dict[int, ast.AST]):
    """Walk from ``node`` up to the root, yielding each ancestor."""
    current = parents.get(id(node))
    while current is not None:
        yield current
        current = parents.get(id(current))


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
#
# LIMIT, stated: the scan walks ASYNC bodies, so a blocking call inside a SYNC
# helper reached from an async body is not visible here (that was the removed
# ``_session_pinned_org`` lazy-read shape; it is pinned directly by
# ``test_session_pinned_org_is_a_pure_predicate``). The dynamic
# ``test_no_new_on_loop_control_plane_helper_calls`` names the residual
# explicitly so a new on-loop helper call still fails.
CONTROL_PLANE_OFFLOAD_INVENTORY = frozenset({
    "resolve_api_key",          # key-auth: 2-3 dependent PostgREST round-trips
    "update_last_used",         # key-auth: the last_used_at PATCH (best-effort)
    "user_memberships",         # session lane: membership rows
    "membership_for_user_org",  # session/DI + login/claim lanes
    "_orgs_row_fail_soft",      # session/DI lane: the orgs additive ladder
    "org_by_id",                # session/DI + invite/onboarding lanes
    "invitation_info_by_token",  # invite-info (hosted): the token lookup
    "_org_node_sync_limits",    # session/DI lane: org limit props (org_by_id)
    "api_key_by_id",            # key-write lanes: the key lookup
    "set_dashboard_key_login",  # dashboard-login + provisioning flag write
    "set_api_key_enabled",       # key-write lane: the enabled PATCH
    "set_api_key_name",          # key-write lane: the label PATCH
    "set_api_key_scopes",        # key-write lane: the scopes PATCH
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
#: key-write/login/claim/invite-info lanes). If a name leaves the inventory its
#: routed sites lose their regression guard, so the pin asserts they stay.
_ROUTED_SESSION_SEAMS = frozenset({
    "membership_for_user_org", "org_by_id", "api_key_by_id",
    "set_dashboard_key_login", "set_api_key_enabled", "set_api_key_name",
    "set_api_key_scopes", "_org_node_sync_limits", "_resolve_signup_token",
    "invitation_info_by_token",
})

#: Blocking ``supabase_control`` helpers that are STILL called directly
#: (un-offloaded) from an async body. This is the DECLARED residual of #4350
#: plus the in-lock mint calls in ``_session_key_supabase`` (which cannot await
#: under the synchronous ``_org_mint_lock``). It is deliberately explicit and
#: reviewed: a NEW on-loop call to a helper outside this set fails
#: ``test_no_new_on_loop_control_plane_helper_calls`` — which is the design's
#: "fail on the next call site" guard, with the residual named rather than
#: implied. Burn it down in #4350.
_KNOWN_ON_LOOP_RESIDUAL = frozenset({
    "active_api_keys", "claim_membership", "consume_link_intent",
    "consume_unlink_permit", "count_active_free_memberships",
    "count_graph_keys", "decline_invitation_by_email",
    "expired_bootstrap_keys", "graph_key_ids", "insert_api_key",
    "invitation_accept", "invitation_accept_by_id", "invitation_expire",
    "invitation_mint", "invitation_rescind",
    "invitation_resend", "invitation_row_by_token", "is_anon_org",
    "membership_by_identity", "membership_count_since", "membership_role",
    "mint_target_user_for_key", "org_api_keys", "org_by_email",
    "org_by_name", "org_members", "org_tier", "owned_free_org_ids",
    "pending_invitations", "pending_invitations_for_email",
    "provision_org", "provision_org_with_token", "recover_org_key",
    "reserve_unlink", "revoke_api_key", "set_graph_name",
    "set_graph_recording", "set_membership", "set_org_onboarding_email_sent",
    "signup_token_row", "soft_delete_graph", "store_github_credentials",
    "store_link_intent", "user_identity_inventory", "webhook_event_marker",
})

#: Callees that OFFLOAD their argument — a call nested inside one of these is
#: not on the loop, so the walk does not descend into it. ``_oauth_offload``
#: (#3669) is the OAuth-lane wrapper over the same seam (it calls ``_cp_offload``
#: on the dedicated ``oauth`` pool), so it is a boundary for the same reason;
#: ``_graph_offload`` (#3773) is the DATA-PLANE wrapper over the same seam (the
#: dedicated ``graph`` pool).
OFFLOAD_BOUNDARY_CALLEES = frozenset({
    "_cp_offload", "_oauth_offload", "_graph_offload",
    "run_control_plane_call", "run_on_daemon_worker", "to_thread",
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
                       module_aliases: dict[str, str] | None = None,
                       boundaries: frozenset[str] | None = None,
                       descend_nested: frozenset[str] | set[str] = frozenset(),
                       ) -> list[tuple[str, ast.Call]]:
    """Every ``ast.Call`` in ``node``'s OWN body that is not itself an offload
    boundary and is not nested inside one, as ``(resolved_callee, call)``.

    The callee name is RESOLVED through ``from ... import X as Y`` aliases
    (module-level and local to the body), so ``_sb_memberships(...)`` (an alias
    of ``user_memberships``) is seen for what it is — an alias is exactly how
    the session lane smuggles one of these calls past a naive name match.

    Nested ``def``/``async def``/``class`` bodies are skipped: a blocking call
    in a nested function belongs to that function's own inventory entry (and
    the pre-existing #2988 pin covers the probe case deliberately). EXCEPT a
    LOCAL function has no inventory entry, so ``descend_nested`` names the
    nested defs that ARE invoked on the loop (the #4455 / #4625 rule, shared
    with Guard B) — their bodies are scanned as part of THIS body, which is
    the entry a residual declares (#4625 review F1).
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
    boundary_names = boundaries or OFFLOAD_BOUNDARY_CALLEES

    def visit(current: ast.AST) -> None:
        if isinstance(current, ast.Call):
            if _callee_name(current.func) in boundary_names:
                # The CALLABLE argument runs on the worker — skip it. Every
                # OTHER argument is evaluated EAGERLY on the loop, so it must
                # still be scanned (#3498 review): a
                # ``_cp_offload(user_memberships(cp, uid))`` call (a Call, not
                # a Lambda/Name) would otherwise hide an on-loop call.
                for idx, arg in enumerate(current.args):
                    if idx == 0 and isinstance(arg, (ast.Lambda, ast.Name)):
                        continue
                    visit(arg)
                for kw in current.keywords:
                    # The callable may be passed by KEYWORD (``fn=``/``func=``)
                    # — it still runs on the worker, so skip it the same way.
                    if kw.arg in ("fn", "func") and isinstance(
                            kw.value, (ast.Lambda, ast.Name)):
                        continue
                    visit(kw.value)
                return
            name = resolved(current.func)
            if name is not None:
                found.append((name, current))
        for child in ast.iter_child_nodes(current):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if getattr(child, "name", None) in descend_nested:
                    for stmt in child.body:
                        visit(stmt)
                continue
            visit(child)

    for stmt in getattr(node, "body", []):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if getattr(stmt, "name", None) in descend_nested:
                for inner in stmt.body:
                    visit(inner)
            continue
        visit(stmt)
    return found


def _on_loop_nested_names(node: ast.AST) -> set[str]:
    """Nested defs of ``node`` that are INVOKED on the loop (not merely
    reference-passed to an offload boundary) — Guard B's rule, shared.

    The boundaries are ``_ONBOARDING_OFFLOAD_BOUNDARIES`` (the superset this
    scan honours): a nested def handed to ``_run_off_loop`` / ``_graph_offload``
    etc. runs in the worker and must stay out of the scan.
    """
    nested_names = {
        sub.name
        for sub in ast.walk(node)
        if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef))
        and sub is not node
    }
    return _nested_defs_invoked_on_loop(
        node, nested_names, _ONBOARDING_OFFLOAD_BOUNDARIES)


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
    """A DIRECT ``httpx.Client(...)`` construction inside a coroutine.

    This catches only that shape — a client built in a sync helper (as
    ``_track_analytics_event`` and ``_github_repos_count`` do) is covered by
    the name inventory above, not here."""
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


def _supabase_control_blocking_names() -> set[str]:
    """Module-level ``supabase_control`` functions that transitively reach the
    synchronous HTTP client (``cp.query`` / ``cp.rpc`` / ``cp.rpc_value``).

    This is the DYNAMIC half of the pin: a new helper enters this set
    automatically, so a new on-loop call site cannot hide behind a name that
    was simply never added to a hand-written list.
    """
    tree = ast.parse(SUPABASE_CONTROL.read_text())
    fns = {n.name: n for n in tree.body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    blocking: set[str] = set()
    changed = True
    while changed:
        changed = False
        for name, fn in fns.items():
            if name in blocking:
                continue
            calls: set[str | None] = set()
            for stmt in fn.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                calls |= {_callee_name(c.func) for c in ast.walk(stmt)
                          if isinstance(c, ast.Call)}
            if (calls & blocking) or (calls & {"query", "rpc", "rpc_value"}):
                blocking.add(name)
                changed = True
    return blocking


def test_no_new_on_loop_control_plane_helper_calls():
    """The design's "fail on the NEXT call site" guard, with an explicit
    residual rather than an implied one.

    An un-offloaded direct call from an async body to ANY blocking
    ``supabase_control`` helper must be either (a) covered by the inventory
    (which fails the test above) or (b) in the reviewed ``_KNOWN_ON_LOOP_RESIDUAL``
    set (#4350). A NEW call to a helper outside that set fails here.
    """
    blocking = _supabase_control_blocking_names()
    assert len(blocking) > 50, (
        f"the blocking-helper derivation saw only {len(blocking)} functions — "
        "it is not seeing supabase_control's HTTP surface"
    )
    tree = ast.parse(HOSTED_API.read_text())
    aliases = _module_aliases(tree)
    offenders = [
        (node.name, call.lineno, name)
        for node in _async_bodies(tree)
        for name, call in _unoffloaded_calls(node, aliases)
        if name in blocking
        and name not in CONTROL_PLANE_OFFLOAD_INVENTORY
        and name not in _KNOWN_ON_LOOP_RESIDUAL
    ]
    assert not offenders, (
        "NEW on-loop control-plane helper call(s) from an async body "
        f"(function, line, callee): {offenders} — route them through "
        "_cp_offload, or add the site to #4350's residual inventory "
        "(_KNOWN_ON_LOOP_RESIDUAL) with a reason"
    )
    # A helper cannot be both routed and allowlisted: if it were, the routed
    # sites would silently lose the guard above.
    assert not (CONTROL_PLANE_OFFLOAD_INVENTORY & _KNOWN_ON_LOOP_RESIDUAL), (
        "the offload inventory and the declared residual overlap: "
        f"{sorted(CONTROL_PLANE_OFFLOAD_INVENTORY & _KNOWN_ON_LOOP_RESIDUAL)}"
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


def test_detector_flags_an_eagerly_evaluated_boundary_argument():
    """A boundary call whose callable argument is NOT a lambda/name reference
    evaluates that argument on the loop — the detector must still see it."""
    src = (
        "async def handler():\n"
        "    rows = await _cp_offload(user_memberships(cp, uid), op='x')\n"
    )
    node = ast.parse(src).body[0]
    hits = [
        (call.lineno, name)
        for name, call in _unoffloaded_calls(node)
        if name in CONTROL_PLANE_OFFLOAD_INVENTORY
    ]
    assert hits == [(2, "user_memberships")]


def test_detector_ignores_a_keyword_callable_argument():
    """``fn=lambda: user_memberships(...)`` runs on the worker — the callable
    slot is skipped whether positional or keyword."""
    src = (
        "async def handler():\n"
        "    await _cp_offload(fn=lambda: user_memberships(cp, uid), op='x')\n"
    )
    node = ast.parse(src).body[0]
    hits = [
        name
        for name, _call in _unoffloaded_calls(node)
        if name in CONTROL_PLANE_OFFLOAD_INVENTORY
    ]
    assert hits == []


def test_session_pinned_org_is_a_pure_predicate():
    """The #3498 regression shape was a SYNC helper that read the control
    plane lazily (``_session_pinned_org``'s old ``user_memberships`` call).
    The name-based scan walks async bodies, so pin that removed shape
    directly: the helper must not import or call a control-plane seam."""
    tree = ast.parse(HOSTED_API.read_text())
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_session_pinned_org"
    )
    imported = {
        alias.name
        for imp in ast.walk(node)
        if isinstance(imp, ast.ImportFrom)
        for alias in imp.names
    }
    called = {
        _callee_name(c.func)
        for c in ast.walk(node)
        if isinstance(c, ast.Call)
    }
    offenders = (imported | called) & CONTROL_PLANE_OFFLOAD_INVENTORY
    assert not offenders, (
        f"_session_pinned_org references control-plane helper(s) {sorted(offenders)} "
        "— it must stay a pure in-memory predicate (the #3498 lazy-read regression)"
    )


# ── #4625: hosted-helper indirection + the mcp_server.py blind spot ─────────
#
# Two blind spots let #4625's read legs regrow while every existing guard stayed
# green:
#
#  1. ``CONTROL_PLANE_OFFLOAD_INVENTORY`` above is a hand-curated list of
#     ``supabase_control`` seams, and the dynamic half
#     (``_supabase_control_blocking_names``) is derived from
#     ``supabase_control.py`` ALONE. A blocking helper DEFINED IN
#     ``hosted_api.py`` (``_get_onboarding_state`` -> ``org_onboarding_state``,
#     ``_get_onboarding_projection``, ``_update_onboarding_state``,
#     ``_session_recording_allowed``, ...) can never enter either set — the scan
#     SEES the call and classifies it as non-blocking.
#
#  2. Neither this file nor ``test_read_routes_loop_responsiveness.py`` reads
#     ``tortoise/mcp_server.py`` at all, so the MCP onboarding surface was
#     unguarded.
#
# The companion below closes both, and it is per-CALLEE (not per-body): the
# bodies it flags (``_capture_session_impl``, ``patch_onboarding_state``, ...)
# are already declared residuals of the sibling graph-seam pin, so a body-level
# assertion could not see a FIXED leg regress inside one of them.
#
# SYNC MCP TOOLS ARE DELIBERATELY OUT OF THIS SCAN — and that is a verified
# fact, not an omission (the #4625 work order §4(b) claimed the opposite).
# ``mcp_server`` registers every tool through ``FunctionTool.from_function``
# with NO ``run_in_thread`` argument; the installed fastmcp 3.4.6 defaults it to
# **True** and ``FunctionTool._execute`` dispatches a sync tool body via
# ``anyio.to_thread.run_sync`` (``fastmcp/tools/function_tool.py`` — the work
# order instead read the *bundled* ``mcp/server/fastmcp`` copy, which is not the
# package this repo imports). So the sync onboarding tools already run OFF the
# loop by dispatch; only ASYNC ``mcp_server`` bodies are scanned here, and
# ``test_sync_mcp_tools_stay_off_loop`` pins the dispatch assumption so an
# upstream default change fails loudly instead of silently re-opening the hole.

MCP_SERVER = REPO / "tortoise" / "mcp_server.py"

#: ``hosted_api``-defined onboarding/capture helpers that reach blocking
#: control-plane (PostgREST via ``SupabaseControlPlane``) or FalkorDB I/O. Hand
#: curated, like the #3498 inventory — the graph/PostgREST leaves are
#: ``org_onboarding_state`` / ``_registry_existing_graphs`` / ``_get_proj``.
#: ``test_onboarding_helper_names_still_exist`` asserts every name resolves, so
#: a rename must update this set rather than silently vacate the pin.
_ONBOARDING_BLOCKING_HELPERS = frozenset({
    "_get_onboarding_state",       # teams.onboarding_state (PostgREST) / Team node
    "_get_onboarding_projection",  # the merged jsonb + graph projection
    "_update_onboarding_state",    # READ side (the WRITE path is the residual below)
    "_org_email",                  # teams.email (PostgREST)
    "_session_recording_allowed",  # #4625 leg 12 — now off-loop
    "_graph_recording_override",   # per-graph recording override (graph read)
    "_org_proj",                   # opens the org graph
    "_open_org_graph_sdk",         # opens the org graph
    "_graph_available",            # graph-exists probe
    "_write_org_email",            # the org-email PATCH
})

#: The off-loop wrappers that ARE the seam for those helpers — a call through
#: one of these is the fix, and the helper call itself lives inside the
#: wrapper's ``_graph_offload`` boundary.
_ONBOARDING_OFFLOAD_WRAPPERS = frozenset({
    "_get_onboarding_projection_off_loop",
    "_session_recording_allowed_off_loop",
    "_data_sdk_offloaded",
})

#: Offload boundaries this scan must honour — a superset of
#: ``OFFLOAD_BOUNDARY_CALLEES`` adding the data-plane / pool wrappers (#3773)
#: that the #3498 scan did not need.
_ONBOARDING_OFFLOAD_BOUNDARIES = OFFLOAD_BOUNDARY_CALLEES | frozenset({
    "_run_off_loop", "_submit_off_loop", "_run_with_close", "_run_dream_on_pool",
})

#: Functions reachable ON the loop that STILL call an
#: ``_ONBOARDING_BLOCKING_HELPERS`` member inline. This is the DECLARED,
#: reviewed residual of #4625 — every entry is either the WRITE path (which
#: needs a bounded-mutation design: a bounded ``wait_for`` abandons a worker
#: mid-write, CPython #87185 / #2863) or a read site not in this unit's scope.
#: The two FIXED read legs are asserted per-CALLEE (leg 12 in
#: ``test_capture_session_recording_gate_is_offloaded`` here, leg 62 in
#: ``test_read_routes_loop_responsiveness.py``), so this body-level set can
#: never hide their regression. Burn down under #4625.
_KNOWN_ONBOARDING_INLINE_RESIDUAL = frozenset({
    # capture path — remaining reads + the WRITE path (the receipt/last-error
    # writers and the onboarding jsonb read-modify-write); #4625 writes only.
    "_capture_session_impl",
    "_record_capture_last_error",   # the last-error WRITE chain
    "_reconcile_capture_receipts",  # reached inline from delete_session
    "_get_onboarding_state", "_update_onboarding_state",
    "_get_onboarding_projection", "_maybe_apply_completion",
    # the onboarding REST routes — every remaining entry is a WRITE path (a
    # bounded wait_for abandons a worker mid-write, CPython #87185 / #2863).
    # The GET route (``get_onboarding_state``) is NOT here: its read legs are
    # offloaded (#4625 review F3) and it is pinned by
    # ``test_onboarding_read_route_offloads_both_legs``.
    "patch_onboarding_state", "onboarding_checkpoint",
    "set_session_recording", "create_onboarding_org",
    "_create_onboarding_org_lane", "public_demo", "github_callback",
    # install probe + indexing lanes (write-side onboarding mirrors).
    "session_install_probe", "_run_indexing", "_run_docs_indexing",
    # sync seed helpers reached ON-LOOP from their async routes.
    "_run_onboarding_seed", "_run_starter_seed",
    # invitee member-progress arm (graph/onboarding write) — reached inline
    # from the invite-accept / register routes.
    "_arm_invitee_member_progress",
})


def _offloaded_callable_names(tree: ast.AST) -> set[str]:
    """Module-level helper names handed to an offload boundary as a callable.

    Such a helper runs in a worker, so its own calls are NOT on the loop and
    must not enter the reachable-on-loop closure. The callable may be wrapped in
    ``functools.partial`` (the ``_run_with_close`` shape).
    """
    names: set[str] = set()
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        if _callee_name(call.func) not in _ONBOARDING_OFFLOAD_BOUNDARIES:
            continue
        args = list(call.args)
        if _callee_name(call.func) == "run_in_executor":
            args = args[1:]  # arg 0 is the executor
        args += [kw.value for kw in call.keywords]
        for arg in args:
            if isinstance(arg, ast.Call) and _callee_name(arg.func) == "partial":
                for sub in arg.args:
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
            elif isinstance(arg, ast.Name):
                names.add(arg.id)
            elif isinstance(arg, ast.Attribute):
                names.add(arg.attr)
    return names


def _reachable_on_loop_functions(tree: ast.AST) -> dict[str, ast.AST]:
    """Module-level functions reachable ON the loop from ANY async body.

    A fixpoint over the call graph: an async body runs on the loop; a
    module-level function it calls INLINE (not through an offload boundary)
    runs there too, and so on. Sync helpers handed to an offload boundary as a
    callable are removed — they run in a worker. This is the sync-helper
    indirection the name-based scan would otherwise miss
    (``onboarding_seed`` -> ``_run_onboarding_seed`` -> ``_get_onboarding_projection``).
    """
    aliases = _module_aliases(tree)
    fns = {
        n.name: n for n in getattr(tree, "body", [])
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    on_loop = {name for name, n in fns.items()
               if isinstance(n, ast.AsyncFunctionDef)}
    changed = True
    while changed:
        changed = False
        for name in list(on_loop):
            node = fns.get(name)
            if node is None:
                continue
            for callee, _call in _unoffloaded_calls(
                    node, aliases, boundaries=_ONBOARDING_OFFLOAD_BOUNDARIES,
                    descend_nested=_on_loop_nested_names(node)):
                if callee in fns and callee not in on_loop:
                    on_loop.add(callee)
                    changed = True
    return {name: fns[name] for name in on_loop - _offloaded_callable_names(tree)}


def _onboarding_inline_calls() -> list[tuple[str, str, int, str]]:
    """``(source, body, line, callee)`` for inline calls to the blocking helpers.

    BOTH files are closed over the reachable-on-loop function set (async bodies
    + the sync helpers they reach), and each reachable body's INVOKED nested
    defs are scanned with it (#4625 review F1). ``mcp_server.py`` is closed over
    too (#4625 review F4): a module-level sync helper an async MCP body calls
    inline runs on the loop, so scanning async bodies alone was blind to it.
    The sync TOOLS stay out by construction — they are off-loop by fastmcp
    dispatch (``run_in_thread=True``, pinned by
    ``test_sync_mcp_tools_stay_off_loop``) and nothing reads them from an async
    body, so the closure never reaches them.
    """
    hits: list[tuple[str, str, int, str]] = []
    hosted_tree = ast.parse(HOSTED_API.read_text())
    aliases = _module_aliases(hosted_tree)
    for name, node in _reachable_on_loop_functions(hosted_tree).items():
        for callee, call in _unoffloaded_calls(
                node, aliases, boundaries=_ONBOARDING_OFFLOAD_BOUNDARIES,
                descend_nested=_on_loop_nested_names(node)):
            if callee in _ONBOARDING_BLOCKING_HELPERS:
                hits.append((HOSTED_API.name, name, call.lineno, callee))
    mcp_tree = ast.parse(MCP_SERVER.read_text())
    mcp_aliases = _module_aliases(mcp_tree)
    for name, node in _reachable_on_loop_functions(mcp_tree).items():
        for callee, call in _unoffloaded_calls(
                node, mcp_aliases, boundaries=_ONBOARDING_OFFLOAD_BOUNDARIES,
                descend_nested=_on_loop_nested_names(node)):
            if callee in _ONBOARDING_BLOCKING_HELPERS:
                hits.append((MCP_SERVER.name, name, call.lineno, callee))
    return sorted(hits)


def test_onboarding_blocking_helpers_are_offloaded_or_declared():
    """#4625: a hosted-helper / ``mcp_server`` call may not run inline silently.

    Every inline (non-boundary) call to an ``_ONBOARDING_BLOCKING_HELPERS``
    member reachable on the loop must be either a routed off-load or a declared
    residual. A NEW site fails here — the "fail on the next call site" guard
    for the class the two existing pins could not see.
    """
    offenders = [
        hit for hit in _onboarding_inline_calls()
        if not (hit[0] == HOSTED_API.name
                and hit[1] in _KNOWN_ONBOARDING_INLINE_RESIDUAL)
    ]
    assert not offenders, (
        "on-loop call(s) to a blocking onboarding helper (source, function, "
        f"line, callee): {offenders} — route them through the off-loop wrapper "
        "(`_get_onboarding_projection_off_loop` / "
        "`_session_recording_allowed_off_loop` / `_data_sdk_offloaded`), or add "
        "the body to `_KNOWN_ONBOARDING_INLINE_RESIDUAL` with a #4625 reason"
    )


def test_onboarding_helper_names_still_exist():
    """A rename must fail HERE, not silently vacate the #4625 pin."""
    defined = {
        n.name
        for path in (HOSTED_API, MCP_SERVER)
        for n in ast.walk(ast.parse(path.read_text()))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = sorted(_ONBOARDING_BLOCKING_HELPERS - defined)
    assert not missing, (
        f"the #4625 onboarding helper set names {missing}, which no longer "
        "exist — a rename must update the set, or the pin guards nothing"
    )


def test_onboarding_residual_names_still_exist():
    """A declared residual body that was renamed/deleted must fail here."""
    tree = ast.parse(HOSTED_API.read_text())
    defined = {n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    ghosts = sorted(_KNOWN_ONBOARDING_INLINE_RESIDUAL - defined)
    assert not ghosts, (
        f"{ghosts} are declared #4625 onboarding residual but no longer "
        "defined in hosted_api.py"
    )


def test_capture_session_recording_gate_is_offloaded():
    """#4625 leg 12, asserted PER CALLEE inside the residual body.

    ``_capture_session_impl`` is itself a declared residual body (its WRITE
    path and remaining reads), so the body-level assertion above cannot see
    this leg regress. Leg 62 (the graph seam) is pinned the same way in
    ``test_read_routes_loop_responsiveness.py`` — that file owns the graph
    seams; this one owns the control-plane/onboarding helpers.
    """
    tree = ast.parse(HOSTED_API.read_text())
    aliases = _module_aliases(tree)
    node = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef)
        and n.name == "_capture_session_impl"
    )
    callees = [
        name for name, _call in _unoffloaded_calls(
            node, aliases, boundaries=_ONBOARDING_OFFLOAD_BOUNDARIES)
    ]
    assert callees.count("_session_recording_allowed") == 0, (
        "#4625 leg 12 regressed: `_capture_session_impl` calls the blocking "
        "`_session_recording_allowed` inline — route it through "
        "`_session_recording_allowed_off_loop`"
    )


def test_onboarding_read_route_offloads_both_legs():
    """#4625 review F3: ``GET /v1/onboarding/state`` is a pure READ — both legs.

    The route's projection read rides ``_get_onboarding_projection_off_loop``
    and its email read rides ``asyncio.to_thread``. It is deliberately NOT in
    ``_KNOWN_ONBOARDING_INLINE_RESIDUAL``: that set is for WRITE paths (a
    bounded wait abandons a worker mid-write, CPython #87185), and this route
    writes nothing — so a regression here must fail, not hide behind a residual
    entry whose blanket reason covers a read.
    """
    tree = ast.parse(HOSTED_API.read_text())
    aliases = _module_aliases(tree)
    node = next(n for n in ast.walk(tree)
                if isinstance(n, ast.AsyncFunctionDef)
                and n.name == "get_onboarding_state")
    callees = [
        name for name, _call in _unoffloaded_calls(
            node, aliases, boundaries=_ONBOARDING_OFFLOAD_BOUNDARIES)
    ]
    assert callees.count("_get_onboarding_projection_off_loop") == 1, (
        "#4625 F3: `get_onboarding_state` must read the projection through "
        "`_get_onboarding_projection_off_loop`"
    )
    assert "_get_onboarding_projection" not in callees, (
        "#4625 F3 regressed: `get_onboarding_state` reads the projection "
        "inline — it must ride the off-loop wrapper"
    )
    assert "_org_email" not in callees, (
        "#4625 F3 regressed: `get_onboarding_state` reads the org email "
        "inline — it must ride `asyncio.to_thread`"
    )


def test_onboarding_offload_wrappers_are_used():
    """A wrapper nothing calls is not a fix — and a rename would silently
    vacate the leg it was added for (#4625).

    Both files are scanned: ``_get_onboarding_projection_off_loop`` is defined
    in ``hosted_api.py`` but its caller is the MCP gate in ``mcp_server.py``.
    """
    called = {
        _callee_name(call.func)
        for path in (HOSTED_API, MCP_SERVER)
        for call in ast.walk(ast.parse(path.read_text()))
        if isinstance(call, ast.Call)
    }
    missing = sorted(_ONBOARDING_OFFLOAD_WRAPPERS - called)
    assert not missing, (
        f"the #4625 offload wrapper(s) {missing} are never called — a leg they "
        "were added for has silently regressed or been renamed"
    )


def test_sync_mcp_tools_stay_off_loop():
    """The dispatch assumption behind scanning only ASYNC ``mcp_server`` bodies.

    fastmcp's ``FunctionTool.from_function`` defaults ``run_in_thread=True`` and
    ``FunctionTool._execute`` sends a sync tool body through
    ``anyio.to_thread.run_sync``, so the sync onboarding tools are already off
    the loop. If that default flips, they become on-loop work and this scan's
    scope is wrong — fail HERE rather than silently.
    """
    from fastmcp.tools import FunctionTool
    assert FunctionTool.model_fields["run_in_thread"].default is True, (
        "fastmcp's sync-tool dispatch default changed — the sync MCP tools may "
        "now run on the event loop; re-scope the #4625 mcp_server scan"
    )


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
    plus the ENFORCED SDK-acquisition phase), plus each plane's fail-closed flag
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
    # the ENFORCED SDK-acquisition phase). Sufficient for THIS shape since
    # #3446: the acquisition is a bounded phase of ``probe_db``, so the sum is
    # over deadlines the code imposes — but the residual is still STRANDING (a
    # phase that overruns its deadline is abandoned, not cancelled), not an
    # unenforced phase (see monitoring.PROBE_MAX_SUPERSEDES).
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
        f"{PROBE_SDK_ACQUISITION_BUDGET}s ENFORCED SDK-acquisition phase), or it "
        "abandons a live worker on every timeout (#2988). The phase is bounded "
        "(#3446); what remains is stranding — a phase that overruns is "
        "abandoned, not cancelled."
    )


def test_each_plane_bound_sits_above_its_own_client_timeout(monkeypatch):
    """The #2988 layered-timeout ordering, expressed PER PLANE.

    Abandoning a probe does not stop its thread — ``wait_for`` cancels the
    awaitable, not the worker (CPython #87185), so the worker stays parked in
    its socket read. The defence is ordering: keep the outer bound ABOVE the
    probe's loose inner figure (``PROBE_DB_TOTAL_TIMEOUT`` — deliberately an
    OVER-ESTIMATE, not the exact inner total), so the inner bound normally fires
    first and the thread returns by itself. For the DB plane this ordering is
    now PROVABLE as an inequality between ENFORCED deadlines (#3446 — the
    acquisition is a bounded phase of ``probe_db``), though the residual is
    still stranding: a phase that overruns its own deadline is abandoned, not
    cancelled. For the CONTROL plane it remains a best-effort ALIGNMENT: httpx's
    ``read`` is per-read, so that inner request has no enforceable total.

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
    """The DB probes' bound clears the whole worker path ``probe_db`` runs.

    ``_probe_db`` is ``probe_db(acquire=_acquire_probe_sdk)``: since #3446 the
    SDK lookup runs INSIDE ``probe_db`` as a bounded phase under
    ``PROBE_SDK_ACQUISITION_BUDGET``, not as a prefix on the coordinator's
    thread before the call. In URI mode (the hosted steady state) that phase is
    ~free — the SDK's projection is LAZY and the connect happens inside
    ``probe_db``'s own per-attempt bound. On the EMBEDDED path (cold
    ``_probe_sdk`` cache / ``_probe_sdk_reset()``) the anchor path connects
    EAGERLY and runs real queries, so the phase is REPORTED as failed when it
    cannot finish inside its budget — the budget bounds the PHASE, not the
    interior. The bound is DERIVED as ``PROBE_DB_TOTAL_TIMEOUT`` +
    ``PROBE_SDK_ACQUISITION_BUDGET`` — pin both the budget's source (so it
    cannot drift from projection) and the derivation (so a ``PROBE_TIMEOUT``
    change propagates instead of leaving a stale hand-typed literal).
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
    # inner total — PLUS the acquisition budget). Since #3446 the acquisition is
    # an ENFORCED phase (``probe_db(acquire=…)``); ``PROBE_DB_TOTAL_TIMEOUT``
    # itself is still an over-estimate nothing enforces, which is precisely why
    # this margin sits above the LOOSE figure. Equality would still be a race.
    assert mod.DB_PROBE_HARD_TIMEOUT > \
        PROBE_DB_TOTAL_TIMEOUT + PROBE_SDK_ACQUISITION_BUDGET, (
        "DB_PROBE_HARD_TIMEOUT must sit strictly above PROBE_DB_TOTAL_TIMEOUT + "
        "the SDK-acquisition budget, or the outer bound can win the race"
    )


def test_db_probe_inner_path_is_a_sum_of_enforced_deadlines():
    """#3446: the outer DB bound is DERIVED above a sum the code ENFORCES.

    The pre-#3446 defect was never the arithmetic — ``PROBE_HARD_TIMEOUT`` has
    always been ``PROBE_DB_TOTAL_TIMEOUT + PROBE_SDK_ACQUISITION_BUDGET +
    PROBE_DB_BOUND_MARGIN_S``. It was that ONE of the summed terms named a
    phase NOTHING bounded: the SDK acquisition ran inline on the coordinator's
    own thread. An outer bound can only be PROVEN above a sum of deadlines the
    code actually imposes, so the derivation was unsound while the constant
    looked right — which is why the previous test's ordering assertion passed
    and proved nothing.

    ``probe_db(acquire=…)`` closes that by making the acquisition a bounded
    phase. This test pins the resulting arithmetic; the ENFORCEMENT is pinned
    behaviourally in
    ``tests/test_monitoring.py::TestProbeDbBoundedAcquisition`` (the phase is
    abandoned at its budget, on the shared worker, exactly once per call) and
    the WIRING in ``tests/test_hosted_api.py``'s ``probe_db`` spy plus
    ``test_selfhost_health_probe_executor``'s AST check — deliberately not
    duplicated here.

    LOAD-BEARING — this test reads CONSTANTS only, so it can pin the derivation
    but cannot observe the enforcement. Its red is a constant move: set
    ``PROBE_DB_BOUND_MARGIN_S = 0.0`` (verified) and the derivation lock and
    the positive-margin assertion both red, which is what forces a re-derivation
    rather than a silent drift. The ENFORCEMENT itself is reddened where it is
    observable —
    ``tests/test_monitoring.py::TestProbeDbBoundedAcquisition`` (mutation:
    ``future.result(timeout=budget)`` -> ``future.result()``) — and the WIRING
    in ``tests/test_hosted_api.py``'s ``probe_db`` spy (mutation: revert
    ``_probe_db`` to ``probe_db(_probe_sdk)``) plus
    ``test_selfhost_health_probe_executor``'s AST check, neither duplicated
    here. A new phase added inside ``probe_db`` is NOT caught by any of them —
    see the warning in ``probe_db``'s docstring.
    """
    import tortoise.hosted_api as mod
    from tortoise.monitoring import (
        PROBE_DB_BOUND_MARGIN_S,
        PROBE_DB_TOTAL_TIMEOUT,
        PROBE_HARD_TIMEOUT,
        PROBE_RETRY_DELAY,
        PROBE_SDK_ACQUISITION_BUDGET,
        PROBE_TIMEOUT,
    )

    # (1) DERIVATION LOCK — the outer bound IS this sum, not a literal that
    # happens to match it. Moving any contributor moves the bound with it.
    assert PROBE_HARD_TIMEOUT == (
        PROBE_DB_TOTAL_TIMEOUT + PROBE_SDK_ACQUISITION_BUDGET
        + PROBE_DB_BOUND_MARGIN_S), (
        "PROBE_HARD_TIMEOUT drifted from the sum it is defined as "
        "(PROBE_DB_TOTAL_TIMEOUT + PROBE_SDK_ACQUISITION_BUDGET + "
        "PROBE_DB_BOUND_MARGIN_S)"
    )
    assert PROBE_DB_TOTAL_TIMEOUT == 2 * PROBE_TIMEOUT + PROBE_RETRY_DELAY, (
        "PROBE_DB_TOTAL_TIMEOUT drifted from the alignment structure it "
        "describes (2 x PROBE_TIMEOUT + PROBE_RETRY_DELAY)"
    )
    # A zero/negative margin would let the outer bound equal the inner sum —
    # the worker's own timeout and the coordinator's deadline would fire in the
    # same instant and the worker still needs a moment to record its result.
    assert PROBE_DB_BOUND_MARGIN_S > 0, (
        "the strict-above margin must be positive, or '> inner' degenerates "
        "into '== inner' and the race returns"
    )

    # (2) THE ENFORCED INNER TOTAL — the PLATFORM shape's real inner path is
    # the acquisition phase plus probe_db's single combined deadline. Since
    # #3446 both are deadlines ``probe_db`` imposes, so this is the sum the
    # outer bound has to clear; the loose PROBE_DB_TOTAL_TIMEOUT figure above
    # is deliberately an OVER-estimate and is the STRICTER check.
    platform_inner = PROBE_TIMEOUT + PROBE_SDK_ACQUISITION_BUDGET
    assert platform_inner < mod.DB_PROBE_HARD_TIMEOUT, (
        f"DB_PROBE_HARD_TIMEOUT ({mod.DB_PROBE_HARD_TIMEOUT}s) must exceed the "
        f"platform shape's enforced inner total ({platform_inner}s = "
        f"PROBE_TIMEOUT {PROBE_TIMEOUT}s + the acquisition phase "
        f"{PROBE_SDK_ACQUISITION_BUDGET}s)"
    )
    assert mod.DB_PROBE_HARD_TIMEOUT > PROBE_DB_TOTAL_TIMEOUT, (
        "the outer bound must also clear probe_db's own single deadline"
    )


# ── the selfhost twin of the same defect ───────────────────────────────────


def test_selfhost_ready_does_not_probe_on_the_loop():
    """``tortoise/selfhost.py::health_ready`` had the identical bug: it built the
    SDK and touched the DB inline. ``publish-selfhost.yml`` curls this endpoint
    on every publish, so it is the same outage vector in the other image.

    #3287 — the pin FLIPPED. It used to require ``asyncio.to_thread`` here,
    because that was #2988's fix. ``to_thread`` is no longer acceptable on this
    endpoint: it always submits to the event loop's SHARED default executor,
    whose queue is unbounded, so unrelated work can queue the probe past
    ``_READY_PROBE_TIMEOUT_S`` and make a HEALTHY DB report a false 503 — which
    fails the publish. The endpoint must dispatch through the module's own
    pool (``_submit_probe``) instead. Guarding the new shape here is what stops
    a refactor quietly reintroducing the shared pool.
    """
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
    shared_pool = [
        call.lineno
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "to_thread"
    ]
    assert not shared_pool, (
        f"selfhost health_ready uses asyncio.to_thread at line(s) {shared_pool} — "
        "to_thread submits to the loop's SHARED default executor, where unrelated "
        "work starves the probe and a healthy DB reports a false 503 (#3287)"
    )
    dedicated = [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "_submit_probe"
    ]
    assert dedicated, (
        "selfhost health_ready dispatches nothing through the module's dedicated "
        "probe pool (_submit_probe) — #3287"
    )
    # Presence alone is not enough: a call whose future is dropped satisfies the
    # check above while the endpoint answers nothing. The future must be AWAITED,
    # so the probe's result is what the handler responds with.
    parents = {
        id(child): parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)
    }
    not_awaited = [
        call.lineno
        for call in dedicated
        if not any(a is not None and isinstance(a, ast.Await) for a in _ancestors(call, parents))
    ]
    assert not not_awaited, (
        f"selfhost health_ready calls _submit_probe at line(s) {not_awaited} but never "
        "awaits its future — the probe's result is discarded, so the endpoint answers "
        "whatever the fall-through path produces (#3287)"
    )


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
