#!/usr/bin/env python3
"""#334 Phase 0/1 — docker-aware RDB snapshot + verified restore.

Scoping Phase 1 ("Pre-migration") requires a docker-aware RDB snapshot +
verified restore path before ANY destructive op. Rationale (solution-verify
P1-2): ``tortoise backup --db`` takes a FILE path (no docker:// URI support),
and JSONL replay is NOT a restore path — it resurrects stub Points and drops
SDK-created points. Real restore = docker-aware RDB (copy the .rdb file back
into the container and restart).

This script implements both halves with a verification handshake:
  - snapshot: BGSAVE → wait for LASTSAVE to advance → docker cp the RDB out →
    record pre-snapshot graph stats (node counts by type) into a sidecar
    .meta.json so restore can VERIFY it recovered the same state.
  - restore: refuses without --yes (destructive: container restart) → stop →
    place RDB → start → wait for connectivity → re-run the connectivity gate's
    graph stats and reconcile counts against the snapshot meta.

Connection-mode gate (connectivity_gate.py classification):
  - docker:// / redis:// / rediss:// URI → RDB path below.
  - Embedded FalkorDBLite file path → NO docker RDB. Restore fallback is
    backup.py JSONL replay, which is NOT a real restore (see scoping P1-2);
    embedded mode is tooling-validation only. The script refuses to restore
    an embedded target (clear error instead of a false restore).
  - bolt:// fallback (coherence-review P1-2): no docker reachable for a URI
    target → the script fails with the documented managed/cloud alternative
    (FalkorDB Cloud console snapshot) rather than pretending to restore.

Precedent: graph-scripts/pre_migration_snapshot.py (BGSAVE + RDB check +
restore PROCEDURE text for the #49 migration). This script adds the VERIFIED
restore half and the snapshot↔restore count-reconciliation for #334.

Usage:
  # Snapshot (safe, read-only apart from BGSAVE + docker cp):
  TORTOISE_DB_URI=docker://:falkordb@localhost:6379/tortoise \
    python3 graph-scripts/rdb_snapshot_restore.py snapshot --out backups/334

  # Dry-run snapshot (prints the command sequence, no side effects):
  python3 graph-scripts/rdb_snapshot_restore.py snapshot --dry-run

  # Verified restore (DESTRUCTIVE — restarts the container; requires --yes):
  python3 graph-scripts/rdb_snapshot_restore.py restore \
    --rdb backups/334/pre-migration-<ts>.rdb --yes

Exit codes: 0 = ok, 1 = operational failure, 2 = usage error.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

# Allow running from any directory (repo-root import).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DEFAULT_URI = "docker://:falkordb@localhost:6379/tortoise"
SUPPORTED_URI_SCHEMES = ("docker", "redis", "rediss")
_CONNECT_POLL_S = 2
_CONNECT_MAX_WAIT_S = 60


# ── URI + container helpers ──────────────────────────────────────────────

def parse_uri(uri: str) -> dict:
    """Parse a connection URI into host/port/password/graph components."""
    from urllib.parse import urlparse
    parsed = urlparse(uri)
    return {
        "scheme": parsed.scheme,
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 6379,
        "password": parsed.password or "",
        "graph": parsed.path.lstrip("/") or "tortoise",
    }


def classify_target(uri: str | None, path: str | None) -> dict:
    """Mirror connectivity_gate.classify_target — uri vs embedded mode."""
    if uri is not None:
        scheme = uri.split("://", 1)[0]
        if scheme not in SUPPORTED_URI_SCHEMES:
            raise ValueError(
                f"Unsupported scheme {scheme!r} — expected one of "
                f"{'/'.join(SUPPORTED_URI_SCHEMES)} (or --path for embedded)."
            )
        return {"mode": "uri", "uri": uri}
    if path is not None:
        return {"mode": "embedded", "path": path}
    env_uri = os.environ.get("TORTOISE_DB_URI")
    if env_uri:
        return classify_target(env_uri, None)
    return classify_target(DEFAULT_URI, None)


def graph_stats_for(uri: str) -> dict:
    """Node counts by type for the URI target (verification baseline).

    Reuses the connectivity gate's read-only stats. Raises on unreachable —
    the caller treats that as restore-verification failure.
    """
    from tortoise.projection import FalkorProjection
    from connectivity_gate import graph_stats  # repo graph-scripts/ module
    proj = FalkorProjection.from_uri(uri)
    return graph_stats(proj)


def _docker(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a docker CLI command, returning the CompletedProcess (no raise)."""
    return subprocess.run(["docker", *args], capture_output=True,
                          text=True, timeout=timeout)


def resolve_container(uri: str, container: str | None) -> str:
    """Resolve the container name: explicit arg > port-based docker ps probe
    > default 'falkordb'. Raises RuntimeError when docker is unavailable."""
    if container:
        return container
    port = parse_uri(uri)["port"]
    r = _docker(["ps", "--format", "{{.Names}}\t{{.Ports}}"], timeout=15)
    if r.returncode != 0:
        raise RuntimeError(
            "docker CLI unavailable or daemon not running — the docker-aware "
            f"RDB path needs local Docker. bolt:// fallback: managed "
            "FalkorDB Cloud console snapshot, or backup.py JSONL (NOT a real "
            "restore — scoping P1-2)."
        )
    for line in r.stdout.splitlines():
        name, ports = line.split("\t", 1)
        if f":{port}->" in ports or f":{port}/tcp" in ports:
            return name
    return "falkordb"


