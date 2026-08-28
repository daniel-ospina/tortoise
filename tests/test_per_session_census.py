"""#1785 Task 3 — loss-location diagnostic (per-session census replay).

Pins the per-session census machinery: session-boundary detection over the
deterministic id pattern, the read-verified per-session census (a partial
read is labeled read-fault and retried; only a stable two-read consensus
counts as loss), the post-ingest census + trace + verdict, and the
load-injection workers (replay under concurrent write pressure).
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from tests.longmem_eval.test_vector_arm import _mini
from tools.longmem_eval.retrieve import install_gate_fault_proxy, reset_gate_fault_proxy
from tools.longmem_eval.run import (
    REPLAY_VERDICT_H6A,
    REPLAY_VERDICT_H6B,
    REPLAY_VERDICT_INCONCLUSIVE,
    REPLAY_VERDICT_UNEXERCISED,
    _PerSessionCensus,
    _ReplayLoadWorkers,
)
from tortoise.sdk import TortoiseSDK


@pytest.fixture(autouse=True)
def _no_fault_proxy():
    reset_gate_fault_proxy()
    yield
    reset_gate_fault_proxy()


def _fresh_sdk() -> TortoiseSDK:
    return TortoiseSDK(str(Path(tempfile.mkdtemp()) / "lme.db"))


def _write_session_batch(proj, qid: str, si: int, n_turns: int,
                         *, tag: str = "t") -> None:
    """Write one session's Phase-A-style batch (Session node + turn points
    + chunk points + CONTAINS edges) using the deterministic id pattern the
    census's session-boundary detector keys on. For a 3-turn session with
    chunk_turns=2 the expected per-session census is 3 turns + 2 chunks = 5."""
    proj.g.query(
        "MERGE (s:Session {id:$id}) SET s.lme_question_id=$q, "
        "s.lme_session_index=$si",
        params={"id": f"lme:{qid}:s{si}", "q": qid, "si": si})
    for ti in range(n_turns):
        proj.g.query(
            "CREATE (p:Point {id:$id, lme_question_id:$q, "
            "lme_session_index:$si, has_answer:false, pointKind:'event'})",
            params={"id": f"lme:{qid}:s{si}:{tag}{ti}", "q": qid, "si": si})
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
            "MERGE (s)-[:CONTAINS]->(t)",
            params={"sid": f"lme:{qid}:s{si}", "tid": f"lme:{qid}:s{si}:{tag}{ti}"})
    for ci in range(2):  # chunk_turns=2 → 2 chunk windows for 3 turns
        proj.g.query(
            "CREATE (p:Point {id:$id, lme_question_id:$q, "
            "lme_session_index:$si, has_answer:false, "
            "pointKind:'session-transcript'})",
            params={"id": f"lme:{qid}:s{si}:{tag}c{ci}", "q": qid, "si": si})
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (t:Point {id:$tid}) "
            "MERGE (s)-[:CONTAINS]->(t)",
            params={"sid": f"lme:{qid}:s{si}",
                    "tid": f"lme:{qid}:s{si}:{tag}c{ci}"})


def test_census_interleaves_after_each_session_batch():
    """The census fires after EACH session's batch (Phase A then Phase C) —
    the trace records per-session observed counts at each phase."""
    q = _mini()[0]
    qid = q["question_id"]
    sdk = _fresh_sdk()
    proj = sdk._get_proj()
    census = _PerSessionCensus(proj, qid, q, chunk_turns=2,
                               signature_reproduced=True)
    with census:
        # Phase A: all sessions' raw writes, sequentially
        _write_session_batch(proj, qid, 0, 3)
        _write_session_batch(proj, qid, 1, 3)
        # Phase C: payload writes, sequentially
        _write_session_batch(proj, qid, 0, 3, tag="p")
        _write_session_batch(proj, qid, 1, 3, tag="p")
    trace = census.finalize({"turns": 6, "chunks": 0, "points": 0})
    assert set(trace["sessions"]) == {"0", "1"}
    # session 0 Phase A: 3 turns + 2 chunks observed; Phase C: 10 (adds 3
    # payload points + 2 payload chunks)
    assert trace["sessions"]["0"]["phase_a_observed"] == 5
    assert trace["sessions"]["0"]["phase_c_observed"] == 10
    assert trace["sessions"]["1"]["phase_a_observed"] == 5
    assert trace["post_ingest"]["observed"] == 20
    assert trace["faults"] == []
    assert trace["verdict"] == REPLAY_VERDICT_INCONCLUSIVE


def test_census_detects_during_ingest_loss_h6a():
    """Loss accumulating DURING ingest (a Phase-A census observes fewer
    than turns+chunks) → H6a (never-durably-written under load)."""
    q = _mini()[0]
    qid = q["question_id"]
    sdk = _fresh_sdk()
    proj = sdk._get_proj()
    census = _PerSessionCensus(proj, qid, q, chunk_turns=2,
                               signature_reproduced=True)
    with census:
        _write_session_batch(proj, qid, 0, 3)
        # session 1's Phase A writes are SWALLOWED (only 1 of 3 turns land)
        _write_session_batch(proj, qid, 1, 1)
        # Phase C interleaves per-session (the real ingest's write order) —
        # the Phase-C s0 boundary fires session 1's Phase A census.
        _write_session_batch(proj, qid, 0, 3, tag="p")
        _write_session_batch(proj, qid, 1, 3, tag="p")
    trace = census.finalize({"turns": 6, "chunks": 4, "points": 0})
    assert trace["verdict"] == REPLAY_VERDICT_H6A


def test_census_detects_post_ingest_loss_h6b():
    """Loss appearing only between the per-session censuses and the post-
    ingest census → H6b (H6c is UNREACHABLE — no restart cycle in the
    protocol; plan P2-2)."""
    q = _mini()[0]
    qid = q["question_id"]
    sdk = _fresh_sdk()
    proj = sdk._get_proj()
    census = _PerSessionCensus(proj, qid, q, chunk_turns=2,
                               signature_reproduced=True)
    with census:
        _write_session_batch(proj, qid, 0, 3)
        _write_session_batch(proj, qid, 1, 3)
    # post-ingest removal: delete session 1's points before the finalize
    proj.g.query("MATCH (p:Point {lme_question_id:$q}) WHERE "
                 "p.lme_session_index = 1 DETACH DELETE p",
                 params={"q": qid})
    trace = census.finalize({"turns": 6, "chunks": 4, "points": 0})
    assert trace["verdict"] == REPLAY_VERDICT_H6B


def test_census_read_verify_fault_is_labeled_not_loss():
    """A partial read during the per-session census is labeled read-fault
    and retried — a stable two-read consensus is required before anything
    counts as loss (plan Task 3 read-verify fault labeling)."""
    q = _mini()[0]
    qid = q["question_id"]
    sdk = _fresh_sdk()
    proj = sdk._get_proj()
    state = {"faulted": False}

    def proxy(query_fn, cypher, params):
        if ("lme_session_index:$si" in cypher
                and "count(DISTINCT p)" in cypher and not state["faulted"]):
            state["faulted"] = True
            return _FakeResult([[1]])  # first traversal read short → retried
        return query_fn(cypher, params=params)

    install_gate_fault_proxy(proxy)
    census = _PerSessionCensus(proj, qid, q, chunk_turns=2,
                               signature_reproduced=True)
    with census:
        _write_session_batch(proj, qid, 0, 3)
        _write_session_batch(proj, qid, 1, 3)
    trace = census.finalize({"turns": 6, "chunks": 0, "points": 0})
    assert trace["verdict"] == REPLAY_VERDICT_INCONCLUSIVE
    assert trace["faults"] == []  # retried to consensus — not a fault label


def test_census_persistent_fault_is_labeled_read_fault():
    q = _mini()[0]
    qid = q["question_id"]
    sdk = _fresh_sdk()
    proj = sdk._get_proj()

    def proxy(query_fn, cypher, params):
        if "-[:CONTAINS]->(p:Point)" in cypher and "count(DISTINCT" in cypher:
            return _FakeResult([[0]])  # stably wrong traversal shape only
        return query_fn(cypher, params=params)

    install_gate_fault_proxy(proxy)
    census = _PerSessionCensus(proj, qid, q, chunk_turns=2,
                               signature_reproduced=True)
    with census:
        _write_session_batch(proj, qid, 0, 3)
        _write_session_batch(proj, qid, 1, 3)
    trace = census.finalize({"turns": 6, "chunks": 0, "points": 0})
    assert any(f["label"] == "read-fault" for f in trace["faults"])


def test_census_unexercised_verdict_without_signature():
    """The failure signature must be REPRODUCED for a pass-with-evidence
    verdict; without it the verdict is 'H6a unexercised — env remediation
    only' (explicitly NOT passing, plan P2-3)."""
    q = _mini()[0]
    qid = q["question_id"]
    sdk = _fresh_sdk()
    proj = sdk._get_proj()
    census = _PerSessionCensus(proj, qid, q, chunk_turns=2,
                               signature_reproduced=False)
    with census:
        _write_session_batch(proj, qid, 0, 3)
        _write_session_batch(proj, qid, 1, 3)
    trace = census.finalize({"turns": 6, "chunks": 0, "points": 0})
    assert trace["verdict"] == REPLAY_VERDICT_UNEXERCISED


