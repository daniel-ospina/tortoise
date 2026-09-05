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
from tests._http_fixtures import patched_tortoise_sdk
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


@pytest.fixture(scope="session", autouse=True)
def _close_seed_sdks():
    """Close held seed SDKs at session end (see _SEED_SDKS docstring)."""
    yield
    while _SEED_SDKS:
        try:  # noqa: SIM105
            _SEED_SDKS.pop().close()
        except Exception:
            pass


@pytest.fixture
def client():
    """TestClient with the internal key configured + a temp FalkorDBLite DB."""
    old_key = os.environ.get("FASTAPI_INTERNAL_KEY", "")
    os.environ["FASTAPI_INTERNAL_KEY"] = _INTERNAL_KEY
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        # #2127 wave 2: shared helper — patch __init__ → temp DB, #1950
        # TORTOISE_DB_PATH pin, close-then-clear at enter; pop-env → restore
        # __init__ → deterministic anchor close → clear overrides at exit.
        # Supersedes the inline _patched_init/clear/restore trio (restore
        # was restore-init-only — no pin, no anchor close). The _SEED_SDKS
        # hold is DISJOINT from _FALLBACK_KEEPALIVE (seeded SDKs are held
        # directly, never registered as anchors) so it composes with the
        # helper unchanged — seeds still close at session end.
        try:
            with patched_tortoise_sdk(db_path), TestClient(ha_mod.app) as tc:
                yield tc
        finally:
            os.environ["FASTAPI_INTERNAL_KEY"] = old_key


@pytest.fixture(autouse=True)
def _quiet_watcher(monkeypatch):
    monkeypatch.setenv("BACKUP_WATCHER_DISABLED", "1")


@pytest.fixture(autouse=True)
def _clean_team_graphs(monkeypatch):
    """Epic #1647 (PR #1684 CI-fix): the DR tests seed the RAW team_team_x
    graph (select_graph(f"team_{team_id}")) — NON-test-prefixed, so the
    server lane's wipe_server skips it and seeds ACCUMULATE across tests
    (CREATE not MERGE → duplicate Points → rebaseline count 6 != 3). The
    embedded lane's wipe() clears everything per test; the server lane must
    mirror that by dropping the raw team graphs before each test."""
    yield
    _uri = os.environ.get("TORTOISE_DB_URI")
    if _uri and not os.environ.get("TORTOISE_TEST_CARVE_OUT"):
        try:
            from tortoise.config import is_loopback_uri
            if not is_loopback_uri(_uri):
                return  # never drop team_* on a remote/shared server
            import tortoise.hosted_api as _ha
            _sdk = _ha._make_sdk(namespace="registry")
            _db = _sdk._get_proj().db
            for _g in list(_db.list_graphs() or []):
                if _g.startswith("team_") and not _g.startswith("team_test_"):
                    try:  # noqa: SIM105
                        _db.select_graph(_g).delete()
                    except Exception:
                        pass
        except Exception:
            pass


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


# #1587/#1579: hold seed SDKs alive — `_seed_team` creates a registry SDK
# that goes out of scope at function end; with #1475 close-on-GC the shared
# embedded server is shut down before the sweep/drill handler opens its own
# SDK, so the seed's writes are lost ('no_teams' / empty-manifest IndexError
# flake). Same pattern as _REG_SDKS in test_invites_http.py (#1556).
_SEED_SDKS: list = []


