"""Task 6 (#1349) — mini-BEIR research harness.

Covers the four-dataset retrieval-quality harness (``tools/mini_beir/run.py``):

  * nDCG@10 + R@10 — binary-gain metrics hand-computed on ranked lists,
  * end-to-end on a tiny synthetic corpus (fixture-only, NEVER downloads):
    a deterministic token-overlap fake embedder produces a hand-computable
    ranking; per-query AND mean metrics asserted exactly,
  * empty and 1-passage corpora → defined output (nDCG@10=0, R@10=0), no crash,
  * digest-pinned datasets: a corrupt/truncated cache re-downloads (mocked
    urllib) or raises a clear error under ``--no-download`` — never a
    silently-partial corpus,
  * ``--model`` / ``--query-prompt`` wiring — the probe is MOCKED (no model
    download); the results JSON records model + probe state per model,
  * ``--limit`` caps queries per dataset (smoke runs),
  * qrels-constraint: queries without test qrels are excluded.

Runs fully offline. No live downloads, no live model loads.
"""
from __future__ import annotations

import io
import json
import math
import re
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import tortoise.embeddings as emb  # noqa: E402

from tools.mini_beir import run as mb  # noqa: E402

_FAKE_DIM = 32
_TOKEN_RE = re.compile(r"[a-z0-9']+")


def _fake_vec(text: str) -> list[float]:
    """Deterministic token-overlap embedding (stable across processes)."""
    dims: dict[str, float] = {}
    for tok in _TOKEN_RE.findall((text or "").lower()):
        dims[tok] = dims.get(tok, 0.0) + 1.0
    vec = [0.0] * _FAKE_DIM
    for tok, c in dims.items():
        vec[zlib_crc(tok) % _FAKE_DIM] += c
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def zlib_crc(tok: str) -> int:
    import zlib
    return zlib.crc32(tok.encode("utf-8"))


class _FakeModel:
    """Stand-in for the EmbeddingModel singleton (injected-model contract)."""

    def encode(self, texts: list[str], **kwargs):
        return np.array([_fake_vec(t) for t in texts], dtype=np.float32)


@pytest.fixture()
def fake_embeddings(monkeypatch):
    """Deterministic embedding path: query + doc encode via _fake_vec."""
    monkeypatch.setattr(emb.EmbeddingModel, "get", lambda: _FakeModel())


# ── tiny synthetic BEIR dataset (hand-computed fixture) ────────────────────
#
# corpus (index order matters — ties keep index order via stable sort; the
# word set was verified so token-hash dims produce EXACTLY these cosines):
#   d1: "statins prevent breast cancer recurrence"
#   d2: "lipids regulate breast cancer cells"
#   d3: "brown fox alpha beta delta epsilon zeta"
#   d4: "quantum mechanics observation experiment"
#   d5: "breast cancer screening guidelines extra context words here"
# queries:
#   q1: "statins breast cancer"   → relevant {d1, d5}
#   q2: "brown fox"               → relevant {d3}
#   q3: "unrelated nonsense blah" → judged (score-0 qrels row) but NO
#       positive-score relevant docs → nDCG@10 = 0.0, R@10 = 0.0
# qrels (test.tsv): q1 d1 2; q1 d5 1; q2 d3 1; q3 d1 0
#   (relevance = score > 0; the score-0 row keeps q3 qrels-constrained)
#
# Token-overlap cosines for q1 (query dims: statins→9, breast→11, cancer→17):
#   d1 = 3 tokens (statins, breast, cancer) → 0.7746  → rank 1
#   d2 = 2 tokens (breast, cancer)          → 0.5164  → rank 2  (non-relevant!)
#   d5 = 2 tokens (breast, cancer, 6 more)  → 0.4082  → rank 3  (relevant)
#   d3 = 0 tokens, d4 = 0 tokens (verified: no dim-9/11/17 words)
#   → ranked [d1, d2, d5, d3, d4]
#   nDCG@10 = (1/log2(2) + 1/log2(4)) / (1/log2(2) + 1/log2(3))
#           = 1.5 / 1.63093 ≈ 0.91972 ; R@10 = 2/2 = 1.0
# q2 → [d3, d1, d2, d4, d5] (d3 = brown+fox; all others verified 0)
#   nDCG@10 = 1.0 ; R@10 = 1.0
# q3 → no relevant docs (ranking irrelevant) → nDCG@10 = 0.0 ; R@10 = 0.0
# means: nDCG@10 ≈ (0.91972 + 1.0 + 0.0)/3 ≈ 0.63991 ; R@10 = 2/3 ≈ 0.66667

