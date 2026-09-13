"""Embedding-based cross-vocabulary concept matching for Tortoise.

Different sources use different words for the same concepts. Term-index matching
finds 0 cross-lens connections; embeddings bridge that gap.

Threshold calibration (BAAI/bge-small-en-v1.5, measured 2026-08-21 for #1349):
    near-duplicate paraphrases ....... 0.89+   (NEAR_DUPLICATE_THRESHOLD = 0.89)
    dedup review band ................ 0.84    (DEDUP_REVIEW — sdk.py, T14)
    dedup auto-merge band ............ 0.94    (DEDUP_AUTO_MERGE — sdk.py, T14)
    cross-vocabulary paraphrase band . 0.72+   (DEFAULT_THRESHOLD = 0.72)
    unrelated / noise floor ........... below the 0.72 default band

Thresholds are model-specific — recalibrate when swapping the embedder.
"""
from __future__ import annotations

import logging
import threading
import time

import numpy as np

logger = logging.getLogger(__name__)

# The active embedder — single source of truth for the production model id.
# #1349 embedder-selection swap (2026-08-21): bge-small replaces
# all-MiniLM-L6-v2 as the default (evidence gate: recall +15.7%, p=0.0005;
# HNSW spot-check cleared). Rotating the embedder = editing this line.
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
# Supply-chain pin (VULN-001, security review): resolved HF commit at bake time
# (2026-08-21). A mutable tag would silently serve tampered weights.
EMBEDDING_MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"

# #1349: thresholds are model-specific — recalibrated for bge-small from
# tests/fixtures/labeled_pairs.jsonl (tools/calibrate_thresholds --model
# bge-small, measured 2026-08-21): DEFAULT = p25(paraphrase band),
# NEAR_DUPLICATE = p5(near-dup band).
NEAR_DUPLICATE_THRESHOLD = 0.89
DEFAULT_THRESHOLD = 0.72


