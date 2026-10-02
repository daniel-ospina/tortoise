-- #5331 — the graph-storage GAUGE is a gauge, and it is asserted against the
-- REAL SQL function, not against the Python fake.
--
-- WHY THIS FILE EXISTS. The branch's own review found that the central claimed
-- property — "OVERWRITE, never add" — was only exercised against
-- ``tests/fake_control_plane.py``, which the SAME pull request authored. A fake
-- that mirrors the intended semantics can never falsify them: the mutation
-- ``graph_storage_mb = EXCLUDED.graph_storage_mb`` →
-- ``= public.metering_records.graph_storage_mb + EXCLUDED.graph_storage_mb``
-- (a double-count of the same graph's bytes) left every test green. The
-- harness applies EVERY migration and then runs the suites named in
-- ``supabase/tests/pglite/validate.mjs``'s EXPLICIT ``suites`` array — that list
-- is not a glob, so a new ``.sql`` file here runs ONLY once registered there.
-- (Registering it is part of this commit; a suite file alone would have been
-- dead code that still read as coverage.) The function under test is therefore
-- the one 20260926000002 shipped.
--
-- Each DO block RAISE EXCEPTIONs on the specific mutation it catches, so a
-- green schema drill means the artifact BEHAVES — not merely that it exists.
--
-- ⚠️ Every comparison against a stored value uses ``IS DISTINCT FROM``, never
-- ``<>``: a column left NULL by a dropped assignment makes ``NULL <> <literal>``
-- evaluate to NULL, which plpgsql treats as FALSE — the assertion would pass on
-- EXACTLY the partial-write skew this file exists to catch.

INSERT INTO public.organizations
    (id, name, graph_name, subscription_id,
     current_period_start, current_period_end)
VALUES
    ('5331-gauge', '5331-gauge', 'org_5331-gauge', NULL, NULL, NULL),
    ('5331-gauge-other', '5331-gauge-other', 'org_5331-gauge-other', NULL, NULL, NULL),
    ('5331-gauge-seeded', '5331-gauge-seeded', 'org_5331-gauge-seeded', NULL, NULL, NULL);

-- 1) A ROW MINTED BY ANOTHER LANE IS UPGRADED IN PLACE, AND THAT LANE'S
--    COUNTERS SURVIVE. ``metering_increment`` does not mention the
--    graph-storage columns at all, so this is also the executable form of the
--    migration's NOT NULL DEFAULT reasoning.
--    Mutation caught: an INSERT-only implementation (no ON CONFLICT → the
--    second write raises a duplicate-key error here), and any
--    DELETE-then-INSERT that would wipe the other lane's write_ops.
SELECT public.metering_increment(
    '5331-gauge-seeded',
    '2026-09-01T00:00:00+00:00'::timestamptz,
    '2026-10-01T00:00:00+00:00'::timestamptz,
    7, 0);

--    The row exists and its graph-storage columns read 0 (not NULL) BEFORE the
--    gauge ever touches it. Mutation caught: making any of the numeric columns
--    nullable-without-default, which would render a permanently dead figure and
--    make the arithmetic below silently NULL.
DO $$
DECLARE mb double precision; smp integer;
BEGIN
    SELECT graph_storage_mb, graph_storage_samples INTO mb, smp
      FROM public.metering_records
     WHERE org_id = '5331-gauge-seeded'
       AND period_start = '2026-09-01T00:00:00+00:00'::timestamptz;
    IF mb IS DISTINCT FROM 0::double precision THEN
        RAISE EXCEPTION 'a freshly-incremented row must read graph_storage_mb=0, got %', mb;
    END IF;
    IF smp IS DISTINCT FROM 0 THEN
        RAISE EXCEPTION 'a freshly-incremented row must read graph_storage_samples=0, got %', smp;
    END IF;
END $$;

