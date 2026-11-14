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
    (``operator_alert.alert_store``, ``mcp_server``'s own fast-path invariant —
    and, until #11609 batch 1 archived it, ``mcp_server.tortoise_pack_install``'s
    HOSTED-only stub). The MCP lane must resolve its
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


def looks_like_uri_userinfo(value: str) -> bool:
    """Does a SCHEME-LESS DB target carry URI USERINFO? (#10655 review P0)

    ONE predicate, shared by the two redaction sites that must agree: this
    module's `resolve_db_path` log line and `__main__`'s `config show` report.
    They were two hand-written copies, and both leaked the same shape.

    Three arms. Each one exists because the arm before it missed a measured
    shape, and the arms must stay in this order:

    * **TWO OR MORE LEADING SLASHES** — the value still wears an AUTHORITY
      (``//userinfo@host/…``) or the remnant of one after `os.path.abspath`
      (``///userinfo@host/…``). Nothing else can occupy that position, so any
      ``@`` there is userinfo. ⚠ The RAW value is what `resolve_db_path` redacts,
      and it keeps all three slashes while the REPORT sees the collapsed path —
      an arm that required ``@`` inside the first ``/``-delimited segment missed
      the raw form, and a single-segment heuristic missed it too.
    * **A COLON BEFORE THE LAST ``@``** — the ``user:password@`` tell, and it
      spans a raw ``/`` in the password, which is why a `/`-boundary arm lost
      ``//u:p/ss@host/x.db`` while the display masker redacted it.
    * **NO DIRECTORY ABOVE THE ``@``** — the colon-less twin: a token used as
      userinfo (``//token@host/x.db``) reaches the report as
      ``/token@host/x.db``, which has a single segment before the ``@``. A real
      absolute path with an ``@`` in it has a directory there
      (``/tmp/me@work/db.fdb``), and that is the distinction both are pinned on.

    Deliberately NOT a bare ``@`` test: `_mask_display_target` fails closed on any
    ``@`` in a scheme-less value, but `main` pins an absolute path with an ``@``
    as a LEGIBLE path (`test_the_graph_store_classifies_the_target_instead_of_
    crying_wolf`, `test_the_graph_target_keeps_the_maskers_fail_closed_rule`), and
    that legibility is what a report exists for. The two rules agree on every
    credential shape; this predicate adds the path-shaped exemption on top.

    Residual, stated rather than discovered later: a COLON-LESS token embedded
    under a real directory (``TORTOISE_DB_PATH=/srv/db/token@host/x.db``) is
    indistinguishable from a legal path by any lexical tell, so it is treated as
    one. A collapsed authority cannot take that shape (`abspath` leaves exactly
    one leading slash), so this is only reachable by hand-writing a token into a
    path — not by the copy-pasted DSN this predicate exists for.
    """
    if "@" not in value:
        return False
    if value.startswith("//"):
        return True
    before = value.rpartition("@")[0]
    if ":" in before:
        return True
    return "/" not in before.lstrip("/")


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


#: The keys the MCP DAEMON's ladder actually reads — and nothing else.
#: ⛔ The legacy `FALKORDB_*` trio is deliberately ABSENT: the CLI honours it
#: (`__main__._resolve_db_target`, a migration guard) and the daemon NEVER reads
#: it. That asymmetry is a documented fact of the two ladders, and a disclosure
#: that listed the trio as daemon-relevant would report a divergence where the
#: daemon simply ignores the file's key (#12407).
DAEMON_DB_KEYS = ("TORTOISE_DB_URI", "TORTOISE_DB_PATH")


