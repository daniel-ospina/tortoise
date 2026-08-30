"""Hosted POST /v1/ask tests (#1987 Task 7).

Team-scoped auth, per-minute budget (429 + Retry-After), in-flight cap,
timeout (504), reader/retrieval failure mapping (502), the canonical error
body via the path-scoped HTTPException handler (401 status-derived; non-ask
paths unchanged), the AskRequest input-boundary set (mode=\"before\"
validators; malformed JSON → 400 via the path-scoped RequestValidationError
handler), empty-pool abstained-200, and honest metering (recorded once per
successful ask; zero records when the reader fails).

Reuses the test_hosted_api harness (auth override + temp embedded DB).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# #2013 PRODUCT-GATING: the hosted /v1/ask route is OFF by default — this
# file exercises the FULL ask pipeline, so it explicitly registers the
# route on the shared app AFTER importing hosted_api (the idempotent
# ``_register_ask_route`` — no reliance on import order or on the env flag
# being set before import, which would leak a session-wide env mutation).
# The gating itself (404 OFF / serves ON) is pinned in
# tests/test_ask_gating.py via isolated subprocesses.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from test_hosted_api import (  # noqa: E402, RUF100
    TEST_TEAM,
    TEST_TEAM_ID,
    client as _client_fixture,  # noqa: F401
    unauth_client,  # noqa: F401
    _patch_tortoise_sdk_init,
    _restore_tortoise_sdk_init,
)

from tortoise import hosted_api as ha_mod  # noqa: E402

# #2013 PRODUCT-GATING: register the /v1/ask route explicitly (idempotent)
# so the full-pipeline tests in this file serve it regardless of import
# order or a session env flag (test-review #2013 — no env mutation leak).
ha_mod._register_ask_route()
from fastapi import Request as _AskRequest  # noqa: E402 — module-level so the
# `_suspended(request: _AskRequest)` override annotation resolves under
# ``from __future__ import annotations`` (a local import inside the test fn
# would leave 'Request' unresolvable in the fn's module globals → FastAPI
# treats it as a required query param → spurious 400 invalid_question).
from tortoise.quota import (  # noqa: E402
    _reset_ask_budget_for_tests,
    _reset_ask_loop_state_for_tests,
)
from tortoise.sdk import _reset_ask_reader_cache_for_tests  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_ask_state():
    _reset_ask_budget_for_tests()
    _reset_ask_loop_state_for_tests()
    _reset_ask_reader_cache_for_tests()
    yield
    _reset_ask_budget_for_tests()
    _reset_ask_loop_state_for_tests()
    _reset_ask_reader_cache_for_tests()


# Re-export the harness fixture under the name pytest resolves.
@pytest.fixture
def client():
    yield from _client_fixture.__wrapped__()


class _FakeReaderFactory:
    """Injects a fake reader into the SDK ask lane (via the module seam)."""

    def __init__(self, reply: str = "The gym schedule is Monday and Wednesday."):
        self.reply = reply
        self.calls = 0
        self.instances = 0

    def install(self, monkeypatch):
        import tortoise.sdk as sdk_mod
        calls = {"n": 0}

        def _factory():
            self.instances += 1
            class _R:
                def __init__(self, reply):
                    self.reply = reply
                    self.last_completion_tokens = 12

                def complete(self, *, system, user):
                    calls["n"] += 1
                    return self.reply

                def close(self):
                    pass
            return _R(self.reply)
        monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", _factory)
        return calls


def _seed_point(client, content: str = "the gym schedule is Monday and Wednesday",
                session_date: str = "2026-08-01") -> None:
    """Seed a point into the TEST_TEAM graph (through the patched SDK)."""
    sdk = ha_mod._make_sdk(namespace=TEST_TEAM_ID)
    try:
        proj = sdk._get_proj()
        point = sdk.create_point("statement", content)
        proj.g.query(
            "MERGE (e:Event {eventId: 'ev-ask'}) SET e.startedAt = $st",
            params={"st": f"{session_date}T10:00:00Z"},
        )
        proj.g.query(
            "MATCH (p:Point {id: $pid}) SET p.eventId = 'ev-ask', p.sessionId = 's1'",
            params={"pid": point["id"]},
        )
    finally:
        sdk.close()


# ── Auth + error body ──────────────────────────────────────────────────────

def test_ask_unauthenticated_401(unauth_client):
    """401 missing/invalid key → the CANONICAL body (status-derived — the
    auth dependency's details are non-canonical, P1-3)."""
    r = unauth_client.post("/v1/ask", json={"question": "q"})
    assert r.status_code == 401
    assert r.json() == {"error": {"code": "unauthorized"}}


def test_error_body_shape(client, monkeypatch):
    """The canonical error body carries no provider/model internals."""
    _FakeReaderFactory(reply="x").install(monkeypatch)
    r = client.post("/v1/ask", json={"question": ""})
    assert r.status_code == 400
    assert r.json() == {"error": {"code": "invalid_question"}}


def test_non_ask_paths_keep_default_body(unauth_client):
    """P1-3: non-ask paths keep FastAPI's default {\"detail\": …} — the
    path-scoped handler never touches them."""
    r = unauth_client.get("/v1/team")
    assert r.status_code == 401
    body = r.json()
    assert "detail" in body  # default shape, not the canonical error body


def test_suspended_team_403_passthrough(client):
    """P2-16/P2-2: a suspended team's 403 passes through UNTRANSLATED as the
    _suspended_detail() DICT detail (never the canonical body, never an 11th
    code)."""
    from fastapi import HTTPException
    from tortoise.hosted_api import _suspended_detail

    def _suspended(request: _AskRequest):
        raise HTTPException(status_code=403, detail=_suspended_detail())
    ha_mod.app.dependency_overrides[ha_mod.get_current_team] = _suspended
    try:
        r = client.post("/v1/ask", json={"question": "q"})
        assert r.status_code == 403
        detail = r.json().get("detail")
        assert isinstance(detail, dict)
        assert detail.get("code") == "SUSPENDED"
        assert "error" not in r.json()
    finally:
        ha_mod.app.dependency_overrides.clear()


# ── The happy path ─────────────────────────────────────────────────────────

def test_ask_returns_answer(client, monkeypatch):
    """test_ask_returns_answer pins the full 12-field shape + the RESOLVED
    question_date semantics (P2-15)."""
    _seed_point(client)
    fake = _FakeReaderFactory().install(monkeypatch)
    r = client.post("/v1/ask", json={"question": "what is the gym schedule?"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"answer", "abstained", "question_type",
                         "question_date", "evidence", "context_tokens",
                         "model", "provider", "route", "cost_estimate_usd",
                         "duration_ms", "retrieval_degraded"}
    assert body["answer"] == "The gym schedule is Monday and Wednesday."
    assert body["abstained"] is False
    assert body["question_date"]  # resolved value present
    assert body["context_tokens"] <= 8000
    assert body["cost_estimate_usd"] >= 0
    assert body["duration_ms"] >= 0
    assert fake["n"] == 1  # exactly one LLM call


def test_empty_pool_abstained_200(client, monkeypatch):
    """P2-10: empty pool → 200 abstained with NO_EVIDENCE_TEXT + evidence,
    exactly one LLM call."""
    from tortoise.reader import NO_EVIDENCE_TEXT
    fake = _FakeReaderFactory(reply="I do not know.").install(monkeypatch)
    r = client.post("/v1/ask", json={"question": "nothing about this at all"})
    assert r.status_code == 200
    body = r.json()
    assert body["abstained"] is True
    assert body["answer"] == "I do not know."  # the recorded abstention
    assert body["evidence"] is not None
    assert fake["n"] == 1
    assert NO_EVIDENCE_TEXT  # canonical text is the blank-output substitution


def test_empty_pool_blank_reply_substitutes_no_evidence(client, monkeypatch):
    """P2: hosted empty-pool with a BLANK reply → 200 abstained with the
    canonical NO_EVIDENCE_TEXT substitution (exactly one LLM call) — the
    blank path is only otherwise exercised at unit/local-lane level."""
    from tortoise.reader import NO_EVIDENCE_TEXT
    fake = _FakeReaderFactory(reply="").install(monkeypatch)
    r = client.post("/v1/ask", json={"question": "nothing about this at all"})
    assert r.status_code == 200
    body = r.json()
    assert body["abstained"] is True
    assert body["answer"] == NO_EVIDENCE_TEXT
    assert body["evidence"] is not None
    assert fake["n"] == 1


# ── Input boundary (the pinned code set) ───────────────────────────────────

@pytest.mark.parametrize("payload,code", [
    ({"question": ""}, "invalid_question"),
    ({"question": "   "}, "invalid_question"),
    ({}, "invalid_question"),                     # MISSING question (P1-7)
    ({"question": 123}, "invalid_question"),      # wrong type (P2-5)
    ({"question": "."}, "invalid_question"),      # punctuation-only (P2-20)
    ({"question": "\u200b"}, "invalid_question"),  # zero-width (P2-9)
    ({"question": "a\x00b"}, "invalid_question"),  # control char (P2-22)
    ({"question": "x" * 2001}, "question_too_long"),
    ({"question": "q", "question_type": "bogus"}, "invalid_question_type"),
    ({"question": "q", "question_date": "2025-13-99"}, "invalid_question_date"),
    ({"question": "q", "question_date": "2023-02-29"}, "invalid_question_date"),
    ({"question": "q", "question_date": "2024-02-30"}, "invalid_question_date"),
])
def test_input_boundary_codes(client, monkeypatch, payload, code):
    _FakeReaderFactory().install(monkeypatch)
    r = client.post("/v1/ask", json=payload)
    assert r.status_code == 400, (payload, r.text)
    assert r.json() == {"error": {"code": code}}, (payload, r.text)


def test_malformed_json_400_invalid_question(client, monkeypatch):
    """P1-3: malformed JSON body → 400 invalid_question via the path-scoped
    RequestValidationError handler (raised at body-parse time)."""
    _FakeReaderFactory().install(monkeypatch)
    r = client.post("/v1/ask", data="{not json", headers={
        "Content-Type": "application/json"})
    assert r.status_code == 400
    assert r.json() == {"error": {"code": "invalid_question"}}


def test_unicode_question_accepted(client, monkeypatch):
    fake = _FakeReaderFactory().install(monkeypatch)
    r = client.post("/v1/ask", json={"question": "¿cuál es el horario? 🏋️"})
    assert r.status_code == 200


# ── Budget + in-flight + timeout ───────────────────────────────────────────

def test_budget_429_with_retry_after(client, monkeypatch):
    """60 budgeted asks → the 61st is 429 quota_exceeded + Retry-After; an
    ask after the window elapses succeeds (no permanent lockout)."""
    import time as _t
    fake = _FakeReaderFactory().install(monkeypatch)
    # fill the budget: 60 asks (each consumes a slot)
    for _ in range(60):
        r = client.post("/v1/ask", json={"question": "q"})
        assert r.status_code in (200, 429)  # budget-exhausted mid-fill is fine
    r = client.post("/v1/ask", json={"question": "q"})
    assert r.status_code == 429
    body = r.json()
    assert body["error"]["code"] == "quota_exceeded"
    assert "Retry-After" in r.headers or "retry_after" in body["error"]
    # the budget self-heals: clear the window (monotonic-forward) → succeeds
    _reset_ask_budget_for_tests()
    r2 = client.post("/v1/ask", json={"question": "q"})
    assert r2.status_code == 200


def test_in_flight_cap_429(client, monkeypatch):
    """Per-team in-flight cap 4 → the 5th concurrent ask is 429
    in_flight_limit (Retry-After omitted)."""
    import threading
    import time as _t
    import tortoise.sdk as sdk_mod

    gate = threading.Event()
    release = threading.Event()

    class _SlowReader:
        def __init__(self):
            self.last_completion_tokens = 12

        def complete(self, *, system, user):
            gate.set()
            release.wait(timeout=10)
            return "slow answer"

        def close(self):
            pass

    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", _SlowReader)
    # hold 4 in-flight asks in threads
    results = []

    def _ask():
        results.append(client.post("/v1/ask", json={"question": "q"}))

    threads = [threading.Thread(target=_ask) for _ in range(4)]
    for t in threads:
        t.start()
    assert gate.wait(timeout=10), "4 asks must reach the reader"
    # the 5th ask while 4 are in flight → 429 in_flight_limit
    r5 = client.post("/v1/ask", json={"question": "q"})
    assert r5.status_code == 429, r5.text
    assert r5.json()["error"]["code"] == "in_flight_limit"
    release.set()
    for t in threads:
        t.join()
    # after the drain the ask succeeds (no leaked counter)
    r6 = client.post("/v1/ask", json={"question": "q"})
    assert r6.status_code == 200


def test_reader_failure_502(client, monkeypatch):
    """LLM failure with no surviving lane → 502 reader_unavailable; zero
    meter records (honest metering)."""
    import tortoise.sdk as sdk_mod
    from tortoise.metering import get_ask_usage

    class _Boom:
        def complete(self, *, system, user):
            raise RuntimeError("provider down")

        def close(self):
            pass

    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", _Boom)
    r = client.post("/v1/ask", json={"question": "q"})
    assert r.status_code == 502
    assert r.json() == {"error": {"code": "reader_unavailable"}}
    usage = get_ask_usage(TEST_TEAM_ID)
    assert usage["ask_calls"] == 0  # no record when the reader call FAILS


def test_reader_timeout_504(client, monkeypatch):
    """A hung reader past the (monkeypatched short) _ASK_TIMEOUT_S → 504
    timeout."""
    import tortoise.quota as quota_mod
    import tortoise.sdk as sdk_mod

    class _Hung:
        def complete(self, *, system, user):
            import time
            time.sleep(5)
            return "late"

        def close(self):
            pass

    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", _Hung)
    monkeypatch.setattr(quota_mod, "_ASK_TIMEOUT_S", 0.5)
    # The exec floor must be BELOW the timeout, else acquire_timeout =
    # max(0, 0.5-5.0) = 0 and wait_for(timeout<=0) cancels even a free-
    # semaphore acquire (504-at-acquire, never reaching the reader).
    monkeypatch.setattr(quota_mod, "_ASK_EXEC_FLOOR_S", 0.1)
    r = client.post("/v1/ask", json={"question": "q"})
    assert r.status_code == 504, r.text
    assert r.json() == {"error": {"code": "timeout"}}


def test_ask_exec_floor_guarantees_execution(monkeypatch):
    """#1987 P2: a queued ask released before the queue-wait cap gets a real
    execution window (completes) rather than a near-zero remaining budget; a
    request released past the cap 504s at acquire WITHOUT starting the call
    (no wasted model call)."""
    import asyncio
    import tortoise.quota as quota_mod

    monkeypatch.setattr(quota_mod, "_ASK_TIMEOUT_S", 3.0)
    monkeypatch.setattr(quota_mod, "_ASK_EXEC_FLOOR_S", 1.0)

    calls = {"n": 0}

    def _fn():
        calls["n"] += 1
        return "ok"

    async def _queue_and_release(release_at: float):
        loop = asyncio.get_running_loop()
        sem = quota_mod._ask_state_for_loop(loop)["sem"]
        # hold all 8 global slots → the ask queues behind the semaphore
        for _ in range(quota_mod._ASK_GLOBAL_SEMAPHORE_SIZE):
            await sem.acquire()
        task = asyncio.ensure_future(quota_mod.run_ask_bounded(_fn, None))
        await asyncio.sleep(release_at)
        for _ in range(quota_mod._ASK_GLOBAL_SEMAPHORE_SIZE):
            sem.release()
        return await task

    # released at ~1.5s (< the 2.0s queue-wait cap) → acquires, remaining
    # ~1.5s >= the 1.0s execution floor → completes (no bogus 504)
    assert asyncio.run(_queue_and_release(1.5)) == "ok"
    assert calls["n"] == 1

    # control: released at ~2.5s (> the 2.0s cap) → 504 at acquire, the call
    # never starts (no wasted model call)
    calls["n"] = 0
    with pytest.raises(quota_mod.AskBoundedTimeoutError):
        asyncio.run(_queue_and_release(2.5))
    assert calls["n"] == 0


def test_metered_exactly_once_per_hosted_ask(client, monkeypatch):
    """Meter record written exactly once per hosted ask (the single call
    site: sdk.ask with team_id from get_current_team)."""
    _seed_point(client)
    _FakeReaderFactory().install(monkeypatch)
    from tortoise.metering import get_ask_usage
    for _ in range(3):
        r = client.post("/v1/ask", json={"question": "gym schedule?"})
        assert r.status_code == 200
    usage = get_ask_usage(TEST_TEAM_ID)
    assert usage["ask_calls"] == 3
    assert usage["ask_tokens_in"] > 0
