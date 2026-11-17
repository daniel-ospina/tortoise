-- 20261117000003_postgres_residency_zero_vanished.sql
--
-- ⛔ WHY THIS IS A FORWARD MIGRATION AND NOT AN EDIT OF ITS PREDECESSOR (#1095).
-- `20261117000002_postgres_residency_gauge.sql` is ALREADY MERGED (it landed with
-- #13277), so `.github/scripts/check-migration-append-only diff` refuses any `M`
-- against the base tree — and it is right to: Supabase `db push` records an
-- applied migration by its VERSION TIMESTAMP ONLY, never by content, so every
-- environment that has already run `20261117000002` would skip a corrected copy
-- of it for ever and the fix would live only in the repo and in PGlite. That is
-- the #1001 P0 the append-only rule exists for. The corrected function body
-- therefore lands HERE, as a new timestamp, and `20261117000002` is untouched.
--
-- ⛔ WHAT IT SUPERSEDES. The merged file's own comment block — "ORGS THE AUDIT NO
-- LONGER REPORTS ARE REFUSED, NOT DELETED HERE … this function cannot tell those
-- apart" — describes the behaviour this migration replaces. That comment is left
-- exactly as merged (append-only); the current statement of the rule lives below,
-- and `postgres_residency_snapshot()` is the authority either way.
--
-- ============================================================================
-- Two defects in the refresh, both measured, both fixed by the body below.
-- ============================================================================
--
-- (1) AN ORG THE AUDIT STOPS REPORTING WAS REFUSED FOR EVER (#13277 review
--     round 8, finding 1, P1). Leaving the row frozen keeps its old
--     `measured_at`; once that ages past the gate's `STALE_AFTER_SECONDS` the
--     gate refuses with `QuotaCheckError` — for ever, because the write that
--     would re-create the org's journal rows is the one being refused. That is
--     the fresh-tenant deadlock the three-answer vocabulary exists to prevent
--     (#13084/#13102), made permanent. It is REACHABLE:
--     `org_postgres_residency` has no foreign key to `organizations`, so
--     `purge_org_record()` — which deletes the ORG and cascades to
--     `journal_event` — leaves the residency row behind as an ORPHAN, and a
--     reused `org_id` (or an operator journal purge that keeps the org) then
--     meets it.
--
--     Writing a MEASURED ZERO does neither of the two wrong things: the row
--     keeps the table non-empty, so `absent` keeps its meaning (a DELETE would
--     empty a single-tenant table and flip every org to `unreported`, refusing
--     everyone), and the fresh `measured_at` is what the gate ages. A row is only
--     ever zeroed if it ALREADY EXISTS, so an org the audit has never reported
--     never gets an INVENTED row — that distinction is what keeps `absent`
--     honest.
--
-- (2) ⛔ AND IT RUNS ONLY WHEN THE AUDIT ATTRIBUTED SOMETHING TO SOMEBODY
--     (review round 2 of this follow-up, P1, MEASURED). An UNGUARDED
--     `NOT EXISTS` is TRUE for EVERY row when `orgs` is `'[]'`, and
--     `postgres_per_org_bytes()` returns exactly that when `journal_event` holds
--     no rows — so one refresh over an empty journal would re-stamp the WHOLE
--     table to 0 bytes with a FRESH instant. Every org would then compare as 0
--     against any cap, and the reader's `unreported` refusal — which exists for
--     exactly this — could never fire, because the table never empties and
--     nothing ever goes stale. Measured on PGlite: 28 rows zeroed in one call,
--     one of them still holding two journal rows.
--
--     When the audit reports NOTHING while rows exist, the two readings — "the
--     journal really is empty" and "the audit could not see it" — are
--     indistinguishable HERE, so the fail-CLOSED one wins: leave the rows alone
--     (their `measured_at` then ages out under the gate's bound, which refuses
--     rather than serving a zero it cannot defend) and RAISE WARNING, so an
--     operator sees it. `jsonb_array_length(NULL)` is NULL, so a malformed audit
--     takes the same safe branch. The single-org erasure (1) still zeroes — that
--     is the case the harness asserts.
--
-- Asserted in `supabase/tests/pglite/validate.mjs`'s `#9559` block: the vanished
-- org KEEPS its row, carries 0 bytes / 0 rows, is stamped with THIS refresh's
-- instant, and the zero does not touch a still-reported org; and a refresh over
-- an EMPTY journal (with the audit first proven to report 0 orgs) leaves a
-- still-holding row UNTOUCHED and DELETES nothing.
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

    -- Orgs the audit no longer reports: a MEASURED ZERO, never frozen and never
    -- deleted — but ONLY when the audit reported SOMEBODY (see the header).
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

-- `CREATE OR REPLACE` preserves the existing ACLs, so these two are a restated
-- invariant rather than the grant itself: the refresh is never callable by a
-- tenant. Anon EXECUTE on it is asserted against in the PGlite suite.
REVOKE ALL ON FUNCTION public.postgres_residency_snapshot()
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.postgres_residency_snapshot()
    TO service_role;
