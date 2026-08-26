"""Report integrity-block tests (M4 #1524, D5; #1747 gate criterion).

The additive ``integrity`` block (valid / invalid_rate / n_valid / n_invalid /
n_failed / threshold / error_census) is emitted by ``build_report``, printed
BEFORE the score in ``_print_summary``, gated by the ``--integrity-threshold``
CLI flag, and never blocks publication (scope: retry-then-fix, not
refuse-to-publish). E2E-2's offline analog: a mini pipeline with a flaky
extractor model reports its integrity on the same block.

#1747: the ``valid`` VERDICT is now census-class-aware — ``valid == True`` iff
``invalid_rate <= threshold`` AND zero hard-failure questions. Recoverable
classes (parse_error / truncated / truncated_parse_error / partial_parse /
transient_*) are rate-limited (a healthy run at 500-Q scale admits a handful
— the old binary ``len(errors)==0`` invalidity made ``valid=true``
unreachable); hard classes (fatal_* / ingest / unknown census classes /
non-census error strings with an EMPTY census / permanent eval failures)
veto the run at any threshold (a mixed recoverable+structural shape is
rate-limited — the #1746 lane). Each qid is graded ONCE (failure-grade
dominance on qid overlap — concurrent-checkpoint robustness); ``n_valid`` /
``n_invalid`` / ``invalid_rate`` keep their previous semantics for the
production shape; the breakdown rides in the additive ``n_hard_invalid`` /
``n_recoverable_invalid`` / ``recoverable_invalid_rate`` / ``criterion`` /
``error_census_malformed`` fields.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval.dataset_audit import audit_dataset  # noqa: E402, I001, RUF100
from tools.longmem_eval.report import build_report  # noqa: E402, I001, RUF100
from tools.longmem_eval.run import _build_parser, _print_summary  # noqa: E402, I001, RUF100


def _audit() -> dict:
    return audit_dataset([{
        "question_id": "q-audit",
        "haystack_session_ids": ["s0"],
        "answer_session_ids": ["s0"],
        "haystack_sessions": [[
            {"role": "user", "content": "x", "has_answer": True}]],
    }])


def _outcome(qid: str, *, valid: bool = True,
             error_classes: dict | None = None, label: bool = True) -> dict:
    return {
        "question_id": qid, "question_type": "single-session-user",
        "question_date": "2024-01-15", "label": label, "hypothesis": "h",
        "session_recall@k": {"5": 1.0}, "turn_recall@k": {"5": 1.0},
        "evidence_recall@k": {"5": 1.0}, "chunk_evidence_recall@k": {"5": 0.5},
        "n_ingest_errors": 0 if valid else 1, "context_tokens": 100,
        "context_point_count": 2, "retrieval_latency_ms": 1.0,
        "reader_latency_ms": 2.0, "judge_latency_ms": 3.0, "total_ms": 6.0,
        "valid": valid, "error_classes": error_classes or {},
        "leg_mix": {"tfidf": 2}, "leg_mix@k": {"5": {"tfidf": 2}},
        "pool_size": 5, "evidence_written": 1,
        "evidence_retrieved@k": {"5": 1}, "ingest_latency_ms": 1.0,
    }


def _report(outcomes: list[dict], *, threshold: float = 0.0,
            failures: list[dict] | None = None) -> dict:
    return build_report(
        outcomes,
        dataset_id="xiaowu0162/longmemeval-cleaned", split="s",
        reader_model="mock-reader", judge_model="mock-judge",
        extraction_approach="deterministic session ingestion",
        ingest_mode="deterministic", ks=(5,), top_k=5,
        dataset_semantics_audit=_audit(),
        integrity_threshold=threshold, failures=failures,
    )


def test_report_integrity_block_shape():
    """Mixed error classes → correct census / counts; the #1747 verdict is
    FALSE because q2 carries a HARD class (fatal_402_billing) — the hard veto
    fires even at threshold 0.5 where the 0.5 rate alone would pass (veto
    isolation). q1's recoverable-only census with the runner flag
    ``valid=True`` grades CLEAN (the runner's binary flag is the authority on
    whether error strings exist — the shape is a pinned drift guard, not a
    production shape)."""
    outcomes = [
        _outcome("q1", valid=True,
                 error_classes={"transient_429_rate_limit": 2}),
        _outcome("q2", valid=False,
                 error_classes={"fatal_402_billing": 1, "parse_error": 1}),
    ]
    integ = _report(outcomes, threshold=0.5)["integrity"]
    assert integ["valid"] is False       # hard class (fatal_402) vetoes
    assert integ["invalid_rate"] == 0.5  # rate alone would PASS at 0.5
    assert integ["n_attempted"] == 2
    assert integ["n_valid"] == 1
    assert integ["n_invalid"] == 1
    assert integ["n_failed"] == 0
    assert integ["threshold"] == 0.5
    assert integ["error_census"] == {
        "transient_429_rate_limit": 2,
        "fatal_402_billing": 1,
        "parse_error": 1,
    }
    # #1747 additive breakdown: q1 grades clean, q2 grades hard (the
    # recoverable parse_error in the same outcome does NOT soften it).
    assert integ["n_hard_invalid"] == 1
    assert integ["n_recoverable_invalid"] == 0
    assert integ["recoverable_invalid_rate"] == 0.0
    assert "criterion" in integ
    assert "census-class-aware" in integ["criterion"]


def test_report_integrity_zero_outcomes():
    """Empty run: vacuously valid — zero attempted, zero invalid, no hard
    classes, empty census (a 0-question report is not a degraded run)."""
    integ = _report([])["integrity"]
    assert integ["valid"] is True
    assert integ["invalid_rate"] == 0.0
    assert integ["n_attempted"] == 0
    assert integ["n_valid"] == 0
    assert integ["n_invalid"] == 0
    assert integ["n_failed"] == 0
    assert integ["error_census"] == {}
    assert integ["n_hard_invalid"] == 0
    assert integ["n_recoverable_invalid"] == 0
    assert integ["recoverable_invalid_rate"] == 0.0


def test_report_integrity_threshold():
    """#1747 rate semantics: recoverable-class (parse_error) invalid rate
    0.1 — threshold 0.05 → valid=false; threshold 0.2 → valid=true (no hard
    classes present, so the rate criterion alone decides)."""
    outcomes = [_outcome(f"q{i}", valid=True) for i in range(9)]
    outcomes.append(_outcome("q9", valid=False,
                             error_classes={"parse_error": 1}))
    assert _report(outcomes, threshold=0.05)["integrity"]["valid"] is False
    assert _report(outcomes, threshold=0.2)["integrity"]["valid"] is True


def test_report_integrity_recoverable_rate_at_threshold_is_valid():
    """#1747 (a): a run whose only error signals are RECOVERABLE classes at a
    rate within the declared threshold is valid=true — the gate criterion is
    reachable at scale (the old binary any-error-string invalidity made
    valid=true unreachable even for a healthy 500-Q run). Pins the INCLUSIVE
    ``<=`` boundary (9/100 = 9% == threshold 0.09 → valid) — a regression to
    strict ``<`` fails this test. Exercises ALL NINE recoverable allowlist
    classes, including the #1746 parse family (truncated_parse_error /
    partial_parse) — if any class drops out of the allowlist it hard-vetoes
    healthy runs with no other test catching it."""
    outcomes = [_outcome(f"q{i}", valid=True) for i in range(91)]
    # 9/100 questions, one per recoverable allowlist class → 9% rate.
    for i, cls in enumerate((
            "parse_error", "truncated", "truncated_parse_error",
            "partial_parse", "transient_429_rate_limit", "transient_5xx",
            "transient_timeout", "transient_network", "transient_unknown")):
        outcomes.append(_outcome(f"r{i}", valid=False,
                                 error_classes={cls: 1}))
    integ = _report(outcomes, threshold=0.09)["integrity"]
    assert integ["valid"] is True
    assert integ["n_hard_invalid"] == 0
    assert integ["n_recoverable_invalid"] == 9
    assert integ["n_invalid"] == 9
    assert integ["invalid_rate"] == 0.09
    assert integ["recoverable_invalid_rate"] == 0.09


def test_report_integrity_recoverable_rate_above_threshold_is_invalid():
    """#1747 (b): a recoverable-class rate ABOVE the threshold is still
    invalid — a parse_error spike (prompt/OUTPUT_CONTRACT regression) is a
    genuinely degraded run and must not pass."""
    outcomes = [_outcome(f"q{i}", valid=True) for i in range(90)]
    for i in range(10):
        outcomes.append(_outcome(f"bad{i}", valid=False,
                                 error_classes={"parse_error": 1}))
    integ = _report(outcomes, threshold=0.05)["integrity"]
    assert integ["invalid_rate"] == 0.1
    assert integ["valid"] is False


def test_report_integrity_hard_class_vetoes_regardless_of_rate():
    """#1747 (c): ANY hard-failure class → valid=false even when the rate is
    far below the threshold — a single fatal_402_billing in 500 questions
    (0.2% rate, threshold 0.05) must still fail the gate."""
    outcomes = [_outcome(f"q{i}", valid=True) for i in range(499)]
    outcomes.append(_outcome("q499", valid=False,
                             error_classes={"fatal_402_billing": 1}))
    integ = _report(outcomes, threshold=0.05)["integrity"]
    assert integ["invalid_rate"] == 0.002
    assert integ["valid"] is False
    assert integ["n_hard_invalid"] == 1


def test_report_integrity_unknown_census_class_fails_closed():
    """#1747: a census class outside the recoverable allowlist (a future
    extractor vocabulary) fails CLOSED — vetoes the run rather than silently
    riding the rate threshold."""
    outcomes = [_outcome("q0", valid=False,
                         error_classes={"future_class_xyz": 1})]
    integ = _report(outcomes, threshold=1.0)["integrity"]
    assert integ["valid"] is False
    assert integ["n_hard_invalid"] == 1


def test_report_integrity_non_census_error_string_is_structural_hard():
    """#1747 (structural corruption): a question the runner flagged invalid
    with an EMPTY census (non-census error strings: no-embed-list / S5
    failure / entity-resolution failure) is HARD — structural degradation
    cannot ride the rate threshold (no retry/ladder recovers it)."""
    outcomes = [_outcome("q0", valid=False, error_classes={})]
    integ = _report(outcomes, threshold=1.0)["integrity"]
    assert integ["valid"] is False
    assert integ["n_hard_invalid"] == 1


def test_report_integrity_recoverable_census_with_runner_clean_is_valid():
    """#1747 drift-pin: a recoverable-only census on a question the RUNNER
    already declared clean (valid=True) grades clean — the binary flag is
    the authority on whether error strings exist. This shape is UNREACHABLE
    with the current runner (every census bump pairs an errors.append, so
    non-empty census ⟹ valid=False — extractor_v2/run.py lockstep), but the
    grader's default is pinned so a future producer split cannot silently
    downgrade clean questions."""
    integ = _report([_outcome("q0", valid=True,
                              error_classes={"transient_network": 3})],
                    threshold=0.0)["integrity"]
    assert integ["valid"] is True
    assert integ["n_valid"] == 1
    assert integ["n_recoverable_invalid"] == 0


def test_report_integrity_hard_census_on_runner_clean_grades_hard():
    """#1747 (reviewer-pinned drift): a HARD census class on a question the
    runner flagged valid=True grades HARD regardless of the flag — a fatal_*
    census entry is an unrecoverable failure signal and must veto. (This
    shape is unreachable in production today — the runner bumps error
    strings and census together — but the grader's behavior is pinned so the
    drift cannot silently regress.)"""
    integ = _report([_outcome("q0", valid=True,
                              error_classes={"fatal_402_billing": 1})],
                    threshold=1.0)["integrity"]
    assert integ["valid"] is False
    assert integ["n_hard_invalid"] == 1
    assert integ["n_valid"] == 0


def test_report_integrity_legacy_flat_error_classes_shape():
    """#1747: the pre-census legacy flat-list ``error_classes`` shape still
    grades defensively (recoverable classes rate-limited; anything else hard)
    — the census block's back-compat branch is mirrored in the grader."""
    outcomes = [_outcome("q0", valid=False,
                         error_classes=["parse_error", "parse_error"])]
    integ = _report(outcomes, threshold=0.0)["integrity"]
    assert integ["n_recoverable_invalid"] == 1
    assert integ["valid"] is False  # 1/1 > threshold 0.0
    outcomes = [_outcome("q0", valid=False,
                         error_classes=["fatal_402_billing"])]
    integ = _report(outcomes, threshold=1.0)["integrity"]
    assert integ["n_hard_invalid"] == 1
    assert integ["valid"] is False  # hard veto


