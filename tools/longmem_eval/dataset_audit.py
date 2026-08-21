"""Dataset recall-semantics audit (issue #1527, M7 — E2E-3 Precondition 2).

LongMemEval's official retrieval metrics exclude ``_abs`` questions from
retrieval aggregates, build the turn corpus from ``role == 'user'`` turns
only, and are binary (recall_any / recall_all / ndcg) — our harness
aggregates a per-question *fraction* over ALL questions including ``_abs``.
Before any ``turn_recall`` / ``evidence_recall`` number is published, this
module audits the loaded dataset instance schema / coverage / consistency
against the recorded semantics (measured 2026-08-20 on the cleaned S split)
and stamps the report methodology with the record.

Publication gate (enforced by construction in ``report.build_report``): a
report containing recall numbers provably contains the audit record
(``build_report`` raises ``ValueError`` without it). If the live census
diverges structurally (field presence/consistency — e.g. a HF dataset
refresh adds ``answer_turn`` or breaks a subset invariant), the verdict
flips to ``not-trusted`` and the recall keys serialize to ``null`` until the
dataset is re-audited.
"""
from __future__ import annotations

from typing import Any

#: The 2026-08-20 measured census of the cleaned S split — the audit's
#: expected-baseline (pinned by the @slow test against the cached dataset).
BASELINE: dict[str, Any] = {
    "findings_date": "2026-08-20",
    "n_instances": 500,
    "fields": {
        "answer_session_ids": "present",
        "answer_turn": "absent",
        "has_answer": "sparse-present",
    },
    "coverage": {
        "with_answer_session_ids": 500,
        "with_has_answer_turns": 479,
        "with_answer_turn_field": 0,
    },
    "consistency": {
        "answer_session_ids_subset_haystack": 500,
        "has_answer_sessions_subset_answer_session_ids": 479,
        "violations": 0,
    },
    "has_answer_roles": {"user": 842, "assistant": 54},
    "abstentions": {"n": 30, "with_has_answer": 9, "evidence_absent": 21,
                    "empty_answer_session_ids": 0},
    "turn_marking": {"total_turns": 246750, "with_has_answer_key": 10960,
                     "marked_true": 896},
}

#: Recorded paper divergences (verbatim in every audit record — the report
#: always states which semantics its numbers stand on).
PAPER_DIVERGENCES = [
    "official print_retrieval_metrics.py excludes _abs questions from "
    "retrieval aggregates — legacy aggregates include them (paper-aligned "
    "_paper@k keys added)",
    "official turn corpus indexes role=='user' turns only — assistant-role "
    "evidence turns are out-of-corpus; legacy turn_recall includes them",
    "official metrics are binary recall_any/recall_all + ndcg; ours is a "
    "per-question fraction (documented variant)",
    "official code asserts has_answer on every user turn; the cleaned split "
    "marks it sparsely",
]

GATE_TEXT = ("no turn_recall/evidence_recall number is published unless this "
             "record is present in the report methodology")

TRUSTED_VERDICT = "trusted-as-documented-variant"
NOT_TRUSTED_VERDICT = "not-trusted"


def semantics_baseline() -> dict[str, Any]:
    """The recorded 2026-08-20 expected values (test pin)."""
    return dict(BASELINE)


