"""Fault-injection suite for OAuth grant atomicity (#2863).

Every ``/oauth/token`` write window is made reachable in-process (including
commit-then-lost-response, and failures of a *specific* call site — which a
raise-on-N injector cannot express) so that a compensation failure is loud in
tests instead of an untyped 500 in production.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import tempfile

import pytest
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import _FAULT_CPS, FakeControlPlane  # noqa: RUF100
from tests.test_oauth_mcp import (  # noqa: RUF100
    _U1,
    _enable_supabase,
    _pkce,
)
from tortoise.hosted_api import app
from tortoise.oauth import (  # noqa: RUF100
    REFRESH_TOKEN_TTL_S,
    _expires_iso,
    _sha256,
)

_CLIENT_ID = "client-1"                      # must equal the seeded oauth_clients.id
_REDIRECT = "https://app.example/cb"


# ── Task 1: the fault-injection hook itself ─────────────────────────────────

def test_fault_hook_raises_before_and_after_mutation():
    from tests.fake_control_plane import FakeControlPlane
    cp = FakeControlPlane()
    cp.query("oauth_codes", method="POST", json_body={"code_hash": "h", "used_at": None})

    cp.fail_query(table="oauth_codes", method="GET", times=1, exc=RuntimeError("boom"))
    with pytest.raises(RuntimeError):
        cp.query("oauth_codes", filters=[("code_hash", "eq", "h")])
    assert cp.query("oauth_codes", filters=[("code_hash", "eq", "h")])   # times consumed

    cp.fail_query(table="oauth_codes", method="PATCH", after_mutation=True,
                  exc=RuntimeError("lost response"))
    with pytest.raises(RuntimeError):
        cp.query("oauth_codes", method="PATCH", select=["used_at"],
                 filters=[("code_hash", "eq", "h")], json_body={"used_at": "T"})
    assert cp.query("oauth_codes", filters=[("code_hash", "eq", "h")])[0]["used_at"] == "T"


def test_fault_hook_discriminates_call_sites_by_select_shape():
    """The observation SELECT and the consume PATCH share table+method; only `select`
    distinguishes them — this is what makes the CAS/observation tests possible."""
    cp = FakeControlPlane()
    cp.fail_query(table="oauth_codes", method="PATCH", select=["used_at", "expires_at"],
                  times=1, exc=RuntimeError("observation only"))
    with pytest.raises(RuntimeError):
        cp.query("oauth_codes", method="PATCH", select=["used_at", "expires_at"],
                 filters=[], json_body={"used_at": None})
    assert cp.query("oauth_codes", method="PATCH", select=["id"], filters=[],
                    json_body={"revoked_at": "T"}) == []      # different select → no fault


# ── Task 1b: the shared fixture layer ───────────────────────────────────────

@pytest.fixture
def fault_client(monkeypatch):
    """(TestClient with raise_server_exceptions=False, cp, seeded). Mirrors `api_client`
    (test_oauth_mcp.py:167) but must NOT reuse it: that fixture yields TestClient(app),
    where raise_server_exceptions defaults True, so an injected fault would propagate
    instead of becoming a response."""
    cp = FakeControlPlane()
    _enable_supabase(monkeypatch, cp)
    _seed_base_tables(cp)
    with tempfile.TemporaryDirectory() as tmpdir, \
         patched_tortoise_sdk(os.path.join(tmpdir, "oauth.db")), \
         TestClient(app, raise_server_exceptions=False) as tc:
        yield tc, cp


@pytest.fixture(autouse=True)
def _no_silent_faults():
    """THE recurrence guard for this whole suite. A fault matcher that does not match
    the real call shape is a test that pins nothing — and it passes. `fail_query`
    registers every control plane it is called on, so bare-`FakeControlPlane` tests are
    covered too (not just `fault_client` ones). Any injector left unfired (or under-fired
    vs its `times`) fails the test loudly."""
    _FAULT_CPS.clear()
    yield
    stale = [f for cp in _FAULT_CPS for f in cp.unfired_faults()]
    if stale:
        pytest.fail(f"fault injectors never fired (stale match): {stale}")


def _seed_base_tables(cp) -> None:
    """oauth_clients (id=_CLIENT_ID, token_endpoint_auth_method='none'), teams
    (id='t1'), team_memberships (user_id=_U1 UUID, team_id='t1', status='active')."""
    cp.tables.setdefault("oauth_clients", []).append({
        "id": _CLIENT_ID, "client_name": "test", "redirect_uris": [_REDIRECT],
        "scope": "mcp", "token_endpoint_auth_method": "none",
        "created_at": "2026-01-01T00:00:00+00:00"})
    cp.tables.setdefault("teams", []).append({
        "id": "t1", "tier": "Team", "suspended_at": None, "flagged_at": None,
        "email": "t@example.com"})
    cp.tables.setdefault("team_memberships", []).append({
        "user_id": _U1, "team_id": "t1", "role": "owner", "status": "active"})


def _live(cp, table: str) -> list[dict]:
    return [r for r in cp.tables.get(table, []) if r.get("revoked_at") is None]


def _s256(verifier: str) -> str:
    """base64url(sha256(verifier)), unpadded — the same transform `_verify_pkce` applies.
    Compare within ONE `_pkce()` pair (it generates a fresh pair per call, so comparing
    two invocations can never hold): `v, c = _pkce(); assert _s256(v) == c`."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()
                                    ).rstrip(b"=").decode()


