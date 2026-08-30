"""Tests for tortoise.sdk.TortoiseSDK — TORT-SDK-001.

Runnable with: .venv/bin/python -m pytest tests/test_sdk.py -v
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: I001

from tortoise.domain_loader import known_kinds, register_kind  # noqa: F401
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

    def test_update_content_recomputes_content_hash(self, sdk):
        """#1904: update_point(content=...) recomputes content_hash in the
        same round trip. All dedup surfaces match on the stored hash, so a
        stale hash after a content edit silently breaks dedup — the edited
        point's re-insert must dedup to the SAME point (exactly once)."""
        import hashlib
        p = sdk.create_point("statement", "X")
        sdk.update_point(p["id"], content="Y")
        after = sdk.get_point(p["id"])
        assert after["content_hash"] == hashlib.sha256(b"Y").hexdigest()
        again = sdk.create_or_update_point("statement", "Y")
        assert again["id"] == p["id"]
        g = sdk._get_proj().g
        cnt = g.query(
            "MATCH (n:Point {content_hash:$ch}) WHERE n.is_operator = false "
            "RETURN count(n)",
            params={"ch": hashlib.sha256(b"Y").hexdigest()},
        ).result_set[0][0]
        assert cnt == 1

    def test_update_content_hash_not_in_event_record(self, tmp_path):
        """#1904: content_hash is DERIVED from content — the PointRevised
        record must not persist it (mirrors create_point's snapshot strip)."""
        import json
        sdk = TortoiseSDK(db_path=str(tmp_path / "t.db"),
                          event_log_path=str(tmp_path / "events.jsonl"))
        p = sdk.create_point("statement", "X")
        sdk.update_point(p["id"], content="Y")
        lines = (tmp_path / "events.jsonl").read_text().splitlines()
        revised = [json.loads(l) for l in lines  # noqa: E741
                   if json.loads(l).get("type") == "PointRevised"]
        assert revised, "PointRevised event must be emitted"
        assert "content_hash" not in revised[0]
        assert revised[0]["new_content"] == "Y"
        sdk.close()


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

    def test_filter_key_injection_rejected(self, sdk):
        sdk.create_point("statement", "A")
        # Keys that reach the filters loop (not bound by the signature)
        payloads = [
            {"kind_0": "goal"},                       # reserved (expansion prefix)
            {"x`} DETACH DELETE (n) //": 1},          # backtick breakout
            {"x' OR 1=1 //": 1},                      # quote injection
            {"émoji": 1}, {"中": 1},                   # Unicode keys
        ]
        for filters in payloads:
            with pytest.raises(ValueError):
                sdk.query(**filters)

    def test_filter_key_signature_collision_raises(self, sdk):
        # kind binds to the method signature — a caller passing it via
        # **filters together with the named arg gets TypeError at the call
        # site (MCP path), never a silently-corrupted WHERE.
        sdk.create_point("statement", "A")
        with pytest.raises(TypeError):
            sdk.query(kind="statement", **{"kind": "goal"})
        with pytest.raises(TypeError):
            sdk.paginated_query(kind="statement", **{"kind": "goal"})

    def test_filter_key_legit_still_works(self, sdk):
        sdk.create_point("statement", "A", confidence=0.9)
        sdk.create_point("statement", "B", confidence=0.1)
        results = sdk.query(confidence=0.9, status="draft")
        assert len(results) == 1
        assert results[0]["content"] == "A"


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

    def test_filter_key_reserved_rejected(self, sdk):
        sdk.create_point("statement", "A")
        for key in ("kind_0", "kind_7", "x`} DETACH DELETE (n) //", "émoji"):
            with pytest.raises(ValueError):
                sdk.paginated_query(kind="statement", **{key: 1})

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

    def test_invalid_pagination_params_rejected(self, sdk):
        # #1914: limit=0 would make hasMore = skip + 0 < total always True
        # on non-empty graphs (infinite pagination loop); negative skip/limit
        # would pass raw into Cypher. Both must fail cleanly (ValueError →
        # clean 400 at the API surface).
        sdk.create_point("statement", "A")
        for kwargs in ({"limit": 0}, {"limit": -5}, {"skip": -1},
                       {"skip": -1, "limit": 0}):
            with pytest.raises(ValueError, match="must be"):
                sdk.paginated_query(kind="statement", **kwargs)


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

    def test_unknown_rel_type_rejected(self, sdk):
        """#329: rel_type is allowlisted — unknown types raise, no query runs."""
        p = _make_point(sdk)
        with pytest.raises(ValueError, match="Invalid relationship type"):
            sdk.traverse(p["id"], "NONE_SUCH")

    def test_injection_payload_rejected(self, sdk):
        """#329: Cypher injection via rel_type is blocked before any query."""
        p = _make_point(sdk)
        payload = "IMPL]->(x:Point {id:'p2'}) DETACH DELETE x //"
        with pytest.raises(ValueError, match="Invalid relationship type"):
            sdk.traverse(p["id"], payload)

    def test_known_rel_types_accepted(self, sdk):
        a = _make_point(sdk, content="src")
        b = _make_point(sdk, content="tgt")
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        # No results but NO raise — known types pass validation
        assert sdk.traverse(a["id"], "NAND") == []
        assert sdk.traverse(a["id"], "TAGGED") == []
        assert sdk.traverse(a["id"], "aboutPoint") == []

    def test_invalid_direction_rejected(self, sdk):
        p = _make_point(sdk)
        with pytest.raises(ValueError, match="Invalid direction"):
            sdk.traverse(p["id"], "IMPL", direction="sideways")


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

    def test_supersede_idempotent_corrects_edge(self, sdk):
        # #432: repeated supersede is an illegal transition (superseded is
        # terminal). The CORRECTS edge from the first call is still unique.
        old = _make_point(sdk, content="old-sup")
        new = _make_point(sdk, content="new-sup")
        sdk.supersede_point(old["id"], new["id"])
        with pytest.raises(ValueError, match="already terminal"):
            sdk.supersede_point(old["id"], new["id"])
        corrected = sdk.traverse(new["id"], "CORRECTS", direction="outgoing")
        assert len(corrected) == 1, "CORRECTS edge duplicated on re-supersede"

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

    def test_supersede_missing_old_raises(self, sdk):
        # #432: supersede_point raises ValueError for a missing old point
        # (the pre-#432 {"invalidated": False} contract was superseded by
        # the #432 terminal guard).
        new = _make_point(sdk, content="new")
        with pytest.raises(ValueError, match="No point"):
            sdk.supersede_point("missing-old", new["id"])

    def test_supersede_missing_new_raises(self, sdk):
        # #547: missing new point would orphan an outdated old — must raise
        old = _make_point(sdk, content="old")
        with pytest.raises(ValueError):
            sdk.supersede_point(old["id"], "missing-new")
        # Old point must NOT be marked outdated (no partial write)
        assert not sdk.get_point(old["id"]).get("outdated")
        # No CORRECTS edge
        assert len(sdk.traverse(old["id"], "CORRECTS", direction="outgoing")) == 0

    def test_supersede_self_raises(self, sdk):
        # #547: a self-CORRECTS edge poisons traversal — must raise
        old = _make_point(sdk, content="old")
        with pytest.raises(ValueError):
            sdk.supersede_point(old["id"], old["id"])
        assert not sdk.get_point(old["id"]).get("outdated")
        assert len(sdk.traverse(old["id"], "CORRECTS", direction="outgoing")) == 0


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
        p = sdk.create_point("statement", "tagged content", tags=["alpha"])  # noqa: F841
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
        b = sdk.create_point("statement", "b", tags=["shared"])  # noqa: F841
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


