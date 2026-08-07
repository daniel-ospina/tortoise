"""Tests for tortoise.sdk.TortoiseSDK — TORT-SDK-001.

Runnable with: .venv/bin/python -m pytest tests/test_sdk.py -v
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.domain_loader import known_kinds, register_kind
from tortoise.sdk import TortoiseSDK


# ── Helpers ──────────────────────────────────────────────────────────

UNRECOGNIZED_KIND = "zztop_not_a_real_kind_nope"


@pytest.fixture
def sdk():
    """SDK with temp database. Closed after test."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_sdk_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


def _make_point(sdk: TortoiseSDK, **kw):
    return sdk.create_point(kw.pop("kind", "statement"),
                            kw.pop("content", "test content"), **kw)


# ── create_point ─────────────────────────────────────────────────────

class TestCreatePoint:
    def test_valid_kind(self, sdk):
        p = sdk.create_point("statement", "hello")
        assert p["pointKind"] == "statement"
        assert p["content"] == "hello"
        assert "id" in p

    def test_unrecognized_kind_still_creates(self, sdk, caplog):
        # ponytail: _validate_kind warns, doesn't reject
        assert UNRECOGNIZED_KIND not in known_kinds()
        with caplog.at_level(logging.WARNING):
            p = sdk.create_point(UNRECOGNIZED_KIND, "anyway")
        assert p["pointKind"] == UNRECOGNIZED_KIND
        assert len(caplog.records) >= 1

    def test_with_props(self, sdk):
        p = sdk.create_point("statement", "x", confidence=0.9)
        assert p["confidence"] == 0.9
        assert "context" not in p


# ── create_or_update_point ───────────────────────────────────────────

class TestCreateOrUpdatePoint:
    def test_idempotent(self, sdk):
        p1 = sdk.create_or_update_point("statement", "same stuff")
        p2 = sdk.create_or_update_point("statement", "same stuff")
        assert p1["id"] == p2["id"]

    def test_second_call_updates_props(self, sdk):
        p1 = sdk.create_or_update_point("statement", "dup", confidence=0.1)
        p2 = sdk.create_or_update_point("statement", "dup", confidence=0.9)
        assert p1["id"] == p2["id"]
        assert p2["confidence"] == 0.9


# ── update_point ─────────────────────────────────────────────────────

class TestUpdatePoint:
    def test_set_properties(self, sdk):
        p = _make_point(sdk)
        updated = sdk.update_point(p["id"], confidence=0.5)
        assert updated["confidence"] == 0.5

    def test_verify_with_get_point(self, sdk):
        p = _make_point(sdk, content="before")
        sdk.update_point(p["id"], content="after")
        assert sdk.get_point(p["id"])["content"] == "after"


# ── delete_point ─────────────────────────────────────────────────────

class TestDeletePoint:
    def test_existing(self, sdk):
        p = _make_point(sdk)
        assert sdk.delete_point(p["id"]) is True
        assert sdk.get_point(p["id"]) == {}

    def test_nonexistent(self, sdk):
        assert sdk.delete_point("no-such-id") is False


# ── create_operator ──────────────────────────────────────────────────

class TestCreateOperator:
    def test_impl(self, sdk):
        a, b = _make_point(sdk, content="A"), _make_point(sdk, content="B")
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        assert op["is_operator"] is True
        assert op["op_type"] == "IMPL"
        assert "id" in op

    def test_nand(self, sdk):
        a, b = _make_point(sdk), _make_point(sdk)
        op = sdk.create_operator("NAND", a["id"], [b["id"]])
        assert op["op_type"] == "NAND"

    def test_composed_of(self, sdk):
        a, b = _make_point(sdk), _make_point(sdk)
        op = sdk.create_operator("composedOf", a["id"], [b["id"]])
        assert op["op_type"] == "composedOf"

    def test_invalid_type_raises(self, sdk):
        with pytest.raises(ValueError, match="op_type must be"):
            sdk.create_operator("FOOBAR", "x", ["y"])

    def test_invalid_direction_raises(self, sdk):
        a, b = _make_point(sdk), _make_point(sdk)
        with pytest.raises(ValueError, match="direction must be"):
            sdk.create_operator("IMPL", a["id"], [b["id"]], direction="sideways")

    def test_valid_direction_accepted(self, sdk):
        a, b = _make_point(sdk), _make_point(sdk)
        op_bi = sdk.create_operator("IMPL", a["id"], [b["id"]], direction="bidirectional")
        assert op_bi.get("direction") == "bidirectional"
        op_uni = sdk.create_operator("NAND", a["id"], [b["id"]], direction="unidirectional")
        assert op_uni.get("direction") == "unidirectional"

    def test_direction_default_bidirectional(self, sdk):
        a, b = _make_point(sdk), _make_point(sdk)
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        assert op.get("direction") == "bidirectional"


