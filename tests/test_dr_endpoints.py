"""Integration tests for the #596 internal DR endpoints (sweep / status /
heartbeat / simulate / re-baseline / drill)."""

from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from typing import ClassVar
from uuid import uuid4

import pytest
import redis
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
    graph (select_graph(f"org_{org_id}")) — NON-test-prefixed, so the
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
                if _g.startswith("org_") and not _g.startswith("org_test_"):
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


def _reset_graph(db, graph_name: str) -> None:
    """DETACH-DELETE every node in ``graph_name`` before seeding it (#3745).

    These tests assert on RAW node counts (``MATCH (n) RETURN count(n)`` —
    drill's ``== 2``, the sweep manifests' ``node_count``), which counts
    whatever the graph holds, not just the seed. A graph can already carry
    the projection's INTERNAL ``:Meta {key:'point_fts_v2'}`` bookkeeping
    marker: ``FalkorProjection._ensure_indexes`` MERGEs it whenever a
    projection is opened ON that graph, and an SDK that opens its projection
    on this graph (``_make_sdk(graph_name=...)`` — the sweep / re-baseline /
    acl-reconcile seams) writes it into the CURRENT ``TORTOISE_DB_PATH``.
    Because ``patched_tortoise_sdk`` re-pins that path per test, work
    deferred past a test's teardown lands in the NEXT test's temp DB — so
    the seed accumulated one bookkeeping node and the counts read 3 == 2 /
    2 == 1.

    Seeding from an EMPTY graph is the same contract ``_clean_team_graphs``
    already gives the server lane (it drops the raw ``org_*`` graphs before
    each test); this makes the embedded lane mirror it instead of letting
    the count depend on what a previous test left behind.
    """
    db.select_graph(graph_name).query("MATCH (n) DETACH DELETE n")


def _held_proj_db():
    """A data-plane `db` handle whose SDK is HELD for the session.

    `ha_mod._make_sdk(namespace=None)` returns a FRESH SDK per call; capturing
    only its `.db` lets the SDK be collected (close-on-GC) and the handle go
    dead mid-test — the same hazard `_SEED_SDKS` documents for seeds. Hold it.
    """
    sdk = ha_mod._make_sdk(namespace=None)
    _SEED_SDKS.append(sdk)
    return sdk._get_proj().db


