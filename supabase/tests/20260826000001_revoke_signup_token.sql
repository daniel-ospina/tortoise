-- ============================================================================
-- SQL-level verification for migration 20260826000001 (issue #1715)
-- User-facing signup-token revocation: the revoke_signup_token RPC
-- (service_role-only, team-scoped, idempotent) + the post-revoke behavior
-- (resolve_signup_token → NULL → the app maps to the uniform 422).
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--
-- Every assertion RAISEs on failure; with ON_ERROR_STOP=1 any failure exits
-- non-zero. Test rows use the "-1715" suffix for safe cleanup.
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
DELETE FROM public.agent_signup_tokens WHERE team_id LIKE '%-1715';
DELETE FROM public.api_keys WHERE team_id LIKE '%-1715';
DELETE FROM public.team_memberships WHERE team_id LIKE '%-1715';
DELETE FROM public.teams WHERE id LIKE '%-1715';

-- ============================================================================
-- SECTION 1 — grant hygiene (service_role-only; anon/authenticated DENIED)
-- ============================================================================
DO $$ BEGIN
  PERFORM tests.assert(
    NOT has_function_privilege('anon', 'public.revoke_signup_token(text,text)', 'EXECUTE'),
    'anon must NOT EXECUTE revoke_signup_token');
  PERFORM tests.assert(
    NOT has_function_privilege('authenticated', 'public.revoke_signup_token(text,text)', 'EXECUTE'),
    'authenticated must NOT EXECUTE revoke_signup_token');
  PERFORM tests.assert(
    has_function_privilege('service_role', 'public.revoke_signup_token(text,text)', 'EXECUTE'),
    'service_role must EXECUTE revoke_signup_token');
  PERFORM tests.assert(
    (SELECT count(*) FROM pg_proc p
      JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'revoke_signup_token') = 1,
    'revoke_signup_token must be a single 2-arg function');
END $$;

-- ============================================================================
-- SECTION 2 — revoke semantics: team-scoped + idempotent + revocation-aware
-- ============================================================================
DO $$ DECLARE
  v_token_a text := 'a1' || repeat('cd', 31);  -- live token for team-a
  v_token_b text := 'b2' || repeat('cd', 31);  -- live token for team-b
BEGIN
  -- Seed two teams with one live token each (mirrors the minted shape).
  INSERT INTO public.teams (id, name, graph_name)
  VALUES ('team-1715-a', 'Agent 1715 A', 'team_team-1715-a'),
         ('team-1715-b', 'Agent 1715 B', 'team_team-1715-b');
  INSERT INTO public.agent_signup_tokens (token_hash, team_id)
  VALUES (v_token_a, 'team-1715-a'), (v_token_b, 'team-1715-b');

  -- Live revoke (matching team): sets revoked_at; the caller's team wins.
  PERFORM public.revoke_signup_token(v_token_a, 'team-1715-a');
  PERFORM tests.assert(
    (SELECT revoked_at IS NOT NULL FROM public.agent_signup_tokens
      WHERE token_hash = v_token_a),
    'revoke_signup_token must set revoked_at for the matching team');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.agent_signup_tokens
      WHERE token_hash = v_token_b),
    'revoke must not touch another team''s token');

  -- ⛔ Team-scope guard: a caller for team-a cannot revoke team-b's token
  -- (zero-row no-op — the RPC never raises on a no-op).
  PERFORM public.revoke_signup_token(v_token_b, 'team-1715-a');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.agent_signup_tokens
      WHERE token_hash = v_token_b),
    'revoke with the WRONG team must be a no-op (team-scoped)');

  -- Unknown token → zero-row no-op (no error, nothing written).
  PERFORM public.revoke_signup_token('ff' || repeat('cd', 31), 'team-1715-a');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.agent_signup_tokens
      WHERE token_hash = 'ff' || repeat('cd', 31)) = 0,
    'revoke of an unknown token must be a zero-row no-op');

  -- Idempotent: revoking the already-revoked token is a no-op (no error).
  PERFORM public.revoke_signup_token(v_token_a, 'team-1715-a');
  PERFORM tests.assert(true, 'revoking an already-revoked token must not raise');

  -- Post-revoke behavior: resolve_signup_token returns NULL for the revoked
  -- token (the app maps NULL → the uniform 422 invalid_signup_token) while
  -- the other team''s live token still resolves.
  PERFORM tests.assert(
    public.resolve_signup_token(v_token_a) IS NULL,
    'a revoked token must resolve to NULL (uniform 422 downstream)');
  PERFORM tests.assert(
    public.resolve_signup_token(v_token_b) = 'team-1715-b',
    'an unrevoked token still resolves to its team');
END $$;

-- ── Final cleanup (idempotent re-runs) ─────────────────────────────────────
DELETE FROM public.agent_signup_tokens WHERE team_id LIKE '%-1715';
DELETE FROM public.api_keys WHERE team_id LIKE '%-1715';
DELETE FROM public.team_memberships WHERE team_id LIKE '%-1715';
DELETE FROM public.teams WHERE id LIKE '%-1715';
