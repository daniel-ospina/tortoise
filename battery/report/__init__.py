"""Report/verdict assembler + calibration (issue #1415): the full
differentiation profile (14 families × arms, STRONG/STRUCTURAL/PARITY/WEAK
+ load-bearing), the 4-branch pre-committed verdict rule, report_status
incomplete guard (never fabricated), and the [cal] print-only calibration
mode. R2's mechanism gate is excluded from the verdict classification
(spec §R2 — AC-D1 counts the judged subscore only).
"""
from __future__ import annotations

from battery.report.assemble import (
    REPORT_STATUS_EMITTER_GAP,
    REPORT_STATUS_INCOMPLETE,
    REPORT_STATUS_OK,
    REPORT_STATUS_REAL_NO_EPISODES,
    REPORT_STATUS_REAL_OVER_BUDGET,
    REPORT_STATUS_REAL_PARTIAL,
    Profile,
    assemble,
    attempt_dir_resolve,
    compose_run_status,
    read_family_file,
    save_profile,
    write_family_files,
    write_recall_file,
)
from battery.report.calibrate import cal_table_hash, print_deltas
from battery.report.classify import (
    CLASSIFICATIONS,
    LOAD_BEARING_FAMILIES,
    STRUCTURAL_FAMILIES,
    CellClassification,
    classify_cell,
)
from battery.report.verdict import (
    ARTIFACTS_CHANGED,
    VERDICTS,
    Verdict,
    decide_verdict,
)

__all__ = [
    "ARTIFACTS_CHANGED", "CLASSIFICATIONS", "LOAD_BEARING_FAMILIES",
    "REPORT_STATUS_EMITTER_GAP", "REPORT_STATUS_INCOMPLETE",
    "REPORT_STATUS_OK", "REPORT_STATUS_REAL_NO_EPISODES",
    "REPORT_STATUS_REAL_OVER_BUDGET", "REPORT_STATUS_REAL_PARTIAL",
    "STRUCTURAL_FAMILIES", "VERDICTS", "CellClassification", "Profile",
    "Verdict", "assemble", "attempt_dir_resolve", "cal_table_hash",
    "classify_cell", "compose_run_status", "decide_verdict",
    "print_deltas", "read_family_file", "save_profile",
    "write_family_files", "write_recall_file",
]
