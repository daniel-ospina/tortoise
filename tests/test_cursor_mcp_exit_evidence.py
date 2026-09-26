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

Acceptance — mutation-reds / legitimate-green
---------------------------------------------
A test that reads a file and greps it reports on a SPELLING; this suite
reports on BEHAVIOUR. Every claim below is driven through the real handler
(the OAuth AS app + the mounted MCP app under ``TestClient`` over one
in-memory ``FakeControlPlane``) and compared against what the server
RESOLVED — never against a literal re-typed here. Two consequences:

* the receipt key is RESOLVED through the production derivation
  (``hosted_api._capture_receipt_key``); the literal spelling the exit
  criterion names is pinned exactly ONCE, explicitly, as a contract in
  ``TestCursorHarnessRegistration``;
* the capture's receipt is asserted on the VALUE the production state setter
  receives (``_update_onboarding_state`` wrapped, recording + delegating) —
  deep-equal to the tool payload's ``provenance.ingested_at`` — not on a
  truthy read-back, which any other writer could satisfy.

| # | Exit claim | Mutation that REDS it | Legitimate form that stays GREEN |
|---|---|---|---|
| 1 | Cursor's client registers via DCR (201) with its redirect set | ``register_client`` drops/rewrites a registered URI | extra URIs registered; a different client name/scope |
| 2 | ``/oauth/authorize`` accepts the REGISTERED redirect | the authorize-leg redirect validation is deleted → 400/OAuthError | consent-page markup/template changes |
| 3 | The consent leg resolves the redirect target back to the registered URI | ``oauth_consent`` echoes a re-derived/divergent URI | the loopback matcher is relaxed (native-client portless form) — still the URI the client sent |
| 4 | The token leg binds redemption to that SAME resolved redirect | the ``redirect_uri != code_row["redirect_uri"]`` check is dropped (``TestCursorRedirectBinding`` reds) | internal token hashing/mint refactors |
| 5 | PKCE S256 exchange mints an ``oat_`` token | verifier check removed, or a non-``oat_`` token returned | the prefix helper/rotation internals change; ``oat_`` stays the contract |
| 6 | The ``oat_`` token authenticates the MCP boundary and lists the capture tool | ``mcp_auth`` drops the ``resolve_oauth_access_token`` leg → 401 | more tools are registered (asserted by membership, not by exact list) |
| 7 | The capture handler hands the state setter the receipt key with the payload's stamp | the receipt write is removed, or writes a different value | refactors of the capture path — the key comes from the production derivation and the stamp from the response payload, so neither is re-spelled. A change to the DERIVATION itself reds the single contract pin by design (it is a dashboard contract) |
| 8 | The receipt is observable on ``GET /v1/onboarding/state`` and on a fresh org's defaults | the key leaves ``_ALLOWED_STATE_KEYS`` / the default state / the PATCH model | the read-projection internals change; the key still round-trips |
| 9 | The receipt is not an orphan marker — a durable ``cursor``-tagged Session exists | the Session MERGE stops stamping ``harness`` | Session property names are added alongside ``harness`` |

Verified, not asserted-on-paper: removing the receipt write, and writing a
different value, were each applied to the real handler —
``TestCursorDocumentedPairEndToEnd`` reds with the state-setter message in both
cases; dropping the token leg's ``redirect_uri`` binding reds
``TestCursorRedirectBinding``. The legitimate-green side was confirmed by
re-running this suite with a DIFFERENT client loopback port — all green, because
the assertions compare against what the client sent / the server resolved,
never against a literal.

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
    SessionRequest,
    _get_onboarding_state,
    app,  # noqa: F401 — the OAuth AS app under test
)
from tortoise.mcp_server import create_http_app
from tortoise.oauth import _valid_redirect_uri

# Cursor's three-entry DCR payload (see PR #3580's evidence section).
CURSOR_CB_HTTPS = "https://www.cursor.com/agents/mcp/oauth/callback"
CURSOR_CB_LOOPBACK = "http://localhost:8787/callback"
CURSOR_CB_SCHEME = "cursor://anysphere.cursor-mcp/oauth/callback"

