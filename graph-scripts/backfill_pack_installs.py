#!/usr/bin/env python3
"""#318 — backfill per-tenant pack activation records (``PackInstall``).

Idempotent activation of the starter pack set for EXISTING teams (pre-#318
tenants have no pack install-state). Safe to re-run: ``ensure_tenant_packs``
is an additive MERGE per namespace — never duplicates, never uninstalls.

D5 (plan docs/plans/2026-08-15-318-pack-isolation-plan.md): handles the
legacy ``team_{name}`` vs ``team_{id}`` graph naming. Install records ALWAYS
land in the introspection READ TARGET (``team_{team_id}`` — the graph GET
/v1/packs + packs_list read via ``namespace=team_id``). The RECORDED
graph_name (``teams.graph_name`` in Supabase control-plane mode, the Team
node's ``graph_name`` property in registry mode) is read only to DETECT and
report legacy ``team_{name}`` tenants — writing into a legacy graph would
leave backfilled records invisible to the read surface (code-review conf 70,
PR #1261) and the self-heal would mint a duplicate set.

Usage:
    python3 graph-scripts/backfill_pack_installs.py            # DRY-RUN (default)
    python3 graph-scripts/backfill_pack_installs.py --apply    # write installs
    python3 graph-scripts/backfill_pack_installs.py --apply --starter dev,marketing

Starter set: ``TORTOISE_STARTER_PACKS`` env when set, else the built-in
default (dev,marketing,product-strategy,pm). Unknown names are skipped with
a warning — never a failure.
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow running from the worktree root or graph-scripts/ dir.
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from tortoise.pack_state import (  # noqa: E402
    DEFAULT_STARTER_PACKS, ensure_tenant_packs,
)


def _iter_teams() -> list[dict]:
    """Every existing (non-deleted) team as {team_id, graph_name}.

    Supabase control-plane mode: ``teams`` table rows (graph_name recorded
    via the provision_team RPC's p_graph_name). Registry mode: ``Team``
    nodes (graph_name property when present — legacy /v1/teams + onboarding
    recorded team_{name}; hosted provisioning has none → team_{id} derivation).
    """
    try:
        from tortoise.supabase_control import get_control_plane, is_supabase_enabled
        if is_supabase_enabled():
            rows = get_control_plane().query(
                "teams", select=["id", "graph_name"],
                filters=[("deleted_at", "is", None)],
            )
            return [{"team_id": r["id"], "graph_name": r.get("graph_name")}
                    for r in rows if r.get("id")]
    except Exception as e:  # noqa: BLE001 — best-effort enumeration
        print(f"⚠️  Supabase team enumeration failed ({e}) — trying registry mode")
    from tortoise.sdk import TortoiseSDK
    sdk = TortoiseSDK()
    rows = sdk._get_registry().query(
        "MATCH (t:Team) WHERE t.deleted_at IS NULL RETURN t.id, t.graph_name"
    ).result_set
    return [{"team_id": r[0], "graph_name": r[1] if len(r) > 1 else None}
            for r in rows if r and r[0]]


def main() -> int:
    ap = argparse.ArgumentParser(
        description="#318: backfill per-tenant pack activation records")
    ap.add_argument("--apply", action="store_true",
                    help="write activation records (default is DRY-RUN)")
    ap.add_argument("--starter", default=os.environ.get("TORTOISE_STARTER_PACKS", ""),
                    help="comma-separated starter pack namespaces "
                         "(default: TORTOISE_STARTER_PACKS env or the built-in set)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only process the first N teams (sanity check)")
    args = ap.parse_args()

    starter = [s.strip() for s in args.starter.split(",") if s.strip()] \
        if args.starter.strip() else list(DEFAULT_STARTER_PACKS)
    print(f"Starter set: {', '.join(starter)}")
    print(f"Mode: {'APPLY (writes)' if args.apply else 'DRY-RUN (no writes)'}")

    teams = _iter_teams()
    if not teams:
        print("No existing teams found — nothing to backfill.")
        return 0
    if args.limit:
        teams = teams[: args.limit]

    n_activated = 0
    for t in teams:
        team_id, recorded = t["team_id"], t["graph_name"]
        # D5 (code-review conf 70, PR #1261): ALWAYS target the introspection
        # read surface — team_{team_id}. GET /v1/packs and MCP packs_list read
        # the SDK-derived team_{team_id} graph (namespace=team_id), never the
        # legacy team_{name} graph recorded by sdk.team_create; landing
        # installs there would make backfilled records invisible and the read
        # surface's self-heal would mint a second set.
        graph_name = f"team_{team_id}"
        if recorded and recorded != graph_name:
            print(f"· team {team_id}: recorded graph {recorded!r} is legacy "
                  f"team_{{name}} — landing installs in read target {graph_name}")
        if args.apply:
            from tortoise.sdk import TortoiseSDK
            sdk = TortoiseSDK(namespace=team_id)
            try:
                activated = ensure_tenant_packs(sdk, graph_name=graph_name)
                print(f"✔ team {team_id} -> graph {graph_name}: "
                      f"{len(activated)} pack(s) active")
                n_activated += len(activated)
            except Exception as e:  # noqa: BLE001 — one bad team never aborts
                print(f"✖ team {team_id} -> graph {graph_name}: FAILED ({e})")
        else:
            print(f"· team {team_id} -> graph {graph_name}: "
                  f"would activate {', '.join(starter)} (dry-run)")
            n_activated += len(starter)

    print(f"\nDone: {len(teams)} team(s) processed, "
          f"{n_activated} activation record(s) "
          f"{'written' if args.apply else 'would be written (dry-run)'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
