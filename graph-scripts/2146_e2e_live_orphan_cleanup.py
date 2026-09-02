#!/usr/bin/env python3
"""2146 — enumerate + clean e2e-live orphan teams (Supabase control plane).

Issue: daniel-ospina/tortoise#2146 (ops/prod-cleanup). The welcome-e2e-monitor's
live signup smoke was red for ~14 days (2026-08-19T22:20 → 2026-09-02): the
/welcome route stub was dead after the #1566 cross-site redirect change, so the
REAL app root loaded every run and ran #1566 welcome-mode provisioning under
emails matching `e2e-live-<8hex>@premise-labs.dev` — minting a prod team +
api_keys row + FalkorDB graph per run. Monitor teardown deleted only the auth
user (FK cascade removes team_memberships); the minted teams/api_keys rows and
FalkorDB graphs were never cleaned (fix PR #2144 prevents NEW mints).

Mint path (verified in repo, 2026-09-02):
  dashboard welcome-mode (website/apps/dashboard/src/main.jsx provisionInApp)
  → Supabase edge fn `tenant-provision` (supabase/functions/tenant-provision/
    index.ts) → `provision_team` SECURITY DEFINER RPC (migration 0010) which
    writes teams + team_memberships + api_keys atomically, then FastAPI
    `/internal/demo` seeds the FalkorDB graph `team_{team_id}` where
    team_id = sha256(user_id).hexdigest()[:26] (deterministic per user).

This script cleans the SUPABASE side (auth users + teams + api_keys +
team_memberships + invitations + abuse_events). FalkorDB graphs are dropped by
the companion script 2146_falkordb_graph_cleanup.py (separate store, needs
FALKORDB_CLOUD_URI).

SAFETY MODEL
  * DRY-RUN by default. Every destructive phase requires --execute.
  * Guard A — email shape: only rows matching
      ^e2e-live-[0-9a-f]{8}@premise-labs\.dev$
    are ever touched (all 222 live rows matched on 2026-09-02).
  * Guard B — window: default scope is teams created in the red window
    2026-08-19T22:20:00Z → 2026-09-03T00:00:00Z (154 rows). Pass --all-e2e-live
    to include the 68 pre-window monitor mints (same mint path — review first).
  * Idempotent: every delete is enumerated up-front (manifest) and re-runs
    after a successful cleanup find nothing.
  * The teams row is deleted LAST (children first) — the product's retry-anchor
    ordering (purge_team_control_plane, tortoise/supabase_control.py).
  * An append-only audit_events row is written per purged team BEFORE the teams
    delete (audit_events has no FK to teams — the delete trail survives).

DELETE-ORDER RATIONALE (mirrors tortoise purge semantics; live FK catalog):
  1. auth.users (remaining e2e-live accounts) via GoTrue Admin API — FK
     `user_teams_user_id_fkey` (team_memberships.user_id → auth.users) ON
     DELETE CASCADE removes their memberships; auth-internal tables cascade.
  2. team_memberships WHERE team_id IN (...) — team_id has NO FK (explicit
     delete required; also covers any row whose user is already gone).
  3. api_keys WHERE team_id IN (...) — has FK ON DELETE CASCADE, deleted
     explicitly for an auditable, non-cascade-dependent sequence.
  4. invitations WHERE team_id IN (...) — FK CASCADE, explicit anyway.
  5. abuse_events WHERE team_id IN (...) — FK CASCADE (0015 key_create rows
     minted by provision_team), explicit anyway.
  6. audit_events INSERT per team (operation=e2e_live_orphan_purged).
  7. teams WHERE id IN (...) LAST — children gone; row = retry anchor.
  FalkorDB graphs: companion script, run against the same manifest (may run
  before or after; a graph left behind is a benign orphan with no DB row).

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

Usage (dry-run first, then execute):
  # 1) enumerate + write manifest (no writes)
  python3 graph-scripts/2146_e2e_live_orphan_cleanup.py --phase enumerate
  # 2) review the manifest JSON + printed counts
  # 3) dry-run the delete (prints SQL/counts, writes nothing)
  python3 graph-scripts/2146_e2e_live_orphan_cleanup.py --phase all
  # 4) execute (users then control-plane rows; teams last)
  python3 graph-scripts/2146_e2e_live_orphan_cleanup.py --phase all --execute
  # 5) drop FalkorDB graphs (separate store; needs FALKORDB_CLOUD_URI)
  FALKORDB_CLOUD_URI=... python3 graph-scripts/2146_falkordb_graph_cleanup.py \
      --manifest 2146-e2e-live-orphans.manifest.json --execute
  # 6) verify (expect 0 rows) — see runbook docs/runbook/2146-e2e-live-orphan-cleanup.md
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

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
    FROM public.teams t
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

CHILDREN_SQL = """
    SELECT 'api_keys' AS kind, count(*) AS n FROM public.api_keys
      WHERE team_id IN ({ids})
    UNION ALL
    SELECT 'team_memberships', count(*) FROM public.team_memberships
      WHERE team_id IN ({ids})
    UNION ALL
    SELECT 'invitations', count(*) FROM public.invitations
      WHERE team_id IN ({ids})
    UNION ALL
    SELECT 'abuse_events', count(*) FROM public.abuse_events
      WHERE team_id IN ({ids})
    UNION ALL
    SELECT 'audit_events', count(*) FROM public.audit_events
      WHERE team_id IN ({ids});
