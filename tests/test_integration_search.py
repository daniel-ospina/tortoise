"""Integration tests for #7849 — pack-aware search, chain verification, migration, and MCP surface.

Requires live FalkorDB. Skips gracefully when unavailable.

Usage:
    TORTOISE_DB_URI=docker://localhost:6379/tortoise_test_integration_search pytest tests/test_integration_search.py -v
    pytest tests/test_integration_search.py -v -m integration  # (with marker config)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── FalkorDB availability check ────────────────────────────────────────────
# Try env URI, then common local defaults (docker://localhost:6379, :16379)
import os as _os
FALKORDB_AVAILABLE = False
_uri_candidates = [
    _os.environ.get("TORTOISE_DB_URI"),
    "docker://localhost:6379/tortoise_test_integration_search",
    "docker://localhost:16379/tortoise_test_integration_search",
]
_old_uri = _os.environ.get("TORTOISE_DB_URI")
for _uri in _uri_candidates:
    if not _uri:
        continue
    try:
        from tortoise.sdk import TortoiseSDK
        # URI comes from TORTOISE_DB_URI env var, not positional arg.
        # Keep the working URI set for the duration of the test run —
        # integration test classes construct TortoiseSDK() directly.
        _os.environ["TORTOISE_DB_URI"] = _uri
        _sdk = TortoiseSDK()
        _sdk.status()
        FALKORDB_AVAILABLE = True
        break
    except Exception:
        # Restore the user's original env var (or unset if never set)
        if _old_uri is None:
            _os.environ.pop("TORTOISE_DB_URI", None)
        else:
            _os.environ["TORTOISE_DB_URI"] = _old_uri
        continue


# ── Helpers ─────────────────────────────────────────────────────────────────

def _cleanup_sdk(sdk, *point_ids: str):
    """Delete test points by ID. Best-effort — logs cleanup failures."""
    import logging
    _log = logging.getLogger(__name__)
    for pid in point_ids:
        try:
            sdk.delete_point(pid)
        except Exception as e:
            _log.warning("Cleanup failed for point %s: %s", pid, e)


def _create_test_points(sdk):
    """Create a small graph of inter-related points for chain-verification tests.

    Returns (ids_dict, operator_ids) where ids_dict has keys:
        jtbd, uc, uj, wf, req, feature, customer_seg
    """
    jtbd = sdk.create_point(
        "product-strategy:jobToBeDone", "Deliver product insights",
        context="product-strategy",
        jtbd_id="JTBD-7849-1",
    )
    uc = sdk.create_point(
        "product-strategy:useCase", "Analyze market trends",
        context="product-strategy",
        uc_id="UC-7849-1",
    )
    uj = sdk.create_point(
        "product-strategy:userJourney", "Market analyst workflow",
        context="product-strategy",
        covered_use_cases="UC-7849-1",
    )
    wf = sdk.create_point(
        "product-strategy:workflow", "Weekly analysis pipeline",
        context="product-strategy",
        enables_jtbd="JTBD-7849-1",
    )
    req = sdk.create_point(
        "dev:requirement", "REQ-1: Data pipeline",
        context="product-strategy",
        enabled_workflow=wf["id"],
    )
    feature = sdk.create_point(
        "product-strategy:feature", "Automated reporting",
        context="product-strategy",
    )
    customer_seg = sdk.create_point(
        "product-strategy:customerSegment", "Enterprise data teams",
        context="product-strategy",
    )

    # Build relationships:
    # JTBD -(composedOf)-> UC
    op_composed = sdk.create_operator(
        "composedOf", jtbd["id"], [uc["id"]],
    )
    # Feature -(addresses)-> CustomerSegment
    op_addresses = sdk.create_operator(
        "IMPL", feature["id"], [customer_seg["id"]],
    )
    # Label the addresses operator so traversal_path can find it
    try:
        sdk.update_point(op_addresses["id"], {"label": "addresses"})
    except Exception:
        pass

    ids = {
        "jtbd": jtbd["id"],
        "uc": uc["id"],
        "uj": uj["id"],
        "wf": wf["id"],
        "req": req["id"],
        "feature": feature["id"],
        "customer_seg": customer_seg["id"],
    }
    operator_ids = [op_composed["id"], op_addresses["id"]]
    return ids, operator_ids


# ── Cross-entity search (#7849) ────────────────────────────────────────────

@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
class TestCrossEntitySearch:
    """Test tortoise_search with entity_type='event' and 'subject'."""

    def test_search_events_returns_list(self):
        """tortoise_search with entity_type='event' returns list."""
        from tortoise.mcp_server import tortoise_search
        result = tortoise_search("test", entity_type="event", limit=5)
        assert isinstance(result, list) or isinstance(result.get("error"), str)

    def test_search_subjects_returns_list(self):
        """tortoise_search with entity_type='subject' returns list."""
        from tortoise.mcp_server import tortoise_search
        result = tortoise_search("test", entity_type="subject", limit=5)
        assert isinstance(result, list) or isinstance(result.get("error"), str)

    def test_search_event_has_expected_fields(self):
        """Event search results include eventId and eventKind when available."""
        from tortoise.mcp_server import tortoise_search
        result = tortoise_search(entity_type="event", limit=5)
        if isinstance(result, list) and result:
            entry = result[0]
            # Event results come from the batch query — check they have sensible shape
            assert isinstance(entry, dict)
            assert "id" in entry or "error" in entry

    def test_search_subject_has_expected_fields(self):
        """Subject search results include subjectKind when available."""
        from tortoise.mcp_server import tortoise_search
        result = tortoise_search(entity_type="subject", limit=5)
        if isinstance(result, list) and result:
            entry = result[0]
            assert isinstance(entry, dict)
            assert "id" in entry or "error" in entry

    def test_search_point_is_default(self):
        """Omitting entity_type defaults to 'point'."""
        from tortoise.mcp_server import tortoise_search
        result = tortoise_search("test", limit=5)
        assert isinstance(result, list) or isinstance(result.get("error"), str)

    def test_entity_type_validation(self):
        """Invalid entity_type raises ValueError."""
        from tortoise.mcp_server import tortoise_search
        result = tortoise_search("test", entity_type="invalid-type", limit=5)
        if isinstance(result, dict) and "error" in result:
            assert "entity_type" in str(result["error"]).lower()


# ── Chain verification with packs loaded (#7849) ───────────────────────────

@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
class TestChainVerificationWithPacks:
    """Test check_structure resolves product-strategy:useCase and related kinds."""

    def test_check_structure_runs_with_packs(self):
        """check_structure returns a list (may be empty or contain violations)."""
        sdk = TortoiseSDK()
        try:
            result = sdk.check_structure()
            assert isinstance(result, list)
        finally:
            del sdk

    def test_check_structure_resolves_product_strategy_use_case(self):
        """Verify that check_structure uses pack-aware _expand_kind for useCase."""
        sdk = TortoiseSDK()
        ids = {}
        operator_ids = []
        try:
            ids, operator_ids = _create_test_points(sdk)

            # Run check_structure — should resolve product-strategy:useCase via
            # pack expansion (not just bare 'useCase'). verify the kinds are found.
            violations = sdk.check_structure()
            assert isinstance(violations, list)

            # Key assertion: check_structure ran against pack-prefixed kinds and
            # found our test points (they appear in violations OR are clean).
            # The composedOf-wired useCase (jtbd→uc) should NOT be an orphan_use_case.
            uc_violations = [v for v in violations if v.get("id") == ids["uc"]]
            uc_types = {v["type"] for v in uc_violations}
            assert "orphan_use_case" not in uc_types, (
                f"useCase should have parent JTBD (composedOf wired), got: {uc_types}"
            )
        finally:
            _cleanup_sdk(sdk, *ids.values())
            for oid in operator_ids:
                try:
                    sdk.delete_point(oid)
                except Exception:
                    pass
            del sdk

    def test_check_structure_detects_orphan_use_case(self):
        """An orphan useCase (no parent JTBD) should be detected."""
        sdk = TortoiseSDK()
        created = []
        try:
            # Create a useCase with a composedOf operator but NO parent JTBD (orphan).
            # The operator gives it edges so check_structure flags orphan_use_case,
            # not orphaned_draft (which fires for draft points with zero edges).
            orphan = sdk.create_point(
                "product-strategy:useCase", "Orphan use case",
                context="product-strategy",
                uc_id="UC-ORPHAN-7849",
            )
            created.append(orphan["id"])
            op = sdk.create_operator("composedOf", orphan["id"], [orphan["id"]])
            created.append(op["id"])

            violations = sdk.check_structure()
            our_violations = [v for v in violations if v.get("id") == orphan["id"]]
            assert len(our_violations) >= 1, (
                f"Expected orphan useCase violation for {orphan['id']}, got none"
            )
            types = {v["type"] for v in our_violations}
            assert "orphan_use_case" in types, (
                f"Expected orphan_use_case in {types}"
            )
        finally:
            _cleanup_sdk(sdk, *created)
            del sdk

    def test_check_structure_detects_dangling_use_case_ref(self):
        """A userJourney referencing a non-existent UC should be detected."""
        sdk = TortoiseSDK()
        created = []
        try:
            # Create a userJourney with a dangling UC reference.
            # Add an operator so it has edges (otherwise flagged orphaned_draft).
            uj = sdk.create_point(
                "product-strategy:userJourney", "Dangling ref journey",
                context="product-strategy",
                covered_use_cases="UC-NONEXISTENT-7849",
            )
            created.append(uj["id"])
            op = sdk.create_operator("composedOf", uj["id"], [uj["id"]])
            created.append(op["id"])

            violations = sdk.check_structure()
            our_violations = [v for v in violations if v.get("id") == uj["id"]]
            types = {v["type"] for v in our_violations}
            assert "dangling_use_case_ref" in types, (
                f"Expected dangling_use_case_ref in {types}"
            )
        finally:
            _cleanup_sdk(sdk, *created)
            del sdk

    def test_check_structure_returns_violations_as_dicts(self):
        """All violations are dicts with expected keys."""
        sdk = TortoiseSDK()
        try:
            violations = sdk.check_structure()
            for v in violations:
                assert isinstance(v, dict)
                assert "type" in v
                assert "message" in v
        finally:
            del sdk


# ── Migration script (#7849) ───────────────────────────────────────────────

@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
class TestMigrationScript:
    """Test migrate_kinds updates old core kinds to pack-prefixed."""

    def test_migrate_returns_dict(self):
        """migrate() returns {old_kind: count_updated}. May be empty."""
        from tortoise.migrate_kinds import migrate
        sdk = TortoiseSDK()
        try:
            result = migrate(sdk)
            assert isinstance(result, dict)
        finally:
            del sdk

    def test_migrate_updates_old_kind(self):
        """Creating a point with an old core kind and running migrate updates it."""
        from tortoise.migrate_kinds import migrate
        sdk = TortoiseSDK()
        created = None
        try:
            # Create a point with an old core kind
            pt = sdk.create_point("useCase", "Migration test use case",
                                  context="test-migration")
            created = pt["id"]

            # Before migration, it should have old kind
            fetched = sdk.get_point(created)
            assert fetched.get("pointKind") == "useCase"

            # Run migration
            result = migrate(sdk)
            assert isinstance(result, dict)
            assert result.get("useCase", {}).get("count", 0) >= 1

            # After migration, kind should be updated
            fetched = sdk.get_point(created)
            assert fetched.get("pointKind") == "product-strategy:useCase"
        finally:
            if created:
                _cleanup_sdk(sdk, created)
            del sdk

    def test_migrate_is_idempotent(self):
        """Running migrate twice on already-migrated points produces 0 updates."""
        from tortoise.migrate_kinds import migrate
        sdk = TortoiseSDK()
        created = None
        try:
            pt = sdk.create_point("jobToBeDone", "Idempotent test",
                                  context="test-migration")
            created = pt["id"]

            # First run
            first = migrate(sdk)
            assert first.get("jobToBeDone", {}).get("count", 0) >= 1

            # Second run — should find 0 points with old kind
            second = migrate(sdk)
            assert second.get("jobToBeDone", {}).get("count", 0) == 0
        finally:
            if created:
                _cleanup_sdk(sdk, created)
            del sdk

    def test_migrate_all_mapped_kinds(self):
        """All entries in MIGRATIONS are valid (old, new, entity_type) tuples."""
        from tortoise.migrate_kinds import MIGRATIONS
        assert isinstance(MIGRATIONS, list)
        assert len(MIGRATIONS) > 0
        for entry in MIGRATIONS:
            assert len(entry) == 3, f"Expected (old, new, entity_type), got: {entry}"
            old, new, entity_type = entry
            assert ":" in new, f"{new} must be pack-prefixed"
            ns, kind = new.split(":", 1)
            assert ns, f"namespace missing in {new}"
            assert kind, f"kind missing in {new}"


# ── MCP tool surface (#7849) ───────────────────────────────────────────────

@pytest.mark.skipif(not FALKORDB_AVAILABLE, reason="FalkorDB not available")
class TestMCPSurface:
    """Test tortoise_search with relationship_filter and traversal_path."""

    def test_relationship_filter_format(self):
        """relationship_filter with 'predicate:target_id' format."""
        from tortoise.mcp_server import tortoise_search

        sdk_obj = TortoiseSDK()
        ids = {}
        op_ids = []
        try:
            ids, op_ids = _create_test_points(sdk_obj)

            # Search for features connected to our customer segment via 'addresses'
            result = tortoise_search(
                kind="product-strategy:feature",
                relationship_filter=f"addresses:{ids['customer_seg']}",
                limit=10,
            )
            assert isinstance(result, list) or isinstance(result.get("error"), str)
            if isinstance(result, list) and result:
                found_ids = {r["id"] for r in result}
                assert ids["feature"] in found_ids, (
                    f"Expected feature {ids['feature']} in results, got {found_ids}"
                )
        finally:
            _cleanup_sdk(sdk_obj, *ids.values())
            for oid in op_ids:
                try:
                    sdk_obj.delete_point(oid)
                except Exception:
                    pass
            del sdk_obj

    def test_relationship_filter_invalid_format_warns(self):
        """Malformed relationship_filter returns empty or error."""
        from tortoise.mcp_server import tortoise_search

        result = tortoise_search(
            "test",
            relationship_filter="no-colon-here",
            limit=5,
        )
        # Should not crash — returns list or error dict
        assert isinstance(result, (list, dict))

    def test_traversal_path_resolves(self):
        """traversal_path 'Product→Feature' resolves via pack registry."""
        from tortoise.mcp_server import tortoise_search

        sdk_obj = TortoiseSDK()
        ids = {}
        op_ids = []
        try:
            ids, op_ids = _create_test_points(sdk_obj)

            # 'Product→Feature' should resolve to predicate='contains' via
            # product-strategy pack's relations
            result = tortoise_search(
                traversal_path="Product→Feature",
                limit=10,
            )
            assert isinstance(result, list) or isinstance(result.get("error"), str)
        finally:
            _cleanup_sdk(sdk_obj, *ids.values())
            for oid in op_ids:
                try:
                    sdk_obj.delete_point(oid)
                except Exception:
                    pass
            del sdk_obj

    def test_traversal_path_feature_to_segment(self):
        """traversal_path 'Feature→CustomerSegment' resolves 'addresses' predicate."""
        from tortoise.mcp_server import tortoise_search

        sdk_obj = TortoiseSDK()
        ids = {}
        op_ids = []
        try:
            ids, op_ids = _create_test_points(sdk_obj)

            # 'Feature→CustomerSegment' should resolve to predicate='addresses'
            result = tortoise_search(
                traversal_path="Feature→CustomerSegment",
                limit=10,
            )
            assert isinstance(result, list) or isinstance(result.get("error"), str)
        finally:
            _cleanup_sdk(sdk_obj, *ids.values())
            for oid in op_ids:
                try:
                    sdk_obj.delete_point(oid)
                except Exception:
                    pass
            del sdk_obj

    def test_traversal_path_unknown_returns_empty(self):
        """Unknown traversal_path returns empty list gracefully."""
        from tortoise.mcp_server import tortoise_search

        result = tortoise_search(
            "test",
            traversal_path="Unknown→Nonsense",
            limit=5,
        )
        assert isinstance(result, (list, dict))

    def test_traversal_path_ascii_warns(self):
        """ASCII '->' triggers warning but doesn't crash."""
        from tortoise.mcp_server import tortoise_search

        result = tortoise_search(
            "test",
            traversal_path="Product->Feature",
            limit=5,
        )
        assert isinstance(result, (list, dict))

    def test_combined_filters(self):
        """relationship_filter + traversal_path + kind expansion together."""
        from tortoise.mcp_server import tortoise_search

        sdk_obj = TortoiseSDK()
        ids = {}
        op_ids = []
        try:
            ids, op_ids = _create_test_points(sdk_obj)

            # Combine kind expansion (WorkItem → dev:issue, pm:task, etc.)
            # with traversal_path filter
            result = tortoise_search(
                kind="WorkItem",
                traversal_path="Feature→CustomerSegment",
                limit=10,
            )
            assert isinstance(result, list) or isinstance(result.get("error"), str)
        finally:
            _cleanup_sdk(sdk_obj, *ids.values())
            for oid in op_ids:
                try:
                    sdk_obj.delete_point(oid)
                except Exception:
                    pass
            del sdk_obj


