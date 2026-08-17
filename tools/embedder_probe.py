"""tools/embedder_probe.py — probe injection seam for the #1349 embedder selection.

Enables the benchmark harness to run any of the 4 candidate embedding models
(and, later, rotate the production embedder) WITHOUT touching production code.

Mechanism: ``tortoise.embeddings.EmbeddingModel._load()`` resolves
``SentenceTransformer`` at CALL time via a function-local
``from sentence_transformers import SentenceTransformer`` (embeddings.py:108).
:func:`inject_model` replaces the ``SentenceTransformer`` attribute on the
``sentence_transformers`` module with a factory that constructs the candidate
model (revision-pinned when a pin is recorded), then resets the
``EmbeddingModel`` singleton so the next ``get()`` loads the candidate.
:func:`reset` restores the original symbol and the singleton.

HARD FAIL contract: if the candidate fails to load (missing cache under
HF_HUB_OFFLINE, timeout, wrong dimension) :func:`inject_model` RAISES
:class:`EmbedderProbeError` — the harness must abort rather than silently
degrade to the TF-IDF fallback that ``EmbeddingModel``/``_encode`` otherwise
provide.

Warm-process guarantee: if the singleton was already loaded in-process before
injection, a discriminating-embedding check (cosine distance between a
reference vector captured from the old model and the post-injection vector on
the same text) verifies the swap GENUINELY took effect — identical vectors
mean the singleton served a stale model, which is a HARD FAIL. The check is
skipped when the previously loaded model is the same model being injected
(vectors would legitimately be identical).
"""
from __future__ import annotations

import importlib
import threading
from typing import Any

import numpy as np

from tortoise.embeddings import EmbeddingModel

#: Short name → HF model id. Revision pins use the ``id@<commit>`` form —
#: parsed by :func:`_split_pin`; the resolved revision is recorded at load.
#: No commit pins are recorded in-repo yet (research/scoping docs carry none);
#: pin entries via ``@<commit>`` before an evidence burn once the commits are
#: resolved (HF tags are mutable — same-revision is asserted across the 6
#: config runs by T4's manifest check).
PROBE_MODELS: dict[str, str] = {
    "minilm": "sentence-transformers/all-MiniLM-L6-v2",
    "arctic-xs": "snowflake/snowflake-arctic-embed-xs",
    "arctic-s": "snowflake/snowflake-arctic-embed-s",
    "bge-small": "BAAI/bge-small-en-v1.5",
}

#: The production literal resolved by EmbeddingModel._load() (embeddings.py).
DEFAULT_MODEL_ID = "all-MiniLM-L6-v2"

#: All candidates are 384-dim; anything else is a HARD FAIL.
EXPECTED_DIM = 384

#: Cosine at/above this between pre- and post-injection vectors on the
#: discriminator text ⇒ the swap did not take effect (stale singleton).
WARM_SWAP_COSINE_TOL = 0.9995

#: Fixed text used for the discriminating warm-process swap check. Long and
#: topical so different embedders produce measurably different vectors.
_DISCRIMINATOR_TEXT = (
    "The tortoise graph engine stores epistemic claims as nodes with evidence "
    "propagation across sessions, speakers, and lenses; cosine thresholds are "
    "calibrated per embedding model, and the search path must never silently "
    "degrade to lexical matching when a requested model is unavailable."
)

_state: dict[str, Any] | None = None        # last successful injection
_patch: tuple[Any, Any, Any] | None = None  # (patched module, original, wrapper)
_lock = threading.Lock()


class EmbedderProbeError(RuntimeError):
    """HARD FAIL — an injected embedder could not be loaded or verified.

    Raised instead of silently degrading to the TF-IDF fallback.
    """


class _CandidateFactory:
    """Callable that replaces ``sentence_transformers.SentenceTransformer``.

    While active, every construction routes to the candidate model id, so
    ``EmbeddingModel._load()`` (function-local import, call-time resolution)
    loads the candidate. ``revision`` is forced to the pinned commit when one
    is recorded; ``query_prompt`` is threaded as ``prompt_name`` (the arctic
    vendor-config re-validation path — sentence-transformers applies the named
    prompt template during encode).
    """

    def __init__(self, original: Any, hf_id: str,
                 revision: str | None, query_prompt: str | None):
        self._original = original
        self._hf_id = hf_id
        self._revision = revision
        self._query_prompt = query_prompt

    def __call__(self, model_name_or_path: str, *args: Any, **kwargs: Any) -> Any:
        kwargs = dict(kwargs)
        if self._revision is not None:
            kwargs["revision"] = self._revision
        if self._query_prompt is not None and "prompt_name" not in kwargs:
            kwargs["prompt_name"] = self._query_prompt
        return self._original(self._hf_id, *args, **kwargs)