SELECT public.metering_set_graph_storage(
    '5331-gauge-seeded',
    '2026-09-01T00:00:00+00:00'::timestamptz,
    '2026-10-01T00:00:00+00:00'::timestamptz,
    12.5, 3.25, 250, 3, 11.0, 14.0, 3.0,
    '2026-09-26T12:00:00+00:00'::timestamptz);

DO $$
DECLARE ops integer; mb double precision; smp integer;
BEGIN
    SELECT write_ops INTO ops FROM public.metering_records
     WHERE org_id = '5331-gauge-seeded'
       AND period_start = '2026-09-01T00:00:00+00:00'::timestamptz;
    IF ops IS DISTINCT FROM 7 THEN
        RAISE EXCEPTION 'the gauge must not disturb the increment lane (% != 7)', ops;
    END IF;
    SELECT graph_storage_mb, graph_storage_samples INTO mb, smp
      FROM public.metering_records
     WHERE org_id = '5331-gauge-seeded'
       AND period_start = '2026-09-01T00:00:00+00:00'::timestamptz;
    IF mb IS DISTINCT FROM 12.5::double precision THEN
        RAISE EXCEPTION 'the gauge did not land on the pre-existing row (got %)', mb;
    END IF;
    IF smp IS DISTINCT FROM 250 THEN
        RAISE EXCEPTION 'the gauge did not carry its precision (got %)', smp;
    END IF;
END $$;

-- 2) THE GAUGE IS A GAUGE: a second reading in the SAME window REPLACES the
--    first. This is the assertion the fake could not make.
--    Mutation caught: ``= EXCLUDED.x`` → ``= public.metering_records.x +
--    EXCLUDED.x`` (the double-count). 20.0 + 12.5 = 32.5, which is exactly what
--    a summing implementation stores, so this REDs on that mutation and only it.
SELECT public.metering_set_graph_storage(
    '5331-gauge',
    '2026-09-01T00:00:00+00:00'::timestamptz,
    '2026-10-01T00:00:00+00:00'::timestamptz,
    12.5, 3.25, 250, 3, 11.0, 14.0, 3.0,
    '2026-09-26T12:00:00+00:00'::timestamptz);

SELECT public.metering_set_graph_storage(
    '5331-gauge',
    '2026-09-01T00:00:00+00:00'::timestamptz,
    '2026-10-01T00:00:00+00:00'::timestamptz,
    20.0, NULL, 400, 5, 18.0, 22.0, 4.0,
    '2026-09-26T18:00:00+00:00'::timestamptz);

DO $$
DECLARE mb double precision;
BEGIN
    SELECT graph_storage_mb INTO mb FROM public.metering_records
     WHERE org_id = '5331-gauge'
       AND period_start = '2026-09-01T00:00:00+00:00'::timestamptz;
    IF mb IS DISTINCT FROM 20.0::double precision THEN
        RAISE EXCEPTION
            'the latest reading must REPLACE the previous one; stored % — if '
            'this is 32.5 the setter is summing a gauge', mb;
    END IF;
END $$;

