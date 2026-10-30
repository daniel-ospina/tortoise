"""#3524 §9 step 4 — the consent page stops signing people in.

The pre-#4054 design gave the AS a SECOND identity system: the consent page
(`tortoise/oauth.py::_CONSENT_HTML`) ran an inline supabase-js client and wrote a
JS-readable `Domain=.premiselabs.co` cookie, so it could sign the visitor in
itself. Step 4 deletes that outright:

  * the page offers and performs NO sign-in — no provider buttons, no password
    form, no cookie write, no auth client;
  * an unauthenticated `GET /oauth/authorize` **302s to the app's sign-in leg**
    (`{app}/auth?next=/api/consent/start?{complete authorize query}`), because
    the AS cannot read the app's host-only HttpOnly session and must not guess;
  * the authenticated POST of the page carries the bound single-use
    **consent token** minted by `POST {app}/api/consent/token` — the page is that
    mint's ONLY caller, so the per-attempt key (`attempt_key`) has to reach it
    from the page's OWN URL or every mint answers `403 no_authorize_attempt`
    AFTER the visitor approves.

This file REPLACES `tests/test_oauth_consent_pkce.py` wholesale (§9 step 4's
"test-surface migration"): its invariants were about the inline client — the
grant type, the verifier's storage home, the cookie write path, the capability
probe — and none of them has a subject now. Two of its properties survive and
are re-borne here in the new terms: the #6001 single-pass substitution invariant
(there is one inline <script>, and it must still be emitted verbatim-safe) and
the #3443 navigation invariant (the page navigates the SERVER-rebuilt target).

⛔ The three constraints that decided the last two review rounds are pinned
here, each on the artefact rather than on a string this file also writes:
1. the attempt key is taken FROM A `start` REDIRECT's URL and sent to the mint
   (`test_the_page_mints_with_the_key_from_its_own_url` + the cross-half
   constant pin, which reads the TS authority);
2. the return leg is admitted by `signup.html`'s REAL `APP_RETURN_ROUTES`, which
   is EXECUTED here (a test that asserted the Location string while the
   allow-list would drop it is the bug, not the proof);
3. opaque params are never trimmed — asserted byte-for-byte through the bounce
   and into the page's POST.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

from tests._http_fixtures import patched_tortoise_sdk
from tests._verdict import verdict_client
from tests.fake_control_plane import FakeControlPlane
from tests.test_oauth_mcp import (
    _U1,
    TEAM_FREE,
    TEAM_TEAM,
    _enable_supabase,
    _member,
)
from tests.test_session_bridge_fragment_retention import _require_node
from tortoise.hosted_api import app

REPO_ROOT = Path(__file__).resolve().parents[1]
OAUTH = REPO_ROOT / "tortoise" / "oauth.py"
HOSTED_API = REPO_ROOT / "tortoise" / "hosted_api.py"
SIGNUP_HTML = REPO_ROOT / "website" / "apps" / "dashboard" / "public" / "signup.html"
CONSENT_BRIDGE_TS = (
    REPO_ROOT / "website" / "apps" / "dashboard" / "functions" / "_shared"
    / "auth" / "consent-bridge.ts"
)
START_TS = REPO_ROOT / "website" / "apps" / "dashboard" / "functions" / "api" / "consent" / "start.ts"
TOKEN_TS = REPO_ROOT / "website" / "apps" / "dashboard" / "functions" / "api" / "consent" / "token.ts"

APP_ORIGIN = "https://app.premiselabs.co"
REDIRECT = "http://127.0.0.1:8765/callback"

# ── the AS client, built HERE on purpose ────────────────────────────────────
# These three MIRROR `tests/test_oauth_mcp.py`'s fixtures (the file that owns the
# OAuth protocol suite). They are defined locally rather than imported because
# importing a pytest fixture into another module makes ruff read every fixture
# PARAMETER as an F811 redefinition of the import, on ~20 lines, and this repo's
# ruff config carries no per-file ignore for it. Only the plain helpers/constants
# are imported. If the AS client's construction changes, change BOTH files.


@pytest.fixture
def supabase_cp(monkeypatch) -> FakeControlPlane:
    """Supabase mode on + a fake control plane seeded with two teams, _U1 in one."""
    cp = FakeControlPlane({
        "organizations": [dict(TEAM_FREE), dict(TEAM_TEAM)],
        "org_memberships": [_member(_U1, "team-free-001")],
        "api_keys": [],
    })
    _enable_supabase(monkeypatch, cp)
    return cp


@pytest.fixture
def api_client(supabase_cp, monkeypatch):
    """TestClient over the real hosted_api app (OAuth + well-known routes).

    `CONSENT_TOKEN_SECRET` is set here because a *configured* AS is what these
    tests model: since §9 step 4 the page cannot approve without the AS holding
    the key the BFF minted with, so `/oauth/authorize` refuses to RENDER one
    without it (typed 503, `oauth_authorize`). The refusal itself is pinned
    separately, with the variable removed.
    """
    monkeypatch.setenv("CONSENT_TOKEN_SECRET", _TEST_SECRET)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "oauth.db")
        with patched_tortoise_sdk(db_path), verdict_client(app) as tc:
            yield tc, supabase_cp


@pytest.fixture
def session_user(api_client, monkeypatch):
    """Stub the browser-session JWT verification (the JWKS path has its own suite)."""

    def _set(user_id: str = _U1, email: str = "u@example.com"):
        async def _fake(request):
            return {"user_id": user_id, "email": email}

        monkeypatch.setattr("tortoise.hosted_api.verify_session_jwt", _fake)

    yield _set


# ═══════════════════════════════════════════════════════════════════════════
# The AS client (the real app, the in-memory control plane)
# ═══════════════════════════════════════════════════════════════════════════

def _register_client(tc, **overrides) -> dict:
    body = {
        "client_name": "test-connector",
        "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "mcp",
    }
    body.update(overrides)
    r = tc.post("/register", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _pkce() -> tuple[str, str]:
    import base64
    import hashlib
    import secrets

    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _authorize_params(tc, *, state: str = "st-1",
                      redirect_uri: str = REDIRECT,
                      client: dict | None = None, **extra) -> dict:
    reg = _register_client(tc, redirect_uris=[redirect_uri], **(client or {}))
    _verifier, challenge = _pkce()
    params = {
        "client_id": reg["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": "mcp",
        "resource": "",
    }
    params.update(extra)
    return params


def _split(location: str) -> tuple[str, dict, str]:
    """``(base, query dict, RAW query)`` — the ONE decode, plus the bytes.

    The raw query is what a browser receives, so the allowlist test feeds THIS
    string to signup.html rather than re-assembling one: a `next` value rebuilt
    from the parsed dict would lose the percent-encoding the real bounce carries,
    and an allow-list check run on a value the server never emits proves nothing.
    """
    from urllib.parse import parse_qs, urlsplit

    parts = urlsplit(location)
    assert parts.query, f"the bounce carries no query: {location}"
    return (f"{parts.scheme}://{parts.netloc}{parts.path}",
            {k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()},
            parts.query)


def _bounce(tc, params: dict):
    r = tc.get("/oauth/authorize", params=params, follow_redirects=False)
    assert r.status_code == 302, r.text
    base, q, raw = _split(r.headers["location"])
    assert base == APP_ORIGIN + "/auth", (
        f"the bounce must land on the app's sign-in page, got {base!r}")
    return r, q, raw


# ═══════════════════════════════════════════════════════════════════════════
# 1. The unauthenticated bounce
# ═══════════════════════════════════════════════════════════════════════════

def test_an_unauthenticated_authorize_request_302s_to_the_app_signin_leg(api_client):
    """No attempt key ⇒ no evidence of a session ⇒ the APP decides.

    The AS cannot read `__Host-session` (host-only to `app.*`, HttpOnly), so
    "unauthenticated" is not a judgement it can make — it bounces, carrying the
    COMPLETE request so nothing has to be reconstructed from memory on the way
    back. This is the assertion the old page made impossible: it answered 200
    and offered its own sign-in form.
    """
    tc, _ = api_client
    params = _authorize_params(tc)
    _r, q, _raw = _bounce(tc, params)
    assert "next" in q, q

    # ── the COMPLETE authorize URL is recoverable from the return target ────
    from urllib.parse import parse_qs, urlsplit

    inner = q["next"]
    assert inner.startswith("/api/consent/start?"), (
        "the return target must be the consent bridge's start route — the only "
        f"API path signup.html's allowlist admits, got {inner!r}")
    inner_parts = urlsplit(inner)
    recovered = {k: v[0] for k, v in parse_qs(
        inner_parts.query, keep_blank_values=True).items()}
    for key, value in params.items():
        assert recovered.get(key) == value, (
            f"{key!r} did not survive the bounce: {recovered.get(key)!r} != {value!r}")


def test_opaque_params_are_carried_through_the_bounce_byte_exactly(api_client):
    """`state` and `redirect_uri` are compared BYTE-FOR-BYTE by the client.

    A validator or a serialiser that normalises them makes a client whose
    `state` carries padding/`+`/percent-escapes abandon the callback as a CSRF
    mismatch AFTER the visitor consented — the silent, post-consent class the
    bridge refuses everywhere else. `+` and `%2F` are the two shapes a
    parse-and-re-serialise round trip changes.
    """
    tc, _ = api_client
    state = "a+b c%2Fd  padded=="
    params = _authorize_params(tc, state=state)
    _r, q, _raw = _bounce(tc, params)
    from urllib.parse import parse_qs, urlsplit

    recovered = {k: v[0] for k, v in parse_qs(
        urlsplit(q["next"]).query, keep_blank_values=True).items()}
    assert recovered["state"] == state, (
        f"`state` was normalised somewhere on the bounce: {recovered['state']!r} "
        f"!= {state!r}")
    assert recovered["redirect_uri"] == REDIRECT


def test_the_bounce_return_target_is_admitted_by_the_REAL_signup_allowlist(
        api_client, tmp_path):
    """/auth is signup.html, whose return-to is allow-list only.

    `if (!allowed) return;` — anything else is SILENTLY dropped and the visitor
    lands on the app root, losing the flow (that is how a signed-in visitor's
    consent would be abandoned on every cold browser). So the proof cannot be
    "the Location string says /api/consent/start"; it has to be the page's own
    predicate ACCEPTING it. This test therefore EXTRACTS signup.html's real
    scripts and runs them under node against the exact URL the server emits.
    """
    _require_node()
    tc, _ = api_client
    params = _authorize_params(tc, state="st-allow")
    _r, q, raw = _bounce(tc, params)
    inner = q["next"]

    page = SIGNUP_HTML.read_text()
    scripts = re.findall(r"<script>([\s\S]*?)</script>", page)
    assert len(scripts) >= 2, (
        "signup.html no longer opens with the dashboard-origin helper + the "
        "return-to allowlist block this guard extracts")
    # The bootstrap (`__DASHBOARD_BASE_URL` + dashboardOrigin/dashboardBase) and
    # the allowlist IIFE — in the order the browser runs them.
    driver = r"""
