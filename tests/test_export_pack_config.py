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
  records after a restore, AT the artifact's own activation state (#1936
  parity: `disabled` stays disabled; a `removed` pack is skipped per entry
  instead of aborting the whole restore)

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


# ── #2040: shared pack_config shape validator ───────────────────────────────

# (pack_config, expected-error-substring) pairs — single-violation artifacts
# where possible; check order pinned (dict → schema_version → packs → entry
# → ns → yaml). Entry-level messages interpolate the concrete index.
MALFORMED_PC_CASES = [
    # (1) non-dict pack_config
    ("x", "pack_config must be an object"),
    (5, "pack_config must be an object"),
    ([1], "pack_config must be an object"),
    # (2) schema_version missing / 2 / "1" / True / 1.0
    ({"packs": []}, "schema_version must be 1"),
    ({"schema_version": 2, "packs": []}, "schema_version must be 1"),
    ({"schema_version": "1", "packs": []}, "schema_version must be 1"),
    ({"schema_version": True, "packs": []}, "schema_version must be 1"),
    ({"schema_version": 1.0, "packs": []}, "schema_version must be 1"),
    # (3) packs missing / None
    ({"schema_version": 1}, "packs is required and must be a list"),
    ({"schema_version": 1, "packs": None}, "packs must be a list"),
    # (4) packs non-list
    ({"schema_version": 1, "packs": 42}, "packs must be a list"),
    ({"schema_version": 1, "packs": "junk"}, "packs must be a list"),
    ({"schema_version": 1, "packs": ("a",)}, "packs must be a list"),
    # (5) entry not dict
    ({"schema_version": 1, "packs": [42]}, "packs[0] must be an object"),
    ({"schema_version": 1, "packs": ["x"]}, "packs[0] must be an object"),
    ({"schema_version": 1, "packs": [None]}, "packs[0] must be an object"),
    # (6) ns absent / "" / " " / 42
    ({"schema_version": 1, "packs": [{"version": "0.1.0"}]},
     "packs[0] must declare a non-empty string namespace"),
    ({"schema_version": 1, "packs": [{"namespace": "", "version": "0.1.0"}]},
     "packs[0] must declare a non-empty string namespace"),
    ({"schema_version": 1, "packs": [{"namespace": " ", "version": "0.1.0"}]},
     "packs[0] must declare a non-empty string namespace"),
    ({"schema_version": 1, "packs": [{"namespace": 42, "version": "0.1.0"}]},
     "packs[0] must declare a non-empty string namespace"),
    # (7) yaml empty-string case
    ({"schema_version": 1, "packs": [{"namespace": "tenant-ops", "yaml": ""}]},
     "yaml must be a non-empty string or null"),
    # (7) yaml whitespace-only → same empty-str message (pre-restore shape
    # error, consistent with the ns `" "` handling — a guaranteed-fail
    # artifact must not 422 post-swap with a misleading reason)
    ({"schema_version": 1, "packs": [{"namespace": "tenant-ops", "yaml": " "}]},
     "yaml must be a non-empty string or null"),
    # (7) yaml non-str (typed) case
    ({"schema_version": 1, "packs": [{"namespace": "tenant-ops", "yaml": 0}]},
     "yaml must be a non-empty string or null (got int)"),
    ({"schema_version": 1, "packs": [{"namespace": "tenant-ops", "yaml": False}]},
     "yaml must be a non-empty string or null (got bool)"),
    ({"schema_version": 1, "packs": [{"namespace": "tenant-ops", "yaml": []}]},
     "yaml must be a non-empty string or null (got list)"),
    ({"schema_version": 1, "packs": [{"namespace": "tenant-ops", "yaml": {}}]},
     "yaml must be a non-empty string or null (got dict)"),
    ({"schema_version": 1, "packs": [{"namespace": "tenant-ops", "yaml": 42}]},
     "yaml must be a non-empty string or null (got int)"),
]

