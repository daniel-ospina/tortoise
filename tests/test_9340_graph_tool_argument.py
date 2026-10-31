"""#9340 — the graph is a TOOL ARGUMENT, not a token claim.

The owner's 2026-10-22 decision on #9340 supersedes requirements 1 and 5 of the
issue body: a graph is working context inside one workspace, so a connector
selects it per call (`graph`), the token stays `(user, workspace, scope)`, and
the DEFAULT graph remains the untouched path.

What these tests pin, in the decision's own terms:

* the resolution rule — an **id** wins over a name; a **name** resolves only
  when it is unique in the token's workspace, otherwise the call FAILS listing
  the candidates ("a silent wrong-graph write is the failure class this feature
  is most likely to cause");
* the IDOR guard — a graph id belonging to ANOTHER workspace is refused on
  every call, never resolved, and never confirmed to exist;
* the two live edge cases — a soft-deleted graph stops resolving on the next
  call, and a graph-bound API key cannot be re-targeted (that would widen a
  credential past its mint);
* discovery — `tortoise_list_graphs` names the workspace's OTHER graphs (so the
  argument is usable at all) while still never enumerating another tenant's,
  and the prefix trap that would leak one is covered;
* the surface — the argument is advertised on graph-touching tools and NOT on
  the tools that name their graph explicitly, and the HANDLER signatures are
  untouched (the owner-reviewed `served_attributes` baseline does not move).
* the argument is HONOURED where it is advertised — a handler that names its
  own graph (`tortoise_entity_profile`, `tortoise_traverse`, `tortoise_analyze`)
  reads the SELECTED namespace, not the workspace default, and the derivation
  that asserts it is proven non-vacuous in both directions (no handler names
  the default itself; every graph-naming handler routes through the one
  sanctioned helper). Advertising an argument a handler then ignores is the
  same silent wrong-graph class as not having it.
* a lookup that fails with its OWN `TimeoutError` (a control-plane/socket
  timeout — `socket.timeout` IS `TimeoutError` on 3.12) surfaces that failure
  instead of being misreported as a breach of the transport wait budget.

Before this change there was no argument at all: `tortoise_query(graph=...)`
was not a call a connector could make, so every one of these cases was
unreachable and every connector landed on the workspace's default graph.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import threading
import time as _time

import pytest
from fastmcp.exceptions import ToolError

from tortoise import mcp_auth, mcp_server

ORG_A = "org_a"
ORG_B = "org_b"


def _graph(gid, name, *, kind="custom", namespace=None, status="active"):
    return {
        "graph_id": gid,
        "org_id": ORG_A,
        "name": name,
        "kind": kind,
        "namespace": namespace or f"org_{ORG_A}_{gid}",
        "status": status,
    }


DEFAULT = _graph("default", "default", kind="default", namespace=f"org_{ORG_A}")
FUND_A = _graph("g_fund_a", "Fund A / Research")
FUND_B = _graph("g_fund_b", "Fund B / Research")
DUP_1 = _graph("g_dup_1", "Research")
DUP_2 = _graph("g_dup_2", "research")
DELETED = _graph("g_gone", "Gone", status="deleted")
OTHER_TENANT = {
    "graph_id": "g_other",
    "org_id": ORG_B,                      # another workspace's row
    "name": "Other tenant graph",
    "kind": "custom",
    "namespace": "org_org_b_x",
    "status": "active",
}

#: What `list_memory_graphs(ORG_A)` returns — the caller's own rows incl. a tombstone.
OWN_ROWS = [DEFAULT, FUND_A, FUND_B, DUP_1, DUP_2, DELETED]
#: The same set with a FOREIGN row a leaky reader could hand back; the
#: resolver must drop it locally rather than trust the reader.
ROWS = [*OWN_ROWS, OTHER_TENANT]


class _FakeSDK:
    """The control-plane reader `list_memory_graphs` stands in for."""

    def __init__(self, rows):
        self._rows = rows

    def list_memory_graphs(self, org_id):  # one tenant in these tests
        return list(self._rows)


# ── the resolution rule: id first, name only when unique ────────────────────


def test_a_graph_id_resolves_and_its_namespace_comes_back():
    row = mcp_auth.resolve_graph_target(ORG_A, "g_fund_a", rows=ROWS)
    assert row["graph_id"] == "g_fund_a"
    assert row["namespace"] == f"org_{ORG_A}_g_fund_a"


def test_an_id_wins_over_a_name_that_happens_to_collide():
    """A name collision must never steal an id's dispatch."""
    rows = [*ROWS, _graph("g_named_default", "default")]
    row = mcp_auth.resolve_graph_target(ORG_A, "default", rows=rows)
    assert row["graph_id"] == "default", "an id lost to a same-named custom graph"


def test_a_unique_name_resolves_case_insensitively():
    row = mcp_auth.resolve_graph_target(ORG_A, "fund a / research", rows=ROWS)
    assert row["graph_id"] == "g_fund_a"


def test_a_duplicate_name_is_refused_with_the_candidates():
    """DECISION: a name matching more than one graph FAILS listing candidates."""
    with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
        mcp_auth.resolve_graph_target(ORG_A, "Research", rows=ROWS)
    message = str(excinfo.value)
    assert "matches 2 graphs" in message, message
    assert "g_dup_1" in message and "g_dup_2" in message, message
    assert "Pass the graph id instead" in message, message


def test_a_name_matching_a_second_graph_only_by_casing_is_still_ambiguous():
    """`Research` and `research` are two graphs; guessing one is a wrong-graph write."""
    with pytest.raises(mcp_auth.GraphTargetError):
        mcp_auth.resolve_graph_target(ORG_A, "RESEARCH", rows=ROWS)


def test_a_namespace_resolves_so_a_listed_graph_can_be_passed_straight_back():
    row = mcp_auth.resolve_graph_target(
        ORG_A, f"org_{ORG_A}_g_fund_b", rows=ROWS)
    assert row["graph_id"] == "g_fund_b"


# ── the IDOR guard: another workspace's graph is unreachable ───────────────


def test_another_workspaces_graph_is_refused_and_its_existence_is_not_confirmed():
    with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
        mcp_auth.resolve_graph_target(ORG_A, "g_other", rows=ROWS)
    message = str(excinfo.value)
    assert "No graph 'g_other' is reachable from this workspace" in message, message
    # The refusal must not confirm the id exists somewhere else.
    assert "workspace b" not in message.lower(), message
    assert "org_org_b_x" not in message, message


def test_the_refusal_names_what_the_caller_can_reach():
    with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
        mcp_auth.resolve_graph_target(ORG_A, "nope", rows=ROWS)
    message = str(excinfo.value)
    for reachable in ("g_fund_a", "g_fund_b", "default"):
        assert reachable in message, message
    assert "g_gone" not in message, "a deleted graph was offered as reachable"


# ── the live edge cases ────────────────────────────────────────────────────


def test_a_soft_deleted_graph_stops_resolving_on_the_next_call():
    """Deleted WHILE a connector is live → refuse, never fall back to default."""
    with pytest.raises(mcp_auth.GraphTargetError):
        mcp_auth.resolve_graph_target(ORG_A, "g_gone", rows=ROWS)


def test_reachable_rows_drops_a_tombstoned_status():
    rows = mcp_auth.reachable_graph_rows(ORG_A, sdk=_FakeSDK(ROWS))
    ids = {r["graph_id"] for r in rows}
    assert "g_gone" not in ids
    assert ids == {"default", "g_fund_a", "g_fund_b", "g_dup_1", "g_dup_2"}


def test_a_row_from_another_workspace_is_dropped_by_the_reachable_set():
    """The resolver does not TRUST `list_memory_graphs` to be org-scoped.

    `list_memory_graphs(org_id)` is org-scoped today; this pins the second, local
    check, so a future reader change cannot turn a namespace into a
    cross-tenant opener.
    """
    rows = mcp_auth.reachable_graph_rows(ORG_A, sdk=_FakeSDK([DEFAULT, OTHER_TENANT]))
    assert [r["graph_id"] for r in rows] == ["default"], rows


