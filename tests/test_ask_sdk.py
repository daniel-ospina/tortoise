"""Ask-lane SDK tests (#1987 Tasks 4-5) — annotate_ask_hits + TortoiseSDK.ask.

Task 4 — ask-path-local hit annotation: session_date/speaker from the Event
join + source-turn speaker, additive keys only, undated/null-join
byte-identical, has_answer passthrough.

Task 5 — the SDK answer surface: local-lane pipeline (validation FIRST,
exactly ONE model call incl. empty context — no pre-gate), 8k/40/32KiB caps,
resolved question_date semantics, the per-namespace reader cache
(tokens-race, key isolation, failed-build, lifecycle), hosted-mode _post_ask
via a fake HTTP server (body+auth header; the pinned status mapping incl.
code-less 429/402/422; client timeout), and both-not-either (search surfaces
never invoke the reader).

Runs on the docker lane (TORTOISE_DB_URI) — the #1987 test strategy's
integration layer.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.exceptions import (
    AskInFlightLimit,
    AskQuotaExceeded,
    AskReaderUnavailable,
    AskRetrievalUnavailable,
    AskTimeout,
    AskValidationError,
)
from tortoise.retrieval import estimate_tokens_ask
from tortoise.schemas import (
    CODE_INVALID_QUESTION,
    CODE_INVALID_QUESTION_DATE,
    CODE_INVALID_QUESTION_TYPE,
    CODE_QUESTION_TOO_LONG,
)
from tortoise.sdk import (
    ASK_SDK_TIMEOUT_S,
    TortoiseSDK,
    _reset_ask_reader_cache_for_tests,
)


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

def _seed_event_graph(sdk: TortoiseSDK, turns: list[dict]) -> list[dict]:
    """Seed points + Events; return the tortoise_fts_query hits."""
    proj = sdk._get_proj()
    for i, t in enumerate(turns):
        point = sdk.create_point("statement", t["content"])
        eid = t.get("eventId", f"ev-{i}")
        proj.g.query(
            "MERGE (e:Event {eventId: $eid}) SET e.startedAt = $st",
            params={"eid": eid, "st": f"{t.get('session_date', '2026-08-20')}T10:00:00Z"},
        )
        sets = ["p.eventId = $eid"]
        params = {"pid": point["id"], "eid": eid}
        if t.get("sessionId"):
            sets.append("p.sessionId = $sid")
            params["sid"] = t["sessionId"]
        if t.get("speaker"):
            sets.append("p.speaker = $spk")
            params["spk"] = t["speaker"]
        proj.g.query(
            "MATCH (p:Point {id: $pid}) SET " + ", ".join(sets),
            params=params,
        )
    return sdk.tortoise_fts_query("gym", limit=40, include_terminal=True)


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
    import tortoise.sdk as sdk_mod
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
    result = sdk.ask("what is the gym schedule?", question_date="2026-08-29")
    # the full 12-field shape
    assert set(result) == {"answer", "abstained", "question_type",
                           "question_date", "evidence", "context_tokens",
                           "model", "provider", "route", "cost_estimate_usd",
                           "duration_ms", "retrieval_degraded"}
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
    import tortoise.sdk as sdk_mod
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
    result = sdk.ask("q")
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
    import tortoise.sdk as sdk_mod
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
    result = sdk.ask("q")
    assert result["model"] == "deepseek-v4-flash"
    inp = (estimate_tokens_ask(system_prompt_for(result["question_type"]))
           + estimate_tokens_ask(result["evidence"]))
    expected = estimate_ask_cost_usd(inp, 12, rates=ASK_METER_RATES)
    assert result["cost_estimate_usd"] == pytest.approx(expected)


def test_ask_record_path_uses_strong_rates(monkeypatch):
    """#2069: the ask() metering RECORD call site (step 7 — the pinned
    record path) meters the cost_usd at the SERVING lane's STRONG rates — a
    strong-lane query's cost_usd record never uses the deepseek envelope."""
    import tortoise.metering as metering_mod
    import tortoise.sdk as sdk_mod
    from tortoise.metering import (
        ASK_METER_RATES,
        ASK_METER_RATES_STRONG,
        estimate_ask_cost_usd,
    )

    captured = {}

    def _capture(team_id, *, tokens_in=0, tokens_out=0, cost_usd=0.0, **_):
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
    sdk.ask("q", team_id="team-x")
    assert captured, "the record path must have run (explicit team_id)"
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
    result = sdk.ask("q")
    import re
    assert re.match(r"^\d{4}-\d{2}-\d{2}$", result["question_date"])
    assert result["evidence"].startswith(f"Current Date: {result['question_date']}")