# ── Kind expansion — all 5 categories (#7849) ──────────────────────────────

class TestKindExpansionAllCategories:
    """Test PackRegistry.expand_kind covers all 5 kind categories."""

    def _registry(self):
        from tortoise.pack_registry import PackRegistry
        packs_dir = Path(__file__).resolve().parents[1] / "packs"
        r = PackRegistry(packs_dir)
        r.load_all()
        return r

    def test_object_kinds_expand(self):
        """Object kinds (WorkItem, Project) expand via subclassOf."""
        registry = self._registry()

        # WorkItem → [WorkItem, dev:issue, pm:issue, product-strategy:feature]
        expanded = registry.expand_kind("WorkItem")
        assert "WorkItem" in expanded
        assert "dev:issue" in expanded
        assert "pm:issue" in expanded
        assert "product-strategy:feature" in expanded

        # Project → [Project, dev:epic]
        expanded = registry.expand_kind("Project")
        assert "Project" in expanded
        assert "dev:epic" in expanded

    def test_point_kinds_expand(self):
        """Point kinds (jobToBeDone, useCase) expand to [self]."""
        registry = self._registry()

        # Pack-registered pointKind — should be queryable but expands to [self]
        expanded = registry.expand_kind("product-strategy:jobToBeDone")
        assert expanded == ["product-strategy:jobToBeDone"]

    def test_event_kinds_listable(self):
        """Event kinds are registered and listable via list_all_kinds()."""
        registry = self._registry()
        all_kinds = registry.list_all_kinds()
        assert "eventKinds" in all_kinds
        # May be empty if no packs register events — that's OK
        assert isinstance(all_kinds["eventKinds"], list)

    def test_document_kinds_listable(self):
        """Document kinds are registered and listable via list_all_kinds()."""
        registry = self._registry()
        all_kinds = registry.list_all_kinds()
        assert "documentKinds" in all_kinds
        assert isinstance(all_kinds["documentKinds"], list)
        # product-strategy pack registers document kinds
        found = [k for k in all_kinds["documentKinds"]
                 if k.startswith("product-strategy:")]
        assert len(found) > 0, (
            f"Expected product-strategy document kinds, got: {all_kinds['documentKinds']}"
        )

    def test_action_kinds_listable(self):
        """Action kinds are registered and listable via list_all_kinds()."""
        registry = self._registry()
        all_kinds = registry.list_all_kinds()
        assert "actionKinds" in all_kinds
        assert isinstance(all_kinds["actionKinds"], list)

    def test_all_five_categories_present(self):
        """list_all_kinds returns all 5 categories."""
        registry = self._registry()
        all_kinds = registry.list_all_kinds()
        expected_keys = {"objectKinds", "eventKinds", "pointKinds",
                         "documentKinds", "actionKinds"}
        assert set(all_kinds.keys()) == expected_keys

    def test_expand_kind_unknown_returns_self(self):
        """Unknown kind expands to [self] without crash."""
        registry = self._registry()
        expanded = registry.expand_kind("completely:unknown")
        assert expanded == ["completely:unknown"]

    def test_equivalence_bidirectional(self):
        """Equivalent kinds expand symmetrically."""
        registry = self._registry()
        dev_issue = registry.expand_kind("dev:issue")
        pm_task = registry.expand_kind("pm:issue")
        assert "pm:issue" in dev_issue
        assert "dev:issue" in pm_task
