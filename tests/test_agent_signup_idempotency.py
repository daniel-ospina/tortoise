"""#1709 — server-side signup idempotency + keyless recovery (approach C).

Server-issued 256-bit ``st_<64hex>`` signup token minted at first signup
(hash-only at rest). Re-presenting the token IS the dedupe check AND the
recovery credential:
· no-token → always mints (2/24h per-IP limiter unchanged);
· valid token → keyless recovery on the SAME team (NEW minted key);
· bad token → uniform 422 invalid_signup_token (identical body for malformed /
  unknown / revoked / soft-deleted-team — no existence oracle);
· valid token + suspended team → 403 SUSPENDED (possession-authenticated).

#741(a) is preserved literally: the client identity and x-device-id remain
ignored — tests/test_agent_signup.py is UNTOUCHED (byte-identical).

This file holds the NEW #1709 tests; the #741(a) suite stays locked in
test_agent_signup.py.
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
from tortoise.auth import lookup_hash as _lookup_hash
from tortoise.hosted_api import app


def _wait_for(predicate, timeout_s: float = 5.0):
    """Poll for a fire-and-forget side effect (the R8 feed runs via
    asyncio.to_thread on the TestClient's background loop)."""
    import time as _time
    deadline = _time.time() + timeout_s
    while not predicate():
        if _time.time() > deadline:
            return
        _time.sleep(0.01)


def _mint(client, **extra):
    r = client.post("/v1/agent/signup", json=extra)
    assert r.status_code == 200, r.text
    return r.json()


def _st_token() -> str:
    return "st_" + uuid.uuid4().hex


# #1719 (Task 3): Membership.user_id is a uuid column — a non-UUID claim
# literal would 22P02 (HTTP 400) under the fake's fidelity check / PostgREST.
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class TestMintIssuesToken:
    """The mint response gains an additive signup_token (backward-compatible
    — the existing #741(a) suite asserts only the pre-existing fields)."""

    def test_mint_returns_st_token(self, client):
        data = _mint(client)
        tok = data.get("signup_token")
        assert isinstance(tok, str) and tok.startswith("st_")
        assert len(tok) == 3 + 64  # 'st_' + 256-bit hex
        assert all(c in "0123456789abcdef" for c in tok[3:])

    def test_mint_writes_signuptoken_node_registry(self, client):
        """Registry lane: a SignupToken node is created for the minted team
        (hash-only — the node must NOT store the plaintext)."""
        data = _mint(client)
        sdk = ha_mod._make_sdk(namespace="registry")
        rows = sdk._get_registry().query(
            "MATCH (s:SignupToken {team_id:$tid}) RETURN s.token_hash, properties(s)",
            params={"tid": data["team_id"]},
        ).result_set
        assert rows, f"no SignupToken node for {data['team_id']}"
        assert rows[0][0] != data["signup_token"]  # hash-only at rest
        assert rows[0][0]  # a hash is stored

    def test_mint_writes_apikey_props_registry(self, client):
        """Registry lane: the minted APIKey node carries created_via +
        expires_at (NULL-as-never) — #1708 list parity, owned here."""
        data = _mint(client)
        sdk = ha_mod._make_sdk(namespace="registry")
        row = sdk._get_registry().query(
            "MATCH (k:APIKey {team_id:$tid}) WHERE k.revoked_at IS NULL "
            "RETURN k.created_via, k.expires_at",
            params={"tid": data["team_id"]},
        ).result_set[0]
        assert row[0] == "provisioned"
        assert row[1] is None


class TestSequentialIdempotency:
    """Token twice → 1 team, key rotated (recovery mint), 0 new teams."""

    def test_resignup_with_token_same_team_new_key(self, client):
        first = _mint(client)
        team_id, token = first["team_id"], first["signup_token"]

        r = client.post("/v1/agent/signup",
                        json={"signup_token": token})
        assert r.status_code == 200, r.text
        second = r.json()

        # same team, NEW key, no identity echo (recovery is not a mint)
        assert second["team_id"] == team_id
        assert second["key"] != first["key"]
        assert second["key"].startswith("tt_")
        assert "identity" not in second
        assert "signup_token" not in second  # rotation rejected — no re-issue

        # the recovered key authenticates on the same team
        r2 = client.get("/v1/team", headers={"Authorization": f"Bearer {second['key']}"})
        assert r2.status_code == 200, r2.text
        assert r2.json()["team_id"] == team_id

        # exactly 1 team for that team_id
        sdk = ha_mod._make_sdk(namespace="registry")
        rows = sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) RETURN count(t)", params={"id": team_id}
        ).result_set
        assert rows[0][0] == 1

    def test_recover_endpoint_happy_path(self, client):
        """POST /v1/agent/recover: config-lost-with-token → new key, same team."""
        first = _mint(client)
        team_id, token = first["team_id"], first["signup_token"]
        r = client.post("/v1/agent/recover", json={"signup_token": token})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["team_id"] == team_id
        assert data["key"].startswith("tt_")
        assert data["key"] != first["key"]
        assert data["team_name"] and data["graph_name"] and data["tier"] == "free"
        # key authenticates
        r2 = client.get("/v1/team", headers={"Authorization": f"Bearer {data['key']}"})
        assert r2.status_code == 200
        assert r2.json()["team_id"] == team_id

    def test_recover_missing_token_422(self, client):
        r = client.post("/v1/agent/recover", json={})
        assert r.status_code == 422
        assert r.json()["detail"]["error_code"] == "invalid_signup_token"

    def test_post_claim_recovery_same_team(self, client):
        """Post-claim re-signup with token → recovery on the SAME (claimed)
        team — no second team (scope E2E-6)."""
        first = _mint(client)
        team_id, token = first["team_id"], first["signup_token"]
        # claim emulation: attach a user to the membership
        sdk = ha_mod._make_sdk(namespace="registry")
        sdk._get_registry().query(
            f"MATCH (m:Membership {{team_id:$tid}}) SET m.user_id = '{_U1}'",
            params={"tid": team_id},
        )
        r = client.post("/v1/agent/signup", json={"signup_token": token})
        assert r.status_code == 200, r.text
        assert r.json()["team_id"] == team_id
        rows = sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) RETURN count(t)", params={"id": team_id}
        ).result_set
        assert rows[0][0] == 1