CORPUS_LINES = [
    '{"_id": "d1", "title": "", "text": "statins prevent breast cancer recurrence"}',
    '{"_id": "d2", "title": "", "text": "lipids regulate breast cancer cells"}',
    '{"_id": "d3", "title": "", "text": "brown fox alpha beta delta epsilon zeta"}',
    '{"_id": "d4", "title": "", "text": "quantum mechanics observation experiment"}',
    '{"_id": "d5", "title": "", "text": "breast cancer screening guidelines extra context words here"}',
]
QUERY_LINES = [
    '{"_id": "q1", "text": "statins breast cancer"}',
    '{"_id": "q2", "text": "brown fox"}',
    '{"_id": "q3", "text": "unrelated nonsense blah"}',
]
QRELS_TSV = "query-id\tcorpus-id\tscore\nq1\td1\t2\nq1\td5\t1\nq2\td3\t1\nq3\td1\t0\n"


def _write_dataset_dir(tmp_path: Path, *,
                       corpus: list[str] | None = None,
                       queries: list[str] | None = None,
                       qrels: str | None = None) -> Path:
    """Materialize a BEIR-format dataset directory (the --data-dir fixture)."""
    d = tmp_path / "dataset"
    d.mkdir(parents=True, exist_ok=True)
    (d / "corpus.jsonl").write_text(
        "\n".join(corpus if corpus is not None else CORPUS_LINES) + "\n",
        encoding="utf-8")
    (d / "queries.jsonl").write_text(
        "\n".join(queries if queries is not None else QUERY_LINES) + "\n",
        encoding="utf-8")
    qrels_dir = d / "qrels"
    qrels_dir.mkdir(exist_ok=True)
    (qrels_dir / "test.tsv").write_text(qrels if qrels is not None else QRELS_TSV,
                                        encoding="utf-8")
    return d


