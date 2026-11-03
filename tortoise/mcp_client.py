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
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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

#: credential stores already warned about (#5109) — one line per process.
_WARNED_READABLE_STORES: set[str] = set()


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
        f"no session restart. Server said: {exc}"
    )


def _raise_if_credential_was_missing(exc: BaseException, sent: Connection | None) -> None:
    """Translate an auth rejection into the named refusal when NO key was sent.

    Classified from what the FAILED REQUEST actually carried (`sent`), never from
    a fresh resolution: a key that was rotated away between the send and the
    failure must not make us claim the request had none, and a key that appeared
    during the round trip must not make us relay the server's message unchanged.
    """
    if not _AUTH_REJECTION_RE.search(str(exc)):
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
    never decided once. `sent` is the provenance of the last request, which is
    what the refusal is classified from.
    """

    def __init__(self) -> None:
        self.sent: Connection | None = None

    def auth_flow(self, request):
        connection = resolve_connection()
        self.sent = connection
        if connection.key:  # a blank resolution is absent, never `Bearer ` + nothing
            request.headers["Authorization"] = f"Bearer {connection.key}"
        yield request


def _client(provider: _BearerProvider):
    """A fastmcp Client wired to the resolved endpoint and the live provider.

    Lazy imports keep the module importable when fastmcp is unavailable
    (graceful degradation for scripts).
    """
    from fastmcp.client import Client
    from fastmcp.client.transports.http import StreamableHttpTransport

    transport = StreamableHttpTransport(url=resolve_connection().url)
    return Client(
        transport,
        auth=provider,
        client_info={"name": "tortoise-mcp-client", "version": "0.1.0"},
        auto_initialize=True,
    )


def _url(provider: _BearerProvider) -> str:
    """The endpoint actually used by the last attempt (else the live one)."""
    return provider.sent.url if provider.sent else mcp_url()


def _run(coro):
    """Run an async fastmcp call from sync code (script-friendly)."""
    return asyncio.run(coro)


def _run_guarded(coro, provider: _BearerProvider):
    """`_run`, with a credential-less auth rejection re-raised as the refusal."""
    try:
        return _run(coro)
    except MCPKeyEmptyError:
        raise
    except Exception as exc:  # noqa: BLE001, RUF100
        _raise_if_credential_was_missing(exc, provider.sent)
        raise


def get_client():
    """Build a fastmcp Client bound to the resolved endpoint, with live auth."""
    return _client(_BearerProvider())


def status() -> dict[str, Any]:
    """Connectivity + surface probe. Never raises.

    Returns {"status": "ok", "url": ..., "tools": N} when reachable,
    {"status": "unconfigured", "url": ..., "error": "MCP-KEY-EMPTY: …"} when the
    endpoint demands a credential the client does not have (the recorded
    `unconfigured` term — a set-up gap, not an outage, #3805), else
    {"status": "tortoise_unavailable", "url": ..., "error": ...}.
    """
    provider = _BearerProvider()

    async def _probe() -> dict[str, Any]:
        async with _client(provider) as client:
            tools = await client.list_tools()
        return {"status": "ok", "url": _url(provider), "tools": len(tools)}

    try:
        return _run_guarded(_probe(), provider)
    except MCPKeyEmptyError as exc:
        # The refusal is a CONFIGURATION fact, not an outage: report it under the
        # recorded `unconfigured` term so a caller's exit code (4 — a set-up gap)
        # cannot read as "the store is down" (3).
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
    provider = _BearerProvider()

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
    provider = _BearerProvider()

    async def _call() -> Any:
        async with _client(provider) as client:
            return await client.call_tool(name, arguments or {})

    return _run_guarded(_call(), provider)
