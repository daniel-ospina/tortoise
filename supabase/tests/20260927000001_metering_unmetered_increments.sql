-- #4779 — a dropped increment is DISTINGUISHABLE from a zero increment.
--
-- Runs after every migration, so the table/functions under test are the ones
-- 20260927000001 shipped. This suite is the SQL half of the issue's central
-- acceptance criterion: it must FAIL if the representation cannot tell "this
-- org's increments were dropped" apart from "this org spent nothing".
--
-- The distinguisher is stated as a PAIR of observables over the SAME org:
--   * ``metering_unmetered_total``  -> N > 0   (the drop is on record)
--   * ``metering_cohort_spend``     -> 0.0     (the ledger legitimately reads
--                                               zero — the count is NOT spend)
-- ...and a CONTROL org that genuinely never dropped reads (0, 0.0). Before this
-- migration the dropped org's pair was also (—, 0.0): identical to the control.
-- The mutation "these two are one number" therefore REDs here.
--
-- Every assertion RAISE EXCEPTIONs on the mutation it catches, so a green
-- schema-drill means the artifact BEHAVES, not merely that it exists.

-- ============================================================================
-- 0) Seeds. Two orgs that drop (one subscription-anchored, one with real
--    ledger spend too), one control org that never drops.
-- ============================================================================
INSERT INTO public.organizations
    (id, name, graph_name, subscription_id,
     current_period_start, current_period_end)
VALUES
    ('4779-dropped', '4779-dropped', 'org_4779-dropped', 'sub_4779_inverted',
     '2026-10-03T00:00:00+00:00', '2026-09-03T00:00:00+00:00'),
    ('4779-both', '4779-both', 'org_4779-both', 'sub_4779_inverted_2',
     '2026-10-03T00:00:00+00:00', '2026-09-03T00:00:00+00:00'),
    ('4779-control', '4779-control', 'org_4779-control', NULL, NULL, NULL),
    ('4779-free', '4779-free', 'org_4779-free', NULL, NULL, NULL);

-- 4779-both also carries REAL ledger spend for the window, so the "the count
-- must not leak into the SUM" assertion below is about a row that exists.
SELECT public.metering_increment_ask(
    '4779-both', '2026-09-03T00:00:00+00:00', '2026-10-03T00:00:00+00:00',
    3, 100, 200, 12.5);

-- ============================================================================
-- 1) THE WRITER: an atomic, cumulative count with a preserved since-when.
--    Mutation caught: dropping the ON CONFLICT clause (the second drop would
--    raise a duplicate key instead of accumulating); recording ``p_n`` as a
--    constant instead of adding.
-- ============================================================================
DO $$
DECLARE v integer; n integer;
BEGIN
    v := public.metering_record_unmetered(
        '4779-dropped', 'write_op', 'window_unresolvable', 'QuotaCheckError');
    IF v IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'first drop returned %, expected 1', v;
    END IF;
    v := public.metering_record_unmetered(
        '4779-dropped', 'write_op', 'window_unresolvable', 'QuotaCheckError');
    IF v IS DISTINCT FROM 2 THEN
        RAISE EXCEPTION 'second drop returned %, expected the cumulative 2', v;
    END IF;
    v := public.metering_record_unmetered(
        '4779-dropped', 'ask_ledger', 'window_unresolvable', 'QuotaCheckError',
        p_n := 5);
    IF v IS DISTINCT FROM 5 THEN
        RAISE EXCEPTION 'p_n=5 batch recorded as %', v;
    END IF;
    SELECT count(*) INTO n FROM public.metering_unmetered_increments
     WHERE org_id = '4779-dropped';
    IF n <> 2 THEN
        RAISE EXCEPTION 'expected 2 rows (one per lane), got %', n;
    END IF;
END $$;

