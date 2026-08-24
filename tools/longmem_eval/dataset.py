"""LongMemEval dataset loading (issue #1144, axis 2).

Loads the official LongMemEval benchmark from HuggingFace
(``xiaowu0162/longmemeval-cleaned`` — the canonical, cleaned replacement for
the deprecated ``xiaowu0162/longmemeval``; verified via web search 2026-08-17)
or from a local JSON/JSONL path. Downloads with the stdlib ``urllib`` (no
datasets dependency — mirrors ``tortoise/models.py`` transport style).

Dataset format (official README):
    Each instance has: question_id, question_type (one of
    ``single-session-user|single-session-assistant|single-session-preference|
    temporal-reasoning|knowledge-update|multi-session``; a ``_abs`` suffix on
    question_id marks an abstention question), question, answer,
    question_date, haystack_session_ids, haystack_dates, haystack_sessions
    (list of sessions; each session is a list of turns
    ``{"role", "content"}`` with optional ``has_answer: true`` on evidence
    turns), answer_session_ids (evidence sessions for session-level recall).

The dataset is large (~500 questions × ~40 sessions, ~115k tokens per
question for the S split) — it is NEVER committed to the repo. The runner
fetches it on demand into a cache dir outside the repository
(``TORTOISE_LME_CACHE_DIR`` or ``~/.cache/tortoise-longmemeval``) or reads a
user-supplied local path.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.request
from pathlib import Path

# Canonical HF dataset id — verified 2026-08-17 (search result: the original
# ``xiaowu0162/longmemeval`` is deprecated and replaced by this cleaned
# version because noisy history sessions interfered with answer correctness).
HUGGINGFACE_DATASET = "xiaowu0162/longmemeval-cleaned"

# Split → remote filename (official data files; see LongMemEval README).
SPLIT_FILES: dict[str, str] = {
    "s": "longmemeval_s_cleaned.json",       # LongMemEval-S: ~40 sessions, ~115k tokens/history
    "m": "longmemeval_m_cleaned.json",       # LongMemEval-M: ~500 sessions/history
    "oracle": "longmemeval_oracle.json",     # oracle retrieval: evidence sessions only
}

# Digest-pin per split (sha256 of the official HF file — the git-LFS oid the
# ``x-linked-etag`` header carries). A truncated/tampered cache must never be
# served as a partial corpus with a silently wrong denominator: load verifies
# the cached file against the pin and re-downloads (or errors) on mismatch.
#   s      — verified against the local authentic 277MB file AND the official
#            HF LFS etag (2026-08-17).
#   m      — official HF LFS etag (2.7GB file — etag IS the LFS sha256 oid).
#   oracle — verified by downloading the official 15MB file (2026-08-17).
SPLIT_DIGESTS: dict[str, str] = {
    "s": "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442",
    "m": "9d79e5524794a2e6900a3aa9cb7d9152c5a3e8319c9a87c25494ba1eacee495f",
    "oracle": "821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c",
}


class DatasetDigestError(ValueError):
    """The dataset file's sha256 does not match the pinned digest — the cache
    is truncated/tampered. Re-download or fail; never serve a partial corpus."""

DEFAULT_CACHE_DIR = "~/.cache/tortoise-longmemeval"

# The S split is the design-locked target (#1144 axis 2: "official 500-Q
# benchmark ... S split").
DEFAULT_SPLIT = "s"


def cache_dir() -> Path:
    """Resolve the dataset cache dir (env override or default, outside repo)."""
    raw = os.environ.get("TORTOISE_LME_CACHE_DIR", "").strip()
    base = Path(raw).expanduser() if raw else Path(DEFAULT_CACHE_DIR).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base


def remote_url(split: str) -> str:
    """HF resolve URL for the given split's data file."""
    if split not in SPLIT_FILES:
        raise ValueError(
            f"split must be one of {sorted(SPLIT_FILES)}, got {split!r}")
    return (f"https://huggingface.co/datasets/{HUGGINGFACE_DATASET}"
            f"/resolve/main/{SPLIT_FILES[split]}")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _verify_digest(path: Path, split: str) -> None:
    """Raise :class:`DatasetDigestError` when the file's sha256 does not match
    the pinned digest for the split (truncated/tampered cache)."""
    expected = SPLIT_DIGESTS[split]
    actual = _sha256_file(path)
    if actual != expected:
        raise DatasetDigestError(
            f"dataset digest mismatch for {split}: expected {expected}, got "
            f"{actual} — the cached file is truncated or tampered; "
            f"re-download or pass a verified file")


