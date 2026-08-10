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
    active_api_keys,
    get_control_plane,
    insert_api_key,
    is_supabase_enabled,
    membership_for_user_team,
    resolve_api_key,
    revoke_api_key,
    team_by_id,
    update_last_used,
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
    return FakeControlPlane({
        "api_keys": [],
        "team_memberships": [],
        "teams": [dict(FREE_TEAM)],
    })


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

    def test_patch_and_post(self):
        cp = FakeControlPlane({"t": [{"id": "k1", "x": None}]})
        assert cp.query("t", method="PATCH", filters=[("id", "eq", "k1")],
                        json_body={"x": 1}) == []
        assert cp.tables["t"][0]["x"] == 1
        row = cp.query("t", method="POST", json_body={"id": "k2"})
        assert row == [{"id": "k2"}]
        assert len(cp.tables["t"]) == 2


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