def test_report_integrity_failed_questions_cross_ref():
    """Question-level eval failures (reader/judge) are orthogonal to
    extraction integrity: they count into n_failed/n_invalid and their
    site-prefixed eval class rides the census (M7 semantics preserved).
    #1747: a transient-safe ``retries_exhausted`` failure is rate-limited
    (recoverable) — the 0.5 rate still fails the threshold 0.0 verdict here.
    """
    outcomes = [_outcome("q1", valid=True)]
    failures = [{
        "question_id": "q2", "error_class": "reader:retries_exhausted",
        "error": "boom", "failed_at_utc": "2026-08-20T00:00:00Z",
    }]
    integ = _report(outcomes, failures=failures)["integrity"]
    assert integ["n_failed"] == 1
    assert integ["n_attempted"] == 2
    assert integ["n_invalid"] == 1
    assert integ["invalid_rate"] == 0.5
    assert integ["error_census"]["reader:retries_exhausted"] == 1
    # retries_exhausted is transient-safe → recoverable, not a hard veto.
    assert integ["n_hard_invalid"] == 0
    assert integ["n_recoverable_invalid"] == 1
    assert integ["valid"] is False   # 0.5 > threshold 0.0 (rate criterion)


def test_report_integrity_permanent_eval_failure_vetoes():
    """#1747: a PERMANENT eval failure (reader:fatal / judge:parse / bare
    ingest) vetoes the run even at a rate within the threshold — a question
    that died on a permanent condition cannot be confirmed by re-running.
    """
    outcomes = [_outcome("q1", valid=True)]
    failures = [{
        "question_id": "q2", "error_class": "reader:fatal",
        "error": "boom", "failed_at_utc": "2026-08-20T00:00:00Z",
    }]
    integ = _report(outcomes, failures=failures, threshold=0.5)["integrity"]
    assert integ["invalid_rate"] == 0.5   # within threshold
    assert integ["valid"] is False        # hard veto fires
    assert integ["n_hard_invalid"] == 1

    ingest_fail = _report(outcomes, failures=[{
        "question_id": "q2", "error_class": "ingest",
        "error": "write-path boom", "failed_at_utc": "2026-08-20T00:00:00Z",
    }], threshold=0.5)["integrity"]
    assert ingest_fail["valid"] is False
    assert ingest_fail["n_hard_invalid"] == 1