def test_a_graph_bound_key_cannot_be_retargeted_by_the_argument(monkeypatch):
    """Widening a minted graph-bound credential is the escalation to refuse."""
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    tokens = [mcp_auth._current_org_id.set(ORG_A),
              mcp_auth._current_graph_id.set("g_fund_a"),
              mcp_auth._current_graph_namespace.set(f"org_{ORG_A}_g_fund_a")]
    try:
        with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
            mcp_auth.apply_graph_target("g_fund_b")
        assert "bound to one graph" in str(excinfo.value)
        assert mcp_auth._current_graph_id.get() == "g_fund_a", (
            "a refused re-target changed the credential's graph")
    finally:
        mcp_auth._current_graph_namespace.reset(tokens[2])
        mcp_auth._current_graph_id.reset(tokens[1])
        mcp_auth._current_org_id.reset(tokens[0])


def test_a_graph_bound_key_cannot_be_redirected_to_the_DEFAULT_graph(monkeypatch):
    """The bound check runs BEFORE the default early-return.

    Otherwise a graph-bound key asking for the workspace default is TOLD it got
    `default` while every call keeps reading its own graph — a silent
    wrong-graph outcome freshly confirmed to the caller.
    """
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    tokens = [mcp_auth._current_org_id.set(ORG_A),
              mcp_auth._current_graph_id.set("g_fund_a"),
              mcp_auth._current_graph_namespace.set(f"org_{ORG_A}_g_fund_a")]
    try:
        for ref in ("default", f"org_{ORG_A}"):
            with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
                mcp_auth.apply_graph_target(ref)
            assert "bound to one graph" in str(excinfo.value), ref
        assert mcp_auth._current_graph_id.get() == "g_fund_a"
    finally:
        mcp_auth._current_graph_namespace.reset(tokens[2])
        mcp_auth._current_graph_id.reset(tokens[1])
        mcp_auth._current_org_id.reset(tokens[0])


def test_a_graph_bound_key_may_restate_its_own_graph(monkeypatch):
    """Refusing the bound check must not refuse a no-op re-selection."""
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    tokens = [mcp_auth._current_org_id.set(ORG_A),
              mcp_auth._current_graph_id.set("g_fund_a"),
              mcp_auth._current_graph_namespace.set(f"org_{ORG_A}_g_fund_a")]
    try:
        assert mcp_auth.apply_graph_target("g_fund_a")["graph_id"] == "g_fund_a"
    finally:
        mcp_auth._current_graph_namespace.reset(tokens[2])
        mcp_auth._current_graph_id.reset(tokens[1])
        mcp_auth._current_org_id.reset(tokens[0])


def test_a_custom_graph_row_without_an_id_is_refused(monkeypatch):
    """`_get_org_sdk()` opens a custom graph only from (id, namespace).

    A row missing the id would fall through to the workspace DEFAULT while the
    caller believes it selected a custom graph.
    """
    broken = {**_graph("unused", "Half a row"), "graph_id": None}
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: [DEFAULT, broken])
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
            mcp_auth.apply_graph_target(f"org_{ORG_A}_unused")
        assert "cannot be opened" in str(excinfo.value)
        assert mcp_auth._current_graph_id.get() is None
    finally:
        mcp_auth._current_org_id.reset(org)


@pytest.mark.parametrize("bad", [5, 5.5, True, ["g_fund_a"], {"id": "x"}, object()])
def test_a_non_string_graph_argument_is_a_refusal_not_a_crash(monkeypatch, bad):
    """The advertised `string` type is descriptive, not enforced on this path.

    An int/list/object must produce the bad-argument refusal — an AttributeError
    escaping the dispatch would be classified a SERVER error instead.

    Both entry points are pinned WITH A WORKSPACE IN CONTEXT: without one,
    `apply_graph_target` raises the transport refusal for ANY value, so the
    assertion would pass for a perfectly valid id too and the guard here would
    be unenforced.
    """
    with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
        mcp_auth.resolve_graph_target(ORG_A, bad, rows=ROWS)
    assert "must be a graph id or name" in str(excinfo.value)

    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        with pytest.raises(mcp_auth.GraphTargetError) as excinfo2:
            mcp_auth.apply_graph_target(bad)
        assert "must be a graph id or name" in str(excinfo2.value), (
            "the refusal came from somewhere else — this does not pin the type guard")
        assert mcp_auth._current_graph_id.get() is None
    finally:
        mcp_auth._current_org_id.reset(org)


# ── the default path is untouched, and a custom graph really scopes ────────


def test_no_argument_leaves_every_context_var_alone(monkeypatch):
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        assert mcp_auth.apply_graph_target(None) is None
        assert mcp_auth._current_graph_id.get() is None
        assert mcp_auth._current_graph_namespace.get() is None
    finally:
        mcp_auth._current_org_id.reset(org)


def test_the_default_graph_sets_no_override(monkeypatch):
    """`graph="default"` must be byte-identical to omitting the argument."""
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        assert mcp_auth.apply_graph_target("default") is None
        assert mcp_auth._current_graph_id.get() is None
        assert mcp_auth._current_graph_namespace.get() is None
    finally:
        mcp_auth._current_org_id.reset(org)


def test_the_default_graph_needs_no_listed_row(monkeypatch):
    """A workspace whose graph list comes back EMPTY still HAS a default graph.

    `supabase_control.graph_metadata` returns `[]` when the workspace's stored
    `graph_name` is empty (an anticipated, fail-soft shape), and
    `reachable_graph_rows` reads a non-list reader answer as `[]`. A resolver
    that can only match a `graph_id="default"` ROW therefore refuses
    `graph="default"` — while the SAME call with the argument omitted reads
    that very graph — and the refusal actively misinforms the caller by
    reporting "Reachable graphs: none".
    """
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: [])
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        assert mcp_auth.apply_graph_target("default") is None
        assert mcp_auth._current_graph_id.get() is None
        assert mcp_auth._current_graph_namespace.get() is None
        # …and it is the same no-op as omitting the argument.
        assert mcp_auth.apply_graph_target(None) is None
    finally:
        mcp_auth._current_org_id.reset(org)


def test_the_dispatch_accepts_the_default_graph_with_an_empty_row_set(monkeypatch):
    """The user-visible half: `graph="default"` must not be a ToolError."""
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: [])
    org = mcp_auth._current_org_id.set(ORG_A)

    async def _dispatch():
        stack = contextlib.ExitStack()
        try:
            await mcp_server._resolve_graph_argument("default", stack)
            return mcp_auth._current_graph_id.get()
        finally:
            stack.close()

    try:
        assert asyncio.run(_dispatch()) is None
    finally:
        mcp_auth._current_org_id.reset(org)


def test_the_default_graph_needs_no_workspace_either(monkeypatch):
    """stdio / self-host: `"default"` is trivially satisfiable, so accept it.

    `graph` is advertised on the registered tools for EVERY transport, so a
    connector that sets it uniformly (the usage the argument exists for) would
    otherwise lose every tool on stdio/self-host — for a no-op. A NAME still
    refuses there, because there is no graph set to check it against.
    """
    org = mcp_auth._current_org_id.set(None)
    try:
        assert mcp_auth.apply_graph_target("default") is None
        assert mcp_auth._current_graph_id.get() is None
        assert mcp_auth._current_graph_namespace.get() is None
        with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
            mcp_auth.apply_graph_target("Fund A / Research")
        assert "requires a hosted workspace connection" in str(excinfo.value)
    finally:
        mcp_auth._current_org_id.reset(org)


def test_a_bound_credential_cannot_ask_for_the_default_graph(monkeypatch):
    """The short-circuit must NOT widen a graph-bound key past its mint.

    Accepting `"default"` without a lookup is only safe while the bound check
    still fires: a key minted for graph X asked for "default" would otherwise
    be told "default" while every call kept reading X.
    """
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: [])
    tokens = [mcp_auth._current_org_id.set(ORG_A),
              mcp_auth._current_graph_id.set("g_fund_a")]
    try:
        with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
            mcp_auth.apply_graph_target("default")
        assert "bound to one graph" in str(excinfo.value)
        assert mcp_auth._current_graph_id.get() == "g_fund_a", (
            "a refused re-target moved the credential's own graph")
    finally:
        mcp_auth._current_graph_id.reset(tokens[1])
        mcp_auth._current_org_id.reset(tokens[0])


def test_a_non_string_graph_is_the_type_refusal_on_every_transport():
    """A caller's type error must not be answered as "this transport"."""
    org = mcp_auth._current_org_id.set(None)
    try:
        with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
            mcp_auth.apply_graph_target(5)
        message = str(excinfo.value)
        assert "must be a graph id or name (a string)" in message, message
        assert "hosted workspace connection" not in message, message
    finally:
        mcp_auth._current_org_id.reset(org)


