-- ============================================================================
-- SQL-level verification for migration 20261012000006 (issue #4550)
-- `recover_team_key`: the cap count AND the revoke-target scan exclude
-- EXPIRED-but-unrevoked rows, matching `public.api_key_slot_count`, every
-- Python twin and the registry `sdk.signup_token_recover`.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--
-- Every assertion RAISEs on failure; with ON_ERROR_STOP=1 any failure exits
-- non-zero. Test rows use the "-4550" suffix for safe cleanup.
--
-- HONEST LIMIT: PGlite is SINGLE-CONNECTION, so this suite proves the RPC's
-- FUNCTIONAL predicate semantics, not contention. The org/token row locks are
-- unchanged by this migration and are already covered by
-- 20260927000001_api_key_mint_serialization.sql §2.
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

-- ── Cleanup any prior test rows (idempotent re-runs) ────────────────────────
DELETE FROM public.api_keys WHERE org_id LIKE '%-4550%';
DELETE FROM public.agent_signup_tokens WHERE org_id LIKE '%-4550%';
DELETE FROM public.org_memberships WHERE org_id LIKE '%-4550%';
DELETE FROM public.organizations WHERE id LIKE '%-4550%';

-- ============================================================================
-- SECTION 1 — grant hygiene + exactly ONE overload (a re-stated function must
-- not leave a stale signature behind), unchanged by this migration.
-- ============================================================================
DO $$ BEGIN
  PERFORM tests.assert(
    NOT has_function_privilege('anon', 'public.recover_team_key(text,text,text,text,integer)', 'EXECUTE'),
    'anon must NOT EXECUTE recover_team_key');
  PERFORM tests.assert(
    NOT has_function_privilege('authenticated', 'public.recover_team_key(text,text,text,text,integer)', 'EXECUTE'),
    'authenticated must NOT EXECUTE recover_team_key');
  PERFORM tests.assert(
    has_function_privilege('service_role', 'public.recover_team_key(text,text,text,text,integer)', 'EXECUTE'),
    'service_role must EXECUTE recover_team_key');
  PERFORM tests.assert(
    (SELECT count(*) FROM pg_proc
      WHERE proname = 'recover_team_key'
        AND pronamespace = 'public'::regnamespace) = 1,
    'recover_team_key must have exactly ONE overload');
END $$;

-- ============================================================================
-- SECTION 2 — THE HARM CASE (#4550). cap=2; ONE live durable key (never used)
-- plus two EXPIRED-but-unrevoked durables that were used recently. The
-- expiry-blind count read 2 live-ish rows + 2 expired + the minted row = 4 > 2,
-- and the never-used-first victim scan then revoked the LIVE key as collateral
-- while both expired rows survived.
-- ============================================================================
DO $$ DECLARE
  v_org   text := 'org-4550-harm';
  v_token text := 'aa' || repeat('cd', 31);
  v_now   timestamptz := now();
  v_count integer;
BEGIN
  INSERT INTO public.organizations (id, name, graph_name)
  VALUES (v_org, 'Harm 4550', 'org_org-4550-harm');
  INSERT INTO public.agent_signup_tokens (token_hash, org_id)
  VALUES (v_token, v_org);
  INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix,
                               created_via, created_by, created_at,
                               last_used_at, expires_at)
  VALUES ('k4550-live', v_org, 'lu4550-live', 'tt_4550live',
          'recovery', 'st_seed', v_now - interval '10 days', NULL, NULL),
         ('k4550-exp1', v_org, 'lu4550-exp1', 'tt_4550exp1',
          'recovery', 'st_seed', v_now - interval '10 days',
          v_now - interval '5 minutes', v_now - interval '2 days'),
         ('k4550-exp2', v_org, 'lu4550-exp2', 'tt_4550exp2',
          'recovery', 'st_seed', v_now - interval '10 days',
          v_now - interval '5 minutes', v_now - interval '2 days');

  -- Predicate parity BEFORE the mint: the ONE SQL declaration already
  -- excludes the expired rows (this is the number the RPC must agree with).
  PERFORM tests.assert(
    public.api_key_slot_count(v_org, v_now) = 1,
    'api_key_slot_count excludes expired-but-unrevoked rows (#4550 parity)');

  PERFORM public.recover_team_key(v_token, v_org, 'lu4550-rec-a', 'tt_4550a', 2);

  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE id = 'k4550-live'),
    '#4550: the LIVE key is NOT revoked as cap collateral for expired rows');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE id = 'k4550-exp1'),
    '#4550: an expired row is never the revoke target');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE id = 'k4550-exp2'),
    '#4550: an expired row is never the revoke target');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys
      WHERE lookup_hash = 'lu4550-rec-a'),
    '#4550: the minted recovery row landed and is live');
  -- 1 live durable + the minted row = 2 = cap: no revoke was owed at all, and
  -- the org is NOT read as over-cap. (Pre-#4550 the same row-set read as 4.)
  SELECT public.api_key_slot_count(v_org, v_now) INTO v_count;
  PERFORM tests.assert(
    v_count = 2,
    '#4550: the org sits AT cap (1 live + minted) — the expired rows charge nothing');
END $$;

-- ============================================================================
-- SECTION 3 — NON-REGRESSION: the cap revoke still fires when every counted
-- row is genuinely LIVE, and the #1872 LRU ordering survives the re-state.
-- cap=2; two live durable keys (one never used, created FIRST) + two expired
-- never-used rows created EARLIER than both. Post-#4550 the expired rows are
-- invisible to the scan, so the victim is the never-used LIVE key and the org
-- converges to cap. Pre-#4550 the scan picked an expired row (earliest
-- created_at), revoked it, freed no slot and left the org at 3 live.
-- ============================================================================
DO $$ DECLARE
  v_org   text := 'org-4550-parity';
  v_token text := 'bb' || repeat('cd', 31);
  v_now   timestamptz := now();
  v_count integer;
