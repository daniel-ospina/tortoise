#!/usr/bin/env python3
"""calibrate_thresholds — per-model threshold calibration from labeled pairs.

The durable recalibration artifact (#1349 T5): embedding cosine thresholds
are MODEL-SPECIFIC, so the embedder swap needs re-runnable calibration, not
hand-tuning. This tool runs tests/fixtures/labeled_pairs.jsonl through a
candidate embedder (via tools/embedder_probe.py inject_model), computes the
pairwise cosine for every pair, groups by band, and emits suggested
threshold bands re-derived for that model.

The #399 measured bands (docs/plans/2026-08-08-399-embedding-matching.md
§58-68) are the MiniLM anchors — this tool reproduces them for MiniLM and
re-derives them for any candidate:

    DEFAULT_THRESHOLD          = p25 of the paraphrase band
                                (75% of cross-vocab matches clear the bar;
                                 MiniLM ≈ 0.405 ≈ the #399 0.40 value)
    NEAR_DUPLICATE_THRESHOLD   = p5 of the near-dup band
                                (95% of near-duplicates clear the bar)
    DEDUP_REVIEW_THRESHOLD     = p25 of the dedup band (review tier)
    DEDUP_AUTO_MERGE_THRESHOLD = p95 of the dedup band (auto-merge tier)

    Band semantics (MiniLM reference, #399 §58-68): near-dup 0.75-0.95,
    dedup 0.60/0.92, paraphrase 0.35-0.51, noise = negatives — p50 < 0.15
    floor, with the #399 boundary (0.291) / weak (0.172) anchors in the
    tail of the noise band by design (they must NOT cross DEFAULT).

Fail-closed: an empty pair set or any band below --min-samples is an
EXPLICIT error (exit 1) — a calibration with missing evidence never emits
thresholds (no NaN bands, no silent gaps).

Deterministic: encode + cosine are fixed functions of the model; no RNG is
used anywhere in the computation, so re-running over the same model +
fixture yields byte-identical output.

Usage:
    python tools/calibrate_thresholds.py --model minilm \
        --pairs tests/fixtures/labeled_pairs.jsonl --out thresholds.json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Callable

# Make the tools package importable when run directly (python tools/xxx.py).
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tools.embedder_probe import (  # noqa: E402
    PROBE_MODELS,
    EmbedderProbeError,
    inject_model,
)

#: Band order = calibration strata (low → high similarity).
BANDS = ("near-dup", "dedup", "paraphrase", "noise")

#: Default floor for per-band sample counts (matches the fixture's ≥30/band).
DEFAULT_MIN_SAMPLES = 30


class CalibrationError(ValueError):
    """Invalid calibration inputs — fail closed, never emit NaN thresholds."""


def load_pairs(path: str) -> list[dict]:
    """Load + shape-validate the labeled-pairs JSONL."""
    pairs: list[dict] = []
    for lineno, line in enumerate(Path(path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CalibrationError(f"{path}:{lineno}: invalid JSON: {exc}")
        if not isinstance(row, dict):
            raise CalibrationError(f"{path}:{lineno}: row must be an object")
        missing = {"content_a", "content_b", "band"} - set(row)
        if missing:
            raise CalibrationError(f"{path}:{lineno}: missing keys {sorted(missing)}")
        if row["band"] not in BANDS:
            raise CalibrationError(
                f"{path}:{lineno}: band {row['band']!r} not in {BANDS}")
        pairs.append(row)
    return pairs


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Deterministic nearest-rank percentile on a sorted list (q in [0, 1]).

    rank = ceil(q·n) 1-based — the standard nearest-rank convention
    (Hyndman–Fan R6-style for percentiles); deterministic for a fixed
    input, no RNG, no cross-version interpolation subtleties.
    """
    import math
    if not sorted_vals:
        raise CalibrationError("percentile over an empty band")
    n = len(sorted_vals)
    rank = max(int(math.ceil(q * n)), 1)
    return sorted_vals[min(rank - 1, n - 1)]


def _band_stats(cosines: list[float]) -> dict:
    if not cosines:
        raise CalibrationError("band with no measured pairs")
    vals = sorted(cosines)
    n = len(vals)
    return {
        "n": n,
        "min": vals[0],
        "p5": _percentile(vals, 0.05),
        "p25": _percentile(vals, 0.25),
        "p50": _percentile(vals, 0.50),
        "p75": _percentile(vals, 0.75),
        "p95": _percentile(vals, 0.95),
        "max": vals[-1],
    }