# Legal families: absent (payload.get → None), explicit None, empty packs,
# well-formed incl. int `version`, `activated: "anything"`, unknown keys,
# and yaml absent/None — all must return None (no error).
LEGAL_PC_CASES = [
    None,
    {"schema_version": 1, "packs": []},
    {"schema_version": 1, "packs": [
        {"namespace": "tenant-ops", "version": 1, "activated": "anything",
         "yaml": CUSTOM_MANIFEST, "future_key": "unknown"}]},
    {"schema_version": 1, "packs": [
        {"namespace": "dev", "version": "0.2.0", "activated": True,
         "yaml": None}]},
    {"schema_version": 1, "packs": [
        {"namespace": "tenant-ops", "version": "0.1.0", "activated": True}]},
]


class TestPackConfigShapeGate:
    """#2040: shared pack_config legality matrix — single source of truth
    used by the envelope gate (422 pre-restore) AND the apply defense
    (ValueError), so the matrix cannot drift between the two sites."""

    @pytest.mark.parametrize("pc,substr",
                             MALFORMED_PC_CASES,
                             ids=[f"case{i}" for i in range(len(MALFORMED_PC_CASES))])
    def test_malformed_pack_config_returns_error(self, pc, substr):
        from tortoise.hosted_api import _pack_config_shape_error
        err = _pack_config_shape_error(pc)
        assert err is not None
        assert "pack_config" in err
        assert substr in err

    @pytest.mark.parametrize("pc",
                             LEGAL_PC_CASES,
                             ids=[f"legal{i}" for i in range(len(LEGAL_PC_CASES))])
    def test_legal_pack_config_returns_none(self, pc):
        from tortoise.hosted_api import _pack_config_shape_error
        assert _pack_config_shape_error(pc) is None


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

    # ── #2040 Task 3: single-validator drift-lock (shared shape gate) ──

    @pytest.mark.parametrize("pc,substr",
                             MALFORMED_PC_CASES,
                             ids=[f"case{i}" for i in range(len(MALFORMED_PC_CASES))])
    def test_malformed_pack_config_raises_value_error(self, sdk, pc, substr):
        """Every malformed shape raises ValueError through
        ``_apply_import_pack_config`` with the SHAPE error message (pinned via
        ``match=``) — NOT a bare ValueError (several falsy-yaml / non-str-ns
        cases already raise "unknown starter pack" today via the starter
        branch, so a bare raises() would pass vacuously pre-implementation)."""
        import re

        from tortoise.hosted_api import _apply_import_pack_config
        payload = {"pack_config": pc}
        with pytest.raises(ValueError, match=re.escape(substr)):
            _apply_import_pack_config(sdk, payload)

    def test_ns_yaml_mismatch_raises_pre_write(self, sdk):
        """Declared pack namespace disagrees with the manifest's yaml
        namespace → ValueError naming both namespaces BEFORE any write — no
        PackManifest/PackInstall residue (closes the crafted-artifact
        silent-200 class)."""
        from tortoise.hosted_api import _apply_import_pack_config
        from tortoise.pack_manifest_store import get_tenant_manifests
        from tortoise.pack_state import get_tenant_packs
        mismatched = CUSTOM_MANIFEST.replace("tenant-ops", "other-ns")
        payload = {"pack_config": {"schema_version": 1, "packs": [
            {"namespace": "tenant-ops", "version": "0.1.0", "activated": True,
             "yaml": mismatched},
        ]}}
        with pytest.raises(ValueError, match="does not match manifest namespace"):
            _apply_import_pack_config(sdk, payload)
        assert get_tenant_manifests(sdk) == []  # no PackManifest residue
        assert not any(p["namespace"] == "tenant-ops"
                       for p in get_tenant_packs(sdk))  # no PackInstall residue

    def test_ns_yaml_match_applies(self, sdk):
        """A ns↔yaml-consistent artifact still applies (guard does not trip
        the legitimate path) — regression lock for Task 5's success fixture."""
        from tortoise.hosted_api import _apply_import_pack_config
        from tortoise.pack_manifest_store import get_tenant_manifests
        payload = {"pack_config": {"schema_version": 1, "packs": [
            {"namespace": "tenant-ops", "version": "0.1.0", "activated": True,
             "yaml": CUSTOM_MANIFEST},
        ]}}
        _apply_import_pack_config(sdk, payload)
        assert any(m["namespace"] == "tenant-ops" for m in get_tenant_manifests(sdk))