# ── query ────────────────────────────────────────────────────────────

class TestQuery:
    def test_by_kind(self, sdk):
        sdk.create_point("statement", "A")
        sdk.create_point("goal", "B")
        results = sdk.query(kind="statement")
        assert len(results) == 1
        assert results[0]["pointKind"] == "statement"

    def test_by_kind_filter(self, sdk):
        """P2 #49: context removed — query filters by kind instead."""
        sdk.create_point("statement", "X")
        sdk.create_point("observation", "Y")
        results = sdk.query(kind="statement")
        assert len(results) == 1
        assert results[0]["pointKind"] == "statement"

    def test_by_filter(self, sdk):
        sdk.create_point("statement", "A", confidence=0.99)
        sdk.create_point("statement", "B", confidence=0.01)
        results = sdk.query(confidence=0.99)
        assert len(results) == 1
        assert results[0]["confidence"] == 0.99

    def test_empty_results(self, sdk):
        assert sdk.query(kind="nonexistent") == []


# ── paginated_query ──────────────────────────────────────────────────

class TestPaginatedQuery:
    def test_pagination_structure(self, sdk):
        for i in range(5):
            sdk.create_point("statement", f"point {i}")
        result = sdk.paginated_query(kind="statement", skip=0, limit=2)
        assert "results" in result
        assert "total" in result
        assert "hasMore" in result
        assert result["total"] == 5
        assert len(result["results"]) == 2
        assert result["hasMore"] is True

    def test_no_more_pages(self, sdk):
        for i in range(3):
            sdk.create_point("statement", f"point {i}")
        result = sdk.paginated_query(kind="statement", skip=0, limit=10)
        assert result["total"] == 3
        assert len(result["results"]) == 3
        assert result["hasMore"] is False

    def test_skip_returns_correct_page(self, sdk):
        for i in range(5):
            sdk.create_point("statement", f"point {i}")
        page1 = sdk.paginated_query(kind="statement", skip=0, limit=2)
        page2 = sdk.paginated_query(kind="statement", skip=2, limit=2)
        assert page1["total"] == 5
        assert page2["total"] == 5
        assert len(page1["results"]) == 2
        assert len(page2["results"]) == 2
        # No overlap between pages
        ids1 = {r["id"] for r in page1["results"]}
        ids2 = {r["id"] for r in page2["results"]}
        assert ids1.isdisjoint(ids2)


# ── get_point ────────────────────────────────────────────────────────

class TestGetPoint:
    def test_existing(self, sdk):
        p = _make_point(sdk)
        assert sdk.get_point(p["id"]) == p

    def test_nonexistent(self, sdk):
        assert sdk.get_point("nope") == {}


# ── traverse ─────────────────────────────────────────────────────────

class TestTraverse:
    def test_outgoing(self, sdk):
        a, b = _make_point(sdk, content="src"), _make_point(sdk, content="tgt")
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        # Ontology v2.1: operators hold IMPL edges to their inputs (source +
        # targets); INPUT edges were removed. Outgoing IMPL from the operator
        # node returns both inputs.
        connected = sdk.traverse(op["id"], "IMPL", direction="outgoing")
        assert len(connected) >= 2
        assert {c["id"] for c in connected} == {a["id"], b["id"]}

    def test_incoming(self, sdk):
        a, b = _make_point(sdk, content="src"), _make_point(sdk, content="tgt")
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        connected = sdk.traverse(b["id"], "IMPL", direction="incoming")
        assert len(connected) >= 1

    def test_no_results(self, sdk):
        p = _make_point(sdk)
        assert sdk.traverse(p["id"], "NONE_SUCH") == []


