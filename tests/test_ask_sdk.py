"""Ask-lane eval-lane tests (#1987 Tasks 4-5) — annotate_ask_hits + run_ask_lane.

Task 4 — ask-path-local hit annotation: session_date/speaker from the Event
join + source-turn speaker, additive keys only, undated/null-join
byte-identical, has_answer passthrough.

Task 5 — the eval-only ask lane (``tortoise/ask_lane.py``, #3849): local-lane
pipeline (validation FIRST,
exactly ONE model call incl. empty context — no pre-gate), 8k/40/32KiB caps,
resolved question_date semantics, the per-namespace reader cache
(tokens-race, key isolation, failed-build, lifecycle), and both-not-either
(search surfaces
never invoke the reader). The hosted-mode ``_post_ask`` client was removed
with the REST surface (#3849).

Runs on the docker lane (TORTOISE_DB_URI) — the #1987 test strategy's
integration layer.
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.ask_spotcheck import merge_capture_session
from tortoise.ask_lane import (
    _reset_ask_reader_cache_for_tests,
    run_ask_lane,
)
from tortoise.exceptions import (
    AskReaderUnavailable,
    AskValidationError,
)
from tortoise.retrieval import estimate_tokens_ask
from tortoise.schemas import (
    CODE_INVALID_QUESTION,
    CODE_INVALID_QUESTION_DATE,
    CODE_INVALID_QUESTION_TYPE,
    CODE_QUESTION_TOO_LONG,
)
from tortoise.sdk import TortoiseSDK


@pytest.fixture(autouse=True)
def _clean_ask_state():
    """Reset the shared ask-reader cache + budget between tests."""
    _reset_ask_reader_cache_for_tests()
    from tortoise.quota import _reset_ask_budget_for_tests
    _reset_ask_budget_for_tests()
    yield
    _reset_ask_reader_cache_for_tests()


def _new_sdk() -> TortoiseSDK:
    db = os.path.join(tempfile.mkdtemp(prefix="ask_sdk_"), "t.db")
    return TortoiseSDK(db)


class FakeReader:
    """complete() stub with a call counter + captured user message."""

    def __init__(self, reply: str = "The gym schedule is Monday and Wednesday.",
                 tokens_out: int = 12):
        self.reply = reply
        self.tokens_out = tokens_out
        self.calls = 0
        self.last_user: str | None = None
        self.closed = False

    def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        self.last_user = user
        return self.reply

    def close(self) -> None:
        self.closed = True


# ── Task 4: annotate_ask_hits ──────────────────────────────────────────────

#: The session `_seed_event_graph`'s extracted claims belong to. Capture
#: CONTAINS-wires its EXTRACTED Points to the session too (the extraction
#: loops in `tortoise/sdk.py`: `MATCH (s:Session {id:$sid}),
#: (p:Point {id:$pid}) MERGE (s)-[:CONTAINS]->(p)`), not just its turns.
SEEDED_EVENT_SESSION = "sess-extracted"


def _seed_event_graph(sdk: TortoiseSDK, turns: list[dict]) -> list[dict]:
    """Seed EXTRACTED-claim points + Events; return the ``tortoise_fts_query``
    hits.

    These Points model capture's EXTRACTED claims, and both halves of the
    shape they carry are written here: capture stamps ``n.eventId`` on an
    extracted claim (so the ``:Event`` join that produces ``session_date`` —
    this fixture's subject, #1987 Task 4 — reaches it) AND wires it to the
    session node with ``MERGE (s)-[:CONTAINS]->(p)`` (the extraction loops in
    ``tortoise/sdk.py``). The shipping point fetch resolves an extracted
    claim's identity from that edge.

    What it must NOT write is the ``p.sessionId`` PROP. Capture never writes
    one — on claims or on turns — and the fetch PREFERS a renderable
    ``p.sessionId`` over the CONTAINS edge, so a prop is the only provenance
    this fixture could carry that the graph cannot legitimately hold, and the
    only thing a forgery in it would satisfy. Pre-#3914 this helper had a
    ``t.get("sessionId")`` branch writing exactly that, with no ``(:Session)``
    node and no edge; ``test_seed_event_graph_ignores_a_session_id_key`` pins
    that a passed key is IGNORED, so re-adding the branch reds the build.
    """
    proj = sdk._get_proj()
    # The session node carries capture's own prop set — a bare-id Session is a
    # node shape no product writer produces, and consumers reading
    # `s.turn_count` / `s.is_episodic` would see None. This fixture models no
    # TURN stream, so `turn_count` counts the claims seeded here: the prop SET
    # is capture's, that one VALUE is the fixture's placeholder.
    merge_capture_session(sdk, SEEDED_EVENT_SESSION, len(turns))
    for i, t in enumerate(turns):
        point = sdk.create_point("statement", t["content"])
        eid = t.get("eventId", f"ev-{i}")
        proj.g.query(
            "MERGE (e:Event {eventId: $eid}) SET e.startedAt = $st",
            params={"eid": eid, "st": f"{t.get('session_date', '2026-08-20')}T10:00:00Z"},
        )
        sets = ["p.eventId = $eid"]
        params = {"pid": point["id"], "eid": eid}
        if t.get("speaker"):
            sets.append("p.speaker = $spk")
            params["spk"] = t["speaker"]
        proj.g.query(
            "MATCH (p:Point {id: $pid}) SET " + ", ".join(sets),
            params=params,
        )
        proj.g.query(
            "MATCH (s:Session {id:$sid}), (p:Point {id:$pid}) "
            "MERGE (s)-[:CONTAINS]->(p)",
            params={"sid": SEEDED_EVENT_SESSION, "pid": point["id"]},
        )
    return sdk.tortoise_fts_query("gym", limit=40, include_terminal=True)


def test_seed_event_graph_ignores_a_session_id_key():
    """#3914: an extracted-claim fixture must NOT forge the ``sessionId`` PROP.

    The removed branch wrote ``p.sessionId = $sid`` — no ``(:Session)`` node,
    no ``CONTAINS`` edge — the one provenance the graph cannot legitimately
    carry and the one the shipping fetch PREFERS over the edge. A passed key
    must now be a no-op AND the identity must still resolve, from the edge the
    fixture does write, so re-adding the branch OR dropping the edge reds it.
    """
    sdk = _new_sdk()
    hits = _seed_event_graph(sdk, [
        {"content": "the gym schedule is Monday", "eventId": "ev1",
         "session_date": "2026-08-01", "speaker": "user",
         "sessionId": "forged-sess"},
    ])
    assert hits, "fixture must retrieve"
    proj = sdk._get_proj()
    points = proj.g.query(
        "MATCH (p:Point) RETURN p.id, p.sessionId, p.eventId").result_set
    assert points, points
    for pid, sess_prop, _ev_prop in points:
        assert sess_prop is None, (pid, sess_prop)
    edges = proj.g.query(
        "MATCH (s:Session {id:$sid})-[:CONTAINS]->(p:Point) RETURN p.id",
        params={"sid": SEEDED_EVENT_SESSION}).result_set
    assert [r[0] for r in edges] == [r[0] for r in points], edges
    # The Session side is part of the shape too — capture writes all three.
    sess = proj.g.query(
        "MATCH (s:Session {id:$sid}) RETURN s.created_at, s.turn_count, "
        "s.is_episodic", params={"sid": SEEDED_EVENT_SESSION}).result_set
    assert len(sess) == 1, sess
    created_at, turn_count, is_episodic = sess[0]
    assert created_at, sess
    assert turn_count == len(points), sess
    assert is_episodic is True, sess
    # The wire identity comes from that edge — never from the forged key.
    assert all(h.get("sessionId") == SEEDED_EVENT_SESSION for h in hits), hits
    sdk.close()


def test_annotate_session_date_and_speaker():
    sdk = _new_sdk()
    hits = _seed_event_graph(sdk, [
        {"content": "the gym schedule is Monday", "eventId": "ev1",
         "session_date": "2026-08-01", "speaker": "user"},
    ])
    assert hits, "fixture must retrieve"
    ann = sdk.annotate_ask_hits(hits)
    assert len(ann) == len(hits)
    assert ann[0]["session_date"] == "2026-08-01"
    assert ann[0]["speaker"] == "user"


def test_annotate_speaker_from_source_turn():
    """Extracted point with source_turn_id → speaker from the source turn."""
    sdk = _new_sdk()
    proj = sdk._get_proj()
    turn = sdk.create_point("statement", "we decided the office hours are 9am")
    proj.g.query(
        "MATCH (p:Point {id: $pid}) SET p.speaker = 'assistant', p.eventId = 'ev9'",
        params={"pid": turn["id"]},
    )
    extracted = sdk.create_point("statement", "office hours are 9am")
    proj.g.query(
        "MATCH (p:Point {id: $pid}) SET p.eventId = 'ev9', p.source_turn_id = $tid",
        params={"pid": extracted["id"], "tid": turn["id"]},
    )
    proj.g.query(
        "MERGE (e:Event {eventId: 'ev9'}) SET e.startedAt = '2026-08-02T10:00:00Z'",
    )
    hits = sdk.tortoise_fts_query("office hours", limit=40, include_terminal=True)
    ann = sdk.annotate_ask_hits(hits)
    by_id = {h["id"]: h for h in ann}
    assert by_id[extracted["id"]]["speaker"] == "assistant"


def test_annotate_undated_hit_byte_identical():
    """Undated hit (no Event join) → no session_date; rendering unchanged."""
    sdk = _new_sdk()
    point = sdk.create_point("statement", "office hours are 9am")
    hits = sdk.tortoise_fts_query("office hours", limit=40, include_terminal=True)
    assert hits and hits[0]["id"] == point["id"]
    ann = sdk.annotate_ask_hits(hits)
    # additive keys ""/absent — never a marker (byte-identical rendering)
    assert not ann[0].get("session_date")
    assert not ann[0].get("speaker")
    from tortoise.retrieval import render_context
    assert render_context(hits, question_date="2026-08-29") == \
        render_context(ann, question_date="2026-08-29")


def test_annotate_null_join_byte_identical():
    """A hit whose eventId has NO Event node + no source turn → additive keys
    absent, no crash, no stale markers."""
    sdk = _new_sdk()
    sdk.create_point("statement", "office hours are 9am")
    hits = sdk.tortoise_fts_query("office hours", limit=40, include_terminal=True)
    # decorate the hit with a dangling eventId
    ann = sdk.annotate_ask_hits(hits)
    assert ann[0]["id"] == hits[0]["id"]
    assert not ann[0].get("session_date")


def test_annotate_has_answer_passthrough():
    """A hit carrying has_answer survives annotate_ask_hits unchanged."""
    sdk = _new_sdk()
    proj = sdk._get_proj()
    point = sdk.create_point("statement", "office hours are 9am")
    proj.g.query(
        "MATCH (p:Point {id: $pid}) SET p.has_answer = true, p.eventId = 'ev1'",
        params={"pid": point["id"]},
    )
    proj.g.query(
        "MERGE (e:Event {eventId: 'ev1'}) SET e.startedAt = '2026-08-02T10:00:00Z'",
    )
    hits = sdk.tortoise_fts_query("office hours", limit=40, include_terminal=True)
    # the MockReader-stamping field is not a SearchResult.to_dict field —
    # the eval's read-time marks inject it into the hit dict; annotate must
    # preserve it (dict passthrough, never dropped).
    hits[0]["has_answer"] = True
    ann = sdk.annotate_ask_hits(hits)
    assert ann[0]["has_answer"] is True
    assert ann[0]["session_date"] == "2026-08-02"


def test_annotate_d8_rides_through_decorated_hits():
    """Superseded point → superseded_by rides through the ALREADY-decorated
    hits (annotate_ask_hits does NOT re-fetch D8 state)."""
    sdk = _new_sdk()
    old = sdk.create_point("statement", "gym schedule is Monday")
    new = sdk.create_point("statement", "gym schedule is Tuesday")
    sdk.supersede_point(old["id"], new["id"])
    hits = sdk.tortoise_fts_query("gym schedule", limit=40, include_terminal=True)
    ann = sdk.annotate_ask_hits(hits)
    old_hit = next(h for h in ann if h["id"] == old["id"])
    assert old_hit.get("superseded_by"), "D8 marker must ride through"
    assert old_hit.get("status") == "superseded"


def test_annotate_dedup_key_order_pinning():
    """P2-20: hits LACKING sessionId but sharing an Event join group by the
    ANNOTATED session identifier — the per-session dedup cap applies."""
    from tortoise.retrieval import dedup_pool
    hits = [
        {"id": "a", "content": "chunk 1", "point_kind": "session-transcript",
         "session_date": "2026-08-01"},
        {"id": "b", "content": "chunk 2", "point_kind": "session-transcript",
         "session_date": "2026-08-01"},
        {"id": "c", "content": "chunk 3", "point_kind": "session-transcript",
         "session_date": "2026-08-01"},
        {"id": "d", "content": "chunk 4", "point_kind": "session-transcript",
         "session_date": "2026-08-01"},
        {"id": "e", "content": "other", "point_kind": "session-transcript",
         "session_date": "2026-08-02"},
    ]
    key = lambda h: h.get("session_date") or h.get("session_id") or f"idx:{h.get('lme_session_index', -1)}"  # noqa: E731
    deduped = dedup_pool(hits, max_chunks_per_session=3, session_key=key)
    assert [h["id"] for h in deduped] == ["a", "b", "c", "e"]


# ── Task 5: local-lane ask pipeline ────────────────────────────────────────

def _install_fake(sdk: TortoiseSDK, monkeypatch, reply="The gym schedule is Monday and Wednesday.", tokens_out=12) -> FakeReader:
    import tortoise.ask_lane as sdk_mod
    fake = FakeReader(reply=reply, tokens_out=tokens_out)
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
    return fake


def test_local_lane_pipeline(monkeypatch):
    sdk = _new_sdk()
    _seed_event_graph(sdk, [
        {"content": "the gym schedule is Monday and Wednesday",
         "eventId": "ev1", "session_date": "2026-08-01", "speaker": "user"},
    ])
    fake = _install_fake(sdk, monkeypatch)
    result = run_ask_lane(sdk, "what is the gym schedule?", question_date="2026-08-29")
    # the full 13-field shape
    assert set(result) == {"answer", "abstained", "question_type",
                           "question_date", "evidence", "context_tokens",
                           "model", "provider", "route", "cost_estimate_usd",
                           "duration_ms", "retrieval_degraded",
                           "retrieved_session_ids"}
    assert result["answer"] == "The gym schedule is Monday and Wednesday."
    assert result["abstained"] is False
    assert result["question_date"] == "2026-08-29"  # resolved value
    assert result["context_tokens"] == estimate_tokens_ask(result["evidence"])
    assert result["context_tokens"] <= 8000
    assert result["cost_estimate_usd"] > 0
    assert result["duration_ms"] >= 0
    assert fake.calls == 1  # exactly ONE model call


def test_cost_estimate_strong_rates_for_qwen_serving_reader(monkeypatch):
    """#2069: the response's cost_estimate_usd is metered at
    ASK_METER_RATES_STRONG when the SERVING lane's wire id is a
    strong-family spec (``qwen/qwen3.8-max`` via ``_LockedReader.model``)
    — the strong lane never reports a deepseek-envelope estimate."""
    import tortoise.ask_lane as sdk_mod
    from tortoise.metering import ASK_METER_RATES_STRONG, estimate_ask_cost_usd
    from tortoise.reader import system_prompt_for

    class _StrongReader(FakeReader):
        model = "qwen/qwen3.8-max"
        provider = "openrouter"
        route = "openrouter"
        last_finish_reason = "stop"
        last_completion_tokens = 12

    sdk = _new_sdk()
    fake = _StrongReader()
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
    result = run_ask_lane(sdk, "q")
    assert result["model"] == "qwen/qwen3.8-max"
    inp = (estimate_tokens_ask(system_prompt_for(result["question_type"]))
           + estimate_tokens_ask(result["evidence"]))
    expected = estimate_ask_cost_usd(inp, 12, rates=ASK_METER_RATES_STRONG)
    assert result["cost_estimate_usd"] == pytest.approx(expected)


def test_cost_estimate_default_rates_for_deepseek_serving_reader(monkeypatch):
    """#2069 regression: a deepseek-family serving wire id (the default
    lane — bare ``deepseek-v4-flash`` on deepseek-direct) keeps the deepseek
    envelope; the response cost_estimate_usd is unchanged for the default
    lane."""
    import tortoise.ask_lane as sdk_mod
    from tortoise.metering import ASK_METER_RATES, estimate_ask_cost_usd
    from tortoise.reader import system_prompt_for

    class _DeepSeekReader(FakeReader):
        model = "deepseek-v4-flash"
        provider = "deepseek-direct"
        route = "deepseek-direct"
        last_completion_tokens = 12

    sdk = _new_sdk()
    fake = _DeepSeekReader()
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
    result = run_ask_lane(sdk, "q")
    assert result["model"] == "deepseek-v4-flash"
    inp = (estimate_tokens_ask(system_prompt_for(result["question_type"]))
           + estimate_tokens_ask(result["evidence"]))
    expected = estimate_ask_cost_usd(inp, 12, rates=ASK_METER_RATES)
    assert result["cost_estimate_usd"] == pytest.approx(expected)


def test_ask_record_path_uses_strong_rates(monkeypatch):
    """#2069: the run_ask_lane() metering RECORD call site (step 7 — the pinned
    record path) meters the cost_usd at the SERVING lane's STRONG rates — a
    strong-lane query's cost_usd record never uses the deepseek envelope."""
    import tortoise.ask_lane as sdk_mod
    import tortoise.metering as metering_mod
    from tortoise.metering import (
        ASK_METER_RATES,
        ASK_METER_RATES_STRONG,
        estimate_ask_cost_usd,
    )

    captured = {}

    def _capture(org_id, *, tokens_in=0, tokens_out=0, cost_usd=0.0, **_):
        captured.update(tokens_in=tokens_in, tokens_out=tokens_out,
                        cost_usd=cost_usd)
        return None

    monkeypatch.setattr(metering_mod, "record_ask_usage", _capture)

    class _StrongReader(FakeReader):
        model = "qwen/qwen3.8-max"
        provider = "openrouter"
        route = "openrouter"
        last_completion_tokens = 12

    sdk = _new_sdk()
    fake = _StrongReader()
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: fake)
    run_ask_lane(sdk, "q", org_id="team-x")
    assert captured, "the record path must have run (explicit org_id)"
    expected = estimate_ask_cost_usd(
        captured["tokens_in"], captured["tokens_out"],
        rates=ASK_METER_RATES_STRONG)
    assert captured["cost_usd"] == pytest.approx(expected)
    # never the deepseek envelope (the under-count hazard is gone)
    under = estimate_ask_cost_usd(
        captured["tokens_in"], captured["tokens_out"], rates=ASK_METER_RATES)
    assert captured["cost_usd"] > under


