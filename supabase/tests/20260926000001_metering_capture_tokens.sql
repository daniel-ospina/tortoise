-- #5045 — the capture-token accumulator is asserted against the REAL SQL
-- function, not only against the Python fake.
--
-- WHY THIS FILE EXISTS. ``metering_increment_capture_cost`` is a business-logic
-- Postgres function (INSERT … ON CONFLICT DO UPDATE with accumulation) that
-- carries the token counters onto the durable ledger. No EXECUTED SQL assert
-- covered it: the harness's inline checks never mention it, and
-- ``tests/test_cohort_cost_cap.py`` plus the Python fake assert on cost, not on
-- this arithmetic. ``tests/test_metering_period_window.py`` does assert on this
-- function, but TEXTUALLY — that the migration's DROP/CREATE and parameter
-- names are present — which cannot distinguish an accumulating implementation
-- from a replacing one. This is the same defect the sibling #5331 branch's
-- review graded P0 (a fake that mirrors the intended semantics cannot falsify
-- them). Concretely, each of these mutants would have left the whole suite
-- green:
--
--   * ``capture_tokens_in = coalesce(…,0) + p_tokens_in`` → ``= p_tokens_in``
--     (the accumulator REPLACES instead of adding — the token ledger silently
--     reports the last capture's tokens as the window's total);
--   * dropping the ``DEFAULT 0`` on ``p_tokens_*`` (the legacy caller, which
--     sends no token keys, stops resolving);
--   * assigning ``p_tokens_in`` from ``p_cost_usd`` (a cost silently billed as
--     a token count);
--   * dropping the degenerate-window guard.
--
-- Every comparison uses ``IS DISTINCT FROM`` so a NULL left by a dropped
-- assignment cannot make an assertion pass vacuously.

INSERT INTO public.organizations
    (id, name, graph_name, subscription_id,
     current_period_start, current_period_end)
VALUES
    ('5045-tokens', '5045-tokens', 'org_5045-tokens', NULL, NULL, NULL),
    ('5045-tokens-legacy', '5045-tokens-legacy', 'org_5045-tokens-legacy', NULL, NULL, NULL),
    ('5045-tokens-seeded', '5045-tokens-seeded', 'org_5045-tokens-seeded', NULL, NULL, NULL);

-- 1) ACCUMULATE, never replace. Two captures in the same window ADD.
--    Mutation caught: ``= coalesce(…,0) + p_tokens_in`` → ``= p_tokens_in``.
--    A replacing implementation stores 200/250 (the SECOND call), and REDs
--    here; a correct one stores 300/400.
DO $$
DECLARE tin bigint; tout bigint; calls integer;
BEGIN
    PERFORM public.metering_increment_capture_cost(
        '5045-tokens', '2026-09-01T00:00:00+00:00'::timestamptz,
        '2026-10-01T00:00:00+00:00'::timestamptz, 2, 100, 150, 0.25);
    PERFORM public.metering_increment_capture_cost(
        '5045-tokens', '2026-09-01T00:00:00+00:00'::timestamptz,
        '2026-10-01T00:00:00+00:00'::timestamptz, 3, 200, 250, 0.5);

    SELECT capture_tokens_in, capture_tokens_out, capture_calls
      INTO tin, tout, calls
      FROM public.metering_records
     WHERE org_id = '5045-tokens'
       AND period_start = '2026-09-01T00:00:00+00:00'::timestamptz;

    IF tin IS DISTINCT FROM 300::bigint THEN
        RAISE EXCEPTION
            'capture_tokens_in must ACCUMULATE across the window (100+200=300), '
            'got % — a replacing accumulator reports one capture as the total', tin;
    END IF;
    IF tout IS DISTINCT FROM 400::bigint THEN
        RAISE EXCEPTION 'capture_tokens_out must accumulate (150+250=400), got %', tout;
    END IF;
    IF calls IS DISTINCT FROM 5 THEN
        RAISE EXCEPTION 'capture_calls must accumulate (2+3=5), got %', calls;
    END IF;
END $$;

