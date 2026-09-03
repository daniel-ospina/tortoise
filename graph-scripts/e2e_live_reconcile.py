#!/usr/bin/env python3
"""e2e-live reconciliation — weekly READ-ONLY detector for live-test prod bleeds.

Issue: daniel-ospina/tortoise#2189 (ops: scheduled e2e-live orphan
reconciliation). The welcome-e2e-monitor's live signup smoke mints real prod
rows (teams + api_keys + abuse_events under emails matching
`e2e-live-<8hex>@premise-labs.dev`, occasionally a surviving auth user) on a
red run. The #2140 incident — ~200 real signup runs over ~14 days minting
222 orphan teams/keys + 12 auth users + 222 FalkorDB graphs before a manual CI
audit found them — is the gap this detector closes. #2144 stops the *route
drift* that caused the bleed (the monitor now stubs the whole app origin); this
script verifies the *output* stays clean. The prod DB was fully cleaned by
#2146 on 2026-09-03; baseline expectation is 0 e2e-live-* rows.

READ-ONLY by construction: it runs the runbook's Q1/Q2/Q3 count queries
(docs/runbook/2146-e2e-live-orphan-cleanup.md) as SELECT-only Management API
calls and never writes or deletes. Remediation stays manual + operator-gated
(graph-scripts/2146_e2e_live_orphan_cleanup.py, dry-run by default, per the
runbook's guards). FalkorDB graph reconciliation is deliberately out of scope
(tier 2 of #2189).

Cadence: weekly (Mon 03:00 UTC) via .github/workflows/e2e-live-reconcile.yml
(schedule + workflow_dispatch only — never a merge gate). A recurrence at the
monitor's ~16 rows/day rate (#2140's 222 in ~14 days) is caught within a week
of onset at ~1/10th the #2140 scale (~100 rows) instead of weeks-unnoticed.
On detection the workflow auto-files an issue with the counts (#2144's
permissions/curl pattern); this script only computes them. Known behavior:
rc=1/rc=2 auto-file a fresh issue per failing run with no dedup against open
issues (matches the welcome-e2e-monitor precedent) — a stale-issue cleanup is
out of scope for #2189.

SAFETY MODEL — fail-closed; a dead check must never look green (#2140's
"a deaf monitor is not a detection layer"):
  * SUPABASE_ACCESS_TOKEN is REQUIRED (Management API driver; gh secret name on
    daniel-ospina/tortoise). Missing token = exit 2 with a clear message.
  * Any HTTP/transport/parse error = exit 2 with the error on stderr. No
    supabase-CLI fallback driver here: the detector needs one deterministic
    read path (the 2146 cleanup script offers the CLI fallback for operator
    convenience; this job runs in CI where the token is the wired secret).
  * Scope = ALL rows with email LIKE 'e2e-live-%@premise-labs.dev', all-time.
    The #2146 historical red-window (2026-08-19 -> 2026-09-03) is meaningless
    for detection — a NEW bleed is by definition recent, so any row in the
    test namespace is a signal. Rows deviating from the strict guard shape
    `^e2e-live-[0-9a-f]{8}@premise-labs.dev$` still fail the check (they are
    inside the test namespace) and are surfaced as shape_clean=false in the
    --json output — the 2146 cleanup script needs the strict shape, so an
    operator reviews before running it.
  * Counts ABOVE --threshold (default 0) = exit 1 with the offenders listed
    (emails truncated). Post-#2146 baseline (0 rows) = green.

Exit codes: 0 = clean (every count <= threshold) | 1 = bleed detected
(offenders listed) | 2 = could-not-determine (guard/transport failure).

Usage:
  # weekly CI (workflow): token from the secret; --json prints one JSON line to
  # stdout (human output moves to stderr) for the auto-file step's payload.
  SUPABASE_ACCESS_TOKEN=... python3 graph-scripts/e2e_live_reconcile.py --json
  # operator / fault-injection runs: human output; 0-tolerant threshold opt-in
  SUPABASE_ACCESS_TOKEN=... python3 graph-scripts/e2e_live_reconcile.py
  SUPABASE_ACCESS_TOKEN=... python3 graph-scripts/e2e_live_reconcile.py --threshold 2
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime

# ── Guard constants (verified against live prod 2026-09-02 by #2146) ────────
EMAIL_RE = re.compile(r"^e2e-live-[0-9a-f]{8}@premise-labs\.dev$")
DEFAULT_PROJECT_REF = "ybetwichurajbfswfeqa"  # premise-labs prod (same ref #2146 targets)
# Canonical output order (the workflow issue body + summary line rely on it).
COUNT_KINDS = (
    "teams",
    "users",
    "api_keys",
    "memberships",
    "invitations",
    "abuse",
    "analytics",
    "audit",
)

# NOTE: runbook Q1/Q2 verbatim scope (email LIKE + the e2e-live- test
# namespace); Q1 selects enough to count + report offender emails.
TEAMS_SQL = """
    SELECT t.id, t.name, t.email, t.created_at::text AS created_at
    FROM public.teams t
    WHERE t.email LIKE 'e2e-live-%@premise-labs.dev'
    ORDER BY t.created_at;
