-- Migration 20260926000001: the CAPTURE lane's extraction TOKEN WORKLOAD on
-- the metering ledger (#5045, lane c7-instrumentation).
--
-- WHY THIS EXISTS. The owner ruling (2026-09-26, on #4495; carried forward in
-- #5331/#5045) sells extraction overage:
--
--     "we give them a number of 'tokens' but if they use too much, they have
--      to purchase extraction overage."
--
-- The terminal's own pricing ruler spans MB/GB for storage, MB for the graph,
-- and TOKENS for extraction. The ASK lane has recorded the token counters
-- since #1987 (``ask_tokens_in``/``ask_tokens_out``, 20260829000001), but the
-- CAPTURE (extraction) lane recorded only ``capture_calls`` and
-- ``capture_cost_usd`` (20260917000001) — so there is today NO per-org number
-- an extraction ALLOWANCE could be drawn against, and extraction is exactly
-- what the ruling sells overage for.
--
-- NO NEW MEASUREMENT IS INVENTED HERE. ``hosted_api._capture_cost_props``
-- already reads ``prompt_tokens``/``completion_tokens`` out of the extractor
-- telemetry (``meta["stats"]["llm"]``) and puts them on the ANALYTICS row;
-- they simply were never carried onto the durable LEDGER. This migration adds
-- the two columns, and the code change on this branch threads the ALREADY
-- COMPUTED numbers through. The count is never re-derived from cost.
--
-- NOT A BILLING CHANGE. These columns are WORKLOAD, never price, tier, quota
-- or entitlement. ``metering_cohort_spend`` (whose LIVE body is re-issued by
-- 20260918000001) and ``get_cohort_spend_usd`` read ONLY
-- ``ask_cost_usd + capture_cost_usd`` and are NOT touched here, so the spend
-- ceiling cannot see these columns at all — untouched by CONSTRUCTION, not by
-- convention.
--
-- ⛔ DEPLOY ORDER: THIS MIGRATION FIRST, THE PYTHON SECOND. The increment RPC
-- is best-effort by contract — a missing function is logged at WARNING and the
-- increment is DROPPED, not raised — so deploying the code first yields a
-- SILENT ZERO WINDOW rather than an error: precisely the fail-open this
-- ledger exists to avoid. (Same operational note the 20260918000001 re-issue
-- carries.)

ALTER TABLE public.metering_records
    ADD COLUMN IF NOT EXISTS capture_tokens_in  bigint NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS capture_tokens_out bigint NOT NULL DEFAULT 0;

-- ⛔ ``NOT NULL DEFAULT 0`` — and the reason is narrower than it looks. The
-- INCREMENT PATH is already NULL-safe on its own: it wraps the column in
-- ``coalesce(capture_tokens_in, 0) + p_tokens_in``, so a nullable column would
-- still increment correctly. The NOT NULL DEFAULT is load-bearing for the rows
-- this RPC NEVER TOUCHES: a metering row is created by whichever lane writes
-- FIRST — ``metering_increment`` on a plain write, ``metering_increment_ask``
-- on an ask — and those RPCs do not mention the capture token columns at all.
-- Without the default, every such row reads NULL to a reader with no error
-- anywhere, which renders as 0 and looks like "no extraction work".
-- (The capture cost columns 20260917000001, which ARE on main, carry the same
-- reasoning and are the precedent to read; the embed columns
-- 20260925000003 mirror it but live on an unmerged sibling branch, so they are
-- NOT yet deployable history.)
COMMENT ON COLUMN public.metering_records.capture_tokens_in IS
    '#5045: prompt (input) tokens consumed by the LLM calls attributed to '
    'this window''s capture/extraction work. WORKLOAD, never price — it is '
    'the number an extraction allowance is drawn against, and it is never '
    'read by any spend cap, tier or refusal.';
COMMENT ON COLUMN public.metering_records.capture_tokens_out IS
    '#5045: completion (output) tokens produced by the LLM calls attributed '
    'to this window''s capture/extraction work. WORKLOAD, never price — see '
    'capture_tokens_in.';

-- ============================================================================
-- The increment RPC. DROPPED before CREATE, not replaced in place: a different
-- argument list would otherwise be an OVERLOAD and leave the OLD
-- (text, timestamptz, timestamptz, integer, double precision) signature
-- callable — the silent second write path #3825 removed. The ACL is re-issued
-- after the DROP (a missing REVOKE/GRANT re-issue is how ACLs get lost;
-- 20260915's own comment records exactly that).
--
-- The two token parameters are ``bigint`` to match the COLUMNS they feed
-- (20260829000001's ``ask_tokens_in``/``out`` are bigint for the same
-- reason): a single capture is small, but there is no gain in narrowing the
-- accumulator's input to ``integer`` and inheriting a 2.1B ceiling.
-- ============================================================================
DROP FUNCTION IF EXISTS public.metering_increment_capture_cost(text,
    timestamptz, timestamptz, integer, double precision);

CREATE FUNCTION public.metering_increment_capture_cost(
    p_org_id       text,
    p_period_start timestamptz,
    p_period_end   timestamptz,
    p_calls        integer DEFAULT 0,
    p_tokens_in    bigint DEFAULT 0,
    p_tokens_out   bigint DEFAULT 0,
    p_cost_usd     double precision DEFAULT 0
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- A degenerate window mints a row no reader can ever match (the cohort
    -- aggregate is a strict overlap test). Refuse rather than create orphan
    -- measurement: fail-closed is the discipline of this ledger.
    IF p_period_end <= p_period_start THEN
        RAISE EXCEPTION
            'metering_increment_capture_cost: period_end (%) must be after '
            'period_start (%)', p_period_end, p_period_start;
    END IF;
    INSERT INTO public.metering_records
        (org_id, period, period_start, period_end,
         capture_calls, capture_tokens_in, capture_tokens_out, capture_cost_usd)
    VALUES (p_org_id,
            to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM'),
            p_period_start, p_period_end,
            p_calls, p_tokens_in, p_tokens_out, p_cost_usd)
    ON CONFLICT (org_id, period_start)
    DO UPDATE SET
        capture_calls      = public.metering_records.capture_calls + p_calls,
        -- coalesce() is belt-and-braces with the NOT NULL DEFAULT above: a
        -- row created by an RPC that predates the columns still increments
        -- instead of sticking at NULL.
        capture_tokens_in  = coalesce(public.metering_records.capture_tokens_in, 0)
                             + p_tokens_in,
        capture_tokens_out = coalesce(public.metering_records.capture_tokens_out, 0)
                             + p_tokens_out,
        capture_cost_usd   = public.metering_records.capture_cost_usd + p_cost_usd,
        updated_at         = now();
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment_capture_cost(text,
    timestamptz, timestamptz, integer, bigint, bigint, double precision)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment_capture_cost(text,
    timestamptz, timestamptz, integer, bigint, bigint, double precision)
    TO service_role;
