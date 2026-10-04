"""Content-addressed, persisted kind-embedding index (issue #1695, Task 3).

The classify-later layer's retrieval surface: every classifiable kind
(core §5 objects + subjects + points + events + pack kindDefs with
description/synonyms/examples/nearMisses) embedded with the production
embedder (``BAAI/bge-small-en-v1.5`` via ``tortoise.embeddings``), persisted
to ``data/kind_index/<sha256(manifest-hash+core-version+embedder-id)>.npz``,
recomputed on hash change (a pack install or core-vocabulary change rotates
the cache key), and loaded on demand.

Design constraints (from the plan):

- **No torch at module level** — the encoder (sentence-transformers via
  ``EmbeddingModel``) imports lazily, inside ``build``/``encode``.
- **Embedder None is the caller's problem** — ``EmbeddingModel.get()``
  returning None degrades to the TF-IDF fallback (``embeddings._encode``),
  and the CLASSIFIER owns the fail-open path (family fallback); the index
  just refuses to build with a degraded marker.
- **Load-once memoization** like ``_MASTER_LIST_CACHE`` (extractor_v2).
- **Injectable encoder seam** — tests pass a stub encoder (fixed numpy
  fixture vectors); production defaults to the ``EmbeddingModel`` singleton
  (never re-instantiated).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
import threading
from pathlib import Path

import numpy as np

from tortoise._gate_memo import GateMemo
from tortoise.embeddings import (
    EMBEDDING_MODEL,
    EMBEDDING_MODEL_REVISION,
)
from tortoise.heavy_imports import import_tfidf_vectorizer  # #5718 lock-taking helper

#: The persisted-index directory (gitignored — see .gitignore).
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "kind_index"

#: Cap on the memoized indexes. Deliberately SMALLER than
#: ``_gate_memo._MAX_GATE_MEMOS`` (64): an index entry holds a full float64
#: ``vectors`` + ``_norm`` matrix (~0.5 MB at the current 91-kind core+pack
#: baseline, larger as packs add kinds), where a brief/spec entry is
#: kilobytes — so the resident set is bounded harder here.
_MAX_MEMOIZED_INDEXES = 16

#: Load-once memoized built indexes: cache_key → KindIndex.
#:
#: BOUNDED + LRU (#5339 review): #5163 threads ``installed_namespaces`` into
#: ``compile_kind_index_spec``, so the spec — and therefore this content-hash
#: key — now varies per graph. Each entry holds the full float64 ``vectors`` +
#: ``_norm`` matrices for one gate, and the key space is tenant-growable, so an
#: uncapped memo accumulates one index per distinct installed-namespace set for
#: the process lifetime. The shared ``GateMemo`` primitive
#: (``tortoise/_gate_memo.py``) owns the atomic LRU evict-and-insert invariant
#: so it lives in ONE place, not a sixth hand-rolled copy; its ``discard``
#: covers this cache's targeted pop (the degraded-index path). Eviction drops
#: the in-memory copy only; a later ``load`` re-reads or rebuilds it.
#:
#: The persisted ``data/kind_index/*.npz`` files are STILL unbounded on disk —
#: one per distinct gate, and nothing sweeps them — tracked by #7351. Do not
#: read this memo's cap as covering the disk.
_INDEX_CACHE = GateMemo(maxsize=_MAX_MEMOIZED_INDEXES)


def _memoize_index(key: str, idx: KindIndex) -> None:
    """Memoize under the shared ``GateMemo`` (#5339 review): lock-guarded and
    never raising, so a concurrent capture worker cannot lose a check-then-pop
    race out of the classifier."""
    _INDEX_CACHE.put_if_absent(key, idx)


def _clear_index_cache() -> None:
    """Test hook — clear the memoized indexes (cross-test isolation)."""
    _INDEX_CACHE.clear()


def _evict_index(key: str) -> None:
    """Evict ONE memoized index (cycle-3 P2 degraded-path hook): the
    classifier drops a good-dim memo entry when the embedder goes down
    mid-process so the degraded rebuild can't be shadowed by it."""
    _INDEX_CACHE.discard(key)


def cache_key_for(spec: dict) -> str:
    """Content-addressed cache key: sha256 of the canonical spec JSON + the
    embedder id + its pinned HF revision. A pack install, core-vocabulary
    change, or embedder rotation rotates the key → the index recomputes."""
    payload = (
        json.dumps(spec, sort_keys=True, default=str)
        + "|"
        + EMBEDDING_MODEL
        + "|"
        + EMBEDDING_MODEL_REVISION
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


#: Module-level TF-IDF degrade vectorizer — fitted ONCE on first use (the
#: index build's spec texts) and reused for per-item encodes so the degrade
#: lane is DIMENSION-STABLE across the index and the items (a per-call
#: refit would mismatch the vector spaces and silently no-op the classifier
#: whenever the embedder is down — review P2, #1695 Task 5).
_TFIDF = None
_TFIDF_LOCK = threading.Lock()


def _reset_tfidf() -> None:
    """Test hook — clear the shared degrade vectorizer."""
    global _TFIDF
    with _TFIDF_LOCK:
        _TFIDF = None


class _DefaultEncoder:
    """Production encoder: routes through the EmbeddingModel singleton
    (never re-instantiates the model) with a dimension-stable TF-IDF
    degrade fallback (one shared vectorizer).

    ``encode(texts)`` → ``(vectors: np.ndarray, degraded: bool)``.
    """

    def encode(self, texts: list[str]) -> tuple[np.ndarray, bool]:
        global _TFIDF
        from tortoise.embeddings import EmbeddingModel
        model = EmbeddingModel.get()
        if model is not None:
            try:
                vecs = model.encode(texts, show_progress_bar=False)
                if vecs is not None and len(vecs) > 0:
                    return np.asarray(vecs, dtype=np.float64), False
            except Exception:  # degrade path
                pass
        TfidfVectorizer = import_tfidf_vectorizer()  # lazy + serialized: [embeddings] extra
        with _TFIDF_LOCK:
            if _TFIDF is None:
                _TFIDF = TfidfVectorizer()
                return _TFIDF.fit_transform(texts).toarray(), True
            return _TFIDF.transform(texts).toarray(), True


class KindIndex:
    """The kind → embedding matrix + metadata for the classifier.

    Build from ``compile_kind_index_spec()`` (``tortoise.value_extractor``),
    persist/load content-addressed npz, memoized per cache key.
    """

    def __init__(
        self, kind_names: list[str], vectors: np.ndarray, metadata: dict, *, degraded: bool = False
    ) -> None:
        self.kind_names = list(kind_names)
        self.vectors = np.asarray(vectors, dtype=np.float64)
        self.metadata = metadata  # kind → spec dict (text/synonyms/...)
        self.degraded = degraded
        norms = np.linalg.norm(self.vectors, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        self._norm = self.vectors / norms

    # ── construction ───────────────────────────────────────────────────────

    @classmethod
    def build(
        cls, spec: dict, *, encoder=None, persist: bool = True, cache_dir: Path | str | None = None
    ) -> KindIndex:
        """Build (and optionally persist) the index for the given spec.

        ``encoder`` is the injectable seam (default: the production
        ``EmbeddingModel`` singleton + TF-IDF degrade). The load-once memo
        applies to DEFAULT-encoder builds only — an injected stub encoder
        changes the vector space, so its builds are never memoized (a
        stub build must never shadow a production index under the same
        spec key, and vice versa). A memo-hit with a NEW persist target
        still writes there.
        """
        key = cache_key_for(spec)
        memoize = encoder is None
        if memoize:
            cached = _INDEX_CACHE.get(key)
            if cached is not None:
                if persist:
                    target = KindIndex._path_for(key, cache_dir)
                    if not target.exists():
                        cached.persist(cache_dir=cache_dir)
                return cached
        enc = encoder or _DefaultEncoder()
        kind_names = sorted(spec)
        texts = [spec[k]["text"] for k in kind_names]
        vectors, degraded = enc.encode(texts)
        idx = cls(kind_names, vectors, {k: dict(spec[k]) for k in kind_names}, degraded=degraded)
        if persist:
            idx.persist(cache_dir=cache_dir)
        if memoize:
            _memoize_index(key, idx)
        return idx

    @classmethod
    def load(
        cls, spec: dict, *, cache_dir: Path | str | None = None, encoder=None
    ) -> KindIndex | None:
        """Load the persisted index for this spec's cache key; None when the
        file is missing OR the stored index was built DEGRADED (embedder was
        down — never trust a degraded persisted index; the caller rebuilds
        in-process, cycle-3 P2). A memoized DEGRADED entry is popped and
        treated as a miss (FIX-N: the process memo must not pin a degraded
        build — a recovered embedder rebuilds good, persist=True). Encoder
        is only used when building."""
        key = cache_key_for(spec)
        cached = _INDEX_CACHE.get(key)
        if cached is not None:
            if cached.degraded:
                # FIX-N: a degraded index memoized in-process (embedder
                # was down during its build) must NOT stick — the disk
                # guard below can't cover the memo path, and every
                # classify_items would fall back for the process
                # lifetime. Pop it and treat as a miss so a recovered
                # embedder rebuilds good (persist=True).
                _INDEX_CACHE.discard(key)
            else:
                return cached
        path = cls._path_for(key, cache_dir)
        if not path.exists():
            return None
        with np.load(path, allow_pickle=False) as data:
            if bool(data.get("degraded", False)):
                # A persisted index built while the embedder was DOWN
                # (degraded vectors) is never trusted — return None so the
                # caller rebuilds (cycle-3 P2 + FIX-N: a degraded npz must
                # not load forever after the embedder recovers, and the
                # in-process rebuild is dimension-matched with the degraded
                # item lane — recovery rebuilds good).
                return None
            idx = cls(
                [str(x) for x in data["kind_names"]],
                data["vectors"],
                json.loads(str(data["metadata"])),
                degraded=False,
            )
        _memoize_index(key, idx)
        return idx

    # ── persistence ────────────────────────────────────────────────────────

    @staticmethod
    def _path_for(key: str, cache_dir: Path | str | None = None) -> Path:
        return (Path(cache_dir) if cache_dir else DEFAULT_CACHE_DIR) / f"{key}.npz"

    def persist(self, cache_dir: Path | str | None = None) -> Path:
        """Write ``data/kind_index/<key>.npz`` (content-addressed, atomic:
        UNIQUE temp-file + rename, so a crash mid-save never leaves a corrupt
        index, and two concurrent writers for the same key cannot collide on
        the temp path — a fixed ``.<key>.tmp.npz`` made the loser's ``replace``
        hit an already-renamed source and raise ``FileNotFoundError`` out of
        ``build``; #5339 review. kind_names stored as a unicode array — no
        pickle)."""
        key = cache_key_for(self._spec_of())
        path = self._path_for(key, cache_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        # mkstemp hands each writer its OWN temp path (and the suffix ends in
        # .npz so savez does not append a second one).
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp.npz")
        tmp = Path(tmp_name)
        try:
            # mkstemp creates 0600. Reproduce the pre-#5339 mode for a fresh
            # index (np.savez's ``0o666 & ~umask``), or keep the mode of an
            # index ALREADY on disk clamped by the umask — so a shared cache
            # dir keeps group/other read (mirrors
            # ``hook_install._atomic_write_text``) without ever widening past
            # the operator's umask, and without keeping a world-writable index
            # world-writable (#5339 review).
            umask = os.umask(0)
            os.umask(umask)
            try:
                base = path.stat().st_mode & 0o777
            except OSError:
                # Raced away (or absent): ``exists()``+``stat()`` would raise
                # here with mkstemp's temp already created and the unlink
                # below unreachable (mirrors hook_install's guarded stat).
                base = 0o666
            os.fchmod(fd, base & ~umask)
            np.savez(
                tmp,
                kind_names=np.asarray(self.kind_names, dtype=str),
                vectors=self.vectors,
                metadata=json.dumps(self.metadata, default=str),
                degraded=np.asarray(self.degraded),
            )
            tmp.replace(path)
        finally:
            # Close mkstemp's fd (np.savez re-opened the path itself), and a
            # failed save or a raised mode step leaves no stray temp behind;
            # after a successful replace ``tmp`` no longer exists.
            with contextlib.suppress(OSError):
                os.close(fd)
            if tmp.exists():
                with contextlib.suppress(OSError):
                    tmp.unlink()
        return path

    def _spec_of(self) -> dict:
        """Reconstruct a spec dict from this index (for the cache key)."""
        return {k: self.metadata[k] for k in self.kind_names}

    # ── classifier surface ─────────────────────────────────────────────────

    def nearest(
        self, vector: np.ndarray, k: int = 5, *, restrict: list[str] | None = None
    ) -> list[tuple[str, float]]:
        """Top-k kind candidates for one item vector (cosine, normalized).

        ``restrict`` — optional per-type candidate list (entities →
        object+subject kinds, events → event kinds, points → point kinds +
        statement); the closed-vocab gate lives in the classifier.
        """
        v = np.asarray(vector, dtype=np.float64).reshape(1, -1)
        nv = v / (np.linalg.norm(v) or 1.0)
        sims = self._norm @ nv.T
        sims = sims[:, 0]
        order = np.argsort(-sims, kind="stable")  # stable: equal ties keep index order
        out: list[tuple[str, float]] = []
        restrict_set = {r.lower() for r in restrict} if restrict else None
        for j in order:
            kind = self.kind_names[int(j)]
            if restrict_set is not None and (
                kind.lower() not in restrict_set
                and kind.lower().rsplit(":", 1)[-1] not in restrict_set
            ):
                continue
            out.append((kind, float(sims[int(j)])))
            if len(out) >= k:
                break
        return out

    def near_misses(self, kind: str) -> set[str]:
        """The kind's declared nearMisses (namespaced resolution: a bare
        nearMiss ref resolves same-namespace-first, then any namespace —
        never guess across namespaces when the pack's own scope matches)."""
        md = self.metadata.get(kind, {})
        refs = set(md.get("nearMisses") or [])
        resolved: set[str] = set()
        ns = kind.rsplit(":", 1)[0] + ":" if ":" in kind else ""
        for r in refs:
            if r in self.metadata:
                resolved.add(r)
                continue
            same_ns = [k for k in self.metadata
                       if k.rsplit(":", 1)[-1].lower() == r.lower()
                       and (not ns or k.startswith(ns))]
            if same_ns:
                resolved.add(same_ns[0])
                continue
            for k in self.metadata:
                if k.rsplit(":", 1)[-1].lower() == r.lower():
                    resolved.add(k)
        return resolved

    def __len__(self) -> int:
        return len(self.kind_names)