'use strict';
const vm = require('vm');
const blocks = JSON.parse(process.argv[2]);
const search = process.argv[3];
const origin = process.argv[4];
const window = { location: { search, hash: '', origin, pathname: '/auth' } };
const ctx = vm.createContext({ window, URL, URLSearchParams, JSON, console, String });
for (const src of blocks) vm.runInContext(src, ctx, { filename: 'signup.html' });
process.stdout.write(JSON.stringify({
  returnTo: window.__ADMIN_RETURN_TO === undefined ? null : window.__ADMIN_RETURN_TO,
  base: window.__DASHBOARD_BASE_URL === undefined ? null : window.__DASHBOARD_BASE_URL,
}));
"""
    script_path = tmp_path / "signup-allowlist.js"
    script_path.write_text(driver)
    proc = subprocess.run(
        ["node", str(script_path), json.dumps(scripts[:2]), "?" + raw,
         APP_ORIGIN],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["returnTo"] == inner, (
        "signup.html's own return-to allowlist DROPPED the server's bounce "
        f"target ({out['returnTo']!r} != {inner!r}) — the visitor would land on "
        "the app root and the consent flow would be lost. Either emit a route "
        "the list admits or extend the list deliberately.")
    assert out["base"] == APP_ORIGIN + inner, (
        "the post-sign-in navigation target is not the consent flow's start "
        f"route: {out['base']!r}")


def test_an_invalid_authorize_request_still_gets_its_oauth_error_not_a_bounce(api_client):
    """Validation runs BEFORE the bounce: a malformed request is the CLIENT's
    error (RFC 6749 §4.1.2.1), delivered to its registered redirect — not a
    navigation to the app, which would have to fail there instead."""
    tc, _ = api_client
    reg = _register_client(tc)
    _verifier, challenge = _pkce()
    r = tc.get("/oauth/authorize", params={
        "client_id": reg["client_id"],
        # a redirect_uri the client never registered → invalid_request
        "redirect_uri": "http://127.0.0.1:8765/other",
        "response_type": "code", "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "st-1", "scope": "mcp"}, follow_redirects=False)
    assert r.status_code == 400, r.text
    assert r.json()["error"] == "invalid_request", r.text
    assert "location" not in {k.lower() for k in r.headers}, (
        "an unregistered `redirect_uri` must NOT be navigated to — that IS the "
        f"open redirect: {r.headers.get('location')!r}")

    # …and the registered case RELAYS the error to the client's own redirect,
    # with `state` echoed — RFC 6749 §4.1.2.1's "inform the client" half, which a
    # blanket 400 would drop.
    relayed = tc.get("/oauth/authorize", params={
        "client_id": reg["client_id"],
        "redirect_uri": REDIRECT,
        "response_type": "token",          # this client registers only "code"
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "st+1 /2", "scope": "mcp"}, follow_redirects=False)
    assert relayed.status_code in (302, 303, 307), relayed.text
    from urllib.parse import parse_qs, urlsplit
    parts = urlsplit(relayed.headers["location"])
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == REDIRECT, parts
    relayed_q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert relayed_q["error"] == "invalid_request", relayed_q
    assert relayed_q["state"] == "st+1 /2", (
        f"the relayed error must echo the client's `state` byte-exactly: {relayed_q}")
    assert "/auth?next=" not in relayed.headers["location"], (
        "an invalid authorize request must never be forwarded to the app's "
        "sign-in leg — the app cannot fix a client error")


def test_an_authorize_request_WITHOUT_state_is_the_clients_error_not_a_dead_end(
        api_client):
    """`state` is not required by RFC 6749, and the AS's own `bind` admits
    `state: null` — but the APP's `start.ts`/`token.ts` use `state` as the
    attempt's identity and refuse an empty one with a bare JSON 400.

    So a state-less request that bounced would hand the visitor a JSON body in
    the browser: no page, no link, no recovery, nothing naming the field. Refused
    HERE it is the client's error, delivered at the client's own registered
    redirect with `state` echoed — the same shape as every other refusal
    (`RFC 6749 §4.1.2.1`).
    """
    tc, _ = api_client
    reg = _register_client(tc)
    _verifier, challenge = _pkce()
    params = {"client_id": reg["client_id"], "redirect_uri": REDIRECT,
              "response_type": "code", "code_challenge": challenge,
              "code_challenge_method": "S256", "scope": "mcp",
              "attempt_key": "ak-no-state"}          # no `state`
    r = tc.get("/oauth/authorize", params=params, follow_redirects=False)
    assert r.status_code in (302, 303, 307), (
        f"a state-less authorize request must be refused to the CLIENT, not "
        f"bounced to the app where it dies as JSON: {r.status_code} {r.text[:200]}")
    from urllib.parse import parse_qs, urlsplit
    parts = urlsplit(r.headers["location"])
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == REDIRECT, parts
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert q["error"] == "invalid_request", q
    # ⛔ The TARGET is what discriminates: without the refusal this request would
    # 302 to `{app}/auth?next=…` (the bounce), which is the dead end — the app's
    # `start.ts` answers a state-less `next` with a bare JSON 400. `q` also proves
    # the relay carried `state` (empty here), i.e. this is the authorize error
    # shape and not some other redirect.
    assert "/auth?next=" not in r.headers["location"], (
        "a request the app's own routes will refuse must not be forwarded there")
    # …and it is the authorize-error shape, not some other redirect: the relay
    # adds `state` only when the request has one, so its ABSENCE here is expected
    # and its presence would be a lie about the flow. What matters is the code.
    assert set(q) <= {"error", "state", "error_description"}, q


def test_the_served_button_ships_DISABLED_and_the_page_only_armss_it_later(
        api_client, tmp_path):
    """Fail-closed at PARSE time, not at script time.

    The page is only safe if Authorize is inert before any of its logic runs — a
    network stall between paint and the mint would otherwise show an armed button
    for a flow with no assertion. Pinned on the SERVED MARKUP (the attribute), and
    the harness now reads that attribute rather than assuming an element's initial
    state, so removing `disabled` from the template reddens this test.
    """
    tc, _ = api_client
    params = _authorize_params(tc, attempt_key="ak-markup")
    r = tc.get("/oauth/authorize", params=params)
    assert r.status_code == 200, r.text
    assert re.search(r'<button[^>]*id="btn-auth"[^>]*\bdisabled\b', r.text), (
        "the served Authorize button is not disabled in the MARKUP — the page "
        "would be armed from first paint")
    # …and the harness refuses to invent an element the served page does not have,
    # so a renamed id is a TypeError instead of a silent no-op (asserted by
    # construction in `_PAGE_DRIVER`; this test proves the driver still agrees).
    out = _run_page(r.text, {"search": "?" + (r.request.url.query.decode()
                                              if isinstance(r.request.url.query, bytes)
                                              else r.request.url.query),
                             "responses": [
                                 {"status": 200, "body": {"token": "ct1.x.y"}},
                                 _SOLE_ORG]}, tmp_path)
    assert out["afterLoad"]["authDisabled"] is False, (
        "a resolved org + a token must ARM the button after load — otherwise the "
        "markup's `disabled` is never lifted and the flow cannot be completed")


def test_a_request_carrying_an_attempt_key_renders_the_page(api_client):
    """The attempt key is the AS's only evidence the app advanced this flow.

    Anything else bounces. It is not a credential: a forged key renders a page
    whose mint refuses (`403 no_authorize_attempt`) — a typed refusal, never a
    code — which the page test below pins.
    """
    tc, _ = api_client
    params = _authorize_params(tc, attempt_key="ak-abcdef")
    r = tc.get("/oauth/authorize", params=params, follow_redirects=False)
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    assert "test-connector" in r.text


# ═══════════════════════════════════════════════════════════════════════════
# 2. The page performs no sign-in — the deletion, asserted as an ABSENCE
# ═══════════════════════════════════════════════════════════════════════════

#: Every artefact of the deleted second identity system. One tuple per family so
#: a failure names WHICH one came back, and the comments say what each load-bears
#: — the point is that a reviewer re-introducing one of them should understand
#: what they are re-introducing rather than see a bare grep.
_DELETED_FROM_THE_PAGE = [
    # the JS-readable parent-domain cookie jar: a credential any script on any
    # *.premiselabs.co sibling can read.
    "sb-tortoise-auth-token",
    "document.cookie",
    "COOKIE_DOMAIN",
    # the inline auth client.
    "supabase-js",
    "createClient(",
    "onAuthStateChange(",
    "flowType",
    # the sign-in surface itself.
    'id="btn-github"',
    'id="btn-google"',
    'type="password"',
    "signInWithPassword",
    "pkceIncapable",
    # the bearer the page used to mint for itself.
    "access_token",
]


def _rendered_page(api_client, *, state: str = "st-1",
                   client: dict | None = None, **overrides) -> tuple[object, str]:
    tc, _ = api_client
    params = _authorize_params(tc, state=state, client=client,
                               attempt_key="ak-1", **overrides)
    r = tc.get("/oauth/authorize", params=params)
    assert r.status_code == 200, r.text
    return r, r.text


def test_the_page_offers_and_performs_no_sign_in(api_client):
    """§9 step 4's deletion, in one place: the page authenticates NOBODY.

    What replaces it is an ASSERTION: the page asks the session origin for the
    bound token and hands it to `/oauth/consent`. If any of these markers is
    present, the second identity system is back — and with it the divergence
    between two identity sources that made the pre-#4054 design unsafe.
    """
    _r, html = _rendered_page(api_client)
    present = [marker for marker in _DELETED_FROM_THE_PAGE if marker in html]
    assert not present, (
        "the consent page still carries the deleted sign-in apparatus: "
        f"{present}. Step 4 removed it wholesale (§7's deletion set); the "
        "session is proved by a bound token minted on the session origin, never "
        "by the page signing anyone in.")


def test_the_page_has_no_third_party_script_and_its_csp_says_so(api_client):
    r, html = _rendered_page(api_client)
    assert "<script src=" not in html, html[:400]
    assert html.count("<script") == 1, "exactly one inline script is expected"
    csp = r.headers["content-security-policy"]
    assert "cdn.jsdelivr.net" not in csp
    assert "connect-src 'self' " + APP_ORIGIN in csp, (
        "the page must be allowed to reach the mint on the session origin and "
        f"nothing else: {csp}")
    assert "frame-ancestors 'none'" in csp


# ═══════════════════════════════════════════════════════════════════════════
# 3. THE ATTEMPT KEY — constraint 1, read from the page's own URL
# ═══════════════════════════════════════════════════════════════════════════

def test_the_attempt_key_param_name_matches_the_bff_authority():
    """One constant, two languages, no drift.

    `ATTEMPT_KEY_PARAM` in consent-bridge.ts is the authority (start.ts writes
    it onto the authorize URL). The page reads the name the SERVER embeds, so a
    Python-side rename is not a build error — it is every mint answering
    `403 no_authorize_attempt` after the visitor approved.
    """
    from tortoise.oauth import CONSENT_ATTEMPT_KEY_PARAM

    ts = CONSENT_BRIDGE_TS.read_text()
    match = re.search(r'export const ATTEMPT_KEY_PARAM = "([^"]+)"', ts)
    assert match, "ATTEMPT_KEY_PARAM is gone from consent-bridge.ts"
    assert match.group(1) == CONSENT_ATTEMPT_KEY_PARAM, (
        f"the page reads {CONSENT_ATTEMPT_KEY_PARAM!r} but the BFF writes "
        f"{match.group(1)!r}")
    # …and start.ts really writes it onto the authorize URL (the carriage this
    # name is for), rather than only into the return target.
    assert "ATTEMPT_KEY_PARAM" in START_TS.read_text()


def test_the_mint_url_is_the_bff_route_the_token_route_serves(api_client):
    from tortoise.oauth import CONSENT_MINT_PATH, CONSENT_START_PATH

    _r, html = _rendered_page(api_client)
    assert f'"{APP_ORIGIN}{CONSENT_MINT_PATH}"' in html, (
        "the page does not name the mint route on the session origin")
    # Both routes exist as Pages functions (a renamed route would make the
    # bounce or the mint a 404 with the flow already lost).
    functions = REPO_ROOT / "website" / "apps" / "dashboard" / "functions"
    assert (functions / (CONSENT_START_PATH.strip("/") + ".ts")).is_file(), (
        CONSENT_START_PATH)
    assert (functions / (CONSENT_MINT_PATH.strip("/") + ".ts")).is_file(), (
        CONSENT_MINT_PATH)
    # …and the mint really reads the field the page posts, answering the same
    # 403 when it is absent or foreign (the refusal this key exists for).
    token_src = TOKEN_TS.read_text()
    assert "body.attempt_key" in token_src, (
        "the mint no longer reads an `attempt_key` body field — the page posts one")
    assert "no_authorize_attempt" in token_src


# ── the node harness: the page's own logic, executed ───────────────────────

_PAGE_DRIVER = r"""
'use strict';
const fs = require('fs');
const vm = require('vm');

