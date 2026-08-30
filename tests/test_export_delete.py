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

import tortoise.hosted_api as ha_mod  # noqa: I001
from tortoise.hosted_api import app, get_current_user
from tortoise.sdk import TortoiseSDK

from tests.fake_control_plane import FakeControlPlane
from tests.test_supabase_control import (
    FREE_TEAM, TOKEN, _key_row, _membership_row,
)

TEAM_ID = "team-free-001"

# #1719 (Task 3): team_memberships.user_id is a uuid column — real JWT
# subjects are UUIDs; non-UUID literals 22P02 (HTTP 400) under
# FakeControlPlane's fidelity check. user-1 → _U1; registry owner
# "u-owner" → _U2; JWT overrides for non-members → _U3/_U4.
_U1 = "9f2c1a40-0000-4a00-8000-000000000001"
_U2 = "9f2c1a40-0000-4a00-8000-000000000002"
_U3 = "9f2c1a40-0000-4a00-8000-000000000003"
_U4 = "9f2c1a40-0000-4a00-8000-000000000004"
OWNER = _U1


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
    # #1497: break the _make_sdk embedded fallback anchor — module-level
    # _FALLBACK_KEEPALIVE survives tests, so an anchored SDK bound to a prior
    # test's temp DB leaks state / dies socket. Re-bind to THIS temp DB.
    ha_mod._FALLBACK_KEEPALIVE.clear()
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
                n_points: int = 2, n_events: int = 1) -> TortoiseSDK:
    """Seed the team's FalkorDB graph: points + a Tag + a TAGGED edge + events.

    Returns the SDK — the caller MUST keep the returned reference alive until
    the export/read that follows: with TORTOISE_FAST_ATEXIT=1 (tests/conftest)
    and the #1475 close-on-GC finalizer, the seed SDK going out of scope fires
    SHUTDOWN NOSAVE on the server, so a later read on the same path either
    reconnects to a dead socket or a fresh empty DB (redis.socket
    ConnectionError / 0 nodes — the test-isolation flake class).
    """
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
    return sdk  # caller keeps this alive until the export reads the graph


