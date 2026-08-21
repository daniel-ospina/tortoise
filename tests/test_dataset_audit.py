"""Dataset recall-semantics audit tests (M7 #1527, E2E-3 Precondition 2).

The audit's publication gate: no turn_recall/evidence_recall number leaves
the harness without the dataset recall-semantics audit record
(``build_report`` requires it; a not-trusted verdict serializes recall to
null). The mini fixture doubles as a fixture-consistency check; the @slow
test pins the real cleaned-S-split census (measured 2026-08-20) verbatim.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval.dataset_audit import (  # noqa: E402, RUF100
    BASELINE,
    NOT_TRUSTED_VERDICT,
    TRUSTED_VERDICT,
    audit_dataset,
    semantics_baseline,
)
from tools.longmem_eval.report import build_report  # noqa: E402, RUF100

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"


def _mini() -> list[dict]:
    return json.loads(MINI.read_text(encoding="utf-8"))


def _trusted_audit() -> dict:
    return audit_dataset([{
        "question_id": "q-audit",
        "haystack_session_ids": ["s0"],
        "answer_session_ids": ["s0"],
        "haystack_sessions": [[
            {"role": "user", "content": "x", "has_answer": True}]],
    }])


def _outcome(qid: str, *, sr: float = 1.0, tr: float = 1.0) -> dict:
    return {
        "question_id": qid,
        "question_type": "single-session-user",
        "question_date": "2024-01-15",
        "label": True,
        "hypothesis": "h",
        "session_recall@k": {"5": sr, "10": sr, "20": sr},
        "turn_recall@k": {"5": tr, "10": tr, "20": tr},
        "evidence_recall@k": {"5": tr, "10": tr, "20": tr},
        "chunk_evidence_recall@k": {"5": 0.5, "10": 0.5, "20": 0.5},
        "n_ingest_errors": 0,
        "context_tokens": 100,
        "context_point_count": 3,
    }


def test_audit_on_mini():
    """D10 (M7 #1527, surface 27): the mini fixture audits to the recorded
    semantics — 5 instances, 4 with has_answer turns, 1 abstention
    evidence-absent, 0 answer_turn fields, answer_session_ids ⊆
    haystack_session_ids on all non-empty, and the fixture-vs-real
    divergence (empty answer_session_ids on mini_abs_005_abs) is recorded.
    Verdict stays trusted (the divergence is informational — the mini is a
    pipeline smoke, not a metric source)."""
    aud = audit_dataset(_mini())
    assert aud["n_instances"] == 5
    assert aud["fields"] == {
        "answer_session_ids": "present",
        "answer_turn": "absent",
        "has_answer": "sparse-present",
    }
    assert aud["coverage"]["with_answer_session_ids"] == 4  # 4 non-empty
    assert aud["coverage"]["with_has_answer_turns"] == 4
    assert aud["coverage"]["with_answer_turn_field"] == 0
    assert aud["consistency"]["answer_session_ids_subset_haystack"] == 4
    assert aud["consistency"]["has_answer_sessions_subset_answer_session_ids"] \
        == 4
    assert aud["consistency"]["violations"] == 0
    assert aud["abstentions"]["n"] == 1
    assert aud["abstentions"]["evidence_absent"] == 1
    assert aud["abstentions"]["empty_answer_session_ids"] == 1  # mini_abs
    assert aud["verdict"] == TRUSTED_VERDICT
    assert any("mini_abs_005_abs" in d for d in aud["fixture_divergences"])
    assert len(aud["paper_divergences"]) == 4
    assert aud["gate"]


def test_build_report_requires_audit():
    """Gate 2 (M7 #1527): build_report raises ValueError without the audit
    record — there is no opt-out flag (E2E-3 Precondition 2)."""
    with pytest.raises(ValueError, match="dataset_semantics_audit"):
        build_report([], dataset_id="d", split="s", reader_model="r",
                     judge_model="j", extraction_approach="x",
                     ks=(5,), top_k=20)


def test_paper_aligned_aggregates_exclude_abs():
    """D10 (M7 #1527): _paper@k aggregates exclude _abs questions (the
    official print_retrieval_metrics.py exclusion); legacy keys keep the
    _abs-inclusive definition."""
    outcomes = [
        _outcome("q1", sr=1.0, tr=1.0),
        _outcome("q1_abs", sr=0.0, tr=0.0),  # abstention — recall 0 legacy
    ]
    report = build_report(outcomes, dataset_id="d", split="s",
                          reader_model="r", judge_model="j",
                          extraction_approach="x", ks=(5, 10, 20), top_k=20,
                          dataset_semantics_audit=_trusted_audit())
    ret = report["retrieval"]
    # legacy includes the _abs question (0.5 mean)
    assert ret["session_recall@k"]["5"] == 0.5
    assert ret["turn_recall@k"]["5"] == 0.5
    # paper-aligned excludes it (1.0 mean)
    assert ret["session_recall_paper@k"]["5"] == 1.0
    assert ret["turn_recall_paper@k"]["5"] == 1.0
    assert ret["evidence_recall_paper@k"]["5"] == 1.0


def test_not_trusted_serializes_recall_to_null():
    """Gate 2 (M7 #1527): a not-trusted audit (structural divergence — e.g.
    a dataset refresh adds answer_turn) serializes EVERY recall key to null:
    the report contains no recall numbers until re-audited."""
    bad_instances = [*_mini(), {
        "question_id": "refresh_q",
        "question_type": "single-session-user",
        "question": "q", "answer": "a",
        "answer_turn": "x",  # the refresh added the paper's answer_turn field
        "haystack_session_ids": ["s0"],
        "answer_session_ids": ["s0"],
        "haystack_sessions": [[
            {"role": "user", "content": "x", "has_answer": True}]],
    }]
    aud = audit_dataset(bad_instances)
    assert aud["fields"]["answer_turn"] == "present"
    assert aud["verdict"] == NOT_TRUSTED_VERDICT
    report = build_report(
        [_outcome("q1")], dataset_id="d", split="s", reader_model="r",
        judge_model="j", extraction_approach="x", ks=(5,), top_k=20,
        dataset_semantics_audit=aud)
    ret = report["retrieval"]
    assert ret["session_recall@k"] is None
    assert ret["turn_recall@k"] is None
    assert ret["evidence_recall@k"] is None
    assert ret["session_recall_paper@k"] is None
    assert ret["turn_recall_paper@k"] is None
    # accuracy is NOT a recall number — it stays published
    assert report["accuracy"]["overall"] == 1.0
    assert report["methodology"]["dataset_semantics_audit"]["verdict"] == \
        NOT_TRUSTED_VERDICT


@pytest.mark.slow
def test_audit_on_real_s_split():
    """D10 (M7 #1527): the cached cleaned S split audits to the measured
    2026-08-20 baseline verbatim (the census the plan's D11 table pins).
    Skipped when the dataset is not cached (CI has no dataset)."""
    cache = (Path.home() / ".cache" / "tortoise-longmemeval"
             / "longmemeval_s_cleaned.json")
    if not cache.is_file():
        pytest.skip("real S split not cached (CI)")
    instances = json.loads(cache.read_text(encoding="utf-8"))
    aud = audit_dataset(instances)
    b = semantics_baseline()
    assert aud["n_instances"] == b["n_instances"] == 500
    for key in ("fields", "coverage", "consistency", "has_answer_roles",
                "abstentions", "turn_marking"):
        assert aud[key] == b[key], key
    assert aud["verdict"] == TRUSTED_VERDICT
    assert aud["fixture_divergences"] == []  # real data has none
    # the pinned baseline numbers themselves (D11 census table)
    assert aud["coverage"] == {
        "with_answer_session_ids": 500,
        "with_has_answer_turns": 479,
        "with_answer_turn_field": 0,
    }
    assert aud["consistency"]["violations"] == 0
    assert aud["has_answer_roles"] == {"user": 842, "assistant": 54}
    assert aud["abstentions"] == {
        "n": 30, "with_has_answer": 9, "evidence_absent": 21,
        "empty_answer_session_ids": 0}
    assert aud["turn_marking"] == {
        "total_turns": 246750, "with_has_answer_key": 10960, "marked_true": 896}
    assert aud["fields"]["answer_turn"] == "absent"
    assert BASELINE["findings_date"] == "2026-08-20"