def inject_model(name: str, query_prompt: str | None = None) -> dict[str, Any]:
    """Inject candidate ``name`` as the active embedding model.

    Args:
        name: short name in :data:`PROBE_MODELS`.
        query_prompt: optional named prompt template threaded to the model
            (e.g. ``"query"`` for the snowflake-arctic vendor config).

    Returns:
        The recorded probe state: ``{name, hf_id, resolved_revision,
        query_prompt, dim}``.

    Raises:
        KeyError: unknown candidate name.
        EmbedderProbeError: the candidate failed to load, loaded with the
            wrong dimension, or a warm-process swap did not take effect.
    """
    global _state, _patch
    if name not in PROBE_MODELS:
        raise KeyError(f"unknown probe model {name!r} — known: {sorted(PROBE_MODELS)}")
    with _lock:
        if _patch is not None:
            _unpatch()  # re-inject over an active injection: drop the wrapper first
        hf_id, revision = _split_pin(PROBE_MODELS[name])
        prev_id = _state["hf_id"] if _state is not None else DEFAULT_MODEL_ID

        wrapper = None
        try:
            try:
                st = importlib.import_module("sentence_transformers")
            except ImportError as e:
                raise EmbedderProbeError(
                    "sentence-transformers not installed — refusing to degrade; "
                    "install the [embeddings] extra"
                ) from e
            original = st.SentenceTransformer

            # Warm-process baseline: if a model is already loaded and it is a
            # DIFFERENT model than the candidate, capture a reference vector so we
            # can prove the swap actually happened (stale-singleton detection).
            # Introspect instead of EmbeddingModel.get() so a cold process is not
            # forced to load the default model just to produce a baseline.
            old_model = _warm_model()
            ref_vec = None
            if old_model is not None and _base_name(prev_id) != _base_name(hf_id):
                try:
                    ref_vec = _encode_vec(old_model, _DISCRIMINATOR_TEXT)
                except Exception:  # noqa: BLE001 — no baseline ⇒ skip the check
                    ref_vec = None

            wrapper = _CandidateFactory(original, hf_id, revision, query_prompt)
            st.SentenceTransformer = wrapper
            _patch = (st, original, wrapper)
            EmbeddingModel._reset()  # clear singleton + failure cooldown
            model = EmbeddingModel.get()
            if model is None:
                raise EmbedderProbeError(
                    f"probe model {name!r} ({hf_id}) FAILED TO LOAD — refusing "
                    "to degrade to TF-IDF; download the model and check the HF "
                    "cache (HF_HUB_OFFLINE)"
                )
            dim = _model_dim(model)
            if dim != EXPECTED_DIM:
                raise EmbedderProbeError(
                    f"probe model {name!r} ({hf_id}) loaded with dim {dim}, "
                    f"expected {EXPECTED_DIM}"
                )
            try:
                new_vec = _encode_vec(model, _DISCRIMINATOR_TEXT)
            except Exception as e:  # noqa: BLE001
                raise EmbedderProbeError(
                    f"probe model {name!r} ({hf_id}) failed to encode: {e}"
                ) from e
            if ref_vec is not None:
                sim = float(_cosine(new_vec, ref_vec))
                if sim > WARM_SWAP_COSINE_TOL:
                    raise EmbedderProbeError(
                        f"warm-process injection of {name!r} did not genuinely "
                        f"swap the singleton: cosine(old, new)={sim:.4f} "
                        f"(>{WARM_SWAP_COSINE_TOL}) — a stale model may be served"
                    )
            _state = {
                "name": name,
                "hf_id": hf_id,
                "resolved_revision": _resolved_revision(model),
                "query_prompt": query_prompt,
                "dim": dim,
            }
            return dict(_state)
        except Exception:
            _unpatch()
            EmbeddingModel._reset()
            _state = None
            raise


def reset() -> None:
    """Restore pristine state: unpatch the module symbol, clear the singleton
    and failure cooldown, drop the recorded injection state."""
    with _lock:
        _unpatch()
        EmbeddingModel._reset()
        global _state
        _state = None


def get_state() -> dict[str, Any] | None:
    """Recorded state of the last successful injection (or None)."""
    return dict(_state) if _state is not None else None


def _unpatch() -> None:
    """Restore the original SentenceTransformer symbol if we own the patch."""
    global _patch
    if _patch is None:
        return
    mod, original, wrapper = _patch
    if getattr(mod, "SentenceTransformer", None) is wrapper:
        mod.SentenceTransformer = original
    _patch = None


def _split_pin(entry: str) -> tuple[str, str | None]:
    """Split ``id@<commit>`` → (hf_id, revision); revision None when unpinned."""
    if "@" in entry:
        hf_id, _, revision = entry.partition("@")
        return hf_id, revision
    return entry, None


def _base_name(hf_id: str) -> str:
    """Last path component of an HF id — normalizes ``all-MiniLM-L6-v2`` vs
    ``sentence-transformers/all-MiniLM-L6-v2`` to the same model identity."""
    return hf_id.split("/")[-1]


def _warm_model() -> Any | None:
    """Currently loaded singleton model WITHOUT triggering a load (cold ⇒ None)."""
    inst = EmbeddingModel._instance
    if inst is not None and inst._model is not None:
        return inst._model
    return None


def _model_dim(model: Any) -> int | None:
    """Best-effort embedding dimension of the loaded model."""
    for meth in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
        fn = getattr(model, meth, None)
        if callable(fn):
            try:
                dim = int(fn())
                if dim:
                    return dim
            except Exception:  # noqa: BLE001
                continue
    try:
        vec = np.asarray(model.encode([_DISCRIMINATOR_TEXT]))
        return int(vec.shape[-1])
    except Exception:  # noqa: BLE001
        return None


def _resolved_revision(model: Any) -> str | None:
    """Best-effort resolved commit for the loaded model (HF cache metadata)."""
    if model is None:
        return None
    direct = getattr(model, "_commit_hash", None)
    if direct:
        return str(direct)
    try:
        for _mod_name, module in model.named_modules():
            cfg = getattr(module, "config", None)
            commit = getattr(cfg, "_commit_hash", None)
            if commit:
                return str(commit)
        mcd = getattr(model, "model_card_data", None)
        rev = getattr(mcd, "base_model_revision", None)
        if rev:
            return str(rev)
    except Exception:  # noqa: BLE001
        pass
    return None


def _encode_vec(model: Any, text: str) -> np.ndarray:
    vec = np.asarray(model.encode([text]))
    return vec[0] if vec.ndim > 1 else vec


def _cosine(a: np.ndarray, b: np.ndarray) -> float:
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))
