-- ============================================================================
-- SQL-level verification for migration 20260814000001 (issue #1709)
-- Agent signup tokens: table + provision_team_with_token / resolve_signup_token
-- / recover_team_key RPCs (service_role-only, public.-qualified).
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--
-- Every assertion RAISEs on failure; with ON_ERROR_STOP=1 any failure exits
-- non-zero. Test rows use the "-1709" suffix for safe cleanup.
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
DELETE FROM public.agent_signup_tokens WHERE team_id LIKE '%-1709';
DELETE FROM public.api_keys WHERE team_id LIKE '%-1709';
DELETE FROM public.team_memberships WHERE team_id LIKE '%-1709';
DELETE FROM public.teams WHERE id LIKE '%-1709';

-- ============================================================================
-- SECTION 1 — grant hygiene (service_role-only; anon/authenticated DENIED)
-- ============================================================================
DO $$ BEGIN
  -- Table: no privileges for public/anon/authenticated.
  PERFORM tests.assert(
    NOT has_table_privilege('anon', 'public.agent_signup_tokens', 'SELECT'),
    'anon must NOT SELECT agent_signup_tokens');
  PERFORM tests.assert(
    NOT has_table_privilege('anon', 'public.agent_signup_tokens', 'INSERT'),
    'anon must NOT INSERT agent_signup_tokens');
  PERFORM tests.assert(
    NOT has_table_privilege('authenticated', 'public.agent_signup_tokens', 'SELECT'),
    'authenticated must NOT SELECT agent_signup_tokens');
  PERFORM tests.assert(
    has_table_privilege('service_role', 'public.agent_signup_tokens', 'SELECT'),
    'service_role must SELECT agent_signup_tokens');
  PERFORM tests.assert(
    has_table_privilege('service_role', 'public.agent_signup_tokens', 'INSERT'),
    'service_role must INSERT agent_signup_tokens');
  PERFORM tests.assert(
    has_table_privilege('service_role', 'public.agent_signup_tokens', 'UPDATE'),
    'service_role must UPDATE agent_signup_tokens');

  -- Functions: EXECUTE denied to anon/authenticated, granted to service_role.
  -- (Supabase's ALTER DEFAULT PRIVILEGES in the harness grants EXECUTE to
  -- anon/authenticated — this asserts the migration's explicit REVOKEs.)
  PERFORM tests.assert(
    NOT has_function_privilege('anon', 'public.provision_team_with_token(uuid,text,text,text,text,text,text,text,text,text,text,integer,integer,integer,bigint,text)', 'EXECUTE'),
    'anon must NOT EXECUTE provision_team_with_token');
  PERFORM tests.assert(
    NOT has_function_privilege('authenticated', 'public.provision_team_with_token(uuid,text,text,text,text,text,text,text,text,text,text,integer,integer,integer,bigint,text)', 'EXECUTE'),
    'authenticated must NOT EXECUTE provision_team_with_token');
  PERFORM tests.assert(
    has_function_privilege('service_role', 'public.provision_team_with_token(uuid,text,text,text,text,text,text,text,text,text,text,integer,integer,integer,bigint,text)', 'EXECUTE'),
    'service_role must EXECUTE provision_team_with_token');
  PERFORM tests.assert(
    NOT has_function_privilege('anon', 'public.resolve_signup_token(text)', 'EXECUTE'),
    'anon must NOT EXECUTE resolve_signup_token');
  PERFORM tests.assert(
    NOT has_function_privilege('authenticated', 'public.resolve_signup_token(text)', 'EXECUTE'),
    'authenticated must NOT EXECUTE resolve_signup_token');
  PERFORM tests.assert(
    has_function_privilege('service_role', 'public.resolve_signup_token(text)', 'EXECUTE'),
    'service_role must EXECUTE resolve_signup_token');
  PERFORM tests.assert(
    NOT has_function_privilege('anon', 'public.recover_team_key(text,text,text,text,integer)', 'EXECUTE'),
    'anon must NOT EXECUTE recover_team_key');
  PERFORM tests.assert(
    NOT has_function_privilege('authenticated', 'public.recover_team_key(text,text,text,text,integer)', 'EXECUTE'),
    'authenticated must NOT EXECUTE recover_team_key');
  PERFORM tests.assert(
    has_function_privilege('service_role', 'public.recover_team_key(text,text,text,text,integer)', 'EXECUTE'),
    'service_role must EXECUTE recover_team_key');

  -- provision_team stays a SINGLE 15-arg function (no overload created).
  PERFORM tests.assert(
    (SELECT count(*) FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'provision_team') = 1,
    'provision_team must have exactly ONE overload (the 15-arg original)');
END $$;

-- ============================================================================
-- SECTION 2 — provision_team_with_token: mint + token insert atomic
-- ============================================================================
DO $$ DECLARE
  v_token_hash text := 'ab' || repeat('cd', 31);  -- 64-hex st_ token hash
  v_err text := NULL;
BEGIN
  -- Success path: team + membership + key + token row land in one call.
  PERFORM public.provision_team_with_token(
    p_user_id => NULL,
    p_identity => 'anon-1709mint',
    p_team_id => 'team-1709-mint-a',
    p_team_name => 'Agent 1709 A',
    p_api_key => 'tt_1709mintkey',
    p_key_hash => 'pbkdf2-stub-1709',
    p_lookup_hash => 'lookup-1709-a',
    p_graph_name => 'team_team-1709-mint-a',
    p_key_prefix => 'tt_1709min',
    p_tier => 'free',
    p_max_users => 1,
    p_max_graphs => 1,
    p_ops_allowance => 10000,
    p_graph_size_cap => 10000,
    p_signup_token_hash => v_token_hash
  );
  PERFORM tests.assert(
    (SELECT count(*) FROM public.teams WHERE id = 'team-1709-mint-a') = 1,
    'provision_team_with_token must create the team');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships WHERE team_id = 'team-1709-mint-a') = 1,
    'provision_team_with_token must create the membership');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE team_id = 'team-1709-mint-a') = 1,
    'provision_team_with_token must create the api key');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.agent_signup_tokens WHERE token_hash = v_token_hash) = 1,
    'provision_team_with_token must insert the token row');
  PERFORM tests.assert(
    (SELECT team_id FROM public.agent_signup_tokens WHERE token_hash = v_token_hash) = 'team-1709-mint-a',
    'token row must bind to the minted team');

  -- ⛔ Atomicity contract: a FAILED provision leaves NO token row behind.
  -- The rejection must be GENUINE — provision_team's guard is asymmetric:
  -- p_api_key checks IS NULL ONLY ('' passes — the old p_api_key => ''
  -- "failure" actually SUCCEEDED and the assert-false EXCEPTION-handler
  -- savepoint made the assertions vacuous). The structure here is also
  -- non-vacuous: the rejection is captured into v_err (a swallowed
  -- provision would leave v_err NULL → the first assert FAILS loudly)
  -- and the wrapper must propagate provision_team's OWN guard error.
  BEGIN
    PERFORM public.provision_team_with_token(
      p_user_id => NULL,
      p_identity => 'anon-1709b',
      p_team_id => NULL,               -- genuine rejection (guard: IS NULL)
      p_team_name => 'Agent 1709 B',
      p_api_key => 'tt_1709mintkeyB',
      p_key_hash => 'pbkdf2-stub-1709',
      p_lookup_hash => 'lookup-1709-b',
      p_graph_name => 'team_team-1709-mint-b',
      p_signup_token_hash => 'bb' || repeat('cd', 31)
    );
  EXCEPTION WHEN OTHERS THEN
    v_err := SQLERRM;
  END;
  PERFORM tests.assert(v_err IS NOT NULL,
    'provision_team_with_token must raise on a failed provision');
  PERFORM tests.assert(v_err LIKE 'provision_team: required parameters missing%',
    'the raised error must be provision_team''s guard rejection (not swallowed)');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.agent_signup_tokens
      WHERE token_hash = 'bb' || repeat('cd', 31)) = 0,
    'a failed provision must roll back the token insert (no orphan token)');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.teams WHERE id = 'team-1709-mint-b') = 0,
    'a failed provision must roll back the team insert');

  -- One-token-per-team: a second (different) token for the same team is
  -- rejected by uq_agent_signup_tokens_team.
  BEGIN
    PERFORM public.provision_team_with_token(
      p_user_id => NULL,
      p_identity => 'anon-1709c',
      p_team_id => 'team-1709-mint-a',
      p_team_name => 'Agent 1709 A',
      p_api_key => 'tt_1709mintkey2',
      p_key_hash => 'pbkdf2-stub-1709',
      p_lookup_hash => 'lookup-1709-a2',
      p_graph_name => 'team_team-1709-mint-a',
      p_signup_token_hash => 'cc' || repeat('cd', 31)
    );
    PERFORM tests.assert(false, 'second live token for one team must be rejected');
  EXCEPTION WHEN unique_violation THEN
    PERFORM tests.assert(true, 'uq_agent_signup_tokens_team rejects a second live token');
  END;
