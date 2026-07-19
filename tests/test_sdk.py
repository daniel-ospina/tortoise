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
        p = sdk.create_point("statement", "x", confidence=0.9, context="ctx")
        assert p["confidence"] == 0.9
        assert p["context"] == "ctx"


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
        updated = sdk.update_point(p["id"], confidence=0.5, context="new")
        assert updated["confidence"] == 0.5
        assert updated["context"] == "new"

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


# ── query ────────────────────────────────────────────────────────────

class TestQuery:
    def test_by_kind(self, sdk):
        sdk.create_point("statement", "A")
        sdk.create_point("goal", "B")
        results = sdk.query(kind="statement")
        assert len(results) == 1
        assert results[0]["pointKind"] == "statement"

    def test_by_context(self, sdk):
        sdk.create_point("statement", "X", context="g0")
        sdk.create_point("statement", "Y", context="g1")
        results = sdk.query(context="g0")
        assert len(results) == 1
        assert results[0]["context"] == "g0"

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
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        connected = sdk.traverse(b["id"], "INPUT", direction="outgoing")
        assert len(connected) >= 1

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
