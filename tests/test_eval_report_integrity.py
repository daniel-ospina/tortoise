"""Report integrity-block tests (M4 #1524, D5; #1747 gate criterion).

The additive ``integrity`` block (valid / invalid_rate / n_valid / n_invalid /
n_failed / threshold / error_census) is emitted by ``build_report``, printed
BEFORE the score in ``_print_summary``, gated by the ``--integrity-threshold``
CLI flag, and never blocks publication (scope: retry-then-fix, not
refuse-to-publish). E2E-2's offline analog: a mini pipeline with a flaky
extractor model reports its integrity on the same block.

#1747: the ``valid`` VERDICT is now census-class-aware — ``valid == True`` iff
``n_hard_invalid == 0`` AND ``n_excluded_hard == 0`` AND
``invalid_rate <= threshold`` AND (outcome-derived attempted set non-empty
whenever any entry was excluded or dropped — a fully excluded/dropped run
never certifies; failures do not count as attempts for this guard).
Recoverable
classes (parse_error / truncated / truncated_parse_error / partial_parse /
transient_* census classes, plus reader/judge:retries_exhausted eval
failures) are rate-limited (a healthy run at 500-Q scale admits a handful
— the old binary ``len(errors)==0`` invalidity made ``valid=true``
unreachable); hard classes (fatal_* / ingest / unknown census classes /
non-census error strings with an EMPTY census / permanent eval failures /
malformed inputs — present non-bool `valid` / non-iterable, non-str,
falsy-but-present or PRESENT-null `error_classes` — fail closed to hard,
and a shape-broken / breaker_open outcome with a hard census still vetoes
via ``n_excluded_hard``) veto the run at any threshold (a
mixed recoverable+structural shape is rate-limited — the #1746 lane). Each
qid is graded ONCE (failure-grade dominance on qid overlap —
concurrent-checkpoint robustness); ``n_valid`` /
``n_invalid`` / ``invalid_rate`` keep their previous semantics for the
production shape; the breakdown rides in the additive ``n_hard_invalid`` /
``n_recoverable_invalid`` / ``recoverable_invalid_rate`` / ``criterion`` /
``error_census_malformed`` / ``n_excluded`` / ``n_excluded_hard`` fields.
"""
from __future__ import annotations

import math
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
             error_classes: dict | None = None, label: bool = True,
             gate_reasons: list | None = None) -> dict:
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
        "gate_reasons": gate_reasons or [],
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
    non-empty census ⟹ valid=False — tortoise/extractor_v2.py +
    tools/longmem_eval/run.py lockstep), but the
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


def test_report_integrity_ingest_transient_recoverable_rates():
    """#1776: an ingest-site TRANSIENT failure grades
    ``ingest:retries_exhausted`` — recoverable, rate-limited at the
    threshold like the identical reader/judge transients (a single
    FalkorDB/network blip during ingest must not veto the whole run); a
    bare ``ingest`` (structurally-fatal) still hard-vetoes at any
    threshold (fail-closed)."""
    outcomes = [_outcome("q1", valid=True)]
    recoverable = _report(outcomes, failures=[{
        "question_id": "q2", "error_class": "ingest:retries_exhausted",
        "error": "FalkorDB blip", "failed_at_utc": "2026-08-20T00:00:00Z",
    }], threshold=0.5)["integrity"]
    assert recoverable["valid"] is True   # rate-limited, not vetoed
    assert recoverable["n_hard_invalid"] == 0

    above = _report(outcomes, failures=[{
        "question_id": "q2", "error_class": "ingest:retries_exhausted",
        "error": "FalkorDB blip", "failed_at_utc": "2026-08-20T00:00:00Z",
    }], threshold=0.0)["integrity"]
    assert above["valid"] is False        # recoverable still rate-limited

    bare = _report(outcomes, failures=[{
        "question_id": "q2", "error_class": "ingest",
        "error": "extractor-internal boom",
        "failed_at_utc": "2026-08-20T00:00:00Z",
    }], threshold=1.0)["integrity"]
    assert bare["valid"] is False         # bare ingest vetoes at ANY threshold


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


def test_print_summary_surfaces_invalidity_deciding_terms(capsys):
    """#1747 (round-17 code-review P2): valid=false is commonly decided by
    terms NOT in the old one-line summary (n_hard_invalid / n_excluded_hard
    / n_excluded / vacuity) — an operator can see valid: false with
    invalid_rate 0.0 where the only printed numbers did NOT decide the
    verdict. The deciding terms are surfaced when the verdict is false
    (additive — the valid=true output shape is unchanged)."""
    report = _report([
        _outcome("q1", valid=False,
                 error_classes={"fatal_402_billing": 2}),
        _outcome("q2", valid=True),
    ])
    _print_summary(report)
    captured = capsys.readouterr().out
    assert "invalidity decided by:" in captured
    assert "n_hard_invalid 1" in captured
    assert "n_excluded_hard 0" in captured
    assert "n_excluded 0" in captured
    assert "n_attempted 2" in captured
    assert "vacuity" in captured
    # a valid run does NOT print the deciding-terms line (additive-only).
    _print_summary(_report([_outcome("q1", valid=True)]))
    captured_ok = capsys.readouterr().out
    assert "invalidity decided by:" not in captured_ok
    # round-17 review-fix: a VACUITY-decided verdict (all breaker_open
    # drops, rate+veto terms all pass) prints the vacuity evidence
    # (n_attempted / dropped), not a misleading all-zeros line.
    drops = []
    for i in range(3):
        o = _outcome(f"q{i}", valid=True)
        o["breaker_open"] = True
        o["dropped_reason"] = "breaker_open"
        drops.append(o)
    _print_summary(_report(drops, threshold=1.0))
    captured_vac = capsys.readouterr().out
    assert "invalidity decided by:" in captured_vac
    assert "dropped 3" in captured_vac
    assert "n_excluded_hard 0" in captured_vac


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
    assert integ["error_census_malformed"] == {"fatal_402_billing": [False]}


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
    assert integ["error_census_malformed"] == {"fatal_402_billing": ["abc"]}
    # recoverable class with a malformed count → one recoverable question
    # (rate-limited); the malformed value is still recorded.
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"parse_error": "abc"})],
                    threshold=1.0)["integrity"]
    assert integ["valid"] is True
    assert integ["n_recoverable_invalid"] == 1
    assert integ["error_census_malformed"] == {"parse_error": ["abc"]}


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
    assert integ["error_census_malformed"] == {"parse_error": ["abc"]}
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
    assert integ["error_census_malformed"] == {"fatal_402_billing": [True]}
    # mixed-type class keys (programmatic-caller shape) do not crash — and
    # the report SERIALIZES (json.dumps sort_keys with str-coerced keys).
    integ = _report([_outcome("q0", valid=False,
                              error_classes={5: 1, "parse_error": 2})],
                    threshold=1.0)["integrity"]
    assert integ["valid"] is False
    assert integ["n_hard_invalid"] == 1  # non-str key fails closed
    assert integ["error_census"]["parse_error"] == 2
    from tools.longmem_eval.report import save_report  # noqa: I001
    import json as _json
    import tempfile as _tempfile
    _json.loads(save_report(
        _report([_outcome("q0", valid=False,
                          error_classes={5: 1, "parse_error": 2})],
                threshold=1.0),
        Path(_tempfile.mkdtemp()) / "r.json").read_text())
    # container-valued counts (list/dict — tampered JSON) are preserved
    # verbatim in error_census_malformed.
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"parse_error": [1, 2]})],
                    threshold=1.0)["integrity"]
    assert integ["n_recoverable_invalid"] == 1
    assert integ["error_census"] == {}
    assert integ["error_census_malformed"] == {"parse_error": [[1, 2]]}
    # TWO DISTINCT malformed values for one class (the heterogeneous
    # checkpoint-merge shape) accumulate — the second never vanishes.
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"parse_error": "abc"}),
                     _outcome("q1", valid=False,
                              error_classes={"parse_error": [1, 2]})],
                    threshold=1.0)["integrity"]
    assert integ["error_census"] == {}
    assert integ["error_census_malformed"]["parse_error"] == ["abc", [1, 2]]
    # a None FIRST malformed value is not overwritten by a later one (the
    # presence check treats a stored None as a recorded value).
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"parse_error": None}),
                     _outcome("q1", valid=False,
                              error_classes={"parse_error": "abc"})],
                    threshold=1.0)["integrity"]
    assert integ["error_census_malformed"]["parse_error"] == [None, "abc"]
    # legacy flat-list junk ACCUMULATES across outcomes (no overwrite).
    integ = _report([_outcome("q0", valid=False,
                              error_classes=[{"a": 1}]),
                     _outcome("q1", valid=False,
                              error_classes=[{"b": 2}])],
                    threshold=1.0)["integrity"]
    assert integ["error_census_malformed"]["<legacy-list>"] == [{"a": 1}, {"b": 2}]
    # round-11: a LIST-first malformed count (container) is a flat list of
    # DISTINCT values — an identical container re-occurrence dedupes instead
    # of nesting ("[1,2] then [1,2]" → [[1, 2]], never [1,2,[1,2]]).
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"parse_error": [1, 2]}),
                     _outcome("q1", valid=False,
                              error_classes={"parse_error": [1, 2]})],
                    threshold=1.0)["integrity"]
    assert integ["error_census_malformed"] == {"parse_error": [[1, 2]]}
    # round-12: NaN counts dedupe (NaN != NaN would defeat the membership
    # check) — canonicalized to None, matching the serialized null.
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"parse_error": float("nan")}),
                     _outcome("q1", valid=False,
                              error_classes={"parse_error": float("nan")})],
                    threshold=1.0)["integrity"]
    assert integ["error_census_malformed"] == {"parse_error": [None]}
    # round-12: legacy flat-list junk dedupes across outcomes (mirror of the
    # dict branch) — a value-identical junk element re-occurring never
    # duplicates in the published evidence.
    integ = _report([_outcome("q0", valid=False,
                              error_classes=[{"a": 1}]),
                     _outcome("q1", valid=False,
                              error_classes=[{"a": 1}])],
                    threshold=1.0)["integrity"]
    assert integ["error_census_malformed"]["<legacy-list>"] == [{"a": 1}]
    # round-13: legacy flat-list NaN junk dedupes too (canonicalized to None,
    # mirroring the dict branch — NaN != NaN would defeat the membership
    # check).
    integ = _report([_outcome("q0", valid=False,
                              error_classes=["parse_error", float("nan")]),
                     _outcome("q1", valid=False,
                              error_classes=["parse_error", float("nan")])],
                    threshold=1.0)["integrity"]
    assert integ["error_census_malformed"]["<legacy-list>"] == [None]


