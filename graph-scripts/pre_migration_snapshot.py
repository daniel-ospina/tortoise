#!/usr/bin/env python3
"""Pre-migration snapshot helper — #49 Phase 2 safety net (Task 2.0).

Triggers FalkorDB BGSAVE, verifies the RDB file exists, and prints the
exact restore procedure so we can recover if the REMOVE migration goes wrong.

Usage:
  TORTOISE_DB_URI=docker://:@localhost:16379/tortoise \\
    python3 graph-scripts/pre_migration_snapshot.py

  # Dry-run (no side effects):
  python3 graph-scripts/pre_migration_snapshot.py --dry-run

  # Custom container/port:
  python3 graph-scripts/pre_migration_snapshot.py \\
    --container falkordb-personal --port 6379
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time  # noqa: F401
from datetime import datetime, timezone

# Allow running from any directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Connection helpers ────────────────────────────────────────────────

def _parse_uri(uri: str) -> dict:
    """Parse docker:// URI into components."""
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 16379,
        "password": parsed.password or "",
        "graph": parsed.path.lstrip("/") or "tortoise",
    }


# ── BGSAVE trigger ────────────────────────────────────────────────────

def trigger_bgsave(host: str = "localhost", port: int = 16379,
                   password: str = "") -> dict:
    """Trigger FalkorDB BGSAVE and return status.

    Returns {"ok": bool, "message": str, "timestamp": str}
    """
    try:
        from falkordb import FalkorDB
        db = FalkorDB(host=host, port=port, password=password or None,
                      socket_connect_timeout=5, socket_timeout=120)
        result = db.connection.execute_command("BGSAVE")
        ts = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        return {"ok": True, "message": f"BGSAVE triggered: {result}",
                "timestamp": ts}
    except Exception as e:
        return {"ok": False, "message": f"BGSAVE failed: {e}",
                "timestamp": datetime.now(timezone.utc).isoformat()}  # noqa: UP017


