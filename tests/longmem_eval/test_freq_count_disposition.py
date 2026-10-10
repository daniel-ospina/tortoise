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
    # An abstention-DESIGN row is a correct refusal only when the row RECORDS
    # the refusal — answering an abstention row must not read as a pass.
    assert fcd.disposition_for(
        {"qid": "x_abs", "gold_admitted": False, "reader_refusal": True,
         "label": True}) == "abstention-control"
    assert fcd.disposition_for(
        {"qid": "x_abs", "gold_admitted": False, "reader_refusal": False,
         "label": True}) == "unmeasured"
    assert fcd.disposition_for(
        {"qid": "x", "gold_admitted": True,
         "label": True}) == "fixed-by-admission"
    assert fcd.disposition_for(
        {"qid": "x", "gold_admitted": True, "label": False}) == "conversion"
    # An ABSENT field is not evidence of non-admission: it must never be
    # reported as a disposition the row did not carry.
    assert fcd.disposition_for({"qid": "x"}) == "unmeasured"
    assert fcd.disposition_for(
        {"qid": "x", "gold_admitted": True}) == "unmeasured"


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
    """The union scan is reported SEPARATELY from the loaded-arm flag.

    ``conversion_undetermined`` must track the rows being summarized: a
    ``conversion`` of 0 is a MEASUREMENT only if THOSE rows observed gold
    admitted. An admission in another arm (the union scan) never tested
    them, so it cannot make the zero measured — it is reported on its own as
    ``conversion_reachable_any_arm``."""
    census = fcd.load_census()
    rows = fcd.build_rows(census, fcd.load_outcomes())
    loaded = fcd.summarize(rows)
    assert loaded["conversion_undetermined"] is True
    assert loaded["conversion_reachable_any_arm"] is False
    union = fcd.gold_admitted_qids(census)
    assert fcd.summarize(
        rows, reachable_qids=union)["conversion_reachable_any_arm"] is False
    # A qid reachable in ANOTHER arm does not make THIS arm's zero measured;
    # it surfaces as the separate union fact instead.
    mixed = fcd.summarize(rows, reachable_qids={"b46e15ed"})
    assert mixed["conversion_undetermined"] is True
    assert mixed["conversion_reachable_any_arm"] is True


def test_rows_carry_the_2578_pool_fields():
    """The acceptance asks for the 2578 row shape: every source row carries
    ``pool_limit``/``pool_depth``, so the emitted rows must too — a joiner
    reading the pool geometry must not KeyError on this file."""
    rows = fcd.build_rows(fcd.load_census(), fcd.load_outcomes())
    assert rows
    for row in rows:
        assert "pool_limit" in row and "pool_depth" in row


def test_union_scan_reports_missing_sources():
    """A declared source that was NOT read must be visible: an empty union must
    never be read as "no class member was ever admitted" when a file its claim
    depends on was simply absent (absence is not evidence)."""
    missing_path = "docs/runbook/does-not-exist.jsonl"
    admitted, scanned, missing = fcd.gold_admitted_scan(
        fcd.load_census(), sources=[missing_path, fcd.OUTCOMES_DEFAULT])
    assert admitted == set()
    assert missing == [missing_path]
    assert scanned == [fcd.OUTCOMES_DEFAULT]
    summary = fcd.summarize(
        fcd.build_rows(fcd.load_census(), fcd.load_outcomes()),
        reachable_qids=admitted, union_sources_missing=missing)
    assert summary["union_sources_missing"] == [missing_path]
    assert summary["conversion_reachable_any_arm"] is False


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


def test_resolution_readout_from_arm_verdict():
    """The #2886 wiring read-out: a committed outcome carrying the arm's
    verdict reports ``resolved`` when a value was published and
    ``abstained:<reason>`` when the owner declined — never guessing. An
    outcome without a verdict is ``unmeasured`` (the arm was OFF)."""
    assert fcd.resolution_for(
        {"qid": "x", "temporal_aggregate_verdict": {"value": 30,
                                                    "reason": None}}) \
        == "resolved"
    assert fcd.resolution_for(
        {"qid": "x", "temporal_aggregate_verdict": {
            "value": None, "reason": "no_anchors"}}) == "abstained:no_anchors"
    assert fcd.resolution_for({"qid": "x"}) == "unmeasured"
    # A malformed verdict (not a mapping) is not a resolution.
    assert fcd.resolution_for(
        {"qid": "x", "temporal_aggregate_verdict": "garbage"}) == "unmeasured"


def test_summary_counts_deterministic_resolution():
    """The summary surfaces the arm read-out; the committed (arm-OFF) rows
    are all ``unmeasured`` and the split is not invented."""
    rows = fcd.build_rows(fcd.load_census(), fcd.load_outcomes())
    summary = fcd.summarize(rows)
    assert summary["deterministic_resolution"] == {
        "resolved": 0, "abstained": 0, "unmeasured": 12}
    assert all(r["deterministic_resolution"] == "unmeasured" for r in rows)
