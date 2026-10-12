-- ============================================================================
-- SQL-level verification for migration 20261013000002 (issue #3025)
-- The OAuth mint is ONE transaction: `redeem_oauth_code` and
-- `rotate_oauth_refresh`.
--
-- WHAT THIS SUITE IS FOR. #3025's whole claim is TRANSACTIONALITY, so the
-- load-bearing assertions here are the two atomicity drills (SECTION 3 and
-- SECTION 5): a failure in the MIDDLE of a function must leave zero new rows
-- and an unset `used_at`. A functional-only suite would pass on the old
-- hand-rolled sequence too (`_issue_tokens` also produced the right rows when
-- nothing failed) — the drills are what distinguish one transaction from four
-- independent HTTP requests.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--
-- Every assertion RAISEs on failure; with ON_ERROR_STOP=1 any failure exits
-- non-zero. Test rows use the '3025-' prefix for safe cleanup.
--
-- HONEST LIMIT: PGlite is SINGLE-CONNECTION, so this suite cannot exercise
-- contention (two concurrent redeems). The single-use CAS is a single UPDATE
-- with the same predicate #1264/#3027 ratified and is covered functionally by
-- the repeat/replay assertions below.
-- ============================================================================

-- ── Assertion helper (tests schema; executed as the harness's superuser) ───
CREATE SCHEMA IF NOT EXISTS tests;
CREATE OR REPLACE FUNCTION tests.assert(cond boolean, msg text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ASSERTION FAILED: %', msg;
  END IF;
END $$;

-- ── Cleanup any prior test rows (idempotent re-runs; child rows first) ──────
DELETE FROM public.oauth_access_tokens  WHERE id LIKE '3025-%';
DELETE FROM public.oauth_refresh_tokens WHERE id LIKE '3025-%';
DELETE FROM public.oauth_codes          WHERE code_hash LIKE '3025-%';
DELETE FROM public.oauth_clients        WHERE id LIKE '3025-%';
DELETE FROM public.organizations        WHERE id LIKE '3025-%';

-- ============================================================================
-- SECTION 1 — grant hygiene: service_role ONLY, exactly ONE overload each
-- ============================================================================
DO $$ BEGIN
  PERFORM tests.assert(
    NOT has_function_privilege('anon', 'public.redeem_oauth_code(bigint,text,text,text,text,text,text,text,text,text,text,timestamp with time zone,timestamp with time zone,timestamp with time zone,boolean)', 'EXECUTE'),
    'anon must NOT EXECUTE redeem_oauth_code');
  PERFORM tests.assert(
    NOT has_function_privilege('authenticated', 'public.redeem_oauth_code(bigint,text,text,text,text,text,text,text,text,text,text,timestamp with time zone,timestamp with time zone,timestamp with time zone,boolean)', 'EXECUTE'),
    'authenticated must NOT EXECUTE redeem_oauth_code');
  PERFORM tests.assert(
    has_function_privilege('service_role', 'public.redeem_oauth_code(bigint,text,text,text,text,text,text,text,text,text,text,timestamp with time zone,timestamp with time zone,timestamp with time zone,boolean)', 'EXECUTE'),
    'service_role must EXECUTE redeem_oauth_code');

  PERFORM tests.assert(
    NOT has_function_privilege('anon', 'public.rotate_oauth_refresh(text,text,text,text,text,timestamp with time zone,timestamp with time zone,timestamp with time zone)', 'EXECUTE'),
    'anon must NOT EXECUTE rotate_oauth_refresh');
  PERFORM tests.assert(
    NOT has_function_privilege('authenticated', 'public.rotate_oauth_refresh(text,text,text,text,text,timestamp with time zone,timestamp with time zone,timestamp with time zone)', 'EXECUTE'),
    'authenticated must NOT EXECUTE rotate_oauth_refresh');
  PERFORM tests.assert(
    has_function_privilege('service_role', 'public.rotate_oauth_refresh(text,text,text,text,text,timestamp with time zone,timestamp with time zone,timestamp with time zone)', 'EXECUTE'),
    'service_role must EXECUTE rotate_oauth_refresh');

  -- One overload each: a DROP/CREATE slip must not leave a stale second body
  -- live (the metering_increment overload trap the harness comments about).
  PERFORM tests.assert(
    (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'public' AND p.proname = 'redeem_oauth_code') = 1,
    'redeem_oauth_code must have exactly ONE overload');
  PERFORM tests.assert(
    (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'public' AND p.proname = 'rotate_oauth_refresh') = 1,
    'rotate_oauth_refresh must have exactly ONE overload');
END $$;

-- ============================================================================
-- SECTION 2 — seed + `redeem_oauth_code` happy path
-- ============================================================================
DO $$
DECLARE
  v_code_id bigint;
  v_res     jsonb;
BEGIN
  INSERT INTO public.organizations (id, name, graph_name)
  VALUES ('3025-org', 'org-3025', 'org_3025_org');
  INSERT INTO public.oauth_clients (id, client_name)
  VALUES ('3025-client', 'client-3025');

  INSERT INTO public.oauth_codes
      (code_hash, client_id, user_id, org_id, redirect_uri, code_challenge,
       code_challenge_method, scope, expires_at)
  VALUES ('3025-code-ok', '3025-client', '3025-user', '3025-org',
          'https://app.example/cb', '3025-challenge', 'S256', 'mcp',
          now() + interval '10 minutes');
  SELECT id INTO v_code_id FROM public.oauth_codes WHERE code_hash = '3025-code-ok';

  v_res := public.redeem_oauth_code(
      v_code_id, '3025-code-ok', '3025-client', '3025-user', '3025-org', 'mcp',
      '3025-redemption', '3025-r-ok', '3025-rh-ok', '3025-a-ok', '3025-ah-ok',
      now(), now() + interval '30 days', now() + interval '1 hour');

  PERFORM tests.assert(v_res->>'status' = 'minted',
      'redeem must report minted, got ' || v_res::text);

  -- The claim, in the SAME statement as the mint: `used_at` set, state
  -- 'claimed' (NOT 'minted' — delivery is #3027's separate gate).
  PERFORM tests.assert(
    (SELECT used_at IS NOT NULL AND redemption_state = 'claimed'
            AND redemption_id = '3025-redemption'
       FROM public.oauth_codes WHERE id = v_code_id),
    'redeem must claim the code (used_at set, state=claimed, redemption_id recorded)');

  -- Both token rows, each carrying the #3027 provenance link back to the code.
  PERFORM tests.assert(
    (SELECT count(*) FROM public.oauth_refresh_tokens
      WHERE id = '3025-r-ok' AND code_id = v_code_id AND revoked_at IS NULL
        AND client_id = '3025-client' AND user_id = '3025-user'
        AND org_id = '3025-org' AND scope = 'mcp') = 1,
    'redeem must insert the refresh row with the code_id provenance link');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.oauth_access_tokens
      WHERE id = '3025-a-ok' AND code_id = v_code_id AND revoked_at IS NULL
        AND refresh_token_id = '3025-r-ok') = 1,
    'redeem must insert the access row linked to the new refresh row');
END $$;

-- ============================================================================
-- SECTION 3 — ATOMICITY DRILL: a failure mid-function leaves ZERO new rows and
-- an UNSET used_at.
-- ============================================================================
-- The failure is induced at the SECOND of the three writes: a pre-existing
-- `oauth_access_tokens.token_hash` makes the access INSERT raise a unique
-- violation AFTER the refresh INSERT has already succeeded. Under the old
-- four-call sequence that produced exactly the live orphan refresh row #3025
-- exists to eliminate; under one transaction the whole function rolls back.
DO $$
DECLARE
  v_code_id bigint;
BEGIN
  -- A row already holding the hash the mint will try to insert.
  INSERT INTO public.oauth_access_tokens
      (id, token_hash, client_id, user_id, org_id, scope, expires_at, refresh_token_id)
  VALUES ('3025-dup-holder', '3025-ah-dup', '3025-client', '3025-user',
          '3025-org', 'mcp', now() + interval '1 hour', NULL);

  INSERT INTO public.oauth_codes
      (code_hash, client_id, user_id, org_id, redirect_uri, code_challenge,
       code_challenge_method, scope, expires_at)
  VALUES ('3025-code-atomic', '3025-client', '3025-user', '3025-org',
          'https://app.example/cb', '3025-challenge', 'S256', 'mcp',
          now() + interval '10 minutes');
  SELECT id INTO v_code_id FROM public.oauth_codes WHERE code_hash = '3025-code-atomic';

  BEGIN
    PERFORM public.redeem_oauth_code(
        v_code_id, '3025-code-atomic', '3025-client', '3025-user', '3025-org',
        'mcp', '3025-redemption-atomic', '3025-r-atomic', '3025-rh-atomic',
        '3025-a-atomic', '3025-ah-dup',
        now(), now() + interval '30 days', now() + interval '1 hour');
    PERFORM tests.assert(false,
        'the redeem must RAISE when the access INSERT violates token_hash UNIQUE');
  EXCEPTION WHEN unique_violation THEN
    NULL;   -- the expected, induced failure
  END;

  PERFORM tests.assert(
    (SELECT count(*) FROM public.oauth_refresh_tokens WHERE id = '3025-r-atomic') = 0,
    'ATOMICITY: the refresh INSERT that already ran must be rolled back — zero new rows');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.oauth_access_tokens WHERE id = '3025-a-atomic') = 0,
    'ATOMICITY: zero new access rows');
  PERFORM tests.assert(
    (SELECT used_at IS NULL AND redemption_state = 'unclaimed'
       FROM public.oauth_codes WHERE id = v_code_id),
    'ATOMICITY: used_at must be UNSET and the state back to unclaimed after a failed redeem');
