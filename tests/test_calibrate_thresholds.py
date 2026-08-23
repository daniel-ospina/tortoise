"""Unit tests for tools/calibrate_thresholds.py (#1349 T5).

The tool re-derives the embedding threshold bands for a candidate model
(#399 bands 0.35-0.51 / 0.75 are the MiniLM anchors) by running the
labeled-pair fixture through the model's encode + cosine. Locked contracts:

- band stats monotone in band (p50 strictly increasing: noise < paraphrase
  < dedup < near-dup) — the fixture is the reference distribution;
- empty pair set → explicit error, never NaN thresholds;
- a band below --min-samples → explicit error naming the band;
- model-agnostic re-run determinism (real MiniLM encode — cached; skipped
  under HF_HUB_OFFLINE when the cache is absent);
- suggested thresholds finite and inside their bands.

Error-path tests validate BEFORE any model load (encode_fn=None is legal).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools import calibrate_thresholds as ct
from tools import embedder_probe

pytestmark = pytest.mark.timeout(600)  # real-embedder tests load bge-small (~57s load) — #1349 swap

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "labeled_pairs.jsonl"
BAND_ORDER = ("noise", "paraphrase", "dedup", "near-dup")


def _minilm_cached() -> bool:
    """True when all-MiniLM-L6-v2 loads offline (HF cache present)."""
    try:
        from sentence_transformers import SentenceTransformer
        SentenceTransformer("all-MiniLM-L6-v2", local_files_only=True)
        return True
    except Exception:
        return False


@pytest.fixture(autouse=True)
def _clean_probe_state():
    embedder_probe.reset()
    yield
    embedder_probe.reset()


def _minilm_encode(texts):
    from tortoise.embeddings import _encode
    vecs, degraded = _encode(list(texts))
    assert degraded is False, "probe injection must never degrade to TF-IDF"
    return vecs


def _real_calibration(min_samples: int = 30) -> dict:
    embedder_probe.inject_model("minilm")
    return ct.calibrate(ct.load_pairs(str(FIXTURE)), _minilm_encode,
                        min_samples=min_samples)


@pytest.mark.skipif(
    not _minilm_cached(),
    reason="all-MiniLM-L6-v2 not in HF cache (HF_HUB_OFFLINE in CI)",
)
def test_band_stats_monotone_in_band():
    report = _real_calibration()
    medians = [report["bands"][band]["p50"] for band in BAND_ORDER]
    assert medians == sorted(medians) and len(set(medians)) == len(medians), (
        f"p50 per band must be strictly increasing: {dict(zip(BAND_ORDER, medians))}"
    )


def test_empty_pairs_explicit_error():
    empty = Path(__file__).parent / "fixtures" / "empty_tmp.jsonl"
    empty.write_text("")
    try:
        pairs = ct.load_pairs(str(empty))
        assert pairs == []
        with pytest.raises(ct.CalibrationError, match="[Ee]mpty"):
            ct.calibrate(pairs, encode_fn=None, min_samples=30)
    finally:
        empty.unlink(missing_ok=True)


def test_below_min_samples_explicit_error():
    # 5 noise pairs — far below the 30-pair floor; validation must fire
    # before any encode (encode_fn=None is legal).
    tiny = [{"content_a": f"a{i}", "content_b": f"b{i}",
             "band": "noise", "label": "UNRELATED"} for i in range(5)]
    with pytest.raises(ct.CalibrationError, match="noise"):
        ct.calibrate(tiny, encode_fn=None, min_samples=30)


def test_missing_band_explicit_error():
    pairs = [{"content_a": f"a{i}", "content_b": f"b{i}",
              "band": "near-dup", "label": "NEAR_DUPLICATE"} for i in range(40)]
    with pytest.raises(ct.CalibrationError, match="near-dup|dedup|paraphrase|noise"):
        ct.calibrate(pairs, encode_fn=None, min_samples=30)


def test_min_samples_default_is_30():
    assert ct.DEFAULT_MIN_SAMPLES == 30


@pytest.mark.skipif(
    not _minilm_cached(),
    reason="all-MiniLM-L6-v2 not in HF cache (HF_HUB_OFFLINE in CI)",
)
def test_deterministic_model_agnostic_rerun():
    r1 = json.loads(json.dumps(_real_calibration()))
    r2 = json.loads(json.dumps(_real_calibration()))
    assert r1 == r2, "two runs over the same model must produce identical output"


@pytest.mark.skipif(
    not _minilm_cached(),
    reason="all-MiniLM-L6-v2 not in HF cache (HF_HUB_OFFLINE in CI)",
)
def test_suggested_thresholds_finite_and_in_band():
    report = _real_calibration()
    th = report["suggested_thresholds"]
    for key, value in th.items():
        assert isinstance(value, float) and value == value, f"{key} is NaN"
    para = report["bands"]["paraphrase"]
    near = report["bands"]["near-dup"]
    assert para["min"] <= th["DEFAULT_THRESHOLD"] <= para["max"], (
        "DEFAULT_THRESHOLD must sit inside the paraphrase band")
    assert near["min"] <= th["NEAR_DUPLICATE_THRESHOLD"] <= near["max"], (
        "NEAR_DUPLICATE_THRESHOLD must sit inside the near-dup band")
    assert th["DEFAULT_THRESHOLD"] < th["NEAR_DUPLICATE_THRESHOLD"]
    # MiniLM sanity vs the #399 anchors: paraphrase band ≈ 0.35-0.51,
    # DEFAULT_THRESHOLD ≈ 0.40 (measured all-MiniLM value).
    assert para["min"] >= 0.35 and para["max"] <= 0.51
    assert 0.35 <= th["DEFAULT_THRESHOLD"] <= 0.45


def test_unknown_model_rejected_cleanly(capsys):
    rc = ct.main(["--model", "not-a-model", "--pairs", str(FIXTURE)])
    assert rc != 0
    captured = capsys.readouterr()
    assert "not-a-model" in captured.err + captured.out


def test_cli_writes_report(tmp_path, monkeypatch):
    import numpy as np

    def fake_encode(texts):
        rng = np.random.RandomState(7)
        return rng.rand(len(list(texts)), 384)

    # No real model load: main()'s inject_model is stubbed so the wiring
    # (args → load_pairs → calibrate → out) is tested without the HF cache
    # (the sibling real-model tests carry the model-dependent coverage and
    # skip under HF_HUB_OFFLINE; this one must run everywhere).
    monkeypatch.setattr(ct, "inject_model",
                        lambda name, load_timeout=None: {"name": name})
    monkeypatch.setattr(ct, "_encode_with_model", fake_encode)
    out = tmp_path / "thresholds.json"
    rc = ct.main(["--model", "minilm", "--pairs", str(FIXTURE),
                  "--out", str(out)])
    assert rc == 0
    assert out.exists()
    report = json.loads(out.read_text())
    assert report["model"] == "minilm"


def test_cli_min_samples_flag_threads_through(tmp_path, monkeypatch):
    fixture = tmp_path / "small.jsonl"
    fixture.write_text("\n".join(
        json.dumps({"content_a": f"a{i}", "content_b": f"b{i}",
                    "band": "near-dup", "label": "NEAR_DUPLICATE"})
        for i in range(40)))
    monkeypatch.setattr(ct, "inject_model",
                        lambda name, load_timeout=None: {"name": name})
    rc = ct.main(["--model", "minilm", "--pairs", str(fixture),
                  "--min-samples", "100"])
    assert rc != 0  # near-dup 40 < 100 → explicit error, exit 1


def test_non_finite_cosine_aborts_never_nan():
    import numpy as np

    pairs = [
        {"content_a": f"a{i}", "content_b": f"b{i}", "band": band,
         "label": "UNRELATED"}
        for i, band in enumerate(("near-dup", "dedup", "paraphrase", "noise"))
    ]

    def nan_encode(texts):
        return np.full((len(list(texts)), 384), np.nan)

    with pytest.raises(ct.CalibrationError, match="non-finite|NaN"):
        ct.calibrate(pairs, nan_encode, min_samples=1)