# ── #329: write-side rejection of server-managed fields ─────────────

class TestSanitizeProps:
    def test_create_document_rejects_sourcepath_and_id(self, sdk):
        with pytest.raises(ValueError, match="server-managed"):
            sdk.create_document("Doc", "planDoc", sourcePath="/etc/passwd")
        with pytest.raises(ValueError, match="server-managed"):
            sdk.create_document("Doc", "planDoc", source_path="/etc/passwd")
        with pytest.raises(ValueError, match="server-managed"):
            sdk.create_document("Doc", "planDoc", id="/etc/passwd")

    def test_create_document_clean_has_no_sourcepath(self, sdk):
        """Positive: a clean create_document stores no sourcePath property."""
        doc = sdk.create_document("Clean", "planDoc")
        assert "sourcePath" not in doc

    def test_create_document_links_extracted_from_source(self, sdk):
        """#394: create_document wires Document → Source via extractedFrom."""
        doc = sdk.create_document(
            "Sourced", "planDoc", extractedFrom="https://docs.example.com/spec"
        )
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (d:Document {id:$did})-[:extractedFrom]->(s:Source {url:$url}) "
            "RETURN count(*) > 0",
            params={"did": doc["id"], "url": "https://docs.example.com/spec"},
        ).result_set
        assert r[0][0] is True

    def test_create_document_no_extracted_from_no_edge(self, sdk):
        """#394: without extractedFrom, no Document→Source edge is created."""
        doc = sdk.create_document("Unsourced", "planDoc")
        proj = sdk._get_proj()
        r = proj.g.query(
            "MATCH (d:Document {id:$did})-[:extractedFrom]->(s) RETURN count(s)",
            params={"did": doc["id"]},
        ).result_set
        assert r[0][0] == 0

    def test_update_entity_rejects_sourcepath_and_id(self, sdk):
        doc = sdk.create_document("Upd", "planDoc")
        did = doc["id"]
        with pytest.raises(ValueError, match="server-managed"):
            sdk.update_entity(did, sourcePath="/etc/passwd")
        with pytest.raises(ValueError, match="server-managed"):
            sdk.update_entity(did, source_path="/etc/passwd")
        with pytest.raises(ValueError, match="server-managed"):
            sdk.update_entity(did, id="evil")

    def test_update_point_rejects_id_and_sourcepath(self, sdk):
        p = sdk.create_point("statement", "A")
        # id binds to the signature → TypeError at the call site (never
        # silently mutates node identity)
        with pytest.raises(TypeError):
            sdk.update_point(p["id"], **{"id": "evil"})
        with pytest.raises(ValueError, match="server-managed"):
            sdk.update_point(p["id"], sourcePath="/etc/passwd")
        with pytest.raises(ValueError, match="server-managed"):
            sdk.update_point(p["id"], source_path="/etc/passwd")

    def test_create_event_document_mint_safe_and_unsafe_ids(self, sdk):
        # Safe basename id mints a Document
        sdk.create_event("ev1", "meeting", object="session-2026-08-07.md", objectType="Document")
        rows = sdk._get_proj().g.query(
            "MATCH (d:Document {id:$id}) RETURN count(d)",
            params={"id": "session-2026-08-07.md"},
        ).result_set
        assert rows[0][0] == 1
        # Unsafe id → ValueError at write
        with pytest.raises(ValueError, match="Invalid document id"):
            sdk.create_event("ev2", "meeting", object="/etc/passwd", objectType="Document")
        with pytest.raises(ValueError, match="Invalid document id"):
            sdk.create_event("ev3", "meeting", object="../x", objectType="Document")

    def test_create_point_explicit_id_preserved(self, sdk):
        """Operator explicit-id path (create_point props.pop('id')) still works."""
        import uuid
        pid = f"op-{uuid.uuid4().hex[:8]}"
        p = sdk.create_point("statement", "Explicit", id=pid)
        assert p["id"] == pid


