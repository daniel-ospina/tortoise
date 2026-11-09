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
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.projection import FalkorProjection  # noqa: E402, I001, RUF100
from tortoise.projection import _reset_falkordb_version_cache  # noqa: E402, RUF100
from tortoise.search_engine import degradation_chain, run_vector_query  # noqa: E402, RUF100

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
        if "db.indexes()" in low:
            # #11642: the retirement gate reads the catalogue before it drops
            # anything, so the mock must answer it or the gate refuses to drop.
            if not self._vector_index_created:
                return _ResultSet([])
            return _ResultSet([("Point", "Point", {"embedding": ["VECTOR"]})])
        if "drop index" in low and "embedding" in low:
            if not self._vector_index_created:
                raise Exception("No such index")
            self._vector_index_created = False
            self.dropped = True
            return _ResultSet([])
        if "db.idx.vector.querynodes" in low:
            if self.vector_api == "none":
                raise Exception(
                    "Procedure `db.idx.vector.queryNodes` is not registered")
            # #6214: discriminate by the CALL's argument ORDER, not by the
            # presence of vecf32($query_vec) — signature A now carries that
            # wrapper too, for the vec.cosineDistance() it RETURNs, so the old
            # substring test would misclassify an A query as B.
            if "'embedding', $limit, vecf32($query_vec))" in cypher:
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
        """No endpoint identity at all — no connection attrs AND no pool kwargs
        (unit mocks) → probed fresh every call. A client with no ATTRS but a pool
        IS identified now (#11328), which is the whole fix."""
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


class TestVersionProbeCacheKeyServerMode:
    """The cache key must resolve on the client shape a HOSTED REQUEST builds
    (#11328).

    redis-py 7.4.1's client carries NO ``.host``/``.port``/``._host`` of its
    own — the configured endpoint lives in
    ``connection_pool.connection_kwargs``. Reading only the connection's
    attributes therefore answered ``None`` here, so ``_get_falkordb_version``
    never read or wrote ``_FALKORDB_VERSION_CACHE`` and the 2-RTT probe
    (MODULE LIST + INFO server) ran on **every** projection construction —
    i.e. per hosted request on the paid tier, which is the exact cost the
    cache exists to remove.

    The tests below use the REAL client object (a ``redis.Redis`` built without
    connecting) wherever the claim is about a real client SHAPE — a fake with
    ``.host``/``.port`` bolted on would pin a client that does not exist and
    would stay green through this defect. Where a test deliberately exercises a
    shape no real client produces (a partially-resolving pool), it builds that
    shape explicitly and says so.
    """

    def _proj(self, db) -> FalkorProjection:
        proj = object.__new__(FalkorProjection)
        proj.db = db
        return proj

    def _pool_only_client(self, host: str = "10.0.0.5", port: int = 6379):
        import redis

        client = redis.Redis(host=host, port=port)
        # The premise of the whole class: assert the shape, not the assumption.
        assert not hasattr(client, "host") and not hasattr(client, "_host"), (
            "the pinned redis-py now exposes .host — the defect this pins is gone, "
            "so re-measure rather than leaving a test that proves nothing"
        )
        return client

    def test_pool_only_client_resolves_a_server_key(self):
        """A client whose endpoint lives only in the pool kwargs still keys."""
        db = type("_Db", (), {"connection": self._pool_only_client()})()
        assert self._proj(db)._falkordb_version_cache_key() == (
            "server", "10.0.0.5", 6379)

    def test_the_key_is_the_same_bucket_as_the_store_identity(self):
        """One store must not land in two buckets: the key the version cache uses
        and the endpoint the `#4997`/`#11087` registers key on are the SAME tuple
        for a resolved server client.

        ⛔ The projection's `_host` is deliberately set to a DIFFERENT host than
        the pool's (only a real projection keeps them equal — `__init__` uses one
        `host` local for both). With them equal this test could not fail for the
        property it names: `_stable_endpoint()` delegates to this key, so equality
        would be guaranteed by construction and a future edit that sent the
        registers to `_host` while the cache keyed on the pool would still pass.
        Divergent is the only discriminating shape, and it also pins WHICH source
        wins: the pool's, because a redis-py client carries no endpoint
        attributes at all.
        """
        proj = self._proj(
            type("_Db", (), {"connection": self._pool_only_client()})())
        proj._host = "a-different-host"
        assert proj._falkordb_version_cache_key() == ("server", "10.0.0.5", 6379)
        assert proj._stable_endpoint() == proj._falkordb_version_cache_key()

    def test_second_projection_of_a_pool_only_client_skips_the_probe(self):
        """The consequence, end to end: two projections of ONE store probe the
        version ONCE. This is the per-request cost #11328 removes."""
        _reset_falkordb_version_cache()
        try:
            probes = {"n": 0}

            def _counting_probe(self):
                probes["n"] += 1
                return (4, 18, 3)

            first = self._proj(
                type("_Db", (), {"connection": self._pool_only_client()})())
            first._probe_falkordb_version = _counting_probe.__get__(first)
            assert first._get_falkordb_version() == (4, 18, 3)
            assert probes["n"] == 1

            second = self._proj(
                type("_Db", (), {"connection": self._pool_only_client()})())
            second._probe_falkordb_version = _counting_probe.__get__(second)
            assert second._get_falkordb_version() == (4, 18, 3)
            assert probes["n"] == 1, (
                "a second projection of the same store must hit the cache, not "
                "re-run the 2-RTT probe (this is the per-hosted-request cost)"
            )
        finally:
            _reset_falkordb_version_cache()

    def test_embedded_still_keys_on_the_path(self):
        """Regression pin: the embedded lane must not be re-keyed as a server
        bucket by the pool fallback.

        ⛔ The client here is deliberately a SERVER-shaped one (`_pool_only_client`,
        whose pool kwargs DO carry host/port), not `object()`: with a pool-less
        mock the fallback branch is never entered, so deleting the `and path is
        None` guard leaves the key unchanged and this pin stays GREEN through the
        mutation it advertises (#11360 review, measured). A real embedded client's
        pool carries `path` alone, so pinning the STRONGER shape is the only
        version whose failure is attributable to the guard: `_path` must win over
        a resolvable pool, because `_path` is the attribute the embedded key is
        built from.
        """
        proj = self._proj(
            type("_Db", (), {"connection": self._pool_only_client()})())
        proj._path = "/tmp/embedded.db"
        proj._is_embedded = True
        assert proj._falkordb_version_cache_key() == (
            "embedded", "/tmp/embedded.db")

    def test_the_embedded_guard_reads_the_same_attribute_as_the_fallthrough(self):
        """`_is_embedded` and `_path` are two attributes for one fact, and
        `__init__` sets them together — but a guard that reads one while the
        fallthrough reads the other disagrees on the shape where they diverge,
        silently re-keying an embedded store as a network store (#11360 review
        P3). The guard must read `_path`, the attribute the embedded key is
        BUILT from, so the two cannot disagree. Divergence is staged here on
        purpose: `_path` present, `_is_embedded` left False."""
        proj = self._proj(
            type("_Db", (), {"connection": self._pool_only_client()})())
        proj._path = "/tmp/embedded-but-flagged-server.db"
        proj._is_embedded = False
        assert proj._falkordb_version_cache_key() == (
            "embedded", "/tmp/embedded-but-flagged-server.db")

    def test_a_partial_connection_does_not_mint_a_hybrid_endpoint(self):
        """Both identity fields must come from ONE source (#11360 review P3):
        host from the connection and port from the pool would mint
        `('server', pool-host, conn-port)`, a tuple matching NEITHER source —
        which can equal a different store's real endpoint, serving its version
        to this store and merging the two stores' registers. A redis-py client
        never resolves half an endpoint, but a partial fake does."""
        partial = SimpleNamespace(connection=SimpleNamespace(port=7000))
        partial.connection.connection_pool = SimpleNamespace(
            connection_kwargs={"host": "pool-host", "port": 6399})
        key = self._proj(partial)._falkordb_version_cache_key()
        assert key == ("server", "pool-host", 6399), (
            f"expected the pool's complete pair, got {key!r} — a hybrid sources "
            "the two fields from different places and matches no real store"
        )


