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

#4367 — REQUIRED-PATH HERMETICITY. This file is registered on the `api`
surface (config/ci-surfaces.yml), so it is selected for any PR that touches an
api-owned path AND for every push to main. The link-resolution guard used to
make a LIVE HTTPS call from inside that gate, so an unrelated change (a deploy,
a Cloudflare blip, the 301 → app.premiselabs.co host move that had already
drifted from the docstring's 308) could red CI for reasons the diff had
nothing to do with. Two halves now:

- ``test_invite_link_contract_is_hermetic`` — runs on every PR/push. It drives
the REAL ``_build_invite_link`` and replays the recorded cassette
(``fixtures/invite_accept_page.json``) through a transport that RAISES on any
URL the cassette does not cover, so it can never pass by reaching the network.
- ``test_invite_link_resolves`` — the deployed-page probe, now
``@pytest.mark.live`` (deselected by the required path's ``-m 'not live'``) and
gated on ``ALLOW_PROD=1`` (the repo convention for production assertions).
"""
from __future__ import annotations

import inspect
import json
import os
import time
from pathlib import Path

import httpx
import pytest

from tortoise import email_notify

pytestmark = pytest.mark.integration

RESEND_URL = "https://api.resend.com/emails"
TEST_DELIVERED = "delivered@resend.dev"
TEST_BOUNCED = "bounced@resend.dev"
POLL_TIMEOUT_S = 60

# #4367: the recorded accept-page exchange (status + redirect target +
# contract headers), captured once from the live deployment.
ACCEPT_CASSETTE = Path(__file__).parent / "fixtures" / "invite_accept_page.json"


def _load_accept_cassette() -> dict:
    return json.loads(ACCEPT_CASSETTE.read_text())


class _CassetteTransport(httpx.BaseTransport):
    """Replay the recorded exchange; raise on anything else (fail-closed).

    Any request the cassette does not cover raises, so a test using this
    transport cannot pass by silently going live — the hermeticity of the
    guard is enforced, not merely asserted in a comment. ``requests`` records
    every URL the code under test actually asked for.
    """

    def __init__(self, exchanges: list[dict]):
        self._by_url = {e["request_url"]: e for e in exchanges}
        self.requests: list[str] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        entry = self._by_url.get(url)
        if entry is None:
            raise AssertionError(
                f"hermetic guard: refusing an unrecorded network request to {url!r} "
                f"(cassette covers {sorted(self._by_url)})"
            )
        return httpx.Response(entry["status_code"], headers=entry["headers"])


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


# ── #4367: hermetic link contract — required path, no network ─────────────────


def test_invite_link_contract_is_hermetic(monkeypatch):
    """The emailed invite link's contract, asserted off a recorded cassette.

    Runs on the required path (PR + push). Real logic under test: the build
    goes through the production ``_build_invite_link`` (not a re-derived
    string — the old live guard duplicated the format, so a change to the
    builder would not have been caught here), resolving its base through
    ``email_link_base()``. The URL that comes out must be exactly the first
    request of the recorded exchange, the redirect chain the cassette
    recorded must land on a 200, and the final recorded response must carry
    an acceptable Referrer-Policy.

    The referrer-policy assertion is a pin on the RECORDED response (the
    contract as last observed) — verifying the DEPLOYED page still honours it
    is the job of the live-marked ``test_invite_link_resolves`` below. What
    this test guarantees on every PR/push is the link-construction contract
    and the recorded page contract, with no network at all.
    """
    monkeypatch.delenv("EMAIL_LINK_BASE_URL", raising=False)  # ambient-env-proof
    cassette = _load_accept_cassette()
    transport = _CassetteTransport(cassette["exchanges"])

    link = email_notify._build_invite_link("linkcheck")
    with httpx.Client(transport=transport, follow_redirects=True) as client:
        r = client.get(link, timeout=15.0)

    # 1. the builder emits the recorded URL, with no redirect skipped or invented
    assert transport.requests == [e["request_url"] for e in cassette["exchanges"]], \
        f"built link {link!r} does not match the recorded exchange chain"
    assert r.status_code == 200, f"recorded final page is not 200: {r.status_code} ({r.url})"
    assert str(r.url) == cassette["exchanges"][-1]["request_url"]
    rp = r.headers.get("Referrer-Policy", "")
    # The accept page serves strict-origin-when-cross-origin (modern default,
    # arguably stricter than no-referrer for this flow) — accept either; the
    # guard's intent is that the invite link does not leak the full token URL
    # as a referrer to third parties.
    assert "no-referrer" in rp or "strict-origin" in rp, \
        f"weak referrer policy on final {r.url}: {rp!r}"


def test_hermetic_guard_refuses_unrecorded_requests():
    """The hermeticity proof itself: an unrecorded URL RAISES, never goes live."""
    transport = _CassetteTransport(_load_accept_cassette()["exchanges"])
    with httpx.Client(transport=transport) as client, pytest.raises(AssertionError, match="hermetic guard"):
        client.get("https://tortoise.premiselabs.co/not-in-the-cassette")


# ── #4367: the deployed-page probe — live-marked, opt-in ─────────────────────


@pytest.mark.live
def test_invite_link_resolves():
    """LIVE: the DEPLOYED invite link resolves to a working accept page.

    Production assertion, so it needs BOTH opt-ins: ``@pytest.mark.live``
    (deselected by the required path's ``-m 'not live'`` — see
    pyproject.toml markers) and ``ALLOW_PROD=1`` (the repo convention that
    forbids production assertions pre-merge). The hermetic counterpart above
    covers link construction + the recorded page contract on every PR/push;
    this probe is what re-records the cassette's assumptions against reality.

    Follows redirects: the site serves a permanent redirect from
    ``/invite-accept.html`` on the static-Pages host to the extensionless
    ``/invite-accept`` route on the app host (the landing deploy moved it).
    The contract is that a recipient clicking the emailed link reaches a
    working accept page, so a redirect to a 200 page satisfies it; the
    referrer header is checked on the FINAL response (the page the browser
    actually lands on).
    """
    if os.environ.get("ALLOW_PROD") != "1":
        pytest.skip("ALLOW_PROD=1 required for a production link assertion")
    link = email_notify._build_invite_link("linkcheck")
    r = httpx.get(link, timeout=15.0, follow_redirects=True)
    assert r.status_code == 200, f"invite link dead: {link} → {r.status_code} ({r.url})"
    rp = r.headers.get("Referrer-Policy", "")
    assert "no-referrer" in rp or "strict-origin" in rp, \
        f"weak referrer policy on final {r.url}: {rp!r}"


def test_live_link_probe_marker_pinned():
    """#4367 guard: the deployed-page probe MUST carry @pytest.mark.live so the
    required path (``-m 'not live'``) never makes the production call. A
    dropped marker re-reddens CI for unrelated diffs — the exact failure this
    split exists to prevent (mirrors test_extractor_reliability's pin)."""
    marker = getattr(test_invite_link_resolves, "pytestmark", None)
    assert marker and any(getattr(m, "name", None) == "live" for m in marker), \
        "test_invite_link_resolves must carry @pytest.mark.live"
    assert "ALLOW_PROD" in inspect.getsource(test_invite_link_resolves)