def test_report_integrity_label_null_excluded_and_retrieval_only_carveout():
    """#1747 (round-12 review): a tampered label:null outcome (non-bool) is
    excluded into n_excluded — never silently counted as an incorrect answer
    (the accuracy numerator only ever sees real bools). EXCEPT on
    retrieval-only runs, where the runner emits label: None by design and
    the accuracy block is not published — those outcomes still occupy the
    attempted set (the vector-arm retrieval-only report shape)."""
    # label: null (tampered) on a normal run → excluded, never wrong-graded.
    o = _outcome("q0", valid=True)
    o["label"] = None
    integ = _report([o, _outcome("q1", valid=True)])["integrity"]
    assert integ["n_excluded"] == 1
    assert integ["n_attempted"] == 1
    # label: "true" (stringified) → excluded too (real bool required).
    o = _outcome("q0", valid=True)
    o["label"] = "true"
    integ = _report([o, _outcome("q1", valid=True)])["integrity"]
    assert integ["n_excluded"] == 1
    # retrieval-only carve-out: label None is admitted, attempted set intact.
    o = _outcome("q0", valid=True)
    o["label"] = None  # the runner emits label: None on retrieval-only runs
    integ2 = build_report(
        [o],
        dataset_id="xiaowu0162/longmemeval-cleaned", split="s",
        reader_model="mock-reader", judge_model="mock-judge",
        extraction_approach="deterministic session ingestion",
        ingest_mode="deterministic", ks=(5,), top_k=5,
        dataset_semantics_audit=_audit(), integrity_threshold=0.0,
        retrieval_only=True)["integrity"]
    assert integ2["n_attempted"] == 1
    assert integ2["valid"] is True


def test_report_integrity_json_safe_decimal_and_in_memory_strict():
    """#1747 (round-12 security review): (1) _json_safe handles Decimal
    non-finite (save_report never TypeErrors on it — strict JSON null);
    (2) the report RETURNED by build_report is strict JSON by contract — a
    NaN riding the raw extra[outcomes] projection serializes as null even
    before save_report (json.dumps with allow_nan=False succeeds on the
    in-memory dict)."""
    import json as _json
    import tempfile as _tempfile
    from decimal import Decimal as _Decimal

    from tools.longmem_eval.report import save_report
    p = Path(_tempfile.mkdtemp()) / "r.json"
    save_report({"v": _Decimal("NaN")}, p)
    _json.loads(p.read_text())                     # no TypeError, strict
    # round-13: a FINITE Decimal is preserved as a number (nulling every
    # Decimal silently destroyed finite values).
    p2 = Path(_tempfile.mkdtemp()) / "r2.json"
    save_report({"v": _Decimal("3.14")}, p2)
    assert _json.loads(p2.read_text())["v"] == 3.14
    # round-14: a huge-but-finite Decimal (float() overflows to inf) is
    # nulled — the strict-JSON boundary never leaks an Infinity token.
    p3 = Path(_tempfile.mkdtemp()) / "r3.json"
    save_report({"v": _Decimal("1e400")}, p3)
    assert _json.loads(p3.read_text())["v"] is None
    # round-15: a signaling NaN Decimal is nulled too (float(sNaN) raises
    # ValueError — the finiteness check runs BEFORE converting).
    p4 = Path(_tempfile.mkdtemp()) / "r4.json"
    save_report({"v": _Decimal("sNaN")}, p4)
    assert _json.loads(p4.read_text())["v"] is None
    o = _outcome("q0", valid=True)
    o["session_recall@k"]["5"] = float("nan")
    r = build_report(
        [o], dataset_id="xiaowu0162/longmemeval-cleaned", split="s",
        reader_model="mock-reader", judge_model="mock-judge",
        extraction_approach="deterministic session ingestion",
        ingest_mode="deterministic", ks=(5,), top_k=5,
        dataset_semantics_audit=_audit(), integrity_threshold=0.5)
    _json.dumps(r, allow_nan=False)                # in-memory dict is strict


def test_report_integrity_json_safe_nonfinite_null():
    """#1747 (round-11 security review): a NaN/Infinity value riding the raw
    extra[outcomes] projection (excluded from the MEANS but published
    verbatim) must serialize as STRICT JSON — _json_safe maps non-finite
    floats to null so json.dumps never emits NaN/Infinity tokens."""
    import json as _json
    import tempfile as _tempfile

    from tools.longmem_eval.report import save_report
    o = _outcome("q0", valid=True)
    o["session_recall@k"]["5"] = float("nan")  # bypasses the shape filter's
    # _numeric (the raw projection can still publish it) — save_report must
    # emit strict JSON regardless.
    report = _report([o])
    report["outcomes"] = [o]
    text = save_report(report, Path(_tempfile.mkdtemp()) / "r.json").read_text()
    _json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(
        ValueError(f"non-strict token {c}")))  # strict parse must succeed


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


