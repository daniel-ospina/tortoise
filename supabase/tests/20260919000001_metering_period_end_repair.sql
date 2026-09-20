-- #4216 — the metering period-bound repair is EXECUTABLE and IDEMPOTENT.
--
-- Runs after every migration (the harness applies all of them, then the
-- suites), so the function under test is the one 20260919000001 shipped.
-- Seeds every anchor shape and drives the REAL repair function:
--
--   * end missing, start stored  → the end is DERIVED (the direction the
--     20260918000001 backfill lacked — a partially-written anchor);
--   * start missing, end stored  → the start is DERIVED;
--   * neither bound stored (the checkout shape), start >= end (inverted), and
--     start == end (zero-length) → reported LOUDLY (returned + WARNING), NOT
--     silently skipped, NOT invented and NOT mutated;
--   * a no-subscription org carrying one bound → untouched and not reported.
--
-- The session TimeZone is set to a DST-bearing zone so the ``AT TIME ZONE
-- 'UTC'`` normalisation is load-bearing in BOTH derivation branches: each has
-- its own DST-spanning seed (`4216-dst-start` for branch 1, `4216-dst` for
-- branch 2), because a derivation that sits inside one DST offset cancels the
-- offset and would not catch a missing normalisation.
--
-- ⚠️ Every bound comparison uses ``IS DISTINCT FROM``, never ``<>``: a bound
-- left NULL by a dropped derivation makes ``NULL <> <literal>`` evaluate to
-- NULL, which plpgsql treats as FALSE — the assertion would pass on EXACTLY the
-- failure this migration exists to catch. Every assertion RAISE EXCEPTIONs on
-- the mutation it catches, so a green schema-drill means the artifact behaves —
-- not merely that it exists.

SET TIME ZONE 'America/New_York';

INSERT INTO public.organizations
    (id, name, graph_name, subscription_id,
     current_period_start, current_period_end)
VALUES
    -- month-END start: the derived end must clamp to Feb 28.
    ('4216-end-null', '4216-end-null', 'org_4216-end-null', 'sub_end_null',
     '2026-01-31T00:00:00+00:00', NULL),
    ('4216-start-null', '4216-start-null', 'org_4216-start-null', 'sub_start_null',
     NULL, '2026-10-03T00:00:00+00:00'),
    -- DST-spanning, one per derivation branch: the exact instant proves the
    -- UTC normalisation. Under America/New_York an unnormalised derivation
    -- yields 2026-10-14T23:00Z (branch 1) / 2026-03-14T23:00Z (branch 2).
    ('4216-dst-start', '4216-dst-start', 'org_4216-dst-start', 'sub_dst_start',
     NULL, '2026-11-15T00:00:00+00:00'),
    ('4216-dst', '4216-dst', 'org_4216-dst', 'sub_dst',
     '2026-02-15T00:00:00+00:00', NULL),
    ('4216-both-null', '4216-both-null', 'org_4216-both-null', 'sub_both_null',
     NULL, NULL),
    ('4216-inverted', '4216-inverted', 'org_4216-inverted', 'sub_inverted',
     '2026-10-03T00:00:00+00:00', '2026-09-03T00:00:00+00:00'),
    -- zero-length (start == end) is refused by the resolver and must be reported.
    ('4216-empty', '4216-empty', 'org_4216-empty', 'sub_empty',
     '2026-06-01T00:00:00+00:00', '2026-06-01T00:00:00+00:00'),
    -- no subscription → must never be repaired from a single stray bound.
    ('4216-free-one-bound', '4216-free-one-bound', 'org_4216-free-one-bound',
     NULL, '2026-05-01T00:00:00+00:00', NULL),
    -- whitespace-only subscription_id: the runtime resolver treats it as NO
    -- subscription (calendar month, cap enforceable), so the repair must not
    -- derive or report it.
    ('4216-blank-sub', '4216-blank-sub', 'org_4216-blank-sub', '   ',
     NULL, NULL),
    -- ...and one carrying a bound, so the UPDATE branches' btrim guard is
    -- exercised too (without it branch 2 would derive an end).
    ('4216-blank-sub-bound', '4216-blank-sub-bound',
     'org_4216-blank-sub-bound', '   ',
     '2026-06-01T00:00:00+00:00', NULL),
    -- branch-1 counterparts (start NULL, end set) so the OTHER UPDATE's
    -- `subscription_id`/`btrim` guard is exercised too.
    ('4216-blank-sub-end', '4216-blank-sub-end', 'org_4216-blank-sub-end', '   ',
     NULL, '2026-06-01T00:00:00+00:00'),
    ('4216-free-no-sub-end', '4216-free-no-sub-end', 'org_4216-free-no-sub-end',
     NULL, NULL, '2026-07-01T00:00:00+00:00');