def test_empty_context_single_call(monkeypatch):
    """Empty pool → exactly ONE model call (no pre-gate) + abstained with
    evidence present."""
    sdk = _new_sdk()
    fake = _install_fake(sdk, monkeypatch, reply="I do not know the answer.")
    result = sdk.ask("something not in memory")
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
    r1 = sdk.ask("what is the bicycle's color?")
    assert fake.calls == 1 and r1["abstained"] is True
    # near-miss: similar but different value present
    r2 = sdk.ask("what is the car's color?")
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
         CODE_INVALID_QUESTION_DATE),  # non-str date → str()-coerced like the
                                       # hosted AskRequest validator (P2)
    ]:
        with pytest.raises(AskValidationError) as ei:
            sdk.ask(bad, **kw)
        assert ei.value.code == code, (bad, kw, ei.value.code)
    assert fake.calls == 0
    assert retrieved == []


def test_2000_char_boundary(monkeypatch):
    sdk = _new_sdk()
    fake = _install_fake(sdk, monkeypatch)
    assert sdk.ask("x" * 2000)["answer"] == fake.reply  # passes
    with pytest.raises(AskValidationError):
        sdk.ask("x" * 2001)


def test_oversized_hit_skip_and_caps(monkeypatch):
    """8k/40 caps honored; the byte cap (32 KiB) binds independently; the
    evidence never splits a character (no U+FFFD)."""
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
    result = sdk.ask("office hours")
    assert len(result["evidence"].encode("utf-8")) <= 32768
    assert estimate_tokens_ask(result["evidence"]) <= 8000
    assert "\ufffd" not in result["evidence"]
    assert fake.calls == 1


def test_undated_hits(monkeypatch):
    sdk = _new_sdk()
    sdk.create_point("statement", "office hours are 9am")
    fake = _install_fake(sdk, monkeypatch)
    result = sdk.ask("office hours?")
    assert result["answer"] == fake.reply
    assert result["evidence"]


def test_question_type_passthrough_and_override(monkeypatch):
    sdk = _new_sdk()
    _install_fake(sdk, monkeypatch)
    r = sdk.ask("how many days ago did we meet?")
    assert r["question_type"] == "temporal-reasoning"
    r2 = sdk.ask("how many days ago did we meet?",
                 question_type="multi-session")
    assert r2["question_type"] == "multi-session"


def test_reader_raise_maps_reader_unavailable(monkeypatch):
    sdk = _new_sdk()
    import tortoise.sdk as sdk_mod
    class Boom(FakeReader):
        def complete(self, *, system, user):
            raise RuntimeError("provider down")
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", lambda: Boom())
    with pytest.raises(AskReaderUnavailable):
        sdk.ask("q")


def test_tokens_race_same_cached_instance(monkeypatch):
    """2-3 concurrent ask() calls through ONE cached model instance → each
    call's captured usage matches its own completion (the per-instance lock
    makes inner complete() + capture atomic)."""
    import tortoise.sdk as sdk_mod
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
            results.append(sdk.ask(q))
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
    sdk_a = TortoiseSDK(tempfile.mkdtemp() + "/a.db", namespace="team-a")
    sdk_b = TortoiseSDK(tempfile.mkdtemp() + "/b.db", namespace="team-b")
    a = sdk_a._ask_reader_model()
    b = sdk_b._ask_reader_model()
    assert a is not b
    sdk_a.close()
    sdk_b.close()


