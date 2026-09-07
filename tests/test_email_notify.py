"""Tests for tortoise/email_notify.py — Resend invite sender (#307).

No real network: httpx.AsyncClient.post is monkeypatched. Env-gated skip
(absent RESEND_API_KEY → no call, once-log). Secrets never in logs.
"""
import asyncio
import logging
from datetime import datetime, timezone

import pytest

from tortoise import email_notify


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret_key_123")
    monkeypatch.setenv("RESEND_FROM_EMAIL", "noreply@premiselabs.co")
    monkeypatch.setenv("EMAIL_LINK_BASE_URL", "https://tortoise.premiselabs.co")
    # #1138: budget envs unset (free-tier defaults) + counters reset per test.
    monkeypatch.delenv("RESEND_SEND_BUDGET_DAILY", raising=False)
    monkeypatch.delenv("RESEND_SEND_BUDGET_MONTHLY", raising=False)
    # #2406: onboarding sender env-tunable from — unset per test so the
    # default daniel@premiselabs.co applies unless a test sets it.
    monkeypatch.delenv("RESEND_ONBOARDING_FROM_EMAIL", raising=False)
    email_notify._send_counts_day = 0
    email_notify._send_counts_month = 0
    email_notify._send_counts_day_period = ""
    email_notify._send_counts_month_period = ""
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
    email_notify._send_counts_month_period = datetime.now(timezone.utc).strftime("%Y-%m")  # current month → kept  # noqa: UP017
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


# ── #2406: onboarding-call offer email ───────────────────────────────────────

def _invoke_onboarding(email, display_name=None, team_name=None,
                       team_id="team-1"):
    """Run the AWAITED onboarding send to completion (no background task)."""
    return asyncio.run(email_notify.send_onboarding_offer_email(
        email, display_name, team_name, team_id))


def test_onboarding_payload_verbatim_copy_exact_url_and_from(monkeypatch):
    """Copy is verbatim (issue #2406) incl. the EXACT booking URL (never the
    ...onbaording-call typo); from daniel@premiselabs.co; personalized with
    the greeting; provider Idempotency-Key onboarding:{team_id}."""
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append((url, kwargs))
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    result = _invoke_onboarding(
        "daniel@premiselabs.co", "Daniel Ospina", "acme", "team-abc")

    assert result["status"] == "sent"
    assert result["message_id"] == "msg_123"
    assert calls, "resend should have been called"
    url, kwargs = calls[0]
    assert url == email_notify.RESEND_URL
    headers = kwargs["headers"]
    assert headers["Authorization"] == "Bearer re_test_secret_key_123"
    assert headers["Idempotency-Key"] == "onboarding:team-abc"
    body = kwargs["json"]
    assert body["from"] == "daniel@premiselabs.co"
    assert body["to"] == ["daniel@premiselabs.co"]
    assert body["subject"] == email_notify.ONBOARDING_SUBJECT

    # Verbatim copy — text body keeps every paragraph faithful.
    text = body["text"]
    assert text.startswith("Hey Daniel\n")
    assert "We like to meet and help our users." in text
    assert "strategising how to use Tortoise" in text
    assert "a soundboard to discuss your usecase and how to make it better" in text
    assert "Much love\nDaniel" in text
    # EXACT URL — the ...onbaording-call typo is WRONG (#2406).
    assert email_notify.ONBOARDING_BOOK_URL == \
        "https://cal.com/danielospina/tortoise-onboarding-call"
    assert "onbaording" not in email_notify.ONBOARDING_BOOK_URL
    assert email_notify.ONBOARDING_BOOK_URL in text
    # HTML body: greeting personalised + link present + escaping sane.
    html_body = body["html"]
    assert "Hey Daniel" in html_body
    assert email_notify.ONBOARDING_BOOK_URL in html_body
    assert "Daniel" in html_body
    assert "onbaording" not in html_body