-- 0b) EVERY code point the predicate claims to treat as blank, GENERATED from
--     the declared set, so the DROP direction is structural rather than a spot
--     check. ``cp`` list = ``str.isspace()``: ASCII whitespace, U+001C–U+001F,
--     U+0085, U+00A0, U+1680, U+2000–U+200A, U+2028, U+2029, U+202F, U+205F,
--     U+3000. Three shapes per code point, so BOTH derivation branches' blank
--     guards are exercised across the whole set: no bounds, end only (branch
--     1), and start only (branch 2).
INSERT INTO public.organizations
    (id, name, graph_name, subscription_id,
     current_period_start, current_period_end)
SELECT '4216-blank-cp-' || cp, '4216-blank-cp-' || cp,
       'org_4216-blank-cp-' || cp, chr(cp), NULL, NULL
  FROM unnest(ARRAY[9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,
                    8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,
                    8287,12288]) AS t(cp);

-- ...carrying ONLY an end (branch 1's shape: end known, start missing)...
INSERT INTO public.organizations
    (id, name, graph_name, subscription_id,
     current_period_start, current_period_end)
SELECT '4216-blank-cpe-' || cp, '4216-blank-cpe-' || cp,
       'org_4216-blank-cpe-' || cp, chr(cp),
       NULL, '2026-06-01T00:00:00+00:00'
  FROM unnest(ARRAY[9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,
                    8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,
                    8287,12288]) AS t(cp);

-- ...and ONLY a start (branch 2's shape: start known, end missing). Both are
-- needed: each guards on the blank predicate in its OWN UPDATE, so covering
-- one direction leaves the other's guard blind to non-ASCII blanks.
INSERT INTO public.organizations
    (id, name, graph_name, subscription_id,
     current_period_start, current_period_end)
SELECT '4216-blank-cps-' || cp, '4216-blank-cps-' || cp,
       'org_4216-blank-cps-' || cp, chr(cp),
       '2026-06-01T00:00:00+00:00', NULL
  FROM unnest(ARRAY[9,10,11,12,13,28,29,30,31,32,133,160,5760,8192,8193,8194,
                    8195,8196,8197,8198,8199,8200,8201,8202,8232,8233,8239,
                    8287,12288]) AS t(cp);

-- NEGATIVE controls: code points that share an encoding prefix with a blank one
-- (or are otherwise easy to mistake for whitespace) but are NOT whitespace.
-- They must STILL be treated as real subscription ids — a wrongly WIDENED
-- predicate would leave them unrepaired, the #4216 defect in the other
-- direction. (U+0080, U+180E, U+200B, U+FEFF.)
--
-- COVERAGE, stated precisely. The DROP direction is EXHAUSTIVE: every declared
-- code point has generated blank-id rows (three shapes each), so dropping any
-- one of them REDs the suite. The ADD direction is an adversarial SAMPLE, not
-- an exhaustive sweep — that belongs in a property test, not here; U+180E is
-- the specific trap (it was Unicode whitespace until Unicode 6.3 and is the
-- classic over-broad-strip bug).
INSERT INTO public.organizations
    (id, name, graph_name, subscription_id,
     current_period_start, current_period_end)
SELECT '4216-ctrl-' || cp, '4216-ctrl-' || cp, 'org_4216-ctrl-' || cp,
       chr(cp), NULL, '2026-06-01T00:00:00+00:00'
  FROM unnest(ARRAY[128, 6158, 8203, 65279]) AS t(cp);

-- 1) The repair RETURNS exactly the unusable orgs (loud, not silent).
--    Mutation caught: dropping either reported class (both-NULL / inverted /
--    zero-length) — the silent skip this issue removes — or reporting a
--    derivable org.
DO $$
DECLARE n integer; ids text; inv_reason text;
BEGIN
    SELECT count(*), string_agg(org_id, ',' ORDER BY org_id)
      INTO n, ids
      FROM public.metering_repair_period_bounds();
    IF n <> 3 THEN
        RAISE EXCEPTION 'expected exactly 3 unusable orgs, got % (%)', n, ids;
    END IF;
    IF ids IS DISTINCT FROM '4216-both-null,4216-empty,4216-inverted' THEN
        RAISE EXCEPTION 'unusable set was % — a derivable org was misreported', ids;
    END IF;
    SELECT reason INTO inv_reason
      FROM public.metering_repair_period_bounds()
     WHERE org_id = '4216-inverted';
    IF inv_reason IS NULL THEN
        RAISE EXCEPTION 'the inverted class was not returned at all';
    END IF;
    IF inv_reason NOT LIKE '%inverted%' THEN
        RAISE EXCEPTION 'the inverted class was not named: %', inv_reason;
    END IF;
END $$;

-- 2) NULL-END org → a COMPLETE, usable window (start + 1 month, month-end clamp).
--    Mutation caught: dropping the end-derivation UPDATE (the org stays
--    NULL-end — ``NULL IS DISTINCT FROM <literal>`` is TRUE, so the assertion
--    fires on exactly that misbehaviour).
DO $$
DECLARE v timestamptz; s timestamptz;
BEGIN
    SELECT current_period_end, current_period_start INTO v, s
      FROM public.organizations WHERE id = '4216-end-null';
    IF v IS DISTINCT FROM '2026-02-28T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'derived end % is not the month-end clamped start + 1 month', v;
    END IF;
    IF s IS DISTINCT FROM '2026-01-31T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'the stored start was modified: %', s;
    END IF;
