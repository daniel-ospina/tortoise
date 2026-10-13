"""Tests for tortoise.taxonomy — P0-5/6/7 read-only entity counting tools."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_tax_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    # Seed: Points with different contexts/kinds
    sdk.create_point("statement", "S1")
    sdk.create_point("decision", "D1")
    sdk.create_point("observation", "O1")
    sdk.create_point("hypothesis", "H1")
    sdk.create_point("goal", "G1")  # no context
    yield sdk
    sdk.close()
    shutil.rmtree(os.path.dirname(db_path), ignore_errors=True)


# ── taxonomy ────────────────────────────────────────────────────────

class TestTaxonomy:
    def test_keys(self, sdk):
        result = sdk.taxonomy()
        # D10 (ONTOLOGY v3.15 §4.4): :Document is retired — a document is a
        # :Source, so the label set carries ``Source``, not ``Document``.
        for label in ("Point", "Event", "Subject", "Object", "Source"):
            assert label in result
        assert "Document" not in result

    def test_published_keys_match_the_counted_labels(self, sdk):
        """The MCP description's published key set must BE what taxonomy() counts.

        The description is the published contract: a consumer that codes to its
        keys gets ``KeyError`` when they disagree — the exact defect D10 left
        (the description kept ``Document`` after the label retired, #5121).
        Read the keys out of the registry description and pin them to what
        ``taxonomy()`` actually returns, so a future label retirement cannot
        re-stale the prose silently.

        The entry lives in ``RETIRED_TOOL_REGISTRY`` (the name was folded into
        ``tortoise_overview(section="taxonomy")``, #3883), and a retired name's
        docstring is the description its warning shim serves — so it is read
        through both registries rather than assumed live.
        """
        import re

        from tortoise.tool_registry import RETIRED_TOOL_REGISTRY, TOOL_REGISTRY

        entries = [
            e for e in (*TOOL_REGISTRY, *RETIRED_TOOL_REGISTRY)
            if e.id == "surface.taxonomy"
        ]
        assert len(entries) == 1, f"expected exactly one surface.taxonomy entry, got {len(entries)}"
        block = re.search(r"\{([^}]*)\}", entries[0].description)
        assert block, f"no {{Key: N}} block in the description: {entries[0].description!r}"
        published = {part.split(":")[0].strip() for part in block.group(1).split(",")}
        assert published == set(sdk.taxonomy().keys())
        assert "Document" not in published

    def test_counts_points(self, sdk):
        result = sdk.taxonomy()
        assert result["Point"] == 5

    def test_empty_labels_return_zero(self, sdk):
        result = sdk.taxonomy()
        # No events/subjects/objects/sources in test data
        assert result["Event"] == 0
        assert result["Subject"] == 0
        assert result["Object"] == 0
        assert result["Source"] == 0


# ── list_pointkinds (replaces list_domains, #49) ─────────────────────────

class TestListPointKinds:
    def test_returns_list(self, sdk):
        result = sdk.list_pointkinds()
        assert isinstance(result, list)

    def test_counts_by_kind(self, sdk):
        sdk.create_point("strategy", "strat one")
        sdk.create_point("strategy", "strat two")
        sdk.create_point("research", "research one")
        sdk.create_point("research", "research two")
        result = sdk.list_pointkinds()
        kinds = {r["kind"]: r["count"] for r in result}
        assert kinds["strategy"] == 2
        assert kinds["research"] == 2

    def test_empty_db(self):
        db_path = os.path.join(tempfile.mkdtemp(), "empty.db")
        s = TortoiseSDK(db_path)
        try:
            assert s.list_pointkinds() == []
        finally:
            s.close()


# ── list_topics ────────────────────────────────────────────────────────

class TestListTopics:
    def test_entity_not_found(self, sdk):
        result = sdk.list_topics("nonexistent-id")
        assert "error" in result

    def test_entity_without_neighbors(self, sdk):
        p = sdk.create_point("statement", "loner")
        result = sdk.list_topics(p["id"])
        assert result["id"] == p["id"]
        assert result["pointKind"] == "statement"
        assert result["neighbors"] == []
        assert result["neighborCounts"] == {}

    def test_entity_with_neighbors(self, sdk):
        a = sdk.create_point("statement", "A")
        b = sdk.create_point("decision", "B")
        c = sdk.create_point("observation", "C")
        sdk.create_operator("IMPL", a["id"], [b["id"]])
        sdk.create_operator("NAND", a["id"], [c["id"]])

        result = sdk.list_topics(a["id"])
        assert result["id"] == a["id"]
        assert len(result["neighbors"]) == 2
        counts = result["neighborCounts"]
        assert counts["decision"] == 1
        assert counts["observation"] == 1