class TestUniform422:
    """Oracle-free contract: malformed / unknown / revoked / soft-deleted all
    produce the IDENTICAL 422 body (a deleted team is indistinguishable from
    never-existed)."""

    def _assert_uniform_422(self, r):
        assert r.status_code == 422, r.text
        return r.json()["detail"]

    def test_uniform_422_all_four_cases(self, client):
        malformed = client.post("/v1/agent/signup", json={"signup_token": "st_short"})
        unknown = client.post("/v1/agent/signup", json={"signup_token": _st_token()})

        # revoked: mint then revoke the token row
        data = _mint(client)
        sdk = ha_mod._make_sdk(namespace="registry")
        sdk._get_registry().query(
            "MATCH (s:SignupToken {team_id:$tid}) SET s.revoked_at = '2026-01-01T00:00:00Z'",
            params={"tid": data["team_id"]},
        )
        revoked = client.post("/v1/agent/signup",
                              json={"signup_token": data["signup_token"]})

        # deleted team: mint then soft-delete the team
        data2 = _mint(client)
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.deleted_at = '2026-01-01T00:00:00Z'",
            params={"id": data2["team_id"]},
        )
        deleted = client.post("/v1/agent/signup",
                              json={"signup_token": data2["signup_token"]})

        bodies = [self._assert_uniform_422(r) for r in (malformed, unknown, revoked, deleted)]
        assert bodies[0]["error_code"] == "invalid_signup_token"
        # identical body across all four — no existence signal
        assert bodies[0] == bodies[1] == bodies[2] == bodies[3], bodies

    def test_uniform_422_via_recover_endpoint(self, client):
        r = client.post("/v1/agent/recover", json={"signup_token": _st_token()})
        d = self._assert_uniform_422(r)
        assert d["error_code"] == "invalid_signup_token"

    def test_non_string_token_uniform_422_not_500(self, client):
        """#1709 fixer P2.1: a body like {"signup_token": 123} passes the
        `is not None` gate — the REGISTRY lane must format-gate it to the
        uniform 422 BEFORE any registry scan (a non-str token reaching the
        registry full-scan would key.encode() → AttributeError → 500)."""
        r = client.post("/v1/agent/signup", json={"signup_token": 123})
        d = self._assert_uniform_422(r)
        assert d["error_code"] == "invalid_signup_token"
        r2 = client.post("/v1/agent/recover", json={"signup_token": ["st_", "x"]})
        d2 = self._assert_uniform_422(r2)
        assert d2 == d  # identical body — no existence signal


