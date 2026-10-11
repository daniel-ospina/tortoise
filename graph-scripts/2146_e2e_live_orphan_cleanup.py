#!/usr/bin/env python3
"""2146 — enumerate + clean e2e-live orphan teams (Supabase control plane).

Issue: daniel-ospina/tortoise#2146 (ops/prod-cleanup). The welcome-e2e-monitor's
live signup smoke was red for ~14 days (2026-08-19T22:20 → 2026-09-02): the
/welcome route stub was dead after the #1566 cross-site redirect change, so the
REAL app root loaded every run and ran #1566 welcome-mode provisioning under
emails matching `e2e-live-<8hex>@premise-labs.dev` — minting a prod team +
api_keys row + FalkorDB graph per run. Monitor teardown deleted only the auth
user (FK cascade removes org_memberships); the minted organizations/api_keys
rows and
FalkorDB graphs were never cleaned (fix PR #2144 prevents NEW mints).

Mint path (verified in repo, 2026-09-02):
  dashboard welcome-mode (website/apps/dashboard/src/main.jsx provisionInApp)
  → Supabase edge fn `tenant-provision` (supabase/functions/tenant-provision/
    index.ts) → `provision_team` SECURITY DEFINER RPC (migration 0010) which
    writes teams + org_memberships + api_keys atomically, then FastAPI
    `/internal/demo` seeds the FalkorDB graph `team_{org_id}` (the red-window
    convention; #3543/#3622 later renamed the prefix to `org_`) where
    org_id = sha256(user_id).hexdigest()[:26] (deterministic per user).

This script cleans the SUPABASE side (auth users + teams + api_keys +
org_memberships + invitations + abuse_events). FalkorDB graphs are dropped by
the companion script 2146_falkordb_graph_cleanup.py (separate store, needs
FALKORDB_CLOUD_URI).

SAFETY MODEL
  * DRY-RUN by default. Every destructive phase requires --execute.
  * Guard A — email shape: rows matching
      ^e2e-live-[0-9a-f]{8}@premise-labs\.dev$
    are the DEFAULT delete scope (all 222 live rows matched on 2026-09-02).
    The outer bound is the e2e-live NAMESPACE
    (`email LIKE 'e2e-live-%@premise-labs.dev'`): a row inside the namespace
    but OFF the Guard-A shape is Guard-D residue (#5971) and enters the delete
    set ONLY behind the explicit, review-gated --include-residue (below).
  * Guard B — window: default scope is teams created in the red window
    2026-08-19T22:20:00Z → 2026-09-03T00:00:00Z (154 rows). Pass --all-e2e-live
    to include the 68 pre-window monitor mints (same mint path — review first).
    Guard-D residue is ALL-TIME — the window never applies to it.
  * Idempotent: every delete is enumerated up-front (manifest) and re-runs
    after a successful cleanup find nothing.
  * The teams row is deleted LAST (children first) — the product's retry-anchor
    ordering (purge_org_control_plane, tortoise/supabase_control.py).
  * An append-only audit_events row is written per purged team BEFORE the teams
    delete (audit_events has no FK to teams — the delete trail survives).

DELETE-ORDER RATIONALE (mirrors tortoise purge semantics; FK catalog as of the
2026-09-02 red window — #3543 later renamed `user_teams_user_id_fkey` to
`org_memberships_user_id_fkey`):
  1. auth.users (remaining e2e-live accounts) via GoTrue Admin API — FK
     `org_memberships_user_id_fkey` (org_memberships.user_id → auth.users) ON
     DELETE CASCADE removes their memberships; auth-internal tables cascade.
  2. api_keys WHERE org_id IN (...) — has FK ON DELETE CASCADE, deleted
     explicitly for an auditable, non-cascade-dependent sequence.
  3. org_memberships WHERE org_id IN (...) — org_id has NO FK (explicit
     delete required; also covers any row whose user is already gone).
     (2 and 3 are independent children of the organizations row; their
     relative order is immaterial, and this matches phase_db + §4.)
  4. invitations WHERE org_id IN (...) — FK CASCADE, explicit anyway.
  5. abuse_events WHERE org_id IN (...) — FK CASCADE (0015 key_create rows
     minted by provision_team), explicit anyway.
  6. audit_events INSERT per team (operation=e2e_live_orphan_purged). Each run
     carries a fresh uuid in the audit id so a partial failure (INSERT ok,
     organizations DELETE failed) is re-runnable without PK collision (audit_events is
     append-only — immutable trigger, migration 0002).
  7. organizations WHERE id IN (...) LAST — children gone; row = retry anchor.
  FalkorDB graphs: companion script, run against the same manifest (may run
  before or after; a graph left behind is a benign orphan with no DB row).

Note on FK survival: metering_records/oauth/agent_signup_tokens/graphs all
have REFERENCES organizations(id) ON DELETE CASCADE (migrations 0014/0016/
20260814000001/20260901000001) so they die with the team row; audit_events
(immutable append-only) and analytics_events (no FK + its own immutability
trigger, migration 0004) survive by design and are NOT script-deleted — audit
rows are the intended trail; analytics rows are counted (CHILDREN_SQL/Q3)
but a bare DELETE would fire analytics_events_immutable and strand the purge
(#2162 re-review ISSUE A).

REQUIRED ACCESS (operator)
  * SQL:  SUPABASE_ACCESS_TOKEN (Management API) OR supabase CLI linked to the
          project (project ref ybetwichurajbfswfeqa — `supabase db query
          --linked`). Both were verified working 2026-09-02.
  * Users: SUPABASE_URL + SUPABASE_SERVICE_KEY (GoTrue Admin API — the same
          mechanism as tests/e2e/supabase_admin.py), OR --delete-users-via sql
          (postgres superuser; cascades auth-internal FKs — fine for these
          synthetic accounts).
  All are GitHub Actions secrets on daniel-ospina/tortoise (names only:
  SUPABASE_ACCESS_TOKEN, SUPABASE_URL, SUPABASE_SERVICE_KEY).

GUARD D — RESIDUE (#5971). The weekly reconcile detector
(`graph-scripts/e2e_live_reconcile.py`) counts every row in the namespace
(`email LIKE 'e2e-live-%@premise-labs.dev'`, all-time), but this script's DELETE
scope was only Guard A's strict shape AND the historical window. A row inside
the namespace but outside Guard A was therefore DETECTED forever and deleted by
NO phase — silently, so the reconcile issue read as "just not cleaned yet"
while nothing in the repo could clean it. The live example is the #3781
manual-verification account `e2e-live-3781-<hex>@premise-labs.dev` (created
2026-09-17; the operator had no SUPABASE_SERVICE_KEY, so the monitor's Admin-API
teardown could not run — see PR #3790's commit message).
  * The residue is ALWAYS enumerated (on the phases that enumerate: --phase
    enumerate|all), ALL-TIME, explicitly NOT matching the guard shape — a row
    that does match is refused as a guard logic error — and always PRINTED, on
    every phase, plus recorded in the manifest's `residue` key.
  * It is NOT in the delete set by default. `--include-residue` promotes it
    AFTER the operator has read the printed list — an explicit, review-gated
    inclusion, the same shape as `--all-e2e-live` (never a widened regex: the
    guard is what keeps a non-test row out).
  * Recurrences use a SEPARATE --manifest (below): their rows are usually all
    users, so the enumeration has no Guard-A (strict-shape) teams, and the
    clobber guard refuses to overwrite a manifest that holds team graph names
    when the new enumeration has no Guard-A rows — that list is the drop record
    the FalkorDB companion reads.

Usage (dry-run first, then execute):
  # 1) enumerate + write manifest (no writes)
  uv run python graph-scripts/2146_e2e_live_orphan_cleanup.py --phase enumerate
  # 2) review the manifest JSON + printed counts (incl. the Guard-D residue)
  # 3) dry-run the delete (prints SQL/counts, writes nothing)
  uv run python graph-scripts/2146_e2e_live_orphan_cleanup.py --phase all
  # 4) execute (users then control-plane rows; teams last)
  uv run python graph-scripts/2146_e2e_live_orphan_cleanup.py --phase all --execute
  # 5) drop FalkorDB graphs (separate store; needs FALKORDB_CLOUD_URI)
  FALKORDB_CLOUD_URI=... uv run python graph-scripts/2146_falkordb_graph_cleanup.py \\
      --manifest 2146-e2e-live-orphans.manifest.json --execute
  # 6) verify (expect 0 rows) — see runbook docs/runbook/2146-e2e-live-orphan-cleanup.md

Recurrence (the #2189 reconcile case — the rows are NEWER than the window, and
usually have NO teams: the live #5971 case is users=4 teams=0). Use a SEPARATE
--manifest: the default #2146 manifest (154 window teams) is the FalkorDB
companion's drop record for them — the executed red-window run's own record is
the committed graph-scripts/2146-orphan-manifest.json (222 graph entries). The
clobber guard refuses to overwrite a manifest
that holds team graph names when the new enumeration found no Guard-A
(strict-shape) teams — so a users-only recurrence, and a residue team promoted
via --include-residue, are refused rather than silently discarding the list.
Only a recurrence that mints Guard-A teams would overwrite it, which is why
using a separate --manifest is unconditional.
  uv run python graph-scripts/2146_e2e_live_orphan_cleanup.py \\
      --phase enumerate --all-e2e-live \\
      --manifest 2146-recurrence.manifest.json   # review the manifest's delete
                                                 # list AND the printed residue
  rm -f 2146-recurrence.manifest.json.dryrun     # ephemeral (see below)
  # dry-run to a SCRATCH manifest: a dry-run re-enumerates and writes its own
  # --manifest, and for a residue TEAM that write would leave prior_n>0 with
  # Guard-A=0, so the execute below would refuse. The scratch keeps the execute's
  # manifest team-empty.
  uv run python graph-scripts/2146_e2e_live_orphan_cleanup.py \\
      --phase all --all-e2e-live --include-residue \\
      --manifest 2146-recurrence.manifest.json.dryrun
  uv run python graph-scripts/2146_e2e_live_orphan_cleanup.py \\
      --phase all --all-e2e-live --include-residue \\
      --manifest 2146-recurrence.manifest.json --execute
  # then: rm 2146-recurrence.manifest.json{,.dryrun}
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"graph-scripts/2146_e2e_live_orphan_cleanup.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python graph-scripts/2146_e2e_live_orphan_cleanup.py`"
    )

import argparse
import csv
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime

# ── Guard constants (verified against live prod, 2026-09-02) ────────────────
EMAIL_RE = re.compile(r"^e2e-live-[0-9a-f]{8}@premise-labs\.dev$")
WINDOW_START = "2026-08-19T22:20:00Z"          # red-window start (#2140 monitor red)
WINDOW_END = "2026-09-03T00:00:00Z"            # exclusive ceiling past the #2144 fix
DEFAULT_PROJECT_REF = "ybetwichurajbfswfeqa"   # premise-labs prod project
AUDIT_OP = "e2e_live_orphan_purged"
AUDIT_ACTOR = "ops-runbook-2146"

# NOTE: __WINDOW__ is replaced literally (no .format) so the LIKE '%' and
# regex {8} stay untouched.
TEAM_SCOPE_SQL = """
    SELECT t.id, t.name, t.email, t.graph_name,
           t.created_at::text AS created_at,
           t.deleted_at::text AS deleted_at
    FROM public.organizations t
    WHERE t.email LIKE 'e2e-live-%@premise-labs.dev'
      AND t.email ~ '^e2e-live-[0-9a-f]{8}@premise-labs\.dev$'
      __WINDOW__
    ORDER BY t.created_at;
