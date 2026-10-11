-- #4242 — the ledger WINDOW follows the authoritative bound, asserted against
-- the REAL SQL functions and the REAL cohort read.
--
-- WHAT THIS FILE EXISTS TO CATCH. Every period-keyed writer upserts on
-- ``(org_id, period_start)`` with an ``ON CONFLICT … DO UPDATE`` that used to
-- update the COUNTERS only, so ``period_end`` was written once, on INSERT, and
-- never again. A row minted while the org's anchor was a ``± 1 month``
-- APPROXIMATION (20260918000001 / 20260919000001) therefore kept that window
-- after the authoritative ``current_period_end`` arrived — and
-- ``metering_cohort_spend``'s half-open OVERLAP test could then miss the row.
-- On a spend CEILING an under-read is fail-OPEN.
--
-- Each test NAMES the mutation that must make it RED. The load-bearing one is
-- ``2b``: it asserts the CAP INPUT (what the cohort SUM returns for a window
-- that begins after the approximated end), not the SQL text. A suite that only
-- asserted ``period_end = EXCLUDED.period_end`` appears in the migration would
-- stay green on the real defect — which is that the ROW is wrong, not that the
-- statement was missing.
--
-- WHY NOT ONLY THE PYTHON FAKE. ``tests/fake_control_plane.py`` mirrors the
-- same semantics and is fixed in the same change, but a fake that mirrors the
-- INTENDED behaviour cannot falsify it (the defect the sibling #5045 suite's
-- review graded P0). These assertions run the shipped SQL.
--
-- All five period-keyed writers are covered. #4242 was filed against three;
-- ``metering_increment_embedding`` and ``metering_set_graph_storage`` landed
-- after it with the SAME omission, and a migration that fixed three would
-- leave two live lanes sticky.

-- Seed the orgs. One per lane, plus two for the cohort-read half of test 2.
INSERT INTO public.organizations
    (id, name, graph_name, subscription_id,
     current_period_start, current_period_end)
VALUES
    -- the ANNUAL-plan shape: start known, end repaired as ``start + 1 month``
    -- (20260919000001 branch 2), then an authoritative end ~12 months out.
    ('4242-writeops', '4242-writeops', 'org_4242-writeops', 'sub_4242_wo',
     '2026-03-01T00:00:00+00:00', '2026-04-01T00:00:00+00:00'),
    ('4242-ask', '4242-ask', 'org_4242-ask', 'sub_4242_ask',
     '2026-03-01T00:00:00+00:00', '2026-04-01T00:00:00+00:00'),
    ('4242-capture', '4242-capture', 'org_4242-capture', 'sub_4242_cap',
     '2026-03-01T00:00:00+00:00', '2026-04-01T00:00:00+00:00'),
    ('4242-embed', '4242-embed', 'org_4242-embed', 'sub_4242_emb',
     '2026-03-01T00:00:00+00:00', '2026-04-01T00:00:00+00:00'),
    ('4242-gauge', '4242-gauge', 'org_4242-gauge', 'sub_4242_gauge',
     '2026-03-01T00:00:00+00:00', '2026-04-01T00:00:00+00:00'),
    -- the NEGATIVE control for test 2c: an org carrying only a row that was
    -- never refreshed, so the discriminating read can be shown to be
    -- discriminating rather than trivially satisfiable.
    ('4242-ask-stale', '4242-ask-stale', 'org_4242-ask-stale', 'sub_4242_stale',
     '2026-03-01T00:00:00+00:00', '2026-04-01T00:00:00+00:00');