# ── verify_chain ─────────────────────────────────────────────────────

class TestVerifyChain:
    def test_returns_list(self, sdk):
        result = sdk.check_structure()
        assert isinstance(result, list)

    def test_no_crash_empty_db(self, sdk):
        result = sdk.check_structure()
        assert result == []  # empty db → no violations


# ── get_chain_status ─────────────────────────────────────────────────

class TestGetChainStatus:
    def test_expected_keys(self, sdk):
        status = sdk.summarize_structure()
        for key in ("gate0_jtbds", "gate1_use_cases", "gate2_user_journeys",
                     "gate3_workflows", "gate4_requirements", "total"):
            assert key in status, f"missing key: {key}"

    def test_total_matches_sum(self, sdk):
        status = sdk.summarize_structure()
        gate_sum = sum(v for k, v in status.items() if k != "total")
        assert status["total"] == gate_sum


# ── file_jtbd ────────────────────────────────────────────────────────






class TestBatchCreatePoints:
    def test_creates_multiple(self, sdk):
        results = sdk.batch_create_points([
            {"kind": "statement", "content": "A"},
            {"kind": "goal", "content": "B"},
            {"kind": "observation", "content": "C"},
        ])
        assert len(results) == 3
        for r in results:
            assert "id" in r

    def test_all_have_ids(self, sdk):
        results = sdk.batch_create_points([
            {"kind": "statement", "content": "X"},
            {"kind": "statement", "content": "Y"},
        ])
        ids = [r["id"] for r in results]
        assert len(ids) == len(set(ids))  # unique


# ── close ────────────────────────────────────────────────────────────

# ── Invalidate / Supersede (#6999 GAP-12) ──────────────────────────

class TestInvalidateSupersede:
    def test_invalidate_sets_outdated_and_corrects_edge(self, sdk):
        old = _make_point(sdk, content="old claim")
        new = _make_point(sdk, content="new claim")
        result = sdk.invalidate_point(old["id"], new["id"])
        assert result["invalidated"] is True
        assert result["id"] == old["id"]
        assert result["corrected_by"] == new["id"]
        # Verify outdated flag persisted
        old_after = sdk.get_point(old["id"])
        assert old_after["outdated"] is True
        # Verify CORRECTS edge exists (traverse outgoing from new)
        corrected = sdk.traverse(new["id"], "CORRECTS", direction="outgoing")
        assert len(corrected) == 1
        assert corrected[0]["id"] == old["id"]

    def test_invalidate_missing_old_is_false(self, sdk):
        # #330: invalidate_point must not report success for a missing old point
        new = _make_point(sdk, content="new")
        result = sdk.invalidate_point("missing-old", new["id"])
        assert result["invalidated"] is False
        assert result["id"] == "missing-old"
        assert result["corrected_by"] == new["id"]
        # No CORRECTS edge was created
        corrected = sdk.traverse(new["id"], "CORRECTS", direction="outgoing")
        assert len(corrected) == 0

    def test_invalidate_missing_new_raises(self, sdk):
        # #330: missing corrected_by would orphan an outdated point — must raise
        old = _make_point(sdk, content="old")
        with pytest.raises(ValueError):
            sdk.invalidate_point(old["id"], "missing-new")
        # Old point must NOT be marked outdated (no partial write)
        assert not sdk.get_point(old["id"]).get("outdated")

    def test_invalidate_self_raises(self, sdk):
        # #330: a self-CORRECTS edge poisons traversal/credibility — must raise
        old = _make_point(sdk, content="old")
        with pytest.raises(ValueError):
            sdk.invalidate_point(old["id"], old["id"])
        assert not sdk.get_point(old["id"]).get("outdated")
        assert len(sdk.traverse(old["id"], "CORRECTS", direction="outgoing")) == 0

    def test_invalidate_idempotent_corrects_edge(self, sdk):
        # #330: re-invalidating the same pair must not duplicate CORRECTS, and
        # the second call re-asserts (both points still exist -> True) without
        # creating extra edges.
        old = _make_point(sdk, content="old")
        new = _make_point(sdk, content="new")
        r1 = sdk.invalidate_point(old["id"], new["id"])
        assert r1["invalidated"] is True
        r2 = sdk.invalidate_point(old["id"], new["id"])
        assert r2["invalidated"] is True  # present endpoints -> re-assert
        corrected = sdk.traverse(new["id"], "CORRECTS", direction="outgoing")
        assert len(corrected) == 1, "CORRECTS edge duplicated on re-invalidate"

    def test_supersede_atomically_replaces(self, sdk):
        old = _make_point(sdk, content="old")
        new = _make_point(sdk, content="new")
        result = sdk.supersede_point(old["id"], new["id"])
        assert result["invalidated"] is True
        assert result["id"] == old["id"]
        assert result["corrected_by"] == new["id"]
        # New point should NOT be marked outdated
        new_after = sdk.get_point(new["id"])
        assert not new_after.get("outdated")


