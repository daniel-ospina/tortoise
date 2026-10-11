-- Migration 20261010000001: the metering ledger's WINDOW follows the
-- authoritative bound on every increment (#4242, follow-up to #4216).
--
-- WHY. Every period-keyed writer in this ledger upserts on
-- ``(org_id, period_start)`` (20260918000001:138,141) with an
-- ``ON CONFLICT … DO UPDATE`` that updated the COUNTERS ONLY. ``period_end``
-- — half of the row's identity as a WINDOW — was written on INSERT and never
-- again. So a row created while the org's anchor was a ``± 1 month``
-- APPROXIMATION (20260918000001's ``end - 1 month`` backfill, or
-- 20260919000001's ``start + 1 month`` repair) kept that approximated window
-- after the authoritative ``current_period_end`` arrived on
-- ``organizations``: the next increment hit ``ON CONFLICT (org_id,
-- period_start)`` and bumped the counters, leaving ``period_end`` sticky.
-- 20260919000001's own header names this gap and files it as the follow-up
-- this migration closes.
--
-- WHY IT IS A SPEND-CEILING DEFECT AND NOT COSMETIC. The cap's cohort read is
-- a half-open OVERLAP of the supplied window
-- (``metering_cohort_spend``: ``period_start < p_period_end AND period_end >
-- p_period_start``), and the window it is handed is the REQUESTING org's
-- (``cohort_cost.enforce_cohort_cost_cap`` → ``metering._current_period``). A
-- row whose ``period_end`` is shorter than the period it actually meters can
-- therefore fall OUTSIDE a later read window that its spend belongs to. An
-- under-read on a spend CEILING is fail-OPEN — the cap fires later than it
-- should, which is the one error class this ledger exists to prevent
-- (20260918000001 §4 argues the same asymmetry: an over-read can only fire
-- the cap EARLIER).
--
-- The concrete shape: an ANNUAL plan whose ``current_period_start`` is known
-- and whose ``current_period_end`` was missing, repaired by 20260919000001 as
-- ``start + 1 month`` — short by ~11 months. Spend metered through the rest of
-- the year accumulates in that one row, whose stored window says the period
-- lasted a month. Once the authoritative end arrives, a cohort read for a
-- window beginning after ``start + 1 month`` no longer overlaps the row, and
-- that org's real spend becomes invisible to the ceiling.
--
-- WHAT THIS DOES. Adds ``period_end = EXCLUDED.period_end`` to every
-- period-keyed ``DO UPDATE``, so the row's window is corrected to the newest
-- authoritative bound on the next increment — the supersession
-- 20260918000001 documents for ``organizations`` now reaches the already
-- written ledger row. The refreshed end is ALWAYS the end the resolver
-- (``metering._current_period``) read from ``organizations`` for this same
-- period, so the ledger row and the runtime window agree again.
--
-- WHY ``= EXCLUDED.period_end`` AND NOT ``greatest(...)``. Widening is what
-- this defect needs, and the derived→authoritative transition only ever
-- WIDENS (the derived end is ``start + 1 month``; an authoritative end for the
-- same start is that or longer). A monotonic-``greatest`` guard would look
-- safer and is not: it would re-introduce stickiness in the other direction
-- (an end corrected DOWNWARD by an authoritative write could never follow its
-- authority), and it would make the ledger disagree with the resolver that
-- produced it. The authority is ``organizations``; the row mirrors it.
--
-- WHY THE REFRESH CANNOT MINT A DEGENERATE ROW. ``period_end`` is only ever
-- assigned from ``p_period_end``, and every RPC below already REFUSES
-- ``p_period_end <= p_period_start`` before the INSERT. The row's conflict
-- target IS ``p_period_start``, so the refreshed end is strictly after the
-- window's own start: the half-open window stays non-empty by construction.
-- (Ordering inside the ``SET`` list is irrelevant — PostgreSQL evaluates every
-- assignment against the OLD row, the rule 20261001000004 documents.)
--
-- RE-MEASUREMENT (#4242 was filed against THREE RPCs; there are FIVE). The
-- issue names ``metering_increment``, ``metering_increment_ask`` and
-- ``metering_increment_capture_cost``. Two further period-keyed writers landed
-- after it — ``metering_increment_embedding`` (20261001000004) and
-- ``metering_set_graph_storage`` (20261001000005) — and each carries the SAME
-- omission: the embedding lane's counters accumulate and the gauge OVERWRITES
-- its values, but NEITHER refreshes ``period_end``. The gauge setter is a period-keyed writer
-- too, so it is on this list rather than exempt: a stale window hides its row
-- from the same overlap read. All five are re-created here; a migration that
-- fixed three would leave a live lane sticky.
--
-- ⚠️ RESIDUAL, DELIBERATELY NOT IN THIS MIGRATION. This closes the class from
-- the next increment onwards. A row whose org never increments again is
-- therefore NOT healed by this migration — the correction is written by the
-- next increment, not by a backfill. A deploy-time backfill of existing rows
-- (``UPDATE … SET period_end = o.current_period_end WHERE m.period_start =
-- o.current_period_start``) was considered and deflects: it is a DATA
-- migration over the spend ledger, and its shrink direction (an
-- authoritative end shorter than the stored one) can move a row OUT of a later
-- read window — i.e. it can re-open the exact fail-open this migration is
-- closing. #4242's own ruling is explicit that the ON-CONFLICT refresh is the
-- minimal fail-safe change and that anything larger is a separate decision.
-- Tracked as #8005 (with the measured proof that an un-refreshed row is
-- invisible to the late-window read, and with the shrink direction named as the
-- reason a mirror-the-authority backfill cannot ride this migration).
--
-- DROP-then-CREATE, not ``CREATE OR REPLACE``: the repository's convention for
-- a re-created metering RPC (20260918000001 §3, 20261001000001,
-- 20261001000004, 20261001000005), and it keeps the ACL re-issue — 20260915's
-- comment records that a missing REVOKE/GRANT re-issue is how ACLs get lost.
-- The argument lists below are IDENTICAL to the definitions they replace, so
-- the DROP removes exactly the old overload and the CREATE leaves exactly one
-- callable signature per RPC (no silent second write path, #3825).
-- ============================================================================

-- ============================================================================
-- 1. metering_increment — the write-op accumulator
-- ============================================================================
DROP FUNCTION IF EXISTS public.metering_increment(text, timestamptz,
    timestamptz, integer, integer);

CREATE FUNCTION public.metering_increment(
    p_org_id         text,
    p_period_start   timestamptz,
    p_period_end     timestamptz,
    p_n              integer DEFAULT 1,
    p_nodes_written  integer DEFAULT 0
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE v_ops integer;
BEGIN
    -- A degenerate window mints a row no reader can ever match (the cohort
    -- aggregate is a strict overlap test). Refuse rather than create orphan
    -- spend: fail-closed is the whole discipline of this ledger. (#4242: this
    -- guard is also what keeps the ``period_end`` refresh below non-degenerate
    -- — see the migration header.)
    IF p_period_end <= p_period_start THEN
        RAISE EXCEPTION
            'metering_increment: period_end (%) must be after period_start (%)',
            p_period_end, p_period_start;
    END IF;
    INSERT INTO public.metering_records
        (org_id, period, period_start, period_end, write_ops, nodes_written)
    VALUES (p_org_id,
            to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM'),
            p_period_start, p_period_end, p_n, p_nodes_written)
    ON CONFLICT (org_id, period_start)
    DO UPDATE SET
        -- #4242: the WINDOW is not a write-once INSERT default. An
        -- approximated ``period_end`` (20260918000001 / 20260919000001) is
        -- corrected to the authoritative bound on the next increment; without
        -- this the row keeps a window too short for the period it meters, and
        -- the cohort read's overlap test can miss it (fail-open on a spend
        -- ceiling).
        period_end    = EXCLUDED.period_end,
        write_ops     = public.metering_records.write_ops + p_n,
        nodes_written = public.metering_records.nodes_written + p_nodes_written,
        updated_at    = now()
    RETURNING write_ops INTO v_ops;
    RETURN v_ops;
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment(text, timestamptz,
    timestamptz, integer, integer) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment(text, timestamptz,
    timestamptz, integer, integer) TO service_role;

-- ============================================================================
-- 2. metering_increment_ask — the per-query ask accumulator
-- ============================================================================
DROP FUNCTION IF EXISTS public.metering_increment_ask(text, timestamptz,
    timestamptz, integer, integer, integer, double precision);

CREATE FUNCTION public.metering_increment_ask(
    p_org_id       text,
    p_period_start timestamptz,
    p_period_end   timestamptz,
    p_calls        integer DEFAULT 1,
    p_tokens_in    integer DEFAULT 0,
    p_tokens_out   integer DEFAULT 0,
    p_cost_usd     double precision DEFAULT 0
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    IF p_period_end <= p_period_start THEN
        RAISE EXCEPTION
            'metering_increment_ask: period_end (%) must be after period_start (%)',
            p_period_end, p_period_start;
    END IF;
    INSERT INTO public.metering_records
        (org_id, period, period_start, period_end,
         ask_calls, ask_tokens_in, ask_tokens_out, ask_cost_usd)
    VALUES (p_org_id,
            to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM'),
            p_period_start, p_period_end,
            p_calls, p_tokens_in, p_tokens_out, p_cost_usd)
    ON CONFLICT (org_id, period_start)
    DO UPDATE SET
        -- #4242: refresh the window (see metering_increment above). This lane
        -- feeds ``ask_cost_usd``, one of the two columns the cohort SUM reads,
        -- so a stale ``period_end`` here is the fail-open shape directly.
        period_end     = EXCLUDED.period_end,
        ask_calls      = public.metering_records.ask_calls + p_calls,
        ask_tokens_in  = public.metering_records.ask_tokens_in + p_tokens_in,
        ask_tokens_out = public.metering_records.ask_tokens_out + p_tokens_out,
        ask_cost_usd   = public.metering_records.ask_cost_usd + p_cost_usd,
        updated_at     = now();
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment_ask(text, timestamptz,
    timestamptz, integer, integer, integer, double precision)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment_ask(text, timestamptz,
    timestamptz, integer, integer, integer, double precision) TO service_role;

-- ============================================================================
-- 3. metering_increment_capture_cost — the measured capture accumulator
--    (latest definition: 20261001000001, the ``bigint`` token counters)
-- ============================================================================
DROP FUNCTION IF EXISTS public.metering_increment_capture_cost(text,
    timestamptz, timestamptz, integer, bigint, bigint, double precision);

CREATE FUNCTION public.metering_increment_capture_cost(
    p_org_id       text,
    p_period_start timestamptz,
    p_period_end   timestamptz,
    p_calls        integer DEFAULT 0,
    p_tokens_in    bigint DEFAULT 0,
    p_tokens_out   bigint DEFAULT 0,
    p_cost_usd     double precision DEFAULT 0
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- A degenerate window mints a row no reader can ever match (the cohort
    -- aggregate is a strict overlap test). Refuse rather than create orphan
    -- measurement: fail-closed is the discipline of this ledger.
    IF p_period_end <= p_period_start THEN
        RAISE EXCEPTION
            'metering_increment_capture_cost: period_end (%) must be after '
            'period_start (%)', p_period_end, p_period_start;
    END IF;
    INSERT INTO public.metering_records
        (org_id, period, period_start, period_end,
         capture_calls, capture_tokens_in, capture_tokens_out, capture_cost_usd)
    VALUES (p_org_id,
            to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM'),
            p_period_start, p_period_end,
            p_calls, p_tokens_in, p_tokens_out, p_cost_usd)
    ON CONFLICT (org_id, period_start)
    DO UPDATE SET
        -- #4242: refresh the window. ``capture_cost_usd`` is the other column
        -- the cohort SUM reads, so this is the second direct fail-open site.
        period_end         = EXCLUDED.period_end,
        capture_calls      = public.metering_records.capture_calls + p_calls,
        -- coalesce() is belt-and-braces with the NOT NULL DEFAULT above: a
        -- row created by an RPC that predates the columns still increments
        -- instead of sticking at NULL.
        capture_tokens_in  = coalesce(public.metering_records.capture_tokens_in, 0)
                             + p_tokens_in,
        capture_tokens_out = coalesce(public.metering_records.capture_tokens_out, 0)
                             + p_tokens_out,
        capture_cost_usd   = public.metering_records.capture_cost_usd + p_cost_usd,
        updated_at         = now();
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment_capture_cost(text,
    timestamptz, timestamptz, integer, bigint, bigint, double precision)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment_capture_cost(text,
    timestamptz, timestamptz, integer, bigint, bigint, double precision)
    TO service_role;

-- ============================================================================
-- 4. metering_increment_embedding — the embed accumulator
--    (definition from 20261001000004, re-measured as a defect site)
-- ============================================================================
DROP FUNCTION IF EXISTS public.metering_increment_embedding(
    text, timestamptz, timestamptz, integer, bigint, bigint,
    double precision, integer, text, text, boolean);

CREATE FUNCTION public.metering_increment_embedding(
    p_org_id         text,
    p_period_start   timestamptz,
    p_period_end     timestamptz,
    p_calls          integer DEFAULT 0,
    p_texts          bigint DEFAULT 0,
    p_chars          bigint DEFAULT 0,
    p_wall_ms        double precision DEFAULT 0,
    p_skipped        integer DEFAULT 0,
    p_model          text DEFAULT NULL,
    p_revision       text DEFAULT NULL,
    p_identity_mixed boolean DEFAULT false
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- A degenerate window mints a row no reader can ever match (the cohort
    -- aggregate is a strict overlap test). Refuse rather than create orphan
    -- measurement: fail-closed is the discipline of this ledger.
    IF p_period_end <= p_period_start THEN
        RAISE EXCEPTION
            'metering_increment_embedding: period_end (%) must be after '
            'period_start (%)', p_period_end, p_period_start;
    END IF;
    -- ``period`` is NOT NULL with no default (0014) and period_start/period_end
    -- became NOT NULL with no default in 20260918000001, so the derived label
    -- and BOTH window edges must be supplied explicitly — omitting any of them
    -- raises a NOT NULL violation on the INSERT and the increment is dropped.
    INSERT INTO public.metering_records
        (org_id, period, period_start, period_end, embed_calls, embed_texts,
         embed_chars, embed_wall_ms, embed_skipped, embed_model,
         embed_revision, embed_identity_mixed)
    VALUES (p_org_id,
            to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM'),
            p_period_start, p_period_end,
            p_calls, p_texts, p_chars, p_wall_ms, p_skipped,
            p_model, p_revision, p_identity_mixed)
    ON CONFLICT (org_id, period_start)
    DO UPDATE SET
        -- #4242: refresh the window — the third lane that had the omission.
        period_end    = EXCLUDED.period_end,
        -- The mixed flag is computed in its own assignment from the STORED
        -- identity, so a mid-window swap is visible. It reads the row's value
        -- as it was BEFORE this UPDATE — but NOT because it is written first:
        -- PostgreSQL evaluates every `SET` expression against the OLD row
        -- regardless of the order the assignments appear in. That rule is why
        -- `public.metering_records.embed_model` below still means "the value
        -- this row already had"; reordering these clauses would change nothing.
        -- (An earlier version of this comment claimed the opposite — that the
        -- targets are evaluated in order — which would teach a maintainer a
        -- false model of the statement they are about to edit. Found in
        -- review.) The flag is sticky (never reset), paired (the swap term
        -- requires BOTH a stored and an incoming identity), and skip-safe (a
        -- model-less flush cannot latch it).
        embed_identity_mixed =
            coalesce(public.metering_records.embed_identity_mixed, false)
            OR coalesce(p_identity_mixed, false)
            OR (public.metering_records.embed_model IS NOT NULL
                AND p_model IS NOT NULL
                AND (public.metering_records.embed_model IS DISTINCT FROM p_model
                     OR public.metering_records.embed_revision
                        IS DISTINCT FROM p_revision)),
        -- coalesce() on every counter is belt-and-braces with the NOT NULL
        -- DEFAULT above: a row created before the columns existed (or by a
        -- future lane that forgets them) still increments instead of sticking
        -- at NULL.
        embed_calls  = coalesce(public.metering_records.embed_calls, 0) + p_calls,
        embed_texts  = coalesce(public.metering_records.embed_texts, 0) + p_texts,
        embed_chars  = coalesce(public.metering_records.embed_chars, 0) + p_chars,
        embed_wall_ms = coalesce(public.metering_records.embed_wall_ms, 0) + p_wall_ms,
        embed_skipped = coalesce(public.metering_records.embed_skipped, 0) + p_skipped,
        -- A skipped-only flush passes NULL and must NOT erase the identity of
        -- the encoder the window actually used.
        embed_model    = coalesce(p_model,
                                  public.metering_records.embed_model),
        embed_revision = coalesce(p_revision,
                                  public.metering_records.embed_revision),
        updated_at     = now();
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment_embedding(
    text, timestamptz, timestamptz, integer, bigint, bigint,
    double precision, integer, text, text, boolean)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment_embedding(
    text, timestamptz, timestamptz, integer, bigint, bigint,
    double precision, integer, text, text, boolean)
    TO service_role;

-- ============================================================================
-- 5. metering_set_graph_storage — the graph-storage GAUGE
--    (definition from 20261001000005)
--
-- A GAUGE, not an accumulator: the latest reading wins, so its counters are
-- OVERWRITTEN rather than added. It is on this list for the WINDOW, which is
-- the same write-once-INSERT omission — the gauge's own values are not what is
-- being fixed here.
-- ============================================================================
DROP FUNCTION IF EXISTS public.metering_set_graph_storage(
    text, timestamptz, timestamptz, double precision, double precision,
    integer, integer, double precision, double precision, double precision,
    timestamptz);

CREATE FUNCTION public.metering_set_graph_storage(
    p_org_id       text,
    p_period_start timestamptz,
    p_period_end   timestamptz,
    p_total_mb     double precision,
    p_indices_mb   double precision DEFAULT NULL,
    p_samples      integer DEFAULT 100,
    p_repeats      integer DEFAULT 1,
    p_min_mb       double precision DEFAULT NULL,
    p_max_mb       double precision DEFAULT NULL,
    p_spread_mb    double precision DEFAULT 0,
    p_measured_at  timestamptz DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- A degenerate window mints a row no reader can ever match (the cohort
    -- aggregate is a strict overlap test). Refuse rather than create orphan
    -- measurement: fail-closed is the discipline of this ledger.
    IF p_period_end <= p_period_start THEN
        RAISE EXCEPTION
            'metering_set_graph_storage: period_end (%) must be after '
            'period_start (%)', p_period_end, p_period_start;
    END IF;
    -- ``period`` is NOT NULL with no default (0014) and period_start/period_end
    -- became NOT NULL with no default in 20260918000001, so the derived label
    -- and BOTH window edges must be supplied explicitly.
    INSERT INTO public.metering_records
        (org_id, period, period_start, period_end, graph_storage_mb,
         graph_storage_indices_mb, graph_storage_samples, graph_storage_repeats,
         graph_storage_min_mb, graph_storage_max_mb, graph_storage_spread_mb,
         graph_storage_measured_at)
    VALUES (p_org_id,
            to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM'),
            p_period_start, p_period_end, p_total_mb,
            p_indices_mb, p_samples, p_repeats,
            coalesce(p_min_mb, p_total_mb), coalesce(p_max_mb, p_total_mb),
            p_spread_mb, p_measured_at)
    ON CONFLICT (org_id, period_start)
    DO UPDATE SET
        -- #4242: refresh the window. The gauge's own columns are a separate
        -- semantics question; the WINDOW is not — a stale ``period_end`` here
        -- hides this row from the cohort read's overlap test exactly as it
        -- does on the accumulator lanes.
        period_end                = EXCLUDED.period_end,
        -- ⛔ OVERWRITE, never add. This is a GAUGE: the latest reading in the
        -- window wins. ``= EXCLUDED.x`` (not ``coalesce(...) + x``) is the whole
        -- semantics — adding would double-count the same graph's bytes.
        graph_storage_mb          = EXCLUDED.graph_storage_mb,
        graph_storage_indices_mb  = EXCLUDED.graph_storage_indices_mb,
        graph_storage_samples     = EXCLUDED.graph_storage_samples,
        graph_storage_repeats     = EXCLUDED.graph_storage_repeats,
        graph_storage_min_mb      = EXCLUDED.graph_storage_min_mb,
        graph_storage_max_mb      = EXCLUDED.graph_storage_max_mb,
        graph_storage_spread_mb   = EXCLUDED.graph_storage_spread_mb,
        graph_storage_measured_at = EXCLUDED.graph_storage_measured_at,
        updated_at                = now();
END;
$$;

REVOKE ALL ON FUNCTION public.metering_set_graph_storage(
    text, timestamptz, timestamptz, double precision, double precision,
    integer, integer, double precision, double precision, double precision,
    timestamptz)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_set_graph_storage(
    text, timestamptz, timestamptz, double precision, double precision,
    integer, integer, double precision, double precision, double precision,
    timestamptz)
    TO service_role;
