# tests/test_wipe_server.py
"""Unit surface: server-mode wipe_server() + session journal + sweeps
(epic #1647 Task 2, D-4 — the hermeticity core)."""
import os
import subprocess
import sys
import types
import uuid

import pytest

from tests._embedded import (
    _journal_append,
    _sweep_drop,
    _wipe_or,
    wipe,
    wipe_server,
)


def _docker_reachable(host: str = "localhost", port: int = 6379) -> bool:
    """Live-FalkorDB probe (#1436 skip convention — post-merge-validation
    runs without a docker service; docker-required tests SKIP, never error)."""
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


@pytest.fixture
def server_proj(uri_env):
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_wipe_target")
    proj.g.query("CREATE (:Point {id:'x'})")
    yield proj
    proj.close()


# ── fakes for the unit-level sweep/wipe tests ──────────────────────────────


class _FakeGraph:
    def __init__(self, name, db, fail_delete=False):
        self._name = name
        self._db = db
        self.fail_delete = fail_delete

    def query(self, q, *a, **k):
        # #3214: a one-shot hook that fires at the FIRST real DETACH — the
        # deterministic stand-in for "a peer session minted a graph while the
        # sweep was already deleting earlier ones" (a real race is not
        # reliably reproducible in CI).
        hook = self._db.on_first_detach
        if hook is not None:
            self._db.on_first_detach = None
            hook()
        self._db.detached.append(self._name)
        return types.SimpleNamespace(result_set=[])

    def delete(self):
        if self.fail_delete:
            raise RuntimeError("injected delete failure")
        self._db.deleted.append(self._name)


class _FakeDb:
    def __init__(self, fail_delete=()):
        self.detached: list[str] = []
        self.deleted: list[str] = []
        self._fail_delete = set(fail_delete)
        self.graphs: list[str] = []
        # #3214 deterministic window hooks (both one-shot, fired once):
        # on_list_graphs — the peer mints just as the sweep ENUMERATES;
        # on_first_detach — the peer mints after enumeration, mid-loop.
        self.on_list_graphs = None
        self.on_first_detach = None

    def list_graphs(self):
        hook = self.on_list_graphs
        if hook is not None:
            self.on_list_graphs = None
            hook()
        return list(self.graphs)

    def select_graph(self, name):
        return _FakeGraph(name, self, fail_delete=name in self._fail_delete)


class _FakeProj:
    def __init__(self, db=None):
        self._host = "localhost"
        self._is_embedded = False
        self.graph_name = "test_sweep_probe"
        self.db = db or _FakeDb()

    def close(self):
        pass


# ── wipe_server: filter/refusal/completeness surface ───────────────────────


def test_wipe_server_clears_only_test_prefixed(server_proj):
    # Cycle-5 P2-2: delta invariant — a UUID-SUFFIXED non-test graph's
    # seeded node must SURVIVE the wipe (absolute counts on fixed names red
    # on a dev docker with pre-existing leftovers).
    import uuid
    proj = server_proj
    non_test = f"team_ws_ctrl_{uuid.uuid4().hex[:8]}"
    proj.db.select_graph(non_test).query("CREATE (:Point {id:'keep'})")
    # Epic #1647 (T7): the swept-names family — a registry-shaped and a
    # team-shaped graph must ALSO survive wipe_server untouched (the Task 7
    # namespace sweep's fail-closed guarantee: only test_/tortoise_test_*
    # are ever wiped). Per-run-unique names (review P1): the fixed literals
    # registry_tortoise/team_e2e-900 are SHARED graphs (44 registry sites +
    # the index suite) — seeding + DETACH + GRAPH.DELETE on them would
    # destroy peer sessions'/dev-docker data. The fail-closed property is
    # prefix-based, so unique names prove it identically.
    swept_a = f"registry_ws_{uuid.uuid4().hex[:8]}"
    swept_b = f"team_ws_{uuid.uuid4().hex[:8]}"
    for g in (swept_a, swept_b):
        proj.db.select_graph(g).query("CREATE (:Point {id:'keep'})")
    try:
        wipe_server(proj)
        # test-prefixed graph emptied
        assert proj.g.query("MATCH (n) RETURN count(n)").result_set[0][0] == 0
        # non-test graphs untouched (delta: the seeded nodes survive)
        for g in (non_test, swept_a, swept_b):
            assert proj.db.select_graph(g).query(
                "MATCH (n) RETURN count(n)").result_set[0][0] == 1, \
                f"non-test graph {g} must survive wipe_server (fail-closed)"
    finally:
        # the seeded non-test graphs are deliberately never wiped (fail-closed);
        # delete them here so a dev docker does not accumulate one per run
        for g in (non_test, swept_a, swept_b):
            try:  # noqa: SIM105
                proj.db.select_graph(g).delete()
            except Exception:
                pass


def test_wipe_server_global_scope_spares_live_peer_graphs(monkeypatch, tmp_path):
    """#3074: a scope=None (server-global) sweep must never DETACH a graph
    journaled by a LIVE PEER session.

    Regression: migrated test graphs are server-GLOBAL ``test_*`` names on
    one shared Docker FalkorDB, so ``wipe_server(proj)`` (scope=None) used
    to DETACH every ``test_``-prefixed graph on the server — including a
    concurrently running session's. That surfaced as a random test losing
    the nodes it had written moments earlier, e.g.
    ``tests/test_projection.py::test_falkor_apply_points_merged`` failing at
    ``assert count(n:Point {id:'a'}) == 1`` with ``0 == 1`` (both ``a`` and
    ``b`` gone). ``tests/test_wipe_server.py`` calls ``wipe_server(proj)``
    (scope=None) itself, so an unlucky interleaving was reproducible while
    running this file next to any other session.

    Ownership is the session journal (the single source of truth for the
    graphs a session minted) keyed by live-session nonces: a live peer's
    graphs survive, unowned/orphan graphs are still swept.
    """
    peer_nonce = "abcdef123456"
    (tmp_path / f"{peer_nonce}.graphs.jsonl").write_text(
        "test_peer_live_graph\ntest_peer_live_graph_2\n")
    monkeypatch.setenv("TORTOISE_TEST_SESSION", "000000000000")
    monkeypatch.setattr(
        "tortoise.embedded_reaper.ACTIVE_SUITES_DIR", str(tmp_path))
    monkeypatch.setattr(
        "tortoise.embedded_reaper.active_suite_markers",
        lambda: [{"token": f"{os.getpid()}-{peer_nonce}",
                  "pid": os.getpid(), "start": None}])
    db = _FakeDb()
    db.graphs = ["test_peer_live_graph", "test_peer_live_graph_2",
                 "test_orphan_graph"]
    wipe_server(_FakeProj(db), scope=None)
    assert db.detached == ["test_orphan_graph"], (
        "a live peer session's journaled graphs must survive a scope=None "
        f"sweep; detached={db.detached}")


def test_wipe_server_global_scope_sweeps_own_session_graphs(monkeypatch,
                                                            tmp_path):
    """#3074 companion: the protection is PEER-only — a session may still
    sweep its OWN journaled graphs with scope=None (the last-suite-standing
    leftover sweep and this file's own wipe_server calls depend on that)."""
    our_nonce = "000000000000"
    monkeypatch.setenv("TORTOISE_TEST_SESSION", our_nonce)
    (tmp_path / f"{our_nonce}.graphs.jsonl").write_text("test_own_graph\n")
    monkeypatch.setattr(
        "tortoise.embedded_reaper.ACTIVE_SUITES_DIR", str(tmp_path))
    monkeypatch.setattr(
        "tortoise.embedded_reaper.active_suite_markers",
        lambda: [{"token": f"{os.getpid()}-{our_nonce}",
                  "pid": os.getpid(), "start": None}])
    db = _FakeDb()
    db.graphs = ["test_own_graph"]
    wipe_server(_FakeProj(db), scope=None)
    assert db.detached == ["test_own_graph"], db.detached


# ── #3214: TOCTOU in the live-peer protection ──────────────────────────────

def _peer_env(monkeypatch, tmp_path, peer_nonce):
    """Wire a LIVE peer session whose journal is ``tmp_path/{nonce}.graphs.jsonl``.

    Returns the journal path so a test can write it at a CHOSEN moment — the
    deterministic stand-in for a peer minting a graph inside the window (a
    real race is not reliably reproducible in CI).
    """
    journal = tmp_path / f"{peer_nonce}.graphs.jsonl"
    monkeypatch.setenv("TORTOISE_TEST_SESSION", "000000000000")
    monkeypatch.setattr(
        "tortoise.embedded_reaper.ACTIVE_SUITES_DIR", str(tmp_path))
    monkeypatch.setattr(
        "tortoise.embedded_reaper.active_suite_markers",
        lambda: [{"token": f"{os.getpid()}-{peer_nonce}",
                  "pid": os.getpid(), "start": None}])
    return journal


