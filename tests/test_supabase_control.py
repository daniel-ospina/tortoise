"""Unit tests for the Supabase control-plane auth resolution (#767, plan Task 3).

Covers the lookup scheme (plan P1-1: lookup_hash exact-match against
api_keys then team_memberships), authoritative revocation (P1-2:
api_keys.revoked_at), #742 expiry, E2E-7-negative (registry-only key → None),
fail-closed error behavior (P1-3 pattern), and tier/quota from teams — all
against the in-memory FakeControlPlane (zero network).

See also tests/test_auth_flip.py for the REST + MCP end-to-end flips.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

from tortoise.auth import lookup_hash
from tortoise.supabase_control import (
    ClaimError,
    InvitationError,
    active_api_keys,
    api_key_by_id,
    claim_membership,
    expired_bootstrap_keys,
    get_control_plane,
    github_credentials,
    graph_metadata,
    insert_api_key,
    invitation_accept,
    invitation_mint,
    invitation_rescind,
    is_anon_team,
    is_supabase_enabled,
    membership_count_since,
    membership_for_user_team,
    membership_role,
    pending_invitations,
    provision_team,
    resolve_api_key,
    revoke_api_key,
    set_membership,
    store_github_credentials,
    team_by_email,
    team_by_id,
    team_by_name,
    team_email,
    team_members,
    team_onboarding_state,
    team_api_keys,
    update_last_used,
    update_onboarding_state,
    update_team_email,
    user_memberships,
)

from tests.fake_control_plane import ErrorControlPlane, FakeControlPlane

# ── Fixtures / helpers ──────────────────────────────────────────────────────

TOKEN = "tt_unit_test_session_key_00000000001"

FREE_TEAM = {
    "id": "team-free-001", "name": "Free Team", "tier": "free",
    "max_users": 1, "max_graphs": 1, "graph_size_cap": 10000,
    "ops_allowance": 1000,
}
TEAM_TIER_TEAM = {
    "id": "team-team-001", "name": "Team Tier", "tier": "team",
    "max_users": None, "max_graphs": None, "graph_size_cap": 500000,
    "ops_allowance": None,
}


def _key_row(**overrides) -> dict:
    row = {
        "id": "key-001", "team_id": "team-free-001",
        # Computed at CALL time, not import time: other test modules reload
        # tortoise.auth with different peppers (test_hosted_auth #6984 suite),
        # which mutates auth._PEPPER_BYTES mid-session — a frozen import-time
        # hash would silently mismatch resolve_api_key's runtime lookup_hash
        # and 401 in full-suite runs (#767).
        "lookup_hash": lookup_hash(TOKEN),
        "key_prefix": TOKEN[:10], "created_via": "bootstrap",
        "created_by": "user-1", "expires_at": None, "revoked_at": None,
    }
    row.update(overrides)
    return row


def _membership_row(**overrides) -> dict:
    row = {
        "user_id": "user-1", "team_id": "team-free-001",
        "lookup_hash": lookup_hash(TOKEN),
        "role": "owner", "status": "active", "identity": None,
    }
    row.update(overrides)
    return row


@pytest.fixture
def fake() -> FakeControlPlane:
    # The invitation-seam tests use team-1 (with a generous member cap for
    # the accept quota gate, PR #864 review P2); FREE_TEAM serves the
    # resolve/session tests.
    return FakeControlPlane({
        "api_keys": [],
        "team_memberships": [],
        "teams": [dict(FREE_TEAM),
                   {"id": "team-1", "name": "Invite Team", "tier": "free",
                    "max_users": 100, "graph_name": "team_team-1"}],
    })


def test_fake_filter_column_drift_raises():
    """Fake fidelity (#1096; out-of-slice scaffolding for the escalation
    decomposition's sweep/health tests): PostgREST 400s on a FILTER of an
    absent column just as on a select — the #302 sweeps filter deleted_at."""
    fake = FakeControlPlane(
        {"teams": [dict(FREE_TEAM)]},
        missing_columns={"teams": {"deleted_at"}})
    with pytest.raises(RuntimeError):
        fake.query("teams", select=["id"],
                   filters=[("deleted_at", "is", None)])


# ── Resolve: api_keys path (E2E-2) ──────────────────────────────────────────

class TestResolveApiKey:
    def test_session_key_resolves_via_api_keys(self, fake):
        """E2E-2: a minted session key (api_keys row) resolves via lookup_hash."""
        fake.seed("api_keys", [_key_row()])
        team = resolve_api_key(fake, TOKEN)
        assert team is not None
        assert team["team_id"] == "team-free-001"
        assert team["key_id"] == "key-001"
        assert team["tier"] == "free"
        assert team["created_via"] == "bootstrap"
        assert team["created_by"] == "user-1"
        assert team["key_prefix"] == TOKEN[:10]
        # exact-match index: only the api_keys table is consulted, no scans
        assert fake.query_count == 2  # api_keys + teams

    def test_long_lived_key_resolves_via_team_memberships(self, fake):
        """Long-lived (provisioned) key with no api_keys row → membership path."""
        fake.seed("team_memberships", [_membership_row()])
        team = resolve_api_key(fake, TOKEN)
        assert team is not None
        assert team["team_id"] == "team-free-001"
        assert team["key_id"] is None  # no api_keys row to point at
        assert team["tier"] == "free"

    def test_revoked_twin_rejects_even_when_membership_matches(self, fake):
        """P1-2: api_keys.revoked_at is AUTHORITATIVE — a revoked twin rejects
        even when the team_memberships row is active."""
        fake.seed("api_keys", [_key_row(revoked_at="2026-08-01T00:00:00Z")])
        fake.seed("team_memberships", [_membership_row()])
        assert resolve_api_key(fake, TOKEN) is None

    def test_expired_key_rejects(self, fake):
        """#742: expired keys must NOT authenticate."""
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        fake.seed("api_keys", [_key_row(expires_at=past)])
        assert resolve_api_key(fake, TOKEN) is None

    def test_unexpired_key_passes(self, fake):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        fake.seed("api_keys", [_key_row(expires_at=future)])
        assert resolve_api_key(fake, TOKEN) is not None

    def test_unknown_key_returns_none(self, fake):
        assert resolve_api_key(fake, "tt_no_such_key_anywhere_00000000001") is None

    def test_registry_only_key_returns_none(self, fake):
        """E2E-7-negative: a key that exists ONLY in the FalkorDB registry
        resolves to nothing in Supabase → None → 401 on both paths."""
        fake.seed("api_keys", [_key_row(lookup_hash="deadbeef" * 8)])
        assert resolve_api_key(fake, TOKEN) is None

    def test_inactive_membership_does_not_resolve(self, fake):
        fake.seed("team_memberships",
                  [_membership_row(status="removed"), _membership_row(status="invited")])
        assert resolve_api_key(fake, TOKEN) is None

    def test_missing_team_fails_closed(self, fake):
        """A key whose team row vanished → None (401), never authenticate."""
        fake.tables["teams"] = []
        fake.seed("api_keys", [_key_row()])
        assert resolve_api_key(fake, TOKEN) is None

    def test_control_plane_error_raises_fail_closed(self):
        """P1-3: a Supabase query error RAISES — never None (401) and never a
        registry fallback."""
        with pytest.raises(RuntimeError):
            resolve_api_key(ErrorControlPlane(), TOKEN)

    def test_tier_and_quota_from_teams_row(self, fake):
        """Tier/quota come from the teams row (plan Task 3)."""
        fake.tables["teams"] = [dict(TEAM_TIER_TEAM)]
        fake.seed("api_keys", [_key_row(team_id="team-team-001")])
        team = resolve_api_key(fake, TOKEN)
        assert team["tier"] == "team"
        # Team tier = unlimited → None preserved (matches registry path)
        assert team["max_users"] is None
        assert team["max_graphs"] is None
        # points counter counts graph nodes → graph_size_cap (#310 GAP-B)
        assert team["max_points"] == 500000
        assert team["max_api_keys"] > 0
        assert team["max_sessions"] == 1000

    def test_free_tier_quota_falls_back_to_pricing(self, fake):
        """0006 teams has no max_api_keys/max_sessions columns → pricing
        defaults (mirrors registry path)."""
        fake.seed("api_keys", [_key_row()])
        team = resolve_api_key(fake, TOKEN)
        assert team["max_users"] == 1
        assert team["max_graphs"] == 1
        assert team["max_points"] == 10000
        assert team["max_sessions"] == 1000
        assert team["max_api_keys"] > 0

    def test_dict_shape_matches_registry_contract(self, fake):
        fake.seed("api_keys", [_key_row()])
        team = resolve_api_key(fake, TOKEN)
        for key in ("team_id", "key_id", "tier", "max_users", "max_graphs",
                    "max_points", "max_api_keys", "max_sessions"):
            assert key in team, f"missing {key}"


# ── #1096 fail-soft additive teams columns (post-#1001 auth resilience) ───

class TestResolveApiKeyFailSoft:
    def test_resolve_api_key_additive_columns_missing_fail_soft(self, fake,
                                                               caplog):
        """#1096: teams missing 0015 additive columns (the #1001 drift) →
        resolve returns the team, NOT RuntimeError; additive fields default
        to safe values (un-suspended/un-flagged; key login allowed); the
        base read still carries real values (email, 0006) and the degrade
        is logged (drift stays diagnosable)."""
        fake.tables["teams"][0]["email"] = "owner@example.com"
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        fake.seed("api_keys", [_key_row()])
        with caplog.at_level("WARNING", logger="tortoise.supabase_control"):
            team = resolve_api_key(fake, TOKEN)
        assert team is not None
        assert team["team_id"] == "team-free-001"
        assert team["tier"] == "free"
        assert team["max_points"] == 10000
        assert team["suspended_at"] is None
        assert team["flagged_at"] is None
        # 0006 base column — the base retry carries the REAL value (not a
        # None pad): proves the base-vs-additive split is discriminating.
        assert team["email"] == "owner@example.com"
        # 20260813000005 additive — safe default is ALLOWED (matches the
        # column's NOT NULL DEFAULT true; the #1148 gate must not 403
        # key-auth management during drift).
        assert team["dashboard_key_login"] is True
        assert any("additive" in r.message for r in caplog.records)

    def test_resolve_api_key_dashboard_key_login_only_drift(self, fake,
                                                           caplog):
        """#1096: the 20260813000005 additive class alone (dashboard_key_login
        missing while 0015 present) fails soft the same way — key login
        degrades to the safe default True."""
        fake.missing_columns = {"teams": {"dashboard_key_login"}}
        fake.seed("api_keys", [_key_row()])
        with caplog.at_level("WARNING", logger="tortoise.supabase_control"):
            team = resolve_api_key(fake, TOKEN)
        assert team is not None
        assert team["dashboard_key_login"] is True
        assert any("additive" in r.message for r in caplog.records)

    def test_resolve_api_key_dkl_only_drift_keeps_suspension(self, fake):
        """#1096 (code-review fix): a 20260813000005-ONLY drift must NOT
        discard real 0015 suspension state — the tiered retry reads
        suspended_at/flagged_at on the second attempt (discarding it would
        bypass enforcement with REAL data present)."""
        fake.tables["teams"][0]["suspended_at"] = \
            datetime.now(timezone.utc).isoformat()
        fake.missing_columns = {"teams": {"dashboard_key_login"}}
        fake.seed("api_keys", [_key_row()])
        team = resolve_api_key(fake, TOKEN)
        assert team is not None
        assert team["suspended_at"] is not None  # real 0015 data kept
        assert team["dashboard_key_login"] is True  # dkl tier padded to default

    def test_resolve_api_key_api_keys_enabled_drift_fail_soft(self, fake,
                                                             caplog):
        """#1096 (code-review fix): api_keys.enabled is additive
        (20260813000005) — a schema missing it fails soft to the pre-#1148
        default True instead of taking down ALL key auth at step 1 (the
        realistic drift fails the api_keys read first; the caplog WARNING
        discriminates the degrade)."""
        fake.missing_columns = {"api_keys": {"enabled"}}
        fake.seed("api_keys", [_key_row()])
        with caplog.at_level("WARNING", logger="tortoise.supabase_control"):
            team = resolve_api_key(fake, TOKEN)
        assert team is not None
        assert team["enabled"] is True
        assert any("enabled" in r.message for r in caplog.records)

    def test_resolve_api_key_stored_false_drift_fail_open(self, fake):
        """#1096 accepted-risk doc: additive drift loses ALL additive state —
        a stored dashboard_key_login=False is not readable through the base
        retry, so the gate degrades to the pre-20260813000005 default True
        (fail-open, same class as the suspension degrade). Pins the actual
        behavior so a future change cannot silently flip it closed."""
        fake.tables["teams"][0]["dashboard_key_login"] = False
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        fake.seed("api_keys", [_key_row()])
        team = resolve_api_key(fake, TOKEN)
        assert team is not None
        assert team["dashboard_key_login"] is True  # drift lose-all-additive
        # healthy-mode False is still carried (the gate stays closed)
        fake.missing_columns = None
        assert resolve_api_key(fake, TOKEN)["dashboard_key_login"] is False

    def test_resolve_api_key_unaffected_by_deletion_columns_drift(self, fake,
                                                                 caplog):
        """#1096: the 20260813000001 class (deleted_at/grace_hours) is NOT in
        _QUOTA_SELECT — resolve is unaffected (no degrade, no raise, no
        WARNING, query count unchanged). The discriminating boundary of
        suspension-fails-soft vs deletion-fails-closed."""
        fake.missing_columns = {"teams": {"deleted_at", "grace_hours"}}
        fake.seed("api_keys", [_key_row()])
        with caplog.at_level("WARNING", logger="tortoise.supabase_control"):
            team = resolve_api_key(fake, TOKEN)
        assert team is not None
        assert team["suspended_at"] is None
        assert not any("additive" in r.message or "base-only read failed" in r.message
                       for r in caplog.records)
        assert fake.query_count == 2  # api_keys + teams (single round-trip)

    def test_resolve_api_key_carries_suspension_state(self, fake):
        """O/I/T target 2: with the columns PRESENT, suspension state still
        resolves (enforcement is unchanged — REST 403 / MCP -32006 consume
        this field)."""
        fake.seed("api_keys", [_key_row()])
        fake.tables["teams"][0]["suspended_at"] = \
            datetime.now(timezone.utc).isoformat()
        team = resolve_api_key(fake, TOKEN)
        assert team is not None
        assert team["suspended_at"] is not None

    def test_resolve_api_key_degrade_then_recover(self, fake):
        """#1096: degrade-then-recover — the helper is stateless; after the
        additive columns become readable again, enforcement resumes (a
        future latch/cache in the degrade path must not stick)."""
        fake.seed("api_keys", [_key_row()])
        fake.tables["teams"][0]["suspended_at"] = \
            datetime.now(timezone.utc).isoformat()
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        assert resolve_api_key(fake, TOKEN)["suspended_at"] is None  # degraded
        fake.missing_columns = None
        assert resolve_api_key(fake, TOKEN)["suspended_at"] is not None  # recovered

    def test_resolve_api_key_missing_team_under_drift_returns_none(self, fake):
        """#1096: drift + absent team — the base retry returns [] → None
        (401), never a raise (fail-closed on not-found, fail-soft on drift)."""
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        fake.seed("api_keys", [_key_row(team_id="team-gone")])
        assert resolve_api_key(fake, TOKEN) is None

    def test_long_lived_key_resolves_under_0015_drift(self, fake):
        """#1096: the team_memberships (long-lived key) branch drifts the
        same way — the shared _teams_row_fail_soft teams read degrades
        identically; the membership query itself is drift-scoped."""
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        fake.seed("team_memberships", [_membership_row()])
        team = resolve_api_key(fake, TOKEN)
        assert team is not None
        assert team["team_id"] == "team-free-001"
        assert team["suspended_at"] is None
        assert team["flagged_at"] is None

    def test_resolve_api_key_base_column_drift_fails_closed(self, fake,
                                                           caplog):
        """#1096: a drifted BASE column (0006) fails the auth hot path
        CLOSED — combined read raises → base retry also raises (ops_allowance
        stays in the base set) → RuntimeError + the fatal-path WARNING."""
        fake.missing_columns = {"teams": {"ops_allowance"}}
        fake.seed("api_keys", [_key_row()])
        with caplog.at_level("WARNING", logger="tortoise.supabase_control"):
            with pytest.raises(RuntimeError):
                resolve_api_key(fake, TOKEN)
        assert any("base-only read failed" in r.message for r in caplog.records)


class TestTeamByID:
    def test_team_by_id_additive_columns_missing_fail_soft(self, fake,
                                                           caplog):
        """#1096: team_by_id survives 0015 + 20260813000005 drift
        (suspension/staging + key-login columns) — returns the row with
        safe None defaults, no raise; the caplog WARNING discriminates the
        degrade actually firing (a None assert alone would pass without
        drift, since FREE_TEAM lacks the columns)."""
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at",
                                           "dashboard_key_login"}}
        with caplog.at_level("WARNING", logger="tortoise.supabase_control"):
            team = team_by_id(fake, "team-free-001")
        assert team is not None
        assert team["name"] == "Free Team"
        assert team["suspended_at"] is None
        assert team["flagged_at"] is None
        assert team["dashboard_key_login"] is None  # raw seam value
        assert team["deleted_at"] is None  # base column read via retry
        assert any("additive" in r.message for r in caplog.records)

    def test_team_by_id_missing_team_under_drift_returns_none(self, fake):
        """#1096: team_by_id drift + absent team — the base retry returns
        [] → None, never a raise (fail-closed on not-found)."""
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        assert team_by_id(fake, "missing") is None

    def test_team_by_id_dkl_only_drift_keeps_suspension(self, fake):
        """#1096 (code-review fix): team_by_id's tiered retry — a
        20260813000005-ONLY drift keeps real 0015 suspension state."""
        fake.tables["teams"][0]["suspended_at"] = \
            datetime.now(timezone.utc).isoformat()
        fake.missing_columns = {"teams": {"dashboard_key_login"}}
        team = team_by_id(fake, "team-free-001")
        assert team is not None
        assert team["suspended_at"] is not None  # real 0015 data kept
        assert team["dashboard_key_login"] is None  # dkl tier padded (raw seam)

    def test_team_by_id_deleted_at_survives_0015_drift(self, fake, caplog):
        """#1096: the deletion kill-switch must survive 0015 drift — a SET
        deleted_at (soft-deleted team) is carried by the base retry, so
        invitation_accept/export_team 410 guards keep firing. The caplog
        WARNING proves the retry (not just the select) fired."""
        fake.tables["teams"][0]["deleted_at"] = \
            datetime.now(timezone.utc).isoformat()
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        with caplog.at_level("WARNING", logger="tortoise.supabase_control"):
            team = team_by_id(fake, "team-free-001")
        assert team is not None
        assert team["deleted_at"] is not None
        assert any("additive" in r.message for r in caplog.records)

    def test_team_by_id_deletion_columns_drift_fails_closed(self, caplog):
        """#1096: the #302 soft-delete columns are NOT fail-soft — a schema
        missing deleted_at/grace_hours fails the deletion guard CLOSED
        (RuntimeError propagates), never opening the 410 kill-switch; the
        fatal-path WARNING (the drift-diagnosability tripwire) fires and
        names the retry select."""
        fake = FakeControlPlane(
            {"api_keys": [], "team_memberships": [], "teams": [dict(FREE_TEAM)]},
            missing_columns={"teams": {"deleted_at", "grace_hours"}})
        with caplog.at_level("WARNING", logger="tortoise.supabase_control"):
            with pytest.raises(RuntimeError):
                team_by_id(fake, "team-free-001")
        assert any("base-only read failed" in r.message for r in caplog.records)

    def test_team_by_id_base_read_fails_closed(self):
        """#1096: the base-only retry must NOT swallow a real outage — a
        broken teams read still propagates RuntimeError (fail-closed)."""
        with pytest.raises(RuntimeError):
            team_by_id(ErrorControlPlane(), "team-free-001")

    def test_invitation_accept_410_under_0015_drift(self, fake):
        """#1096: the deletion kill-switch fires at the CONSUMER level under
        0015 drift — a soft-deleted team still 410s invitation_accept (the
        deleted_at-carried test proves the mechanism; this proves the
        contract the Architecture section claims)."""
        inv = invitation_mint(fake, "team-free-001", "bob@example.com",
                              "member", "owner-1")
        fake.tables["teams"][0]["deleted_at"] = \
            datetime.now(timezone.utc).isoformat()
        fake.missing_columns = {"teams": {"suspended_at", "flagged_at"}}
        with pytest.raises(InvitationError) as ei:
            invitation_accept(fake, inv["token"], "user-2")
        assert ei.value.status == 410


