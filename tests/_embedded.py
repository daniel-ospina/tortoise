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

import contextlib
import logging
import os
import re
import shutil
import tempfile
import threading

import pytest

from tortoise.config import is_db_uri
from tortoise.env_truthy import is_truthy  # #4097: the declared truthy contract
from tortoise.projection import FalkorProjection

# #4096: session-scoped test trees created by fixtures in this module and in
# tests/conftest.py. They are reclaimed by `conftest.py::_reclaim_session_tmpdirs`,
# which `_redislite_hygiene` declares as a dependency so pytest's reverse-order
# teardown runs it LAST — after `_redislite_hygiene` / `_server_graph_hygiene` have
# used the socket/pid evidence inside these trees. A local `rmtree` in the shared
# fixture's own finalizer would run first, destroy that evidence, and could orphan
# a live redislite server (the #4068/#1005 class).
SESSION_TMPDIRS: list[str] = []


def register_session_tmpdir(path: str) -> None:
    """Register a session-scoped test tree for end-of-session reclamation."""
    SESSION_TMPDIRS.append(path)


def reclaim_tmpdirs(dirs: list[str]) -> int:
    """rmtree each tree in ``dirs`` (best-effort) and return the count.

    Split out from the session reclaimer so the removal primitive is unit-
    testable without draining the live ``SESSION_TMPDIRS`` registry mid-session.
    """
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)
    return len(dirs)


def drain_session_tmpdirs() -> int:
    """Drain + reclaim ``SESSION_TMPDIRS`` (the session reclaimer's body).

    Split from the fixture so the drain-and-clear behaviour is unit-testable
    without driving a session-scoped pytest fixture.
    """
    dirs, SESSION_TMPDIRS[:] = list(SESSION_TMPDIRS), []
    return reclaim_tmpdirs(dirs)


# ── #3546: ONE process-wide embedded construction lock ────────────────────
# Consolidated here from the two per-file copies that #3511 installed
# (`tests/test_import_endpoint.py`, `tests/test_export_delete.py`). The
# invariant is PROCESS-wide while each copy was a module-scoped lock OBJECT —
# two independent RLocks cannot serialize against each other, so every other
# file that constructs an embedded projection without a prior construction on
# its pinned path stayed exposed. `tests/test_invites_http.py` was one of
# them: its `client` fixture enters `TestClient(app)` (arming the lifespan's
# `tortoise-health-probe` constructor) before the `reg` fixture constructs the
# seeder's projection, both on the same fresh temp db_path.
#
# The race (redislite `RedisMixin.__init__`): a NEW redis-server daemon is
# started whenever `<db>.settings` is absent (or its recorded pid is dead).
# Two constructions that interleave BEFORE either calls
# `_save_setting_registry()` both take the fresh-start branch, each spawning a
# daemon in its own tempdir; the LATER writer silently owns the registry, and
# the loser's writes become invisible to every later opener. It surfaces as a
# seed that reads back empty — e.g. a seeded Membership that an owner/admin
# gate cannot see, so POST /v1/invites 403s instead of reaching its 402/200
# branch (test_invites_http), or an import that looks like it wiped the graph
# (`assert [] == ['old-0']`, test_import_endpoint #3505).
#
# Scope of the guarantee (this is NOT a global single-writer guarantee): the
# lock serializes only IN-PROCESS `FalkorProjection.__init__` calls; two paths
# stay outside it and can still add or remove a registry entry — (1) a
# construction that raises inside `_start_redis()` (RedisLiteException /
# RedisLiteServerStartError) after its daemon spawned but before
# `_save_setting_registry()`, leaving an unregistered orphan; and (2)
# redislite's `_cleanup()` last-client branch, which removes `<db>.settings`
# and shuts the daemon down from `__del__`/atexit on any thread. Callers that
# need more than the lock must therefore ALSO verify visibility at seed time
# (the named `SeedVisibilityError` guard in test_import_endpoint's
# `_seed_live_graph` is the reference shape).
#
# Two further classes sit outside the lock BY CONSTRUCTION — they are limits,
# not coverage gaps to close here: (3) a construction made at test-module
# IMPORT time, because the fixture installs at first test SETUP, after
# collection has already imported every module; and (4) a raw redislite
# `FalkorDB(path)` client, which never calls `FalkorProjection.__init__` — the
# raw-layer files that build clients directly (RAW_EMBEDDED_ALLOWLIST in
# test_embedded_lifecycle.py) do so because the construction IS their test
# input. Subprocess constructions are likewise outside an IN-PROCESS lock.
# A test that monkeypatches `FalkorProjection.__init__` itself (e.g.
# test_pipeline_cli) replaces this wrapper for that test's duration; those
# seams are function-scoped and single-threaded.
#
# Blast radius of the critical section: it spans the WHOLE `__init__`,
# including redislite's blocking `subprocess.call` server start and the
# post-start `_auto_health_recover()` / `_ensure_indexes()` work. A wedged
# embedded start therefore stalls every in-process constructor, not just its
# own thread. That wait is bounded by redislite's socket-wait `start_timeout`,
# but NOT by any timeout on a hung `redis-server` binary — accepted
# deliberately: a hung start is a louder failure than a silent second daemon.
EMBEDDED_CONSTRUCTION_LOCK = threading.RLock()