-- 2) ``last_observed_at`` ADVANCES on every drop and ``first_observed_at`` is
--    PRESERVED. Seeded synthetically because ``now()`` is the transaction
--    timestamp and therefore identical for every statement in this file.
--    Mutations caught: dropping ``last_observed_at = now()`` from the upsert
--    (it stays at the synthetic old value); adding ``first_observed_at = now()``
--    to it (the since-when is rewritten on every drop, destroying the answer to
--    "how long has this been happening").
DO $$
DECLARE f0 timestamptz; l0 timestamptz; f1 timestamptz; l1 timestamptz;
BEGIN
    UPDATE public.metering_unmetered_increments
       SET first_observed_at = now() - interval '1 day',
           last_observed_at  = now() - interval '1 hour'
     WHERE org_id = '4779-dropped' AND lane = 'write_op';
    SELECT first_observed_at, last_observed_at INTO f0, l0
      FROM public.metering_unmetered_increments
     WHERE org_id = '4779-dropped' AND lane = 'write_op';
    PERFORM public.metering_record_unmetered(
        '4779-dropped', 'write_op', 'window_unresolvable', 'QuotaCheckError');
    SELECT first_observed_at, last_observed_at INTO f1, l1
      FROM public.metering_unmetered_increments
     WHERE org_id = '4779-dropped' AND lane = 'write_op';
    IF f1 IS DISTINCT FROM f0 THEN
        RAISE EXCEPTION 'first_observed_at was rewritten by a later drop (% -> %)',
            f0, f1;
    END IF;
    IF l1 IS NOT DISTINCT FROM l0 OR l1 <= l0 THEN
        RAISE EXCEPTION 'last_observed_at did not advance (% -> %)', l0, l1;
    END IF;
END $$;