-- 3) THE SECOND READING OVERWRITES ALL EIGHT COLUMNS — including the ones it
--    supplies as NULL, and including ``measured_at``. Column-set SKEW between
--    the two substrates is the failure this pins: a value that overwrites on
--    one path and is left stale on the other reports a reading that never
--    happened.
--    Mutation caught: dropping ANY single SET assignment (the dropped column
--    keeps the first reading's value and REDs below); and
--    ``coalesce(EXCLUDED.graph_storage_indices_mb, m.graph_storage_indices_mb)``
--    instead of the plain assignment (indices would stay 3.25 instead of NULL,
--    resurrecting a stale index share the engine did not report).
DO $$
DECLARE idx double precision; smp integer; rep integer;
        lo double precision; hi double precision; sp double precision;
        at timestamptz;
BEGIN
    SELECT graph_storage_indices_mb, graph_storage_samples, graph_storage_repeats,
           graph_storage_min_mb, graph_storage_max_mb, graph_storage_spread_mb,
           graph_storage_measured_at
      INTO idx, smp, rep, lo, hi, sp, at
      FROM public.metering_records
     WHERE org_id = '5331-gauge'
       AND period_start = '2026-09-01T00:00:00+00:00'::timestamptz;
    -- A NULL-supplied reading must CLEAR a previously-set index share. This is
    -- the nullable column's own gauge semantics.
    IF idx IS NOT NULL THEN
        RAISE EXCEPTION 'a NULL indices reading must overwrite to NULL, got %', idx;
    END IF;
    IF smp IS DISTINCT FROM 400 THEN
        RAISE EXCEPTION 'graph_storage_samples was not overwritten (got %)', smp;
    END IF;
    IF rep IS DISTINCT FROM 5 THEN
        RAISE EXCEPTION 'graph_storage_repeats was not overwritten (got %)', rep;
    END IF;
    IF lo IS DISTINCT FROM 18.0::double precision THEN
        RAISE EXCEPTION 'graph_storage_min_mb was not overwritten (got %)', lo;
    END IF;
    IF hi IS DISTINCT FROM 22.0::double precision THEN
        RAISE EXCEPTION 'graph_storage_max_mb was not overwritten (got %)', hi;
    END IF;
    IF sp IS DISTINCT FROM 4.0::double precision THEN
        RAISE EXCEPTION 'graph_storage_spread_mb was not overwritten (got %)', sp;
    END IF;
    IF at IS DISTINCT FROM '2026-09-26T18:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'graph_storage_measured_at was not overwritten (got %) — '
                        'provenance would name a reading that has been replaced', at;
    END IF;
END $$;

-- 3b) OMITTING min/max stores the TOTAL for that repeat — not the previous
--     reading's envelope. A gauge carrying a stale envelope would report a
--     range the latest measurement never observed.
--     Mutation caught: ``coalesce(p_min_mb, <existing column>)``.
SELECT public.metering_set_graph_storage(
    '5331-gauge-other',
    '2026-09-01T00:00:00+00:00'::timestamptz,
    '2026-10-01T00:00:00+00:00'::timestamptz,
    30.0, 5.0, 100, 1, 25.0, 35.0, 10.0,
    '2026-09-26T12:00:00+00:00'::timestamptz);

SELECT public.metering_set_graph_storage(
    '5331-gauge-other',
    '2026-09-01T00:00:00+00:00'::timestamptz,
    '2026-10-01T00:00:00+00:00'::timestamptz,
    42.0);  -- defaults: indices NULL, min/max → the total, 1 repeat

DO $$
DECLARE lo double precision; hi double precision; sp double precision;
BEGIN
    SELECT graph_storage_min_mb, graph_storage_max_mb, graph_storage_spread_mb
      INTO lo, hi, sp
      FROM public.metering_records
     WHERE org_id = '5331-gauge-other'
       AND period_start = '2026-09-01T00:00:00+00:00'::timestamptz;
    IF lo IS DISTINCT FROM 42.0::double precision THEN
        RAISE EXCEPTION 'an omitted min must fall back to THIS reading''s total, got %', lo;
    END IF;
    IF hi IS DISTINCT FROM 42.0::double precision THEN
        RAISE EXCEPTION 'an omitted max must fall back to THIS reading''s total, got %', hi;
    END IF;
    IF sp IS DISTINCT FROM 0.0::double precision THEN
        RAISE EXCEPTION 'an omitted spread must default to 0, not a stale envelope, got %', sp;
    END IF;
END $$;

