-- Migration 0004: analytics_events table for onboarding funnel analytics
-- Funnel events from the hosted onboarding flow (issue #501/#543):
--   signup_completed, key_provisioned, artifact_copied, agent_connected,
--   question_answered, github_connected, indexing_started/completed,
--   demo_created, session_recording_enabled, onboarding_complete, onboarding_error
-- Written by tortoise/hosted_api.py _track_analytics_event() via the
-- service-role key (SUPABASE_URL + SUPABASE_SERVICE_KEY); JSONL file fallback
-- when Supabase is unconfigured.
-- Properties are PII-filtered at the source (_ALLOWED_ANALYTICS_PROPS allowlist).

-- ============================================================================
-- Table: analytics_events
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.analytics_events (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id     text NOT NULL,
    event_name  text NOT NULL,
    properties  jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- ============================================================================
-- Indexes: funnel queries are (team_id, created_at) and (event_name) grouped
-- ============================================================================
CREATE INDEX IF NOT EXISTS idx_analytics_team_time
    ON public.analytics_events (team_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_analytics_event_name
    ON public.analytics_events (event_name, created_at DESC);

-- ============================================================================
-- RLS: service-role-only writes (events come from the hosted API, not the
-- browser — the welcome page never touches this table directly). Read access
-- is not granted to end users; analytics dashboards use the service role.
-- ============================================================================
ALTER TABLE public.analytics_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Service role can manage analytics events"
    ON public.analytics_events
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ============================================================================
-- Immutability: events are append-only (analytics integrity)
-- ============================================================================
CREATE OR REPLACE FUNCTION public.prevent_analytics_mutation()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    RAISE EXCEPTION 'analytics_events is append-only: % not allowed', TG_OP;
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER analytics_events_immutable
  BEFORE UPDATE OR DELETE ON public.analytics_events
  FOR EACH STATEMENT
  EXECUTE FUNCTION public.prevent_analytics_mutation();
