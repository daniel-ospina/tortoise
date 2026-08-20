#!/usr/bin/env python3
"""verify-cutover preconditions — the #669 flip gate's precondition leg.

Part of the pre-deploy cutover gate (plan Task 10.1, issue #771). Asserts
the two preconditions that must hold BEFORE the single-deploy flip:

  1. Registry precondition — the FalkorDB control-plane registry graph
     (``registry_control_plane``, docs/registry-graph-schema.md) has node
     count == 0. Every registry writer was migrated to Supabase or disabled
     (plan Task 8, #765); zero nodes is the runnable proof.
  2. Supabase precondition — the Supabase control plane contains ONLY
     reconcilable placeholders: no ``teams`` rows, no ``api_keys`` rows,
     and every ``team_memberships`` row is the auth-trigger placeholder
     (``team_id=''`` AND ``key_hash='pending'``, migrations 0003/0010 —
     provisioned at signup by the tenant-provision RPC). Anything else
     means real data would have to migrate (legacy salted hashes are
     un-migratable without plaintext — the flip requires zero data).

The Supabase check runs through the SAME query() seam the app uses
(``SupabaseControlPlane`` live, ``FakeControlPlane`` in local/CI mode), so
the assertion logic is shared verbatim between the operator's live run and
the no-network CI run.

This script is READ-ONLY: it never writes to the registry, never creates
graphs, never deletes. Exit codes are documented in ``--help``.

Usage:
    # Local / CI equivalence mode (no network): fresh embedded DB + seam fake
    .github/scripts/verify-cutover-preconditions.py

    # Live pre-flip run (operator, prod creds in env):
    TORTOISE_DB_URI=<falkordb-uri> SUPABASE_URL=... \
        SUPABASE_SERVICE_ROLE_KEY=... \
        .github/scripts/verify-cutover-preconditions.py --live

Exit codes:
    0 — preconditions hold
    1 — an assertion failed (registry non-empty / Supabase not placeholder-only)
    2 — could not run (missing env, DB unreachable, fake unavailable)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The registry graph name (namespace='registry' → f"{ns}_control_plane",
# tortoise/sdk.py _get_registry; docs/registry-graph-schema.md).
REGISTRY_GRAPH = "registry_control_plane"

# Placeholder sentinels written by the auth trigger (migration 0003) and
# reconciled in place by tenant-provision (migration 0010 RPC): a membership
# row with team_id='' / key_hash='pending' is a signup in flight, NOT data.
PLACEHOLDER_TEAM_ID = ""
PLACEHOLDER_KEY_HASH = "pending"

# Tables that must contain no real rows before the flip.
_ZERO_ROW_TABLES = ("teams", "api_keys")


def check_registry_empty(db_path: str | None = None, db_uri: str | None = None) -> list[str]:
    """Assert the registry graph has zero nodes. Returns a list of failures
    (empty = pass). Strictly READ-ONLY: opens the graph via FalkorProjection
    (no SDK index creation, no writes of any kind).

    #771 review P2: a missing graph is treated as EMPTY without querying it
    — FalkorDB's GRAPH.QUERY auto-creates a missing graph, so a naive count
    on a deleted registry would RESURRECT it (and a post-delete re-run of
    the gate would recreate the exact artifact the flip removes). The graph
    is checked against proj.db.list_graphs() first; only an EXISTING graph
    is counted.
    """
    from tortoise.projection import FalkorProjection

    failures: list[str] = []
    proj = None
    try:
        if db_uri:
            proj = FalkorProjection.from_uri(db_uri)
        elif db_path:
            proj = FalkorProjection(db_path)
        else:
            # Local/CI equivalence: a fresh embedded DB — empty by construction.
            proj = FalkorProjection(os.path.join(tempfile.mkdtemp(), "gate.db"))
        if REGISTRY_GRAPH not in proj.db.list_graphs():
            # Missing = empty (never query — GRAPH.QUERY would auto-create).
            return failures
        reg = proj.db.select_graph(REGISTRY_GRAPH)
        rows = reg.query("MATCH (n) RETURN count(n)").result_set
        count = int(rows[0][0]) if rows else 0
        if count != 0:
            failures.append(
                f"registry {REGISTRY_GRAPH} has {count} node(s) — every "
                "registry writer must be migrated or disabled before the "
                "flip (plan Task 8 writer inventory, #765).")
    except Exception as e:  # pragma: no cover — env problem, not a pass
        failures.append(f"registry query failed ({e}) — DB unreachable")
    finally:
        if proj is not None:
            try:  # noqa: SIM105
                proj.close()
            except Exception:
                pass
    return failures


def check_supabase_placeholders(cp) -> list[str]:
    """Assert the Supabase control plane holds ONLY reconcilable placeholders.

    ``cp`` is any adapter exposing ``query(table, select=..., filters=...)``
    (the #669 seam): live ``SupabaseControlPlane`` or the in-memory
    ``FakeControlPlane``. Returns a list of failures (empty = pass). Fail-
    closed: a query error raises RuntimeError (the caller surfaces it as a
    cannot-run, never as a pass).
    """
    failures: list[str] = []
    for table in _ZERO_ROW_TABLES:
        rows = cp.query(table, select=["id"])
        if rows:
            failures.append(
                f"Supabase {table} has {len(rows)} row(s) — the flip requires "
                "zero real rows (legacy salted hashes are un-migratable "
                "without plaintext; plan Task 1 P1-1).")
    memberships = cp.query("team_memberships", select=["team_id", "key_hash"])
    for i, row in enumerate(memberships):
        if row.get("team_id") != PLACEHOLDER_TEAM_ID or \
                row.get("key_hash") != PLACEHOLDER_KEY_HASH:
            failures.append(
                f"Supabase team_memberships[{i}] is NOT a reconcilable "
                "placeholder (team_id={row.get('team_id')!r}, "
                f"key_hash={row.get('key_hash')!r}) — expected "
                f"team_id={PLACEHOLDER_TEAM_ID!r} / "
                f"key_hash={PLACEHOLDER_KEY_HASH!r} (migration 0003 trigger).")
    return failures


def build_control_plane(*, live: bool, fake_seed_json: str | None):
    """Build the seam adapter for the Supabase precondition.

    live=True → ``SupabaseControlPlane`` (requires SUPABASE_URL + a
    service-role key in env; raises RuntimeError otherwise — fail-closed).
    live=False → ``FakeControlPlane`` (in-memory, zero network; optionally
    seeded from a JSON string so tests/operators can exercise violations).
    """
    if live:
        from tortoise.supabase_control import SupabaseControlPlane
        return SupabaseControlPlane()
    try:
        from tests.fake_control_plane import FakeControlPlane
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            f"cannot import tests.fake_control_plane for the local/CI "
            f"equivalence mode: {e}. Run from the repo root, or use --live "
            f"with Supabase creds.") from e
    seed = json.loads(fake_seed_json) if fake_seed_json else {}
    return FakeControlPlane(seed)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="verify-cutover-preconditions",
        description="Assert the #669 flip preconditions (registry empty; "
                    "Supabase placeholder-only). READ-ONLY.",
        epilog="Exit codes: 0 preconditions hold; 1 assertion failed; "
               "2 could not run.",
    )
    ap.add_argument("--live", action="store_true",
                    help="Check the REAL Supabase control plane (requires "
                         "SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY). Default "
                         "is the local/CI seam-fake equivalence mode.")
    ap.add_argument("--db-path", help="Embedded FalkorDBLite DB path "
                    "(default: a fresh temp DB — empty by construction).")
    ap.add_argument("--db-uri", help="FalkorDB URI (overrides "
                    "TORTOISE_DB_URI env).")
    ap.add_argument("--fake-cp-seed-json",
                    help="JSON seed for the fake control plane, e.g. "
                         '{"teams": [{"id": "t"}]} (test/forensic use).')
    args = ap.parse_args(argv)

    db_path = args.db_path or os.environ.get("TORTOISE_DB_PATH") or None
    db_uri = args.db_uri or os.environ.get("TORTOISE_DB_URI") or None
    if args.live and db_path is not None:
        # Review P1 (PR #878): an embedded dev DB is empty by construction —
        # the LIVE gate must check the REAL registry via TORTOISE_DB_URI;
        # TORTOISE_DB_PATH would false-PASS over a non-empty production
        # registry.
        print("verify-cutover: CANNOT RUN (--live) — TORTOISE_DB_PATH is "
              "refused in --live; set TORTOISE_DB_URI to the real registry.",
              file=sys.stderr)
        return 2

    assertion_failures = 0
    cannot_run = False
    # ── 1) Registry precondition ──
    reg_failures = check_registry_empty(db_path=db_path, db_uri=db_uri)
    if reg_failures:
        assertion_failures += 1
        for f in reg_failures:
            print(f"verify-cutover: FAIL — {f}", file=sys.stderr)

    # ── 2) Supabase placeholder precondition ──
    try:
        cp = build_control_plane(live=args.live,
                                 fake_seed_json=args.fake_cp_seed_json)
        cp_mode = "LIVE Supabase" if args.live else "seam FAKE (local/CI equivalence)"
        if not args.live and not args.fake_cp_seed_json:
            print("verify-cutover: note — Supabase precondition checked "
                  "against the seam FAKE (no SUPABASE_URL / service key); "
                  "the operator's live run needs --live with prod creds.")
        sb_failures = check_supabase_placeholders(cp)
        if sb_failures:
            assertion_failures += 1
            for f in sb_failures:
                print(f"verify-cutover: FAIL — {f}", file=sys.stderr)
        else:
            print(f"verify-cutover: OK — Supabase precondition holds "
                  f"({cp_mode}: no real teams/api_keys rows, "
                  "memberships placeholder-only).")
    except Exception as e:
        cannot_run = True  # never a pass
        print(f"verify-cutover: CANNOT RUN — Supabase precondition: {e}",
              file=sys.stderr)

    if assertion_failures:
        # Exit-code contract (review P2, PR #878): 1 = an assertion was
        # VIOLATED (detected but not fixed), 2 = could not run. An assertion
        # failure wins over a cannot-run on the other leg — the operator
        # must see "assertion failed" even when the second leg couldn't
        # run, so a masked violation is impossible.
        return 1
    if cannot_run:
        return 2
    print("verify-cutover: PASS — preconditions hold "
          f"(registry {REGISTRY_GRAPH} empty, Supabase placeholder-only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
