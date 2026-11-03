"""Tortoise MCP client (#338 T2.1).

Thin wrapper over fastmcp's built-in client (StreamableHttpTransport) — zero new
third-party deps. Connects to a Tortoise daemon (self-host
http://localhost:8000/mcp or hosted endpoint) and calls the registry tool surface
(tool_registry #510, single source of truth).

Sync wrappers over fastmcp's async API via asyncio.run() — script callers
(e.g. the minutes bridge) stay synchronous.

Graceful degradation: availability/status never raise — a down daemon
reports "tortoise_unavailable" so script callers skip cleanly.

⛔ THE BEARER CREDENTIAL IS RESOLVED PER REQUEST (#10605), never at import time
and never once per client. A credential captured at construction or start-up is
sent forever: a long-lived process keeps replaying the value it happened to hold
when it was built, so a rotated, refreshed, or later-added key never reaches the
wire — and the failure looks intermittent because it only reproduces on sessions
old enough. `_BearerProvider.auth_flow()` reads the live environment and the
stored credential file on EVERY request, which is the shape the MCP SDKs converge
on (a `token()`/`auth_flow` provider callback, not a captured string).

WHICH LEG IS ACTUALLY LIVE, stated rather than implied: re-reading the
environment helps a process that sets its own `os.environ` after import (a
notebook, a CLI that logs in mid-run); it cannot observe a value exported in some
other shell, and a CHILD process (the bridge) inherits a snapshot. The
genuinely rotatable source is therefore the stored credential file, which is why
both legs exist and why the file leg is what a long-running daemon rotates.

A BLANK credential is "no credential", never an empty header: the pre-fix
`BearerAuth(_api_key())` turned a whitespace value into `Authorization: Bearer `,
which a server can only reject as "invalid or missing Bearer token" — a message
that points the reader at the SERVER's auth policy when the fault is the
CLIENT's empty value. Now a blank value sends no header at all, and if the
endpoint then rejects the call as unauthenticated, the client raises
`MCPKeyEmptyError` (code `MCP-KEY-EMPTY`) naming every source it read. That is
the difference between "your key is wrong" and "your client has no key".

Seeds #526 (thin driver package — this client is the future pip surface).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from tortoise.status_vocabulary import STATUS_UNCONFIGURED

_logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8000/mcp"

#: The driver's own word for "the endpoint could not be reached at all". Kept as
#: a literal (not a `status_vocabulary` term): it is the pre-#3805 word the
#: driver has always emitted, and `status_vocabulary.resolve(word,
#: configured=…)` is what translates it. Only the credential-less REFUSAL is
#: reported as the recorded `unconfigured` term — a set-up gap, not an outage.
_UNAVAILABLE = "tortoise_unavailable"

#: Env names a credential may arrive through, in precedence order.
#:
#: `TORTOISE_API_KEY` is canonical (#1708 D1). `TORTOISE_MCP_API_KEY` is the
#: fleet-environment alias recorded on #5200, where the mismatch between the two
#: names made a `.mcp.json` header expand to an EMPTY bearer **while a valid key
#: was present** — the same empty-Bearer class as #10605, reached by a different
#: route. Reading both here means a box that exports either one works, instead
#: of reproducing that trap in the client.
_ENV_KEY_NAMES: tuple[str, ...] = ("TORTOISE_API_KEY", "TORTOISE_MCP_API_KEY")

#: Substrings that identify a server-side auth rejection. Deliberately broad:
#: fastmcp surfaces a 401 as `httpx.HTTPStatusError` in some paths and as a
#: `RuntimeError("Client failed to connect: … 401 …")` in others, and the
#: consequence of missing one is exactly the misleading message this module
#: exists to remove.
_AUTH_REJECTION_RE = re.compile(r"\b40[13]\b|unauthorized|invalid or missing bearer", re.IGNORECASE)

REFUSAL_CODE = "MCP-KEY-EMPTY"

#: the port a URL means when it names none, per scheme (`_same_endpoint`).
_DEFAULT_PORTS = {"http": 80, "https": 443}


def redact(text: str, key: str | None) -> str:
    """Remove a credential VALUE from text that came from OUTSIDE this process.

    A server is free to echo what it rejected (`key tt_… is invalid`), and this
    module quotes server text into refusals the harness logs. Quoting is right —
    it is the evidence — but the VALUE must never travel with it: the refusal is
    not a place a credential may leak into a transcript, a CI log or a file.
    """
    if not key:
        return text
    return text.replace(key, "<redacted>")

#: credential stores already warned about (#5109) — one line per process.
_WARNED_READABLE_STORES: set[str] = set()

#: one-off operator warnings already emitted, keyed by topic — never repeated.
_WARNED_TOPICS: set[str] = set()


def _warn_once(topic: str, message: str) -> None:
    """Write a one-off operator warning to stderr (never raises, never repeats)."""
    if topic in _WARNED_TOPICS:
        return
    _WARNED_TOPICS.add(topic)
    print(f"[tortoise-mcp] {message}", file=sys.stderr)


class MCPCredentialHostMismatchError(RuntimeError):
    """A credential exists, but it belongs to a DIFFERENT host than this client's.

    Raised instead of putting the header on the wire. Co-source (#2369 D1.1) ties
    a stored credential to its endpoint, and a client object fixes its endpoint at
    construction; when the two disagree the credential does not apply to THIS
    request, and sending it would hand it to a host chosen by whatever set the
    environment. Callers branch on `.code`
    (`"MCP-CREDENTIAL-HOST-MISMATCH"`).
    """

    code = "MCP-CREDENTIAL-HOST-MISMATCH"


class MCPKeyEmptyError(RuntimeError):
    """No MCP credential resolved, and the endpoint refused the request.

    Raised INSTEAD of forwarding the server's `Unauthorized: invalid or missing
    Bearer token`, which is accurate and useless: it blames the credential's
    VALUE when the client never had one to send. Callers branch on `.code`
    (`"MCP-KEY-EMPTY"`); the message names every source that was read.
    """

    code = REFUSAL_CODE


@dataclass(frozen=True)
class Connection:
    """The endpoint and the credential, resolved as ONE identity.

    `url` and `key` are resolved TOGETHER on purpose (#2369 D1.1 co-source): a
    stored key's endpoint comes from the same file (or the built-in default), so
    a repo- or environment-supplied URL can never redirect a stored credential to
    a host of its choosing. `sources` names every source that was read, in order,
    so a refusal can be specific instead of saying "missing".
    """

    url: str
    key: str | None
    source: str | None
    sources: tuple[str, ...]


def _credential_path() -> Path:
    """The stored credential the onboarding CLI writes (#1708 D4).

    A PATH FUNCTION, never a module constant: a `Path.home()` captured at import
    is the same capture-at-start bug this module exists to remove (#10605).
    """
    return Path.home() / ".tortoise" / "credentials.json"


def _warn_if_store_is_readable(path: Path) -> None:
    """Warn once per process when a credential store is group/other-readable.

    The recorded rule (#5109, `tortoise/__main__.py::_warn_if_credential_store_
    is_readable`) is that EVERY reader of a credential store carries this check,
    because one stderr line is the only signal a drifted mode ever produces. This
    reader runs on the highest-frequency path in the tree, so the check is
    cheap — a `stat` — and it never prints the value.
    """
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if not mode & 0o077:  # no group/other bits at all → nothing to say
        return
    label = str(path)
    if label in _WARNED_READABLE_STORES:
        return
    _WARNED_READABLE_STORES.add(label)
    print(f"⚠️  {path} is mode {mode:04o} — group/other can read the MCP "
          f"credential stored in it. Fix: chmod 600 {path}", file=sys.stderr)


def resolve_connection() -> Connection:
    """Resolve the endpoint + credential NOW.

    Precedence: env (`TORTOISE_API_KEY`, then the `TORTOISE_MCP_API_KEY` alias)
    then the stored `~/.tortoise/credentials.json` — the same env-and-store shape
    `tortoise/__main__.py::_resolve_config_path` uses (#1708 D1/D4/D5), reproduced
    here rather than imported because this module ships in the client-only wheel
    (it cannot depend on the engine's `__main__`).

    A blank/whitespace value is treated as ABSENT (never as an empty credential).
    A store that exists but cannot be parsed or read is NOT reported as absent
    (#1708 D6): its label in `sources` carries the failure, so the refusal names
    the real state instead of telling the operator to set a key that is there.

    CO-SOURCE (#2369 D1.1): when the FILE supplies the key, the FILE decides the
    host — its `api_url` plus `/mcp/`, or the BUILT-IN DEFAULT when it records
    none. A `TORTOISE_MCP_URL` from the environment is honoured only by an
    env-sourced credential, so a committed harness config cannot aim a stored key
    at a host of its choosing.
    """
    sources: list[str] = []
    env_url = os.environ.get("TORTOISE_MCP_URL") or None
    for name in _ENV_KEY_NAMES:
        sources.append(name)
        value = os.environ.get(name)
        if value and value.strip():
            return Connection(env_url or _DEFAULT_URL, value.strip(), name, tuple(sources))

    path = _credential_path()
    label = str(path)
    sources.append(label)
    try:
        config = json.loads(path.read_text())
    except FileNotFoundError:
        return Connection(env_url or _DEFAULT_URL, None, None, tuple(sources))
    except (OSError, ValueError) as exc:
        sources[-1] = f"{label} ({type(exc).__name__}: {exc})"
        return Connection(env_url or _DEFAULT_URL, None, None, tuple(sources))

    key = config.get("api_key") if isinstance(config, dict) else None
    if not (isinstance(key, str) and key.strip()):
        return Connection(env_url or _DEFAULT_URL, None, None, tuple(sources))

    _warn_if_store_is_readable(path)
    api_url = config.get("api_url")
    if isinstance(api_url, str) and api_url.strip():
        url = api_url.strip().rstrip("/") + "/mcp/"
    else:
        # #2369 D1.1, strictly: the store's own identity, or the BUILT-IN DEFAULT —
        # never the environment. A harness config is committable, so a
        # `TORTOISE_MCP_URL` it carries must not be able to aim a stored
        # credential at a host of its choosing; a store that records no api_url
        # therefore resolves to the default, not to whatever the env says. (An
        # operator who does want a stored key at a non-default host records that
        # host IN the store — `tortoise init` writes it.)
        url = _DEFAULT_URL
    return Connection(url, key.strip(), label, tuple(sources))


def mcp_url() -> str:
    """The endpoint the next request will use."""
    return resolve_connection().url


def _refusal(exc: BaseException, sent: Connection) -> MCPKeyEmptyError:
    return MCPKeyEmptyError(
        f"{REFUSAL_CODE}: the MCP endpoint rejected this request as "
        f"unauthenticated and NO credential resolved on the client (checked "
        f"{', '.join(sent.sources)}). The credential is read on EVERY request, so "
        f"setting it now takes effect on the next call — no client rebuild and "
        f"no session restart. Server said: {redact(str(exc), sent.key)}"
    )


def _is_tool_error(exc: BaseException) -> bool:
    """True for a failed TOOL RESULT — the tool's error, not the client's auth."""
    try:
        from fastmcp.exceptions import ToolError
    except ImportError:  # pragma: no cover - fastmcp absent: nothing can be one
        return False
    return isinstance(exc, ToolError)


def _raise_if_credential_was_missing(exc: BaseException, sent: Connection | None) -> None:
    """Translate an auth rejection into the named refusal when NO key was sent.

    Classified from what the FAILED REQUEST actually carried (`sent`), never from
    a fresh resolution: a key that was rotated away between the send and the
    failure must not make us claim the request had none, and a key that appeared
    during the round trip must not make us relay the server's message unchanged.
    """
    if not _AUTH_REJECTION_RE.search(str(exc)):
        return
    if _is_tool_error(exc):
        # A TOOL's error is not evidence about the client's credential: fastmcp
        # raises `ToolError` carrying the tool's own text, which may legitimately
        # mention `401`/`Unauthorized` for an upstream provider. Rewriting that
        # into "your client has no key" would misdirect exactly as badly as the
        # server message this module removes.
        return
    if sent is None:
        # The request never reached the point of attaching a credential (a
        # connect failure), so the live resolution is the only evidence there is.
        sent = resolve_connection()
    elif sent.key is not None:
        return  # a credential WAS sent: the value is the problem, not its absence
    raise _refusal(exc, sent) from exc


class _BearerProvider(httpx.Auth):
    """An httpx auth provider that resolves the credential on EVERY request.

    fastmcp's `BearerAuth(token: str)` captures the string once — that is the
    captured-credential shape #10605 measures. This provider is attached
    UNCONDITIONALLY (also when nothing resolves at build time), so a key that
    appears later is used by the very next request: "with auth or without" is
    never decided once.

    `sent` is the provenance of the last request, which is what a refusal is
    classified from. `withheld` records a CO-SOURCE conflict: a client's endpoint
    is fixed at construction, so a credential that now resolves to a DIFFERENT
    host is not this request's credential — it is withheld (never handed to a
    host the environment chose) and reported by name on the failure path.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.sent: Connection | None = None
        self.withheld: Connection | None = None

    def auth_flow(self, request):
        connection = resolve_connection()
        self.sent = connection
        self.withheld = None
        if connection.key and not _same_endpoint(connection.url, self.url):
            self.withheld = connection
        elif connection.key:  # a blank resolution is absent, never `Bearer ` + nothing
            request.headers["Authorization"] = f"Bearer {connection.key}"
        yield request


def _same_endpoint(a: str, b: str) -> bool:
    """True when two endpoint strings name the same URL, ignoring cosmetics.

    A store records a BASE (`api_url`) and `resolve_connection` appends `/mcp/`,
    while an env URL and the built-in default carry `/mcp`. Raw string equality
    therefore read the SAME endpoint as two different hosts whenever a store
    credential met a client built from the env — and withheld the credential
    with a message that blamed the wrong thing.

    The same reasoning covers an EXPLICIT DEFAULT PORT (`http://h:80/mcp` and
    `http://h/mcp` are one endpoint) and host case / IPv6 brackets. It does NOT
    loosen the security property: scheme, host and path must all still match, so
    no spelling can make a credential for one host look like another's.
    """
    def parts(url: str) -> tuple[str, str, int | None, str]:
        split = urlsplit(url)
        scheme = split.scheme.lower()
        try:
            port = split.port
        except ValueError:  # malformed port: compare it as text, never crash
            port = None
        return (
            scheme,
            (split.hostname or "").lower(),
            port if port is not None else _DEFAULT_PORTS.get(scheme),
            split.path.rstrip("/"),
        )

    return parts(a) == parts(b)


def _store_base(client_url: str) -> str:
    """The `api_url` a store must record for `client_url` to resolve to it.

    `resolve_connection` builds a store endpoint as `api_url + "/mcp/"`, so
    naming the endpoint itself as the value produces `/mcp/mcp/`. The base is
    what the message must tell the operator to record.
    """
    base = client_url.rstrip("/")
    return base[: -len("/mcp")] if base.endswith("/mcp") else base


def _mismatch(connection: Connection, client_url: str) -> MCPCredentialHostMismatchError:
    return MCPCredentialHostMismatchError(
        f"MCP-CREDENTIAL-HOST-MISMATCH: the credential from {connection.source} belongs "
        f"to {connection.url}, but this client talks to {client_url} (#2369 D1.1). The "
        f"credential was NOT sent — an environment-supplied URL must never be able to "
        f"aim a stored credential at a host of its choosing. Fix the source: record "
        f"{_store_base(client_url)} as `api_url` in the store, use the env key for this "
        f"host, or build the client for {connection.url}."
    )


def _classify_failure(exc: BaseException, provider: _BearerProvider) -> None:
    """Raise the named refusal this failure actually is (no-op otherwise).

    A withheld credential explains an AUTH REJECTION (the request went out bare
    because the credential belonged elsewhere). It does not explain a transport
    fault: a daemon that is down is an outage whatever the credential situation,
    and reporting it as a co-source conflict would move an outage into the
    set-up-gap bucket (#3805) exactly as the server message this module removes
    moves a set-up gap into the credential-value bucket.
    """
    if provider.withheld is not None and _AUTH_REJECTION_RE.search(str(exc)):
        if not _is_tool_error(exc):
            raise _mismatch(provider.withheld, provider.url) from exc
        return
    _raise_if_credential_was_missing(exc, provider.sent)


def _client(provider: _BearerProvider):
    """A fastmcp Client wired to the resolved endpoint and the live provider.

    The client is wrapped (`_GuardedClient`) so the NAMED-refusal contract holds
    on the PUBLIC constructor too, not only inside this module's helpers: a
    consumer that builds its own client with `get_client()` and drives it in an
    `async with` gets `MCP-KEY-EMPTY` / `MCP-CREDENTIAL-HOST-MISMATCH`, never the
    library's raw transport error.

    Lazy imports keep the module importable when fastmcp is unavailable
    (graceful degradation for scripts).
    """
    from fastmcp.client import Client
    from fastmcp.client.transports.http import StreamableHttpTransport

    transport = StreamableHttpTransport(url=provider.url)
    raw = Client(
        transport,
        auth=provider,
        client_info={"name": "tortoise-mcp-client", "version": "0.1.0"},
        auto_initialize=True,
    )
    return _GuardedClient(raw, provider)


class _GuardedClient:
    """A fastmcp Client whose failures come back as this module's named refusals.

    Classification happens on the way OUT of the session (where the exception is
    known), and so does the withheld-credential warning for a call that
    SUCCEEDED — the two states a caller cannot otherwise tell apart.
    """

    def __init__(self, client, provider: _BearerProvider) -> None:
        self._client = client
        self._provider = provider

    def __getattr__(self, name):
        return getattr(self._client, name)

    async def __aenter__(self):
        # `auto_initialize=True` means the FIRST request happens HERE, so this is
        # where a rejected credential surfaces — not in `__aexit__`.
        try:
            await self._client.__aenter__()
        except BaseException as raised:
            # A failed enter has nothing to close; the refusal is what matters.
            with contextlib.suppress(BaseException):
                await self._client.__aexit__(type(raised), raised, raised.__traceback__)
            _classify_failure(raised, self._provider)
            raise
        return self

    async def __aexit__(self, exc_type, exc, tb):
        result = False
        if exc is None:
            try:
                result = await self._client.__aexit__(exc_type, exc, tb)
            except BaseException as raised:
                exc = raised
        else:
            # The library re-raises the body's error here; whichever instance
            # arrives, it is the one that must be classified.
            try:
                await self._client.__aexit__(exc_type, exc, tb)
            except BaseException as raised:
                exc = raised
        if exc is not None:
            _classify_failure(exc, self._provider)
        else:
            _announce_withheld(self._provider)
        return result


def _url(provider: _BearerProvider) -> str:
    """The endpoint actually used by the last attempt (else the live one)."""
    return provider.sent.url if provider.sent else mcp_url()


def _run(coro):
    """Run an async fastmcp call from sync code (script-friendly)."""
    return asyncio.run(coro)


def _announce_withheld(provider: _BearerProvider) -> None:
    """Warn once when a call SUCCEEDED with a credential that was withheld.

    Nothing failed, so there is no refusal to raise — but the operator is running
    on a misconfiguration that bites the moment the endpoint requires a key. The
    message names the sources and both endpoints, never the credential VALUE.
    """
    if provider.withheld is None:
        return
    _warn_once(
        "withheld",
        f"MCP credential from {provider.withheld.source} was NOT sent: it belongs "
        f"to {provider.withheld.url}, and this client talks to {provider.url}. "
        f"This call succeeded unauthenticated; it will fail as soon as the "
        f"endpoint requires a key.",
    )


def _run_guarded(coro, provider: _BearerProvider):
    """`_run`, with a credential fault re-raised as its NAMED refusal."""
    try:
        result = _run(coro)
    except (MCPKeyEmptyError, MCPCredentialHostMismatchError):
        raise
    except Exception as exc:  # noqa: BLE001, RUF100
        _classify_failure(exc, provider)
        raise
    _announce_withheld(provider)
    return result


def get_client():
    """Build a fastmcp Client bound to the resolved endpoint, with live auth.

    The ENDPOINT is fixed for this client's life (a transport takes a URL); the
    CREDENTIAL is not. When the two stop agreeing — the credential's co-sourced
    host is no longer this client's — the provider withholds it and the failure is
    reported as `MCPCredentialHostMismatchError` rather than sent.
    """
    return _client(_BearerProvider(resolve_connection().url))


def status() -> dict[str, Any]:
    """Connectivity + surface probe. Never raises.

    Returns {"status": "ok", "url": ..., "tools": N} when reachable,
    {"status": "unconfigured", "url": ..., "error": "MCP-KEY-EMPTY: …"} when the
    endpoint demands a credential the client does not have (the recorded
    `unconfigured` term — a set-up gap, not an outage, #3805), else
    {"status": "tortoise_unavailable", "url": ..., "error": ...}.
    """
    provider = _BearerProvider(resolve_connection().url)

    async def _probe() -> dict[str, Any]:
        async with _client(provider) as client:
            tools = await client.list_tools()
        return {"status": "ok", "url": _url(provider), "tools": len(tools)}

    try:
        return _run_guarded(_probe(), provider)
    except (MCPKeyEmptyError, MCPCredentialHostMismatchError) as exc:
        # Both are CONFIGURATION facts, not outages: report them under the recorded
        # `unconfigured` term so a caller's exit code (4 — a set-up gap) cannot
        # read as "the store is down" (3).
        _logger.debug("tortoise unconfigured: %s", exc)
        return {"status": STATUS_UNCONFIGURED, "url": _url(provider), "error": str(exc)}
    except Exception as exc:  # noqa: BLE001, RUF100
        _logger.debug("tortoise unavailable: %s", exc)
        return {"status": _UNAVAILABLE, "url": _url(provider), "error": str(exc)}


def available() -> bool:
    """True when the daemon is reachable (status probe)."""
    return status().get("status") == "ok"


def list_tools() -> list[str]:
    """Names of the tools exposed by the daemon (registry surface)."""
    provider = _BearerProvider(resolve_connection().url)

    async def _list() -> list[str]:
        async with _client(provider) as client:
            tools = await client.list_tools()
        return [t.name for t in tools]

    return _run_guarded(_list(), provider)


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call an MCP tool (e.g. tortoise_create_point) and return its result.

    Raises on transport/auth failure — callers that want graceful behavior
    should check `available()` first (or catch exceptions). An auth rejection
    with no credential resolved raises `MCPKeyEmptyError`.
    """
    provider = _BearerProvider(resolve_connection().url)

    async def _call() -> Any:
        async with _client(provider) as client:
            return await client.call_tool(name, arguments or {})

    return _run_guarded(_call(), provider)
