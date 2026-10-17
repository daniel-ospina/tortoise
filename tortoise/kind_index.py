"""Content-addressed, persisted kind-embedding index (issue #1695, Task 3).

The classify-later layer's retrieval surface: every classifiable kind
(core §5 objects + subjects + points + events + pack kindDefs with
description/synonyms/examples/nearMisses) embedded with the production
embedder (``BAAI/bge-small-en-v1.5`` via ``tortoise.embeddings``), persisted
to ``data/kind_index/<sha256(manifest-hash+core-version+embedder-id)>.npz``,
recomputed on hash change (a pack install or core-vocabulary change rotates
the cache key), and loaded on demand.

The directory is a DERIVED cache: every file can be regenerated from its
spec, so a wholesale delete is always safe and the persisted set is bounded
(``_MAX_PERSISTED_INDEXES``, evicted least-recently-used on every ``persist``
— #7351). It is not customer data and carries no retention window.

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
import threading
import uuid
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
#: The persisted ``data/kind_index/*.npz`` set has the SAME tenant-growable
#: key space ON DISK and is bounded separately (``_MAX_PERSISTED_INDEXES``,
#: swept on every ``persist`` — #7351). Do not read this memo's cap as
#: covering the disk: the memo is per-process and dies with the process,
#: while the npz set is shared across workers and survives restarts.
_INDEX_CACHE = GateMemo(maxsize=_MAX_MEMOIZED_INDEXES)

#: Cap on the number of PERSISTED indexes in the cache directory (#7351).
#: Mirrors ``_MAX_MEMOIZED_INDEXES`` because the key space is the same one:
#: #5163 threads ``installed_namespaces`` into the spec, so every distinct
#: gate is its own content-addressed key, and ``persist`` wrote a new npz for
#: each one — with no ``unlink`` anywhere in this module, the directory grew
#: until ENOSPC. That host is ``/app/data/kind_index`` (``DEFAULT_CACHE_DIR``),
#: NOT the Fly ``/data`` volume, and ``auto_stop_machines = "off"`` never
#: discards the rootfs. An index is ~0.3 MB today (99 kinds × 384-dim
#: float64), so the cap holds the directory at ≈5 MB regardless of how many
#: gates a tenant mints. Eviction costs a re-EMBED on the next build of that
#: gate — never a wrong answer — so the lever is safe to raise if the host's
#: live gate count ever justifies it.
_MAX_PERSISTED_INDEXES = 16


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


# ── persisted-set retention (#7351) ────────────────────────────────────────

def _touch_index(path: Path) -> None:
    """Refresh a persisted index's mtime, so the retention sweep evicts by
    least-recently-USED rather than least-recently-WRITTEN (#7351): a gate
    that is read on every boot but written once must not lose its npz to a
    burst of one-off gates (the cache's whole value is surviving restarts).

    Best-effort: a read-only mount or a file the sweep just removed is not an
    error on a read path."""
    with contextlib.suppress(OSError):
        os.utime(path, None)


def _sweep_persisted_indexes(
    directory: Path, *, keep_path: Path, max_files: int | None = None
) -> list[Path]:
    """Evict the least-recently-used persisted indexes beyond the cap
    (#7351), oldest first; returns the paths actually removed.

    A persisted index is content-addressed and purely derived — always
    regenerable from its spec — so eviction cannot lose data; the cost is one
    re-embed the next time that gate's index is built. Two properties make it
    safe to run on the capture write path:

    * **The just-written file is never a candidate** (``keep_path``): the
      caller persists and THEN sweeps, and its own file is the newest —
      asserted explicitly rather than inferred from the clock, so a coarse
      mtime granularity (many ties) can never make ``persist`` evict the file
      it just wrote and re-embed on every call.
    * **In-flight temps are never candidates**: the per-writer save temp is
      dot-prefixed (``.<key>.<pid>.<uuid>.tmp.npz``) and dot-names are
      skipped — unlinking another writer's temp mid-save would break the
      atomic rename it is about to do.

    Everything is best-effort: an unreadable directory, an unstattable entry
    or an ``unlink`` lost to a concurrent sweep is skipped, never raised. A
    host that already accumulated an unbounded directory is trimmed to the cap
    on the FIRST ``persist`` after this ships, in one pass.
    """
    limit = _MAX_PERSISTED_INDEXES if max_files is None else max_files
    try:
        entries = [p for p in directory.iterdir()
                   if p.suffix == ".npz" and not p.name.startswith(".")
                   and p.is_file()]
    except OSError:
        return []
    if len(entries) <= limit:
        return []

    def _recency(path: Path) -> tuple[float, str]:
        try:
            return (path.stat().st_mtime, path.name)
        except OSError:
            # Unstattable ⇒ oldest, so it is evicted before a usable file.
            return (0.0, path.name)

    evicted: list[Path] = []
    for path in sorted(entries, key=_recency, reverse=True)[limit:]:
        if path == keep_path:
            continue
        try:
            path.unlink()
        except OSError:
            continue  # already gone (concurrent sweep) or unremovable
        evicted.append(path)
    return evicted


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
        try:
            data = np.load(path, allow_pickle=False)
        except FileNotFoundError:
            # The retention sweep (#7351) evicted this file between the
            # exists() check and the open. A miss, not an error: the caller
            # rebuilds (one re-embed), so a racing sweep can never surface as
            # a failure out of the classifier.
            return None
        with data:
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
        _touch_index(path)  # access recency for the retention sweep (#7351)
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
        pickle).

        The temp is created with ``os.open(..., 0o666)``, so the KERNEL applies
        the process umask and the persisted file keeps the mode the pre-#5339
        ``np.savez`` produced (``0o666 & ~umask``); ``tempfile.mkstemp``'s
        forced 0600 would have narrowed it. The umask is never read: it is
        process-global, so an ``os.umask(0)``/restore dance on this path either
        races across the capture pool or leaks umask 0 on an interrupt.

        Bounded set (#7351): after a SUCCESSFUL replace, the directory is swept
        down to ``_MAX_PERSISTED_INDEXES`` least-recently-used files. This is
        the only place the persisted set grows, so it is the only place it
        needs bounding; a FAILED save evicts nothing.
        """
        key = cache_key_for(self._spec_of())
        path = self._path_for(key, cache_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        # A per-writer unique temp in the SAME directory (so ``replace`` is
        # atomic). ``O_EXCL`` on a uuid4 name keeps it collision-free without
        # mkstemp (whose 0600 mode is the thing being avoided).
        tmp_name = path.parent / (
            f".{path.stem}.{os.getpid()}.{uuid.uuid4().hex}.tmp.npz")
        fd = os.open(tmp_name, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o666)
        tmp = Path(tmp_name)
        try:
            # close cannot be retried on EINTR (PEP 475), and a close failure
            # must still reach the unlink below.
            with contextlib.suppress(OSError):
                os.close(fd)
            np.savez(
                tmp,
                kind_names=np.asarray(self.kind_names, dtype=str),
                vectors=self.vectors,
                metadata=json.dumps(self.metadata, default=str),
                degraded=np.asarray(self.degraded),
            )
            tmp.replace(path)
        finally:
            # A failed save leaves no stray temp behind; after a successful
            # replace ``tmp`` no longer exists.
            if tmp.exists():
                with contextlib.suppress(OSError):
                    tmp.unlink()
        # #7351 — bound the set at its single growth point. Reached only after
        # the replace above succeeded, so a failed save never evicts a peer.
        _sweep_persisted_indexes(path.parent, keep_path=path)
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
