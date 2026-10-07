-- ============================================================================
-- SQL-level verification for migration 20260925000002 (issue #3027 — OAuth
-- authorization-code redemption state).
--
-- The migration adds the durable outcome of a code claim:
--   * oauth_codes.redemption_state / redemption_id / redemption_settled_at /
--     redemption_note, the state-vocabulary CHECK, and the DIRECTIONAL CHECK
--     `used_at IS NULL ⇒ state = 'unclaimed'`
--     (`used_at IS NOT NULL OR redemption_state = 'unclaimed'` — deliberately NOT
--     the biconditional: the pre-#3027 writer sets `used_at` alone and the
--     constraint must not break it during the rollout. See the migration box.)
--   * oauth_access_tokens.code_id / oauth_refresh_tokens.code_id, the provenance
--     link back to the authorizing code, with ON DELETE SET NULL + indexes.
--
-- The migration's BACKFILL (used_at IS NOT NULL → 'burned') is exercised by the
-- harness pre-seed hook in `supabase/tests/pglite/validate.mjs` — a consumed row
-- is inserted BEFORE the migration is applied, then asserted 'burned' after.
-- Do not re-add a hand-copied backfill predicate here: a copy cannot make the
-- migration text the thing under test.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--
-- Test rows use the '3027-' id/hash prefix for safe cleanup.
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
DELETE FROM public.oauth_access_tokens  WHERE token_hash LIKE '3027-%';
DELETE FROM public.oauth_refresh_tokens WHERE token_hash LIKE '3027-%';
DELETE FROM public.oauth_codes          WHERE code_hash LIKE '3027-%';
DELETE FROM public.oauth_clients        WHERE id LIKE '3027-%';
DELETE FROM public.organizations        WHERE id LIKE '3027-%';

-- Fixtures: one org + one client (FKs on oauth_codes require them).
INSERT INTO public.organizations (id, name, graph_name)
VALUES ('3027-org', '3027-org', 'org_3027-org');
INSERT INTO public.oauth_clients (id, client_name)
VALUES ('3027-client', '3027-client');

-- ── 1. The four columns exist; only `redemption_state` is NOT NULL + defaulted
SELECT tests.assert(
  (SELECT count(*) FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'oauth_codes'
      AND column_name IN ('redemption_state', 'redemption_id',
                          'redemption_settled_at', 'redemption_note')) = 4,
  '3027: all four redemption-state columns exist on oauth_codes');

SELECT tests.assert(
  (SELECT is_nullable = 'NO' AND column_default LIKE '%unclaimed%'
     FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'oauth_codes'
      AND column_name = 'redemption_state'),
  '3027: redemption_state is NOT NULL with an unclaimed default');

-- ── 2. Both CHECK constraints exist, on the right table ────────────────────
-- Table-QUALIFIED: names are scoped per table in PostgreSQL, so a same-named
-- object elsewhere must not be able to satisfy the probe.
SELECT tests.assert(
  (SELECT count(*) FROM pg_constraint
    WHERE conname IN ('chk_oauth_codes_redemption_state',
                      'chk_oauth_codes_redemption_used_at')
      AND conrelid = 'public.oauth_codes'::regclass
      AND contype = 'c') = 2,
  '3027: both redemption CHECK constraints exist on oauth_codes');

-- The biconditional form must NOT be present: it breaks the deployed writer.
-- BOTH legacy names, so a rename that forgets one is caught.
SELECT tests.assert(
  (SELECT count(*) FROM pg_constraint
    WHERE conname IN ('ck_oauth_codes_redemption_used_at',
                      'ck_oauth_codes_redemption_state')
      AND conrelid = 'public.oauth_codes'::regclass) = 0,
  '3027: the legacy ck_ constraint names must not exist (the biconditional rejected the legacy writer)');

-- ── 3. The default lands as 'unclaimed' with used_at NULL ──────────────────
DO $$
DECLARE got text;
BEGIN
  INSERT INTO public.oauth_codes
    (code_hash, client_id, user_id, org_id, redirect_uri, code_challenge, expires_at)
  VALUES ('3027-h-default', '3027-client', '3027-user', '3027-org',
          'https://app.example/cb', 'challenge', now() + interval '10 min')
  RETURNING redemption_state INTO got;
  PERFORM tests.assert(got = 'unclaimed',
    '3027: a code inserted without a state defaults to unclaimed, got ' || coalesce(got, '<null>'));
END $$;

-- ── 4. The state vocabulary is enforced ────────────────────────────────────
DO $$
DECLARE rejected boolean := false; which text;
BEGIN
  BEGIN
    INSERT INTO public.oauth_codes
      (code_hash, client_id, user_id, org_id, redirect_uri, code_challenge,
       expires_at, used_at, redemption_state)
    VALUES ('3027-h-vocab', '3027-client', '3027-user', '3027-org',
            'https://app.example/cb', 'challenge', now() + interval '10 min',
            now(), 'zombie');
  EXCEPTION WHEN check_violation THEN
    rejected := true;
    GET STACKED DIAGNOSTICS which = CONSTRAINT_NAME;
  END;
  PERFORM tests.assert(rejected, '3027: an unknown redemption_state must be rejected');
  PERFORM tests.assert(which = 'chk_oauth_codes_redemption_state',
    '3027: the rejection must come from chk_oauth_codes_redemption_state, got '
    || coalesce(which, '<none>'));
