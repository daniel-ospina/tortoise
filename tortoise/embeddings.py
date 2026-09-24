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
import math
import threading
import time

import numpy as np

from .env_truthy import env_flag  # #4097: the declared truthy contract
from .exceptions import EmbedderUnavailableError  # #4861: the REQUIRED contract
from .ids import content_hash


def _embedder_warmup_enabled() -> bool:
    """`TORTOISE_EMBEDDER_WARMUP` — the engine-init warm-up opt-in (default ON).

    #4097: the single resolution point (``EmbeddingModel.start_warm_up`` calls it),
    through the declared truthy contract; ``0``/``false``/``no``/``off`` opt out.
    """
    return env_flag("TORTOISE_EMBEDDER_WARMUP", True)

logger = logging.getLogger(__name__)

# The active embedder — single source of truth for the production model id.
# #1349 embedder-selection swap (2026-08-21): bge-small replaces
# all-MiniLM-L6-v2 as the default (evidence gate: recall +15.7%, p=0.0005;
# HNSW spot-check cleared). Rotating the embedder = editing this line.
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
#: #4194: the model's vector width, in ONE place. The Point HNSW index is
#: created with this width (tortoise/projection/__init__.py) and the vector leg
#: requires it, so a stored vector of any other length is not a near-miss — it
#: is a broken leg. The STORE declares this width to the write path
#: (``FalkorProjection.required_embedding_dim``), which routes through
#: :func:`encode_for_store` / :func:`encode_batch_for_store` — those degrade a
#: wrong-width row to ``None`` (fail-soft, LOGGED) rather than handing it to
#: ``vecf32``. A store with no vector index (the embedded brute-force lane)
#: declares ``None`` — it has no width to enforce, so the encoder's own width
#: governs.
EMBEDDING_DIM = 384
# Supply-chain pin (VULN-001, security review): resolved HF commit at bake time
# (2026-08-21). A mutable tag would silently serve tampered weights.
EMBEDDING_MODEL_REVISION = "5c38ec7c405ec4b44b94cc5a9bb96e735b38267a"


def embedding_identity() -> tuple[str | None, str | None]:
    """The (model, revision) identity of the vectors the active embedder makes.

    #5004: the journal stores an embedding alongside THIS identity, so a replay
    can restore the recorded vector verbatim (R1, `docs/durability-posture.md`
    §3/§14.1 O1: the embedding STORES, it is not regenerated) and treat a
    model change as an explicit, recorded decision instead of a silent
    re-encode that yields a different graph.

    Resolved at CALL time, not import time, so a test (or a future operator
    swap) that rebinds the module constants is reflected by both the writer
    that stamps the identity and the replay that compares against it.
    """
    return EMBEDDING_MODEL, EMBEDDING_MODEL_REVISION


#: Defensive bound on a journalled vector's length. The writer emits exactly
#: `EMBEDDING_DIM`; a hand-edited/corrupt record must not make a rebuild
#: materialise an unbounded list. Far larger than any real width.
_MAX_JOURNALLED_VECTOR_LEN = 8192


