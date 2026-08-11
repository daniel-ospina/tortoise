"""Integration tests for the #596 internal DR endpoints (sweep / status /
heartbeat / simulate / re-baseline / drill)."""

from __future__ import annotations

import base64
import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
from tortoise.hosted_backup import MemoryStorage
from tortoise.sdk import TortoiseSDK

_INTERNAL_KEY = "test-internal-shared-secret-xyz"
INTERNAL_HEADERS = {"Authorization": f"Bearer {_INTERNAL_KEY}"}
GOOD_ENV = {
    "BACKUP_SWEEP_ENABLED": "true",
    "TORTOISE_BACKUP_KEY": base64.b64encode(b"k" * 32).decode(),
    # #661: sweep archives encrypt with the Fly-only registry stream key —
    # fail-closed when missing, so the DR test env must provide it.
    "REGISTRY_STREAM_KEY": base64.b64encode(b"s" * 32).decode(),
    "R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "ak",
    "R2_SECRET_ACCESS_KEY": "sk", "R2_BUCKET": "tortoise-backups",
    "TELEGRAM_BOT_TOKEN": "123:t", "TELEGRAM_CHAT_ID": "1",
    "DR_ISSUES_PAT": "ghp_x", "BACKUP_ALERT_ASSIGNEE": "daniel-ospina",
}


@pytest.fixture
def client():
    """TestClient with the internal key configured + a temp FalkorDBLite DB."""
    old_key = os.environ.get("FASTAPI_INTERNAL_KEY", "")
    os.environ["FASTAPI_INTERNAL_KEY"] = _INTERNAL_KEY
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        _orig_init = ha_mod.TortoiseSDK.__init__

        def _patched_init(self, db_path_arg=None, *, namespace=None, **kwargs):
            _orig_init(self, db_path, namespace=namespace)

        ha_mod.TortoiseSDK.__init__ = _patched_init
        try:
            with TestClient(ha_mod.app) as tc:
                yield tc
        finally:
            ha_mod.TortoiseSDK.__init__ = _orig_init
            os.environ["FASTAPI_INTERNAL_KEY"] = old_key


@pytest.fixture(autouse=True)
def _quiet_watcher(monkeypatch):
    monkeypatch.setenv("BACKUP_WATCHER_DISABLED", "1")


@pytest.fixture
def dr_env(monkeypatch):
    for k, v in GOOD_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
    yield


@pytest.fixture
def mem_storage(monkeypatch):
    store = MemoryStorage()
    monkeypatch.setattr(ha_mod, "_backup_storage", lambda: store)
    return store


def _seed_team(team_id: str = "team_x", nodes: int = 2) -> None:
    sdk = TortoiseSDK("/tmp/x.db", namespace="registry")
    reg = sdk._get_registry()
    reg.query("MATCH (t:Team {id:$id}) DELETE t", params={"id": team_id})
    reg.query("CREATE (t:Team {id:$id, tier:'pro'})", params={"id": team_id})
    g = sdk._get_proj().db.select_graph(f"team_{team_id}")
    for i in range(nodes):
        g.query(
            "CREATE (p:Point {id:$id, content:$c, pointKind:'claim'})",
            params={"id": f"pt-{i}", "c": f"c{i}"},
        )


class TestDrAuth:
    def test_internal_endpoints_reject_bad_key(self, client, mem_storage):
        for path, method in (("/v1/internal/backups/sweep", "post"),
                            ("/v1/internal/backups/status", "get"),
                            ("/v1/internal/backups/drill", "post"),
                            ("/v1/internal/reconcile", "post")):
            r = getattr(client, method)(
                path, headers={"Authorization": "Bearer wrong"},
                **({"json": {}} if method == "post" else {}),
            )
            assert r.status_code == 401, path


