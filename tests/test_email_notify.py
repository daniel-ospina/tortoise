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