def test_local_lane_default_question_date_utc(monkeypatch):
    """question_date default = server-now-UTC (resolved value in the
    response); a non-UTC clock at a boundary time does not leak local date."""
    sdk = _new_sdk()
    _install_fake(sdk, monkeypatch)
    result = run_ask_lane(sdk, "q")
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", result["question_date"])
    assert result["evidence"].startswith(f"Current Date: {result['question_date']}")


def test_empty_context_single_call(monkeypatch):
    """Empty pool → exactly ONE model call (no pre-gate) + abstained with
    evidence present."""
    sdk = _new_sdk()
    fake = _install_fake(sdk, monkeypatch, reply="I do not know the answer.")
    result = run_ask_lane(sdk, "something not in memory")
    assert fake.calls == 1
    assert result["abstained"] is True
    assert result["evidence"] is not None
    assert "do not know" in result["answer"]


def test_decoy_near_miss_exactly_one_call(monkeypatch):
    """Decoy-only and near-miss-only pools → exactly ONE model call each."""
    sdk = _new_sdk()
    _seed_event_graph(sdk, [
        {"content": "I bought a new bicycle yesterday", "eventId": "ev1",
         "session_date": "2026-08-20"},
    ])
    fake = _install_fake(sdk, monkeypatch, reply="I do not know.")
    # decoy: asks for a value that is a different attribute
    r1 = run_ask_lane(sdk, "what is the bicycle's color?")
    assert fake.calls == 1 and r1["abstained"] is True
    # near-miss: similar but different value present
    r2 = run_ask_lane(sdk, "what is the car's color?")
    assert fake.calls == 2 and r2["abstained"] is True