def _fixture_zip_bytes(name: str, files: dict[str, str]) -> bytes:
    """A BEIR-layout zip in memory: {rel_path: file_content} → {name}/{rel}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, content in files.items():
            z.writestr(f"{name}/{rel}", content)
    return buf.getvalue()


def _sha256_bytes(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


# ── nDCG@10 / R@10 — hand-computed ────────────────────────────────────────

def test_ndcg_at_k_hand_computed():
    ranked = ["d1", "d2", "d3", "d4", "d5"]
    # relevant at ranks 1 and 3 (1-based): DCG = 1/log2(2) + 1/log2(4) = 1.5
    # IDCG (both relevant first) = 1/log2(2) + 1/log2(3) ≈ 1.63093
    assert mb.ndcg_at_k(ranked, {"d1", "d3"}, k=10) == pytest.approx(
        1.5 / 1.63093, abs=1e-4)
    # perfect ranking → 1.0
    assert mb.ndcg_at_k(["d1", "d3", "d2", "d4", "d5"], {"d1", "d3"}, k=10) == \
        pytest.approx(1.0)
    # zero relevant docs → defined 0.0 (no crash, no divide-by-zero)
    assert mb.ndcg_at_k(ranked, set(), k=10) == 0.0
    # top-k boundary: rank 10 (index 9) is included with its log₂(11)
    # discount; rank 11 (index 10) is excluded entirely
    ranked11 = [f"x{i}" for i in range(11)]
    assert mb.ndcg_at_k(ranked11, {"x0", "x9"}, k=10) == pytest.approx(
        (1.0 + 1.0 / math.log2(11)) / 1.63093, abs=1e-4)
    assert mb.ndcg_at_k(ranked11, {"x0", "x10"}, k=10) == pytest.approx(
        1.0 / 1.63093, abs=1e-4)
    # IDCG for >10 relevant = Σ_{i=0..9} 1/log₂(i+2) ≈ 4.5435
    assert mb.dcg_at_k([1.0] * 10, k=10) == pytest.approx(4.5435, abs=1e-3)


def test_recall_at_k_hand_computed():
    ranked = ["d1", "d2", "d3", "d4", "d5"]
    assert mb.recall_at_k(ranked, {"d1", "d3"}, k=10) == pytest.approx(1.0)
    assert mb.recall_at_k(ranked, {"d1", "d3", "dX"}, k=10) == pytest.approx(2 / 3)
    assert mb.recall_at_k(ranked, {"dX", "dY"}, k=10) == 0.0
    # empty relevant set → defined 0.0
    assert mb.recall_at_k(ranked, set(), k=10) == 0.0
    # top-k boundary: index 10 excluded at k=10
    ranked11 = [f"x{i}" for i in range(11)]
    assert mb.recall_at_k(ranked11, {"x0", "x10"}, k=10) == pytest.approx(0.5)
    assert mb.recall_at_k(ranked11, {"x10"}, k=10) == 0.0


# ── end-to-end on the synthetic fixture (fake embedder, no downloads) ──────

def test_end_to_end_synthetic_corpus(tmp_path, monkeypatch, fake_embeddings):
    """Full pipeline over the hand-computed fixture: per-query AND mean
    nDCG@10/R@10 match the hand-computed values exactly."""
    calls: list[tuple] = []

    def _inject(name, query_prompt=None):
        calls.append((name, query_prompt))
        return {"name": name, "hf_id": f"hf/{name}", "resolved_revision": "rev1",
                "query_prompt": query_prompt, "dim": 384}

    monkeypatch.setattr(mb, "inject_model", _inject)
    data_dir = _write_dataset_dir(tmp_path)
    out = tmp_path / "out"
    res = mb.run_main(["--data-dir", str(data_dir),
                       "--model", "minilm", "--output-dir", str(out)])

    # probe wiring: injected model IS what encoded everything
    assert calls == [("minilm", None)]
    assert res["model"] == "minilm"
    ds = res["datasets"]["synthetic"]
    by_id = {q["_id"]: q for q in ds["queries"]}
    assert ds["n_queries"] == 3
    assert by_id["q1"]["ndcg@10"] == pytest.approx(0.91972, abs=1e-4)
    assert by_id["q1"]["r@10"] == pytest.approx(1.0)
    assert by_id["q2"]["ndcg@10"] == pytest.approx(1.0)
    assert by_id["q2"]["r@10"] == pytest.approx(1.0)
    assert by_id["q3"]["ndcg@10"] == 0.0
    assert by_id["q3"]["r@10"] == 0.0
    assert ds["metrics"]["ndcg@10"] == pytest.approx(0.63991, abs=1e-4)
    assert ds["metrics"]["r@10"] == pytest.approx(2 / 3, abs=1e-6)
    # results JSON per model written to the output dir
    path = mb.result_path(out, "minilm", None)
    assert path.is_file()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["model"] == "minilm"
    assert on_disk["datasets"]["synthetic"]["metrics"]["ndcg@10"] == \
        pytest.approx(0.63991, abs=1e-4)


def test_empty_corpus_defined_output(tmp_path, monkeypatch, fake_embeddings):
    """Empty corpus → nDCG@10 = 0.0, R@10 = 0.0, no crash (plan-review P2)."""
    monkeypatch.setattr(mb, "inject_model",
                        lambda name, query_prompt=None: {
                            "name": name, "hf_id": f"hf/{name}",
                            "resolved_revision": None, "query_prompt": None,
                            "dim": 384})
    data_dir = _write_dataset_dir(tmp_path, corpus=[])
    out = tmp_path / "out"
    res = mb.run_main(["--data-dir", str(data_dir),
                       "--model", "minilm", "--output-dir", str(out)])
    ds = res["datasets"]["synthetic"]
    assert ds["corpus"]["n"] == 0
    assert ds["n_queries"] == 3          # queries still evaluated (all relevant ∅)
    assert ds["metrics"]["ndcg@10"] == 0.0
    assert ds["metrics"]["r@10"] == 0.0
    assert all(q["ndcg@10"] == 0.0 and q["r@10"] == 0.0 for q in ds["queries"])


def test_one_passage_corpus_defined_output(tmp_path, monkeypatch, fake_embeddings):
    """1-passage corpus → defined output, no crash (plan-review P2)."""
    monkeypatch.setattr(mb, "inject_model",
                        lambda name, query_prompt=None: {
                            "name": name, "hf_id": f"hf/{name}",
                            "resolved_revision": None, "query_prompt": None,
                            "dim": 384})
    data_dir = _write_dataset_dir(
        tmp_path, corpus=['{"_id": "d1", "title": "", "text": "statins breast cancer"}'])
    out = tmp_path / "out"
    res = mb.run_main(["--data-dir", str(data_dir),
                       "--model", "minilm", "--output-dir", str(out)])
    ds = res["datasets"]["synthetic"]
    assert ds["corpus"]["n"] == 1
    assert 0.0 <= ds["metrics"]["ndcg@10"] <= 1.0
    assert 0.0 <= ds["metrics"]["r@10"] <= 1.0


def test_limit_caps_queries_per_dataset(tmp_path, monkeypatch, fake_embeddings):
    """--limit caps queries per dataset (smoke runs; the test uses this)."""
    monkeypatch.setattr(mb, "inject_model",
                        lambda name, query_prompt=None: {
                            "name": name, "hf_id": f"hf/{name}",
                            "resolved_revision": None, "query_prompt": None,
                            "dim": 384})
    data_dir = _write_dataset_dir(tmp_path)
    out = tmp_path / "out"
    res = mb.run_main(["--data-dir", str(data_dir), "--limit", "1",
                       "--model", "minilm", "--output-dir", str(out)])
    assert res["datasets"]["synthetic"]["n_queries"] == 1
    assert [q["_id"] for q in res["datasets"]["synthetic"]["queries"]] == ["q1"]


def test_qrels_constraint_excludes_unjudged_queries():
    """Queries without test qrels are excluded; order is deterministic."""
    queries = [
        {"_id": "qA", "text": "a"}, {"_id": "qZ", "text": "z"}, {"_id": "qM", "text": "m"},
    ]
    qrels = {"qZ": {"d1": 1}, "qM": {"d2": 1}}
    constrained = mb.constrain_queries(queries, qrels, cap=None)
    assert [q["_id"] for q in constrained] == ["qM", "qZ"]  # sorted by _id
    # a query with qrels but score 0 is NOT relevant (binaryize at > 0)
    assert mb.relevance_set("qZ", qrels) == {"d1"}
    assert mb.relevance_set("qNope", qrels) == set()


def test_model_and_query_prompt_wiring(tmp_path, monkeypatch, fake_embeddings):
    """--model/--query-prompt reach inject_model; results JSON records probe."""
    calls: list[tuple] = []

    def _inject(name, query_prompt=None):
        calls.append((name, query_prompt))
        return {"name": name, "hf_id": f"hf/{name}", "resolved_revision": "abc123",
                "query_prompt": query_prompt, "dim": 384}

    monkeypatch.setattr(mb, "inject_model", _inject)
    data_dir = _write_dataset_dir(tmp_path)
    out = tmp_path / "out"
    res = mb.run_main(["--data-dir", str(data_dir),
                       "--model", "arctic-s", "--query-prompt", "query",
                       "--output-dir", str(out)])
    assert calls == [("arctic-s", "query")]
    assert res["probe"]["name"] == "arctic-s"
    assert res["probe"]["query_prompt"] == "query"
    assert res["probe"]["resolved_revision"] == "abc123"
    # per-model result file name encodes model + prompt
    p = mb.result_path(out, "arctic-s", "query")
    assert p.is_file()
    assert "arctic-s" in p.name


def test_unknown_model_rejected(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mb, "inject_model", lambda *a, **k: None)
    data_dir = _write_dataset_dir(tmp_path)
    with pytest.raises(SystemExit) as ei:
        mb.run_main(["--data-dir", str(data_dir), "--model", "nope",
                     "--output-dir", str(tmp_path / "out")])
    code = ei.value.code
    assert code == 2 or (isinstance(code, tuple) and code[0] == 2)
    assert "unknown probe model" in capsys.readouterr().err


# ── digest-pinned datasets: corrupt cache → re-download or clear error ─────

class _FakeResp:
    """Streaming urllib response over an in-memory payload."""

    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, n: int) -> bytes:
        data, self._payload = self._payload[:n], self._payload[n:]
        return data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _pin_fixture_digests(monkeypatch, name: str, files: dict[str, str]):
    """Override the code-pinned digests with the fixture's computed ones."""
    pins = {rel: _sha256_bytes(content.encode("utf-8"))
            for rel, content in files.items()}
    monkeypatch.setitem(mb.DATASET_DIGESTS, name, pins)