END $$;

-- ── 5. The DIRECTIONAL state/`used_at` invariant ──────────────────────────
-- ⇉ TOLERATED: 'unclaimed' WITH a claim timestamp. This is exactly the pre-#3027
--   writer's shape (`_consume_code` PATCHes `used_at` alone). It must be
--   ACCEPTED, because `deploy-hosted`'s fail-closed drift gate requires prod to
--   hold this migration BEFORE the new image ships, so the old writer serves
--   traffic against this schema. If this ever starts being rejected, the rollout
--   is broken and every authorization-code exchange returns 23514.
DO $$
BEGIN
  INSERT INTO public.oauth_codes
    (code_hash, client_id, user_id, org_id, redirect_uri, code_challenge,
     expires_at, used_at, redemption_state)
  VALUES ('3027-h-legacy-claim', '3027-client', '3027-user', '3027-org',
          'https://app.example/cb', 'challenge', now() + interval '10 min',
          now(), 'unclaimed');
  PERFORM tests.assert(true,
    '3027: the legacy writer (used_at alone, state left unclaimed) must be ACCEPTED');
END $$;

-- A terminal/claimed state with NO claim timestamp: the outcome write that
-- skipped the claim. This is the direction the constraint EXISTS for — it is what
-- makes `_settle_redemption`'s `redemption_state='claimed'` CAS filter imply a
-- `used_at` value, and it must stay rejected.
DO $$
DECLARE rejected boolean := false; which text;
BEGIN
  BEGIN
    INSERT INTO public.oauth_codes
      (code_hash, client_id, user_id, org_id, redirect_uri, code_challenge,
       expires_at, used_at, redemption_state)
    VALUES ('3027-h-bad-minted', '3027-client', '3027-user', '3027-org',
            'https://app.example/cb', 'challenge', now() + interval '10 min',
            NULL, 'minted');
  EXCEPTION WHEN check_violation THEN
    rejected := true;
    GET STACKED DIAGNOSTICS which = CONSTRAINT_NAME;
  END;
  PERFORM tests.assert(rejected,
    '3027: a terminal state must require a claim timestamp');
  PERFORM tests.assert(which = 'chk_oauth_codes_redemption_used_at',
    '3027: the disagreement must be caught by chk_oauth_codes_redemption_used_at, got '
    || coalesce(which, '<none>'));
END $$;

-- …and the same on UPDATE: settling is fine, un-settling a claimed row (clearing
-- `used_at` while leaving the state) is not.
DO $$
DECLARE code_pk bigint; rejected boolean := false; which text;
BEGIN
  INSERT INTO public.oauth_codes
    (code_hash, client_id, user_id, org_id, redirect_uri, code_challenge,
     expires_at, used_at, redemption_state)
  VALUES ('3027-h-update-drift', '3027-client', '3027-user', '3027-org',
          'https://app.example/cb', 'challenge', now() + interval '10 min',
          now(), 'claimed')
  RETURNING id INTO code_pk;
  BEGIN
    UPDATE public.oauth_codes SET used_at = NULL WHERE id = code_pk;
  EXCEPTION WHEN check_violation THEN
    rejected := true;
    GET STACKED DIAGNOSTICS which = CONSTRAINT_NAME;
  END;
  PERFORM tests.assert(rejected AND which = 'chk_oauth_codes_redemption_used_at',
    '3027: clearing used_at on a claimed row must be rejected by chk_oauth_codes_redemption_used_at');
  DELETE FROM public.oauth_codes WHERE id = code_pk;
END $$;

-- ── 6. Both code_id FKs exist and are ON DELETE SET NULL (not CASCADE) ─────
SELECT tests.assert(
  (SELECT count(*) FROM pg_constraint
    WHERE conname IN ('fk_oauth_access_tokens_code', 'fk_oauth_refresh_tokens_code')
      AND conrelid IN ('public.oauth_access_tokens'::regclass,
                       'public.oauth_refresh_tokens'::regclass)) = 2,
  '3027: both code_id FK constraints exist');

SELECT tests.assert(
  (SELECT confdeltype FROM pg_constraint
    WHERE conname = 'fk_oauth_access_tokens_code'
      AND conrelid = 'public.oauth_access_tokens'::regclass) = 'n',
  '3027: access.code_id FK is ON DELETE SET NULL (not CASCADE)');

SELECT tests.assert(
  (SELECT confdeltype FROM pg_constraint
    WHERE conname = 'fk_oauth_refresh_tokens_code'
      AND conrelid = 'public.oauth_refresh_tokens'::regclass) = 'n',
  '3027: refresh.code_id FK is ON DELETE SET NULL (not CASCADE)');