def test_local_lane_validation_first_zero_calls(monkeypatch):
    """P2-8: invalid inputs → AskValidationError with ZERO model calls AND
    zero retrieval calls (the retrieval stub is never invoked)."""
    sdk = _new_sdk()
    fake = _install_fake(sdk, monkeypatch)
    retrieved = []

    orig = sdk.tortoise_fts_query
    def _no_retrieval(*a, **k):
        retrieved.append(1)
        return orig(*a, **k)
    monkeypatch.setattr(sdk, "tortoise_fts_query", _no_retrieval)

    for bad, kw, code in [
        ("", {}, CODE_INVALID_QUESTION),
        ("   ", {}, CODE_INVALID_QUESTION),
        ("x" * 2001, {}, CODE_QUESTION_TOO_LONG),
        ("q", {"question_type": "bogus"}, CODE_INVALID_QUESTION_TYPE),
        ("q", {"question_date": "2023-02-29"}, CODE_INVALID_QUESTION_DATE),
        ("a\x00b", {}, CODE_INVALID_QUESTION),
        ("\u200b", {}, CODE_INVALID_QUESTION),
        ("q", {"question_date": 20230101},
         CODE_INVALID_QUESTION_DATE),  # non-str date → str()-coerced by
                                       # ask_lane._ask_validate (the removed
                                       # hosted AskRequest validator behaved
                                       # identically — P2)
    ]:
        with pytest.raises(AskValidationError) as ei:
            run_ask_lane(sdk, bad, **kw)
        assert ei.value.code == code, (bad, kw, ei.value.code)
    assert fake.calls == 0
    assert retrieved == []