def check_rdb(host: str = "localhost", port: int = 16379,
              password: str = "") -> dict:
    """Check FalkorDB persistence state via CONFIG GET + DBSIZE + LASTSAVE.

    Returns {"ok": bool, "dir": str, "dbfilename": str, "dbsize": int,
             "lastsave": int, "lastsave_utc": str}
    """
    try:
        from falkordb import FalkorDB
        db = FalkorDB(host=host, port=port, password=password or None,
                      socket_connect_timeout=5, socket_timeout=120)

        # CONFIG GET dir
        dir_result = db.connection.execute_command("CONFIG", "GET", "dir")
        rdb_dir = dir_result.get("dir", "unknown") if isinstance(dir_result, dict) else (
            dir_result[1] if isinstance(dir_result, list) and len(dir_result) > 1 else "unknown"
        )

        # CONFIG GET dbfilename
        fn_result = db.connection.execute_command("CONFIG", "GET", "dbfilename")
        rdb_fn = fn_result.get("dbfilename", "unknown") if isinstance(fn_result, dict) else (
            fn_result[1] if isinstance(fn_result, list) and len(fn_result) > 1 else "unknown"
        )

        # DBSIZE
        dbsize = db.connection.execute_command("DBSIZE")

        # LASTSAVE
        lastsave = db.connection.execute_command("LASTSAVE")
        lastsave_utc = datetime.fromtimestamp(lastsave, tz=timezone.utc).isoformat() if lastsave else "unknown"  # noqa: UP017

        return {
            "ok": True,
            "dir": rdb_dir,
            "dbfilename": rdb_fn,
            "dbsize": int(dbsize) if dbsize else 0,
            "lastsave": lastsave,
            "lastsave_utc": lastsave_utc,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_rdb_via_docker(container: str = "falkordb-personal",
                         port: int = 6379) -> dict:
    """Check RDB via docker exec (fallback if SDK can't connect)."""
    try:
        # CONFIG GET dir
        r = subprocess.run(
            ["docker", "exec", container, "redis-cli", "-p", str(port),
             "CONFIG", "GET", "dir"],
            capture_output=True, text=True, timeout=10,
        )
        # Output is alternating key/value lines
        lines = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]  # noqa: E741
        rdb_dir = lines[-1] if lines else "unknown"

        # CONFIG GET dbfilename
        r = subprocess.run(
            ["docker", "exec", container, "redis-cli", "-p", str(port),
             "CONFIG", "GET", "dbfilename"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]  # noqa: E741
        rdb_fn = lines[-1] if lines else "unknown"  # noqa: F841

        # DBSIZE
        r = subprocess.run(
            ["docker", "exec", container, "redis-cli", "-p", str(port), "DBSIZE"],
            capture_output=True, text=True, timeout=10,
        )
        dbsize = int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0

        # LASTSAVE
        r = subprocess.run(
            ["docker", "exec", container, "redis-cli", "-p", str(port), "LASTSAVE"],
            capture_output=True, text=True, timeout=10,
        )
        lastsave = int(r.stdout.strip()) if r.stdout.strip().isdigit() else 0
        lastsave_utc = datetime.fromtimestamp(lastsave, tz=timezone.utc).isoformat() if lastsave else "unknown"  # noqa: UP017

        # Verify RDB file exists on disk
        r = subprocess.run(
            ["docker", "exec", container, "redis-cli", "-p", str(port),
             "CONFIG", "GET", "dbfilename"],
            capture_output=True, text=True, timeout=10,
        )
        lines = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]  # noqa: E741
        rdb_name = lines[-1] if lines else "dump.rdb"

        r = subprocess.run(
            ["docker", "exec", container, "ls", "-la",
             f"{rdb_dir}/{rdb_name}"],
            capture_output=True, text=True, timeout=10,
        )
        file_exists = r.returncode == 0
        file_info = r.stdout.strip() if file_exists else f"MISSING: {rdb_dir}/{rdb_name}"

        return {
            "ok": True,
            "dir": rdb_dir,
            "dbfilename": rdb_name,
            "dbsize": dbsize,
            "lastsave": lastsave,
            "lastsave_utc": lastsave_utc,
            "file_exists": file_exists,
            "file_info": file_info,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Restore procedure ──────────────────────────────────────────────────

RESTORE_PROCEDURE = """
╔══════════════════════════════════════════════════════════════════════════╗
║                    RESTORE PROCEDURE (if migration goes wrong)          ║
╠══════════════════════════════════════════════════════════════════════════╣
║                                                                          ║
║  OPTION A — Restore from RDB snapshot (fastest, ~30s downtime):         ║
║    1. docker stop falkordb-personal                                      ║
║    2. cp {rdb_dir}/pre_migration_backup.rdb {rdb_dir}/{rdb_filename}     ║
║       (or: docker cp pre_migration_backup.rdb falkordb-personal:{rdb_dir}/{rdb_filename})
║    3. docker start falkordb-personal                                      ║
║    4. Verify: docker exec falkordb-personal redis-cli DBSIZE             ║
║                                                                          ║
║  OPTION B — FalkorDB Cloud snapshot (if using managed):                 ║
║    1. Go to FalkorDB Cloud dashboard → Backups                          ║
║    2. Restore from pre-migration snapshot taken at {timestamp}           ║
║                                                                          ║
║  OPTION C — Replay from event log (slowest, but most complete):         ║
║    1. git checkout the commit BEFORE the REMOVE migration               ║
║    2. python3 -c "from tortoise.projection import FalkorProjection;     ║
║       p = FalkorProjection.from_uri('docker://:@localhost:16379/tortoise');
║       p.replay('events.jsonl')"                                          ║
║    3. Verify graph state                                                ║
║                                                                          ║
║  OPTION D — Restore from backup.py archive:                             ║
║    1. Find the most recent backup: ls backups/                           ║
║    2. python3 -c "from tortoise.backup import restore;                  ║
║       restore('backups/{backup_dir}', 'tortoise.db', into_falkor=True)"  ║
║                                                                          ║
╚══════════════════════════════════════════════════════════════════════════╝
"""


# ── Main ────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-migration safety snapshot — #49 Phase 2 Task 2.0"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without side effects")
    parser.add_argument("--container", default="falkordb-personal",
                        help="FalkorDB container name (default: falkordb-personal)")
    parser.add_argument("--port", type=int, default=6379,
                        help="Redis port inside container (default: 6379)")
    parser.add_argument("--uri", default=None,
                        help="Override TORTOISE_DB_URI")
    parser.add_argument("--trigger-bgsave", action="store_true",
                        help="Trigger BGSAVE (default: read-only, just check state)")
    parser.add_argument("--copy-rdb", default=None,
                        help="Copy RDB to this path as backup (requires docker cp)")
    args = parser.parse_args()

    uri = args.uri or os.environ.get(
        "TORTOISE_DB_URI", "docker://:@localhost:16379/tortoise"
    )
    cfg = _parse_uri(uri)

    if args.dry_run:
        print("=" * 60)
        print("DRY RUN — no side effects")
        print("=" * 60)
        print(f"  URI:        {uri}")
        print(f"  Container:  {args.container}")
        print(f"  Container port: {args.port}")
        print(f"  Host:       {cfg['host']}:{cfg['port']}")
        print(f"  Graph:      {cfg['graph']}")
        print()
        print("Would:")
        print(f"  1. Trigger BGSAVE on {cfg['host']}:{cfg['port']}")
        print(f"  2. Verify RDB via docker exec {args.container}")
        print(f"  3. Print restore procedure")  # noqa: F541
        if args.copy_rdb:
            print(f"  4. Copy RDB to {args.copy_rdb}")
        print()
        print("Restore procedure (template):\n" + RESTORE_PROCEDURE.format(
            rdb_dir="/var/lib/falkordb/data",
            rdb_filename="dump.rdb",
            timestamp="DRY_RUN",
            backup_dir="YYYYMMDDTHHMMSSZ",
        ))
        return 0

    print("=" * 60)
    print("PRE-MIGRATION SAFETY SNAPSHOT")
    print("=" * 60)
    print(f"  Time: {datetime.now(timezone.utc).isoformat()}")  # noqa: UP017
    print(f"  URI:  {uri}")
    print(f"  Host: {cfg['host']}:{cfg['port']}")
    print(f"  Graph: {cfg['graph']}")
    print()

    # ── Step 1: BGSAVE ──────────────────────────────────────────────
    if args.trigger_bgsave:
        print("[1] Triggering BGSAVE...")
        result = trigger_bgsave(cfg["host"], cfg["port"], cfg["password"])
        if result["ok"]:
            print(f"    ✓ {result['message']}")
            print(f"    Triggered at: {result['timestamp']}")
        else:
            print(f"    ✗ {result['message']}")
            # Non-fatal — the DB may already have periodic snapshots
        print()
    else:
        print("[1] BGSAVE: skipping (use --trigger-bgsave to trigger).")
        print("    Periodic snapshots from --save config may already exist.")
        print()

    # ── Step 2: Check RDB via Docker ────────────────────────────────
    print(f"[2] Checking RDB via docker exec {args.container}...")
    rdb = check_rdb_via_docker(args.container, args.port)
    if rdb.get("ok"):
        print(f"    RDB dir:     {rdb['dir']}")
        print(f"    RDB file:    {rdb['dbfilename']}")
        print(f"    DB size:     {rdb['dbsize']} keys")
        print(f"    Last save:   {rdb['lastsave_utc']}")
        print(f"    File exists: {rdb.get('file_exists', 'unknown')}")
        if rdb.get("file_info"):
            print(f"    File info:   {rdb['file_info']}")
    else:
        print(f"    Docker check failed: {rdb.get('error', 'unknown')}")
        print("    Trying SDK-level check...")
        rdb2 = check_rdb(cfg["host"], cfg["port"], cfg["password"])
        if rdb2.get("ok"):
            print(f"    RDB dir:     {rdb2['dir']}")
            print(f"    RDB file:    {rdb2['dbfilename']}")
            print(f"    DB size:     {rdb2['dbsize']} keys")
            print(f"    Last save:   {rdb2['lastsave_utc']}")
        else:
            print(f"    SDK check also failed: {rdb2.get('error', 'unknown')}")
            print("    ⚠  Could not verify RDB state — proceed with caution.")

    # ── Step 3: Copy RDB ────────────────────────────────────────────
    if args.copy_rdb:
        print(f"\n[3] Copying RDB backup to {args.copy_rdb}...")
        src = f"{rdb.get('dir', '/var/lib/falkordb/data')}/{rdb.get('dbfilename', 'dump.rdb')}"
        r = subprocess.run(
            ["docker", "cp", f"{args.container}:{src}", args.copy_rdb],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            print(f"    ✓ Copied to {args.copy_rdb}")
        else:
            print(f"    ✗ Copy failed: {r.stderr}")
    else:
        print("\n[3] RDB copy: skipping (use --copy-rdb PATH to backup).")

    # ── Step 4: Restore procedure ───────────────────────────────────
    print()
    print(RESTORE_PROCEDURE.format(
        rdb_dir=rdb.get("dir", "/var/lib/falkordb/data"),
        rdb_filename=rdb.get("dbfilename", "dump.rdb"),
        timestamp=rdb.get("lastsave_utc", datetime.now(timezone.utc).isoformat()),  # noqa: UP017
        backup_dir=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),  # noqa: UP017
    ))

    print("\nSnapshot check complete. RDB should be safe before REMOVE migration.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
