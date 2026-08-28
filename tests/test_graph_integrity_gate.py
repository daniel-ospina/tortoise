"""#1785 graph-integrity gate — unit + synthetic-graph fixtures (plan Task 1).

The re-validation's 5 session@20=0.0 questions were graph-integrity
artifacts: their answer sessions' nodes were ABSENT from the graph at
retrieval time (pool_size : points_total ratio 0.005–0.196 vs exactly
1.000 healthy), NOT retrieval misses. This file pins the gate predicate:
ratio tier (sub-1.0 truncation flag / >1.0 anomaly), answer-session
presence (red-on-any, abstention exemption keyed on EMPTY
``answer_session_ids`` only), evidence-mark census (D5 pointKind-filtered,
write-observed, loss-only red), per-session floor (write-path stat source),
dataset-join fail-closed resolution, read-verify two-read consensus, the
falsification-trigger truth table, and the historical checkpoint ratio-tier
replay (flags exactly the 15 sub-1.0 outcomes; passes 31 full-graph).

Synthetic graphs are seeded on embedded TortoiseSDK servers (fresh tempdir
per test) mirroring the real ingest node shapes (Session nodes + CONTAINS
edges + turn/chunk points with lme_question_id / lme_session_index /
has_answer / pointKind).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tests.longmem_eval.test_vector_arm import _mini
from tools.longmem_eval import run as runner
from tools.longmem_eval.judge import MockJudge
from tools.longmem_eval.reader import MockReader
from tools.longmem_eval.retrieve import (
    GATE_REASON_ANSWER_SESSION_ABSENT,
    GATE_REASON_CENSUS_ERROR,
    GATE_REASON_CENSUS_OVERFLOW,
    GATE_REASON_DATASET_JOIN_ERROR,
    GATE_REASON_EVIDENCE_MARK_CENSUS,
    GATE_REASON_GRAPH_TRUNCATED,
    classify_ratio,
    install_gate_fault_proxy,
    reset_gate_fault_proxy,
    resolve_answer_session_indices,
    run_integrity_gate,
)
from tools.longmem_eval.run import (
    _gate_red,
    _legs_degraded,
    _RunWatchdog,
    falsification_trigger,
    replay_verdict,
)
from tortoise.sdk import TortoiseSDK

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"
REPLAY_FIXTURE = Path(__file__).parent / "fixtures" / "reval_ratio_replay.json"

#: Fixture digest (computed at fixture build time; a rebuild changes CI).
REPLAY_FIXTURE_DIGEST = "847979402669b794"
#: The historical checkpoint's git_sha + the dataset digest the fixture pins.
REPLAY_CHECKPOINT_GIT_SHA = "57f439782457eb54b6bca855dc878f0cc7ba928b"
REPLAY_DATASET_DIGEST = "d6f21ea9d60a0d56"

#: The 15 sub-1.0-ratio qids the replay must flag (plan §1 / Task 1 Step 3).
EXPECTED_TRUNCATED_QIDS = {
    "118b2229", "1e043500", "e47becba", "51a45a95", "58bf7951",
    "c5e8278d", "6ade9755", "6f9b354f", "58ef2f1c", "f8c5f88b",
    "af8d2e46", "dccbc061", "c8c3f81d", "8ebdbe50", "6b168ec8",
}


@pytest.fixture(autouse=True)
def _no_fault_proxy():
    reset_gate_fault_proxy()
    yield
    reset_gate_fault_proxy()


def _fresh_sdk() -> TortoiseSDK:
    return TortoiseSDK(str(Path(tempfile.mkdtemp()) / "lme.db"))


def _seed_question(proj, qid: str, sessions: dict[int, int],
                   marks: dict[int, int] | None = None,
                   point_kind: str | None = None,
                   session_transcript_marks: int = 0) -> None:
    """Seed a question-shaped graph: session ``si`` gets ``n_turns`` turn
    points (``pointKind`` = ``point_kind`` or 'event'), the first
    ``marks.get(si)`` of them marked has_answer. Optionally adds
    ``session_transcript_marks`` raw chunks carrying has_answer (the D5
    pointKind-exclusion fixture)."""
    marks = marks or {}
    for si, n_turns in sessions.items():
        proj.g.query(
            "CREATE (s:Session {id:$id, lme_question_id:$q, "
            "lme_session_index:$si})",
            params={"id": f"lme:{qid}:s{si}", "q": qid, "si": si})
        for ti in range(n_turns):
            has = ti < marks.get(si, 0)
            proj.g.query(
                "CREATE (p:Point {id:$id, lme_question_id:$q, "
                "lme_session_index:$si, has_answer:$h, pointKind:$pk})",
                params={"id": f"lme:{qid}:s{si}:t{ti}", "q": qid, "si": si,
                        "h": has, "pk": point_kind or "event"})
            proj.g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": f"lme:{qid}:s{si}", "tid": f"lme:{qid}:s{si}:t{ti}"})
        for ci in range(session_transcript_marks):
            proj.g.query(
                "CREATE (p:Point {id:$id, lme_question_id:$q, "
                "lme_session_index:$si, has_answer:true, "
                "pointKind:'session-transcript'})",
                params={"id": f"lme:{qid}:s{si}:c{ci}", "q": qid, "si": si})
            proj.g.query(
                "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
                "MERGE (s)-[:CONTAINS]->(t)",
                params={"sid": f"lme:{qid}:s{si}",
                        "tid": f"lme:{qid}:s{si}:c{ci}"})


def _q(qid: str) -> dict:
    return next(q for q in _mini() if q["question_id"] == qid)


# ── pure predicates ─────────────────────────────────────────────────────────

def test_classify_ratio_healthy_exact():
    assert classify_ratio(1382, 1382) is None
    assert classify_ratio(1418, 1418) is None


def test_classify_ratio_sub_1_0_flags_truncation():
    # healthy is exactly 1.000 — ANY sub-1.0 ratio is a truncated graph
    assert classify_ratio(8, 1710) == GATE_REASON_GRAPH_TRUNCATED
    assert classify_ratio(276, 1408) == GATE_REASON_GRAPH_TRUNCATED
    # sub-1.0 hits (e47becba 0.026 / af8d2e46 0.148 / c8c3f81d 0.313 /
    # 6b168ec8 0.722) are still gated — the reject tier is presence-driven
    # and the old 0.25 ratio constant is retracted.
    assert classify_ratio(39, 1500) == GATE_REASON_GRAPH_TRUNCATED
    assert classify_ratio(722, 1000) == GATE_REASON_GRAPH_TRUNCATED


def test_classify_ratio_overflow_is_anomaly():
    # ratio > 1.0 = census counting leftovers from a prior partial run
    assert classify_ratio(1500, 1382) == GATE_REASON_CENSUS_OVERFLOW


def test_classify_ratio_zero_expected():
    assert classify_ratio(0, 0) == GATE_REASON_CENSUS_ERROR


def test_resolve_answer_session_indices_normal():
    q = _q("mini_ie_user_001")  # answer_session_ids ['mini-s1']
    idxs, err = resolve_answer_session_indices(q)
    assert err is None
    assert idxs == [1]


def test_resolve_answer_session_indices_empty_is_exemption():
    q = _q("mini_abs_005_abs")  # EMPTY answer_session_ids — the ONLY key
    idxs, err = resolve_answer_session_indices(q)
    assert err is None
    assert idxs == []


def test_resolve_answer_session_indices_none_key_fails_closed():
    q = dict(_q("mini_ie_user_001"))
    q.pop("answer_session_ids")
    _, err = resolve_answer_session_indices(q)
    assert err == GATE_REASON_DATASET_JOIN_ERROR
    assert err is not None  # fail-closed (None/key-absent never skips)
    q["answer_session_ids"] = None
    _, err = resolve_answer_session_indices(q)
    assert err == GATE_REASON_DATASET_JOIN_ERROR


def test_resolve_answer_session_indices_absent_id_fails_closed():
    q = dict(_q("mini_ie_user_001"))
    q["answer_session_ids"] = ["mini-nonexistent"]
    _, err = resolve_answer_session_indices(q)
    assert err == GATE_REASON_DATASET_JOIN_ERROR


def test_resolve_answer_session_indices_duplicate_haystack_fails_closed():
    q = dict(_q("mini_ie_user_001"))
    q["haystack_session_ids"] = ["mini-s0", "mini-s1", "mini-s1"]
    _, err = resolve_answer_session_indices(q)
    assert err == GATE_REASON_DATASET_JOIN_ERROR


def test_resolve_answer_session_indices_out_of_range_fails_closed():
    q = dict(_q("mini_ie_user_001"))
    q["answer_session_ids"] = ["mini-s2"]
    q["haystack_session_ids"] = ["mini-s0", "mini-s2"]  # idx 1
    q["haystack_sessions"] = [[]]  # only 1 session → idx 1 out of range
    _, err = resolve_answer_session_indices(q)
    assert err == GATE_REASON_DATASET_JOIN_ERROR


def test_resolve_answer_session_indices_empty_haystack_fails_closed():
    q = dict(_q("mini_ie_user_001"))
    q["haystack_session_ids"] = []
    _, err = resolve_answer_session_indices(q)
    assert err == GATE_REASON_DATASET_JOIN_ERROR


# ── the gate on synthetic graphs ────────────────────────────────────────────

def test_gate_green_healthy_v2():
    q = _q("mini_ie_user_001")  # answer session = mini-s1 (index 1)
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3},
                   marks={1: 1})
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0,
                      "evidence_points": 1})
    assert res["reasons"] == []
    assert res["ratio"] == 1.0


def test_gate_green_healthy_legacy_no_points_key():
    # legacy ingest_haystack stats have NO 'points' key — denominator is
    # turns + chunks; evidence_points is a SUBSET of turn Points (adding
    # it would double-count → false graph_truncated on every healthy leg).
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3},
                   marks={1: 1})
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "evidence_turns": 1,
                      "evidence_points": 1})
    assert res["reasons"] == []
    assert res["ratio"] == 1.0


def test_gate_ratio_flags_sub_1_0_truncation():
    # 118b2229/58bf7951 shapes: the graph is truncated AND the answer
    # session absent → graph_truncated (flag) + answer_session_absent.
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3})  # s1 gone
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0})
    assert GATE_REASON_GRAPH_TRUNCATED in res["reasons"]
    assert GATE_REASON_ANSWER_SESSION_ABSENT in res["reasons"]


def test_gate_ratio_sub_1_0_hit_is_gated_not_rejected():
    # c8c3f81d 0.313 / 6b168ec8 0.722 shapes — sub-1.0 HITS: the answer
    # session IS present (presence green) but the graph is truncated →
    # gated with graph_truncated, NEVER a hard-reject reason.
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 1, 1: 3})
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0})
    assert GATE_REASON_GRAPH_TRUNCATED in res["reasons"]
    assert GATE_REASON_ANSWER_SESSION_ABSENT not in res["reasons"]


def _seed_leftover(proj, qid: str, si: int = 0) -> None:
    """Seed a leftover node (from a prior partial run) WITH its CONTAINS
    edge — the real shape a partial run leaves behind."""
    proj.g.query(
        "CREATE (p:Point {id:$id, lme_question_id:$q, lme_session_index:$si})",
        params={"id": f"lme:{qid}:s{si}:leftover", "q": qid, "si": si})
    proj.g.query(
        "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
        "MERGE (s)-[:CONTAINS]->(t)",
        params={"sid": f"lme:{qid}:s{si}", "tid": f"lme:{qid}:s{si}:leftover"})


def test_gate_within_run_collision_stays_green():
    """P2-1 (plan): a within-run content collision OR-links a point created
    by an EARLIER session into a LATER session's Session node (the Phase-C
    CONTAINS loop runs regardless of the idempotent skip). The presence
    traversal shape keys on the point's OWN lme_session_index, so the
    healthy multi-session question stays gate-green (never a false
    census_error from the cross-session CONTAINS edge)."""
    q = _q("mini_msr_002")  # answer_session_ids ['mini-s0', 'mini-s1']
    sdk = _fresh_sdk()
    proj = sdk._get_proj()
    qid = q["question_id"]
    _seed_question(proj, qid, {0: 3, 1: 3}, marks={1: 1})
    # the collision: session 1's Session node CONTAINS a point whose OWN
    # lme_session_index is 0 (a session-B payload OR-linked an earlier
    # session's point)
    proj.g.query(
        "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
        "MERGE (s)-[:CONTAINS]->(t)",
        params={"sid": f"lme:{qid}:s1", "tid": f"lme:{qid}:s0:t0"})
    # an extracted point created this run (marked) — the write-observed
    # created-id census sees it; the Phase-C CONTAINS loop links it to its
    # session (the real ingest's shape)
    proj.g.query(
        "CREATE (p:Point {id:$id, lme_question_id:$q, lme_session_index:1, "
        "has_answer:true, pointKind:'statement'})",
        params={"id": f"lme:{qid}:s1:px0", "q": qid})
    proj.g.query(
        "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
        "MERGE (s)-[:CONTAINS]->(t)",
        params={"sid": f"lme:{qid}:s1", "tid": f"lme:{qid}:s1:px0"})
    res = run_integrity_gate(
        proj, q, qid,
        ingest_stats={"turns": 6, "chunks": 0, "points": 1,
                      "evidence_points": 1,
                      "created_point_ids": [f"lme:{qid}:s1:px0"]})
    assert res["reasons"] == []


def test_gate_ratio_overflow_fails_closed():
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    proj = sdk._get_proj()
    _seed_question(proj, q["question_id"], {0: 3, 1: 3})
    _seed_leftover(proj, q["question_id"])  # leftover → ratio 7/6 > 1.0
    res = run_integrity_gate(
        proj, q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0})
    assert GATE_REASON_CENSUS_OVERFLOW in res["reasons"]


def test_gate_multi_answer_session_red_on_any():
    q = _q("mini_msr_002")  # answer_session_ids ['mini-s0', 'mini-s1']
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {1: 3})  # s0 gone
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0})
    assert GATE_REASON_ANSWER_SESSION_ABSENT in res["reasons"]


def test_gate_abstention_exemption_skips_presence_only():
    # mini_abs_005_abs: EMPTY answer_session_ids — the presence check is
    # skipped, but the RATIO tier still applies (a truncated graph on an
    # abstention question is still integrity loss — P2-11).
    q = _q("mini_abs_005_abs")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3})
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0})
    assert res["reasons"] == []  # healthy abstention — green


def test_gate_abstention_truncated_graph_still_gated():
    q = _q("mini_abs_005_abs")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 2})
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0})
    assert GATE_REASON_GRAPH_TRUNCATED in res["reasons"]


def test_gate_abstention_overflow_still_gated_anomaly():
    q = _q("mini_abs_005_abs")
    sdk = _fresh_sdk()
    proj = sdk._get_proj()
    _seed_question(proj, q["question_id"], {0: 3, 1: 3})
    _seed_leftover(proj, q["question_id"])
    res = run_integrity_gate(
        proj, q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0})
    assert GATE_REASON_CENSUS_OVERFLOW in res["reasons"]


def test_gate_evidence_mark_loss_only_red():
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    # 2 evidence-bearing points created this run, but the graph lost one
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3},
                   marks={1: 1})
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0,
                      "evidence_points": 2,
                      "created_point_ids": [
                          f"lme:{q['question_id']}:s1:t0",
                          f"lme:{q['question_id']}:s1:t1"]})
    assert GATE_REASON_EVIDENCE_MARK_CENSUS in res["reasons"]


def test_gate_evidence_mark_inflation_not_red():
    # OR-in / NOOP-fold / within-run collisions INFLATE graph has_answer
    # without incrementing evidence_points — loss-only red never fires on
    # inflation (plan P2-1); the write-observed created-id census stays
    # green.
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3},
                   marks={0: 3, 1: 3})  # inflated marks
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0,
                      "evidence_points": 1,
                      "created_point_ids": [f"lme:{q['question_id']}:s1:t0"]})
    assert res["reasons"] == []


def test_gate_evidence_mark_pointkind_filter_keeps_v2_green():
    # v2 session-transcript chunks carry has_answer=contains_evidence — a
    # naive has_answer count EXCEEDS evidence_points; the D5 pointKind
    # filter keeps the census green (plan P1-1).
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3},
                   marks={1: 1}, session_transcript_marks=4)
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 8, "points": 0,
                      "evidence_points": 1})
    assert res["reasons"] == []


def test_gate_per_session_floor_red_on_reduced_set():
    # per-session floor (write-path stat is the ONE floor source): session
    # 1's evidence set reduced below floor (expected 8, tolerance 5 →
    # floor 3; observed 1) → evidence_mark_census red (H6 loss-location
    # attribution).
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3},
                   marks={1: 1})
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0,
                      "evidence_points": 1,
                      "per_session_evidence_points": {"1": 8}})
    assert GATE_REASON_EVIDENCE_MARK_CENSUS in res["reasons"]


def test_gate_per_session_floor_green_on_intact_set():
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3},
                   marks={1: 1})
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0,
                      "evidence_points": 1,
                      "per_session_evidence_points": {"1": 1}})
    assert res["reasons"] == []


def test_gate_floor_expected_zero_stays_green():
    # P2-11: a PRESENT session with zero pointKind-filtered evidence stays
    # green — floor max(1, 0−T)=1 must not silently gate-red.
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3})  # no marks
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0,
                      "evidence_points": 0,
                      "per_session_evidence_points": {"1": 0}})
    assert res["reasons"] == []


def test_gate_floor_legacy_shape():
    """P2-12 (plan): a LEGACY-shaped floor fixture (no ``points`` stat key;
    evidence marks on turn points) — floor green on an intact set, red on a
    reduced evidence-bearing set (mirrors the P2-3 legacy pointKind fixture)."""
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 8},
                   marks={1: 8})  # intact 8-mark legacy evidence set
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 11, "chunks": 0, "evidence_turns": 8,
                      "evidence_points": 8,
                      "per_session_evidence_points": {"1": 8}})
    assert res["reasons"] == []  # intact legacy set — floor green
    sdk2 = _fresh_sdk()
    _seed_question(sdk2._get_proj(), q["question_id"], {0: 3, 1: 8},
                   marks={1: 1})  # reduced evidence set
    res2 = run_integrity_gate(
        sdk2._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 11, "chunks": 0, "evidence_turns": 8,
                      "evidence_points": 8,
                      "per_session_evidence_points": {"1": 8}})
    assert GATE_REASON_EVIDENCE_MARK_CENSUS in res2["reasons"]


def test_gate_floor_multi_session_red_on_any():
    """P2-12 (plan): multi-session floor semantics are RED-ON-ANY — a
    surplus in session A must not mask session B reduced below its floor.
    The fixture constructs a surplus in A (9 marks >= floor 3) while B is
    reduced (1 < floor 3); the global evidence census is GREEN (10 marks ==
    evidence_points 10), so the red fires ONLY from the B-floor arm."""
    q = _q("mini_msr_002")  # answer_session_ids ['mini-s0', 'mini-s1']
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 12, 1: 3},
                   marks={0: 9, 1: 1})  # A surplus at floor, B reduced
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 15, "chunks": 0, "points": 0,
                      "evidence_points": 10,
                      "per_session_evidence_points": {"0": 8, "1": 8}})
    assert GATE_REASON_EVIDENCE_MARK_CENSUS in res["reasons"]


def test_gate_floor_tolerance_boundary():
    """P2-12 (plan): tolerance-boundary fixtures — exactly-tolerance green
    vs tolerance+1 red (floor = max(1, expected − T) with T = GATE_FLOOR_T)."""
    import tools.longmem_eval.retrieve as _rt

    t = _rt.GATE_FLOOR_T
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    expected = 10
    at_boundary = expected - t  # exactly-tolerance → floor green
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 12},
                   marks={1: at_boundary})
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 15, "chunks": 0, "points": 0,
                      "evidence_points": at_boundary,
                      "per_session_evidence_points": {"1": expected}})
    assert res["reasons"] == []
    below = expected - t - 1  # tolerance+1 below → floor red
    sdk2 = _fresh_sdk()
    _seed_question(sdk2._get_proj(), q["question_id"], {0: 3, 1: 12},
                   marks={1: below})
    res2 = run_integrity_gate(
        sdk2._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 15, "chunks": 0, "points": 0,
                      "evidence_points": below,
                      "per_session_evidence_points": {"1": expected}})
    assert GATE_REASON_EVIDENCE_MARK_CENSUS in res2["reasons"]


def test_gate_lost_mark_cross_check_red_flags_mark_stripped():
    """P1-4 (plan): the non-scoped lost-mark cross-check — a mapped answer
    session with >=1 point but ZERO marks while the write-path stat claims
    evidence red-flags a mark-stripped session (H6 attribution) even when
    both created-id counts are 0."""
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3})  # NO marks
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0,
                      "evidence_points": 0,
                      "per_session_evidence_points": {"1": 5}})
    assert GATE_REASON_EVIDENCE_MARK_CENSUS in res["reasons"]


def test_gate_lost_mark_cross_check_healthy_skipped_session_green():
    """P1-4 (plan): a healthy SKIPPED session (marks intact from the prior
    run, nothing added this run) must NOT false-red via the cross-check."""
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3},
                   marks={1: 4})  # marks intact
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0,
                      "evidence_points": 0,  # nothing created this run
                      "per_session_evidence_points": {"1": 5}})
    assert res["reasons"] == []


def test_gate_pre_red_skips_post_retrieval_census(tmp_path):
    """P2-3/P2-8 (plan): an already-red question SKIPS the post-retrieval
    census — its outcome carries ``gate_reasons`` (pre-red) with
    ``post_retrieval_reasons == []`` (phase-keyed reason-list shape); the
    run completes without a watchdog abort (1/5 gated = 0.2 <= 0.25)."""
    qid0 = "mini_ie_user_001"

    def proxy(query_fn, cypher, params):
        q = (params or {}).get("q")
        if q != qid0:
            return query_fn(cypher, params=params)
        c = cypher
        if "OPTIONAL MATCH (m:Point" in c:
            return _FakeResult([[1, None, False]])  # ns=1, no members
        if "count(DISTINCT p)" in c:
            return _FakeResult([[1]])
        if "-[:CONTAINS]->(p:Point)" in c:
            return _FakeResult([[0]])  # presence traversal: sessions absent
        if "lme_session_index:$si" in c:
            return _FakeResult([[0]])  # presence absence probe: absent
        if "RETURN count(*)" in c and "has_answer" not in c \
                and "WHERE" not in c:
            return _FakeResult([[1]])  # ratio probe agrees: truncated
        return query_fn(cypher, params=params)

    install_gate_fault_proxy(proxy)
    try:
        outcomes, _ = runner.run_evaluation(
            _mini(), reader=MockReader(), judge=MockJudge(), ks=(5,), top_k=5,
            split="s", work_dir=str(tmp_path), ingest_mode="deterministic")
    finally:
        reset_gate_fault_proxy()
    by_qid = {o["question_id"]: o for o in outcomes}
    assert GATE_REASON_GRAPH_TRUNCATED in by_qid[qid0]["gate_reasons"]
    assert by_qid[qid0]["post_retrieval_reasons"] == []  # pre-red skips post
    for other in ("mini_msr_002", "mini_tr_003", "mini_ku_004",
                  "mini_abs_005_abs"):
        assert by_qid[other]["gate_reasons"] == []
        assert by_qid[other]["post_retrieval_reasons"] == []


def test_gate_ratio_probe_disagreement_is_census_error():
    """P2-15 (plan): the third-shape probe contradicting the consensus
    (wrong-count disagreement) treats the reads as FAULTED — census_error,
    never graph_truncated / answer_session_absent."""
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 1})  # 1 point, expected 6
    state = {"probed": False}

    def proxy(query_fn, cypher, params):
        if "RETURN count(*)" in cypher and "count(DISTINCT" not in cypher \
                and not state["probed"] and "lme_session_index" not in cypher:
            state["probed"] = True
            return _FakeResult([[6]])  # fresh probe sees the FULL graph
        return query_fn(cypher, params=params)

    install_gate_fault_proxy(proxy)
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0})
    assert GATE_REASON_CENSUS_ERROR in res["reasons"]
    assert GATE_REASON_GRAPH_TRUNCATED not in res["reasons"]


def test_gate_dataset_join_error_pool_size_still_read():
    # a join-failing question STILL produces the pool_size readout and
    # carries dataset_join_error (plan cycle4-P3).
    q = dict(_q("mini_ie_user_001"))
    q["answer_session_ids"] = ["mini-gone"]
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3})
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0})
    assert GATE_REASON_DATASET_JOIN_ERROR in res["reasons"]
    assert res["pool_size"] == 6


def test_gate_extraction_error_fold_no_integrity_reasons():
    # a session extraction exception (stats errors / error_census non-empty)
    # classifies via the ERROR attribution — NEVER census_overflow on the
    # corrupted denominator, never a bare answer_session_absent (cycle3-
    # P1-11 + cycle4-P3 sibling fixture).
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3})  # s1 absent
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0,
                      "errors": ["s1: ExtractorError: boom"],
                      "error_census": {"extractor:exception": 1}})
    assert res["reasons"] == []
    assert res["census"].get("error_fold") is True


def test_gate_expected_zero_is_census_error():
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 0, "chunks": 0, "points": 0})
    assert GATE_REASON_CENSUS_ERROR in res["reasons"]


def test_gate_retrieval_only_exempt():
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 2})  # truncated
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0},
        retrieval_only=True)
    assert res["reasons"] == []


def test_gate_resumed_suppresses_ratio_presence_primary():
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    proj = sdk._get_proj()
    _seed_question(proj, q["question_id"], {0: 3, 1: 3})
    _seed_leftover(proj, q["question_id"])
    # leftovers from a prior partial run push ratio > 1.0 — suppressed on
    # resume; presence still primary.
    res = run_integrity_gate(
        proj, q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0}, resumed=True)
    assert res["reasons"] == []


def test_gate_created_id_contradiction_is_census_error():
    # shape-independent truncation anchor (cycle3-P2-31): a consensus
    # namespace count SHORTER than the client-known created-id set size is
    # provably faulted → census_error, never a phantom graph_truncated.
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3})
    created = [f"lme:{q['question_id']}:s1:tx{i}" for i in range(30)]
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 30,
                      "created_point_ids": created})
    assert GATE_REASON_CENSUS_ERROR in res["reasons"]
    assert GATE_REASON_GRAPH_TRUNCATED not in res["reasons"]


# ── read-verify fault scenarios (fault-proxy shim) ──────────────────────────

def test_read_verify_faulted_first_read_retries_to_consensus():
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3})
    state = {"faulted": False}

    def proxy(query_fn, cypher, params):
        if ("count(DISTINCT p)" in cypher) and not state["faulted"]:
            state["faulted"] = True
            return _FakeResult([[2]])  # wrong first read — retried
        return query_fn(cypher, params=params)

    install_gate_fault_proxy(proxy)
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0})
    assert res["reasons"] == []
    assert res["ratio"] == 1.0


def test_read_verify_persistent_mismatch_is_census_error():
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3})

    def proxy(query_fn, cypher, params):
        if "count(DISTINCT p)" in cypher:
            return _FakeResult([[5]])  # stably wrong on the traversal shape
        return query_fn(cypher, params=params)

    install_gate_fault_proxy(proxy)
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0})
    assert GATE_REASON_CENSUS_ERROR in res["reasons"]
    assert GATE_REASON_GRAPH_TRUNCATED not in res["reasons"]


def test_read_verify_absence_probe_contradiction_is_census_error():
    """P2-9 (plan): BOTH consensus reads agree the answer session is absent
    (a faulted session), but the raw third-shape probe (a FRESH read)
    confirms presence → the reads were faulted → census_error, NEVER
    answer_session_absent (a probe contradicting the consensus means the
    consensus is faulted, not that the session is lost)."""
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3})

    def proxy(query_fn, cypher, params):
        if "OPTIONAL MATCH (m:Point" in cypher:
            # faulted folded read: ns_count=6, members only for session 0
            return _FakeResult([[6, 0, False], [6, 0, False], [6, 0, False]])
        if "-[:CONTAINS]->(p:Point)" in cypher and "count(DISTINCT" not in cypher:
            # faulted per-index traversal: session 1 reads as absent
            if params and params.get("si") == 1:
                return _FakeResult([[0]])
            return query_fn(cypher, params=params)
        return query_fn(cypher, params=params)  # the probe is a fresh read

    install_gate_fault_proxy(proxy)
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0})
    assert GATE_REASON_CENSUS_ERROR in res["reasons"]
    assert GATE_REASON_ANSWER_SESSION_ABSENT not in res["reasons"]


def test_read_verify_persistent_fault_is_census_error_never_loss():
    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3})

    def proxy(query_fn, cypher, params):
        raise RuntimeError("server went away")

    install_gate_fault_proxy(proxy)
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0})
    assert GATE_REASON_CENSUS_ERROR in res["reasons"]
    assert GATE_REASON_ANSWER_SESSION_ABSENT not in res["reasons"]
    assert GATE_REASON_GRAPH_TRUNCATED not in res["reasons"]


class _FakeResult:
    def __init__(self, result_set):
        self.result_set = result_set


# ── falsification trigger (pure, full 6-input truth table) ──────────────────

def test_falsification_trigger_arms_on_all_green_miss():
    assert falsification_trigger(ratio_ok=True, presence_ok=True, floor_ok=True,
                                 post_census_ok=True, legs_healthy=True,
                                 strategy_timeout=False, miss=True)


def test_falsification_trigger_suppression_arms():
    kwargs = dict(ratio_ok=True, presence_ok=True, floor_ok=True,
                  post_census_ok=True, legs_healthy=True,
                  strategy_timeout=False, miss=True)
    for key, value in (("ratio_ok", False), ("presence_ok", False),
                       ("floor_ok", False), ("post_census_ok", False),
                       ("legs_healthy", False), ("strategy_timeout", True),
                       ("miss", False)):
        case = dict(kwargs)
        case[key] = value
        assert not falsification_trigger(**case), key


def test_falsification_trigger_not_a_miss_is_noop():
    assert not falsification_trigger(ratio_ok=True, presence_ok=True,
                                     floor_ok=True, post_census_ok=True,
                                     legs_healthy=True, strategy_timeout=False,
                                     miss=False)


# ── leg-health predicate (conservative observable-signal) ───────────────────

def test_legs_degraded_clean_legs():
    out = {"legs": [{"leg": "fts", "reason": "ok", "count": 25},
                    {"leg": "vector", "reason": "ok", "count": 120},
                    {"leg": "structural", "reason": "ok", "count": 120}]}
    assert _legs_degraded(out, "single-session-user") == []


def test_legs_degraded_fts_dead():
    out = {"legs": [{"leg": "fts", "reason": "empty_results", "count": 0},
                    {"leg": "vector", "reason": "ok", "count": 120}]}
    degraded = _legs_degraded(out, "single-session-user")
    assert any(name == "fts" for name, _ in degraded)


def test_legs_degraded_benign_reasons_are_healthy():
    out = {"legs": [{"leg": "fts", "reason": "index_missing", "count": 0},
                    {"leg": "vector", "reason": "no_embeddings", "count": 0}]}
    assert _legs_degraded(out, "single-session-user") == []


def test_legs_degraded_timeout_is_degraded():
    out = {"legs": [{"leg": "fts", "reason": "timeout", "count": 0}]}
    assert any(name == "fts" for name, _ in _legs_degraded(out, "single-session-user"))


def test_legs_degraded_tr_by_design_empty_event_leg_no_signal():
    # TR questions run with entity_types=("point","event") — the event FTS
    # leg is EMPTY BY DESIGN; a signal requires ALL fts entries dead.
    out = {"legs": [{"leg": "fts", "reason": "empty_results", "count": 0},
                    {"leg": "fts", "reason": "ok", "count": 40},
                    {"leg": "vector", "reason": "ok", "count": 120}]}
    assert _legs_degraded(out, "temporal-reasoning") == []
    out2 = {"legs": [{"leg": "fts", "reason": "empty_results", "count": 0},
                     {"leg": "fts", "reason": "empty_results", "count": 0}]}
    assert any(name == "fts" for name, _ in _legs_degraded(out2, "temporal-reasoning"))


def test_legs_degraded_legless_is_vacuous():
    assert _legs_degraded({"no": "legs"}, "single-session-user") == []


def test_legs_degraded_below_floor_is_observable():
    out = {"legs": [{"leg": "fts", "reason": "ok", "count": 10}]}  # < F_fts=24
    assert any(name == "fts" for name, _ in _legs_degraded(out, "single-session-user"))


def test_gate_red_union():
    out = {"gate_reasons": ["graph_truncated"],
           "post_retrieval_reasons": []}
    assert _gate_red(out) == ["graph_truncated"]
    out2 = {"gate_reasons": [], "post_retrieval_reasons": ["census_error"]}
    assert _gate_red(out2) == ["census_error"]
    out3 = {"gate_reasons": ["answer_session_absent"],
            "post_retrieval_reasons": ["graph_truncated"]}
    assert _gate_red(out3) == ["answer_session_absent", "graph_truncated"]


# ── historical checkpoint ratio-tier replay (Task 1 Step 3(a)) ──────────────

def test_replay_fixture_pins_source_triple():
    data = json.loads(REPLAY_FIXTURE.read_text(encoding="utf-8"))
    src = data["source"]
    assert src["checkpoint_git_sha"] == REPLAY_CHECKPOINT_GIT_SHA
    assert src["dataset_digest"] == REPLAY_DATASET_DIGEST
    assert len(data["outcomes"]) == 46
    assert len(data["failures"]) == 4


def test_replay_fixture_digest_is_committed():
    body = REPLAY_FIXTURE.read_text(encoding="utf-8").strip()
    import hashlib
    digest = hashlib.sha256(body.encode()).hexdigest()[:16]
    assert digest == REPLAY_FIXTURE_DIGEST


def test_replay_ratio_tier_flags_exactly_15():
    """The gate flags exactly the 15 sub-1.0-ratio outcomes (the re-val's
    degraded population) and passes all 31 full-graph outcomes — the
    acceptance proof the gate detects the real defect without false
    positives on full graphs (plan §7 / Task 1 Step 3(a))."""
    data = json.loads(REPLAY_FIXTURE.read_text(encoding="utf-8"))
    flagged = []
    passed = []
    for o in data["outcomes"]:
        if o["pool_size"] is None or not o["expected"]:
            continue
        if classify_ratio(o["pool_size"], o["expected"]) is not None:
            flagged.append(o["qid"])
        else:
            passed.append(o["qid"])
    assert set(flagged) == EXPECTED_TRUNCATED_QIDS
    assert len(flagged) == 15
    assert len(passed) == 31
    # the 5 miss-questions are all flagged
    for miss in ("118b2229", "1e043500", "51a45a95", "dccbc061", "58bf7951"):
        assert miss in flagged
    # the 2 documented controls are passed
    assert "0862e8bf" in passed
    assert "001be529" in passed


def test_replay_failures_are_noop():
    """The gate is a NO-OP over the 4 failures — failures never enter
    outcomes and the gate predicate must not touch them (plan P2-10); the
    fixture's failure qids are NOT part of the flagged/passed outcome sets."""
    data = json.loads(REPLAY_FIXTURE.read_text(encoding="utf-8"))
    outcome_qids = {o["qid"] for o in data["outcomes"]}
    assert len(data["failures"]) == 4
    for f in data["failures"]:
        assert f["qid"] not in outcome_qids  # failures never enter outcomes
        assert f["qid"] not in EXPECTED_TRUNCATED_QIDS


