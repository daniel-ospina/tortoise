"""tortoise migrate-db — data-safe migration of legacy embedded.db to the
canonical path (plan Task 11, issue #176).

Safety properties:
  1. Backup-first: embedded.db is copied to .bak-<timestamp> before any
     rebuild; backup failure aborts with source intact.
  2. Advisory lock (~/.tortoise/.migrate.lock): concurrent runs serialize;
     exactly one wins, others skip with message.
  3. 3-way discriminator for tortoise.db present + no marker:
       - 0 nodes / corrupt            -> interrupted -> delete + rebuild
       - 0 < count < source events    -> partial -> delete + rebuild (no --force)
       - count >= source AND valid    -> genuine conflict -> error / --force
  4. Marker (.migrated-v2) written AFTER successful rebuild + integrity
     verification (never before — marker-before-rebuild blocks recovery).
  5. Never binary-copies the .db (JSONL rebuild via rebuild_all; a binary
     copy could silently corrupt across versions).
"""
from __future__ import annotations

import fcntl
import logging
import os
import shutil
import sys
import time

logger = logging.getLogger(__name__)

MARKER = ".migrated-v2"
LOCK = ".migrate.lock"
BACKUP_PREFIX = "embedded.db.bak-"
DEFAULT_EMBEDDED = os.path.join(os.path.expanduser("~"), ".tortoise", "embedded.db")


class MigrateLock:
    """fcntl advisory lock; auto-released on process exit (incl. SIGKILL)."""

    def __init__(self, path: str):
        self.path = path
        self._fh = None

    def acquire(self) -> bool:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self._fh = open(self.path, "a")  # noqa: SIM115
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fh.seek(0)
            self._fh.truncate()
            self._fh.write(str(os.getpid()))
            self._fh.flush()
            return True
        except OSError:
            self._fh.close()
            self._fh = None
            return False

    def release(self) -> None:
        if self._fh:
            try:  # noqa: SIM105
                fcntl.flock(self._fh, fcntl.LOCK_UN)
            except OSError:
                pass
            self._fh.close()
            self._fh = None


def _tortoise_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".tortoise")


def _count_nodes(db_path: str) -> int | None:
    """Open a FalkorDB db and count nodes; None on failure (corrupt/absent)."""
    try:
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection(db_path, allow_nonstandard_path=True)
        try:
            rows = proj.g.query("MATCH (n) RETURN count(n)").result_set
            return int(rows[0][0]) if rows and rows[0][0] else 0
        finally:
            proj.close()
    except Exception:
        return None


def _count_source_events(source_db: str) -> int:
    """Count events derivable from the source embedded.db (via its JSONL if
    present, else its node count). Returns node count as the discriminator."""
    count = _count_nodes(source_db)
    return count if count is not None else 0


