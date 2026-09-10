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
    # REAL dataset shape: three PARALLEL arrays annotated by index (the
    # runbook-documented artifact is a content-identical duplicated
    # haystack_session_id, here at indices 1 and 3).
    s_dup = [{"role": "user", "content": "same text"}]
    s_other = [{"role": "assistant", "content": "x"}]
    inst = {
        "question_id": mt.DUPLICATED_SESSION_QID,
        "haystack_session_ids": ["s_dup", "s_dup", "s_other", "s_dup"],
        "haystack_sessions": [s_dup, s_dup, s_other, s_dup],
        "haystack_dates": ["d0", "d1", "d2", "d3"],
    }
    cleaned = mt.dedup_instance_sessions(inst)
    # every duplicate after the first occurrence is dropped, and ALL THREE
    # parallel arrays stay aligned (the date shift this guards against)
    assert cleaned["haystack_session_ids"] == ["s_dup", "s_other"]
    assert cleaned["haystack_sessions"] == [s_dup, s_other]
    assert cleaned["haystack_dates"] == ["d0", "d2"]
    assert (len(cleaned["haystack_session_ids"])
            == len(cleaned["haystack_sessions"])
            == len(cleaned["haystack_dates"]))
    # zero information loss: the kept copy is content-identical
    assert cleaned["haystack_sessions"][0] == s_dup


def test_dedup_refuses_misaligned_parallel_arrays():
    """Regression: deduping ids+sessions while leaving dates untouched
    silently shifts every later session's date (dates are paired by index —
    the crux of temporal reasoning). The helper must REFUSE misaligned
    input rather than emit a corrupted instance."""
    inst = {"question_id": mt.DUPLICATED_SESSION_QID,
            "haystack_session_ids": ["a", "a"],
            "haystack_sessions": [[], []],
            "haystack_dates": ["d0", "d1", "d2"]}  # one date too many
    with pytest.raises(ValueError, match="misaligned"):
        mt.dedup_instance_sessions(inst)


def test_differing_content_duplicate_kept_for_join_guard():
    """#2578 (Task 2, VGATE P2): a repeated session_id with DIFFERENT
    content is NOT the documented artifact — it is KEPT so the #1785
    fail-closed join guard sees the anomaly and vetoes (never silently
    dedup fail-open with information loss)."""
    inst = {"question_id": mt.DUPLICATED_SESSION_QID,
            "haystack_session_ids": ["s_x", "s_x", "s_ok"],
            "haystack_sessions": [
                [{"content": "version a"}],
                [{"content": "version b"}],
                [{"content": "fine"}]],
            "haystack_dates": ["d0", "d1", "d2"]}
    cleaned = mt.dedup_instance_sessions(inst)
    assert cleaned["haystack_session_ids"] == ["s_x", "s_x", "s_ok"]
    assert cleaned["haystack_dates"] == ["d0", "d1", "d2"]


def test_other_instances_pass_through_unchanged():
    inst = {"question_id": "gpt4_fe651585_abs",
            "haystack_session_ids": ["s1"],
            "haystack_sessions": [[{"content": "y"}]],
            "haystack_dates": ["d0"]}
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


# ── Task 3: arm driver ─────────────────────────────────────────────────────

MINI = Path(__file__).resolve().parent.parent / "fixtures" \
    / "longmemeval_mini.json"


def _arm(arm_id: str) -> dict:
    return next(a for a in mt.ARM_TABLE if a["id"] == arm_id)


def test_arm_argv_distinct_workdirs_and_knobs(tmp_path):
    a = _arm("A-default")
    b = _arm("tr_top_k16")
    base = dict(data="d.json", split="s")
    av = mt._arm_argv(a, work_dir=tmp_path / "A-default",
                      checkpoint=tmp_path / "A-default" / "cp.json",
                      output=tmp_path / "out" / "A-default.json", **base)
    bv = mt._arm_argv(b, work_dir=tmp_path / "tr_top_k16",
                      checkpoint=tmp_path / "tr_top_k16" / "cp.json",
                      output=tmp_path / "out" / "tr_top_k16.json", **base)
    assert "--work-dir" in av and "--checkpoint" in av
    assert a["id"] in av[-1]  # per-arm output file
    # knobs appended verbatim from the arm table
    assert b["knobs"] == ["--tr-top-k", "16"]
    assert bv[bv.index("--data") + 1] == "d.json"
    assert "tr_top_k16" in bv[bv.index("--work-dir") + 1]


