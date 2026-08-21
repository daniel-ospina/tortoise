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

from tools.longmem_eval.ingest import ingest_haystack  # noqa: E402, RUF100
from tools.longmem_eval.judge import (  # noqa: E402, RUF100
    LLMJudge, MockJudge, OfficialJudgeModel, _parse_judge_response,
    get_anscheck_prompt, is_abstention,
)
from tools.longmem_eval.reader import MockReader, build_reader  # noqa: E402, RUF100
from tools.longmem_eval.retrieve import (  # noqa: E402, RUF100
    _annotate_hits, render_context, retrieve_for_question,
)
from tools.longmem_eval.run import (  # noqa: E402
    outcomes_to_report, run_evaluation, run_main,
)

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


def test_outcomes_to_report_golden_shape():
    """Golden report-shape pin (M1/S22, issue #1522): outcomes_to_report
    returns a dict with the full published key set. Regression guard — commit
    4acb47d4 absorbed the build_report(...) return into reader_prompt_source
    as dead code, making outcomes_to_report(...) implicitly return None; the
    restore (2f7c3df8) is pinned here so the report contract (E2E-2: report is
    a real dict) cannot silently regress again."""
    outcomes = [{
        "question_id": "q-golden-1",
        "question_type": "single-session-user",
        "question_date": "2024-01-15",
        "label": True,
        "hypothesis": "golden hypothesis",
        "session_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
        "turn_recall@k": {"5": 0.5, "10": 0.5, "20": 0.5},
        "evidence_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
        "n_ingest_errors": 0,
        "context_tokens": 120,
        "context_point_count": 3,
        "retrieval_latency_ms": 11.0,
        "reader_latency_ms": 22.0,
        "judge_latency_ms": 33.0,
        "total_ms": 66.0,
    }]
    report = outcomes_to_report(
        outcomes,
        reader_model="golden-reader",
        judge_model="golden-judge",
        ks=(5, 10, 20),
        top_k=20,
        split="s",
    )
    # The regression made this None — a real dict is the whole point (E2E-2).
    assert isinstance(report, dict)
    # Top-level key set is the published report contract.
    assert set(report) == {
        "benchmark", "dataset", "split", "n_questions", "accuracy",
        "retrieval", "latency_ms", "methodology", "failures", "n_failed",
        "outcomes",
    }
    assert report["benchmark"] == "LongMemEval"
    assert report["dataset"] == "xiaowu0162/longmemeval-cleaned"
    assert report["split"] == "s"
    assert report["n_questions"] == 1

    acc = report["accuracy"]
    assert acc["overall"] == 1.0
    assert acc["task_averaged"] == 1.0
    assert acc["per_category"]["Information Extraction"] == {
        "accuracy": 1.0, "n": 1}
    assert acc["per_type"]["single-session-user"] == {"accuracy": 1.0, "n": 1}

    ret = report["retrieval"]
    assert ret["session_recall@k"] == {"5": 1.0, "10": 1.0, "20": 1.0}
    assert ret["turn_recall@k"] == {"5": 0.5, "10": 0.5, "20": 0.5}
    assert ret["evidence_recall@k"] == {"5": 1.0, "10": 1.0, "20": 1.0}
    assert ret["context_tokens_mean"] == 120.0
    assert ret["context_point_count_mean"] == 3.0

    lat = report["latency_ms"]
    assert lat["retrieval"]["mean_ms"] == 11.0
    assert lat["reader"]["mean_ms"] == 22.0
    assert lat["judge"]["mean_ms"] == 33.0
    assert lat["total_per_question"]["mean_ms"] == 66.0

    m = report["methodology"]
    assert m["reader_model"] == "golden-reader"
    assert m["judge_model"] == "golden-judge"
    assert m["k_values"] == [5, 10, 20]
    assert m["top_k_context"] == 20
    # #1414 parity hashes — produced by reader_prompt_source/JUDGE_RUBRIC_ID.
    assert m["reader_prompt_hash"]
    assert m["judge_rubric_id_hash"]

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
        "n_ingest_errors": 0,
        "context_tokens": 120,
        "retrieval_latency_ms": 11.0,
        "reader_latency_ms": 22.0,
        "judge_latency_ms": 33.0,
        "total_ms": 66.0,
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
        # evidence turn carries has_answer=true (turn-level recall source);
        # M6 (#1526): the answer session's raw transcript carries mark (c)/
        # (a) too — 1 evidence turn + 1 marked raw transcript
        rows = proj.g.query(
            "MATCH (p:Point {has_answer:true}) RETURN count(p)").result_set
        assert rows[0][0] == 2
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


