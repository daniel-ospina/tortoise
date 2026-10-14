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
import tempfile
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


#: The self-hosted deployment's data root — the container path the image
#: creates (`Dockerfile.selfhost`) and `docker-compose.yml` mounts a named
#: volume on. The hosted lane has the same role for `/data` (the Fly volume).
_SELFHOST_DATA_ROOT = "/data"

#: Reasons already logged by :func:`ensure_selfhost_event_log`, so a per-request
#: caller does not re-log the same refusal on every SDK construction.
_SELFHOST_JOURNAL_SIGNALLED: set[str] = set()

#: The arming VERDICT per data root, memoized for the process. The self-host
#: doors call :func:`ensure_selfhost_event_log` lazily (per request) as well as
#: at daemon start, and the answer cannot change within a process — an fd/ls
#: probe on every request would be waste, and the second pass is also what made
#: the probe RACY (two first requests both unlinking the fixed probe name).
_SELFHOST_JOURNAL_ARMED: dict[str, str | None] = {}

#: Explicit base dirs whose writability probe already ran in this process. The
#: SHIPPED compose deployment sets ``TORTOISE_EVENT_LOG_BASE_DIR`` explicitly,
#: so the EXPLICIT branch — not the default-arm branch — is the per-request
#: path: every ``/v1/*`` request, every self-host MCP tool call and every
#: readiness probe reaches it. Without this set the explicit branch re-ran
#: ``makedirs`` + ``mkstemp`` + ``unlink`` on the journal volume each time
#: (review of #8526, P2). Keyed by VALUE, so a changed env re-probes.
_SELFHOST_JOURNAL_PROBED: set[str] = set()


def _parse_mountinfo(text: str) -> frozenset[str]:
    """The mount-point field of every line of ``/proc/self/mountinfo``.

    ``mountinfo``'s format is ``mount-id parent-id major:minor root
    mount-point options [optional fields…] - fstype source super-options``, so
    field 4 (0-indexed) is the mount point, with space/tab/newline/backslash
    escaped as ``\\040`` / ``\\011`` / ``\\012`` / ``\\134``.
    """
    points: set[str] = set()
    for line in text.splitlines():
        fields = line.split(" ")
        if len(fields) < 5:
            continue
        point = fields[4]
        for escaped, raw in (("\\040", " "), ("\\011", "\t"),
                             ("\\012", "\n"), ("\\134", "\\")):
            point = point.replace(escaped, raw)
        points.add(os.path.realpath(point))
    return frozenset(points)


def _mount_points() -> frozenset[str]:
    """Mount points named by ``/proc/self/mountinfo`` (Linux); empty elsewhere."""
    try:
        with open("/proc/self/mountinfo", encoding="utf-8",
                  errors="replace") as handle:
            return _parse_mountinfo(handle.read())
    except OSError:  # pragma: no cover — absent on macOS/BSD
        return frozenset()


def _persistent_dir(path: str) -> bool:
    """True when ``path`` is a MOUNT POINT — a dir that survives the container.

    ``os.path.isdir`` alone is NOT the signal, and using it would reopen the
    very defect #8497 closes: `Dockerfile.selfhost` creates ``/data`` at BUILD
    time, so every container from that image has a writable ``/data`` on its
    ephemeral layer. A journal armed there LOOKS like a record and dies with
    the container — the "partial record" state, indistinguishable from a real
    one. A mount point (a compose named volume, a bind mount, a Fly volume) is
    the only shape that survives, so it is the gate.

    TWO probes, because one is not enough. ``os.path.ismount`` compares
    ``st_dev`` with the parent, so it catches every mount whose device differs
    (the compose named volume, ``docker run -v``, a Fly volume). A SAME-
    FILESYSTEM bind mount (``mount --bind /srv/tortoise /data`` on a bare-metal
    host, or a systemd mount unit on the same partition) has ``st_dev`` equal
    to its parent's and is invisible to it — so the mount table is consulted
    too (#8526 review P2/P3-2).
    """
    try:
        if os.path.ismount(path):
            return True
    except OSError:  # pragma: no cover — ismount does not raise today
        return False
    return os.path.realpath(path) in _mount_points()