def test_run_one_arm_precreates_work_dir_and_runs_mock(tmp_path):
    """run_one_arm mkdir -p's the arm dir (runbook 1987: _ensure_work_dir
    has ZERO call sites — a missing dir fails every embedded question) and
    runs through committed run_main in-process."""
    arm_dir = tmp_path / "arms" / "A-default"
    report = mt.run_one_arm(
        _arm("A-default"), data=MINI, arm_dir=arm_dir,
        output=tmp_path / "reports", split="s", limit=2, mock=True)
    assert arm_dir.is_dir()
    assert (arm_dir / "checkpoint.json").is_file()
    assert (tmp_path / "reports" / "A-default.json").is_file()
    # >= 1: the embedded lane can flake a question (observed msr_002
    # ConnectionError) — the driver contract here is dir pre-creation +
    # in-process run_main execution + a persisted report, not 2/2.
    assert report["n_questions"] >= 1


def test_run_one_arm_propagates_stale_checkpoint(tmp_path):
    """The driver NEVER swallows CheckpointStaleError — a fingerprint-
    mismatched resume (Task-1 tr_top_k gate) must fail loudly (a silent
    denominator blend across arms is the exact gap the measurement closes)."""
    from tools.longmem_eval.run import CheckpointStaleError
    arm_dir = tmp_path / "arms"
    mt.run_one_arm(_arm("A-default"), data=MINI, arm_dir=arm_dir,
                   output=tmp_path / "r1", split="s", limit=2, mock=True)
    # resume the SAME checkpoint with tr_top_k 16 -> fingerprint mismatch
    with pytest.raises(CheckpointStaleError, match="tr_top_k"):
        mt.run_one_arm(_arm("tr_top_k16"), data=MINI, arm_dir=arm_dir,
                       output=tmp_path / "r2", split="s", limit=2,
                       mock=True)


# ── Task 3: verdict classification (2×2) ───────────────────────────────────

def _mk_outcome(qid: str, label: bool | None, *,
                admitted: list | None = None,
                bands: dict | None = None,
                hypothesis: str | None = None,
                refusal: bool | None = None,
                pool_size: int = 60) -> dict:
    mf = None
    if label is False:
        mf = {
            "gold_admitted_ids": admitted if admitted is not None else [],
            "pool_depth": {
                "requested": 40, "pool_size": pool_size,
                "marked_points_total": sum((bands or {}).values()),
                "marked_points_in_pool": sum((bands or {}).values()),
                "marked_points_bands": bands or {},
                "marked_chunks_in_pool": 0,
            },
            "reader_refusal": refusal,
        }
    return {"question_id": qid, "label": label, "hypothesis": hypothesis,
            "measure_facts": mf}


def test_classify_correct():
    v = mt.classify_outcome(_mk_outcome("q1", True))
    assert v["correct"] is True and v["attribution"] == "correct"


def test_classify_no_bool_label_unattributed():
    v = mt.classify_outcome(_mk_outcome("q1", None))
    assert v["attribution"] == "unattributed"
    v2 = mt.classify_outcome({"question_id": "q1", "label": "yes"})
    assert v2["attribution"] == "unattributed"  # tampered non-bool label


def test_classify_wrong_no_facts_unattributed():
    v = mt.classify_outcome({"question_id": "q1", "label": False,
                             "hypothesis": "no"})
    assert v["attribution"] == "unattributed"
    assert v["reason"] == "facts-gate-off"


def test_classify_admission_empty_gold():
    v = mt.classify_outcome(_mk_outcome("q1", False, admitted=[]))
    assert v["attribution"] == "admission"