def stamp_journal_embedding(payload: dict, *, creating: bool) -> dict:
    """#5004: normalise and stamp a point payload's embedding + its identity.

    THE single seam both journal producers use (`TortoiseSDK._emit_event`,
    `EventAPI._point`, and the capture turn loop), so they cannot drift.

    **PRESENCE IS OWNERSHIP.** A producer that owns the field sets
    ``payload["embedding"]`` (to the vector, or to ``None`` when it genuinely
    has none) BEFORE calling. The key being present is what tells the replay
    "the journal owns this field for this id — do NOT recompute" — which is how
    a re-capture made while the embedder is unavailable keeps its PRESERVED
    vector, instead of the replay inventing a new one (round-3 review). A key
    that is ABSENT means a legacy/foreign record, where recomputation is the
    only available behaviour.

    R1 (`docs/durability-posture.md` → *Derived properties that are
    STORED, not recomputed*: the design source is
    `docs/architecture/STORAGE-ARCHITECTURE.md` §3/§14.1 O1, landed via #5016)
    is that the
    embedding STORES —
    it is not regenerated on replay, because a re-embed is a RE-RUN: its output
    depends on model identity, revision and tokenizer, none of which the
    journal used to carry. So the payload carries the vector VERBATIM plus the
    identity it was computed under, and a replay restores it rather than
    silently producing a different graph from the same journal.

    ``creating`` gates the ATTESTATION, not the vector. `creating=True` stamps
    `embedding_text_hash` plus the model identity, because a creating event's
    vector was computed from the content in the same write. `creating=False`
    (a re-emitted snapshot such as `PointPromoted`) still carries the vector —
    without it the replay would RE-ENCODE and silently change the point's
    vector — but stamps NO identity, because that vector may predate a model
    change and the record cannot attest its origin. The creating record owns
    the attestation.

    Mutates and returns *payload*. A vector that cannot be normalised to a
    finite numeric list is replaced with ``None`` (the field stays OWNED) with
    a warning — never silently, and never allowed to reach the engine's
    `vecf32()` after a wipe: a journal record is a FILE (hand-editable,
    truncatable, writable by an older build), and `json` round-trips
    `NaN`/`Infinity` and unbounded ints by default.
    """
    if "embedding" not in payload:
        return payload
    raw = payload.get("embedding")
    vec: list[float] | None = None
    if raw is not None:
        # Accept ANY non-string iterable: the producers hand over a list, but a
        # graph read-back (`t.embedding` after a `vecf32` write, #5004 round-3)
        # can arrive as a numpy array or a driver vector type. A `dict` is not
        # a sequence of numbers and a `str` is a sequence of CHARACTERS —
        # both are refused rather than silently transposed.
        if isinstance(raw, (str, bytes, dict)):
            _drop_journalled_embedding(payload, "not a numeric sequence")
            return payload
        try:
            items = list(raw)
        except TypeError:
            _drop_journalled_embedding(payload, "not a numeric sequence")
            return payload
        if not items:
            _drop_journalled_embedding(payload, "empty")
            return payload
        if len(items) > _MAX_JOURNALLED_VECTOR_LEN:
            _drop_journalled_embedding(payload, "implausibly long")
            return payload
        vec = []
        for x in items:
            try:
                f = float(x)
            except (TypeError, ValueError, OverflowError):
                _drop_journalled_embedding(payload, "non-numeric element")
                return payload
            if not math.isfinite(f):
                _drop_journalled_embedding(payload, "non-finite element")
                return payload
            vec.append(f)
    payload["embedding"] = vec
    if (vec is not None and creating
            and not payload.get("embedding_verbatim")
            # #5004 round-3: a re-capture that did NOT encode a vector (the
            # embedder was unavailable) PRESERVES the node's existing one. The
            # vector may have been computed by a DIFFERENT model, so attesting
            # the ACTIVE one would record a false origin — and if the original
            # capture record is gone (retention, a journal enabled mid-stream)
            # the model change becomes SILENT, which is the failure R1 forbids.
            and not payload.get("embedding_preserved")):
        # Only a CREATING event may attest the vector's origin: its vector was
        # computed from the content in the same write.
        model, revision = embedding_identity()
        payload["embedding_model"] = model
        payload["embedding_revision"] = revision
        content = payload.get("content")
        if isinstance(content, str) and content:
            payload["embedding_text_hash"] = content_hash(content)
    else:
        # A RE-EMITTED snapshot (PointPromoted/…) carries the node's existing
        # vector, which may predate a model change — stamping the CURRENT
        # identity would mis-attest it. Drop both the text-hash and the
        # identity; the creating record owns the attestation, and the replay
        # restores this vector verbatim without claiming an origin. With NO
        # vector there is nothing to attest at all.
        payload.pop("embedding_text_hash", None)
        payload.pop("embedding_model", None)
        payload.pop("embedding_revision", None)
    return payload


