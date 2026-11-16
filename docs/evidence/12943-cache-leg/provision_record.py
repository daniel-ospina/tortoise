"""Provision a THROWAWAY record Postgres for the #12943 measurements.

Applies the record's own migration list (read from
``record_projection.RECORD_MIGRATIONS``, never restated) to an empty container,
then gives the record's APPLICATION role ``LOGIN`` + a throwaway password.

Why the app role and not the owner: ``record_projection`` REFUSES a connection
that BYPASSES RLS (R6, epic #7879) — a superuser/owner connection would cross
tenants, so the live writer refuses it by design. That refusal is what a naive
harness measures instead of the cache leg, which is why it is provisioned here.

Usage (never against a shared or production database):

    RECORD_DSN=postgresql://record:record@127.0.0.1:33943/record \\
    uv run python docs/evidence/12943-cache-leg/provision_record.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

_PREAMBLE = """
DO $$ BEGIN
  CREATE ROLE anon NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE ROLE authenticated NOLOGIN;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
  CREATE ROLE service_role NOLOGIN BYPASSRLS;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
CREATE TABLE IF NOT EXISTS public.organizations (
    id text PRIMARY KEY,
    name text NOT NULL,
    graph_name text
);
CREATE TABLE IF NOT EXISTS public.graphs (
    id text PRIMARY KEY,
    org_id text NOT NULL REFERENCES public.organizations(id),
    name text NOT NULL,
    namespace text,
    UNIQUE (org_id, id)
);
"""

#: The record's application role — the ONE role the record write path may
#: connect as (R6). ``LOGIN`` and its password are an operator act by design
#: (`20261025000001_record_tenancy_role_and_guc.sql`), so they are set here and
#: nowhere in a migration.
APP_ROLE = "tortoise_record_app"
APP_PASSWORD = "app12943"


def main() -> int:
    import psycopg2                                   # noqa: PLC0415

    from tortoise import record_projection            # noqa: PLC0415

    dsn = os.environ["RECORD_DSN"]
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(_PREAMBLE)
        for name in record_projection.RECORD_MIGRATIONS:
            path = REPO_ROOT / "supabase" / "migrations" / name
            cur.execute(path.read_text(encoding="utf-8"))
        # The role is created NOLOGIN/NOBYPASSRLS by the tenancy migration.
        cur.execute(f"ALTER ROLE {APP_ROLE} LOGIN PASSWORD %s", (APP_PASSWORD,))
    conn.close()
    host = dsn.rsplit("@", 1)[1]
    print(f"provisioned {len(record_projection.RECORD_MIGRATIONS)} migrations")
    print(f"app DSN: postgresql://{APP_ROLE}:{APP_PASSWORD}@{host}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
