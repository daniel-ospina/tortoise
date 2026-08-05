-- Migration 0001: user_teams table for signup provisioning
-- Maps Supabase auth.users to provisioned Tortoise teams.
-- API key stored in plaintext for one-time display on welcome page;
-- key_hash stored for lookup; plaintext should be nulled after first display.

-- ============================================================================
-- Table: user_teams
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.user_teams (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    team_id     text NOT NULL,
    team_name   text NOT NULL,
    api_key     text,          -- plaintext — shown once on welcome page, then nulled
    key_hash    text NOT NULL, -- SHA-256 hash of api_key for lookup/auth
    graph_name  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),

    -- One team per user (Phase 1 — Free tier has 1 team)
    CONSTRAINT uq_user_teams_user UNIQUE (user_id)
);

-- Index for key_hash lookups during API request auth (future Phase 2)
CREATE INDEX IF NOT EXISTS idx_user_teams_key_hash ON public.user_teams (key_hash);

-- ============================================================================
-- RLS: Users can read only their own team. Service role manages all.
-- ============================================================================
ALTER TABLE public.user_teams ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own teams"
    ON public.user_teams
    FOR SELECT
    TO authenticated
    USING (auth.uid() = user_id);

CREATE POLICY "Service role can manage all teams"
    ON public.user_teams
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ============================================================================
-- Function: handle_new_user() — called by trigger on auth.users INSERT
-- ============================================================================
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- Insert a placeholder row. The Edge Function tenant-provision
    -- will update this row with the actual team data after provisioning.
    -- The welcome page polls/watches this row until api_key is populated.
    INSERT INTO public.user_teams (
        user_id,
        team_id,
        team_name,
        key_hash,
        graph_name
    ) VALUES (
        NEW.id,
        '',           -- placeholder, filled by edge function
        'provisioning...',
        'pending',
        ''
    )
    ON CONFLICT (user_id) DO NOTHING;

    RETURN NEW;
END;
$$;

-- ============================================================================
-- Trigger: on_auth_user_created
-- ============================================================================
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;

CREATE TRIGGER on_auth_user_created
    AFTER INSERT ON auth.users
    FOR EACH ROW
    EXECUTE FUNCTION public.handle_new_user();

-- ============================================================================
-- Function: update_user_team() — called by Edge Function after provisioning
-- ============================================================================
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
    UPDATE public.user_teams
    SET team_id    = p_team_id,
        team_name  = p_team_name,
        api_key    = p_api_key,
        key_hash   = p_key_hash,
        graph_name = p_graph_name,
        updated_at = now()
    WHERE user_id = p_user_id;
END;
$$;