def test_wipe_server_toctou_peer_minted_during_enumeration_is_spared(
        monkeypatch, tmp_path):
    """#3214: the guard snapshotted the protected set BEFORE enumerating, so
    a peer graph minted after the snapshot but visible by delete time was
    still swept.

    The window is forced deterministically: the peer journals
    ``test_peer_toctou_new`` the moment the sweeper calls ``list_graphs()`` —
    i.e. after the old up-front snapshot, and before any DETACH. Pre-fix the
    name was outside the stale snapshot and got detached; post-fix the
    per-graph re-read sees it and spares it. A real race is not reliably
    reproducible in CI, so an ordered fake supplies the interleaving.
    """
    journal = _peer_env(monkeypatch, tmp_path, "peer00000001")
    peer_graph = "test_peer_toctou_new"
    db = _FakeDb()
    db.graphs = ["test_toctou_own", peer_graph]
    db.on_list_graphs = lambda: journal.write_text(peer_graph + "\n")
    wipe_server(_FakeProj(db), scope=None)
    assert peer_graph not in db.detached, (
        "a peer graph minted after the protection snapshot but visible at "
        f"delete time must survive the scope=None sweep; detached={db.detached}")
    assert db.detached == ["test_toctou_own"], db.detached


def test_wipe_server_toctou_peer_minted_mid_loop_is_spared(monkeypatch,
                                                           tmp_path):
    """#3214 (the discriminator): the peer graph is minted AFTER enumeration —
    while the sweeper is already DETACHing earlier graphs — and must still
    survive.

    This is why the fix re-checks PER GRAPH rather than merely re-deriving
    the protected set once after enumerating: the loop spans one server
    round-trip per graph, so a post-enumeration snapshot still leaves every
    graph after the first inside the window. Here the peer journals on the
    first DETACH, so the name falls outside ANY up-front snapshot and only a
    re-read immediately before that graph's own DETACH can see it.
    """
    journal = _peer_env(monkeypatch, tmp_path, "peer00000002")
    peer_graph = "test_peer_mid_loop"
    db = _FakeDb()
    db.graphs = ["test_toctou_own", peer_graph]
    db.on_first_detach = lambda: journal.write_text(peer_graph + "\n")
    wipe_server(_FakeProj(db), scope=None)
    assert db.detached == ["test_toctou_own"], (
        "a peer graph minted mid-loop must survive — only a per-graph "
        f"re-check can see it; detached={db.detached}")


def test_wipe_server_scope_explicit_ignores_peer_protection(monkeypatch,
                                                            tmp_path):
    """#3214 do-not-over-fix: an EXPLICIT scope names the caller's OWN graphs
    (the per-test scope is journal-derived from the caller's session), so the
    peer protection must not apply to it. #3074's protection is peer-only and
    reaches the scope=None (server-global) sweep alone — moving the check must
    not quietly turn explicit scopes into no-ops."""
    journal = _peer_env(monkeypatch, tmp_path, "peer00000003")
    shared = "test_shared_name"
    journal.write_text(shared + "\n")
    db = _FakeDb()
    db.graphs = [shared]
    wipe_server(_FakeProj(db), scope={shared})
    assert db.detached == [shared], (
        "a scope-explicit wipe must stay untouched by the peer guard: the "
        f"caller's own graph is always the caller's to sweep; got {db.detached}")


def test_live_peer_session_graphs_is_the_peer_journal_union(monkeypatch,
                                                            tmp_path):
    """#3214: the ownership primitive is the union of the LIVE peers' journal
    files, and it is composed of the two layers ``wipe_server`` now uses —
    the marker scan (``_live_peer_journal_files``, run ONCE) and the journal
    reads (``_peer_journaled_graphs``, re-run per graph)."""
    from tests._embedded import (
        _live_peer_journal_files,
        _live_peer_session_graphs,
        _peer_journaled_graphs,
    )
    journal = _peer_env(monkeypatch, tmp_path, "peer00000004")
    journal.write_text("test_peer_a\ntest_peer_b\n")
    paths = _live_peer_journal_files()
    assert paths == [str(journal)]
    assert _peer_journaled_graphs(paths) == {"test_peer_a", "test_peer_b"}
    assert _live_peer_session_graphs() == {"test_peer_a", "test_peer_b"}


def test_wipe_server_localhost_acceptance(uri_env):
    # Cycle-3 P0-1 (RED-FIRST): from_uri with a LOOPBACK host must WIPE, not
    # raise. Host extraction reads the host RECORDED ON THE PROJECTION
    # (self._host, Task 1) — the raw falkordb client's .connection has no
    # .host (redis-py 8.1.0: host lives in
    # connection_pool.connection_kwargs['host']), so the old getattr path
    # returned None → _is_loopback_host(None) → False → every server
    # projection was refused.
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_local_accept")
    try:
        assert proj._host == "localhost"  # recorded on the projection (P0-1)
        proj.g.query("CREATE (:Point {id:'x'})")
        wipe_server(proj)  # must NOT raise RuntimeError
        assert proj.g.query("MATCH (n) RETURN count(n)").result_set[0][0] == 0
    finally:
        proj.close()


def test_wipe_server_skips_non_test_graphs(server_proj):
    # Cycle-5 P2-2: delta instead of absolute-zero — seed a uuid-suffixed
    # non-test graph, wipe, assert its nodes SURVIVE.
    import uuid
    non_test = f"registry_ws_{uuid.uuid4().hex[:8]}"
    server_proj.db.select_graph(non_test).query("CREATE (:Point {id:'keep'})")
    try:
        wipe_server(server_proj)
        assert server_proj.db.select_graph(non_test).query(
            "MATCH (n) RETURN count(n)").result_set[0][0] == 1  # never touched
    finally:
        try:  # noqa: SIM105
            server_proj.db.select_graph(non_test).delete()
        except Exception:
            pass


def test_wipe_server_refuses_non_loopback(monkeypatch):
    # Cycle-4 P1-1: a REAL FalkorProjection(host="db.internal...") can never
    # reach wipe_server — the falkordb client's __init__ does a LIVE
    # round-trip and raises redis ConnectionError first. Stub the projection
    # instead: wipe_server reads ONLY proj._host (the host check raises
    # first) — pinned with a SimpleNamespace.
    proj = types.SimpleNamespace(_host="db.internal.example.com")
    with pytest.raises(RuntimeError, match="loopback"):
        wipe_server(proj)


def test_embedded_wipe_still_refuses_server_mode(server_proj):
    with pytest.raises(RuntimeError, match="EMBEDDED"):
        wipe(server_proj)  # the pre-existing refusal must survive


def test_wipe_server_completeness(server_proj, monkeypatch):
    # Cycle-2 P2-7 + cycle-3 P2-7: after wipe_server, the fixture's OWN
    # test_-prefixed graphs must retain no nodes — a silently-skipped graph
    # would break the hermeticity claim. list_graphs is faked to the
    # fixture's graphs so the completeness property is tested without
    # touching unrelated graphs.
    created = []
    for g in ["test_ws_wipe_target", "test_ws_second_target"]:
        server_proj.db.select_graph(g).query("CREATE (:Point {id:'x'})")
        created.append(g)
    monkeypatch.setattr(server_proj.db, "list_graphs", lambda: created)
    wipe_server(server_proj)
    for g in created:
        n = server_proj.db.select_graph(g).query(
            "MATCH (n) RETURN count(n)").result_set[0][0]
        assert n == 0, f"graph {g} still has {n} nodes after wipe_server"


def test_wipe_server_failure_is_collected(server_proj, monkeypatch):
    # Cycle-2 P2-7: a failing DETACH must re-raise, not pass silently.
    def _boom(*a, **k):
        raise RuntimeError("injected")
    monkeypatch.setattr(server_proj.db, "select_graph", _boom)
    with pytest.raises(RuntimeError, match="test_ws_wipe_target"):
        wipe_server(server_proj)