# ── mid-run watchdog (Task 1 Step 5) ───────────────────────────────────────

def _wd(n=50, revalidate=False):
    return _RunWatchdog(revalidate=revalidate, n_questions=n)


def test_watchdog_short_run_rolling_arms_inert():
    """Runs < 10 questions keep the rolling-window arms INERT (plan
    cycle2-P3) — a 5-Q smoke must not abort on a naturally-short small-
    graph leg or a window-level signal; the cumulative arms still apply."""
    wd = _wd(n=5)
    for _ in range(5):
        reason = wd.record(
            qid="q", gate_reasons=[], post_retrieval_reasons=[],
            strategy_timeout=False, census_latency_ms=50.0,
            consec_census_error=5, legs_degraded=True)
        assert reason is None
    # cumulative arm still fires on a short run (timeout bound 2)
    wd2 = _wd(n=5)
    for i in range(2):
        reason = wd2.record(
            qid="q", gate_reasons=[], post_retrieval_reasons=[],
            strategy_timeout=True, census_latency_ms=50.0,
            consec_census_error=0, legs_degraded=False)
        if i == 1:
            assert reason == "strategy_timeout"


def test_watchdog_revalidate_first_gate_red_aborts():
    wd = _wd(revalidate=True)
    reason = wd.record(
        qid="q", gate_reasons=["graph_truncated"],
        post_retrieval_reasons=[], strategy_timeout=False,
        census_latency_ms=10.0, consec_census_error=0, legs_degraded=False)
    assert reason == "gate_red"
    # exactly one abort record
    assert wd.record(
        qid="q", gate_reasons=[], post_retrieval_reasons=[],
        strategy_timeout=False, census_latency_ms=10.0,
        consec_census_error=0, legs_degraded=False) is None


