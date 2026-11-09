# tests/test_redirect_seam.py
"""Unit surface: the class-level URI-aware redirect (epic #1647 Task 1)."""
import functools
import os
import subprocess
import sys
import threading
import types

import pytest

from tests import _live_utils
from tests._tmpdir_hygiene import scan_root
from tortoise.projection import FalkorProjection


def _tmp_db(name: str) -> str:
    """A DB path inside the PRIVATE session temp root (#3752).

    These tests used a hardcoded path in the SHARED temp dir. On the Linux CI
    ``$TMPDIR`` is unset, so ``/tmp`` IS the shared temp base: constructing an
    embedded projection there makes the process enumerate that base, which the
    #3752 scan guard forbids — correctly, since that enumeration can match
    another session's ``redis.pid``/``redis.socket``. ``scan_root()`` returns
    the private per-session root (and raises rather than falling back to the
    shared dir, which is the whole point).
    """
    return os.path.join(scan_root(), name)

# Cycle-5 P2-14: import-time snapshot of the session nonce — the stability
# probe below asserts the runtime value never mutated mid-session (a mutation
# would strand this session's graphs: the derived-name hash input AND the
# journal filename key off the ORIGINAL value). 12 hex = 48 bits (P2-1).
_SESSION_NONCE_AT_IMPORT = os.environ.get("TORTOISE_TEST_SESSION", "")


def _docker_reachable(host: str | None = None,
                     port: int | None = None) -> bool:
    """True when the PROVISIONED docker-lane FalkorDB answers a TCP connect.

    Mirrors tests/test_ingest.py's _docker_falkor_reachable — the repo's
    skip-guard convention (#1436): live-FalkorDB-required tests SKIP with a
    FalkorDB-reason when the docker is absent (post-merge-validation runs
    the full suite with NO docker service), never ERROR on
    redis.ConnectionError. The fast CI job provisions the falkordb service,
    so the probe passes there and the redirect tests actually run.

    #6673: the port used to be the 6379 literal; it is now the ephemeral host
    port assigned by the provision step (docker `-p 127.0.0.1:0:6379`). `host=None`
    resolves through `_live_utils.service_host()`, so a
    `TORTOISE_TEST_DOCKER_HOST` override reaches the probe exactly as it reaches
    the clients (the product's `FALKORDB_HOST` is not read — see the seam).
    """
    return _live_utils.tcp_reachable(port or _live_utils.docker_port(), host=host)


@pytest.fixture
def uri_env(monkeypatch):
    if not _docker_reachable():
        pytest.skip(f"live FalkorDB (localhost:{_live_utils.docker_port()}) not reachable")
    monkeypatch.setenv("TORTOISE_DB_URI", _live_utils.docker_base_uri())
    yield


def test_unset_uri_constructs_embedded(monkeypatch):
    # Cycle-3 P1-6: self-contained w.r.t. TORTOISE_DB_URI — a dev/CI lane
    # with a URI set must not flip this to the server lane (the P2 half-b
    # docker job would red it).
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    proj = FalkorProjection(_tmp_db("seam-test-a.db"))
    try:
        assert proj._is_embedded is True
    finally:
        proj.close()


def test_uri_set_redirects_to_server(uri_env):
    proj = FalkorProjection(_tmp_db("seam-test-b.db"))
    try:
        assert proj._is_embedded is False
        # graph name derived: test_<stem_sanitized>_<hash12(session+path)>
        # — per-path + per-session unique (cycle-2 P0-1b/P2-3); stem
        # sanitized (cycle-3 P2-5) + 12 hex = 48+ bits (cycle-3 P2-17)
        assert proj.graph_name.startswith("test_seam_test_b_")
        assert len(proj.graph_name) == len("test_seam_test_b_") + 12
        assert proj._host == "localhost"  # cycle-3 P0-1: recorded on the projection
    finally:
        proj.close()


def test_explicit_graph_name_honored(uri_env):
    # Cycle-2 P0-1b rule: a TEST-PREFIXED explicit name is the shared opt-in
    # — honored verbatim (seam fixtures use test_suite_<uuid> this way).
    proj = FalkorProjection(_tmp_db("seam-test-c.db"), graph_name="test_explicit")
    try:
        assert proj.graph_name == "test_explicit"
        assert proj._is_embedded is False
    finally:
        proj.close()


def test_explicit_nondefault_name_derives_per_path(uri_env):
    # Cycle-2 P0-1b: test_projection's parity pair constructs DISTINCT paths
    # with the SAME explicit non-test name ("test"). Deriving per-path names
    # is mandatory — a shared rename would collapse parity_a/parity_b onto
    # one server graph and the apply-vs-rebuild comparison would be a graph
    # compared to itself (vacuous pass, #942 class). Same path + same
    # explicit name must still share (the embedded same-file analog).
    a = FalkorProjection(_tmp_db("seam-parity-a.db"), graph_name="test")
    b = FalkorProjection(_tmp_db("seam-parity-b.db"), graph_name="test")
    a2 = FalkorProjection(_tmp_db("seam-parity-a.db"), graph_name="test")
    try:
        assert a.graph_name != b.graph_name, "distinct paths must yield distinct server graphs"
        # cycle-3 P2-5: path-derived stems are sanitized (hyphens → "_")
        assert a.graph_name.startswith("test_seam_parity_a_")
        assert b.graph_name.startswith("test_seam_parity_b_")
        assert a2.graph_name == a.graph_name, "same path + same explicit name shares"
        assert a._is_embedded is False
    finally:
        a.close()
        b.close()
        a2.close()


def test_no_arg_does_not_redirect(monkeypatch):
    # D-1 scope: explicit path= only. No-arg stays embedded canonical.
    # Decoupled from uri_env (review P2): this test only needs the URI SET
    # to prove the redirect does NOT fire for no-arg — it never contacts the
    # server, so it must run on docker-absent lanes too.
    monkeypatch.setenv("TORTOISE_DB_URI", _live_utils.docker_base_uri())
    proj = FalkorProjection()
    try:
        assert proj._is_embedded is True
    finally:
        proj.close()


def test_no_redirect_env_exempts_caller_test_module(monkeypatch):
    # P0-1: the exemption keys on the CALLER TEST MODULE (frame-identified),
    # never on the DB-file basename. List this test file's own stem.
    # Decoupled from uri_env (review P2): the exempted caller stays embedded
    # — no server contact — so it must run on docker-absent lanes too.
    monkeypatch.setenv("TORTOISE_DB_URI", _live_utils.docker_base_uri())
    monkeypatch.setenv("TORTOISE_TEST_NO_REDIRECT", "test_redirect_seam")
    proj = FalkorProjection(_tmp_db("seam-test-d.db"))
    try:
        assert proj._is_embedded is True  # exempted caller module stays embedded
    finally:
        proj.close()


def test_db_file_stem_never_exempts(uri_env, monkeypatch):
    # P0-1 regression: the OLD bug compared os.path.basename(path) against
    # the exemption list — carve-out files use arbitrary DB names (c.db,
    # fresh.db, solo.db...) that never match their file stems, so the
    # exemption never fired. Listing a DB-file stem must NOT exempt.
    monkeypatch.setenv("TORTOISE_TEST_NO_REDIRECT", "seam-test-d")
    proj = FalkorProjection(_tmp_db("seam-test-d.db"))
    try:
        assert proj._is_embedded is False  # DB basename ≠ test module → redirects
    finally:
        proj.close()


