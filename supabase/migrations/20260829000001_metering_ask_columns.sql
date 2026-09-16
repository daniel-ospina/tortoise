-- Migration 20260829000001: metering_records ask_* columns — per-query ask
-- metering (#1987 Task 6).
--
-- The ask lane records per-query LLM cost on the SAME metering_records row
-- (PK (team_id, period)) via additive columns — the write-op MERGE only
-- creates the row on first write; a fresh team with no ask records renders
-- ZEROS (never 500, P2-14). The increment RPC mirrors metering_increment
-- (0014): atomic under Postgres row locking, SECURITY DEFINER,
-- service_role-only.
--
-- ask_tokens_in/out are bigint (NOT integer, P2): the ask envelope is up to
-- ~8k tokens/query x 60/min/team sustained → ~20.7B tokens/month, ~10x over
-- the integer range — overflow would raise and (best-effort metering)
-- silently stop recording.

ALTER TABLE public.metering_records
    ADD COLUMN IF NOT EXISTS ask_calls       integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ask_tokens_in   bigint  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ask_tokens_out  bigint  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS ask_cost_usd    double precision NOT NULL DEFAULT 0;

CREATE OR REPLACE FUNCTION public.metering_increment_ask(
    p_team_id    text,
    p_period     text,
    p_calls      integer DEFAULT 1,
    p_tokens_in  integer DEFAULT 0,
    p_tokens_out integer DEFAULT 0,
    p_cost_usd   double precision DEFAULT 0
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.metering_records
        (team_id, period, ask_calls, ask_tokens_in, ask_tokens_out, ask_cost_usd)
    VALUES (p_team_id, p_period, p_calls, p_tokens_in, p_tokens_out, p_cost_usd)
    ON CONFLICT (team_id, period)
    DO UPDATE SET
        ask_calls      = public.metering_records.ask_calls + p_calls,
        ask_tokens_in  = public.metering_records.ask_tokens_in + p_tokens_in,
        ask_tokens_out = public.metering_records.ask_tokens_out + p_tokens_out,
        ask_cost_usd   = public.metering_records.ask_cost_usd + p_cost_usd,
        updated_at     = now();
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment_ask FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment_ask TO service_role;
