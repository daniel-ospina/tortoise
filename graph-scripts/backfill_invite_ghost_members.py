#!/usr/bin/env python3
"""#1908 — one-time backfill: delete pre-#1880 invite-{iid} ghost memberships.

Registry invites mint a fake `Membership(user_id='invite-{iid}')` row so
list_members can show the 'invited' placeholder. #1880 wired deletion into
accept/rescind/decline; the EXPIRY path and everything created before the
#1880 deploy were left behind — a consumed/expired/revoked invite keeps its
ghost row forever.

This sweep deletes `invite-*` Membership rows whose backing Invitation node
is TERMINAL:
  - consumed   (accepted_at set / status='accepted')
  - expired    (expires_at past / status='expired')
  - revoked    (status='revoked')
  - orphaned   (no Invitation node exists)
Rows backing a still-pending, unexpired invite are KEPT (the legit
'invited' placeholder). Supabase path untouched (fake rows are registry-only).

Idempotent — re-running finds nothing. `--dry-run` reports without writing.

Usage:
    python3 graph-scripts/backfill_invite_ghost_members.py [--dry-run] [--yes] [--uri URI]

Defaults to TORTOISE_DB_URI env var (or docker://:falkordb@localhost:6379/tortoise).
Test safety: the guard runs on the graph the sweep ACTUALLY writes (the
SDK-resolved registry graph), never on the URI path — so a test-prefixed
`--uri` never auto-approves a write to the shared registry graph, and `--yes`
is required for a real run (#5188).
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow running from worktree root or graph-scripts/ dir
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

DEFAULT_URI = "docker://:falkordb@localhost:6379/tortoise"


def test_guard(graph_name: str, yes: bool = False) -> None:
    """Safety gate: must be given the graph the sweep ACTUALLY writes.

    The key difference here — and the reason this guard takes the RESOLVED
    name, NOT the URI path — is that `TortoiseSDK(namespace="registry")`
    resolves to `<ns>_control_plane` (`registry_control_plane`) whatever the
    URI path says. Gating on the URI path auto-approves a run whose path
    merely LOOKS test-prefixed while the write lands on the shared registry
    graph (#5188). Because that resolved name is never test-prefixed on the
    no-`path=` CLI path this script uses, a real write must always pass
    `--yes`.
    """
    if graph_name.startswith("tortoise_test_") or graph_name.startswith("test_"):
        print(f"✅ Test graph detected ({graph_name}) — proceeding")
        return
    if yes:
        print(f"⚠️  Production graph ({graph_name}) — --yes flag set, proceeding")
        return
    print(f"\n⚠️  Target graph is '{graph_name}' — NOT a test graph.")
    print("    This script DELETES ghost invite-* Membership rows whose")
    print("    Invitation is consumed/expired/revoked/orphaned.")
    print("    Run with --yes to confirm, or use a test-prefixed graph.")
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="#1908: Backfill sweep for invite-{iid} ghost memberships")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only, no writes")
    ap.add_argument("--yes", action="store_true",
                    help="skip confirmation (required for non-test graphs)")
    ap.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI", DEFAULT_URI))
    args = ap.parse_args()

    # Connect through the SDK (registry namespace) so the sweep shares the
    # exact registry graph + namespace logic as the invite endpoints — and so
    # the printed registry name comes from that SAME derivation. #3634: do
    # not re-derive it here; a second copy drifts from sdk._get_registry.
    os.environ["TORTOISE_DB_URI"] = args.uri
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(namespace="registry")
    try:
        reg = sdk._get_registry()
        target = reg.name
        # The URI path never names this graph — `TortoiseSDK(namespace="registry")`
        # derives the registry name from the NAMESPACE, not from the URI path.
        # #5188: gate on the RESOLVED name, never the URI path — a test-prefixed
        # `--uri` used to auto-approve a sweep of the shared registry graph.
        test_guard(target, args.yes)
        print(f"Registry graph (SDK-resolved): {target}")
        result = sdk.sweep_invite_ghost_memberships(dry_run=args.dry_run)
    finally:
        sdk.close()

    mode = "DRY-RUN" if args.dry_run else "SWEEP"
    print(f"[{mode}] invite-* membership rows found:    {result['found']}")
    print(f"[{mode}] terminal-state ghosts (deletable): {result['ghosts']}")
    print(f"[{mode}] rows deleted:                      {result['deleted']}")
    if args.dry_run:
        print("\nDry-run complete — re-run without --dry-run to delete.")
    elif result["ghosts"] == 0:
        print("\nNo ghosts — graph is clean (idempotent sweep, safe to re-run).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