def _container_rdb_info(container: str) -> dict:
    """CONFIG GET dir + dbfilename via docker exec redis-cli."""
    def _cfg(key: str) -> str:
        r = _docker(["exec", container, "redis-cli", "CONFIG", "GET", key],
                    timeout=15)
        lines = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
        return lines[-1] if lines else ""
    return {"dir": _cfg("dir"), "dbfilename": _cfg("dbfilename")}


def _bgsave_and_wait(container: str, start_ts: int, timeout_s: int = 120) -> dict:
    """Trigger BGSAVE and poll LASTSAVE until it advances past start_ts."""
    r = _docker(["exec", container, "redis-cli", "BGSAVE"], timeout=30)
    if r.returncode != 0:
        return {"ok": False, "error": f"BGSAVE failed: {r.stderr.strip()}"}
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = _docker(["exec", container, "redis-cli", "LASTSAVE"], timeout=15)
        if r.returncode == 0 and r.stdout.strip().isdigit():
            ts = int(r.stdout.strip())
            if ts > start_ts:
                return {"ok": True, "lastsave": ts}
        time.sleep(_CONNECT_POLL_S)
    return {"ok": False, "error": f"LASTSAVE did not advance within {timeout_s}s"}


# ── Snapshot ─────────────────────────────────────────────────────────────

def snapshot(uri: str, out_dir: str, container: str | None,
             dry_run: bool = False) -> dict:
    """Docker-aware RDB snapshot with a stats sidecar for restore verification.

    Returns {"ok": bool, "rdb": str, "meta": str, ...}. Side effects: BGSAVE,
    docker cp (unless dry_run).
    """
    cfg = parse_uri(uri)
    os.makedirs(out_dir, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    if dry_run:
        return {"ok": True, "dry_run": True,
                "would": [
                    f"docker exec <container> redis-cli BGSAVE",
                    f"docker cp <container>:<rdb-dir>/<dbfilename> {out_dir}/pre-migration-{ts}.rdb",
                    f"record graph stats sidecar -> {out_dir}/pre-migration-{ts}.meta.json",
                ]}

    cname = resolve_container(uri, container)

    info = _container_rdb_info(cname)
    if not info["dir"] or not info["dbfilename"]:
        return {"ok": False,
                "error": f"could not read RDB config from container {cname!r} — "
                         "is it a FalkorDB/Redis container?"}

    # 1. BGSAVE + wait for LASTSAVE to advance
    lastsave = _bgsave_and_wait(cname, start_ts=int(time.time()) - 5)
    if not lastsave["ok"]:
        return lastsave

    # 2. Copy RDB out
    rdb_path = os.path.join(out_dir, f"pre-migration-{ts}.rdb")
    src = f"{cname}:{info['dir']}/{info['dbfilename']}"
    r = _docker(["cp", src, rdb_path], timeout=60)
    if r.returncode != 0:
        return {"ok": False, "error": f"docker cp failed: {r.stderr.strip()}"}
    if not os.path.isfile(rdb_path) or os.path.getsize(rdb_path) == 0:
        return {"ok": False, "error": f"copied RDB missing/empty: {rdb_path}"}

    # 3. Record pre-snapshot graph stats (verification baseline)
    stats = graph_stats_for(uri)
    meta = {
        "snapshot_at": ts,
        "uri": uri,
        "container": cname,
        "rdb": rdb_path,
        "rdb_bytes": os.path.getsize(rdb_path),
        "lastsave": lastsave["lastsave"],
        "graph_stats": stats,
    }
    meta_path = f"{rdb_path}.meta.json"
    with open(meta_path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, default=str)

    return {"ok": True, "rdb": rdb_path, "meta": meta_path, **meta}


# ── Restore ──────────────────────────────────────────────────────────────

def restore(uri: str, rdb_file: str, container: str | None,
            yes: bool = False, dry_run: bool = False) -> dict:
    """Verified docker-aware RDB restore. DESTRUCTIVE: restarts the container.

    Sequence: stop → place RDB (docker cp) → start → wait for connectivity →
    reconcile graph stats against the snapshot's sidecar meta (when present).
    """
    if not yes:
        return {"ok": False,
                "error": "refusing to restore without --yes — this stops and "
                         "restarts the FalkorDB container (destructive)."}
    if not os.path.isfile(rdb_file):
        return {"ok": False, "error": f"RDB file not found: {rdb_file}"}

    cfg = parse_uri(uri)
    cname = resolve_container(uri, container)

    # Expected state from the snapshot sidecar (verification handshake)
    meta = {}
    meta_path = f"{rdb_file}.meta.json"
    if os.path.isfile(meta_path):
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)

    if dry_run:
        return {"ok": True, "dry_run": True, "would": [
            f"docker stop {cname}",
            f"docker cp {rdb_file} {cname}:<rdb-dir>/<dbfilename>",
            f"docker start {cname}",
            f"wait for connectivity (≤{_CONNECT_MAX_WAIT_S}s) + reconcile "
            f"graph stats vs {meta_path}",
        ]}

    # Pre-restore sanity: capture current state for the audit trail + read
    # the RDB location BEFORE the stop (docker exec fails on stopped containers).
    before = graph_stats_for(uri)
    info = _container_rdb_info(cname)

    # 1. Stop container
    r = _docker(["stop", cname], timeout=60)
    if r.returncode != 0:
        return {"ok": False,
                "error": f"docker stop {cname} failed: {r.stderr.strip()}"}

    try:
        # 2. Place RDB into the container's data dir (read pre-stop; defaults
        #    match the FalkorDB image layout when the probe came up empty).
        target = (f"{info['dir']}/{info['dbfilename']}" if info["dir"]
                  else "/var/lib/falkordb/dump.rdb")
        r = _docker(["cp", rdb_file, f"{cname}:{target}"], timeout=60)
        if r.returncode != 0:
            return {"ok": False,
                    "error": f"docker cp RDB into container failed: {r.stderr.strip()}"}

        # 3. Start container
        r = _docker(["start", cname], timeout=60)
        if r.returncode != 0:
            return {"ok": False,
                    "error": f"docker start {cname} failed: {r.stderr.strip()}"}
    except Exception as exc:  # noqa: BLE001 — restart on any mid-restore failure
        _docker(["start", cname], timeout=60)  # best-effort recovery
        return {"ok": False, "error": f"restore failed mid-sequence: {exc}"}

    # 4. Wait for connectivity
    after = None
    deadline = time.time() + _CONNECT_MAX_WAIT_S
    while time.time() < deadline:
        try:
            after = graph_stats_for(uri)
            break
        except Exception:  # noqa: BLE001 — poll until deadline
            time.sleep(_CONNECT_POLL_S)
    if after is None:
        return {"ok": False,
                "error": f"container did not become reachable within "
                         f"{_CONNECT_MAX_WAIT_S}s after restore"}

    # 5. Verified-restore reconciliation
    expected = meta.get("graph_stats") or before
    def _counts(s): return s.get("by_label", {})
    mismatch = {k: (expected.get("by_label", {}).get(k),
                    _counts(after).get(k))
                for k in set(expected.get("by_label", {})) |
                          set(_counts(after))
                if expected.get("by_label", {}).get(k) != _counts(after).get(k)}
    verified = not mismatch

    return {
        "ok": True,
        "verified": verified,
        "before": before,
        "after": after,
        "expected": expected,
        "count_mismatches": mismatch,
        "note": "verified restore" if verified else
                "restore completed but node counts DIFFER from the snapshot — "
                "investigate before destructive migration proceeds.",
    }