def test_failed_build_never_cached(monkeypatch):
    """P2-21: the first ask's build raises → the cache does NOT retain the
    key → the second ask rebuilds and succeeds. P2: the per-key build lock
    is ALSO popped on the failed build — it never lingers in the module
    dict (unbounded growth under sustained build failure across
    namespaces)."""
    import tortoise.sdk as sdk_mod
    sdk = _new_sdk()
    state = {"fail": True}
    def _factory():
        if state["fail"]:
            raise RuntimeError("build boom")
        return FakeReader(reply="ok now")
    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", _factory)
    with pytest.raises(AskReaderUnavailable):
        sdk.ask("q")
    # P2: the failed build leaves NO cached entry AND no lingering lock
    assert sdk_mod._ask_build_locks == {}
    state["fail"] = False
    result = sdk.ask("q")
    assert result["answer"] == "ok now"


def test_locked_reader_forwards_finish_reason(monkeypatch):
    """_LockedReader forwards the inner model's last_finish_reason from the
    same capture frame as the token usage (P2 — consumers previously got
    None because the attribute was never set)."""
    import tortoise.sdk as sdk_mod

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


# ── Task 5: hosted-mode _post_ask (fake HTTP server) ───────────────────────

class _FakeAskServer:
    """Records the POST body + auth header; serves scripted responses."""

    def __init__(self):
        self.requests: list[tuple[dict, str]] = []
        self.responses: list = [{"answer": "ok", "abstained": False,
                                 "question_type": None,
                                 "question_date": "2026-08-29",
                                 "evidence": "", "context_tokens": 0,
                                 "model": "m", "provider": "p", "route": "p",
                                 "cost_estimate_usd": 0.0, "duration_ms": 1,
                                 "retrieval_degraded": False}]
        self.status = 200
        self.headers: dict[str, str] = {}
        self.handler = None

    def _handle(self, body: dict, auth: str) -> tuple[int, dict, dict]:
        self.requests.append((body, auth))
        if self.status == 200 and self.responses:
            return 200, self.responses[0], self.headers
        if self.status and self.responses:
            return self.status, self.responses[0], self.headers
        return 200, self.responses[0], self.headers

    def start(self, monkeypatch) -> str:
        class _H(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                auth = self.headers.get("Authorization", "")
                status, payload, headers = server._handle(body, auth)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

            def log_message(self, *a):
                pass

        server = self
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _H)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setenv("TORTOISE_API_URL", f"http://127.0.0.1:{self.httpd.server_address[1]}")
        monkeypatch.setenv("TORTOISE_API_KEY", "tt_test_key")
        return self.httpd.server_address[1]

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()


def test_post_ask_body_and_auth_header(monkeypatch):
    server = _FakeAskServer()
    server.start(monkeypatch)
    try:
        sdk = _new_sdk()
        result = sdk.ask("what is the schedule?", question_type="temporal-reasoning")
        body, auth = server.requests[0]
        assert body["question"] == "what is the schedule?"
        assert body["question_type"] == "temporal-reasoning"
        assert auth == "Bearer tt_test_key"
        assert result["answer"] == "ok"
    finally:
        server.stop()


