"""CLI entry point: python -m tortoise <command>"""
from __future__ import annotations

import argparse
import sys

def _cmd_rebuild(args):
    print(f"Rebuilding from {args.dir} → {args.db}")
    try:
        from tortoise.projection import FalkorProjection
        proj = FalkorProjection(args.db)
        counts = proj.rebuild_all(args.dir)
        print(f"Done: {counts['nodes']} nodes, {counts['edges']} edges from {counts['events']} events")
    except ImportError as e:
        print(f"FalkorDB unavailable ({e}). Use InMemory rebuild:", file=sys.stderr)
        from tortoise.log import EventLog
        from tortoise.projection import fold
        import os
        events = []
        for f in sorted(os.listdir(args.dir)):
            if f.endswith('.jsonl'):
                events.extend(EventLog(os.path.join(args.dir, f)).read_all())
        points = fold(events)
        statements, ops = 0, 0
        for p in points.values():
            if p.get('operator'):
                ops += 1
            else:
                statements += 1
        print(f"Done: {len(points)} total ({statements} statements, {ops} operators) [in-memory, no DB]")

def _cmd_demo(args):
    from pathlib import Path
    from tempfile import NamedTemporaryFile
    from tortoise.log import EventLog
    from tortoise.api import EventAPI
    from tortoise.extractor import MockExtractor

    transcript = Path(__file__).parent.parent / "tests" / "sample_transcript.txt"
    text = transcript.read_text(encoding="utf-8")

    with NamedTemporaryFile(suffix=".jsonl", mode="w+", delete=False) as tmp:
        tmp.close()
        log = EventLog(tmp.name)
        api = EventAPI(log, initiated_by="extractor", agent_id="mock@0")
        MockExtractor().run(text, transcript.name, api)
        events = log.read_all()
        Path(tmp.name).unlink()

    points, operators = {}, []
    for ev in events:
        if ev["type"] == "PointAdded":
            p = ev["point"]
            points[p["id"]] = p
        elif ev["type"] == "OperatorAdded":
            operators.append(ev["point"])

    print(f"{'='*60}")
    print(f"Tortoise Demo \u2014 {transcript.name}")
    print(f"Extracted {len(points)} statements, {len(operators)} connections")
    print(f"{'='*60}")

    # Build ID -> label lookup
    lookup = {}
    for pid, p in points.items():
        prov = p.get("provenance", {})
        lookup[pid] = f"{prov.get('speaker','?')}: {prov.get('quote', p['content'])}"

    # Print statements in order of utterance (by span start)
    ordered = sorted(points.items(), key=lambda kv: kv[1]["provenance"]["span"][0])
    for pid, p in ordered:
        prov = p["provenance"]
        print(f"\n  [{prov['speaker']}] {prov['quote']}")

    if operators:
        print(f"\n{'\u2500'*40}")
        print("Connections:")
        for op in operators:
            op_data = op["operator"]
            label = "supports" if op_data["op_type"] == "IMPL" else "contradicts"
            src = lookup.get(op_data["inputs"][0], op_data["inputs"][0])
            dst = lookup.get(op_data["inputs"][1], op_data["inputs"][1])
            print(f"  {op_data['op_type']}: \u201c{src[:70]}{'\u2026' if len(src)>70 else ''}\u201d")
            print(f"        {label} \u2192 \u201c{dst[:70]}{'\u2026' if len(dst)>70 else ''}\u201d")
    print()