# ── update_last_used (#685 write-through) ───────────────────────────────────

class TestUpdateLastUsed:
    def test_sets_last_used_at_on_api_keys_row(self, fake):
        fake.seed("api_keys", [_key_row()])
        update_last_used(fake, "key-001")
        assert fake.tables["api_keys"][0]["last_used_at"] is not None

    def test_best_effort_never_raises(self):
        """Telemetry write must never gate auth — errors are swallowed."""
        update_last_used(ErrorControlPlane(), "key-001")  # no raise


# ── Env gating (plan Task 8 variable, minimal here) ─────────────────────────

class TestEnvGating:
    def test_registry_forced_when_toggle_set(self, monkeypatch):
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
        assert is_supabase_enabled() is False

    def test_supabase_default_when_configured(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_CONTROL_PLANE", raising=False)
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
        assert is_supabase_enabled() is True

    def test_registry_default_without_creds(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_CONTROL_PLANE", raising=False)
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        assert is_supabase_enabled() is False

    def test_supabase_mode_requires_creds(self, monkeypatch):
        """Explicit TORTOISE_CONTROL_PLANE=supabase with missing creds is
        FAIL-CLOSED (code-review P2, PR #851): enabled=True so
        get_control_plane() raises RuntimeError (→ REST 500 / MCP 503) — a
        Supabase-only deployment must never silently authenticate via the
        registry."""
        monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        assert is_supabase_enabled() is True
        from tortoise.supabase_control import SupabaseControlPlane
        with pytest.raises(RuntimeError, match="not configured"):
            SupabaseControlPlane()

    def test_legacy_service_key_env_accepted(self, monkeypatch):
        monkeypatch.delenv("TORTOISE_CONTROL_PLANE", raising=False)
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc-legacy")
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        assert is_supabase_enabled() is True


# ── Session-path helpers (get_current_user memberships, E1/E6/E8) ──────────

class TestSessionHelpers:
    def test_user_memberships_active_only_no_placeholder(self, fake):
        fake.seed("team_memberships", [
            _membership_row(team_id="team-a", user_id="user-1", lookup_hash=None),
            _membership_row(team_id="team-b", user_id="user-1", lookup_hash=None,
                            status="removed"),
            _membership_row(team_id="", user_id="user-1", lookup_hash=None),
        ])
        got = user_memberships(fake, "user-1")
        assert got == [{"team_id": "team-a", "role": "owner"}]

    def test_membership_for_user_team(self, fake):
        fake.seed("team_memberships", [
            _membership_row(team_id="team-a", user_id="user-1", lookup_hash=None),
        ])
        assert membership_for_user_team(fake, "user-1", "team-a") == {
            "team_id": "team-a", "role": "owner"}
        assert membership_for_user_team(fake, "user-1", "team-b") is None

    def test_team_by_id_returns_row(self, fake):
        row = team_by_id(fake, "team-free-001")
        assert row is not None
        assert row["name"] == "Free Team"
        assert team_by_id(fake, "missing") is None

    def test_active_api_keys_excludes_expired(self, fake):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        fake.seed("api_keys", [
            _key_row(id="k1", expires_at=past, created_via="bootstrap"),
            _key_row(id="k2", expires_at=None, created_via="recovery"),
            _key_row(id="k3", revoked_at="2026-08-01T00:00:00Z"),
        ])
        active = active_api_keys(fake, "team-free-001")
        assert [r["id"] for r in active] == ["k2"]

    def test_active_api_keys_created_via_filter(self, fake):
        fake.seed("api_keys", [
            _key_row(id="k1", created_via="bootstrap", created_by="user-1"),
            _key_row(id="k2", created_via="recovery", created_by="user-2"),
        ])
        boot = active_api_keys(fake, "team-free-001", created_via="bootstrap",
                               created_by="user-1")
        assert [r["id"] for r in boot] == ["k1"]

    def test_insert_and_revoke_api_key(self, fake):
        insert_api_key(fake, _key_row())
        assert len(fake.tables["api_keys"]) == 1
        revoke_api_key(fake, "key-001", now="2026-08-02T00:00:00Z")
        assert fake.tables["api_keys"][0]["revoked_at"] == "2026-08-02T00:00:00Z"


# ── Invitations seam (plan Task 4: mint / accept / rescind) ───────────────

class TestInvitationSeam:
    """Mint/accept/rescind against the invitations table (E2E-3 owns).

    Covers the O/I/T contract: dedup (team,email) pending + 7-day expiry
    enforced at mint; accept verifies via lookup_hash and creates the REAL
    membership with the INVITED role; used/expired/revoked invites are
    rejected (E2E-3).
    """

    def _owner(self, fake, team_id="team-1", user_id="owner-1"):
        fake.seed("team_memberships", [{
            "user_id": user_id, "team_id": team_id,
            "role": "owner", "status": "active",
        }])
        # Accept's quota/tier gate (PR #864 review P2) reads the teams row —
        # seed it with a generous member cap unless a test overrides.
        if not any(t.get("id") == team_id for t in fake.tables.get("teams", [])):
            fake.seed("teams", [{
                "id": team_id, "name": "Invite Team", "tier": "free",
                "max_users": 100, "graph_name": f"team_{team_id}",
            }])

    # ── mint ───────────────────────────────────────────────────────────

    def test_mint_creates_pending_row_with_lookup_hash(self, fake):
        inv = invitation_mint(fake, "team-1", "bob@example.com", "admin",
                              "owner-1")
        assert inv["token"]
        assert inv["role"] == "admin"
        assert inv["status"] == "pending"
        rows = fake.tables["invitations"]
        assert len(rows) == 1
        row = rows[0]
        # token stored ONLY as SHA-256(pepper + token) — O(1) indexed verify
        assert row["lookup_hash"] == lookup_hash(inv["token"])
        assert row["status"] == "pending"
        assert row["team_id"] == "team-1"
        assert row["email"] == "bob@example.com"
        assert row["invited_by"] == "owner-1"
        # 7-day expiry (default)
        exp = datetime.fromisoformat(row["expires_at"])
        now = datetime.now(timezone.utc)
        assert timedelta(days=6) < exp - now < timedelta(days=8)

    def test_mint_rejects_duplicate_pending(self, fake):
        """Dedup: partial unique (team_id, email) WHERE status='pending'."""
        invitation_mint(fake, "team-1", "bob@example.com", "admin", "u1")
        with pytest.raises(InvitationError) as ei:
            invitation_mint(fake, "team-1", "bob@example.com", "member", "u1")
        assert ei.value.status == 409
        assert len(fake.tables["invitations"]) == 1

    def test_mint_reinvite_allowed_after_consumed(self, fake):
        """Dedup is on PENDING only — a consumed invite doesn't block a
        fresh re-invite (NULLs are distinct; the partial index enforces)."""
        inv1 = invitation_mint(fake, "team-1", "bob@example.com", "admin", "u1")
        invitation_accept(fake, inv1["token"], "user-2")
        inv2 = invitation_mint(fake, "team-1", "bob@example.com", "member", "u1")
        assert inv2["token"] != inv1["token"]
        assert len(fake.tables["invitations"]) == 2

    def test_mint_validates_role_and_email(self, fake):
        """0008 CHECK closes role to admin|member (owner is NOT invitable)."""
        with pytest.raises(InvitationError) as ei:
            invitation_mint(fake, "team-1", "bob@example.com", "owner", "u1")
        assert ei.value.status == 422
        with pytest.raises(InvitationError):
            invitation_mint(fake, "team-1", "not-an-email", "member", "u1")

    # ── accept ──────────────────────────────────────────────────────────

    def test_accept_creates_membership_with_invited_role(self, fake):
        """E2E-3 core: mint → lookup_hash verify → accept → REAL membership
        carrying the INVITED role; pending invite consumed."""
        inv = invitation_mint(fake, "team-1", "bob@example.com", "admin",
                              "owner-1")
        result = invitation_accept(fake, inv["token"], "user-2")
        assert result == {"team_id": "team-1", "role": "admin"}

        mem = fake.tables["team_memberships"]
        assert len(mem) == 1
        assert mem[0]["user_id"] == "user-2"
        assert mem[0]["team_id"] == "team-1"
        assert mem[0]["role"] == "admin"  # invited role preserved (O/I/T)
        assert mem[0]["status"] == "active"
        assert mem[0]["invited_email"] == "bob@example.com"

        inv_row = fake.tables["invitations"][0]
        assert inv_row["status"] == "accepted"  # pending invite consumed
        assert inv_row["accepted_at"] is not None

    def test_accept_role_member_preserved(self, fake):
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        invitation_accept(fake, inv["token"], "user-2")
        assert fake.tables["team_memberships"][0]["role"] == "member"

    def test_accept_unknown_token_rejected(self, fake):
        invitation_mint(fake, "team-1", "bob@example.com", "member", "u1")
        with pytest.raises(InvitationError, match="Invalid or expired"):
            invitation_accept(fake, "not-the-token", "user-2")

    def test_accept_expired_rejected(self, fake):
        """E2E-3: expiry enforced (expires_at <= now → rejected)."""
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        fake.tables["invitations"][0]["expires_at"] = past
        with pytest.raises(InvitationError, match="expired"):
            invitation_accept(fake, inv["token"], "user-2")
        assert fake.tables["team_memberships"] == []

    def test_accept_used_invite_rejected(self, fake):
        """E2E-3: a used invite cannot be re-accepted."""
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        invitation_accept(fake, inv["token"], "user-2")
        with pytest.raises(InvitationError, match="accepted"):
            invitation_accept(fake, inv["token"], "user-3")
        assert len(fake.tables["team_memberships"]) == 1  # no double join

    def test_accept_revoked_invite_rejected(self, fake):
        """E2E-3: a revoked invite cannot be accepted."""
        self._owner(fake)
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        invitation_rescind(fake, inv["id"], "team-1", "owner-1")
        with pytest.raises(InvitationError, match="revoked"):
            invitation_accept(fake, inv["token"], "user-2")
        # no membership created for the invitee (owner-1's row is the actor)
        assert all(m["user_id"] != "user-2"
                   for m in fake.tables["team_memberships"])

    def test_accept_rejects_when_already_active_member(self, fake):
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        fake.seed("team_memberships", [{
            "user_id": "user-2", "team_id": "team-1",
            "role": "member", "status": "active"}])
        with pytest.raises(InvitationError) as ei:
            invitation_accept(fake, inv["token"], "user-2")
        assert ei.value.status == 409

    def test_accept_resurrects_removed_membership(self, fake):
        """A previously removed member re-joining is resurrected in place
        (registry MERGE semantics — uq_member_team would reject a second
        row)."""
        inv = invitation_mint(fake, "team-1", "bob@example.com", "admin",
                              "owner-1")
        fake.seed("team_memberships", [{
            "id": "mem-1", "user_id": "user-2", "team_id": "team-1",
            "role": "member", "status": "removed"}])
        invitation_accept(fake, inv["token"], "user-2")
        rows = fake.tables["team_memberships"]
        assert len(rows) == 1
        assert rows[0]["status"] == "active"
        assert rows[0]["role"] == "admin"  # invited role wins

    def test_accept_email_mismatch_403(self, fake):
        """Invitee must be the invitee's account (JWT email guard, #574)."""
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        with pytest.raises(InvitationError) as ei:
            invitation_accept(fake, inv["token"], "user-2",
                              user_email="mallory@example.com")
        assert ei.value.status == 403
        assert fake.tables["team_memberships"] == []

    def test_accept_email_guard_skipped_without_jwt_email(self, fake):
        """No email claim in the JWT → no guard (mirrors registry path)."""
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        invitation_accept(fake, inv["token"], "user-2", user_email=None)
        assert len(fake.tables["team_memberships"]) == 1

    def test_accept_402_when_team_at_member_cap(self, fake):
        """Quota/tier gate parity with the registry accept path (code-review
        P2, PR #864): a free-tier team at its 1-user cap rejects with 402."""
        # free tier: max_users=1; the owner already occupies the slot
        for t in fake.tables["teams"]:
            if t["id"] == "team-1":
                t.update({"tier": "free", "max_users": 1})
        self._owner(fake)
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        with pytest.raises(InvitationError) as ei:
            invitation_accept(fake, inv["token"], "user-2")
        assert ei.value.status == 402
        # invite still pending — the gate fires BEFORE consumption
        assert fake.tables["invitations"][0]["status"] == "pending"

    def test_accept_compensates_invite_when_membership_write_fails(self, fake):
        """Burn-window fix (code-review P2, PR #864): if the membership write
        fails after the invite was consumed, the invite rolls back to pending
        so the invitee can retry."""
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        fake.seed("teams", [{"id": "team-1", "name": "T", "tier": "free",
                              "max_users": 10, "graph_name": "team_team-1"}])
        # Fail the membership POST only (first failing call after accept)
        class _FlakyAfterAccept(FakeControlPlane):
            def __init__(self, base):
                self._base = base
                self._fail_next_membership = False

            def query(self, table, **kw):
                if table == "team_memberships" and kw.get("method") == "POST":
                    self._fail_next_membership = True
                    raise RuntimeError("simulated membership write failure")
                return self._base.query(table, **kw)

        flaky = _FlakyAfterAccept(fake)
        with pytest.raises(RuntimeError):
            invitation_accept(flaky, inv["token"], "user-2")
        # compensating rollback: invite is pending again, not burned
        row = fake.tables["invitations"][0]
        assert row["status"] == "pending"
        assert row["accepted_at"] is None

    def test_rescind_loses_race_to_concurrent_accept_409(self, fake):
        """Race fix (code-review P2, PR #864): a rescind racing a concurrent
        accept must not flip a consumed invite to revoked — 409 instead."""
        self._owner(fake)
        fake.seed("teams", [{"id": "team-1", "name": "T", "tier": "free",
                              "max_users": 10, "graph_name": "team_team-1"}])
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        # simulate: accept consumed the invite between rescind's read + PATCH
        class _AcceptMidRace(FakeControlPlane):
            def __init__(self, base):
                self._base = base

            def query(self, table, **kw):
                # on the rescind PATCH, consume first (the concurrent accept)
                if table == "invitations" and kw.get("method") == "PATCH" \
                        and kw.get("json_body", {}).get("status") == "revoked":
                    for r in self._base.tables["invitations"]:
                        if r["id"] == inv["id"]:
                            r["status"] = "accepted"
                return self._base.query(table, **kw)

        racer = _AcceptMidRace(fake)
        with pytest.raises(InvitationError) as ei:
            invitation_rescind(racer, inv["id"], "team-1", "owner-1")
        assert ei.value.status == 409
        assert fake.tables["invitations"][0]["status"] == "accepted"

    def test_mint_concurrent_duplicate_maps_to_409(self, fake):
        """Concurrent dedup (code-review P2, PR #864): when the partial
        unique index rejects the loser (PostgREST HTTP 409), mint surfaces
        InvitationError(409) — not a 500."""
        class _UniqueViolation(FakeControlPlane):
            def __init__(self, base):
                self._base = base

            def query(self, table, **kw):
                if table == "invitations" and kw.get("method") == "POST":
                    raise RuntimeError(
                        "Supabase control-plane query failed (invitations): "
                        "HTTP 409 (duplicate key value violates unique "
                        "constraint uq_invitations_team_email_pending)")
                return self._base.query(table, **kw)

        with pytest.raises(InvitationError) as ei:
            invitation_mint(_UniqueViolation(fake), "team-1",
                            "bob@example.com", "member", "owner-1")
        assert ei.value.status == 409

    # ── rescind ─────────────────────────────────────────────────────────

    def test_rescind_owner_admin_only(self, fake):
        self._owner(fake)
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        with pytest.raises(InvitationError) as ei:
            invitation_rescind(fake, inv["id"], "team-1", "outsider")
        assert ei.value.status == 403
        assert fake.tables["invitations"][0]["status"] == "pending"

    def test_rescind_sets_revoked(self, fake):
        self._owner(fake)
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        result = invitation_rescind(fake, inv["id"], "team-1", "owner-1")
        assert result == {"revoked": True, "invitation_id": inv["id"]}
        assert fake.tables["invitations"][0]["status"] == "revoked"

    def test_rescind_idempotent_for_already_revoked(self, fake):
        self._owner(fake)
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        invitation_rescind(fake, inv["id"], "team-1", "owner-1")
        again = invitation_rescind(fake, inv["id"], "team-1", "owner-1")
        assert again["already"] is True

    def test_rescind_rejects_accepted_invite(self, fake):
        """A used invite cannot be rescinded — the membership already exists."""
        self._owner(fake)
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        invitation_accept(fake, inv["token"], "user-2")
        with pytest.raises(InvitationError) as ei:
            invitation_rescind(fake, inv["id"], "team-1", "owner-1")
        assert ei.value.status == 409

    def test_rescind_unknown_or_other_team_404(self, fake):
        self._owner(fake)
        inv = invitation_mint(fake, "team-1", "bob@example.com", "member",
                              "owner-1")
        # an owner of ANOTHER team cannot see this team's invite → 404
        # (role check passes for team-OTHER, but the id is not scoped there)
        self._owner(fake, team_id="team-OTHER")
        with pytest.raises(InvitationError) as ei:
            invitation_rescind(fake, inv["id"], "team-OTHER", "owner-1")
        assert ei.value.status == 404
        with pytest.raises(InvitationError) as ei:
            invitation_rescind(fake, "no-such-id", "team-1", "owner-1")
        assert ei.value.status == 404

    # ── list ────────────────────────────────────────────────────────────

    def test_pending_invitations_lists_only_pending(self, fake):
        inv = invitation_mint(fake, "team-1", "bob@example.com", "admin",
                              "u1")
        invitation_mint(fake, "team-1", "carol@example.com", "member", "u1")
        invitation_mint(fake, "team-2", "other@example.com", "member", "u1")
        used = invitation_mint(fake, "team-1", "dave@example.com", "member",
                               "u1")
        invitation_accept(fake, used["token"], "user-2")
        rows = pending_invitations(fake, "team-1")
        assert [r["email"] for r in rows] == ["bob@example.com", "carol@example.com"]
        assert all(r["status"] == "pending" for r in rows)
        assert inv["id"] in [r["id"] for r in rows]

    # ── fail-closed ─────────────────────────────────────────────────────

    def test_invitation_seam_fail_closed_on_query_error(self):
        """P1-3: a control-plane error RAISES (RuntimeError) — mint/accept
        must never silently succeed or fall back to the registry."""
        cp = ErrorControlPlane()
        with pytest.raises(RuntimeError):
            invitation_mint(cp, "team-1", "bob@example.com", "member", "u1")
        with pytest.raises(RuntimeError):
            invitation_accept(cp, "some-token", "user-2")
        with pytest.raises(RuntimeError):
            invitation_rescind(cp, "inv-1", "team-1", "owner-1")
# ── Onboarding / email / GitHub connect (plan Task 6, issue #764) ─────────

class TestOnboardingState:
    """teams.onboarding_state (jsonb) read-patch via the seam (E2E-5)."""

    def _set_state(self, fake, state):
        fake.tables["teams"][0]["onboarding_state"] = state

    def test_read_returns_merged_defaults_for_empty_state(self, fake):
        """A team row with empty onboarding_state reads as the full hosted
        default shape (registry auto-initialize parity)."""
        self._set_state(fake, {})
        state = team_onboarding_state(fake, "team-free-001")
        assert state == {
            "github_connected": False, "github_indexed": False,
            "demo_created": False, "session_recording": False,
            "team_created": False, "prompt_pasted": False,
            "onboarding_complete": False,
        }

    def test_read_merges_partial_state_over_defaults(self, fake):
        self._set_state(fake, {"demo_created": True})
        state = team_onboarding_state(fake, "team-free-001")
        assert state["demo_created"] is True
        assert state["team_created"] is False  # default preserved

    def test_read_preserves_unknown_keys(self, fake):
        """Unknown stored keys are PRESERVED on the merge — registry parity
        (code-review P2, PR #861): dropping them would let a later write-back
        permanently erase keys the whitelist doesn't know (e.g.
        completed_at / github_index_job_id)."""
        self._set_state(fake, {"not_a_field": 1})
        state = team_onboarding_state(fake, "team-free-001")
        assert state["not_a_field"] == 1

    def test_read_missing_team_returns_none(self, fake):
        assert team_onboarding_state(fake, "no-such-team") is None

    def test_patch_round_trip_no_string_wrapping(self, fake):
        """PATCH stores the dict directly (jsonb — migration 0006), unlike
        the registry path's JSON-string wrapping."""
        self._set_state(fake, {})
        update_onboarding_state(fake, "team-free-001", {"demo_created": True})
        stored = fake.tables["teams"][0]["onboarding_state"]
        assert isinstance(stored, dict)  # jsonb object, NOT a string
        assert stored == {"demo_created": True}
        # and the read back merges over defaults

    def test_email_read_patch_round_trip(self, fake):
        """E2E-5: team email read-patch from teams (wired via the onboarding
        endpoints — #764 review P2: the email seam must not be dead code)."""
        fake.tables["teams"][0]["email"] = None  # fixture has none set
        assert team_email(fake, "team-free-001") is None
        update_team_email(fake, "team-free-001", "owner@premise-labs.dev")
        assert team_email(fake, "team-free-001") == "owner@premise-labs.dev"
        # missing team → None (no exception)
        assert team_email(fake, "no-such-team") is None

    def test_fail_closed_on_error(self):
        with pytest.raises(RuntimeError):
            team_onboarding_state(ErrorControlPlane(), "team-free-001")
        with pytest.raises(RuntimeError):
            update_onboarding_state(ErrorControlPlane(), "team-free-001", {})


class TestTeamEmail:
    """teams.email read-patch via the seam (E2E-5)."""

    def test_read_and_patch_round_trip(self, fake):
        fake.tables["teams"][0]["email"] = None
        assert team_email(fake, "team-free-001") is None
        update_team_email(fake, "team-free-001", "owner@example.com")
        assert team_email(fake, "team-free-001") == "owner@example.com"

    def test_read_missing_team_returns_none(self, fake):
        assert team_email(fake, "no-such-team") is None

    def test_fail_closed_on_error(self):
        with pytest.raises(RuntimeError):
            team_email(ErrorControlPlane(), "team-free-001")
        with pytest.raises(RuntimeError):
            update_team_email(ErrorControlPlane(), "team-free-001", "a@b.co")


class TestGithubCredentials:
    """github_token_enc/github_org via the service-role seam (E2E-5).

    github_token_enc is column-REVOKEd from anon/authenticated (0006) — the
    seam is the ONLY read/write path in Supabase mode."""

    def test_store_then_read_round_trip(self, fake):
        fake.tables["teams"][0].update(
            {"github_token_enc": None, "github_org": None})
        store_github_credentials(
            fake, "team-free-001", token_enc="enc-blob", org="acme")
        got = github_credentials(fake, "team-free-001")
        assert got == {"github_token_enc": "enc-blob", "github_org": "acme"}

    def test_rotation_overwrites_in_place(self, fake):
        """Re-connecting PATCHes a fresh encrypted token over the old one —
        rotation is the reconnect itself (plan Task 6 'rotation documented')."""
        fake.tables["teams"][0].update(
            {"github_token_enc": "old", "github_org": "acme"})
        store_github_credentials(
            fake, "team-free-001", token_enc="new", org="acme")
        assert github_credentials(fake, "team-free-001") == {
            "github_token_enc": "new", "github_org": "acme"}

    def test_missing_team_returns_none_creds(self, fake):
        assert github_credentials(fake, "no-such-team") == {
            "github_token_enc": None, "github_org": None}

    def test_fail_closed_on_error(self):
        with pytest.raises(RuntimeError):
            github_credentials(ErrorControlPlane(), "team-free-001")
        with pytest.raises(RuntimeError):
            store_github_credentials(ErrorControlPlane(), "team-free-001",
                                     token_enc="x", org="acme")


# ── Fake adapter semantics (query dialect parity) ───────────────────────────

class TestFakeControlPlane:
    def test_eq_neq_is_filters(self):
        cp = FakeControlPlane({"t": [
            {"a": 1, "b": None}, {"a": 2, "b": "x"}, {"a": 1, "b": "y"},
        ]})
        assert cp.query("t", filters=[("a", "eq", 1)]) == [
            {"a": 1, "b": None}, {"a": 1, "b": "y"}]
        assert cp.query("t", filters=[("a", "neq", 1)]) == [{"a": 2, "b": "x"}]
        assert cp.query("t", filters=[("b", "is", None)]) == [{"a": 1, "b": None}]
        assert cp.query("t", filters=[("a", "eq", 1)], select=["a"]) == [
            {"a": 1}, {"a": 1}]

    def test_gt_lt_filters_null_excluding(self):
        """#765 dialect: gt/lt mirror SQL NULL semantics — a NULL column
        never matches an ordered comparison."""
        cp = FakeControlPlane({"t": [
            {"a": 1, "b": None}, {"a": 2, "b": "x"}, {"a": 3, "b": "y"},
        ]})
        assert cp.query("t", filters=[("a", "gt", 1)]) == [
            {"a": 2, "b": "x"}, {"a": 3, "b": "y"}]
        assert cp.query("t", filters=[("a", "lt", 3)]) == [
            {"a": 1, "b": None}, {"a": 2, "b": "x"}]
        # NULL b never matches gt/lt
        assert cp.query("t", filters=[("b", "gt", "a")]) == [
            {"a": 2, "b": "x"}, {"a": 3, "b": "y"}]
        assert cp.query("t", filters=[("b", "lt", "z")]) == [
            {"a": 2, "b": "x"}, {"a": 3, "b": "y"}]

    def test_patch_and_post(self):
        cp = FakeControlPlane({"t": [{"id": "k1", "x": None}]})
        assert cp.query("t", method="PATCH", filters=[("id", "eq", "k1")],
                        json_body={"x": 1}) == []
        assert cp.tables["t"][0]["x"] == 1
        row = cp.query("t", method="POST", json_body={"id": "k2"})
        assert row == [{"id": "k2"}]
        assert len(cp.tables["t"]) == 2

    def test_rpc_records_calls(self):
        cp = FakeControlPlane()
        cp.rpc("some_fn", {"a": 1})
        assert cp.rpc_calls == [("some_fn", {"a": 1})]


# ── Task 8 seam helpers (#765 writer/reader inventory) ───────────────────────

class TestTask8Helpers:
    """The migrated writers/readers' seam helpers (plan Task 8)."""

    def test_provision_team_via_rpc(self, fake):
        """provision_team() routes through the RPC and lands teams +
        membership + api_keys rows (fake mirrors the 0010 SQL)."""
        from tortoise.auth import lookup_hash
        key = "tt_provision_test_key_0000000000001"
        provision_team(fake, **{
            "p_user_id": None, "p_identity": "anon-abc123",
            "p_team_id": "team-new-1", "p_team_name": "agent-new",
            "p_api_key": key, "p_key_hash": "salt:hash",
            "p_lookup_hash": lookup_hash(key),
            "p_graph_name": "team_team-new-1",
            "p_tier": "free", "p_max_users": 1, "p_max_graphs": 1,
            "p_ops_allowance": 10000, "p_graph_size_cap": 10000,
        })
        assert fake.rpc_calls[0][0] == "provision_team"
        assert any(t["id"] == "team-new-1" for t in fake.tables["teams"])
        mem = [m for m in fake.tables["team_memberships"]
               if m["team_id"] == "team-new-1"]
        assert len(mem) == 1
        assert mem[0]["user_id"] is None
        assert mem[0]["identity"] == "anon-abc123"
        assert mem[0]["role"] == "owner" and mem[0]["status"] == "active"
        keys = [k for k in fake.tables["api_keys"]
                if k["team_id"] == "team-new-1"]
        assert len(keys) == 1
        assert keys[0]["lookup_hash"] == lookup_hash(key)
        assert keys[0]["created_via"] == "provisioned"
        assert keys[0]["id"] == f"key_team-new-1_{lookup_hash(key)[:12]}"

    def test_provision_team_idempotent(self, fake):
        """Re-invocation is an idempotent upsert (0010 ON CONFLICT) — no
        duplicate rows, key material refreshed."""
        from tortoise.auth import lookup_hash
        key = "tt_provision_test_key_0000000000002"
        params = {
            "p_user_id": None, "p_identity": "anon-abc123",
            "p_team_id": "team-new-2", "p_team_name": "agent-new",
            "p_api_key": key, "p_key_hash": "salt:hash",
            "p_lookup_hash": lookup_hash(key),
            "p_graph_name": "team_team-new-2", "p_tier": "free",
            "p_max_users": 1, "p_max_graphs": 1,
            "p_ops_allowance": 10000, "p_graph_size_cap": 10000,
        }
        provision_team(fake, **params)
        key2 = "tt_provision_test_key_0000000000003"
        params["p_api_key"] = key2
        params["p_lookup_hash"] = lookup_hash(key2)
        params["p_key_hash"] = "salt2:hash"
        provision_team(fake, **params)
        assert len([t for t in fake.tables["teams"]
                    if t["id"] == "team-new-2"]) == 1
        assert len([m for m in fake.tables["team_memberships"]
                    if m["team_id"] == "team-new-2"]) == 1
        # rotated key lands as a SECOND api_keys row (multi-key valid; free
        # tier caps at 2) — matching 0010's ON CONFLICT (lookup_hash)
        assert len([k for k in fake.tables["api_keys"]
                    if k["team_id"] == "team-new-2"]) == 2

    def test_provision_team_fail_closed_on_rpc_error(self):
        with pytest.raises(RuntimeError):
            provision_team(ErrorControlPlane(), p_team_id="t")

    def test_team_by_email_and_name(self, fake):
        fake.seed("teams", [{"id": "t1", "name": "acme",
                              "email": "a@example.com"}])
        assert team_by_email(fake, "a@example.com") == {"id": "t1"}
        assert team_by_email(fake, "nope@example.com") is None
        assert team_by_name(fake, "acme") == {"id": "t1"}
        assert team_by_name(fake, "other") is None

    def test_team_api_keys_all_rows_newest_first(self, fake):
        fake.seed("api_keys", [
            _key_row(id="k1", created_at="2026-07-01T00:00:00Z"),
            _key_row(id="k2", created_at="2026-08-01T00:00:00Z"),
            _key_row(id="k3", created_at="2026-08-02T00:00:00Z",
                     revoked_at="2026-08-03T00:00:00Z"),
            _key_row(id="k4", team_id="team-other"),
        ])
        rows = team_api_keys(fake, "team-free-001")
        assert [r["id"] for r in rows] == ["k3", "k2", "k1"]
        assert rows[0]["revoked_at"] == "2026-08-03T00:00:00Z"  # revoked shown
        assert {"id", "key_prefix", "created_at", "last_used_at",
                "revoked_at"} <= set(rows[0])

    def test_api_key_by_id(self, fake):
        fake.seed("api_keys", [_key_row()])
        row = api_key_by_id(fake, "key-001")
        # #1148: api_key_by_id now returns created_via + enabled for the
        # key-toggle guard (bootstrap keys can't be toggled).
        assert row["team_id"] == "team-free-001"
        assert row["revoked_at"] is None
        assert row["created_via"] == "bootstrap"
        assert row["enabled"] is None
        assert api_key_by_id(fake, "missing") is None

    def test_membership_count_since(self, fake):
        """Rate-limit counts: gt cutoff + anchor match, NULL-anchored rows
        excluded."""
        recent = "2026-08-01T00:00:00Z"
        fake.seed("team_memberships", [
            {"id": "m1", "user_id": "u1", "identity": None,
             "created_at": "2026-08-02T00:00:00Z"},
            {"id": "m2", "user_id": "u1", "identity": None,
             "created_at": "2026-07-01T00:00:00Z"},  # old — excluded
            {"id": "m3", "user_id": None, "identity": "anon-x",
             "created_at": "2026-08-03T00:00:00Z"},
            {"id": "m4", "user_id": "u1", "identity": None,
             "created_at": None},  # NULL — excluded (SQL semantics)
        ])
        assert membership_count_since(
            fake, cutoff=recent, user_id="u1") == 1
        assert membership_count_since(
            fake, cutoff=recent, identity="anon-x") == 1
        assert membership_count_since(
            fake, cutoff=recent, identity="anon-other") == 0

    def test_team_members_active_and_invited_with_identity(self, fake):
        fake.seed("team_memberships", [
            {"id": "m1", "user_id": "u1", "team_id": "team-free-001",
             "identity": None, "role": "owner", "status": "active",
             "invited_email": None},
            {"id": "m2", "user_id": None, "team_id": "team-free-001",
             "identity": "anon-abc", "role": "member", "status": "active",
             "invited_email": None},
            {"id": "m3", "user_id": "u2", "team_id": "team-free-001",
             "identity": None, "role": "member", "status": "invited",
             "invited_email": "bob@example.com"},
            {"id": "m4", "user_id": "u3", "team_id": "team-free-001",
             "identity": None, "role": "member", "status": "removed",
             "invited_email": None},
            {"id": "m5", "user_id": "u4", "team_id": "team-other",
             "identity": None, "role": "member", "status": "active",
             "invited_email": None},
        ])
        rows = team_members(fake, "team-free-001")
        assert len(rows) == 3  # removed + other-team excluded
        # identity rows surface their anon anchor as user_id (round-trip)
        by_id = {r["user_id"]: r for r in rows}
        assert by_id["anon-abc"]["role"] == "member"
        assert by_id["u2"]["email"] == "bob@example.com"

    def test_membership_role_and_set_membership(self, fake):
        fake.seed("team_memberships", [
            {"id": "m1", "user_id": "u1", "team_id": "team-free-001",
             "identity": None, "role": "owner", "status": "active",
             "invited_email": None},
            {"id": "m2", "user_id": None, "team_id": "team-free-001",
             "identity": "anon-abc", "role": "member", "status": "active",
             "invited_email": None},
        ])
        assert membership_role(fake, "team-free-001", "u1") == "owner"
        # identity rows match by their anchor
        assert membership_role(fake, "team-free-001", "anon-abc") == "member"
        assert membership_role(fake, "team-free-001", "ghost") is None
        set_membership(fake, "team-free-001", "anon-abc", status="removed")
        assert fake.tables["team_memberships"][1]["status"] == "removed"
        set_membership(fake, "team-free-001", "u1", role="admin")
        assert fake.tables["team_memberships"][0]["role"] == "admin"

    def test_expired_bootstrap_keys(self, fake):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        fake.seed("api_keys", [
            _key_row(id="e1", created_via="bootstrap", expires_at=past),
            _key_row(id="e2", created_via="bootstrap", expires_at=past,
                     revoked_at="2026-01-01T00:00:00Z"),  # already revoked
            _key_row(id="e3", created_via="recovery", expires_at=past),
            _key_row(id="e4", created_via="bootstrap", expires_at=None),
            _key_row(id="e5", created_via="bootstrap", expires_at=future),
        ])
        got = expired_bootstrap_keys(fake, datetime.now(timezone.utc).isoformat())
        assert [r["id"] for r in got] == ["e1"]

    def test_graph_metadata_derives_default(self, fake):
        fake.tables["teams"][0]["graph_name"] = "team_team-free-001"
        assert graph_metadata(fake, "team-free-001") == [{
            "graph_id": "default", "name": "default", "kind": "default",
            "namespace": "team_team-free-001"}]
        assert graph_metadata(fake, "no-such-team") == []
        assert graph_metadata(fake, "team-free-001")[0]["graph_id"] == "default"

    def test_seam_helpers_fail_closed(self):
        cp = ErrorControlPlane()
        for fn in (team_api_keys, api_key_by_id, team_members,
                   expired_bootstrap_keys, graph_metadata):
            with pytest.raises(RuntimeError):
                fn(cp, "team-x")
        with pytest.raises(RuntimeError):
            team_by_email(cp, "a@b.co")
        with pytest.raises(RuntimeError):
            team_by_name(cp, "acme")
        with pytest.raises(RuntimeError):
            membership_count_since(cp, cutoff="2026-01-01", user_id="u")
        with pytest.raises(RuntimeError):
            membership_role(cp, "team-x", "u")
        with pytest.raises(RuntimeError):
            set_membership(cp, "team-x", "u", status="removed")


# ── Constructor sanity ──────────────────────────────────────────────────────

class TestClientConstruction:
    def test_requires_config(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        from tortoise.supabase_control import SupabaseControlPlane
        with pytest.raises(RuntimeError):
            SupabaseControlPlane()

    def test_get_control_plane_singleton(self, monkeypatch):
        monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc")
        cp = get_control_plane()
        assert cp is get_control_plane()

    def test_real_client_survives_multiple_queries(self):
        """The REAL httpx client must serve 2+ queries on one instance — the
        persistent-client regression (re-review P0, PR #851): `with client:`
        on an externally-constructed httpx.Client CLOSES it on exit, so the
        second resolve_api_key query raised "Cannot reopen a client" and
        every auth resolution died. The FakeControlPlane cannot catch this
        class of bug, so this test drives a local HTTP server with the real
        client."""
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        calls = {"n": 0}

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                calls["n"] += 1
                body = json.dumps([{"id": "team-1"}]).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):  # silence
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            from tortoise.supabase_control import SupabaseControlPlane
            cp = SupabaseControlPlane(
                url=f"http://127.0.0.1:{server.server_port}",
                service_key="svc",
            )
            # resolve_api_key makes 2-3 queries per auth — the second one
            # must not fail on a persistent client.
            r1 = cp.query("teams", select=["id"], filters=[("id", "eq", "team-1")])
            r2 = cp.query("teams", select=["id"], filters=[("id", "eq", "team-1")])
            assert r1 == [{"id": "team-1"}] and r2 == [{"id": "team-1"}]
            assert calls["n"] == 2
        finally:
            server.shutdown()
            server.server_close()

    def test_real_client_rpc_persistent_and_fail_closed(self):
        """#765: rpc() uses the SAME persistent httpx client (no `with
        client:` close hazard) and fails closed on non-2xx (RuntimeError,
        never a silent pass)."""
        import json
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        calls = {"n": 0}

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                calls["n"] += 1
                if self.path.startswith("/rest/v1/rpc/provision_team"):
                    body = b""
                    self.send_response(204)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    body = json.dumps({"message": "boom"}).encode()
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, *a):  # silence
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            from tortoise.supabase_control import SupabaseControlPlane
            cp = SupabaseControlPlane(
                url=f"http://127.0.0.1:{server.server_port}",
                service_key="svc",
            )
            # two RPC calls on one persistent client (the #851 regression
            # class: `with client:` would close it after the first call)
            assert cp.rpc("provision_team", {"a": 1}) is None
            assert cp.rpc("provision_team", {"a": 2}) is None
            assert calls["n"] == 2
            # fail-closed: non-2xx raises RuntimeError
            with pytest.raises(RuntimeError, match="HTTP 500"):
                cp.rpc("no_such_fn")
            # and the client still works after the failure
            assert cp.rpc("provision_team", {"a": 3}) is None
        finally:
            server.shutdown()
            server.server_close()


# ── Metering (post-#669 flip — PR #911) ─────────────────────────────────────

class TestMeteringSeam:
    """metering_records read/increment via the seam (the registry path is
    deleted post-flip; these cover the Supabase branches the reviewer noted
    had zero direct tests)."""

    def test_metering_get_absent_is_zero(self):
        from tortoise.supabase_control import metering_get
        from tests.fake_control_plane import FakeControlPlane

        fake = FakeControlPlane({"metering_records": []})
        assert metering_get(fake, "team-1", "2026-08") == 0

    def test_metering_increment_creates_and_reads_back(self):
        from tortoise.supabase_control import metering_get, metering_increment
        from tests.fake_control_plane import FakeControlPlane

        fake = FakeControlPlane({"metering_records": []})
        n = metering_increment(fake, "team-1", "2026-08", 3)
        assert n == 3
        assert metering_get(fake, "team-1", "2026-08") == 3
        # increment again → 5
        assert metering_increment(fake, "team-1", "2026-08", 2) == 5
        # different period isolated
        assert metering_get(fake, "team-1", "2026-07") == 0

    def test_metering_rpc_called_with_args(self):
        """The atomic increment goes through the RPC path (not GET-PATCH)."""
        from tortoise.supabase_control import metering_increment
        from tests.fake_control_plane import FakeControlPlane

        class _Spy(FakeControlPlane):
            def __init__(self):
                super().__init__({"metering_records": []})
                self.rpc_calls = []

            def rpc(self, fn, body):
                self.rpc_calls.append((fn, body))
                # emulate the SQL function: upsert + increment
                rows = self.tables["metering_records"]
                row = next((r for r in rows
                            if r["team_id"] == body["p_team_id"]
                            and r["period"] == body["p_period"]), None)
                if row:
                    row["write_ops"] += body["p_n"]
                else:
                    rows.append({"team_id": body["p_team_id"],
                                 "period": body["p_period"],
                                 "write_ops": body["p_n"]})
                return None  # PostgREST minimal — no echo

        spy = _Spy()
        assert metering_increment(spy, "team-1", "2026-08", 2) == 2
        assert metering_increment(spy, "team-1", "2026-08", 4) == 6
        # #953: the RPC body carries p_nodes_written (epic #909 W-4 commit
        # cost driver; default 0 on plain increments).
        assert spy.rpc_calls == [
            ("metering_increment", {"p_team_id": "team-1",
                                    "p_period": "2026-08", "p_n": 2,
                                    "p_nodes_written": 0}),
            ("metering_increment", {"p_team_id": "team-1",
                                    "p_period": "2026-08", "p_n": 4,
                                    "p_nodes_written": 0}),
        ]

    def test_metering_increment_passes_nodes_written(self):
        """#953: a non-zero nodes_written flows through to the RPC body."""
        from tortoise.supabase_control import metering_increment
        from tests.fake_control_plane import FakeControlPlane

        class _Spy(FakeControlPlane):
            def __init__(self):
                super().__init__({"metering_records": []})
                self.rpc_calls = []

            def rpc(self, fn, body):
                self.rpc_calls.append((fn, body))
                return None

        spy = _Spy()
        metering_increment(spy, "team-1", "2026-08", 3, nodes_written=5)
        assert spy.rpc_calls == [
            ("metering_increment", {"p_team_id": "team-1",
                                    "p_period": "2026-08", "p_n": 3,
                                    "p_nodes_written": 5}),
        ]

    def test_metering_increment_readback_failure_returns_delta(self):
        """#925: the atomic RPC committed but the read-back fails (network
        blip) → return the known delta instead of raising. The stored
        counter is correct server-side; only the current total is unknown.
        A raising read-back would make record_write_ops return None and a
        caller retry would double-count."""
        from tortoise.supabase_control import metering_increment
        from tests.fake_control_plane import FakeControlPlane

        class _ReadbackFails(FakeControlPlane):
            """rpc() succeeds (SQL increment emulated); the subsequent
            metering_records read-back query raises."""

            def __init__(self):
                super().__init__({"metering_records": []})
                self.fail_readback = False

            def rpc(self, fn, body):
                result = super().rpc(fn, body)
                self.fail_readback = True
                return result

            def query(self, table, *args, **kwargs):
                if self.fail_readback:
                    raise RuntimeError("read-back failed (simulated blip)")
                return super().query(table, *args, **kwargs)

        fake = _ReadbackFails()
        n = metering_increment(fake, "team-1", "2026-08", 3)
        assert n == 3
        # the atomic increment really did land server-side
        assert fake.tables["metering_records"][0]["write_ops"] == 3

    def test_metering_increment_rpc_failure_still_raises(self):
        """#925: the RPC call itself is NOT best-effort — if the atomic
        write genuinely failed, metering_increment raises (record_write_ops
        turns that into a logged None; nobody retries a failed write)."""
        import pytest
        from tortoise.supabase_control import metering_increment
        from tests.fake_control_plane import ErrorControlPlane

        with pytest.raises(RuntimeError):
            metering_increment(ErrorControlPlane(), "team-1", "2026-08", 1)


# ── resolve_team_limits Supabase mode (PR #911 review P2) ───────────────────

class TestResolveTeamLimitsSupabase:
    def test_supabase_mode_never_touches_registry(self, monkeypatch):
        """resolve_team_limits reads the teams row via the seam in Supabase
        mode; a registry-namespaced SDK must NOT be constructed."""
        import tortoise.quota as q
        from tests.fake_control_plane import FakeControlPlane

        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_key")
        import tortoise.supabase_control as sc
        fake = FakeControlPlane({"teams": [{"id": "team-1", "tier": "free",
                                             "max_users": 1, "max_graphs": 1,
                                             "graph_size_cap": 10000,
                                             "ops_allowance": 1000}]})
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)

        class _Boom:
            def _get_registry(self):
                raise AssertionError("registry touched in Supabase mode")

        monkeypatch.setattr(q, "_make_sdk", lambda **kw: _Boom())
        limits = q.resolve_team_limits("team-1")
        assert limits["team_id"] == "team-1"
        assert limits["tier"] == "free"
        assert limits["max_users"] == 1
        assert limits["max_points"] == 10000

    def test_supabase_mode_preserves_none_as_unlimited(self, monkeypatch):
        """NULL max_users/max_graphs = UNLIMITED (registry parity, PR #911
        review P2) — never substitute pricing defaults."""
        import tortoise.quota as q
        from tests.fake_control_plane import FakeControlPlane

        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_key")
        import tortoise.supabase_control as sc
        fake = FakeControlPlane({"teams": [{"id": "team-1", "tier": "team",
                                             "max_users": None,
                                             "max_graphs": None,
                                             "graph_size_cap": 500000}]})
        monkeypatch.setattr(sc, "get_control_plane", lambda: fake)
        limits = q.resolve_team_limits("team-1")
        assert limits["max_users"] is None  # unlimited, not pricing default
        assert limits["max_graphs"] is None
        assert limits["max_points"] == 500000


