-- Migration 20260926000002: the GRAPH STORAGE gauge on the metering ledger
-- (#5331, lane c7-instrumentation).
--
-- ⚠️ WHY 000002 AND NOT 000001. The sibling #5045 branch adds
-- ``20260926000001_metering_capture_tokens.sql``, and the two files touch
-- DISJOINT objects, so nothing about the SQL collides — but the VERSION
-- PREFIX is the key of ``supabase_migrations.schema_migrations``, and this
-- repo guards that key twice: ``tests/test_migration_append_only.py::
-- test_prefix_duplicates_rejected`` and ``tests/test_migration_drift_gate.py::
-- test_duplicate_prefix_blocks`` (#1235 — *"duplicate still blocks"*,
-- *"db push would abort"*). Two files sharing a prefix therefore turn main RED
-- as soon as both land, regardless of merge order. ``000002`` follows the
-- same-day convention already in this directory (20260925000001/00002). Do not
-- "tidy" this back to 000001.
--
-- WHY THIS EXISTS. The owner ruling (2026-09-26) is that storage is counted in
-- MB/GB, not nodes. ``GRAPH.MEMORY USAGE`` returns MB directly and is already
-- per-graph, so per-org attribution is free under one-graph-per-tenant — but it
-- is a SAMPLING ESTIMATE (``SAMPLES``, default 100) that excludes per-graph /
-- Redis-key overhead. These columns put the reading, its observed RANGE and the
-- ``SAMPLES``/``repeats`` it was taken with on the SAME per-(org, period) ledger
-- the ask/capture lanes use. The precision travels WITH the number.
--
-- NOT A BILLING CHANGE (#5331 is measurement only). These columns are a
-- MEASUREMENT, never a price, cap, tier or entitlement. ``metering_cohort_spend``
-- and ``get_cohort_spend_usd`` read ONLY ``ask_cost_usd`` + ``capture_cost_usd``
-- and are NOT touched here, so the spend ceiling cannot see these columns at all
-- — untouched by construction, not by convention.
--
-- A GAUGE, NOT AN INCREMENT. The other lanes are cumulative workload. Graph
-- storage is a current-state measurement, so the RPC OVERWRITES these columns
-- (the latest reading in the window wins) instead of adding to them: summing two
-- readings of the same graph would double-count the same bytes.
--
-- ⛔ DEPLOY ORDER: this migration FIRST, the Python second. The RPC is
-- best-effort by contract — a missing function is logged at WARNING and the
-- write is dropped, while the READER degrades to a zero view. Deploying the code
-- first therefore yields a SILENT ZERO (not an error), which is precisely the
-- fail-open this ledger exists to avoid. (Same operational note the
-- 20260918000001 / 20260925000003 migrations carry.)

ALTER TABLE public.metering_records
    ADD COLUMN IF NOT EXISTS graph_storage_mb double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS graph_storage_indices_mb double precision,
    ADD COLUMN IF NOT EXISTS graph_storage_samples integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS graph_storage_repeats integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS graph_storage_min_mb double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS graph_storage_max_mb double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS graph_storage_spread_mb double precision NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS graph_storage_measured_at timestamptz;

-- ⛔ EVERY NUMERIC COLUMN ABOVE IS ``NOT NULL DEFAULT 0`` **EXCEPT**
-- ``graph_storage_indices_mb``, which is deliberately NULLABLE with no default
-- (the engine may not report an index share at all, and "not reported" must
-- stay distinguishable from "reported as zero" — the same distinction this
-- meter exists to keep). The exception is stated HERE, where the invariant is,
-- because a reader who trusts the rule and does arithmetic on
-- ``graph_storage_indices_mb`` would silently get NULL.
--
-- The NOT NULL DEFAULT is load-bearing, not tidiness. A metering row is created
-- by whichever lane writes FIRST — a plain write (``metering_increment``), an
-- ask, a capture — and those RPCs do NOT mention the graph-storage columns.
-- ``NULL + n`` is NULL in SQL, so a later lane relying on arithmetic would stick
-- at NULL forever and the reader would render 0 with no error anywhere: a
-- permanently dead figure that looks like "no graph". The gauge SETTER itself
-- does not do arithmetic, but the NOT NULL DEFAULT keeps the row self-consistent
-- for any future reader.
-- (20260917000001's ``capture_cost_usd`` carries the same reasoning.)

COMMENT ON COLUMN public.metering_records.graph_storage_mb IS
    '#5331: GRAPH.MEMORY USAGE total_graph_sz_mb for this org''s graph — the '
    'best estimate (MEDIAN of the reading''s repeats). MB. A SAMPLING ESTIMATE '
    'that excludes per-graph/Redis-key overhead; acceptable as a CAP INPUT, NOT '
    'invoice-grade.';
COMMENT ON COLUMN public.metering_records.graph_storage_indices_mb IS
    '#5331: the index share (GRAPH.MEMORY USAGE indices_sz_mb) of '
    'graph_storage_mb. NULL when the engine did not report it.';
COMMENT ON COLUMN public.metering_records.graph_storage_samples IS
    '#5331: the SAMPLES value the reading was taken with (FalkorDB averages '
    'that many nodes/edges). Stored so a reader can see the estimate''s basis.';
COMMENT ON COLUMN public.metering_records.graph_storage_repeats IS
    '#5331: how many times the command was run for this reading.';
COMMENT ON COLUMN public.metering_records.graph_storage_min_mb IS
    '#5331: the LOWEST total observed across the reading''s repeats (the bottom '
    'of the reported range).';
COMMENT ON COLUMN public.metering_records.graph_storage_max_mb IS
    '#5331: the HIGHEST total observed across the reading''s repeats (the top of '
    'the reported range).';
COMMENT ON COLUMN public.metering_records.graph_storage_spread_mb IS
    '#5331: max_mb - min_mb — the OBSERVED spread. A spread of 0 is not proof '
    'of precision; it is one more sample saying the estimate was stable.';
COMMENT ON COLUMN public.metering_records.graph_storage_measured_at IS
    '#5331: when the reading was taken (the meter''s own timestamp). The window '
    'key stays period_start; this is provenance, not the key.';

-- ============================================================================
-- The gauge-setter RPC. DROPPED before CREATE, not replaced in place: a
-- different argument list would otherwise be an OVERLOAD and leave an older
-- signature callable — the silent second path #3825 removed. The ACL is
-- re-issued after the DROP (a missing REVOKE/GRANT re-issue is how ACLs get
-- lost).
-- ============================================================================
DROP FUNCTION IF EXISTS public.metering_set_graph_storage(
    text, timestamptz, timestamptz, double precision, double precision,
    integer, integer, double precision, double precision, double precision,
    timestamptz);

CREATE FUNCTION public.metering_set_graph_storage(
    p_org_id       text,
    p_period_start timestamptz,
    p_period_end   timestamptz,
    p_total_mb     double precision,
    p_indices_mb   double precision DEFAULT NULL,
    p_samples      integer DEFAULT 100,
    p_repeats      integer DEFAULT 1,
    p_min_mb       double precision DEFAULT NULL,
    p_max_mb       double precision DEFAULT NULL,
    p_spread_mb    double precision DEFAULT 0,
    p_measured_at  timestamptz DEFAULT NULL
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
            'metering_set_graph_storage: period_end (%) must be after '
            'period_start (%)', p_period_end, p_period_start;
    END IF;
    -- ``period`` is NOT NULL with no default (0014) and period_start/period_end
    -- became NOT NULL with no default in 20260918000001, so the derived label
    -- and BOTH window edges must be supplied explicitly.
    INSERT INTO public.metering_records
        (org_id, period, period_start, period_end, graph_storage_mb,
         graph_storage_indices_mb, graph_storage_samples, graph_storage_repeats,
         graph_storage_min_mb, graph_storage_max_mb, graph_storage_spread_mb,
         graph_storage_measured_at)
    VALUES (p_org_id,
            to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM'),
            p_period_start, p_period_end, p_total_mb,
            p_indices_mb, p_samples, p_repeats,
            coalesce(p_min_mb, p_total_mb), coalesce(p_max_mb, p_total_mb),
            p_spread_mb, p_measured_at)
    ON CONFLICT (org_id, period_start)
    DO UPDATE SET
        -- ⛔ OVERWRITE, never add. This is a GAUGE: the latest reading in the
        -- window wins. ``= EXCLUDED.x`` (not ``coalesce(...) + x``) is the whole
        -- semantics — adding would double-count the same graph's bytes.
        graph_storage_mb          = EXCLUDED.graph_storage_mb,
        graph_storage_indices_mb  = EXCLUDED.graph_storage_indices_mb,
        graph_storage_samples     = EXCLUDED.graph_storage_samples,
        graph_storage_repeats     = EXCLUDED.graph_storage_repeats,
        graph_storage_min_mb      = EXCLUDED.graph_storage_min_mb,
        graph_storage_max_mb      = EXCLUDED.graph_storage_max_mb,
        graph_storage_spread_mb   = EXCLUDED.graph_storage_spread_mb,
        graph_storage_measured_at = EXCLUDED.graph_storage_measured_at,
        updated_at                = now();
END;
$$;

REVOKE ALL ON FUNCTION public.metering_set_graph_storage(
    text, timestamptz, timestamptz, double precision, double precision,
    integer, integer, double precision, double precision, double precision,
    timestamptz)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_set_graph_storage(
    text, timestamptz, timestamptz, double precision, double precision,
    integer, integer, double precision, double precision, double precision,
    timestamptz)
    TO service_role;