def test_a_custom_graph_scopes_the_request_sdk(monkeypatch):
    """The resolved graph reaches `_get_org_sdk()` — the ONE graph opener."""
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        assert mcp_auth.apply_graph_target("Fund A / Research")["graph_id"] == "g_fund_a"
        assert mcp_auth._current_graph_id.get() == "g_fund_a"
        assert mcp_auth._current_graph_namespace.get() == f"org_{ORG_A}_g_fund_a"
    finally:
        mcp_auth._current_org_id.set(None)
        mcp_auth._current_graph_namespace.set(None)
        mcp_auth._current_graph_id.set(None)
        mcp_auth._current_org_id.reset(org)


def test_a_custom_graph_without_a_namespace_is_refused_not_opened_as_default(monkeypatch):
    broken = {**_graph("g_nons", "No namespace"), "namespace": None}
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: [broken])
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
            mcp_auth.apply_graph_target("g_nons")
        assert "missing its graph id or its storage namespace" in str(excinfo.value)
        assert mcp_auth._current_graph_id.get() is None
    finally:
        mcp_auth._current_org_id.reset(org)


def test_a_transport_without_a_workspace_refuses_instead_of_attaching(monkeypatch):
    """stdio/self-host has no graph set to check a name against."""
    org = mcp_auth._current_org_id.set(None)
    try:
        with pytest.raises(mcp_auth.GraphTargetError) as excinfo:
            mcp_auth.apply_graph_target("Fund A / Research")
        assert "requires a hosted workspace connection" in str(excinfo.value)
    finally:
        mcp_auth._current_org_id.reset(org)


# ── the dispatch seam ──────────────────────────────────────────────────────


def test_the_dispatch_strips_the_argument_before_any_tool_sees_it():
    """The handler must never receive a keyword its signature does not declare."""
    arguments = {"query": "x", "graph": "g_fund_b"}
    assert mcp_server._take_graph_argument("tortoise_query", arguments) == "g_fund_b"
    assert arguments == {"query": "x"}
    assert mcp_server._take_graph_argument("tortoise_query", {"query": "x"}) is None
    assert mcp_server._take_graph_argument("tortoise_query", None) is None


def test_the_dispatch_scopes_the_call(monkeypatch):
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    org = mcp_auth._current_org_id.set(ORG_A)

    async def _dispatch():
        # The scope is read INSIDE the task on purpose: `asyncio.run` gives the
        # coroutine its own context copy, and in production the dispatch and the
        # tool body share the request's task — which is exactly why scoping a
        # ContextVar here reaches every tool call.
        stack = contextlib.ExitStack()
        try:
            await mcp_server._resolve_graph_argument("g_fund_b", stack)
            return (mcp_auth._current_graph_id.get(),
                    mcp_auth._current_graph_namespace.get())
        finally:
            stack.close()

    try:
        assert asyncio.run(_dispatch()) == ("g_fund_b", f"org_{ORG_A}_g_fund_b")
    finally:
        mcp_auth._current_org_id.reset(org)


def test_a_cross_workspace_graph_id_cannot_be_reached_through_the_dispatch(monkeypatch):
    """The IDOR case, at the seam a tool call actually crosses."""
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        stack = contextlib.ExitStack()
        try:
            with pytest.raises(ToolError) as excinfo:
                asyncio.run(mcp_server._resolve_graph_argument("g_other", stack))
        finally:
            stack.close()
        assert "is reachable from this workspace" in str(excinfo.value)
        assert mcp_auth._current_graph_id.get() is None, (
            "a refused graph still scoped the dispatch")
    finally:
        mcp_auth._current_org_id.reset(org)


def test_a_tool_that_names_its_graph_explicitly_refuses_the_argument(monkeypatch):
    """Silently ignoring `graph` is how a caller believes it chose a graph."""
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        with pytest.raises(ToolError) as excinfo:
            mcp_server._take_graph_argument(
                "tortoise_list_graphs", {"graph": "g_fund_a"})
        assert "does not take a `graph` argument" in str(excinfo.value)
    finally:
        mcp_auth._current_org_id.reset(org)


def test_resolution_does_not_run_on_the_event_loop_thread(monkeypatch):
    """The reachable-set read is a control-plane query: it must be offloaded.

    A ContextVar set inside `asyncio.to_thread` dies with the thread's context
    copy, so the split (plan in the thread, `use_graph_target` on the loop) is
    the only shape that both offloads the I/O and scopes the request. This pins
    that the read happens on a DIFFERENT thread and the scope still lands.
    """
    loop_thread = threading.get_ident()
    seen: list[int] = []

    def _rows(org_id):
        seen.append(threading.get_ident())
        return OWN_ROWS

    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", _rows)
    org = mcp_auth._current_org_id.set(ORG_A)

    async def _dispatch():
        stack = contextlib.ExitStack()
        try:
            await mcp_server._resolve_graph_argument("g_fund_a", stack)
            return mcp_auth._current_graph_id.get()
        finally:
            stack.close()

    try:
        assert asyncio.run(_dispatch()) == "g_fund_a"
        assert seen, "the resolver never read the reachable set"
        assert seen[0] != loop_thread, (
            "the control-plane read ran ON the event loop thread")
    finally:
        mcp_auth._current_org_id.reset(org)


def test_a_call_without_the_argument_is_untouched():
    assert mcp_server._take_graph_argument("tortoise_query", {"query": "x"}) is None
    assert mcp_server._take_graph_argument("tortoise_query", None) is None
    assert mcp_auth.use_graph_target(None) is None
    assert mcp_auth._current_graph_id.get() is None

    stack = contextlib.ExitStack()
    try:
        asyncio.run(mcp_server._resolve_graph_argument(None, stack))
    finally:
        stack.close()
    assert mcp_auth._current_graph_id.get() is None


def test_the_dispatch_resolution_is_bounded_and_refuses_legibly(monkeypatch):
    """A degraded control plane must not eat the budget and abandon a write.

    The reachable-set read is a control-plane query. If it were unbounded it
    could consume the caller's whole transport budget and the dispatch would
    then be abandoned mid-write while the caller is told to retry — a retry
    duplicates the write.
    """
    import time as _time

    def _slow(org_id):
        _time.sleep(0.5)
        return OWN_ROWS

    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", _slow)
    monkeypatch.setattr(mcp_server, "_wait_bound_arrival", lambda: _time.monotonic() - 60.0)
    org = mcp_auth._current_org_id.set(ORG_A)

    async def _dispatch():
        stack = contextlib.ExitStack()
        try:
            await mcp_server._resolve_graph_argument("g_fund_a", stack)
            return mcp_auth._current_graph_id.get()
        finally:
            stack.close()

    try:
        with pytest.raises(ToolError) as excinfo:
            asyncio.run(_dispatch())
        message = str(excinfo.value)
        assert "wait budget" in message and "Retry after" in message, message
        assert "(The graph lookup exceeded the wait budget.)" in message, (
            "with NO time left the refusal must be the shipped #9340 wording — "
            "the margin message ('did not finish inside the time it had left') "
            f"would misdescribe a budget that was already fully spent: {message}")
        assert mcp_auth._current_graph_id.get() is None, (
            "a timed-out lookup still scoped the request")
    finally:
        mcp_auth._current_org_id.reset(org)


class TestTheRefusalIsMeasurable:
    """A refused `graph` is a CALLER error, and the series must be able to say so.

    `GraphTargetError` carries `code = "invalid_graph"`; the dispatch turned it
    into a bare `ToolError` (`from None`), so `_classify_mcp_call_error` saw no
    cause and recorded `exec_error / ToolError` — the caller's argument error
    bucketed with server faults, and the one question this new surface has to
    answer ("how often do connectors send an invalid graph?") unanswerable from
    the same `mcp_tool_call` series the repo justifies its policies from.
    """

    def test_a_refused_graph_is_a_coded_validation_error(self, monkeypatch):
        monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
        org = mcp_auth._current_org_id.set(ORG_A)

        async def _dispatch():
            stack = contextlib.ExitStack()
            try:
                await mcp_server._resolve_graph_argument("g_nowhere", stack)
            finally:
                stack.close()

        try:
            with pytest.raises(ToolError) as excinfo:
                asyncio.run(_dispatch())
        finally:
            mcp_auth._current_org_id.reset(org)
        assert mcp_server._classify_mcp_call_error(excinfo.value) == (
            "validation_error", "invalid_graph"), (
            "a refused `graph` must be an identifiable argument refusal, not "
            "an un-coded server fault")


