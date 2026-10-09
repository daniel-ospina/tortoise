"""#2886 — census ``frequency/count`` disposition tool tests.

Embedded-safe: pure over the committed census + 133-Q outcomes (no DB, no
network). Locks the disposition rule table and the acceptance summary.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.longmem_eval import freq_count_disposition as fcd


def test_load_census_has_12_frequency_count_rows():
    census = fcd.load_census()
    rows = [r for r in census["rows"] if r["cls"] == fcd.CLASS]
    assert len(rows) == 12


def test_load_outcomes_has_all_12_class_members():
    census = fcd.load_census()
    outcomes = fcd.load_outcomes()
    qids = {r["qid"] for r in census["rows"] if r["cls"] == fcd.CLASS}
    missing = qids - set(outcomes)
    assert not missing, f"class members without a committed outcome: {missing}"


def test_disposition_rule_table():
    assert fcd.disposition_for(
        {"qid": "x", "gold_admitted": False, "label": False}) == "structural"
    assert fcd.disposition_for(
        {"qid": "x_abs", "gold_admitted": False,
         "label": True}) == "abstention-control"
    assert fcd.disposition_for(
        {"qid": "x", "gold_admitted": True,
         "label": True}) == "fixed-by-admission"
    assert fcd.disposition_for(
        {"qid": "x", "gold_admitted": True, "label": False}) == "conversion"


def test_is_answerable_excludes_abs_controls():
    assert fcd.is_answerable("c8090214") is True
    assert fcd.is_answerable("c8090214_abs") is False


def test_build_rows_and_summary_are_the_measured_split():
    """The committed 133-Q data gives 11 structural answerable rows + 1
    abstention control, 0 observed conversion, and conversion is
    UNREACHABLE (undetermined) — no class member ever had gold admitted."""
    census = fcd.load_census()
    outcomes = fcd.load_outcomes()
    rows = fcd.build_rows(census, outcomes)
    assert len(rows) == 12
    summary = fcd.summarize(rows)
    assert summary["n"] == 12
    assert summary["n_answerable"] == 11
    assert summary["structural"] == 11
    assert summary["abstention_controls"] == 1
    assert summary["conversion"] == 0
    assert summary["fixed_by_admission"] == 0
    assert summary["unmeasured"] == 0
    assert summary["conversion_undetermined"] is True


def test_every_row_is_reclassified_to_a_date_shape():
    """No class member is a count/frequency surface — all reclassify to a
    date-arithmetic shape (the measured mislabelling finding)."""
    census = fcd.load_census()
    rows = fcd.build_rows(census, fcd.load_outcomes())
    for r in rows:
        assert r["aggregate_kind"] in (
            "interval", "before-offset", "duration", "total"), r
        assert r["aggregate_unit"] in ("days", "weeks", "months", "years"), r


def test_build_rows_is_deterministic():
    census = fcd.load_census()
    outcomes = fcd.load_outcomes()
    assert fcd.build_rows(census, outcomes) == fcd.build_rows(census, outcomes)
