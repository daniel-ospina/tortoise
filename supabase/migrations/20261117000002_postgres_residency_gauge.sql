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
--     database's size, which is the affordability the block required. An org
--     with NO row pays ONE more probe — the table's newest `measured_at`, which
--     is the instant the gate ages that absence by (see the index below) — and
--     `org_postgres_residency_measured_at_idx` is what keeps THAT probe an
--     index lookup too. Without it the absent-org branch is a sequential scan
--     plus a sort over one row per org: O(orgs) on a gated write path, growing
--     with the fleet's tenant count rather than with anything the writing tenant
--     did (review finding 4 — measured: this file held 0 `CREATE INDEX`).
--
-- ⛔ THE STALENESS IS DECLARED, NOT HIDDEN. A snapshot describes the table as of
-- `measured_at`; the gate holds it to `STALE_AFTER_SECONDS` (allowance_gate) and
-- REFUSES on an older reading rather than capping from a figure it cannot
-- defend — the same evidence-age bound the graph gauge already uses. An EMPTY
-- table is NO READING (never a zero) and the gate fails closed on it; a row
-- missing for ONE org while the table is non-empty is served as a zero-BY-ABSENCE
-- stamped with the table's newest refresh — not an invented row, and not a
-- measurement of that org. A row the audit STOPS reporting is written as a real
-- measured zero (see the refresh below). Those are three different answers, and
-- `metering.get_postgres_residency` states all three.
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

-- ⛔ THE ABSENT-ORG PROBE NEEDS ITS OWN ACCESS PATH (review finding 4, P2).
-- `metering.get_postgres_residency` reads ONE row by PRIMARY KEY for an org that
-- HAS a snapshot; for an org that has none — every fresh tenant, and every org
-- the audit attributed nothing to — it asks for the table's NEWEST
-- `measured_at`, the instant the zero-by-absence is AGED by. The primary key is
-- on `org_id`, so without this index that probe is a sequential scan plus a sort
-- over one row per org: O(orgs) on the gated write path, i.e. a tenant's write
-- cost growing with the FLEET's tenant count. With it the probe is an index
-- lookup on the leading key, which is what makes the affordability claim true on
-- BOTH branches rather than only on the one with a row.
CREATE INDEX IF NOT EXISTS org_postgres_residency_measured_at_idx
    ON public.org_postgres_residency (measured_at DESC);

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
    v_zeroed  bigint := 0;
    v_held    bigint;
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

    -- ⛔ ORGS THE AUDIT NO LONGER REPORTS ARE WRITTEN AS A MEASURED ZERO — NOT
    -- LEFT FROZEN, AND NOT DELETED — but ONLY when the audit reported SOMEBODY.
    --
    -- `postgres_per_org_bytes()` groups `public.journal_event` BY `org_id`, so an
    -- org missing from a NON-EMPTY `orgs` array has no journal rows left.
    --
    -- Both earlier readings of this case were wrong:
    --
    --   * LEAVING THE ROW FREEZES ITS `measured_at`. Once it is older than
    --     `STALE_AFTER_SECONDS` the gate refuses with `QuotaCheckError` — for
    --     ever, because the write that would re-create the org's journal rows is
    --     the one being refused. That is the fresh-tenant deadlock this whole
    --     three-answer vocabulary exists to prevent (#13084/#13102), mirrored and
    --     made permanent. It is reachable: `org_postgres_residency` has NO
    --     foreign key to `organizations`, so `purge_org_record()` — which deletes
    --     the ORG and cascades to `journal_event` — leaves the residency row
    --     behind as an orphan, and the row outlives the erasure it should have
    --     followed. A reused `org_id`, or an operator journal purge that keeps the
    --     org, then meets it.
    --   * DELETING THE ROW turns the org into `absent`, which the reader serves
    --     from the table's NEWEST `measured_at`. Correct for that org — but in a
    --     database where the only org with rows is the one being zeroed, the
    --     table goes EMPTY and every org flips to `unreported`, refusing
    --     everyone.
    --
    -- Writing the zero does neither: the row keeps the table non-empty so
    -- `absent` keeps its meaning, and the fresh `measured_at` is what the gate
    -- ages.
    --
    -- ⛔ A ROW IS ONLY EVER ZEROED IF IT ALREADY EXISTS, so an org the audit has
    -- never reported is never given an INVENTED row. That distinction is what
    -- makes `absent` honest, and it is asserted in
    -- `supabase/tests/pglite/validate.mjs`.
    --
    -- ⛔⛔ AND IT RUNS ONLY WHEN THE AUDIT ATTRIBUTED SOMETHING TO SOMEBODY
    -- (review round 2 of this follow-up, P1, MEASURED). An UNGUARDED `NOT EXISTS`
    -- is TRUE for EVERY row when `orgs` is `'[]'`, and `postgres_per_org_bytes()`
    -- returns exactly that when `journal_event` holds no rows — so one refresh
    -- over an empty journal would re-stamp the WHOLE table to 0 bytes with a
    -- fresh instant. Every org would then compare as 0 against any cap, and the
    -- reader's `unreported` refusal — which exists for exactly this — could never
    -- fire, because the table never empties and nothing ever goes stale.
    -- Measured on PGlite: 28 rows zeroed in one call, one of them still holding
    -- two journal rows.
    --
    -- When the audit reports NOTHING while rows exist, the two readings — "the
    -- journal really is empty" and "the audit could not see it" — are
    -- indistinguishable HERE, so the fail-CLOSED one wins: leave the rows alone
    -- (their `measured_at` then ages out under the gate's bound, which refuses
    -- rather than serving a zero it cannot defend) and RAISE, so an operator sees
    -- it. `jsonb_array_length(NULL)` is NULL, so a malformed audit takes the same
    -- safe branch. The single-org erasure this fixes still zeroes — that is the
    -- case the harness asserts.
    IF jsonb_array_length(v_audit->'orgs') > 0 THEN
        UPDATE public.org_postgres_residency r
           SET stored_bytes = 0,
               rows         = 0,
               measured_at  = v_at,
               source       = 'audit',
               updated_at   = pg_catalog.now()
         WHERE NOT EXISTS (
             SELECT 1 FROM jsonb_array_elements(v_audit->'orgs') AS o
              WHERE o->>'org_id' = r.org_id);

        GET DIAGNOSTICS v_zeroed = ROW_COUNT;
    ELSE
        SELECT count(*) INTO v_held FROM public.org_postgres_residency;
        IF v_held > 0 THEN
            RAISE WARNING 'postgres_residency_snapshot: the audit attributed no '
                'bytes to any org while % residency row(s) exist — leaving them '
                'UNTOUCHED (their measured_at ages out and the gate then '
                'refuses) instead of writing a fleet-wide zero that would pass '
                'every cap', v_held;
        END IF;
    END IF;

    -- `orgs_written` counts every row this refresh wrote: the audit's orgs
    -- (inserted or updated) plus the orgs it no longer reports (zeroed), because
    -- both are writes that move the gauge.
    RETURN QUERY SELECT v_written + v_zeroed, v_at;
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
