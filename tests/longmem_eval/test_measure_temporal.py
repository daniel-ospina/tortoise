"""#2578 (Task 2) — measurement scaffold tests: census loader, 55-Q pin,
pre-registration writer, refusal classifier.

Embedded-safe (committed JSON + pure string logic — no DB, no network).
Follows the plan's Task 2 acceptance line by line.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.longmem_eval import measure_temporal as mt  # noqa: E402, RUF100
from tortoise.reader import _looks_abstained

CENSUS = Path(__file__).resolve().parent.parent / "_assembly_census.json"
CORPUS = Path(__file__).resolve().parent / "_refusal_calibration.json"

EXPECTED_ABS_CONTROLS = ("gpt4_93159ced_abs", "gpt4_c27434e8_abs",
                         "gpt4_fe651585_abs")


# ── Census loader + 55-Q pin ───────────────────────────────────────────────

def test_census_loads_133_rows():
    census = mt.load_census(CENSUS)
    assert census["n"] == 133
    assert len(census["rows"]) == 133
    assert {"qid", "question", "cls"} <= set(census["rows"][0])


def test_deterministic_subset_is_exactly_55():
    census = mt.load_census(CENSUS)
    subset = mt.deterministic_subset(census["rows"])
    # the pin: ordering/compare 34 + interval 19 + current-state 2 = 55.
    # Exact-string match — recency/current-state (8) is NOT in this subset.
    assert len(subset) == 55
    assert {r["cls"] for r in subset} == set(mt.ANALYSIS_CLASSES)
    assert len([r for r in subset if r["cls"] == "ordering/compare"]) == 34
    assert len([r for r in subset if r["cls"] == "interval"]) == 19
    assert len([r for r in subset if r["cls"] == "current-state"]) == 2


def test_abs_controls_enumerated_in_subset():
    census = mt.load_census(CENSUS)
    subset = mt.deterministic_subset(census["rows"])
    qids = {r["qid"] for r in subset}
    for q in EXPECTED_ABS_CONTROLS:
        assert q in qids, f"{q} must be enumerated as an abstention control"
    assert all(mt.is_abs_control(q) for q in EXPECTED_ABS_CONTROLS)


def test_abs_control_membership_boundaries():
    # the 3 controls are controls; the other 52 subset qids are not
    assert mt.is_abs_control("gpt4_59149c77") is False
    assert mt.is_abs_control("totally-made-up-qid") is False


def test_duplicated_session_qid_deduped_before_materialize():
    census = mt.load_census(CENSUS)
    subset = mt.deterministic_subset(census["rows"])
    qids = {r["qid"] for r in subset}
    assert mt.DUPLICATED_SESSION_QID in qids  # still enumerated (55 pin)
    # synthetic instance carrying the runbook-documented artifact: a
    # content-identical duplicated haystack_session_id
    dupe_sessions = [
        {"session_id": "s_dup", "role": "user", "content": "same text"},
        {"session_id": "s_dup", "role": "user", "content": "same text"},
        {"session_id": "s_other", "role": "assistant", "content": "x"},
    ]
    inst = {"question_id": mt.DUPLICATED_SESSION_QID,
            "haystack_session_ids": ["s_dup", "s_dup", "s_other"],
            "haystack_sessions": dupe_sessions}
    cleaned = mt.dedup_instance_sessions(inst)
    assert len(cleaned["haystack_sessions"]) == 2  # duplicate removed
    assert cleaned["haystack_session_ids"] == ["s_dup", "s_other"]
    # zero information loss: the kept duplicate is content-identical
    kept = [s for s in cleaned["haystack_sessions"]
            if s["session_id"] == "s_dup"]
    assert kept == [dupe_sessions[0]]


def test_differing_content_duplicate_kept_for_join_guard():
    """#2578 (Task 2, VGATE P2): a repeated session_id with DIFFERENT
    content is NOT the documented artifact — it is KEPT so the #1785
    fail-closed join guard sees the anomaly and vetoes (never silently
    dedup fail-open with information loss)."""
    inst = {"question_id": mt.DUPLICATED_SESSION_QID,
            "haystack_session_ids": ["s_x", "s_x", "s_ok"],
            "haystack_sessions": [
                {"session_id": "s_x", "content": "version a"},
                {"session_id": "s_x", "content": "version b"},
                {"session_id": "s_ok", "content": "fine"}]}
    cleaned = mt.dedup_instance_sessions(inst)
    ids = [s["session_id"] for s in cleaned["haystack_sessions"]]
    assert ids == ["s_x", "s_x", "s_ok"]  # differing repeat untouched


def test_other_instances_pass_through_unchanged():
    inst = {"question_id": "gpt4_fe651585_abs",
            "haystack_session_ids": ["s1"],
            "haystack_sessions": [{"session_id": "s1", "content": "y"}]}
    assert mt.dedup_instance_sessions(inst) is inst or \
        mt.dedup_instance_sessions(inst) == inst


def test_materialize_manifest_count_and_flags():
    census = mt.load_census(CENSUS)
    manifest = mt.materialize_run_data(census["rows"])
    assert manifest["analysis_count"] == 55
    assert len(manifest["rows"]) == 55
    flagged = [r for r in manifest["rows"] if r["session_dedup"]]
    assert [r["qid"] for r in flagged] == [mt.DUPLICATED_SESSION_QID]
    controls = [r for r in manifest["rows"] if r["is_abs_control"]]
    assert {r["qid"] for r in controls} == set(EXPECTED_ABS_CONTROLS)


# ── Pre-registration writer ────────────────────────────────────────────────

def test_preregistration_arm_table_pinned_six_arms(tmp_path):
    """The pinned arm table: 6 arm groups in 8 rows (the tr_top_k family
    expands to 16/20/24) — plan Task 2 numbering (1)-(6)."""
    census = mt.load_census(CENSUS)
    out = tmp_path / "prereg.json"
    rec = mt.write_preregistration(census["rows"], out)
    ids = [a["id"] for a in rec["arms"]]
    assert ids == ["A-default", "tr_top_k16", "tr_top_k20", "tr_top_k24",
                   "c2-on", "applied-rerank", "pool-only-isolation",
                   "cap3-only"]
    for arm in rec["arms"]:
        assert str(arm["reach"]).strip()  # every arm carries a reach
        assert arm["indicators"]  # every arm maps to an issue Indicator
        assert "knobs" in arm
    # the arm table in the JSON file matches the persisted record
    assert json.loads(out.read_text(encoding="utf-8")) == rec


def test_preregistration_indicator_mapping(tmp_path):
    census = mt.load_census(CENSUS)
    rec = mt.write_preregistration(census["rows"], tmp_path / "m.json")
    by_id = {a["id"]: a for a in rec["arms"]}
    assert "1" in by_id["A-default"]["indicators"]
    assert all("2(d)" in by_id[a]["indicators"]
               for a in ("tr_top_k16", "tr_top_k20", "tr_top_k24"))
    assert "2(c)" in by_id["c2-on"]["indicators"]
    assert by_id["applied-rerank"]["indicators"] == ["2(a)", "2(b)"]
    assert by_id["cap3-only"]["indicators"] == ["2(b)"]
    assert by_id["pool-only-isolation"]["indicators"] == ["2(a)"]


def test_preregistration_refuses_arm_without_reach(tmp_path):
    census = mt.load_census(CENSUS)
    bad = ({"id": "reachless", "knobs": [], "reach": "   ",
            "indicators": ["1"]},)
    with pytest.raises(ValueError, match="no reach statement"):
        mt.write_preregistration(census["rows"],
                                 tmp_path / "x.json", arms=bad)


def test_preregistration_carries_null_guard_and_constancy(tmp_path):
    census = mt.load_census(CENSUS)
    rec = mt.write_preregistration(census["rows"], tmp_path / "p.json")
    assert rec.get("conversion_null")
    assert rec["r5_rollback_guard"]["trigger"]
    assert rec["reader_constancy"]["note"]
    # abstention controls + duplicated-session veto recorded
    assert rec["analysis_subset"]["count"] == 55
    assert rec["analysis_subset"]["abs_controls"] == \
        list(EXPECTED_ABS_CONTROLS)
    assert rec["analysis_subset"]["duplicated_session_qid"]["qid"] == \
        mt.DUPLICATED_SESSION_QID


# ── Refusal classifier over the materialized calibration corpus ───────────

def _score(class_) -> float:
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    rows = [r for r in corpus["rows"] if r["class"] == class_]
    agree = sum(1 for r in rows
                if bool(_looks_abstained(r["text"]))
                == (r["gold"] == "abstain"))
    return agree / len(rows)


def test_corpus_materialized_with_stated_balance():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert corpus["n"] == len(corpus["rows"]) == 17
    assert corpus["class_balance"] == {"committed-hedge": 5,
                                       "genuine-abstention": 12}
    for r in corpus["rows"]:
        assert r["gold"] in ("commit", "abstain")
        assert r["provenance"]  # every row is provenance'd


def test_classifier_agreement_bar_committed_hedge():
    # >= 0.95 on the committed-hedge class (#2027: a trailing hedge must
    # NOT label a committed answer abstained)
    assert _score("committed-hedge") >= 0.95


def test_classifier_agreement_bar_genuine_abstention():
    # >= 0.95 on genuine-abstention
    assert _score("genuine-abstention") >= 0.95


def test_classify_refusal_is_the_shared_product_classifier():
    # classify_refusal is the SAME shared read-only classifier the run.py
    # facts gate uses — never a second matcher
    assert mt.classify_refusal is not None
    assert mt.classify_refusal("I don't know.") is True
    assert mt.classify_refusal(
        "The gym schedule is Monday, though I do not know if it changed."
    ) is False
    assert mt.classify_refusal(None) is True  # blank = abstained (product)
    assert mt.classify_refusal("") is True