const pagePath = process.argv[2];
const opts = JSON.parse(process.argv[3] || '{}');
const page = fs.readFileSync(pagePath, 'utf8');

// Strict nonce form + sentinel, so the harness cannot run a script the page
// would not have run (a bare <script> would also match a template that lost
// its nonce).
const m = page.match(/<script nonce="[^"]+">([\s\S]*?)<\/script>/);
if (!m) { process.stdout.write(JSON.stringify({error: 'NO_INLINE_SCRIPT'})); process.exit(0); }

function mkEl(id) {
  const cls = new Set();
  const kids = [];
  return {
    id, style: {}, textContent: '', value: '', href: '', disabled: false,
    attrs: {}, onclick: null, addEventListener() {},
    get firstChild() { return kids.length ? kids[0] : null; },
    get childNodes() { return kids.slice(); },
    appendChild(c) { kids.push(c); return c; },
    removeChild(c) { const i = kids.indexOf(c); if (i !== -1) kids.splice(i, 1); return c; },
    classList: {
      add(c) { cls.add(c); },
      remove(c) { cls.delete(c); },
      contains(c) { return cls.has(c); },
    },
    setAttribute(k, v) { this.attrs[k] = v; },
    getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; },
  };
}

// ⛔ THE MARKUP IS THE SOURCE OF TRUTH, not a list beside it. An id the served page
// does not contain is NULL here, exactly as in a browser — so a renamed or typo'd id
// surfaces as the TypeError it is, instead of being papered over by a shim that
// invents every element it is asked for (#3524 round-3 review).
const ids = (page.match(/\bid="([^"]+)"/g) || []).map((m) => m.slice(4, -1));
const els = {};
for (const id of ids) els[id] = mkEl(id);
// `disabled` is an ATTRIBUTE in the served markup and a PROPERTY after load: the
// initial state has to come from the markup, or a page that ships without it reads
// as fail-closed here while a real browser renders an armed button.
for (const id of ids) {
  const tag = (page.match(new RegExp('<[^>]*\\bid="' + id + '"[^>]*>')) || [])[0] || '';
  if (/\bdisabled\b/.test(tag)) els[id].disabled = true;
}

const navs = [];
const fetchCalls = [];
const scripted = (opts.responses || []).slice();

const location = {
  search: opts.search || '', hash: '', pathname: '/oauth/authorize',
  origin: opts.origin || 'https://api.premiselabs.co',
};
Object.defineProperty(location, 'href', {
  get() { return location.origin + location.pathname + location.search; },
  set(v) { navs.push(String(v)); },
});

function fetchStub(url, init) {
  fetchCalls.push({
    url: String(url),
    method: (init && init.method) || 'GET',
    credentials: init && init.credentials,
    contentType: init && init.headers && init.headers['Content-Type'],
    headers: (init && init.headers) || {},
    body: init && init.body ? JSON.parse(init.body) : null,
  });
  const step = scripted.length ? scripted.shift()
                               : { status: 500, body: { error: 'scripted out' } };
  return Promise.resolve({
    ok: step.status >= 200 && step.status < 300,
    status: step.status,
    json() { return Promise.resolve(step.body); },
  });
}

const window = { location };
const document = {
  getElementById(id) { return els[id] === undefined ? null : els[id]; },
  createElement(tag) { return mkEl('<' + tag + '>'); },
};
const ctx = vm.createContext({
  window, document, fetch: fetchStub, URLSearchParams, JSON, console,
  Promise, String, Error, TypeError,
});

function snap() {
  const sel = els['org-select'];
  return {
    error: els['error'].textContent,
    errorVisible: els['error'].classList.contains('visible'),
    authDisabled: els['btn-auth'].disabled,
    retryOn: els['btn-retry'].classList.contains('on'),
    signinOn: els['btn-signin'].classList.contains('on'),
    signinHref: els['btn-signin'].attrs.href === undefined ? null
                                                          : els['btn-signin'].attrs.href,
    spinner: els['spinner'].style.display || 'none',
    orgLine: els['org-line'].textContent,
    orgLineShown: els['org-line'].style.display !== 'none',
    orgChooserOn: sel.classList.contains('on'),
    orgOptions: sel.childNodes.map((o) => ({ value: o.value, text: o.textContent })),
    resourceLine: els['resource-line'].textContent,
    scopeLine: els['scope-line'].textContent,
  };
}

const tick = () => new Promise((r) => setTimeout(r, 5));