END $$;

-- ============================================================================
-- SECTION 3 — resolve_signup_token semantics
-- ============================================================================
DO $$ DECLARE
  v_token_hash text := 'ab' || repeat('cd', 31);
BEGIN
  PERFORM tests.assert(
    public.resolve_signup_token(v_token_hash) = 'team-1709-mint-a',
    'resolve_signup_token returns the bound team_id');
  PERFORM tests.assert(
    (SELECT last_used_at IS NOT NULL FROM public.agent_signup_tokens
      WHERE token_hash = v_token_hash),
    'resolve_signup_token touches last_used_at');
  PERFORM tests.assert(
    public.resolve_signup_token('ff' || repeat('cd', 31)) IS NULL,
    'unknown token resolves to NULL');
  -- Revoked token resolves to NULL.
  UPDATE public.agent_signup_tokens SET revoked_at = now()
   WHERE token_hash = v_token_hash;
  PERFORM tests.assert(
    public.resolve_signup_token(v_token_hash) IS NULL,
    'revoked token resolves to NULL');
  -- Re-open the slot (revoke is reversible for test setup only; the support
  -- runbook revokes permanently — this row is cleaned up below anyway).
  UPDATE public.agent_signup_tokens SET revoked_at = NULL
   WHERE token_hash = v_token_hash;
