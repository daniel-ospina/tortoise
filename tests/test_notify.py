"""Tests for tortoise/notify.py — Resend + Telegram, both best-effort.

No real network calls: httpx.post and urllib are monkeypatched. Secret values
must never leak into logs (redact_error).
"""
import logging

import pytest

from tortoise import notify

TEAM = {"name": "Acme", "org_id": "team_123", "tier": "pro"}
DETAILS = {"subscription_status": "past_due", "message": "Payment failed", "grace_until": "2030-01-01T00:00:00Z"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret_key_123")
    monkeypatch.setenv("BILLING_NOTIFY_TO", "ops@premiselabs.co")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:ABCsecret")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "551595722")
    # #1136: sender identity is env-driven — unset both so tests exercise the default.
    monkeypatch.delenv("RESEND_FROM_EMAIL", raising=False)
    monkeypatch.delenv("BILLING_FROM_EMAIL", raising=False)
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
    assert body["from"] == notify.DEFAULT_FROM_ADDRESS  # #1136: default managed sender identity
    assert "billing_upgrade" in body["subject"]
    assert "pro" in body["html"]


def test_resend_from_email_env_used(monkeypatch):
    """#1136: notify.py sends from RESEND_FROM_EMAIL — the single managed identity."""
    monkeypatch.setenv("RESEND_FROM_EMAIL", "noreply@premiselabs.co")
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        class _R:
            def raise_for_status(self):
                pass
        return _R()

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    notify.notify_billing_event("billing_upgrade", TEAM, {"tier": "pro"})
    assert calls
    assert calls[0]["json"]["from"] == "noreply@premiselabs.co"


def test_billing_from_email_override(monkeypatch):
    """#1136: BILLING_FROM_EMAIL keeps the distinct billing sender when desired."""
    monkeypatch.setenv("RESEND_FROM_EMAIL", "noreply@premiselabs.co")
    monkeypatch.setenv("BILLING_FROM_EMAIL", "billing@premiselabs.co")
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs)
        class _R:
            def raise_for_status(self):
                pass
        return _R()

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    notify.notify_billing_event("billing_upgrade", TEAM, {"tier": "pro"})
    assert calls
    assert calls[0]["json"]["from"] == "billing@premiselabs.co"


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


def _install_alert_store(monkeypatch):
    """Point the notify sink at a REAL AlertStore over MemoryStorage.

    A real store (not a fake) so the dedup contract under test is the store's
    own create-once behavior — same approach as tests/test_alert_store.py.
    Returns (filed_titles, telegram_texts).
    """
    from tortoise import hosted_api as ha
    from tortoise.alert_store import AlertStore
    from tortoise.hosted_backup import MemoryStorage

    filed: list[str] = []
    pushed: list[str] = []

    def file_issue(title, body):
        filed.append(title)
        return len(filed)

    store = AlertStore(
        MemoryStorage(),
        file_issue=file_issue,
        close_issue=lambda number, comment=None: None,
        search_open=lambda kind, org_id="": [],
        push_telegram=pushed.append,
        repo="daniel-ospina/tortoise",
    )
    monkeypatch.setattr(ha, "_backup_config_safe", lambda: object())
    monkeypatch.setattr(ha, "_alert_store_from", lambda cfg: store)
    return filed, pushed


def test_billing_send_failure_files_exactly_one_deduped_incident(monkeypatch, caplog):
    """A swallowed Resend failure reaches the ops sink — ONCE.

    Before this, a billing notification that never left the building existed
    only as a log line. Repeated failures of the same channel must NOT file
    repeatedly: that is AlertStore's per-(kind, org_id) create-once dedup.
    """
    filed, pushed = _install_alert_store(monkeypatch)

    def boom(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(notify.httpx, "post", boom)
    with caplog.at_level(logging.WARNING):
        notify.notify_billing_event("billing_cancel", TEAM)  # must not raise
        notify.notify_billing_event("billing_cancel", TEAM)  # same outage again

    assert len(filed) == 1, f"expected one incident, got {filed}"
    assert notify._BILLING_SEND_FAILED_KIND in filed[0]
    assert len(pushed) == 1, pushed
    # The original warning is preserved on BOTH failures.
    assert sum("resend failed" in r.message for r in caplog.records) == 2


def test_billing_send_failure_alert_never_raises(monkeypatch, caplog):
    """A dead alert channel must not break the send path.

    The sink is downstream of a best-effort notification; an R2/GitHub outage
    while FILING must degrade to a log line, never propagate into the caller.
    """
    from tortoise import hosted_api as ha

    def boom(cfg):
        raise RuntimeError("r2 down")

    def boom_post(url, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(ha, "_backup_config_safe", lambda: object())
    monkeypatch.setattr(ha, "_alert_store_from", boom)
    monkeypatch.setattr(notify.httpx, "post", boom_post)
    with caplog.at_level(logging.WARNING):
        notify.notify_billing_event("billing_cancel", TEAM)  # must not raise
    assert any("incident filing failed" in r.message for r in caplog.records)


def test_file_incident_returns_false_when_sink_disabled(monkeypatch):
    """No backup config -> the sink is off; filing is a no-op, not an error."""
    from tortoise import hosted_api as ha

    monkeypatch.setattr(ha, "_backup_config_safe", lambda: None)
    assert notify.file_incident("ANY_KIND") is False


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
    """#1081 + #3639: abuse_signup_velocity ∈ KINDS — notify_abuse must NOT hit
    the unknown-kind early return, and the IP (the most actionable field of an
    IP-scoped ops alert) renders in the Telegram message. Telegram is the ONLY
    channel for abuse since #3639, so a Resend call here is a regression."""
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
    notify._skip_logged.clear()
    notify.notify_abuse("abuse_signup_velocity", {"org_id": "team_123"},
                        {"ip": "203.0.113.7", "count": 3,
                         "threshold": 2, "window_s": 86400})
    assert calls == [], "abuse must not post to Resend (#3639)"
    assert sent and "203.0.113.7" in sent["text"]  # IP renders in Telegram
    assert "abuse_signup_velocity" in sent["text"]