"""

USERS_SQL = """
    SELECT u.id::text AS id, u.email, u.created_at::text AS created_at
    FROM auth.users u
    WHERE u.email LIKE 'e2e-live-%@premise-labs.dev'
    ORDER BY u.created_at;
"""

# NOTE: runbook Q3 (children per orphan team), with short kind aliases matching
# COUNT_KINDS. analytics_events/audit_events are check-only children (append-only
# via their immutability triggers — they can never be script-deleted, #2146/#2162);
# they are counted here as a drift signal, not a cleanup target.
CHILDREN_SQL = """
    SELECT 'api_keys' AS kind, count(*) AS n FROM public.api_keys
      WHERE team_id IN ({ids})
    UNION ALL SELECT 'memberships', count(*) FROM public.team_memberships
      WHERE team_id IN ({ids})
    UNION ALL SELECT 'invitations', count(*) FROM public.invitations
      WHERE team_id IN ({ids})
    UNION ALL SELECT 'abuse', count(*) FROM public.abuse_events
      WHERE team_id IN ({ids})
    UNION ALL SELECT 'analytics', count(*) FROM public.analytics_events
      WHERE team_id IN ({ids})
    UNION ALL SELECT 'audit', count(*) FROM public.audit_events
      WHERE team_id IN ({ids});
"""


class ReconcileError(Exception):
    """Fatal, human-readable error — exit 2 (fail-closed, never a green run)."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ── SQL driver (Management API; pattern from 2146_e2e_live_orphan_cleanup.py) ─

def _mgmt_api_sql(project_ref: str, token: str, query: str) -> list[dict]:
    """SELECT via the Management API SQL endpoint (runs as postgres superuser).

    Read-only by callers — this detector only ever sends SELECT statements.
    """
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
        raise ReconcileError(f"Management API SQL failed HTTP {e.code}: {body[:600]}") from None
    except Exception as e:
        raise ReconcileError(f"Management API SQL failed: {e!r}") from None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return payload["rows"]
    raise ReconcileError(f"Unexpected Management API response shape: {str(payload)[:300]}")


def _run(run_sql, query: str, label: str) -> list[dict]:
    """Run one query, labeling failures so the stderr names the failed step."""
    try:
        return run_sql(query)
    except ReconcileError as e:
        raise ReconcileError(f"{label}: {e}") from None


def _qlist(ids: list[str]) -> str:
    return ", ".join("'" + i.replace("'", "''") + "'" for i in ids)


def _short_email(email: str, limit: int = 28) -> str:
    """Truncate an offender email for logs/issue bodies (they are synthetic)."""
    return email if len(email) <= limit else email[: limit - 1] + "…"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ── Counting (runbook Q1-Q3, SELECT-only) ───────────────────────────────────