class DaemonTargetError(ValueError):
    """A rejection from :func:`daemon_db_target`, carrying the RUNG it was on.

    ⛔ WHY THIS TYPE EXISTS (#12441 item 5). The disclosure has to answer "which
    key DECIDED the daemon's target" for two different questions — who to blame
    for a rejection, and which side of a divergence to name. Both used to be
    answered by a SECOND walk of ``DAEMON_DB_KEYS`` from ``__main__``, spelling
    the ladder's rule again as "first truthy key". That is faithful only while
    the ladder stays exactly that. The day it gains a rung which is not "first
    truthy" (a stripped URI, ``TORTOISE_ALLOW_EMBEDDED`` promoted above
    ``TORTOISE_DB_PATH``, a typed value), the attribution desyncs **silently**
    and nothing fails — the ladder is right and the report describes a resolver
    that no longer runs, which is the one failure this disclosure exists to
    prevent.

    So the resolver OWNS the answer: it attaches the key it was reading when it
    raised, and the report reads the attachment. A second spelling cannot drift
    out of step with the first if there is only one.

    It SUBCLASSES :class:`ValueError` so that every existing ``except ValueError``
    on this path keeps catching it — ``__main__``'s three call sites do, and so
    does the disclosure's own overlay arms, so no call site had to change.
    ⛔ ``mcp_server._get_sdk`` is NOT one of them: it calls this function with no
    local try/except, so a relative target propagates out of it exactly as a
    plain ``ValueError`` did before. The subclass adds no handler that did not
    exist, and no caller's CONTROL FLOW changes.

    ⛔ But it is not invisible, and an earlier version of this paragraph claimed
    it was "no caller's behaviour" (review, P3). Control flow is unchanged; what
    moves is what an UNCAUGHT exception renders — and, less obviously, the class
    name the MCP dispatcher records for a CAUGHT one: `mcp_server` puts an
    exception's class name into the machine-readable `error_kind` of its
    `mcp_tool_call` row (`_classify_mcp_call_error`), so a tool body raising
    through this type reports `DaemonTargetError` there where it reported
    `ValueError`, and a consumer keying on the old name must be taught the new
    one (measured: `_classify_mcp_call_error(DaemonTargetError("x", None))` →
    `('exec_error', 'DaemonTargetError')`). That is a recorded VALUE, not prose —
    so "only the traceback" was too narrow a second time (cycle-5 review).

    The traceback arm: ``serve``
    (``__main__`` → ``mcp_server.main()`` → ``_get_sdk()``, none of the three
    guarded) used to die ending at ``ValueError:``, and now dies ending at
    ``tortoise.config.DaemonTargetError:`` with the original ``ValueError:`` line
    chained ABOVE it by ``from exc``. Nothing is lost — the stable token is still
    in the output, so a runbook grep still matches — but the terminal line moved,
    and that is exactly the kind of user-visible drift ``_daemon_error_label``
    exists to prevent at the two disclosure render sites. It is NOT prevented
    here: a traceback names the class it was raised with, and papering over that
    would mean not raising the subclass at all. Accepted because the alternative
    — attaching ``.key`` to a plain ``ValueError`` instance — was ruled out by
    #12441 item 5's recorded durable form, which names ``DaemonTargetError`` as
    the carrier.
    """

    def __init__(self, message: str, key: str | None) -> None:
        super().__init__(message)
        #: The ``DAEMON_DB_KEYS`` entry the ladder was ON when it raised, or
        #: ``None`` in TWO states that are LIVE today — an earlier version of
        #: this comment called ``None`` unreachable and "verified by review",
        #: and both halves were false (measured; review cycles 2, 4 and 5):
        #:
        #: (a) THE RUNG HAD A VALUE THE RESOLVER DISCARDS. A whitespace-only
        #: ``TORTOISE_DB_PATH`` enters the path rung on TRUTHINESS, but
        #: ``resolve_db_path()`` STRIPS it and falls through to the embedded
        #: default — the rejected value is the process's own, so no key can be
        #: named. THIS TYPE IS CONSTRUCTED WITH ``None`` HERE, in `config.py`'s
        #: path rung, and every downstream arm reports ``unknown``.
        #:
        #: (b) THE REJECTION NEVER ENTERS THIS TYPE AT ALL. The
        #: ``embedded-allowed`` rung calls ``resolve_db_path()`` UNWRAPPED, so a
        #: relative ``DEFAULT_DB_PATH`` (a uid with no passwd entry and no
        #: ``HOME``, where ``expanduser("~")`` stays literal) raises a PLAIN
        #: ``ValueError``; the disclosure reads its key with
        #: ``getattr(exc, "key", None)`` and also reports ``unknown``.
        #:
        #: So ``str | None`` is NOT future-proofing — both arms are live and each
        #: is pinned (`test_a_blank_path_rung_is_not_credited_with_the_default_it_
        #: fell_through_to`, `test_a_keyless_rejection_is_reported_as_unknown_origin`).
        self.key = key