def _cache_file(cache: Path, name: str, rel: str) -> Path:
    return cache / name / rel


def test_corrupt_cache_redownloads(monkeypatch, tmp_path):
    """A digest-mismatched cache re-downloads (never served as partial corpus)."""
    import urllib.request

    name = "nfcorpus"
    files = {"corpus.jsonl": "\n".join(CORPUS_LINES) + "\n",
             "queries.jsonl": "\n".join(QUERY_LINES) + "\n",
             "qrels/test.tsv": QRELS_TSV}
    payload = _fixture_zip_bytes(name, files)
    _pin_fixture_digests(monkeypatch, name, files)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=120: _FakeResp(payload))

    cache = tmp_path / "cache"
    cache.mkdir()
    # tampered cache: truncated corpus.jsonl (and no sidecar → code pin governs)
    tampered = _cache_file(cache, name, "corpus.jsonl")
    tampered.parent.mkdir(parents=True)
    tampered.write_text(files["corpus.jsonl"][:-40], encoding="utf-8")

    ds = mb.load_dataset(name, cache=cache, download=True)
    corpus, total = mb._load_all_corpus(ds["corpus_path"])
    assert len(corpus) == len(CORPUS_LINES)
    assert total == len(CORPUS_LINES)
    # the tampered cache was replaced by the verified re-download
    assert _sha256_bytes(tampered.read_bytes()) == \
        _sha256_bytes(files["corpus.jsonl"].encode("utf-8"))
    # and all extracted files verified against the pin
    for rel, content in files.items():
        p = _cache_file(cache, name, rel)
        assert p.is_file()
        assert _sha256_bytes(p.read_bytes()) == _sha256_bytes(content.encode("utf-8"))


