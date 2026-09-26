-- Migration 20260925000003: the EMBED lane's encode WORKLOAD on the metering
-- ledger (#4488, lane c7-instrumentation).
--
-- WHY THIS EXISTS. The cost meter (#3359/#3824) covers LLM PROVIDER calls,
-- which carry a billable token count. A local ``sentence-transformers`` encode
-- consumes CPU seconds and RAM and produces no such count, so embedding work is
-- invisible to every existing figure. #4488 asks for a per-org embedding
-- workload figure, the model identity that produced it, and a way for a person
-- to read it — nothing more.
--
-- NOT A BILLING CHANGE. These columns are WORKLOAD, never price, tier, quota or
-- entitlement (#4488's explicit out-of-scope list). ``metering_cohort_spend``
-- and ``get_cohort_spend_usd`` read ONLY ``ask_cost_usd`` + ``capture_cost_usd``
-- and are NOT touched here, so the spend ceiling cannot see these columns at
-- all — untouched by construction, not by convention.
--
-- The shape copies the capture lane (20260917000001) and the window keying
-- (20260918000001): an additive row on the SAME ``(org_id, period_start)`` PK,
-- written through ONE atomic increment RPC, read back by PRIMARY KEY.
--
-- ⛔ DEPLOY ORDER: this migration FIRST, the Python second. The RPC is
-- best-effort by contract — a missing function is logged at WARNING and the
-- write is dropped, while the READER degrades to a zero view. Deploying the
-- code first therefore yields a SILENT ZERO WINDOW (not an error), which is
-- precisely the fail-open this ledger exists to avoid. (Same operational note
-- the 20260918000001 re-issue carries.)

ALTER TABLE public.metering_records
    ADD COLUMN IF NOT EXISTS embed_calls  integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS embed_texts  bigint  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS embed_chars  bigint  NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS embed_wall_ms double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS embed_skipped integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS embed_model   text,
    ADD COLUMN IF NOT EXISTS embed_revision text,
    ADD COLUMN IF NOT EXISTS embed_identity_mixed boolean NOT NULL DEFAULT false;

-- ⛔ EVERY COUNTER IS ``NOT NULL DEFAULT 0``, and that is load-bearing, not
-- tidiness. A metering row is created by whichever lane writes FIRST —
-- ``metering_increment`` on a plain write, ``metering_increment_ask`` on an ask
-- — and those RPCs do NOT mention the embed columns. ``NULL + n`` is NULL in
-- SQL, so without the NOT NULL DEFAULT the embed counters would evaluate to
-- NULL forever on every pre-existing row and the reader would render 0 on the
-- default path with no error anywhere: a permanently dead figure that looks
-- like "no embedding work". (20260917000001's ``capture_cost_usd`` carries the
-- same reasoning; this is its precedent.)

COMMENT ON COLUMN public.metering_records.embed_calls IS
    '#4488: number of MODEL ENCODE calls attributed to this window. One call is '
    'one `model.encode(...)` invocation, which may carry many texts.';
COMMENT ON COLUMN public.metering_records.embed_texts IS
    '#4488: number of texts passed to those encodes.';
COMMENT ON COLUMN public.metering_records.embed_chars IS
    '#4488: characters (of the TRUNCATED strings actually encoded) passed to '
    'those encodes — a size proxy the text count cannot express.';
COMMENT ON COLUMN public.metering_records.embed_wall_ms IS
    '#4488: measured WALL TIME of the encode calls, in milliseconds. Measured '
    'around model.encode only; it is CPU/batching dependent and is NOT a price.';
COMMENT ON COLUMN public.metering_records.embed_skipped IS
    '#4488: encode attempts that ran NO model work (embedder unavailable). Kept '
    'distinct so a zero reads as "the embedder did not run", never as "it ran '
    'and produced nothing".';
COMMENT ON COLUMN public.metering_records.embed_model IS
    '#4488: the encoder identity that DID the work, travelling WITH the figure '
    'so a reading cannot be attributed to an encoder that never ran.';
COMMENT ON COLUMN public.metering_records.embed_identity_mixed IS
    '#4488: STICKY latch — TRUE once two DIFFERENT encoder identities have been '
    'observed in the same window (a mid-period model/revision change, or a '
    'swap inside one work unit). Deliberately never cleared: it is what makes a '
    'change VISIBLE instead of being averaged into an unattributed figure.';

-- ============================================================================
-- The increment RPC. DROPPED before CREATE, not replaced in place: a different
-- argument list would otherwise be an OVERLOAD and leave an older signature
-- callable — the silent second path #3825 removed. The ACL is re-issued after
-- the DROP (a missing REVOKE/GRANT re-issue is how ACLs get lost).
-- ============================================================================
DROP FUNCTION IF EXISTS public.metering_increment_embedding(
    text, timestamptz, timestamptz, integer, bigint, bigint,
    double precision, integer, text, text, boolean);

CREATE FUNCTION public.metering_increment_embedding(
    p_org_id         text,
    p_period_start   timestamptz,
    p_period_end     timestamptz,
    p_calls          integer DEFAULT 0,
    p_texts          bigint DEFAULT 0,
    p_chars          bigint DEFAULT 0,
    p_wall_ms        double precision DEFAULT 0,
    p_skipped        integer DEFAULT 0,
    p_model          text DEFAULT NULL,
    p_revision       text DEFAULT NULL,
    p_identity_mixed boolean DEFAULT false
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
            'metering_increment_embedding: period_end (%) must be after '
            'period_start (%)', p_period_end, p_period_start;
    END IF;
    -- ``period`` is NOT NULL with no default (0014) and period_start/period_end
    -- became NOT NULL with no default in 20260918000001, so the derived label
    -- and BOTH window edges must be supplied explicitly — omitting any of them
    -- raises a NOT NULL violation on the INSERT and the increment is dropped.
    INSERT INTO public.metering_records
        (org_id, period, period_start, period_end, embed_calls, embed_texts,
         embed_chars, embed_wall_ms, embed_skipped, embed_model,
         embed_revision, embed_identity_mixed)
    VALUES (p_org_id,
            to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM'),
            p_period_start, p_period_end,
            p_calls, p_texts, p_chars, p_wall_ms, p_skipped,
            p_model, p_revision, p_identity_mixed)
    ON CONFLICT (org_id, period_start)
    DO UPDATE SET
        -- The mixed flag is computed in its own assignment from the STORED
        -- identity, so a mid-window swap is visible. It reads the row's value
        -- as it was BEFORE this UPDATE — but NOT because it is written first:
        -- PostgreSQL evaluates every `SET` expression against the OLD row
        -- regardless of the order the assignments appear in. That rule is why
        -- `public.metering_records.embed_model` below still means "the value
        -- this row already had"; reordering these clauses would change nothing.
        -- (An earlier version of this comment claimed the opposite — that the
        -- targets are evaluated in order — which would teach a maintainer a
        -- false model of the statement they are about to edit. Found in
        -- review.) The flag is sticky (never reset), paired (the swap term
        -- requires BOTH a stored and an incoming identity), and skip-safe (a
        -- model-less flush cannot latch it).
        embed_identity_mixed =
            coalesce(public.metering_records.embed_identity_mixed, false)
            OR coalesce(p_identity_mixed, false)
            OR (public.metering_records.embed_model IS NOT NULL
                AND p_model IS NOT NULL
                AND (public.metering_records.embed_model IS DISTINCT FROM p_model
                     OR public.metering_records.embed_revision
                        IS DISTINCT FROM p_revision)),
        -- coalesce() on every counter is belt-and-braces with the NOT NULL
        -- DEFAULT above: a row created before the columns existed (or by a
        -- future lane that forgets them) still increments instead of sticking
        -- at NULL.
        embed_calls  = coalesce(public.metering_records.embed_calls, 0) + p_calls,
        embed_texts  = coalesce(public.metering_records.embed_texts, 0) + p_texts,
        embed_chars  = coalesce(public.metering_records.embed_chars, 0) + p_chars,
        embed_wall_ms = coalesce(public.metering_records.embed_wall_ms, 0) + p_wall_ms,
        embed_skipped = coalesce(public.metering_records.embed_skipped, 0) + p_skipped,
        -- A skipped-only flush passes NULL and must NOT erase the identity of
        -- the encoder the window actually used.
        embed_model    = coalesce(p_model,
                                  public.metering_records.embed_model),
        embed_revision = coalesce(p_revision,
                                  public.metering_records.embed_revision),
        updated_at     = now();
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment_embedding(
    text, timestamptz, timestamptz, integer, bigint, bigint,
    double precision, integer, text, text, boolean)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment_embedding(
    text, timestamptz, timestamptz, integer, bigint, bigint,
    double precision, integer, text, text, boolean)
    TO service_role;
