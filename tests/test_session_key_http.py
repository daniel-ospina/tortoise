"""HTTP-layer tests for POST /v1/session/key — E1 (#518 chicken-and-egg fix).

Issue #748 (P1): E1 had ZERO HTTP-layer coverage — test_session_key.py only
creates APIKey nodes via raw Cypher and checks schema fields. test_auth_flip.py
covers the Supabase control-plane path (FakeControlPlane); this file covers the
REGISTRY (selfhost) path — the default embedded mode — branch-for-branch from
the audit:

- 200 mint: bootstrap (24h expiry, cap-exempt, 3-active backstop) vs recovery
  (persistent, counts against max_api_keys)
- 403 no-membership / no membership in team
- 400 multi-team without team_id
- 429 bootstrap backstop (3 active) — expired keys don't count (#742)
- 402 recovery-at-cap with NO revocable other key
- recovery-at-cap auto-revokes the OLDEST OTHER key (#750.10 — never the
  caller's own key)
- 422 bad purpose
- mint → Bearer round-trip through get_current_team (registry lookup path)

Fixture mirrors tests/test_hosted_api.py (dependency override + temp
FalkorDBLite DB via TortoiseSDK.__init__ patch).
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import pytest
from fastapi.testclient import TestClient

from tortoise.auth import hash_api_key, verify_api_key  # noqa: F401
from tortoise.hosted_api import app, get_current_user
from tortoise.sdk import TortoiseSDK

# #1719 (Task 3): team_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs; non-UUID user_id literals are prod-impossible.
# Session-key api_keys.created_by mirrors the minting user's UUID (the
# cap/backstop logic compares it to the JWT subject); anon/reg identity
# values would stay TEXT, but none appear here.
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"
_U2 = "9f2c1a40-0000-4a00-8000-000000000002"


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _patch_tortoise_sdk_init(db_path: str):
    """Make TortoiseSDK use a temp db_path when constructed without one."""
    import tortoise.hosted_api as ha_mod

    _orig_init = ha_mod.TortoiseSDK.__init__

    def _patched_init(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig_init(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched_init
    # Break the _make_sdk embedded fallback anchor (#1470): _FALLBACK_KEEPALIVE
    # is module-level and survives test files, so an anchored SDK bound to a
    # PREVIOUS test's temp DB leaks state into this test (the anchor's socket
    # dies when that tempdir is removed → redis.socket ConnectionError, or the
    # previous graph's rows appear in the "fresh" temp DB). Clear it so
    # _make_sdk re-binds to THIS test's temp DB.
    ha_mod._FALLBACK_KEEPALIVE.clear()
    return _orig_init


def _restore_tortoise_sdk_init(original_init):
    import tortoise.hosted_api as ha_mod

    ha_mod.TortoiseSDK.__init__ = original_init


@pytest.fixture
def client():
    """TestClient with get_current_user (session JWT) overridden + temp DB.

    All SDK instances inside hosted_api resolve against the same temp
    FalkorDBLite DB; the registry namespace (registry_control_plane graph) is
    where Membership/Team/APIKey nodes live for the selfhost mint path.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": _U1,
            "email": "owner@example.com",
        }
        _orig_init = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc
        finally:
            _restore_tortoise_sdk_init(_orig_init)
            app.dependency_overrides.clear()
            while _REG_SDKS:
                try:  # noqa: SIM105
                    _REG_SDKS.pop().close()
                except Exception:
                    pass


# ── Registry seeding helpers ─────────────────────────────────────────────────


@pytest.fixture
def reg():
    """Registry graph handle (same temp DB via the patched __init__).

    Holds the SDK in _REG_SDKS so close-on-GC (#1475) does not shut the
    shared temp server down before the test uses the handle (#1588).
    """
    sdk = TortoiseSDK(namespace="registry")
    _REG_SDKS.append(sdk)
    return sdk._get_registry()


