"""Stop-writes tests for #49 Phase 2 — context REMOVED entirely.

Runs on the conftest isolated graph. Verifies that context is no longer accepted
— create_point(context=X) now raises TypeError.
"""
from __future__ import annotations

import uuid

import pytest
from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    s = TortoiseSDK()
    yield s
    try:
        s.close()
    except Exception:
        pass


def _mk():
    return f"sw_{uuid.uuid4().hex[:8]}"


def test_create_point_context_raises_type_error(sdk):
    """create_point(context=X) raises TypeError in Phase 2."""
    with pytest.raises(TypeError, match="context"):
        sdk.create_point("statement", "test point", context="legacy")


def test_create_operator_context_raises_type_error(sdk):
    """create_operator(context=X) raises TypeError — context param removed."""
    p1 = sdk.create_point("statement", "source point")
    p2 = sdk.create_point("statement", "target point")
    with pytest.raises(TypeError, match="context"):
        sdk.create_operator("IMPL", p1["id"], [p2["id"]], context="legacy")


def test_update_point_context_raises_type_error(sdk):
    """update_point(context=X) raises TypeError."""
    p = sdk.create_point("statement", "test point")
    with pytest.raises(TypeError, match="context"):
        sdk.update_point(p["id"], context="legacy")


def test_dedup_content_hash_pointkind(sdk):
    """Same content + different pointKind → no dedup. Same + same kind → dedup."""
    content = f"dedup test {_mk()}"
    p1 = sdk.create_point("statement", content, dedup=True)
    p2 = sdk.create_point("observation", content, dedup=True)
    assert p1["id"] != p2["id"], "different pointKinds must not dedup"
    p3 = sdk.create_point("statement", content, dedup=True)
    assert p3["id"] == p1["id"], "same pointKind must dedup"


def test_create_without_context(sdk):
    """create_point without context works normally (Phase 2 — context removed)."""
    p = sdk.create_point("statement", f"no ctx {_mk()}")
    assert p["id"]
    assert "context" not in p or p.get("context") is None


def test_file_decision_no_context(sdk):
    """file_decision does not accept context param (Phase 2)."""
    with pytest.raises(TypeError, match="context"):
        sdk.file_decision(
            options=["A", "B"],
            evidence=["E1 supports A"],
            choice=0,
            context="legacy",
        )