def test_watchdog_revalidate_first_timeout_aborts():
    wd = _wd(revalidate=True)
    reason = wd.record(
        qid="q", gate_reasons=[], post_retrieval_reasons=[],
        strategy_timeout=True, census_latency_ms=10.0,
        consec_census_error=0, legs_degraded=False)
    assert reason == "strategy_timeout"


def test_watchdog_revalidate_first_mid_write_failure_aborts():
    wd = _wd(revalidate=True)
    assert wd.record_failure() == "mid_write_failure"


def test_watchdog_gate_red_rolling_fraction_cumulative():
    """The gate-red-fraction arm fires on a >0.25 last-10 fraction with a
    cumulative crossing count >= 2; a single one-off crossing never aborts."""
    wd = _wd()
    # 3 gate-red in a full 10-window → 0.3 > 0.25 → crossing 1 (no abort)
    for i in range(10):
        red = i < 3
        wd.record(
            qid=f"q{i}", gate_reasons=["graph_truncated"] if red else [],
            post_retrieval_reasons=[], strategy_timeout=False,
            census_latency_ms=10.0, consec_census_error=0,
            legs_degraded=False)
    # recover fully, then another 3-red window → crossing 2 → abort
    for i in range(10):
        wd.record(
            qid=f"r{i}", gate_reasons=[], post_retrieval_reasons=[],
            strategy_timeout=False, census_latency_ms=10.0,
            consec_census_error=0, legs_degraded=False)
    reason = None
    for i in range(10):
        reason = wd.record(
            qid=f"w{i}", gate_reasons=["graph_truncated"] if i < 3 else [],
            post_retrieval_reasons=[], strategy_timeout=False,
            census_latency_ms=10.0, consec_census_error=0,
            legs_degraded=False)
        if reason:
            break
    assert reason == "gate_red"


