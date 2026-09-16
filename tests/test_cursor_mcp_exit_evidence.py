"""Lane B2(c) exit evidence — Cursor connects end-to-end over MCP (#3657 / #3497).

The lane's exit criterion, verbatim:

    "Cursor connects end-to-end via MCP to the hosted server — DCR succeeds,
     the OAuth 2.1 flow completes, and a capture receipt is observed for the
     `cursor` harness."

The human-only step (driving a real Cursor/Claude desktop UI) is #3657. This
file is the AUTOMATED half: every leg is server-side and hermetic, so it runs
in CI. The chain driven here is

    POST /register            (Cursor's redirect payload)
      -> GET  /oauth/authorize    (validation leg)
      -> POST /oauth/consent      (browser-session JWT)
      -> POST /oauth/token        (PKCE S256 -> ``oat_`` access token)
      -> POST /mcp  tools/list    (``oat_`` authenticates the MCP boundary)
      -> POST /mcp  tools/call    ``tortoise_session_capture`` harness=cursor
      -> GET  /v1/onboarding/state -> ``session_capture_receipt_cursor``

Why the last leg is the lane's exit receipt
-------------------------------------------
``tortoise_session_capture`` is the T3 filing tool every MCP-surface harness
uses, and it calls the SAME hosted capture pipeline as ``POST /v1/sessions``
(``hosted_api._capture_session_impl``) so the two surfaces cannot drift on
gate order; it writes the per-harness receipt
``session_capture_receipt_cursor`` through the identical code path
(``hosted_api._capture_receipt_key``). A receipt produced over MCP is
therefore the same receipt the REST/CLI path produces — it proves a durable,
server-acknowledged capture with the ``cursor`` harness label.

The receipt is read by the dashboard's 4-state capture-status derivation
(``website/apps/dashboard/src/captureStatus.js``: receipt -> ``active``,
authoritative over the install probe) and is a REGISTERED onboarding-state
key, so it round-trips through ``GET /v1/onboarding/state``.

Two Cursor payloads, deliberately
---------------------------------
Cursor's real DCR payload is three entries — the documented
``https://www.cursor.com/agents/mcp/oauth/callback`` + loopback pair AND the
legacy ``cursor://anysphere.cursor-mcp/oauth/callback`` exthost scheme.

* ``TestCursorDocumentedPairEndToEnd`` runs TODAY: the https+loopback pair is
  registrable on ``main`` and drives the whole chain to the receipt.
* ``TestCursorRealPayloadEndToEnd`` sends the EXACT three-entry payload. It is
  gated on the private-use scheme being registrable — see
  ``_cursor_scheme_supported()``. Registration is all-or-nothing, so until
  #3579 (PR #3580, private-use scheme acceptance at DCR) lands, the real
  payload is rejected whole and a Cursor client never reaches
  ``/oauth/authorize``. The gate SKIPS with that reason rather than passing
  vacuously, and activates automatically once the scheme is accepted.

Nothing here reaches the network: the in-memory ``FakeControlPlane`` is the
control plane and the real FastAPI apps (OAuth AS + mounted MCP) run under
``TestClient``. This is a server-side confirmation — NOT an observed desktop
Cursor success (that is #3657).
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest

from tests.test_oauth_mcp import (
    _U1,
    _mcp_headers,
    _mounted_test_client,
    _parse_sse_json,
    _pkce,
    api_client,  # noqa: F401 — pytest fixture re-export
    session_user,  # noqa: F401 — pytest fixture re-export
    supabase_cp,  # noqa: F401 — pytest fixture re-export (api_client dep)
)
from tortoise import hosted_api as _ha
from tortoise.hosted_api import (
    _ALLOWED_STATE_KEYS,
    _ONBOARDING_DEFAULT_STATE,
    _SESSION_HARNESS_VALUES,
    OnboardingStatePatchRequest,
    _get_onboarding_state,
    app,  # noqa: F401 — the OAuth AS app under test
)
from tortoise.mcp_server import create_http_app
from tortoise.oauth import _valid_redirect_uri

# Cursor's three-entry DCR payload (see PR #3580's evidence section).
CURSOR_CB_HTTPS = "https://www.cursor.com/agents/mcp/oauth/callback"
CURSOR_CB_LOOPBACK = "http://localhost:8787/callback"
CURSOR_CB_SCHEME = "cursor://anysphere.cursor-mcp/oauth/callback"

RECEIPT_KEY = "session_capture_receipt_cursor"

_CONV = [
    {"role": "user", "content": "Cursor connected to Tortoise over MCP today."},
    {"role": "assistant", "content": "Confirmed — filing this session as the "
                                     "cursor-harness receipt evidence."},
    {"role": "user", "content": "ok"},
]


def _cursor_scheme_supported() -> bool:
    """Whether DCR accepts Cursor's private-use redirect scheme.

    False on ``main`` until #3579 (PR #3580) lands. The probe is the real
    validator, so this gate self-activates the moment the scheme is accepted —
    no hardcoded flag to rot.
    """
    return _valid_redirect_uri(CURSOR_CB_SCHEME)


@pytest.fixture(autouse=True)
def llm_extraction_provider(monkeypatch):
    """Offline MockModel session extractor (#822) — the capture leg runs with
    zero network regardless of ambient provider keys."""
    monkeypatch.setenv("TORTOISE_SESSION_LLM_MOCK", "1")


def _register_cursor_client(tc, redirect_uris: list[str]) -> object:
    """Raw DCR POST — the caller asserts the outcome (a fixture-style helper
    that asserted 201 could not express the real-payload rejection)."""
    return tc.post("/register", json={
        "client_name": "cursor",
        "redirect_uris": redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "mcp",
    })


def _consent(tc, *, client_id: str, redirect_uri: str, challenge: str) -> object:
    return tc.post("/oauth/consent", json={
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "cursor-st-1",
        "scope": "mcp",
    }, headers={"Authorization": "Bearer fake-session-jwt"})


def _mcp_call(mcp_tc, name: str, arguments: dict):
    return mcp_tc.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })


def _result_text(body: dict) -> str:
    return "".join(c.get("text", "") for c in
                   body.get("result", {}).get("content", []))


def _drive_cursor_connect(tc, *, client_id: str, redirect_uri: str) -> str:
    """authorize -> consent -> PKCE exchange; returns the ``oat_`` token.

    The authorize leg is driven first (its validation is part of the exit
    criterion); consent then mints the code and the token endpoint performs
    the S256 exchange.
    """
    verifier, challenge = _pkce()
    r = tc.get("/oauth/authorize", params={
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "scope": "mcp",
        "state": "cursor-st-1",
    })
    # A real client is redirected to its callback with the consent page; the
    # server must not reject the registered URI at this leg.
    assert r.status_code == 200, r.text

    r = _consent(tc, client_id=client_id, redirect_uri=redirect_uri,
                 challenge=challenge)
    assert r.status_code == 200, r.text
    code = r.json()["code"]
    assert code

    r = tc.post("/oauth/token", data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    })
    assert r.status_code == 200, r.text
    access = r.json()["access_token"]
    assert access.startswith("oat_"), access
    return access


def _assert_cursor_receipt_end_to_end(tc, cp, access: str, org_id: str,
                                      monkeypatch) -> str:
    """tools/list + the capture tool call + the receipt read-back.

    Returns the receipt value. Asserts the graph-side Session too, so the
    receipt cannot be satisfied by a marker without durable data (the
    T1-P12 receipt<->Session invariant).
    """
    mcp_tc = _mounted_test_client(create_http_app(allowed_origins=[]))
    mcp_tc.headers.update(_mcp_headers(access))
    with mcp_tc:
        rr = mcp_tc.post("/mcp", json={
            "jsonrpc": "2.0", "method": "tools/list", "id": 1})
        assert rr.status_code == 200, rr.text
        listed = _parse_sse_json(rr)
        assert "result" in listed, listed
        names = {t["name"] for t in listed["result"]["tools"]}
        assert "tortoise_session_capture" in names, \
            f"capture tool absent from the Cursor-facing tool list: {names}"

        rr = _mcp_call(mcp_tc, "tortoise_session_capture",
                       {"conversation": _CONV, "harness": "cursor"})
        assert rr.status_code == 200, rr.text
        body = _parse_sse_json(rr)
        assert "result" in body, body
        import json as _json
        payload = _json.loads(_result_text(body))
        assert payload.get("status") == "ok", f"capture tool errored: {payload}"
        assert payload.get("error") is None, f"capture tool errored: {payload}"
        assert payload["provenance"]["source_harness"] == "cursor", payload

    # Receipt observed on the org's onboarding state — the lane's exit marker.
    state = _get_onboarding_state(org_id)
    assert state.get(RECEIPT_KEY), \
        f"no {RECEIPT_KEY} after a cursor MCP capture: {state}"

    # ... and OBSERVABLE on the public read surface the dashboard consumes
    # (GET /v1/onboarding/state -> onboarding{} -> the 4-state capture-status
    # derivation in captureStatus.js, where a receipt resolves to `active`).
    # The session-JWT seam here is session_auth's verifier (get_current_user
    # resolves it in that module) — distinct from the OAuth consent seam that
    # test_oauth_mcp's session_user fixture patches.
    import tortoise.session_auth as _sa

    async def _fake_verify(_request):
        return {"user_id": _U1, "email": "u@example.com", "sub": _U1}

    monkeypatch.setattr(_sa, "verify_session_jwt", _fake_verify)
    r = tc.get("/v1/onboarding/state",
               headers={"Authorization": "Bearer eyJ.sess"})
    assert r.status_code == 200, r.text
    public = r.json()["onboarding"]
    assert public.get(RECEIPT_KEY), \
        f"{RECEIPT_KEY} not on the public onboarding-state surface: {public}"

    # The receipt is not an orphan marker: the Session is durable, harness-tagged.
    sdk = _ha._make_sdk(namespace=org_id)
    rows = sdk._get_proj().g.query(
        "MATCH (s:Session) RETURN s.id, s.harness").result_set
    assert rows, "capture receipt landed with no Session (T1-P12 violation)"
    assert any(r[1] == "cursor" for r in rows), \
        f"no cursor-harness Session persisted: {rows}"
    return state[RECEIPT_KEY]


class TestCursorDocumentedPairEndToEnd:
    """The Cursor payload registrable on ``main`` TODAY.

    Cursor's own documented callback pair — driving it end-to-end is the
    reproducible non-UI proof of the lane exit criterion.
    """

    def test_dcr_authorize_consent_token_tools_and_receipt(
            self, api_client, session_user, monkeypatch):  # noqa: F811
        tc, cp = api_client
        session_user()

        r = _register_cursor_client(
            tc, [CURSOR_CB_HTTPS, CURSOR_CB_LOOPBACK])
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["redirect_uris"] == [CURSOR_CB_HTTPS, CURSOR_CB_LOOPBACK]
        client_id = body["client_id"]

        access = _drive_cursor_connect(
            tc, client_id=client_id, redirect_uri=CURSOR_CB_LOOPBACK)
        receipt = _assert_cursor_receipt_end_to_end(
            tc, cp, access, "team-free-001", monkeypatch)
        assert receipt  # a server timestamp — truthy, non-empty

    def test_cursor_harness_is_in_the_capture_vocabulary(self):
        """The receipt only exists because ``cursor`` is a first-class capture
        harness. Pin all three registration surfaces — an unregistered key is
        SILENTLY DROPPED by the state allowlist, which would make this lane's
        exit marker unobservable."""
        assert "cursor" in _SESSION_HARNESS_VALUES
        assert RECEIPT_KEY in _ALLOWED_STATE_KEYS
        assert RECEIPT_KEY in _ONBOARDING_DEFAULT_STATE
        assert RECEIPT_KEY in OnboardingStatePatchRequest.model_fields, \
            "receipt key absent from the PATCH registration surface — an " \
            "unregistered key is silently dropped by the allowlist filter"


@pytest.mark.skipif(
    not _cursor_scheme_supported(),
    reason="Cursor's private-use redirect scheme is not registrable yet "
           "(#3579 / PR #3580). Registration is all-or-nothing, so the real "
           "payload is rejected whole and a Cursor client cannot reach "
           "/oauth/authorize. This test activates automatically once the "
           "scheme is accepted.")
class TestCursorRealPayloadEndToEnd:
    """Cursor's EXACT three-entry DCR payload, all the way to the receipt."""

    def test_real_payload_connects_and_observes_receipt(
            self, api_client, session_user, monkeypatch):  # noqa: F811
        tc, cp = api_client
        session_user()

        payload = [CURSOR_CB_HTTPS, CURSOR_CB_LOOPBACK, CURSOR_CB_SCHEME]
        r = _register_cursor_client(tc, payload)
        assert r.status_code == 201, (
            f"Cursor's real DCR payload was rejected whole: {r.text}")
        assert r.json()["redirect_uris"] == payload
        client_id = r.json()["client_id"]

        # The exthost path is the one Cursor actually uses for the code
        # delivery, so the custom scheme is the redirect_uri for the whole
        # flow — authorize, consent, PKCE exchange — not just DCR.
        access = _drive_cursor_connect(
            tc, client_id=client_id, redirect_uri=CURSOR_CB_SCHEME)
        receipt = _assert_cursor_receipt_end_to_end(
            tc, cp, access, "team-free-001", monkeypatch)
        assert receipt