-- ============================================================================
-- 1) THE WINDOW IS REFRESHED, AND THE COUNTERS STILL ACCUMULATE.
--
-- Mutation caught: dropping ``period_end = EXCLUDED.period_end`` from any of
-- the five ``DO UPDATE`` blocks (the row keeps the ``start + 1 month``
-- approximation). Also caught: a refresh that REPLACES the counters instead of
-- adding them (the change must not trade one defect for another), and a
-- refresh that touches the derived ``period`` label or ``period_start``.
--
-- The second call passes the SAME ``period_start`` with the AUTHORITATIVE
-- end — exactly what ``metering._current_period`` does once
-- ``customer.subscription.updated`` has supplied the real bound.
-- ============================================================================
DO $$
DECLARE e timestamptz; ops integer; lbl text; s timestamptz;
BEGIN
    -- 1.1) write-op lane — the ONLY lane that RETURNS a value, so its counter
    --      is read from the RPC's own return as well as from the row.
    PERFORM public.metering_increment(
        '4242-writeops', '2026-03-01T00:00:00+00:00'::timestamptz,
        '2026-04-01T00:00:00+00:00'::timestamptz, 2, 7);
    ops := public.metering_increment(
        '4242-writeops', '2026-03-01T00:00:00+00:00'::timestamptz,
        '2027-03-01T00:00:00+00:00'::timestamptz, 3, 11);
    IF ops IS DISTINCT FROM 5 THEN
        RAISE EXCEPTION
            'metering_increment must ACCUMULATE (2+3=5), got % — the window '
            'refresh must not replace the counters', ops;
    END IF;
    SELECT period_end, period, period_start INTO e, lbl, s
      FROM public.metering_records WHERE org_id = '4242-writeops';
    IF e IS DISTINCT FROM '2027-03-01T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION
            'metering_increment left period_end at % — the approximated '
            'window is STICKY (the #4242 defect)', e;
    END IF;
    IF s IS DISTINCT FROM '2026-03-01T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION 'the refresh moved the PK column period_start to %', s;
    END IF;
    IF lbl IS DISTINCT FROM '2026-03' THEN
        RAISE EXCEPTION 'the derived period label drifted to %', lbl;
    END IF;

    -- 1.2) ask lane (a cohort SUM input).
    PERFORM public.metering_increment_ask(
        '4242-ask', '2026-03-01T00:00:00+00:00'::timestamptz,
        '2026-04-01T00:00:00+00:00'::timestamptz, 1, 10, 20, 3.50);
    PERFORM public.metering_increment_ask(
        '4242-ask', '2026-03-01T00:00:00+00:00'::timestamptz,
        '2027-03-01T00:00:00+00:00'::timestamptz, 1, 30, 40, 1.50);
    SELECT period_end INTO e FROM public.metering_records
     WHERE org_id = '4242-ask';
    IF e IS DISTINCT FROM '2027-03-01T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION
            'metering_increment_ask left period_end at % — the approximated '
            'window is STICKY', e;
    END IF;
    IF (SELECT ask_cost_usd FROM public.metering_records
         WHERE org_id = '4242-ask') IS DISTINCT FROM 5.0::double precision THEN
        RAISE EXCEPTION 'the ask cost did not accumulate (3.50 + 1.50 expected)';
    END IF;

    -- 1.3) capture lane (the other cohort SUM input).
    PERFORM public.metering_increment_capture_cost(
        '4242-capture', '2026-03-01T00:00:00+00:00'::timestamptz,
        '2026-04-01T00:00:00+00:00'::timestamptz, 1, 100, 150, 2.25);
    PERFORM public.metering_increment_capture_cost(
        '4242-capture', '2026-03-01T00:00:00+00:00'::timestamptz,
        '2027-03-01T00:00:00+00:00'::timestamptz, 1, 200, 250, 0.75);
    SELECT period_end INTO e FROM public.metering_records
     WHERE org_id = '4242-capture';
    IF e IS DISTINCT FROM '2027-03-01T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION
            'metering_increment_capture_cost left period_end at % — the '
            'approximated window is STICKY', e;
    END IF;
    IF (SELECT capture_cost_usd FROM public.metering_records
         WHERE org_id = '4242-capture') IS DISTINCT FROM 3.0::double precision THEN
        RAISE EXCEPTION 'the capture cost did not accumulate (2.25 + 0.75)';
    END IF;

    -- 1.4) embed lane (landed AFTER #4242 was filed — same omission).
    PERFORM public.metering_increment_embedding(
        '4242-embed', '2026-03-01T00:00:00+00:00'::timestamptz,
        '2026-04-01T00:00:00+00:00'::timestamptz, 1, 10, 100);
    PERFORM public.metering_increment_embedding(
        '4242-embed', '2026-03-01T00:00:00+00:00'::timestamptz,
        '2027-03-01T00:00:00+00:00'::timestamptz, 2, 20, 200);
    SELECT period_end INTO e FROM public.metering_records
     WHERE org_id = '4242-embed';
    IF e IS DISTINCT FROM '2027-03-01T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION
            'metering_increment_embedding left period_end at % — the '
            'approximated window is STICKY', e;
    END IF;
    IF (SELECT embed_calls FROM public.metering_records
         WHERE org_id = '4242-embed') IS DISTINCT FROM 3 THEN
        RAISE EXCEPTION 'the embed calls did not accumulate (1+2)';
    END IF;

    -- 1.5) the graph-storage GAUGE (landed after #4242 — same omission). Its
    --      VALUES overwrite (a gauge, not an accumulator); the WINDOW is what
    --      this issue fixes.
    PERFORM public.metering_set_graph_storage(
        '4242-gauge', '2026-03-01T00:00:00+00:00'::timestamptz,
        '2026-04-01T00:00:00+00:00'::timestamptz, 10.0);
    PERFORM public.metering_set_graph_storage(
        '4242-gauge', '2026-03-01T00:00:00+00:00'::timestamptz,
        '2027-03-01T00:00:00+00:00'::timestamptz, 42.0);
    SELECT period_end INTO e FROM public.metering_records
     WHERE org_id = '4242-gauge';
    IF e IS DISTINCT FROM '2027-03-01T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION
            'metering_set_graph_storage left period_end at % — the '
            'approximated window is STICKY', e;
    END IF;
    IF (SELECT graph_storage_mb FROM public.metering_records
         WHERE org_id = '4242-gauge') IS DISTINCT FROM 42.0::double precision THEN
        RAISE EXCEPTION 'the gauge must OVERWRITE its reading, not add it';
    END IF;
END $$;

-- ============================================================================
-- 2) THE CAP INPUT IS CORRECTED — the assertion that ties the fix to the
--    spend ceiling, not to the SQL text.
--
-- The read window begins ONE DAY AFTER the approximated end (``start + 1
-- month``) and runs to the authoritative end. That is precisely a cohort check
-- taken later in the annual period, for a DIFFERENT org than the row's owner —
-- the shape the issue describes.
--
-- Mutation caught: the sticky window. Without the refresh the row's
-- ``period_end`` is ``start + 1 month``, the overlap test
-- (``period_end > p_period_start``) is FALSE, the SUM returns 0 and the ceiling
-- reads a cohort that has spent nothing — fail-OPEN.
-- ============================================================================
DO $$
DECLARE spent double precision; hidden double precision;
BEGIN
    -- 2a) the row now covers the authoritative window: read over the FULL
    --     window (this half is green under the defect too — it is the control
    --     that proves the reader is wired to these rows at all).
    spent := public.metering_cohort_spend(
        ARRAY['4242-ask'],
        '2026-03-01T00:00:00+00:00'::timestamptz,
        '2027-03-01T00:00:00+00:00'::timestamptz);
    IF spent IS DISTINCT FROM 5.0::double precision THEN
        RAISE EXCEPTION
            'the cohort read must see the ask row over its own window, got %',
            spent;
    END IF;

    -- 2b) ⋆ THE FAIL-OPEN ASSERTION ⋆ — a window that BEGINS after the
    --     approximated end must still count the row.
    spent := public.metering_cohort_spend(
        ARRAY['4242-ask'],
        '2026-04-02T00:00:00+00:00'::timestamptz,
        '2027-03-01T00:00:00+00:00'::timestamptz);
    IF spent IS DISTINCT FROM 5.0::double precision THEN
        RAISE EXCEPTION
            'the cohort read returned % for a window beginning after the '
            'APPROXIMATED end — the sticky window under-reads the cohort, '
            'which is fail-OPEN on a spend ceiling (#4242)', spent;
    END IF;

    -- 2c) the same read BEFORE the refresh would have returned 0. This is the
    --     negative control that proves 2b is discriminating rather than
    --     trivially satisfiable: a synthetic row carrying the approximated
    --     window is invisible to the same read.
    INSERT INTO public.metering_records
        (org_id, period, period_start, period_end, ask_cost_usd)
    VALUES ('4242-ask-stale', '2026-03', '2026-03-01T00:00:00+00:00',
            '2026-04-01T00:00:00+00:00', 99.0)
    ON CONFLICT (org_id, period_start) DO NOTHING;
    hidden := public.metering_cohort_spend(
        ARRAY['4242-ask-stale'],
        '2026-04-02T00:00:00+00:00'::timestamptz,
        '2027-03-01T00:00:00+00:00'::timestamptz);
    IF hidden IS DISTINCT FROM 0.0::double precision THEN
        RAISE EXCEPTION
            'the negative control must be INVISIBLE to this read (got %) — '
            'without that, 2b cannot distinguish a refreshed window from a '
            'read that ignores the window entirely', hidden;
    END IF;
