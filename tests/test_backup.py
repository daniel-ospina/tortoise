"""Tests for backup — backup/restore round-trip."""
from __future__ import annotations  # noqa: I001

import json
import os
import tempfile
from pathlib import Path

import pytest  # noqa: F401
from tortoise.backup import backup, restore


def test_backup_creates_timestamped_dir():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create fake events.jsonl
        events_path = os.path.join(tmpdir, "events.jsonl")
        with open(events_path, "w") as f:
            f.write(json.dumps({"type": "PointAdded", "point": {"id": "p1", "content": "hello"}}) + "\n")

        db_path = os.path.join(tmpdir, "tortoise.db")
        Path(db_path).write_text("fake db")

        target = backup(db_path=db_path, events_path=events_path,
                        target_dir=os.path.join(tmpdir, "backups", "manual"))

        assert target.exists()
        assert (target / "events.jsonl").exists()
        assert (target / "tortoise.db").exists()
        manifest = target / "manifest.json"
        assert manifest.exists()
        data = json.loads(manifest.read_text())
        assert data["db"] == "tortoise.db"
        assert data["events"] == "events.jsonl"
        assert "backed_up_at" in data


def test_restore_replays_events():
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create backup
        src = Path(tmpdir) / "backup_src"
        src.mkdir()
        (src / "events.jsonl").write_text(
            json.dumps({"type": "PointAdded", "point": {"id": "p1", "content": "test"}}) + "\n"
        )
        (src / "manifest.json").write_text('{"backed_up_at":"2026-01-01","db":"tortoise.db","events":"events.jsonl"}')

        dst_events = os.path.join(tmpdir, "restored.jsonl")
        dst_db = os.path.join(tmpdir, "restored.db")

        result = restore(str(src), db_path=dst_db, events_path=dst_events)
        assert result["status"] == "ok"
        assert result["events"] == 1
        assert os.path.exists(dst_events)


def test_restore_missing_dir():
    result = restore("/nonexistent/backup", db_path="/tmp/unused.db")
    assert result["status"].startswith("error")


def test_restore_rdb_first_when_snapshot_present(monkeypatch):
    """into_falkor restores via RDB snapshot when present (#114)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "backup_src"
        src.mkdir()
        (src / "events.jsonl").write_text(
            json.dumps({"type": "PointAdded", "point": {"id": "p1", "content": "event-only"}}) + "\n"
        )
        (src / "manifest.json").write_text('{"backed_up_at":"2026-01-01","db":"tortoise.db","events":"events.jsonl"}')
        # A non-empty RDB stub — restore must use it, not replay JSONL
        (src / "tortoise.db").write_text("rdb-snapshot-data")

        dst_events = os.path.join(tmpdir, "restored.jsonl")
        dst_db = os.path.join(tmpdir, "restored.db")

        # Fake a projection whose snapshot has data (count > 0) so the
        # RDB-first path returns without replaying JSONL.
        class _FakeProj:
            def __init__(self, db_path):
                class _G:
                    def query(self, cypher, params=None):
                        class _R:
                            result_set = [[5]]  # noqa: RUF012
                        return _R()
                self.g = _G()
            def close(self):
                pass

        import tortoise.projection as proj_mod
        monkeypatch.setattr(proj_mod, "FalkorProjection", _FakeProj)

        result = restore(str(src), db_path=dst_db, events_path=dst_events, into_falkor=True)
        assert result["status"] == "ok"
        assert result.get("restored_via") == "rdb"
        assert os.path.exists(dst_db)


def test_restore_jsonl_fallback_when_rdb_empty(monkeypatch):
    """into_falkor falls back to JSONL replay when the RDB snapshot is empty (#114)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        src = Path(tmpdir) / "backup_src"
        src.mkdir()
        (src / "events.jsonl").write_text(
            json.dumps({"type": "PointAdded", "point": {"id": "p1", "content": "event-only"}}) + "\n"
        )
        (src / "manifest.json").write_text('{"backed_up_at":"2026-01-01","db":"tortoise.db","events":"events.jsonl"}')
        (src / "tortoise.db").write_text("rdb-snapshot-data")

        dst_events = os.path.join(tmpdir, "restored.jsonl")
        dst_db = os.path.join(tmpdir, "restored.db")

        class _EmptyProj:
            def __init__(self, db_path):
                class _G:
                    def query(self, cypher, params=None):
                        class _R:
                            result_set = [[0]]  # noqa: RUF012
                        return _R()
                self.g = _G()
            def apply(self, ev):
                pass
            def close(self):
                pass

        import tortoise.projection as proj_mod
        monkeypatch.setattr(proj_mod, "FalkorProjection", _EmptyProj)

        result = restore(str(src), db_path=dst_db, events_path=dst_events, into_falkor=True)
        assert result["status"] == "ok"
        # Empty RDB → fell through to JSONL replay → no restored_via=rdb
        assert result.get("restored_via") is None


# ── #331: legacy-manifest embedded.db fallback (NameError regression) ──

def test_restore_legacy_manifest_embedded_db_fallback(tmp_path):
    """#331 regression: restore with a legacy manifest (no 'db' key) must
    fall back to embedded.db WITHOUT crashing.

    Pre-fix this path raised NameError inside restore() (logger was used
    before it was defined); the fallback must copy embedded.db to the target.
    """
    src = tmp_path / "backup"
    src.mkdir()
    (src / "events.jsonl").write_text(
        '{"type": "PointAdded", "point": {"id": "p1", "content": "x"}}\n'
    )
    # Legacy manifest: no 'db' key → restore must fall back to embedded.db
    (src / "manifest.json").write_text(
        json.dumps({"backed_up_at": "20260101T000000Z"})
    )
    (src / "embedded.db").write_bytes(b"stub-embedded-db")

    dst = tmp_path / "dst"
    dst.mkdir()
    result = restore(str(src), db_path=str(dst / "tortoise.db"),
                     events_path=str(dst / "events.jsonl"))
    assert result["status"] == "ok"
    assert (dst / "tortoise.db").exists(), \
        "embedded.db fallback must be copied to the target db path"
    assert (dst / "tortoise.db").read_bytes() == b"stub-embedded-db"