# #1588: hold registry SDKs alive — the `reg` fixture returns
# _get_registry() but the SDK goes out of scope; with #1475 close-on-GC
# the server is shut down before the test uses the handle (redis.socket
# ConnectionError flake). Keep the SDK referenced for the test duration
# (same pattern as test_invites_http.py #1556).
_REG_SDKS: list = []


def _seed_team(reg, team_id: str, tier: str = "free"):
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


def _seed_api_key(reg, team_id: str, key_id: str, *, created_by: str,
                  created_via: str, created_at: str,
                  revoked_at: str | None = None,
                  expires_at: str | None = None):
    reg.query(
        "CREATE (k:APIKey {id:$id, team_id:$tid, key_hash:'h', key_prefix:'tt_x', "
        "created_by:$cb, created_at:$ca, revoked_at:$ra, expires_at:$ea, "
        "created_via:$cv})",
        params={"id": key_id, "tid": team_id, "cb": created_by, "ca": created_at,
                "ra": revoked_at, "ea": expires_at, "cv": created_via},
    )


def _count_active_keys(reg, team_id: str) -> int:
    rows = reg.query(
        "MATCH (k:APIKey {team_id:$tid}) WHERE k.revoked_at IS NULL RETURN count(k)",
        params={"tid": team_id},
    ).result_set
    return int(rows[0][0])


def _count_persistent_keys(reg, team_id: str) -> int:
    """Non-revoked keys that COUNT against max_api_keys (bootstrap-excluded
    — mirrors the mint's count predicate)."""
    rows = reg.query(
        "MATCH (k:APIKey {team_id:$tid}) WHERE k.revoked_at IS NULL "
        "AND (k.created_via IS NULL OR k.created_via <> 'bootstrap') RETURN count(k)",
        params={"tid": team_id},
    ).result_set
    return int(rows[0][0])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def _hours_ago(n: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=n)).isoformat()  # noqa: UP017


def _hours_ahead(n: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=n)).isoformat()  # noqa: UP017


# ═══════════════════════════════════════════════════════════════════════════════
# E1 — POST /v1/session/key (registry/selfhost path)
# ═══════════════════════════════════════════════════════════════════════════════


class TestBootstrapMint:
    """Bootstrap purpose: 24h ephemeral, cap-exempt, 3-active backstop."""

    def test_happy_path_returns_key_and_stores_hash(self, client, reg):
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        r = client.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["purpose"] == "bootstrap"
        assert body["team_id"] == "team-a"
        assert body["expires_at"] is not None
        assert body["key"].startswith("tt_")
        assert body["key_prefix"] == body["key"][:10]

        # Registry stores hash only — never the plaintext key
        rows = reg.query(
            "MATCH (k:APIKey {team_id:'team-a'}) RETURN k.key_hash, "
            "k.key_prefix, k.created_via, k.created_by",
        ).result_set
        assert len(rows) == 1
        stored_hash, prefix, created_via, created_by = rows[0]
        assert verify_api_key(body["key"], stored_hash)
        assert prefix == body["key"][:10]
        assert created_via == "bootstrap"
        assert created_by == _U1

    def test_bootstrap_expiry_is_24h(self, client, reg):
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        r = client.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 200
        expires = datetime.fromisoformat(r.json()["expires_at"])
        delta = expires - datetime.now(timezone.utc)  # noqa: UP017
        # 24h ± small clock skew between the mint's `now` and ours
        assert timedelta(hours=23, minutes=55) < delta <= timedelta(hours=24, minutes=1)

    def test_default_purpose_is_bootstrap(self, client, reg):
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        r = client.post("/v1/session/key", json={})
        assert r.status_code == 200, r.text
        assert r.json()["purpose"] == "bootstrap"
        assert r.json()["expires_at"] is not None

    def test_three_active_bootstrap_429(self, client, reg):
        """3-active backstop: the 4th bootstrap mint is rejected."""
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        for i in range(3):
            _seed_api_key(reg, "team-a", f"boot-{i}", created_by=_U1,
                          created_via="bootstrap",
                          created_at=_hours_ago(1), expires_at=_hours_ahead(1))
        r = client.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 429
        assert "session keys" in r.json()["detail"]

    def test_expired_bootstrap_keys_do_not_count(self, client, reg):
        """#742: expired bootstrap keys neither authenticate nor count
        against the 3-active backstop."""
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        for i in range(3):
            _seed_api_key(reg, "team-a", f"boot-exp-{i}", created_by=_U1,
                          created_via="bootstrap",
                          created_at=_hours_ago(48), expires_at=_hours_ago(1))
        r = client.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 200, r.text  # expired → cap slot free

    def test_bootstrap_is_cap_exempt_for_recovery_cap(self, client, reg):
        """R13: bootstrap keys do NOT count against max_api_keys — a recovery
        mint still succeeds with 2 active bootstrap keys on free tier."""
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        for i in range(2):  # free tier max_api_keys == 2
            _seed_api_key(reg, "team-a", f"boot-{i}", created_by=_U1,
                          created_via="bootstrap",
                          created_at=_hours_ago(1), expires_at=_hours_ahead(1))
        r = client.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200, r.text
        assert r.json()["expires_at"] is None