def test_census_gc_events_in_loss_window_flip_h6a_to_h6b():
    """An H6a verdict must be supported by 'no GC event in the loss window'
    — a fork-GC removal inside the per-session window is indistinguishable
    from never-written at that check (final-verification P2)."""
    q = _mini()[0]
    qid = q["question_id"]
    sdk = _fresh_sdk()
    proj = sdk._get_proj()
    census = _PerSessionCensus(proj, qid, q, chunk_turns=2,
                               signature_reproduced=True,
                               gc_events=[{"si": 1, "errno": 17}])
    with census:
        _write_session_batch(proj, qid, 0, 3)
        _write_session_batch(proj, qid, 1, 1)  # loss in session 1's window
        _write_session_batch(proj, qid, 0, 3, tag="p")
        _write_session_batch(proj, qid, 1, 3, tag="p")
    trace = census.finalize({"turns": 6, "chunks": 4, "points": 0})
    assert trace["verdict"] == REPLAY_VERDICT_H6B


def test_census_trace_written_to_work_dir(tmp_path):
    q = _mini()[0]
    qid = q["question_id"]
    sdk = _fresh_sdk()
    proj = sdk._get_proj()
    census = _PerSessionCensus(proj, qid, q, chunk_turns=2,
                               work_dir=str(tmp_path),
                               signature_reproduced=True)
    with census:
        _write_session_batch(proj, qid, 0, 3)
        _write_session_batch(proj, qid, 1, 3)
    census.finalize({"turns": 6, "chunks": 4, "points": 0})
    out = tmp_path / f"per_session_census_{qid}.json"
    assert out.exists()
    trace = json.loads(out.read_text(encoding="utf-8"))
    assert trace["qid"] == qid
    assert trace["verdict"] == REPLAY_VERDICT_INCONCLUSIVE


def test_replay_load_workers_write_pressure():
    """The load-injection workers write synthetic points into scratch
    namespaces while the replay runs — reproducing the degraded run's write
    pressure. Stop is idempotent and bounded."""
    workers = _ReplayLoadWorkers(lambda: _fresh_sdk(), n=2)
    workers.start()
    import time
    time.sleep(0.3)
    workers.stop()
    workers.stop()  # idempotent


class _FakeResult:
    def __init__(self, result_set):
        self.result_set = result_set