def test_report_integrity_unclassified_failure_vetoes_at_justified_threshold():
    """#1747 (round-17 code-review P2): the full-context cell producer
    (tools/longmem_eval/full_context.py) appends failures WITHOUT an
    error_class key — _failure_grade(None) grades HARD, so it vetoes at
    ANY threshold, silently changing integrity.valid semantics vs the old
    M7 gate (rate-limited) — a deliberate fail-closed flip, deferred to
    #1746 (documented in the README + the missing-error-class test above,
    but not exercised at a NONZERO threshold). Pin it at the step-5
    justified threshold 0.02: 49 clean outcomes + 1 unclassified failure
    rides the rate exactly (invalid_rate 0.02 ≤ 0.02) yet must NOT
    certify — the unclassified failure is hard, never rate-limited."""
    outcomes = [_outcome(f"q{i}", valid=True) for i in range(49)]
    integ = _report(outcomes, failures=[{
        # full_context.py's shape: question_id / question_type / error /
        # failed_at_utc — NO error_class.
        "question_id": "q-cell", "error": "boom",
        "failed_at_utc": "2026-08-20T00:00:00Z"}],
        threshold=0.02)["integrity"]
    assert integ["n_hard_invalid"] == 1
    assert integ["invalid_rate"] == round(1 / 50, 4)  # 0.02 — rides the rate
    assert integ["valid"] is False                    # but hard vetoes
    # the same shape with a recoverable class does NOT veto at the
    # boundary — invalid_rate 0.02 ≤ threshold 0.02 passes the rate, so
    # valid=True (the flip is class-specific, not rate-specific: the
    # unclassified failure vetoes where the recoverable one rides).
    recoverable = _report(outcomes, failures=[{
        "question_id": "q-cell", "error_class": "reader:retries_exhausted",
        "error": "boom", "failed_at_utc": "2026-08-20T00:00:00Z"}],
        threshold=0.02)["integrity"]
    assert recoverable["n_recoverable_invalid"] == 1
    assert recoverable["invalid_rate"] == round(1 / 50, 4)  # 0.02
    assert recoverable["valid"] is True    # 0.02 ≤ 0.02 — rate boundary met
    # just below the boundary the recoverable class flips invalid —
    # contrasting the unclassified failure that vetoes at ANY threshold.
    below = _report(outcomes, failures=[{
        "question_id": "q-cell", "error_class": "reader:retries_exhausted",
        "error": "boom", "failed_at_utc": "2026-08-20T00:00:00Z"}],
        threshold=0.0199)["integrity"]
    assert below["valid"] is False



def test_report_integrity_non_str_failure_class_preserved_in_census():
    """#1747 (round-17 code-review P2): a failure with a non-str
    error_class VALUE (int/dict/float/bool — malformed checkpoint JSON)
    grades hard and vetoes via _failure_grade, but used to VANISH from the
    record — absent from error_census AND error_census_malformed —
    contradicting the round-15/16 'no malformed evidence vanishes at any
    level' contract (outcome-side shapes are preserved under sentinel
    keys). It is now preserved under the ``<failure-class>`` sentinel key
    with the same distinct-membership accumulator + non-finite
    canonicalization as the census branches."""
    outcomes = [_outcome("q1", valid=True)]
    integ = _report(outcomes, threshold=0.5, failures=[
        {"question_id": "q2", "error_class": 123, "error": "x",
         "failed_at_utc": "2026-08-20T00:00:00Z"},
    ])["integrity"]
    assert integ["n_hard_invalid"] == 1
    assert integ["valid"] is False                  # still vetoes
    assert integ["error_census"] == {}              # never into the census
    assert integ["error_census_malformed"] == {"<failure-class>": [123]}
    # dict-valued class preserved verbatim; int 123 and float 123.0 stay
    # DISTINCT JSON tokens (type-exact membership, round-16 discipline).
    integ = _report(outcomes, threshold=0.5, failures=[
        {"question_id": "q2", "error_class": {"a": 1}, "error": "x",
         "failed_at_utc": "2026-08-20T00:00:00Z"},
        {"question_id": "q3", "error_class": 123, "error": "x",
         "failed_at_utc": "2026-08-20T00:00:00Z"},
        {"question_id": "q4", "error_class": 123.0, "error": "x",
         "failed_at_utc": "2026-08-20T00:00:00Z"},
        {"question_id": "q5", "error_class": 123, "error": "x",
         "failed_at_utc": "2026-08-20T00:00:00Z"},   # dup dedupes
    ])["integrity"]
    assert integ["error_census_malformed"] == {
        "<failure-class>": [{"a": 1}, 123, 123.0]}
    # non-finite class canonicalizes to None (NaN != NaN dedup defeat).
    integ = _report(outcomes, threshold=0.5, failures=[
        {"question_id": "q2", "error_class": float("nan"), "error": "x",
         "failed_at_utc": "2026-08-20T00:00:00Z"},
        {"question_id": "q3", "error_class": float("nan"), "error": "x",
         "failed_at_utc": "2026-08-20T00:00:00Z"},
    ])["integrity"]
    assert integ["error_census_malformed"] == {"<failure-class>": [None]}
    # str recoverable classes still ride the census as before (no regression)
    # and a missing error_class (full-context cell shape) still grades hard
    # without fabricating malformed evidence.
    integ = _report(outcomes, threshold=0.5, failures=[
        {"question_id": "q2", "error_class": "reader:retries_exhausted",
         "error": "x", "failed_at_utc": "2026-08-20T00:00:00Z"},
        {"question_id": "q3", "error": "boom",
         "failed_at_utc": "2026-08-20T00:00:00Z"},
    ])["integrity"]
    assert integ["error_census"] == {"reader:retries_exhausted": 1}
    assert "<failure-class>" not in integ["error_census_malformed"]
    assert integ["valid"] is False


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


def test_report_integrity_non_str_question_id_handling():
    """#1747 (security-review F4): non-dict list entries are excluded at
    entry (the report always builds — no AttributeError on a bare string);
    non-str question_ids are graded under a SENTINEL key so a hard census on
    a malformed-qid outcome still VETOES (no skip-launder), while an
    unhashable value can never crash the set/dict grades."""
    good = _outcome("q0", valid=True)
    bad = _outcome("q1", valid=True)      # non-str qid, clean census
    bad["question_id"] = ["not", "a", "string"]
    hard = _outcome("q2", valid=False)    # non-str qid, HARD census
    hard["question_id"] = 7
    hard["error_classes"] = {"fatal_402_billing": 1}
    integ = _report([good, bad, hard, "junk-string-entry"], failures=[
        {"question_id": 9, "error_class": "reader:fatal",
         "error": "x", "failed_at_utc": "2026-08-20T00:00:00Z"},
        "junk-failure-entry",
    ], threshold=0.5)["integrity"]
    # 2 str-absent outcomes + 1 str-absent failure: all three graded under
    # collision-proof sentinel keys (plus the str-qid "q0" — n_attempted == 4).
    assert integ["n_attempted"] == 4
    assert integ["n_hard_invalid"] == 2   # hard outcome + fatal failure veto
    assert integ["n_valid"] == 2          # good + clean-sentinel
    assert integ["valid"] is False        # hard veto fires
    assert integ["error_census"]["fatal_402_billing"] == 1


def test_report_integrity_non_dict_entries_excluded():
    """#1747 (security-review P1): a non-dict entry in outcomes/failures
    (malformed checkpoint JSON, e.g. a bare string) is excluded at entry —
    build_report always builds and serializes; n_attempted counts only the
    real question dicts."""
    integ = _report([_outcome("q0", valid=True), "boom", 5, None],
                    failures=["boom", 7, {"question_id": "f1",
                                           "error_class": "reader:fatal",
                                           "error": "x",
                                           "failed_at_utc": "x"}],
                    threshold=0.5)["integrity"]
    assert integ["n_attempted"] == 2      # q0 + the f1 failure
    assert integ["n_valid"] == 1
    assert integ["n_hard_invalid"] == 1   # reader:fatal vetoes
    assert integ["valid"] is False