class TestRecoveryMint:
    """Recovery purpose: persistent, revocable, capped at max_api_keys."""

    def test_recovery_key_is_persistent(self, client, reg):
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        r = client.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200, r.text
        assert r.json()["expires_at"] is None
        rows = reg.query(
            "MATCH (k:APIKey {team_id:'team-a'}) RETURN k.created_via",
        ).result_set
        assert rows[0][0] == "recovery"

    def test_at_cap_auto_revokes_oldest_other(self, client, reg):
        """Free tier max_api_keys=2: minting a 3rd recovery key revokes the
        OLDEST OTHER user's key (#750.10 — never the caller's own)."""
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        _seed_api_key(reg, "team-a", "other-old", created_by=_U2,
                      created_via="recovery", created_at=_hours_ago(10))
        _seed_api_key(reg, "team-a", "other-new", created_by=_U2,
                      created_via="recovery", created_at=_hours_ago(1))
        assert _count_active_keys(reg, "team-a") == 2
        r = client.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200, r.text
        rows = reg.query(
            "MATCH (k:APIKey) WHERE k.id IN ['other-old','other-new'] "
            "RETURN k.id, k.revoked_at",
        ).result_set
        by_id = {rid: revoked for rid, revoked in rows}
        assert by_id["other-old"] is not None   # oldest other revoked
        assert by_id["other-new"] is None       # newest other survives
        assert _count_active_keys(reg, "team-a") == 2  # revoke + mint = 2

    def test_at_cap_keeps_own_keys_and_402s_when_nothing_else_to_revoke(
            self, client, reg):
        """#750.10 + #1828 fail-closed: recovery never dead-ends by killing
        the user's own PERSISTENT key — when ALL active cap-counting keys are
        the caller's own recovery keys AND there is no own bootstrap key to
        rotate (#1828), the mint 402s."""
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        _seed_api_key(reg, "team-a", "own-1", created_by=_U1,
                      created_via="recovery", created_at=_hours_ago(10))
        _seed_api_key(reg, "team-a", "own-2", created_by=_U1,
                      created_via="recovery", created_at=_hours_ago(1))
        r = client.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 402
        assert "Key limit reached" in r.json()["detail"]
        # Own keys untouched
        rows = reg.query(
            "MATCH (k:APIKey) WHERE k.id IN ['own-1','own-2'] RETURN k.revoked_at",
        ).result_set
        assert all(revoked is None for (revoked,) in rows)

    def test_at_cap_rotates_own_oldest_bootstrap_key(self, client, reg):
        """#1828 + review P2-1: at max_api_keys with only OWN keys, the
        recovery fallback rotates the user's own OLDEST bootstrap key (24h
        ephemeral — safe to rotate; EXPIRED ones included, review P3) then
        RE-CHECKS the persistent count — a rotated modern bootstrap was never
        in the count, so the mint fails CLOSED (402) instead of minting cap+1
        persistent keys (the old overshoot grew persistent keys unboundedly
        per login). Persistent user-minted keys stay untouched (#750.10); the
        rotated ephemeral frees bootstrap headroom for the next login."""
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        # 2 own PERSISTENT recovery keys fill the free-tier cap (2)...
        _seed_api_key(reg, "team-a", "own-rec-1", created_by=_U1,
                      created_via="recovery", created_at=_hours_ago(10))
        _seed_api_key(reg, "team-a", "own-rec-2", created_by=_U1,
                      created_via="recovery", created_at=_hours_ago(1))
        # ...and 2 own 24h bootstrap keys are rotatable (oldest first; the
        # OLDEST is already EXPIRED — P3: expiry is no longer a barrier).
        _seed_api_key(reg, "team-a", "own-boot-1", created_by=_U1,
                      created_via="bootstrap", created_at=_hours_ago(8),
                      expires_at=_hours_ago(1))
        _seed_api_key(reg, "team-a", "own-boot-2", created_by=_U1,
                      created_via="bootstrap", created_at=_hours_ago(2),
                      expires_at=_hours_ahead(1))
        r = client.post("/v1/session/key", json={"purpose": "recovery"})
        # P2-1: fail-closed — the rotation freed NO persistent slot (modern
        # bootstraps never count), so the re-check 402s (no cap+1 overshoot).
        assert r.status_code == 402, r.text
        assert "Key limit reached" in r.json()["detail"]
        rows = reg.query(
            "MATCH (k:APIKey) WHERE k.id IN ['own-boot-1','own-boot-2'] "
            "RETURN k.id, k.revoked_at",
        ).result_set
        by_id = {rid: revoked for rid, revoked in rows}
        assert by_id["own-boot-1"] is not None  # oldest own bootstrap rotated
        assert by_id["own-boot-2"] is None      # newest own bootstrap survives
        # Persistent keys untouched (#750.10) + count never exceeds the cap
        rows = reg.query(
            "MATCH (k:APIKey) WHERE k.id IN ['own-rec-1','own-rec-2'] "
            "RETURN k.revoked_at",
        ).result_set
        assert all(revoked is None for (revoked,) in rows)
        assert _count_persistent_keys(reg, "team-a") <= 2

    def test_at_cap_rotates_legacy_unowned_key_when_it_frees_a_slot(
            self, client, reg):
        """#1828 review P3-4 + P2-1: a LEGACY team-scoped unowned key
        (created_by IS NULL — a pre-created_by session credential by
        construction) COUNTS against max_api_keys, so rotating it frees a
        REAL slot: the re-check passes and the mint lands at exactly the cap
        (never cap+1). Legacy is preferred over own modern bootstraps."""
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        # cap=2: one own persistent key + one LEGACY unowned key (counted) —
        # no OTHER-user key to auto-revoke (#750.10), so the fallback runs.
        _seed_api_key(reg, "team-a", "own-rec-1", created_by=_U1,
                      created_via="recovery", created_at=_hours_ago(10))
        _seed_api_key(reg, "team-a", "legacy-1", created_by=None,
                      created_via=None, created_at=_hours_ago(6))
        # a newer own bootstrap exists — legacy must win (frees a slot)
        _seed_api_key(reg, "team-a", "own-boot-1", created_by=_U1,
                      created_via="bootstrap", created_at=_hours_ago(2),
                      expires_at=_hours_ahead(1))
        assert _count_persistent_keys(reg, "team-a") == 2
        r = client.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200, r.text
        assert r.json()["expires_at"] is None  # recovery mint, not bootstrap
        assert r.json()["rotated"] is True     # rotation signal → UI banner
        rows = reg.query(
            "MATCH (k:APIKey) WHERE k.id IN ['legacy-1','own-boot-1','own-rec-1'] "
            "RETURN k.id, k.revoked_at",
        ).result_set
        by_id = {rid: revoked for rid, revoked in rows}
        assert by_id["legacy-1"] is not None   # legacy rotated (frees a slot)
        assert by_id["own-boot-1"] is None     # own bootstrap survives
        assert by_id["own-rec-1"] is None      # own persistent untouched
        assert _count_persistent_keys(reg, "team-a") <= 2  # revoke+mint = cap