# ── #1936: activation parity on restore (indicator 2) + removed-pack
#    granularity (indicator 3). The export `activated` boolean was written but
#    never read, so every import force-set `status='active'` — a pack the
#    tenant had switched off came back ON (a silent widening of the tenant's
#    vocabulary, the same class #8648 fixed on the seed side). And a pack the
#    tenant had REMOVED exported as `yaml: null`, fell through to the starter
#    branch and raised `unknown starter pack` — one uninstalled custom pack
#    aborted the whole import (post-swap: the dump had landed, the pack config
#    never applied), at the wrong granularity.

def _install_status(sdk, ns):
    """The graph's raw ``:PackInstall`` status for ``ns`` (None = no record)."""
    rows = sdk._get_proj().g.query(
        "MATCH (p:PackInstall {namespace: $ns}) RETURN p.status",
        params={"ns": ns},
    ).result_set
    return rows[0][0] if rows else None


def _pack_entry(**kw):
    entry = {"namespace": "dev", "version": "0.2.0", "activated": True,
             "yaml": None}
    entry.update(kw)
    return {"pack_config": {"schema_version": 1, "packs": [entry]}}


class TestActivationParity:
    def test_collect_exports_status(self, sdk):
        """The artifact must carry the FULL status, not just the boolean —
        `activated: False` alone cannot tell `disabled` from `removed`."""
        from tortoise.export import collect_pack_config
        _seed_packs(sdk, custom=True)
        g = sdk._get_proj().g
        g.query("MATCH (p:PackInstall {namespace: 'dev'}) "
                "SET p.status = 'disabled'")
        g.query("MERGE (p:PackInstall {namespace: 'gone-ops'}) "
                "SET p.status = 'removed', p.source = 'custom'")
        by_ns = {p["namespace"]: p
                 for p in collect_pack_config(sdk._get_proj())["packs"]}
        assert by_ns["dev"]["status"] == "disabled"
        assert by_ns["dev"]["activated"] is False
        assert by_ns["gone-ops"]["status"] == "removed"
        assert by_ns["tenant-ops"]["status"] == "active"

    def test_collect_clamps_an_out_of_vocabulary_status(self, sdk):
        """Export → import must be TOTAL.

        The exporter used to write ``:PackInstall.status`` verbatim while the
        importer's shape gate accepts only the three vocabulary values, so a
        graph holding any other value produced an artifact its OWN importer
        rejected pre-restore — and an artifact is sha-pinned and usually the
        only surviving copy of a graph that is gone. No in-product writer can
        create such a status, so this pins the residual against an out-of-band
        graph write: clamp, never emit an unimportable value.
        """
        from tortoise.export import collect_pack_config
        from tortoise.hosted_api import _pack_config_shape_error
        _seed_packs(sdk, custom=True)
        sdk._get_proj().g.query(
            "MATCH (p:PackInstall {namespace: 'tenant-ops'}) "
            "SET p.status = 'inactive'")
        pc = collect_pack_config(sdk._get_proj())
        by_ns = {p["namespace"]: p for p in pc["packs"]}
        # clamped, and never UP: `inactive` must not become `active`
        assert by_ns["tenant-ops"]["status"] == "disabled"
        assert by_ns["tenant-ops"]["activated"] is False
        # the artifact the exporter just produced must pass its own gate
        assert _pack_config_shape_error(pc) is None

    def test_disabled_starter_is_not_reactivated(self, sdk):
        """Explicit `status` path (the v1.1 export's own field)."""
        from tortoise.hosted_api import _apply_import_pack_config
        _apply_import_pack_config(sdk, _pack_entry(namespace="dev",
                                                   activated=False,
                                                   status="disabled"))
        assert _install_status(sdk, "dev") == "disabled"

    def test_disabled_custom_pack_is_not_reactivated(self, sdk):
        """The custom branch hard-coded `status='active'` in
        upsert_tenant_manifest — a disabled custom pack came back ON."""
        from tortoise.hosted_api import _apply_import_pack_config
        from tortoise.pack_manifest_store import get_tenant_manifests
        _apply_import_pack_config(sdk, _pack_entry(
            namespace="tenant-ops", activated=False, status="disabled",
            yaml=CUSTOM_MANIFEST))
        assert _install_status(sdk, "tenant-ops") == "disabled"
        # the manifest itself is restored (installed, just not activated)
        assert any(m["namespace"] == "tenant-ops" for m in get_tenant_manifests(sdk))

    def test_active_stays_active(self, sdk):
        from tortoise.hosted_api import _apply_import_pack_config
        _apply_import_pack_config(sdk, _pack_entry(namespace="dev"))
        assert _install_status(sdk, "dev") == "active"

    @pytest.mark.parametrize("activated", ["anything", 0, 1, "false", []])
    def test_non_false_activated_is_not_read_as_disabled(self, sdk, activated):
        """The shape gate leaves `activated` unconstrained (it is documented
        legal), so only a literal ``False`` may mean "switched off" — a
        truthiness read would disable packs on a hand-built artifact."""
        from tortoise.hosted_api import _apply_import_pack_config
        _apply_import_pack_config(sdk, _pack_entry(namespace="dev",
                                                   activated=activated))
        assert _install_status(sdk, "dev") == "active"

    def test_legacy_artifact_without_status_honours_activated(self, sdk):
        """FALLBACK path: an artifact written before `status` existed carries
        no `status` key at all, and its `activated` still expresses intent.
        (Kept distinct from the explicit-status test above — the two paths are
        different code.)"""
        from tortoise.hosted_api import _apply_import_pack_config
        # Premise, not an assertion: `_pack_entry` sets no `status`, so this is
        # the pre-`status` shape the fallback exists for. Asserting it would be
        # dead weight (agent-infra#3442 rule 3) — no mutation of the diff can
        # change a test-local helper's output.
        entry = _pack_entry(namespace="dev", activated=False)
        _apply_import_pack_config(sdk, entry)
        assert _install_status(sdk, "dev") == "disabled"

    def test_round_trip_preserves_activation(self, sdk, tmp_path):
        """Export → import into a FRESH graph reproduces the activation state
        (indicator 2: structure parity incl. activation)."""
        from tortoise.export import collect_pack_config
        from tortoise.hosted_api import _apply_import_pack_config
        _seed_packs(sdk, custom=True)
        sdk._get_proj().g.query(
            "MATCH (p:PackInstall {namespace: 'tenant-ops'}) "
            "SET p.status = 'disabled'")
        pc = collect_pack_config(sdk._get_proj())
        # The new field must actually be CARRIED — without this, the legacy
        # `activated` fallback alone satisfies every assertion below and the
        # test cannot discriminate the `status` half of the change.
        by_ns = {e["namespace"]: e for e in pc["packs"]}
        assert by_ns["tenant-ops"]["status"] == "disabled"
        assert by_ns["dev"]["status"] == "active"

        fresh = TortoiseSDK(db_path=str(tmp_path / "restored.db"))
        try:
            _apply_import_pack_config(fresh, {"pack_config": pc})
            assert _install_status(fresh, "dev") == "active"
            assert _install_status(fresh, "tenant-ops") == "disabled"
        finally:
            fresh.close()