-- 2) TOKENS AND COST DO NOT CROSS-CONTAMINATE. A cost is money; a token count
--    is workload. Assigning either from the other is the defect this pins.
--    Mutation caught: ``p_tokens_in`` ← ``p_cost_usd`` (or vice versa).
DO $$
DECLARE tin bigint; tout bigint; cost double precision;
BEGIN
    -- cost only: the token counters must be untouched.
    PERFORM public.metering_increment_capture_cost(
        '5045-tokens', '2026-09-01T00:00:00+00:00'::timestamptz,
        '2026-10-01T00:00:00+00:00'::timestamptz, 1, 0, 0, 9.75);
    SELECT capture_tokens_in, capture_tokens_out, capture_cost_usd
      INTO tin, tout, cost FROM public.metering_records
     WHERE org_id = '5045-tokens'
       AND period_start = '2026-09-01T00:00:00+00:00'::timestamptz;
    IF tin IS DISTINCT FROM 300::bigint OR tout IS DISTINCT FROM 400::bigint THEN
        RAISE EXCEPTION 'a cost-only capture must not move the token counters (% %)', tin, tout;
    END IF;
    IF cost IS DISTINCT FROM 10.5::double precision THEN
        RAISE EXCEPTION 'capture_cost_usd must accumulate (0.25+0.5+9.75=10.5), got %', cost;
    END IF;
    -- tokens only: the cost must be untouched.
    PERFORM public.metering_increment_capture_cost(
        '5045-tokens', '2026-09-01T00:00:00+00:00'::timestamptz,
        '2026-10-01T00:00:00+00:00'::timestamptz, 0, 7, 11);
    SELECT capture_tokens_in, capture_tokens_out, capture_cost_usd
      INTO tin, tout, cost FROM public.metering_records
     WHERE org_id = '5045-tokens'
       AND period_start = '2026-09-01T00:00:00+00:00'::timestamptz;
    IF tin IS DISTINCT FROM 307::bigint OR tout IS DISTINCT FROM 411::bigint THEN
        RAISE EXCEPTION 'a token-only capture must add only tokens (% %)', tin, tout;
    END IF;
    IF cost IS DISTINCT FROM 10.5::double precision THEN
        RAISE EXCEPTION 'a token-only capture must not move the cost (got %)', cost;
    END IF;
END $$;

-- 3) THE LEGACY CALLER STILL RESOLVES. The deploy-order comment claims Python
--    may be deployed AFTER the migration, so the pre-#5045 call — which sends
--    no token keys — must still work and simply leave the counters at 0.
--    Mutation caught: dropping the ``DEFAULT 0`` on ``p_tokens_in``/``out``
--    (the call raises "function does not exist"), or defaulting them to
--    anything non-zero.
DO $$
DECLARE tin bigint; tout bigint; cost double precision;
BEGIN
    PERFORM public.metering_increment_capture_cost(
        p_org_id       => '5045-tokens-legacy',
        p_period_start => '2026-09-01T00:00:00+00:00'::timestamptz,
        p_period_end   => '2026-10-01T00:00:00+00:00'::timestamptz,
        p_calls        => 4,
        p_cost_usd     => 2.5);
    SELECT capture_tokens_in, capture_tokens_out, capture_cost_usd
      INTO tin, tout, cost FROM public.metering_records
     WHERE org_id = '5045-tokens-legacy'
       AND period_start = '2026-09-01T00:00:00+00:00'::timestamptz;
    IF tin IS DISTINCT FROM 0::bigint OR tout IS DISTINCT FROM 0::bigint THEN
        RAISE EXCEPTION
            'the legacy caller omits the token keys — they must default to 0, got % %', tin, tout;
    END IF;
    IF cost IS DISTINCT FROM 2.5::double precision THEN
        RAISE EXCEPTION 'the legacy caller''s cost must still land (got %)', cost;
    END IF;
END $$;

-- 4) A ROW MINTED BY THE PLAIN-WRITE LANE UPGRADES IN PLACE, and that lane's
--    counter survives. This is the executable form of the migration's NOT NULL
--    DEFAULT reasoning: ``metering_increment`` never mentions the token
--    columns.
--    Mutation caught: an INSERT-only implementation (duplicate-key error here),
--    and any DELETE-then-INSERT that would wipe write_ops.
SELECT public.metering_increment(
    '5045-tokens-seeded',
    '2026-09-01T00:00:00+00:00'::timestamptz,
    '2026-10-01T00:00:00+00:00'::timestamptz,
    6, 0);

DO $$
DECLARE tin bigint; ops integer;
BEGIN
    SELECT capture_tokens_in, write_ops INTO tin, ops
      FROM public.metering_records
     WHERE org_id = '5045-tokens-seeded'
       AND period_start = '2026-09-01T00:00:00+00:00'::timestamptz;
    IF tin IS DISTINCT FROM 0::bigint THEN
        RAISE EXCEPTION 'a row this RPC never touched must read 0, not NULL (got %)', tin;
    END IF;
    IF ops IS DISTINCT FROM 6 THEN
        RAISE EXCEPTION 'the plain-write lane''s counter must be present (got %)', ops;
    END IF;
END $$;

SELECT public.metering_increment_capture_cost(
    '5045-tokens-seeded', '2026-09-01T00:00:00+00:00'::timestamptz,
    '2026-10-01T00:00:00+00:00'::timestamptz, 1, 50, 60, 0.1);

DO $$
DECLARE tin bigint; ops integer;
BEGIN
    SELECT capture_tokens_in, write_ops INTO tin, ops
      FROM public.metering_records
     WHERE org_id = '5045-tokens-seeded'
       AND period_start = '2026-09-01T00:00:00+00:00'::timestamptz;
    IF tin IS DISTINCT FROM 50::bigint THEN
        RAISE EXCEPTION 'the capture lane did not land on the pre-existing row (got %)', tin;
    END IF;
    IF ops IS DISTINCT FROM 6 THEN
        RAISE EXCEPTION 'the capture lane must not disturb write_ops (got %)', ops;
    END IF;
