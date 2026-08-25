"""Centralized session-shared embedded construction (#1012).

All `FalkorProjection(` / `FalkorDB(` construction for converted test files
lives HERE so those files contain zero raw constructions — one session-scoped
projection (ONE redislite server) serves the whole suite instead of one
server per test (the recurring #1005 leak driver).

Hermeticity comes from per-test `wipe()`, not per-test fresh paths — the
same pattern the shared_embedded_db users (test_ranking.py,
test_session_semantic_search.py) already follow.

Not converted here (deliberately): raw-layer tests whose construction IS the
test input (guard, hard-reject, reaper, chaos, lifecycle-close, config path
resolution, migrations, backup restore, flip-gate script integration) — see
RAW_EMBEDDED_ALLOWLIST in test_embedded_lifecycle.py.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile

import pytest

from tortoise.config import is_db_uri
from tortoise.projection import FalkorProjection

# Epic #1647 (plan-review P0-1): in-repo carve-out exemption list — the
# caller TEST-MODULE stems exempted from the URI-aware redirect via
# TORTOISE_TEST_NO_REDIRECT (conftest exports it from this constant so the
# product-side redirect reads the list without importing tests/). Exemption
# keys on the CALLER test module (frame-identified by _caller_test_stem),
# NEVER on the DB-file basename. The list is wired in Task 5; empty in P1
# (URI unset everywhere → the redirect is dormant).
# Task 5 (D-2/H1): the 7 half-b carve-out TEST-MODULE stems (the 6 from
# cycle 1 + test_smoke_embedded, cycle-2 P1-1a — tests/bench rides the P2
# flip via the push_extra distribution, so its URI job must keep the file
# embedded). Exempting the CALLER TEST MODULE means the carve-out files'
# constructions stay embedded under the P2 job-level URI — their embedded-
# specific assertions never flip to the server lane. The 3 busy-error tests
# are NOT here: they keep running embedded via the embedded_only marker
# (D-2=A), a separate mechanism from this redirect exemption.
# Task 9 (P3): expanded to the FULL 17-file carve-out set (cycle-3 P2-12:
# the old "16" undercounts — 7 Task-5 stems + 10 additions; fixtures/
# redis-guard/* are subprocess scripts, not test modules, and
# test_smoke_embedded is already one of the 7). At P3 the 17 files run in
# the DEDICATED URI-unset carve-out job (E2E-4); the exemption stays
# load-bearing for the P4 post-merge-validation full-tests/ run (Task 10
# Step 1a) and any tier-2/other docker surface that selects a carve-out
# file — its embedded-specific assertions must never flip to the server
# lane.
TEST_NO_REDIRECT_STEMS: tuple[str, ...] = (
    "test_backup_e2e",
    "test_config",
    "test_embedded_concurrency",
    "test_embedded_lifecycle",
    "test_embedded_lifecycle_fast_close",
    "test_flip_gate",
    "test_guard",
    "test_hard_reject",
    "test_hosted_backup",
    "test_migrate_db",
    "test_ops_safety",
    "test_pre_migration_safety",
    "test_projection_lifecycle",
    "test_reaper",
    "test_reaper_orphan",
    "test_redis_guard",
    "test_smoke_embedded",
)

_HAS_FALKOR: bool | None = None


def _uri_set_supported() -> bool:
    """Epic #1647: True when a supported TORTOISE_DB_URI is set.

    The seam-fixture URI branch predicate (plan-review P2-16): under a
    supported URI the redirect flips embedded constructions to the server,
    so the fixtures construct server-mode directly and has_falkor()
    short-circuits (the embedded probe would redirect, mint a test graph on
    the server, and misreport backend availability).
    """
    return is_db_uri(os.environ.get("TORTOISE_DB_URI"))


def has_falkor() -> bool:
    """Runtime probe: is embedded FalkorDBLite usable on this machine?

    Mirrors the historical per-file probes (issue #82 — redislite interprets
    some paths as hostnames → idna UnicodeEncodeError). Centralized so
    converted files do not construct a raw client just to probe.

    Epic #1647 (P2-16): under a supported TORTOISE_DB_URI the probe is
    SKIPPED and True is returned immediately — the probe would construct a
    projection, which would redirect (calling test frame + URI + TEST_MODE)
    and mint a derived test_<stem>_<hash> graph on the server while
    misreporting backend availability. Under URI the server IS the backend,
    so skip_if_no_falkor() returns False and migrated files never
    vacuous-return.
    """
    global _HAS_FALKOR
    if _uri_set_supported():
        return True
    if _HAS_FALKOR is None:
        try:
            from redislite.falkordb_client import FalkorDB  # noqa: F401
            db_path = os.path.join(
                tempfile.mkdtemp(prefix="tortoise_probe_"), "probe.db")
            proj = FalkorProjection(db_path, graph_name="test")
            proj.close()
            _HAS_FALKOR = True
        except Exception:
            _HAS_FALKOR = False
    return _HAS_FALKOR


def skip_if_no_falkor() -> bool:
    """DEPRECATED (epic #1647 Task 9, P3): True when embedded FalkorDBLite
    is unavailable.

    Historical semantics: callers returned early on True (the vacuous-pass
    behavior). Task 9 retires the vacuous early-return from migrated files —
    callers now use a VISIBLE `pytest.skip("embedded FalkorDBLite
    unavailable ...")` (guard-exempt reason family) or fail-fast. Under a
    supported TORTOISE_DB_URI the probe short-circuits False (P2-16: the
    server IS the backend; the embedded probe would redirect and mint a
    server graph), so migrated docker-lane files never skip. Kept for
    back-compat with the remaining embedded-lane callers.
    """
    return not has_falkor()


def wipe(proj) -> None:
    """Wipe every graph in the shared embedded DB (hermeticity per test).

    EMBEDDED-ONLY: refuses to run against a server-mode (Docker/cloud)
    projection — this helper targets the session-shared TEST server, never a
    live registry. The shared DB serves multiple graphs
    (registry_control_plane, team_*), not just the projection's default, so
    ALL graphs are cleared. _GuardedGraph's test-graph assertion is NOT
    consulted here (raw db handle; and it returns early for embedded mode) —
    the embedded guard below is the actual protection.
    """
    if not getattr(proj, "_is_embedded", False):
        raise RuntimeError(
            "wipe() is for the session-shared EMBEDDED test server only — "
            "refusing to wipe a server-mode projection"
        )
    try:
        graphs = list(proj.db.list_graphs())
    except Exception:
        graphs = []  # list_graphs unknown on this backend — fall back below
    if not graphs:
        # list_graphs failure would silently narrow the wipe; the embedded
        # backend enumerates graphs reliably, so fall back to the projection
        # default and let the next test's exact-set assertions surface any
        # leak loudly.
        graphs = [getattr(proj, "_graph_name", "test")]
    for g in graphs:
        try:  # noqa: SIM105
            proj.db.select_graph(g).query("MATCH (n) DETACH DELETE n")
        except Exception:
            pass


# ── Epic #1647 Task 2: session journal + wipe_server + _wipe_or ───────────
# The session journal (FILE = single source of truth) records every graph
# name this session mints — the per-test wipe delta (created-since-last-wipe)
# and the session-end/stale/atexit drop sets both derive from it. The FILE
# journal is written by (a) the tests-side _journal_append (raw-client/test
# sites), (b) the product-side writer (_journal_append_product — the redirect
# + frame-gated from_uri seam, file-only), so the FILE is the ONLY structure
# any sweep reads (cycle-8 P1-2); the in-memory _JOURNAL is tests-side only.
_JOURNAL: list[str] = []
_WIPED_UP_TO = 0
_JOURNAL_FILE: str = os.environ.get("TORTOISE_TEST_JOURNAL_FILE", "") or ""

# ── Epic #1647 Task 4 (P2): session backend-identity record ────────────────
class BackendIdentity:
    """Which backend this session actually ran on (epic #1647 E2E-6).

    Recorded by the conftest session-start tripwire (_assert_backend_identity)
    so other conftest machinery (skip-guard wiring, manifest generation, the
    Task 5 embedded_only marker hook) can read the lane without re-probing.
    backend: "server" when the tripwire's redirect-traversing probe ran in
    server mode; "embedded" on URI-less sessions. uri: the TORTOISE_DB_URI
    the session ran with ("" on the embedded lane).
    """
    __slots__ = ("backend", "uri")

    def __init__(self, backend: str = "embedded", uri: str = ""):
        self.backend = backend
        self.uri = uri


BACKEND_IDENTITY = BackendIdentity()


# ── Epic #1647 Task 5 (D-2=A): the embedded_only marker hook ──────────────
def _embedded_only_skip(request: pytest.FixtureRequest) -> None:
    """D-2 skip hook for the `embedded_only` marker (epic #1647 Task 5).

    The 3 busy-error tests (test_audit (d) case, TestBackfillScript's
    dry-run test, test_index_directory E2E-9) keep running EMBEDDED because
    real FalkorDB's busy-error semantics differ — under a supported
    TORTOISE_DB_URI (the server lane) they SKIP VISIBLY with the
    embedded-only reason; on the embedded lane (URI unset) the marker is
    inert. Named + importable so test_markers.py pins the exact skip
    contract (cycle-5 P2-12): a visible skip whose reason carries
    "embedded-only" and never the "FalkorDB" substring (Task 3's skip-guard
    trip). Lives HERE (not conftest) so imports resolve through the cached
    tests._embedded module — a `from tests.conftest import` re-executes
    conftest's top-level code mid-session (pytest loads it as the top-level
    `conftest` module; the namespace-package tests.conftest import is a
    SECOND instance that overwrites TORTOISE_TEST_SESSION and re-points the
    journal — review P0). The lane gate is the seam predicate
    _uri_set_supported() (is_db_uri — the redirect's own gate family,
    P2-16): a set-but-unsupported URI keeps the marker inert (the redirect
    would not fire either).
    """
    if not _uri_set_supported():
        return  # embedded lane — the marker is inert
    if request.node.get_closest_marker("embedded_only") is None:
        return
    pytest.skip(
        "embedded-only: busy-error semantics are embedded-specific — the "
        "server lane has no cross-process busy concept (epic #1647 D-2)")

_GRAPH_NAME_RE = re.compile(r"[A-Za-z0-9_.\-]+")


def _journal_file() -> str:
    """Resolve the current journal file path (cycle-5 P2-15).

    The module attribute wins when set (tests monkeypatch `_JOURNAL_FILE` to
    drive the REAL file journal); unpatched sessions fall back to the env var
    exported by conftest at import (cycle-4 P2-9: resolved before any test
    module import so product-side module-import appends fire). Empty string
    (no test session) → appenders no-op, never fail.
    """
    if _JOURNAL_FILE:
        return _JOURNAL_FILE
    return os.environ.get("TORTOISE_TEST_JOURNAL_FILE", "") or ""


def _read_journal_file(path: str) -> list[str]:
    """Tolerant line parser (cycle-4 P2-3 / cycle-7 P2-8).

    The journal is one graph name per line, written with per-append
    open/write/close atomicity. The reader:
      - drops a final fragment lacking a trailing newline (a killed writer's
        torn write — the appender always writes name + "\n"),
      - stops at the first syntactically unparseable line (all prior lines
        honored),
      - treats a FIRST-LINE poison as an EMPTY journal and deletes the file
        (poison-file guard, mirroring the marker hygiene).
    """
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError:
        return []
    if not text:
        return []
    if not text.endswith("\n"):
        # A killed writer's torn final fragment (the appender always writes
        # name + "\n") is dropped BEFORE parsing — all prior complete lines
        # are honored (cycle-4 P2-3: a truncated final line is dropped, the
        # rest honored).
        text = text.rsplit("\n", 1)[0]
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()  # trailing newline
    if not lines:
        return []  # empty or torn-only journal
    names: list[str] = []
    for line in lines:
        if not line:
            break  # empty embedded line → unparseable boundary
        if not _GRAPH_NAME_RE.fullmatch(line) or len(line) > 256:
            break  # unparseable line — truncate, honor prior lines
        names.append(line)
    if not names and not _GRAPH_NAME_RE.fullmatch(lines[0]):
        # first line unparseable → poison file → empty + delete
        try:  # noqa: SIM105
            os.remove(path)
        except OSError:
            pass
    return names


def _read_journal() -> list[str]:
    """The FILE journal's contents (the single source of truth)."""
    return _read_journal_file(_journal_file())


def _read_wiped_cursor() -> int:
    """The persisted wipe cursor (sidecar `{_JOURNAL_FILE}.cursor`, cycle-7
    P1-4). Absent/unreadable sidecar → 0 (re-wipe from the journal start —
    correct, just not O(delta))."""
    path = _journal_file() + ".cursor"
    if not path or path == ".cursor":
        return 0
    try:
        with open(path) as fh:
            return int(fh.read().strip() or "0")
    except (OSError, ValueError):
        return 0


def _write_wiped_cursor(n: int) -> None:
    """Persist the wipe cursor. Best-effort — a lost cursor re-wipes more
    (correct); never fail a wipe over bookkeeping."""
    path = _journal_file() + ".cursor"
    if not path or path == ".cursor":
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(str(n))
    except OSError:
        pass


def _journal_append(name: str) -> None:
    """Tests-side journal appender: in-memory delta (tests-side only) PLUS
    the FILE journal — the single source of truth (cycle-7 P1-4 / cycle-8
    P1-2). The file is written directly through the same path resolution as
    the reader so a patched `_JOURNAL_FILE` is honored (cycle-5 P2-15: the
    wiring tests drive the REAL file journal). The product-side writer
    (tortoise.projection._journal_append_product) covers product seams
    (redirect/from_uri); in a normal session both write the same file.
    """
    _JOURNAL.append(name)
    path = _journal_file()
    if not path:
        return
    try:
        # cycle-7 P1-1: the parent dir may not exist (fresh ACTIVE_SUITES_DIR)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a") as fh:
            fh.write(name + "\n")
    except OSError:
        logging.getLogger(__name__).debug(
            "journal append skipped for %r (%r)", name, path)


def _created_since_last_wipe() -> set[str]:
    """The session's created-since-last-wipe delta (cycle-4 P0-1/P2-10).

    journal[cursor:] — the FILE journal is the source of truth, NOT the
    in-memory `_JOURNAL` (product-side redirect/from_uri mints are file-only,
    cycle-7 P1-4). The cursor is persisted as `{_JOURNAL_FILE}.cursor` and
    advanced by `_wipe_or` after each successful wipe (cycle-4 P2-10).
    """
    names = _read_journal()
    return set(names[_read_wiped_cursor():])


def _uri_default_graph_name() -> str | None:
    """The URI-path default graph name, or None when no URI is set
    (cycle-6 P2-12). `from_uri(uri)` without an explicit graph_name resolves
    the URI path (`parsed.path.lstrip('/') or "tortoise"`) — a SHARED graph
    every session uses. Per-test scopes must NEVER include it (it is swept
    only at session-end by the last suite standing)."""
    uri = os.environ.get("TORTOISE_DB_URI")
    if not uri:
        return None
    from urllib.parse import urlparse
    return urlparse(uri).path.lstrip("/") or "tortoise"


def _projection_host(proj) -> str | None:
    """Resolve the projection's server host (cycle-3 P0-1).

    Reads the host recorded on the projection by Task 1 (`proj._host`); the
    connection_pool fallback covers pre-seam constructions (redis-py 8.1.0
    keeps host in `connection_pool.connection_kwargs['host']` — the raw
    client has no `.host`)."""
    host = getattr(proj, "_host", None)
    if host is None:
        _conn = getattr(proj.db, "connection", None)
        _pool = getattr(_conn, "connection_pool", None) if _conn else None
        host = (_pool.connection_kwargs.get("host") if _pool else None) \
            or getattr(_conn, "_host", None)
    return host


def wipe_server(proj, scope: set[str] | None = None, drop: bool = False) -> None:
    """Server-mode hermeticity wipe (epic #1647, D-4).

    Enumerates list_graphs() and DETACH-DELETEs ONLY graphs named
    test_/tortoise_test_* — the guard-passing test-graph family. Every other
    graph is skipped, never wiped (fail-closed). Refuses non-loopback hosts
    (D-4): a test suite must never wipe a remote dev/shared server.

    scope (the session's created set, cycle-3 P1-7): when given, ONLY names
    in the scope are considered — a per-test wipe never blind-wipes another
    concurrent session's live graphs. scope=None is the server-global sweep,
    reserved for the session-end/last-suite-standing sweep ONLY.

    drop=True (cycle-4 P1-9): after DETACH, GRAPH.DELETE the wiped names via
    graph.delete() so server GRAPH.LIST stays bounded (E2E-7). Per-test
    wipes keep drop=False — graphs are reused across tests in a session.

    Per-graph failures are collected and re-raised (cycle-2 P2-7) — explicit
    wipes fail loud.
    """
    from tortoise.projection import _is_loopback_host  # shared predicate (P0-2)
    host = _projection_host(proj)
    if not _is_loopback_host(host):
        raise RuntimeError(
            f"wipe_server() refuses non-loopback host {host!r} — test wipes "
            f"are local-only (decision D-4)")
    failures: list[tuple[str, Exception]] = []
    dropped: list[str] = []
    default_graph = _uri_default_graph_name()
    for g in proj.db.list_graphs() or []:
        if scope is not None and g not in scope:
            continue  # cycle-3 P1-7: per-test wipes touch only the session's set
        # Cycle-8 P1-1: per-test scopes NEVER DETACH the shared URI-default
        # graph (the job-URI path, e.g. tortoise_test_matrix) — a frame-gated
        # from_uri append may journal it, and a per-session DETACH races
        # concurrent sessions' live writes. The session-end/last-suite-standing
        # sweep (scope=None → global) still owns it.
        if scope is not None and g == default_graph:
            continue
        if not g.startswith(("test_", "tortoise_test")):
            continue  # fail-closed: never wipe a non-test graph
        try:
            proj.db.select_graph(g).query("MATCH (n) DETACH DELETE n")
        except Exception as e:  # P2-7: collect + re-raise, never pass silently
            failures.append((g, e))
        else:
            if drop:
                dropped.append(g)
    if failures:
        raise RuntimeError(
            "wipe_server() failed on graph(s): " +
            "; ".join(f"{g}: {e!r}" for g, e in failures))
    for g in dropped:  # cycle-4 P1-9: DETACH-then-DELETE keeps GRAPH.LIST bounded
        try:
            # Cycle-6 P1-0 (FM-2): graph.delete() rides execute_command —
            # NEVER query("GRAPH.DELETE") (that transmits GRAPH.QUERY <g>
            # "GRAPH.DELETE" --compact, a Cypher parse error).
            proj.db.select_graph(g).delete()
        except Exception as e:
            # Cycle-5 P2-3: a graph already dropped by a concurrent suite
            # (last-suite-standing) or an earlier stale sweep is SUCCESS;
            # only genuine command errors collect.
            if _is_missing_graph_error(e):
                continue
            failures.append((g, e))
    if failures:
        raise RuntimeError(
            "wipe_server() GRAPH.DELETE failed on graph(s): " +
            "; ".join(f"{g}: {e!r}" for g, e in failures))


def _is_missing_graph_error(e: Exception) -> bool:
    """True when the error means the graph no longer exists (idempotent).

    Real server text (v4.16.7, empirically verified): GRAPH.DELETE on a
    missing graph raises "Invalid graph operation on empty key" — the
    patterns below cover it and the family of client-side variants."""
    s = str(e).lower()
    return any(k in s for k in ("graph not found", "no such graph",
                                "does not exist", "unknown graph",
                                "invalid graph operation", "empty key"))


def _wipe_or(proj, scope: set[str] | None = None) -> None:
    """Mode-dispatching hermeticity wipe (plan-review P0-2).

    Embedded projection → wipe(proj) (all-graphs, today's semantics). Server
    projection → wipe_server(proj, scope=scope) (test-prefix-filtered,
    loopback-only). Every migrated per-test wipe converts to this so the
    server-mode refusal never raises on the docker lane.

    Cycle-4 P0-1 (WIRING): when the caller passes NO scope (every converted
    call site), the scope DEFAULTS to `_created_since_last_wipe()` — the
    session's created-since-last-wipe delta — NEVER None (scope=None, true
    server-global, is reachable only by calling wipe_server directly — the
    session-end/last-suite-standing sweep path; cycle-8 P2-8). Cycle-5 P1-2:
    the default additionally unions {proj.graph_name} — a session-cached
    shared projection (journaled once) would otherwise drop out of the delta
    slice after the first wipe and never be re-wiped. Cycle-6 P2-12: the
    union EXCLUDES the URI-path default graph name.

    Cycle-4 P2-10: after a successful wipe the persisted cursor advances to
    the FILE journal's length, so the next per-test wipe is O(delta), not
    O(session) (cycle-7 P1-4: the FILE length, NOT len(_JOURNAL) — product
    mints are file-only).
    """
    if getattr(proj, "_is_embedded", False):
        wipe(proj)
        return
    if scope is None:
        scope = _created_since_last_wipe()  # cycle-4 P0-1: never None by default
        _gn = getattr(proj, "graph_name", None)
        if _gn:
            scope.add(_gn)  # cycle-5 P1-2: the caller means THIS projection's graph
        _uri_default = _uri_default_graph_name()
        if _gn == _uri_default:
            scope.discard(_gn)  # cycle-6 P2-12: never per-test DETACH the default
    wipe_server(proj, scope=scope)
    _write_wiped_cursor(len(_read_journal()))


# ── Epic #1647 Task 2 Step 7: session-end / stale / atexit sweeps ──────────
# Shared by the conftest session fixture (URI lane). Sweep helpers take
# skip_on_non_loopback=True (cycle-4 P1-8): ALLOW_REMOTE sessions log-and-
# skip instead of raising — D-4's RuntimeError refusal is for EXPLICIT
# wipe_server()/wipe_or calls only. Failure policy (cycle-8 P2-3/P2-4):
# log-and-continue per graph; the journal file is removed ONLY when every
# journaled graph was dropped (keep-on-partial — the next session's stale
# sweep retries the remainder).


def _proj_for_uri(uri: str):
    """A host-mode projection for a URI, constructed WITHOUT from_uri so the
    frame-gated journal append never fires from sweep code."""
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    return FalkorProjection(
        host=parsed.hostname or "localhost",
        port=parsed.port or 16379,
        username=parsed.username or None,
        password=parsed.password or None,
        graph_name=f"test_sweep_{os.urandom(4).hex()}",
        ssl=(parsed.scheme == "rediss"),
        skip_health_check=True,
    )


def _sweep_proj(uri: str):
    """Context manager yielding a host-mode projection for sweep operations;
    best-effort cleanup deletes the probe graph so a sweep never leaves a
    mint behind (the projection's _ensure_indexes creates it)."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx():
        proj = _proj_for_uri(uri)
        try:
            yield proj
        finally:
            try:  # noqa: SIM105
                proj.db.select_graph(proj.graph_name).delete()
            except Exception:
                pass
            try:  # noqa: SIM105
                proj.close()
            except Exception:
                pass

    return _ctx()


def _drop_one_graph(proj, g: str, *, drop: bool) -> bool:
    """DETACH-then-DELETE one graph (cycle-4 P1-9 + cycle-6 P1-0:
    graph.delete() rides execute_command, never query("GRAPH.DELETE")).
    Log-and-continue on error (cycle-8 P2-3 — hygiene never fails the suite)."""
    try:
        proj.db.select_graph(g).query("MATCH (n) DETACH DELETE n")
        if drop:
            proj.db.select_graph(g).delete()
        return True
    except Exception as e:
        logging.getLogger(__name__).warning(
            "session sweep: drop of %r failed: %r", g, e)
        return False


def _remove_journal_file(journal_file: str) -> None:
    """Remove the journal AND its cursor sidecar (both are per-session
    bookkeeping — a removed journal without its sidecar leaves a stray
    .cursor file in ACTIVE_SUITES_DIR every docker session)."""
    for p in (journal_file, journal_file + ".cursor"):
        try:  # noqa: SIM105
            os.remove(p)
        except OSError:
            pass


def _sweep_drop(proj, journal_file: str, *, drop: bool = True,
                skip_on_non_loopback: bool = True) -> dict:
    """Drop the FILE journal's graph set on proj's server (cycle-8 P1-2:
    the drop set is the FILE journal — never the in-memory list).

    Per-graph DETACH + (drop=True) GRAPH.DELETE; failures log-and-continue;
    the journal file is removed ONLY when every graph dropped (cycle-8 P2-4
    keep-on-partial — a crashed/partial sweep cannot lose its own drop-set
    bookkeeping; the next session's stale sweep retries). Returns a summary
    dict {"dropped", "failed", "journal_removed"} or {"skipped": ...}.
    """
    from tortoise.projection import _is_loopback_host
    host = _projection_host(proj)
    if skip_on_non_loopback and not _is_loopback_host(host):
        logging.getLogger(__name__).info(
            "session sweep SKIPS non-loopback host %r — graphs preserved for "
            "the next session (ALLOW_REMOTE write-only escape, D-4)", host)
        return {"skipped": f"non-loopback {host!r}", "journal_removed": False}
    names = _read_journal_file(journal_file)
    default_graph = _uri_default_graph_name()
    dropped: list[str] = []
    failed: list[str] = []
    seen: set[str] = set()
    for g in names:
        if g in seen:
            continue  # duplicate entries (per-test seam re-appends) — idempotent
        seen.add(g)
        if g == default_graph:
            # Cycle-4 P2-2 / cycle-8 P1-1: the shared URI-default graph is
            # swept ONLY by the last-suite-standing full sweep (scope=None) —
            # a per-session own/stale drop would race other concurrent
            # sessions' live writes on the shared default.
            continue
        if _drop_one_graph(proj, g, drop=drop):
            dropped.append(g)
        else:
            failed.append(g)
    removed = False
    if not failed:
        _remove_journal_file(journal_file)
        removed = True
    return {"dropped": dropped, "failed": failed, "journal_removed": removed}


def _session_end_own_sweep(uri: str, journal_file: str, *,
                           skip_on_non_loopback: bool = True) -> dict:
    """Session-end sweep of THIS session's journaled graphs (Step 7 item 3)."""
    with _sweep_proj(uri) as proj:
        return _sweep_drop(proj, journal_file, drop=True,
                           skip_on_non_loopback=skip_on_non_loopback)


def _leftover_sweep(uri: str, *, skip_on_non_loopback: bool = True) -> dict:
    """LAST-suite-standing FULL sweep: every test-prefixed graph on the
    server (wipe_server scope=None → global, drop=True). Log-and-continue on
    errors — hygiene never fails the suite."""
    with _sweep_proj(uri) as proj:
        from tortoise.projection import _is_loopback_host
        host = _projection_host(proj)
        if skip_on_non_loopback and not _is_loopback_host(host):
            logging.getLogger(__name__).info(
                "leftover sweep SKIPS non-loopback host %r", host)
            return {"skipped": f"non-loopback {host!r}"}
        try:
            wipe_server(proj, scope=None, drop=True)
            return {"full_sweep": True}
        except Exception as e:
            logging.getLogger(__name__).warning(
                "leftover sweep failed: %r", e)
            return {"full_sweep": False, "error": str(e)}


def _stale_sweep(uri: str, *, skip_on_non_loopback: bool = True) -> dict:
    """Session-start stale sweep (Step 7 item 1): drop DEAD sessions'
    journaled graphs.

    Liveness (cycle-8 P2-7): a journal is LIVE iff its matching
    {pid}-{nonce} marker parses through active_suite_markers() as a LIVE
    marker (pid+start verified against the live process's start time — the
    recycled-pid guard, #1642 FIX 5). Bare marker-file EXISTENCE is NOT the
    liveness rule. Dead (no matching live marker) → drop its journaled
    graphs + remove the journal (keep-on-partial defers the remainder)."""
    from tortoise.embedded_reaper import ACTIVE_SUITES_DIR as _ASD
    from tortoise.embedded_reaper import active_suite_markers
    try:
        entries = os.listdir(_ASD)
    except OSError:
        return {"stale": []}
    live_nonces = {
        m["token"].split("-", 1)[1] for m in active_suite_markers()
        if m.get("token") and "-" in m["token"]
    }
    results: list[dict] = []
    for e in sorted(entries):
        if not e.endswith(".graphs.jsonl"):
            continue
        nonce = e[: -len(".graphs.jsonl")]
        if nonce in live_nonces:
            continue  # a live suite's journal — never touch
        jf = os.path.join(_ASD, e)
        with _sweep_proj(uri) as proj:
            res = _sweep_drop(proj, jf, drop=True,
                              skip_on_non_loopback=skip_on_non_loopback)
        results.append({"journal": e, **res})
    return {"stale": results}


@pytest.fixture(scope="session")
def shared_proj():
    """One session-scoped embedded projection (#1012).

    Replaces per-test `FalkorProjection(fresh-tmp-path)` construction in
    converted files: ONE redislite server for the whole session instead of
    one per test. Yields None when embedded mode is unavailable so callers
    keep the historical skip semantics (`if shared_proj is None: return`).

    Epic #1647 (D-1=A): URI-aware seam — when a supported TORTOISE_DB_URI is
    set, construct server-mode via from_uri with a guard-passing shared-tier
    graph name (test_suite_<uuid>) instead; the URI branch runs BEFORE
    has_falkor() (P2-16: the probe would redirect and mint a server graph).
    Unset → today's embedded construction unchanged (P1 zero-change).
    """
    if _uri_set_supported():
        proj = FalkorProjection.from_uri(
            os.environ["TORTOISE_DB_URI"],
            graph_name=f"test_suite_{os.urandom(4).hex()}")
        yield proj
        proj.close()
        return
    if not has_falkor():
        yield None
        return
    db_path = os.path.join(
        tempfile.mkdtemp(prefix="tortoise_shared_embedded_"), "shared.db")
    proj = FalkorProjection(db_path, graph_name="test")
    yield proj
    proj.close()
