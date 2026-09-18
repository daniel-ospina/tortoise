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
import threading
import time
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
    TEST_ORG_ID,
    unauth_client,  # noqa: F401
)
from test_hosted_api import (
    client as _client_fixture,
)

from tortoise import hosted_api as ha_mod

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
from tortoise.schemas import ASK_BUSY_MESSAGE  # noqa: E402
from tortoise.sdk import _reset_ask_reader_cache_for_tests  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_ask_state(tmp_path, monkeypatch):
    """Hermetic analytics + telemetry state for EVERY test in this file.

    #3834/#3993: the ask route now emits one ``ask_request`` row per committing
    request, so this file MUST NOT write to the developer's real
    ``~/.tortoise/analytics_fallback.jsonl`` — that file is the very evidence
    the 10s bound was derived from. Pin the JSONL fallback into ``tmp_path``
    and make the Supabase branch unreachable. **The ``SUPABASE_URL`` deletion
    is the load-bearing one** (``_track_analytics_event`` short-circuits on
    ``if url and key:`` and ``_service_key()`` also honours
    ``SUPABASE_SERVICE_ROLE_KEY``, so deleting the two key names alone is not
    hermetic).
    """
    monkeypatch.setattr(ha_mod, "_ANALYTICS_FALLBACK_PATH",
                        str(tmp_path / "analytics_fallback.jsonl"))
    # The FLEET SHELL exports TORTOISE_API_URL=https://api.premiselabs.co, which
    # flips the route's SDK into REMOTE mode: the fake reader seam is never
    # reached and the test POSTs REAL requests at PRODUCTION (returning 404/504
    # nondeterministically). Without this deletion the whole file is
    # env-dependent — and the base file failed 9/29 under the fleet shell while
    # passing 29/29 with the var cleared.
    monkeypatch.delenv("TORTOISE_API_URL", raising=False)
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    _reset_ask_budget_for_tests()
    _reset_ask_loop_state_for_tests()
    _reset_ask_reader_cache_for_tests()
    # The counter is NOT asserted here: a post-reset assert is tautological and
    # a pre-reset one only fires on a benign race — an assertion that cannot
    # fail for the reason it appears to exist for is worse than none.
    ha_mod._reset_ask_telemetry_for_tests()
    yield
    # THE GUARD is this drain: it raises ``AssertionError`` naming the leftover
    # count if a write is still in flight, so a leak is reported as an ERROR at
    # the leaking test. The ``finally`` reset then bounds the blast radius to
    # one test — it must run even when the drain raises (a raise in an autouse
    # teardown would otherwise cascade to every later test).
    try:
        ha_mod._drain_ask_telemetry()
    finally:
        _reset_ask_budget_for_tests()
        _reset_ask_loop_state_for_tests()
        _reset_ask_reader_cache_for_tests()
        ha_mod._reset_ask_telemetry_for_tests()


def _ask_rows() -> list[dict]:
    """The ``ask_request`` rows written to the (tmp-pinned) JSONL fallback."""
    path = ha_mod._ANALYTICS_FALLBACK_PATH
    if not path or not os.path.exists(path):
        return []
    with open(path) as f:
        rows = [json.loads(ln) for ln in f.read().splitlines() if ln.strip()]
    return [r for r in rows if r["event_name"] == "ask_request"]


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
    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
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

def test_ask_unauthenticated_401(unauth_client):  # noqa: F811
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