def _cmd_mine_conversation(args):
    """Mine conversation transcript → Events + Points (GAP-15 / #7003)."""
    import json
    from pathlib import Path
    from tempfile import NamedTemporaryFile

    from tortoise.api import EventAPI
    from tortoise.log import EventLog
    from tortoise.mining import ConversationMiner
    from tortoise.projection import FalkorProjection

    transcript_path = Path(args.transcript)
    if not transcript_path.exists():
        sys.exit(f"Transcript not found: {args.transcript}")

    source_id = args.source_id or transcript_path.stem
    text = transcript_path.read_text(encoding="utf-8")

    # Set up log + projection
    proj = None
    log_path = Path(f"mine-{source_id}.jsonl")
    if args.db:
        try:
            proj = FalkorProjection.from_uri(args.db)
        except Exception as e:
            print(f"Warning: FalkorDB unavailable ({e}), using log-only mode")

    log = EventLog(str(log_path))
    api = EventAPI(log, initiated_by="extractor", agent_id="mining-pilot", projection=proj)

    # Disable idempotency for mining (always process fresh)
    api._ingest_cache = {}

    miner = ConversationMiner()
    result = miner.mine(text, source_id, api)

    if proj:
        proj.close()

    events = log.read_all()
    event_entries = [e for e in events if e["type"] == "EventRecorded"]
    point_entries = [e for e in events if e["type"] == "PointAdded"]
    op_entries = [e for e in events if e["type"] == "OperatorAdded"]

    print(f"{'='*60}")
    print(f"Conversation Mining — {source_id}")
    print(f"{'='*60}")
    print(f"Events:    {len(event_entries)} (gate: >=3)")
    print(f"Points:    {len(point_entries)}")
    print(f"Operators: {len(op_entries)}")
    print(f"Log:       {log_path}")
    print()

    if len(event_entries) < 3:
        print(f"\u26a0\ufe0f  GATE FAILED: {len(event_entries)} events < 3 minimum")
        print(f"   Per plan WF4: <3 events/session → permanently descoped.")
    else:
        print(f"\u2705  GATE PASSED: {len(event_entries)} events")

    print()
    for ev in event_entries:
        e = ev.get("event", {})
        kind = e.get("eventKind", "unknown")
        obj = e.get("object", "")[:80]
        print(f"  [{kind}] {obj}..." if len(e.get("object","")) > 80 else f"  [{kind}] {obj}")
    print()

    return result


def _cmd_reconcile(args):
    """Replay unprojected EventRecorded entries from JSONL into FalkorDB."""
    import json, sys
    from pathlib import Path

    if not args.db.startswith("docker://"):
        sys.exit("Error: reconcile requires docker:// URI (e.g. docker://:pass@localhost:6379)")

    log_path = Path(args.log)
    if not log_path.exists():
        sys.exit(f"No event log found at {args.log}. Nothing to reconcile.")

    try:
        from tortoise.log import EventLog
        from tortoise.projection import FalkorProjection
    except ImportError:
        sys.exit("Tortoise not installed. Run: pip install -e negation-game-explorations/tortoise")

    events = EventLog(log_path).read_all()

    proj = None
    try:
        proj = FalkorProjection.from_uri(args.db)

        event_entries = [ev for ev in events if ev["type"] == "EventRecorded"]
        all_ids = [ev["event"]["eventId"] for ev in event_entries]
        existing: set[str] = set()
        for i in range(0, len(all_ids), 500):
            batch = all_ids[i:i+500]
            rows = proj.g.query(
                "UNWIND $ids AS eid MATCH (e:Event {eventId:eid}) RETURN e.eventId",
                params={"ids": batch}
            ).result_set
            existing.update(r[0] for r in rows if r)

        applied = 0
        for ev in event_entries:
            if ev["event"]["eventId"] not in existing:
                proj.apply(ev)
                applied += 1

        total = len(event_entries)
        print(f"Reconciled {applied} events ({total - applied} already projected — {total} total)")
    finally:
        if proj:
            proj.close()



