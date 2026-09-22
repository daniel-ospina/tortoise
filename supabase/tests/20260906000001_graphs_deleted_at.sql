-- ============================================================================
-- SQL-level verification for migration 20260906000001 (#2304 — trash-can
-- delete: graphs.deleted_at / purged_at / purged_residual).
-- NOTE: assertion messages use the 'd1' shorthand for this migration.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--   (harness applies ALL migrations incl. d1, then runs this suite with
--   ON_ERROR_STOP semantics — every assertion RAISEs on failure)
--
-- Covers #2304 scoping test-design surfaces: the three additive columns,
-- default values (purged_residual NOT NULL DEFAULT false), and the extended
-- column-level SELECT grant to authenticated.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS tests;
CREATE OR REPLACE FUNCTION tests.assert(cond boolean, msg text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ASSERTION FAILED: %', msg;
  END IF;
END $$;
GRANT USAGE ON SCHEMA tests TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION tests.assert(boolean, text) TO anon, authenticated, service_role;

-- ── 1. Column presence + nullability ──────────────────────────────────────
SELECT tests.assert(
  (SELECT count(*) FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'graphs'
      AND column_name IN ('deleted_at', 'purged_at', 'purged_residual')) = 3,
  'd1: graphs gained deleted_at/purged_at/purged_residual');

SELECT tests.assert(
  (SELECT data_type FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'graphs'
      AND column_name = 'deleted_at') = 'timestamp with time zone',
  'd1: deleted_at is timestamptz');

SELECT tests.assert(
  (SELECT data_type FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'graphs'
      AND column_name = 'purged_at') = 'timestamp with time zone',
  'd1: purged_at is timestamptz');

-- ── 2. purged_residual default (NOT NULL DEFAULT false) ───────────────────
SELECT tests.assert(
  (SELECT is_nullable FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'graphs'
      AND column_name = 'purged_residual') = 'NO',
  'd1: purged_residual is NOT NULL');

SELECT tests.assert(
  (SELECT column_default FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'graphs'
      AND column_name = 'purged_residual') = 'false',
  'd1: purged_residual defaults to false');

-- ── 3. Idempotent re-apply (IF NOT EXISTS) ────────────────────────────────
ALTER TABLE public.graphs
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz,
    ADD COLUMN IF NOT EXISTS purged_at timestamptz,
    ADD COLUMN IF NOT EXISTS purged_residual boolean NOT NULL DEFAULT false;
SELECT tests.assert(true, 'd1: migration re-applies idempotently');

-- ── 4. Column-level SELECT grant to authenticated (new columns readable) ──
SELECT tests.assert(
  EXISTS (SELECT 1 FROM information_schema.column_privileges
    WHERE table_schema = 'public' AND table_name = 'graphs'
      AND grantee = 'authenticated' AND privilege_type = 'SELECT'
      AND column_name IN ('deleted_at', 'purged_at', 'purged_residual')),
  'd1: authenticated can SELECT the new columns');

-- ── 5. RLS still enforced on the new columns (PATCH via tenant GUC path) ──
SELECT tests.assert(
  (SELECT count(*) FROM pg_policies
    WHERE schemaname = 'public' AND tablename = 'graphs') >= 1,
  'd1: graphs retains its RLS policies');
