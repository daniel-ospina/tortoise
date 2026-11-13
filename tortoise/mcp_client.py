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
import subprocess
import sys
from dataclasses import dataclass, field
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

#: A URL embedded in a failure's own text. Stripped BEFORE `_AUTH_REJECTION_RE`
#: runs, because a status code is a DELIMITED token and a path segment is not:
#: `… for url 'http://h:9/401/mcp'` (any non-auth HTTP status, a proxy message, a
#: transport error that names its target) matched the `40[13]` arm and turned an
#: unreachable or mis-addressed endpoint into the credential-less refusal —
#: "add a key" for a request that never got as far as a credential decision.
#: That is the #3805 inversion this module exists to remove, in the other
#: direction (an outage reported as a set-up gap). The scheme is required on
#: purpose: an httpx status/transport error carries a fully-spelled URL, while
#: stripping bare `host:port` would also eat the legitimate `status:401` form.
_URL_IN_ERROR_RE = re.compile(r"\b[a-z][a-z0-9+.\-]*://\S+", re.IGNORECASE)


def _is_auth_rejection(text: str) -> bool:
    """Does this failure text carry a server-side auth rejection?

    One owner for both classifiers (`_raise_if_credential_was_missing` and
    `_classify_failure`), so the URL strip cannot be applied to one and forgotten
    in the other — two owners drifting is how the same text got classified two
    ways.
    """
    return bool(_AUTH_REJECTION_RE.search(_URL_IN_ERROR_RE.sub("<url>", text)))

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

#: Stores already reported as repo-committed, so the per-request path prints the
#: line once instead of on every MCP call.
_WARNED_TRACKED_STORES: set[str] = set()

#: one-off operator warnings already emitted, keyed by topic — never repeated.
_WARNED_TOPICS: set[str] = set()

#: provenance of the most recent request this process made. Used ONLY as a
#: redaction fallback by `describe_failure`: a caller that prints an error after
#: a rotation must still strip the key that was actually on the wire.
_LAST_SENT: list[Connection | None] = [None]