class TestClose:
    def test_no_crash(self, sdk):
        sdk.close()
        # should not raise on double close
        sdk.close()

    def test_can_reopen(self, sdk):
        sdk.close()
        # methods re-init the projection on next call (lazy init)
        p = sdk.create_point("statement", "after close")
        assert "id" in p


# ── annotate_operator ────────────────────────────────────────────────

class TestAnnotateOperator:
    def test_valid_operator(self, sdk):
        a, b = _make_point(sdk, content="A"), _make_point(sdk, content="B")
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        result = sdk.annotate_operator(op["id"], 0.1, 0.8, 0.7, 0.9)
        assert result["annotator_bias"] == 0.1
        assert result["annotator_precision"] == 0.8
        assert result["annotator_consistency"] == 0.7
        assert result["annotator_directness"] == 0.9

    def test_nonexistent_id_raises(self, sdk):
        with pytest.raises(ValueError, match="not found"):
            sdk.annotate_operator("nonexistent-id", 0.5, 0.5, 0.5, 0.5)

    def test_non_operator_raises(self, sdk):
        p = _make_point(sdk, content="not an operator")
        with pytest.raises(ValueError, match="not an operator"):
            sdk.annotate_operator(p["id"], 0.5, 0.5, 0.5, 0.5)

    def test_out_of_range_raises(self, sdk):
        a, b = _make_point(sdk), _make_point(sdk)
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        with pytest.raises(ValueError, match="bias must be 0-1"):
            sdk.annotate_operator(op["id"], -0.1, 0.5, 0.5, 0.5)
        with pytest.raises(ValueError, match="precision must be 0-1"):
            sdk.annotate_operator(op["id"], 0.5, 1.5, 0.5, 0.5)

    def test_boundary_values(self, sdk):
        a, b = _make_point(sdk), _make_point(sdk)
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        result = sdk.annotate_operator(op["id"], 0.0, 1.0, 0.0, 1.0)
        assert result["annotator_bias"] == 0.0
        assert result["annotator_precision"] == 1.0

    def test_zombie_operator(self, sdk):
        a, b = _make_point(sdk), _make_point(sdk)
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        sdk.delete_point(a["id"])
        sdk.delete_point(b["id"])
        # annotating orphaned operator should still succeed
        result = sdk.annotate_operator(op["id"], 0.5, 0.5, 0.5, 0.5)
        assert result["annotator_bias"] == 0.5


# ── mitigate_operator ────────────────────────────────────────────────