def test_report_integrity_malformed_dict_outcome_dropped():
    """#1747 (security-review P1): a dict outcome missing the keys the
    aggregation dereferences directly (label / session_recall@k as a dict /
    context_tokens) is excluded at entry — a malformed checkpoint outcome
    that passes run.py's presence-only loader gate must not KeyError/
    AttributeError mid-report."""
    bad = _outcome("q0", valid=True)
    del bad["label"]
    integ = _report([bad, _outcome("q1", valid=True)], threshold=0.5)["integrity"]
    assert integ["n_attempted"] == 1
    assert integ["n_valid"] == 1
    assert integ["valid"] is True

    bad = _outcome("q0", valid=True)
    bad["session_recall@k"] = "not-a-dict"
    integ = _report([bad, _outcome("q1", valid=True)], threshold=0.5)["integrity"]
    assert integ["n_attempted"] == 1
    assert integ["valid"] is True

    bad = _outcome("q0", valid=True)
    bad["context_tokens"] = "abc"
    integ = _report([bad, _outcome("q1", valid=True)], threshold=0.5)["integrity"]
    assert integ["n_attempted"] == 1
    assert integ["valid"] is True
    # extended shape whitelist (security-review P1): non-numeric latency /
    # pool_size / ndcg, non-dict leg_mix, list question_type, non-numeric
    # recall values, None/missing context and recall dicts, None leg_mix
    # values, None ingest evidence keys, malformed rerank_pass fields — all
    # excluded, never crash.
    for mutate in (
            lambda o: o.__setitem__("retrieval_latency_ms", "slow"),
            lambda o: o.__setitem__("pool_size", "big"),
            lambda o: o.__setitem__("ndcg@10", "high"),
            lambda o: o.__setitem__("leg_mix", "oops"),
            lambda o: o.__setitem__("question_type", ["a", "b"]),
            lambda o: o["session_recall@k"].__setitem__("5", "oops"),
            lambda o: o.__setitem__("evidence_recall@k", "oops"),
            lambda o: o.__setitem__("ingest", {"evidence_turns": "many"}),
            lambda o: o.__setitem__("evidence_retrieved@k", {"5": "oops"}),
            # None/missing shapes (VGATE-pinned): o[...]-dereferenced keys
            lambda o: o.__setitem__("session_recall@k", None),
            lambda o: o.__setitem__("turn_recall@k", None),
            lambda o: o.__setitem__("context_tokens", None),
            lambda o: o.pop("context_tokens"),
            lambda o: o.pop("context_point_count"),
            lambda o: o.pop("question_id"),
            lambda o: o["session_recall@k"].__setitem__("5", None),
            # round-7 families: None leg_mix value, None ingest evidence
            lambda o: o.__setitem__("leg_mix", {"tfidf": None}),
            lambda o: o.__setitem__("ingest", {"evidence_turns": None,
                                               "evidence_points": 5}),
            lambda o: o.__setitem__("rerank_pass",
                                    {"applied": True,
                                     "max_session_chunks": "abc"}),
            lambda o: o.__setitem__("rerank_pass",
                                    {"applied": False,
                                     "degrade_reason": ["x"]}),
            lambda o: o.__setitem__("rerank_pass",
                                    {"applied": True,
                                     "pool_recall@k": {"session": 5.0}}),
            # round-8 families: bool-is-int admission (a tampered true must
            # never aggregate as 1.0), rerank_latency_ms crash family
            lambda o: o["session_recall@k"].__setitem__("5", True),
            lambda o: o["turn_recall@k"].__setitem__("5", False),
            lambda o: o.__setitem__("context_tokens", True),
            lambda o: o.__setitem__("pool_size", True),
            lambda o: o.__setitem__("rerank_latency_ms", "12ms"),
    ):
        o = _outcome("q0", valid=True)
        mutate(o)
        integ = _report([o, _outcome("q1", valid=True)],
                        threshold=0.5)["integrity"]
        assert integ["n_attempted"] == 1, f"shape not excluded: {o!r}"
        assert integ["valid"] is True
    # M6 N/A semantics are preserved: None TURN-recall values are LEGITIMATE
    # (dropped from the mean), so an outcome with them is NOT excluded.
    o = _outcome("q0", valid=True)
    o["turn_recall@k"] = {"5": None}
    integ = _report([o, _outcome("q1", valid=True)],
                    threshold=0.5)["integrity"]
    assert integ["n_attempted"] == 2
    assert integ["valid"] is True
    # n_excluded counts BOTH non-dict junk AND shape-broken dicts.
    bad = _outcome("q0", valid=True)
    bad["pool_size"] = "big"
    integ = _report([bad, _outcome("q1", valid=True), "junk", 42],
                    threshold=0.5)["integrity"]
    assert integ["n_excluded"] == 3
    assert integ["n_attempted"] == 1


