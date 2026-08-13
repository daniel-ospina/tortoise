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


class TestSignupIpRateLimit:
    """#1081: agent-signup per-IP limiter (2/24h, env-tunable, OWN store).

    The shared register bucket (3/hr) is untouched (locked by
    test_shared_ip_bucket_3_per_hour); this store limits the agent path
    only. All three tests delenv RATE_LIMIT_DISABLED (set at import in
    test_writer_inventory.py:31 — without the delenv they'd test the mask,
    not the limiter).
    """

    def test_signup_ip_limit_2_per_24h(self, client, monkeypatch):
        # delenv pattern: test_writer_inventory.py:31 sets RATE_LIMIT_DISABLED=1
        # at import; the signup limiter must actually be ON for this test.
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        for _ in range(2):
            r = client.post("/v1/agent/signup", json={})
            assert r.status_code == 200, r.text
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 429, r.text
        # P2-FIX-5: computed Retry-After (sliding window) — integer <= 86400,
        # NOT flat "86400" (remaining = oldest + window - now, so once >=1s
        # elapses it is 86399).
        assert int(r.headers.get("retry-after")) <= 86400
        assert r.json()["detail"]["error_code"] == "over_signup_ip_rate_limit"

    def test_signup_ip_limit_configurable_via_env(self, client, monkeypatch):
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setenv("TORTOISE_SIGNUP_IP_LIMIT", "1")
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 200, r.text
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 429, r.text

    def test_signup_ip_limit_not_bypassable_via_client_identity(self, client, monkeypatch):
        # REWRITTEN from dead-per-identity semantics (#741): the IP is the key.
        # Rotating client identities must NOT dodge the per-IP limit.
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        for _ in range(2):
            r = client.post("/v1/agent/signup",
                            json={"identity": f"anon-{uuid.uuid4().hex[:12]}"})
            assert r.status_code == 200, r.text
        r = client.post("/v1/agent/signup", json={"identity": "anon-fresh"})
        assert r.status_code == 429, r.text


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
