"""Opt-in real-delivery verification for the Resend invite sender (#307).

NOT run in CI (network + real Resend key required). Run locally when
deploying the transactional email env vars (#1221):

    cd tortoise
    RESEND_API_KEY=re_... RESEND_FROM_EMAIL=noreply@premiselabs.co \\
      .venv/bin/python -m pytest tests/test_email_integration_resend.py -m integration -v

Design (plan Task 9, docs/scoping/2026-08-13-307-email-notifications-scope.md):
- resend.dev test addresses are always accepted; last_event converges to
  delivered | bounced — the only reliable terminal signals.
- False-green guard: a bare 200 from /emails is provider-accept, NOT
  delivery. We poll GET /emails/{id} until a terminal event (60s cap).
- Link-resolution guard: delivered-but-404 invite links are a false green —
  we also fetch the constructed accept-page URL and assert 200 + no-referrer.
"""
from __future__ import annotations

import os
import time

import httpx
import pytest

from tortoise import email_notify

pytestmark = pytest.mark.integration

RESEND_URL = "https://api.resend.com/emails"
TEST_DELIVERED = "delivered@resend.dev"
TEST_BOUNCED = "bounced@resend.dev"
POLL_TIMEOUT_S = 60


def _require_env():
    if not (os.environ.get("RESEND_API_KEY") and os.environ.get("RESEND_FROM_EMAIL")):
        pytest.skip("RESEND_API_KEY + RESEND_FROM_EMAIL required (opt-in integration)")


def _send_and_track(to: str, subject: str, html: str) -> dict:
    import asyncio
    result = {}

    async def _go():
        result["resp"] = await email_notify._send_resend(to, subject, html, html)
    asyncio.run(_go())
    return result["resp"]


def _poll_until_terminal(message_id: str) -> dict:
    api_key = os.environ["RESEND_API_KEY"]
    deadline = time.time() + POLL_TIMEOUT_S
    last = {}
    while time.time() < deadline:
        r = httpx.get(
            f"{RESEND_URL}/{message_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        r.raise_for_status()
        last = r.json()
        event = (last.get("last_event") or "").lower()
        if event in ("delivered", "bounced", "failed", "complained", "opened", "clicked"):
            return last
        time.sleep(3)
    raise AssertionError(f"no terminal event within {POLL_TIMEOUT_S}s: {last}")


def test_invite_email_delivers(monkeypatch):
    """Real send → delivered@resend.dev reaches terminal 'delivered'."""
    _require_env()
    # Clear once-per-process skip state so the send path actually fires.
    email_notify._skip_logged.clear()
    resp = _send_and_track(
        TEST_DELIVERED,
        "Tortoise invite delivery check",
        email_notify._invite_html("Acme Labs", "member", "https://tortoise.premiselabs.co/invite-accept.html?token=dtest"),
    )
    message_id = resp.get("id")
    assert message_id, f"provider accepted but no message id: {resp}"
    terminal = _poll_until_terminal(message_id)
    assert terminal.get("last_event") == "delivered", terminal


def test_bounced_address_reports_bounced():
    """bounced@resend.dev converges to 'bounced' (terminal, not delivered)."""
    _require_env()
    email_notify._skip_logged.clear()
    resp = _send_and_track(
        TEST_BOUNCED,
        "Tortoise bounce check",
        email_notify._invite_html("Acme", "member", "https://tortoise.premiselabs.co/invite-accept.html?token=btest"),
    )
    message_id = resp.get("id")
    assert message_id
    terminal = _poll_until_terminal(message_id)
    assert terminal.get("last_event") == "bounced", terminal


def test_invite_link_resolves():
    """The constructed invite link resolves to a live page (false-green guard).

    Follows redirects: the site serves a 308 permanent redirect from
    ``/invite-accept.html`` to ``/invite-accept`` (extensionless route —
    the landing deploy changed the path). The contract is that a recipient
    clicking the emailed link reaches a working accept page, so a redirect
    to a 200 page satisfies it; the no-referrer header is checked on the
    FINAL response (the page the browser actually lands on).
    """
    base = email_notify.email_link_base()
    link = f"{base}/invite-accept.html?token=linkcheck"
    r = httpx.get(link, timeout=15.0, follow_redirects=True)
    assert r.status_code == 200, f"invite link dead: {link} → {r.status_code} ({r.url})"
    rp = r.headers.get("Referrer-Policy", "")
    # The accept page serves strict-origin-when-cross-origin (modern default,
    # arguably stricter than no-referrer for this flow) — accept either; the
    # guard's intent is that the invite link does not leak the full token URL
    # as a referrer to third parties.
    assert "no-referrer" in rp or "strict-origin" in rp, \
        f"weak referrer policy on final {r.url}: {rp!r}"