def _drop_journalled_embedding(payload: dict, why: str) -> None:
    """Drop an unusable vector to an OWNED `None` and SAY SO.

    The key stays present on purpose: the producer observed this field, so the
    replay must not recompute it (see `stamp_journal_embedding` above). A
    silent drop is indistinguishable from "there was never a vector", which is
    exactly the divergence #5004 removes.
    """
    payload["embedding"] = None
    payload.pop("embedding_text_hash", None)
    payload.pop("embedding_model", None)
    payload.pop("embedding_revision", None)
    logger.warning(
        "journal: dropping an unusable embedding on Point %s (%s) — the field "
        "stays journal-owned as None, so the replay will NOT invent a vector "
        "(#5004)", payload.get("id"), why)

# #1349: thresholds are model-specific — recalibrated for bge-small from
# tests/fixtures/labeled_pairs.jsonl (tools/calibrate_thresholds --model
# bge-small, measured 2026-08-21): DEFAULT = p25(paraphrase band),
# NEAR_DUPLICATE = p5(near-dup band).
NEAR_DUPLICATE_THRESHOLD = 0.89
DEFAULT_THRESHOLD = 0.72

# #4028: the OPT-IN retrieval relevance floor (cosine) for the vector leg.
# NOT a default — see the measured reason below. Set
# TORTOISE_VECTOR_MIN_SIMILARITY to a value (this constant is the calibrated
# starting point) to enable it; unset → no floor (the pre-#4028 behaviour).
#
# Below this, a nearest-neighbour hit carries no relevance signal. It is
# distinct from DEFAULT_THRESHOLD above (the symmetric PARAPHRASE band, two
# short sentences about the same thing); this is the asymmetric
# QUERY->DOCUMENT noise ceiling, which sits lower.
#
# Calibration: bge-small-en-v1.5 (the pinned EMBEDDING_MODEL). On
# tests/fixtures/labeled_pairs.jsonl the UNRELATED band tops out at 0.580
# (37/37 below 0.60) while every paraphrase pair is >= 0.653 — a clean
# separation on that fixture.
#
# ⛔ WHY IT IS NOT DEFAULT-ON: that clean separation does NOT survive the
# query->document retrieval shape, where the bands OVERLAP. Two independent
# real measurements: the LongMemEval-v2 fixture's gold hits run down to
# 0.461 (median 0.693) against non-gold hard negatives up to 0.795, and a
# real relevant pair at 0.536 (query "Which programming language does this
# person prefer for coding?" -> "I really enjoy building side projects with
# Elixir these days.") sits BELOW the 0.580 unrelated ceiling. No floor can
# both drop the #4028 residue (observed up to 0.606) and keep those answers,
# so defaulting it on turns real reads empty (it fails
# tests/test_longmem_runner.py::test_vector_strategy_verified_in_eval_path).
# #4028's own store defect is a DATA defect (test residue), fixed by
# tools/purge_test_residue.py; this knob is defence-in-depth for an operator
# who has measured their own corpus.
#
# Recalibrate when the embedder is swapped (same rule as the thresholds
# above).
VECTOR_RELEVANCE_FLOOR = 0.60


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
    def _embedder_required(cls) -> bool:
        """``TORTOISE_EMBEDDING_MODEL_REQUIRED`` — the fail-loud opt-in (OFF).

        #4861: resolved through the declared truthy contract (#4097), like every
        other flag here, so ``0``/``false``/``no``/``off`` also opt out. UNSET →
        :meth:`get` returns ``None`` exactly as before, so dev and hosted
        behaviour cannot drift: the contract is off by default and reversible.
        """
        return env_flag("TORTOISE_EMBEDDING_MODEL_REQUIRED", False)

    @classmethod
    def _unavailable(cls, *, context: str) -> "EmbeddingModel | None":  # noqa: UP037
        """The single exit for "no model": ``None``, or raise when REQUIRED.

        #4861 — every ``None`` path in :meth:`get` returns through here, so its
        four origins (the outer and in-lock negative caches, and the transient
        load failure) cannot drift apart: a REQUIRED process fails by name, at
        the point of use, instead of silently running keyword-only while a
        green run and a runner-down run stay indistinguishable.

        ``failure_kind`` is carried through unchanged so the two cases stay
        distinguishable — ``not_installed`` (the environment never had the
        embedder) vs ``load_failed`` / ``load_timeout`` (it had it and the load
        broke). A REQUIRED process does not accept ``not_installed`` as an
        excuse: "designed absence" is only designed for a process that did not
        ask for the embedder.
        """
        if not cls._embedder_required():
            return None
        raise EmbedderUnavailableError(
            failure_kind=cls._last_failure_kind or "model_unavailable",
            model=EMBEDDING_MODEL,
            revision=EMBEDDING_MODEL_REVISION,
            last_error=cls._last_error,
            context=context,
        )

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
            return cls._unavailable(context="negative cache, load failed within cooldown")
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
                        return cls._unavailable(
                            context="negative cache (in-lock), load failed within cooldown")
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
        if model is None:
            # #4861: the transient-failure exit — the last of the four origins,
            # and the one a REQUIRED runner hits when the load itself broke.
            return cls._unavailable(context="load failed (timeout/OOM)")
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
        if not _embedder_warmup_enabled():
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