"""

USERS_SQL = """
    SELECT u.id::text AS id, u.email, u.created_at::text AS created_at
    FROM auth.users u
    WHERE u.email LIKE 'e2e-live-%@premise-labs.dev'
      AND u.email ~ '^e2e-live-[0-9a-f]{8}@premise-labs\.dev$'
      __WINDOW__
    ORDER BY u.created_at;
"""

# #5971: Guard D — the residue queries. Same namespace scope as the detector's
# LIKE, explicitly NEGATED against Guard A's shape, and ALL-TIME: a residue row is
# an anomaly, not a window artifact — the live example is off the guard shape as
# well as after WINDOW_END, so only a shape-negated, all-time enumeration can see
# it. `~`/`!~` are POSIX regex; the SQL keeps a literal backslash-dot (a
# doubled backslash in the Python source here, since a single one is the
# deprecated non-raw escape #3054 tracks).
RESIDUE_TEAMS_SQL = """
    SELECT t.id, t.name, t.email, t.graph_name,
           t.created_at::text AS created_at,
           t.deleted_at::text AS deleted_at
    FROM public.organizations t
    WHERE t.email LIKE 'e2e-live-%@premise-labs.dev'
      AND t.email !~ '^e2e-live-[0-9a-f]{8}@premise-labs\\.dev$'
    ORDER BY t.created_at;