END $$;

-- ============================================================================
-- SECTION 4 — the single-use CAS: a replayed code writes nothing
-- ============================================================================
DO $$
DECLARE
  v_code_id bigint;
  v_res     jsonb;
BEGIN
  SELECT id INTO v_code_id FROM public.oauth_codes WHERE code_hash = '3025-code-ok';

  v_res := public.redeem_oauth_code(
      v_code_id, '3025-code-ok', '3025-client', '3025-user', '3025-org', 'mcp',
      '3025-redemption-2', '3025-r-2', '3025-rh-2', '3025-a-2', '3025-ah-2',
      now(), now() + interval '30 days', now() + interval '1 hour');

  PERFORM tests.assert(v_res->>'status' = 'not_claimable',
      'a replayed code must report not_claimable, got ' || v_res::text);
  PERFORM tests.assert(
    (SELECT count(*) FROM public.oauth_refresh_tokens WHERE id = '3025-r-2') = 0,
    'a not_claimable redeem must insert NO refresh row');
  PERFORM tests.assert(
    (SELECT redemption_id FROM public.oauth_codes WHERE id = v_code_id) = '3025-redemption',
    'a lost claim must not overwrite the winning claim''s redemption_id');
END $$;

-- ============================================================================
-- SECTION 4a-2 — THE CAS PREDICATE, arm by arm: every conjunct must REFUSE on its
-- own, writing nothing
-- ============================================================================
-- The single-use CAS is `id AND code_hash AND client_id AND user_id AND org_id
-- AND used_at IS NULL AND redemption_state='unclaimed' AND expires_at > p_now`.
-- A suite that exercises only ONE arm leaves the rest unpinned: measured by
-- mutation during test review, `org_id` reddened the harness but dropping
-- `user_id`/`client_id`/`expires_at` did not — and because the Python double
-- re-implements these predicates independently, no Python test compensates.
--
-- Each arm is therefore driven separately, and each asserts the SAME three
-- things: `not_claimable`, the code still `unclaimed` with `used_at IS NULL`,
-- and no token row.
DO $$
DECLARE
  v_code_id bigint;
  v_res     jsonb;
  v_arm     text;
  v_expires timestamptz;
