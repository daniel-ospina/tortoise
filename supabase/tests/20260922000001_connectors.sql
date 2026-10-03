-- ============================================================================
-- SQL-level verification for migration 20260922000001 (issue #2636, epic #2632)
-- connectors table: post-rename schema targeting, RLS scoping, credential
-- column protection.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--   (the harness applies ALL migrations incl. 20260922000001 in filename order,
--   then runs this suite with ON_ERROR_STOP semantics — every assertion RAISEs
--   on failure)
--
-- This suite is ALSO the migration-apply proof: the harness applies migrations
-- in filename order, so a green run means this file resolved
-- public.organizations / public.org_memberships — objects that only exist after
-- 20260915000001 — i.e. the timestamp ordering is correct and the FK types
-- agree.
--
-- Test rows use the "-2636" suffix for safe cleanup.
-- ============================================================================

-- ── Assertion helper (matches harness convention) ───────────────────────────
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

-- ── 1. Schema presence + post-rename targeting ──────────────────────────────
SELECT tests.assert(
  EXISTS (SELECT 1 FROM information_schema.tables
          WHERE table_schema='public' AND table_name='connectors'),
  '2636: connectors table exists');

-- org_id MUST be text: organizations.id is text and org ids are 26-hex, so a
-- uuid column makes the FK un-implementable and `uuid IN (SELECT text)` raise
-- `operator does not exist: uuid = text`.
SELECT tests.assert(
  (SELECT data_type FROM information_schema.columns
    WHERE table_schema='public' AND table_name='connectors' AND column_name='org_id') = 'text',
  '2636: connectors.org_id is text (matches organizations.id)');

-- The FK must target the POST-rename table. The pre-rename target would fail
-- on the hosted project, where 20260915000001 has already run.
SELECT tests.assert(
  EXISTS (SELECT 1 FROM pg_constraint c
            JOIN pg_class t  ON t.oid  = c.conrelid
            JOIN pg_class rt ON rt.oid = c.confrelid
           WHERE t.relname = 'connectors' AND c.contype = 'f'
             AND rt.relname = 'organizations'),
  '2636: connectors.org_id FK targets organizations');
SELECT tests.assert(
  NOT EXISTS (SELECT 1 FROM pg_class
               WHERE relname='teams' AND relnamespace='public'::regnamespace),
  '2636: no `teams` table survives the tenancy rename (no compat view)');

SELECT tests.assert(
  EXISTS (SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name='connectors'
            AND column_name='credential_enc'),
  '2636: connectors.credential_enc exists');

SELECT tests.assert(
  (SELECT count(*) FROM pg_indexes
    WHERE schemaname='public' AND tablename='connectors'
      AND indexname IN ('idx_connectors_org_source','idx_connectors_org',
                        'idx_connectors_sync_eligible')) = 3,
  '2636: all three connectors indexes exist');

SELECT tests.assert(
  (SELECT count(*) FROM pg_trigger
    WHERE tgname='trg_connectors_updated_at' AND NOT tgisinternal) = 1,
  '2636: updated_at trigger exists');

SELECT tests.assert(
  (SELECT relrowsecurity FROM pg_class WHERE relname='connectors') = true,
  '2636: RLS enabled on connectors');

SELECT tests.assert(
  (SELECT count(*) FROM pg_policies
    WHERE schemaname='public' AND tablename='connectors'
      AND policyname IN ('connectors_read_org','connectors_write_org',
                         'connectors_update_org','connectors_delete_org')) = 4,
  '2636: all four RLS policies exist');

-- ── 2. Column-level protection (effective pattern, per 0006/0009) ───────────
DO $$ BEGIN
  PERFORM tests.assert(
    has_column_privilege('authenticated','public.connectors','credential_enc','SELECT') = false,
    'credential_enc: authenticated must NOT have SELECT');
  PERFORM tests.assert(
    has_column_privilege('anon','public.connectors','credential_enc','SELECT') = false,
    'credential_enc: anon must NOT have SELECT');
  PERFORM tests.assert(
    has_column_privilege('authenticated','public.connectors','credential_enc','UPDATE') = false,
    'credential_enc: authenticated must NOT have UPDATE');
  PERFORM tests.assert(
    has_column_privilege('authenticated','public.connectors','credential_enc','INSERT') = false,
    'credential_enc: authenticated must NOT have INSERT');
  PERFORM tests.assert(
    has_column_privilege('service_role','public.connectors','credential_enc','SELECT') = true,
    'credential_enc: service_role must keep SELECT');
  -- the non-secret columns stay usable for SELECT
  PERFORM tests.assert(
    has_column_privilege('authenticated','public.connectors','config','SELECT') = true,
    'connectors.config: authenticated keeps SELECT');
  -- #2642 re-review P2: this table follows the repo-wide SELECT-only
  -- ownership model (0006/0007/0008/0009/20260901000001) — NO write grant for
  -- `authenticated`; writes go through the service-role seam. This assertion
  -- REPLACED `sync_status ... UPDATE = true`, which encoded the write grants
  -- this migration no longer issues.
  PERFORM tests.assert(
    has_column_privilege('authenticated','public.connectors','sync_status','UPDATE') = false,
    'connectors.sync_status: authenticated must NOT have UPDATE');
  PERFORM tests.assert(
    has_table_privilege('authenticated','public.connectors','INSERT') = false,
    'authenticated: no INSERT grant (SELECT-only ownership model)');
  PERFORM tests.assert(
    has_table_privilege('authenticated','public.connectors','UPDATE') = false,
    'authenticated: no UPDATE grant (SELECT-only ownership model)');
  PERFORM tests.assert(
    has_table_privilege('authenticated','public.connectors','DELETE') = false,
    'authenticated: no DELETE grant (SELECT-only ownership model)');
  PERFORM tests.assert(
    has_table_privilege('service_role','public.connectors','INSERT') = true,
    'service_role: keeps INSERT (the write seam)');
  -- anon has no table-level access at all
  PERFORM tests.assert(
    has_table_privilege('anon','public.connectors','SELECT') = false,
    'anon: no table-level SELECT on connectors');
END $$;

-- ── 3. Behavioral RLS: org member vs non-member ─────────────────────────────
-- Fixtures as postgres (bypasses RLS).
INSERT INTO public.organizations (id, name, graph_name)
VALUES ('org-a-2636','Alpha 2636','org_alpha_2636'),
       ('org-b-2636','Beta 2636','org_beta_2636')
ON CONFLICT (id) DO NOTHING;

INSERT INTO auth.users (instance_id, id, aud, role, email, encrypted_password,
                        email_confirmed_at, raw_app_meta_data, raw_user_meta_data)
VALUES ('00000000-0000-0000-0000-000000000000'::uuid,
        'a1111111-1111-1111-1111-111111111111'::uuid,
        'authenticated','authenticated','member-a-2636@example.com','', now(),
        '{}'::jsonb,'{}'::jsonb),
       ('00000000-0000-0000-0000-000000000000'::uuid,
        'b2222222-2222-2222-2222-222222222222'::uuid,
        'authenticated','authenticated','outsider-b-2636@example.com','', now(),
        '{}'::jsonb,'{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

-- auth.users INSERT fires handle_new_user, which drops a placeholder membership
-- (org_id=''); clear it so the membership set for these users is deterministic.
DELETE FROM public.org_memberships
 WHERE user_id IN ('a1111111-1111-1111-1111-111111111111'::uuid,
                   'b2222222-2222-2222-2222-222222222222'::uuid);

-- only user A is a member of org-a; user B has NO membership anywhere
INSERT INTO public.org_memberships (user_id, org_id, org_name, key_hash, graph_name, role, status)
VALUES ('a1111111-1111-1111-1111-111111111111'::uuid,'org-a-2636','Alpha 2636',
        'k-a-2636','org_alpha_2636','owner','active');

INSERT INTO public.connectors (id, org_id, source_type, config, credential_enc)
VALUES ('c0000000-0000-0000-0000-000000000001'::uuid,'org-a-2636','github',
        '{"repo":"alpha"}'::jsonb,'enc-blob-a'),
       ('c0000000-0000-0000-0000-000000000002'::uuid,'org-b-2636','github',
        '{"repo":"beta"}'::jsonb,'enc-blob-b')
ON CONFLICT (id) DO NOTHING;

-- member (org-a) sees only org-a
SET ROLE authenticated;
SET request.jwt.claim.sub = 'a1111111-1111-1111-1111-111111111111';
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.connectors WHERE org_id='org-a-2636') = 1,
    'RLS: org member reads own org connector');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.connectors WHERE org_id='org-b-2636') = 0,
    'RLS: cross-org connectors read denied');
  PERFORM tests.assert(
    (SELECT config->>'repo' FROM public.connectors WHERE org_id='org-a-2636') = 'alpha',
    'RLS+grants: safe column (config) readable by member');
  -- credential_enc denied even on a row the member CAN see
  BEGIN
    PERFORM credential_enc FROM public.connectors WHERE org_id='org-a-2636';
    RAISE EXCEPTION 'FAIL: authenticated must not be able to read credential_enc';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  -- #2642 re-review P2: SELECT-only grants mean a member's write is refused by
  -- the ACL — the retained RLS write policies have no `authenticated` client.
  BEGIN
    UPDATE public.connectors SET sync_status='syncing' WHERE org_id='org-a-2636';
    RAISE EXCEPTION 'FAIL: authenticated must not be able to UPDATE connectors';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    DELETE FROM public.connectors WHERE org_id='org-a-2636';
    RAISE EXCEPTION 'FAIL: authenticated must not be able to DELETE connectors';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    INSERT INTO public.connectors (org_id, source_type)
      VALUES ('org-a-2636','slack');
    RAISE EXCEPTION 'FAIL: authenticated must not be able to INSERT connectors';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
