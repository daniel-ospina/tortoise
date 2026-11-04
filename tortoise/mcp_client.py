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
old enough. `_BearerProvider` reads the live environment and the stored
credential file on EVERY request — through `auth_flow` on a sync client and
`async_auth_flow` on an `AsyncClient` (the one fastmcp's client drives), both
funnelling into ONE `_resolve_and_stamp` — which is the shape the MCP SDKs
converge on (a `token()`/`auth_flow` provider callback, not a captured string).

⛔ THE TRANSMITTING POSTURE EXCLUDES THE PROJECT-LOCAL STORE (#2369 D1.2,
mirrored from `tortoise/__main__.py::_resolve_config_path(global_only=True)`).
`<cwd>/.tortoise` is COMMITTABLE, and this module is reached by a harness that
runs with cwd = the project (the stdio bridge): whoever controls the repository
would otherwise choose both the Bearer key AND the host it is sent to, and would
shadow whatever credential the user has stored. Measured on this module (#10605
review, P0): a cloned repo shipping
`./.tortoise = {"api_key": "tt_repo_controlled", "api_url": …}` made
`list_tools()`, `call_tool()` and `tortoise/mcp_bridge.py` all contact the
repo's own endpoint with the repo's own key. Every entry point that puts the
credential on the wire resolves `resolve_connection(global_only=True)`; the
project store is still read by non-transmitting callers (redaction) and can be
opted back in deliberately with `TORTOISE_MCP_ALLOW_PROJECT_STORE=1`.

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

from tortoise.env_truthy import is_truthy
from tortoise.status_vocabulary import STATUS_UNCONFIGURED

_logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8000/mcp"

#: The driver's own word for "the endpoint could not be reached at all". Kept as
#: a literal (not a `status_vocabulary` term): it is the pre-#3805 word the
#: driver has always emitted, and `status_vocabulary.resolve(word,
#: configured=…)` is what translates it. Only the credential-less REFUSAL is
#: reported as the recorded `unconfigured` term — a set-up gap, not an outage.
_UNAVAILABLE = "tortoise_unavailable"

#: The opt-in that lets a TRANSMITTING path read the project-local store.
#:
#: Off by default, and it has to be a deliberate act: the project-local store is
#: committable, so whoever controls the repository controls the key AND the host
#: an OPT-OUT would send them to (#2369 D1.2, mirrored from
#: `tortoise/__main__.py::_resolve_config_path(global_only=True)`). `tortoise init
#: --api-key` writes exactly that file, so a user who deliberately keeps their
#: credential in the project sets this to say so — rather than every cloned repo
#: silently inheriting the right to choose the endpoint.
#:
#: ⛔ Read it through `is_truthy`, NEVER by presence (`os.environ.get(X)` is
#: truthy for EVERY non-empty string, so `=0`/`=false`/`=off`/`=no` — the
#: spellings `tortoise/env_truthy.py` (#4097) declares as OFF — would switch the
#: P0 this module exists to close back ON, and the module would report itself as
#: disabled while doing it). This is a BOOLEAN opt-in, not a key/URI/path, so the
#: V5 presence shape that contract scopes out does not reach it.
_PROJECT_STORE_OPT_IN = "TORTOISE_MCP_ALLOW_PROJECT_STORE"

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

#: an SDK that REFUSES to follow a cross-origin redirect instead of following it
#: and letting httpx strip the header: `Redirect to <url> not followed; use that
#: URL as the endpoint if it is the intended server`. Two MCP SDK versions this
#: repo runs against (1.29 follows, 1.30 refuses) reach the SAME operator problem
#: by different routes, so both must classify to the same named refusal —
#: otherwise a version bump silently turns a set-up gap back into an outage.
#:
#: Anchor on the DISCRIMINATOR (`; use that URL as the endpoint`), never on the
#: shared prefix: the same SDK function has a second refusal, `not followed: it
#: would downgrade this HTTPS endpoint to plain HTTP`, whose own remedy is the
#: OPPOSITE one ("use https, or fix the proxy settings"). A prefix-only match
#: swallowed it and told the operator to send the bearer over cleartext.
_REDIRECT_REFUSED_RE = re.compile(
    r"redirect to (\S+?) not followed; use that URL as the endpoint",
    re.IGNORECASE,
)

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


def _redact_known(text: str, sent: Connection | None = None) -> str:
    """Redact every credential VALUE this process knows, not just the one sent.

    A refusal is built from SERVER text, and the server echoes whatever it was
    given — but the credential that answers a request is not always the one a
    caller holds in hand: a request can go out bare (a pure keyless endpoint, or a
    withheld co-source credential) while THIS process still knows a value from the
    environment, the store, or a previous request. Redacting only `sent.key` left
    a server-echoed value printed verbatim on exactly those paths.
    """
    keys: list[str] = []
    for connection in (sent, _LAST_SENT[0]):
        if connection is not None and connection.key:
            keys.append(connection.key)
    with contextlib.suppress(Exception):
        # A store read can fail (absent, unreadable, corrupt); that must not turn a
        # refusal into a different exception on the failure path.
        live = resolve_connection().key
        if live:
            keys.append(live)
    for key in dict.fromkeys(keys):
        text = redact(text, key)
    return text

#: credential stores already warned about (#5109) — one line per process.
_WARNED_READABLE_STORES: set[str] = set()

#: one-off operator warnings already emitted, keyed by topic — never repeated.
_WARNED_TOPICS: set[str] = set()

#: provenance of the most recent request this process made. Used ONLY as a
#: redaction fallback by `describe_failure`: a caller that prints an error after
#: a rotation must still strip the key that was actually on the wire. An unlocked
#: last-write-wins slot is deliberate — redaction is the one consumer, and a lost
#: update can only make it strip a key that was not this request's (a superset of
#: `<redacted>`s), never leak one. It is NEVER consulted for classification.
_LAST_SENT: list[Connection | None] = [None]

#: The httpx `Request.extensions` key carrying `(Connection, withheld)` for ONE
#: request. This is how a failure is classified from what ITS OWN request
#: resolved: one `_BearerProvider` is shared by every request on a client, so its
#: `sent`/`withheld` attributes describe whichever request resolved last.
_PROVENANCE_EXT = "tortoise_mcp_connection"


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


class MCPCredentialNotCarriedError(RuntimeError):
    """A credential resolved, but the request that failed carried NONE.

    httpx strips `Authorization` when a redirect changes the origin, and fastmcp
    follows redirects (the `/mcp` → `/mcp/` hop needs it — #2864). So a 401 from
    the redirect TARGET answers a request that never had the header, and reporting
    it as a value fault sends the operator to rotate a key that was never on the
    wire — the #10605 misdirection, mirrored. An SDK that refuses the redirect
    outright (#10605's CI measured mcp 1.30 doing that where 1.29 followed) lands
    here too, with one deliberate difference in the message: there the credential
    DID reach the first endpoint and what would go bare is the declined hop, so
    the refusal says that instead of claiming nothing was ever sent. Callers
    branch on `.code` (`"MCP-CREDENTIAL-NOT-CARRIED"`); `status()` maps it to
    `unconfigured`, because a redirect hop is a set-up fact, not an outage.
    """

    code = "MCP-CREDENTIAL-NOT-CARRIED"


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


def _credential_paths(*, global_only: bool = False) -> tuple[Path, ...]:
    """The credential stores to READ, in the recorded precedence (#1708 D4/D5).

    PATH FUNCTIONS, never module constants: a `Path.home()` or `Path.cwd()`
    captured at import is the same capture-at-start bug this module exists to
    remove (#10605).

    BOTH stores the onboarding CLI writes are read, project-local first:
    `tortoise init --api-key` saves `./.tortoise` (`_cmd_init`), and the #1708
    global store is `~/.tortoise/credentials.json`. Reading only the global one
    made the client blind to the store the hosted onboarding actually creates —
    no credential, the default endpoint, and a `MCP-KEY-EMPTY` naming stores the
    user never wrote.

    `global_only=True` is the TRANSMITTING posture (#2369 D1.2), and it omits
    the project-local candidate. A `./.tortoise` is COMMITTABLE, so whoever
    controls the repository controls both the key AND the endpoint a request
    goes to: MEASURED on this module (#10605 review, P0), a cloned repo shipping
    `./.tortoise = {"api_key": "tt_repo_controlled", "api_url": "http://…"}`
    made `resolve_connection()` return THAT endpoint, and `list_tools()`,
    `call_tool()` and `tortoise/mcp_bridge.py` (which a harness launches with
    cwd = the project) all contacted it — the repo choosing both halves of the
    identity, and additionally SHADOWING the user's own stored credential. This
    mirrors `_resolve_config_path(global_only=True)`, the posture the engine's
    transmitting surfaces already carry. A NON-transmitting read (redaction)
    still wants every store, which is why the default is unchanged.

    The escape hatch is `TORTOISE_MCP_ALLOW_PROJECT_STORE=1`: a user who
    deliberately keeps their credential in the project (the file
    `tortoise init --api-key` writes) says so once, instead of a clone getting
    that say silently. It is decided by the VALUE through `is_truthy` (#4097),
    so the declared OFF spellings (`=0`, `=false`, `=no`, `=off`) are no-ops —
    a presence read would have let a value that READS as "disabled" enable it.
    """
    global_store = Path.home() / ".tortoise" / "credentials.json"
    if global_only and not is_truthy(os.environ.get(_PROJECT_STORE_OPT_IN)):
        return (global_store,)
    return (Path.cwd() / ".tortoise", global_store)


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


def resolve_connection(*, global_only: bool = False) -> Connection:
    """Resolve the endpoint + credential NOW.

    Precedence: env (`TORTOISE_API_KEY`, then the `TORTOISE_MCP_API_KEY` alias)
    then the stored `~/.tortoise/credentials.json` — the same env-and-store shape
    `tortoise/__main__.py::_resolve_config_path` uses (#1708 D1/D4/D5), reproduced
    here rather than imported because this module ships in the client-only wheel
    (it cannot depend on the engine's `__main__`).

    `global_only=True` (the #2369 D1.2 TRANSMITTING posture, mirrored from
    `_resolve_config_path(global_only=True)`) SKIPS the project-local
    `<cwd>/.tortoise` candidate: every entry point that puts the credential on
    the wire passes it, because a repo-committed store must never choose the
    host a Bearer key is sent to. The skipped store is still NAMED in `sources`,
    so a refusal tells the operator the file was there and why it was not read,
    instead of claiming they never wrote one.

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

    read_paths = _credential_paths(global_only=global_only)
    for path in _credential_paths():
        label = str(path)
        if path not in read_paths:
            # NAMED, never silent: an operator whose key IS in `./.tortoise` must
            # not read "no credential anywhere" and go hunting — the refusal has
            # to say the file exists, was deliberately not read on a transmitting
            # path, and which switch says otherwise.
            sources.append(
                f"{label} (SKIPPED: not read on a transmitting path — a committed "
                f"store may not choose the host a credential is sent to; set "
                f"{_PROJECT_STORE_OPT_IN}=1 to read it deliberately)"
            )
            continue
        sources.append(label)
        if not path.is_file():
            # Absent, or a DIRECTORY at the project-local path (a repo's own
            # `./.tortoise` state dir) — "no config here", so the next candidate
            # wins (#1708 D5). Reading it would raise IsADirectoryError and be
            # reported as a CORRUPT store.
            continue
        try:
            config = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            # Named, not erased (#1708 D6): the label carries the failure, so a
            # later refusal shows the real state instead of "no key anywhere".
            sources[-1] = f"{label} ({type(exc).__name__}: {exc})"
            continue

        key = config.get("api_key") if isinstance(config, dict) else None
        if not (isinstance(key, str) and key.strip()):
            continue  # an empty key is "no config here": the next candidate wins

        _warn_if_store_is_readable(path)
        api_url = config.get("api_url")
        if isinstance(api_url, str) and api_url.strip():
            url = api_url.strip().rstrip("/") + "/mcp/"
        else:
            # #2369 D1.1, strictly: the store's own identity, or the BUILT-IN
            # DEFAULT — never the environment. A harness config is committable, so
            # a `TORTOISE_MCP_URL` it carries must not be able to aim a stored
            # credential at a host of its choosing; a store that records no
            # api_url therefore resolves to the default, not to whatever the env
            # says. (An operator who does want a stored key at a non-default host
            # records that host IN the store — `tortoise init` writes it.)
            url = _DEFAULT_URL
        return Connection(url, key.strip(), label, tuple(sources))

    return Connection(env_url or _DEFAULT_URL, None, None, tuple(sources))


def mcp_url() -> str:
    """The endpoint the next request will use.

    Resolved with the TRANSMITTING posture, because that is what the sentence
    says: a caller asking "where will this request go?" must not be told the
    project-local store's endpoint when the request itself skips that store
    (#2369 D1.2).
    """
    return resolve_connection(global_only=True).url


def _refusal(exc: BaseException, sent: Connection) -> MCPKeyEmptyError:
    return MCPKeyEmptyError(
        f"{REFUSAL_CODE}: the MCP endpoint rejected this request as "
        f"unauthenticated and NO credential resolved on the client (checked "
        f"{', '.join(sent.sources)}). The credential is read on EVERY request, so "
        f"setting it now takes effect on the next call — no client rebuild and "
        f"no session restart. Server said: {_redact_known(str(exc), sent)}"
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
        # connect failure), so the live resolution is the only evidence there is
        # — resolved with the TRANSMITTING posture, because the refusal claims
        # which stores were checked for a credential that was about to be sent.
        sent = resolve_connection(global_only=True)
    elif sent.key is not None:
        # A credential RESOLVED — but did the failed request actually carry it?
        # httpx drops `Authorization` across a cross-origin redirect, so a 401 can
        # answer a request that had none. Classifying from the resolution alone
        # would call that a value fault (and hide a real outage behind it).
        carried = _carried_authorization(exc)
        if carried is False:
            raise _not_carried(exc, sent) from exc
        return  # a credential WAS sent: the value is the problem, not its absence
    raise _refusal(exc, sent) from exc


def _redirect_refused(exc: BaseException) -> str | None:
    """The redirect target an SDK refused to follow, or None.

    Split out because it is a CLASSIFICATION, not a message format: the target is
    the endpoint the operator must point the client at. The regex anchors on the
    cross-origin wording, so a DOWNGRADE refusal (`not followed: it would
    downgrade this HTTPS endpoint to plain HTTP`, whose remedy is https-or-fix-the-
    proxy) is deliberately NOT this class: no configured endpoint is the answer to
    it, and repeating the operator's own URL back truncated to `http` would hand a
    bearer to a cleartext host.
    """
    match = _REDIRECT_REFUSED_RE.search(str(exc))
    return match.group(1) if match else None


def describe_failure(exc: BaseException, provider: _BearerProvider | None = None) -> str:
    """`str(exc)` with any credential VALUE redacted — for callers that PRINT it.

    A server may echo the key it rejected, and fastmcp carries the server's JSON-RPC
    text into the exception it raises. `status()` returns text, and the CLI prints
    it, so the same rule the bridge applies has to hold here: the evidence may
    travel, the value may not. The key is taken from what the failed request
    actually carried when that is known, else from the live resolution.
    """
    keys = []
    if provider is not None and provider.sent is not None:
        keys.append(provider.sent.key)
    if _LAST_SENT[0] is not None:
        keys.append(_LAST_SENT[0].key)
    with contextlib.suppress(Exception):
        keys.append(resolve_connection().key)
    text = str(exc)
    for key in dict.fromkeys(keys):
        text = redact(text, key)
    return text


def _carried_authorization(exc: BaseException) -> bool | None:
    """Did the FAILED request carry an Authorization header? None = unknowable."""
    request = getattr(exc, "request", None)
    headers = getattr(request, "headers", None)
    if headers is None:
        return None
    return bool((headers.get("authorization") or "").strip())


def _not_carried(
    exc: BaseException, sent: Connection, final: str | None = None,
    *, hop_refused: bool = False, request_url: str | None = None,
) -> MCPCredentialNotCarriedError:
    """The named refusal for a credential that resolved but was never sent.

    `hop_refused` is the SDK-declined-redirect shape, and it is NOT the same
    statement as "carried none": the first request DID carry the credential — the
    redirect answered it — and what would go bare is the HOP to `final`. Saying
    "never sent" there asserts a falsehood the operator can check, in the same way
    the server's message asserts one this module exists to remove.

    `request_url` is where the request ACTUALLY went — the client's endpoint, not
    `sent.url`: `sent` is the credential's provenance, and under co-source (#2369
    D1.1) the credential may belong to a host this client never talked to. Naming
    `sent.url` as the destination would credit the credential with a delivery that
    never happened.
    """
    if final is None:
        final = str(getattr(getattr(exc, "request", None), "url", "") or sent.url)
    destination = request_url or sent.url
    # The target came from SERVER text (a Location header), so it gets the same
    # treatment as every other quoted server string: quote it, never the value —
    # and `_redact_known` rather than `sent.key`, because the value a request
    # leaked is not always the value a caller holds (`sent.key` is None whenever
    # the request went out bare).
    final = _redact_known(final, sent)
    if sent.source and ("/" in sent.source):
        # A FILE-sourced credential decides its own endpoint (co-source, #2369
        # D1.1), so telling the operator to set an env URL is advice that cannot
        # work: the store's `api_url` wins. Name the value to record instead.
        remedy = (
            f"record {store_base(final)} as `api_url` in {sent.source} — a stored "
            f"credential takes its endpoint from that same file, so an environment "
            f"URL cannot aim it at {final}."
        )
    else:
        remedy = (
            f"point the endpoint at {final} directly (TORTOISE_MCP_URL, or the "
            f"store's `api_url`) so the credential goes out on the FIRST hop"
        )
    # A CLIENT's endpoint is fixed at construction (`_BearerProvider.url`), so a
    # remedy that only changes the SOURCE leaves the client pointed at the old
    # host — applying it would swap this refusal for a host-mismatch one, whose
    # advice puts the endpoint back. Say the rebuild out loud.
    remedy += (
        " Then rebuild the client (`get_client()`) — an existing client's endpoint "
        "is fixed at construction, so editing the source alone changes nothing."
    )
    if hop_refused:
        if sent.key:
            what_went_wrong = (
                f"the request went to {destination} and was answered with a redirect "
                f"to {final}, which this client's SDK declines to follow — and a "
                f"credential is deliberately not carried across origins. The value "
                f"resolved from {sent.source} reached {destination}, never {final}, "
                f"so it is not what was refused."
            )
        else:
            what_went_wrong = (
                f"the request went to {destination} and was answered with a redirect "
                f"to {final}, which this client's SDK declines to follow. No "
                f"credential resolved either ({', '.join(sent.sources)}), so that "
                f"request went bare — but the redirect is the fault to fix first."
            )
        return MCPCredentialNotCarriedError(
            f"{MCPCredentialNotCarriedError.code}: {what_went_wrong} Fix: {remedy}"
        )
    return MCPCredentialNotCarriedError(
        f"{MCPCredentialNotCarriedError.code}: the MCP endpoint rejected this "
        f"request as unauthenticated, and the request that failed carried NO "
        f"credential — although one resolved from {sent.source}. The endpoint "
        f"redirected to a different origin ({final}) and Authorization is "
        f"deliberately not carried across origins, so the value was never sent "
        f"and is not what was refused. Fix: {remedy}"
    )


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

    ⛔ `sent`/`withheld` are BOOKKEEPING FOR THE LAST REQUEST, not per-request
    state — one provider is shared by every request on one client. So the
    failure path does NOT read them back: `_resolve_and_stamp` also writes the
    pair onto the request itself (`_PROVENANCE_EXT`), which is what
    `_provenance` prefers. Reading the attributes after the fact attributed a
    PEER request's credential to this failure — a withheld-credential rejection
    reported as a generic value fault, or the reverse (review, P2). The
    attributes stay because two best-effort readers need a last-known value
    (`_url` names the endpoint of the last attempt, and the success-path
    `_announce_withheld` has no failing request to key on), and because
    losing a race there can only misdescribe a warning, never misclassify a
    refusal.
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.sent: Connection | None = None
        self.withheld: Connection | None = None

    def _resolve_and_stamp(self, request) -> None:
        """Resolve the credential for THIS request and record it ON the request.

        `global_only=True`: this is the step that actually puts the credential on
        the wire, so it is the transmitting posture by definition — a
        project-local store must not decide the header here even if some caller
        resolved its endpoint with the default posture (#2369 D1.2).
        """
        connection = resolve_connection(global_only=True)
        withheld = None
        self.sent = connection
        _LAST_SENT[0] = connection
        self.withheld = None
        if connection.key and not _same_endpoint(connection.url, self.url):
            withheld = connection
            self.withheld = connection
        elif connection.key:  # a blank resolution is absent, never `Bearer ` + nothing
            request.headers["Authorization"] = f"Bearer {connection.key}"
        # The per-request record a failure is classified from: the provider's own
        # attributes are overwritten by whichever request resolved last.
        request.extensions[_PROVENANCE_EXT] = (connection, withheld)

    def auth_flow(self, request):
        self._resolve_and_stamp(request)
        yield request

    async def async_auth_flow(self, request):
        """The ASYNC variant, because this scheme performs FILE I/O.

        httpx's own `Auth` docstring says to override `async_auth_flow` when the
        scheme does I/O, and it is not a hint: the base implementation drains the
        SYNC generator, so `resolve_connection()` — a `stat`, a `read_text` and a
        `json.loads`, on EVERY request — ran ON the event loop. Every async client
        in the tree (fastmcp's, and `status()`/`list_tools()`/`call_tool()` ride
        it) would stall the whole loop for a disk read per request, which shows
        up as latency under concurrency rather than as an error.

        `asyncio.to_thread` moves exactly that call off the loop — the credential
        is still resolved immediately before the request is sent (the #10605
        property), only not on the loop thread.
        """
        await asyncio.to_thread(self._resolve_and_stamp, request)
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


def store_base(client_url: str) -> str:
    """The `api_url` a store must record for `client_url` to resolve to it.

    `resolve_connection` builds a store endpoint as `api_url + "/mcp/"`, so
    naming the endpoint itself as the value produces `/mcp/mcp/`. The base is
    what the message must tell the operator to record.

    PUBLIC (no leading underscore) because `tortoise/mcp_bridge.py` imports it:
    a cross-module API spelled `_store_base` reads as private, so the next
    author needing it either re-implements the `/mcp` trimming (and gets
    `/mcp/mcp/` back) or imports a name the module's author believed was
    internal. Its siblings in that same import (`resolve_connection`, `redact`,
    `Connection`, `REFUSAL_CODE`) are all public, so this one is too.
    """
    base = client_url.rstrip("/")
    return base[: -len("/mcp")] if base.endswith("/mcp") else base


def _mismatch(connection: Connection, client_url: str) -> MCPCredentialHostMismatchError:
    return MCPCredentialHostMismatchError(
        f"MCP-CREDENTIAL-HOST-MISMATCH: the credential from {connection.source} belongs "
        f"to {connection.url}, but this client talks to {client_url} (#2369 D1.1). The "
        f"credential was NOT sent — an environment-supplied URL must never be able to "
        f"aim a stored credential at a host of its choosing. Fix the source: record "
        f"{store_base(client_url)} as `api_url` in the store, use the env key for this "
        f"host, or build the client for {connection.url}."
    )


def _provenance(
    exc: BaseException, provider: _BearerProvider,
) -> tuple[Connection | None, Connection | None]:
    """`(sent, withheld)` for the request that FAILED, not for the last one.

    The pair is stamped onto the request by `_BearerProvider._resolve_and_stamp`
    (`_PROVENANCE_EXT`) and read back off the exception's request, so a refusal
    can never be classified from a PEER request's resolution: one provider is
    shared by everything the client sends, and concurrent requests overwrite its
    `sent`/`withheld` attributes. Reading those after the fact reported a
    withheld-credential rejection as a generic value fault, or the reverse.

    Falls back to the provider's last values when the exception carries no
    request (`httpx` sets one on every transport error, but a wrapper from the
    MCP layer need not): a best-effort answer beats no refusal, and the withheld
    leg still fails CLOSED — the credential was withheld from the wire either
    way, so the only thing a stale record can affect is which message names it.
    """
    extensions = getattr(getattr(exc, "request", None), "extensions", None)
    record = extensions.get(_PROVENANCE_EXT) if isinstance(extensions, dict) else None
    if record is not None:
        return record
    return provider.sent, provider.withheld


def _classify_failure(exc: BaseException, provider: _BearerProvider) -> None:
    """Raise the named refusal this failure actually is (no-op otherwise).

    A withheld credential explains an AUTH REJECTION (the request went out bare
    because the credential belonged elsewhere). It does not explain a transport
    fault: a daemon that is down is an outage whatever the credential situation,
    and reporting it as a co-source conflict would move an outage into the
    set-up-gap bucket (#3805) exactly as the server message this module removes
    moves a set-up gap into the credential-value bucket.

    The credential facts come from `_provenance`, i.e. from THIS failure's own
    request — see that function for why the provider's attributes are not read
    back here.
    """
    sent, withheld = _provenance(exc, provider)
    is_auth_rejection = bool(_AUTH_REJECTION_RE.search(str(exc)))
    declined_hop = _redirect_refused(exc)
    if withheld is not None:
        # A withheld credential explains BOTH failure shapes: an auth rejection
        # (the request went out bare) and a declined redirect (the hop it would
        # have taken is the one a withheld credential must never take). The
        # redirect case has to be named here too — otherwise a co-source conflict
        # reports as "your endpoint redirects", and the message credits a
        # withheld value with a delivery that never happened.
        if (is_auth_rejection or declined_hop) and not _is_tool_error(exc):
            raise _mismatch(withheld, provider.url) from exc
        if is_auth_rejection:
            return  # a TOOL's own 401 is the tool's, whatever the credential state
    # An SDK that REFUSES the redirect never reaches the auth check below (no
    # 401 happened — the hop was declined), so it has to be classified here or the
    # refusal is lost and the set-up gap reports as an outage. A TOOL error is
    # exempt for the same reason the other classifier exempts it: a Tortoise tool
    # that proxies another MCP server can quote a nested SDK refusal verbatim, and
    # rewriting that into "your endpoint is misconfigured" moves a failing tool
    # into the credential bucket.
    if declined_hop is not None and not _is_tool_error(exc):
        raise _not_carried(
            exc, sent or resolve_connection(global_only=True), declined_hop,
            hop_refused=True, request_url=provider.url,
        ) from exc
    _raise_if_credential_was_missing(exc, sent)


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
        teardown_failed = False
        if exc is None:
            try:
                result = await self._client.__aexit__(exc_type, exc, tb)
            except BaseException as raised:
                # A close/session-termination failure AFTER a successful body must
                # not be swallowed: `status()` would report `ok` for a probe whose
                # teardown failed — a false PASS, not a benign one.
                exc = raised
                teardown_failed = True
        else:
            # The library re-raises the body's error here; whichever instance
            # arrives, it is the one that must be classified.
            try:
                await self._client.__aexit__(exc_type, exc, tb)
            except BaseException as raised:
                exc = raised
        if exc is not None:
            _classify_failure(exc, self._provider)
            if teardown_failed:
                raise exc
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
    except (MCPKeyEmptyError, MCPCredentialHostMismatchError,
            MCPCredentialNotCarriedError):
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
    return _client(_BearerProvider(resolve_connection(global_only=True).url))


def _resolve_provider() -> _BearerProvider:
    """Build the live-credential provider for a TRANSMITTING entry point.

    ⛔ Called from INSIDE each entry point's guarded region, never as an argument
    evaluated before it. Resolving the connection is I/O — `Path.home()` (which
    raises `RuntimeError("Could not determine home directory.")` for an
    unregistered uid, the arbitrary-uid container case), then a `stat`, a
    `read_text` and a `json.loads` — so it can fail, and it fails in the same
    places a request can. Accepting `_BearerProvider(resolve_connection(…).url)`
    as a pre-built argument moved that I/O outside every handler: the raise
    escaped `status()`/`available()`, which promise never to raise, as a
    traceback instead of the `tortoise_unavailable` payload a script caller skips
    cleanly on. Pre-#10605 the resolution happened INSIDE the probed coroutine and
    was therefore caught by `except Exception` (the module's own premise: both
    redaction sites wrap the same call in `contextlib.suppress(Exception)`).
    """
    return _BearerProvider(resolve_connection(global_only=True).url)


def _failure_url(provider: _BearerProvider | None) -> str:
    """The endpoint to NAME on a failure payload — resolved without any I/O.

    `_url` falls back to `mcp_url()`, which re-resolves the connection. That is
    the very I/O that may have just failed, so a failure payload must not depend
    on it: it would re-raise out of the very `except` it is building. `provider`
    is None only when the resolution itself failed, and the built-in default is
    then the truthful answer (nothing else was ever named).
    """
    if provider is None:
        return _DEFAULT_URL
    return provider.sent.url if provider.sent is not None else provider.url


def status() -> dict[str, Any]:
    """Connectivity + surface probe. Never raises.

    Returns {"status": "ok", "url": ..., "tools": N} when reachable,
    {"status": "unconfigured", "url": ..., "error": "MCP-KEY-EMPTY: …"} when the
    endpoint demands a credential the client does not have (the recorded
    `unconfigured` term — a set-up gap, not an outage, #3805), else
    {"status": "tortoise_unavailable", "url": ..., "error": ...}.
    """
    provider: _BearerProvider | None = None

    async def _probe(prov: _BearerProvider) -> dict[str, Any]:
        async with _client(prov) as client:
            tools = await client.list_tools()
        return {"status": "ok", "url": _url(prov), "tools": len(tools)}

    try:
        provider = _resolve_provider()
        return _run_guarded(_probe(provider), provider)
    except (MCPKeyEmptyError, MCPCredentialHostMismatchError,
            MCPCredentialNotCarriedError) as exc:
        # Both are CONFIGURATION facts, not outages: report them under the recorded
        # `unconfigured` term so a caller's exit code (4 — a set-up gap) cannot
        # read as "the store is down" (3).
        _logger.debug("tortoise unconfigured: %s", exc)
        return {
            "status": STATUS_UNCONFIGURED,
            "url": _failure_url(provider),
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001, RUF100
        _logger.debug("tortoise unavailable: %s", describe_failure(exc, provider))
        return {
            "status": _UNAVAILABLE,
            "url": _failure_url(provider),
            "error": describe_failure(exc, provider),
        }


def available() -> bool:
    """True when the daemon is reachable (status probe)."""
    return status().get("status") == "ok"


def list_tools() -> list[str]:
    """Names of the tools exposed by the daemon (registry surface)."""
    async def _list(prov: _BearerProvider) -> list[str]:
        async with _client(prov) as client:
            tools = await client.list_tools()
        return [t.name for t in tools]

    return _run_resolved(_list)


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call an MCP tool (e.g. tortoise_create_point) and return its result.

    Raises on transport/auth failure — callers that want graceful behavior
    should check `available()` first (or catch exceptions). An auth rejection
    with no credential resolved raises `MCPKeyEmptyError`.
    """
    async def _call(prov: _BearerProvider) -> Any:
        async with _client(prov) as client:
            return await client.call_tool(name, arguments or {})

    return _run_resolved(_call)


def _run_resolved(operation):
    """Resolve the provider, then run `operation(provider)` under the guard.

    ⛔ The RESOLUTION is the one step that cannot be CLASSIFIED: every named
    refusal `_classify_failure` builds is keyed on the provider's own endpoint,
    and the provider is exactly what failed to exist. So a resolution fault
    propagates as itself — which is what these two entry points document
    ("raises on transport/auth failure"). `status()`, which promises never to
    raise, is the caller that wraps this in its own handler instead.
    """
    provider = _resolve_provider()
    return _run_guarded(operation(provider), provider)