def _suggest_thresholds(stats: dict[str, dict]) -> dict[str, float]:
    """Derive the suggested thresholds from the per-band stats.

    Percentiles of the band's OWN measured distribution (never cross-band
    arithmetic) so the suggestion is a property of that model + fixture.
    """
    return {
        "DEFAULT_THRESHOLD": stats["paraphrase"]["p25"],
        "NEAR_DUPLICATE_THRESHOLD": stats["near-dup"]["p5"],
        "DEDUP_REVIEW_THRESHOLD": stats["dedup"]["p25"],
        "DEDUP_AUTO_MERGE_THRESHOLD": stats["dedup"]["p95"],
    }


def calibrate(
    pairs: list[dict],
    encode_fn: Callable[[list[str]], object] | None,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    model_name: str = "unknown",
) -> dict:
    """Full calibration: validate inputs → encode → band stats → thresholds.

    ``encode_fn`` maps a list of texts to a (n, dim) numeric array. The
    empty/<min-samples validation fires BEFORE any encode, so callers can
    pass encode_fn=None to exercise the fail-closed paths.

    Raises CalibrationError on empty pairs, a band below min_samples, or
    non-finite computed cosines — a calibration never emits NaN bands.
    """
    if not pairs:
        raise CalibrationError(
            "empty pair set — no thresholds can be derived (refusing to emit "
            "NaN bands)"
        )
    counts = Counter(row["band"] for row in pairs)
    below = {band: counts[band] for band in BANDS if counts[band] < min_samples}
    if below:
        raise CalibrationError(
            "band(s) below min_samples="
            f"{min_samples}: {dict(sorted(below.items()))} — refusing to "
            "calibrate (a band without evidence cannot pin a threshold)"
        )
    if encode_fn is None:
        raise CalibrationError("encode_fn is required for calibration")

    # Encode each unique text once; pairs reuse the vectors.
    texts = sorted({row["content_a"] for row in pairs}
                   | {row["content_b"] for row in pairs})
    vecs = list(encode_fn(texts))
    vec_map = {text: vec for text, vec in zip(texts, vecs)}

    cosines: dict[str, list[float]] = {band: [] for band in BANDS}
    for row in pairs:
        sim = _cosine(vec_map[row["content_a"]], vec_map[row["content_b"]])
        if not _finite(sim):
            raise CalibrationError(
                f"non-finite cosine {sim!r} for pair "
                f"{row['content_a']!r} / {row['content_b']!r} — aborting "
                "(never emit NaN bands)"
            )
        cosines[row["band"]].append(sim)

    stats = {band: _band_stats(cosines[band]) for band in BANDS}

    return {
        "model": model_name,
        "n_pairs": len(pairs),
        "min_samples": min_samples,
        "bands": stats,
        "suggested_thresholds": _suggest_thresholds(stats),
    }


def _cosine(a: object, b: object) -> float:
    import numpy as np
    va, vb = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    na, nb = np.linalg.norm(va), np.linalg.norm(vb)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def _finite(value: float) -> bool:
    import math
    return math.isfinite(value)


def _encode_with_model(texts: list[str]) -> object:
    """Encode via the probe-injected EmbeddingModel singleton (#1349 T1).

    inject_model() HARD-FAILS on load/dim problems, so the model is real;
    the degraded flag is still asserted — the calibration must never run
    on TF-IDF stand-ins.
    """
    from tortoise.embeddings import _encode
    vecs, degraded = _encode(texts)
    if degraded:
        raise CalibrationError(
            "embedding encode degraded (TF-IDF fallback) — refusing to "
            "calibrate on degraded vectors"
        )
    return vecs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="calibrate_thresholds",
        description="Re-derive embedding threshold bands for a candidate "
                    "model from the labeled-pair fixture (#1349).",
    )
    parser.add_argument("--model", required=True,
                        help=f"probe model short name: {sorted(PROBE_MODELS)}")
    parser.add_argument("--pairs", required=True,
                        help="labeled-pairs JSONL (tests/fixtures/labeled_pairs.jsonl)")
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES,
                        help=f"minimum pairs per band (default {DEFAULT_MIN_SAMPLES})")
    parser.add_argument("--out", default=None,
                        help="write the thresholds JSON to this file")
    args = parser.parse_args(argv)

    if args.model not in PROBE_MODELS:
        print(f"calibrate_thresholds: error: unknown model {args.model!r} — "
              f"known: {sorted(PROBE_MODELS)}", file=sys.stderr)
        return 1

    try:
        pairs = load_pairs(args.pairs)
        inject_model(args.model)  # HARD-FAILs on load/dim/degrade (embedder_probe)
        report = calibrate(pairs, _encode_with_model, min_samples=args.min_samples,
                           model_name=args.model)
    except (OSError, CalibrationError, EmbedderProbeError) as exc:
        print(f"calibrate_thresholds: error: {exc}", file=sys.stderr)
        return 1

    payload = json.dumps(report, indent=2)
    if args.out:
        Path(args.out).write_text(payload + "\n")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
