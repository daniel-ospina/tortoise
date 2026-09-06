"""REST + MCP auth-flip tests (#767, plan Task 3) — Supabase-backed resolution.

Covers the flip end-to-end with the in-memory FakeControlPlane (zero network):
- REST get_current_team: valid key auths, revoked key 401, registry-only key
  401 (E2E-7-negative), Supabase error → 500 (fail-closed), header semantics.
- E2E-2 session-key round-trip: /v1/session/key mint → api_keys row
  (lookup_hash/created_via/expires_at) → resolves on REST → revoked rejected.
- MCP TeamResolutionMiddleware: resolves via the SAME shared function;
  registry-only/revoked → 401 JSON-RPC; Supabase error → 503.

Env-gated: SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY set → Supabase mode;
TORTOISE_CONTROL_PLANE=registry (or unset creds) keeps the registry path —
covered by the unchanged existing suites (test_hosted_api, test_mcp_http).
"""
from __future__ import annotations

import os
import tempfile
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.routing import Mount

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

from tortoise.auth import lookup_hash  # noqa: I001
from tortoise.hosted_api import app, get_current_team, get_current_user  # noqa: F401
from tortoise.mcp_server import create_http_app

from tests._http_fixtures import patched_tortoise_sdk
from tests.fake_control_plane import ErrorControlPlane, FakeControlPlane
from tests.test_supabase_control import (
    FREE_TEAM, TEAM_TIER_TEAM, TOKEN, _key_row, _membership_row,
)

# #1719 (Task 3): real UUIDs — JWT subjects + team_memberships.user_id are
# uuid in prod; non-UUID literals would 22P02 (the fake now enforces it).
_USER1 = "9f2c1a40-0000-4a00-8000-000000000001"
_USER2 = "9f2c1a40-0000-4a00-8000-000000000002"
_USER9 = "9f2c1a40-0000-4a00-8000-000000000009"

# Supabase mode token (deterministic via conftest pepper)


# ── Fixtures ────────────────────────────────────────────────────────────────

def _enable_supabase(monkeypatch, cp) -> FakeControlPlane:
    """Turn Supabase mode on and inject the fake control plane."""
    import tortoise.supabase_control as sc
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    return cp


@pytest.fixture
def supabase_fake() -> FakeControlPlane:
    """Fake control plane pre-seeded with one free team."""
    return FakeControlPlane({
        "api_keys": [],
        "team_memberships": [],
        "teams": [dict(FREE_TEAM)],
    })


@pytest.fixture
def rest_client(monkeypatch, supabase_fake):
    """TestClient over the real app with REAL get_current_team (no override)
    resolving against the fake Supabase control plane."""
    _enable_supabase(monkeypatch, supabase_fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "auth.db")
        # #2127: shared helper (tests._http_fixtures.patched_tortoise_sdk) —
        # patch __init__ → temp DB + #1950 TORTOISE_DB_PATH pin + close-then-
        # clear at enter; pop-pin → restore __init__ → deterministic anchor
        # close → clear overrides at exit (replaces the local
        # _patch_tortoise_sdk_init copy).
        with patched_tortoise_sdk(db_path), TestClient(app) as tc:
            yield tc, supabase_fake


# ── REST flip ───────────────────────────────────────────────────────────────

class TestRestAuthFlip:
    def test_valid_key_auths(self, rest_client):
        """A Supabase api_keys row (lookup_hash match) authenticates REST."""
        tc, fake = rest_client
        fake.seed("api_keys", [_key_row()])
        r = tc.get("/v1/team/keys",
                   headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200, r.text
        assert "keys" in r.json()

    def test_last_used_at_write_through(self, rest_client):
        """#685: successful auth writes api_keys.last_used_at (best-effort)."""
        tc, fake = rest_client
        fake.seed("api_keys", [_key_row()])
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 200
        assert fake.tables["api_keys"][0]["last_used_at"] is not None

    def test_revoked_key_401(self, rest_client):
        """P1-2: api_keys.revoked_at rejects on REST."""
        tc, fake = rest_client
        fake.seed("api_keys", [_key_row(revoked_at="2026-08-01T00:00:00Z")])
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 401

    def test_registry_only_key_401(self, rest_client):
        """E2E-7-negative: a key that exists only in the FalkorDB registry
        does NOT authenticate REST anymore."""
        tc, _ = rest_client  # fake has NO api_keys/team_memberships rows
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 401

    def test_missing_header_401(self, rest_client):
        tc, _ = rest_client
        assert tc.get("/v1/team/keys").status_code == 401

    def test_bad_scheme_401(self, rest_client):
        tc, _ = rest_client
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Basic {TOKEN}"})
        assert r.status_code == 401

    def test_wrong_prefix_401(self, rest_client):
        tc, _ = rest_client
        r = tc.get("/v1/team/keys", headers={"Authorization": "Bearer not-a-tt-key"})
        assert r.status_code == 401

    def test_supabase_down_500_fail_closed(self, monkeypatch, rest_client):
        """P1-3: a Supabase error is a 500 — never a 401, never a 200, never
        a registry fallback."""
        import tortoise.supabase_control as sc
        monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())
        tc, _ = rest_client
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 500
        assert r.json()["detail"] == "Auth error"


