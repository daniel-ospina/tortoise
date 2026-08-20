"""E2E-9-D — GitHub integration surface (connect/status/callback/index).

Reconstructed case (#303). Hermetic positives: with GITHUB_CLIENT_ID set (no
secret), connect returns the OAuth auth_url + CSRF state and status reports
not-connected — no network needed. Token exchange needs a real GitHub App →
not exercised (skip-guarded by absence of GITHUB_CLIENT_SECRET on the server).

Negatives: connect on the bare server (no client id) → 503; callback with an
unknown state → 404; index/github without a connection → 400.
"""
from __future__ import annotations  # noqa: I001

import json
import urllib.error
import urllib.request
import uuid

import pytest

from conftest import is_remote_mode, skip_unless_hosted_e2e

skip_unless_hosted_e2e()


def test_connect_returns_auth_url_and_state(api, tenant_factory):
    if is_remote_mode():
        pytest.skip("auth_url asserts the fixture GITHUB_CLIENT_ID "
                    "(e2e_client_id_303) — a remote target returns its own "
                    "client id or 503s unconfigured (E2E-9-D local seam)")
    t = tenant_factory("gh-connect")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    r = api.post("/v1/onboarding/github/connect", headers=h)
    assert r.status == 200, f"github connect: {r.status} {r.text()}"
    body = r.json()
    assert body.get("auth_url", "").startswith("https://github.com/login/oauth/authorize"), body
    assert "client_id=e2e_client_id_303" in body["auth_url"], body
    assert body.get("state"), "connect must return a CSRF state"

    r = api.get("/v1/onboarding/github/status", headers=h)
    assert r.status == 200, r.text()
    status = r.json()
    assert status.get("connected") is False or status.get("github_connected") is False, status


def test_connect_unconfigured_503_bare_server(bare_hosted_server):
    """Bare server has no GITHUB_CLIENT_ID → connect fails closed (503) once
    auth is satisfied (auth runs first — a garbage key would 401)."""
    # Register a tenant ON the bare server for a valid key.
    email = f"e2e-gh-bare-{uuid.uuid4().hex[:8]}@e2e.premise-labs.dev"
    req = urllib.request.Request(
        f"{bare_hosted_server.base_url}/v1/register",
        data=json.dumps({"email": email, "password": "E2ePass-303-x"}).encode(),
        headers={"Content-Type": "application/json"})
    key = json.loads(urllib.request.urlopen(req, timeout=10).read())["api_key"]

    req = urllib.request.Request(
        f"{bare_hosted_server.base_url}/v1/onboarding/github/connect",
        data=b"{}", headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {key}"})
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("connect on unconfigured server must not 2xx")
    except urllib.error.HTTPError as e:
        assert e.code == 503, f"expected 503, got {e.code}: {e.read()[:200]!r}"


def test_callback_bad_state_404(api):
    r = api.get("/v1/onboarding/github/callback?state=e2e_unknown_state&code=x")
    assert r.status == 404, f"unknown state must 404, got {r.status}"


def test_index_github_without_connection_400(api, tenant_factory):
    if is_remote_mode():
        pytest.skip("400 body asserts the unconfigured-connect error text — "
                    "a remote target with GitHub configured returns a "
                    "different error (E2E-9-D local seam)")
    t = tenant_factory("gh-index")
    h = {"Authorization": f"Bearer {t['api_key']}"}
    r = api.post("/v1/index/github", headers=h, data={"org": "some-org"})
    assert r.status == 400, f"index without connect must 400, got {r.status}: {r.text()}"
    assert "connect" in r.text().lower(), r.text()