def _cmd_init(args):
    """Auto-detect FalkorDB and create default graph — onboarding."""
    import os
    print("Tortoise init — auto-detecting FalkorDB…")

    # 1. Try Docker FalkorDB
    docker_host = os.environ.get("FALKORDB_HOST", "localhost")
    docker_pass = os.environ.get("FALKORDB_PASSWORD", "")

    try:
        docker_port = int(os.environ.get("FALKORDB_PORT", "6379"))
    except (ValueError, TypeError):
        print(f"  ❌ Invalid FALKORDB_PORT: {os.environ.get('FALKORDB_PORT')!r}. Must be an integer.")
        raise SystemExit(1)

    try:
        from falkordb import FalkorDB
        db = FalkorDB(host=docker_host, port=docker_port,
                      password=docker_pass or None)
        db.select_graph("tortoise").query("RETURN 1")
        print(f"  ✅ Docker FalkorDB detected at {docker_host}:{docker_port}")
        print(f"  Graph: tortoise")
        print()
        print("Next steps:")
        print("  tortoise demo              — run mock extractor on sample transcript")
        print("  tortoise serve             — start MCP server")
        print("  tortoise ingest <file>     — ingest documents into graph")
        return
    except ImportError:
        pass  # falkordb package not installed — try Lite
    except (ConnectionError, ConnectionRefusedError, OSError) as e:
        print(f"  ⚠️  Docker FalkorDB unreachable at {docker_host}:{docker_port}: {e}")
    except Exception as e:
        err = str(e).lower()
        if "auth" in err or "password" in err:
            print(f"  ⚠️  Docker FalkorDB auth failed — check FALKORDB_PASSWORD env var")
        else:
            print(f"  ⚠️  Docker FalkorDB unreachable ({e})")

    # 2. Fallback: FalkorDBLite (SQLite-backed)
    db_path = args.path or "tortoise.db"
    try:
        from redislite.falkordb_client import FalkorDB
        db = FalkorDB(db_path)
        db.select_graph("tortoise").query("RETURN 1")
        print(f"  ✅ FalkorDBLite initialized at {db_path}")
        print(f"  Graph: tortoise")
        print()
        print("Next steps:")
        print("  tortoise demo              — run mock extractor on sample transcript")
        print("  tortoise serve             — start MCP server")
        print("  tortoise ingest <file>     — ingest documents into graph")
        return
    except ImportError:
        print(f"  ❌ Neither falkordb nor redislite installed.")
        print(f"     pip install falkordb       # for Docker mode")
        print(f"     pip install redislite      # for embedded mode")
        raise SystemExit(1)
    except Exception as e:
        print(f"  ❌ FalkorDBLite init failed: {e}")
        raise SystemExit(1)


def _cmd_verify(args):
    """Write, read, delete a test Point — health check."""
    from .projection import FalkorProjection
    proj = FalkorProjection.from_uri(args.db)
    try:
        proj.apply([{"type": "PointAdded", "point": {"id": "test-verify", "content": "verify", "pointKind": "observation", "createdAt": "2026-01-01T00:00:00Z"}}])
        print("✓ write OK")
        result = proj.db.query("MATCH (p:Point {id: 'test-verify'}) RETURN p")
        print("✓ read OK" if result.result_set else "✗ read FAILED")
        proj.db.query("MATCH (p:Point {id: 'test-verify'}) DELETE p")
        print("✓ delete OK")
    except Exception as e:
        print(f"✗ {e}")
        raise SystemExit(1)
    finally:
        proj.close()


def _cmd_backfill(args):
    """Backfill missing properties on existing Points."""
    from .projection import FalkorProjection, _now_iso
    proj = FalkorProjection(args.db) if hasattr(args, 'db') else FalkorProjection("tortoise.db")
    try:
        r = proj.g.query("MATCH (p:Point) WHERE p.status IS NULL SET p.status = 'live' RETURN count(p)").result_set
        status_count = r[0][0] if r else 0
        r = proj.g.query(f"MATCH (p:Point) WHERE p.createdAt IS NULL SET p.createdAt = '{_now_iso()}' RETURN count(p)").result_set
        created_count = r[0][0] if r else 0
        print(f"Backfilled: {status_count} status + {created_count} createdAt")
    finally:
        proj.close()


