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

import contextlib
import logging
import os
import socket
import uuid

import pytest

from tests._embedded import _is_missing_graph_error
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

DB_URI = os.environ.get(
    "TORTOISE_DB_URI",
    # CI's falkordb service requires the password (python-ci.yml
    # `--requirepass falkordb`); local passwordless instances can override.
    "docker://:falkordb@localhost:6379/tortoise_test_matrix",
)


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
        proj = sdk._get_proj()
        graph_name = f"team_{namespace}"
        # #1884: the opt-in-only sweep no longer removes team_* graphs, so
        # each test drops its OWN isolated graph — GRAPH.DELETE while it
        # still holds nodes. Verified on the server: delete() raises
        # "Invalid graph operation on empty key" on a key that never
        # existed, succeeds on a live key, and a DETACH query on a missing
        # graph AUTO-CREATES the key — so the fallback must re-delete to
        # avoid leaving an empty shell (review P2).
        try:
            proj.db.select_graph(graph_name).delete()
            return
        except Exception as ex:
            if _is_missing_graph_error(ex):
                return  # graph never existed — nothing to clean
            logging.getLogger(__name__).warning(
                "clean_question GRAPH.DELETE failed for %s: %r",
                graph_name, ex)
        proj.g.query(
            "MATCH (p:Point {lme_question_id:$q}) DETACH DELETE p",
            params={"q": qid})
        proj.g.query(
            "MATCH (s:Session {lme_question_id:$q}) DETACH DELETE s",
            params={"q": qid})
        # the DETACH queries recreated the (now empty) key — drop it
        with contextlib.suppress(Exception):
            proj.db.select_graph(graph_name).delete()
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


# ── #1884 regression: mini-pipeline pool:ingest invariant ──────────────────
# reval2 showed silent write loss on 12-18/50 questions (pool_size <<
# ingest.points, e.g. e47becba ingest 374 → pool 8) with ingest_retries=0,
# errors=[] — writes succeeded client-side but were absent at census time.
# Root cause: a CONCURRENT docker-lane pytest session's last-suite-standing
# _leftover_sweep → _sweep_team_strays DETACH-DELETEd + GRAPH.DELETEd the
# eval's LIVE team_default__default__{qid} graphs (the URI-path "test"
# inference in _team_sweep_allowed treated the shared dev container as a
# dedicated test DB). These tests pin the WRITE-PATH side of the invariant
# on the REAL server: the #1785 gate census interleaved with the #1806
# write-stage retry does NOT drop points (ratio stays >= 0.9 per question).
# The sweep-side regression lives in tests/test_wipe_server.py.

_MIN_POOL_RATIO = 0.9


def _expected_denominator(outcome: dict) -> int:
    ing = outcome.get("ingest") or {}
    if "points" in ing:
        return int(ing.get("turns", 0) + ing.get("chunks", 0)
                   + ing.get("points", 0))
    return int(ing.get("turns", 0) + ing.get("chunks", 0))


def test_docker_mini_pipeline_pool_ratio_per_question():
    """A 3-question mini-pipeline on the REAL FalkorDB keeps
    pool_size : (turns+chunks+points) >= 0.9 per question — the invariant
    reval2 violated (write loss surfaced as sub-0.1 ratios at census). On a
    dedicated/CI docker (no concurrent session sweeping team_*) this pins
    the write-path + gate-census interaction on the unshimmed client; on a
    SHARED dev container a concurrent session's leftover sweep can delete
    the test's own graphs mid-run (the #1884 mechanism) — that sweep-side
    regression is pinned in tests/test_wipe_server.py."""
    model = _unique_model("poolratio")
    qids = ["mini_ie_user_001", "mini_msr_002", "mini_tr_003"]
    instances = [q for q in _mini() if q["question_id"] in qids]
    try:
        outcomes, _ = runner.run_evaluation(
            instances, reader=MockReader(), judge=MockJudge(), ks=(5,),
            top_k=5, split="s", ingest_mode="deterministic",
            db_uri=DB_URI, model=model, query_prompt="query")
        assert len(outcomes) == len(qids)
        for out in outcomes:
            expected = _expected_denominator(out)
            ratio = out["pool_size"] / expected if expected else 0.0
            assert ratio >= _MIN_POOL_RATIO, (
                f"{out['question_id']}: pool {out['pool_size']} vs "
                f"expected {expected} (ratio {ratio:.3f}) — write loss")
    finally:
        for qid in qids:
            _clean_question(
                runner.question_graph_namespace(model, "query", qid), qid)


def test_docker_v2_ingest_retry_gate_pool_ratio(monkeypatch):
    """reval2's exact write path (v2 ingest + #1806 write-stage retry +
    #1785 gate census) on the REAL FalkorDB with a mocked extractor: the
    gate census interleaved between Phase A/C writes does NOT drop points —
    every question keeps pool:ingest >= 0.9 and gates green. This isolates
    the reval2 loss from the (disproven) gate×retry write-loss hypothesis:
    the loss was external (concurrent test-session sweep), not the write
    path."""
    import tortoise.extractor_v2 as ev2

    def _fake_extract(model, turns, **kw):
        pts = []
        for i, t in enumerate(turns or []):
            content = str(t.get("content") or "")
            if not content:
                continue
            pts.append({
                "id": f"pt_1884_{kw.get('session_id', 's')}_{i}",
                "content": content, "pointKind": "statement",
                "quote": "", "source_turn_id": i,
            })
        return {"payload": {"entities": [], "points": pts, "events": [],
                            "operators": [], "supersessions": []},
                "minted_kinds": [], "supersessions": [], "errors": [],
                "warnings": [],
                "stats": {"llm": {"calls": 1, "retries": 0, "truncated": 0}},
                "error_census": {}}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)
    model = _unique_model("v2pool")

    class _StubModel:
        """Stable-id adapter stub — keeps the extractor fingerprint
        deterministic (an address-bearing repr would warn)."""
        model_id = "stub-1884-v2"
        id = model_id

    qids = ["mini_ie_user_001", "mini_ku_004"]
    instances = [q for q in _mini() if q["question_id"] in qids]
    try:
        outcomes, _ = runner.run_evaluation(
            instances, reader=MockReader(), judge=MockJudge(), ks=(5,),
            top_k=5, split="s", ingest_mode="v2", extractor_model=_StubModel(),
            db_uri=DB_URI, model=model, query_prompt="query",
            ingest_write_retries=2)
        assert len(outcomes) == len(qids)
        for out in outcomes:
            expected = _expected_denominator(out)
            ratio = out["pool_size"] / expected if expected else 0.0
            assert ratio >= _MIN_POOL_RATIO, (
                f"{out['question_id']}: pool {out['pool_size']} vs "
                f"expected {expected} (ratio {ratio:.3f}) — v2 write loss")
            assert out["gate_reasons"] == [], (
                f"{out['question_id']}: gate red {out['gate_reasons']}")
            assert out.get("ingest_retries", 0) == 0
    finally:
        for qid in qids:
            _clean_question(
                runner.question_graph_namespace(model, "query", qid), qid)