def test_classify_conversion_refusal_from_hypothesis():
    # admitted gold + abstained hypothesis -> CONVERSION with refusal subclass
    v = mt.classify_outcome(_mk_outcome(
        "q1", False, admitted=["p1"], hypothesis="I don't know."))
    assert v["attribution"] == "conversion"
    assert v["subclass"] == "refusal" and v["conversion_refusal"] is True


def test_classify_conversion_refusal_raw_marker_beats_hypothesis():
    # the Task-1 raw marker (None = not measured) is honored when present
    v = mt.classify_outcome(_mk_outcome(
        "q1", False, admitted=["p1"], hypothesis="I don't know.",
        refusal=False))
    assert v["attribution"] == "conversion" and v["subclass"] == "reader-wrong"


def test_classify_conversion_wrong():
    v = mt.classify_outcome(_mk_outcome(
        "q1", False, admitted=["p1"],
        hypothesis="The answer is 10 days.", refusal=False))
    assert v["subclass"] == "reader-wrong"


def test_classify_admission_outside_rerank_depth():
    # marked gold beyond the pool horizon (band 121+ at default pool 40)
    v = mt.classify_outcome(_mk_outcome(
        "q1", False, admitted=[],
        bands={"top-20": 0, "21-40": 0, "41-120": 0, "121+": 2}))
    assert v["attribution"] == "admission"
    assert v["derivable_subclasses"] == ["admission-outside-rerank-depth"]


def test_classify_band_41_120_beyond_default_pool_40():
    """VGATE round: at the default pool 40 the 41-120 band is BEYOND the
    arm's rerank pool limit (the pinned tr_top_k reach statements name the
    rank-48-68 gold band — it sits inside 41-120). It must fire
    admission-outside-rerank-depth, never dropped-by-item-cap (which would
    over-claim the item-cap knob could admit gold widening cannot reach)."""
    v = mt.classify_outcome(_mk_outcome(
        "q1", False, admitted=[],
        bands={"top-20": 0, "21-40": 0, "41-120": 1, "121+": 0}))
    assert v["derivable_subclasses"] == ["admission-outside-rerank-depth"]
    # mixed shallow + deep gold at pool 40: deep gold dominates the claim
    v2 = mt.classify_outcome(_mk_outcome(
        "q1", False, admitted=[],
        bands={"top-20": 2, "21-40": 0, "41-120": 1, "121+": 0}))
    assert v2["derivable_subclasses"] == ["admission-outside-rerank-depth"]
    # applied-rerank pool 120: only 121+ is beyond it
    v3 = mt.classify_outcome(_mk_outcome(
        "q1", False, admitted=[],
        bands={"top-20": 0, "21-40": 0, "41-120": 1, "121+": 0}),
        pool_limit=120)
    assert v3["derivable_subclasses"] == ["dropped-by-item-cap"]


def test_classify_dropped_by_item_cap():
    # marked gold at reader-horizon ranks (shallow bands) yet NOT admitted
    v = mt.classify_outcome(_mk_outcome(
        "q1", False, admitted=[],
        bands={"top-20": 3, "21-40": 0, "41-120": 0, "121+": 0}))
    assert v["derivable_subclasses"] == ["dropped-by-item-cap"]


def test_classify_structural_absence_undated_flag():
    v = mt.classify_outcome(_mk_outcome("q1", False, admitted=[]),
                            gold_undated=True)
    assert "structural-absence-undated-gold" in v["flags"]


def test_classify_never_emits_non_derivable_subclasses():
    """(iii) dropped-by-per-session-cap and (v) ordering-of-admitted-
    evidence are NOT derivable from the committed facts — classify_outcome
    must never emit them from any path (pre-registered boundaries; both are
    follow-up qualitative-pass material, not aggregate claims)."""
    paths = [
        _mk_outcome("q1", False, admitted=[]),                     # admission
        _mk_outcome("q2", False, admitted=["p"], hypothesis="x",
                    refusal=False),  # conversion reader-wrong
        _mk_outcome("q3", False, admitted=["p"],
                    hypothesis="I don't know."),  # conversion refusal
        _mk_outcome("q4", False, admitted=[],
                    bands={"top-20": 1, "21-40": 0, "41-120": 0,
                           "121+": 0}),  # dropped-by-item-cap
    ]
    for o in paths:
        v = mt.classify_outcome(o)
        assert v["subclass"] not in mt.NOT_DERIVABLE_SUBCLASSES
        assert not (set(v.get("derivable_subclasses") or [])
                    & set(mt.NOT_DERIVABLE_SUBCLASSES))