# ── Phase-4 promotion + draft queue (#785) ────────────────────────────
# promote_point: reviewer-gated draft→live, batch quarantine lock, R16
# zombie-operator prevention, already-live no-op (DE2E-N9).
# list_drafts: J-5 draft queue for promotion review.

def _set_status(sdk, pid, status):
    """Direct status write — test seam for pre-#780 paths (create_operator
    writes no operator status on main and auto-promotes the source, #131)."""
    sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) SET n.status=$st",
        params={"id": pid, "st": status},
    )


def _make_draft_operator(sdk, op_type, inputs, *, label=None):
    """Hand-build a DRAFT operator node with edges (simulates #780's
    create_operator(promote_source=False) output; on main that flag does not
    exist yet). Returns the operator point dict."""
    from tortoise.ids import ulid
    proj = sdk._get_proj()
    oid = ulid()
    proj.g.query(
        "CREATE (o:Point {id:$id, is_operator:true, op_type:$op, status:'draft'"
        + (", label:$label" if label else "") + "})",
        params={"id": oid, "op": op_type, "label": label},
    )
    for i, pid in enumerate(inputs):
        proj.g.query(
            "MATCH (o:Point {id:$oid}), (s:Point {id:$sid}) "
            "CREATE (o)-[:NAND {idx:$i}]->(s)",
            params={"oid": oid, "sid": pid, "i": i},
        )
    return sdk.get_point(oid)