def test_drop_delete_uses_command_vector(monkeypatch):
    # Cycle-6 P1-0 (FM-2): the drop loop must invoke GRAPH.DELETE as a
    # COMMAND, never as a Cypher query. The vendored client's Graph.query()
    # sends ["GRAPH.QUERY", name, q, "--compact"] — so
    # select_graph(g).query("GRAPH.DELETE") is a Cypher PARSE ERROR on every
    # journaled graph → wipe_server(drop=True) raises at teardown. The
    # correct invocation is graph.delete() (execute_command("GRAPH.DELETE",
    # name)) — pinned here so a re-introduction of query("GRAPH.DELETE") reds.
    calls = []

    class _FakeGraph:
        def __init__(self, name):
            self._name = name

        def query(self, q, *a, **k):
            calls.append(("query", self._name, q))
            return types.SimpleNamespace(result_set=[])

        def delete(self):
            calls.append(("delete", self._name))

    class _FakeDb:
        def __init__(self):
            self.graphs = ["test_ws_drop_a", "test_ws_drop_b"]

        def list_graphs(self):
            return list(self.graphs)

        def select_graph(self, name):
            return _FakeGraph(name)

    proj = types.SimpleNamespace(
        _host="localhost", _is_embedded=False,
        graph_name="test_ws_drop_a", db=_FakeDb())
    wipe_server(proj, scope={"test_ws_drop_a", "test_ws_drop_b"}, drop=True)
    delete_calls = [c for c in calls if c[0] == "delete"]
    assert delete_calls == [("delete", "test_ws_drop_a"),
                            ("delete", "test_ws_drop_b")], \
        f"GRAPH.DELETE must ride execute_command (graph.delete()), got {calls}"
    assert not [c for c in calls if c[1] == "GRAPH.DELETE"], \
        "GRAPH.DELETE must never be sent as a query string"


def test_bare_test_graph_wipe_still_raises_on_server(uri_env):
    """E2E-2 control (epic #1647 Task 7 Step 2): the graph guard must
    survive the migration — bulk DETACH on the bare `test` graph raises."""
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test")
    try:
        with pytest.raises(RuntimeError, match="test graph"):
            proj.g.query("MATCH (n) DETACH DELETE n")
    finally:
        proj.close()


# ── _wipe_or wiring: scoping + the shared-projection union ─────────────────


def test_team_registry_isolation_across_sequential_tests(server_proj, monkeypatch, tmp_path):
    """Cycle-2 P0-1a (docker-lane): test_backup_sweep's fixtures must be
    isolated per test. Two sequential seeds on per-iteration registry/team
    graphs, wiped via _wipe_or, must never see each other's nodes.

    DIVERGENCE (documented in the epic changelog): the plan's Step 1 text
    omits the _journal_append calls, but the plan's own O(delta) scope
    (cycle-4 P0-1/P2-10 — per-test wipes touch ONLY the session's
    created-since-last-wipe set + {proj.graph_name}) cannot include raw
    select_graph-minted names unless they are journaled — the plan's own
    cycle-8 P2-2 rule ("raw-client sites append via _journal_append"). The
    appends are the mechanism that makes the isolation assertion reachable.
    The journal file is patched to tmp (URI-unset lanes export no journal
    env; the tests-side appender honors the patched attribute)."""
    monkeypatch.setattr("tests._embedded._JOURNAL_FILE",
                        str(tmp_path / "isolation.graphs.jsonl"))
    import tortoise.backup_sweep as bs
    fake_org_names = iter(["test_org_0_tortoise", "test_org_1_tortoise"])
    monkeypatch.setattr(
        bs, "org_graph_name", lambda registry, org_id: next(fake_org_names))
    for i in range(2):
        reg_name = f"test_registry_{i}"
        org_name = f"test_org_{i}_tortoise"
        _journal_append(reg_name)
        _journal_append(org_name)
        reg = server_proj.db.select_graph(reg_name)
        team = server_proj.db.select_graph(org_name)
        reg.query("CREATE (:Team {id:'team_x', tier:'pro'})")
        team.query("CREATE (:Point {id:'pt-0', content:'c', pointKind:'claim'})")
        # the sweep consumes the SEAM name, never the derived team_team_x
        assert bs.org_graph_name(None, "team_x") == org_name
        assert org_name.startswith(("test_", "tortoise_test")), \
            "P0 guard: _backup_team's graph must stay guard-passing"
        _wipe_or(server_proj)
        if i == 1:
            stale = server_proj.db.select_graph("test_registry_0").query(
                "MATCH (t:Team) RETURN count(t)").result_set[0][0]
            assert stale == 0, "test 0's Team survived into test 1 (pollution)"


def test_per_test_wipe_or_touches_only_session_set(server_proj, monkeypatch, tmp_path):
    """Cycle-4 P0-1 (WIRING) + cycle-5 P2-15 + cycle-8 P1-1: a converted
    `_wipe_or(proj)` with NO scope arg defaults to the session's
    created-since-last-wipe FILE-journal delta + {proj.graph_name} — foreign
    graphs, unrelated test_* graphs, and the shared URI-default graph
    survive. Driven through the REAL file journal (patched _JOURNAL_FILE)."""
    import uuid

    from tests._embedded import _uri_default_graph_name
    monkeypatch.setattr("tests._embedded._JOURNAL_FILE",
                        str(tmp_path / "session.graphs.jsonl"))
    foreign = f"test_foreign_{uuid.uuid4().hex[:8]}"
    foreign_g = server_proj.db.select_graph(foreign)
    foreign_g.query("MATCH (n) DETACH DELETE n")  # clear prior-run leftovers
    foreign_g.query("CREATE (:Point {id:'foreign'})")
    unrelated = []
    for i in range(100):
        g = f"test_unrelated_{i}"
        # clear prior-run leftovers — the survival invariant is about scope,
        # not an absolute count (a re-run on a persistent docker would
        # otherwise accumulate one node per run and red the count assert)
        server_proj.db.select_graph(g).query("MATCH (n) DETACH DELETE n")
        server_proj.db.select_graph(g).query("CREATE (:Point {id:'u'})")
        unrelated.append(g)
    # the session's own graph, journaled ONCE through the real appender
    mine = server_proj.graph_name
    _journal_append(mine)
    _wipe_or(server_proj)  # first wipe — the cursor advances past mine (P2-10)
    server_proj.db.select_graph(mine).query("CREATE (:Point {id:'mine2'})")
    _wipe_or(server_proj)  # second wipe: delta slice is EMPTY — the P1-2
    # union {proj.graph_name} must still wipe the shared graph
    assert server_proj.db.select_graph(mine).query(
        "MATCH (n) RETURN count(n)").result_set[0][0] == 0, \
        "session's own graph must be wiped on EVERY per-test wipe (P1-2 union)"
    # Cycle-8 P1-1: the URI-default graph (job-URI path, e.g.
    # tortoise_test_matrix), journaled by the frame-gated from_uri append,
    # must SURVIVE a per-test wipe — wipe_server's per-test scope filter
    # skips it, so the shared default is never DETACHed mid-session.
    monkeypatch.setenv("TORTOISE_DB_URI",
                       "docker://:falkordb@localhost:6379/tortoise_test_matrix")
    default = _uri_default_graph_name()
    assert default == "tortoise_test_matrix"
    default_g = server_proj.db.select_graph(default)
    # clear any pre-existing leftover nodes (a dev docker may hold the shared
    # default from earlier sessions — the count must be deterministic)
    default_g.query("MATCH (n) DETACH DELETE n")
    default_g.query("CREATE (:Point {id:'default'})")
    _journal_append(default)  # the from_uri append's journal entry, verbatim
    _wipe_or(server_proj)
    assert default_g.query(
        "MATCH (n) RETURN count(n)").result_set[0][0] == 1, \
        "URI-default graph's nodes must SURVIVE a per-test wipe (P1-1)"
    assert foreign_g.query(
        "MATCH (n) RETURN count(n)").result_set[0][0] == 1, \
        "foreign-session graph's nodes must SURVIVE a per-test wipe"
    for g in unrelated:
        assert server_proj.db.select_graph(g).query(
            "MATCH (n) RETURN count(n)").result_set[0][0] == 1, \
            f"unrelated test_* graph {g} must survive (scope ≠ server-global)"
    # journal stays intact for the session-end sweep (no manual reset)
    import tests._embedded as _te
    assert _te._JOURNAL_FILE  # the tmp journal was the one used (patched)


def test_shared_cached_projection_wiped_each_test(server_proj):
    """Cycle-5 P1-2 (the E2E-2 tier shape): session-cached shared
    projections journal their graph once — after the first wipe the delta
    slice is empty, so a scope that excludes proj.graph_name would no-op
    every later wipe. Two sequential "tests": the {proj.graph_name} union
    keeps the shared graph in scope every time."""
    from tests._embedded import _wipe_or
    _wipe_or(server_proj)          # test 1 pre-clean
    server_proj.g.query("CREATE (:Point {id:'t1'})")
    _wipe_or(server_proj)          # test 1's autouse wipe
    # ---- test 2 begins ----
    assert server_proj.g.query(
        "MATCH (n) RETURN count(n)").result_set[0][0] == 0, \
        "test 1's data survived into test 2 — the shared projection was not re-wiped"
    server_proj.g.query("CREATE (:Point {id:'t2'})")
    _wipe_or(server_proj)          # test 2's autouse wipe — must NOT no-op
    assert server_proj.g.query(
        "MATCH (n) RETURN count(n)").result_set[0][0] == 0


