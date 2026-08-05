"""Regression test for #80: dedup must work even when the first create_point
call omitted dedup.

Runnable with:
  TORTOISE_DB_URI=docker://:@localhost:16379/tortoise python3 -m pytest tests/test_sdk_dedup.py -v
"""
from __future__ import annotations

import os
import pytest

from tortoise.sdk import TortoiseSDK, _content_hash


@pytest.fixture
def sdk():
    """SDK connected to Docker FalkorDB in an isolated namespace."""
    os.environ.setdefault(
        "TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise"
    )
    s = TortoiseSDK(namespace="test80")
    yield s
    # Cleanup: delete ALL nodes in the isolated test graph
    s._get_proj().g.query("MATCH (n) DETACH DELETE n")


def test_create_point_dedup_without_first_dedup(sdk):
    """#80: dedup=True must find points created WITHOUT dedup.

    Before the fix, content_hash was only persisted when dedup=True was
    passed.  A point created without dedup had no content_hash, so a
    later call with dedup=True would silently create a duplicate.
    """
    content = "dedup-regression-test-#80"

    # 1) Create WITHOUT dedup — pre-fix this would NOT store content_hash
    p1 = sdk.create_point("statement", content)
    assert p1["id"]

    # 2) Create same content WITH dedup — must return the SAME id
    p2 = sdk.create_point("statement", content, dedup=True)
    assert p2["id"] == p1["id"], (
        f"dedup=True should return existing point id. "
        f"Got {p2['id']!r}, expected {p1['id']!r}"
    )

    # 3) Verify content_hash is actually stored on p1
    point = sdk.get_point(p1["id"])
    assert point.get("content_hash") == _content_hash(content), (
        "content_hash should be stored on every new point (#80)"
    )


def test_dedup_with_extra_props(sdk):
    """#80: mixed dedup with extra props — existing point returned unchanged."""
    content = "dedup-with-props-#80"

    p1 = sdk.create_point("statement", content, credibility="gold")
    p2 = sdk.create_point("statement", content, dedup=True,
                          credibility="unverified")
    assert p2["id"] == p1["id"]
    # Gold baseline should be preserved (not overwritten by unverified)
    point = sdk.get_point(p1["id"])
    assert point.get("ep_alpha") == 10, "original gold baseline preserved"
