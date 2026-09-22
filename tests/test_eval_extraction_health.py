"""Extraction-health gate tests (#1946): degraded ingest is flagged, never
silently blended.

The reval3 incident: 33/50 questions ingested ZERO semantic points
(``fatal_402_billing`` 1591 / ``s1_chunk_summary`` 1596 /
``empty_embed_list`` 1600 in the error census — DeepSeek 402'd on every
session) yet the run 'completed' and the report blended a 0.880 accuracy
that was 66% raw-fallback. The integrity gate's census COUNTED the errors
but nothing acted on them — the run continued in degraded mode and the
report blended the two populations (healthy 17 @ 0.824 + degraded 33 @
0.909) into one headline number.

#1946 adds an extraction-health gate that ACTS on the census:

* Per-question classification (``_outcome_extraction_health``): a question
  is DEGRADED when its semantic extraction produced < ``EXTRACTION_MIN_POINTS``
  points (the issue's "points_total < 100" — the real semantic signal is
  ``ingest.points``; ``points_total`` counts raw chunks+turns too and stays
  ~900-1100 on a 402-degraded run), OR the graph pool is tiny
  (``points_total < min_points`` — nothing to retrieve), OR the error census
  carries an extraction-killing class (``fatal_402_billing`` /
  ``empty_embed_list`` — presence at any count: billing intervened), OR
  ``s1_chunk_summary`` at scale (the S1→S2 cascade class; a handful of
  per-chunk digest failures on a productive extraction is benign, a count
  near the session scale means the semantic layer collapsed).
* Run-level flag (``report["extraction_health"]``): status DEGRADED when the
  degraded fraction is MATERIAL (>= ``EXTRACTION_HEALTH_DEGRADED_FRACTION``)
  OR the run census carries any ``fatal_402_billing`` / ``empty_embed_list``
  (a single billing event is a run-level integrity event — the 500-Q must
  never certify a billing-limited run). The report ALWAYS emits
  healthy_n / degraded_n / per-population accuracy so the blend is visible.
* The design is FLAG + split (not abort): the degraded population's
  raw-fallback numbers are a useful baseline and the healthy population
  carries the real full-stack signal (reval3: 14/17 = 0.824, n=17) — abort
  pre-finalize would destroy salvageable data. The integrity gate already
  vetoes such runs (reval3: valid=false, 35 hard-invalid); the missing piece
  was the REPORT-level readout.

Deterministic mode (no ``ingest.points`` — the deterministic leg writes
turns/chunks, no semantic extraction): classified by census ONLY — the
points rule can never falsely degrade a deterministic run.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval.dataset_audit import audit_dataset  # noqa: E402, I001, RUF100
from tools.longmem_eval.report import (  # noqa: E402, I001, RUF100
    EXTRACTION_DEGRADE_MIN_COUNT,
    EXTRACTION_MIN_POINTS,
    _outcome_extraction_health,
    build_report,
)
from tools.longmem_eval.run import _print_summary  # noqa: E402, I001, RUF100


def _audit() -> dict:
    return audit_dataset([{
        "question_id": "q-audit",
        "haystack_session_ids": ["s0"],
        "answer_session_ids": ["s0"],
        "haystack_sessions": [[
            {"role": "user", "content": "x", "has_answer": True}]],
    }])


def _outcome(qid: str, *, valid: bool = True, label: bool = True,
             error_classes: dict | None = None,
             points: int | None = None,
             points_total: int | None = None) -> dict:
    """Shape-filter-compliant outcome (mirrors test_eval_report_integrity's
    helper + the #1946 health fields). ``points`` = the semantic extraction
    count (``ingest.points``); ``points_total`` = the live pool size."""
    ingest = {"evidence_turns": 1, "evidence_points": 0}
    if points is not None:
        ingest["points"] = points
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
        "pool_size": points_total or 100, "evidence_written": 1,
        "evidence_retrieved@k": {"5": 1}, "ingest_latency_ms": 1.0,
        "ingest": ingest,
        "points_total": points_total,
    }


def _report(outcomes: list[dict], *, threshold: float = 0.0,
            failures: list[dict] | None = None,
            retrieval_only: bool = False) -> dict:
    return build_report(
        outcomes,
        dataset_id="xiaowu0162/longmemeval-cleaned", split="s",
        reader_model="mock-reader", judge_model="mock-judge",
        extraction_approach="v2 llm extraction",
        ingest_mode="v2", ks=(5,), top_k=5,
        dataset_semantics_audit=_audit(),
        integrity_threshold=threshold, failures=failures,
        retrieval_only=retrieval_only,
    )


# ── Per-question classification unit tests ─────────────────────────────────

def test_health_semantic_points_below_min_is_degraded():
    """A question with ingest.points < EXTRACTION_MIN_POINTS (semantic
    extraction failed — the reval3 402 shape produced 0 points) classifies
    DEGRADED even with a clean census (the extractor can silently produce
    nothing; the census may be empty)."""
    assert _outcome_extraction_health(
        _outcome("q", points=0)) == "degraded"
    assert _outcome_extraction_health(
        _outcome("q", points=EXTRACTION_MIN_POINTS - 1)) == "degraded"


def test_health_semantic_points_at_or_above_min_is_healthy():
    """ingest.points >= EXTRACTION_MIN_POINTS with a clean census →
    healthy (the reval3 healthy population extracted 326-457 points)."""
    assert _outcome_extraction_health(
        _outcome("q", points=EXTRACTION_MIN_POINTS)) == "healthy"
    assert _outcome_extraction_health(
        _outcome("q", points=400)) == "healthy"


def test_health_tiny_pool_is_degraded():
    """points_total < EXTRACTION_MIN_POINTS (the graph pool itself is tiny
    — fewer raw items than the floor) classifies degraded: nothing to
    retrieve is a structural failure, not a measurement."""
    assert _outcome_extraction_health(
        _outcome("q", points=500, points_total=10)) == "degraded"


def test_health_fatal_402_billing_presence_degrades_regardless_of_points():
    """fatal_402_billing in the census degrades even when the question
    partially extracted (reval3's 66f24dbb shape: points 202 but 20
    billing errors — the run was billing-limited)."""
    assert _outcome_extraction_health(
        _outcome("q", points=300,
                 error_classes={"fatal_402_billing": 20})) == "degraded"


def test_health_empty_embed_list_presence_degrades():
    """empty_embed_list (the S1→S2 cascade's structural marker) degrades at
    any count — an embed list that never materialized means no dense leg."""
    assert _outcome_extraction_health(
        _outcome("q", points=300,
                 error_classes={"empty_embed_list": 1})) == "degraded"


def test_health_s1_chunk_summary_only_at_scale_degrades():
    """s1_chunk_summary is the cascade class: DEGRADED only at scale
    (count >= EXTRACTION_DEGRADE_MIN_COUNT) — a couple of per-chunk digest
    failures on a productive extraction (points >= min) is benign."""
    assert _outcome_extraction_health(
        _outcome("q", points=300,
                 error_classes={"s1_chunk_summary": 2})) == "healthy"
    assert _outcome_extraction_health(
        _outcome("q", points=300,
                 error_classes={"s1_chunk_summary":
                                EXTRACTION_DEGRADE_MIN_COUNT})) == "degraded"


def test_health_recoverable_classes_do_not_degrades():
    """Recoverable-only census classes (parse_error / partial_parse /
    transient_*) with points >= min → healthy — the integrity gate's
    rate-limited classes are not extraction degradation."""
    for cls in ("partial_parse", "parse_error", "transient_429_rate_limit",
                "transient_5xx"):
        assert _outcome_extraction_health(
            _outcome("q", points=300, error_classes={cls: 3})) == "healthy"


def test_health_deterministic_no_points_never_falsely_degraded():
    """Deterministic-mode outcomes carry NO ``ingest.points`` (the
    deterministic leg writes turns/chunks, no semantic extraction) — the
    points rule must NOT fire; a clean census classifies healthy (a
    deterministic run is never flagged by the semantic-points rule)."""
    o = _outcome("q")
    assert "points" not in o["ingest"]
    assert _outcome_extraction_health(o) == "healthy"


def test_health_deterministic_tiny_pool_not_degraded():
    """The points_total rule is semantic-mode ONLY: a deterministic
    outcome with a small raw chunk/turn pool (a dev slice) must NOT be
    flagged degraded — the "census-only" promise covers points_total too
    (points_total is present in both ingest modes; only the v2 leg's
    semantic extraction distinguishes real degradation from a small
    deterministic pool)."""
    o = _outcome("q", points_total=50)
    assert "points" not in o["ingest"]
    assert _outcome_extraction_health(o) == "healthy"


def test_health_v2_tiny_pool_degraded():
    """In semantic mode (ingest.points present) a tiny live pool is
    degraded — a graph with fewer items than the floor has nothing to
    retrieve (the rule is largely subsumed by the points rule since
    pool ⊇ points, but catches the contradictory shape)."""
    assert _outcome_extraction_health(
        _outcome("q", points=150, points_total=50)) == "degraded"


def test_health_full_context_sentinel_healthy():
    """The full_context.py cell baseline emits the no-extraction sentinel
    {"sessions", "points": 0, "errors": []} — raw context only, extraction
    BY DESIGN (option-5 baseline). The sentinel must not be flagged
    degraded (it is distinguishable from a real zero-extract by its
    minimal key set; the v2/deterministic legs always record more)."""
    o = _outcome("q")
    o["ingest"] = {"sessions": 3, "points": 0, "errors": []}
    assert _outcome_extraction_health(o) == "healthy"


def test_health_full_context_sentinel_with_killer_census_still_degrades():
    """The full-context sentinel is exempt from the POINTS rules only — a
    killer census class on that shape still degrades (a tampered or
    billing-limited cell run must not escape the flag)."""
    o = _outcome("q", error_classes={"fatal_402_billing": 1})
    o["ingest"] = {"sessions": 3, "points": 0, "errors": []}
    assert _outcome_extraction_health(o) == "degraded"


def test_health_nonfinite_points_never_fabricate_verdict():
    """Non-finite points (NaN / ±inf — malformed checkpoint data) never
    fire the points rules: the outcome falls to census-only classification
    (a clean census → healthy; the verdict is never fabricated by a
    poisoned value, mirroring _numeric's fail-closed discipline)."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        o = _outcome("q")
        o["ingest"]["points"] = bad
        assert _outcome_extraction_health(o) == "healthy"


def test_health_legacy_flat_list_s1_fail_closed():
    """Legacy flat-list error_classes carry no counts — presence of the
    cascade class IS scale (fail-closed toward degraded, per the inline
    promise)."""
    o = _outcome("q", points=300)
    o["error_classes"] = ["s1_chunk_summary"]
    assert _outcome_extraction_health(o) == "degraded"


def test_health_retrieval_only_no_population_accuracy():
    """Retrieval-only runs carry label:None by design — the population
    split is still emitted (counts), but per-population accuracy is None
    (never fabricated from None labels)."""
    o = _outcome("q", points=400)
    o["label"] = None
    report = _report([o], retrieval_only=True)
    eh = report["extraction_health"]
    assert eh["healthy_n"] == 1
    assert eh["per_population_accuracy"] is None


def test_health_empty_run_vacuous_healthy():
    """An empty report (no outcomes) is vacuously healthy — mirroring the
    integrity block's vacuous-valid posture; healthy_n/degraded_n are 0 and
    per-population accuracies are None."""
    eh = _report([])["extraction_health"]
    assert eh["status"] == "healthy"
    assert eh["healthy_n"] == 0
    assert eh["degraded_n"] == 0
    assert eh["degraded_fraction"] == 0.0
    assert eh["per_population_accuracy"]["healthy"]["accuracy"] is None
    assert eh["per_population_accuracy"]["degraded"]["accuracy"] is None


def test_health_killer_class_in_failures_flags_run():
    """A killer census class arriving via a FAILURES entry (a malformed /
    tampered checkpoint carrying fatal_402_billing as a failure error_class
    — the production runner never emits census classes on failure entries,
    but the census roll-up includes failure classes) still flags the run:
    degraded_n stays 0 and the summary surfaces the census as the deciding
    term."""
    outcomes = [_outcome(f"q{i}", points=400) for i in range(50)]
    eh = _report(outcomes, failures=[{
        "question_id": "f0", "error_class": "fatal_402_billing",
        "error": "billing"}])["extraction_health"]
    assert eh["status"] == "degraded"
    assert eh["degraded_n"] == 0
    assert eh["healthy_n"] == 50
    assert eh["degraded_fraction"] == 0.0


def test_health_deterministic_with_killer_census_still_degrades():
    """Deterministic mode is census-only — a killer class still degrades
    (a billing-limited deterministic run must not escape the flag)."""
    assert _outcome_extraction_health(
        _outcome("q", error_classes={"fatal_402_billing": 1})) == "degraded"


# ── Run-level gate + report block tests ────────────────────────────────────

def test_health_gate_fatal_402_billing_at_scale_fires():
    """A run where a material fraction of questions carry fatal_402_billing
    → extraction_health.status == "degraded" (Indicator 1: flagged at the
    gate)."""
    outcomes = [_outcome(f"q{i}", points=400) for i in range(10)]
    outcomes += [_outcome(f"d{i}", points=0,
                          error_classes={"fatal_402_billing": 52,
                                         "s1_chunk_summary": 52,
                                         "empty_embed_list": 52})
                 for i in range(40)]
    eh = _report(outcomes)["extraction_health"]
    assert eh["status"] == "degraded"
    assert eh["degraded_n"] == 40
    assert eh["healthy_n"] == 10
    assert eh["degraded_fraction"] == 0.8
    assert eh["degraded_fraction"] >= eh["threshold"]


def test_health_gate_points_below_min_fires():
    """A run where a material fraction have semantic points < min → flagged
    degraded (points_total < 100 in the issue's shorthand; the real signal
    is ingest.points — this is the silent-zero-extraction shape)."""
    outcomes = [_outcome(f"q{i}", points=400) for i in range(30)]
    outcomes += [_outcome(f"d{i}", points=0) for i in range(20)]
    eh = _report(outcomes)["extraction_health"]
    assert eh["status"] == "degraded"
    assert eh["degraded_n"] == 20
    assert eh["degraded_fraction"] == 0.4


def test_health_gate_single_billing_event_flags_run():
    """ANY fatal_402_billing / empty_embed_list at run level flags the run
    even below the fraction threshold — a single billing event is a
    run-level integrity event (the 500-Q must never certify a billing-
    limited run); the split still shows the true scale."""
    outcomes = [_outcome(f"q{i}", points=400) for i in range(99)]
    outcomes.append(_outcome("d0", points=400,
                             error_classes={"fatal_402_billing": 1}))
    eh = _report(outcomes)["extraction_health"]
    assert eh["status"] == "degraded"
    assert eh["degraded_n"] == 1
    assert eh["degraded_fraction"] < eh["threshold"]


def test_health_gate_healthy_run_passes():
    """A fully healthy run (points >= min, clean census) → status healthy,
    degraded_n 0, healthy_n == n (Indicator 1: passes)."""
    outcomes = [_outcome(f"q{i}", points=350) for i in range(50)]
    eh = _report(outcomes)["extraction_health"]
    assert eh["status"] == "healthy"
    assert eh["healthy_n"] == 50
    assert eh["degraded_n"] == 0
    assert eh["degraded_fraction"] == 0.0


def test_health_gate_recoverable_blips_do_not_fire():
    """A handful of recoverable-class blips (partial_parse / transient) on
    an otherwise healthy run → status healthy (the integrity gate's
    rate-limited classes are not extraction degradation)."""
    outcomes = [_outcome(f"q{i}", points=350) for i in range(48)]
    outcomes += [_outcome("r1", points=350, error_classes={"partial_parse": 2}),
                 _outcome("r2", points=350,
                          error_classes={"transient_429_rate_limit": 3})]
    eh = _report(outcomes)["extraction_health"]
    assert eh["status"] == "healthy"
    assert eh["degraded_n"] == 0
    assert eh["healthy_n"] == 50


def test_report_emits_population_split():
    """The report emits healthy_n / degraded_n / per-population accuracy
    (Indicator 2) — the blend is visible, never silent (Indicator 3)."""
    outcomes = [_outcome("h0", points=400, label=True),
                _outcome("h1", points=400, label=False),
                _outcome("h2", points=400, label=True)]
    outcomes += [_outcome("d0", points=0, label=True),
                 _outcome("d1", points=0,
                          error_classes={"fatal_402_billing": 1},
                          label=False)]
    eh = _report(outcomes)["extraction_health"]
    assert eh["healthy_n"] == 3
    assert eh["degraded_n"] == 2
    assert eh["per_population_accuracy"]["healthy"] == {
        "n": 3, "accuracy": round(2 / 3, 4)}
    assert eh["per_population_accuracy"]["degraded"] == {
        "n": 2, "accuracy": 0.5}
    # overall accuracy is the BLEND — the split must be readable beside it.
    assert _report(outcomes)["accuracy"]["overall"] == 0.6
    # degraded qids are listed so the operator can act on the population.
    assert sorted(eh["degraded_qids"]) == ["d0", "d1"]
    assert "criterion" in eh and "#1946" in eh["criterion"]


def test_health_block_zero_degraded_accuracy_is_none():
    """An empty population's accuracy is None, never a fabricated 0.0 (a
    0.0 would read as a score)."""
    outcomes = [_outcome("h0", points=400, label=True)]
    eh = _report(outcomes)["extraction_health"]
    assert eh["per_population_accuracy"]["healthy"]["accuracy"] == 1.0
    assert eh["per_population_accuracy"]["degraded"]["accuracy"] is None
    assert eh["per_population_accuracy"]["degraded"]["n"] == 0


def test_print_summary_extraction_health_readout(capsys):
    """_print_summary prints the extraction-health section BEFORE the score
    with the per-population accuracy — an operator reading stdout sees the
    blend, never a bare headline."""
    outcomes = [_outcome("h0", points=400, label=True),
                _outcome("h1", points=400, label=True)]
    outcomes += [_outcome("d0", points=0, label=True),
                 _outcome("d1", points=0,
                          error_classes={"fatal_402_billing": 1},
                          label=False)]
    report = _report(outcomes)
    _print_summary(report)
    out = capsys.readouterr().out
    assert "extraction health" in out
    assert "healthy 2 / degraded 2" in out
    assert "degraded" in out
    # per-population accuracies printed (healthy 1.0, degraded 0.5)
    assert "accuracy 1.0 (n=2)" in out
    assert "accuracy 0.5 (n=2)" in out
    # printed BEFORE the score line
    assert out.index("extraction health") < out.index("overall accuracy")


# ── Reval3 replay ──────────────────────────────────────────────────────────

def test_reval3_replay_flagged_degraded():
    """Replay of the reval3 checkpoint shape (17 healthy + 33 degraded):
    the run would be FLAGGED degraded with the healthy/degraded split and
    per-population accuracy (Indicator 3 / the issue's target: reval3 would
    have been flagged — 33/50). Healthy carries the real full-stack signal
    (14/17 = 0.824); degraded is raw-fallback (30/33 = 0.909); the blended
    0.880 is never presented as a single-population measurement.

    The fixture reproduces the real checkpoint's census pattern verbatim:
    degraded questions carry fatal_402_billing / s1_chunk_summary /
    empty_embed_list at session scale (~50 each) with ingest.points == 0,
    while healthy questions extracted 300+ points with at most
    partial_parse recoverable blips."""
    healthy, degraded = [], []
    for i in range(17):
        healthy.append(_outcome(
            f"h{i:08x}", points=350 + i * 7,
            label=(i < 14),
            error_classes=({"partial_parse": 1} if i % 5 == 0 else {})))
    for i in range(33):
        degraded.append(_outcome(
            f"d{i:08x}", points=0, label=(i < 30),
            error_classes={"fatal_402_billing": 50 + i,
                           "s1_chunk_summary": 50 + i,
                           "empty_embed_list": 50 + i}))
    outcomes = healthy + degraded
    report = _report(outcomes)
    eh = report["extraction_health"]
    assert eh["status"] == "degraded"
    assert eh["healthy_n"] == 17
    assert eh["degraded_n"] == 33
    assert eh["degraded_fraction"] == round(33 / 50, 4)
    assert eh["degraded_fraction"] >= eh["threshold"]
    # the real reval3 per-population numbers, reproduced exactly.
    assert eh["per_population_accuracy"]["healthy"] == {
        "n": 17, "accuracy": round(14 / 17, 4)}       # 0.8235 — real stack
    assert eh["per_population_accuracy"]["degraded"] == {
        "n": 33, "accuracy": round(30 / 33, 4)}       # 0.9091 — raw fallback
    assert len(eh["degraded_qids"]) == 33
    # the integrity gate independently vetoes (valid=false, 35 hard-invalid
    # on the real checkpoint — the census-class-aware criterion).
    assert report["integrity"]["valid"] is False
    assert report["integrity"]["error_census"]["fatal_402_billing"] > 0
    # the BLEND is still emitted — but never without the split beside it.
    assert report["accuracy"]["overall"] == round(44 / 50, 4)  # 0.88 blend