# ── E2E-2 session-key round-trip ────────────────────────────────────────────

class TestSessionKeyRoundTrip:
    """mint → api_keys row → resolve → revoked rejected (E2E-2 indicator)."""

    @pytest.fixture
    def authed_user(self):
        app.dependency_overrides[get_current_user] = lambda: {"user_id": _USER1}
        yield
        app.dependency_overrides.pop(get_current_user, None)

    def test_mint_resolves_then_revoked_rejected(self, rest_client, authed_user):
        tc, fake = rest_client
        fake.seed("team_memberships", [_membership_row(user_id=_USER1, team_id="team-free-001")])
        fake.tables["api_keys"] = []  # ensure clean

        # bootstrap mint → api_keys row with created_via + expires_at
        r = tc.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 200, r.text
        key = r.json()["key"]
        assert key.startswith("tt_")
        assert r.json()["purpose"] == "bootstrap"
        assert r.json()["expires_at"] is not None

        rows = fake.tables["api_keys"]
        assert len(rows) == 1
        assert rows[0]["created_via"] == "bootstrap"
        assert rows[0]["created_by"] == _USER1
        assert rows[0]["expires_at"] is not None  # 24h
        assert rows[0]["lookup_hash"] == lookup_hash(key)
        assert rows[0]["key_prefix"] == key[:10]

        # minted key RESOLVES on REST (api_keys.lookup_hash path)
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 200, r.text

        # revoked → rejected (authoritative)
        rows[0]["revoked_at"] = "2026-08-03T00:00:00Z"
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 401

    def test_recovery_mint_persistent_no_expiry(self, rest_client, authed_user):
        tc, fake = rest_client
        fake.seed("team_memberships", [_membership_row(user_id=_USER1, team_id="team-free-001")])
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200, r.text
        assert r.json()["expires_at"] is None
        assert fake.tables["api_keys"][0]["created_via"] == "recovery"

    def test_bootstrap_cap_three_active(self, rest_client, authed_user):
        tc, fake = rest_client
        fake.seed("team_memberships", [_membership_row(user_id=_USER1, team_id="team-free-001")])
        for i in range(3):
            fake.seed("api_keys", [_key_row(
                id=f"boot-{i}", created_via="bootstrap", created_by=_USER1,
                lookup_hash=f"hash-{i}")])
        r = tc.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 429

    def test_recovery_cap_auto_revokes_oldest_other(self, rest_client, authed_user):
        """Free tier max_api_keys=2: minting a 3rd recovery key auto-revokes
        the oldest OTHER user's key (#750.10 — never the user's own)."""
        tc, fake = rest_client
        fake.seed("team_memberships", [_membership_row(user_id=_USER1, team_id="team-free-001")])
        fake.seed("api_keys", [
            _key_row(id="other-old", created_via="recovery", created_by=_USER2,
                     lookup_hash="h1", created_at="2026-08-01T00:00:00Z"),
            _key_row(id="other-new", created_via="recovery", created_by=_USER2,
                     lookup_hash="h2", created_at="2026-08-02T00:00:00Z"),
        ])
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200, r.text
        by_id = {row["id"]: row for row in fake.tables["api_keys"]}
        assert by_id["other-old"]["revoked_at"] is not None  # oldest other revoked
        assert by_id["other-new"]["revoked_at"] is None
        new_rows = [row for row in fake.tables["api_keys"]
                    if row["id"] not in ("other-old", "other-new")]
        assert len(new_rows) == 1
        assert new_rows[0]["revoked_at"] is None  # minted key lands unrevoked
        assert new_rows[0]["created_via"] == "recovery"

    def test_recovery_cap_rotates_own_bootstrap_when_all_keys_own(
            self, rest_client, authed_user):
        """#1828 + review P2-1: at max_api_keys with only OWN keys and NO
        own recovery key to rotate (#1830 makes recovery the tier-2
        candidate — this test isolates the tier-3 bootstrap fallback by
        seeding PROVISIONED fillers), the recovery fallback rotates the
        user's own OLDEST bootstrap key (24h ephemeral — safe to rotate)
        then RE-CHECKS the persistent count — a rotated modern bootstrap was
        never in the count, so the mint fails CLOSED (402) instead of
        minting cap+1 persistent keys (the old overshoot grew persistent
        keys unboundedly per login). Persistent user-minted keys stay
        untouched (#750.10)."""
        tc, fake = rest_client
        fake.seed("team_memberships", [_membership_row(user_id=_USER1, team_id="team-free-001")])
        fake.seed("api_keys", [
            # deliberate user-created keys (never rotation candidates)
            _key_row(id="own-prov-1", created_via="provisioned", created_by=_USER1,
                     lookup_hash="h1", created_at="2026-08-01T00:00:00Z"),
            _key_row(id="own-prov-2", created_via="provisioned", created_by=_USER1,
                     lookup_hash="h2", created_at="2026-08-02T00:00:00Z"),
            _key_row(id="own-boot-1", created_via="bootstrap", created_by=_USER1,
                     lookup_hash="h3", created_at="2026-08-01T00:00:00Z"),
            _key_row(id="own-boot-2", created_via="bootstrap", created_by=_USER1,
                     lookup_hash="h4", created_at="2026-08-03T00:00:00Z"),
        ])
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        # P2-1: fail-closed — the rotation freed NO persistent slot (modern
        # bootstraps never count), so the re-check 402s (no cap+1 overshoot).
        assert r.status_code == 402, r.text
        assert "Key limit reached" in r.json()["detail"]
        by_id = {row["id"]: row for row in fake.tables["api_keys"]}
        assert by_id["own-boot-1"]["revoked_at"] is not None  # oldest own bootstrap rotated
        assert by_id["own-boot-2"]["revoked_at"] is None
        assert by_id["own-prov-1"]["revoked_at"] is None     # provisioned keys untouched
        assert by_id["own-prov-2"]["revoked_at"] is None
        # post-mint persistent count never exceeds the cap
        persistent = [r for r in fake.tables["api_keys"]
                      if r.get("revoked_at") is None
                      and r.get("created_via") != "bootstrap"]
        assert len(persistent) <= 2

    def test_recovery_cap_rotates_own_oldest_recovery_key(
            self, rest_client, authed_user):
        """#1830: at max_api_keys with only OWN persistent keys, the
        recovery fallback now rotates the user's own OLDEST recovery key
        (tier 2 — recovery keys are SYSTEM-MINTED fallback credentials, not
        deliberate user-created keys (those are created_via='provisioned'),
        so rotating one at cap is the escape-hatch semantics). A recovery key
        COUNTS against max_api_keys, so the rotation frees a REAL slot: the
        re-check passes and the mint lands at exactly the cap (never cap+1)
        with rotated=True. Own PROVISIONED keys are never rotation
        candidates (#750.10) and stay untouched."""
        tc, fake = rest_client
        fake.seed("team_memberships", [_membership_row(user_id=_USER1, team_id="team-free-001")])
        fake.seed("api_keys", [
            # cap=2: two own RECOVERY keys fill the cap (the #1830 deadlock)…
            _key_row(id="own-rec-1", created_via="recovery", created_by=_USER1,
                     lookup_hash="h1", created_at="2026-08-01T00:00:00Z"),
            _key_row(id="own-rec-2", created_via="recovery", created_by=_USER1,
                     lookup_hash="h2", created_at="2026-08-02T00:00:00Z"),
            # …plus an own PROVISIONED key (deliberate user key, already
            # revoked) — never a rotation candidate, stays untouched.
            _key_row(id="own-prov-1", created_via="provisioned", created_by=_USER1,
                     lookup_hash="h3", created_at="2026-08-01T12:00:00Z",
                     revoked_at="2026-08-03T00:00:00Z"),
        ])
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200, r.text
        assert r.json()["expires_at"] is None  # recovery mint, not bootstrap
        assert r.json()["rotated"] is True     # rotation signal → UI banner
        # #1854: the mint response names the rotated key for the UI banner
        # (own-rec-1 was rotated; it carries the shared seeded key_prefix)
        assert r.json()["rotated_key_prefix"] == "tt_unit_te"
        by_id = {row["id"]: row for row in fake.tables["api_keys"]}
        assert by_id["own-rec-1"]["revoked_at"] is not None  # oldest recovery rotated
        assert by_id["own-rec-2"]["revoked_at"] is None      # newest recovery survives
        # provisioned key untouched — its original revoke timestamp stands
        assert by_id["own-prov-1"]["revoked_at"] == "2026-08-03T00:00:00Z"
        # revoke(own-rec-1) + mint = exactly the cap, never cap+1
        persistent = [r for r in fake.tables["api_keys"]
                      if r.get("revoked_at") is None
                      and r.get("created_via") != "bootstrap"]
        assert len(persistent) <= 2
        new_rows = [r for r in fake.tables["api_keys"]
                    if r["id"] not in ("own-rec-1", "own-rec-2", "own-prov-1")]
        assert len(new_rows) == 1
        assert new_rows[0]["created_via"] == "recovery"

    def test_recovery_cap_rotates_never_used_own_recovery_over_recently_used(
            self, rest_client, authed_user):
        """#1854 (supabase lane): own_recovery rotation is last_used_at-
        aware — a recovery key that was NEVER used (last_used_at NULL) is
        rotated BEFORE a recently-used one, even when the never-used key was
        created LATER (pre-#1854 the OLDEST-created key won on every mint,
        killing a live persistent credential). The mint response names the
        rotated key (rotated_key_prefix)."""
        tc, fake = rest_client
        fake.seed("team_memberships",
                  [_membership_row(user_id=_USER1, team_id="team-free-001")])
        fake.seed(
            "api_keys",
            [
                # cap=2: two own RECOVERY keys fill the cap. own-rec-old is the
                # OLDEST-created but was USED recently (a live credential);
                # own-rec-new was created later but NEVER used (NULL).
                _key_row(
                    id="own-rec-old",
                    created_via="recovery",
                    created_by=_USER1,
                    lookup_hash="h1",
                    key_prefix="tt_oldrec1",
                    created_at="2026-08-01T00:00:00Z",
                    last_used_at="2026-08-05T00:00:00Z",
                ),
                _key_row(
                    id="own-rec-new",
                    created_via="recovery",
                    created_by=_USER1,
                    lookup_hash="h2",
                    key_prefix="tt_newrec1",
                    created_at="2026-08-04T00:00:00Z",
                    last_used_at=None,
                ),
            ],
        )
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200, r.text
        assert r.json()["rotated"] is True
        # the NEVER-USED key is rotated, not the oldest-created one
        assert r.json()["rotated_key_prefix"] == "tt_newrec1"
        by_id = {row["id"]: row for row in fake.tables["api_keys"]}
        assert by_id["own-rec-old"]["revoked_at"] is None      # recently-used survives
        assert by_id["own-rec-new"]["revoked_at"] is not None  # never-used rotated
        # revoke(own-rec-new) + mint = exactly the cap, never cap+1
        persistent = [r for r in fake.tables["api_keys"]
                      if r.get("revoked_at") is None
                      and r.get("created_via") != "bootstrap"]
        assert len(persistent) <= 2

    def test_recovery_cap_prefers_own_recovery_over_own_bootstrap(
            self, rest_client, authed_user):
        """#1830 core (Supabase lane): the tier-2 own-RECOVERY candidate
        beats the tier-3 own-bootstrap — the exact deadlock scenario (own
        recovery keys + own bootstraps coexist, no legacy, no other-user
        key). Rotating the recovery key frees a REAL persistent slot so the
        re-check passes: 200, rotated=True, the OLDEST recovery key revoked,
        the bootstrap survives. (A regression swapping tiers 2/3 would
        re-introduce the 402 deadlock — the rotated bootstrap would free no
        persistent slot.)"""
        tc, fake = rest_client
        fake.seed("team_memberships", [_membership_row(user_id=_USER1, team_id="team-free-001")])
        fake.seed("api_keys", [
            # cap=2: two own RECOVERY keys fill the cap (the #1830 deadlock)...
            _key_row(id="own-rec-1", created_via="recovery", created_by=_USER1,
                     lookup_hash="h1", created_at="2026-08-01T00:00:00Z"),
            _key_row(id="own-rec-2", created_via="recovery", created_by=_USER1,
                     lookup_hash="h2", created_at="2026-08-02T00:00:00Z"),
            # ...plus an own bootstrap (24h ephemeral — rotating it would NOT
            # free a persistent slot; recovery must win the rotation order).
            _key_row(id="own-boot-1", created_via="bootstrap", created_by=_USER1,
                     lookup_hash="h3", created_at="2026-08-01T12:00:00Z"),
        ])
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200, r.text
        assert r.json()["rotated"] is True  # recovery rotation → UI banner
        by_id = {row["id"]: row for row in fake.tables["api_keys"]}
        assert by_id["own-rec-1"]["revoked_at"] is not None  # oldest recovery rotated
        assert by_id["own-rec-2"]["revoked_at"] is None      # newest recovery survives
        assert by_id["own-boot-1"]["revoked_at"] is None     # own bootstrap survives (tier 3)
        # revoke(own-rec-1) + mint = exactly the cap, never cap+1
        persistent = [r for r in fake.tables["api_keys"]
                      if r.get("revoked_at") is None
                      and r.get("created_via") != "bootstrap"]
        assert len(persistent) <= 2

    def test_recovery_cap_rotates_legacy_unowned_key(self, rest_client,
                                                     authed_user):
        """#1828 review P3-4 + P2-1 + #1859 P3-1 (Supabase lane, LANE
        PARITY): a LEGACY team-scoped unowned key (created_by IS NULL) is a
        rotation candidate — it COUNTS against max_api_keys (created_via
        NULL), so rotating it frees a REAL slot: the mint lands at exactly
        the cap (never cap+1). #1859: the legacy key is EXCLUDED from the
        others list (mirroring the registry predicate ``created_by <> $uid``
        whose Cypher NULL semantics exclude unowned rows), so it falls to
        the #1828 rotation branch and the rotated flag fires — the SAME
        scenario as the registry twin
        (test_session_key_http.py::test_at_cap_rotates_legacy_unowned_key_when_it_frees_a_slot
        asserts rotated is True there)."""
        tc, fake = rest_client
        fake.seed("team_memberships", [_membership_row(user_id=_USER1, team_id="team-free-001")])
        fake.seed("api_keys", [
            _key_row(id="own-rec-1", created_via="recovery", created_by=_USER1,
                     lookup_hash="h1", created_at="2026-08-01T00:00:00Z"),
            # legacy unowned persistent key (created_by NULL, created_via NULL)
            _key_row(id="legacy-1", created_via=None, created_by=None,
                     lookup_hash="h2", created_at="2026-08-01T00:00:00Z"),
            _key_row(id="own-boot-1", created_via="bootstrap", created_by=_USER1,
                     lookup_hash="h3", created_at="2026-08-03T00:00:00Z"),
        ])
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200, r.text
        # #1859 P3-1: rotation branch fires (legacy excluded from others) —
        # flag parity with the registry lane (rotated=True there too).
        assert r.json()["rotated"] is True
        by_id = {row["id"]: row for row in fake.tables["api_keys"]}
        assert by_id["legacy-1"]["revoked_at"] is not None   # legacy rotated
        assert by_id["own-rec-1"]["revoked_at"] is None      # own untouched
        assert by_id["own-boot-1"]["revoked_at"] is None     # bootstrap survives
        new_rows = [row for row in fake.tables["api_keys"]
                    if row["id"] not in ("own-rec-1", "legacy-1", "own-boot-1")]
        assert len(new_rows) == 1
        assert new_rows[0]["created_via"] == "recovery"
        # revoke(legacy) + mint = exactly the cap, never cap+1
        persistent = [r for r in fake.tables["api_keys"]
                      if r.get("revoked_at") is None
                      and r.get("created_via") != "bootstrap"]
        assert len(persistent) <= 2

    def test_mint_requires_membership(self, rest_client, authed_user):
        tc, fake = rest_client
        fake.tables["team_memberships"] = []
        r = tc.post("/v1/session/key", json={"purpose": "bootstrap"})
        assert r.status_code == 403


