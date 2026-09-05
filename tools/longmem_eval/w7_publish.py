"""W7 sealed-run publication layer — official metrics + labeled variants +
sealed keys + validated receipt (issue #2105, epic #2080 DM-11 / §6.6 / E2E-8).

The LongMemEval *runner* produces the run report (``run.py`` → report JSON
with per-outcome ``session_recall@k`` fractions + the dataset recall-
semantics audit record). This module is the publication layer WIRED AROUND
the runner: it derives the OFFICIAL retrieval metric and the labeled
divergence variants from the report, seals the answer keys at the boundary,
and builds/validates the receipt (run_status / verdict / failure_origin /
exact commit / corpus hash / judge pin / cost_usd) per plan §6.6 and the
W2/W3 receipt discipline (``tests/eval/harness/runner.py`` receipt schema,
same key vocabulary).

Official semantics (asserted against the official evaluator source — see
the tests): the official ``print_retrieval_metrics.py`` filters ``_abs``
questions and averages per-question BINARY metrics where ``evaluate_retrieval``
(``src/retrieval/eval_utils.py``) defines

    recalled_docs = corpus docs in rankings[:k]
    recall_any@k  = any(doc in recalled_docs for doc in correct_docs)
    recall_all@k  = all(doc in recalled_docs for doc in correct_docs)

The harness's per-outcome ``session_recall@k`` is the exact per-question
fraction ``|correct ∩ top-k| / |correct|`` over the question's answer
sessions (real S-split data has no empty answer sets — the audit census).
For non-empty correct sets the official binaries are EXACT projections of
the fraction: ``recall_all@k == (fraction == 1.0)`` and ``recall_any@k ==
(fraction > 0)`` — never approximated (see ``binary_projections`` + the
empty-set edge below). Aggregates mirror the official filter: non-_abs
questions only.

The four dataset-audit divergences (``dataset_audit.py`` PAPER_DIVERGENCES)
are reported as EXPLICITLY LABELED variants, never mixed into the official
number (R12):

    D1  official excludes _abs from retrieval aggregates → legacy
        _abs-INCLUSIVE fraction is a labeled variant (V-_abs)
    D2  official turn corpus indexes role=='user' only → assistant-role
        evidence turns are out-of-corpus (labeled; the report's turn-level
        numbers include them — V-assistant, census + note, no exact
        user-only numeric exists in the report payload)
    D3  official metrics are binary recall_any/recall_all + ndcg; ours is
        the per-question FRACTION → the fraction (non-_abs) is a labeled
        variant (V-fraction); official recall_all@5 and any-hit are
        computed as the exact projections above
    D4  official code asserts has_answer on every user turn; the cleaned
        split marks it sparsely → the sparse-marking census rides the
        audit record (the official noans-renaming rule is unexecutable on
        the cleaned split without interpreting divergence #4 — recorded,
        never silently applied)

Empty-set edge: for an EMPTY correct set the official ``all([])`` is 1.0
and ``any([])`` is 0.0. The real S split has zero empty answer_session_ids
(audit ``abstentions.empty_answer_session_ids: 0``), and abstention (_abs)
questions are excluded from the official aggregate anyway — the edge is
documented here so a future dataset shape cannot silently change the
official number's meaning (the module treats an absent/empty fraction as
not-eligible and counts it in ``n_excluded``, never in the mean).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

#: Receipt schema keys mandated by plan §6.6 (same vocabulary as the
#: W2/W3 harness receipts).
RUN_STATUS_VALUES = ("completed", "failed", "skipped")
VERDICT_VALUES = ("pass", "regression", "inconclusive")
FAILURE_ORIGIN_VALUES = (None, "config_mismatch", "hash_mismatch",
                         "runner_error", "gate_regression")

#: Judge pin label for the W7 runs: the OFFICIAL LongMemEval judge —
#: gpt-4o-2024-08-06 (the paper's judge model) + the verbatim
#: ``evaluate_qa.py`` anscheck prompts (``judge.py``), pinned by the
#: report's ``judge_rubric_id_hash``.
JUDGE_PIN = "longmemeval-official-anscheck-gpt4o-2024-08-06-v1"


# ── official metric derivation ─────────────────────────────────────────────

def _is_abs(question_id: Any) -> bool:
    return isinstance(question_id, str) and "_abs" in question_id


def _fraction_at_k(outcome: dict, k: int | str) -> float | None:
    """Per-question session_recall@k fraction from an outcome (None when the
    outcome carries no record for k — a failure/shape-broken entry is never
    coerced into the mean)."""
    sr = outcome.get("session_recall@k")
    if not isinstance(sr, dict):
        return None
    v = sr.get(str(k))
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def binary_projections(fraction: float) -> tuple[float, float]:
    """Exact per-question projections of the official binaries from the
    harness's exact per-question fraction (see module docstring):

    ``recall_all == (fraction == 1.0)`` (all correct docs in top-k) and
    ``recall_any == (fraction > 0)`` (any-hit — the gbrain-erratum
    contrast, NEVER presented as the official number).
    """
    return (1.0 if fraction == 1.0 else 0.0,
            1.0 if fraction > 0 else 0.0)


def variant_rows(outcomes: list[dict], k: int | str = 5) -> dict[str, Any]:
    """The labeled metric-variant block for one report (official +
    any-hit + the computable divergence variants). Pure over the outcomes
    list; each row carries its label, semantics pointer, n and value.

    Rows (never mixed — R12):
      official_recall_all@k — mean over non-_abs of the official binary
          ``all(doc in recalled_docs for doc in correct_docs)`` (asserted
          against the official evaluator in the tests)
      any_hit@k (labeled variant) — mean over non-_abs of the any-hit
          binary; the gbrain 97.6% erratum semantics, explicitly NOT the
          official number
      fraction_paper@k (variant D1/D3: per-question fraction, non-_abs) —
          the harness's own per-question fraction averaged over non-_abs
      fraction_legacy@k (variant D1: _abs-INCLUSIVE) — the legacy
          aggregate over ALL questions
      turn_* (variant D2: assistant-role turns) — the report's turn-level
          aggregates include assistant-role evidence turns (out-of-corpus
          officially); carried as labeled values with the divergence note
      audit_record — dataset_audit.py census (variant D4: sparse
          has_answer; and the D2 role census)
    """
    non_abs = [o for o in outcomes if not _is_abs(o.get("question_id"))]
    abs_q = [o for o in outcomes if _is_abs(o.get("question_id"))]

    def _mean(rows: list[dict]) -> tuple[float | None, int, int]:
        vals = [v for v in (_fraction_at_k(o, k) for o in rows)
                if v is not None]
        return (sum(vals) / len(vals) if vals else None,
                len(vals), len(rows) - len(vals))

    # official binaries are EXACT projections of the per-question fraction
    # (module docstring: real data has no empty correct sets; empty/absent
    # fractions are excluded and counted, never coerced)
    official_vals: list[float] = []
    anyhit_vals: list[float] = []
    for o in non_abs:
        f = _fraction_at_k(o, k)
        if f is None:
            continue
        recall_all, recall_any = binary_projections(f)
        official_vals.append(recall_all)
        anyhit_vals.append(recall_any)
    frac_paper, n_paper, n_ex_paper = _mean(non_abs)
    frac_legacy, n_legacy, n_ex_legacy = _mean(outcomes)
    return {
        f"official_recall_all@{k}": {
            "label": "OFFICIAL LongMemEval recall_all@k (all correct "
                     "sessions in top-k; non-_abs)",
            "value": (sum(official_vals) / len(official_vals)
                      if official_vals else None),
            "n": len(official_vals),
            "n_excluded": len(non_abs) - len(official_vals),
            "semantics": "all(doc in recalled_docs for doc in correct_docs) "
                         "per print_retrieval_metrics.py + "
                         "eval_utils.evaluate_retrieval",
        },
        f"any_hit@{k}": {
            "label": "LABELED VARIANT (NOT official): any-hit recall — any "
                     "correct session in top-k (gbrain 97.6% erratum "
                     "semantics)",
            "value": (sum(anyhit_vals) / len(anyhit_vals)
                      if anyhit_vals else None),
            "n": len(anyhit_vals),
            "n_excluded": len(non_abs) - len(anyhit_vals),
        },
        f"fraction_paper@{k}": {
            "label": "LABELED VARIANT (D1+D3): per-question-fraction "
                     "session recall, non-_abs (paper-aligned exclusion)",
            "value": frac_paper,
            "n": n_paper,
            "n_excluded": n_ex_paper,
        },
        f"fraction_legacy@{k}": {
            "label": "LABELED VARIANT (D1): per-question-fraction session "
                     "recall, _abs-INCLUSIVE (legacy aggregate)",
            "value": frac_legacy,
            "n": n_legacy,
            "n_excluded": n_ex_legacy,
            "n_abs": len(abs_q),
        },
        "turn_recall_paper@k": {
            "label": "LABELED VARIANT (D2): turn-level recall over the "
                     "report's marked hits — INCLUDES assistant-role "
                     "evidence turns (out-of-corpus under the official "
                     "role=='user' turn corpus); read from the report",
            "value": None,  # filled from the report's retrieval block
        },
    }


# ── sealed keys ────────────────────────────────────────────────────────────

def seal_answer_keys(instances: list[dict]) -> dict[str, Any]:
    """Sealed-key manifest (DM-11 / plan §6.6 "sealed at the boundary,
    sha256-manifested"): per-question sha256 over the canonical JSON of the
    answer-key fields (question_id, answer, answer_session_ids) + one
    aggregate digest over the sorted per-question digests (a gold-only edit
    changes the aggregate — baselines/gold invalidated, never silent).

    The corpus hash (the report's ``dataset_fingerprint``/full-file sha256,
    verified against the official split digest) is recorded SEPARATELY in
    the receipt; this manifest seals the ANSWER KEYS within the corpus.
    """
    per_q: dict[str, str] = {}
    for q in instances:
        qid = q.get("question_id")
        if qid is None:
            continue
        canon = json.dumps(
            {"question_id": qid,
             "answer": q.get("answer"),
             "answer_session_ids": q.get("answer_session_ids")},
            sort_keys=True, separators=(",", ":"))
        per_q[str(qid)] = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    agg = hashlib.sha256()
    for qid in sorted(per_q):
        agg.update(f"{qid}:{per_q[qid]}\n".encode())
    return {
        "seal_schema": "w7-answer-keys-v1",
        "n_questions": len(per_q),
        "per_question": per_q,
        "digest": "sha256:" + agg.hexdigest(),
    }


# ── receipt ────────────────────────────────────────────────────────────────

def build_receipt(*, run_id: str, date: str, commit: str, corpus_hash: str,
                  judge_pin: str, judge_model: str, judge_rubric_id_hash: str,
                  reader_model_spec: str, reader_pinned: bool,
                  ingest_mode: str, run_status: str, verdict: str,
                  failure_origin: str | None, cost_usd: float,
                  metrics: dict, notes: list[str],
                  resolved_config: dict | None = None) -> dict:
    """Validated-receipt shape per plan §6.6 (same vocabulary as the W2/W3
    harness receipts). ``judge_pin`` = the pinned judge version label;
    ``judge_model`` + ``judge_rubric_id_hash`` carry the resolved identity.
    """
    return {
        "receipt_version": 1,
        "run_id": run_id,
        "date": date,
        "run_status": run_status,
        "verdict": verdict,
        "failure_origin": failure_origin,
        "commit": commit,
        "corpus_hash": corpus_hash,
        "judge_pin": judge_pin,
        "judge_model": judge_model,
        "judge_rubric_id_hash": judge_rubric_id_hash,
        "reader_model_spec": reader_model_spec,
        "reader_pinned": reader_pinned,
        "ingest_mode": ingest_mode,
        "resolved_config": resolved_config or {},
        "cost_usd": cost_usd,
        "metrics": metrics,
        "notes": notes,
    }


def validate_receipt(receipt: dict) -> list[str]:
    """Receipt validation — the S13 check an auditor (J7) re-runs. Returns
    a list of issues (empty = valid)."""
    issues: list[str] = []
    if not isinstance(receipt, dict):
        return ["receipt: not an object"]
    for key in ("run_id", "date", "commit", "corpus_hash", "judge_pin"):
        value = receipt.get(key)
        if key == "judge_pin":
            if receipt.get("run_status") == "completed" and (
                    not isinstance(value, str) or not value.strip()):
                issues.append(
                    "receipt.judge_pin: completed run requires a pinned judge")
        elif not isinstance(value, str) or not value.strip():
            issues.append(
                f"receipt.{key}: expected a non-empty string, got {value!r}")
    if receipt.get("run_status") not in RUN_STATUS_VALUES:
        issues.append(
            f"receipt.run_status: unexpected {receipt.get('run_status')!r}")
    if receipt.get("verdict") not in VERDICT_VALUES:
        issues.append(
            f"receipt.verdict: unexpected {receipt.get('verdict')!r}")
    origin = receipt.get("failure_origin")
    if origin not in FAILURE_ORIGIN_VALUES:
        issues.append(
            f"receipt.failure_origin: unexpected {origin!r}")
    metrics = receipt.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        issues.append("receipt.metrics: expected a non-empty object")
    elif receipt.get("run_status") == "completed" and not any(
            str(key).startswith("official_recall_all@")
            for key in metrics):
        issues.append(
            "receipt.metrics: completed run missing the "
            "official_recall_all@k metric key")
    if not isinstance(receipt.get("resolved_config"), dict):
        issues.append("receipt.resolved_config: expected an object")
    if isinstance(receipt.get("cost_usd"), bool) or not isinstance(
            receipt.get("cost_usd"), (int, float)):
        issues.append("receipt.cost_usd: expected a number")
    elif receipt.get("cost_usd") < 0:
        issues.append(
            f"receipt.cost_usd: expected >= 0, got {receipt.get('cost_usd')!r}")
    if receipt.get("run_status") == "completed":
        if not isinstance(receipt.get("judge_model"), str) or not (
                receipt.get("judge_model") or "").strip():
            issues.append(
                "receipt.judge_model: completed run requires the resolved "
                "judge model identity")
        if not isinstance(receipt.get("judge_rubric_id_hash"), str) or not (
                receipt.get("judge_rubric_id_hash") or "").strip():
            issues.append(
                "receipt.judge_rubric_id_hash: completed run requires the "
                "pinned rubric hash (judge drift guard)")
        if not isinstance(receipt.get("corpus_hash"), str) or not (
                receipt.get("corpus_hash") or "").startswith("sha256:"):
            issues.append(
                "receipt.corpus_hash: expected a sha256:… corpus hash")
    return issues


# ── CLI ────────────────────────────────────────────────────────────────────

def _main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        prog="python -m tools.longmem_eval.w7_publish",
        description="W7 publication layer: derive official metrics + labeled "
                    "variants from a run report, seal answer keys, build + "
                    "validate the receipt (issue #2105).")
    p.add_argument("--report", required=True, help="runner report JSON")
    p.add_argument("--dataset", required=True,
                   help="dataset file (answer-key seal source)")
    p.add_argument("--commit", required=True, help="exact run commit sha")
    p.add_argument("--corpus-hash", required=True,
                   help="sha256:… corpus hash (full dataset file digest)")
    p.add_argument("--cost-usd", type=float, required=True,
                   help="per-run cost in USD")
    p.add_argument("--out", required=True,
                   help="output artifact JSON path")
    args = p.parse_args(argv)

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    instances = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    outcomes = report.get("outcomes", [])

    rows = variant_rows(outcomes)
    # turn-level variants read from the report's own retrieval block
    retrieval = report.get("retrieval") or {}
    for k in ("turn_recall_paper@k", "turn_recall@k",
              "session_recall_paper@k"):
        if k in retrieval and rows.get(k) is not None:
            rows[k]["value"] = retrieval[k].get("5")
    # pull the paper-fraction + legacy values from the report too
    if "session_recall_paper@k" in retrieval and "5" in (
            retrieval["session_recall_paper@k"] or {}):
        rows["fraction_paper@5"]["report_value"] = \
            retrieval["session_recall_paper@k"]["5"]
    if "session_recall@k" in retrieval and "5" in (
            retrieval["session_recall@k"] or {}):
        rows["fraction_legacy@5"]["report_value"] = \
            retrieval["session_recall@k"]["5"]

    methodology = report.get("methodology") or {}
    seal = seal_answer_keys(instances)
    integrity = report.get("integrity") or {}
    run_status = ("completed" if integrity.get("valid") is True
                  and report.get("accuracy") is not None
                  else "failed")
    verdict = "pass" if run_status == "completed" else "inconclusive"
    metrics = {f"official_recall_all@{k}": rows[f"official_recall_all@{k}"]["value"]
               for k in ("5",)}
    metrics["variants"] = rows
    receipt = build_receipt(
        run_id=f"w7a-500q-{args.commit[:12]}",
        date=report.get("updated_at_utc")
             or (methodology.get("run_at_utc") or ""),
        commit=args.commit,
        corpus_hash=args.corpus_hash,
        judge_pin=JUDGE_PIN,
        judge_model=methodology.get("judge_model", ""),
        judge_rubric_id_hash=methodology.get("judge_rubric_id_hash", ""),
        reader_model_spec=methodology.get("reader_model_spec", ""),
        reader_pinned=bool(methodology.get("reader_pinned")),
        ingest_mode=methodology.get("ingest_mode", ""),
        run_status=run_status,
        verdict=verdict,
        failure_origin=None,
        cost_usd=args.cost_usd,
        metrics=metrics,
        notes=[],
        resolved_config={
            "split": report.get("split"),
            "ingest_mode": methodology.get("ingest_mode"),
            "workers": methodology.get("workers"),
            "audit_verdict": (methodology.get("dataset_semantics_audit")
                              or {}).get("verdict"),
        },
    )
    issues = validate_receipt(receipt)
    artifact = {
        "seal": seal,
        "variants": rows,
        "receipt": receipt,
        "receipt_valid": not issues,
        "receipt_issues": issues,
        "integrity": integrity,
        "accuracy": report.get("accuracy"),
    }
    Path(args.out).write_text(
        json.dumps(artifact, indent=2), encoding="utf-8")
    print(f"[w7_publish] wrote {args.out}")
    print(f"[w7_publish] official_recall_all@5 = "
          f"{rows['official_recall_all@5']['value']} (n="
          f"{rows['official_recall_all@5']['n']})")
    print(f"[w7_publish] receipt valid: {not issues}")
    for i in issues:
        print(f"  RECEIPT ISSUE: {i}")


if __name__ == "__main__":
    _main()
