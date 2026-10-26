"""W7 sealed-run publication layer tests (issue #2105, epic #2080 E2E-8).

Pins the OFFICIAL LongMemEval recall_all@5 semantics against the official
evaluator definition — never any-hit (R12), never the per-question
fraction — plus the labeled-variant separation, the sealed-key manifest
(gold-only edit changes the digest), and the receipt validator (S13:
run_status / verdict / failure_origin / commit / corpus hash / judge pin).
"""
from __future__ import annotations

import json

import pytest

from tools.longmem_eval import measurement_posture
from tools.longmem_eval.w7_publish import (
    JUDGE_PIN,
    MOCK_JUDGE_PIN,
    OFFICIAL_JUDGE_MODEL_ID,
    _fraction_at_k,
    binary_projections,
    build_receipt,
    category_variant_rows,
    judge_pin_for,
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


def _posture(ingest_mode: str = "v2", *, activated: bool = False) -> dict:
    """#8486: the posture block a completed receipt now requires."""
    return measurement_posture.build(ingest_mode=ingest_mode, activation={
        "requested": activated,
        "questions": 1,
        "ep_ran": 1 if activated else 0,
        "failed": 0,
    })


def test_receipt_valid_when_completed_with_all_pins():
    receipt = build_receipt(
        run_id="w7a-500q-abc123", date="2026-09-04T12:00:00Z",
        commit="abc123", corpus_hash="sha256:deadbeef",
        judge_pin=JUDGE_PIN, judge_model="openai/gpt-4o-2024-08-06",
        judge_rubric_id_hash="82d07b0de05daa48",
        reader_model_spec="openrouter:deepseek/deepseek-v4-flash",
        reader_pinned=True, ingest_mode="deterministic",
        run_status="completed", verdict="pass", failure_origin=None,
        cost_usd=2.0, metrics={"official_recall_all@5": 0.9,
                               "official_recall_all@5_by_category":
                                   _by_category({"multi-session": [1.0]})},
        measurement_posture=_posture("deterministic"), notes=["n"])
    assert validate_receipt(receipt) == []


def _by_category(per_type: dict[str, list[float]]) -> dict:
    """Build the #2516 per-category block shape from outcomes."""
    outcomes = []
    for qtype, fracs in per_type.items():
        for i, f in enumerate(fracs):
            outcomes.append(_outcome(f"{qtype}-{i}", f))
            outcomes[-1]["question_type"] = qtype
    return category_variant_rows(outcomes, 5)


def test_receipt_completed_requires_the_per_category_rows():
    """#2516 (C1): a completed receipt must carry the type-tail gate surface.

    The measured loss is CATEGORY-specific and the all-or-nothing official
    binary hides it in the full-mix mean, so a receipt that cannot report the
    category is not a gate for the #2513 levers. RED mutation: drop the
    per-category block from the metrics → this fails.
    """
    base = dict(
        run_id="r", date="d", commit="c", corpus_hash="sha256:x",
        # #8486: the pin must belong to the recorded judge — the fixture uses
        # the real pair rather than an arbitrary pin, which is exactly the
        # pairing the validator now refuses (see the judge-identity tests).
        judge_pin=JUDGE_PIN, judge_model="gpt-4o-2024-08-06",
        judge_rubric_id_hash="h",
        reader_model_spec="s", reader_pinned=True, ingest_mode="det",
        run_status="completed", verdict="pass", failure_origin=None,
        cost_usd=1.0, metrics={"official_recall_all@5": 0.5},
        measurement_posture=_posture("det"),
        resolved_config={}, notes=[])
    issues = validate_receipt(base)
    assert any("per-category" in i for i in issues)
    # all three gate categories must be PRESENT (n == 0 when the run carried
    # none — coverage has to be readable, and an absent row cannot be)
    partial = _by_category({"multi-session": [1.0]})
    del partial["rows"]["knowledge-update"]
    issues = validate_receipt(dict(
        base, metrics={"official_recall_all@5": 0.5,
                       "official_recall_all@5_by_category": partial}))
    assert any("incomplete" in i for i in issues)
    # full block → clean; a category with zero questions is legitimate
    ok = _by_category({"multi-session": [1.0]})
    assert validate_receipt(dict(
        base, metrics={"official_recall_all@5": 0.5,
                       "official_recall_all@5_by_category": ok})) == []
    assert ok["rows"]["knowledge-update"]["n"] == 0
    # a failed run is not asked for the gate surface (and a failed run's
    # verdict is 'inconclusive' — 'pass' would be refused by the #8486
    # status/verdict clause, tested separately)
    assert validate_receipt(dict(base, run_status="failed",
                                 verdict="inconclusive",
                                 metrics={"x": 1})) == []


def _base_receipt_metrics(**over) -> dict:
    m = {"official_recall_all@5": 0.5,
         "official_recall_all@5_by_category": _by_category(
             {"multi-session": [1.0]})}
    m.update(over)
    return m


def test_receipt_completed_requires_pins():
    base = dict(
        run_id="r", date="d", commit="c", corpus_hash="sha256:x",
        # #8486: real pin/judge pair — an arbitrary pin with an arbitrary judge
        # is the pairing the validator now refuses.
        judge_pin=JUDGE_PIN, judge_model="gpt-4o-2024-08-06",
        judge_rubric_id_hash="h",
        reader_model_spec="s", reader_pinned=True, ingest_mode="det",
        run_status="completed", verdict="pass", failure_origin=None,
        cost_usd=1.0, metrics=_base_receipt_metrics(),
        measurement_posture=_posture("det"),
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
    # a failed run does not demand the completed-run pins: it records its own
    # verdict ('inconclusive' — 'pass' is refused for any non-completed run)
    failed = dict(base, run_status="failed", verdict="inconclusive",
                  metrics={"x": 1})
    assert validate_receipt(failed) == []


def test_judge_pin_is_derived_from_the_recorded_judge():
    """#8486 (R12): the pin follows the judge that graded — allow-list, never a
    default. ``JUDGE_PIN`` was minted for every run, so a stub-graded or
    substituted-judge report published under the OFFICIAL pin."""
    # the official MODEL keeps the official pin, over either transport
    assert judge_pin_for("gpt-4o-2024-08-06") == JUDGE_PIN
    assert judge_pin_for("openrouter:openai/gpt-4o-2024-08-06") == JUDGE_PIN
    assert judge_pin_for("openai:gpt-4o-2024-08-06") == JUDGE_PIN
    # a stub is never the official judge
    assert judge_pin_for("mock-judge") == MOCK_JUDGE_PIN
    assert judge_pin_for("mock") == MOCK_JUDGE_PIN
    assert judge_pin_for("mock-judge") != JUDGE_PIN
    # any substituted model travels as an UNPINNED record, never the official
    assert judge_pin_for("deepseek-v4-flash") == "unpinned:deepseek-v4-flash"
    assert judge_pin_for("variant-containment") == \
        "unpinned:variant-containment"
    assert judge_pin_for("gpt-4o") == "unpinned:gpt-4o"
    assert judge_pin_for("") == "unpinned:<unrecorded>"
    assert judge_pin_for(None) == "unpinned:<unrecorded>"


def test_receipt_pin_must_belong_to_the_recorded_judge():
    """A pin/model pair that cannot both be true is refused — that pairing is
    how a stub-graded run is published under the official pin."""
    base = dict(
        run_id="r", date="d", commit="c", corpus_hash="sha256:x",
        judge_pin=JUDGE_PIN, judge_model="mock-judge",
        judge_rubric_id_hash="h",
        reader_model_spec="s", reader_pinned=True, ingest_mode="det",
        run_status="completed", verdict="inconclusive", failure_origin=None,
        cost_usd=1.0, metrics=_base_receipt_metrics(),
        measurement_posture=_posture("det"),
        resolved_config={}, notes=[])
    issues = validate_receipt(base)
    assert any("judge_pin" in i and "does not belong" in i for i in issues)
    # the honest pair for the same run validates clean
    assert validate_receipt(dict(base, judge_pin=MOCK_JUDGE_PIN)) == []


def _cli_artifact(tmp_path, judge_model: str) -> dict:
    """Run the publication CLI over a minimal completed report whose recorded
    judge is ``judge_model`` (offline, no keys)."""
    from tools.longmem_eval.w7_publish import _main
    report = {
        "outcomes": [_outcome("q1", 1.0), _outcome("q2", 0.0)],
        "retrieval": {"session_recall@k": {"5": 0.5}},
        "methodology": {"judge_model": judge_model,
                        "judge_rubric_id_hash": "hash",
                        "reader_model_spec": "mock-reader",
                        "reader_pinned": True, "ingest_mode": "v2",
                        "run_at_utc": "2026-10-01T00:00:00Z",
                        "measurement_posture": _posture("v2"),
                        "dataset_semantics_audit": {"verdict": "trusted"}},
        "integrity": {"valid": True}, "accuracy": {"overall": 1.0},
        "split": "s", "updated_at_utc": "2026-10-01T00:00:00Z",
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
    return json.loads(out.read_text(encoding="utf-8"))


def test_mock_graded_run_cannot_publish_the_official_pin(tmp_path):
    """#8486 (R12) — the defect this pins: a ``--mock`` report (zero network,
    zero cost) used to mint a receipt carrying the OFFICIAL judge pin with
    ``receipt_valid: True`` and ``verdict: pass``. Measured before the fix:
    judge_pin ``longmemeval-official-anscheck-gpt4o-2024-08-06-v1``,
    receipt_valid True, [] issues.

    The verdict is asserted HERE, not only in the validator: an earlier
    revision made the receipt *invalid* without making it *honest* (the
    receipt still read ``verdict: pass``), so a consumer reading that field
    alone — the field R12 is about — could still read the claim off the run.
    """
    artifact = _cli_artifact(tmp_path, "mock-judge")
    receipt = artifact["receipt"]
    assert receipt["judge_pin"] == MOCK_JUDGE_PIN
    assert receipt["judge_pin"] != JUDGE_PIN
    # the receipt is self-consistent: nothing in it claims the official run
    assert receipt["verdict"] == "inconclusive"
    assert receipt["run_status"] == "completed"
    assert artifact["receipt_valid"] is True


def test_substituted_judge_run_is_unpinned_and_cannot_pass(tmp_path):
    """A cheaper model grading the graded run (the substitution this issue
    forbids) records ``unpinned:<model>`` and never a 'pass'."""
    artifact = _cli_artifact(tmp_path, "deepseek-v4-flash")
    receipt = artifact["receipt"]
    assert receipt["judge_pin"] == "unpinned:deepseek-v4-flash"
    assert receipt["verdict"] == "inconclusive"


def test_validator_refuses_pass_for_a_non_official_judge():
    """Defence in depth: a receipt HAND-BUILT (or written by an older
    publisher) whose pin honestly belongs to a stub, yet claims 'pass', is
    refused — the CLI derives an honest verdict, so this clause only fires
    outside it."""
    base = dict(
        run_id="r", date="d", commit="c", corpus_hash="sha256:x",
        judge_pin=MOCK_JUDGE_PIN, judge_model="mock-judge",
        judge_rubric_id_hash="h",
        reader_model_spec="s", reader_pinned=True, ingest_mode="det",
        run_status="completed", verdict="pass", failure_origin=None,
        cost_usd=1.0, metrics=_base_receipt_metrics(),
        measurement_posture=_posture("det"),
        resolved_config={}, notes=[])
    issues = validate_receipt(base)
    assert any("cannot publish 'pass'" in i for i in issues)
    # the same receipt with the honest verdict validates clean
    assert validate_receipt(dict(base, verdict="inconclusive")) == []


@pytest.mark.parametrize("run_status", ["failed", "skipped"])
def test_hand_built_non_completed_receipt_cannot_claim_the_official_pin(
        run_status):
    """#8486 (R12): the judge-identity clauses gate EVERY receipt J7 can read,
    not only a completed one.

    Measured at 885e6f372 (the parent head): both clauses sat inside
    ``if run_status == "completed":``, so this exact receipt — the OFFICIAL pin
    with a stub grader, ``verdict: pass``, one neighbouring field flipped to
    ``failed``/``skipped`` — returned ``[]`` and read ``receipt_valid: True``.
    ``failed`` and ``skipped`` are legal statuses, so the scope made the gate
    step-around-able by whichever forger wrote the rest of the receipt.

    RED mutation: move either clause back inside the completed block → this
    fails on the pin assertion (both statuses), i.e. the hole is back.
    """
    base = dict(
        run_id="r", date="d", commit="c", corpus_hash="sha256:x",
        judge_pin=JUDGE_PIN, judge_model="mock-judge",
        judge_rubric_id_hash="h",
        reader_model_spec="s", reader_pinned=True, ingest_mode="det",
        run_status=run_status, verdict="pass", failure_origin=None,
        cost_usd=1.0, metrics={"x": 1}, resolved_config={}, notes=[])
    issues = validate_receipt(base)
    # the pin claim is refused on its own
    assert any("does not belong" in i for i in issues)
    # and the verdict claim is refused on its own (non-official judge)
    assert any("cannot publish 'pass'" in i and "graded by" in i
               for i in issues)
    # the honest pin/verdict pair for the SAME non-completed run is clean —
    # the clauses refuse the claim, not the status
    assert validate_receipt(dict(base, judge_pin=MOCK_JUDGE_PIN,
                                 verdict="inconclusive")) == []


def test_pass_requires_a_completed_run():
    """The documented claim has TWO predicates — *only an official-pinned
    **completed** run records `verdict: pass`* — and the validator enforced
    only the pin half, so a receipt graded by the official judge could still
    publish `pass` while declaring itself `failed` (measured at 885e6f372:
    ``validate_receipt`` returned ``[]``).

    RED mutation: delete the status clause → this fails.
    """
    base = dict(
        run_id="r", date="d", commit="c", corpus_hash="sha256:x",
        judge_pin=JUDGE_PIN, judge_model="gpt-4o-2024-08-06",
        judge_rubric_id_hash="h",
        reader_model_spec="s", reader_pinned=True, ingest_mode="det",
        run_status="failed", verdict="pass", failure_origin=None,
        cost_usd=1.0, metrics={"x": 1}, resolved_config={}, notes=[])
    issues = validate_receipt(base)
    assert any("only a completed run carries a verdict" in i for i in issues)
    # no pin complaint: this judge legitimately owns the official pin
    assert not any("judge_pin" in i for i in issues)
    assert validate_receipt(dict(base, verdict="inconclusive")) == []


@pytest.mark.parametrize("judge_pin", [JUDGE_PIN, MOCK_JUDGE_PIN])
def test_partial_receipt_naming_no_judge_cannot_carry_a_pin(judge_pin):
    """A PARTIAL receipt — a pin with no ``judge_model`` at all — is refused
    too, in every status: a pin means "the judge that graded", so a receipt
    that names no judge cannot back any pin it carries. Before the fix this
    also returned ``[]`` for a non-completed run (the clauses never ran)."""
    base = dict(
        run_id="r", date="d", commit="c", corpus_hash="sha256:x",
        judge_pin=judge_pin, judge_model=None,
        judge_rubric_id_hash="h",
        reader_model_spec="s", reader_pinned=True, ingest_mode="det",
        run_status="failed", verdict="inconclusive", failure_origin=None,
        cost_usd=1.0, metrics={"x": 1}, resolved_config={}, notes=[])
    issues = validate_receipt(base)
    assert any("does not belong" in i and "unpinned:<unrecorded>" in i
               for i in issues)
    # a receipt that carries NO pin makes no claim, and stays valid
    assert validate_receipt(dict(base, judge_pin="")) == []


def test_official_judge_model_id_matches_the_judge_module():
    """``OFFICIAL_JUDGE_MODEL_ID`` is a second copy of the identity
    ``judge.DEFAULT_JUDGE_MODEL`` owns (that module is not imported here: it
    pulls ``tortoise.ingest``/``reader``, ~2.5 s, into a publication tool).
    This test is what stops the copy drifting: a judge-model bump applied to
    one file only would otherwise refuse the NEW official judge while still
    minting ``JUDGE_PIN`` for the OLD one."""
    from tools.longmem_eval.judge import DEFAULT_JUDGE_MODEL

    assert OFFICIAL_JUDGE_MODEL_ID in DEFAULT_JUDGE_MODEL
    assert judge_pin_for(DEFAULT_JUDGE_MODEL) == JUDGE_PIN


def test_cli_keeps_the_official_pin_for_the_official_judge(tmp_path):
    """No false block: the official judge (routed through another provider)
    still publishes a valid, passing receipt under the official pin."""
    artifact = _cli_artifact(tmp_path, "openrouter:openai/gpt-4o-2024-08-06")
    assert artifact["receipt"]["judge_pin"] == JUDGE_PIN
    assert artifact["receipt"]["verdict"] == "pass"
    assert artifact["receipt_valid"] is True
    assert artifact["receipt_issues"] == []


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
                        # #8486: the posture the runner records — a completed
                        # receipt cannot publish without it.
                        "measurement_posture": _posture("deterministic"),
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
    # #2516 (C1): the artifact + receipt carry the per-category gate surface
    by_cat = artifact["by_category"]
    assert by_cat["rows"]["full_mix"]["official_recall_all@5"] == 0.5
    assert artifact["receipt"]["metrics"][
        "official_recall_all@5_by_category"]["rows"]["full_mix"][
        "official_recall_all@5"] == 0.5
    # every gate category is present even when the run carried none
    for cat in ("multi-session", "knowledge-update",
                "single-session-preference"):
        assert cat in by_cat["rows"]


# ── #2516 (C1): per-category official rows (the type-tail gate surface) ────

def test_category_rows_use_the_same_official_projection():
    """#2516 (C1): the per-category row is the SAME exact projection as the
    headline (``recall_all == (fraction == 1.0)``, non-_abs only) — never a
    second rule, never the fraction, never any-hit."""
    outcomes = [
        dict(_outcome("m1", 1.0), question_type="multi-session"),
        dict(_outcome("m2", 0.5), question_type="multi-session"),
        dict(_outcome("k1", 1.0), question_type="knowledge-update"),
        dict(_outcome("p1", 0.0),
             question_type="single-session-preference"),
        dict(_outcome("x_abs", 1.0), question_type="multi-session"),
    ]
    block = category_variant_rows(outcomes, 5)
    rows = block["rows"]
    # multi-session: fractions 1.0 / 0.5 → official = 1/2 (NOT 0.75)
    assert rows["multi-session"]["official_recall_all@5"] == 0.5
    assert rows["multi-session"]["fraction_paper@5"] == 0.75
    assert rows["multi-session"]["n"] == 2
    assert rows["knowledge-update"]["official_recall_all@5"] == 1.0
    assert rows["single-session-preference"]["official_recall_all@5"] == 0.0
    # the _abs question is excluded from every category AND from full_mix
    assert rows["full_mix"]["n"] == 4
    assert rows["full_mix"]["official_recall_all@5"] == 0.5
    assert block["coverage"] == {"gate_categories_total": 3,
                                 "gate_categories_with_questions": 3,
                                 "coverage": 1.0}


def test_category_rows_report_absent_categories_as_covered_zero():
    """#2516 (C1): a gate category with NO questions in the run is returned
    with ``n == 0`` / ``value is None`` — visible, never omitted and never
    fabricated (the '≥95% tail coverage' target has to be checkable)."""
    outcomes = [dict(_outcome("m1", 1.0), question_type="multi-session")]
    block = category_variant_rows(outcomes, 5)
    for cat in ("knowledge-update", "single-session-preference"):
        assert block["rows"][cat] == {"official_recall_all@5": None,
                                      "fraction_paper@5": None,
                                      "n": 0, "n_excluded": 0}
    assert block["coverage"] == {"gate_categories_total": 3,
                                 "gate_categories_with_questions": 1,
                                 "coverage": pytest.approx(1 / 3)}


def test_variant_rows_carry_the_by_category_block():
    """#2516 (C1): the per-category rows ride WITH the top-level rows so a
    publication artifact cannot carry one without the other."""
    outcomes = [dict(_outcome("m1", 1.0), question_type="multi-session")]
    rows = variant_rows(outcomes, k=5)
    assert rows["by_category"]["rows"]["multi-session"][
        "official_recall_all@5"] == 1.0
    # the per-category full_mix row agrees with the headline row's value over
    # the same non-_abs set (one projection implementation)
    assert rows["by_category"]["rows"]["full_mix"][
        "official_recall_all@5"] == rows["official_recall_all@5"]["value"]
