"""Smoke tests for the LongMemEval-S external comparability runner (#1144,
axis 2). Runs fully offline against the committed MINI fixture with mocked
reader + judge — no dataset download, no API keys, embedded FalkorDBLite.

The full 500-question run is @pytest.mark.slow and gated on the dataset +
provider keys (never exercised in CI).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.sdk import TortoiseSDK  # noqa: E402, I001, RUF100
from tortoise.models import OpenAICompatModel  # noqa: E402, RUF100
from tortoise.search_engine import reset_circuit_breakers

from tortoise import extractor_v2 as v2  # noqa: E402, I001, RUF100

from tools.longmem_eval.ingest import (  # noqa: E402, RUF100
    _session_chunks, _session_transcript, ingest_haystack,
)
from tools.longmem_eval.judge import (  # noqa: E402, RUF100
    LLMJudge, MockJudge, OfficialJudgeModel, _parse_judge_response,
    get_anscheck_prompt, is_abstention,
)
from tools.longmem_eval.reader import MockReader, build_reader  # noqa: E402, RUF100
from tools.longmem_eval.retrieve import (  # noqa: E402, RUF100
    _annotate_hits, _assemble_context, _dedup_pool, _estimate_tokens,
    _is_raw_chunk, hybrid_search, render_context, retrieve_for_question,
)
from tools.longmem_eval.run import (
    CheckpointStaleError, _assert_python_version, _print_summary,
    outcomes_to_report, run_evaluation, run_main,
)
from tools.longmem_eval.report import (
    compare_reports, mcnemar_exact, wilson_ci,
)

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"


def _mini() -> list[dict]:
    return json.loads(MINI.read_text(encoding="utf-8"))


def _trusted_audit() -> dict:
    """A minimal TRUSTED dataset-semantics audit for programmatic
    outcomes_to_report/build_report callers (M7 #1527, E2E-3 Precondition 2
    publication gate — build_report raises without it)."""
    from tools.longmem_eval.dataset_audit import audit_dataset
    return audit_dataset([{
        "question_id": "q-audit",
        "haystack_session_ids": ["s0"],
        "answer_session_ids": ["s0"],
        "haystack_sessions": [[
            {"role": "user", "content": "x", "has_answer": True}]],
    }])


def _fresh_sdk(tmp_path) -> TortoiseSDK:
    Path(tmp_path).mkdir(parents=True, exist_ok=True)
    return TortoiseSDK(str(tmp_path / "lme.db"))


@pytest.fixture(autouse=True)
def _reset_embedding_singleton():
    """R3 (#1542, Task 5): clear the EmbeddingModel singleton before and
    after EVERY test in this module — a real model load or a failed load's
    60s negative cache cannot leak across tests (the module stays
    order-independent in the vector-enabled eval env, where
    sentence-transformers is installed)."""
    from tortoise.embeddings import EmbeddingModel
    EmbeddingModel._reset()
    yield
    EmbeddingModel._reset()


def _require_embedder():
    """Skip when the embedder is unavailable — the R3 tests that ASSERT
    dense-leg behavior (write-time coverage == 1.0, vector-leg hits) need
    sentence-transformers + the cached all-MiniLM-L6-v2 model. The
    embedder-present variants run where CI guarantees them; skip-if-no-
    embedder keeps the module green on any env."""
    from tortoise.embeddings import EmbeddingModel
    m = EmbeddingModel.get(load_timeout=120)
    if m is None:
        pytest.skip("sentence-transformers / all-MiniLM-L6-v2 cache "
                    "not available — dense-leg assertion skipped")
    return m


def _no_embedder(monkeypatch) -> None:
    """Pin the sparse-leg path: EmbeddingModel.get → None (Task 5). Uses
    monkeypatch so the classmethod is restored after the test (the autouse
    fixture only clears singleton state, not the class attribute)."""
    from tortoise.embeddings import EmbeddingModel
    monkeypatch.setattr(EmbeddingModel, "get",
                        staticmethod(lambda load_timeout=None: None))


# ── Pipeline end-to-end (mocked reader + judge, embedded DB) ───────────────

def test_mini_pipeline_end_to_end_mock(tmp_path, monkeypatch):
    """Env-hermeticity (R6 #1545): a leaked TORTOISE_LME_RERANK must never
    flip this baseline regression into a rerank-on run (the R6 gate default
    is fail-safe OFF, but the guard pins it)."""
    monkeypatch.delenv("TORTOISE_LME_RERANK", raising=False)
    instances = _mini()
    assert len(instances) == 5
    outcomes, report = run_evaluation(
        instances, reader=MockReader(), judge=MockJudge(),
        ks=(5, 10, 20), top_k=20, split="s", work_dir=str(tmp_path),
    )
    assert len(outcomes) == 5
    assert report["n_questions"] == 5
    assert report["split"] == "s"

    acc = report["accuracy"]
    # IE + MSR + KU evidence is retrievable + answer-bearing → all three pass
    # (TR is date-derived and AB is evidence-less; both honestly score 0 in
    # the mock mode).
    assert acc["overall"] >= 0.4
    assert acc["per_category"]["Information Extraction"]["accuracy"] == 1.0
    assert acc["per_category"]["Multi-Session Reasoning"]["accuracy"] == 1.0
    assert acc["per_category"]["Knowledge Updates"]["accuracy"] == 1.0
    assert acc["per_category"]["Abstention"]["n"] == 1
    assert acc["abstention"] == 0.0  # mock reader cannot abstain — honest

    # Retrieval actually delivered the evidence sessions.
    ret = report["retrieval"]
    assert ret["session_recall@k"]["5"] >= 0.6
    assert ret["session_recall@k"]["10"] >= 0.6
    assert ret["context_tokens_mean"] > 0
    assert ret["context_point_count_mean"] > 0

    # Full methodology provenance (design-locked axis 2).
    m = report["methodology"]
    assert m["reader_model"] == "mock-reader"
    assert m["judge_model"] == "mock-judge"
    assert m["extraction_approach"]
    assert m["git_sha"]
    assert m["run_at_utc"]
    assert m["k_values"] == [5, 10, 20]

    # M4 (#1524, D5): the integrity block — deterministic mode → valid,
    # zero invalid_rate, empty census (the E2E-2 offline analog).
    integ = report["integrity"]
    assert integ["valid"] is True
    assert integ["invalid_rate"] == 0.0
    assert integ["n_valid"] == 5
    assert integ["n_invalid"] == 0
    assert integ["n_failed"] == 0
    assert integ["error_census"] == {}


def test_outcomes_to_report_golden_shape():
    """Golden report-shape pin (M1/S22, issue #1522 + M7 #1527 contract
    extension): outcomes_to_report returns a dict with the full published key
    set. Regression guard — commit 4acb47d4 absorbed the build_report(...)
    return into reader_prompt_source as dead code, making outcomes_to_report
    (...) implicitly return None; the restore (2f7c3df8) is pinned here so the
    report contract (E2E-2: report is a real dict) cannot silently regress.

    M7 #1527 (Gate 4 — intentional contract change): the pinned key set now
    includes the self-explanatory-report keys (integrity / leg_mix /
    pool_size / evidence / latency_ms.ingest) and the Layer-1 outcome
    projection gains valid / error_classes / leg_mix / leg_mix@k / pool_size /
    evidence_written / evidence_retrieved@k / ingest_latency_ms."""
    outcomes = [{
        "question_id": "q-golden-1",
        "question_type": "single-session-user",
        "question_date": "2024-01-15",
        "label": True,
        "hypothesis": "golden hypothesis",
        "session_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
        "turn_recall@k": {"5": 0.5, "10": 0.5, "20": 0.5},
        "evidence_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
        "chunk_evidence_recall@k": {"5": 0.5, "10": 0.5, "20": 0.5},
        "n_ingest_errors": 0,
        "context_tokens": 120,
        "context_point_count": 3,
        "retrieval_latency_ms": 11.0,
        "reader_latency_ms": 22.0,
        "judge_latency_ms": 33.0,
        "total_ms": 66.0,
        # M7 outcome instrumentation (persisted in the Layer-1 projection).
        "valid": True,
        "error_classes": {},
        "leg_mix": {"tfidf": 3},
        "leg_mix@k": {"5": {"tfidf": 3}, "10": {"tfidf": 3},
                       "20": {"tfidf": 3}},
        "pool_size": 10,
        "evidence_written": 2,
        "evidence_retrieved@k": {"5": 1, "10": 2, "20": 2},
        "ingest_latency_ms": 12.5,
    }]
    report = outcomes_to_report(
        outcomes,
        reader_model="golden-reader",
        judge_model="golden-judge",
        ks=(5, 10, 20),
        top_k=20,
        split="s",
        r1_knobs={"chunk_turns": 2, "context_token_cap": 8000,
                  "max_chunks_per_session": 2},
        # M7 publication gate + run-hygiene provenance.
        dataset_semantics_audit=_trusted_audit(),
        integrity_threshold=0.0,
        python_version="3.12.0",
        workers=1,
        dataset_fingerprint="deadbeefcafe1234",
    )
    # The regression made this None — a real dict is the whole point (E2E-2).
    assert isinstance(report, dict)
    # Top-level key set is the published report contract (M7 adds the
    # self-explanatory-report keys).
    assert set(report) == {
        "benchmark", "dataset", "split", "n_questions", "accuracy",
        "retrieval", "latency_ms", "methodology", "failures", "n_failed",
        "outcomes", "integrity", "leg_mix", "pool_size", "evidence",
    }
    assert report["benchmark"] == "LongMemEval"
    assert report["dataset"] == "xiaowu0162/longmemeval-cleaned"
    assert report["split"] == "s"
    assert report["n_questions"] == 1

    acc = report["accuracy"]
    assert acc["overall"] == 1.0
    # M8 (#1528, D3): the additive 95% Wilson CI rides beside every published
    # accuracy (overall / abstention / per_category). n=1,k=1 -> (0.207, 1.0)
    # — the standard Wilson interval, no continuity correction (the plan's
    # draft claimed [1.0, 1.0]; the pinned formula yields 0.207 lower bound).
    assert acc["ci95"] == [0.207, 1.0]
    assert acc["task_averaged"] == 1.0
    assert acc["abstention_ci95"] == [0.0, 0.0]  # no _abs outcomes -> (0,0)
    assert acc["per_category"]["Information Extraction"] == {
        "accuracy": 1.0, "n": 1, "ci95": [0.207, 1.0]}
    assert isinstance(acc["per_category"]["Information Extraction"]["ci95"],
                      list)
    assert len(acc["per_category"]["Information Extraction"]["ci95"]) == 2
    assert acc["per_type"]["single-session-user"] == {"accuracy": 1.0, "n": 1}

    ret = report["retrieval"]
    assert ret["session_recall@k"] == {"5": 1.0, "10": 1.0, "20": 1.0}
    assert ret["turn_recall@k"] == {"5": 0.5, "10": 0.5, "20": 0.5}
    assert ret["evidence_recall@k"] == {"5": 1.0, "10": 1.0, "20": 1.0}
    assert ret["chunk_evidence_recall@k"] == {"5": 0.5, "10": 0.5, "20": 0.5}
    assert ret["chunk_evidence_recall_n@k"] == {"5": 1, "10": 1, "20": 1}
    # M7 paper-aligned aggregates (non-_abs — the golden outcome is non-_abs).
    assert ret["session_recall_paper@k"] == {"5": 1.0, "10": 1.0, "20": 1.0}
    assert ret["turn_recall_paper@k"] == {"5": 0.5, "10": 0.5, "20": 0.5}
    assert ret["context_tokens_mean"] == 120.0
    assert ret["context_point_count_mean"] == 3.0

    lat = report["latency_ms"]
    assert lat["retrieval"]["mean_ms"] == 11.0
    assert lat["reader"]["mean_ms"] == 22.0
    assert lat["judge"]["mean_ms"] == 33.0
    assert lat["ingest"]["mean_ms"] == 12.5  # M7 D5: isolated write-path cost
    assert lat["total_per_question"]["mean_ms"] == 66.0

    # M7 D1: integrity block — one completed, zero invalid → valid.
    integ = report["integrity"]
    assert integ["valid"] is True
    assert integ["threshold"] == 0.0
    assert integ["n_attempted"] == 1
    assert integ["n_valid"] == 1
    assert integ["n_invalid"] == 0
    assert integ["invalid_rate"] == 0.0
    assert integ["error_census"] == {}
    assert len(integ["checks"]) == 5

    # M7 D2/D3/D4 aggregates.
    assert report["leg_mix"]["total_counts"] == {"tfidf": 3}
    assert report["leg_mix"]["mean_share"] == {"tfidf": 1.0}
    assert report["leg_mix"]["unknown_count"] == 0
    assert report["leg_mix"]["n_questions"] == 1
    assert report["pool_size"] == {"mean": 10.0, "p50": 10.0, "p95": 10.0}
    assert report["evidence"]["written_mean"] == 2.0
    assert report["evidence"]["retrieved_mean@k"]["20"] == 2.0
    assert report["evidence"]["evidence_bearing_n"] == 1
    assert report["evidence"]["evidence_absent_n"] == 0
    assert report["evidence"]["vacuity_rate"] == 0.0

    m = report["methodology"]
    assert m["reader_model"] == "golden-reader"
    assert m["judge_model"] == "golden-judge"
    assert m["k_values"] == [5, 10, 20]
    assert m["top_k_context"] == 20
    # R1 (#1540) D7: knob provenance in the methodology
    assert m["chunk_turns"] == 2
    assert m["context_token_cap"] == 8000
    assert m["max_chunks_per_session"] == 2
    # #1414 parity hashes — produced by reader_prompt_source/JUDGE_RUBRIC_ID.
    assert m["reader_prompt_hash"]
    assert m["judge_rubric_id_hash"]
    # M7 env/audit provenance.
    assert m["python_version"] == "3.12.0"
    assert m["workers"] == 1
    assert m["dataset_fingerprint"] == "deadbeefcafe1234"
    assert m["integrity_threshold"] == 0.0
    assert m["dataset_semantics_audit"]["verdict"] == \
        "trusted-as-documented-variant"

    # Layer-1 payload projection (surface 22) is carried under extra.
    assert len(report["outcomes"]) == 1
    assert report["outcomes"][0] == {
        "question_id": "q-golden-1",
        "question_type": "single-session-user",
        "question_date": "2024-01-15",
        "label": True,
        "hypothesis": "golden hypothesis",
        "session_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
        "turn_recall@k": {"5": 0.5, "10": 0.5, "20": 0.5},
        "evidence_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
        "chunk_evidence_recall@k": {"5": 0.5, "10": 0.5, "20": 0.5},
        "n_ingest_errors": 0,
        "context_tokens": 120,
        # M8 (#1528, D6): the projection now carries the live graph point
        # count (was present per-outcome but stripped) — the compare
        # flip-list zero-point flag consumes it.
        "context_point_count": 3,
        "retrieval_latency_ms": 11.0,
        "reader_latency_ms": 22.0,
        "judge_latency_ms": 33.0,
        "total_ms": 66.0,
        "valid": True,
        "error_classes": {},
        "leg_mix": {"tfidf": 3},
        "leg_mix@k": {"5": {"tfidf": 3}, "10": {"tfidf": 3},
                       "20": {"tfidf": 3}},
        "pool_size": 10,
        "evidence_written": 2,
        "evidence_retrieved@k": {"5": 1, "10": 2, "20": 2},
        "ingest_latency_ms": 12.5,
        # R3 (#1542) D3/D4: the projected outcome carries the dense-leg
        # keys with defaults (None / []) — pre-R3 checkpoints render
        # (flagged as lacking data) instead of KeyError.
        "points_total": None,
        "points_embedded": None,
        "embedding_coverage": None,
        "legs": [],
        # R5 (#1544): the TR-constraint surface with defaults (None/False)
        # — pre-R5 checkpoints render instead of KeyError.
        "tr_constraint": None,
        "tr_window_fallback": False,
    }
    assert report["failures"] == []
    assert report["n_failed"] == 0


def test_cli_smoke(tmp_path):
    """CLI entry (python -m tools.longmem_eval.run) with mini + mock."""
    out = tmp_path / "report.json"
    report = run_main([
        "--data", str(MINI), "--limit", "5", "--split", "s",
        "--mock", "--output", str(out),
    ])
    assert out.is_file()
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert saved["n_questions"] == 5
    assert saved["accuracy"]["overall"] == report["accuracy"]["overall"]


# ── Ingestion structure ────────────────────────────────────────────────────

def test_ingestion_creates_session_turn_raw_structure(tmp_path):
    """R1 (#1540): the whole-session :raw blob is replaced by turn-granular
    raw chunks — ``lme:{qid}:s{si}:c{ci}`` windows of ``chunk_turns`` turns
    (union of chunks == the full session)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = _mini()[0]  # mini_ie_user_001: 2 sessions × 3 turns
        stats = ingest_haystack(sdk, q, chunk_turns=2)
        assert stats["sessions"] == 2
        assert stats["turns"] == 6
        assert stats["evidence_turns"] == 1
        # 2 sessions × windows [t0,t1]+[t2] at chunk_turns=2
        assert stats["chunks"] == 4
        # evidence_points counts marked extracted (non-chunk) points only
        # (D5): the deterministic leg's chunks stay UNMARKED (D3).
        assert stats["evidence_points"] == 1  # the single evidence turn

        proj = sdk._get_proj()
        rows = proj.g.query("MATCH (s:Session) RETURN count(s)").result_set
        assert rows[0][0] == 2
        rows = proj.g.query(
            "MATCH (p:Point {pointKind:'event'}) RETURN count(p)").result_set
        assert rows[0][0] == 6
        rows = proj.g.query(
            "MATCH (p:Point {pointKind:'session-transcript'}) RETURN count(p)"
        ).result_set
        assert rows[0][0] == 4
        # the :raw id is retired — no point id ends with :raw
        rows = proj.g.query(
            "MATCH (p:Point) WHERE p.id CONTAINS ':raw' RETURN count(p)"
        ).result_set
        assert rows[0][0] == 0
        # evidence turn carries has_answer=true (turn-level recall source);
        # deterministic chunks stay UNMARKED (D3) — 1 marked point total
        rows = proj.g.query(
            "MATCH (p:Point {has_answer:true}) RETURN count(p)").result_set
        assert rows[0][0] == 1
        # deterministic ids, session linkage: 3 turns + 2 chunks
        rows = proj.g.query(
            "MATCH (s:Session {id:'lme:mini_ie_user_001:s1'})-[:CONTAINS]->"
            "(t:Point) RETURN count(t)").result_set
        assert rows[0][0] == 5
        # chunk props: lme_chunk_index ∈ {0,1}, lme_chunk_turns = the ACTUAL
        # window lengths ([2, 1] — the remainder window carries 1)
        props = {}
        for ci in (0, 1):
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN p.lme_chunk_index, "
                "p.lme_chunk_turns, coalesce(p.has_answer, false)",
                params={"id": f"lme:mini_ie_user_001:s1:c{ci}"}).result_set
            assert rows, f"chunk c{ci} missing"
            idx, turns, marked = rows[0]
            assert idx == ci
            props[idx] = turns
            assert marked is False  # D3: deterministic chunks unmarked
        assert props == {0: 2, 1: 1}
        # the evidence turn (s1 t2) is contained in a chunk (c1)
        rows = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN p.content",
            params={"id": "lme:mini_ie_user_001:s1:c1"}).result_set
        assert "My favorite board game is Catan" in rows[0][0]
    finally:
        sdk.close()


def test_ingestion_idempotent(tmp_path):
    """Re-ingest over the same fresh graph is a no-op: no double-write, and
    ``stats["chunks"]`` reports chunks WRITTEN this run (post-guard, V1
    #1540) — 0 on re-ingest, matching the graph's 0 new chunks (a fresh
    single ingest reports the full graph state)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = _mini()[1]  # multi-session question: 2 sessions × 3 turns
        stats1 = ingest_haystack(sdk, q, chunk_turns=2)
        assert stats1["chunks"] == 4  # fresh ingest == graph state
        stats2 = ingest_haystack(sdk, q, chunk_turns=2)
        assert stats2["chunks"] == 0  # nothing written on re-ingest
        proj = sdk._get_proj()
        rows = proj.g.query("MATCH (p:Point) RETURN count(p)").result_set
        # 2 sessions × (3 turns + 2 chunks)
        assert rows[0][0] == 10
    finally:
        sdk.close()


def test_ingestion_chunk_window_boundaries(tmp_path):
    """BVA on window boundaries (R1 #1540): 1-turn, exact-multiple,
    remainder-window and empty sessions."""
    sdk = _fresh_sdk(tmp_path)
    try:
        # 1-turn session at chunk_turns=2 → 1 chunk
        q1 = {"question_id": "bva1", "haystack_sessions": [
            [{"role": "user", "content": "a", "has_answer": True}]]}
        assert ingest_haystack(sdk, q1, chunk_turns=2)["chunks"] == 1
        # 2-turn session at chunk_turns=2 → 1 chunk (exact multiple)
        q2 = {"question_id": "bva2", "haystack_sessions": [
            [{"role": "user", "content": "a"},
             {"role": "user", "content": "b"}]]}
        assert ingest_haystack(sdk, q2, chunk_turns=2)["chunks"] == 1
        # 5-turn session → 3 chunks: [0,1], [2,3], [4]
        q3 = {"question_id": "bva3", "haystack_sessions": [
            [{"role": "user", "content": f"t{i}"} for i in range(5)]]}
        assert ingest_haystack(sdk, q3, chunk_turns=2)["chunks"] == 3
        # empty session → 0 chunks, no exception
        q4 = {"question_id": "bva4", "haystack_sessions": [[]]}
        assert ingest_haystack(sdk, q4, chunk_turns=2)["chunks"] == 0
    finally:
        sdk.close()


# ── R5 (#1544): session dates on points + dated timeline Events ────────────

def test_ingest_writes_dated_session_events(tmp_path):
    """R5 D3: one dated ``lmeHaystackSession`` :Event per dated haystack
    session — ``startedAt == haystack_dates[si]``, ``sessionId == dataset
    sid`` (session_recall@k keys on h["session_id"]), ``lme_session_index``
    for the date annotation, and an ``aboutSession`` edge to the Session
    node. mini_ie_user_001 has 2 dated sessions (2025-06-10 / 2025-06-14)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_ie_user_001")
        ingest_haystack(sdk, q)
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (e:Event {eventKind:'lmeHaystackSession'}) "
            "RETURN e.eventId, e.startedAt, e.sessionId, e.lme_session_index "
            "ORDER BY e.lme_session_index").result_set
        assert [(r[1], r[2], r[3]) for r in rows] == [
            ("2025-06-10", "mini-s0", 0), ("2025-06-14", "mini-s1", 1)]
        # aboutSession edges exist (event → Session)
        n = proj.g.query(
            "MATCH (e:Event {eventKind:'lmeHaystackSession'})"
            "-[:aboutSession]->(s:Session) "
            "RETURN count(*)").result_set[0][0]
        assert n == 2
        # endedAt mirrors startedAt (the dated timeline surface)
        rows = proj.g.query(
            "MATCH (e:Event {eventKind:'lmeHaystackSession'}) "
            "RETURN e.startedAt, e.endedAt").result_set
        assert all(s == e for s, e in rows)
    finally:
        sdk.close()


def test_ingest_undated_session_gets_no_timeline_event(tmp_path):
    """R5 D3 negative: an undated session (empty haystack_dates entry)
    produces NO timeline event — honest absence (a dated event with a fake
    date would be a false date-answer vector). Points still get the explicit
    sentinel createdAt (deterministic-oldest → recency 0.0, never the
    server default now)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = {"question_id": "undated", "haystack_dates": [""],
             "haystack_session_ids": ["s-ud"],
             "haystack_sessions": [[{"role": "user", "content": "hi"}]]}
        ingest_haystack(sdk, q)
        proj = sdk._get_proj()
        n = proj.g.query(
            "MATCH (e:Event {eventKind:'lmeHaystackSession'}) RETURN count(*)"
        ).result_set[0][0]
        assert n == 0
        rows = proj.g.query(
            "MATCH (p:Point) RETURN p.createdAt").result_set
        assert all(r[0] == "1970-01-01T00:00:00Z" for r in rows)
    finally:
        sdk.close()


def test_ingest_points_carry_session_date_as_created_at(tmp_path):
    """R5 D3: turn + raw-chunk points carry the session date as their
    creation-time prop (``createdAt`` — the stored key the engine's
    recency re-rank queries). mini_tr_003 has one dated session
    (2025-06-10)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_tr_003")
        ingest_haystack(sdk, q)
        rows = sdk._get_proj().g.query(
            "MATCH (p:Point) WHERE p.lme_question_id = 'mini_tr_003' "
            "RETURN p.createdAt, p.lme_session_index").result_set
        assert all(r[0] == "2025-06-10" for r in rows)  # single dated session
    finally:
        sdk.close()


# ── R5 (#1544): TR union pool (point + event) + event annotation ───────────

def test_hybrid_search_tr_union_includes_events(tmp_path):
    """R5 D4: ``hybrid_search(entity_types=("point", "event"))`` merges
    event hits into the pool — each hit carries ``scores.rrf`` (comparable
    across the two calls — same k constant, same leg structure) and the
    merge is deterministic (RRF desc, id tiebreak). The retrievable event
    here is created inline with a ``subject`` (the engine fetches
    ``n.subject`` as event content) — the deterministic timeline events
    (name-only) prove the graph surface, not retrieval."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_tr_003")
        ingest_haystack(sdk, q)
        sdk.create_event("adopted dog", "core:occurrence",
                         subject="Ava adopted a dog last week",
                         sessionId="mini-s0", lme_question_id="mini_tr_003",
                         lme_session_index=0, startedAt="2025-06-10",
                         is_episodic=True)
        hits = hybrid_search(sdk, q["question"], limit=20,
                             entity_types=("point", "event"),
                             recency_fields={"point": "createdAt",
                                             "event": "startedAt"},
                             recency_boost=0.5)
        # the event hit surfaced with a comparable rrf score
        ev_hits = [h for h in hits
                   if h.get("point_kind") == "core:occurrence"]
        assert ev_hits
        for h in hits:
            assert (h.get("scores") or {}).get("rrf") is not None
        # deterministic merge: rrf desc, then id
        rrf = [((h.get("scores") or {}).get("rrf") or 0.0, h["id"])
               for h in hits]
        assert rrf == sorted(rrf, key=lambda t: (-t[0], t[1]))
    finally:
        sdk.close()


def test_hybrid_search_default_stays_points_only(tmp_path):
    """R5 D4 regression: the default ``entity_types=("point",)`` path is
    unchanged — an event in the graph does NOT join the pool unless the
    caller opts in (baseline isolation, M8)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_tr_003")
        ingest_haystack(sdk, q)
        sdk.create_event("adopted dog", "core:occurrence",
                         subject="Ava adopted a dog last week",
                         sessionId="mini-s0", lme_question_id="mini_tr_003",
                         lme_session_index=0, startedAt="2025-06-10",
                         is_episodic=True)
        hits = hybrid_search(sdk, q["question"], limit=20)
        kinds = {h.get("point_kind") for h in hits}
        assert kinds and "core:occurrence" not in kinds
    finally:
        sdk.close()


def test_annotate_hits_resolves_event_session_linkage(tmp_path):
    """R5 D8: ``event_props_for_hits`` resolves an event id to its session
    linkage (sessionId → session_id, lme_session_index) and ``_annotate_hits``
    maps it to the dataset's haystack_dates — the same session_date
    annotation path as points. ``has_answer`` stays False (evidence marking
    is point-level)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_tr_003")
        ingest_haystack(sdk, q)
        from tools.longmem_eval.ingest import event_props_for_hits
        from tools.longmem_eval.retrieve import _annotate_hits
        proj = sdk._get_proj()
        ev = proj.g.query(
            "MATCH (e:Event {eventKind:'lmeHaystackSession'}) "
            "RETURN e.eventId").result_set
        eid = ev[0][0]
        props = event_props_for_hits(proj, [eid])
        assert props[eid]["lme_session_index"] == 0
        assert props[eid]["session_id"] == "mini-s0"
        assert props[eid]["has_answer"] is False
        ann = _annotate_hits([{"id": eid, "content": "x",
                               "match_source": "rrf"}],
                             props, q["haystack_dates"])
        assert ann[0]["session_date"] == "2025-06-10"
    finally:
        sdk.close()


# ── R5 (#1544): TR wiring — union pool, window filter, time-ascending ──────

def _tr_question(two_sessions: bool = True) -> dict:
    """A TR-shaped question with one/two dated sessions (the mini fixture's
    TR question has a single session; the ordering/window tests need two)."""
    base = {
        "question_id": "mini_tr_tmp",
        "question_type": "temporal-reasoning",
        "question": "How many days ago did Ava tell you she adopted a dog?",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["s0", "s1"],
        "haystack_dates": ["2025-06-10", "2025-06-14"],
        "haystack_sessions": [
            [{"role": "user", "content": "I adopted a dog last week.",
              "has_answer": True}],
            [{"role": "user", "content": "I got a new job."}],
        ],
        "answer_session_ids": ["s0"],
    }
    if not two_sessions:
        base["haystack_session_ids"] = ["s0"]
        base["haystack_dates"] = ["2025-06-10"]
        base["haystack_sessions"] = base["haystack_sessions"][:1]
    return base


def test_tr_question_uses_tr_top_k_and_events_pool(tmp_path):
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_tr_003")
        ingest_haystack(sdk, q)
        # a retrievable event (subject content matching the question) —
        # proves the TR union pool carries event hits (E2E-4's "no
        # point-only filter")
        sdk.create_event("adopted dog", "core:occurrence",
                         subject="Ava adopted a dog last week",
                         sessionId="mini-s0", lme_question_id="mini_tr_003",
                         lme_session_index=0, startedAt="2025-06-10",
                         is_episodic=True)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    tr_top_k=12, tr_date_weight=0.5)
        assert ret["context_point_count"] <= 12          # TR cap (20→12)
        # union pool engaged: the event hit joined the pool
        assert any(h.get("point_kind") == "core:occurrence"
                   for h in ret["hits"])
        assert ret["context_tokens"] > 0
        # TR questions surface the detected constraint kind
        assert ret["tr_constraint"] == "ordering"
        assert ret["tr_window_fallback"] is False
    finally:
        sdk.close()


def test_tr_context_renders_time_ascending(tmp_path):
    sdk = _fresh_sdk(tmp_path)
    try:
        q = _tr_question(two_sessions=True)
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20,
                                    tr_top_k=12)
        # the READER's context is time-ascending (D6) — dated hits sorted
        # by session_date, undated last; the pool (ret["hits"]) keeps
        # retrieval order (recall measures retrieval, not rendering).
        ctx_dates = [h["session_date"] for h in ret["context_points"]
                     if h["session_date"]]
        assert ctx_dates == sorted(ctx_dates)
        assert ctx_dates  # both dated sessions present in context
    finally:
        sdk.close()


def test_non_tr_question_path_unchanged(tmp_path, monkeypatch):
    """R5 regression: a non-TR question ignores every TR knob — points-only
    pool, top_k 20 (not TR-capped), RRF order, no constraint surface."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_ie_user_001")
        _no_embedder(monkeypatch)
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    tr_top_k=12, tr_date_weight=0.5)
        assert ret["tr_constraint"] is None
        assert ret["tr_window_fallback"] is False
        assert ret["context_point_count"] <= 20  # not TR-capped
        # points-only pool: no event kind anywhere in the hits
        kinds = {h.get("point_kind") for h in ret["hits"]}
        assert "lmeHaystackSession" not in kinds
    finally:
        sdk.close()