def test_no_test_frame_in_stack_no_redirect(monkeypatch):
    # Cycle-2 P1-1b: subprocess CLI children (test_export_cli's `python -m
    # tortoise export`, redis-guard fixture scripts, ...) inherit
    # TORTOISE_DB_URI + TORTOISE_TEST_MODE via os.environ.copy() but their
    # process has NO test module in the stack (_caller_test_stem() → None).
    # The redirect must NOT fire — the child exercises the embedded/CLI lane.
    monkeypatch.setenv("TORTOISE_DB_URI", _live_utils.docker_base_uri())
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    out = subprocess.run(
        [sys.executable, "-c",
         "import os, tempfile; "
         "from tortoise.projection import FalkorProjection; "
         "p = FalkorProjection(os.path.join(tempfile.gettempdir(), "
         "'seam-child.db'), skip_health_check=True); "
         "print(p._is_embedded); p.close()"],
        capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "True", \
        "no test frame in the stack → embedded lane must be preserved"


def test_caller_test_stem_nearest_frame_semantics():
    # Cycle-3 P2-18: _caller_test_stem() keys on the NEAREST test_ frame, not
    # the outermost. A construction made through a CROSS-TEST-MODULE helper
    # (a test_-prefixed module, e.g. tests/test_helpers.py, imported by other
    # test files) resolves to the HELPER's stem — so listing a shared helper
    # in TEST_NO_REDIRECT_STEMS exempts ALL its callers (documented caveat;
    # the exemption list is the caller-module list, and shared helpers must
    # never be listed). Non-test_-prefixed helpers (tests/_embedded.py) are
    # skipped by the prefix sniff and resolve UP to the calling test module.
    import tests.test_helpers as _th  # test_-prefixed helper module (tiny, new)
    from tortoise.projection import _caller_test_stem
    # Cycle-4 P1-4: tests/test_helpers.py is CREATED in Step 3's edit list
    # below (this file would otherwise ImportError at collection — the
    # cycle-3 plan referenced it without an owner task). It is the ONLY
    # test_-prefixed shared helper; the P2-9 stem-registry guard (Task 5
    # Step 1) asserts no carve-out file imports it — a carve-out
    # constructing through it would resolve to stem "test_helpers" (not its
    # own exempted stem) and silently lose its redirect exemption.
    assert _caller_test_stem() == "test_redirect_seam"  # this file is nearest
    assert _th.construct_via_helper() == "test_helpers", \
        "nearest-frame semantics: the helper's own stem wins over the caller"


def test_memory_path_derives_unique_test_graph(uri_env):
    # P2-13: :memory: is a constant string — a path-hash would collide every
    # :memory: construction onto one shared graph. Must derive a per-
    # construction unique test_memory_* graph instead.
    proj = FalkorProjection(":memory:")
    proj2 = FalkorProjection(":memory:")
    try:
        assert proj._is_embedded is False
        assert proj.graph_name.startswith("test_memory_")
        assert proj.graph_name != proj2.graph_name
    finally:
        proj.close()
        proj2.close()


def test_no_redirect_without_test_mode(monkeypatch):
    # P0-4: TORTOISE_TEST_MODE is the test-session signal. URI set but
    # TEST_MODE absent (prod tool: backup/rebuild/migrate) → NO redirect —
    # prod path constructions are preserved byte-for-byte.
    monkeypatch.setenv("TORTOISE_DB_URI", _live_utils.docker_base_uri())
    monkeypatch.delenv("TORTOISE_TEST_MODE", raising=False)
    proj = FalkorProjection(_tmp_db("seam-test-f.db"), skip_health_check=True)
    try:
        assert proj._is_embedded is True  # prod-style construction unaffected
    finally:
        proj.close()


def test_uri_set_refuses_non_loopback_host(monkeypatch):
    # Cycle-2 P0-2 (fail-fast): a typo'd/shared TORTOISE_DB_URI must refuse
    # BEFORE the first construction — not at the first wipe_server call,
    # which is after every migrated test has already written to the remote.
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@db.internal.example.com:6379")
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    with pytest.raises(RuntimeError, match="loopback"):
        FalkorProjection(_tmp_db("seam-remote.db"), skip_health_check=True)


def test_uri_hostless_refuses(monkeypatch):
    # Cycle-8 P2-1: a HOSTLESS URI (`docker://:pw@:6379`) must refuse at the
    # redirect, matching is_loopback_uri's absent-hostname → False (the old
    # `hostname or "localhost"` fallback accepted it and minted graphs on the
    # default host while the tripwire refused). Same RuntimeError class as the
    # non-loopback host case — the shared predicate is the single judge.
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@:6379")
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    with pytest.raises(RuntimeError, match="loopback"):
        FalkorProjection(_tmp_db("seam-hostless.db"), skip_health_check=True)


def test_allow_remote_escape_lets_non_loopback_through(monkeypatch):
    # P0-2 escape: TORTOISE_TEST_ALLOW_REMOTE=1 is the explicit opt-in. The
    # redirect proceeds (wipe_server still refuses non-loopback — D-4;
    # cycle-3 P2-6: the escape is write-only — usable for assert-only/read-only
    # sessions, never for wipes).
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@db.internal.example.com:6379")
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    monkeypatch.setenv("TORTOISE_TEST_ALLOW_REMOTE", "1")
    # Cycle-4 P1-1: the redirect falls through to the host branch, which
    # constructs `from falkordb import FalkorDB` — and the REAL FalkorDB
    # constructor performs a LIVE round-trip at __init__ (falkordb.py:132
    # Is_Sentinel(conn) → conn.info(), verified in the vendored 3.x source)
    # BEFORE returning, so a remote host raises redis.exceptions.ConnectionError
    # (not RuntimeError) and the test could never reach its assertions. FIX:
    # monkeypatch falkordb.FalkorDB with a stub — the host branch only needs
    # select_graph() + close() (projection L380-390 + L1547-1568, verified);
    # the redirect's own predicate + _host recording are what this test pins.
    import types

    class _GraphStub:
        def query(self, *a, **k):
            return types.SimpleNamespace(result_set=[])

    class _DbStub:
        def select_graph(self, name):
            return _GraphStub()
        def close(self):
            pass

    class _FakeFalkorDB:
        # Divergence note (epic #1647 Task 1 impl): the projection assigns
        # `self.db = FalkorDB(...)` and then calls `self.db.select_graph(...)`
        # / `self.db.close()` directly on the constructor's RETURN VALUE —
        # the plan's original stub attached the db under `.db`, which the
        # projection never sees (AttributeError: no select_graph). The stub
        # implements the host-branch surface the projection actually uses.
        def __init__(self, **kw):
            self._graph = _GraphStub()
        def select_graph(self, name):
            return self._graph
        def close(self):
            pass

    monkeypatch.setattr("falkordb.FalkorDB", _FakeFalkorDB)
    proj = FalkorProjection(_tmp_db("seam-remote.db"), skip_health_check=True)
    try:
        assert proj._is_embedded is False
        assert proj.graph_name.startswith("test_seam_remote_")  # sanitized stem (P2-5)
        assert proj._host == "db.internal.example.com"  # cycle-3 P0-1: recorded
    finally:
        proj.close()


def test_redirect_connection_boundedness(uri_env):
    # Cycle-4 P1-2 (re-scoped from cycle-3 P1-4 — the pool-release assert was
    # STILL INVERTED/VACUOUS): on redis-py 8.1.0, `p.db.connection` CHECKS OUT
    # a connection (pool._available_connections == 0 before), then close() →
    # pool.release() → disconnect() (which does NOT remove connection objects)
    # → reset() (which empties) → after == 0 → "0 >= 0" passes; AND a NO-OP
    # close (a real leak) also passes (after == before). The assert cannot
    # distinguish release from leak. Re-scoped to a SERVER-VISIBLE metric +
    # a weakref live-count; redis-py introspection is secondary only.
    #   (a) WEAKREF live ConnectionPool count: after close(), the pool must be
    #       unreferenced (collected by gc) — a leaked client keeps it alive.
    #   (b) SERVER-VISIBLE: INFO clients' connected_clients before vs after 20
    #       construct/close cycles must grow by ≤ 1 (the probe's own client),
    #       never by ~20 — this is what the docker job can actually observe.
    # Cycle-5 P2-6: leg (b) is SERVER-GLOBAL — connected_clients counts every
    # client on the docker, so a CONCURRENT URI session's live clients flake
    # the "≤ 1" assert (they are not ours to account for). The leg SKIPS when
    # other active-suite markers exist (reusing `active_suite_markers()`, the
    # shared last-suite-standing helpers — a concurrent session detected);
    # leg (a) stays the primary, process-local leak signal.
    import gc
    import weakref

    import redis

    from tortoise.embedded_reaper import active_suite_markers
    # cycle-5 P2-6: markers are {pid}-{uuid8} tokens (conftest L178) — a
    # marker whose pid is NOT this process's belongs to a CONCURRENT suite;
    # the info-clients leg is server-global and skips under concurrency.
    # Cycle-6 P2-2: active_suite_markers() returns list[dict] ({pid}, {token},
    # {start}) — the cycle-5 code iterated it as STRINGS (m.startswith(...))
    # which would TypeError. Parse the pid from each marker dict's token.
    _markers = active_suite_markers()
    _other_pids = {
        m.get("pid") for m in _markers
        if m.get("token") and not str(m.get("token")).startswith(f"{os.getpid()}-")
    }
    _info_leg_runs = not _other_pids
    pools = []
    for i in range(20):
        p = FalkorProjection(_tmp_db(f"seam-conn-{i}.db"))
        conn = p.db.connection  # redis.Redis client
        pools.append(weakref.ref(conn.connection_pool))
        assert isinstance(conn, redis.Redis), "host branch is the raw redis client"
        p.close()
    gc.collect()
    live = [r for r in pools if r() is not None]
    assert len(live) <= 1, \
        f"close() leaked {len(live)} of 20 live ConnectionPool instances (weakref)"
    probe = redis.Redis(host=_live_utils.service_host(),
                    port=_live_utils.docker_port(), password="falkordb",
                        socket_connect_timeout=5)
    try:
        before = int(probe.info("clients")["connected_clients"])
    finally:
        probe.close()
    for i in range(20):
        p = FalkorProjection(_tmp_db(f"seam-conn-{i}.db"))
        p.close()
    probe = redis.Redis(host=_live_utils.service_host(),
                    port=_live_utils.docker_port(), password="falkordb",
                        socket_connect_timeout=5)
    try:
        after = int(probe.info("clients")["connected_clients"])
    finally:
        probe.close()
    if _info_leg_runs:  # cycle-5 P2-6: concurrency-skipped — weakref leg still guards
        assert after - before <= 1, \
            f"close() left {after - before} extra server-side clients (INFO clients)"


def test_unsupported_uri_scheme_stays_embedded(monkeypatch):
    monkeypatch.setenv("TORTOISE_DB_URI", "postgres://x@localhost/y")
    proj = FalkorProjection(_tmp_db("seam-test-e.db"))
    try:
        assert proj._is_embedded is True
    finally:
        proj.close()


def test_session_nonce_is_stable_and_hex12():
    # Cycle-5 P2-14: the cycle-4 changelog P2-5 row claimed this test was
    # ADDED to Task 1 Step 1 — the BODY never shipped it (it is absent from
    # this step's test list; only test_markers.py's conditional variant
    # exists, Task 5). Import-snapshot vs runtime value: a mid-session
    # TORTOISE_TEST_SESSION mutation would strand this session's graphs
    # (derived-name hash input + journal filename key off the ORIGINAL
    # value). Shape: 12 hex = 48 bits (cycle-5 P2-1).
    import re
    assert os.environ.get("TORTOISE_TEST_SESSION", "") == _SESSION_NONCE_AT_IMPORT, \
        "TORTOISE_TEST_SESSION mutated mid-session — derived names/journal would strand"
    assert re.fullmatch(r"[0-9a-f]{12}", _SESSION_NONCE_AT_IMPORT), \
        "conftest must export TORTOISE_TEST_SESSION = 12 hex (48 bits)"


def test_loopback_predicate_single_source():
    # Cycle-7 P1-3: tortoise.config.is_loopback_uri is the SINGLE shared
    # loopback predicate (created in this task — it did not exist; the Task 4
    # tripwire imports it, and projection._is_loopback_host delegates to the
    # same LOOPBACK_HOSTS constant). Both modules must resolve the SAME host
    # set — a divergence would let the redirect accept a host the tripwire
    # refuses (or vice versa), breaking the fail-before-first-write chain.
    from tortoise.config import LOOPBACK_HOSTS, is_loopback_uri
    from tortoise.projection import _is_loopback_host
    for host in ("localhost", "127.0.0.1", "::1"):
        assert host in LOOPBACK_HOSTS
        assert _is_loopback_host(host) is True
        # Divergence note (epic #1647 Task 1 impl): a BARE IPv6 literal
        # (docker://:pw@::1:6379) does not parse — urlparse yields hostname
        # None (RFC 3986 requires brackets), so the URI is built with the
        # bracketed [::1] form to actually exercise the ::1 hostname.
        _host_form = f"[{host}]" if ":" in host else host
        assert is_loopback_uri(f"docker://:pw@{_host_form}:6379") is True
    for host in ("db.internal.example.com", "falkor.prod.internal"):
        assert _is_loopback_host(host) is False
        assert is_loopback_uri(f"docker://:pw@{host}:6379") is False
    # Cycle-8 P2-1: HOSTLESS URIs — the single predicate must refuse them the
    # SAME way in both modules. The redirect previously accepted
    # `hostname or "localhost"` while is_loopback_uri refused (absent
    # hostname → not loopback) — a divergence that let the redirect mint
    # graphs while the session-start tripwire said non-loopback.
    assert is_loopback_uri("docker://:pw@:6379") is False, \
        "hostless URI is not loopback (absent hostname, fail-closed)"
    assert _is_loopback_host(None) is False, \
        "None host is not loopback — the redirect's hostless path must refuse"
    assert is_loopback_uri(_live_utils.docker_base_uri()) is True


# ── Epic #1686: worker-thread carve-out attribution ─────────────────────
# The frame-keyed exemption cannot see worker-thread stacks (TestClient
# portals, background threads): with _TEST_SESSION_ACTIVE they'd redirect
# even for carve-out files. conftest's pytest_runtest_setup records the
# running module's stem per-thread; projection's patched Thread.start
# stamps it onto spawned threads; _resolve_caller_stem() falls back to the
# inherited stem when the stack walk finds no test_ frame. These tests
# pin the inheritance + redirect behavior with self-contained env
# (no live FalkorDB contact: the carve-out case constructs embedded, the
# non-exempt case stubs falkordb.FalkorDB).


_STAMP_NAME = "_thread_start_inherit_stem"


def _is_stamp(fn) -> bool:
    """True when ``fn`` is a Thread.start test-stem stamp (#1686/#11430).

    Identified by ``__globals__``, NEVER by ``__name__``/``__module__``:
    ``functools.wraps`` copies those onto a FOREIGN wrapper. ``sentry_sdk``'s
    ``ThreadingIntegration`` replaces ``Thread.start`` with
    ``@wraps(old_start) def sentry_start(...)`` — so after any test initialises
    the real SDK, ``Thread.start`` is a function *named*
    ``_thread_start_inherit_stem`` *from* ``tortoise.projection`` that is not the
    module's function object (#11430). A name/module test calls that a double
    wrap; an identity test does too; both are wrong — the stamp is the inner
    link and still runs.
    """
    g = getattr(fn, "__globals__", None)
    return (isinstance(g, dict)
            and g.get("__name__") == "tortoise.projection"
            and getattr(fn, "__name__", None) == _STAMP_NAME)


def _closure_stamp(fn):
    """The stamp a PLAIN (non-``@wraps``) delegating wrapper closes over.

    ``def plain_wrapper(self, *a, **k): return stamp(self, *a, **k)`` calls our
    stamp but carries no ``__wrapped__`` (only ``functools.wraps`` copies
    that), so a ``__wrapped__``-only walk reported it as "never installed" —
    the FALSE-RED half of #11430's review. A closure cell is what makes the
    SHAPE visible here, but it is not the only static evidence: bytecode shows
    whether a wrapper CALLS what it closes over (a caller carries a CALL opcode
    fed by the cell — ``CALL_FUNCTION_EX`` for the ``*a, **k`` forwarding shape,
    ``CALL`` for plain positional — where a bare reference carries none). What no static
    check proves is that the call site is REACHED — so this helper stays a
    SHAPE test, and the ``@wraps``-that-never-calls shape stays tolerated rather
    than guessed at (see ``_is_stamp``). Deliberately NOT a globals-dict scan: any function defined
    in a module that merely HOLDS the stamp would then read as delegating,
    which is a false GREEN on the "the stamp still runs" property. The residue
    is stated rather than papered over: a MODULE-SCOPE plain wrapper that
    reaches the stamp through globals still reads as "never installed". The
    reason is not that a static check cannot see the call — bytecode can — but
    that no static check can prove the call site is REACHED (a branch dead at
    RUNTIME compiles to the same opcodes as a live one — though a literal
    ``if False:`` elides its call entirely), so a recovered "link" would be a
    guess.
    """
    for cell in getattr(fn, "__closure__", None) or ():
        try:
            value = cell.cell_contents
        except ValueError:  # a not-yet-bound cell
            continue
        if _is_stamp(value):
            return value
    return None


def _delegate_of(fn):
    """The single frame ``fn`` delegates to, or None.

    ONE successor function, so the walkers below cannot drift apart: our stamp
    delegates through ``_ORIG_THREAD_START`` (that is what the installer
    chains); anything else delegates through ``__wrapped__`` (``functools.wraps``
    / sentry) or, for a plain wrapper, a closed-over stamp. Single-valued on
    purpose: a wrapper carrying BOTH edges (a foreign ``__wrapped__`` and a
    closed-over stamp) is followed through ``__wrapped__`` only, so a
    closure-only delegator behind a foreign ``__wrapped__`` still reads as
    "never installed" — the documented residue in ``_closure_stamp``. Keeping
    ONE successor is what makes a revisit in ``_delegation_path`` mean a cycle
    rather than a diamond.
    """
    if _is_stamp(fn):
        return fn.__globals__.get("_ORIG_THREAD_START")
    nxt = getattr(fn, "__wrapped__", None)
    return nxt if nxt is not None else _closure_stamp(fn)


def _delegation_path(start) -> list:
    """Frames from ``start`` along its delegate path, TRUNCATING on a revisit.

    Truncation is hang-safety, not a verdict: the repeated frame is included
    once so ``_stamp_links`` still sees a self-cycle's stamp, and a stride that
    comes back on itself cannot loop forever. Truncation is LOAD-BEARING, not
    merely defensive: ``importlib.reload`` re-executes the module body in the
    SAME module ``__dict__``, so an UNGUARDED ``_ORIG_THREAD_START =
    Thread.start`` rewrites the ALREADY-INSTALLED stamp's delegate pointer to
    itself — a self-revisit, which is exactly the state in which
    ``Thread.start()`` recurses and every chain-length check still passes. The
    module body now GUARDS that assignment and keeps the captured original
    (#11217), so a real reload no longer produces this; the shape stays
    reachable — and stays this walker's subject — by synthesising the module
    dict in tests.
    """
    frames, seen, fn = [], set(), start
    while fn is not None:
        frames.append(fn)
        if id(fn) in seen:
            break
        seen.add(id(fn))
        fn = _delegate_of(fn)
    return frames


def _stamp_links(start) -> list:
    """Every DISTINCT frame reachable from ``start`` that is OUR stamp."""
    links = []
    for frame in _delegation_path(start):
        if _is_stamp(frame) and all(frame is not seen for seen in links):
            links.append(frame)
    return links


def _stamp_problems(start) -> list[str]:
    """The install invariant on ``Thread.start``, as a list of violations.

    Two facts, and both are needed because they catch disjoint states:

    1. exactly ONE of our stamps is reachable — zero means the installer never
       ran; two means one execution of the module stacked its stamp on
       another's.
    2. a stamp's ``_ORIG_THREAD_START`` does not reach ANOTHER stamp — the
       SELF-revisit an UNGUARDED module body produced on a plain
       ``importlib.reload`` is the state fact 2 alone can see (a two-stamp
       stack already trips fact 1). This is
       the genuine double wrap the class marker exists to prevent: a module
       body that captures ``_ORIG_THREAD_START = Thread.start``
       UNCONDITIONALLY takes the PREVIOUS stamp on a reload — directly or
       under a vendor ``@wraps`` wrapper — so a re-install wraps a wrapper, or,
       on a PLAIN reload (no re-install at all), the assignment rewrites the
       ALREADY-INSTALLED stamp's delegate to itself, because the module body
       re-executes in the same ``__dict__``. The marker prevents a re-install;
       it cannot prevent that rewrite — which is why the body itself now keeps
       the captured original (#11217,
       `test_a_RELOAD_keeps_the_REAL_Thread_start`), and why this arm must
       still catch the shape if that guard is ever removed. Fact 1 sees the
       re-install shape (two stamps — measured count 2, path
       ``[stamp2, old_stamp, old_stamp]``); the self-revisit shape is the one
       only fact 2 can see (count stays 1, path ``[s, s]``). One-hop fails for
       both whenever the delegate is a WRAPPER whose inner frame is the previous
       stamp — which is why fact 2 is checked by REACHABILITY.
    """
    links = _stamp_links(start)
    problems = []
    if len(links) != 1:
        problems.append(
            "expected exactly ONE test-stem stamp on Thread.start, "
            f"found {len(links)} (0 = never installed; 2+ = a stamp stacked on a stamp)")
    for link in links:
        delegate = link.__globals__.get("_ORIG_THREAD_START")
        if any(_is_stamp(frame) for frame in _delegation_path(delegate)):
            message = ("a test-stem stamp delegates THROUGH another stamp — a double "
                       "wrap (a reload rewrote the delegate, and/or a re-install "
                       "stacked a stamp)")
            if message not in problems:  # one report, however many stamps stack
                problems.append(message)
    return problems


def _assert_probe(results: list, expected_stem: str | None, embedded: bool) -> None:
    """Shared probe assertion: exactly one probe result with the expected
    inherited stem + embedded flag + a graph name."""
    assert len(results) == 1, f"expected one probe result, got {results!r}"
    stem, is_embedded, graph_name = results[0]
    assert stem == expected_stem
    assert is_embedded is embedded
    return graph_name


def test_worker_thread_carve_out_stays_embedded(monkeypatch):
    """#1686: a worker thread spawned by a carve-out test resolves the test
    module INHERITED from its spawner (conftest's runtest hook recorded the
    stem; the patched Thread.start stamped it) — the construction stays
    EMBEDDED under a URI-set process, exactly as the frame gate holds for
    the main thread."""
    import threading

    from tests._embedded import _worker_stem_embedded_probe

    monkeypatch.setenv("TORTOISE_DB_URI", _live_utils.docker_base_uri())
    monkeypatch.setenv("TORTOISE_TEST_NO_REDIRECT", "test_redirect_seam")
    # conftest's pytest_runtest_setup recorded this module's stem on the
    # main thread before the test body ran — sanity-check the hook is live.
    from tortoise.projection import _inherited_test_stem
    assert _inherited_test_stem() == "test_redirect_seam", \
        "conftest stem hook not active for this module"

    results: list = []
    t = threading.Thread(
        target=_worker_stem_embedded_probe, args=(results, _tmp_db("seam-worker-a.db")))
    t.start()
    t.join(timeout=20)
    assert not t.is_alive(), "worker thread hung — stamp/construction deadlock"
    _assert_probe(results, "test_redirect_seam", embedded=True)


def test_worker_thread_non_exempt_redirects(monkeypatch):
    """#1686: a worker thread spawned by a NON-exempt test still REDIRECTS —
    the inherited stem resolves (it just isn't in the exemption list), so
    the process flag fires exactly as it did pre-#1686 for worker threads."""
    import threading
    import types

    from tests._embedded import _worker_stem_embedded_probe

    monkeypatch.setenv("TORTOISE_DB_URI", _live_utils.docker_base_uri())
    monkeypatch.setenv("TORTOISE_TEST_NO_REDIRECT", "some_other_stem")
    # Host-branch stub (same surface as test_allow_remote_escape above):
    # the redirect's host branch constructs falkordb.FalkorDB, whose real
    # __init__ round-trips to the server — stub select_graph/close only.
    class _GraphStub:
        def query(self, *a, **k):
            return types.SimpleNamespace(result_set=[])

    class _DbStub:
        def select_graph(self, name):
            return _GraphStub()
        def close(self):
            pass

    class _FakeFalkorDB:
        def __init__(self, **kw):
            self._graph = _GraphStub()
        def select_graph(self, name):
            return self._graph
        def close(self):
            pass

    monkeypatch.setattr("falkordb.FalkorDB", _FakeFalkorDB)

    results: list = []
    t = threading.Thread(
        target=_worker_stem_embedded_probe, args=(results, _tmp_db("seam-worker-b.db")))
    t.start()
    t.join(timeout=20)
    assert not t.is_alive(), "worker thread hung"
    graph_name = _assert_probe(results, "test_redirect_seam", embedded=False)
    assert graph_name.startswith("test_seam_worker_b_"), graph_name


def _thread_start_stamp_violations() -> list[str]:
    """The install contract for the Thread.start stamp, as a VERDICT.

    Extracted so a TEST can make it fire in a CHOSEN process state — the states
    are driven by `monkeypatch` in
    `test_a_third_party_wrapper_on_thread_start_is_not_a_double_wrap` (there is
    no fixture; this helper takes no arguments and reads whatever
    `threading.Thread.start` currently is): inline in the assertion it only ever
    ran in the one state the session happened to be in, so the identity form
    below could not be shown to be the WRONG question — and it was measured
    wrong in CI (PR #11354, `test (b)`, run 42362934147):

        AssertionError: Thread.start was double-wrapped (installer re-ran
        without the marker guard)

    `threading.Thread.start is _thread_start_inherit_stem` holds only until the
    REAL `sentry_sdk` is initialised: `sentry_sdk.integrations.threading`
    installs `sentry_start` on top of whatever `Thread.start` already is, with
    `@functools.wraps(old_start)` — so the outermost function is Sentry's (and
    its `__qualname__` is even `_thread_start_inherit_stem`, copied by `wraps`),
    and a HEALTHY process read as double-wrapped. Measured: the wrapper's
    `__globals__` is `sentry_sdk.integrations.threading`'s dict while
    `tortoise.projection._thread_start_inherit_stem` is untouched in its own.

    The properties that are actually invariant, and are what the marker pin is
    for:

    (1) the marker is set (conftest's installer ran — never module-body, so a
        leaked TEST_MODE env cannot patch stdlib);
    (2) OUR wrapper is INSTALLED — reachable from `threading.Thread.start`
        through the `__wrapped__` links `functools.wraps` records, at any depth
        of third-party layers above it;
    (3) re-invoking the installer changes nothing (idempotence — a non-idempotent
        installer installs a NEW function object and the identity MOVES).

    ⛔ WHAT THIS CANNOT SEE — AND WHAT IT REPORTS WRONGLY — stated so nobody
    reads it as more:

    (a) NOT a blind spot: a stale copy at `Thread.start` IS caught, and named.
        The reachability check compares OBJECTS, so a different function
        carrying the same name / `__module__` / `__qualname__` misses the chain
        and is reported as "… marker guard bypassed, or a stale module copy".
        Measured: driving that state produces exactly that violation, so a
        re-executed `tortoise.projection` is NOT indistinguishable from a
        third-party layer — an earlier draft of this docstring claimed it was,
        and that claim was false.

    (b) A blind spot of THIS walk: `_ORIG_THREAD_START` is never inspected
        here. The walk follows `__wrapped__` only, and this wrapper is a plain
        `def` that publishes none, so the chain stops at it. An UNGUARDED
        module body re-captured that global on `importlib.reload` and so named
        a stale copy of this wrapper (measured before #11217:
        `_ORIG_THREAD_START is <the pre-reload wrapper>`); the body now keeps
        the captured original (#11217), and the property is asserted OUTSIDE
        this walk by `test_a_RELOAD_keeps_the_REAL_Thread_start`, which checks
        it directly in a subprocess (`_ORIG_THREAD_START` IS the real
        `Thread.start`) across a real reload.
        What no arm here can see is a state where `Thread.start` is the CURRENT
        wrapper while `_ORIG_THREAD_START` holds a stale copy.

    (c) A FALSE POSITIVE, fail-closed: a layer ABOVE ours that publishes no
        `__wrapped__` — a hand-rolled `Thread.start = f` with no
        `functools.wraps` — hides our wrapper from the walk, so an INSTALLED
        wrapper is reported as "not reachable". Measured: a plain-def
        interceptor above ours yields that violation while our wrapper sits
        installed one level down. The reachability check is therefore sound only
        through `functools.wraps`-publishing layers — which is what invariant
        (2) says, and what the real wrapper in play
        (`sentry_sdk.integrations.threading`) publishes.

    So this census pins the MARKER, installer IDEMPOTENCE, and OUR WRAPPER'S
    REACHABILITY through the `__wrapped__` links `functools.wraps` records —
    not that the install cannot be what broke the stamp.
    """
    import threading

    from tortoise import projection as proj_mod

    proj = proj_mod
    violations: list[str] = []
    if getattr(threading.Thread, "_tortoise_stamp_installed", False) is not True:
        violations.append(
            "Thread.start stamp not installed in the test session "
            "(install_thread_stamp must run from conftest, never module-body)")
    # Bounded walk (a seen-set, so a cycle cannot hang the census).
    chain, seen, fn = [], set(), threading.Thread.start
    while fn is not None and id(fn) not in seen:
        chain.append(fn)
        seen.add(id(fn))
        fn = getattr(fn, "__wrapped__", None)
    if proj._thread_start_inherit_stem not in chain:
        violations.append(
            "the Thread.start stamp wrapper is not reachable from "
            f"threading.Thread.start ({threading.Thread.start!r}) — the installed "
            "wrapper is not ours (marker guard bypassed, or a stale module copy)")
    before = threading.Thread.start
    proj.install_thread_stamp()  # re-invocation → no-op (marker)
    if threading.Thread.start is not before:
        violations.append(
            "re-invoked installer re-wrapped Thread.start (the class-attribute "
            "marker did not make it idempotent)")
    return violations


def test_a_third_party_wrapper_on_thread_start_is_not_a_double_wrap(monkeypatch):
    """#11354: the stamp census must survive a wrapper it does not own, and must
    still report the two failures it does claim to catch.

    The first arm is the CI failure itself, reproduced: `test (b)`'s run
    42362934147 initialised the REAL Sentry SDK (tests/test_11092_sentry_loop_stall.py's
    `live_sentry_client`), whose `ThreadingIntegration` puts a `@functools.wraps`
    layer on `Thread.start`, and the old identity assertion read that healthy
    state as "double-wrapped". The other three arms are the verdict's TEETH — an
    absent stamp, a wiped install MARKER, and an installer that is not
    idempotent — so arm 1 cannot pass by being blind, and no branch of the
    helper can be deleted without reddening a row here (agent-infra#3442).
    """
    import functools
    import threading

    from tortoise import projection as proj_mod

    assert not _thread_start_stamp_violations(), "the session state is already broken"

    installed = threading.Thread.start

    @functools.wraps(installed)  # exactly sentry_sdk.integrations.threading's shape
    def _sentry_style_start(self, *a, **k):
        return installed(self, *a, **k)

    monkeypatch.setattr(threading.Thread, "start", _sentry_style_start)
    assert threading.Thread.start is not proj_mod._thread_start_inherit_stem, (
        "the outer wrapper did not take effect — this arm would prove nothing")
    assert not _thread_start_stamp_violations(), (
        "a third-party @functools.wraps layer on Thread.start was read as a "
        "double-wrap of OUR installer (#11354)")

    # TEETH 1: the stamp GONE (installer never ran, or a reload/restore wiped it)
    # must be reported, not silently accepted as "some wrapper is installed".
    monkeypatch.setattr(threading.Thread, "start", proj_mod._ORIG_THREAD_START)
    assert any("not reachable" in v for v in _thread_start_stamp_violations()), (
        "an ABSENT stamp passed the census")

    # TEETH 2: an installer that re-wraps on every call must be reported — this
    # is the idempotence half, and it is the shape the marker guard exists for.
    def _non_idempotent_install():
        current = threading.Thread.start

        @functools.wraps(current)
        def _again(self, *a, **k):
            return current(self, *a, **k)

        threading.Thread.start = _again

    monkeypatch.setattr(proj_mod, "install_thread_stamp", _non_idempotent_install)
    monkeypatch.setattr(threading.Thread, "start", _sentry_style_start)
    assert any("re-wrapped" in v for v in _thread_start_stamp_violations()), (
        "a non-idempotent installer passed the census — the idempotence "
        "assertion is vacuous")

    # TEETH 3: the MARKER cleared while our wrapper is still reachable. The
    # marker and the install are two SEPARATE facts that the installer sets
    # together (`projection.install_thread_stamp` assigns `Thread.start` and
    # `_tortoise_stamp_installed` in the same branch), so this arm pins that the
    # census NOTICES if they ever diverge — a future install path, or a
    # reload/restore that clears the class attribute.
    # ⛔ It is deliberately NOT reachable today by a leaked `TORTOISE_TEST_MODE`
    # env: the installer refuses without conftest's `_TEST_SESSION_ACTIVE`, so
    # "installed without the marker" cannot happen (that is why the helper says a
    # leaked env cannot patch stdlib). The arm exists so a divergence is not
    # SILENT if one is ever introduced — not because one is possible now.
    # The installer is stubbed to a no-op for this arm: the REAL one with the
    # marker off would legitimately re-wrap, and that re-wrap is TEETH 2's
    # subject, not this one's — isolating it here is what makes the row
    # attributable to the marker branch alone.
    monkeypatch.setattr(proj_mod, "install_thread_stamp", lambda: None)
    monkeypatch.setattr(threading.Thread, "_tortoise_stamp_installed", False)
    monkeypatch.setattr(threading.Thread, "start", _sentry_style_start)
    assert any("not installed" in v for v in _thread_start_stamp_violations()), (
        "a CLEARED install marker passed the census — the marker and the install "
        "are separate facts, and the census must notice when they diverge")


def test_stem_registry_prod_parity_and_patch_idempotence(monkeypatch):
    """#1686 prod parity + install idempotence: (a) outside an active
    session the registry REFUSES records (no inheritance → None, the frame
    gate's None semantics — subprocess CLI children), and (b) the Thread.start
    stamp is installed exactly once and only by conftest's installer
    (install_thread_stamp — never module-body, so a leaked TEST_MODE env
    cannot patch stdlib; the class-attribute marker makes the installer
    idempotent, so it cannot double-wrap even if re-invoked after a
    reload)."""
    from tortoise import projection as proj_mod

    # (a) clear the conftest hook's main-thread record FIRST (it ran at
    # setup), then deactivate the session for the parity probe ONLY — scoped,
    # because (b) needs the installer ARMED. (An unscoped deactivation made
    # (b) a tautology: `install_thread_stamp` gates on `_TEST_SESSION_ACTIVE`
    # BEFORE it consults the class marker, so the installer body could never
    # run and the re-invocation assertion compared a list to itself.)
    proj_mod._record_current_test_stem(None)
    with monkeypatch.context() as scoped:
        scoped.setattr(proj_mod, "_TEST_SESSION_ACTIVE", False)
        proj_mod._record_current_test_stem("test_redirect_seam")  # refused
        assert proj_mod._inherited_test_stem() is None, \
            "prod parity broken: record accepted outside an active session"

    # (b) install idempotence: conftest installed the wrapper at session
    # start (install_thread_stamp called after _TEST_SESSION_ACTIVE=True),
    # the marker is set, and re-invoking the installer is a no-op. The check is
    # CHAIN-based, not identity-based: a vendor integration may legitimately
    # wrap Thread.start on top of our stamp, and `@wraps` copies our name onto
    # it, so identity (and any name/module test) reports a double wrap that
    # never happened (#11430).
    assert getattr(threading.Thread, "_tortoise_stamp_installed", False) is True, \
        "Thread.start stamp not installed in the test session"
    problems = _stamp_problems(threading.Thread.start)
    assert problems == [], f"Thread.start stamp invariant broken: {problems}"

    # The probe below is only meaningful with the installer ARMED — the whole
    # tautology this replaces came from an unscoped `_TEST_SESSION_ACTIVE =
    # False`, under which the installer body cannot run even with the marker
    # guard deleted. Assert the arming, so the premise cannot silently vanish.
    assert proj_mod._TEST_SESSION_ACTIVE is True, \
        "installer is not armed: the re-invocation probe below would be vacuous"
    assert os.environ.get("TORTOISE_TEST_MODE") == "1", \
        "TORTOISE_TEST_MODE is not set: the installer would refuse for the wrong reason"

    # The re-invocation probe is made FALSIFIABLE, not self-comparing: point
    # the installer's own install target at a distinguishable function, so a
    # re-run of the installer BODY is observable at all. Under the marker guard
    # the body cannot run; with the guard deleted the body installs the
    # sentinel and the assertion below fails.
    live_stamp = proj_mod._thread_start_inherit_stem
    sentinel = types.FunctionType(
        live_stamp.__code__, live_stamp.__globals__, _STAMP_NAME)
    monkeypatch.setattr(proj_mod, "_thread_start_inherit_stem", sentinel)
    before_obj = threading.Thread.start
    # Restore Thread.start if the body DID run: `install_thread_stamp` assigns
    # the class attribute directly, so a failure here would otherwise leave the
    # sentinel installed for every later test in the shard.
    monkeypatch.setattr(threading.Thread, "start", before_obj)

    proj_mod.install_thread_stamp()  # re-invocation → no-op (marker)
    # The falsifier: the installer BODY did not run. Deliberately NOT an
    # identity assert on Thread.start — a vendor integration may legitimately
    # repaint stdlib at any moment (sentry's `@wraps` wrapper), and an identity
    # check would then red for a reason that has nothing to do with the marker.
    assert threading.Thread.start is not sentinel, \
        "re-invoked installer installed a fresh stamp — the class marker guard is gone"


def test_the_double_wrap_verdict_reads_the_CHAIN_not_the_identity():
    """#11430: the two states the removed identity assert confused.

    The assert was `threading.Thread.start is proj_mod._thread_start_inherit_stem`,
    with the message "installer re-ran without the marker guard". It is wrong in
    both directions:

    (1) FALSE RED — a vendor wrapper over a healthy stamp. Once any test
        initialises the real ``sentry_sdk``, its ``ThreadingIntegration``
        replaces ``Thread.start`` with ``@wraps(old_start) def sentry_start(...)``
        over our stamp. Our installer never re-ran; the stamp still runs first.
        This is the measured `test (b)` red of the #11368 run (job
        128410663087), reproduced by running
        `tests/test_11092_sentry_loop_stall.py tests/test_redirect_seam.py` in
        ONE pytest process.
    (2) FALSE GREEN — a genuine double wrap. A module body that captures
        ``_ORIG_THREAD_START = Thread.start`` UNCONDITIONALLY takes the PREVIOUS
        stamp on a reload; the body now guards that assignment (#11217), so
        this shape is synthesised below rather than produced by a real reload.
        The reload alone cannot stack anything: it also resets
        ``_TEST_SESSION_ACTIVE`` and the class marker survives, so the installer
        is a no-op for two reasons. With an UNGUARDED body, clearing the marker
        AND re-arming the flag (what a test had to do to reach it) then
        installed the reloaded module's stamp ON the old one — ``Thread.start``
        was that new function, so identity was TRUE and the check the message
        promised could not see it, while a chain-length check saw two stamps.
        With the guard in place that path no longer reaches the state at all
        (measured: after a reload + re-install ``_ORIG_THREAD_START`` is still
        the REAL ``Thread.start``, so the new stamp is the only layer), which is
        why this shape is synthesised below instead. An unguarded PLAIN reload
        with no re-install was worse still: it rewrote the installed stamp's
        delegate to ITSELF, so the count stayed 1, only reachability saw it, and
        ``Thread.start()`` recursed.
    """
    links = _stamp_links(threading.Thread.start)
    assert len(links) == 1, links
    stamp = links[0]

    # (1) the VENDOR shape: `functools.wraps` copies our name AND module onto the
    # vendor's function, exactly as sentry_sdk's integration does.
    @functools.wraps(stamp)
    def sentry_start(self, *a, **k):  # pragma: no cover - shape, never called
        return stamp(self, *a, **k)

    assert sentry_start.__name__ == _STAMP_NAME
    assert sentry_start.__module__ == "tortoise.projection"
    assert sentry_start is not stamp, "the removed identity assert called this a double wrap"
    vendor_problems = _stamp_problems(sentry_start)
    assert vendor_problems == [], \
        f"a vendor wrapper over a healthy stamp is not a double wrap: {vendor_problems}"

    # (2) the GENUINE double wrap: a stamp whose `_ORIG_THREAD_START` is the
    # previous stamp — the state an UNGUARDED reload + re-install created
    # (before #11217; the guard keeps the real original, so it is synthesised
    # here rather than reached).
    reloaded = types.FunctionType(
        stamp.__code__,
        {"__name__": "tortoise.projection", "_ORIG_THREAD_START": stamp},
        _STAMP_NAME,
    )
    assert _is_stamp(reloaded) and reloaded is not stamp
    problems = _stamp_problems(reloaded)
    assert problems, "a stamp stacked on a stamp MUST be reported as a double wrap"
    assert any("double wrap" in p for p in problems), problems


@pytest.mark.parametrize("shape", ["plain", "wrapped_delegate", "cycle"])
def test_the_stamp_delegate_graph_is_checked_by_REACHABILITY(shape):
    """#11430 review P1s: the three shapes the one-hop ``__wrapped__`` walk missed.

    ``plain`` — a plain delegating wrapper (no ``@wraps``, so no ``__wrapped__``)
    that DOES call our stamp: the one-hop walk saw no stamp and reported "never
    installed", a FALSE RED. It is a legitimate wrapper and must be tolerated.

    ``wrapped_delegate`` — the reload-in-vendor-order state an UNGUARDED module
    body produced (before #11217; synthesised here, since the guard keeps the
    captured original): a stamp whose ``_ORIG_THREAD_START`` is a ``@wraps``
    wrapper *over* the previous stamp.
    The one-hop arm tested only the immediate delegate, found the vendor wrapper
    (not a stamp) and reported nothing while ``Thread.start()`` recursed. The
    stamp must be found by REACHABILITY.

    ``cycle`` — a delegate graph that revisits a frame: every length check still
    passes and ``start()`` never returns, so the cycle itself must be a
    violation.
    """
    links = _stamp_links(threading.Thread.start)
    assert len(links) == 1, links
    stamp = links[0]

    if shape == "plain":
        def plain_wrapper(self, *a, **k):  # pragma: no cover - shape, never called
            return stamp(self, *a, **k)

        assert _stamp_links(plain_wrapper) == [stamp], \
            "a plain wrapper that CALLS our stamp must not read as 'never installed'"
        assert _stamp_problems(plain_wrapper) == [], _stamp_problems(plain_wrapper)
        return

    if shape == "wrapped_delegate":
        @functools.wraps(stamp)
        def vendor_start(self, *a, **k):  # pragma: no cover - shape, never called
            return stamp(self, *a, **k)

        # An unguarded reloaded module captured `Thread.start` — here the vendor
        # wrapper over the previous stamp — as its delegate, then installed on
        # top. #11217's guard keeps the real original across a reload, so this
        # shape is synthesised rather than reached.
        reloaded = types.FunctionType(
            stamp.__code__,
            {"__name__": "tortoise.projection", "_ORIG_THREAD_START": vendor_start},
            _STAMP_NAME,
        )
        problems = _stamp_problems(reloaded)
        assert any("double wrap" in p for p in problems), problems
        return

    # self-revisit: the state an UNGUARDED module body produced on a PLAIN
    # `importlib.reload`. The body now keeps the captured original, so this is
    # synthesised here (see `test_a_RELOAD_keeps_the_REAL_Thread_start` for the
    # real-reload arm). The module body re-executes in the SAME `__dict__`, so a
    # bare `_ORIG_THREAD_START = Thread.start` rewrites the already-installed
    # stamp's delegate to ITSELF.
    # This is fact 2's unique domain — the stamp count stays 1, so the length
    # arm is silent and only reachability can see it.
    own = {"__name__": "tortoise.projection"}
    self_revisiting = types.FunctionType(stamp.__code__, own, _STAMP_NAME)
    own["_ORIG_THREAD_START"] = self_revisiting
    assert _stamp_links(self_revisiting) == [self_revisiting], \
        "a self-revisit must still count as ONE stamp"
    path = _delegation_path(self_revisiting)
    assert len(path) == 2 and path[0] is path[1], \
        f"the walk must terminate on the revisit, keeping it once: {path}"
    problems = _stamp_problems(self_revisiting)
    assert any("double wrap" in p for p in problems), problems

    # two stamps delegating to each other: the length arm catches it, and the
    # message is emitted ONCE, not once per stacked stamp.
    first = types.FunctionType(
        stamp.__code__, {"__name__": "tortoise.projection"}, _STAMP_NAME)
    second = types.FunctionType(
        stamp.__code__,
        {"__name__": "tortoise.projection", "_ORIG_THREAD_START": first},
        _STAMP_NAME,
    )
    first.__globals__["_ORIG_THREAD_START"] = second
    two_stack = _stamp_problems(second)
    assert any("double wrap" in p for p in two_stack), two_stack
    assert len(two_stack) == len(set(two_stack)), f"duplicate violations: {two_stack}"


def _arm_the_installer(monkeypatch, *, session: bool, mode: str | None):
    """Put `install_thread_stamp` in ONE clause's state; return the third-party
    `Thread.start` wrapper the arm starts from.

    The wrapper is a `functools.wraps` layer over whatever is installed — exactly
    `sentry_sdk.integrations.threading`'s shape — so "the installer declined" and
    "the installer replaced the integration" are distinguishable by identity.
    """
    from tortoise import projection as proj_mod

    inner = threading.Thread.start

    def _third_party_start(self, *a, **k):  # pragma: no cover - delegated
        return inner(self, *a, **k)

    wrapper = functools.wraps(inner)(_third_party_start)
    monkeypatch.setattr(threading.Thread, "start", wrapper)
    monkeypatch.delattr(threading.Thread, "_tortoise_stamp_installed", raising=False)
    monkeypatch.setattr(proj_mod, "_TEST_SESSION_ACTIVE", session)
    if mode is None:
        monkeypatch.delenv("TORTOISE_TEST_MODE", raising=False)
    else:
        monkeypatch.setenv("TORTOISE_TEST_MODE", mode)
    return wrapper


def test_the_installer_guard_clauses_each_decline_by_effect(monkeypatch):
    """#11373: `install_thread_stamp`'s two NON-marker clauses, and its `== "1"`
    spelling, each pinned by the state it must REFUSE.

    The MARKER clause already has a by-effect pin (`test_stem_registry_prod_parity_and_patch_idempotence`'s
    sentinel falsifier: a re-invocation must not rerun the installer body). The other
    two clauses had none. Measured on `dbbf5d874c`: deleting `and
    _TEST_SESSION_ACTIVE` from the installer left this module green, deleting
    `TORTOISE_TEST_MODE == "1"` left it green, and relaxing that clause to a
    truthiness test (`os.environ.get(...)` alone) left it green too — `"0"` is a
    non-empty string, so it is truthy.

    The property behind the two clauses is the one the installer's own docstring
    names: a leaked `TORTOISE_TEST_MODE=1` in a PROD process must not patch
    stdlib, which is why the install is a function conftest calls and never a
    module-body side effect. A guard no test can fail is not a guard, and both of
    these could be deleted with a fully green suite.

    Every arm is by EFFECT on `Thread.start`, never by reading a flag, and arm (0)
    is the NON-VACUITY row the other three need: an installer that assigns nothing
    satisfies every "declines" arm below.

    ⛔ What each mutation reds, measured — so the next mutation-tester is not sent
    down a wrong path: deleting `and _TEST_SESSION_ACTIVE` reds (d); deleting
    `TORTOISE_TEST_MODE == "1"` reds (e); relaxing it to a truthiness test reds
    (f). ⛔ Where an ABSENT install lands depends on WHICH statement is deleted,
    because the guard is three clauses but the install is two statements — so
    "the installer is broken" is not one mutation and has not one landing point,
    measured:

      delete the guard (`if False:`)      -> the PRECONDITION above reds first
                                             (conftest installs through this same
                                             function, so nothing is ever
                                             installed or marked) and (0)-(f)
                                             never run
      delete the marker-RECORDING line    -> the PRECONDITION again, same reason
      delete only `Thread.start = …`      -> the marker IS still recorded (separate
                                             statement), so the precondition
                                             PASSES and the red lands at arm (0)
                                             **within this test** — it is NOT the
                                             only red in the file: measured, the
                                             same mutant gives 9 failed / 12
                                             passed / 6 skipped, and the file
                                             reds first in
                                             `test_worker_thread_carve_out_stays_embedded`,
                                             `test_worker_thread_non_exempt_redirects`
                                             and
                                             `test_stem_registry_prod_parity_and_patch_idempotence`
                                             (`problems == ['expected exactly ONE
                                             test-stem stamp …']`) before this
                                             test's arm (0) is reached, because
                                             the stamp is genuinely absent from
                                             `Thread.start` for every other arm
                                             too. Read the middle column as WHERE
                                             THIS ARM LANDS, not as the mutant's
                                             only landing point.

    That last row is why (0) exists: it is the non-vacuity row that catches an
    installer which runs, records, and installs nothing.
    """
    from tortoise import projection as proj_mod

    assert getattr(threading.Thread, "_tortoise_stamp_installed", False) is True, (
        "precondition: conftest's installer must have run in this session")

    # (0) NON-VACUITY — all three clauses satisfied, marker absent: the installer
    # MUST install. Without this row each "declines" assertion below is
    # satisfiable by an installer that never assigns anything at all.
    # ⛔ NO local "and it recorded the marker" row (round-17 review). `_arm_the_installer`
    # clears `_tortoise_stamp_installed`, so a post-install `True` here LOOKS like
    # the installer's own record — but the precondition at the top of this test
    # already asserted that same attribute, set by conftest's install through the
    # SAME `install_thread_stamp`, so deleting the product's record line reds the
    # PRECONDITION before this arm is ever reached. The row could not fail on its
    # own, and the behaviour it named is pinned by that precondition plus the
    # idempotence row in `test_stem_registry_prod_parity_and_patch_idempotence`.
    _arm_the_installer(monkeypatch, session=True, mode="1")
    proj_mod.install_thread_stamp()
    assert threading.Thread.start is proj_mod._thread_start_inherit_stem, (
        "with the session live, the test-mode signal set and the marker absent, "
        "the installer must install the stamp — otherwise the decline arms below "
        "prove nothing about the guards that are supposed to fire")

    # (d) the SESSION-FLAG clause: marker absent (so the marker clause would let
    # the install through) and `_TEST_SESSION_ACTIVE` off. This is the signal a
    # prod process can never satisfy, and it must decline — AND must not record
    # the marker, or a later in-session call would skip the real install.
    wrapper = _arm_the_installer(monkeypatch, session=False, mode="1")
    proj_mod.install_thread_stamp()
    assert threading.Thread.start is wrapper, (
        "the installer patched `Thread.start` with `_TEST_SESSION_ACTIVE` off — "
        "that flag is the one a prod process cannot satisfy, and it is why even a "
        "leaked `TORTOISE_TEST_MODE=1` cannot stamp stdlib")
    assert not getattr(threading.Thread, "_tortoise_stamp_installed", False), (
        "the installer recorded `_tortoise_stamp_installed` while declining to "
        "install — a later in-session call would then skip the real install")

    # (e) the TEST-MODE clause: session live, marker absent, env signal gone →
    # still declines. One of the two signals alone is not enough.
    wrapper = _arm_the_installer(monkeypatch, session=True, mode=None)
    proj_mod.install_thread_stamp()
    assert threading.Thread.start is wrapper, (
        "the installer patched `Thread.start` with `TORTOISE_TEST_MODE` unset — "
        "the env clause is one of two signals that must BOTH be live")
    assert not getattr(threading.Thread, "_tortoise_stamp_installed", False), (
        "the installer recorded the marker while the test-mode signal was unset")

    # (f) …and the clause is `== "1"`, not a truthiness test. `"0"` is a
    # non-empty (hence truthy) string, so a relaxed spelling installs here; the
    # strict one must not, or any session that sets a non-"1" value arms the
    # stdlib patch.
    wrapper = _arm_the_installer(monkeypatch, session=True, mode="0")
    proj_mod.install_thread_stamp()
    assert threading.Thread.start is wrapper, (
        'the installer patched `Thread.start` with `TORTOISE_TEST_MODE="0"` — the '
        'guard must compare against the literal `"1"`, not test truthiness')
    assert not getattr(threading.Thread, "_tortoise_stamp_installed", False), (
        'the installer recorded the marker for `TORTOISE_TEST_MODE="0"`')


def test_a_RELOAD_keeps_the_REAL_Thread_start():
    """#11217 round-4 review P2: the reload arm must be OBSERVED, not asserted.

    `importlib.reload` re-executes the module body into its EXISTING dict. If the
    body captures `_ORIG_THREAD_START = threading.Thread.start` unconditionally,
    then on a reload that name is rebound to the STAMP — which the stamp reads at
    CALL time, so it calls itself and every `Thread.start()` raises
    `RecursionError`. The module captures once (`globals().get(...) or …`).

    Nothing else in the tree reloads `tortoise.projection`, so without this test
    reverting the guard leaves the ENTIRE suite green while reload breaks all
    thread creation — measured (#11217 round-4 review: an import hook that
    restored the bare assignment passed every assertion in this file).

    Runs in a SUBPROCESS on purpose: a reload resets `_TEST_SESSION_ACTIVE` and
    re-executes module state, which must not leak into the parent session.
    """
    script = (
        "import importlib, os, threading\n"
        "os.environ['TORTOISE_TEST_MODE'] = '1'\n"
        "import tortoise.projection as proj\n"
        "real = threading.Thread.start\n"
        "proj._TEST_SESSION_ACTIVE = True\n"
        "proj.install_thread_stamp()\n"
        "assert threading.Thread.start is not real, 'installer did not run'\n"
        "importlib.reload(proj)\n"
        "if proj._ORIG_THREAD_START is not real:\n"
        "    raise SystemExit('RELOAD_REBOUND_THE_ORIGINAL')\n"
        "t = threading.Thread(target=lambda: None)\n"
        "t.start(); t.join()\n"
        "print('RELOAD_OK')\n"
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = dict(os.environ)
    env.pop("TORTOISE_TEST_DB", None)
    env.pop("TORTOISE_DB_URI", None)
    proc = subprocess.run(
        [sys.executable, "-c", script], cwd=root, env=env,
        capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, (
        "a reload of tortoise.projection broke thread creation — the module body "
        "must KEEP the captured original, not rebind it to the stamp. "
        f"stdout={proc.stdout!r} stderr={proc.stderr[-800:]!r}")
    assert "RELOAD_OK" in proc.stdout, (
        f"the reload probe did not reach its assertion: {proc.stdout!r} "
        f"{proc.stderr[-800:]!r}")