def _seed_team(org_id: str = "team_x", nodes: int = 2) -> None:
    # The path arg is IGNORED under the client fixture's patched __init__
    # (all current callers use client); the SDK binds to the per-test temp DB.
    sdk = TortoiseSDK(namespace="registry")
    _SEED_SDKS.append(sdk)
    reg = sdk._get_registry()
    reg.query("MATCH (t:Team {id:$id}) DELETE t", params={"id": org_id})
    reg.query("CREATE (t:Team {id:$id, tier:'pro'})", params={"id": org_id})
    _reset_graph(sdk._get_proj().db, f"org_{org_id}")
    g = sdk._get_proj().db.select_graph(f"org_{org_id}")
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
            "graph_failures": [{"org_id": "team_x", "graph_id": "g_a",
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

    def test_status_surfaces_the_dialect_when_only_noop_runs_have_happened(
            self, client, dr_env, mem_storage):
        """#2823 (code-review cycle 4): a deployment whose only runs were no-ops
        has a dialect in ops/state.json but no `last_sweep_at`. The lane must
        still be readable — that IS the deployment an operator is diagnosing
        (a registry-dialect source on a Supabase deployment is exactly what
        `_refuse_wrong_dialect` deliberately does not catch) — and the block
        must NOT fabricate outcome fields the driver keys off."""
        mem_storage.upload("ops/state.json", json.dumps({
            "last_team_count": 0,
            "updated_at": "2026-09-11T04:00:00+00:00",
            "last_run_source": "registry",
        }).encode())
        r = client.get("/v1/internal/backups/status", headers=INTERNAL_HEADERS)
        assert r.status_code == 200
        ls = r.json()["last_sweep"]
        assert ls == {"last_run_source": "registry"}
        assert "last_sweep_at" not in ls
        assert "graph_totals" not in ls

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
    def test_sweep_resolves_open_guard_kinds_a_clear_run_did_not_re_emit(
            self, client, dr_env, mem_storage, monkeypatch):
        """#3030 wiring + review P2: a conclusive run closes the guard incidents it
        did NOT re-emit — but only those that are actually OPEN, and it reports what
        it closed. A subject that is not open is never resolved."""
        _seed_team("team_x", nodes=2)
        fake = _FakeAlerts(open_subjects={
            "ENUM_DELTA": {"_"},
            "P0_GUARD_FAIL": {"team_x", "team_x:gone"},
            "SWEEP_NO_COVERAGE": {"_"},
        })
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: fake)

        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "backed_up"
        assert ("resolve", "ENUM_DELTA", "") in fake.calls
        assert ("resolve", "P0_GUARD_FAIL", "team_x") in fake.calls
        # Not open → no resolve call. (This line would hold even without the
        # open-set check — no candidate is generated for `team_x:gone`; the
        # open-set check itself is pinned by SWEEP_NO_COVERAGE / NO_ELIGIBLE_TEAMS
        # never appearing in `incidents_resolved` below.)
        assert ("resolve", "P0_GUARD_FAIL", "team_x:gone") not in fake.calls
        assert ("resolve", "SWEEP_NO_COVERAGE", "") not in fake.calls
        assert ("open_subjects", "ENUM_DELTA", "") in fake.calls
        assert sorted(body["incidents_resolved"]) == ["ENUM_DELTA", "P0_GUARD_FAIL/team_x"]

    def test_sweep_survives_a_resolve_failure(self, client, dr_env, mem_storage,
                                              monkeypatch):
        """A failing close must never fail the sweep request (the incident simply
        stays open for the next run) nor lose the other resolutions — and it must
        be REPORTED as unresolved rather than silently vanishing (cycle-3)."""
        _seed_team("team_x", nodes=2)
        fake = _FakeAlerts(
            open_subjects={"ENUM_DELTA": {"_"}, "P0_GUARD_FAIL": {"team_x"}},
            fail_resolve_kinds={"ENUM_DELTA"},
        )
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: fake)

        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        assert r.json()["incidents_resolved"] == ["P0_GUARD_FAIL/team_x"]
        assert r.json()["incidents_unresolved"] == ["ENUM_DELTA"]

    def test_sweep_resolves_a_platform_incident_filed_under_the_global_spelling(
            self, client, dr_env, mem_storage, monkeypatch):
        """Cycle-3 review P2: the platform subject has two spellings in the store
        (`_` and a literal `global`), so a matcher that accepts `global` MUST also
        resolve `global` — passing "" would read `_.json` and clear nothing, which
        is what the first version of this branch did."""
        _seed_team("team_x", nodes=2)
        fake = _FakeAlerts(open_subjects={"ENUM_DELTA": {"global"}})
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: fake)

        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        assert ("resolve", "ENUM_DELTA", "global") in fake.calls
        assert r.json()["incidents_resolved"] == ["ENUM_DELTA/global"]

    def test_sweep_reports_a_listing_outage_instead_of_reading_clean(
            self, client, dr_env, mem_storage, monkeypatch):
        """Final-cycle review P2: `open_subjects` fails safe (empty set), which made
        an R2 LIST outage indistinguishable from "nothing was open" — the endpoint
        now lists strictly and reports the candidates it could not verify."""
        _seed_team("team_x", nodes=2)
        fake = _FakeAlerts(open_subjects={"ENUM_DELTA": {"_"}},
                           fail_listing_kinds={"ENUM_DELTA"})
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: fake)

        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        assert r.json().get("incidents_unresolved") == ["ENUM_DELTA"]

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
        assert manifest["graph_name"] == "org_team_x"
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

        cp = FakeControlPlane().seed("organizations", [
            {"id": "team_s1", "graph_name": "org_team_s1",
             "tier": "pro", "backup_enabled": True},
            {"id": "team_s2", "graph_name": "org_team_s2",
             "tier": "pro", "backup_enabled": True},
        ])
        monkeypatch.setattr("tortoise.supabase_control.is_supabase_enabled",
                            lambda: True)
        monkeypatch.setattr("tortoise.supabase_control.get_control_plane",
                            lambda: cp)
        # Seed the DATA plane (FalkorDB stays the graph store in both lanes).
        db = _held_proj_db()
        for tid in ("team_s1", "team_s2"):
            _reset_graph(db, f"org_{tid}")
            g = db.select_graph(f"org_{tid}")
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
        # #2823: the per-run provenance field rides /status too. After a REAL
        # sweep the two agree by definition (a no-op run is where they differ —
        # pinned in test_backup_sweep).
        assert r.json()["last_sweep"]["last_run_source"] == "registry"

    def test_sweep_empty_supabase_lane_is_a_quiet_confirmed_empty(
            self, client, dr_env, mem_storage, monkeypatch):
        """#2823 boundary: the wrong-dialect refusal is ONE-DIRECTIONAL. A
        correct-lane Supabase read that genuinely finds no teams is a benign
        `no_teams` — NOT `enum_failed` — and still reports its dialect."""
        from tests.fake_control_plane import FakeControlPlane

        cp = FakeControlPlane().seed("organizations", [])
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

    TEAM: ClassVar[dict] = {"id": "team_s1", "graph_name": "org_team_s1",
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

        # #3745: a PER-TEST graph name. These tests assert on the graph's RAW
        # node count (`node_count`) and the graph projection MERGEs its own
        # internal `:Meta {key:'point_fts_v2'}` bookkeeping marker into
        # whatever `TORTOISE_DB_PATH` is current whenever a projection is
        # opened ON that graph — so work deferred past a sibling test's
        # teardown lands in the NEXT test's temp DB under the SAME graph name
        # and inflates the count (2 == 1). A unique name per test makes that
        # structurally impossible: the stale writer's target no longer exists
        # in this test's DB, so the seed is the only content (the same
        # per-test isolation `_clean_team_graphs` gives the server lane).
        monkeypatch.setitem(TestSupabaseLaneSeam.TEAM, "graph_name",
                            f"org_team_s1_{uuid4().hex[:8]}")
        cp = FakeControlPlane().seed("organizations", [dict(TestSupabaseLaneSeam.TEAM)])
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
        db = ha._make_sdk(namespace=None)._get_proj().db
        _reset_graph(db, TestSupabaseLaneSeam.TEAM["graph_name"])
        g = db.select_graph(TestSupabaseLaneSeam.TEAM["graph_name"])
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
            lambda graph_id, org_id: {"username": "u"})
        cp = self._fortify_supabase_lane(monkeypatch)
        r = client.post("/v1/internal/backups/acl-reconcile",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "reconciled"
        assert body["organizations"] == 1, body
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
            "id": "g_old", "org_id": "team_s1", "name": "g_old",
            "kind": "custom", "status": "deleted",
            "namespace": "org_team_s1_g_old", "deleted_at": expired,
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
                        headers=INTERNAL_HEADERS, json={"org_id": "team_s1"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "rebaselined", body
        assert body["node_count"] == 1, body
        assert cp.query_count > 0

    def test_drill_resolves_the_active_graph_via_the_supabase_seam(
            self, client, dr_env, mem_storage, monkeypatch):
        """The drill resolves its target graph through the seam AFTER the
        org_id/backup_key validation — a body without a real archive key
        short-circuits before the seam and proves nothing."""
        cp = self._fortify_supabase_lane(monkeypatch)
        self._seed_data_plane()
        key = _default_drill_key(client, mem_storage, org_id="team_s1")
        monkeypatch.setattr(ha_mod, "_LAST_DRILL_AT", 0.0)
        r = client.post("/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
                        json={"org_id": "team_s1", "backup_key": key})
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "drill_ok", r.text
        assert cp.query_count > 0

    def test_drill_scheduled_resolves_via_the_supabase_seam(
            self, client, dr_env, mem_storage, monkeypatch):
        cp = self._fortify_supabase_lane(monkeypatch)
        self._seed_data_plane()
        assert client.post("/v1/internal/backups/sweep",
                           headers=INTERNAL_HEADERS).json()["status"] == "backed_up"
        monkeypatch.setattr(ha_mod, "_LAST_DRILL_AT", 0.0)
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
        cp.seed("graphs", [{"id": "g_custom", "org_id": "team_s1",
                            "name": "g_custom", "kind": "custom",
                            "status": "active",
                            "namespace": "team_team_s1_g_custom"}])
        backup_id = "team_s1/20260101T000000Z_ab12"
        mem_storage.upload(
            f"backups/{backup_id}/manifest.json",
            json.dumps({
                "backup_id": backup_id, "org_id": "team_s1",
                "graph_name": "team_team_s1_g_custom",
                "created_at": "2026-01-01T00:00:00+00:00",
                "node_count": 1, "edge_count": 0, "sha256": "0" * 64,
            }).encode())
        ha_mod.app.dependency_overrides[
            ha_mod.get_current_org_session_ungated
        ] = lambda: {"org_id": "team_s1"}
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
        """#2823: the dialect rides the 202 too — the lock-held shape is where an
        unresolved dialect would surface as `unknown` to every consumer of the
        sweep result. Asserted on the real endpoint shape (no fabricated
        body)."""
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
        assert watcher.watcher._orgs() == ["team_s1"]

class TestDrRebaseline:
    def test_rebaseline_requires_team(self, client, dr_env, mem_storage):
        r = client.post("/v1/internal/backups/re-baseline", headers=INTERNAL_HEADERS, json={})
        assert r.status_code == 400

    def test_rebaseline_rejects_malformed_ids(self, client, dr_env, mem_storage):
        """#2377: org_id/graph_id flow into R2 state keys — charset-gate the
        shape before any write (defense in depth; rows are server-generated
        today, but this endpoint must never mint keys off an attacker-shaped
        id)."""
        bad = [
            {"org_id": "team_x", "graph_id": "../esc"},
            {"org_id": "team_x", "graph_id": "g_a/b"},
            {"org_id": "../team", "graph_id": "default"},
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
            json={"org_id": "team_x"},
        )
        assert r.status_code == 200
        assert r.json()["node_count"] == 3
        state = json.loads(mem_storage.download("ops/teams/team_x/state.json"))
        assert state["node_count"] == 3

    def test_rebaseline_survives_a_resolve_failure(self, client, dr_env, mem_storage,
                                                    monkeypatch):
        """Cycle-2 review P2: `resolve_incident` RAISES on a failed close, and the
        state write has already succeeded by then — a raise out of the endpoint
        would 500 a request whose effect was persisted, inviting the operator to
        retry a write that already happened."""
        _seed_team("team_x", nodes=3)
        fake = _FakeAlerts(open_subjects={"DATA_LOSS_CANDIDATE": {"team_x"}},
                           fail_resolve_kinds={"DATA_LOSS_CANDIDATE", "SIZE_GUARD_ABORT"})
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: fake)

        r = client.post(
            "/v1/internal/backups/re-baseline", headers=INTERNAL_HEADERS,
            json={"org_id": "team_x"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "rebaselined"
        assert json.loads(mem_storage.download("ops/teams/team_x/state.json"))["node_count"] == 3

    def test_rebaseline_excludes_projection_bookkeeping_marker(
            self, client, dr_env, mem_storage):
        """#4233 — re-baseline's node_count is ``dump_graph``'s node set, not
        a raw ``MATCH (n)``.

        A projection opened ON the org graph (the export seam does this via
        ``_make_sdk(graph_name=...)``) MERGEs its internal
        ``:Meta {key:'point_fts_v2'}`` index-guard marker into that graph
        (#1541). Counting it made a 3-point graph re-baseline to 4 — the flake
        that red'd the required check on unrelated PRs.

        RED (mutation): count ``MATCH (n) RETURN count(n)`` again — the
        assertion below reads 4 == 3 (verified).
        """
        _seed_team("team_x", nodes=3)
        # The projection's index guard (`FalkorProjection._ensure_indexes`,
        # #1541) MERGEs this marker into whatever graph it is opened on — the
        # sweep / export / re-baseline seams all open one. A test session
        # redirects an SDK's graph NAME (Epic #1647 D-1=A), so inject the
        # identical node directly into the raw org graph the DR seams address
        # by name — the marker node is what matters, not how it got there.
        sdk = TortoiseSDK(namespace="registry")
        _SEED_SDKS.append(sdk)
        db = sdk._get_proj().db
        db.select_graph("org_team_x").query(
            "MERGE (m:Meta {key:'point_fts_v2'}) SET m.v = true")
        assert int(db.select_graph("org_team_x").query(
            "MATCH (m:Meta {key:'point_fts_v2'}) RETURN count(m)"
        ).result_set[0][0]) == 1, "probe did not write the bookkeeping marker"
        assert int(db.select_graph("org_team_x").query(
            "MATCH (n) RETURN count(n)").result_set[0][0]) == 4, (
            "the marker must be present for this guard to be non-vacuous"
        )

        mem_storage.upload(
            "ops/teams/team_x/state.json",
            json.dumps({"node_count": 10}).encode(),
        )
        r = client.post(
            "/v1/internal/backups/re-baseline", headers=INTERNAL_HEADERS,
            json={"org_id": "team_x"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["node_count"] == 3, r.text
        state = json.loads(mem_storage.download("ops/teams/team_x/state.json"))
        assert state["node_count"] == 3

    def test_count_data_nodes_matches_the_dump_node_set(self, client):
        """#4233 — ``count_data_nodes`` and ``dump_graph`` count the SAME nodes.

        The count is re-expressed as a cap-immune server-side aggregate, so it
        must stay semantically identical to ``_is_export_skip_node`` at every
        boundary: each label-wide skip class, each key-scoped Meta marker, a
        Meta with a NON-skip key (DATA), and — the subtle one — a Meta with NO
        ``key`` (also DATA: Cypher three-valued logic would otherwise drop it
        from the aggregate while ``dump_graph`` keeps it).

        RED (mutation): drop ``n.key IS NOT NULL`` (or any skip class) from the
        aggregate — the parity assertion below fails (verified).
        """
        import tortoise.hosted_backup as hb
        from tortoise.hosted_api import (
            _EXPORT_SKIP_LABELS,
            _EXPORT_SKIP_META_KEYS,
            _is_export_skip_node,
        )

        db = _held_proj_db()
        g = db.select_graph("org_settle_source")
        g.query("MATCH (n) DETACH DELETE n")
        g.query("CREATE (p:Point {id:'data-1'})")           # content
        g.query("CREATE (m:Meta {key:'calibration_milestone'})")  # DATA
        g.query("CREATE (m:Meta {v:true})")                 # no key — DATA
        g.query("CREATE (m:Meta)")                          # no key — DATA
        for label in sorted(_EXPORT_SKIP_LABELS):            # skipped
            g.query(f"CREATE (n:{label} {{x:1}})")
        for key in sorted(_EXPORT_SKIP_META_KEYS):           # skipped
            g.query("CREATE (m:Meta {key:$k})", params={"k": key})

        expected = sum(
            1 for labels, props in g.query(
                "MATCH (n) RETURN labels(n), properties(n)").result_set
            if not _is_export_skip_node(
                [str(l) for l in (labels or [])],  # noqa: E741
                dict(props or {}))
        )
        assert hb.count_data_nodes(db, "org_settle_source") == expected
        assert expected == 4, expected  # 1 Point + 3 content Meta nodes

    def test_count_data_nodes_query_is_a_single_row_aggregate(self, client):
        """#4233 — cap immunity, pinned STRUCTURALLY (no server-global mutation).

        ``RESULTSET_SIZE`` truncates the ROWS a read returns; an aggregate
        always returns exactly ONE row regardless of graph size. So asserting
        the count query's row shape pins cap-immunity WITHOUT lowering a
        server-global setting on a shared test server.

        RED (mutation): change ``_COUNT_DATA_NODES_QUERY`` to a non-aggregate
        ``MATCH (n) RETURN …`` — it returns N rows and the row-count assertion
        fails (verified).
        """
        import tortoise.hosted_backup as hb
        from tortoise.hosted_api import (
            _EXPORT_SKIP_LABELS,
            _EXPORT_SKIP_META_KEYS,
        )

        db = _held_proj_db()
        g = db.select_graph("org_settle_source")
        g.query("MATCH (n) DETACH DELETE n")
        for i in range(6):
            g.query("CREATE (p:Point {id:$id})", params={"id": f"p-{i}"})
        g.query("CREATE (m:Meta {key:'point_fts_v2'})")  # content-neutral marker

        params = {
            "skip_labels": sorted(str(l) for l in _EXPORT_SKIP_LABELS),  # noqa: E741
            "meta_keys": sorted(str(k) for k in _EXPORT_SKIP_META_KEYS),
        }
        rows = g.query(hb._COUNT_DATA_NODES_QUERY, params=params).result_set
        assert len(rows) == 1, rows  # an aggregate — immune to the row cap
        assert int(rows[0][0]) == 6
        # The same graph read WITHOUT an aggregate returns many rows — the
        # shape RESULTSET_SIZE would truncate, which this query must never be.
        assert len(g.query(
            "MATCH (n) RETURN labels(n), properties(n)").result_set) == 7
        assert hb.count_data_nodes(db, "org_settle_source") == 6

class TestDrDrill:
    def test_drill_requires_params(self, client, dr_env, mem_storage):
        r = client.post("/v1/internal/backups/drill", headers=INTERNAL_HEADERS, json={})
        assert r.status_code == 400

    def test_drill_restores_to_scratch(self, client, dr_env, mem_storage,
                                       monkeypatch):
        _seed_team("team_x", nodes=2)
        # Produce a real archive for team_x via the sweep pipeline.
        r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        assert r.json()["status"] == "backed_up"
        manifest = [  # noqa: RUF015
            k for k in mem_storage.list("backups/team_x/") if k.endswith("manifest.json")
        ][0]
        backup_key = manifest.replace("/manifest.json", "/dump.enc")

        monkeypatch.setattr(ha_mod, "_LAST_DRILL_AT", 0.0)  # clear cooldown
        r2 = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"org_id": "team_x", "backup_key": backup_key},
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["status"] == "drill_ok"
        assert body["target_graph"].startswith("_drill_")
        # Live graph untouched, scratch cleaned. The count is taken over
        # `:Point` — the label `_seed_team` writes, and the convention the
        # sibling backup/restore tests use (tests/test_backup_e2e.py:70) — so
        # the projection's internal `:Meta {key:'point_fts_v2'}` bookkeeping
        # node cannot be mistaken for restored data (#3745: it made this read
        # 3 == 2 whenever a projection had been opened on the graph).
        sdk = TortoiseSDK("/tmp/x.db", namespace="registry")
        live = sdk._get_proj().db.select_graph("org_team_x")
        assert live.query("MATCH (n:Point) RETURN count(n)").result_set[0][0] == 2
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
        monkeypatch.setattr(ha_mod, "_LAST_DRILL_AT", 0.0)
        r2 = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"org_id": "team_x", "backup_key": backup_key},
        )
        assert r2.status_code == 200, r2.text
        assert r2.json()["status"] == "drill_ok"
        monkeypatch.undo()

    def test_drill_cooldown(self, client, dr_env, mem_storage, monkeypatch):
        _seed_team("team_x", nodes=1)
        client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
        manifest = [  # noqa: RUF015
            k for k in mem_storage.list("backups/team_x/") if k.endswith("manifest.json")
        ][0]
        backup_key = manifest.replace("/manifest.json", "/dump.enc")
        monkeypatch.setattr(ha_mod, "_LAST_DRILL_AT", 0.0)
        r1 = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"org_id": "team_x", "backup_key": backup_key},
        )
        assert r1.status_code == 200
        r2 = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"org_id": "team_x", "backup_key": backup_key},
        )
        assert r2.status_code == 429