END $$;

-- 3) NULL-START org → the start is derived from the known end.
--    Mutation caught: dropping the start-derivation UPDATE — the direct false
--    green round 2 found (``NULL <> <literal>`` was FALSE; ``IS DISTINCT FROM``
--    fires).
DO $$
DECLARE v timestamptz;
BEGIN
    SELECT current_period_start INTO v FROM public.organizations
     WHERE id = '4216-start-null';
    IF v IS DISTINCT FROM '2026-09-03T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'derived start % is not end - 1 month', v;
    END IF;
END $$;

-- 3b) The derivation is UTC-normalised, not session-TimeZone-dependent — in
--     BOTH branches. Mutation caught: dropping ``AT TIME ZONE 'UTC'`` in
--     branch 2 (derived end becomes 2026-03-14T23:00Z under America/New_York)
--     or in branch 1 (derived start becomes 2026-10-14T23:00Z).
DO $$
DECLARE e timestamptz; s timestamptz;
BEGIN
    SELECT current_period_end INTO e FROM public.organizations
     WHERE id = '4216-dst';
    IF e IS DISTINCT FROM '2026-03-15T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'derived end % is session-TimeZone dependent', e;
    END IF;
    SELECT current_period_start INTO s FROM public.organizations
     WHERE id = '4216-dst-start';
    IF s IS DISTINCT FROM '2026-10-15T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'derived start % is session-TimeZone dependent', s;
    END IF;
END $$;