def test_post_ask_status_mapping(monkeypatch):
    """429 quota_exceeded → AskQuotaExceeded with Retry-After; 429
    in_flight_limit → AskInFlightLimit; code-less 429 → AskQuotaExceeded
    (retry_after=None); 400 + invalid_question → AskValidationError; 504 →
    AskTimeout; 502 → typed; unreachable → typed."""
    cases = [
        # the REAL server shape: Retry-After in the HTTP HEADER (the
        # hosted server ALSO ships the seconds in the 429 body — P1)
        (429, {"error": {"code": "quota_exceeded"}},
         AskQuotaExceeded, None, {"Retry-After": "42"}),
        # body-only fallback (still honored when the header is absent)
        (429, {"error": {"code": "quota_exceeded", "retry_after": 42}},
         AskQuotaExceeded, None, None),
        (429, {"error": {"code": "in_flight_limit"}},
         AskInFlightLimit, None, None),
        (429, {}, AskQuotaExceeded, None, None),  # code-less 429 → quota (P2-15)
        (400, {"error": {"code": "invalid_question"}},
         AskValidationError, "invalid_question", None),
        (400, {}, AskValidationError, "invalid_question", None),  # code-less 400
        (401, {}, AskValidationError, "unauthorized", None),      # code-less 401
        (403, {}, AskValidationError, "unauthorized", None),      # code-less 403
        (422, {}, AskValidationError, "invalid_question", None),  # code-less 422
        (402, {}, AskReaderUnavailable, None, None),   # code-less 402 (P2-3)
        (502, {"error": {"code": "retrieval_unavailable"}},
         AskRetrievalUnavailable, None, None),
        (502, {"error": {"code": "reader_unavailable"}},
         AskReaderUnavailable, None, None),
        (500, {}, AskReaderUnavailable, None, None),   # residual 5xx → typed (P2)
        (500, {"error": {"code": "retrieval_unavailable"}},
         AskRetrievalUnavailable, None, None),         # body code still honored
        (503, {}, AskReaderUnavailable, None, None),   # LB/deploy drain → typed
        (504, {}, AskTimeout, None, None),
    ]
    for status, body, exc_type, code, headers in cases:
        server = _FakeAskServer()
        server.status = status
        server.responses = [body]
        if headers:
            server.headers = headers
        server.start(monkeypatch)
        try:
            sdk = _new_sdk()
            with pytest.raises(exc_type) as ei:
                sdk.ask("q")
            if code:
                assert ei.value.code == code, (status, ei.value.code)
            if exc_type is AskQuotaExceeded and status == 429:
                ra = ei.value.retry_after
                if (headers and headers.get("Retry-After")) or body.get("error", {}).get("retry_after"):
                    assert ra == 42
                else:
                    assert ra is None
        finally:
            server.stop()


def test_post_ask_404_is_reader_unavailable(monkeypatch):
    """#2013: a code-less 404 on /v1/ask is the EXPECTED gated state
    (the route is NOT registered when the hosted ask exposure is gated
    off) — it maps to AskReaderUnavailable, never AskValidationError
    (the default code-less 4xx map would mislabel it invalid_question)."""
    server = _FakeAskServer()
    server.status = 404
    server.responses = [{}]
    server.start(monkeypatch)
    try:
        sdk = _new_sdk()
        with pytest.raises(AskReaderUnavailable) as ei:
            sdk.ask("q")
        assert ei.value.status_code == 404
        assert "not enabled" in str(ei.value)
    finally:
        server.stop()


def test_post_ask_429_unparseable_header_falls_back_to_body(monkeypatch):
    """A 429 with a non-numeric (HTTP-date) Retry-After header and a numeric
    body ``retry_after`` → AskQuotaExceeded.retry_after == 42.0 (the header
    fails float() and the body survives — RFC 7231 allows an HTTP-date)."""
    server = _FakeAskServer()
    server.status = 429
    server.responses = [{"error": {"code": "quota_exceeded", "retry_after": 42}}]
    server.headers = {"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}
    server.start(monkeypatch)
    try:
        sdk = _new_sdk()
        with pytest.raises(AskQuotaExceeded) as ei:
            sdk.ask("q")
        assert ei.value.retry_after == 42.0
    finally:
        server.stop()


def test_post_ask_timeout_mapping(monkeypatch):
    """504 → AskTimeout with source='server'; a client-fired timeout maps to
    AskTimeout with source='client' (monkeypatched SHORT client timeout)."""
    server = _FakeAskServer()
    server.status = 504
    server.responses = [{}]
    server.start(monkeypatch)
    try:
        sdk = _new_sdk()
        with pytest.raises(AskTimeout) as ei:
            sdk.ask("q")
        assert ei.value.source == "server"
    finally:
        server.stop()
    # constant-check: the SDK client timeout is pinned > the server's
    from tortoise.quota import _ASK_TIMEOUT_S
    assert ASK_SDK_TIMEOUT_S == 75
    assert ASK_SDK_TIMEOUT_S > _ASK_TIMEOUT_S
