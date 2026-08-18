"""In-memory degraded-fallback corpus snapshot (#1375).

The degraded path (all retrieval strategies failed → in-memory TF-IDF) used to
re-fetch the whole corpus with FULL payloads via ``self.query`` (~350ms on
Docker for ~1000 points) and re-fit the TF-IDF vectorizer per call (8-700ms
variable). This module keeps a LEAN projection (id/content/pointKind/status)
plus the fitted sklearn vectorizer + document vectors, invalidated by a dirty
flag on the write surfaces (``_mark_dirty`` hook + ``delete_point``) with a
LAZY TTL backstop (age check at read — zero steady-state cost; logs when it
fires while clean, signalling a write bypassed the normal surfaces).

Sklearn-path parity: the cached scorer fits on DOCUMENTS ONLY and transforms
the query separately — the exact semantics the legacy path preserves (#399) —
so results are provably identical (verified by the parity test).

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

# Matches the retrieval layer's retracted/operator exclusions so the snapshot
# mirrors what self.query(kind=...) would return (#1375 D2).
_SNAPSHOT_QUERY = (
    "MATCH (n:Point) "
    "WHERE (n.is_operator = false OR n.is_operator IS NULL) "
    "RETURN n.id, n.content, n.pointKind, n.status"
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
    """Lean corpus projection + fitted sklearn vectors. None if too big."""
    g = proj.g
    try:
        count = g.query(
            "MATCH (n:Point) WHERE (n.is_operator = false OR n.is_operator IS NULL) "
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
            {"id": r[0], "content": r[1] or "", "pointKind": r[2] or "", "status": r[3] or ""}
            for r in rows
        ]
    except Exception as e:  # noqa: BLE001 — degraded path never crashes
        logger.warning("Fallback snapshot build query failed: %s", e)
        return None

    vectorizer = doc_vecs = None
    try:
        import numpy as np
        from sklearn.feature_extraction.text import TfidfVectorizer
        tv = TfidfVectorizer()
        doc_vecs = np.asarray(
            tv.fit_transform([p["content"] for p in points]).toarray(),
            dtype=np.float64,
        )
        vectorizer = tv
    except Exception as e:  # noqa: BLE001 — vectors optional; scorer falls back
        logger.info("Fallback snapshot vectorization unavailable (sklearn): %s", e)

    return {
        "built_at": time.monotonic(),
        "dirty": False,
        "points": points,
        "vectorizer": vectorizer,
        "doc_vecs": doc_vecs,
    }


def search_snapshot(
    query: str,
    snap: dict,
    *,
    limit: int = 10,
    exclude_status: list[str] | None = None,
    include_terminal: bool = False,
    threshold: float = 0.0,
) -> list[dict]:
    """Score the snapshot corpus against ``query``.

    Returns the SAME shape as ``search_engine.fallback_tfidf`` (a list of
    SearchResult.to_dict() with match_source="tfidf"). Mirrors the legacy
    fallback semantics: terminal-status points (retracted/superseded/
    outdated/archived/deprecated) are excluded unless ``include_terminal``
    (the #1391 include_terminal contract), and ``exclude_status`` composes
    on top. When the cached vectors are unavailable, delegates to the
    legacy in-memory scorer.
    """
    import numpy as np

    from tortoise.embeddings import search_points
    from tortoise.search_engine import (
        SearchResult, SearchScores, TERMINAL_EXCLUDED_STATUSES,
    )

    points = snap["points"]
    mask = None
    if not include_terminal:
        _term = set(TERMINAL_EXCLUDED_STATUSES)
        idx = [i for i, p in enumerate(points) if p["status"] not in _term]
        points = [points[i] for i in idx]
        mask = idx
    if exclude_status:
        _ex = set(exclude_status)
        idx = [i for i, p in enumerate(points) if p["status"] not in _ex]
        points = [points[i] for i in idx]
        mask = idx if mask is None else [mask[i] for i in idx]
    if not points:
        return []

    if snap["vectorizer"] is not None and snap["doc_vecs"] is not None:
        try:
            tv = snap["vectorizer"]
            doc_vecs = snap["doc_vecs"][mask] if mask is not None else snap["doc_vecs"]
            query_vec = np.asarray(tv.transform([query]).toarray()[0], dtype=np.float64)
            norms = np.linalg.norm(doc_vecs, axis=1, keepdims=True)
            norms[norms == 0] = 1
            sims = (doc_vecs @ query_vec) / norms[:, 0]
            order = np.argsort(-sims)
            scored = [
                {"id": points[i]["id"], "content": points[i]["content"],
                 "similarity": float(sims[i])}
                for i in order
                if sims[i] >= threshold
            ][:limit]
        except Exception:  # noqa: BLE001 — empty/stopword vocabulary → same as legacy
            scored = []
    else:
        # Cached vectors unavailable — legacy in-memory scorer (same inputs).
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
