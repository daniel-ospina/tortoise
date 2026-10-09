"""#2886 — census ``frequency/count`` disposition tool tests.

Embedded-safe: pure over the committed census + 133-Q outcomes (no DB, no
network). Locks the disposition rule table and the acceptance summary.
"""
from __future__ import annotations

import json
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


def test_reachability_union_scan_covers_every_committed_source():
    """Regression (P3): the reachability flag must be derived from the union
    over the committed outcomes files (all arms), not only the default
    133-Q arm. Asserts the 8-arm file is among the declared sources and that
    the scan is exactly those two files (the honesty caveat enumerates
    them)."""
    names = [Path(s).name for s in fcd.OUTCOME_SOURCES]
    assert "2578-measured-outcomes-133.jsonl" in names
    assert "2578-measured-outcomes.jsonl" in names
    assert len(fcd.OUTCOME_SOURCES) == 2


def test_gold_admitting_arms_are_exactly_three_and_named():
    """Regression (P2): the honesty caveat names EVERY arm carrying
    gold-admitted rows, not two. The committed 8-arm file has three:
    ``applied-rerank`` 21, ``cap3-only`` 21, ``tr_top_k24`` 1 (qid
    ``8c18457d``). None covers a class member, so conversion stays
    undetermined. Fails if the wording drops ``tr_top_k24`` or a new
    gold-admitting arm appears without the caveat being updated."""
    counts: dict[str, int] = {}
    qids: dict[str, list[str]] = {}
    with open(fcd._resolve(fcd.OUTCOMES_8ARM), encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("gold_admitted"):
                arm = row["arm"]
                counts[arm] = counts.get(arm, 0) + 1
                qids.setdefault(arm, []).append(str(row["qid"]))
    assert counts == {"applied-rerank": 21, "cap3-only": 21, "tr_top_k24": 1}
    assert qids["tr_top_k24"] == ["8c18457d"]


def test_reachability_scan_reads_the_8_arm_file():
    """Regression (P3): prove the union scan actually reads the 8-arm file.
    A synthetic census that claims an 8-arm gold-admitted qid as a class
    member must have it surfaced — a qid the default 133-Q arm never loads.
    Fails on the pre-fix code, which only loaded ``A-default-133q`` from the
    133-Q file and knew nothing of the 8-arm file."""
    # gpt4_213fd887 is gold_admitted=False in the 133-Q arm but
    # gold_admitted=True in the 8-arm file.
    assert not fcd.load_outcomes()["gpt4_213fd887"].get("gold_admitted")
    census = {"rows": [{"qid": "gpt4_213fd887", "cls": fcd.CLASS,
                        "question": "How many weeks in total ..."}]}
    assert "gpt4_213fd887" in fcd.gold_admitted_qids(census)


def test_summarize_honours_union_reachable_qids():
    """The union scan overrides the loaded-arm derivation: a class qid
    gold-admitted anywhere makes conversion DETERMINATE (flag False), even
    though the loaded arm shows no admission."""
    census = fcd.load_census()
    rows = fcd.build_rows(census, fcd.load_outcomes())
    assert fcd.summarize(rows)["conversion_undetermined"] is True
    union = fcd.gold_admitted_qids(census)
    assert fcd.summarize(
        rows, reachable_qids=union)["conversion_undetermined"] is True
    # A reachable qid flips the flag — the parameter is wired, not ignored.
    assert fcd.summarize(
        rows, reachable_qids={"b46e15ed"})["conversion_undetermined"] is False


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