def test_print_summary_integrity_before_score(capsys):
    report = _report([
        _outcome("q1", valid=True),
        _outcome("q2", valid=False,
                 error_classes={"fatal_402_billing": 2}),
    ])
    _print_summary(report)
    captured = capsys.readouterr().out
    assert captured.index("integrity") < captured.index("overall accuracy")
    assert "invalid_rate" in captured
    assert "fatal_402_billing" in captured
    assert "n_failed" in captured


def test_cli_integrity_threshold_flag():
    parser = _build_parser()
    assert parser.parse_args([]).integrity_threshold == 0.0
    assert parser.parse_args(["--integrity-threshold",
                              "0.05"]).integrity_threshold == 0.05
    assert parser.parse_args(["--integrity-threshold",
                              "1"]).integrity_threshold == 1.0
    # wiring pin: the CLI-parsed value is what build_report records as the
    # effective threshold the verdict is gated on (run_evaluation forwards it
    # verbatim — test_longmem_runner pins that hop end-to-end).
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"parse_error": 1})],
                    threshold=parser.parse_args(
                        ["--integrity-threshold", "0.05"]).integrity_threshold)["integrity"]
    assert integ["threshold"] == 0.05
    assert integ["valid"] is False  # 1/1 > 0.05


def test_report_integrity_mixed_hard_and_recoverable_run():
    """#1747 (reviewer-pinned): the realistic 500-Q production shape — clean
    majority + a handful of recoverable blips + ONE hard question. The hard
    veto — not the rate — is what flips the verdict, and the additive
    breakdown reports both buckets."""
    outcomes = [_outcome(f"q{i}", valid=True) for i in range(95)]
    for i in range(4):
        outcomes.append(_outcome(f"t{i}", valid=False,
                                 error_classes={"transient_network": 1}))
    outcomes.append(_outcome("hard", valid=False,
                             error_classes={"fatal_402_billing": 1}))
    integ = _report(outcomes, threshold=0.06)["integrity"]
    assert integ["valid"] is False        # hard veto fires
    assert integ["n_hard_invalid"] == 1
    assert integ["n_recoverable_invalid"] == 4
    assert integ["recoverable_invalid_rate"] == 0.04  # within threshold
    assert integ["n_invalid"] == 5
    assert integ["invalid_rate"] == 0.05


