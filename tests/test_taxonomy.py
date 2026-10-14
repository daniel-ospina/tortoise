"""Tests for tortoise.taxonomy — P0-5/6/7 read-only entity counting tools."""
from __future__ import annotations

import inspect
import os
import shutil
import sys
import tempfile
from pathlib import Path

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
        # No negative ``assert "Document" not in result`` here: ``taxonomy()``'s
        # keys come from the hard-coded label tuple in ``tortoise/taxonomy.py``,
        # which this change does not touch, so that assertion would pass on the
        # pre-fix code too. The real pin is
        # ``test_published_keys_match_the_counted_labels`` below, which reads the
        # registry prose and does fail pre-fix.

    def test_published_keys_match_the_counted_labels(self, sdk):
        """The MCP description's key set must BE what taxonomy() counts.

        A consumer that codes to the keys in the description gets ``KeyError``
        when they disagree — the exact defect D10 left (the description kept
        ``Document`` after the label retired, #5121). Read the keys out of the
        registry description and pin them to what ``taxonomy()`` actually
        returns, so a future label retirement cannot re-stale the prose
        silently.

        The entry lives in ``RETIRED_TOOL_REGISTRY`` (the name was folded into
        ``tortoise_overview(section="taxonomy")``, #3883) — it is not a live
        advertised tool, so this is NOT a live published contract. It is pinned
        because the retired name's warning shim still serves that description to
        any caller that uses the old name, and it was the description D10
        re-staled. It is read through both registries rather than assumed live.
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


# ── the retired :Document label outside the MCP registry (#5121) ──────

class TestRetiredDocumentLabelClaims:
    """#5121: D10 retires ``:Document`` — a document is a ``:Source``.

    The MCP registry descriptions were the enumerated sites, but the same claim
    also lived in the CLI help, the public SDK docstring and the self-hosted
    quickstart. These read the real artifact (the parsed CLI help, the shipped
    docstring, the doc text) so the claim cannot come back un-noticed.
    """

    def test_create_entity_docstring_does_not_claim_a_document_node(self):
        from tortoise.sdk import TortoiseSDK

        doc = inspect.getdoc(TortoiseSDK.create_entity) or ""
        assert "Document node" not in doc, doc
        # Positive half: a merely deleted sentence would satisfy the negative
        # alone, so the docstring must also say what the document IS.
        assert "document ``:Source``" in doc, doc

    def test_file_human_approval_docstring_names_the_document_source(self):
        from tortoise.sdk import TortoiseSDK

        doc = inspect.getdoc(TortoiseSDK.file_human_approval) or ""
        assert "Object/Document id" not in doc, doc
        # Positive half: the MCP handler's copy of this parameter was already
        # corrected, so the SDK's copy must carry the same phrasing rather
        # than a deleted sentence.
        assert "url of the document Source" in doc, doc

    def test_index_directory_help_does_not_claim_a_document_node(self, capsys):
        from tortoise.__main__ import main

        with pytest.raises(SystemExit):
            main(["index", "--help"])
        # argparse hard-wraps help at the terminal width, so collapse whitespace
        # before matching the phrase.
        out = " ".join(capsys.readouterr().out.split())
        assert "Events/Documents" not in out, out
        assert "Sources + Events" in out, out

    def test_quickstart_keeps_the_file_source_and_the_document_distinct(self):
        text = (Path(__file__).resolve().parent.parent
                / "docs" / "quickstart-selfhosted.md").read_text()
        # The false collapse (#5121 P2): the file Source and the document are
        # distinct nodes — the document is a :Source keyed by its documentId
        # and linked from the file Source — so this claim must be gone.
        assert "the file's document *is* that Source" not in text
        assert "Sources/Events/Documents" not in text
        # Positive half: the corrected description must still be present.
        assert "`documentKind`" in text, "the corrected §4b description is missing"


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