def test_retrieve_for_question_surfaces_supersession_annotation(tmp_path):
    """#1367 integration: a real superseded claim in the eval graph (via the
    PRODUCTION supersede_point → CORRECTS edge) is (a) carried through
    retrieve_for_question's annotated hits as superseded_by/supersedes keys
    and (b) rendered with the SUPERSEDED BY marker when decorated by the
    production fetch_point_epistemic_state — the exact path the Docker/HNSW
    eval takes. Embedded CI itself can't decorate (snapshot fallback), so the
    decoration is applied with the real graph machinery, not a fake."""
    from tortoise.search_engine import fetch_point_epistemic_state

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

        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        assert all("superseded_by" in h and "supersedes" in h
                   for h in ret["hits"])

        # the production decoration (what tortoise_fts_query applies on the
        # full path) sees the CORRECTS edge — the reader would get the marker
        state = fetch_point_epistemic_state(sdk._get_proj().g, ["ku_old"])
        assert state["ku_old"]["superseded_by"]["id"] == "ku_new"
        assert state["ku_old"]["status"] == "superseded"

        # simulate the full-path hit → annotation → rendering pipeline
        raw_hit = {"id": "ku_old", "content": "I used to prefer espresso in the morning.",
                   "match_source": "rrf", "superseded_by": state["ku_old"]["superseded_by"]}
        props = {"ku_old": {"lme_session_index": 0, "session_id": "s0",
                             "has_answer": False}}
        [annotated] = _annotate_hits([raw_hit], props, ["2025-06-02"])
        text = render_context([annotated], question_date=q.get("question_date"))
        assert "[SUPERSEDED BY: I now prefer drip coffee over espresso.]" in text
        assert "I used to prefer espresso in the morning." in text
    finally:
        sdk.close()


# ── Error isolation + checkpoint/resume (P2) ───────────────────────────────


def test_single_question_failure_does_not_abort_run(tmp_path):
    """A failing question is recorded in report['failures'] and the run
    continues — one transient LLM error never aborts the whole 500-Q run."""
    class _ExplodingReader(MockReader):
        def answer(self, **kw):
            raise RuntimeError("transient provider boom")

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
    # a resume over the failed checkpoint keeps skipping them (no re-run)
    outcomes4, report4 = run_evaluation(
        _mini()[:2], reader=MockReader(), judge=MockJudge(),
        ks=(5,), top_k=5, split="s", work_dir=str(tmp_path),
        checkpoint=str(tmp_path / "lme-fail.json"))
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
    """#1369: ingest_haystack_v2 writes the extractor payload (points with
    evidence marks by content overlap, entities, events, operators) and
    retains the Session + raw-transcript legs."""
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
        stats = ingest_haystack_v2(sdk, question, model=object())
        assert stats["points"] == 2
        assert stats["operators"] == 1
        assert stats["entities"] == 1
        assert stats["events"] == 1
        # raw transcript leg retained
        proj = sdk._get_proj()
        raw_rows = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN count(*)",
            params={"id": "lme:test_v2_q:s0:raw"}).result_set
        assert raw_rows[0][0] == 1
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
        # CONTAINS edges: session → raw + extracted points + turn points
        cnt = proj.g.query(
            "MATCH (s:Session {id:$id})-[:CONTAINS]->(p) RETURN count(*)",
            params={"id": "lme:test_v2_q:s0"}).result_set
        assert cnt[0][0] == 5  # raw + 2 extracted + 2 turn points
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
                lambda sdk, query, limit: [{"id": "pt_x",
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
