-- #4216 — the metering period-bound repair is EXECUTABLE and IDEMPOTENT.
--
-- Runs after every migration (the harness applies all of them, then the
-- suites), so the function under test is the one 20260919000001 shipped.
-- Seeds every anchor shape and drives the REAL repair function:
--
--   * end missing, start stored  → the end is DERIVED (the direction the
--     20260918000001 backfill lacked — a partially-written anchor);
--   * start missing, end stored  → the start is DERIVED;
--   * neither bound stored (the checkout shape) and start >= end (inverted)
--     → reported LOUDLY (returned + WARNING), NOT silently skipped, NOT
--     invented and NOT mutated;
--   * a no-subscription org carrying one bound → untouched and not reported.
--
-- The session TimeZone is set to a DST-bearing zone so the ``AT TIME ZONE
-- 'UTC'`` normalisation is load-bearing: without it the derived instant drifts
-- by an hour across the DST boundary. Every assertion RAISE EXCEPTIONs on the
-- mutation it catches, so a green schema-drill means the artifact behaves —
-- not merely that it exists.

SET TIME ZONE 'America/New_York';

INSERT INTO public.organizations
    (id, name, graph_name, subscription_id,
     current_period_start, current_period_end)
VALUES
    -- month-END start: the derived end must clamp to Feb 28 and must NOT drift
    -- on a re-run (an unguarded `+1 month` would move it to Mar 28).
    ('4216-end-null', '4216-end-null', 'org_4216-end-null', 'sub_end_null',
     '2026-01-31T00:00:00+00:00', NULL),
    ('4216-start-null', '4216-start-null', 'org_4216-start-null', 'sub_start_null',
     NULL, '2026-10-03T00:00:00+00:00'),
    -- DST-spanning: the exact instant proves the UTC normalisation.
    ('4216-dst', '4216-dst', 'org_4216-dst', 'sub_dst',
     '2026-02-15T00:00:00+00:00', NULL),
    ('4216-both-null', '4216-both-null', 'org_4216-both-null', 'sub_both_null',
     NULL, NULL),
    ('4216-inverted', '4216-inverted', 'org_4216-inverted', 'sub_inverted',
     '2026-10-03T00:00:00+00:00', '2026-09-03T00:00:00+00:00'),
    -- no subscription → must never be repaired from a single stray bound.
    ('4216-free-one-bound', '4216-free-one-bound', 'org_4216-free-one-bound',
     NULL, '2026-05-01T00:00:00+00:00', NULL);

-- 1) The repair RETURNS exactly the unusable orgs (loud, not silent).
--    Mutation caught: dropping either reported class (both-NULL / inverted) —
--    the silent skip this issue removes — or reporting a derivable org.
DO $$
DECLARE n integer; ids text; inv_reason text;
BEGIN
    SELECT count(*), string_agg(org_id, ',' ORDER BY org_id)
      INTO n, ids
      FROM public.metering_repair_period_bounds();
    IF n <> 2 THEN
        RAISE EXCEPTION 'expected exactly 2 unusable orgs, got % (%)', n, ids;
    END IF;
    IF ids <> '4216-both-null,4216-inverted' THEN
        RAISE EXCEPTION 'unusable set was % — a derivable org was misreported', ids;
    END IF;
    SELECT reason INTO inv_reason
      FROM public.metering_repair_period_bounds()
     WHERE org_id = '4216-inverted';
    IF inv_reason NOT LIKE '%inverted%' THEN
        RAISE EXCEPTION 'the inverted class was not named: %', inv_reason;
    END IF;
END $$;

-- 2) NULL-END org → a COMPLETE, usable window (start + 1 month, month-end clamp).
--    Mutation caught: dropping the end-derivation UPDATE (the org stays
--    NULL-end, i.e. unmeterable — the pre-#4216 behaviour).
DO $$
DECLARE v timestamptz; s timestamptz;
BEGIN
    SELECT current_period_end, current_period_start INTO v, s
      FROM public.organizations WHERE id = '4216-end-null';
    IF v IS NULL THEN
        RAISE EXCEPTION 'NULL-end subscription org was left unmeterable';
    END IF;
    IF v <> '2026-02-28T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'derived end % is not the month-end clamped start + 1 month', v;
    END IF;
    IF s <> '2026-01-31T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'the stored start was modified: %', s;
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

-- 3b) The derivation is UTC-normalised, not session-TimeZone-dependent.
--     Mutation caught: dropping either ``AT TIME ZONE 'UTC'`` — under
--     America/New_York the derived end becomes 2026-03-14T23:00Z.
DO $$
DECLARE v timestamptz;
BEGIN
    SELECT current_period_end INTO v FROM public.organizations
     WHERE id = '4216-dst';
    IF v <> '2026-03-15T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'derived end % is session-TimeZone dependent', v;
    END IF;
