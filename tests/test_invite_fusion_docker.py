"""#2003 (W7) — invite-accept fusion DE2E-7/8 docker-lane journey tests.

Docker-lane (TORTOISE_DB_URI): real FalkorDB graph semantics (server-mode
eager-init Cypher + keyed-MERGE writers — the embedded redislite lane cannot
satisfy these, #1997 tier-2 regression). URI-less runs (tier-2 embedded
legs / carve-out) SKIP at module level — mirror tests/test_onboarding_state_split.py.

Journey under test (epic DE2E-8 surface 12):
- register a fresh owner (account leg) → invite a second email → mismatch
  override accept via OTP (DE2E-7 mechanics on the real graph) →
  consumed-token replay is idempotent → member_progress arming writes the
  user-scoped slot WITHOUT advancing org-level steps / faking completion.
"""
from __future__ import annotations

import os
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest

# docker-lane gate (epic #1647 P4 / #1997): URI-less embedded legs cannot run
# these server-mode graph assertions — skip cleanly instead of failing.
from tortoise.config import is_db_uri as _is_db_uri

if not _is_db_uri(os.environ.get("TORTOISE_DB_URI")):
    pytest.skip("docker-lane invite-fusion tests require TORTOISE_DB_URI "
                "(tier-2 embedded legs skip)", allow_module_level=True)

from fastapi.testclient import TestClient

from tortoise import email_notify
from tortoise.hosted_api import _make_sdk, app, get_current_user
from tortoise.onboarding import state as _os

# real-JWT-shaped uuid subjects (#1719)
_U_OWNER = "9f2c1a40-0000-4a00-8000-000000000101"
_U_INVITEE = "9f2c1a40-0000-4a00-8000-000000000102"

V2_ACCEPT = "application/vnd.tortoise.onboarding+json;version=2"


@pytest.fixture
def client():
    """Registry-mode TestClient on the docker lane (env URI)."""
    with TestClient(app) as tc:
        yield tc


def _suffix() -> str:
    return uuid.uuid4().hex[:10]


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "re_test_secret_key_123")
    monkeypatch.setenv("EMAIL_LINK_BASE_URL", "https://tortoise.premiselabs.co")
    email_notify._skip_logged.clear()


def _invitee_email() -> str:
    return f"invitee-{_suffix()}@example.com"