def test_non_ask_paths_keep_default_body(unauth_client):  # noqa: F811
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
    ha_mod.app.dependency_overrides[ha_mod.get_current_org] = _suspended
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
    """test_ask_returns_answer pins the full 13-field shape + the RESOLVED
    question_date semantics (P2-15)."""
    _seed_point(client)
    fake = _FakeReaderFactory().install(monkeypatch)
    r = client.post("/v1/ask", json={"question": "what is the gym schedule?"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"answer", "abstained", "question_type",
                         "question_date", "evidence", "context_tokens",
                         "model", "provider", "route", "cost_estimate_usd",
                         "duration_ms", "retrieval_degraded",
                         "retrieved_session_ids"}
    assert body["answer"] == "The gym schedule is Monday and Wednesday."
    assert body["abstained"] is False
    assert body["question_date"]  # resolved value present
    assert body["context_tokens"] <= 8000
    assert body["cost_estimate_usd"] >= 0
    assert body["duration_ms"] >= 0
    assert fake["n"] == 1  # exactly one LLM call


def test_empty_pool_abstained_200(client, monkeypatch):
    """P2-10: empty pool → 200 abstained with the model's WRITTEN
    abstention + evidence, exactly one LLM call (#2280: an abstention is a
    written decision — empty output is no longer substituted as "no
    evidence", it fails loud as 502)."""
    fake = _FakeReaderFactory(reply="I do not know.").install(monkeypatch)
    r = client.post("/v1/ask", json={"question": "nothing about this at all"})
    assert r.status_code == 200
    body = r.json()
    assert body["abstained"] is True
    assert body["answer"] == "I do not know."  # the recorded abstention
    assert body["evidence"] is not None
    assert fake["n"] == 1


def test_empty_pool_blank_reply_fails_loud_502(client, monkeypatch):
    """#2280: a BLANK reader reply is a malfunction, never an abstention.

    Pre-fix, an empty model output was substituted with the canonical
    ``NO_EVIDENCE_TEXT`` abstention (200) — silently fabricating a "no
    evidence" answer on a collapsed/empty reader call. The two-phase
    prompt abstains in WRITING; an empty output is either a
    reasoning-budget collapse (``finish_reason="length"``) or a reader
    malfunction. The ask lane now retries once and fails LOUD as
    ``reader_unavailable`` (502) — the hosted surface never reads an
    empty output as an abstention."""
    from tortoise.reader import NO_EVIDENCE_TEXT
    fake = _FakeReaderFactory(reply="").install(monkeypatch)
    r = client.post("/v1/ask", json={"question": "nothing about this at all"})
    assert r.status_code == 502
    assert r.json() == {"error": {"code": "reader_unavailable"}}
    assert fake["n"] == 2  # one same-budget retry, then fail-loud
    assert NO_EVIDENCE_TEXT  # constant retained (defensive invariant only)


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
    _FakeReaderFactory().install(monkeypatch)
    r = client.post("/v1/ask", json={"question": "¿cuál es el horario? 🏋️"})
    assert r.status_code == 200


# ── Budget + in-flight + timeout ───────────────────────────────────────────

def test_budget_429_with_retry_after(client, monkeypatch):
    """60 budgeted asks → the 61st is 429 quota_exceeded + Retry-After; an
    ask after the window elapses succeeds (no permanent lockout)."""
    _FakeReaderFactory().install(monkeypatch)
    # fill the budget: 60 asks (each consumes a slot)
    for _ in range(60):
        r = client.post("/v1/ask", json={"question": "q"})
        assert r.status_code in (200, 429)  # budget-exhausted mid-fill is fine
    r = client.post("/v1/ask", json={"question": "q"})
    assert r.status_code == 429
    body = r.json()
    assert body["error"]["code"] == "quota_exceeded"
    assert "Retry-After" in r.headers
    # the documented body contract ships ``retry_after`` IN THE BODY — the
    # header alone would leave it absent (P2)
    assert "retry_after" in body["error"]
    assert body["error"]["retry_after"] == int(r.headers["Retry-After"])
    # the budget self-heals: clear the window (monotonic-forward) → succeeds
    _reset_ask_budget_for_tests()
    r2 = client.post("/v1/ask", json={"question": "q"})
    assert r2.status_code == 200


def test_in_flight_cap_429(client, monkeypatch):
    """Per-team in-flight cap 4 → the 5th concurrent ask is 429
    in_flight_limit (Retry-After omitted)."""
    import threading

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
    # #3834: the `message`/`retry_after` additions are path-scoped to the
    # bound-breach 504 — the in-flight 429 keeps its EXACT prior body (no
    # `Retry-After` header either, unlike the quota 429).
    assert r5.json() == {"error": {"code": "in_flight_limit"}}
    assert "Retry-After" not in r5.headers
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
    usage = get_ask_usage(TEST_ORG_ID)
    assert usage["ask_calls"] == 0  # no record when the reader call FAILS


def test_reader_timeout_504(client, monkeypatch):
    """A hung reader past the (monkeypatched short) ``_ASK_TIMEOUT_S`` → 504
    ``timeout`` with the #3834 legible refusal — AND a real UPPER bound on the
    response time (#3993 AC1).

    AC1 exists because ``duration_ms >= 0`` "is satisfied by every possible
    implementation including a route that takes 59 seconds". So this test
    induces the delay deterministically (monkeypatched bound + a bounded hung
    reader) and asserts the refusal arrived inside the bound plus a stated
    margin; it also pins the emitted ``duration_ms`` to the same bound.
    Red-able: removing the bound (or leaving ``_ASK_TIMEOUT_S`` at 60) makes
    the request take the reader's full sleep and the assertion fails.
    """
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

    t0 = time.monotonic()
    r = client.post("/v1/ask", json={"question": "q"})
    elapsed = time.monotonic() - t0

    # 1. The refusal contract (#3834): status, header, and the FULL body.
    assert r.status_code == 504, r.text
    assert r.headers["Retry-After"] == "2"
    assert r.json() == {"error": {"code": "timeout", "retry_after": 2,
                                  "message": ASK_BUSY_MESSAGE}}
    # header == body (the path-scoped mirror)
    assert int(r.headers["Retry-After"]) == r.json()["error"]["retry_after"]

    # 2. AC1 — a REAL upper bound (bound + a stated margin covering thread
    #    start + response serialization).
    assert elapsed < 0.5 + 2.5, f"refusal took {elapsed:.2f}s"

    # 3. AC1, second half: the PERSISTED duration is bounded too, and the
    #    refusal arm emitted its own row (status="timeout").
    ha_mod._drain_ask_telemetry()
    rows = _ask_rows()
    assert len(rows) == 1, rows
    assert rows[0]["properties"]["status"] == "timeout"
    assert rows[0]["properties"]["error_kind"] == "ask_bounded_timeout"
    assert rows[0]["properties"]["duration_ms"] <= int((0.5 + 2.5) * 1000)


# ── #3834/#3993: the emission is OFF the loop, NON-BLOCKING, and hermetic ────

def test_ask_emission_is_handed_off_the_event_loop(client, monkeypatch):
    """(a-i) Thread identity at the REAL route: the emission runs on a thread
    other than the handler's — asserted on the REFUSAL arm, which emits its own
    row so the assertion cannot ride on the 200 path.

    Includes the precedent's non-vacuity guard: if the probe never ran, the
    assertion is vacuous — so that is checked FIRST.
    """
    import tortoise.quota as quota_mod
    import tortoise.sdk as sdk_mod

    seen: dict = {}

    def _recorder(org_id, event_name, props=None):
        # Non-blocking: records the emitting THREAD and returns.
        seen["emit_thread"] = threading.get_ident()
        seen["event"] = event_name
        seen["props"] = props

    class _Hung:
        def complete(self, *, system, user):
            time.sleep(5)
            return "late"

        def close(self):
            pass

    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", _Hung)
    monkeypatch.setattr(quota_mod, "_ASK_TIMEOUT_S", 0.5)
    monkeypatch.setattr(quota_mod, "_ASK_EXEC_FLOOR_S", 0.1)
    monkeypatch.setattr(ha_mod, "_track_analytics_event", _recorder)
    # Sample the HANDLER thread by patching the module the LATE IMPORT reads —
    # `hosted_api.py` imports `run_ask_bounded` INSIDE the function body, so
    # there is no `ha_mod.run_ask_bounded` attribute to patch.
    real = quota_mod.run_ask_bounded

    async def _inline(*a, **k):
        seen["handler_thread"] = threading.get_ident()
        return await real(*a, **k)

    monkeypatch.setattr(quota_mod, "run_ask_bounded", _inline)

    r = client.post("/v1/ask", json={"question": "q"})
    assert r.status_code == 504, r.text
    ha_mod._drain_ask_telemetry()

    # Non-vacuity FIRST — an unrun probe must never read as a pass.
    assert "handler_thread" in seen and "emit_thread" in seen, \
        "the probe never ran (vacuous assertion)"
    assert seen["emit_thread"] != seen["handler_thread"]
    assert seen["event"] == "ask_request"
    assert seen["props"]["duration_ms"] >= 0


def test_ask_emission_is_non_blocking(monkeypatch):
    """(a-ii) The helper returns while the write is STILL blocked.

    Thread identity alone cannot distinguish fire-and-forget from an AWAITED
    ``asyncio.to_thread``, so this probes the property directly with a bounded,
    guaranteed-release recorder: an inline/awaited implementation blocks for
    the recorder's hold (~10s) and goes RED; the recorder's own timeout makes
    the test terminate either way.
    """
    import asyncio

    started = threading.Event()
    release = threading.Event()

    def _recorder(org_id, event_name, props=None):
        started.set()
        release.wait(timeout=10)

    monkeypatch.setattr(ha_mod, "_track_analytics_event", _recorder)

    async def _probe():
        try:
            t0 = time.monotonic()
            ha_mod._emit_ask_latency_off_path(TEST_ORG_ID, 12.0, "ok")
            elapsed = time.monotonic() - t0
            # Non-vacuous: the write really reached the recorder...
            begin = await asyncio.to_thread(started.wait, 2.0)
            assert begin, "the write never reached the recorder"
            # ...and the call had already returned. Margin: ~20x the expected
            # cost and ~1/20 of the ~10s the recorder holds.
            assert elapsed < 0.5, f"helper blocked for {elapsed:.2f}s"
        finally:
            # INSIDE the coroutine: outside asyncio.run, loop shutdown blocks
            # on the default executor while the recorder is still parked.
            release.set()
            await asyncio.to_thread(ha_mod._drain_ask_telemetry, 5.0)

    asyncio.run(_probe())


def test_ask_emission_daemon_thread_branch(monkeypatch):
    """The daemon-thread branch (no running loop) — a defensive mirror that is
    unreachable from the async route, so it needs its own test.

    It is deliberately NOT routed through ``_retain_feed_task`` (a Thread has
    no ``add_done_callback`` and ``threading._active`` already holds it), so
    this also pins that it still writes and still decrements exactly once.
    """
    caller = threading.get_ident()
    seen: dict = {}

    def _recorder(org_id, event_name, props=None):
        seen["thread"] = threading.get_ident()
        seen["props"] = props

    monkeypatch.setattr(ha_mod, "_track_analytics_event", _recorder)
    assert ha_mod._ask_telemetry_inflight() == 0
    ha_mod._emit_ask_latency_off_path(TEST_ORG_ID, 7, "ok")
    ha_mod._drain_ask_telemetry()
    assert seen["thread"] != caller
    assert seen["props"] == {"duration_ms": 7, "status": "ok"}
    assert ha_mod._ask_telemetry_inflight() == 0


def test_ask_emission_dispatch_failure_never_changes_the_status(
        client, monkeypatch):
    """R1-1/R1-6: a DISPATCH failure must not turn the pinned 504 into a 500,
    and must not leak the in-flight counter.

    Without this, a mutation reverting the swallow to ``raise`` survives the
    suite — and so would the ``_log``/``_logger`` NameError the plan-review
    caught (a raise inside the guard escapes it).
    """

    import tortoise.quota as quota_mod
    import tortoise.sdk as sdk_mod

    class _Hung:
        def complete(self, *, system, user):
            time.sleep(5)
            return "late"

        def close(self):
            pass

    monkeypatch.setattr(sdk_mod, "_default_ask_reader_factory", _Hung)
    monkeypatch.setattr(quota_mod, "_ASK_TIMEOUT_S", 0.5)
    monkeypatch.setattr(quota_mod, "_ASK_EXEC_FLOOR_S", 0.1)

    # The dispatch itself fails — no future is ever constructed. NARROWED to
    # the telemetry dispatch: `run_ask_bounded` ALSO uses run_in_executor (with
    # the wrapped callable plus args), so a blanket patch would break the ask
    # instead of the emission and the test would pass for the wrong reason.
    import asyncio as _asyncio
    _real_rie = _asyncio.BaseEventLoop.run_in_executor

    def _boom(self, executor, func, *args, **kwargs):
        if not args:
            raise RuntimeError("dispatch boom")
        return _real_rie(self, executor, func, *args, **kwargs)

    monkeypatch.setattr(_asyncio.BaseEventLoop, "run_in_executor", _boom)

    r = client.post("/v1/ask", json={"question": "q"})
    assert r.status_code == 504, r.text
    ha_mod._drain_ask_telemetry()
    assert ha_mod._ask_telemetry_inflight() == 0


def test_ask_analytics_writer_never_raises_and_is_audible(caplog):
    """R5-4: the allowlist's silent strip is now logged at WARNING, and the
    never-raise contract survives it.

    The Stripe caller (``hosted_api.py:23469``) passes ``plan``/``tier``,
    neither of which is allowlisted — so this branch runs on REAL traffic, and
    a raise here would 500 a webhook whose event marker was already claimed
    (dropping the billing notification permanently).
    """
    import logging
    with caplog.at_level(logging.WARNING, logger="tortoise.hosted_api"):
        ha_mod._track_analytics_event("org", "stripe_notify",
                                      {"plan": "pro", "tier": "pro",
                                       "status": "ok"})
    assert "analytics prop stripped" in caplog.text
    assert "plan" in caplog.text


def test_ask_analytics_writer_sends_only_allowlisted_props(monkeypatch):
    """R5-4's second half: the strip is not just LOGGED, it is still APPLIED —
    the row actually written carries only allowlisted keys (a fix that logged
    but forwarded the unknown key would leak unbounded caller props)."""
    ha_mod._track_analytics_event("org", "stripe_notify",
                                  {"plan": "pro", "tier": "pro",
                                   "status": "ok"})
    path = ha_mod._ANALYTICS_FALLBACK_PATH
    with open(path) as f:
        rows = [json.loads(ln) for ln in f.read().splitlines() if ln.strip()]
    row = rows[-1]
    # EXACT set (not a subset — a subset assertion is a tautology after the
    # allowlist filter has run, so it cannot detect the loss class this guards).
    assert row["properties"] == {"status": "ok"}, row


@pytest.mark.parametrize("branch", ["loop", "thread"])
@pytest.mark.parametrize("boom", ["decrement", "log"])
def test_ask_emission_failure_path_cannot_escape(monkeypatch, branch, boom):
    """Cycle-7 regression: on BOTH dispatch-failure paths the fallback
    decrement and the log are each inside a suppression.

    The harm: this runs on the refusal arm, so anything escaping turns the
    pinned 504 into a 500 — the exact contract the guard exists to keep. The
    decrement is not hypothetical: it logs (unsuppressed) when the counter is
    already zero, i.e. it raises wherever the log handler does.
    """
    import threading as _threading

    calls: list = []

    def _fail(*a, **k):
        calls.append(1)
        raise RuntimeError(f"{boom} boom")

    monkeypatch.setattr(ha_mod, "_track_analytics_event", lambda *a, **k: None)

    if branch == "loop":
        class _BoomLoop:
            def is_closed(self):
                return False

            def run_in_executor(self, *a, **k):
                raise RuntimeError("executor boom")

        monkeypatch.setattr(ha_mod.asyncio, "get_running_loop",
                            lambda: _BoomLoop())
    else:
        monkeypatch.setattr(ha_mod.asyncio, "get_running_loop",
                            _raise_no_loop)

        class _BoomThread:
            def __init__(self, *a, **k):
                pass

            def start(self):
                raise RuntimeError("cannot start new thread")

        class _ThreadingShim:
            Thread = _BoomThread

            def __getattr__(self, name):
                return getattr(_threading, name)

        monkeypatch.setattr(ha_mod, "threading", _ThreadingShim())

    if boom == "decrement":
        monkeypatch.setattr(ha_mod, "_ask_telemetry_decrement", _fail)
    else:
        monkeypatch.setattr(ha_mod._logger, "warning", _fail)

    try:
        # The whole point: this RETURNS. A raise here is a 504 -> 500.
        ha_mod._emit_ask_latency_off_path(TEST_ORG_ID, 5, "timeout")
        assert calls, "the probe never reached the guarded statement (vacuous)"
    finally:
        # A suppressed decrement may legitimately have leaked the counter.
        ha_mod._reset_ask_telemetry_for_tests()


def _raise_no_loop():
    raise RuntimeError("no running event loop")


def test_ask_emission_daemon_thread_start_failure(monkeypatch):
    """R1-6/R2-8: the daemon-thread branch's ``start()`` failure must be the
    same never-raise / single-decrement contract as the loop branch — a
    mutation dropping the ``finally`` decrement would leak the counter and a
    later drain would raise at an unrelated test."""
    import threading as _threading

    seen: list = []

    def _recorder(*a, **k):
        seen.append(1)

    monkeypatch.setattr(ha_mod, "_track_analytics_event", _recorder)

    class _BoomThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("cannot start new thread")

    class _ThreadingShim:
        """Confines the boom to ``hosted_api``'s module global: patching
        ``threading.Thread.start`` process-wide would also break the test
        harness' own background threads."""

        Thread = _BoomThread

        def __getattr__(self, name):
            return getattr(_threading, name)

    monkeypatch.setattr(ha_mod, "threading", _ThreadingShim())
    # Sync context → the daemon-thread branch. Never raises...
    ha_mod._emit_ask_latency_off_path(TEST_ORG_ID, 5, "ok")
    # ...the write never happened...
    assert seen == []
    # ...and the counter did not leak (the drain would raise otherwise).
    ha_mod._drain_ask_telemetry(timeout=1.0)
    assert ha_mod._ask_telemetry_inflight() == 0


def test_ask_telemetry_underflow_is_audible(caplog):
    """R5-2: a decrement with nothing in flight LOGS (mirroring
    ``_capture_slot_decrement``) instead of silently clamping — otherwise a
    leak is invisible and a drain can return with a write still running."""
    import logging
    with caplog.at_level(logging.WARNING, logger="tortoise.hosted_api"):
        ha_mod._reset_ask_telemetry_for_tests()
        ha_mod._ask_telemetry_decrement()
    assert "nothing in flight" in caplog.text
    ha_mod._reset_ask_telemetry_for_tests()


def test_ask_bound_pins():
    """The bound and ALL of its relations (the four scope-AC1 pins)."""
    import tortoise.quota as quota_mod
    from tortoise.quota import ASK_BUSY_RETRY_AFTER_S
    from tortoise.sdk import ASK_RETRY_CAP_S, ASK_SDK_TIMEOUT_S

    assert quota_mod._ASK_TIMEOUT_S == 10
    assert quota_mod._ASK_TIMEOUT_S + ASK_BUSY_RETRY_AFTER_S < 15
    assert quota_mod._ASK_TIMEOUT_S > quota_mod._ASK_EXEC_FLOOR_S > 0
    assert ASK_RETRY_CAP_S >= ASK_BUSY_RETRY_AFTER_S
    # The per-ATTEMPT transport invariant: a breach is always received as the
    # typed AskTimeout, never as a socket timeout.
    assert ASK_SDK_TIMEOUT_S > quota_mod._ASK_TIMEOUT_S


def test_ask_per_surface_keys_never_combine():
    """The allowlist carries `duration_ms` (the #3359 loss class) and the two
    latency keys are per SURFACE: a MCP tool-call row and a hosted request row
    are DIFFERENT quantities, so the two producers must build disjoint props.
    """
    import inspect

    from tortoise.mcp_server import _emit_mcp_tool_call_telemetry

    assert "duration_ms" in ha_mod._ALLOWED_ANALYTICS_PROPS
    assert "latency_ms" in ha_mod._ALLOWED_ANALYTICS_PROPS
    # The MCP producer's row key is `latency_ms` (transport = tool call)...
    assert "latency_ms" in inspect.getsource(_emit_mcp_tool_call_telemetry)
    assert "duration_ms" not in inspect.getsource(
        _emit_mcp_tool_call_telemetry)
    # ...and the hosted ask producer's is `duration_ms` (transport = HTTP
    # request). Neither writes the other's key, so no aggregation can silently
    # add a tool-call duration to an HTTP wait.
    src = inspect.getsource(ha_mod._emit_ask_latency_off_path)
    assert "duration_ms" in src and "latency_ms" not in src


def test_ask_emission_writes_exactly_one_row_through_the_real_writer(
        monkeypatch, tmp_path):
    """The REAL writer path (not a stubbed recorder): with the tmp fallback,
    one helper call produces exactly one `ask_request` row carrying
    `duration_ms` — proving the allowlist actually carries the value."""
    assert str(
        tmp_path / "analytics_fallback.jsonl") == ha_mod._ANALYTICS_FALLBACK_PATH
    ha_mod._emit_ask_latency_off_path(TEST_ORG_ID, 42, "ok")
    ha_mod._drain_ask_telemetry()
    rows = _ask_rows()
    assert len(rows) == 1, rows
    assert rows[0]["properties"] == {"duration_ms": 42, "status": "ok"}


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
    site: sdk.ask with org_id from get_current_org)."""
    _seed_point(client)
    _FakeReaderFactory().install(monkeypatch)
    from tortoise.metering import get_ask_usage
    for _ in range(3):
        r = client.post("/v1/ask", json={"question": "gym schedule?"})
        assert r.status_code == 200
    usage = get_ask_usage(TEST_ORG_ID)
    assert usage["ask_calls"] == 3
    assert usage["ask_tokens_in"] > 0


# ── #2165 Task 6: hosted /v1/ask exposure (connected-assembly branch) ──────

def _seed_assembly_graph(client) -> None:
    """Build the #2165 base fixture graph into the TEST_TEAM namespace."""
    import tests._assembly_graph as ag
    sdk = ha_mod._make_sdk(namespace=TEST_ORG_ID)
    try:
        ag.build_base_graph(sdk)
    finally:
        sdk.close()