BEGIN
  FOR v_arm IN SELECT unnest(ARRAY[
        'code_hash', 'client_id', 'user_id', 'org_id', 'expired']) LOOP
    INSERT INTO public.oauth_codes
        (code_hash, client_id, user_id, org_id, redirect_uri, code_challenge,
         code_challenge_method, scope, expires_at)
    VALUES ('3025-cas-' || v_arm, '3025-client', '3025-user', '3025-org',
            'https://app.example/cb', '3025-challenge', 'S256', 'mcp',
            CASE WHEN v_arm = 'expired' THEN now() - interval '1 minute'
                 ELSE now() + interval '10 minutes' END);
    SELECT id INTO v_code_id
      FROM public.oauth_codes WHERE code_hash = '3025-cas-' || v_arm;

    -- Exactly ONE parameter is wrong per arm; everything else matches the row.
    v_res := public.redeem_oauth_code(
        v_code_id,
        CASE WHEN v_arm = 'code_hash'  THEN '3025-wrong' ELSE '3025-cas-' || v_arm END,
        CASE WHEN v_arm = 'client_id'  THEN '3025-other' ELSE '3025-client' END,
        CASE WHEN v_arm = 'user_id'    THEN '3025-other' ELSE '3025-user'   END,
        CASE WHEN v_arm = 'org_id'     THEN '3025-other' ELSE '3025-org'    END,
        'mcp', '3025-redemption-' || v_arm, '3025-r-' || v_arm,
        '3025-rh-' || v_arm, '3025-a-' || v_arm, '3025-ah-' || v_arm,
        now(), now() + interval '30 days', now() + interval '1 hour');

    PERFORM tests.assert(v_res->>'status' = 'not_claimable',
        'CAS arm ' || v_arm || ' must refuse with not_claimable, got ' || v_res::text);
    PERFORM tests.assert(
      (SELECT used_at IS NULL AND redemption_state = 'unclaimed'
         FROM public.oauth_codes WHERE id = v_code_id),
      'CAS arm ' || v_arm || ' must leave the code UNCLAIMED');
    PERFORM tests.assert(
      (SELECT count(*) FROM public.oauth_refresh_tokens
        WHERE id = '3025-r-' || v_arm) = 0,
      'CAS arm ' || v_arm || ' must insert NO refresh row');
    PERFORM tests.assert(
      (SELECT count(*) FROM public.oauth_access_tokens
        WHERE id = '3025-a-' || v_arm) = 0,
      'CAS arm ' || v_arm || ' must insert NO access row');
  END LOOP;