END $$;

-- 5) A DEGENERATE WINDOW IS REFUSED, not stored.
--    Mutation caught: dropping the ``IF p_period_end <= p_period_start`` guard.
DO $$
DECLARE n integer;
BEGIN
    BEGIN
        PERFORM public.metering_increment_capture_cost(
            '5045-tokens', '2026-10-01T00:00:00+00:00'::timestamptz,
            '2026-09-01T00:00:00+00:00'::timestamptz, 1, 1, 1, 0.01);
        RAISE EXCEPTION 'an inverted window must be refused, not stored';
    EXCEPTION WHEN raise_exception THEN
        IF SQLERRM LIKE '%must be refused%' THEN RAISE; END IF;
    END;
    SELECT count(*) INTO n FROM public.metering_records
     WHERE org_id = '5045-tokens' AND period_end <= period_start;
    IF n <> 0 THEN
        RAISE EXCEPTION 'a refused window was persisted anyway (% row(s))', n;
    END IF;
END $$;

-- 6) EXACTLY ONE DEFINITION — no stale 5-argument overload survives. The
--    migration DROPs before CREATE precisely so a changed argument list cannot
--    remain callable as an overload (the silent second path #3825 removed).
--    ALSO A SIGNATURE PIN. The parameter NAMES and ORDER are asserted. This is
--    an explicit INTENT pin, not the sole guard: a swap of ``p_tokens_in`` with
--    ``p_cost_usd`` would make the positional calls in blocks 1/2 stop
--    resolving first, and a swap of the two token parameters is caught by
--    block 2's 307/411 assertion. It is here because the token parameters were
--    inserted BEFORE ``p_cost_usd``, which is safe only because every caller
--    passes NAMED arguments (verified in ``tortoise/supabase_control.py``) — a
--    positional 5-argument call would now bind its cost to ``p_tokens_in``. If
--    someone reorders these, this makes them do it deliberately.
--    Mutation caught: CREATE OR REPLACE on a changed signature (old overload
--    survives), or an accidental parameter reorder.
DO $$
DECLARE n integer; args text;
BEGIN
    SELECT count(*) INTO n
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'public'
       AND p.proname = 'metering_increment_capture_cost';
    IF n <> 1 THEN
        RAISE EXCEPTION 'expected exactly 1 metering_increment_capture_cost, '
                        'found % (a stale overload is callable)', n;
    END IF;
    SELECT pg_get_function_arguments(p.oid) INTO args
      FROM pg_proc p JOIN pg_namespace ns ON ns.oid = p.pronamespace
     WHERE ns.nspname = 'public'
       AND p.proname = 'metering_increment_capture_cost';
    IF args NOT LIKE '%p_tokens_in bigint DEFAULT 0%'
       OR args NOT LIKE '%p_tokens_out bigint DEFAULT 0%' THEN
        RAISE EXCEPTION 'the token parameters lost their DEFAULT 0, so the '
                        'legacy caller stops resolving: %', args;
    END IF;
    IF args NOT LIKE '%p_cost_usd%' OR position('p_cost_usd' IN args)
                                       < position('p_tokens_in' IN args) THEN
        RAISE EXCEPTION 'p_cost_usd must FOLLOW p_tokens_in (the order the '
                        'callers use); got %', args;
    END IF;
END $$;

-- 7) ACL — the harness's default privileges grant EXECUTE on a new public
--    function to anon/authenticated, so the migration's REVOKE is the ONLY
--    thing separating an all-tenant ledger write from those roles.
--    Mutation caught: dropping the REVOKE.
DO $$
DECLARE sig text := 'public.metering_increment_capture_cost(text, timestamptz, '
                    'timestamptz, integer, bigint, bigint, double precision)';
        acl text;
BEGIN
    IF has_function_privilege('anon', sig, 'EXECUTE') THEN
        RAISE EXCEPTION 'anon must NOT be able to write the capture-token ledger';
    END IF;
    IF has_function_privilege('authenticated', sig, 'EXECUTE') THEN
        RAISE EXCEPTION 'authenticated must NOT be able to write the capture-token ledger';
    END IF;
    IF NOT has_function_privilege('service_role', sig, 'EXECUTE') THEN
        RAISE EXCEPTION 'service_role MUST be able to write the capture-token ledger';
    END IF;
    SELECT p.proacl::text INTO acl
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname = 'metering_increment_capture_cost';
    IF acl IS NULL OR acl NOT LIKE '%service_role=X%'
       OR acl LIKE '%anon=X%' OR acl LIKE '%authenticated=X%' THEN
        RAISE EXCEPTION 'the stored ACL does not match the artifact: %', acl;
    END IF;
END $$;

DELETE FROM public.organizations
 WHERE id IN ('5045-tokens', '5045-tokens-legacy', '5045-tokens-seeded');
