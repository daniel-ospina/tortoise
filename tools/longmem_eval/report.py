"""Report aggregation + methodology provenance (issue #1144, axis 2).

Aggregates per-question outcomes into the published report shape:
overall + task-averaged + per-category accuracy (the five paper abilities:
information extraction, multi-session reasoning, temporal reasoning,
knowledge updates, abstention), per-type accuracy (the six raw dataset
types), retrieval recall@k (session- and turn-level; paper-aligned _paper@k
keys over non-_abs questions, M7), context tokens, latency (incl. the
isolated ingest write-path cost, M7), an integrity block with the per-
question error census (M7) + a census-class-aware gate criterion (#1747),
leg-mix / pool-size / evidence written-·retrieved aggregates (M7) —
together with a full methodology block (dataset id, split,
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
import math
import os  # noqa: F401
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tortoise.embeddings import EMBEDDING_MODEL

from .dataset_audit import is_trusted

# R3 (#1542) D5: the dense-leg methodology is ALWAYS emitted — a report can
# never be keyless about the vector strategy (MemDelta pinning: embedder
# identity + availability ride in the methodology so a future swap or silent
# degradation is visible before any accuracy comparison). Programmatic
# callers (existing tests, battery/parity, the capstone harness) that omit
# embedder_status get the not_checked default.
DEFAULT_EMBEDDER_STATUS = {
    "model": EMBEDDING_MODEL,  # derived — single source of truth (#1349 swap)
    "sentence_transformers_version": None,
    "available": False,
    "reason": "not_checked",
}

# question_type → paper category (the five abilities from the LongMemEval
# paper; abstention is signalled by the ``_abs`` suffix, not a type).
#: #1747: census classes that are RECOVERABLE (rate-limited) — self-
#: correcting parse/truncation (the #1746 ladder) or transient provider
#: conditions (retry with backoff). A question whose ONLY error-class signals
#: are recoverable is still INVALID (it had extraction errors) but is
#: rate-limited by ``integrity.threshold`` instead of vetoing the run — this
#: is what makes ``valid=true`` reachable at 500-Q scale (≈24k session
#: extractions guarantee a handful of transient/parse blips even on a
#: healthy run). EVERYTHING not in this allowlist fails CLOSED: a fatal_*
#: class, a bare ``ingest`` class, or an unknown class from a future
#: extractor vocabulary vetoes the run rather than silently passing.
RECOVERABLE_CENSUS_CLASSES = frozenset({
    "parse_error",             # S2/S4 unparseable output (re-prompt ladder)
    "truncated",               # stage cap hit (raise-the-cap triage)
    "truncated_parse_error",   # #1746: truncated + unparseable after ladder
    "partial_parse",           # #1746: accepted-but-partial (invalid-but-embedded)
    "transient_429_rate_limit",  # provider rate limit (backoff, retry)
    "transient_5xx",           # provider 5xx (backoff, retry)
    "transient_timeout",       # call timeout (retry)
    "transient_network",       # connection/network error (retry)
    "transient_unknown",       # unclassified transient (retry-safe)
})

#: #1747: eval-failure classes that are transient-safe — the retry budget
#: was BURNED (``retries_exhausted``), so the question failed, but the cause
#: is recoverable → rate-limited like the census recoverable classes, not
#: vetoed. EXACT site-prefixed strings (errors.py emits
#: ``<site>:retries_exhausted`` for transient/unknown burns; ``ingest`` is
#: bare and permanent): a tampered suffix (``evil:retries_exhausted``) must
#: NOT match (fail-closed, security review). Everything else (``:fatal`` /
#: ``:fatal_config`` / ``:parse`` — a local decode bug — / bare ``ingest`` /
#: unclassified) is PERMANENT → hard veto.
RECOVERABLE_EVAL_FAILURE_CLASSES = frozenset({
    "reader:retries_exhausted",
    "judge:retries_exhausted",
})


def _outcome_grade(o: dict[str, Any]) -> str:
    """#1747: grade one COMPLETED outcome for the integrity gate.

    Returns ``"clean"`` / ``"recoverable"`` / ``"hard"``:

    * ``"hard"`` — a census class outside ``RECOVERABLE_CENSUS_CLASSES``
      (fatal_* / ingest / unknown — fail-closed), OR a NON-census error
      string with an EMPTY census (``valid=False`` + no census classes:
      no-embed-list / S5 failure / entity-resolution failure — structural
      degradation that no retry or ladder can recover, so it must not ride
      the rate threshold). NOTE (reviewer-pinned, #1747): a non-census
      structural string CO-OCCURRING with a recoverable census class cannot
      be distinguished here — the raw outcomes DO carry the error strings
      (``ingest_error_text`` / ``ingest.errors``), but the grading layer
      deliberately consumes only ``valid`` + ``error_classes`` (string-
      matching is brittle and count heuristics false-positive on the
      S1-chunk summary duplicate that double-reports already-census-bumped
      chunk failures), so that mixed shape grades recoverable
      (rate-limited). The realistic worst case is an S2 parse failure that
      cascades to the structural "no embed list produced" string — the
      question embeds zero points yet rides the rate; the extractor-side
      fix (classify structural strings into the census) is the #1746 lane.
      This limitation is documented here so the promise is scoped, not
      overstated.
    * ``"recoverable"`` — only recoverable census classes and the runner's
      own flag agrees the question is invalid (``valid=False``).
    * ``"clean"`` — no error signal; a recoverable-only census with the
      runner's flag ``valid=True`` is also clean (the runner's binary flag
      is the authority on whether error strings exist; the shape is
      unreachable with the current runner — every census bump pairs an
      ``errors.append`` — and is pinned as a drift guard).
    """
    ec = o.get("error_classes")
    if ec is None:
        ec = {}  # distinguish MISSING from falsy-but-present (0/""/False):
    # a falsy present value is malformed and must fail CLOSED to hard, not
    # collapse to an empty census (security review, #1747).
    if isinstance(ec, dict):
        # Class presence = KEY presence (security review, #1747). The count
        # VALUE never decides presence: a tampered checkpoint zeroing or
        # falsing a hard class's count (``{"fatal_402_billing": 0}`` /
        # ``false``) would otherwise launder it to clean — grader and record
        # must agree, and the extractor never emits zero/absent counts, so
        # ANY present key is a real (or anomalous, fail-closed) signal.
        # Malformed count values ride only the census roll-up
        # (``error_census_malformed``).
        classes = set(ec)
    else:  # legacy flat-list shape (defensive back-compat) — non-iterable /
        # unhashable values (malformed checkpoint JSON) fail CLOSED to hard
        # instead of crashing the report; a list carrying ANY non-str element
        # also fails closed (mirrors the dict branch's non-str-key posture,
        # security review, #1747).
        if not isinstance(ec, (list, tuple, set, frozenset)):
            return "hard"
        if any(not isinstance(c, str) for c in ec):
            return "hard"
        classes = {c for c in ec if c}
    if classes - RECOVERABLE_CENSUS_CLASSES:
        return "hard"
    # The runner's binary ``valid`` flag must be a REAL bool when PRESENT: a
    # present non-bool flag (``"valid": "false"`` from a schema-less
    # checkpoint) is malformed input and fails CLOSED to hard — truthiness
    # coercion would fail OPEN and certify a structurally-degraded run as
    # clean (security review, #1747). A MISSING flag keeps the historical
    # back-compat default True (full_context cell outcomes and legacy pre-M7
    # checkpoints carry no flag; a tampered checkpoint can always clear
    # ``error_classes`` anyway — same trust model, documented).
    flag = o.get("valid", True)
    if not isinstance(flag, bool):
        return "hard"
    if classes:
        return "recoverable" if not flag else "clean"
    # census empty — the runner's binary flag is the only error signal.
    return "hard" if not flag else "clean"


def _failure_grade(error_class: Any) -> str:
    """#1747: grade one eval ``failures`` entry. ``"recoverable"`` iff the
    class is EXACTLY a transient-safe site-prefixed ``retries_exhausted``
    (the retry budget was burned, but the cause is recoverable → rate-limited
    like the census recoverable classes). Everything else — permanent
    classes (``:fatal`` / ``:fatal_config`` / ``:parse``), bare ``ingest``,
    a missing class, a non-string value, or a tampered suffix like
    ``evil:retries_exhausted`` — is ``"hard"`` (fail-closed; security
    review, #1747)."""
    if isinstance(error_class, str) and error_class in RECOVERABLE_EVAL_FAILURE_CLASSES:
        return "recoverable"
    return "hard"


PAPER_CATEGORY = {
    "single-session-user": "Information Extraction",
    "single-session-assistant": "Information Extraction",
    "single-session-preference": "Information Extraction",
    "multi-session": "Multi-Session Reasoning",
    "temporal-reasoning": "Temporal Reasoning",
    "knowledge-update": "Knowledge Updates",
}


def category_of(question: dict) -> str:
    # #1747: a non-str question_id (malformed checkpoint JSON) categorizes as
    # "Other" instead of crashing the ``"_abs" in qid`` check.
    if not isinstance(question.get("question_id"), str):
        return "Other"
    if "_abs" in question["question_id"]:
        return "Abstention"
    return PAPER_CATEGORY.get(question["question_type"], "Other")


def _mean(xs: list[float]) -> float:
    # #1747 (round-7): None entries are SKIPPED — a malformed checkpoint value
    # can never TypeError the sum; callers that rely on N/A-drop semantics
    # (turn/evidence recall) get it here instead of pre-filtering.
    real = [x for x in xs if x is not None]
    return round(sum(real) / len(real), 4) if real else 0.0


def _percentile(xs: list[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo = int(math.floor(k))  # noqa: RUF046
    hi = int(math.ceil(k))  # noqa: RUF046
    if lo == hi:
        return xs[lo]
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k/n, no continuity correction.

    M8 (#1528) D1 — stdlib-only (the eval toolchain has zero third-party
    deps; no scipy/numpy). Pinned to the validity-synthesis
    recomputations (/tmp/v3-synth/02-validity.md):
    (20,28) -> (0.529, 0.847); (63,121) -> (0.432, 0.608);
    (72,133) -> (0.457, 0.624).
    """
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    half = (z / denom) * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n))
    return (round(max(0.0, center - half), 3), round(center + half, 3))


def mcnemar_exact(w: int, l: int) -> float:  # noqa: E741 — w/l = A-wins/B-wins
    """Two-sided exact McNemar p-value on discordant pairs (exact binomial).

    M8 (#1528) D1 — w = pairs where run A correct / run B wrong (B lost);
    l = the converse (B won). p = 2 * P(X <= min(w, l)) for
    X ~ Binomial(w+l, 0.5), capped at 1.0. Integer arithmetic via
    math.comb — no float-overflow risk at n <= 500.
    Pinned: (4,1)->0.375, (11,10)->1.0, (19,18)->1.0, (3,15)->0.0075,
    (1,0)->1.0.
    """
    n = w + l
    if n == 0:
        return 1.0
    k = min(w, l)
    p = sum(math.comb(n, i) for i in range(k + 1)) / (2 ** n)
    return min(1.0, 2.0 * p)


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
    # R5 (#1544) D7: the TR knob values (tr_top_k / tr_date_weight /
    # tr_events) — recorded in the methodology via the same spread pattern
    # as ``r1_knobs`` (published numbers carry their methodology).
    r5_knobs: dict[str, Any] | None = None,
    # R6 (#1545): the effective rerank config + pre-warm outcome (None on
    # baseline runs → the report carries ZERO rerank keys — the
    # no-flag-report contract, D2).
    rerank_config: dict[str, Any] | None = None,
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
    # #1349 vector arm: retriever/model/query_prompt/retrieval_only/surface/
    # run_key — recorded in the methodology + provenance so a gate report
    # always says which retriever + injected model produced it.
    retriever: str = "hybrid",
    model: str | None = None,
    query_prompt: str | None = None,
    retrieval_only: bool = False,
    surface: str = "embedded",
    run_key: str | None = None,
) -> dict[str, Any]:
    """Aggregate per-question outcomes into the report + provenance dict.

    ``outcomes`` must contain only COMPLETED questions (failed questions are
    passed via ``failures`` and reported separately — a transient LLM error
    on one question must not abort the run or skew the aggregates).

    M7 (#1527) contract additions (additive-only; D11):
      * ``integrity`` — validity + per-question error census (D1/D6): the
        gate criterion is CENSUS-CLASS-AWARE (#1747) — ``valid == True`` iff
        ``invalid_rate <= threshold`` AND zero hard-failure questions;
        recoverable classes (parse_error / truncated / truncated_parse_error
        / partial_parse / transient_*, plus reader/judge:retries_exhausted
        eval failures) are rate-limited (a healthy 500-Q run
        admits a handful — the OLD binary ``len(errors)==0`` per-question
        invalid made ``valid=true`` unreachable at scale); hard classes
        (fatal_* / ingest / unknown census classes, non-census error strings
        with an EMPTY census, permanent eval failures, malformed inputs —
        a present non-bool valid flag / non-iterable or non-str error_classes
        — fail closed to hard) veto the run at any threshold (a mixed
        recoverable+structural shape is rate-limited —
        the #1746 lane); additive breakdown fields ``n_hard_invalid`` /
        ``n_recoverable_invalid`` / ``recoverable_invalid_rate`` /
        ``n_excluded`` (outcomes dropped by the entry shape filter — the
        denominator shrink is observable, never silent) /
        ``criterion`` ride the block, and malformed non-int census counts
        are preserved verbatim in ``error_census_malformed`` (never mixed
        into ``error_census``, never crashing the report); the numbers are
        always recorded, so no degraded run can masquerade as clean.
        Relationship to issue #1746 (its plan doc, D10 — "the flag's
        semantics are #1747's lane"; the plan file lands with #1746): that
        plan deliberately does NOT make ``integrity.valid`` its closing
        condition — the flag's semantics are this issue's lane; the
        run-protocol step-5 gate string (run_protocol.py) states the
        justified threshold for the 500-Q baseline.
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
    # #1747 entry normalization (security review): entries that cannot be
    # aggregated are EXCLUDED (the report ALWAYS builds and serializes):
    # non-dict entries, and dict outcomes missing the keys the aggregation
    # dereferences directly (label / session_recall@k / turn_recall@k as
    # dicts / numeric context_tokens / context_point_count) — a malformed
    # checkpoint outcome that passes run.py's presence-only loader gate would
    # otherwise KeyError/AttributeError mid-report (security-review P1;
    # loader-side REQUIRED_OUTCOME_KEYS hardening is tracked in #1770).
    # Outcomes are copied with error_classes keys str()-coerced so
    # json.dumps(sort_keys=True) never TypeErrors on a programmatic
    # mixed-type key (identity for JSON checkpoints, whose keys are always
    # strings). Non-str question_ids are NOT dropped — they are graded under
    # collision-proof tuple keys so a hard census on a malformed-qid outcome
    # still VETOES.
    def _num(v: Any) -> bool:
        """numeric-or-None (bool EXCLUDED — a tampered checkpoint true/false
        must fail closed, never aggregate as 1.0/0.0; round-8 security
        review) — safe for float(v or 0.0) / skip-None sites."""
        return v is None or (isinstance(v, (int, float))
                             and not isinstance(v, bool))

    def _recall_dict(v: Any, *, allow_none_values: bool = True) -> bool:
        """recall dicts: dict with numeric values; None values are allowed
        where the aggregation drops them (turn/evidence N/A semantics),
        disallowed where they are summed directly (session recall). A None
        VALUE (missing key) is admitted ONLY for keys the aggregation
        dereferences via ``or {}`` (evidence/chunk — genuinely None-safe);
        the keys dereferenced via ``o[...]`` (session/turn/context) use the
        REAL-dict check ``_recall_dict_present`` below — never this
        None-tolerant guard (round-7 root cause)."""
        if v is None:
            return True
        if not isinstance(v, dict):
            return False
        for x in v.values():
            if allow_none_values and x is None:
                continue
            if not isinstance(x, (int, float)) or isinstance(x, bool):
                return False
        return True

    def _recall_dict_present(o: dict[str, Any], key: str,
                             *, allow_none_values: bool = False) -> bool:
        """session/turn recall are dereferenced via o[...] (no or-{}
        fallback) — the key must be a PRESENT dict. Session values are summed
        directly (no N/A → numeric only); turn values may be None (M6 N/A,
        dropped from the mean). bools are excluded everywhere (round-8
        security review: a tampered true must never aggregate as 1.0)."""
        v = o.get(key)
        if not isinstance(v, dict):
            return False
        for x in v.values():
            if allow_none_values and x is None:
                continue
            if not isinstance(x, (int, float)) or isinstance(x, bool):
                return False
        return True

    def _leg_mix_ok(v: Any) -> bool:
        """leg_mix: dict of NUMERIC (non-None) values with str keys — the
        aggregation sums the values directly and sorts the keys, so None
        values or non-str keys (programmatic mixed-type) must be excluded."""
        if v is None:
            return True
        return (isinstance(v, dict)
                and all(isinstance(k, str) for k in v)
                and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                        for x in v.values()))

    def _ingest_ok(v: Any) -> bool:
        """ingest stats: dict-or-None; evidence_turns / evidence_points are
        compared with > 0, so when PRESENT they must be numeric (not None,
        not bool — round-8 security review)."""
        if v is None:
            return True
        return (isinstance(v, dict)
                and all(isinstance(v.get(k), (int, float))
                        and not isinstance(v.get(k), bool)
                        for k in ("evidence_turns", "evidence_points")
                        if k in v))

    def _rerank_pass_ok(v: Any) -> bool:
        """rerank_pass (rerank runs only): every field the aggregation
        dereferences must be coercible — degrade_reason str-or-None;
        max_session_chunks/moved/dropped/selected_count numeric-or-None;
        pool_recall@k None or a dict whose per-level values are dicts of
        numeric-or-None (security review P1, rerank crash family)."""
        if v is None:
            return True
        if not isinstance(v, dict):
            return False
        if (v.get("degrade_reason") is not None
                and not isinstance(v["degrade_reason"], str)):
            return False
        for k in ("max_session_chunks", "moved", "dropped", "selected_count"):
            x = v.get(k)
            if x is not None and (not isinstance(x, (int, float))
                                  or isinstance(x, bool)):
                return False
        pr = v.get("pool_recall@k")
        if pr is None:
            return True
        if not isinstance(pr, dict):
            return False
        for lvl in pr.values():
            if lvl is None:
                continue
            if not isinstance(lvl, dict):
                return False
            if not all(x is None
                       or (isinstance(x, (int, float)) and not isinstance(x, bool))
                       for x in lvl.values()):
                return False
        return True

    def _outcome_shape_ok(o: dict[str, Any]) -> bool:
        """Every key the aggregation dereferences directly must be present
        with a coercible type — a malformed checkpoint outcome that passes
        run.py's presence-only loader gate is EXCLUDED here instead of
        crashing mid-report (security-review P1; the loader-side
        REQUIRED_OUTCOME_KEYS type-hardening is tracked in #1770). Note the
        asymmetry: session_recall@k VALUES are summed directly (no N/A), so
        None values are excluded; turn/evidence recall values are dropped
        when None (M6 N/A semantics), so None values are allowed; context
        keys are dereferenced via o[...] and must be PRESENT and numeric."""
        return ("question_id" in o
                and "label" in o
                and isinstance(o.get("question_type", ""), str)
                and _recall_dict_present(o, "session_recall@k")
                and _recall_dict_present(o, "turn_recall@k",
                                         allow_none_values=True)
                and _recall_dict(o.get("evidence_recall@k"))
                and _recall_dict(o.get("chunk_evidence_recall@k"))
                and _recall_dict(o.get("evidence_retrieved@k"))
                and (o.get("context_tokens") is not None
                     and _num(o.get("context_tokens")))
                and (o.get("context_point_count") is not None
                     and _num(o.get("context_point_count")))
                and all(_num(o.get(k)) for k in (
                    "pool_size", "evidence_written", "retrieval_latency_ms",
                    "rerank_latency_ms", "reader_latency_ms",
                    "judge_latency_ms", "ingest_latency_ms", "total_ms"))
                and all(_num(o.get(k)) for k in ("ndcg@10", "p@10", "p@5"))
                and _leg_mix_ok(o.get("leg_mix"))
                and _ingest_ok(o.get("ingest"))
                and _rerank_pass_ok(o.get("rerank_pass")))

    raw_n = len(outcomes)
    # #1747 (round-8 review): non-dict failure entries are junk too — they
    # count into n_excluded (the same "never silent" observability as
    # outcome junk) instead of vanishing from the record.
    n_failure_junk = sum(1 for f in (failures or []) if not isinstance(f, dict))
    outcomes = [o for o in outcomes if isinstance(o, dict)]
    # #1349: questions dropped by the vector arm (breaker_open) are excluded
    # from the means and surfaced in ``dropped`` — never recall 0. The
    # breaker_open split runs BEFORE the shape filter: dropped questions may
    # legitimately lack retrieval keys (no retrieval was run) and never reach
    # the aggregations. n_excluded counts EVERY excluded entry (non-dict
    # junk in outcomes AND failures + shape-broken dicts) so the denominator
    # shrink is observable. VETO-ESCAPE GUARD (security-review P1): every
    # non-mean outcome is still graded for the hard veto — a hard census on
    # a malformed outcome (e.g. a truncated checkpoint that lost a recall
    # key) must still veto; only the MEANS/accuracy use the shape-filtered
    # set.
    all_outcomes = [o for o in outcomes if isinstance(o, dict)]
    dropped = [o for o in all_outcomes if o.get("breaker_open")]
    gradable = [o for o in all_outcomes if not o.get("breaker_open")]
    # single-pass grading (round-8 architecture review): shape + grade are
    # computed ONCE per outcome so the shape filter and the veto scan can
    # never disagree on a stale second evaluation.
    graded = [(o, _outcome_shape_ok(o), _outcome_grade(o)) for o in gradable]
    shape_ok = [o for o, ok, _ in graded if ok]
    n_excluded = raw_n - len(dropped) - len(shape_ok) + n_failure_junk
    outcomes = [dict(o) for o in shape_ok]
    for o in outcomes:
        ec = o.get("error_classes")
        if isinstance(ec, dict):
            o["error_classes"] = {str(k): v for k, v in ec.items()}
    failures = [f for f in (failures or []) if isinstance(f, dict)]
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
        # #1747: a non-str question_id (malformed checkpoint JSON) must not
        # TypeError the abstention filter — guard, don't crash.
        if isinstance(q["question_id"], str) and "_abs" in q["question_id"]:
            abstention_labels.append(o["label"])

    # M8 (#1528) D3: every published accuracy carries its 95% Wilson CI
    # (additive — ``overall``/``abstention``/``per_category`` accuracies stay
    # floats; the interval rides beside them, so existing consumers reading
    # ``accuracy``/``n`` only are untouched).
    def _ci95(correct: int, n: int) -> list[float]:
        return list(wilson_ci(correct, n))

    per_category = {c: {"accuracy": _mean([1.0 if l else 0.0 for l in ls]),  # noqa: E741
                        "n": len(ls),
                        "ci95": _ci95(sum(1 for l in ls if l),  # noqa: E741
                                      len(ls))}
                    for c, ls in sorted(by_category.items())}
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
        # #1747 (round-7): session/turn recall are dereferenced via .get with
        # None-safe fallbacks — the shape filter excludes malformed outcomes,
        # and the aggregation stays crash-proof regardless (a truncated
        # checkpoint must never AttributeError mid-report).
        sr = [(o.get("session_recall@k") or {}).get(str(k), 0.0)
              for o in outcomes]
        session_recall[str(k)] = _mean(sr)
        # M6 (#1526): N/A (None) outcomes are DROPPED from the turn-level
        # mean too — a None coerced to 0.0 silently re-drags the vacuity the
        # epic excludes (bug-pattern flag 4).
        tr = [(o.get("turn_recall@k") or {}).get(str(k)) for o in outcomes]
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
                      if not (isinstance(o.get("question_id"), str)
                              and "_abs" in o.get("question_id", ""))]

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

    # ── #1349 vector-arm metrics (binary-gain nDCG@10 + P@10/P@5) ──
    def _vmean(key: str):
        vals = [o.get(key) for o in outcomes if o.get(key) is not None]
        return _mean(vals) if vals else None
    ndcg10 = _vmean("ndcg@10")
    p10 = _vmean("p@10")
    p5 = _vmean("p@5")

    # ── M7 (D1) + #1747: integrity — census-class-aware gate criterion ──
    # n_attempted dedups by qid across outcomes+failures. Each qid is graded
    # ONCE via a qid-keyed map (outcome grade, overridden by a failure grade
    # when a qid appears in both — failure-grade dominance: a question with a
    # failure entry is invalid, and hard beats recoverable/clean):
    #   hard       — fatal_*/ingest/unknown census classes OR a non-census
    #                error string with an empty census (structural
    #                degradation) → VETOES the run at any threshold
    #   recoverable — only parse_error/truncated/truncated_parse_error/
    #                partial_parse/transient_* classes with the runner flag
    #                valid=False → INVALID but rate-limited (threshold)
    #   clean      — no error signal; a recoverable-only census with the
    #                runner flag valid=True also grades clean (drift-pin:
    #                test_report_integrity_recoverable_census_with_runner_
    #                clean_is_valid — the runner's binary flag is the
    #                authority on whether error strings exist)
    # Eval ``failures`` are graded by their exact site-prefixed class:
    # permanent (fatal/fatal_config/parse/ingest) → hard veto;
    # transient-safe (reader/judge:retries_exhausted) → recoverable.
    # Grading by qid (not per-entry) makes the invariant
    # n_hard_invalid + n_recoverable_invalid == n_invalid hold BY
    # CONSTRUCTION for every input — including the concurrent
    # checkpoint-merge overlap (a qid completed by one worker and failed by
    # another lands in both lists; the OLD per-entry sum broke the
    # n_valid + n_invalid == n_attempted invariant — an overlapped failure
    # was under-counted as silently clean, and duplicate outcome entries for
    # one qid drove n_invalid negative — history-review P1, #1747). ``n_valid`` /
    # ``n_invalid`` / ``invalid_rate`` keep their previous semantics for the
    # production shape (n_invalid = every error-carrying or failed question)
    # — the runner sets ``valid=False`` whenever error strings exist, so
    # ``valid=True`` never co-occurs with a hard census class (drift shape
    # pinned in test_report_integrity_hard_census_on_runner_clean_grades_hard);
    # only the ``valid`` VERDICT gains the hard veto on top of the rate
    # criterion.
    effective_threshold = float(integrity_threshold or 0.0)
    # #1747 grading: each qid is graded ONCE under a stable key. Str qids
    # key by value; non-str qids (malformed checkpoint JSON) key by a
    # COLLISION-PROOF tuple — a real string question_id can never equal a
    # tuple, so a crafted qid like "<anon:0>" cannot overwrite a sentinel
    # grade (security review), and value-identical malformed qids dedupe
    # across outcomes and failures (hashable values) so failure-grade
    # dominance applies to them too. Collisions on duplicate qids merge by
    # MAX severity (hard > recoverable > clean) — a hard grade is never
    # overwritten by a weaker one.
    _SEV = {"clean": 0, "recoverable": 1, "hard": 2}

    def _qid_key(o: dict[str, Any]) -> tuple | str:
        qid = o.get("question_id")
        if isinstance(qid, str):
            return qid
        if qid is None:
            # a MISSING qid is not a shared identity — per-object key so
            # distinct unknown-question entries never undercount (reviewer-
            # pinned, #1747).
            return ("__anon__", id(o))
        try:
            hash(qid)
        except TypeError:  # unhashable (list/dict) — per-object key
            return ("__anon__", id(o))
        # value-identical malformed qids share a key across outcomes AND
        # failures so failure-grade dominance applies to them too.
        return ("__anon__", qid)

    def _merge_grade(prev: str | None, grade: str) -> str:
        if prev is None or _SEV[grade] >= _SEV[prev]:
            return grade
        return prev

    grade_by_qid: dict[Any, str] = {}
    # #1747 grading: the ATTEMPTED set = well-shaped outcomes + failures —
    # the existing semantics (n_attempted / n_valid / n_invalid / invalid_rate
    # keep the trusted denominator; n_excluded surfaces the shape-filtered
    # shrink, never silent). Grades come from the single-pass ``graded`` list.
    for o, ok, grade in graded:
        if not ok:
            continue  # excluded from the attempted set (n_excluded)
        key = _qid_key(o)
        grade_by_qid[key] = _merge_grade(grade_by_qid.get(key), grade)
    for f in (failures or []):
        key = _qid_key(f)
        fg = _failure_grade(f.get("error_class"))
        grade_by_qid[key] = _merge_grade(grade_by_qid.get(key), fg)
    # VETO-ESCAPE GUARD (security review, #1747): every outcome EXCLUDED from
    # the attempted set (shape-broken dict outcomes AND breaker_open vector-
    # arm drops) is still graded for the HARD veto — a malformed outcome
    # carrying a hard census class (a truncated checkpoint that lost a recall
    # key, or a tampered checkpoint laundering a hard class under the breaker
    # flag) cannot launder a fatal class out of the gate. Only the hard grade
    # vetoes: recoverable classes on an excluded shape ride neither the rate
    # (excluded denominator) nor the veto — the census roll-up below still
    # records them as evidence. Published as ``n_excluded_hard`` so the veto
    # is self-explanatory (n_hard_invalid excludes excluded outcomes by
    # design; an operator never faces an unexplained valid=false).
    n_excluded_hard = 0
    for _, ok, grade in graded:
        if not ok and grade == "hard":
            n_excluded_hard += 1
    for o in dropped:
        if _outcome_grade(o) == "hard":
            n_excluded_hard += 1
    n_hard_invalid = sum(1 for g in grade_by_qid.values() if g == "hard")
    n_recoverable_invalid = sum(
        1 for g in grade_by_qid.values() if g == "recoverable")
    n_valid = sum(1 for g in grade_by_qid.values() if g == "clean")
    n_invalid = n_hard_invalid + n_recoverable_invalid
    n_attempted = len(grade_by_qid)
    invalid_rate = round(n_invalid / n_attempted, 4) if n_attempted else 0.0
    recoverable_invalid_rate = (
        round(n_recoverable_invalid / n_attempted, 4) if n_attempted else 0.0)
    census: Counter = Counter()
    malformed_census: dict[str, Any] = {}
    # census over ALL non-dict dict outcomes (gradable shape-broken AND
    # breaker_open drops included) so excluded outcomes' error classes are
    # still recorded as evidence (the veto-escape guard's census-side
    # mirror — a tampered checkpoint cannot hide a fatal class from the
    # record by also breaking the shape or setting the breaker flag).
    for o in all_outcomes:
        ec = o.get("error_classes")
        if ec is None:
            ec = {}  # falsy-but-present values (0/""/False) are malformed
            # → fail closed in the grader; the roll-up treats them as absent.
        if isinstance(ec, dict):
            for cls, count in ec.items():
                # Collision-safe roll-up (reviewer-pinned, #1747): an int
                # count sums into the typed accumulator under its str() key
                # (JSON keys are always strings — str()-coercion keeps the
                # published census and json.dumps(sort_keys=True) consistent
                # for programmatic non-str keys, security review); EVERY
                # other count value (None / str / float / bool / list /
                # dict — malformed JSON) is preserved in the separate
                # ``error_census_malformed`` field (accumulated per class so
                # no malformed evidence vanishes). Grading is presence-by-key
                # and independent of these fields (the veto cannot be
                # laundered through them).
                if isinstance(count, int) and not isinstance(count, bool):
                    census[str(cls)] += count
                else:
                    key = str(cls)
                    if key not in malformed_census:
                        malformed_census[key] = count
                    elif isinstance(malformed_census[key], list):
                        if count not in malformed_census[key]:
                            malformed_census[key].append(count)
                    elif malformed_census[key] != count:
                        malformed_census[key] = [malformed_census[key], count]
        else:  # legacy flat-list shape (defensive back-compat): str elements
            # ride the census; non-str elements are evidence-preserved in
            # error_census_malformed (the grader fails the whole shape closed
            # to hard — mirroring the dict branch's non-str-key posture).
            if isinstance(ec, (list, tuple, set, frozenset)):
                census.update([str(c) for c in ec if isinstance(c, str) and c])
                junk = [c for c in ec if not isinstance(c, str)]
                if junk:
                    # accumulate across outcomes (the dict branch's merge
                    # logic) — a later outcome's junk must not overwrite an
                    # earlier one's.
                    prev = malformed_census.get("<legacy-list>")
                    if prev is None:
                        malformed_census["<legacy-list>"] = junk
                    elif isinstance(prev, list) and isinstance(junk, list):
                        prev.extend(junk)
    for f in (failures or []):
        eclass = f.get("error_class")
        if isinstance(eclass, str) and eclass:
            census[eclass] += 1
    error_census = dict(sorted(census.items(), key=lambda kv: str(kv[0])))
    checks = [
        "python >= 3.12 guard enforced at run entry",
        "dataset loaded and recall-semantics audited",
        "dataset semantics audit present (publication gate)",
        "checkpoint fingerprint matched (no stale resume)",
        "per-question error census computed",
    ]
    integrity: dict[str, Any] = {
        # #1747: hard failures VETO at any threshold; the recoverable-class /
        # structural error RATE then rides the declared threshold. Threshold
        # 0.0 = a fully clean run (the strict default); the run-protocol
        # step-5 gate documents 0.02 as the justified default at 500-Q scale.
        "valid": ((n_hard_invalid == 0) and (n_excluded_hard == 0)
                  and (invalid_rate <= effective_threshold)
                  # round-8 security review: a run whose ENTIRE outcome set
                  # was excluded (n_attempted == 0 with n_excluded > 0 — a
                  # wholesale corrupt/version-drifted checkpoint) must never
                  # certify valid on an empty denominator; a truly EMPTY
                  # report (nothing excluded) stays vacuously valid.
                  and not (n_attempted == 0 and n_excluded > 0)),
        "threshold": effective_threshold,
        "n_attempted": n_attempted,
        "n_excluded": n_excluded,  # #1747: entries dropped by the entry
        # shape filter (malformed checkpoint JSON in outcomes or failures) —
        # the denominator shrink is observable, never silent (history-review
        # P2).
        "n_excluded_hard": n_excluded_hard,  # #1747 (round-8): hard grades
        # on outcomes EXCLUDED from the attempted set (shape-broken dicts +
        # breaker_open drops) — these veto the run; published so the veto is
        # self-explanatory when n_hard_invalid == 0 yet valid == false.
        "n_valid": n_valid,
        "n_invalid": n_invalid,
        "n_failed": len(failures or []),  # M4 #1524 (D5): cross-ref
        "invalid_rate": invalid_rate,
        # #1747 additive breakdown: hard-invalid questions (veto) vs
        # recoverable-class questions (rate-limited) — the two never overlap;
        # n_hard_invalid + n_recoverable_invalid == n_invalid.
        "n_hard_invalid": n_hard_invalid,
        "n_recoverable_invalid": n_recoverable_invalid,
        "recoverable_invalid_rate": recoverable_invalid_rate,
        "error_census": error_census,
        "error_census_malformed": (dict(sorted(malformed_census.items(),
                                                key=lambda kv: str(kv[0])))
                                    if malformed_census else {}),
        "criterion": (
            "#1747 census-class-aware: valid = (n_hard_invalid == 0) AND "
            "(n_excluded_hard == 0) AND (invalid_rate <= threshold) AND "
            "(attempted set non-empty whenever any entry was excluded); "
            "hard = fatal_*/ingest/unknown census classes + non-census error "
            "strings with an EMPTY census (mixed recoverable+structural "
            "grades recoverable — #1746 lane) + permanent eval failures + "
            "malformed inputs (present non-bool valid flag, non-iterable or "
            "non-str error_classes, falsy-but-present error_classes) fail "
            "closed to hard + excluded outcomes (shape-broken dicts / "
            "breaker_open drops) with a hard census still veto "
            "(n_excluded_hard); "
            "recoverable = parse_error/truncated/truncated_parse_error/"
            "partial_parse/transient_* census classes + "
            "reader/judge:retries_exhausted eval failures (rate-limited, "
            "not vetoed)"
        ),
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
    # #1747 (round-7): .get with a numeric default — a missing/None
    # context_tokens can never KeyError/TypeError the mean (the shape filter
    # excludes such outcomes; this is the None-safe fallback layer).
    ctx = [o.get("context_tokens", 0) for o in outcomes]
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

    # ── R6 (#1545): rerank aggregation over the per-question rerank_pass ──
    # (only on rerank runs — ``rerank_config`` is None on baseline, so the
    # report carries zero rerank keys including the latency block).
    rerank_agg: dict[str, Any] | None = None
    rerank_report_block: dict[str, Any] | None = None
    if rerank_config is not None:
        applied = [o.get("rerank_pass") or {} for o in outcomes]
        applied_ok = [rp for rp in applied if rp.get("applied")]
        degraded = [rp for rp in applied if not rp.get("applied")]
        reasons: list[str] = []
        seen: set[str] = set()
        for rp in degraded:
            reason = (rp.get("degrade_reason") or "").strip() or "unknown"
            if reason not in seen:
                seen.add(reason)
                reasons.append(reason)
        mcs = [float(rp.get("max_session_chunks") or 0) for rp in applied_ok]
        pool_recall_mean: dict[str, dict[str, float]] = {}
        # #1747 (round-7 finding 5): pool_recall@k must be a DICT (and each
        # per-level value a dict of numeric-or-None) — a malformed non-dict
        # value (float/list/str from a truncated checkpoint) is skipped, never
        # `.get`/`.items()`-ed into an AttributeError. The shape filter
        # excludes such outcomes; this keeps the aggregation crash-proof even
        # if a future call site forgets the filter.
        carriers = [rp.get("pool_recall@k") for rp in applied_ok
                    if isinstance(rp.get("pool_recall@k"), dict)]
        if carriers:
            for level in ("session", "turn", "evidence"):
                ks_lists: dict[str, list[float]] = {}
                for cr in carriers:
                    lvl = cr.get(level)
                    if not isinstance(lvl, dict):
                        continue
                    for k, v in lvl.items():
                        if isinstance(v, (int, float)) and not isinstance(v, bool):
                            ks_lists.setdefault(str(k), []).append(float(v))
                pool_recall_mean[level] = {
                    str(k): round(sum(v) / len(v), 4)
                    for k, v in sorted(ks_lists.items())}
        rerank_agg = {
            "applied_fraction": (round(len(applied_ok) / n, 4)
                                 if n else 0.0),
            "degraded_n": len(degraded),
            "degraded_fraction": (round(len(degraded) / n, 4)
                                  if n else 0.0),
            "sample_reasons": reasons[:5],
            "mean_moved": round(sum(float(rp.get("moved") or 0)
                                     for rp in applied_ok) / len(applied_ok), 2)
                if applied_ok else 0.0,
            "mean_dropped": round(sum(float(rp.get("dropped") or 0)
                                      for rp in applied_ok) / len(applied_ok), 2)
                if applied_ok else 0.0,
            "mean_selected_count": round(
                sum(float(rp.get("selected_count") or 0)
                    for rp in applied_ok) / len(applied_ok), 2)
                if applied_ok else 0.0,
            # E2E-10 assertion 2: the MAX over questions (a mean would mask
            # a single violating question).
            "max_session_chunks_max": round(max(mcs), 2) if mcs else 0.0,
            "pool_recall_mean@k": pool_recall_mean,
        }
        # The report's rerank block = effective config + pre-warm + aggregates
        # (spread into ``extra`` by the caller — the block rides the report at
        # top level like ``outcomes``/``preflight``).
        rerank_report_block = {
            "rerank": {
                "enabled": rerank_config.get("enabled"),
                "model": rerank_config.get("model"),
                "lambda_": rerank_config.get("lambda_"),
                "per_session_cap": rerank_config.get("per_session_cap"),
                "pool_size": rerank_config.get("pool_size"),
                "model_load_ms": rerank_config.get("model_load_ms"),
                "prewarmed": rerank_config.get("prewarmed"),
                **rerank_agg,
            }
        }

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
        # #1747: entries dropped by the entry shape filter (non-dict junk +
        # shape-broken dicts, outcomes AND failures) — published at top level
        # so gate_1349/compare_reports can reconcile n_questions vs
        # len(outcomes) (round-8 architecture review: the runner's
        # extra["outcomes"] Layer-1 projection can override the filtered
        # list, so the divergence is observable here, never silent).
        "n_excluded": n_excluded,
        # #1349: dropped-question accounting — emitted ONLY when a question
        # was dropped (breaker_open), so the zero-dropped report shape is
        # byte-identical to origin's published contract (golden-shape pin).
        **({"dropped": {
                "n": len(dropped),
                "breaker_open": sum(1 for o in dropped
                                     if o.get("dropped_reason") == "breaker_open"),
                "questions": [o.get("question_id") for o in dropped],
            },
            "n_dropped": len(dropped)} if dropped else {}),
        "accuracy": (
            None if retrieval_only else {
            "overall": overall,
            "ci95": _ci95(sum(1 for l in labels if l), n),  # noqa: E741
            "task_averaged": task_averaged,
            "abstention": _mean([1.0 if l else 0.0 for l in abstention_labels]),  # noqa: E741
            "abstention_ci95": _ci95(
                sum(1 for l in abstention_labels if l),  # noqa: E741
                len(abstention_labels)),
            "abstention_n": len(abstention_labels),
            "per_category": per_category,
            "per_type": per_type,
            }
        ),
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
            # #1349 vector arm: nDCG@10 (binary-gain) — emitted only when the
            # per-question outcomes carry it (vector runs).
            **({"ndcg@10": _gated(ndcg10)} if ndcg10 is not None else {}),
            **({"p@10": _gated(p10)} if p10 is not None else {}),
            **({"p@5": _gated(p5)} if p5 is not None else {}),
            "context_tokens_mean": ctx_mean,
            "context_point_count_mean": round(
                sum(o.get("context_point_count", 0) for o in outcomes) / n, 2)
                if n else 0,
            # R6 (#1545): the same aggregate block, gated on the same
            # condition — a baseline report carries zero rerank keys.
            **({"rerank": rerank_agg} if rerank_agg is not None else {}),
        },
        "latency_ms": {
            "retrieval": _lat(["retrieval_latency_ms"]),
            # R6 (#1545): the rerank latency is recorded SEPARATELY so the
            # follow-up delta can subtract it (the R6 arm's
            # retrieval_latency_ms includes rerank_ms — the plan's latency
            # comparability note).
            **({"rerank": _lat(["rerank_latency_ms"])}
               if rerank_agg is not None else {}),
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
            "retriever": retriever,
            "retrieval_arm": (
                "#1349 vector arm — run_vector_query ONLY, never "
                "tortoise_fts_query; nDCG@10 (binary gains, "
                "log2(i+2), IDCG all-evidence-first capped 10, "
                "zero-evidence -> 0.0) + P@10/P@5; elevated "
                "5000ms timeout; MODEL_ENCODE_FAILED abort on "
                "empty embedding graph; breaker-open questions "
                "dropped from means"
                if retriever == "vector" else
                "hybrid RRF (FTS+vector+structural, TF-IDF "
                "fallback) over graph turn points + raw session "
                "transcripts"
            ),
            "model": model or "default (production literal)",
            "query_prompt": query_prompt,
            "surface": surface,
            "checkpoint_key": run_key,
            "retrieval_only": retrieval_only,
            "retrieval": (
                "#1349 vector arm: run_vector_query ONLY (never "
                "tortoise_fts_query); nDCG@10 + P@10/P@5; breaker-open "
                "questions dropped from means"
                if retriever == "vector" else
                "Tortoise hybrid RRF (FTS+vector+structural, TF-IDF "
                "fallback) over graph turn points + turn-granular raw "
                "chunks (pointKind session-transcript, chunk_turns "
                "turns per non-overlapping window; candidates fetched "
                "at max(k)*3 depth, deduped per-session to "
                "max_chunks_per_session raw chunks in rank order, R1 "
                "#1540)"
                + (f" + R6 rerank stage (cross-encoder + MMR, pool "
                   f"{rerank_config['pool_size']} — post-fusion "
                   "precision+diversity, #1545)"
                   if rerank_agg is not None else "")
            ),
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
            **(r5_knobs or {}),
        },
        "failures": failures or [],
        "n_failed": len(failures or []),
        # #1349 gate contract: the per-question outcomes ride the report so
        # gate_1349.py's extract_report can recompute per-question metrics
        # (nDCG@10/P@10/P@5/ranked_ids/evidence_turn_matches + breaker_open
        # dropped markers) from the producer's own output — the gate reads
        # the report file, never a side channel. NOTE: when the caller
        # passes ``extra["outcomes"]`` (run_evaluation's outcomes_to_report
        # Layer-1 projection), the ``**(extra or {})`` spread below
        # OVERRIDES this raw list with the projected one — which carries the
        # same per-question keys plus the validity/leg-mix/evidence
        # instrumentation the gate's extract_report also reads.
        "outcomes": outcomes,
        **(rerank_report_block if rerank_report_block is not None else {}),
        **(extra or {}),
    }


def _json_safe(obj: Any) -> Any:
    """#1747: recursive JSON-normalization for save_report — dict keys are
    str()-coerced so ``json.dumps(sort_keys=True)`` never TypeErrors on a
    mixed-type key (the programmatic mixed-key census shape, security
    review), and sets become sorted lists (a malformed checkpoint value can
    otherwise crash serialization). Identity for well-formed reports (JSON
    keys are always strings; no sets)."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, tuple):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        # repr-key sort so a mixed-type set can never TypeError (security
        # review, #1747).
        return sorted((_json_safe(v) for v in obj), key=repr)
    return obj


def save_report(report: dict[str, Any], path: Path | str) -> Path:
    """Write the report JSON (pretty-printed) and return the path."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_json_safe(report), indent=2, sort_keys=True) + "\n",
                 encoding="utf-8")
    return p


def default_report_path(split: str, *, output_dir: str | None = None) -> Path:
    """Default report path: output dir (or CWD) + timestamped filename."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017
    base = Path(output_dir) if output_dir else Path.cwd()
    return base / f"longmemeval_{split}_{stamp}.report.json"


# ── M8 (#1528): paired report comparison — shared-qid deltas are PRIMARY ──

def _pick(o: dict[str, Any], key: str, default=None) -> Any:
    return o.get(key, default) if isinstance(o, dict) else default


def _flip_row(qid, category, a_o, b_o, failed_in_a, failed_in_b) -> dict[str, Any]:
    """One flip-list row (M8 D4): best-effort auxiliary columns via ``.get()``
    — honest ``None`` on stripped reports, never fabricated."""
    a_lab, b_lab = bool(a_o["label"]), bool(b_o["label"])

    def _num(o, *keys):
        v = o
        for k in keys:
            v = _pick(v, k) if isinstance(v, dict) else None
            if v is None:
                return None
        return v if isinstance(v, (int, float)) else None

    zp_a, zp_b = _num(a_o, "context_point_count"), _num(b_o, "context_point_count")
    return {
        "question_id": qid, "category": category,
        "direction": "b_win" if (not a_lab and b_lab) else "a_win",
        "a_label": a_lab, "b_label": b_lab,
        "failed_in_a": failed_in_a, "failed_in_b": failed_in_b,
        "sr20_a": _num(a_o, "session_recall@k", "20"),
        "sr20_b": _num(b_o, "session_recall@k", "20"),
        "context_tokens_a": _num(a_o, "context_tokens"),
        "context_tokens_b": _num(b_o, "context_tokens"),
        "error_count_a": _num(a_o, "n_ingest_errors"),
        "error_count_b": _num(b_o, "n_ingest_errors"),
        "zero_point_a": (zp_a == 0) if zp_a is not None else None,
        "zero_point_b": (zp_b == 0) if zp_b is not None else None,
    }


def compare_reports(report_a: dict[str, Any], report_b: dict[str, Any]) -> dict[str, Any]:
    """Paired comparison of two run reports (A = older/baseline, B = newer).

    M8 (#1528) D4 — shared-qid deltas are the PRIMARY statistic (validity
    synthesis, "Statistical discipline requirements" 1-3, 5, 9): every delta
    is joined on question_id, per category via ``category_of()`` (the same
    function the single report uses, so ``_abs`` handling cannot drift), with
    exact McNemar on discordant pairs, Wilson 95% CIs for both runs,
    per-category flip lists, a stated ``_abs`` inclusion rule, a
    comparability block (v2 lesson F1: reader/judge drift), and fixed caveats
    (judge-stability x answer-shape — owner-accepted —, content-identity, and
    reliability-restored-not-a-sample).
    """
    ABS_RULE = ("Categories follow report.category_of: question ids containing "
                "'_abs' are categorized as 'Abstention' in per_category (counted "
                "once there) AND under their raw question_type in per_type. All "
                "completed questions are included — no silent subsetting (the v2 "
                "MSR report silently excluded its 12 _abs qids from its flip "
                "table; validity B5). Flip lists and McNemar are per_category.")
    CAVEATS = [
        "judge-stability x answer-shape (owner-accepted methodology note): the "
        "judge is temperature-0 with a fixed model (see each report's "
        "methodology), but clean-abstention vs partial-answer-with-decoy scoring "
        "is judge-model-dependent and unmeasured — label flips near the "
        "abstention boundary may reflect answer cleanliness, not memory content.",
        "identical-context claims carry the content-identity caveat: token-count "
        "identity is not content identity (validity requirement 7).",
        "reliability-restored qids (failed in one run, completed in the other) "
        "failed by a run event (network/billing), not by content — they are not "
        "a difficulty sample (validity F5).",
    ]
    oa = {str(o["question_id"]): o for o in report_a.get("outcomes", [])
          if o.get("question_id") is not None}
    ob = {str(o["question_id"]): o for o in report_b.get("outcomes", [])
          if o.get("question_id") is not None}
    # #1747 (round-8 review): reports may carry non-str question_ids (graded
    # under collision-proof sentinel keys) and failure entries WITHOUT
    # question_id (per-object unknown questions) — the join keys must be
    # hashable + sortable, so str()-coerce outcome qids and skip qid-less
    # failures (they can never join an outcome qid).
    fa = {str(f["question_id"]) for f in report_a.get("failures", [])
          if f.get("question_id") is not None}
    fb = {str(f["question_id"]) for f in report_b.get("failures", [])
          if f.get("question_id") is not None}
    shared = sorted(oa.keys() & ob.keys())
    only_a, only_b = sorted(set(oa) - set(ob)), sorted(set(ob) - set(oa))
    n_a, n_b = len(oa), len(ob)
    acc_a, acc_b = (sum(1 for o in oa.values() if o["label"]) / n_a if n_a else 0.0,
                    sum(1 for o in ob.values() if o["label"]) / n_b if n_b else 0.0)

    # categories
    def _cat(o):
        return category_of({"question_id": o["question_id"],
                            "question_type": o.get("question_type", "")})

    cats = sorted({_cat(o) for o in [*oa.values(), *ob.values()]})
    by_cat: dict[str, dict] = {c: {"a": [], "b": [], "shared": [],
                                   "only_a": [], "only_b": []} for c in cats}
    for qid, o in oa.items():
        by_cat[_cat(o)]["a"].append(o)
        by_cat[_cat(o)]["only_a"].append(qid)
    for qid, o in ob.items():
        by_cat[_cat(o)]["b"].append(o)
        by_cat[_cat(o)]["only_b"].append(qid)
    for qid in shared:
        by_cat[_cat(oa[qid])]["shared"].append(qid)

    per_category: dict[str, Any] = {}
    flip_lists: dict[str, list] = {}
    for c in cats:
        blk = by_cat[c]
        n_shared = len(blk["shared"])
        b_wins = sum(1 for q in blk["shared"] if not oa[q]["label"] and ob[q]["label"])
        a_wins = sum(1 for q in blk["shared"] if oa[q]["label"] and not ob[q]["label"])
        b_correct_s = sum(1 for q in blk["shared"] if ob[q]["label"])
        a_correct_s = sum(1 for q in blk["shared"] if oa[q]["label"])
        per_category[c] = {
            "a": {"accuracy": _mean([1.0 if o["label"] else 0.0 for o in blk["a"]]),
                  "n": len(blk["a"]),
                  "ci95": list(wilson_ci(sum(1 for o in blk["a"] if o["label"]),
                                         len(blk["a"])))},
            "b": {"accuracy": _mean([1.0 if o["label"] else 0.0 for o in blk["b"]]),
                  "n": len(blk["b"]),
                  "ci95": list(wilson_ci(sum(1 for o in blk["b"] if o["label"]),
                                         len(blk["b"])))},
            "shared": {"n": n_shared, "a_correct": a_correct_s,
                       "b_correct": b_correct_s,
                       "b_wins": b_wins, "a_wins": a_wins,
                       "concordant": n_shared - b_wins - a_wins,
                       "delta_pp": round(
                           (b_correct_s - a_correct_s) / n_shared * 100, 2)
                           if n_shared else 0.0},
            "mcnemar": {"discordant_n": b_wins + a_wins, "b_wins": b_wins,
                        "a_wins": a_wins,
                        "p_value": mcnemar_exact(a_wins, b_wins),
                        "significant_at_0_05": mcnemar_exact(a_wins, b_wins) < 0.05},
        }
        flip_lists[c] = sorted(
            (_flip_row(q, c, oa[q], ob[q], q in fa, q in fb)
             for q in blk["shared"] if oa[q]["label"] != ob[q]["label"]),
            key=lambda r: r["question_id"])

    # overall decomposition (MSR-pinned: headline = shared + reliability + residual)
    n_shared = len(shared)
    restored = [q for q in only_b if q in fa]
    lost = [q for q in only_a if q in fb]
    k_restored = sum(1 for q in restored if ob[q]["label"])
    k_lost = sum(1 for q in lost if oa[q]["label"])
    acc_b_shared = (sum(1 for q in shared if ob[q]["label"]) / n_shared
                    if n_shared else 0.0)
    shared_delta_pp = round(
        (sum(1 for q in shared if ob[q]["label"])
         - sum(1 for q in shared if oa[q]["label"])) / n_shared * 100, 2) \
        if n_shared else 0.0
    reliability_pp = round(
        (k_restored - len(restored) * acc_b_shared) / n_b * 100, 2) if n_b else 0.0
    headline_pp = round((acc_b - acc_a) * 100, 2)

    def _meta(r): return r.get("methodology", {})

    def _val(r, k):
        m = _meta(r)
        if k == "dataset":
            return m.get("dataset_source") or r.get("dataset")
        return m.get(k)

    def _match(k):
        va, vb = _val(report_a, k), _val(report_b, k)
        return {"a": va, "b": vb,
                "match": (va == vb) if (va is not None and vb is not None) else None}

    warnings: list[str] = []
    for k, human in (("reader_model", "reader_model"), ("judge_model", "judge_model"),
                     ("ingest_mode", "ingest_mode"), ("split", "split"),
                     ("dataset", "dataset")):
        m = _match(k)
        if m["match"] is False:
            warnings.append(
                f"{human} differs: {m['a']} vs {m['b']} — deltas may reflect "
                f"{human.replace('_', ' ')} drift, not memory content (v2 "
                f"lesson F1)")
    ph = _match("reader_prompt_hash")
    if ph["match"] is None:
        warnings.append(
            "reader_prompt_hash absent on one side — prompt identity "
            "unverifiable across the comparison (pre-hash report)")

    b_wins_all = sum(1 for q in shared if not oa[q]["label"] and ob[q]["label"])
    a_wins_all = sum(1 for q in shared if oa[q]["label"] and not ob[q]["label"])
    restored_rate = round(k_restored / len(restored), 4) if restored else None
    lost_rate = round(k_lost / len(lost), 4) if lost else None
    residual_pp = round(headline_pp - shared_delta_pp - reliability_pp, 2)

    def _identity(r):
        m = _meta(r)
        return {
            "dataset": m.get("dataset_source") or r.get("dataset"),
            "split": m.get("split") or r.get("split"),
            "n_completed": len(r.get("outcomes", [])),
            "n_failed": len(r.get("failures", [])),
            "reader_model": m.get("reader_model"),
            "judge_model": m.get("judge_model"),
            "git_sha": m.get("git_sha"),
            "run_at_utc": m.get("run_at_utc"),
        }

    return {
        "comparison_rule": "shared-qid deltas are primary; every delta is "
                           "joined on question_id — no cross-question-set "
                           "comparison (validity requirement 9)",
        "a": _identity(report_a),
        "b": _identity(report_b),
        "header": {
            "abs_inclusion_rule": ABS_RULE,
            "per_category_n": {c: {"a": len(by_cat[c]["a"]),
                                   "b": len(by_cat[c]["b"]),
                                   "shared": len(by_cat[c]["shared"])}
                               for c in cats},
            "per_type_n": {t: {"a": sum(1 for o in oa.values()
                                        if o.get("question_type") == t),
                               "b": sum(1 for o in ob.values()
                                        if o.get("question_type") == t),
                               "shared": sum(1 for q in shared
                                             if oa[q].get("question_type") == t)}
                           for t in sorted({o.get("question_type", "")
                                            for o in [*oa.values(), *ob.values()]})},
        },
        "overall": {
            "headline_delta_pp": headline_pp,
            "a_accuracy": round(acc_a, 4), "b_accuracy": round(acc_b, 4),
            "shared_n": n_shared,
            "shared_delta_pp": shared_delta_pp,          # PRIMARY
            "decomposition": {
                "shared_net_flips": b_wins_all - a_wins_all,
                "b_wins": b_wins_all, "a_wins": a_wins_all,
                "concordant": n_shared - b_wins_all - a_wins_all,
                "reliability_restored": {"count": len(restored),
                                         "correct": k_restored,
                                         "rate": restored_rate},
                "reliability_lost": {"count": len(lost),
                                     "correct_in_a": k_lost,
                                     "rate": lost_rate},
                "composition_only_in_b": len(only_b) - len(restored),
                "composition_only_in_a": len(only_a) - len(lost),
                "reliability_pp": reliability_pp,
                # denominator drift + A-side-only qids; reported so nothing hides
                "residual_pp": residual_pp,
            },
            "decomposition_note": "MSR verified: headline +2.07pp (52.07% "
                                  "n=121 -> 54.14% n=133) = +0.83pp shared "
                                  "(1 net flip / 121) + ~1.24pp reliability "
                                  "(8/12 on baseline-failed) + ~0 residual. "
                                  "Every component is reported as a count or "
                                  "an explicit pp so the headline cannot hide "
                                  "its parts.",
        },
        "per_category": per_category,
        "flip_lists": flip_lists,
        "comparability": {
            "dataset": _match("dataset"),
            "split": _match("split"),
            "reader_model": _match("reader_model"),
            "judge_model": _match("judge_model"),
            "ingest_mode": _match("ingest_mode"),
            "reader_prompt_hash": ph,
            "warnings": warnings,
        },
        "caveats": CAVEATS,
    }


def print_comparison(cmp: dict[str, Any], file=None) -> None:
    """Console render of a ``compare_reports`` artifact (mirrors
    ``_print_summary`` style). Header + per-category rows (both runs'
    accuracies with n and ci95, shared delta, McNemar p), flip-list counts,
    comparability warnings, then the fixed caveats."""
    print("\n" + "=" * 64, file=file)
    a_id, b_id = cmp["a"], cmp["b"]
    print("LongMemEval report comparison — A (baseline) vs B (newer)", file=file)
    print(f"  A: dataset={a_id['dataset']} split={a_id['split']} "
          f"n_completed={a_id['n_completed']} n_failed={a_id['n_failed']} "
          f"reader={a_id['reader_model']} judge={a_id['judge_model']}",
          file=file)
    print(f"  B: dataset={b_id['dataset']} split={b_id['split']} "
          f"n_completed={b_id['n_completed']} n_failed={b_id['n_failed']} "
          f"reader={b_id['reader_model']} judge={b_id['judge_model']}",
          file=file)
    ov = cmp["overall"]
    print("── score ──", file=file)
    print(f"headline delta:   {ov['headline_delta_pp']:+}pp "
          f"(A {ov['a_accuracy']} -> B {ov['b_accuracy']})", file=file)
    print(f"SHARED-qid delta: {ov['shared_delta_pp']:+}pp (shared n="
          f"{ov['shared_n']}) — PRIMARY", file=file)
    dec = ov["decomposition"]
    rst, lost = dec["reliability_restored"], dec["reliability_lost"]
    print(f"decomposition: net flips {dec['shared_net_flips']:+} "
          f"(W/L {dec['b_wins']}/{dec['a_wins']}, "
          f"concordant {dec['concordant']}); restored {rst['count']} "
          f"({rst['correct']} correct, rate {rst['rate']}); lost "
          f"{lost['count']} ({lost['correct_in_a']} correct-in-A); "
          f"reliability {dec['reliability_pp']:+}pp; residual "
          f"{dec['residual_pp']:+}pp", file=file)
    print("── per-category ──", file=file)
    for cat, v in sorted(cmp["per_category"].items()):
        a, b = v["a"], v["b"]
        s = v["shared"]
        m = v["mcnemar"]
        print(f"  {cat:<28} A {a['accuracy']} (n={a['n']}, ci95 {a['ci95']}) vs "
              f"B {b['accuracy']} (n={b['n']}, ci95 {b['ci95']})  "
              f"Δshared {s['delta_pp']:+}pp  McNemar p={m['p_value']}"
              f"{' *' if m['significant_at_0_05'] else ''}", file=file)
        flips = cmp["flip_lists"].get(cat, [])
        if flips:
            print(f"    flips: {len(flips)} ({sum(1 for f in flips if f['direction'] == 'b_win')} b-win / "
                  f"{sum(1 for f in flips if f['direction'] == 'a_win')} a-win)", file=file)
    print("── comparability ──", file=file)
    warnings = cmp["comparability"]["warnings"]
    if warnings:
        for w in warnings:
            print(f"⚠ {w}", file=file)
    else:
        print("no methodology mismatches detected", file=file)
    print("── caveats ──", file=file)
    for c in cmp["caveats"]:
        print(f"  - {c}", file=file)
    print("=" * 64, file=file)