def test_tr_window_filter_excludes_out_of_window_session(tmp_path):
    """R5 D5: a numeric recency bound hard-filters the pool to the
    in-window sessions BEFORE truncation — session_recall@k measures the
    in-window pool. s1 (2025-06-01) is outside [2025-06-05, 2025-06-15]."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = _tr_question(two_sessions=True)
        q["question"] = "What did Ava do 10 days ago?"
        q["haystack_dates"] = ["2025-06-10", "2025-06-01"]
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20, tr_top_k=12)
        assert ret["tr_constraint"] == "recency"
        assert ret["tr_window_fallback"] is False
        pool_dates = {h["session_date"] for h in ret["hits"]
                      if h["session_date"]}
        assert pool_dates == {"2025-06-10"}  # only the in-window session
    finally:
        sdk.close()


def test_tr_window_filter_falls_back_when_starved(tmp_path):
    """R5 D5 defensive rule: when the filter would empty the dated pool
    (no in-window hits), the unfiltered pool is kept — the reader is never
    starved into abstention — recorded as ``tr_window_fallback: true``."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = _tr_question(two_sessions=True)
        q["question"] = "What did Ava do 3 days ago?"
        q["haystack_dates"] = ["2025-06-10", "2025-06-01"]
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20, tr_top_k=12)
        assert ret["tr_constraint"] == "recency"
        assert ret["tr_window_fallback"] is True
        assert len(ret["hits"]) > 0  # unfiltered pool retained
    finally:
        sdk.close()


def test_session_chunks_union_equals_full_transcript(tmp_path):
    """Owner invariant (R1 #1540): the union of a session's chunk contents
    == the full verbatim session transcript — extraction never replaces
    verbatim evidence."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_ie_user_001")
        ingest_haystack(sdk, q, chunk_turns=2)
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (s:Session {id:$id})-[:CONTAINS]->"
            "(p:Point {pointKind:'session-transcript'}) RETURN p.id ORDER BY p.id",
            params={"id": "lme:mini_ie_user_001:s1"}).result_set
        ids = sorted(r[0] for r in rows)
        assert ids == ["lme:mini_ie_user_001:s1:c0", "lme:mini_ie_user_001:s1:c1"]
        texts = []
        for pid in ids:
            r = proj.g.query("MATCH (p:Point {id:$id}) RETURN p.content",
                             params={"id": pid}).result_set
            texts.append(r[0][0])
        session = q["haystack_sessions"][1]
        assert "\n".join(texts) == _session_transcript(session)
        # the chunker itself is boundary-honest too
        windows = _session_chunks(session, 2)
        assert "\n".join(t for _, t, _ in windows) == _session_transcript(session)
    finally:
        sdk.close()


def test_chunk_turns_validation():
    """chunk_turns ∈ {0, -1} raises ValueError (a 0/negative value would
    silently delete the verbatim leg — the owner invariant)."""
    session = [{"role": "user", "content": "a"}]
    with pytest.raises(ValueError, match="chunk_turns"):
        _session_chunks(session, 0)
    with pytest.raises(ValueError, match="chunk_turns"):
        _session_chunks(session, -1)


# ── Retrieval recall ───────────────────────────────────────────────────────

def test_retrieval_recalls_evidence_session(tmp_path, monkeypatch):
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_ie_user_001")
        # R3 (#1542) Task 5: pinned to the sparse leg — this test asserts
        # the TF-IDF path deterministically regardless of the dev env's
        # embedder state (a vector reorder could drive recall@5 to 0.0, not
        # a floor; the vector leg owns its own tests).
        _no_embedder(monkeypatch)
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20)
        # Embedded mode degrades to TF-IDF; the evidence session's terms
        # overlap the question → both recalls hit 1.0 at k=5.
        assert ret["session_recall@k"]["5"] == 1.0
        assert ret["turn_recall@k"]["5"] == 1.0
        assert ret["context_tokens"] > 0
        assert 0 < ret["context_point_count"] <= 20  # capped at top_k
        assert ret["retrieval_latency_ms"] > 0
        # every annotated hit carries its session linkage
        assert all("session_id" in h for h in ret["hits"])
    finally:
        sdk.close()


def test_retrieval_multi_session_evidence(tmp_path, monkeypatch):
    # #1595: the shared module-level FTS circuit breaker (left OPEN by an
    # earlier test's failed queries under parallel load — #1568 class) can
    # short-circuit retrieval to empty/partial before this test runs. Reset
    # the breaker and RETRY the retrieval with backoff (a pure read): under a
    # loaded matrix runner the first call can degrade inside the strategies'
    # collective cap while the FTS index is synchronous — a re-query is a
    # legitimate read, never a re-do. Bounded: 3 attempts, ~1.5s worst-case
    # added.
    import time

    reset_circuit_breakers()
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_msr_002")
        # R3 (#1542) Task 5: pinned to the sparse leg (see
        # test_retrieval_recalls_evidence_session).
        _no_embedder(monkeypatch)
        ingest_haystack(sdk, q)
        ret = None
        for attempt in range(3):
            reset_circuit_breakers()
            # #1608: this question's evidence turns rank at 0-indexed pos 4
            # and 8 under sparse-only TF-IDF — k=5 is a marginal boundary
            # (0.5 locally, deterministically 0.0 under CI load — the flake),
            # k=10 is stable (1.0). Assert the real claim — BOTH evidence
            # turns recovered by top-10 — and retry until it holds.
            ret = retrieve_for_question(sdk, q, ks=(10,), top_k=20)
            if ret["session_recall@k"]["10"] == 1.0 and ret["turn_recall@k"]["10"] >= 1.0:
                break
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
        # both evidence sessions recovered (session-level recall exact) and
        # both evidence turns recovered by top-10 (turn-level exact). k=5 is
        # intentionally NOT asserted — the second turn sits at pos 8, so a
        # k=5 floor is a ranking-boundary race under load, not a real claim.
        assert ret["session_recall@k"]["10"] == 1.0  # both evidence sessions
        assert ret["turn_recall@k"]["10"] == 1.0  # both evidence turns (#1608)
    finally:
        sdk.close()


# ── Official judge prompts (verbatim from LongMemEval evaluate_qa.py) ─────

def test_official_anscheck_prompts():
    p = get_anscheck_prompt("temporal-reasoning", "q", "18 days", "19 days")
    assert "off-by-one" in p and "days" in p
    p2 = get_anscheck_prompt("knowledge-update", "q", "a", "h")
    assert "updated answer" in p2
    p3 = get_anscheck_prompt("single-session-preference", "q", "rubric", "h")
    assert "rubric" in p3
    p4 = get_anscheck_prompt("single-session-user", "q", "a", "h",
                             abstention=True)
    assert "unanswerable" in p4
    p5 = get_anscheck_prompt("multi-session", "q", "a", "h")
    assert "contains the correct answer" in p5
    with pytest.raises(ValueError):
        get_anscheck_prompt("bogus-type", "q", "a", "h")
    assert _parse_judge_response("Yes.") is True
    assert _parse_judge_response("No, the response is wrong") is False
    assert is_abstention("q_abs") and not is_abstention("q1")


def test_mock_judge_semantics():
    j = MockJudge()
    assert j.judge(question_type="single-session-user", question="q",
                   answer="Catan", hypothesis="My favorite is Catan.",
                   abstention=False)
    assert not j.judge(question_type="single-session-user", question="q",
                       answer="Catan", hypothesis="My favorite is Monopoly.",
                       abstention=False)
    # abstention: correct iff the response flags unanswerability
    assert j.judge(question_type="single-session-user", question="q",
                   answer="The user never mentioned a favorite color.",
                   hypothesis="I do not know; the history does not mention it.",
                   abstention=True)
    assert not j.judge(question_type="single-session-user", question="q",
                       answer="The user never mentioned a favorite color.",
                       hypothesis="Her favorite color is red.", abstention=True)
    assert not j.judge(question_type="single-session-user", question="q",
                       answer="Catan", hypothesis="", abstention=False)


def test_mock_reader_returns_evidence():
    r = MockReader()
    hits = [
        {"id": "x", "content": "[user] filler", "has_answer": False},
        {"id": "y", "content": "[user] Tokyo in November it is.",
         "has_answer": True},
        {"id": "z", "content": "[user] The city is Tokyo.", "has_answer": True},
    ]
    assert r.answer(context_hits=hits, question="q") == (
        "[user] Tokyo in November it is. [user] The city is Tokyo.")
    assert r.answer(context_hits=[{"id": "a", "content": "  ", "has_answer": False}],
                    question="q") == ""
    assert r.answer(context_hits=[], question="q") == ""


# ── Official API call shapes (P1: judge/reader must match official protocol) ─


def test_official_judge_call_shape():
    """The judge's request MUST match official evaluate_qa.py verbatim:
    messages=[user], n=1, temperature=0, max_tokens=10 — NO response_format
    (JSON mode), NO system message."""
    m = OfficialJudgeModel(
        id="gpt-4o-2024-08-06", base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY")
    req = m.build_request("user prompt")
    assert req["model"] == "gpt-4o-2024-08-06"
    assert req["messages"] == [{"role": "user", "content": "user prompt"}]
    assert req["n"] == 1
    assert req["temperature"] == 0
    assert req["max_tokens"] == 10
    assert "response_format" not in req  # no JSON mode


def test_llm_judge_sends_only_the_anscheck_prompt():
    """LLMJudge must NOT prepend an empty system message — the anscheck
    prompt is the single user message (official call shape)."""
    calls: list[str] = []

    class _RecordingModel:
        def complete(self, *, user: str) -> str:
            calls.append(user)
            return "Yes, the response is correct."

    j = LLMJudge(_RecordingModel(), model_id="gpt-4o")
    assert j.judge(question_type="single-session-user", question="q",
                   answer="a", hypothesis="h", abstention=False) is True
    assert len(calls) == 1
    assert calls[0].startswith("I will give you a question, a correct answer")
    assert "\n\nQuestion: q\n\nCorrect Answer: a\n\nModel Response: h" in calls[0]


def test_openai_compat_model_overridable_call_params():
    """response_format/max_tokens are overridable on OpenAICompatModel;
    the default stays json_object/no-max_tokens so other extraction callers
    are unaffected (PR #1355 P1)."""
    # defaults unchanged → legacy JSON mode, no max_tokens
    m = OpenAICompatModel(id="deepseek-chat",
                          base_url="https://api.example.com/v1")
    req = m.build_request("SYS", "USER")
    assert req["response_format"] == {"type": "json_object"}
    assert "max_tokens" not in req
    # explicit overrides (the LongMemEval reader): no JSON mode + bounded
    # max_tokens — the official gen.py call shape
    m2 = OpenAICompatModel(id="deepseek-chat",
                           base_url="https://api.example.com/v1",
                           response_format=None, max_tokens=500)
    req2 = m2.build_request("SYS", "USER")
    assert "response_format" not in req2
    assert req2["max_tokens"] == 500
    # an explicit non-default response_format is honored verbatim
    m3 = OpenAICompatModel(id="x", base_url="https://api.example.com/v1",
                           response_format={"type": "text"})
    assert m3.build_request("S", "U")["response_format"] == {"type": "text"}


# ── Temporal-reasoning context (P1: dates must reach the reader) ───────────


def test_reader_context_surfaces_question_and_session_dates(tmp_path):
    """P1 temporal fix: the reader must see ``Current Date: {question_date}``
    and per-session dates (official gen.py shape) — without them TR questions
    are structurally unanswerable (TR ≈ 0% regardless of retrieval)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_tr_003")
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        # every annotated hit carries its dataset session date
        assert all("session_date" in h for h in ret["hits"])
        text = render_context(ret["hits"],
                              question_date=q.get("question_date", "") or None)
        assert "Current Date: 2025-06-15" in text
        assert "session date 2025-06-10" in text
        # the (mock) reader consumes the same dated context
        hyp = MockReader().answer(
            context_hits=ret["hits"], question=q["question"],
            question_date=q.get("question_date", "") or None)
        assert hyp  # evidence is retrievable + answer-bearing
    finally:
        sdk.close()


def test_render_context_without_dates_is_backward_compatible():
    hits = [{"id": "x", "content": "hi", "lme_session_index": 0}]
    assert render_context(hits) == "[session 0] hi"
    assert render_context(hits, question_date=None) == "[session 0] hi"


# ── #1367: supersede/NAND structure surfaced to the reader ────────────────

def test_render_context_annotates_superseded_and_superseding_hits():
    """#1367: a superseded hit must carry a SUPERSEDED BY marker (with the
    superseding claim's content_snippet) and a superseding hit a SUPERSEDES
    marker — the reader must see "this statement replaced that one" (the
    knowledge-update + multi-session weakness). Reuses the #1353 promoted
    D8 fields; no second supersede-detection path."""
    hits = [
        {"id": "ku_old", "content": "I used to prefer espresso in the morning.",
         "lme_session_index": 0, "session_date": "2025-06-02",
         "superseded_by": {
             "id": "ku_new",
             "content_snippet": "Actually, I now prefer drip coffee over espresso.",
             "created_at": "2025-06-16",
         }},
        {"id": "ku_new", "content": "Actually, I now prefer drip coffee over espresso.",
         "lme_session_index": 1, "session_date": "2025-06-16",
         "supersedes": [{"id": "ku_old",
                          "content_snippet": "I used to prefer espresso in the morning.",
                          "created_at": "2025-06-02"}]},
    ]
    text = render_context(hits, question_date="2025-06-18")
    assert text.startswith("Current Date: 2025-06-18")
    # the superseded hit is marked AND the superseding claim is present
    assert "[SUPERSEDED BY: Actually, I now prefer drip coffee over espresso.]" in text
    assert "I used to prefer espresso in the morning." in text
    # the superseding hit carries the replaced-claim marker
    assert "[SUPERSEDES: I used to prefer espresso in the morning.]" in text
    # the superseding claim's full content is in the context either way
    assert "Actually, I now prefer drip coffee over espresso." in text


def test_render_context_multiple_supersedes_and_chain_midpoint():
    """#1367: multiple replaced claims join with ' ; ' and a hit that both
    replaced something AND was replaced (a chain mid-point) shows both
    markers."""
    hits = [{"id": "mid", "content": "I now prefer pour-over.",
             "lme_session_index": 0,
             "supersedes": [
                 {"id": "a", "content_snippet": "espresso first",
                  "created_at": "d1"},
                 {"id": "b", "content_snippet": "drip second",
                  "created_at": "d2"}],
             "superseded_by": {
                 "id": "c", "content_snippet": "cold brew third",
                 "created_at": "d3"}}]
    text = render_context(hits)
    assert "[SUPERSEDED BY: cold brew third] [SUPERSEDES: espresso first ; drip second]" in text


def test_render_context_supersede_markers_absent_without_promoted_fields():
    """#1367 backward compat: hits WITHOUT the promoted D8 fields (embedded
    TF-IDF fallback — CI) render byte-identically to today; empty/None
    supersession state adds no markers either."""
    hits = [{"id": "x", "content": "hi", "lme_session_index": 0,
             "session_date": "2025-06-10"}]
    assert render_context(hits) == (
        "[session 0] (session date 2025-06-10) hi")
    hits2 = [{"id": "x", "content": "hi", "lme_session_index": 0,
              "superseded_by": None, "supersedes": []}]
    assert render_context(hits2) == "[session 0] hi"
    # a malformed promoted entry (missing content_snippet) is ignored
    hits3 = [{"id": "x", "content": "hi", "lme_session_index": 0,
              "superseded_by": {"id": "y"}, "supersedes": [{"id": "z"}]}]
    assert render_context(hits3) == "[session 0] hi"


def test_annotate_hits_passes_through_supersession_state():
    """#1367: the annotation step carries the search payload's promoted
    superseded_by/supersedes into the annotated hits the reader sees — and
    stays a no-op (None/[]) when the engine didn't decorate (embedded)."""
    raw = [
        # full-path (Docker/HNSW): D8 fields present on the raw hit
        {"id": "ku_old", "content": "I used to prefer espresso.",
         "match_source": "rrf",
         "superseded_by": {"id": "ku_new",
                            "content_snippet": "drip now", "created_at": "t"}},
        # embedded fallback: raw hit carries NO promoted keys
        {"id": "plain", "content": "just a fact", "match_source": "tfidf"},
    ]
    props = {"ku_old": {"lme_session_index": 0, "session_id": "s0",
                         "has_answer": False},
             "plain": {"lme_session_index": 1, "session_id": "s1",
                        "has_answer": False}}
    annotated = _annotate_hits(raw, props, ["2025-06-02", "2025-06-16"])
    by_id = {h["id"]: h for h in annotated}
    assert by_id["ku_old"]["superseded_by"] == {
        "id": "ku_new", "content_snippet": "drip now", "created_at": "t"}
    assert by_id["ku_old"]["supersedes"] == []
    assert by_id["plain"]["superseded_by"] is None
    assert by_id["plain"]["supersedes"] == []
    # every annotated hit carries the keys (reader-side contract)
    assert all("superseded_by" in h and "supersedes" in h for h in annotated)
    # the decorated hit renders with its marker end-to-end
    text = render_context(annotated[:1])
    assert "[SUPERSEDED BY: drip now]" in text


