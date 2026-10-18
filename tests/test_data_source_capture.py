"""#3514 (epic #1714 J4) — coming-soon demand capture: the record and the ping.

WHAT THIS PROVES. Clicking an integration tile that is not built yet records
**which integration, by which team, when** in the canonical append-only demand
store (epic #1714 R2) and pings the operator's Telegram once per
``(integration_key, team)`` inside a pinned dedupe window (R3) — and does
nothing else (R4: no delivery, no status record, no extra state).

WHY EVERY ROW IS HERE.

* The record write's RESPONSE is checked and its FAILURE is surfaced
  (``test_click_record_write_failure_surfaces_rather_than_pinging``) — the
  cycle-5 finding this issue inherited. On hosted a failed write writes NO local
  file; the JSONL is the self-hosted sink only.
* The dedupe marker is written ONLY on a 2xx send
  (``test_failed_send_is_not_deduped``). ``analytics_events`` is append-only by
  trigger, so the marker is not an update of the record row — it is a bounded
  process-local reservation, and the claim/release pair is what makes both
  "exactly one send" and "a failed send is retried" true at once.
* NON-BLOCKING is proven against a transport that NEITHER returns NOR raises
  (``test_hung_transport_never_blocks_the_click_request``) — an exception is not
  how "never blocking" is violated; a send that never returns is (the cycle-7
  finding). The timeout constant is pinned, not assumed.
* Card 1 and card 2 of the silent-drop class are asserted by name
  (``test_demand_props_registered_parametrized``), and the tile-inventory
  validation is proven in both directions
  (``test_every_inventory_key_is_accepted``).
* R4's "Notify me" creates NO state (``test_notify_me_label_creates_no_extra_state``).
* The READ half runs OFF the event loop (``test_demand_read_runs_off_the_event_loop``)
  — the #3514 review P2. ``_demand_store_rows`` is a sync ``httpx.Client.get``
  with a 5 s timeout plus an unbounded local file read, called from four
  ``async def`` handlers on a SINGLE-uvicorn-worker app, so an inline call
  stalled every tenant (re-measured: a 1.00 s read ⇒ a 1.00 s loop gap, 0.05 s
  free). ``_demand_store_rows_off_loop`` routes it through ``_cp_offload``.

DB-FREE BY CONSTRUCTION. The analytics sink is either the local JSONL (Supabase
env removed) or a stubbed HTTP client; Telegram is a stub; the marker set is
reset per test. No network, no store, no docker lane.
"""
from __future__ import annotations

import ast
import asyncio
import json
import os
import threading
import time
from pathlib import Path

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest
from fastapi.testclient import TestClient

from tests._verdict import verdict_async_client, verdict_client
from tortoise import demand, hosted_api
from tortoise.hosted_api import app
from tortoise.telegram_push import TelegramPushError

TEAM = {"org_id": "test-team-3514", "tier": "free", "key_id": "k1"}
OTHER_TEAM = {"org_id": "test-team-3514-other", "tier": "free", "key_id": "k2"}

#: Two keys that are in the tile inventory (they come from
#: ``config/connector_manifest.yaml`` and the epic's declared tiles), used
#: wherever a test needs "a valid, distinct key".
KEY_A = "slack"
KEY_B = "linear"

_ENV_KEYS = ("SUPABASE_URL", "SUPABASE_SERVICE_KEY", "SUPABASE_SERVICE_ROLE_KEY")


def _events(client) -> list[dict]:
    if not client._jsonl.exists():
        return []
    return [json.loads(line) for line in client._jsonl.read_text().splitlines()
            if line.strip()]


def _demand_events(client) -> list[dict]:
    return [e for e in _events(client)
            if e.get("event_name") == demand.DEMAND_EVENT_NAME]