def _seed_code(cp, code="code-1", *, verifier=None, client_id=_CLIENT_ID,
               user_id=_U1, team_id="t1", redirect_uri=_REDIRECT, used_at=None,
               expires_in=600) -> str:
    """Insert an oauth_codes row and RETURN the PKCE verifier (a code seeded without
    its verifier 400s on PKCE before ever reaching the injected fault). Uses the
    production encoders so the hash and timestamp formats match."""
    verifier = verifier or _pkce()[0]
    cp.tables.setdefault("oauth_codes", []).append({
        "code_hash": _sha256(code), "client_id": client_id, "user_id": user_id,
        "team_id": team_id, "redirect_uri": redirect_uri,
        "code_challenge": _s256(verifier), "code_challenge_method": "S256",
        "scope": "mcp", "resource": None,
        "expires_at": _expires_iso(expires_in), "used_at": used_at,
        "created_at": _expires_iso(0)})
    return verifier


def _seed_refresh_token(cp, token="rt-1", **over) -> tuple[str, str]:
    """Insert an oauth_refresh_tokens row. RETURNS (row_id, plaintext_token) — ONE
    VALUE CANNOT BOTH: `refresh_grant` looks the row up by `token_hash` but lane 3
    and the claim PATCH address it by `id`."""
    token = over.pop("token", token)
    row = {"id": over.pop("id", secrets.token_urlsafe(16)), "token_hash": _sha256(token),
           "client_id": _CLIENT_ID, "user_id": _U1, "team_id": "t1", "scope": "mcp",
           "expires_at": _expires_iso(REFRESH_TOKEN_TTL_S), "revoked_at": None,
           "rotated_from": None, "created_at": _expires_iso(0), **over}
    cp.tables.setdefault("oauth_refresh_tokens", []).append(row)
    return row["id"], token


def _seed_access_token(cp, *, refresh_id: str) -> str:
    """Insert an oauth_access_tokens row with `refresh_token_id=refresh_id` and return
    its `id` (this is what `refresh_grant`'s prev_access SELECT matches)."""
    row_id = secrets.token_urlsafe(16)
    cp.tables.setdefault("oauth_access_tokens", []).append({
        "id": row_id, "token_hash": _sha256("at-" + row_id), "client_id": _CLIENT_ID,
        "user_id": _U1, "team_id": "t1", "scope": "mcp",
        "expires_at": _expires_iso(3600), "revoked_at": None,
        "refresh_token_id": refresh_id, "created_at": _expires_iso(0)})
    return row_id


def _post_code(tc, cp, code, verifier, **over):
    body = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier,
            "client_id": _CLIENT_ID, "redirect_uri": _REDIRECT, **over}
    return tc.post("/oauth/token", data=body)


def _post_refresh(tc, cp, token: str, **over):
    return tc.post("/oauth/token", data={"grant_type": "refresh_token",
                                        "refresh_token": token,
                                        "client_id": _CLIENT_ID, **over})


def test_fixture_layer_no_fault_probe(fault_client):
    """The fixtures must not themselves 400/401 — an unseeded base table would make
    every later assertion vacuous."""
    tc, cp = fault_client
    verifier = _seed_code(cp, "probe-code")
    r = _post_code(tc, cp, "probe-code", verifier)
    assert r.status_code == 200, r.text
    _, rt = _seed_refresh_token(cp, "probe-rt")
    r2 = _post_refresh(tc, cp, rt)
    assert r2.status_code == 200, r2.text
