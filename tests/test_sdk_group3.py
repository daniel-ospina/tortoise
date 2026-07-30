"""Tests for P0 Group 3 SDK methods: checkpoint, diary, status, ingest_corpus.

Runnable with: .venv/bin/python -m pytest tests/test_sdk_group3.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.sdk import TortoiseSDK


@pytest.fixture
def sdk():
    """SDK with temp database. Closed after test."""
    db_path = os.path.join(tempfile.mkdtemp(prefix="tortoise_g3_test_"), "test.db")
    sdk = TortoiseSDK(db_path)
    yield sdk
    sdk.close()


# ── checkout ────────────────────────────────────────────────────

class TestCheckpoint:
    def test_files_all_new(self, sdk):
        items = [
            {"wing": "proj", "room": "decisions", "content": "decision A"},
            {"wing": "proj", "room": "backend", "content": "backend note B"},
            {"wing": "other", "room": "meetings", "content": "meeting note C"},
        ]
        result = sdk.checkpoint(items)
        assert result["filed"] == 3
        assert result["duplicates"] == 0

        # Verify points exist
        points = sdk.query(kind="checkpoint-item")
        assert len(points) == 3

    def test_dedup_duplicates(self, sdk):
        items = [
            {"wing": "p", "room": "r", "content": "same content"},
            {"wing": "p", "room": "r", "content": "same content"},
        ]
        result = sdk.checkpoint(items)
        assert result["filed"] == 1
        assert result["duplicates"] == 1

        points = sdk.query(kind="checkpoint-item")
        assert len(points) == 1

    def test_empty_items(self, sdk):
        result = sdk.checkpoint([])
        assert result == {"filed": 0, "duplicates": 0}

    def test_emits_provenance_events(self, sdk):
        """GAP-07: checkpoint emits EventRecorded for each filed item."""
        items = [
            {"wing": "proj", "room": "decisions", "content": "decision A"},
            {"wing": "proj", "room": "backend", "content": "backend note B"},
        ]
        result = sdk.checkpoint(items, agent_name="pi-agent")
        assert result["filed"] == 2

        proj = sdk._get_proj()
        events = proj.g.query(
            "MATCH (e:Event {eventKind:'pointAdded', subject:'pi-agent'}) "
            "RETURN e.eventKind, e.subject, e.object, e.startedAt, e.eventId"
        ).result_set
        assert len(events) == 2
        event_ids = []
        for ev in events:
            assert ev[0] == "pointAdded"
            assert ev[1] == "pi-agent"
            assert ev[2]  # object (point ID) is non-empty
            assert ev[3]  # startedAt is non-empty
            assert ev[4]  # eventId is non-empty
            event_ids.append(ev[4])
        assert event_ids[0] != event_ids[1], "eventIds must be distinct"

    def test_dedup_no_duplicate_events(self, sdk):
        """GAP-07: duplicate content doesn't create duplicate provenance events."""
        items = [
            {"wing": "p", "room": "r", "content": "same content"},
            {"wing": "p", "room": "r", "content": "same content"},
        ]
        result = sdk.checkpoint(items, agent_name="test-agent")
        assert result["filed"] == 1
        assert result["duplicates"] == 1

        proj = sdk._get_proj()
        events = proj.g.query(
            "MATCH (e:Event {eventKind:'pointAdded', subject:'test-agent'}) "
            "RETURN count(e)"
        ).result_set
        assert events[0][0] == 1  # only one event for the single filed item

    # ── GAP-08: Semantic dedup ──────────────────────────────────

    def test_semantic_dedup_catches_near_duplicates(self, sdk):
        """GAP-08: near-duplicate content with high word overlap is caught by TF-IDF similarity."""
        original = "deploy the new feature to production servers tonight"
        sdk.checkpoint([{"content": original}])
        # Near-duplicate: one word changed, same structure
        result = sdk.checkpoint(
            [{"content": "deploy the new feature to production servers today"}],
            threshold=0.7,
        )
        assert result["duplicates"] == 1
        assert result["filed"] == 0

    def test_semantic_dedup_threshold_disables(self, sdk):
        """GAP-08: threshold=1.0 disables semantic dedup — hash-only fallback."""
        original = "original content about deployment strategy"
        sdk.checkpoint([{"content": original}])
        # Similar but not exact — should file since semantic dedup is off
        result = sdk.checkpoint(
            [{"content": "original content about deployment strategies"}],
            threshold=1.0,
        )
        assert result["filed"] == 1
        assert result["duplicates"] == 0
        # Exact dup still caught by hash
        result2 = sdk.checkpoint(
            [{"content": "original content about deployment strategies"}],
            threshold=1.0,
        )
        assert result2["duplicates"] == 1


