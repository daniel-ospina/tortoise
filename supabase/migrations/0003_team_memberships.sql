-- Migration 0003: user↔team decoupling (M:N) — product ontology foundation
-- Epic: 2026-08-07-tortoise-user-journeys · Issue: #568 (D1)
--
-- Replaces the 1:1 user_teams model with a many-to-many team_memberships
-- junction (per-team billing; multi-team is a USER capability, not a tier
-- feature). Adds role/status/invited_email for Team-tier collaboration,
-- column-level protection for the one-time-reveal api_key, and the
-- reveal_api_key SECURITY DEFINER RPC (A13 — key shown once, then nulled).

-- ============================================================================
-- 1) Rename + drop the 1:1 constraint
-- ============================================================================
ALTER TABLE public.user_teams RENAME TO team_memberships;
ALTER TABLE public.team_memberships DROP CONSTRAINT uq_user_teams_user;

-- ============================================================================
-- 2) New columns (M:N junction)
-- ============================================================================
ALTER TABLE public.team_memberships
  ADD COLUMN role          text NOT NULL DEFAULT 'owner',  -- owner | admin | member
  ADD COLUMN status        text NOT NULL DEFAULT 'active', -- active | invited | removed
  ADD COLUMN invited_email text,                            -- pre-signup invite target
  ADD CONSTRAINT uq_member_team UNIQUE (user_id, team_id),
  ADD CONSTRAINT chk_member_or_invite CHECK (user_id IS NOT NULL OR invited_email IS NOT NULL);

-- One ACTIVE invite per (team, email) — NULLs are distinct in unique
-- constraints, so without this duplicate invites to the same email are allowed.
CREATE UNIQUE INDEX IF NOT EXISTS uq_team_invite_email
  ON public.team_memberships (team_id, invited_email) WHERE status = 'invited';

-- ============================================================================
-- 3) Column-level api_key protection (P1 — RLS filters ROWS, not columns;
--    the row-owner SELECT could read api_key directly, bypassing reveal RPC)
-- ============================================================================
REVOKE SELECT (api_key) ON public.team_memberships FROM authenticated, anon;

-- ============================================================================
-- 4) RE-CREATE trigger functions (they reference the old table name — every
--    signup after the rename would break otherwise; P0-1)
-- ============================================================================
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- Placeholder row: team_id='' is the placeholder sentinel. Provisioning
    -- updates THIS row (WHERE user_id = X AND team_id = '') and flips team_id
    -- to the real value in the same upsert — no second row, no phantom
    -- membership (M:N placeholder semantics, plan §4.1 step 6).
    INSERT INTO public.team_memberships (
        user_id, team_id, team_name, key_hash, graph_name, role
    ) VALUES (
        NEW.id, '', 'provisioning...', 'pending', '', 'owner'
    )
    ON CONFLICT (user_id, team_id) DO NOTHING;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();

CREATE OR REPLACE FUNCTION public.update_user_team(
    p_user_id   uuid,
    p_team_id   text,
    p_team_name text,
    p_api_key   text,
    p_key_hash  text,
    p_graph_name text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- Update the placeholder row (team_id='') and flip to the real team_id
    -- in the same statement — guarantees exactly one membership row per
    -- provisioned user (plan §4.1 step 6).
    UPDATE public.team_memberships
    SET team_id    = p_team_id,
        team_name  = p_team_name,
        api_key    = p_api_key,
        key_hash   = p_key_hash,
        graph_name = p_graph_name,
        status     = 'active',
        updated_at = now()
    WHERE user_id = p_user_id AND team_id = '';
END;
$$;

-- ============================================================================
-- 5) reveal_api_key — atomic reveal + null (A13). SECURITY DEFINER bypasses
--    RLS, so the caller MUST prove they are the row owner (P1 — any authed
--    user could otherwise exfiltrate + null another user's key).
-- ============================================================================
CREATE OR REPLACE FUNCTION public.reveal_api_key(p_user_id uuid, p_team_id text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE k text;
BEGIN
    IF auth.uid() IS NULL OR auth.uid() <> p_user_id THEN
        RETURN NULL;
    END IF;
    SELECT api_key INTO k FROM public.team_memberships
     WHERE user_id = p_user_id AND team_id = p_team_id
       AND status = 'active' AND role = 'owner';
    IF k IS NULL OR k = 'pending' THEN
        RETURN NULL;
    END IF;
    UPDATE public.team_memberships SET api_key = NULL, updated_at = now()
     WHERE user_id = p_user_id AND team_id = p_team_id;
    RETURN k;  -- shown once; nulled atomically
END;
$$;

GRANT EXECUTE ON FUNCTION public.reveal_api_key TO authenticated;

-- ============================================================================
-- 6) RLS (rebuilt for the junction)
-- ============================================================================
-- Users read their own memberships (api_key column already revoked above).
CREATE POLICY "Users view own memberships"
    ON public.team_memberships
    FOR SELECT
    TO authenticated
    USING (user_id = auth.uid());

-- Invitee pre-accept: SELECT-only, email match (token accept is primary per
-- decision 1e; email-match retained defensively, NOT a resolution fallback).
CREATE POLICY "Invitee views own invite"
    ON public.team_memberships
    FOR SELECT
    TO authenticated
    USING (status = 'invited' AND invited_email = auth.jwt() ->> 'email');

-- Service role manages all.
CREATE POLICY "Service role manages all memberships"
    ON public.team_memberships
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
