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
        """GAP-08: near-duplicate content with high word overlap is caught by TF-IDF similarity.

        Requires sklearn or sentence_transformers for vector similarity.
        Gracefully skipped when neither is available (embedded dev environments).
        """
        try:
            import sklearn  # noqa: F401
        except ImportError:
            try:
                import sentence_transformers  # noqa: F401
            except ImportError:
                pytest.skip("semantic dedup requires sklearn or sentence_transformers — neither available")
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
        assert p["wing"] == "test_wing"  # #49: wing replaces context

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

    def test_reingest_unchanged_skipped(self, sdk, corpus_dir):
        # #330: byte-identical re-ingest must count as skipped, not updated
        sdk.ingest_corpus(str(corpus_dir))
        result = sdk.ingest_corpus(str(corpus_dir))
        assert result["ingested"] == 0
        assert result["updated"] == 0
        assert result["skipped"] == 3

    def test_reingest_changed_updates(self, sdk, corpus_dir):
        # #330: a changed file counts as updated (not skipped)
        sdk.ingest_corpus(str(corpus_dir))
        (corpus_dir / "doc1.md").write_text("---\ntitle: Changed\n---\nnew body\n")
        result = sdk.ingest_corpus(str(corpus_dir))
        assert result["updated"] == 1
        assert result["skipped"] == 2
        assert result["ingested"] == 0

    def test_reingest_new_doc_ingested(self, sdk, corpus_dir):
        # #330: a genuinely new doc still counts as ingested
        sdk.ingest_corpus(str(corpus_dir))
        (corpus_dir / "doc3.md").write_text("---\ntitle: Doc Three\n---\n# Three\n")
        result = sdk.ingest_corpus(str(corpus_dir))
        assert result["ingested"] == 1
        assert result["skipped"] == 3
        assert result["updated"] == 0

    def test_reingest_legacy_no_hash_backfills(self, sdk, corpus_dir):
        # #330: legacy events without a stored file_hash must update + backfill
        # (NOT crash, NOT skip) — simulate pre-upgrade data.
        sdk.ingest_corpus(str(corpus_dir))
        proj = sdk._get_proj()
        proj.g.query(
            "MATCH (e:Event {eventId:'doc1.md'}) REMOVE e.file_hash"
        )
        result = sdk.ingest_corpus(str(corpus_dir))
        assert result["updated"] == 1, f"legacy no-hash event should update+backfill, got {result}"
        assert result["skipped"] == 2
        row = proj.g.query(
            "MATCH (e:Event {eventId:'doc1.md'}) RETURN e.file_hash"
        ).result_set[0]
        assert row[0] is not None, "file_hash was not backfilled"

    def test_agentsession_reingest_unchanged_skipped(self, sdk, tmp_path):
        # #330: AgentSession mode — identical file counts as skipped
        p = tmp_path / "s.md"
        p.write_text("---\ntitle: S1\n---\ncontent here\n")
        sdk.ingest_corpus(str(tmp_path), eventKind="AgentSession")
        result = sdk.ingest_corpus(str(tmp_path), eventKind="AgentSession")
        assert result["skipped"] == 1, f"expected 1 skipped, got {result}"
        assert result["updated"] == 0

    def test_agentsession_changed_updates_with_arc(self, sdk, tmp_path):
        # #330: AgentSession mode — changed file counts as updated
        p = tmp_path / "s.md"
        p.write_text("---\ntitle: S2\n---\nphase one content\n")
        sdk.ingest_corpus(str(tmp_path), eventKind="AgentSession")
        p.write_text("---\ntitle: S2\n---\nphase two content\n")
        result = sdk.ingest_corpus(str(tmp_path), eventKind="AgentSession")
        assert result["updated"] == 1, f"expected 1 updated, got {result}"
        assert result["skipped"] == 0

    def test_agentsession_keywordless_unchanged_is_skipped(self, sdk, tmp_path):
        # #330: an unchanged file whose stored event lacks keywords must count
        # as skipped (enrichment is a no-op), not updated. Content is chosen so
        # keyword extraction returns [] (only 1-char/stopword tokens).
        content = "a b c d e f g"
        p = tmp_path / "s.md"
        p.write_text(content)
        sdk.ingest_corpus(str(tmp_path), eventKind="AgentSession")
        proj = sdk._get_proj()
        import hashlib as _h
        h = _h.sha256(content.encode()).hexdigest()
        row = proj.g.query(
            "MATCH (e:Event) WHERE e.file_hash = $h RETURN e.keywords",
            params={"h": h},
        ).result_set
        assert row and not row[0][0], "fixture failed: event should have no keywords"
        r2 = sdk.ingest_corpus(str(tmp_path), eventKind="AgentSession")
        assert r2["skipped"] == 1, f"keyword-less unchanged file should skip, got {r2}"
        assert r2["updated"] == 0

    def test_ingest_agentsession_progress_resume_skips(self, sdk, tmp_path):
        # #330: progress-file resume counts completed files as skipped (no re-write)
        import json as _json
        p = tmp_path / "s.md"
        p.write_text("---\ntitle: S3\n---\nresume content\n")
        prog = str(tmp_path / "progress.json")
        r1 = sdk.ingest_corpus(str(tmp_path), eventKind="AgentSession", progress_file=prog)
        assert r1["ingested"] == 1
        r2 = sdk.ingest_corpus(str(tmp_path), eventKind="AgentSession", progress_file=prog)
        assert r2["skipped"] == 1, f"resumed files should be skipped, got {r2}"
        assert r2["updated"] == 0
        # Progress file still tracks the completed file
        data = _json.load(open(prog))
        assert str(p) in data["completed_files"]


# ── create_point dedup regression (#80) ─────────────────────────

def test_create_point_dedup_without_first_dedup(sdk):
    """#80: dedup=True must find points created WITHOUT dedup.

    Before the fix, content_hash was only persisted when dedup=True was
    passed.  A point created without dedup had no content_hash, so a
    later call with dedup=True would silently create a duplicate."""
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
    from tortoise.sdk import _content_hash
    assert point.get("content_hash") == _content_hash(content), (
        "content_hash should be stored on every new point (#80)"
    )


# ── #330: checkpoint semantic-dedup failure observability ────────────────


class TestCheckpointDedupObservability:
    """#330: checkpoint() must not swallow semantic-dedup failures silently —
    log them (INFO for expected missing-deps, WARNING otherwise) while
    keeping the fail-open hash-only fallback."""

    def test_semantic_dedup_failure_falls_back(self, sdk, monkeypatch):
        def boom(candidates, threshold):
            raise RuntimeError("simulated dedup backend failure")
        monkeypatch.setattr(sdk, "_semantic_dedup", boom)
        result = sdk.checkpoint(
            [{"wing": "p", "room": "r", "content": "unique content X"}]
        )
        assert result["filed"] == 1      # hash-only fallback worked
        assert result["duplicates"] == 0

    def test_semantic_dedup_failure_is_logged(self, sdk, monkeypatch, caplog):
        import logging

        def boom(candidates, threshold):
            raise RuntimeError("simulated dedup backend failure")
        monkeypatch.setattr(sdk, "_semantic_dedup", boom)
        with caplog.at_level(logging.WARNING, logger="tortoise.sdk"):
            sdk.checkpoint(
                [{"wing": "p", "room": "r", "content": "unique content Y"}]
            )
        assert any("dedup" in r.message for r in caplog.records), (
            "semantic-dedup failure was swallowed silently — no log record"
        )