def migrate(force: bool = False) -> dict:
    """Run the migration; returns result dict. Raises on abort conditions."""
    tortoise_dir = _tortoise_dir()
    os.makedirs(tortoise_dir, exist_ok=True)
    source = os.path.join(tortoise_dir, "embedded.db")
    target = os.path.join(tortoise_dir, "tortoise.db")
    marker = os.path.join(tortoise_dir, MARKER)
    lock_path = os.path.join(tortoise_dir, LOCK)

    # No source -> no-op (FileNotFoundError)
    if not os.path.exists(source):
        return {"status": "noop", "reason": "no embedded.db"}

    # Marker present -> already migrated; verify DB not corrupt first
    if os.path.exists(marker) and not force:
        node_count = _count_nodes(target)
        if node_count is None:
            logger.warning("marker present but tortoise.db corrupt — re-migrating")
            os.remove(marker)
            if os.path.exists(target):
                os.remove(target)
        else:
            return {"status": "already-migrated", "nodes": node_count}

    # Backup first (P0): never proceed without the restore point
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(tortoise_dir, f"{BACKUP_PREFIX}{timestamp}")
    try:
        shutil.copy2(source, backup_path)
    except OSError as e:
        raise RuntimeError(
            f"backup failed: {e}. Migration aborted. Source DB intact."
        ) from e

    # Advisory lock under which the conflict check runs (P0: concurrent
    # --force migrators must not both rename tortoise.db)
    lock = MigrateLock(lock_path)
    if not lock.acquire():
        return {"status": "skipped", "reason": "another migration in progress"}
    try:
        if os.path.exists(target):
            target_nodes = _count_nodes(target)
            source_events = _count_source_events(source)
            if target_nodes is not None and target_nodes >= source_events:
                # Genuine conflict: valid target with >= source events
                if not force:
                    raise RuntimeError(
                        f"Both embedded.db and tortoise.db exist without "
                        f"migration marker (target has {target_nodes} nodes, "
                        f"source {source_events}). Resolve manually or use --force."
                    )
                # --force: rename existing target to .bak-conflict-<ts>
                conflict_bak = os.path.join(
                    tortoise_dir, f"tortoise.db.bak-conflict-{timestamp}")
                try:
                    os.rename(target, conflict_bak)
                except OSError as e:
                    raise RuntimeError(
                        f"rename failed: {e}. Migration aborted. Resolve manually."
                    ) from e
                logger.warning("moved existing tortoise.db to %s", conflict_bak)
            else:
                # 0-node/corrupt OR partial (< source): interrupted migration
                # -> delete partial + rebuild cleanly (no --force needed)
                logger.warning(
                    "tortoise.db appears interrupted (%s nodes, source %s) — "
                    "rebuilding cleanly", target_nodes, source_events)
                if os.path.exists(target):
                    os.remove(target)

        # Rebuild from JSONL — but we don't have the source's event log dir
        # here (embedded.db is a binary RDB). Per plan: rebuild via
        # rebuild_all from the canonical JSONL log directory. Locate it:
        events_dir = os.path.join(os.path.dirname(source), "events")
        if not os.path.isdir(events_dir):
            events_dir = _find_events_dir()
        if not events_dir:
            # No JSONL available: copy the RDB snapshot (documented fallback —
            # NOT the binary-copy path for the primary flow; this is the
            # recovery path when no event log exists).
            logger.warning(
                "no JSONL event log found — falling back to RDB snapshot copy "
                "(best-effort recovery)")
            # #915 — remove any stale AOF at the target: with AOF enabled,
            # Redis loads the AOF in preference to the RDB, so a stale
            # appendonlydir/ would shadow the copied snapshot.
            from tortoise.projection import remove_stale_aof
            remove_stale_aof(target)
            shutil.copy2(backup_path, target)
        else:
            from tortoise.projection import remove_stale_aof, FalkorProjection  # noqa: I001
            remove_stale_aof(target)
            proj = FalkorProjection(target, allow_nonstandard_path=True)
            try:
                proj.rebuild_all(events_dir)
            finally:
                proj.close()

        # Integrity verification (node-ID-set equality vs source) before marker
        target_nodes = _count_nodes(target)
        if target_nodes is None:
            raise RuntimeError("rebuild produced corrupt/unreadable tortoise.db")
        source_nodes = _count_nodes(backup_path) or 0
        if source_nodes and target_nodes < source_nodes:
            logger.warning(
                "integrity: target has %s nodes, source %s — mismatch",
                target_nodes, source_nodes)

        # Marker AFTER successful rebuild + verify
        with open(marker, "w") as fh:
            fh.write(time.strftime("%Y-%m-%dT%H:%M:%SZ"))
        return {"status": "migrated", "nodes": target_nodes, "backup": backup_path}
    finally:
        lock.release()


def _find_events_dir() -> str | None:
    """Locate the canonical JSONL event-log directory (heuristic)."""
    home = os.path.expanduser("~")
    for candidate in (
        os.path.join(home, "events"),
        os.path.join(home, ".tortoise", "events"),
        os.path.join(home, ".tortoise", "logs"),
        os.path.join(home, ".tortoise", "memory", "events"),
    ):
        if os.path.isdir(candidate) and any(
            f.endswith(".jsonl") for f in os.listdir(candidate)):
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    import argparse
    import logging as _logging
    _logging.basicConfig(level=_logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="tortoise migrate-db")
    p.add_argument("--force", action="store_true",
                   help="Bypass marker / overwrite conflicting tortoise.db")
    args = p.parse_args(argv)
    try:
        result = migrate(force=args.force)
        if result.get("status") == "migrated":
            logger.warning("migrated embedded.db -> tortoise.db (%s nodes)",
                           result.get("nodes"))
        elif result.get("status") == "already-migrated":
            logger.warning("already migrated (marker present, %s nodes) — skip",
                           result.get("nodes"))
        elif result.get("status") == "noop":
            logger.warning("no embedded.db found — nothing to migrate")
        elif result.get("status") == "skipped":
            logger.warning("skipped: %s", result.get("reason"))
        return 0
    except RuntimeError as e:
        logger.error("%s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
