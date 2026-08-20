"""Task 1 — #1511 session-exchange helpers: mint-target resolution + GoTrue admin session mint.

Mirrors test_claim_endpoints.py's seam (FakeControlPlane + monkeypatched httpx).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.fake_control_plane import FakeControlPlane  # noqa: E402


# ── mint_target_user_for_key (supabase_control.py) ──────────────────────────

def _cp_with_members(rows: list[dict]) -> FakeControlPlane:
    return FakeControlPlane(tables={"team_memberships": rows})


def test_mint_target_returns_active_member_uuid() -> None:
    from tortoise.supabase_control import mint_target_user_for_key

    cp = _cp_with_members([
        {"team_id": "t1", "user_id": "u-owner", "role": "owner", "status": "active"},
        {"team_id": "t1", "user_id": "u-member", "role": "member", "status": "active"},
    ])
    assert mint_target_user_for_key(cp, "u-member", "t1") == "u-member"
    assert mint_target_user_for_key(cp, "u-owner", "t1") == "u-owner"


def test_mint_target_none_for_non_uuid_or_inactive() -> None:
    from tortoise.supabase_control import mint_target_user_for_key

    cp = _cp_with_members([
        {"team_id": "t1", "user_id": "u-owner", "role": "owner", "status": "active"},
    ])
    # "api" literal, an identity string, NULL, and a UUID not on the team → None
    assert mint_target_user_for_key(cp, "api", "t1") is None
    assert mint_target_user_for_key(cp, "anon-abc", "t1") is None
    assert mint_target_user_for_key(cp, None, "t1") is None
    assert mint_target_user_for_key(cp, "u-other", "t1") is None
    # inactive membership → None
    cp2 = _cp_with_members([
        {"team_id": "t1", "user_id": "u-left", "role": "member", "status": "inactive"},
    ])
    assert mint_target_user_for_key(cp2, "u-left", "t1") is None


# ── _gotrue_admin_get_user (hosted_api.py) ──────────────────────────────────

def _fake_admin_get_user(status: int, body: dict):
    import httpx

    def _handler(url, **kwargs):
        # GET only — the helper calls httpx.get(url, ...); the URL pin proves
        # it is the admin user fetch (not generate_link/verify, which POST).
        assert url.endswith("/auth/v1/admin/users/u-123"), f"url: {url}"
        hdr = kwargs.get("headers", {})
        assert "Bearer" in hdr.get("Authorization", ""), "service-role bearer required"
        return httpx.Response(status, json=body, request=httpx.Request("GET", url))

    return _handler


def test_admin_get_user_hits_get_and_returns_body(monkeypatch) -> None:
    import httpx

    import tortoise.hosted_api as api

    monkeypatch.setattr(os.environ, "SUPABASE_URL", "https://proj.supabase.co", raising=False)
    monkeypatch.setattr(os.environ, "SUPABASE_SERVICE_ROLE_KEY", "svc-key", raising=False)
    monkeypatch.setattr(httpx, "get", _fake_admin_get_user(
        200, {"id": "u-123", "email": "owner@example.com"}))
    status, body = api._gotrue_admin_get_user("u-123")
    assert status == 200
    assert body["email"] == "owner@example.com"


def test_admin_get_user_404_returns_none(monkeypatch) -> None:
    import httpx

    import tortoise.hosted_api as api

    monkeypatch.setattr(os.environ, "SUPABASE_URL", "https://proj.supabase.co", raising=False)
    monkeypatch.setattr(os.environ, "SUPABASE_SERVICE_ROLE_KEY", "svc-key", raising=False)
    monkeypatch.setattr(httpx, "get", _fake_admin_get_user(404, {"msg": "Not found"}))
    assert api._gotrue_admin_get_user("u-123") is None


# ── _gotrue_admin_mint_session (hosted_api.py) ──────────────────────────────

def _fake_mint(gen_link_status: int, gen_link_body: dict, verify_status: int, verify_body: dict):
    import httpx

    calls = {"generate_link": 0, "verify": 0}

    def _post(url, **kwargs):
        if url.endswith("/auth/v1/admin/generate_link"):
            calls["generate_link"] += 1
            assert kwargs["json"] == {"type": "magiclink", "email": "owner@example.com"}
            return httpx.Response(gen_link_status, json=gen_link_body,
                                  request=httpx.Request("POST", url))
        if url.endswith("/auth/v1/verify"):
            calls["verify"] += 1
            body = kwargs["json"]
            assert body["type"] == "magiclink", f"verify type must be magiclink: {body}"
            assert "email" not in body, "token_hash requests must not carry email"
            assert body["token_hash"] == gen_link_body["hashed_token"], \
                "token_hash must be the generate_link hashed_token verbatim"
            return httpx.Response(verify_status, json=verify_body,
                                  request=httpx.Request("POST", url))
        raise AssertionError(f"unexpected POST {url}")

    return _post, calls


def test_mint_session_happy_path(monkeypatch) -> None:
    import httpx

    import tortoise.hosted_api as api

    monkeypatch.setattr(os.environ, "SUPABASE_URL", "https://proj.supabase.co", raising=False)
    monkeypatch.setattr(os.environ, "SUPABASE_SERVICE_ROLE_KEY", "svc-key", raising=False)
    session = {"access_token": "at", "refresh_token": "rt", "expires_in": 3600,
               "expires_at": 9999999999, "token_type": "bearer",
               "user": {"id": "u-123", "email": "owner@example.com"}}
    handler, calls = _fake_mint(200, {"hashed_token": "ht-1", "action_link": "x"},
                                200, session)
    monkeypatch.setattr(httpx, "post", handler)
    got = api._gotrue_admin_mint_session("owner@example.com")
    assert got == session
    assert calls["generate_link"] == 1 and calls["verify"] == 1


def test_mint_session_retryable_on_otp_expired(monkeypatch) -> None:
    import httpx

    import tortoise.hosted_api as api

    monkeypatch.setattr(os.environ, "SUPABASE_URL", "https://proj.supabase.co", raising=False)
    monkeypatch.setattr(os.environ, "SUPABASE_SERVICE_ROLE_KEY", "svc-key", raising=False)
    # double-submit: the first verify consumed the token; the second returns
    # otp_expired → the helper surfaces a RETRYABLE error, not a crash.
    handler, calls = _fake_mint(
        200, {"hashed_token": "ht-1"}, 400,
        {"code": 400, "msg": "Email link is invalid or has expired"})
    monkeypatch.setattr(httpx, "post", handler)
    with pytest.raises(RuntimeError) as exc:
        api._gotrue_admin_mint_session("owner@example.com")
    assert "retry" in str(exc.value).lower() or "expired" in str(exc.value).lower()
    assert calls["verify"] == 1