def test_corrupt_cache_errors_when_no_download(monkeypatch, tmp_path):
    name = "nfcorpus"
    files = {"corpus.jsonl": "\n".join(CORPUS_LINES) + "\n",
             "queries.jsonl": "\n".join(QUERY_LINES) + "\n",
             "qrels/test.tsv": QRELS_TSV}
    _pin_fixture_digests(monkeypatch, name, files)

    cache = tmp_path / "cache"
    cache.mkdir()
    files_on_disk = {rel: _cache_file(cache, name, rel)
                     for rel in ("corpus.jsonl", "queries.jsonl",
                                 "qrels/test.tsv")}
    for rel, content in files.items():
        p = files_on_disk[rel]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    # tamper ONLY the corpus (the cache is otherwise complete + valid)
    files_on_disk["corpus.jsonl"].write_text(
        files["corpus.jsonl"][:-10], encoding="utf-8")

    with pytest.raises(mb.DatasetDigestError) as ei:
        mb.load_dataset(name, cache=cache, download=False)
    msg = str(ei.value)
    assert "corpus.jsonl" in msg and "digest" in msg.lower()


def test_missing_cache_errors_when_no_download(tmp_path):
    cache = tmp_path / "empty-cache"
    cache.mkdir()
    with pytest.raises(FileNotFoundError):
        mb.load_dataset("scifact", cache=cache, download=False)