-- 4) Unusable and free rows are NOT mutated; nothing is invented.
--    Mutation caught: substituting a calendar month (or any invented window)
--    for an unusable row; repairing a free org that has no subscription;
--    rewriting an inverted or zero-length interval rather than reporting it.
DO $$
DECLARE a timestamptz; b timestamptz;
        i_s timestamptz; i_e timestamptz;
        z_s timestamptz; z_e timestamptz;
        f_s timestamptz; f_e timestamptz;
        n_cp integer;
BEGIN
    SELECT current_period_start, current_period_end INTO a, b
      FROM public.organizations WHERE id = '4216-both-null';
    IF a IS NOT NULL OR b IS NOT NULL THEN
        RAISE EXCEPTION 'an underivable window must not be invented (got %, %)', a, b;
    END IF;
    SELECT current_period_start, current_period_end INTO i_s, i_e
      FROM public.organizations WHERE id = '4216-inverted';
    IF i_s IS DISTINCT FROM '2026-10-03T00:00:00+00:00'::timestamptz
       OR i_e IS DISTINCT FROM '2026-09-03T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'an inverted interval must be reported, not rewritten (% %)',
            i_s, i_e;
    END IF;
    SELECT current_period_start, current_period_end INTO z_s, z_e
      FROM public.organizations WHERE id = '4216-empty';
    IF z_s IS DISTINCT FROM '2026-06-01T00:00:00+00:00'::timestamptz
       OR z_e IS DISTINCT FROM '2026-06-01T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'a zero-length interval must be reported, not rewritten (% %)',
            z_s, z_e;
    END IF;
    SELECT current_period_start, current_period_end INTO f_s, f_e
      FROM public.organizations WHERE id = '4216-free-one-bound';
    IF f_s IS DISTINCT FROM '2026-05-01T00:00:00+00:00'::timestamptz
       OR f_e IS NOT NULL THEN
        RAISE EXCEPTION 'a no-subscription org must be untouched (% %)', f_s, f_e;
    END IF;
    SELECT current_period_start, current_period_end INTO f_s, f_e
      FROM public.organizations WHERE id = '4216-blank-sub';
    IF f_s IS NOT NULL OR f_e IS NOT NULL THEN
        RAISE EXCEPTION 'a blank-subscription org must be untouched (% %)', f_s, f_e;
    END IF;
    SELECT current_period_start, current_period_end INTO f_s, f_e
      FROM public.organizations WHERE id = '4216-blank-sub-bound';
    IF f_s IS DISTINCT FROM '2026-06-01T00:00:00+00:00'::timestamptz
       OR f_e IS NOT NULL THEN
        RAISE EXCEPTION 'a blank-subscription org must not be derived (% %)', f_s, f_e;
    END IF;
    SELECT current_period_start, current_period_end INTO f_s, f_e
      FROM public.organizations WHERE id = '4216-blank-sub-end';
    IF f_s IS NOT NULL
       OR f_e IS DISTINCT FROM '2026-06-01T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'a blank-subscription org must not derive a start (% %)',
            f_s, f_e;
    END IF;
    -- EVERY member of the blank set, not a spot check. A code point DROPPED
    -- from ``blank_chars`` makes its generated rows REAL subscriptions, which
    -- REDs here: an end-only row would gain a start, a start-only row would
    -- gain an end. (A both-NULL row has no derivable bound, so a dropped code
    -- point surfaces for that shape in test 1's returned set instead.)
    SELECT count(*) INTO n_cp
      FROM public.organizations
     WHERE id LIKE '4216-blank-cpe-%' AND current_period_start IS NOT NULL;
    IF n_cp <> 0 THEN
        RAISE EXCEPTION '% blank-cpe org(s) derived a start from a blank id', n_cp;
    END IF;
    SELECT count(*) INTO n_cp
      FROM public.organizations
     WHERE id LIKE '4216-blank-cps-%' AND current_period_end IS NOT NULL;
    IF n_cp <> 0 THEN
        RAISE EXCEPTION '% blank-cps org(s) derived an end from a blank id', n_cp;
    END IF;
    -- NEGATIVE controls: a code point that is NOT blank must still be repaired
    -- as a real subscription (a wrongly WIDENED predicate would skip it).
    SELECT count(*) INTO n_cp
      FROM public.organizations
     WHERE id LIKE '4216-ctrl-%'
       AND current_period_start IS DISTINCT FROM
           (current_period_end AT TIME ZONE 'UTC' - interval '1 month')
           AT TIME ZONE 'UTC';
    IF n_cp <> 0 THEN
        RAISE EXCEPTION '% non-blank control org(s) were NOT repaired — the '
                        'predicate wrongly treats them as blank', n_cp;
    END IF;
    SELECT current_period_start, current_period_end INTO f_s, f_e
      FROM public.organizations WHERE id = '4216-free-no-sub-end';
    IF f_s IS NOT NULL
       OR f_e IS DISTINCT FROM '2026-07-01T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'a no-subscription org must not derive a start (% %)',
            f_s, f_e;
    END IF;
END $$;

-- 5) IDEMPOTENT: a second run derives nothing further and still reports the
--    same three. The bound checks pin that a re-run does not MOVE or clear a
--    full anchor; the guard's primary catcher is test 1 (unguarding a branch
--    rewrites the inverted/zero-length rows and drops the unusable count).
DO $$
DECLARE n integer; end_after timestamptz; start_after timestamptz;
        start_of_end_null timestamptz;