-- 4) A DEGENERATE WINDOW IS REFUSED, not stored. ``p_period_end <= p_period_start``
--    mints a row the strict-overlap cohort aggregate can never match, i.e.
--    orphan measurement that looks like it was recorded.
--    Mutation caught: dropping the ``IF p_period_end <= p_period_start`` guard.
DO $$
DECLARE raised boolean := false; n integer;
BEGIN
    BEGIN
        PERFORM public.metering_set_graph_storage(
            '5331-gauge',
            '2026-09-01T00:00:00+00:00'::timestamptz,
            '2026-09-01T00:00:00+00:00'::timestamptz,  -- end == start
            1.0);
    EXCEPTION WHEN OTHERS THEN
        raised := true;
    END;
    IF NOT raised THEN
        RAISE EXCEPTION 'a zero-length window must be refused, not stored';
    END IF;
    BEGIN
        PERFORM public.metering_set_graph_storage(
            '5331-gauge',
            '2026-10-01T00:00:00+00:00'::timestamptz,
            '2026-09-01T00:00:00+00:00'::timestamptz,  -- inverted
            1.0);
    EXCEPTION WHEN OTHERS THEN
        raised := true;
    END;
    IF raised IS NOT TRUE THEN
        RAISE EXCEPTION 'an inverted window must be refused, not stored';
    END IF;
    -- ...and the refusal must not have written anything.
    SELECT count(*) INTO n FROM public.metering_records
     WHERE org_id = '5331-gauge'
       AND (period_start >= '2026-10-01T00:00:00+00:00'::timestamptz
            OR period_end <= period_start);
    IF n <> 0 THEN
        RAISE EXCEPTION 'a refused window was persisted anyway (% row(s))', n;
    END IF;
END $$;

-- 5) ACL — the harness's default privileges grant EXECUTE on a new public
--    function to anon/authenticated, so the migration's REVOKE is the ONLY
--    thing separating an all-tenant measurement write from those roles.
--    Mutation caught: dropping the REVOKE (a browser role could write the
--    ledger). The explicit GRANT to service_role is an invariant pin, not a
--    mutation catcher — it coincides with the harness default privilege.
DO $$
DECLARE acl text;
BEGIN
    IF has_function_privilege('anon',
            'public.metering_set_graph_storage(text, timestamptz, timestamptz, '
            'double precision, double precision, integer, integer, '
            'double precision, double precision, double precision, timestamptz)',
            'EXECUTE') THEN
        RAISE EXCEPTION 'anon must NOT be able to write the graph-storage gauge';
    END IF;
    IF has_function_privilege('authenticated',
            'public.metering_set_graph_storage(text, timestamptz, timestamptz, '
            'double precision, double precision, integer, integer, '
            'double precision, double precision, double precision, timestamptz)',
            'EXECUTE') THEN
        RAISE EXCEPTION 'authenticated must NOT be able to write the graph-storage gauge';
    END IF;
    IF NOT has_function_privilege('service_role',
            'public.metering_set_graph_storage(text, timestamptz, timestamptz, '
            'double precision, double precision, integer, integer, '
            'double precision, double precision, double precision, timestamptz)',
            'EXECUTE') THEN
        RAISE EXCEPTION 'service_role MUST be able to write the graph-storage gauge';
    END IF;
    SELECT p.proacl::text INTO acl
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'metering_set_graph_storage';
    IF acl IS NULL OR acl NOT LIKE '%service_role=X%'
       OR acl LIKE '%anon=X%' OR acl LIKE '%authenticated=X%' THEN
        RAISE EXCEPTION 'the stored ACL does not match the artifact: %', acl;
    END IF;
END $$;

-- 6) THE SETTER IS THE ONLY PATH: no stale overload is left callable. The
--    migration DROPs before CREATE precisely so a changed argument list cannot
--    survive as an overload — the silent second path #3825 removed.
--    Mutation caught: replacing the DROP+CREATE with CREATE OR REPLACE on a
--    changed signature (the old signature would remain in pg_proc).
DO $$
DECLARE n integer;
BEGIN
    SELECT count(*) INTO n
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'public' AND p.proname = 'metering_set_graph_storage';
    IF n <> 1 THEN
        RAISE EXCEPTION 'expected exactly 1 metering_set_graph_storage, found % '
                        '(a stale overload is callable)', n;
    END IF;
END $$;

DELETE FROM public.organizations
 WHERE id IN ('5331-gauge', '5331-gauge-other', '5331-gauge-seeded');
