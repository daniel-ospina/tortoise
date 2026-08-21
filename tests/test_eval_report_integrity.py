"""Report integrity-block tests (M4 #1524, D5).

The additive ``integrity`` block (valid / invalid_rate / n_valid / n_invalid /
n_failed / threshold / error_census) is emitted by ``build_report``, printed
BEFORE the score in ``_print_summary``, gated by the ``--integrity-threshold``
CLI flag, and never blocks publication (scope: retry-then-fix, not
refuse-to-publish). E2E-2's offline analog: a mini pipeline with a flaky
extractor model reports its integrity on the same block.
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
    """Mixed valid flags → correct valid / invalid_rate / census / counts."""
    outcomes = [
        _outcome("q1", valid=True,
                 error_classes={"transient_429_rate_limit": 2}),
        _outcome("q2", valid=False,
                 error_classes={"fatal_402_billing": 1, "parse_error": 1}),
    ]
    integ = _report(outcomes)["integrity"]
    assert integ["valid"] is False           # 1/2 invalid > threshold 0.0
    assert integ["invalid_rate"] == 0.5
    assert integ["n_attempted"] == 2
    assert integ["n_valid"] == 1
    assert integ["n_invalid"] == 1
    assert integ["n_failed"] == 0
    assert integ["threshold"] == 0.0
    assert integ["error_census"] == {
        "transient_429_rate_limit": 2,
        "fatal_402_billing": 1,
        "parse_error": 1,
    }


def test_report_integrity_zero_outcomes():
    integ = _report([])["integrity"]
    assert integ["valid"] is True
    assert integ["invalid_rate"] == 0.0
    assert integ["n_attempted"] == 0
    assert integ["n_valid"] == 0
    assert integ["n_invalid"] == 0
    assert integ["n_failed"] == 0
    assert integ["error_census"] == {}


def test_report_integrity_threshold():
    """invalid_rate 0.1: threshold 0.05 → valid=false; 0.2 → valid=true."""
    outcomes = [_outcome(f"q{i}", valid=True) for i in range(9)]
    outcomes.append(_outcome("q9", valid=False,
                             error_classes={"parse_error": 1}))
    assert _report(outcomes, threshold=0.05)["integrity"]["valid"] is False
    assert _report(outcomes, threshold=0.2)["integrity"]["valid"] is True


def test_report_integrity_failed_questions_cross_ref():
    """Question-level failures (reader/judge) are orthogonal to extraction
    integrity: they count into n_failed/n_invalid and their site-prefixed
    eval class rides the census (M7 semantics preserved)."""
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