def ensure_selfhost_event_log(*, data_root: str | None = None) -> str | None:
    """Arm the self-host deployment's per-graph JSONL journal (#8497).

    Returns the journal base dir when one is armed, else ``None``.

    WHY THIS EXISTS. The self-hosted instance is a SHIPPED deployment that
    claims ``derived = replay(journal)`` exactly as the hosted lane does, but it
    has no equivalent of the hosted ``entrypoint.sh``: nothing defaults
    ``TORTOISE_EVENT_LOG_BASE_DIR``, so ``_resolve_event_log_path`` returns
    ``None`` for every org graph, the SDK is built with no ``event_log_path``,
    ``_journal_backed`` stays False and §17.1's single-writer guard is INERT on
    a shipped deployment (`docs/architecture/STORAGE-ARCHITECTURE.md:365` — *"a
    wiring gap, not a design gap"*). This closes the wiring gap with the SAME
    single derivation #4240 built: it only SETS the env var, so every lane that
    already calls ``_resolve_event_log_path`` (``mcp_auth._get_org_sdk`` for the
    MCP door, ``selfhost_api._sdk`` for the REST door) gets a real journal and
    the derivation stays in exactly one place.

    ⚠️ The journal is the REBUILD source, never the durability mechanism
    (``docs/durability-posture.md``; the ``#2881`` collision is CLOSED by
    scoping, §14.1 O1). Arming it does not change the self-hosted durability
    row: on-box AOF, no off-box copy (#2880).

    HONEST DEGRADE — it never raises, never exits, and never writes to a
    fallback location:
      * an EXPLICIT ``TORTOISE_EVENT_LOG_BASE_DIR`` always wins (the operator's
        choice, the pre-existing contract) and is returned unchanged — it is
        still probed, so an unwritable one is a loud boot ERROR rather than a
        surprise on the first write (the probe never overrides the value:
        appends stay fail-soft, counted and receipt-disclosed);
      * otherwise the default ``<data_root>/events`` is armed only when
        ``data_root`` is a persistent mount (``_persistent_dir``);
      * a default that cannot be created/probed writable leaves the env UNSET
        and logs a loud ERROR — the instance is journal-less AND says so,
        rather than pretending to record. A broken install must keep serving.
      * a root that EXISTS as a directory but is not a detected persistent mount
        arms nothing and says so ONCE (a WARNING naming the root) — that is the
        shape a bare `docker run` and a same-filesystem bind mount both land on,
        and silence there is indistinguishable from a correctly journal-less
        dev lane (#8526 review P2/P3-2).
    """
    explicit = os.environ.get(_HOSTED_EVENT_LOG_ENV)
    if explicit:
        # Probe ONCE per VALUE per process. This is the SHIPPED compose path
        # (`docker-compose.yml` sets the var), and the callers are per-request
        # — an un-memoized probe is a real `makedirs` + `mkstemp` + `unlink` on
        # the journal volume on every REST request, MCP tool call and readiness
        # probe, which is exactly the cost `_SELFHOST_JOURNAL_ARMED`'s own
        # docstring says the memo exists to avoid (#8526 review P2).
        if explicit not in _SELFHOST_JOURNAL_PROBED:
            _SELFHOST_JOURNAL_PROBED.add(explicit)
            _probe_journal_dir(explicit)
        return explicit
    root = _SELFHOST_DATA_ROOT if data_root is None else data_root
    if root in _SELFHOST_JOURNAL_ARMED:
        cached = _SELFHOST_JOURNAL_ARMED[root]
        if cached is not None:
            # Re-establish the env the arm wrote. The memo is consulted only
            # when the var is UNSET, and something outside this function can
            # clear it after the arm (a test's monkeypatch teardown, an ops
            # harness) — returning the cached base WITHOUT restoring the var
            # made this function report "armed" while
            # `_resolve_event_log_path` (the ONE derivation every door reads)
            # resolved ``None``: a journal-less SDK built behind a claim that a
            # journal was armed, with no signal (#8526 review P3).
            os.environ[_HOSTED_EVENT_LOG_ENV] = cached
        return cached
    if not _persistent_dir(root):
        # No persistent volume → no default. The dev/CI/test lane and the
        # ephemeral `docker run` shape stay journal-less by design (the #4240
        # boundary the issue's OVERRIDES marker keeps for embedded/dev/CI).
        # An operator who wants a journal off the default path sets the env
        # var.
        #
        # BUT a root that EXISTS as a directory and is still not a mount is
        # the ambiguous shape — `Dockerfile.selfhost` bakes `/data`, so a bare
        # `docker run` lands here, and a same-filesystem bind mount lands here
        # too. Staying silent there is indistinguishable from a correctly
        # journal-less dev lane (#8526 review P2/P3-2), so it is said once per
        # root rather than nothing.
        if os.path.isdir(root):
            reason = f"no-persistent-mount:{root}"
            if reason not in _SELFHOST_JOURNAL_SIGNALLED:
                _SELFHOST_JOURNAL_SIGNALLED.add(reason)
                logger.warning(
                    "self-host journal: no default armed under %s — it is a "
                    "directory but not a detected persistent mount, so a "
                    "journal there would die with the container. The instance "
                    "runs with NO journal: `derived = replay(journal)` is "
                    "false and `rebuild` has nothing to replay. Mount a "
                    "volume on %s, or set %s to a persistent path.",
                    root, root, _HOSTED_EVENT_LOG_ENV,
                )
        _SELFHOST_JOURNAL_ARMED[root] = None
        return None
    base = os.path.join(root, "events")
    if not _probe_journal_dir(base):
        _SELFHOST_JOURNAL_ARMED[root] = None
        return None
    os.environ[_HOSTED_EVENT_LOG_ENV] = base
    # Register the value the DEFAULT arm just wrote: it is now the env var, so
    # every later call takes the EXPLICIT branch, and without this the arm would
    # re-probe the very directory it just probed once (#8526 review P2 —
    # `_SELFHOST_JOURNAL_PROBED` is keyed by value for exactly this hand-off).
    _SELFHOST_JOURNAL_PROBED.add(base)
    _SELFHOST_JOURNAL_ARMED[root] = base
    return base