def _seed_team(team_id: str = "team_x", nodes: int = 2) -> None:
    # The path arg is IGNORED under the client fixture's patched __init__
    # (all current callers use client); the SDK binds to the per-test temp DB.
    sdk = TortoiseSDK(namespace="registry")
    _SEED_SDKS.append(sdk)
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
        manifest = [  # noqa: RUF015
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
        manifest = [  # noqa: RUF015
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


class TestDrRebaselinePerGraph:
    """#2313 Task 5: re-baseline resolves the ACTIVE-graph seam (per-graph
    state; tombstone guard)."""

    def _seed_custom(self, team_id="team_x", gid="g_c1", ns="team_team_x_g_c1"):
        sdk = TortoiseSDK(namespace="registry")
        _SEED_SDKS.append(sdk)
        reg = sdk._get_registry()
        reg.query(
            "CREATE (g:Graph {id:$gid, team_id:$tid, kind:'custom', "
            "namespace:$ns, status:'active'})",
            params={"gid": gid, "tid": team_id, "ns": ns},
        )
        g = sdk._get_proj().db.select_graph(ns)
        g.query("CREATE (p:Point {id:'c-0', content:'c', pointKind:'claim'})")

    def test_rebaseline_default_writes_per_graph_and_mirror(self, client, dr_env, mem_storage):
        _seed_team("team_x", nodes=3)
        r = client.post(
            "/v1/internal/backups/re-baseline", headers=INTERNAL_HEADERS,
            json={"team_id": "team_x"},
        )
        assert r.status_code == 200
        assert r.json()["graph_id"] == "default"
        # per-graph state written AND the legacy team mirror
        assert json.loads(mem_storage.download(
            "ops/teams/team_x/graphs/default/state.json"))["node_count"] == 3
        assert json.loads(mem_storage.download(
            "ops/teams/team_x/state.json"))["node_count"] == 3

    def test_rebaseline_custom_graph_writes_only_per_graph(self, client, dr_env, mem_storage):
        _seed_team("team_x", nodes=2)
        self._seed_custom()
        mem_storage.upload(
            "ops/teams/team_x/state.json",
            json.dumps({"node_count": 99}).encode(),
        )
        r = client.post(
            "/v1/internal/backups/re-baseline", headers=INTERNAL_HEADERS,
            json={"team_id": "team_x", "graph_id": "g_c1"},
        )
        assert r.status_code == 200
        assert r.json()["node_count"] == 1
        state = json.loads(mem_storage.download(
            "ops/teams/team_x/graphs/g_c1/state.json"))
        assert state["node_count"] == 1
        # team mirror untouched for custom re-baseline
        assert json.loads(mem_storage.download(
            "ops/teams/team_x/state.json"))["node_count"] == 99

    def test_rebaseline_refuses_tombstoned_graph(self, client, dr_env, mem_storage):
        _seed_team("team_x", nodes=2)
        sdk = TortoiseSDK(namespace="registry")
        _SEED_SDKS.append(sdk)
        sdk._get_registry().query(
            "CREATE (g:Graph {id:'g_dead', team_id:'team_x', kind:'custom', "
            "namespace:'team_team_x_g_dead', status:'deleted'})")
        r = client.post(
            "/v1/internal/backups/re-baseline", headers=INTERNAL_HEADERS,
            json={"team_id": "team_x", "graph_id": "g_dead"},
        )
        assert r.status_code == 400
        assert "not an active graph" in r.json()["detail"]


class TestDrDrillPerGraph:
    """#2313 Task 5: drill resolves the archive's graph through the active
    seam; tombstoned/custom archives refused or restored to scratch."""

    def _sweep_and_pick(self, client, mem_storage, graph_segment):
        client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        keys = [k for k in mem_storage.list(f"backups/team_x/{graph_segment}/")
                if k.endswith("manifest.json")]
        assert len(keys) == 1
        return keys[0].replace("/manifest.json", "/dump.enc")

    def test_drill_custom_graph_restores_to_scratch(self, client, dr_env, mem_storage):
        _seed_team("team_x", nodes=2)
        sdk = TortoiseSDK(namespace="registry")
        _SEED_SDKS.append(sdk)
        reg = sdk._get_registry()
        reg.query(
            "CREATE (g:Graph {id:'g_c1', team_id:'team_x', kind:'custom', "
            "namespace:'team_team_x_g_c1', status:'active'})")
        g = sdk._get_proj().db.select_graph("team_team_x_g_c1")
        g.query("CREATE (p:Point {id:'c-0', content:'c', pointKind:'claim'})")
        key = self._sweep_and_pick(client, mem_storage, "g_c1")
        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"team_id": "team_x", "backup_key": key},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "drill_ok"
        assert r.json()["target_graph"].startswith("_drill_")

    def test_drill_refuses_tombstoned_after_backup(self, client, dr_env, mem_storage):
        """Back the custom graph up while ACTIVE, then tombstone it (a graph
        deleted AFTER its last backup) — the drill's resolution must refuse:
        quarantined archives are never a drill/restore source (#2304)."""
        _seed_team("team_x", nodes=1)
        sdk = TortoiseSDK(namespace="registry")
        _SEED_SDKS.append(sdk)
        reg = sdk._get_registry()
        reg.query(
            "CREATE (g:Graph {id:'g_x', team_id:'team_x', kind:'custom', "
            "namespace:'team_team_x_g_x', status:'active'})")
        g = sdk._get_proj().db.select_graph("team_team_x_g_x")
        g.query("CREATE (p:Point {id:'x-0', content:'x', pointKind:'claim'})")
        key = self._sweep_and_pick(client, mem_storage, "g_x")
        reg.query(
            "MATCH (g:Graph {id:'g_x'}) SET g.status = 'deleted'")
        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"team_id": "team_x", "backup_key": key},
        )
        assert r.status_code == 409, r.text
        assert "not an active graph" in r.json()["detail"]



