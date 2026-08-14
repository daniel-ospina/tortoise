"""HTTP-layer tests for invites + RBAC — E3/E4/E8 (#748).

Issue #748 (P1): test_invites.py covers SDK primitives only; the actual
endpoints (/v1/invites, /v1/invites/accept, /v1/teams/{id}/members
GET/DELETE/PATCH) were untested. Revenue gates (402/409) and the member-role
invite path (#743) could ship broken with green CI.

Covers, on the registry (selfhost) path:
- 402 free-tier gate (invites require the Team tier)
- 409 duplicate pending invitation
- 422 validation (email, role)
- 403 non-owner/admin invite + member RBAC denial on all member endpoints
- token returned once (hash-only at rest) + single-use accept
- 403 email-mismatch on accept
- 409 already-a-member, 409 owner removal / owner demotion
- 404 unknown member
- member accept creates active membership with the INVITED ROLE (E4 role
  preservation — the #743 bug class)
- expired invite token → 400

Fixture mirrors tests/test_hosted_api.py (dependency override + temp
FalkorDBLite DB).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest
from fastapi.testclient import TestClient

from tortoise.auth import verify_api_key
from tortoise.hosted_api import app, get_current_user
from tortoise.sdk import TortoiseSDK


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _patch_tortoise_sdk_init(db_path: str):
    import tortoise.hosted_api as ha_mod

    _orig_init = ha_mod.TortoiseSDK.__init__

    def _patched_init(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig_init(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched_init
    return _orig_init


def _restore_tortoise_sdk_init(original_init):
    import tortoise.hosted_api as ha_mod

    ha_mod.TortoiseSDK.__init__ = original_init


@pytest.fixture
def client():
    """TestClient with get_current_user (session JWT) overridden + temp DB.

    The authenticated user is the OWNER (user-1 / owner@example.com) unless a
    test overrides the dependency.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": "user-1",
            "email": "owner@example.com",
        }
        _orig_init = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            _restore_tortoise_sdk_init(_orig_init)
            app.dependency_overrides.clear()


@pytest.fixture
def reg():
    """Registry graph handle (same temp DB via the patched __init__)."""
    sdk = TortoiseSDK(namespace="registry")
    return sdk._get_registry()


def _as_user(user_id: str, email: str):
    app.dependency_overrides[get_current_user] = lambda u=user_id, e=email: {
        "user_id": u,
        "email": e,
    }


def _seed_team(reg, team_id: str, tier: str = "team"):
    reg.query(
        "CREATE (t:Team {id:$id, name:$id, tier:$tier})",
        params={"id": team_id, "tier": tier},
    )


def _seed_membership(reg, team_id: str, user_id: str, role: str,
                     status: str = "active"):
    reg.query(
        "CREATE (m:Membership {user_id:$uid, team_id:$tid, role:$role, "
        "status:$status, created_at:'2026-08-01T00:00:00+00:00'})",
        params={"uid": user_id, "tid": team_id, "role": role, "status": status},
    )


def _active_roles(reg, team_id: str) -> dict:
    rows = reg.query(
        "MATCH (m:Membership {team_id:$tid}) WHERE m.status = 'active' "
        "RETURN m.user_id, m.role",
        params={"tid": team_id},
    ).result_set
    return {uid: role for uid, role in rows}


def _seed_team_with_owner(reg, team_id: str, tier: str = "team",
                          owner: str = "user-1"):
    _seed_team(reg, team_id, tier=tier)
    _seed_membership(reg, team_id, owner, "owner")


# ═══════════════════════════════════════════════════════════════════════════════
# E3 — POST /v1/invites
# ═══════════════════════════════════════════════════════════════════════════════


