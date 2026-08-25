# tests/test_divergence_conformance.py
"""E2E-8: the D1–D16 divergence surface, executable (epic #1647 Task 8).

The research brief (docs/epics/2026-08-24-test-db-migration/research-brief.md
§2) maps every `_is_embedded` / embedded-specific branch to an engine behavior
pair. This file makes that table RUNNABLE: each divergence is a test
parametrized over BOTH lanes (embedded FalkorDBLite vs docker FalkorDB), with
mode-split assertions — embedded asserts the legacy behavior, docker asserts
the server behavior — never a skip (except the documented docker-absent
skip).

Env control is EXPLICIT per leg (cycle-8 P2-13): the embedded leg delenvs
TORTOISE_DB_URI (+ TORTOISE_TEST_MODE), the docker leg setenvs it — the same
pattern as E2E-1's test_round_trip_same_shape (Task 1 Step 5b). On a URI-set
dev/CI lane a leg with NO env control would run docker-vs-docker and the
conformance file could never detect divergence. Both legs are declared in
DELIBERATE_URI_MUTATIONS (tests/test_uri_env_mutations_declared.py).

The P2 divergence-confirmation pass (plan Task 8 Step 4) consumes this file:
per-divergence observed-vs-expected is reported from the parametrized
nodeids, and the findings land in docs/divergence-change-list.md.
"""
from __future__ import annotations

import contextlib
import json
import os

import pytest

from tortoise.log import EventLog
from tortoise.projection import FalkorProjection

pytestmark = pytest.mark.timeout(300)

_LEGS = ["embedded", "docker"]

# Docker-leg URI (E2E-1's leg constant — the local provisioned service).
_DOCKER_URI = "docker://:falkordb@localhost:6379"