def _probe_journal_dir(base: str) -> bool:
    """True when ``base`` can hold records: created, and provably writable.

    The probe file is UNIQUE (``mkstemp``), not the hosted lane's fixed
    ``.write-probe`` name: two self-host doors arming concurrently would
    otherwise have one thread's ``unlink`` hit the other's already-removed
    probe and read a writable journal as unwritable. The temp file is created
    and removed inside ``base`` so the check is a real write on the real
    filesystem — ``mkdir -p`` alone succeeds on an existing, unwritable dir.

    Never raises: a failed probe is a REFUSAL (reported by the caller), and an
    unwritable journal must degrade honestly rather than break the install.
    """
    try:
        os.makedirs(base, exist_ok=True)
        fd, probe = tempfile.mkstemp(dir=base, prefix=".write-probe-")
        os.close(fd)
        os.unlink(probe)
    except OSError as exc:
        reason = f"{base}:{type(exc).__name__}"
        if reason not in _SELFHOST_JOURNAL_SIGNALLED:
            _SELFHOST_JOURNAL_SIGNALLED.add(reason)
            logger.error(
                "self-host journal unavailable — cannot record under %s (%s). "
                "The instance runs with NO journal: `derived = replay(journal)` "
                "is false, `rebuild` has nothing to replay, and the §17.1 "
                "single-writer guard has nothing to enforce. Fix the volume's "
                "permissions (or point %s at a writable directory) — this is a "
                "REBUILDABILITY loss, not a durability change (docs/durability-"
                "posture.md).",
                base, exc, _HOSTED_EVENT_LOG_ENV,
            )
        return False
    return True


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