# ── E2E-3 invitations flip (plan Task 4) ───────────────────────────────────

class TestInvitesEndpointFlip:
    """E2E-3: owner invites by email → invite verified via lookup_hash,
    accepted → real membership with the invited role, pending invite
    consumed; a used/revoked invite cannot be re-accepted — over HTTP in
    Supabase mode with the fake control plane."""

    @pytest.fixture
    def as_user(self):
        """Override get_current_user per test (JWT session user)."""

        def _set(user_id: str, email: str | None = None):
            app.dependency_overrides[get_current_user] = lambda: {
                "user_id": user_id, "email": email}

        yield _set
        app.dependency_overrides.pop(get_current_user, None)

    @pytest.fixture
    def team_tier(self, rest_client):
        """Team-tier team with user-1 as owner (invites enabled)."""
        tc, fake = rest_client
        fake.tables["teams"] = [dict(TEAM_TIER_TEAM)]
        fake.seed("team_memberships", [{
            "user_id": _USER1, "team_id": "team-team-001",
            "role": "owner", "status": "active"}])
        return tc, fake

    def test_mint_accept_round_trip_role_preserved(self, team_tier, as_user):
        """E2E-3 happy path: mint → lookup_hash row → accept → membership
        with the INVITED role; consumed invite cannot be re-accepted."""
        tc, fake = team_tier
        as_user(_USER1)

        r = tc.post("/v1/invites", json={
            "team_id": "team-team-001", "email": "bob@example.com",
            "role": "admin"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "invited"
        assert body["role"] == "admin"
        token = body["token"]
        assert token

        # invite verified via lookup_hash (token never stored plaintext)
        rows = fake.tables["invitations"]
        assert len(rows) == 1
        assert rows[0]["lookup_hash"] == lookup_hash(token)
        assert rows[0]["email"] == "bob@example.com"
        assert rows[0]["status"] == "pending"

        # accept as the invitee (JWT email must match)
        as_user(_USER2, "bob@example.com")
        r = tc.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 200, r.text
        assert r.json() == {"team_id": "team-team-001", "role": "admin"}

        mem = [m for m in fake.tables["team_memberships"]
               if m["user_id"] == _USER2]
        assert len(mem) == 1
        assert mem[0]["role"] == "admin"  # invited role preserved (O/I/T)
        assert mem[0]["status"] == "active"
        assert rows[0]["status"] == "accepted"  # pending invite consumed
        assert rows[0]["accepted_at"] is not None

        # used invite cannot be re-accepted (E2E-3)
        r = tc.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 400
        assert "accepted" in r.json()["detail"]

    def test_mint_dedup_409(self, team_tier, as_user):
        tc, fake = team_tier
        as_user(_USER1)
        payload = {"team_id": "team-team-001", "email": "bob@example.com",
                   "role": "member"}
        assert tc.post("/v1/invites", json=payload).status_code == 200
        r = tc.post("/v1/invites", json=payload)
        assert r.status_code == 409
        assert len(fake.tables["invitations"]) == 1

    def test_mint_requires_team_tier(self, rest_client, as_user):
        """Free tier → 402 (invites are a Team-tier feature, D7 #574)."""
        tc, fake = rest_client
        fake.seed("team_memberships", [{
            "user_id": _USER1, "team_id": "team-free-001",
            "role": "owner", "status": "active"}])
        as_user(_USER1)
        r = tc.post("/v1/invites", json={
            "team_id": "team-free-001", "email": "bob@example.com",
            "role": "member"})
        assert r.status_code == 402

    def test_mint_requires_owner_admin(self, team_tier, as_user):
        tc, fake = team_tier
        fake.seed("team_memberships", [{
            "user_id": _USER9, "team_id": "team-team-001",
            "role": "member", "status": "active"}])
        as_user(_USER9)
        r = tc.post("/v1/invites", json={
            "team_id": "team-team-001", "email": "bob@example.com",
            "role": "member"})
        assert r.status_code == 403

    def test_expired_invite_rejected(self, team_tier, as_user):
        tc, fake = team_tier
        as_user(_USER1)
        r = tc.post("/v1/invites", json={
            "team_id": "team-team-001", "email": "bob@example.com",
            "role": "member"})
        token = r.json()["token"]
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()  # noqa: UP017
        fake.tables["invitations"][0]["expires_at"] = past
        as_user(_USER2, "bob@example.com")
        r = tc.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 400
        assert "expired" in r.json()["detail"]

    def test_revoked_invite_rejected_after_rescind(self, team_tier, as_user):
        """E2E-3: rescind → revoked; a revoked invite cannot be accepted."""
        tc, fake = team_tier
        as_user(_USER1)
        r = tc.post("/v1/invites", json={
            "team_id": "team-team-001", "email": "bob@example.com",
            "role": "member"})
        invite_id = r.json()["invite_id"]
        token = r.json()["token"]

        r = tc.delete(f"/v1/invites/{invite_id}?team_id=team-team-001")
        assert r.status_code == 200, r.text
        assert r.json()["revoked"] is True
        assert fake.tables["invitations"][0]["status"] == "revoked"

        as_user(_USER2, "bob@example.com")
        r = tc.post("/v1/invites/accept", json={"token": token})
        assert r.status_code == 400
        assert "revoked" in r.json()["detail"]
        # no membership created for the invitee (user-1's row is the owner)
        assert all(m["user_id"] != _USER2
                   for m in fake.tables["team_memberships"])

    def test_rescind_requires_owner_admin(self, team_tier, as_user):
        tc, fake = team_tier
        as_user(_USER1)
        r = tc.post("/v1/invites", json={
            "team_id": "team-team-001", "email": "bob@example.com",
            "role": "member"})
        invite_id = r.json()["invite_id"]
        fake.seed("team_memberships", [{
            "user_id": _USER9, "team_id": "team-team-001",
            "role": "member", "status": "active"}])
        as_user(_USER9)
        r = tc.delete(f"/v1/invites/{invite_id}?team_id=team-team-001")
        assert r.status_code == 403
        assert fake.tables["invitations"][0]["status"] == "pending"

    def test_list_pending_invites(self, team_tier, as_user):
        tc, fake = team_tier
        as_user(_USER1)
        tokens = {}
        for email in ("bob@example.com", "carol@example.com", "dave@example.com"):
            r = tc.post("/v1/invites", json={
                "team_id": "team-team-001", "email": email,
                "role": "member"})
            assert r.status_code == 200, r.text
            tokens[email] = r.json()["token"]
        # consume one (accepted), rescind another → only one stays pending
        as_user(_USER2, "bob@example.com")
        r = tc.post("/v1/invites/accept", json={"token": tokens["bob@example.com"]})
        assert r.status_code == 200, r.text
        as_user(_USER1)
        r = tc.delete(f"/v1/invites/{fake.tables['invitations'][2]['id']}?team_id=team-team-001")
        assert r.status_code == 200, r.text

        r = tc.get("/v1/invites?team_id=team-team-001")
        assert r.status_code == 200
        rows = r.json()
        assert [i["email"] for i in rows] == ["carol@example.com"]
        assert rows[0]["status"] == "pending"

    def test_invites_fail_closed_on_control_plane_error(self, monkeypatch,
                                                        team_tier, as_user):
        """#2380 (P2): the seam's own membership read (inside
        _require_owner_admin) is now wrapped — a control-plane outage is a
        503 control_plane_unavailable (#1719 class, mirroring the #2401
        toggle/dashboard-login precedent), never a registry fallback and
        never the raw 500 this test previously pinned (the RuntimeError
        escaped the seam to the global handler). Fail-closed preserved:
        invite_to_team's `except HTTPException: raise` re-raises the 503.
        """
        import tortoise.supabase_control as sc
        monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())
        tc, _ = team_tier
        as_user(_USER1)
        r = tc.post("/v1/invites", json={
            "team_id": "team-team-001", "email": "bob@example.com",
            "role": "member"})
        assert r.status_code == 503
        assert r.json().get("detail", {}).get("error_code") == "control_plane_unavailable"


# ── MCP flip ────────────────────────────────────────────────────────────────

def _mounted_test_client(mcp_app):
    """Mount the MCP app at /mcp (mirrors hosted_api + test_mcp_http)."""

    @asynccontextmanager
    async def _lifespan(parent_app):
        async with mcp_app.lifespan(mcp_app):
            yield

    parent = Starlette(lifespan=_lifespan, routes=[Mount("/mcp", app=mcp_app)])
    return TestClient(parent)


def _mcp_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }


