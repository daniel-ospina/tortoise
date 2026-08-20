"""Judge harness + adjudication tests (#1144) — mock models, no LLM calls."""
from __future__ import annotations  # noqa: I001

import json

import pytest

from tests.eval.retrieval.judge import (
    GRADE_VOCAB,
    RELEVANCE_RUBRIC_SYSTEM,
    Pool,
    PoolPoint,
    PoolQuery,
    JudgeError,
    adjudication_stats,
    agreement_report,
    build_pool,
    emit_rulings_template,
    merge_labels,
    parse_judge_response,
)


class _MockJudge:
    """Model-adapter-shaped mock: returns a canned JSON per query."""

    def __init__(self, responses: dict[str, str]):
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        for qid in self.responses:
            if qid in user:
                return self.responses[qid]
        raise AssertionError(f"no canned response for {user[:60]!r}")


def _pool() -> Pool:
    return Pool(
        queries=[
            PoolQuery(
                id="aq001", query="pricing tiers",
                points=[PoolPoint(id="p0", content="pricing plans"),
                        PoolPoint(id="p1", content="circuit breakers"),
                        PoolPoint(id="p2", content="enterprise tiers")],
            ),
        ],
        strategies=["fts", "vector", "tfidf"],
        corpus_fingerprint={"n_points": 3},
    )


def _valid_response() -> str:
    return json.dumps({
        "query_id": "aq001",
        "verdicts": [
            {"id": "p0", "grade": 2},
            {"id": "p1", "grade": 0},
            {"id": "p2", "grade": 2},
        ],
    })


# ── Rubric ──────────────────────────────────────────────────────────────────

def test_rubric_is_retrieval_relevance_not_extraction():
    """The retrieval rubric is NEW and distinct from the #945 extraction
    rubric (probe_extractor): it grades retrieval RELEVANCE over a pool,
    with the graded 0|1|2 vocab — it must not carry over the extraction
    rubric's closed entity-kind vocabulary (decision/event/claim/process /
    entity / relation / IMPL / NAND / extract), or judges would grade the
    wrong axis."""
    assert "relevance" in RELEVANCE_RUBRIC_SYSTEM.lower()
    assert "grade" in RELEVANCE_RUBRIC_SYSTEM.lower()
    assert GRADE_VOCAB == ("non_relevant", "partially", "relevant")
    assert "non-relevant" in RELEVANCE_RUBRIC_SYSTEM.lower()
    assert "pool" in RELEVANCE_RUBRIC_SYSTEM.lower()
    assert "query" in RELEVANCE_RUBRIC_SYSTEM.lower()
    # Extraction-axis vocabulary (probe_extractor closed vocab + the
    # extraction rubric's relations/entities) must be entirely absent.
    import re as _re
    extraction_vocab = (
        "decision", "event", "claim", "process", "entity", "relation",
        "extract", "impl", "nand", "transcript", "utterance", "ontology",
    )
    body = RELEVANCE_RUBRIC_SYSTEM.lower()
    leaked = [w for w in extraction_vocab if _re.search(rf"\b{w}\b", body)]
    assert not leaked, (
        f"retrieval rubric leaks extraction vocabulary: {leaked} — it must "
        "grade relevance, not extract decision/event/claim/..."
    )


# ── Parsing ─────────────────────────────────────────────────────────────────

def test_parse_judge_response_valid():
    labels = parse_judge_response(_valid_response(), "aq001", ["p0", "p1", "p2"])
    assert labels == {"p0": 2, "p1": 0, "p2": 2}


def test_parse_judge_response_strips_fences():
    raw = "```json\n" + _valid_response() + "\n```"
    labels = parse_judge_response(raw, "aq001", ["p0", "p1", "p2"])
    assert labels == {"p0": 2, "p1": 0, "p2": 2}


def test_parse_judge_response_missing_point_is_error():
    raw = json.dumps({"query_id": "aq001", "verdicts": [
        {"id": "p0", "grade": 2}, {"id": "p1", "grade": 0},
    ]})  # p2 omitted
    with pytest.raises(JudgeError, match="never omit"):
        parse_judge_response(raw, "aq001", ["p0", "p1", "p2"])