def _warn_once(topic: str, message: str) -> None:
    """Write a one-off operator warning to stderr (never raises, never repeats).

    Routed through `_warn`, not `print`: this writer is the one behind
    `_announce_withheld`, which runs AFTER a successful request on both public
    paths (`_run_guarded` and `_GuardedClient.__aexit__`). An unguarded write
    there does not merely lose a diagnostic — it turns a result the client
    already holds into a `BrokenPipeError`, and `status()`'s broad `except`
    converts a probe that SUCCEEDED into `tortoise_unavailable`.
    """
    if topic in _WARNED_TOPICS:
        return
    _WARNED_TOPICS.add(topic)
    _warn(f"[tortoise-mcp] {message}")


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

    `key` is excluded from `repr()`: a dataclass's generated repr renders every
    field, so one `logger.info(connection)`, one `print(conn)` or one traceback
    frame that includes the object would put a live Bearer credential in a log
    line, a transcript, or a CI artifact. The value stays a readable ATTRIBUTE
    (`connection.key` is what `_redact_known` and the bridge's `_headers` use),
    it is only the IMPLICIT rendering that must not carry it.
    """

    url: str
    key: str | None = field(repr=False)
    source: str | None
    sources: tuple[str, ...]
    #: whether an ENDPOINT was DECLARED — by `TORTOISE_MCP_URL`, or by a store's
    #: `api_url`. `url` is always populated, because the built-in default is a
    #: FALLBACK rather than a configuration; a caller that must tell "configured
    #: but down" from "never set up" (#3805) needs this flag, since "a URL
    #: resolves" cannot answer it.
    declared: bool = False


def _credential_paths() -> tuple[tuple[Path, bool], ...]:
    """The credential stores, in the recorded precedence (#1708 D4/D5).

    Each candidate is `(path, repo_controlled)`. `repo_controlled` marks the
    candidate a REPOSITORY can commit — the project-local `./.tortoise` — which is
    ALWAYS vetted by `_git_tracks` before it is read. The global store is the
    user's own, and is vetted only when a `.git` sits above it: that spawn-free
    pre-check is what keeps the ordinary host free of a per-request `git` call,
    while a `$HOME` that IS a checkout still cannot be re-aimed by one committed
    file.

    PATH FUNCTIONS, never module constants: a `Path.home()` or `Path.cwd()`
    captured at import is the same capture-at-start bug this module exists to
    remove (#10605).

    BOTH stores the onboarding CLI writes are read, project-local first:
    `tortoise init --api-key` saves `./.tortoise` (`_cmd_init`), and the #1708
    global store is `~/.tortoise/credentials.json`. Reading only the global one
    made the client blind to the store the hosted onboarding actually creates —
    no credential, the default endpoint, and a `MCP-KEY-EMPTY` naming stores the
    user never wrote.

    NEVER RAISES. A candidate list is not a claim that a store EXISTS, so a
    location that cannot be named is simply absent — the same "no config here"
    the loop already `continue`s on. `Path.cwd()` raises `FileNotFoundError` for
    a REMOVED working directory (a reaped worktree, and this module exists for
    the long-lived process), and `Path.home()` raises `RuntimeError` when HOME is
    unresolvable (a container on an arbitrary UID with no passwd entry). Those
    are not hypothetical here: resolution runs PER REQUEST (#10605), so an
    unguarded raise does not fail one probe — it breaks `status()`'s documented
    never-raise contract, and every `list_tools`/`call_tool`, on every call.
    """
    candidates: list[tuple[Path, bool]] = []
    with contextlib.suppress(OSError):
        candidates.append((Path.cwd() / ".tortoise", True))
    with contextlib.suppress(RuntimeError, OSError):
        candidates.append((Path.home() / ".tortoise" / "credentials.json", False))
    return tuple(candidates)


#: How long git may take to answer whether it tracks a store. Bounded because
#: this sits on the per-request resolution path: a wedged git must cost a
#: credential, never a hang.
_GIT_QUERY_TIMEOUT_S = 5.0


def _in_a_git_work_tree(directory: Path) -> bool:
    """Whether a `.git` entry sits at or above `directory`.

    `.git` is a DIRECTORY in an ordinary checkout and a FILE in a linked
    worktree or a submodule, so both count. Used at TWO sites, and both matter:

    * `_git_tracks` calls it when git cannot answer at all (no binary, a timeout);
    * `resolve_connection` calls it DIRECTLY, as the spawn-free pre-check that
      decides whether git is asked at all — which is what keeps the global
      candidate covered without paying a `git` spawn per request. Deleting that
      disjunct as "redundant" would stop a `~/.tortoise/credentials.json` under a
      dotfiles repo from being vetted at all (fail-open).

    `exists()` re-raises OSError for an UNSEARCHABLE directory (`chmod 000`, an
    EACCES on a container's `HOME`); it only swallows ENOENT/ENOTDIR/ELOOP/EINVAL.
    A directory that cannot be searched might hide a `.git`, so an unanswerable
    question fails CLOSED — the same direction `_git_tracks` takes on a git fatal.
    """
    try:
        return any((candidate / ".git").exists() for candidate in (directory, *directory.parents))
    except OSError:
        return True


def _git_tracks(path: Path) -> bool:
    """Whether git TRACKS `path` — i.e. whether the store arrived with the checkout.

    A store `tortoise init --api-key` writes is GITIGNORED, so one git tracks came
    from the REPOSITORY: a clone, a `git add -f`, or a hostile repo — the
    attacker-controllable class `tortoise/__main__.py::_resolve_transmit_config`
    (#3660) fails closed on, because such a file controls BOTH halves of the
    identity (its `api_key` and its `api_url`) and would otherwise choose the host
    a stored Bearer is sent to.

    NO LOCK-INVERSION: a store outside any repository (a plain directory, a
    tarball) cannot have been committed, so it is read as before.

    ONLY TWO GIT EXIT CODES ARE ANSWERS, and every other one is a FAIL-CLOSED
    fallback: `0` (tracked) and `1` (this repository does not track it). A fatal
    MUST NOT read as "not tracked" — `128` is shared by "not a git repository",
    "dubious ownership" (a bind-mounted repo owned by another uid, the ordinary
    container case), an unreadable or corrupt index, and a `.git` git will not
    parse. Reading those as a negative fails OPEN on exactly the checkouts a
    container sees, and a repo-committed store then supplies BOTH halves again.
    So the fatal arm asks the mechanical question instead — is there a `.git`
    above the store? — and the same arm answers a git that cannot be run at all.

    A store in NO repository cannot have been committed, so the fatal arm is right
    for two opposite readings of `128`: "not a git repository" has no `.git` above
    it (so the store is read), while "dubious ownership" / an unreadable index / a
    corrupt `.git` does (so it is skipped).

    THE AMBIENT ENVIRONMENT IS NOT AN INPUT. `git` would otherwise honour
    `GIT_DIR`, `GIT_WORK_TREE`, `GIT_INDEX_FILE` and friends, which make it answer
    about a DIFFERENT repository or index — and a committed harness `env` block is
    exactly the channel #2369 D1.1 assumes is reachable (see
    `tortoise/__main__.py::_resolve_transmit_config`). With `GIT_INDEX_FILE`
    pointed at an empty index, a TRACKED store answers `1` and is read: the guard
    fails OPEN on the shape it exists to stop. So the child gets a scrubbed
    environment, no inherited stdin (in the bridge this process's stdin IS the
    JSON-RPC pipe), and no interactive prompt.

    WHAT THIS GUARD IS, AND IS NOT: it stops the file that ARRIVES WITH A
    CHECKOUT, which is the case that needs no local code execution. It is not a
    general "repo-controlled file is never an identity" rule — a repo-run step
    (`setup.sh`, a devcontainer `postCreateCommand`) or an extracted archive can
    write an UNTRACKED `./.tortoise`, and #1708 D5 requires that store to be read
    (it is indistinguishable from the one `tortoise init` leaves). Closing that
    case means excluding the project-local candidate from TRANSMITTING resolution
    entirely, as #3660 does — a reversal of D5, which needs an owner decision and
    is not taken here.
    """
    try:
        completed = subprocess.run(
            ["git", "-C", str(path.parent), "ls-files", "--error-unmatch", "--", path.name],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env={
                # Nothing from the ambient environment may choose the repository
                # or the index: that is what makes `1` an answer about THIS file.
                key: value for key, value in os.environ.items()
                if not key.startswith("GIT_")
            } | {"GIT_TERMINAL_PROMPT": "0"},
            timeout=_GIT_QUERY_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return _in_a_git_work_tree(path.parent)
    if completed.returncode == 0:
        return True  # tracked: the store came from the repository
    if completed.returncode == 1:
        return False  # git ANSWERED: this repository does not track it
    return _in_a_git_work_tree(path.parent)  # a fatal is not an answer


def _warn(line: str) -> None:
    """Write one operator warning to stderr, and NEVER raise (or redirect) doing it.

    A diagnostic must not become a failure. `status()` documents "never raises"
    and its callers skip cleanly on that basis (`integrations/crm/twenty/bridge.py`
    catches only ImportError/ValueError), but a stderr write can raise: a closed
    pipe (`cmd | head -1` -> BrokenPipeError), a full filesystem (`2>/dev/full` ->
    ENOSPC), fd exhaustion, or a stderr that has been closed (ValueError). Those
    are reached from `resolve_connection()`, which now runs on EVERY request, so
    an unguarded `print` turns a cosmetic warning into exactly the crash the
    never-raise contract exists to prevent.

    `sys.stderr is None` (fd 2 closed) is checked FIRST because `print(file=None)`
    falls back to **stdout**: in the bridge that is the JSON-RPC pipe, and a
    diagnostic there is a protocol error, not a warning. A dropped line is always
    cheaper than a corrupted frame.
    """
    if sys.stderr is None:
        return
    with contextlib.suppress(OSError, ValueError):
        print(line, file=sys.stderr)


def _warn_if_store_is_git_tracked(path: Path) -> None:
    """Warn once per process when a credential store is COMMITTED to a repository.

    The store is IGNORED, not merely reported (#10796): a warning that still used
    the credential would leave a cloned repo free to re-aim the endpoint, which is
    the whole defect. The line exists so the operator learns WHY their store went
    unread instead of seeing only the `MCP-KEY-EMPTY` that names it.

    The wording covers both arms of `_git_tracks`: git may have said the file is
    TRACKED, or may have been unable to answer at all while a `.git` sits above it
    (failed closed). Claiming "tracked" for the second would be a guess dressed
    as a fact.
    """
    label = str(path)
    if label in _WARNED_TRACKED_STORES:
        return
    _WARNED_TRACKED_STORES.add(label)
    _warn(
        f"⚠️  {label} is tracked by git, or sits in a git work tree that could not "
        f"be checked — either way a repository can commit that file, so it is NOT "
        f"trusted as a credential source and is IGNORED. Remove it from the "
        f"repository (`git rm --cached {path.name}`) and let `tortoise init "
        f"--api-key` write a gitignored store, or set TORTOISE_API_KEY."
    )


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
    _warn(f"⚠️  {path} is mode {mode:04o} — group/other can read the MCP "
          f"credential stored in it. Fix: chmod 600 {path}")


def resolve_connection() -> Connection:
    """Resolve the endpoint + credential NOW.

    Precedence: env (`TORTOISE_API_KEY`, then the `TORTOISE_MCP_API_KEY` alias)
    then the two STORES in #1708 D5 order — the project-local `./.tortoise`
    `tortoise init --api-key` writes, then `~/.tortoise/credentials.json` — the
    same env-and-store shape `tortoise/__main__.py::_resolve_config_path` uses
    (#1708 D1/D4/D5), reproduced here rather than imported because this module
    ships in the client-only wheel (it cannot depend on the engine's `__main__`).
    A store git TRACKS is skipped rather than read; `_git_tracks` says why.

    A blank/whitespace value is treated as ABSENT (never as an empty credential).
    A store that exists but cannot be parsed or read is NOT reported as absent
    (#1708 D6): its label in `sources` carries the failure, so the refusal names
    the real state instead of telling the operator to set a key that is there.

    CO-SOURCE (#2369 D1.1): when the FILE supplies the key, the FILE decides the
    host — its `api_url` plus `/mcp/`, or the BUILT-IN DEFAULT when it records
    none. A `TORTOISE_MCP_URL` from the environment is honoured only by an
    env-sourced credential, so a committed harness config cannot aim a stored key
    at a host of its choosing.

    A store git TRACKS is not an identity source at all (#10796). Both halves of
    this identity come from one file, so a repo that commits `./.tortoise` would
    choose the key AND the host — exactly the shape #3660 fails closed on for the
    transcript uploaders. Precedence itself is unchanged (#1708 D5): a store the
    user's own `tortoise init` wrote is gitignored, so it is still read first.
    """
    sources: list[str] = []
    env_url = os.environ.get("TORTOISE_MCP_URL") or None
    for name in _ENV_KEY_NAMES:
        sources.append(name)
        value = os.environ.get(name)
        if value and value.strip():
            return Connection(
                env_url or _DEFAULT_URL, value.strip(), name, tuple(sources),
                declared=bool(env_url),
            )

    for path, repo_controlled in _credential_paths():
        label = str(path)
        sources.append(label)
        try:
            present = path.is_file()
        except OSError as exc:
            # A candidate that cannot be STATTED (an unsearchable directory —
            # EACCES on a container's HOME, a `chmod 000` store dir) is not a
            # store that is readable here. `is_file()` only swallows
            # ENOENT/ENOTDIR/ELOOP/EINVAL, and a raise here would break the
            # never-raise contract on every request. The class is NAMED from the
            # exception rather than assumed: EACCES gives PermissionError, but a
            # network-mounted `$HOME` gives a plain OSError (EIO/ESTALE/ETIMEDOUT),
            # and naming the wrong errno misdirects the operator.
            sources[-1] = f"{label} (unreadable: {type(exc).__name__})"
            continue
        if not present:
            # Absent, or a DIRECTORY at the project-local path (a repo's own
            # `./.tortoise` state dir) — "no config here", so the next candidate
            # wins (#1708 D5). Reading it would raise IsADirectoryError and be
            # reported as a CORRUPT store.
            continue
        # The project-local candidate is ALWAYS vetted (a repository can commit
        # it). The global store is vetted only when a `.git` sits above it: the
        # ordinary host pays no `git` spawn per request, while a `$HOME` that IS a
        # checkout is still covered — a dotfiles repo tracking
        # `.tortoise/credentials.json` would otherwise supply both halves from one
        # committed file, the #10796 shape on this candidate.
        if (repo_controlled or _in_a_git_work_tree(path.parent)) and _git_tracks(path):
            # Checked BEFORE the read: the file is repo-controlled, so it must not
            # supply a key OR an `api_url`. Marked in `sources` so a later refusal
            # reports the real state — ignored, not absent. The label says
            # "repo-controlled", not "git-tracked": the fatal arm only proves a
            # `.git` sits above the store, and asserting more would be a guess.
            _warn_if_store_is_git_tracked(path)
            sources[-1] = f"{label} (repo-controlled: ignored)"
            continue
        try:
            config = json.loads(path.read_text())
        except Exception as exc:
            # ANY failure to obtain config from this path IS "this store is not
            # readable config", so the catch is deliberately total. `read_text`
            # raises OSError; `json.loads` raises ValueError for malformed JSON
            # and **RecursionError** (a RuntimeError, NOT a ValueError) for
            # deeply nested input — which escaped this handler, escaped
            # `resolve_connection`, and reached `status()` and the bridge's
            # `main()` as a traceback. That made "never raises" false and made a
            # bridge die without answering the frame it was meant to answer.
            #
            # Named, not erased (#1708 D6): the label carries the failure, so a
            # later refusal shows the real state instead of "no key anywhere".
            sources[-1] = f"{label} ({type(exc).__name__}: {exc})"
            continue

        key = config.get("api_key") if isinstance(config, dict) else None
        if not (isinstance(key, str) and key.strip()):
            continue  # an empty key is "no config here": the next candidate wins

        _warn_if_store_is_readable(path)
        api_url = config.get("api_url")
        endpoint_declared = isinstance(api_url, str) and bool(api_url.strip())
        # #2369 D1.1, strictly: the store's own identity, or the BUILT-IN DEFAULT —
        # never the environment. A harness config is committable, so a
        # `TORTOISE_MCP_URL` it carries must not be able to aim a stored credential
        # at a host of its choosing; a store that records no api_url therefore
        # resolves to the default, not to whatever the env says. (An operator who
        # does want a stored key at a non-default host records that host IN the
        # store — `tortoise init` writes it.)
        url = (api_url.strip().rstrip("/") + "/mcp/") if endpoint_declared else _DEFAULT_URL
        return Connection(
            url, key.strip(), label, tuple(sources), declared=endpoint_declared,
        )

    return Connection(
        env_url or _DEFAULT_URL, None, None, tuple(sources), declared=bool(env_url),
    )


def endpoint_declared() -> bool:
    """True when an ENDPOINT is configured — `TORTOISE_MCP_URL`, or a store's `api_url`.

    The client-boundary probe's contribution (#3805). The driver always resolves
    SOME endpoint, because the built-in default is a FALLBACK rather than a
    configuration — so "a URL resolves" cannot distinguish "configured but down"
    from "never set up", and neither can `TORTOISE_MCP_URL` alone now that a
    store's `api_url` is a first-class endpoint source (#10605).
    """
    return resolve_connection().declared


def mcp_url() -> str:
    """The endpoint the next request will use."""
    return resolve_connection().url


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
    if not _is_auth_rejection(str(exc)):
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
        if sent.key is not None:
            # ⛔ NOT the empty-credential class: a key RESOLVES here, so
            # `_refusal`'s "NO credential resolved on the client (checked …)"
            # would assert the opposite of the current state AND name the very
            # source that is set — the misdirection this module exists to remove
            # (#10727 review, P3). What is true is narrower: the FAILED request
            # never carried one, and nothing in the exception proves WHY (it
            # carries no request, so `_carried_authorization` is None), so no
            # second claim is invented — the server's message is relayed unchanged
            # and the operator keeps the part that is accurate.
            return
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
            f"record {_store_base(final)} as `api_url` in {sent.source} — a stored "
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
    """

    def __init__(self, url: str) -> None:
        self.url = url
        self.sent: Connection | None = None
        self.withheld: Connection | None = None

    def auth_flow(self, request):
        connection = resolve_connection()
        self.sent = connection
        _LAST_SENT[0] = connection
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
    is_auth_rejection = _is_auth_rejection(str(exc))
    declined_hop = _redirect_refused(exc)
    if provider.withheld is not None:
        # A withheld credential explains BOTH failure shapes: an auth rejection
        # (the request went out bare) and a declined redirect (the hop it would
        # have taken is the one a withheld credential must never take). The
        # redirect case has to be named here too — otherwise a co-source conflict
        # reports as "your endpoint redirects", and the message credits a
        # withheld value with a delivery that never happened.
        if (is_auth_rejection or declined_hop) and not _is_tool_error(exc):
            raise _mismatch(provider.withheld, provider.url) from exc
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
            exc, provider.sent or resolve_connection(), declined_hop,
            hop_refused=True, request_url=provider.url,
        ) from exc
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
    except (MCPKeyEmptyError, MCPCredentialHostMismatchError,
            MCPCredentialNotCarriedError) as exc:
        # Both are CONFIGURATION facts, not outages: report them under the recorded
        # `unconfigured` term so a caller's exit code (4 — a set-up gap) cannot
        # read as "the store is down" (3).
        _logger.debug("tortoise unconfigured: %s", exc)
        return {"status": STATUS_UNCONFIGURED, "url": _url(provider), "error": str(exc)}
    except Exception as exc:  # noqa: BLE001, RUF100
        _logger.debug("tortoise unavailable: %s", describe_failure(exc, provider))
        return {
            "status": _UNAVAILABLE,
            "url": _url(provider),
            "error": describe_failure(exc, provider),
        }


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


def refusal_message(error: BaseException | str) -> str | None:
    """The message, when `error` is one of the surface's DESIGNED refusals.

    Since #11153 the server flags a refused tool call with `isError: true`, and a
    fastmcp `Client` is built with `raise_on_error=True` by default — so a refusal
    reaches a caller as a raised `ToolError` BEFORE any payload can be inspected.
    A caller that must keep a refusal distinct from a CRASH therefore needs the
    classification at the boundary that raises, which is here: this module owns
    `call_tool`, and two callers (`client/tortoise_client/cli.py`,
    `tools/curate_graph.py`) each needed it. One owner, no second copy.

    TWO designed refusal shapes arrive as a `ToolError`:

    * a `_safe` / `_quota_gated` refusal — fastmcp raises `content[0].text`, which
      for a `_safe` payload is the JSON error dict (`{"error": "Team points limit
      reached …", "code": -32006}`);
    * the TRANSPORT WAIT-BOUND refusal (`-32009`) — its `content` is PROSE, not
      JSON, and on the hosted surface it is the ROUTINE refusal for an expensive
      census walk. Its two arms (dispatched vs never-dispatched) say OPPOSITE
      things about whether a write can be in flight, so both are matched here and
      the text itself is handed back for the caller to branch on.

    A server-side CRASH's text is prose too (`Error calling tool: TypeError: …`)
    and matches neither arm — returns None, so a driver that pages on "the run
    crashed" is not silenced by a refusal verdict (#11153 review round 3, P2).
    """
    text = error if isinstance(error, str) else str(error)
    if not text:
        return None
    # Imported LAZILY: `tortoise.mcp_auth` imports fastmcp at module level, and
    # this module stays importable without fastmcp (script callers degrade
    # gracefully). The prose lives there — one source, not copied here.
    try:
        from tortoise.mcp_auth import (
            _TRANSPORT_WAIT_BOUND_MESSAGE,
            _TRANSPORT_WAIT_BOUND_NOT_SENT_MESSAGE,
        )
    except Exception:  # fastmcp absent; there is no client here to refuse anything
        pass
    else:
        for prose in (_TRANSPORT_WAIT_BOUND_MESSAGE,
                      _TRANSPORT_WAIT_BOUND_NOT_SENT_MESSAGE):
            # `startswith` and not equality: a wrapped message keeps its prefix,
            # and the refusal prose itself is the leading sentence either way.
            if text.startswith(prose[:60]):
                return text
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return None
    if isinstance(payload, dict) and isinstance(payload.get("error"), (str, dict)):
        return text
    return None


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call an MCP tool (e.g. tortoise_create_point) and return its result.

    ⛔ Raises `fastmcp.exceptions.ToolError` for ANY tool-level ERROR RESULT, not only for
    transport/auth failure (#11153). The hosted surface flags a refused call with
    `isError: true` — a quota refusal (`-32006`), an auth rejection, a validation error, a
    wait-bound expiry — and fastmcp's client is built with `raise_on_error=True` by
    default, so that refusal arrives as a RAISED `ToolError` and never reaches the caller
    as a payload. `normalise_tool_result` therefore cannot see it; a caller that needs the
    refusal's `code` must catch `ToolError` and read its text — `refusal_message()`
    below is the ONE classifier for "is this text a DESIGNED refusal or a crash?",
    so a consumer distinguishes the two the same way this module's other callers do
    instead of re-deriving the shapes a fourth time.

    Also raises on transport failure, and `MCPKeyEmptyError` when an auth rejection leaves
    no credential resolved.

    ⚠️ `available()` answers REACHABILITY only — it cannot pre-empt a tool-level refusal, so
    "call `available()` first" is not a substitute for handling `ToolError`.
    """
    provider = _BearerProvider(resolve_connection().url)

    async def _call() -> Any:
        async with _client(provider) as client:
            return await client.call_tool(name, arguments or {})

    return _run_guarded(_call(), provider)
