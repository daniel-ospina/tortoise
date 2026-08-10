-- Migration 0013: webhook_events — Stripe webhook dedup marker (#771 re-review P1)
-- Epic: #669 plan v2 Task 10 · Issue: #771
--
-- The webhook's first-seen marker (WebhookEvent node in the registry) must
-- not write the registry in Supabase mode — an unguarded write would
-- resurrect the deleted registry graph (FalkorDB GRAPH.QUERY auto-creates).
-- This table is the Supabase-mode marker store: event_id PK, first_seen,
-- type. Same SET-then-marker semantics as the registry twin: the apply is
-- idempotent (replays converge); only side-effects (notify/audit/analytics)
-- are dedup'd to first processing.

CREATE TABLE IF NOT EXISTS public.webhook_events (
    event_id    text PRIMARY KEY,
    first_seen  timestamptz NOT NULL DEFAULT now(),
    type        text
);

-- RLS: service_role manages all; no browser surface reads this table.
ALTER TABLE public.webhook_events ENABLE ROW LEVEL SECURITY;

CREATE POLICY webhook_events_service_role_all ON public.webhook_events
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