END $$;

-- ============================================================================
-- SECTION 4 — recover_team_key: cap + revoke-oldest-non-bootstrap + lock
-- ============================================================================
DO $$ DECLARE
  v_token_hash text := 'ab' || repeat('cd', 31);
  v_team_id text := 'team-1709-mint-a';
  v_first_id text;
  v_count integer;
BEGIN
  -- Bootstrap key on the team (never counted against the non-bootstrap cap).
  INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix, created_via, created_by)
  VALUES ('key-bootstrap-1709', v_team_id, 'lookup-bootstrap-1709', 'tt_1709boot', 'bootstrap', 'st_' || left(v_token_hash, 12));

  -- Under cap (0 non-bootstrap): recovery mints, returns team_id.
  PERFORM tests.assert(
    public.recover_team_key(v_token_hash, v_team_id, 'lu1709aaaaaaaa', 'tt_1709rec', 2) = v_team_id,
    'recover_team_key returns the team_id');
  SELECT id INTO v_first_id FROM public.api_keys
   WHERE team_id = v_team_id AND created_via = 'recovery' AND revoked_at IS NULL;
  PERFORM tests.assert(
    v_first_id = 'key_' || v_team_id || '_' || left('lu1709aaaaaaaa', 12),
    'recovery key uses the deterministic id');
  PERFORM tests.assert(
    (SELECT created_by FROM public.api_keys WHERE id = v_first_id) = 'st_' || left(v_token_hash, 12),
    'recovery key created_by derives from the token hash (not caller-supplied)');
  PERFORM tests.assert(
    (SELECT created_via FROM public.api_keys WHERE id = v_first_id) = 'recovery',
    'recovery key created_via = recovery');
  -- Bootstrap key untouched.
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE id = 'key-bootstrap-1709'),
    'bootstrap key is never revoked by recovery');

  -- Second recovery: 1 active non-bootstrap key before the mint; cap=2 →
  -- INSERT first, re-count AFTER (the new key is active) → 2 ≤ cap → no
  -- revoke (the cap revoke fires only when a row was genuinely inserted —
  -- a no-op retry with the same lookup_hash must never revoke a live key).
  PERFORM tests.assert(
    public.recover_team_key(v_token_hash, v_team_id, 'lu1709bbbbbbbb', 'tt_1709rec', 2) = v_team_id,
    'second recovery under cap mints');
  SELECT count(*) INTO v_count FROM public.api_keys
   WHERE team_id = v_team_id AND revoked_at IS NULL
     AND (created_via IS NULL OR created_via <> 'bootstrap');
  PERFORM tests.assert(v_count = 2, 'non-bootstrap active keys = 2 at cap');

  -- Third recovery: AT cap (2 non-bootstrap) → insert (r3) → count = 3 > cap
  -- → revoke the OLDEST non-bootstrap (lookup-1709-r1, created first) →
  -- still 2 active.
  PERFORM tests.assert(
    public.recover_team_key(v_token_hash, v_team_id, 'lu1709cccccccc', 'tt_1709rec', 2) = v_team_id,
    'third recovery at cap mints');
  PERFORM tests.assert(
    (SELECT revoked_at IS NOT NULL FROM public.api_keys WHERE lookup_hash = 'lu1709aaaaaaaa'),
    'at cap, the OLDEST non-bootstrap key is revoked');
  SELECT count(*) INTO v_count FROM public.api_keys
   WHERE team_id = v_team_id AND revoked_at IS NULL
     AND (created_via IS NULL OR created_via <> 'bootstrap');
  PERFORM tests.assert(v_count = 2, 'non-bootstrap active keys stay ≤ 2 (cap cannot overshoot)');

  -- Zero-row lock: a token/team mismatch (or revoked token) fails closed.
  BEGIN
    PERFORM public.recover_team_key('zz' || repeat('cd', 31), v_team_id, 'lu1709dddddddd', 'tt_1709rec');
    PERFORM tests.assert(false, 'recover_team_key must raise on an unknown token');
  EXCEPTION WHEN OTHERS THEN
    PERFORM tests.assert(true, 'recover_team_key fails closed on a zero-row lock');
  END;
  BEGIN
    PERFORM public.recover_team_key(v_token_hash, 'team-1709-other', 'lu1709eeeeeeee', 'tt_1709rec');
    PERFORM tests.assert(false, 'recover_team_key must raise on a team mismatch');
  EXCEPTION WHEN OTHERS THEN
    PERFORM tests.assert(true, 'recover_team_key fails closed on a team mismatch');
  END;

  -- Soft-deleted team → fail closed.
  INSERT INTO public.teams (id, name, graph_name, deleted_at)
  VALUES ('team-1709-del', 'Deleted 1709', 'team_team-1709-del', now());
  INSERT INTO public.agent_signup_tokens (token_hash, team_id)
  VALUES ('dd' || repeat('cd', 31), 'team-1709-del');
  BEGIN
    PERFORM public.recover_team_key('dd' || repeat('cd', 31), 'team-1709-del', 'lu1709ffffffff', 'tt_1709rec');
    PERFORM tests.assert(false, 'recover_team_key must raise on a soft-deleted team');
  EXCEPTION WHEN OTHERS THEN
    PERFORM tests.assert(true, 'recover_team_key fails closed on a soft-deleted team');
  END;
END $$;

-- ── Final cleanup (idempotent re-runs) ─────────────────────────────────────
DELETE FROM public.agent_signup_tokens WHERE team_id LIKE '%-1709';
DELETE FROM public.api_keys WHERE team_id LIKE '%-1709';
DELETE FROM public.team_memberships WHERE team_id LIKE '%-1709';
DELETE FROM public.teams WHERE id LIKE '%-1709';