def test_report_integrity_huge_int_magnitude_excluded_no_crash():
    """#1747 (round-17 code-review P1): json.loads produces arbitrary-
    precision ints, so a tampered/truncated checkpoint with a 309+-digit
    integer literal in ANY numeric field (pool_size / evidence_written /
    evidence_retrieved@k / session_recall@k / ndcg@10 / total_ms /
    ingest_latency_ms / rerank_pass.max_session_chunks) passes the type /
    bool / float-finiteness checks and then ``float(v)`` raises
    OverflowError mid-report — build_report aborts before any report is
    written and every resume crashes on the retained poisoned value. The
    magnitude bound mirrors the float-finiteness posture: abs(v) > 1e300 is
    EXCLUDED (counted in n_excluded), never converted, never crashed on
    (round-18: tightened from 1e308 to 1e300 so an n-way sum of accepted
    values stays finite — 10**308 passes the per-value check but two such
    outcomes sum past float max and _json_safe SILENTLY nulls the mean).
    answer_string_evidence_recall@k is covered too (round-18 gate review
    fix — previously an uncovered aggregation seam).
    """
    # a 400-digit int in pool_size: outcome excluded, report builds, no crash.
    bad = _outcome("q0", valid=True)
    bad["pool_size"] = 10 ** 400
    integ = _report([bad, _outcome("q1", valid=True)],
                    threshold=1.0)["integrity"]
    assert integ["n_excluded"] == 1
    assert integ["n_attempted"] == 1
    assert integ["n_excluded_hard"] == 0        # clean shape — no veto
    assert integ["valid"] is True
    # same guard on a latency field (the _lat aggregation's float() site).
    bad = _outcome("q0", valid=True)
    bad["ingest_latency_ms"] = 10 ** 400
    integ = _report([bad, _outcome("q1", valid=True)],
                    threshold=1.0)["integrity"]
    assert integ["n_excluded"] == 1
    assert integ["valid"] is True
    # negative magnitude is excluded too (abs() bound).
    bad = _outcome("q0", valid=True)
    bad["evidence_written"] = -(10 ** 400)
    integ = _report([bad, _outcome("q1", valid=True)],
                    threshold=1.0)["integrity"]
    assert integ["n_excluded"] == 1
    assert integ["valid"] is True
    # round-18 fix (gate review cycle 2): answer_string_evidence_recall@k was
    # NOT covered by the shape filter — a 400-digit int in it passed and
    # OverflowError'd the aggregation (build_report aborts; every resume
    # re-crashes on the retained poisoned value). Now excluded like the other
    # recall dicts.
    bad = _outcome("q0", valid=True)
    bad["answer_string_evidence_recall@k"] = {"5": 10 ** 400}
    integ = _report([bad, _outcome("q1", valid=True)],
                    threshold=1.0)["integrity"]
    assert integ["n_excluded"] == 1
    assert integ["n_attempted"] == 1
    assert integ["valid"] is True
    # ... and the published report carries NO answer_string_evidence_recall@k
    # aggregate under its REAL retrieval keys (the poisoned outcome was
    # excluded, nothing fabricated). NB: the cycle-2 assertion
    # ``report.get("answer_string_recall")`` checked a top-level key that
    # NEVER exists in any report (vacuously true regardless of behavior);
    # the published keys are retrieval.answer_string_evidence_recall@k /
    # retrieval.answer_string_evidence_recall_n@k (report.py ~1368) — both
    # present, null here, and NON-null iff the aggregate were fabricated.
    report = _report([bad, _outcome("q1", valid=True)], threshold=1.0)
    assert report["retrieval"]["answer_string_evidence_recall@k"] is None
    assert report["retrieval"]["answer_string_evidence_recall_n@k"] is None
    # a huge int co-occurring with a hard census class still VETOES via
    # n_excluded_hard (the hard-grade path is unchanged by the exclusion).
    bad = _outcome("q0", valid=True)
    bad["pool_size"] = 10 ** 400
    bad["error_classes"] = {"fatal_402_billing": 1}
    integ = _report([bad], threshold=1.0)["integrity"]
    assert integ["n_excluded"] == 1
    assert integ["n_excluded_hard"] == 1
    assert integ["valid"] is False
    # the ONLY outcome carrying a huge int → the entire attempted set is
    # excluded → the vacuity guard refuses to certify (valid=False), and
    # build_report still completes.
    bad = _outcome("q0", valid=True)
    bad["pool_size"] = 10 ** 400
    report = _report([bad], threshold=1.0)
    assert report["n_questions"] == 0
    assert report["integrity"]["valid"] is False
    # boundary (round-18 fix, gate review cycles 2/3): the magnitude bound is
    # tightened from 1e308 to 1e300 for SUM-safety — 10**308 passes the OLD
    # per-value check, but TWO such outcomes sum past float max
    # (2e308 > 1.797e308) to inf, which round() propagates and _json_safe
    # SILENTLY nulls (valid=True, n_excluded=0 — the PR's "never silent"
    # principle). 10**300 (the new edge) still aggregates, and any n-way sum
    # of accepted values stays finite.
    ok = _outcome("q0", valid=True)
    ok["pool_size"] = 10 ** 300
    integ = _report([ok], threshold=1.0)["integrity"]
    assert integ["n_excluded"] == 0
    assert integ["n_attempted"] == 1
    assert integ["valid"] is True
    # two outcomes at the OLD 1e308 bound edge: the old code admitted them
    # and summed 2e308 → inf → _json_safe SILENTLY nulled the mean
    # (valid=True, n_excluded=0). The new 1e300 bound EXCLUDES both —
    # n_excluded=2, empty attempted set → the vacuity guard fails closed
    # (valid=False, mean None — never a null mean with valid=True). This
    # block FAILS under the old bound, pinning the sum-safety behavior
    # change (the cycle-2 10**300 pair was finite under EITHER bound and
    # never executed the overflow path).
    a, b = _outcome("q0", valid=True), _outcome("q1", valid=True)
    a["pool_size"] = 10 ** 308
    b["pool_size"] = 10 ** 308
    report = _report([a, b], threshold=1.0)
    integ = report["integrity"]
    assert integ["n_excluded"] == 2
    assert integ["n_attempted"] == 0
    assert integ["valid"] is False
    assert report["pool_size"] == {}     # no aggregate for an empty attempted set
    # two outcomes at the NEW bound edge: the MEAN is finite (not null, not
    # inf) — sum-safety is what the tightened bound buys.
    a, b = _outcome("q0", valid=True), _outcome("q1", valid=True)
    a["pool_size"] = 10 ** 300
    b["pool_size"] = 10 ** 300
    report = _report([a, b], threshold=1.0)
    mean = report["pool_size"]["mean"]
    assert mean is not None and not math.isinf(mean) and not math.isnan(mean)
    assert report["integrity"]["n_excluded"] == 0
    assert report["integrity"]["valid"] is True
    # a value ABOVE the new bound is still excluded.
    bad = _outcome("q0", valid=True)
    bad["pool_size"] = 10 ** 301
    integ = _report([bad, _outcome("q1", valid=True)],
                    threshold=1.0)["integrity"]
    assert integ["n_excluded"] == 1
    assert integ["valid"] is True


def test_report_integrity_huge_float_magnitude_excluded_no_silent_null():
    """#1747 (round-18 gate-review cycle-3 P2): the cycle-2 magnitude bound
    was int-only — a finite float literal like 1.5e308 passed _numeric
    (finite, no int bound), and TWO such pool_size values summed past float
    max to inf, which round() propagated and _json_safe SILENTLY nulled the
    mean (n_excluded=0, valid=True — the exact silent-null failure the int
    tightening claims to eliminate). A 1.5e308 JSON literal is as easy to
    inject as a 309-digit int, and the same seam hits latencies,
    context_tokens, counts, recall values, leg_mix. The bound now applies
    to BOTH int and float magnitudes: abs(v) > 1e300 is excluded, never
    converted."""
    # a single huge float in pool_size: outcome excluded, report builds.
    bad = _outcome("q0", valid=True)
    bad["pool_size"] = 1.5e308
    integ = _report([bad, _outcome("q1", valid=True)],
                    threshold=1.0)["integrity"]
    assert integ["n_excluded"] == 1
    assert integ["n_attempted"] == 1
    assert integ["valid"] is True
    # a huge float in a latency field is excluded too (the _lat float() site).
    bad = _outcome("q0", valid=True)
    bad["ingest_latency_ms"] = 1.5e308
    integ = _report([bad, _outcome("q1", valid=True)],
                    threshold=1.0)["integrity"]
    assert integ["n_excluded"] == 1
    assert integ["valid"] is True
    # TWO huge floats at the old float-finite edge: both EXCLUDED — the old
    # code admitted them, summed 3e308 → inf, and silently nulled the mean
    # with valid=True, n_excluded=0. The empty attempted set now fails
    # closed (valid=False) — never a null mean with valid=True.
    a, b = _outcome("q0", valid=True), _outcome("q1", valid=True)
    a["pool_size"] = 1.5e308
    b["pool_size"] = 1.5e308
    report = _report([a, b], threshold=1.0)
    integ = report["integrity"]
    assert integ["n_excluded"] == 2
    assert integ["n_attempted"] == 0
    assert integ["valid"] is False
    assert report["pool_size"] == {}     # no aggregate for an empty attempted set
    # the float bound edge: 1e300 (equal to the bound — accepted; the
    # cycle-2 int-only bound had NO float arm, so the edge float must be
    # pinned separately).
    ok = _outcome("q0", valid=True)
    ok["pool_size"] = 1e300
    integ = _report([ok], threshold=1.0)["integrity"]
    assert integ["n_excluded"] == 0
    assert integ["n_attempted"] == 1
    assert integ["valid"] is True
    # and a float ABOVE the bound (1.5e300) is excluded.
    bad = _outcome("q0", valid=True)
    bad["pool_size"] = 1.5e300
    integ = _report([bad, _outcome("q1", valid=True)],
                    threshold=1.0)["integrity"]
    assert integ["n_excluded"] == 1
    assert integ["valid"] is True


def test_report_integrity_shape_broken_outcome_with_hard_census_vetoes():
    """#1747 (security-review P1, veto-escape): a shape-broken outcome (e.g.
    a truncated checkpoint that lost a recall/context key) carrying a HARD
    census class is excluded from the means AND the attempted set (its shape
    is untrusted — n_excluded surfaces the shrink), but its hard grade still
    VETOES the run: malformed shapes cannot launder a fatal class out of the
    gate. The census still records the fatal class as evidence."""
    o = _outcome("q0", valid=False)
    o["error_classes"] = {"fatal_402_billing": 1}
    del o["context_tokens"]
    integ = _report([o, _outcome("q1", valid=True)],
                    threshold=1.0)["integrity"]
    assert integ["n_excluded"] == 1
    assert integ["n_attempted"] == 1        # excluded from the attempted set
    assert integ["n_hard_invalid"] == 0     # ... yet the hard grade VETOES
    assert integ["n_excluded_hard"] == 1    # published so the veto is explainable
    assert integ["valid"] is False          # veto fires on the malformed outcome
    assert integ["error_census"]["fatal_402_billing"] == 1  # evidence kept


