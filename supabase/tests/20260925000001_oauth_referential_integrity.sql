-- ============================================================================
-- SQL-level verification for migration 20260925000001 (issue #3036 — OAuth
-- referential integrity) and its retention/GC sibling.
--
-- The migration adds the two FKs 0016 omitted, both deliberately
-- ON DELETE SET NULL:
--   * oauth_access_tokens.refresh_token_id → oauth_refresh_tokens(id)
--   * oauth_refresh_tokens.rotated_from    → oauth_refresh_tokens(id)
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--   (the harness applies ALL migrations, then runs this suite with
--   ON_ERROR_STOP semantics — every assertion RAISEs on failure)
--
-- Test rows use the '3036-' id prefix for safe cleanup.
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

-- ── Cleanup any prior rows (idempotent re-runs) ────────────────────────────
DELETE FROM public.oauth_access_tokens  WHERE id LIKE '3036-%';
DELETE FROM public.oauth_refresh_tokens WHERE id LIKE '3036-%';
DELETE FROM public.oauth_clients        WHERE id LIKE '3036-%';
DELETE FROM public.organizations        WHERE id LIKE '3036-%';

-- Fixtures: one org + one client (the FKs under test live on the oauth tables).
INSERT INTO public.organizations (id, name, graph_name)
VALUES ('3036-org', '3036-org', 'org_3036-org');
INSERT INTO public.oauth_clients (id, client_name)
VALUES ('3036-client', '3036-client');

-- ── 1. Both constraints exist with the deliberate ON DELETE SET NULL policy ─
SELECT tests.assert(
  (SELECT count(*) FROM pg_constraint
    WHERE conname IN ('fk_oauth_access_tokens_refresh_token',
                      'fk_oauth_refresh_tokens_rotated_from')) = 2,
  '3036: both OAuth FK constraints exist');

SELECT tests.assert(
  (SELECT confdeltype FROM pg_constraint
    WHERE conname = 'fk_oauth_access_tokens_refresh_token') = 'n',
  '3036: refresh_token_id FK is ON DELETE SET NULL (not CASCADE)');

SELECT tests.assert(
  (SELECT confdeltype FROM pg_constraint
    WHERE conname = 'fk_oauth_refresh_tokens_rotated_from') = 'n',
  '3036: rotated_from FK is ON DELETE SET NULL (not CASCADE)');

-- ── 2. A dangling reference is REJECTED ────────────────────────────────────
DO $$
DECLARE rejected boolean := false;
BEGIN
  BEGIN
    INSERT INTO public.oauth_access_tokens
      (id, token_hash, client_id, user_id, org_id, expires_at, refresh_token_id)
    VALUES ('3036-access-dangling', '3036-h-dangling', '3036-client',
            '3036-user', '3036-org', now() + interval '1 hour',
            'no-such-refresh-row');
  EXCEPTION WHEN foreign_key_violation THEN rejected := true;
  END;
  PERFORM tests.assert(rejected,
    '3036: an access.refresh_token_id pointing at a missing refresh row must be rejected');
END $$;

DO $$
DECLARE rejected boolean := false;
BEGIN
  BEGIN
    INSERT INTO public.oauth_refresh_tokens
      (id, token_hash, client_id, user_id, org_id, expires_at, rotated_from)
    VALUES ('3036-refresh-dangling', '3036-h-r-dangling', '3036-client',
            '3036-user', '3036-org', now() + interval '1 hour',
            'no-such-ancestor-row');
  EXCEPTION WHEN foreign_key_violation THEN rejected := true;
  END;
  PERFORM tests.assert(rejected,
    '3036: a refresh.rotated_from pointing at a missing row must be rejected');
END $$;

-- ── 3. Deleting a refresh row SETS NULL — it does not CASCADE ──────────────
INSERT INTO public.oauth_refresh_tokens
  (id, token_hash, client_id, user_id, org_id, expires_at)
VALUES ('3036-refresh-a', '3036-h-ra', '3036-client', '3036-user',
        '3036-org', now() + interval '1 hour');
INSERT INTO public.oauth_access_tokens
  (id, token_hash, client_id, user_id, org_id, expires_at, refresh_token_id)
VALUES ('3036-access-a', '3036-h-aa', '3036-client', '3036-user',
        '3036-org', now() + interval '1 hour', '3036-refresh-a');

DELETE FROM public.oauth_refresh_tokens WHERE id = '3036-refresh-a';