def main():

    p = argparse.ArgumentParser(prog="tortoise")
    sp = p.add_subparsers(dest="cmd")
    rb = sp.add_parser("rebuild", help="Rebuild FalkorDB from all .jsonl files")
    rb.add_argument("--db", default="tortoise.db")
    rb.add_argument("--dir", default=".")
    sp.add_parser("demo", help="Run mock extractor on sample transcript")
    sp.add_parser("backfill", help="Backfill missing Point properties (status, createdAt)")
    vf = sp.add_parser("verify", help="Write/read/delete test Point — health check")
    vf.add_argument("--db", required=True, help="FalkorDB docker:// URI")
    cc = sp.add_parser("check-consistency", help="Verify event log matches graph state")
    cc.add_argument("--log", required=True, help="Path to events.jsonl")
    cc.add_argument("--db", required=True, help="FalkorDB docker:// URI")
    rc = sp.add_parser("reconcile", help="Replay unprojected EventRecorded entries from JSONL into FalkorDB")
    rc.add_argument("--db", required=True, help="FalkorDB docker:// URI")
    rc.add_argument("--log", required=True, help="Path to events.jsonl")
    bk = sp.add_parser("backup", help="Backup events.jsonl + FalkorDB to timestamped dir")
    bk.add_argument("--db", default="tortoise.db", help="Path to database file")
    bk.add_argument("--events", default="events.jsonl", help="Path to event log")
    rs = sp.add_parser("restore", help="Restore from backup directory")
    rs.add_argument("backup_dir", help="Path to backup directory")
    rs.add_argument("--db", default="tortoise.db", help="Target database path")
    rs.add_argument("--events", default="events.jsonl", help="Target event log path")
    mc = sp.add_parser("mine-conversation", help="Mine conversation transcript → Events + Points (GAP-15)")
    mc.add_argument("transcript", help="Path to transcript file (Speaker: text format)")
    mc.add_argument("--source-id", default=None, help="Source identifier (default: basename of transcript)")
    mc.add_argument("--db", default=None, help="FalkorDB docker:// URI for projection")
    sr = sp.add_parser("serve", help="Start Tortoise MCP server (stdio)")
    init = sp.add_parser("init", help="Auto-detect FalkorDB and create default graph")
    init.add_argument("--path", default="tortoise.db", help="Path for FalkorDBLite (default: tortoise.db)")
    hs = sp.add_parser("health-server", help="Start standalone /health HTTP server")
    hs.add_argument("--port", type=int, default=9090, help="HTTP port (default: 9090)")
    args = p.parse_args()
    if args.cmd == "rebuild":
        _cmd_rebuild(args)
    elif args.cmd == "demo":
        _cmd_demo(args)
    elif args.cmd == "check-consistency":
        import sys as _sys
        try:
            from tortoise.consistency import check_consistency
            from tortoise.projection import FalkorProjection
            proj = FalkorProjection.from_uri(args.db)
            try:
                result = check_consistency(args.log, proj)
            finally:
                proj.close()
        except Exception as e:
            print(f"Error: {e}", file=_sys.stderr)
            _sys.exit(1)
        if result["ok"]:
            print(f"\u2713 Consistent: {result['log_points']} points in both log and graph")
        else:
            print(f"\u2717 Inconsistent: {result['log_points']} in log, {result['db_points']} in graph (delta: {result['delta']})")
            _sys.exit(1)
    elif args.cmd == "reconcile":
        _cmd_reconcile(args)
    elif args.cmd == "backfill":
        _cmd_backfill(args)
    elif args.cmd == "verify":
        _cmd_verify(args)
    elif args.cmd == "backup":
        from tortoise.backup import backup
        target = backup(db_path=args.db, events_path=args.events)
        print(f"Backed up to {target}")
    elif args.cmd == "restore":
        from tortoise.backup import restore
        result = restore(args.backup_dir, db_path=args.db, events_path=args.events)
        print(f"Restored {result['events']} events — {result['status']}")
    elif args.cmd == "serve":
        from tortoise.mcp_server import main as serve_main
        serve_main()
    elif args.cmd == "mine-conversation":
        _cmd_mine_conversation(args)
    elif args.cmd == "init":
        _cmd_init(args)
    elif args.cmd == "health-server":
        from tortoise.monitoring import serve_health
        print(f"Health server on http://0.0.0.0:{args.port}/health")
        serve_health(args.port)
    else:
        p.print_help()

if __name__ == "__main__":
    main()
