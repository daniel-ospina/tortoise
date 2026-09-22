"""MCP HTTP transport auth, rate limit, security headers, SDK resolution (#236).

Serves as the auth/rate-limit boundary for the MCP Streamable HTTP endpoint
mounted at /mcp on the hosted FastAPI app. Imports ONLY tortoise.sdk +
starlette — mcp_server imports from here (one-directional; no circular import).

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
import hmac
import ipaddress
import os
import re
import time
from collections import OrderedDict, defaultdict
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

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


def _get_base_sdk() -> TortoiseSDK:
    """Lazy module-level SDK for stdio mode. Never touched in HTTP mode.

    Delegates to tortoise.mcp_server._get_sdk() (the #451 canonical lazy
    init) so there is exactly ONE source of truth for DB resolution. The
    function-level import avoids the mcp_server ↔ mcp_auth cycle.
    """
    from tortoise import mcp_server as _ms
    return _ms._get_sdk()


# ── Org-scoped SDK (D2) ────────────────────────────────────────────────────
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
    Org-wide keys / OAuth / selfhost → the default graph (unchanged)."""
    org_id = _current_org_id.get()
    if org_id is None:
        return _get_base_sdk()
    gid = _current_graph_id.get()
    ns = _current_graph_namespace.get()
    if gid and ns:
        return TortoiseSDK(graph_name=ns)
    return TortoiseSDK(namespace=org_id)


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
# MCP surface it is the code in the refused tool result's
# ``structuredContent.error`` (``mcp_server._await_under_mcp_wait_bound``): the
# MCP SDK converts tool-handler exceptions to ``CallToolResult(isError=True)``
# (``mcp/server/lowlevel/server.py::_make_error_result``), so once the SSE
# stream is open a raised ``McpError`` loses its code and ``data`` — the result
# is the only channel that can still carry the retry signal. Either way the
# advertised delay is ``error.data.retry_after`` (#3851's shape).
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


def _jsonrpc_error(code: int, message: str, data: dict | None = None,
                   status: int = 400,
                   headers: dict[str, str] | None = None) -> JSONResponse:
    """Build an MCP-compatible JSON-RPC error response with an HTTP status."""
    body: dict[str, Any] = {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
        "id": None,
    }
    if data is not None:
        body["error"]["data"] = data
    return JSONResponse(body, status_code=status, headers=headers)


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
                        org = resolve_oauth_access_token(
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
        # No .reset() needed: Starlette creates a fresh asyncio task per request;
        # ContextVars are copy-on-write per task (verified by
        # test_contextvar_not_leaked_to_next_request).
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
            return JSONResponse(
                {"jsonrpc": "2.0", "error": {"code": -32099, "message": "Static auth misconfigured: no API key set."}, "id": None},
                status_code=503,
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
    """

    def __init__(self, app, max_per_minute: int = 100, limit_get: bool = False,
                 paths_prefix: tuple[str, ...] = ()):
        super().__init__(app)
        self.max_per_minute = max_per_minute
        self._limit_get = limit_get
        self._paths_prefix = paths_prefix
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._lock = asyncio.Lock()
        self._last_cleanup = time.time()
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
            # Periodic cleanup: filter stale timestamps from ALL buckets, then
            # prune empty ones (code-review fix — mirrors hosted_api's
            # RateLimitMiddleware pattern; prevents one-off-IP bucket growth)
            if now - self._last_cleanup > 60:
                stale = []
                for k, v in list(self._buckets.items()):
                    v[:] = [t for t in v if now - t < 60]
                    if not v:
                        stale.append(k)
                for k in stale:
                    del self._buckets[k]
                self._last_cleanup = now
            bucket = self._buckets[key_id]
            bucket[:] = [t for t in bucket if now - t < 60]
            if len(bucket) >= self.max_per_minute:
                resp = _jsonrpc_error(ERR_RATE_LIMIT, "Rate limit exceeded",
                                      {"retry_after": 30}, status=429)
                resp.headers["Retry-After"] = "30"
                return resp
            bucket.append(now)
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
    """Reject POST bodies > 1MB (D13).

    Caveat: chunked transfer-encoding has NO content-length header — the
    header check misses it. Infrastructure layer (Fly/nginx client_max_body_size
    or uvicorn limit) is the primary defense; this middleware is best-effort.
    """

    MAX_BODY = 1_000_000

    async def dispatch(self, request: Request, call_next):
        if request.method == "POST":
            length = request.headers.get("content-length")
            if length and int(length) > self.MAX_BODY:
                return _jsonrpc_error(-32600, "Request body too large (max 1MB)",
                                      status=413)
        return await call_next(request)