END $$;

-- ============================================================================
-- 3) THE ROW MIRRORS THE AUTHORITY — no ``greatest()`` guard.
--
-- The refresh is ``= EXCLUDED.period_end``, not a monotonic widening. Both
-- agree in the direction this issue fixes (the derived→authoritative transition
-- only widens), so this asserts the OTHER direction deliberately: a later
-- increment carrying a SHORTER window for the same ``period_start`` must follow
-- it. That is the "the row mirrors ``organizations``" invariant — the ledger
-- and the resolver must not disagree, and a ``greatest()`` guard would
-- re-introduce stickiness in the opposite direction (an end corrected downward
-- by an authoritative write could never follow its authority).
--
-- Mutation caught: hardening the refresh into ``greatest(...)``.
-- ============================================================================
DO $$
DECLARE e timestamptz;
BEGIN
    PERFORM public.metering_increment_ask(
        '4242-ask', '2026-03-01T00:00:00+00:00'::timestamptz,
        '2026-06-01T00:00:00+00:00'::timestamptz, 0, 0, 0, 0.0);
    SELECT period_end INTO e FROM public.metering_records
     WHERE org_id = '4242-ask';
    IF e IS DISTINCT FROM '2026-06-01T00:00:00+00:00'::timestamptz THEN
        RAISE EXCEPTION
            'the row must MIRROR the authoritative window (%), got % — a '
            'monotonic guard would make the ledger disagree with the resolver '
            'that produced it', '2026-06-01T00:00:00+00:00', e;
    END IF;