class TestPromotePoint:
    def test_promote_draft_to_live(self, sdk):
        p = _make_point(sdk, status="draft")
        res = sdk.promote_point(p["id"])
        assert res["promoted"] is True
        assert res["status"] == "live"
        assert res["reviewed"] is True
        live = sdk.get_point(p["id"])
        assert live["status"] == "live"
        assert live["reviewed"] is True
        assert "promotedAt" in live

    def test_promote_already_live_is_noop(self, sdk):
        # DE2E-N9: already-live promote → no-op, no error.
        p = _make_point(sdk, status="live")
        res = sdk.promote_point(p["id"])
        assert res == {"id": p["id"], "status": "live", "promoted": False,
                       "blocked": False, "reason": "already_live"}

    def test_promote_terminal_point_is_blocked(self, sdk):
        p = _make_point(sdk, status="draft")
        sdk.retract_point(p["id"])
        res = sdk.promote_point(p["id"])
        assert res["blocked"] is True
        assert res["reason"] == "not_draft"
        assert res["status"] == "retracted"

    def test_promote_missing_point_raises(self, sdk):
        with pytest.raises(ValueError, match="No point"):
            sdk.promote_point("no-such-point")

    def test_promote_blocked_on_quarantined_batch(self, sdk):
        from tortoise.mining import quarantine_batch
        p = _make_point(sdk, status="draft", batch_id="qb1")
        quarantine_batch(sdk._get_proj(), "qb1", reason="EP drift (W-3)")
        res = sdk.promote_point(p["id"])
        assert res == {"id": p["id"], "status": "draft", "promoted": False,
                       "blocked": True, "reason": "batch_quarantined",
                       "batch_id": "qb1"}
        assert sdk.get_point(p["id"])["status"] == "draft"  # stays draft

    def test_promote_not_blocked_by_unregistered_batch(self, sdk):
        p = _make_point(sdk, status="draft", batch_id="never-ran")
        res = sdk.promote_point(p["id"])
        assert res["promoted"] is True

    def test_r16_operator_promoted_once_all_endpoints_live(self, sdk):
        a = _make_point(sdk, status="draft")
        b = _make_point(sdk, status="draft")
        op = _make_draft_operator(sdk, "NAND", [a["id"], b["id"]])
        assert op["status"] == "draft"
        # promote only A → operator must stay draft (B not live yet)
        sdk.promote_point(a["id"])
        assert sdk.get_point(op["id"])["status"] == "draft"
        # promote B → all endpoints live → R16 promotes the operator
        res = sdk.promote_point(b["id"])
        assert res["operator_nodes_promoted"] == [op["id"]]
        assert sdk.get_point(op["id"])["status"] == "live"

    def test_r16_no_promote_for_operator_with_live_endpoints_only(self, sdk):
        # operator whose endpoints were ALREADY live before this promote
        a = _make_point(sdk, status="live")
        b = _make_point(sdk, status="draft")
        op = _make_draft_operator(sdk, "IMPL", [a["id"], b["id"]])
        # promoting b: endpoint a is live, b now live → operator promotes
        res = sdk.promote_point(b["id"])
        assert op["id"] in res["operator_nodes_promoted"]

    def test_r16_skips_live_operator_nodes(self, sdk):
        # an operator node already live (event-path default) is not re-promoted
        a = _make_point(sdk, status="draft")
        b = _make_point(sdk, status="draft")
        op = _make_draft_operator(sdk, "NAND", [a["id"], b["id"]])
        _set_status(sdk, op["id"], "live")
        sdk.promote_point(a["id"])
        res = sdk.promote_point(b["id"])
        assert res["operator_nodes_promoted"] == []

    def test_promote_emits_point_promoted_event(self, tmp_path):
        sdk = TortoiseSDK(db_path=str(tmp_path / "t.db"),
                          event_log_path=str(tmp_path / "events.jsonl"))
        p = _make_point(sdk, status="draft")
        sdk.promote_point(p["id"])
        log = sdk._get_event_log()
        assert log is not None
        types = [e["type"] for e in log.read_all() if e.get("type")]
        assert "PointPromoted" in types
        sdk.close()