def test_onboarding_greeting_name_real_funnel_vocabulary():
    """Greeting heuristic pinned with REAL funnel vocabulary (scope doc):
    OAuth display names, email local-parts, role mailboxes, org slugs,
    numeric GitHub relays, hyphenated handles."""
    g = email_notify._onboarding_greeting_name
    # OAuth display name → first name token
    assert g("Daniel Ospina", "daniel.ospina@gmail.com", "acme") == "Daniel"
    # Email/password signup (no display_name) → email local-part
    assert g(None, "daniel.ospina@gmail.com", "acme") == "Daniel"
    assert g("", "alice.smith@example.com", "alice") == "Alice"
    # Role mailbox → org token (title-cased)
    assert g(None, "info@acme.com", "acme") == "Acme"
    assert g(None, "support@acme.com", "acme") == "Acme"
    # GitHub numeric-relay: the relay prefix fails the letters test → org token
    assert g(None, "123456789+daniel-ospina@users.noreply.github.com",
             "acme") == "Acme"
    # Hyphenated slug handle falls through to the email/org fallbacks
    assert g("daniel-ospina", "x@example.com", "acme") == "Acme"
    # Inner case preserved; first char upper-cased
    assert g("mcdonald", None, None) == "Mcdonald"
    # Unicode letters count as letters
    assert g("josé garcía", "j@example.com", "org") == "José"
    # Nothing usable → 'there'
    assert g(None, None, None) == "there"
    assert g(None, "123456789@users.noreply.github.com", "u-9") == "there"
    assert g(None, "a@b.co", "") == "there"


def test_onboarding_from_is_env_tunable(monkeypatch):
    monkeypatch.setenv("RESEND_ONBOARDING_FROM_EMAIL", "daniel@example.com")
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append(kwargs["json"]["from"])
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    _invoke_onboarding("x@example.com", None, "org", "t1")
    assert calls == ["daniel@example.com"]


def test_onboarding_absent_key_returns_skipped(monkeypatch):
    monkeypatch.delenv("RESEND_API_KEY")
    called = []

    async def fake_post(self, url, **kwargs):
        called.append(url)
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    result = _invoke_onboarding("x@example.com", None, "org", "t1")
    assert result == {"status": "skipped"}
    assert not called


def test_onboarding_transient_retry_then_sent(monkeypatch):
    """One 0.5s transient retry: 503 → provider accept."""
    class _StubResp:
        def __init__(self, code):
            self.status_code = code

    attempts = {"n": 0}

    async def fake_post(self, url, **kwargs):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise email_notify.httpx.HTTPStatusError(
                "503", request=None, response=_StubResp(503))
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    result = _invoke_onboarding("x@example.com", None, "org", "t1")
    assert attempts["n"] == 2
    assert result["status"] == "sent"


def test_onboarding_provider_failure_failed_and_budget_refunded(monkeypatch):
    """Provider failure → {status: failed}; the reserved budget slot is
    refunded so a later send can still go out (caller never stamps)."""
    class _StubResp:
        def __init__(self, code):
            self.status_code = code

    attempts = {"n": 0}

    async def failing_post(self, url, **kwargs):
        attempts["n"] += 1
        raise email_notify.httpx.HTTPStatusError(
            "503", request=None, response=_StubResp(503))

    async def ok_post(self, url, **kwargs):
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", failing_post)
    result = _invoke_onboarding("x@example.com", None, "org", "t1")
    assert attempts["n"] == 2  # first + one 0.5s transient retry
    assert result == {"status": "failed"}
    # Budget refunded: counters back to 0 and a later send proceeds.
    assert email_notify._send_counts_day == 0
    assert email_notify._send_counts_month == 0
    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", ok_post)
    result2 = _invoke_onboarding("y@example.com", None, "org", "t2")
    assert result2["status"] == "sent"


def test_onboarding_4xx_no_retry(monkeypatch):
    """Permanent 4xx → single attempt, failed (never retried)."""
    class _StubResp:
        def __init__(self, code):
            self.status_code = code

    attempts = {"n": 0}

    async def fake_post(self, url, **kwargs):
        attempts["n"] += 1
        raise email_notify.httpx.HTTPStatusError(
            "422", request=None, response=_StubResp(422))

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    result = _invoke_onboarding("x@example.com", None, "org", "t1")
    assert attempts["n"] == 1
    assert result == {"status": "failed"}


def test_onboarding_budget_exhausted_returns_skipped(monkeypatch, caplog):
    """Shared #1138 budget guard: at the daily cap the onboarding send is
    hard-stopped with a loud warning (never silently 429s past the tier)."""
    monkeypatch.setenv("RESEND_SEND_BUDGET_DAILY", "1")
    calls = []

    async def fake_post(self, url, **kwargs):
        calls.append(url)
        return _FakeResponse()

    monkeypatch.setattr(email_notify.httpx.AsyncClient, "post", fake_post)
    assert _invoke_onboarding("a@example.com", None, "org", "t1")["status"] == "sent"
    with caplog.at_level(logging.WARNING):
        result = _invoke_onboarding("b@example.com", None, "org", "t2")
    assert result == {"status": "skipped"}
    assert len(calls) == 1  # second send skipped at the daily cap
    assert any("SKIPPED" in r.message and "budget" in r.message
               for r in caplog.records)