class EmbeddingModel:
    """Lazy-loaded embedding model singleton.

    Loads BAAI/bge-small-en-v1.5 (384-dim, EMBEDDING_MODEL) via
    sentence-transformers. Model loading
    runs in a worker thread with a 90s timeout (#1349: bge-small cold load measured ~57s). In the hosted Docker image the
    model is pre-downloaded at build time (Dockerfile.hosted) and pre-warmed at
    container start (entrypoint.sh), so the first API request never hits a cold
    start. Failures are NOT permanent — the next get() call creates a fresh
    instance and retries.

    Embeddings are OPTIONAL — point creation and search must never depend on them.
    """
    _instance: "EmbeddingModel | None" = None  # noqa: UP037
    _model = None
    _lock = threading.Lock()
    _LOAD_TIMEOUT_S = 90.0  # #1349: bge-small cold load measured ~57s on a
    # contended machine (vs ~30s for the old MiniLM) — 30s caused silent
    # TF-IDF degrade on cold caches; 90s covers the download+torch-import
    # window. Pre-warmed at startup in hosted.
    _FAIL_COOLDOWN_S = 60.0  # negative cache: skip retry for 60s after a failed load
    _last_failed_at: float | None = None
    # (B) #2952 explicit warm-up state — see warm_up()/start_warm_up()/status().
    _WARM_UP_LOCK = threading.Lock()
    _warm_up_thread: threading.Thread | None = None
    _warm_up_started = False
    _last_error: str | None = None
    #: Why the last load attempt failed: "not_installed" (designed absence —
    #: INFO) vs "load_failed"/"load_timeout" (real degrade — WARNING).
    _last_failure_kind: str | None = None

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
            # None immediately instead of blocking up to 90s per request in a
            # degraded environment (offline dev, cold CI, OOM). Retry after the
            # cooldown window via the normal "retries on next get()" path.
            return None
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    # #2952 (B) P1 review fix: re-check the negative cache
                    # INSIDE the lock. A caller that passed the outer check
                    # before a concurrent load (engine-init warm-up!) recorded
                    # its failure would otherwise start a SECOND full load,
                    # defeating the once-only warm-up and double-blocking the
                    # first query. The failure timestamp is written under this
                    # same lock, so the re-check is race-safe.
                    now_locked = time.monotonic()
                    if cls._last_failed_at is not None and \
                            (now_locked - cls._last_failed_at) < cls._FAIL_COOLDOWN_S:
                        return None
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
        if model is not None:
            # #2952: a healthy model clears the prior failure state so
            # status() never reports a stale failure_kind next to
            # available=True (P2 review fix).
            cls._last_failure_kind = None
            cls._last_error = None
        return model

    @classmethod
    def warm_up(cls, *, load_timeout: float | None = None) -> bool:
        """(B) #2952 — explicitly probe/load the embedder ONCE at init.

        Best-effort and non-fatal: returns True when the model is available,
        False when the vector leg cannot run. NEVER raises. The point is to
        surface a load failure *at init* (one clear WARNING) instead of
        letting it masquerade as a per-query ``_FAIL_COOLDOWN_S`` gap — the
        cooldown itself is unchanged (the model is still retried on a later
        ``get()`` call; this is not sticky-off).

        The failure is declared, not silent: ``status()`` reports the state
        and ``tortoise.search_engine.declared_degraded_read(leg_trace)`` marks
        the resulting single-leg reads.
        """
        try:
            model = cls.get(load_timeout=load_timeout)
        except Exception as exc:  # noqa: BLE001, RUF100 — warm-up is non-fatal
            cls._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "embedder warm-up raised — the vector leg is unavailable and "
                "single-leg (keyword-only) reads must be declared (#2952): %s",
                exc, exc_info=True,
            )
            return False
        if model is None:
            kind = cls._last_failure_kind or "model_unavailable"
            cls._last_error = kind
            if kind == "not_installed":
                # Designed zero-dependency path (embeddings extra absent) —
                # INFO, matching the loader's own contract; not a new degrade.
                logger.info(
                    "embedder warm-up: sentence-transformers not installed "
                    "(designed) — vector leg unavailable; keyword-only reads "
                    "must be declared (#2952)."
                )
            else:
                logger.warning(
                    "embedder warm-up FAILED (%s) — the vector leg will not "
                    "run until a later retry succeeds; a keyword-only read is "
                    "NOT hybrid retrieval and must be declared (#2952).",
                    kind,
                )
            return False
        cls._last_error = None
        cls._last_failure_kind = None
        return True

    @classmethod
    def start_warm_up(cls, *, load_timeout: float | None = None
                      ) -> threading.Thread | None:
        """(B) #2952 — non-blocking, once-per-process warm-up for engine init.

        Spawns a daemon thread that calls :meth:`warm_up` so client/engine
        init never blocks on the ~57s cold BAAI/bge-small-en-v1.5 load while
        still surfacing the outcome once (the same posture as the hosted
        ``_lifespan`` pre-warm, #545). Idempotent: repeated calls after the
        first return the existing thread (or None when it already finished).

        Opt out with ``TORTOISE_EMBEDDER_WARMUP=0`` (tests disable it — a
        background load would race explicit embedder stubs). Returns None
        when disabled or already started.
        """
        import os
        if os.environ.get("TORTOISE_EMBEDDER_WARMUP", "1").strip().lower() \
                in ("0", "false", "no", "off"):
            return None
        with cls._WARM_UP_LOCK:
            if cls._warm_up_started:
                t = cls._warm_up_thread
                return t if (t is not None and t.is_alive()) else None
            cls._warm_up_started = True
            thread = threading.Thread(
                target=cls._warm_up_worker, args=(load_timeout,),
                name="tortoise-embedder-warmup", daemon=True,
            )
            cls._warm_up_thread = thread
            thread.start()
            return thread

    @classmethod
    def _warm_up_worker(cls, load_timeout: float | None) -> None:
        """Daemon-thread body — never lets a warm-up failure escape."""
        try:
            cls.warm_up(load_timeout=load_timeout)
        except Exception:  # noqa: BLE001, RUF100 — a daemon thread must not raise
            logger.debug("embedder warm-up worker failed", exc_info=True)

    @classmethod
    def status(cls) -> dict:
        """(C) #2952 — the DECLARED embedder availability state.

        A read surface that could not run its vector leg can report this
        instead of silently presenting a keyword-only result as hybrid:
        ``{"model", "available", "state" ∈ ready|cooldown|unavailable,
        "last_error", "cooldown_remaining_s"}``. Read-only, no side effects.
        """
        instance = cls._instance
        model = instance._model if instance is not None else None
        remaining = 0.0
        if cls._last_failed_at is not None:
            remaining = max(
                0.0, cls._FAIL_COOLDOWN_S - (time.monotonic() - cls._last_failed_at))
        if model is not None:
            state = "ready"
        elif remaining > 0.0:
            state = "cooldown"
        else:
            state = "unavailable"
        return {
            "model": EMBEDDING_MODEL,
            "available": model is not None,
            "state": state,
            "last_error": cls._last_error,
            "failure_kind": cls._last_failure_kind,
            "cooldown_remaining_s": round(remaining, 3),
        }

    @classmethod
    def _reset(cls) -> None:
        """Test hook — clear cached instance and failure cooldown."""
        with cls._lock:
            cls._instance = None
            cls._model = None
            cls._last_failed_at = None
        with cls._WARM_UP_LOCK:
            cls._warm_up_thread = None
            cls._warm_up_started = False
            cls._last_error = None
            cls._last_failure_kind = None

    def __init__(self, load_timeout: float | None = None):
        timeout = load_timeout if load_timeout is not None else self._LOAD_TIMEOUT_S
        result: dict = {"model": None}

        def _load():
            try:
                from sentence_transformers import SentenceTransformer
                result["model"] = SentenceTransformer(
                    EMBEDDING_MODEL, revision=EMBEDDING_MODEL_REVISION)
            except ImportError as e:
                # Distinct the DESIGNED absence (package not installed — INFO)
                # from an import-time failure inside an installed dependency
                # chain (real degrade — WARNING) (#2952 P2 review fix).
                import importlib.util
                try:
                    spec = importlib.util.find_spec("sentence_transformers")
                except Exception:  # noqa: BLE001, RUF100 — a probe must never
                    spec = object()  # raise; treat unknown as a real failure
                if spec is None:
                    # Designed zero-dependency path — INFO, no traceback noise.
                    logger.info("sentence-transformers not installed — embeddings degrade")
                    type(self)._last_failure_kind = "not_installed"
                else:
                    logger.warning(
                        "sentence-transformers import failed — embeddings "
                        "degrade: %s", e, exc_info=True,
                    )
                    type(self)._last_failure_kind = "load_failed"
                result["model"] = None
            except Exception as e:  # noqa: BLE001, RUF100
                # #880: a load failure (e.g. LocalEntryNotFoundError when the
                # model is missing under HF_HUB_OFFLINE) is a real degrade —
                # warn with traceback so it stays observable (#330 contract).
                logger.warning(
                    "sentence-transformers unavailable — embeddings degrade: %s",
                    e, exc_info=True,
                )
                type(self)._last_failure_kind = "load_failed"
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
            type(self)._last_failure_kind = "load_timeout"
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

    Routes through the EmbeddingModel singleton (EMBEDDING_MODEL, the
    bge-small embedder) — never
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
    threshold: float = NEAR_DUPLICATE_THRESHOLD,
) -> list[dict]:
    """Find points from different speakers that describe the same concept.

    Backward-compatible wrapper (speaker-keyed) over the shared encode +
    cosine pipeline (#399). Use find_cross_lens_matches (tortoise/cross_lens.py)
    for lens/source-keyed matching.

    The default keeps the #399 issue-spec near-dup-ONLY semantics: it tracks
    NEAR_DUPLICATE_THRESHOLD (0.89 for bge-small) so a model swap cannot
    silently admit loose paraphrases into this conservative wrapper (#1349
    T14 — was the hand-pinned 0.75 MiniLM literal).

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
