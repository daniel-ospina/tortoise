"""Export/import pack config carryover (#1936, epic #1891 slice 4;
test-design #1898 surfaces 8/9).

Covers:
- collect_pack_config: PackInstall records + :PackManifest YAML (custom packs
  carry yaml, starter packs null)
- Export artifact round-trip: pack_config rides INSIDE the encrypted payload
  (same integrity hash — additive v1.1, never an envelope sibling)
- _check_foreign_kinds: pre-v1.1 artifact referencing unknown pack kinds →
  loud ValueError (422 quarantine); clean artifact passes
- _apply_import_pack_config: custom manifest upsert + starter activation
  records after a restore

Docker lane (default): TORTOISE_DB_URI must be set (epic #1647 P4).
"""
from __future__ import annotations

import os

os.environ.setdefault("TORTOISE_SECRET_PEPPER", "test-static-pepper")

import pytest

from tortoise.sdk import TortoiseSDK

CUSTOM_MANIFEST = """namespace: tenant-ops
name: Tenant Operations
version: 0.1.0
tier: free
ontology:
  extends: core
  objectKinds:
  - contract
"""


@pytest.fixture
def sdk(tmp_path):
    s = TortoiseSDK(db_path=str(tmp_path / "t.db"))
    yield s
    s.close()


def _seed_packs(sdk, *, custom: bool = True):
    g = sdk._get_proj().g
    g.query(
        "MERGE (p:PackInstall {namespace: 'dev'}) "
        "SET p.version = '0.2.0', p.status = 'active', p.source = 'starter'"
    )
    if custom:
        g.query(
            "MERGE (m:PackManifest {namespace: 'tenant-ops'}) "
            "SET m.name = 'Tenant Operations', m.version = '0.1.0', "
            "    m.yaml = $yaml, m.status = 'active'",
            params={"yaml": CUSTOM_MANIFEST},
        )
        g.query(
            "MERGE (p:PackInstall {namespace: 'tenant-ops'}) "
            "SET p.version = '0.1.0', p.status = 'active', p.source = 'custom'"
        )


class TestCollectPackConfig:
    def test_collects_custom_and_starter(self, sdk):
        from tortoise.export import collect_pack_config
        _seed_packs(sdk, custom=True)
        pc = collect_pack_config(sdk._get_proj())
        by_ns = {p["namespace"]: p for p in pc["packs"]}
        assert pc["schema_version"] == 1
        assert "dev" in by_ns and by_ns["dev"]["yaml"] is None
        assert "tenant-ops" in by_ns
        assert by_ns["tenant-ops"]["yaml"] == CUSTOM_MANIFEST
        assert by_ns["tenant-ops"]["activated"] is True

    def test_read_failure_is_additive(self, tmp_path):
        """A pack-config read failure must not fail the export (additive)."""
        from tortoise.export import collect_pack_config
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection(str(tmp_path / "empty.db"))
        pc = collect_pack_config(proj)
        proj.close()
        assert pc["schema_version"] == 1
        assert isinstance(pc["packs"], list)


class TestExportRoundTrip:
    def test_pack_config_rides_inside_encrypted_payload(self, sdk):
        import json

        from tortoise.export import build_artifact, collect_pack_config, parse_artifact, verify_blob
        from tortoise.hosted_backup import decrypt_backup
        _seed_packs(sdk, custom=True)
        dump = {"node_count": 1, "nodes": [], "graph_name": "t"}
        dump["pack_config"] = collect_pack_config(sdk._get_proj())
        artifact = build_artifact(dump, key=b"k" * 32, source_surface="selfhost")
        from tortoise.export import artifact_bytes as _ab
        parsed = parse_artifact(_ab(artifact))
        blob = verify_blob(parsed)
        plain = decrypt_backup(blob, key=b"k" * 32)
        payload = json.loads(plain)
        # The inner envelope nests the dump under 'payload' — the pack_config
        # block rides inside THAT (same integrity hash as the dump).
        dump_payload = payload["payload"]
        assert "pack_config" in dump_payload, \
            "pack_config must ride INSIDE the payload"
        by_ns = {p["namespace"]: p for p in dump_payload["pack_config"]["packs"]}
        assert by_ns["tenant-ops"]["yaml"] == CUSTOM_MANIFEST


class TestForeignKindsGuard:
    def test_pre_v1_1_with_foreign_kinds_raises(self):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [{"kind": "tenant-ops:contract", "id": "x"}]}
        with pytest.raises(ValueError, match="predates pack-config"):
            _check_foreign_kinds(payload)

    def test_pre_v1_1_clean_payload_passes(self):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [{"kind": "statement", "id": "x"},
                             {"pointKind": "dev:epic", "id": "y"}]}
        _check_foreign_kinds(payload)  # dev is a starter → no raise


class TestApplyImportPackConfig:
    def test_applies_custom_manifest_and_starter(self, sdk):
        from tortoise.hosted_api import _apply_import_pack_config
        from tortoise.pack_manifest_store import get_tenant_manifests
        from tortoise.pack_state import get_tenant_packs
        payload = {"pack_config": {"schema_version": 1, "packs": [
            {"namespace": "dev", "version": "0.2.0", "activated": True, "yaml": None},
            {"namespace": "tenant-ops", "version": "0.1.0", "activated": True,
             "yaml": CUSTOM_MANIFEST},
        ]}}
        _apply_import_pack_config(sdk, payload)
        ms = get_tenant_manifests(sdk)
        assert any(m["namespace"] == "tenant-ops" for m in ms)
        packs = get_tenant_packs(sdk)
        assert any(p["namespace"] == "dev" for p in packs)
        assert any(p["namespace"] == "tenant-ops" and p["source"] == "custom"
                   for p in packs)

    def test_invalid_custom_manifest_raises(self, sdk):
        from tortoise.hosted_api import _apply_import_pack_config
        payload = {"pack_config": {"schema_version": 1, "packs": [
            {"namespace": "tenant-ops", "version": "0.1.0", "activated": True,
             "yaml": "namespace: [broken"},
        ]}}
        with pytest.raises(ValueError):
            _apply_import_pack_config(sdk, payload)

    def test_unknown_starter_reference_raises(self, sdk):
        from tortoise.hosted_api import _apply_import_pack_config
        payload = {"pack_config": {"schema_version": 1, "packs": [
            {"namespace": "not-a-real-pack", "version": "0.1.0",
             "activated": True, "yaml": None},
        ]}}
        with pytest.raises(ValueError, match="unknown starter pack"):
            _apply_import_pack_config(sdk, payload)