def test_parse_judge_response_bad_grade_is_error():
    raw = json.dumps({"query_id": "aq001", "verdicts": [
        {"id": "p0", "grade": 5}, {"id": "p1", "grade": 0}, {"id": "p2", "grade": 2},
    ]})
    with pytest.raises(JudgeError, match="0|1|2"):  # noqa: RUF043
        parse_judge_response(raw, "aq001", ["p0", "p1", "p2"])


def test_parse_judge_response_not_json_is_error():
    with pytest.raises(JudgeError, match="not JSON"):
        parse_judge_response("I think p0 is relevant", "aq001", ["p0"])


def test_parse_judge_response_unknown_point_is_error():
    raw = json.dumps({"query_id": "aq001", "verdicts": [
        {"id": "p0", "grade": 2}, {"id": "pX", "grade": 1}, {"id": "p1", "grade": 0},
    ]})
    with pytest.raises(JudgeError, match="not in the pool"):
        parse_judge_response(raw, "aq001", ["p0", "p1"])


# ── Pool ────────────────────────────────────────────────────────────────────

def test_build_pool_dedupes_across_strategies():
    per_query = {
        "aq001": {
            "_query": "pricing",
            "fts": [{"id": "p0", "content": "a", "point_kind": "claim"}],
            "vector": [{"id": "p0", "content": "a", "point_kind": "claim"},
                       {"id": "p1", "content": "b", "point_kind": "term"}],
            "tfidf": [{"id": "p1", "content": "b", "point_kind": "term"}],
        },
    }
    pool = build_pool(per_query, {"n_points": 2}, ["fts", "vector", "tfidf"])
    assert len(pool.queries) == 1
    assert sorted(p.id for p in pool.queries[0].points) == ["p0", "p1"]
    assert pool.queries[0].query == "pricing"


def test_pool_roundtrip_schema():
    pool = _pool()
    data = pool.to_dict()
    assert data["schema_version"] == 1
    restored = Pool.from_dict(data)
    assert restored.queries[0].id == "aq001"
    assert [p.id for p in restored.queries[0].points] == ["p0", "p1", "p2"]


# ── Agreement (κ via tools/kappa.py reuse) ─────────────────────────────────

def _labels_for(grades: dict[str, int]) -> dict[str, dict[str, int]]:
    return {"aq001": grades}


def test_agreement_perfect_kappa_green():
    a = _labels_for({"p0": 2, "p1": 0, "p2": 1})
    report = agreement_report(a, {"aq001": dict(a["aq001"])}, {"aq001": "pricing"})
    assert report["kappa"] == pytest.approx(1.0)
    assert report["gate"]["verdict"] == "GREEN"
    assert report["judged_points"] == 3


def test_agreement_disjoint_kappa_low():
    a = _labels_for({"p0": 2, "p1": 2, "p2": 2})
    b = _labels_for({"p0": 0, "p1": 0, "p2": 0})
    report = agreement_report(a, b, {"aq001": "pricing"})
    assert report["kappa"] < 0.60
    assert report["gate"]["verdict"] in ("NOT_GREEN", "REVISE")


def test_agreement_no_overlap_not_green():
    a = _labels_for({"p0": 2})
    b = {"aq002": {"p9": 1}}
    report = agreement_report(a, b, {"aq001": "x", "aq002": "y"})
    assert report["kappa"] is None
    assert report["gate"]["verdict"] == "NOT_GREEN"


# ── Adjudication ────────────────────────────────────────────────────────────

def test_adjudication_acceptance_and_merge():
    a = _labels_for({"p0": 2, "p1": 2, "p2": 0, "p3": 1})
    b = _labels_for({"p0": 2, "p1": 0, "p2": 0, "p3": 1})
    tmpl = emit_rulings_template(a, b, {"aq001": "pricing"})
    assert tmpl["n_disagreements"] == 1  # only p1 differs
    assert len([r for r in tmpl["rulings"] if r["sample_type"] == "disagreement"]) == 1
    # Fill the ruling matching judge A (grade 2).
    for r in tmpl["rulings"]:
        if r["point_id"] == "p1":
            r["owner_ruling"] = 2
        elif r["owner_ruling"] is None:
            r["owner_ruling"] = r["judge_a_grade"]  # agreement-slice: confirm
    stats = adjudication_stats(tmpl)
    # agreed = 3 (p0, p2, p3), accepted ruling = p1 → 4/4 accepted.
    assert stats["acceptance"] == pytest.approx(1.0)
    assert stats["passed"] is True
    merged = merge_labels(a, b, tmpl)
    assert merged["aq001"]["p1"] == 2  # owner ruling applied


