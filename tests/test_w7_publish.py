"""W7 sealed-run publication layer tests (issue #2105, epic #2080 E2E-8).

Pins the OFFICIAL LongMemEval recall_all@5 semantics against the official
evaluator definition — never any-hit (R12), never the per-question
fraction — plus the labeled-variant separation, the sealed-key manifest
(gold-only edit changes the digest), and the receipt validator (S13:
run_status / verdict / failure_origin / commit / corpus hash / judge pin).
"""
from __future__ import annotations

import json

from tools.longmem_eval.w7_publish import (
    JUDGE_PIN,
    _fraction_at_k,
    binary_projections,
    build_receipt,
    seal_answer_keys,
    validate_receipt,
    variant_rows,
)


def _outcome(qid: str, session5: float | None) -> dict:
    out: dict = {"question_id": qid, "session_recall@k": {}}
    if session5 is not None:
        out["session_recall@k"]["5"] = session5
    return out


def _abs_outcome() -> dict:
    return _outcome("abc_abs", 0.5)


def test_official_recall_all_is_all_in_topk_not_any_hit():
    # 3 non-abs questions: q1 all answer sessions in top-5 (fraction 1.0),
    # q2 only one of two (0.5), q3 none (0.0).
    outcomes = [_outcome("q1", 1.0), _outcome("q2", 0.5), _outcome("q3", 0.0)]
    rows = variant_rows(outcomes, k=5)
    # official = mean of the binary all(doc in recalled_docs ...) — only q1
    assert rows["official_recall_all@5"]["value"] == 1 / 3
    # any-hit (labeled variant) = q1 + q2
    assert rows["any_hit@5"]["value"] == 2 / 3
    # the two are DIFFERENT numbers (the R12 trap: never conflate)
    assert rows["official_recall_all@5"]["value"] != \
        rows["any_hit@5"]["value"]
    # per-question-fraction paper variant (non-_abs) = (1.0 + 0.5 + 0.0)/3
    assert rows["fraction_paper@5"]["value"] == 0.5
    # the official number is NOT the fraction either
    assert rows["official_recall_all@5"]["value"] != 0.5


def test_binary_projections_match_official_evaluator_formula():
    # The official eval_utils.evaluate_retrieval:
    #   recall_all = all(doc in recalled_docs for doc in correct_docs)
    # With |correct ∩ top-k| / |correct| known exactly, recall_all ==
    # (fraction == 1.0) and recall_any == (fraction > 0).
    assert binary_projections(1.0) == (1.0, 1.0)
    assert binary_projections(0.5) == (0.0, 1.0)
    assert binary_projections(0.0) == (0.0, 0.0)
    assert binary_projections(2 / 3) == (0.0, 1.0)
    # |correct| = 3 with all three retrieved → fraction 1.0 → recall_all 1.0
    assert binary_projections(3 / 3) == (1.0, 1.0)


def test_abs_questions_excluded_from_official_aggregate():
    # The official print_retrieval_metrics.py filters '_abs' BEFORE the
    # mean. An _abs question with a partial fraction must not drag the
    # official or any-hit/fraction-paper rows (only the legacy row).
    outcomes = [_outcome("q1", 1.0), _outcome("q2", 0.0),
                _abs_outcome()]  # _abs partial (0.5)
    rows = variant_rows(outcomes, k=5)
    assert rows["official_recall_all@5"]["n"] == 2
    assert rows["official_recall_all@5"]["value"] == 0.5   # q1 only
    assert rows["any_hit@5"]["value"] == 0.5
    assert rows["fraction_paper@5"]["value"] == 0.5        # (1.0+0.0)/2
    # legacy _abs-inclusive fraction = (1.0 + 0.0 + 0.5)/3
    assert rows["fraction_legacy@5"]["value"] == 0.5
    assert rows["fraction_legacy@5"]["n_abs"] == 1


def test_missing_outcome_fraction_is_excluded_not_zero():
    # a failure outcome without a session_recall@k record must never be
    # coerced into the official mean as a 0.0 miss
    outcomes = [_outcome("q1", 1.0), _outcome("q2", 1.0),
                _outcome("q3", None)]
    rows = variant_rows(outcomes, k=5)
    assert rows["official_recall_all@5"]["value"] == 1.0
    assert rows["official_recall_all@5"]["n"] == 2
    assert rows["official_recall_all@5"]["n_excluded"] == 1


def test_fraction_at_k_rejects_malformed_shapes():
    assert _fraction_at_k({"session_recall@k": {"5": True}}, 5) is None
    assert _fraction_at_k({"session_recall@k": {"5": "1.0"}}, 5) is None
    assert _fraction_at_k({"session_recall@k": {}}, 5) is None
    assert _fraction_at_k({}, 5) is None
    assert _fraction_at_k({"session_recall@k": {"5": 1.0}}, 5) == 1.0