def _parse_sse_json(r) -> dict | None:
    """Extract the JSON payload from an SSE response body."""
    text = r.text
    if text.startswith("{"):
        return r.json()
    for line in text.splitlines():
        if line.startswith("data: "):
            import json
            return json.loads(line[6:])
    return None


class TestMcpAuthFlip:
    def test_mcp_resolves_via_supabase(self, monkeypatch, supabase_fake):
        """MCP TeamResolutionMiddleware resolves tt_ keys via Supabase."""
        supabase_fake.seed("api_keys", [_key_row()])
        _enable_supabase(monkeypatch, supabase_fake)
        mcp_app = create_http_app(allowed_origins=[])
        tc = _mounted_test_client(mcp_app)
        tc.headers.update(_mcp_headers(TOKEN))
        with tc:
            r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert r.status_code == 200, r.text
            body = _parse_sse_json(r)
            assert body is not None and "result" in body

    def test_mcp_registry_only_key_401(self, monkeypatch, supabase_fake):
        """E2E-7-negative: registry-only key → 401 JSON-RPC on MCP."""
        _enable_supabase(monkeypatch, supabase_fake)  # no key rows at all
        mcp_app = create_http_app(allowed_origins=[])
        tc = _mounted_test_client(mcp_app)
        tc.headers.update(_mcp_headers(TOKEN))
        with tc:
            r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert r.status_code == 401
            body = _parse_sse_json(r)
            assert body is not None and "error" in body
            assert "tt_" in body["error"]["message"]

    def test_mcp_revoked_key_401(self, monkeypatch, supabase_fake):
        _enable_supabase(monkeypatch, supabase_fake)
        supabase_fake.seed("api_keys",
                           [_key_row(revoked_at="2026-08-01T00:00:00Z")])
        mcp_app = create_http_app(allowed_origins=[])
        tc = _mounted_test_client(mcp_app)
        tc.headers.update(_mcp_headers(TOKEN))
        with tc:
            r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert r.status_code == 401

    def test_mcp_supabase_error_503_fail_closed(self, monkeypatch):
        """A Supabase error is a 503 JSON-RPC — never 200, never a registry
        fallback."""
        import tortoise.supabase_control as sc
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
        monkeypatch.setattr(sc, "get_control_plane", lambda: ErrorControlPlane())
        mcp_app = create_http_app(allowed_origins=[])
        tc = _mounted_test_client(mcp_app)
        tc.headers.update(_mcp_headers(TOKEN))
        with tc:
            r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
            assert r.status_code == 503
            body = _parse_sse_json(r)
            assert body is not None and "error" in body

    def test_mcp_supabase_mode_never_touches_registry(self, monkeypatch,
                                                      supabase_fake):
        """In Supabase mode the middleware must not construct the registry SDK
        (its apikey_verify would 503 on a registry-only key)."""
        import tortoise.mcp_auth as ma
        supabase_fake.seed("api_keys", [_key_row()])
        _enable_supabase(monkeypatch, supabase_fake)
        called = []

        orig = ma.TeamResolutionMiddleware._get_registry_sdk

        def _boom(self):
            called.append(True)
            raise AssertionError("registry SDK must not be used in Supabase mode")

        ma.TeamResolutionMiddleware._get_registry_sdk = _boom
        try:
            mcp_app = create_http_app(allowed_origins=[])
            tc = _mounted_test_client(mcp_app)
            tc.headers.update(_mcp_headers(TOKEN))
            with tc:
                r = tc.post("/mcp", json={"jsonrpc": "2.0", "method": "tools/list", "id": 1})
                assert r.status_code == 200, r.text
            assert called == []
        finally:
            ma.TeamResolutionMiddleware._get_registry_sdk = orig


