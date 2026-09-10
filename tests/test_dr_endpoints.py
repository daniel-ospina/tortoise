"""Integration tests for the #596 internal DR endpoints (sweep / status /
heartbeat / simulate / re-baseline / drill)."""

from __future__ import annotations

import base64
import json
import os
import tempfile
from typing import ClassVar

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

    def test_status_surfaces_last_sweep_rollup(self, client, dr_env, mem_storage):
        """#2372: /status surfaces the sweep's per-graph roll-up (totals,
        failures, streaks) from ops/state.json — not just the watcher's
        per-team tri-state."""
        mem_storage.upload("ops/state.json", json.dumps({
            "last_team_count": 2,
            "last_sweep_at": "2026-09-06T10:00:00+00:00",
            "graph_totals": {"attempted": 3, "backed_up": 2, "errors": 1},
            "graph_failures": [{"team_id": "team_x", "graph_id": "g_a",
                                "error": "boom", "streak": 2}],
            "graph_error_streaks": {"team_x:g_a": 2},
            # #2823: the dialect the sweep enumerated. This is the field that
            # distinguishes "backed up nothing because the deployment is
            # empty" from "backed up nothing because it enumerated the wrong
            # control plane" — the 31-day blind spot behind #2823.
            "source": "supabase",
        }).encode())
        r = client.get("/v1/internal/backups/status", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        ls = r.json()["last_sweep"]
        assert ls["last_sweep_at"] == "2026-09-06T10:00:00+00:00"
        assert ls["graph_totals"]["errors"] == 1
        assert ls["graph_failures"][0]["streak"] == 2
        assert ls["graph_error_streaks"] == {"team_x:g_a": 2}
        # #2823: VALUE, not shape — `assert "source" in ls` is vacuous because
        # the endpoint builds the key unconditionally (`sweep_state.get(k)`);
        # only an equality assertion fails if the field is dropped or hardcoded
        # to None, which is exactly the state that leaves a wrong-dialect run
        # indistinguishable from an empty deployment.
        assert ls["source"] == "supabase"

    def test_status_last_sweep_is_none_before_any_sweep(self, client, dr_env,
                                                        mem_storage):
        """Boundary: with no ops/state.json the projection is omitted entirely —
        the #2823 `source` field must not turn into a half-empty block."""
        r = client.get("/v1/internal/backups/status", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["last_sweep"] is None

    def test_status_degrades_gracefully_without_a_source_key(
            self, client, dr_env, mem_storage):
        """An older app build's roll-up has no `source` — it must surface as
        None, never raise or drop the whole last_sweep block."""
        mem_storage.upload("ops/state.json", json.dumps({
            "last_team_count": 1,
            "last_sweep_at": "2026-09-06T10:00:00+00:00",
        }).encode())
        r = client.get("/v1/internal/backups/status", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["last_sweep"]["source"] is None


class TestDrLock:
    """#2319: /status lock block + /v1/internal/backups/verify-lock."""

    def test_status_lock_block_absent_when_disabled(self, client, mem_storage):
        r = client.get("/v1/internal/backups/status", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["lock"] is None  # no surface change until lock is on

    def test_status_lock_unverifiable_without_token(self, client, dr_env, mem_storage, monkeypatch):
        monkeypatch.setenv("BACKUP_LOCK_ENABLED", "true")
        monkeypatch.setenv("BACKUP_LOCK_DAYS", "3")
        r = client.get("/v1/internal/backups/status", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        lock = r.json()["lock"]
        assert lock["enabled"] is True
        assert lock["expected_days"] == 3
        assert lock["prefix"] == "backups/"
        assert lock["status"] == "unverifiable"  # no CF_API_TOKEN → runbook path

    def test_verify_lock_absent_when_not_enabled(self, client, dr_env, mem_storage):
        r = client.post("/v1/internal/backups/verify-lock", headers=INTERNAL_HEADERS, json={})
        assert r.status_code == 200
        assert r.json()["status"] == "absent"
        assert r.json()["enabled"] is False

    def test_verify_lock_verified_with_token_and_rule(
            self, client, dr_env, mem_storage, monkeypatch):
        monkeypatch.setenv("BACKUP_LOCK_ENABLED", "true")
        monkeypatch.setenv("BACKUP_LOCK_DAYS", "3")
        monkeypatch.setenv("CF_API_TOKEN", "cf-token")
        import tortoise.hosted_backup as hb

        rules = [{"id": "backups-lock-3d", "enabled": True, "prefix": "backups/",
                  "condition": {"type": "Age", "maxAgeSeconds": 3 * 86400}}]
        monkeypatch.setattr(
            hb, "_lock_rules_http_get",
            lambda url, headers, timeout: {"success": True, "result": {"rules": rules}})
        r = client.post("/v1/internal/backups/verify-lock", headers=INTERNAL_HEADERS, json={})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "verified"
        assert body["enabled"] is True
        assert body["bucket"] == "tortoise-backups"

    def test_verify_lock_reports_drift_and_unverifiable(
            self, client, dr_env, mem_storage, monkeypatch):
        monkeypatch.setenv("BACKUP_LOCK_ENABLED", "true")
        monkeypatch.setenv("BACKUP_LOCK_DAYS", "3")
        monkeypatch.setenv("CF_API_TOKEN", "cf-token")
        import tortoise.hosted_backup as hb

        # rule shorter than the contract window → drift
        monkeypatch.setattr(
            hb, "_lock_rules_http_get",
            lambda url, headers, timeout: {"success": True, "result": {"rules": [
                {"id": "r", "enabled": True, "prefix": "backups/",
                 "condition": {"type": "Age", "maxAgeSeconds": 86400}}]}})
        r = client.post("/v1/internal/backups/verify-lock", headers=INTERNAL_HEADERS, json={})
        assert r.json()["status"] == "drift"

        # transport failure → unverifiable (never 500, never blocks backups)
        def boom(url, headers, timeout):
            from tortoise.hosted_backup import LockVerificationError
            raise LockVerificationError("cannot read R2 bucket-lock rules: boom")

        monkeypatch.setattr(hb, "_lock_rules_http_get", boom)
        r = client.post("/v1/internal/backups/verify-lock", headers=INTERNAL_HEADERS, json={})
        assert r.status_code == 200
        assert r.json()["status"] == "unverifiable"

    def test_verify_lock_override_target_store(
            self, client, dr_env, mem_storage, monkeypatch):
        """The body {account_id, bucket} override lets the runbook check the
        mirror store with the same rule contract."""
        monkeypatch.setenv("BACKUP_LOCK_ENABLED", "true")
        monkeypatch.setenv("CF_API_TOKEN", "cf-token")
        import tortoise.hosted_backup as hb

        seen = {}
        monkeypatch.setattr(
            hb, "_lock_rules_http_get",
            lambda url, headers, timeout: (seen.update(url=url) or
                                           {"success": True, "result": {"rules": []}}))
        r = client.post(
            "/v1/internal/backups/verify-lock", headers=INTERNAL_HEADERS,
            json={"account_id": "mirror-acct", "bucket": "tortoise-backups-mirror"})
        assert r.status_code == 200
        assert r.json()["bucket"] == "tortoise-backups-mirror"
        assert "mirror-acct" in seen["url"]
        assert r.json()["status"] == "absent"  # empty rules → absent


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

    def test_sweep_reports_resolved_control_plane(self, client, dr_env, mem_storage):
        """#2823: the run result names the dialect it actually enumerated, so a
        wrong-source read is diagnosable from one run's output instead of being
        indistinguishable from an empty deployment."""
        _seed_team("team_x", nodes=2)
        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["source"] == "registry"

    def test_sweep_enumerates_supabase_control_plane(
            self, client, dr_env, mem_storage, monkeypatch):
        """#2823 REGRESSION (P0): in the Supabase control-plane lane the sweep
        must enumerate `teams` through the PostgREST control plane.

        Pre-fix the endpoint passed the raw FalkorDB registry handle
        (`_registry_sdk()._get_registry()`); `_is_supabase_source()` is False
        for a graph handle, so the sweep took the Cypher branch against the
        graph #669 DELETED, enumerated 0 teams, and returned a benign
        `no_teams` — production backed nothing up for 31 days while the driver
        self-healed every run as healthy.
        """
        from tests.fake_control_plane import FakeControlPlane

        cp = FakeControlPlane().seed("teams", [
            {"id": "team_s1", "graph_name": "team_team_s1",
             "tier": "pro", "backup_enabled": True},
            {"id": "team_s2", "graph_name": "team_team_s2",
             "tier": "pro", "backup_enabled": True},
        ])
        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: cp)
        # Seed the DATA plane (FalkorDB stays the graph store in both lanes).
        db = ha_mod._make_sdk(namespace=None)._get_proj().db
        for tid in ("team_s1", "team_s2"):
            g = db.select_graph(f"team_{tid}")
            g.query("CREATE (p:Point {id:'p1', content:'c', pointKind:'claim'})")

        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "backed_up", body
        assert body["teams_backed_up"] == 2
        assert body["source"] == "supabase"
        assert set(body["results"]) == {"team_s1", "team_s2"}
        # Every enumerated team produced a real archive (not a no-op).
        for tid in ("team_s1", "team_s2"):
            assert body["results"][tid]["status"] == "backed_up"
            manifests = [k for k in mem_storage.list(f"backups/{tid}/")
                         if k.endswith("manifest.json")]
            assert len(manifests) == 1, tid

    def test_sweep_no_teams_reports_source(self, client, dr_env, mem_storage):
        """#2823: even a 0-team run names its dialect — that field is what makes
        "backups stopped" a one-run diagnosis instead of a 31-day blind spot."""
        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "no_teams"
        assert body["source"] == "registry"

    def test_status_surfaces_the_dialect_a_real_sweep_wrote(
            self, client, dr_env, mem_storage):
        """#2823 end-to-end (persist → surface): a real sweep's `source` must
        come back out of `/status` unchanged. Guards the whole chain instead of
        each half against a hand-written fixture."""
        _seed_team("team_x", nodes=1)
        sweep = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert sweep.status_code == 200, sweep.text
        assert sweep.json()["status"] == "backed_up"
        assert sweep.json()["source"] == "registry"
        r = client.get("/v1/internal/backups/status", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        assert r.json()["last_sweep"]["source"] == "registry"

    def test_sweep_empty_supabase_lane_is_a_quiet_confirmed_empty(
            self, client, dr_env, mem_storage, monkeypatch):
        """#2823 boundary: the wrong-dialect refusal is ONE-DIRECTIONAL. A
        correct-lane Supabase read that genuinely finds no teams is a benign
        `no_teams` — NOT `enum_failed` — and still reports its dialect."""
        from tests.fake_control_plane import FakeControlPlane

        cp = FakeControlPlane().seed("teams", [])
        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: cp)
        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "no_teams", body
        assert body["teams_backed_up"] == 0
        assert body["source"] == "supabase"


class TestSupabaseLaneSeam:
    """#2823 / #2340: EVERY backup/DR operator must resolve the control plane
    through the shared dialect-aware seam.

    The change converted six handler call sites from
    `_registry_sdk()._get_registry()` to `_control_plane_source()`
    (`backups_sweep`, `backups_purge`, `backups_rebaseline`, `backups_drill`,
    `backups_drill_scheduled`, `_reconcile_acl_users_sync`) plus two more that
    cannot use the hard-fail tripwire and are guarded by their outcome instead:
    `backups_list` (fail-soft by design — `_legacy_graph_overrides`) and the
    watcher lifespan (covered by `tests/test_backup_watcher.py` + the seam).
    The same handlers also obtained their DATA-plane handle (#1366: the
    `GRAPH.DELETE` / `select_graph` target) from `_registry_sdk()._get_proj()`,
    i.e. by opening the registry namespace — the auto-recreate artifact the
    #669 post-flip verification flagged; they now use
    `_make_sdk(namespace=None)._get_proj().db`.

    Only `backups_sweep` had a Supabase-lane integration test, so reverting any
    of the others — or a future refactor re-introducing the raw registry
    handle — left the suite green while re-creating the production defect (a
    moved/deleted registry graph reads as an empty deployment: a benign
    `no_teams`, a `{"teams": 0}` ACL no-op, a purge that erases nothing).

    These tests make the registry SDK a HARD FAILURE (`_registry_sdk` raises on
    ANY call) under the Supabase lane, then drive each endpoint and assert an
    OPERATOR-VISIBLE outcome (plus a control-plane consultation) — never merely
    "did not 500", which a 404/400/422 route-or-validation miss would also
    satisfy.
    """

    TEAM: ClassVar[dict] = {"id": "team_s1", "graph_name": "team_team_s1",
                          "tier": "pro", "backup_enabled": True}

    @staticmethod
    def _fortify_supabase_lane(monkeypatch):
        """Supabase lane + a registry SDK that is FORBIDDEN outright.

        #2823/#669: under the Supabase lane no handler may construct or open
        the registry namespace AT ALL — `_registry_sdk()` eagerly builds the
        registry projection, which re-materializes the control-plane namespace
        the #669 flip deleted (and `_get_registry()` then runs `CREATE INDEX`
        against it). The data-plane handle (``_make_sdk(namespace=None)``) is a
        different SDK and stays available. Returns the fake control plane so
        callers can seed/assert on it."""
        import tortoise.hosted_api as ha
        from tests.fake_control_plane import FakeControlPlane

        cp = FakeControlPlane().seed("teams", [dict(TestSupabaseLaneSeam.TEAM)])
        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: cp)

        def _forbidden_registry_sdk():
            raise AssertionError(
                "the registry-namespaced SDK was opened under the Supabase "
                "lane — resolve the control plane via _control_plane_source() "
                "and the data plane via _make_sdk(namespace=None) (#2823/#669)")

        monkeypatch.setattr(ha, "_registry_sdk", _forbidden_registry_sdk)
        return cp

    @staticmethod
    def _seed_data_plane() -> None:
        """The graph store stays FalkorDB in both lanes — the Supabase lane
        only changes where TEAMS/GRAPHS rows are read from."""
        import tortoise.hosted_api as ha
        g = ha._make_sdk(namespace=None)._get_proj().db.select_graph(
            TestSupabaseLaneSeam.TEAM["graph_name"])
        g.query("CREATE (p:Point {id:'p1', content:'c', pointKind:'claim'})")

    @pytest.mark.parametrize("path,body", [
        ("/v1/internal/backups/sweep", {}),
        ("/v1/internal/backups/purge", {}),
        ("/v1/internal/backups/acl-reconcile", {}),
    ])
    def test_supabase_lane_never_reads_the_raw_registry_handle(
            self, client, dr_env, mem_storage, monkeypatch, path, body):
        """Guarded by an OUTCOME, not just `status < 500`: a 404/400/422 would
        otherwise read as "the handler used the seam". The fake control plane
        must have been consulted, which is what proves the handler ran AND
        resolved through the dialect-aware seam (a route typo or an early
        validation failure fails here)."""
        cp = self._fortify_supabase_lane(monkeypatch)
        self._seed_data_plane()
        r = client.post(path, headers=INTERNAL_HEADERS, json=body)
        assert r.status_code != 404, (path, r.text)
        assert r.status_code < 500, (path, r.status_code, r.text)
        assert cp.query_count > 0, (path, r.text)

    def test_acl_reconcile_enumerates_the_supabase_control_plane(
            self, client, dr_env, mem_storage, monkeypatch):
        """#2340: pre-fix this returned `{"teams": 0}` — a silent no-op SUCCESS
        that rebuilt no ACLs after a full-platform restore."""
        monkeypatch.setattr(
            "tortoise.acl_graph_users.create_acl_user",
            lambda graph_id, team_id: {"username": "u"})
        cp = self._fortify_supabase_lane(monkeypatch)
        r = client.post("/v1/internal/backups/acl-reconcile",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "reconciled"
        assert body["teams"] == 1, body
        assert body["results"]["team_s1"]["status"] == "reconciled"
        assert cp.query_count > 0

    def test_purge_erases_expired_trash_via_the_supabase_control_plane(
            self, client, dr_env, mem_storage, monkeypatch):
        """#2340: pre-fix the purge enumerated the deleted registry graph, so
        expired trash was never erased in the Supabase lane. Asserted on the
        PURGE EFFECT (the expired tombstone is erased and its row stamped), not
        on a query count — a purge that enumerated the wrong/empty set returns
        `teams_purged: 0`, which a call-count assertion cannot distinguish."""
        from datetime import UTC, datetime, timedelta

        cp = self._fortify_supabase_lane(monkeypatch)
        expired = (datetime.now(UTC) - timedelta(days=30)).isoformat()
        cp.seed("graphs", [{
            "id": "g_old", "team_id": "team_s1", "name": "g_old",
            "kind": "custom", "status": "deleted",
            "namespace": "team_team_s1_g_old", "deleted_at": expired,
            "purged_at": None,
        }])
        r = client.post("/v1/internal/backups/purge", headers=INTERNAL_HEADERS,
                        json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "ok", body
        assert body["teams_purged"] == 1, body
        assert [p["graph_id"] for p in body["purged"]] == ["g_old"], body
        # The row was stamped through the SAME control plane (a write, not just
        # a read) — the trash can never be re-purged.
        stamped = next(g for g in cp.tables["graphs"] if g["id"] == "g_old")
        assert stamped["purged_at"], stamped

    def test_rebaseline_resolves_the_active_graph_via_the_supabase_seam(
            self, client, dr_env, mem_storage, monkeypatch):
        """The route is `/re-baseline` (hyphen) — a wrong spelling 404s and a
        `status < 500` assertion would silently pass, leaving this converted
        call site untested."""
        cp = self._fortify_supabase_lane(monkeypatch)
        self._seed_data_plane()
        r = client.post("/v1/internal/backups/re-baseline",
                        headers=INTERNAL_HEADERS, json={"team_id": "team_s1"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "rebaselined", body
        assert body["node_count"] == 1, body
        assert cp.query_count > 0

    def test_drill_resolves_the_active_graph_via_the_supabase_seam(
            self, client, dr_env, mem_storage, monkeypatch):
        """The drill resolves its target graph through the seam AFTER the
        team_id/backup_key validation — a body without a real archive key
        short-circuits before the seam and proves nothing."""
        cp = self._fortify_supabase_lane(monkeypatch)
        self._seed_data_plane()
        key = _default_drill_key(client, mem_storage, team="team_s1")
        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post("/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
                        json={"team_id": "team_s1", "backup_key": key})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "drill_ok", r.text
        assert cp.query_count > 0

    def test_drill_scheduled_resolves_via_the_supabase_seam(
            self, client, dr_env, mem_storage, monkeypatch):
        cp = self._fortify_supabase_lane(monkeypatch)
        self._seed_data_plane()
        assert client.post("/v1/internal/backups/sweep",
                           headers=INTERNAL_HEADERS).json()["status"] == "backed_up"
        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post("/v1/internal/backups/drill-scheduled",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "drill_ok", r.text
        assert cp.query_count > 0

    def test_backups_list_endpoint_reverse_lookup_uses_the_supabase_seam(
            self, client, dr_env, mem_storage, monkeypatch):
        """#2823: `backups_list` is the 8th converted call site. It fail-softs to
        the default bucket on any control-plane error by design, so the
        raise-stub tripwire CANNOT cover it — this drives the real endpoint and
        asserts the OUTCOME only the Supabase source can produce: a pre-#2313
        legacy flat manifest lists under its actual custom graph instead of
        being mislabeled as the default."""
        cp = self._fortify_supabase_lane(monkeypatch)
        cp.seed("graphs", [{"id": "g_custom", "team_id": "team_s1",
                            "name": "g_custom", "kind": "custom",
                            "status": "active",
                            "namespace": "team_team_s1_g_custom"}])
        backup_id = "team_s1/20260101T000000Z_ab12"
        mem_storage.upload(
            f"backups/{backup_id}/manifest.json",
            json.dumps({
                "backup_id": backup_id, "team_id": "team_s1",
                "graph_name": "team_team_s1_g_custom",
                "created_at": "2026-01-01T00:00:00+00:00",
                "node_count": 1, "edge_count": 0, "sha256": "0" * 64,
            }).encode())
        ha_mod.app.dependency_overrides[
            ha_mod.get_current_team_session_ungated
        ] = lambda: {"team_id": "team_s1"}
        try:
            r = client.get("/backups", headers=INTERNAL_HEADERS)
        finally:
            ha_mod.app.dependency_overrides.clear()
        assert r.status_code == 200, r.text
        entries = r.json()["backups"]
        assert [e["graph_id"] for e in entries] == ["g_custom"], entries
        assert cp.query_count > 0

    def test_sweep_in_flight_202_reports_the_dialect(self, client, dr_env,
                                                     mem_storage, monkeypatch):
        """#2823: the driver logs `sweep source:` for EVERY run — the lock-held
        202 is the one shape where an unresolved dialect would print `unknown`.
        Asserted on the real endpoint shape (no fabricated body)."""
        class _Locked:
            @staticmethod
            def locked() -> bool:
                return True

        monkeypatch.setattr(ha_mod, "_SWEEP_INFLIGHT", _Locked())
        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "already_running", body
        assert body["teams_backed_up"] == 0
        assert body["source"] == "registry"

    def test_watcher_lifespan_resolves_the_team_source_through_the_seam(
            self, client, dr_env, mem_storage, monkeypatch):
        """#2823: the watcher lifespan is the 7th resolved call site. Every
        other DR test runs with `BACKUP_WATCHER_DISABLED=1`, so nothing else
        enters this branch — reverting it to the raw registry handle (empty
        post-flip → chronic no_teams → no staleness incidents) must fail here.

        This test also caught an ADJACENT P0 in the same block: a function-local
        `import os` later in `_lifespan` shadowed the module-level `os`, so the
        branch's own `os.environ.get("BACKUP_WATCHER_DISABLED")` raised
        UnboundLocalError on every boot and was swallowed by its `except
        Exception` — the in-process staleness watcher never started at all.
        `assert watcher.started` is that regression guard (construction alone
        must not satisfy it — deleting `_WATCHER.start()` used to leave this
        green), and the provider call at the end proves the source the watcher
        ENUMERATES with is the seam-resolved control plane, not just that the
        seam was called.

        The watcher THREAD is stubbed out (it sleeps 60s before its first poll
        and would outlive the test); the lifespan branch itself runs for real.
        """
        import asyncio

        monkeypatch.delenv("BACKUP_WATCHER_DISABLED", raising=False)
        self._fortify_supabase_lane(monkeypatch)
        started: list = []
        seam_calls: list = []

        class _RecordingThread:
            def __init__(self, watcher, interval_seconds=0):
                self.watcher = watcher
                self.started = False

            def start(self) -> None:
                self.started = True
                started.append(self)

            def stop(self) -> None:
                pass

        monkeypatch.setattr("tortoise.backup_watcher.WatcherThread",
                            _RecordingThread)
        real_source = ha_mod._control_plane_source

        def _recording_source():
            seam_calls.append(1)
            return real_source()

        monkeypatch.setattr(ha_mod, "_control_plane_source", _recording_source)
        prev_watcher = ha_mod._WATCHER
        try:
            async def _boot() -> None:
                async with ha_mod._lifespan(ha_mod.app):
                    pass

            asyncio.run(_boot())
        finally:
            ha_mod._WATCHER = prev_watcher
        assert started, "the watcher branch never ran — config/env setup is wrong"
        assert seam_calls, ("the watcher lifespan did not resolve its control "
                            "plane through the shared seam")
        watcher = started[0]
        assert watcher.started, "the watcher thread was constructed but never started"
        # The provider the lifespan WIRED IN must enumerate the Supabase teams
        # (BackupWatcher stores it as `_teams`) — a partial revert that calls the
        # seam but hands the watcher the raw registry handle fails here.
        assert watcher.watcher._teams() == ["team_s1"]

class TestDrRebaseline:
    def test_rebaseline_requires_team(self, client, dr_env, mem_storage):
        r = client.post("/v1/internal/backups/re-baseline", headers=INTERNAL_HEADERS, json={})
        assert r.status_code == 400

    def test_rebaseline_rejects_malformed_ids(self, client, dr_env, mem_storage):
        """#2377: team_id/graph_id flow into R2 state keys — charset-gate the
        shape before any write (defense in depth; rows are server-generated
        today, but this endpoint must never mint keys off an attacker-shaped
        id)."""
        bad = [
            {"team_id": "team_x", "graph_id": "../esc"},
            {"team_id": "team_x", "graph_id": "g_a/b"},
            {"team_id": "../team", "graph_id": "default"},
        ]
        for body in bad:
            r = client.post("/v1/internal/backups/re-baseline",
                            headers=INTERNAL_HEADERS, json=body)
            assert r.status_code == 400, body
        # no state object was written under any escaped key
        assert mem_storage.list("ops/teams/") == []

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

    def test_drill_old_archive_after_stream_key_rotation(self, client, dr_env, mem_storage):
        """#2318: after a REGISTRY_STREAM_KEY rotation the app RETAINS the old
        key (REGISTRY_STREAM_KEY_PREVIOUS) so a pre-rotation sweep archive
        drills in-app — the DR runbook's manual-recovery path becomes the
        automated dual-key path."""
        _seed_team("team_x", nodes=2)
        # 1. Sweep encrypts under the CURRENT stream key ("s"*32).
        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.json()["status"] == "backed_up"
        manifest = [  # noqa: RUF015
            k for k in mem_storage.list("backups/team_x/") if k.endswith("manifest.json")
        ][0]
        backup_key = manifest.replace("/manifest.json", "/dump.enc")

        # 2. Rotate: new active stream key, OLD active retained as _PREVIOUS
        #    (the exact state tools/rotate-backup-keys.py stages on Fly).
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("REGISTRY_STREAM_KEY", base64.b64encode(b"z" * 32).decode())
        monkeypatch.setenv("REGISTRY_STREAM_KEY_PREVIOUS", base64.b64encode(b"s" * 32).decode())
        monkeypatch.setenv("BACKUP_SWEEP_ENABLED", "true")  # keep the sweep config live

        # 3. The drill (which passes cfg.backup_key) must decrypt the OLD
        #    archive through the retained stream key — no manual recovery.
        ha_mod._LAST_DRILL_AT = 0.0
        r2 = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"team_id": "team_x", "backup_key": backup_key},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "drill_ok"
        monkeypatch.undo()

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


class TestDrPurgeGraceGate:
    """#2566 (re-audit P3): the internal purge endpoint must refuse a
    grace_days override SHORTER than the standard recovery window unless the
    operator explicitly confirms it (a drill with a small value would
    otherwise permanently erase rows the trash UI/API still promise)."""
    def test_short_grace_requires_confirmation(self, client, dr_env,
                                               mem_storage):
        r = client.post("/v1/internal/backups/purge",
                        headers=INTERNAL_HEADERS,
                        json={"grace_days": 1})
        assert r.status_code == 422, r.text
        assert "confirm_short_grace" in r.json()["detail"]

    def test_short_grace_runs_with_confirmation(self, client, dr_env,
                                                mem_storage):
        r = client.post("/v1/internal/backups/purge",
                        headers=INTERNAL_HEADERS,
                        json={"grace_days": 1, "confirm_short_grace": True})
        assert r.status_code == 200, r.text
        assert r.json()["purged"] == []

    def test_standard_grace_needs_no_confirmation(self, client, dr_env,
                                                  mem_storage):
        r = client.post("/v1/internal/backups/purge",
                        headers=INTERNAL_HEADERS, json={})
        assert r.status_code == 200, r.text


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


# #2317 — scheduled verification-restore drill (monthly, unattended) + the
# shared drill core's pass/fail + measured-time record. The scheduled leg is
# exercised through POST /v1/internal/backups/drill-scheduled: auto-selection
# of the OLDEST eligible nested archive, tombstone-guard skipping, durable
# ops/drills/last.json records, RESTORE_DRILL_FAILED incident lifecycle, and
# /status → last_drill surfacing.

class _FakeAlerts:
    """Recording alert-store stub — captures open/resolve calls (no network)."""

    def __init__(self):
        self.calls: list = []

    def open_incident(self, kind, team_id="", detail=None):
        self.calls.append(("open", kind, team_id, dict(detail or {})))
        return True

    def resolve_incident(self, kind, team_id=""):
        self.calls.append(("resolve", kind, team_id))
        return True


def _backdate_archive(store, backup_key: str, created_at: str) -> None:
    """Backdate a stored archive's manifest created_at (sha256 untouched —
    restore verification still passes; selection order changes)."""
    mkey = backup_key.replace("/dump.enc", "/manifest.json")
    manifest = json.loads(store.download(mkey))
    manifest["created_at"] = created_at
    store.upload(mkey, json.dumps(manifest).encode())


def _default_drill_key(client, mem_storage, team="team_x") -> str:
    """Sweep the team once and return the NEWEST default-graph archive key
    (a sweep always adds one archive; retention keeps older ones)."""
    r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
    assert r.json()["status"] == "backed_up", r.text
    keys = [k for k in mem_storage.list(f"backups/{team}/default/")
            if k.endswith("dump.enc")]
    assert keys, f"no default archive for {team}"
    return sorted(keys)[-1]  # newest (lexicographic ts == chronological)


def _has_open(calls, kind, team="global") -> bool:
    return any(c[0] == "open" and c[1] == kind and c[2] == team for c in calls)


def _has_resolve(calls, kind, team="global") -> bool:
    return any(c[0] == "resolve" and c[1] == kind and c[2] == team for c in calls)


class TestDrDrillScheduled:
    """#2317 scheduled drill endpoint: oldest-eligible selection, records,
    incidents, cooldown, /status surfacing."""

    def test_requires_config(self, client, mem_storage):
        r = client.post("/v1/internal/backups/drill-scheduled",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 503  # fail-closed when the sweep is disabled

    def test_no_candidates_records_and_resolves(self, client, dr_env,
                                                mem_storage, monkeypatch):
        fake = _FakeAlerts()
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: fake)
        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post("/v1/internal/backups/drill-scheduled",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "no_candidates"
        assert body["record"]["status"] == "no_candidates"
        assert body["record"]["ok"] is False
        rec = json.loads(mem_storage.download(ha_mod._DRILL_RECORD_KEY))
        assert rec["status"] == "no_candidates"
        assert rec["run"] == "scheduled"
        # no candidates is a benign state → any open incident resolves
        assert _has_resolve(fake.calls, ha_mod._DRILL_FAILED_KIND)

    def test_restores_oldest_archive_and_records(self, client, dr_env,
                                                 mem_storage, monkeypatch):
        _seed_team("team_x", nodes=2)
        first = _default_drill_key(client, mem_storage)          # T1
        _backdate_archive(mem_storage, first, "2020-01-01T00:00:00+00:00")
        second = _default_drill_key(client, mem_storage)         # T2 (now)
        assert first != second
        fake = _FakeAlerts()
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: fake)
        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post("/v1/internal/backups/drill-scheduled",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "drill_ok"
        assert body["target_graph"].startswith("_drill_")
        # the OLDEST (backdated) archive was selected, not the newest
        assert body["record"]["backup_key"] == first
        assert body["record"]["status"] == "ok" and body["record"]["ok"]
        assert body["record"]["run"] == "scheduled"
        # measured restore time vs the committed RTO
        assert isinstance(body["duration_s"], (int, float))
        assert body["within_rto"] is True
        assert body["rto_s"] == ha_mod._DRILL_RTO_S
        rec = json.loads(mem_storage.download(ha_mod._DRILL_RECORD_KEY))
        assert rec["backup_key"] == first and rec["status"] == "ok"
        # success resolves any open incident
        assert _has_resolve(fake.calls, ha_mod._DRILL_FAILED_KIND)

    def test_skips_tombstoned_oldest_for_next_active(self, client, dr_env,
                                                     mem_storage, monkeypatch):
        """The OLDEST candidate is a deleted/quarantined graph's archive
        (#2304) — it is skipped (never drilled) and the next-oldest ACTIVE
        archive is restored instead."""
        _seed_team("team_x", nodes=1)
        sdk = TortoiseSDK(namespace="registry")
        _SEED_SDKS.append(sdk)
        reg = sdk._get_registry()
        reg.query(
            "CREATE (g:Graph {id:'g_dead', team_id:'team_x', kind:'custom', "
            "namespace:'team_team_x_g_dead', status:'active'})")
        g = sdk._get_proj().db.select_graph("team_team_x_g_dead")
        g.query("CREATE (p:Point {id:'d-0', content:'d', pointKind:'claim'})")
        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.json()["status"] == "backed_up"
        dead_key = [  # noqa: RUF015
            k for k in mem_storage.list("backups/team_x/g_dead/")
            if k.endswith("dump.enc")
        ][0]
        _backdate_archive(mem_storage, dead_key, "2020-01-01T00:00:00+00:00")
        reg.query("MATCH (g:Graph {id:'g_dead'}) SET g.status = 'deleted'")
        fake = _FakeAlerts()
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: fake)
        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post("/v1/internal/backups/drill-scheduled",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "drill_ok"
        # the tombstoned custom's archive was NOT drilled; the default was
        assert body["record"]["backup_key"].startswith("backups/team_x/default/")

    def test_failure_opens_incident_and_records(self, client, dr_env,
                                                mem_storage, monkeypatch):
        """A genuine drill failure (oldest archive corrupt — integrity check
        fails) returns 409, records status=rejected, and opens a deduplicated
        RESTORE_DRILL_FAILED incident; a later SUCCESSFUL run resolves it."""
        _seed_team("team_x", nodes=2)
        key = _default_drill_key(client, mem_storage)
        mem_storage.upload(key, b"corrupt-blob")  # sha256 mismatch
        fake = _FakeAlerts()
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: fake)
        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post("/v1/internal/backups/drill-scheduled",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 409, r.text
        rec = json.loads(mem_storage.download(ha_mod._DRILL_RECORD_KEY))
        assert rec["status"] == "rejected" and rec["ok"] is False
        assert rec["backup_key"] == key
        assert _has_open(fake.calls, ha_mod._DRILL_FAILED_KIND)
        # drop the corrupt archive and sweep a fresh one → success resolves
        mem_storage.delete(key)
        mem_storage.delete(key.replace("/dump.enc", "/manifest.json"))
        _default_drill_key(client, mem_storage, "team_x")
        ha_mod._LAST_DRILL_AT = 0.0
        r2 = client.post("/v1/internal/backups/drill-scheduled",
                         headers=INTERNAL_HEADERS)
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "drill_ok"
        assert _has_resolve(fake.calls, ha_mod._DRILL_FAILED_KIND)

    def test_rto_breach_opens_incident_but_returns_drill_ok(self, client,
                                                            dr_env,
                                                            mem_storage,
                                                            monkeypatch):
        """Restore succeeded but slower than the committed ≤15-min RTO:
        HTTP 200 with within_rto=false + a rto_breach record + the incident
        (restore speed is a target, not an accident)."""
        monkeypatch.setattr(ha_mod, "_DRILL_RTO_S", 0.0)  # any duration breaches
        fake = _FakeAlerts()
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: fake)
        _seed_team("team_x", nodes=1)
        _default_drill_key(client, mem_storage)
        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post("/v1/internal/backups/drill-scheduled",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "drill_ok"
        assert body["within_rto"] is False
        assert body["record"]["status"] == "rto_breach"
        assert _has_open(fake.calls, ha_mod._DRILL_FAILED_KIND)
        assert not _has_resolve(fake.calls, ha_mod._DRILL_FAILED_KIND)

    def test_respects_shared_cooldown(self, client, dr_env, mem_storage):
        import time as _time
        _seed_team("team_x", nodes=1)
        _default_drill_key(client, mem_storage)
        ha_mod._LAST_DRILL_AT = _time.time()  # simulate a drill < 1h ago
        r = client.post("/v1/internal/backups/drill-scheduled",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 429
        assert "cooldown" in r.json()["detail"]

    def test_status_surfaces_last_drill(self, client, dr_env, mem_storage,
                                        monkeypatch):
        _seed_team("team_x", nodes=1)
        _default_drill_key(client, mem_storage)
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: _FakeAlerts())
        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post("/v1/internal/backups/drill-scheduled",
                        headers=INTERNAL_HEADERS)
        assert r.json()["status"] == "drill_ok"
        s = client.get("/v1/internal/backups/status", headers=INTERNAL_HEADERS)
        assert s.status_code == 200
        ld = s.json()["last_drill"]
        assert ld is not None and ld["status"] == "ok"
        assert ld["duration_s"] is not None

    def test_manual_drill_records_measured_time(self, client, dr_env,
                                                mem_storage):
        """The manual endpoint rides the same record — pass/fail + measured
        time is written for every drill, not just the scheduled one."""
        _seed_team("team_x", nodes=2)
        key = _default_drill_key(client, mem_storage)
        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"team_id": "team_x", "backup_key": key},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "drill_ok"
        assert isinstance(body["duration_s"], (int, float))
        assert body["within_rto"] is True
        assert body["record"]["status"] == "ok"
        assert body["record"]["run"] == "manual"
        assert body["record"]["backup_key"] == key
        rec = json.loads(mem_storage.download(ha_mod._DRILL_RECORD_KEY))
        assert rec["run"] == "manual" and rec["status"] == "ok"


class TestBackupsSurfaceUnits:
    """#2313 listing/incident surface units (no client needed)."""

    def test_manifest_graph_buckets_by_key_shape_and_override(self):
        _m = ha_mod._manifest_graph
        # per-graph manifest carries its graph_id
        out = _m({"backup_id": "team_x/g_c1/20260101T000000Z_x", "graph_id": "g_c1"})
        assert out["graph_id"] == "g_c1" and out["kind"] == "custom"
        # nested default segment → default
        out = _m({"backup_id": "team_x/default/20260101T000000Z_x"})
        assert out["graph_id"] == "default" and out["kind"] == "default"
        # legacy flat → default by key shape (Q6 fallback)
        out = _m({"backup_id": "team_x/20260101T000000Z_x"})
        assert out["graph_id"] == "default"
        # override wins (Q6 reverse lookup found a custom graph)
        out = _m({"backup_id": "team_x/20260101T000000Z_x"}, override_gid="g_c1")
        assert out["graph_id"] == "g_c1" and out["kind"] == "custom"

    def test_legacy_graph_overrides_reverse_lookup(self):
        rows = [
            {"graph_id": "g_c1", "kind": "custom",
             "namespace": "team_team_x_g_c1", "graph_name": "team_team_x_g_c1"},
        ]
        # the caller pre-filters to legacy (2-segment backup_id, no graph_id)
        over = ha_mod._legacy_bucket_map(rows, [
            # legacy flat whose graph_name names the custom namespace
            {"backup_id": "team_x/20260101T000000Z_x",
             "graph_name": "team_team_x_g_c1"},
            # legacy flat naming the default (teams.graph_name) → no override
            {"backup_id": "team_x/20260102T000000Z_x",
             "graph_name": "team_team_x"},
        ])
        assert over == {"team_x/20260101T000000Z_x": "g_c1"}

    def test_legacy_overrides_from_index(self):
        """#2370: GET /backups derives legacy-flat graph identity from the
        sweep-written classification index — no control-plane query. A legacy
        flat the index resolves to a custom graph overrides to that graph;
        default/unresolvable entries keep the default bucket."""
        _ov = ha_mod._legacy_overrides_from_index
        listed = [
            {"backup_id": "team_x/20260101T000000Z_x"},          # legacy flat
            {"backup_id": "team_x/20260102T000000Z_x"},          # legacy flat
            {"backup_id": "team_x/g_c1/20260103T000000Z_x",
             "graph_id": "g_c1"},                                # nested
        ]
        index = {
            "team_x/20260101T000000Z_x": {"graph_name": "team_team_x_g_c1",
                                          "graph_id": "g_c1"},
            "team_x/20260102T000000Z_x": {"graph_name": "team_team_x",
                                          "graph_id": "default"},
        }
        assert _ov(listed, index) == {"team_x/20260101T000000Z_x": "g_c1"}
        # empty/missing index → no overrides (CP fallback path, no query)
        assert _ov(listed, {}) == {}

    def test_incident_subject_mapping(self):
        _sub = ha_mod._incident_subject
        assert _sub({"kind": "STALE", "team_id": "team_x"}) == "team_x"
        # custom-graph incidents carry graph_id → "{team}:{gid}"
        assert _sub({"team_id": "team_x", "graph_id": "g_c1"}) == "team_x:g_c1"
        # default-graph incidents stay team-level (back-compat alert keys)
        assert _sub({"team_id": "team_x", "graph_id": "default"}) == "team_x"
        # team-level kinds have no graph_id
        assert _sub({"kind": "NO_ELIGIBLE_TEAMS", "team_id": ""}) == ""
