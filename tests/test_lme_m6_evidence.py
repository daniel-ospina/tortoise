"""M6 evidence-marking recalibration tests (#1526, epic #1509).

Covers the three independent evidence marks (source-session attribution /
verbatim quote anchor / raw-chunk containment) from the shared
``tools.longmem_eval.evidence`` module, the N/A-not-0.0 retrieve semantics,
the report vacuity accounting, the session_id coverage validation on both
capture paths (SDK + hosted commit — owner validation), and the 52-healthy
fixture calibration (run protocol step 2 — offline, no graph, no LLM keys).

The old miscalibrated ``>=0.4`` content-overlap predicate fired 1/12,085 on
the v2 run (51/52 healthy questions with zero evidence marks); the fixture
pins that old state and proves the recalibration reaches the E2E-3 >95%
gate with marks (a)+(c) alone.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.longmem_eval import evidence as ev  # noqa: E402, RUF100
from tools.longmem_eval.evidence import (  # noqa: E402, RUF100
    EVIDENCE_QUOTE_CAP,
    anchor_quote,
    chunk_mark,
    evidence_sessions,
    mark_for,
    overlap,
    quote_mark,
    source_session_mark,
    tokens,
)
from tools.longmem_eval.ingest import _session_transcript, ingest_haystack  # noqa: E402, RUF100
from tools.longmem_eval.report import build_report  # noqa: E402, RUF100
from tools.longmem_eval.retrieve import retrieve_for_question  # noqa: E402, RUF100
from tortoise.sdk import TortoiseSDK  # noqa: E402, RUF100

MINI = Path(__file__).parent / "fixtures" / "longmemeval_mini.json"
HEALTHY52 = Path(__file__).parent / "fixtures" / "lme_v2_healthy52.json"

# ── helpers ────────────────────────────────────────────────────────────────


def _mini() -> list[dict]:
    return json.loads(MINI.read_text(encoding="utf-8"))


def _healthy52() -> dict:
    return json.loads(HEALTHY52.read_text(encoding="utf-8"))


def _fresh_sdk(tmp_path) -> TortoiseSDK:
    return TortoiseSDK(str(tmp_path / "lme.db"))


def _outcome(qid: str, *, er=None, tr=None, eturns: int = 0,
             epoints: int = 0) -> dict:
    """Minimal completed outcome for build_report vacuity tests."""
    return {
        "question_id": qid,
        "question_type": "single-session-user",
        "question_date": "2024-01-15",
        "label": True,
        "hypothesis": "h",
        "session_recall@k": {"5": 1.0, "10": 1.0, "20": 1.0},
        "turn_recall@k": {"5": tr if tr is not None else None,
                          "10": tr if tr is not None else None,
                          "20": tr if tr is not None else None},
        "evidence_recall@k": {"5": er, "10": er, "20": er},
        "n_ingest_errors": 0,
        "context_tokens": 100,
        "context_point_count": 3,
        "ingest": {"evidence_turns": eturns, "evidence_points": epoints},
    }


# ── D1/D2: the three marks (unit) ──────────────────────────────────────────

def test_evidence_marks_matrix():
    """Each mark fires alone; OR combinations; the removed >=0.4 predicate's
    BVA (0.39/0.40/0.41 content overlap with NO new mark) leaves a point
    unmarked — the recalibration is not a threshold retune."""
    ev_turns = ["quantum observation is key to the experiment"]
    ev_sessions = {"sess-1"}
    # (a) alone: same session id, no quote, no verbatim content
    m = mark_for({"content": "unrelated filler text", "quote": ""},
                 session_id="sess-1", evidence_sessions=ev_sessions,
                 answer_turn_contents=ev_turns)
    assert m["marks"] == {"source_session": True, "verbatim": False,
                          "raw_chunk": False} and m["has_answer"]
    # (b) alone: foreign session, verbatim-containing quote
    m = mark_for({"content": "unrelated filler", "quote": ev_turns[0]},
                 session_id="sess-2", evidence_sessions=ev_sessions,
                 answer_turn_contents=ev_turns)
    assert m["marks"] == {"source_session": False, "verbatim": True,
                          "raw_chunk": False} and m["has_answer"]
    # (c) alone: foreign session, no quote, raw-chunk verbatim containment
    m = mark_for({"content": ev_turns[0], "quote": ""},
                 session_id="sess-2", evidence_sessions=ev_sessions,
                 answer_turn_contents=ev_turns)
    assert m["marks"] == {"source_session": False, "verbatim": False,
                          "raw_chunk": True} and m["has_answer"]
    # OR combo (a)+(b)
    m = mark_for({"content": "x", "quote": ev_turns[0]},
                 session_id="sess-1", evidence_sessions=ev_sessions,
                 answer_turn_contents=ev_turns)
    assert m["has_answer"] and m["marks"]["source_session"] \
        and m["marks"]["verbatim"]
    # >=0.4-removal equivalence: 0.39/0.40/0.41 overlap with no new mark →
    # all identical (unmarked) — the old predicate is gone, not retuned.
    base = " ".join(f"w{i}" for i in range(100))  # 100 content tokens
    assert len(tokens(base)) == 100
    for frac in (0.39, 0.40, 0.41):
        n = max(1, int(len(tokens(base)) * frac))
        assert n in (39, 40, 41), f"frac={frac} maps to distinct n"
        partial = " ".join(sorted(tokens(base))[:n])
        m = mark_for({"content": partial, "quote": ""},
                     session_id="other", evidence_sessions=set(),
                     answer_turn_contents=[base])
        assert not m["has_answer"], f"frac={frac} must stay unmarked"


def test_quote_mark_bva():
    """(b) boundary: 0.49 no / 0.50 yes / 0.51 yes (the run-protocol step-2
    knob); containment fires regardless of overlap; the >200-char truncation
    falls back to the best-window (<= EVIDENCE_QUOTE_CAP chars)."""
    # a turn of exactly 100 content tokens → overlap is the token ratio
    turn = " ".join(f"word{i}" for i in range(100))
    assert len(tokens(turn)) == 100
    assert not quote_mark(" ".join(f"word{i}" for i in range(49)), [turn])
    assert quote_mark(" ".join(f"word{i}" for i in range(50)), [turn])
    assert quote_mark(" ".join(f"word{i}" for i in range(51)), [turn])
    # containment beats truncation: a quote CONTAINING the turn verbatim
    # (case/whitespace-insensitively) is marked regardless of length limits
    assert quote_mark("  quantum   OBSERVATION is key. ", ["quantum observation is key"])
    # >200-char turn → best-window quote stays within the cap
    # (40 distinct content tokens padded past 200 chars — a realistic long
    # turn where a 200-char window covers more than half the tokens)
    long_turn = " ".join(f"tok{i} a" for i in range(40))
    assert len(long_turn) > EVIDENCE_QUOTE_CAP
    window = ev._best_window(long_turn, long_turn)
    assert len(window) <= EVIDENCE_QUOTE_CAP
    # the window still n-gram-overlaps the full turn >= 0.5 when it covers
    # half its distinct tokens (the truncation fallback keeps (b) real)
    assert overlap(window, long_turn) >= 0.5
    assert quote_mark(window, [long_turn])
    # single-token overflow: a token longer than the cap is truncated — the
    # quote never exceeds the commit-schema max_length=200 contract
    overflow = "x" * 250 + " tail"
    w2 = ev._best_window(overflow, overflow)
    assert len(w2) <= EVIDENCE_QUOTE_CAP


def test_chunk_mark_normalized_verbatim():
    """(c) is case/whitespace-insensitive verbatim containment; non-answer
    text is never marked."""
    assert chunk_mark(
        "User: My favorite board game is Catan.\nAssistant: Nice!",
        ["my favorite board game is catan."])
    assert chunk_mark("A   B\nC", ["a b c"])
    assert not chunk_mark("the user likes chess", ["my favorite board game is catan."])
    assert not chunk_mark("", ["anything"])
    assert not chunk_mark("text", [])


def test_evidence_sessions_matches_ingest_id_fallback():
    q = {"question_id": "q1",
         "haystack_session_ids": ["s0"],
         "haystack_sessions": [[{"role": "user", "content": "a",
                                 "has_answer": True}]]}
    assert evidence_sessions(q) == {"s0"}
    q2 = {"question_id": "q1", "haystack_sessions": [
        [{"role": "user", "content": "a", "has_answer": True}]]}
    assert evidence_sessions(q2) == {"q1-s0"}  # ingest id fallback


# ── D3: deterministic quote anchoring (unit) ───────────────────────────────

def test_anchor_quote_floor_and_cap():
    turns = [{"role": "user", "content": "I love hiking in the alps"},
             {"role": "user", "content": "quantum observation is the key fact"}]
    # point paraphrasing the second turn → anchored there (>= anchor floor)
    quote = anchor_quote("the quantum observation remains the key fact", turns)
    assert quote and "quantum" in quote
    assert len(quote) <= EVIDENCE_QUOTE_CAP
    # unrelated point → below the anchor floor → no quote
    assert anchor_quote("completely unrelated nonsense about pasta", turns) == ""
    # no turns → no quote
    assert anchor_quote("anything", []) == ""


# ── integration: v2 ingest marks three ways (FalkorDBLite) ─────────────────

def test_v2_ingest_marks_three_ways(tmp_path, monkeypatch):
    """M6 D1/D9 + R1 (#1540): the v2 leg writes has_answer=true via (a)
    source-session attribution on evidence-session points, (b) verbatim
    anchor on a quoted foreign-session point, and (c) raw-chunk containment
    on the answer session's chunk; idempotent re-ingest ORs the marks. The
    D5 denominator hygiene keeps the raw-chunk containment view OUT of the
    point-level evidence_marks breakdown (chunk evidence is reported as
    chunk_evidence_recall@k)."""
    import tortoise.extractor_v2 as ev2
    from tools.longmem_eval.ingest_v2 import ingest_haystack_v2

    def _fake_extract(model, conversation, **kw):
        # which session is this? the answer-turn content selects the payload
        conv_text = " ".join(t["content"] for t in conversation)
        if "quantum observation is key" in conv_text:
            points = [
                {"id": "pt_s0a",
                 "content": "the quantum observation is the key fact",
                 "pointKind": "statement"},
                {"id": "pt_s0b",
                 "content": "the team recorded the observation yesterday",
                 "pointKind": "statement"},
            ]
        else:
            points = [
                # foreign session (s1, not an evidence session): (b) fires
                # via the payload quote overlapping the answer turn (gate 2 —
                # a non-empty payload quote is consumed directly), (a) does
                # not (session_id not in evidence sessions).
                {"id": "pt_s1a", "content": "unrelated logistics note",
                 "quote": "quantum observation is key", "pointKind": "statement"},
            ]
        return {"payload": {"entities": [], "events": [], "points": points,
                            "operators": []},
                "minted_kinds": [], "supersessions": [], "errors": [],
                "warnings": []}

    monkeypatch.setattr(ev2, "extract_session_v2", _fake_extract)

    sdk = _fresh_sdk(tmp_path)
    try:
        question = {
            "question_id": "q_m6_v2",
            "haystack_session_ids": ["s0", "s1"],
            "haystack_dates": ["2026-08-01", "2026-08-02"],
            "haystack_sessions": [
                [{"role": "user", "content": "quantum observation is key",
                  "has_answer": True}],
                [{"role": "user", "content": "what's for lunch"}],
            ],
        }
        stats = ingest_haystack_v2(sdk, question, model=object(),
                                   chunk_turns=2)
        proj = sdk._get_proj()

        def _has_answer(pid):
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.has_answer, false)",
                params={"id": pid}).result_set
            return bool(rows[0][0])

        # (c): the answer session's chunk is marked via raw-chunk containment
        # (R1 #1540: turn-granular chunks replace the whole-session :raw blob)
        assert _has_answer("lme:q_m6_v2:s0:c0") is True
        # non-answer session's chunk stays unmarked
        assert _has_answer("lme:q_m6_v2:s1:c0") is False
        # (a): points extracted from the evidence session are marked
        assert _has_answer("pt_s0a") is True
        assert _has_answer("pt_s0b") is True  # whole evidence session
        # (b): the foreign-session point with an overlapping quote is marked
        assert _has_answer("pt_s1a") is True
        # marks OR into the stats with a per-type breakdown. D5 (#1540):
        # evidence_points counts marked EXTRACTED points only — the marked
        # chunk is excluded (chunk evidence = chunk_evidence_recall@k).
        assert stats["evidence_points"] == 3  # pt_s0a + pt_s0b + pt_s1a
        assert stats["evidence_marks"]["source_session"] >= 2
        assert stats["evidence_marks"]["verbatim"] >= 1
        assert stats["evidence_marks"]["raw_chunk"] == 0  # D5 hygiene
        # every written point carries its source session (runner leg)
        rows = proj.g.query(
            "MATCH (p:Point {id:'pt_s0a'}) RETURN coalesce(p.session_id, '')"
        ).result_set
        assert rows[0][0] == "s0"

        # idempotent re-ingest ORs the marks (never False over True)
        stats2 = ingest_haystack_v2(sdk, question, model=object(),
                                    chunk_turns=2)
        assert stats2["points"] == 0  # all points pre-existed
        assert stats2["chunks"] == 0  # all chunks pre-existed
        assert _has_answer("pt_s0a") is True
        assert _has_answer("lme:q_m6_v2:s0:c0") is True
    finally:
        sdk.close()


# ── integration: deterministic leg marks the raw transcript ────────────────

def test_deterministic_ingest_marks_raw_transcript(tmp_path):
    """M6 Task 3 + R1 (#1540): the deterministic leg's evidence TURN point
    carries has_answer=true; the raw chunks stay UNMARKED (D3 — the
    deterministic leg keeps its turn-id evidence path); the :raw blob is
    retired; stats count marked extracted points only (D5)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_ie_user_001")
        stats = ingest_haystack(sdk, q, chunk_turns=2)
        assert stats["evidence_points"] == 1  # the evidence turn only
        assert stats["evidence_turns"] == 1
        proj = sdk._get_proj()

        def _has_answer(pid):
            rows = proj.g.query(
                "MATCH (p:Point {id:$id}) RETURN coalesce(p.has_answer, false)",
                params={"id": pid}).result_set
            return bool(rows[0][0])

        # answer session mini-s1: evidence turn marked; chunks UNMARKED (D3)
        assert _has_answer("lme:mini_ie_user_001:s1:t2") is True
        assert _has_answer("lme:mini_ie_user_001:s1:c0") is False
        assert _has_answer("lme:mini_ie_user_001:s1:c1") is False
        # the :raw blob id is retired
        rows = proj.g.query(
            "MATCH (p:Point {id:$id}) RETURN count(*)",
            params={"id": "lme:mini_ie_user_001:s1:raw"}).result_set
        assert rows[0][0] == 0
        # all has_answer points = the evidence turn only
        rows = proj.g.query(
            "MATCH (p:Point {has_answer:true}) RETURN count(p)").result_set
        assert rows[0][0] == 1
        # idempotent re-ingest: same count, marks OR'd
        ingest_haystack(sdk, q, chunk_turns=2)
        rows = proj.g.query(
            "MATCH (p:Point {has_answer:true}) RETURN count(p)").result_set
        assert rows[0][0] == 1
    finally:
        sdk.close()


# ── D5: N/A-not-0.0 retrieve semantics ─────────────────────────────────────

def test_retrieve_na_not_zero(tmp_path):
    """Empty denominators report None, never a forced 0.0 (#1369): the
    evidence-less abstention mini → both None; a one-leg denominator reports
    its real number."""
    sdk = _fresh_sdk(tmp_path)
    try:
        # evidence-less abstention question: no evidence turns, no marks →
        # evidence_recall@k AND turn_recall@k are None (not 0.0)
        q_abs = next(x for x in _mini() if x["question_id"] == "mini_abs_005_abs")
        ingest_haystack(sdk, q_abs)
        ret = retrieve_for_question(sdk, q_abs, ks=(5, 10, 20), top_k=20)
        assert ret["evidence_recall@k"] == {"5": None, "10": None, "20": None}
        assert ret["turn_recall@k"] == {"5": None, "10": None, "20": None}
        # session_recall unchanged (answer_session_ids present in S split)
        assert ret["session_recall@k"]["5"] == 0.0
    finally:
        sdk.close()


def test_retrieve_na_one_leg_denominator(tmp_path):
    """D5: when ONE leg has a denominator it reports its real number —
    (i) evidence turns in the dataset but no marks in the graph → turn_recall
    real (deterministic leg), evidence_recall None; (ii) marks in the graph
    but no evidence-turn ids → both real (v2 attribution)."""
    sdk = _fresh_sdk(tmp_path)
    try:
        q = next(x for x in _mini() if x["question_id"] == "mini_ie_user_001")
        ingest_haystack(sdk, q)
        proj = sdk._get_proj()
        # (i) strip all marks from the graph → evidence_recall None,
        # turn_recall still real over the deterministic evidence-turn ids
        proj.g.query("MATCH (p:Point {lme_question_id:$q}) "
                     "SET p.has_answer = false",
                     params={"q": "mini_ie_user_001"})
        ret = retrieve_for_question(sdk, q, ks=(5,), top_k=20)
        assert ret["evidence_recall@k"] == {"5": None}
        assert isinstance(ret["turn_recall@k"]["5"], float)
        # (ii) re-mark one point, strip the question's has_answer flags →
        # evidence_turn_ids empty, marked points present → real numbers
        proj.g.query("MATCH (p:Point {id:$id}) SET p.has_answer = true",
                     params={"id": "lme:mini_ie_user_001:s1:t2"})
        q_stripped = json.loads(json.dumps(q))
        for session in q_stripped["haystack_sessions"]:
            for t in session:
                t.pop("has_answer", None)
        ret2 = retrieve_for_question(sdk, q_stripped, ks=(5,), top_k=20)
        assert isinstance(ret2["evidence_recall@k"]["5"], float)
        assert isinstance(ret2["turn_recall@k"]["5"], float)
    finally:
        sdk.close()


# ── D6: report vacuity accounting ──────────────────────────────────────────

def test_report_vacuity_excludes_na():
    """Mixed None/real outcomes: the evidence_recall mean drops N/A (the
    vacuity-drag regression — None coerced to 0.0), records
    evidence_recall_n@k + evidence_vacuity_rate@k + evidence_coverage, and
    the methodology carries the vacuity band + anchor."""
    outcomes = [
        _outcome("q1", er=0.5, tr=0.5, eturns=3, epoints=5),
        # miscalibration: evidence in dataset, zero marks → N/A (None)
        _outcome("q2", er=None, tr=None, eturns=1, epoints=0),
        # evidence exists in the graph but never surfaced → vacuous 0.0
        _outcome("q3", er=0.0, tr=0.0, eturns=2, epoints=2),
        # abstention: no evidence at all → N/A (None), excluded everywhere
        _outcome("q4", er=None, tr=None, eturns=0, epoints=0),
    ]
    report = build_report(outcomes, dataset_id="d", split="s",
                          reader_model="r", judge_model="j",
                          extraction_approach="x", ks=(5, 10, 20), top_k=20)
    ret = report["retrieval"]
    # mean over evidence-bearing (non-None) only: (0.5 + 0.0) / 2
    assert ret["evidence_recall@k"]["5"] == 0.25
    assert ret["evidence_recall_n@k"]["5"] == 2
    # vacuity rate: 1 of 2 evidence-bearing outcomes had 0.0 while evidence
    # exists; q2 (None) is NOT vacuous — it is N/A
    assert ret["evidence_vacuity_rate@k"]["5"] == 0.5
    # coverage: 2 of 3 dataset-evidence-bearing questions wrote evidence
    # points (q1, q3 covered; q2 miscalibrated; q4 not evidence-bearing)
    assert ret["evidence_coverage"] == round(2 / 3, 4)
    # turn_recall mean drops Nones (no vacuity drag)
    assert ret["turn_recall@k"]["5"] == 0.25
    m = report["methodology"]
    # vacuity band anchored to the fixture calibration: derive the expected
    # fraction from the committed fixture so a fixture change fails the test
    fix = _healthy52()
    covered = sum(
        1 for q in fix["questions"]
        if chunk_mark(
            _session_transcript(q["answer_sessions"][0]),
            [str(t["content"]) for t in q["answer_sessions"][0]
             if t.get("has_answer")]))
    vacuous = len(fix["questions"]) - covered
    assert f"{vacuous}/52 vacuous" in m["vacuity_band"]
    assert m["vacuity_band_anchor"] == (
        "fixture calibration 2026-08-20 (0/52 vacuous); re-anchor at "
        "run protocol step 6")
    assert "N/A (None) on empty denominators" in m["recall_definition"]


def test_report_evidence_coverage_zero_when_no_evidence():
    """No evidence-bearing questions → coverage 0.0 (and no vacuity keys)."""
    outcomes = [_outcome("abs", er=None, eturns=0, epoints=0)]
    report = build_report(outcomes, dataset_id="d", split="s",
                          reader_model="r", judge_model="j",
                          extraction_approach="x", ks=(5,), top_k=20)
    assert report["retrieval"]["evidence_coverage"] == 0.0
    assert report["retrieval"]["evidence_recall@k"] is None


# ── OWNER VALIDATION: session_id on both capture paths ─────────────────────

def test_session_id_written_by_sdk_capture_path(tmp_path, monkeypatch):
    """The SDK capture path (capture_session → _extract_session_v2) writes
    session_id on the extracted points — source-session attribution (a) is
    trustable on this path."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")
    sdk = _fresh_sdk(tmp_path)
    try:
        res = sdk.capture_session([
            {"role": "user", "content": "We decided to ship the strategy "
                                        "document first."},
            {"role": "assistant", "content": "Agreed, the strategy is durable."},
        ])
        sid = res["session_id"]
        assert res["extracted"] >= 1
        proj = sdk._get_proj()
        rows = proj.g.query(
            "MATCH (p:Point {is_episodic:false}) "
            "RETURN p.id, coalesce(p.session_id, '')").result_set
        assert rows, "expected extracted (non-episodic) points"
        for pid, psid in rows:
            assert psid == sid, f"point {pid} missing session_id"
    finally:
        sdk.close()


def test_session_id_written_by_hosted_commit_path(monkeypatch, tmp_path):
    """The HOSTED commit path (POST /v1/sessions/commit) writes session_id on
    the committed points — M6 owner validation found this path missing it
    (SDK capture already wrote it); the gap is closed so mark (a) is
    trustable on both capture paths."""
    import fastapi.testclient as ftc

    os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
    os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

    from tortoise.commit_schema import compute_client_commit_id
    from tortoise.hosted_api import app, get_current_team

    team = {"team_id": "m6-test-team", "key_id": "k", "tier": "free",
            "max_users": 1, "max_graphs": 1, "max_points": 10000,
            "max_api_keys": 2, "max_sessions": 1000}
    app.dependency_overrides[get_current_team] = lambda: dict(team)

    import tortoise.hosted_api as ha_mod
    _orig_init = ha_mod.TortoiseSDK.__init__

    def _patched_init(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig_init(self, str(tmp_path / "hosted.db"), namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched_init
    ha_mod._FALLBACK_KEEPALIVE.clear()
    try:
        payload = {
            "schema_version": "1",
            "session_id": "m6-hosted-session",
            "client_commit_id": "",
            "captured_at": "2026-08-20T10:00:00Z",
            "extractor": {"version": "v2", "mode": "byok",
                          "calibration_version": "v2"},
            "summary": "summary text", "story_arc": "arc text",
            "provenance_refs": [{"path": "session.md", "spans": ["0-10"]}],
            "sources": [], "entities": [], "events": [], "operators": [],
            "points": [{
                "id": "pt_" + "0" * 63 + "1",
                "content": "the strategy is durable and shipped first",
                "pointKind": "statement", "status": "live",
                "reason": "NEW",
                "confidence": 0.8, "c_cal": 0.5, "quote": "the strategy",
                "source_ref": "session.md",
            }],
            "telemetry": {"extractor": {"version": "v2", "mode": "byok",
                                        "calibration_version": "v2"},
                          "model": {"provider": "byok", "id": "m",
                                    "cfg_hash": ""},
                          "counts": {"kept": 1, "candidate": 1,
                                     "segment": 1, "window": 1,
                                     "empty_windows": 0},
                          "keep_ratio": None, "dedup_hits": None,
                          "frontier_calls": 1, "llm_cost_usd": None,
                          "extraction_ms": 0, "retry_count": 0,
                          "last_error_code": None,
                          "confidence_histogram": None},
        }
        payload["client_commit_id"] = compute_client_commit_id(
            payload["session_id"], payload["points"], payload["entities"],
            payload["operators"], payload["summary"], payload["story_arc"],
            payload.get("events", []))
        with ftc.TestClient(app) as tc:
            r = tc.post("/v1/sessions/commit", json=payload)
            assert r.status_code == 200, r.text
        sdk = ha_mod._make_sdk(namespace=team["team_id"])
        proj = sdk._get_proj()
        pid = "pt_" + "0" * 63 + "1"
        rows = proj.g.query(
            "MATCH (p:Point {id:$id}) "
            "RETURN coalesce(p.session_id, '')",
            params={"id": pid}).result_set
        assert rows and rows[0][0] == "m6-hosted-session", \
            "committed point missing session_id"
    finally:
        ha_mod.TortoiseSDK.__init__ = _orig_init
        app.dependency_overrides.clear()


# ── D7/D8: 52-healthy fixture shape + calibration ──────────────────────────

def test_healthy52_fixture_shape():
    """52 qids, all single-session-user, exactly 1 answer session each, the
    v2-checkpoint subset complete, and the file committable (<= 1 MB)."""
    fix = _healthy52()
    assert HEALTHY52.stat().st_size <= 1_048_576, "fixture exceeds 1 MB"
    qs = fix["questions"]
    assert len(qs) == 52
    assert fix["_meta"]["n_questions"] == 52
    assert all(q["question_type"] == "single-session-user" for q in qs)
    # exactly one answer session per question (the 54 evidence turns inside)
    assert all(len(q["answer_sessions"]) == 1 for q in qs)
    assert fix["_meta"]["n_questions_without_answer_session"] == 0
    assert fix["_meta"]["n_evidence_turns"] == 54
    # checkpoint subset complete
    ck_keys = {"points", "evidence_points", "sessions", "turns",
               "raw_transcripts", "entities", "events", "operators",
               "supersessions", "n_ingest_errors", "first_error",
               "evidence_recall@k", "turn_recall@k", "session_recall@k"}
    assert all(set(q["checkpoint"]) == ck_keys for q in qs)
    # meta provenance complete
    for key in ("dataset", "split", "checkpoint_source", "updated_at_utc",
                "healthy_criterion", "calibration_goal",
                "miscalibration_note", "builder"):
        assert fix["_meta"].get(key), f"_meta.{key} missing"


def test_healthy52_calibration_coverage():
    """Run-protocol step-2 calibration, offline: marks (a)+(c) hit 52/52
    (>= the E2E-3 >95% gate) via the answer session's raw transcript and
    session attribution; the old-state regression pin (1/12,085; 51/52 with
    zero evidence marks) asserts the miscalibration the recalibration fixes;
    vacuity baseline 0/52."""
    fix = _healthy52()
    qs = fix["questions"]

    covered_a = 0
    covered_c = 0
    for q in qs:
        ans = q["answer_sessions"][0]
        transcript = _session_transcript(ans)
        ev_contents = [str(t["content"]) for t in ans if t.get("has_answer")]
        assert ev_contents, q["question_id"]
        # (c): the answer session's raw transcript contains the turns verbatim
        if chunk_mark(transcript, ev_contents):
            covered_c += 1
        # (a): rebuild the question shape the ingest legs see (the answer
        # session + a non-evidence session) and run the REAL evidence_sessions
        # predicate — the answer session id must be attributed.
        q_like = {
            "question_id": q["question_id"],
            "haystack_session_ids": [q["answer_session_ids"][0], "dummy-other"],
            "haystack_sessions": [
                ans,
                [{"role": "user", "content": "unrelated filler",
                  "has_answer": None}],
            ],
        }
        ev_sessions = evidence_sessions(q_like)
        assert ev_sessions == {q["answer_session_ids"][0]}, q["question_id"]
        if source_session_mark(q["answer_session_ids"][0], ev_sessions):
            covered_a += 1
    assert covered_a == 52 and covered_c == 52
    coverage = (covered_a + covered_c) / 2 / len(qs)
    assert coverage >= 0.95, "E2E-3 >95% gate must be reachable offline"
    # vacuity baseline: 0/52 evidence-bearing questions left evidence-less by
    # the new marks (every answer transcript is marked) — the initial
    # expectation band for report D6.
    assert covered_c == len(qs)

    # old-state regression pin from the checkpoint subset
    total_points = sum(q["checkpoint"]["points"] for q in qs)
    total_evidence = sum(q["checkpoint"]["evidence_points"] for q in qs)
    zero_evidence = sum(1 for q in qs
                        if q["checkpoint"]["evidence_points"] == 0)
    assert total_points == 12_085
    assert total_evidence == 1  # the 1/12,085 pin
    assert zero_evidence == 51  # 51/52 healthy questions had zero marks


def test_healthy52_evidence_sessions_equivalent_to_answer_ids():
    """The M6 reliance on answer_session_ids ≡ has-answer-session (the M7
    audit target) holds on the fixture as a pre-check."""
    fix = _healthy52()
    for q in fix["questions"]:
        assert len(q["answer_session_ids"]) == 1
        # the single answer session's id is in the haystack session ids
        assert q["answer_session_ids"][0] in q["haystack_session_ids"]
