"""In-memory degraded-fallback corpus snapshot (#1375).

The degraded path (all retrieval strategies failed → in-memory TF-IDF) used to
re-fetch the whole corpus with FULL payloads via ``self.query`` (~350ms-2s on
Docker for ~1000 points) and re-encode/re-fit per call (8-700ms variable).
This module keeps a LEAN projection (id/content/pointKind/status) plus cached
document vectors, invalidated by a dirty flag on the write surfaces
(``_mark_dirty`` hook — one hook covers create/update/supersede/retract/
operator/mitigation/delete/ingest/dream) with a LAZY TTL backstop (age check
at read — zero steady-state cost; logs when it fires while clean, signalling
a write bypassed the normal surfaces).

Vector parity: when an embedding model is present, doc vectors come from
``model.encode(texts)`` (cached) and the query is encoded per call — same
model, same texts, so rankings match the legacy neural path (the batch-vs-
separate encode caveat is documented; the sklearn path is byte-identical to
legacy per the #399 fit-on-docs/transform-query contract and is what the
parity test verifies).

Mirrors the retrieval layer's exclusions: non-operators, retracted and
``outdated=true`` points are excluded at build; terminal-status points
(retracted/superseded/outdated/archived/deprecated) are filtered at serve
unless ``include_terminal`` (the #1391 contract); ``kind`` and
``exclude_status`` compose on top.

Size cap: above MAX_CORPUS_POINTS the snapshot is skipped (hosted OOM
protection) and the legacy path runs unchanged.
"""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

MAX_CORPUS_POINTS = 50_000
SNAPSHOT_TTL_SECONDS = 60.0

# Holds ALL non-operator points (incl. terminal statuses + outdated flag);
# the serve-time exclusion in search_snapshot mirrors self.query's two-mode
# semantics (#1391: terminal statuses + outdated excluded unless include_terminal).
_SNAPSHOT_QUERY = (
    "MATCH (n:Point) "
    "WHERE (n.is_operator = false OR n.is_operator IS NULL) "
    "RETURN n.id, n.content, n.pointKind, n.status, coalesce(n.outdated, false)"
)


class FallbackSnapshotStore:
    """Thread-safe snapshot store keyed by (graph_name, namespace)."""

    def __init__(self) -> None:
        self._store: dict[tuple, dict] = {}
        self._lock = threading.RLock()

    def invalidate(self, key: tuple) -> None:
        with self._lock:
            self._store.pop(key, None)

    def get(self, key: tuple) -> dict | None:
        """Fresh snapshot or None (missing / dirty / TTL-expired)."""
        now = time.monotonic()
        with self._lock:
            snap = self._store.get(key)
            if snap is None:
                return None
            if snap.get("dirty"):
                del self._store[key]
                return None
            if now - snap["built_at"] > SNAPSHOT_TTL_SECONDS:
                logger.warning(
                    "Fallback snapshot %r TTL-fired while clean — a write may "
                    "have bypassed the normal write surfaces", key,
                )
                del self._store[key]
                return None
            return snap

    def put(self, key: tuple, snap: dict) -> None:
        with self._lock:
            self._store[key] = snap

    def clear(self) -> None:
        with self._lock:
            self._store.clear()


_store = FallbackSnapshotStore()


def snapshot_key(proj, namespace: str | None) -> tuple:
    return (getattr(proj, "graph_name", "tortoise"), namespace)


def build_snapshot(proj) -> dict | None:
    """Lean corpus projection + cached document vectors. None if too big."""
    g = proj.g
    try:
        count = g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator = false OR n.is_operator IS NULL) "
            "RETURN count(n)",
        ).result_set[0][0]
        if int(count) > MAX_CORPUS_POINTS:
            logger.info(
                "Fallback snapshot skipped — corpus %d > cap %d",
                int(count), MAX_CORPUS_POINTS,
            )
            return None
        rows = g.query(_SNAPSHOT_QUERY).result_set
        points = [
            {"id": r[0], "content": r[1] or "", "pointKind": r[2] or "",
             "status": r[3] or "", "outdated": bool(r[4])}
            for r in rows
        ]
    except Exception as e:  # noqa: BLE001 — degraded path never crashes
        logger.warning("Fallback snapshot build query failed: %s", e)
        return None

    vectorizer = doc_vecs = None
    model_id = None
    texts = [p["content"] for p in points]
    try:
        from tortoise.embeddings import EmbeddingModel
        model = EmbeddingModel.get()
        if model is not None:
            import numpy as np
            doc_vecs = np.asarray(
                model.encode(texts, show_progress_bar=False), dtype=np.float64,
            )
            model_id = getattr(model, "model_id", "embedding-model")
    except Exception as e:  # noqa: BLE001 — fall back to sklearn
        logger.info("Fallback snapshot model encoding unavailable: %s", e)

    if doc_vecs is None:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            tv = TfidfVectorizer()
            # Keep the sparse matrix — densify only the served slice (P2: the
            # dense 50k × vocab array is an OOM risk; csr stays lean).
            doc_vecs = tv.fit_transform(texts)
            vectorizer = tv
        except Exception as e:  # noqa: BLE001 — vectors optional; scorer falls back
            logger.info("Fallback snapshot vectorization unavailable (sklearn): %s", e)

    return {
        "built_at": time.monotonic(),
        "dirty": False,
        "points": points,
        "vectorizer": vectorizer,
        "doc_vecs": doc_vecs,
        "model_id": model_id,
    }