class TestRegistrySdkRetry:
    """#1579: _registry_sdk() retries ONCE on a transient embedded-DB CONNECT
    failure (redis ConnectionError / OSError-family under parallel temp-DB
    contention) — the drill/sweep/restore handlers must not 500 on a momentary
    connect blip. A persistent failure or a timeout is never retried past one
    attempt (a genuinely broken/hung DB must keep failing).

    Mirrors the #1565 probe_db retry semantics (monitoring._is_transient_connect_error)
    via stub-SDK injection, same style as test_monitoring.py.
    """

    def test_transient_connect_retries_once_and_recovers(self, monkeypatch):
        """First connect raises a transient redis ConnectionError → the eager
        _get_proj() retry must recover and the SDK must be usable.

        The retry-success path returns a fake projection (test_monitoring.py
        FlakySDK style) — never the real _make_sdk: that would open the shared
        process-global embedded path and flake with EmbeddedStoreBusyError
        under ambient daemon state (#1579 review P1)."""
        import redis.exceptions as redis_exc

        calls = {"n": 0}

        class _FlakySDK:
            def _get_proj(self):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise redis_exc.ConnectionError("transient connect refused")
                return object()  # fake projection — retry recovered

        monkeypatch.setattr(ha_mod, "_make_sdk", lambda *, namespace=None: _FlakySDK())
        sdk = ha_mod._registry_sdk()
        assert calls["n"] == 2  # exactly ONE retry
        assert sdk._get_proj() is not None

    def test_persistent_connect_error_raises_after_retry(self, monkeypatch):
        """A PERSISTENT connect failure (real outage) must still raise after the
        single retry — the retry never masks a broken DB."""
        import redis.exceptions as redis_exc

        calls = {"n": 0}

        class _DeadSDK:
            def _get_proj(self):
                calls["n"] += 1
                raise redis_exc.ConnectionError("NXDOMAIN")

        monkeypatch.setattr(ha_mod, "_make_sdk", lambda *, namespace=None: _DeadSDK())
        with pytest.raises(redis_exc.ConnectionError):
            ha_mod._registry_sdk()
        assert calls["n"] == 2  # retried once, then raised

    def test_timeout_is_never_retried(self, monkeypatch):
        """A timeout (hung DB) is NEVER retried — exactly one attempt, then raise
        (builtin TimeoutError is an OSError subclass; monitoring excludes it FIRST)."""
        calls = {"n": 0}

        class _HungSDK:
            def _get_proj(self):
                calls["n"] += 1
                raise TimeoutError("hung connect")

        monkeypatch.setattr(ha_mod, "_make_sdk", lambda *, namespace=None: _HungSDK())
        with pytest.raises(TimeoutError):
            ha_mod._registry_sdk()
        assert calls["n"] == 1  # timeout → no retry


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


class TestDrAclReconcile:
    """#2313 folded delta: post-restore per-graph ACL rebuild — the endpoint
    replays create_acl_user (idempotent upsert) for every ACTIVE custom graph
    of every eligible team; fail-soft on ACL-layer absence."""

    def test_acl_reconcile_covers_custom_graphs_and_is_fail_soft(self, client, dr_env, mem_storage, monkeypatch):
        _seed_team("team_x", nodes=1)
        sdk = TortoiseSDK(namespace="registry")
        _SEED_SDKS.append(sdk)
        reg = sdk._get_registry()
        # eligible for hosted backup (acl reconcile enumerates eligible teams)
        reg.query("MATCH (t:Team {id:'team_x'}) SET t.backup_enabled = true")
        reg.query(
            "CREATE (g:Graph {id:'g_a', team_id:'team_x', kind:'custom', "
            "namespace:'team_team_x_g_a', status:'active'})")
        reg.query(
            "CREATE (g:Graph {id:'g_dead', team_id:'team_x', kind:'custom', "
            "namespace:'team_team_x_g_dead', status:'deleted'})")
        calls: list = []
        monkeypatch.setattr(
            "tortoise.acl_graph_users.create_acl_user",
            lambda graph_id, team_id: calls.append((graph_id, team_id)) or {"username": "u"},
        )
        r = client.post(
            "/v1/internal/backups/acl-reconcile", headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "reconciled"
        team_res = body["results"]["team_x"]
        assert team_res["custom_graphs_ok"] == 1
        assert team_res["default_skipped"] == 1
        # the ACTIVE custom graph is rebuilt; the tombstone is never touched
        assert ("g_a", "team_x") in calls
        assert ("g_dead", "team_x") not in calls

    def test_acl_reconcile_requires_config(self, client, mem_storage):
        r = client.post(
            "/v1/internal/backups/acl-reconcile", headers=INTERNAL_HEADERS)
        assert r.status_code == 503
