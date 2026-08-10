"""HTTP tests for the #302 security-baseline remainder — data export +
account/team deletion (E2E-6-D), on BOTH control planes.

Supabase mode (FakeControlPlane, mirroring test_auth_flip):
- GET /v1/teams/{id}/export — owner-only JSON export (graph + control plane)
- DELETE /v1/teams/{id} — owner-only soft delete → 24h grace → hard purge

Registry mode (temp FalkorDBLite, mirroring test_dr_endpoints): the same
surface over registry Membership/APIKey/Team nodes.

Covers: auth failures (401/403), unknown team (404), deleted team (410),
happy paths, idempotency, fail-closed key auth after delete, audit events,
per-IP rate limits, and the post-grace purge sweep.
"""
from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
# Global middleware + sensitive-op limiter opt out in tests (mirrors
# test_hosted_api); the rate-limit test re-enables the sensitive limiter.
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")

import tortoise.hosted_api as ha_mod
from tortoise.hosted_api import app, get_current_user
from tortoise.sdk import TortoiseSDK

from tests.fake_control_plane import FakeControlPlane
from tests.test_supabase_control import (
    FREE_TEAM, TOKEN, _key_row, _membership_row,
)

TEAM_ID = "team-free-001"
OWNER = "user-1"


def _enable_supabase(monkeypatch, cp) -> FakeControlPlane:
    """Turn Supabase mode on and inject the fake control plane."""
    import tortoise.supabase_control as sc
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "svc_role_key_test")
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "supabase")
    monkeypatch.setattr(sc, "get_control_plane", lambda: cp)
    return cp


def _patch_tortoise_sdk_init(db_path: str):
    """Make hosted_api's TortoiseSDK use a temp embedded DB (mirrors
    test_hosted_api) so registry/team reads don't touch prod."""
    _orig = ha_mod.TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig(self, db_path, namespace=namespace)

    ha_mod.TortoiseSDK.__init__ = _patched
    return _orig


def _restore_sdk_init(_orig):
    ha_mod.TortoiseSDK.__init__ = _orig
    app.dependency_overrides.clear()


@pytest.fixture
def sb_client(monkeypatch):
    """Supabase-mode TestClient with a fake control plane + temp DB."""
    fake = FakeControlPlane({"teams": [], "api_keys": [],
                             "team_memberships": [], "invitations": []})
    _enable_supabase(monkeypatch, fake)
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "export.db")
        _orig = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc, fake, db_path
        finally:
            _restore_sdk_init(_orig)


@pytest.fixture
def reg_client(monkeypatch):
    """Registry-mode TestClient (TORTOISE_CONTROL_PLANE=registry) + temp DB."""
    monkeypatch.setenv("TORTOISE_CONTROL_PLANE", "registry")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "export.db")
        _orig = _patch_tortoise_sdk_init(db_path)
        try:
            with TestClient(app) as tc:
                yield tc, db_path
        finally:
            _restore_sdk_init(_orig)


@pytest.fixture
def as_user():
    """Override get_current_user per test (JWT session user)."""

    def _set(user_id: str = OWNER):
        app.dependency_overrides[get_current_user] = lambda: {"user_id": user_id}

    yield _set
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def capture_audit(monkeypatch):
    """Capture _audit_logger.append calls (no Postgres/JSONL in tests).

    AuditLogger.append is called with positional team_id/actor/operation by
    the purge sweep and with kwargs by _async_audit — normalize both into
    the kwargs dict."""
    captured: list[dict] = []
    _POSITIONAL = ("team_id", "actor_user_id", "operation",
                   "resource_type", "resource_id", "ip_address", "user_agent")

    def _capture(*args, **kwargs):
        for i, a in enumerate(args):
            if i < len(_POSITIONAL):
                kwargs[_POSITIONAL[i]] = a
        captured.append(kwargs)

    monkeypatch.setattr(ha_mod._audit_logger, "append", _capture)
    return captured


# ── Seeding helpers ─────────────────────────────────────────────────────────


