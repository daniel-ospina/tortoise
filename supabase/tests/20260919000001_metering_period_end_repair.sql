-- #4216 — the metering period-bound repair is EXECUTABLE and IDEMPOTENT.
--
-- Runs after every migration (the harness applies all of them, then the
-- suites), so the function under test is the one 20260919000001 shipped.
-- Seeds the three anchor shapes and drives the REAL repair function:
--
--   * end missing, start stored  → the end is DERIVED (the direction the
--     20260918000001 backfill lacked — a checkout org's shape);
--   * start missing, end stored  → the start is DERIVED;
--   * neither bound stored       → reported LOUDLY (returned + WARNING), NOT
--     silently skipped, and NOT invented;
--   * a no-subscription org      → untouched.
--
-- Every assertion RAISE EXCEPTIONs on the mutation it catches, so a green
-- schema-drill means the artifact behaves — not merely that it exists.

INSERT INTO public.organizations
    (id, name, graph_name, subscription_id,
     current_period_start, current_period_end)
VALUES
    ('4216-end-null', '4216-end-null', 'org_4216-end-null', 'sub_end_null',
     '2026-09-03T00:00:00+00:00', NULL),
    ('4216-start-null', '4216-start-null', 'org_4216-start-null', 'sub_start_null',
     NULL, '2026-10-03T00:00:00+00:00'),
    ('4216-both-null', '4216-both-null', 'org_4216-both-null', 'sub_both_null',
     NULL, NULL),
    ('4216-free', '4216-free', 'org_4216-free', NULL, NULL, NULL);

-- 1) The repair RETURNS exactly the underivable org (loud, not silent).
--    Mutation caught: the both-NULL branch returning nothing / not existing at
--    all (the silent skip this issue removes).
DO $$
DECLARE n integer; only_id text;
BEGIN
    SELECT count(*), max(org_id) INTO n, only_id
      FROM public.metering_repair_period_bounds();
    IF n <> 1 THEN
        RAISE EXCEPTION 'expected exactly 1 underivable org, got %', n;
    END IF;
    IF only_id <> '4216-both-null' THEN
        RAISE EXCEPTION 'underivable org misreported: %', only_id;
    END IF;
END $$;

-- 2) NULL-END org → a COMPLETE, usable window (start + 1 month).
--    Mutation caught: dropping the end-derivation UPDATE (the org stays
--    NULL-end, i.e. unmeterable — the pre-#4216 behaviour).
DO $$
DECLARE v timestamptz;
BEGIN
    SELECT current_period_end INTO v FROM public.organizations
     WHERE id = '4216-end-null';
    IF v IS NULL THEN
        RAISE EXCEPTION 'NULL-end subscription org was left unmeterable';
    END IF;
    IF v <> '2026-10-03T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'derived end % is not start + 1 month', v;
    END IF;
END $$;

-- 3) NULL-START org → the start is derived from the known end.
--    Mutation caught: dropping the start-derivation UPDATE.
DO $$
DECLARE v timestamptz;
BEGIN
    SELECT current_period_start INTO v FROM public.organizations
     WHERE id = '4216-start-null';
    IF v <> '2026-09-03T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'derived start % is not end - 1 month', v;
    END IF;
END $$;

-- 4) The both-NULL org keeps NULL bounds (nothing invented) and the
--    no-subscription org is NOT touched.
--    Mutation caught: substituting a calendar month (or any invented window)
--    for an underivable row, and repairing a free org that has no period.
DO $$
DECLARE a timestamptz; b timestamptz; f_start timestamptz; f_end timestamptz;
BEGIN
    SELECT current_period_start, current_period_end INTO a, b
      FROM public.organizations WHERE id = '4216-both-null';
    IF a IS NOT NULL OR b IS NOT NULL THEN
        RAISE EXCEPTION 'an underivable window must not be invented (got %, %)',
            a, b;
    END IF;
    SELECT current_period_start, current_period_end INTO f_start, f_end
      FROM public.organizations WHERE id = '4216-free';
    IF f_start IS NOT NULL OR f_end IS NOT NULL THEN
        RAISE EXCEPTION 'a no-subscription org must be untouched';
    END IF;
END $$;

-- 5) IDEMPOTENT: a second run derives nothing further and still reports the
--    one underivable org.
--    Mutation caught: an unguarded UPDATE that shifts an already-derived bound
--    on every run (the window would drift one month per invocation).
DO $$
DECLARE n integer; end_after timestamptz;
BEGIN
    SELECT count(*) INTO n FROM public.metering_repair_period_bounds();
    IF n <> 1 THEN
        RAISE EXCEPTION 'second run reported % underivable org(s), expected 1', n;
    END IF;
    SELECT current_period_end INTO end_after FROM public.organizations
     WHERE id = '4216-end-null';
    IF end_after <> '2026-10-03T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'a re-run moved an already-complete bound to %', end_after;
    END IF;
END $$;

DELETE FROM public.organizations
 WHERE id IN ('4216-end-null', '4216-start-null', '4216-both-null', '4216-free');
