"""tools/longmem_eval/encode_cache.py — model-keyed disk-persisted encode cache.

The LongMemEval burn re-ingests overlapping haystack content per question
(5-10× redundant encodes). This cache makes the burn feasible by memoizing
``content → embedding`` on disk — a mid-burn crash must not lose the
cross-question reuse the cache exists for.

Keying: ``sha256(model_id + prompt_name + text)`` — the model component
prevents cross-model contamination (a content-hash-only cache would let
MiniLM vectors serve arctic runs); the prompt component separates the
arctic query/document prompt configs. The cache FILE is additionally
namespaced per (model, prompt) under the cache root.

The cache intercepts the ingest-time encode path by wrapping
``tortoise.embeddings.compute_embedding`` (the module attribute that
``create_point`` resolves at call time — same seam the repo's own tests
monkeypatch); query encoding routes through :func:`encode_query` when a
cache is active. Concurrency model: sequential workers (the runner's
simplest correct choice) — no cross-process locking on the cache file.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

#: Atomic-persist threshold: flush to disk after this many new entries so a
#: crash loses at most the last few encodes (JSON cannot append in place).
_SAVE_EVERY_N = 25


def _slug(value: str | None) -> str:
    """Filesystem-safe slug for the per-config namespace (no path separators)."""
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(value or "").strip()) or "default"
    return s[:60]


def cache_path_for(cache_root: Path | str, model: str, prompt: str | None) -> Path:
    """Per-config cache file path: namespaced by (model, prompt)."""
    root = Path(cache_root).expanduser()
    return root / "encode-cache" / f"{_slug(model)}__{_slug(prompt)}" / "cache.json"


class EncodeCache:
    """Disk-persisted ``text → embedding`` memo, model-keyed.

    Args:
        path: cache JSON file location (see :func:`cache_path_for`).
        model_id: the embedding model identity (HF id) — part of every key so
            two models can never share entries.
        prompt_name: the active prompt (``--query-prompt``) — part of the key
            (arctic query vs default configs are distinct encode surfaces).
    """

    def __init__(self, path: Path | str, model_id: str,
                 prompt_name: str | None = None):
        self.path = Path(path).expanduser()
        self.model_id = str(model_id)
        self.prompt_name = prompt_name
        self._data: dict[str, list[float]] = {}
        self._dirty_since_save = 0
        self._lock = threading.Lock()
        self._load()

    # ── keying ────────────────────────────────────────────────────────────
    def key_for(self, text: str) -> str:
        """``sha256(model_id + prompt_name + text)`` — the model component is
        part of the key material (cross-model contamination impossible)."""
        material = f"{self.model_id}\u0000{self.prompt_name or ''}\u0000{text}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    # ── API ───────────────────────────────────────────────────────────────
    def get(self, text: str) -> list[float] | None:
        key = self.key_for(text)
        with self._lock:
            vec = self._data.get(key)
        return list(vec) if vec is not None else None

    def put(self, text: str, vec: list[float]) -> None:
        key = self.key_for(text)
        with self._lock:
            if key in self._data:
                return
            self._data[key] = [float(v) for v in vec]
            self._dirty_since_save += 1
        if self._dirty_since_save >= _SAVE_EVERY_N:
            self.save()

    def size(self) -> int:
        with self._lock:
            return len(self._data)

    def save(self) -> None:
        """Atomic temp-file-then-rename persist (a crash never leaves a
        truncated cache file)."""
        with self._lock:
            payload = json.dumps({
                "format": "lme-encode-cache-v1",
                "model_id": self.model_id,
                "prompt_name": self.prompt_name,
                "entries": self._data,
            })
            self._dirty_since_save = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self.path)

    # ── ingest interception ───────────────────────────────────────────────
    @contextmanager
    def active(self):
        """Wrap ``tortoise.embeddings.compute_embedding`` so every ingest-time
        encode consults this cache first; restores + flushes on exit."""
        global _ACTIVE_CACHE
        import tortoise.embeddings as emb

        original = emb.compute_embedding
        cache = self

        def _cached(content: str, max_tokens: int = 512) -> list[float] | None:
            hit = cache.get(content)
            if hit is not None:
                return hit
            vec = original(content, max_tokens=max_tokens)
            if vec is not None:
                cache.put(content, vec)
            return vec

        prev = _ACTIVE_CACHE
        _ACTIVE_CACHE = cache
        emb.compute_embedding = _cached
        try:
            yield cache
        finally:
            emb.compute_embedding = original
            _ACTIVE_CACHE = prev
            cache.save()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            entries = raw.get("entries", {})
            # Per-file namespace is per (model, prompt) already; the model_id
            # recorded inside is a second line of defense — refuse a file that
            # claims a different model (never serve another model's vectors).
            if raw.get("format") != "lme-encode-cache-v1" or \
                    raw.get("model_id") != self.model_id or \
                    raw.get("prompt_name") != self.prompt_name:
                return
            self._data = {
                k: [float(v) for v in vec]
                for k, vec in entries.items()
                if isinstance(vec, list)
            }
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            # Corrupt cache file → treat as empty (re-encode; never crash).
            self._data = {}


_ACTIVE_CACHE: EncodeCache | None = None


def encode_query(model: Any, text: str) -> list[float]:
    """Encode a single query through the active cache (if any) or the model
    directly. Mirrors ``compute_embedding``'s encode shape (``[text]`` → first
    vector) — the query prompt is the injected model's active prompt."""
    cache = _ACTIVE_CACHE
    if cache is not None:
        hit = cache.get(text)
        if hit is not None:
            return hit
    vec = model.encode([text])
    if vec is None or len(vec) == 0:
        raise ValueError("model.encode returned no vectors — cannot encode query")
    out = vec[0].tolist() if getattr(vec, "ndim", 1) > 1 else list(vec)
    if cache is not None and out:
        cache.put(text, out)
    return out