"""

RESIDUE_USERS_SQL = """
    SELECT u.id::text AS id, u.email, u.created_at::text AS created_at
    FROM auth.users u
    WHERE u.email LIKE 'e2e-live-%@premise-labs.dev'
      AND u.email !~ '^e2e-live-[0-9a-f]{8}@premise-labs\\.dev$'
    ORDER BY u.created_at;
"""

CHILDREN_SQL = """
    SELECT 'api_keys' AS kind, count(*) AS n FROM public.api_keys
      WHERE org_id IN ({ids})
    UNION ALL
    SELECT 'org_memberships', count(*) FROM public.org_memberships
      WHERE org_id IN ({ids})
    UNION ALL
    SELECT 'invitations', count(*) FROM public.invitations
      WHERE org_id IN ({ids})
    UNION ALL
    SELECT 'abuse_events', count(*) FROM public.abuse_events
      WHERE org_id IN ({ids})
    UNION ALL
    SELECT 'analytics_events', count(*) FROM public.analytics_events
      WHERE org_id IN ({ids})
    UNION ALL
    SELECT 'audit_events', count(*) FROM public.audit_events
      WHERE org_id IN ({ids});
"""


class OpError(Exception):
    """Fatal, human-readable error."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ── SQL drivers ──────────────────────────────────────────────────────────────

