"""Zero-email signup tests (issue #663) + #740 membership-status regression.

POST /v1/agent/signup — anonymous device mints a team + key, no email/
dashboard/Supabase account. Per-identity rate limit (3/hour).

#741: identity is ALWAYS server-side — client-supplied identity and
x-device-id are ignored (a client-chosen identity trivially bypasses the
per-identity rate limit).
#740: /internal/provision must write Membership with status:'active' so the
E6 /v1/teams listing (active-membership query) includes the provisioned team.
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


class TestProvisionMembershipStatus:
    """#740 — /internal/provision must write Membership status:'active' so
    the E6 /v1/teams listing (which filters on status='active') shows it."""

    def test_provisioned_team_lists_in_teams_e6(self, client, monkeypatch):
        old_key = ha_mod._INTERNAL_KEY
        ha_mod._INTERNAL_KEY = _INTERNAL_KEY
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

            # E6 (GET /v1/teams) is session-JWT gated — stub the JWKS verify
            # to the provisioned user (get_current_user resolves the module
            # global at call time).
            async def fake_verify(request):
                return {"user_id": "user-740-provision", "email": "prov@test.dev"}

            monkeypatch.setattr("tortoise.session_auth.verify_session_jwt", fake_verify)

            r2 = client.get("/v1/teams")
            assert r2.status_code == 200, r2.text
            teams = r2.json()
            assert any(t["team_id"] == team_id for t in teams), (
                f"provisioned team {team_id} missing from E6 listing: {teams}"
            )
        finally:
            ha_mod._INTERNAL_KEY = old_key