class TestRemovedPackGranularity:
    """A removed pack is a per-entry SKIP, never an aborted restore."""

    def test_removed_custom_pack_does_not_abort_restore(self, sdk):
        from tortoise.hosted_api import _apply_import_pack_config
        payload = {"pack_config": {"schema_version": 1, "packs": [
            {"namespace": "gone-ops", "version": "0.1.0", "activated": False,
             "status": "removed", "yaml": None},
            {"namespace": "dev", "version": "0.2.0", "activated": True,
             "status": "active", "yaml": None},
        ]}}
        # pre-#1936 this raised `unknown starter pack 'gone-ops'`
        _apply_import_pack_config(sdk, payload)
        assert _install_status(sdk, "dev") == "active"
        # and the healthy entry really did apply (no abort short-circuit)
        assert _install_status(sdk, "gone-ops") is None

    def test_removed_pack_writes_no_record_of_its_own(self, sdk):
        """This WRITE PATH creates no record for a removed pack — it does not
        claim the end state of a real restore has none.

        ⛔ Corrected in review: `:PackInstall`/`:PackManifest`/`:PackSelection`
        are CONTENT nodes (`_EXPORT_SKIP_LABELS` excludes only the three Meta
        labels), so the dump carries the tombstone and `restore_graph` CREATEs
        it BEFORE this pass runs. A real import therefore ends with
        `status='removed'` present — parity with the source graph. What the
        skip guarantees is narrower: this pass neither overwrites the restored
        state nor aborts. `test_removed_pack_preserves_restored_record` models
        that real end state."""
        from tortoise.hosted_api import _apply_import_pack_config
        _apply_import_pack_config(sdk, _pack_entry(
            namespace="gone-ops", activated=False, status="removed"))
        assert _install_status(sdk, "gone-ops") is None

    def test_removed_pack_preserves_restored_record(self, sdk):
        """The REAL restore end state: the dump already restored a
        `status='removed'` record, and the apply pass must leave it exactly as
        the swap wrote it (parity) instead of reactivating or erasing it."""
        from tortoise.hosted_api import _apply_import_pack_config
        g = sdk._get_proj().g
        # what restore_graph's CREATE leaves on the swapped graph
        g.query("MERGE (p:PackInstall {namespace: 'gone-ops'}) "
                "SET p.version = '0.1.0', p.status = 'removed', "
                "    p.source = 'custom'")
        _apply_import_pack_config(sdk, _pack_entry(
            namespace="gone-ops", activated=False, status="removed"))
        assert _install_status(sdk, "gone-ops") == "removed"

    def test_removed_starter_pack_also_skipped(self, sdk):
        from tortoise.hosted_api import _apply_import_pack_config
        _apply_import_pack_config(sdk, _pack_entry(namespace="dev", activated=False,
                                                  status="removed"))
        assert _install_status(sdk, "dev") is None

    def test_genuinely_unknown_namespace_still_raises(self, sdk):
        """The fail-loud guard must NOT be weakened into a silent skip: a
        namespace that is neither removed nor a known starter is still an
        error (the vocabulary would be silently dropped)."""
        from tortoise.hosted_api import _apply_import_pack_config
        with pytest.raises(ValueError, match="unknown starter pack"):
            _apply_import_pack_config(
                sdk, _pack_entry(namespace="not-a-real-pack", yaml=None))

    def test_legacy_removed_pack_skipped_not_aborted(self, sdk):
        """A PRE-`status` artifact skips a removed pack instead of aborting.

        ``delete_tenant_manifest`` drops the :PackManifest, so an export taken
        before the ``status`` field existed records a removed custom pack as
        ``activated: False`` + ``yaml: null`` with no ``status`` key —
        byte-identical to a disabled one. Before this test such an artifact hit
        the starter branch and raised ``unknown starter pack``, aborting the
        WHOLE restore: indicator 3 of #1936, still live for the artifacts that
        predate the fix. The artifact is usually the only surviving carrier of
        the source graph and is sha-authenticated, so "aborts forever" is not
        recoverable.
        """
        from tortoise.hosted_api import _apply_import_pack_config
        payload = {"pack_config": {"schema_version": 1, "packs": [
            # removed custom pack: manifest gone, no `status` (legacy export)
            {"namespace": "gone-ops", "version": "0.1.0",
             "activated": False, "yaml": None},
            # a known starter the tenant had disabled, same legacy shape
            {"namespace": "dev", "version": "0.2.0",
             "activated": False, "yaml": None},
            # ⛔ the SAME artifact shape with the null SPELLED: the shape gate
            # blesses `status: null` as "predates the field" and
            # `_pack_entry_activation` reads it as absent, so this entry must
            # behave identically. Keying the skip on key PRESENCE let these two
            # spellings of one artifact diverge (the key-absent one skipped, the
            # `status: null` one still aborting) — a review finding, pinned here.
            {"namespace": "gone-ops-null", "version": "0.1.0",
             "activated": False, "yaml": None, "status": None},
        ]}}
        _apply_import_pack_config(sdk, payload)
        # the removed entry is skipped: this pass neither creates nor erases it
        assert _install_status(sdk, "gone-ops") is None
        assert _install_status(sdk, "gone-ops-null") is None
        # the disabled starter is still carried as `disabled`, not reactivated
        assert _install_status(sdk, "dev") == "disabled"

    def test_legacy_skip_does_not_fire_on_explicit_status(self, sdk):
        """A namespace the artifact wants INSTALLED keeps the loud guard.

        Pins that the skip is keyed on the RESOLVED state (``disabled``) and
        cannot swallow an entry whose explicit status says it should be
        installed — ``active`` on an unknown namespace is still an error (the
        vocabulary would be dropped). ``disabled`` is deliberately NOT here:
        it resolves to the same state as the legacy ``activated: False``
        spelling and is skipped identically — see
        ``test_explicit_disabled_on_unknown_namespace_is_skipped``.
        """
        from tortoise.hosted_api import _apply_import_pack_config
        with pytest.raises(ValueError, match="unknown starter pack"):
            _apply_import_pack_config(
                sdk, _pack_entry(namespace="not-a-real-pack", yaml=None,
                                 activated=False, status="active"))

    def test_explicit_disabled_on_unknown_namespace_is_skipped(self, sdk):
        """The SAME state spelled explicitly is skipped, not fatal.

        ``_pack_entry_activation`` resolves both ``{"activated": False}`` and
        ``{"activated": False, "status": "disabled"}`` to ``disabled``, so the
        abort class #1936 exists to remove (one entry fails the whole import)
        must not survive behind a spelling difference. The exporter can emit
        this shape: a custom ``:PackInstall`` whose ``:PackManifest`` is
        absent yields ``yaml: null`` regardless of status.
        """
        from tortoise.hosted_api import _apply_import_pack_config
        _apply_import_pack_config(
            sdk, _pack_entry(namespace="typo-ops", yaml=None,
                             activated=False, status="disabled"))
        assert _install_status(sdk, "typo-ops") is None

    def test_legacy_unknown_namespace_activated_still_raises(self, sdk):
        """Same legacy shape but `activated: True` — the guard still fires.

        Pins the boundary of the skip above: it must key on `activated is
        False`, so an unknown namespace the artifact wanted ACTIVE is still a
        loud error rather than a silent drop."""
        from tortoise.hosted_api import _apply_import_pack_config
        with pytest.raises(ValueError, match="unknown starter pack"):
            _apply_import_pack_config(
                sdk, _pack_entry(namespace="not-a-real-pack", yaml=None,
                                 activated=True))