def test_ask_connected_assembly_fired_200(client, monkeypatch):
    """#2165 Task 6 exposure: flags ON + fired shape → 200 with ASSEMBLED
    evidence (the state-header golden text) — the hosted /v1/ask surface
    inherits the branch via the in-process sdk.ask()."""
    _seed_assembly_graph(client)
    _FakeReaderFactory(reply="GOLD").install(monkeypatch)
    monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")
    try:
        r = client.post("/v1/ask", json={
            "question": "what is the current status of the couch?",
            "question_date": "2026-09-10"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert "STATE (couch): superseded by sofa on 2026-09-01" in \
            body["evidence"], body["evidence"]
        assert "[SUPERSEDED BY: sofa]" in body["evidence"]
        assert body["retrieval_degraded"] is False
        assert body["answer"] == "GOLD"
    finally:
        monkeypatch.delenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", raising=False)


def test_ask_connected_assembly_stage_raise_502_not_500(client, monkeypatch):
    """#2165 Task 6 exposure: a forced ASSEMBLER-stage raise maps to the
    retrieval-unavailable code (502) — NEVER a 500 (the fired envelope maps
    any stage raise to AskRetrievalUnavailable; the ask route maps that to
    the canonical 502 body)."""
    import tortoise.assembly as amod
    _seed_assembly_graph(client)
    _FakeReaderFactory(reply="GOLD").install(monkeypatch)
    monkeypatch.setenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", "1")

    def _boom_classify(question: str, *a, **k):
        raise RuntimeError("stage exploded")

    monkeypatch.setattr(amod, "classify_question", _boom_classify)
    try:
        r = client.post("/v1/ask", json={
            "question": "what is the current status of the couch?",
            "question_date": "2026-09-10"})
        assert r.status_code == 502, r.status_code
        body = r.json()
        assert "error" in body, body
        assert body["error"]["code"] in ("retrieval_unavailable",
                                         "ask_retrieval_unavailable"), body
    finally:
        monkeypatch.delenv("TORTOISE_ASK_CONNECTED_ASSEMBLY", raising=False)