def serialize_embedded_construction(monkeypatch) -> None:
    """Install THE process-wide `FalkorProjection.__init__` wrapper (#3546).

    Called exactly once, by the session-scoped autouse
    `_serialize_embedded_construction` fixture in `tests/conftest.py` — never
    per file. Files must NOT install their own copy: a second lock object
    cannot serialize against this one, which is the defect #3546 names.

    `monkeypatch` is a `pytest.MonkeyPatch` instance owned by the caller (the
    session fixture cannot use the function-scoped `monkeypatch` fixture), so
    the caller controls undo. Idempotent: re-wrapping an already-wrapped
    `__init__` is a no-op, so a duplicate install can never stack a redundant
    critical section.
    """
    prev = FalkorProjection.__init__
    if getattr(prev, "_tortoise_construction_serialized", False):
        return

    def _serialized_proj_init(self, *args, **kwargs):
        # `return` forwarded deliberately: `__init__` must return None, so it
        # is inert today, but it stays correct if this wrapper is ever reused
        # for a factory or `__new__` (where dropping the result is a bug).
        with EMBEDDED_CONSTRUCTION_LOCK:
            return prev(self, *args, **kwargs)

    _serialized_proj_init._tortoise_construction_serialized = True
    monkeypatch.setattr(FalkorProjection, "__init__", _serialized_proj_init)