class TestStatusShapeGate:
    """`status` is written verbatim onto :PackInstall, so it is constrained
    by the SHARED shape gate — pre-restore, live graph untouched (#2040)."""

    def test_crafted_status_rejected_pre_restore(self):
        from tortoise.hosted_api import _pack_config_shape_error
        err = _pack_config_shape_error(
            {"schema_version": 1,
             "packs": [{"namespace": "dev", "status": "bogus"}]})
        assert err is not None and "status must be one of" in err

    @pytest.mark.parametrize("bad", [{}, [], [1], {"a": 1}, 1, 0, True])
    def test_non_string_status_is_an_error_string_not_a_crash(self, bad):
        """⛔ `x not in frozenset` HASHES the operand, so a JSON object/array
        status raised TypeError — which the import route does not catch → 500
        with no quarantine audit and no ledger stamp, the exact outcome this
        pre-restore gate exists to prevent (review finding F1, P1). The check
        must be type-first, like its `schema_version`/`yaml` siblings."""
        from tortoise.hosted_api import _pack_config_shape_error
        err = _pack_config_shape_error(
            {"schema_version": 1,
             "packs": [{"namespace": "dev", "status": bad}]})
        assert err is not None and "status must be one of" in err

    @pytest.mark.parametrize("bad", [{}, [], 1])
    def test_entry_activation_guard_never_raises_typeerror(self, bad):
        """The defence-in-depth guard's contract is ValueError, never a
        TypeError that a caller's `except ValueError` would miss."""
        from tortoise.hosted_api import _pack_entry_activation
        with pytest.raises(ValueError, match="invalid status"):
            _pack_entry_activation({"namespace": "dev", "status": bad}, "dev")

    def test_apply_path_fails_closed_on_direct_call(self, sdk):
        """A direct caller bypassing the envelope gate cannot write an
        out-of-vocabulary status either — the SHARED shape gate runs first
        (single source of truth), so the message is the shape message."""
        from tortoise.hosted_api import _apply_import_pack_config
        with pytest.raises(ValueError, match="status must be one of"):
            _apply_import_pack_config(sdk, _pack_entry(namespace="dev",
                                                       status="bogus"))
        assert _install_status(sdk, "dev") is None  # zero residue

    def test_entry_activation_guard_is_defence_in_depth(self):
        """The per-entry resolver carries its OWN check for a caller that
        never ran the shape gate — it must fail closed, not write."""
        from tortoise.hosted_api import _pack_entry_activation
        with pytest.raises(ValueError, match="invalid status"):
            _pack_entry_activation({"namespace": "dev", "status": "bogus"},
                                   "dev")

    @pytest.mark.parametrize("status", ["active", "disabled", "removed", None])
    def test_vocabulary_is_legal(self, status):
        from tortoise.hosted_api import _pack_config_shape_error
        entry = {"namespace": "dev"}
        if status is not None:
            entry["status"] = status
        assert _pack_config_shape_error(
            {"schema_version": 1, "packs": [entry]}) is None

    def test_vocabulary_matches_pack_state(self):
        """Drift lock: the shape gate's set must be EXACTLY pack_state's
        PackInstall vocabulary, derived from the module.

        The expectation is computed from ``pack_state``'s own constants rather
        than written out again: a hand-written literal set here compares the
        three already-imported names against themselves, so it locks the
        three VALUES and cannot detect an ADDITION — the very drift this test
        exists to catch (a new ``PACK_STATUS_*`` added there without the gate
        would 422 a legitimately-exported artifact).
        """
        from tortoise import pack_state
        from tortoise.hosted_api import _PACK_CONFIG_STATUSES
        declared = {
            value for name, value in vars(pack_state).items()
            if name.startswith("PACK_STATUS_") and isinstance(value, str)
        }
        assert declared, "pack_state declares no PACK_STATUS_* constants"
        assert declared == set(_PACK_CONFIG_STATUSES)


class TestUpsertStatusParam:
    def test_default_stays_active(self, sdk):
        """Upload/provision routes pass no status — unchanged behaviour."""
        from tortoise.pack_manifest_store import (
            get_tenant_manifests,
            upsert_tenant_manifest,
        )
        rec = upsert_tenant_manifest(sdk, CUSTOM_MANIFEST)
        assert rec["status"] == "active"
        assert _install_status(sdk, "tenant-ops") == "active"
        assert any(m["namespace"] == "tenant-ops" for m in get_tenant_manifests(sdk))

    def test_removed_is_not_a_settable_status(self, sdk):
        """`removed` is a tombstone expressed by omission, never a status a
        writer accepts (mirrors set_graph_packs' vocabulary)."""
        from tortoise.pack_manifest_store import upsert_tenant_manifest
        with pytest.raises(ValueError, match="invalid pack status"):
            upsert_tenant_manifest(sdk, CUSTOM_MANIFEST, status="removed")
        assert _install_status(sdk, "tenant-ops") is None