class TestDrStatus:
    def test_status_disabled_by_default(self, client, mem_storage):
        r = client.get("/v1/internal/backups/status", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    def test_status_enabled_with_config(self, client, dr_env, mem_storage):
        r = client.get("/v1/internal/backups/status", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert "watcher" in body and "driver" in body


class TestDrSimulate:
    def test_simulate_403_when_disabled(self, client, dr_env, mem_storage):
        r = client.post("/v1/internal/backups/simulate-stale", headers=INTERNAL_HEADERS)
        assert r.status_code == 403

    def test_simulate_stale_then_recover(self, client, dr_env, mem_storage, monkeypatch):
        monkeypatch.setenv("BACKUP_SIMULATE_ENABLED", "true")
        r = client.post("/v1/internal/backups/simulate-stale", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["status"] == "simulated_stale"
        assert len(mem_storage.list("ops/simulate/")) == 1
        r2 = client.post("/v1/internal/backups/simulate-recover", headers=INTERNAL_HEADERS)
        assert r2.status_code == 200
        assert mem_storage.list("ops/simulate/") == []


class TestDrHeartbeat:
    def test_heartbeat_writes_object(self, client, dr_env, mem_storage):
        r = client.post(
            "/v1/internal/driver/heartbeat", headers=INTERNAL_HEADERS,
            json={"run_id": "r1"},
        )
        assert r.status_code == 200
        hb = json.loads(mem_storage.download(ha_mod._DRIVER_HEARTBEAT_KEY))
        assert hb["body"]["run_id"] == "r1"


class TestDrSweep:
    def test_sweep_backs_up_seeded_team(self, client, dr_env, mem_storage):
        _seed_team("team_x", nodes=2)
        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "backed_up"
        assert body["teams_backed_up"] == 1
        assert body["results"]["team_x"]["status"] == "backed_up"
        manifests = [k for k in mem_storage.list("backups/team_x/") if k.endswith("manifest.json")]
        assert len(manifests) == 1
        manifest = json.loads(mem_storage.download(manifests[0]))
        assert manifest["graph_name"] == "team_team_x"
        assert manifest["node_count"] == 2

    def test_sweep_no_teams(self, client, dr_env, mem_storage):
        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["status"] == "no_teams"


class TestDrRebaseline:
    def test_rebaseline_requires_team(self, client, dr_env, mem_storage):
        r = client.post("/v1/internal/backups/re-baseline", headers=INTERNAL_HEADERS, json={})
        assert r.status_code == 400

    def test_rebaseline_updates_state(self, client, dr_env, mem_storage):
        _seed_team("team_x", nodes=3)
        mem_storage.upload(
            "ops/teams/team_x/state.json",
            json.dumps({"node_count": 10}).encode(),
        )
        r = client.post(
            "/v1/internal/backups/re-baseline", headers=INTERNAL_HEADERS,
            json={"team_id": "team_x"},
        )
        assert r.status_code == 200
        assert r.json()["node_count"] == 3
        state = json.loads(mem_storage.download("ops/teams/team_x/state.json"))
        assert state["node_count"] == 3


class TestDrDrill:
    def test_drill_requires_params(self, client, dr_env, mem_storage):
        r = client.post("/v1/internal/backups/drill", headers=INTERNAL_HEADERS, json={})
        assert r.status_code == 400

    def test_drill_restores_to_scratch(self, client, dr_env, mem_storage):
        _seed_team("team_x", nodes=2)
        # Produce a real archive for team_x via the sweep pipeline.
        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.json()["status"] == "backed_up"
        manifest = [
            k for k in mem_storage.list("backups/team_x/") if k.endswith("manifest.json")
        ][0]
        backup_key = manifest.replace("/manifest.json", "/dump.enc")

        ha_mod._LAST_DRILL_AT = 0.0  # clear cooldown
        r2 = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"team_id": "team_x", "backup_key": backup_key},
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["status"] == "drill_ok"
        assert body["target_graph"].startswith("_drill_")
        # Live graph untouched, scratch cleaned.
        sdk = TortoiseSDK("/tmp/x.db", namespace="registry")
        live = sdk._get_proj().db.select_graph("team_team_x")
        assert live.query("MATCH (n) RETURN count(n)").result_set[0][0] == 2
        graphs = sdk._get_proj().db.list_graphs()
        assert body["target_graph"] not in graphs
        # Zero production writes (review P2-8): no registry end-stamp, no
        # live-named staging/pre-restore scratch graphs.
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (t:Team {id:$id}) RETURN t.backup_restored_at",
            params={"id": "team_x"},
        ).result_set
        assert not rows or rows[0][0] is None, "drill must skip the end-stamp"
        assert not [g for g in graphs if g.startswith("team_team_x") and ("_restore_" in g or "_pre_restore_" in g)]

    def test_drill_cooldown(self, client, dr_env, mem_storage):
        _seed_team("team_x", nodes=1)
        client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        manifest = [
            k for k in mem_storage.list("backups/team_x/") if k.endswith("manifest.json")
        ][0]
        backup_key = manifest.replace("/manifest.json", "/dump.enc")
        ha_mod._LAST_DRILL_AT = 0.0
        r1 = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"team_id": "team_x", "backup_key": backup_key},
        )
        assert r1.status_code == 200
        r2 = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"team_id": "team_x", "backup_key": backup_key},
        )
        assert r2.status_code == 429


