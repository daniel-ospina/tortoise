-- ============================================================================
-- The per-org RESIDENCY GAUGE — an affordable per-write read of what an org
-- stores, so `postgres_storage` can be capped at all (#9559 R11).
--
-- ⛔ THE PROBLEM THIS SOLVES, MEASURED. `postgres_per_org_bytes()` is the ground
-- truth and it is a WHOLE-TABLE aggregation: a per-org `GROUP BY` with a per-row
-- `pg_column_toast_chunk_id` subquery PLUS a second full pass over the TOAST
-- relation. Calling it from the allowance gate — which runs on every gated write
-- (`points`/`documents`) — would make one tenant's write cost grow with the
-- whole database's size. That is why `postgres_storage` shipped with
-- `meter=None` and a block: no per-org figure the gate can afford.
--
-- The fix is a SNAPSHOT, not a cheaper aggregation.
--
--   * `org_postgres_residency` holds ONE row per org: the stored bytes the audit
--     attributed to it, and WHEN it measured them.
--   * `postgres_residency_snapshot()` refreshes that table from the audit. It is
--     explicitly NOT a write-path function — call it from a scheduled job.
--   * The write path reads one row by PRIMARY KEY: an index lookup, O(1) in the
--     database's size, which is the affordability the block required.
--
-- ⛔ THE STALENESS IS DECLARED, NOT HIDDEN. A snapshot describes the table as of
-- `measured_at`; the gate holds it to `STALE_AFTER_SECONDS` (allowance_gate) and
-- REFUSES on an older reading rather than capping from a figure it cannot
-- defend — the same evidence-age bound the graph gauge already uses. A snapshot
-- that has not run leaves NO row, and no row is NO READING (never a zero), which
-- the gate fails closed on.
--
-- ⛔ WHAT THIS IS NOT. It is not a per-org split of the database TOTAL: the
-- position #4492 took, and #12762 preserved, is that bytes an org's OWN rows
-- occupy are a MEASUREMENT while the rest is a labelled residual. The snapshot
-- carries only the measured per-org figure; the residual stays in the audit's
-- own report and is never apportioned.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.org_postgres_residency (
    org_id        text        PRIMARY KEY,
    -- The bytes the audit ATTRIBUTED to this org's rows. Measured, never split.
    stored_bytes  bigint      NOT NULL CHECK (stored_bytes >= 0),
    -- Row count at measurement time: disclosure, so a consumer can tell a
    -- footprint that GREW from one that was re-measured differently.
    rows          bigint      NOT NULL CHECK (rows >= 0),
    -- When the audit measured these bytes. The gate ages the reading from THIS
    -- instant, not from when the row was written.
    measured_at   timestamptz NOT NULL,
    -- Which instrument produced it. A future incremental writer must be able to
    -- say so rather than silently passing as the audit.
    source        text        NOT NULL DEFAULT 'audit',
    updated_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.org_postgres_residency IS
    'Per-org stored Postgres bytes, snapshotted from postgres_per_org_bytes() '
    'so the allowance gate can read one row on a write path (#9559 R11). '
    'Measured per-org figures only; the unattributable residual is never split.';

-- ============================================================================
-- The refresh — run OFF the write path (scheduled), never from a request.
-- ============================================================================
CREATE OR REPLACE FUNCTION public.postgres_residency_snapshot()
RETURNS TABLE (orgs_written bigint, captured_at timestamptz)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_audit   jsonb;
    v_at      timestamptz;
    v_written bigint;
BEGIN
    v_audit := public.postgres_per_org_bytes();
    v_at    := coalesce((v_audit->>'captured_at')::timestamptz,
                        pg_catalog.now());

    INSERT INTO public.org_postgres_residency
        (org_id, stored_bytes, rows, measured_at, source, updated_at)
    SELECT o->>'org_id',
           greatest(0, (o->>'row_bytes')::bigint),
           greatest(0, (o->>'rows')::bigint),
           v_at,
           'audit',
           pg_catalog.now()
      FROM jsonb_array_elements(v_audit->'orgs') AS o
    ON CONFLICT (org_id) DO UPDATE
       SET stored_bytes = EXCLUDED.stored_bytes,
           rows         = EXCLUDED.rows,
           measured_at  = EXCLUDED.measured_at,
           source       = EXCLUDED.source,
           updated_at   = EXCLUDED.updated_at;

    GET DIAGNOSTICS v_written = ROW_COUNT;

    -- ⛔ ORGS THE AUDIT NO LONGER REPORTS ARE REFUSED, NOT DELETED HERE. An
    -- org vanishing from the audit means it has no journal rows left (a real
    -- zero footprint) OR that the audit could not see it (a fault), and this
    -- function cannot tell those apart. Deleting would silently turn the second
    -- case into "no reading" while LOOKING like maintenance; leaving the row
    -- lets the gate age it out via `measured_at`. The deletion, when wanted, is
    -- an explicit operator action with the audit's output in hand.
    RETURN QUERY SELECT v_written, v_at;
END;
$$;

REVOKE ALL ON FUNCTION public.postgres_residency_snapshot()
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.postgres_residency_snapshot()
    TO service_role;

-- ============================================================================
-- Row-level security: the table is per-org data. `metering_records` (0014) is
-- the sibling convention and this matches it — the SERVICE ROLE is the only
-- reader/writer, because the gate reads it server-side on the write path.
-- ⛔ Deliberately NOT a per-end-user policy: exposing a tenant's raw stored-byte
-- figure to any authenticated session is a disclosure decision nobody has made,
-- and a policy is the wrong place to make it. Widening this is a decision, not
-- an edit.
-- ============================================================================
ALTER TABLE public.org_postgres_residency ENABLE ROW LEVEL SECURITY;

-- ⛔ THE GRANT IS THE SECOND LOCK ON THE SAME DOOR (the `node_identity`
-- convention). RLS alone would filter anon/authenticated to zero rows, but on
-- Supabase a new `public` table carries a DEFAULT grant, and a table whose only
-- protection is a policy is one `ALTER TABLE … DISABLE ROW LEVEL SECURITY` away
-- from exposing every org's footprint. Revoking the privilege means the RLS line
-- is not the sole thing standing there — measured: with RLS disabled and the
-- grant intact, anon reads every org's `stored_bytes`.
REVOKE ALL ON public.org_postgres_residency FROM anon, authenticated, public;
GRANT SELECT ON public.org_postgres_residency TO service_role;

DROP POLICY IF EXISTS org_postgres_residency_service_role_all
    ON public.org_postgres_residency;
CREATE POLICY org_postgres_residency_service_role_all
    ON public.org_postgres_residency
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
