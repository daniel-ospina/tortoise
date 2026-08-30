"""Hosted per-tenant custom packs (#1935, epic #1891 slice 4; test-design
#1898 surfaces 5/7/8).

Covers:
- validate_manifest: valid / malformed / missing-namespace / reserved /
  ontology-only (connector+tool entrypoints) / oversized
- POST /v1/packs/manifests: 201 / 422 / 413 / 401
- Storage + activation: :PackManifest node + PackInstall source='custom'
- GET /v1/packs: tenant packs listed; cross-tenant isolation (A's pack
  never visible to B — structural graph-namespace isolation)
- Concurrent double-upload → exactly one activation (idempotent MERGE +
  per-(graph,namespace) lock #1307)
- Deployment gate: tortoise_pack_install is an actionable stub when the
  serving app is not hosted_api (self-host)
- tenant_view memoization: cached per (tenant, pack_config_version),
  invalidated on :PackManifest write (#1154/#1350)

Docker lane (default): TORTOISE_DB_URI must be set (epic #1647 P4).
"""
from __future__ import annotations

import os
import tempfile
import threading
import uuid

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")
os.environ.setdefault("FASTAPI_INTERNAL_KEY", "test-internal-shared-secret-xyz")

import pytest
from fastapi.testclient import TestClient

import tortoise.hosted_api as ha_mod
from tortoise.hosted_api import app, get_current_team

TEST_TEAM_ID = f"team-{uuid.uuid4().hex[:8]}"
TEST_TEAM = {
    "team_id": TEST_TEAM_ID,
    "key_id": "test-key-001",
    "tier": "free",
    "max_users": 1, "max_graphs": 1, "max_points": 10000,
    "max_api_keys": 2, "max_sessions": 1000,
}
TEST_TEAM_B = {"team_id": f"team-{uuid.uuid4().hex[:8]}", "key_id": "test-key-002",
               "tier": "free", "max_users": 1, "max_graphs": 1,
               "max_points": 10000, "max_api_keys": 2, "max_sessions": 1000}

VALID_MANIFEST = """namespace: tenant-ops
name: Tenant Operations
version: 0.1.0
tier: free
ontology:
  extends: core
  objectKinds:
  - contract
"""

RESERVED_MANIFEST = VALID_MANIFEST.replace("tenant-ops", "dev")
CONNECTOR_MANIFEST = VALID_MANIFEST + """
connectors:
- source: github
  sourceKind: github_issue
  entrypoint: connector.py::GitHubConnector
"""


def _patch_sdk_init(db_path: str):
    import tortoise.sdk as sdk_mod
    _orig = sdk_mod.TortoiseSDK.__init__

    def _patched(self, db_path_arg=None, *, namespace=None, **kwargs):
        _orig(self, db_path, namespace=namespace)
    sdk_mod.TortoiseSDK.__init__ = _patched
    return _orig


@pytest.fixture
def client():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM)
        _orig = _patch_sdk_init(db_path)
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()
            import tortoise.sdk as sdk_mod
            sdk_mod.TortoiseSDK.__init__ = _orig


