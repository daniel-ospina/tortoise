"""Tests for tortoise/email_notify.py — Resend invite sender (#307).

No real network: httpx.AsyncClient.post is monkeypatched. Env-gated skip
(absent RESEND_API_KEY → no call, once-log). Secrets never in logs.
"""
import asyncio
import logging

import pytest

from tortoise import email_notify


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret_key_123")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "noreply@premiselabs.co")
    monkeypatch.setenv("EMAIL_LINK_BASE_URL", "https://tortoise.premiselabs.co")
    email_notify._skip_logged.clear()
    yield


class _FakeResponse:
    def __init__(self, payload=None):
        self._payload = payload or {"id": "msg_123"}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _invoke(team, email, role, token, iid, on_sent=None):
    """Run send_invite_email + let its task complete, inside a real loop."""
    async def _main():
        email_notify.send_invite_email(team, email, role, token, iid, on_sent)
        await asyncio.sleep(0.05)
        if email_notify._pending_email_tasks:
            await asyncio.wait(list(email_notify._pending_email_tasks), timeout=1.0)
        await email_notify.drain_pending_sends(0.01)
    asyncio.run(_main())


def test_invite_email_payload_and_link(monkeypatch):
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    sent = []
    _invoke(
        "Acme <Team>", "bob@example.com", "member", "tok_123", "inv_1",
        on_sent=lambda mid: sent.append(mid),
    )

    assert calls, "resend should have been called"
    url, kwargs = calls[0]
    assert url == email_notify.RESEND_URL
    headers = kwargs["headers"]
    assert headers["Authorization"] == "Bearer re_test_secret_key_123"
    assert headers["User-Agent"] == "tortoise-api/0.1"
    assert headers.get("Idempotency-Key") == "invite:inv_1"
    body = kwargs["json"]
    assert body["to"] == ["bob@example.com"]
    assert body["from"] == "noreply@premiselabs.co"
    assert "Acme &lt;Team&gt;" in body["html"]  # HTML-escaped team name
    assert "tortoise.premiselabs.co/invite-accept.html?token=tok_123" in body["html"]
    assert sent == ["msg_123"]  # on_sent only on provider accept


def test_absent_key_skips_send(monkeypatch, caplog):
    monkeypatch.delenv("RESEND_API_KEY")
    called = []

    async def fake_post(self, url, **kwargs):
        called.append(url)
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    _invoke("Acme", "bob@example.com", "member", "t", "i")
    assert not called
    assert any("skipped" in r.message for r in caplog.records)


def test_transient_retry_then_fail_logs_redacted(monkeypatch, caplog):
    class _Err:
        def __init__(self):
            pass

    class _FakeRaisingResponse:
        def raise_for_status(self):
            raise email_notify.httpx.HTTPStatusError(
                "503", request=None, response=_StubResp(503))

    class _StubResp:
        def __init__(self, code):
            self.status_code = code

    attempts = {"n": 0}

    async def fake_post(self, url, **kwargs):
        attempts["n"] += 1
        raise email_notify.httpx.HTTPStatusError(
            "503", request=None, response=_StubResp(503))

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    with caplog.at_level(logging.WARNING):
        _invoke("Acme", "bob@example.com", "member", "t", "i")
    assert attempts["n"] == 2  # first + one retry
    assert any("invite email failed" in r.message for r in caplog.records)


def test_4xx_no_retry(monkeypatch):
    class _StubResp:
        def __init__(self, code):
            self.status_code = code

    attempts = {"n": 0}

    async def fake_post(self, url, **kwargs):
        attempts["n"] += 1
        raise email_notify.httpx.HTTPStatusError(
            "422", request=None, response=_StubResp(422))

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    _invoke("Acme", "bob@example.com", "member", "t", "i")
    assert attempts["n"] == 1  # permanent error — no retry


def test_drain_pending_sends_is_safe():
    asyncio.run(email_notify.drain_pending_sends(0.01))


