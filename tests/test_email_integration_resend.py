"""Resend invite-sender checks (#307): a hermetic contract half and an opt-in live half.

Two halves live in this module — do not conflate them:

- **Hermetic contract half** — ``test_invite_link_contract_is_hermetic``,
  ``test_hermetic_guard_refuses_unrecorded_requests`` and
  ``test_live_link_probe_marker_pinned``. This half RUNS IN CI on every PR/push
  that selects the ``api`` surface. It needs no credentials and makes no
  network calls.
- **Opt-in live half** — ``test_invite_email_delivers``,
  ``test_bounced_address_reports_bounced`` and the ``live``-marked
  ``test_invite_link_resolves``. This half is NOT run in CI: each test carries
  ``@pytest.mark.integration`` (registered in ``pyproject.toml``), which the
  deterministic lanes deselect with
  ``-m 'not track_b and not live and not integration'`` (#4750). The marker — not
  a per-test ``pytest.skip`` — is what keeps this half out of the required path,
  so the exclusion is explicit and cannot read as a green skip. It requires a
  real ``RESEND_API_KEY`` + ``RESEND_FROM_EMAIL``, and the deployed-page probe
  additionally requires ``ALLOW_PROD=1`` (the repo convention for production
  assertions). Run it locally when deploying the transactional email env vars
  (#1221):

    cd tortoise
    RESEND_API_KEY=re_... RESEND_FROM_EMAIL=noreply@premiselabs.co \\
      .venv/bin/python -m pytest tests/test_email_integration_resend.py -m integration -v

  (that invocation runs the two delivery tests; add ``-m live`` with
  ``ALLOW_PROD=1`` for the deployed-page probe.)

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
``@pytest.mark.live`` + ``@pytest.mark.integration`` (both deselected by the
required path's ``-m 'not track_b and not live and not integration'``) and
gated on ``ALLOW_PROD=1`` (the repo convention for production assertions).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import pytest

from tortoise import email_notify

# #4750: NO module-level ``pytestmark``. This module is deliberately HALF
# required-path (the hermetic contract + its pin, which must keep running in CI)
# and HALF opt-in (the live/credentialed tests, which must not). A module-level
# ``integration`` mark said "none of this runs in CI" — false for the hermetic
# half — and, being unregistered, matched no lane filter anyway. The marker now
# lives on the opt-in tests only, and the lanes deselect it.

RESEND_URL = "https://api.resend.com/emails"
TEST_DELIVERED = "delivered@resend.dev"
TEST_BOUNCED = "bounced@resend.dev"
POLL_TIMEOUT_S = 60

# #4367: the recorded accept-page exchange (status + redirect target +
# contract headers), captured once from the live deployment.
#
# OPEN RISK — CASSETTE ROT IS UNDETECTED BY DESIGN (documented, not automated).
# This file pins a RECORDED contract, i.e. the exchange as observed at capture
# time — not the deployed one. Nothing on the required path re-checks it
# against the live host: the live half below is deselected by default
# (``-m 'not live'``) and gated on ``ALLOW_PROD=1``, and the nightly live tier
# was deliberately retired in #4367 (no scheduled job is added on purpose). So
# if the deployed host/path moves again — it already moved once, the
# /invite-accept.html -> app.premiselabs.co 301 — CI stays green and the
# cassette rots silently until someone re-records it against reality:
#
#     ALLOW_PROD=1 ... -m live -k invite_link_resolves
#
# (Re-recording is therefore a real obligation of the live half, not a
# formality: it is the only thing that keeps this cassette honest.)
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


@pytest.mark.integration
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


@pytest.mark.integration
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
    is the job of the live-marked ``test_invite_link_resolves`` below. That
    probe is deselected on the required path, so the DEPLOYED host/path
    contract is asserted by NOTHING by default — a deliberate trade of #4367,
    with the resulting cassette-rot exposure documented on ``ACCEPT_CASSETTE``
    above. What this test guarantees on every PR/push is the link-construction
    contract and the recorded page contract, with no network at all.
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


@pytest.mark.integration
@pytest.mark.live
def test_invite_link_resolves():
    """LIVE: the DEPLOYED invite link resolves to a working accept page.

    Production assertion, so it needs BOTH opt-ins: ``@pytest.mark.live`` and
    ``@pytest.mark.integration`` (both deselected by the required path's
    ``-m 'not track_b and not live and not integration'`` — see pyproject.toml
    markers) and ``ALLOW_PROD=1`` (the repo convention that forbids production
    assertions pre-merge). The hermetic counterpart above covers link
    construction + the recorded page contract on every PR/push; this probe is
    what re-records the cassette's assumptions against reality.

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
    required path (``-m 'not live'``) never makes the production call, and MUST
    still gate on ``ALLOW_PROD=1``. A dropped marker — or a dropped ALLOW_PROD
    gate — re-reddens (or re-opens) CI for unrelated diffs, the exact failure
    this split exists to prevent (mirrors test_extractor_reliability's pin).

    The ALLOW_PROD pin reads the function's code object CONSTANTS, not its
    source text: ``"ALLOW_PROD" in inspect.getsource(...)`` is satisfied by a
    mere comment, so a source-substring pin stays green while the real gate is
    gone. ``__code__.co_consts`` is the compiled literal pool — comments
    contribute nothing to it — so this pin can only be satisfied by live code
    that actually compares the env var against the string "1".
    """
    marker = getattr(test_invite_link_resolves, "pytestmark", None)
    assert marker and any(getattr(m, "name", None) == "live" for m in marker), \
        "test_invite_link_resolves must carry @pytest.mark.live"
    consts = test_invite_link_resolves.__code__.co_consts
    assert "ALLOW_PROD" in consts and "1" in consts, \
        "test_invite_link_resolves must read ALLOW_PROD against '1' in code " \
        f"(a comment does not count): co_consts={consts!r}"