def _mgmt_api_sql(project_ref: str, token: str, query: str) -> list[dict]:
    """Management API SQL endpoint (runs as postgres superuser on the project)."""
    url = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"
    req = urllib.request.Request(
        url,
        data=json.dumps({"query": query}).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise OpError(f"Management API SQL failed HTTP {e.code}: {body[:600]}") from None
    except Exception as e:
        raise OpError(f"Management API SQL failed: {e!r}") from None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        rows = payload.get("rows")
        if isinstance(rows, list):
            return rows
    raise OpError(f"Unexpected Management API response shape: {str(payload)[:300]}")


def _cli_sql(query: str) -> list[dict]:
    """Fallback driver: supabase db query --linked (needs linked project + CLI auth)."""
    proc = subprocess.run(
        ["supabase", "db", "query", "--linked", "-o", "json", query],
        capture_output=True, text=True, timeout=180,
    )
    if proc.returncode != 0:
        raise OpError(f"supabase db query failed: {proc.stderr[-600:]}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise OpError(f"supabase db query returned non-JSON: {proc.stdout[:300]}") from None
    if isinstance(data, dict) and isinstance(data.get("rows"), list):
        return data["rows"]
    if isinstance(data, list):
        return data
    raise OpError(f"Unexpected supabase db query output: {str(data)[:300]}")


def _make_sql_runner(args: argparse.Namespace):
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    via = args.via
    if via == "auto":
        if token:
            via = "api"
        else:
            proc = subprocess.run(
                ["supabase", "--version"], capture_output=True, text=True)
            via = "cli" if proc.returncode == 0 else "missing"
    if via == "api":
        if not token:
            raise OpError("--via api requires SUPABASE_ACCESS_TOKEN env var")
        return lambda q: _mgmt_api_sql(args.project_ref, token, q)
    if via == "cli":
        return _cli_sql
    raise OpError(
        "No SQL driver available: set SUPABASE_ACCESS_TOKEN (Management API) or "
        "install/link the supabase CLI (`supabase db query --linked`).")


def _run_sql(runner, query: str, label: str) -> list[dict]:
    try:
        return runner(query)
    except OpError:
        raise
    except Exception as e:  # pragma: no cover
        raise OpError(f"{label} failed: {e!r}") from None


def _qlist(ids: list[str]) -> str:
    return ", ".join("'" + i.replace("'", "''") + "'" for i in ids)


# ── GoTrue Admin API (user deletion — same mechanism as monitor teardown) ───

def _gotrue_delete_user(base_url: str, service_key: str, user_id: str) -> None:
    headers = {
        "Authorization": f"Bearer {service_key}",
        "apikey": service_key,
        "Accept": "application/json",
    }
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/auth/v1/admin/users/{user_id}",
        method="DELETE", headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=30):
            return
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # #2162 re-review ISSUE B: a partial mid-list failure followed by a
            # re-run hits already-deleted users -> 404. Treat 404 as
            # already-deleted so the loop continues (the runbook's idempotency
            # guarantee: re-running any phase is a no-op).
            print(f"[users] {user_id} already gone (404) — continuing")
            return
        raise OpError(
            f"GoTrue delete user {user_id} failed: HTTP {e.code} {e.reason}") from None


# ── Phases ───────────────────────────────────────────────────────────────────

def _report_residue(residue_teams: list[dict], residue_users: list[dict], included: bool) -> None:
    """Print Guard-D residue (#5971) — always, whether or not it is in the delete set.

    The point of the guard is that nothing is SILENT: before #5971 these rows were
    excluded by a SQL regex and the operator saw neither the row nor the exclusion,
    so a reconcile issue could never be closed.
    """
    if not residue_teams and not residue_users:
        return
    print(f"[residue] Guard-A residue — in the e2e-live namespace, OFF the guard shape "
          f"{EMAIL_RE.pattern}")
    for row in residue_teams:
        print(f"[residue]   team {row['id']}  {row['email']}  created {row['created_at']}")
    for row in residue_users:
        print(f"[residue]   user {row['id']}  {row['email']}  created {row['created_at']}")
    n = len(residue_teams) + len(residue_users)
    if included:
        print(f"[residue]   {n} row(s) INCLUDED via --include-residue — residue is "
              f"ALL-TIME, so the window does not apply to it")
    else:
        print(f"[residue]   {n} row(s) NOT in the delete set — pass --include-residue to "
              f"purge them (recorded in the manifest's `residue` key either way)")


def phase_enumerate(args: argparse.Namespace, runner) -> dict:
    window_clause = "" if args.all_e2e_live else (
        f"AND t.created_at >= '{args.window_start}' "
        f"AND t.created_at < '{args.window_end}'")
    teams = _run_sql(
        runner,
        TEAM_SCOPE_SQL.replace("__WINDOW__", window_clause),
        "enumerate teams",
    )
    users = _run_sql(
        runner,
        USERS_SQL.replace(
            "__WINDOW__",
            "" if args.all_e2e_live else (
                f"AND u.created_at >= '{args.window_start}' "
                f"AND u.created_at < '{args.window_end}'")),
        "enumerate users",
    )
    for row in teams:
        if not EMAIL_RE.match(row.get("email") or ""):
            raise OpError(
                f"GUARD FAIL: team {row.get('id')} email {row.get('email')!r} "
                "does not match e2e-live shape — aborting")

    # #5971 / Guard D — the residue the detector counts but Guard A excludes
    # (all-time: the window is a red-window artifact, and a residue row is newer
    # than it by construction). Fail CLOSED if a "residue" row actually matches
    # the guard shape: that would mean the negation and the guard disagree, i.e.
    # the exclusion logic itself is broken — not that the row is safe to skip.
    residue_teams = _run_sql(runner, RESIDUE_TEAMS_SQL, "enumerate residue teams")
    residue_users = _run_sql(runner, RESIDUE_USERS_SQL, "enumerate residue users")
    for row in (*residue_teams, *residue_users):
        email = row.get("email") or ""
        if EMAIL_RE.match(email):
            raise OpError(
                f"GUARD FAIL: residue row {row.get('id')} email {email!r} DOES match "
                f"{EMAIL_RE.pattern} — the residue query and Guard A disagree; aborting")
    _report_residue(residue_teams, residue_users, bool(args.include_residue))
    # #5971: the clobber guard's input is the GUARD-A enumeration, captured BEFORE
    # any promotion. Promoting a residue TEAM makes `teams` non-empty, and a guard
    # that read the promoted list would then overwrite a prior manifest holding
    # team graph names — silently discarding the drop record the FalkorDB
    # companion reads, which is the failure the guard exists to prevent. So
    # record the pre-promotion count for `main()` to test.
    guard_a_teams = len(teams)
    if args.include_residue:
        # Explicit, review-gated inclusion (the --all-e2e-live shape). The two sets
        # are disjoint by construction (strict shape vs. NOT strict shape), so no
        # dedupe is needed; residue rows carry the same columns as their scope
        # query (teams need id/graph_name for phase_db + the FalkorDB manifest).
        teams = teams + residue_teams
        users = users + residue_users

    ids = [t["id"] for t in teams]
    child_rows = _run_sql(runner, CHILDREN_SQL.format(ids=_qlist(ids)), "enumerate children") if ids else []
    counts = {r["kind"]: int(r["n"]) for r in child_rows} if child_rows else {}
    counts.update(teams=len(teams), users=len(users))
    manifest = {
        "generated_at": _now_iso(),
        "scope": {
            "all_e2e_live": bool(args.all_e2e_live),
            "include_residue": bool(args.include_residue),
            "window_start": None if args.all_e2e_live else args.window_start,
            "window_end": None if args.all_e2e_live else args.window_end,
            "email_regex": EMAIL_RE.pattern,
            # #5971: the Guard-A (pre-promotion) team count — what the manifest
            # would hold WITHOUT --include-residue. `main()`'s clobber guard
            # tests this, never the promoted `teams`, so a promoted residue team
            # cannot route around the protection.
            "guard_a_teams": guard_a_teams,
        },
        "counts": counts,
        # #5971: ALWAYS the residue as enumerated (never the delete-set view), so
        # the manifest is the durable record of what the guard excluded even on a
        # run that did not promote it.
        "residue": {"teams": residue_teams, "users": residue_users},
        "teams": teams,
        "users": users,
    }
    return manifest


def phase_users(args: argparse.Namespace, manifest: dict, runner) -> None:
    """Delete remaining e2e-live auth users (GoTrue Admin API default)."""
    users = manifest.get("users") or []
    if not users:
        print("[users] no e2e-live users to delete")
        return
    if not args.execute:
        print(f"[users] DRY-RUN — would delete {len(users)} auth user(s):")
        for u in users:
            print(f"        {u['id']}  {u['email']}  created {u['created_at']}")
        return
    if args.delete_users_via == "gotrue":
        base = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        if not base or not key:
            raise OpError(
                "user deletion via GoTrue needs SUPABASE_URL + "
                "SUPABASE_SERVICE_KEY env vars (or pass --delete-users-via sql)")
        for u in users:
            _gotrue_delete_user(base, key, u["id"])
            print(f"[users] deleted {u['id']} ({u['email']}) via GoTrue Admin API")
    else:
        # SQL as postgres superuser — cascades auth-internal FKs (identities,
        # sessions, mfa_factors, one_time_tokens, oauth_*, webauthn_* — all
        # ON DELETE CASCADE, verified on the live catalog 2026-09-02). These
        # synthetic accounts only ever had email identities + sessions.
        ids = _qlist([u["id"] for u in users])
        _run_sql(runner, f"DELETE FROM auth.users WHERE id IN ({ids});", "delete auth users")
        print(f"[users] deleted {len(users)} auth user(s) via SQL")


def phase_db(args: argparse.Namespace, manifest: dict, runner) -> None:
    """Delete control-plane rows for the manifest teams (children first, teams last)."""
    teams = manifest.get("teams") or []
    if not teams:
        print("[db] no teams in manifest — nothing to delete")
        return
    ids = _qlist([t["id"] for t in teams])
    steps = [
        ("api_keys", f"DELETE FROM public.api_keys WHERE org_id IN ({ids});"),
        ("org_memberships", f"DELETE FROM public.org_memberships WHERE org_id IN ({ids});"),
        ("invitations", f"DELETE FROM public.invitations WHERE org_id IN ({ids});"),
        ("abuse_events", f"DELETE FROM public.abuse_events WHERE org_id IN ({ids});"),
        # #2162 re-review ISSUE A: analytics_events is NOT deletable — migration
        # 0004 creates an analytics_events_immutable BEFORE UPDATE OR DELETE
        # FOR EACH STATEMENT trigger (not role-gated: fires on ANY delete
        # statement, even 0 rows), so a bare DELETE would abort phase_db after
        # steps 1-4 and strand the purge. It stays in CHILDREN_SQL as a
        # check-only child (the 09-02 inventory counted 0; the runbook Q3 sees
        # it). Rows, if any ever exist, need an explicit operator decision
        # (DISABLE TRIGGER around the delete), NOT script deletion.
    ]
    # #2146 review P1-1: the audit id carries a per-RUN uuid so a partial
    # failure (audit INSERT ok, organizations DELETE failed) can be re-run safely — a
    # deterministic id would collide on the append-only audit_events PK
    # (immutable trigger, migration 0002) and strand the cleanup permanently.
    run_id = uuid.uuid4().hex[:8]
    audit_values = ", ".join(
        "('2146-purge-{rid}-{tid}-{i}', '{tid2}', '{actor}', '{op}', 'team', '{tid3}', '{ts}')".format(
            rid=run_id, tid=t["id"][:16], i=i, tid2=t["id"],
            actor=AUDIT_ACTOR, op=AUDIT_OP, tid3=t["id"], ts=_now_iso())
        for i, t in enumerate(teams))
    steps.append((
        "audit_events (append trail)",
        f"INSERT INTO public.audit_events "
        f"(id, org_id, actor_user_id, operation, resource_type, resource_id, created_at) "
        f"VALUES {audit_values};",
    ))
    steps.append(("organizations (LAST — retry anchor)", f"DELETE FROM public.organizations WHERE id IN ({ids});"))

    print(f"[db] manifest teams: {len(teams)}  graph names: "
          f"{[t['graph_name'] for t in teams][:3]}{' …' if len(teams) > 3 else ''}")
    for label, sql in steps:
        print(f"[db] {'DRY-RUN ' if not args.execute else ''}{label}")
        if args.execute:
            _run_sql(runner, sql, f"db phase: {label}")
            print("      ok")
        elif args.verbose:
            print(f"      {sql[:300]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase", choices=["enumerate", "users", "db", "all"],
                    default="enumerate",
                    help="enumerate (write manifest only) | users (auth users) | "
                         "db (control-plane rows) | all (users+db). Default enumerate.")
    ap.add_argument("--execute", action="store_true",
                    help="REAL deletions (default is dry-run — nothing is written)")
    ap.add_argument("--all-e2e-live", action="store_true",
                    help="include the 68 pre-window e2e-live mints (default: window only) — "
                         "REQUIRED for a recurrence, whose rows are newer than the window")
    ap.add_argument("--include-residue", action="store_true",
                    help="also purge Guard-A residue (in the e2e-live namespace, off the guard "
                         "shape). Enumerated on --phase enumerate|all and printed on "
                         "every phase; pass this only after "
                         "reading that list (#5971)")
    ap.add_argument("--window-start", default=WINDOW_START)
    ap.add_argument("--window-end", default=WINDOW_END)
    ap.add_argument("--manifest", default="2146-e2e-live-orphans.manifest.json")
    ap.add_argument("--project-ref", default=DEFAULT_PROJECT_REF)
    ap.add_argument("--via", choices=["auto", "api", "cli"], default="auto")
    ap.add_argument("--delete-users-via", choices=["gotrue", "sql"], default="gotrue")
    ap.add_argument("--verbose", "-v", action="store_true")
    ap.add_argument("--dump-csv", metavar="PATH",
                    help="write full team rows to CSV before any delete (rollback/audit)")
    args = ap.parse_args()

    runner = _make_sql_runner(args)

    if args.phase in ("enumerate", "all"):
        manifest = phase_enumerate(args, runner)
        # #2146 review P2-1: refuse to clobber a non-empty manifest with no
        # Guard-A team rows. A post-success re-run enumerates no Guard-A teams; if
        # it overwrote the manifest in place, the FalkorDB companion would read an
        # empty list and silently skip all ~154 graphs with no row-level record
        # of what to drop. Delete the manifest manually to force a fresh empty
        # enumeration.
        #
        # #5971: a recurrence whose rows are all users (residue OR strict-shape;
        # the live case is `users=4 teams=0`) also enumerates 0 Guard-A teams, so
        # this refusal fires for it too —
        # deliberately. The test is against the PRE-promotion count
        # (`scope.guard_a_teams`), never the promoted `teams`: promoting a residue
        # team must not route around it. The prior `teams` list is the drop record
        # the FalkorDB companion reads (`2146_falkordb_graph_cleanup.py` reads
        # `manifest["teams"]` and returns 0 on an empty list), so the guard is
        # what keeps that list from being discarded silently. A recurrence
        # therefore enumerates to its OWN --manifest (the runbook §6.1 path): the
        # historical list survives, and the residue is still reachable.
        if os.path.exists(args.manifest):
            try:
                with open(args.manifest) as fh:
                    prior = json.load(fh)
                prior_n = len(prior.get("teams") or [])
            except (OSError, json.JSONDecodeError):
                prior_n = 0
            scope = manifest.get("scope") or {}
            guard_a_teams = scope.get("guard_a_teams")
            if guard_a_teams is None:  # a pre-#5971 manifest shape / fixture
                guard_a_teams = len(manifest.get("teams") or [])
            if prior_n > 0 and not guard_a_teams:
                print(f"[enumerate] REFUSING to overwrite {args.manifest}: it holds "
                      f"{prior_n} teams but the new enumeration found no Guard-A "
                      f"(strict-shape) teams (post-success re-run, or a recurrence "
                      f"whose rows are all users, such as #5971's)? Those teams are the "
                      f"FalkorDB companion's drop list; re-enumerate a recurrence to a "
                      f"separate --manifest (2146-recurrence.manifest.json — runbook "
                      f"§6.1), or remove this file once those graphs are already dropped.")
                return 2
        with open(args.manifest, "w") as fh:
            json.dump(manifest, fh, indent=2, default=str)
        print(f"[enumerate] wrote {args.manifest}")
        c = manifest["counts"]
        print(f"[enumerate] teams={c.get('teams', 0)} users={c.get('users', 0)} "
              f"api_keys={c.get('api_keys', 0)} memberships={c.get('org_memberships', 0)} "
              f"invitations={c.get('invitations', 0)} abuse_events={c.get('abuse_events', 0)} "
              f"analytics_events={c.get('analytics_events', 0)} "
              f"audit_events={c.get('audit_events', 0)}")
        res = manifest.get("residue") or {}
        included = bool(manifest["scope"]["include_residue"])
        print(f"[enumerate] residue (off guard shape, included={included}): "
              f"teams={len(res.get('teams') or [])} users={len(res.get('users') or [])}")
        print(f"[enumerate] graph names to drop (FalkorDB): {len(manifest['teams'])}")
        if args.dump_csv:
            with open(args.dump_csv, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=list(manifest["teams"][0].keys()) if manifest["teams"] else ["id"])
                w.writeheader()
                w.writerows(manifest["teams"])
            print(f"[enumerate] wrote {args.dump_csv}")
        if args.phase == "enumerate":
            if manifest["teams"]:
                print("[enumerate] REVIEW the manifest. Then: --phase all --execute "
                      "(and the FalkorDB helper for graphs).")
            return 0
    else:
        if not os.path.exists(args.manifest):
            raise OpError(f"manifest {args.manifest} missing — run --phase enumerate first")
        with open(args.manifest) as fh:
            manifest = json.load(fh)
        # #5971: `users`/`db` are the phases that ACTUALLY DELETE, and they are
        # manifest-driven (no re-enumeration) — so report the manifest's residue
        # here too. A run that removes only guard-shaped rows while staying quiet
        # about the residue it leaves behind is the same silent exclusion #5971
        # removed from the enumerate path.
        _res = manifest.get("residue") or {}
        _report_residue(_res.get("teams") or [], _res.get("users") or [],
                        bool((manifest.get("scope") or {}).get("include_residue")))

    if args.include_residue and args.phase not in ("enumerate", "all") \
            and not (manifest.get("scope") or {}).get("include_residue"):
        # The delete set is the MANIFEST's, and these phases do not re-enumerate
        # — so the flag cannot promote anything here. Never silently inert. Only
        # warn when it genuinely cannot act: if the manifest already recorded
        # include_residue=true, the residue IS in its delete set and the note
        # would contradict the `INCLUDED via --include-residue` line above it.
        print(f"[residue] NOTE: --include-residue has no effect on --phase "
              f"{args.phase} — the delete set is read from {args.manifest}. "
              f"Re-enumerate (--phase enumerate|all --include-residue) to promote "
              f"residue rows.")

    if args.phase in ("users", "all"):
        phase_users(args, manifest, runner)
    if args.phase in ("db", "all"):
        phase_db(args, manifest, runner)

    if not args.execute:
        print("\nDRY-RUN complete — no writes performed. Re-run with --execute to delete.")
        print("FalkorDB graphs are NOT touched by this script — run the companion\n"
              "  2146_falkordb_graph_cleanup.py --manifest "
              f"{args.manifest} --execute\n"
              "with FALKORDB_CLOUD_URI exported.")
    else:
        print("\nDone. Verify with the runbook queries (docs/runbook/2146-e2e-live-orphan-cleanup.md).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except OpError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