def _encode_query(query: str, snap: dict):
    """Encode the query the way the corpus was encoded (model or sklearn)."""
    if snap.get("model_id"):
        from tortoise.embeddings import EmbeddingModel
        model = EmbeddingModel.get()
        if model is not None:
            import numpy as np
            return np.asarray(model.encode([query], show_progress_bar=False),
                              dtype=np.float64)[0]
        return None
    if snap.get("vectorizer") is not None:
        import numpy as np
        return np.asarray(snap["vectorizer"].transform([query]).toarray()[0],
                          dtype=np.float64)
    return None


def search_snapshot(
    query: str,
    snap: dict,
    *,
    limit: int = 10,
    kind: str | None = None,
    exclude_status: list[str] | None = None,
    include_terminal: bool = False,
    threshold: float = 0.0,
) -> list[dict]:
    """Score the snapshot corpus against ``query``.

    Returns the SAME shape as ``search_engine.fallback_tfidf`` (a list of
    SearchResult.to_dict() with match_source="tfidf"). Mirrors the legacy
    fallback semantics: ``kind`` (pointKind equality), terminal-status
    exclusion unless ``include_terminal`` (#1391), and ``exclude_status``
    compose. When no cached vectors exist, delegates to the legacy scorer.
    """
    import numpy as np

    from tortoise.embeddings import search_points
    from tortoise.search_engine import (
        SearchResult, SearchScores, TERMINAL_EXCLUDED_STATUSES,
    )

    points = snap["points"]
    mask = None

    def _filter(pred) -> None:
        nonlocal points, mask
        idx = [i for i, p in enumerate(points) if pred(p)]
        points = [points[i] for i in idx]
        mask = idx if mask is None else [mask[i] for i in idx]

    if kind:
        _filter(lambda p: p["pointKind"] == kind)
    if not include_terminal:
        # #1391 two-mode semantics: terminal statuses AND the legacy outdated
        # flag are excluded unless include_terminal surfaces them.
        _term = set(TERMINAL_EXCLUDED_STATUSES)
        _filter(lambda p: p["status"] not in _term and not p.get("outdated"))
    if exclude_status:
        _ex = set(exclude_status)
        _filter(lambda p: p["status"] not in _ex)
    if not points:
        return []

    try:
        query_vec = _encode_query(query, snap)
    except Exception:  # noqa: BLE001 — degraded path never crashes (#1375 R2)
        query_vec = None
    scored: list[dict] = []
    vectors_failed = False
    if query_vec is not None and snap["doc_vecs"] is not None:
        try:
            doc_vecs = snap["doc_vecs"]
            if mask is not None:
                doc_vecs = doc_vecs[mask] if hasattr(doc_vecs, "__getitem__") else doc_vecs
            # csr slice stays sparse; densify only what we serve
            if hasattr(doc_vecs, "toarray"):
                dense = np.asarray(doc_vecs.toarray(), dtype=np.float64)
            else:
                dense = np.asarray(doc_vecs, dtype=np.float64)
            # true cosine (query normalized too — P2 parity of similarity values)
            q_norm = np.linalg.norm(query_vec) or 1.0
            norms = np.linalg.norm(dense, axis=1, keepdims=True)
            norms[norms == 0] = 1
            sims = (dense @ query_vec) / (norms[:, 0] * q_norm)
            order = np.argsort(-sims)
            scored = [
                {"id": points[i]["id"], "content": points[i]["content"],
                 "similarity": float(sims[i])}
                for i in order
                if sims[i] >= threshold
            ][:limit]
        except Exception:  # noqa: BLE001 — transient vector failure → legacy
            vectors_failed = True
    if (not scored and query_vec is None) or vectors_failed:
        # Cached vectors unavailable/failed — legacy in-memory scorer (same inputs).
        scored = search_points(query, points, threshold=threshold, limit=limit)

    meta = {p["id"]: p for p in points}
    return [
        SearchResult(
            id=r["id"],
            content=r["content"],
            point_kind=meta.get(r["id"], {}).get("pointKind", ""),
            scores=SearchScores(fts=None, vector=None, structural=None, rrf=r["similarity"]),
            match_source="tfidf",
            ep=None,
        ).to_dict()
        for r in scored
    ]
