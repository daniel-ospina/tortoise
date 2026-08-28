"""#1785 graph-integrity gate — docker lane (real FalkorDB).

Runs the gate against the REAL FalkorDB server (TORTOISE_DB_URI) with real
ingests: the census ratio=1.000 invariant on a healthy question, red flags
on a truncated graph (answer session absent), the post-retrieval census
detecting mid-run loss on the REAL unshimmed client (every query sees
current state — plan P1-I), read-verify retry on an injected partial read,
and fresh-run namespace cleanup (leftover nodes from a prior partial run
are wiped → ratio=1.000). Mock reader/judge (no LLM) — the gate runs for
real (NOT retrieval_only).

Isolation (plan cycle2-P2-25): every test uses a UNIQUE graph namespace
(unique model name) and removes its debris before exit so one test's
deliberately injected leftover state cannot contaminate another test's
fresh-namespace ratio=1.000 assumption.
"""
from __future__ import annotations

import socket
import uuid

import pytest

from tests.longmem_eval.test_vector_arm import _mini
from tools.longmem_eval import run as runner
from tools.longmem_eval.judge import MockJudge
from tools.longmem_eval.reader import MockReader
from tools.longmem_eval.retrieve import (
    GATE_REASON_ANSWER_SESSION_ABSENT,
    install_gate_fault_proxy,
    reset_gate_fault_proxy,
    run_integrity_gate,
)
from tortoise.sdk import TortoiseSDK

DB_URI = "docker://localhost:6379/tortoise_test_matrix"


def _falkordb_up() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(("127.0.0.1", 6379)) == 0


pytestmark = pytest.mark.skipif(
    not _falkordb_up(), reason="FalkorDB not reachable at docker://localhost:6379"
)


@pytest.fixture(autouse=True)
def _reset_proxy():
    reset_gate_fault_proxy()
    yield
    reset_gate_fault_proxy()


def _unique_model(tag: str) -> str:
    """Unique per-test model name → unique per-question graph namespace."""
    return f"gate-{tag}-{uuid.uuid4().hex[:8]}"


def _clean_question(namespace: str, qid: str) -> None:
    sdk = TortoiseSDK(namespace=namespace)
    try:
        sdk._get_proj().g.query(
            "MATCH (p:Point {lme_question_id:$q}) DETACH DELETE p",
            params={"q": qid})
        sdk._get_proj().g.query(
            "MATCH (s:Session {lme_question_id:$q}) DETACH DELETE s",
            params={"q": qid})
    finally:
        sdk.close()


def _run_question(qid: str, model: str, *, checkpoint=None, work_dir=None,
                  seed_junk: int = 0, **over):
    """Deterministic-ingest run_evaluation on the docker lane (mock reader/
    judge — no LLM, gate runs for real). Returns (outcomes, report)."""
    instances = [next(q for q in _mini() if q["question_id"] == qid)]
    if seed_junk:
        ns = runner.question_graph_namespace(model, "query", qid)
        sdk = TortoiseSDK(namespace=ns)
        try:
            for i in range(seed_junk):
                sdk._get_proj().g.query(
                    "CREATE (p:Point {id:$id, lme_question_id:$q})",
                    params={"id": f"leftover-{qid}-{i}", "q": qid})
        finally:
            sdk.close()
    return runner.run_evaluation(
        instances, reader=MockReader(), judge=MockJudge(), ks=(5,), top_k=5,
        split="s", work_dir=work_dir, checkpoint=checkpoint,
        ingest_mode="deterministic", db_uri=DB_URI, model=model,
        query_prompt="query", **over)


def _question(qid: str) -> dict:
    return next(q for q in _mini() if q["question_id"] == qid)


def test_docker_census_ratio_invariant_healthy():
    """A healthy question on a FRESH namespace produces a gate-green
    outcome (no gate reasons) — the ratio tier + presence + evidence-mark
    censuses all pass on the real client."""
    model = _unique_model("healthy")
    qid = "mini_ie_user_001"
    ns = runner.question_graph_namespace(model, "query", qid)
    try:
        outcomes, _ = _run_question(qid, model)
        out = outcomes[0]
        assert out["gate_reasons"] == []
        assert out["post_retrieval_reasons"] == []
        assert out["pool_size"] > 0
        assert out["pool_size"] == out.get("points_total")
    finally:
        _clean_question(ns, qid)