def test_watchdog_first_hard_census_class_aborts():
    wd = _wd()
    reason = wd.record(
        qid="q", gate_reasons=["census_error"],
        post_retrieval_reasons=[], strategy_timeout=False,
        census_latency_ms=10.0, consec_census_error=0, legs_degraded=False)
    assert reason == "census_error"


def test_watchdog_gated_fraction_cumulative_bound(monkeypatch):
    """The whole-run gated fraction past the shared certifier bound aborts —
    the spread-gating case (gated questions spread so the rolling 10-window
    never exceeds 0.25) that the rolling arms miss (plan cycle2-P2-16)."""
    import tools.longmem_eval.run as _run_mod

    monkeypatch.setattr(_run_mod, "GATE_MAX_GATED", 0.1)
    wd = _wd(n=50)
    reason = None
    gated = 0
    for i in range(50):
        red = (i % 5 == 0)  # 10 gated, spaced 5 apart → ≤2 per window
        if red:
            gated += 1
        reason = wd.record(
            qid=f"q{i}", gate_reasons=["graph_truncated"] if red else [],
            post_retrieval_reasons=[], strategy_timeout=False,
            census_latency_ms=10.0, consec_census_error=0,
            legs_degraded=False)
        if reason:
            break
    assert gated == 6  # the loop broke at the 6th gated (q25)
    # rolling window max 2/10 = 0.2 < 0.25 never fires; the whole-run
    # fraction crosses 0.1 at the 6th gated (6/50 = 0.12)
    assert reason == "gated_fraction"