def test_report_integrity_all_clean_nonempty_run():
    """#1747: a non-empty all-clean run → valid=true with the full zeroed
    breakdown (the surface-map happy path: clean run → valid=true)."""
    integ = _report([_outcome(f"q{i}") for i in range(3)])["integrity"]
    assert integ["valid"] is True
    assert integ["n_attempted"] == 3
    assert integ["n_valid"] == 3
    assert integ["n_invalid"] == 0
    assert integ["invalid_rate"] == 0.0
    assert integ["recoverable_invalid_rate"] == 0.0
    assert integ["n_hard_invalid"] == 0
    assert integ["n_recoverable_invalid"] == 0
    assert integ["error_census"] == {}


def test_report_integrity_zero_count_census_semantics():
    """#1747 (security-review P1 flip): class presence is KEY presence — a
    present class with a zero/false count is still PRESENT (the extractor
    never emits zero counts, so a zero entry is anomalous and must fail
    closed; count-value presence would let a tampered checkpoint launder a
    hard class to clean). The count value only feeds the published census."""
    # zero-count recoverable + valid=False → the class is present and
    # recoverable-only → RECOVERABLE (rate-limited), not the empty-census
    # hard path.
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"parse_error": 0})],
                    threshold=1.0)["integrity"]
    assert integ["n_recoverable_invalid"] == 1
    assert integ["n_hard_invalid"] == 0
    assert integ["valid"] is True
    assert integ["error_census"] == {"parse_error": 0}
    # zero-count HARD class DOES veto (presence-by-key — the launder is
    # closed); the census still records the zero.
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"fatal_402_billing": 0,
                                              "parse_error": 1})],
                    threshold=1.0)["integrity"]
    assert integ["n_hard_invalid"] == 1
    assert integ["valid"] is False
    assert integ["error_census"] == {"fatal_402_billing": 0, "parse_error": 1}
    # false (bool) count on a hard class — the F1 laundering repro — vetoes.
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"fatal_402_billing": False})],
                    threshold=1.0)["integrity"]
    assert integ["n_hard_invalid"] == 1
    assert integ["valid"] is False
    assert integ["error_census_malformed"] == {"fatal_402_billing": False}