def test_a_bound_credential_with_no_resolved_namespace_never_reads_the_default():
    """A HALF-resolved graph credential must fail closed, as REST does.

    `supabase_control._graph_namespace_for` and `sdk.apikey_verify` both return
    `None` on a missing row OR a failed read, and both document that as
    fail-closed; the REST lane refuses the identical state (403
    GRAPH_NOT_FOUND). `_get_org_sdk()` used to fall through to
    `namespace=<org>`, i.e. open the workspace DEFAULT graph with a
    graph-scoped key — silently, on the one lane where every other
    cross-graph move is an explicit refusal.
    """
    tokens = [mcp_auth._current_org_id.set(ORG_A),
              mcp_auth._current_graph_id.set("g_fund_a")]
    try:
        assert mcp_auth._current_graph_namespace.get() is None
        with pytest.raises(mcp_auth.AuthorizationError) as excinfo:
            mcp_auth._get_org_sdk()
        assert "storage namespace could not be resolved" in str(excinfo.value)
    finally:
        mcp_auth._current_graph_id.reset(tokens[1])
        mcp_auth._current_org_id.reset(tokens[0])


def test_a_lookup_timeout_is_not_misreported_as_our_budget(monkeypatch):
    """A control-plane `TimeoutError` is not a breach of the TRANSPORT budget.

    `asyncio.wait_for` converts BOTH its own deadline and any builtin
    `TimeoutError` raised inside the awaited coroutine into `TimeoutError`, so a
    control-plane or socket timeout (`socket.timeout` IS `TimeoutError` on 3.12)
    would be reported to the caller as "the graph lookup exceeded the wait
    budget … Retry after Ns" — a wrong, retry-shaped story about a failure that
    has nothing to do with the caller's arrival budget, with the real cause
    discarded. `cm.expired()` is the only thing that distinguishes them.
    """
    def _boom(ref):
        raise TimeoutError("postgrest: connection timed out")

    monkeypatch.setattr(mcp_auth, "plan_graph_target", _boom)
    org = mcp_auth._current_org_id.set(ORG_A)

    async def _dispatch():
        stack = contextlib.ExitStack()
        try:
            await mcp_server._resolve_graph_argument("g_fund_a", stack)
        finally:
            stack.close()

    try:
        with pytest.raises(TimeoutError) as excinfo:
            asyncio.run(_dispatch())
        assert "wait budget" not in str(excinfo.value), (
            "our transport budget blamed for the lookup's own timeout: "
            f"{excinfo.value}")
        assert "postgrest" in str(excinfo.value), (
            "the real cause was discarded, so the caller cannot act on it")
    finally:
        mcp_auth._current_org_id.reset(org)


def test_the_wiring_scopes_the_dispatch_and_never_forwards_the_key(monkeypatch):
    """`_wrapped_call_tool` is the ONLY thing that makes the feature work.

    Driving the two halves directly would stay green if the dispatch stopped
    calling them — every schema still advertising an argument nothing honours,
    which is the defect #9340 exists to close.

    Only the INNERMOST dispatch is faked: the real wrapper, the real seam and
    the real resolution all run, so the assertion below is about the scope the
    tool body actually executes under (#9762 moved the resolution into the
    dispatch task — see the `#9762` section at the end of this file).
    """
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    seen: dict = {}

    async def _body(name, arguments, *, version=None, run_middleware=True,
                    task_meta=None):
        seen["name"] = name
        seen["arguments"] = dict(arguments or {})
        seen["graph"] = mcp_auth._current_graph_id.get()
        return "DISPATCHED"

    monkeypatch.setattr(mcp_server, "_original_call_tool", _body)
    monkeypatch.setattr(mcp_server, "_check_mcp_op_budget",
                        _async_noop)
    org = mcp_auth._current_org_id.set(ORG_A)

    async def _call():
        return await mcp_server._wrapped_call_tool(
            "tortoise_query", {"query": "x", "graph": "g_fund_a"})

    try:
        assert asyncio.run(_call()) == "DISPATCHED"
    finally:
        mcp_auth._current_org_id.reset(org)
    assert seen["graph"] == "g_fund_a", (
        "the dispatch never scoped the tool call to the named graph")
    assert "graph" not in seen["arguments"], (
        "the handler would receive a keyword its signature does not declare")


async def _async_noop(*args, **kwargs):
    return None


# ── the record's tenant follows the SELECTED graph ─────────────────────────


def test_a_selected_custom_graph_names_the_record_tenant(monkeypatch):
    """Data in graph X must not have its record row refused or filed in Y.

    An org-wide (OAuth) credential resolves no `graph_id`, so the per-request
    record scope installs nothing; the SELECTED graph carries a control-plane
    row, so it IS nameable and must be carried.
    """
    monkeypatch.setattr(
        "tortoise.supabase_control.is_supabase_enabled", lambda: True)
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        assert mcp_auth.record_tenant_for_graph(FUND_A) == (ORG_A, "g_fund_a")
        assert mcp_auth.record_tenant_for_graph(DEFAULT) is None, (
            "DEFAULT needs no install HERE: the credential's own "
            "`default_graph_id` already names it, and #10047/#10046 closed the "
            "lanes that carried no credential — CORRECT BY CONSTRUCTION, not "
            "an open question (tortoise/mcp_auth.py::record_tenant_for_graph)")
        assert mcp_auth.record_tenant_for_graph(None) is None
    finally:
        mcp_auth._current_org_id.reset(org)


def test_the_dispatch_installs_the_record_tenant_for_a_custom_graph(monkeypatch):
    """End to end at the seam: the scope the writer reads must be installed.

    `current_record_tenant()` is what an armed write consults; if the dispatch
    scopes the data graph but not the record tenant, the row is refused
    (`no_tenant`) or filed under the environment's declared pair — a
    well-formed record row for a graph the data never touched.
    """
    from tortoise.record_writer import current_record_tenant

    monkeypatch.setattr(
        "tortoise.supabase_control.is_supabase_enabled", lambda: True)
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    org = mcp_auth._current_org_id.set(ORG_A)

    async def _dispatch(ref):
        # A ContextVar set in `asyncio.to_thread` would be lost, so the record
        # tenant must be entered on the CALLER's stack — read it here, inside
        # the task that owns the request context.
        stack = contextlib.ExitStack()
        try:
            await mcp_server._resolve_graph_argument(ref, stack)
            return current_record_tenant()
        finally:
            stack.close()

    try:
        assert asyncio.run(_dispatch("g_fund_a")) == (ORG_A, "g_fund_a")
        assert asyncio.run(_dispatch("default")) is None, (
            "DEFAULT needs no record scope here: the credential's own "
            "`default_graph_id` names it (#10047/#10046), so a second write of "
            "the same scope would only be able to disagree with it")
    finally:
        mcp_auth._current_org_id.reset(org)


def test_the_record_tenant_is_not_named_off_the_control_plane(monkeypatch):
    """Registry-mode graph ids are Falkor node ids, not `public.graphs` ids."""
    monkeypatch.setattr(
        "tortoise.supabase_control.is_supabase_enabled", lambda: False)
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        assert mcp_auth.record_tenant_for_graph(FUND_A) is None
    finally:
        mcp_auth._current_org_id.reset(org)


# ── discovery: the argument can only be used if the graphs are nameable ────


def test_list_graphs_names_the_workspaces_other_graphs(monkeypatch):
    server_graphs = [f"org_{ORG_A}", f"org_{ORG_A}_g_fund_a",
                     f"org_{ORG_A}_g_fund_b", "org_org_b_x", "org_org_b_y"]

    class _SDK:
        def list_graphs(self):
            return list(server_graphs)

    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: _SDK())
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    org = mcp_auth._current_org_id.set(ORG_A)
    mode = mcp_auth._transport_mode.set("http")
    try:
        listed = mcp_server.tortoise_list_graphs()
    finally:
        mcp_auth._transport_mode.reset(mode)
        mcp_auth._current_org_id.reset(org)
    assert f"org_{ORG_A}_g_fund_a" in listed
    assert f"org_{ORG_A}_g_fund_b" in listed
    assert f"org_{ORG_A}" in listed
    for leaked in ("org_org_b_x", "org_org_b_y"):
        assert leaked not in listed, f"cross-tenant enumeration through {leaked}"