def _download(url: str, dest: Path, *, timeout: int = 120) -> Path:
    """Download ``url`` to ``dest`` atomically.

    Streams to a ``<dest>.part`` temp file first (the S split is tens of MB),
    JSON-validates the downloaded bytes AND verifies the pinned sha256, THEN
    atomically renames into place — an interrupted/corrupt download can never
    leave a poisoned "cache" that would be served forever: the partial file is
    cleaned up and the next run re-downloads.
    """
    print(f"[longmem_eval] downloading {url} …", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": "tortoise-longmem-eval"})
    # #1360: a fresh cache dir (first run — default ~/.cache/tortoise-longmemeval
    # doesn't pre-exist) must be auto-created; without this the .part write below
    # crashes with FileNotFoundError.
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    split = next((s for s, f in SPLIT_FILES.items() if f == dest.name), None)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: SIM117
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
        # Validate BEFORE the file can become the cache (raises on corrupt
        # JSON or a digest mismatch, leaving the .part behind for cleanup
        # below). Digest-pin: a tampered/truncated download is discarded, not
        # promoted into the cache (never a partial corpus).
        _read_instances(tmp)
        if split is not None:
            _verify_digest(tmp, split)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def load_dataset(
    split: str = DEFAULT_SPLIT,
    *,
    limit: int | None = None,
    data_path: str | None = None,
    cache: Path | None = None,
    download: bool = True,
) -> list[dict]:
    """Load LongMemEval instances for a split.

    Resolution order:
      1. ``data_path`` — local JSON or JSONL file (no download).
      2. cached copy under the cache dir (per split).
      3. remote download from HuggingFace (if ``download``).

    ``limit`` truncates the returned list (smoke runs). Returns a list of
    question instance dicts (schema documented in the module docstring).
    """
    if split not in SPLIT_FILES:
        raise ValueError(f"split must be one of {sorted(SPLIT_FILES)}, got {split!r}")

    raw: list[dict]
    if data_path:
        p = Path(data_path)
        if not p.is_file():
            raise FileNotFoundError(f"dataset file not found: {p}")
        raw = _read_instances(p)
        source_desc = f"local file {p}"
    else:
        cache_base = cache or cache_dir()
        cached = cache_base / SPLIT_FILES[split]
        if cached.is_file():
            try:
                # Digest-pin first (cheap sha256 < JSON parse): a truncated/
                # tampered cache must re-download or error — never a partial
                # corpus with a silently wrong denominator.
                _verify_digest(cached, split)
                raw = _read_instances(cached)
            except (json.JSONDecodeError, ValueError, UnicodeDecodeError,
                    DatasetDigestError):
                if not download:
                    raise
                print(f"[longmem_eval] cached dataset {cached} is corrupt or "
                      f"digest-mismatched — re-downloading", file=sys.stderr)
                cached.unlink(missing_ok=True)
                _download(remote_url(split), cached)
                raw = _read_instances(cached)
            source_desc = f"cache {cached}"
        elif download:
            _download(remote_url(split), cached)
            raw = _read_instances(cached)
            source_desc = f"downloaded {cached}"
        else:
            raise FileNotFoundError(
                f"no cached dataset at {cached} and download disabled — set "
                "TORTOISE_LME_CACHE_DIR or pass --data <path>")

    instances = raw if limit is None else raw[:limit]
    print(f"[longmem_eval] loaded {len(instances)}/{len(raw)} instances "
          f"(split={split}) from {source_desc}", file=sys.stderr)
    return instances


def _read_instances(path: Path) -> list[dict]:
    """Read a JSON (list) or JSONL (one instance per line) dataset file."""
    text = path.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        data = json.loads(text)
        if not isinstance(data, list):
            raise ValueError(f"{path}: expected a JSON list, got {type(data).__name__}")
        return data
    # JSONL fallback (one object per line; tolerate the official file being a
    # bare array too — handled above).
    instances = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError(f"{path}: JSONL line is not an object: {line[:80]!r}")
        instances.append(obj)
    if not instances:
        raise ValueError(f"{path}: no instances found")
    return instances