-- ============================================================================
-- 3) THE DECLARED VOCABULARY. ``drop_class`` is CLOSED, and the writer refuses
--    a zero/negative count. An ad-hoc class string would re-create the
--    re-derivation defect at the representation layer, so it is a migration
--    rather than a literal.
--    Mutation caught: dropping the CHECK (an undeclared class is accepted);
--    dropping the p_n guard (a zero count is recorded — the exact state this
--    table exists to distinguish); reverting ``increments`` to ``DEFAULT 0``
--    (the direct-insert probe below then trips the positive CHECK); accepting an
--    explicit NULL p_n (which the Python lane refuses too); a bare ``btrim(x)``
--    for the blank guard (a TAB-only lane is then written here but refused on the
--    embedded lane).
--    The probe org below exists only for the two DEFAULT checks and is removed
--    with the records (the surface is FK-keyed to ``organizations``, so neither
--    can be written without the other).
-- ============================================================================
DO $$
DECLARE rejected boolean; v_n integer;
BEGIN
    -- This probe org exists only for the two DEFAULT probes below, and is
    -- removed with those records (the surface is FK-keyed to ``organizations``,
    -- so neither can be written without the other).
    INSERT INTO public.organizations (id, name, graph_name)
    VALUES ('4779-default-probe', '4779-default-probe', 'org_4779-default-probe')
    ON CONFLICT (id) DO NOTHING;
    rejected := false;
    BEGIN
        PERFORM public.metering_record_unmetered(
            '4779-dropped', 'write_op', 'bogus_class', 'X');
    EXCEPTION WHEN check_violation THEN rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'an UNDECLARED drop_class was accepted';
    END IF;

    rejected := false;
    BEGIN
        PERFORM public.metering_record_unmetered(
            '4779-dropped', 'write_op', 'window_unresolvable', 'X', p_n := 0);
    EXCEPTION WHEN raise_exception THEN rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'a ZERO increment was accepted (indistinguishable from zero)';
    END IF;

    rejected := false;
    BEGIN
        PERFORM public.metering_record_unmetered(
            '', 'write_op', 'window_unresolvable', 'X');
    EXCEPTION WHEN raise_exception THEN rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'an EMPTY org_id was accepted';
    END IF;

    rejected := false;
    BEGIN
        PERFORM public.metering_record_unmetered(
            '4779-dropped', '   ', 'window_unresolvable', 'X');
    EXCEPTION WHEN raise_exception THEN rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'a blank lane was accepted';
    END IF;

    -- The blank test is Python's own whitespace set (``str.isspace()``), mirrored
    -- by the migration's ``blank_chars`` — both keys, one comparison. A bare
    -- ``btrim(x)`` (ASCII spaces only) used to accept a TAB-only lane here while
    -- the embedded lane refused it: two modes disagreeing about whether the
    -- record exists. The embedded half is pinned by
    -- ``test_a_unicode_whitespace_lane_is_refused_like_the_sql_lane`` and the set
    -- itself by ``test_the_blank_set_is_pythons_exact_whitespace_set``.
    rejected := false;
    BEGIN
        PERFORM public.metering_record_unmetered(
            '4779-dropped', E'\t', 'window_unresolvable', 'X');
    EXCEPTION WHEN raise_exception THEN rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'a TAB-only lane was accepted (the embedded lane refuses it)';
    END IF;

    -- ...including NBSP and the other Unicode spaces, which the ASCII-only
    -- ``btrim(x)`` also let through.
    rejected := false;
    BEGIN
        PERFORM public.metering_record_unmetered(
            '4779-dropped', chr(160), 'window_unresolvable', 'X');
    EXCEPTION WHEN raise_exception THEN rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'an NBSP-only lane was accepted (the embedded lane refuses it)';
    END IF;

    rejected := false;
    BEGIN
        PERFORM public.metering_record_unmetered(
            E'\n', 'write_op', 'window_unresolvable', 'X');
    EXCEPTION WHEN raise_exception THEN rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'a CR/LF-only org_id was accepted';
    END IF;

    rejected := false;
    BEGIN
        PERFORM public.metering_record_unmetered(
            chr(160), 'write_op', 'window_unresolvable', 'X');
    EXCEPTION WHEN raise_exception THEN rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'a whitespace-only org_id was accepted';
    END IF;

    -- p_n OMITTED -> ONE increment (the FUNCTION's ``p_n DEFAULT 1``); it is not
    -- "zero", which is the state this whole surface exists to distinguish from
    -- a drop.
    PERFORM public.metering_record_unmetered(
        '4779-default-probe', 'write_op', 'window_unresolvable', 'X');
    SELECT m.increments INTO v_n
      FROM public.metering_unmetered_increments AS m
     WHERE m.org_id = '4779-default-probe' AND m.lane = 'write_op';
    IF v_n IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'an omitted p_n recorded % instead of the DEFAULT 1', v_n;
    END IF;

    -- ...and the COLUMN's ``DEFAULT 1``, which the RPC never exercises (it always
    -- passes p_n explicitly), asserted by a DIRECT insert that omits
    -- ``increments``. Reverted to ``DEFAULT 0`` this INSERT trips the positive
    -- CHECK — a column default that contradicts the table's own constraint.
    INSERT INTO public.metering_unmetered_increments
        (org_id, lane, drop_class, last_error_type)
    VALUES ('4779-default-probe', 'direct_insert', 'window_unresolvable', 'X');
    SELECT m.increments INTO v_n
      FROM public.metering_unmetered_increments AS m
     WHERE m.org_id = '4779-default-probe' AND m.lane = 'direct_insert';
    IF v_n IS DISTINCT FROM 1 THEN
        RAISE EXCEPTION 'the column DEFAULT recorded % instead of 1', v_n;
    END IF;

    DELETE FROM public.metering_unmetered_increments
     WHERE org_id = '4779-default-probe';
    DELETE FROM public.organizations WHERE id = '4779-default-probe';

    -- ...and an EXPLICIT NULL is refused rather than silently defaulted: a
    -- caller that computed a batch size and got NULL has a bug, and defaulting
    -- it to 1 would hide an unknown number of lost increments behind a 1.
    rejected := false;
    BEGIN
        PERFORM public.metering_record_unmetered(
            '4779-dropped', 'write_op', 'window_unresolvable', 'X', p_n := NULL);
    EXCEPTION WHEN raise_exception THEN rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'an explicit NULL p_n was accepted';
    END IF;
END $$;

-- ============================================================================
-- 4) THE DISTINGUISHER — the acceptance criterion, in SQL.
--    A dropped org and a genuinely-zero control org must NOT read the same.
-- ============================================================================
DO $$
DECLARE dropped_total bigint; control_total bigint;
        dropped_spend double precision; control_spend double precision;
        start_ts timestamptz := '2026-09-03T00:00:00+00:00';
        end_ts   timestamptz := '2026-10-03T00:00:00+00:00';