def audit_dataset(instances: list[dict]) -> dict[str, Any]:
    """Census + consistency + divergences + verdict for one loaded dataset.

    Structural invariants (field presence + subset consistency) drive the
    verdict — instance counts may legitimately vary (a ``limit`` run), but a
    dataset whose schema contradicts the recorded semantics is NOT trusted.
    """
    n = len(instances)

    # ── coverage ──
    # Field PRESENCE (the schema invariant driving the verdict) vs non-empty
    # coverage (the baseline census) are tracked separately: the committed
    # MINI fixture's abstention has an EMPTY answer_session_ids list — the
    # field is present, the real data has none empty (recorded divergence).
    with_answer_session_ids_field = sum(
        1 for q in instances if isinstance(q.get("answer_session_ids"), list))
    with_answer_session_ids = sum(
        1 for q in instances if isinstance(q.get("answer_session_ids"), list)
        and len(q.get("answer_session_ids") or []))
    with_answer_turn_field = sum(1 for q in instances if "answer_turn" in q)
    with_has_answer_turns = sum(
        1 for q in instances
        if any(t.get("has_answer")
               for s in q.get("haystack_sessions") or []
               for t in s))

    # ── consistency: answer_session_ids ⊆ haystack_session_ids; and
    # has_answer-turn sessions ⊆ answer_session_ids (no orphan evidence) ──
    ans_subset = 0
    has_ans_subset = 0
    violations = 0
    for q in instances:
        hay = set(q.get("haystack_session_ids") or [])
        ans = set(q.get("answer_session_ids") or [])
        if ans:
            if ans <= hay:
                ans_subset += 1
            else:
                violations += 1
        has_ans_sessions: set[str] = set()
        for si, sess in enumerate(q.get("haystack_sessions") or []):
            if any(t.get("has_answer") for t in sess):
                hay_ids = q.get("haystack_session_ids") or []
                has_ans_sessions.add(hay_ids[si] if si < len(hay_ids)
                                     else f"<idx{si}>")
        if has_ans_sessions:
            if has_ans_sessions <= ans:
                has_ans_subset += 1
            else:
                violations += 1

    # ── has_answer turn roles + turn-marking sparsity ──
    user_marks = assistant_marks = 0
    total_turns = 0
    turns_with_key = 0
    marked_true = 0
    for q in instances:
        for sess in q.get("haystack_sessions") or []:
            for t in sess:
                total_turns += 1
                if "has_answer" in t:
                    turns_with_key += 1
                if t.get("has_answer"):
                    marked_true += 1
                    if t.get("role") == "assistant":
                        assistant_marks += 1
                    else:
                        user_marks += 1

    # ── abstentions (_abs qids) ──
    abs_qids = [q for q in instances if "_abs" in q.get("question_id", "")]
    abs_with_has = sum(
        1 for q in abs_qids
        if any(t.get("has_answer")
               for s in q.get("haystack_sessions") or []
               for t in s))
    abs_empty_ans = sum(1 for q in abs_qids
                        if not (q.get("answer_session_ids") or []))

    if n:
        ans_field = ("present" if with_answer_session_ids_field == n
                     else "absent")
        turn_field = "present" if with_answer_turn_field else "absent"
        if with_has_answer_turns == 0:
            has_field = "absent"
        elif with_has_answer_turns < n:
            has_field = "sparse-present"
        else:
            has_field = "present"
    else:
        ans_field = turn_field = has_field = "unknown"

    fields = {
        "answer_session_ids": ans_field,
        "answer_turn": turn_field,
        "has_answer": has_field,
    }

    # ── verdict: structural invariants only (counts may legitimately vary) ──
    trusted = (
        n > 0
        and ans_field == "present"
        and turn_field == "absent"
        and has_field in ("present", "sparse-present")
        and violations == 0
    )

    # ── fixture divergences (informational — the committed MINI fixture is a
    # pipeline smoke, not a metric source; real data has none of these) ──
    fixture_divergences: list[str] = []
    empty_ans_qids = [q.get("question_id")
                      for q in instances
                      if isinstance(q.get("answer_session_ids"), list)
                      and not q.get("answer_session_ids")]
    if empty_ans_qids:
        fixture_divergences.append(
            "instances with EMPTY answer_session_ids (real data has none): "
            + ", ".join(str(x) for x in sorted(empty_ans_qids)))

    return {
        "findings_date": BASELINE["findings_date"],
        "n_instances": n,
        "fields": fields,
        "coverage": {
            "with_answer_session_ids": with_answer_session_ids,
            "with_has_answer_turns": with_has_answer_turns,
            "with_answer_turn_field": with_answer_turn_field,
        },
        "consistency": {
            "answer_session_ids_subset_haystack": ans_subset,
            "has_answer_sessions_subset_answer_session_ids": has_ans_subset,
            "violations": violations,
        },
        "has_answer_roles": {"user": user_marks, "assistant": assistant_marks},
        "abstentions": {
            "n": len(abs_qids),
            "with_has_answer": abs_with_has,
            "evidence_absent": len(abs_qids) - abs_with_has,
            "empty_answer_session_ids": abs_empty_ans,
        },
        "turn_marking": {
            "total_turns": total_turns,
            "with_has_answer_key": turns_with_key,
            "marked_true": marked_true,
        },
        "paper_divergences": list(PAPER_DIVERGENCES),
        "fixture_divergences": fixture_divergences,
        "verdict": TRUSTED_VERDICT if trusted else NOT_TRUSTED_VERDICT,
        "gate": GATE_TEXT,
    }


def is_trusted(audit: dict[str, Any] | None) -> bool:
    """True when the audit record licenses publishing recall numbers."""
    return bool(audit) and audit.get("verdict") == TRUSTED_VERDICT