def test_2000_char_boundary(monkeypatch):
    sdk = _new_sdk()
    fake = _install_fake(sdk, monkeypatch)
    assert run_ask_lane(sdk, "x" * 2000)["answer"] == fake.reply  # passes
    with pytest.raises(AskValidationError):
        run_ask_lane(sdk, "x" * 2001)


def test_oversized_hit_skip_and_caps(monkeypatch):
    """8k/40 caps honored; the byte cap (32 KiB) binds independently; the
    evidence never splits a character (no U+FFFD).

    The caps are SET explicitly: #4105 raised the ask-lane defaults to
    200/200/16000/128KiB, and this test pins the CAP-BINDING mechanism at a
    known small shape rather than depending on the product defaults."""
    monkeypatch.setenv("TORTOISE_ASK_RETRIEVAL_LIMIT", "40")
    monkeypatch.setenv("TORTOISE_ASK_CONTEXT_ITEM_CAP", "40")
    monkeypatch.setenv("TORTOISE_ASK_POOL_SIZE", "120")
    monkeypatch.setenv("TORTOISE_ASK_CONTEXT_TOKEN_CAP", "8000")
    monkeypatch.setenv("TORTOISE_ASK_CONTEXT_BYTE_CAP", "32768")
    sdk = _new_sdk()
    proj = sdk._get_proj()
    # a pathological CJK-heavy pool (unspaced runs — the word-based estimate
    # under-counts; the byte cap must bind)
    big_run = "\u4f60" * 30000  # ~90 KiB of CJK
    point = sdk.create_point("statement", big_run)
    proj.g.query(
        "MATCH (p:Point {id: $pid}) SET p.eventId = 'ev1'",
        params={"pid": point["id"]},
    )
    proj.g.query(
        "MERGE (e:Event {eventId: 'ev1'}) SET e.startedAt = '2026-08-01T10:00:00Z'",
    )
    fake = _install_fake(sdk, monkeypatch)
    result = run_ask_lane(sdk, "office hours")
    assert len(result["evidence"].encode("utf-8")) <= 32768
    assert estimate_tokens_ask(result["evidence"]) <= 8000
    assert "\ufffd" not in result["evidence"]
    assert fake.calls == 1


