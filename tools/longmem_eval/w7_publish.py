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

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/longmem_eval/w7_publish.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python -m tools.longmem_eval.w7_publish`"
    )

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from . import measurement_posture

#: Receipt schema keys mandated by plan §6.6 (same vocabulary as the
#: W2/W3 harness receipts).
RUN_STATUS_VALUES = ("completed", "partial", "failed", "skipped")
VERDICT_VALUES = ("pass", "regression", "inconclusive")
FAILURE_ORIGIN_VALUES = (None, "config_mismatch", "hash_mismatch",
                         "runner_error", "gate_regression")

#: Judge pin label for the W7 runs: the OFFICIAL LongMemEval judge —
#: gpt-4o-2024-08-06 (the paper's judge model) + the verbatim
#: ``evaluate_qa.py`` anscheck prompts (``judge.py``), pinned by the
#: report's ``judge_rubric_id_hash``.
JUDGE_PIN = "longmemeval-official-anscheck-gpt4o-2024-08-06-v1"

#: The pin a MOCK-graded run carries. The pin is DERIVED from the judge the
#: report records (``judge_pin_for``), never passed in independently, so a
#: receipt cannot carry the official pin while a stub graded the answers.
MOCK_JUDGE_PIN = "longmemeval-mock-judge"

#: The official judge MODEL id (the transport serving it is a routing detail:
#: ``openrouter:openai/gpt-4o-2024-08-06`` is the same judge).
OFFICIAL_JUDGE_MODEL_ID = "gpt-4o-2024-08-06"


def _judge_model_id(spec: Any) -> str:
    """``provider:model`` → ``model``; a bare id is its own model id."""
    return str(spec or "").strip().split(":", 1)[-1]


def judge_pin_for(judge_model: Any) -> str:
    """The pin belonging to the judge identity a report RECORDS — an
    ALLOW-LIST, never a default.

    ``JUDGE_PIN`` used to be minted for every run, so a report graded by
    ``mock-judge`` — zero network, zero cost, ``run.py --mock`` — produced a
    receipt carrying the OFFICIAL pin with ``receipt_valid: True`` and
    ``verdict: pass`` (measured at #8486). The same shape covers the tempting
    substitution this issue's thread keeps returning to (grade the graded run
    with a cheaper model): a substituted judge recorded as its own model id
    was published under the official pin. R12 forbids substituting the judge in
    the official number, so the pin has to travel with the judge that actually
    graded: the official pin belongs to the OFFICIAL MODEL, a stub gets the
    mock pin, and everything else records ``unpinned:<model>``.

    Same allow-list SHAPE as ``measure_ask_distilled.judge_pin_for`` (which
    closed the same hole for the ask-exit measurement) — deliberately NOT the
    same strings, so do not read "mirrors" into it: that one keys on the judge
    SPEC (only the literal ``mock`` is its stub, and a routed non-official spec
    records ``unpinned:<spec>``), this one keys on the MODEL id (``mock`` and
    ``mock-judge`` are both stubs, and a routed non-official spec records
    ``unpinned:<model>``).
    """
    model = _judge_model_id(judge_model)
    if not model:
        return "unpinned:<unrecorded>"
    if model in ("mock", "mock-judge"):
        return MOCK_JUDGE_PIN
    if model == OFFICIAL_JUDGE_MODEL_ID or model.endswith(
            "/" + OFFICIAL_JUDGE_MODEL_ID):
        return JUDGE_PIN
    return f"unpinned:{model}"


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


#: The reader identity a retrieval-only report records (``run.py`` renders
#: these when the reader/judge were never built — #8486 F3).
RETRIEVAL_ONLY_MODEL = "n/a (retrieval-only)"


def _reports_retrieval_only(methodology: dict, retrieval: dict) -> bool:
    """Did this report come from a run that never invoked a reader/judge?

    Read from the report's OWN record, never inferred from ``accuracy is
    None`` alone: an accuracy of None is also what a run whose every question
    failed produces, and that run is ``failed``, not ``partial``. Three
    independent signals, any one of which is sufficient (they are written by
    ``run.py`` from the same ``retrieval_only`` flag):
    ``methodology.retrieval_only`` (written from the #1349 arm block),
    ``methodology.reader_model == "n/a (retrieval-only)"`` (what ``run.py``
    renders when no reader was built — #8486 F3), and
    ``retrieval.retrieval_by_category.population.reader_ran is False``.
    """
    if methodology.get("retrieval_only") is True:
        return True
    if str(methodology.get("reader_model", "")).strip() \
            == RETRIEVAL_ONLY_MODEL:
        return True
    by_category = (retrieval or {}).get("retrieval_by_category") \
        if isinstance(retrieval, dict) else None
    population = (by_category or {}).get("population") \
        if isinstance(by_category, dict) else None
    return isinstance(population, dict) and population.get("reader_ran") is False


def binary_projections(fraction: float) -> tuple[float, float]:
    """Exact per-question projections of the official binaries from the
    harness's exact per-question fraction (see module docstring):

    ``recall_all == (fraction == 1.0)`` (all correct docs in top-k) and
    ``recall_any == (fraction > 0)`` (any-hit — the gbrain-erratum
    contrast, NEVER presented as the official number).
    """
    return (1.0 if fraction == 1.0 else 0.0,
            1.0 if fraction > 0 else 0.0)


#: #2516 (C1): the TYPE-TAIL gate categories — the per-category rows the
#: sealed run must publish beside the full-mix number. These are the RAW
#: dataset ``question_type`` values (the same field the report's
#: ``category_of`` maps to the paper categories), so the grouping needs no
#: second category table here. The 2026-09-07 measurement (epic-scope
#: doc §1) put the partial-evidence loss on exactly these classes; a naive
#: head slice is ~100% ``single-session-user`` and cannot measure any of
#: them (the 50-Q head-slice artifact).
CATEGORY_GATE_TYPES = ("multi-session", "knowledge-update",
                       "single-session-preference")


def category_variant_rows(outcomes: list[dict], k: int | str = 5,
                          *,
                          categories: tuple[str, ...] = CATEGORY_GATE_TYPES,
                          ) -> dict[str, Any]:
    """#2516 (C1): per-category OFFICIAL ``recall_all@k`` rows.

    The official aggregate in :func:`variant_rows` answers "how did the run
    do overall"; the EPIC's gate needs "how did it do on the tail" — the
    failure profile is category-specific and the official binary is
    all-or-nothing, so a full-mix number can sit near its anchor while the
    multi-session class is where the loss lives.

    Grouping is by the dataset's own ``question_type`` (the raw field), with
    a ``full_mix`` row over every non-``_abs`` question. The projection is
    the SAME exact one :func:`binary_projections` applies
    (``recall_all == (fraction == 1.0)``) — deliberately one implementation
    of the official semantics, shared with the top-level row, so the
    per-category numbers can never be computed by a different rule than the
    headline.

    A gate category ABSENT from the outcome set is returned with
    ``n == 0`` and ``value is None`` — never omitted (an omitted row is
    indistinguishable from a row nobody looked at, and the coverage target
    "tail categories ≥95% covered" has to be checkable) and never
    fabricated.
    """
    non_abs = [o for o in outcomes if not _is_abs(o.get("question_id"))]

    def _row(rows: list[dict]) -> dict[str, Any]:
        vals: list[float] = []
        fracs: list[float] = []
        for o in rows:
            f = _fraction_at_k(o, k)
            if f is None:
                continue
            vals.append(binary_projections(f)[0])
            fracs.append(f)
        return {
            f"official_recall_all@{k}": (
                sum(vals) / len(vals) if vals else None),
            f"fraction_paper@{k}": (
                sum(fracs) / len(fracs) if fracs else None),
            "n": len(vals),
            "n_excluded": len(rows) - len(vals),
        }

    rows = {"full_mix": _row(non_abs)}
    for cat in categories:
        rows[cat] = _row([o for o in non_abs
                          if o.get("question_type") == cat])
    covered = sum(1 for c in categories if rows[c]["n"] > 0)
    return {
        "k": k,
        "semantics": "official all(doc in recalled_docs for doc in "
                     "correct_docs) projection, non-_abs ONLY, grouped by "
                     "the dataset's question_type",
        "gate_categories": list(categories),
        "rows": rows,
        "coverage": {
            "gate_categories_total": len(categories),
            "gate_categories_with_questions": covered,
            "coverage": (covered / len(categories)) if categories else None,
        },
    }


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
    out = {
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
    # #2516 (C1): the per-category official rows ride WITH the top-level
    # rows (one projection implementation, one artifact) — the sealed
    # receipt's gate surface for the type tail.
    out["by_category"] = category_variant_rows(outcomes, k)
    return out


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
                  resolved_config: dict | None = None,
                  measurement_posture: dict[str, Any] | None = None) -> dict:
    """Validated-receipt shape per plan §6.6 (same vocabulary as the W2/W3
    harness receipts). ``judge_pin`` = the pinned judge version label;
    ``judge_model`` + ``judge_rubric_id_hash`` carry the resolved identity.

    #8486: ``measurement_posture`` carries the ingest mode AND whether the
    belief layer was activated — the disclosure the number is unreadable
    without. Omitted by a pre-#8486 producer, whose completed receipt the
    validator then REFUSES (a number never publishes with an implied
    posture).
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
        "measurement_posture": measurement_posture,
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
    elif receipt.get("run_status") == "completed":
        # #2516 (C1): a sealed run that cannot report the TYPE TAIL cannot
        # gate the #2513 levers (the measured loss is category-specific and
        # the all-or-nothing official binary hides it in the full-mix mean).
        # The per-category block is REQUIRED on a completed receipt; a gate
        # category with no questions is legitimate and stays VISIBLE as
        # n == 0 (coverage is then readable) — an absent block is not.
        by_cat = metrics.get("official_recall_all@5_by_category")
        if not isinstance(by_cat, dict) or not isinstance(
                by_cat.get("rows"), dict):
            issues.append(
                "receipt.metrics: completed run missing the per-category "
                "official rows (official_recall_all@5_by_category — the "
                "#2516 type-tail gate surface)")
        else:
            cat_rows = by_cat["rows"]
            missing = [c for c in CATEGORY_GATE_TYPES
                       if not isinstance(cat_rows.get(c), dict)]
            if "full_mix" not in cat_rows:
                missing = ["full_mix", *missing]
            if missing:
                issues.append(
                    "receipt.metrics: per-category official rows incomplete "
                    f"(missing {sorted(missing)} — every gate category must "
                    "be present, n == 0 when the run carried none)")
    if not isinstance(receipt.get("resolved_config"), dict):
        issues.append("receipt.resolved_config: expected an object")
    # #8486: the MEASUREMENT POSTURE gate. A completed number must say which
    # ingest mode produced it AND whether the belief layer was activated —
    # without both it is not comparable to a competitor's full-pipeline
    # number, and a v2 run without activation measures a Tortoise whose
    # epistemic layer is EP-inert (#2598). Refused fail-closed when the
    # disclosure is absent, unrecorded (``None``) or ``unknown`` — a number
    # never publishes with an IMPLIED posture.
    if receipt.get("run_status") == "completed":
        posture = receipt.get("measurement_posture")
        if not isinstance(posture, dict):
            issues.append(
                "receipt.measurement_posture: completed run requires the "
                "posture block (ingest mode + belief-layer activation — "
                "#8486)")
        else:
            beliefs = posture.get("beliefs_activated")
            if not isinstance(beliefs, bool):
                issues.append(
                    "receipt.measurement_posture.beliefs_activated: completed "
                    f"run requires an explicit boolean, got {beliefs!r} — an "
                    "unrecorded belief-layer posture is not publishable "
                    "(#8486)")
            layer = posture.get("belief_layer")
            if layer not in measurement_posture.BELIEF_LAYER_VALUES \
                    or layer == measurement_posture.BELIEF_LAYER_UNKNOWN:
                issues.append(
                    "receipt.measurement_posture.belief_layer: completed run "
                    f"requires a resolved state, got {layer!r} (#8486)")
            p_mode = posture.get("ingest_mode")
            if not isinstance(p_mode, str) or not p_mode.strip():
                issues.append(
                    "receipt.measurement_posture.ingest_mode: completed run "
                    "requires the ingest mode the number was produced with "
                    "(#8486)")
            elif (isinstance(receipt.get("ingest_mode"), str)
                  and receipt["ingest_mode"].strip()
                  and p_mode != receipt["ingest_mode"]):
                issues.append(
                    "receipt.measurement_posture.ingest_mode: "
                    f"{p_mode!r} disagrees with receipt.ingest_mode "
                    f"{receipt['ingest_mode']!r} — one number, one mode "
                    "(#8486)")
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
    # #8486 (R12): the pin must belong to the judge the report records. A
    # pin/model pair that cannot both be true is how a stub-graded or
    # substituted-judge run is published under the OFFICIAL pin — the receipt
    # is the artifact J7 audits, so this is checked there.
    #
    # Checked on EVERY receipt the auditor can read, never only a completed
    # one: ``failed`` and ``skipped`` are legal ``run_status`` values, so a
    # clause scoped to ``completed`` validated nothing in the other two — a
    # hand-built receipt simply declared itself failed and kept the OFFICIAL
    # pin for a stub-graded run (``validate_receipt`` returned ``[]``;
    # measured at 885e6f372). A receipt that publishes measurements is read by
    # J7 whatever status it claims, and the status is a field in the same
    # hand-built object, so it cannot be the fence around the claim.
    recorded_judge = receipt.get("judge_model")
    expected_pin = judge_pin_for(recorded_judge)
    recorded_pin = receipt.get("judge_pin")
    if isinstance(recorded_pin, str) and recorded_pin.strip() \
            and recorded_pin != expected_pin:
        issues.append(
            f"receipt.judge_pin: {recorded_pin!r} does not belong to the "
            f"judge the report records ({recorded_judge!r} — expected "
            f"{expected_pin!r}) (#8486)")
    # The official pin is the licence to publish a comparable number; a run
    # graded by anything else may record its result, never a 'pass'. Both
    # predicates of the documented claim — "only an official-pinned COMPLETED
    # run records verdict: pass" — are enforced here, on every status: the pin
    # half below, the completion half first (a failed/skipped receipt that
    # reads 'pass' asserts a result the run cannot back, whichever judge
    # graded it).
    if receipt.get("verdict") == "pass":
        if receipt.get("run_status") != "completed":
            issues.append(
                f"receipt.verdict: a {receipt.get('run_status')!r} run cannot "
                "publish 'pass' — only a completed run carries a verdict, and "
                "a failed/skipped receipt records 'inconclusive' (#8486)")
        if expected_pin != JUDGE_PIN:
            issues.append(
                "receipt.verdict: a run graded by "
                f"{recorded_judge!r} (pin {expected_pin!r}) cannot publish "
                "'pass' — R12 forbids substituting the judge in the official "
                "number (#8486)")
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
    # #8486: the posture the runner recorded (ingest mode + whether the belief
    # layer was activated). Read from the report — never hand-set here — so a
    # receipt cannot claim a posture the run did not take. Absent (a pre-#8486
    # report) → the UNKNOWN posture, which ``validate_receipt`` REFUSES on a
    # completed run.
    posture = (measurement_posture.of_report(report)
               or measurement_posture.build(
                   ingest_mode=methodology.get("ingest_mode", "")))
    seal = seal_answer_keys(instances)
    integrity = report.get("integrity") or {}
    judge_model = methodology.get("judge_model", "")
    judge_pin = judge_pin_for(judge_model)
    # #8486 F4: the status must say what the run ACTUALLY did. ``accuracy is
    # None`` used to force ``failed``, so a clean retrieval-only run —
    # ``integrity.valid == true``, every question retrieved, reader/judge never
    # invoked by design — was recorded as a failure and could not be published
    # as what it is. A retrieval-only run carries the OFFICIAL retrieval metric
    # and no QA number, so it is ``partial`` (its own status), never
    # ``completed`` (which would claim an accuracy) and never ``failed`` (which
    # would disown a measurement that is valid).
    retrieval_only = _reports_retrieval_only(methodology, retrieval)
    run_status = ("completed"
                  if integrity.get("valid") is True
                  and report.get("accuracy") is not None
                  else "partial"
                  if integrity.get("valid") is True and retrieval_only
                  else "failed")
    # #8486 (R12): the verdict is derived from the SAME identity as the pin, so
    # the receipt is self-consistent rather than merely refused later. A
    # completed run graded by a stub or a substituted model records
    # ``inconclusive`` — a consumer that reads ``verdict`` alone (the field R12
    # is about, cited by ``docs/benchmarks/w7a-comparison-rows-for-2106.md``)
    # can no longer read 'pass' off a run that cannot back the official number.
    # ``validate_receipt`` keeps its clause as defence in depth for receipts
    # that are HAND-BUILT rather than derived here.
    verdict = ("pass" if run_status == "completed" and judge_pin == JUDGE_PIN
               else "inconclusive")
    metrics = {f"official_recall_all@{k}": rows[f"official_recall_all@{k}"]["value"]
               for k in ("5",)}
    # #2516 (C1): the sealed receipt carries the per-category official rows
    # beside the full-mix number (the type-tail gate surface).
    metrics["official_recall_all@5_by_category"] = rows["by_category"]
    metrics["variants"] = rows
    receipt = build_receipt(
        run_id=f"w7a-500q-{args.commit[:12]}",
        date=report.get("updated_at_utc")
             or (methodology.get("run_at_utc") or ""),
        commit=args.commit,
        corpus_hash=args.corpus_hash,
        # #8486: DERIVED from the judge the report recorded — never the
        # constant (a mock/substituted judge must not mint the official pin).
        judge_pin=judge_pin,
        judge_model=judge_model,
        judge_rubric_id_hash=methodology.get("judge_rubric_id_hash", ""),
        reader_model_spec=methodology.get("reader_model_spec", ""),
        reader_pinned=bool(methodology.get("reader_pinned")),
        ingest_mode=methodology.get("ingest_mode", ""),
        measurement_posture=posture,
        run_status=run_status,
        verdict=verdict,
        failure_origin=None,
        cost_usd=args.cost_usd,
        metrics=metrics,
        # #8486 F4: a ``partial`` receipt says WHICH measurement it carries -
        # a reader here (reading ``partial`` as "half the questions failed"
        # would be a different claim entirely).
        notes=(["retrieval-only run: the official retrieval metric was "
                "measured and QA accuracy was NOT (the reader/judge never "
                "ran by design) — run_status 'partial', never 'failed' and "
                "never 'completed'"]
               if run_status == "partial" else []),
        resolved_config={
            "split": report.get("split"),
            "ingest_mode": methodology.get("ingest_mode"),
            "beliefs_activated": posture.get("beliefs_activated"),
            "belief_layer": posture.get("belief_layer"),
            "workers": methodology.get("workers"),
            "audit_verdict": (methodology.get("dataset_semantics_audit")
                              or {}).get("verdict"),
        },
    )
    issues = validate_receipt(receipt)
    artifact = {
        "seal": seal,
        "variants": rows,
        "by_category": rows["by_category"],
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
    cat = rows["by_category"]
    for name, row in cat["rows"].items():
        print(f"[w7_publish]   recall_all@5[{name}] = "
              f"{row['official_recall_all@5']} (n={row['n']})")
    print(f"[w7_publish] gate-category coverage = "
          f"{cat['coverage']['gate_categories_with_questions']}/"
          f"{cat['coverage']['gate_categories_total']}")
    print(f"[w7_publish] receipt valid: {not issues}")
    for i in issues:
        print(f"  RECEIPT ISSUE: {i}")


if __name__ == "__main__":
    _main()