def test_report_integrity_all_excluded_never_certifies():
    """#1747 (round-8 security review): a run whose ENTIRE outcome set is
    shape-broken (a wholesale corrupt/version-drifted checkpoint) must never
    certify valid — n_attempted == 0 with n_excluded > 0 fails closed on the
    empty denominator (the vacuous-valid hole: invalid_rate 0.0 over zero
    graded questions). A truly EMPTY report (no outcomes, nothing excluded)
    stays vacuously valid (test_report_integrity_zero_outcomes)."""
    o = _outcome("q0", valid=False)
    o["error_classes"] = {"parse_error": 1}
    del o["context_tokens"]            # shape-broken → excluded
    integ = _report([o], threshold=1.0)["integrity"]
    assert integ["n_attempted"] == 0
    assert integ["n_excluded"] == 1
    assert integ["n_excluded_hard"] == 0
    assert integ["valid"] is False     # never certify an empty attempted set
    # the empty report stays vacuously valid (nothing was excluded).
    assert _report([])["integrity"]["valid"] is True


def test_report_integrity_breaker_open_hard_census_vetoes():
    """#1747 (round-8 security review): a breaker_open (vector-arm drop)
    outcome carrying a HARD census class still vetoes — the breaker flag
    means "no retrieval ran", never "errors absolved"; a tampered checkpoint
    cannot launder a fatal class under the dropped marker (n_excluded_hard)."""
    o = _outcome("q0", valid=False)
    o["breaker_open"] = True
    o["dropped_reason"] = "breaker_open"
    o["error_classes"] = {"fatal_402_billing": 1}
    integ = _report([o, _outcome("q1", valid=True)],
                    threshold=1.0)["integrity"]
    assert integ["n_excluded_hard"] == 1
    assert integ["valid"] is False
    assert integ["error_census"]["fatal_402_billing"] == 1
    # a clean breaker_open drop (no census) does NOT veto.
    o2 = _outcome("q2", valid=True)
    o2["breaker_open"] = True
    o2["dropped_reason"] = "breaker_open"
    integ = _report([o2, _outcome("q1", valid=True)],
                    threshold=1.0)["integrity"]
    assert integ["n_excluded_hard"] == 0
    assert integ["valid"] is True


def test_report_integrity_rerank_run_malformed_pool_recall_no_crash():
    """#1747 (round-7 finding 5): on a RERANK run (rerank_config set), a
    malformed rerank_pass.pool_recall@k — non-dict per-level values, a list,
    or a string (truncated checkpoint) — must be excluded, never crash the
    rerank aggregation (`cr.get(level).items()` on a float/list = the round-7
    AttributeError). A well-formed pool_recall@k still aggregates."""
    rerank_config = {"enabled": True, "model": "x", "lambda_": 0.7,
                     "per_session_cap": 2, "pool_size": 40, "prewarmed": True}
    for pr in ({"session": 5.0}, {"session": [1, 2]}, [1, 2], "oops"):
        o = _outcome("q0", valid=True)
        o["rerank_pass"] = {"applied": True, "pool_recall@k": pr}
        integ = build_report(
            [o, _outcome("q1", valid=True)],
            dataset_id="xiaowu0162/longmemeval-cleaned", split="s",
            reader_model="mock-reader", judge_model="mock-judge",
            extraction_approach="deterministic session ingestion",
            ingest_mode="deterministic", ks=(5,), top_k=5,
            dataset_semantics_audit=_audit(), integrity_threshold=0.5,
            rerank_config=rerank_config)["integrity"]
        assert integ["n_attempted"] == 1, f"shape not excluded: {pr!r}"
        assert integ["n_excluded"] == 1
        assert integ["valid"] is True
    # well-formed pool_recall@k on a rerank run still aggregates (no exclusion)
    o = _outcome("q0", valid=True)
    o["rerank_pass"] = {"applied": True,
                         "pool_recall@k": {"session": {"5": 1.0},
                                            "turn": {"5": 0.5}}}
    r = build_report(
        [o, _outcome("q1", valid=True)],
        dataset_id="xiaowu0162/longmemeval-cleaned", split="s",
        reader_model="mock-reader", judge_model="mock-judge",
        extraction_approach="deterministic session ingestion",
        ingest_mode="deterministic", ks=(5,), top_k=5,
        dataset_semantics_audit=_audit(), integrity_threshold=0.5,
        rerank_config=rerank_config)
    assert r["retrieval"]["rerank"]["pool_recall_mean@k"]["session"]["5"] == 1.0
    assert r["retrieval"]["rerank"]["pool_recall_mean@k"]["turn"]["5"] == 0.5


def test_report_integrity_falsy_error_classes_fail_closed():
    """#1747 (security review): a PRESENT but falsy error_classes value
    (0 / "" / False — malformed checkpoint JSON) fails CLOSED to hard like
    any other non-dict/non-list shape — only a MISSING key means
    "no census". A PRESENT null (JSON null — Python None) is also malformed
    and fails closed (round-10 review: get() conflated missing with
    present-null, certifying error_classes:null as clean)."""
    for bad in (0, "", False, None):
        o = _outcome("q0", valid=True)
        o["error_classes"] = bad  # bypass the helper's `or {}` collapse
        integ = _report([o], threshold=1.0)["integrity"]
        assert integ["n_hard_invalid"] == 1, f"{bad!r} not graded hard"
        assert integ["valid"] is False
    # a MISSING error_classes key still means "no census" (clean).
    o = _outcome("q0", valid=True)
    del o["error_classes"]
    integ = _report([o], threshold=1.0)["integrity"]
    assert integ["n_hard_invalid"] == 0
    assert integ["valid"] is True
    # round-15: the malformed TOP-LEVEL value is preserved as evidence under
    # the sentinel key (0 / "" / False / None / 5 / "abc") — "no malformed
    # evidence vanishes" holds for top-level shapes too, not just count
    # values; the grader still fails closed to hard.
    for bad in (0, "", False, None, 5, "abc"):
        o = _outcome("q0", valid=True)
        o["error_classes"] = bad
        integ = _report([o], threshold=1.0)["integrity"]
        assert integ["n_hard_invalid"] == 1
        assert integ["error_census_malformed"]["<malformed-top-level>"] == [bad]
    # distinct top-level shapes accumulate as distinct evidence.
    o1 = _outcome("q0", valid=True)
    o1["error_classes"] = 0
    o2 = _outcome("q1", valid=True)
    o2["error_classes"] = "abc"
    integ = _report([o1, o2], threshold=1.0)["integrity"]
    assert integ["error_census_malformed"]["<malformed-top-level>"] == [0, "abc"]
    # round-16: evidence membership is TYPE-EXACT — Python-equal but
    # JSON-DISTINCT tokens (0 vs False, 1 vs 1.0, True vs 1) stay distinct
    # records; no malformed evidence vanishes.
    o3 = _outcome("q2", valid=True)
    o3["error_classes"] = False
    o4 = _outcome("q3", valid=True)
    o4["error_classes"] = 1
    o5 = _outcome("q4", valid=True)
    o5["error_classes"] = 1.0
    o6 = _outcome("q5", valid=True)
    o6["error_classes"] = True
    integ = _report([o1, o2, o3, o4, o5, o6], threshold=1.0)["integrity"]
    assert integ["error_census_malformed"]["<malformed-top-level>"] == [
        0, "abc", False, 1, 1.0, True]
    # dict-count values: bool True and float 1.0 both ride the malformed
    # accumulator (int counts ride the typed census) and stay DISTINCT
    # evidence — Python-equal but JSON-distinct tokens never collapse.
    integ = _report([_outcome("q0", valid=False,
                              error_classes={"parse_error": True}),
                     _outcome("q1", valid=False,
                              error_classes={"parse_error": 1.0})],
                    threshold=1.0)["integrity"]
    assert integ["error_census_malformed"]["parse_error"] == [True, 1.0]
    assert integ["error_census"] == {}
    # legacy junk: bool True and int 1 stay distinct evidence.
    integ = _report([_outcome("q0", valid=False,
                              error_classes=[True]),
                     _outcome("q1", valid=False,
                              error_classes=[1])],
                    threshold=1.0)["integrity"]
    assert integ["error_census_malformed"]["<legacy-list>"] == [True, 1]


