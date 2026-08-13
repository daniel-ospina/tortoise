-- Migration 0015: metering_records.nodes_written — value-first commit cost driver
-- Epic: #909 value-first mining · Issue: #953 (slice 5b — POST /v1/sessions/commit)
--
-- The MeteringRecord gains `nodes_written` (net-new non-episodic nodes per
-- commit call — plan §4.4/W-4/PL4): `write_ops` stays the published billed
-- unit (+1 per NON-duplicate commit call; hold commits bill 0 and their
-- re-submission bills the single +1 — one logical payload billed exactly
-- once), while `nodes_written` is the cost-driver counter that prevents the
-- 25x per-node arbitrage vs create_point (supersede-only deltas are exempt,
-- R-14). The 0015 RPC increments both columns atomically under the same
-- Postgres row lock (mirrors the 0014 atomicity rationale).

ALTER TABLE public.metering_records
    ADD COLUMN IF NOT EXISTS nodes_written integer NOT NULL DEFAULT 0;

-- Atomic dual-column increment (extend 0014 — same locking model).
CREATE OR REPLACE FUNCTION public.metering_increment(
    p_team_id      text,
    p_period       text,
    p_n            integer DEFAULT 1,
    p_nodes_written integer DEFAULT 0
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE v_ops integer;
BEGIN
    INSERT INTO public.metering_records (team_id, period, write_ops, nodes_written)
    VALUES (p_team_id, p_period, p_n, p_nodes_written)
    ON CONFLICT (team_id, period)
    DO UPDATE SET write_ops = public.metering_records.write_ops + p_n,
                  nodes_written = public.metering_records.nodes_written + p_nodes_written,
                  updated_at = now()
    RETURNING write_ops INTO v_ops;
    RETURN v_ops;
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment TO service_role;