BEGIN
  INSERT INTO public.organizations (id, name, graph_name)
  VALUES (v_org, 'Parity 4550', 'org_org-4550-parity');
  INSERT INTO public.agent_signup_tokens (token_hash, org_id)
  VALUES (v_token, v_org);
  INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix,
                               created_via, created_by, created_at,
                               last_used_at, expires_at)
  VALUES ('k4550-exp-old1', v_org, 'lu4550-exp-old1', 'tt_4550eo1',
          'recovery', 'st_seed', v_now - interval '5 days', NULL,
          v_now - interval '4 days'),
         ('k4550-exp-old2', v_org, 'lu4550-exp-old2', 'tt_4550eo2',
          'recovery', 'st_seed', v_now - interval '5 days', NULL,
          v_now - interval '4 days'),
         ('k4550-live-never', v_org, 'lu4550-live-never', 'tt_4550ln',
          'recovery', 'st_seed', v_now - interval '2 days', NULL, NULL),
         ('k4550-live-used', v_org, 'lu4550-live-used', 'tt_4550lu',
          'recovery', 'st_seed', v_now - interval '3 days',
          v_now - interval '1 hour', NULL);

  PERFORM tests.assert(
    public.api_key_slot_count(v_org, v_now) = 2, 'the two live rows sit AT cap=2');

  PERFORM public.recover_team_key(v_token, v_org, 'lu4550-rec-b', 'tt_4550b', 2);

  PERFORM tests.assert(
    (SELECT revoked_at IS NOT NULL FROM public.api_keys
      WHERE id = 'k4550-live-never'),
    '#4550: over cap, the never-used LIVE key is the LRU victim (#1872 preserved)');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE id = 'k4550-live-used'),
    '#1872: a recently-used credential is NOT the rotation victim');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE id = 'k4550-exp-old1'),
    '#4550: the victim scan skips expired rows — picking one frees no slot');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE id = 'k4550-exp-old2'),
    '#4550: the victim scan skips expired rows — picking one frees no slot');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE lookup_hash = 'lu4550-rec-b'),
    '#1872: the row this call minted is never its own collateral');
  -- ⛔ THE PARITY ASSERTION: count and scan carry the SAME predicate, so the
  -- over-cap check is self-healing — the org lands exactly on the cap.
  SELECT public.api_key_slot_count(v_org, v_now) INTO v_count;
  PERFORM tests.assert(
    v_count = 2,
    '#4550: count and victim scan share one predicate — the cap still holds (self-healing)');
END $$;

-- ============================================================================
-- SECTION 4 — a NO-OP retry (same lookup_hash) still revokes nothing, and the
-- expiry arm did not narrow the COUNT away from the minted row.
-- ============================================================================
DO $$ DECLARE
  v_org   text := 'org-4550-retry';
  v_token text := 'cc' || repeat('cd', 31);
  v_now   timestamptz := now();
BEGIN
  INSERT INTO public.organizations (id, name, graph_name)
  VALUES (v_org, 'Retry 4550', 'org_org-4550-retry');
  INSERT INTO public.agent_signup_tokens (token_hash, org_id)
  VALUES (v_token, v_org);
  INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix,
                               created_via, created_by, created_at,
                               last_used_at, expires_at)
  VALUES ('k4550-ret-live', v_org, 'lu4550-ret-live', 'tt_4550rl',
          'recovery', 'st_seed', v_now - interval '3 days', NULL, NULL),
         ('k4550-ret-exp', v_org, 'lu4550-ret-exp', 'tt_4550re',
          'recovery', 'st_seed', v_now - interval '2 days',
          v_now - interval '1 day', v_now - interval '1 day');

  -- First recovery at cap=2: 1 live + minted = 2 → no revoke.
  PERFORM public.recover_team_key(v_token, v_org, 'lu4550-ret-a', 'tt_4550r1', 2);
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE id = 'k4550-ret-live'),
    '#4550: under cap, the live key is untouched');
  -- Retry with the SAME lookup_hash = ON CONFLICT no-op: v_inserted_id IS NULL,
  -- so the whole cap section is skipped (an idempotent retry must never revoke).
  PERFORM public.recover_team_key(v_token, v_org, 'lu4550-ret-a', 'tt_4550r1', 2);
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE id = 'k4550-ret-live'),
    'a no-op retry never revokes a live key');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE lookup_hash = 'lu4550-ret-a'),
    'a no-op retry leaves the previously minted row live');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys
      WHERE org_id = v_org AND lookup_hash = 'lu4550-ret-a') = 1,
    'ON CONFLICT (lookup_hash) DO NOTHING still mints exactly once');
END $$;

-- ── Final cleanup (idempotent re-runs) ─────────────────────────────────────
DELETE FROM public.api_keys WHERE org_id LIKE '%-4550%';
DELETE FROM public.agent_signup_tokens WHERE org_id LIKE '%-4550%';
DELETE FROM public.org_memberships WHERE org_id LIKE '%-4550%';
DELETE FROM public.organizations WHERE id LIKE '%-4550%';