(async () => {
  let loadError = null;
  try { vm.runInContext(m[1], ctx, { filename: 'consent-page.js' }); }
  catch (e) { loadError = String(e); }
  await tick();
  const out = { loadError, afterLoad: snap(), afterClick: null,
                afterDeny: null, afterPick: null, afterRepick: null,
                afterSecondClick: null, fetchCalls, navs };
  if (!loadError && opts.pick !== undefined && opts.pick !== null) {
    // The chooser's change handler, as the browser fires it on a user pick.
    els['org-select'].value = opts.pick;
    els['org-select'].onchange({ target: { value: opts.pick } });
    out.afterPick = snap();
  }
  if (!loadError && typeof els['btn-auth'].onclick === 'function' &&
      opts.click === true) {
    // NOT force-enabled: the PAGE arms this button (one expression, from the
    // token + the org), so a click here is the visitor clicking what is
    // actually clickable.
    els['btn-auth'].onclick();
    await tick();
    out.afterClick = snap();
  }
  if (!loadError && opts.repick !== undefined && opts.repick !== null) {
    // A SECOND pick, after the first click: the gesture that used to bypass the
    // 401 branch's disable.
    els['org-select'].value = opts.repick;
    els['org-select'].onchange({ target: { value: opts.repick } });
    out.afterRepick = snap();
  }
  if (!loadError && typeof els['btn-auth'].onclick === 'function' &&
      opts.clickTwice === true) {
    // The FIRST click, then the SECOND — exactly what a visitor does after a
    // refusal. `afterClick` is the state the refusal left behind.
    els['btn-auth'].onclick();
    await tick();
    out.afterClick = snap();
    if (!els['btn-auth'].disabled) {
      els['btn-auth'].onclick();
      await tick();
      out.afterSecondClick = snap();
    }
  }
  if (!loadError && typeof els['btn-deny'].onclick === 'function' &&
      opts.clickDeny === true) {
    els['btn-deny'].onclick();
    await tick();
    out.afterDeny = snap();
  }
  process.stdout.write(JSON.stringify(out));
})();
"""


def _run_page(html: str, opts: dict, tmp_path: Path) -> dict:
    _require_node()
    page = tmp_path / "consent-page.html"
    page.write_text(html)
    driver = tmp_path / "consent-page-driver.js"
    driver.write_text(_PAGE_DRIVER)
    proc = subprocess.run(
        ["node", str(driver), str(page), json.dumps(opts)],
        capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert not out.get("error"), out
    assert out.get("loadError") is None, (
        f"the page's inline script does not PARSE/run: {out['loadError']}")
    return out


def _page_html(api_client, *, state: str = "st-1", attempt_key: str | None = "ak-1",
               **overrides) -> tuple[str, str]:
    """``(html, raw_query)`` — the page and the query IT WAS RENDERED FROM.

    The second value is what the harness feeds to `location.search`, because that
    is the truth of the browser: the page's own URL is the request the server
    answered. A synthetic search would let the test assert a carriage the page
    never receives (the `state` it POSTs comes from the SERVER-embedded PARAMS,
    not from the URL — only the attempt key is read from the URL at runtime).
    """
    tc, _ = api_client
    params = _authorize_params(tc, state=state, **overrides)
    if attempt_key is not None:
        params["attempt_key"] = attempt_key
    r = tc.get("/oauth/authorize", params=params)
    assert r.status_code == 200, r.text
    raw = r.request.url.query
    return r.text, (raw.decode() if isinstance(raw, bytes) else raw)


def test_the_page_mints_with_the_key_from_its_own_url(api_client, tmp_path):
    """Constraint 1, executed: the key comes off the page's OWN URL and reaches
    the mint — with `state` untouched — or the flow dies after consent.

    The search string is the shape a `start` redirect emits (`?…&attempt_key=…`),
    and `state` carries a `+` and a space so a normaliser anywhere on this path
    is visible as a mismatch rather than as a silently different flow.
    """
    html, raw = _page_html(api_client, state="a+b c", attempt_key="k-9f2c")
    out = _run_page(html, {"search": "?" + raw, "responses": [
        {"status": 200, "body": {"token": "ct1.payload.sig", "exp": 1893456000}},
        _SOLE_ORG,
    ]}, tmp_path)

    calls = out["fetchCalls"]
    assert len(calls) == 2, calls
    mint = calls[0]
    assert mint["url"] == APP_ORIGIN + "/api/consent/token", mint["url"]
    assert mint["method"] == "POST"
    assert mint["contentType"] == "application/json"
    assert mint["credentials"] == "include", (
        "the mint is credentialed — without `include` the session cookie does "
        "not ride and every mint answers 401")
    assert mint["body"] == {"state": "a+b c", "attempt_key": "k-9f2c"}, (
        "the mint body must carry the attempt key from the page's URL and the "
        f"state verbatim: {mint['body']!r}")
    assert out["afterLoad"]["authDisabled"] is False, (
        "a 200 mint must arm Authorize; without it the visitor can never approve")


def test_the_page_cannot_approve_without_a_mint(api_client, tmp_path):
    """Fail-closed by construction: Authorize stays disabled unless the mint
    returned a token, so there is no path that approves an unproven identity."""
    html, raw = _page_html(api_client, attempt_key="k-forged")
    out = _run_page(html, {"search": "?" + raw, "responses": [
        {"status": 403, "body": {"error": "no_authorize_attempt"}},
    ]}, tmp_path)
    assert out["afterLoad"]["authDisabled"] is True
    assert out["afterLoad"]["errorVisible"] is True
    assert "not yours" in out["afterLoad"]["error"] or "expired" in out["afterLoad"]["error"]
    assert out["navs"] == [], "a refused mint must never navigate on its own"


def test_a_401_from_the_mint_offers_the_sign_in_leg_as_a_LINK(api_client, tmp_path):
    """The one refusal with a remedy the page cannot perform itself.

    It is a LINK, never an automatic redirect: the page must not bounce itself
    between two origins when a session that cannot mint comes back — that is the
    loop `decideConsentAdvance`'s `consent_retry` guard exists to stop on the
    server side, and the page must not be able to re-open it.
    """
    html, raw = _page_html(api_client, attempt_key="k-401")
    out = _run_page(html, {"search": "?" + raw, "responses": [
        {"status": 401, "body": {"error": "no_session"}},
    ]}, tmp_path)
    after = out["afterLoad"]
    assert after["authDisabled"] is True
    assert after["signinOn"] is True, (
        "a 401 (no session) must offer the app's sign-in leg — the FLOW's remedy")
    assert after["retryOn"] is False, (
        "retrying the same unauthenticated mint cannot succeed; offering it "
        "would be a dead end")
    assert after["signinHref"].startswith(APP_ORIGIN + "/auth?next="), after["signinHref"]
    assert "%2Fapi%2Fconsent%2Fstart" in after["signinHref"], (
        "the sign-in link must return to the flow's start route, or the "
        f"visitor signs in and lands nowhere useful: {after['signinHref']}")
    assert out["navs"] == [], "the sign-in leg must not fire by itself"


def test_a_missing_attempt_key_is_a_typed_refusal_not_a_mint_call(api_client, tmp_path):
    # The page is served (the app wrote a key), but the URL the BROWSER holds in
    # the failure case is one that lost it — a hand-typed link, a copy-paste that
    # dropped the param, a proxy that stripped the query.
    html, _raw = _page_html(api_client, attempt_key="k-present")
    out = _run_page(html, {"search": "?client_id=c&state=s", "responses": [
        {"status": 200, "body": {"token": "should-never-be-requested"}},
    ]}, tmp_path)
    assert out["fetchCalls"] == [], (
        "with no key on the page's URL there is nothing the mint could accept; "
        "calling it would only produce a 403 the visitor cannot act on")
    assert out["afterLoad"]["authDisabled"] is True
    assert out["afterLoad"]["errorVisible"] is True


def test_the_page_displays_the_scope_that_will_actually_be_BOUND(api_client, tmp_path):
    """⛔ THE CONSENT SCREEN MUST NOT UNDER-STATE WHAT IT AUTHORIZES.

    When the authorize URL omits `scope`, the AS mints the code with the CLIENT's registered
    scope (`body.get("scope") or client.get("scope") or "mcp"`) — so rendering the request's
    value (`"mcp"`) showed a NARROWER scope than the visitor was granting. Measured at
    `68a7ec727`: a client registered `scope: "mcp offline_access"` + an authorize URL with no
    `scope` rendered "Requested scopes: mcp" while the token exchange returned
    `mcp offline_access` (round 9 of #10346's review). RFC 6749 §3.3 and RFC 9700 both require
    the consent screen to show what is granted.

    `resolved_scope` is stamped server-side at render; the raw `scope` still rides along and
    is still what the page POSTS, so the request is unchanged and only the display is.
    """
    tc, _ = api_client
    params = _authorize_params(tc, client={"scope": "mcp offline_access"},
                               attempt_key="ak-scope")
    del params["scope"]                     # the case: the URL names no scope
    r = tc.get("/oauth/authorize", params=params)
    assert r.status_code == 200, r.text
    assert '"resolved_scope": "mcp offline_access"' in r.text, (
        "the page does not carry the scope that will be BOUND — a visitor reading "
        "'Requested scopes: mcp' would be approving less than the code grants")
    assert r.text.count('"resolved_scope":') == 1, (
        "the params object must carry ONE resolved_scope — the server's — not one merged in "
        "from the query (a client-supplied value would let the URL lie about the scope)")

    # …and when the request DOES name a scope, that is what is displayed (request wins).
    params2 = _authorize_params(tc, client={"scope": "mcp offline_access"},
                                attempt_key="ak-scope2", scope="mcp")
    r2 = tc.get("/oauth/authorize", params=params2)
    assert r2.status_code == 200, r2.text
    assert '"resolved_scope": "mcp"' in r2.text, (
        "the request's own scope must win over the client's registered default")

    # ⛔ AND THE SCREEN SHOWS **THE SCOPE THE CODE ACTUALLY CARRIES** — end to end, not a copy
    # of the fallback expression. Asserting the render against a literal mirrored the same
    # `scope or client scope or "mcp"` the code computes, so editing the MINT's fallback
    # (hosted_api: `scope=body.get("scope") or "mcp"`) would leave the screen saying
    # `mcp offline_access` while the token carried `mcp` — round 9's defect back, all cases
    # green (round 10, executed). So: render → approve the page's OWN body → exchange the code
    # → compare the screen with the `scope` the token carries.
    raw_q = r.request.url.query
    out = _run_page(r.text, {
        "search": "?" + (raw_q.decode() if isinstance(raw_q, bytes) else raw_q),
        "click": True,
        "responses": [
            {"status": 200, "body": {"token": "ct-scope"}},
            _SOLE_ORG,
            {"status": 200, "body": {"code": "ac_scope", "state": params["state"],
                                     "redirect_uri": REDIRECT,
                                     "redirect_target": REDIRECT}},
        ],
    }, tmp_path)
    shown = out["afterLoad"]["scopeLine"]
    assert shown == "mcp offline_access", (
        f"the consent screen shows {shown!r} — the screen must not under-state what it "
        "authorizes")

    # ⛔ AND THE GRANT AGREES WITH THE SCREEN — measured from the TOKEN, not from a copy of the
    # expression. A test that compared the render with `resolve_authorize_scope(...)` mirrored
    # the seam instead of checking the outcome: editing the MINT's arm alone
    # (`scope=body.get("scope") or "mcp"`) left every case green while the screen said
    # `mcp offline_access` and the token carried `mcp` (round 10, executed). So the real
    # approval runs here and the scope is read back off `/oauth/token`.
    _verifier, challenge = _pkce()
    token = _mint(_U1, {"client_id": params["client_id"],
                        "redirect_uri": params["redirect_uri"],
                        "code_challenge": challenge,
                        "state": params["state"]})
    r_ok = tc.post("/oauth/consent", json={
        "consent_token": token,
        "client_id": params["client_id"],
        "redirect_uri": params["redirect_uri"],
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": params["state"],
        # ⛔ EXACTLY what the page posts when the URL named no scope: the empty string.
        "scope": params.get("scope", ""),
        "resource": None,
    })
    assert r_ok.status_code == 200, r_ok.text
    granted_body = r_ok.json()
    exchanged = tc.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": granted_body["code"],
        "redirect_uri": params["redirect_uri"], "client_id": params["client_id"],
        "code_verifier": _verifier,
    })
    assert exchanged.status_code == 200, exchanged.text
    granted = exchanged.json()["scope"]
    assert shown == granted, (
        f"the consent screen shows {shown!r} while the code carries {granted!r} — the screen "
        "must show exactly what it grants")


def test_the_authorize_click_posts_the_token_and_navigates_with_the_state_echoed(
        api_client, tmp_path):
    """The bound token + the raw params to `/oauth/consent`, then the SERVER's
    navigation target with `state` echoed — byte-for-byte, from the response."""
    state = "st+1 /2"
    html, raw = _page_html(api_client, state=state, attempt_key="k-1")
    out = _run_page(html, {
        "search": "?" + raw,
        "click": True,
        "responses": [
            {"status": 200, "body": {"token": "ct1.payload.sig"}},
            _SOLE_ORG,
            {"status": 200, "body": {"code": "ac_123", "state": state,
                                     "redirect_uri": REDIRECT,
                                     "redirect_target": REDIRECT}},
        ],
    }, tmp_path)
    calls = out["fetchCalls"]
    assert len(calls) == 3, calls
    consent = calls[2]
    assert consent["url"] == "/oauth/consent", consent["url"]
    body = consent["body"]
    assert body["consent_token"] == "ct1.payload.sig", (
        "the POST must carry the bound token — the AS's only identity input")
    assert body["state"] == state, f"state was altered on the way out: {body['state']!r}"
    assert body["redirect_uri"] == REDIRECT, (
        "the raw redirect_uri is posted back for the byte-exact validation "
        "/oauth/token later performs; the page must not rewrite it")
    assert body["response_type"] == "code"
    assert body["code_challenge_method"] == "S256"
    # ⛔ AND THE TWO §2.2 BIND FIELDS THAT HAD NO ASSERTION AT ALL. Deleting
    # `code_challenge:` (or `client_id:`) from the page's consent POST left all 46 cases
    # green — the harness answers from index-scripted responses and never inspects this body
    # — while in production EVERY approval would answer `400 invalid_request` ("code_challenge
    # (PKCE, 43-128 chars) is required.") or `401 invalid_token: bind.client_id`: the flow is
    # dead immediately after the visitor approves, with the suite reporting clean (round 9 of
    # #10346's review, measured). They ride the rendered query, so `raw` supplies them.
    from urllib.parse import parse_qs

    sent_params = {k: v[0] for k, v in parse_qs(raw).items()}
    assert body["code_challenge"] == sent_params["code_challenge"], (
        "the PKCE challenge must be posted with the token — /oauth/consent binds it, and a "
        "missing one turns every approval into a 400 the visitor cannot act on")
    assert body["client_id"] == sent_params["client_id"], (
        "the client_id must be posted with the token — it is one of the four bind fields")

    assert len(out["navs"]) == 1, out["navs"]
    assert out["navs"][0].startswith(REDIRECT + "?")
    sent = {k: v[0] for k, v in parse_qs(out["navs"][0].split("?", 1)[1]).items()}
    assert sent == {"code": "ac_123", "state": state}, sent


def test_a_refused_consent_post_does_not_navigate_and_names_its_reason(
        api_client, tmp_path):
    html, raw = _page_html(api_client, attempt_key="k-400")
    out = _run_page(html, {
        "search": "?" + raw,
        "click": True,
        "responses": [
            {"status": 200, "body": {"token": "t"}},
            _SOLE_ORG,
            {"status": 400, "body": {"error": "invalid_resource",
                                     "error_description": "This account belongs to multiple teams."}},
        ],
    }, tmp_path)
    assert out["navs"] == [], (
        "a refused approval must not send the browser anywhere — the code it "
        "would have carried does not exist")
    assert "multiple teams" in out["afterClick"]["error"], out["afterClick"]
    assert out["afterClick"]["authDisabled"] is False, "a 400 is retryable by the visitor"


def test_a_sole_org_account_sees_the_named_org_and_can_approve(api_client, tmp_path):
    """The single-org branch of #1701 R1, restored on the new identity.

    The org line NAMES the team (from the AS, never from the URL), the chooser
    stays hidden, and Authorize is armed — the pre-R1 single-org contract.
    """
    html, raw = _page_html(api_client, attempt_key="k-sole")
    out = _run_page(html, {"search": "?" + raw, "responses": [
        {"status": 200, "body": {"token": "ct1.payload.sig"}},
        _SOLE_ORG,
    ]}, tmp_path)
    after = out["afterLoad"]
    assert after["orgLineShown"] is True, after
    assert "Acme Labs" in after["orgLine"] and "org-1" in after["orgLine"], after
    assert after["orgChooserOn"] is False, (
        "a sole-org account must not be shown a chooser")
    assert after["authDisabled"] is False, after
    # the resource LINE keeps showing the client-declared value, not the resolved
    # org URL (the pre-R1 byte-identical contract)
    assert after["resourceLine"] == "default (sole org)", after


def test_a_MULTI_org_account_gets_the_chooser_and_approves_with_the_PICK(
        api_client, tmp_path):
    """#1701 R1's account chooser, executed end-to-end on the new page.

    This is the branch the served harness docs describe ("pick the Organization
    you are onboarding"): more than one team ⇒ a chooser, no named org line, and
    Authorize DISABLED until a change event picks one — then the PICK, not the
    client-declared indicator, is what the approval posts.
    """
    html, raw = _page_html(api_client, attempt_key="k-multi")
    out = _run_page(html, {
        "search": "?" + raw,
        "pick": "https://api.premiselabs.co/mcp/org-2",
        "click": True,
        "responses": [
            {"status": 200, "body": {"token": "ct1.payload.sig"}},
            {"status": 200, "body": {
                "org_id": None, "org_name": None,
                "resource": "https://api.premiselabs.co/mcp",
                "memberships": [
                    {"org_id": "org-1", "org_name": "Acme Labs",
                     "resource": "https://api.premiselabs.co/mcp/org-1"},
                    {"org_id": "org-2", "org_name": "Beta Inc",
                     "resource": "https://api.premiselabs.co/mcp/org-2"},
                ]}},
            {"status": 200, "body": {"code": "ac_9", "state": "st-1",
                                     "redirect_target": REDIRECT}},
        ],
    }, tmp_path)
    after = out["afterLoad"]
    assert after["orgChooserOn"] is True, after
    assert after["orgLineShown"] is False, (
        "with several teams the named-org line is replaced by the chooser — "
        "naming one would be a lie about which team binds")
    assert after["authDisabled"] is True, (
        "the picker starts with no value, and Authorize must be inert until "
        "one is picked (no silent wrong-org bind)")
    assert [o["value"] for o in after["orgOptions"]] == [
        "", "https://api.premiselabs.co/mcp/org-1",
        "https://api.premiselabs.co/mcp/org-2"], after["orgOptions"]
    assert after["orgOptions"][0]["text"] == "Choose an org…", after["orgOptions"]

    assert out["afterPick"]["authDisabled"] is False, (
        "the change event is what arms Authorize")
    assert out["fetchCalls"][-1]["body"]["resource"] == \
        "https://api.premiselabs.co/mcp/org-2", (
            "the approval must post the PICKED resource — posting the "
            "client-declared one would bind the wrong team: "
            f"{out['fetchCalls'][-1]['body']}")
    assert len(out["navs"]) == 1, out["navs"]


def test_an_unresolvable_org_leaves_Authorize_INERT(api_client, tmp_path):
    """Fail-closed: when the preview refuses, the page names the reason, offers
    the retry, and NO path arms Authorize — approval needs a resolved team."""
    html, raw = _page_html(api_client, attempt_key="k-noorg")
    out = _run_page(html, {"search": "?" + raw, "responses": [
        {"status": 200, "body": {"token": "ct1.payload.sig"}},
        {"status": 403, "body": {"error": "invalid_grant",
                                 "error_description": "This account has no active team."}},
    ]}, tmp_path)
    after = out["afterLoad"]
    assert after["authDisabled"] is True, after
    assert "no active team" in after["error"], after
    assert after["retryOn"] is True, "a refused preview is retryable"
    assert out["navs"] == [], "a refused preview must never navigate"


def test_the_preview_carries_the_token_in_a_header_not_the_url(api_client, tmp_path):
    """Executed on the page: the assertion rides `X-Consent-Token`, and the bind
    fields — the only thing that may be logged — ride the query."""
    from tortoise.oauth import CONSENT_TOKEN_HEADER

    html, raw = _page_html(api_client, attempt_key="k-hdr")
    out = _run_page(html, {"search": "?" + raw, "responses": [
        {"status": 200, "body": {"token": "ct1.secret.sig"}},
        _SOLE_ORG,
    ]}, tmp_path)
    preview = out["fetchCalls"][1]
    assert preview["url"].startswith("/oauth/consent/preview?"), preview["url"]
    assert "ct1.secret.sig" not in preview["url"], (
        "the token must never ride a URL — it reaches logs and `Referer`")
    assert preview["headers"].get(CONSENT_TOKEN_HEADER) == "ct1.secret.sig", (
        f"the assertion must ride {CONSENT_TOKEN_HEADER}: {preview['headers']}")
    assert "code_challenge=" in preview["url"], (
        "the four bind fields must ride the query — the AS compares them")


def test_a_failed_approval_keeps_the_PICK_so_the_retry_is_not_doomed(
        api_client, tmp_path):
    """A refused approval must not desynchronise the picker from the button.

    The bug this pins (found in review): the failure path re-enabled Authorize
    AND cleared the remembered pick while the `<select>` still DISPLAYED the
    chosen team, so the next click posted `PARAMS.resource` (or nothing) — for a
    multi-team account that is a guaranteed `400 invalid_resource`, i.e. an armed
    button that can never succeed and a message that blames the client.
    """
    picked = "https://api.premiselabs.co/mcp/org-2"
    html, raw = _page_html(api_client, attempt_key="k-retry")
    out = _run_page(html, {
        "search": "?" + raw,
        "pick": picked,
        "clickTwice": True,
        "responses": [
            {"status": 200, "body": {"token": "ct1.payload.sig"}},
            {"status": 200, "body": {
                "org_id": None, "org_name": None,
                "resource": "https://api.premiselabs.co/mcp",
                "memberships": [
                    {"org_id": "org-1", "org_name": "Acme Labs",
                     "resource": "https://api.premiselabs.co/mcp/org-1"},
                    {"org_id": "org-2", "org_name": "Beta Inc",
                     "resource": picked},
                ]}},
            {"status": 500, "body": {"error": "temporarily_unavailable"}},
            {"status": 500, "body": {"error": "temporarily_unavailable"}},
        ],
    }, tmp_path)
    assert out["afterClick"]["authDisabled"] is False, (
        "a 5xx is retryable by the visitor")
    approvals = [c for c in out["fetchCalls"]
                 if c["url"] == "/oauth/consent"]
    assert len(approvals) == 2, approvals
    assert approvals[1]["body"]["resource"] == picked, (
        "the retry must post the team the chooser still DISPLAYS; posting the "
        f"client-declared value instead is refused by _default_org: {approvals[1]['body']}")
    assert out["afterSecondClick"]["orgChooserOn"] is True, (
        "the picker must still be on screen with its value")


def test_a_spent_token_does_not_leave_Authorize_ARMED(api_client, tmp_path):
    """A 401 from `/oauth/consent` means the `jti` is spent, so the remedy is a
    RE-MINT. Leaving Authorize enabled would re-post a token the AS already
    recorded — an armed button whose only outcome is another 401."""
    html, raw = _page_html(api_client, attempt_key="k-401c")
    out = _run_page(html, {
        "search": "?" + raw,
        "click": True,
        "responses": [
            {"status": 200, "body": {"token": "ct1.payload.sig"}},
            _SOLE_ORG,
            {"status": 401, "body": {"error": "invalid_token",
                                     "error_description": "already redeemed"}},
        ],
    }, tmp_path)
    after = out["afterClick"]
    assert after["authDisabled"] is True, (
        "the armed button would re-post a spent assertion")
    assert after["retryOn"] is True, "the remedy is a re-mint, offered as a control"
    assert "expired" in after["error"], after
    assert out["navs"] == [], out["navs"]


def test_the_page_is_not_RENDERED_when_the_AS_cannot_verify_what_it_mints(
        api_client, monkeypatch):
    """A page whose every approval is doomed is not the right answer.

    Without a usable `CONSENT_TOKEN_SECRET` the mint still answers 200 (the app
    signs with its copy) and every redemption answers `401 invalid_token`, so the
    rendered page would offer an Authorize that can never succeed and a Retry that
    cannot help. The AS already refuses an unusable `CONSENT_TOKEN_ISSUER`/origin
    this way (`test_an_UNPARSEABLE_session_origin_is_a_typed_503_not_a_crash`, which
    executes that claim instead of asserting it); the key gets the same treatment,
    at RENDER.
    """
    tc, _ = api_client
    params = _authorize_params(tc, attempt_key="ak-1")
    monkeypatch.delenv("CONSENT_TOKEN_SECRET", raising=False)
    r = tc.get("/oauth/authorize", params=params)
    assert r.status_code == 503, r.text
    assert r.json()["error"] == "temporarily_unavailable", r.text
    assert "CONSENT_TOKEN_SECRET" in r.json()["error_description"], r.text
    # …and the BOUNCE is unaffected: it needs neither the key nor the mint, so an
    # unauthenticated visitor is still handed to the app's sign-in leg.
    bounced = tc.get("/oauth/authorize",
                     params=_authorize_params(tc), follow_redirects=False)
    assert bounced.status_code == 302, bounced.text
    assert bounced.headers["location"].startswith(APP_ORIGIN + "/auth?next="), (
        bounced.headers["location"])


@pytest.mark.parametrize("issuer", [
    # (1) absolute-but-not-a-serviceable-origin — the DOCUMENTED `None` branch.
    "app.premiselabs.co",
    "ftp://x.y",
    # (2) THE CRASH ARM: `urlsplit` RAISES `ValueError` on a bracketed netloc that is
    # not terminated, so these never reach the `scheme`/`netloc` test at all.
    "https://[",
    "http://[::1",
])
def test_an_UNPARSEABLE_session_origin_is_a_typed_503_not_a_crash(
        api_client, monkeypatch, issuer):
    """`consent_app_origin()` is the ONLY reader of the binding, and it must be TOTAL.

    ⛔ `urlsplit` does not merely return a useless object for every bad binding: it
    RAISES for an unterminated `[` netloc. Before the fix that escaped
    `GET /oauth/authorize` as a `ValueError` and was answered by the app-level
    handler — the bare 500 `{"detail": …}` this endpoint's own `except Exception`
    net three lines above exists to prevent (#3026) — instead of the typed 503 the
    function's docstring promises and the handler implements for `None`.

    Both TS halves of the same knob (`token.ts::originVerdict`,
    `start.ts::parseAsOrigin`) already parse inside a `try`; this pins the third.
    Nothing here is reachable from REQUEST input — the binding is operator config —
    which is why it is one assertion per shape rather than a fuzz arm.
    """
    tc, _ = api_client
    params = _authorize_params(tc, attempt_key="ak-1")
    monkeypatch.setenv("CONSENT_TOKEN_ISSUER", issuer)
    r = tc.get("/oauth/authorize", params=params, follow_redirects=False)
    assert r.status_code == 503, (
        f"{issuer!r} must be the SAME typed refusal as any other unusable binding, "
        f"got {r.status_code}: {r.text}")
    body = r.json()
    assert body["error"] == "temporarily_unavailable", body
    assert "session origin is misconfigured" in body["error_description"], body
    # …and it is not a 302 either: a bounce to an unparseable origin is the other
    # wrong answer this branch exists to refuse.
    assert "location" not in {k.lower() for k in r.headers}, dict(r.headers)


@pytest.mark.parametrize("issuer, expected_origin", [
    ("https://app.premiselabs.co", APP_ORIGIN),          # the plain binding
    ("https://app.premiselabs.co/consent", APP_ORIGIN),  # a path is stripped
    ("http://localhost:3000/deep/path", "http://localhost:3000"),  # local preview
    ("https://[::1]:8443", "https://[::1]:8443"),   # a WELL-FORMED bracketed netloc
])
def test_a_SERVICEABLE_session_origin_is_returned_as_a_bare_origin(
        api_client, monkeypatch, issuer, expected_origin):
    """The other direction, so the `try` above cannot swallow a GOOD binding.

    A parametrised refusal test that never asserts an acceptance can be satisfied
    by a function that always returns `None` — and then every real deployment is a
    503. This pins the value the total parse still has to produce.
    """
    tc, _ = api_client
    params = _authorize_params(tc, attempt_key="ak-1")
    monkeypatch.setenv("CONSENT_TOKEN_ISSUER", issuer)
    r = tc.get("/oauth/authorize", params=params, follow_redirects=False)
    assert r.status_code == 200, r.text
    assert f'"{expected_origin}/api/consent/token"' in r.text, (
        "the page's mint URL must be the ORIGIN, with no path from the binding")


def test_the_deny_path_returns_an_error_to_the_SERVER_built_target(api_client, tmp_path):
    html, raw = _page_html(api_client, state="st-d", attempt_key="k-d")
    out = _run_page(html, {
        "search": "?" + raw, "clickDeny": True,
        "responses": [{"status": 500, "body": {}}],
    }, tmp_path)
    assert len(out["navs"]) == 1, out["navs"]
    from urllib.parse import parse_qs

    sent = {k: v[0] for k, v in parse_qs(out["navs"][0].split("?", 1)[1]).items()}
    assert sent == {"error": "access_denied", "state": "st-d"}, sent


# ═══════════════════════════════════════════════════════════════════════════
# 3b. The org preview's SECOND identity arm (§9 step 4 keeps the picker)
# ═══════════════════════════════════════════════════════════════════════════
# The chooser is a property of the PAGE (#1701 R1), and it survives this work —
# but its data source had to move with the page's identity. It used to read
# `GET /oauth/consent/preview` with the app's session JWT, which this page no
# longer holds. The route now also accepts the BOUND CONSENT TOKEN in
# `X-Consent-Token`, comparing the same four bind fields it would compare at
# redemption. These tests pin the arm, its fail-closed directions, and — the
# property the whole design rests on — that a preview does NOT spend the `jti`.

def _consent_token_for(params: dict, user_id: str = _U1) -> str:
    from tortoise.consent_token import mint_consent_token

    return mint_consent_token(
        user_id=user_id, email="u@example.com",
        bind={"client_id": params["client_id"],
              "redirect_uri": params["redirect_uri"],
              "code_challenge": params["code_challenge"],
              "state": params.get("state")})


def _preview_bind(params: dict) -> dict:
    return {"client_id": params["client_id"],
            "redirect_uri": params["redirect_uri"],
            "code_challenge": params["code_challenge"],
            "state": params.get("state"),
            "resource": params.get("resource") or ""}


def test_the_preview_accepts_the_bound_consent_token_in_a_HEADER(api_client):
    """The chooser's data source, as the page reaches it: no bearer anywhere.

    Header, not query string — the token is a bearer-equivalent assertion and a
    query string is copied into access logs, proxy logs and the `Referer` of the
    next navigation. `verify_session_jwt` is deliberately NOT stubbed here: the
    request proves the token arm does not need it at all.
    """
    from tortoise.oauth import CONSENT_TOKEN_HEADER

    tc, _cp = api_client
    params = _authorize_params(tc)
    token = _consent_token_for(params)
    r = tc.get("/oauth/consent/preview", params=_preview_bind(params),
               headers={CONSENT_TOKEN_HEADER: token})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["org_id"] == "team-free-001", body
    assert body["org_name"], body
    assert token not in str(r.request.url), (
        "the assertion must never ride the query string")


@pytest.mark.parametrize("field", ["client_id", "redirect_uri",
                                   "code_challenge", "state"])
def test_a_preview_token_from_ANOTHER_flow_is_refused(api_client, field):
    """§2.2 at the preview: EVERY bind field is COMPARED, not merely present.

    Parametrised one field at a time on purpose: a single fixture that differs in all four
    at once cannot ATTRIBUTE the 401, because `_bind_mismatch` reports only the first. One
    mutated field per case is what makes `bind.<field>` a meaningful assertion, and it is
    what proves each comparison runs.

    ⛔ The mutation this catches is the COMPARISON being dropped or short-circuited, so a
    WRONG presented value is accepted. It is NOT "deleting a key from `presented`": that
    would make `_bind_mismatch` report the missing field and red the POSITIVE test
    (`test_the_preview_accepts_the_bound_consent_token_in_a_HEADER`), which is a different
    failure surfaced by a different test. Stated precisely, because an overstated claim of
    discriminating power is the kind of sentence that survives review while being false
    (round 8 of #10346's review measured the old wording).
    """
    from tortoise.oauth import CONSENT_TOKEN_HEADER

    tc, _cp = api_client
    # ONE client, two registered redirect URIs, so `redirect_uri` can be mutated
    # without also changing the client (which would confound the field under test).
    second = "http://127.0.0.1:8765/other-callback"
    reg = _register_client(tc, redirect_uris=[REDIRECT, second])
    _verifier, challenge = _pkce()
    _v2, challenge2 = _pkce()
    mine = {"client_id": reg["client_id"], "redirect_uri": REDIRECT,
            "code_challenge": challenge, "state": "st-mine"}
    token = _consent_token_for(mine)
    # each field's "somebody else's" value — every one of them REACHABLE (a real
    # second client, a real second registered URI), so a refusal can only be the
    # bind comparison and never a shape error
    elsewhere = {
        "client_id": _register_client(tc)["client_id"],
        "redirect_uri": second,
        "code_challenge": challenge2,
        "state": "st-somebody-elses",
    }
    presented = {"client_id": mine["client_id"],
                 "redirect_uri": mine["redirect_uri"],
                 "code_challenge": mine["code_challenge"],
                 "state": mine["state"], "resource": ""}
    presented[field] = elsewhere[field]       # EXACTLY one field differs
    r = tc.get("/oauth/consent/preview", params=presented,
               headers={CONSENT_TOKEN_HEADER: token})
    assert r.status_code == 401, r.text
    assert r.json()["error"] == "invalid_token", r.text
    assert f"bind.{field}" in r.json()["error_description"], (
        f"the refusal must name the field that mismatched, or a future reader "
        f"cannot tell which comparison fired: {r.json()}")


def test_a_preview_does_NOT_spend_the_jti_so_the_visitor_can_still_approve(
        api_client):
    """The preview runs BEFORE the decision, so it must not consume the
    assertion — and the same token must still redeem afterwards.

    This is the property that makes the picker safe to keep: a page load cannot
    burn the credential the approval needs.
    """
    from tortoise.oauth import CONSENT_TOKEN_HEADER

    tc, _cp = api_client
    params = _authorize_params(tc)
    token = _consent_token_for(params)
    for _attempt in range(2):           # a reload, then a reload again
        r = tc.get("/oauth/consent/preview", params=_preview_bind(params),
                   headers={CONSENT_TOKEN_HEADER: token})
        assert r.status_code == 200, r.text
    redeem = tc.post("/oauth/consent", json={
        "consent_token": token,
        "client_id": params["client_id"],
        "redirect_uri": params["redirect_uri"],
        "response_type": "code",
        "code_challenge": params["code_challenge"],
        "code_challenge_method": "S256",
        "state": params["state"],
        "resource": None,
    })
    assert redeem.status_code == 200, redeem.text
    assert redeem.json()["code"], redeem.text


def test_the_bearer_arm_of_the_preview_is_unchanged(api_client, session_user):
    """No token header ⇒ the pre-existing path, byte for byte: the session JWT
    still resolves the preview, so a non-bridged caller is unaffected."""
    tc, _cp = api_client
    session_user()
    params = _authorize_params(tc)
    r = tc.get("/oauth/consent/preview",
               params={"resource": params["resource"] or ""})
    assert r.status_code == 200, r.text
    assert r.json()["org_id"] == "team-free-001", r.text


def test_BOTH_arms_of_the_preview_ship_Cache_Control_no_store(api_client, session_user):
    """An identity-bearing GET must not be storable by a SHARED cache.

    ⛔ The carrier change removed a FREE protection. The old page authenticated with
    `Authorization: Bearer …`, and RFC 9111 §3.5 forbids a shared cache from reusing the response
    to a request carrying that header. §9 step 4 moved the page to `X-Consent-Token` in a HEADER
    — right for logs and `Referer` — but no cache rule covers a custom header, this 200 is
    heuristically cacheable, and the cache key is the URL, which does not contain the token. So
    an intermediary could replay one visitor's team list to another.

    Pinned on BOTH arms: the token arm is the one the carrier change created, and the bearer arm
    is pinned so the guarantee stops depending on a cache honouring a rule about a header the page
    no longer sends.
    """
    from tortoise.oauth import CONSENT_TOKEN_HEADER

    tc, _cp = api_client
    params = _authorize_params(tc)

    token_arm = tc.get("/oauth/consent/preview", params=_preview_bind(params),
                       headers={CONSENT_TOKEN_HEADER: _consent_token_for(params)})
    assert token_arm.status_code == 200, token_arm.text
    assert token_arm.headers.get("cache-control") == "no-store", (
        f"the token arm is cacheable: {dict(token_arm.headers)!r}")

    session_user()
    bearer_arm = tc.get("/oauth/consent/preview",
                        params={"resource": params["resource"] or ""})
    assert bearer_arm.status_code == 200, bearer_arm.text
    assert bearer_arm.headers.get("cache-control") == "no-store", (
        f"the bearer arm is cacheable: {dict(bearer_arm.headers)!r}")


# ═══════════════════════════════════════════════════════════════════════════
# 3c. #3443: the page navigates the SERVER-built target, never the raw string
# ═══════════════════════════════════════════════════════════════════════════
# The wholesale rewrite replaced the page that carried these assertions, so they
# are rebuilt here — armed, not merely present. A page navigating `redirect_uri`
# instead of `redirect_target` delivers the code to a URL the CLIENT asked for
# rather than the one that was REGISTERED, which is the open-redirect class
# #3443 exists to remove.

_RAW_REDIRECT = "https://CLIENT.example/cb"
_BUILT_TARGET = "https://client.example/cb"


def _render_page(*, redirect_uri: str = _RAW_REDIRECT,
                 redirect_target: str | None = _BUILT_TARGET) -> str:
    """The page as a PURE function of `params`, so the two redirect values can be
    made to DIFFER — passing one string in both places cannot distinguish "read
    the server's target" from "read the request's value".

    Only this test renders directly: every other page test goes through
    `/oauth/authorize`, where the handler decides both values.
    """
    from tortoise.oauth import consent_page_html

    params = {
        "client_id": "c-3443", "redirect_uri": redirect_uri,
        "response_type": "code", "code_challenge": "ch-3443",
        "code_challenge_method": "S256", "state": "st-1", "scope": "mcp",
        "resource": None, "client_name": "c-3443",
    }
    if redirect_target is not None:
        params["redirect_target"] = redirect_target
    html = consent_page_html(client_name="c-3443", params=params,
                             raw_query="client_id=c-3443&attempt_key=ak-3443&state=st-1",
                             app_origin=APP_ORIGIN)
    return html[0] if isinstance(html, tuple) else html


def test_3443_the_page_navigates_the_SERVER_built_target_not_the_raw_request(
        api_client, tmp_path):
    """#3443, all four arms plus the control that makes them discriminating.

    (a) deny — no server round trip, so the page-scoped `redirect_target` is the
        ONLY source; (b) a successful approval PREFERS the response's target
        (which is made to differ, or the assertion could not tell); (c) version
        skew — an API that omits the field falls back to the page-scoped target,
        never to the raw string; (d) fail-closed — no target at all does NOT
        navigate (an empty dest would resolve as a RELATIVE url and deliver the
        code to this page's own origin).
    """
    page = _render_page()

    # (a) deny
    deny = _run_page(page, {"search": "?client_id=c-3443&attempt_key=ak-3443",
                            "clickDeny": True,
                            "responses": [{"status": 500, "body": {}}]}, tmp_path)
    assert deny["navs"] == [_BUILT_TARGET + "?error=access_denied&state=st-1"], deny

    # (b) the response's target WINS over the page-scoped one
    minted_target = "https://client.example/cb-from-the-mint"
    minted = _run_page(page, {
        "search": "?client_id=c-3443&attempt_key=ak-3443",
        "click": True,
        "responses": [
            {"status": 200, "body": {"token": "t"}},
            _SOLE_ORG,
            {"status": 200, "body": {"code": "code-1", "state": "st-1",
                                     "redirect_target": minted_target}},
        ]}, tmp_path)
    assert minted["navs"] == [minted_target + "?code=code-1&state=st-1"], minted[
        "navs"]

    # (c) version skew: the field is absent from the response
    skew = _run_page(page, {
        "search": "?client_id=c-3443&attempt_key=ak-3443",
        "click": True,
        "responses": [
            {"status": 200, "body": {"token": "t"}},
            _SOLE_ORG,
            {"status": 200, "body": {"code": "code-1", "state": "st-1"}},
        ]}, tmp_path)
    assert skew["navs"] == [_BUILT_TARGET + "?code=code-1&state=st-1"], skew["navs"]

    for arm, out in (("deny", deny), ("mint", minted), ("skew", skew)):
        assert all("CLIENT.example" not in n for n in out["navs"]), (
            f"the {arm} arm navigated the RAW request string: {out['navs']}")

    # (d) fail-closed: no server-built target, no navigation
    blank = _run_page(_render_page(redirect_target=None), {
        "search": "?client_id=c-3443&attempt_key=ak-3443", "clickDeny": True,
        "responses": [{"status": 500, "body": {}}]}, tmp_path)
    assert blank["navs"] == [], (
        "navigated with no server-built target — an empty dest resolves as a "
        "RELATIVE url and would hand the code to this page's own origin")
    assert blank["afterDeny"]["errorVisible"] is True, blank["afterDeny"]
    assert blank["afterDeny"]["error"].strip(), blank["afterDeny"]

    # ── the control that makes the three assertions above discriminating ──
    guard_off = page.replace("const dest = target || PARAMS.redirect_target;",
                            "const dest = target || PARAMS.redirect_uri;")
    assert guard_off != page, "the guard's source line moved — update this control"
    ctl = _run_page(guard_off, {"search": "?client_id=c-3443&attempt_key=ak-3443",
                                "clickDeny": True,
                                "responses": [{"status": 500, "body": {}}]}, tmp_path)
    assert any("CLIENT.example" in n for n in ctl["navs"]), (
        "the guard-removed control did NOT navigate the raw string, so the "
        f"assertions above are vacuous: {ctl['navs']}")


def test_a_401_then_a_NEW_PICK_cannot_rearm_a_spent_token(api_client, tmp_path):
    """The picker's onchange must not defeat the 401 branch.

    A spent `jti` disables Authorize; the chooser is still on screen, so a new
    pick runs `onchange` — which must NOT re-arm a button holding the redeemed
    token. Authorize is armed from ONE expression (`consentToken && orgReady`),
    and this is the gesture that used to bypass it.
    """
    html, raw = _page_html(api_client, attempt_key="k-401pick")
    out = _run_page(html, {
        "search": "?" + raw,
        "pick": "https://api.premiselabs.co/mcp/org-1",
        "click": True,
        "repick": "https://api.premiselabs.co/mcp/org-2",
        "responses": [
            {"status": 200, "body": {"token": "ct1.spent.sig"}},
            {"status": 200, "body": {
                "org_id": None, "org_name": None,
                "resource": "https://api.premiselabs.co/mcp",
                "memberships": [
                    {"org_id": "org-1", "org_name": "Acme Labs",
                     "resource": "https://api.premiselabs.co/mcp/org-1"},
                    {"org_id": "org-2", "org_name": "Beta Inc",
                     "resource": "https://api.premiselabs.co/mcp/org-2"},
                ]}},
            {"status": 401, "body": {"error": "invalid_token",
                                     "error_description": "already redeemed"}},
        ],
    }, tmp_path)
    assert out["afterClick"]["authDisabled"] is True, (
        "the spent token must leave the button disabled")
    assert out["afterRepick"]["authDisabled"] is True, (
        "picking another team re-armed a button whose token is already "
        "redeemed — the click posts a spent jti and can only earn another 401")
    assert out["navs"] == [], out["navs"]


def test_a_mint_4xx_offers_no_retry_that_cannot_help(api_client, tmp_path):
    """The mint's 4xx is the REQUEST's fault, so a Retry would re-send the same body forever.

    ⛔ NOT because a state-less render is reachable: `state` IS required at authorize
    (`require_state=True` refuses an empty/missing one before the page renders), so this
    branch is defence-in-depth for the 4xx the mint CAN answer — a refused `attempt_key`
    (`403 no_authorize_attempt`) or a body it will not take (`400 invalid_request`). The
    rewrite comment in `tortoise/oauth.py` says the same; keeping them in step matters,
    because "stateless renders are supported" is how a future edit drops `require_state`.
    The payload is the REAL shape: `token.ts` answers `{error, message}`.
    """
    html, raw = _page_html(api_client, attempt_key="k-400")
    out = _run_page(html, {"search": "?" + raw, "responses": [
        {"status": 400, "body": {"error": "invalid_request",
                                 "message": "state is required."}},
    ]}, tmp_path)
    after = out["afterLoad"]
    assert after["authDisabled"] is True, after
    assert after["errorVisible"] is True, after
    assert "state is required" in after["error"], after
    assert after["retryOn"] is False, (
        "a 400 from the mint cannot be fixed by retrying the same request")

    # …while a 5xx still offers it: the failure above must not have removed the
    # remedy for a transient one.
    html5, raw5 = _page_html(api_client, attempt_key="k-500")
    out5 = _run_page(html5, {"search": "?" + raw5, "responses": [
        {"status": 503, "body": {"error": "temporarily_unavailable"}},
    ]}, tmp_path)
    assert out5["afterLoad"]["retryOn"] is True, out5["afterLoad"]


# ═══════════════════════════════════════════════════════════════════════════
# 4. #6001 survives the rewrite: single-pass substitution
# ═══════════════════════════════════════════════════════════════════════════

_PLACEHOLDERS = [
    "__PARAMS__", "__MINT_URL__", "__CONSENT_URL__",
    "__SIGNIN_URL__", "__ATTEMPT_KEY_PARAM__", "__NONCE__",
    "__PREVIEW_URL__", "__CONSENT_TOKEN_HEADER__",
]

#: A resolved preview for a SOLE-org account — the shape the page renders as a
#: named org with Authorize armed. Scripted after every successful mint because
#: the org line is resolved from the AS, not from anything the page names.
_SOLE_ORG = {"status": 200, "body": {
    "org_id": "org-1", "org_name": "Acme Labs",
    "resource": "https://api.premiselabs.co/mcp/org-1",
}}


def test_the_page_declares_exactly_the_placeholders_the_renderer_fills():
    """A placeholder added to the template but not to the mapping is served
    VERBATIM (the regex only visits declared keys), so the page ships with a
    literal `__FOO__` where a value should be — the inert-page class #6001 is
    one instance of. Asserted in both directions."""
    from tortoise.oauth import _CONSENT_HTML, consent_page_html

    declared = set(re.findall(r"__[A-Z][A-Z0-9_]*__", _CONSENT_HTML))
    assert declared == set(_PLACEHOLDERS), (
        f"template placeholders {sorted(declared)} != renderer's "
        f"{sorted(_PLACEHOLDERS)}")
    html, nonce = consent_page_html(
        client_name="c", params={"state": "s"}, raw_query="state=s",
        app_origin=APP_ORIGIN)
    assert nonce in html
    leftover = set(re.findall(r"__[A-Z][A-Z0-9_]*__", html))
    assert not leftover, f"unsubstituted placeholders served to the browser: {leftover}"


@pytest.mark.parametrize("token", _PLACEHOLDERS)
def test_a_param_carrying_a_placeholder_token_is_inserted_verbatim(token, api_client):
    """#6001's defect class, re-pinned for the new placeholder set: substitution
    is a SINGLE pass, so a value containing a token is inserted rather than
    re-expanded (which broke the JSON literal and left an inert page)."""
    _r, html = _rendered_page(api_client, state=token, client={"client_name": token})
    assert html.count('nonce="') == 1
    payload = html.split("const PARAMS = ", 1)[1].split(";\n", 1)[0]
    parsed = json.loads(payload.encode().decode("unicode_escape"))
    assert parsed["state"] == token, (
        f"the token in `state` was re-expanded ({parsed['state']!r}) instead of "
        "being inserted verbatim")
    assert parsed["client_name"] == token, parsed["client_name"]


# ═══════════════════════════════════════════════════════════════════════════
# 5. The authenticated, TOKEN-BOUND POST — the code, the client's redirect_uri
# ═══════════════════════════════════════════════════════════════════════════

_TEST_SECRET = "step4-test-secret-0123456789abcdef0123456789abcdef"


def _mint(user_id: str, bind: dict) -> str:
    from tortoise.consent_token import mint_consent_token

    return mint_consent_token(user_id=user_id, email=None, bind=bind)


def test_a_bound_consent_token_issues_the_code_to_the_clients_redirect_uri(
        api_client):
    """The POST the page actually makes: `consent_token` + the presented params.

    The code goes to the CLIENT's `redirect_uri` (the registered one, matched
    through the AS's own predicate), `state` comes back byte-identical, and the
    code it returns is redeemable at `/oauth/token` with the bound verifier —
    proving it was minted from the BOUND params and not from the page's choices.
    """
    tc, _ = api_client
    verifier, challenge = _pkce()
    params = _authorize_params(tc)          # registers the client
    registered = params["redirect_uri"]
    state = "st+bound /ok"
    token = _mint(_U1, {"client_id": params["client_id"],
                        "redirect_uri": registered,
                        "code_challenge": challenge,
                        "state": state})

    r = tc.post("/oauth/consent", json={
        "consent_token": token,
        "client_id": params["client_id"],
        "redirect_uri": registered,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": "mcp",
        "resource": None,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["code"], body
    assert body["state"] == state, (
        f"`state` must be echoed byte-for-byte, got {body['state']!r}")
    assert body["redirect_uri"] == registered, (
        "the code must be issued for the CLIENT's registered redirect_uri")
    assert body["redirect_target"] == registered

    exchanged = tc.post("/oauth/token", data={
        "grant_type": "authorization_code", "code": body["code"],
        "redirect_uri": registered, "client_id": params["client_id"],
        "code_verifier": verifier,
    })
    assert exchanged.status_code == 200, exchanged.text
    assert exchanged.json()["access_token"]


def test_the_token_arm_and_the_bearer_arm_are_BOTH_still_live(
        api_client, session_user):
    """Step 4 deletes the PAGE's bearer use, not the AS's bearer path.

    A deployment carrying step 4 against an old page (or any existing
    integration) must still complete — the two source split is what step 2
    made additive, and step 4 must not quietly retire one of them.
    """
    tc, _ = api_client
    session_user(_U1)
    _verifier, challenge = _pkce()
    params = _authorize_params(tc)
    r = tc.post("/oauth/consent", json={
        "client_id": params["client_id"], "redirect_uri": params["redirect_uri"],
        "response_type": "code", "code_challenge": challenge,
        "code_challenge_method": "S256", "state": "st-bearer", "scope": "mcp",
    }, headers={"Authorization": "Bearer fake-session-jwt"})
    assert r.status_code == 200, r.text
    assert r.json()["code"], r.text


# ═══════════════════════════════════════════════════════════════════════════
# the coverage evidence this PR publishes about THIS FILE is derived, not typed
# ═══════════════════════════════════════════════════════════════════════════

#: Where this suite's own size is asserted to the outside world. Two homes, both
#: of which had gone stale TWICE on this PR (review rounds 2, 5, 9, 11): the
#: `durations` weight's "what it covers" note (`config/ci-surfaces.yml`) and
#: §9 step 4's evidence cell (`docs/engineering/auth/consent-identity-bridge.md`).
#: A number a lane types is a number that rots on the next test; these are
#: DERIVED from the file below and compared, so a new case cannot land without
#: the evidence following it.
CI_SURFACES = REPO_ROOT / "config" / "ci-surfaces.yml"
CONSENT_BRIDGE_DOC = (
    REPO_ROOT / "docs" / "engineering" / "auth" / "consent-identity-bridge.md"
)


def _module_level_literal_lengths(tree) -> dict[str, int]:
    """`NAME = [ … ]` / `NAME = ( … )` at module scope → its LENGTH.

    Needed because one parametrisation passes a module-level list by NAME
    (`@pytest.mark.parametrize("token", _PLACEHOLDERS)`), and resolving it is the
    difference between counting 8 cases and quietly counting 1.
    """
    import ast

    lengths: dict[str, int] = {}
    for node in tree.body:
        targets, value = (), None
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = (node.target,), node.value
        if not isinstance(value, (ast.List, ast.Tuple)):
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                lengths[target.id] = len(value.elts)
    return lengths


def _coverage_arithmetic(path: Path) -> tuple[int, int]:
    """(collected cases, test functions) for a pytest module — FROM ITS SOURCE.

    `collected = Σ over test functions of Π over that function's literal
    `parametrize` lists`. Deliberately NOT `pytest --collect-only` inside a test:
    a nested collection re-imports this module in a second interpreter and its
    own conftest, which is both slow and a different environment than the one
    that governs the doc claim.

    ⛔ Every `parametrize` argument set must be a LITERAL list/tuple (or a
    module-level name bound to one). A computed set would make this arithmetic
    silently undercount — the exact failure mode ("stale evidence ships green")
    that this gate exists to end — so an unresolvable one is a LOUD failure here,
    not a guess.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    named = _module_level_literal_lengths(tree)
    cases = 0
    functions = 0
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        functions += 1
        multiplier = 1
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or len(decorator.args) < 2:
                continue
            if not ast.unparse(decorator.func).endswith("parametrize"):
                continue
            values = decorator.args[1]
            if isinstance(values, ast.Name):
                assert values.id in named, (
                    f"{path.name}: parametrize in {node.name} passes {values.id!r}, which is not "
                    "a module-level list/tuple literal — this counter cannot derive the case "
                    "count, so the coverage evidence cannot be checked")
                multiplier *= named[values.id]
            elif isinstance(values, (ast.List, ast.Tuple)):
                multiplier *= len(values.elts)
            else:
                raise AssertionError(
                    f"{path.name}: parametrize in {node.name} passes "
                    f"{ast.unparse(values)!r}; the case count is only derivable from a literal "
                    "or a module-level literal, and a silent undercount is how this evidence "
                    "went stale")
        cases += multiplier
    return cases, functions


def test_the_coverage_evidence_matches_the_FILE_it_counts():
    """⛔ Both published counts are DERIVED here, so neither can silently rot again.

    Round 11 measured `config/ci-surfaces.yml` claiming 55 cases and §9 step 4
    claiming 55 cases / 39 test functions, against a file that collects 56 and
    defines 40 — after rounds 2, 5 and 9 had each reported the same number stale.
    The numbers are this PR's own evidence that "the page surface is covered", so
    a lane that adds a test and forgets them is publishing a false coverage
    claim, not making a bookkeeping slip.

    Non-vacuity: the assertions below compare against numbers computed from this
    module's AST, so restoring `55 cases` (or `39 test functions`) anywhere reds
    — and each home must still BE FOUND, so deleting the claim is not a way to
    pass (a missing row would leave nothing to compare and this would be a gate
    that scans nothing).
    """
    cases, functions = _coverage_arithmetic(Path(__file__))

    # sanity: the derivation inspected a real suite, not an empty module
    assert functions >= 30 and cases >= functions, (
        f"derived {cases} cases / {functions} test functions from {Path(__file__).name} — "
        "that is not this suite, so the comparison below would prove nothing")

    surfaces = CI_SURFACES.read_text(encoding="utf-8")
    row = re.search(
        r"^  test_oauth_consent_page_step4\.py: .*?What it covers: (\d+) cases",
        surfaces, re.MULTILINE | re.DOTALL)
    assert row, (
        "config/ci-surfaces.yml has no `durations` row for this file carrying "
        "`What it covers: N cases` — the weight's justification is the only place the "
        "CI map states this suite's size, and it is gone")
    assert int(row.group(1)) == cases, (
        f"config/ci-surfaces.yml says this suite covers {row.group(1)} cases; the file "
        f"collects {cases} ({functions} test functions). Update the row, not this test.")

    doc = CONSENT_BRIDGE_DOC.read_text(encoding="utf-8")
    claimed = re.search(
        r"`tests/test_oauth_consent_page_step4\.py`\s*\((\d+) cases\s*/\s*(\d+) test functions",
        doc)
    assert claimed, (
        "docs/engineering/auth/consent-identity-bridge.md §9 step 4 no longer states "
        "`tests/test_oauth_consent_page_step4.py` (N cases / M test functions) — the "
        "evidence cell for the delivered page rewrite must carry its own numbers")
    assert (int(claimed.group(1)), int(claimed.group(2))) == (cases, functions), (
        f"the bridge record claims {claimed.group(1)} cases / {claimed.group(2)} test "
        f"functions; the file holds {cases} / {functions}. Update the record, not this test.")


def test_the_state_requirement_is_MARKED_as_a_departure_where_a_reader_adopts():
    """`require_state=True` departs from a SPEC DEFAULT, so the departure is
    MARKED where a reader ADOPTS from — not only where it is implemented.

    RFC 6749 §4.1.1 makes `state` RECOMMENDED, so a state-less authorize request
    is admissible, and this AS's own `bind` still admits `state: null` at
    redemption. The page-render path refuses it anyway because the app's
    `start.ts`/`token.ts` use `state` as the ATTEMPT's identity, and a bounce
    would hand the visitor a bare JSON 400. A later lane holding the RFC is
    exactly the reader that relaxes this as a tidy-up — so per `AGENTS.md` the
    ruling carries an `OVERRIDES:` line on the artifact that lane reads.

    Both directions are pinned, which is the whole point:

      * the marker + the wire-contract sentence in the bridge doc §10; and
      * the BEHAVIOUR the marker claims — a marker that outlives its refusal is
        the NEXT lane's false premise, so it reddens here too.

    The scope is pinned by COUNT: `require_state=True` appears once, on the
    render path. Adopting it at redemption (where a `state: null` token must
    still redeem) makes the count two and reds this — that is the ruling's own
    boundary, not this test's invention.
    """
    doc = CONSENT_BRIDGE_DOC.read_text(encoding="utf-8")
    start = doc.index("## 10. Integration docs")
    section = doc[start:doc.index("\n## ", start + 1)]

    assert re.search(r"`state` is REQUIRED at the page", section), (
        "the bridge doc §10 wire contract — the section a reader of the page's "
        "contract adopts from — no longer carries the `state` requirement row")

    marker = re.search(
        r"\*\*OVERRIDES:\*\* RFC 6749 §4\.1\.1[^\n]*RECOMMENDED", section)
    assert marker, (
        "the `state` requirement is a CLIENT-VISIBLE departure from a spec default "
        "(RFC 6749 §4.1.1 makes `state` RECOMMENDED) and it carries no "
        "`**OVERRIDES:**` marker naming that default, so the next lane holding the "
        "RFC reads the refusal as an over-strict tidy-up and relaxes it — silently "
        "restoring the bounce that dies as a bare JSON 400. `AGENTS.md` → "
        "\"MARK A DELIBERATE DEPARTURE WITH AN `OVERRIDES:` LINE\".")

    assert re.search(r"invalid_request", section) and re.search(
        r"registered", section), (
        "the marker must state the WIRE SHAPE it overrides with: refused as "
        "`error=invalid_request` at the client's own REGISTERED redirect "
        "(RFC 6749 §4.1.2.1), not as a JSON body — a marker that names neither is "
        "not the contract a client implementer can read")

    assert re.search(r"[Rr]edemption is \*\*unaffected\*\*", section), (
        "the marker must pin its own SCOPE: redemption is unaffected, because "
        "§2.1's `bind` admits `state: null` and such a token must still be "
        "redeemable. Without it the marker licenses the opposite tidy-up")

    # ── the marker's SECOND home: the issue, which is the artifact a lane reads ──
    # `AGENTS.md` → "the marker belongs ON THE ISSUE … with the decision ledger carrying the same
    # line as the index". The relay is half of the finding this row answers (a ruling that lives
    # only in a doc is invisible to the lane scoping #3524), and a LINK is the one part of a
    # marker a tidy-up drops while every word above survives — so it is asserted, not assumed.
    assert re.search(r"issues/3524#issuecomment-\d+", section), (
        "§10's `state` row no longer points at the decision comment carrying the same "
        "`OVERRIDES:` line on #3524 — the marker's second home is gone, so the ruling is back "
        "to living only where a reader of THIS file finds it, invisible to the lane scoping "
        "#3524. That is the exact gap this row closes; re-relay the line and restore the link.")

    # ── the behaviour the marker claims, so the marker cannot outlive it ──
    hosted = HOSTED_API.read_text(encoding="utf-8")
    assert "require_state=True" in hosted, (
        "the doc now advertises a `state` requirement that `hosted_api.py` no "
        "longer enforces — a marker outliving its behaviour is the next lane's "
        "false premise")
    assert hosted.count("require_state=True") == 1, (
        f"`require_state=True` appears {hosted.count('require_state=True')} times in "
        "hosted_api.py; it belongs on the PAGE-RENDER path ONLY. Redemption must "
        "not adopt it — §2.1's `bind` admits `state: null` and that token must "
        "still redeem")

    oauth_src = OAUTH.read_text(encoding="utf-8")
    assert re.search(
            r'if require_state and not \(state or ""\)\.strip\(\):', oauth_src), (
        "`validate_authorize_params` no longer carries the `require_state` arm, so "
        "the page accepts a state-less request it cannot complete; the doc still "
        "claims it is refused")