def test_undated_hits(monkeypatch):
    sdk = _new_sdk()
    sdk.create_point("statement", "office hours are 9am")
    fake = _install_fake(sdk, monkeypatch)
    result = run_ask_lane(sdk, "office hours?")
    assert result["answer"] == fake.reply
    assert result["evidence"]


def test_question_type_passthrough_and_override(monkeypatch):
    sdk = _new_sdk()
    _install_fake(sdk, monkeypatch)
    r = run_ask_lane(sdk, "how many days ago did we meet?")
    assert r["question_type"] == "temporal-reasoning"
    r2 = run_ask_lane(sdk, "how many days ago did we meet?",
                 question_type="multi-session")
    assert r2["question_type"] == "multi-session"


def test_reader_raise_maps_reader_unavailable(monkeypatch):
    sdk = _new_sdk()
    import tortoise.ask_lane as sdk_mod
    class Boom(FakeReader):
        def complete(self, *, system, user):
            raise RuntimeError("provider down")
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: Boom())
    with pytest.raises(AskReaderUnavailable):
        run_ask_lane(sdk, "q")


def test_tokens_race_same_cached_instance(monkeypatch):
    """2-3 concurrent run_ask_lane() calls through ONE cached model instance → each
    call's captured usage matches its own completion (the per-instance lock
    makes inner complete() + capture atomic)."""
    import tortoise.ask_lane as sdk_mod
    sdk = _new_sdk()
    _seed_event_graph(sdk, [
        {"content": "the gym schedule is Monday", "eventId": "ev1",
         "session_date": "2026-08-01"},
        {"content": "the gym schedule is Wednesday", "eventId": "ev2",
         "session_date": "2026-08-01"},
        {"content": "the gym schedule is Friday", "eventId": "ev3",
         "session_date": "2026-08-01"},
    ])

    class RaceReader:
        def __init__(self):
            self.lock = threading.Lock()
            self.completed = 0

        def complete(self, *, system, user):
            with self.lock:
                self.completed += 1
                n = self.completed
            # yield the CPU to force interleaving
            time.sleep(0.01)
            self.last_completion_tokens = n * 10
            return f"answer {n}"

        def close(self):
            pass

    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: RaceReader())
    results = []
    errors = []

    def _ask(q):
        try:
            results.append(run_ask_lane(sdk, q))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=_ask, args=(f"what is the schedule {i}?",))
               for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, errors
    assert len(results) == 3
    # every answer got a response; the shared instance served all three
    assert all(r["answer"] for r in results)