def test_seal_changes_on_answer_key_edit():
    instances = [
        {"question_id": "q1", "answer": "42", "answer_session_ids": ["a1"]},
        {"question_id": "q2", "answer": "springfield",
         "answer_session_ids": ["a2"]},
    ]
    s1 = seal_answer_keys(instances)
    assert s1["n_questions"] == 2
    assert s1["digest"].startswith("sha256:")
    # a gold-only edit (answer changed) alters the aggregate digest
    edited = [dict(instances[0], answer="43"), instances[1]]
    s2 = seal_answer_keys(edited)
    assert s2["digest"] != s1["digest"]
    # identical input is deterministic
    assert seal_answer_keys(instances)["digest"] == s1["digest"]
    # order-independence: question order does not change the digest
    assert seal_answer_keys(list(reversed(instances)))["digest"] == \
        s1["digest"]


def test_receipt_valid_when_completed_with_all_pins():
    receipt = build_receipt(
        run_id="w7a-500q-abc123", date="2026-09-04T12:00:00Z",
        commit="abc123", corpus_hash="sha256:deadbeef",
        judge_pin=JUDGE_PIN, judge_model="openai/gpt-4o-2024-08-06",
        judge_rubric_id_hash="82d07b0de05daa48",
        reader_model_spec="openrouter:deepseek/deepseek-v4-flash",
        reader_pinned=True, ingest_mode="deterministic",
        run_status="completed", verdict="pass", failure_origin=None,
        cost_usd=2.0, metrics={"official_recall_all@5": 0.9},
        notes=["n"])
    assert validate_receipt(receipt) == []


def test_receipt_completed_requires_pins():
    base = dict(
        run_id="r", date="d", commit="c", corpus_hash="sha256:x",
        judge_pin="pin", judge_model="m", judge_rubric_id_hash="h",
        reader_model_spec="s", reader_pinned=True, ingest_mode="det",
        run_status="completed", verdict="pass", failure_origin=None,
        cost_usd=1.0, metrics={"official_recall_all@5": 0.5},
        resolved_config={}, notes=[])
    issues = validate_receipt(dict(base, judge_pin=""))
    assert any("judge_pin" in i for i in issues)
    issues = validate_receipt(dict(base, corpus_hash="nope"))
    assert any("corpus_hash" in i for i in issues)
    issues = validate_receipt(dict(base, judge_rubric_id_hash=""))
    assert any("judge_rubric_id_hash" in i for i in issues)
    issues = validate_receipt(dict(base, metrics={"other": 1}))
    assert any("official_recall_all" in i for i in issues)
    issues = validate_receipt(dict(base, run_status="bogus"))
    assert any("run_status" in i for i in issues)
    # a failed run does not demand the completed-run pins
    failed = dict(base, run_status="failed", metrics={"x": 1})
    assert validate_receipt(failed) == []


def test_cli_emits_valid_artifact(tmp_path):
    """End-to-end over the committed mini fixture shape (offline)."""
    from tools.longmem_eval.w7_publish import _main
    report = {
        "outcomes": [_outcome("q1", 1.0), _outcome("q2", 0.0)],
        "retrieval": {"session_recall_paper@k": {"5": 0.5},
                      "session_recall@k": {"5": 0.5}},
        "methodology": {"judge_model": "openai/gpt-4o-2024-08-06",
                        "judge_rubric_id_hash": "hash",
                        "reader_model_spec": "openrouter:deepseek/..."
                                             "deepseek-v4-flash",
                        "reader_pinned": True, "ingest_mode": "deterministic",
                        "run_at_utc": "2026-09-04T12:00:00Z",
                        "dataset_semantics_audit": {"verdict": "trusted"}},
        "integrity": {"valid": True}, "accuracy": {"overall": 1.0},
        "split": "s", "updated_at_utc": "2026-09-04T12:00:00Z",
    }
    rp = tmp_path / "report.json"
    rp.write_text(json.dumps(report), encoding="utf-8")
    ds = tmp_path / "data.json"
    ds.write_text(json.dumps(
        [{"question_id": "q1", "answer": "a",
          "answer_session_ids": ["s1"]}]), encoding="utf-8")
    out = tmp_path / "artifact.json"
    _main(["--report", str(rp), "--dataset", str(ds), "--commit", "abc123",
           "--corpus-hash", "sha256:" + "0" * 64, "--cost-usd", "1.5",
           "--out", str(out)])
    artifact = json.loads(out.read_text(encoding="utf-8"))
    assert artifact["receipt_valid"] is True
    assert artifact["receipt"]["corpus_hash"] == "sha256:" + "0" * 64
    assert artifact["receipt"]["metrics"]["official_recall_all@5"] == 0.5
    assert artifact["seal"]["n_questions"] == 1
    assert artifact["variants"]["official_recall_all@5"]["n"] == 2
