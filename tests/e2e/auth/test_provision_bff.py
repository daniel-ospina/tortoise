"""
/api/provision — the same-origin BFF bridge to the `tenant-provision` Edge
Function.

WHY THIS SUITE EXISTS
---------------------
First-org provisioning used to be the ONE client data call that did not go
through the BFF: the dashboard POSTed `{SUPABASE_URL}/functions/v1/
tenant-provision` from the browser with `Authorization: Bearer <access token>`.
That is impossible under the BFF — for the BFF session the browser holds only an opaque
`__Host-session` handle — so the route must mint the credential server-side and
attach it outbound, exactly as `/api/v1` does.

These tests observe the UPSTREAM (the gateway records what the provision
endpoint actually received), because the property under test is "was the
server-held token attached?", and only the upstream can answer that. A status
check alone cannot distinguish "credential attached" from "credential missing".

Contract note: the real Edge Function answers **201** on success
(`supabase/functions/tenant-provision/index.ts`), so the pass-through route
answers 201. This suite asserts the REAL upstream status rather than a
normalised 200 — normalising would violate the route's own contract ("return the
upstream body + status") and blind it to the Edge Function's actual response.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from bff_test_helpers import pick_free_port, require_toolchain, stop

REPO_ROOT = Path(__file__).resolve().parents[3]
# The BFF moved to the DASHBOARD Pages project (issue #4054). The Pages project
# root is `website/apps/dashboard` — `functions/` must sit beside the site dir,
# which is why `wrangler pages dev .` runs from there and not from `website/`.
DASHBOARD_DIR = REPO_ROOT / "website" / "apps" / "dashboard"
MOCK = REPO_ROOT / "tests" / "e2e" / "auth" / "mock_supabase.mjs"
GATEWAY = REPO_ROOT / "tests" / "e2e" / "auth" / "mock_provision_gateway.mjs"

APP_PORT = int(os.environ.get("AUTH_PROV_APP_PORT", "9002"))
MOCK_PORT = int(os.environ.get("AUTH_PROV_MOCK_PORT", "9003"))
GW_PORT = int(os.environ.get("AUTH_PROV_GW_PORT", "9004"))
APP = f"http://localhost:{APP_PORT}"
GW_URL = f"http://127.0.0.1:{GW_PORT}"


def _wait(port: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.4)
    return False


@pytest.fixture(scope="module")
def stack():
    # FAIL, do not skip: a skipped security suite is indistinguishable from a
    # passing one (see bff_test_helpers).
    require_toolchain()

    global APP_PORT, MOCK_PORT, GW_PORT, APP, GW_URL
    claimed: set[int] = set()
    APP_PORT = pick_free_port(APP_PORT, claimed)
    MOCK_PORT = pick_free_port(MOCK_PORT, claimed)
    GW_PORT = pick_free_port(GW_PORT, claimed)
    assert len({APP_PORT, MOCK_PORT, GW_PORT}) == 3
    APP = f"http://localhost:{APP_PORT}"
    GW_URL = f"http://127.0.0.1:{GW_PORT}"

    # 1) The shared auth mock — REAL ES256 signing and REAL PKCE S256 checks.
    mock_env = os.environ.copy()
    mock_env["MOCK_PORT"] = str(MOCK_PORT)
    mock = subprocess.Popen(
        [shutil.which("node"), str(MOCK)], cwd=str(MOCK.parent), env=mock_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    assert _wait(MOCK_PORT), "mock failed to start"

    # 2) The provision gateway: proxies auth to the mock, emulates ONLY the
    #    tenant-provision surface (see the file header for why).
    gw_env = os.environ.copy()
    gw_env["MOCK_PORT"] = str(MOCK_PORT)
    gw_env["GATEWAY_PORT"] = str(GW_PORT)
    gateway = subprocess.Popen(
        [shutil.which("node"), str(GATEWAY)], cwd=str(GATEWAY.parent), env=gw_env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    assert _wait(GW_PORT), "gateway failed to start"

    # 3) The real Pages runtime. SUPABASE_URL points at the gateway so that the
    #    SAME origin serves the auth flow AND the provision endpoint.
    app = subprocess.Popen(
        [
            shutil.which("wrangler"), "pages", "dev", "dist",
            "--port", str(APP_PORT), "--ip", "127.0.0.1",
            "--d1", "SESSIONS",
            "-b", f"SUPABASE_URL={GW_URL}",
            "-b", "SUPABASE_ANON_KEY=mock-anon-key",
            "-b", f"AUTH_CALLBACK_URL={APP}/auth/callback",
            "-b", f"APP_ORIGIN={APP}",
        ],
        cwd=str(DASHBOARD_DIR),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    if not _wait(APP_PORT):
        for p in (app, gateway, mock):
            stop(p)
        pytest.fail("pages dev failed to start")
    time.sleep(2.5)

    yield {"app": APP, "gateway": GW_URL}

    for p in (app, gateway, mock):
        stop(p)


def _session_cookie() -> str:
    """Drive the REAL sign-in flow and return the opaque session cookie."""

    class Policy(http.cookiejar.DefaultCookiePolicy):
        def return_ok_secure(self, cookie, request):
            u = request.get_full_url() or ""
            if u.startswith(("http://127.0.0.1", "http://localhost")):
                return True
            return super().return_ok_secure(cookie, request)

    jar = http.cookiejar.CookieJar(policy=Policy())
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    with opener.open(f"{APP}/auth/start", timeout=45) as r:
        r.read()
    for c in jar:
        if c.name == "__Host-session":
            return f"__Host-session={c.value}"
    raise AssertionError("sign-in did not produce a session cookie")


def _gateway_reset() -> None:
    """Forget the last provision request, so 'was upstream called?' is exact."""
    req = urllib.request.Request(
        f"{GW_URL}/__gateway/seen", method="POST",
        data=json.dumps({"reset": True}).encode(),
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def _gateway_seen():
    """The last provision request the upstream actually received (or None)."""
    req = urllib.request.Request(f"{GW_URL}/__gateway/seen", method="GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())["seen"]


def _gateway_fault(**kwargs) -> None:
    req = urllib.request.Request(
        f"{GW_URL}/__gateway/fault", method="POST",
        data=json.dumps(kwargs).encode(),
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        r.read()


def _post_provision(cookie: str | None, payload: dict):
    """POST /api/provision. Deliberately sends NO Authorization header."""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(f"{APP}/api/provision", method="POST", data=data)
    req.add_header("Content-Type", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), dict(e.headers)


VALID_PAYLOAD = {
    "user_id": "user-123",
    "email": "user-123@example.test",
    "org_name": "Acme",
}


# ---------------------------------------------------------------------------
# 1. No session cookie -> 401, and the upstream is NEVER called.
# ---------------------------------------------------------------------------
def test_anonymous_provision_is_401_and_upstream_not_called(stack):
    _gateway_reset()
    status, body, _ = _post_provision(None, VALID_PAYLOAD)

    assert status == 401, f"anonymous must be 401, got {status} {body}"
    assert json.loads(body)["error"] == "not_signed_in", body
    # The credential is minted AFTER the session check; a missing session must
    # short-circuit before any upstream call. Asserting the upstream saw nothing
    # is the only proof of that ordering.
    assert _gateway_seen() is None, (
        "the provision endpoint was called without a session — the auth gate "
        "does not run before the upstream request"
    )


# ---------------------------------------------------------------------------
# 2. Valid session -> upstream called with the SERVER-held token + body.
# ---------------------------------------------------------------------------
def test_provision_attaches_server_held_credential_and_forwards_body(stack):
    _gateway_reset()
    cookie = _session_cookie()

    status, body, _ = _post_provision(cookie, VALID_PAYLOAD)
    # The real Edge Function returns 201 on success; the route passes the
    # upstream status through (it does not normalise to 200).
    assert status == 201, f"expected proxied 201, got {status} {body}"

    seen = _gateway_seen()
    assert seen is not None, "the provision endpoint was never called"
    assert seen["method"] == "POST", seen
    # The browser sent NO Authorization header. Anything the upstream saw was
    # therefore attached server-side — this is the whole point of the route.
    assert seen["authorization"], "the BFF did not attach an Authorization header"
    assert seen["authorization"].startswith("Bearer "), seen["authorization"]
    assert seen["authorization"].count(".") == 2, (
        f"expected a JWT-shaped server-held token, got {seen['authorization']!r}"
    )
    # Our session handle must not be forwarded upstream — it is ours, not theirs.
    assert seen["cookieLeaked"] is False, "the session cookie leaked upstream"
    # The request body is forwarded verbatim.
    forwarded = json.loads(seen["body"])
    assert forwarded == VALID_PAYLOAD, f"body was not forwarded verbatim: {forwarded}"
    # Content-Type is preserved.
    assert seen["contentType"] == "application/json", seen["contentType"]


# ---------------------------------------------------------------------------
# 3. Upstream 500 -> mapped honestly, never swallowed into a 200.
# ---------------------------------------------------------------------------
def test_upstream_500_is_not_swallowed_into_a_200(stack):
    cookie = _session_cookie()
    _gateway_fault(provision=500)
    try:
        status, body, _ = _post_provision(cookie, VALID_PAYLOAD)
    finally:
        _gateway_fault(provision=None)

    assert 500 <= status < 600, (
        f"an upstream 500 must surface as a 5xx, never a 200 — got {status} {body}"
    )
    # And the upstream WAS reached (the failure is the upstream's, not the
    # route short-circuiting).
    assert _gateway_seen() is not None


# ---------------------------------------------------------------------------
# 4. The credential never appears in the response (body or headers).
# ---------------------------------------------------------------------------
def test_token_never_reaches_the_browser(stack):
    _gateway_reset()
    cookie = _session_cookie()
    status, body, headers = _post_provision(cookie, VALID_PAYLOAD)
    assert status == 201, f"setup call failed: {status} {body}"

    seen = _gateway_seen()
    token = seen["authorization"].split(" ", 1)[1]
    assert token and len(token) > 20, f"no real token observed: {token!r}"

    raw = body + "\n" + "\n".join(f"{k}: {v}" for k, v in headers.items())
    assert token not in raw, "the server-held access token leaked into the response"
    assert "Bearer " + token not in raw, "the Authorization header leaked into the response"


# ---------------------------------------------------------------------------
# 5. Non-POST methods are rejected, and never reach the upstream.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE", "OPTIONS"])
def test_non_post_methods_are_rejected(stack, method):
    _gateway_reset()
    cookie = _session_cookie()
    req = urllib.request.Request(f"{APP}/api/provision", method=method)
    req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status, body, headers = r.status, r.read().decode(), dict(r.headers)
    except urllib.error.HTTPError as e:
        status, body, headers = e.code, e.read().decode(), dict(e.headers)

    assert status == 405, f"{method} must be 405, got {status} {body}"
    assert json.loads(body)["error"] == "method_not_allowed", body
    assert "POST" in (headers.get("Allow") or ""), (
        f"405 must advertise Allow: POST, got {headers.get('Allow')!r}"
    )
    assert _gateway_seen() is None, (
        f"{method} reached the provision endpoint — the method gate does not "
        "run before the upstream request"
    )


# ---------------------------------------------------------------------------
# House-style properties, matching the rest of the BFF.
# ---------------------------------------------------------------------------
def test_provision_response_is_not_cacheable(stack):
    cookie = _session_cookie()
    _gateway_fault(provision=500)
    try:
        _, _, headers = _post_provision(cookie, VALID_PAYLOAD)
    finally:
        _gateway_fault(provision=None)
    cc = headers.get("Cache-Control", "")
    assert "no-store" in cc, f"every path must be uncacheable, got {cc!r}"


def test_provision_response_has_no_cors_headers(stack):
    """Same-origin by construction: the BFF must not advertise cross-origin access."""
    cookie = _session_cookie()
    _, _, headers = _post_provision(cookie, VALID_PAYLOAD)
    cors = {k: v for k, v in headers.items() if k.lower().startswith("access-control-")}
    assert not cors, f"/api/provision must be Access-Control-free, got {cors}"


def test_upstream_401_is_503_not_a_signout(stack):
    """The #3485 rule at this route: an upstream credential rejection is not OUR 401.

    This route's own 401 means — and only ever means — "you are not signed in".
    Forwarding an upstream 401 verbatim would make /api/provision say "signed
    out" while /api/session says 200 for the SAME cookie: the divergence the BFF
    exists to remove. The retry signal is 503.
    """
    cookie = _session_cookie()
    _gateway_fault(provision=401)
    try:
        status, body, _ = _post_provision(cookie, VALID_PAYLOAD)
    finally:
        _gateway_fault(provision=None)

    assert status == 503, (
        f"an upstream 401 must map to 503 (retry), never our 401 (signed out) — "
        f"got {status} {body}"
    )
    assert json.loads(body)["error"] != "not_signed_in", body
