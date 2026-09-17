-- Migration 20260917000001: metering_records.capture_cost_usd — the CAPTURE
-- lane's measured LLM spend on the durable per-period ledger (#3665, lane B7).
--
-- WHY THIS EXISTS. #3359 (PR #3508) made the hosted capture path MEASURE
-- per-session LLM cost and emitted it as an ``analytics_events`` row
-- (``event_name='capture_cost'``, ``properties.cost_usd``). That is a
-- MEASUREMENT, not a LEDGER: it has no per-period row keyed by org, so a
-- spend CEILING cannot read it without an aggregate scan of every capture
-- row in the period — and #3665's whole point is that a cap which is
-- configured but not enforced is a false PASS.
--
-- The #1987 ask lane already proved the shape to copy: an additive
-- ``*_cost_usd`` column on the ``metering_records`` row (PK (org_id,
-- period)) written through an atomic increment RPC (#20260829000001). This
-- migration adds the capture lane to the SAME row so the cohort aggregate is
-- ``SUM(ask_cost_usd + capture_cost_usd)`` over a cohort's org ids — one row
-- per org per month, no per-capture scan.
--
-- NOT a billing change: ``ask_cost_usd``/``capture_cost_usd`` are measured
-- COGS, never charged to a customer. ``product/pricing.json`` prices
-- overage on counts; this column is orthogonal to it (#3665 non-goal).

ALTER TABLE public.metering_records
    ADD COLUMN IF NOT EXISTS capture_cost_usd double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS capture_calls    integer NOT NULL DEFAULT 0;

-- Atomic additive increment, mirroring metering_increment_ask exactly
-- (Postgres row locking; SECURITY DEFINER; service_role-only). Best-effort
-- by contract — the Python caller swallows failures so metering can never
-- block a committed capture.
CREATE OR REPLACE FUNCTION public.metering_increment_capture_cost(
    p_org_id    text,
    p_period    text,
    p_calls     integer DEFAULT 0,
    p_cost_usd  double precision DEFAULT 0
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.metering_records
        (org_id, period, capture_calls, capture_cost_usd)
    VALUES (p_org_id, p_period, p_calls, p_cost_usd)
    ON CONFLICT (org_id, period)
    DO UPDATE SET
        capture_calls    = public.metering_records.capture_calls + p_calls,
        capture_cost_usd = public.metering_records.capture_cost_usd + p_cost_usd,
        updated_at       = now();
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment_capture_cost(text, text,
    integer, double precision) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment_capture_cost(text, text,
    integer, double precision) TO service_role;