def _seed_registry(db_path: str, team_id: str = "reg-team-1", *,
                   deleted_at: str | None = None) -> None:
    """Seed registry Team + owner Membership + APIKey (+ optional deleted_at)."""
    sdk = TortoiseSDK(db_path, namespace="registry")
    reg = sdk._get_registry()
    reg.query("CREATE (t:Team {id:$id, name:$name, tier:'free'})",
              params={"id": team_id, "name": team_id})
    reg.query(
        # user_id mirrors _U2 (9f2c1a40-...-0002) — registry Membership
        # user_id is the same uuid column as team_memberships (#1719 T3).
        "CREATE (m:Membership {id:'m-1', user_id:'9f2c1a40-0000-4a00-8000-000000000002', team_id:$tid, "
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

    def test_export_unknown_team_403(self, sb_client, as_user):
        """AuthZ-first: a non-member gets 403 for an unknown team (no
        existence oracle — security review, PR #873)."""
        tc, _, _ = sb_client
        as_user()
        r = tc.get("/v1/teams/nope/export")
        assert r.status_code == 403

    def test_export_deleted_team_410(self, sb_client, as_user):
        tc, fake, _ = sb_client
        _seed_supabase_team(
            fake, deleted_at=datetime.now(timezone.utc).isoformat())  # noqa: UP017
        as_user()
        r = tc.get(f"/v1/teams/{TEAM_ID}/export")
        assert r.status_code == 410

    def test_export_deleted_team_non_owner_403(self, sb_client, as_user):
        """Non-owner probing a deleted team gets 403, not the 410/deletion
        schedule (no info disclosure — security review, PR #873)."""
        tc, fake, _ = sb_client
        _seed_supabase_team(
            fake, role="member", deleted_at=datetime.now(timezone.utc).isoformat())  # noqa: UP017
        as_user()
        r = tc.get(f"/v1/teams/{TEAM_ID}/export")
        assert r.status_code == 403

    def test_export_events_truncated(self, sb_client, as_user, monkeypatch):
        """Event cap: newest-by-seq window kept + truncation flags."""
        monkeypatch.setattr(ha_mod, "_EXPORT_MAX_EVENTS", 3)
        tc, fake, db_path = sb_client
        _seed_supabase_team(fake)
        seed_sdk = _seed_graph(db_path, n_events=5)  # noqa: F841
        as_user()
        r = tc.get(f"/v1/teams/{TEAM_ID}/export")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["summary"]["events"] == 3
        assert body["summary"]["events_total"] == 5
        assert body["summary"]["events_truncated"] is True
        # newest-by-seq kept (traversal order is unspecified — sorted)
        seqs = sorted(e["seq"] for e in body["events"])
        assert seqs == [3, 4, 5]

    def test_export_happy_path(self, sb_client, as_user):
        tc, fake, db_path = sb_client
        _seed_supabase_team(fake)
        seed_sdk = _seed_graph(db_path)  # noqa: F841
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
        seed_sdk = _seed_graph(db_path)  # noqa: F841
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

    def test_delete_unknown_team_403(self, sb_client, as_user):
        """AuthZ-first: unknown team → 403 for non-members (no oracle)."""
        tc, _, _ = sb_client
        as_user()
        assert tc.delete("/v1/teams/nope").status_code == 403

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
        assert by_id[TEAM_ID]["grace_hours"] == 24  # persisted promise
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

    def test_delete_replay_non_owner_403(self, sb_client, as_user):
        """Idempotent replay is owner-gated too — a non-owner probing a
        delete-pending team gets 403, never the deletion schedule."""
        tc, fake, _ = sb_client
        _seed_supabase_team(fake, deleted_at=datetime.now(timezone.utc).isoformat())  # noqa: UP017
        as_user()
        # owner replay still works (removed-owner state accepted)
        r = tc.delete(f"/v1/teams/{TEAM_ID}")
        assert r.status_code == 200
        assert r.json()["already"] is True
        # non-owner → 403
        app.dependency_overrides[get_current_user] = lambda: {"user_id": _U4}
        r2 = tc.delete(f"/v1/teams/{TEAM_ID}")
        assert r2.status_code == 403

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
# #1903 — dashboard-created teams (POST /v1/teams): stored graph_name must
# equal the data-plane namespace (team_{team_id}) so export/delete resolve
# the REAL graph. The old mint (team_{name}) made export empty and delete
# orphan the real graph.
# ═══════════════════════════════════════════════════════════════════════════


class TestDashboardCreatedTeamRoundTrip:
    def test_dashboard_created_team_export_returns_points(self, sb_client, as_user):
        """#1903 Indicator 1+2: POST /v1/teams mints graph_name=team_{team_id}
        and a dashboard-created team's export returns its points (the stored
        name resolves the real data graph)."""
        tc, fake, db_path = sb_client
        as_user()
        r = tc.post("/v1/teams", json={"name": "acme"})
        assert r.status_code == 200, r.text
        body = r.json()
        team_id = body["team_id"]
        assert body["graph_name"] == f"team_{team_id}"  # Indicator 1
        fn, p = fake.rpc_calls[0]
        assert fn == "provision_team"
        assert p["p_graph_name"] == f"team_{team_id}"
        # data-plane write (the real write path: namespace=team_id)
        seed_sdk = _seed_graph(db_path, team_id=team_id, n_points=1, n_events=0)  # noqa: F841
        r2 = tc.get(f"/v1/teams/{team_id}/export")
        assert r2.status_code == 200, r2.text
        assert r2.json()["summary"]["points"] == 1  # Indicator 2

    def test_dashboard_created_team_delete_drops_team_id_graph(self, sb_client, as_user, monkeypatch, capture_audit):
        """#1903 Indicator 3: delete of a dashboard-created team targets the
        team_{team_id} graph (the old team_{name} stored name orphaned it).
        The _drop_team_graph_strict spy is the mechanism proof — embedded
        FalkorDBLite has no delete_graph, so the assertion is on the CORRECT
        TARGET passed to the drop."""
        tc, fake, _ = sb_client
        as_user()
        # env must be 0 BEFORE delete — soft_delete stamps the STORED
        # grace_hours and the purge honors stored grace over env
        # (_past_grace): a 24h stamp would skip the just-deleted team.
        monkeypatch.setenv("TORTOISE_TEAM_DELETE_GRACE_HOURS", "0")
        r = tc.post("/v1/teams", json={"name": "acme"})
        assert r.status_code == 200, r.text
        team_id = r.json()["team_id"]
        assert r.json()["graph_name"] == f"team_{team_id}"
        dropped = []
        monkeypatch.setattr(ha_mod, "_drop_team_graph_strict",
                            lambda tid, gn=None: dropped.append((tid, gn)))
        r = tc.delete(f"/v1/teams/{team_id}")
        assert r.status_code == 202, r.text
        assert r.json()["grace_hours"] == 0  # env->stored promise pinned
        ha_mod._purge_deleted_teams()
        # exactly one drop, exactly the team_{team_id} target (suite precedent:
        # TestPurge asserts strict equality on the captured drop list)
        assert dropped == [(team_id, f"team_{team_id}")]  # Indicator 3
        assert not any(t["id"] == team_id for t in fake.tables["teams"])
        ops = [e["operation"] for e in capture_audit]
        assert "team_delete_purged" in ops


# ═══════════════════════════════════════════════════════════════════════════
# Same surface — registry mode (selfhost control plane)
# ═══════════════════════════════════════════════════════════════════════════


class TestExportDeleteRegistry:
    def test_export_happy_path_registry(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path)
        seed_sdk = _seed_graph(db_path, team_id="reg-team-1")  # noqa: F841
        as_user(user_id=_U2)
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
        as_user(user_id=_U3)  # no membership at all
        assert tc.get("/v1/teams/reg-team-1/export").status_code == 403

    def test_export_uses_stored_graph_name(self, reg_client, as_user):
        """Teams created via sdk.team_create store graph_name=team_{name} —
        export must read THAT graph, not team_{id} (code-review P1, #873)."""
        tc, db_path = reg_client
        sdk = TortoiseSDK(db_path, namespace="registry")
        reg = sdk._get_registry()
        reg.query(
            "CREATE (t:Team {id:'reg-named', name:'Acme', tier:'free', "
            "graph_name:'team_Acme'})"
        )
        reg.query(
            "CREATE (m:Membership {id:'m-2', user_id:'9f2c1a40-0000-4a00-8000-000000000002', "
            "team_id:'reg-named', role:'owner', status:'active'})"
        )
        seed_sdk = _seed_graph(db_path, team_id="Acme", n_points=1, n_events=0)  # noqa: F841
        as_user(user_id=_U2)
        r = tc.get("/v1/teams/reg-named/export")
        assert r.status_code == 200, r.text
        assert r.json()["summary"]["points"] == 1

    def test_export_deleted_team_410_registry(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path, deleted_at=datetime.now(timezone.utc).isoformat())  # noqa: UP017
        as_user(user_id=_U2)
        r = tc.get("/v1/teams/reg-team-1/export")
        assert r.status_code == 410

    def test_delete_cascade_registry(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path)
        # pending invitation must be revoked too (registry branch)
        sdk = TortoiseSDK(db_path, namespace="registry")
        sdk._get_registry().query(
            "CREATE (i:Invitation {id:'inv-r', team_id:'reg-team-1', "
            "email:'bob@example.com', role:'member', status:'pending'})"
        )
        as_user(user_id=_U2)
        r = tc.delete("/v1/teams/reg-team-1")
        assert r.status_code == 202, r.text
        assert r.json()["status"] == "delete_scheduled"

        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (t:Team {id:'reg-team-1'}) RETURN t.deleted_at, t.grace_hours"
        ).result_set
        assert rows and rows[0][0]  # deleted_at stamped
        assert rows[0][1] == 24  # persisted grace promise
        assert _registry_count(db_path, "APIKey", "reg-team-1") == 1
        rev = reg.query(
            "MATCH (k:APIKey {team_id:'reg-team-1'}) RETURN k.revoked_at"
        ).result_set
        assert rev and rev[0][0] is not None
        mem = reg.query(
            "MATCH (m:Membership {team_id:'reg-team-1'}) RETURN m.status"
        ).result_set
        assert mem and mem[0][0] == "removed"
        inv = reg.query(
            "MATCH (i:Invitation {team_id:'reg-team-1'}) RETURN i.status"
        ).result_set
        assert inv and inv[0][0] == "revoked"

    def test_delete_idempotent_registry(self, reg_client, as_user):
        tc, db_path = reg_client
        _seed_registry(db_path)
        as_user(user_id=_U2)
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
            "CREATE (m:Membership {id:'m-1', user_id:'9f2c1a40-0000-4a00-8000-000000000002', "
            "team_id:'reg-team-1', role:'owner', status:'active'})"
        )
        as_user(user_id=_U2)
        assert tc.delete("/v1/teams/reg-team-1").status_code == 202
        r = tc.get("/v1/team", headers={"Authorization": f"Bearer {TOKEN}"})
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════
# Post-grace purge (hard delete)
# ═══════════════════════════════════════════════════════════════════════════


