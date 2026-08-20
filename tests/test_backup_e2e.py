"""GAP-06 #6992: Backup/restore E2E test.

Verify: backup command copies events.jsonl + triggers BGSAVE.
Restore replays log into fresh graph.
Test: create 10 Points → backup → delete graph → restore → verify all 10
Points exist with same properties.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tortoise.backup import backup, restore  # noqa: I001
from tortoise.log import EventLog
from tortoise.projection import FalkorProjection


# ── helpers ────────────────────────────────────────────────────────────────


def _make_point(i: int) -> dict:
    """Create a PointAdded event with rich, distinct properties.

    ``context``/``extractedFrom`` are intentionally NOT asserted as node props:
    context was removed by the Phase 1 stop-writes (#49) and extractedFrom is
    an edge (Point → Source), not a node property.
    """
    return {
        "type": "PointAdded",
        "point": {
            "id": f"pt-{i:03d}",
            "content": f"Content for point {i}",
            "confidence": round(0.05 * (i + 1), 3),
            "pointKind": ["claim", "observation", "decision"][i % 3],
            "extractedFrom": f"doc-{i % 3}.md",
            "validFrom": f"2026-07-{(i % 28) + 1:02d}T10:00:00Z",
            "createdAt": f"2026-07-{(i % 28) + 1:02d}T10:00:00Z",
        },
    }


# ── E2E test ───────────────────────────────────────────────────────────────

def test_backup_restore_e2e():
    """Full round-trip: create Points → backup → delete graph → restore → verify."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "tortoise.db")
        events_path = os.path.join(tmpdir, "events.jsonl")

        # ── Step 1: Create 10 Points + project into FalkorDB ──────
        points_data = [_make_point(i) for i in range(10)]

        log = EventLog(events_path)
        for ev in points_data:
            log.append(ev)

        proj = FalkorProjection(db_path)
        for ev in points_data:
            proj.apply(ev)

        # Verify initial projection (before close — graph is in-memory)
        count = proj.g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
        assert count == 10, f"Expected 10 Points, got {count}"

        # Close — writes DB file to disk for backup to copy
        proj.close()

        # ── Step 2: Backup — verify files copied + BGSAVE ─────────
        backup_dir = os.path.join(tmpdir, "backups", "manual")

        with patch("tortoise.backup._bgsave") as mock_bgsave:
            target = backup(
                db_path=db_path,
                events_path=events_path,
                target_dir=backup_dir,
            )
            mock_bgsave.assert_called_once()

        assert target.exists(), "Backup directory not created"
        assert (target / "events.jsonl").exists(), "events.jsonl not backed up"
        assert (target / "tortoise.db").exists(), "tortoise.db not backed up"

        # Verify manifest
        manifest_path = target / "manifest.json"
        assert manifest_path.exists(), "manifest.json not created"
        manifest = json.loads(manifest_path.read_text())
        assert manifest["db"] == "tortoise.db"
        assert manifest["events"] == "events.jsonl"
        assert "backed_up_at" in manifest

        # Verify backed-up events.jsonl has same content (JSON-parse for
        # robustness against key-ordering differences)
        orig_events = [json.loads(ln) for ln in
                       Path(events_path).read_text().strip().splitlines()]
        backup_events = [json.loads(ln) for ln in
                         (target / "events.jsonl").read_text().strip().splitlines()]
        assert orig_events == backup_events, "Backed-up events differ from original"

        # ── Step 3: Delete DB from backup so restore MUST replay events.
        #    This verifies into_falkor=True independently of the file-copied DB.
        (target / "tortoise.db").unlink()

        # Use fresh DB + events destination paths
        restored_db = os.path.join(tmpdir, "restored.db")
        restored_events = os.path.join(tmpdir, "restored_events.jsonl")

        # ── Step 4: Restore with into_falkor=True ─────────────
        result = restore(
            str(target),
            db_path=restored_db,
            events_path=restored_events,
            into_falkor=True,
        )

        assert result["status"] == "ok", f"Restore failed: {result}"
        assert result["events"] == 10, f"Expected 10 events, got {result['events']}"

        # Verify restored events.jsonl was created and matches original
        assert Path(restored_events).exists(), "restored events.jsonl not created"
        restored_evs = [json.loads(ln) for ln in
                        Path(restored_events).read_text().strip().splitlines()]
        assert len(restored_evs) == 10, \
            f"Expected 10 events in restored log, got {len(restored_evs)}"
        assert restored_evs == orig_events, \
            "Restored events differ from original"

        # ── Step 5: Verify all 10 Points exist with same properties
        proj2 = FalkorProjection(restored_db)
        try:
            total = proj2.g.query(
                "MATCH (n:Point) RETURN count(n)"
            ).result_set[0][0]
            assert total == 10, f"Expected 10 Points after restore, got {total}"

            for i in range(10):
                pid = f"pt-{i:03d}"
                rows = proj2.g.query(
                    "MATCH (n:Point {id:$id}) RETURN n",
                    params={"id": pid},
                ).result_set
                assert len(rows) == 1, f"Point {pid} not found"

                node = rows[0][0]
                props = node.properties
                expected = points_data[i]["point"]
                assert props["content"] == expected["content"], \
                    f"{pid}: content mismatch"
                assert props["confidence"] == expected["confidence"], \
                    f"{pid}: confidence mismatch"
                assert props["pointKind"] == expected["pointKind"], \
                    f"{pid}: pointKind mismatch"
                assert props["validFrom"] == expected["validFrom"], \
                    f"{pid}: validFrom mismatch"
        finally:
            proj2.close()
