-- Migration 20261013000001: the RETRIEVAL lane's search work on the metering
-- ledger (#4489, lane c4-answer-quality / c7-instrumentation).
--
-- RENUMBERED FORWARD from 20261012000001 (#4489 review). ``main`` landed a
-- second migration under that exact version prefix (20261012000001_record_event
-- _stream.sql) after this lane branched, and the Supabase CLI keys migrations by
-- prefix — two files under one version means one of them is never applied and
-- ``db push`` aborts (the class #1235 blocks and #7634 documents). A version
-- strictly greater than main's newest keeps this file pending on every lane.
--
-- WHY THIS EXISTS. Reads are free by DECISION (``product/pricing.json`` →
-- ``billing.reads_free: true``), so the engine cost of answering a query had no
-- meter at all: the only per-org dimension was write-ops (``metering`` →
-- ``_ops_allowance``), and a read-heavy org and an idle one were
-- indistinguishable. #4489 asks for a per-org retrieval figure — how many
-- queries, how much wall time, and WHICH retrieval legs ran (dense vector /
-- FTS / graph traversal) — and a way for a person to read it. Nothing more.
--
-- ⛔ NOT A BILLING CHANGE. These columns are WORKLOAD, never price, tier, quota
-- or entitlement (#4489's explicit out-of-scope list: "Any price level, tier
-- boundary, or read quota"). ``metering_cohort_spend`` / ``get_cohort_spend_usd``
-- read ONLY ``ask_cost_usd`` + ``capture_cost_usd``, so the spend ceiling cannot
-- see these columns — untouched by construction, not by convention.
--
-- WHY THE LEDGER AND NOT A PROCESS COUNTER. The same cluster's compute/egress
-- lanes (#4490/#4491) put their figures in the in-process Prometheus registry
-- and state the limit that follows: nothing scrapes it in production, so the
-- snapshot is the only queryable form and it dies with the process. A cost base
-- for "unlimited reads" has to survive a deploy and be comparable against the
-- SAME org's write-ops figure, which is on this ledger — so this dimension is
-- the durable per-(org, period) row, read through the existing ``/v1/team``
-- path, exactly as the sibling local-compute dimension (#4488, embedding
-- encode) did. One vocabulary, one read path.
--
-- The shape copies the embedding lane (20261001000004) and the window keying
-- (20260918000001): an additive row on the SAME ``(org_id, period_start)`` PK,
-- written through ONE atomic increment RPC, read back by PRIMARY KEY.
--
-- ⛔ DEPLOY ORDER: this migration FIRST, the Python second. The RPC is
-- best-effort by contract — a missing function is logged at WARNING and the
-- write is dropped, while the READER degrades to a zero view. Deploying the
-- code first therefore yields a SILENT ZERO WINDOW (not an error), which is
-- precisely the fail-open this ledger exists to avoid.

ALTER TABLE public.metering_records
    ADD COLUMN IF NOT EXISTS retrieval_queries     integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS retrieval_leg_runs    integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS retrieval_leg_degraded integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS retrieval_wall_ms     double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS retrieval_leg_fts        integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS retrieval_leg_vector     integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS retrieval_leg_structural integer NOT NULL DEFAULT 0;

-- ⛔ EVERY COUNTER IS ``NOT NULL DEFAULT 0``, and that is load-bearing, not
-- tidiness. A metering row is created by whichever lane writes FIRST —
-- ``metering_increment`` on a plain write, ``metering_increment_ask`` on an ask
-- — and those RPCs do NOT mention the retrieval columns. ``NULL + n`` is NULL in
-- SQL, so without the NOT NULL DEFAULT the counters would evaluate to NULL
-- forever on every pre-existing row and the reader would render 0 on the default
-- path with no error anywhere: a permanently dead figure that reads as "this org
-- never read". (20260917000001's ``capture_cost_usd`` carries the same
-- reasoning; 20261001000004 restates it for the embed lane.)

COMMENT ON COLUMN public.metering_records.retrieval_queries IS
    '#4489: number of RETRIEVAL QUERIES (hybrid search calls) attributed to this '
    'window. One query exercises one or more legs below.';
COMMENT ON COLUMN public.metering_records.retrieval_wall_ms IS
    '#4489: measured WALL TIME of those queries, in milliseconds. Measured '
    'around the retrieval call only (it EXCLUDES the query-encode and the '
    'post-retrieval annotation/assembly) and is CPU/DB dependent — a cost '
    'signal, NOT a price.';
COMMENT ON COLUMN public.metering_records.retrieval_leg_runs IS
    '#4489: total leg EXECUTIONS behind those queries. Bounded by construction: '
    'a leg name that is not one of the three recorded legs (fts/vector/'
    'structural) advances this total and no per-leg column, so an unknown leg '
    'can never create a label or a column.';
COMMENT ON COLUMN public.metering_records.retrieval_leg_degraded IS
    '#4489: leg executions that returned DEGRADED (the leg ran but contributed '
    'nothing — breaker open, index missing, timeout). Kept distinct so a read '
    'that silently fell back to one leg is visible as such, not averaged in.';
COMMENT ON COLUMN public.metering_records.retrieval_leg_fts IS
    '#4489: FTS (sparse keyword) leg executions.';
COMMENT ON COLUMN public.metering_records.retrieval_leg_vector IS
    '#4489: dense VECTOR leg executions — the leg that consumes the embedding '
    'encode, so this column is what makes the retrieval/embed cost split read '
    'off one row.';
COMMENT ON COLUMN public.metering_records.retrieval_leg_structural IS
    '#4489: STRUCTURAL (graph traversal / hop expansion) leg executions.';

-- ============================================================================
-- The increment RPC. DROPPED before CREATE, not replaced in place: a different
-- argument list would otherwise be an OVERLOAD and leave an older signature
-- callable — the silent second path #3825 removed. The ACL is re-issued after
-- the DROP (a missing REVOKE/GRANT re-issue is how ACLs get lost).
-- ============================================================================
DROP FUNCTION IF EXISTS public.metering_increment_retrieval(
    text, timestamptz, timestamptz, integer, integer, integer,
    double precision, integer, integer, integer);

CREATE FUNCTION public.metering_increment_retrieval(
    p_org_id           text,
    p_period_start     timestamptz,
    p_period_end       timestamptz,
    p_queries          integer DEFAULT 0,
    p_leg_runs         integer DEFAULT 0,
    p_leg_degraded     integer DEFAULT 0,
    p_wall_ms          double precision DEFAULT 0,
    p_leg_fts          integer DEFAULT 0,
    p_leg_vector       integer DEFAULT 0,
    p_leg_structural   integer DEFAULT 0
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
            'metering_increment_retrieval: period_end (%) must be after '
            'period_start (%)', p_period_end, p_period_start;
    END IF;
    -- ``period`` is NOT NULL with no default (0014) and period_start/period_end
    -- became NOT NULL with no default in 20260918000001, so the derived label
    -- and BOTH window edges must be supplied explicitly — omitting any of them
    -- raises a NOT NULL violation on the INSERT and the increment is dropped.
    INSERT INTO public.metering_records
        (org_id, period, period_start, period_end, retrieval_queries,
         retrieval_leg_runs, retrieval_leg_degraded, retrieval_wall_ms,
         retrieval_leg_fts, retrieval_leg_vector, retrieval_leg_structural)
    VALUES (p_org_id,
            to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM'),
            p_period_start, p_period_end,
            p_queries, p_leg_runs, p_leg_degraded, p_wall_ms,
            p_leg_fts, p_leg_vector, p_leg_structural)
    ON CONFLICT (org_id, period_start)
    DO UPDATE SET
        -- coalesce() on every counter is belt-and-braces with the NOT NULL
        -- DEFAULT above: a row created before the columns existed (or by a
        -- future lane that forgets them) still increments instead of sticking
        -- at NULL.
        retrieval_queries      = coalesce(public.metering_records.retrieval_queries, 0) + p_queries,
        retrieval_leg_runs     = coalesce(public.metering_records.retrieval_leg_runs, 0) + p_leg_runs,
        retrieval_leg_degraded = coalesce(public.metering_records.retrieval_leg_degraded, 0) + p_leg_degraded,
        retrieval_wall_ms      = coalesce(public.metering_records.retrieval_wall_ms, 0) + p_wall_ms,
        retrieval_leg_fts        = coalesce(public.metering_records.retrieval_leg_fts, 0) + p_leg_fts,
        retrieval_leg_vector     = coalesce(public.metering_records.retrieval_leg_vector, 0) + p_leg_vector,
        retrieval_leg_structural = coalesce(public.metering_records.retrieval_leg_structural, 0) + p_leg_structural,
        -- #4242: the WINDOW follows the authoritative bound. Every period-keyed
        -- writer carries this (20261010000001 re-created all five) because a row
        -- minted under an APPROXIMATED anchor keeps a stale ``period_end``
        -- forever, which the cohort read's half-open overlap test can then miss
        -- — fail-OPEN on a spend ceiling. This lane is the first writer of a
        -- shared row and must not pin a window it cannot later correct.
        period_end             = EXCLUDED.period_end,
        updated_at             = now();
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment_retrieval(
    text, timestamptz, timestamptz, integer, integer, integer,
    double precision, integer, integer, integer)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment_retrieval(
    text, timestamptz, timestamptz, integer, integer, integer,
    double precision, integer, integer, integer)
    TO service_role;
