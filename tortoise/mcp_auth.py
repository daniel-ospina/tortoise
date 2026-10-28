"""MCP HTTP transport auth, rate limit, security headers, SDK resolution (#236).

Serves as the auth/rate-limit boundary for the MCP Streamable HTTP endpoint
mounted at /mcp on the hosted FastAPI app. Imports ONLY starlette plus
``tortoise`` modules that carry no hosted app (``sdk``, ``body_limits``,
``rate_buckets``, ``tool_registry``) — mcp_server imports from here
(one-directional; no circular import). ⛔ Never import ``hosted_api`` here: it
is a cycle, and its *presence* in ``sys.modules`` is the runtime deployment
discriminator the self-host branches read (see ``_journal_path_for``).

Design: per-request org-scoped SDK via ContextVar. OrgResolutionMiddleware
validates the Bearer tt_/tk_ token (API_KEY_PREFIXES) against the control
plane — Supabase
(lookup_hash, #767 plan Task 3) when SUPABASE_URL + service key are set,
otherwise the FalkorDB registry (apikey_verify) — and sets
_current_org_id / _transport_mode. Tools resolve the request-scoped SDK via
_get_org_sdk(). Fail-closed: if _transport_mode is None (unset/misconfigured),
_safe() rejects ALL operations — it never depends on is_dev_mode(), which
returns True in hosted production (TORTOISE_API_KEY unset).
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import ipaddress
import os
import re
import time
from collections import OrderedDict
from contextvars import ContextVar
from typing import Any

from fastmcp.exceptions import AuthorizationError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from tortoise.body_limits import BodyTooLargeError, read_capped_body
from tortoise.rate_buckets import (
    _BUCKET_OVERFLOW_KEY,
    _bucket_route,
    _bucket_touch,
)
from tortoise.sdk import TortoiseSDK

# ── ContextVars ─────────────────────────────────────────────────────────────
# Reserved placeholder org id for selfhost transports (auth_mode "static"/"none",
# #338): no tenant resolution happens, and the graph namespace is isolated under
# org_selfhost. Quota is N/A for this placeholder — selfhost has no billing.
SELFHOST_ORG_ID = "selfhost"
_current_org_id: ContextVar[str | None] = ContextVar("_current_org_id", default=None)
# #329: resolved org quota limits (from the registry Org node), cached 60s
# with the auth cache so MCP write tools enforce the SAME limits REST sees.
_current_org_limits: ContextVar[dict | None] = ContextVar("_current_org_limits", default=None)
# C5 #2114 (D-C5-4): graph scope rides the ContextVars — the resolved org's
# C1 tenancy fields (graph_id / FULL graph namespace / flat scopes /
# legacy_full_access), set by OrgResolutionMiddleware at resolution time so
# _get_org_sdk() + the tool-call scope gate see the SAME scope REST sees.
_current_graph_id: ContextVar[str | None] = ContextVar("_current_graph_id", default=None)
_current_graph_namespace: ContextVar[str | None] = ContextVar(
    "_current_graph_namespace", default=None)
_current_scopes: ContextVar[list | None] = ContextVar("_current_scopes", default=None)
_current_legacy_full_access: ContextVar[bool | None] = ContextVar(
    "_current_legacy_full_access", default=None)
_transport_mode: ContextVar[str | None] = ContextVar("_transport_mode", default=None)
# Curation group for the active MCP app (#523) — set per request by the app's
# middleware so the shared tools/list transform filters correctly even when
# multiple apps exist in one process.
_tool_group: ContextVar[str | None] = ContextVar("_tool_group", default=None)

# mcp_server.py owns lazy SDK init (URI resolution, 3x retry, test-swap
# pattern). mcp_auth delegates via a function-level import to avoid the
# circular import (mcp_server imports mcp_auth at module level).


def _record_tenant_scope(org: dict, *, control_plane_ids: bool):
    """The record's tenant for ONE request: a `with` scope, or a no-op.

    #9192. The record's write seam (:mod:`tortoise.record_writer`) refuses an
    armed write that cannot name its tenant (`no_tenant`) instead of filing it
    under a process-global guess. This is the per-request carrier that names it
    — installed by :class:`OrgResolutionMiddleware` around the whole request,
    from the `(org_id, graph_id)` the resolution just produced, so no tool and
    no write door has to remember it.

    A credential that resolves NO named graph (session / legacy / OAuth: an
    org-wide key opens the org's default graph) is named by
    :func:`tortoise.record_writer.record_tenant_for` from the credential's
    ``default_graph_id`` — the id of the org's active ``kind='default'``
    ``public.graphs`` row, resolved by the credential's own resolver through
    ``supabase_control.default_graph_id_for_credential`` (``resolve_api_key``
    for a ``tt_``/``tk_`` key since #10025; ``hosted_api._session_user_org`` and
    :func:`tortoise.oauth.resolve_oauth_access_token` since #10046; and
    `20261028000001` mints that row per organization). When the control plane
    cannot name it — no row, no field — the write is REFUSED and counted
    (``no_tenant``) instead. Nothing is guessed here: a guess is the
    cross-tenant write this whole seam exists to prevent.

    ⚠️ ``control_plane_ids`` IS NOT DECORATION — it is the id-space precondition,
    and the caller passes whether the resolution came from the control plane
    (`is_supabase_enabled()`, the same predicate that chose the resolver). The
    record's layer tables FK `(org_id, graph_id) -> public.graphs(org_id, id)`, so
    ONLY a control-plane resolution carries a graph id the record can key on.
    The SELF-HOST registry lane's `graph_id` is a Falkor ``:Graph`` node id
    (``g_<hex>``) that is never inserted into `public.graphs`, so installing it
    would replace a correctly declared single-tenant pair with a value the FK
    cannot satisfy — that lane installs nothing and keeps arming by declaration
    (or refuses), its pre-#9192 behaviour exactly.

    WHICH ``(org, graph)`` COUNTS is not decided here: it is
    :func:`tortoise.record_writer.record_tenant_for`, the ONE declaration this
    lane and the hosted REST lane both call, so the two lanes cannot drift into
    filing one request's rows under different tenants. This function only wraps
    that answer in the ambient carrier this lane can use.
    """
    from tortoise.record_writer import record_tenant_for, use_record_tenant

    tenant = record_tenant_for(org, control_plane_ids=control_plane_ids)
    if tenant is None:
        return contextlib.nullcontext()
    return use_record_tenant(*tenant)


def _get_base_sdk() -> TortoiseSDK:
    """Lazy module-level SDK for stdio mode. Never touched in HTTP mode.

    Delegates to tortoise.mcp_server._get_sdk() (the #451 canonical lazy
    init) so there is exactly ONE source of truth for DB resolution. The
    function-level import avoids the mcp_server ↔ mcp_auth cycle.
    """
    from tortoise import mcp_server as _ms
    return _ms._get_sdk()


# ── Org-scoped SDK (D2) ────────────────────────────────────────────────────
def _journal_path_for(namespace: str | None,
                      graph_name: str | None) -> str | None:
    """The per-graph JSONL journal path for an MCP-lane SDK (`#7951` P0).

    Delegates to ``config._resolve_event_log_path`` — the ONE derivation
    of namespace/graph_name → ``<base>/<graph>/events.jsonl`` (#4240) — so an
    MCP-lane SDK can never key its journal to a graph the SDK does not open.
    Inventing a second derivation here is exactly the #4240 review's
    divergence class (``namespace='test-foo'`` opens
    ``test_foo_tortoise``, NOT ``org_test-foo``).

    Returns ``None`` when ``TORTOISE_EVENT_LOG_BASE_DIR`` is unset (the
    journal-less lanes stay byte-identical) AND for the unnamed default /
    registry graphs, which are not org data graphs — the same boundary
    ``hosted_api._make_sdk`` draws. A ``None`` here means the SDK is NOT
    journal-backed, so `#7951`'s guard has nothing to enforce (there is no
    truth store for its cache to be ahead of) — not that the gate was
    forgotten.

    ⛔ The derivation lives in ``tortoise.config`` (no FastAPI import), NOT in
    ``hosted_api``. Importing ``hosted_api`` here put the whole hosted app in
    ``sys.modules`` on EVERY stdio/self-host MCP process — and that module's
    *presence* is the runtime deployment discriminator the self-host branches
    read (``mcp_server.tortoise_pack_install``'s HOSTED-only stub,
    ``operator_alert.alert_store``'s light leg, the fast-path invariant at the
    top of ``mcp_server``). The import also cost ~1 s on a lane whose result is
    usually ``None``. Re-exported from ``hosted_api`` for its own callers, so
    there is still exactly ONE derivation (#7951 review P1).
    """
    from tortoise.config import _resolve_event_log_path
    return _resolve_event_log_path(namespace=namespace, graph_name=graph_name)


def _get_org_sdk() -> TortoiseSDK:
    """Request-scoped SDK: org namespace in HTTP mode, base SDK in stdio.

    C5 #2114 (D-C5-4): a graph-bound key (graph_id + namespace ContextVars
    set at resolution) opens ITS OWN graph via the SDK graph-name seam
    (TortoiseSDK(graph_name=ns) — the namespace derivation would prepend
    org_). The middleware resolution already proved key→graph ownership
    (the same trusted resolver REST uses: graph_id/scopes only resolve from
    the owned Graph node / graphs row) and graph-delete revokes the graph's
    keys (C3) — a deleted graph's key fails resolution (401), so no
    per-call re-probe (REST's _data_sdk probe is defense-in-depth for the
    resolve→open window; MCP's resolve→open window is the same request).
    Org-wide keys / OAuth / selfhost → the default graph (unchanged).

    #7951 P0: every SDK built here is handed its per-graph JSONL journal
    (``_journal_path_for``), so ``TortoiseSDK._get_proj`` binds it and §17.1's
    single-writer guard is ARMED on this lane. Before this, no MCP-lane SDK
    carried an ``event_log_path`` and the guard was inert on the surface
    agents actually write through.

    ⛔ BOUNDARY, corrected so it is not over-read: the guard is armed on the
    ORG-SCOPED lane — including the SELF-HOST lane (``SELFHOST_ORG_ID`` →
    ``namespace='selfhost'`` → ``<base>/org_selfhost/events.jsonl`` when
    ``TORTOISE_EVENT_LOG_BASE_DIR`` is set). It is the BASE/stdio lane
    (``_get_base_sdk``, no org) that is journal-less and therefore unarmed;
    that lane's journal resolves to ``None`` by construction and is not this
    function's concern. So the earlier claim that the self-host lane "could
    not be armed" was wrong: it is armed whenever the journal base dir is
    set."""
    org_id = _current_org_id.get()
    if org_id is None:
        return _get_base_sdk()
    if org_id == SELFHOST_ORG_ID:
        # #8497: arm the self-host deployment's default journal BEFORE the path
        # is resolved. `tortoise.selfhost` arms it for the docker daemon, but
        # `tortoise serve --http` is a documented self-host daemon that never
        # imports that module — so without this the self-host MCP door resolved
        # a `None` journal and wrote an UNRECORDED org_selfhost graph.
        # Memoized in `config` — the probe runs once per base dir per process,
        # so this is a lookup after the first call.
        from tortoise.config import ensure_selfhost_event_log

        ensure_selfhost_event_log()
    gid = _current_graph_id.get()
    ns = _current_graph_namespace.get()
    if gid and not ns:
        # A graph-BOUND credential whose storage namespace did not resolve must
        # not be served the workspace DEFAULT graph. Both producers document
        # this half-resolved state as fail-closed: `supabase_control.
        # _graph_namespace_for` returns None on a missing `graphs` row OR on any
        # read exception, and `sdk.apikey_verify` returns None on a missing
        # `:Graph` node. The REST lane refuses the identical state (403
        # GRAPH_NOT_FOUND), so falling through to line below would open the
        # default graph with a graph-scoped key — silently, on the lane where
        # every other cross-graph move is an explicit refusal.
        raise AuthorizationError(
            "This credential is bound to a graph whose storage namespace could "
            "not be resolved. Retry the call; if it persists, mint a key for "
            "the graph again.")
    if gid and ns:
        # #7951 P0: pass the journal so `_get_proj`'s `_bind_journal` ARMS
        # §17.1's guard on THIS lane. Without it the MCP surface built a
        # journal-backed-intent SDK with no `event_log_path`, so the guard was
        # inert exactly where agents write (the reviewer's reproduction).
        return TortoiseSDK(
            graph_name=ns,
            event_log_path=_journal_path_for(namespace=None, graph_name=ns))
    sdk = TortoiseSDK(
        namespace=org_id,
        event_log_path=_journal_path_for(namespace=org_id, graph_name=None))
    if org_id == SELFHOST_ORG_ID:
        # #2007 (W12): the selfhost SDK-init seam — the selfhost MCP transport
        # (`tortoise/selfhost.py`'s mounted /mcp, and `serve --http --auth
        # static|none`) reaches the graph ONLY through here, so this is where
        # the selfhost OnboardingState node must exist from. ONE owned write
        # point (`onboarding/selfhost.py`), memoized per target, best-effort —
        # a graph failure must never fail the tool call that triggered it.
        from tortoise.onboarding.selfhost import (
            ensure_selfhost_onboarding_once,
        )
        _target = (os.environ.get("TORTOISE_DB_URI")
                   or os.environ.get("TORTOISE_DB_PATH") or "default")
        ensure_selfhost_onboarding_once(sdk, target=_target)
    return sdk


# ── #9340: the graph is a TOOL ARGUMENT, not a token claim ────────────
#
# The owner's 2026-10-22 decision on #9340 supersedes requirements 1 and 5 of
# that issue: a graph is working context INSIDE one identity, not a tenant
# boundary, so it is selected per call rather than frozen into the token.
#
#   resource URL  → the WORKSPACE (unchanged, RFC 8707 §3 path component)
#   token         → (user, workspace, scope) — unchanged, no migration
#   tool argument → the GRAPH, defaulting to the workspace's default graph
#
# **OVERRIDES:** the tenant-level consent scope used by most MCP connectors (a
# `graph_id` claim minted at consent) — a graph is context within a workspace,
# so binding it at consent would make one account's four graphs addressable
# only by four connections and would force a re-consent (and a token
# re-issue) every time a user switched graph. The owner decision is recorded on
# #9340 (comment "DECISION (2026-10-22): the graph is a **tool argument**, not
# a token claim").
#
# Two properties of this resolver are load-bearing, and both are re-checked on
# EVERY call — never cached at resolution time:
#
#   1. **The reachable set is the token's workspace only.** An id belonging to
#      another workspace must read as "not reachable from this workspace",
#      never as a hit: an unfiltered list plus an unchecked argument is a plain
#      IDOR (a token for workspace A naming workspace B's graph).
#   2. **A soft-deleted graph stops resolving.** The reader is not trusted to
#      filter — the Supabase lane omits deleted rows while the selfhost registry
#      lane still carries a `status='deleted'` row — so the reachable set applies
#      the status filter in BOTH lanes, and the next call after a delete refuses
#      instead of falling back to the default graph: a live connection must never
#      keep reading a graph the user deleted.

from tortoise.tool_registry import (  # noqa: E402, I001
    GRAPH_ARGUMENT_DESCRIPTION as _GRAPH_ARGUMENT_DESCRIPTION,
    GRAPH_ARGUMENT_EXCLUDED as _GRAPH_ARGUMENT_EXCLUDED,
)

#: MCP tools that must NOT advertise (or honour) the `graph` argument: they
#: already name a graph explicitly (`graph_id`), address the WORKSPACE rather
#: than a graph, or are the discovery surface itself. The declaration lives in
#: `tortoise.tool_registry` (that module owns the surface); it is re-exported
#: here because this is where the resolution lives, and ONE import site is
#: what keeps the advertised set and the honoured set from drifting.
GRAPH_ARGUMENT_EXCLUDED = _GRAPH_ARGUMENT_EXCLUDED
GRAPH_ARGUMENT_DESCRIPTION = _GRAPH_ARGUMENT_DESCRIPTION


#: The bad-argument refusal for a `graph` that is not even a string. ONE
#: constant because the refusal must not depend on the TRANSPORT either: an
#: int/list arriving on stdio must be the type error it is, not "this
#: transport has no graph set".
_GRAPH_TYPE_REFUSAL = (
    "The `graph` argument must be a graph id or name (a string). "
    "Omit it to use this workspace's default graph."
)

#: The `graph` id of the workspace-default graph in BOTH readers
#: (`supabase_control.graph_metadata` derives the default row with
#: `graph_id: "default"`; the registry lane carries the same literal). Naming
#: it is a NO-OP that needs no lookup at all — see :func:`plan_graph_target`.
_DEFAULT_GRAPH_ID = "default"


class GraphTargetError(ValueError):
    """A `graph` argument named no graph this caller may reach (#9340).

    Raised — never swallowed — because the two silent alternatives are both
    wrong: falling back to the default graph reads/writes the wrong graph with
    no signal, and an empty result is indistinguishable from a graph that is
    genuinely empty. The message names what the caller CAN reach so the
    refusal teaches.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "invalid_graph"


def _is_default_graph_ref(ref: Any) -> bool:
    """True when `ref` names the workspace DEFAULT graph, which needs no lookup.

    Only the exact id (stripped, case-insensitively) qualifies. A *name* is not
    accepted here: a workspace may rename its default graph (`graph_metadata`
    reads `graphs.name` for it), so a name can only be resolved against the
    workspace's own rows.
    """
    return isinstance(ref, str) and ref.strip().casefold() == _DEFAULT_GRAPH_ID


def _retarget_refusal(requested: str) -> GraphTargetError:
    """The ONE refusal for a `graph` that would widen a graph-bound credential."""
    return GraphTargetError(
        "This credential is bound to one graph and cannot be re-targeted "
        f"by a `graph` argument (requested {requested}).")


def _describe_graph(row: dict) -> str:
    """One graph as a caller can act on it: `name (graph_id)`.

    Every refusal the resolver raises routes its reachable set through here, so
    this is the ONE place that tells a caller what to pass instead. When the
    #10025 `stable_id` differs from `graph_id` it is therefore part of the hint:
    the default row carries the `"default"` SENTINEL in `graph_id` — explicitly
    not an id — and its `stable_id` is the only real id it has, so a hint shaped
    `name (graph_id)` named nothing that identifies that row while the id it
    omitted is exactly the value `graph_metadata` / `GET /v1/graphs` hand out
    (and now a value this resolver accepts). A custom row's two ids are the same
    string, so its hint stays the single short form.
    """
    gid = row.get("graph_id")
    name = row.get("name") or gid
    stable_id = row.get("stable_id")
    if stable_id and stable_id != gid:
        return f"{name!r} (id {stable_id}, graph_id {gid!r})"
    return f"{name!r} ({gid})"


def _active(rows: list[dict], org_id: str) -> list[dict]:
    """The rows a `graph` argument may name, for THIS workspace and ACTIVE.

    Two filters, both load-bearing:

    * **the workspace** — `list_memory_graphs(org_id)` is already org-scoped, but this
      does not TRUST the reader: a row that declares a different ``org_id`` is
      dropped here, so a future reader change (or any row that slips into an
      org-scoped read) cannot turn this resolver into the IDOR the decision
      names — a token for workspace A opening workspace B's graph through a
      namespace nobody re-checked.
    * **the tombstone** — the Supabase reader already omits deleted custom
      graphs; the registry lane can still carry a deleted status, and a
      soft-deleted graph must stop resolving on the NEXT call (a live
      connector must not keep reading a graph the user deleted).

    A row that declares NO ``org_id`` is kept: the resolver's job is to refuse
    an explicitly foreign row, not to invent a tenancy claim a reader never
    made (both shipped readers do set it).
    """
    return [r for r in rows if isinstance(r, dict)
            and (r.get("status") or "active") == "active"
            and r.get("org_id") in (None, org_id)]


def reachable_graph_rows(org_id: str, *, sdk: TortoiseSDK | None = None) -> list[dict]:
    """The ACTIVE graphs of `org_id` — the only set a `graph` argument may name.

    `list_memory_graphs` is the control-plane reader (Supabase `graphs` table / selfhost
    registry Graph nodes). The `status` filter here is what carries the
    tombstone guarantee into the registry lane, whose rows may still be present
    with a deleted status.

    `sdk` is injectable so a test can pin the reachable set without a database.
    """
    if not org_id:
        return []
    reader = sdk if sdk is not None else _get_org_sdk()
    rows = reader.list_memory_graphs(org_id)
    return _active(rows, org_id) if isinstance(rows, list) else []


def resolve_graph_target(org_id: str, ref: str, *,
                         rows: list[dict] | None = None) -> dict:
    """Resolve one `graph` argument to a row this caller may reach (#9340).

    Resolution order is the owner-decided rule, and the order is the point:
    an **id** always wins over a name, and a **name** resolves only when it is
    unique within the token's workspace. A name matching two graphs is refused
    with the candidates, because picking one silently is a wrong-graph write
    the caller cannot detect — the failure class this feature is most likely
    to cause.

    Both spellings of a row's id are accepted, because a caller may have read
    either one off another surface: `graph_id` (the storage/registry id — and,
    for the DEFAULT row, the `"default"` SENTINEL) and `stable_id` (#10025's
    identity field). For the default graph `stable_id` is the ONLY real id, so
    without it the value `graph_metadata`/`GET /v1/graphs` calls a graph's
    identity was REFUSED here while the sentinel — explicitly not an id —
    resolved: a dual-surface caller (list over REST, write over MCP, the shape
    this repo's own agents take) could not use the id it had just been handed.

    Raises :class:`GraphTargetError` for anything that is not exactly one
    reachable graph: unknown id/name, a name matching several, a graph that is
    soft-deleted (absent from the reachable set), and a graph belonging to
    another workspace (indistinguishable from unknown ON PURPOSE — the refusal
    must not confirm that another tenant's graph id exists).
    """
    ref = ref.strip() if isinstance(ref, str) else None
    if ref is None:
        # The advertised schema says `string`, but that schema is descriptive:
        # nothing validates it on this path (the handler signature does not
        # declare the argument). An int/list/object here would otherwise raise
        # AttributeError out of the dispatch and be classified a SERVER error
        # rather than the bad-argument refusal it is.
        raise GraphTargetError(_GRAPH_TYPE_REFUSAL)
    # An injected row set is filtered HERE too, so the tenancy + tombstone
    # rules hold on both entry points rather than depending on which one the
    # caller used.
    reachable = _active(reachable_graph_rows(org_id) if rows is None else rows,
                        org_id)
    names = ", ".join(_describe_graph(r) for r in reachable) or "none"
    if not ref:
        raise GraphTargetError(
            "The `graph` argument is empty. Omit it to use this workspace's "
            f"default graph, or pass one of: {names}.")
    # 1. id — exact, and allowed to win over a name that happens to collide.
    for row in reachable:
        if ref == row.get("graph_id"):
            return row
    # 1b. the #10025 identity id, matched over the SAME `reachable` set — the
    #     workspace + tombstone filters above are exactly what keeps this from
    #     becoming an IDOR: a foreign or soft-deleted row's stable id is not in
    #     the list, so it stays indistinguishable from unknown. The default
    #     row carries the sentinel in `graph_id`, so `stable_id` is the only
    #     field that can name it by id at all.
    for row in reachable:
        if row.get("stable_id") and ref == row["stable_id"]:
            return row
    # 2. namespace — the storage spelling `list_memory_graphs` returns, so a caller
    #    that read it from `tortoise_list_graphs` can pass it straight back.
    for row in reachable:
        if row.get("namespace") and ref == row["namespace"]:
            return row
    # 3. name — only when unique, case-insensitively (a user types a name, not
    #    its casing). Several matches is a REFUSAL, not a guess.
    wanted = ref.casefold()
    by_name = [r for r in reachable
               if (r.get("name") or "").casefold() == wanted]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise GraphTargetError(
            f"Graph name {ref!r} matches {len(by_name)} graphs in this "
            f"workspace: {', '.join(_describe_graph(r) for r in by_name)}. "
            "Pass the graph id instead.")
    raise GraphTargetError(
        f"No graph {ref!r} is reachable from this workspace. "
        f"Reachable graphs: {names}.")


def plan_graph_target(ref: Any) -> dict | None:
    """Resolve + authorise a `graph` argument — and DO the I/O, changing nothing.

    Split from :func:`use_graph_target` because the dispatch seam is an async
    coroutine: `reachable_graph_rows` is a control-plane read (in the Supabase
    lane, up to three PostgREST round trips on a synchronous client), so it must
    run OFF the event loop. A ContextVar `.set()` inside `asyncio.to_thread`
    would die with the thread's context copy, so the read rides the thread and
    the ScopeVars are applied back on the loop — hence two functions rather than
    one. `apply_graph_target` keeps the synchronous pairing for tests and any
    caller already on a worker thread.

    Raises :class:`GraphTargetError` for every refusal (all of them BEFORE any
    ContextVar moves, so a refused argument cannot half-scope a request).
    """
    if ref is None:
        return None
    if not isinstance(ref, str):
        # Before the transport check, so a caller's TYPE error is answered as
        # one on every transport instead of being blamed on the transport.
        raise GraphTargetError(_GRAPH_TYPE_REFUSAL)
    if _is_default_graph_ref(ref):
        # `"default"` names the graph the untouched path ALREADY opens, so it
        # needs neither a workspace nor a reachable-set lookup. Two refusals
        # below must therefore not fire on it (#9340 review P2):
        #
        #   * the no-workspace refusal — otherwise a connector that sets
        #     `graph` uniformly, the usage this whole argument is designed for,
        #     loses EVERY tool on stdio/self-host for a ref that is trivially
        #     satisfiable;
        #   * the reachability resolution — a workspace whose stored
        #     `graph_name` is empty makes `graph_metadata` return `[]` (and any
        #     reader answering with a non-list reads as `[]`), so there is no
        #     default ROW to match, while the SAME call with the argument
        #     omitted reads that very graph. Refusing it tells the caller
        #     "Reachable graphs: none" about a graph it is reading.
        #
        # The one thing still refused: a credential ALREADY bound to another
        # graph, because telling it "default" while every call keeps reading
        # its own graph is the silent wrong-graph outcome the bound check
        # exists to prevent.
        bound = _current_graph_id.get()
        if bound and bound != _DEFAULT_GRAPH_ID:
            raise _retarget_refusal("the workspace default graph")
        return None
    org_id = _current_org_id.get()
    if not org_id or org_id == SELFHOST_ORG_ID:
        # No workspace to resolve against: stdio/self-host has no control-plane
        # graph set, so a name cannot be checked. Refusing beats attaching a
        # connection to a graph the caller did not choose.
        raise GraphTargetError(
            "The `graph` argument requires a hosted workspace connection "
            "(an OAuth or tenant-key MCP session) — this transport has no "
            "graph set to resolve it against. Omit `graph` to use the "
            "default graph.")
    row = resolve_graph_target(org_id, ref)
    bound = _current_graph_id.get()
    if bound and bound != row.get("graph_id"):
        # A graph-bound API key is ALREADY narrowed to one graph. Honouring a
        # `graph` argument here would widen a credential past its mint — and
        # this check comes BEFORE the default-graph early return, because
        # otherwise a bound key asking for the workspace default would be told
        # "default" while every call kept reading its own graph: the silent
        # wrong-graph outcome, with the caller's intent freshly confirmed.
        raise _retarget_refusal(_describe_graph(row))
    if (row.get("kind") or "") == _DEFAULT_GRAPH_ID:
        return row
    graph_id = row.get("graph_id")
    namespace = row.get("namespace")
    if not graph_id or not namespace:
        # `_get_org_sdk()` opens a custom graph only from the PAIR; with either
        # half missing it would fall through to the workspace default, i.e.
        # read the wrong graph under a custom graph's name.
        raise GraphTargetError(
            f"Graph {_describe_graph(row)} cannot be opened: it is missing its "
            "graph id or its storage namespace. Omit `graph` to use the "
            "default graph.")
    return row


def use_graph_target(plan: dict | None) -> dict | None:
    """Apply a :func:`plan_graph_target` result to THIS request. No I/O.

    A resolved CUSTOM graph sets the same two ContextVars the credential
    resolution sets (`_current_graph_id` / `_current_graph_namespace`), so
    `_get_org_sdk()` opens that graph through the SAME seam — no second way to
    open a graph, and every tool benefits without a per-tool change.

    The resolved DEFAULT graph deliberately sets nothing: `list_memory_graphs` returns
    the workspace namespace for it, and opening that by graph name would be a
    second, subtly different construction of the graph the untouched path
    already opens. "default" therefore means byte-identical behaviour — and a
    `None` plan (no argument) means the same thing.
    """
    if plan is None or (plan.get("kind") or "") == "default":
        return plan
    _current_graph_id.set(plan.get("graph_id"))
    _current_graph_namespace.set(plan.get("namespace"))
    return plan


def apply_graph_target(ref: str | None) -> dict | None:
    """Synchronous convenience: plan + use, for callers already off the loop.

    The async dispatch seam MUST use the two halves separately (see
    :func:`plan_graph_target`); tests and worker-thread callers use this.
    """
    return use_graph_target(plan_graph_target(ref))


def record_tenant_for_graph(plan: dict | None) -> tuple[str, str] | None:
    """The record tenant a SELECTED custom graph implies, or None (#9340).

    An org-wide credential has no `graph_id` at resolution time, so the
    ambient scope names its DEFAULT graph — the credential's
    `default_graph_id` since #10025 / #10046 — and nothing names a graph the
    call SELECTS. A graph selected per call DOES carry a control-plane
    `public.graphs` row — the very row the resolver just read — so the tenant
    is nameable and must be carried, or the write lands in graph X while its
    record row stays filed under the default (or, under a declared
    single-tenant env pair, under graph Y). That divergence is the same
    silent-wrong-target class this function exists to close.

    The DEFAULT graph is deliberately still None HERE, but no longer because
    it lacks a row or because its credential cannot name it: `20261028000001`
    mints an active `kind='default'` row per organization (provisioning mints
    it forward, with an auth-time self-heal), and every credential resolver
    stamps that row's id as `default_graph_id` — `resolve_api_key` since
    #10025, `hosted_api._session_user_org` and
    :func:`tortoise.oauth.resolve_oauth_access_token` since #10046. So a
    DEFAULT selection does not need THIS seam to install anything: it enters
    no scope, and the ambient scope `OrgResolutionMiddleware` already installed
    from the credential names the same default graph. Wiring this branch
    explicitly is #10047's scope — a deferral of this seam only; the lanes that
    build their own SDK and write the org DEFAULT graph are #10047 too, and they
    are named there rather than here.
    """
    if plan is None or (plan.get("kind") or "") == "default":
        return None
    org_id = _current_org_id.get()
    graph_id = plan.get("graph_id")
    if not org_id or not graph_id:
        return None
    from tortoise.record_writer import record_tenant_for
    from tortoise.supabase_control import is_supabase_enabled

    return record_tenant_for({"org_id": org_id, "graph_id": graph_id},
                             control_plane_ids=is_supabase_enabled())


# ── HTTP tool allow-list (derived from registry; #454) ────────────────
# New tools are EXCLUDED from the tenant HTTP surface unless registered with
# http_policy=True in tool_registry.py. Zero manual sync — see #454.
from tortoise.tool_registry import get_http_allowed as _get_http_allowed  # noqa: E402, I001
HTTP_ALLOWED: frozenset[str] = _get_http_allowed()


# JSON-RPC error codes (D9)
ERR_UNAUTHORIZED = -32001
ERR_RATE_LIMIT = -32002
ERR_EXCLUDED = -32004
ERR_REGISTRY = -32005
# #308 (R5): suspended org — mirrors REST 403 SUSPENDED (appeal link in data)
ERR_SUSPENDED = -32006
# #3834: the transport-level wait bound was breached — the server stopped
# waiting before a response was ready. On the REST surface this is the code in
# the JSON-RPC 504 body returned by ``hosted_api.WaitBoundMiddleware``. On the
# MCP surface it is the code in the refused tool result
# (``mcp_server._await_under_mcp_wait_bound``): the MCP SDK converts
# tool-handler exceptions to ``CallToolResult(isError=True)``
# (``mcp/server/lowlevel/server.py::_make_error_result``), so once the SSE
# stream is open a raised ``McpError`` loses its code and ``data`` — the result
# is the channel that can still carry the retry signal.
#
# WHICH field inside that result carries it depends on the tool's envelope
# (#8482). For a tool with no wrap envelope it is ``structuredContent.error``.
# For the 13 wrap-envelope tools (``x-fastmcp-wrap-result``) the payload is
# re-homed on the result's ``_meta.tortoise_error`` instead — the same
# ``{code, message, data}`` mapping — because the repair that moves a
# schema-violating payload off ``structuredContent`` is exactly what drops it
# (``mcp_server._schema_error_result``). Either way the advertised delay is
# ``error.data.retry_after`` (#3851's shape).
#
# -32009, NOT -32007: the ERR_* namespace is split across this module and
# ``mcp_server.py``, and -32007 is already ``ERR_QUOTA_SERVER`` there (see the
# ``#329`` namespace note above ``ERR_QUOTA_SERVER`` in ``mcp_server.py``, which
# already tracks the ``-32006`` quota/suspended collision as a known defect).
# Reusing -32007 would make a wait-bound refusal indistinguishable from a
# server-quota refusal.
ERR_TIMEOUT = -32009


# ── #3834: the transport-level wait bound's VALUE and refusal vocabulary ───
# The bound is enforced at two seams — ``hosted_api.WaitBoundMiddleware`` for
# the REST routes and ``mcp_server._await_under_mcp_wait_bound`` for the MCP
# tool dispatch — and this module is the only place both can share without an
# import cycle (``hosted_api`` imports ``mcp_server``, which imports this
# module). It is also the transport-NEUTRAL home: these three constants carry no
# REST- or MCP-specific behaviour, unlike ``hosted_api._TRANSPORT_WAIT_BOUND_
# EXEMPT``, which is a REST-only route exemption and stays there.
#
# The number is not chosen here: it is the value the owner pinned for this
# question (10 s, under the 15 s flat budget of the narrowest uncontrolled
# client, D-12) and it is re-homed unchanged. It is a module constant rather
# than an env knob on purpose — once a caller codes to the number, a silent
# env change is a client-visible contract change (owner's own framing).
#
# Why the bound is justified even though the tail it cuts is small: the measured
# maximum MCP tool-call latency sits ABOVE the 15 s client budget, so for that
# tail the bound does not abandon work that would otherwise have succeeded — it
# converts an opaque client-side timeout into a legible refusal. Measured on the
# TRANSPORT-wide population (the 7,795 `mcp_tool_call` events in
# `~/.tortoise/analytics_fallback.jsonl`; 22,510 events in the file at this
# measurement, nearest-rank quantiles on `latency_ms`): p50 26 ms, p95 2,213 ms,
# p99 22,476 ms, max 157,116 ms; 162 calls (2.1%) exceed the bound. ⚠️ Caveat
# that travels with these numbers: this is the MCP transport PER TOOL CALL, not
# the REST HTTP request wait — the best available proxy, not the same quantity.
# The 10 s value itself was derived from the ask lane's distribution at
# derivation time (n=573, 1 call > 10 s) and re-homed onto this wider one; the
# breach event exists so that the difference is measurable in production rather
# than assumed.
_TRANSPORT_WAIT_BOUND_S = 10.0

#: Seconds advertised as the back-off on a breach. Ships WITH the bound as one
#: unit — a bound alone turns an invisible failure into a visible one with no
#: recovery. This is the single source for the number: the message below carries
#: no literal, and the REST ``Retry-After`` header and the JSON-RPC
#: ``error.data.retry_after`` both read it.
#:
#: ⚠️ It MUST NOT be shorter than ``_TRANSPORT_WAIT_BOUND_S``. Every breach was
#: caused by work that exceeded the bound, so a shorter advertised delay tells a
#: compliant caller to re-enter the SAME slow operation while the abandoned
#: attempt is still running. What the SHIPPED pair 10/10 BUYS is only a FLOOR:
#: a compliant caller cannot re-enter before the bound elapses, so overlap now
#: requires work that outlives ``bound + retry_after`` (≈20 s). That is NOT an
#: elimination — the measured tail above exceeds it (p99 22,476 ms, max
#: 157,116 ms), so a retry can still land while the abandoned original runs,
#: and for a non-idempotent tool the original can still commit AFTER the caller
#: was told to retry — duplicate side effects. The record below is for the
#: PRE-FIX pair: at the pre-fix bound/retry = 10/2 the steady-state concurrent
#: copies of one logical operation WERE 5 (measured at 1/50 scale: refusals=5,
#: dispatches_started=5, peak_concurrent=5 for ONE logical operation). A prose
#: caveat does not discharge this: retry middleware acts on status/code/header,
#: not on the body. Do NOT "simplify" the retry constant back to 2. Pinned by
#: ``tests/test_transport_wait_bound.py::
#: test_retry_signal_is_never_shorter_than_the_bound``.
_TRANSPORT_WAIT_RETRY_AFTER_S = 10

#: The readable refusal. Static and digit-free (the advertised delay has exactly
#: one source, above) and transport-neutral (it also ships on the MCP surface,
#: which has no header). Answers the three things a caller must be able to read
#: at the call site: what happened, whether to retry, and how long to wait.
_TRANSPORT_WAIT_BOUND_MESSAGE = (
    "The server's wait budget for this request was exceeded before a response "
    "was ready. The work may still complete on the server. Wait for the "
    "advertised delay before retrying, and retry only if repeating the "
    "operation is safe."
)


def _sanitize_for_log(value: str) -> str:
    """Escape the control characters that can forge a log line or an ANSI
    escape, for a log AND a telemetry sink (the sanitized form is passed to
    both).

    Lives HERE, not in ``hosted_api``, for the same reason the bound's
    constants do (#3834): both surfaces need it — the REST arm sanitizes the
    route path, the MCP arm the client-supplied tool name — and ``mcp_server``
    must not import ``hosted_api`` on the fast path (that import builds the
    whole hosted FastAPI app). This module is the neutral home both surfaces
    already share.

    The ASGI server percent-DECODES the path, so ``/v1/x/%0d%0aFORGED`` arrives
    with embedded CR/LF; escaped verbatim it forges log lines. CR/LF alone is
    not the whole class (code-review round 2): VT/FF/ESC/NUL, DEL, the C1 range
    (U+0085 NEL and U+009B CSI are line-break / escape introducers to Unicode-
    aware readers) and U+2028/U+2029 all do the same. CR/LF/TAB keep their
    readable backslash escapes so existing log greps still match. This is
    deliberately BROADER than ``tortoise/schemas.py``'s C0-only control-char
    validation: that rejects a user field; this escapes a value bound for a log
    line and a telemetry sink.
    """
    out = []
    for ch in value:
        if ch == "\r":
            out.append("\\r")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch < " " or "\x7f" <= ch <= "\x9f":
            out.append(f"\\x{ord(ch):02x}")
        elif ch in ("\u2028", "\u2029"):
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


# ── #4055: keep one-time PROCESS SETUP out of the wait budget ──────────────
# The bound times the WAITING, not the SETTING UP (owner ruling on #4055,
# comment 5845912938: "Option 2 — lift the one-time load out of the bounded
# section"). The collision it fixes: the first embedding request after a
# process start runs ``EmbeddingModel.get()``, whose one-time load is sized
# above this bound by the codebase's own constant (``_LOAD_TIMEOUT_S``, 90.0;
# 270.0 in the test lane, #6960) and measured ~27s cold — so the request was
# refused 504 while the load still ran, order- and env-dependently (the
# rotating `python-ci` red on `main`).
#
# Why a clock exemption and not a startup pre-warm: the owner rejected the
# startup pre-warm (it changes the selfhost startup contract and needs no
# change here), and a route/tool allowlist for eager warming would drift
# across 126 routes / ~80 tools and load a model on shards that never embed.
# This reads the embedder's own one-time-load state (``embeddings.EmbeddingModel``)
# only when a deadline has ALREADY fired, so the ordinary path pays nothing.
# ``TORTOISE_EMBEDDER_WARMUP`` does NOT gate this path, and that is the point:
# the exemption reads the embedder's load state, not the flag. A hosted
# lifespan pre-warm that DOES run (``hosted_api._prewarm_embeddings`` — itself
# gated on the flag as of #7809, so ``=0`` skips it entirely) is process setup
# no request owns: it stamps no setup clock (``EmbeddingModel.background_load``),
# so a process that runs the hosted lifespan can hold a load no request asked
# for. Background pre-warms are excluded at the source, so this exemption
# fires for the REQUEST that triggered a load and nothing else.
#
# ⚠️ OPEN (measured, #4055) — this exemption does NOT close that issue:
#   1. The load that blocks the issue's own reproduction is the LIFESPAN
#      PRE-WARM. It is ownerless by design, so it never stamps the clock this
#      reads and the request is still refused 504. #4055 records the
#      reproduction failing at head b38fc2219 for exactly this reason.
#   2. A SECOND concurrent request that blocks on the same in-flight
#      REQUEST-owned load is refused as well: admission is by owner IDENTITY,
#      so it does not inherit the triggering request's exemption.
# Both belong to #4055, not to this change — which is why the PR references the
# issue rather than closing it.
_COLD_SETUP_POLL_S = 0.05


def _cold_setup_ran_during(reference: float, owner: object | None = None) -> bool:
    """True when a REQUEST-OWNED one-time embedder load sits in this wait.

    A load is admitted only when ALL of the following hold (see
    ``EmbeddingModel.one_time_setup_owned_and_started_since``):

    * it is currently IN FLIGHT — a load that started during the wait but
      COMPLETED before the deadline is not setup this request is still waiting
      on, so exempting it would re-arm the full bound for work that is entirely
      the request's own (P1);
    * it STARTED at/after ``reference`` — the request whose wait began at
      ``reference`` did not merely overlap an older load;
    * ``owner`` IDENTIFIES this request — a concurrent request that started the
      one-time load inside this request's wait is not this request's setup
      (P2). ``owner`` is the token the caller bound with
      ``embeddings.request_load_owner``; ``None`` never admits.

    A BACKGROUND pre-warm (#2952, the hosted ``_prewarm_embeddings`` thread) is
    deliberately NOT admitted: it is process setup no request owns, so it never
    stamps the clock this reads (``EmbeddingModel.background_load``). A request
    that merely overlaps a pre-warm is waiting on init it did not trigger, and
    exempting it turned a genuine breach into a false success (a sibling test's
    app lifespan pre-warm leaked its load into the next request's wait).

    Never raises: the embedder is optional (the ``embeddings`` extra may be
    absent), and a wait-bound seam must not fail because a probe did.
    """
    try:
        from tortoise.embeddings import EmbeddingModel
    except Exception:  # noqa: BLE001, RUF100 — the embedder is optional; no bound may depend on it
        return False
    return EmbeddingModel.one_time_setup_owned_and_started_since(reference, owner)


async def _drain_cold_setup() -> None:
    """Wait out an in-flight one-time embedder load, OFF the request budget.

    Bounded by the embedder's OWN ``_LOAD_TIMEOUT_S`` (the load attempt cannot
    outlive its join timeout), so this cannot hang a request on a wedged model.
    Polling, not an event await: the check is a lock-free bool read, this fires
    at most once per process, and a poll keeps the ordinary path free of any
    thread hand-off.
    """
    try:
        from tortoise.embeddings import EmbeddingModel
    except Exception:  # noqa: BLE001, RUF100 — the embedder is optional
        return
    while EmbeddingModel.one_time_setup_in_progress():
        deadline = EmbeddingModel.one_time_setup_deadline()
        if deadline is None:
            # No recorded start to bound against (the two are set together in
            # ``__init__``, so this is defensive): stop rather than spin, and
            # let the caller's re-armed bound decide.
            return
        if time.monotonic() >= deadline:
            return
        await asyncio.sleep(_COLD_SETUP_POLL_S)


async def await_under_wait_bound(task: asyncio.Task, timeout: float, *,
                                 reference: float, owner: object | None = None):
    """Await ``task`` under ``timeout``, exempting one-time PROCESS SETUP (#4055).

    Both bound seams (REST ``hosted_api.WaitBoundMiddleware`` and MCP
    ``mcp_server._await_under_mcp_wait_bound``) delegate here, for the same
    reason the number itself lives in this module: one implementation, no
    drift between the surfaces.

    Semantics, stated precisely because a bound is a promise:

    * the ORDINARY request is unchanged — the task gets ``timeout`` and a
      breach raises ``TimeoutError`` exactly as the bare
      ``asyncio.wait_for(asyncio.shield(task), timeout=…)`` did;
    * when the deadline fires while the process's ONE-TIME embedder load, OWNED
      BY THIS REQUEST AND STILL IN FLIGHT, ran during this wait, the load is
      drained OUTSIDE the budget and the bound THIS SEAM WAS GIVEN is re-armed
      for the request's own work — **at most once**, so a genuinely slow
      request still breaches. WHICH bound that is differs by seam: REST passes
      the transport constant, so it re-arms a full bound; the MCP seam passes
      ``remaining``, which is the residual
      (``_TRANSPORT_WAIT_BOUND_S - elapsed``) when there is a transport
      arrival, and the FULL constant when there is none (stdio). This helper
      re-arms its argument and cannot know which it was given, so a seam that
      wants a full re-arm must pass a full bound;
    * ``timeout <= 0`` never exempts: a deadline that was already spent before
      this seam (the pre-SSE-stall path, where ``remaining`` collapsed to 0)
      is not a cold-start case, and the exactly-once refusal contract on that
      path must not be reopened;
    * ``task`` MUST already be owned by the caller — this helper only SHIELDS
      it, never cancels it, so a breach still leaves it to the caller's
      ABANDON-never-cancel path.

    ``reference`` is the mono-clock instant the caller's wait began (the
    transport arrival on the REST middleware, the same on the MCP arm; the
    seam's own entry off-HTTP). ``owner`` is the identity token the caller
    bound with ``embeddings.request_load_owner`` BEFORE spawning ``task``; it
    is what attributes a load to THIS request (identity, not merely a time
    window — see ``_cold_setup_ran_during``). ``owner=None`` never exempts.
    """
    remaining = max(0.0, float(timeout))
    exempted = False
    while True:
        try:
            # ``wait_for`` + ``shield``: the primitive both seams already used.
            # ``shield`` is REQUIRED — ``wait_for`` alone CANCELS the awaited
            # future on timeout, and these bounds ABANDON, never cancel.
            return await asyncio.wait_for(asyncio.shield(task), timeout=remaining)
        except TimeoutError:
            if task.done():
                # The dispatch finished as the deadline expired: surface its own
                # result/exception. A handler's ``TimeoutError`` is NOT a breach.
                return task.result()
            if (exempted or float(timeout) <= 0.0
                    or not _cold_setup_ran_during(reference, owner)):
                raise
            exempted = True
            await _drain_cold_setup()
            remaining = max(0.0, float(timeout))


# ── #3144 / #3812: the Retry-After contract on an auth-plane 503 ───────────
# An org-resolution outage (control plane or registry unreachable) is a
# RETRYABLE dependency condition, not a hard outage. Before this the 503
# carried no ``Retry-After``, so an MCP client connecting at startup had no
# instruction to back off — and the reported symptom is exactly that: Pi's
# ``mcp-client`` connects eagerly with a 15s connect budget and NO retry, so a
# single 503 during the startup connect silently costs the whole session its
# Tortoise tools (#3144). The header (integer seconds, RFC 7231 §7.1.3) is what
# turns an unrecoverable-looking failure into an actionable one.
#
# Env-overridable and clamped to a sane range: an operator may tune the
# advertised back-off, but a misconfigured value can never advertise 0 seconds
# (a busy-retry that hammers a down dependency) or an absurd window.
#
# Read at CALL time, not frozen in a module constant: ``mcp_server`` imports
# this module BEFORE its ``_load_dotenv()`` runs, so an import-time read would
# silently ignore a value set in ``.env`` (the #880 freeze class — the sibling
# health-probe knobs are call-time reads for exactly this reason).
def _resolve_auth_retry_after_s() -> int:
    """Seconds advertised in ``Retry-After`` on the auth-plane 503 (clamped)."""
    try:
        v = int(os.environ.get("TORTOISE_MCP_AUTH_RETRY_AFTER", "5"))
    except (TypeError, ValueError):
        return 5
    return max(1, min(v, 3600))


def _jsonrpc_error_body(code: int, message: str,
                        data: dict | None = None, *,
                        request_id: Any = None) -> dict[str, Any]:
    """The auth plane's one JSON-RPC error envelope.

    Split out of ``_jsonrpc_error`` so an error that is framed differently on
    the wire -- ``mcp_server``'s ``tools/call`` admission guard answers 200 with
    an SSE frame instead of a JSON HTTP error -- shares the SAME envelope
    definition rather than hand-building a second one. (The SSE framing around
    it is still the guard's own.) ``request_id`` defaults to null, i.e. the
    pre-existing behaviour for every auth-plane caller (a rejected request has
    no trustworthy id to echo).
    """
    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
        "id": request_id,
    }
    if data is not None:
        body["error"]["data"] = data
    return body


def _jsonrpc_error(code: int, message: str, data: dict | None = None,
                   status: int = 400,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    """Build an MCP-compatible JSON-RPC error response with an HTTP status."""
    return JSONResponse(_jsonrpc_error_body(code, message, data),
                        status_code=status, headers=headers)


def _resource_metadata_url(request: Request) -> str | None:
    """Absolute PRM URL for the RFC 9728 challenge (#2864), or None.

    Built from ``scheme`` + the ``Host`` header rather than
    ``request.base_url``: this middleware runs INSIDE the sub-app mounted at
    ``/mcp`` (``hosted_api.py``: ``app.mount("/mcp", mcp_http_app)``), where
    ``base_url`` carries ``root_path="/mcp"`` and would yield
    ``…/mcp/.well-known/oauth-protected-resource/mcp`` — a 404. ``Host`` is the
    client-visible host in both production and tests, and ``scheme`` has already
    been corrected by ``ForwardedProtoMiddleware`` (#985).

    Returns ``None`` — meaning "emit no challenge" — when the host is absent or
    is not a syntactically valid URI host. This is a *syntax* filter, not the
    authorization decision: whether the host is one we are willing to serve at
    all is decided separately by FastMCP's ``HostOriginGuardMiddleware`` allowlist
    (``host_origin_protection=True``), which rejects a non-allowlisted host with
    421 before this middleware runs. Two different jobs — syntax here, allowlist
    there — and both must pass.

    It is nonetheless load-bearing on its own, because the app's guard does NOT
    cover every *host form* that reaches this function — it wraps every request,
    but its normalizer mis-parses some values. The value here is reflected into a
    response header that steers the client's OAuth discovery, and FastMCP's
    ``_normalize_host`` splits on the LAST ``:``, so
    ``Host: api.premiselabs.co:443@evil.com`` normalizes to the allowlisted host
    while its raw form resolves to ``evil.com`` per RFC 3986 — reflecting it would
    hand the attacker the client's authorization-code exchange. Verified
    exploitable before this check existed.
    """
    scheme = request.scope.get("scheme") or "https"
    host = request.headers.get("host")
    if not host or len(host) > 255:
        return None
    match = _SAFE_HOST_RE.match(host)
    if match is None:
        return None
    port = match.group("port")
    if port is not None and not 0 < int(port) <= 65535:
        # `:99999` / `:00000` are RFC 3986-parseable but not usable URL ports —
        # WHATWG/Node reject the URL, so the client's discovery would fail.
        return None
    if host.startswith("["):
        # The grammar in the regex accepts any hex-and-colon run inside brackets,
        # which also admits malformed literals (`[:]`, `[:::]`, `[1::2::3]`,
        # `[12345::1]`). Those are REJECTED by ``urlsplit``/WHATWG just like
        # ``[127.0.0.1]``, so emitting one would be the same silent discovery
        # failure. Validate the literal properly rather than trusting the shape.
        try:
            ipaddress.IPv6Address(host[1:host.index("]")])
        except ValueError:
            return None
    return f"{scheme}://{host}/.well-known/oauth-protected-resource/mcp"


# RFC 3986 host: a reg-name (letters/digits/hyphen/dot) or a bracketed IP-LITERAL,
# plus an optional numeric port. Deliberately narrow — anything outside this
# grammar (userinfo `@`, `/`, `"`, `,`, `%`, whitespace, controls) makes
# _resource_metadata_url return None rather than reflect attacker-controlled text.
#
# `\Z`, NOT `$`: Python's `$` also matches immediately BEFORE a trailing newline,
# so `$` would accept `"api.premiselabs.co\n"` and reflect it (h11 rejects CR/LF
# on both the request and the response side, so that is not reachable through a
# real server — but the grammar should say what it means).
#
# The bracketed branch REQUIRES a colon: RFC 3986's IP-literal is
# `"[" ( IPv6address / IPvFuture ) "]"`, so `[127.0.0.1]` is not a legal host —
# `urlsplit` rejects it outright and WHATWG/Node refuse the URL, which would turn
# the challenge into a silent discovery failure. Shape alone is not enough, so the
# literal is additionally validated with `ipaddress.IPv6Address` (see
# _resource_metadata_url). The port is range-checked separately: `:99999` parses
# here but is not a usable URL port.
_SAFE_HOST_RE = re.compile(
    r"^(?:[A-Za-z0-9][A-Za-z0-9.\-]*|\[[0-9A-Fa-f]*:[0-9A-Fa-f:.]*\])"
    r"(?::(?P<port>[0-9]{1,5}))?\Z"
)


def _unauthorized_challenge(request: Request,
                            enabled: bool) -> dict[str, str] | None:
    """``WWW-Authenticate`` headers for a 401 on the OAuth-protected MCP
    resource (#2864), or ``None`` when no challenge must be emitted.

    MCP 2025-11-25 requires a *discovery mechanism* - either the resource
    metadata URL in the ``WWW-Authenticate`` header or a well-known URI. The
    challenge form itself is RFC 9728 section 5.1's
    ``Bearer resource_metadata="…"`` form. Without this header an MCP client has
    no discoverable path from the 401 to the authorization server — this is the
    defect that blocks the Claude Desktop/Web connector on accounts lacking the
    beta ``Request headers`` field.

    Two independent conditions suppress the challenge, and both matter:

    * ``enabled`` is False — the surface has no authorization server, so the
      header would point at a 404. That is ``StaticKeyMiddleware`` (self-host
      single-key) AND tenant-mode self-host (``tortoise serve --http``, the
      ``create_http_app`` default), which runs this same middleware against a
      registry with no ``/.well-known/*`` routes. Only the hosted app passes
      ``emit_challenge=True``.
    * the origin cannot be safely determined (see ``_resource_metadata_url``).
    """
    if not enabled:
        return None
    url = _resource_metadata_url(request)
    if url is None:
        return None
    return {"WWW-Authenticate": f'Bearer resource_metadata="{url}"'}


class OrgResolutionMiddleware(BaseHTTPMiddleware):
    """Bearer token → org_id ContextVar. 401 pre-tool-leak (D3, D17).

    Accepts THREE credential families (#524, C2 #2111 — additive, never
    breaking):
      * ``tt_<key>`` / ``tk_<key>`` — tenant API keys (tt_ = legacy/owner
        mints; tk_ = C2 per-graph scoped keys, both in API_KEY_PREFIXES).
        Supabase-backed
        (#767): resolves via tortoise.supabase_control.resolve_api_key
        (lookup_hash exact-match; api_keys.revoked_at authoritative;
        tier/quota from orgs) — the SAME shared function REST
        get_current_org uses. Registry apikey_verify (O(keys) salted-hash
        scan) stays for selfhost.
      * ``oat_<token>`` — OAuth 2.1 access tokens (hosted-only, D3):
        introspected via tortoise.oauth.resolve_oauth_access_token (D6 —
        self-sufficient at the MCP boundary, no tt_ key minting). Registry
        mode has no OAuth tables → oat_ always 401s there.
    Bounded 60s true-LRU cache protects against MCP init bursts.
    """

    def __init__(self, app, *, max_cache: int = 10000,
                 registry_sdk: TortoiseSDK | None = None,
                 emit_challenge: bool = False):
        super().__init__(app)
        self._registry_sdk = registry_sdk  # test injection
        # #2864: only the hosted surface has an authorization server to point
        # at. Default False so every other caller (tenant-mode self-host via
        # create_http_app's default) stays challenge-free rather than emitting
        # a header that 404s.
        self._emit_challenge = emit_challenge
        self._init_lock = asyncio.Lock()
        self._cache: OrderedDict[str, tuple[float, dict, dict]] = OrderedDict()  # (ts, org, limits)
        self._max_cache = max_cache

    async def _get_registry_sdk(self) -> TortoiseSDK:
        if self._registry_sdk is None:
            async with self._init_lock:
                if self._registry_sdk is None:
                    # Delegate to hosted_api._make_sdk (canonical SDK builder —
                    # handles TORTOISE_DB_URI vs embedded TORTOISE_DB_PATH vs
                    # /data fallback). Function-level import avoids any cycle.
                    from tortoise import hosted_api as _ha
                    self._registry_sdk = _ha._make_sdk(namespace="registry")
        return self._registry_sdk

    async def dispatch(self, request: Request, call_next):
        # GET metadata route + DELETE (stateless no-op) skip auth (cycle-2 P1 fix)
        if request.method != "POST":
            return await call_next(request)
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return _jsonrpc_error(
                ERR_UNAUTHORIZED,
                "Unauthorized: invalid or missing Bearer token. "
                "Expected format: Authorization: Bearer tt_<key>/tk_<key> "
                "(or an OAuth access token for #524 OAuth clients)",
                status=401,
                headers=_unauthorized_challenge(request, self._emit_challenge),
            )
        token = auth[7:]
        # C2 (#2111): accept tk_ scoped keys too. Lazy import preserves the
        # module's documented "imports ONLY tortoise.sdk + starlette"
        # contract (auth.py triggers pepper env checks at module import).
        from tortoise.auth import API_KEY_PREFIXES
        if not (token.startswith(API_KEY_PREFIXES) or token.startswith("oat_")):
            if not token:
                return _jsonrpc_error(
                    ERR_UNAUTHORIZED,
                    "Unauthorized: invalid or missing Bearer token. "
                    "Expected format: Authorization: Bearer tt_<key>/tk_<key> "
                    "(or an OAuth access token for #524 OAuth clients)",
                    status=401,
                    headers=_unauthorized_challenge(request, self._emit_challenge),
                )
            return _jsonrpc_error(
                ERR_UNAUTHORIZED,
                "Unauthorized: invalid Bearer token format. "
                "Expected tt_<tenant key> or an OAuth access token.",
                status=401,
                headers=_unauthorized_challenge(request, self._emit_challenge),
            )
        is_oauth = token.startswith("oat_")
        now = time.time()
        cached = self._cache.get(token)
        if cached and now - cached[0] < 60:
            # #308 (delta 14): a suspension signal forces a FRESH resolution —
            # a cached entry can never serve a suspended org. The set is a
            # cache-invalidation signal only; durable suspended_at decides.
            from tortoise.abuse import is_suspended_signal
            if is_suspended_signal(cached[1].get("org_id") or ""):
                cached = None
            # C2 (#2111) defense-in-depth: a deleg=0 minted key must never
            # ride a warm cache entry. Fresh resolution gates BEFORE the
            # cache is written, so this can only trip on an entry cached
            # before the mint stamped delegation_depth — a 60s TTL edge
            # that costs one dict lookup to close.
            if cached is not None and not is_oauth \
                    and cached[1].get("delegation_depth") == 0:
                self._cache.pop(token, None)
                cached = None
        if cached and now - cached[0] < 60:
            org, limits = cached[1], cached[2]
            self._cache.move_to_end(token)  # true LRU
        else:
            try:
                # #767 (plan Task 3): Supabase-backed resolution (lookup_hash)
                # when the control plane is Supabase-backed — the SAME shared
                # function REST get_current_org uses (single source of truth,
                # REST + MCP cannot drift). Registry apikey_verify stays for
                # selfhost. #524: OAuth access tokens (oat_) introspect via
                # tortoise.oauth — hosted-only (registry mode has no OAuth
                # tables → None → 401).
                from tortoise.supabase_control import (  # noqa: I001
                    get_control_plane, is_supabase_enabled, resolve_api_key,
                )
                if is_supabase_enabled():
                    if is_oauth:
                        from tortoise.oauth import resolve_oauth_access_token
                        # #10046 review P2: this resolution is no longer read-only.
                        # It stamps the org's default-graph row id for the
                        # record tenant, and on a MISS that means
                        # `default_graph_id_for_credential` MINTS the row (up to
                        # three graph reads, an org read, an insert and an
                        # adopt-read, each with its own per-phase timeout on the
                        # sync PostgREST client). Since #3773/#3718 that class of
                        # sync control-plane I/O must ride a worker, not the
                        # event loop — on the loop one degraded control plane
                        # stalls EVERY concurrent request on the process, auth
                        # included. Same seam as `_abuse.check_new_country`
                        # below. Safe to thread: the resolver writes no
                        # ContextVar (it returns the dict; the #9192 ScopeVars
                        # are applied back on the loop by the caller), so the
                        # thread's context copy loses nothing.
                        org = await asyncio.to_thread(
                            resolve_oauth_access_token,
                            get_control_plane(), token)
                    else:
                        org = resolve_api_key(get_control_plane(), token)
                else:
                    if is_oauth:
                        org = None
                    else:
                        sdk = await self._get_registry_sdk()
                        org = sdk.apikey_verify(token)
            except Exception:
                # Registry/control plane down → 503, never 500/stack-trace.
                # The 503 carries ``Retry-After`` (#3144/#3812): an auth-plane
                # outage is retryable, and an MCP client that cannot read a
                # back-off treats it as a hard outage and gives up on the
                # startup connect.
                return _jsonrpc_error(
                    ERR_REGISTRY,
                    "Authentication temporarily unavailable. Try again shortly.",
                    status=503,
                    headers={"Retry-After": str(_resolve_auth_retry_after_s())},
                )
            if org is None:
                return _jsonrpc_error(
                    ERR_UNAUTHORIZED,
                    "Unauthorized: invalid API key. "
                    "Expected format: Authorization: Bearer tt_<key>/tk_<key>",
                    status=401,
                    headers=_unauthorized_challenge(request, self._emit_challenge),
                )
            # C2 (#2111) → C5 (#2114) one-level-deep guard (code-review P1,
            # #2b): a MINTED (deleg=0) key drives MCP tools ONLY when it
            # carries a data scope (graphs:read/write) — C5 routes it to its
            # OWN graph (_get_org_sdk) and the tool-call scope gate enforces
            # read/write. A deleg=0 key WITHOUT a data scope stays rejected
            # (a keys:manage/team:manage-only child has no graph data to
            # exercise; escalation scopes never land on children). Pre-C5
            # this rejected ALL deleg=0 keys blanket (dormancy). resolve_api_key
            # (Supabase) and apikey_verify (registry, extended in C2) both
            # carry delegation_depth + scopes. tk_ keys minted with deleg=NULL
            # (an owner-minted scoped key — C3 surface) pass regardless.
            if not is_oauth and org.get("delegation_depth") == 0 and not (
                    {"graphs:read", "graphs:write"}
                    & set(org.get("scopes") or [])):
                # jsonrpc -32001 (ERR_UNAUTHORIZED) with HTTP 403 — mirrors
                # the ERR_SUSPENDED precedent (403 status + data payload
                # carrying a machine-readable code): MCP clients switching
                # on the jsonrpc code get a distinct authz signal.
                return _jsonrpc_error(
                    ERR_UNAUTHORIZED,
                    "Minted keys cannot be used over MCP without a data "
                    "scope (graphs:read/write).",
                    {"code": "KEY_NOT_USER_MINTED"},
                    status=403,
                )
            # #1854 (review P2): best-effort #685 write-through on MCP
            # resolution — a recovery key used ONLY via MCP (never REST)
            # must still bump last_used_at, else NULL-first rotation treats
            # a LIVE MCP credential as never-used and rotates it (the #1854
            # bug class on the MCP surface). Mirrors the REST lanes'
            # best-effort write; telemetry must NEVER gate auth, so a write
            # failure is swallowed. OAuth tokens have no api_keys row
            # (key_id None) → no write.
            try:
                _key_id = org.get("key_id")
                if _key_id:
                    from datetime import UTC, datetime
                    if is_supabase_enabled():
                        from tortoise.supabase_control import (  # noqa: I001
                            get_control_plane, update_last_used,
                        )
                        update_last_used(get_control_plane(), _key_id)
                    else:
                        _sdk = await self._get_registry_sdk()
                        _sdk._get_registry().query(
                            "MATCH (k:APIKey {id: $id}) SET k.last_used_at = $now",
                            params={"id": _key_id,
                                    "now": datetime.now(UTC).isoformat()},
                        )
            except Exception:
                pass  # telemetry — auth already succeeded
            # #308 (R5): durable suspension check — the sole rejection
            # authority. Pop the LRU entry so a re-resolution is required
            # after un-suspension; clear the signal when the fresh
            # resolution says the org is NOT suspended (AC8 self-heal).
            from tortoise.abuse import (appeal_url, clear_suspended,  # noqa: I001
                                        is_suspended_signal, suspended_message)
            suspended_at = org.get("suspended_at")
            if suspended_at is None and not is_supabase_enabled():
                # Registry mode: apikey_verify returns no suspension state —
                # read the durable Org prop the abuse store writes (delta 4).
                try:
                    sdk = await self._get_registry_sdk()
                    rows = sdk._get_registry().query(
                        "MATCH (t:Team {id: $id}) RETURN t.suspended_at",
                        params={"id": org.get("org_id")},
                    ).result_set
                    suspended_at = rows[0][0] if rows else None
                except Exception:
                    suspended_at = None  # best-effort selfhost enforcement
            if suspended_at is not None:
                self._cache.pop(token, None)
                return _jsonrpc_error(
                    ERR_SUSPENDED, suspended_message(),
                    {"code": "SUSPENDED", "appeal_url": appeal_url()},
                    status=403,
                )
            if is_suspended_signal(org.get("org_id") or ""):
                clear_suspended(org.get("org_id") or "")
            if is_supabase_enabled():
                # The Supabase resolution already carries tier/quota from the
                # orgs row (one round-trip) — use it directly as the limits
                # dict so REST and MCP enforce identical limits (#329).
                limits = org
            else:
                # #329: resolve quota limits (registry Org node). A resolution
                # FAILURE leaves a keyless {"org_id": ...} — fail-closed, not
                # "defaults": `enforce_org_limit` raises QuotaCheckError on a
                # MISSING limit key for every resource (#310 GAP-B, #4010), so
                # a degraded resolution refuses writes rather than granting
                # them. (The old comment claimed "defaults" applied; there are
                # none, and sessions' 1000 fallback was deleted in #4010.)
                from tortoise.quota import resolve_org_limits
                try:
                    limits = resolve_org_limits(org["org_id"])
                except Exception:
                    limits = {"org_id": org["org_id"]}
            # #2600 (#2664, review #8): alias actor_user_id before caching
            # so the cache always holds an already-aliased dict — never
            # mutates a cached object on a warm hit.
            from tortoise.sdk import _alias_actor_user_id
            _alias_actor_user_id(org)
            if len(self._cache) >= self._max_cache:
                self._cache.popitem(last=False)  # evict LRU
            self._cache[token] = (now, org, limits)
        _current_org_id.set(org["org_id"])
        _current_org_limits.set(limits)
        # #2600: canonical actor_user_id ContextVar — the org dict is
        # already aliased before it was cached (cache-miss) or is carrying
        # actor_user_id from the cached entry (cache-hit); this line reads
        # whichever is present (None → unattributed).
        from tortoise.sdk import _current_actor_user_id
        _current_actor_user_id.set(org.get("actor_user_id"))
        # C5 #2114 (D-C5-4): graph scope rides the resolution — the SAME C1
        # tenancy fields REST get_current_org carries. Session/legacy/OAuth
        # resolutions (no graph_id) leave the defaults → org-wide SDK.
        _current_graph_id.set(org.get("graph_id"))
        _current_graph_namespace.set(org.get("graph_namespace"))
        _current_scopes.set(org.get("scopes"))
        _current_legacy_full_access.set(org.get("legacy_full_access"))
        _transport_mode.set("http")
        # #308 (R4, delta 10): geo check on EVERY post-auth request —
        # cache-hit and cache-miss alike, so an IP-rotation burst cannot ride
        # the 60s LRU past detection. Best-effort; in-process seen-cache makes
        # the hot path allocation-free (durable lookup once/org/24h).
        try:
            from tortoise import abuse as _abuse
            if not _abuse.abuse_disabled():
                country = _abuse.resolve_country(request.headers)
                if country and org.get("org_id"):
                    await asyncio.to_thread(
                        _abuse.check_new_country, org["org_id"], country,
                        _abuse.get_engine().store)
        except Exception:
            pass  # best-effort — geo telemetry never breaks the request
        # No .reset() needed for the ContextVars above: Starlette creates a
        # fresh asyncio task per request; ContextVars are copy-on-write per task
        # (verified by test_contextvar_not_leaked_to_next_request).
        #
        # #9192 — THE PRODUCTION CALLER of the record's tenant scope, and the
        # reason it lives HERE: this dispatch is the ONE place every
        # authenticated MCP request resolves its `(org_id, graph_id)` (the C5
        # ContextVars set just above), so a write that runs anywhere under
        # `call_next` is filed under the REQUEST's tenant — the request task, a
        # task spawned from it (Starlette runs the downstream app that way), or
        # an offload that COPIES the context (`asyncio.to_thread`, the repo's
        # `_submit_off_loop`). ⚠️ A bare `ThreadPoolExecutor.submit` does NOT
        # copy it (cpython#78195), so a lane that submits without
        # `copy_context()` must install the scope in the worker instead.
        # Installing it at a tool would be forgotten by the next tool;
        # installing it at the write door would be too late (the door knows the
        # graph, not the control-plane ids).
        #
        # It is a SCOPE, not a bare set: a `with` cannot leak this request's
        # tenant into the next request served on the same connection, and it
        # nests. It is installed for a CONTROL-PLANE resolution only
        # (`is_supabase_enabled()` — the same predicate that chose the resolver
        # two blocks up, so a cache hit is covered too): the record's layer
        # tables FK to `public.graphs`, and the self-host registry lane's
        # `g_<hex>` Falkor ids have no row there, so installing one would
        # displace a declared single-tenant pair with an id the FK cannot
        # satisfy (`write_failed`, cause unattributable).
        #
        # An ORG-WIDE **API KEY** (`tt_`/`tk_`, `graph_id` None) IS named on
        # this lane since #10025: `resolve_api_key` puts the org's active
        # `kind='default'` row id on the credential as `default_graph_id`, and
        # `record_tenant_for` files the write under it — the same answer the
        # hosted REST lane gives for that credential. Since #10046 the OAuth
        # family (`oat_`, introspected by
        # `tortoise.oauth.resolve_oauth_access_token`) and the hosted
        # SESSION/JWT credential (`hosted_api._session_user_org`) answer the
        # same way: both stamp `default_graph_id` from
        # `supabase_control.default_graph_id_for_credential`, so this lane
        # carries whatever the resolver handed it and still guesses nothing.
        #
        # It also stays REFUSED and counted (`no_tenant`) whenever the control
        # plane could not name that row (unset, tombstoned, unreadable), which
        # is the fail-closed direction. Guessing it here is exactly the
        # cross-tenant write #9192 exists to stop.
        from tortoise.supabase_control import is_supabase_enabled

        tenant_scope = _record_tenant_scope(
            org, control_plane_ids=is_supabase_enabled())
        with tenant_scope:
            return await call_next(request)


class TransportModeMiddleware(BaseHTTPMiddleware):
    """Self-host transport init (auth_mode="static" | "none", #338).

    OrgResolutionMiddleware sets these ContextVars for tenant mode; selfhost
    modes have no tenant resolution, so this middleware initializes them:
    _transport_mode="http" (passes _safe()'s fail-closed gate — auth was
    enforced at transport: static key check or localhost-bound none mode) and
    _current_org_id="selfhost" (isolated org_selfhost graph namespace).

    #1987 Task 8 (P1-2/P1-4): ALSO sets the ``_selfhost_transport`` ContextVar
    (tortoise/transport.py) — the transport-keyed metering/budget exemption
    channel. The selfhost HTTP MCP transport is the ONLY selfhost path whose
    org_id is truthy ("selfhost"), so it is the only one that NEEDS the
    flag: ``record_ask_usage`` and the ask budget helper no-op on it alongside
    ``not org_id`` (stdio org_id=None needs no flag), closing the
    phantom-record hole — the value "selfhost" is NEVER the exemption key (a
    hosted org with the raw id "selfhost" is legal and must record usage).
    """

    async def dispatch(self, request: Request, call_next):
        _transport_mode.set("http")
        _current_org_id.set(SELFHOST_ORG_ID)
        # C5: selfhost/static transports have no graph scope — clear any
        # prior tenant request's ContextVars (ContextVar defaults are
        # per-request here, but an explicit reset is cheap + future-proof).
        _current_graph_id.set(None)
        _current_graph_namespace.set(None)
        _current_scopes.set(None)
        _current_legacy_full_access.set(None)
        # #2600: selfhost/static transports have no tenant resolution → no
        # human actor (ContextVar default is None per request; explicit reset
        # mirrors the sibling ContextVars above).
        from tortoise.sdk import _current_actor_user_id
        _current_actor_user_id.set(None)
        from tortoise.transport import _selfhost_transport
        _selfhost_transport.set(True)
        try:
            return await call_next(request)
        finally:
            _selfhost_transport.set(False)


class ToolGroupMiddleware(BaseHTTPMiddleware):
    """Set the curation group ContextVar per request (#523).

    The tools/list transform is registered ONCE on the shared module-level mcp
    instance, so per-app group scoping must come from request context — not
    capture at app construction (which would let the first app win).
    """

    def __init__(self, app, *, tool_group: str | None):
        super().__init__(app)
        self._tool_group = tool_group

    async def dispatch(self, request: Request, call_next):
        _tool_group.set(self._tool_group)
        return await call_next(request)


class StaticKeyMiddleware(BaseHTTPMiddleware):
    """Static API-key auth for single-tenant self-host (auth_mode="static").

    Validates a single configured key (TORTOISE_API_KEY) with constant-time
    compare. Fail-closed: if api_key is None (misconfiguration), all POSTs
    are rejected 503 — never allow unauthenticated writes.
    """

    def __init__(self, app, *, api_key: str | None):
        super().__init__(app)
        self._api_key = api_key

    async def dispatch(self, request: Request, call_next):
        # GET metadata route + DELETE (stateless no-op) skip auth, matching
        # OrgResolutionMiddleware behavior.
        if request.method != "POST":
            return await call_next(request)
        if self._api_key is None:
            return _jsonrpc_error(
                -32099,
                "Static auth misconfigured: no API key set.",
                status=503,
            )
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return _jsonrpc_error(
                ERR_UNAUTHORIZED,
                "Unauthorized: missing Bearer token.",
                status=401,
            )
        token = auth[7:]
        if not hmac.compare_digest(token.encode(), self._api_key.encode()):
            return _jsonrpc_error(
                ERR_UNAUTHORIZED,
                "Unauthorized: invalid API key.",
                status=401,
            )
        return await call_next(request)


class MCPRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-key token bucket for ALL POSTs to /mcp (D8). 429 JSON-RPC -32002.

    limit_get=True (parent app / #525): rate-limits GETs too — /v1/* endpoints
    accept the static key and would otherwise be an unthrottled brute-force
    surface. The /mcp sub-app keeps the default (GET = metadata/SSE only).

    #5518: the store is BOUNDED (owned-key cap + exactly ONE shared overflow
    bucket) and the hot path is O(1) in store size, via the SAME capacity
    policy the REST lanes use (``tortoise/rate_buckets`` — the neutral home
    this module can import without pulling in ``hosted_api``). This lane's key
    space is FULLY attacker-chosen (``key_id = auth[7:]`` accepts ANY
    ``Bearer <string>`` — no ``tt_``/``tk_`` prefix gate, unlike the hosted
    limiter), so the old arrangement was the defect, not a bound: a
    rotating-key flood pruned nothing (every bucket was charged inside the
    window), and the 60 s wholesale sweep was O(n) over that growing store.
    ``_bucket_route`` never inserts, so a 429 leaves NO bucket behind. The
    old read ``self._buckets[key_id]`` on a ``defaultdict`` inserted the bucket
    on the LOOKUP path (#1719's insertion-discipline defect) — but that is not
    a per-429 leak: a fresh key's bucket is empty, so it is under any
    ``max_per_minute >= 1`` and the pre-insert is always followed by a charge.
    Only the degenerate ``max_per_minute == 0`` config (the off switch is
    ``RATE_LIMIT_DISABLED=1``) could refuse a key the lookup had just created.
    The sweep is gone entirely; the deadline attribute with it.
    """

    #: Hard cap on OWNED (client-keyed) buckets. Mirrors
    #: ``hosted_api.RateLimitMiddleware.MAX_BUCKETS`` — the two lanes must not
    #: drift; ``len(store) <= MAX_BUCKETS + 1`` (the shared overflow).
    MAX_BUCKETS = 10_000

    def __init__(self, app, max_per_minute: int = 100, limit_get: bool = False,
                 paths_prefix: tuple[str, ...] = (),
                 max_buckets: int | None = None):
        super().__init__(app)
        self.max_per_minute = max_per_minute
        self._limit_get = limit_get
        self._paths_prefix = paths_prefix
        self.max_buckets = (
            self.MAX_BUCKETS if max_buckets is None else max_buckets)
        # A PLAIN dict, never a ``defaultdict``: `_bucket_route` resolves a
        # fresh key WITHOUT inserting (#1719), and the write-back below is the
        # only charge path — so a 429 leaves no entry behind.
        self._buckets: dict[str, list] = {}
        self._lock = asyncio.Lock()
        self._disabled = os.environ.get("RATE_LIMIT_DISABLED") == "1"

    async def dispatch(self, request: Request, call_next):
        if self._disabled:
            return await call_next(request)
        if self._paths_prefix and not any(request.url.path.startswith(p) for p in self._paths_prefix):
            return await call_next(request)  # scope to /v1 (code-review P2, #525)
        if request.method != "POST" and not self._limit_get:
            return await call_next(request)  # GET metadata not rate-limited (unless limit_get)
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            key_id = auth[7:]
        else:
            # IP fallback; guard None client
            ip = request.client.host if request.client and request.client.host else "unknown"
            key_id = f"ip:{ip}"
        now = time.time()
        async with self._lock:
            # #5518: bounded key space + O(1) hot path, via the SAME
            # `_bucket_route` policy as the REST lanes. Inactive
            # insertion-order-head buckets are reclaimed ONLY on the new-key
            # path (a tracked key is pruned in place and never scans the
            # store); a new key at a full store is charged to the one shared
            # overflow bucket (capped at this lane's limit) instead of growing
            # the store. Replaces the old 60 s wholesale O(n) prune.
            bucket, on_overflow = _bucket_route(
                self._buckets, key_id, now, 60, self.max_buckets)
            if len(bucket) >= self.max_per_minute:
                resp = _jsonrpc_error(ERR_RATE_LIMIT, "Rate limit exceeded",
                                      {"retry_after": 30}, status=429)
                resp.headers["Retry-After"] = "30"
                return resp
            bucket.append((now, key_id) if on_overflow else now)
            dest = _BUCKET_OVERFLOW_KEY if on_overflow else key_id
            self._buckets[dest] = bucket
            _bucket_touch(self._buckets, dest)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """HSTS + X-Content-Type-Options + X-Frame-Options on /mcp responses.

    Parent FastAPI middleware does NOT propagate to mounted sub-apps, so the
    MCP stack needs its own security headers.
    """

    async def dispatch(self, request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("Strict-Transport-Security",
                                "max-age=31536000; includeSubDomains")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        return resp


class RequestBodySizeMiddleware(BaseHTTPMiddleware):
    """Cap request bodies at 1 MB (D13) — INCLUDING chunked bodies (#2048).

    The parent FastAPI app's middleware does NOT propagate into this mounted
    sub-app's stack, so the cap lives here. The original implementation checked
    the ``content-length`` HEADER only: a chunked ``Transfer-Encoding`` body
    (RFC 7230 §3.3.3 — it overrides Content-Length; HTTP/2 has no CL framing)
    carried no header and bypassed the check entirely. This middleware streams
    the body under the same 1 MB cap instead.

    REPLAY MECHANISM. Assigning ``request._body`` after the capped read hands
    the cached bytes downstream via Starlette's ``_CachedRequest`` — the SAME
    path ``await request.body()`` uses. A raw-ASGI receive wrapper is brittle
    here: a downstream ``BaseHTTPMiddleware`` whose app has consumed the
    request calls ``receive`` again on the response path expecting
    ``http.disconnect``, and a synthetic ``http.request`` there raises
    ``RuntimeError: Unexpected message received`` (it broke the SSE response
    path). ``_body`` is the private field ``Request.body()`` itself populates.

    POST-only, preserving the original scope: the Streamable-HTTP JSON-RPC
    carrier is POST; GET (SSE listener / metadata) and DELETE carry no body.
    """

    MAX_BODY = 1_000_000
    MAX_BODY_DETAIL = "Request body too large (max 1MB)"

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST":
            try:
                request._body = await read_capped_body(
                    request, self.MAX_BODY, self.MAX_BODY_DETAIL)
            except BodyTooLargeError:
                return _jsonrpc_error(
                    -32600, self.MAX_BODY_DETAIL, status=413)
        return await call_next(request)