RESET ROLE;
RESET request.jwt.claim.sub;

-- outsider (no membership row) sees nothing
SET ROLE authenticated;
SET request.jwt.claim.sub = 'b2222222-2222-2222-2222-222222222222';
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(id) FROM public.connectors) = 0,
    'RLS: user with no membership sees 0 connectors');
END $$;
RESET ROLE;
RESET request.jwt.claim.sub;

-- service_role keeps full access incl. credential_enc (the seam the API uses)
SET ROLE service_role;
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.connectors) = 2,
    'service_role: reads all connectors');
  PERFORM tests.assert(
    (SELECT credential_enc FROM public.connectors
      WHERE id='c0000000-0000-0000-0000-000000000001') = 'enc-blob-a',
    'service_role: reads credential_enc');
END $$;
RESET ROLE;

-- ── cleanup ─────────────────────────────────────────────────────────────────
DELETE FROM public.connectors
 WHERE id IN ('c0000000-0000-0000-0000-000000000001'::uuid,
              'c0000000-0000-0000-0000-000000000002'::uuid);
DELETE FROM public.org_memberships
 WHERE user_id IN ('a1111111-1111-1111-1111-111111111111'::uuid,
                   'b2222222-2222-2222-2222-222222222222'::uuid);
DELETE FROM public.organizations WHERE id IN ('org-a-2636','org-b-2636');