class TestMitigateOperator:
    def test_valid_mitigation(self, sdk):
        a, b = _make_point(sdk), _make_point(sdk)
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        result = sdk.mitigate_operator(op["id"], "sample too small", 0.3)
        assert result["pointKind"] == "statement"
        assert "[MITIGATION] sample too small" in result["content"]
        assert result["mitigation_strength"] == 0.3

    def test_non_operator_raises(self, sdk):
        p = _make_point(sdk)
        with pytest.raises(ValueError, match="not an operator"):
            sdk.mitigate_operator(p["id"], "reason")

    def test_nonexistent_operator_raises(self, sdk):
        with pytest.raises(ValueError, match="not found"):
            sdk.mitigate_operator("nonexistent", "reason")

    def test_invalid_strength_raises(self, sdk):
        a, b = _make_point(sdk), _make_point(sdk)
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        with pytest.raises(ValueError, match="strength must be 0-1"):
            sdk.mitigate_operator(op["id"], "reason", 1.5)

    def test_idempotent(self, sdk):
        a, b = _make_point(sdk), _make_point(sdk)
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        first = sdk.mitigate_operator(op["id"], "reason v1", 0.3)
        second = sdk.mitigate_operator(op["id"], "reason v2", 0.7)
        assert first["id"] == second["id"]  # same mitigation Point
        assert "reason v2" in second["content"]  # updated reason
        assert second["mitigation_strength"] == 0.7  # updated strength

    def test_strength_default(self, sdk):
        a, b = _make_point(sdk), _make_point(sdk)
        op = sdk.create_operator("IMPL", a["id"], [b["id"]])
        result = sdk.mitigate_operator(op["id"], "reason")
        assert result["mitigation_strength"] == 0.5


# ── list_tags + query_points_by_tag (#215) ─────────────────────────

class TestListTags:
    def test_empty_graph(self, sdk):
        """Empty graph returns empty list."""
        assert sdk.list_tags() == []

    def test_single_tag(self, sdk):
        p = sdk.create_point("statement", "tagged content", tags=["alpha"])
        tags = sdk.list_tags()
        assert len(tags) == 1
        assert tags[0]["name"] == "alpha"
        assert tags[0]["count"] == 1

    def test_multiple_points_same_tag(self, sdk):
        sdk.create_point("statement", "first", tags=["shared"])
        sdk.create_point("statement", "second", tags=["shared"])
        tags = sdk.list_tags()
        assert len(tags) == 1
        assert tags[0]["name"] == "shared"
        assert tags[0]["count"] == 2

    def test_multiple_tags(self, sdk):
        sdk.create_point("statement", "a", tags=["alpha", "beta"])
        sdk.create_point("statement", "b", tags=["beta", "gamma"])
        tags = sdk.list_tags()
        names = {t["name"] for t in tags}
        assert names == {"alpha", "beta", "gamma"}
        # alpha tagged 1 point, beta 2, gamma 1
        by_name = {t["name"]: t["count"] for t in tags}
        assert by_name["alpha"] == 1
        assert by_name["beta"] == 2
        assert by_name["gamma"] == 1

    def test_untagged_point_not_counted(self, sdk):
        sdk.create_point("statement", "no tags here")
        sdk.create_point("statement", "tagged", tags=["solo"])
        tags = sdk.list_tags()
        assert len(tags) == 1
        assert tags[0]["name"] == "solo"
        assert tags[0]["count"] == 1