def _truncate_for_embedding(content: str, max_tokens: int) -> str:
    """The ONE stored-text composition used by every write-side embedder.

    Word-truncation to ``max_tokens`` before encoding prevents OOM. Shared by
    :func:`compute_embedding` and :func:`compute_embeddings` so the batched
    and single forms can never compose a different string for the same input
    (#4194).
    """
    return " ".join(content.split()[:max_tokens])


def compute_embeddings(
    texts: list[str], max_tokens: int = 512,
) -> list[list[float] | None]:
    """Batched form of :func:`compute_embedding` — SAME embedder, per text.

    Returns one entry per input text: a vector of the ENCODER's own width, or
    ``None`` where the model is unavailable / the encode failed. This exists so
    a whole capture window can be embedded in ONE model call instead of one per
    turn (#4194) without forking the embedder: it routes through the same
    ``EmbeddingModel`` singleton, the same :func:`_truncate_for_embedding`
    composition and the same un-normalised model output as
    :func:`compute_embedding`, so a batched vector and a single vector are
    byte-identical for the same text.

    ⛔ The width a STORE can hold is the INDEX's constraint, and there is no
    index on the embedded FalkorDBLite lane (brute-force
    ``vec.euclideanDistance`` is dimension-agnostic), so this generic encoder
    does not apply one — see :func:`encode_batch_for_store`, which the store's
    write paths call. Enforcing :data:`EMBEDDING_DIM` here silently NULLed
    every vector for a non-384 encoder on the index-less lane, and the
    cross-lens candidate pool filters on ``p.embedding IS NOT NULL`` — the
    #4280 regression (``tests/test_cross_lens_candidates.py``).

    ⛔ This is a widely-REPLACED seam (``tools/longmem_eval/encode_cache.py``
    and the longmem eval doubles swap the function itself), so it keeps its
    narrow ``(texts, max_tokens)`` call shape on purpose: a caller-side width
    keyword would raise ``TypeError`` inside every replacement and be swallowed
    by the write paths' ``except Exception`` — the same fail-open in a new
    place.
    """
    if not texts:
        return []
    model = EmbeddingModel.get()
    if model is None:
        return [None] * len(texts)
    try:
        truncated = [_truncate_for_embedding(t, max_tokens) for t in texts]
        vecs = model.encode(truncated)
        if vecs is None or len(vecs) != len(texts):
            return [None] * len(texts)
        return [vec.tolist() for vec in vecs]
    except Exception:
        return [None] * len(texts)


#: #4280: width mismatches already warned about, keyed ``(expected_dim, actual)``.
#: A width misconfiguration drops EVERY row, so an unlatched warning storms a
#: bulk write (one line per Point); the signal is the first occurrence of each
#: distinct mismatch.
_WIDTH_MISMATCH_WARNED: set[tuple[int | None, int]] = set()