def test_per_test_wipe_or_bounded_by_delta(monkeypatch, tmp_path):
    """Cycle-4 P2-10: a per-test wipe touches ONLY the created-since-last-
    wipe slice — never O(server-graphs): 2000 unrelated test_* graphs
    survive a scoped wipe (a server-global blind wipe would touch all)."""
    monkeypatch.setattr("tests._embedded._JOURNAL_FILE",
                        str(tmp_path / "bounded.graphs.jsonl"))
    mine = "test_bounded_mine"
    _journal_append(mine)
    db = _FakeDb()
    db.graphs = [mine] + [f"test_unrelated_{i}" for i in range(2000)]
    proj = types.SimpleNamespace(_host="localhost", _is_embedded=False,
                                 graph_name=mine, db=db)
    _wipe_or(proj)
    assert db.deleted == []  # per-test wipes are DETACH-only (drop=False)
    assert set(db.detached) == {mine}, \
        "scoped wipe must touch ONLY the session's delta + the projection's graph"


def test_sequential_same_path_redirect_mints_are_wiped(monkeypatch, tmp_path):
    """Cycle-7 P1-4 (the delta source): the per-test wipe delta must include
    PRODUCT-side redirect mints. Two sequential "tests" construct the SAME
    fixed path — the Task 1 redirect derives a graph and the PRODUCT writer
    journals it (FILE journal only — the tests-side in-memory _JOURNAL never
    sees it). The delta comes from the FILE journal + the persisted cursor."""
    if not _docker_reachable():
        pytest.skip("live FalkorDB (localhost:6379) not reachable")
    from tests._embedded import _wipe_or
    from tortoise.projection import FalkorProjection
    monkeypatch.setenv("TORTOISE_DB_URI", "docker://:falkordb@localhost:6379")
    monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    monkeypatch.setenv("TORTOISE_TEST_SESSION", "0123456789ab")
    monkeypatch.setenv("TORTOISE_TEST_JOURNAL_FILE",
                       str(tmp_path / "session.graphs.jsonl"))
    monkeypatch.setattr("tests._embedded._JOURNAL_FILE",
                        str(tmp_path / "session.graphs.jsonl"))
    # --- test 1 ---
    p1 = FalkorProjection("/tmp/fixed.db")  # redirect → derived test_* graph (product-journaled)
    try:
        assert p1.graph_name.startswith("test_")
        p1.g.query("CREATE (:Point {id:'t1'})")
        _wipe_or(p1)                          # per-test wipe — delta must include p1's graph
    finally:
        p1.close()
    # --- test 2 (same fixed path) ---
    p2 = FalkorProjection("/tmp/fixed.db")
    try:
        assert p2.graph_name == p1.graph_name, "same path must derive the same graph"
        # DIVERGENCE (documented in the epic changelog): the plan's Step 1
        # entry assert is `count == 0`, but a server construction's
        # _ensure_indexes re-mints its Meta marker node (MERGE, e.g.
        # key:'event_fts_v2') on the graph — so a freshly-constructed graph
        # carries exactly 1 Meta node. The P1-4 pollution marker is test 1's
        # Point t1: its ABSENCE is the invariant; the count is bounded by the
        # construction marker alone (≤ 1).
        assert p2.g.query(
            "MATCH (n:Point {id:'t1'}) RETURN count(n)"
        ).result_set[0][0] == 0, \
            "test 1's data survived into test 2 — the redirect mint was NOT in the wipe delta (P1-4)"
        n = p2.g.query("MATCH (n) RETURN count(n)").result_set[0][0]
        assert n <= 1, \
            f"graph should hold only the construction Meta marker, got {n} nodes"
    finally:
        p2.close()


# ── session journal: tolerant reader + writers ─────────────────────────────


def test_journal_tolerant_reader_truncated_line(tmp_path):
    """Cycle-4 P2-3: a journal whose last line is a truncated half-name
    (simulated torn write — no trailing newline) parses to the complete
    prefix lines only; a mid-file unparseable line truncates (prior honored)."""
    from tests._embedded import _read_journal_file
    j = tmp_path / "j.graphs.jsonl"
    j.write_text("test_ws_a\ntest_ws_b\ntest_ws_torn")  # torn final line
    assert _read_journal_file(str(j)) == ["test_ws_a", "test_ws_b"]
    j.write_text("test_ws_a\ntest_ws_b\n")  # well-formed
    assert _read_journal_file(str(j)) == ["test_ws_a", "test_ws_b"]
    j.write_text("test_ws_a\ntest ws bad\ntest_ws_c\n")  # mid-file poison
    assert _read_journal_file(str(j)) == ["test_ws_a"]
    assert j.exists()


def test_journal_poison_first_line_deleted(tmp_path):
    """Cycle-4 P2-3: a journal whose FIRST line is unparseable is treated as
    EMPTY and deleted (poison-file guard, mirroring the marker hygiene)."""
    from tests._embedded import _read_journal_file
    j = tmp_path / "poison.graphs.jsonl"
    j.write_text("bad name here\n")
    assert _read_journal_file(str(j)) == []
    assert not j.exists()


def test_journal_writer_creates_parent_dir(monkeypatch, tmp_path):
    """Cycle-7 P1-1: the product-side writer appends with the parent dir
    ABSENT (a fresh ACTIVE_SUITES_DIR — no _redislite_hygiene fixture has
    run) and must NOT raise FileNotFoundError; the journal exists after,
    with the graph name line intact."""
    from tortoise.projection import _journal_append_product
    journal = tmp_path / "a" / "b" / "session.graphs.jsonl"
    monkeypatch.setenv("TORTOISE_TEST_JOURNAL_FILE", str(journal))
    _journal_append_product("test_ws_parent_dir")
    assert journal.exists()
    assert journal.read_text() == "test_ws_parent_dir\n"


def test_journal_append_failure_raises_instead_of_silently_nopping(monkeypatch,
                                                                  tmp_path):
    """#3214: the tests-side appender swallowed the OSError at DEBUG, leaving
    the minted graph UNOWNED — invisible to every live peer's scope=None
    sweep, which has no record of it and may therefore delete it (the exact
    cross-session flake #3074 exists to stop). An unjournaled graph must
    surface as a problem, so the appender now raises at the mint site.

    The failure is forced portably: the journal's PARENT path is a regular
    file, so ``os.makedirs(..., exist_ok=True)`` raises FileExistsError.
    """
    from tests._embedded import _journal_append
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory\n")
    monkeypatch.setattr("tests._embedded._JOURNAL_FILE",
                        str(blocker / "session.graphs.jsonl"))
    with pytest.raises(RuntimeError, match="UNOWNED") as ei:
        _journal_append("test_unowned_graph")
    assert "test_unowned_graph" in str(ei.value), (
        "the failure must name the graph it could not record: "
        f"{ei.value}")


def test_journal_append_no_path_configured_still_noops(monkeypatch):
    """#3214 do-not-over-fix: with NO journal configured the appender stays a
    silent no-op. There is no ownership file to fail to write, and test
    modules are imported outside sessions — failing there would be noise, not
    protection."""
    from tests._embedded import _journal_append
    monkeypatch.setattr("tests._embedded._JOURNAL_FILE", "")
    monkeypatch.delenv("TORTOISE_TEST_JOURNAL_FILE", raising=False)
    _journal_append("test_no_journal_configured")  # must not raise


def test_journal_append_product_failure_raises(monkeypatch, tmp_path):
    """#3214: the PRODUCT-side writer shares the contract — its silent no-op
    left product mint sites (``team_*``/``registry_*``) unowned in exactly the
    same way, so it raises too. The no-op gates (no path / not a test session)
    are unchanged, so production mints are unaffected."""
    import tortoise.projection as proj_mod
    blocker = tmp_path / "blocker2"
    blocker.write_text("not a directory\n")
    monkeypatch.setattr(proj_mod, "_journal_file_path",
                        lambda: str(blocker / "session.graphs.jsonl"))
    monkeypatch.setattr(proj_mod, "_TEST_SESSION_ACTIVE", True)
    with pytest.raises(RuntimeError, match="UNOWNED") as ei:
        proj_mod._journal_append_product("team_unowned")
    assert "team_unowned" in str(ei.value), ei.value