# ── diary ────────────────────────────────────────────────────────

class TestDiary:
    def test_write_read(self, sdk):
        p = sdk.diary_write("pi-agent", "SESSION:2026-07-19|built.checkpoint|★★★",
                            topic="general", wing="test_wing")
        assert p["pointKind"] == "diary"
        assert p["authoredBy"] == "pi-agent"
        assert p["context"] == "test_wing"

        entries = sdk.diary_read("pi-agent", last_n=10, wing="test_wing")
        assert len(entries) == 1
        assert entries[0]["pointKind"] == "diary"

    def test_read_empty(self, sdk):
        entries = sdk.diary_read("nobody", last_n=5)
        assert entries == []

    def test_multi_agent_isolation(self, sdk):
        sdk.diary_write("agent-a", "entry a1")
        sdk.diary_write("agent-b", "entry b1")

        a_entries = sdk.diary_read("agent-a", last_n=10)
        b_entries = sdk.diary_read("agent-b", last_n=10)

        assert len(a_entries) == 1
        assert len(b_entries) == 1
        assert a_entries[0]["authoredBy"] == "agent-a"
        assert b_entries[0]["authoredBy"] == "agent-b"

    def test_read_respects_last_n(self, sdk):
        for i in range(5):
            sdk.diary_write("agent-c", f"entry {i}")
        entries = sdk.diary_read("agent-c", last_n=3)
        assert len(entries) == 3


# ── status ───────────────────────────────────────────────────────

class TestStatus:
    def test_connected_and_counts(self, sdk):
        sdk.create_point("statement", "hello")
        result = sdk.status()
        assert result["connected"] is True
        assert "Point" in result["counts"]
        assert result["counts"]["Point"] >= 1
        assert result["total_entities"] >= 1


# ── ingest_corpus ────────────────────────────────────────────────

class TestIngestCorpus:
    @pytest.fixture
    def corpus_dir(self, tmp_path):
        """Create a temp directory with .md files that have YAML frontmatter."""
        doc1 = tmp_path / "doc1.md"
        doc1.write_text("""---
title: Test Document One
type: plan
domain: test-domain
doc_status: draft
version: "1.0"
authoredBy: tester
---

# Test Document One

Some content here.
""")
        doc2 = tmp_path / "nested" / "doc2.md"
        doc2.parent.mkdir()
        doc2.write_text("""---
title: Test Document Two
type: reference
domain: other-domain
---

# Test Document Two

More content.
""")
        no_fm = tmp_path / "nofm.md"
        no_fm.write_text("# No frontmatter\n\nJust content.")
        return tmp_path

    def test_ingests_all(self, sdk, corpus_dir):
        result = sdk.ingest_corpus(str(corpus_dir))
        assert result["ingested"] == 3
        assert result["updated"] == 0
        assert result["skipped"] == 0

    def test_reingest_updates(self, sdk, corpus_dir):
        sdk.ingest_corpus(str(corpus_dir))
        result = sdk.ingest_corpus(str(corpus_dir))
        # All should be updates since docs already exist
        assert result["updated"] == 3
        assert result["ingested"] == 0