END $$;

-- ============================================================================
-- 4) THE DEGENERATE-WINDOW GUARD STILL REFUSES — the property that keeps the
--    refresh from minting an unreadable row.
--
-- ``period_end`` is only ever assigned from ``p_period_end``, and the RPC
-- REFUSES ``p_period_end <= p_period_start`` before the INSERT. Since the
-- conflict target IS ``p_period_start``, the refreshed end is strictly after
-- the window's own start and the half-open window stays non-empty by
-- construction.
--
-- Mutation caught: dropping the guard (a refresh could then write an empty
-- window and orphan the row outside every later read).
-- ============================================================================
DO $$
DECLARE caught boolean := false;
BEGIN
    BEGIN
        PERFORM public.metering_increment(
            '4242-writeops', '2026-03-01T00:00:00+00:00'::timestamptz,
            '2026-03-01T00:00:00+00:00'::timestamptz, 1, 0);
    EXCEPTION WHEN raise_exception THEN
        caught := true;
    END;
    IF NOT caught THEN
        RAISE EXCEPTION
            'a degenerate window must be REFUSED, not drawn into the ledger';
    END IF;
END $$;

-- ============================================================================
-- 5) THE FUNCTIONS ARE UNIQUE AND ACL-SEALED.
--
-- (a) exactly ONE callable signature per RPC name. DROP-then-CREATE is the
--     convention precisely so a re-created function cannot leave an older
--     signature callable — a silent second write path (#3825). Mutation
--     caught: a CREATE without its DROP (an OVERLOAD), or a missed DROP of a
--     superseded argument list (the capture lane's 5-arg
--     ``(text, timestamptz, timestamptz, integer, double precision)``).
-- (b) ``anon``/``authenticated`` must NOT be able to execute any of them. The
--     harness's default privileges GRANT EXECUTE on a new public function, so
--     the migration's REVOKE is the ONLY thing separating every tenant's
--     metering ledger from those roles. Mutation caught: dropping a REVOKE
--     (the ACL-loss failure mode 20260915's own comment records).
-- ============================================================================
DO $$
DECLARE n integer; fn text;
BEGIN
    FOREACH fn IN ARRAY ARRAY['metering_increment', 'metering_increment_ask',
                              'metering_increment_capture_cost',
                              'metering_increment_embedding',
                              'metering_set_graph_storage'] LOOP
        SELECT count(*) INTO n
          FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
         WHERE ns.nspname = 'public' AND p.proname = fn;
        IF n <> 1 THEN
            RAISE EXCEPTION
                'public.% has % callable signatures — a DROP-then-CREATE must '
                'leave exactly one (an OVERLOAD is a silent second write '
                'path)', fn, n;
        END IF;
    END LOOP;

    -- The ACL check is spelled per RPC with its OWN argument list: a
    -- name-only ``has_function_privilege`` is ambiguous for these names (and
    -- raises), so it would not be evidence. The harness's default privileges
    -- grant EXECUTE to anon/authenticated on a newly created public function,
    -- so each REVOKE is the only thing standing between every tenant and the
    -- metering ledger.
    IF has_function_privilege('anon',
            'public.metering_increment(text, timestamptz, timestamptz, integer, integer)',
            'EXECUTE')
       OR has_function_privilege('authenticated',
            'public.metering_increment(text, timestamptz, timestamptz, integer, integer)',
            'EXECUTE') THEN
        RAISE EXCEPTION 'metering_increment: anon/authenticated must not execute';
    END IF;
    IF has_function_privilege('anon',
            'public.metering_increment_ask(text, timestamptz, timestamptz, integer, '
            'integer, integer, double precision)', 'EXECUTE')
       OR has_function_privilege('authenticated',
            'public.metering_increment_ask(text, timestamptz, timestamptz, integer, '
            'integer, integer, double precision)', 'EXECUTE') THEN
        RAISE EXCEPTION 'metering_increment_ask: anon/authenticated must not execute';
    END IF;
    IF has_function_privilege('anon',
            'public.metering_increment_capture_cost(text, timestamptz, timestamptz, '
            'integer, bigint, bigint, double precision)', 'EXECUTE')
       OR has_function_privilege('authenticated',
            'public.metering_increment_capture_cost(text, timestamptz, timestamptz, '
            'integer, bigint, bigint, double precision)', 'EXECUTE') THEN
        RAISE EXCEPTION
            'metering_increment_capture_cost: anon/authenticated must not execute';
    END IF;
    IF has_function_privilege('anon',
            'public.metering_increment_embedding(text, timestamptz, timestamptz, '
            'integer, bigint, bigint, double precision, integer, text, text, boolean)',
            'EXECUTE')
       OR has_function_privilege('authenticated',
            'public.metering_increment_embedding(text, timestamptz, timestamptz, '
            'integer, bigint, bigint, double precision, integer, text, text, boolean)',
            'EXECUTE') THEN
        RAISE EXCEPTION
            'metering_increment_embedding: anon/authenticated must not execute';
    END IF;
    IF has_function_privilege('anon',
            'public.metering_set_graph_storage(text, timestamptz, timestamptz, '
            'double precision, double precision, integer, integer, double precision, '
            'double precision, double precision, timestamptz)', 'EXECUTE')
       OR has_function_privilege('authenticated',
            'public.metering_set_graph_storage(text, timestamptz, timestamptz, '
            'double precision, double precision, integer, integer, double precision, '
            'double precision, double precision, timestamptz)', 'EXECUTE') THEN
        RAISE EXCEPTION
            'metering_set_graph_storage: anon/authenticated must not execute';
    END IF;
END $$;

-- ============================================================================
-- 6) THE LEDGER ROW AND ``organizations`` AGREE.
--
-- The invariant this issue restores, stated directly: the window an increment
-- carries is the one ``metering._current_period`` read from ``organizations``,
-- and it is what the LEDGER row ends up carrying. This block emulates the
-- resolver (read BOTH bounds from ``organizations``, pass them straight into
-- the RPC) and then compares the stored ledger window against the stored
-- authoritative bound — so it is falsifiable against any refresh that writes
-- something other than ``p_period_end`` (a re-derivation, a calendar month, a
-- sentinel).
--
-- Mutation caught: a refresh that stores anything but the authoritative bound.
-- ============================================================================
DO $$
DECLARE v_start timestamptz; v_end timestamptz; m_end timestamptz;
BEGIN
    UPDATE public.organizations
       SET current_period_end = '2027-03-01T00:00:00+00:00'::timestamptz
     WHERE id = '4242-writeops';

    -- emulate ``metering._current_period`` for a subscription org: the window
    -- comes from the two stored bounds, not from this suite's constants.
    SELECT current_period_start, current_period_end INTO v_start, v_end
      FROM public.organizations WHERE id = '4242-writeops';
    PERFORM public.metering_increment('4242-writeops', v_start, v_end, 1, 0);

    SELECT period_end INTO m_end FROM public.metering_records
     WHERE org_id = '4242-writeops' AND period_start = v_start;
    IF m_end IS DISTINCT FROM v_end THEN
        RAISE EXCEPTION
            'the ledger window (%) disagrees with the authoritative anchor (%) '
            'after an increment — the row must mirror organizations',
            m_end, v_end;
    END IF;
END $$;

DELETE FROM public.metering_records WHERE org_id LIKE '4242-%';
DELETE FROM public.organizations WHERE id LIKE '4242-%';