def test_watchdog_scoped_failure_rate_arm():
    """The mid-write failure-rate arm is SCOPED — a gated-free run keeps the
    #1776 certifier semantics un-aborted (cycle4-P1-12); a run with gated
    outcomes + >0.05 failure rate aborts."""
    wd = _wd(n=50)
    for _ in range(4):
        assert wd.record_failure() is None  # gated-free → arm suppressed
    assert wd._failures == 0  # not even counted on the gated-free path
    wd2 = _wd(n=50)
    wd2.record(
        qid="q", gate_reasons=["graph_truncated"],
        post_retrieval_reasons=[], strategy_timeout=False,
        census_latency_ms=10.0, consec_census_error=0, legs_degraded=False)
    reason = None
    for _ in range(4):
        reason = wd2.record_failure()
        if reason:
            break
    assert reason == "mid_write_failure"  # 3/50 = 0.06 > 0.05, min 2


def test_watchdog_leg_dead_two_consecutive():
    wd = _wd()
    # 9 healthy questions, then 4 consecutive leg-degraded (2 cumulative
    # crossings → abort)
    for i in range(9):
        wd.record(qid=f"h{i}", gate_reasons=[], post_retrieval_reasons=[],
                  strategy_timeout=False, census_latency_ms=10.0,
                  consec_census_error=0, legs_degraded=False)
    reason = None
    for i in range(4):
        reason = wd.record(qid=f"d{i}", gate_reasons=[],
                           post_retrieval_reasons=[], strategy_timeout=False,
                           census_latency_ms=10.0, consec_census_error=0,
                           legs_degraded=True)
        if reason:
            break
    assert reason == "leg_dead"


