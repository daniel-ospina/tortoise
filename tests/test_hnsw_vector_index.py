"""#247 — verify FalkorDB HNSW vector index auto-updates on embedding SET.

Question: when PointRevised changes ``n.embedding`` (SET), does the HNSW
vector index pick the new vector up automatically, or is a manual re-index
required?

FalkorDB maintains indexes transactionally on writes (engine-internal), but
this must be verified for the exact tortoise workflow: create Point with
embedding A → query near A → SET embedding to B → query near A (must not
find it top-ranked) and near B (must find it).

Requires live FalkorDB (Docker/server, not FalkorDBLite — HNSW needs the
RediSearch module). Skips gracefully when unavailable, mirroring
tests/test_integration_search.py.

Usage:
    TORTOISE_DB_URI=docker://localhost:6379/tortoise_hnsw pytest tests/test_hnsw_vector_index.py -v
"""
from __future__ import annotations

import os as _os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── FalkorDB availability check (mirrors test_integration_search.py) ──
FALKORDB_AVAILABLE = False
_uri_candidates = [
    _os.environ.get("TORTOISE_DB_URI"),
    "docker://localhost:6379/tortoise_hnsw",
    "docker://localhost:16379/tortoise_hnsw",
]

_old_uri = _os.environ.get("TORTOISE_DB_URI")
_working_uri: str | None = None
try:
    for _uri in _uri_candidates:
        if not _uri:
            continue
        try:
            from tortoise.sdk import TortoiseSDK
            _os.environ["TORTOISE_DB_URI"] = _uri
            _sdk = TortoiseSDK()
            try:
                _proj = _sdk._get_proj()
                _proj.g.query("RETURN 1")
                _is_embedded = getattr(_proj, "_is_embedded", True)
                _ver = getattr(_proj, "_falkordb_version", None)
                # HNSW vector index requires FalkorDB >= 4.x (mirrors
                # projection._ensure_indexes). Older servers skip cleanly.
                hnsw_capable = _ver is None or _ver[0] >= 4
                if not _is_embedded and hnsw_capable:
                    FALKORDB_AVAILABLE = True
                    _working_uri = _uri
                break
            finally:
                _sdk.close()
        except Exception:
            continue
except Exception:
    pass
finally:
    # Always restore the original env so no fixed graph name leaks into the session.
    if _old_uri is None:
        _os.environ.pop("TORTOISE_DB_URI", None)
    else:
        _os.environ["TORTOISE_DB_URI"] = _old_uri

DIM = 384  # matches projection's vector index dimension


def _ensure_vector_index(g, label: str) -> None:
    """Create the HNSW vector index, tolerating engine API drift.

    FalkorDB < 8.x registers the RediSearch-style procedure
    ``db.idx.vector.createNodeIndex``; 8.x+ moved index creation to the
    Cypher-native ``CREATE VECTOR INDEX`` form (mirrors the fallback in
    projection._ensure_indexes, #1359). ``db.idx.vector.queryNodes`` still
    works on both, so only the creation call needs the dual path.
    """
    try:
        g.query(
            f"CALL db.idx.vector.createNodeIndex('{label}', 'embedding', {DIM}, 'HNSW')"
        )
    except Exception as e:
        if "already" in str(e).lower():
            return
        # Fallback: FalkorDB 8.x Cypher-native form. Tolerate "already
        # indexed" here too (the SDK's _ensure_indexes may have created it
        # at connection time).
        try:
            g.query(
                f"CREATE VECTOR INDEX FOR (n:{label}) ON (n.embedding) "
                "OPTIONS {dimension: 384, similarityFunction: 'cosine'}"
            )
        except Exception as e2:
            if "already" not in str(e2).lower():
                raise


def _vec(vals: list[float]) -> list[float]:
    """Build a 384-dim one-hot-ish vector from (index, value) pairs."""
    v = [0.0] * DIM
    for i, val in vals:
        v[i] = val
    return v


