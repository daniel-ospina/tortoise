"""Report aggregation + methodology provenance (issue #1144, axis 2).

Aggregates per-question outcomes into the published report shape:
overall + task-averaged + per-category accuracy (the five paper abilities:
information extraction, multi-session reasoning, temporal reasoning,
knowledge updates, abstention), per-type accuracy (the six raw dataset
types), retrieval recall@k (session- and turn-level; paper-aligned _paper@k
keys over non-_abs questions, M7), context tokens, latency (incl. the
isolated ingest write-path cost, M7), an integrity block with the per-
question error census (M7), leg-mix / pool-size / evidence written-·retrieved
aggregates (M7) — together with a full methodology block (dataset id, split,
reader model, judge model, extraction approach, k values, token estimator,
git sha, python version, workers, dataset fingerprint, the dataset recall-
semantics audit record, run date) so numbers are honestly contextualized
(no "#1" claims).

⛔ Publication gate (E2E-3 Precondition 2, M7 #1527): ``dataset_semantics_audit``
is a REQUIRED build_report argument — no recall number leaves the harness
without the dataset recall-semantics audit record; a not-trusted verdict
serializes every recall key to null.
"""
from __future__ import annotations

import json
import os  # noqa: F401
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .dataset_audit import is_trusted

# R3 (#1542) D5: the dense-leg methodology is ALWAYS emitted — a report can
# never be keyless about the vector strategy (MemDelta pinning: embedder
# identity + availability ride in the methodology so a future swap or silent
# degradation is visible before any accuracy comparison). Programmatic
# callers (existing tests, battery/parity, the capstone harness) that omit
# embedder_status get the not_checked default.
DEFAULT_EMBEDDER_STATUS = {
    "model": "all-MiniLM-L6-v2",
    "sentence_transformers_version": None,
    "available": False,
    "reason": "not_checked",
}

# question_type → paper category (the five abilities from the LongMemEval
# paper; abstention is signalled by the ``_abs`` suffix, not a type).
PAPER_CATEGORY = {
    "single-session-user": "Information Extraction",
    "single-session-assistant": "Information Extraction",
    "single-session-preference": "Information Extraction",
    "multi-session": "Multi-Session Reasoning",
    "temporal-reasoning": "Temporal Reasoning",
    "knowledge-update": "Knowledge Updates",
}


def category_of(question: dict) -> str:
    if "_abs" in question["question_id"]:
        return "Abstention"
    return PAPER_CATEGORY.get(question["question_type"], "Other")


def _mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 4) if xs else 0.0


def _percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    import math
    k = (len(xs) - 1) * q
    lo = int(math.floor(k))  # noqa: RUF046
    hi = int(math.ceil(k))  # noqa: RUF046
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True,
            timeout=5, cwd=Path(__file__).resolve().parent.parent.parent,
        )
        return out.stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001, RUF100
        return "unknown"


