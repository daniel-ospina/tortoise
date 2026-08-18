"""Schema validation for tests/fixtures/labeled_pairs.jsonl (#1349 T5).

The fixture is the durable recalibration artifact: model-agnostic text pairs
stratified into the four calibration bands (near-dup / dedup / paraphrase /
noise), seeded with the 5 #399 measured anchor pairs verbatim. This test
locks the fixture contract so band coverage can never silently erode (a gap
would unground dedup/checkpoint recalibration, plan surface 14).

Row shape (locked)::
    {"content_a": str, "content_b": str, "label": "IMPLIES|NEAR_DUPLICATE|UNRELATED",
     "band": "near-dup|dedup|paraphrase|noise", "source": "anchor|synthetic|graph"}

Band-label coherence (the fixture's semantic strata, ref. docs/plans/
2026-08-08-399-embedding-matching.md §58-68)::
    near-dup  → NEAR_DUPLICATE   (0.75-0.95 zone — auto-merge candidates)
    dedup     → NEAR_DUPLICATE   (0.60/0.92 review vs auto-merge zone)
    paraphrase → IMPLIES          (0.35-0.51 cross-vocabulary band)
    noise     → UNRELATED        (<0.15 floor — negatives incl. the
                                  boundary 0.291 / weak 0.172 anchors)
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "labeled_pairs.jsonl"

MAX_PAIRS = 200
MIN_PER_BAND = 30

LABELS = {"IMPLIES", "NEAR_DUPLICATE", "UNRELATED"}
BANDS = {"near-dup", "dedup", "paraphrase", "noise"}
SOURCES = {"anchor", "synthetic", "graph"}
BAND_LABEL = {
    "near-dup": "NEAR_DUPLICATE",
    "dedup": "NEAR_DUPLICATE",
    "paraphrase": "IMPLIES",
    "noise": "UNRELATED",
}

# The 5 #399 anchors, verbatim from docs/plans/2026-08-08-399-embedding-
# matching.md §58-68 (the measured MiniLM table), in fixture order.
ANCHOR_ROWS = [
    {"band": "near-dup", "label": "NEAR_DUPLICATE",
     "content_a": "Deployments must be automated for reliability",
     "content_b": "Automating deployments is required for reliability"},
    {"band": "paraphrase", "label": "IMPLIES",
     "content_a": "Growth depends on distribution channels and partnerships",
     "content_b": "Winning requires strong go to market and channel partners"},
    {"band": "noise", "label": "UNRELATED",
     "content_a": "Cost inversion from fixed to variable",
     "content_b": "MVP now costs ~$100"},
    {"band": "noise", "label": "UNRELATED",
     "content_a": "Deployments must be automated for reliability",
     "content_b": "Growth depends on distribution channels and partnerships"},
    {"band": "noise", "label": "UNRELATED",
     "content_a": "quantum physics research papers",
     "content_b": "chocolate chip cookie recipes"},
]


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    if not FIXTURE.exists():
        pytest.fail(f"fixture missing: {FIXTURE}")
    parsed = []
    for lineno, line in enumerate(FIXTURE.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            pytest.fail(f"{FIXTURE.name}:{lineno}: invalid JSON: {exc}")
        parsed.append((lineno, row))
    return [row for _, row in parsed]


def test_fixture_exists():
    assert FIXTURE.exists(), "tests/fixtures/labeled_pairs.jsonl is required"


def test_rows_are_json_objects_with_exact_schema(rows):
    required = {"content_a", "content_b", "label", "band", "source"}
    for row in rows:
        assert set(row) == required, f"unexpected keys: {sorted(set(row) ^ required)}"
        assert isinstance(row["content_a"], str) and row["content_a"].strip()
        assert isinstance(row["content_b"], str) and row["content_b"].strip()
        assert isinstance(row["label"], str)
        assert isinstance(row["band"], str)
        assert isinstance(row["source"], str)


def test_at_most_200_pairs(rows):
    assert len(rows) <= MAX_PAIRS, f"{len(rows)} > {MAX_PAIRS}"


def test_labels_in_enum(rows):
    for row in rows:
        assert row["label"] in LABELS, f"bad label {row['label']!r}"


def test_bands_in_enum(rows):
    for row in rows:
        assert row["band"] in BANDS, f"bad band {row['band']!r}"


def test_sources_in_enum(rows):
    for row in rows:
        assert row["source"] in SOURCES, f"bad source {row['source']!r}"


def test_at_least_30_pairs_per_band(rows):
    counts = Counter(row["band"] for row in rows)
    for band in BANDS:
        assert counts[band] >= MIN_PER_BAND, (
            f"band {band!r} has {counts[band]} pairs (< {MIN_PER_BAND})"
        )


def test_five_399_anchors_verbatim_first(rows):
    assert len(rows) >= 5
    for i, expected in enumerate(ANCHOR_ROWS):
        actual = {k: rows[i][k] for k in ("content_a", "content_b", "label", "band")}
        assert actual == expected, f"anchor row {i} drifted:\n  {actual}"
        assert rows[i]["source"] == "anchor"


def test_anchor_source_only_on_anchor_rows(rows):
    for i, row in enumerate(rows):
        if i < len(ANCHOR_ROWS):
            assert row["source"] == "anchor"
        else:
            assert row["source"] in ("synthetic", "graph")


def test_band_label_coherence(rows):
    for row in rows:
        assert row["label"] == BAND_LABEL[row["band"]], (
            f"label {row['label']!r} inconsistent with band {row['band']!r}"
        )


def test_positive_and_negative_labels_present(rows):
    labels = {row["label"] for row in rows}
    assert labels & {"IMPLIES", "NEAR_DUPLICATE"}, "no positive (matching) label"
    assert "UNRELATED" in labels, "no negative (unrelated) label"


def test_no_duplicate_pairs(rows):
    pairs = [(row["content_a"], row["content_b"]) for row in rows]
    assert len(pairs) == len(set(pairs)), "duplicate (content_a, content_b) pairs"
