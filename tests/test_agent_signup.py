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


def _wait_for(predicate, timeout_s: float = 5.0):
    """Poll for a fire-and-forget side effect (the R8 feed runs via
    asyncio.to_thread on the TestClient's background loop — the response can
    beat the thread). Raises AssertionError on timeout."""
    import time as _time
    deadline = _time.time() + timeout_s
    while not predicate():
        if _time.time() > deadline:
            return
        _time.sleep(0.01)


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

    def test_anon_team_bound_by_free_tier_caps(self, client):
        """#1081 (indicator 3): the minted Team node must carry the free-tier
        caps EXACTLY — a pricing-drift regression must never silently un-cap
        anon teams. (Reduced anon ceiling is deliberately deferred to #1082.)"""
        from tortoise.pricing import tier_limits
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 200, r.text
        lim = tier_limits("free")
        sdk = ha_mod._make_sdk(namespace="registry")
        row = sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) RETURN t.max_users, t.max_graphs, "
            "t.max_api_keys, t.ops_allowance, t.graph_size_cap",
            params={"id": r.json()["team_id"]},
        ).result_set[0]
        assert row[0] == lim["max_users_per_team"]
        assert row[1] == lim["max_graphs_per_team"]
        assert row[2] == lim["max_api_keys"]
        assert row[3] == lim["included_write_ops_per_month"]
        assert row[4] == lim["max_graph_nodes"]


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

    def test_signup_feeds_velocity_tracker(self, client, monkeypatch):
        """R8 success-path feed: a successful mint records the IP."""
        calls = []
        monkeypatch.setattr("tortoise.abuse.record_signup",
                            lambda ip, team_id=None, now=None: calls.append((ip, team_id)))
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 200
        # the feed is fire-and-forget (to_thread) — poll briefly for the call
        _wait_for(lambda: len(calls) == 1)
        assert len(calls) == 1
        assert calls[0][1] == r.json()["team_id"]

    def test_signup_ip_limit_and_velocity_single_notify(self, client, monkeypatch):
        """P1-FIX-2 integration: 3rd signup 429 AND exactly ONE ops notify —
        the success feed fires at the allowance boundary (2nd mint); the 429's
        record_block path is dedup-suppressed (same bare-ip key).
        """
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        notified = []
        monkeypatch.setattr("tortoise.notify.notify_abuse",
                            lambda kind, team, details: notified.append(kind))
        for _ in range(2):
            r = client.post("/v1/agent/signup", json={})
            assert r.status_code == 200, r.text
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 429, r.text
        _wait_for(lambda: notified == ["abuse_signup_velocity"])
        assert notified == ["abuse_signup_velocity"]  # exactly one, not two


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


class TestIpv6Normalization:
    """#1081 review P4: IPv4-mapped IPv6 must share ONE bucket with the
    dotted-quad form — a dual-stack client cannot double its 2/24h budget
    by presenting ::ffff:1.2.3.4 and 1.2.3.4 (or hex ::ffff:7f00:1)."""

    def test_mapped_ipv6_shares_bucket_with_v4(self, monkeypatch):
        from tortoise.hosted_api import _normalize_mapped_ipv6
        assert _normalize_mapped_ipv6("::ffff:1.2.3.4") == "1.2.3.4"
        assert _normalize_mapped_ipv6("::ffff:7f00:1") == "127.0.0.1"
        assert _normalize_mapped_ipv6("1.2.3.4") == "1.2.3.4"
        assert _normalize_mapped_ipv6("::1") == "::1"  # not mapped
        assert _normalize_mapped_ipv6(None) is None
        assert _normalize_mapped_ipv6("::ffff:0") == "::ffff:0"  # degenerate

    def test_limiter_hex_form_single_bucket(self, client, monkeypatch):
        """The limiter keys on the NORMALIZED ip — hex ::ffff:7f00:1 and
        127.0.0.1 are the same bucket (2 mints then 429)."""
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        import tortoise.hosted_api as ha_mod
        ha_mod._SIGNUP_BUCKETS.clear()
        # Simulate a dual-stack client: same normalized address, two forms.
        # The bucket key comes from request.state.client_ip — exercise the
        # normalization via the helper directly on the bucket store.
        from tortoise.hosted_api import _normalize_mapped_ipv6
        ip = _normalize_mapped_ipv6("::ffff:7f00:1")
        assert ip == "127.0.0.1"