def _wait_for(predicate, timeout: float = 5.0) -> bool:
    """Poll until ``predicate()`` is true (the ping is dispatched off-thread)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def client(tmp_path, monkeypatch):
    """TestClient with auth overridden, Supabase + Telegram env removed.

    Supabase env removed pins the SELF-HOSTED branch: the record lands in the
    local JSONL, exactly as the degradation row requires. Tests that need the
    hosted branch set the env themselves.
    """
    monkeypatch.setattr(hosted_api, "_ANALYTICS_FALLBACK_PATH",
                        str(tmp_path / "analytics_fallback.jsonl"))
    for name in (*_ENV_KEYS, "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
                 "FASTAPI_INTERNAL_KEY"):
        monkeypatch.delenv(name, raising=False)
    demand.DEMAND_MARKERS.reset()
    previous = app.dependency_overrides.get(
        hosted_api.get_current_org_session_ungated)
    app.dependency_overrides[hosted_api.get_current_org_session_ungated] = (
        lambda: TEAM)
    with verdict_client(app) as c:
        c._jsonl = tmp_path / "analytics_fallback.jsonl"
        c._override_org = TEAM
        yield c
    if previous is None:
        app.dependency_overrides.pop(
            hosted_api.get_current_org_session_ungated, None)
    else:  # pragma: no cover — another suite's override, restored verbatim
        app.dependency_overrides[hosted_api.get_current_org_session_ungated] = (
            previous)
    demand.DEMAND_MARKERS.reset()


@pytest.fixture
def telegram(monkeypatch):
    """A recording Telegram transport. ``mode`` selects ok / raise / hang.

    Patches ``telegram_push.send_message`` — the exact symbol the production
    path imports lazily — so the wiring, the timeout argument and the swallow
    are exercised for real. NEVER live-fired.
    """
    calls: list[dict] = []
    state: dict = {"mode": "ok", "gate": threading.Event()}

    def fake_send(bot_token, chat_id, text, *, timeout=None):
        calls.append({"bot_token": bot_token, "chat_id": chat_id, "text": text,
                      "timeout": timeout,
                      "records_at_send": None})
        if state["mode"] == "raise":
            raise TelegramPushError("telegram api 500: boom")
        if state["mode"] == "hang":
            # Neither returns nor raises until the test releases it — the
            # cycle-7 case an exception cannot model.
            state["gate"].wait(30)
        return {"ok": True}

    import tortoise.telegram_push as tp

    monkeypatch.setattr(tp, "send_message", fake_send)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    return calls, state


def _post(client, integration_key=KEY_A, action="click", surface="grid",
          headers=None):
    body = {"integration_key": integration_key, "action": action,
            "surface": surface}
    return client.post("/v1/data-sources/demand", json=body,
                       headers=headers or {})


# ── Trap 2: every prop is registered ───────────────────────────────────

def test_data_source_prop_registered():
    """The event name and every prop the record carries are registered."""
    for prop in demand.DEMAND_PROPS:
        assert prop in hosted_api._ALLOWED_ANALYTICS_PROPS, (
            f"{prop!r} is not registered — the filter would DROP it in silence "
            "and the demand row would under-count with no diagnostic")
    assert demand.DEMAND_EVENT_NAME == "data_source_demand"


@pytest.mark.parametrize("prop", demand.DEMAND_PROPS)
def test_demand_props_registered_parametrized(prop):
    """Parametrized over ``demand.DEMAND_PROPS`` — the single declaration.

    A prop-name drift between the record's payload and the allowlist is
    invisible behaviourally (the row still writes, one key short), so the
    assertion is per-name rather than "a row exists".
    """
    assert prop in hosted_api._ALLOWED_ANALYTICS_PROPS


# ── The record ─────────────────────────────────────────────────────────

def test_click_record_lands_in_canonical_store_with_server_derived_org_key(
        client, telegram):
    """The row is the canonical funnel event, and its org key is SERVER-derived."""
    resp = _post(client)
    assert resp.status_code == 200, resp.text
    rows = _demand_events(client)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["org_id"] == TEAM["org_id"]
    assert row["properties"] == {
        "integration_key": KEY_A, "action": "click", "surface": "grid",
        "org_key": demand.derive_org_key(TEAM["org_id"]),
        # #3514 review P2: the record carries its OWN idempotency identity, so a
        # replay can ask the STORE whether the write landed instead of inferring
        # it from a click aggregate. Empty here: this POST sent no key, and an
        # unkeyed request is never replayed.
        "idempotency_fingerprint": ""}
    payload = resp.json()
    assert payload["ok"] is True
    assert payload["integration_key"] == KEY_A
    assert payload["org_key"] == demand.derive_org_key(TEAM["org_id"])
    assert payload["click_count"] == 1


def test_org_key_server_derived_not_client_supplied(client, telegram):
    """A forged ``org_key`` in the body cannot reach the record (R2)."""
    forged = "forged-org-key"
    resp = client.post("/v1/data-sources/demand",
                       json={"integration_key": KEY_A, "action": "click",
                             "surface": "grid", "org_key": forged})
    assert resp.status_code == 200, resp.text
    stored = _demand_events(client)[0]["properties"]["org_key"]
    assert stored != forged
    assert stored == demand.derive_org_key(TEAM["org_id"])


def test_each_click_writes_a_demand_record(client, telegram):
    """R4: the store holds ONE RECORD PER CLICK — only the send is deduped."""
    assert _post(client).status_code == 200
    assert _post(client).status_code == 200
    assert len(_demand_events(client)) == 2
    assert _post(client, integration_key=KEY_B).status_code == 200
    assert len(_demand_events(client)) == 3


def test_selfhosted_demand_degrades_to_local_jsonl(client, telegram):
    """No Supabase sink ⇒ the record degrades to local JSONL, honestly."""
    resp = _post(client)
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "local_jsonl"
    assert len(_demand_events(client)) == 1


def test_click_record_write_failure_surfaces_rather_than_pinging(
        client, telegram, monkeypatch):
    """A failed write on HOSTED: 503, no ping, and NO local file.

    This is the inherited cycle-5 finding: the demand writer used to be
    contracted to "never raises" and never checked its response. The record is
    the thing the user is told about, so a lost record must be visible and
    recoverable — and the ping must not announce a click that was not recorded.
    """
    calls, _state = telegram
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")

    class _FailingClient:
        def post(self, *a, **kw):
            raise RuntimeError("connection refused")

    monkeypatch.setattr(hosted_api, "_analytics_http_client",
                        lambda url, key: _FailingClient())
    resp = _post(client)
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"]["recorded"] is False
    assert not client._jsonl.exists(), (
        "a hosted write failure must NOT divert the record to local disk")
    assert calls == [], "no ping may be sent for an unrecorded click"


def test_demand_store_read_failure_is_503_not_an_empty_list(
        client, telegram, monkeypatch):
    """An unreadable store must not render as "no demand"."""
    monkeypatch.setattr(hosted_api, "_demand_store_rows",
                        lambda org_id: (None, "unavailable"))
    assert client.get("/v1/data-sources/demand").status_code == 503


def test_a_malformed_2xx_store_body_is_unavailable_not_a_crash(monkeypatch):
    """#3514 review P2: the read must not trust a 2xx to be a list of rows.

    ``list(response.json())`` accepted a dict (yielding its KEYS) or a string
    (yielding its CHARACTERS). The resulting rows then blew up in the
    aggregator's ``row.get(...)`` — and on the write path that happened AFTER
    the record was written and the ping dispatched, so a malformed store answer
    turned a SUCCESSFUL write into a 500.

    This drives the real ``_demand_store_rows`` (the neighbouring 503 test
    stubs it out, which is why the gap was invisible): a body that is not a
    list of row objects must come back as the established unavailable shape.
    """
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "service-key")

    class _Client:
        def __init__(self, payload):
            self._payload = payload
            self.status_code = 200

        def get(self, *a, **kw):
            return self

        def json(self):
            return self._payload

    # A list of row objects stays a list of dicts; anything else is unavailable.
    cases = [
        ({"error": "nope"}, (None, "unavailable")),
        ("oops", (None, "unavailable")),
        (7, (None, "unavailable")),
        ([{"org_id": "o"}, "junk", 3], ([{"org_id": "o"}], "supabase")),
    ]
    for payload, expected in cases:
        monkeypatch.setattr(hosted_api, "_analytics_http_client",
                            lambda url, key, p=payload: _Client(p))
        assert hosted_api._demand_store_rows(None) == expected, payload


# ── The ping: dedupe, failure, non-blocking ────────────────────────────

def test_click_record_persisted_before_send(client, telegram, monkeypatch):
    """Ordering: the record is on the sink BEFORE the ping is dispatched."""
    observed: list[int] = []
    import tortoise.telegram_push as tp

    def _observing_send(*_a, **_kw):
        observed.append(len(_demand_events(client)))
        return {"ok": True}

    monkeypatch.setattr(tp, "send_message", _observing_send)
    assert _post(client).status_code == 200
    assert _wait_for(lambda: len(observed) == 1)
    assert observed == [1], (
        "the ping observed a store without the record — it can announce a "
        "click that was not persisted")


def test_send_deduped_per_integration_team(client, telegram):
    """Two integrations ⇒ two sends; a repeat of one ⇒ no third."""
    calls, _state = telegram
    assert _post(client, integration_key=KEY_A).status_code == 200
    assert _post(client, integration_key=KEY_B).status_code == 200
    assert _post(client, integration_key=KEY_A).status_code == 200
    assert _wait_for(lambda: len(calls) == 2)
    time.sleep(0.1)
    assert len(calls) == 2, (
        f"expected one send per (integration_key, team), got {len(calls)}")


def test_rate_cap_n_clicks_one_send(client, telegram):
    """N clicks of one tile inside the window ⇒ exactly ONE send (R3)."""
    calls, _state = telegram
    for _ in range(5):
        assert _post(client).status_code == 200
    assert _wait_for(lambda: len(calls) == 1)
    time.sleep(0.1)
    assert len(calls) == 1
    assert len(_demand_events(client)) == 5, (
        "every click must still be RECORDED — only the send is deduped")


def test_failed_send_is_not_deduped(client, telegram):
    """The marker is retained ONLY on a 2xx send: a failure is re-attempted."""
    calls, state = telegram
    state["mode"] = "raise"
    assert _post(client).status_code == 200
    assert _wait_for(lambda: len(calls) == 1)
    # The claim must be released by the failed send, or the next click within
    # the window is silently dropped for the whole 60 s.
    state["mode"] = "ok"
    assert _post(client).status_code == 200
    assert _wait_for(lambda: len(calls) == 2), (
        "a failed send suppressed the retry — the demand signal was lost")
    # And a 2xx send DOES hold: a third click is deduped.
    assert _post(client).status_code == 200
    time.sleep(0.1)
    assert len(calls) == 2


def test_telegram_transport_error_cannot_propagate(client, telegram):
    """R3: a raising transport cannot fail the user's request."""
    calls, state = telegram
    state["mode"] = "raise"
    resp = _post(client)
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    assert _wait_for(lambda: len(calls) == 1)