class TestSuspendedTeam:
    def test_suspended_team_403_fail_closed(self, client):
        """Valid token + suspended team → 403 SUSPENDED (possession-authenticated;
        platform convention). The CLI fails closed (no fresh mint)."""
        data = _mint(client)
        sdk = ha_mod._make_sdk(namespace="registry")
        sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) SET t.suspended_at = '2026-01-01T00:00:00Z'",
            params={"id": data["team_id"]},
        )
        r = client.post("/v1/agent/signup",
                        json={"signup_token": data["signup_token"]})
        assert r.status_code == 403, r.text
        assert r.json()["detail"]["code"] == "SUSPENDED"
        assert r.json()["detail"]["appeal_url"]


class TestRecoveryRateLimiting:
    """The recovery surface has its OWN limiter (per-IP 5/24h + per-token
    10/h) shared by BOTH the token-present signup branch and /v1/agent/recover.
    The mint limiter (2/24h) bounds MINTING only."""

    def test_token_present_bypasses_mint_limiter(self, client, monkeypatch):
        """An IP at the 2/24h signup limit can still recover with a valid
        token — a token-present request is a recovery, never a mint."""
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        first = _mint(client)
        _mint(client)  # consume the 2/24h signup budget
        # 3rd no-token mint → 429
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 429, r.text
        assert r.json()["detail"]["error_code"] == "over_signup_ip_rate_limit"
        # token-present re-signup → 200 (bypasses the mint bucket)
        r2 = client.post("/v1/agent/signup",
                         json={"signup_token": first["signup_token"]})
        assert r2.status_code == 200, r2.text
        assert r2.json()["team_id"] == first["team_id"]

    def test_token_present_bound_by_recovery_ip_limiter(self, client, monkeypatch):
        """Compensating control: an IP over the recovery per-IP cap is 429'd
        on token-present signup too (shared bucket, identical key)."""
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setenv("TORTOISE_RECOVER_IP_LIMIT", "2")
        data = _mint(client)
        token = data["signup_token"]
        for _ in range(2):
            r = client.post("/v1/agent/signup", json={"signup_token": token})
            assert r.status_code == 200, r.text
        # 3rd token-present attempt (any token) → 429 over_recovery_ip_rate_limit
        r = client.post("/v1/agent/signup", json={"signup_token": _st_token()})
        assert r.status_code == 429, r.text
        assert r.json()["detail"]["error_code"] == "over_recovery_ip_rate_limit"
        # the recover endpoint shares the SAME bucket (identical IP key)
        r = client.post("/v1/agent/recover", json={"signup_token": token})
        assert r.status_code == 429, r.text
        assert r.json()["detail"]["error_code"] == "over_recovery_ip_rate_limit"
        # the mint limiter is untouched: a no-token mint still passes (1 used)
        r = client.post("/v1/agent/signup", json={})
        assert r.status_code == 200, r.text

    def test_recovery_token_attempt_cap(self, client, monkeypatch):
        """Per-token attempt cap (10/h default) — keyed on the token HASH."""
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setenv("TORTOISE_RECOVER_TOKEN_LIMIT", "1")
        data = _mint(client)
        token = data["signup_token"]
        r = client.post("/v1/agent/recover", json={"signup_token": token})
        assert r.status_code == 200, r.text
        r = client.post("/v1/agent/recover", json={"signup_token": token})
        assert r.status_code == 429, r.text
        assert r.json()["detail"]["error_code"] == "over_recovery_token_rate_limit"

    def test_recovery_feed_not_signup_feed(self, client, monkeypatch):
        """Success feed: token-present recovery fires record_recovery (NOT
        record_signup — ops metrics must not conflate recoveries with mints)."""
        recovered, minted = [], []
        monkeypatch.setattr("tortoise.abuse.record_recovery",
                            lambda ip, team_id=None, now=None: recovered.append(team_id))
        monkeypatch.setattr("tortoise.abuse.record_signup",
                            lambda ip, team_id=None, now=None: minted.append(team_id))
        data = _mint(client)
        # The mint fires record_signup via asyncio.to_thread on the
        # TestClient loop — its append can land AFTER this clear, which
        # would trip the recovery≠mint assertion below. Wait for the mint
        # feed to land, THEN zero it (deterministic race-free ordering).
        _wait_for(lambda: len(minted) == 1)
        minted.clear()  # the mint legitimately fires record_signup — zero it
        r = client.post("/v1/agent/signup",
                        json={"signup_token": data["signup_token"]})
        assert r.status_code == 200
        _wait_for(lambda: len(recovered) >= 1)
        assert data["team_id"] in recovered
        assert data["team_id"] not in minted  # recovery ≠ mint


