"""#244 semantic session search — index-time embeddings + hybrid routing.

Covers:
  - compute_session_embedding: 384-dim from name+summary+keywords+topics,
    graceful None when the model is unavailable / text is empty
  - ingest_corpus AgentSession branch stores a vecf32 embedding on the Event
    when the model is available, and stores none (graceful) when it isn't
  - search_sessions routes through the hybrid engine (vector strategy active)
    and still returns relevant results, preserving agent/topics/after/before
    filters + relevance ordering with startedAt tiebreak
  - legacy keyword CONTAINS surface preserved when no FTS/vector strategy
    contributes (embedded FalkorDBLite without embeddings)

Uses TortoiseSDK(file_path) for embedded FalkorDBLite (no Docker needed).
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tortoise.embeddings import EmbeddingModel   # noqa: E402
from tortoise.sdk import TortoiseSDK             # noqa: E402


@pytest.fixture(autouse=True)
def _use_shared_embedded_db(shared_embedded_db):
    pass


def _fresh_sdk():
    """SDK backed by a fresh, isolated embedded FalkorDBLite instance."""
    import tempfile as _tf
    db_path = os.path.join(_tf.mkdtemp(prefix="tortoise_semsess_"), "test.db")
    sdk = TortoiseSDK(db_path)
    # Wipe before use — hermeticity comes from the wipe, not a fresh path.
    try:
        sdk._get_proj().g.query("MATCH (n) DETACH DELETE n")
    except Exception:
        pass
    return sdk


# ── Fake embedder (deterministic, no sentence-transformers needed) ────────

_CONCEPT_DIMS = {"port": 0, "migration": 1, "falkordb": 2, "cookie": 3, "baking": 4}


def _fake_text_vec(text: str) -> list[float]:
    """384-dim deterministic pseudo-embedding: concept dims mark presence."""
    v = [0.0] * 384
    tl = str(text).lower()
    for token, dim in _CONCEPT_DIMS.items():
        if token in tl:
            v[dim] = 1.0
    return v


class _FakeEmbedder:
    def encode(self, texts, batch_size=32, show_progress_bar=False):
        return np.array([_fake_text_vec(t) for t in texts], dtype=np.float64)


# ── Helpers ───────────────────────────────────────────────────────────────

def _index_session_raw(sdk, event_id: str, name: str, *,
                       agent: str = "pi", keywords: list[str] | None = None,
                       topics: list[str] | None = None,
                       started_at: str | None = None) -> None:
    """Raw CREATE of an AgentSession Event WITHOUT an embedding (legacy shape)."""
    props = {
        "eventId": event_id,
        "eventKind": "AgentSession",
        "name": name,
        "session_id": f"s-{event_id}",
        "agent": agent,
        "keywords": keywords or [],
        "topics": topics or ["general"],
        "eventStatus": "completed",
        "classificationLevel": "internal",
    }
    if started_at is not None:
        props["startedAt"] = started_at
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (e:Event {eventId: $eid}) SET e += $props",
        params={"eid": event_id, "props": props},
    )


def _index_session_with_embedding(sdk, event_id: str, name: str, *,
                                  agent: str = "pi", keywords: list[str] | None = None,
                                  topics: list[str] | None = None,
                                  started_at: str | None = None) -> None:
    """Index an AgentSession Event the same way ingest_corpus does (#244):
    compute the session embedding and store as vecf32 on the Event."""
    from tortoise.session_indexer import compute_session_embedding
    keywords = keywords or []
    topics = topics or ["general"]
    emb = compute_session_embedding(name, name, keywords, topics)
    props = {
        "eventId": event_id,
        "eventKind": "AgentSession",
        "name": name,
        "session_id": f"s-{event_id}",
        "agent": agent,
        "keywords": keywords,
        "topics": topics,
        "eventStatus": "completed",
        "classificationLevel": "internal",
    }
    if started_at is not None:
        props["startedAt"] = started_at
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (e:Event {eventId: $eid}) SET e += $props, "
        "e.embedding = CASE WHEN $embedding IS NOT NULL THEN vecf32($embedding) END",
        params={"eid": event_id, "props": props, "embedding": emb},
    )


def _ids(sessions: list[dict]) -> list[str]:
    return [s.get("eventId") or s.get("session_id") for s in sessions]


def _write_session_md(directory: str, filename: str, body: str) -> str:
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, filename)
    with open(path, "w") as f:
        f.write(body)
    return path


# ── 1. compute_session_embedding unit behavior ─────────────────────────────

class TestComputeSessionEmbedding:
    def test_returns_384_dim_when_model_available(self):
        from tortoise.session_indexer import compute_session_embedding
        with patch.object(EmbeddingModel, "get", return_value=_FakeEmbedder()):
            emb = compute_session_embedding(
                "FalkorDB port migration", "changed default port",
                ["migration"], ["infrastructure"],
            )
            assert emb is not None
            assert len(emb) == 384

    def test_returns_none_when_model_unavailable(self):
        from tortoise.session_indexer import compute_session_embedding
        with patch.object(EmbeddingModel, "get", return_value=None):
            assert compute_session_embedding("any text") is None

    def test_returns_none_for_empty_text(self):
        from tortoise.session_indexer import compute_session_embedding
        with patch.object(EmbeddingModel, "get", return_value=_FakeEmbedder()):
            assert compute_session_embedding("") is None
            assert compute_session_embedding(" ", "", [], []) is None

    def test_text_composition_dedups(self):
        from tortoise.session_indexer import session_embedding_text
        text = session_embedding_text("Title", "Title", ["kw", "kw"], ["topic"])
        assert text == "Title kw topic"


# ── 2. Index-time embedding stored on the Event ────────────────────────────

class TestIndexTimeEmbedding:
    def _ingest(self, sdk, directory, body):
        _write_session_md(directory, "sess1.md", body)
        return sdk.ingest_corpus(directory, eventKind="AgentSession",
                                 extract_metadata=False)

    def test_embedding_stored_when_model_available(self):
        sdk = _fresh_sdk()
        try:
            with patch.object(EmbeddingModel, "get", return_value=_FakeEmbedder()):
                result = self._ingest(sdk, tempfile.mkdtemp(), _session_md())
            assert result["ingested"] == 1, result
            props = sdk._get_proj().g.query(
                "MATCH (e:Event) RETURN properties(e)"
            ).result_set[0][0]
            assert props.get("eventKind") == "AgentSession"
            emb = props.get("embedding")
            assert emb is not None, "embedding must be stored at index time"
            assert len(emb) == 384
        finally:
            sdk.close()

    def test_no_embedding_when_model_unavailable_graceful(self):
        sdk = _fresh_sdk()
        try:
            with patch.object(EmbeddingModel, "get", return_value=None):
                result = self._ingest(sdk, tempfile.mkdtemp(), _session_md())
            assert result["ingested"] == 1, result
            props = sdk._get_proj().g.query(
                "MATCH (e:Event) RETURN properties(e)"
            ).result_set[0][0]
            # Graceful degradation: no embedding property, no crash.
            assert props.get("embedding") is None
        finally:
            sdk.close()


def _session_md() -> str:
    return """---
title: FalkorDB port migration
tags: [migration, falkordb]
domain: infrastructure
---
## User
Change the FalkorDB default port from 6379 to 16379.
## Assistant
Done — migrated the port in config and restarted the server.
"""


# ── 3. search_sessions hybrid route + filters ──────────────────────────────

_JUL_01 = "2026-07-01T10:00:00+00:00"
_JUL_15 = "2026-07-15T12:30:00+00:00"
_JUL_31 = "2026-07-31T18:45:00+00:00"


class TestSearchSessionsHybrid:
    """search_sessions with stored embeddings → vector strategy active."""

    def _setup(self, sdk):
        with patch.object(EmbeddingModel, "get", return_value=_FakeEmbedder()):
            _index_session_with_embedding(
                sdk, "s1", "FalkorDB port migration", agent="pi",
                keywords=["port", "migration"], topics=["infrastructure"],
                started_at=_JUL_01)
            _index_session_with_embedding(
                sdk, "s2", "Cookie baking recipe", agent="pi",
                keywords=["baking"], topics=["food"], started_at=_JUL_15)
            _index_session_with_embedding(
                sdk, "s3", "FalkorDB default port change", agent="opine",
                keywords=["falkordb", "port"], topics=["data"],
                started_at=_JUL_31)

    def _search(self, sdk, query, **kw):
        # Model must be available at QUERY time too (indexing and searching
        # share the same embedding model in production).
        with patch.object(EmbeddingModel, "get", return_value=_FakeEmbedder()):
            return sdk.search_sessions(query, **kw)

    def test_semantic_match_ranks_relevant_first(self):
        sdk = _fresh_sdk()
        try:
            self._setup(sdk)
            # Word-distinct query: "migration" never appears in the query,
            # yet the port-migration session surfaces ahead of the cookie one.
            res = self._search(sdk, "changing the database default port", limit=3)
            ids = _ids(res)
            assert ids[0] in ("s1", "s3"), ids
            assert "s2" in ids and ids[-1] == "s2", ids
        finally:
            sdk.close()

    def test_agent_filter_preserved(self):
        sdk = _fresh_sdk()
        try:
            self._setup(sdk)
            res = self._search(sdk, "port", agent="pi", limit=5)
            ids = _ids(res)
            assert all(r["agent"] == "pi" for r in res)
            assert "s1" in ids and "s3" not in ids
        finally:
            sdk.close()

    def test_topics_filter_preserved(self):
        sdk = _fresh_sdk()
        try:
            self._setup(sdk)
            res = self._search(sdk, "port", topics=["data"], limit=5)
            ids = _ids(res)
            assert ids and ids[0] == "s3", ids
        finally:
            sdk.close()

    def test_after_before_filters_preserved(self):
        sdk = _fresh_sdk()
        try:
            self._setup(sdk)
            # after: s1 (Jul 1) excluded, s3 is the best port match in window
            res = self._search(sdk, "port", after="2026-07-10T00:00:00Z", limit=5)
            ids = _ids(res)
            assert "s1" not in ids, ids
            assert ids[0] == "s3", ids
            # datetime bound with timezone offset (PDT = UTC-7 → Aug 1 06:00Z)
            before = datetime(2026, 7, 31, 23, 0, tzinfo=timezone(timedelta(hours=-7)))
            res = self._search(sdk, "port", before=before, limit=5)
            ids = _ids(res)
            assert "s3" in ids and "s1" in ids, ids
        finally:
            sdk.close()

    def test_relevance_order_with_limit(self):
        sdk = _fresh_sdk()
        try:
            self._setup(sdk)
            res = self._search(sdk, "port migration", limit=1)
            assert _ids(res) == ["s1"], res
        finally:
            sdk.close()

    def test_offset_pagination(self):
        sdk = _fresh_sdk()
        try:
            self._setup(sdk)
            page1 = self._search(sdk, "port", limit=2, offset=0)
            page2 = self._search(sdk, "port", limit=2, offset=2)
            assert not set(_ids(page1)) & set(_ids(page2))
        finally:
            sdk.close()


class TestSearchSessionsLegacyFallback:
    """No embeddings + no FTS token match → keyword CONTAINS fallback.

    NOTE: on a fresh embedded DB the Event FTS index now covers name (#244),
    so these tests search via the keywords array (NOT FTS-indexed) — that
    guarantees no FTS contribution and exercises the legacy fallback.
    """

    def test_keyword_surface_preserved(self):
        sdk = _fresh_sdk()
        try:
            _index_session_raw(sdk, "k1", "session alpha", keywords=["port", "migration"], started_at=_JUL_01)
            _index_session_raw(sdk, "k2", "session beta", keywords=["cookie"], started_at=_JUL_15)
            # Partial-word CONTAINS on the keywords array still works
            # without any semantic strategy.
            res = sdk.search_sessions("migra")
            assert _ids(res) == ["k1"], res
            # Exact keyword token match.
            _index_session_raw(sdk, "k3", "session gamma", keywords=["zebra-cluster"], started_at=_JUL_31)
            res = sdk.search_sessions("zebra-cluster")
            assert _ids(res) == ["k3"], res
        finally:
            sdk.close()

    def test_temporal_filters_in_fallback(self):
        sdk = _fresh_sdk()
        try:
            _index_session_raw(sdk, "k1", "session alpha", keywords=["samekw"], started_at=_JUL_01)
            _index_session_raw(sdk, "k2", "session beta", keywords=["samekw"], started_at=_JUL_15)
            _index_session_raw(sdk, "k3", "session gamma", keywords=["samekw"], started_at=None)
            # after: k1 (Jul 1) before the bound; k3 has no startedAt → excluded
            res = sdk.search_sessions("samekw", after="2026-07-10T00:00:00Z")
            assert _ids(res) == ["k2"], res
            # ...but the no-timestamp session appears in unbounded results
            res = sdk.search_sessions("samekw")
            assert "k3" in _ids(res), res
        finally:
            sdk.close()

    def test_agent_and_topics_filters_in_fallback(self):
        sdk = _fresh_sdk()
        try:
            _index_session_raw(sdk, "k1", "session alpha", agent="pi",
                               keywords=["kwalpha"], topics=["infrastructure"], started_at=_JUL_01)
            _index_session_raw(sdk, "k2", "session beta", agent="opine",
                               keywords=["kwalpha"], topics=["data"], started_at=_JUL_15)
            res = sdk.search_sessions("kwalpha", agent="pi", limit=5)
            assert _ids(res) == ["k1"], res
            res = sdk.search_sessions("kwalpha", topics=["data"], limit=5)
            assert _ids(res) == ["k2"], res
        finally:
            sdk.close()


# ── 5. Review regressions (#244 plan-review) ───────────────────────────────

def _index_session_plain_list_embedding(sdk, event_id: str, name: str) -> None:
    """Simulate the pre-#244 _upsert_event shape: embedding stored as a PLAIN
    Python list (not vecf32). One such node poisons brute-force vector search
    for the whole Event label (vec.euclideanDistance rejects List)."""
    vec = [0.1] * 384
    props = {
        "eventId": event_id,
        "eventKind": "AgentSession",
        "name": name,
        "session_id": f"s-{event_id}",
        "keywords": [], "topics": ["general"],
    }
    proj = sdk._get_proj()
    proj.g.query(
        "CREATE (e:Event {eventId: $eid}) SET e += $props, e.embedding = $vec",
        params={"eid": event_id, "props": props, "vec": vec},
    )


class TestReviewRegressions:
    def test_legacy_plain_list_embedding_degrades_gracefully(self):
        """A pre-#244 plain-list Event embedding must not crash Event vector
        search — the brute-force query wraps vecf32 and the poison node's
        label-wide failure is caught, so FTS/semantic search still works."""
        sdk = _fresh_sdk()
        try:
            with patch.object(EmbeddingModel, "get", return_value=_FakeEmbedder()):
                from tortoise.session_indexer import compute_session_embedding
                emb = compute_session_embedding(
                    "FalkorDB port migration", "changed the default port",
                    ["migration"], ["infrastructure"])
                assert emb is not None
                _index_session_with_embedding(sdk, "s1", "FalkorDB port migration",
                                              keywords=["migration"])
                _index_session_plain_list_embedding(sdk, "s_legacy", "legacy poison node")
                # Semantic route must still return the matching session and
                # must NOT include the unrelated legacy node.
                res = sdk.search_sessions("port migration", limit=5)
                ids = _ids(res)
                assert "s1" in ids, res
                assert "s_legacy" not in ids, res
        finally:
            sdk.close()

    def test_repair_legacy_embeddings_rewrites_plain_lists(self):
        """--repair-embeddings rewrites plain-list Event embeddings to vecf32
        (idempotent — second run counts them as already-migrated)."""
        import importlib.util as _ilu
        _spec = _ilu.spec_from_file_location(
            "backfill_embeddings",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "graph-scripts", "backfill_embeddings.py"),
        )
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        sdk = _fresh_sdk()
        try:
            _index_session_plain_list_embedding(sdk, "legacy1", "old session")
            proj = sdk._get_proj()
            # Repair (dry-run first: reports, writes nothing)
            _mod._repair_legacy_event_embeddings(proj.g, dry_run=True, limit=0)
            row = proj.g.query(
                "MATCH (n:Event {eventId:'legacy1'}) RETURN n.embedding"
            ).result_set
            assert isinstance(row[0][0], list), "dry-run must not rewrite"
            _mod._repair_legacy_event_embeddings(proj.g, dry_run=False, limit=0)
            # After repair, brute-force vector distance works (no type error).
            import random
            qv = [random.random() for _ in range(384)]
            dist = proj.g.query(
                "MATCH (n:Event {eventId:'legacy1'}) "
                "WITH vecf32($qv) AS _qv, n "
                "RETURN vec.euclideanDistance(n.embedding, _qv)",
                params={"qv": qv},
            ).result_set
            assert dist and isinstance(dist[0][0], float), dist
        finally:
            sdk.close()

    def test_hybrid_precision_gate_excludes_structural_only_sessions(self):
        """Rows with NO semantic signal (no FTS, no vector — e.g. a session
        with no embedding and no keyword overlap) must NOT be returned. The
        kind-filtered structural strategy matches every AgentSession event at
        score 1.0; without the gate those would leak into every result set.
        Vector-only rows are intentionally kept (word-distinct semantic recall
        is the #244 point — documented in the docstring)."""
        sdk = _fresh_sdk()
        try:
            with patch.object(EmbeddingModel, "get", return_value=_FakeEmbedder()):
                _index_session_with_embedding(sdk, "s1", "FalkorDB port migration",
                                              keywords=["migration"])
                # No embedding, no keyword overlap → structural-only row.
                _index_session_raw(sdk, "s_unrelated", "quantum espresso tuning",
                                   keywords=["coffee"])
                res = sdk.search_sessions("port migration", limit=10)
                ids = _ids(res)
                assert "s1" in ids, res
                assert "s_unrelated" not in ids, f"structural-only session leaked: {ids}"
        finally:
            sdk.close()

    def test_reindex_preserves_embedding_when_model_unavailable(self):
        """Re-indexing an existing session with the model unavailable must keep
        the stored vecf32 embedding (ON MATCH ... ELSE e.embedding)."""
        sdk = _fresh_sdk()
        try:
            with patch.object(EmbeddingModel, "get", return_value=_FakeEmbedder()):
                _index_session_with_embedding(sdk, "s1", "FalkorDB port migration")
            proj = sdk._get_proj()
            before = proj.g.query(
                "MATCH (n:Event {eventId:'s1'}) RETURN n.embedding IS NOT NULL"
            ).result_set
            assert before[0][0] is True
            # Re-index via the ingest path with the model unavailable.
            with patch.object(EmbeddingModel, "get", return_value=None):
                from tortoise.session_indexer import compute_session_embedding
                assert compute_session_embedding("FalkorDB port migration") is None
                _index_session_with_embedding(sdk, "s1", "FalkorDB port migration")
            after = proj.g.query(
                "MATCH (n:Event {eventId:'s1'}) RETURN n.embedding IS NOT NULL"
            ).result_set
            assert after[0][0] is True, "embedding wiped on re-index with model unavailable"
        finally:
            sdk.close()