def _degrade_to_width(
    vectors: list[list[float] | None], expected_dim: int | None,
) -> list[list[float] | None]:
    """Drop the rows a store of width ``expected_dim`` cannot hold (#4280).

    ``None`` means the caller's store has NO width-fixing vector index, so the
    encoder's own width governs and nothing is dropped. A dropped row becomes
    ``None`` (the node is still written; the read path declares the leg
    impaired) and is LOGGED once per distinct ``(expected_dim, actual)`` — a
    silent drop is indistinguishable from "the leg ran and found nothing",
    which is exactly how #4280 hid (fail-open).
    """
    if expected_dim is None:
        return list(vectors)
    out: list[list[float] | None] = []
    dropped = 0
    widths: set[int] = set()
    for row in vectors:
        if row is not None and len(row) != expected_dim:
            dropped += 1
            widths.add(len(row))
            out.append(None)
        else:
            out.append(row)
    if dropped:
        fresh = {(expected_dim, w) for w in widths} - _WIDTH_MISMATCH_WARNED
        if fresh:
            _WIDTH_MISMATCH_WARNED.update(fresh)
            logger.warning(
                "embedder returned %d/%d row(s) whose width != the store's "
                "required %d — those vectors are NOT stored (the dense leg "
                "degrades to keyword-only for them). Rotating the embedder "
                "requires re-embedding the store. (Warned once per distinct "
                "mismatch.)",
                dropped, len(vectors), expected_dim,
            )
    return out


def encode_for_store(
    content: str, expected_dim: int | None,
) -> list[float] | None:
    """Encode ONE text through the seam, degraded to the STORE's width (#4280).

    The store-scoped wrapper the Point write paths use:
    ``expected_dim`` is ``FalkorProjection.required_embedding_dim``
    (:data:`EMBEDDING_DIM` when the store has a Point HNSW index, ``None`` on
    the index-less embedded brute-force lane). It calls the
    :func:`compute_embedding` SEAM — the module global, so an installed
    ``EncodeCache`` still intercepts — and then applies the width the store
    declared.

    ⛔ The seam is called with the ONE positional argument its replacements
    declare (``compute_embedding(content)``; the encoder's own 512-word cap) and
    this helper takes no ``max_tokens``: several in-repo doubles are
    ``lambda content: ...``, and a second positional argument would raise
    ``TypeError`` inside them — swallowed by the write paths'
    ``except Exception`` into the same silent no-vector degrade #4280 is about.
    """
    vec = compute_embedding(content)
    return _degrade_to_width([vec], expected_dim)[0]


def encode_batch_for_store(
    texts: list[str], expected_dim: int | None,
) -> list[list[float] | None]:
    """Batched :func:`encode_for_store` — one model call, same width guard.

    The batch length is enforced against the input: the turn writers index the
    result per windowed turn, so a REPLACEMENT of the seam that returns a short
    batch would otherwise raise ``IndexError`` inside the capture loop — after
    the Session write — and leave a partial session. A short batch degrades to
    no vector per text, which the writers already handle.
    """
    vecs = compute_embeddings(texts)
    if len(vecs) != len(texts):
        logger.warning(
            "embedder returned %d row(s) for %d text(s) — no vector is "
            "stored for this batch (#4280).", len(vecs), len(texts),
        )
        return [None] * len(texts)
    return _degrade_to_width(vecs, expected_dim)


def compute_embedding(content: str, max_tokens: int = 512) -> list[float] | None:
    """Compute embedding for a single text. Returns the model's vector or None.

    Truncates to max_tokens before encoding to prevent OOM.
    Returns None if model unavailable or encoding fails. The WIDTH is the
    encoder's own — a store that has a width-fixing vector index applies its
    own constraint via :func:`encode_for_store` (#4280).

    Delegates to :func:`compute_embeddings` so the single and batched forms
    share one composition and can never diverge (#4194).
    """
    return compute_embeddings([content], max_tokens)[0]


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