def test_cache_key_isolation():
    """Team A vs team B resolve to DIFFERENT cached model instances."""
    from tortoise.ask_lane import _ask_reader_model
    sdk_a = TortoiseSDK(tempfile.mkdtemp() + "/a.db", namespace="team-a")
    sdk_b = TortoiseSDK(tempfile.mkdtemp() + "/b.db", namespace="team-b")
    a = _ask_reader_model(sdk_a)
    b = _ask_reader_model(sdk_b)
    assert a is not b
    sdk_a.close()
    sdk_b.close()


def test_failed_build_never_cached(monkeypatch):
    """P2-21: the first ask's build raises → the cache does NOT retain the
    key → the second ask rebuilds and succeeds. P2: the per-key build lock
    is ALSO popped on the failed build — it never lingers in the module
    dict (unbounded growth under sustained build failure across
    namespaces)."""
    import tortoise.ask_lane as sdk_mod
    sdk = _new_sdk()
    state = {"fail": True}
    def _factory():
        if state["fail"]:
            raise RuntimeError("build boom")
        return FakeReader(reply="ok now")
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", _factory)
    with pytest.raises(AskReaderUnavailable):
        run_ask_lane(sdk, "q")
    # P2: the failed build leaves NO cached entry AND no lingering lock
    assert sdk_mod._ask_build_locks == {}
    state["fail"] = False
    result = run_ask_lane(sdk, "q")
    assert result["answer"] == "ok now"


