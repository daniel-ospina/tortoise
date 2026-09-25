-- ============================================================================
-- SQL-level verification for migration 20260925000001 (issue #3036 — OAuth
-- referential integrity) and its retention/GC sibling.
--
-- The migration adds the two FKs 0016 omitted, both deliberately
-- ON DELETE SET NULL, plus `expires_at` indexes for the retention sweep:
--   * oauth_access_tokens.refresh_token_id → oauth_refresh_tokens(id)
--   * oauth_refresh_tokens.rotated_from    → oauth_refresh_tokens(id)
--
-- The migration's DANGLING-POINTER REPAIR (its only data-mutating statement) is
-- exercised by the harness pre-seed hook in
-- `supabase/tests/pglite/validate.mjs` (a dirty row is seeded BEFORE the
-- migration is applied, then asserted NULL after) — not replayed here. Do not
-- re-add a hand-copied repair predicate to this file: a copy cannot make the
-- migration text the thing under test.
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
-- Table-QUALIFIED: PostgreSQL scopes constraint/index names per table, so a
-- same-named object on another table must not be able to satisfy these probes.
SELECT tests.assert(
  (SELECT count(*) FROM pg_constraint
    WHERE conname IN ('fk_oauth_access_tokens_refresh_token',
                      'fk_oauth_refresh_tokens_rotated_from')
      AND conrelid IN ('public.oauth_access_tokens'::regclass,
                       'public.oauth_refresh_tokens'::regclass)) = 2,
  '3036: both OAuth FK constraints exist');

SELECT tests.assert(
  (SELECT confdeltype FROM pg_constraint
    WHERE conname = 'fk_oauth_access_tokens_refresh_token'
      AND conrelid = 'public.oauth_access_tokens'::regclass) = 'n',
  '3036: refresh_token_id FK is ON DELETE SET NULL (not CASCADE)');

SELECT tests.assert(
  (SELECT confdeltype FROM pg_constraint
    WHERE conname = 'fk_oauth_refresh_tokens_rotated_from'
      AND conrelid = 'public.oauth_refresh_tokens'::regclass) = 'n',
  '3036: rotated_from FK is ON DELETE SET NULL (not CASCADE)');