def _seed_supabase_team(fake, *, role: str = "owner", deleted_at: str | None = None,
                        with_key: bool = True, with_invite: bool = False):
    team = dict(FREE_TEAM)
    if deleted_at:
        team["deleted_at"] = deleted_at
    fake.seed("teams", [team])
    fake.seed("team_memberships", [_membership_row(role=role)])
    if with_key:
        fake.seed("api_keys", [_key_row()])
    if with_invite:
        fake.seed("invitations", [{
            "id": "inv-1", "team_id": TEAM_ID, "email": "bob@example.com",
            "role": "member", "status": "pending", "expires_at": None,
        }])


def _seed_graph(db_path: str, team_id: str = TEAM_ID, *,
                n_points: int = 2, n_events: int = 1) -> None:
    """Seed the team's FalkorDB graph: points + a Tag + a TAGGED edge + events."""
    sdk = TortoiseSDK(db_path, namespace=team_id)
    g = sdk._get_proj().g
    for i in range(n_points):
        g.query(
            "CREATE (p:Point {id:$id, content:$c, pointKind:'claim', confidence:0.8})",
            params={"id": f"pt-{i}", "c": f"content {i}"},
        )
    g.query("CREATE (t:Tag {id:'tag-1', name:'alpha'})")
    g.query(
        "MATCH (p:Point {id:'pt-0'}), (t:Tag {id:'tag-1'}) CREATE (p)-[:TAGGED]->(t)"
    )
    for i in range(n_events):
        g.query(
            "CREATE (e:GraphEvent {seq:$s, ts:$ts, type:'point_create', "
            "event_id:$eid, payload:$p})",
            params={"s": i + 1, "ts": "2026-08-01T00:00:00Z",
                    "eid": f"ev-{i}", "p": '{"id":"pt-0"}'},
        )


def _seed_registry(db_path: str, team_id: str = "reg-team-1", *,
                   deleted_at: str | None = None) -> None:
    """Seed registry Team + owner Membership + APIKey (+ optional deleted_at)."""
    sdk = TortoiseSDK(db_path, namespace="registry")
    reg = sdk._get_registry()
    reg.query("CREATE (t:Team {id:$id, name:$name, tier:'free'})",
              params={"id": team_id, "name": team_id})
    reg.query(
        "CREATE (m:Membership {id:'m-1', user_id:'u-owner', team_id:$tid, "
        "role:'owner', status:'active', joined_at:'2026-08-01T00:00:00Z'})",
        params={"tid": team_id},
    )
    reg.query(
        "CREATE (k:APIKey {id:'k-1', team_id:$tid, key_hash:'h', "
        "key_prefix:'reg-team', revoked_at:null})",
        params={"tid": team_id},
    )
    if deleted_at:
        reg.query("MATCH (t:Team {id:$id}) SET t.deleted_at=$d",
                  params={"id": team_id, "d": deleted_at})


def _registry_count(db_path: str, label: str, team_id: str) -> int:
    """Count registry nodes of `label` scoped to a team. Team nodes key on
    `id`; Membership/APIKey/Invitation key on `team_id`."""
    prop = "id" if label == "Team" else "team_id"
    sdk = TortoiseSDK(db_path, namespace="registry")
    rows = sdk._get_registry().query(
        f"MATCH (n:{label} {{{prop}:$tid}}) RETURN count(n)",
        params={"tid": team_id},
    ).result_set
    return int(rows[0][0]) if rows else 0


# ═══════════════════════════════════════════════════════════════════════════
# GET /v1/teams/{team_id}/export — Supabase mode
# ═══════════════════════════════════════════════════════════════════════════


