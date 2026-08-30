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
        """Real dump shape: kinds live inside node["props"] (#2028)."""
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [{"dump_id": 1, "labels": ["Point"],
                              "props": {"objectKind": "tenant-ops:contract"}}]}
        with pytest.raises(ValueError, match="predates pack-config"):
            _check_foreign_kinds(payload)

    def test_pre_v1_1_clean_payload_passes(self):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [
            {"dump_id": 1, "labels": ["Point"], "props": {"pointKind": "statement"}},
            {"dump_id": 2, "labels": ["Event"], "props": {"eventKind": "dev:review"}},
            {"dump_id": 3, "labels": ["Point"], "props": {"kind": "core:meeting"}},
            {"dump_id": 4, "labels": ["Point"]},  # absent props — robustness
        ]}
        _check_foreign_kinds(payload)  # dev is a starter, core excluded, bare passes

    @pytest.mark.parametrize("key", ["pointKind", "objectKind", "eventKind",
                                     "documentKind", "subjectKind", "sourceKind",
                                     "actionKind", "kind"])
    def test_extended_keys_foreign_raises(self, key):
        """Every kind-carrying prop key must catch a foreign namespace (#2028)."""
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [{"dump_id": 1, "labels": ["X"],
                              "props": {key: "tenant-ops:thing"}}]}
        with pytest.raises(ValueError, match="predates pack-config"):
            _check_foreign_kinds(payload)

    @pytest.mark.parametrize("key,value", [
        ("pointKind", "statement"), ("objectKind", "dev:epic"),
        ("eventKind", "pm:cardCreated"), ("kind", "core:meeting")])
    def test_extended_keys_clean_passes(self, key, value):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [{"dump_id": 1, "labels": ["X"],
                              "props": {key: value}}]}
        _check_foreign_kinds(payload)

    def test_non_string_kind_value_passes(self):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [{"dump_id": 1, "labels": ["X"],
                              "props": {"pointKind": 42}}]}
        _check_foreign_kinds(payload)  # isinstance guard — no crash

    def test_non_dict_props_and_nodes_skipped(self):
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [
            "junk",
            {"dump_id": 2, "labels": ["X"], "props": []},
            # PackManifest-labeled node with non-dict props must not
            # AttributeError in the absorption loop either
            {"dump_id": 3, "labels": ["PackManifest"], "props": []},
        ]}
        _check_foreign_kinds(payload)  # no AttributeError → 500

    def test_many_foreign_kinds_lists_only_five(self):
        from tortoise.hosted_api import _check_foreign_kinds
        foreign = [f"ns{i}:kind{j}" for i in range(6) for j in range(2)]
        payload = {"nodes": [{"dump_id": i, "labels": ["X"], "props": {"objectKind": k}}
                             for i, k in enumerate(foreign)]}
        with pytest.raises(ValueError) as ei:
            _check_foreign_kinds(payload)
        # message lists exactly 5 (sorted deduped/truncated kinds [:5])
        assert "ns0:kind0" in str(ei.value) and "ns5:kind1" not in str(ei.value)

    def test_self_contained_packmanifest_passes(self):
        """A dump carrying its OWN PackManifest restores its vocabulary —
        the namespace is self-contained and must not be rejected (#2028)."""
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [
            {"dump_id": 1, "labels": ["PackManifest"],
             "props": {"namespace": "tenant-ops", "yaml": "namespace: tenant-ops"}},
            {"dump_id": 2, "labels": ["Object"],
             "props": {"objectKind": "tenant-ops:contract"}},
        ]}
        _check_foreign_kinds(payload)

    def test_packmanifest_mismatch_still_raises(self):
        """A manifest for a DIFFERENT namespace does not mask the foreign
        kind — absorption is namespace-keyed (#2028)."""
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"nodes": [
            {"dump_id": 1, "labels": ["PackManifest"],
             "props": {"namespace": "other-ns"}},
            {"dump_id": 2, "labels": ["Object"],
             "props": {"objectKind": "tenant-ops:contract"}},
        ]}
        with pytest.raises(ValueError, match="predates pack-config"):
            _check_foreign_kinds(payload)

    def test_declared_pack_namespaces_absorbed(self):
        """v1.1 artifact: namespaces declared in pack_config are legitimate
        (manifest upsert establishes them post-swap) → pass (#2028)."""
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"pack_config": {"schema_version": 1, "packs": [
            {"namespace": "tenant-ops", "version": "0.1.0",
             "activated": True, "yaml": CUSTOM_MANIFEST}]},
            "nodes": [{"dump_id": 1, "labels": ["Object"],
                        "props": {"objectKind": "tenant-ops:contract"}}]}
        _check_foreign_kinds(payload)

    def test_partial_packs_do_not_mask_foreign_kinds(self):
        """Declared packs for namespace A do NOT legitimize kinds from
        undeclared namespace B — same silent-drop class as pre-v1.1
        (#2028, follow-up #2039)."""
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"pack_config": {"schema_version": 1, "packs": [
            {"namespace": "tenant-ops", "version": "0.1.0",
             "activated": True, "yaml": CUSTOM_MANIFEST}]},
            "nodes": [{"dump_id": 1, "labels": ["Object"],
                        "props": {"objectKind": "ghost:poltergeist"}}]}
        with pytest.raises(ValueError, match="does not cover"):
            _check_foreign_kinds(payload)

    def test_malformed_packs_list_gets_accurate_reason(self):
        """pack_config present with an unusable `packs` value → the guard
        still rejects foreign kinds without crashing, labeled as declaring no
        usable packs (not mislabeled pre-v1.1) (#2028 code-review)."""
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"pack_config": {"schema_version": 1, "packs": "junk"},
                    "nodes": [{"dump_id": 1, "labels": ["Object"],
                                "props": {"objectKind": "tenant-ops:contract"}}]}
        with pytest.raises(ValueError, match="declares no packs"):
            _check_foreign_kinds(payload)

    def test_non_iterable_packs_value_no_crash(self):
        """Truthy non-iterable `packs` (e.g. 5) must not TypeError → 500 —
        the absorption loop guards the container shape (#2028 code-review)."""
        from tortoise.hosted_api import _check_foreign_kinds
        payload = {"pack_config": {"schema_version": 1, "packs": 5},
                    "nodes": [{"dump_id": 1, "labels": ["Object"],
                                "props": {"objectKind": "tenant-ops:contract"}}]}
        with pytest.raises(ValueError, match="declares no packs"):
            _check_foreign_kinds(payload)

    def test_real_dump_graph_foreign_kind_raises(self, sdk):
        """Guard against ACTUAL dump_graph output — the exact shape the bug
        shipped against (#2028 Indicator 3).

        No _seed_packs here: a genuine pre-v1.1 artifact predates PackManifest
        storage (#1935) so its dump carries NO manifest node — tenant-ops is
        foreign to both the shared catalog and the dump itself → must raise.
        (A dump that DOES carry its own PackManifest is self-contained and
        covered by test_self_contained_packmanifest_passes above — seeding one
        here would mask the foreign kind and wrongly pass.)"""
        from tortoise.hosted_api import _check_foreign_kinds
        from tortoise.hosted_backup import dump_graph
        sdk.create_object("Contract 1", objectKind="tenant-ops:contract")
        dump = dump_graph(sdk._get_proj().g, graph_name="t")
        dump.pop("pack_config", None)  # pre-v1.1 artifact (no pack_config)
        with pytest.raises(ValueError, match="predates pack-config"):
            _check_foreign_kinds(dump)

    def test_real_dump_graph_clean_graph_passes(self, sdk):
        from tortoise.hosted_api import _check_foreign_kinds
        from tortoise.hosted_backup import dump_graph
        sdk.create_point("statement", content="A claim")
        dump = dump_graph(sdk._get_proj().g, graph_name="t")
        dump.pop("pack_config", None)
        _check_foreign_kinds(dump)

    def test_real_dump_graph_event_kind_foreign_raises(self, sdk):
        """Real-dump coverage for a second writer key (eventKind) — locks
        the props scan against actual dump output for non-point kinds
        (#2028, code-review #2041)."""
        from tortoise.hosted_api import _check_foreign_kinds
        from tortoise.hosted_backup import dump_graph
        sdk.create_event("Contract signed", eventKind="tenant-ops:contract_signed")
        dump = dump_graph(sdk._get_proj().g, graph_name="t")
        dump.pop("pack_config", None)
        with pytest.raises(ValueError, match="predates pack-config"):
            _check_foreign_kinds(dump)


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
