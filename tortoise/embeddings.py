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

    Loads all-MiniLM-L6-v2 (384-dim) via sentence-transformers. Model loading
    runs in a worker thread with a 30s timeout. In the hosted Docker image the
    model is pre-downloaded at build time (Dockerfile.hosted) and pre-warmed at
    container start (entrypoint.sh), so the first API request never hits a cold
    start. Failures are NOT permanent — the next get() call creates a fresh
    instance and retries.

    Embeddings are OPTIONAL — point creation and search must never depend on them.
    """
    _instance: "EmbeddingModel | None" = None
    _model = None
    _lock = threading.Lock()
    _LOAD_TIMEOUT_S = 30.0  # generous for cold I/O (model is 90MB on disk; pre-warmed at startup in hosted)

    @classmethod
    def get(cls, load_timeout: float | None = None) -> "EmbeddingModel | None":
        """Get or create the singleton. Returns None if model unavailable.

        Loads the model in a worker thread with a hard timeout. In the hosted
        Docker image the model is pre-downloaded at build time (Dockerfile.hosted)
        and pre-warmed at startup via the FastAPI lifespan background thread
        (hosted_api.py _lifespan), so this path is only hit in dev or if the
        pre-warm was skipped. Unlike the old implementation, we do NOT
        permanently self-disable — a transient failure (OOM from a competing
        process, slow I/O) is retried on the next call.

        Args:
            load_timeout: Override _LOAD_TIMEOUT_S. The lifespan pre-warm
                passes a longer window (cold-start torch import on a 2GB VM
                can exceed 30s, #545); request paths keep the default so
                latency stays bounded.
        """
        timeout = load_timeout if load_timeout is not None else cls._LOAD_TIMEOUT_S
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(load_timeout=timeout)
        model = cls._instance._model if (cls._instance and cls._instance._model) else None
        if model is None and cls._instance is not None:
            # Transient load failure (timeout/OOM) — clear the instance so the
            # NEXT get() call retries (code-review P2 fix, #160). Previously
            # the timed-out instance was cached forever, making the failure
            # permanent despite the docstring claiming retry.
            with cls._lock:
                cls._instance = None
                cls._model = None
        return model

    @classmethod
    def _reset(cls) -> None:
        """Test hook — clear cached instance."""
        with cls._lock:
            cls._instance = None
            cls._model = None

    def __init__(self, load_timeout: float | None = None):
        timeout = load_timeout if load_timeout is not None else self._LOAD_TIMEOUT_S
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
        t.join(timeout=timeout)
        if t.is_alive():
            # Model load timed out — log and return None. Do NOT permanently
            # self-disable: in the hosted container the model is pre-downloaded
            # and pre-warmed at startup (entrypoint.sh), so this path means
            # transient resource starvation (OOM, competing process). Next call
            # will create a fresh instance and retry.
            logger.warning(
                "Embedding model load exceeded %ss — returning None "
                "(retries on next get() call).",
                self._LOAD_TIMEOUT_S,
            )
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
