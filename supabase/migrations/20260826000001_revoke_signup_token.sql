-- Migration 20260826000001: user-facing signup-token revocation (issue #1715)
--
-- The #1709 agent-signup recovery token (agent_signup_tokens.revoked_at) had
-- only a support-runbook revocation path (audited SQL). This migration adds
-- the service_role RPC behind the user-facing POST /v1/agent/token/revoke
-- endpoint: a claimed user (dashboard session) or CLI user (key auth) revokes
-- their own team's token by plaintext → the token can no longer recover keys
-- (token-present signup/recover → uniform 422 invalid_signup_token).
--
-- Design contract:
--   * SECURITY DEFINER + SET search_path = '' + service_role ONLY — the SAME
--     grant hygiene as 20260814000001 (an anon-executable revoke would be a
--     cross-team token-kill primitive; an anon-executable wrapper an
--     unauthenticated revoke).
--   * Team-scoped + idempotent IN SQL: UPDATE ... WHERE token_hash AND
--     team_id AND revoked_at IS NULL. An unknown token, another team's token,
--     or an already-revoked token is a zero-row NO-OP (no RAISE) — the RPC's
--     WHERE is the authoritative team-scope guard, so a caller can never
--     revoke another team's token even with a wrong pre-read. The endpoint
--     maps 404/403/already from its pre-read (signup_token_row); the RPC
--     itself stays silent on all no-ops.
--   * RETURNS void (like provision_team_with_token): PostgREST return=minimal
--     does not echo volatile SECURITY DEFINER results — the wrapper reads the
--     row back through the control plane (repo precedent, resolve_signup_token).

-- ============================================================================
-- 1) revoke_signup_token — the user-facing revocation RPC
-- ============================================================================
CREATE OR REPLACE FUNCTION public.revoke_signup_token(
    p_token_hash text,
    p_team_id    text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    UPDATE public.agent_signup_tokens
       SET revoked_at = now()
     WHERE token_hash = p_token_hash
       AND team_id = p_team_id
       AND revoked_at IS NULL;
END;
$$;

-- ============================================================================
-- 2) Grant hygiene — service_role ONLY (see header comment)
-- ============================================================================
REVOKE ALL ON FUNCTION public.revoke_signup_token FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.revoke_signup_token TO service_role;
