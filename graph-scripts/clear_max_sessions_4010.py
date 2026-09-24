#!/usr/bin/env python3
"""#4010 — clear stored `Team.max_sessions` so the removed 1000-session cap
cannot survive in data after it is gone from code.

WHY (the trap this closes)
-------------------------
`DEFAULT_MAX_SESSIONS = 1000` was only the *fallback* for an org whose stored
limits were absent. Deleting the constant therefore does **not** uncap an org
whose registry `:Team` node still carries `max_sessions = 1000` (or 40, or any
other direct-write value) — the row keeps capping it. This sweep sets those
rows to NULL (unlimited), deliberately and idempotently.

Two independent halves, either sufficient:
  * CODE (this PR): every resolver returns `max_sessions: None` — a stored
    value is no longer honoured as a cap.
  * DATA (this script): the stored rows are cleared, so a future reader that
    *does* look at the column cannot re-introduce the cap.

Idempotent — re-running finds nothing (`WHERE t.max_sessions IS NOT NULL`),
and `--dry-run` reports (and NAMES) the rows without writing. The clear is
IRREVERSIBLE: FalkorDB stores no property history and the `SET ... = NULL`
DELETES the property, so a per-org value that pre-dates the change is gone —
run `--dry-run` first and keep its per-team listing.

Scope: the REGISTRY graph only (`:Team` nodes + `apply_limits`' registry twin).
The hosted Supabase `orgs`/`teams` schema has no `max_sessions` column at all
(pre-#4010 the Supabase resolver always returned the constant), so there is
nothing to clear there — stated here so a reader does not go looking. In
Supabase mode the script therefore REFUSES and exits 0: opening the registry
namespace there would auto-create `registry_control_plane`, the graph the #669
flip deliberately deleted.

Usage:
    python3 graph-scripts/clear_max_sessions_4010.py [--dry-run] [--yes] [--uri URI]

Defaults to TORTOISE_DB_URI env var (or docker://:falkordb@localhost:6379/tortoise).
Test safety: the guard runs on the graph the sweep ACTUALLY writes (the
SDK-resolved `registry_control_plane`), never on the URI path — so a real write
always needs `--yes`.
"""
from __future__ import annotations

import argparse
import os
import sys

# Allow running from worktree root or graph-scripts/ dir
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

DEFAULT_URI = "docker://:falkordb@localhost:6379/tortoise"

#: Rows still carrying a stored cap — the sweep's match set.
_COUNT_STATEMENT = (
    "MATCH (t:Team) WHERE t.max_sessions IS NOT NULL RETURN count(t)"
)
#: `:Team` rows only (never Graph/Membership) — the quota field lives there.
_LIST_STATEMENT = (
    "MATCH (t:Team) WHERE t.max_sessions IS NOT NULL "
    "RETURN t.id, t.max_sessions ORDER BY t.id"
)
#: The sweep. NULL = unlimited; only touches rows that still carry a value, so
#: a second run is a no-op by construction.
_CLEAR_STATEMENT = (
    "MATCH (t:Team) WHERE t.max_sessions IS NOT NULL "
    "SET t.max_sessions = NULL RETURN count(t)"
)


def clear_stored_max_sessions(registry, *, dry_run: bool = False) -> dict:
    """NULL every stored `Team.max_sessions`. Idempotent.

    Importable so tests can drive it directly on an embedded test graph.
    Returns {"found": int, "cleared": int, "teams": [(id, stored_value), ...]}
    — `found` is the count of rows still carrying a value, `cleared` the
    number written to NULL (0 on a dry run and on an already-clean graph).

    `teams` names the rows on BOTH paths: `--dry-run` is the operator's only
    pre-flight before an irreversible write (the property is DELETED, and the
    registry lane has no operator-controlled backup), so the read-only pass
    must say which orgs it would change, not just how many.
    """
    count_rows = registry.query(_COUNT_STATEMENT).result_set
    found = int(count_rows[0][0] or 0) if count_rows else 0
    if found == 0:
        return {"found": 0, "cleared": 0, "teams": []}
    listed = registry.query(_LIST_STATEMENT).result_set or []
    teams = [(str(r[0]), r[1]) for r in listed]
    if dry_run:
        return {"found": found, "cleared": 0, "teams": teams}
    cleared_rows = registry.query(_CLEAR_STATEMENT).result_set
    cleared = int(cleared_rows[0][0] or 0) if cleared_rows else 0
    return {"found": found, "cleared": cleared, "teams": teams}


