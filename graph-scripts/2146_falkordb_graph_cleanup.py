#!/usr/bin/env python3
"""2146 — drop e2e-live orphan FalkorDB graphs (companion to the Supabase cleanup).

Issue: daniel-ospina/tortoise#2146. Each red-window welcome-mode provisioning
mint seeded a FalkorDB graph `org_{org_id}` where
org_id = sha256(user_id).hexdigest()[:26] (see supabase/functions/
tenant-provision/index.ts). The Supabase side is cleaned by
2146_e2e_live_orphan_cleanup.py; THIS script drops the graphs for the teams in
the manifest.

It resolves FALKORDB_CLOUD_URI → TORTOISE_DB_URI exactly like entrypoint.sh
lines 89-93, and drops graphs with `select_graph(name).delete()` — the same
call the in-repo rollback paths use (tortoise/hosted_api.py — the
mint-failure compensation calls).
(NOTE: the production purge helper `_drop_team_graph_impl` in
hosted_api.py branches on `hasattr(proj.db, "delete_graph")`; the pip
falkordb cloud client exposes NO delete_graph attribute, so that branch
log-and-skips on FalkorDB Cloud — tracked separately as daniel-ospina/
tortoise#2163. THIS script's GRAPH.DELETE works regardless.)

DRY-RUN by default. GRAPH.DELETE only runs with --execute.
Deletes are strictly limited to graph names present in the manifest's
graph_name column — never a wildcard. Idempotent: absent graphs are skipped.

Required access: FALKORDB_CLOUD_URI (FalkorDB Cloud connection string; a gh
secret on daniel-ospina/tortoise — the value is NOT available locally; inject
it via a GH Actions workflow or `gh secret` consumers), plus this repo's venv
(`uv sync` — the falkordb client is a dependency).

Usage:
  python3 graph-scripts/2146_falkordb_graph_cleanup.py --manifest 2146-e2e-live-orphans.manifest.json            # dry-run: list
  python3 graph-scripts/2146_falkordb_graph_cleanup.py --manifest 2146-e2e-live-orphans.manifest.json --execute   # GRAPH.DELETE
  FALKORDB_CLOUD_URI=redis://:<pw>@<host>:<port> python3 ...   # URI via env (or TORTOISE_DB_URI directly)

Rollback note: GRAPH.DELETE is irreversible and there is no backup of these
free-tier test graphs (backup_enabled=false). The manifest is the only record
of their existence — keep it before executing.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"graph-scripts/2146_falkordb_graph_cleanup.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python graph-scripts/2146_falkordb_graph_cleanup.py`"
    )

import argparse
import json
import os


class OpError(Exception):
    """Fatal, human-readable error."""


def _get_db():
    # Mirror entrypoint.sh: FALKORDB_CLOUD_URI → TORTOISE_DB_URI (prod path).
    uri = os.environ.get("TORTOISE_DB_URI") or os.environ.get("FALKORDB_CLOUD_URI")
    if not uri:
        raise OpError(
            "Neither TORTOISE_DB_URI nor FALKORDB_CLOUD_URI is set. The FalkorDB "
            "Cloud connection string is a gh secret on daniel-ospina/tortoise and "
            "is NOT readable locally — run this where the secret is injectable "
            "(GH Actions workflow_dispatch, Fly machine, or an operator shell).")
    os.environ["TORTOISE_DB_URI"] = uri
    try:
        from tortoise.sdk import TortoiseSDK
    except ImportError:
        raise OpError(
            "tortoise SDK not importable — run via `uv run python3 ...` in the "
            "repo (falkordb client is a dependency)") from None
    sdk = TortoiseSDK(namespace="registry")  # graph list/delete are DB-wide
    proj = sdk._get_proj()
    return proj.db


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", default="2146-e2e-live-orphans.manifest.json")
    ap.add_argument("--execute", action="store_true",
                    help="REAL GRAPH.DELETE (default: dry-run list)")
    args = ap.parse_args()

    with open(args.manifest) as fh:
        manifest = json.load(fh)
    teams = manifest.get("teams") or []
    target_graphs = sorted({t.get("graph_name") for t in teams if t.get("graph_name")})
    if not target_graphs:
        print("[falkordb] manifest has no graph_name entries — nothing to do")
        return 0
    # Guard: every target must be exactly <prefix>_<26 hex> (mint convention).
    # #3543: the prefix is `org_` for graphs minted after the rename and
    # `team_` for those minted before it — the manifest may legitimately
    # carry either, and the pattern stays fully anchored for both.
    import re
    bad = [g for g in target_graphs
           if not re.fullmatch(r"(?:org|team)_[0-9a-f]{26}", g)]
    if bad:
        raise OpError(
            f"GUARD FAIL — manifest graphs not <org|team>_<26hex> shape: {bad[:5]}")

    db = _get_db()
    try:
        live = set(db.list_graphs())
    except Exception as e:
        raise OpError(f"GRAPH.LIST failed (dead connection?): {e!r} — aborting "
                      "(fail closed, mirroring hosted_backup)") from None

    present = [g for g in target_graphs if g in live]
    missing = [g for g in target_graphs if g not in live]
    print(f"[falkordb] manifest graphs: {len(target_graphs)}  live in store: "
          f"{len(present)}  already absent: {len(missing)}")
    for g in present:
        print(f"  {'[DRY-RUN] would delete' if not args.execute else '[delete]'} {g}")
    if missing:
        print(f"  already absent (skipped): {missing[:5]}{' …' if len(missing) > 5 else ''}")
    if not args.execute:
        print("\nDRY-RUN complete — no graphs deleted. Re-run with --execute.")
        return 0
    for g in present:
        try:
            db.select_graph(g).delete()  # GRAPH.DELETE — same call as prod purge
            print(f"[falkordb] deleted {g}")
        except Exception as e:
            print(f"[falkordb] FAILED {g}: {e!r} — re-run to retry (idempotent)", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OpError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