def test_ingest_v2_supersession_end_to_end(tmp_path, monkeypatch):
    """E5 Task 4 (#1537): the v2 eval ingest materializes point-level
    supersession records via the EXISTING canonical sdk.supersede() — a
    two-session question ("gym at 6pm" → "gym at 5pm") lands a CORRECTS
    edge old→new with the old point superseded, and re-ingest is idempotent
    (no raise, single edge, zero writes). The extractor's S3 is simulated as
    a REAL backend (search_graph returns the live graph's statement points —
    embedded mode skips S3 by design) so the REVISES detection sees session
    0's point; the payload written by session 1 must carry the supersession
    record (the old bug: _write_payload never saw it)."""
    import json

    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2
    from tortoise.ids import content_hash

    class _MockModel:
        """Deterministic adapter — S1 story + per-session S2/S4 fixtures."""
        def __init__(self, resp):
            self._resp = resp

        def complete(self, *, system: str, user: str) -> str:
            return self._resp(system, user)

    def _s2(content: str) -> dict:
        return {"entities": [{"name": "gym", "kind": "core:plan",
                              "lifecycle": "created", "supersedes": None,
                              "note": None}],
                "events": [], "operators": [],
                "points": [{"content": content, "pointKind": "statement",
                            "about_entities": ["gym"], "when": None}]}

    def _resp(system: str, user: str) -> str:
        if "STORY SUMMARIZER" in system:
            return "Gym session at 6pm." if "6pm" in user else "Gym session at 5pm."
        # S2 carries the story in the USER prompt; S4 carries it in the SYSTEM
        # prompt (the search results may also mention the old content — match
        # the full story sentence, case+punctuation exact, to disambiguate).
        blob = user if "GRAPH MAPPER" in system else system
        is_6pm = "Gym session at 6pm." in blob
        fixture = _s2("gym at 6pm") if is_6pm else _s2("gym at 5pm")
        if "GRAPH MAPPER" in system:
            return json.dumps(fixture)
        if "GAP REVIEWER" in system:
            return json.dumps(fixture)  # no gaps
        raise AssertionError(f"unexpected system prompt: {system[:50]}")

    def _fake_search(sdk, embed_list, story, **kw):
        """Simulate a real backend: S3 returns the live graph's statement
        points (the current session's points are written AFTER its search)."""
        rows = sdk._get_proj().g.query(
            "MATCH (p:Point {pointKind:'statement'}) RETURN p.id, p.content "
            "LIMIT 25").result_set
        return {"mode": "real", "degraded": False, "reason": None,
                "entities": [], "events": [],
                "points": [{"id": r[0], "content": r[1], "kind": "statement"}
                            for r in rows],
                "queries_run": 1}

    monkeypatch.setattr(ev2, "search_graph", _fake_search)

    sdk = _fresh_sdk(tmp_path)
    try:
        question = {
            "question_id": "q_sup",
            "haystack_session_ids": ["s0", "s1"],
            "haystack_dates": ["2026-06-02", "2026-06-16"],
            "haystack_sessions": [
                [{"role": "user", "content": "gym at 6pm",
                  "has_answer": True}],
                [{"role": "user", "content": "gym at 5pm",
                  "has_answer": True}],
            ],
        }
        model = _MockModel(_resp)
        stats = ingest_haystack_v2(sdk, question, model=model)
        proj = sdk._get_proj()
        # extractor ids are content-addressed pt_<sha[:62]> (commit_schema's
        # point_content_id keeps the full sha — the extractor truncates)
        def _pid(c: str) -> str:
            return f"pt_{content_hash(c)[:62]}"

        old_id = _pid("gym at 6pm")
        new_id = _pid("gym at 5pm")
        # old point exists; CORRECTS edge new→old; old superseded + outdated
        rows = proj.g.query(
            "MATCH (old:Point {id:$old}) RETURN old.status, old.outdated",
            params={"old": old_id}).result_set
        assert rows and rows[0][0] == "superseded" and rows[0][1] is True
        n = proj.g.query(
            "MATCH (new:Point {id:$new})-[:CORRECTS]->(old:Point {id:$old}) "
            "RETURN count(old)",
            params={"new": new_id, "old": old_id}).result_set[0][0]
        assert n == 1
        assert stats["supersessions_written"] == 1
        # the REVISES reason is preserved on the new point (observability)
        rows = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN coalesce(p.reason, '')",
            params={"id": new_id}).result_set
        assert rows and rows[0][0] == "REVISES"

        # idempotent re-ingest: no raise, no second edge, zero writes
        stats2 = ingest_haystack_v2(sdk, question, model=_MockModel(_resp))
        assert stats2["points"] == 0
        assert stats2["supersessions_written"] == 0
        n2 = proj.g.query(
            "MATCH (:Point {id:$old})<-[:CORRECTS]-(p:Point) RETURN count(p)",
            params={"old": old_id}).result_set[0][0]
        assert n2 == 1
    finally:
        sdk.close()


def test_ingest_v2_supersession_missing_endpoint_skips(tmp_path, monkeypatch):
    """E5 Task 4: a supersession record whose endpoints are missing is
    skipped with a warning (fail-open) — never a crash or phantom edge."""
    from tools.longmem_eval.ingest_v2 import _write_payload

    sdk = _fresh_sdk(tmp_path)
    try:
        payload = {"entities": [], "events": [], "operators": [],
                   "points": [{"id": "pt_new", "content": "gym at 5pm",
                               "pointKind": "statement"}],
                   "supersessions": [
                       {"superseded": "pt_missing", "supersedes_by": "pt_new",
                        "evidence": "fact-value contradiction (later session "
                                     "value change)"}]}
        stats = _write_payload(sdk, payload, sid="s1", qid="q", si=0,
                               evidence_turns=[], turns=[],
                               ev_sessions=set())
        assert stats["supersessions_written"] == 0
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (p:Point {id:'pt_missing'}) RETURN count(p)").result_set
        assert rows[0][0] == 0
    finally:
        sdk.close()


def test_retrieve_for_question_surfaces_supersession_annotation(tmp_path):
    """#1367 + E5 (#1537): a real superseded claim in the eval graph (via
    the PRODUCTION supersede_point → CORRECTS edge) is (a) co-retrieved —
    include_terminal=True lets the superseded point survive base retrieval —
    (b) decorated with superseded_by/supersedes even in EMBEDDED mode (the
    call-site fetch_point_epistemic_state decoration, E5 Task 5; the old
    "embedded CI itself can't decorate" caveat is gone by design), and (c)
    rendered with the SUPERSEDED BY marker end-to-end (E2E-6 read side)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini()
                 if x["question_id"] == "mini_ku_004")
        ingest_haystack(sdk, q)
        # real supersession on the eval graph (CORRECTS ku_new → ku_old)
        sdk.create_point(
            "statement", "I used to prefer espresso in the morning.",
            id="ku_old", status="live", lme_question_id=q["question_id"],
            lme_session_index=0, is_episodic=True)
        sdk.create_point(
            "statement", "I now prefer drip coffee over espresso.",
            id="ku_new", status="live", lme_question_id=q["question_id"],
            lme_session_index=1, is_episodic=True)
        sdk.supersede_point("ku_old", "ku_new")

        # co-retrieve with a query matching the OLD claim verbatim — the
        # superseded point must surface in the hits (embedded mode)
        q2 = dict(q)
        q2["question"] = "I used to prefer espresso in the morning"
        ret = retrieve_for_question(sdk, q2, ks=(5,), top_k=20)
        assert all("superseded_by" in h and "supersedes" in h
                   for h in ret["hits"])
        old_hits = [h for h in ret["hits"] if h["id"] == "ku_old"]
        assert old_hits, "superseded point must co-retrieve (E2E-6 read side)"
        old = old_hits[0]
        assert old["superseded_by"] \
            and old["superseded_by"]["id"] == "ku_new"
        assert old["supersedes"] == []

        # the marker renders end-to-end in embedded mode (E5 Task 5
        # decoration + the #1367 render machinery)
        text = render_context(ret["hits"], question_date=q.get("question_date"))
        assert "[SUPERSEDED BY: I now prefer drip coffee over espresso.]" in text
        assert "I used to prefer espresso in the morning." in text
    finally:
        sdk.close()


def test_retrieve_for_question_structural_recall_amplifier(tmp_path):
    """R4 (#1543) — graph as recall amplifier: the answer statement has no
    text overlap with the question; it enters top-k via the structural leg
    (kind-scan of statement points + 1-2 hop IMPL expansion from the text
    hit). match_source is never null (E2E-1)."""
    from tools.longmem_eval.ingest import EXTRACTION_POINT_KIND, ingest_haystack
    from tools.longmem_eval.retrieve import retrieve_for_question

    sdk = _fresh_sdk(tmp_path)
    try:
        # a question whose answer turn is the seed text hit
        q = next(x for x in _mini()
                 if x["question_id"] == "mini_ku_004")
        ingest_haystack(sdk, q)
        # mint an extracted statement (v2-write shape) with NO text overlap
        # with the question, then wire turn → op → statement (IMPL)
        sdk.create_point(
            EXTRACTION_POINT_KIND, "personal best 5K time is 27:12",
            id="r4-amp-stmt-1", session_id="s1",
            lme_question_id=q["question_id"], lme_session_index=0,
            is_episodic=True, status="draft")
        sdk._get_proj().g.query(
            "MATCH (t:Point) WHERE t.id = $tid "
            "MATCH (s:Point) WHERE s.id = $sid "
            "CREATE (op:Point {is_operator:true, op_type:'IMPL', label:'supports'}) "
            "CREATE (t)-[:IMPL {idx:0}]->(op) "
            "CREATE (op)-[:IMPL {idx:1}]->(s)",
            params={"tid": "lme:mini_ku_004:s0:t0", "sid": "r4-amp-stmt-1"},
        )
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        ids = {h["id"] for h in ret["hits"]}
        assert "r4-amp-stmt-1" in ids       # amplifier surfaced the statement
        assert all(h["match_source"] for h in ret["hits"])  # never null
    finally:
        sdk.close()


# ── Error isolation + checkpoint/resume (P2) ───────────────────────────────


def test_single_question_failure_does_not_abort_run(tmp_path):
    """A failing question is recorded in report['failures'] and the run
    continues — one transient LLM error never aborts the whole 500-Q run."""
    class _ExplodingReader(MockReader):
        def answer(self, **kw):
            raise RuntimeError("transient provider boom")

    # M2 (#1523) guard: a plain RuntimeError is UNKNOWN-class (transient-safe
    # per the P2 taxonomy) — the fatal-abort guard must NOT fire for it, so
    # the run keeps recording failures and continuing.
    from tortoise.model_adapters import is_fatal
    assert not is_fatal(RuntimeError("transient provider boom"))

    outcomes, report = run_evaluation(
        _mini()[:2], reader=_ExplodingReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path), max_retries=0,
    )
    assert outcomes == []  # both questions failed
    assert report["n_failed"] == 2
    assert len(report["failures"]) == 2
    qids = {f["question_id"] for f in report["failures"]}
    assert qids == {"mini_ie_user_001", "mini_msr_002"}
    assert all("error" in f and "failed_at_utc" in f for f in report["failures"])
    # aggregates over completed questions only — no crash on empty
    assert report["accuracy"]["overall"] == 0.0


def test_checkpoint_resume_skips_completed_questions(tmp_path):
    cp = tmp_path / "lme-state.json"
    instances = _mini()[:2]
    kwargs = dict(reader=MockReader(), judge=MockJudge(), ks=(5,), top_k=5,
                  split="s", work_dir=str(tmp_path), checkpoint=str(cp))
    outcomes, report = run_evaluation(instances, **kwargs)
    assert len(outcomes) == 2
    assert report["n_failed"] == 0
    assert cp.is_file()
    # M7 (D7): the checkpoint carries the code fingerprint — a resume with
    # IDENTICAL kwargs matches and proceeds (pinned here).
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert "fingerprint" in saved
    assert saved["fingerprint"]["reader_model"] == "mock-reader"
    assert saved["fingerprint"]["judge_model"] == "mock-judge"
    assert saved["fingerprint"]["ks"] == [5]
    assert saved["fingerprint"]["max_retries"] == 3

    # resume: both are skipped (no re-execution) → identical outcomes
    outcomes2, report2 = run_evaluation(instances, **kwargs)
    assert [o["question_id"] for o in outcomes2] == \
        [o["question_id"] for o in outcomes]
    assert outcomes2[0]["label"] == outcomes[0]["label"]
    assert report2["n_failed"] == 0

    # failures are checkpointed too and skipped on resume
    class _ExplodingReader(MockReader):
        def answer(self, **kw):
            raise RuntimeError("boom")

    outcomes3, report3 = run_evaluation(
        _mini()[:2], reader=_ExplodingReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
        checkpoint=str(tmp_path / "lme-fail.json"), max_retries=0)
    assert outcomes3 == []
    assert report3["n_failed"] == 2
    # M7 (D7): failure entries carry the P2-aligned eval error class — a
    # RuntimeError at the reader that burned its retries (max_retries=0) is
    # reader:retries_exhausted.
    assert {f["error_class"] for f in report3["failures"]} == \
        {"reader:retries_exhausted"}
    # a resume over the failed checkpoint keeps skipping them (no re-run);
    # the resume must use the SAME config (max_retries is part of the
    # fingerprint — a different value is a refused stale resume, D7).
    outcomes4, report4 = run_evaluation(
        _mini()[:2], reader=MockReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
        checkpoint=str(tmp_path / "lme-fail.json"), max_retries=0)
    assert outcomes4 == []
    assert report4["n_failed"] == 2


# ── Dataset download atomicity (P2: corrupt cache) ─────────────────────────


def test_download_is_atomic_and_validated(monkeypatch, tmp_path):
    """Interrupted downloads must never poison the cache: temp .part file +
    JSON validation + atomic rename into place."""
    from tools.longmem_eval import dataset as ds  # noqa: I001
    import urllib.error
    import urllib.request

    class _FakeResp:
        def __init__(self, mode):
            self._mode = mode
            self._payload = b'[{"question_id": "mini_x"}]'

        def read(self, n):
            if self._mode == "interrupted":
                raise urllib.error.URLError("connection reset")
            data, self._payload = self._payload[:n], self._payload[n:]
            return data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    calls = {"n": 0}

    def _fake_urlopen(req, timeout=120):
        calls["n"] += 1
        return _FakeResp("interrupted" if calls["n"] == 1 else "ok")

    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
    dest = tmp_path / ds.SPLIT_FILES["s"]

    with pytest.raises(urllib.error.URLError):
        ds._download("https://example.invalid/x.json", dest)
    # interrupted download leaves NO final file and cleans up the .part
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()

    ds._download("https://example.invalid/x.json", dest)
    assert dest.is_file()
    assert json.loads(dest.read_text(encoding="utf-8")) == [
        {"question_id": "mini_x"}]
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_creates_missing_cache_dir(monkeypatch, tmp_path):
    """#1360: a fresh (non-existent) cache dir must be auto-created — first-
    run downloads previously crashed with FileNotFoundError writing the .part."""
    from tools.longmem_eval import dataset as ds  # noqa: I001
    import urllib.request

    class _FakeResp:
        def __init__(self):
            self._payload = b'[{"question_id": "mini_x"}]'

        def read(self, n):
            data, self._payload = self._payload[:n], self._payload[n:]
            return data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda req, timeout=120: _FakeResp())

    # Parent chain does NOT exist (fresh cache on first run).
    dest = tmp_path / "fresh" / "nested" / "cache" / ds.SPLIT_FILES["s"]
    ds._download("https://example.invalid/x.json", dest)

    assert dest.is_file()
    assert json.loads(dest.read_text(encoding="utf-8")) == [
        {"question_id": "mini_x"}]
    # No .part residue
    assert not dest.with_name(dest.name + ".part").exists()


def test_corrupt_cache_is_not_served(monkeypatch, tmp_path):
    """A corrupt cached file must raise (or re-download), never be served."""
    from tools.longmem_eval import dataset as ds
    cache = tmp_path / "cache"
    cache.mkdir()
    bad = cache / ds.SPLIT_FILES["s"]
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("TORTOISE_LME_CACHE_DIR", str(cache))

    # download disabled → fail loudly rather than serve the corrupt file
    with pytest.raises((json.JSONDecodeError, ValueError, UnicodeDecodeError)):
        ds.load_dataset("s", limit=1, download=False)


# ── Full run is gated on dataset + keys (never in CI) ─────────────────────

@pytest.mark.slow
def test_full_run_gated_on_keys(monkeypatch):
    for k in ("OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY",
              "GEMINI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError):
        build_reader()  # fail-closed without keys


@pytest.mark.slow
def test_dataset_download_gated_on_network(monkeypatch, tmp_path):
    monkeypatch.setenv("TORTOISE_LME_CACHE_DIR", str(tmp_path / "cache"))
    from tools.longmem_eval import dataset as ds

    with pytest.raises(Exception):  # noqa: B017
        ds.load_dataset("s", limit=1, download=False)  # no cache → fails


# ── #1369: v2 ingest mode (deterministic — monkeypatched extractor) ─────

def test_v2_ingest_writes_payload_with_evidence_marks(tmp_path, monkeypatch):
    """#1369 + R1 (#1540): ingest_haystack_v2 writes the extractor payload
    (points with evidence marks by content overlap, entities, events,
    operators) and retains the Session + turn-granular raw chunks — the
    answer session's chunk carries the containment mark (M6, mark c)."""
    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    payload = {
        "entities": [{"name": "the strategy", "kind": "core:strategy"}],
        "events": [{"content": "we decided X", "eventKind": "core:decision"}],
        "points": [
            {"id": "pt_alpha", "content": "the quantum observation is the key fact",
             "pointKind": "statement",
             "quote": "quantum observation is key",
             "search_keys": ["quantum observation"],
             "source_turn_id": 0},
            {"id": "pt_beta", "content": "unrelated mechanics note",
             "pointKind": "statement"},
        ],
        "operators": [{"src": "pt_alpha", "dst": "pt_beta", "op_type": "IMPL"}],
    }

    def _fake_extract(model, conversation, **kw):
        return {"payload": payload, "minted_kinds": [], "supersessions": [],
                "errors": [], "warnings": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)

    sdk = _fresh_sdk(tmp_path)
    try:
        question = {
            "question_id": "test_v2_q",
            "haystack_session_ids": ["sess-1"],
            "haystack_dates": ["2026-08-01"],
            "haystack_sessions": [[
                {"role": "user", "content": "quantum observation is key",
                 "has_answer": True},
                {"role": "assistant", "content": "ack"},
            ]],
        }
        stats = ingest_haystack_v2(sdk, question, model=object(),
                                   chunk_turns=2)
        assert stats["points"] == 2
        assert stats["operators"] == 1
        assert stats["entities"] == 1
        assert stats["events"] == 1
        # R1: the 2-turn session yields one chunk (chunk_turns=2); the
        # :raw id is retired
        assert stats["chunks"] == 1
        proj = sdk._get_proj()
        raw_rows = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN count(*)",
            params={"id": "lme:test_v2_q:s0:raw"}).result_set
        assert raw_rows[0][0] == 0  # :raw gone
        chunk_rows = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN p.has_answer",
            params={"id": "lme:test_v2_q:s0:c0"}).result_set
        assert chunk_rows[0][0] is True  # containment mark (turn 0 is evidence)
        # the evidence-overlapping point carries has_answer
        ev_rows = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN p.has_answer",
            params={"id": "pt_alpha"}).result_set
        assert ev_rows[0][0] is True
        # E3: turn points exist with speaker; the extracted point resolves
        # the source_turn_id index → turn node id (D6/D8)
        tr = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN p.speaker",
            params={"id": "lme:test_v2_q:s0:t0"}).result_set
        assert tr and tr[0][0] == "user"
        pt_props = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN p.quote, p.search_keys, "
            "p.source_turn_id, coalesce(p.speaker, '')",
            params={"id": "pt_alpha"}).result_set
        assert pt_props[0][0] == "quantum observation is key"
        assert pt_props[0][1] == ["quantum observation"]
        assert pt_props[0][2] == "lme:test_v2_q:s0:t0"
        # review-gate fix, graph mirror: speaker is NEVER written on an
        # extracted point (derived at read time from the source-turn link)
        assert pt_props[0][3] == ""
        # pt_beta (no E3 fields) stays pre-E3-shaped — the E3-additive props
        # (search_keys/source_turn_id) and speaker do NOT exist on the node
        # (EXISTS, not coalesce: absent ≠ empty; quote='' is always written
        # by this path — a pre-existing field, not an E3 addition)
        beta = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN "
            "toBoolean(EXISTS(p.search_keys)), toBoolean(EXISTS(p.source_turn_id)), "
            "toBoolean(EXISTS(p.speaker)), coalesce(p.has_answer, false)",
            params={"id": "pt_beta"}).result_set
        assert beta[0][0] is False and beta[0][1] is False
        assert beta[0][2] is False
        # has_answer is M6's domain (#1526), not E3's: the fixture's session
        # carries a has_answer turn, so M6 mark (a) source-session attribution
        # marks EVERY point from that session — including the non-overlapping
        # pt_beta (M6 recalibration, OR of 3 marks; the pre-M6 overlap-only
        # negative is obsolete). E3 does not change evidence marking.
        assert beta[0][3] is True
        # CONTAINS edges: session → chunk + extracted points + turn points
        cnt = proj.g.query(
            "MATCH (s:Session {id:$id})-[:CONTAINS]->(p) RETURN count(*)",
            params={"id": "lme:test_v2_q:s0"}).result_set
        assert cnt[0][0] == 5  # 1 chunk + 2 extracted + 2 turn points
    finally:
        sdk.close()