class TestMintGuards:
    """Membership / team_id / purpose validation."""

    def test_no_membership_403(self, client, reg):
        r = client.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 403
        assert "No team membership" in r.json()["detail"]

    def test_no_membership_in_requested_team_403(self, client, reg):
        """Multi-membership + team_id pointing at a team the user is NOT in."""
        _seed_team(reg, "team-a")
        _seed_team(reg, "team-b")
        _seed_team(reg, "team-x")
        _seed_membership(reg, "team-a", _U1, "owner")
        _seed_membership(reg, "team-b", _U1, "member")
        r = client.post("/v1/session/key",
                        json={"purpose": "bootstrap", "team_id": "team-x"})
        assert r.status_code == 403
        assert "No membership in team" in r.json()["detail"]

    def test_multi_team_requires_team_id_400(self, client, reg):
        _seed_team(reg, "team-a")
        _seed_team(reg, "team-b")
        _seed_membership(reg, "team-a", _U1, "owner")
        _seed_membership(reg, "team-b", _U1, "member")
        r = client.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 400
        assert "team_id required" in r.json()["detail"]

    def test_multi_team_with_team_id_ok(self, client, reg):
        _seed_team(reg, "team-a")
        _seed_team(reg, "team-b")
        _seed_membership(reg, "team-a", _U1, "owner")
        _seed_membership(reg, "team-b", _U1, "member")
        r = client.post("/v1/session/key",
                        json={"purpose": "bootstrap", "team_id": "team-b"})
        assert r.status_code == 200, r.text
        assert r.json()["team_id"] == "team-b"

    def test_bad_purpose_422(self, client, reg):
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        r = client.post("/v1/session/key", json={"purpose": "enterprise"})
        assert r.status_code == 422
        assert "purpose" in r.json()["detail"]


