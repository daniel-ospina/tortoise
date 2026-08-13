"""Zero-email signup tests (issue #663) + #740 membership-status regression.

POST /v1/agent/signup — anonymous device mints a team + key, no email/
dashboard/Supabase account. Per-identity rate limit (3/hour).

#741: identity is ALWAYS server-side — client-supplied identity and
x-device-id are ignored (a client-chosen identity trivially bypasses the
per-identity rate limit).
#740: /internal/provision must write Membership with status:'active' so the
E6 /v1/teams listing (active-membership query) includes the provisioned team.
#770 (plan Task 2 — identity path): the server-side anon identity is the
anchor for the Supabase control-plane row. When the agent writer flips to
Supabase (plan Task 8/#765), provision_team stores it as team_memberships.identity
with user_id NULL (0009 chk_member_or_invite amendment + 0010 provision_team
p_identity variant) — the endpoint itself still writes the registry until
Task 8, and test_signup_identity_anchors_anon_membership locks the anchor
contract now.
"""
from __future__ import annotations

import os
import sys
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
from tortoise.hosted_api import app
from tortoise.session_auth import get_current_user

_INTERNAL_KEY = "test-internal-shared-secret-xyz"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class TestAgentSignup:
    def test_minted_key_authenticates_team_info(self, client):
        # Runs FIRST (fresh IP rate-limit bucket): the minted key must
        # authenticate AND /v1/team must not 500 (regression: team_info read
        # team["max_teams"] which no longer exists after D1 — pre-existing
        # 500 on every call, exposed by signup verification).
        r = client.post("/v1/agent/signup", json={"identity": f"anon-{uuid.uuid4().hex[:12]}"})
        assert r.status_code == 200, r.text
        key = r.json()["key"]
        r2 = client.get("/v1/team", headers={"Authorization": f"Bearer {key}"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["team_id"]

    def test_signup_returns_key(self, client):
        r = client.post("/v1/agent/signup", json={"identity": f"anon-{uuid.uuid4().hex[:12]}"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["key"].startswith("tt_")
        assert data["team_id"]
        assert data["tier"] == "free"

    def test_client_identity_ignored(self, client):
        # #741(a): client-supplied identity (body or x-device-id) is never
        # trusted — the server always generates anon-{uuid12}.
        ident = f"anon-{uuid.uuid4().hex[:12]}"
        r = client.post("/v1/agent/signup", headers={"X-Device-Id": ident}, json={})
        assert r.status_code == 200
        body_ident = f"anon-{uuid.uuid4().hex[:12]}"
        r2 = client.post("/v1/agent/signup", json={"identity": body_ident})
        assert r2.status_code == 200
        assert r.json()["identity"].startswith("anon-")
        assert r.json()["identity"] != ident
        assert r2.json()["identity"].startswith("anon-")
        assert r2.json()["identity"] != body_ident

    def test_signup_generated_identity_when_none(self, client):
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 200
        assert r.json()["identity"].startswith("anon-")

    def test_rate_limit_not_bypassable_via_client_identity(self, client):
        # #741(a): a client-chosen identity no longer keys the rate limit —
        # the server-side identity is fresh per request, so replaying one
        # client identity must NOT 429 (the old per-identity limit was
        # trivially bypassable and is dead).
        ident = f"anon-{uuid.uuid4().hex[:12]}"
        seen = set()
        for _ in range(4):
            r = client.post("/v1/agent/signup", json={"identity": ident})
            assert r.status_code == 200, r.text
            seen.add(r.json()["identity"])
        assert len(seen) == 4  # every request minted a distinct server identity

    def test_signup_identity_anchors_anon_membership(self, client):
        """#770 identity path: the server-side anon identity returned by signup
        is the anchor for the Supabase team_memberships row (NULL user_id +
        identity — 0009 chk_member_or_invite amendment; provision_team's
        p_identity variant, 0010). The registry Membership node's user_id must
        equal the response identity — the SAME value provision_team stores in
        team_memberships.identity when the agent writer flips to Supabase
        (plan Task 8/#765), so a later migration can reconcile rows 1:1."""
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["identity"].startswith("anon-")
        sdk = ha_mod._make_sdk(namespace="registry")
        rows = sdk._get_registry().query(
            "MATCH (m:Membership {team_id:$tid}) RETURN m.user_id",
            params={"tid": data["team_id"]},
        ).result_set
        assert rows, f"no membership row for {data['team_id']}"
        assert rows[0][0] == data["identity"], (
            f"membership anchor {rows[0][0]!r} != signup identity {data['identity']!r}"
        )


class TestProvisionMembershipStatus:
    """#740 — /internal/provision must write Membership status:'active' so
    the E6 /v1/teams listing (which filters on status='active') shows it."""

    def test_provisioned_team_lists_in_teams_e6(self, client, monkeypatch):
        # #880: _check_internal reads FASTAPI_INTERNAL_KEY lazily (was a
        # module-import constant). Set the env var directly — the old
        # ha_mod._INTERNAL_KEY attribute patch is dead post-fix.
        old_key = os.environ.get("FASTAPI_INTERNAL_KEY", "")
        monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
        try:
            r = client.post(
                "/internal/provision",
                headers={"Authorization": f"Bearer {_INTERNAL_KEY}"},
                json={
                    "team_id": f"prov{os.urandom(3).hex()}",
                    "team_name": "Provisioned Team",
                    "api_key_hash": "ab" * 64,
                    "created_by": "user-740-provision",
                },
            )
            assert r.status_code == 200, r.text
            team_id = r.json()["team_id"]

            # E6 (GET /v1/teams) is session-JWT gated — override the FastAPI
            # dependency with the provisioned user (the established pattern in
            # test_hosted_api.py; not timing-sensitive like monkeypatching the
            # underlying verify_session_jwt, which flakes under load).
            app.dependency_overrides[get_current_user] = lambda: {
                "user_id": "user-740-provision",
                "email": "prov@test.dev",
            }
            try:
                r2 = client.get("/v1/teams")
                assert r2.status_code == 200, r2.text
                teams = r2.json()
                assert any(t["team_id"] == team_id for t in teams), (
                    f"provisioned team {team_id} missing from E6 listing: {teams}"
                )
            finally:
                app.dependency_overrides.pop(get_current_user, None)
        finally:
            os.environ["FASTAPI_INTERNAL_KEY"] = old_key


class TestAgentSignupClaim:
    """#1082 PR1 — claim path on agent-signup teams (Supabase mode).

    Indicators 1 + 3: the SAME key authenticates pre/post claim and memories
    (data-plane graph) are intact — the claim touches only the control-plane
    membership/email rows, never the team_id or the graph namespace.
    """

    @pytest.fixture(autouse=True)
    def _supabase_claim_env(self, monkeypatch):
        from tests.fake_control_plane import FakeControlPlane
        import tortoise.supabase_control as sc
        import tortoise.hosted_api as ha_mod

        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://agentclaim.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-agent-claim")
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
        fake = FakeControlPlane()
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)

        async def _confirmed(request):
            return True

        monkeypatch.setattr(ha_mod, "_gotrue_email_confirmed", _confirmed)
        return fake

    def _patch_jwt(self, monkeypatch, user_id, email, providers):
        import tortoise.hosted_api as ha_mod

        async def _verify(request):
            return {"user_id": user_id, "email": email,
                    "app_metadata": {"providers": providers}}

        monkeypatch.setattr(ha_mod, "verify_session_jwt", _verify)

    def test_claim_same_key_authenticates_pre_post_and_memories_intact(
            self, client, monkeypatch):
        """Indicator 1 + 3: mint → point → claim → same key reads the SAME
        graph (team_id unchanged) — memories preserved."""
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 200, r.text
        key = r.json()["key"]
        team_id = r.json()["team_id"]

        # pre-claim: write a memory with the key
        r = client.post(
            "/v1/points",
            headers={"Authorization": f"Bearer {key}"},
            json={"content": "pre-claim memory", "kind": "statement"},
        )
        assert r.status_code == 200, r.text

        self._patch_jwt(monkeypatch, "user-claim-a", "verified@example.com",
                        ["github"])
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": key},
        )
        assert r.status_code == 200, r.text
        assert r.json()["team_id"] == team_id

        # post-claim: the same key reads the SAME graph (memories intact)
        r = client.get(
            "/v1/points",
            headers={"Authorization": f"Bearer {key}"},
        )
        assert r.status_code == 200, r.text
        contents = [p.get("content") for p in r.json().get("points", r.json())]
        assert "pre-claim memory" in contents, (
            f"pre-claim memory lost after claim: {contents}")

    def test_claim_email_overwrite_on_reg_team(self, client, monkeypatch):
        """reg- identity teams (email set at mint) get the email overwritten
        with the verified OAuth email on claim (P1-FIX-B, unconditional)."""
        r = client.post("/v1/agent/signup", json={})
        key = r.json()["key"]
        import tortoise.supabase_control as sc
        fake = sc.get_control_plane()
        # simulate a reg- mint: provision a SECOND team with email set
        import uuid as _uuid
        from tortoise.auth import lookup_hash as _lh, hash_api_key as _hash
        team_id = f"team-reg-{_uuid.uuid4().hex[:10]}"
        api_key = f"tt_{_uuid.uuid4().hex}"
        sc.provision_team(fake, **{
            "p_user_id": None, "p_identity": f"reg-{_uuid.uuid4().hex[:12]}",
            "p_team_id": team_id, "p_team_name": f"Reg {team_id}",
            "p_api_key": api_key, "p_key_hash": _hash(api_key),
            "p_lookup_hash": _lh(api_key), "p_graph_name": f"team_{team_id}",
            "p_email": "stale-reg@example.com",
            "p_key_prefix": api_key[:10], "p_tier": "free",
            "p_max_users": 1, "p_max_graphs": 1, "p_ops_allowance": 10000,
            "p_graph_size_cap": 10000,
        })
        self._patch_jwt(monkeypatch, "user-claim-a", "fresh-verified@example.com",
                        ["google"])
        r = client.post(
            "/v1/claim",
            headers={"Authorization": "Bearer abc.def.ghi"},
            json={"api_key": api_key},
        )
        assert r.status_code == 200, r.text
        team_row = next(t for t in fake.tables["teams"] if t["id"] == team_id)
        assert team_row["email"] == "fresh-verified@example.com", (
            f"reg- email must be overwritten A→B, got {team_row['email']}")