-- ── 2. Retention-sweep indexes on expires_at exist (all three tables) ──────
-- Row-VALUE pairing, not two independent INs: a cross-product would be
-- satisfied by all three index names landing on ONE table (a copy-paste swap),
-- leaving two tables unindexed while the count still read 3. The regex anchors
-- the KEY LIST to the exact shipped single-column ascending index —
-- `LIKE '%(expires_at)'` also matched `<wrong-key> INCLUDE (expires_at)` (an
-- INCLUDE column cannot serve the sweep's search qualifier) and treated `_` as
-- a wildcard. A future legitimate descriptor change (DESC, WHERE) reds here by
-- design: the sweep indexes are frozen history for this migration.
SELECT tests.assert(
  (SELECT count(*) FROM pg_indexes
    WHERE schemaname = 'public'
      AND (tablename, indexname) IN (
            ('oauth_codes', 'idx_oauth_codes_expires'),
            ('oauth_access_tokens', 'idx_oauth_access_tokens_expires'),
            ('oauth_refresh_tokens', 'idx_oauth_refresh_tokens_expires'))
      AND indexdef ~ 'USING btree \(expires_at\)$') = 3,
  '3036: all three expires_at sweep indexes exist on their own table');

-- ── 3. A dangling reference is REJECTED — by the constraint under test ─────
-- GET STACKED DIAGNOSTICS pins WHICH constraint fired: a future FK on the same
-- table must not be able to make this probe pass for the wrong reason.
DO $$
DECLARE rejected boolean := false; which text;
BEGIN
  BEGIN
    INSERT INTO public.oauth_access_tokens
      (id, token_hash, client_id, user_id, org_id, expires_at, refresh_token_id)
    VALUES ('3036-access-dangling', '3036-h-dangling', '3036-client',
            '3036-user', '3036-org', now() + interval '1 hour',
            'no-such-refresh-row');
  EXCEPTION WHEN foreign_key_violation THEN
    rejected := true;
    GET STACKED DIAGNOSTICS which = CONSTRAINT_NAME;
  END;
  PERFORM tests.assert(rejected,
    '3036: an access.refresh_token_id pointing at a missing refresh row must be rejected');
  PERFORM tests.assert(which = 'fk_oauth_access_tokens_refresh_token',
    '3036: the rejection must come from fk_oauth_access_tokens_refresh_token, got ' || coalesce(which, '<none>'));
END $$;

DO $$
DECLARE rejected boolean := false; which text;
BEGIN
  BEGIN
    INSERT INTO public.oauth_refresh_tokens
      (id, token_hash, client_id, user_id, org_id, expires_at, rotated_from)
    VALUES ('3036-refresh-dangling', '3036-h-r-dangling', '3036-client',
            '3036-user', '3036-org', now() + interval '1 hour',
            'no-such-ancestor-row');
  EXCEPTION WHEN foreign_key_violation THEN
    rejected := true;
    GET STACKED DIAGNOSTICS which = CONSTRAINT_NAME;
  END;
  PERFORM tests.assert(rejected,
    '3036: a refresh.rotated_from pointing at a missing row must be rejected');
  PERFORM tests.assert(which = 'fk_oauth_refresh_tokens_rotated_from',
    '3036: the rejection must come from fk_oauth_refresh_tokens_rotated_from, got ' || coalesce(which, '<none>'));
END $$;

-- ── 4. Deleting a refresh row SETS NULL — it does not CASCADE ──────────────
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

-- ── 5. The rotation CHAIN does not cascade ─────────────────────────────────
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

-- ── 6. A BULK chain delete (the sweep's real shape) is also safe ───────────
-- The sweep issues one multi-row DELETE, not a single-row delete. Build a
-- 3-deep chain, delete the two oldest in ONE statement, and assert the live
-- tail survives with its link nulled.
INSERT INTO public.oauth_refresh_tokens
  (id, token_hash, client_id, user_id, org_id, expires_at)
VALUES ('3036-bulk-1', '3036-h-b1', '3036-client', '3036-user',
        '3036-org', now() + interval '1 hour');
INSERT INTO public.oauth_refresh_tokens
  (id, token_hash, client_id, user_id, org_id, expires_at, rotated_from)
VALUES ('3036-bulk-2', '3036-h-b2', '3036-client', '3036-user',
        '3036-org', now() + interval '1 hour', '3036-bulk-1');
INSERT INTO public.oauth_refresh_tokens
  (id, token_hash, client_id, user_id, org_id, expires_at, rotated_from)
VALUES ('3036-bulk-3', '3036-h-b3', '3036-client', '3036-user',
        '3036-org', now() + interval '1 hour', '3036-bulk-2');

DELETE FROM public.oauth_refresh_tokens WHERE id IN ('3036-bulk-1', '3036-bulk-2');

SELECT tests.assert(
  (SELECT count(*) FROM public.oauth_refresh_tokens
    WHERE id = '3036-bulk-3') = 1,
  '3036: a bulk delete must NOT cascade through the chain to the live tail');
SELECT tests.assert(
  (SELECT rotated_from FROM public.oauth_refresh_tokens
    WHERE id = '3036-bulk-3') IS NULL,
  '3036: the live tail keeps its row with rotated_from nulled after a bulk delete');

-- ── 7. No dangling references remain (the migration-repair invariant) ──────
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

-- ── Cleanup ────────────────────────────────────────────────────────────────
DELETE FROM public.oauth_access_tokens  WHERE id LIKE '3036-%';
DELETE FROM public.oauth_refresh_tokens WHERE id LIKE '3036-%';
DELETE FROM public.oauth_clients        WHERE id LIKE '3036-%';
DELETE FROM public.organizations        WHERE id LIKE '3036-%';