def test_v2_ingest_out_of_range_source_turn_drops_link(tmp_path, monkeypatch):
    """E3: the index→node-id resolution guard (type() is int, bounded to the
    session's turn count) silently drops a link for out-of-range / non-int /
    boolean payload source_turn_id — no dangling turn node id is ever written."""
    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    payload = {"entities": [], "events": [], "points": [
        {"id": "pt_bad", "content": "beyond session turns",
         "pointKind": "statement", "source_turn_id": 5},
        {"id": "pt_str", "content": "string turn ref",
         "pointKind": "statement", "source_turn_id": "0"},
        {"id": "pt_bool", "content": "boolean turn ref",
         "pointKind": "statement", "source_turn_id": True},
    ], "operators": []}

    def _fake_extract(model, conversation, **kw):
        return {"payload": payload, "minted_kinds": [], "supersessions": [],
                "errors": [], "warnings": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)
    sdk = _fresh_sdk(tmp_path)
    try:
        # 1-turn session: index 5 is beyond the session, "0" is a string,
        # True is a bool (int subclass) — all must drop the link
        question = {
            "question_id": "q_out", "haystack_session_ids": ["sess-1"],
            "haystack_dates": ["2026-08-01"],
            "haystack_sessions": [[{"role": "user", "content": "hi"}]],
        }
        ingest_haystack_v2(sdk, question, model=object())
        proj = sdk._get_proj()
        for pid in ("pt_bad", "pt_str", "pt_bool"):
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.source_turn_id, '')",
                params={"id": pid}).result_set
            assert rows and rows[0][0] == "", f"{pid} must have no source_turn_id"
    finally:
        sdk.close()


def test_v2_chunks_marked_by_containment(tmp_path, monkeypatch):
    """M6/R1: a 4-turn session with evidence in turn 3 → chunk c1 (turns
    2-3) is containment-marked, chunk c0 (turns 0-1) is not."""
    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    def _fake_extract(model, conversation, **kw):
        return {"payload": {"entities": [], "events": [], "points": [],
                            "operators": []},
                "minted_kinds": [], "supersessions": [], "errors": [],
                "warnings": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)

    sdk = _fresh_sdk(tmp_path)
    try:
        question = {
            "question_id": "containment_q",
            "haystack_session_ids": ["cs-0"],
            "haystack_dates": ["2026-08-01"],
            "haystack_sessions": [[
                {"role": "user", "content": "filler one"},
                {"role": "user", "content": "filler two"},
                {"role": "user", "content": "filler three"},
                {"role": "user", "content": "the dog is named Rex",
                 "has_answer": True},
            ]],
        }
        ingest_haystack_v2(sdk, question, model=object(), chunk_turns=2)
        proj = sdk._get_proj()

        def _mark(pid):
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.has_answer, false)",
                params={"id": pid}).result_set
            return bool(rows[0][0])

        assert _mark("lme:containment_q:s0:c0") is False  # turns 0-1
        assert _mark("lme:containment_q:s0:c1") is True   # turns 2-3
    finally:
        sdk.close()


def test_v2_extractor_failure_retains_chunks(tmp_path, monkeypatch):
    """The exact "silent evidence-leg emptiness" guard (R8 #1540): chunks +
    containment marks are written BEFORE extraction, so an extractor
    failure still retains the verbatim evidence and the session remains
    retrievable; the error is recorded and the run continues."""
    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    def _boom(model, conversation, **kw):
        raise RuntimeError("extractor crash")

    monkeypatch.setattr(ev2, "extract_session_v2", _boom)

    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_ie_user_001")
        stats = ingest_haystack_v2(sdk, q, model=object(), chunk_turns=2)
        assert stats["errors"]  # extractor failure recorded
        proj = sdk._get_proj()
        # the evidence-session chunk (s1:c1 contains the answer turn) was
        # still written AND containment-marked
        rows = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN coalesce(p.has_answer, false)",
            params={"id": "lme:mini_ie_user_001:s1:c1"}).result_set
        assert rows[0][0] is True
        # the session still surfaces via its chunks (raw recall floor)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        assert ret["session_recall@k"]["5"] >= 0.5
        # D5: no extracted points → evidence_recall is N/A (None), never
        # a forced 0.0, while the chunk containment view is real
        assert ret["evidence_recall@k"]["5"] is None
        assert isinstance(ret["chunk_evidence_recall@k"]["5"], float)
    finally:
        sdk.close()


def test_v2_ingest_writes_when_and_started_at(tmp_path, monkeypatch):
    """T7 (#1533): a dated haystack session writes Point.when and
    Event.startedAt onto the graph nodes (the E1 write side) — the
    call-site threading assert also covers T9 for the dated leg."""
    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    payload = {
        "entities": [],
        "events": [{"content": "we decided X", "eventKind": "core:decision",
                     "started_at": "2026-08-01"}],
        "points": [{"id": "pt_alpha", "content": "the strategy shifted",
                     "pointKind": "statement", "when": "2026-08-01"}],
        "operators": [],
    }

    def _fake_extract(model, conversation, **kw):
        assert kw.get("session_date") == "2026-08-01"  # T9: threaded
        return {"payload": payload, "minted_kinds": [], "supersessions": [],
                "errors": [], "warnings": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)

    sdk = _fresh_sdk(tmp_path)
    try:
        question = {
            "question_id": "test_v2_dates_q",
            "haystack_session_ids": ["sess-1"],
            "haystack_dates": ["2026-08-01"],
            "haystack_sessions": [[{"role": "user", "content": "shifted",
                                    "has_answer": True}]],
        }
        stats = ingest_haystack_v2(sdk, question, model=object())
        assert stats["points"] == 1
        assert stats["events"] == 1
        proj = sdk._get_proj()
        when_rows = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN p.when",
            params={"id": "pt_alpha"}).result_set
        assert when_rows[0][0] == "2026-08-01"
        sat_rows = proj.g.query(
            "MATCH (e:Event {name:'we decided X'}) RETURN e.startedAt"
        ).result_set
        assert sat_rows[0][0] == "2026-08-01"
    finally:
        sdk.close()


def test_v2_ingest_undated_writes_no_dates(tmp_path, monkeypatch):
    """T8 (#1533, E2E-4 owned negative): an undated session writes no
    when/startedAt props onto the graph nodes."""
    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    payload = {
        "entities": [],
        "events": [{"content": "we decided X", "eventKind": "core:decision"}],
        "points": [{"id": "pt_alpha", "content": "the strategy shifted",
                     "pointKind": "statement"}],
        "operators": [],
    }

    def _fake_extract(model, conversation, **kw):
        return {"payload": payload, "minted_kinds": [], "supersessions": [],
                "errors": [], "warnings": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)

    sdk = _fresh_sdk(tmp_path)
    try:
        question = {
            "question_id": "test_v2_undated_q",
            "haystack_session_ids": ["sess-1"],
            "haystack_dates": [],
            "haystack_sessions": [[{"role": "user", "content": "shifted"}]],
        }
        ingest_haystack_v2(sdk, question, model=object())
        proj = sdk._get_proj()
        when_rows = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN p.when",
            params={"id": "pt_alpha"}).result_set
        assert not when_rows[0][0]
        sat_rows = proj.g.query(
            "MATCH (e:Event {name:'we decided X'}) RETURN e.startedAt"
        ).result_set
        assert not sat_rows[0][0]
    finally:
        sdk.close()


def test_v2_ingest_threads_session_date(tmp_path, monkeypatch):
    """T9 (#1533): ingest_haystack_v2 hands the dataset's session date to
    extract_session_v2; an undated session passes None (no false date)."""
    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    received = []

    def _fake_extract(model, conversation, **kw):
        received.append(kw.get("session_date"))
        return {"payload": {}, "minted_kinds": [], "supersessions": [],
                "errors": [], "warnings": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)

    sdk = _fresh_sdk(tmp_path)
    try:
        question = {
            "question_id": "test_v2_thread_q",
            "haystack_session_ids": ["sess-1", "sess-2"],
            "haystack_dates": ["2026-08-01"],  # session 0 dated
            "haystack_sessions": [
                [{"role": "user", "content": "a"}],
                [{"role": "user", "content": "b"}],  # session 1 undated
            ],
        }
        ingest_haystack_v2(sdk, question, model=object())
        assert received == ["2026-08-01", None]
    finally:
        sdk.close()


def test_v2_ingest_cli_flag(tmp_path, monkeypatch):
    """#1369: --ingest-mode v2 is a valid CLI flag and routes to the v2
    ingestion — the report methodology records ingest_mode=v2 and the
    extractor stats surface (monkeypatched extractor, no API)."""
    import subprocess
    import sys
    r = subprocess.run(
        [sys.executable, "-m", "tools.longmem_eval.run", "--help"],
        capture_output=True, text=True, cwd=str(Path(__file__).parent.parent))
    assert "--ingest-mode" in r.stdout
    assert "deterministic" in r.stdout and "v2" in r.stdout

    # Real routing: run the mini with --ingest-mode v2 --mock and a
    # monkeypatched extractor; the report must record the v2 methodology
    # and the ingest stats must show the mocked points.
    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.run import run_main

    payload = {"entities": [], "events": [], "points": [
        {"id": "pt_cli", "content": "the answer to the question",
         "pointKind": "statement"}], "operators": []}

    def _fake(model, conversation, **kw):
        return {"payload": payload, "minted_kinds": [], "supersessions": [],
                "errors": [], "warnings": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake)
    report = run_main(["--data", str(MINI), "--limit", "1", "--split", "s",
                       "--ingest-mode", "v2", "--mock"])
    assert report["methodology"]["ingest_mode"] == "v2"
    assert "v2 extractor ingestion" in report["methodology"]["extraction_approach"]
    o = report["outcomes"][0]
    assert o["n_ingest_errors"] == 0
    assert report["retrieval"]["evidence_recall@k"] is not None


# ── E3 (issue #1535): read-time speaker derivation ─────────────────────────

class TestE3SpeakerDerivation:
    def test_context_renders_speaker_prefix(self):
        # prefix-renderer behavior: a known speaker decorates between the
        # session prefix and the content (derivation itself is covered by
        # test_annotate_hits_resolves_turn_speaker + the retrieve path test)
        from tools.longmem_eval import retrieve as rt
        hits = [{"id": "pt_x", "content": "my 5K best is 27:12",
                 "speaker": "user", "lme_session_index": 0,
                 "session_date": "", "has_answer": False,
                 "superseded_by": None, "supersedes": []}]
        ctx = rt.render_context(hits)
        assert ctx == "[session 0] [user] my 5K best is 27:12"

    def test_context_unchanged_without_speaker(self):
        # byte-identical backward-compat: no speaker → EXACT pre-E3 rendering
        from tools.longmem_eval import retrieve as rt
        hits = [{"id": "pt_y", "content": "plain fact",
                 "speaker": None, "lme_session_index": 0,
                 "session_date": "", "has_answer": False,
                 "superseded_by": None, "supersedes": []}]
        assert rt.render_context(hits) == "[session 0] plain fact"

    def test_turn_point_content_no_double_speaker_decoration(self):
        # P1-1: turn points are written with content "[role] text" AND the
        # speaker prop — decorating on top would render "[user] [user] ..."
        # (the deterministic leg's primary recall surface). The role bracket
        # already carries the attribution; decoration must be skipped.
        from tools.longmem_eval import retrieve as rt
        hits = [{"id": "lme:q1:s0:t0",
                 "content": "[user] my 5K best is 27:12",
                 "speaker": "user", "lme_session_index": 0,
                 "session_date": "", "has_answer": False,
                 "superseded_by": None, "supersedes": []}]
        assert rt.render_context(hits) == "[session 0] [user] my 5K best is 27:12"

    def test_non_role_bracket_still_decorated(self):
        # P1-1 guard precision: only a leading ROLE bracket suppresses the
        # decoration — a session/date-style bracket elsewhere must not
        content = "[context] the 5K best is 27:12"  # not a role bracket
        from tools.longmem_eval import retrieve as rt
        hits = [{"id": "pt_z", "content": content,
                 "speaker": "user", "lme_session_index": 0,
                 "session_date": "", "has_answer": False,
                 "superseded_by": None, "supersedes": []}]
        ctx = rt.render_context(hits)
        assert ctx == "[session 0] [user] [context] the 5K best is 27:12"

    def test_annotate_hits_resolves_turn_speaker(self, tmp_path):
        from tools.longmem_eval import retrieve as rt
        from tools.longmem_eval.ingest import point_props_for_hits
        sdk = _fresh_sdk(tmp_path)
        try:
            sdk.create_point("statement", "my 5K best is 27:12", id="pt_x",
                             source_turn_id="lme:q1:s0:t0",
                             lme_session_index=0, is_episodic=True)
            sdk.create_point("event", "[user] my 5K best is 27:12",
                             id="lme:q1:s0:t0", speaker="user",
                             lme_session_index=0, is_episodic=True)
            proj = sdk._get_proj()
            props = point_props_for_hits(proj, ["pt_x", "lme:q1:s0:t0"])
            assert props["pt_x"]["source_turn_id"] == "lme:q1:s0:t0"
            annotated = rt._annotate_hits(
                [{"id": "pt_x", "content": "my 5K best is 27:12",
                  "match_source": "fts"}], props, [])
            assert annotated[0]["speaker"] == "user"
        finally:
            sdk.close()

    def test_retrieve_for_question_resolves_turn_not_in_batch(self, tmp_path,
                                                               monkeypatch):
        """D7's primary derivation surface: the extracted point is retrieved
        but its turn node is NOT in the hit batch — the _speaker_for_turns
        batch lookup supplies the speaker. hybrid_search is pinned to return
        ONLY pt_x so the turn can never be in the batch (the backfill is the
        only possible speaker source; deleting it fails this test)."""
        from tools.longmem_eval import retrieve as rt
        sdk = _fresh_sdk(tmp_path)
        try:
            sdk.create_point("statement", "my 5K best is 27:12", id="pt_x",
                             source_turn_id="lme:q1:s0:t0",
                             lme_question_id="q1", lme_session_index=0,
                             session_id="s0", is_episodic=True)
            sdk.create_point("event", "[user] ack",
                             id="lme:q1:s0:t0", speaker="user",
                             lme_question_id="q1", lme_session_index=0,
                             session_id="s0", is_episodic=True)
            monkeypatch.setattr(
                rt, "hybrid_search",
                lambda sdk, query, limit, *, leg_trace=None,
                       entity_types=("point",), recency_fields=None,
                       recency_boost=0.0: [{"id": "pt_x",
                                            "content": "my 5K best is 27:12",
                                            "match_source": "fts"}])
            question = {
                "question_id": "q1", "question": "what is the 5K best",
                "answer_session_ids": ["s0"], "haystack_dates": ["2026-08-01"],
                "haystack_sessions": [[{"role": "user", "content": "my 5K best is 27:12"}]],
            }
            out = rt.retrieve_for_question(sdk, question, ks=(5,), top_k=20)
            assert {h["id"] for h in out["hits"]} == {"pt_x"}
            assert out["hits"][0]["speaker"] == "user"
            ctx = rt.render_context(out["hits"])
            assert "[user] my 5K best is 27:12" in ctx
        finally:
            sdk.close()

    def test_missing_turn_node_speaker_empty(self, tmp_path):
        """A dangling source_turn_id (turn node does not exist) yields empty
        speaker — byte-identical render, never a crash."""
        from tools.longmem_eval import retrieve as rt
        sdk = _fresh_sdk(tmp_path)
        try:
            sdk.create_point("statement", "my 5K best is 27:12", id="pt_x",
                             source_turn_id="lme:q1:s0:t99",
                             lme_question_id="q1", lme_session_index=0,
                             session_id="s0", is_episodic=True)
            question = {
                "question_id": "q1", "question": "what is the 5K best",
                "answer_session_ids": ["s0"], "haystack_dates": ["2026-08-01"],
                "haystack_sessions": [[{"role": "user", "content": "my 5K best is 27:12"}]],
            }
            out = rt.retrieve_for_question(sdk, question, ks=(5,), top_k=20)
            hit = next(h for h in out["hits"] if h["id"] == "pt_x")
            assert hit["speaker"] == ""
            ctx = rt.render_context(out["hits"])
            assert "[speaker]" not in ctx
        finally:
            sdk.close()