def _supabase_lane_refusal() -> str | None:
    """The #669 resurrection guard: a Supabase-mode control plane has NO
    registry graph — `registry_control_plane` is the graph the flip DELETED,
    and a MATCH/CREATE INDEX on it auto-creates it (the documented
    post-flip-verification finding, hosted_api.py, and the reason
    verify-cutover-preconditions.py refuses to probe a missing graph).
    Opening it here would resurrect a deliberately deleted production graph
    and then report a meaningless "0 rows — graph is clean" about the graph
    it just created. Supabase has no max_sessions column, so there is nothing
    to clear there anyway.
    """
    try:
        from tortoise.supabase_control import is_supabase_enabled
    except Exception as exc:  # pragma: no cover - exercised via monkeypatch
        # FAIL CLOSED. "Could not determine the mode" must never mean "not
        # Supabase": `tortoise.sdk` imports `supabase_control` lazily, so a
        # broken import still lets `main()` reach the registry namespace and
        # DELETE properties on a deployment that may be Supabase-backed —
        # i.e. it would auto-create the graph the #669 flip removed and then
        # report "graph is clean" about it. Refuse instead.
        return ("control plane mode could not be determined "
                f"({type(exc).__name__}: {exc}) — refusing to open the "
                "registry graph. Re-run once tortoise.supabase_control "
                "imports.")
    if is_supabase_enabled():
        return ("control plane is Supabase — the registry graph does not exist "
                "(there is no max_sessions column either). Refusing to open "
                "it; nothing to clear.")
    return None


def test_guard(graph_name: str, yes: bool = False) -> None:
    """Safety gate: must be given the graph the sweep ACTUALLY writes.

    The key difference here — and the reason this guard takes the RESOLVED
    name, NOT the URI path — is that `TortoiseSDK(namespace="registry")`
    always resolves to `<ns>_control_plane` (`registry_control_plane`),
    whatever the URI path says. Gating on the URI path would auto-approve a
    run whose path merely LOOKS test-prefixed while the write lands on the
    shared registry graph. Because that resolved name is never test-prefixed
    ON THE NO-`path=` CLI PATH THIS SCRIPT USES (the redirect that derives a
    test-prefixed name needs an explicit `path=`), a real write must always
    pass `--yes`. An in-test construction WITH an explicit `path=` does derive
    a `test_`-prefixed, sweepable name (#3634) — so the no-prefix invariant is
    scoped to the CLI path, not a property of the registry namespace.
    """
    if graph_name.startswith("tortoise_test_") or graph_name.startswith("test_"):
        print(f"✅ Test graph detected ({graph_name}) — proceeding")
        return
    if yes:
        print(f"⚠️  Production graph ({graph_name}) — --yes flag set, proceeding")
        return
    print(f"\n⚠️  Target graph is '{graph_name}' — NOT a test graph.")
    print("    This script CLEARS stored Team.max_sessions (→ NULL/unlimited)")
    print("    so the removed 1000-session cap cannot survive in data (#4010).")
    print("    Run with --yes to confirm, or use a test-prefixed graph.")
    sys.exit(1)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="#4010: clear stored max_sessions (1000-session cap) on :Team")
    ap.add_argument("--dry-run", action="store_true",
                    help="report only, no writes")
    ap.add_argument("--yes", action="store_true",
                    help="skip confirmation (required for non-test graphs)")
    ap.add_argument("--uri", default=os.environ.get("TORTOISE_DB_URI", DEFAULT_URI))
    args = ap.parse_args()

    # The #669 resurrection guard runs BEFORE any connection: opening the
    # registry namespace in Supabase mode would create the graph the flip
    # deleted.
    refusal = _supabase_lane_refusal()
    if refusal is not None:
        print(f"⛔ {refusal}")
        return 0

    # Connect through the SDK (registry namespace) so the sweep shares the
    # exact registry graph + namespace logic as resolve_org_limits /
    # apply_limits. The graph NAME is the SDK's to resolve
    # (namespace='registry' → `<ns>_control_plane`); the script never guesses.
    os.environ["TORTOISE_DB_URI"] = args.uri
    from tortoise.sdk import TortoiseSDK

    sdk = TortoiseSDK(namespace="registry")
    try:
        reg = sdk._get_registry()
        target = reg.name
        # The URI path never names this graph — `TortoiseSDK(namespace="registry")`
        # derives the registry name from the NAMESPACE, not from the URI path.
        test_guard(target, args.yes)
        print(f"Registry graph (SDK-resolved): {target}")
        report = clear_stored_max_sessions(reg, dry_run=args.dry_run)
    finally:
        sdk.close()

    mode = "DRY-RUN" if args.dry_run else "SWEEP"
    print(f"[{mode}] :Team rows carrying max_sessions: {report['found']}")
    for team_id, stored in report["teams"]:
        print(f"           · {team_id} → max_sessions={stored!r}")
    print(f"[{mode}] rows set to NULL:                  {report['cleared']}")
    if args.dry_run:
        print("\nDry-run complete — re-run without --dry-run to clear.")
    elif report["found"] == 0:
        print("\nNo stored max_sessions — graph is clean "
              "(idempotent sweep, safe to re-run).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