class TestDrRebaselinePerGraph:
    """#2313 Task 5: re-baseline resolves the ACTIVE-graph seam (per-graph
    state; tombstone guard)."""

    def _seed_custom(self, org_id="team_x", gid="g_c1", ns="team_team_x_g_c1"):
        sdk = TortoiseSDK(namespace="registry")
        _SEED_SDKS.append(sdk)
        reg = sdk._get_registry()
        reg.query(
            "CREATE (g:Graph {id:$gid, org_id:$tid, kind:'custom', "
            "namespace:$ns, status:'active'})",
            params={"gid": gid, "tid": org_id, "ns": ns},
        )
        _reset_graph(sdk._get_proj().db, ns)
        g = sdk._get_proj().db.select_graph(ns)
        g.query("CREATE (p:Point {id:'c-0', content:'c', pointKind:'claim'})")

    def test_rebaseline_default_writes_per_graph_and_mirror(self, client, dr_env, mem_storage):
        _seed_team("team_x", nodes=3)
        r = client.post(
            "/v1/internal/backups/re-baseline", headers=INTERNAL_HEADERS,
            json={"org_id": "team_x"},
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
            json={"org_id": "team_x", "graph_id": "g_c1"},
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
            "CREATE (g:Graph {id:'g_dead', org_id:'team_x', kind:'custom', "
            "namespace:'team_team_x_g_dead', status:'deleted'})")
        r = client.post(
            "/v1/internal/backups/re-baseline", headers=INTERNAL_HEADERS,
            json={"org_id": "team_x", "graph_id": "g_dead"},
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

    def test_drill_custom_graph_restores_to_scratch(self, client, dr_env,
                                                    mem_storage, monkeypatch):
        _seed_team("team_x", nodes=2)
        sdk = TortoiseSDK(namespace="registry")
        _SEED_SDKS.append(sdk)
        reg = sdk._get_registry()
        reg.query(
            "CREATE (g:Graph {id:'g_c1', org_id:'team_x', kind:'custom', "
            "namespace:'team_team_x_g_c1', status:'active'})")
        _reset_graph(sdk._get_proj().db, "team_team_x_g_c1")
        g = sdk._get_proj().db.select_graph("team_team_x_g_c1")
        g.query("CREATE (p:Point {id:'c-0', content:'c', pointKind:'claim'})")
        key = self._sweep_and_pick(client, mem_storage, "g_c1")
        monkeypatch.setattr(ha_mod, "_LAST_DRILL_AT", 0.0)
        r = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"org_id": "team_x", "backup_key": key},
        )
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "drill_ok"
        assert r.json()["target_graph"].startswith("_drill_")

    def test_drill_refuses_tombstoned_after_backup(self, client, dr_env,
                                                   mem_storage, monkeypatch):
        """Back the custom graph up while ACTIVE, then tombstone it (a graph
        deleted AFTER its last backup) — the drill's resolution must refuse:
        quarantined archives are never a drill/restore source (#2304)."""
        _seed_team("team_x", nodes=1)
        sdk = TortoiseSDK(namespace="registry")
        _SEED_SDKS.append(sdk)
        reg = sdk._get_registry()
        reg.query(
            "CREATE (g:Graph {id:'g_x', org_id:'team_x', kind:'custom', "
            "namespace:'team_team_x_g_x', status:'active'})")
        _reset_graph(sdk._get_proj().db, "team_team_x_g_x")
        g = sdk._get_proj().db.select_graph("team_team_x_g_x")
        g.query("CREATE (p:Point {id:'x-0', content:'x', pointKind:'claim'})")
        key = self._sweep_and_pick(client, mem_storage, "g_x")
        reg.query(
            "MATCH (g:Graph {id:'g_x'}) SET g.status = 'deleted'")
        monkeypatch.setattr(ha_mod, "_LAST_DRILL_AT", 0.0)
        r = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"org_id": "team_x", "backup_key": key},
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
                "org_id:$tid, created_by:$by, created_at:$now, "
                "revoked_at:$rev, expires_at:$exp, created_via:$via})",
                params={
                    "id": e["id"],
                    "prefix": e.get("prefix", "tt_test"),
                    "tid": e.get("org_id", "team-reconcile"),
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
            "CREATE (g:Graph {id:'g_a', org_id:'team_x', kind:'custom', "
            "namespace:'team_team_x_g_a', status:'active'})")
        reg.query(
            "CREATE (g:Graph {id:'g_dead', org_id:'team_x', kind:'custom', "
            "namespace:'team_team_x_g_dead', status:'deleted'})")
        calls: list = []
        monkeypatch.setattr(
            "tortoise.acl_graph_users.create_acl_user",
            lambda graph_id, org_id: calls.append((graph_id, org_id)) or {"username": "u"},
        )
        r = client.post(
            "/v1/internal/backups/acl-reconcile", headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "reconciled"
        org_res = body["results"]["team_x"]
        assert org_res["custom_graphs_ok"] == 1
        assert org_res["default_skipped"] == 1
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
    """Recording alert-store stub — captures open/resolve calls (no network).

    ``open_subjects`` models the #3030 review contract: the endpoint must LIST
    what is open (one call per kind) instead of issuing an R2 read per graph.
    Pass ``{"KIND": {"subject", ...}}`` to declare open incidents.
    """

    def __init__(self, open_subjects=None, fail_resolve_kinds=(),
                 fail_listing_kinds=()):
        self.calls: list = []
        self._open = dict(open_subjects or {})
        self._fail_resolve = set(fail_resolve_kinds)
        self.fail_listing_kinds = set(fail_listing_kinds)

    def open_incident(self, kind, org_id="", detail=None):
        self.calls.append(("open", kind, org_id, dict(detail or {})))
        return True

    def open_subjects(self, kind, *, strict=False):
        self.calls.append(("open_subjects", kind, ""))
        if strict and kind in getattr(self, "fail_listing_kinds", set()):
            raise RuntimeError("R2 listing outage")
        return set(self._open.get(kind, set()))

    def resolve_incident(self, kind, org_id=""):
        self.calls.append(("resolve", kind, org_id))
        if kind in self._fail_resolve:
            raise RuntimeError("alert store down")
        return True


def _backdate_archive(store, backup_key: str, created_at: str) -> None:
    """Backdate a stored archive's manifest created_at (sha256 untouched —
    restore verification still passes; selection order changes)."""
    mkey = backup_key.replace("/dump.enc", "/manifest.json")
    manifest = json.loads(store.download(mkey))
    manifest["created_at"] = created_at
    store.upload(mkey, json.dumps(manifest).encode())


def _default_drill_key(client, mem_storage, org_id="team_x") -> str:
    """Sweep the team once and return the NEWEST default-graph archive key
    (a sweep always adds one archive; retention keeps older ones)."""
    r = client.post("/v1/internal/backups/sweep", headers=INTERNAL_HEADERS)
    assert r.json()["status"] == "backed_up", r.text
    keys = [k for k in mem_storage.list(f"backups/{org_id}/default/")
            if k.endswith("dump.enc")]
    assert keys, f"no default archive for {org_id}"
    return sorted(keys)[-1]  # newest (lexicographic ts == chronological)


def _has_open(calls, kind, team="global") -> bool:
    return any(c[0] == "open" and c[1] == kind and c[2] == team for c in calls)


def _has_resolve(calls, kind, team="global") -> bool:
    return any(c[0] == "resolve" and c[1] == kind and c[2] == team for c in calls)


class TestDrDrillScheduled:
    """#2317 scheduled drill endpoint: oldest-eligible selection, records,
    incidents, cooldown, /status surfacing."""

    @pytest.fixture(autouse=True)
    def _isolated_drill_state(self, monkeypatch, mem_storage):
        """#2878: the drill cooldown is MODULE state (`ha_mod._LAST_DRILL_AT`)
        that both drill handlers overwrite on every accepted request, and the
        drill record is written to a fixed storage key.

        A bare ``ha_mod._LAST_DRILL_AT = …`` in a test body is never reverted,
        so a cooldown left by one test 429s the next drill in the same process
        — an order-dependent flake whose failing test moved run to run. Pin it
        through ``monkeypatch`` (auto-reverted) so every test starts cleared and
        the module ends where it started; drop any stale drill record so no test
        inherits the previous test's record."""
        monkeypatch.setattr(ha_mod, "_LAST_DRILL_AT", 0.0)
        mem_storage.delete(ha_mod._DRILL_RECORD_KEY)

    def test_requires_config(self, client, mem_storage):
        r = client.post("/v1/internal/backups/drill-scheduled",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 503  # fail-closed when the sweep is disabled

    def test_no_candidates_records_and_resolves(self, client, dr_env,
                                                mem_storage, monkeypatch):
        fake = _FakeAlerts()
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: fake)
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
            "CREATE (g:Graph {id:'g_dead', org_id:'team_x', kind:'custom', "
            "namespace:'team_team_x_g_dead', status:'active'})")
        _reset_graph(sdk._get_proj().db, "team_team_x_g_dead")
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
        # the 409 above already consumed the cooldown slot (the handler stamps
        # _LAST_DRILL_AT at ACCEPT, before the integrity check) — clear it for
        # the recovery drill. monkeypatch, so the reset cannot leak (#2878).
        monkeypatch.setattr(ha_mod, "_LAST_DRILL_AT", 0.0)
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
        r = client.post("/v1/internal/backups/drill-scheduled",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "drill_ok"
        assert body["within_rto"] is False
        assert body["record"]["status"] == "rto_breach"
        assert _has_open(fake.calls, ha_mod._DRILL_FAILED_KIND)
        assert not _has_resolve(fake.calls, ha_mod._DRILL_FAILED_KIND)

    def test_rto_breach_incident_names_the_copy_overrun(self, client, dr_env,
                                                        mem_storage,
                                                        monkeypatch):
        """#4233 — an RTO breach caused by an overrun copy NAMES it in the
        incident payload, not only in the drill record.

        The unattended scheduled drill alerts from the incident; a flag that
        never reaches it leaves the breach unattributed for the operator who
        only sees alerts.

        RED (mutation): drop the ``copy_read_bound_overrun`` entry from the
        RTO-breach incident payload — this fails (verified).
        """
        import tortoise.hosted_backup as hb

        monkeypatch.setenv("TORTOISE_RESTORE_SWAP_SETTLE_S", "2")
        monkeypatch.setattr(ha_mod, "_DRILL_RTO_S", 0.0)  # any duration breaches

        def copy_then_timeout(redis_client, src_name, dst_name):
            from falkordb import Graph
            hb.restore_graph(Graph(redis_client, dst_name),
                             hb.dump_graph(Graph(redis_client, src_name)))
            try:
                raise redis.exceptions.TimeoutError("Timeout reading from socket")
            except redis.exceptions.TimeoutError:
                raise ValueError("I/O operation on closed file.")  # noqa: B904

        monkeypatch.setattr(hb, "_issue_graph_copy", copy_then_timeout)
        fake = _FakeAlerts()
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: fake)
        _seed_team("team_x", nodes=1)
        _default_drill_key(client, mem_storage)
        r = client.post("/v1/internal/backups/drill-scheduled",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "drill_ok" and body["within_rto"] is False
        opened = [c for c in fake.calls
                  if c[0] == "open" and c[1] == ha_mod._DRILL_FAILED_KIND]
        assert opened, fake.calls
        assert opened[-1][3].get("copy_read_bound_overrun") is True, opened[-1]

    def test_respects_shared_cooldown(self, client, dr_env, mem_storage,
                                      monkeypatch):
        import time as _time
        _seed_team("team_x", nodes=1)
        _default_drill_key(client, mem_storage)
        # a drill accepted <1h ago — via monkeypatch so it is reverted after
        # the test (a bare module-global write here leaked the cooldown, #2878).
        monkeypatch.setattr(ha_mod, "_LAST_DRILL_AT", _time.time())
        r = client.post("/v1/internal/backups/drill-scheduled",
                        headers=INTERNAL_HEADERS)
        assert r.status_code == 429
        assert "cooldown" in r.json()["detail"]

    def test_status_surfaces_last_drill(self, client, dr_env, mem_storage,
                                        monkeypatch):
        _seed_team("team_x", nodes=1)
        _default_drill_key(client, mem_storage)
        monkeypatch.setattr(ha_mod, "_alert_store_from", lambda cfg: _FakeAlerts())
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
        r = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"org_id": "team_x", "backup_key": key},
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
        assert _sub({"kind": "STALE", "org_id": "team_x"}) == "team_x"
        # custom-graph incidents carry graph_id → "{team}:{gid}"
        assert _sub({"org_id": "team_x", "graph_id": "g_c1"}) == "team_x:g_c1"
        # default-graph incidents stay team-level (back-compat alert keys)
        assert _sub({"org_id": "team_x", "graph_id": "default"}) == "team_x"
        # team-level kinds have no graph_id
        assert _sub({"kind": "NO_ELIGIBLE_TEAMS", "org_id": ""}) == ""