class TestConcurrencyE2E:
    """REAL parallel dispatch via asyncio.gather of N client POSTs (scope §6
    — the _UniqueViolation seam at test_writer_inventory.py:468 is an error-
    mapping fake, NOT a concurrency mechanism). N stays below the per-token
    attempt cap so the burst does not trip the limiter mid-assertion."""

    @pytest.fixture(autouse=True)
    def _off_limits(self, monkeypatch):
        # concurrency asserts the RPC/fake/sdk serialization, not the limiter
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")

    async def _burst(self, ac, payloads):
        import asyncio


        async def _post(payload):
            return await ac.post("/v1/agent/signup", json=payload)

        return await asyncio.gather(*(_post(p) for p in payloads))

    def test_parallel_same_token_one_team_under_cap(self, client):
        import asyncio

        import httpx

        # mint first (sequential) to get the token
        data = _mint(client)
        token, team_id = data["signup_token"], data["team_id"]

        async def _run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as ac:
                results = await self._burst(ac, [{"signup_token": token}] * 4)
                return results

        results = asyncio.run(_run())
        assert all(r.status_code == 200 for r in results), [r.text for r in results]
        bodies = [r.json() for r in results]
        # all responses resolve to the SAME team (recovery never creates a team)
        assert all(b["team_id"] == team_id for b in bodies)
        # exactly 1 team node
        sdk = ha_mod._make_sdk(namespace="registry")
        teams = sdk._get_registry().query(
            "MATCH (t:Team {id:$id}) RETURN count(t)", params={"id": team_id}
        ).result_set
        assert teams[0][0] == 1
        # active non-bootstrap keys ≤ max_api_keys (2 on free) — the recovery
        # mint is serialized (Supabase FOR UPDATE / registry process lock)
        keys = sdk._get_registry().query(
            "MATCH (k:APIKey {team_id:$tid}) WHERE k.revoked_at IS NULL "
            "AND (k.created_via IS NULL OR k.created_via <> 'bootstrap') "
            "RETURN count(k)",
            params={"tid": team_id},
        ).result_set[0][0]
        assert keys <= 2, f"non-bootstrap active keys overshot cap: {keys}"
        # recovery keys are REAL (persisted, minted through the api_keys
        # insert path — never fabricated): at least the surviving recovery
        # key authenticates (over-cap recoveries revoke the OLDEST key, so
        # some responses' keys may already be rotated away).
        auth_ok = [b for b in bodies
                   if client.get("/v1/team",
                                 headers={"Authorization": f"Bearer {b['key']}"}).status_code == 200]
        assert len(auth_ok) >= 1

    def test_parallel_no_token_mints_n_teams(self, client):
        """Parallel no-token mints → N teams (documented = today's behavior;
        the 2/24h IP limiter is the control for that path)."""
        import asyncio

        import httpx

        async def _run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as ac:
                results = await self._burst(ac, [{}] * 3)
                return results

        results = asyncio.run(_run())
        assert all(r.status_code == 200 for r in results)
        team_ids = {r.json()["team_id"] for r in results}
        assert len(team_ids) == 3  # N teams — no server-side dedupe pre-mint


