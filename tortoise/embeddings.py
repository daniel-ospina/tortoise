"""Embedding-based cross-vocabulary concept matching for Tortoise.

Different sources use different words for the same concepts. Term-index matching
finds 0 cross-lens connections; embeddings bridge that gap.

Threshold calibration (all-MiniLM-L6-v2, measured 2026-08-07 for #399):
    near-duplicate paraphrases ....... 0.90+   (NEAR_DUPLICATE_THRESHOLD = 0.75)
    cross-vocabulary paraphrase band . 0.35-0.51  (DEFAULT_THRESHOLD = 0.40)
    issue #399 motivating pair ....... 0.29 (boundary: topically similar, NOT
                                         logically implied — verification decides)
    unrelated / noise floor ........... <= 0.15

Thresholds are model-specific — recalibrate when swapping the embedder.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

logger = logging.getLogger(__name__)

# #399: the issue's proposed 0.75 threshold is a near-duplicate-only setting
# with all-MiniLM-L6-v2 (cross-vocab paraphrase pairs score 0.35-0.51).
NEAR_DUPLICATE_THRESHOLD = 0.75
DEFAULT_THRESHOLD = 0.40


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
    _instance: "EmbeddingModel | None" = None  # noqa: UP037
    _model = None
    _lock = threading.Lock()
    _LOAD_TIMEOUT_S = 30.0  # generous for cold I/O (model is 90MB on disk; pre-warmed at startup in hosted)
    _FAIL_COOLDOWN_S = 60.0  # negative cache: skip retry for 60s after a failed load
    _last_failed_at: float | None = None

    @classmethod
    def get(cls, load_timeout: float | None = None) -> "EmbeddingModel | None":  # noqa: UP037
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
        now = time.monotonic()
        if cls._instance is None and cls._last_failed_at is not None and \
                (now - cls._last_failed_at) < cls._FAIL_COOLDOWN_S:
            # Negative cache (code-review P2, #399): a load just failed — return
            # None immediately instead of blocking up to 30s per request in a
            # degraded environment (offline dev, cold CI, OOM). Retry after the
            # cooldown window via the normal "retries on next get()" path.
            return None
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
                cls._last_failed_at = time.monotonic()
        return model

    @classmethod
    def _reset(cls) -> None:
        """Test hook — clear cached instance and failure cooldown."""
        with cls._lock:
            cls._instance = None
            cls._model = None
            cls._last_failed_at = None

    def __init__(self, load_timeout: float | None = None):
        timeout = load_timeout if load_timeout is not None else self._LOAD_TIMEOUT_S
        result: dict = {"model": None}

        def _load():
            try:
                from sentence_transformers import SentenceTransformer
                result["model"] = SentenceTransformer("all-MiniLM-L6-v2")
            except ImportError:
                # Designed zero-dependency path — INFO, no traceback noise.
                logger.info("sentence-transformers not installed — embeddings degrade")
                result["model"] = None
            except Exception as e:  # noqa: BLE001, RUF100
                # #880: a load failure (e.g. LocalEntryNotFoundError when the
                # model is missing under HF_HUB_OFFLINE) is a real degrade —
                # warn with traceback so it stays observable (#330 contract).
                logger.warning(
                    "sentence-transformers unavailable — embeddings degrade: %s",
                    e, exc_info=True,
                )
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


def _encode(texts: list[str]) -> tuple[np.ndarray, bool]:
    """Encode texts → (vectors, degraded). degraded=True ⇒ TF-IDF fallback.

    Routes through the EmbeddingModel singleton (all-MiniLM-L6-v2) — never
    re-instantiates the model per call (#399: find_cross_source_matches and
    search_points used to reload the 90MB model on EVERY call). Falls back to
    deterministic sklearn TF-IDF when the model is unavailable. Embeddings stay
    optional: callers must tolerate degraded output (degraded=True).
    """
    if not texts:
        return np.zeros((0, 0)), False
    model = EmbeddingModel.get()
    if model is not None:
        try:
            vecs = model.encode(texts, show_progress_bar=False)
            if vecs is not None and len(vecs) > 0:
                return np.asarray(vecs, dtype=np.float64), False
        except Exception:  # noqa: BLE001, RUF100
            logger.warning("embedding encode failed — TF-IDF fallback", exc_info=True)
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # lazy: [embeddings] extra
        return TfidfVectorizer().fit_transform(texts).toarray(), True
    except (ValueError, ImportError):
        # Empty / stopword-only vocabulary or sklearn missing — nothing to
        # match; return a zero matrix so cosine similarity is 0 and no
        # candidates emerge. Embeddings stay OPTIONAL (#399 contract).
        return np.zeros((len(texts), 1)), True


def cosine_similarity_matrix(vectors: np.ndarray) -> np.ndarray:
    """Normalized dot product = cosine similarity. Pure numpy.

    Handles zero-norm rows (all-zero vectors) by leaving them at 0 similarity.
    """
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    v = vectors / norms
    return v @ v.T


def find_cross_source_matches(
    points: dict[str, dict],
    threshold: float = 0.75,
) -> list[dict]:
    """Find points from different speakers that describe the same concept.

    Backward-compatible wrapper (speaker-keyed) over the shared encode +
    cosine pipeline (#399). Use find_cross_lens_matches (tortoise/cross_lens.py)
    for lens/source-keyed matching.

    Args:
        points: point_id → {content, speaker, ...} from fold()
        threshold: cosine similarity threshold (0.0 to 1.0)

    Returns:
        List of {"src": id, "dst": id, "similarity": float, "speakers": [sp1, sp2]}
    """
    ids = list(points)
    texts = [points[i]["content"] for i in ids]
    speakers = [points[i].get("speaker", "unknown") for i in ids]

    vectors, _ = _encode(texts)
    sim = cosine_similarity_matrix(vectors)

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

    # #399: route through the EmbeddingModel singleton (never re-instantiate
    # the model per call). Degraded mode preserves LEGACY TF-IDF semantics
    # (code-review P2): fit on DOCUMENTS ONLY, transform the query separately —
    # jointly fitting on [query] + texts lets the query enter the vocabulary,
    # shifting idf and silently reordering results (verified: ~2% reorders,
    # ~38% threshold changes at 0.3 on random corpora).
    model = EmbeddingModel.get()
    if model is not None:
        try:
            vecs = np.asarray(model.encode([query] + texts, show_progress_bar=False),  # noqa: RUF005
                              dtype=np.float64)
            query_vec, doc_vecs = vecs[0], vecs[1:]
        except Exception:  # noqa: BLE001, RUF100
            model = None
    if model is None:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            tv = TfidfVectorizer()
            doc_vecs = tv.fit_transform(texts).toarray()
            query_vec = tv.transform([query]).toarray()[0]
        except (ValueError, ImportError):
            # Empty/stopword-only vocabulary or sklearn missing — nothing to
            # search (legacy: raised ValueError, caught by fallback_tfidf → []).
            return []

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