#: Which :data:`DAEMON_DB_KEYS` entry each rung of :func:`daemon_db_target`
#: CONSUMES — the rung is the resolver's own answer, so the report cannot spell
#: the ladder's rule a second time (#12441 items 1 and 5).
#:
#: ⛔ WHY THIS TABLE EXISTS. The disclosure asks "which key decided the daemon's
#: target" in four places, and each answered it by walking ``DAEMON_DB_KEYS``
#: itself and restating the ladder as "the first key present". At the TWO
#: ASSERTION sites that walk was already WRONG, because it tested PRESENCE while
#: the ladder tests TRUTHINESS (``if uri:``): a `.env` carrying a BLANK
#: ``TORTOISE_DB_URI`` beside a shell ``TORTOISE_DB_PATH=/tmp/env-path.db``
#: resolved to the shell's path on the ``path`` rung, and the row still said
#: *"the file's TORTOISE_DB_URI resolves to it for the daemon too"* — naming a
#: value the ladder never read, in the surface whose whole purpose is truthful
#: disclosure (#12441 item 1). At the two MIXED (``_top``) sites the input was
#: already non-blank filtered, so the walk COINCIDED with the ladder there and no
#: state distinguished them; the walk went anyway because it re-spelt the ladder's
#: ORDER, which a new rung would silently desync. Both halves are why the answer
#: belongs to the resolver.
#:
#: ⛔ ``embedded-allowed`` maps to ``TORTOISE_DB_PATH`` deliberately: that rung is
#: reached only AFTER ``TORTOISE_DB_PATH`` was read and found FALSY — UNSET, or
#: EXPORTED EMPTY. ⛔ NOT whitespace-only: ``if os.environ.get("TORTOISE_DB_PATH"):``
#: is RAW truthiness, so ``'   '`` is truthy and takes the ``path`` rung (which
#: then falls back to the default inside :func:`resolve_db_path`). So DB_PATH did
#: NOT decide; the hatch did. It is therefore the ladder key the rung ANSWERS FOR,
#: never a claim that DB_PATH is set: a renderer that says "the ENVIRONMENT's
#: DB_PATH decides it" must not use this row for `embedded-allowed` at all, or it
#: invents a decider (and inventing the FILE as the decider instead is the
#: false-credit this table exists to remove). ``refuses`` consumed NEITHER key, and
#: ``None`` is not a rung at all (a caller that did not grade the daemon).
RUNG_DECIDING_KEY: dict[str, str | None] = {
    "uri": "TORTOISE_DB_URI",
    "file-uri": "TORTOISE_DB_URI",
    "path": "TORTOISE_DB_PATH",
    "embedded-allowed": "TORTOISE_DB_PATH",
    "refuses": None,
}


def rung_deciding_key(rung: str | None) -> str | None:
    """The ``DAEMON_DB_KEYS`` entry the rung CONSUMED — ``None`` when it consumed none.

    ⛔ NOT a second spelling of the ladder: the rung is :func:`daemon_db_target`'s
    own OUTPUT, so this cannot drift from the ladder — it can only be
    INCOMPLETE, and incompleteness is what
    ``test_every_rung_the_ladder_can_return_has_a_deciding_key`` fails on. An
    unmapped rung therefore degrades to "unknown origin" (``None``) instead of
    silently naming a key that did not run.
    """
    return RUNG_DECIDING_KEY.get(rung)


