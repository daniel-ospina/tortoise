# tests/test_redirect_seam.py
"""Unit surface: the class-level URI-aware redirect (epic #1647 Task 1)."""
import os
import subprocess
import sys

import pytest

from tortoise.projection import FalkorProjection

# Cycle-5 P2-14: import-time snapshot of the session nonce — the stability
# probe below asserts the runtime value never mutated mid-session (a mutation
# would strand this session's graphs: the derived-name hash input AND the
# journal filename key off the ORIGINAL value). 12 hex = 48 bits (P2-1).
_SESSION_NONCE_AT_IMPORT = os.environ.get("TORTOISE_TEST_SESSION", "")


def _docker_reachable(host: str = "localhost", port: int = 6379) -> bool:
    """True when a live FalkorDB answers a TCP connect on host:port.

    Mirrors tests/test_ingest.py's _docker_falkor_reachable — the repo's
    skip-guard convention (#1436): live-FalkorDB-required tests SKIP with a
    FalkorDB-reason when the docker is absent (post-merge-validation runs
    the full suite with NO docker service), never ERROR on
    redis.ConnectionError. The fast CI job provisions the falkordb service,
    so the probe passes there and the redirect tests actually run.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


@pytest.fixture
def uri_env(monkeypatch):
    if not _docker_reachable():
        pytest.skip("live FalkorDB (localhost:6379) not reachable")
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    yield


def test_unset_uri_constructs_embedded(monkeypatch):
    # Cycle-3 P1-6: self-contained w.r.t. TORTOISE_DB_URI — a dev/CI lane
    # with a URI set must not flip this to the server lane (the P2 half-b
    # docker job would red it).
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    proj = FalkorProjection("/tmp/seam-test-a.db")
    try:
        assert proj._is_embedded is True
    finally:
        proj.close()


def test_uri_set_redirects_to_server(uri_env):
    proj = FalkorProjection("/tmp/seam-test-b.db")
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
    proj = FalkorProjection("/tmp/seam-test-c.db", graph_name="test_explicit")
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
    a = FalkorProjection("/tmp/seam-parity-a.db", graph_name="test")
    b = FalkorProjection("/tmp/seam-parity-b.db", graph_name="test")
    a2 = FalkorProjection("/tmp/seam-parity-a.db", graph_name="test")
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
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
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
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    monkeypatch.setenv("TORTOISE_TEST_NO_REDIRECT", "test_redirect_seam")
    proj = FalkorProjection("/tmp/seam-test-d.db")
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
    proj = FalkorProjection("/tmp/seam-test-d.db")
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
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    out = subprocess.run(
        [sys.executable, "-c",
         "from tortoise.projection import FalkorProjection; "
         "p = FalkorProjection('/tmp/seam-child.db', skip_health_check=True); "
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
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    monkeypatch.delenv("TORTOISE_TEST_MODE", raising=False)
    proj = FalkorProjection("/tmp/seam-test-f.db", skip_health_check=True)
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
        FalkorProjection("/tmp/seam-remote.db", skip_health_check=True)


def test_uri_hostless_refuses(monkeypatch):
    # Cycle-8 P2-1: a HOSTLESS URI (`docker://:pw@:6379`) must refuse at the
    # redirect, matching is_loopback_uri's absent-hostname → False (the old
    # `hostname or "localhost"` fallback accepted it and minted graphs on the
    # default host while the tripwire refused). Same RuntimeError class as the
    # non-loopback host case — the shared predicate is the single judge.
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:pw@:6379")
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    with pytest.raises(RuntimeError, match="loopback"):
        FalkorProjection("/tmp/seam-hostless.db", skip_health_check=True)


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
    proj = FalkorProjection("/tmp/seam-remote.db", skip_health_check=True)
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
        p = FalkorProjection(f"/tmp/seam-conn-{i}.db")
        conn = p.db.connection  # redis.Redis client
        pools.append(weakref.ref(conn.connection_pool))
        assert isinstance(conn, redis.Redis), "host branch is the raw redis client"
        p.close()
    gc.collect()
    live = [r for r in pools if r() is not None]
    assert len(live) <= 1, \
        f"close() leaked {len(live)} of 20 live ConnectionPool instances (weakref)"
    probe = redis.Redis(host="localhost", port=6379, password="falkordb",
                        socket_connect_timeout=5)
    try:
        before = int(probe.info("clients")["connected_clients"])
    finally:
        probe.close()
    for i in range(20):
        p = FalkorProjection(f"/tmp/seam-conn-{i}.db")
        p.close()
    probe = redis.Redis(host="localhost", port=6379, password="falkordb",
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
    proj = FalkorProjection("/tmp/seam-test-e.db")
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
    assert is_loopback_uri("docker://:falkordb@localhost:6379") is True