def test_report_integrity_all_breaker_open_never_certifies():
    """#1747 (round-10 security review): a run whose ENTIRE outcome set is
    breaker_open (a vector-arm outage that tripped every question — or a
    tampered checkpoint marking everything dropped) measures ZERO questions:
    n_attempted == 0 with dropped > 0 must fail closed, exactly like the
    all-excluded case (the breaker lane reopened the vacuous-valid hole the
    all-excluded floor closed). A clean drop mixed with a real attempt still
    rides the rate/veto criteria."""
    os_ = []
    for i in range(3):
        o = _outcome(f"q{i}", valid=True)
        o["breaker_open"] = True
        o["dropped_reason"] = "breaker_open"
        os_.append(o)
    integ = _report(os_, threshold=1.0)["integrity"]
    assert integ["n_attempted"] == 0
    assert integ["n_excluded"] == 0
    assert integ["valid"] is False       # zero measured questions, never valid
    # one real attempt + one clean drop → rides the rate/veto criteria.
    integ = _report([*os_, _outcome("real", valid=True)],
                    threshold=1.0)["integrity"]
    assert integ["n_attempted"] == 1
    assert integ["valid"] is True


def test_report_integrity_dropped_run_plus_failure_never_certifies():
    """#1747 (round-17 code-review P2): the vacuity guard keys off the
    OUTCOME-derived attempted set, NOT the failure-merged n_attempted — a
    run whose entire outcome set was breaker-dropped (a vector-arm outage
    that tripped every question) PLUS one recoverable failure entry
    (reader:retries_exhausted) used to report n_attempted=1,
    invalid_rate=1.0 ≤ threshold, n_hard_invalid=0, n_excluded_hard=0 →
    valid=True — the README's documented promise ('a run whose entire
    outcome set was excluded OR dropped never certifies') broken by the
    failure entry resurrecting the vacuous-valid hole."""
    drops = []
    for i in range(3):
        o = _outcome(f"q{i}", valid=True)
        o["breaker_open"] = True
        o["dropped_reason"] = "breaker_open"
        drops.append(o)
    integ = _report(drops, threshold=1.0, failures=[
        {"question_id": "f1", "error_class": "reader:retries_exhausted",
         "error": "x", "failed_at_utc": "x"},
    ])["integrity"]
    assert integ["n_attempted"] == 1        # the failure IS an attempt
    assert integ["n_recoverable_invalid"] == 1
    assert integ["invalid_rate"] == 1.0
    assert integ["n_excluded_hard"] == 0
    assert integ["valid"] is False          # outcome-derived set is empty
    # same hole via the excluded lane: all shape-broken + one recoverable
    # failure must not certify either.
    bad = _outcome("q0", valid=True)
    del bad["label"]
    integ = _report([bad], threshold=1.0, failures=[
        {"question_id": "f1", "error_class": "reader:retries_exhausted",
         "error": "x", "failed_at_utc": "x"},
    ])["integrity"]
    assert integ["n_excluded"] == 1
    assert integ["valid"] is False
    # a truly EMPTY report stays vacuously valid (nothing excluded/dropped).
    assert _report([])["integrity"]["valid"] is True


def test_report_integrity_nonfinite_and_duplicate_malformed_qids():
    """#1747 (round-10 security review): the attempted-set identity is
    canonical — duplicate copies of the same malformed-qid outcome dedupe
    (NaN qids canonicalized, unhashable list qids keyed by repr) so they can
    never inflate n_attempted and dilute invalid_rate; distinct unknown
    entries still count individually (reviewer-pinned per-object semantics
    for MISSING qids)."""
    # NaN qids: value-identical copies dedupe to ONE attempted question.
    o1 = _outcome("q0", valid=True)
    o1["question_id"] = float("nan")
    o2 = _outcome("q0", valid=True)
    o2["question_id"] = float("nan")
    integ = _report([o1, o2], threshold=1.0)["integrity"]
    assert integ["n_attempted"] == 1
    # unhashable list qids: value-identical copies dedupe too.
    o3 = _outcome("q0", valid=True)
    o3["question_id"] = ["a", "b"]
    o4 = _outcome("q0", valid=True)
    o4["question_id"] = ["a", "b"]
    integ = _report([o3, o4], threshold=1.0)["integrity"]
    assert integ["n_attempted"] == 1
    # int 1 and str "1" stay DISTINCT questions (collision-proof keys).
    o5 = _outcome("q0", valid=True)
    o5["question_id"] = 1
    o6 = _outcome("q0", valid=True)
    o6["question_id"] = "1"
    integ = _report([o5, o6], threshold=1.0)["integrity"]
    assert integ["n_attempted"] == 2
    # round-14: bool true vs int 1 vs float 1.0 are DISTINCT JSON tokens —
    # the type-tagged key keeps them separate (Python == would merge them).
    ob_, oi, of = (_outcome("q0", valid=True) for _ in range(3))
    ob_["question_id"] = True
    oi["question_id"] = 1
    of["question_id"] = 1.0
    integ = _report([ob_, oi, of], threshold=1.0)["integrity"]
    assert integ["n_attempted"] == 3
    # distinct MISSING-qid failures stay distinct (pinned per-object).
    integ = _report([_outcome("q0", valid=True)], failures=[
        {"error_class": "reader:retries_exhausted", "error": "x",
         "failed_at_utc": "x"},
        {"error_class": "reader:retries_exhausted", "error": "y",
         "failed_at_utc": "y"},
    ], threshold=0.5)["integrity"]
    assert integ["n_attempted"] == 3


def test_report_integrity_missing_qid_failures_not_merged():
    """#1747 (reviewer-pinned): failure entries with NO question_id are
    distinct unknown questions — per-object keys, never merged into one
    undercounted entry."""
    integ = _report([_outcome(f"q{i}", valid=True) for i in range(49)],
                    failures=[
                        {"error_class": "reader:retries_exhausted",
                         "error": "x", "failed_at_utc": "x"},
                        {"error_class": "reader:retries_exhausted",
                         "error": "y", "failed_at_utc": "y"},
                    ], threshold=0.02)["integrity"]
    assert integ["n_attempted"] == 51
    assert integ["n_recoverable_invalid"] == 2
    assert integ["invalid_rate"] == round(2 / 51, 4)
    assert integ["valid"] is False   # 0.039 > 0.02 — no undercount flip