class TestReconcile:
    """POST /v1/internal/reconcile — expired-key sweep (#654)."""

    def _seed_keys(self, sdk, entries: list[dict]) -> None:
        """Insert APIKey nodes directly into the registry graph."""
        reg = sdk._get_registry()
        for e in entries:
            reg.query(
                "CREATE (k:APIKey {id:$id, key_prefix:$prefix, hash:'test', "
                "team_id:$tid, created_by:$by, created_at:$now, "
                "revoked_at:$rev, expires_at:$exp, created_via:$via})",
                params={
                    "id": e["id"],
                    "prefix": e.get("prefix", "tt_test"),
                    "tid": e.get("team_id", "team-reconcile"),
                    "by": "test",
                    "now": "2025-01-01T00:00:00+00:00",
                    "rev": e.get("revoked_at"),
                    "exp": e.get("expires_at"),
                    "via": e.get("created_via", "bootstrap"),
                },
            )

    def test_reconcile_rejects_bad_key(self, client):
        r = client.post(
            "/v1/internal/reconcile",
            headers={"Authorization": "Bearer wrong"},
        )
        assert r.status_code == 401

    def test_reconcile_rejects_missing_auth(self, client):
        r = client.post("/v1/internal/reconcile")
        assert r.status_code == 401

    def test_reconcile_no_expired_keys(self, client):
        r = client.post("/v1/internal/reconcile", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["expired_keys_swept"] == 0
        assert "bootstrap-expiry sweep complete" in body["notes"]

    def test_reconcile_sweeps_expired_bootstrap_keys(self, client):
        sdk = TortoiseSDK("/tmp/x.db", namespace="registry")
        self._seed_keys(sdk, [
            {"id": "expired-1", "expires_at": "2024-01-01T00:00:00+00:00"},
            {"id": "expired-2", "expires_at": "2024-06-15T00:00:00+00:00"},
        ])
        r = client.post("/v1/internal/reconcile", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["expired_keys_swept"] == 2
        # Verify keys are now revoked
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey) WHERE k.id IN ['expired-1','expired-2'] RETURN k.revoked_at"
        ).result_set
        assert len(rows) == 2
        assert all(r[0] is not None for r in rows), "expired keys must be revoked"

    def test_reconcile_preserves_active_keys(self, client):
        sdk = TortoiseSDK("/tmp/x.db", namespace="registry")
        future = "2099-01-01T00:00:00+00:00"
        self._seed_keys(sdk, [
            {"id": "active-1", "expires_at": future},
            {"id": "active-2", "expires_at": future},
        ])
        r = client.post("/v1/internal/reconcile", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["expired_keys_swept"] == 0
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {id:'active-1'}) RETURN k.revoked_at"
        ).result_set
        assert rows[0][0] is None, "active key must not be revoked"

    def test_reconcile_ignores_non_bootstrap_keys(self, client):
        sdk = TortoiseSDK("/tmp/x.db", namespace="registry")
        self._seed_keys(sdk, [
            {"id": "non-boot-1", "expires_at": "2024-01-01T00:00:00+00:00",
             "created_via": "provision"},
        ])
        r = client.post("/v1/internal/reconcile", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["expired_keys_swept"] == 0
        reg = sdk._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {id:'non-boot-1'}) RETURN k.revoked_at"
        ).result_set
        assert rows[0][0] is None, "non-bootstrap key must not be touched"

    def test_reconcile_skips_already_revoked(self, client):
        sdk = TortoiseSDK("/tmp/x.db", namespace="registry")
        self._seed_keys(sdk, [
            {"id": "already-revoked", "expires_at": "2024-01-01T00:00:00+00:00",
             "revoked_at": "2024-02-01T00:00:00+00:00"},
        ])
        r = client.post("/v1/internal/reconcile", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["expired_keys_swept"] == 0

    def test_reconcile_returns_reprovisioned_stub(self, client):
        """The reprovisioned field is a stub (returns 0) — verify shape."""
        r = client.post("/v1/internal/reconcile", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["reprovisioned"] == 0
        assert isinstance(body["notes"], list)