def test_watchdog_one_off_crossing_never_aborts():
    """P2-13: a single one-off crossing window (one 3x latency spike) never
    aborts — the cumulative crossing count must be >= 2."""
    wd = _wd()
    for i in range(10):
        wd.record(qid=f"q{i}", gate_reasons=[], post_retrieval_reasons=[],
                  strategy_timeout=False,
                  census_latency_ms=350.0 if i == 5 else 10.0,
                  consec_census_error=0, legs_degraded=False)
    for i in range(10):
        wd.record(qid=f"r{i}", gate_reasons=[], post_retrieval_reasons=[],
                  strategy_timeout=False, census_latency_ms=10.0,
                  consec_census_error=0, legs_degraded=False)
    assert wd.record(qid="x", gate_reasons=[], post_retrieval_reasons=[],
                     strategy_timeout=False, census_latency_ms=10.0,
                     consec_census_error=0, legs_degraded=False) is None


def test_watchdog_exactly_one_abort_record():
    """Two arms crossing in the same window produce EXACTLY ONE
    degraded_aborted record (P2-13)."""
    wd = _wd(revalidate=True)
    r1 = wd.record(qid="q", gate_reasons=["graph_truncated"],
                   post_retrieval_reasons=[], strategy_timeout=True,
                   census_latency_ms=10.0, consec_census_error=0,
                   legs_degraded=False)
    r2 = wd.record(qid="q2", gate_reasons=[], post_retrieval_reasons=[],
                   strategy_timeout=False, census_latency_ms=10.0,
                   consec_census_error=0, legs_degraded=False)
    assert r1 == "gate_red"  # first-wins
    assert r2 is None         # latched
    assert wd.record_failure() is None