END $$;

-- ============================================================================
-- SECTION 4b — the REFUSAL path: `p_burn` claims AND burns, one transaction,
-- with NO token row
-- ============================================================================
-- #3027 records the terminal `claimed -> burned` transition for a code whose
-- request failed validation AFTER the claim (bad PKCE, client/redirect mismatch,
-- unsupported scope, unusable org). #3025 keeps that transition reachable by
-- making it part of the same transaction as the claim — this asserts the
-- transaction really does both, and inserts nothing.
DO $$
DECLARE
  v_code_id bigint;
  v_res     jsonb;
BEGIN
  INSERT INTO public.oauth_codes
      (code_hash, client_id, user_id, org_id, redirect_uri, code_challenge,
       code_challenge_method, scope, expires_at)
  VALUES ('3025-code-burn', '3025-client', '3025-user', '3025-org',
          'https://app.example/cb', '3025-challenge', 'S256', 'mcp',
          now() + interval '10 minutes');
  SELECT id INTO v_code_id FROM public.oauth_codes WHERE code_hash = '3025-code-burn';

  v_res := public.redeem_oauth_code(
      v_code_id, '3025-code-burn', '3025-client', '3025-user', '3025-org', 'mcp',
      '3025-redemption-burn', '3025-r-burn', '3025-rh-burn', '3025-a-burn',
      '3025-ah-burn', now(), now() + interval '30 days',
      now() + interval '1 hour', true);

  PERFORM tests.assert(v_res->>'status' = 'burned',
      'a refusal must report burned, got ' || v_res::text);
  PERFORM tests.assert(
    (SELECT used_at IS NOT NULL AND redemption_state = 'burned'
            AND redemption_note = 'terminal' AND redemption_settled_at IS NOT NULL
       FROM public.oauth_codes WHERE id = v_code_id),
    'a refusal must record the terminal claimed->burned transition with note=terminal');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.oauth_refresh_tokens WHERE id = '3025-r-burn') = 0,
    'a refusal must insert NO refresh row');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.oauth_access_tokens WHERE id = '3025-a-burn') = 0,
    'a refusal must insert NO access row');
END $$;

-- ============================================================================
-- SECTION 5 — `rotate_oauth_refresh`: happy path, loser, and the ATOMICITY DRILL
-- ============================================================================
DO $$
DECLARE
  v_res jsonb;