BEGIN
    dropped_total := public.metering_unmetered_total(ARRAY['4779-dropped']);
    control_total := public.metering_unmetered_total(ARRAY['4779-control']);
    dropped_spend := public.metering_cohort_spend(
        ARRAY['4779-dropped'], start_ts, end_ts);
    control_spend := public.metering_cohort_spend(
        ARRAY['4779-control'], start_ts, end_ts);

    IF dropped_total <= 0 THEN
        RAISE EXCEPTION 'the dropped org reads as ZERO unmetered increments (%) — '
                        'indistinguishable from an org that spent nothing',
            dropped_total;
    END IF;
    IF control_total <> 0 THEN
        RAISE EXCEPTION 'the control org (no drops) reads % unmetered increments',
            control_total;
    END IF;
    IF dropped_spend IS DISTINCT FROM 0.0 THEN
        RAISE EXCEPTION 'the unmetered count leaked into cohort spend: %',
            dropped_spend;
    END IF;
    IF control_spend IS DISTINCT FROM 0.0 THEN
        RAISE EXCEPTION 'the control org''s spend is %, expected 0.0', control_spend;
    END IF;
    -- THE PAIR: identical spend, DIFFERENT representation. This is the whole
    -- point, and it is what was not expressible before this migration.
    IF dropped_spend = control_spend AND dropped_total = control_total THEN
        RAISE EXCEPTION 'a dropped org is INDISTINGUISHABLE from a zero org '
                        '(spend=%, unmetered=%)', dropped_spend, dropped_total;
    END IF;
END $$;

-- 5) THE COUNT IS NOT SPEND, even for an org that has BOTH.
--    Mutation caught: joining ``metering_unmetered_increments`` into
--    ``metering_cohort_spend`` (the spend becomes 12.5 + the count), or adding
--    an ``increments`` column to that SUM. #4779's constraint 1.
DO $$
DECLARE spend double precision; unmetered bigint;
BEGIN
    PERFORM public.metering_record_unmetered(
        '4779-both', 'capture_ledger', 'window_unresolvable', 'QuotaCheckError',
        p_n := 7);
    spend := public.metering_cohort_spend(ARRAY['4779-both'],
        '2026-09-03T00:00:00+00:00', '2026-10-03T00:00:00+00:00');
    unmetered := public.metering_unmetered_total(ARRAY['4779-both']);
    IF spend IS DISTINCT FROM 12.5 THEN
        RAISE EXCEPTION 'cohort spend is % — expected the ask cost ONLY (12.5); '
                        'the unmetered count must never be a cap input', spend;
    END IF;
    IF unmetered <> 7 THEN
        RAISE EXCEPTION 'unmetered total is %, expected 7', unmetered;
    END IF;
END $$;

-- ============================================================================
-- 6) THE READERS. ``metering_unmetered_for_org`` is a bounded ROW read — bounded
--    by the CALLERS' lane inventory (six swallow sites x two declared classes),
--    NOT by the schema, since ``lane`` is deliberately unconstrained — which is
--    why it can be a row read at all; ``metering_unmetered_total`` is a SINGLE
--    scalar, so db-max-rows cannot truncate the cohort answer.
--    Mutation caught: returning the cohort total per row instead of one scalar;
--    a NULL-tolerant empty/NULL-cohort check (``total <> 0`` is NULL, not TRUE,
--    when the function returns NULL — so the assertion must be NULL-safe).
-- ============================================================================
DO $$
DECLARE n integer; total bigint;
BEGIN
    SELECT count(*) INTO n FROM public.metering_unmetered_for_org('4779-dropped');
    IF n <> 2 THEN
        RAISE EXCEPTION 'expected 2 rows for 4779-dropped, got %', n;
    END IF;
    SELECT count(*) INTO n FROM public.metering_unmetered_for_org('4779-both');
    IF n <> 1 THEN
        RAISE EXCEPTION 'expected 1 row for 4779-both, got %', n;
    END IF;
    SELECT count(*) INTO n FROM public.metering_unmetered_for_org('4779-control');
    IF n <> 0 THEN
        RAISE EXCEPTION 'the control org has % representation row(s)', n;
    END IF;
    -- The reader carries the diagnostic payload, not just a number.
    SELECT count(*) INTO n FROM public.metering_unmetered_for_org('4779-dropped')
     WHERE last_error_type = 'QuotaCheckError' AND first_observed_at IS NOT NULL
       AND last_observed_at IS NOT NULL;
    IF n <> 2 THEN
        RAISE EXCEPTION 'the reader dropped the diagnostic payload (rows=%)', n;
    END IF;
    -- The cohort scalar aggregates BOTH orgs and ignores an unknown org id.
    -- 4779-dropped: write_op window_unresolvable 3 (steps 1 + 2) + ask_ledger 5
    -- (step 1) = 8; 4779-both: capture_ledger 7 (step 5). An unknown org adds 0.
    total := public.metering_unmetered_total(
        ARRAY['4779-dropped', '4779-both', '4779-nonexistent']);
    IF total <> 15 THEN
        RAISE EXCEPTION 'cohort total is %, expected 8 + 7 = 15', total;
    END IF;
    total := public.metering_unmetered_total(ARRAY[]::text[]);
    IF total IS DISTINCT FROM 0 THEN
        RAISE EXCEPTION 'an empty cohort reads %, expected 0', total;
    END IF;
    total := public.metering_unmetered_total(NULL);
    IF total IS DISTINCT FROM 0 THEN
        RAISE EXCEPTION 'a NULL cohort reads %, expected 0', total;
    END IF;
