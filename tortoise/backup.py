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

from tortoise.config import is_db_uri

logger = logging.getLogger(__name__)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017


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

    # Trigger FalkorDB BGSAVE if available. Best-effort — a snapshot failure
    # never aborts the file backup — but the outcome is logged AND recorded in
    # the manifest so a silent no-op is impossible (#2974). If the CLI target
    # is itself a URI, snapshot THAT instance; otherwise the configured
    # TORTOISE_DB_URI is used (never a hardcoded localhost).
    bgsave = _bgsave(db_path if is_db_uri(db_path) else None)

    # Write manifest
    manifest = target / "manifest.json"
    import json
    manifest.write_text(json.dumps({
        "backed_up_at": _timestamp(),
        "db": db.name,
        "events": ev.name,
        "bgsave": bgsave,
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
        try:  # noqa: SIM105
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
        # #915 — with AOF enabled, Redis loads the AOF in preference to the
        # RDB. A stale appendonlydir/ at the target path would make this
        # restore silently serve the OLD live graph instead of the snapshot.
        # Restore semantics = "the restored snapshot wins".
        from tortoise.projection import remove_stale_aof
        remove_stale_aof(db_path)
        shutil.copy2(db_file, db_path)

    # Count events
    with open(events_file) as f:
        count = sum(1 for _ in f)

    # Restore into FalkorDB if requested
    if into_falkor:
        from tortoise.projection import FalkorProjection  # noqa: I001
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


def _bgsave(uri: str | None = None) -> str:
    """Trigger a FalkorDB BGSAVE on the CONFIGURED database (#2974).

    The endpoint is resolved through the same canonical resolver the product
    uses — ``TORTOISE_DB_URI`` parsed by ``tortoise.projection.
    resolve_db_endpoint`` (the derivation ``FalkorProjection.from_uri`` also
    uses). The previous implementation dialed a hardcoded
    ``localhost:16379`` from the embedded-mode ``FALKORDB_HOST``/``FALKORDB_
    PORT`` defaults, which hosted production never sets, so the BGSAVE never
    reached the real instance and the swallowed exception hid it.

    Best-effort semantics are preserved — a snapshot failure must NOT abort
    the file backup — but it is never silent (#2820): every failure is logged
    at ERROR and returned so ``backup()`` records it in the manifest.

    Args:
        uri: explicit database URI (the CLI ``--db`` target when it is a
            ``docker://``/``redis://``/``rediss://`` URI). When None, the
            configured ``TORTOISE_DB_URI`` is used.

    Returns:
        ``"ok"``, ``"skipped: <why>"`` (embedded — no server endpoint to
        snapshot), or ``"failed: <why>"``.
    """
    target = (uri or os.environ.get("TORTOISE_DB_URI", "") or "").strip()
    if not target:
        # Embedded mode: redislite owns the on-disk RDB and the file copy in
        # backup() is the durable artifact — nothing to BGSAVE. Not an error.
        logger.info(
            "BGSAVE skipped — no server URI configured (embedded mode); "
            "the copied DB file is the durable artifact")
        return "skipped: no server URI configured (embedded mode)"
    if "://" in target and not is_db_uri(target):
        # A URI was configured but its scheme is unsupported — the endpoint is
        # UNRESOLVABLE. This is a misconfiguration, never a silent skip.
        scheme = target.split("://", 1)[0]
        reason = (
            f"unsupported DB URI scheme {scheme!r} (expected docker://, "
            f"redis://, or rediss://) — snapshot endpoint unresolvable")
        logger.error("BGSAVE failed — %s", reason)
        return f"failed: {reason}"
    if not is_db_uri(target):
        # TORTOISE_DB_URI is a file path (backward-compat embedded mode).
        logger.info("BGSAVE skipped — configured DB is an embedded file path")
        return "skipped: embedded DB path"
    try:
        from tortoise.projection import resolve_db_endpoint
        endpoint = resolve_db_endpoint(target)
    except Exception as e:
        reason = f"could not resolve the DB endpoint: {e}"
        logger.error("BGSAVE failed — %s", reason, exc_info=True)
        return f"failed: {reason}"
    try:
        from falkordb import FalkorDB
        db = FalkorDB(host=endpoint.host, port=endpoint.port,
                      username=endpoint.username, password=endpoint.password,
                      ssl=endpoint.ssl,
                      socket_connect_timeout=5, socket_timeout=10)
        db.connection.execute_command("BGSAVE")
    except Exception as e:
        reason = f"BGSAVE against {endpoint.host}:{endpoint.port} failed: {e}"
        logger.error("%s", reason, exc_info=True)
        return f"failed: {reason}"
    logger.info("BGSAVE triggered at %s:%s", endpoint.host, endpoint.port)
    return "ok"