class TestMintedKeyRoundTrip:
    """Mint → Bearer auth on a real endpoint (registry lookup path)."""

    def test_minted_bootstrap_key_authenticates_rest(self, client, reg):
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        r = client.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 200
        key = r.json()["key"]
        # Same app, real get_current_team (NOT overridden): the minted key
        # resolves via key_prefix + verify_api_key against the registry.
        r2 = client.get("/v1/team/keys", headers={"Authorization": f"Bearer {key}"})
        assert r2.status_code == 200, r2.text
        keys = r2.json()["keys"]
        assert any(k["key_prefix"] == key[:10] for k in keys)

    def test_minted_recovery_key_revoked_rejects(self, client, reg):
        _seed_team(reg, "team-a")
        _seed_membership(reg, "team-a", _U1, "owner")
        r = client.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200
        key = r.json()["key"]
        # Revoke via registry, then the same key must 401
        reg.query(
            "MATCH (k:APIKey {key_prefix:$kp}) SET k.revoked_at = $now",
            params={"kp": key[:10], "now": _now_iso()},
        )
        r2 = client.get("/v1/team/keys", headers={"Authorization": f"Bearer {key}"})
        assert r2.status_code == 401

    def test_unauthenticated_mint_401(self, client):
        """No session override — the real get_current_user rejects the mint."""
        app.dependency_overrides.clear()
        r = client.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 401
