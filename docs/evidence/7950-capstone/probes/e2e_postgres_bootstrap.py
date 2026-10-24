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
    # The erasure RPC (#7968): a FUNCTION, so this Postgres gains a function and
    # no column. Applied anyway — the list is pinned to
    # `record_projection.RECORD_MIGRATIONS` in EQUALITY (#9550).
    "20261012000005_record_org_purge_rpc.sql",
    "20261014000001_record_subject_props_no_derived.sql",
    # FORWARD `ALTER TABLE public.point ADD COLUMN` (#2782's money value). The
    # probe drives the SHIPPED projection, whose schema read comes from
    # `record_projection.RECORD_MIGRATIONS` on DISK — omit a forward migration
    # here and this Postgres is one migration behind the projection that writes
    # to it (`UndefinedColumn` on the point INSERT). `tests/
    # test_7950_capstone_probe.py` pins this list to `RECORD_MIGRATIONS`.
    "20261018000001_record_point_money_value_2782.sql",
    # FORWARD `ALTER TABLE public.session ALTER COLUMN capture_redactions TYPE
    # integer` (#9264). Same reason as the entry above: the probe drives the
    # SHIPPED projection, so a schema still declaring `jsonb` keeps refusing the
    # producer's integer count with `DatatypeMismatch` here too.
    "20261022000001_record_session_capture_redactions_integer.sql",
    # FORWARD `CREATE TABLE public.session_narrative` (§9.1 / R7, #9555). The
    # shipped projection's `SessionRecorded` replay now emits an INSERT into it,
    # so a list without this migration stands up a database that raises
    # `UndefinedTable` the moment the probe runs the shipped code.
    "20261023000001_record_session_narrative.sql",
    # #9555/R7's carrier migration (`COMMENT ON` only). Applied so this list stays
    # EQUAL to `record_projection.RECORD_MIGRATIONS` (#9550) — the probe stands up
    # the SHIPPED schema, comments included.
    "20261024000001_record_session_narrative_carrier.sql",
    # The TENANCY POSTURE (#9554, epic #7879): the app role, its grants (and
    # `relation_id_seq`'s USAGE), and the `*_app_tenant` policies — one per record
    # table, ten since #9555's `session_narrative`. It adds
    # no column and is applied anyway — the list is pinned to
    # `RECORD_MIGRATIONS` in EQUALITY, and a probe whose Postgres lacks the
    # policies is not the Postgres the shipped read runs against.
    "20261025000001_record_tenancy_role_and_guc.sql",
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