# ── CLI ──────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="#334 Phase 0/1 docker-aware RDB snapshot + verified "
                    "restore (scoping Phase 1 prerequisite)."
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_snap = sub.add_parser("snapshot", help="BGSAVE + copy RDB out + stats sidecar")
    p_snap.add_argument("--out", default="backups/334",
                        help="Output directory (default: backups/334)")
    p_snap.add_argument("--container", default=None,
                        help="FalkorDB container name (default: auto-probe)")
    p_snap.add_argument("--dry-run", action="store_true")

    p_rest = sub.add_parser("restore", help="Stop, place RDB, start, verify")
    p_rest.add_argument("--rdb", required=True, help="Path to the .rdb file")
    p_rest.add_argument("--container", default=None)
    p_rest.add_argument("--yes", action="store_true",
                        help="Confirm the destructive container restart")
    p_rest.add_argument("--dry-run", action="store_true")

    common = parser.add_argument_group("target")
    common.add_argument("--uri", default=None, help="Override TORTOISE_DB_URI")
    common.add_argument("--path", default=None,
                        help="Embedded path — unsupported for RDB ops (error out)")

    args = parser.parse_args(argv)

    try:
        cfg = classify_target(args.uri, args.path)
    except ValueError as exc:
        print(f"[rdb-snapshot-restore] ERROR: {exc}", file=sys.stderr)
        return 2
    if cfg["mode"] != "uri":
        print("[rdb-snapshot-restore] ERROR: embedded FalkorDBLite target has "
              "no docker RDB — restore fallback is backup.py JSONL replay, "
              "which is NOT a real restore (scoping P1-2). Refusing.",
              file=sys.stderr)
        return 1

    try:
        if args.cmd == "snapshot":
            result = snapshot(cfg["uri"], args.out, args.container,
                              dry_run=args.dry_run)
        else:
            result = restore(cfg["uri"], args.rdb, args.container,
                             yes=args.yes, dry_run=args.dry_run)
    except Exception as exc:  # noqa: BLE001 — operational failures exit 1
        print(f"[rdb-snapshot-restore] FAIL: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