def test_recorded_digest_catches_tamper_after_first_download(monkeypatch, tmp_path):
    """msmarco's digest is RECORDED at first verified download (sidecar) — a
    tampered cache afterwards is caught against the recorded digest and
    re-downloaded; the recorded pin is never re-derived from the tampered file."""
    import urllib.request

    name = "msmarco"
    files = {"corpus.jsonl": "\n".join(CORPUS_LINES) + "\n",
             "queries.jsonl": "\n".join(QUERY_LINES) + "\n",
             "qrels/test.tsv": QRELS_TSV}
    payload = _fixture_zip_bytes(name, files)
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=120: _FakeResp(payload))

    cache = tmp_path / "cache"
    cache.mkdir()
    # first download records the sidecar
    ds1 = mb.load_dataset(name, cache=cache, download=True)
    corpus, total = mb._load_all_corpus(ds1["corpus_path"])
    assert len(corpus) == len(CORPUS_LINES)
    sidecar = cache / f"{name}.digest.json"
    assert sidecar.is_file()
    recorded = json.loads(sidecar.read_text(encoding="utf-8"))
    assert recorded["dataset"] == name
    assert recorded["files"]["corpus.jsonl"] == \
        _sha256_bytes(files["corpus.jsonl"].encode("utf-8"))

    # tamper the cached corpus → recorded-digest mismatch → re-download
    tampered = _cache_file(cache, name, "corpus.jsonl")
    tampered.write_text(files["corpus.jsonl"][:-50], encoding="utf-8")
    ds2 = mb.load_dataset(name, cache=cache, download=True)
    corpus2, _ = mb._load_all_corpus(ds2["corpus_path"])
    assert len(corpus2) == len(CORPUS_LINES)
    assert _sha256_bytes(tampered.read_bytes()) == \
        _sha256_bytes(files["corpus.jsonl"].encode("utf-8"))

    # and with download disabled the same tamper is a clear error, not a
    # silently-partial corpus
    tampered.write_text(files["corpus.jsonl"][:-50], encoding="utf-8")
    with pytest.raises(mb.DatasetDigestError):
        mb.load_dataset(name, cache=cache, download=False)


def test_download_never_caches_partial_zip(monkeypatch, tmp_path):
    """An interrupted download (short read) must never become the cache: the
    .part temp file is cleaned up and the run errors clearly — no partial
    corpus can ever be served."""
    import urllib.request

    name = "scifact"
    files = {"corpus.jsonl": "\n".join(CORPUS_LINES) + "\n",
             "queries.jsonl": "\n".join(QUERY_LINES) + "\n",
             "qrels/test.tsv": QRELS_TSV}
    payload = _fixture_zip_bytes(name, files)
    _pin_fixture_digests(monkeypatch, name, files)

    # a connection that dies after handing out only half the zip
    half = payload[:len(payload) // 2]
    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=120: _FakeResp(half))

    cache = tmp_path / "cache"
    cache.mkdir()
    with pytest.raises(mb.DatasetError):
        mb.load_dataset(name, cache=cache, download=True)
    # nothing partial was cached; no .part residue
    assert not _cache_file(cache, name, "corpus.jsonl").exists()
    assert not list(cache.rglob("*.part"))
    assert not (cache / "scifact.zip").exists()


# ── sampling (msmarco 100k-passage sample) ─────────────────────────────────

