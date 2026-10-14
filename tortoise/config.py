"""Tortoise configuration — canonical embedded DB path resolution.

Child 2 (issue #176): unify all embedded connection points on ONE canonical
path so redislite's native path-keyed reuse works (one redis-server per
machine instead of one per connection/path).

Env precedence (plan Task 6):
  1. TORTOISE_DB_URI with a supported scheme (docker://, redis://, rediss://)
     -> URI mode (resolve_db_path() is NEVER called — the caller handles
     the URI separately; see SUPPORTED_URI_SCHEMES)
  2. TORTOISE_DB_PATH env -> file path (canonical for embedded)
  3. TORTOISE_DB_URI without a supported scheme -> treated as a file path
     (backward compat)
  4. default ~/.tortoise/tortoise.db

When both a non-URI TORTOISE_DB_URI value and PATH are set, PATH wins with
an explicit warning.
Empty/whitespace TORTOISE_DB_PATH falls through to the default (never passes
"" to FalkorProjection).
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = os.path.join(os.path.expanduser("~"), ".tortoise", "tortoise.db")

# Shared error message for relative-path rejection (plan Task 7). All call
# sites (FalkorProjection hard-reject, ingest.py pre-check, mcp_server error
# surface) reference this constant so they cannot drift.
RELATIVE_PATH_ERROR = (
    "Relative DB path {path!r} rejected. Use (1) the canonical path "
    "(no-arg FalkorProjection() or TORTOISE_DB_PATH), (2) an absolute path, "
    "or (3) allow_nonstandard_path=True (env TORTOISE_ALLOW_NONSTANDARD_PATH=1) "
    "for absolute non-canonical paths. Relative paths are never permitted."
)


# Canonical set of supported TORTOISE_DB_URI schemes — the single source of
# truth for URI-vs-path routing. docker:// (local instance), redis:// /
# rediss:// (FalkorDB Cloud / managed instances). Keep in sync with
# projection._validate_uri_scheme, which REUSES this tuple so the routing
# checks and the connection layer cannot drift (#715: rediss:// was
# documented-supported but _resolve_db_target only recognized docker://).
SUPPORTED_URI_SCHEMES = ("docker", "redis", "rediss")

# Epic #1647 (cycle-7 P1-3): the SINGLE shared loopback host set — the
# URI-aware test redirect (projection._is_loopback_host DELEGATES here), the
# Task 4 session-start tripwire, and wipe_server all resolve the SAME host
# set so the fail-before-first-write chain cannot diverge (pinned by
# test_loopback_predicate_single_source).
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


#: #4240 — base dir for the hosted lane's per-graph JSONL rebuild journal.
#: Unset ⇒ **no journal** (byte-identical to pre-#4240: the lane journals
#: nothing). Set ⇒ every hosted data SDK is built with an ``event_log_path``,
#: so the JSONL-only reassembly records (``EntityLinked``, ``SessionRecorded``,
#: ``ObjectRegistered``, ``PointAdded``, ``EntityMutated``) actually land and
#: ``rebuild_all`` / ``rebuild`` / ``recover_from_log`` can reconstruct the
#: capture — closing the #2296 live-only-edge hazard for this lane.
#: ⚠️ The JSONL is a DOMAIN EVENT LOG, **never the durability mechanism**
#: (``docs/durability-posture.md``): it makes the derived graph REBUILDABLE,
#: it does not keep it alive.
#: ⚠️ Records are fail-soft at the write site (a failed append is counted and
#: logged, never raised — the graph mutation already succeeded). The deployed
#: guard is ``entrypoint.sh``'s boot-time WRITABILITY probe (not just
#: ``mkdir -p``): a base dir that exists but cannot be written fails the boot
#: loudly for a NON-ROOT uid (mode bits), and for EROFS/ENOSPC on ANY uid
#: (root cannot bypass those). It does NOT cover a mode-bit failure when the
#: server runs as root — ``Dockerfile.hosted`` sets no ``USER``, so on Fly the
#: process is uid 0 and the probe write succeeds on a mode-0500 dir. That case
#: is covered by the runtime signal, not the boot: a failed append logs at
#: ERROR, increments ``tortoise_journal_write_failures_total`` and is disclosed
#: on the capture receipt (``TortoiseSDK._emit_event``). A journal that starts
#: failing AFTER that probe (the foreseeable trigger is ENOSPC once the journal
#: grows below the volume's free space) is recorded, not fatal — see #5612 and
#: ``TortoiseSDK._emit_event``.
_HOSTED_EVENT_LOG_ENV = "TORTOISE_EVENT_LOG_BASE_DIR"


#: The graph-name charset ``TortoiseSDK.__init__`` enforces (namespace and
#: graph_name alike). The journal key must satisfy it or it can escape base.
_JOURNAL_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}$")


def _resolve_event_log_path(*, namespace: str | None,
                            graph_name: str | None) -> str | None:
    """Per-graph JSONL rebuild journal path for the hosted lane (#4240).

    ONE file per graph under ``TORTOISE_EVENT_LOG_BASE_DIR``, so a rebuild of a
    graph replays exactly its own journal and two orgs can never share one. The
    key is the SDK's OWN derived graph name (``sdk._derive_graph_name``) — the
    ONE derivation of namespace/graph_name → graph, so the journal can never
    name a graph the SDK did not open. A local re-derivation is the divergence
    class the #4240 review found: the branches are not obvious
    (``namespace="test-foo"`` opens ``test_foo_tortoise``, NOT ``org_test-foo``).

    Returns ``None`` when the base dir is unset — no journal (the pre-#4240
    behaviour; embedded/dev/CI stay journal-less) — and for the registry
    control plane / the unnamed default graph, which are not org data graphs
    and are never rebuilt from this journal.

    Charset: the key is validated against the SDK's OWN graph-name rule and a
    violation RAISES. Sanitizing instead would silently key the journal to a
    directory no graph uses — and the prior guard kept ``.``, so a ``..`` key
    survived until ``TortoiseSDK.__init__`` rejected it, i.e. the stated
    anti-traversal guard was dead.

    ⛔ This lives HERE, not in ``hosted_api``, because of what an *import* of
    ``hosted_api`` costs: it builds the whole FastAPI app at module scope
    (~1 s) and, more importantly, its presence in ``sys.modules`` IS the
    runtime deployment discriminator every self-host branch reads
    (``mcp_server.tortoise_pack_install``, ``operator_alert.alert_store``,
    ``mcp_server``'s own fast-path invariant). The MCP lane must resolve its
    journal without importing the hosted app; ``hosted_api`` re-exports this
    name for its own lane and the existing callers, so there is still exactly
    ONE derivation (#7951 review P1).
    """
    base = os.environ.get(_HOSTED_EVENT_LOG_ENV)
    if not base:
        return None
    if graph_name is None and (not namespace or namespace == "registry"):
        return None
    # Function-level: ``sdk`` imports ``projection``, which imports THIS
    # module, so a module-level import would close the cycle. ``tortoise.sdk``
    # is already loaded by the time any caller here runs (mcp_auth/hosted_api
    # both import it), so this is a ``sys.modules`` lookup — and it is the
    # ONLY derivation, never a re-implementation.
    from tortoise.sdk import _derive_graph_name
    key = _derive_graph_name(namespace=namespace, graph_name=graph_name)
    if not _JOURNAL_KEY_RE.match(key):
        raise ValueError(
            f"refusing to build a journal path for unsafe key {key!r} "
            f"(namespace={namespace!r}, graph_name={graph_name!r}) — it is not "
            "a valid graph name and could escape the journal base dir")
    return os.path.join(base, key, "events.jsonl")


def is_loopback_uri(uri: str | None) -> bool:
    """True when the URI's hostname is a loopback host (localhost/127.0.0.1/::1).

    Fail-closed: an absent hostname (``docker://:pw@:6379``) or a bare IPv6
    literal parses to ``None`` and is NOT loopback — a session must never
    mint test graphs on an unintended default host (cycle-8 P2-1). Note the
    RFC 3986 rule: IPv6 literals must be bracketed (``[::1]``) to parse.
    """
    if not uri:
        return False
    from urllib.parse import urlparse
    return (urlparse(uri).hostname or "") in LOOPBACK_HOSTS


def is_db_uri(uri: str | None) -> bool:
    """True if a value is a supported connection URI (docker://, redis://,
    rediss://) rather than a file path."""
    if not uri:
        return False
    scheme = uri.split("://", 1)[0]
    return scheme in SUPPORTED_URI_SCHEMES


def raw_uri_userinfo(uri: str) -> tuple[str | None, str | None]:
    """Return a URI's ``(username, password)`` in the percent-ENCODED form.

    The RAW counterparts of :func:`parse_uri_userinfo` — exactly what
    ``urlparse`` yields, with no :func:`unquote`. Consumers that must compare or
    scrub the credential *as the URI wrote it* (a log redactor covering a client
    that echoes its DSN in either form) need this pair alongside the decoded one.

    It lives here, beside the decoding rule, so a module that also CONSTRUCTS a
    client can obtain the raw pair WITHOUT earning a raw-userinfo exemption from
    the #3039 source guard: the guard's ``continue`` is file-wide, so exempting
    such a module would blind it to the very bug class it exists to catch
    (#3067). This module is already the one sanctioned reader.

    Absent/empty components stay ``None`` (the client-default sentinel), the same
    shape as :func:`parse_uri_userinfo`. ``urlparse`` yields ``''`` for an empty
    userinfo component (``docker://:pw@host`` — the canonical credentialed form
    in this repo), so the fields are normalised with ``or None`` here.
    """
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    return parsed.username or None, parsed.password or None


def parse_uri_userinfo(uri: str) -> tuple[str | None, str | None]:
    """Return a connection URI's ``(username, password)``, percent-DECODED.

    The SINGLE decoding rule for URI credentials (#3039).
    ``urllib.parse.urlparse`` does NOT percent-decode userinfo, while
    ``redis.from_url`` DOES (it applies :func:`unquote` to both fields).
    Any code that hands a credential from a ``urlparse`` result to a client
    constructor must decode here: otherwise a password needing
    percent-encoding (``@``, ``:``, ``/``, ``?``, ``#``, ``%`` …) reaches
    the client as a literal ``%XX`` and authentication fails with a
    misleading "invalid username-password pair" error.

    ``unquote`` — NOT ``unquote_plus``: in the RFC 3986 userinfo component
    a ``+`` is a literal plus, not an encoded space.

    Absent or empty components return ``None`` (the client-default
    sentinel): ``docker://:pw@host`` carries an anonymous (empty) username
    and ``docker://host`` carries no userinfo at all — both stay ``None``
    so callers keep the ``or None`` semantics they had.
    """
    from urllib.parse import unquote
    username, password = raw_uri_userinfo(uri)
    return (
        unquote(username) if username else None,
        unquote(password) if password else None,
    )


def resolve_db_path(explicit: str | None = None) -> str:
    """Resolve the canonical embedded DB path with explicit precedence.

    Args:
        explicit: caller-provided path (e.g. from CLI --db). Wins if present.

    Returns an absolute path string. Raises nothing — falls through to the
    default on empty/whitespace env values.
    """
    if explicit:
        # #715 P2 conf 75: a supported URI is NOT a path — resolving it as
        # one mangles the string into a garbage "path" and the caller
        # silently misses the real target. Route URIs through
        # FalkorProjection.from_uri() instead (this pre-check keeps the
        # failure loud, never silent).
        if is_db_uri(explicit):
            raise ValueError(
                f"{explicit.split('://', 1)[0]}:// DB URI passed to "
                f"resolve_db_path — route it through "
                f"FalkorProjection.from_uri() instead")
        return _abs(explicit)

    # 2. TORTOISE_DB_PATH env -> file path
    env_path = os.environ.get("TORTOISE_DB_PATH")
    if env_path is not None and env_path.strip():
        return _abs(env_path.strip())
    if env_path is not None and not env_path.strip():
        logger.warning(
            "TORTOISE_DB_PATH is empty/whitespace — using default %s",
            DEFAULT_DB_PATH)

    # 3. TORTOISE_DB_URI without a supported URI scheme -> treated as file
    # path (backward compat). Supported schemes (docker://, redis://,
    # rediss://) are handled by the caller via is_db_uri / from_uri — they
    # fall through to the default here, mirroring the docker:// behavior.
    uri = os.environ.get("TORTOISE_DB_URI")
    if uri and not is_db_uri(uri):
        # Reject relative paths BEFORE _abs() normalizes them — otherwise
        # FalkorProjection's hard-reject is defeated (plan Task 7).
        expanded = os.path.expanduser(uri)
        if not os.path.isabs(expanded):
            raise ValueError(RELATIVE_PATH_ERROR.format(path=uri))
        logger.warning(
            "TORTOISE_DB_URI=%r is a file path (no supported scheme: %s) — "
            "treating as embedded DB path (backward compat)",
            uri, ", ".join(f"{s}://" for s in SUPPORTED_URI_SCHEMES))
        return _abs(uri)

    # 4. default
    return _abs(DEFAULT_DB_PATH)


def is_docker_uri(uri: str | None) -> bool:
    """True if a TORTOISE_DB_URI value is a docker:// connection string.

    Legacy docker-only check — kept for callers with docker-specific
    semantics (migrate_kinds, ingest pre-checks). New URI-routing code
    should use is_db_uri() so redis:// and rediss:// are recognized too.
    """
    return bool(uri and uri.startswith("docker://"))


def _abs(path: str) -> str:
    """Expand ~ and make absolute.

    REJECTS relative paths (plan Task 7): a relative path like 'tortoise.db'
    resolves per-CWD and silently creates a per-directory redislite server
    (Category-3 leak). This guard in the single choke-point covers ALL
    branches (explicit arg, TORTOISE_DB_PATH, TORTOISE_DB_URI) uniformly.

    Exceptions: ``:memory:`` (redislite in-memory server, not a file path)
    passes through untouched.
    """
    if path == ":memory:":
        return path
    expanded = os.path.expanduser(path)
    if not os.path.isabs(expanded):
        raise ValueError(RELATIVE_PATH_ERROR.format(path=path))
    return os.path.abspath(expanded)


def canonical_path_exists() -> bool:
    """True if the canonical embedded DB file already exists."""
    return Path(resolve_db_path()).exists()
