"""Calibration harness for the banded semantic judge (issue #5085).

The owner's D14 resolution asks for a **concrete, ready-to-label sample**
(n ≥ 30 units spanning all four bands) so the judged bands can be validated
with a chance-corrected agreement statistic (κ / α).  This module builds that
sample from a completed run's additive ``semantic_judge`` block, and computes
Cohen's κ / Krippendorff's α **once a human has filled the labels in**.

⛔ It NEVER invents a κ.  Until every item carries a ``human_band`` the
report is ``labels_pending: true`` with ``cohen_kappa: null`` /
``krippendorff_alpha: null`` — a number nobody measured is not a number.

Pure + hermetic: no DB/network/LLM import surface (it consumes a receipt
document), so it is unit-testable in isolation.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests.eval.write_path import judge

CALIBRATION_SCHEMA_VERSION = 1

_INSTRUCTIONS = (
    "Read each item's PROBE (the claim the session was expected to retain) "
    "and MEMORY NOTES (what the write path actually stored), then set "
    '"human_band" to exactly one of: '
    "same_fact (the memory states the same fact), "
    "likely (probably the same fact), "
    "maybe (related but the claim is not clearly preserved), "
    "likely_not (the fact is not preserved, or is contradicted). "
    "Label MEANING, not wording. Leave the judge_* fields untouched."
)


def build_calibration_sample(
    semantic_block: dict, *, n: int = 30, run_id: str | None = None
) -> dict:
    """Build a labelable sample from a run's ``semantic_judge`` block.

    Selection is deterministic and stratified by the JUDGE'S own band: each
    band's units are ordered low→high probability and drawn alternately from
    the two ends, then rounds interleave the four bands.  That spans each
    band's probability range (not just its centre) into n records; if a band
    holds fewer units than its share the sample says so honestly instead of
    padding from another band.

    Requires the block to carry ``units`` (each with ``session_id``,
    ``unit_id``, ``probe``, ``probability``, ``band``) and
    ``memory_by_session``.
    """
    if n < 1:
        raise ValueError(f"n must be >= 1, got {n!r}")
    units = [u for u in semantic_block.get("units", []) if isinstance(u, dict)]
    memory_by_session = semantic_block.get("memory_by_session") or {}

    by_band: dict[str, list[dict]] = {band: [] for band in judge.BAND_ORDER}
    for unit in units:
        probability = float(unit.get("probability") or 0.0)
        band = unit.get("band") or judge.band_for_probability(probability)
        by_band.setdefault(band, []).append(unit)
    # Alternate low/high ends within a band (range coverage, deterministic).
    picks: dict[str, list[dict]] = {}
    for band in judge.BAND_ORDER:
        items = sorted(
            by_band.get(band, []),
            key=lambda u: (float(u.get("probability") or 0.0), str(u.get("unit_id"))),
        )
        ordered_band: list[dict] = []
        lo, hi = 0, len(items) - 1
        while lo <= hi:
            ordered_band.append(items[lo])
            lo += 1
            if lo <= hi:
                ordered_band.append(items[hi])
                hi -= 1
        picks[band] = ordered_band

    selected: list[dict] = []
    round_index = 0
    while len(selected) < n and any(
        len(picks[band]) > round_index for band in judge.BAND_ORDER
    ):
        for band in judge.BAND_ORDER:
            if len(selected) >= n:
                break
            if len(picks[band]) > round_index:
                selected.append(picks[band][round_index])
        round_index += 1

    items: list[dict] = []
    for index, unit in enumerate(selected):
        session_id = unit.get("session_id")
        band = unit.get("band") or judge.band_for_probability(
            float(unit.get("probability") or 0.0)
        )
        items.append(
            {
                "item_id": f"cal{index + 1:03d}",
                "session_id": session_id,
                "unit_id": unit.get("unit_id"),
                "probe": unit.get("probe"),
                "judge_probability": unit.get("probability"),
                "judge_band": band,
                "judge_votes": {
                    "yes": unit.get("votes_yes"),
                    "total": unit.get("votes_total"),
                },
                "memory_notes": list(memory_by_session.get(session_id, [])),
                "human_band": None,
            }
        )

    band_span = {band: 0 for band in judge.BAND_ORDER}
    for item in items:
        band_span[item["judge_band"]] = band_span.get(item["judge_band"], 0) + 1
    present = [band for band in judge.BAND_ORDER if band_span.get(band)]
    return {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "source_run_id": run_id or semantic_block.get("run_id"),
        "semantic_judge_pin": semantic_block.get("pin"),
        "source_units_total": len(units),
        "n_requested": n,
        "n_units": len(items),
        "bands": list(judge.BAND_ORDER),
        "band_span": band_span,
        "bands_present": present,
        "bands_missing": [
            band for band in judge.BAND_ORDER if band not in present
        ],
        "instructions": _INSTRUCTIONS,
        "labels_pending": True,
        "cohen_kappa": None,
        "krippendorff_alpha": None,
        "kappa_note": (
            "pending human labels — κ/α cannot be computed until `human_band` "
            "is filled for every item; NO value is asserted here"
        ),
        "items": items,
    }


# ── Chance-corrected agreement ──────────────────────────────────────────────


def cohen_kappa(judge_labels: list[str], human_labels: list[str]) -> float:
    """Nominal Cohen's κ between the judge's bands and a human's bands.

    Two raters, the same items, a closed label set (the four bands).  Raises
    ``ValueError`` on a shape mismatch, an unknown label, an empty input, or
    the degenerate case where expected agreement is 1.0 (κ undefined — the
    caller reports ``null``, never a fabricated 0/1).
    """
    if len(judge_labels) != len(human_labels):
        raise ValueError(
            "cohen_kappa: rater lengths differ "
            f"({len(judge_labels)} vs {len(human_labels)})"
        )
    n = len(judge_labels)
    if n == 0:
        raise ValueError("cohen_kappa: no items")
    allowed = set(judge.BAND_ORDER)
    labels = sorted(set(judge_labels) | set(human_labels))
    unknown = [label for label in labels if label not in allowed]
    if unknown:
        raise ValueError(f"cohen_kappa: unknown band label(s) {unknown}")
    observed = sum(1 for a, b in zip(judge_labels, human_labels, strict=True) if a == b)
    po = observed / n
    pe = 0.0
    for label in labels:
        pe += (judge_labels.count(label) / n) * (human_labels.count(label) / n)
    if pe >= 1.0:
        raise ValueError(
            "cohen_kappa: undefined — expected agreement is 1.0 "
            "(both raters constant with identical marginals)"
        )
    return (po - pe) / (1.0 - pe)


def krippendorff_alpha(coders: list[list[str | None]]) -> float:
    """Nominal Krippendorff's α over ``coders × units`` with missing values.

    ``coders[j][i]`` is coder ``j``'s label for unit ``i`` (``None`` when
    that coder did not rate the unit; a shorter list is padded with None).
    Standard coincidence-matrix form.  Raises ``ValueError`` when fewer than
    two coders are supplied, when fewer than two units carry two or more
    codings (α undefined), or when a label is outside the band vocabulary.
    """
    if len(coders) < 2:
        raise ValueError("krippendorff_alpha: needs at least 2 coders")
    allowed = set(judge.BAND_ORDER)
    n_units = max((len(c) for c in coders), default=0)
    coincidence: dict[tuple[str, str], float] = {}
    marginals: dict[str, float] = {}
    total = 0.0
    eligible_units = 0
    for index in range(n_units):
        values = [
            coder[index] for coder in coders
            if index < len(coder) and coder[index] is not None
        ]
        for value in values:
            if value not in allowed:
                raise ValueError(f"krippendorff_alpha: unknown band label {value!r}")
        m_u = len(values)
        if m_u < 2:
            continue
        eligible_units += 1
        for i, a in enumerate(values):
            for j, b in enumerate(values):
                if i == j:
                    continue
                coincidence[(a, b)] = coincidence.get((a, b), 0.0) + 1.0 / (m_u - 1)
        total += m_u
    if eligible_units < 2:
        raise ValueError(
            "krippendorff_alpha: undefined — fewer than 2 units carry ≥2 codings"
        )
    for (a, _b), value in coincidence.items():
        marginals[a] = marginals.get(a, 0.0) + value
    observed = sum(
        value for (a, b), value in coincidence.items() if a != b
    )
    expected = 0.0
    for c, n_c in marginals.items():
        for k, n_k in marginals.items():
            if c != k:
                expected += n_c * n_k
    expected /= total - 1.0
    if expected == 0.0:
        raise ValueError(
            "krippendorff_alpha: undefined — expected disagreement is 0"
        )
    return 1.0 - (observed / expected)


def calibration_report(sample: dict) -> dict:
    """κ/α status for a calibration sample — pending until labels exist.

    ``labels_pending`` stays True while ANY item lacks a ``human_band``.
    A partially-labeled sample reports progress and NO statistic (a κ on a
    subset would be a different measurement, silently mislabeled).
    """
    items = [item for item in sample.get("items", []) if isinstance(item, dict)]
    labeled = [item for item in items if item.get("human_band")]
    pending = len(items) - len(labeled)
    report = {
        "n_units": len(items),
        "n_labeled": len(labeled),
        "n_pending": pending,
        "labels_pending": pending > 0,
        "bands_present": sample.get("bands_present"),
        "bands_missing": sample.get("bands_missing"),
        "cohen_kappa": None,
        "krippendorff_alpha": None,
        "kappa_note": sample.get("kappa_note") or (
            "pending human labels" if pending else ""
        ),
    }
    if pending > 0:
        return report
    if not items:
        return report
    judge_labels = [item["judge_band"] for item in items]
    human_labels = [item["human_band"] for item in items]
    try:
        report["cohen_kappa"] = round(cohen_kappa(judge_labels, human_labels), 6)
    except ValueError as exc:
        report["kappa_note"] = f"cohen_kappa undefined: {exc}"
    try:
        report["krippendorff_alpha"] = round(
            krippendorff_alpha([judge_labels, human_labels]), 6
        )
    except ValueError as exc:
        report["kappa_note"] = (
            (report["kappa_note"] + "; " if report["kappa_note"] else "")
            + f"krippendorff_alpha undefined: {exc}"
        )
    return report


def load_sample(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_sample(sample: dict, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sample, indent=2) + "\n", encoding="utf-8")
    return path