# ── #1855: per-team mint lock (cap integrity under concurrency) ─────────────

class TestSessionKeyMintConcurrency:
    """#1855 — the session-key mint critical section (cap read → revoke →
    recheck → insert) runs under a per-team in-process lock (_team_mint_lock
    in hosted_api). The load-bearing pair is test_supabase_mint_blocks_while_
    team_lock_held + the lock mechanics tests (they FAIL if the with-lock
    wrapping is removed); the gather E2E tests are regression guards for the
    cap invariant (an all-sync section serializes on one event loop with or
    without the lock — they catch a future await being introduced into the
    section)."""

    @pytest.fixture
    def authed_user(self):
        app.dependency_overrides[get_current_user] = lambda: {"user_id": _USER1}
        yield
        app.dependency_overrides.pop(get_current_user, None)

    def test_team_mint_lock_serializes_same_team(self):
        """The lock is mutually exclusive — a second mint for the SAME team
        blocks until the first releases (deterministic: a non-blocking
        acquire must fail while held and succeed after release)."""
        import threading

        from tortoise.hosted_api import _team_mint_lock

        lock = _team_mint_lock("team-lock-same")
        inside = threading.Event()
        release = threading.Event()

        def worker():
            with lock:
                inside.set()
                release.wait(timeout=5)

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        assert inside.wait(timeout=5), "worker never entered the critical section"
        assert not lock.acquire(blocking=False), \
            "lock NOT held while worker is inside"
        release.set()
        t.join(timeout=5)
        assert lock.acquire(blocking=False), \
            "lock NOT released after worker exited"
        lock.release()

    def test_team_mint_lock_isolates_different_teams(self):
        """Locks are per-TEAM — a mint for team A never blocks team B."""
        from tortoise.hosted_api import _team_mint_lock

        lock_a = _team_mint_lock("team-lock-iso-a")
        lock_b = _team_mint_lock("team-lock-iso-b")
        assert lock_a is not lock_b
        with lock_a:
            assert lock_b.acquire(blocking=False), \
                "team B's mint blocked by team A's lock"
            lock_b.release()
        # cache is stable: same team → same lock object
        assert _team_mint_lock("team-lock-iso-a") is lock_a

    def test_supabase_mint_blocks_while_team_lock_held(self, monkeypatch,
                                                       rest_client,
                                                       authed_user):
        """The SUPABASE mint actually wraps its critical section in the
        per-team lock: a mint for a team whose lock is HELD by the test
        blocks (cannot even reach the cap read) until the lock is released.
        Fails if the with-lock wrapping is ever removed."""
        import asyncio
        import threading

        import tortoise.hosted_api as ha
        from tortoise.hosted_api import _team_mint_lock

        async def _noop(*_a, **_k):
            return None

        _, fake = rest_client
        tid = "team-free-001"
        fake.seed("team_memberships",
                  [_membership_row(user_id=_USER1, team_id=tid)])
        # the mint's post-insert side effects run OUTSIDE the lock and need a
        # real request object — no-op them (we drive the mint directly).
        monkeypatch.setattr(ha, "_async_audit", _noop)
        monkeypatch.setattr(ha, "_abuse_evaluate_keys", _noop)

        lock = _team_mint_lock(tid)
        lock.acquire()
        done = threading.Event()
        outcome = {}

        def _mint():
            try:
                asyncio.run(ha._session_key_supabase(
                    {"purpose": "recovery"}, None, {"user_id": _USER1}))
                outcome["ok"] = True
            except Exception as e:
                outcome["err"] = repr(e)
            done.set()

        t = threading.Thread(target=_mint, daemon=True)
        t.start()
        assert not done.wait(timeout=0.75), \
            f"mint proceeded while the team lock was held (wrapping missing?): {outcome}"
        lock.release()
        assert done.wait(timeout=10), "mint blocked forever after lock release"
        assert outcome.get("ok") is True, f"mint failed after release: {outcome}"
        assert len(fake.tables["api_keys"]) == 1

    @pytest.mark.timeout(15)
    @pytest.mark.timeout(15)
    def test_lock_released_when_mint_fails_closed(self, rest_client,
                                                  authed_user):
        """A mint that fails CLOSED (402 at cap with nothing rotatable) must
        release the team lock — the next mint (after a slot frees) succeeds
        instead of deadlocking on a leaked lock (per-test timeout: a leaked
        lock would otherwise hang the suite red instead of failing)."""
        tc, fake = rest_client
        tid = "team-free-001"
        fake.seed("team_memberships",
                  [_membership_row(user_id=_USER1, team_id=tid)])
        # cap reached with ONLY the user's own PROVISIONED keys — #750.10 /
        # #1828: provisioned keys are NEVER rotation candidates → 402.
        fake.seed("api_keys", [
            _key_row(id="own-1", created_via="provisioned", created_by=_USER1,
                     lookup_hash="h1", created_at="2026-08-01T00:00:00Z"),
            _key_row(id="own-2", created_via="provisioned", created_by=_USER1,
                     lookup_hash="h2", created_at="2026-08-02T00:00:00Z"),
        ])
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 402, r.text
        # free a slot (admin revokes one of the user's own keys)
        fake.tables["api_keys"][0]["revoked_at"] = "2026-08-03T00:00:00Z"
        r = tc.post("/v1/session/key", json={"purpose": "recovery"})
        assert r.status_code == 200, r.text

    def test_concurrent_recovery_mints_stay_at_cap(self, rest_client,
                                                   authed_user):
        """Issue #1855 verification checklist: two CONCURRENT recovery mints
        (real parallel HTTP dispatch via asyncio.gather) never overshoot
        max_api_keys (free = 2). Regression guard — an all-sync section
        serializes on one event loop regardless of the lock, so this catches
        a future await entering the section (the lock then becomes
        load-bearing), not today's interleaving."""
        import asyncio

        import httpx

        from tortoise.hosted_api import app

        _, fake = rest_client
        tid = "team-free-001"
        fake.seed("team_memberships",
                  [_membership_row(user_id=_USER1, team_id=tid)])
        # at cap-1: one OTHER user's recovery key occupies a slot
        fake.seed("api_keys", [
            _key_row(id="other-1", created_via="recovery", created_by=_USER2,
                     lookup_hash="h-other", created_at="2026-08-01T00:00:00Z"),
        ])

        async def _run():
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://test") as ac:
                async def _mint(_i):
                    return await ac.post("/v1/session/key",
                                         json={"purpose": "recovery"})

                return await asyncio.gather(*(_mint(i) for i in range(2)))

        results = asyncio.run(_run())
        assert all(r.status_code == 200 for r in results), \
            [r.text for r in results]
        active = [k for k in fake.tables["api_keys"]
                  if k.get("team_id") == tid and k.get("revoked_at") is None
                  and k.get("created_via") != "bootstrap"]
        assert len(active) <= 2, \
            f"cap overshot: {len(active)} active non-bootstrap keys"