def test_aggregate_taxonomy_per_class_arithmetic():
    qid_to_cls = {"q1": "ordering/compare", "q2": "ordering/compare",
                  "q3": "interval"}
    verdicts = [
        mt.classify_outcome(_mk_outcome("q1", True)),
        mt.classify_outcome(_mk_outcome("q2", False, admitted=[])),
        mt.classify_outcome(_mk_outcome(
            "q3", False, admitted=["p1"], hypothesis="I don't know.")),
    ]
    tables = mt.aggregate_taxonomy(verdicts, qid_to_cls)
    oc = tables["ordering/compare"]
    assert oc["n"] == 2 and oc["correct"] == 1 and oc["admission"] == 1
    assert len(oc["correct_ci"]) == 2  # Wilson CI present
    iv = tables["interval"]
    assert iv["conversion_refusal"] == 1 and iv["conversion_wrong"] == 0


def test_compare_arms_mcnemar_and_discordance():
    baseline = [
        mt.classify_outcome(_mk_outcome("q1", True)),
        mt.classify_outcome(_mk_outcome("q2", False, admitted=[])),
        mt.classify_outcome(_mk_outcome("q3", False, admitted=["p"],
                                        hypothesis="wrong", refusal=False)),
    ]
    arm = [
        mt.classify_outcome(_mk_outcome("q1", True)),
        mt.classify_outcome(_mk_outcome("q2", True)),  # arm wins q2
        mt.classify_outcome(_mk_outcome("q3", False, admitted=["p"],
                                        hypothesis="wrong", refusal=False)),
    ]
    c = mt.compare_arms_to_baseline(baseline, arm)
    assert c["common_n"] == 3
    assert c["arm_wins"] == 1 and c["baseline_wins"] == 0
    assert c["discordant_pairs"] == 1
    assert c["baseline_conversion_wrong"] == 1
    assert c["arm_conversion_wrong"] == 1


def test_rollback_guard_readout_flags_breach():
    stats = {
        "A-default": {"refusal_rate": 0.10, "mean_context_tokens": 2000},
        "tr_top_k24": {"refusal_rate": 0.45, "mean_context_tokens": 8000},
    }
    g = mt.rollback_guard_readout(stats)
    assert g["bound"] == round(0.10 + mt.ROLLBACK_MARGIN, 4)
    by_id = {r["arm"]: r for r in g["arms"]}
    assert by_id["A-default"]["flagged_rollback_candidate"] is False
    assert by_id["tr_top_k24"]["flagged_rollback_candidate"] is True


def test_assert_reader_constancy_aborts_on_mismatch():
    mt.assert_reader_constancy({
        "A-default": {"reader_model_spec": "deepseek:deepseek-v4-flash",
                      "reader_prompt_hash": "abc",
                      "judge_model": "openai/gpt-4o-2024-08-06"},
        "tr_top_k16": {"reader_model_spec": "deepseek:deepseek-v4-flash",
                       "reader_prompt_hash": "abc",
                       "judge_model": "openai/gpt-4o-2024-08-06"},
    })  # ok
    with pytest.raises(ValueError, match="reader_model_spec"):
        mt.assert_reader_constancy({
            "A-default": {"reader_model_spec": "deepseek:deepseek-v4-flash",
                          "reader_prompt_hash": "abc",
                          "judge_model": "openai/gpt-4o-2024-08-06"},
            "tr_top_k16": {"reader_model_spec": "other:model",
                           "reader_prompt_hash": "abc",
                           "judge_model": "openai/gpt-4o-2024-08-06"},
        })