class TestQueryPointsByTag:
    def test_no_match(self, sdk):
        """Querying a non-existent tag returns empty list."""
        assert sdk.query_points_by_tag("nonexistent") == []

    def test_single_point(self, sdk):
        p = sdk.create_point("statement", "find me", tags=["alpha"])
        results = sdk.query_points_by_tag("alpha")
        assert len(results) == 1
        assert results[0]["id"] == p["id"]
        assert results[0]["content"] == "find me"

    def test_multiple_points_same_tag(self, sdk):
        p1 = sdk.create_point("statement", "first", tags=["shared"])
        p2 = sdk.create_point("statement", "second", tags=["shared"])
        results = sdk.query_points_by_tag("shared")
        assert len(results) == 2
        ids = {r["id"] for r in results}
        assert ids == {p1["id"], p2["id"]}

    def test_point_with_multiple_tags(self, sdk):
        p = sdk.create_point("statement", "multi-tagged", tags=["alpha", "beta"])
        # Querying alpha returns the point
        alpha_results = sdk.query_points_by_tag("alpha")
        assert len(alpha_results) == 1
        assert alpha_results[0]["id"] == p["id"]
        # Querying beta also returns the same point
        beta_results = sdk.query_points_by_tag("beta")
        assert len(beta_results) == 1
        assert beta_results[0]["id"] == p["id"]

    def test_mixed_untagged_excluded(self, sdk):
        sdk.create_point("statement", "untagged")
        tagged = sdk.create_point("statement", "tagged", tags=["alpha"])
        results = sdk.query_points_by_tag("alpha")
        assert len(results) == 1
        assert results[0]["id"] == tagged["id"]

    def test_tag_case_sensitive(self, sdk):
        """Tag names are case-sensitive — 'Alpha' != 'alpha'."""
        sdk.create_point("statement", "x", tags=["Alpha"])
        assert sdk.query_points_by_tag("alpha") == []
        assert len(sdk.query_points_by_tag("Alpha")) == 1


# ── update_point tag sync + orphan Tag GC (#485) ──────────────────