def test_a_prefix_collision_between_workspace_ids_does_not_leak(monkeypatch):
    """A PEER whose id begins with ours — a namespace-prefix test leaks it.

    Our namespace spelling is `org_{ORG_A}` = `org_org_a`, so a prefix test
    would use `org_org_a_`, and any peer whose own id starts with `org_a_`
    produces `org_org_a_b…` — a MATCH. (The earlier fixture used `a_b`, which
    is not a prefix collision at all and so could not have failed.)
    """
    other = "org_a_b"  # its namespace is `org_org_a_b_*` → prefix `org_org_a_`
    server_graphs = [f"org_{ORG_A}", f"org_{ORG_A}_g_fund_a", f"org_{other}_x"]

    class _SDK:
        def list_graphs(self):
            return list(server_graphs)

    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: _SDK())
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows",
                        lambda org_id: [DEFAULT, FUND_A])
    org = mcp_auth._current_org_id.set(ORG_A)
    mode = mcp_auth._transport_mode.set("http")
    try:
        listed = mcp_server.tortoise_list_graphs()
    finally:
        mcp_auth._transport_mode.reset(mode)
        mcp_auth._current_org_id.reset(org)
    assert f"org_{other}_x" not in listed, (
        f"a namespace PREFIX selected another workspace's graph: {listed}")
    # Non-vacuity: the peer's namespace really does start with the prefix a
    # `startswith` implementation would use for OUR org, so that implementation
    # WOULD have leaked it. Without this the fixture could drift into a
    # collision-free spelling and the test would pass no matter what.
    own_prefix = f"{f'org_{ORG_A}'}_"
    assert f"org_{other}_x".startswith(own_prefix), (
        f"fixture is not a prefix collision: {f'org_{other}_x'!r} does not "
        f"start with {own_prefix!r}")


def test_a_degraded_graph_set_falls_back_to_the_tenant_filter(monkeypatch):
    """Discovery is best-effort; a failure must not widen the answer."""
    class _SDK:
        def list_graphs(self):
            return [f"org_{ORG_A}", "org_org_b_x"]

    def _boom(org_id):
        raise RuntimeError("control plane down")

    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: _SDK())
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", _boom)
    org = mcp_auth._current_org_id.set(ORG_A)
    mode = mcp_auth._transport_mode.set("http")
    try:
        listed = mcp_server.tortoise_list_graphs()
    finally:
        mcp_auth._transport_mode.reset(mode)
        mcp_auth._current_org_id.reset(org)
    assert listed == [f"org_{ORG_A}"]


# ── the advertised surface ─────────────────────────────────────────────────


def _schema(name):
    tools = asyncio.run(mcp_server.mcp.list_tools())
    tool = next(t for t in tools if t.name == name)
    return tool.parameters


def test_the_argument_is_advertised_on_a_graph_touching_tool():
    props = _schema("tortoise_query")["properties"]
    assert "graph" in props, "a connector cannot discover an unadvertised argument"
    assert props["graph"]["type"] == "string"
    description = props["graph"]["description"]
    assert "default graph" in description
    # The schema is transport-agnostic (one `mcp` instance serves stdio and
    # HTTP), so the hosted-only constraint must be IN the description: on
    # stdio/self-host the argument is always refused, and a description that
    # promised a default-graph semantic there would be the advertised-but-
    # refused trap this set exists to prevent.
    assert "HOSTED" in description and "stdio" in description, description
    # The discovery surface returns NAMESPACES, not ids: a description that told
    # the caller to obtain an id from it would advertise an input nothing
    # returns.
    assert "namespace" in description and "org_<workspace>_<graph_id>" in description, (
        description)


def test_the_excluded_tools_are_real_and_do_not_advertise_it():
    """Every excluded name must RESOLVE, and every advertised one must not offer it.

    A name that resolves to nothing is a dead entry that hides a live gap: the
    earlier version of this test skipped such names, which is how two retired
    names and a missing workspace-level entry both went unnoticed.
    """
    from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY

    resolvable = {t.name for t in TOOL_REGISTRY} | {t.name for t in RETIRED_TOOL_REGISTRY}
    dead = sorted(mcp_auth.GRAPH_ARGUMENT_EXCLUDED - resolvable)
    assert not dead, (
        f"GRAPH_ARGUMENT_EXCLUDED names tools that resolve to nothing: {dead}")
    advertised = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    for name in sorted(mcp_auth.GRAPH_ARGUMENT_EXCLUDED & advertised):
        assert "graph" not in _schema(name)["properties"], (
            f"{name} advertises a `graph` argument it does not honour")


def test_every_workspace_level_tool_is_excluded_by_derivation():
    """A tool that REFUSES a selected graph must not advertise one.

    `_reject_graph_bound_mcp_org_surface` is the source of truth for "this tool
    targets the WORKSPACE, not a graph", and it refuses whenever a graph is
    selected. Advertising `graph` there means a connector that selects a graph
    uniformly loses pack install / sources / onboarding — and is told its
    credential is graph-bound when it is not. Derived from the source, so a new
    caller reds here instead of shipping the trap.
    """
    import re
    from pathlib import Path

    source = Path(mcp_server.__file__).read_text()
    callers = set()
    current = None
    for line in source.splitlines():
        match = re.match(r"def (tortoise_[a-z0-9_]+)\(", line)
        if match:
            current = match.group(1)
        if "_reject_graph_bound_mcp_org_surface(" in line and current:
            callers.add(current)
    assert callers, "the derivation found no callers — it cannot fail, so it proves nothing"
    missing = sorted(callers - mcp_auth.GRAPH_ARGUMENT_EXCLUDED)
    assert not missing, (
        "these tools refuse a selected graph but advertise the `graph` "
        f"argument: {missing}")


def test_the_handler_signatures_are_unchanged():
    """The owner-reviewed `served_attributes` baseline must not move.

    The argument is advertised on the registered Tool and honoured at the
    dispatch point; adding it to the handler signature would re-stamp every
    row of `config/surface-manifest.yml` for one uniform argument.
    """
    for name in ("tortoise_query", "tortoise_create_point", "tortoise_packs_list"):
        fn = getattr(mcp_server, name)
        assert "graph" not in inspect.signature(fn).parameters, (
            f"{name}'s signature grew a `graph` parameter")


# ── the argument must be HONOURED where it is advertised ───────────────────


class _FakeProj:
    db = "DB"


class _FakeScopedSDK:
    """Stands in for `_get_org_sdk()` — the scoping half must not be re-proven."""

    def _get_proj(self):
        return _FakeProj()


@pytest.fixture
def _http_workspace(monkeypatch):
    """An HTTP dispatch in ORG_A, with the graph layer stubbed out."""
    mode = mcp_auth._transport_mode.set("http")
    org = mcp_auth._current_org_id.set(ORG_A)
    monkeypatch.setattr(mcp_server, "_get_org_sdk", lambda: _FakeScopedSDK())
    yield
    mcp_auth._transport_mode.reset(mode)
    mcp_auth._current_org_id.reset(org)


@pytest.mark.parametrize("handler,fn_name", [
    ("tortoise_entity_profile", "entityProfile"),
    ("tortoise_traverse", "tortoise_traverse"),
])
def test_the_selected_graph_reaches_the_navigation_call(
        monkeypatch, _http_workspace, handler, fn_name):
    """`db.select_graph(graph_name)` is what picks the graph, not the SDK scope.

    `tortoise_entity_profile` and `tortoise_traverse` compute a Falkor
    namespace themselves and hand it to `tortoise.navigation`; `_get_org_sdk()`
    is scoped correctly by the argument, so the ONLY thing that decides which
    graph is read is this name. Naming `org_<org_id>` here meant
    `graph="Fund A"` returned the workspace DEFAULT graph with HTTP 200 and no
    signal — the advertised-then-ignored defect #9340 exists to close.
    """
    import tortoise.navigation as navigation

    seen = {}

    def _capture(db, graph_name, *args, **kwargs):
        seen["graph_name"] = graph_name
        return {"entity": {}, "connected": {}}

    monkeypatch.setattr(navigation, fn_name, _capture)
    ns = mcp_auth._current_graph_namespace.set(FUND_A["namespace"])
    try:
        getattr(mcp_server, handler)("e1")
    finally:
        mcp_auth._current_graph_namespace.reset(ns)

    assert seen.get("graph_name") == FUND_A["namespace"], (
        f"{handler} read {seen.get('graph_name')!r} — the workspace default — "
        f"while the call selected {FUND_A['namespace']!r}")