END $$;

-- 4) Unusable and free rows are NOT mutated; nothing is invented.
--    Mutation caught: substituting a calendar month (or any invented window)
--    for an unusable row; repairing a free org that has no subscription;
--    rewriting an inverted interval rather than reporting it.
DO $$
DECLARE a timestamptz; b timestamptz;
        i_s timestamptz; i_e timestamptz;
        f_s timestamptz; f_e timestamptz;
BEGIN
    SELECT current_period_start, current_period_end INTO a, b
      FROM public.organizations WHERE id = '4216-both-null';
    IF a IS NOT NULL OR b IS NOT NULL THEN
        RAISE EXCEPTION 'an underivable window must not be invented (got %, %)', a, b;
    END IF;
    SELECT current_period_start, current_period_end INTO i_s, i_e
      FROM public.organizations WHERE id = '4216-inverted';
    IF i_s <> '2026-10-03T00:00:00+00:00'::timestamptz
       OR i_e <> '2026-09-03T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'an inverted interval must be reported, not rewritten (% %)',
            i_s, i_e;
    END IF;
    SELECT current_period_start, current_period_end INTO f_s, f_e
      FROM public.organizations WHERE id = '4216-free-one-bound';
    IF f_s <> '2026-05-01T00:00:00+00:00'::timestamptz OR f_e IS NOT NULL THEN
        RAISE EXCEPTION 'a no-subscription org must be untouched (% %)', f_s, f_e;
    END IF;
END $$;

-- 5) IDEMPOTENT: a second run derives nothing further and still reports the
--    same two; an already-derived month-end bound must not drift.
--    Mutation caught: an unguarded UPDATE that shifts an already-derived bound
--    on every run (the window would drift a month per invocation).
DO $$
DECLARE n integer; end_after timestamptz; start_after timestamptz;
BEGIN
    SELECT count(*) INTO n FROM public.metering_repair_period_bounds();
    IF n <> 2 THEN
        RAISE EXCEPTION 'second run reported % unusable org(s), expected 2', n;
    END IF;
    SELECT current_period_end INTO end_after FROM public.organizations
     WHERE id = '4216-end-null';
    IF end_after <> '2026-02-28T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'a re-run moved an already-complete bound to %', end_after;
    END IF;
    SELECT current_period_start INTO start_after FROM public.organizations
     WHERE id = '4216-start-null';
    IF start_after <> '2026-09-03T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'a re-run moved an already-complete bound to %', start_after;
    END IF;
END $$;

-- 6) ACL — the harness's default privileges grant EXECUTE on a new public
--    function to anon/authenticated, so the migration's REVOKE is the ONLY
--    thing separating an all-tenant repair from those roles. Pinned here.
--    Mutation caught: dropping the REVOKE (anon/authenticated could repair).
DO $$
BEGIN
    IF has_function_privilege(
            'anon', 'public.metering_repair_period_bounds()', 'EXECUTE') THEN
        RAISE EXCEPTION 'anon must NOT be able to execute the repair';
    END IF;
    IF has_function_privilege(
            'authenticated', 'public.metering_repair_period_bounds()', 'EXECUTE') THEN
        RAISE EXCEPTION 'authenticated must NOT be able to execute the repair';
    END IF;
    IF NOT has_function_privilege(
            'service_role', 'public.metering_repair_period_bounds()', 'EXECUTE') THEN
        RAISE EXCEPTION 'service_role MUST be able to execute the repair';
    END IF;
END $$;

SET TIME ZONE 'UTC';

DELETE FROM public.organizations
 WHERE id IN ('4216-end-null', '4216-start-null', '4216-dst',
              '4216-both-null', '4216-inverted', '4216-free-one-bound');
