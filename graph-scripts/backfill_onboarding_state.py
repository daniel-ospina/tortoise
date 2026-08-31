#!/usr/bin/env python3
"""#2001 (W5) — grandfathered OnboardingState backfill (migration LAST).

Idempotently backfills the FLOW-relevant legacy field (completion status)
from jsonb/Team-node onboarding_state into the tenant graph's
OnboardingState node for grandfathered orgs. OPERATIONAL keys stay jsonb —
never migrated. Safe to re-run (absent-node-only; a node-present org is
never touched).

Contract (scope pin 14):
- absent-node-only reconciliation: jsonb onboarding_complete=true →
  node.status='complete' (one-directional); NEVER jsonb-false → complete;
  NEVER status → jsonb.
- fork stays null — read-time default (J6); persisted only on explicit
  opt-in (the checkpoint fork card / grandfathered opt-in).
- exclusions: placeholder teams.id='' + soft-deleted (deleted_at non-null).
- re-run no-op; wire stable across backfill/materialization/flip (DE2E-6).

Usage:
    python3 graph-scripts/backfill_onboarding_state.py            # DRY-RUN
    python3 graph-scripts/backfill_onboarding_state.py --apply    # write nodes
    python3 graph-scripts/backfill_onboarding_state.py --recompute [--apply]
        # T7 recompute sweep: grandfathered branch first (zero agent edges +
        # legacy complete → status stays complete, never re-onboarded), then
        # fork-aware gate eval for edge-bearing orgs (monotonic).
"""
from __future__ import annotations

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from tortoise.onboarding import state as _os  # noqa: E402


def _iter_teams() -> list[dict]:
    """Every existing (non-deleted) team as {team_id, legacy_complete}.

    Supabase control-plane mode: ``teams`` table rows (onboarding_state
    jsonb; placeholder id '' excluded). Registry mode: ``Team`` nodes
    (onboarding_state JSON-string; deleted_at null; id '' excluded).
    """
    try:
        from tortoise.supabase_control import get_control_plane, is_supabase_enabled
        if is_supabase_enabled():
            rows = get_control_plane().query(
                "teams", select=["id", "onboarding_state"],
                filters=[("deleted_at", "is", None)],
            )
            out = []
            for r in rows:
                tid = r.get("id")
                if not tid:
                    continue
                state = r.get("onboarding_state") or {}
                out.append({"team_id": tid,
                            "legacy_complete": bool(
                                (state or {}).get("onboarding_complete"))})
            return out
    except Exception as e:  # noqa: BLE001, RUF100
        print(f"⚠️  Supabase team enumeration failed ({e}) — trying registry mode")
    import json as _json

    from tortoise.sdk import TortoiseSDK
    # Hosted team records live in the registry graph (registry_tortoise — the
    # namespace=registry surface hosted_api reads); bare TortoiseSDK() would
    # hit the URI/embedded default graph instead.
    sdk = TortoiseSDK(namespace="registry")
    rows = sdk._get_registry().query(
        "MATCH (t:Team) WHERE t.deleted_at IS NULL RETURN t.id, t.onboarding_state"
    ).result_set
    out = []
    for r in rows:
        if not r or not r[0]:
            continue
        raw = r[1] if len(r) > 1 else None
        try:
            state = _json.loads(raw) if isinstance(raw, str) else (raw or {})
        except (TypeError, ValueError):
            state = {}
        out.append({"team_id": r[0],
                    "legacy_complete": bool(
                        (state or {}).get("onboarding_complete"))})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="#2001 (W5): grandfathered OnboardingState backfill")
    ap.add_argument("--apply", action="store_true",
                    help="write nodes (default is DRY-RUN)")
    ap.add_argument("--recompute", action="store_true",
                    help="T7 recompute sweep (gate eval + grandfathered branch)")
    ap.add_argument("--limit", type=int, default=0,
                    help="only process the first N orgs (sanity check)")
    args = ap.parse_args()

    print(f"Mode: {'APPLY (writes)' if args.apply else 'DRY-RUN (no writes)'}"
          + (" + recompute sweep" if args.recompute else ""))
    teams = _iter_teams()
    if not teams:
        print("No existing teams found — nothing to backfill.")
        return 0
    if args.limit:
        teams = teams[: args.limit]

    created = skipped_present = skipped_incomplete = recomputed = 0
    for t in teams:
        team_id = t["team_id"]
        try:
            from tortoise.sdk import TortoiseSDK
            graph = TortoiseSDK(namespace=team_id)._get_proj()
            if args.recompute:
                outcome = _os.recompute_completion(
                    graph, team_id, t["legacy_complete"])
                if outcome.startswith("complete"):
                    recomputed += 1
                    print(f"· {team_id}: recompute → {outcome}")
                continue
            res = _os.backfill_org(graph, team_id, t["legacy_complete"],
                                   dry_run=not args.apply)
            action = res["action"]
            if action == "created-complete":
                created += 1
            elif action == "skipped-node-present":
                skipped_present += 1
            elif action == "skipped-not-complete":
                skipped_incomplete += 1
            if action.startswith("would"):
                print(f"· {team_id}: {action} (dry-run)")
        except Exception as e:  # noqa: BLE001, RUF100
            print(f"✖ {team_id}: FAILED ({e})")

    if args.recompute:
        print(f"\nDone: {len(teams)} org(s) scanned, {recomputed} completed "
              f"by the recompute sweep.")
    else:
        print(f"\nDone: {len(teams)} org(s) processed — created {created}, "
              f"skipped-node-present {skipped_present}, "
              f"skipped-not-complete {skipped_incomplete} "
              f"({'written' if args.apply else 'would be written (dry-run)'}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
