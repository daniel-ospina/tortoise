#!/usr/bin/env python3
"""4220 — enumerate + delete blog E2E residue from public.blog_posts.

Issue: daniel-ospina/tortoise#4220. The `verify-blog` job in
``.github/workflows/deploy-pages.yml`` ran ``tests/e2e/test_blog.py`` against
PRODUCTION on every deploy. Two of its tests create rows and deliberately left
them behind:

  * ``test_agent_api_meta_length_contract`` → ``meta-contract-<n>``
  * ``test_publish_lifecycle_crawler_visibility`` → ``lifecycle-e2e-<seed>``

``meta-contract-<n>`` was derived from ``abs(hash(title)) % 100000`` — Python
string hashing is per-process randomised (PYTHONHASHSEED), so every deploy
minted a NEW slug and therefore a NEW row: the pile grew without bound. The
fix PR stops new residue (both tests now DELETE in a ``finally:``, the slugs are
deterministic, and the write tests are off the deploy path). This script removes
what already accumulated.

SAFETY MODEL
  * DRY-RUN by default. Any delete requires --execute.
  * Guard A — slug prefix: --prefix must be one of the known test prefixes
    (``meta-contract-``, ``lifecycle-e2e-``) or ``both``. An arbitrary prefix is
    refused, so a typo cannot reach editorial content.
  * Guard B — created_by: every statement is AND-ed with
    ``created_by = 'blog-e2e'`` (the E2E agent). This is the guard that protects
    human-authored rows: the owner's ``hello-tortoise-first-post`` draft is never
    touched, and no row is deletable unless the E2E agent created it.
  * Verification is part of the run: after the DELETE the script re-counts and
    FAILS (exit 1) if any row remains. A cleanup that claims success without
    checking is the #4220 defect class.
  * Idempotent: re-running a completed cleanup enumerates 0 rows.

REQUIRED ACCESS (operator)
  * ``SUPABASE_ACCESS_TOKEN`` (Management API SQL) — same access path as the
    #2146 cleanup; OR the ``supabase`` CLI linked to the project
    (``supabase db query --linked``).
  Neither is registered as a plain value in CI output; run this LOCALLY with your
  own credential. Unknown/absent credential → exit 2 (fail-closed), never a
  silent "nothing to do".

Usage (dry-run first, then execute):
  # 1) list what would be deleted (no writes)
  python3 graph-scripts/4220_blog_residue_cleanup.py --prefix both
  # 2) review the listed slugs/counts
  # 3) delete + verify 0 remain
  python3 graph-scripts/4220_blog_residue_cleanup.py --prefix both --execute

Exit codes: 0 = goal met (dry-run listed / execute left 0 rows); 1 = deletes ran
but rows remain (goal not met); 2 = refused or could-not-determine (missing
credential, unknown prefix, SQL/transport error).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

# premise-labs prod project (same project the #2146 cleanup used; blog_posts
# lives here). Override with --project-ref if the blog ever moves.
DEFAULT_PROJECT_REF = "ybetwichurajbfswfeqa"
# blog_agent_keys.agent_name for the E2E key (docs/epics/.../README.md §Ops).
DEFAULT_AGENT = "blog-e2e"
ALLOWED_PREFIXES = ("meta-contract-", "lifecycle-e2e-")
BOTH = "both"


class OpError(Exception):
    """Refused, or could-not-determine. Maps to exit 2 (fail-closed)."""


def _sql_lit(value: str) -> str:
    """Single-quoted SQL literal (values here are internal constants)."""
    return "'" + value.replace("'", "''") + "'"


def resolve_prefixes(prefix: str) -> tuple[str, ...]:
    """Guard A — only the known test prefixes, or `both`.

    Refusing an arbitrary prefix is the point: this script must never be a
    general-purpose "delete posts matching X" tool against production.
    """
    if prefix == BOTH:
        return ALLOWED_PREFIXES
    if prefix not in ALLOWED_PREFIXES:
        raise OpError(
            f"prefix {prefix!r} refused — allowed: {', '.join(ALLOWED_PREFIXES)} "
            f"or {BOTH!r}. This script only removes E2E residue."
        )
    return (prefix,)


def where_clause(prefixes: tuple[str, ...], agent: str = DEFAULT_AGENT) -> str:
    """Guard A (prefix) AND Guard B (created_by) — the two are inseparable."""
    likes = " OR ".join(f"slug LIKE {_sql_lit(p + '%')}" for p in prefixes)
    return f"(({likes}) AND created_by = {_sql_lit(agent)})"


def enumerate_sql(prefixes: tuple[str, ...], agent: str = DEFAULT_AGENT) -> str:
    return (
        "SELECT slug, status, created_by, created_at FROM public.blog_posts "
        f"WHERE {where_clause(prefixes, agent)} ORDER BY created_at"
    )


def count_sql(prefixes: tuple[str, ...], agent: str = DEFAULT_AGENT) -> str:
    return (
        "SELECT count(*)::int AS remaining FROM public.blog_posts "
        f"WHERE {where_clause(prefixes, agent)}"
    )


def delete_sql(prefixes: tuple[str, ...], agent: str = DEFAULT_AGENT) -> str:
    return (
        "DELETE FROM public.blog_posts "
        f"WHERE {where_clause(prefixes, agent)} RETURNING slug"
    )


# ── SQL drivers (same two access paths as the #2146 cleanup) ────────────────

def _mgmt_api_sql(project_ref: str, token: str, query: str) -> list[dict]:
    """Management API SQL endpoint (runs as postgres superuser on the project)."""
    url = f"https://api.supabase.com/v1/projects/{project_ref}/database/query"
    req = urllib.request.Request(
        url,
        data=json.dumps({"query": query}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise OpError(f"Management API SQL failed HTTP {e.code}: {body[:600]}") from None
    except Exception as e:  # transport/parse
        raise OpError(f"Management API SQL failed: {e!r}") from None
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("rows"), list):
        return payload["rows"]
    raise OpError(f"Unexpected Management API response shape: {str(payload)[:300]}")


def _cli_sql(query: str) -> list[dict]:
    """Fallback: ``supabase db query --linked`` (needs a linked project + CLI auth)."""
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


def resolve_via(args: argparse.Namespace) -> tuple[str, str]:
    """Resolve --via and enforce the credential gate.

    Always called by main() — even when a runner is injected — so the gate is a
    property of the RUN: an injected runner (the test seam) must not be able to
    walk a credential-less run past the check.
    """
    token = os.environ.get("SUPABASE_ACCESS_TOKEN", "").strip()
    via = args.via
    if via == "auto":
        via = "api" if token else "cli"
    if via == "api" and not token:
        raise OpError("--via api requires the SUPABASE_ACCESS_TOKEN env var")
    return via, token


def make_runner(args: argparse.Namespace, via: str, token: str):
    if via == "api":
        return lambda q: _mgmt_api_sql(args.project_ref, token, q)
    if via == "cli":
        return _cli_sql
    raise OpError(f"unknown --via {via!r}")


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Enumerate + delete blog E2E residue in public.blog_posts (#4220).",
    )
    p.add_argument("--prefix", required=True,
                   help=f"{', '.join(ALLOWED_PREFIXES)} or {BOTH}")
    p.add_argument("--execute", action="store_true",
                   help="actually DELETE; without it the run is a dry-run")
    p.add_argument("--agent", default=DEFAULT_AGENT, choices=(DEFAULT_AGENT,),
                   help=f"created_by guard — the only accepted value is {DEFAULT_AGENT} "
                        "(the E2E agent); any other value is rejected so the guard "
                        "cannot be pointed at human-authored rows")
    p.add_argument("--project-ref", default=DEFAULT_PROJECT_REF)
    p.add_argument("--via", choices=("auto", "api", "cli"), default="auto")
    return p.parse_args(argv)


def main(argv: list[str] | None = None, runner=None) -> int:
    """Entry point. `runner` is the test seam: a callable SQL -> list[dict]."""
    args = _parse(argv)
    try:
        prefixes = resolve_prefixes(args.prefix)
        via, token = resolve_via(args)
        run_sql = runner if runner is not None else make_runner(args, via, token)
        where = where_clause(prefixes, args.agent)

        rows = run_sql(enumerate_sql(prefixes, args.agent))
        print(f"scope: {where}")
        if not rows:
            print("nothing to clean — 0 rows match.")
            return 0

        print(f"{len(rows)} row(s) match:")
        for row in rows:
            print(f"  - {row.get('slug')}  [{row.get('status')}]  "
                  f"{row.get('created_by')}  {row.get('created_at')}")

        if not args.execute:
            print("\n[DRY RUN] nothing written. To delete, re-run with --execute.")
            print(f"[DRY RUN] would run: {delete_sql(prefixes, args.agent)}")
            return 0

        deleted = run_sql(delete_sql(prefixes, args.agent))
        print(f"\ndeleted {len(deleted)} row(s).")

        remaining_rows = run_sql(count_sql(prefixes, args.agent))
        remaining = int(remaining_rows[0]["remaining"]) if remaining_rows else 0
        if remaining:
            print(f"ERROR: {remaining} row(s) still match after the delete.",
                  file=sys.stderr)
            return 1
        print(f"verified: 0 rows matching {args.prefix!r} remain.")
        return 0
    except OpError as e:
        print(f"refused/failed: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # unexpected — never report success
        print(f"unexpected failure: {e!r}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