class TestUpdatePointTags:
    """update_point keeps TAGGED edges consistent with the n.tags property
    (#485). Before the fix, update_point set the property but left edges
    stale, so query_points_by_tag missed updated points."""

    def test_update_adds_tags_and_query_returns_point(self, sdk):
        """Updating a point with tags creates TAGGED edges + Tag nodes."""
        p = sdk.create_point("statement", "tagged later")
        assert sdk.query_points_by_tag("alpha") == []
        sdk.update_point(p["id"], tags=["alpha"])
        results = sdk.query_points_by_tag("alpha")
        assert len(results) == 1
        assert results[0]["id"] == p["id"]
        # n.tags property and list_tags agree
        assert sdk.get_point(p["id"])["tags"] == ["alpha"]
        by_name = {t["name"]: t["count"] for t in sdk.list_tags()}
        assert by_name["alpha"] == 1

    def test_update_removes_tag_deletes_edge(self, sdk):
        """Removing a tag deletes the TAGGED edge — query stops returning it."""
        p = sdk.create_point("statement", "multi", tags=["alpha", "beta"])
        sdk.update_point(p["id"], tags=["alpha"])
        assert sdk.query_points_by_tag("beta") == []
        assert [r["id"] for r in sdk.query_points_by_tag("alpha")] == [p["id"]]
        assert sdk.get_point(p["id"])["tags"] == ["alpha"]

    def test_update_replaces_all_tags(self, sdk):
        """A full tag swap leaves no stale edges behind."""
        p = sdk.create_point("statement", "swap", tags=["old1", "old2"])
        sdk.update_point(p["id"], tags=["new1"])
        for stale in ("old1", "old2"):
            assert sdk.query_points_by_tag(stale) == [], f"{stale} edge not removed"
        assert len(sdk.query_points_by_tag("new1")) == 1

    def test_update_clears_tags_with_empty_list(self, sdk):
        """tags=[] removes all TAGGED edges for the point."""
        p = sdk.create_point("statement", "clear", tags=["alpha"])
        sdk.update_point(p["id"], tags=[])
        assert sdk.query_points_by_tag("alpha") == []
        # point still exists, just untagged
        assert sdk.get_point(p["id"])["id"] == p["id"]
        assert sdk.get_point(p["id"]).get("tags") == []

    def test_update_nested_props_tags(self, sdk):
        """MCP-style props={'tags': [...]} shape is synced too (#218)."""
        p = sdk.create_point("statement", "nested")
        sdk.update_point(p["id"], props={"tags": ["gamma"]})
        assert [r["id"] for r in sdk.query_points_by_tag("gamma")] == [p["id"]]

    def test_update_removal_gc_orphan_tag(self, sdk):
        """Removing a tag's last reference GCs the orphan :Tag node — no
        count-0 entry lingers in list_tags (#485)."""
        p = sdk.create_point("statement", "solo", tags=["only"])
        sdk.update_point(p["id"], tags=[])
        assert sdk.query_points_by_tag("only") == []
        assert sdk.list_tags() == []

    def test_update_non_list_tags_no_crash(self, sdk):
        """Non-list tag values match create_point: property set, no edge sync."""
        p = sdk.create_point("statement", "scalar", tags=["alpha"])
        sdk.update_point(p["id"], tags="not-a-list")
        # edges untouched — old edge still there
        assert len(sdk.query_points_by_tag("alpha")) == 1

    def test_update_none_clears_tags(self, sdk):
        """tags=None normalizes to [] like create_point — clears edges, no stale
        state left behind by the 'clear' idiom (#485)."""
        p = sdk.create_point("statement", "clear-none", tags=["alpha"])
        sdk.update_point(p["id"], tags=None)
        assert sdk.query_points_by_tag("alpha") == []
        assert sdk.get_point(p["id"]).get("tags") is None

    def test_update_object_branch_tags(self, sdk):
        """The :Point:Object branch (SET n += $props whole-dict + version bump)
        also syncs TAGGED edges (#485)."""
        p = sdk.create_point("statement", "entity-ish")
        proj = sdk._get_proj()
        proj.g.query("MATCH (n:Point {id:$id}) SET n:Object", params={"id": p["id"]})
        sdk.update_point(p["id"], tags=["objtag"])
        assert [r["id"] for r in sdk.query_points_by_tag("objtag")] == [p["id"]]
        assert sdk.get_point(p["id"])["version"] == 1  # Object branch bumps version
        sdk.update_point(p["id"], tags=[])
        assert sdk.query_points_by_tag("objtag") == []

    def test_update_nonexistent_point_no_orphan(self, sdk):
        """update_point on a non-existent id must not create phantom :Tag
        nodes (empty MATCH short-circuits the MERGEs)."""
        sdk.update_point("000000000000-000000000000", tags=["phantom"])
        assert sdk.list_tags() == []

    def test_int_tag_names_roundtrip(self, sdk):
        """Non-string (int) tag names are param-bound and round-trip."""
        p = sdk.create_point("statement", "ints", tags=[123])
        assert [r["id"] for r in sdk.query_points_by_tag(123)] == [p["id"]]
        names = {t["name"] for t in sdk.list_tags()}
        assert 123 in names

    def test_update_shared_tag_survives_removal(self, sdk):
        """Removing a tag from one point leaves the Tag intact if another
        point still uses it (GC only deletes truly orphaned Tags)."""
        a = sdk.create_point("statement", "a", tags=["shared"])
        b = sdk.create_point("statement", "b", tags=["shared"])
        sdk.update_point(a["id"], tags=[])
        tags = sdk.list_tags()
        assert len(tags) == 1
        assert tags[0]["name"] == "shared"
        assert tags[0]["count"] == 1

    def test_dedup_syncs_tags(self, sdk):
        """create_point(dedup=True) routes through update_point — tags reconcile
        to the latest call (no stale edges from the earlier tags)."""
        sdk.create_point("statement", "same content", tags=["first"])
        sdk.create_point("statement", "same content", dedup=True, tags=["second"])
        assert sdk.query_points_by_tag("first") == []
        assert len(sdk.query_points_by_tag("second")) == 1


class TestOrphanTagGC:
    """delete_point garbage-collects orphaned :Tag nodes (#485)."""

    def test_delete_gc_orphan_tags(self, sdk):
        """Deleting the only tagged point removes its Tag from list_tags."""
        p = sdk.create_point("statement", "only tagged", tags=["alpha"])
        assert len(sdk.list_tags()) == 1
        sdk.delete_point(p["id"])
        assert sdk.list_tags() == []

    def test_delete_keeps_shared_tag(self, sdk):
        """A tag still used by another point survives GC."""
        p1 = sdk.create_point("statement", "one", tags=["shared"])
        sdk.create_point("statement", "two", tags=["shared"])
        sdk.delete_point(p1["id"])
        tags = sdk.list_tags()
        assert len(tags) == 1
        assert tags[0]["name"] == "shared"
        assert tags[0]["count"] == 1