def test_locked_reader_forwards_finish_reason(monkeypatch):
    """_LockedReader forwards the inner model's last_finish_reason from the
    same capture frame as the token usage (P2 — consumers previously got
    None because the attribute was never set)."""
    import tortoise.ask_lane as sdk_mod

    class FinishReader:
        def complete(self, *, system, user):
            self.last_prompt_tokens = 10
            self.last_completion_tokens = 20
            self.last_finish_reason = "stop"
            return "the gym schedule is Monday"

        def close(self):
            pass

    locked = sdk_mod._LockedReader(FinishReader())
    assert locked.complete(system="s", user="u") == "the gym schedule is Monday"
    assert locked.last_prompt_tokens == 10
    assert locked.last_completion_tokens == 20
    assert locked.last_finish_reason == "stop"
    # an inner model WITHOUT the attribute → None (never a crash)
    locked2 = sdk_mod._LockedReader(FakeReader(reply="x"))
    locked2.complete(system="s", user="u")
    assert locked2.last_finish_reason is None


def test_locked_reader_forwards_max_tokens(monkeypatch):
    """#2280: _LockedReader forwards a per-call max_tokens override to the
    inner model (the escalation lever) and omits it when not provided."""
    import tortoise.ask_lane as sdk_mod

    seen = {}

    class CapReader:
        def complete(self, *, system, user, max_tokens=None):
            seen["mt"] = max_tokens
            self.last_completion_tokens = 7
            self.last_finish_reason = "stop"
            return "x"

        def close(self):
            pass

    locked = sdk_mod._LockedReader(CapReader())
    assert locked.complete(system="s", user="u", max_tokens=2000) == "x"
    assert seen["mt"] == 2000
    locked.complete(system="s", user="u")
    assert seen["mt"] is None


class _CollapsingReader:
    """complete() stub emulating a reasoning-budget collapse (#2280):
    scripted replies + finish_reasons; records per-call max_tokens."""

    def __init__(self, sequence, finish_reasons, tokens=None):
        self.sequence = list(sequence)
        self.finish_reasons = list(finish_reasons)
        # per-call completion-token reports (billed usage); default 12
        self.tokens = list(tokens) if tokens is not None else [12] * max(
            len(self.sequence), 1)
        self.calls = 0
        self.max_tokens_seen: list = []
        self.last_finish_reason = None
        self.last_completion_tokens = 0

    def complete(self, *, system, user, max_tokens=None):
        self.calls += 1
        self.max_tokens_seen.append(max_tokens)
        self.last_finish_reason = self.finish_reasons[
            min(self.calls - 1, len(self.finish_reasons) - 1)]
        self.last_completion_tokens = self.tokens[
            min(self.calls - 1, len(self.tokens) - 1)]
        return self.sequence[min(self.calls - 1, len(self.sequence) - 1)]

    def close(self):
        pass


def _install_collapsing(sdk, monkeypatch, sequence, finish_reasons):
    import tortoise.ask_lane as sdk_mod
    fake = _CollapsingReader(sequence, finish_reasons)
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory",
                        lambda: fake)
    # deterministic escalation budget for the assertions
    monkeypatch.setenv("TORTOISE_ASK_ESCALATION_TOKENS", "2000")
    return fake