def _counts(run_sql) -> tuple[dict[str, int], dict[str, list[str]], bool]:
    """Return (counts by COUNT_KINDS short key, offender emails by kind, shape_clean)."""
    teams = _run(run_sql, TEAMS_SQL, "teams count (Q1)")
    users = _run(run_sql, USERS_SQL, "auth.users count (Q2)")
    counts: dict[str, int] = {
        "teams": len(teams),
        "users": len(users),
        "api_keys": 0,
        "memberships": 0,
        "invitations": 0,
        "abuse": 0,
        "analytics": 0,
        "audit": 0,
    }
    if teams:
        ids = [t["id"] for t in teams]
        child_rows = _run(run_sql, CHILDREN_SQL.format(ids=_qlist(ids)), "children counts (Q3)")
        for row in child_rows:
            counts[row["kind"]] = int(row["n"])
    emails = {
        "teams": [str(t.get("email") or "") for t in teams],
        "users": [str(u.get("email") or "") for u in users],
    }
    shape_clean = all(
        EMAIL_RE.match(email) for kind_emails in emails.values() for email in kind_emails
    )
    return counts, emails, shape_clean


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--project-ref",
        default=DEFAULT_PROJECT_REF,
        help=f"Supabase project ref (default: {DEFAULT_PROJECT_REF} — the premise-labs prod ref #2146 targets)",
    )
    ap.add_argument(
        "--threshold",
        type=int,
        default=0,
        help="FAIL when any count is ABOVE this (default 0). 0-tolerant runs pass e.g. --threshold 2.",
    )
    ap.add_argument(
        "--json",
        action="store_true",
        help="print a single-line JSON result to stdout (human output moves to stderr)",
    )
    args = ap.parse_args()
    if args.threshold < 0:
        raise ReconcileError(f"--threshold must be >= 0 (got {args.threshold})")

    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    if not token:
        raise ReconcileError(
            "SUPABASE_ACCESS_TOKEN is not set — required for the Management API SQL driver "
            "(gh secret name on daniel-ospina/tortoise). #2189 fails closed: a check without "
            "creds must never look green."
        )

    def run_sql(query: str) -> list[dict]:
        return _mgmt_api_sql(args.project_ref, token, query)

    checked_at = _now_iso()
    counts, emails, shape_clean = _counts(run_sql)

    over_counts = {k: counts[k] for k in COUNT_KINDS if counts[k] > args.threshold}
    over_emails = {k: [_short_email(e) for e in emails.get(k, [])] for k in over_counts}

    def log(msg: str) -> None:
        # --json keeps stdout machine-only (single JSON doc) — diagnostics to stderr.
        (sys.stderr if args.json else sys.stdout).write(msg + "\n")

    log(f"[reconcile] project={args.project_ref} checked_at={checked_at} threshold={args.threshold}")
    summary = " ".join(f"{k}={counts[k]}" for k in COUNT_KINDS)
    log(f"[reconcile] {summary}")
    if not over_counts:
        log(f"[reconcile] PASS — every count <= threshold {args.threshold}")
        rc = 0
    else:
        log(
            f"[reconcile] FAIL — {_plural(len(over_counts), 'count')} above threshold "
            f"{args.threshold} (max {_plural(max(counts.values()), 'orphaned row')})"
        )
        for kind in over_counts:
            tail = f": {', '.join(over_emails[kind])}" if over_emails[kind] else ""
            log(f"[reconcile]   {kind}={counts[kind]}{tail}")
        if not shape_clean:
            log(
                "[reconcile]   NOTE: a row deviates from the 2146 guard email shape "
                "^e2e-live-[0-9a-f]{8}@premise-labs.dev$ — review before running the cleanup"
            )
        rc = 1

    if args.json:
        doc = {
            "ok": rc == 0,
            "checked_at": checked_at,
            "project_ref": args.project_ref,
            "threshold": args.threshold,
            "counts": counts,
            "max": max(counts.values()),
            "shape_clean": shape_clean,
            "offenders": {
                k: {"count": c, "emails": over_emails.get(k, [])} for k, c in over_counts.items()
            },
        }
        print(json.dumps(doc))  # the ONLY stdout output in --json mode
    return rc


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ReconcileError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:  # #2189 review P1: any escape must be rc=2
        # (could-not-determine), NEVER rc=1 (bleed semantics) — an uncaught
        # parse/shape error (row missing a key, UNION kind renamed, etc.) is
        # a detector failure, not evidence of orphans.
        import traceback
        traceback.print_exc(file=sys.stderr)
        print(f"ERROR: reconcile crashed unexpectedly: {e!r}", file=sys.stderr)
        sys.exit(2)