def test_assert_reader_constancy_rejects_absent_metadata():
    """Constancy cannot be asserted from missing/blank methodology — the
    vacuous pass that would let a mixed or unrecorded reader ship as
    evidence (regression guard: an all-empty meta dict must RAISE)."""
    with pytest.raises(ValueError, match="under-specified"):
        mt.assert_reader_constancy({"A-default": {}, "c2-on": {}})
    with pytest.raises(ValueError, match="under-specified"):
        mt.assert_reader_constancy({
            "A-default": {"reader_model_spec": "",
                          "reader_prompt_hash": "   ",
                          "judge_model": "openai/gpt-4o-2024-08-06"},
            "c2-on": {"reader_model_spec": "deepseek:deepseek-v4-flash",
                      "reader_prompt_hash": "abc",
                      "judge_model": "openai/gpt-4o-2024-08-06"},
        })


def test_assert_reader_constancy_rejects_stub_reader():
    with pytest.raises(ValueError, match="stub"):
        mt.assert_reader_constancy({
            "A-default": {"reader_model": "stub-reader",
                          "reader_model_spec": "stub:reader",
                          "reader_prompt_hash": "abc",
                          "judge_model": "openai/gpt-4o-2024-08-06"},
        })


# ── Task 3: branch decision + gate output ─────────────────────────────────

def _tot(qid, correct, admission=0, refusal=0, wrong=0):
    """Build an outcome whose classify totals match the given counts."""
    if correct:
        return mt.classify_outcome(_mk_outcome(qid, True))
    if admission:
        return mt.classify_outcome(_mk_outcome(qid, False, admitted=[]))
    if refusal:
        return mt.classify_outcome(_mk_outcome(
            qid, False, admitted=["p"], hypothesis="I don't know."))
    return mt.classify_outcome(_mk_outcome(
        qid, False, admitted=["p"], hypothesis="wrong", refusal=False))


def test_branch_admission_attributed():
    base = [_tot("q1", True), _tot("q2", True), _tot("q3", False,
                                                     admission=1)]
    arm = [_tot("q1", True), _tot("q2", True), _tot("q3", True)]
    totals = {"baseline": mt._totals(base),
              "arms": {"tr_top_k24": mt._totals(arm)}}
    assert mt.branch_decision(totals) == "admission-attributed"


def test_branch_conversion_bound_on_admitted_residual():
    base = [_tot("q1", True), _tot("q2", False, admission=1)]
    arm = [_tot("q1", True),
           _tot("q2", False, refusal=1)]  # gold now admitted, reader refuses
    totals = {"baseline": mt._totals(base),
              "arms": {"c2-on": mt._totals(arm)}}
    assert mt.branch_decision(totals) == "conversion-bound"
    # a correct lift with NO admission reduction is reader-side
    # (conversion-bound), never admission-attributed
    base2 = [_tot("q1", False, wrong=1)]  # admitted gold, reader wrong
    arm2 = [_tot("q1", True)]
    totals2 = {"baseline": mt._totals(base2),
               "arms": {"c2-on": mt._totals(arm2)}}
    assert mt.branch_decision(totals2) == "conversion-bound"


def test_branch_structural_path_when_nothing_moves():
    base = [_tot("q1", True), _tot("q2", False, admission=1)]
    arm = [_tot("q1", True), _tot("q2", False, admission=1)]
    totals = {"baseline": mt._totals(base),
              "arms": {"tr_top_k20": mt._totals(arm)}}
    assert mt.branch_decision(totals) == "structural-path-evidence"


def test_branch_conversion_indeterminate_when_undiscriminable():
    # correct flat, admission flat, but the residual SPLIT moved on q2
    # (refusal <-> wrong swap): conversion-wrong ~ baseline across the arm
    # — the shipped reader cannot be discriminated; routed to #2013, never
    # claimed as decided.
    base = [_tot("q1", True), _tot("q2", False, refusal=1)]
    arm = [_tot("q1", True), _tot("q2", False, wrong=1)]
    totals = {"baseline": mt._totals(base),
              "arms": {"applied-rerank": mt._totals(arm)}}
    assert mt.branch_decision(totals) == "conversion-indeterminate"