class TestListDrafts:
    def test_lists_only_draft_points(self, sdk):
        _make_point(sdk, status="draft", content="draft one")
        _make_point(sdk, status="live", content="live one")
        drafts = sdk.list_drafts()
        assert [d["content"] for d in drafts] == ["draft one"]
        assert len(drafts) == 1

    def test_contract_shape(self, sdk):
        p = _make_point(sdk, status="draft", batch_id="b9",
                        extractedFrom="src-1")
        d = sdk.list_drafts()[0]
        assert set(d) == {"id", "content", "pointKind", "provenance",
                          "dedup_context", "batch_id"}
        assert d["id"] == p["id"]
        assert d["provenance"] == "src-1"
        assert d["batch_id"] == "b9"
        assert d["dedup_context"] is None

    def test_limit(self, sdk):
        for i in range(5):
            _make_point(sdk, status="draft", content=f"d{i}")
        assert len(sdk.list_drafts(limit=2)) == 2
        with pytest.raises(ValueError):
            sdk.list_drafts(limit=0)

    def test_empty_queue(self, sdk):
        assert sdk.list_drafts() == []

    def test_dedup_context_assembled(self, sdk):
        p = _make_point(sdk, status="draft", dedup_candidate=True,
                        dedup_method="hash", dedup_similarity=1.0,
                        dedup_target_id="t-1")
        d = [x for x in sdk.list_drafts() if x["id"] == p["id"]][0]  # noqa: RUF015
        assert d["dedup_context"] == {"dedup_method": "hash",
                                      "dedup_similarity": 1.0,
                                      "dedup_target_id": "t-1"}


def test_de2e8_contradiction_propagates_after_promotion(sdk):
    """DE2E-8 tail: after both NAND endpoints promote, the incident operator
    node goes live and the contradiction propagates through EP (live→live)."""
    from tortoise.ep import TortoiseEP

    a = _make_point(sdk, status="draft", content="A: port is 16379")
    b = _make_point(sdk, status="draft", content="B: port is 16380")
    op = _make_draft_operator(sdk, "NAND", [a["id"], b["id"]])
    # Evidence: both claims individually strong before promotion.
    for pid in (a["id"], b["id"]):
        sdk._get_proj().g.query(
            "MATCH (n:Point {id:$id}) SET n.ep_alpha=$al, n.ep_beta=$be, "
            "n.baseline_set=true",
            params={"id": pid, "al": 8.0, "be": 1.0},
        )
    sdk.promote_point(a["id"])
    sdk.promote_point(b["id"])
    assert sdk.get_point(op["id"])["status"] == "live", (
        "R16: operator must go live once ALL endpoints are live"
    )

    # The contradiction is now live→live: EP must move BOTH posteriors down
    # from their strong priors (mutual exclusion crushes equal-strength sides).
    proj = sdk._get_proj()
    rows = proj.g.query(
        "MATCH (n:Point) WHERE n.baseline_set = true AND n.ep_alpha IS NOT NULL "
        "RETURN n.id, n.ep_alpha, n.ep_beta"
    ).result_set
    evidence = {r[0]: (r[1], r[2]) for r in rows}
    ep = TortoiseEP(proj, damping=0.5, n_quad=12, max_iter=50, tol=1e-3,
                    evidence=evidence)
    iters, converged = ep.run([op["id"]], max_hops=2)  # noqa: RUF059
    assert converged
    for pid in (a["id"], b["id"]):
        r = proj.g.query(
            "MATCH (n:Point {id:$id}) "
            "RETURN coalesce(n.posterior_alpha, n.ep_alpha, 1.0), "
            "       coalesce(n.posterior_beta, n.ep_beta, 1.0)",
            params={"id": pid},
        ).result_set
        al, be = r[0][0], r[0][1]
        mean = al / (al + be)
        assert mean < 0.80, (
            f"NAND contradiction must suppress the promoted claim's "
            f"posterior (mean {mean:.3f} vs prior 0.889)"
        )


def test_promote_operator_node_is_blocked(sdk):
    """promote_point on an operator node must be blocked — operators only go
    live via the R16 endpoint gate (review #944)."""
    a = _make_point(sdk, status="draft", content="a")
    b = _make_point(sdk, status="draft", content="b")
    op = _make_draft_operator(sdk, "NAND", [a["id"], b["id"]])
    res = sdk.promote_point(op["id"])
    assert res["blocked"] is True
    assert res["reason"] == "is_operator"
    assert sdk.get_point(op["id"])["status"] == "draft"