@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="Live FalkorDB (server mode) not available")
class TestHnswAutoUpdate:
    """#247 — HNSW index reflects embedding updates without manual re-index."""

    @pytest.fixture(autouse=True)
    def _graph(self):
        from tortoise.sdk import TortoiseSDK
        # Point every test at the probe-verified server (never the session default).
        if _working_uri is not None:
            _os.environ["TORTOISE_DB_URI"] = _working_uri
        sdk = TortoiseSDK()
        proj = sdk._get_proj()
        g = proj.g
        # Isolate: clear this test's nodes, ensure the vector index exists.
        g.query("MATCH (n:Point) WHERE n.id STARTS WITH 'hnsw247_' DETACH DELETE n")
        _ensure_vector_index(g, "Point")
        yield g
        g.query("MATCH (n:Point) WHERE n.id STARTS WITH 'hnsw247_' DETACH DELETE n")
        sdk.close()
        if _old_uri is None:
            _os.environ.pop("TORTOISE_DB_URI", None)
        else:
            _os.environ["TORTOISE_DB_URI"] = _old_uri

    def _create(self, g, pid: str, vec: list[float]):
        g.query(
            "CREATE (n:Point {id: $id, content: $content, embedding: vecf32($vec)})",
            params={"id": pid, "content": pid, "vec": vec},
        )

    def _query(self, g, vec: list[float], k: int = 5) -> list[str]:
        rows = g.query(
            f"CALL db.idx.vector.queryNodes('Point', 'embedding', {k}, vecf32($vec)) "
            "YIELD node, score RETURN node.id, score",
            params={"vec": vec},
        ).result_set
        return [(r[0], float(r[1])) for r in rows]

    def test_new_embedding_is_found(self, _graph):
        """Baseline: node with embedding A is returned when querying near A."""
        g = _graph
        vec_a = _vec([(0, 1.0)])
        self._create(g, "hnsw247_a", vec_a)

        hits = self._query(g, _vec([(0, 0.9)]))
        assert any(hid == "hnsw247_a" for hid, _ in hits), \
            f"expected hnsw247_a in hits, got {hits}"

    def test_set_new_embedding_reflects_in_index(self, _graph):
        """CORE: SET n.embedding = B (far from A) -> index serves B immediately.

        This is the exact workflow projection uses on PointRevised. If the
        HNSW index did NOT auto-update, the stale A vector would keep
        matching queries near A and miss queries near B.

        Score semantics: queryNodes YIELD score is a DISTANCE (cosine
        distance = 1 - cos, [0, 2]; smaller = closer). With one vector in the
        index, KNN returns it for any query — so the assertions compare the
        DISTANCE before vs after the SET, not top-k membership.
        """
        g = _graph
        vec_a = _vec([(0, 1.0)])     # cluster at dim 0
        vec_b = _vec([(100, 1.0)])   # far away, cluster at dim 100
        self._create(g, "hnsw247_mut", vec_a)

        def _dist(query_vec) -> float:
            hits = self._query(g, query_vec)
            for hid, score in hits:
                if hid == "hnsw247_mut":
                    return score
            return 2.0  # absent == maximally far

        # Sanity (distance semantics): near-A is CLOSE (< 0.5), near-B is FAR.
        assert _dist(_vec([(0, 0.9)])) < 0.5, "vec_a should be close to near-A"
        assert _dist(_vec([(100, 0.9)])) >= 0.5, "vec_a should be far from near-B"

        # The PointRevised update path: SET a new embedding on the same node.
        g.query(
            "MATCH (n:Point {id: $id}) SET n.embedding = vecf32($vec)",
            params={"id": "hnsw247_mut", "vec": vec_b},
        )

        # After the SET the index serves the NEW vector: near-B is now CLOSE
        # and near-A is now FAR. A stale index would report the opposite.
        assert _dist(_vec([(100, 0.9)])) < 0.5, \
            "new embedding not served by index after SET (near-B still far)"
        assert _dist(_vec([(0, 0.9)])) >= 0.5, \
            "stale vector still served near-A after SET (index did not update)"

    def test_unindexed_embedding_updates_stay_queryable_after_recreate(self, _graph):
        """Documentation of behavior: nodes created BEFORE index creation are
        not served by the index until re-written (index is write-maintained).
        This is the known caveat: CREATE the index first, then write nodes."""
        g = _graph
        # Node created BEFORE the index existed (simulate by using a label the
        # index doesn't cover — Document has no vector index by default).
        g.query(
            "CREATE (n:Document {id: $id, name: 'pre', embedding: vecf32($vec)})",
            params={"id": "hnsw247_pre", "vec": _vec([(0, 1.0)])},
        )
        _ensure_vector_index(g, "Document")
        hits = g.query(
            f"CALL db.idx.vector.queryNodes('Document', 'embedding', 5, vecf32($vec)) "
            "YIELD node, score RETURN node.id",
            params={"vec": _vec([(0, 0.9)])},
        ).result_set
        # PIN the caveat: on FalkorDB < 8.x a vector written BEFORE index
        # creation is NOT served by the index (index is write-maintained from
        # creation time). 8.x+ serves pre-index vectors immediately (engine
        # improvement) — either behavior is acceptable; the invariant that
        # MUST hold is that a re-write keeps the vector queryable.
        pre_served = any(r[0] == "hnsw247_pre" for r in hits)
        try:
            # A re-write makes it queryable.
            g.query(
                "MATCH (n:Document {id: $id}) SET n.embedding = vecf32($vec)",
                params={"id": "hnsw247_pre", "vec": _vec([(0, 1.0)])},
            )
            hits2 = g.query(
                f"CALL db.idx.vector.queryNodes('Document', 'embedding', 5, vecf32($vec)) "
                "YIELD node, score RETURN node.id",
                params={"vec": _vec([(0, 0.9)])},
            ).result_set
            assert any(r[0] == "hnsw247_pre" for r in hits2), \
                f"re-write should make vector queryable, got {hits2}"
        finally:
            g.query("MATCH (n:Document {id: $id}) DETACH DELETE n",
                    params={"id": "hnsw247_pre"})