def test_report_integrity_sentinel_qid_collision_impossible():
    """#1747 (security review): a crafted string question_id like
    "<anon:0>" cannot collide with a malformed-qid sentinel key (tuple
    keys) to overwrite a hard grade — and duplicate-qid collisions merge by
    MAX severity, so a hard grade is never replaced by a weaker one."""
    # non-str-qid hard outcome + crafted clean qid that looks like a sentinel
    o1 = _outcome("q0", valid=False)
    o1["question_id"] = ["x"]
    o1["error_classes"] = {"fatal_402_billing": 1}
    o2 = _outcome("<anon:0>", valid=True)
    integ = _report([o1, o2], threshold=1.0)["integrity"]
    assert integ["n_hard_invalid"] == 1
    assert integ["valid"] is False
    # duplicate str qids merge by max severity (hard wins over clean)
    o3 = _outcome("dup", valid=True)
    o4 = _outcome("dup", valid=False)
    o4["error_classes"] = {"fatal_402_billing": 1}
    integ = _report([o3, o4], threshold=1.0)["integrity"]
    assert integ["n_attempted"] == 1
    assert integ["n_hard_invalid"] == 1
    assert integ["valid"] is False
    # duplicate malformed-qid FAILURES dedupe by value (no double count)
    integ = _report([_outcome("q0", valid=True)], failures=[
        {"question_id": 9, "error_class": "reader:fatal",
         "error": "x", "failed_at_utc": "x"},
        {"question_id": 9, "error_class": "reader:fatal",
         "error": "x", "failed_at_utc": "x"},
    ], threshold=0.5)["integrity"]
    assert integ["n_attempted"] == 2   # q0 + one deduped malformed failure
    assert integ["n_hard_invalid"] == 1


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
    assert "n_excluded_hard == 0" in gate       # round-8 veto-escape term
    assert f"{JUSTIFIED_BASELINE_THRESHOLD}" in gate
    # the allowance math is pinned (0.02 × 500 = 10 questions — a regression
    # to percentage formatting fails here).
    assert "≤10 of 500 questions" in gate
    # round-8/10 clauses: excluded outcomes still veto; falsy error_classes
    # fail closed; a fully excluded/dropped run never certifies.
    assert "falsy-but-present" in gate
    assert "breaker_open" in gate
    assert "fully excluded/dropped run never certifies" in gate
    assert "PRESENT-null" in gate

    # the executed step-5 command injects the justified threshold AND its
    # recorded justification (M7: a non-default threshold is never silently
    # applied) — the run matches the documented gate.
    state = ProtocolState(Path(tempfile.mkdtemp()) / "state.json")
    cmd = build_command(STEPS_BY_NUMBER[5], [], state=state)
    assert "--integrity-threshold" in cmd
    assert f"{JUSTIFIED_BASELINE_THRESHOLD}" in cmd
    assert "--integrity-justification" in cmd
    assert "#1747 justified" in " ".join(cmd)
    # round-13: the justification INTERPOLATES the constant (no hardcoded
    # "0.02" drift) and an OPERATOR OVERRIDE of the threshold suppresses the
    # baseline justification — a recorded reason never claims the 0.02
    # baseline for a non-baseline threshold (M7: the report records the
    # ACTUAL reason).
    cmd = build_command(STEPS_BY_NUMBER[5], [], state=state)
    assert (f"step-5 baseline: #1747 justified "
            f"{JUSTIFIED_BASELINE_THRESHOLD} default at 500-Q scale"
            in " ".join(cmd))
    overridden = build_command(STEPS_BY_NUMBER[5],
                               ["--integrity-threshold", "0.5"], state=state)
    assert "--integrity-justification" not in overridden
    assert "--integrity-threshold" in overridden       # operator flag passes
    assert "0.5" in overridden
    # round-14: the `=` (argparse) form is detected too — same suppression,
    # so the recorded reason never claims the 0.02 baseline for 0.5.
    overridden_eq = build_command(STEPS_BY_NUMBER[5],
                                  ["--integrity-threshold=0.5"], state=state)
    assert "--integrity-justification" not in overridden_eq
    assert "--integrity-threshold=0.5" in overridden_eq
    # round-15: argparse prefix abbreviations (--integrity-thres /
    # --integrity-t) are accepted by the runner — detected too, no injected
    # justification (the recorded reason never claims the 0.02 baseline for
    # a non-baseline threshold).
    overridden_abbr = build_command(STEPS_BY_NUMBER[5],
                                    ["--integrity-thres=0.5"], state=state)
    assert "--integrity-justification" not in overridden_abbr
    assert "--integrity-thres=0.5" in overridden_abbr
    # round-16: a quoted justification VALUE whose text merely CONTAINS the
    # flag token is NOT a threshold override (argparse consumes a non-option
    # value token as --integrity-justification's value) — the baseline
    # injection stays.
    quoted = build_command(STEPS_BY_NUMBER[5],
                           ["--integrity-justification",
                            "doc: see --integrity-threshold=0.02 in ticket X"],
                           state=state)
    assert "--integrity-justification" in quoted
    assert "step-5 baseline: #1747 justified" in " ".join(quoted)
    # round-17 (code review): the detector registers --integrity-justification
    # (same single-value store the runner uses), so an OPTION-LOOKING
    # justification value token (starting with ``--``) can never be misread
    # as a REAL threshold override — it raises 'expected one argument' in
    # BOTH parsers → no-override → the baseline injection stays. The OLD
    # threshold-only parser parsed the single-token form below as a genuine
    # 0.02 override, suppressed the baseline injection, and the emitted
    # command applied the strict 0.0 default while recording the token as
    # the justification — the M7 'recorded reason never claims a threshold
    # that wasn't applied' contract violated (and #1747's valid=true
    # unreachable at 500-Q scale silently recurs).
    single_tok = build_command(STEPS_BY_NUMBER[5],
                               ["--integrity-justification",
                                "--integrity-threshold=0.02"],
                               state=state)
    assert "--integrity-threshold" in single_tok          # baseline injected
    assert "step-5 baseline: #1747 justified" in " ".join(single_tok)
    # round-17: the pinned scenarios' EMITTED commands must actually parse
    # under the RUNNER's parser (run.py _build_parser — the detector's
    # parse semantics must never diverge from the runner's): space/equals/
    # abbreviation overrides + a well-formed justification all round-trip;
    # the malformed single-token justification is the RUNNER's loud
    # rejection (SystemExit 'expected one argument'), never a silently-
    # wrong threshold.
    from tools.longmem_eval.run import _build_parser as runner_parser

    def _runner_argv(cmd):
        # drop the [sys.executable, "-m", "tools.longmem_eval.run"] head.
        return cmd[3:]

    rp = runner_parser()
    overridden = build_command(STEPS_BY_NUMBER[5],
                               ["--integrity-threshold", "0.5"], state=state)
    ns = rp.parse_args(_runner_argv(overridden))
    assert ns.integrity_threshold == 0.5
    overridden_eq = build_command(STEPS_BY_NUMBER[5],
                                  ["--integrity-threshold=0.5"], state=state)
    assert rp.parse_args(_runner_argv(overridden_eq)).integrity_threshold == 0.5
    overridden_abbr = build_command(STEPS_BY_NUMBER[5],
                                    ["--integrity-thres=0.5"], state=state)
    assert rp.parse_args(_runner_argv(overridden_abbr)).integrity_threshold == 0.5
    ns = rp.parse_args(_runner_argv(quoted))
    assert ns.integrity_threshold == 0.02                 # baseline survives
    assert "doc: see --integrity-threshold=0.02" in ns.integrity_justification
    import pytest
    with pytest.raises(SystemExit):
        rp.parse_args(_runner_argv(single_tok))  # runner rejects, loudly


def test_report_retrieval_emits_reader_surface():
    """#1948: the retrieval block emits reader_surface@k — evidence-bearing
    content (points AND chunks) in the reader's FULL context / evidence-
    bearing content total — aggregated parallel to reader_evidence@k; N/A
    per-outcome values are dropped from the mean; outcomes without the key
    leave the aggregate absent (never fabricated from the ingest census)."""
    from tools.longmem_eval.dataset_audit import TRUSTED_VERDICT
    trusted_audit = {
        "verdict": TRUSTED_VERDICT,
        "n": 3,
        "fields": {"answer_session_ids": "present", "answer_turn": "absent",
                   "has_answer": "present"},
        "violations": 0,
    }
    outcomes = [_outcome("q-rs-1"), _outcome("q-rs-2"), _outcome("q-rs-3")]
    outcomes[0]["reader_surface@k"] = {"5": 1.0}
    outcomes[0]["reader_evidence@k"] = {"5": 0.5}
    outcomes[1]["reader_surface@k"] = {"5": 0.5}
    outcomes[2]["reader_surface@k"] = {"5": None}  # N/A dropped from mean
    report = build_report(
        outcomes, dataset_id="xiaowu0162/longmemeval-cleaned", split="s",
        reader_model="mock-reader", judge_model="mock-judge",
        extraction_approach="deterministic session ingestion",
        ingest_mode="deterministic", ks=(5,), top_k=5,
        dataset_semantics_audit=trusted_audit,
    )
    r = report["retrieval"]
    assert r["reader_surface@k"] == {"5": 0.75}
    assert r["reader_surface_n@k"] == {"5": 2}
    # the points-only reader-surface view stays independently emitted
    assert r["reader_evidence@k"] == {"5": 0.5}
    assert r["reader_evidence_n@k"] == {"5": 1}
    # methodology records the metric semantics (truthful labels)
    assert "reader_surface@k" in report["methodology"]["recall_definition"]