def test_journal_append_product_outside_a_test_session_still_noops(
        monkeypatch, tmp_path):
    """#3214 do-not-over-fix: without a configured journal path the product
    writer is inert (production mints are unjournaled BY DESIGN) — the new
    failure policy must not reach production code paths."""
    import tortoise.projection as proj_mod
    monkeypatch.setattr(proj_mod, "_TEST_SESSION_ACTIVE", True)
    monkeypatch.setattr(proj_mod, "_journal_file_path", lambda: None)
    proj_mod._journal_append_product("team_prod_untouched")  # must not raise


def test_from_uri_append_gated_on_test_frame(monkeypatch, tmp_path):
    """Cycle-7 P2-9: a subprocess -c probe with TORTOISE_TEST_MODE=1 + a URI
    but NO test module in the stack calls FalkorProjection.from_uri(...) →
    the journal file is UNCHANGED (no frame → no append; the child is not a
    concurrent writer); an in-process from_uri call from a test module DOES
    append."""
    from tortoise.projection import FalkorProjection
    journal = tmp_path / "child.graphs.jsonl"
    env = {**os.environ,
           "TORTOISE_TEST_JOURNAL_FILE": str(journal),
           "TORTOISE_TEST_MODE": "1",
           "TORTOISE_DB_URI": "docker://:falkordb@localhost:6379"}
    if not _docker_reachable():
        pytest.skip("live FalkorDB (localhost:6379) not reachable")
    out = subprocess.run(
        [sys.executable, "-c",
         "from tortoise.projection import FalkorProjection; "
         "FalkorProjection.from_uri('docker://:falkordb@localhost:6379', "
         "graph_name='test_ws_child')"],
        capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert not journal.exists() or journal.read_text() == "", \
        "no test frame in the stack → the child must NOT append to the journal"
    # in-process from a TEST frame → appends (construction stubbed — the
    # append fires BEFORE the host branch connects)
    monkeypatch.setenv("TORTOISE_TEST_JOURNAL_FILE", str(journal))
    monkeypatch.setattr(FalkorProjection, "__init__",
                        lambda self, *a, **k: None)
    FalkorProjection.from_uri("docker://:falkordb@localhost:6379",
                              graph_name="test_ws_inproc")
    assert journal.read_text() == "test_ws_inproc\n"


# ── sweeps: drop set, failure policies, liveness, deferral ─────────────────


def test_session_end_sweep_drops_file_journal_set(uri_env, monkeypatch, tmp_path):
    """Cycle-8 P1-2: the session-end sweep's drop set is the FILE journal —
    a redirect-minted name written to the file only (never the in-memory
    list) AND a tests-side name are BOTH dropped; post-sweep GRAPH.LIST
    reflects exactly the file set."""
    from tests._embedded import _session_end_own_sweep
    from tortoise.projection import FalkorProjection, _journal_append_product
    journal = tmp_path / "session.graphs.jsonl"
    monkeypatch.setattr("tests._embedded._JOURNAL_FILE", str(journal))
    monkeypatch.setenv("TORTOISE_TEST_JOURNAL_FILE", str(journal))
    redirect_name = "test_ws_redirect_mint"
    _journal_append_product(redirect_name)  # product-side, FILE only
    _journal_append("test_ws_tests_side")   # tests-side (in-memory + file)
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_probe")
    try:
        for g in (redirect_name, "test_ws_tests_side", "test_ws_probe"):
            proj.db.select_graph(g).query("CREATE (:Point {id:'x'})")
        res = _session_end_own_sweep(
            os.environ["TORTOISE_DB_URI"], str(journal))
        assert res["journal_removed"] is True
        assert not journal.exists()
        remaining = proj.db.list_graphs() or []
        assert redirect_name not in remaining
        assert "test_ws_tests_side" not in remaining
    finally:
        proj.close()


def test_sweep_delete_error_logs_and_continues(tmp_path):
    """Cycle-8 P2-3: a sweep whose drop hits a genuine delete error LOGS the
    graph + error and ends GREEN — the journal file SURVIVES for the next
    session's stale sweep; explicit wipe_server() with the same failure
    still RAISES (D-4/P2-7 intact — pinned elsewhere)."""
    journal = tmp_path / "session.graphs.jsonl"
    journal.write_text("test_ws_ok\ntest_ws_bad\n")
    db = _FakeDb(fail_delete={"test_ws_bad"})
    res = _sweep_drop(_FakeProj(db), str(journal), drop=True)
    assert res["failed"] == ["test_ws_bad"]
    assert res["dropped"] == ["test_ws_ok"]
    assert res["journal_removed"] is False
    assert journal.exists(), "keep-journal-on-partial (self-healing retry)"


def test_sweep_partial_delete_failure_keeps_journal(tmp_path):
    """Cycle-8 P2-4: a sweep whose SECOND delete raises drops the first
    graph successfully and then fails — the journal file is NOT deleted; a
    subsequent clean sweep drops the remainder and ONLY THEN removes it."""
    journal = tmp_path / "session.graphs.jsonl"
    journal.write_text("test_ws_first\ntest_ws_second\n")
    db = _FakeDb(fail_delete={"test_ws_second"})
    res1 = _sweep_drop(_FakeProj(db), str(journal), drop=True)
    assert res1["dropped"] == ["test_ws_first"]
    assert res1["journal_removed"] is False
    assert journal.exists()
    # second sweep (the next session's stale sweep): both succeed → removed
    db2 = _FakeDb()
    res2 = _sweep_drop(_FakeProj(db2), str(journal), drop=True)
    assert res2["journal_removed"] is True
    assert not journal.exists()


def test_sweep_preserves_non_owned_graphs(monkeypatch, tmp_path):
    """#7795 fail-closed: the sweep may only DETACH+DELETE the name families
    it owns (``test_``/``tortoise_test``/``team_``/``org_``). A shared graph
    name that reached the journal because a test drove PRODUCT code with a
    shared path (e.g. ``doctor --db docker://…/tortoise`` — the doctor CLI
    runs in-process, so its ``from_uri`` journals from the test frame) must
    be PRESERVED: a test run may never wipe the dev/compose graph.

    ``TORTOISE_DB_URI`` is CLEARED: with it naming a pathless graph (e.g.
    ``…/tortoise``), ``_uri_default_graph_name()`` returns that name and the
    URI-default ``continue`` fires BEFORE this gate — ``tortoise`` would
    then never reach ``preserved`` and this pin would false-fail on an
    ambient URI (review P1). The sibling
    ``test_sweep_skips_uri_default_graph`` sets the URI explicitly."""
    from tests._embedded import _uri_default_graph_name
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    assert _uri_default_graph_name() is None, \
        "this pin must not depend on an ambient TORTOISE_DB_URI"
    journal = tmp_path / "session.graphs.jsonl"
    journal.write_text("test_ws_ours\nteam_acme\ntortoise\nx\n")
    db = _FakeDb()
    res = _sweep_drop(_FakeProj(db), str(journal), drop=True)
    assert res["dropped"] == ["test_ws_ours", "team_acme"]
    assert res["preserved"] == ["tortoise", "x"]
    assert res["failed"] == []
    # Retrying a non-owned name cannot help — the journal is still consumed.
    assert res["journal_removed"] is True
    assert db.deleted == ["test_ws_ours", "team_acme"]
    assert "tortoise" not in db.detached and "x" not in db.detached


def test_sweep_preserved_warning_reports_journal_kept(
        monkeypatch, tmp_path, caplog):
    """#7795 review P2-1: the PRESERVED warning is the operator's ONLY record
    of a non-owned name, and it must not claim the journal was consumed when
    an OWNED drop failure KEPT it — that sends the operator away from the
    file that still holds the drop-set bookkeeping. Mixed case: one
    non-owned name (preserved) + one owned name whose drop raises."""
    import logging

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    journal = tmp_path / "session.graphs.jsonl"
    journal.write_text("test_ws_bad\ntortoise\n")
    db = _FakeDb(fail_delete={"test_ws_bad"})
    with caplog.at_level(logging.WARNING):
        res = _sweep_drop(_FakeProj(db), str(journal), drop=True)
    assert res["preserved"] == ["tortoise"]
    assert res["failed"] == ["test_ws_bad"]
    assert res["journal_removed"] is False
    assert journal.exists(), "an owned drop failure KEEPS the journal"
    warned = [r.getMessage() for r in caplog.records
              if "PRESERVED" in r.getMessage()]
    assert warned, "a preserved name must be surfaced to the operator"
    assert "KEPT" in warned[0], \
        f"the KEPT branch must be stated, not the consumed branch: {warned[0]!r}"
    assert "retrying cannot reclaim them" not in warned[0], \
        "false in the mixed case — the journal was kept"


def test_sweep_preserved_warning_reports_journal_consumed(
        monkeypatch, tmp_path, caplog):
    """#7795 review P2-1 (clean branch): with no owned drop failure the
    journal IS removed and the names become unreclaimable, so the warning
    must say so — the counterpart pin to the mixed case above."""
    import logging

    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    journal = tmp_path / "session.graphs.jsonl"
    journal.write_text("tortoise\n")
    db = _FakeDb()
    with caplog.at_level(logging.WARNING):
        res = _sweep_drop(_FakeProj(db), str(journal), drop=True)
    assert res["preserved"] == ["tortoise"]
    assert res["failed"] == []
    assert res["journal_removed"] is True
    assert not journal.exists()
    warned = [r.getMessage() for r in caplog.records
              if "PRESERVED" in r.getMessage()]
    assert warned, "a preserved name must be surfaced to the operator"
    assert "retrying cannot reclaim them" in warned[0]
    assert "KEPT" not in warned[0], \
        "the journal WAS removed — the message must not say it was kept"


def test_sweep_owns_org_namespace_by_name(monkeypatch, tmp_path):
    """#7795 review P1: ``org_`` — the CURRENT product-namespace spelling
    (#3543 tenancy rename) — must be in the sweep's owned set BY THAT NAME,
    and a journaled ``org_*`` graph must be DROPPED, not preserved.

    The journal IS the ownership record for a product-side mint
    (``_journal_append_product`` at the hosted ``org_create`` sites:
    ``hosted_api.provision_tenant``, ``hosted_api.register_user`` (both the
    Supabase and registry lanes), and
    ``hosted_api._eager_provision_org_graph``), so a journaled
    ``org_*`` graph is demonstrably ours — each of those sites journals only
    a graph the call itself minted. The earlier pins exercised
    ``team_`` only — the PRE-rename spelling — which is exactly why the
    missing ``org_`` slipped through: an ``org_`` name took the
    ``preserved`` branch while the empty ``failed`` list still removed the
    journal, destroying the only record that could reclaim it."""
    from tests._embedded import _SWEEP_OWNED_PREFIXES
    monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
    assert "org_" in _SWEEP_OWNED_PREFIXES, \
        "the product-namespace spelling org_ (#3543) must be owned by name"
    journal = tmp_path / "session.graphs.jsonl"
    journal.write_text(
        "test_something_ours\n"
        "org_journalled\n"
        "org_acme\n"
        "team_ws_journal_drop\n"
    )
    db = _FakeDb()
    res = _sweep_drop(_FakeProj(db), str(journal), drop=True)
    assert res["dropped"] == [
        "test_something_ours", "org_journalled", "org_acme",
        "team_ws_journal_drop"]
    assert res["preserved"] == [], \
        "journaled org_* graphs are OWNED — they must never be preserved"
    assert res["failed"] == []
    assert res["journal_removed"] is True
    assert db.deleted == res["dropped"]


def test_sweep_skips_uri_default_graph(monkeypatch, tmp_path):
    """Cycle-4 P2-2 / cycle-8 P1-1 (review P1-2): the shared URI-default
    graph is swept ONLY by the last-suite-standing full sweep — a session's
    OWN sweep must not drop it (that would race concurrent sessions' live
    writes on the shared default). The journal is still removed (no
    failures), the default graph's nodes survive."""
    from tests._embedded import _sweep_drop, _uri_default_graph_name
    monkeypatch.setenv("TORTOISE_DB_URI",
                       "docker://:falkordb@localhost:6379/tortoise_test_matrix")
    default = _uri_default_graph_name()
    assert default == "tortoise_test_matrix"
    journal = tmp_path / "session.graphs.jsonl"
    journal.write_text(f"{default}\ntest_ws_own_graph\n")
    db = _FakeDb()
    res = _sweep_drop(_FakeProj(db), str(journal), drop=True)
    assert res["dropped"] == ["test_ws_own_graph"]
    assert default not in db.deleted, \
        "the shared URI-default graph must survive the session's OWN sweep"
    assert res["journal_removed"] is True
    assert not journal.exists()


def test_is_missing_graph_error_matches_real_server_text():
    """Review P2-1: GRAPH.DELETE on a missing graph raises the real server
    text "Invalid graph operation on empty key" (v4.16.7) — a concurrent
    suite may have dropped the graph first; the error must read as
    idempotent success (cycle-5 P2-3), not a failure."""
    from tests._embedded import _is_missing_graph_error
    assert _is_missing_graph_error(
        RuntimeError("Invalid graph operation on empty key")) is True
    assert _is_missing_graph_error(
        RuntimeError("GRAPH.DELETE failed: no such graph")) is True
    assert _is_missing_graph_error(
        RuntimeError("connection refused")) is False


def test_sweep_dedupes_journal_entries(tmp_path):
    """Review P2-2: duplicate journal entries (the per-test backup seam
    re-appends the same module-level names every test) drop once — the
    sweep is idempotent and the journal is still removed."""
    from tests._embedded import _sweep_drop
    journal = tmp_path / "dup.graphs.jsonl"
    journal.write_text("test_ws_dup_a\ntest_ws_dup_a\ntest_ws_dup_b\n")
    db = _FakeDb()
    res = _sweep_drop(_FakeProj(db), str(journal), drop=True)
    assert res["dropped"] == ["test_ws_dup_a", "test_ws_dup_b"]
    assert db.deleted == ["test_ws_dup_a", "test_ws_dup_b"]
    assert res["journal_removed"] is True
    assert not journal.exists()


def test_stale_sweep_recycled_pid_marker_journal_dead(monkeypatch, tmp_path):
    """Cycle-7 P2-8: a marker with the right name but a START-MISMATCH
    (simulated recycled pid — marker start= differs from the live process's
    start) fails active_suite_markers() liveness → the journal is classified
    DEAD and its graphs are dropped (bare marker-file EXISTENCE is NOT the
    liveness rule)."""
    import tortoise.embedded_reaper as er
    from tests._embedded import _stale_sweep
    adir = tmp_path / "suites"
    adir.mkdir()
    monkeypatch.setattr(er, "ACTIVE_SUITES_DIR", str(adir))
    nonce = "deadbeefdead"
    (adir / f"{os.getpid()}-{nonce}").write_text(
        f"pid={os.getpid()}\nstart=1.0\n")  # start mismatch → recycled
    j = adir / f"{nonce}.graphs.jsonl"
    j.write_text("test_ws_recycled_graph\n")
    db = _FakeDb()
    monkeypatch.setattr("tests._embedded._proj_for_uri",
                        lambda uri: _FakeProj(db))
    assert er.active_suite_markers() == []  # the recycled marker is NOT live
    _stale_sweep("docker://:x@localhost:6379")
    assert "test_ws_recycled_graph" in db.deleted
    assert not j.exists()


def test_concurrent_suite_end_sweep_leaves_other_suite_graphs(monkeypatch, tmp_path):
    """Cycle-3 P2-13 / cycle-5 P2-4: suite A's end-sweep (journal A) must
    leave suite B's live graphs (marker B present) untouched; with B's
    marker removed (B crashed), A's stale sweep drops B's journaled graphs."""
    import tortoise.embedded_reaper as er
    from tests._embedded import _stale_sweep, _sweep_drop
    adir = tmp_path / "suites"
    adir.mkdir()
    monkeypatch.setattr(er, "ACTIVE_SUITES_DIR", str(adir))
    j_a = adir / "nonce_a.graphs.jsonl"
    j_b = adir / "nonce_b.graphs.jsonl"
    j_a.write_text("test_ws_a_graph\n")
    j_b.write_text("test_ws_b_graph\n")
    db = _FakeDb()
    monkeypatch.setattr("tests._embedded._proj_for_uri",
                        lambda uri: _FakeProj(db))
    # A's END sweep drops ONLY journal A's set — B's graph untouched
    res = _sweep_drop(_FakeProj(db), str(j_a))
    assert res["dropped"] == ["test_ws_a_graph"]
    assert "test_ws_b_graph" not in db.deleted
    assert j_b.exists()
    # B's marker present + live → B's journal classified LIVE → stale sweep skips
    monkeypatch.setattr(er, "active_suite_markers",
                        lambda: [{"token": "54321-nonce_b",
                                  "pid": 54321, "start": None}])
    _stale_sweep("docker://:x@localhost:6379")
    assert "test_ws_b_graph" not in db.deleted
    assert j_b.exists()
    # B's marker removed (B crashed) → B's journal DEAD → stale sweep drops it
    monkeypatch.setattr(er, "active_suite_markers", lambda: [])
    _stale_sweep("docker://:x@localhost:6379")
    assert "test_ws_b_graph" in db.deleted
    assert not j_b.exists()


def test_one_session_two_markers_counts_as_one_suite(monkeypatch, tmp_path):
    """Cycle-5 P2-4 / cycle-6 P2-16: a docker session holds BOTH its
    embedded-format marker ({pid}-{uuid8}) AND its docker-format marker
    ({pid}-{nonce12}) — same pid, different tokens. The deferral predicate
    is PID-grouped: same pid → ONE suite (the own-journal sweep never defers
    to the session's OWN second marker). The token-based predicate (the
    pre-fix form) counts the second marker → the P2-4 double-count hazard."""
    import tortoise.embedded_reaper as er
    adir = tmp_path / "suites"
    adir.mkdir()
    monkeypatch.setattr(er, "ACTIVE_SUITES_DIR", str(adir))
    from tortoise.embedded_reaper import _process_start_time
    start = _process_start_time(os.getpid())
    for tok in (f"{os.getpid()}-aaaa1111", f"{os.getpid()}-0123456789ab"):
        (adir / tok).write_text(f"pid={os.getpid()}\nstart={start}\n")
    markers = er.active_suite_markers()
    assert len(markers) == 2  # both parse as LIVE (pid + start verified)
    own_token = f"{os.getpid()}-aaaa1111"
    others_pid = [m for m in markers if m.get("pid") != os.getpid()]
    assert others_pid == [], "PID-grouped: same pid → ONE suite"
    others_token = [m for m in markers if m.get("token") != own_token]
    assert len(others_token) == 1, \
        "token-based grouping double-counts the docker marker (P2-4 hazard)"


def test_allow_remote_session_teardown_green(tmp_path):
    """Cycle-4 P1-8: an ALLOW_REMOTE session's sweep SKIPS (log-and-continue)
    on a non-loopback host — teardown ends GREEN; graphs + journal preserved
    for the next session (D-4's RuntimeError is for explicit wipe_server())."""
    journal = tmp_path / "session.graphs.jsonl"
    journal.write_text("test_ws_remote_graph\n")
    proj = types.SimpleNamespace(_host="db.internal.example.com",
                                 db=_FakeDb())
    res = _sweep_drop(proj, str(journal), drop=True,
                      skip_on_non_loopback=True)
    assert "skipped" in res
    assert res["journal_removed"] is False
    assert journal.exists()


def test_session_end_sweep_drops_journaled_team_graph(uri_env, monkeypatch, tmp_path):
    """#1686: product-namespace graphs (hosted parity — NEVER test-prefixed)
    reach the sweep ONLY via the journal: _sweep_drop drops every journaled
    name in its owned set (``test_``/``tortoise_test``/``team_``/``org_``),
    skipping only the URI-default, so a journaled team_<name> is deleted at
    session end (this is the mechanism that stops product-namespace
    accumulation on the docker)."""
    from tests._embedded import _session_end_own_sweep
    from tortoise.projection import FalkorProjection, _journal_append_product

    journal = tmp_path / "session.graphs.jsonl"
    monkeypatch.setattr("tests._embedded._JOURNAL_FILE", str(journal))
    monkeypatch.setenv("TORTOISE_TEST_JOURNAL_FILE", str(journal))
    org_name = "team_ws_journal_drop"
    _journal_append_product(org_name)  # the #1686 mint-site seam
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_team_probe")
    try:
        proj.db.select_graph(org_name).query("CREATE (:TeamMeta {name:'x'})")
        res = _session_end_own_sweep(os.environ["TORTOISE_DB_URI"], str(journal))
        assert res["journal_removed"] is True
        assert not journal.exists()
        remaining = proj.db.list_graphs() or []
        assert org_name not in remaining
    finally:
        proj.close()


def test_leftover_team_strays_dropped_when_opted_in(uri_env, monkeypatch):
    """#1686 closure (review P1-1 fix): the journal-blind product-namespace
    residual class is closed by _sweep_team_strays, but ONLY when allowed —
    an explicit TORTOISE_TEST_SWEEP_TEAM_STRAYS=1 opt-in (never inferred
    from the URI path since #1884; a pathless shared/dev docker never
    triggers it). The helper is exercised DIRECTLY (no mid-suite global
    wipe — the last-suite-standing gate is conftest's, not this helper's;
    review P1-2).

    Both mint-namespace generations are reclaimed: `org_` (current, #3543)
    and `team_` (graphs minted before the rename). A sweep that matches
    only the legacy prefix is a silent no-op and lets strays re-accumulate
    — the #2850-class DB-full disease."""
    from tests._embedded import _sweep_team_strays
    from tortoise.projection import FalkorProjection

    monkeypatch.setenv("TORTOISE_TEST_SWEEP_TEAM_STRAYS", "1")
    stray = "org_ws_stray_8f3a"
    legacy_stray = "team_ws_stray_8f3a"
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_leftover_probe")
    try:
        for name in (stray, legacy_stray):
            proj.db.select_graph(name).query("CREATE (:TeamMeta {name:'stray'})")
        dropped = _sweep_team_strays(proj, os.environ["TORTOISE_DB_URI"])
        assert stray in dropped, f"expected {stray} dropped, got {dropped!r}"
        assert legacy_stray in dropped, \
            f"expected pre-rename {legacy_stray} reclaimed too, got {dropped!r}"
        remaining = proj.db.list_graphs() or []
        assert stray not in remaining
        assert legacy_stray not in remaining
    finally:
        proj.close()


def test_leftover_team_strays_refused_on_shared_docker(uri_env, monkeypatch):
    """#1686 default-fail-safe (review P1-1): a pathless shared/dev URI does
    NOT trigger the product-namespace pass without the explicit opt-in —
    the current mint namespace (`org_<name>`, #3543) holds REAL tenant
    graphs and must survive on a shared docker. The stray is cleaned up
    directly by the test itself."""
    from tests._embedded import _sweep_team_strays
    from tortoise.projection import FalkorProjection

    monkeypatch.delenv("TORTOISE_TEST_SWEEP_TEAM_STRAYS", raising=False)
    stray = "org_ws_stray_keep"
    legacy_stray = "team_ws_stray_keep"
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_leftover_probe")
    try:
        for name in (stray, legacy_stray):
            proj.db.select_graph(name).query("CREATE (:TeamMeta {name:'keep'})")
        dropped = _sweep_team_strays(proj, "docker://:falkordb@localhost:6379")
        assert dropped == [], f"shared-docker sweep must refuse, got {dropped!r}"
        remaining = proj.db.list_graphs() or []
        assert stray in remaining, "product-named graph must survive"
        assert legacy_stray in remaining, "pre-rename product graph must survive"
    finally:
        for name in (stray, legacy_stray):
            proj.db.select_graph(name).query("MATCH (n) DETACH DELETE n")
            proj.db.select_graph(name).delete()
        proj.close()


def test_leftover_team_strays_refused_on_test_matrix_uri(uri_env, monkeypatch):
    """#1884 regression: the LONGMEM_EVAL URI (docker://.../tortoise_test_
    matrix — the shared dev container's test-named graph) does NOT trigger
    the journal-blind product-namespace pass without the explicit opt-in.
    The re-validation ran per-question graphs (named team_default__default__
    {qid} then; minted org_* today) against this exact URI; a concurrent
    docker-lane pytest session ending last-suite-standing inferred
    "dedicated test DB" from the "test" path substring and DETACH-DELETEd +
    GRAPH.DELETEd the eval's LIVE graphs mid-ingest (silent write loss:
    pool_size 8 vs 374 ingested points). The opt-in-only gate makes the
    eval's graphs survive any concurrent test session's sweep on the shared
    container."""
    from tests._embedded import _sweep_team_strays, _team_sweep_allowed
    from tortoise.projection import FalkorProjection

    eval_uri = "docker://:falkordb@localhost:6379/tortoise_test_matrix"
    assert "test" in eval_uri.split("/")[-1], \
        "fixture URI must carry the test-named path (the eval's shared container)"
    monkeypatch.delenv("TORTOISE_TEST_SWEEP_TEAM_STRAYS", raising=False)
    # the retracted inference: the URI path says "test" but the gate refuses
    assert _team_sweep_allowed(eval_uri) is False, \
        "URI-path 'test' inference must be retracted (#1884)"
    stray = f"org_ws_eval_stray_{uuid.uuid4().hex[:8]}"
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_evalsweep_probe")
    try:
        proj.db.select_graph(stray).query("CREATE (:TeamMeta {name:'eval'})")
        dropped = _sweep_team_strays(proj, eval_uri)
        assert dropped == [], \
            f"eval-URI sweep must refuse without opt-in, got {dropped!r}"
        remaining = proj.db.list_graphs() or []
        assert stray in remaining, \
            "eval question graphs (product-namespace) must survive a " \
            "concurrent session's sweep"
    finally:
        proj.db.select_graph(stray).query("MATCH (n) DETACH DELETE n")
        proj.db.select_graph(stray).delete()
        proj.close()


# ── #3634 Task 3: the opt-in, journal-blind LEGACY RESIDUE sweep ───────────


@pytest.mark.parametrize("value,expected", [
    (None, False), ("", False), ("0", False), ("true", False),
    ("yes", False), ("1 ", False), ("01", False), ("1", True),
])
def test_legacy_sweep_gate_is_narrow_by_design(monkeypatch, value, expected):
    """The gate requires the EXACT string "1" — every other spelling refuses.

    `_legacy_sweep_allowed` is the sole authorization for an irreversible
    journal-blind DETACH DELETE + GRAPH.DELETE, so it must not ride the
    truthy-set contract (#4097) that `is_truthy` declares. This parametrized
    matrix is the executable statement of that narrowing.
    """
    from tests._embedded import _legacy_sweep_allowed

    if value is None:
        monkeypatch.delenv("TORTOISE_TEST_SWEEP_LEGACY", raising=False)
    else:
        monkeypatch.setenv("TORTOISE_TEST_SWEEP_LEGACY", value)
    assert _legacy_sweep_allowed() is expected


# The fixture is DERIVED from `_LEGACY_RESIDUE_PREFIXES` — each name is either
# an exact residue prefix or a name that must be refused for a stated reason.
# NOTE (Task 1 narrowing): "askshape_b6" was in the plan text but is NOT a
# residue prefix (Task 1 narrowed it to the census name
# `askshape_b6_live_1_33760_21`), so the fixture uses the full census name.
_RESIDUE_FIXTURE = [
    "test_a", "tortoise_test_b", "registry_test_c_control_plane",
    "v10fix_c1", "ttm_a1", "review_rw_probe", "askshape_b6_live_1_33760_21",
    "legbudget_25979_txrx",
    "registry_tortoise", "registry_control_plane", "tortoise_test_matrix",
    "tortoise_restored_20260101", "org_x", "team_y", "totally_unrelated", "t",
]


def test_legacy_sweep_off_deletes_nothing(monkeypatch):
    """AC3: with the opt-in unset, the sweep is a pure no-op on GRAPH.LIST."""
    from tests._embedded import _sweep_legacy_strays

    monkeypatch.delenv("TORTOISE_TEST_SWEEP_LEGACY", raising=False)
    db = _FakeDb()
    db.graphs = list(_RESIDUE_FIXTURE)
    _sweep_legacy_strays(_FakeProj(db), default_graph="tortoise_test_matrix")
    assert db.detached == []
    assert db.deleted == []


def test_legacy_sweep_on_reclaims_only_the_residue(monkeypatch):
    """AC2/AC3: opted in, the sweep reclaims EXACTLY the declared residue.

    Every shared/preserved name in the fixture is asserted to survive: the
    shared registries, the URI default, a `tortoise_restored_*` snapshot, the
    owned families, and an unrelated name. The residue set is derivable from
    `_LEGACY_RESIDUE_PREFIXES` (see the fixture comment).
    """
    from tests._embedded import _sweep_legacy_strays

    monkeypatch.setenv("TORTOISE_TEST_SWEEP_LEGACY", "1")
    db = _FakeDb()
    db.graphs = list(_RESIDUE_FIXTURE)
    _sweep_legacy_strays(_FakeProj(db), default_graph="tortoise_test_matrix")
    assert set(db.detached) == {
        "registry_test_c_control_plane", "v10fix_c1", "ttm_a1",
        "review_rw_probe", "askshape_b6_live_1_33760_21",
        "legbudget_25979_txrx",
    }
    assert db.deleted == db.detached, \
        "every detached residue graph must also be GRAPH.DELETEd"
    for protected in ("registry_tortoise", "registry_control_plane",
                      "tortoise_test_matrix", "tortoise_restored_20260101",
                      "org_x", "team_y", "test_a", "tortoise_test_b",
                      "totally_unrelated", "t"):
        assert protected not in db.detached, protected
        assert protected not in db.deleted, protected


def test_legacy_sweep_protects_the_default_graph_itself(monkeypatch):
    """The `default_graph` pass-through is load-bearing, not incidental.

    `registry_test_shared` is residue AND unowned, so the shape alone reclaims
    it UNLESS it IS the URI default graph. The two halves below isolate the
    `default_graph` branch: the same name survives when it is the default and
    is residue when the default is something else. A mutant that hardcodes
    `default_graph=None` in the sweep fails the first half.
    """
    from tests._embedded import _sweep_legacy_strays

    monkeypatch.setenv("TORTOISE_TEST_SWEEP_LEGACY", "1")
    db = _FakeDb()
    db.graphs = ["registry_test_shared"]
    _sweep_legacy_strays(_FakeProj(db), default_graph="registry_test_shared")
    assert db.detached == []
    assert db.deleted == []

    # the same name with a DIFFERENT default IS residue
    db2 = _FakeDb()
    db2.graphs = ["registry_test_shared"]
    _sweep_legacy_strays(_FakeProj(db2), default_graph="tortoise_test_matrix")
    assert db2.detached == ["registry_test_shared"]


def test_legacy_sweep_refuses_non_loopback_host(monkeypatch):
    """#3634: the residue pass is loopback-only — a remote host refuses.

    `_sweep_drop` receives `skip_on_non_loopback` from its callers and
    `_sweep_team_strays` inherits `_leftover_sweep`'s guard; this pass has NO
    caller, so it must carry the guard itself. Modeled on
    `test_allow_remote_session_teardown_green`: the sweep SKIPS (returns ``[]``)
    and every candidate graph — even ones the residue shape would reclaim — is
    left untouched, opt-in or not.
    """
    from tests._embedded import _sweep_legacy_strays

    monkeypatch.setenv("TORTOISE_TEST_SWEEP_LEGACY", "1")  # even OPTED IN
    db = _FakeDb()
    db.graphs = ["v10fix_c1", "ttm_a1"]  # both reclaimed on loopback
    proj = types.SimpleNamespace(_host="db.internal.example.com", db=db)
    dropped = _sweep_legacy_strays(proj, default_graph="tortoise_test_matrix")
    assert dropped == []
    assert db.detached == []
    assert db.deleted == []


def test_legacy_sweep_reclaims_on_live_server(uri_env, monkeypatch):
    """#3634 Task 3 Step 7: the residue pass reclaims on a REAL server, and a
    shared-registry-shaped name survives.

    Run-unique names, per this file's own convention (see
    `test_wipe_server_clears_only_test_prefixed`): the fixed literals
    `registry_tortoise`/`registry_control_plane` are SHARED graphs — seeding
    and GRAPH.DELETE on them would damage peer sessions' / the dev docker's
    data. The property under test is name-SHAPE based, so a unique
    `registry_ws_<uuid>_control_plane` (same `registry_*` family, no residue
    prefix) proves it identically and leaves the shared names untouched.
    """
    from tests._embedded import _sweep_legacy_strays, is_legacy_residue
    from tortoise.projection import FalkorProjection

    monkeypatch.setenv(
        "TORTOISE_DB_URI",
        "docker://:falkordb@localhost:6379/tortoise_test_matrix")
    monkeypatch.setenv("TORTOISE_TEST_SWEEP_LEGACY", "1")
    residue = f"registry_test_{uuid.uuid4().hex}_control_plane"
    shared = f"registry_ws_{uuid.uuid4().hex}_control_plane"
    assert is_legacy_residue(residue, default_graph="tortoise_test_matrix")
    assert not is_legacy_residue(shared, default_graph="tortoise_test_matrix")
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_legacy_probe")
    try:
        for name in (residue, shared):
            proj.db.select_graph(name).query("CREATE (:Registry {id:'probe'})")
        before = proj.db.list_graphs() or []
        assert residue in before and shared in before
        dropped = _sweep_legacy_strays(
            proj, default_graph="tortoise_test_matrix")
        assert residue in dropped, f"expected {residue} reclaimed, got {dropped!r}"
        assert shared not in dropped, \
            f"the shared-registry shape must survive: {dropped!r}"
        after = proj.db.list_graphs() or []
        assert residue not in after, "the residue graph must be GRAPH.DELETEd"
        assert shared in after, "the shared-registry-shaped name must survive"
    finally:
        # the survivor is deliberately never swept (fail-closed by shape); the
        # residue one is already gone. Delete both so a dev docker does not
        # accumulate one graph per run.
        for name in (residue, shared):
            try:  # noqa: SIM105
                proj.db.select_graph(name).delete()
            except Exception:
                pass
        proj.close()