def build_report(
    outcomes: list[dict[str, Any]],
    *,
    dataset_id: str,
    split: str,
    reader_model: str,
    judge_model: str,
    extraction_approach: str,
    ingest_mode: str = "deterministic",
    ks: tuple[int, ...],
    top_k: int,
    extra: dict[str, Any] | None = None,
    failures: list[dict[str, Any]] | None = None,
    reader_prompt_hash: str = "",
    judge_rubric_id_hash: str = "",
    reader_model_spec: str = "",
    reader_provider: str | None = None,
    reader_pinned: bool | None = None,
    reader_system_prompt: str = "",
    reader_type_fragments: dict[str, str] | None = None,
    r1_knobs: dict[str, Any] | None = None,
    # R3 (#1542) D5: the embedder pre-flight status (from run.py's
    # _preflight_embedder) recorded in the methodology — embedder identity,
    # sentence-transformers version, availability, reason. Always-emitted:
    # when omitted the not_checked default is recorded (never keyless).
    embedder_status: dict[str, Any] | None = None,
    # M7 (#1527): publication-gated inputs — see the docstring.
    dataset_semantics_audit: dict[str, Any] | None = None,
    integrity_threshold: float = 0.0,
    integrity_justification: str | None = None,
    python_version: str = "",
    workers: int = 1,
    dataset_fingerprint: str = "unknown",
) -> dict[str, Any]:
    """Aggregate per-question outcomes into the report + provenance dict.

    ``outcomes`` must contain only COMPLETED questions (failed questions are
    passed via ``failures`` and reported separately — a transient LLM error
    on one question must not abort the run or skew the aggregates).

    M7 (#1527) contract additions (additive-only; D11):
      * ``integrity`` — validity + per-question error census (D1/D6): a run
        with any failed question or ingest-error question is ``valid=false``
        unless an override threshold (+ recorded justification) admits it;
        the numbers are always recorded, so no degraded run can masquerade
        as clean.
      * ``leg_mix`` (D2) — per-leg ``match_source`` counts over the
        top_k context the reader saw + per-k over the deduped pool.
      * ``pool_size`` (D3) — live graph point count per question.
      * ``evidence`` (D4) — evidence written vs retrieved + vacuity over
        evidence-bearing questions only (evidence_absent_n excluded).
      * ``latency_ms.ingest`` (D5) — the isolated write-path cost.
      * paper-aligned ``retrieval.*_paper@k`` keys (non-_abs only, the
        official exclusion) alongside the legacy _abs-inclusive keys.
      * ``methodology`` gains python_version / workers / dataset_fingerprint
        / dataset_semantics_audit / integrity_threshold — a report always
        says what code and dataset produced it.

    ⛔ Publication gate (E2E-3 Precondition 2, enforced by construction):
    ``dataset_semantics_audit`` is REQUIRED — ``ValueError`` without it, and
    there is no flag to skip it. A not-trusted verdict (live census diverges
    structurally from the recorded semantics) serializes every recall key to
    ``null``: the report then contains no recall numbers until re-audited.
    """
    if dataset_semantics_audit is None:
        raise ValueError(
            "build_report requires dataset_semantics_audit (E2E-3 "
            "Precondition 2): no recall number is published without the "
            "dataset recall-semantics audit record — run_evaluation "
            "computes it; programmatic callers must pass "
            "audit_dataset(instances)")
    trusted = is_trusted(dataset_semantics_audit)
    n = len(outcomes)

    # ── accuracy ──
    labels = [o["label"] for o in outcomes]
    overall = _mean([1.0 if l else 0.0 for l in labels])  # noqa: E741

    by_category: dict[str, list[bool]] = {}
    by_type: dict[str, list[bool]] = {}
    abstention_labels: list[bool] = []
    for o in outcomes:
        q = {"question_id": o["question_id"],
             "question_type": o.get("question_type", "")}
        by_category.setdefault(category_of(q), []).append(o["label"])
        by_type.setdefault(q["question_type"], []).append(o["label"])
        if "_abs" in q["question_id"]:
            abstention_labels.append(o["label"])

    per_category = {c: {"accuracy": _mean([1.0 if l else 0.0 for l in ls]),  # noqa: E741
                        "n": len(ls)} for c, ls in sorted(by_category.items())}
    per_type = {t: {"accuracy": _mean([1.0 if l else 0.0 for l in ls]),  # noqa: E741
                    "n": len(ls)} for t, ls in sorted(by_type.items())}
    # task-averaged accuracy = mean of the per-raw-type means (official
    # print_qa_metrics.py definition).
    task_averaged = (_mean([1.0 if l else 0.0 for l in labels])  # noqa: E741
                     if len(by_type) <= 1 else
                     _mean([v["accuracy"] for v in per_type.values()]))

    # ── retrieval recall@k (session + turn level, mean over questions) ──
    session_recall: dict[str, float] = {}
    turn_recall: dict[str, float] = {}
    evidence_recall: dict[str, float] = {}
    evidence_recall_n: dict[str, int] = {}
    evidence_vacuity_rate: dict[str, float] = {}
    chunk_evidence_recall: dict[str, float] = {}
    chunk_evidence_recall_n: dict[str, int] = {}
    for k in ks:
        sr = [o["session_recall@k"].get(str(k), 0.0) for o in outcomes]
        session_recall[str(k)] = _mean(sr)
        # M6 (#1526): N/A (None) outcomes are DROPPED from the turn-level
        # mean too — a None coerced to 0.0 silently re-drags the vacuity the
        # epic excludes (bug-pattern flag 4).
        tr = [o["turn_recall@k"].get(str(k)) for o in outcomes]
        tr_real = [v for v in tr if v is not None]
        turn_recall[str(k)] = _mean(tr_real) if tr_real else 0.0
        # evidence_recall@k: mean over evidence-bearing outcomes ONLY (non-
        # None values) + the vacuity/coverage accounting (D6).
        er = [(o.get("evidence_recall@k") or {}).get(str(k), None)
              for o in outcomes]
        real = [v for v in er if v is not None]
        if real:
            evidence_recall[str(k)] = _mean(real)
            evidence_recall_n[str(k)] = len(real)
            evidence_vacuity_rate[str(k)] = round(
                sum(1.0 for v in real if v == 0.0) / len(real), 4)
        # R1 (#1540) D5: the M6 raw-chunk containment view, aggregated
        # parallel to evidence_recall@k (the sweep collection source).
        cer = [(o.get("chunk_evidence_recall@k") or {}).get(str(k), None)
               for o in outcomes]
        real_chunks = [v for v in cer if v is not None]
        if real_chunks:
            chunk_evidence_recall[str(k)] = _mean(real_chunks)
            chunk_evidence_recall_n[str(k)] = len(real_chunks)

    # M6 (D6): evidence_coverage — fraction of evidence-bearing questions
    # (dataset has >=1 evidence turn) whose ingest wrote evidence points
    # (the E2E-3 >95% gate metric; computed from the per-outcome ingest
    # stats, comparable across ingest modes since both legs report
    # evidence_turns/evidence_points).
    ev_bearing = [o for o in outcomes
                  if (o.get("ingest") or {}).get("evidence_turns", 0) > 0]
    evidence_coverage = (
        round(sum(1 for o in ev_bearing
                  if (o.get("ingest") or {}).get("evidence_points", 0) > 0)
              / len(ev_bearing), 4)
        if ev_bearing else 0.0)

    # M7 (D10): paper-aligned aggregates — the same per-question fraction
    # metric computed over non-_abs questions ONLY (the official
    # print_retrieval_metrics.py exclusion). Legacy keys keep the
    # _abs-inclusive definition (back-compat through V3).
    paper_outcomes = [o for o in outcomes
                      if "_abs" not in o.get("question_id", "")]

    def _paper_agg(key: str, k: int) -> float | None:
        vals = [(o.get(key) or {}).get(str(k)) for o in paper_outcomes]
        real = [v for v in vals if v is not None]
        return _mean(real) if real else None

    session_recall_paper = {
        str(k): _paper_agg("session_recall@k", k) for k in ks}
    turn_recall_paper = {
        str(k): _paper_agg("turn_recall@k", k) for k in ks}
    evidence_recall_paper = {
        str(k): _paper_agg("evidence_recall@k", k) for k in ks}

    # ── M7 (D1): integrity — validity + per-question error census ──
    # invalid = a failed question OR a completed question with
    # n_ingest_errors > 0; n_attempted dedups by qid across outcomes+failures.
    # M4 (#1524, D4/D5): the per-question ``error_classes`` is now the
    # extractor's granular class→count census (fatal_402_billing /
    # transient_429_rate_limit / parse_error / truncated / …) — rolled up
    # here by exact count. ``failures`` keep their site-prefixed eval classes
    # (errors.py: reader:retries_exhausted / judge:fatal / ingest) so the
    # census still answers "where did failures come from" at a glance.
    effective_threshold = float(integrity_threshold or 0.0)
    failure_qids = {f.get("question_id") for f in (failures or [])}
    attempted_qids = {o["question_id"] for o in outcomes} | failure_qids
    n_attempted = len(attempted_qids)
    n_valid = sum(1 for o in outcomes if o.get("valid", True))
    n_invalid = n_attempted - n_valid
    invalid_rate = round(n_invalid / n_attempted, 4) if n_attempted else 0.0
    census: Counter = Counter()
    for o in outcomes:
        ec = o.get("error_classes") or {}
        if isinstance(ec, dict):
            for cls, count in ec.items():
                census[cls] += int(count or 0)
        else:  # legacy flat-list shape (defensive back-compat)
            census.update(ec)
    for f in (failures or []):
        eclass = f.get("error_class")
        if eclass:
            census[eclass] += 1
    error_census = dict(sorted(census.items()))
    checks = [
        "python >= 3.12 guard enforced at run entry",
        "dataset loaded and recall-semantics audited",
        "dataset semantics audit present (publication gate)",
        "checkpoint fingerprint matched (no stale resume)",
        "per-question error census computed",
    ]
    integrity: dict[str, Any] = {
        "valid": invalid_rate <= effective_threshold,
        "threshold": effective_threshold,
        "n_attempted": n_attempted,
        "n_valid": n_valid,
        "n_invalid": n_invalid,
        "n_failed": len(failures or []),  # M4 #1524 (D5): cross-ref
        "invalid_rate": invalid_rate,
        "error_census": error_census,
        "checks": checks,
    }
    if integrity_justification:
        integrity["justified"] = True
        integrity["threshold_violation_justification"] = integrity_justification

    # ── Retry-then-fix protocol: census → mechanical-fix triage (M4 #1524,
    # D6 — documented, never gated: integrity.valid is REPORTED, not a
    # publish gate) ─────────────────────────────────────────────────────────
    # | Census signal            | Mechanical fix (run-protocol steps 4/6)   |
    # |--------------------------|------------------------------------------|
    # | fatal_402_billing > 0    | M2 pre-flight probe missed it → check     |
    # |                          | budget (A6), re-run pre-flight — not a   |
    # |                          | code bug                                 |
    # | transient_429 spike      | reduce --workers / raise backoff cap /   |
    # |                          | provider load                            |
    # | transient_timeout spike  | raise TORTOISE_EXTRACTOR_MAX_TOKENS or   |
    # |                          | reduce chunk_size (S1 output bound)      |
    # | parse_error spike        | S2/S4 prompt / OUTPUT_CONTRACT regression |
    # |                          | → fix prompt, not retries                |
    # | truncated > 0            | cap too low for the stage → raise the    |
    # |                          | stage cap (TORTOISE_EXTRACTOR_MAX_TOKENS) |
    # | fatal_401_auth /         | key rotation / provider config — pre-    |
    # | fatal_403_forbidden      | flight (M2) should have caught           |
    # ───────────────────────────────────────────────────────────────────────

    # ── M7 (D2): leg-mix — match_source aggregation, never re-derived ──
    leg_total: Counter = Counter()
    leg_shares: dict[str, list[float]] = defaultdict(list)
    unknown_count = 0
    n_legmix = 0
    for o in outcomes:
        lm = o.get("leg_mix") or {}
        if not lm:
            continue
        n_legmix += 1
        total = sum(lm.values()) or 1
        for leg, count in lm.items():
            leg_total[leg] += count
            leg_shares[leg].append(count / total)
        unknown_count += lm.get("unknown", 0)
    leg_mix = {
        "total_counts": dict(sorted(leg_total.items())),
        "mean_share": {
            leg: round(sum(v) / len(v), 4)
            for leg, v in sorted(leg_shares.items())},
        "unknown_count": unknown_count,
        "n_questions": n_legmix,
    }

    # ── M7 (D3): pool size — live graph point count per question ──
    pools = [float(o.get("pool_size") or 0) for o in outcomes]
    pool_size = (
        {"mean": round(sum(pools) / n, 2) if n else 0.0,
         "p50": round(_percentile(pools, 0.50), 2),
         "p95": round(_percentile(pools, 0.95), 2)}
        if pools else {})

    # ── M7 (D4): evidence written/retrieved + vacuity over evidence-bearing
    # questions only (ground-truth-absent abstentions excluded from the
    # denominator) at the design-locked k (top_k). evidence_written is the
    # per-outcome D4 number (deterministic → evidence_turns; v2 →
    # evidence_points); evidence_retrieved@k is the raw hit count (turn_recall
    # numerator) — independent of M6's N/A-per-question semantics. ──
    ev_written_outcomes = [o for o in outcomes
                           if (o.get("evidence_written") or 0) > 0]
    written_all = [float(o.get("evidence_written") or 0) for o in outcomes]
    k_key = str(top_k)
    retrieved_bearing = [
        float((o.get("evidence_retrieved@k") or {}).get(k_key, 0) or 0)
        for o in ev_written_outcomes]
    evidence = {
        "written_mean": (round(sum(written_all) / len(written_all), 2)
                         if written_all else 0.0),
        "retrieved_mean@k": {
            k_key: (round(sum(retrieved_bearing) / len(retrieved_bearing), 2)
                    if retrieved_bearing else None)},
        "evidence_bearing_n": len(ev_written_outcomes),
        "evidence_absent_n": len(outcomes) - len(ev_written_outcomes),
        "vacuity_rate": (round(
            sum(1.0 for v in retrieved_bearing if v == 0.0)
            / len(retrieved_bearing), 4) if retrieved_bearing else 0.0),
    }

    # ── context tokens ──
    ctx = [o["context_tokens"] for o in outcomes]
    ctx_mean = round(sum(ctx) / n, 1) if n else 0.0

    # ── latency (ms) ──
    def _lat(keys: list[str]) -> dict[str, float]:
        xs = []
        for o in outcomes:
            v = o
            for k in keys:
                v = v.get(k, 0.0)
            xs.append(float(v or 0.0))
        return {"mean_ms": round(sum(xs) / n, 2) if n else 0.0,
                "p50_ms": round(_percentile(xs, 0.50), 2),
                "p95_ms": round(_percentile(xs, 0.95), 2)} if xs else {}

    # M7 (Gate 2): a not-trusted audit serializes EVERY recall key to null —
    # the report then contains no recall numbers until the dataset is
    # re-audited (E2E-3 Precondition 2).
    def _gated(value):
        return value if trusted else None

    return {
        "benchmark": "LongMemEval",
        "dataset": dataset_id,
        "split": split,
        "n_questions": n,
        "accuracy": {
            "overall": overall,
            "task_averaged": task_averaged,
            "abstention": _mean([1.0 if l else 0.0 for l in abstention_labels]),  # noqa: E741
            "abstention_n": len(abstention_labels),
            "per_category": per_category,
            "per_type": per_type,
        },
        "retrieval": {
            "session_recall@k": _gated(session_recall),
            "turn_recall@k": _gated(turn_recall),
            "evidence_recall@k": _gated(evidence_recall or None),
            "evidence_recall_n@k": _gated(evidence_recall_n or None),
            "evidence_vacuity_rate@k": _gated(evidence_vacuity_rate or None),
            "evidence_coverage": evidence_coverage,
            "chunk_evidence_recall@k": _gated(chunk_evidence_recall or None),
            "chunk_evidence_recall_n@k": _gated(chunk_evidence_recall_n or None),
            # M7 (D10): paper-aligned aggregates over non-_abs only.
            "session_recall_paper@k": _gated(session_recall_paper),
            "turn_recall_paper@k": _gated(turn_recall_paper),
            "evidence_recall_paper@k": _gated(evidence_recall_paper),
            "context_tokens_mean": ctx_mean,
            "context_point_count_mean": round(
                sum(o["context_point_count"] for o in outcomes) / n, 2) if n else 0,
        },
        "latency_ms": {
            "retrieval": _lat(["retrieval_latency_ms"]),
            "reader": _lat(["reader_latency_ms"]),
            "judge": _lat(["judge_latency_ms"]),
            "ingest": _lat(["ingest_latency_ms"]),  # M7 (D5): write-path cost
            "total_per_question": _lat(["total_ms"]),
        },
        # M7 (D1/D2/D3/D4): the self-explanatory-report keys.
        "integrity": integrity,
        "leg_mix": leg_mix,
        "pool_size": pool_size,
        "evidence": evidence,
        "methodology": {
            "reader_prompt_hash": reader_prompt_hash,
            "judge_rubric_id_hash": judge_rubric_id_hash,
            "reader_model": reader_model,
            # M5 (#1525): the reader's resolved identity + verbatim prompt
            # constants — recorded so cross-cell/cross-run reader drift is
            # visible in the report (additive; reader_model stays for compat).
            "reader_model_spec": reader_model_spec,
            "reader_provider": reader_provider,
            "reader_pinned": reader_pinned,
            "reader_system_prompt": reader_system_prompt,
            "reader_type_fragments": reader_type_fragments or {},
            "judge_model": judge_model,
            "ingest_mode": ingest_mode,
            "judge_rule": "official LongMemEval get_anscheck_prompt; "
                          "label = 'yes' in response.lower()",
            "judge_call_shape": "official evaluate_qa.py: messages=[user], "
                                "n=1, temperature=0, max_tokens=10 — no "
                                "response_format (JSON mode), no system "
                                "message",
            "reader_context_format": "official gen.py shape: 'Current Date: "
                                     "{question_date}' header + per-session "
                                     "date annotation on every retrieved chunk "
                                     "(question_date + haystack_dates surfaced — "
                                     "temporal-reasoning questions are "
                                     "answerable); points-first budget-capped "
                                     "context (UX decision 3, R1 #1540): "
                                     "extracted points render in rank order, raw "
                                     "turn-granular chunks backfill the remaining "
                                     "context_token_cap tokens",
            "extraction_approach": extraction_approach,
            "retrieval": "Tortoise hybrid RRF (FTS+vector+structural, TF-IDF "
                         "fallback) over graph turn points + turn-granular raw "
                         "chunks (pointKind session-transcript, chunk_turns "
                         "turns per non-overlapping window; candidates fetched "
                         "at max(k)*3 depth, deduped per-session to "
                         "max_chunks_per_session raw chunks in rank order, R1 "
                         "#1540)",
            "retrieval_scope": "ISOLATED per-question corpus — each question's "
                               "haystack is ingested into a fresh graph and "
                               "recall is measured against that question alone; "
                               "NOT the official full-corpus indexing (official "
                               "retrievers index all questions' histories "
                               "together). Per-question recall@k is therefore "
                               "computed on a smaller, question-scoped corpus "
                               "and is not directly comparable to the paper's "
                               "recall numbers",
            "recall_definition": "session-level: fraction of answer_session_ids "
                                 "(evidence sessions) in top-k over the DEDUPED "
                                 "pool (R1 #1540: ret[hits] == the per-session-"
                                 "deduped pool; max_chunks_per_session raw chunks "
                                 "per session); turn-level: fraction of has_answer "
                                 "extracted points (pointKind <> "
                                 "session-transcript) in top-k — raw chunks are "
                                 "excluded from the turn/evidence numerator and "
                                 "denominator (D5, no granularity bias), with the "
                                 "deterministic evidence-turn-id fallback when the "
                                 "graph has no marks; evidence_recall@k = marked "
                                 "extracted points surfaced / marked extracted "
                                 "points total, N/A (None) on empty denominators "
                                 "(M6 #1526 — never forced 0.0); chunk containment "
                                 "is reported separately as chunk_evidence_recall@k "
                                 "(containment-marked raw chunks surfaced / marked "
                                 "raw chunks total); evidence_recall_n@k = "
                                 "evidence-bearing outcomes in the mean; "
                                 "evidence_vacuity_rate@k = fraction of "
                                 "evidence-bearing outcomes with 0.0 while "
                                 "evidence exists; evidence_coverage = fraction of "
                                 "evidence-bearing questions with ingest."
                                 "evidence_points > 0; M7 #1527: paper-aligned "
                                 "session/turn/evidence_recall_paper@k keys are "
                                 "the SAME per-question fraction over non-_abs "
                                 "questions only (official print_retrieval_metrics."
                                 "py excludes _abs; legacy keys keep the "
                                 "_abs-inclusive definition); evidence.vacuity_rate "
                                 "= share of evidence-bearing questions (ingest "
                                 "evidence_written > 0) with evidence_retrieved@k "
                                 "== 0 at top_k (evidence-absent abstentions "
                                 "excluded from the denominator); recall numbers "
                                 "are published only under the dataset recall-"
                                 "semantics audit (methodology.dataset_semantics_"
                                 "audit; a not-trusted verdict serializes recall "
                                 "to null); all measured over the isolated "
                                 "per-question corpus (see retrieval_scope)",
            "vacuity_band": "0/52 vacuous on healthy questions (fixture "
                            "calibration 2026-08-20)",
            "vacuity_band_anchor": "fixture calibration 2026-08-20 (0/52 "
                                   "vacuous); re-anchor at run protocol step 6",
            "token_estimator": "whitespace tokens + 10% markup allowance",
            "k_values": list(ks),
            "top_k_context": top_k,
            "dataset_source": dataset_id,
            "split": split,
            "git_sha": git_sha(),
            "run_at_utc": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
            # M7 (#1527, D7/D9/D10): what code + dataset + audit produced the
            # report (a report always says); the checkpoint fingerprint fields
            # are persisted here too.
            "python_version": python_version,
            "workers": workers,
            "dataset_fingerprint": dataset_fingerprint,
            "integrity_threshold": effective_threshold,
            "dataset_semantics_audit": dataset_semantics_audit,
            # R3 (#1542) D5: embedder identity + vector-strategy availability
            # — always present (default reason="not_checked" when omitted, so
            # no report is ever keyless about the dense leg).
            "embedder": embedder_status or dict(DEFAULT_EMBEDDER_STATUS),
            "vector_strategy": ("enabled"
                                if (embedder_status or {})
                                .get("available") else "unavailable"),
            **(r1_knobs or {}),
        },
        "failures": failures or [],
        "n_failed": len(failures or []),
        **(extra or {}),
    }


def save_report(report: dict[str, Any], path: Path | str) -> Path:
    """Write the report JSON (pretty-printed) and return the path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")
    return p


def default_report_path(split: str, *, output_dir: str | None = None) -> Path:
    """Default report path: output dir (or CWD) + timestamped filename."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017
    base = Path(output_dir) if output_dir else Path.cwd()
    return base / f"longmemeval_{split}_{stamp}.report.json"
