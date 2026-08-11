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

-- Atomic increment (review P2, PR #911): a GET-then-PATCH increment loses
-- updates under concurrency (two readers see N, both write N+1 → net +1
-- instead of +2). This SECURITY DEFINER RPC does the increment in SQL —
-- write_ops = write_ops + n is atomic under Postgres row locking. The
-- caller (tortoise/metering.py metering_increment) invokes it via the
-- seam's rpc(); the registry MERGE it replaces had the same atomicity.
CREATE OR REPLACE FUNCTION public.metering_increment(
    p_team_id text,
    p_period  text,
    p_n       integer DEFAULT 1
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE v_ops integer;
BEGIN
    INSERT INTO public.metering_records (team_id, period, write_ops)
    VALUES (p_team_id, p_period, p_n)
    ON CONFLICT (team_id, period)
    DO UPDATE SET write_ops = public.metering_records.write_ops + p_n,
                  updated_at = now()
    RETURNING write_ops INTO v_ops;
    RETURN v_ops;
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment TO service_role;
