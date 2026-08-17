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

from tortoise.sdk import TortoiseSDK  # noqa: E402

from tools.longmem_eval.ingest import ingest_haystack  # noqa: E402
from tools.longmem_eval.judge import (  # noqa: E402
    MockJudge, _parse_judge_response, get_anscheck_prompt, is_abstention,
)
from tools.longmem_eval.reader import MockReader, build_reader  # noqa: E402
from tools.longmem_eval.retrieve import retrieve_for_question  # noqa: E402
from tools.longmem_eval.run import run_evaluation, run_main  # noqa: E402

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"


def _mini() -> list[dict]:
    return json.loads(MINI.read_text(encoding="utf-8"))


def _fresh_sdk(tmp_path) -> TortoiseSDK:
    return TortoiseSDK(str(tmp_path / "lme.db"))


# ── Pipeline end-to-end (mocked reader + judge, embedded DB) ───────────────

def test_mini_pipeline_end_to_end_mock(tmp_path):
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
    sdk = _fresh_sdk(tmp_path)
    try:
        q = _mini()[0]  # mini_ie_user_001: 2 sessions
        stats = ingest_haystack(sdk, q)
        assert stats["sessions"] == 2
        assert stats["turns"] == 6
        assert stats["evidence_turns"] == 1
        assert stats["raw_transcripts"] == 2

        proj = sdk._get_proj()
        rows = proj.g.query("MATCH (s:Session) RETURN count(s)").result_set
        assert rows[0][0] == 2
        rows = proj.g.query(
            "MATCH (p:Point {pointKind:'event'}) RETURN count(p)").result_set
        assert rows[0][0] == 6
        rows = proj.g.query(
            "MATCH (p:Point {pointKind:'session-transcript'}) RETURN count(p)"
        ).result_set
        assert rows[0][0] == 2
        # evidence turn carries has_answer=true (turn-level recall source)
        rows = proj.g.query(
            "MATCH (p:Point {has_answer:true}) RETURN count(p)").result_set
        assert rows[0][0] == 1
        # deterministic ids, session linkage
        rows = proj.g.query(
            "MATCH (s:Session {id:'lme:mini_ie_user_001:s1'})-[:CONTAINS]->"
            "(t:Point) RETURN count(t)").result_set
        assert rows[0][0] == 4  # 3 turns + 1 raw transcript
    finally:
        sdk.close()


def test_ingestion_idempotent(tmp_path):
    sdk = _fresh_sdk(tmp_path)
    try:
        q = _mini()[1]  # multi-session question
        ingest_haystack(sdk, q)
        ingest_haystack(sdk, q)  # re-run same haystack → no double-write
        proj = sdk._get_proj()
        rows = proj.g.query("MATCH (p:Point) RETURN count(p)").result_set
        # 2 sessions × (3 turns + 1 raw transcript)
        assert rows[0][0] == 8
    finally:
        sdk.close()


# ── Retrieval recall ───────────────────────────────────────────────────────

def test_retrieval_recalls_evidence_session(tmp_path):
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_ie_user_001")
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


def test_retrieval_multi_session_evidence(tmp_path):
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_msr_002")
        ingest_haystack(sdk, q)
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        # both evidence sessions are recovered (session-level recall exact);
        # turn-level: s0's evidence turn is inside the top-5, s1's is further
        # down (TF-IDF ranks s1's planning turn higher) — ≥0.5 is honest.
        assert ret["session_recall@k"]["5"] == 1.0  # both evidence sessions
        assert ret["turn_recall@k"]["5"] >= 0.5
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

    with pytest.raises(Exception):
        ds.load_dataset("s", limit=1, download=False)  # no cache → fails
