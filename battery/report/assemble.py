"""Profile assembler (issue #1415, plan §4 profile.json + §5 report/).

report.assemble(run_artifacts, thresholds) → Profile: the full
differentiation matrix (14 families × arms, classified), the verdict, the
matched-recall record, and report_status (complete | incomplete_missing_metrics
— never fabricated). Profile schema per plan §4 (value types: numeric |
enum | n/a).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from battery.report.classify import CellClassification, classify_cell
from battery.report.verdict import Verdict, decide_verdict

REPORT_STATUS_OK = "complete"
REPORT_STATUS_INCOMPLETE = "incomplete_missing_metrics"


@dataclass(frozen=True)
class Profile:
    matrix: dict[str, dict[str, dict[str, Any]]]  # family -> arm -> cell
    verdict: Verdict
    matched_recall: dict[str, Any]
    report_status: str
    families_measured: int
    families_expected: int


def assemble(run_artifacts: dict[str, dict[str, dict[str, float]]],
             expected_families: tuple[str, ...],
             mitigation_paths: dict[str, str],
             matched_recall: dict[str, Any] | None = None,
             delta_threshold: float = 0.10) -> Profile:
    """run_artifacts: family -> arm -> value (from the D1 sweep)."""
    # Missing-metrics guard: never fabricate a classification for a family
    # that was not measured (E2E-6.2).
    measured = [f for f in expected_families if f in run_artifacts]
    missing = [f for f in expected_families if f not in run_artifacts]
    status = REPORT_STATUS_OK if not missing else REPORT_STATUS_INCOMPLETE

    matrix: dict[str, dict[str, dict[str, Any]]] = {}
    classifications: list[CellClassification] = []
    for fam in measured:
        arms = run_artifacts[fam]
        matrix[fam] = {}
        for arm, value in arms.items():
            # Best COMPARATOR = max over the OTHER arms (never the cell's
            # own arm — a self-comparison always classifies PARITY).
            best_comparator = max(v for a, v in arms.items() if a != arm)
            cell = classify_cell(fam, arm, value, best_comparator,
                                 delta_threshold)
            classifications.append(cell)
            matrix[fam][arm] = {
                "value": value,
                "delta": value - best_comparator,
                "classification": cell.classification,
                "load_bearing": cell.load_bearing,
            }

    verdict = decide_verdict(classifications, mitigation_paths,
                             matched_recall)
    return Profile(matrix=matrix, verdict=verdict,
                   matched_recall=matched_recall or {},
                   report_status=status,
                   families_measured=len(measured),
                   families_expected=len(expected_families))


def save_profile(profile: Profile, path: str | Path) -> Path:
    """Serialize the profile to profile.json (plan §4 schema)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "matrix": profile.matrix,
        "verdict": {
            "outcome": profile.verdict.outcome,
            "differentiators": list(profile.verdict.differentiators),
            "weaknesses": list(profile.verdict.weaknesses),
            "mitigation_paths": profile.verdict.mitigation_paths,
            "artifacts_changed": list(profile.verdict.artifacts_changed),
        },
        "matched_recall": profile.matched_recall,
        "report_status": profile.report_status,
        "families": {"measured": profile.families_measured,
                     "expected": profile.families_expected},
    }
    p.write_text(json.dumps(payload, indent=2))
    return p