def test_r16_skips_unset_status_operator(sdk):
    """Unset-status (legacy, pre-#780) operator nodes are LIVE under the
    canonical read model — R16 must NOT promote them or emit spurious
    OperatorPromoted events (review #944)."""
    import tempfile  # noqa: I001
    import json
    tmp = tempfile.mkdtemp()
    sdk2 = TortoiseSDK(db_path=os.path.join(tmp, "t.db"),
                       event_log_path=os.path.join(tmp, "events.jsonl"))
    a = _make_point(sdk2, status="draft", content="a")
    b = _make_point(sdk2, status="draft", content="b")
    # Legacy shape: operator node with NO status property (create_operator
    # default path on main before #780 wrote no status).
    op = _make_draft_operator(sdk2, "NAND", [a["id"], b["id"]])
    sdk2._get_proj().g.query(
        "MATCH (o:Point {id:$id}) REMOVE o.status", params={"id": op["id"]})
    assert sdk2.get_point(op["id"]).get("status") is None
    res = sdk2.promote_point(a["id"])
    assert res["operator_nodes_promoted"] == [], (
        "unset-status operator must NOT be re-promoted"
    )
    res2 = sdk2.promote_point(b["id"])
    assert res2["operator_nodes_promoted"] == []
    assert sdk2.get_point(op["id"]).get("status") is None
    lines = open(os.path.join(tmp, "events.jsonl")).read().splitlines()  # noqa: SIM115
    types = [json.loads(l).get("type") for l in lines]  # noqa: E741
    assert "OperatorPromoted" not in types, (
        "no spurious OperatorPromoted for a live-by-projection operator"
    )
    sdk2.close()


def test_update_content_replay_hash_parity(tmp_path):
    """#1904 replay parity: the live graph's stored content_hash for an
    edited point equals the hash the JSONL replay derives from the replayed
    content (PointRevised.new_content). Before the fix the stored hash stayed
    at sha256("X") while the replay content was "Y" — live graph and replay
    diverged."""
    import hashlib
    import json

    from tortoise.projection import fold  # replay single source of truth

    event_log = tmp_path / "events.jsonl"
    sdk = TortoiseSDK(db_path=str(tmp_path / "t.db"),
                      event_log_path=str(event_log))
    p = sdk.create_point("statement", "X")
    sdk.update_point(p["id"], content="Y")
    live_hash = sdk.get_point(p["id"])["content_hash"]
    assert live_hash == hashlib.sha256(b"Y").hexdigest()

    # fold() is the replay's single source of truth (projection module
    # contract): the replayed content determines the derived hash.
    events = [json.loads(l) for l in event_log.read_text().splitlines()]  # noqa: E741
    replayed = fold(events)[p["id"]]
    assert replayed["content"] == "Y"
    assert live_hash == hashlib.sha256(
        replayed["content"].encode()).hexdigest()

    # wipe+rebuild_all: the edited content survives and the edited point is
    # still exactly-once reachable via dedup (hash-less fallback scan).
    rebuilt = sdk._get_proj().rebuild_all(str(tmp_path))
    assert rebuilt["events"] > 0
    row = sdk._get_proj().g.query(
        "MATCH (n:Point {id:$id}) RETURN n.content",
        params={"id": p["id"]},
    ).result_set[0]
    assert row[0] == "Y"
    again = sdk.create_or_update_point("statement", "Y")
    assert again["id"] == p["id"]
    sdk.close()


def test_promotion_survives_rebuild(sdk, tmp_path):
    """Rebuild parity (#548, review #944): promoted Points and R16-promoted
    operators must still be live after wipe+rebuild_all from the JSONL log."""
    import json  # noqa: F401
    event_log = tmp_path / "events.jsonl"
    sdk2 = TortoiseSDK(db_path=str(tmp_path / "t.db"),
                       event_log_path=str(event_log))
    a = _make_point(sdk2, status="draft", content="a")
    b = _make_point(sdk2, status="draft", content="b")
    op = _make_draft_operator(sdk2, "NAND", [a["id"], b["id"]])
    sdk2.promote_point(a["id"])
    sdk2.promote_point(b["id"])
    assert sdk2.get_point(op["id"])["status"] == "live"
    sdk2.close()

    proj = sdk._get_proj()
    rebuilt = proj.rebuild_all(str(tmp_path))
    assert rebuilt["events"] > 0
    for pid in (a["id"], b["id"], op["id"]):
        rows = proj.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.status",
            params={"id": pid},
        ).result_set
        assert rows and rows[0][0] == "live", (
            f"rebuild must preserve promotion for {pid}, got {rows}"
        )
