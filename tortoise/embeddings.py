"""Embedding-based cross-vocabulary concept matching for Tortoise.

Different sources use different words for the same concepts. Term-index matching
finds 0 cross-lens connections; embeddings bridge that gap.
"""
from __future__ import annotations

import logging
import threading
import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingModel:
    """Lazy-loaded embedding model singleton.

    Phase 0 stub (#7748): returns None until Phase 1A (#7698) loads all-MiniLM-L6-v2.

    Model loading runs in a worker thread with a hard timeout (#7871 E2E):
    SentenceTransformer downloads ~90MB from HuggingFace on first use. In a
    sandboxed/blocked network this hangs indefinitely, blocking every
    create_point call. We therefore (1) load in a thread, (2) time-box it,
    (3) remember failure so we never retry the download per-request.
    Embeddings are OPTIONAL — point creation must never depend on them.
    """
    _instance: "EmbeddingModel | None" = None
    _model = None
    _load_failed = False
    _lock = threading.Lock()
    _LOAD_TIMEOUT_S = 10.0

    @classmethod
    def get(cls) -> "EmbeddingModel | None":
        """Get or create the singleton. Returns None if model unavailable."""
        if cls._instance is None and not cls._load_failed:
            with cls._lock:
                if cls._instance is None and not cls._load_failed:
                    cls._instance = cls()
        return cls._instance._model if (cls._instance and cls._instance._model) else None

    @classmethod
    def _reset(cls) -> None:
        """Test hook — clear cached instance/failure state."""
        with cls._lock:
            cls._instance = None
            cls._model = None
            cls._load_failed = False

    def __init__(self):
        result: dict = {"model": None}

        def _load():
            try:
                from sentence_transformers import SentenceTransformer
                result["model"] = SentenceTransformer("all-MiniLM-L6-v2")
            except Exception as e:  # noqa: BLE001
                logger.info("sentence-transformers unavailable: %s", e)
                result["model"] = None

        t = threading.Thread(target=_load, daemon=True)
        t.start()
        t.join(timeout=self._LOAD_TIMEOUT_S)
        if t.is_alive():
            # Download/load hung — give up, remember failure, never retry.
            logger.warning(
                "Embedding model load exceeded %ss — disabling embeddings "
                "for this process (point creation must not block on it).",
                self._LOAD_TIMEOUT_S,
            )
            type(self)._load_failed = True
            self._model = None
            return
        self._model = result["model"]

    def encode(self, texts: list[str], batch_size: int = 32):
        """Encode texts to embeddings. Returns numpy array or None."""
        if self._model is None:
            return None
        return self._model.encode(texts, batch_size=batch_size, show_progress_bar=False)


def compute_embedding(content: str, max_tokens: int = 512) -> list[float] | None:
    """Compute embedding for a single text. Returns 384-dim list or None.

    Truncates to max_tokens before encoding to prevent OOM.
    Returns None if model unavailable or encoding fails.
    """
    model = EmbeddingModel.get()
    if model is None:
        return None
    try:
        words = content.split()[:max_tokens]
        truncated = " ".join(words)
        vec = model.encode([truncated])
        if vec is None or len(vec) == 0:
            return None
        return vec[0].tolist()
    except Exception:
        return None


def find_cross_source_matches(
    points: dict[str, dict],
    threshold: float = 0.75,
) -> list[dict]:
    """Find points from different speakers that describe the same concept.

    Args:
        points: point_id → {content, speaker, ...} from fold()
        threshold: cosine similarity threshold (0.0 to 1.0)

    Returns:
        List of {"src": id, "dst": id, "similarity": float, "speakers": [sp1, sp2]}
    """
    ids = list(points)
    texts = [points[i]["content"] for i in ids]
    speakers = [points[i].get("speaker", "unknown") for i in ids]

    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        vectors = model.encode(texts, show_progress_bar=False)
    except ImportError:
        from sklearn.feature_extraction.text import TfidfVectorizer
        model = TfidfVectorizer()
        vectors = model.fit_transform(texts).toarray()

    # Normalized dot product = cosine similarity
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1
    vectors_n = vectors / norms
    sim = vectors_n @ vectors_n.T

    matches = []
    n = len(ids)
    for i in range(n):
        for j in range(i + 1, n):
            if speakers[i] == speakers[j]:
                continue
            if sim[i, j] >= threshold:
                matches.append({
                    "src": ids[i],
                    "dst": ids[j],
                    "similarity": float(sim[i, j]),
                    "speakers": [speakers[i], speakers[j]],
                })

    return matches


def search_points(
    query: str,
    points: list[dict],
    *,
    threshold: float = 0.3,
    limit: int = 10,
) -> list[dict]:
    """Semantic search over Points. Returns ranked [{id, content, similarity, snippet}, ...]."""
    if not points:
        return []

    ids = [p["id"] for p in points]
    texts = [p["content"] for p in points]

    # ponytail: TF-IDF fallback — sentence_transformers is heavy
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        vectors = model.encode([query] + texts, show_progress_bar=False)
        query_vec, doc_vecs = vectors[0], vectors[1:]
    except ImportError:
        from sklearn.feature_extraction.text import TfidfVectorizer
        model = TfidfVectorizer()
        doc_vecs = model.fit_transform(texts).toarray()
        query_vec = model.transform([query]).toarray()[0]

    norms = np.linalg.norm(doc_vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1
    doc_vecs_n = doc_vecs / norms
    q_norm = np.linalg.norm(query_vec)
    q_norm = q_norm if q_norm > 0 else 1
    query_vec_n = query_vec / q_norm

    sims = doc_vecs_n @ query_vec_n.T

    results = []
    for i, sim in enumerate(sims):
        if sim < threshold:
            continue
        content = texts[i]
        results.append({
            "id": ids[i],
            "content": content,
            "similarity": float(sim),
            "snippet": content[:200] if len(content) > 200 else content,
        })

    results.sort(key=lambda r: r["similarity"], reverse=True)
    return results[:limit]