def test_results_digests_recorded_from_sidecars(tmp_path):
    """The results-JSON digests field records sha256/size/date per dataset
    from the verified sidecars (the 'results JSONs digest-pinned' contract)."""
    name = "scifact"
    files = {"corpus.jsonl": "\n".join(CORPUS_LINES) + "\n",
             "queries.jsonl": "\n".join(QUERY_LINES) + "\n",
             "qrels/test.tsv": QRELS_TSV}
    cache = tmp_path / "cache"
    cache.mkdir()
    # fabricate the verified-cache state: extracted files + sidecar record
    for rel, content in files.items():
        p = _cache_file(cache, name, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    mb._record_sidecar(name, cache,
                       {rel: _cache_file(cache, name, rel)
                        for rel in files})
    digests = mb._collect_digests(cache)
    assert name in digests
    entry = digests[name]
    assert entry["files"]["corpus.jsonl"]["sha256"] == \
        _sha256_bytes(files["corpus.jsonl"].encode("utf-8"))
    assert entry["files"]["corpus.jsonl"]["size_bytes"] == \
        len(files["corpus.jsonl"].encode("utf-8"))
    assert entry["source"] == mb.DATASETS[name]["url"]
    assert entry["fetched"]
    # datasets with no record are simply absent
    assert "nfcorpus" not in digests


def test_sample_corpus_reservoir_deterministic_and_bounded():
    corpus = [{"_id": f"d{i}", "title": "", "text": f"text {i}"} for i in range(1000)]
    a = mb.sample_corpus(corpus, n=100, seed=1349)
    b = mb.sample_corpus(corpus, n=100, seed=1349)
    assert [d["_id"] for d in a] == [d["_id"] for d in b]   # deterministic
    assert len(a) == 100
    assert len(set(d["_id"] for d in a)) == 100             # no duplicates
    # n >= corpus → full corpus returned unchanged (order preserved)
    full = mb.sample_corpus(corpus, n=2000, seed=1349)
    assert full == corpus
    # empty corpus → empty sample, no crash
    assert mb.sample_corpus([], n=100, seed=1349) == []
    # 1-passage corpus → the single passage
    one = mb.sample_corpus(corpus[:1], n=100, seed=1349)
    assert one == corpus[:1]


def test_dataset_failure_recorded_not_abort(tmp_path, monkeypatch):
    """P1 (code review): one dataset failing must not discard completed runs —
    the failure is recorded per-dataset and the results JSON still writes."""
    monkeypatch.setattr(mb, "inject_model",
                        lambda name, query_prompt=None: {
                            "name": name, "hf_id": f"hf/{name}",
                            "resolved_revision": None, "query_prompt": None,
                            "dim": 384})
    # P1 (code review): run_main also calls the real EmbeddingModel.get() after
    # probe injection — stub it so the test stays offline (no HF download).
    monkeypatch.setattr(mb.EmbeddingModel, "get", lambda: _FakeModel())
    seen = []

    def _flaky_load(name, cache, download=True):
        seen.append(name)
        if name == "nfcorpus":
            raise mb.DatasetError("nfcorpus: download failed (simulated)")
        return _dummy_dataset()

    def _dummy_dataset():
        cp = tmp_path / "dummy_corpus.jsonl"
        cp.write_text(json.dumps({"_id": "d1", "text": "x"}) + "\n")
        return {"name": "dummy", "corpus_path": str(cp),
                "queries": [], "qrels": {}}

    monkeypatch.setattr(mb, "load_dataset", _flaky_load)
    out = tmp_path / "out"
    res = mb.run_main(["--model", "minilm", "--output-dir", str(out),
                       "--cache-dir", str(tmp_path / "cache")])
    assert "nfcorpus" in res["datasets"]
    assert "error" in res["datasets"]["nfcorpus"]
    assert res["datasets"]["nfcorpus"]["metrics"]["ndcg@10"] is None
    # other datasets proceeded
    assert any("error" not in d for d in res["datasets"].values())
    # results JSON still written
    assert (out / "mini_beir-minilm.json").exists()


def test_network_failure_wraps_as_dataset_error(tmp_path, monkeypatch):
    """P1: urlopen URLError surfaces as DatasetError, not a bare traceback."""
    def _boom(req, timeout=120):
        raise urllib.error.URLError("simulated network down")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(mb.DatasetError):
        mb._download_zip("nfcorpus", tmp_path / "nfcorpus.zip")
