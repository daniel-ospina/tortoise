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

    def list_graphs(self):
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
    fake_team_names = iter(["test_team_0_tortoise", "test_team_1_tortoise"])
    monkeypatch.setattr(
        bs, "team_graph_name", lambda registry, team_id: next(fake_team_names))
    for i in range(2):
        reg_name = f"test_registry_{i}"
        team_name = f"test_team_{i}_tortoise"
        _journal_append(reg_name)
        _journal_append(team_name)
        reg = server_proj.db.select_graph(reg_name)
        team = server_proj.db.select_graph(team_name)
        reg.query("CREATE (:Team {id:'team_x', tier:'pro'})")
        team.query("CREATE (:Point {id:'pt-0', content:'c', pointKind:'claim'})")
        # the sweep consumes the SEAM name, never the derived team_team_x
        assert bs.team_graph_name(None, "team_x") == team_name
        assert team_name.startswith(("test_", "tortoise_test")), \
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
    """#1686: team_* graphs (hosted parity — NEVER test-prefixed) reach the
    sweep ONLY via the journal: _sweep_drop drops any journaled name except
    the URI-default, so a journaled team_<name> is deleted at session end
    (this is the mechanism that stops team_* accumulation on the docker)."""
    from tests._embedded import _session_end_own_sweep
    from tortoise.projection import FalkorProjection, _journal_append_product

    journal = tmp_path / "session.graphs.jsonl"
    monkeypatch.setattr("tests._embedded._JOURNAL_FILE", str(journal))
    monkeypatch.setenv("TORTOISE_TEST_JOURNAL_FILE", str(journal))
    team_name = "team_ws_journal_drop"
    _journal_append_product(team_name)  # the #1686 mint-site seam
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_team_probe")
    try:
        proj.db.select_graph(team_name).query("CREATE (:TeamMeta {name:'x'})")
        res = _session_end_own_sweep(os.environ["TORTOISE_DB_URI"], str(journal))
        assert res["journal_removed"] is True
        assert not journal.exists()
        remaining = proj.db.list_graphs() or []
        assert team_name not in remaining
    finally:
        proj.close()


def test_leftover_team_strays_dropped_when_opted_in(uri_env, monkeypatch):
    """#1686 closure (review P1-1 fix): the journal-blind team_* residual
    class is closed by _sweep_team_strays, but ONLY when allowed — an
    explicit TORTOISE_TEST_SWEEP_TEAM_STRAYS=1 opt-in (never inferred from
    the URI path since #1884; a pathless shared/dev docker never triggers
    it). The helper is exercised DIRECTLY (no mid-suite global wipe — the
    last-suite-standing gate is conftest's, not this helper's; review
    P1-2)."""
    from tests._embedded import _sweep_team_strays
    from tortoise.projection import FalkorProjection

    monkeypatch.setenv("TORTOISE_TEST_SWEEP_TEAM_STRAYS", "1")
    stray = "team_ws_stray_8f3a"
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_leftover_probe")
    try:
        proj.db.select_graph(stray).query("CREATE (:TeamMeta {name:'stray'})")
        dropped = _sweep_team_strays(proj, os.environ["TORTOISE_DB_URI"])
        assert stray in dropped, f"expected {stray} dropped, got {dropped!r}"
        remaining = proj.db.list_graphs() or []
        assert stray not in remaining
    finally:
        proj.close()


def test_leftover_team_strays_refused_on_shared_docker(uri_env, monkeypatch):
    """#1686 default-fail-safe (review P1-1): a pathless shared/dev URI does
    NOT trigger the team_* pass without the explicit opt-in — team_<name> is
    the product's mint namespace and real tenant graphs must survive on a
    shared docker. The stray is cleaned up directly by the test itself."""
    from tests._embedded import _sweep_team_strays
    from tortoise.projection import FalkorProjection

    monkeypatch.delenv("TORTOISE_TEST_SWEEP_TEAM_STRAYS", raising=False)
    stray = "team_ws_stray_keep"
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_leftover_probe")
    try:
        proj.db.select_graph(stray).query("CREATE (:TeamMeta {name:'keep'})")
        dropped = _sweep_team_strays(proj, "docker://:falkordb@localhost:6379")
        assert dropped == [], f"shared-docker sweep must refuse, got {dropped!r}"
        remaining = proj.db.list_graphs() or []
        assert stray in remaining, "product-named graph must survive"
    finally:
        proj.db.select_graph(stray).query("MATCH (n) DETACH DELETE n")
        proj.db.select_graph(stray).delete()
        proj.close()


def test_leftover_team_strays_refused_on_test_matrix_uri(uri_env, monkeypatch):
    """#1884 regression: the LONGMEM_EVAL URI (docker://.../tortoise_test_
    matrix — the shared dev container's test-named graph) does NOT trigger
    the journal-blind team_* pass without the explicit opt-in. The
    re-validation ran per-question graphs named team_default__default__{qid}
    against this exact URI; a concurrent docker-lane pytest session ending
    last-suite-standing inferred "dedicated test DB" from the "test" path
    substring and DETACH-DELETEd + GRAPH.DELETEd the eval's LIVE graphs
    mid-ingest (silent write loss: pool_size 8 vs 374 ingested points). The
    opt-in-only gate makes the eval's graphs survive any concurrent test
    session's sweep on the shared container."""
    from tests._embedded import _sweep_team_strays, _team_sweep_allowed
    from tortoise.projection import FalkorProjection

    eval_uri = "docker://:falkordb@localhost:6379/tortoise_test_matrix"
    assert "test" in eval_uri.split("/")[-1], \
        "fixture URI must carry the test-named path (the eval's shared container)"
    monkeypatch.delenv("TORTOISE_TEST_SWEEP_TEAM_STRAYS", raising=False)
    # the retracted inference: the URI path says "test" but the gate refuses
    assert _team_sweep_allowed(eval_uri) is False, \
        "URI-path 'test' inference must be retracted (#1884)"
    stray = f"team_ws_eval_stray_{uuid.uuid4().hex[:8]}"
    proj = FalkorProjection.from_uri(
        "docker://:falkordb@localhost:6379", graph_name="test_ws_evalsweep_probe")
    try:
        proj.db.select_graph(stray).query("CREATE (:TeamMeta {name:'eval'})")
        dropped = _sweep_team_strays(proj, eval_uri)
        assert dropped == [], \
            f"eval-URI sweep must refuse without opt-in, got {dropped!r}"
        remaining = proj.db.list_graphs() or []
        assert stray in remaining, \
            "eval question graphs (team_*) must survive a concurrent session's sweep"
    finally:
        proj.db.select_graph(stray).query("MATCH (n) DETACH DELETE n")
        proj.db.select_graph(stray).delete()
        proj.close()