END $$;

-- ============================================================================
-- 7) IDENTITY AND LIFECYCLE: the PK is (org_id, lane, drop_class) — a lane and
--    a class are SEPARATE episodes, never merged; the FK is to
--    ``organizations`` and CASCADEs with the org (mirroring ``metering_records``).
-- ============================================================================
DO $$
DECLARE n integer; rejected boolean;
BEGIN
    -- A second class on the SAME lane is its own row, not an accumulation onto
    -- the first. Mutation caught: a PK that omits drop_class.
    PERFORM public.metering_record_unmetered(
        '4779-dropped', 'write_op', 'increment_write_unconfirmed', 'RuntimeError');
    SELECT count(*) INTO n FROM public.metering_unmetered_increments
     WHERE org_id = '4779-dropped' AND lane = 'write_op';
    IF n <> 2 THEN
        RAISE EXCEPTION 'the two declared classes share one row per lane (rows=%)', n;
    END IF;

    -- The FK rejects an unknown org (audit integrity: every row names an org).
    rejected := false;
    BEGIN
        PERFORM public.metering_record_unmetered(
            '4779-absent-org', 'write_op', 'window_unresolvable', 'X');
    EXCEPTION WHEN foreign_key_violation THEN rejected := true;
    END;
    IF NOT rejected THEN
        RAISE EXCEPTION 'the org FK is missing — a row was written for an unknown org';
    END IF;

    -- ...and a blank org id never reaches the FK at all: the guard above refuses
    -- it first (probed in the blank-set block).

    -- ...and CASCADEs on the org's deletion.
    PERFORM public.metering_record_unmetered(
        '4779-free', 'write_op', 'window_unresolvable', 'X');
    DELETE FROM public.organizations WHERE id = '4779-free';
    SELECT count(*) INTO n FROM public.metering_unmetered_increments
     WHERE org_id = '4779-free';
    IF n <> 0 THEN
        RAISE EXCEPTION '% row(s) outlived their org — the FK does not CASCADE', n;
    END IF;
END $$;

-- ============================================================================
-- 8) ACL — the harness's default privileges grant EXECUTE on a new public
--    function to anon/authenticated, so the migration's REVOKE is the ONLY
--    thing separating tenant-wide metering state from those roles.
--    Mutation caught: dropping any REVOKE.
-- ============================================================================
DO $$
DECLARE fn text;
BEGIN
    FOREACH fn IN ARRAY ARRAY[
        'public.metering_record_unmetered(text, text, text, text, integer)',
        'public.metering_unmetered_for_org(text)',
        'public.metering_unmetered_total(text[])'
    ] LOOP
        IF has_function_privilege('anon', fn, 'EXECUTE') THEN
            RAISE EXCEPTION 'anon can execute %', fn;
        END IF;
        IF has_function_privilege('authenticated', fn, 'EXECUTE') THEN
            RAISE EXCEPTION 'authenticated can execute %', fn;
        END IF;
        IF NOT has_function_privilege('service_role', fn, 'EXECUTE') THEN
            RAISE EXCEPTION 'service_role CANNOT execute %', fn;
        END IF;
    END LOOP;
END $$;

-- 9) RLS is ON for the table (no browser surface reads metering).
--    Mutation caught: dropping the ENABLE ROW LEVEL SECURITY.
DO $$
DECLARE on_ boolean;
BEGIN
    SELECT c.relrowsecurity INTO on_ FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'public' AND c.relname = 'metering_unmetered_increments';
    IF on_ IS NOT TRUE THEN
        RAISE EXCEPTION 'RLS is not enabled on metering_unmetered_increments';
    END IF;
END $$;

-- ============================================================================
-- Cleanup — the FK cascades the representation rows with their orgs.
-- ============================================================================
DELETE FROM public.organizations WHERE id LIKE '4779-%';