class TestFusionDockerJourney:
    def test_de2e8_atomic_accept_arms_member_progress(
            self, client, env, monkeypatch):
        """Register (account) → invite → one-click accept (atomic: token
        consumed + membership in the SAME request) → member slot armed,
        org-level steps untouched, replay idempotent."""
        owner_email = f"owner-{_suffix()}@example.com"
        r = client.post("/v1/register",
                        json={"email": owner_email, "password": "password123"})
        assert r.status_code == 200, r.text
        team_id = r.json()["team_id"]

        # owner Membership is required for invite RBAC — the register lane
        # keys the team by email (no membership row), so seed it directly on
        # the shared control graph.
        reg = _make_sdk(namespace="registry")._get_registry()
        reg.query(
            "CREATE (m:Membership {user_id:$uid, team_id:$tid, role:'owner', "
            "status:'active', created_at:'2026-09-01T00:00:00+00:00'})",
            params={"uid": _U_OWNER, "tid": team_id},
        )
        # register provisions the team at tier='free' — invites need the Team
        # tier (docker lane is registry mode; the conftest provision fixture
        # does the same tier bump for its teams).
        reg.query("MATCH (t:Team {id:$id}) SET t.tier = 'team', "
                  "t.max_users = 3",
                  params={"id": team_id})
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_OWNER, "email": owner_email,
        }
        inv_email = _invitee_email()
        r = client.post("/v1/invites",
                        json={"team_id": team_id, "email": inv_email})
        assert r.status_code == 200, r.text
        token = r.json()["token"]

        # Atomic accept as the invitee (email match — one action, no
        # intermediate create-then-accept state).
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_INVITEE, "email": inv_email,
        }
        r = client.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 200, r.text
        assert r.json() == {"team_id": team_id, "role": "member"}
        # membership exists in the control graph
        rows = reg.query(
            "MATCH (m:Membership {team_id:$tid, user_id:$uid, status:'active'}) "
            "RETURN count(m)",
            params={"tid": team_id, "uid": _U_INVITEE},
        ).result_set
        assert rows[0][0] == 1

        # member slot armed: node exists (create-on-write) + user-scoped
        # member_progress {user_id: []} — never org-level steps.
        proj = _make_sdk(namespace=team_id)._get_proj()
        node = _os.read_onboarding_node(proj, team_id)
        assert node is not None, "OnboardingState node not armed after accept"
        progress = _os.parse_member_progress(node.get("member_progress"))
        assert progress.get(_U_INVITEE) == []
        assert node.get("status") == _os.STATUS_ACTIVE
        # the org's own register-time edge (team-named) is the ONLY org-level
        # step — the accept/member write never faked the rest.
        steps = _os.completed_steps(proj, team_id)
        assert set(steps) <= {"team-named"}

        # consumed-token replay → idempotent failure, no double membership
        r2 = client.post("/v1/invites/accept", json={"token": token})
        assert r2.status_code == 400
        rows = reg.query(
            "MATCH (m:Membership {team_id:$tid, user_id:$uid, status:'active'}) "
            "RETURN count(m)",
            params={"tid": team_id, "uid": _U_INVITEE},
        ).result_set
        assert rows[0][0] == 1

    def test_de2e7_mismatch_override_with_otp_on_real_graph(
            self, client, env, monkeypatch):
        """3-path discovery → OTP send (captured via the email seam) → fuse
        override with OTP accepts under the CURRENT account and records the
        mismatch + proof on the invitation."""
        owner_email = f"owner2-{_suffix()}@example.com"
        r = client.post("/v1/register",
                        json={"email": owner_email, "password": "password123"})
        assert r.status_code == 200, r.text
        team_id = r.json()["team_id"]
        reg = _make_sdk(namespace="registry")._get_registry()
        reg.query(
            "CREATE (m:Membership {user_id:$uid, team_id:$tid, role:'owner', "
            "status:'active', created_at:'2026-09-01T00:00:00+00:00'})",
            params={"uid": _U_OWNER, "tid": team_id},
        )
        # register provisions the team at tier='free' — invites need the Team
        # tier (docker lane is registry mode; the conftest provision fixture
        # does the same tier bump for its teams).
        reg.query("MATCH (t:Team {id:$id}) SET t.tier = 'team', "
                  "t.max_users = 3",
                  params={"id": team_id})
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_OWNER, "email": owner_email,
        }
        inv_email = _invitee_email()
        r = client.post("/v1/invites",
                        json={"team_id": team_id, "email": inv_email})
        assert r.status_code == 200, r.text
        inv = r.json()

        # a DIFFERENT logged-in account clicks the link → opted-in mismatch
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_INVITEE, "email": f"other-{_suffix()}@example.com",
        }
        # 3-path discovery (never silent)
        r = client.post("/v1/invites/accept", json={"token": inv["token"]},
                        headers={"Accept": V2_ACCEPT})
        assert r.status_code == 409, r.text
        choice = r.json()["detail"]["choice"]
        assert choice["default_path"] == "fuse"
        assert choice["invited_email"] == inv_email
        # fuse without OTP → blocked
        r = client.post("/v1/invites/accept",
                        json={"token": inv["token"], "path": "fuse"},
                        headers={"Accept": V2_ACCEPT})
        assert r.status_code == 403
        assert r.json()["detail"]["error_code"] == "invite_mismatch_otp_required"

        # send the OTP to the INVITEE mailbox (captured via the email seam)
        captured = {}

        def fake_send(team_name, invitee_email, code, on_sent=None):
            captured.update(code=code, email=invitee_email)

        monkeypatch.setattr(email_notify, "send_otp_email", fake_send)
        r = client.post("/v1/invites/otp", json={"token": inv["token"]})
        assert r.status_code == 200, r.text
        assert captured["email"] == inv_email

        # fuse + OTP → accepted under the current account
        r = client.post("/v1/invites/accept",
                        json={"token": inv["token"], "path": "fuse",
                              "otp": captured["code"]},
                        headers={"Accept": V2_ACCEPT})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["accepted_via"] == "fuse"
        assert body["mismatch"] == {"invited_email": inv_email, "recorded": True}
        rows = reg.query(
            "MATCH (m:Membership {team_id:$tid, user_id:$uid, status:'active'}) "
            "RETURN count(m)",
            params={"tid": team_id, "uid": _U_INVITEE},
        ).result_set
        assert rows[0][0] == 1
        # invite records the override — never silent
        row = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN i.accepted_via, "
            "i.accepted_mismatch, i.fused_from_email, i.otp_verified_at",
            params={"id": inv["invite_id"]},
        ).result_set[0]
        assert row[0] == "fuse" and row[1] is True
        assert row[2] == inv_email and row[3] is not None

        # legacy (no v2 header) replay → byte-unchanged 403 mismatch... the
        # invite is consumed now — replay is the consumed-token 400 (not a
        # double membership).
        r = client.post("/v1/invites/accept", json={"token": inv["token"]})
        assert r.status_code == 400

    def test_legacy_403_byte_unchanged_no_optin(self, client, env):
        """DE2E-7: no v2 header → the pre-W7 403 verbatim on the real lane."""
        owner_email = f"owner3-{_suffix()}@example.com"
        r = client.post("/v1/register",
                        json={"email": owner_email, "password": "password123"})
        assert r.status_code == 200, r.text
        team_id = r.json()["team_id"]
        reg = _make_sdk(namespace="registry")._get_registry()
        reg.query(
            "CREATE (m:Membership {user_id:$uid, team_id:$tid, role:'owner', "
            "status:'active', created_at:'2026-09-01T00:00:00+00:00'})",
            params={"uid": _U_OWNER, "tid": team_id},
        )
        # register provisions the team at tier='free' — invites need the Team
        # tier (docker lane is registry mode; the conftest provision fixture
        # does the same tier bump for its teams).
        reg.query("MATCH (t:Team {id:$id}) SET t.tier = 'team', "
                  "t.max_users = 3",
                  params={"id": team_id})
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_OWNER, "email": owner_email,
        }
        inv_email = _invitee_email()
        r = client.post("/v1/invites",
                        json={"team_id": team_id, "email": inv_email})
        assert r.status_code == 200, r.text
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U_INVITEE,
            "email": f"stranger-{_suffix()}@example.com",
        }
        r = client.post("/v1/invites/accept", json={"token": r.json()["token"]})
        assert r.status_code == 403
        assert r.json()["detail"] == "Invite email does not match this account"
