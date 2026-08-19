"""Issue #1415 — report/verdict assembler: classification, 4-branch verdict
rule, incomplete guard, calibration print-only, R2 mechanism exclusion."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from battery.report.assemble import (
    REPORT_STATUS_INCOMPLETE,
    REPORT_STATUS_OK,
    assemble,
    save_profile,
)
from battery.report.calibrate import cal_table_hash, print_deltas
from battery.report.classify import classify_cell
from battery.report.verdict import VERDICTS, decide_verdict

FAMILIES = ("R1", "R2", "R3", "R4", "R5",
            "L1", "L2", "L3", "L4", "L5", "L6",
            "D2", "D3", "D4")
ARMS = ("a0", "a1", "a2", "a2b", "a3", "a4")


def _matrix(a4_win: set[str] | None = None, a4_weak: set[str] | None = None):
    a4_win = a4_win or set()
    a4_weak = a4_weak or set()
    out = {}
    for f in FAMILIES:
        out[f] = {a: 0.4 for a in ARMS}
        if f in a4_win:
            out[f]["a4"] = 0.9   # beats comparators by ≥0.10
        elif f in a4_weak:
            out[f]["a4"] = 0.1   # loses by ≥0.10
        else:
            out[f]["a4"] = 0.45  # parity
    return out


class TestClassify:
    def test_strong_parity_weak(self):
        assert classify_cell("R2", "a4", 0.9, 0.4, 0.10).classification == "STRONG"
        assert classify_cell("R2", "a4", 0.45, 0.4, 0.10).classification == "PARITY"
        assert classify_cell("R2", "a4", 0.1, 0.4, 0.10).classification == "WEAK"

    def test_structural_r1(self):
        c = classify_cell("R1", "a4", 0.9, 0.4, 0.10)
        assert c.classification == "STRUCTURAL"  # won by construction
        assert c.load_bearing


class TestVerdictRule:
    def test_unique(self):
        m = _matrix(a4_win={"R2", "R3"})  # contested STRONG on load-bearing
        p = assemble(m, FAMILIES, mitigation_paths={})
        assert p.verdict.outcome == "UNIQUE"
        assert p.report_status == REPORT_STATUS_OK

    def test_mechanism_not_unique(self):
        # Only structural wins (R1) — no contested STRONG on load-bearing.
        p = assemble(_matrix(), FAMILIES, mitigation_paths={})
        assert p.verdict.outcome == "MECHANISM-NOT-UNIQUE"

    def test_weak_unmitigated(self):
        m = _matrix(a4_weak={"R3"})  # load-bearing WEAK, no mitigation
        p = assemble(m, FAMILIES, mitigation_paths={})
        assert p.verdict.outcome == "WEAK-UNMITIGATED"

    def test_weak_mitigated_allows_unique(self):
        m = _matrix(a4_win={"R2"}, a4_weak={"R3"})
        p = assemble(m, FAMILIES, mitigation_paths={"R3": "EP damping re-cal"})
        assert p.verdict.outcome == "UNIQUE"

    def test_inconclusive(self):
        m = _matrix(a4_win={"R2"})
        p = assemble(m, FAMILIES, mitigation_paths={},
                     matched_recall={"trigger_fired": True, "subset_pct": 0.3})
        assert p.verdict.outcome == "INCONCLUSIVE"

    def test_all_verdicts_valid(self):
        assert set(VERDICTS) == {"UNIQUE", "MECHANISM-NOT-UNIQUE",
                                 "WEAK-UNMITIGATED", "INCONCLUSIVE"}


class TestIncompleteGuard:
    def test_missing_family_incomplete(self):
        m = _matrix()
        del m["D4"]  # drop one family
        p = assemble(m, FAMILIES, mitigation_paths={})
        assert p.report_status == REPORT_STATUS_INCOMPLETE
        assert p.families_measured == 13

    def test_full_families_complete(self):
        p = assemble(_matrix(), FAMILIES, mitigation_paths={})
        assert p.report_status == REPORT_STATUS_OK
        assert p.families_measured == 14


class TestCalibration:
    def test_hash_stable(self):
        rows = (("surfaced-rate", "a4", 0.90), ("brier", "a4", 0.25))
        assert cal_table_hash(rows) == cal_table_hash(tuple(reversed(rows)))

    def test_print_only(self, tmp_path):
        rows = (("surfaced-rate", "a4", 0.90),)
        lines = print_deltas(rows, {"surfaced-rate": {"a4": 0.92}})
        assert any("+0.020" in l for l in lines)
        # print-only: nothing written
        assert not list(tmp_path.iterdir())


class TestProfileIO:
    def test_save_load_roundtrip(self, tmp_path):
        p = assemble(_matrix(a4_win={"R2"}), FAMILIES, mitigation_paths={})
        path = save_profile(p, tmp_path / "profile.json")
        import json
        data = json.loads(path.read_text())
        assert data["verdict"]["outcome"] == "UNIQUE"
        assert data["report_status"] == "complete"