@pytest.fixture
def client_b():
    """Second tenant (isolation probe)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test_b.db")
        app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM_B)
        _orig = _patch_sdk_init(db_path)
        try:
            yield TestClient(app)
        finally:
            app.dependency_overrides.clear()
            import tortoise.sdk as sdk_mod
            sdk_mod.TortoiseSDK.__init__ = _orig


def _team_sdk(team_id: str = TEST_TEAM_ID):
    return ha_mod._make_sdk(namespace=team_id)


# ── validate_manifest (unit) ────────────────────────────────────────────────

class TestValidateManifest:
    def test_valid(self):
        from tortoise.pack_manifest_store import validate_manifest
        r = validate_manifest(VALID_MANIFEST)
        assert r.ok and r.namespace == "tenant-ops", r.errors

    def test_malformed_yaml(self):
        from tortoise.pack_manifest_store import validate_manifest
        r = validate_manifest("namespace: [unclosed")
        assert not r.ok

    def test_missing_namespace(self):
        from tortoise.pack_manifest_store import validate_manifest
        r = validate_manifest("name: No Namespace\ntier: free\n")
        assert not r.ok
        assert "namespace" in r.errors[0]

    def test_reserved_namespace_rejected(self):
        from tortoise.pack_manifest_store import validate_manifest
        r = validate_manifest(RESERVED_MANIFEST)
        assert not r.ok
        assert "reserved starter pack" in r.errors[0]

    def test_connector_entrypoint_rejected_ontology_only(self):
        from tortoise.pack_manifest_store import validate_manifest
        r = validate_manifest(CONNECTOR_MANIFEST)
        assert not r.ok
        assert "ontology-only" in r.errors[0]

    def test_oversized(self):
        from tortoise.pack_manifest_store import validate_manifest
        r = validate_manifest("namespace: big\nname: X\n# " + "x" * (70 * 1024))
        assert not r.ok
        assert "64KB" in r.errors[0]


# ── API surface ─────────────────────────────────────────────────────────────

class TestUploadEndpoint:
    def test_upload_201_and_activation(self, client):
        r = client.post("/v1/packs/manifests",
                        json={"manifest_yaml": VALID_MANIFEST})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["activated"] is True and body["namespace"] == "tenant-ops"
        # Storage + activation records present.
        sdk = _team_sdk()
        from tortoise.pack_manifest_store import get_tenant_manifests
        ms = get_tenant_manifests(sdk)
        assert any(m["namespace"] == "tenant-ops" for m in ms)
        from tortoise.pack_state import get_tenant_packs
        packs = get_tenant_packs(sdk)
        assert any(p["namespace"] == "tenant-ops" and p["source"] == "custom"
                   for p in packs)

    def test_upload_422_invalid(self, client):
        r = client.post("/v1/packs/manifests",
                        json={"manifest_yaml": "namespace: [broken"})
        assert r.status_code == 422
        assert "errors" in r.json()["detail"]

    def test_upload_422_reserved(self, client):
        r = client.post("/v1/packs/manifests",
                        json={"manifest_yaml": RESERVED_MANIFEST})
        assert r.status_code == 422
        assert "reserved" in str(r.json()["detail"])

    def test_upload_413_oversized(self, client):
        big = "namespace: big\nname: X\n# " + "x" * (70 * 1024)
        r = client.post("/v1/packs/manifests", json={"manifest_yaml": big})
        assert r.status_code == 413

    def test_upload_401_unauthenticated(self):
        from tortoise.hosted_api import get_current_team as _gt
        app.dependency_overrides.pop(_gt, None)
        with tempfile.TemporaryDirectory() as tmpdir:
            _orig = _patch_sdk_init(os.path.join(tmpdir, "t.db"))
            try:
                c = TestClient(app)
                r = c.post("/v1/packs/manifests",
                           json={"manifest_yaml": VALID_MANIFEST})
                assert r.status_code == 401
            finally:
                import tortoise.sdk as sdk_mod
                sdk_mod.TortoiseSDK.__init__ = _orig
                app.dependency_overrides.clear()

    def test_list_includes_tenant_pack(self, client):
        client.post("/v1/packs/manifests",
                    json={"manifest_yaml": VALID_MANIFEST})
        r = client.get("/v1/packs")
        assert r.status_code == 200
        ns = [p["namespace"] for p in r.json()["packs"]]
        assert "tenant-ops" in ns


# ── Cross-tenant isolation (structural — graph namespace) ──────────────────

class TestIsolation:
    def test_tenant_a_pack_invisible_to_tenant_b(self):
        """Structural isolation via graph namespace: tenant A's pack is never
        visible to tenant B (both share one process but separate graphs).
        Uses a namespace-routing SDK patch so each team resolves its OWN temp
        DB (a single module-global patch — two sequential patches would let
        the second override the first)."""
        with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
            db_a = os.path.join(tmp_a, "a.db")
            db_b = os.path.join(tmp_b, "b.db")

            def _route_patch(self, db_path_arg=None, *, namespace=None, **kwargs):
                target = db_b if namespace == TEST_TEAM_B["team_id"] else db_a
                _orig(self, target, namespace=namespace)

            import tortoise.sdk as sdk_mod
            _orig = sdk_mod.TortoiseSDK.__init__
            sdk_mod.TortoiseSDK.__init__ = _route_patch
            app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM)
            try:
                c_a = TestClient(app)
                r = c_a.post("/v1/packs/manifests",
                             json={"manifest_yaml": VALID_MANIFEST})
                assert r.status_code == 201, r.text
                app.dependency_overrides[get_current_team] = lambda: dict(TEST_TEAM_B)
                c_b = TestClient(app)
                r_b = c_b.get("/v1/packs")
                ns = [p["namespace"] for p in r_b.json()["packs"]]
                assert "tenant-ops" not in ns, \
                    "tenant B must not see tenant A's pack"
            finally:
                app.dependency_overrides.clear()
                sdk_mod.TortoiseSDK.__init__ = _orig


# ── Concurrency (idempotent activation) ────────────────────────────────────

class TestConcurrency:
    def test_double_upload_single_activation(self, client):
        from tortoise.pack_manifest_store import get_tenant_manifests
        results = []

        def _post():
            r = client.post("/v1/packs/manifests",
                            json={"manifest_yaml": VALID_MANIFEST})
            results.append(r.status_code)

        threads = [threading.Thread(target=_post) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        sdk = _team_sdk()
        ms = get_tenant_manifests(sdk)
        assert len([m for m in ms if m["namespace"] == "tenant-ops"]) == 1, \
            f"expected exactly one PackManifest, got {len(ms)}"


# ── Deployment gate + tenant view ──────────────────────────────────────────

class TestDeploymentGateAndView:
    def test_mcp_install_stub_on_selfhost(self, monkeypatch):
        """The MCP tool is an actionable stub when the serving app is not
        hosted_api (self-host) — deployment-gated (#1935 R10)."""
        import sys as _sys

        import tortoise.mcp_server as mcp_mod
        monkeypatch.setitem(_sys.modules, "tortoise.hosted_api", None)
        out = mcp_mod.tortoise_pack_install(VALID_MANIFEST)
        assert out["installed"] is False
        assert "HOSTED-only" in out["error"]

    def test_tenant_view_memoized_and_invalidated(self, client):
        from tortoise.pack_manifest_store import tenant_view
        sdk = _team_sdk()
        v1 = tenant_view(TEST_TEAM_ID, sdk)
        v2 = tenant_view(TEST_TEAM_ID, sdk)
        assert v1 is v2, "memoized view must be cached"
        client.post("/v1/packs/manifests",
                    json={"manifest_yaml": VALID_MANIFEST})
        v3 = tenant_view(TEST_TEAM_ID, sdk)
        assert any(m["namespace"] == "tenant-ops" for m in v3["tenant"]), \
            "view must refresh after a :PackManifest write"