def test_report_integrity_malformed_census_count_fails_closed():
    """#1747 (security review): a non-int census count (malformed JSON from a
    schema-less checkpoint merge) must not crash build_report NOR silently
    drop the class: the class stays PRESENT in the graded set (a HARD class
    with a malformed count still vetoes — no fail-open), the malformed value
    is recorded in the separate ``error_census_malformed`` field (never
    vanishes, never poisons the int-summed ``error_census``), and the
    verdict counts QUESTIONS not census entries (a recoverable class with a
    malformed count is one rate-limited question)."""
    # hard class with a malformed count → veto (fail-closed).
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"fatal_402_billing": "abc"})],
                    threshold=1.0)["integrity"]
    assert integ["valid"] is False
    assert integ["n_hard_invalid"] == 1
    assert integ["error_census"] == {}
    assert integ["error_census_malformed"] == {"fatal_402_billing": "abc"}
    # recoverable class with a malformed count → one recoverable question
    # (rate-limited); the malformed value is still recorded.
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"parse_error": "abc"})],
                    threshold=1.0)["integrity"]
    assert integ["valid"] is True
    assert integ["n_recoverable_invalid"] == 1
    assert integ["error_census_malformed"] == {"parse_error": "abc"}


def test_report_integrity_mixed_malformed_and_int_census_counts():
    """#1747 (reviewer-pinned P1): the same census class with a malformed
    count in one outcome and a valid int in another (the realistic
    checkpoint-merge shape) must not TypeError the roll-up — the int sums
    into error_census, the malformed value is preserved in
    error_census_malformed."""
    outcomes = [
        _outcome("q0", valid=False, error_classes={"parse_error": "abc"}),
        _outcome("q1", valid=False, error_classes={"parse_error": 3}),
        _outcome("q2", valid=True, error_classes={"transient_network": 2}),
    ]
    integ = _report(outcomes, threshold=0.0)["integrity"]
    assert integ["error_census"] == {"parse_error": 3, "transient_network": 2}
    assert integ["error_census_malformed"] == {"parse_error": "abc"}
    assert integ["n_recoverable_invalid"] == 2