def test_telegram_skipped_when_env_unset(tmp_path, monkeypatch):
    """Self-hosted with no operator channel: the ping is SKIPPED, not faked.

    Asserted, never a silent failure: the request still 200s and the record is
    still written, but the transport is not called at all.
    """
    fallback = tmp_path / "analytics_fallback.jsonl"
    monkeypatch.setattr(hosted_api, "_ANALYTICS_FALLBACK_PATH", str(fallback))
    for name in (*_ENV_KEYS, "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    demand.DEMAND_MARKERS.reset()
    calls: list[dict] = []
    import tortoise.telegram_push as tp

    monkeypatch.setattr(tp, "send_message",
                        lambda *a, **kw: calls.append(kw) or {"ok": True})
    app.dependency_overrides[hosted_api.get_current_org_session_ungated] = (
        lambda: TEAM)
    try:
        with verdict_client(app) as c:
            resp = _post(c)
            time.sleep(0.15)
    finally:
        app.dependency_overrides.pop(
            hosted_api.get_current_org_session_ungated, None)
        demand.DEMAND_MARKERS.reset()
    assert resp.status_code == 200, resp.text
    assert calls == [], "telegram was called with no operator channel configured"
    rows = [json.loads(line) for line in fallback.read_text().splitlines()
            if line.strip()]
    assert [r["event_name"] for r in rows] == [demand.DEMAND_EVENT_NAME]

def test_hung_transport_never_blocks_the_click_request(client, telegram):
    """The cycle-7 case: a send that NEITHER returns NOR raises.

    An exception is not how "never blocking" is violated — a send that never
    returns is. The request must still answer 2xx inside the pinned budget, the
    record must already be persisted, and N such clicks must not serialise
    behind the parked transport.
    """
    calls, state = telegram
    state["mode"] = "hang"
    assert demand.DEMAND_REQUEST_BUDGET_S == 2.0
    assert demand.DEMAND_SEND_TIMEOUT_S == 5.0

    started = time.monotonic()
    assert _post(client, integration_key=KEY_A).status_code == 200
    elapsed = time.monotonic() - started
    assert elapsed < demand.DEMAND_REQUEST_BUDGET_S, (
        f"the click request took {elapsed:.2f}s — it waited on the transport")
    assert len(_demand_events(client)) == 1, (
        "the record must already be persisted when the send is parked")
    assert _wait_for(lambda: len(calls) == 1)

    # N further clicks for distinct integrations: none may queue behind the
    # parked worker, and each must answer inside the same budget.
    others = [k for k in sorted(demand.demand_tile_inventory())
              if k not in (KEY_A,)][:4]
    started = time.monotonic()
    for key in others:
        assert _post(client, integration_key=key).status_code == 200
    elapsed = time.monotonic() - started
    assert elapsed < demand.DEMAND_REQUEST_BUDGET_S * len(others), (
        f"{len(others)} clicks took {elapsed:.2f}s — serialised behind the hang")
    state["gate"].set()


def test_demand_send_timeout_is_pinned_and_passed_explicitly(client, telegram):
    """The timeout is a pinned literal AND is passed to the transport."""
    calls, _state = telegram
    assert demand.DEMAND_SEND_TIMEOUT_S == 5.0
    assert _post(client).status_code == 200
    assert _wait_for(lambda: len(calls) == 1)
    assert calls[0]["timeout"] == demand.DEMAND_SEND_TIMEOUT_S


def test_dedupe_window_and_rate_cap_are_pinned_literals():
    """The tests above assert the LITERALS, not "a cap exists"."""
    assert demand.DEMAND_DEDUPE_WINDOW_S == 60.0
    assert demand.DEMAND_RATE_CAP_PER_MINUTE == 20
    assert demand.DEMAND_RATE_WINDOW_S == 60.0
    assert demand.DEMAND_MAX_DISTINCT_KEYS_PER_TEAM == 24


def test_rate_cap_returns_429_with_retry_after(client, telegram, monkeypatch):
    """The (cap+1)th request in the window ⇒ 429 + Retry-After, no record."""
    monkeypatch.setattr(demand, "DEMAND_RATE_CAP_PER_MINUTE", 3)
    for _ in range(3):
        assert _post(client).status_code == 200
    resp = _post(client)
    assert resp.status_code == 429, resp.text
    assert resp.headers.get("Retry-After")
    assert len(_demand_events(client)) == 3


def test_click_map_cardinality_bounded(client, telegram, monkeypatch):
    """Cardinality is bounded separately from rate: novel keys are refused."""
    monkeypatch.setattr(demand, "DEMAND_MAX_DISTINCT_KEYS_PER_TEAM", 2)
    keys = sorted(demand.demand_tile_inventory())
    assert _post(client, integration_key=keys[0]).status_code == 200
    assert _post(client, integration_key=keys[1]).status_code == 200
    resp = _post(client, integration_key=keys[2])
    assert resp.status_code == 429, resp.text
    # An ALREADY-SEEN key still records — the bound is on distinct keys.
    assert _post(client, integration_key=keys[0]).status_code == 200


def test_replayed_request_writes_no_second_record(client, telegram):
    """A replayed request (same idempotency key) writes ONE record."""
    headers = {"X-Idempotency-Key": "click-abc"}
    assert _post(client, headers=headers).status_code == 200
    resp = _post(client, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["replayed"] is True
    assert len(_demand_events(client)) == 1
    assert _wait_for(lambda: True)


def test_a_claim_with_no_row_behind_it_is_not_a_replay(client, telegram,
                                                      monkeypatch):
    """#3514 review P2: `replayed: true` must never be answered on the marker alone.

    A claim can outlive the attempt that took it, so a replay verified only by
    the marker can report a record that does not exist. `recordDemand` maps
    `ok: true` to the RECORDED state, i.e. the user is shown "your team's
    interest is saved" over an empty store — the one thing the failure state
    exists to prevent.

    THE TENURE MATTERS, in both directions (review P2, second half):

    * a claim younger than `DEMAND_INFLIGHT_CLAIM_S` may belong to a LIVE
      attempt still awaiting its write, so the replay answers 503 "still being
      recorded" and **keeps** the reservation — releasing another request's
      claim is what lets a third request record a SECOND row for one click;
    * past that bound the holder is gone (its write is bounded, and a landed
      write leaves `idempotency_fingerprint` on the row), so the replay
      RECOVERS: it takes the claim itself and records, instead of failing a
      write that never happened.

    Pre-fix: the FIRST request answered `200 {ok: true, replayed: true}` with
    zero rows in the store.
    """
    clock = {"now": 1000.0}
    monkeypatch.setattr(demand.DEMAND_MARKERS, "_clock", lambda: clock["now"])
    headers = {"X-Idempotency-Key": "click-phantom"}
    org_key = demand.derive_org_key(TEAM["org_id"])
    claim_key = f"{org_key}:{KEY_A}:click-phantom"
    assert demand.DEMAND_MARKERS.claim(
        demand.MARK_RECORD, claim_key, demand.DEMAND_IDEMPOTENCY_WINDOW_S), \
        "the claim the write path takes — taken here WITHOUT any write behind it"

    phantom = _post(client, headers=headers)
    assert phantom.status_code == 503, phantom.text
    assert phantom.json()["detail"]["recorded"] is False
    assert "still being recorded" in phantom.json()["detail"]["message"], \
        "a claim inside its in-flight window is NOT provably dead"
    assert _demand_events(client) == [], "no row was ever written for that claim"
    assert demand.DEMAND_MARKERS.marked(
        demand.MARK_RECORD, claim_key, demand.DEMAND_IDEMPOTENCY_WINDOW_S), (
        "a replay must NOT release a reservation it did not take (that release "
        "is how a retry becomes a duplicate row)")

    # The abandoned attempt is now provably gone: the claim outlived the in-flight
    # window with no row behind it. The retry RECOVERS the claim and records.
    clock["now"] += demand.DEMAND_INFLIGHT_CLAIM_S + 1.0
    retried = _post(client, headers=headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["replayed"] is False, \
        "the retry is the first record, not a replay of a write that never landed"
    assert len(_demand_events(client)) == 1


def test_an_idempotent_go_back_write_replays_instead_of_duplicating(
        client, telegram):
    """#3514 review P2: replay evidence is about the CLAIM, not about clicks.

    `go_back` is a validated, STORED action, so a team can hold a dismissal row
    and no click row at all. A replay verified with `_demand_click_count` reads
    `0` for that team — "nothing was recorded" — answers 503, releases the
    claim, and the retry then writes a SECOND dismissal row for one request:
    the duplicate the idempotency key exists to prevent, and a false "your click
    was not recorded" for a row that IS in the store.

    Pre-fix: the second POST below answered 503 `recorded: false` and a third
    identical POST wrote a second row. Post-fix: the row's own
    `idempotency_fingerprint` is the evidence, so the replay is a replay
    whatever the action was.
    """
    headers = {"X-Idempotency-Key": "go-back-1"}
    first = _post(client, action="go_back", headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["replayed"] is False

    replay = _post(client, action="go_back", headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True, \
        "a stored go_back row IS a record — the replay must not read as 'not recorded'"
    assert len(_demand_events(client)) == 1, \
        "one idempotency key, one dismissal row — never two"

    # …and the duplicate the pre-fix release made possible cannot happen either.
    assert _post(client, action="go_back", headers=headers).status_code == 200
    assert len(_demand_events(client)) == 1
    # The click figure still counts CLICKS only: the dismissal is not demand.
    assert replay.json()["click_count"] == 0, replay.json()


def test_a_raised_write_releases_its_claim_so_the_retry_records(
        client, telegram, monkeypatch):
    """#3514 review P2: the ROOT CAUSE of the phantom replay above.

    `_demand_record_write` deliberately RE-RAISES `UnregisteredTelemetryKey`
    (#3821), and the claim taken before that write used to stay held for the
    whole idempotency window. The client's Retry — same key — was then answered
    `ok: true, replayed: true` for a click that was never stored, so the dialog
    showed the success state over an empty store.

    Pre-fix: the retry below reported `replayed: true` and wrote nothing.
    Post-fix: every escaping path releases the claim, so the retry is a real
    write and the store holds exactly one row.
    """
    headers = {"X-Idempotency-Key": "click-raised"}

    def _flaky(*_args, **_kwargs):
        raise hosted_api.UnregisteredTelemetryKey(
            "demand_record", "test-team-3514", {"demand.surface"})

    real_write_event = hosted_api._track_analytics_event
    monkeypatch.setattr(hosted_api, "_track_analytics_event", _flaky)
    with pytest.raises(hosted_api.UnregisteredTelemetryKey):
        _post(client, headers=headers)
    assert _demand_events(client) == [], "the raised write stored nothing"

    monkeypatch.setattr(hosted_api, "_track_analytics_event", real_write_event)
    retried = _post(client, headers=headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["replayed"] is False, \
        "a retry after a FAILED attempt must record, not replay a phantom"
    assert len(_demand_events(client)) == 1


def test_unkeyed_second_click_is_a_second_record(client, telegram):
    """Without a client key, a click is a click (R4's one-record-per-click)."""
    assert _post(client).status_code == 200
    assert _post(client).status_code == 200
    assert len(_demand_events(client)) == 2


def test_second_click_is_idempotent_for_the_ui(client, telegram):
    """J4: the second click reports the existing state, not a second toast."""
    first = _post(client).json()
    second = _post(client).json()
    assert first["click_count"] == 1
    assert second["click_count"] == 2
    assert second["integration_key"] == first["integration_key"]


# ── Validation against the tile inventory ──────────────────────────────

def test_every_inventory_key_is_accepted(client, telegram):
    """Positive control: every key in the tile inventory can be recorded.

    Without this, a validation change could refuse LEGITIMATE tiles and the
    refusal would look exactly like a wrong key.
    """
    for key in sorted(demand.demand_tile_inventory()):
        assert _post(client, integration_key=key).status_code == 200, key
    assert len(_demand_events(client)) == len(demand.demand_tile_inventory())


@pytest.mark.parametrize("key", [
    "",                      # empty
    "   ",                   # whitespace only
    "notion",                # unknown (valid charset, not in the inventory)
    "slack" + "x" * 64,      # over-length
    "Slack",                 # out of charset (uppercase)
    "slack; DROP",           # out of charset
    "slack\nx",              # out of charset / control
    "slack\u2028x",          # non-ASCII separator
])
def test_demand_rejects_unknown_or_oversized_integration_key(
        client, telegram, key):
    """No record and no ping for an unacceptable key."""
    resp = _post(client, integration_key=key)
    assert resp.status_code == 422, resp.text
    assert _demand_events(client) == []
    calls, _state = telegram
    time.sleep(0.05)
    assert calls == []


def test_demand_validator_rejects_a_lone_surrogate():
    """A lone surrogate cannot be UTF-8 encoded — rejected, never a later 500.

    Reached through the VALIDATOR rather than the HTTP body: httpx refuses to
    serialise a lone surrogate, so this input cannot arrive over JSON. It is
    asserted anyway because the validator is the boundary every caller crosses,
    and a surrogate that slipped through would raise inside the analytics JSON
    encode — turning a bad key into a 500 on the record path.
    """
    assert demand.validate_integration_key("slack\ud800")
    assert demand.validate_surface("grid\ud800")


def test_demand_rejects_an_unknown_action(client, telegram):
    resp = _post(client, action="ship_it")
    assert resp.status_code == 422, resp.text
    assert _demand_events(client) == []


# ── R4: the click is the only state ────────────────────────────────────

def test_notify_me_label_creates_no_extra_state(client, telegram, monkeypatch):
    """R4: a click writes the base record and NOTHING else.

    The withdrawn notify machinery (status record, notify-intent, per-user
    channel, ship notice) is deleted, not re-derived — so the assertion is that
    no onboarding-state write happens and no second event is emitted.
    """
    written: list[tuple] = []
    monkeypatch.setattr(hosted_api, "_update_onboarding_state",
                        lambda *a, **kw: written.append((a, kw)) or {})
    resp = _post(client)
    assert resp.status_code == 200, resp.text
    assert written == [], "the click wrote onboarding state"
    events = _events(client)
    assert [e["event_name"] for e in events] == [demand.DEMAND_EVENT_NAME]


# ── The read half ──────────────────────────────────────────────────────

def test_demand_read_is_team_scoped_by_the_authenticated_team(client, telegram):
    """A team reads its own demand — and only its own."""
    assert _post(client, integration_key=KEY_A).status_code == 200
    body = client.get("/v1/data-sources/demand").json()
    assert body["ok"] is True
    assert body["demand"] == [{
        "integration_key": KEY_A, "click_count": 1, "org_count": 1,
        "first_clicked_at": body["demand"][0]["first_clicked_at"],
        "last_clicked_at": body["demand"][0]["last_clicked_at"]}]
    assert body["demand"][0]["first_clicked_at"] is not None

    # The other team's view is empty: the filter is the AUTH-resolved org_id,
    # not anything the client sent.
    app.dependency_overrides[hosted_api.get_current_org_session_ungated] = (
        lambda: OTHER_TEAM)
    try:
        other = client.get("/v1/data-sources/demand").json()
    finally:
        app.dependency_overrides[hosted_api.get_current_org_session_ungated] = (
            lambda: TEAM)
    assert other["demand"] == []


def test_a_go_back_row_is_recorded_but_is_not_demand(client, telegram):
    """A dismissal is a FUNNEL row, never a click.

    `action` is a closed server vocabulary (``click`` | ``go_back``), so any
    caller holding a ``tt_`` key can post ``go_back`` — it is reachable, not
    theoretical. The read half ranks DEMAND (clicks), so a dismissal row that
    were counted would inflate both the per-team count the popup shows and the
    cross-org count that triggers #3541 (N distinct orgs → scoping issue).

    Both halves are asserted: the dismissal is RECORDED (the funnel keeps it —
    dropping it would silently discard a real signal) and it adds NO click.
    """
    assert _post(client, integration_key=KEY_A).status_code == 200
    assert _post(client, integration_key=KEY_A, action="go_back").status_code == 200

    rows = client.get("/v1/data-sources/demand").json()["demand"]
    entry = next(d for d in rows if d["integration_key"] == KEY_A)
    assert entry["click_count"] == 1, entry
    assert entry["org_count"] == 1, entry
    # The dismissal IS in the store: the funnel event is not thrown away.
    assert [e["properties"]["action"] for e in _demand_events(client)] == [
        "click", "go_back"]

    # The click response's own count agrees with the read half (one number, one
    # meaning) — an unreadable store answers null, never a fabricated number.
    after = _post(client, integration_key=KEY_A)
    assert after.status_code == 200, after.text
    assert after.json()["click_count"] == 2


def test_cross_org_demand_query_requires_the_internal_key(client, telegram,
                                                          monkeypatch):
    """The cross-org count is on the OPERATOR plane, and only there."""
    assert _post(client, integration_key=KEY_A).status_code == 200
    # Unconfigured internal key ⇒ 503 (never an unauthenticated answer).
    assert client.get("/v1/internal/data-sources/demand").status_code == 503
    monkeypatch.setenv("FASTAPI_INTERNAL_KEY", "internal-secret")
    assert client.get("/v1/internal/data-sources/demand").status_code == 401
    ok = client.get("/v1/internal/data-sources/demand",
                    headers={"Authorization": "Bearer internal-secret"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["distinct_orgs"] == 1
    assert body["demand"][0]["integration_key"] == KEY_A
    assert body["demand"][0]["org_count"] == 1


def test_cross_org_count_dedupes_per_org_not_per_click(tmp_path, monkeypatch):
    """``org_count`` is DISTINCT ORGS — the N=5 trigger's unit, not clicks."""
    monkeypatch.setattr(hosted_api, "_ANALYTICS_FALLBACK_PATH",
                        str(tmp_path / "analytics_fallback.jsonl"))
    for name in (*_ENV_KEYS, "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FASTAPI_INTERNAL_KEY", "internal-secret")
    demand.DEMAND_MARKERS.reset()
    import tortoise.telegram_push as tp

    monkeypatch.setattr(tp, "send_message", lambda *a, **kw: {"ok": True})
    app.dependency_overrides[hosted_api.get_current_org_session_ungated] = (
        lambda: TEAM)
    try:
        with verdict_client(app) as c:
            for _ in range(3):
                assert _post(c, integration_key=KEY_A).status_code == 200
            app.dependency_overrides[
                hosted_api.get_current_org_session_ungated] = (
                    lambda: OTHER_TEAM)
            assert _post(c, integration_key=KEY_A).status_code == 200
            app.dependency_overrides[
                hosted_api.get_current_org_session_ungated] = (lambda: TEAM)
            body = c.get("/v1/internal/data-sources/demand",
                         headers={"Authorization": "Bearer internal-secret"}
                         ).json()
    finally:
        app.dependency_overrides.pop(
            hosted_api.get_current_org_session_ungated, None)
        demand.DEMAND_MARKERS.reset()
    row = body["demand"][0]
    assert row["click_count"] == 4
    assert row["org_count"] == 2, "org_count must dedupe per team"


def test_a_dismissal_only_team_is_store_breadth_not_demand(
        tmp_path, monkeypatch):
    """#3514 review P3: `distinct_orgs` is ACTION-AGNOSTIC on purpose.

    The runbook states it and the operator route's comment now states it too, so
    this pins the behaviour those words describe: a team whose ONLY row is a
    dismissal is counted in `distinct_orgs` (store breadth — how many teams have
    a demand row at all) and appears in NO ranked row (demand — the N=5
    trigger's `org_count`, clicks only).

    Without this arm, "deliberately action-agnostic" is a comment a future
    reader can only trust; with it, flipping either half reds here.
    """
    monkeypatch.setattr(hosted_api, "_ANALYTICS_FALLBACK_PATH",
                        str(tmp_path / "analytics_fallback.jsonl"))
    for name in (*_ENV_KEYS, "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("FASTAPI_INTERNAL_KEY", "internal-secret")
    demand.DEMAND_MARKERS.reset()
    app.dependency_overrides[hosted_api.get_current_org_session_ungated] = (
        lambda: TEAM)
    try:
        with verdict_client(app) as c:
            assert _post(c, integration_key=KEY_A).status_code == 200
            # A second team whose only row is a DISMISSAL.
            app.dependency_overrides[
                hosted_api.get_current_org_session_ungated] = (
                    lambda: OTHER_TEAM)
            assert _post(c, integration_key=KEY_A,
                         action="go_back").status_code == 200
            body = c.get("/v1/internal/data-sources/demand",
                         headers={"Authorization": "Bearer internal-secret"}
                         ).json()
    finally:
        app.dependency_overrides.pop(
            hosted_api.get_current_org_session_ungated, None)
        demand.DEMAND_MARKERS.reset()

    assert body["distinct_orgs"] == 2, (
        "store breadth counts the dismissal-only team — the documented reading")
    row = body["demand"][0]
    assert row["integration_key"] == KEY_A
    assert row["click_count"] == 1, "the dismissal adds no click"
    assert row["org_count"] == 1, (
        "the N=5 trigger's unit counts CLICKING teams only — a dismissal cannot "
        "advance it")


# ── The read half runs OFF the event loop (#3514 review P2) ────────────

def test_demand_read_runs_off_the_event_loop(monkeypatch):
    """The demand read must not stall the loop (measured, not inferred).

    ``_demand_store_rows`` is a synchronous ``httpx.Client.get`` (5 s timeout)
    plus an unbounded local JSONL read, and the app runs ONE uvicorn worker, so
    an inline call freezes EVERY concurrent request for its duration — the same
    class as #3498/#3718/#4015, and the only analytics HTTP call in
    ``hosted_api`` that still ran inline.

    The falsifier is loop LIVENESS while the store is parked: the stub sets
    ``entered`` when its read starts, and the test releases the park only from
    the event loop. On the unfixed code the loop is blocked, so the park can
    only ever time out (``released_in_time is False``); with the read offloaded
    the release runs while the store is still parked. A future refactor that
    puts the read back on the loop fails here regardless of mechanism. (The
    ``max`` tick-gap check is the same invariant read a second way.)
    """
    monkeypatch.setenv("SUPABASE_URL", "https://demand-store.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc-3514")

    entered = threading.Event()
    release = threading.Event()
    state: dict = {}

    class _ParkedStore:
        def get(self, *a, **kw):
            entered.set()
            state["released_in_time"] = release.wait(1.0)

            class _Resp:
                status_code = 200

                def json(self):
                    return [{"org_id": TEAM["org_id"],
                             "properties": {"integration_key": KEY_A},
                             "created_at": "2026-01-01T00:00:00+00:00"}]

            return _Resp()

    monkeypatch.setattr(hosted_api, "_analytics_http_client",
                        lambda url, key: _ParkedStore())
    app.dependency_overrides[hosted_api.get_current_org_session_ungated] = (
        lambda: TEAM)
    try:
        async def _run():
            gaps: list[float] = []
            last = time.perf_counter()
            stop = {"done": False}

            async def _ticker():
                nonlocal last
                while not stop["done"]:
                    await asyncio.sleep(0.05)
                    now = time.perf_counter()
                    gaps.append(now - last)
                    last = now

            async with verdict_async_client(app,
                                            base_url="http://test") as ac:
                tick = asyncio.create_task(_ticker())
                await asyncio.sleep(0.15)  # the ticker must be mid-sleep
                gaps.clear()
                read = asyncio.create_task(
                    ac.get("/v1/data-sources/demand"))
                deadline = time.perf_counter() + 5.0
                while (not entered.is_set() and not read.done()
                       and time.perf_counter() < deadline):
                    await asyncio.sleep(0.02)
                assert entered.is_set(), (
                    "the store read never started — this run proves nothing")
                release.set()
                resp = await read
                stop["done"] = True
                await tick
            return gaps, resp

        gaps, resp = asyncio.run(_run())
    finally:
        app.dependency_overrides.pop(
            hosted_api.get_current_org_session_ungated, None)

    assert resp.status_code == 200, resp.text
    assert resp.json()["demand"][0]["integration_key"] == KEY_A
    assert state.get("released_in_time") is True, (
        "the loop could not release the parked store read until its own park "
        "timed out — the demand read ran ON the event loop (#3514 review P2)")
    assert max(gaps) < 0.5, (
        f"the loop stalled for {max(gaps):.2f}s while the store read was parked "
        "(#3514 review P2)")


def test_demand_read_offload_refusal_is_unavailable_not_a_crash(monkeypatch):
    """#4456's ``OFFLOAD_REFUSED`` is not a ``(rows, source)`` tuple.

    ``_cp_offload(..., best_effort=True)`` returns the sentinel — NOT ``None`` —
    when the pool genuinely refused the submission, so a wrapper that guarded
    only ``is not None`` would hand the aggregator a non-iterable and answer a
    500 exactly when the pool is saturated. The established unavailable
    polarity must hold, and the honest not-zero follows from it.
    """
    async def _refused(*_a, **_kw):
        return hosted_api.OFFLOAD_REFUSED

    monkeypatch.setattr(hosted_api, "_cp_offload", _refused)
    assert asyncio.run(
        hosted_api._demand_store_rows_off_loop(None)) == (None, "unavailable")
    assert hosted_api._demand_click_count(None, KEY_A) is None, (
        "an unreadable store must never render as the click count 0")


def test_the_client_seam_turns_a_wait_bound_refusal_into_an_inconclusive_skip(
        monkeypatch):
    """#8802: the deterministic refusal pin for this module's client seams.

    No load and no wall-clock wait: the bound is forced to 0 s, so the refusal
    comes from the REAL ``WaitBoundMiddleware``. ``/v1/data-sources/demand`` is
    the route this file reads most and it is not exempt from the bound (its
    ``/v1/internal/`` twin is, which is why the public path is the pin). The
    plain-client control is the non-vacuity leg — it is the exact
    ``assert 504 == 200`` shape this conversion removes, and it goes RED if
    the route is ever exempted (the middleware stops refusing, so the
    control's 504 assertion fails).
    """
    from tortoise import mcp_auth
    from tortoise.hosted_api import _TRANSPORT_WAIT_BOUND_EXEMPT

    route = ("GET", "/v1/data-sources/demand")
    assert route not in _TRANSPORT_WAIT_BOUND_EXEMPT
    monkeypatch.setattr(mcp_auth, "_TRANSPORT_WAIT_BOUND_S", 0.0)
    with TestClient(app) as raw:
        assert raw.get("/v1/data-sources/demand").status_code == 504
    with verdict_client(app) as tc, pytest.raises(pytest.skip.Exception) as excinfo:
        tc.get("/v1/data-sources/demand")
    message = str(excinfo.value)
    assert "INCONCLUSIVE [#5049]" in message
    assert "GET /v1/data-sources/demand answered" in message


# ── The e2e harness's own claims (#8975 review P3) ─────────────────────
# The dashboard e2e file is the one place the `sending` state is asserted
# RUNTIME-side (`aria-busy` sampled in the browser while the POST is held by
# `_hold_demand_response`). Its `_wire` docstring used to say the opposite — that
# the loading state is "deliberately NOT asserted here" and is "pinned instead"
# in the two JS suites — which tells the next reader the mechanism is absent and
# invites deleting the browser sampling as dead weight. The repo pins
# docstring-claim accuracy on purpose (tests/test_7846_restore_doc_and_bounds.py
# carries that same docstring-vs-evidence guard), so the paragraph is pinned here
# rather than trusted.
_E2E_ONBOARDING = Path(__file__).resolve().parent / "e2e" / "test_dashboard_onboarding.py"


def _e2e_function(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """The `ast` node for `name` in the e2e file, read WITHOUT importing it.

    Importing that module raises the module-level
    `pytest.skip(..., allow_module_level=True)` that guards `RUN_DASHBOARD_E2E`,
    and pytest turns what it raises into a green-looking SKIP — a guard that
    imported would stop guarding without ever failing. A doc-text claim needs no
    runtime, so this parses the text: each lookup is one `ast.parse` + walk, and
    a caller bounds a spec's body with its own `lineno`/`end_lineno` rather than
    a hand-maintained line number.
    """
    tree = ast.parse(_E2E_ONBOARDING.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {_E2E_ONBOARDING}")


def test_the_e2e_harness_docstring_agrees_with_the_browser_hold() -> None:
    """#8975 review P3: `_wire` must name the mechanism the file actually uses.

    Pre-fix: the paragraph claimed the transient loading state is "deliberately
    NOT asserted here" and "pinned instead" in `demandCapture.test.js` /
    `demandCaptureRender.test.js`, while
    `test_focus_stays_in_the_dialog_while_the_post_is_in_flight` in the SAME
    file samples `aria-busy="true"` in the browser via `_hold_demand_response`.
    """
    doc = ast.get_docstring(_e2e_function("_wire")) or ""
    focus_name = "test_focus_stays_in_the_dialog_while_the_post_is_in_flight"
    # BOTH claim-bearing paragraphs, because they made the SAME false statement
    # and only one of them was corrected first (cycle-3 P3): a guard that reads
    # `_wire` alone leaves the sibling free to drift back out of agreement.
    for name in ("_wire", "test_coming_soon_click_is_recorded_with_its_success_state"):
        claim = ast.get_docstring(_e2e_function(name)) or ""
        assert claim, f"{name} must carry the docstring this guard reads"
        assert "deliberately NOT asserted" not in claim, (
            f"{name}: the in-flight (loading) state IS asserted in this file — "
            "through the page-side hold, not a sync route handler")
        assert "_hold_demand_response" in claim, (
            f"{name}: the paragraph must name the browser mechanism that drives "
            "the state (patching the page's own fetch), or the next reader "
            "cannot find it")
        assert focus_name in claim, (
            f"{name}: it must name the spec that samples the state, or the claim "
            "is unanchored")
        # The NEGATION matters more than the naming: the pre-fix sibling carried
        # both names AND the false claim around them, so only refusing the claim
        # itself catches a full reversion (cycle-4 P2). Whitespace-normalised,
        # because the sentences re-wrap and a re-wrap must not hide it.
        flat = " ".join(claim.split())
        for false_claim in ("pinned by the two JS suites", "cannot be sampled here"):
            assert false_claim not in flat, (
                f"{name}: '{false_claim}' is the pre-fix FALSE claim — the state "
                "IS sampled in this file via the page-side hold (#8975 cycle-3 P3)")
    # The route-handler limitation is the TRUE half of the old sentence and the
    # evidence for the mechanism above — it must survive the fix.
    assert "10/10 polls read" in doc, (
        "the measurement that justifies the page-side hold must stay")
    # Non-vacuity, BOUND to the spec's OWN lines: the docstring naming the
    # mechanism buys nothing if the spec stopped using it, and the paragraph
    # itself mentions `aria-busy`, so a file-wide search would be satisfied by
    # the claim rather than by the code it describes.
    src = _E2E_ONBOARDING.read_text(encoding="utf-8")
    focus = _e2e_function(focus_name)
    segment = "\n".join(src.splitlines()[focus.lineno - 1:focus.end_lineno])
    assert "_hold_demand_response(page)" in segment, (
        "the spec must install the page-side hold before it navigates")
    assert "getAttribute('aria-busy')" in segment, (
        "the spec must SAMPLE the in-flight state in the browser — the "
        "expression, not merely the word, which its own docstring also carries")