class TestRegistryLegacyNode:
    """#1709 registry parity: an APIKey node WITHOUT expires_at must still
    verify (NULL-as-never-expires) — without the NULL clause every legacy
    selfhost key would stop authenticating."""

    def test_legacy_apikey_node_without_expires_at_verifies(self, client, monkeypatch):
        import tortoise.sdk as sdk_mod
        from tortoise.auth import hash_api_key

        sdk = sdk_mod.TortoiseSDK(namespace="test_registry_legacy_test")
        reg = sdk._get_registry()
        team_id = f"legacy-team-{uuid.uuid4().hex[:10]}"
        api_key = f"tt_{uuid.uuid4().hex}"
        reg.query(
            "CREATE (t:Team {id:$id, name:'legacy', tier:'free'})",
            params={"id": team_id},
        )
        reg.query(
            # NO created_via / expires_at props — the pre-#1709 shape
            "CREATE (k:APIKey {id:'legacy-k', team_id:$tid, key_hash:$kh, "
            "key_prefix:$kp, created_by:'anon-legacy'})",
            params={"tid": team_id, "kh": hash_api_key(api_key),
                    "kp": api_key[:10]},
        )
        out = sdk.apikey_verify(api_key)
        # C2 (#2111): apikey_verify now returns delegation_depth + scopes
        # (the MCP TeamResolutionMiddleware deleg gate reads them off the
        # same dict) — additive to the pre-C2 {team_id, key_id} contract;
        # a pre-#1709 legacy node resolves deleg NULL + scopes [] (the
        # full-access legacy class).
        assert out["team_id"] == team_id
        assert out["key_id"] == "legacy-k"
        assert out.get("delegation_depth") is None
        assert out.get("scopes") == []