"""


class OpError(Exception):
    """Fatal, human-readable error."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    except json.JSONDecodeError as e:
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
        raise OpError(
            f"GoTrue delete user {user_id} failed: HTTP {e.code} {e.reason}") from None


# ── Phases ───────────────────────────────────────────────────────────────────

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
    ids = [t["id"] for t in teams]
    child_rows = _run_sql(runner, CHILDREN_SQL.format(ids=_qlist(ids)), "enumerate children") if ids else []
    counts = {r["kind"]: int(r["n"]) for r in child_rows} if child_rows else {}
    counts.update(teams=len(teams), users=len(users))
    manifest = {
        "generated_at": _now_iso(),
        "scope": {
            "all_e2e_live": bool(args.all_e2e_live),
            "window_start": None if args.all_e2e_live else args.window_start,
            "window_end": None if args.all_e2e_live else args.window_end,
            "email_regex": EMAIL_RE.pattern,
        },
        "counts": counts,
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
        ("api_keys", f"DELETE FROM public.api_keys WHERE team_id IN ({ids});"),
        ("team_memberships", f"DELETE FROM public.team_memberships WHERE team_id IN ({ids});"),
        ("invitations", f"DELETE FROM public.invitations WHERE team_id IN ({ids});"),
        ("abuse_events", f"DELETE FROM public.abuse_events WHERE team_id IN ({ids});"),
    ]
    audit_values = ", ".join(
        "('2146-purge-%s-%d', '%s', '%s', '%s', 'team', '%s', '%s')" % (
            t["id"][:16], i, t["id"], AUDIT_ACTOR, AUDIT_OP, t["id"], _now_iso())
        for i, t in enumerate(teams))
    steps.append((
        "audit_events (append trail)",
        f"INSERT INTO public.audit_events "
        f"(id, team_id, actor_user_id, operation, resource_type, resource_id, created_at) "
        f"VALUES {audit_values};",
    ))
    steps.append(("teams (LAST — retry anchor)", f"DELETE FROM public.teams WHERE id IN ({ids});"))

    print(f"[db] manifest teams: {len(teams)}  graph names: "
          f"{[t['graph_name'] for t in teams][:3]}{' …' if len(teams) > 3 else ''}")
    for label, sql in steps:
        print(f"[db] {'DRY-RUN ' if not args.execute else ''}{label}")
        if args.execute:
            _run_sql(runner, sql, f"db phase: {label}")
            print(f"      ok")
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
                    help="include the 68 pre-window e2e-live mints (default: window only)")
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
        with open(args.manifest, "w") as fh:
            json.dump(manifest, fh, indent=2, default=str)
        print(f"[enumerate] wrote {args.manifest}")
        c = manifest["counts"]
        print(f"[enumerate] teams={c.get('teams', 0)} users={c.get('users', 0)} "
              f"api_keys={c.get('api_keys', 0)} memberships={c.get('team_memberships', 0)} "
              f"invitations={c.get('invitations', 0)} abuse_events={c.get('abuse_events', 0)} "
              f"audit_events={c.get('audit_events', 0)}")
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
