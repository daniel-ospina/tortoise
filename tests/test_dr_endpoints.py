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
    "R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "ak",
    "R2_SECRET_ACCESS_KEY": "sk", "R2_BUCKET": "tortoise-backups",
    "TELEGRAM_BOT_TOKEN": "123:t", "TELEGRAM_CHAT_ID": "1",
    "GITHUB_ISSUES_PAT": "ghp_x", "BACKUP_ALERT_ASSIGNEE": "daniel-ospina",
}


@pytest.fixture
def client():
    """TestClient with the internal key configured + a temp FalkorDBLite DB."""
    old_key = os.environ.get("FASTAPI_INTERNAL_KEY", "")
    os.environ["FASTAPI_INTERNAL_KEY"] = _INTERNAL_KEY
    ha_mod._INTERNAL_KEY = _INTERNAL_KEY
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
            ha_mod._INTERNAL_KEY = old_key


@pytest.fixture(autouse=True)
def _quiet_watcher(monkeypatch):
    monkeypatch.setenv("BACKUP_WATCHER_DISABLED", "1")


@pytest.fixture
def dr_env(monkeypatch):
    for k, v in GOOD_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("FASTAPI_INTERNAL_KEY", _INTERNAL_KEY)
    ha_mod._INTERNAL_KEY = _INTERNAL_KEY
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
                            ("/v1/internal/backups/drill", "post")):
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
        assert body["target_graph"] not in sdk._get_proj().db.list_graphs()

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