def test_adjudication_fresh_class_fails_closed():
    a = _labels_for({"p0": 2, "p1": 2})
    b = _labels_for({"p0": 2, "p1": 0})
    tmpl = emit_rulings_template(a, b, {})
    for r in tmpl["rulings"]:
        if r["point_id"] == "p1":
            r["owner_ruling"] = 1  # fresh class — neither judge said 1
        else:
            r["owner_ruling"] = r["judge_a_grade"]
    with pytest.raises(JudgeError, match="must match a judge"):
        merge_labels(a, b, tmpl)
    stats = adjudication_stats(tmpl)
    assert stats["passed"] is False


def test_adjudication_unruled_disagreement_fails_closed():
    a = _labels_for({"p0": 2, "p1": 2})
    b = _labels_for({"p0": 2, "p1": 0})
    tmpl = emit_rulings_template(a, b, {})
    for r in tmpl["rulings"]:
        if r["point_id"] == "p0":
            r["owner_ruling"] = 2
        # p1 left unruled
    with pytest.raises(JudgeError, match="unruled disagreement"):
        merge_labels(a, b, tmpl)


def test_agreement_slice_fresh_ruling_never_enters_labels():
    """Fail-closed lock (both halves):

    1. A fresh class on an AGREEMENT-SLICE point (both judges agreed) is a
       confirmation signal, NOT a re-label: per the documented acceptance
       formula ((agreed + rulings matching a judge) / n_judged) the
       combined denominator already counts agreed pairs as accepted, so
       the slice ruling content does not move the gate — and merge_labels
       can never let the invented grade enter the labels (it uses the
       judges' shared grade).
    2. The FAIL-CLOSED path for fresh classes is the disagreement path:
       merge_labels raises JudgeError (locked by
       test_adjudication_fresh_class_fails_closed).
    """
    a = _labels_for({"p0": 2, "p1": 2, "p2": 2})
    b = _labels_for({"p0": 2, "p1": 2, "p2": 2})  # no disagreements
    tmpl = emit_rulings_template(a, b, {})
    assert tmpl["n_disagreements"] == 0
    assert len(tmpl["rulings"]) >= 1  # the ≥10% agreement slice
    for r in tmpl["rulings"]:
        assert r["sample_type"] == "agreement-slice"
        r["owner_ruling"] = 1  # fresh class — neither judge said 1
    stats = adjudication_stats(tmpl)
    assert stats["accepted_rulings"] == 0
    # Documented formula: agreed pairs are already accepted → the slice
    # ruling is a confirmation signal, not a re-label.
    assert stats["acceptance"] == pytest.approx(1.0)
    assert stats["passed"] is True
    # The invented grade NEVER enters the labels (judges' grade wins).
    merged = merge_labels(a, b, tmpl)
    assert merged == {"aq001": {"p0": 2, "p1": 2, "p2": 2}}
    # Companion: the same fresh class on a DISAGREEMENT fails closed.
    a2 = _labels_for({"p0": 2, "p1": 2})
    b2 = _labels_for({"p0": 2, "p1": 0})
    tmpl2 = emit_rulings_template(a2, b2, {})
    for r in tmpl2["rulings"]:
        r["owner_ruling"] = 1 if r["sample_type"] == "disagreement" \
            else r["judge_a_grade"]
    with pytest.raises(JudgeError, match="must match a judge"):
        merge_labels(a2, b2, tmpl2)


def test_adjudication_acceptance_floor():
    a = _labels_for({"p0": 2, "p1": 2, "p2": 2, "p3": 2, "p4": 2})
    b = _labels_for({"p0": 0, "p1": 0, "p2": 0, "p3": 0, "p4": 0})
    tmpl = emit_rulings_template(a, b, {})
    # Owner picks judge A on every disagreement → 5 agreed... none agreed here.
    for r in tmpl["rulings"]:
        r["owner_ruling"] = r["judge_a_grade"]
    stats = adjudication_stats(tmpl)
    # All 5 were disagreements, all accepted → acceptance 1.0
    assert stats["acceptance"] == pytest.approx(1.0)
    assert stats["passed"] is True
