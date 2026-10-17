-- Migration 20261015000001: Postgres storage footprint + per-org analytics
-- row growth — the MEASUREMENT half of the B7 cost-coverage gap (#4492).
--
-- WHY THIS EXISTS. Two cost dimensions had NO primitive at all:
--
--   * `git grep "pg_database_size\|pg_total_relation_size\|row_count"` over
--     `tortoise/` → empty. The control plane's on-disk footprint (total and
--     per-table) was unmeasured, so the Supabase tier we must buy could only
--     be sized on guesswork.
--   * `analytics_events` row volume was counted by terminal OUTCOME with no
--     org label (`monitoring.ANALYTICS_OUTCOME_COUNT`), so the org that
--     produced the rows could not be identified from the metric and an org
--     generating disproportionate telemetry volume was indistinguishable
--     from a quiet one.
--
-- WHAT THIS ADDS. Two READ-ONLY functions:
--
--   * `postgres_storage_footprint()` — the database's total on-disk bytes and
--     a per-table breakdown (total / heap / index bytes, plus the planner's
--     live-row estimate). Readable by a person as a single query.
--   * `analytics_events_rows_by_org(p_since, p_until)` — an EXACT per-org row
--     count for `analytics_events` inside a half-open window, so an org's
--     analytics row growth is knowable.
--
-- ⛔ THIS MEASURES; IT DOES NOT PRICE. No price, tier boundary, quota,
-- entitlement or retention decision is made here — the two functions only
-- read. `product/pricing.json` is untouched, and nothing calls either function
-- from a request path.
--
-- ⛔ NO RETENTION PATH IS ADDED FOR `analytics_events`. The table is
-- append-only by trigger (`analytics_events_immutable`, migration 0004: a
-- statement-level BEFORE UPDATE OR DELETE that RAISE EXCEPTIONs, with no role
-- gate), and the owner's standing ruling is "no retention window of any
-- length" (`docs/retention-and-deletion.md`; owner decisions #3843/#3853).
-- The rows survive an org erasure deliberately, decoupled from the deleted
-- identity. This migration therefore ADDS a measurement of the growth and
-- deliberately does NOT add a prune/sweep — retention is a product/legal
-- decision, not this issue's.
--
-- WHY BOTH FUNCTIONS RETURN ONE jsonb VALUE (and not a row set). PostgREST
-- silently caps a row LIST at the deployment's `db-max-rows`, and a silently
-- short read understates a footprint (the fail-open a measurement cannot
-- have). A payload that is a single jsonb VALUE per call cannot be truncated
-- at all — the truncation mode is designed out rather than detected. This
-- mirrors the `metering_cohort_spend` / `cohort_org_ids_since` decision in
-- migration 20260917000001 for the same reason.
--
-- HONESTY. `pg_total_relation_size` is an EXACT on-disk figure. `live_rows`
-- is `pg_stat_user_tables.n_live_tup`, which is a PLANNER STATISTIC refreshed
-- by autovacuum/ANALYZE — an ESTIMATE that can lag the truth; the exact
-- per-org count is the second function's `count(*)`. Per-org BYTES do not
-- exist in Postgres (nothing records which org a byte belongs to), so no
-- per-org byte figure is offered: that split would be an ALLOCATION, not a
-- measurement. The decision and its rationale are recorded in
-- `docs/ops/postgres-storage-measurement.md`.

-- ============================================================================
-- 1. Footprint: one jsonb value — total database bytes + per-table breakdown.
-- ============================================================================
CREATE OR REPLACE FUNCTION public.postgres_storage_footprint()
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT jsonb_build_object(
        'database_bytes', pg_catalog.pg_database_size(
                              pg_catalog.current_database()),
        'captured_at',    pg_catalog.now(),
        'tables', coalesce((
            SELECT jsonb_agg(f ORDER BY (f ->> 'total_bytes')::bigint DESC)
              FROM (
                SELECT jsonb_build_object(
                           'name',        c.relname,
                           'total_bytes', pg_catalog.pg_total_relation_size(c.oid),
                           'heap_bytes',  pg_catalog.pg_relation_size(c.oid),
                           'index_bytes', pg_catalog.pg_indexes_size(c.oid),
                           'live_rows',   coalesce(s.n_live_tup, 0)
                       ) AS f
                  FROM pg_catalog.pg_class AS c
                  JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
                  LEFT JOIN pg_catalog.pg_stat_user_tables AS s
                         ON s.relid = c.oid
                 WHERE n.nspname = 'public'
                   AND c.relkind = 'r'
                   AND NOT c.relispartition
              ) AS t
        ), '[]'::jsonb)
    );
$$;

REVOKE ALL ON FUNCTION public.postgres_storage_footprint()
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.postgres_storage_footprint()
    TO service_role;

-- ============================================================================
-- 2. Per-org analytics row growth: exact `count(*)` per org in [since, until).
--    `p_until` NULL means "open-ended" (everything at/after `p_since`).
--    `sum` over an empty set is NULL → coalesce to 0; `jsonb_object_agg` over
--    an empty set is NULL → coalesce to `{}`, so a window with no rows reads
--    as a measured ZERO rather than as an absent key.
--    ⛔ NO `ORDER BY` is attached to the aggregate, deliberately: `jsonb`
--    stores an OBJECT's keys in its own canonical order (length, then
--    bytewise) and DISCARDS the aggregate's sort, so an `ORDER BY cnt DESC`
--    would be inert and would advertise a ranking the type cannot deliver.
--    `rows_by_org` is an unordered mapping; a count-ordered result would have
--    to be an ARRAY (as `cohort_org_ids_since` returns), which this function
--    deliberately is not. See `docs/ops/postgres-storage-measurement.md`.
-- ============================================================================
CREATE OR REPLACE FUNCTION public.analytics_events_rows_by_org(
    p_since timestamptz,
    p_until timestamptz DEFAULT NULL
)
RETURNS jsonb
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT jsonb_build_object(
        'since',       p_since,
        'until',       p_until,
        'total_rows',  coalesce(sum(g.cnt), 0),
        'rows_by_org', coalesce(
                           jsonb_object_agg(g.org_id, g.cnt),
                           '{}'::jsonb)
    )
      FROM (
        SELECT org_id, count(*) AS cnt
          FROM public.analytics_events
         WHERE created_at >= p_since
           AND (p_until IS NULL OR created_at < p_until)
         GROUP BY org_id
      ) AS g;
$$;

REVOKE ALL ON FUNCTION public.analytics_events_rows_by_org(timestamptz,
    timestamptz) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.analytics_events_rows_by_org(timestamptz,
    timestamptz) TO service_role;
