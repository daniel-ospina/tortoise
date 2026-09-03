"""HTTP journey test: invite → email hook → public info → accept (#307/#1177).

Registry (selfhost) path via TestClient + temp FalkorDBLite, mirroring
tests/test_invites_http.py fixtures. Email send is monkeypatched (no network).
"""
from __future__ import annotations

import os
import tempfile

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest
from fastapi.testclient import TestClient

from tests._http_fixtures import patched_tortoise_sdk
from tortoise import email_notify
from tortoise.hosted_api import app, get_current_user

# #1719 (Task 3): team_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs; non-UUID user_id literals are prod-impossible.
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"
_U2 = "9f2c1a40-0000-4a00-8000-000000000002"


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U1,
            "email": "owner@example.com",
        }
        # #2127: shared helper (tests._http_fixtures.patched_tortoise_sdk) —
        # patch __init__ → temp DB + #1950 TORTOISE_DB_PATH pin + close-then-
        # clear at enter; pop-pin → restore __init__ → deterministic anchor
        # close → clear overrides at exit (replaces the local
        # _patch/_restore_tortoise_sdk_init copies). The _REG_SDKS hold +
        # close stay here (direct SDK constructions, not keepalive anchors).
        with patched_tortoise_sdk(db_path):
            try:
                with TestClient(app) as tc:
                    yield tc
            finally:
                while _REG_SDKS:
                    try:  # noqa: SIM105
                        _REG_SDKS.pop().close()
                    except Exception:
                        pass


# #1556: hold registry SDKs alive — _get_registry() drops the SDK ref;
# close-on-GC (#1475) shuts the temp server before the test uses it.
_REG_SDKS: list = []


@pytest.fixture
def reg(client):
    from tortoise.hosted_api import _make_sdk
    sdk = _make_sdk(namespace="registry")
    _REG_SDKS.append(sdk)
    return sdk._get_registry()


def _seed_team(reg, team_id: str = "team-1"):
    reg.query(
        "CREATE (t:Team {id:$id, name:$id, tier:'team'})",
        params={"id": team_id},
    )


def _seed_membership(reg, team_id: str, user_id: str, role: str = "owner"):
    reg.query(
        "CREATE (m:Membership {user_id:$uid, team_id:$tid, role:$role, "
        "status:'active', created_at:'2026-08-01T00:00:00+00:00'})",
        params={"uid": user_id, "tid": team_id, "role": role},
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret_key_123")
    monkeypatch.setenv("EMAIL_LINK_BASE_URL", "https://tortoise.premiselabs.co")
    email_notify._skip_logged.clear()
    yield


def test_full_journey_invite_info_accept(client, reg, monkeypatch):
    """invite → email scheduled with token → info returns variables → accept."""
    _seed_team(reg, "team-1")
    _seed_membership(reg, "team-1", _U1)

    scheduled = {}

    def fake_send(team_name, invitee_email, role, token, invitation_id, on_sent=None):
        scheduled.update(team_name=team_name, email=invitee_email, role=role,
                         token=token, iid=invitation_id, on_sent=on_sent)

    monkeypatch.setattr(email_notify, "send_invite_email", fake_send)

    # 1. Admin invites a member
    r = client.post("/v1/invites", json={"team_id": "team-1", "email": "bob@example.com", "role": "member"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "invited"
    token = body["token"]

    # 2. Email was scheduled with the real token + team name + role
    assert scheduled["token"] == token
    assert scheduled["team_name"] == "team-1"
    assert scheduled["role"] == "member"
    assert scheduled["email"] == "bob@example.com"

    # 3. Public info endpoint returns the display variables (team name + inviter)
    r = client.get(f"/v1/invites/info?token={token}")
    assert r.status_code == 200, r.text
    info = r.json()
    assert info["team_name"] == "team-1"
    assert info["inviter_email"] == "owner@example.com"  # captured from JWT at mint
    assert info["role"] == "member"
    assert "expires_at" in info

    # 4. Invitee (bob, different user) accepts — email-match guard passes
    from tortoise.hosted_api import app as _app
    _app.dependency_overrides[get_current_user] = lambda: {
        "user_id": _U2,
        "email": "bob@example.com",
    }
    r = client.post("/v1/invites/accept", json={"token": token})
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "member"

    # 5. Membership is active
    rows = reg.query(
        "MATCH (m:Membership {team_id:$tid, user_id:$uid}) RETURN m.status",
        params={"tid": "team-1", "uid": _U2},
    ).result_set
    assert rows and rows[0][0] == "active"


def test_info_unknown_token_404(client, reg):
    _seed_team(reg, "team-1")
    _seed_membership(reg, "team-1", _U1)
    r = client.get("/v1/invites/info?token=nonexistent-token")
    assert r.status_code == 404
    assert r.json()["detail"] == "Invite not found or expired"


def test_info_consumed_token_404(client, reg, monkeypatch):
    """An accepted invite is no longer visible via info (no oracle)."""
    _seed_team(reg, "team-1")
    _seed_membership(reg, "team-1", _U1)

    def fake_send(*a, **k):
        pass

    monkeypatch.setattr(email_notify, "send_invite_email", fake_send)

    r = client.post("/v1/invites", json={"team_id": "team-1", "email": "bob@example.com"})
    token = r.json()["token"]

    from tortoise.hosted_api import app as _app
    _app.dependency_overrides[get_current_user] = lambda: {
        "user_id": _U2,
        "email": "bob@example.com",
    }
    assert client.post("/v1/invites/accept", json={"token": token}).status_code == 200
    assert client.get(f"/v1/invites/info?token={token}").status_code == 404


def test_invite_email_failure_does_not_fail_mint(client, reg, monkeypatch, caplog):
    _seed_team(reg, "team-1")
    _seed_membership(reg, "team-1", _U1)

    def boom(*a, **k):
        raise RuntimeError("resend down")

    monkeypatch.setattr(email_notify, "send_invite_email", boom)
    r = client.post("/v1/invites", json={"team_id": "team-1", "email": "bob@example.com"})
    assert r.status_code == 200  # mint survives email failure
    assert r.json()["status"] == "invited"


def test_info_link_host_never_from_host_header(client, reg, monkeypatch):
    """EMAIL_LINK_BASE_URL drives the link — never the request Host header."""
    _seed_team(reg, "team-1")
    _seed_membership(reg, "team-1", _U1)

    captured = {}

    def fake_send(team_name, invitee_email, role, token, invitation_id, on_sent=None):
        captured["link_base"] = None
        from tortoise.email_notify import _build_invite_link
        captured["link"] = _build_invite_link(token)

    monkeypatch.setattr(email_notify, "send_invite_email", fake_send)
    client.post("/v1/invites", json={"team_id": "team-1", "email": "bob@example.com"},
                headers={"Host": "evil.example.com"})
    assert captured["link"].startswith("https://tortoise.premiselabs.co/")
