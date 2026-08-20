"""#1135 — welcome_url / billing defaults derive from env, not hardcoded hosts.

- welcome_url (github_callback redirect) must follow EMAIL_LINK_BASE_URL
  (the static Pages host, same as invite-accept/recover links).
- Billing success/cancel/portal DEFAULTS must follow TORTOISE_DASHBOARD_URL
  (the dashboard host), matching __main__.py's claim-flow convention.
"""
from __future__ import annotations  # noqa: I001

import pytest

from tortoise.hosted_api import app
from fastapi.testclient import TestClient

DEFAULT_EMAIL_BASE = "https://tortoise.premiselabs.co"
DEFAULT_DASHBOARD = "https://app.premiselabs.co"


@pytest.fixture
def client():
    with TestClient(app) as tc:
        yield tc


# ── welcome_url ← EMAIL_LINK_BASE_URL ───────────────────────────────────────

def _denied_location(client) -> str:
    """The github=denied redirect needs no state/auth — pure env check."""
    r = client.get("/v1/onboarding/github/callback?error=access_denied",
                   follow_redirects=False)
    assert r.status_code == 302, r.text
    return r.headers["location"]


def test_welcome_redirect_follows_email_link_base(client, monkeypatch):
    """#1135: welcome_url derives from EMAIL_LINK_BASE_URL — not a hardcoded host."""
    monkeypatch.setenv("EMAIL_LINK_BASE_URL", "https://links.example.com")
    loc = _denied_location(client)
    assert loc == "https://links.example.com/welcome.html?github=denied"


def test_welcome_redirect_default_host(client, monkeypatch):
    """Unset EMAIL_LINK_BASE_URL → default static-Pages host (unchanged)."""
    monkeypatch.delenv("EMAIL_LINK_BASE_URL", raising=False)
    loc = _denied_location(client)
    assert loc == f"{DEFAULT_EMAIL_BASE}/welcome.html?github=denied"


# ── billing defaults ← TORTOISE_DASHBOARD_URL ───────────────────────────────

def test_billing_default_success_url_follows_dashboard_url(monkeypatch):
    import tortoise.hosted_api as ha
    monkeypatch.setenv("TORTOISE_DASHBOARD_URL", "https://dash.example.com")
    assert ha._billing_default_success_url() == (
        "https://dash.example.com/team?session_id={CHECKOUT_SESSION_ID}")
    assert ha._billing_default_cancel_url() == (
        "https://dash.example.com/team?checkout=cancelled")
    assert ha._billing_default_portal_return() == "https://dash.example.com/team"


def test_billing_defaults_fallback_host(monkeypatch):
    """Unset TORTOISE_DASHBOARD_URL → default dashboard host (unchanged)."""
    import tortoise.hosted_api as ha
    monkeypatch.delenv("TORTOISE_DASHBOARD_URL", raising=False)
    assert ha._billing_default_success_url() == (
        f"{DEFAULT_DASHBOARD}/team?session_id={{CHECKOUT_SESSION_ID}}")
    assert ha._billing_default_cancel_url() == (
        f"{DEFAULT_DASHBOARD}/team?checkout=cancelled")
    assert ha._billing_default_portal_return() == f"{DEFAULT_DASHBOARD}/team"