def _docker_reachable(host: str = "localhost", port: int = 6379) -> bool:
    """True when a live FalkorDB answers a TCP connect on host:port.

    Repo skip-guard convention (#1436): the docker leg SKIPS with a
    FalkorDB-reason when the docker is absent (post-merge-validation runs the
    full suite with NO docker service) — never ERROR on redis.ConnectionError.
    The fast CI job provisions the falkordb service, so the probe passes
    there and the docker leg actually runs.
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


@pytest.fixture(params=_LEGS)
def leg(request, monkeypatch):
    """Mode-parametrized env control (E2E-1 pattern, Task 1 Step 5b).

    The embedded leg DELENVS the URI (+ TEST_MODE) so the constructions below
    run embedded; the docker leg SETENVS it so the class-level redirect arms
    and the same constructions run server-mode. Each leg provably runs its
    intended backend — the delenv/setenv IS the divergence-detection
    mechanism (cycle-8 P2-13).
    """
    if request.param == "embedded":
        monkeypatch.delenv("TORTOISE_DB_URI", raising=False)
        monkeypatch.delenv("TORTOISE_TEST_MODE", raising=False)
    else:
        if not _docker_reachable():
            pytest.skip("live FalkorDB (localhost:6379) not reachable")
        monkeypatch.setenv("TORTOISE_DB_URI", _DOCKER_URI)
        monkeypatch.setenv("TORTOISE_TEST_MODE", "1")
    return request.param


# ── construction + seeding helpers ────────────────────────────────────────


def _projection(leg: str, tmp_path, name: str):
    """A FalkorProjection on the requested lane.

    Embedded leg: path= construction (URI unset → embedded). Docker leg: the
    SAME path= construction redirects (URI + TEST_MODE + calling test frame)
    to a server projection with a derived test_<stem>_<hash12> graph. One
    code path, provably both backends.
    """
    return FalkorProjection(str(tmp_path / name))


def _drop_own_graph(proj) -> None:
    """Best-effort drop of the test's OWN docker-leg graph (review P2-1/P2-2).

    In a URI-UNSET session the session journal is not active (conftest
    exports it only when the URI is set at import), so docker-leg mints would
    accumulate on a persistent dev docker — every conformance test drops its
    own graph (DETACH-equivalent GRAPH.DELETE) instead of relying on the
    session-end sweep. Embedded legs are local files — no-op. Missing-graph
    errors are success (idempotent); other errors are swallowed (cleanup
    must never mask the test's real result).
    """
    if getattr(proj, "_is_embedded", False):
        return
    with contextlib.suppress(Exception):
        proj.db.select_graph(proj.graph_name).delete()


def _point_count(proj) -> int:
    rows = proj.g.query("MATCH (n:Point) RETURN count(n)").result_set
    return int(rows[0][0]) if rows and rows[0][0] is not None else 0


def _point_event(i: int) -> dict:
    """PointAdded event shape (same as tests/test_ops_safety.py)."""
    return {"type": "PointAdded", "point": {
        "id": f"ops-pt-{i:03d}", "content": f"ops content {i}",
        "context": "ops-test"}}


def _write_adjacent_log(tmp_path, n: int) -> None:
    log = EventLog(str(tmp_path / "events.jsonl"))
    for i in range(n):
        log.append(_point_event(i))


def _range_indexes(proj):
    """{label: {field: [types]}} from CALL db.indexes() — normalizes the
    3.x flat per-field rows and the 4.x dict-keyed rows to one shape (same
    helper as tests/test_indexes.py)."""
    rows = proj.g.query("CALL db.indexes()").result_set
    out = {}
    for row in rows:
        label, fields, types = row[0], row[1], row[2]
        types = (dict(types) if isinstance(types, dict)
                 else ({f: [t] for f, t in zip(fields, types)}  # noqa: B905
                       if isinstance(types, (list, tuple))
                       else {f: [types] for f in fields}))
        out.setdefault(label, {}).update({f: types.get(f, []) for f in fields})
    return out


def _seed_embeddings(g) -> None:
    """Three 3-dim embedded points: p1≈query, p2 near, p3 far.

    Deterministic ordering probe for run_vector_query on both lanes
    (euclidean from [1,0,0]: p1=0, p2≈0.141, p3≈1.414)."""
    g.query("CREATE (p1:Point {id:'p1', content:'c1', is_operator:false, "
            "embedding: vecf32([1.0, 0.0, 0.0])})")
    g.query("CREATE (p2:Point {id:'p2', content:'c2', is_operator:false, "
            "embedding: vecf32([0.9, 0.1, 0.0])})")
    g.query("CREATE (p3:Point {id:'p3', content:'c3', is_operator:false, "
            "embedding: vecf32([0.0, 0.0, 1.0])})")


def _seed_cross_lens_graph(g) -> None:
    """The cross-lens pool seed (D9): p1≈p2 cross-vocab in-band (cosine
    0.994), p3 same-lens as p1 (excluded) / orthogonal to p2 (below band).
    Sources carry REGISTERED sourceKinds (T1/T2 — the pool's D3 gate)."""
    for pid, vec, ts in (("p1", [1.0, 0.0, 0.0], "2026-08-24T00:00:00+00:00"),
                         ("p2", [0.9, 0.1, 0.0], "2026-08-24T00:00:01+00:00"),
                         ("p3", [0.0, 0.0, 1.0], "2026-08-24T00:00:02+00:00")):
        g.query("CREATE (p:Point {id:$pid, content:'c', is_operator:false, "
                "embedding: vecf32($vec), updatedAt:$ts})",
                params={"pid": pid, "vec": vec, "ts": ts})
    g.query("CREATE (s1:Source {id:'http://s1', url:'http://s1', "
            "sourceKind:'T1'})")
    g.query("CREATE (s2:Source {id:'http://s2', url:'http://s2', "
            "sourceKind:'T2'})")
    g.query("MATCH (p:Point {id:'p1'}), (s:Source {id:'http://s1'}) "
            "CREATE (p)-[:extractedFrom]->(s)")
    g.query("MATCH (p:Point {id:'p2'}), (s:Source {id:'http://s2'}) "
            "CREATE (p)-[:extractedFrom]->(s)")
    g.query("MATCH (p:Point {id:'p3'}), (s:Source {id:'http://s1'}) "
            "CREATE (p)-[:extractedFrom]->(s)")


# ── D1: the _is_embedded seam itself (projection/__init__.py L612) ────────


def test_d1_is_embedded_seam_flag(leg, tmp_path):
    """D1 — the mode flag is the discriminator: path= → embedded,
    redirect → server. Each leg asserts ITS OWN value (the flag is what every
    other D-branch keys on)."""
    proj = _projection(leg, tmp_path, "d1.db")
    try:
        if leg == "embedded":
            assert proj._is_embedded is True
        else:
            assert proj._is_embedded is False
    finally:
        _drop_own_graph(proj)
        proj.close()


# ── D2: probe-failure recovery (projection/__init__.py L701 _auto_health_recover) ──


def test_d2_probe_failure_recovery(leg, tmp_path):
    """D2 — probe fail + adjacent JSONL: embedded AUTO-REBUILDS (recover_from_log);
    server FAILS LOUD (a remote DB is never rebuilt from a local log — the
    production-fail-loud branch)."""
    _write_adjacent_log(tmp_path, 5)
    db_path = str(tmp_path / "lost.db")
    orig = FalkorProjection._probe_ok
    FalkorProjection._probe_ok = lambda self: False
    try:
        if leg == "embedded":
            proj = FalkorProjection(db_path)
            try:
                assert _point_count(proj) == 5, \
                    "embedded must auto-rebuild from the adjacent log"
            finally:
                proj.close()
        else:
            with pytest.raises(RuntimeError, match="health check failed"):
                FalkorProjection(db_path)  # redirect → server mode → raises
    finally:
        FalkorProjection._probe_ok = orig


# ── D3: lost-graph recovery (projection/__init__.py L519 check) ───────────


def test_d3_lost_graph_recovery(leg, tmp_path):
    """D3 — probe ok + 0 nodes + non-empty adjacent log: embedded
    auto-recovers (recover_from_log); server SKIPS — a remote graph is never
    rebuilt from a local log."""
    _write_adjacent_log(tmp_path, 3)
    proj = _projection(leg, tmp_path, "lost.db")
    try:
        if leg == "embedded":
            assert _point_count(proj) == 3, \
                "embedded must auto-rebuild the lost graph from the log"
        else:
            assert _point_count(proj) == 0, \
                "server must NOT rebuild from a local log"
    finally:
        _drop_own_graph(proj)
        proj.close()


# ── D4: bulk-wipe graph guard (projection/__init__.py L70 _GuardedGraph) ──


def test_d4_bulk_wipe_graph_guard(leg, tmp_path):
    """D4 — the bulk DETACH DELETE guard is DISABLED on embedded (per-instance
    temp DB is inherently isolated) and ACTIVE on server (refuses non-test
    graphs). The docker leg bypasses the redirect (explicit host=) so the
    graph name is under test control — the redirect always derives test_
    names and could never trip the guard."""
    if leg == "embedded":
        proj = _projection(leg, tmp_path, "d4.db")
        try:
            proj.g.query("MATCH (n) DETACH DELETE n")  # guard disabled — no raise
        finally:
            proj.close()
        return
    # Server leg — explicit host= construction (never redirects).
    proj = FalkorProjection(host="localhost", port=6379, password="falkordb",
                            graph_name="divergence_guard_probe")
    try:
        with pytest.raises(RuntimeError, match="Graph guard"):
            proj.g.query("MATCH (n) DETACH DELETE n")  # non-test graph → refused
    finally:
        with contextlib.suppress(Exception):  # tidy: drop the probe graph (it
            # is not a test_ graph — the session sweep will never touch it)
            proj.db.select_graph("divergence_guard_probe").delete()
        _drop_own_graph(proj)
        proj.close()
    # A test_-prefixed graph passes the guard.
    proj2 = FalkorProjection(host="localhost", port=6379, password="falkordb",
                             graph_name="test_d4_guard_probe")
    try:
        proj2.g.query("MATCH (n) DETACH DELETE n")  # allowed
    finally:
        _drop_own_graph(proj2)  # review P2-1: fixed-name graph, not journaled
        proj2.close()


# ── D5: range index creation — IDENTICAL in both modes (L1214-1224) ──────


def test_d5_range_index_identical(leg, tmp_path):
    """D5 — the point_props range set (id, pointKind, content_hash) is created
    identically on BOTH engines; is_operator is deliberately absent from the
    D5 range set on both (the #522 regression guard — verified _ensure_indexes
    L1214-1224; docker's is_operator RANGE presence comes from the D6
    composite's leftmost prefix, asserted in test_d6)."""
    proj = _projection(leg, tmp_path, "d5.db")
    try:
        idx = _range_indexes(proj)
        point = idx.get("Point", {})
        for f in ("id", "pointKind", "content_hash"):
            assert "RANGE" in point.get(f, []), \
                f"Point.{f} must be RANGE-indexed on {leg}: {point}"
        if leg == "embedded":
            assert "is_operator" not in point, \
                f"embedded must not index is_operator at all: {point}"
    finally:
        _drop_own_graph(proj)
        proj.close()


# ── D6: freshness composite index — MODE-SPLIT (L1244-1257) ───────────────


def test_d6_freshness_composite_mode_split(leg, tmp_path):
    """D6 — the REAL docker index divergence: server creates the composite
    (is_operator, lastDreamedAt); embedded gets the plain (lastDreamedAt)
    only — a composite containing is_operator is #522-unsafe on redislite
    (stale bool type table across reopen silently zeroes `= false` lookups)."""
    proj = _projection(leg, tmp_path, "d6.db")
    try:
        rows = proj.g.query("CALL db.indexes()").result_set
        point_rows = [r for r in rows if r[0] == "Point"]
        if leg == "embedded":
            assert any("lastDreamedAt" in str(r[1]) for r in point_rows), \
                f"embedded must create the plain lastDreamedAt index: {rows}"
            assert not any("is_operator" in str(r[1]) for r in point_rows), \
                f"embedded must NOT create an is_operator composite: {rows}"
        else:
            composite = [
                r for r in point_rows
                if "is_operator" in str(r[1]) and "lastDreamedAt" in str(r[1])
            ]
            assert composite, \
                f"server must create the (is_operator, lastDreamedAt) composite: {rows}"
    finally:
        _drop_own_graph(proj)
        proj.close()


# ── D7: embedded repair sweep (L1308) — embedded-only code path ───────────


def test_d7_embedded_repair_sweep(leg, tmp_path):
    """D7 — the repair sweep (drops every Point index containing is_operator,
    the #522 crash-reopen fix) runs ONLY on embedded reopen; on server the
    composite is CORRECT and must survive re-init untouched."""
    if leg == "embedded":
        db_path = str(tmp_path / "d7.db")
        # Seed the PRE-#522 stale state: a standalone is_operator RANGE index
        # (the historical build's persisted shape, per test_indexes #522).
        p1 = FalkorProjection(db_path)
        try:
            p1.g.query("CREATE INDEX FOR (n:Point) ON (n.is_operator)")
        finally:
            p1.close()
        # Reopen — the repair sweep must drop the stale index.
        p2 = FalkorProjection(db_path)
        try:
            rows = p2.g.query("CALL db.indexes()").result_set
            stale = [r for r in rows if r[0] == "Point"
                     and "is_operator" in str(r[1])]
            assert not stale, \
                f"embedded repair sweep must drop the stale is_operator index: {stale}"
        finally:
            p2.close()
        return
    # Server leg — the composite survives re-init (the sweep never runs).
    proj = FalkorProjection(host="localhost", port=6379, password="falkordb",
                            graph_name="test_d7_sweep")
    try:
        proj.g.query("MATCH (n) DETACH DELETE n")
        proj._ensure_indexes()
        proj.close()
        proj = None  # reopen below
        proj2 = FalkorProjection(host="localhost", port=6379, password="falkordb",
                                 graph_name="test_d7_sweep")
        try:
            rows = proj2.g.query("CALL db.indexes()").result_set
            composite = [
                r for r in rows if r[0] == "Point"
                and "is_operator" in str(r[1]) and "lastDreamedAt" in str(r[1])
            ]
            assert composite, \
                f"server must KEEP the composite across re-init: {rows}"
        finally:
            _drop_own_graph(proj2)  # review P2-1: fixed-name graph, not journaled
            proj2.close()
    finally:
        if proj is not None:
            proj.close()


# ── D8: HNSW vector index (L1497) — MODE-SPLIT ────────────────────────────


def test_d8_hnsw_vector_index(leg, tmp_path):
    """D8 — docker creates the HNSW vector index (recorded on
    _vector_index_api) and serves index-backed queries; embedded has NO vector
    index and serves brute-force vec.euclideanDistance ordering — EXACT
    (the documented degradation; pinned by bench/test_smoke_embedded)."""
    from tortoise.search_engine import run_vector_query

    proj = _projection(leg, tmp_path, "d8.db")
    try:
        _seed_embeddings(proj.g)
        if leg == "embedded":
            assert proj._vector_index_api is None, \
                "embedded must not create an HNSW vector index"
            hits = run_vector_query(proj.g, [1.0, 0.0, 0.0], limit=3,
                                    is_embedded=True, vector_index_api=None)
            assert [h[0] for h in hits] == ["p1", "p2", "p3"], \
                f"embedded brute-force ordering must be exact: {hits}"
        else:
            assert proj._vector_index_api in ("procedure", "cypher"), \
                f"server must record a vector-index API, got {proj._vector_index_api!r}"
            rows = proj.g.query("CALL db.indexes()").result_set
            assert any(r[0] == "Point" and "embedding" in str(r[1]) for r in rows), \
                f"server must create the Point.embedding vector index: {rows}"
            hits = run_vector_query(
                proj.g, [1.0, 0.0, 0.0], limit=3, is_embedded=False,
                vector_index_api=proj._vector_index_api)
            assert hits and hits[0][0] == "p1", \
                f"server HNSW must top-rank the near neighbor: {hits}"
    finally:
        _drop_own_graph(proj)
        proj.close()


# ── D9: cross-lens calibration (sdk.py L6766/6784 → run_vector_query) ─────


def test_d9_cross_lens_calibration(leg, tmp_path):
    """D9 — the SDK cross-lens surface: brute-force over the ENTIRE Point
    index on embedded (documented degradation, recall EXACT) vs HNSW-
    accelerated on docker (recall CAN differ — the documented divergence
    class; on this deterministic 3-point seed both lanes agree). The
    candidate similarity is the exact cosine recompute from STORED embeddings
    on both lanes (docker-calibrated band pinned here; the full calibration
    surface lives in tests/test_cross_lens.py)."""
    from tortoise.sdk import TortoiseSDK

    ns = f"test_d9_{os.urandom(4).hex()}"
    sdk = TortoiseSDK(str(tmp_path / "d9.db"), namespace=ns)
    try:
        proj = sdk._get_proj()
        _seed_cross_lens_graph(proj.g)
        res = sdk.get_cross_lens_candidates(threshold=0.7, top_k=10)
        by = {(c["src"], c["dst"]): c for c in res["candidates"]}
        assert ("p1", "p2") in by, \
            f"cross-vocab in-band pair p1-p2 must surface on {leg}: {res}"
        pair = by[("p1", "p2")]
        # Calibrated band: the exact cosine of the stored vectors (both lanes
        # recompute from stored embeddings — the ANN only bounds the pull).
        assert abs(pair["similarity"] - 0.9939) < 1e-3, \
            f"calibrated similarity drift on {leg}: {pair['similarity']}"
        if leg == "embedded":
            # Brute-force over the ENTIRE index — recall EXACT: the p1-p2
            # pair is the ONLY cross-lens candidate above the band.
            assert len(res["candidates"]) == 1, \
                f"embedded exact recall must see exactly the p1-p2 pair: {res}"
    finally:
        _drop_own_graph(sdk._get_proj())
        sdk.close()


# ── D10: retrieval pool-floor flag (sdk.py L9616 is_embedded → run_vector_query) ──


def test_d10_retrieval_pool_floor_flag(leg, tmp_path):
    """D10 — the retrieval path forwards is_embedded into run_vector_query on
    BOTH lanes (same API, no mode branch): a query against an embedded point
    returns it whether the vector leg is HNSW-accelerated (docker) or
    brute-force (embedded). Parity — the flag carries the D9 semantics, it
    does not change the surface contract."""
    from tortoise.sdk import TortoiseSDK

    vec = [0.0] * 384
    vec[0] = 1.0
    ns = f"test_d10_{os.urandom(4).hex()}"
    sdk = TortoiseSDK(str(tmp_path / "d10.db"), namespace=ns)
    try:
        proj = sdk._get_proj()
        proj.g.query(
            "CREATE (p:Point {id:'dp1', content:'vector search divergence "
            "parity', pointKind:'statement', is_operator:false, "
            "embedding: vecf32($v)})",
            params={"v": vec})
        res = sdk.tortoise_fts_query("vector search divergence parity",
                                     limit=5, pool_size=10)
        assert any(r.get("id") == "dp1" for r in res), \
            f"retrieval must surface the point on {leg}: {res}"
    finally:
        _drop_own_graph(sdk._get_proj())
        sdk.close()


# ── D11: EmbeddedStoreBusyError — embedded-only fail-fast (sdk.py L1058) ──


def test_d11_busy_error_embedded_only(leg, tmp_path):
    """D11 — the cross-process same-path busy fail-fast is EMBEDDED-ONLY:
    a live foreign holder raises EmbeddedStoreBusyError on embedded; the
    server has NO such concept — concurrent writers on one graph are legal
    (multi-tenant, last-writer-wins per op — test_concurrent_writers_live
    proves no lost writes)."""
    from tortoise.exceptions import EmbeddedStoreBusyError
    from tortoise.sdk import TortoiseSDK

    if leg == "embedded":
        db_path = str(tmp_path / "d11.db")
        # Fabricate the redislite pid registry: a LIVE holder (our own pid —
        # a live process the probe's os.kill(pid, 0) liveness check accepts).
        pidfile = str(tmp_path / "holder.pid")
        with open(pidfile, "w") as fh:
            fh.write(str(os.getpid()))
        with open(db_path + ".settings", "w") as fh:
            json.dump({"pidfile": pidfile}, fh)
        with pytest.raises(EmbeddedStoreBusyError):
            TortoiseSDK(db_path)
        return
    # Server leg: no busy concept — two URI-mode SDKs on one graph write
    # concurrently and both writes land.
    ns = f"test_d11_{os.urandom(4).hex()}"
    sdk1 = TortoiseSDK(namespace=ns)
    sdk2 = TortoiseSDK(namespace=ns)
    try:
        sdk1.create_point("statement", "d11-a")
        sdk2.create_point("statement", "d11-b")
        rows = sdk1._get_proj().g.query(
            "MATCH (n:Point) RETURN count(n)").result_set
        assert rows[0][0] == 2, \
            f"server multi-tenant writers must both land: {rows}"
    finally:
        _drop_own_graph(sdk1._get_proj())
        sdk1.close()
        sdk2.close()


# ── D12: concurrency semantics (conftest sdk_factory docstring L91-100) ───


def test_d12_concurrency_semantics(leg, tmp_path):
    """D12 — embedded: same-process SDKs on one path SHARE the daemon (the
    busy probe passes by construction; last-close-wins on the DB file is the
    documented SUBPROCESS class). Server: multi-connection-safe — one shared
    graph, writers coexist."""
    from tortoise.sdk import TortoiseSDK

    if leg == "embedded":
        db_path = str(tmp_path / "d12.db")
        sdk1 = TortoiseSDK(db_path)
        sdk2 = TortoiseSDK(db_path)  # same-process threads reuse the daemon
        try:
            sdk1.create_point("statement", "d12-a")
            sdk2.create_point("statement", "d12-b")
            rows = sdk1._get_proj().g.query(
                "MATCH (n:Point) RETURN count(n)").result_set
            assert rows[0][0] == 2, \
                f"same-process embedded SDKs must share one daemon: {rows}"
        finally:
            sdk1.close()
            sdk2.close()
        return
    ns = f"test_d12_{os.urandom(4).hex()}"
    sdk1 = TortoiseSDK(namespace=ns)
    sdk2 = TortoiseSDK(namespace=ns)
    try:
        sdk1.create_point("statement", "d12-a")
        sdk2.create_point("statement", "d12-b")
        rows = sdk1._get_proj().g.query(
            "MATCH (n:Point) RETURN count(n)").result_set
        assert rows[0][0] == 2, \
            f"server multi-connection writers must coexist: {rows}"
    finally:
        _drop_own_graph(sdk1._get_proj())
        sdk1.close()
        sdk2.close()


# ── D13: wipe() server refusal (tests/_embedded.py L56) ───────────────────


def test_d13_wipe_server_refusal(leg, tmp_path):
    """D13 — wipe() is the EMBEDDED hermeticity helper (all-graphs wipe) and
    REFUSES server mode; wipe_server() is the server variant (test_-prefixed,
    scope-limited, loopback-only). Each lane exercises ITS helper."""
    from tests._embedded import wipe, wipe_server

    proj = _projection(leg, tmp_path, "d13.db")
    try:
        proj.g.query("CREATE (p:Point {id:'w1', content:'w'})")
        if leg == "embedded":
            wipe(proj)  # works — embedded all-graphs wipe
            assert _point_count(proj) == 0
        else:
            with pytest.raises(RuntimeError, match="EMBEDDED test server only"):
                wipe(proj)
            wipe_server(proj, scope={proj.graph_name})
            assert _point_count(proj) == 0
    finally:
        _drop_own_graph(proj)
        proj.close()


# ── D14: hosted_api embedded fallback (hosted_api.py L78-119) ─────────────


def test_d14_hosted_make_sdk_fallback(leg, tmp_path):
    """D14 — _make_sdk: no URI → embedded fallback at TORTOISE_DB_PATH
    (the prod degraded path, keepalive-anchored); URI → TortoiseSDK(namespace=…)
    server mode. No divergence when the URI is set — each lane's SDK is
    provably its intended backend."""
    from tortoise.hosted_api import _FALLBACK_KEEPALIVE, _make_sdk

    ns = f"test_d14_{os.urandom(4).hex()}"
    sdk = _make_sdk(namespace=ns)
    proj = None
    try:
        proj = sdk._get_proj()
        if leg == "embedded":
            assert proj._is_embedded is True, \
                "no URI → hosted fallback must be embedded"
        else:
            assert proj._is_embedded is False, \
                "URI → hosted SDK must be server-mode"
    finally:
        if proj is not None:
            _drop_own_graph(proj)
        sdk.close()
        anchor = _FALLBACK_KEEPALIVE.pop(ns, None)
        if anchor is not None:
            try:  # noqa: SIM105
                anchor.close()
            except Exception:
                pass


# ── D15: atexit fast-close (embedded_lifecycle.atexit_fast_close) ─────────


def test_d15_atexit_fast_close(leg, tmp_path):
    """D15 — TORTOISE_FAST_ATEXIT=1 fire-and-forget SHUTDOWN NOSAVE applies
    ONLY to ephemeral test-tree redislite servers: embedded returns True
    (handled by the fast path); server-mode clients have no redislite server
    and the seam no-ops (False → caller falls through to normal close())."""
    from tortoise.embedded_lifecycle import atexit_fast_close

    proj = _projection(leg, tmp_path, "d15.db")
    try:
        client = getattr(proj.db, "client", proj.db)
        handled = atexit_fast_close(client)
        if leg == "embedded":
            assert handled is True, \
                "embedded ephemeral server must take the fast-close path"
        else:
            assert handled is False, \
                "server client must fall through to the normal close"
    finally:
        _drop_own_graph(proj)
        proj.close()


# ── D16: version probe (projection/__init__.py L1005-1060) ────────────────


def test_d16_version_probe(leg, tmp_path):
    """D16 — both engines satisfy the >= 4.x FTS/vector gate. Documented
    divergence from the research-brief row: the EMBEDDED probe returns None
    on this stack (redislite's MODULE LIST is dict-shaped; the probe's
    list-index parse skips it) — None is NOT a failure (index creation probes
    the engine directly, the <4 gate is skipped); docker resolves 4.x."""
    proj = _projection(leg, tmp_path, "d16.db")
    try:
        ver = proj._falkordb_version
        if leg == "embedded":
            assert ver is None or ver[0] >= 4, \
                f"embedded version must be None (undetermined) or >= 4.x: {ver}"
        else:
            assert ver is not None and ver[0] >= 4, \
                f"server version must resolve to >= 4.x: {ver}"
    finally:
        _drop_own_graph(proj)
        proj.close()
