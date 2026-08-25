-- Migration 20260814000001: agent signup tokens + recovery RPCs (issue #1709)
--
-- Approach C (server-issued capability token): POST /v1/agent/signup mints a
-- fresh team + tt_ key AND a 256-bit `st_<64hex>` signup token (hash-only at
-- rest). Re-presenting the token IS the dedupe check AND the keyless-recovery
-- credential: there is no unauthenticated dedupe path (no-token always mints,
-- bad token → uniform 422), so no existence oracle, no lockout primitive, and
-- no TOCTOU (the token path never creates a team; recovery mints a NEW key on
-- the SAME team).
--
-- Design contract (scope.md §1):
--   * NEW-named `provision_team_with_token` wrapper — NOT CREATE OR REPLACE on
--     provision_team with a trailing param (that creates a second OVERLOAD on
--     PG16, leaving the old 15-arg function live and making old-arity
--     PostgREST calls ambiguous; the new overload would also inherit
--     Supabase's default function ACL = an unauthenticated mint primitive).
--     provision_team stays untouched at 15 args; ONLY the signup path calls
--     the wrapper. One transaction: provision + token insert are atomic (a
--     failed provision rolls back the token row — no orphan token).
--   * token_hash = SHA-256(PEPPER_BYTES + token), computed in tortoise/auth.py
--     via lookup_hash() (byte-identical construction, domain separation by the
--     st_ prefix) — the pepper NEVER lives in SQL.
--   * `recover_team_key` SELECTs the token row FOR UPDATE — READ COMMITTED
--     check-then-insert race closed: concurrent recoveries for one token are
--     serialized so the non-bootstrap key count cannot overshoot the cap.
--     The cap is a CALLER-SUPPLIED param (p_max_api_keys, tier-derived from
--     tortoise/pricing.py — teams has no max_api_keys column in 0006, and
--     pricing lives in app code, never SQL; mirrors how provision_team
--     receives free-tier limits as params).
--   * All three RPCs are SECURITY DEFINER, SET search_path = '', service_role
--     ONLY (Supabase's ALTER DEFAULT PRIVILEGES grants EXECUTE to
--     anon/authenticated — an anon-executable resolve_signup_token is a
--     token-existence oracle; an anon-executable wrapper is an unauthenticated
--     mint). Mirror 0010:185-186 grant hygiene.

-- ============================================================================
-- 1) agent_signup_tokens table (hash-only; one live token per team)
-- ============================================================================
CREATE TABLE public.agent_signup_tokens (
    token_hash   text PRIMARY KEY,          -- SHA-256(PEPPER + token) — app-side only
    team_id      text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    last_used_at timestamptz,
    revoked_at   timestamptz
);

-- One-token-per-team (the issue's "one-team-per-identity" reframed under C):
-- a team has at most ONE live (unrevoked) token. Revoking frees the slot.
CREATE UNIQUE INDEX uq_agent_signup_tokens_team
    ON public.agent_signup_tokens (team_id) WHERE revoked_at IS NULL;

REVOKE ALL ON public.agent_signup_tokens FROM public, anon, authenticated;
GRANT SELECT, INSERT, UPDATE ON public.agent_signup_tokens TO service_role;

-- ============================================================================
-- 2) provision_team_with_token — the signup mint wrapper (NEW-named, NO overload)
-- ============================================================================
CREATE OR REPLACE FUNCTION public.provision_team_with_token(
    p_user_id     uuid,
    p_identity    text,
    p_team_id     text,
    p_team_name   text,
    p_api_key     text,
    p_key_hash    text,
    p_lookup_hash text,
    p_graph_name  text,
    p_email       text DEFAULT NULL,
    p_key_prefix  text DEFAULT NULL,
    p_tier           text    DEFAULT 'free',
    p_max_users      integer DEFAULT 1,
    p_max_graphs     integer DEFAULT 1,
    p_ops_allowance  integer DEFAULT 10000,
    p_graph_size_cap bigint  DEFAULT 10000,
    p_signup_token_hash text DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- NAMED-ARG notation: immune to param reordering (all 15, verified in
    -- 0010). provision_team RAISEs on every failure mode (RETURNS void, no
    -- internal EXCEPTION handler) → the token INSERT below never runs and the
    -- whole mint rolls back (1 team + 1 token atomic).
    PERFORM public.provision_team(
        p_user_id => p_user_id,
        p_identity => p_identity,
        p_team_id => p_team_id,
        p_team_name => p_team_name,
        p_api_key => p_api_key,
        p_key_hash => p_key_hash,
        p_lookup_hash => p_lookup_hash,
        p_graph_name => p_graph_name,
        p_email => p_email,
        p_key_prefix => p_key_prefix,
        p_tier => p_tier,
        p_max_users => p_max_users,
        p_max_graphs => p_max_graphs,
        p_ops_allowance => p_ops_allowance,
        p_graph_size_cap => p_graph_size_cap
    );
    IF p_signup_token_hash IS NOT NULL THEN
        -- One live token per team is enforced by uq_agent_signup_tokens_team;
        -- a token_hash collision (the same token presented twice) is a no-op.
        INSERT INTO public.agent_signup_tokens (token_hash, team_id)
        VALUES (p_signup_token_hash, p_team_id)
        ON CONFLICT (token_hash) DO NOTHING;
    END IF;
END;
$$;

-- ============================================================================
-- 3) resolve_signup_token — token → team_id (revocation-aware)
-- ============================================================================
CREATE OR REPLACE FUNCTION public.resolve_signup_token(p_token_hash text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_team_id text;
BEGIN
    SELECT team_id INTO v_team_id
      FROM public.agent_signup_tokens
     WHERE token_hash = p_token_hash AND revoked_at IS NULL;
    -- Single tx: token-verify + last_used_at touch are atomic; the caller
    -- checks team suspended/deleted state after.
    UPDATE public.agent_signup_tokens
       SET last_used_at = now()
     WHERE token_hash = p_token_hash;
    RETURN v_team_id;  -- NULL = unknown or revoked (caller maps to uniform 422)
END;
$$;

-- ============================================================================
-- 4) recover_team_key — keyless recovery mint (FOR UPDATE serialized)
-- ============================================================================
CREATE OR REPLACE FUNCTION public.recover_team_key(
    p_token_hash    text,
    p_team_id       text,
    p_api_key       text,
    p_key_hash      text,
    p_lookup_hash   text,
    p_key_prefix    text,
    p_max_api_keys  integer DEFAULT 2   -- tier-derived cap (caller; free = 2)
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_team_id     text;
    v_count       integer;
    v_oldest_id   text;
BEGIN
    -- ⛔ Row lock: serializes concurrent recoveries for the SAME token
    -- (READ COMMITTED check-then-insert race closed). Zero rows → fail closed
    -- (revoke race or team mismatch) — NEVER mint on a zero-row lock.
    SELECT team_id INTO v_team_id
      FROM public.agent_signup_tokens
     WHERE token_hash = p_token_hash
       AND revoked_at IS NULL
       AND team_id = p_team_id
     FOR UPDATE;
    IF v_team_id IS NULL THEN
        RAISE EXCEPTION 'recover_team_key: token not found or revoked';
    END IF;

    -- Soft-deleted teams are unrecoverable (caller maps to the uniform 422 —
    -- indistinguishable from never-existed). Suspension is a CALLER-side 403
    -- (possession-authenticated) — the RPC has no suspension authority.
    IF EXISTS (SELECT 1 FROM public.teams
                WHERE id = p_team_id AND deleted_at IS NOT NULL) THEN
        RAISE EXCEPTION 'recover_team_key: team deleted';
    END IF;

    -- Cap: count ACTIVE non-bootstrap keys (a bootstrap-only team is always
    -- under cap → the 402 branch is unreachable on the token path by design).
    SELECT count(*) INTO v_count
      FROM public.api_keys
     WHERE team_id = p_team_id AND revoked_at IS NULL
       AND created_via <> 'bootstrap';
    IF v_count >= p_max_api_keys THEN
        -- Deterministic revoke-oldest-non-bootstrap (mirrors #750.10
        -- revoke-oldest-other; the token path has no "presenter's own").
        SELECT id INTO v_oldest_id
          FROM public.api_keys
         WHERE team_id = p_team_id AND revoked_at IS NULL
           AND created_via <> 'bootstrap'
         ORDER BY created_at ASC NULLS FIRST
         LIMIT 1;
        IF v_oldest_id IS NOT NULL THEN
            UPDATE public.api_keys SET revoked_at = now() WHERE id = v_oldest_id;
        END IF;
    END IF;

    -- Recovery mint: created_via='recovery' (0007 CHECK admits it); created_by
    -- is DERIVED inside the RPC ('st_' + token-hash prefix) — never
    -- caller-supplied; it identifies the TOKEN, not a human.
    INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix,
                                 created_via, created_by)
    VALUES ('key_' || p_team_id || '_' || left(p_lookup_hash, 12),
            p_team_id, p_lookup_hash, p_key_prefix,
            'recovery', 'st_' || left(p_token_hash, 12))
    ON CONFLICT (lookup_hash) DO NOTHING;

    RETURN p_team_id;
END;
$$;

-- ============================================================================
-- 5) Grant hygiene — service_role ONLY for all three (see header comment)
-- ============================================================================
REVOKE ALL ON FUNCTION public.provision_team_with_token FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.provision_team_with_token TO service_role;

REVOKE ALL ON FUNCTION public.resolve_signup_token FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.resolve_signup_token TO service_role;

REVOKE ALL ON FUNCTION public.recover_team_key FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.recover_team_key TO service_role;