# ── Claim seam (#1082, PR1 — 20260813000004) ────────────────────────────────
# TestClaimSeam exercises the Python claim_membership wrapper + is_anon_team
# over the FakeControlPlane emulation (which mirrors the SQL RPC semantics).


def _provision_anon_team(fake: FakeControlPlane, *, team_id: str, identity: str,
                         api_key: str, lookup: str | None = None,
                         email: str | None = None) -> None:
    """Provision an anonymous (NULL user_id) team via the fake RPC."""
    provision_team(fake, **{
        "p_user_id": None, "p_identity": identity, "p_team_id": team_id,
        "p_team_name": f"Anon {team_id}", "p_api_key": api_key,
        "p_key_hash": "salt:hash",
        "p_lookup_hash": lookup or lookup_hash(api_key),
        "p_graph_name": f"team_{team_id}", "p_email": email,
        "p_key_prefix": api_key[:10], "p_tier": "free",
        "p_max_users": 1, "p_max_graphs": 1, "p_ops_allowance": 10000,
        "p_graph_size_cap": 10000,
    })


class TestClaimSeam:
    def test_claim_links_owner_clears_identity_overwrites_email(self, fake):
        """link + clear-identity + email-overwrite (P1-FIX-B/P2-FIX-K): the
        NULL-user_id owner row gets user_id + identity=NULL, and teams.email
        is overwritten with the verified OAuth email — same key intact."""
        _provision_anon_team(fake, team_id="team-claim-1",
                             identity="anon-claim-1", api_key="tt_claim_1")
        claim_membership(fake, lookup_hash=lookup_hash("tt_claim_1"),
                         user_id="user-claim", email="verified@example.com")

        rows = fake.tables["team_memberships"]
        owner = next(r for r in rows if r["team_id"] == "team-claim-1")
        assert owner["user_id"] == "user-claim"
        assert owner["identity"] is None
        assert owner["role"] == "owner"
        assert owner["status"] == "active"
        team = next(t for t in fake.tables["teams"]
                    if t["id"] == "team-claim-1")
        assert team["email"] == "verified@example.com"
        # same key still resolves (indicator 1) — api_keys row untouched
        assert resolve_api_key(fake, "tt_claim_1")["team_id"] == "team-claim-1"
        # anon predicate flips
        assert is_anon_team(fake, "team-claim-1") is False

    def test_claim_merge_promotes_existing_member(self, fake):
        """P3-FIX-R/P4: an existing (user, team) row is promoted to owner
        with the identity row's key material; a 'removed' row reactivates."""
        _provision_anon_team(fake, team_id="team-merge-1",
                             identity="anon-merge-1", api_key="tt_merge_1")
        fake.seed("team_memberships", [{
            "id": "mem-user-merge", "user_id": "user-merge",
            "team_id": "team-merge-1", "role": "member", "status": "removed",
            "identity": None, "lookup_hash": None, "key_hash": "old-hash",
        }])
        claim_membership(fake, lookup_hash=lookup_hash("tt_merge_1"),
                         user_id="user-merge", email="m@example.com")

        rows = [r for r in fake.tables["team_memberships"]
                if r["team_id"] == "team-merge-1"]
        assert len(rows) == 1, f"merge must collapse to one row: {rows}"
        row = rows[0]
        assert row["user_id"] == "user-merge"
        assert row["role"] == "owner"
        assert row["status"] == "active"  # removed row reactivated (P4)
        # key material copied from the identity row (same-key continuity)
        assert row["lookup_hash"] == lookup_hash("tt_merge_1")

    def test_second_claim_409(self, fake):
        """first-claim-wins: a DIFFERENT user's claim raises ClaimError 409
        already_claimed after the team is claimed."""
        _provision_anon_team(fake, team_id="team-wins-1",
                             identity="anon-wins-1", api_key="tt_wins_1")
        claim_membership(fake, lookup_hash=lookup_hash("tt_wins_1"),
                         user_id="user-a", email="a@example.com")
        with pytest.raises(ClaimError) as ei:
            claim_membership(fake, lookup_hash=lookup_hash("tt_wins_1"),
                             user_id="user-b", email="b@example.com")
        assert ei.value.status == 409
        assert ei.value.code == "already_claimed"

    def test_claim_idempotent_same_user(self, fake):
        """P3-FIX-Q: re-claim by the SAME user is a noop success."""
        _provision_anon_team(fake, team_id="team-idem-1",
                             identity="anon-idem-1", api_key="tt_idem_1")
        claim_membership(fake, lookup_hash=lookup_hash("tt_idem_1"),
                         user_id="user-a", email="a@example.com")
        claim_membership(fake, lookup_hash=lookup_hash("tt_idem_1"),
                         user_id="user-a", email="a2@example.com")  # no raise
        rows = [r for r in fake.tables["team_memberships"]
                if r["team_id"] == "team-idem-1"]
        assert len(rows) == 1

    def test_claim_rejects_non_owner_row(self, fake):
        """non-owner-reject: an anon MEMBER row (role != 'owner') is never
        linked — the RPC only claims owner rows."""
        _provision_anon_team(fake, team_id="team-nonown-1",
                             identity="anon-nonown-1", api_key="tt_nonown_1")
        for r in fake.tables["team_memberships"]:
            if r["team_id"] == "team-nonown-1" and r["user_id"] is None:
                r["role"] = "member"  # demote to anon member
        with pytest.raises(ClaimError) as ei:
            claim_membership(fake, lookup_hash=lookup_hash("tt_nonown_1"),
                             user_id="user-a", email="a@example.com")
        assert ei.value.code == "already_claimed"
        row = next(r for r in fake.tables["team_memberships"]
                   if r["team_id"] == "team-nonown-1")
        assert row["user_id"] is None
        assert row["identity"] == "anon-nonown-1"

    def test_claim_leaves_unrelated_null_user_rows_untouched(self, fake):
        """null-row-untouched: anon rows on OTHER teams (or non-owner rows on
        the same team) are never touched by a claim."""
        _provision_anon_team(fake, team_id="team-t1",
                             identity="anon-t1", api_key="tt_t1")
        _provision_anon_team(fake, team_id="team-t2",
                             identity="anon-t2", api_key="tt_t2")
        claim_membership(fake, lookup_hash=lookup_hash("tt_t1"),
                         user_id="user-a", email="a@example.com")
        other = next(r for r in fake.tables["team_memberships"]
                     if r["team_id"] == "team-t2")
        assert other["user_id"] is None
        assert other["identity"] == "anon-t2"

    def test_claim_bootstrap_key_rejected(self, fake):
        """implementer advisory 1: session keys (created_via='bootstrap') can
        never claim — future-proofs merge/promote against member→owner."""
        _provision_anon_team(fake, team_id="team-boot-1",
                             identity="anon-boot-1", api_key="tt_boot_1")
        # add a bootstrap session key for the SAME team
        fake.seed("api_keys", [{
            "id": "key-sess-1", "team_id": "team-boot-1",
            "lookup_hash": lookup_hash("tt_sess_1"), "created_via": "bootstrap",
            "created_by": "anon-boot-1", "expires_at": None, "revoked_at": None,
        }])
        with pytest.raises(ClaimError) as ei:
            claim_membership(fake, lookup_hash=lookup_hash("tt_sess_1"),
                             user_id="user-a", email="a@example.com")
        assert ei.value.code == "key_not_claimable"

    def test_claim_unknown_key_404(self, fake):
        with pytest.raises(ClaimError) as ei:
            claim_membership(fake, lookup_hash=lookup_hash("tt_nope"),
                             user_id="user-a", email="a@example.com")
        assert ei.value.code == "key_not_found"
        assert ei.value.status == 404

    def test_claim_email_in_use_on_cross_team_collision(self, fake):
        """P3-FIX-S: uq_teams_email parity — a cross-team verified-email
        collision rejects the claim (atomic rollback: owner stays anon)."""
        _provision_anon_team(fake, team_id="team-e1", identity="anon-e1",
                             api_key="tt_e1", email="shared@example.com")
        _provision_anon_team(fake, team_id="team-e2", identity="anon-e2",
                             api_key="tt_e2")
        with pytest.raises(ClaimError) as ei:
            claim_membership(fake, lookup_hash=lookup_hash("tt_e2"),
                             user_id="user-a", email="shared@example.com")
        assert ei.value.code == "email_in_use"
        row = next(r for r in fake.tables["team_memberships"]
                   if r["team_id"] == "team-e2")
        assert row["user_id"] is None  # txn rolled back

    def test_claim_drops_leftover_placeholder(self, fake):
        """P3-FIX-Q tail: the user's placeholder row (team_id='') is dropped
        on claim — fresh, merge, AND idempotent paths."""
        _provision_anon_team(fake, team_id="team-ph-1",
                             identity="anon-ph-1", api_key="tt_ph_1")
        fake.seed("team_memberships", [{
            "id": "ph-user", "user_id": "user-a", "team_id": "",
            "role": "owner", "status": "active", "identity": None,
        }])
        claim_membership(fake, lookup_hash=lookup_hash("tt_ph_1"),
                         user_id="user-a", email="a@example.com")
        assert not any(r["team_id"] == "" and r["user_id"] == "user-a"
                       for r in fake.tables["team_memberships"])

    def test_is_anon_team_predicate(self, fake):
        """The shared anon predicate: EXISTS active owner with user_id NULL.
        NOT the email proxy (reg- teams with teams.email set are anon)."""
        _provision_anon_team(fake, team_id="team-anon-p", identity="anon-p",
                             api_key="tt_anon_p", email="reg@example.com")
        assert is_anon_team(fake, "team-anon-p") is True  # email set but anon
        claim_membership(fake, lookup_hash=lookup_hash("tt_anon_p"),
                         user_id="user-a", email="reg@example.com")
        assert is_anon_team(fake, "team-anon-p") is False
        assert is_anon_team(fake, "no-such-team") is False
