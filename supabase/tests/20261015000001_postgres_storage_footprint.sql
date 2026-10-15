-- Assertion suite for migration 20261015000001 (#4492): the Postgres storage
-- footprint function and the per-org analytics row-growth function.
--
-- Runs against the REAL SQL functions on PGlite (the harness applies every
-- migration first), so the assertions exercise the shipped text — not a Python
-- copy of it. Every check RAISEs on failure; the harness is ON_ERROR_STOP.

-- ── Seed: known analytics rows across three orgs and BOTH window bounds ──
-- `created_at` is explicit so the window predicates are deterministic; the
-- append-only trigger permits INSERT (only UPDATE/DELETE are refused).
--
-- The seed deliberately puts a row EXACTLY ON the window's `p_until`
-- (org-b at 05-04, the boundary used below) and a row BEFORE its `p_since`
-- (org-c at 04-01). Only a seeded row ON the bound can distinguish a
-- half-open `< p_until` from an inclusive `<= p_until`: without it, a future
-- `<` → `<=` refactor would double-count every boundary row between adjacent
-- windows and this suite stayed green.
INSERT INTO public.analytics_events (org_id, event_name, created_at) VALUES
    ('4492-org-a', 'capture_graph_ops', '2026-05-01T00:00:00+00:00'),
    ('4492-org-a', 'mcp_tool_call',     '2026-05-02T00:00:00+00:00'),
    ('4492-org-a', 'mcp_tool_call',     '2026-05-10T00:00:00+00:00'),
    ('4492-org-b', 'capture_graph_ops', '2026-05-03T00:00:00+00:00'),
    ('4492-org-b', 'mcp_tool_call',     '2026-05-04T00:00:00+00:00'),
    ('4492-org-c', 'capture_graph_ops', '2026-04-01T00:00:00+00:00');

DO $$
DECLARE
    r        jsonb;
    tables   jsonb;
    ev       jsonb;
    removed  boolean := false;
BEGIN
    -- ── 1. The footprint function returns the documented shape ───────────
    r := public.postgres_storage_footprint();

    IF r IS NULL OR jsonb_typeof(r) <> 'object' THEN
        RAISE EXCEPTION '#4492: footprint did not return a jsonb object: %', r;
    END IF;
    IF (r ->> 'database_bytes') IS NULL
       OR (r ->> 'database_bytes')::bigint <= 0 THEN
        RAISE EXCEPTION '#4492: database_bytes is missing or non-positive: %',
            r ->> 'database_bytes';
    END IF;
    IF (r ->> 'captured_at') IS NULL THEN
        RAISE EXCEPTION '#4492: captured_at is missing';
    END IF;
    IF jsonb_typeof(r -> 'tables') <> 'array'
       OR jsonb_array_length(r -> 'tables') = 0 THEN
        RAISE EXCEPTION '#4492: tables is not a non-empty array: %',
            r -> 'tables';
    END IF;

    -- ── 2. The named table is present with a real byte breakdown ─────────
    SELECT t INTO tables
      FROM jsonb_array_elements(r -> 'tables') AS t
     WHERE t ->> 'name' = 'analytics_events';
    IF tables IS NULL THEN
        RAISE EXCEPTION '#4492: analytics_events is absent from the footprint';
    END IF;
    IF (tables ->> 'total_bytes')::bigint <= 0 THEN
        RAISE EXCEPTION '#4492: analytics_events total_bytes is non-positive: %',
            tables ->> 'total_bytes';
    END IF;
    -- The heap and index shares are SEPARATE fields, and the two indexes from
    -- migration 0004 make the index share non-zero (a total-only function
    -- that dropped the breakdown would pass a total_bytes check and fail here).
    IF (tables ->> 'index_bytes')::bigint <= 0 THEN
        RAISE EXCEPTION '#4492: analytics_events index_bytes is non-positive: %',
            tables ->> 'index_bytes';
    END IF;
    IF (tables ->> 'heap_bytes')::bigint <= 0 THEN
        RAISE EXCEPTION '#4492: analytics_events heap_bytes is non-positive: %',
            tables ->> 'heap_bytes';
    END IF;
    IF (tables ->> 'total_bytes')::bigint
       < (tables ->> 'heap_bytes')::bigint THEN
        RAISE EXCEPTION '#4492: total_bytes is below heap_bytes: %', tables;
    END IF;
    IF (tables ->> 'live_rows') IS NULL THEN
        RAISE EXCEPTION '#4492: live_rows is missing (must be a number, 0 ok)';
    END IF;

    -- ── 3. Per-org row growth is EXACT and windowed ──────────────────────
    ev := public.analytics_events_rows_by_org(
        '2026-05-01T00:00:00+00:00'::timestamptz,
        '2026-05-04T00:00:00+00:00'::timestamptz);

    IF (ev ->> 'total_rows')::int <> 3 THEN
        RAISE EXCEPTION '#4492: windowed total_rows is % (expected 3)',
            ev ->> 'total_rows';
    END IF;
    IF (ev -> 'rows_by_org' ->> '4492-org-a')::int <> 2
       OR (ev -> 'rows_by_org' ->> '4492-org-b')::int <> 1 THEN
        RAISE EXCEPTION '#4492: per-org split is wrong: %', ev -> 'rows_by_org';
    END IF;
    -- UPPER bound is HALF-OPEN: [05-01, 05-04) — org-b has a row EXACTLY at
    -- p_until (05-04) that must NOT be counted, so org-b reads 1 (its 05-03
    -- row), not 2. This is the ONLY assertion a seeded row on the bound can
    -- make: flipping `<` → `<=` in the function reds here.
    IF (ev -> 'rows_by_org' ->> '4492-org-b')::int <> 1 THEN
        RAISE EXCEPTION '#4492: the half-open UPPER bound is not honoured — '
            'org-b has a row exactly at p_until (05-04) and must be excluded: %',
            ev -> 'rows_by_org';
    END IF;
    -- LOWER bound: org-c's only row is 04-01, BEFORE `p_since`, so it must not
    -- appear at all. This is the LOWER-bound check (not the half-open one).
    IF (ev -> 'rows_by_org' ->> '4492-org-c') IS NOT NULL THEN
        RAISE EXCEPTION '#4492: org-c (row before p_since) leaked into the window: %',
            ev -> 'rows_by_org';
    END IF;
    -- The 05-10 row is excluded (upper bound honoured for org-a).
    IF (ev -> 'rows_by_org' ->> '4492-org-a')::int <> 2 THEN
        RAISE EXCEPTION '#4492: upper bound not honoured for org-a: %', ev;
    END IF;

    -- ── 4. `p_until` NULL is open-ended ──────────────────────────────────
    ev := public.analytics_events_rows_by_org(
        '2026-05-01T00:00:00+00:00'::timestamptz);
    IF (ev ->> 'until') IS NOT NULL THEN
        RAISE EXCEPTION '#4492: until must read null for an open window: %', ev;
    END IF;
    -- 5 = org-a 05-01/05-02/05-10, org-b 05-03/05-04 (the boundary row IS
    -- included when `p_until` is NULL).
    IF (ev ->> 'total_rows')::int <> 5 THEN
        RAISE EXCEPTION '#4492: open window total_rows is % (expected 5)',
            ev ->> 'total_rows';
    END IF;

    -- ── 5. An empty window is a measured ZERO, not an absent key ─────────
    ev := public.analytics_events_rows_by_org(
        '2020-01-01T00:00:00+00:00'::timestamptz,
        '2020-01-02T00:00:00+00:00'::timestamptz);
    IF (ev ->> 'total_rows')::int <> 0 THEN
        RAISE EXCEPTION '#4492: empty window total_rows is % (expected 0)',
            ev ->> 'total_rows';
    END IF;
    IF jsonb_typeof(ev -> 'rows_by_org') <> 'object'
       OR (ev -> 'rows_by_org') <> '{}'::jsonb THEN
        RAISE EXCEPTION '#4492: empty window rows_by_org is % (expected {})',
            ev -> 'rows_by_org';
    END IF;

    -- ── 6. Both functions are service_role-only (no customer surface) ────
    IF pg_catalog.has_function_privilege(
           'anon', 'public.postgres_storage_footprint()', 'EXECUTE')
       OR pg_catalog.has_function_privilege(
           'authenticated', 'public.postgres_storage_footprint()', 'EXECUTE') THEN
        RAISE EXCEPTION '#4492: postgres_storage_footprint is callable by a customer role';
    END IF;
    IF NOT pg_catalog.has_function_privilege(
           'service_role', 'public.postgres_storage_footprint()', 'EXECUTE') THEN
        RAISE EXCEPTION '#4492: postgres_storage_footprint is NOT callable by service_role';
    END IF;
    -- The PGlite bootstrap installs `ALTER DEFAULT PRIVILEGES ... GRANT
    -- EXECUTE ON FUNCTIONS TO anon, authenticated, service_role`, so a new
    -- function is granted to `authenticated` BY DEFAULT; the migration's
    -- REVOKE ... FROM public, anon, authenticated is the only thing that
    -- removes it. `authenticated` is the logged-in-customer role and this RPC
    -- has no org filter (it counts EVERY org), so it is the role that matters
    -- most — dropping it from that REVOKE must red here.
    IF pg_catalog.has_function_privilege(
           'anon',
           'public.analytics_events_rows_by_org(timestamptz, timestamptz)',
           'EXECUTE')
       OR pg_catalog.has_function_privilege(
           'authenticated',
           'public.analytics_events_rows_by_org(timestamptz, timestamptz)',
           'EXECUTE') THEN
        RAISE EXCEPTION '#4492: analytics_events_rows_by_org is callable by a customer role';
    END IF;
    IF NOT pg_catalog.has_function_privilege(
           'service_role',
           'public.analytics_events_rows_by_org(timestamptz, timestamptz)',
           'EXECUTE') THEN
        RAISE EXCEPTION '#4492: analytics_events_rows_by_org is NOT callable by service_role';
    END IF;

    -- ── 7. The retention finding, asserted: the table is still append-only ─
    -- This migration must NOT have introduced a prune path. The DELETE below
    -- is expected to RAISE (the migration-0004 trigger); if it ever succeeds,
    -- a retention path has been added and this suite says so.
    BEGIN
        DELETE FROM public.analytics_events WHERE org_id = '4492-org-a';
        removed := true;
    EXCEPTION
        WHEN OTHERS THEN
            IF position('append-only' in SQLERRM) = 0 THEN
                RAISE;  -- an unexpected failure is not the guard we assert
            END IF;
    END;
    IF removed THEN
        RAISE EXCEPTION '#4492: analytics_events DELETE succeeded — an '
            'append-only guard was removed, so a retention path now exists';
    END IF;
END $$;

-- Cleanup: the append-only trigger makes a DELETE impossible, so the seeded
-- rows stay. That is deliberate and is itself the finding this issue records
-- (there is no retention path); the suite runs LAST in validate.mjs, so the
-- residue cannot affect another suite.