def daemon_db_target() -> tuple[str | None, str]:
    """The MCP daemon's DB target — ONE implementation, TWO consumers (#12407).

    `tortoise.mcp_server._get_sdk` is the daemon's real resolver, and the
    `config show` / `doctor` disclosure has to report *its* answer rather than
    the CLI's. They are DIFFERENT ladders, and the difference is substantive:

        | rung          | daemon (`_get_sdk`)                     | CLI (`_resolve_db_target`) |
        |---------------|-----------------------------------------|----------------------------|
        | supported URI | `from_uri`                              | same                       |
        | scheme-less URI | `resolve_db_path(uri)` — URI WINS     | `TORTOISE_DB_PATH` WINS    |
        | `FALKORDB_*`  | **never read**                          | honoured (migration guard) |
        | nothing set   | REFUSES to start (`main` → exit 1)      | embedded default           |

    So computing the daemon's target with the CLI's resolver invents a divergence
    the daemon does not have (`FALKORDB_*` only → reported "the daemon serves
    docker://…" when it refuses to start) and HIDES a real one (env
    `TORTOISE_DB_PATH=/a.db` + `.env` `TORTOISE_DB_URI=/b.db` → the daemon opens
    `/b.db`, the CLI `/a.db`, and the report said "the same graph target") —
    measured, PR #12428 review round 1. That is why this function exists, and why
    `_get_sdk` calls it: one implementation, so the report cannot describe a
    resolver that no longer runs.

    Returns ``(target, rung)``. ``target`` is ``None`` only for the ``refuses``
    rung, where the daemon has NO target because `mcp_server.main()` exits 1
    telling the user to set `TORTOISE_DB_URI` or `TORTOISE_DB_PATH`; for
    ``embedded-allowed`` (the `TORTOISE_ALLOW_EMBEDDED=1` test hatch) the target
    is the canonical embedded path the daemon then opens.

    Raises :class:`DaemonTargetError` (a ``ValueError``) exactly where the
    daemon's own resolution would (a relative or otherwise unpalatable
    `TORTOISE_DB_URI`/`TORTOISE_DB_PATH`), carrying the key the ladder was on.
    """
    uri = os.environ.get("TORTOISE_DB_URI", "")
    if is_db_uri(uri):
        return uri, "uri"
    if uri:
        # `_get_sdk`: "File path — use Lite mode (backward compat: bare
        # non-docker URI)". The URI wins; TORTOISE_DB_PATH is not consulted.
        try:
            return resolve_db_path(uri), "file-uri"
        except ValueError as exc:
            # ⛔ `from exc` keeps the original message and traceback intact for
            # every existing caller; only the TYPE gains the attribution.
            raise DaemonTargetError(str(exc), "TORTOISE_DB_URI") from exc
    if os.environ.get("TORTOISE_DB_PATH"):
        try:
            return resolve_db_path(), "path"
        except ValueError as exc:
            # ⛔ BLANK IS NOT "DECIDING AND REJECTED" FOR THIS RUNG (measured):
            # `resolve_db_path()` STRIPS a whitespace-only `TORTOISE_DB_PATH`,
            # logs it and falls THROUGH to the embedded default — so the value
            # that failed is the PROCESS's own, and attaching the key made the
            # disclosure say "the value it rejects is the one this `.env`
            # supplied" about a value the resolver discarded. `None` is the
            # honest answer; a non-blank value IS the one that failed and keeps
            # its key.
            # (For `TORTOISE_DB_URI` this guard is unnecessary, and the reason is
            # narrower than "blank is deciding": an EMPTY URI does not enter the
            # rung at all (`if uri:`), so the path rung decides; a WHITESPACE-ONLY
            # URI is rejected as a relative path — by `resolve_db_path` STEP 1
            # (`_abs` on the `explicit` argument the ladder passes), never
            # step 3 — and it IS the value that failed.)
            _path = os.environ.get("TORTOISE_DB_PATH", "")
            raise DaemonTargetError(
                str(exc), "TORTOISE_DB_PATH" if _path.strip() else None) from exc
    if os.environ.get("TORTOISE_ALLOW_EMBEDDED") == "1":
        return resolve_db_path(), "embedded-allowed"
    return None, "refuses"


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
        # #5109: CLI output is captured verbatim into a transcript, and this value
        # can carry a credential — in a query/fragment (`…/x.db?password=…`) or in
        # USERINFO at the head of an absolute value (`//u:pw@host/x.db`, the #2987
        # shape that drops the scheme but keeps the authority). Cut the query, then
        # apply the same userinfo rule the display maskers apply; the two halves are
        # both needed or one shape leaks while the other is redacted. The predicate
        # is the SHARED `looks_like_uri_userinfo`; it is CALLED, not imported as a
        # masker, precisely because `__main__` imports THIS module and a masker
        # import here would cycle.
        _shown = uri.split("?", 1)[0].split("#", 1)[0]
        # AUTHORITY FORM **or** USERINFO COLLAPSED INTO A PATH — one shared
        # predicate (`looks_like_uri_userinfo`), because this site and
        # `__main__`'s report used to be two hand-written copies of a
        # `startswith("//")` test and BOTH leaked `/user:pw@host/x.db`: the
        # value arrives ABSOLUTE here, and `os.path.abspath` collapses a leading
        # `///` to `/`, so the `//` test never fires. The predicate is the
        # masker's own rule (any `@` in a scheme-less value) — every narrower
        # version of it leaked a shape, see its docstring.
        if looks_like_uri_userinfo(_shown):
            _shown = "<redacted: userinfo-shaped>"
        logger.warning(
            "TORTOISE_DB_URI=%r is a file path (no supported scheme: %s) — "
            "treating as embedded DB path (backward compat)",
            _shown,
            ", ".join(f"{s}://" for s in SUPPORTED_URI_SCHEMES))
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
