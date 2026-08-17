"""#1359 — FalkorDB server compatibility: version detection + vector API fallback.

Unit tests mock BOTH engine APIs (RediSearch-style procedure engine vs
Cypher-native engine) and verify:
  - `_get_falkordb_version` resolves via db.info() / MODULE LIST / INFO server
    and degrades to None without crashing.
  - `_ensure_indexes` creates the vector index with the API the engine
    registers (procedure first, Cypher `CREATE VECTOR INDEX` fallback) and
    records `_vector_index_api` on the projection.
  - embedded mode skips vector index creation entirely (unchanged).

Plus a live integration test against a running FalkorDB server
(`docker://localhost:6399/tortoise-1359`) that skips when unavailable.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.projection import FalkorProjection  # noqa: E402
from tortoise.projection import _reset_falkordb_version_cache  # noqa: E402
from tortoise.search_engine import degradation_chain, run_vector_query  # noqa: E402

DIM = 384  # matches projection's vector index dimension


# ── Mock helpers ────────────────────────────────────────────────────────────

class _ResultSet:
    def __init__(self, result_set: list):
        self.result_set = result_set


class _EngineGraph:
    """Mock graph dispatching on Cypher content — models a server engine.

    ``vector_api``: 'procedure' | 'cypher' | 'none' — how index creation and
    queryNodes behave on this engine.
    """

    def __init__(self, vector_api: str = "cypher"):
        self.vector_api = vector_api
        self.calls: list[str] = []
        self._vector_index_created = False

    def query(self, cypher: str, params=None, timeout=None):
        self.calls.append(cypher)
        low = cypher.lower()
        if "db.idx.vector.createnodeindex" in low:
            if self.vector_api == "procedure":
                if self._vector_index_created:
                    raise Exception("Attribute 'embedding' is already indexed")
                self._vector_index_created = True
                return _ResultSet([])
            raise Exception(
                "Procedure `db.idx.vector.createNodeIndex` is not registered")
        if "create vector index" in low:
            if self.vector_api == "none":
                raise Exception("Unknown function/procedure for vector index")
            if self._vector_index_created:
                raise Exception("Attribute 'embedding' is already indexed")
            self._vector_index_created = True
            return _ResultSet([])
        if "db.idx.vector.querynodes" in low:
            if self.vector_api == "none":
                raise Exception(
                    "Procedure `db.idx.vector.queryNodes` is not registered")
            if "vecf32($query_vec)" in cypher:
                # Signature B: (label, attr, k, vecf32(vec)) → node, score
                return _ResultSet([("near-1", 0.95), ("near-2", 0.8)])
            # Signature A: (label, attr, vec, k) → node
            return _ResultSet([("near-1",), ("near-2",)])
        if "db.idx.fulltext.create" in low:
            return _ResultSet([])  # FTS index creation succeeds
        return _ResultSet([])  # range indexes / everything else


def _bare_projection(graph) -> FalkorProjection:
    """Build a FalkorProjection without opening a DB (unit-only)."""
    proj = object.__new__(FalkorProjection)
    proj._is_embedded = False
    proj._graph_name = "test_1359"
    proj._skip_guard = False
    proj.g = graph
    proj._falkordb_version = (4, 18, 3)
    proj._vector_index_api = None
    return proj


# ── _get_falkordb_version ───────────────────────────────────────────────────

class TestGetFalkordbVersion:
    """Version detection across engine/client variants (#1359)."""

    def _proj(self, db) -> FalkorProjection:
        proj = object.__new__(FalkorProjection)
        proj.db = db
        return proj

    def test_db_info_method(self):
        """Legacy client with .info() → module:name=graph,ver=41803 parse."""
        class _Db:
            def info(self, *a):
                return "module:name=graph,ver=41803"

        assert self._proj(_Db())._get_falkordb_version() == (4, 18, 3)

    def test_module_list_fallback(self):
        """Client without .info() → MODULE LIST via raw connection (the
        installed falkordb client case, verified 2026-08-17)."""
        class _Db:
            connection = type("_Conn", (), {
                "execute_command": staticmethod(
                    lambda *args: [["name", "graph", "ver", 41803,
                                    "path", "/x/falkordb.so", "args", []]])
            })()

        assert self._proj(_Db())._get_falkordb_version() == (4, 18, 3)

    def test_info_server_dict_fallback(self):
        """MODULE LIST empty → INFO server dict with falkordb_version key."""
        class _Db:
            connection = type("_Conn", (), {
                "execute_command": staticmethod(
                    lambda *args: {"falkordb_version": "4.2.5"}
                    if args and args[0] == "INFO"
                    else [])
            })()

        assert self._proj(_Db())._get_falkordb_version() == (4, 2, 5)

    def test_no_info_and_no_probes_returns_none(self):
        """Everything unavailable → None, no crash (current behavior)."""
        class _Db:
            connection = type("_Conn", (), {
                "execute_command": staticmethod(lambda *args: [])
            })()

        assert self._proj(_Db())._get_falkordb_version() is None

    def test_info_missing_no_connection_returns_none(self):
        """Client with neither .info() nor .connection → None, no crash."""
        class _Db:
            pass

        assert self._proj(_Db())._get_falkordb_version() is None

    def test_module_list_errors_return_none(self):
        """Raw connection raises → None, no crash."""
        class _Db:
            connection = type("_Conn", (), {
                "execute_command": staticmethod(
                    lambda *args: (_ for _ in ()).throw(RuntimeError("boom")))
            })()

        assert self._proj(_Db())._get_falkordb_version() is None


class _CachedConn:
    """Raw connection carrying a real endpoint — for the cache tests."""

    def __init__(self, host: str = "cache-host", port: int = 6399):
        self._host = host
        self.port = port
        self.calls = 0

    def execute_command(self, *args):
        self.calls += 1
        if args and args[0] == "MODULE":
            return [["name", "graph", "ver", 41803,
                     "path", "/x/falkordb.so", "args", []]]
        return []


class TestVersionProbeCached:
    """Version detection is cached per endpoint for the process lifetime
    (#1359 review P2) — the 2-RTT probe (MODULE LIST + INFO server) must
    not run on every projection open."""

    def _proj(self, db) -> FalkorProjection:
        proj = object.__new__(FalkorProjection)
        proj.db = db
        return proj

    def _db(self, conn):
        return type("_Db", (), {"connection": conn})()

    def test_second_projection_on_same_endpoint_skips_probes(self):
        """Same (host, port) → second projection hits the cache, zero
        network probes."""
        _reset_falkordb_version_cache()
        try:
            conn = _CachedConn()
            assert self._proj(self._db(conn))._get_falkordb_version() == (4, 18, 3)
            assert conn.calls == 1  # probed once

            conn2 = _CachedConn()
            assert self._proj(self._db(conn2))._get_falkordb_version() == (4, 18, 3)
            assert conn2.calls == 0  # cached — no probes
        finally:
            _reset_falkordb_version_cache()

    def test_distinct_endpoints_do_not_collide(self):
        """Different (host, port) → each probed independently."""
        _reset_falkordb_version_cache()
        try:
            conn_a = _CachedConn(host="host-a", port=6399)
            conn_b = _CachedConn(host="host-b", port=6399)
            assert self._proj(self._db(conn_a))._get_falkordb_version() == (4, 18, 3)
            assert self._proj(self._db(conn_b))._get_falkordb_version() == (4, 18, 3)
            assert conn_a.calls == 1
            assert conn_b.calls == 1
        finally:
            _reset_falkordb_version_cache()

    def test_unidentified_client_never_cached(self):
        """No endpoint attrs (unit mocks) → probed fresh every call."""
        _reset_falkordb_version_cache()
        try:
            class _Conn:
                calls = 0

                def execute_command(self, *args):
                    self.calls += 1
                    return []

            conn = _Conn()
            db = type("_Db", (), {"connection": conn})()
            assert self._proj(db)._get_falkordb_version() is None
            assert self._proj(db)._get_falkordb_version() is None
            # 2 probes (MODULE LIST + INFO server) per invocation — not cached
            assert conn.calls == 4
        finally:
            _reset_falkordb_version_cache()


# ── _ensure_indexes vector branch ───────────────────────────────────────────

class TestEnsureIndexesVectorApi:
    """Vector index creation uses the API the engine registers (#1359)."""

    @pytest.mark.parametrize("vector_api,expected", [
        ("procedure", "procedure"),
        ("cypher", "cypher"),
    ])
    def test_vector_index_created_with_engine_api(self, vector_api, expected):
        graph = _EngineGraph(vector_api=vector_api)
        proj = _bare_projection(graph)
        proj._ensure_indexes()

        assert proj._vector_index_api == expected
        vector_creates = [c for c in graph.calls
                          if "vector" in c.lower()
                          and ("createnodeindex" in c.lower()
                               or "create vector index" in c.lower())]
        assert vector_creates, "no vector index creation attempt recorded"

    def test_procedure_engine_uses_procedure_first(self):
        """Procedure engine → createNodeIndex called, NO Cypher fallback."""
        graph = _EngineGraph(vector_api="procedure")
        proj = _bare_projection(graph)
        proj._ensure_indexes()

        assert proj._vector_index_api == "procedure"
        assert any("db.idx.vector.createNodeIndex" in c for c in graph.calls)
        assert not any("CREATE VECTOR INDEX FOR" in c for c in graph.calls)

    def test_cypher_engine_falls_back_to_create_vector_index(self):
        """Cypher-native engine → createNodeIndex fails (not registered),
        `CREATE VECTOR INDEX` used instead."""
        graph = _EngineGraph(vector_api="cypher")
        proj = _bare_projection(graph)
        proj._ensure_indexes()

        assert proj._vector_index_api == "cypher"
        assert any("CREATE VECTOR INDEX FOR" in c for c in graph.calls)

    def test_embedded_skips_vector_index(self):
        """Embedded mode → vector index creation NEVER attempted (unchanged)."""
        graph = _EngineGraph(vector_api="cypher")
        proj = object.__new__(FalkorProjection)
        proj._is_embedded = True
        proj._graph_name = "test_1359"
        proj._skip_guard = False
        proj.g = graph
        proj._falkordb_version = (4, 18, 3)
        proj._vector_index_api = None
        proj._ensure_indexes()

        assert proj._vector_index_api is None
        assert not any("VECTOR" in c.upper() and "INDEX" in c.upper()
                       for c in graph.calls)

    def test_both_apis_fail_records_none_and_logs_warning(self, caplog):
        """Neither API works → _vector_index_api stays None + warning."""
        graph = _EngineGraph(vector_api="none")
        proj = _bare_projection(graph)
        with caplog.at_level("WARNING", logger="tortoise.projection"):
            proj._ensure_indexes()

        assert proj._vector_index_api is None
        assert any("Failed to create vector index" in r.message
                   for r in caplog.records)


# ── run_vector_query / degradation_chain consume _vector_index_api ─────────

class TestVectorIndexApiConsumption:
    """The recorded _vector_index_api is threaded into the query path
    (#1359 review P2): 'cypher' engines skip the failing sig-A attempt."""

    QUERY_VEC = [0.1] * DIM

    def test_run_vector_query_cypher_api_skips_signature_a(self):
        """vector_index_api='cypher' → queryNodes sig B called directly;
        sig A NOT attempted (one failed round trip saved per query)."""
        graph = _EngineGraph(vector_api="cypher")
        hits = run_vector_query(
            graph, self.QUERY_VEC, limit=10, is_embedded=False,
            vector_index_api="cypher")
        assert [h[0] for h in hits] == ["near-1", "near-2"]
        qn = [c for c in graph.calls if "querynodes" in c.lower()]
        assert len(qn) == 1
        assert "vecf32($query_vec)" in qn[0]        # sig B form only
        assert "$query_vec, $limit" not in qn[0]    # sig A not attempted

    def test_run_vector_query_procedure_api_uses_signature_a_first(self):
        """vector_index_api='procedure' → sig A attempted first (unchanged)."""
        graph = _EngineGraph(vector_api="procedure")
        hits = run_vector_query(
            graph, self.QUERY_VEC, limit=10, is_embedded=False,
            vector_index_api="procedure")
        assert [h[0] for h in hits] == ["near-1", "near-2"]
        qn = [c for c in graph.calls if "querynodes" in c.lower()]
        assert len(qn) == 1
        assert "$query_vec, $limit" in qn[0]
        assert "vecf32($query_vec)" not in qn[0]

    def test_degradation_chain_threads_vector_index_api(self):
        """degradation_chain forwards vector_index_api → the vector runner
        hits sig B directly on a Cypher-native engine."""
        graph = _EngineGraph(vector_api="cypher")
        results = degradation_chain(
            graph, query="find near", kind=None,
            query_vec=self.QUERY_VEC,
            strategies={"vector": True, "fts": False, "structural": False},
            is_embedded=False, limit=10,
            vector_index_api="cypher",
        )
        assert "vector" in results
        assert [h[0] for h in results["vector"]] == ["near-1", "near-2"]
        qn = [c for c in graph.calls if "querynodes" in c.lower()]
        assert len(qn) == 1
        assert "vecf32($query_vec)" in qn[0]


# ── Live integration (skips when no server) ─────────────────────────────────

_LIVE_URI = "docker://localhost:6399/tortoise-1359"
_LIVE_AVAILABLE = False
try:
    _probe = FalkorProjection.from_uri(_LIVE_URI)
    try:
        _probe.g.query("RETURN 1")
        _LIVE_AVAILABLE = True
    finally:
        _probe.close()
except Exception:
    _LIVE_AVAILABLE = False


@pytest.mark.skipif(not _LIVE_AVAILABLE,
                    reason="Live FalkorDB server on localhost:6399 not available")
class TestLiveServerCompat:
    """End-to-end against the running server (falkordblite 0.10.0 module)."""

    def _vec(self, val: float) -> list[float]:
        return [val] * DIM

    def test_full_compat_flow(self):
        """Version detected → FTS + vector index via cypher → query returns
        seeded results through whatever path works."""
        proj = FalkorProjection.from_uri(_LIVE_URI)
        g = proj.g
        prefix = "compat1359_"
        try:
            # (a) version detection (may be None on exotic engines — no crash)
            ver = getattr(proj, "_falkordb_version", None)
            # (b) indexes created — vector index MUST be recorded as created
            # via one of the two APIs (on the live engine: cypher).
            assert proj._vector_index_api in ("procedure", "cypher"), \
                f"vector index not created: api={proj._vector_index_api!r}"
            if ver is not None:
                assert ver[0] >= 4, f"unexpected version {ver}"

            # (c) seed two points (one near the query vector, one far)
            g.query(f"MATCH (n:Point) WHERE n.id STARTS WITH '{prefix}' DETACH DELETE n")
            g.query(
                f"CREATE (a:Point {{id: '{prefix}near', embedding: vecf32($v)}})",
                params={"v": self._vec(0.1)})
            g.query(
                f"CREATE (b:Point {{id: '{prefix}far', embedding: vecf32($v)}})",
                params={"v": self._vec(0.9)})

            # (d) index-accelerated or brute-force — either must surface the
            # near vector (verified: queryNodes sig B works on this engine).
            from tortoise.search_engine import run_vector_query
            hits = run_vector_query(g, self._vec(0.1), limit=10, is_embedded=False)
            ids = [h[0] for h in hits]
            assert f"{prefix}near" in ids, f"near vector not found: {hits}"

            # (e) FTS index exists (procedure form — verified working here)
            idx = g.query("CALL db.indexes()").result_set
            fts = [r for r in idx if r[0] == "Point" and "FULLTEXT" in str(r)]
            assert fts, f"Point FULLTEXT index missing: {idx[:3]}"
        finally:
            try:
                g.query(
                    f"MATCH (n:Point) WHERE n.id STARTS WITH '{prefix}' DETACH DELETE n")
            except Exception:
                pass
            proj.close()
