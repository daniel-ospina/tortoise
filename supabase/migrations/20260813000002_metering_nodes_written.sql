-- Migration 20260813000002: metering_records.nodes_written — value-first commit cost driver
-- Epic: #909 value-first mining · Issue: #953 (slice 5b — POST /v1/sessions/commit)
--
-- The MeteringRecord gains `nodes_written` (net-new non-episodic nodes per
-- commit call — plan §4.4/W-4/PL4): `write_ops` stays the published billed
-- unit (+1 per NON-duplicate commit call; hold commits bill 0 and their
-- re-submission bills the single +1 — one logical payload billed exactly
-- once), while `nodes_written` is the cost-driver counter that prevents the
-- 25x per-node arbitrage vs create_point (supersede-only deltas are exempt,
-- R-14). The 0017 RPC increments both columns atomically under the same
-- Postgres row lock (mirrors the 0014 atomicity rationale).
--
-- RENAME (issue #1001, 2026-08-13): the original 0015_metering_nodes_written
-- collided with 0015_abuse_events (Supabase CLI keys migrations by numeric
-- prefix; duplicate prefixes abort db push). Renamed to this timestamp-style
-- name via #1074/#1076/#1077 so it applies after all numeric migrations.
--
-- FIX (#1001): DROP the 0014-era 3-arg metering_increment overload BEFORE
-- CREATE OR REPLACE. A CREATE OR REPLACE with a DIFFERENT argument list adds
-- a second overload (Postgres semantics), which makes the bare
-- `REVOKE ALL ON FUNCTION ...` below ambiguous ("function is not unique")
-- and aborts the whole migration on ANY fresh replay. The DROP removes the
-- 0014 overload so exactly one remains and the REVOKE/GRANT resolve. (Prod
-- already had the overload removed by the earlier 0017 apply; this guard is
-- for fresh databases.)

ALTER TABLE public.metering_records
    ADD COLUMN IF NOT EXISTS nodes_written integer NOT NULL DEFAULT 0;

-- Drop the 0014 3-arg overload BEFORE creating the 4-arg version: a
-- CREATE OR REPLACE with a different arg list ADDS an overload instead of
-- replacing (see header note) — the bare REVOKE below would then be
-- ambiguous and abort the migration.
DROP FUNCTION IF EXISTS public.metering_increment(text, text, integer);

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
