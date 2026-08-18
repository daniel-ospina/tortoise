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
from tortoise.models import OpenAICompatModel  # noqa: E402

from tools.longmem_eval.ingest import ingest_haystack  # noqa: E402
from tools.longmem_eval.judge import (  # noqa: E402
    LLMJudge, MockJudge, OfficialJudgeModel, _parse_judge_response,
    get_anscheck_prompt, is_abstention,
)
from tools.longmem_eval.reader import MockReader, build_reader  # noqa: E402
from tools.longmem_eval.retrieve import (  # noqa: E402
    render_context, retrieve_for_question,
)
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
    from tools.longmem_eval import dataset as ds
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
    from tools.longmem_eval import dataset as ds
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

    with pytest.raises(Exception):
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
             "pointKind": "statement"},
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
        # CONTAINS edges: session → raw + extracted points
        cnt = proj.g.query(
            "MATCH (s:Session {id:$id})-[:CONTAINS]->(p) RETURN count(*)",
            params={"id": "lme:test_v2_q:s0"}).result_set
        assert cnt[0][0] == 3  # raw + 2 points
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