# ── R1 #1540: per-session chunk dedup + budget-capped context ─────────────

def test_session_dedup_cap_in_pool(tmp_path, monkeypatch):
    """E2E-1 pool contract (R1 #1540): at most max_chunks_per_session raw
    chunks per session survive in the pool (rank order); points are never
    capped; dedup_stats counts the capped chunks."""
    from tools.longmem_eval import retrieve as rtr

    sdk = _fresh_sdk(tmp_path)
    try:
        for ci in range(5):
            sdk.create_point("session-transcript", f"chunk {ci}", id=f"a{ci}",
                             session_id="sess-a", lme_question_id="dedup_q",
                             lme_session_index=0, is_episodic=True,
                             status="draft")
        sdk.create_point("statement", "the answer point", id="b0",
                         session_id="sess-b", lme_question_id="dedup_q",
                         lme_session_index=1, is_episodic=True, status="draft")

        def _fake_search(sdk_, query, limit, *, leg_trace=None,
                 entity_types=("point",), recency_fields=None,
                 recency_boost=0.0):
            return ([{"id": f"a{ci}", "content": f"chunk {ci}",
                      "match_source": "tfidf"} for ci in range(5)]
                    + [{"id": "b0", "content": "the answer point",
                        "match_source": "tfidf"}])

        monkeypatch.setattr(rtr, "hybrid_search", _fake_search)
        q = {"question_id": "dedup_q", "question_type": "single-session-user",
             "question": "what", "answer": "x", "question_date": "2025-06-15",
             "haystack_session_ids": ["sess-a", "sess-b"],
             "haystack_dates": ["2025-06-10", "2025-06-12"],
             "answer_session_ids": ["sess-b"],
             "haystack_sessions": [
                 [], [{"role": "user", "content": "the answer point",
                       "has_answer": True}]]}
        ret = retrieve_for_question(sdk, q, ks=(5, 10), top_k=20,
                                    max_chunks_per_session=2)
        a_chunks = [h for h in ret["hits"]
                    if _is_raw_chunk(h) and h["session_id"] == "sess-a"]
        assert len(a_chunks) == 2
        assert ret["dedup_stats"]["chunks_capped"] == 3
        # pinned contract: ret["hits"] == the deduped pool
        assert len(ret["hits"]) == 3
        assert ret["hits"][-1]["id"] == "b0"  # points never capped
    finally:
        sdk.close()


def test_dedup_missing_session_index_no_collapse():
    """Distinct sessions with missing/-1 lme_session_index never collapse
    into one shared -1 bucket (that would over-dedup different sessions
    together — D3 #1540)."""
    hits = [
        {"id": "a0", "content": "a", "point_kind": "session-transcript",
         "session_id": "sess-a", "lme_session_index": -1},
        {"id": "a1", "content": "b", "point_kind": "session-transcript",
         "session_id": "sess-a", "lme_session_index": -1},
        {"id": "a2", "content": "c", "point_kind": "session-transcript",
         "session_id": "sess-a", "lme_session_index": -1},
        {"id": "b0", "content": "d", "point_kind": "session-transcript",
         "session_id": "sess-b", "lme_session_index": -1},
        {"id": "b1", "content": "e", "point_kind": "session-transcript",
         "session_id": "sess-b", "lme_session_index": -1},
        {"id": "b2", "content": "f", "point_kind": "session-transcript",
         "session_id": "sess-b", "lme_session_index": -1},
    ]
    pool = _dedup_pool(hits, max_chunks_per_session=2)
    assert [h["id"] for h in pool] == ["a0", "a1", "b0", "b1"]
    # missing session_id entirely → bucket by lme_session_index (distinct
    # indices are distinct buckets, never one shared idx:-1)
    hits2 = [
        {"id": "x0", "content": "a", "point_kind": "session-transcript",
         "session_id": "", "lme_session_index": 0},
        {"id": "x1", "content": "b", "point_kind": "session-transcript",
         "session_id": "", "lme_session_index": 0},
        {"id": "x2", "content": "c", "point_kind": "session-transcript",
         "session_id": "", "lme_session_index": 0},
        {"id": "y0", "content": "d", "point_kind": "session-transcript",
         "session_id": "", "lme_session_index": 1},
        {"id": "y1", "content": "e", "point_kind": "session-transcript",
         "session_id": "", "lme_session_index": 1},
    ]
    pool2 = _dedup_pool(hits2, max_chunks_per_session=2)
    assert [h["id"] for h in pool2] == ["x0", "x1", "y0", "y1"]


def test_session_crowded_out_still_surfaces(tmp_path, monkeypatch):
    """Pool-depth headroom (R2 #1540): candidates are fetched at max(ks)*3
    so a monopolizing session's points cannot crowd other sessions out
    BEFORE dedup runs — a session ranked beyond the raw top-20 still
    appears in the pool and its session_recall@k is non-zero at k=25."""
    from tools.longmem_eval import retrieve as rtr

    sdk = _fresh_sdk(tmp_path)
    try:
        for i in range(20):
            sdk.create_point("event", f"filler {i}", id=f"a{i}",
                             session_id="crowd-a", lme_question_id="crowd_q",
                             lme_session_index=0, is_episodic=True,
                             status="draft")
        sdk.create_point("event", "the sky is blue", id="b0",
                         session_id="crowd-b", lme_question_id="crowd_q",
                         lme_session_index=1, is_episodic=True, status="draft")
        captured = {}

        def _fake_search(sdk_, query, limit, *, leg_trace=None,
                 entity_types=("point",), recency_fields=None,
                 recency_boost=0.0):
            captured["limit"] = limit
            return ([{"id": f"a{i}", "content": f"filler {i}",
                      "match_source": "tfidf"} for i in range(20)]
                    + [{"id": "b0", "content": "the sky is blue",
                        "match_source": "tfidf"}])

        monkeypatch.setattr(rtr, "hybrid_search", _fake_search)
        q = {"question_id": "crowd_q", "question_type": "single-session-user",
             "question": "what color is the sky", "answer": "blue",
             "question_date": "2025-06-15",
             "haystack_session_ids": ["crowd-a", "crowd-b"],
             "haystack_dates": ["2025-06-10", "2025-06-12"],
             "answer_session_ids": ["crowd-b"],
             "haystack_sessions": [
                 [{"role": "user", "content": f"filler {i}",
                   "has_answer": False} for i in range(20)],
                 [{"role": "user", "content": "the sky is blue",
                   "has_answer": True}]]}
        ret = retrieve_for_question(sdk, q, ks=(20, 25), top_k=20)
        assert captured["limit"] == 25 * 3  # max(ks) * 3 depth headroom
        # B's point (ranked 21st raw) is in the deduped pool — with depth
        # max(ks)=25 it would have been excluded entirely
        assert ret["hits"][-1]["id"] == "b0"
        assert ret["session_recall@k"]["25"] > 0.0
        assert ret["session_recall@k"]["20"] == 0.0  # honest windowing
        assert ret["dedup_stats"]["pool_depth_requested"] == 75
    finally:
        sdk.close()


def test_recall_on_deduped_pool(tmp_path, monkeypatch):
    """Recall@k is computed on the DEDUPED pool (the retrieval contract): a
    session's 3rd-ranked evidence chunk is capped away, so
    chunk_evidence_recall@k honestly reflects only what the reader could
    see (2 of 3 marked chunks), while session recall stays 1.0."""
    from tools.longmem_eval import retrieve as rtr

    sdk = _fresh_sdk(tmp_path)
    try:
        for ci in range(3):
            sdk.create_point("session-transcript", f"evidence chunk {ci}",
                             id=f"e{ci}", session_id="sess-e",
                             lme_question_id="pool_q", lme_session_index=0,
                             is_episodic=True, has_answer=True, status="draft")

        def _fake_search(sdk_, query, limit, *, leg_trace=None,
                 entity_types=("point",), recency_fields=None,
                 recency_boost=0.0):
            return [{"id": f"e{ci}", "content": f"evidence chunk {ci}",
                     "match_source": "tfidf"} for ci in range(3)]

        monkeypatch.setattr(rtr, "hybrid_search", _fake_search)
        q = {"question_id": "pool_q", "question_type": "single-session-user",
             "question": "evidence", "answer": "x", "question_date": "2025-06-15",
             "haystack_session_ids": ["sess-e"], "haystack_dates": ["2025-06-10"],
             "answer_session_ids": ["sess-e"],
             "haystack_sessions": [[{"role": "user", "content": "evidence chunk 0",
                                     "has_answer": True}]]}
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20,
                                    max_chunks_per_session=2)
        assert ret["session_recall@k"]["5"] == 1.0  # present via capped chunks
        assert ret["dedup_stats"]["chunks_capped"] == 1
        assert ret["chunk_evidence_recall@k"]["5"] == 2 / 3
        assert ret["evidence_recall@k"]["5"] is None  # no extracted points
    finally:
        sdk.close()


def test_context_points_first_chunks_backfill():
    """UX-3 (R1 #1540): all non-session-transcript hits order before raw
    chunks (rank order within each tier), bounded by top_k then budget."""
    pool = [
        {"id": "chunk1", "content": "chunk number one here",
         "point_kind": "session-transcript", "lme_session_index": 0,
         "session_date": ""},
        {"id": "pt1", "content": "point one", "point_kind": "statement",
         "lme_session_index": 0},
        {"id": "chunk2", "content": "chunk number two here",
         "point_kind": "session-transcript", "lme_session_index": 1,
         "session_date": ""},
        {"id": "pt2", "content": "point two", "point_kind": "statement",
         "lme_session_index": 1},
    ]
    ctx = _assemble_context(pool, top_k=20, max_context_tokens=10**6)
    assert [h["id"] for h in ctx] == ["pt1", "pt2", "chunk1", "chunk2"]
    # top_k bounds the points-first reordered list
    ctx2 = _assemble_context(pool, top_k=2, max_context_tokens=10**6)
    assert [h["id"] for h in ctx2] == ["pt1", "pt2"]


def test_context_token_budget_enforced():
    """Cap below the full pool → context_tokens ≤ cap; the truncated tail
    is chunks, not points (points-first backfill)."""
    pool = [
        {"id": "chunk1", "content": "chunk number one here",
         "point_kind": "session-transcript", "lme_session_index": 0,
         "session_date": ""},
        {"id": "pt1", "content": "point one", "point_kind": "statement",
         "lme_session_index": 0},
        {"id": "chunk2", "content": "chunk number two here",
         "point_kind": "session-transcript", "lme_session_index": 1,
         "session_date": ""},
        {"id": "pt2", "content": "point two", "point_kind": "statement",
         "lme_session_index": 1},
    ]
    ctx = _assemble_context(pool, top_k=20, max_context_tokens=9)
    assert _estimate_tokens(render_context(ctx)) <= 9
    assert [h["id"] for h in ctx] == ["pt1", "pt2"]  # chunks truncated
    assert all(not _is_raw_chunk(h) for h in ctx)


def test_context_points_reader_alignment(tmp_path):
    """The alignment invariant (R1 #1540): context_tokens ==
    _estimate_tokens(render_context(context_points, question_date)) exactly
    — no per-block int drift — with several blocks and the date header."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_ie_user_001")
        ingest_haystack(sdk, q, chunk_turns=2)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    max_context_tokens=3000)
        assert ret["context_point_count"] > 0
        text = render_context(ret["context_points"],
                              question_date=q.get("question_date") or None)
        assert ret["context_tokens"] == _estimate_tokens(text)
        assert ret["context_tokens"] <= 3000
    finally:
        sdk.close()


def test_context_oversized_hit_skips_not_starves():
    """Skip-and-continue (R1 #1540): a mid-rank hit whose own cost exceeds
    the cap is DROPPED; later hits still append (no starvation)."""
    pool = [
        {"id": "pt1", "content": "point one", "point_kind": "statement",
         "lme_session_index": 0},
        {"id": "huge", "content": " ".join(["word"] * 5000),
         "point_kind": "statement", "lme_session_index": 0},
        {"id": "pt2", "content": "point two", "point_kind": "statement",
         "lme_session_index": 0},
    ]
    ctx = _assemble_context(pool, top_k=20, max_context_tokens=100)
    ids = [h["id"] for h in ctx]
    assert "huge" not in ids
    assert "pt1" in ids and "pt2" in ids  # later hits still selected
    assert len(ctx) == 2


def test_evidence_denominator_points_only(tmp_path, monkeypatch):
    """D5 (R1 #1540): a question with containment-marked chunks but no
    marked extracted points → evidence_recall@k is N/A (None) while
    chunk_evidence_recall@k is real and non-vacuous — the denominator split
    removes the granularity-bias confound."""
    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    def _fake(model, conversation, **kw):
        # the extractor produces NOTHING — only the containment-marked
        # chunks carry evidence
        return {"payload": {"entities": [], "events": [], "points": [],
                            "operators": []},
                "minted_kinds": [], "supersessions": [], "errors": [],
                "warnings": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake)
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_ie_user_001")
        ingest_haystack_v2(sdk, q, model=object(), chunk_turns=2)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20)
        # no extracted (non-chunk) marked points → N/A, never a forced 0.0
        assert ret["evidence_recall@k"]["5"] is None
        # the raw-chunk containment view is real: the evidence chunk (s1:c1
        # contains "My favorite board game is Catan.") surfaces in top-5
        assert isinstance(ret["chunk_evidence_recall@k"]["5"], float)
        assert ret["chunk_evidence_recall@k"]["5"] > 0.0
    finally:
        sdk.close()


def test_degenerate_knobs_raise():
    """R6 (R1 #1540): degenerate knob values raise at the function
    boundary — never a silent run (a 0 chunk cap deletes the raw leg; a 0
    context cap empties the reader context)."""
    with pytest.raises(ValueError, match="max_chunks_per_session"):
        _dedup_pool([], max_chunks_per_session=0)
    with pytest.raises(ValueError, match="max_context_tokens"):
        _assemble_context([], top_k=20, max_context_tokens=0)


# ── R1 #1540: read-path wiring (D6) + knobs (D7) ──────────────────────────

class _RecordingReader(MockReader):
    """Captures run_evaluation's reader.answer kwargs."""

    def __init__(self):
        super().__init__()
        self.calls: list[dict] = []

    def answer(self, **kw):
        self.calls.append(dict(kw))
        return super().answer(**kw)


def test_reader_receives_capped_context(tmp_path):
    """D6 (R1 #1540): the reader consumes EXACTLY the budget-capped
    points-first context (ret["context_points"]), not the full pool — and
    the rendered token estimate stays under the cap."""
    reader = _RecordingReader()
    q = _mini()[0]
    run_evaluation(
        [q], reader=reader, judge=MockJudge(), ks=(5, 10, 20), top_k=20,
        split="s", work_dir=str(tmp_path), max_context_tokens=3000,
    )
    assert len(reader.calls) == 1
    context_hits = reader.calls[0]["context_hits"]
    assert context_hits  # evidence reaches the reader
    text = render_context(context_hits, question_date=q.get("question_date") or None)
    assert _estimate_tokens(text) <= 3000
    # points-first: no raw chunk precedes an extracted point
    first_chunk = next(
        (i for i, h in enumerate(context_hits) if _is_raw_chunk(h)),
        len(context_hits))
    assert all(not _is_raw_chunk(h) for h in context_hits[:first_chunk])
    # an identical fresh ingest reproduces the retrieval contract (the
    # pipeline is deterministic: embedded TF-IDF + mocked reader/judge)
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q, chunk_turns=2)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    max_context_tokens=3000)
        assert [h["id"] for h in context_hits] == \
            [h["id"] for h in ret["context_points"]]
    finally:
        sdk.close()


def test_knob_cli_flags(tmp_path, monkeypatch):
    """R1 knobs thread through the CLI into ingest + methodology (the
    report does not carry per-question ingest stats — assert via the
    captured kwarg + methodology)."""
    import tools.longmem_eval.run as run_mod
    from tools.longmem_eval.ingest import ingest_haystack as _orig_ingest

    captured = {}

    def _capture(sdk, question, **kw):
        captured["chunk_turns"] = kw.get("chunk_turns")
        return _orig_ingest(sdk, question, **kw)

    monkeypatch.setattr(run_mod, "ingest_haystack", _capture)
    out = tmp_path / "report.json"
    report = run_main(["--data", str(MINI), "--limit", "1", "--split", "s",
                       "--mock", "--chunk-turns", "4", "--context-cap", "5000",
                       "--max-chunks-per-session", "1", "--output", str(out)])
    assert captured["chunk_turns"] == 4
    m = report["methodology"]
    assert m["chunk_turns"] == 4
    assert m["context_token_cap"] == 5000
    assert m["max_chunks_per_session"] == 1


def test_knob_env_vars(tmp_path, monkeypatch):
    """The TORTOISE_LME_* env surface works without CLI flags (mirrors the
    --reader-model/env pattern)."""
    monkeypatch.setenv("TORTOISE_LME_CHUNK_TURNS", "3")
    monkeypatch.setenv("TORTOISE_LME_CONTEXT_CAP", "6000")
    monkeypatch.setenv("TORTOISE_LME_MAX_CHUNKS_PER_SESSION", "1")
    report = run_main(["--data", str(MINI), "--limit", "1", "--split", "s",
                       "--mock"])
    m = report["methodology"]
    assert m["chunk_turns"] == 3
    assert m["context_token_cap"] == 6000
    assert m["max_chunks_per_session"] == 1


def test_knob_cli_validation():
    """Degenerate knob values are rejected at the CLI (argparse type guard)
    with a non-zero exit and a clear message — never a silent run."""
    import subprocess
    import sys

    base = [sys.executable, "-m", "tools.longmem_eval.run", "--data",
            str(MINI), "--limit", "1", "--mock"]
    for flag, bad in (("--chunk-turns", "0"), ("--chunk-turns", "-1"),
                      ("--max-chunks-per-session", "0"), ("--context-cap", "0")):
        r = subprocess.run(
            [*base, flag, bad], capture_output=True, text=True,
            cwd=str(Path(__file__).parent.parent))
        assert r.returncode != 0, f"{flag} {bad} must be rejected"
        assert flag in r.stderr, f"{flag} {bad}: missing clear message"


def test_report_methodology_records_r1_knobs():
    """D7 (R1 #1540): outcomes_to_report methodology carries the three knob
    values + the updated retrieval/recall_definition/reader_context_format
    strings."""
    outcomes = [{
        "question_id": "q-knob-1",
        "question_type": "single-session-user",
        "question_date": "2024-01-15",
        "label": True,
        "hypothesis": "h",
        "session_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
        "turn_recall@k": {"5": 0.5, "10": 0.5, "20": 0.5},
        "evidence_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
        "chunk_evidence_recall@k": {"5": 0.5, "10": 0.5, "20": 0.5},
        "n_ingest_errors": 0,
        "context_tokens": 120,
        "context_point_count": 3,
        "retrieval_latency_ms": 11.0,
        "reader_latency_ms": 22.0,
        "judge_latency_ms": 33.0,
        "total_ms": 66.0,
    }]
    report = outcomes_to_report(
        outcomes, reader_model="r", judge_model="j", ks=(5, 10, 20),
        top_k=20, split="s",
        r1_knobs={"chunk_turns": 2, "context_token_cap": 8000,
                  "max_chunks_per_session": 2},
        dataset_semantics_audit=_trusted_audit())
    m = report["methodology"]
    assert m["chunk_turns"] == 2
    assert m["context_token_cap"] == 8000
    assert m["max_chunks_per_session"] == 2
    assert "turn-granular raw chunks" in m["retrieval"]
    assert "DEDUPED" in m["recall_definition"]
    assert "chunk_evidence_recall@k" in m["recall_definition"]
    assert "points-first" in m["reader_context_format"]
    assert "chunk_turns" in m["extraction_approach"]


def test_r1_knobs_threaded_dispatch(tmp_path):
    """The --workers > 1 path with R1 knobs: every question completes
    exactly once (no duplicates/losses), and the checkpoint written during
    the run loads back and resumes cleanly."""
    cp = tmp_path / "state.json"
    kwargs = dict(reader=MockReader(), judge=MockJudge(), ks=(5,), top_k=5,
                  split="s", work_dir=str(tmp_path), workers=4,
                  chunk_turns=1, max_context_tokens=4000,
                  max_chunks_per_session=1, checkpoint=str(cp))
    outcomes, report = run_evaluation(_mini()[:2], **kwargs)
    assert len(outcomes) == 2
    assert len({o["question_id"] for o in outcomes}) == 2  # no dupes/losses
    assert report["n_failed"] == 0
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert len(saved["outcomes"]) == 2
    # resume: completed questions are skipped and continue cleanly (thread
    # completion order is nondeterministic — compare as sets)
    outcomes2, _ = run_evaluation(_mini()[:2], **kwargs)
    assert {o["question_id"] for o in outcomes2} == \
        {o["question_id"] for o in outcomes}
    by_qid = {o["question_id"]: o for o in outcomes2}
    for o in outcomes:
        assert by_qid[o["question_id"]]["label"] == o["label"]


# ── R1 #1540: granularity sweep micro-test (run protocol step 2) ──────────

def test_granularity_sweep_ci(tmp_path, monkeypatch):
    """Run protocol step 2 (R1 #1540): the 3-point sweep (chunk_turns
    {1,2,4}) completes in v2 mode with a mocked extractor over the mini
    fixture, respects the context cap, holds per-session dedup, and selects
    the same deterministic winner on consecutive runs."""
    import hashlib

    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.sweep_granularity import run_sweep

    def _fake_extract(model, conversation, **kw):
        # each session's extracted point CONTAINS the session verbatim → the
        # evidence marks are non-vacuous and the selection metric is
        # actually knob-responsive (points + chunks compete for the pool)
        content = " ".join(t["content"] for t in conversation)
        pid = "pt_" + hashlib.sha256(content.encode()).hexdigest()[:12]
        return {"payload": {"entities": [], "events": [],
                            "points": [{"id": pid, "content": content,
                                        "pointKind": "statement"}],
                            "operators": []},
                "minted_kinds": [], "supersessions": [], "errors": [],
                "warnings": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)

    def _run():
        return run_sweep(
            _mini(), chunk_turns_values=(1, 2, 4), context_cap=8000,
            max_chunks_per_session=2, ingest_mode="v2",
            reader=MockReader(), judge=MockJudge(),
            ks=(5, 10, 20), work_dir=str(tmp_path),
        )

    results, winner = _run()
    assert [r["chunk_turns"] for r in results] == [1, 2, 4]
    assert all(r["n_completed"] == 5 for r in results)
    assert all(r["context_tokens_mean"] <= 8000 for r in results)
    assert winner is not None
    assert winner["chunk_turns"] in (1, 2, 4)
    # determinism: two consecutive runs select the same winner
    _, winner2 = _run()
    assert winner2["chunk_turns"] == winner["chunk_turns"]
    # per-session dedup holds across sweep granularities: on a fresh ingest
    # at chunk_turns=1 (3 chunks per 3-turn session) the pool keeps ≤ 2
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_msr_002")
        ingest_haystack(sdk, q, chunk_turns=1)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    max_chunks_per_session=2)
        per_session = {}
        for h in ret["hits"]:
            if _is_raw_chunk(h):
                per_session[h["session_id"]] = per_session.get(h["session_id"], 0) + 1
        assert all(v <= 2 for v in per_session.values())
    finally:
        sdk.close()