def test_gate_output_renders_decision_tables_and_reach(tmp_path):
    prereg = mt.write_preregistration(
        mt.load_census(CENSUS)["rows"], tmp_path / "prereg.json")
    qid_to_cls = {"q1": "ordering/compare", "q2": "interval",
                  "q3": "ordering/compare"}
    baseline = [_tot("q1", True), _tot("q2", False, admission=1),
                _tot("q3", False, wrong=1)]
    arms = {"tr_top_k24": [_tot("q1", True), _tot("q2", True),
                           _tot("q3", False, wrong=1)]}
    stats = {"A-default": {"refusal_rate": 0.1,
                           "mean_context_tokens": 2000},
             "tr_top_k24": {"refusal_rate": 0.12,
                            "mean_context_tokens": 5000}}
    text = mt.gate_output(issue="2578", prereg=prereg,
                          qid_to_cls=qid_to_cls,
                          baseline_verdicts=baseline,
                          arm_verdicts=arms, arm_stats=stats)
    assert text.startswith("---")
    assert 'title: "2578 Temporal Measurement — Gate Output"' in text
    assert "ownedBy: epistemic-team" in text
    assert "| ordering/compare |" in text and "| interval |" in text
    assert "| tr_top_k24 |" in text
    # per-arm reach from the pre-registration rides the output
    assert "admission-attributed" in text
    assert "pre-registered reach" in text


def test_gate_output_enforces_reader_constancy_when_meta_supplied(
        tmp_path):
    """The gate output is the comparison record — a mixed-reader arm set
    must never reach it. Passing arms_meta runs the constancy assertion
    inside gate_output (regression: it previously claimed the assertion in
    its docstring while never calling it)."""
    prereg = mt.write_preregistration(
        mt.load_census(CENSUS)["rows"], tmp_path / "prereg.json")
    qid_to_cls = {"q1": "interval"}
    baseline = [_tot("q1", True)]
    arms = {"tr_top_k16": [_tot("q1", True)]}
    stats = {"A-default": {"refusal_rate": 0.1,
                           "mean_context_tokens": 10},
             "tr_top_k16": {"refusal_rate": 0.1,
                            "mean_context_tokens": 10}}
    good = {
        "A-default": {"reader_model_spec": "deepseek:deepseek-v4-flash",
                      "reader_prompt_hash": "abc",
                      "judge_model": "openai/gpt-4o-2024-08-06"},
        "tr_top_k16": {"reader_model_spec": "deepseek:deepseek-v4-flash",
                       "reader_prompt_hash": "abc",
                       "judge_model": "openai/gpt-4o-2024-08-06"}}
    mt.gate_output(issue="2578", prereg=prereg, qid_to_cls=qid_to_cls,
                   baseline_verdicts=baseline, arm_verdicts=arms,
                   arm_stats=stats, arms_meta=good)  # ok
    mixed = {**good, "tr_top_k16": {**good["tr_top_k16"],
                                    "reader_model_spec": "other:model"}}
    with pytest.raises(ValueError, match="reader_model_spec"):
        mt.gate_output(issue="2578", prereg=prereg, qid_to_cls=qid_to_cls,
                       baseline_verdicts=baseline, arm_verdicts=arms,
                       arm_stats=stats, arms_meta=mixed)


def test_assert_reader_constancy_rejects_stub_via_spec_only():
    """Stub detection must not depend on the non-required `reader_model`
    key — a stub arm recording only reader_model_spec would otherwise
    evade the pre-registered ABSTAIN gate."""
    with pytest.raises(ValueError, match="stub"):
        mt.assert_reader_constancy({
            "A-default": {"reader_model_spec": "stub:reader",
                          "reader_prompt_hash": "abc",
                          "judge_model": "openai/gpt-4o-2024-08-06"},
        })