def test_send_budget_daily_cap_skips_sends(monkeypatch, caplog):
    """#1138: with a 2-email daily budget, the 3rd invite is hard-stopped with
    a loud warning instead of silently exhausting the Resend free tier."""
    monkeypatch.setenv("RESEND_SEND_BUDGET_DAILY", "2")
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append(url)
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    with caplog.at_level(logging.WARNING):
        _invoke("Acme", "a@example.com", "member", "t", "i1")
        _invoke("Acme", "b@example.com", "member", "t", "i2")
        _invoke("Acme", "c@example.com", "member", "t", "i3")
    assert len(calls) == 2  # third send skipped at the daily cap
    assert any("SKIPPED" in r.message and "budget" in r.message for r in caplog.records)


def test_send_budget_zero_disables_all_sends(monkeypatch, caplog):
    """#1138: RESEND_SEND_BUDGET_DAILY=0 hard-stops every send (ops kill-switch)."""
    monkeypatch.setenv("RESEND_SEND_BUDGET_DAILY", "0")
    called = []

    async def fake_post(self, url, **kwargs):
        called.append(url)
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    with caplog.at_level(logging.WARNING):
        _invoke("Acme", "a@example.com", "member", "t", "i")
    assert not called
    assert any("SKIPPED" in r.message for r in caplog.records)


def test_send_budget_invalid_env_falls_back_to_default(monkeypatch, caplog):
    """#1138: a garbage budget value must not crash or disable the guard — the
    send proceeds under the free-tier default cap."""
    monkeypatch.setenv("RESEND_SEND_BUDGET_DAILY", "not-a-number")
    monkeypatch.setenv("RESEND_SEND_BUDGET_MONTHLY", "not-a-number")
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append(url)
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    with caplog.at_level(logging.WARNING):
        _invoke("Acme", "a@example.com", "member", "t", "i")
    assert len(calls) == 1
    assert any("invalid RESEND_SEND_BUDGET" in r.message for r in caplog.records)


def test_send_budget_monthly_cap_skips_sends(monkeypatch, caplog):
    """#1138: the monthly cap (free tier 3,000/mo) is enforced independently of
    the daily cap."""
    monkeypatch.setenv("RESEND_SEND_BUDGET_DAILY", "1000")
    monkeypatch.setenv("RESEND_SEND_BUDGET_MONTHLY", "2")
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append(url)
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    with caplog.at_level(logging.WARNING):
        _invoke("Acme", "a@example.com", "member", "t", "i1")
        _invoke("Acme", "b@example.com", "member", "t", "i2")
        _invoke("Acme", "c@example.com", "member", "t", "i3")
    assert len(calls) == 2  # monthly cap hit on the 3rd
    assert any("monthly budget" in r.message for r in caplog.records)


def test_send_budget_burst_schedules_at_most_cap(monkeypatch, caplog):
    """#1138 P1 review-fix (TOCTOU): a burst of N>cap invites scheduled
    back-to-back (no awaits between calls, so no provider POST completes first)
    must schedule at most `cap` sends — the budget slot is RESERVED at schedule
    time, not after the provider POST. Without the fix, all N pass the check
    while the counters are still 0 and every one POSTs (simulated 200-at-100
    scenario)."""
    monkeypatch.setenv("RESEND_SEND_BUDGET_DAILY", "3")
    calls = []
    gate = asyncio.Event()  # holds every POST open until the burst has scheduled

    async def fake_post(self, url, **kwargs):
        calls.append(url)
        await gate.wait()
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)

    async def _main():
        for i in range(10):
            email_notify.send_invite_email(
                "Acme", f"u{i}@example.com", "member", "t", f"inv_{i}")
        # All 10 schedule calls returned while every POST is still blocked:
        # only `cap` reservations went through, the rest were skipped.
        assert len(email_notify._pending_email_tasks) == 3
        gate.set()
        if email_notify._pending_email_tasks:
            await asyncio.wait(list(email_notify._pending_email_tasks), timeout=1.0)
        await email_notify.drain_pending_sends(0.01)

    with caplog.at_level(logging.WARNING):
        asyncio.run(_main())

    assert len(calls) == 3  # exactly `cap` sends hit the provider — not 10
    assert email_notify._send_counts_day == 3
    assert email_notify._send_counts_month == 3
    skipped = [r for r in caplog.records if "SKIPPED" in r.message]
    assert len(skipped) == 7