def test_docker_gate_red_when_answer_session_absent():
    """Delete the answer session's points after a green ingest → the
    post-retrieval-style census flags answer_session_absent on the REAL
    unshimmed client (plan P1-I: every query sees current state — mid-run
    removals ARE visible without fresh-session machinery)."""
    model = _unique_model("absent")
    qid = "mini_ie_user_001"
    ns = runner.question_graph_namespace(model, "query", qid)
    try:
        outcomes, _ = _run_question(qid, model)
        assert outcomes[0]["gate_reasons"] == []
        # remove the answer session's points (mini-s1 = index 1)
        sdk = TortoiseSDK(namespace=ns)
        try:
            sdk._get_proj().g.query(
                "MATCH (p:Point {lme_question_id:$q, lme_session_index:1}) "
                "DETACH DELETE p", params={"q": qid})
        finally:
            sdk.close()
        res = run_integrity_gate(
            TortoiseSDK(namespace=ns)._get_proj(), _question(qid), qid,
            ingest_stats={"turns": 30, "chunks": 10, "points": 0})
        assert GATE_REASON_ANSWER_SESSION_ABSENT in res["reasons"]
    finally:
        _clean_question(ns, qid)


def test_docker_fresh_run_cleanup_wipes_leftovers():
    """A FRESH run's per-question namespace cleanup wipes leftover nodes
    from a prior partial run → gate green (a leftover would push
    census_overflow)."""
    model = _unique_model("cleanup")
    qid = "mini_ie_user_001"
    ns = runner.question_graph_namespace(model, "query", qid)
    try:
        outcomes, _ = _run_question(qid, model, seed_junk=5)
        assert outcomes[0]["gate_reasons"] == []
    finally:
        _clean_question(ns, qid)


def test_docker_read_verify_retries_faulted_first_read():
    """An injected partial read on the ratio second shape is read-verified:
    the faulted first read retries to consensus and the healthy question
    stays gate-green (never a phantom graph_truncated)."""
    model = _unique_model("readverify")
    qid = "mini_ie_user_001"
    ns = runner.question_graph_namespace(model, "query", qid)
    state = {"faulted": False}

    def proxy(query_fn, cypher, params):
        if "count(DISTINCT p)" in cypher and not state["faulted"]:
            state["faulted"] = True
            return _FakeResult([[1]])
        return query_fn(cypher, params=params)

    install_gate_fault_proxy(proxy)
    try:
        outcomes, _ = _run_question(qid, model)
        assert outcomes[0]["gate_reasons"] == []
    finally:
        reset_gate_fault_proxy()
        _clean_question(ns, qid)


def test_docker_persistent_pool_fault_never_phantom_truncated():
    """A PERSISTENT fault on the pool-size shape engages the read-verify
    consensus/error path — NEVER a phantom graph_truncated flag on a
    healthy question (plan P2-8/P2-2: fail-closed, not a verdict). The
    resulting hard census class aborts the run via the watchdog's FIRST-
    hard-class arm (P2-11: guaranteed-fail, no other arm samples it)."""
    model = _unique_model("persistfault")
    qid = "mini_ie_user_001"
    ns = runner.question_graph_namespace(model, "query", qid)

    def proxy(query_fn, cypher, params):
        if "count(DISTINCT p)" in cypher:
            return _FakeResult([[1]])  # stably wrong traversal
        return query_fn(cypher, params=params)

    install_gate_fault_proxy(proxy)
    try:
        with pytest.raises(runner.WatchdogAbortError, match="census_error"):
            _run_question(qid, model)
    finally:
        reset_gate_fault_proxy()
        _clean_question(ns, qid)


class _FakeResult:
    def __init__(self, result_set):
        self.result_set = result_set