def test_a_selected_default_graph_still_reads_the_workspace_namespace(
        monkeypatch, _http_workspace):
    """No selection ⇒ the #236 default is unchanged, byte for byte."""
    import tortoise.navigation as navigation

    seen = {}

    def _capture(db, graph_name, *args, **kwargs):
        seen["graph_name"] = graph_name
        return {"entity": {}, "connected": {}}

    monkeypatch.setattr(navigation, "entityProfile", _capture)
    mcp_server.tortoise_entity_profile("e1")
    assert seen["graph_name"] == f"org_{ORG_A}"


def test_the_caller_cannot_name_the_namespace_itself(monkeypatch, _http_workspace):
    """#236 stays intact: on HTTP the argument is the ONLY way to move the graph.

    The selected namespace comes from the resolver's verified `public.graphs`
    row. The handler's own `graph_name` parameter is caller input and must stay
    ignored on HTTP, or the cross-tenant injection guard is reopened.
    """
    import tortoise.navigation as navigation

    seen = {}

    def _capture(db, graph_name, *args, **kwargs):
        seen["graph_name"] = graph_name
        return {"entity": {}, "connected": {}}

    monkeypatch.setattr(navigation, "entityProfile", _capture)
    mcp_server.tortoise_entity_profile("e1", graph_name="org_org_b")
    assert seen["graph_name"] == f"org_{ORG_A}", (
        "a caller-supplied graph_name selected the graph on the HTTP lane")


def test_analyze_derives_its_anchors_from_the_selected_graph(
        monkeypatch, _http_workspace):
    """The anchor set and the analysis must read the SAME graph.

    `tortoise_analyze` derives `entity_subgraph_ids` from a profile and then
    runs `analyze()` on the selected projection. Deriving the anchors from the
    workspace default while analysing the selected graph means an entity that
    exists only in the selected graph yields an empty anchor set, and the
    surrounding `except Exception: pass` silently widens the call to a
    full-graph analysis — a different question answered than the one asked.
    """
    import tortoise.analyze as analyze_mod
    import tortoise.navigation as navigation

    seen = {}

    def _capture(db, graph_name, entity_id, hops=2):
        seen["graph_name"] = graph_name
        return {"entity": {"id": entity_id}, "connected": {"points": [{"id": "p1"}]}}

    monkeypatch.setattr(navigation, "entityProfile", _capture)
    monkeypatch.setattr(analyze_mod, "analyze",
                        lambda *a, **kw: {"answer": "ok", "raw": []})
    monkeypatch.setattr(mcp_server, "_analyze_llm_budget_available", lambda: False)

    ns = mcp_auth._current_graph_namespace.set(FUND_A["namespace"])
    try:
        mcp_server.tortoise_analyze("where is the disagreement?", entityId="e1")
    finally:
        mcp_auth._current_graph_namespace.reset(ns)

    assert seen.get("graph_name") == FUND_A["namespace"], (
        f"the anchors came from {seen.get('graph_name')!r} while the analysis "
        f"ran on {FUND_A['namespace']!r}")


def test_no_advertised_handler_names_the_workspace_default_itself():
    """The complement of the workspace-level derivation: HONOURED, not ignored.

    Derived from handler SOURCE, because the earlier derivation only looked for
    `_reject_graph_bound_mcp_org_surface` callers and so could not see this
    class — a handler that computes `org_<org_id>` itself and hands it to
    `db.select_graph()`, reading the default graph while the call is scoped to
    another. The one sanctioned site is `_dispatch_graph_name()`; anything else
    must be excluded from the argument.
    """
    import re

    advertised = {t.name for t in asyncio.run(mcp_server.mcp.list_tools())}
    helper_source = inspect.getsource(mcp_server._dispatch_graph_name)
    assert re.search(r'f"org_\{', helper_source), (
        "the detector no longer matches the sanctioned site — it cannot fail, "
        "so it proves nothing")

    offenders = []
    routed = set()
    for name in sorted(advertised):
        fn = getattr(mcp_server, name, None)
        if fn is None or not inspect.isfunction(fn):
            continue
        source = inspect.getsource(fn)
        if "_dispatch_graph_name(" in source:
            routed.add(name)
        if name in mcp_auth.GRAPH_ARGUMENT_EXCLUDED:
            continue
        if re.search(r'f"org_\{', source) and "_dispatch_graph_name(" not in source:
            offenders.append(name)

    assert not offenders, (
        "these tools advertise `graph` and then name the workspace default "
        f"graph themselves, so the selection is silently ignored: {offenders}")
    assert routed >= {"tortoise_entity_profile", "tortoise_traverse",
                      "tortoise_analyze"}, (
        "a graph-naming handler stopped reading the dispatch's selection: "
        f"{sorted(routed)}")


# ── #9762: the resolution rides the DISPATCH TASK, not the wrapper ─────────
#
# `_wrapped_call_tool` used to resolve the `graph` argument BEFORE it handed the
# dispatch to `_await_under_mcp_wait_bound`. That seam spends the transport's
# REMAINING deadline (#3834 F1), so when the transport had already refused the
# request (`_wait_bound_refused()`, remaining == 0 by construction) the
# resolution ran FIRST: its `asyncio.timeout(0)` expired and a request the
# transport had already answered raised `ToolError("… graph lookup exceeded the
# wait budget …")`. Three things were wrong with that — the caller's transport
# refusal was replaced by a graph error, the `mcp_tool_call` row was
# `exec_error`/`ToolError` where the truthful row is `refused`/
# `transport_wait_bound` (the very series #3834 F-1/F-2 keep honest), and a
# control-plane `list_memory_graphs` read was issued for an answered request.
#
# The obvious guard (`if not _wait_bound_refused(): resolve`) is WRONG, and the
# tests below pin its real defects: the scope it applies to the CALLER's context
# (ContextVars with no reset token) outlives the dispatch, and it still schedules
# a side-effecting dispatch whose result nothing will read. It would also reopen
# the unscoped-result hazard in principle — `await_under_wait_bound` returns
# `task.result()` when the task finished as the deadline expired, and a ZERO
# timeout does not preclude that (`wait_for(shield(task), 0)` still lets an
# already-scheduled task run during its cancel wait). That hazard belongs to the
# naive-guard variant; on this code it is unreachable only because a refused
# request never creates a task at all — the entry short-circuit — whose removal
# costs a control-plane read and a side-effecting dispatch, not a wrong graph.
# The fix moves the resolution INSIDE the dispatch task, so the scope moves with
# the task and cannot outlive it.


def _spent_budget_state(*, refused: bool) -> dict:
    """The middleware's own ``scope["state"]``: arrival 60 s ago, budget spent.

    ``_wait_bound_refused`` is set by ``hosted_api.WaitBoundMiddleware`` only on
    the pre-SSE-refusal path, where the pre-SSE cost alone has already spent the
    bound — so "refused" and "``remaining == 0``" are the same state, and both
    are produced here through the REAL readers (``_wait_bound_arrival`` /
    ``_wait_bound_refused``), not by patching them out.
    """
    state = {"_wait_bound_t0": _time.monotonic() - 60.0}
    if refused:
        state["_wait_bound_refused"] = True
    return state


def _record_telemetry(monkeypatch) -> list:
    rows: list = []
    monkeypatch.setattr(
        mcp_server, "_emit_mcp_tool_call_telemetry",
        lambda org, name, status, latency, error_kind=None, count=None:
        rows.append((status, error_kind)))
    return rows


def _wrapped_call(monkeypatch, name, arguments):
    monkeypatch.setattr(mcp_server, "_check_mcp_op_budget", _async_noop)
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        return asyncio.run(mcp_server._wrapped_call_tool(name, arguments))
    finally:
        mcp_auth._current_org_id.reset(org)


