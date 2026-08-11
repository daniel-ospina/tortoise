-- Migration 0014: metering_records — write-op metering (#669 post-flip fix)
-- Epic: #669 plan v2 Task 8 follow-up · Issue: #669
--
-- Post-flip verification finding: tortoise/metering.py stored MeteringRecord
-- nodes in the FalkorDB registry graph — /v1/team (get_current_usage) and
-- every write-op increment (record_write_ops) touched the registry, which
-- RECREATED the deleted registry_control_plane on every request (FalkorDB
-- auto-creates graphs on query). Metering is billing infrastructure and must
-- live beside the control plane it meters — this table is the Supabase-mode
-- store (the plan's data model predates metering; this is the minimal
-- addition that keeps /v1/team + billing working post-flip).

CREATE TABLE IF NOT EXISTS public.metering_records (
    team_id     text NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
    period      text NOT NULL,              -- 'YYYY-MM' billing period
    write_ops   integer NOT NULL DEFAULT 0,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, period)
);

-- RLS: service_role manages all; no browser surface reads metering.
ALTER TABLE public.metering_records ENABLE ROW LEVEL SECURITY;

CREATE POLICY metering_service_role_all ON public.metering_records
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
