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

import json
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
        # Wire size ~70KB < ~196KB wire cap → this must be the YAML gate,
        # not the wire layers (pins the ordering of the two 413 paths).
        assert r.json()["detail"] == "manifest exceeds 64KB"

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


# ── Body-size cap before parse (#2029) ────────────────────────────────────

class TestUploadBodyCap:
    """#2029: oversized request bodies are rejected BEFORE buffering/parsing.

    The pre-fix endpoint buffered the whole body via request.json() before
    the 64KB manifest cap applied — a memory-DoS on a tenant-authenticated
    surface. The wire layers (Content-Length early-exit + unconditional
    streaming cap mirroring _read_import_body) reject bodies over the wire
    cap (~196KB) without ever buffering them.
    """

    @staticmethod
    def _wire_cap() -> int:
        from tortoise.pack_manifest_store import MAX_MANIFEST_BYTES
        return MAX_MANIFEST_BYTES * 3 + 4096

    def test_upload_413_spoofed_content_length_not_read(self, client):
        """Content-Length over the wire cap → 413 with the body UNREAD.
        The payload is a VALID JSON envelope — a read+parse would 201, so
        413 uniquely proves the header pre-check fired before the body was
        ever touched. CL is comfortably oversized (1GiB) to pin the contract
        (oversized CL → 413 wire detail), not the cap formula."""
        r = client.post(
            "/v1/packs/manifests",
            content=json.dumps({"manifest_yaml": VALID_MANIFEST}).encode(),
            headers={"content-length": str(1 << 30)},
        )
        assert r.status_code == 413
        assert r.json()["detail"] == "manifest request body exceeds the size cap"

    def test_upload_413_chunked_streaming_cap(self, client):
        """Unknown-length (chunked) oversized body → 413 via the streaming
        cap BEFORE parse — the chunks are invalid JSON, so a parse attempt
        would raise JSONDecodeError (→ 500), not 413."""
        def _stream():
            for _ in range(12):
                yield b"not-json-" * 8192  # 9-byte prefix × 8192, no CL
        r = client.post("/v1/packs/manifests", content=_stream())
        assert r.status_code == 413
        assert r.json()["detail"] == "manifest request body exceeds the size cap"

    def test_upload_201_chunked_valid(self, client):
        """A valid manifest streamed WITHOUT Content-Length still parses and
        activates — the streaming path is not just a cap."""
        def _stream():
            payload = json.dumps({"manifest_yaml": VALID_MANIFEST}).encode()
            for i in range(0, len(payload), 97):
                yield payload[i:i + 97]
        r = client.post("/v1/packs/manifests", content=_stream())
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["activated"] is True and body["namespace"] == "tenant-ops"

    def test_upload_201_exact_64kb_yaml_boundary(self, client):
        """A manifest of EXACTLY 64KB yaml bytes clears the wire layers and
        the authoritative yaml gate (strict `>` → 65536 is not > 65536) →
        201. Guards against the cap shifting from yaml bytes to wire bytes."""
        pad = 65536 - len(VALID_MANIFEST.encode()) - 2
        manifest = VALID_MANIFEST + "# " + "x" * pad
        assert len(manifest.encode()) == 65536
        r = client.post("/v1/packs/manifests", json={"manifest_yaml": manifest})
        assert r.status_code == 201, r.text

    def test_upload_wire_cap_boundary(self, client):
        """Strict `>` at the wire cap: exactly body_cap bytes passes both
        cap checks (→ parse → 201); body_cap+1 → 413. Pins the off-by-one
        semantics of the CL pre-check and the streaming cap."""
        envelope = json.dumps({"manifest_yaml": VALID_MANIFEST}).encode()
        body_cap = self._wire_cap()
        assert len(envelope) < body_cap
        exact = envelope + b" " * (body_cap - len(envelope))  # JSON: trailing ws ok
        r = client.post("/v1/packs/manifests", content=exact)
        assert r.status_code == 201, r.text
        r = client.post("/v1/packs/manifests", content=exact + b" ")
        assert r.status_code == 413
        assert r.json()["detail"] == "manifest request body exceeds the size cap"

    def test_upload_201_malformed_content_length_falls_back(self, client):
        """Malformed Content-Length is ignored (the header is never a trust
        boundary) → the streaming path reads and parses the valid manifest."""
        r = client.post(
            "/v1/packs/manifests",
            content=json.dumps({"manifest_yaml": VALID_MANIFEST}).encode(),
            headers={"content-length": "abc"},
        )
        assert r.status_code == 201, r.text

    def test_upload_413_malformed_content_length_still_capped(self, client):
        """With a malformed Content-Length, the unconditional streaming cap
        still rejects an oversized chunked body → 413."""
        def _stream():
            for _ in range(12):
                yield b"not-json-" * 8192
        r = client.post(
            "/v1/packs/manifests",
            content=_stream(),
            headers={"content-length": "abc"},
        )
        assert r.status_code == 413
        assert r.json()["detail"] == "manifest request body exceeds the size cap"

    def test_upload_malformed_json_500(self, client):
        """Malformed JSON → 500 (JSONDecodeError → generic exception
        handler, detail "Internal server error") — byte-parity with the
        pre-fix request.json() path. raise_server_exceptions=False so the
        500 is returned, not re-raised (precedent: tests/test_hosted_api.py)."""
        with TestClient(app, raise_server_exceptions=False) as tc:
            r = tc.post("/v1/packs/manifests", content=b"{not json")
        assert r.status_code == 500
        assert r.json()["detail"] == "Internal server error"

    def test_upload_empty_body_500(self, client):
        """Empty body → 500 (json.loads(b"") raises JSONDecodeError), same
        as request.json() on the pre-fix path."""
        with TestClient(app, raise_server_exceptions=False) as tc:
            r = tc.post("/v1/packs/manifests", content=b"")
        assert r.status_code == 500
        assert r.json()["detail"] == "Internal server error"

    def test_upload_413_spoofed_small_content_length_still_capped(self, client):
        """A SMALL Content-Length with an oversized actual stream must still
        hit the streaming cap — the CL check is an optimization, never a
        trust boundary (RFC 7230 §3.3.3: Transfer-Encoding beats CL). Pins
        the under-claim direction of the threat model: a future "optimization"
        that skips the streaming read when CL is small would silently
        reintroduce the memory-DoS while every other test still passes."""
        def _stream():
            for _ in range(12):
                yield b"not-json-" * 8192
        r = client.post(
            "/v1/packs/manifests",
            content=_stream(),
            headers={"content-length": "100"},
        )
        assert r.status_code == 413
        assert r.json()["detail"] == "manifest request body exceeds the size cap"

    def test_upload_422_missing_or_invalid_manifest_yaml(self, client):
        """Missing-field guard sub-branches: empty dict, dict without
        manifest_yaml, empty string, non-string value, non-dict JSON — all
        422. A refactor dropping the isinstance(manifest_yaml, str) guard
        would route {"manifest_yaml": 123} to .encode() → AttributeError
        → 500."""
        payloads = [
            b"null",
            b"{}",
            b'{"foo": 1}',
            b'{"manifest_yaml": ""}',
            b'{"manifest_yaml": 123}',
            b"[1, 2, 3]",
        ]
        for payload in payloads:
            r = client.post("/v1/packs/manifests", content=payload)
            assert r.status_code == 422, (payload, r.text)
            assert "manifest_yaml" in r.json()["detail"]


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