class TestSupabaseLane:
    """#1709 Supabase lane (FakeControlPlane): the wrapper RPC + token row +
    resolve/recover RPCs; the concurrency property rides the fake's atomic
    recover (emulating the FOR UPDATE row lock)."""

    @pytest.fixture(autouse=True)
    def _supabase_env(self, monkeypatch):
        import tortoise.supabase_control as sc
        from tests.fake_control_plane import FakeControlPlane

        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.setenv("SUPABASE_URL", "https://agent1709.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc-agent-1709")
        monkeypatch.setenv("RATE_LIMIT_DISABLED", "1")
        fake = FakeControlPlane()
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        self.fake = fake

    def test_mint_uses_wrapper_and_lands_token_row(self, client):
        data = _mint(client)
        tok = data["signup_token"]
        assert tok.startswith("st_")
        wrapper = [c for c in self.fake.rpc_calls
                   if c[0] == "provision_team_with_token"]
        assert len(wrapper) == 1
        _, p = wrapper[0]
        assert p["p_signup_token_hash"] == _lookup_hash(tok)
        assert p["p_user_id"] is None and p["p_identity"].startswith("anon-")
        # plain provision_team is untouched for other callers
        assert not any(c[0] == "provision_team" for c in self.fake.rpc_calls)
        rows = [t for t in self.fake.tables.get("agent_signup_tokens", [])
                if t["team_id"] == data["team_id"]]
        assert len(rows) == 1 and rows[0]["token_hash"] == _lookup_hash(tok)

    def test_resignup_recovers_same_team_new_key(self, client):
        first = _mint(client)
        team_id, token = first["team_id"], first["signup_token"]
        r = client.post("/v1/agent/signup", json={"signup_token": token})
        assert r.status_code == 200, r.text
        second = r.json()
        assert second["team_id"] == team_id
        assert second["key"] != first["key"]
        # resolve + recover RPCs were used (no second mint)
        fns = [c[0] for c in self.fake.rpc_calls]
        assert "resolve_signup_token" in fns
        assert "recover_team_key" in fns
        assert fns.count("provision_team_with_token") == 1
        # exactly one team row
        teams = [t for t in self.fake.tables["teams"] if t["id"] == team_id]
        assert len(teams) == 1
        # the recovered key resolves via api_keys.lookup_hash
        r2 = client.get("/v1/team", headers={"Authorization": f"Bearer {second['key']}"})
        assert r2.status_code == 200 and r2.json()["team_id"] == team_id

    def test_recover_endpoint_and_uniform_422(self, client):
        data = _mint(client)
        token = data["signup_token"]
        r = client.post("/v1/agent/recover", json={"signup_token": token})
        assert r.status_code == 200
        assert r.json()["team_id"] == data["team_id"]
        # unknown token → uniform 422
        r = client.post("/v1/agent/recover", json={"signup_token": _st_token()})
        assert r.status_code == 422
        assert r.json()["detail"]["error_code"] == "invalid_signup_token"
        # malformed → identical 422
        r = client.post("/v1/agent/signup", json={"signup_token": "st_nope"})
        assert r.status_code == 422
        assert r.json()["detail"] == client.post(
            "/v1/agent/recover", json={"signup_token": _st_token()}).json()["detail"]

    def test_revoked_and_deleted_uniform_422(self, client):
        data = _mint(client)
        tok_hash = _lookup_hash(data["signup_token"])
        token_rows = [t for t in self.fake.tables.get("agent_signup_tokens", [])
                      if t["token_hash"] == tok_hash]
        assert token_rows
        token_rows[0]["revoked_at"] = "2026-01-01T00:00:00Z"
        r = client.post("/v1/agent/signup",
                        json={"signup_token": data["signup_token"]})
        assert r.status_code == 422
        assert r.json()["detail"]["error_code"] == "invalid_signup_token"

        # deleted team → uniform 422
        data2 = _mint(client)
        team = next(t for t in self.fake.tables["teams"]
                    if t["id"] == data2["team_id"])
        team["deleted_at"] = "2026-01-01T00:00:00Z"
        r = client.post("/v1/agent/signup",
                        json={"signup_token": data2["signup_token"]})
        assert r.status_code == 422
        assert r.json()["detail"]["error_code"] == "invalid_signup_token"

    def test_suspended_team_403(self, client):
        data = _mint(client)
        team = next(t for t in self.fake.tables["teams"]
                    if t["id"] == data["team_id"])
        team["suspended_at"] = "2026-01-01T00:00:00Z"
        r = client.post("/v1/agent/signup",
                        json={"signup_token": data["signup_token"]})
        assert r.status_code == 403
        assert r.json()["detail"]["code"] == "SUSPENDED"

    def test_concurrent_recovery_cap_cannot_overshoot(self, client):
        """Same-token parallel recoveries → 1 team and non-bootstrap active
        keys ≤ max_api_keys (2) — the fake's recover emulation is atomic
        (mirrors the FOR UPDATE row serialization)."""
        import asyncio

        import httpx

        data = _mint(client)
        token, team_id = data["signup_token"], data["team_id"]

        async def _run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as ac:
                async def _post(_):
                    return await ac.post("/v1/agent/signup",
                                         json={"signup_token": token})

                return await asyncio.gather(*(_post(i) for i in range(4)))

        results = asyncio.run(_run())
        assert all(r.status_code == 200 for r in results)
        assert all(r.json()["team_id"] == team_id for r in results)
        keys = [k for k in self.fake.tables.get("api_keys", [])
                if k["team_id"] == team_id and k.get("revoked_at") is None
                and k.get("created_via") != "bootstrap"]
        assert len(keys) <= 2, f"cap overshot: {len(keys)} active non-bootstrap keys"

    # ── Review P0/P1: transport-shape + error-mapping regressions ──────────
    # PostgREST return=minimal does NOT echo volatile SECURITY DEFINER RPC
    # results (repo precedent: metering_increment). The wrappers MUST read
    # back the authoritative row — these tests stub rpc to mimic the prod
    # transport (rpc commits server-side, echoes nothing) and assert the
    # wrapper still resolves.

    def _token_row(self, th: str, team_id: str) -> dict:
        return {"token_hash": th, "team_id": team_id, "created_at": None,
                "last_used_at": None, "revoked_at": None}

    def test_resolve_wrapper_reads_back_when_rpc_echoes_nothing(self):
        """P0 transport shape: cp.rpc returns None (return=minimal) for a
        VALID token → the old echo-parsing returned None → 422 in prod. The
        wrapper must resolve via the read-back (token row, revoked_at IS
        NULL)."""
        import tortoise.supabase_control as sc
        from tests.fake_control_plane import FakeControlPlane

        fake = FakeControlPlane()
        th = _lookup_hash(_st_token())
        fake.seed("agent_signup_tokens",
                  [self._token_row(th, "team-1709-t1")])
        # mimic prod return=minimal: the RPC committed server-side (row
        # present) but echoes nothing.
        fake.rpc = lambda fn, body=None: None  # type: ignore[method-assign]
        assert sc.resolve_signup_token(fake, th) == "team-1709-t1"
        # revoked → read-back honors revoked_at IS NULL → None (uniform 422)
        fake.tables["agent_signup_tokens"][0]["revoked_at"] = "2026-01-01T00:00:00Z"
        assert sc.resolve_signup_token(fake, th) is None

    def test_recover_wrapper_reads_back_when_rpc_echoes_nothing(self):
        """P0 transport shape: recover_team_key's RPC echoes nothing in prod
        → the wrapper read back the minted api_keys row (lookup_hash is the
        authoritative proof the mint committed) — never a fabricated key."""
        import tortoise.supabase_control as sc
        from tests.fake_control_plane import FakeControlPlane

        fake = FakeControlPlane()
        th = _lookup_hash(_st_token())
        lookup = "lu1709transport0000"
        fake.seed("agent_signup_tokens",
                  [self._token_row(th, "team-1709-t1")])
        fake.seed("teams", [{"id": "team-1709-t1", "name": "T1"}])
        # the RPC committed server-side (mint landed) but echoed nothing
        fake.seed("api_keys", [{
            "id": "key_team-1709-t1_lu1709transp", "team_id": "team-1709-t1",
            "lookup_hash": lookup, "key_prefix": "tt_1709",
            "created_via": "recovery", "created_by": "st_" + th[:12],
            "created_at": None, "expires_at": None, "revoked_at": None}])
        fake.rpc = lambda fn, body=None: None  # type: ignore[method-assign]
        out = sc.recover_team_key(fake, token_hash=th, team_id="team-1709-t1",
                                  lookup_hash=lookup, key_prefix="tt_1709",
                                  max_api_keys=2)
        assert out == "team-1709-t1"

    def test_recover_readback_missing_row_fails_closed(self):
        """The read-back is the mint's authority: an RPC that echoed nothing
        AND left no api_keys row (response lost / mint rolled back) raises
        RuntimeError (fail-closed → 500), never a fabricated success."""
        import tortoise.supabase_control as sc
        from tests.fake_control_plane import FakeControlPlane

        fake = FakeControlPlane()
        fake.rpc = lambda fn, body=None: None  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="no team_id"):
            sc.recover_team_key(fake, token_hash="th", team_id="team-1709-t1",
                                lookup_hash="lu-absent", key_prefix="tt_",
                                max_api_keys=2)

    def test_recover_maps_prod_shaped_rpc_error(self):
        """P1: the RPC error path. With the fix, cp.rpc embeds the PostgREST
        error body's message in the RuntimeError ("...: HTTP 400: recover_team_key:
        token not found or revoked" — the RAISE text, no raw SQL). The wrapper
        must map the semantic rejection to SignupTokenRecoveryError (uniform
        422), NOT propagate RuntimeError (500) — the mapping was dead when the
        seam carried only "HTTP 400"."""
        import tortoise.supabase_control as sc
        from tests.fake_control_plane import FakeControlPlane

        fake = FakeControlPlane()

        def _prod_rpc(fn, body=None):
            raise RuntimeError(
                "Supabase control-plane RPC failed (recover_team_key): "
                "HTTP 400: recover_team_key: token not found or revoked")

        fake.rpc = _prod_rpc  # type: ignore[method-assign]
        with pytest.raises(sc.SignupTokenRecoveryError) as ei:
            sc.recover_team_key(fake, token_hash="th", team_id="team-1",
                                lookup_hash="lu", key_prefix="tt_",
                                max_api_keys=2)
        assert ei.value.status == 422
        assert ei.value.code == "invalid_signup_token"
        # non-semantic control-plane failure still propagates RuntimeError
        def _prod_5xx(fn, body=None):
            raise RuntimeError(
                "Supabase control-plane RPC failed (recover_team_key): HTTP 500")

        fake.rpc = _prod_5xx  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="HTTP 500"):
            sc.recover_team_key(fake, token_hash="th", team_id="team-1",
                                lookup_hash="lu", key_prefix="tt_",
                                max_api_keys=2)