# ── R1 #1540: E2E-1/E2E-10 integration scenarios + robustness ─────────────

def test_e2e1_dedup_cap_assertion(tmp_path):
    """E2E-1 (R1 #1540): an 8-turn single session cannot monopolize the
    pool or the context — per-session chunk count ≤ max_chunks_per_session
    in BOTH ret["hits"] (the pool) and context_points, while the answer
    session's recall is preserved (crowd-out guard)."""
    q = {
        "question_id": "monopoly_q",
        "question_type": "single-session-user",
        "question": "What is the name of the cat?",
        "answer": "Whiskers",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["mono-s0", "mono-s1"],
        "haystack_dates": ["2025-06-10", "2025-06-12"],
        "answer_session_ids": ["mono-s1"],
        "haystack_sessions": [
            [{"role": "user", "content": f"filler topic {i} details",
              "has_answer": False} for i in range(8)],
            [{"role": "user", "content": "the cat is named Whiskers",
              "has_answer": True}],
        ],
    }
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q, chunk_turns=2)  # s0 → 4 chunks
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    max_chunks_per_session=2)

        def _chunks_of(hits, sid):
            return [h for h in hits
                    if _is_raw_chunk(h) and h["session_id"] == sid]

        assert len(_chunks_of(ret["hits"], "mono-s0")) <= 2
        assert len(_chunks_of(ret["context_points"], "mono-s0")) <= 2
        # the answer session still surfaces (pool depth + dedup)
        assert ret["session_recall@k"]["20"] == 1.0
        assert ret["session_recall@k"]["5"] >= 0.5
    finally:
        sdk.close()


def test_e2e10_budget_capped_context_v3_part(tmp_path):
    """E2E-10 V3 part (R1 #1540): many near-duplicate raw chunks from one
    session cannot blow the reader's context budget — the context stays ≤
    cap, points render before chunks, and context_tokens is honest.
    (Cross-encoder/MMR assertions remain V4-conditional — not asserted.)"""
    filler = [{"role": "user", "content": f"planning detail number {i} "
               "about the upcoming trip itinerary", "has_answer": False}
              for i in range(9)] + [
        {"role": "user", "content": "the destination is Kyoto",
         "has_answer": True}]
    q = {
        "question_id": "dupe_q",
        "question_type": "single-session-user",
        "question": "what is the destination",
        "answer": "Kyoto",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["dupe-s0"],
        "haystack_dates": ["2025-06-10"],
        "answer_session_ids": ["dupe-s0"],
        "haystack_sessions": [filler],
    }
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q, chunk_turns=2)  # 5 near-duplicate chunks
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20,
                                    max_context_tokens=2000)
        assert ret["context_tokens"] <= 2000
        # points-first ordering
        first_chunk = next(
            (i for i, h in enumerate(ret["context_points"]) if _is_raw_chunk(h)),
            len(ret["context_points"]))
        assert all(not _is_raw_chunk(h)
                   for h in ret["context_points"][:first_chunk])
        # honest token accounting
        assert ret["context_tokens"] == _estimate_tokens(
            render_context(ret["context_points"],
                           question_date=q.get("question_date") or None))
        # per-session dedup holds even with 5 near-duplicate chunks
        dupe_chunks = [h for h in ret["hits"] if _is_raw_chunk(h)]
        assert len(dupe_chunks) <= 2
        assert ret["dedup_stats"]["chunks_capped"] >= 3
    finally:
        sdk.close()


def test_context_cap_holds_under_pathological_content(tmp_path):
    """Estimator-limitation guard (R1 #1540): a long whitespace-free turn
    (URL/base64 — undercounted by the word-split estimator) cannot silently
    reproduce the whole-session flood: the estimate-space cap holds, the
    rendered context is strictly smaller than the full transcript, and the
    estimator limitation is recorded in the methodology."""
    # one 8000-char no-whitespace token (ONE estimate "word") + 100 wordy
    # filler turns (4000 estimate words — the cap would trim them)
    pathological = "x" * 8000
    filler = [{"role": "user",
               "content": " ".join(f"word{i}" for i in range(40)),
               "has_answer": False} for _ in range(100)]
    session = [{"role": "user", "content": pathological}, *filler]
    q = {
        "question_id": "patho_q",
        "question_type": "single-session-user",
        "question": "what is the key fact",
        "answer": "x",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["patho-s0"],
        "haystack_dates": ["2025-06-10"],
        "answer_session_ids": ["patho-s0"],
        "haystack_sessions": [session],
    }
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q, chunk_turns=2)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20,
                                    max_context_tokens=2000)
        text = render_context(ret["context_points"],
                              question_date=q.get("question_date") or None)
        assert ret["context_tokens"] <= 2000  # estimate-space cap holds
        full = _session_transcript(session)
        # the whole-session flood is NOT reproduced (the estimator
        # undercounts the no-whitespace token — one chunk slips in — but the
        # wordy tail is trimmed; real length stays bounded)
        assert len(text) < len(full)
        assert len(text) < 50_000
        assert ret["context_point_count"] > 0  # not starved
        # the estimator limitation is documented in the report methodology
        report = outcomes_to_report(
            [{
                "question_id": "q-p", "question_type": "single-session-user",
                "question_date": "2025-06-15", "label": True,
                "hypothesis": "h",
                "session_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
                "turn_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
                "evidence_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
                "chunk_evidence_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
                "n_ingest_errors": 0, "context_tokens": 100,
                "context_point_count": 1, "retrieval_latency_ms": 1.0,
                "reader_latency_ms": 1.0, "judge_latency_ms": 1.0,
                "total_ms": 1.0,
            }],
            reader_model="r", judge_model="j", ks=(5,), top_k=20, split="s",
            dataset_semantics_audit=_trusted_audit())
        assert "whitespace" in report["methodology"]["token_estimator"]
    finally:
        sdk.close()