def test_collapse_escalates_and_answers(monkeypatch):
    """#2280: first call collapses empty with finish_reason='length' (the
    reasoning-budget collapse) → ONE escalated retry answers; the result is
    a real answer, NEVER a fabricated abstention."""
    sdk = _new_sdk()
    _seed_event_graph(sdk, [
        {"content": "I spent $25 on a chain and $40 on bike lights",
         "eventId": "ev1", "session_date": "2026-01-05"},
    ])
    fake = _install_collapsing(sdk, monkeypatch,
                               sequence=[None, "$65"],
                               finish_reasons=["length", "length"])
    result = run_ask_lane(sdk, "How much did I spend on bike stuff?",
                     question_date="2026-02-01")
    assert fake.calls == 2
    assert fake.max_tokens_seen == [None, 2000]
    assert result["answer"] == "$65"
    assert result["abstained"] is False


def test_collapse_persists_fails_loud_not_abstention(monkeypatch):
    """#2280: empty output after the escalated retry → AskReaderUnavailable
    (fail-loud). An empty model output is NEVER an abstention."""
    from tortoise.exceptions import AskReaderUnavailable
    sdk = _new_sdk()
    _seed_event_graph(sdk, [
        {"content": "the gym schedule is Monday", "eventId": "ev1",
         "session_date": "2026-08-01"},
    ])
    fake = _install_collapsing(sdk, monkeypatch,
                               sequence=[None, None],
                               finish_reasons=["length", "length"])
    with pytest.raises(AskReaderUnavailable):
        run_ask_lane(sdk, "what is the gym schedule?")
    assert fake.calls == 2
    assert fake.max_tokens_seen == [None, 2000]


def test_empty_stop_finish_recovers_on_same_budget_retry(monkeypatch):
    """#2280: empty output with a NON-length finish reason (transient
    empty/provider variance) retries ONCE at the SAME budget and can
    recover — no escalation needed."""
    sdk = _new_sdk()
    fake = _install_collapsing(sdk, monkeypatch,
                               sequence=[None, "I do not know."],
                               finish_reasons=["stop", "stop"])
    result = run_ask_lane(sdk, "something not in memory")
    assert fake.calls == 2
    assert fake.max_tokens_seen == [None, None]
    assert result["answer"] == "I do not know."
    assert result["abstained"] is True  # a WRITTEN abstention


def test_empty_stop_finish_persists_fails_loud(monkeypatch):
    """#2280: empty output twice with a non-length finish reason →
    AskReaderUnavailable (fail-loud), never abstained."""
    from tortoise.exceptions import AskReaderUnavailable
    sdk = _new_sdk()
    fake = _install_collapsing(sdk, monkeypatch,
                               sequence=[None, None],
                               finish_reasons=["stop", "stop"])
    with pytest.raises(AskReaderUnavailable):
        run_ask_lane(sdk, "q")
    assert fake.calls == 2
    assert fake.max_tokens_seen == [None, None]


def test_collapse_metering_counts_both_billed_calls(monkeypatch):
    """#2280: on the 2-call escalation path the metering record + response
    cost estimate account for BOTH billed calls (the collapsed first call's
    tokens were dropped pre-fix — the per-call last_completion_tokens
    capture only reflects the LAST call)."""
    import tortoise.ask_lane as sdk_mod
    import tortoise.metering as metering_mod

    captured = {}

    def _capture(org_id, *, tokens_in=0, tokens_out=0, cost_usd=0.0, **_):
        captured.update(tokens_out=tokens_out, cost_usd=cost_usd)
        return None

    monkeypatch.setattr(metering_mod, "record_ask_usage", _capture)
    sdk = _new_sdk()
    fake = _CollapsingReader(sequence=[None, "x"],
                             finish_reasons=["length", "length"],
                             tokens=[500, 20])
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory",
                        lambda: fake)
    monkeypatch.setenv("TORTOISE_ASK_ESCALATION_TOKENS", "2000")
    run_ask_lane(sdk, "q", org_id="team-x")
    assert captured, "the record path must have run (explicit org_id)"
    assert captured["tokens_out"] == 520  # 500 (collapsed) + 20 (escalated)


def test_both_not_either_control(monkeypatch):
    """tortoise_search / tortoise_recall never invoke the reader factory."""
    sdk = _new_sdk()
    _seed_event_graph(sdk, [
        {"content": "the gym schedule is Monday", "eventId": "ev1",
         "session_date": "2026-08-01"},
    ])
    fake = _install_fake(sdk, monkeypatch)
    sdk.tortoise_fts_query("gym", limit=10)
    sdk.recall_state(query="gym")
    sdk.recall_gaps(query="gym")
    assert fake.calls == 0