BEGIN
    SELECT count(*) INTO n FROM public.metering_repair_period_bounds();
    IF n <> 3 THEN
        RAISE EXCEPTION 'second run reported % unusable org(s), expected 3', n;
    END IF;
    SELECT current_period_start INTO start_of_end_null
      FROM public.organizations WHERE id = '4216-end-null';
    IF start_of_end_null IS DISTINCT FROM '2026-01-31T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'an unguarded start-derivation moved a full anchor to %',
            start_of_end_null;
    END IF;
    SELECT current_period_end INTO end_after FROM public.organizations
     WHERE id = '4216-end-null';
    IF end_after IS DISTINCT FROM '2026-02-28T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'a re-run cleared or moved an already-complete bound to %',
            end_after;
    END IF;
    SELECT current_period_start INTO start_after FROM public.organizations
     WHERE id = '4216-start-null';
    IF start_after IS DISTINCT FROM '2026-09-03T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'a re-run cleared or moved an already-complete bound to %',
            start_after;
    END IF;
END $$;

-- 6) ACL — the harness's default privileges grant EXECUTE on a new public
--    function to anon/authenticated, so the migration's REVOKE is the ONLY
--    thing separating an all-tenant repair from those roles. That REVOKE is
--    the load-bearing, testable half: dropping it fails here. (The explicit
--    GRANT to service_role is NOT distinguishable from the harness/Supabase
--    default privilege — both produce service_role=X in proacl — so the
--    proacl assertion below is an invariant pin, not a mutation catcher.)
--    Mutation caught: dropping the REVOKE (anon/authenticated could repair).
DO $$
DECLARE acl text;
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
    SELECT p.proacl::text INTO acl
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'metering_repair_period_bounds';
    IF acl IS NULL OR acl NOT LIKE '%service_role=X%' OR acl LIKE '%anon=X%'
       OR acl LIKE '%authenticated=X%' THEN
        RAISE EXCEPTION 'the stored ACL does not match the artifact: %', acl;
    END IF;
END $$;

SET TIME ZONE 'UTC';

DELETE FROM public.organizations
 WHERE id IN ('4216-end-null', '4216-start-null', '4216-dst',
              '4216-dst-start', '4216-both-null', '4216-inverted',
              '4216-empty', '4216-free-one-bound', '4216-blank-sub',
              '4216-blank-sub-bound', '4216-blank-sub-end',
              '4216-free-no-sub-end')
    OR id LIKE '4216-blank-cp-%'
    OR id LIKE '4216-blank-cpe-%'
    OR id LIKE '4216-blank-cps-%'
    OR id LIKE '4216-ctrl-%';