# ── Epic #1686: worker-thread probe ───────────────────────────────────────
def _worker_stem_embedded_probe(results: list, path: str) -> None:
    """#1686 probe: report (inherited_stem, is_embedded, graph_name) from a
    WORKER thread. Lives in tests/_embedded.py so the frame sniff skips it
    (basename does not start with test_) — the construction resolves via the
    INHERITED stem (conftest hook + patched Thread.start), not the frame
    path. The target function must be called directly as the Thread target
    (a lambda/closure wrapper would introduce a test_ frame below it)."""
    from tortoise.projection import _inherited_test_stem
    stem = _inherited_test_stem()
    proj = FalkorProjection(path, skip_health_check=True)
    try:
        results.append((stem, proj._is_embedded, proj.graph_name))
    finally:
        proj.close()

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
# lane. (Post-P3 reconciliations grew the set past 17 — graph-integrity,
# eval_* and longmem stems below are each dated additions.)
TEST_NO_REDIRECT_STEMS: tuple[str, ...] = (
    "test_backup_e2e",
    "test_config",
    "test_embedded_concurrency",
    # #2879: the embedded AOF durability drift pin measures an on-disk
    # `<db>-appendonlydir` artifact — under the docker redirect it would
    # construct against the server and see none (the opt-in half reds).
    "test_embedded_durability_claim",
    "test_embedded_lifecycle",
    "test_embedded_lifecycle_fast_close",
    "test_eval_ingest_retry",
    "test_eval_resume_retry_failed",
    "test_eval_extraction_health",
    # #2573-restored bge cache + the P3 lane flip red'd the longmem eval
    # harness on the docker lane: its D2-D4 vector-leg tests assert
    # embedded-FalkorDBLite-only semantics (no_embeddings guard, plain-list
    # poisoning of vec.euclideanDistance, brute-force deadline timeout) that
    # the server HNSW branch can't produce. The module is a 100% embedded
    # eval harness (_fresh_sdk(tmp_path) only, zero docker-gated tests) —
    # same family as the eval_* carve-outs above.
    "test_longmem_runner",
    "test_flip_gate",
    "test_graph_integrity_gate",
    "test_guard",
    "test_hard_reject",
    # #4047: the two #3845 fork-guard files carry a module-level
    # ``pytestmark = pytest.mark.embedded_only`` — their SUBJECT is the embedded
    # daemon (the module-fork wedge lives inside the bundled redis-server, and
    # both the producer and the mitigation are embedded-daemon internals), so
    # embedded-only is the honest classification and they must not be
    # reclassified onto the server lane. They were registered on the docker
    # api/core surfaces but ABSENT from this list and from
    # ``config/ci-surfaces.yml`` ``carve_out``: a full selection COLLECTED them
    # and every test SKIPPED via the embedded_only hook — a permanently green,
    # permanently unexecuted gate on main. Registered here and in ``carve_out``
    # so the URI-unset carve-out job runs them on every full selection.
    "test_fork_safety_3845",
    "test_fork_slot_wedge_3845",
    # #3663: asserts PRODUCTION graph-name scoping (`org_{org_id}`) on the
    # MCP ``tortoise_list_graphs`` HTTP filter, the namespace probe and its
    # opener — which is only possible BECAUSE this stem is exempt. Without the
    # exemption the redirect (the ``FalkorProjection.__init__`` block) would
    # rename every path-built graph to a per-path ``test_*`` name, so under a
    # server URI no production name would exist: the probe's ``own=True`` and
    # the listing filter would assert FAIL, and the opener would return None.
    # A hard RED, never a false pass. Same carve-out rationale as
    # test_hosted_backup.
    "test_cross_tenant_read_isolation",
    "test_hosted_backup",
    "test_migrate_db",
    "test_ops_safety",
    "test_per_session_census",
    # #4028: the surface half asserts embedded brute-force floor semantics
    # (the docker sig-A vector branch returns no absolute similarity, so the
    # floor cannot be applied there) — it must construct a real embedded
    # store, not a redirected server graph.
    "test_precision_leak_4028",
    "test_pre_migration_safety",
    # #3350: the embedded lane's socket timeout / retry-bound assertions are
    # embedded-only (a redirected construction would run against the docker
    # server, where none of them mean anything).
    "test_projection_embedded_socket_timeout",
    "test_projection_lifecycle",
    "test_reaper",
    "test_reaper_orphan",
    "test_redis_guard",
    "test_resume_gate_parity",
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
            tmpdir = tempfile.mkdtemp(prefix="tortoise_probe_")
            try:
                db_path = os.path.join(tmpdir, "probe.db")
                proj = FalkorProjection(db_path, graph_name="test")
                proj.close()
            finally:
                # #4096: reclaim the probe tree even if construction/close raises
                # — one per process before _HAS_FALKOR caches, and the reaper
                # never reaps a .db-only tree.
                shutil.rmtree(tmpdir, ignore_errors=True)
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


# ── Epic #1647 Task 10 (P4, plan-review P1-9): URI-required enforcement ──
# Default pytest requires TORTOISE_DB_URI: at P4 the embedded lane is the
# carve-out ONLY. A URI-less run that is not the carve-out is the pre-epic
# shape — migrated files would construct embedded and green-pass on the
# wrong backend (the exact vacuous class the epic exists to kill). The
# dedicated carve-out job, the tier-2 URI-less PR legs, and the e2e
# surfaces (welcome/legal/hosted — they boot embedded selfhost daemons and
# need no server) opt in via TORTOISE_TEST_CARVE_OUT=1. Lives HERE (not
# conftest) for the same reason as _embedded_only_skip: an import via
# `tests.conftest` re-executes conftest's top-level code mid-session.
def _carve_out_opted_in() -> bool:
    """The ``TORTOISE_TEST_CARVE_OUT`` opt-in, through the declared contract.

    #4097: truthy spellings (1/true/yes/on) now opt in; unset/blank/falsy/garbage
    do not. Previously only the exact string ``"1"`` did, so ``=true`` — what a
    human or a CI author naturally writes — silently failed the URI gate. The
    opt-in permits a URI-less embedded run; it deletes nothing.
    """
    return is_truthy(os.environ.get("TORTOISE_TEST_CARVE_OUT"))


def _assert_p4_uri_required() -> None:
    """Epic #1647 Task 10 Step 1a (plan-review P1-9): fail the session when
    TORTOISE_DB_URI is unset UNLESS TORTOISE_TEST_CARVE_OUT=1 is set.

    The URI gate is `_uri_set_supported()` (is_db_uri — the seam/redirect's
    own predicate family): a set-but-unsupported value (postgres://, a bare
    path) would never redirect, so a "URI-set" session with one would run
    migrated files embedded and green-pass on the wrong backend (the exact
    vacuous class the enforcement exists to kill; symmetric with the E2E-6
    tripwire's EXPECT_URI handling). CARVE_OUT=1 is the explicit opt-in for
    the URI-less embedded shapes (carve-out job, tier-2 legs, e2e surfaces).

    The named helper (called by the conftest session-start fixture) so
    test_markers.py can pin the contract without importing tests.conftest
    (which would re-execute conftest's top-level code)."""
    if _uri_set_supported():
        return
    if _carve_out_opted_in():
        return
    pytest.fail(
        "default pytest requires TORTOISE_DB_URI (epic #1647 P4); run the "
        "carve-out with TORTOISE_TEST_CARVE_OUT=1")


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

    Failure policy (#3214): the journal write is the OWNERSHIP CONTRACT — a
    minted graph that cannot be journaled is UNOWNED, invisible to every live
    peer's scope=None sweep, which may therefore delete it. An OSError
    therefore RAISES (it used to be a silent no-op at DEBUG) so the session
    stops at the first unowned mint; the no-journal-path no-op is unchanged.
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
    except OSError as e:
        # #3214: the journal write is the OWNERSHIP CONTRACT, not hygiene
        # bookkeeping. A minted graph that cannot be journaled is UNOWNED:
        # every live peer's scope=None sweep finds no record of it and may
        # delete it — the cross-session flake #3074 exists to stop. The old
        # policy (a silent no-op logged at DEBUG) left that state invisible
        # for the whole session. This RAISES so the session stops at the first
        # unowned mint instead of letting the shared server accumulate graphs
        # a peer sweep may destroy. "Treat as protected" is NOT an option: a
        # peer's only ownership channel IS this file, and this write is what
        # failed — there is nothing cross-process to fall back to (in-process,
        # the session may always sweep its own graphs).
        raise RuntimeError(
            f"session journal append failed for {name!r} ({path!r}): {e!r} — "
            f"the graph is minted but UNOWNED: a live peer's scope=None "
            f"sweep has no record of it and may delete it (#3214). Stopping "
            f"at the first mint whose ownership could not be recorded; the "
            f"caller must drop any graph it already created (see #3390 for "
            f"the write-ahead fix)."
        ) from e


def _created_since_last_wipe() -> set[str]:
    """The session's created-since-last-wipe delta (cycle-4 P0-1/P2-10).

    journal[cursor:] — the FILE journal is the source of truth, NOT the
    in-memory `_JOURNAL` (product-side redirect/from_uri mints are file-only,
    cycle-7 P1-4). The cursor is persisted as `{_JOURNAL_FILE}.cursor` and
    advanced by `_wipe_or` after each successful wipe (cycle-4 P2-10).
    """
    names = _read_journal()
    return set(names[_read_wiped_cursor():])


# Graph-name families the session sweep OWNS — the only names it may
# DETACH + GRAPH.DELETE. Anything else found in the journal is preserved
# (#7795): a non-owned name means a test drove product code with a shared
# path (e.g. `doctor --db docker://…/tortoise`).
_SWEEP_OWNED_PREFIXES = ("test_", "tortoise_test", "team_")


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


def _live_peer_session_graphs() -> set[str]:
    """Graph names journaled by a LIVE session OTHER than this process.

    #3074: test graphs are server-GLOBAL names (the redirect derives
    ``test_<stem>_<hash>`` on one shared Docker FalkorDB), and every
    migrated test graph is ``test_``-prefixed — so a prefix-wide sweep is
    indistinguishable from a cross-session wipe. A ``scope=None``
    (server-global) sweep used to DETACH-DELETE every live peer's graphs,
    which surfaces as a random test losing the nodes it wrote moments
    earlier (``test_falkor_apply_points_merged``: ``count(a)==0``).
    ``tests/test_wipe_server.py`` calls ``wipe_server(proj)`` (scope=None)
    directly, so any concurrently running session's graphs were fair game.

    Ownership is the session journal (the single source of truth for
    graphs a session minted, cycle-8 P1-2) keyed by the LIVE session
    nonces from ``active_suite_markers()``. THIS process's own nonce is
    skipped: a session may always sweep its own graphs. Embedded
    (redislite) markers carry a random uuid nonce with no journal — they
    contribute nothing, which is correct (an embedded DB is not on the
    server).
    """
    # #3214: the one-shot form of the ownership primitive. ``wipe_server``
    # uses the two halves directly — the marker scan ONCE, then the journal
    # reads per graph — because the journal CONTENTS are the only part of
    # ownership a peer can change inside its deletion loop.
    return _peer_journaled_graphs(_live_peer_journal_files())


def _live_peer_journal_files() -> list[str]:
    """Journal file paths of the LIVE peer sessions (the EXPENSIVE half).

    #3214: split out of ``_live_peer_session_graphs`` so ``wipe_server`` can
    pay the marker scan ONCE and then re-read only the cheap journal FILES
    immediately before each delete. ``active_suite_markers`` is the costly
    part — a directory scan plus a per-marker pid/start-time liveness probe
    (which shells out), so calling the composition per graph is not an
    option. The live-session SET is also stable across a deletion loop, and a
    peer that starts DURING enumeration is still picked up because
    ``wipe_server`` resolves this AFTER ``list_graphs()``.
    """
    from tortoise.embedded_reaper import ACTIVE_SUITES_DIR, active_suite_markers
    ours = os.environ.get("TORTOISE_TEST_SESSION", "")
    paths: list[str] = []
    for marker in active_suite_markers():
        token = marker.get("token") or ""
        if "-" not in token:
            continue
        nonce = token.split("-", 1)[1]
        if not nonce or nonce == ours:
            continue  # our own session — its graphs are ours to sweep
        paths.append(os.path.join(ACTIVE_SUITES_DIR,
                                  f"{nonce}.graphs.jsonl"))
    return paths


def _peer_journaled_graphs(paths: list[str]) -> set[str]:
    """The union of the given (live-peer) journal files' graph names.

    The CHEAP half: re-run per graph inside the deletion loop, so the
    protection cannot be stale by the length of the loop (#3214).
    """
    owned: set[str] = set()
    for path in paths:
        owned.update(_read_journal_file(path))
    return owned


def wipe_server(proj, scope: set[str] | None = None, drop: bool = False) -> None:
    """Server-mode hermeticity wipe (epic #1647, D-4).

    Enumerates list_graphs() and DETACH-DELETEs ONLY graphs named
    test_/tortoise_test_* — the guard-passing test-graph family. Every other
    graph is skipped, never wiped (fail-closed). Refuses non-loopback hosts
    (D-4): a test suite must never wipe a remote dev/shared server.

    scope (the session's created set, cycle-3 P1-7): when given, ONLY names
    in the scope are considered — a per-test wipe never blind-wipes another
    concurrent session's live graphs. scope=None is the server-global sweep,
    reserved for the session-end/last-suite-standing sweep ONLY — and since
    #3074 it also SPARES every graph journaled by a LIVE PEER session
    (``_live_peer_journal_files`` + ``_peer_journaled_graphs``), so no caller
    can destroy a concurrently running session's graphs by accident. #3214:
    the peer journals are re-read immediately before EACH graph's DETACH
    (not snapshotted once up front), because the journal is a file another
    process appends to — see the note at the loop.

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
    # #3074: the scope=None sweep is the ONLY path that names no graphs up
    # front, so it is the ONLY one that can land on a live peer's graph.
    # An explicit scope is journal-derived (the caller's own session).
    is_global = scope is None
    failures: list[tuple[str, Exception]] = []
    dropped: list[str] = []
    default_graph = _uri_default_graph_name()
    # #3214: enumerate FIRST, then resolve the live peers' journals. The old
    # order snapshotted the protected set BEFORE enumerating, so a peer graph
    # minted in that gap was visible to the loop yet already outside the
    # snapshot — and swept. Resolving after enumeration also picks up a peer
    # session that started while we were listing.
    graphs = list(proj.db.list_graphs() or [])
    peer_journals = _live_peer_journal_files() if is_global else []
    for g in graphs:
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
        # #3074/#3214: re-read the live peers' journals IMMEDIATELY before
        # this graph's DETACH. The up-front snapshot's window was
        # enumerate→delete for EVERY graph; re-reading per graph narrows it to
        # re-read→delete for ONE. (The marker scan itself runs once above —
        # the journal CONTENTS are the only part of ownership a peer can
        # change inside the loop.) Still NOT atomic — see #3214 for the
        # residual: ownership lives in a local file written by another
        # process, and the delete is a server command on a different channel,
        # with no conditional/transactional delete spanning the two.
        if peer_journals and g in _peer_journaled_graphs(peer_journals):
            continue  # a live PEER session owns this graph
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
    dict {"dropped", "failed", "preserved", "journal_removed"} or
    {"skipped": ...}.

    FAIL-CLOSED name gate (#7795): only the families in
    ``_SWEEP_OWNED_PREFIXES`` are ever DETACH+DELETEd. A name outside them
    reached the journal because a test drove PRODUCT code with a shared path
    (e.g. ``doctor --db docker://…/tortoise`` — the doctor CLI runs
    in-process, so its ``from_uri`` journals from the test frame). Those are
    PRESERVED and reported in ``preserved``: a test run must never wipe the
    dev/compose/Cloud graph, and retrying would not make the name ours, so
    the journal is still removed.

    #1686: team_* graphs reach the drop set ONLY via the journal — they are
    never test-prefixed (hosted parity: team_create + the hosted mint sites
    append them via _journal_append_product). Raw select_graph team_* mints
    in test code are journal-blind and are closed by _leftover_sweep's
    team_* pass instead.
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
    preserved: list[str] = []
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
        if not g.startswith(_SWEEP_OWNED_PREFIXES):
            # #7795 fail-closed: a name the sweep does not own is PRESERVED —
            # never DETACH+DELETE a dev/compose/Cloud graph. See the docstring.
            preserved.append(g)
            continue
        if _drop_one_graph(proj, g, drop=drop):
            dropped.append(g)
        else:
            failed.append(g)
    removed = False
    if not failed:
        _remove_journal_file(journal_file)
        removed = True
    return {"dropped": dropped, "failed": failed, "preserved": preserved,
            "journal_removed": removed}


def _session_end_own_sweep(uri: str, journal_file: str, *,
                           skip_on_non_loopback: bool = True) -> dict:
    """Session-end sweep of THIS session's journaled graphs (Step 7 item 3)."""
    with _sweep_proj(uri) as proj:
        return _sweep_drop(proj, journal_file, drop=True,
                           skip_on_non_loopback=skip_on_non_loopback)


# The product's own mint namespace, for the journal-blind stray pass below.
# `org_` is current (tenancy rename, #3543); `team_` is retained so an
# opted-in run still reclaims graphs minted before the rename. Both identify
# REAL tenant graphs — which is why the pass is opt-in only.
_PRODUCT_GRAPH_PREFIXES = ("org_", "team_")


def _team_sweep_allowed(uri: str) -> bool:
    """#1686 (review P1-1): may the product-namespace stray pass run on `uri`?

    The product's own mint namespace (hosted parity: real tenant graphs) is
    the _PRODUCT_GRAPH_PREFIXES family — `org_` since the tenancy rename
    (#3543), `team_` for graphs minted before it. A blanket delete there on
    a shared or dev docker would destroy legitimate product data — the
    pre-#1686 design deliberately kept wipes fail-closed to
    test_/tortoise_test_ prefixes. Allowed ONLY via an explicit operator
    opt-in (TORTOISE_TEST_SWEEP_TEAM_STRAYS=1). Journaled product-namespace
    graphs are always dropped via _sweep_drop (the journal is the ownership
    record) — this gate protects only the journal-blind residual pass.

    #1884: the URI-path inference ("test" substring in the graph name) is
    RETRACTED. The longmem_eval re-validation runs against the SAME
    test-named URI path (.../tortoise_test_matrix) on the SHARED dev
    container that concurrent docker-lane pytest sessions use; a session
    ending last-suite-standing inferred "dedicated test DB" from the path
    and the journal-blind pass DETACH-DELETEd + GRAPH.DELETEd the eval's
    LIVE per-question graphs (then minted team_default__default__{qid}) mid-ingest —
    silent write loss (writes succeed client-side, the post-ingest census
    reads an empty namespace, gate red). A test-named path on a shared
    server is NOT an ownership record; the explicit opt-in is (CI's
    dedicated docker containers are fresh per job, so nothing accumulates
    there without the pass)."""
    # OVERRIDES (#4097): env-truthiness truthy-set parsing ("1"/"true"/"yes"/"on").
    # This gate requires the exact value "1": it is the SOLE authorization for an
    # irreversible journal-blind DETACH DELETE + GRAPH.DELETE of the real-tenant
    # org_*/team_* namespace (the `uri` parameter is dead — the #1884 URI inference
    # was retracted — so no containment check compensates), and widening a
    # destructive opt-in surface is not a vocabulary-coherence win. The refusal is
    # logged with the exact required spelling, so the narrowing is discoverable.
    # Pinned by tests/test_env_truthy.py::test_team_sweep_gate_is_narrow_by_design.
    return os.environ.get("TORTOISE_TEST_SWEEP_TEAM_STRAYS") == "1"


def _sweep_team_strays(proj, uri: str) -> list[str]:
    """Drop journal-blind stray product-namespace graphs (#1686 closure).

    Matches _PRODUCT_GRAPH_PREFIXES. Guarded by _team_sweep_allowed(uri) —
    never on a shared/dev docker (explicit opt-in only since #1884; the
    URI-path "test" inference retracted — see _team_sweep_allowed).
    DETACH+DELETE per graph, log-and-continue; returns the dropped names.
    Runs AFTER wipe_server in _leftover_sweep (journaled product-namespace
    names were already dropped by _sweep_drop; this closes the
    raw-select_graph class)."""
    if not _team_sweep_allowed(uri):
        logging.getLogger(__name__).info(
            "leftover %s pass SKIPPED — %r (set "
            "TORTOISE_TEST_SWEEP_TEAM_STRAYS=1 to opt in)",
            "/".join(_PRODUCT_GRAPH_PREFIXES), uri)
        return []
    dropped: list[str] = []
    for g in proj.db.list_graphs() or []:
        if not g.startswith(_PRODUCT_GRAPH_PREFIXES):
            continue
        try:
            proj.db.select_graph(g).query("MATCH (n) DETACH DELETE n")
            proj.db.select_graph(g).delete()
            dropped.append(g)
        except Exception as e:
            logging.getLogger(__name__).warning(
                "leftover product-namespace drop failed for %r: %r", g, e)
    return dropped


def _leftover_sweep(uri: str, *, skip_on_non_loopback: bool = True) -> dict:
    """LAST-suite-standing FULL sweep: every test-prefixed graph on the
    server (wipe_server scope=None → global, drop=True) PLUS, since #1686,
    journal-blind stray product-namespace graphs (org_*/team_*) — but ONLY
    when _team_sweep_allowed (an explicit
    TORTOISE_TEST_SWEEP_TEAM_STRAYS=1 opt-in; the URI-path inference is
    retracted since #1884): that family is the product's mint namespace and
    a blanket delete on a shared/dev docker would destroy real tenant data
    (review P1-1). Log-and-continue on errors — hygiene never fails the
    suite."""
    with _sweep_proj(uri) as proj:
        from tortoise.projection import _is_loopback_host
        host = _projection_host(proj)
        if skip_on_non_loopback and not _is_loopback_host(host):
            logging.getLogger(__name__).info(
                "leftover sweep SKIPS non-loopback host %r", host)
            return {"skipped": f"non-loopback {host!r}"}
        try:
            wipe_server(proj, scope=None, drop=True)
            dropped_teams = _sweep_team_strays(proj, uri)
            return {"full_sweep": True, "team_strays_dropped": dropped_teams}
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
    tmpdir = tempfile.mkdtemp(prefix="tortoise_shared_embedded_")
    register_session_tmpdir(tmpdir)
    db_path = os.path.join(tmpdir, "shared.db")
    proj = FalkorProjection(db_path, graph_name="test")
    yield proj
    proj.close()


@contextlib.contextmanager
def fresh_embedded_proj(db_dir, *, graph_name: str | None = None, **kwargs):
    """Function-scoped sanctioned embedded construction (#3769).

    The per-test counterpart to ``shared_proj``. ``shared_proj`` is
    ``scope="session"``, so its single server is built **once** and anything
    read at construction time — including ``TORTOISE_EMBEDDED_AOF`` — is frozen
    at the first case's value. A parametrised test would then observe the first
    case's server, and its assertion would be vacuous **in exactly the way it
    exists to prevent** (#3624 review: the default-off and opt-in-on cases must
    not be able to see one another).

    Constructs a FRESH server per call, on an explicit path inside the caller's
    own directory, so construction-time flags are honoured per call and the
    caller can inspect that directory for on-disk artifacts.

    The raw construction lives HERE, at the seam — which is precisely the
    rationale ``RAW_EMBEDDED_ALLOWLIST`` records for allowlisting
    ``_embedded.py`` ("seam/helper — raw constructions ARE the
    embedded-under-test input"). A consumer test therefore needs no
    ``RAW_EMBEDDED_ALLOWLIST`` entry of its own.

    It DOES, however, need a SECOND carve-out: the caller's test module stem
    must be listed in ``TEST_NO_REDIRECT_STEMS``. Under a URI lane
    (``TORTOISE_DB_URI`` + ``TORTOISE_TEST_MODE=1``) a stem that is not exempt
    gets redirected — ``path`` is discarded and the construction connects to a
    server — so there is no fresh embedded server and no on-disk artifact to
    inspect. This seam therefore FAILS CLOSED on that case rather than yielding
    a projection that would make an ``expect_aof=False`` assertion vacuous.

    Teardown never masks the caller's assertion.
    """
    db_path = os.path.join(str(db_dir), "graph.db")
    kwargs.setdefault("allow_nonstandard_path", True)
    kwargs.setdefault("skip_health_check", True)
    if graph_name is not None:
        kwargs["graph_name"] = graph_name
    proj = FalkorProjection(path=db_path, **kwargs)
    if not getattr(proj, "_is_embedded", False):
        with contextlib.suppress(Exception):
            proj.close()
        raise RuntimeError(
            "fresh_embedded_proj is embedded-only: the caller's test module "
            "must be listed in TEST_NO_REDIRECT_STEMS (tests/_embedded.py). "
            "Otherwise the URI redirect flips this construction to a server, "
            "`path` is discarded, and no on-disk artifact exists — which would "
            "make an expect_absent assertion vacuous (#3769)"
        )
    try:
        yield proj
    finally:
        with contextlib.suppress(Exception):
            proj.close()