def test_ingest_over_mixed_blob_chunk_graph(tmp_path):
    """Defensive (R1 #1540): a stale :raw blob under a session is treated
    as a raw chunk by retrieval (kind-based _is_raw_chunk) — no double
    representation of the verbatim leg beyond the cap — and new ingest
    writes chunks without touching it (fresh per-question graphs make this
    unreachable in production, but stats must reflect written chunks)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_ie_user_001")
        proj = sdk._get_proj()
        # simulate a pre-R1 graph: a stale :raw blob under session s0
        proj.g.query(
            "MERGE (s:Session {id:'lme:mini_ie_user_001:s0'}) "
            "SET s.lme_question_id='mini_ie_user_001', s.lme_session_index=0",
            params={})
        sdk.create_point("session-transcript", "stale whole-session blob",
                         id="lme:mini_ie_user_001:s0:raw",
                         session_id="mini-s0", lme_question_id="mini_ie_user_001",
                         lme_session_index=0, is_episodic=True, status="draft")
        stats = ingest_haystack(sdk, q, chunk_turns=2)
        assert stats["chunks"] == 4  # fresh chunks written, stats honest
        rows = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN count(*)",
            params={"id": "lme:mini_ie_user_001:s0:raw"}).result_set
        assert rows[0][0] == 1  # stale blob untouched (defensive)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20)
        # the stale blob is deduped WITH the fresh chunks (same session —
        # kind-based detection, no double representation beyond the cap)
        s0_chunks = [h for h in ret["hits"]
                     if _is_raw_chunk(h) and h["session_id"] == "mini-s0"]
        assert len(s0_chunks) <= 2
    finally:
        sdk.close()


# ── M7 #1527: run hygiene — python guard, fingerprint, flock, instrumentation ─

def test_python_guard_refuses_lt_312(monkeypatch):
    """D9 (M7 #1527): run entry refuses Python < 3.12 with a clear message
    (pyproject requires-python >=3.12; the eval graph write path is
    3.12-only) and accepts 3.12+."""
    import tools.longmem_eval.run as run_mod
    monkeypatch.setattr(run_mod.sys, "version_info", (3, 11, 9))
    with pytest.raises(SystemExit, match=r"3\.12"):
        _assert_python_version()
    monkeypatch.setattr(run_mod.sys, "version_info", (3, 12, 0))
    _assert_python_version()  # passes
    monkeypatch.setattr(run_mod.sys, "version_info", (3, 13, 1))
    _assert_python_version()


def test_report_methodology_env_fields(tmp_path):
    """D7/D9 (M7 #1527): methodology records python_version / workers /
    dataset_fingerprint — a report always says what code produced it."""
    _, report = run_evaluation(
        _mini()[:2], reader=MockReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path), workers=4,
        dataset_fingerprint="abc123def4567890")
    m = report["methodology"]
    assert m["python_version"].startswith("3.12")
    assert m["workers"] == 4
    assert m["dataset_fingerprint"] == "abc123def4567890"
    # programmatic default is "unknown" (stable within a run)
    _, report2 = run_evaluation(
        _mini()[:2], reader=MockReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path))
    assert report2["methodology"]["dataset_fingerprint"] == "unknown"
    assert report2["methodology"]["workers"] == 1


def test_checkpoint_fingerprint_refuses_stale_resume(tmp_path):
    """D7 (M7 #1527, E2E-2 owned negative): a resume with a different
    effective config raises CheckpointStaleError naming the differing
    fields — stale results are never silently reused."""
    cp = tmp_path / "lme-state.json"
    base = dict(reader=MockReader(), judge=MockJudge(), ks=(5,), top_k=5,
                split="s", work_dir=str(tmp_path), checkpoint=str(cp))
    run_evaluation(_mini()[:2], **base)

    class _OtherReader(MockReader):
        model_id = "other-reader"

    with pytest.raises(CheckpointStaleError, match="reader_model"):
        run_evaluation(_mini()[:2], **dict(base, reader=_OtherReader()))
    with pytest.raises(CheckpointStaleError, match="top_k"):
        run_evaluation(_mini()[:2], **dict(base, top_k=10))
    with pytest.raises(CheckpointStaleError, match="max_retries"):
        run_evaluation(_mini()[:2], **dict(base, max_retries=1))
    with pytest.raises(CheckpointStaleError, match="ks"):
        run_evaluation(_mini()[:2], **dict(base, ks=(5, 10)))
    with pytest.raises(CheckpointStaleError, match="dataset_fingerprint"):
        run_evaluation(_mini()[:2], **dict(
            base, dataset_fingerprint="deadbeefcafe1234"))


def test_checkpoint_fingerprint_legacy_refused(tmp_path):
    """D7 (M7 #1527): a legacy v1 checkpoint (no fingerprint key) is refused
    with a clear message — the file predates the fingerprint contract."""
    cp = tmp_path / "legacy.json"
    cp.write_text(json.dumps({"outcomes": [], "failures": []}),
                  encoding="utf-8")
    with pytest.raises(CheckpointStaleError, match="fingerprint"):
        run_evaluation(_mini()[:2], reader=MockReader(), judge=MockJudge(),
                       ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
                       checkpoint=str(cp))


def test_checkpoint_fingerprint_matching_resumes(tmp_path):
    """D7 (M7 #1527): identical kwargs (incl. dataset_fingerprint) resume
    cleanly — the fingerprint is part of the checkpoint file itself."""
    cp = tmp_path / "lme-state.json"
    kwargs = dict(reader=MockReader(), judge=MockJudge(), ks=(5,), top_k=5,
                  split="s", work_dir=str(tmp_path), checkpoint=str(cp),
                  dataset_fingerprint="samehash0000000000")
    outcomes, _ = run_evaluation(_mini()[:2], **kwargs)
    assert len(outcomes) == 2
    outcomes2, report2 = run_evaluation(_mini()[:2], **kwargs)
    assert {o["question_id"] for o in outcomes2} == \
        {o["question_id"] for o in outcomes}
    assert report2["n_failed"] == 0
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert saved["fingerprint"]["dataset_fingerprint"] == "samehash0000000000"


def test_checkpoint_two_processes_no_lost_updates(tmp_path, monkeypatch):
    """D8 (M7 #1527, surface 20): two run PROCESSES sharing one checkpoint
    merge their results under the flock — no lost updates (each process runs
    a disjoint half of the mini set; the final checkpoint holds all 5 qids).
    POSIX-only (flock); uses fork so the child inherits the module state.

    R3 (#1542) Task 5: the children pin EmbeddingModel.get → None BEFORE
    forking (fork inherits the monkeypatch) — the vector-enabled eval env
    has torch installed (sentence-transformers), and forking a process that
    imports torch/MPSGraph on macOS crashes (objc_initializeAfterForkError).
    This test exercises checkpoint merging, not retrieval — the dense leg
    is out of scope by construction.
    """
    import multiprocessing as mp
    ctx = mp.get_context("fork")
    _no_embedder(monkeypatch)  # children inherit — no torch load in fork
    cp = str(tmp_path / "shared-state.json")
    instances = _mini()
    half = len(instances) // 2  # 2 (split 2/3)
    split_a, split_b = instances[:half], instances[half:]

    def _worker(slice_instances, out_q):
        try:
            outs, _ = run_evaluation(
                slice_instances, reader=MockReader(), judge=MockJudge(),
                ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
                checkpoint=cp)
            out_q.put([o["question_id"] for o in outs])
        except Exception as e:
            out_q.put(f"ERR:{e!r}")

    qa, qb = ctx.Queue(), ctx.Queue()
    pa = ctx.Process(target=_worker, args=(split_a, qa))
    pb = ctx.Process(target=_worker, args=(split_b, qb))
    pa.start()
    pb.start()
    pa.join(180)
    pb.join(180)
    assert pa.exitcode == 0 and pb.exitcode == 0
    got_a, got_b = qa.get(timeout=30), qb.get(timeout=30)
    assert not str(got_a).startswith("ERR"), got_a
    assert not str(got_b).startswith("ERR"), got_b
    assert len(got_a) + len(got_b) == 5
    final = json.loads(Path(cp).read_text(encoding="utf-8"))
    assert len(final["outcomes"]) == 5, \
        f"lost updates: {sorted(o['question_id'] for o in final['outcomes'])}"
    assert {o["question_id"] for o in final["outcomes"]} == \
        {q["question_id"] for q in instances}


def test_retrieval_leg_mix_and_evidence_counts(tmp_path, monkeypatch):
    """D2/D4 (M7 #1527, surface 11): retrieve_for_question reports the
    per-hit match_source aggregation (leg-mix) and the evidence_retrieved@k
    counts (the turn_recall numerator, persisted). R3 (#1542) Task 5: pinned
    to the sparse leg (EmbeddingModel.get → None) so the TF-IDF leg-mix
    assertion is deterministic in the vector-enabled eval env."""
    q = _mini()[0]  # mini_ie_user_001 — 1 evidence turn in session s1
    sdk = _fresh_sdk(tmp_path)
    try:
        _no_embedder(monkeypatch)
        ingest_haystack(sdk, q, chunk_turns=2)
        ret = retrieve_for_question(sdk, q, ks=(5, 10, 20), top_k=20)
        lm = ret["match_source_counts"]
        assert sum(lm.values()) == len(ret["context_points"])
        assert "tfidf" in lm  # embedded sparse path: the TF-IDF degradation leg
        assert "unknown" not in lm  # match_source is never "" on hits
        for k in (5, 10, 20):
            at_k = ret["match_source_counts@k"][str(k)]
            assert sum(at_k.values()) == min(k, len(ret["hits"]))
        er = ret["evidence_retrieved@k"]
        assert er["5"] >= 1  # the evidence turn surfaces in top-5
        assert er["5"] <= er["20"]
        assert er["20"] <= sum(1 for t in q["haystack_sessions"][1]
                               if t.get("has_answer"))  # 1 evidence turn
    finally:
        sdk.close()


def test_outcome_instrumentation_fields(tmp_path):
    """D2–D5 (M7 #1527): every outcome carries the six instrumentation keys;
    pool_size is the authoritative live graph point count (turns + chunks);
    evidence_written == ingest's evidence_turns (deterministic leg);
    ingest_latency_ms isolates the write-path cost."""
    outcomes, report = run_evaluation(
        _mini(), reader=MockReader(), judge=MockJudge(),
        ks=(5, 10, 20), top_k=20, split="s", work_dir=str(tmp_path))
    assert len(outcomes) == 5
    for o in outcomes:
        for key in ("valid", "error_classes", "leg_mix", "leg_mix@k",
                    "pool_size", "evidence_written", "evidence_retrieved@k",
                    "ingest_latency_ms"):
            assert key in o, f"outcome {o['question_id']} missing {key}"
        # pool_size == turns + chunks written for THIS question (authoritative)
        assert o["pool_size"] == o["ingest"]["turns"] + o["ingest"]["chunks"]
        assert o["evidence_written"] == o["ingest"]["evidence_turns"]
        assert o["ingest_latency_ms"] > 0
        assert o["valid"] is True
        assert o["error_classes"] == {}  # M4 #1524: census dict (deterministic → empty)
    # the 2-session mini question: 6 turn points + 4 raw chunks (chunk_turns
    # default 2 → ceil(3/2)=2 windows per 3-turn session)
    by_qid = {o["question_id"]: o for o in outcomes}
    assert by_qid["mini_ie_user_001"]["pool_size"] == 10
    # report aggregates exist
    assert report["leg_mix"]["n_questions"] == 5
    assert report["pool_size"]["mean"] > 0
    assert report["evidence"]["evidence_bearing_n"] == 4
    assert report["evidence"]["evidence_absent_n"] == 1  # mini_abs
    assert report["latency_ms"]["ingest"]["mean_ms"] > 0


def test_integrity_block_and_error_census(tmp_path):
    """D1/D6 (M7 #1527, E2E-2): a run with failing questions is
    integrity.valid=false with the real numbers (invalid_rate, per-question
    error census) recorded — no degraded run can masquerade as clean."""
    class _ExplodingReader(MockReader):
        def answer(self, **kw):
            raise RuntimeError("transient provider boom")

    _, report = run_evaluation(
        _mini()[:2], reader=_ExplodingReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path), max_retries=0)
    integ = report["integrity"]
    assert integ["valid"] is False
    assert integ["threshold"] == 0.0
    assert integ["n_attempted"] == 2
    assert integ["n_valid"] == 0
    assert integ["n_invalid"] == 2
    assert integ["invalid_rate"] == 1.0
    # RuntimeError at the reader with 0 retries → reader:retries_exhausted
    assert integ["error_census"] == {"reader:retries_exhausted": 2}
    assert any("error census" in c for c in integ["checks"])


def test_integrity_threshold_override_recorded(tmp_path):
    """D1 (M7 #1527): a justified threshold override is recorded with its
    free text; a VIOLATED override still yields valid=false (numbers + reason
    always published)."""
    class _ExplodingReader(MockReader):
        def answer(self, **kw):
            raise RuntimeError("boom")

    _, report = run_evaluation(
        _mini()[:2], reader=_ExplodingReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path), max_retries=0,
        integrity_threshold=0.5,
        integrity_justification="known provider incident, will re-run")
    integ = report["integrity"]
    assert integ["threshold"] == 0.5
    assert integ["valid"] is False  # 1.0 > 0.5 — violated override is honest
    assert integ["justified"] is True
    assert "provider incident" in integ["threshold_violation_justification"]
    assert report["methodology"]["integrity_threshold"] == 0.5


def test_integrity_prints_before_score(tmp_path, capsys):
    """M4/M7 (D1): _print_summary prints the integrity block + error census
    BEFORE the accuracy score — a run's validity is asserted before its
    numbers are read."""
    class _ExplodingReader(MockReader):
        def answer(self, **kw):
            raise RuntimeError("boom")

    _, report = run_evaluation(
        _mini()[:2], reader=_ExplodingReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path), max_retries=0)
    _print_summary(report)
    out = capsys.readouterr().out
    assert "── integrity ──" in out
    assert "error census" in out
    assert out.index("── integrity ──") < out.index("overall accuracy")


# ── M4 (#1524): per-question valid flag + error census ─────────────────────

class _HTTPError429(Exception):
    """Duck-typed 429 (no hard requests import in tests)."""

    def __init__(self):
        super().__init__("rate limited")
        self.response = type("R", (), {"status_code": 429})()


class _FlakyExtractor:
    """A v2 extractor model whose S2/S4 always transient-fail (429) — S1
    narrative is fine, so the session pipeline completes with errors."""

    def __init__(self):
        self.last_finish_reason = None

    def complete(self, *, system, user, max_tokens=None):
        if "STORY SUMMARIZER" in system:
            return "The session revealed a new strategy."
        raise _HTTPError429()


def _no_extractor_sleep(monkeypatch):
    """Patch ONLY extractor_v2's ``time`` reference (its retry-loop sleep) —
    never the global time module: redislite's embedded server-start readiness
    poll needs real ``time.sleep`` (a global patch breaks the graph startup)."""
    import contextlib
    import types

    real = v2.time
    shim = types.ModuleType("extractor_time")
    for _name in dir(real):
        if _name.startswith("__"):
            continue
        with contextlib.suppress(Exception):  # skip un-copyable attrs
            setattr(shim, _name, getattr(real, _name))
    shim.sleep = lambda _seconds: None
    monkeypatch.setattr(v2, "time", shim)


def test_ingest_v2_rolls_census_and_valid(tmp_path, monkeypatch):
    """M4 (D4): ingest_haystack_v2 rolls each session's extractor
    error_census into stats['error_census'] and CLASSIFIES session-level
    exceptions into the same granular vocabulary."""
    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    def _fake_extract(model, conversation, **kw):
        if conversation and conversation[0].get("content") == "boom":
            raise RuntimeError("session boom")
        return {"payload": {}, "minted_kinds": [], "supersessions": [],
                "errors": ["S2 failed: HTTP 429"], "warnings": [],
                "error_census": {"transient_429_rate_limit": 2}}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)

    sdk = _fresh_sdk(tmp_path)
    try:
        question = {
            "question_id": "census_q",
            "haystack_session_ids": ["s0", "s1"],
            "haystack_dates": ["", ""],
            "haystack_sessions": [
                [{"role": "user", "content": "boom"}],
                [{"role": "user", "content": "fine"}],
            ],
        }
        stats = ingest_haystack_v2(sdk, question, model=object(), chunk_turns=2)
        # session-level exception → granular class; extractor census rolled up
        assert stats["error_census"]["transient_unknown"] == 1
        assert stats["error_census"]["transient_429_rate_limit"] == 2
        assert len(stats["errors"]) == 2  # boom + S2-failed string
    finally:
        sdk.close()


def test_outcome_valid_and_error_classes(tmp_path, monkeypatch):
    """M4 (D4): the per-question outcome carries valid + the granular error
    census; an extraction-invalid run reports valid=false with the census
    before the score (E2E-2 offline analog)."""
    _no_extractor_sleep(monkeypatch)
    outcomes, report = run_evaluation(
        _mini()[:1], reader=MockReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
        ingest_mode="v2", extractor_model=_FlakyExtractor(),
    )
    assert len(outcomes) == 1
    o = outcomes[0]
    assert o["valid"] is False
    assert o["error_classes"]["transient_429_rate_limit"] >= 1
    integ = report["integrity"]
    assert integ["valid"] is False
    assert integ["invalid_rate"] == 1.0
    assert integ["error_census"]["transient_429_rate_limit"] >= 1
    assert integ["n_failed"] == 0  # completed-but-invalid ≠ question failure


def test_checkpoint_roundtrip_preserves_valid(tmp_path, monkeypatch):
    """M4 (D4): valid + error_classes survive the checkpoint round-trip
    (JSON-generic save/load — a resumed run reuses them)."""
    _no_extractor_sleep(monkeypatch)
    cp = tmp_path / "lme-census.json"
    run_evaluation(
        _mini()[:1], reader=MockReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
        checkpoint=str(cp), ingest_mode="v2",
        extractor_model=_FlakyExtractor(),
    )
    saved = json.loads(cp.read_text(encoding="utf-8"))
    assert saved["outcomes"][0]["valid"] is False
    assert saved["outcomes"][0]["error_classes"]["transient_429_rate_limit"] >= 1


# ── Task 7 (#1524): _call_with_backoff fatal-class awareness (M2 pin) ──────

def test_backoff_skips_fatal_4xx(monkeypatch):
    """A fatal-class error (401/402/403 per the P2 taxonomy) is raised
    immediately by _call_with_backoff — no retry, no backoff sleep. Pinned
    so the harness's fatal-class awareness cannot regress."""
    from tools.longmem_eval.run import _call_with_backoff

    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    class _HTTPError(Exception):
        def __init__(self, status):
            super().__init__(f"HTTP {status}")
            self.response = type("R", (), {"status_code": status})()

    calls = {"n": 0}

    def _boom():
        calls["n"] += 1
        raise _HTTPError(401)

    with pytest.raises(_HTTPError):
        _call_with_backoff(_boom, what="test", retries=3)
    assert calls["n"] == 1
    assert sleeps == []


def test_backoff_still_retries_transient(monkeypatch):
    """Transient classes keep the backoff path (existing behavior pinned)."""
    from tools.longmem_eval.run import _call_with_backoff

    monkeypatch.setattr("time.sleep", lambda _: None)
    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise _HTTPError429()
        return "ok"

    assert _call_with_backoff(_flaky, what="test", retries=2) == "ok"
    assert calls["n"] == 3
# ═══════════════════════════════════════════════════════════════════════════
# R3 #1542 — dense leg enabled: embedder pre-flight (D2), write-time
# embedding coverage (D3), vector-leg trace + "legs" (D4), methodology keys
# (D5), vector-strategy verification in the eval path (Task 5).
# ═══════════════════════════════════════════════════════════════════════════

# ── D2: embedder pre-flight (never silent None) ───────────────────────────

class _BrokenEncodeModel:
    """An embedder that loads but fails at encode time (present-but-broken —
    distinct from a missing embedder)."""
    model_id = "all-MiniLM-L6-v2"

    def encode(self, texts, batch_size=32, show_progress_bar=False):
        raise RuntimeError("encode boom")


class _WrongDimModel:
    """An embedder that returns a non-384-dim vector (a swap must be
    caught, not silently used — #399 calibration is model-specific)."""
    model_id = "all-MiniLM-L6-v2"

    def encode(self, texts, batch_size=32, show_progress_bar=False):
        import numpy as np
        return np.zeros((len(texts), 512))


def test_preflight_embedder_missing_real_run_exits(monkeypatch):
    """D2 (R3): EmbeddingModel.get() → None on a real (non-mock) run →
    SystemExit containing the exact remediation commands — the dense leg can
    never silently degrade."""
    from tools.longmem_eval.run import _preflight_embedder
    from tortoise.embeddings import EmbeddingModel
    monkeypatch.setattr(EmbeddingModel, "get",
                        staticmethod(lambda load_timeout=None: None))
    with pytest.raises(SystemExit) as ei:
        _preflight_embedder(mock=False)
    msg = str(ei.value)
    assert "--extra embeddings" in msg
    assert "all-MiniLM-L6-v2" in msg


def test_preflight_embedder_missing_mock_warns_and_continues(monkeypatch):
    """D2 (R3): --mock + missing embedder → warn + continue (status
    recorded, never a crash) — CI smoke stays runnable offline."""
    from tools.longmem_eval.run import _preflight_embedder
    from tortoise.embeddings import EmbeddingModel
    monkeypatch.setattr(EmbeddingModel, "get",
                        staticmethod(lambda load_timeout=None: None))
    status = _preflight_embedder(mock=True)  # must not raise
    assert status["available"] is False
    assert status["reason"] == "no_embedder"
    # the version field is well-formed — str when the extra is installed
    # (this env), null when absent — never an exception
    assert isinstance(status["sentence_transformers_version"],
                      (str, type(None)))


def test_preflight_embedder_present_probe_ok():
    """D2 (R3): embedder present + probe encode OK → the status dict carries
    model identity, the resolved sentence-transformers version and
    available=True / reason=None."""
    from tools.longmem_eval.run import _preflight_embedder
    _require_embedder()
    status = _preflight_embedder(mock=True)
    assert status["available"] is True
    assert status["reason"] is None
    assert status["model"] == "all-MiniLM-L6-v2"
    assert isinstance(status["sentence_transformers_version"], str)


def test_preflight_embedder_encode_raises(monkeypatch):
    """D2 (R3): a present-but-broken embedder (encode raises) →
    reason='encode_failed'; real runs exit, --mock warns and continues."""
    from tools.longmem_eval.run import _preflight_embedder
    from tortoise.embeddings import EmbeddingModel
    monkeypatch.setattr(EmbeddingModel, "get", staticmethod(
        lambda load_timeout=None: _BrokenEncodeModel()))
    with pytest.raises(SystemExit) as ei:
        _preflight_embedder(mock=False)
    assert "encode_failed" in str(ei.value)
    status = _preflight_embedder(mock=True)
    assert status["available"] is False
    assert status["reason"] == "encode_failed"


def test_preflight_embedder_wrong_dim(monkeypatch):
    """D2 (R3): a swapped/wrong-dimension model → reason='dim_mismatch' (a
    run can never publish vector_strategy=enabled with a broken dim)."""
    from tools.longmem_eval.run import _preflight_embedder
    from tortoise.embeddings import EmbeddingModel
    monkeypatch.setattr(EmbeddingModel, "get", staticmethod(
        lambda load_timeout=None: _WrongDimModel()))
    status = _preflight_embedder(mock=True)
    assert status["available"] is False
    assert status["reason"] == "dim_mismatch"


def test_preflight_embedder_mock_uses_short_timeout(monkeypatch):
    """D2 (R3): --mock probes with the short (30s) load timeout — an offline
    env without a cached model warns in ~30s, not 10 minutes."""
    from tools.longmem_eval.run import _preflight_embedder
    from tortoise.embeddings import EmbeddingModel
    seen: dict[str, float | None] = {}

    def _fake_get(*, load_timeout=None):
        seen["load_timeout"] = load_timeout
        return None

    monkeypatch.setattr(EmbeddingModel, "get", staticmethod(_fake_get))
    _preflight_embedder(mock=True)
    assert seen["load_timeout"] == 30.0


def test_report_methodology_embedder_keys_always_emitted():
    """D5 (R3): build_report ALWAYS emits methodology.embedder +
    methodology.vector_strategy — without embedder_status the not_checked
    default (a report is never keyless about the dense leg), with it the
    status flows through and vector_strategy flips to enabled."""
    from tools.longmem_eval.report import DEFAULT_EMBEDDER_STATUS

    def _outcome(qid):
        return {
            "question_id": qid, "question_type": "single-session-user",
            "label": True, "session_recall@k": {"5": 1.0},
            "turn_recall@k": {"5": 0.5}, "evidence_recall@k": {"5": 1.0},
            "chunk_evidence_recall@k": {"5": 0.5}, "n_ingest_errors": 0,
            "context_tokens": 10, "context_point_count": 1,
            "retrieval_latency_ms": 1.0, "reader_latency_ms": 1.0,
            "judge_latency_ms": 1.0, "total_ms": 3.0,
        }

    base = dict(reader_model="r", judge_model="j", ks=(5,), top_k=5,
                split="s", dataset_semantics_audit=_trusted_audit())
    m = outcomes_to_report([_outcome("q-e1")], **base)["methodology"]
    assert m["embedder"] == DEFAULT_EMBEDDER_STATUS
    assert m["vector_strategy"] == "unavailable"
    m2 = outcomes_to_report(
        [_outcome("q-e2")], **base,
        embedder_status={"model": "all-MiniLM-L6-v2",
                         "sentence_transformers_version": "5.7.0",
                         "available": True, "reason": None})["methodology"]
    assert m2["embedder"]["available"] is True
    assert m2["embedder"]["reason"] is None
    assert m2["vector_strategy"] == "enabled"


# ── D3: write-time embedding coverage (observable) ────────────────────────

def test_write_time_embeddings_land_on_points_and_chunks(tmp_path):
    """D3 (R3): write-time embeddings land on every point + chunk for the
    question — coverage == 1.0 and min embedding dim == 384 in the fresh
    graph protocol (the dense leg is observed, not assumed)."""
    _require_embedder()
    q = _mini()[0]  # mini_ie_user_001: 6 turn points + 4 raw chunks
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q, chunk_turns=2)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        assert ret["points_total"] == 10
        assert ret["points_embedded"] == 10
        assert ret["embedding_coverage"] == 1.0
        # dim check via get_point (size() is unsupported on vecf32 in
        # FalkorDBLite — the read-back property is a plain 384-list)
        dims = [len(p["embedding"]) for p in
                (sdk.get_point(f"lme:{q['question_id']}:s0:t0"),
                 sdk.get_point(f"lme:{q['question_id']}:s1:c1"))]
        assert all(d == 384 for d in dims)
    finally:
        sdk.close()


def test_write_time_embeddings_absent_without_embedder(tmp_path, monkeypatch):
    """D3 (R3): without the embedder coverage is 0.0 — observable, never
    silent — and the trace records the vector leg as no_embedder."""
    _no_embedder(monkeypatch)
    q = _mini()[0]
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q, chunk_turns=2)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        assert ret["points_total"] == 10
        assert ret["points_embedded"] == 0
        assert ret["embedding_coverage"] == 0.0
        vec = next(e for e in ret["legs"] if e["leg"] == "vector")
        assert vec["ran"] is False
        assert vec["reason"] == "no_embedder"
    finally:
        sdk.close()


def test_truncated_chunk_still_embedded(tmp_path):
    """D3 (R3): a >512-word point (compute_embedding truncates to 512 words)
    still embeds — embedding non-null, coverage stays 1.0 (pins the
    truncation branch)."""
    _require_embedder()
    long_text = " ".join(f"word{i}" for i in range(700))
    q = {
        "question_id": "mini_long_001",
        "question_type": "single-session-user",
        "question": "what is the fact?",
        "answer": "word0",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["long-s0"],
        "haystack_dates": ["2025-06-10"],
        "haystack_sessions": [[
            {"role": "user", "content": long_text, "has_answer": True}]],
        "answer_session_ids": ["long-s0"],
    }
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        assert ret["points_total"] == 2  # turn point + 1 raw chunk
        assert ret["points_embedded"] == 2
        assert ret["embedding_coverage"] == 1.0
    finally:
        sdk.close()


def test_empty_graph_question_shape(tmp_path):
    """D3/D4 (R3): a question with zero haystack sessions → zero Points →
    embedding_coverage is None (pinned shape, no crash) and the vector leg
    records {ran: True, count: 0, reason: 'no_embeddings'} (0-row + zero
    embedded points per the D4 pinned mapping — NOT empty_results, which is
    reserved for embedded-points-present-but-no-matches)."""
    _require_embedder()
    q = {
        "question_id": "mini_empty_001",
        "question_type": "single-session-user",
        "question": "nothing here",
        "answer": "",
        "question_date": "2025-06-15",
        "haystack_session_ids": [],
        "haystack_dates": [],
        "haystack_sessions": [],
        "answer_session_ids": [],
    }
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        assert ret["points_total"] == 0
        assert ret["embedding_coverage"] is None
        vec = next(e for e in ret["legs"] if e["leg"] == "vector")
        assert vec["ran"] is True
        assert vec["count"] == 0
        assert vec["reason"] == "no_embeddings"
        assert ret["session_recall@k"]["5"] == 0.0  # recall path no crash
    finally:
        sdk.close()


def test_checkpoint_resume_old_shape(tmp_path):
    """D3 (R3): a pre-R3 checkpoint (outcomes WITHOUT the coverage/legs keys)
    resumes without KeyError — the final report carries embedding_coverage:
    None / legs: [] defaults (visible, never silent, never a crash)."""
    cp = tmp_path / "pre-r3-state.json"
    kwargs = dict(reader=MockReader(), judge=MockJudge(), ks=(5,), top_k=5,
                  split="s", work_dir=str(tmp_path))
    run_evaluation(_mini()[:1], checkpoint=str(cp), **kwargs)
    saved = json.loads(Path(cp).read_text(encoding="utf-8"))
    for o in saved["outcomes"]:
        for k in ("points_total", "points_embedded", "embedding_coverage",
                  "legs"):
            o.pop(k, None)
    Path(cp).write_text(json.dumps(saved), encoding="utf-8")
    outcomes, report = run_evaluation(_mini()[:1], checkpoint=str(cp),
                                      **kwargs)
    assert len(outcomes) == 1  # resumed from the pre-R3 checkpoint
    po = report["outcomes"][0]
    assert po["points_total"] is None
    assert po["points_embedded"] is None
    assert po["embedding_coverage"] is None
    assert po["legs"] == []
    # the current run's own pre-flight default is recorded (never keyless)
    assert report["methodology"]["embedder"]["reason"] == "not_checked"


# ── D4: vector-leg trace + "legs" surfacing (E2E-1 never-null) ────────────

def test_tortoise_fts_query_records_no_embedder_and_encode_failed(
        tmp_path, monkeypatch):
    """D4 (R3): tortoise_fts_query distinguishes no_embedder (get() → None)
    from encode_failed (get() returns a model whose encode raises) — the
    two failure modes were previously conflated into silent query_vec=None."""
    from tortoise.embeddings import EmbeddingModel
    q = _mini()[0]
    # (a) no_embedder
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q)
        monkeypatch.setattr(EmbeddingModel, "get",
                            staticmethod(lambda load_timeout=None: None))
        trace: list[dict] = []
        sdk.tortoise_fts_query(q["question"], limit=5, leg_trace=trace)
        vec = next(e for e in trace if e["leg"] == "vector")
        assert vec["reason"] == "no_embedder"
        assert vec["ran"] is False
    finally:
        sdk.close()
    # (b) encode_failed
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q)
        monkeypatch.setattr(EmbeddingModel, "get", staticmethod(
            lambda load_timeout=None: _BrokenEncodeModel()))
        trace = []
        sdk.tortoise_fts_query(q["question"], limit=5, leg_trace=trace)
        vec = next(e for e in trace if e["leg"] == "vector")
        assert vec["reason"] == "encode_failed"
        assert vec["ran"] is False
    finally:
        sdk.close()


def test_poisoned_plain_list_node_fts_still_hits(tmp_path):
    """D4 (R3): a plain-list embedding node (raw projection write bypassing
    create_point) → vector trace reason='query_failed' (NOT no_embeddings),
    and FTS still returns hits — the leg-mix shows the failure, never a
    silent empty vector leg."""
    _require_embedder()
    q = {
        "question_id": "mini_poison_001",
        "question_type": "single-session-user",
        "question": "board game",
        "answer": "Catan",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["poison-s0"],
        "haystack_dates": ["2025-06-10"],
        "haystack_sessions": [[
            {"role": "user", "content": "My favorite board game is Catan.",
             "has_answer": True}]],
        "answer_session_ids": ["poison-s0"],
    }
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q)
        sdk._get_proj().g.query(
            "CREATE (p:Point {id:'poisoned', content:'board game', "
            "pointKind:'statement', lme_question_id:$q, "
            "embedding:[0.1, 0.2]})",
            params={"q": q["question_id"]})
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        vec = next(e for e in ret["legs"] if e["leg"] == "vector")
        assert vec["reason"] == "query_failed"
        fts = next(e for e in ret["legs"] if e["leg"] == "fts")
        assert fts["reason"] == "ok"
        assert any(h["id"] == "poisoned" for h in ret["hits"])
    finally:
        sdk.close()


def test_trace_timeout_on_overrun(tmp_path, monkeypatch):
    """D4 (R3): a strategy overrunning the 500ms collective deadline is
    self-recorded as reason='timeout' (never absent), the merge is
    non-blocking (no join past the deadline), and the outcome's legs are a
    snapshot — a late append to one outcome cannot rewrite another's."""
    import time
    _require_embedder()
    q = _mini()[0]
    sdk = _fresh_sdk(tmp_path / "timeout")
    try:
        ingest_haystack(sdk, q)
        proj = sdk._get_proj()
        real_query = proj.g._g.query  # the guarded wrapper is read-only; patch
        # the underlying graph handle (all queries funnel through it)

        def _slow_vector_query(*args, **kwargs):
            cypher = args[0]
            if "euclideanDistance" in cypher:
                time.sleep(0.8)
            return real_query(*args, **kwargs)

        monkeypatch.setattr(proj.g._g, "query", _slow_vector_query)
        t0 = time.monotonic()
        trace: list[dict] = []
        sdk.tortoise_fts_query(q["question"], limit=5, leg_trace=trace)
        elapsed = time.monotonic() - t0
        vec = next(e for e in trace if e["leg"] == "vector")
        assert vec["reason"] == "timeout"  # never absent
        assert vec["ran"] is True and vec["degraded"] is True
        # The MERGE is non-blocking — the timeout entry is synthesized at the
        # deadline, not after the overrunning worker finishes; the call is
        # bounded by the worker's own duration + overhead (the executor
        # shutdown joins workers to keep graph access serialized). A join-
        # past-the-deadline merge would add the full sleep on top.
        assert elapsed < 1.2, \
            f"call unacceptably blocked on the overrun worker ({elapsed:.2f}s)"
        # the retrieve-level legs are snapshotted per outcome
        ret1 = retrieve_for_question(sdk, q, ks=(5,), top_k=5)
        assert any(e["leg"] == "vector" for e in ret1["legs"])
        ret2 = retrieve_for_question(sdk, q, ks=(5,), top_k=5)
        assert ret2["legs"] is not ret1["legs"]
        ret1["legs"].append({"leg": "vector", "ran": True,
                             "degraded": False, "reason": "late", "count": 0})
        assert all(e["reason"] != "late" for e in ret2["legs"])
    finally:
        sdk.close()


def test_fallback_entry_all_early_returns(tmp_path, monkeypatch):
    """D4 (R3): the fallback entry is appended on EVERY early-return branch
    of tortoise_fts_query — snapshot path (tfidf_snapshot), legacy
    fallback_tfidf path (tfidf_legacy), and the non-point return []
    (no_fallback_applicable)."""
    import tortoise.fallback_snapshot as fb_snap
    q = _mini()[0]
    _no_embedder(monkeypatch)  # sparse-leg path: every strategy fails
    # (a) snapshot path — the cached lean snapshot serves the fallback
    sdk = _fresh_sdk(tmp_path / "a")
    try:
        ingest_haystack(sdk, q)
        trace: list[dict] = []
        sdk.tortoise_fts_query(q["question"], limit=5, leg_trace=trace)
        fb = next(e for e in trace if e["leg"] == "fallback")
        assert fb["reason"] == "tfidf_snapshot"
        assert fb["count"] > 0
    finally:
        sdk.close()
    # (b) legacy path — build_snapshot → None forces fallback_tfidf
    # (distinct graph dir: _fresh_sdk reuses one path, and the snapshot
    # store is keyed by graph_name — a re-ingested same-path graph would
    # serve part (a)'s cached snapshot instead of rebuilding)
    monkeypatch.setattr(fb_snap, "build_snapshot", lambda proj: None)
    sdk = _fresh_sdk(tmp_path / "b")
    try:
        ingest_haystack(sdk, q)
        trace = []
        sdk.tortoise_fts_query(q["question"], limit=5, leg_trace=trace)
        fb = next(e for e in trace if e["leg"] == "fallback")
        assert fb["reason"] == "tfidf_legacy"
        assert fb["count"] > 0
    finally:
        sdk.close()
    # (c) non-point entity → no fallback runs → no_fallback_applicable,
    # appended LAST (never after the fallback entry can come another)
    sdk = _fresh_sdk(tmp_path / "c")
    try:
        trace = []
        sdk.tortoise_fts_query(q["question"], entity_type="event",
                               limit=5, leg_trace=trace)
        fb = next(e for e in trace if e["leg"] == "fallback")
        assert fb["reason"] == "no_fallback_applicable"
        assert trace[-1]["leg"] == "fallback"
    finally:
        sdk.close()


def test_encode_broken_no_contradiction(tmp_path, monkeypatch):
    """D2/D5 (R3): an embedder whose encode raises → embedding_coverage ==
    0.0 AND methodology.vector_strategy != 'enabled' — the report never
    contradicts itself; the trace records encode_failed, not no_embedder."""
    from tools.longmem_eval.run import _preflight_embedder
    from tortoise.embeddings import EmbeddingModel
    monkeypatch.setattr(EmbeddingModel, "get", staticmethod(
        lambda load_timeout=None: _BrokenEncodeModel()))
    q = _mini()[0]
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        assert ret["embedding_coverage"] == 0.0
        vec = next(e for e in ret["legs"] if e["leg"] == "vector")
        assert vec["reason"] == "encode_failed"
    finally:
        sdk.close()
    status = _preflight_embedder(mock=True)
    assert status["reason"] == "encode_failed"
    outcomes, report = run_evaluation(
        _mini()[:1], reader=MockReader(), judge=MockJudge(), ks=(5,),
        top_k=5, split="s", work_dir=str(tmp_path), embedder_status=status)
    assert len(outcomes) == 1
    assert report["methodology"]["vector_strategy"] != "enabled"


def test_legs_recorded_never_null(tmp_path):
    """D4 (R3, E2E-1): retrieve_for_question returns legs with a vector
    entry carrying a boolean ran — the leg-mix precondition is recorded per
    question, never null; the shape rule 'reason never null when degraded'
    holds for every entry."""
    _require_embedder()
    q = _mini()[0]
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        legs = ret["legs"]
        assert legs  # never null/empty
        vec = next(e for e in legs if e["leg"] == "vector")
        assert vec["ran"] is True
        for e in legs:
            assert set(e) >= {"leg", "ran", "degraded", "reason", "count"}
            if e["degraded"]:
                assert e["reason"] is not None  # shape rule
    finally:
        sdk.close()


def test_leg_trace_default_none_byte_identical(tmp_path):
    """D4 (R3): default leg_trace=None → no trace, byte-identical results
    (hit ids equal with and without the kwarg)."""
    q = _mini()[0]
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q)
        base = sdk.tortoise_fts_query(q["question"], limit=5)
        trace: list[dict] = []
        traced = sdk.tortoise_fts_query(q["question"], limit=5,
                                        leg_trace=trace)
        assert [h["id"] for h in base] == [h["id"] for h in traced]
        assert trace  # tracing recorded entries
    finally:
        sdk.close()


# ── Task 5: sparse-leg tests pinned (env-independent) + vector verified ───

def test_vector_strategy_verified_in_eval_path(tmp_path):
    """E2E-1 alignment (R3 #1542, Task 5): a paraphrased query with ZERO
    token overlap with the fact surfaces it via the semantic (vector) leg in
    the ACTUAL eval path — the dense leg is verified end-to-end, not just
    unit-tested (skip-if-no-embedder)."""
    _require_embedder()
    q = {
        "question_id": "mini_semantic_elixir_001",
        "question_type": "single-session-user",
        "question": "Which programming language does this person prefer "
                    "for coding?",
        "answer": "Elixir",
        "question_date": "2025-06-15",
        "haystack_session_ids": ["sem-s0"],
        "haystack_dates": ["2025-06-10"],
        "haystack_sessions": [[
            {"role": "user",
             "content": "I really enjoy building side projects with Elixir "
                        "these days.",
             "has_answer": True}]],
        "answer_session_ids": ["sem-s0"],
    }
    sdk = _fresh_sdk(tmp_path)
    try:
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        # the zero-overlap fact surfaces in top-k via the semantic leg
        elixir_hits = [h for h in ret["hits"] if "Elixir" in h["content"]]
        assert elixir_hits, "the Elixir point must surface in top-k"
        vec = next(e for e in ret["legs"] if e["leg"] == "vector")
        assert vec["ran"] is True
        assert vec["count"] > 0
        # never null: every hit carries a non-empty match_source
        assert all(h.get("match_source") for h in ret["hits"])
        assert ret["embedding_coverage"] == 1.0
    finally:
        sdk.close()


# ── M8 (#1528): statistical discipline — Wilson CI + exact McNemar ──────

@pytest.mark.parametrize("w,l,expected", [
    (4, 1, 0.375),     # Abstention shared 28 — the v2 "win" that was NOT significant
    (11, 10, 1.0),     # MSR 121 shared
    (19, 18, 1.0),     # TR 130 shared
    (3, 15, 0.0075),   # IE — significant but 9/15 losses never ran extraction
    (1, 0, 1.0), (5, 0, 0.0625), (0, 0, 1.0),
])
def test_mcnemar_exact_fixtures(w, l, expected):  # noqa: E741 — w/l pairs
    """Two-sided exact binomial McNemar, pinned to /tmp/v3-synth/02-validity.md."""
    assert mcnemar_exact(w, l) == pytest.approx(expected, abs=1e-3)


@pytest.mark.parametrize("k,n,expected", [
    (20, 28, (0.529, 0.847)),   # Abstention baseline
    (63, 121, (0.432, 0.608)),  # MSR baseline
    (72, 133, (0.457, 0.624)),  # MSR v2
    (0, 10, (0.0, 0.278)),
    (0, 0, (0.0, 0.0)),         # empty denominator → (0,0), never nan
])
def test_wilson_ci_fixtures(k, n, expected):
    """95% Wilson, no continuity correction — recomputed for the validity synthesis."""
    lo, hi = wilson_ci(k, n)
    assert lo == pytest.approx(expected[0], abs=1e-3)
    assert hi == pytest.approx(expected[1], abs=1e-3)


# ── M8 (#1528): compare_reports — shared-qid deltas, McNemar, flip lists ──

def _cmp_report(outcomes, failures, tag, reader_model="reader-x"):
    """Minimal report dict for compare_reports (the comparison reads
    outcomes/failures/methodology only — no build_report gate needed)."""
    return {
        "benchmark": "LongMemEval",
        "dataset": "xiaowu0162/longmemeval-cleaned",
        "split": "s",
        "n_questions": len(outcomes),
        "outcomes": outcomes,
        "failures": failures,
        "n_failed": len(failures),
        "methodology": {
            "dataset_source": "xiaowu0162/longmemeval-cleaned",
            "split": "s",
            "reader_model": reader_model,
            "judge_model": "judge-gpt4o",
            "ingest_mode": "deterministic",
            "reader_prompt_hash": "deadbeefcafe1234",
            "git_sha": "c0ffee",
            "run_at_utc": "2026-08-20T00:00:00Z",
        },
    }


def _msr_outcome(qid, label, *, sp20=0.9, ctx=1000, errs=0, points=0):
    return {"question_id": qid, "question_type": "multi-session", "label": label,
            "session_recall@k": {"20": sp20}, "context_tokens": ctx,
            "n_ingest_errors": errs, "context_point_count": points}


def _msr_reports():
    """Validity-synthesis-verified MSR numbers (regression fixture, not a
    tuning target): 121 shared qids, baseline 63/121 correct, v2 64/121 on
    the shared set (11 b-wins / 10 a-wins), 12 qids failed-in-A with 8/12
    correct in B — all zero-point."""
    a_out, b_out = [], []
    for i in range(53):      # concordant correct
        a_out.append(_msr_outcome(f"msr_cc_{i:03d}", True))
        b_out.append(_msr_outcome(f"msr_cc_{i:03d}", True))
    for i in range(47):      # concordant wrong
        a_out.append(_msr_outcome(f"msr_cw_{i:03d}", False))
        b_out.append(_msr_outcome(f"msr_cw_{i:03d}", False))
    for i in range(11):      # b-wins (A wrong / B right)
        a_out.append(_msr_outcome(f"msr_bwin_{i:03d}", False))
        b_out.append(_msr_outcome(f"msr_bwin_{i:03d}", True))
    for i in range(10):      # a-wins (A right / B wrong)
        a_out.append(_msr_outcome(f"msr_awin_{i:03d}", True))
        b_out.append(_msr_outcome(f"msr_awin_{i:03d}", False))
    restored = [f"msr_failed_{i:03d}" for i in range(12)]
    correct_in_b = {f"msr_failed_{i:03d}" for i in range(8)}
    a_failures = [{"question_id": q, "question_type": "multi-session",
                   "error": "reader:retries_exhausted",
                   "failed_at_utc": "2026-08-20T00:00:00Z"} for q in restored]
    for q in restored:
        b_out.append(_msr_outcome(q, q in correct_in_b, sp20=0.8, ctx=900))
    return (_cmp_report(a_out, a_failures, "msr-a"),
            _cmp_report(b_out, [], "msr-b"))


def test_compare_reports_msr_verified():
    """The MSR numbers from the validity synthesis — the math is pinned so a
    future refactor cannot silently shift the decomposition."""
    a, b = _msr_reports()
    cmp = compare_reports(a, b)
    assert cmp["overall"]["shared_delta_pp"] == pytest.approx(0.83, abs=0.01)
    assert cmp["overall"]["headline_delta_pp"] == pytest.approx(2.07, abs=0.01)
    dec = cmp["overall"]["decomposition"]
    assert dec["shared_net_flips"] == 1 and dec["b_wins"] == 11
    assert dec["a_wins"] == 10
    assert dec["reliability_restored"]["count"] == 12
    assert dec["reliability_restored"]["correct"] == 8
    assert dec["reliability_restored"]["rate"] == pytest.approx(8 / 12, abs=0.001)
    assert dec["reliability_pp"] == pytest.approx(1.24, abs=0.05)
    assert dec["residual_pp"] == pytest.approx(0.0, abs=0.05)
    msr = cmp["per_category"]["Multi-Session Reasoning"]
    assert msr["mcnemar"]["p_value"] == pytest.approx(1.0, abs=1e-3)
    assert len(cmp["flip_lists"]["Multi-Session Reasoning"]) == 21


def _abs_outcome(qid, label):
    """An _abs qid with question_type "multi-session" (the MSR abstention
    variants) — must land in the Abstention category."""
    return {"question_id": qid, "question_type": "multi-session", "label": label,
            "session_recall@k": {"20": 0.9}, "context_tokens": 1000,
            "n_ingest_errors": 0, "context_point_count": 0}


def _abs_reports():
    """The VERIFIED v2 Abstention case (validity A7): A 71.4% n=28 -> B 83.3%
    n=30, shared 23/28 vs 20/28 (4W/1L) -> McNemar p=0.375 (NOT significant).
    gpt4_372c3eed_abs is a b-win, 6456829e_abs an a-win. Plus composition-only
    MSR qids on each side so the Multi-Session Reasoning category exists."""
    a_out, b_out = [], []
    # 19 concordant correct, 4 concordant wrong, 4 b-wins, 1 a-win (n=28)
    for i in range(19):
        a_out.append(_abs_outcome(f"abs_cc_{i:02d}_abs", True))
        b_out.append(_abs_outcome(f"abs_cc_{i:02d}_abs", True))
    for i in range(4):
        a_out.append(_abs_outcome(f"abs_cw_{i:02d}_abs", False))
        b_out.append(_abs_outcome(f"abs_cw_{i:02d}_abs", False))
    b_win_qids = [f"abs_bw_{i:02d}_abs" for i in range(3)] + ["gpt4_372c3eed_abs"]
    for q in b_win_qids:
        a_out.append(_abs_outcome(q, False))
        b_out.append(_abs_outcome(q, True))
    a_out.append(_abs_outcome("6456829e_abs", True))
    b_out.append(_abs_outcome("6456829e_abs", False))
    # B-only _abs qids (both correct) -> B n=30 with 25 correct.
    for q in ("abs_only_b_01_abs", "abs_only_b_02_abs"):
        b_out.append(_abs_outcome(q, True))
    # Composition-only MSR qids (ratios keep A/B accuracies at 71.4%/83.3%).
    for i in range(7):
        a_out.append(_msr_outcome(f"msr_only_a_{i:02d}", i < 5))
    for i in range(6):
        b_out.append(_msr_outcome(f"msr_only_b_{i:02d}", i < 5))
    return _cmp_report(a_out, [], "abs-a"), _cmp_report(b_out, [], "abs-b")


def test_compare_reports_abs_category_and_rule():
    """_abs qids carry question_type "multi-session" but must land in the
    "Abstention" category; the inclusion rule is stated in the header."""
    a, b = _abs_reports()
    cmp = compare_reports(a, b)
    assert cmp["overall"]["headline_delta_pp"] == pytest.approx(11.9, abs=0.05)
    assert cmp["overall"]["shared_delta_pp"] == pytest.approx(10.71, abs=0.01)
    ab = cmp["per_category"]["Abstention"]
    assert ab["mcnemar"]["p_value"] == pytest.approx(0.375, abs=1e-3)
    assert ab["mcnemar"]["significant_at_0_05"] is False
    assert "Abstention" in cmp["flip_lists"]
    flips = {f["question_id"]: f for f in cmp["flip_lists"]["Abstention"]}
    assert flips["gpt4_372c3eed_abs"]["direction"] == "b_win"
    assert flips["6456829e_abs"]["direction"] == "a_win"
    assert "gpt4_372c3eed_abs" not in cmp["flip_lists"]["Multi-Session Reasoning"]
    assert len(flips) == 5                                          # 4W/1L
    assert "_abs" in cmp["header"]["abs_inclusion_rule"]          # rule stated
    assert cmp["header"]["per_category_n"]["Abstention"] == \
        {"a": 28, "b": 30, "shared": 28}
    # _abs counted under the raw question_type too
    assert cmp["header"]["per_type_n"]["multi-session"]["a"] >= 28
    assert cmp["header"]["per_type_n"]["multi-session"]["b"] >= 28
    assert cmp["header"]["per_type_n"]["multi-session"]["shared"] == 28


def test_compare_reports_identical():
    r, _ = _msr_reports()
    cmp = compare_reports(r, r)
    assert cmp["overall"]["shared_delta_pp"] == 0.0
    assert cmp["overall"]["headline_delta_pp"] == 0.0
    assert all(not flips for flips in cmp["flip_lists"].values())
    assert all(v["mcnemar"]["p_value"] == 1.0
               for v in cmp["per_category"].values())


def test_compare_reports_stripped_outcomes_graceful():
    """Outcomes missing the aux columns (pre-M7 reports) -> honest None in
    the flip rows, no crash."""
    a = _cmp_report([
        {"question_id": "q1", "question_type": "single-session-user",
         "label": True},
        {"question_id": "q2", "question_type": "single-session-user",
         "label": False},
    ], [], "strip-a")
    b = _cmp_report([
        {"question_id": "q1", "question_type": "single-session-user",
         "label": False},
        {"question_id": "q2", "question_type": "single-session-user",
         "label": True},
    ], [], "strip-b")
    cmp = compare_reports(a, b)
    f = next(iter(cmp["flip_lists"].values()))[0]
    assert f["sr20_a"] is None and f["zero_point_a"] is None
    assert f["context_tokens_b"] is None and f["error_count_b"] is None


def test_compare_reports_comparability_warnings():
    a, b = _msr_reports()
    a["methodology"]["reader_model"] = "deepseek/deepseek-chat"
    b["methodology"]["reader_model"] = "deepseek-v4-flash"
    cmp = compare_reports(a, b)
    assert any("reader_model" in w for w in cmp["comparability"]["warnings"])
    assert cmp["comparability"]["reader_model"]["match"] is False
    assert any("answer-shape" in c or "judge" in c for c in cmp["caveats"])


# ── M8 (#1528): --compare CLI (no run environment needed) ────────────────

def _mini_compare_report(labels):
    outcomes = [{
        "question_id": f"q{i}",
        "question_type": "single-session-user",
        "question_date": "2024-01-15",
        "label": lab,
        "hypothesis": "h",
        "session_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
        "turn_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
        "context_tokens": 100,
        "context_point_count": 3,
    } for i, lab in enumerate(labels)]
    return outcomes_to_report(
        outcomes,
        reader_model="reader-x",
        judge_model="judge-x",
        ks=(5, 10, 20),
        top_k=20,
        split="s",
        dataset_semantics_audit=_trusted_audit(),
    )


def test_cli_compare(tmp_path):
    """--compare A B loads both report JSONs and returns the compare dict;
    --compare-out persists it round-trippably. No dataset, no keys."""
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps(_mini_compare_report([True, True, True, False])))
    b.write_text(json.dumps(_mini_compare_report([True, True, False, True])))
    cmp = run_main(["--compare", str(a), str(b)])
    assert isinstance(cmp, dict)
    assert cmp["comparison_rule"]
    assert cmp["overall"]["shared_n"] == 4
    # 1 a-win + 1 b-win on the shared 4 -> net zero shared delta
    assert cmp["overall"]["shared_delta_pp"] == 0.0
    assert cmp["overall"]["decomposition"]["a_wins"] == 1
    assert cmp["overall"]["decomposition"]["b_wins"] == 1
    out = tmp_path / "cmp.json"
    cmp2 = run_main(["--compare", str(a), str(b), "--compare-out", str(out)])
    assert out.is_file()
    roundtrip = json.loads(out.read_text(encoding="utf-8"))
    assert roundtrip["overall"]["shared_delta_pp"] == \
        cmp2["overall"]["shared_delta_pp"]
    assert roundtrip["header"]["abs_inclusion_rule"] == \
        cmp2["header"]["abs_inclusion_rule"]