class TestPurge:
    def test_purge_hard_deletes_past_grace_registry(self, reg_client,
                                                    capture_audit, monkeypatch):
        tc, db_path = reg_client  # noqa: RUF059
        past = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()  # noqa: UP017
        _seed_registry(db_path, team_id="reg-old", deleted_at=past)
        _seed_registry(db_path, team_id="reg-recent",
                       deleted_at=datetime.now(timezone.utc).isoformat())  # noqa: UP017
        # wiring check: the graph drop is invoked for the purged team only
        dropped: list[str] = []
        monkeypatch.setattr(ha_mod, "_drop_team_graph",
                            lambda team_id, graph_name=None: dropped.append(team_id))

        ha_mod._purge_deleted_teams()

        assert _registry_count(db_path, "Team", "reg-old") == 0
        assert _registry_count(db_path, "Membership", "reg-old") == 0
        assert _registry_count(db_path, "APIKey", "reg-old") == 0
        # within grace → untouched
        assert _registry_count(db_path, "Team", "reg-recent") == 1
        assert dropped == ["reg-old"]  # never the within-grace team
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("team_delete_purged") == 1
        assert capture_audit[-1]["team_id"] == "reg-old"

    def test_purge_honors_stored_grace(self, reg_client, capture_audit,
                                       monkeypatch):
        """A config change mid-grace must not hard-delete before the
        promised hard_delete_after (code-review P1, PR #873): env shrinks
        to 1h but the team was promised 24h 10h ago → NOT purged."""
        monkeypatch.setenv("TORTOISE_TEAM_DELETE_GRACE_HOURS", "1")
        tc, db_path = reg_client  # noqa: RUF059
        ten_hours = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()  # noqa: UP017
        _seed_registry(db_path, team_id="reg-promised", deleted_at=ten_hours)
        sdk = TortoiseSDK(db_path, namespace="registry")
        sdk._get_registry().query(
            "MATCH (t:Team {id:'reg-promised'}) SET t.grace_hours=24"
        )
        _seed_registry(db_path, team_id="reg-env-old",
                       deleted_at=ten_hours)  # no stored grace → env 1h

        ha_mod._purge_deleted_teams()

        assert _registry_count(db_path, "Team", "reg-promised") == 1  # kept
        assert _registry_count(db_path, "Team", "reg-env-old") == 0  # purged

    def test_purge_deletes_rows_past_grace_supabase(self, sb_client,
                                                    capture_audit):
        tc, fake, _ = sb_client  # noqa: RUF059
        past = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()  # noqa: UP017
        recent = datetime.now(timezone.utc).isoformat()  # noqa: UP017
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

    def test_purge_keeps_row_anchor_on_graph_drop_failure_supabase(
            self, sb_client, capture_audit, monkeypatch):
        """#926: a silent graph-drop failure must not orphan the FalkorDB
        graph. The Supabase-mode drop is strict — on failure the sweep
        skips the team, the teams row survives as the retry anchor
        (control-plane rows untouched, no purge audit event), and the
        next sweep retries the drop to completion."""
        tc, fake, _ = sb_client  # noqa: RUF059
        past = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()  # noqa: UP017
        fake.seed("teams", [dict(FREE_TEAM, deleted_at=past),
                             dict(FREE_TEAM, id="team-other",
                                  deleted_at=past)])
        fake.seed("team_memberships", [_membership_row(),
                                        _membership_row(team_id="team-other")])
        fake.seed("api_keys", [_key_row(),
                                _key_row(team_id="team-other")])

        def _flaky(team_id, graph_name=None):
            if team_id == TEAM_ID:
                raise RuntimeError("graph drop failed (fault injection, #926)")
            return None

        monkeypatch.setattr(ha_mod, "_drop_team_graph_strict", _flaky)

        ha_mod._purge_deleted_teams()

        # retry anchor survives: teams row + child rows NOT purged
        assert any(r["id"] == TEAM_ID for r in fake.tables["teams"])
        assert any(r["team_id"] == TEAM_ID for r in fake.tables["api_keys"])
        assert any(r["team_id"] == TEAM_ID
                   for r in fake.tables["team_memberships"])
        # ...and a failed drop never blocks OTHER past-grace teams
        assert all(r["id"] != "team-other" for r in fake.tables["teams"])
        assert all(r["team_id"] != "team-other"
                   for r in fake.tables["api_keys"])
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("team_delete_purged") == 1
        assert capture_audit[-1]["team_id"] == "team-other"

        # next sweep (drop healed, real strict impl) → row purged
        monkeypatch.setattr(ha_mod, "_drop_team_graph_strict",
                            ha_mod._drop_team_graph_impl)
        ha_mod._purge_deleted_teams()
        assert all(r["id"] != TEAM_ID for r in fake.tables["teams"])
        ops = [e["operation"] for e in capture_audit]
        assert ops.count("team_delete_purged") == 2


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
            r = tc.delete("/v1/teams/nope")  # unknown team → 403, not 429
            assert r.status_code == 403
        r = tc.delete("/v1/teams/nope")
        assert r.status_code == 429
        assert "Retry-After" in r.headers
        ha_mod._SENSITIVE_BUCKETS.clear()

    def test_export_rate_limited_independently(self, sb_client, monkeypatch,
                                               as_user):
        """Export has its own budget; delete calls don't consume it
        (per-op keying)."""
        monkeypatch.delenv("RATE_LIMIT_DISABLED", raising=False)
        monkeypatch.setitem(ha_mod._SENSITIVE_OP_LIMITS, "export", 2)
        ha_mod._SENSITIVE_BUCKETS.clear()
        tc, _, _ = sb_client
        as_user()
        # burn the delete budget first — export must be unaffected
        for _ in range(5):
            assert tc.delete("/v1/teams/nope").status_code == 403
        assert tc.get("/v1/teams/nope/export").status_code == 403  # budget 1
        assert tc.get("/v1/teams/nope/export").status_code == 403  # budget 2
        assert tc.get("/v1/teams/nope/export").status_code == 429  # exhausted
        ha_mod._SENSITIVE_BUCKETS.clear()
