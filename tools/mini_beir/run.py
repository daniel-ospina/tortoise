"""mini-BEIR retrieval-quality research harness (#1349 Task 6).

Independent retrieval-quality signal across four BEIR datasets — a research
surface ONLY (NOT a gate). Feeds the multi-winner tiebreak and the long-term
monitoring baseline.

Datasets (raw BEIR tsv.gz/jsonl via stdlib urllib — NO parquet, NO
``datasets``/``beir`` packages; zero new top-level deps):

    msmarco   — MS MARCO dev, qrels-constrained top-1000 queries, evaluated
                against a DETERMINISTIC 100k-passage sample of the full 8.8M
                corpus (seed 1349, seeded-reservoir). NOT leaderboard-
                comparable (subset corpus) — internal ranking only. Treated
                as IN-DOMAIN sanity (arctic/bge are trained on MS MARCO);
                the OOD datasets carry the selection read.
    nfcorpus  — full corpus, 323 qrels-constrained test queries.
    scifact   — full corpus, 300 qrels-constrained test queries.
    fiqa      — full corpus, 648 qrels-constrained test queries.

Metrics: nDCG@10 + R@10, binary relevance (qrels score > 0, BEIR
convention). nDCG uses the in-repo binary-gain convention (DCG = Σ
rel_i/log₂(i+2); IDCG = all-relevant-first capped at k) — identical to the
LongMemEval gate arm's definition, so the two surfaces stay comparable.

Transport (mirrors tools/longmem_eval/dataset.py): download → verify →
cache → parse, all stdlib. Downloads are ATOMIC (temp .part + rename), the
zip is CRC-validated, extraction is per-file atomic, and every cached file
is DIGEST-PINNED: sha256 pinned in code for nfcorpus/scifact/fiqa (verified
2026-08-17 against the official UKP zips); msmarco's digests are RECORDED
at first verified download in a sidecar (the 1.1GB zip makes an author-time
pin impractical) and every later run verifies the cache against the record.
A corrupt/truncated cache re-downloads or raises a clear error — never a
silently-partial corpus. Empty and 1-passage corpora produce defined output
(nDCG@10 = 0, R@10 = 0), never a crash.

Encoding: ``--model`` → ``tools.embedder_probe.inject_model`` (T1) — the
probe swaps the ``sentence_transformers`` symbol BEFORE the first
``EmbeddingModel.get()``, so the singleton IS the candidate model; queries
and docs encode through it with the same ``--query-prompt`` threading as the
LongMemEval arm (uniform encode via the singleton).

    python -m tools.mini_beir.run --model arctic-s --query-prompt query
    python -m tools.mini_beir.run --model minilm --limit 10        # smoke

Results: one JSON per model written to the output dir
(``tools/mini_beir/results/`` by default) — per-dataset nDCG@10/R@10 means
+ per-query detail + dataset digests + probe state + methodology.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import math
import os
import random
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np

from tools.embedder_probe import PROBE_MODELS, inject_model  # noqa: E402
from tortoise.embeddings import EmbeddingModel  # noqa: E402

#: Rank depth for both metrics (the #1349 mini-BEIR contract).
K = 10

#: Default encode batch size — bounds peak RAM on the 2GB-VM class
#: (100k × 384-dim float32 ≈ 154MB for the msmarco sample).
BATCH_SIZE = 64

DEFAULT_CACHE_DIR = "~/.cache/tortoise-mini-beir"

#: Default output dir for per-model results JSONs (committed per model).
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "results"

#: Deterministic sample for msmarco's 100k-passage corpus.
MSMARCO_SAMPLE = 100_000
MSMARCO_SEED = 1349


class DatasetError(ValueError):
    """A dataset could not be downloaded/verified/parsed — never a partial
    corpus. Raised instead of silently serving truncated data."""


class DatasetDigestError(DatasetError):
    """A cached dataset file's sha256 does not match its pin/record — the
    cache is truncated or tampered. Re-download or fail; never a partial
    corpus."""


#: Remote sources — the official BEIR raw zips (UKP TU-Darmstadt; the BEIR
#: benchmark's canonical raw tsv.gz/jsonl distribution — no parquet).
#: ``files`` are the zip members extracted into the cache; ``query_cap`` is
#: the qrels-constrained query cap (msmarco: top-1000; others: full test
#: set); ``corpus_sample``/``corpus_seed`` configure msmarco's sampled
#: corpus; ``note`` records the contamination read.
DATASETS: dict[str, dict] = {
    "msmarco": {
        "url": ("https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/"
                "datasets/msmarco.zip"),
        "files": ("corpus.jsonl", "queries.jsonl", "qrels/test.tsv"),
        "query_cap": 1000,
        "corpus_sample": MSMARCO_SAMPLE,
        "corpus_seed": MSMARCO_SEED,
        "note": ("IN-DOMAIN sanity only — arctic/bge are trained on MS MARCO; "
                 "the OOD datasets (NFCorpus/SciFact/FiQA) carry the "
                 "selection read"),
    },
    "nfcorpus": {
        "url": ("https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/"
                "datasets/nfcorpus.zip"),
        "files": ("corpus.jsonl", "queries.jsonl", "qrels/test.tsv"),
        "query_cap": None,
        "note": "OOD — carries the selection read",
    },
    "scifact": {
        "url": ("https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/"
                "datasets/scifact.zip"),
        "files": ("corpus.jsonl", "queries.jsonl", "qrels/test.tsv"),
        "query_cap": None,
        "note": "OOD — carries the selection read",
    },
    "fiqa": {
        "url": ("https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/"
                "datasets/fiqa.zip"),
        "files": ("corpus.jsonl", "queries.jsonl", "qrels/test.tsv"),
        "query_cap": None,
        "note": "OOD — carries the selection read",
    },
}

#: sha256 of each extracted file, pinned in code — verified 2026-08-17
#: against the official UKP zips (nfcorpus 2.4MB / scifact 2.8MB / fiqa
#: 18MB). msmarco is intentionally absent: its 1.1GB zip makes an author-time
#: pin impractical, so its digests are RECORDED at first verified download
#: (sidecar) and every later run verifies the cache against that record.
DATASET_DIGESTS: dict[str, dict[str, str]] = {
    "nfcorpus": {
        "corpus.jsonl": "10cc83ef1826b1425e6a87090b5140b39b27755d5a27e48215a88611c899991f",
        "queries.jsonl": "d024e6621b84925d485ae473d316a0c3af31c62c8068a59fb29d22f7613aef2a",
        "qrels/test.tsv": "f8fba6ef3d4dd9c3a242a8ba4ae38276fc3622fce7dcbae764766d564542fd2a",
    },
    "scifact": {
        "corpus.jsonl": "dec31c8182f3d744c7d2c09423756fd1d17cbef75808db13ba01cc0aab4d1ac6",
        "queries.jsonl": "8ff84a7c903f722981cd8d595c022660140c51867b27608a6d4910db86080313",
        "qrels/test.tsv": "0864bb985e0ca2367ba217977e72004d549054b2b06666ed9d4825ac7c21284c",
    },
    "fiqa": {
        "corpus.jsonl": "ff593e4df9933955dc3af83be0c3fa28ac7465f627e08c2e53593e734d506517",
        "queries.jsonl": "eede1e61d4a0188940239b53ebc2da91f577a6a34679c812d1eb9090c29877bc",
        "qrels/test.tsv": "6adc2a640dcdd22bb8b3858f89107adef2a7c3db20a63550dfa7a0f71e379e44",
    },
}


# ── transport: download → verify → cache → parse (longmem_eval precedent) ──

def cache_dir() -> Path:
    """Dataset cache root (env override or default, outside the repo)."""
    raw = os.environ.get("TORTOISE_MINI_BEIR_CACHE_DIR", "").strip()
    base = Path(raw).expanduser() if raw else Path(DEFAULT_CACHE_DIR).expanduser()
    base.mkdir(parents=True, exist_ok=True)
    return base


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


def _sidecar_path(cache_root: Path, name: str) -> Path:
    return cache_root / f"{name}.digest.json"


def _expected_digests(name: str, cache_root: Path) -> dict[str, str]:
    """Authoritative digests for the dataset's cached files.

    Code-pinned for nfcorpus/scifact/fiqa; sidecar-recorded for msmarco.
    A msmarco cache without a sidecar record is treated as unverifiable
    (fail-closed → re-download), never silently trusted.
    """
    pinned = DATASET_DIGESTS.get(name)
    if pinned is not None:
        return dict(pinned)
    sidecar = _sidecar_path(cache_root, name)
    if sidecar.is_file():
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
            if data.get("dataset") == name and isinstance(data.get("files"), dict):
                return {rel: str(digest) for rel, digest in data["files"].items()}
        except (json.JSONDecodeError, OSError):
            pass
        raise DatasetDigestError(
            f"{name}: digest record ({sidecar}) is missing/corrupt — cannot "
            "verify the cache; re-download or pass a verified cache "
            "(never serve a partial corpus)")
    return {}


def _record_sidecar(name: str, cache_root: Path, files: dict[str, Path]) -> None:
    """Record sha256s at first verified download (msmarco; also backfilled
    for pinned datasets so every dataset carries a dated record)."""
    sidecar = _sidecar_path(cache_root, name)
    sidecar.write_text(json.dumps({
        "dataset": name,
        "source": DATASETS.get(name, {}).get("url", "local"),
        "fetched": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "files": {rel: _sha256_file(p) for rel, p in files.items()},
    }, indent=2), encoding="utf-8")


def _verify_cache(name: str, cache_root: Path, files: dict[str, Path]) -> None:
    """Verify every cached file against its expected digest. Raises
    DatasetDigestError on mismatch — a truncated/tampered cache is never
    served as a partial corpus."""
    expected = _expected_digests(name, cache_root)
    if not expected:
        # pinned datasets always have digests; unreachable in practice.
        raise DatasetDigestError(f"{name}: no digest record available to verify")
    for rel, path in files.items():
        want = expected.get(rel)
        if want is None:
            raise DatasetDigestError(
                f"{name}: no recorded digest for {rel} — cache layout changed; "
                "re-download (never serve a partial corpus)")
        got = _sha256_file(path)
        if got != want:
            raise DatasetDigestError(
                f"{name} {rel}: digest mismatch (expected {want}, got {got}) — "
                "the cached file is truncated or tampered; re-download or "
                "pass a verified cache (never serve a partial corpus)")


def _download_zip(name: str, dest: Path, *, timeout: int = 120) -> Path:
    """Download the dataset zip atomically (temp .part + rename).

    A short/interrupted download is validated (zip header) BEFORE promotion
    and discarded — a poisoned cache can never be served forever; the next
    run re-downloads.
    """
    url = DATASETS[name]["url"]
    print(f"[mini_beir] downloading {url} …", file=sys.stderr)
    req = urllib.request.Request(url, headers={"User-Agent": "tortoise-mini-beir"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    try:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                with open(tmp, "wb") as f:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            # P1 (code review): network failures must surface as a clean
            # DatasetError (per-dataset fail-closed), not a bare traceback.
            raise DatasetError(f"{name}: download failed: {e}") from e
        if not zipfile.is_zipfile(tmp):
            raise DatasetError(
                f"{name}: downloaded file is not a valid zip (truncated or "
                "corrupt download) — discarding; re-run to re-download")
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)
    return dest


def _extract_members(name: str, zip_path: Path, dest_dir: Path) -> dict[str, Path]:
    """Extract the dataset's members into ``dest_dir``.

    Extraction goes to a temp dir first (a partial extraction must never
    become the cache), then each member is moved into place atomically.
    The zip's internal CRC is validated by zipfile during extraction.
    """
    tmp = dest_dir.with_name(f"{name}.extract-{os.getpid()}")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        tmp.mkdir(parents=True)
        try:
            with zipfile.ZipFile(zip_path) as z:
                bad = z.testzip()
                if bad is not None:
                    raise DatasetError(
                        f"{name}: zip member {bad!r} failed its CRC check — "
                        "corrupt download; discarding")
                for rel in DATASETS[name]["files"]:
                    member = f"{name}/{rel}"
                    out = tmp / rel
                    out.parent.mkdir(parents=True, exist_ok=True)  # qrels/test.tsv
                    with z.open(member) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        except zipfile.BadZipFile as e:
            raise DatasetError(
                f"{name}: zip is corrupt ({e}) — discarding; re-run to "
                "re-download (never a partial corpus)") from e
        files = {rel: dest_dir / rel for rel in DATASETS[name]["files"]}
        dest_dir.mkdir(parents=True, exist_ok=True)
        for rel, path in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)  # qrels/
            os.replace(tmp / rel, path)  # atomic per file
        return files
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _download_and_extract(name: str, cache_root: Path) -> dict[str, Path]:
    """Full fetch path: download → extract → verify → record. The dataset
    dir is wiped first so a corrupt/partial cache never lingers."""
    ds_dir = cache_root / name
    shutil.rmtree(ds_dir, ignore_errors=True)
    ds_dir.mkdir(parents=True, exist_ok=True)
    zip_path = ds_dir / f"{name}.zip"
    try:
        _download_zip(name, zip_path)
        files = _extract_members(name, zip_path, ds_dir)
        # Pinned datasets are verified against the code pins right after
        # extraction (a server-side tamper or bad download fails here);
        # msmarco records its digests at this first verified download.
        pinned = DATASET_DIGESTS.get(name)
        if pinned is not None:
            for rel, path in files.items():
                got = _sha256_file(path)
                if got != pinned.get(rel):
                    raise DatasetDigestError(
                        f"{name} {rel}: digest mismatch after download "
                        f"(expected {pinned[rel]}, got {got}) — discarding")
        _record_sidecar(name, cache_root, files)
        return files
    finally:
        zip_path.unlink(missing_ok=True)


def load_dataset(name: str, *, cache: Path | None = None,
                 download: bool = True) -> dict:
    """Load a remote BEIR dataset.

    Resolution: cached extracted files (digest-verified) → re-download.
    Returns ``{"name", "corpus_path", "queries", "qrels"}`` — the corpus is
    loaded lazily by the runner (msmarco's 8.8M passages are never
    materialized in RAM; sampling streams the file).

    Raises:
        DatasetDigestError: cached files are corrupt/tampered and download
            is disabled (or a freshly downloaded file fails verification).
        FileNotFoundError: no cache and download disabled.
    """
    if name not in DATASETS:
        raise ValueError(f"unknown dataset {name!r} — known: {sorted(DATASETS)}")
    cache_root = Path(cache).expanduser() if cache is not None else cache_dir()
    files = {rel: cache_root / name / rel for rel in DATASETS[name]["files"]}

    if all(f.is_file() for f in files.values()):
        try:
            _verify_cache(name, cache_root, files)
            _record_sidecar(name, cache_root, files)  # backfill dated record
            return _read_dataset(name, files)
        except DatasetDigestError:
            if not download:
                raise
            print(f"[mini_beir] cached dataset {name} is corrupt or "
                  f"digest-mismatched — re-downloading", file=sys.stderr)
    elif not download:
        missing = [rel for rel, f in files.items() if not f.is_file()]
        raise FileNotFoundError(
            f"no cached dataset {name} at {cache_root / name} (missing "
            f"{missing}) and download disabled (--no-download) — set "
            "TORTOISE_MINI_BEIR_CACHE_DIR or drop --no-download")

    files = _download_and_extract(name, cache_root)
    return _read_dataset(name, files)


def load_local(data_dir: Path) -> dict:
    """Load a local BEIR-format dataset directory (the --data-dir hook —
    fixture-only smoke runs, offline environments; no download, no digest
    pin). Layout: corpus.jsonl / queries.jsonl / qrels/test.tsv."""
    d = Path(data_dir)
    files = {rel: d / rel for rel in ("corpus.jsonl", "queries.jsonl",
                                      "qrels/test.tsv")}
    missing = [rel for rel, f in files.items() if not f.is_file()]
    if missing:
        raise FileNotFoundError(
            f"--data-dir {d} missing {missing} (need corpus.jsonl, "
            "queries.jsonl, qrels/test.tsv)")
    return _read_dataset("synthetic", files)


# ── parsers (structural validation — malformed content is an error, never
# ── a silently-partial corpus) ─────────────────────────────────────────────

def _parse_line(path: Path, line_no: int, raw: str) -> dict:
    line = raw.strip()
    if not line:
        raise DatasetError(f"{path}:{line_no} blank line in dataset file")
    try:
        obj = json.loads(line)
    except json.JSONDecodeError as e:
        raise DatasetError(
            f"{path}:{line_no} malformed JSON line ({e}) — refusing to serve "
            "a partial corpus") from e
    if not isinstance(obj, dict):
        raise DatasetError(f"{path}:{line_no} expected a JSON object")
    return obj


def _read_queries(path: Path) -> list[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for i, raw in enumerate(f, start=1):
            obj = _parse_line(path, i, raw)
            if "_id" not in obj or not isinstance(obj.get("text"), str):
                raise DatasetError(f"{path}:{i} query missing _id/text")
            out.append(obj)
    return out


def _read_qrels(path: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    with open(path, "r", encoding="utf-8") as f:
        for i, raw in enumerate(f, start=1):
            line = raw.strip()
            if not line or line.startswith("query-id"):
                continue  # header
            parts = line.split("\t")
            if len(parts) != 3:
                raise DatasetError(f"{path}:{i} malformed qrels row {line[:60]!r}")
            qid, cid, score = parts
            try:
                score = int(score)
            except ValueError as e:
                raise DatasetError(f"{path}:{i} non-integer qrels score") from e
            out.setdefault(qid, {})[cid] = score
    return out


def _read_dataset(name: str, files: dict[str, Path]) -> dict:
    return {
        "name": name,
        "corpus_path": files["corpus.jsonl"],
        "queries": _read_queries(files["queries.jsonl"]),
        "qrels": _read_qrels(files["qrels/test.tsv"]),
    }


def _load_all_corpus(path: Path) -> tuple[list[dict], int]:
    """Read the full corpus (small datasets). Blank lines are skipped — an
    empty corpus file is DEFINED output (0 passages), not an error."""
    docs = []
    with open(path, "r", encoding="utf-8") as f:
        for i, raw in enumerate(f, start=1):
            if not raw.strip():
                continue
            docs.append(_parse_line(path, i, raw))
    return docs, len(docs)


def _stream_corpus(path: Path, sample_n: int, seed: int) -> tuple[list[dict], int]:
    """Deterministic seeded-reservoir sample of ``sample_n`` passages over a
    streaming corpus read (msmarco: the full 8.8M-passage corpus.jsonl is
    never materialized — ~1.2GB file, one pass, constant RAM). Same seed +
    full corpus ⇒ same sample across runs; n ≥ corpus size ⇒ all passages
    in original order."""
    rng = random.Random(seed)
    sample: list[dict] = []
    total = 0
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            if not raw.strip():
                continue  # blank lines skipped (empty corpus = 0 passages)
            total += 1
            doc = _parse_line(path, total, raw)
            if len(sample) < sample_n:
                sample.append(doc)
            else:
                j = rng.randint(0, total - 1)
                if j < sample_n:
                    sample[j] = doc
    return sample, total


def sample_corpus(corpus: list[dict], n: int, seed: int) -> list[dict]:
    """Deterministic reservoir sample of ``n`` items from an in-memory
    corpus (the pure-function form of ``_stream_corpus`` — same math, unit-
    tested; n ≥ len(corpus) ⇒ the corpus unchanged, order preserved)."""
    if n >= len(corpus):
        return list(corpus)
    rng = random.Random(seed)
    sample = list(corpus[:n])
    for i in range(n, len(corpus)):
        j = rng.randint(0, i)
        if j < n:
            sample[j] = corpus[i]
    return sample


# ── query constraint + relevance ───────────────────────────────────────────

def constrain_queries(queries: list[dict], qrels: dict[str, dict[str, int]],
                      cap: int | None = None) -> list[dict]:
    """Qrels-constrained, deterministically ordered query list.

    Only queries present in the (test) qrels are evaluated; order is by
    ``_id`` (stable across runs). ``cap`` truncates (msmarco top-1000;
    ``--limit`` caps per dataset for smoke runs).
    """
    constrained = [q for q in queries if q["_id"] in qrels]
    constrained.sort(key=lambda q: q["_id"])
    if cap is not None:
        return constrained[:cap]
    return constrained


def relevance_set(qid: str, qrels: dict[str, dict[str, int]]) -> set[str]:
    """Relevant corpus ids for a query — binary (qrels score > 0, the BEIR
    convention). Unknown query → empty set (defined: nDCG = 0)."""
    return {cid for cid, score in qrels.get(qid, {}).items() if score > 0}


# ── metrics: nDCG@10 + R@10 (binary gain; in-repo convention) ──────────────

def dcg_at_k(rels: list[float], k: int = K) -> float:
    """DCG@k, binary gain, log₂(i+2) discount — the in-repo eval convention
    (tests/eval/retrieval/metrics.py + LongMemEval arm)."""
    return sum(rel / math.log2(i + 2)
               for i, rel in enumerate(rels[:k]) if rel > 0)


def ndcg_at_k(ranked_ids: list[str], relevant: set[str], k: int = K) -> float:
    """nDCG@k of ``ranked_ids`` against the binary relevant set.

    IDCG = all-relevant-first, capped at k. No relevant docs → 0.0 (defined
    output, included in the mean — never a crash or NaN).
    """
    if not relevant:
        return 0.0
    dcg = sum(1.0 / math.log2(i + 2)
              for i, pid in enumerate(ranked_ids[:k]) if pid in relevant)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(relevant))))
    if ideal <= 0.0:
        return 0.0
    return dcg / ideal


def recall_at_k(ranked_ids: list[str], relevant: set[str], k: int = K) -> float:
    """R@k: |retrieved ∩ relevant| / |relevant| (top-k, binary). Empty
    relevant set → 0.0 (defined output)."""
    if not relevant:
        return 0.0
    retrieved = set(ranked_ids[:k]) & relevant
    return len(retrieved) / len(relevant)


# ── encode + rank ──────────────────────────────────────────────────────────

def encode_texts(model, texts: list[str]) -> np.ndarray:
    """Encode texts through the injected singleton model (the probe-swapped
    EmbeddingModel — T1). Returns a float32 (n, dim) array; empty input → a
    (0, 0) array (defined, no crash)."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    vecs = model.encode(texts, batch_size=BATCH_SIZE, show_progress_bar=False)
    arr = np.asarray(vecs, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(1, -1)
    return arr


def rank_docs(query_vec: np.ndarray, doc_vecs: np.ndarray, k: int = K) -> list[int]:
    """Top-k doc indices by cosine similarity (descending, STABLE — ties
    keep corpus order, so the tiny-fixture ranking is fully deterministic).
    Empty corpus → [] ; zero-norm query → corpus order (defined)."""
    n = doc_vecs.shape[0]
    if n == 0 or query_vec.ndim == 0 or query_vec.shape[0] == 0:
        return []
    qn = float(np.linalg.norm(query_vec))
    if qn == 0:
        return list(range(min(k, n)))
    q = query_vec / qn
    norms = np.linalg.norm(doc_vecs, axis=1)
    dn = doc_vecs / np.maximum(norms, 1e-12)[:, None]
    sims = dn @ q
    k_eff = min(k, n)
    order = np.argsort(-sims, kind="stable")[:k_eff]
    return [int(i) for i in order]


def _doc_text(doc: dict) -> str:
    return f"{doc.get('title') or ''} {doc.get('text') or ''}".strip()


def evaluate_dataset(name: str, dataset: dict, model, *,
                     limit: int | None = None) -> dict:
    """Run nDCG@10 + R@10 over one dataset.

    ``dataset`` is the load_dataset/load_local shape. Queries are
    qrels-constrained (sorted by _id, capped by the dataset's query_cap and
    ``--limit``). The msmarco corpus is the deterministic 100k sample; the
    others are full. Empty/1-passage corpora are defined (metrics 0.0).
    """
    cfg = DATASETS.get(name, {})
    qrels = dataset["qrels"]
    queries = constrain_queries(dataset["queries"], qrels,
                                cap=cfg.get("query_cap"))
    if limit is not None:
        queries = queries[:limit]

    sample_n = cfg.get("corpus_sample")
    if sample_n is not None:
        corpus, total = _stream_corpus(dataset["corpus_path"], sample_n,
                                       cfg["corpus_seed"])
        corpus_meta = {"n": total, "sampled": len(corpus),
                       "method": "seeded-reservoir", "seed": cfg["corpus_seed"]}
    else:
        corpus, total = _load_all_corpus(dataset["corpus_path"])
        corpus_meta = {"n": total, "sampled": total, "method": "full"}

    doc_vecs = encode_texts(model, [_doc_text(d) for d in corpus])

    per_query: list[dict] = []
    for q in queries:
        rel = relevance_set(q["_id"], qrels)
        qv = encode_texts(model, [q.get("text") or ""])[0]
        ranked = [corpus[i]["_id"] for i in rank_docs(qv, doc_vecs)]
        per_query.append({
            "_id": q["_id"],
            "ndcg@10": round(ndcg_at_k(ranked, rel, K), 6),
            "r@10": round(recall_at_k(ranked, rel, K), 6),
        })

    def _mean(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 6) if vals else 0.0

    return {
        "n_queries": len(per_query),
        "corpus": corpus_meta,
        "queries": per_query,
        "metrics": {
            "ndcg@10": _mean([q["ndcg@10"] for q in per_query]),
            "r@10": _mean([q["r@10"] for q in per_query]),
        },
        "note": cfg.get("note", "synthetic fixture (no remote counterpart)"),
    }


# ── CLI ────────────────────────────────────────────────────────────────────

def result_path(out_dir: Path, model: str, prompt: str | None) -> Path:
    """Per-model results JSON path (committed in the run output dir)."""
    slug = f"{model}-{prompt}" if prompt else model
    return Path(out_dir) / f"mini_beir-{slug}.json"


def _methodology() -> dict:
    return {
        "metrics": ["ndcg@10", "r@10"],
        "k": K,
        "ndcg_at_k": "binary gain (qrels score > 0); DCG = Σ rel_i/log₂(i+2); "
                     "IDCG = all-relevant-first capped at k (in-repo convention, "
                     "identical to the LongMemEval gate arm)",
        "recall_at_k": "|retrieved ∩ relevant| / |relevant| (top-k, binary)",
        "relevance": "qrels score > 0 (BEIR convention)",
        "corpus_composition": "title + ' ' + text",
        "msmarco_corpus": f"{MSMARCO_SAMPLE}-passage deterministic sample "
                          "(seed {MSMARCO_SEED}) of the full 8.8M corpus — "
                          "NOT leaderboard-comparable; internal ranking only",
        "contamination_note": "MS MARCO is in-domain for arctic/bge (trained "
                              "on it) — sanity only; OOD datasets "
                              "(NFCorpus/SciFact/FiQA) carry the selection read",
        "purpose": "research surface only — NOT a gate; feeds the multi-winner "
                   "tiebreak + long-term monitoring baseline",
    }


def _collect_digests(cache_root: Path) -> dict:
    """Recorded dataset digests for the results JSON (sha256/size/date)."""
    out: dict = {}
    for name in DATASETS:
        sidecar = _sidecar_path(cache_root, name)
        if not sidecar.is_file():
            continue
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        sizes = {}
        for rel in data.get("files", {}):
            p = cache_root / name / rel
            sizes[rel] = p.stat().st_size if p.is_file() else None
        out[name] = {
            "files": {rel: {"sha256": data["files"][rel], "size_bytes": sizes[rel]}
                      for rel in data.get("files", {})},
            "source": data.get("source"),
            "fetched": data.get("fetched"),
        }
    return out


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tools.mini_beir.run",
        description="mini-BEIR retrieval-quality research harness (#1349 T6). "
                    "nDCG@10 + R@10 over MS MARCO (top-1000, 100k-passage "
                    "sample), NFCorpus, SciFact, FiQA. Research surface only "
                    "— NOT a gate.")
    p.add_argument("--model", default="minilm",
                   help="probe model short name (tools.embedder_probe "
                        "PROBE_MODELS); default minilm (control)")
    p.add_argument("--query-prompt", default=None,
                   help="named prompt template threaded to the model via "
                        "inject_model (e.g. 'query' for arctic)")
    p.add_argument("--limit", type=int, default=None,
                   help="cap queries per dataset (smoke runs)")
    p.add_argument("--data-dir", default=None,
                   help="local BEIR-format dataset dir (corpus.jsonl, "
                        "queries.jsonl, qrels/test.tsv) — fixture/offline "
                        "runs; no download, no digest pin")
    p.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR),
                   help="per-model results JSON output dir")
    p.add_argument("--cache-dir", default=None,
                   help="dataset cache root (default TORTOISE_MINI_BEIR_"
                        "CACHE_DIR or ~/.cache/tortoise-mini-beir)")
    p.add_argument("--no-download", action="store_true",
                   help="cache-only: error on missing/corrupt cache (never "
                        "download)")
    return p