# RESOLVED through the production derivation, never re-spelled here: a test
# asserting its own copy of the derivation rule is evidence about a spelling,
# not about what the handler writes. The literal the exit criterion names is
# pinned exactly once, explicitly — TestCursorHarnessRegistration.
RECEIPT_KEY = _ha._capture_receipt_key("cursor")

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


def _authorize_and_consent(tc, *, client_id: str,
                           redirect_uri: str) -> tuple[str, str]:
    """authorize -> consent; returns ``(code_verifier, code)``.

    (a) the request's RESOLVED path is asserted HERE, spelling-independent:
    the consent leg hands back the redirect target the server resolved for
    THIS request, compared against the URI the client registered (passed in).
    Driving the authorize leg first is part of the exit criterion — a real
    client is redirected to its callback with the consent page, so the server
    must not reject the registered URI at that leg either.
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
    assert r.status_code == 200, r.text

    r = _consent(tc, client_id=client_id, redirect_uri=redirect_uri,
                 challenge=challenge)
    assert r.status_code == 200, r.text
    consented = r.json()
    assert consented["code"], consented
    assert consented["redirect_uri"] == redirect_uri, consented
    assert consented["state"] == "cursor-st-1", consented
    return verifier, consented["code"]


def _exchange_code(tc, *, client_id: str, redirect_uri: str,
                   verifier: str, code: str) -> str:
    """PKCE S256 exchange at the token endpoint; returns the ``oat_`` token."""
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


def _drive_cursor_connect(tc, *, client_id: str, redirect_uri: str) -> str:
    """authorize -> consent -> PKCE exchange; returns the ``oat_`` token."""
    verifier, code = _authorize_and_consent(
        tc, client_id=client_id, redirect_uri=redirect_uri)
    return _exchange_code(tc, client_id=client_id, redirect_uri=redirect_uri,
                          verifier=verifier, code=code)


def _patch_session_auth(monkeypatch) -> None:
    """Install the session-JWT verifier seam for the onboarding-state surface.

    ``GET``/``PATCH /v1/onboarding/state`` resolve the session through
    ``session_auth.get_current_user`` → the ``session_auth`` module's
    ``verify_session_jwt`` — a DIFFERENT seam from the OAuth-consent leg,
    which ``test_oauth_mcp``'s ``session_user`` fixture patches in
    ``hosted_api``. Both must be installed for one request to clear session
    auth; the header's value is never decoded once both are in place.
    """
    import tortoise.session_auth as _sa

    async def _fake_verify(_request):
        return {"user_id": _U1, "email": "u@example.com", "sub": _U1}

    monkeypatch.setattr(_sa, "verify_session_jwt", _fake_verify)


def _assert_cursor_receipt_end_to_end(tc, cp, access: str, org_id: str,
                                      monkeypatch,
                                      *, harness: str = "cursor") -> str:
    """tools/list + the capture tool call + the receipt read-back.

    (b) the VALUE the state setter receives: the production writer
    ``_update_onboarding_state`` is WRAPPED (recording every field handed to
    it, then delegating to the real writer), so this asserts what the handler
    actually wrote — the key the production derivation resolves, with a value
    deep-equal to the tool payload's ``provenance.ingested_at``. A truthiness
    read-back alone cannot distinguish that from any other path dropping a
    marker on the org.

    Also asserts the graph-side Session, so the receipt cannot be satisfied by
    a marker without durable data (the T1-P12 receipt<->Session invariant).
    """
    written: list[dict] = []
    _real_update = _ha._update_onboarding_state

    def _recording_update(oid, **fields):
        written.append({"org_id": oid, **fields})
        return _real_update(oid, **fields)

    monkeypatch.setattr(_ha, "_update_onboarding_state", _recording_update)

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
                       {"conversation": _CONV, "harness": harness})
        assert rr.status_code == 200, rr.text
        body = _parse_sse_json(rr)
        assert "result" in body, body
        import json as _json
        payload = _json.loads(_result_text(body))
        assert payload.get("status") == "ok", f"capture tool errored: {payload}"
        assert payload.get("error") is None, f"capture tool errored: {payload}"
        assert payload["provenance"]["source_harness"] == harness, payload
        ingested_at = payload["provenance"]["ingested_at"]
        assert ingested_at, payload

    # (b) what the state setter ACTUALLY received for the receipt key.
    receipt_writes = [w for w in written if RECEIPT_KEY in w]
    assert receipt_writes, (
        f"the capture handler never handed {RECEIPT_KEY} to the state "
        f"setter — fields seen: {written}")
    assert receipt_writes[-1]["org_id"] == org_id, receipt_writes
    assert receipt_writes[-1][RECEIPT_KEY] == ingested_at, (
        f"state setter received {receipt_writes[-1][RECEIPT_KEY]!r} for "
        f"{RECEIPT_KEY}; the tool payload said {ingested_at!r}")

    # Receipt observed on the org's onboarding state — the lane's exit marker.
    state = _get_onboarding_state(org_id)
    assert state.get(RECEIPT_KEY) == ingested_at, \
        f"no {RECEIPT_KEY}={ingested_at!r} after a {harness} MCP capture: {state}"

    # ... and OBSERVABLE on the public read surface the dashboard consumes
    # (GET /v1/onboarding/state -> onboarding{} -> the 4-state capture-status
    # derivation in captureStatus.js, where a receipt resolves to `active`).
    _patch_session_auth(monkeypatch)
    r = tc.get("/v1/onboarding/state",
               headers={"Authorization": "Bearer eyJ.sess"})
    assert r.status_code == 200, r.text
    public = r.json()["onboarding"]
    assert public.get(RECEIPT_KEY) == ingested_at, \
        f"{RECEIPT_KEY} not on the public onboarding-state surface at " \
        f"{ingested_at!r}: {public}"

    # The receipt is not an orphan marker: the Session is durable, harness-tagged.
    sdk = _ha._make_sdk(namespace=org_id)
    rows = sdk._get_proj().g.query(
        "MATCH (s:Session) RETURN s.id, s.harness").result_set
    assert rows, "capture receipt landed with no Session (T1-P12 violation)"
    assert any(r[1] == harness for r in rows), \
        f"no {harness}-harness Session persisted: {rows}"
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
        # the receipt IS the server-stamped value the state setter received
        # (deep-equal'd against the tool payload inside the helper)
        assert isinstance(receipt, str) and receipt


class TestCursorHarnessRegistration:
    """``cursor`` is a first-class capture harness and its receipt key is
    registered on every surface the write/read path touches.

    Replaces the constant-membership scan this suite used to carry
    (``"cursor" in _SESSION_HARNESS_VALUES``, ``RECEIPT_KEY in
    _ALLOWED_STATE_KEYS`` / ``_ONBOARDING_DEFAULT_STATE`` / the PATCH
    model_fields). A scan reports that three frozensets contain a string — not
    that the behaviour they gate actually holds. Each test below EXECUTES the
    thing the register protects: the Pydantic harness validator, the state
    allowlist filter, the defaults merge, the PATCH round-trip.
    """

    def test_receipt_key_literal_is_pinned_once(self):
        """The ONE explicit spelling assertion in this file.

        The exit criterion names ``session_capture_receipt_cursor`` (the
        dashboard's capture-status derivation reads exactly that key), so the
        spelling is a contract — pinned here in the open, while every
        behavioural assertion resolves the key through the production
        derivation instead.
        """
        assert RECEIPT_KEY == "session_capture_receipt_cursor"
        # ... and the derivation is per-harness, with the legacy no-harness form
        assert _ha._capture_receipt_key("codex") == \
            "session_capture_receipt_codex"
        assert _ha._capture_receipt_key(None) == "session_capture_receipt"

    def test_capture_validator_accepts_cursor_and_refuses_a_typo(self):
        """Behaviour behind ``cursor in _SESSION_HARNESS_VALUES``: the real
        ``SessionRequest`` validator — the boundary that would 422 a Cursor
        capture — admits ``cursor`` and refuses an unregistered harness."""
        from pydantic import ValidationError

        assert SessionRequest(conversation=_CONV, harness="cursor").harness \
            == "cursor"
        with pytest.raises(ValidationError):
            SessionRequest(conversation=_CONV, harness="cursor-typo")

    def test_receipt_key_is_registered_but_server_owned_on_patch(
            self, api_client, session_user, monkeypatch):  # noqa: F811
        """Behaviour behind the registration surfaces, executed rather than
        scanned — under the #3681 server-owned rule.

        * ``_ONBOARDING_DEFAULT_STATE`` — a FRESH org's GET already carries
          the receipt key (unset), because the read merges defaults;
        * ``OnboardingStatePatchRequest`` + ``_ALLOWED_STATE_KEYS`` — a PATCH
          of a NON-server-owned registered key still round-trips to the
          persisted state and back out (the surface stays open for FLOW keys);
        * **#3681** — the receipt key itself is SERVER-OWNED evidence: a PATCH
          of it is REFUSED (403 ``server_owned_key``) and persists nothing, so
          a client can no longer fabricate the receipt ``captureStatus.js``
          reads to pick the capture sentence's present tense;
        * negative control — an UNREGISTERED spelling is silently dropped.
          Without it the positive assertion could pass vacuously on a filter
          that admits everything.
        """
        tc, _ = api_client
        session_user()
        _patch_session_auth(monkeypatch)
        auth = {"Authorization": "Bearer eyJ.sess"}

        r = tc.get("/v1/onboarding/state", headers=auth)
        assert r.status_code == 200, r.text
        fresh = r.json()["onboarding"]
        assert RECEIPT_KEY in fresh, fresh
        assert fresh[RECEIPT_KEY] is None, fresh

        # A registered FLOW key (not server-owned evidence) still round-trips.
        r = tc.patch("/v1/onboarding/state", json={"prompt_pasted": True},
                     headers=auth)
        assert r.status_code == 200, r.text
        assert r.json()["onboarding"]["prompt_pasted"] is True, r.text
        r = tc.get("/v1/onboarding/state", headers=auth)
        assert r.json()["onboarding"]["prompt_pasted"] is True

        # The receipt itself is SERVER-OWNED (#3681): refused, nothing
        # persisted — no client-fabricated present-tense capture claim.
        stamp = "2026-01-01T00:00:00+00:00"  # server-time in prod; any str here
        r = tc.patch("/v1/onboarding/state", json={RECEIPT_KEY: stamp},
                     headers=auth)
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == {
            "message": "server_owned_key", "keys": [RECEIPT_KEY]}, r.text
        r = tc.get("/v1/onboarding/state", headers=auth)
        assert r.json()["onboarding"][RECEIPT_KEY] is None, \
            "a client-fabricated capture receipt was persisted"

        typo = RECEIPT_KEY[:-1] + "x"
        r = tc.patch("/v1/onboarding/state", json={typo: stamp}, headers=auth)
        assert r.status_code < 500, r.text
        r = tc.get("/v1/onboarding/state", headers=auth)
        assert typo not in r.json()["onboarding"], \
            "an unregistered key was NOT dropped — the filter is not gating"


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
        assert isinstance(receipt, str) and receipt


class TestCursorRedirectBinding:
    """The resolved redirect is bound for the WHOLE flow, not just DCR.

    A server that resolved the redirect only at registration (and never bound
    it into the code / redemption) would pass the happy-path tests above. This
    control reds the moment the token leg stops binding the redemption to the
    redirect the code was minted for (RFC 6749 §4.1.3).
    """

    def test_token_leg_refuses_a_different_registered_redirect(
            self, api_client, session_user):  # noqa: F811
        tc, _ = api_client
        session_user()

        r = _register_cursor_client(tc, [CURSOR_CB_HTTPS, CURSOR_CB_LOOPBACK])
        assert r.status_code == 201, r.text
        client_id = r.json()["client_id"]

        verifier, code = _authorize_and_consent(
            tc, client_id=client_id, redirect_uri=CURSOR_CB_LOOPBACK)
        # BOTH URIs are registered for this client, so the swap is legal at
        # DCR — and must still be refused at redemption.
        r = tc.post("/oauth/token", data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": CURSOR_CB_HTTPS,
            "client_id": client_id,
            "code_verifier": verifier,
        })
        assert r.status_code == 400, r.text
        assert r.json()["error"] == "invalid_grant", r.text