def test_report_integrity_bool_and_mixed_key_census_robustness():
    """#1747 (reviewer-pinned): bool counts (JSON true/false) are recorded in
    error_census_malformed (never vanish from the record) and the class stays
    PRESENT for grading (presence-by-key); mixed-type class keys never crash
    the sorted() roll-up; container-valued counts are preserved verbatim."""
    # bool count on a hard class → veto fires AND the evidence is preserved.
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"fatal_402_billing": True})],
                    threshold=1.0)["integrity"]
    assert integ["valid"] is False
    assert integ["n_hard_invalid"] == 1
    assert integ["error_census"] == {}
    assert integ["error_census_malformed"] == {"fatal_402_billing": True}
    # mixed-type class keys (programmatic-caller shape) do not crash.
    integ = _report([_outcome("q0", valid=False,
                              error_classes={5: 1, "parse_error": 2})],
                    threshold=1.0)["integrity"]
    assert integ["valid"] is False
    assert integ["n_hard_invalid"] == 1  # non-str key fails closed
    assert integ["error_census"]["parse_error"] == 2
    # container-valued counts (list/dict — tampered JSON) are preserved
    # verbatim in error_census_malformed.
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"parse_error": [1, 2]})],
                    threshold=1.0)["integrity"]
    assert integ["n_recoverable_invalid"] == 1
    assert integ["error_census"] == {}
    assert integ["error_census_malformed"] == {"parse_error": [1, 2]}