def test_a_transport_refused_graph_call_is_not_a_graph_error(monkeypatch):
    """#9762 acceptance 1: an already-refused request keeps its own refusal.

    The transport answered the caller and emitted the breach event; this seam's
    job is only to NOT contradict it. Before the fix this raised a graph
    `ToolError` (recorded `exec_error`/`ToolError`), emitted nothing itself but
    issued the control-plane read for a request nobody was waiting on.
    """
    monkeypatch.setattr(mcp_server, "_wait_bound_state",
                        lambda: _spent_budget_state(refused=True))
    reads: list = []

    def _reachable(org_id):
        reads.append(org_id)
        return OWN_ROWS

    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", _reachable)
    breaches: list = []
    monkeypatch.setattr(mcp_server, "_emit_mcp_wait_bound_breach_off_loop",
                        lambda *a, **k: breaches.append(a))
    rows = _record_telemetry(monkeypatch)
    dispatched: list = []

    async def _body(name, arguments, *, version=None, run_middleware=True,
                    task_meta=None):
        dispatched.append(name)
        return "DISPATCHED"

    # Pinned, not incidental: the dispatch must be UNREACHABLE on this path, so
    # a refused request cannot perform a real tool call (a WRITE tool would
    # mutate state for a caller that was told the request had been refused).
    # Without this the assertion below would also be racing the abandoned
    # task's executor thread.
    monkeypatch.setattr(mcp_server, "_original_call_tool", _body)
    # Spied at CREATION time, not at the body: a task that is scheduled and then
    # abandoned before its first step never reaches `_body`, so the body alone
    # would report "nothing happened" for exactly the regression this guards.
    real_scoped = mcp_server._scoped_dispatch
    scheduled: list = []

    def _spy_scoped(name, arguments, graph_ref, *, version, task_meta):
        scheduled.append((name, graph_ref))
        return real_scoped(name, arguments, graph_ref, version=version,
                           task_meta=task_meta)

    monkeypatch.setattr(mcp_server, "_scoped_dispatch", _spy_scoped)

    result = _wrapped_call(monkeypatch, "tortoise_query",
                           {"query": "x", "graph": "g_fund_a"})

    assert getattr(result, "meta", None) and \
        result.meta.get(mcp_server._WAIT_BOUND_META_KEY) is True, (
            "the transport's own refusal was not the result the caller got")
    assert result.is_error is True
    assert rows == [("refused", "transport_wait_bound")], (
        "a graph-bearing call whose transport already refused was recorded as "
        f"{rows} — a false row in the mcp_tool_call series #3834 F-2 keeps "
        "truthful")
    assert breaches == [], (
        f"a second breach event was emitted for one request: {breaches}")
    assert scheduled == [], (
        "a scoped dispatch was CREATED for a request the transport had already "
        "answered — a WRITE tool would have mutated state for a refused call")
    assert dispatched == [], (
        "the dispatch body ran for a request the transport had already answered")
    assert reads == [], (
        "a control-plane `list_memory_graphs` read was issued for a request the "
        "transport had already answered")


def test_a_dispatch_that_completes_as_the_deadline_expires_stays_scoped(
        monkeypatch):
    """#9762 acceptance 2: a COMPLETING dispatch returns only the named graph.

    ``_await_under_mcp_wait_bound`` surfaces ``task.result()`` when the dispatch
    finished as the deadline expired, so the resolution must already have been
    applied to the task that produced that result. A resolution DROPPED for a
    spent budget (or skipped because the transport refused) would hand back a
    result produced by the workspace DEFAULT graph to a caller who named another
    one — trading a telemetry fix for the silent-wrong-graph class #9340 closes.
    Moving the resolution into the task is what makes that unrepresentable; this
    pin holds the property from the outside (it is the shape a naive guard
    breaks).

    ``await_under_wait_bound`` is faked because the branch under test is *the
    dispatch FINISHING at the deadline*; a real clock cannot be made to land
    there deterministically.
    """
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    # A POSITIVE residual: the deadline is about to expire, it has not already
    # been spent (a spent one refuses the lookup by design — the same bound).
    # 2 s, not a hair: the residual carries NO assertion weight (the faked waiter
    # ignores it) — the only requirement is `remaining > 0` — so give the real
    # `asyncio.to_thread` round trip a wide margin instead of turning a
    # congested default executor into a red test about an unrelated property.
    spent = mcp_auth._TRANSPORT_WAIT_BOUND_S - 2.0
    monkeypatch.setattr(
        mcp_server, "_wait_bound_state",
        lambda: {"_wait_bound_t0": _time.monotonic() - spent})

    async def _finish_then_expire(task, timeout, *, reference, owner=None):
        await task           # the dispatch completes as the deadline expires
        raise TimeoutError   # ... and the wait reports the breach

    monkeypatch.setattr(mcp_auth, "await_under_wait_bound", _finish_then_expire)
    seen: dict = {}

    async def _body(name, arguments, *, version=None, run_middleware=True,
                    task_meta=None):
        seen["graph"] = mcp_auth._current_graph_id.get()
        return "RESULT-FROM-THE-NAMED-GRAPH"

    monkeypatch.setattr(mcp_server, "_original_call_tool", _body)

    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        result = asyncio.run(mcp_server._await_under_mcp_wait_bound(
            "tortoise_query", {}, version=None, task_meta=None,
            graph_ref="g_fund_a"))
    finally:
        mcp_auth._current_org_id.reset(org)

    assert result == "RESULT-FROM-THE-NAMED-GRAPH"
    assert seen.get("graph") == "g_fund_a", (
        "the dispatch ran under the workspace DEFAULT graph and its result was "
        "returned to a caller who named another graph — the resolution was "
        "dropped rather than moved into the task")


@pytest.mark.asyncio
async def test_the_resolution_runs_in_the_dispatch_task(monkeypatch):
    """#9762: the resolution rides the TASK — that ordering IS the fix.

    Both halves of the bug come from the same place: a resolution executed in
    the CALLER's task runs before the seam spends the transport's remaining
    deadline (so it raises on an already-refused request), and it applies a
    scope that then outlives the dispatch. This asserts the structural property
    directly — the resolution executes in a DIFFERENT task from the caller's, and
    it is the same task the tool body runs in — so any wrapper-side resolution
    (guarded or not) fails here.
    """
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    monkeypatch.setattr(mcp_server, "_check_mcp_op_budget", _async_noop)
    seen: dict = {}
    real_resolve = mcp_server._resolve_graph_argument

    async def _spy_resolve(ref, stack):
        seen["resolve_task"] = asyncio.current_task()
        return await real_resolve(ref, stack)

    async def _body(name, arguments, *, version=None, run_middleware=True,
                    task_meta=None):
        seen["dispatch_task"] = asyncio.current_task()
        seen["graph"] = mcp_auth._current_graph_id.get()
        return "DISPATCHED"

    monkeypatch.setattr(mcp_server, "_resolve_graph_argument", _spy_resolve)
    monkeypatch.setattr(mcp_server, "_original_call_tool", _body)
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        caller_task = asyncio.current_task()
        await mcp_server._wrapped_call_tool(
            "tortoise_query", {"query": "x", "graph": "g_fund_a"})
    finally:
        mcp_auth._current_org_id.reset(org)

    assert "resolve_task" in seen, "the graph argument was never resolved"
    assert seen["resolve_task"] is seen["dispatch_task"], (
        "the resolution ran in a different task from the dispatch it scopes")
    assert seen["resolve_task"] is not caller_task, (
        "the graph was resolved in the CALLER's task — before the seam spends "
        "the transport's remaining deadline, which is the #9762 ordering bug")
    assert seen["graph"] == "g_fund_a"


def test_the_wrapper_hands_the_ref_to_the_seam_instead_of_resolving_it(
        monkeypatch):
    """#9762: the ORDERING is the contract — nothing resolves before the task.

    A resolution in the wrapper is what produced the bug: it runs before the
    seam spends the transport's remaining deadline, so on an already-refused
    request it expires and raises. A scope applied in the caller's context also
    outlives the dispatch, so a later call in the same context that names no
    graph would inherit it.
    """
    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    seen: dict = {}

    async def _fake_seam(name, arguments, *, version=None, task_meta=None,
                         graph_ref=None):
        seen["graph_ref"] = graph_ref
        seen["arguments"] = dict(arguments or {})
        seen["graph_at_seam_entry"] = mcp_auth._current_graph_id.get()
        return "DISPATCHED"

    monkeypatch.setattr(mcp_server, "_await_under_mcp_wait_bound", _fake_seam)

    assert _wrapped_call(monkeypatch, "tortoise_query",
                         {"query": "x", "graph": "g_fund_a"}) == "DISPATCHED"
    assert seen["graph_ref"] == "g_fund_a", (
        "the wrapper did not hand the `graph` argument to the dispatch seam as "
        "`graph_ref` — the seam cannot resolve it inside the task")
    assert "graph" not in seen["arguments"], (
        "the handler would receive a keyword its signature does not declare")
    assert seen["graph_at_seam_entry"] is None, (
        "the graph was already resolved when the seam was entered — the "
        "#9762 ordering bug: on an already-refused request that resolution "
        "runs under a spent deadline and raises a graph error")