-- ── 7. Both reconciler indexes exist on their own table ────────────────────
-- Row-VALUE pairing, not two independent INs (mirrors the #3036 suite): a
-- cross-product would be satisfied by both names landing on ONE table.
SELECT tests.assert(
  (SELECT count(*) FROM pg_indexes
    WHERE schemaname = 'public'
      AND (tablename, indexname) IN (
            ('oauth_access_tokens', 'idx_oauth_access_tokens_code'),
            ('oauth_refresh_tokens', 'idx_oauth_refresh_tokens_code'))
      AND indexdef ~ 'USING btree \(code_id\)$') = 2,
  '3027: both code_id link indexes exist on their own table');

-- ── 8. A dangling code_id is REJECTED — by the constraint under test ───────
DO $$
DECLARE rejected boolean := false; which text;
BEGIN
  BEGIN
    INSERT INTO public.oauth_access_tokens
      (id, token_hash, client_id, user_id, org_id, expires_at, code_id)
    VALUES ('3027-acc-dangling', '3027-h-acc-dangling', '3027-client',
            '3027-user', '3027-org', now() + interval '1 hour', 424242);
  EXCEPTION WHEN foreign_key_violation THEN
    rejected := true;
    GET STACKED DIAGNOSTICS which = CONSTRAINT_NAME;
  END;
  PERFORM tests.assert(rejected,
    '3027: an access.code_id pointing at a missing code must be rejected');
  PERFORM tests.assert(which = 'fk_oauth_access_tokens_code',
    '3027: the rejection must come from fk_oauth_access_tokens_code, got '
    || coalesce(which, '<none>'));
END $$;

-- ── 9. Deleting a code row SETS NULL — it does not CASCADE ─────────────────
-- The grant survives its dead code (bearer credentials are independent, D6); the
-- retention sweep must not be able to revoke a live token by reaping a code.
DO $$
DECLARE code_pk bigint; link bigint;
BEGIN
  INSERT INTO public.oauth_codes
    (code_hash, client_id, user_id, org_id, redirect_uri, code_challenge,
     expires_at, used_at, redemption_state)
  VALUES ('3027-h-linked', '3027-client', '3027-user', '3027-org',
          'https://app.example/cb', 'challenge', now() + interval '10 min',
          now(), 'minted')
  RETURNING id INTO code_pk;

  INSERT INTO public.oauth_refresh_tokens
    (id, token_hash, client_id, user_id, org_id, expires_at, code_id)
  VALUES ('3027-ref-linked', '3027-h-ref-linked', '3027-client', '3027-user',
          '3027-org', now() + interval '1 hour', code_pk);
  INSERT INTO public.oauth_access_tokens
    (id, token_hash, client_id, user_id, org_id, expires_at, refresh_token_id, code_id)
  VALUES ('3027-acc-linked', '3027-h-acc-linked', '3027-client', '3027-user',
          '3027-org', now() + interval '1 hour', '3027-ref-linked', code_pk);

  DELETE FROM public.oauth_codes WHERE id = code_pk;

  PERFORM tests.assert(
    (SELECT count(*) FROM public.oauth_refresh_tokens
      WHERE id = '3027-ref-linked') = 1
    AND (SELECT count(*) FROM public.oauth_access_tokens
          WHERE id = '3027-acc-linked') = 1,
    '3027: reaping a code must NOT cascade-delete the token rows');
  PERFORM tests.assert(
    (SELECT code_id FROM public.oauth_refresh_tokens
      WHERE id = '3027-ref-linked') IS NULL,
    '3027: the surviving refresh row has code_id set to NULL');
  PERFORM tests.assert(
    (SELECT code_id FROM public.oauth_access_tokens
      WHERE id = '3027-acc-linked') IS NULL,
    '3027: the surviving access row has code_id set to NULL');
END $$;

-- ── 10. No dangling links remain ───────────────────────────────────────────
SELECT tests.assert(
  (SELECT count(*) FROM public.oauth_access_tokens a
    WHERE a.code_id IS NOT NULL
      AND NOT EXISTS (SELECT 1 FROM public.oauth_codes c WHERE c.id = a.code_id)) = 0
  AND (SELECT count(*) FROM public.oauth_refresh_tokens r
        WHERE r.code_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM public.oauth_codes c WHERE c.id = r.code_id)) = 0,
  '3027: no code_id link dangles after the SET NULL actions');

-- ── Cleanup ────────────────────────────────────────────────────────────────
DELETE FROM public.oauth_access_tokens  WHERE token_hash LIKE '3027-%';
DELETE FROM public.oauth_refresh_tokens WHERE token_hash LIKE '3027-%';
DELETE FROM public.oauth_codes          WHERE code_hash LIKE '3027-%';
DELETE FROM public.oauth_clients        WHERE id LIKE '3027-%';
DELETE FROM public.organizations        WHERE id LIKE '3027-%';
