"""P1-10 #6982: Backup/restore — JSONL archiver + FalkorDB BGSAVE.

Backup: copies events.jsonl to timestamped dir, triggers BGSAVE on FalkorDB.
Restore: replays backup JSONL into a fresh projection.
"""
from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def backup(db_path: str, events_path: str = "events.jsonl",
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


def restore(backup_dir: str, db_path: str,
            events_path: str = "events.jsonl", into_falkor: bool = False) -> dict:
    """Restore from backup directory. Replays events into a fresh projection.

    Returns {events, status}.

    Event-sourcing contract (#114): when the backup contains a FalkorDB
    snapshot (tortoise.db — BGSAVE RDB), into_falkor mode opens that
    snapshot directly. The RDB is the complete graph state INCLUDING
    SDK-created points (which never appear in events.jsonl, since the SDK
    writes via Cypher). Replaying only events.jsonl would silently drop
    every SDK-created point. RDB-first restore preserves the full graph;
    JSONL replay is the fallback when no snapshot exists.
    """
    source = Path(backup_dir)
    if not source.exists():
        return {"events": 0, "status": "error: backup dir not found"}

    manifest_file = source / "manifest.json"
    events_file = source / "events.jsonl"
    # Read DB filename from manifest (issue #176, plan Task 11): pre-migration
    # backups may have embedded.db in their manifest — never hardcode.
    import json as _json
    _db_name = "tortoise.db"
    if manifest_file.exists():
        try:
            _db_name = _json.loads(manifest_file.read_text()).get("db", "tortoise.db")
        except Exception:
            pass
    db_file = source / _db_name
    # Graceful fallback for pre-migration backups that lack the manifest 'db'
    # key (embedded.db was the legacy filename). Also try scanning for any
    # *.db file if the manifest-derived name doesn't exist.
    if not db_file.exists():
        fallback = source / "embedded.db"
        if fallback.exists():
            logger.warning(
                "manifest db %r not found — falling back to embedded.db in %s",
                _db_name, source)
            db_file = fallback
            _db_name = "embedded.db"
        else:
            # Last resort: scan for any *.db file in the backup dir
            db_candidates = sorted(source.glob("*.db"))
            if db_candidates:
                logger.warning(
                    "manifest db %r not found, embedded.db not found — "
                    "falling back to %s", _db_name, db_candidates[0].name)
                db_file = db_candidates[0]
                _db_name = db_file.name

    if not events_file.exists():
        return {"events": 0, "status": "error: no events.jsonl in backup"}

    # Copy files to target
    shutil.copy2(events_file, events_path)
    if db_file.exists():
        shutil.copy2(db_file, db_path)

    # Count events
    with open(events_file) as f:
        count = sum(1 for _ in f)

    # Restore into FalkorDB if requested
    if into_falkor:
        from tortoise.projection import FalkorProjection
        from tortoise.log import EventLog
        # RDB-first: open the snapshot directly — it holds the full graph
        # incl. SDK-created points that never made it into events.jsonl.
        if db_file.exists():
            proj = FalkorProjection(db_path)
            try:
                # Verify the snapshot actually has data; if the RDB is a
                # stub/empty, fall through to JSONL replay below.
                rows = proj.g.query("MATCH (n) RETURN count(n)").result_set
                if rows and rows[0][0]:
                    return {"events": count, "status": "ok", "restored_via": "rdb"}
            finally:
                proj.close()
        # JSONL replay fallback (no RDB, or RDB was empty)
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
        db = FalkorDB(host=os.environ.get("FALKORDB_HOST", "localhost"), port=int(os.environ.get("FALKORDB_PORT", "16379")))
        db.connection.execute_command("BGSAVE")
    except Exception:
        pass  # ponytail: embedded/redislite doesn't support BGSAVE, skip