def run_main(argv: list[str] | None = None) -> dict:
    """CLI entry — returns the results dict (and writes the per-model JSON)."""
    ap = _parser()
    args = ap.parse_args(argv)
    if args.model not in PROBE_MODELS:
        ap.error(f"unknown probe model {args.model!r} — known: {sorted(PROBE_MODELS)}")
    if args.limit is not None and args.limit < 1:
        ap.error("--limit must be >= 1")

    # T1 probe: inject BEFORE any encode — the singleton IS the candidate.
    probe = inject_model(args.model, query_prompt=args.query_prompt)
    model = EmbeddingModel.get()
    if model is None:
        raise RuntimeError(
            "embedding model unavailable after probe injection — refusing to "
            "run (probe HARD-FAILs on load failure; a None singleton is a "
            "bug)")

    cache_root = (Path(args.cache_dir).expanduser() if args.cache_dir
                  else cache_dir())
    results: dict = {
        "harness": "mini-beir",
        "version": 1,
        "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "model": args.model,
        "query_prompt": args.query_prompt,
        "probe": probe,
        "datasets": {},
        "digests": {},
        "methodology": _methodology(),
    }

    if args.data_dir:
        ds = load_local(args.data_dir)
        entry = evaluate_dataset("synthetic", ds, model, limit=args.limit)
        entry["source"] = "local"
        results["datasets"]["synthetic"] = entry
    else:
        for name in DATASETS:
            try:
                ds = load_dataset(name, cache=cache_root,
                                  download=not args.no_download)
                entry = evaluate_dataset(name, ds, model, limit=args.limit)
                entry["source"] = "remote"
                results["datasets"][name] = entry
            except DatasetError as e:
                # P1 (code review): one dataset failing must not discard
                # completed runs — record the failure per-dataset and
                # continue, so the results JSON shows exactly which
                # datasets succeeded (fail-closed per-dataset, not per-run).
                results["datasets"][name] = {
                    "error": str(e), "source": "remote",
                    "metrics": {"ndcg@10": None, "r@10": None},
                    "n_queries": 0,
                }
        results["digests"] = _collect_digests(cache_root)

    if "msmarco" in results["datasets"]:
        print("[mini_beir] WARNING: MS MARCO is IN-DOMAIN for arctic/bge "
              "(both trained on it) — treat its scores as sanity only; weight "
              "the OOD datasets (NFCorpus/SciFact/FiQA) for the selection "
              "read", file=sys.stderr)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = result_path(out_dir, args.model, args.query_prompt)
    path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"[mini_beir] results written to {path}", file=sys.stderr)
    return results


def main() -> int:
    results = run_main()
    for name, ds in results["datasets"].items():
        m = ds["metrics"]
        print(f"[mini_beir] {name}: nDCG@10={m['ndcg@10']:.4f} "
              f"R@10={m['r@10']:.4f} (n={ds['n_queries']}, "
              f"corpus={ds['corpus']['sampled']}/{ds['corpus']['n']})",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