BEGIN
  -- A previous family: refresh + its live sibling access row.
  INSERT INTO public.oauth_refresh_tokens
      (id, token_hash, client_id, user_id, org_id, scope, expires_at, code_id)
  VALUES ('3025-pr', '3025-prh', '3025-client', '3025-user', '3025-org', 'mcp',
          now() + interval '30 days', NULL);
  INSERT INTO public.oauth_access_tokens
      (id, token_hash, client_id, user_id, org_id, scope, expires_at, refresh_token_id)
  VALUES ('3025-pa', '3025-pah', '3025-client', '3025-user', '3025-org', 'mcp',
          now() + interval '1 hour', '3025-pr');

  -- ── happy path ──
  v_res := public.rotate_oauth_refresh(
      '3025-pr', '3025-r-new', '3025-rh-new', '3025-a-new', '3025-ah-new',
      now(), now() + interval '30 days', now() + interval '1 hour');

  PERFORM tests.assert(v_res->>'status' = 'rotated',
      'rotate must report rotated, got ' || v_res::text);
  PERFORM tests.assert(v_res->>'scope' = 'mcp' AND v_res->>'org_id' = '3025-org',
      'rotate must return the family identity it derived from the claimed row');
  PERFORM tests.assert(
    (SELECT revoked_at IS NOT NULL FROM public.oauth_refresh_tokens WHERE id = '3025-pr'),
    'rotate must revoke the presented refresh token');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.oauth_refresh_tokens
      WHERE id = '3025-r-new' AND rotated_from = '3025-pr' AND revoked_at IS NULL) = 1,
    'rotate must insert the new refresh row with rotated_from set');
  PERFORM tests.assert(
    (SELECT revoked_at IS NOT NULL FROM public.oauth_access_tokens WHERE id = '3025-pa'),
    'rotate must revoke the PREVIOUS family''s live access row(s)');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.oauth_access_tokens WHERE id = '3025-a-new'),
    'rotate must leave the NEW access row live');

  -- ── the loser: the same presented token again, nothing written ──
  v_res := public.rotate_oauth_refresh(
      '3025-pr', '3025-r-loser', '3025-rh-loser', '3025-a-loser', '3025-ah-loser',
      now(), now() + interval '30 days', now() + interval '1 hour');
  PERFORM tests.assert(v_res->>'status' = 'not_claimable',
      'an already-rotated token must report not_claimable, got ' || v_res::text);
  PERFORM tests.assert(
    (SELECT count(*) FROM public.oauth_refresh_tokens WHERE id = '3025-r-loser') = 0,
    'the rotation loser must leave NO orphan refresh row (the pair was never inserted)');

  -- ── ATOMICITY DRILL: the access INSERT fails -> the PREVIOUS token is NOT revoked ──
  INSERT INTO public.oauth_access_tokens
      (id, token_hash, client_id, user_id, org_id, scope, expires_at, refresh_token_id)
  VALUES ('3025-dup-holder-rt', '3025-ah-dup-rt', '3025-client', '3025-user',
          '3025-org', 'mcp', now() + interval '1 hour', NULL);
  INSERT INTO public.oauth_refresh_tokens
      (id, token_hash, client_id, user_id, org_id, scope, expires_at, code_id)
  VALUES ('3025-pr-atomic', '3025-prh-atomic', '3025-client', '3025-user',
          '3025-org', 'mcp', now() + interval '30 days', NULL);

  BEGIN
    PERFORM public.rotate_oauth_refresh(
        '3025-pr-atomic', '3025-r-rt-atomic', '3025-rh-rt-atomic',
        '3025-a-rt-atomic', '3025-ah-dup-rt',
        now(), now() + interval '30 days', now() + interval '1 hour');
    PERFORM tests.assert(false,
        'rotate must RAISE when the access INSERT violates token_hash UNIQUE');
  EXCEPTION WHEN unique_violation THEN
    NULL;
  END;

  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.oauth_refresh_tokens WHERE id = '3025-pr-atomic'),
    'ATOMICITY: the presented token must NOT be left revoked when the rotation rolled back');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.oauth_refresh_tokens WHERE id = '3025-r-rt-atomic') = 0,
    'ATOMICITY: zero new refresh rows after a failed rotation');
END $$;

-- ============================================================================
-- Cleanup
-- ============================================================================
DELETE FROM public.oauth_access_tokens  WHERE id LIKE '3025-%';
DELETE FROM public.oauth_refresh_tokens WHERE id LIKE '3025-%';
DELETE FROM public.oauth_codes          WHERE code_hash LIKE '3025-%';
DELETE FROM public.oauth_clients        WHERE id LIKE '3025-%';
DELETE FROM public.organizations        WHERE id LIKE '3025-%';