def test_report_integrity_non_bool_valid_flag_fails_closed():
    """#1747 (security review): a PRESENT but non-bool ``valid`` flag
    (``"valid": "false"`` from a schema-less checkpoint) is malformed input
    — truthiness coercion would fail OPEN (certifying a structurally-degraded
    run as clean), so the grader fails CLOSED to hard. A MISSING flag keeps
    the historical back-compat default True (legacy pre-M7 checkpoints have
    no flag at all — they are graded by their census)."""
    outcomes = [_outcome("q0", valid=False, error_classes={})]
    outcomes[0]["valid"] = "false"  # stringified flag — malformed
    integ = _report(outcomes, threshold=1.0)["integrity"]
    assert integ["valid"] is False
    assert integ["n_hard_invalid"] == 1
    # present non-bool flag with a recoverable census → also fail-closed.
    outcomes = [_outcome("q0", valid=False,
                         error_classes={"parse_error": 1})]
    outcomes[0]["valid"] = "false"
    integ = _report(outcomes, threshold=1.0)["integrity"]
    assert integ["n_hard_invalid"] == 1
    assert integ["valid"] is False
    # missing flag → back-compat default True: graded by the census (a
    # legacy pre-M7 checkpoint without the flag is clean when the census is
    # empty — the historical n_valid behavior).
    outcomes = [_outcome("q0", valid=True, error_classes={})]
    del outcomes[0]["valid"]
    integ = _report(outcomes, threshold=1.0)["integrity"]
    assert integ["n_valid"] == 1
    assert integ["valid"] is True


def test_report_integrity_non_iterable_error_classes_fails_closed():
    """#1747 (security review): error_classes that are non-iterable / non-
    string (malformed checkpoint JSON: 5, true, [{"a": 1}]) fail CLOSED to
    hard instead of crashing build_report."""
    for bad in (5, True, [{"a": 1}], None):
        integ = _report([_outcome("q0", valid=False, error_classes=bad)],
                        threshold=1.0)["integrity"]
        assert integ["valid"] is False, f"shape {bad!r} did not veto"
        assert integ["n_hard_invalid"] == 1, f"shape {bad!r} not graded hard"


def test_report_integrity_mixed_failure_grades():
    """#1747 (reviewer-pinned): a failure list with BOTH a transient-safe
    (reader:retries_exhausted) and a permanent (reader:fatal) entry — the
    invariant n_hard_invalid + n_recoverable_invalid == n_invalid is
    asserted against both grades coexisting, and both criteria (rate + veto)
    are exercised simultaneously."""
    outcomes = [_outcome("q1", valid=True)]
    failures = [
        {"question_id": "q2", "error_class": "reader:retries_exhausted",
         "error": "x", "failed_at_utc": "2026-08-20T00:00:00Z"},
        {"question_id": "q3", "error_class": "reader:fatal",
         "error": "x", "failed_at_utc": "2026-08-20T00:00:00Z"},
    ]
    integ = _report(outcomes, failures=failures, threshold=0.5)["integrity"]
    assert integ["n_hard_invalid"] == 1
    assert integ["n_recoverable_invalid"] == 1
    assert integ["n_invalid"] == 2
    assert integ["n_attempted"] == 3
    assert integ["invalid_rate"] == round(2 / 3, 4)
    assert integ["valid"] is False       # rate 0.667 > 0.5 AND hard veto


def test_report_integrity_missing_error_class_fails_closed():
    """#1747 (reviewer-pinned): a failure entry with NO error_class (the
    full_context cell producer's shape) grades HARD — fail-closed; a
    question that died with no recorded class cannot ride the rate
    threshold."""
    outcomes = [_outcome("q1", valid=True)]
    failures = [{"question_id": "q2", "error": "boom",
                 "failed_at_utc": "2026-08-20T00:00:00Z"}]
    integ = _report(outcomes, failures=failures, threshold=0.5)["integrity"]
    assert integ["n_hard_invalid"] == 1
    assert integ["valid"] is False
    # tampered suffix must NOT match the recoverable allowlist (fail-closed).
    tampered = _report(outcomes, failures=[{
        "question_id": "q2", "error_class": "evil:retries_exhausted",
        "error": "x", "failed_at_utc": "2026-08-20T00:00:00Z"}],
        threshold=0.5)["integrity"]
    assert tampered["n_hard_invalid"] == 1
    assert tampered["valid"] is False