def test_watchdog_aborts_run_end_to_end(tmp_path):
    """P0-1 pin (review): the watchdog abort raised on the SUCCESS path is
    NOT swallowed by the per-question failure handler (which would record a
    bogus failure entry and continue) — the run aborts with a DISTINCT
    WatchdogAbortError and the ``degraded_aborted`` marker rides the
    checkpoint (a resume refuses it)."""
    cp = tmp_path / "state.json"

    def proxy(query_fn, cypher, params):
        if "count(DISTINCT p)" in cypher:
            return _FakeResult([[1]])  # persistent traversal fault
        return query_fn(cypher, params=params)

    install_gate_fault_proxy(proxy)
    try:
        with pytest.raises(runner.WatchdogAbortError):
            runner.run_evaluation(
                _mini()[:2], reader=MockReader(), judge=MockJudge(),
                ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
                checkpoint=str(cp), ingest_mode="deterministic",
                revalidate=True)
    finally:
        reset_gate_fault_proxy()
    data = json.loads(cp.read_text(encoding="utf-8"))
    assert data.get("degraded_aborted", {}).get("reason") == "gate_red"
    # the aborted run's completed outcomes never reach an aggregate claim —
    # the exception propagated (asserted above); the resume refuses it.
    with pytest.raises(runner.CheckpointStaleError, match="degraded-aborted"):
        runner._load_checkpoint(str(cp), run_key="embedded__hybrid__default__default",
                                retriever="hybrid")


