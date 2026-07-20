#!/usr/bin/env python3
"""#7047 Sync schedule stub — run all sync scripts in dependency order.

    python tortoise/scripts/sync_schedule.py --db tortoise.db

Orchestrates: GitHub org → products → roles → features → aboutEntities backfill.
ponytail: sequential subprocess calls; no DAG scheduler needed for 4 scripts.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_SCRIPTS = [
    ("sync_github.py", "GitHub org"),
    ("sync_products.py", "Product inventory"),
    ("sync_roles.py", "Role registry"),
    ("sync_features.py", "Feature inventory"),
]


def run_sync(db_path: str = "tortoise.db", org: str = "daniel-ospina",
             root: str = ".") -> dict[str, str]:
    """Run all sync scripts sequentially. Returns {script: status}."""
    results: dict[str, str] = {}
    for script, label in _SCRIPTS:
        script_path = _SCRIPTS_DIR / script
        if not script_path.exists():
            results[label] = "SKIP (not found)"
            continue
        cmd = [sys.executable, str(script_path), "--db", db_path]
        if script == "sync_github.py":
            cmd += ["--org", org]
        if script in ("sync_products.py", "sync_roles.py"):
            cmd += ["--root", root]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        results[label] = r.stdout.strip() if r.returncode == 0 else f"ERROR: {r.stderr.strip()[:100]}"
    return results


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Run all org sync scripts")
    ap.add_argument("--db", default="tortoise.db", help="Path to tortoise.db")
    ap.add_argument("--org", default="daniel-ospina", help="GitHub org")
    ap.add_argument("--root", default=".", help="Repo root")
    args = ap.parse_args()
    results = run_sync(args.db, args.org, args.root)
    for label, status in results.items():
        print(f"  {label}: {status}")