class TestRestoreSwapReadBound:
    """#3813 — the restore swap's GRAPH.COPY is a long SERVER-SIDE operation
    (FalkorDB forks a child that encodes the whole graph; the caller stays
    blocked for the entire copy), so its read bound belongs to the restore and
    NOT to an ordinary request.

    Every assertion is on the OBSERVABLE the caller gets — HTTP status + detail,
    and which graphs do or do not exist afterwards — never on the bound
    constant.
    """

    #: Server-side delay injected into the copy. Must outlive the ordinary read
    #: bound AND the ordinary client's bounded retry (1 retry × bound + jitter),
    #: so the mutated (ordinary-bound) run cannot slip through on a retry.
    _SLOW_COPY_S = 4.0

    def test_swap_copy_outliving_the_ordinary_bound_completes(
            self, client, dr_env, mem_storage, monkeypatch):
        """AC1 — a copy whose server-side work outlives the ordinary request
        read bound still completes, and the live graph holds the RESTORED
        content.

        RED (mutation): drop ``kwargs["socket_timeout"]`` from
        ``hosted_backup._restore_copy_client`` so the copy runs on the ordinary
        client — the 4s hold then dies at the 1s ordinary bound and this fails
        with ``RestoreCopyTimeoutError`` at 1s instead of returning. The
        ordinary client's 1 retry cannot rescue it: 2 × 1s + jitter still < 4s.

        Legitimate form that stays green: the swap keeps its OWN explicit,
        generous, finite bound while #2850's ordinary bound stays at 1s for
        every other operation.
        """
        import tortoise.hosted_backup as hb

        # Ordinary requests keep #2850's fail-fast bound; the copy under test is
        # deliberately slower than it, while the swap's own bound is not. (60 is
        # the floor — the largest bound a *legal* ordinary configuration can
        # reach — so this is the tightest still-generous swap bound.)
        monkeypatch.setenv("TORTOISE_FALKORDB_SOCKET_TIMEOUT_S", "1")
        monkeypatch.setenv("TORTOISE_RESTORE_SWAP_TIMEOUT_S", "60")

        delay = self._SLOW_COPY_S
        calls: list[tuple[str, str]] = []

        def slow_copy(redis_client, src_name, dst_name):
            # Record the seam so the assertion below can prove the restore
            # actually routed its copy through here — a regression that reverts
            # the copy step to ``Graph.copy`` would otherwise leave this green.
            calls.append((src_name, dst_name))
            # The property under test is WHICH read bound governs this client,
            # not how the bytes move. A blocking server-side read on the SAME
            # client is the deterministic hold: the server does not answer
            # until it elapses, exactly like a GRAPH.COPY that outlives the
            # bound. Applied to the SWAP copy (whose source is the verified
            # temp graph) — the pre-restore copy is the same code path.
            #
            # The promotion itself is deliberately fork-free. A real GRAPH.COPY
            # forks a server child whose embedded-lane lifecycle is unreliable
            # (observed: the child hangs, wedging the server's module-fork slot
            # and turning every later copy into "could not fork" — the companion
            # defect to #3813, filed separately). Letting this guard depend on
            # that fork would make it a probe for the OTHER defect instead of
            # this one. The two timeout guards below inject at the same seam but
            # replace the copy with a mock that raises immediately: they cover
            # timeout CLASSIFICATION, not a real copy's read bound. This guard
            # likewise does not exercise a real long GRAPH.COPY — by design.
            if "_restore_" in src_name:
                redis_client.execute_command(
                    "BLPOP", "_restore_swap_bound_probe", str(float(delay)))
            from falkordb import Graph
            dump = hb.dump_graph(Graph(redis_client, src_name))
            hb.restore_graph(Graph(redis_client, dst_name), dump)

        monkeypatch.setattr(hb, "_issue_graph_copy", slow_copy)

        # A stale live graph plus a payload built from a seeded source graph,
        # then the swap driven directly so the LIVE graph's content is
        # observable afterwards (the drill endpoint deletes its scratch target
        # on success, which would hide it).
        db = _held_proj_db()
        source = db.select_graph("org_swap_source")
        source.query("MATCH (n) DETACH DELETE n")
        for i in range(2):
            source.query(
                "CREATE (p:Point {id:$id, content:$c, pointKind:'claim'})",
                params={"id": f"pt-{i}", "c": f"c{i}"},
            )
        payload = hb.dump_graph(source)
        target = db.select_graph("org_swap_target")
        target.query("MATCH (n) DETACH DELETE n")
        target.query(
            "CREATE (p:Point {id:'stale', content:'old', pointKind:'claim'})")

        hb._restore_into_temp_verify_swap(
            db, payload, live_name="org_swap_target")

        # The seam WAS reached, and the delayed (swap) branch taken: otherwise
        # "the bound is generous" would be satisfied vacuously by a regression
        # that reverts the copy step to ``Graph.copy`` (which never comes here).
        assert calls, "restore never reached the _issue_graph_copy seam"
        assert any("_restore_" in src for src, _ in calls), calls

        # The live graph now holds the RESTORED content (not 200-shaped
        # nothing).
        rows = db.select_graph("org_swap_target").query(
            "MATCH (n:Point) RETURN n.id ORDER BY n.id").result_set
        assert [r[0] for r in rows] == ["pt-0", "pt-1"]

    def test_masked_read_timeout_is_reported_as_a_timeout(
            self, client, dr_env, mem_storage, monkeypatch):
        """AC2 — a read timeout during GRAPH.COPY is reported as a TIMEOUT: the
        503 detail says so, names the verified temp graph as intact, and says
        the destination was NOT restored. It is never reported as a dead
        connection.

        The injected error is the EXACT shape the embedded lane produced:
        redis-py closes the socket on a read timeout and then seeks in the
        now-closed parser buffer, so ``redis.exceptions.TimeoutError: Timeout
        reading from socket`` arrives chained behind ``ValueError: I/O
        operation on closed file``.

        RED (mutation): make ``_is_client_read_timeout`` blind to the masked
        form (or drop the ``except RestoreCopyTimeoutError`` branch) — the
        detail then reads "Restore swap failed …: I/O operation on closed
        file" and this fails on the timeout assertions.
        """
        import tortoise.hosted_backup as hb

        # #4233: the settle poll now confirms a reported timeout against the
        # copy's OUTCOME. Shrink it so this classification guard does not wait
        # the full read bound; the destination never materialises here, so the
        # timeout verdict is unchanged.
        monkeypatch.setenv("TORTOISE_RESTORE_SWAP_SETTLE_S", "0.1")

        _seed_team("team_x", nodes=2)
        key = _default_drill_key(client, mem_storage)

        def masked_timeout(redis_client, src_name, dst_name):
            try:
                raise redis.exceptions.TimeoutError(
                    "Timeout reading from socket")
            except redis.exceptions.TimeoutError:
                raise ValueError("I/O operation on closed file.")  # noqa: B904

        monkeypatch.setattr(hb, "_issue_graph_copy", masked_timeout)
        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post("/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
                        json={"org_id": "team_x", "backup_key": key})
        assert r.status_code == 503, r.text
        detail = r.json()["detail"]
        assert "timed out" in detail.lower(), detail
        assert "not restored" in detail.lower(), detail
        assert "i/o operation on closed file" not in detail.lower(), detail
        assert "restore swap failed" not in detail.lower(), detail
        # The verified temp graph is intact AND identifiable, and it is NOT the
        # live target (a timer-out must never be reported as a swap).
        m = re.search(r"(\w+_restore_\w+)", detail)
        assert m, detail
        temp_name = m.group(1)
        target_name = temp_name.split("_restore_")[0]
        sdk = TortoiseSDK("/tmp/x.db", namespace="registry")
        graphs = sdk._get_proj().db.list_graphs()
        assert temp_name in graphs, (temp_name, graphs)
        assert target_name not in graphs, (target_name, graphs)

    def test_restore_timeout_is_never_reaped_as_a_wedge(
            self, client, dr_env, mem_storage, monkeypatch):
        """AC3 (#3813 x #3845) — a restore COPY TIMEOUT is never classified as
        a fork-slot wedge, even when the wedge EVIDENCE is on.

        Both branches are live on the restore path and their inputs are
        independent: the copy can raise its own ``RestoreCopyTimeoutError``
        while our daemon is genuinely carrying a lingering module-fork child.
        On that overlap the TIMEOUT must win — reaping the child and re-issuing
        the copy would start a SECOND server-side fork while the first may
        still be running (the reason the branch exists at all, #3813).

        Both wedge signals are forced ON here. On a plain docker-lane run
        ``fork_slot_is_wedged`` is always False and the #3845 guards inject a
        ``ResponseError`` — never a ``RestoreCopyTimeoutError`` — so round 1's
        suite left this branch unpinned (dropping the guard kept it green).
        Forcing the signals is what makes the assertion non-vacuous.

        RED (mutation): replace the guard with ``if False and isinstance(...)``
        — the forced wedge signals then match, ``recover_fork_slot`` runs, and
        this fails on the call-count and on the status/detail.
        """
        import tortoise.hosted_backup as hb
        from tortoise.fork_slot import ForkSlotRecovery

        # #4233: keep the OUTCOME settle poll short — the destination never
        # materialises, so the TIMEOUT verdict under test is unchanged.
        monkeypatch.setenv("TORTOISE_RESTORE_SWAP_SETTLE_S", "0.1")

        _seed_team("team_x", nodes=2)
        key = _default_drill_key(client, mem_storage)

        # The masked shape the embedded lane produced: the redis client read
        # timeout chained behind the parser's ValueError.
        def masked_timeout(redis_client, src_name, dst_name):
            try:
                raise redis.exceptions.TimeoutError(
                    "Timeout reading from socket")
            except redis.exceptions.TimeoutError:
                raise ValueError("I/O operation on closed file.")  # noqa: B904

        monkeypatch.setattr(hb, "_issue_graph_copy", masked_timeout)
        # Force BOTH wedge-evidence signals ON: a wedge classifier that is
        # consulted AT ALL would call this copy a wedge. Reaching either one is
        # the bug under test.
        monkeypatch.setattr(hb, "is_fork_refusal", lambda exc: True)
        monkeypatch.setattr(hb, "fork_slot_is_wedged", lambda *a, **kw: True)

        recoveries: list = []

        def spy_recover(*args, **kwargs):
            recoveries.append(args)
            return ForkSlotRecovery(
                wedged=True, recovered=True, killed_pids=[4242],
                detail="reaped 1 hung child")

        monkeypatch.setattr(hb, "recover_fork_slot", spy_recover)

        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post("/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
                        json={"org_id": "team_x", "backup_key": key})

        # The TIMEOUT is the verdict — never a wedge.
        assert r.status_code == 503, r.text
        body = r.text.lower()
        detail = r.json()["detail"]
        assert "timed out" in detail.lower(), detail
        assert "not restored" in detail.lower(), detail
        assert "wedge" not in body, detail
        assert "fork slot" not in body, detail
        # The wedge recovery was NEVER invoked: no child was reaped, and no
        # second server-side fork was issued while the first may still run.
        assert recoveries == [], recoveries

    def test_dead_connection_is_reported_differently_from_a_timeout(
            self, client, dr_env, mem_storage, monkeypatch):
        """AC2 (mirror) — a genuinely unusable connection is NOT reported as a
        timeout. Same status (503), different detail: the two verdicts are
        distinguishable from outside, on the observable alone.

        RED (mutation): make ``_is_client_read_timeout`` return True
        unconditionally (classify every copy failure as a timeout) — the detail
        then claims a timeout for a dead connection and this fails.
        """
        import tortoise.hosted_backup as hb

        _seed_team("team_x", nodes=2)
        key = _default_drill_key(client, mem_storage)

        def dead_connection(redis_client, src_name, dst_name):
            raise redis.exceptions.ConnectionError("Connection reset by peer")

        monkeypatch.setattr(hb, "_issue_graph_copy", dead_connection)
        ha_mod._LAST_DRILL_AT = 0.0
        r = client.post("/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
                        json={"org_id": "team_x", "backup_key": key})
        assert r.status_code == 503, r.text
        detail = r.json()["detail"]
        assert "restore swap failed" in detail.lower(), detail
        assert "timed out" not in detail.lower(), detail
        # ... and the verified temp graph is still recoverable.
        m = re.search(r"(\w+_restore_\w+)", detail)
        assert m, detail
        sdk = TortoiseSDK("/tmp/x.db", namespace="registry")
        assert m.group(1) in sdk._get_proj().db.list_graphs(), detail

    def test_ordinary_operations_keep_failing_fast_with_their_own_bound(
            self, client, dr_env, mem_storage, monkeypatch):
        """#2850 intact — the fix must NOT be "give every DB call a longer
        bound". Only the restore's copies get a generous read bound; an
        ORDINARY operation on the SAME connection after a restore still fails
        fast on the ordinary request bound (that is the whole point of #2850:
        a stalled call must not park a thread / the event loop).

        RED (mutation): widen the ordinary bound instead of decoupling the
        swap — e.g. raise ``projection._DB_TIMEOUT_MIN_S`` above the configured
        ordinary bound so ``_socket_timeouts()`` falls back to its (larger)
        default. The ordinary probe below then stops failing fast and this
        fails with ``DID NOT RAISE Exception`` (verified).
        """
        import time

        import tortoise.hosted_backup as hb

        monkeypatch.setenv("TORTOISE_FALKORDB_SOCKET_TIMEOUT_S", "1")
        monkeypatch.setenv("TORTOISE_RESTORE_SWAP_TIMEOUT_S", "60")

        calls: list[tuple[str, str]] = []

        def slow_copy(redis_client, src_name, dst_name):
            # Record the seam so the assertion below can prove the restore
            # actually routed its copy through here (see the AC1 guard above).
            calls.append((src_name, dst_name))
            # Fork-free (see the AC1 guard) — this guard is about the ORDINARY
            # client, not about GRAPH.COPY's embedded-lane fork.
            if "_restore_" in src_name:
                redis_client.execute_command("BLPOP", "_bound_probe", "2.0")
            from falkordb import Graph
            hb.restore_graph(Graph(redis_client, dst_name),
                             hb.dump_graph(Graph(redis_client, src_name)))

        monkeypatch.setattr(hb, "_issue_graph_copy", slow_copy)

        db = _held_proj_db()
        source = db.select_graph("org_bound_source")
        source.query("MATCH (n) DETACH DELETE n")
        source.query(
            "CREATE (p:Point {id:'pt-0', content:'c', pointKind:'claim'})")
        payload = hb.dump_graph(source)
        target = db.select_graph("org_bound_target")
        target.query("MATCH (n) DETACH DELETE n")
        target.query(
            "CREATE (p:Point {id:'stale', content:'old', pointKind:'claim'})")
        hb._restore_into_temp_verify_swap(db, payload, live_name="org_bound_target")

        # The seam WAS reached (see the AC1 guard) — a mutation that reverts the
        # restore copy to ``Graph.copy`` would otherwise leave this green while
        # the delayed swap copy silently stopped exercising the bound.
        assert calls, "restore never reached the _issue_graph_copy seam"
        assert any("_restore_" in src for src, _ in calls), calls

        # ... and the ORDINARY client is untouched by the restore: an ordinary
        # operation outliving the configured 1s ordinary bound still fails fast.
        # (The observable ceiling is the ordinary client's own bounded retry —
        # 1 attempt + _EMBEDDED_RETRY_COUNT(1) x 1s + jitter ~= 2.1s — which is
        # still far short of the 4s hold the server was told to wait.)
        ordinary = getattr(db, "connection", None) or getattr(db, "client", None)
        started = time.monotonic()
        with pytest.raises(Exception) as ei:
            ordinary.execute_command("BLPOP", "_ordinary_bound_probe", "4.0")
        elapsed = time.monotonic() - started
        assert elapsed < 3.0, (
            f"an ordinary operation waited {elapsed:.1f}s — the ordinary read "
            f"bound (#2850) was widened by the restore"
        )
        # ... and it failed AS a timeout, not as some other error.
        assert hb._is_client_read_timeout(ei.value), ei.value

    def test_read_bound_expiry_is_resolved_by_the_copys_outcome(
            self, client, monkeypatch):
        """#4233 — the read bound is a HYPOTHESIS, not a verdict.

        When the client read timeout fires but the server-side copy actually
        COMPLETED, the restore must succeed: the settle poll confirms the
        destination matches the source's node and edge counts instead of
        reporting a timeout
        that never happened. This is the contended-runner flake. The source
        carries an EDGE as well, so the edge half of the parity check is
        non-trivial.

        The injected seam does the real (fork-free) copy and THEN raises the
        exact masked read-timeout shape the embedded lane produced — the
        server-side work finished even though the client's read did not.

        RED (mutation): drop the ``_await_restore_copy_settled`` branch (fail
        on the timeout immediately) — the restore then raises
        ``RestoreCopyTimeoutError`` and this fails (verified).
        """
        import tortoise.hosted_backup as hb

        monkeypatch.setenv("TORTOISE_RESTORE_SWAP_SETTLE_S", "2")

        def copy_then_timeout(redis_client, src_name, dst_name):
            from falkordb import Graph
            hb.restore_graph(Graph(redis_client, dst_name),
                             hb.dump_graph(Graph(redis_client, src_name)))
            try:
                raise redis.exceptions.TimeoutError("Timeout reading from socket")
            except redis.exceptions.TimeoutError:
                raise ValueError("I/O operation on closed file.")  # noqa: B904

        monkeypatch.setattr(hb, "_issue_graph_copy", copy_then_timeout)

        db = _held_proj_db()
        source = db.select_graph("org_settle_source")
        source.query("MATCH (n) DETACH DELETE n")
        for i in range(2):
            source.query(
                "CREATE (p:Point {id:$id, content:$c, pointKind:'claim'})",
                params={"id": f"pt-{i}", "c": f"c{i}"},
            )
        source.query(
            "MATCH (a:Point {id:'pt-0'}), (b:Point {id:'pt-1'}) "
            "CREATE (a)-[:LINKED]->(b)")
        payload = hb.dump_graph(source)
        assert payload["edge_count"] == 1, payload["edge_count"]
        target = db.select_graph("org_settle_target")
        target.query("MATCH (n) DETACH DELETE n")
        target.query(
            "CREATE (p:Point {id:'stale', content:'old', pointKind:'claim'})")

        result = hb._restore_into_temp_verify_swap(
            db, payload, live_name="org_settle_target")

        rows = db.select_graph("org_settle_target").query(
            "MATCH (n:Point) RETURN n.id ORDER BY n.id").result_set
        assert [r[0] for r in rows] == ["pt-0", "pt-1"]
        # #4233: the overrun is surfaced on the result, so an RTO breach
        # caused by it is attributable (the #3845 fork_slot precedent).
        assert result.get("copy_read_bound_overrun") is True

    def test_pre_restore_copy_overrun_is_surfaced(self, client, monkeypatch):
        """#4233 — the PRE-RESTORE safety copy's overrun is surfaced too.

        A restore runs TWO bounded copies, so the flag must not be satisfiable
        only by the swap. Overrun ONLY the pre-restore copy (its destination is
        the ``_pre_restore_`` scratch graph) and assert the flag is set while
        the swap copy completes normally.

        RED (mutation): pass ``settled=None`` for the pre-restore copy — the
        flag is then absent and this fails (verified).
        """
        import tortoise.hosted_backup as hb

        monkeypatch.setenv("TORTOISE_RESTORE_SWAP_SETTLE_S", "2")
        seen: list[str] = []

        def pre_only_timeout(redis_client, src_name, dst_name):
            from falkordb import Graph
            seen.append(dst_name)
            hb.restore_graph(Graph(redis_client, dst_name),
                             hb.dump_graph(Graph(redis_client, src_name)))
            if "_pre_restore_" not in dst_name:
                return  # the swap copy completes cleanly
            try:
                raise redis.exceptions.TimeoutError("Timeout reading from socket")
            except redis.exceptions.TimeoutError:
                raise ValueError("I/O operation on closed file.")  # noqa: B904

        monkeypatch.setattr(hb, "_issue_graph_copy", pre_only_timeout)
        db = _held_proj_db()
        source = db.select_graph("org_settle_source")
        source.query("MATCH (n) DETACH DELETE n")
        source.query("CREATE (p:Point {id:'pt-0'})")
        payload = hb.dump_graph(source)
        target = db.select_graph("org_settle_target")
        target.query("MATCH (n) DETACH DELETE n")
        target.query("CREATE (p:Point {id:'stale'})")  # live_nodes > 0

        result = hb._restore_into_temp_verify_swap(
            db, payload, live_name="org_settle_target")

        # the pre-restore copy actually ran, and it is the one that overran
        assert any("_pre_restore_" in d for d in seen), seen
        assert result.get("copy_read_bound_overrun") is True
        rows = db.select_graph("org_settle_target").query(
            "MATCH (n:Point) RETURN n.id").result_set
        assert [r[0] for r in rows] == ["pt-0"]

    def test_drill_record_carries_the_copy_overrun(self, client, dr_env,
                                                   mem_storage, monkeypatch):
        """#4233 — the overrun is attributed from the PERSISTED drill record.

        Returning the flag from ``_restore_into_temp_verify_swap`` is not
        enough: an unattended scheduled drill is reviewed from its persisted
        record, so the record must carry it — otherwise the attribution the
        flag exists for never reaches an operator.

        RED (mutation): drop the ``copy_read_bound_overrun`` entry from
        ``_drill_execute``'s ``detail`` — the record assertions below fail
        (verified).
        """
        import tortoise.hosted_backup as hb

        monkeypatch.setenv("TORTOISE_RESTORE_SWAP_SETTLE_S", "2")

        def copy_then_timeout(redis_client, src_name, dst_name):
            from falkordb import Graph
            hb.restore_graph(Graph(redis_client, dst_name),
                             hb.dump_graph(Graph(redis_client, src_name)))
            try:
                raise redis.exceptions.TimeoutError("Timeout reading from socket")
            except redis.exceptions.TimeoutError:
                raise ValueError("I/O operation on closed file.")  # noqa: B904

        monkeypatch.setattr(hb, "_issue_graph_copy", copy_then_timeout)
        _seed_team("team_x", nodes=2)
        key = _default_drill_key(client, mem_storage)
        monkeypatch.setattr(ha_mod, "_LAST_DRILL_AT", 0.0)
        r = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"org_id": "team_x", "backup_key": key},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["copy_read_bound_overrun"] is True, body
        assert body["record"]["detail"]["copy_read_bound_overrun"] is True
        rec = json.loads(mem_storage.download(ha_mod._DRILL_RECORD_KEY))
        assert rec["detail"]["copy_read_bound_overrun"] is True

    def test_read_bound_expiry_without_the_copy_is_still_a_timeout(
            self, client, dr_env, mem_storage, monkeypatch):
        """#4233 (mirror, and the settle branch's mutation check).

        The settle poll must NOT blanket-accept a timeout. A client read
        timeout with NO completed destination is still reported as a timeout:
        the restore 503s, names the verified temp graph intact, and says the
        live graph was NOT restored. Force the genuinely broken copy (the
        timeout fires and the destination never materializes) and the restore
        must still red.
        """
        import tortoise.hosted_backup as hb

        monkeypatch.setenv("TORTOISE_RESTORE_SWAP_SETTLE_S", "0.5")

        def timeout_only(redis_client, src_name, dst_name):
            try:
                raise redis.exceptions.TimeoutError("Timeout reading from socket")
            except redis.exceptions.TimeoutError:
                raise ValueError("I/O operation on closed file.")  # noqa: B904

        monkeypatch.setattr(hb, "_issue_graph_copy", timeout_only)

        _seed_team("team_x", nodes=2)
        key = _default_drill_key(client, mem_storage)
        monkeypatch.setattr(ha_mod, "_LAST_DRILL_AT", 0.0)
        r = client.post(
            "/v1/internal/backups/drill", headers=INTERNAL_HEADERS,
            json={"org_id": "team_x", "backup_key": key},
        )
        assert r.status_code == 503, r.text
        detail = r.json()["detail"]
        assert "timed out" in detail.lower(), detail
        assert "not restored" in detail.lower(), detail

    def test_restore_copy_settled_requires_count_parity(self, client):
        """#4233 — the settle predicate is node/edge COUNT parity, not existence.

        A destination that merely EXISTS (a stale graph, a torn install) must
        never be accepted as the copy's outcome. Node AND edge counts are
        compared.

        RED (mutation): reduce ``_restore_copy_settled`` to an existence
        check — the wrong-content assertions below then read True (verified).
        """
        import tortoise.hosted_backup as hb

        db = _held_proj_db()
        src = db.select_graph("org_settle_source")
        src.query("MATCH (n) DETACH DELETE n")
        for i in range(2):
            src.query("CREATE (p:Point {id:$id})", params={"id": f"pt-{i}"})
        src.query(
            "MATCH (a:Point {id:'pt-0'}), (b:Point {id:'pt-1'}) "
            "CREATE (a)-[:LINKED]->(b)")

        dst = db.select_graph("org_settle_target")
        dst.query("MATCH (n) DETACH DELETE n")
        # exists — but the wrong NODE count
        dst.query("CREATE (p:Point {id:'only'})")
        assert hb._restore_copy_settled(
            db, "org_settle_source", "org_settle_target") is False
        # node parity — but the wrong EDGE count
        dst.query("CREATE (p:Point {id:'second'})")
        assert hb._restore_copy_settled(
            db, "org_settle_source", "org_settle_target") is False
        # exact parity
        dst.query(
            "MATCH (a:Point {id:'only'}), (b:Point {id:'second'}) "
            "CREATE (a)-[:LINKED]->(b)")
        assert hb._restore_copy_settled(
            db, "org_settle_source", "org_settle_target") is True

    def test_preexisting_destination_is_never_settled(self, client,
                                                      monkeypatch):
        """#4233 — a destination that already existed when the copy was issued
        is never accepted as the copy's outcome.

        The swap's live-delete is best-effort. If it failed, a destination
        holding matching counts would otherwise read as a successful restore
        of a graph the copy never wrote. Fail-closed.

        RED (mutation): make ``_graph_present`` return False unconditionally —
        the pre-seeded destination is then accepted and this fails (verified).
        """
        import tortoise.hosted_backup as hb

        monkeypatch.setenv("TORTOISE_RESTORE_SWAP_SETTLE_S", "0.5")
        db = _held_proj_db()
        # Source and destination hold the SAME content, and the destination is
        # NOT deleted — exactly the failed-live-delete shape.
        for name in ("org_settle_source", "org_settle_target"):
            g = db.select_graph(name)
            g.query("MATCH (n) DETACH DELETE n")
            g.query("CREATE (p:Point {id:'pt-0'})")

        def timeout_only(redis_client, src_name, dst_name):
            try:
                raise redis.exceptions.TimeoutError("Timeout reading from socket")
            except redis.exceptions.TimeoutError:
                raise ValueError("I/O operation on closed file.")  # noqa: B904

        monkeypatch.setattr(hb, "_issue_graph_copy", timeout_only)
        with pytest.raises(hb.RestoreCopyTimeoutError):
            hb._graph_copy_with_restore_bound(
                db, "org_settle_source", "org_settle_target",
                role="test swap", intact_name="test intact")

    def test_settle_never_creates_a_missing_destination(self, client,
                                                        monkeypatch):
        """#4233 — a failed settle leaves an ABSENT destination absent.

        `_restore_copy_settled` reads `GRAPH.LIST` FIRST because a Cypher read
        on a missing graph CREATES an empty one — on the swap's freshly deleted
        live graph that would be the wipe-then-empty class. A copy that times
        out and never lands must leave the destination absent.

        RED (mutation): drop the `dst_name not in names` guard from
        `_restore_copy_settled` — the probe read creates `org_settle_target`
        and this fails (verified).
        """
        import tortoise.hosted_backup as hb

        monkeypatch.setenv("TORTOISE_RESTORE_SWAP_SETTLE_S", "0.5")
        db = _held_proj_db()
        src = db.select_graph("org_settle_source")
        src.query("MATCH (n) DETACH DELETE n")
        src.query("CREATE (p:Point {id:'pt-0'})")
        if "org_settle_target" in db.list_graphs():
            db.select_graph("org_settle_target").delete()

        def timeout_only(redis_client, src_name, dst_name):
            try:
                raise redis.exceptions.TimeoutError("Timeout reading from socket")
            except redis.exceptions.TimeoutError:
                raise ValueError("I/O operation on closed file.")  # noqa: B904

        monkeypatch.setattr(hb, "_issue_graph_copy", timeout_only)
        with pytest.raises(hb.RestoreCopyTimeoutError):
            hb._graph_copy_with_restore_bound(
                db, "org_settle_source", "org_settle_target",
                role="test swap", intact_name="test intact")
        assert "org_settle_target" not in db.list_graphs()

    def test_graph_present_fails_closed(self):
        """#4233 — an unreadable listing must never authorize a settle.

        ``_graph_present`` is the guard that refuses to treat a pre-existing
        destination as the copy's output, so a probe failure must read
        PRESENT, not absent.

        RED (mutation): return False on the except branch — this fails.
        """
        import tortoise.hosted_backup as hb

        class _Broken:
            @staticmethod
            def list_graphs():
                raise RuntimeError("listing unavailable")

        assert hb._graph_present(_Broken(), "org_settle_target") is True

    def test_settle_accepts_a_copy_that_lands_during_the_poll(self, client,
                                                              monkeypatch):
        """#4233 — the settle is a POLL, not a single check.

        The contended-runner shape is a copy that lands DURING the settle
        window, after the read bound expired. A destination that materializes
        on a LATER poll must still be accepted; collapsing the loop to one
        check would miss it.

        Deterministic — no sleep, no thread: the first poll makes the
        destination appear and reports a miss (as if the copy had not landed
        yet); the second poll sees the real state.

        RED (mutation): replace ``_await_restore_copy_settled``'s loop with a
        single ``_restore_copy_settled`` call — the first call reports the miss
        and this fails (verified).
        """
        import tortoise.hosted_backup as hb

        monkeypatch.setenv("TORTOISE_RESTORE_SWAP_SETTLE_S", "3")
        db = _held_proj_db()
        src = db.select_graph("org_settle_source")
        src.query("MATCH (n) DETACH DELETE n")
        src.query("CREATE (p:Point {id:'pt-0'})")
        if "org_settle_target" in db.list_graphs():
            db.select_graph("org_settle_target").delete()

        def timeout_only(redis_client, src_name, dst_name):
            try:
                raise redis.exceptions.TimeoutError("Timeout reading from socket")
            except redis.exceptions.TimeoutError:
                raise ValueError("I/O operation on closed file.")  # noqa: B904

        monkeypatch.setattr(hb, "_issue_graph_copy", timeout_only)
        real_settled = hb._restore_copy_settled
        polls: list[int] = []

        def land_on_second_poll(db_, src_name, dst_name):
            polls.append(len(polls))
            if len(polls) == 1:
                # the copy lands only now — after the read bound, during the
                # settle window — and this poll still reports the miss.
                hb.restore_graph(db_.select_graph(dst_name),
                                 hb.dump_graph(db_.select_graph(src_name)))
                return False
            return real_settled(db_, src_name, dst_name)

        monkeypatch.setattr(hb, "_restore_copy_settled", land_on_second_poll)
        hb._graph_copy_with_restore_bound(
            db, "org_settle_source", "org_settle_target",
            role="test swap", intact_name="test intact")

        assert len(polls) >= 2, polls  # the loop really polled more than once
        assert "org_settle_target" in db.list_graphs()

    def test_restore_swap_settle_bound_resolution(self, monkeypatch):
        """#4233 — the settle knob's resolution, pinned.

        Empty/non-numeric/non-finite/non-positive all fall back to the READ
        bound (a settle of 0 is not a disable switch); a positive value is
        clamped to [0.05, 3600]. This is also what keeps the other guards from
        silently waiting the 120s read bound if the env var stops being read.
        """
        import tortoise.hosted_backup as hb

        monkeypatch.setenv("TORTOISE_RESTORE_SWAP_TIMEOUT_S", "120")
        for raw, expected in [
            (None, 120.0), ("", 120.0), ("abc", 120.0), ("nan", 120.0),
            ("inf", 120.0), ("0", 120.0), ("-1", 120.0),
            ("0.001", 0.05), ("2", 2.0), ("9999", 3600.0),
        ]:
            if raw is None:
                monkeypatch.delenv("TORTOISE_RESTORE_SWAP_SETTLE_S",
                                   raising=False)
            else:
                monkeypatch.setenv("TORTOISE_RESTORE_SWAP_SETTLE_S", raw)
            assert hb._restore_swap_settle_s() == expected, raw