@pytest.mark.asyncio
async def test_the_graph_scope_cannot_outlive_the_dispatch(monkeypatch):
    """#9762: the scope is the DISPATCH's, not the caller's context's.

    ``use_graph_target`` sets ``_current_graph_id`` / ``_current_graph_namespace``
    with no reset token, so a resolution held in the caller's context persists
    after the call: the next call in that context which names no graph — the
    documented "untouched default path" — would read the PREVIOUS call's graph.
    Executing the resolution inside the dispatch task confines it by
    construction.
    """
    from tortoise.record_writer import current_record_tenant

    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", lambda org_id: OWN_ROWS)
    monkeypatch.setattr(
        "tortoise.supabase_control.is_supabase_enabled", lambda: True)
    monkeypatch.setattr(mcp_server, "_check_mcp_op_budget", _async_noop)
    seen: dict = {}

    async def _body(name, arguments, *, version=None, run_middleware=True,
                    task_meta=None):
        # The record tenant is a ContextVar token too, so the same question
        # applies to it: what the WRITER sees inside the dispatch, and what the
        # caller's context holds afterwards.
        seen["record_tenant"] = current_record_tenant()
        return "DISPATCHED"

    monkeypatch.setattr(mcp_server, "_original_call_tool", _body)
    org = mcp_auth._current_org_id.set(ORG_A)
    try:
        await mcp_server._wrapped_call_tool(
            "tortoise_query", {"query": "x", "graph": "g_fund_a"})
        assert seen.get("record_tenant") == (ORG_A, "g_fund_a"), (
            "the dispatch did not install the record tenant of the graph it "
            f"scopes: {seen.get('record_tenant')}")
        assert mcp_auth._current_graph_id.get() is None, (
            "the selected graph survived the dispatch in the CALLER's context — "
            "the next call in this context that names no graph reads it")
        assert mcp_auth._current_graph_namespace.get() is None, (
            "the selected namespace survived the dispatch")
        assert current_record_tenant() is None, (
            "the record tenant survived the dispatch in the CALLER's context")
    finally:
        mcp_auth._current_org_id.reset(org)


def test_a_slow_graph_lookup_refuses_as_a_graph_error_every_time(monkeypatch):
    """#9762: the lookup's OWN refusal must win the race with the seam's.

    Both deadlines are the same absolute instant (the transport's remaining
    budget), and the seam's timer is armed FIRST — before the dispatch task's
    first step — so with equal deadlines the seam wins, abandons the lookup and
    returns the GENERIC wait-bound refusal. Measured on the equal-deadline code
    with a lookup longer than the whole residual: 3 of 4 runs recorded
    ``timeout/wait_bound`` and 1 the graph refusal, i.e. one failure class with
    two different rows in the series this issue is about, and the actionable
    "the graph lookup timed out" message usually lost.

    Repeated deliberately: a single run would pass a third of the time on that
    code.
    """
    # A short residual, so a lookup longer than it is cheap to stage.
    spent = mcp_auth._TRANSPORT_WAIT_BOUND_S - 0.15
    monkeypatch.setattr(
        mcp_server, "_wait_bound_state",
        lambda: {"_wait_bound_t0": _time.monotonic() - spent})

    def _slow(org_id):
        _time.sleep(0.3)      # longer than the whole residual
        return OWN_ROWS

    monkeypatch.setattr(mcp_auth, "reachable_graph_rows", _slow)
    dispatched: list = []

    async def _body(name, arguments, *, version=None, run_middleware=True,
                    task_meta=None):
        dispatched.append(name)
        return "DISPATCHED"

    monkeypatch.setattr(mcp_server, "_original_call_tool", _body)
    rows = _record_telemetry(monkeypatch)

    for attempt in range(3):
        with pytest.raises(ToolError) as excinfo:
            _wrapped_call(monkeypatch, "tortoise_query",
                          {"query": "x", "graph": "g_fund_a"})
        message = str(excinfo.value)
        assert "graph lookup" in message and "Retry after" in message, (
            f"attempt {attempt}: the lookup's own refusal lost to the generic "
            f"wait-bound one, so the caller cannot tell what timed out: "
            f"{message}")
        assert mcp_auth._TRANSPORT_WAIT_BOUND_MESSAGE not in message, (
            "the margin cut this lookup while the request still had time on the "
            "clock, so the shared budget-breach wording ('…was exceeded… the "
            f"work may still complete on the server') is false here: {message}")
        assert "not executed" in message, (
            "the band's refusal must say that nothing ran — a caller told only "
            "'wait and retry' about a call that may have written would re-send "
            f"it: {message}")
    assert rows == [("exec_error", "ToolError")] * 3, (
        f"one failure class recorded more than one row: {rows}")
    assert dispatched == [], (
        "a dispatch ran even though the budget was spent by the lookup")


def test_a_refusal_that_lands_MID_SEAM_does_not_emit_a_second_breach(monkeypatch):
    """#3834 F-2 stays closed for the refusal that arrives WHILE we wait.

    The entry short-circuit only sees a refusal that already happened. A
    transport that does not start its response before dispatching (fastmcp's
    ``json_response`` mode) can reach ``WaitBoundMiddleware``'s refusal branch
    mid-wait, and then the middleware's event is again the truthful one — the
    seam's would be the duplicate F-2 exists to prevent. The flag is therefore
    read a second time at the emit site, not only at entry.
    """
    state = {"_wait_bound_t0": _time.monotonic() - 1.0}
    monkeypatch.setattr(mcp_server, "_wait_bound_state", lambda: state)

    async def _refused_during_the_wait(task, timeout, *, reference, owner=None):
        # Exactly what the middleware does to the shared `scope["state"]` dict.
        state["_wait_bound_refused"] = True
        raise TimeoutError

    monkeypatch.setattr(mcp_auth, "await_under_wait_bound",
                        _refused_during_the_wait)
    breaches: list = []
    monkeypatch.setattr(mcp_server, "_emit_mcp_wait_bound_breach_off_loop",
                        lambda *a, **k: breaches.append(a))
    rows = _record_telemetry(monkeypatch)

    async def _body(name, arguments, *, version=None, run_middleware=True,
                    task_meta=None):
        return "DISPATCHED"

    monkeypatch.setattr(mcp_server, "_original_call_tool", _body)

    result = _wrapped_call(monkeypatch, "tortoise_query", {"query": "x"})

    assert getattr(result, "meta", None) and \
        result.meta.get(mcp_server._WAIT_BOUND_META_KEY) is True
    assert breaches == [], (
        "the seam emitted a second breach event for a request the middleware "
        f"had already refused mid-wait: {breaches}")
    assert rows == [("refused", "transport_wait_bound")], (
        f"one refusal recorded as {rows} instead of one truthful row")


def test_an_excluded_tool_is_not_a_graph_error_when_the_transport_refused(
        monkeypatch):
    """#9762: the VALIDATION half must not beat the transport's refusal either.

    ``_take_graph_argument`` refuses a `graph` on a tool that does not target
    graphs, and that refusal is raised in the wrapper — ahead of the seam's
    short-circuit. On an already-refused request it therefore recorded the SAME
    false ``exec_error``/``ToolError`` row as the resolution half, for a call the
    transport had already answered. Both halves return the refusal now; the
    refusal itself is unchanged when the transport has NOT refused.
    """
    name = "tortoise_graph_set_recording"
    assert name in mcp_auth.GRAPH_ARGUMENT_EXCLUDED, name
    monkeypatch.setattr(mcp_server, "_wait_bound_state",
                        lambda: _spent_budget_state(refused=True))
    rows = _record_telemetry(monkeypatch)

    result = _wrapped_call(monkeypatch, name, {"graph": "g_fund_a"})

    assert getattr(result, "meta", None) and \
        result.meta.get(mcp_server._WAIT_BOUND_META_KEY) is True, (
            "an excluded-tool argument refusal replaced the transport's own "
            "refusal for a request it had already answered")
    assert rows == [("refused", "transport_wait_bound")], (
        f"the argument refusal was recorded as {rows} — a false row in the "
        "series #3834 F-2 keeps truthful")

    # ...and NOT refused: the argument refusal still surfaces, classified.
    monkeypatch.setattr(mcp_server, "_wait_bound_state", lambda: None)
    refused_rows = _record_telemetry(monkeypatch)
    with pytest.raises(ToolError) as excinfo:
        _wrapped_call(monkeypatch, name, {"graph": "g_fund_a"})
    assert "does not take a `graph` argument" in str(excinfo.value)
    assert refused_rows == [("exec_error", "ToolError")], refused_rows