# ── _ensure_indexes vector branch ───────────────────────────────────────────

class TestEnsureIndexesVectorApi:
    """#1359's engine-API resolution is RETIRED by #11642 — inverted, not deleted.

    ⛔ THIS CLASS USED TO PIN WHICH API `_ensure_indexes` USED TO *CREATE* the
    in-graph `Point.embedding` HNSW index: `createNodeIndex` on a
    procedure-capable engine, `CREATE VECTOR INDEX` on a Cypher-native one, and
    a WARNING when neither worked. The owner's 2026-11-07 decision retired that
    index (its `vectorset` was rebuilt on every cold restore — `#7846`), so the
    property worth pinning INVERTED: the store must now DISCOVER an existing
    index from the catalogue and DROP it, must never create one, and must leave
    the handle `None` so `required_embedding_dim` reports the documented
    no-index lane.

    The engine-API distinction is not lost with the creation — `run_vector_query`
    still needs it for the two `queryNodes` signatures, which
    `TestVectorIndexApiConsumption` below and the docker lane both cover. What
    deleting this class would lose is the only pin on the RETIREMENT's shape.
    """

    @pytest.mark.parametrize("vector_api", ["procedure", "cypher", "none"])
    def test_no_engine_api_is_ever_asked_to_create_a_vector_index(self, vector_api):
        graph = _EngineGraph(vector_api=vector_api)
        proj = _bare_projection(graph)
        proj._ensure_indexes()

        assert proj._vector_index_api is None, (
            "an in-graph vector-index API was resolved — #11642 retired that "
            "index, so no engine capability may be recorded as if one exists")
        creates = [c for c in graph.calls
                   if "vector" in c.lower()
                   and ("createnodeindex" in c.lower()
                        or "create vector index" in c.lower())]
        assert not creates, (
            f"the in-graph vector index was CREATED on a {vector_api!r} engine: "
            f"{creates}")

    def test_an_existing_vector_index_is_dropped(self, monkeypatch):
        # The retirement is authorised only when the Postgres dense leg is
        # armed (#11660 review round 4): the index and its replacement are one
        # decision, so this pin exercises the ARMED posture.
        monkeypatch.setenv("TORTOISE_RECORD_DSN", "postgresql://localhost/record")
        monkeypatch.setenv("TORTOISE_RECORD_VECTOR_LEG", "1")
        """The retirement's positive half: a store that HAS one loses it.

        This is the migration for every already-deployed graph — the one a cold
        restore currently pays 436.5 s (untuned) to rebuild the index for.
        """
        graph = _EngineGraph(vector_api="procedure")
        graph._vector_index_created = True  # a store from before the move
        proj = _bare_projection(graph)
        proj._ensure_indexes()

        assert getattr(graph, "dropped", False), (
            "the store held a vector index and the setup did not drop it")
        assert proj._vector_index_api is None

    def test_a_store_with_no_vector_index_emits_no_drop(self):
        """The steady state costs ZERO DDL — the gate is the catalogue.

        `_ensure_indexes` is reached from the hosted PER-REQUEST path, so an
        unconditional `DROP INDEX` would be a FAILING DDL round trip per
        request, and it reds the repeated-bootstrap pin in
        `tests/test_indexes.py`.
        """
        graph = _EngineGraph(vector_api="procedure")
        proj = _bare_projection(graph)
        proj._ensure_indexes()

        drops = [c for c in graph.calls
                 if "drop index" in c.lower() and "embedding" in c.lower()]
        assert not drops, f"a clean store was sent a DROP: {drops}"

    def test_embedded_skips_the_vector_branch_entirely(self):
        """Embedded mode → no catalogue probe, no drop, no creation (unchanged)."""
        graph = _EngineGraph(vector_api="cypher")
        proj = object.__new__(FalkorProjection)
        proj._is_embedded = True
        proj._graph_name = "test_1359"
        proj.g = graph
        proj._falkordb_version = (4, 18, 3)
        proj._vector_index_api = None
        proj._ensure_indexes()

        assert proj._vector_index_api is None
        assert not any("VECTOR" in c.upper() and "INDEX" in c.upper()
                       and "FULLTEXT" not in c.upper()
                       for c in graph.calls)


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
        # #6214: the fixture returns near-1 at distance 0.95 and near-2 at 0.8,
        # i.e. NOT in distance order. The leg must reorder by distance, so
        # near-2 (closer) ranks first — the pre-#6214 pass-through returned
        # the engine's near-1 first.
        assert [h[0] for h in hits] == ["near-2", "near-1"]
        qn = [c for c in graph.calls if "querynodes" in c.lower()]
        assert len(qn) == 1
        assert "vecf32($query_vec)" in qn[0]        # sig B form only
        assert "$query_vec, $limit" not in qn[0]    # sig A not attempted

    def test_run_vector_query_procedure_api_uses_signature_a_first(self):
        """vector_index_api='procedure' → sig A attempted first (unchanged).

        The ORDER assertion below is the shared tie's, not this test's subject
        (#10121): signature A's rows carry no readable distance (the mock
        returns one column per row, so ``float(row[1])`` raises and both rows
        are annotated ``None``), so the equal — absent — distance falls through
        to the recency-then-id key, which is ``DESC`` on every label. That puts
        ``near-2`` first where the pre-#10121 ``(distance, id ASC)`` pass put
        ``near-1``. Determinism is what this test needs from the order; the
        direction is pinned behaviourally in
        ``tests/test_10121_recency_tie.py``.
        """
        graph = _EngineGraph(vector_api="procedure")
        hits = run_vector_query(
            graph, self.QUERY_VEC, limit=10, is_embedded=False,
            vector_index_api="procedure")
        assert [h[0] for h in hits] == ["near-2", "near-1"]
        qn = [c for c in graph.calls if "querynodes" in c.lower()]
        assert len(qn) == 1
        # #6214: sig A now also carries vecf32($query_vec) — for the
        # vec.cosineDistance() it RETURNs. The A/B distinction is the CALL's
        # argument ORDER, so pin that instead of the absence of the wrapper.
        assert "'embedding', $query_vec, $limit)" in qn[0]
        assert "'embedding', $limit, vecf32($query_vec))" not in qn[0]

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
        assert [h[0] for h in results["vector"]] == ["near-2", "near-1"]
        qn = [c for c in graph.calls if "querynodes" in c.lower()]
        assert len(qn) == 1
        assert "vecf32($query_vec)" in qn[0]


# ── Live integration (skips when no server) ─────────────────────────────────

# Epic #1647 (T7): per-session unique test-prefixed path — the historical
# tortoise-1359 was a FIXED non-test-prefixed graph name (cross-run
# pollution; the bulk-scoped deletes below are STARTS-WITH, so no guard
# trip, but every session probed the same shared graph).
_LIVE_URI = f"docker://localhost:6399/test_falkordb_compat_{os.urandom(4).hex()}"
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
            try:  # noqa: SIM105
                g.query(
                    f"MATCH (n:Point) WHERE n.id STARTS WITH '{prefix}' DETACH DELETE n")
            except Exception:
                pass
            proj.close()
