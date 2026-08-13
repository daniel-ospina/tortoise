"""Tests for tortoise/notify.py — Resend + Telegram, both best-effort.

No real network calls: httpx.post and urllib are monkeypatched. Secret values
must never leak into logs (redact_error).
"""
import logging

import pytest

from tortoise import notify

TEAM = {"name": "Acme", "team_id": "team_123", "tier": "pro"}
DETAILS = {"subscription_status": "past_due", "message": "Payment failed", "grace_until": "2030-01-01T00:00:00Z"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret_key_123")
    monkeypatch.setenv("BILLING_NOTIFY_TO", "ops@premiselabs.co")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    notify._skip_logged.clear()
    yield


def test_resend_called_with_email_payload(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        class _R:
            def raise_for_status(self):
                pass
        return _R()

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    notify.notify_billing_event("billing_upgrade", TEAM, {"tier": "pro"})
    assert calls, "resend should have been called"
    url, kwargs = calls[0]
    assert url == notify.RESEND_URL
    body = kwargs["json"]
    assert body["to"] == ["ops@premiselabs.co"]  # BILLING_NOTIFY_TO, not hardcoded
    assert body["from"] == notify.FROM_ADDRESS
    assert "billing_upgrade" in body["subject"]
    assert "pro" in body["html"]


def test_telegram_called_with_message(monkeypatch):
    sent = {}

    def fake_telegram_send(bot_token, chat_id, text, timeout=15.0):
        sent.update(bot_token=bot_token, chat_id=chat_id, text=text)

    monkeypatch.setattr("tortoise.notify.telegram_send", fake_telegram_send)
    notify.notify_billing_event("billing_payment_failed", TEAM, DETAILS)
    assert sent
    assert sent["chat_id"] == "551595722"
    assert "billing_payment_failed" in sent["text"]
    assert "past_due" in sent["text"]


def test_resend_failure_swallowed(monkeypatch, caplog):
    def boom(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(notify.httpx, "post", boom)
    with caplog.at_level(logging.WARNING):
        notify.notify_billing_event("billing_cancel", TEAM)  # must not raise
    assert any("resend failed" in r.message for r in caplog.records)


def test_telegram_failure_swallowed(monkeypatch, caplog):
    def boom(bot_token, chat_id, text, timeout=15.0):
        raise RuntimeError("tg down")

    monkeypatch.setattr("tortoise.notify.telegram_send", boom)
    with caplog.at_level(logging.WARNING):
        notify.notify_billing_event("billing_downgrade", TEAM)  # must not raise
    assert any("telegram failed" in r.message for r in caplog.records)


def test_missing_secret_skips_channel(monkeypatch, caplog):
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(notify.httpx, "post", lambda url, **kw: (_ for _ in ()).throw(AssertionError("should not call")))
    with caplog.at_level(logging.WARNING):
        notify.notify_billing_event("billing_upgrade", TEAM)  # both channels skipped
    assert any("skipped" in r.message for r in caplog.records)


def test_failed_notify_log_redacts_secret(monkeypatch, caplog):
    """The RESEND_API_KEY value must never appear in log output."""
    def boom(url, **kwargs):
        raise RuntimeError("boom with re_test_secret_key_123 inside")

    monkeypatch.setattr(notify.httpx, "post", boom)
    with caplog.at_level(logging.WARNING):
        notify.notify_billing_event("billing_upgrade", TEAM)
    joined = "\n".join(r.message for r in caplog.records)
    assert "re_test_secret_key_123" not in joined


def test_unknown_kind_ignored(monkeypatch):
    monkeypatch.setattr(notify.httpx, "post", lambda url, **kw: (_ for _ in ()).throw(AssertionError("should not call")))
    notify.notify_billing_event("not_a_real_kind", TEAM)  # no-op, no crash


def test_abuse_signup_velocity_kind_allowed_with_ip(monkeypatch):
    """#1081: abuse_signup_velocity ∈ KINDS — notify_abuse must NOT hit the
    unknown-kind early return, and the IP (the most actionable field of an
    IP-scoped ops alert) renders in BOTH channels. Anon team (no email key)
    → BILLING_NOTIFY_TO ops inbox fallback (notify.py:153)."""
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))

        class _R:
            def raise_for_status(self):
                pass
        return _R()

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    sent = {}

    def fake_telegram_send(bot_token, chat_id, text, timeout=15.0):
        sent.update(chat_id=chat_id, text=text)

    monkeypatch.setattr("tortoise.notify.telegram_send", fake_telegram_send)
    notify.notify_abuse("abuse_signup_velocity", {"team_id": "team_123"},
                        {"ip": "203.0.113.7", "count": 3,
                         "threshold": 2, "window_s": 86400})
    assert calls, "resend should be called for a known kind"
    body = calls[0][1]["json"]
    assert body["to"] == ["ops@premiselabs.co"]  # BILLING_NOTIFY_TO fallback
    assert "abuse_signup_velocity" in body["subject"]
    assert "203.0.113.7" in body["html"]  # IP renders in the email
    assert sent and "203.0.113.7" in sent["text"]  # IP renders in Telegram