class TestInviteCreate:
    def test_free_tier_402(self, client, reg):
        """Revenue gate: invites require the Team tier."""
        _seed_team_with_owner(reg, "team-free", tier="free")
        r = client.post("/v1/invites",
                        json={"team_id": "team-free", "email": "bob@example.com"})
        assert r.status_code == 402
        assert "Team tier" in r.json()["detail"]

    def test_team_tier_returns_token_once(self, client, reg):
        """Token is returned in the create response and stored hash-only."""
        _seed_team_with_owner(reg, "team-t")
        r = client.post("/v1/invites",
                        json={"team_id": "team-t", "email": "bob@example.com",
                              "role": "admin"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "invited"
        assert body["role"] == "admin"
        assert body["token"]
        assert body["expires_at"]
        # Registry: Invitation node holds the hash (verify, never plaintext)
        rows = reg.query(
            "MATCH (i:Invitation {team_id:'team-t'}) RETURN i.email, i.role, "
            "i.token_hash, i.status",
        ).result_set
        assert len(rows) == 1
        email, role, token_hash, status = rows[0]
        assert email == "bob@example.com"
        assert role == "admin"
        assert status == "pending"
        assert verify_api_key(body["token"], token_hash)
        assert token_hash != body["token"]

    def test_default_role_is_member(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        r = client.post("/v1/invites",
                        json={"team_id": "team-t", "email": "bob@example.com"})
        assert r.status_code == 200, r.text
        assert r.json()["role"] == "member"

    def test_duplicate_pending_invitation_409(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        first = client.post("/v1/invites",
                            json={"team_id": "team-t", "email": "bob@example.com"})
        assert first.status_code == 200
        r = client.post("/v1/invites",
                        json={"team_id": "team-t", "email": "bob@example.com"})
        assert r.status_code == 409
        assert "already exists" in r.json()["detail"]

    def test_member_cannot_invite_403(self, client, reg):
        """RBAC: only owner/admin may invite (E8-style denial)."""
        _seed_team_with_owner(reg, "team-t")
        _seed_membership(reg, "team-t", "user-2", "member")
        _as_user("user-2", "member@example.com")
        r = client.post("/v1/invites",
                        json={"team_id": "team-t", "email": "bob@example.com"})
        assert r.status_code == 403
        assert "owner or admin" in r.json()["detail"]

    def test_admin_can_invite(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        _seed_membership(reg, "team-t", "user-2", "admin")
        _as_user("user-2", "admin@example.com")
        r = client.post("/v1/invites",
                        json={"team_id": "team-t", "email": "bob@example.com"})
        assert r.status_code == 200, r.text

    def test_invalid_email_422(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        r = client.post("/v1/invites",
                        json={"team_id": "team-t", "email": "not-an-email"})
        assert r.status_code == 422

    def test_invalid_role_422(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        r = client.post("/v1/invites",
                        json={"team_id": "team-t", "email": "bob@example.com",
                              "role": "superuser"})
        assert r.status_code == 422

    def test_unknown_team_404(self, client, reg):
        """404 fires only after RBAC passes — requires an orphaned membership
        (active membership whose Team node is gone)."""
        _seed_membership(reg, "team-ghost", "user-1", "owner")  # no Team node
        r = client.post("/v1/invites",
                        json={"team_id": "team-ghost", "email": "bob@example.com"})
        assert r.status_code == 404
        assert "Unknown team" in r.json()["detail"]

    def test_email_is_lowercased(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        r = client.post("/v1/invites",
                        json={"team_id": "team-t", "email": "Bob@Example.COM"})
        assert r.status_code == 200, r.text
        rows = reg.query(
            "MATCH (i:Invitation {team_id:'team-t'}) RETURN i.email",
        ).result_set
        assert rows[0][0] == "bob@example.com"


# ═══════════════════════════════════════════════════════════════════════════════
# E4 — POST /v1/invites/accept
# ═══════════════════════════════════════════════════════════════════════════════


class TestInviteAccept:
    def _invite(self, client, reg, team_id="team-t", email="bob@example.com",
                role="member"):
        _seed_team_with_owner(reg, team_id)
        r = client.post("/v1/invites",
                        json={"team_id": team_id, "email": email, "role": role})
        assert r.status_code == 200, r.text
        return r.json()["token"]

    def test_accept_creates_active_membership_with_invited_role(self, client, reg):
        """E4 role preservation — the #743 bug class: an 'admin' invite must
        land as an admin membership, not a degraded member."""
        token = self._invite(client, reg, role="admin")
        _as_user("user-bob", "bob@example.com")
        r = client.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 200, r.text
        assert r.json() == {"team_id": "team-t", "role": "admin"}
        assert _active_roles(reg, "team-t")["user-bob"] == "admin"

    def test_member_role_preserved(self, client, reg):
        token = self._invite(client, reg, role="member")
        _as_user("user-bob", "bob@example.com")
        r = client.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 200
        assert _active_roles(reg, "team-t")["user-bob"] == "member"

    def test_token_is_single_use(self, client, reg):
        token = self._invite(client, reg)
        _as_user("user-bob", "bob@example.com")
        first = client.post("/v1/invites/accept", json={"token": token})
        assert first.status_code == 200
        second = client.post("/v1/invites/accept", json={"token": token})
        assert second.status_code == 400
        assert "Invalid or expired" in second.json()["detail"]

    def test_invalid_token_400(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        _as_user("user-bob", "bob@example.com")
        r = client.post("/v1/invites/accept", json={"token": "not-a-token"})
        assert r.status_code == 400

    def test_missing_token_422(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        r = client.post("/v1/invites/accept", json={})
        assert r.status_code == 422

    def test_email_mismatch_403(self, client, reg):
        """The invitee must be the invited account — invite for bob, accept
        as alice → 403."""
        token = self._invite(client, reg, email="bob@example.com")
        _as_user("user-alice", "alice@example.com")
        r = client.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 403
        assert "does not match" in r.json()["detail"]

    def test_already_member_409(self, client, reg):
        token = self._invite(client, reg)
        _seed_membership(reg, "team-t", "user-bob", "member")
        _as_user("user-bob", "bob@example.com")
        r = client.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 409
        assert "Already a member" in r.json()["detail"]

    def test_expired_token_400(self, client, reg):
        """An invitation past expires_at is rejected even with a valid token."""
        from tortoise.auth import hash_api_key

        _seed_team_with_owner(reg, "team-t")
        token = "expired-token-123"
        reg.query(
            "CREATE (i:Invitation {id:'inv-exp', team_id:'team-t', "
            "email:'bob@example.com', role:'member', token_hash:$th, "
            "created_by:'user-1', created_at:$ca, "
            "expires_at:'2020-01-01T00:00:00+00:00', accepted_at:null, "
            "status:'pending'})",
            params={"th": hash_api_key(token),
                    "ca": datetime.now(timezone.utc).isoformat()},
        )
        _as_user("user-bob", "bob@example.com")
        r = client.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 400
        assert "expired" in r.json()["detail"]


# ═══════════════════════════════════════════════════════════════════════════════
# E4 hardening — POST /v1/invites/accept rate limits (#1134)
# ═══════════════════════════════════════════════════════════════════════════════


class TestInviteAcceptRateLimit:
    """OWASP per-token / per-IP / global caps on invites/accept (#1134).

    The fixture sets RATE_LIMIT_DISABLED=1 (module level); these tests opt
    back in per-test via monkeypatch.delenv and shrink the env-tunable
    thresholds so a handful of requests exhaust each dimension. Tokens are
    INVALID on purpose — the OWASP scenario is repeated FAILED binding
    checks, and the limiter must trip without invalidating anything.
    """

    @pytest.fixture(autouse=True)
    def _limiter_on(self, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)  # limiter ON
        import tortoise.hosted_api as ha_mod

        # Fresh bucket stores per test — TestClient always reports the same
        # host ("testclient"), so the per-IP bucket would bleed across tests.
        ha_mod._INVITE_ACCEPT_TOKEN_BUCKETS.clear()
        ha_mod._INVITE_ACCEPT_IP_BUCKETS.clear()
        ha_mod._INVITE_ACCEPT_GLOBAL_BUCKETS.clear()
        yield
        ha_mod._INVITE_ACCEPT_TOKEN_BUCKETS.clear()
        ha_mod._INVITE_ACCEPT_IP_BUCKETS.clear()
        ha_mod._INVITE_ACCEPT_GLOBAL_BUCKETS.clear()

    def _post(self, client, token="not-a-token"):
        return client.post("/v1/invites/accept", json={"token": token})

    def test_per_token_cap(self, client, monkeypatch):
        """5 attempts / 15 min per token (env-shrunk to 2): the same token
        exhausts its OWN bucket while the per-IP budget stays intact."""
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_TOKEN_LIMIT", "2")
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_TOKEN_WINDOW_S", "3600")
        _as_user("user-bob", "bob@example.com")
        for _ in range(2):
            r = self._post(client, token="same-token")
            assert r.status_code == 400, r.text  # failed binding, allowed
        r3 = self._post(client, token="same-token")
        assert r3.status_code == 429, r3.text
        body = r3.json()["detail"]
        assert body["error_code"] == "over_invite_accept_rate_limit"
        assert r3.headers.get("retry-after")  # RFC 7231 contract

    def test_per_ip_cap(self, client, monkeypatch):
        """20 / hour per IP (env-shrunk to 2): DIFFERENT tokens share the
        IP budget — a rotating-token farm cannot bypass the IP cap."""
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_IP_LIMIT", "2")
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_IP_WINDOW_S", "3600")
        _as_user("user-bob", "bob@example.com")
        for i in range(2):
            assert self._post(client, token=f"ip-token-{i}").status_code == 400
        r3 = self._post(client, token="ip-token-2")
        assert r3.status_code == 429, r3.text
        assert r3.json()["detail"]["error_code"] == \
            "over_invite_accept_rate_limit"

    def test_global_cap(self, client, monkeypatch):
        """200 / hour global (env-shrunk to 2): distinct tokens AND the
        per-token/IP budgets must NOT pre-empt the global dimension."""
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_GLOBAL_LIMIT", "2")
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_GLOBAL_WINDOW_S", "3600")
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_TOKEN_LIMIT", "100")
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_IP_LIMIT", "100")
        _as_user("user-bob", "bob@example.com")
        for i in range(2):
            assert self._post(client, token=f"global-token-{i}").status_code == 400
        r3 = self._post(client, token="global-token-2")
        assert r3.status_code == 429, r3.text
        assert r3.json()["detail"]["error_code"] == \
            "over_invite_accept_rate_limit"

    def test_dimensions_independent(self, client, monkeypatch):
        """Per-token exhaustion must NOT trip the per-IP or global budget:
        a token-typed 429 is dimension-specific (OWASP: throttle the
        binding candidate, not the network)."""
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_TOKEN_LIMIT", "1")
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_TOKEN_WINDOW_S", "3600")
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_IP_LIMIT", "100")
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_GLOBAL_LIMIT", "100")
        _as_user("user-bob", "bob@example.com")
        assert self._post(client, token="token-a").status_code == 400
        r = self._post(client, token="token-a")
        assert r.status_code == 429, r.text
        # a DIFFERENT token from the same client is still allowed
        assert self._post(client, token="token-b").status_code == 400

    def test_rate_limit_disabled_opt_out(self, client, monkeypatch):
        """RATE_LIMIT_DISABLED=1 (test seam) bypasses every dimension even
        with thresholds shrunk to 1."""
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_TOKEN_LIMIT", "1")
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_IP_LIMIT", "1")
        monkeypatch.setenv("TORTOISE_INVITE_ACCEPT_GLOBAL_LIMIT", "1")
        _as_user("user-bob", "bob@example.com")
        for _ in range(3):
            assert self._post(client, token="same-token").status_code == 400


# ═══════════════════════════════════════════════════════════════════════════════
# E8 — GET/DELETE/PATCH /v1/teams/{team_id}/members
# ═══════════════════════════════════════════════════════════════════════════════


class TestMembersRbac:
    def test_list_members_requires_owner_admin_403(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        _seed_membership(reg, "team-t", "user-2", "member")
        _as_user("user-2", "member@example.com")
        r = client.get("/v1/teams/team-t/members")
        assert r.status_code == 403
        assert "owner or admin" in r.json()["detail"]

    def test_list_members_shows_active_and_invited(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        _seed_membership(reg, "team-t", "user-2", "member")
        inv = client.post("/v1/invites",
                          json={"team_id": "team-t", "email": "bob@example.com"})
        assert inv.status_code == 200
        r = client.get("/v1/teams/team-t/members")
        assert r.status_code == 200, r.text
        by_user = {m["user_id"]: m for m in r.json()}
        assert by_user["user-1"]["role"] == "owner"
        assert by_user["user-1"]["status"] == "active"
        assert by_user["user-2"]["role"] == "member"
        assert by_user["user-2"]["status"] == "active"
        # Invited placeholder row surfaces with the invited email
        invited = [m for m in r.json() if m["status"] == "invited"]
        assert len(invited) == 1
        assert invited[0]["email"] == "bob@example.com"

    def test_remove_member_owner_409(self, client, reg):
        """Owner protection: the owner cannot be removed."""
        _seed_team_with_owner(reg, "team-t")
        _seed_membership(reg, "team-t", "user-2", "member")
        r = client.delete("/v1/teams/team-t/members/user-1")
        assert r.status_code == 409
        assert "Owner cannot be removed" in r.json()["detail"]

    def test_remove_member_404(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        r = client.delete("/v1/teams/team-t/members/user-ghost")
        assert r.status_code == 404

    def test_remove_member_sets_status_removed(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        _seed_membership(reg, "team-t", "user-2", "member")
        r = client.delete("/v1/teams/team-t/members/user-2")
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "removed"
        assert "user-2" not in _active_roles(reg, "team-t")

    def test_remove_member_requires_owner_admin_403(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        _seed_membership(reg, "team-t", "user-2", "member")
        _seed_membership(reg, "team-t", "user-3", "member")
        _as_user("user-3", "member3@example.com")
        r = client.delete("/v1/teams/team-t/members/user-2")
        assert r.status_code == 403

    def test_change_role_owner_409(self, client, reg):
        """Owner protection: the owner role cannot be changed."""
        _seed_team_with_owner(reg, "team-t")
        r = client.patch("/v1/teams/team-t/members/user-1",
                         json={"role": "member"})
        assert r.status_code == 409
        assert "Owner role cannot be changed" in r.json()["detail"]

    def test_change_role_invalid_role_422(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        _seed_membership(reg, "team-t", "user-2", "member")
        r = client.patch("/v1/teams/team-t/members/user-2",
                         json={"role": "superuser"})
        assert r.status_code == 422

    def test_change_role_member_to_admin(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        _seed_membership(reg, "team-t", "user-2", "member")
        r = client.patch("/v1/teams/team-t/members/user-2",
                         json={"role": "admin"})
        assert r.status_code == 200, r.text
        assert _active_roles(reg, "team-t")["user-2"] == "admin"

    def test_change_role_admin_to_member(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        _seed_membership(reg, "team-t", "user-2", "admin")
        r = client.patch("/v1/teams/team-t/members/user-2",
                         json={"role": "member"})
        assert r.status_code == 200, r.text
        assert _active_roles(reg, "team-t")["user-2"] == "member"

    def test_change_role_requires_owner_admin_403(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        _seed_membership(reg, "team-t", "user-2", "admin")
        _seed_membership(reg, "team-t", "user-3", "member")
        _as_user("user-3", "member3@example.com")
        r = client.patch("/v1/teams/team-t/members/user-2",
                         json={"role": "member"})
        assert r.status_code == 403

    def test_change_role_unknown_member_404(self, client, reg):
        _seed_team_with_owner(reg, "team-t")
        r = client.patch("/v1/teams/team-t/members/user-ghost",
                         json={"role": "member"})
        assert r.status_code == 404