class TestExportSupabase:
    def test_export_requires_session_auth(self, sb_client):
        tc, _, _ = sb_client
        r = tc.get(f"/v1/teams/{TEAM_ID}/export")
        assert r.status_code == 401

    def test_export_requires_owner(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_supabase_team(fake, role="member")
        as_user()
        r = tc.get(f"/v1/teams/{TEAM_ID}/export")
        assert r.status_code == 403
        assert "owner" in r.json()["detail"]

    def test_export_admin_denied(self, sb_client, as_user):
        """Strict owner — admin can manage members but cannot export."""
        tc, fake, _ = sb_client
        _seed_supabase_team(fake, role="admin")
        as_user()
        assert tc.get(f"/v1/teams/{TEAM_ID}/export").status_code == 403

    def test_export_unknown_team_404(self, sb_client, as_user):
        tc, _, _ = sb_client
        as_user()
        r = tc.get("/v1/teams/nope/export")
        assert r.status_code == 404

    def test_export_deleted_team_410(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_supabase_team(
            fake, deleted_at=datetime.now(timezone.utc).isoformat())
        as_user()
        r = tc.get(f"/v1/teams/{TEAM_ID}/export")
        assert r.status_code == 410

    def test_export_happy_path(self, sb_client, as_user):
        tc, fake, db_path = sb_client
        _seed_supabase_team(fake)
        _seed_graph(db_path)
        as_user()
        r = tc.get(f"/v1/teams/{TEAM_ID}/export")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["schema_version"] == 1
        assert body["team_id"] == TEAM_ID
        assert body["exported_at"]
        # summary: 2 points + 1 Tag + 1 GraphEvent, 1 TAGGED edge
        assert body["summary"]["points"] == 2
        assert body["summary"]["entities"] == 1
        assert body["summary"]["edges"] == 1
        assert body["summary"]["events"] == 1
        assert body["summary"]["nodes"] == 4
        # full point data incl. confidence scores + kind mapping
        pts = {p["id"]: p for p in body["points"]}
        assert pts["pt-0"]["content"] == "content 0"
        assert pts["pt-0"]["kind"] == "claim"
        assert pts["pt-0"]["confidence"] == 0.8
        assert "pointKind" not in pts["pt-0"]
        # entity node
        assert any("Tag" in e["labels"] and e["name"] == "alpha"
                   for e in body["entities"])
        # edge with source/target/type
        edge = body["edges"][0]
        assert edge["source"] == "pt-0" and edge["target"] == "tag-1"
        assert edge["type"] == "TAGGED"
        # event payload decoded to a dict
        assert body["events"][0]["payload"] == {"id": "pt-0"}
        # control-plane metadata: team row, members, plan
        assert body["team"]["id"] == TEAM_ID
        assert body["team"]["tier"] == "free"
        assert body["members"][0]["role"] == "owner"
        assert body["members"][0]["user_id"] == OWNER
        assert body["plan"]["tier"] == "free"
        assert "limits" in body["plan"]

    def test_export_audited(self, sb_client, as_user, capture_audit):
        tc, fake, db_path = sb_client
        _seed_supabase_team(fake)
        _seed_graph(db_path)
        as_user()
        r = tc.get(f"/v1/teams/{TEAM_ID}/export")
        assert r.status_code == 200
        ops = [e["operation"] for e in capture_audit]
        assert "team_export" in ops
        event = next(e for e in capture_audit if e["operation"] == "team_export")
        assert event["actor_user_id"] == OWNER
        assert event["team_id"] == TEAM_ID
        assert event["resource_type"] == "team"


# ═══════════════════════════════════════════════════════════════════════════
# DELETE /v1/teams/{team_id} — Supabase mode
# ═══════════════════════════════════════════════════════════════════════════


class TestDeleteSupabase:
    def test_delete_requires_session_auth(self, sb_client):
        tc, _, _ = sb_client
        r = tc.delete(f"/v1/teams/{TEAM_ID}")
        assert r.status_code == 401

    def test_delete_requires_owner(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_supabase_team(fake, role="admin")  # admin ≠ owner
        as_user()
        r = tc.delete(f"/v1/teams/{TEAM_ID}")
        assert r.status_code == 403

    def test_delete_unknown_team_404(self, sb_client, as_user):
        tc, _, _ = sb_client
        as_user()
        assert tc.delete("/v1/teams/nope").status_code == 404

    def test_delete_cascade(self, sb_client, as_user, capture_audit):
        """Soft delete: deleted_at stamp + keys revoked + memberships
        removed + invitations revoked, audited with the acting user."""
        tc, fake, _ = sb_client
        _seed_supabase_team(fake, with_invite=True)
        as_user()
        r = tc.delete(f"/v1/teams/{TEAM_ID}")
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["status"] == "delete_scheduled"
        assert body["team_id"] == TEAM_ID
        assert body["grace_hours"] == 24
        assert body["deleted_at"]
        assert body["hard_delete_after"] > body["deleted_at"]

        by_id = {row["id"]: row for row in fake.tables["teams"]}
        assert by_id[TEAM_ID]["deleted_at"] == body["deleted_at"]
        assert fake.tables["api_keys"][0]["revoked_at"] == body["deleted_at"]
        assert fake.tables["team_memberships"][0]["status"] == "removed"
        assert fake.tables["invitations"][0]["status"] == "revoked"

        ops = [e["operation"] for e in capture_audit]
        assert "team_delete_requested" in ops
        event = next(e for e in capture_audit
                     if e["operation"] == "team_delete_requested")
        assert event["actor_user_id"] == OWNER
        assert event["team_id"] == TEAM_ID

    def test_delete_revokes_key_auth_fail_closed(self, sb_client, as_user):
        """After delete, the team's tt_ keys stop authenticating (401)."""
        tc, fake, _ = sb_client
        _seed_supabase_team(fake)
        as_user()
        assert tc.delete(f"/v1/teams/{TEAM_ID}").status_code == 202
        r = tc.get("/v1/team/keys", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 401

    def test_delete_idempotent(self, sb_client, as_user, capture_audit):
        tc, fake, _ = sb_client
        _seed_supabase_team(fake)
        as_user()
        first = tc.delete(f"/v1/teams/{TEAM_ID}")
        assert first.status_code == 202
        second = tc.delete(f"/v1/teams/{TEAM_ID}")
        assert second.status_code == 200
        body = second.json()
        assert body["already"] is True
        assert body["status"] == "delete_pending"
        assert body["deleted_at"] == first.json()["deleted_at"]
        # no duplicate delete_requested audit event
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("team_delete_requested") == 1


# ═══════════════════════════════════════════════════════════════════════════
# Same surface — registry mode (selfhost control plane)
# ═══════════════════════════════════════════════════════════════════════════


class TestExportDeleteRegistry:
    def test_export_happy_path_registry(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path)
        _seed_graph(db_path, team_id="reg-team-1")
        as_user(user_id="u-owner")
        r = tc.get("/v1/teams/reg-team-1/export")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"]["points"] == 2
        assert body["summary"]["edges"] == 1
        assert body["members"][0]["role"] == "owner"
        assert body["members"][0]["joined_at"] == "2026-08-01T00:00:00Z"

    def test_export_requires_owner_registry(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path)
        as_user(user_id="someone-else")  # no membership at all
        assert tc.get("/v1/teams/reg-team-1/export").status_code == 403

    def test_delete_cascade_registry(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path)
        as_user(user_id="u-owner")
        r = tc.delete("/v1/teams/reg-team-1")
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "delete_scheduled"

        sdk = TortoiseSDK(db_path, namespace="registry")
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (t:Team {id:'reg-team-1'}) RETURN t.deleted_at"
        ).result_set
        assert rows and rows[0][0]
        assert _registry_count(db_path, "APIKey", "reg-team-1") == 1
        rev = reg.query(
            "MATCH (k:APIKey {team_id:'reg-team-1'}) RETURN k.revoked_at"
        ).result_set
        assert rev and rev[0][0] is not None
        mem = reg.query(
            "MATCH (m:Membership {team_id:'reg-team-1'}) RETURN m.status"
        ).result_set
        assert mem and mem[0][0] == "removed"

    def test_delete_idempotent_registry(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path)
        as_user(user_id="u-owner")
        assert tc.delete("/v1/teams/reg-team-1").status_code == 202
        second = tc.delete("/v1/teams/reg-team-1")
        assert second.status_code == 200
        assert second.json()["already"] is True

    def test_delete_revokes_key_auth_registry(self, reg_client, as_user):
        """Registry-mode key auth fails closed after delete (401)."""
        tc, db_path = reg_client
        # Registry mode resolves tt_ keys via registry APIKey nodes — seed a
        # key whose hash verifies against TOKEN, then delete the team.
        sdk = TortoiseSDK(db_path, namespace="registry")
        from tortoise.auth import hash_api_key
        reg = sdk._get_registry()
        reg.query("CREATE (t:Team {id:'reg-team-1', name:'reg-team-1'})")
        reg.query(
            "CREATE (k:APIKey {id:'k-1', team_id:'reg-team-1', "
            "key_prefix:$pfx, key_hash:$hash, revoked_at:null})",
            params={"pfx": TOKEN[:10], "hash": hash_api_key(TOKEN)},
        )
        reg.query(
            "CREATE (m:Membership {id:'m-1', user_id:'u-owner', "
            "team_id:'reg-team-1', role:'owner', status:'active'})"
        )
        as_user(user_id="u-owner")
        assert tc.delete("/v1/teams/reg-team-1").status_code == 202
        r = tc.get("/v1/team", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# Post-grace purge (hard delete)
# ═══════════════════════════════════════════════════════════════════════════


class TestPurge:
    def test_purge_hard_deletes_past_grace_registry(self, reg_client,
                                                    capture_audit):
        tc, db_path = reg_client
        past = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        _seed_registry(db_path, team_id="reg-old", deleted_at=past)
        _seed_registry(db_path, team_id="reg-recent",
                       deleted_at=datetime.now(timezone.utc).isoformat())

        ha_mod._purge_deleted_teams()

        assert _registry_count(db_path, "Team", "reg-old") == 0
        assert _registry_count(db_path, "Membership", "reg-old") == 0
        assert _registry_count(db_path, "APIKey", "reg-old") == 0
        # within grace → untouched
        assert _registry_count(db_path, "Team", "reg-recent") == 1
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("team_delete_purged") == 1
        assert capture_audit[-1]["team_id"] == "reg-old"

    def test_purge_deletes_rows_past_grace_supabase(self, sb_client,
                                                    capture_audit):
        tc, fake, _ = sb_client
        past = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        recent = datetime.now(timezone.utc).isoformat()
        fake.seed("teams", [
            dict(FREE_TEAM, deleted_at=past),
            dict(FREE_TEAM, id="team-recent", deleted_at=recent),
        ])
        fake.seed("team_memberships", [
            _membership_row(),                       # team-free-001 (old)
            _membership_row(team_id="team-recent"),  # within grace
        ])
        fake.seed("api_keys", [
            _key_row(),                              # team-free-001 (old)
            _key_row(team_id="team-recent"),         # within grace
        ])
        fake.seed("invitations", [{
            "id": "inv-1", "team_id": TEAM_ID, "email": "bob@example.com",
            "role": "member", "status": "pending", "expires_at": None,
        }])

        ha_mod._purge_deleted_teams()

        # team-free-001 control-plane rows hard-deleted (all tables)
        assert all(r["id"] != TEAM_ID for r in fake.tables["teams"])
        assert all(r["team_id"] != TEAM_ID for r in fake.tables["api_keys"])
        assert all(r["team_id"] != TEAM_ID
                   for r in fake.tables["team_memberships"])
        assert fake.tables["invitations"] == []
        # within-grace team survives
        assert any(r["id"] == "team-recent" for r in fake.tables["teams"])
        ops = [e["operation"] for e in capture_audit]
        assert "team_delete_purged" in ops


# ═══════════════════════════════════════════════════════════════════════════
# Sensitive-op rate limits
# ═══════════════════════════════════════════════════════════════════════════


class TestSensitiveRateLimit:
    def test_team_delete_rate_limited(self, sb_client, monkeypatch, as_user):
        """Per-IP hourly budget (5/h): 6th delete request → 429."""
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        ha_mod._SENSITIVE_BUCKETS.clear()
        tc, _, _ = sb_client
        as_user()
        for _ in range(5):
            r = tc.delete("/v1/teams/nope")  # unknown team → 404, not 429
            assert r.status_code == 404
        r = tc.delete("/v1/teams/nope")
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        ha_mod._SENSITIVE_BUCKETS.clear()