def test_gate_census_timeout_yields_census_error_within_budget():
    """P2-1 pin (review): a census query BLOCKING past T_census (the AOF-
    fsync-stall fault class — server accepts, never responds) is converted
    to census_error by the proxy deadline within the stated budget — never
    a multi-second hang (plan cycle2-P1-7)."""
    import time as _time

    q = _q("mini_ie_user_001")
    sdk = _fresh_sdk()
    _seed_question(sdk._get_proj(), q["question_id"], {0: 3, 1: 3})

    def proxy(query_fn, cypher, params):
        if "count(DISTINCT p)" in cypher:
            _time.sleep(3.0)  # true stall — longer than T_census
            return query_fn(cypher, params=params)
        return query_fn(cypher, params=params)

    install_gate_fault_proxy(proxy)
    t0 = _time.monotonic()
    res = run_integrity_gate(
        sdk._get_proj(), q, q["question_id"],
        ingest_stats={"turns": 6, "chunks": 0, "points": 0},
        timeout_ms=200, retry_n=1)
    elapsed = _time.monotonic() - t0
    assert GATE_REASON_CENSUS_ERROR in res["reasons"]
    assert elapsed < 2.0  # 3 attempts x 200ms budget — never a hang


# ── replay verdict rule (Task 3, pure) ──────────────────────────────────────

def test_replay_verdict_h6a_during_ingest_loss():
    trace = {
        "signature_reproduced": True,
        "sessions": {"0": {"phase_a_expected": 10, "phase_a_observed": 4}},
        "post_ingest": {"expected": 100, "observed": 100},
    }
    assert replay_verdict(trace) == "H6a"


def test_replay_verdict_h6a_requires_no_gc_in_window():
    trace = {
        "signature_reproduced": True,
        "sessions": {"0": {"phase_a_expected": 10, "phase_a_observed": 4}},
        "post_ingest": {"expected": 100, "observed": 100},
    }
    assert replay_verdict(trace, gc_events=[{"si": 0}]) == "H6b"


def test_replay_verdict_h6b_post_ingest_only_loss():
    trace = {
        "signature_reproduced": True,
        "sessions": {"0": {"phase_a_expected": 10, "phase_a_observed": 10}},
        "post_ingest": {"expected": 100, "observed": 60},
    }
    assert replay_verdict(trace) == "H6b"


def test_replay_verdict_inconclusive_when_signature_reproduced():
    trace = {
        "signature_reproduced": True,
        "sessions": {"0": {"phase_a_expected": 10, "phase_a_observed": 10}},
        "post_ingest": {"expected": 100, "observed": 100},
    }
    assert replay_verdict(trace) == "INCONCLUSIVE"


def test_replay_verdict_unexercised_not_passing():
    # signature NOT reproduced → 'H6a unexercised — env remediation only'
    # (explicitly NOT a passing Task 3 verdict, plan P2-3).
    trace = {
        "signature_reproduced": False,
        "sessions": {"0": {"phase_a_expected": 10, "phase_a_observed": 10}},
        "post_ingest": {"expected": 100, "observed": 100},
    }
    assert replay_verdict(trace) == "H6a unexercised — env remediation only"