SELECT tests.assert(
  (SELECT count(*) FROM public.oauth_access_tokens WHERE id = '3036-access-a') = 1,
  '3036: deleting the parent refresh row must NOT cascade-delete the access row');
SELECT tests.assert(
  (SELECT refresh_token_id FROM public.oauth_access_tokens
    WHERE id = '3036-access-a') IS NULL,
  '3036: the surviving access row has refresh_token_id set to NULL');

-- ── 4. The rotation CHAIN does not cascade ─────────────────────────────────
INSERT INTO public.oauth_refresh_tokens
  (id, token_hash, client_id, user_id, org_id, expires_at)
VALUES ('3036-chain-parent', '3036-h-cp', '3036-client', '3036-user',
        '3036-org', now() + interval '1 hour');
INSERT INTO public.oauth_refresh_tokens
  (id, token_hash, client_id, user_id, org_id, expires_at, rotated_from)
VALUES ('3036-chain-child', '3036-h-cc', '3036-client', '3036-user',
        '3036-org', now() + interval '1 hour', '3036-chain-parent');

DELETE FROM public.oauth_refresh_tokens WHERE id = '3036-chain-parent';

SELECT tests.assert(
  (SELECT count(*) FROM public.oauth_refresh_tokens
    WHERE id = '3036-chain-child') = 1,
  '3036: reaping an ancestor must NOT cascade through the rotation chain');
SELECT tests.assert(
  (SELECT rotated_from FROM public.oauth_refresh_tokens
    WHERE id = '3036-chain-child') IS NULL,
  '3036: the surviving descendant has rotated_from set to NULL');

-- ── 5. No dangling references remain (the migration-repair invariant) ──────
SELECT tests.assert(
  (SELECT count(*) FROM public.oauth_access_tokens a
    WHERE a.refresh_token_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM public.oauth_refresh_tokens r
                       WHERE r.id = a.refresh_token_id)) = 0,
  '3036: no access row dangles after the migration repair');
SELECT tests.assert(
  (SELECT count(*) FROM public.oauth_refresh_tokens t
    WHERE t.rotated_from IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM public.oauth_refresh_tokens r
                       WHERE r.id = t.rotated_from)) = 0,
  '3036: no rotation link dangles after the migration repair');

-- ── 6. A pre-existing dangling row is repaired before the FK validates ─────
-- The FK now blocks NEW dangling refs, so simulate a DIRTY pre-migration DB:
-- drop the access FK, plant a dangling pointer, replay the migration's repair
-- predicate (20260925000001 step 1 — kept verbatim here; the migration is
-- append-only so it cannot drift), then re-add the FK. The ADD must succeed
-- and the planted pointer must be NULL — i.e. the backfill repair works.
ALTER TABLE public.oauth_access_tokens
    DROP CONSTRAINT IF EXISTS fk_oauth_access_tokens_refresh_token;
INSERT INTO public.oauth_access_tokens
  (id, token_hash, client_id, user_id, org_id, expires_at, refresh_token_id)
VALUES ('3036-access-dirty', '3036-h-dirty', '3036-client', '3036-user',
        '3036-org', now() + interval '1 hour', '3036-refresh-gone');

UPDATE public.oauth_access_tokens AS a
   SET refresh_token_id = NULL
 WHERE a.refresh_token_id IS NOT NULL
   AND NOT EXISTS (SELECT 1 FROM public.oauth_refresh_tokens r
                    WHERE r.id = a.refresh_token_id);

ALTER TABLE public.oauth_access_tokens
    ADD CONSTRAINT fk_oauth_access_tokens_refresh_token
    FOREIGN KEY (refresh_token_id)
    REFERENCES public.oauth_refresh_tokens (id)
    ON DELETE SET NULL;

SELECT tests.assert(
  (SELECT refresh_token_id FROM public.oauth_access_tokens
    WHERE id = '3036-access-dirty') IS NULL,
  '3036: the migration repair nulls a pre-existing dangling pointer');

-- ── Cleanup ────────────────────────────────────────────────────────────────
DELETE FROM public.oauth_access_tokens  WHERE id LIKE '3036-%';
DELETE FROM public.oauth_refresh_tokens WHERE id LIKE '3036-%';
DELETE FROM public.oauth_clients        WHERE id LIKE '3036-%';
DELETE FROM public.organizations        WHERE id LIKE '3036-%';
