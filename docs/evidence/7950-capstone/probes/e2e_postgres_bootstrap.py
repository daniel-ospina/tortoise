#!/usr/bin/env python3
"""Stand up a throwaway Postgres carrying the RECORD migrations, for the
capstone end-to-end chain probe.

Creates only what the record migrations depend on:
  * the three Supabase roles the policies GRANT to (anon / authenticated /
    service_role);
  * `public.organizations(id, name, graph_name)` and
    `public.graphs(id, org_id, name, namespace)` — the tenancy substrate;
  * then applies the record migrations in filename order.

Run:  uv run --with psycopg2-binary python .../e2e_postgres_bootstrap.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[4]
MIG_DIR = ROOT / "supabase/migrations"
MIGRATIONS = [
    "20261012000001_record_event_stream.sql",
    "20261012000002_record_semantic.sql",
    "20261012000003_record_epistemic.sql",
    "20261012000004_record_procedural.sql",
    "20261014000001_record_subject_props_no_derived.sql",
]
DSN = "postgresql://postgres:cap@127.0.0.1:15499/capstone"

BOOTSTRAP = """
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
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT ALL ON TABLES TO anon, authenticated, service_role;
CREATE TABLE IF NOT EXISTS public.organizations (
  id text PRIMARY KEY, name text NOT NULL, graph_name text);
CREATE TABLE IF NOT EXISTS public.graphs (
  id text PRIMARY KEY, org_id text NOT NULL REFERENCES public.organizations(id),
  name text NOT NULL, namespace text);
INSERT INTO public.organizations (id, name, graph_name)
  VALUES ('cap7950-org', 'cap7950', 'cap7950')
  ON CONFLICT (id) DO NOTHING;
INSERT INTO public.graphs (id, org_id, name, namespace)
  VALUES ('cap7950-graph', 'cap7950-org', 'cap7950', 'cap7950')
  ON CONFLICT (id) DO NOTHING;
"""


def main() -> int:
    conn = psycopg2.connect(DSN)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(BOOTSTRAP)
    print("bootstrap: roles + organizations + graphs OK")
    for name in MIGRATIONS:
        sql = (MIG_DIR / name).read_text()
        try:
            cur.execute(sql)
        except Exception as exc:
            print(f"FAILED applying {name}: {exc}")
            return 1
        print(f"applied {name}")
    conn.close()
    print("record migrations applied OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