def test_send_budget_refunded_when_provider_rejects(monkeypatch, caplog):
    """#1138 P1 review-fix: a provider-rejected POST rolls back its reserved
    slot — a later invite can still use the budget (no permanent slot leak)."""
    monkeypatch.setenv("RESEND_SEND_BUDGET_DAILY", "1")
    calls = []
    outcomes = iter(["fail", "ok"])

    class _StubResp:
        def __init__(self, code):
            self.status_code = code

    async def fake_post(self, url, **kwargs):
        calls.append(url)
        if next(outcomes) == "fail":
            raise email_notify.httpx.HTTPStatusError(
                "422", request=None, response=_StubResp(422))
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    with caplog.at_level(logging.WARNING):
        _invoke("Acme", "a@example.com", "member", "t", "i1")  # rejected → refunds
        _invoke("Acme", "b@example.com", "member", "t", "i2")  # slot free again → sent
    assert len(calls) == 2  # both POSTs happened — the refund freed the slot
    assert email_notify._send_counts_day == 1
    assert email_notify._send_counts_month == 1


def test_send_budget_negative_env_falls_back_to_default(monkeypatch, caplog):
    """#1138 review-fix (P2): a NEGATIVE budget value must not silently disable
    the guard — int('-1') → max(0,-1) → 0 would be a kill switch. Warn loudly
    and fall back to the free-tier default."""
    monkeypatch.setenv("RESEND_SEND_BUDGET_DAILY", "-1")
    monkeypatch.setenv("RESEND_SEND_BUDGET_MONTHLY", "-5")
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append(url)
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    with caplog.at_level(logging.WARNING):
        _invoke("Acme", "a@example.com", "member", "t", "i")
    assert len(calls) == 1  # default cap (100) — the send proceeds
    assert any("negative" in r.message and "RESEND_SEND_BUDGET" in r.message
               for r in caplog.records)


def test_send_budget_month_counter_survives_day_rollover():
    """#1138 review-fix: a day rollover resets only the day counter — the month
    counter ACCUMULATES across days (it is the 3,000/month free-tier guard; a
    process at the daily cap for weeks must still trip the monthly guard)."""
    email_notify._send_counts_day = 90
    email_notify._send_counts_month = 2999
    email_notify._send_counts_day_period = "2000-01-01"  # stale day → resets
    email_notify._send_counts_month_period = datetime.now(timezone.utc).strftime("%Y-%m")  # current month → kept
    exceeded, _ = email_notify._budget_exceeded()
    assert email_notify._send_counts_day == 0
    assert email_notify._send_counts_month == 2999  # not reset at day rollover
    assert not exceeded  # 2999 < 3000
    email_notify._reserve_send()
    assert email_notify._send_counts_month == 3000
    exceeded2, reason2 = email_notify._budget_exceeded()
    assert exceeded2 and "monthly budget" in reason2


def test_send_budget_month_rollover_resets_month_counter():
    """#1138 review-fix: at a month change both counters reset."""
    email_notify._send_counts_day = 50
    email_notify._send_counts_month = 3000
    email_notify._send_counts_day_period = "2000-01-01"
    email_notify._send_counts_month_period = "2000-01"  # stale month → resets
    exceeded, _ = email_notify._budget_exceeded()
    assert email_notify._send_counts_month == 0
    assert email_notify._send_counts_day == 0
    assert not exceeded