def test_report_integrity_qid_overlap_failure_grade_dominates():
    """#1747 (history-review P1): a qid in BOTH outcomes and failures (the
    concurrent checkpoint-merge shape — one worker completes a question
    another failed) is graded ONCE with the failure grade dominating; the
    invariants n_valid + n_invalid == n_attempted and n_hard + n_recoverable
    == n_invalid hold without any double count or negative n_valid."""
    integ = _report([_outcome("a", valid=True)], failures=[{
        "question_id": "a", "error_class": "reader:fatal",
        "error": "x", "failed_at_utc": "2026-08-20T00:00:00Z"}])
    i = integ["integrity"]
    assert i["n_attempted"] == 1
    assert i["n_valid"] == 0            # failure grade dominates clean
    assert i["n_invalid"] == 1
    assert i["n_hard_invalid"] == 1
    assert i["n_recoverable_invalid"] == 0
    assert i["invalid_rate"] == 1.0
    assert i["valid"] is False

    # recoverable failure over a clean outcome → recoverable (rate-limited).
    i = _report([_outcome("a", valid=True)], failures=[{
        "question_id": "a", "error_class": "reader:retries_exhausted",
        "error": "x", "failed_at_utc": "2026-08-20T00:00:00Z"}])["integrity"]
    assert i["n_valid"] == 0
    assert i["n_recoverable_invalid"] == 1
    assert i["n_invalid"] == 1
    assert i["n_hard_invalid"] == 0


def test_report_integrity_non_str_question_id_skipped():
    """#1747 (security-review F4): a non-str question_id (malformed
    checkpoint JSON: a list/dict is truthy and would be indexed) is SKIPPED,
    not indexed — an unhashable value must not crash build_report; the
    malformed qid simply does not count toward n_attempted."""
    good = _outcome("q0", valid=True)
    bad = _outcome("q1", valid=True)
    bad["question_id"] = ["not", "a", "string"]
    integ = _report([good, bad], failures=[{
        "question_id": 7, "error_class": "reader:fatal",
        "error": "x", "failed_at_utc": "2026-08-20T00:00:00Z"}])["integrity"]
    assert integ["n_attempted"] == 1     # only the str qid counts
    assert integ["n_valid"] == 1
    assert integ["n_hard_invalid"] == 0  # the int-qid failure is skipped
    assert integ["valid"] is True


def test_run_protocol_step5_gate_string_pins_criterion():
    """#1747 verification-checklist (3): the run-protocol step-5 gate string
    (and the executed step-5 command) must reflect the approved criterion —
    the hard veto + the justified 0.02 threshold injected by `run 5`."""
    from tools.longmem_eval.run_protocol import (  # noqa: I001
        STEPS_BY_NUMBER, build_command, JUSTIFIED_BASELINE_THRESHOLD,
    )
    from tools.longmem_eval.run_protocol import ProtocolState
    from pathlib import Path
    import tempfile

    gate = STEPS_BY_NUMBER[5].gate
    assert "invalid_rate ≤ threshold" in gate
    assert "n_hard_invalid == 0" in gate
    assert f"{JUSTIFIED_BASELINE_THRESHOLD}" in gate
    # the allowance math is pinned (0.02 × 500 = 10 questions — a regression
    # to percentage formatting fails here).
    assert "≤10 of 500 questions" in gate

    # the executed step-5 command injects the justified threshold AND its
    # recorded justification (M7: a non-default threshold is never silently
    # applied) — the run matches the documented gate.
    state = ProtocolState(Path(tempfile.mkdtemp()) / "state.json")
    cmd = build_command(STEPS_BY_NUMBER[5], [], state=state)
    assert "--integrity-threshold" in cmd
    assert f"{JUSTIFIED_BASELINE_THRESHOLD}" in cmd
    assert "--integrity-justification" in cmd
    assert "#1747 justified" in " ".join(cmd)
