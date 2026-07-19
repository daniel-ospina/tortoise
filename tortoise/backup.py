"""P1-10 #6982: Backup/restore — JSONL archiver + FalkorDB BGSAVE.

Backup: copies events.jsonl to timestamped dir, triggers BGSAVE on FalkorDB.
Restore: replays backup JSONL into a fresh projection.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup(db_path: str = "tortoise.db", events_path: str = "events.jsonl",
           target_dir: str | None = None) -> Path:
    """Copy database + event log to a timestamped backup directory.

    Returns the backup directory path.
    """
    if target_dir is None:
        target_dir = f"backups/{_timestamp()}"

    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    # Copy FalkorDB
    db = Path(db_path)
    if db.exists():
        shutil.copy2(db, target / db.name)

    # Copy event log
    ev = Path(events_path)
    if ev.exists():
        shutil.copy2(ev, target / ev.name)

    # Trigger FalkorDB BGSAVE if available
    _bgsave()

    # Write manifest
    manifest = target / "manifest.json"
    import json
    manifest.write_text(json.dumps({
        "backed_up_at": _timestamp(),
        "db": db.name,
        "events": ev.name,
    }, indent=2))

    return target


def restore(backup_dir: str, db_path: str = "tortoise.db",
            events_path: str = "events.jsonl", into_falkor: bool = False) -> dict:
    """Restore from backup directory. Replays events into a fresh projection.

    Returns {events, status}.
    """
    source = Path(backup_dir)
    if not source.exists():
        return {"events": 0, "status": "error: backup dir not found"}

    manifest_file = source / "manifest.json"
    events_file = source / "events.jsonl"
    db_file = source / "tortoise.db"

    if not events_file.exists():
        return {"events": 0, "status": "error: no events.jsonl in backup"}

    # Copy files to target
    shutil.copy2(events_file, events_path)
    if db_file.exists():
        shutil.copy2(db_file, db_path)

    # Count events
    count = sum(1 for _ in open(events_file))

    # Replay into FalkorDB if requested
    if into_falkor:
        from tortoise.projection import FalkorProjection
        from tortoise.log import EventLog
        proj = FalkorProjection(db_path)
        try:
            for ev in EventLog(events_path).read_all():
                proj.apply(ev)
        finally:
            proj.close()

    return {"events": count, "status": "ok"}


def _bgsave() -> None:
    """Trigger FalkorDB BGSAVE if connected."""
    try:
        from falkordb import FalkorDB
        db = FalkorDB(host="localhost", port=int(os.environ.get("FALKORDB_PORT", "6379")))
        db.connection.execute_command("BGSAVE")
    except Exception:
        pass  # ponytail: embedded/redislite doesn't support BGSAVE, skip
