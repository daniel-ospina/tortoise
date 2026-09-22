"""#2578 (Task 5) — v2-lane structural probe tests.

Embedded-safe by construction: wave planning reads committed JSON only, and
the structural measurement is exercised against a monkeypatched ``_cypher``
— no docker, no graph, no network, no LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.longmem_eval import measure_temporal as mt  # noqa: E402, RUF100
from tools.longmem_eval import probe_v2 as pv  # noqa: E402, RUF100

CENSUS = Path(__file__).resolve().parent.parent / "_assembly_census.json"

REQUIRED_KEYS = {
    "qid", "cls", "gold_sessions", "gold_sessions_with_events",
    "objects_total", "point_about_object_edges", "event_about_object_edges",
    "dated_started_at", "undated_events", "entity_name_match",
    "substrate_present",
}


@pytest.fixture(scope="module")
def census_rows() -> list[dict]:
    return mt.load_census(CENSUS)["rows"]


@pytest.fixture(scope="module")
def pinned(census_rows: list[dict]) -> list[dict]:
    subset = mt.deterministic_subset(census_rows)
    assert len(subset) == 55  # the pin this whole module is scoped to
    return subset


# ── wave_questions: 15, pinned, class-spanning, deterministic ─────────────

def test_wave_questions_is_15_pinned_and_class_spanning(pinned):
    wave = pv.wave_questions(pinned)
    assert len(wave) == pv.PROBE_WAVE_SIZE == 15
    assert {r["qid"] for r in wave} <= {r["qid"] for r in pinned}
    # spans all three ANALYSIS_CLASSES, each with >= 1 row
    assert {r["cls"] for r in wave} == set(mt.ANALYSIS_CLASSES)
    for cls in mt.ANALYSIS_CLASSES:
        assert any(r["cls"] == cls for r in wave), cls
    # the 2-row current-state class is fully covered (both members)
    assert sum(1 for r in wave if r["cls"] == "current-state") == 2


def test_wave_questions_is_deterministic(pinned):
    assert pv.wave_questions(pinned) == pv.wave_questions(list(pinned))


def test_wave_questions_refuses_the_133_census(census_rows):
    assert len(census_rows) == 133
    with pytest.raises(ValueError, match="55"):
        pv.wave_questions(census_rows)


def test_wave_questions_refuses_a_wrong_sized_subset(pinned):
    with pytest.raises(ValueError, match="54"):
        pv.wave_questions(pinned[:-1])


# ── probe_guard: the pre-registered refusals ──────────────────────────────

def test_probe_guard_refuses_non_55_subset():
    with pytest.raises(ValueError, match="133"):
        pv.probe_guard(133, 1, wave1_done=False)
    with pytest.raises(ValueError, match="54"):
        pv.probe_guard(54, 1, wave1_done=False)


def test_probe_guard_refuses_wave2_before_wave1_completes():
    with pytest.raises(ValueError, match="wave 1"):
        pv.probe_guard(55, 2, wave1_done=False)


def test_probe_guard_passes_wave1_and_wave2_after_wave1():
    pv.probe_guard(55, 1, wave1_done=False)   # wave 1 needs no prior wave
    pv.probe_guard(55, 1, wave1_done=True)
    pv.probe_guard(55, 2, wave1_done=True)    # full 55 only after wave 1


# ── measure_question_structure against a fake graph ───────────────────────

def _question(*, qid: str = "q1", cls: str = "interval",
              turns: list[list[dict]] | None = None) -> dict:
    sessions = turns if turns is not None else [
        [{"role": "user", "content": "Ava went to Paris with Marco."}],
    ]
    return {
        "question_id": qid,
        "cls": cls,
        "question": "Where did Ava go?",
        "answer": "Paris",
        "haystack_session_ids": [f"s{i}" for i in range(len(sessions))],
        "haystack_sessions": sessions,
        "haystack_dates": ["2025-01-01"] * len(sessions),
        "answer_session_ids": ["s0"],
    }


def _fake_query(*, objects: list[str], point_edges: int, event_edges: int,
                dated: int, undated: int, gold_rows: list[str]):
    """Dispatch on Cypher substrings (order matters: DISTINCT before the
    dated-count query, which shares ``startedAt IS NOT NULL``)."""
    def _query(sdk, query, params=None):
        if "RETURN o.name" in query:
            return [[name] for name in objects]
        if "aboutObject" in query and "Point" in query:
            return [[point_edges]]
        if "aboutObject" in query and "Event" in query:
            return [[event_edges]]
        if "DISTINCT e.sessionId" in query:
            return [[sid] for sid in gold_rows]
        if "startedAt IS NOT NULL" in query:
            return [[dated]]
        if "startedAt IS NULL" in query:
            return [[undated]]
        return []
    return _query


def test_measure_question_structure_empty_graph(monkeypatch):
    monkeypatch.setattr(pv, "_cypher", lambda sdk, q, params=None: [])
    out = pv.measure_question_structure(
        object(), _question(), gold_session_ids=["s0"], namespace="ns")
    assert set(out) >= REQUIRED_KEYS
    assert set(out["entity_name_match"]) == {"matched", "total", "rate"}
    assert out["qid"] == "q1" and out["cls"] == "interval"
    assert out["namespace"] == "ns"
    assert out["gold_sessions"] == 1
    assert out["gold_sessions_with_events"] == 0
    assert out["objects_total"] == 0
    assert out["point_about_object_edges"] == 0
    assert out["event_about_object_edges"] == 0
    assert out["dated_started_at"] == 0
    assert out["undated_events"] == 0
    assert out["substrate_present"] is False
    # candidates ARE derived from the answer turns even on an empty graph
    assert out["entity_name_match"]["total"] >= 1
    assert out["entity_name_match"]["matched"] == 0
    assert out["entity_name_match"]["rate"] == 0.0


def test_measure_question_structure_substrate_present(monkeypatch):
    monkeypatch.setattr(pv, "_cypher", _fake_query(
        objects=["Ava", "Paris", "Marco"], point_edges=4, event_edges=2,
        dated=3, undated=1, gold_rows=["s0"]))
    out = pv.measure_question_structure(
        object(), _question(), gold_session_ids=["s0"], namespace="ns")
    assert out["objects_total"] == 3
    assert out["point_about_object_edges"] == 4
    assert out["event_about_object_edges"] == 2
    assert out["gold_sessions_with_events"] == 1
    assert out["dated_started_at"] == 3
    assert out["undated_events"] == 1
    assert out["substrate_present"] is True
    match = out["entity_name_match"]
    assert match["total"] >= 1 and match["matched"] >= 1
    assert 0.0 < match["rate"] <= 1.0


def test_measure_question_structure_binary_needs_all_three_reads(monkeypatch):
    """substrate_present is the AND of the three assembler-required reads —
    objects alone, or objects+point-edges with no dated gold event, is NOT
    substrate (honest binary, never rounded up)."""
    question = _question()
    monkeypatch.setattr(pv, "_cypher", _fake_query(
        objects=["Ava"], point_edges=0, event_edges=0, dated=0, undated=1,
        gold_rows=[]))
    assert pv.measure_question_structure(
        object(), question, gold_session_ids=["s0"],
        namespace="ns")["substrate_present"] is False
    monkeypatch.setattr(pv, "_cypher", _fake_query(
        objects=["Ava"], point_edges=2, event_edges=0, dated=0, undated=1,
        gold_rows=[]))
    assert pv.measure_question_structure(
        object(), question, gold_session_ids=["s0"],
        namespace="ns")["substrate_present"] is False


# ── probe_report aggregation ──────────────────────────────────────────────

def _result(qid: str, cls: str, present: bool, matched: int, total: int,
            objects: int = 0, point_edges: int = 0) -> dict:
    return {
        "qid": qid, "cls": cls, "namespace": "ns",
        "substrate_present": present, "objects_total": objects,
        "point_about_object_edges": point_edges,
        "event_about_object_edges": 0, "dated_started_at": 0,
        "gold_sessions_with_events": 1 if present else 0,
        "entity_name_match": {
            "matched": matched, "total": total,
            "rate": (matched / total) if total else None},
    }


def test_probe_report_aggregates_two_results_with_variance():
    results = [
        _result("q1", "interval", True, 1, 2, objects=3, point_edges=2),
        _result("q2", "ordering/compare", False, 0, 1),
    ]
    report = pv.probe_report(results, wave=1,
                             saturation={"rule": "wave1-unanimity"})
    assert report["n"] == 2
    assert report["substrate_present"] == 1
    assert report["substrate_present_rate"] == 0.5
    assert report["unanimous"] is False
    assert report["saturated"] is False
    assert report["entity_name_match"] == {
        "matched": 1, "total": 3, "rate": pytest.approx(1 / 3)}
    assert report["by_class"]["interval"]["n"] == 1
    assert report["by_class"]["interval"]["substrate_present_rate"] == 1.0
    assert report["by_class"]["interval"]["objects_total"] == 3
    assert report["by_class"]["ordering/compare"]["n"] == 1
    assert report["by_class"]["ordering/compare"]["substrate_present_rate"] \
        == 0.0
    # the caller's stop-rule context is echoed alongside the computed flag
    assert report["saturation"] == {
        "rule": "wave1-unanimity", "saturated": False}


def test_probe_report_saturates_on_unanimity_and_on_wave2():
    present = _result("q1", "interval", True, 1, 1, objects=1, point_edges=1)
    absent = _result("q2", "interval", False, 0, 1)
    unanimous = pv.probe_report([present, present], wave=1, saturation={})
    assert unanimous["unanimous"] is True
    assert unanimous["saturated"] is True
    assert unanimous["entity_name_match"]["rate"] == 1.0
    # wave 2 is the full pinned 55 — sampling capacity exhausted
    full = pv.probe_report([present, absent], wave=2, saturation={})
    assert full["saturated"] is True
    assert full["unanimous"] is False
    assert full["saturation"]["saturated"] is True


# ── CLI planner (no graph / no network) ──────────────────────────────────

def test_main_dry_run_prints_plan_and_stops(capsys):
    rc = pv.main(["--wave", "1", "--census", str(CENSUS), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "pinned subset: 55 Q" in out
    assert "wave" in out and "-> 15 Q" in out
    assert "stop rule" in out
    for row in pv.wave_questions(mt.deterministic_subset(
            mt.load_census(CENSUS)["rows"])):
        assert row["qid"] in out


def test_main_dry_run_wave2_requires_wave1_and_prints_55(capsys):
    with pytest.raises(ValueError, match="wave 1"):
        pv.main(["--wave", "2", "--census", str(CENSUS), "--dry-run"])
    rc = pv.main(["--wave", "2", "--census", str(CENSUS), "--wave1-done",
                  "--dry-run"])
    assert rc == 0
    assert "-> 55 Q" in capsys.readouterr().out


def test_main_without_dry_run_refuses_without_touching_the_graph(capsys):
    rc = pv.main(["--wave", "1", "--census", str(CENSUS)])
    assert rc == 3
    captured = capsys.readouterr()
    assert "live execution is NOT wired" in captured.err
    assert "wave         : 1 -> 15 Q" in captured.out

def test_measure_question_structure_accepts_explicit_census_class(monkeypatch):
    """A raw LongMemEval row carries `question_type`, NOT the census class —
    passing the row alone must not silently collapse every result into one
    report bucket. The explicit `cls=` wins."""
    monkeypatch.setattr(pv, "_cypher", lambda sdk, q, params=None: [])
    row = {"question_id": "q1", "question_type": "temporal-reasoning"}
    res = pv.measure_question_structure(
        None, row, gold_session_ids=[], namespace="ns",
        cls="ordering/compare")
    assert res["cls"] == "ordering/compare"
    # and the dataset row alone yields an empty class rather than a guess
    assert pv.measure_question_structure(
        None, row, gold_session_ids=[], namespace="ns")["cls"] == ""
