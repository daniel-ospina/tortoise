-- Migration 20260918000001: the metering ledger expresses a PERIOD BOUNDARY,
-- not a calendar-month bucket (#3825, lane B7).
--
-- WHY. D10 (adopted, `B7-ROADMAP-2026-09-17.md` §6) fixes the cost meter's
-- window as **the subscription's own billing period**, anchored to
-- ``organizations.subscription_id`` — not a calendar month, not a rolling
-- 30 days. Stripe's own metering product is the convergent source: usage is
-- totalled over the billing period so the meter reconciles with the invoice
-- line (https://docs.stripe.com/billing/subscriptions/usage-based/recording-usage).
--
-- Hitherto the ledger could only express a month: ``period text`` with
-- ``-- 'YYYY-MM' billing period`` and ``PRIMARY KEY (org_id, period)`` (0014),
-- so the row's IDENTITY was a month. A subscription billing period is an
-- INTERVAL whose edges move with the subscription (Stripe advances
-- ``current_period_end`` on every renewal and the start is an instant, not the
-- 1st), so it cannot be expressed by equality on a label. This migration gives
-- the row a half-open interval ``[period_start, period_end)`` and moves the
-- PK onto ``period_start``.
--
-- D13 (adopted) closes the gap D10 left open: ``subscription_id`` is nullable
-- (0006_teams.sql:32), so every free/anon org — precisely the beta cohort the
-- cap targets — has no subscription to anchor to. The adopted fallback is the
-- **calendar month in UTC** (GitHub Copilot, LangSmith, and PymtHouse all
-- document exactly this split: contract period when a subscription exists,
-- calendar month in UTC otherwise). That fallback lives in PYTHON
-- (``metering._current_period``) — not here — because it is a runtime
-- resolution rule, not a schema rule.
--
-- ``period`` IS RETAINED, deliberately, as a DERIVED label (D10 explicitly
-- permits a month as an AGGREGATION sub-period, never as the invoice window).
-- It is a pure function of ``period_start`` — written by the RPCs as
-- ``to_char(period_start AT TIME ZONE 'UTC', 'YYYY-MM')`` — so it can never
-- drift from the key it labels. Dropping it would break ``metering_get``'s
-- ``write_ops`` consumers (#681) for no gain. It is NO LONGER part of the PK.
--
-- OPERATIONAL NOTE (lock): the PK swap below takes ACCESS EXCLUSIVE on
-- ``metering_records`` for one index rewrite. The table is one row per org per
-- period (a few thousand rows at beta scale), so the rewrite is short; the
-- backfill runs BEFORE the NOT NULL is applied so no row is ever unlabelled.
--
-- Ordering (D14): #3780 lands first and KEEPS the month bucket; this migration
-- is the one that re-keys it — including #3780's own third month producer at
-- ``cohort_cost.py:218`` and its ``metering_cohort_spend`` month-equality read.
--
-- ============================================================================
-- ⛔ DEPLOY ORDER — THIS MIGRATION MUST LAND *BEFORE* THE #3825 CODE.
--
-- The anchor SELECT in ``tortoise/metering.py::_metering_anchor`` is
-- **UNCONDITIONAL**: it reads ``o.current_period_start`` for EVERY org, before
-- any subscription check. On a schema that lacks this migration, that SELECT
-- errors for every org — **including the free tier**, whose D13 calendar-month
-- fallback is unreachable because the anchor READ fails first. The metering
-- window is then unresolvable for every org, the cap's read path raises, and
-- requests fail closed. There is no partial rollout in which code-first is
-- safe: deploy this migration first, and only then ship the code.
-- ============================================================================

-- ============================================================================
-- 1. The anchor: ``organizations.current_period_start``
--
-- D10 cannot be implemented against stored data without this. Only
-- ``current_period_end`` is persisted today (0012_teams_billing_columns.sql:24);
-- ``current_period_start`` exists NOWHERE in the repository (verified: zero
-- grep hits). Deriving the start as ``end - 1 month`` is WRONG for annual
-- plans and for any plan change, so it is not the design — it is only the
-- one-time backfill below.
-- ============================================================================

ALTER TABLE public.organizations
    ADD COLUMN IF NOT EXISTS current_period_start timestamptz;

COMMENT ON COLUMN public.organizations.current_period_start IS
    '#3825/D10: the start of the subscription''s current billing period — the '
    'meter window anchor. Populated from Stripe''s authoritative '
    '`current_period_start` on `customer.subscription.updated`. NULL for a '
    'free/anon org (no subscription) — the meter then falls back to the '
    'calendar month in UTC (D13).';

-- One-time backfill for orgs whose last subscription webhook predates this
-- column. ``end - 1 month`` is exactly right for a monthly plan and WRONG for
-- an annual one; it is superseded by the next ``customer.subscription.updated``
-- (which carries the authoritative start). Backfilling a slightly-wrong-but-
-- STABLE instant is deliberate: the alternative is a NULL start, which the
-- runtime resolver treats as an unresolvable anchor (fail-closed) and which
-- would therefore drop every metering increment for that org until its next
-- renewal. A stable approximation that the next webhook corrects is strictly
-- better than a silent metering outage.
UPDATE public.organizations
   SET current_period_start = (current_period_end AT TIME ZONE 'UTC'
                               - interval '1 month') AT TIME ZONE 'UTC'
 WHERE subscription_id IS NOT NULL
   AND current_period_end IS NOT NULL
   AND current_period_start IS NULL;

-- ============================================================================
-- 2. The ledger interval: ``metering_records.period_start`` / ``period_end``
-- ============================================================================

ALTER TABLE public.metering_records
    ADD COLUMN IF NOT EXISTS period_start timestamptz,
    ADD COLUMN IF NOT EXISTS period_end   timestamptz;

COMMENT ON COLUMN public.metering_records.period_start IS
    '#3825: the half-open window [period_start, period_end) this row meters, '
    'in UTC. Part of the PRIMARY KEY. A subscription org uses its billing '
    'period; an org with no subscription uses the calendar month in UTC (D13).';
COMMENT ON COLUMN public.metering_records.period_end IS
    '#3825: EXCLUSIVE upper bound of the metered window. A capture at exactly '
    'this instant belongs to the NEXT row.';
COMMENT ON COLUMN public.metering_records.period IS
    '#3825: DERIVED aggregation label ''YYYY-MM'' = period_start in UTC. NOT '
    'the meter window and NOT part of the PK — D10 permits a month as an '
    'aggregation sub-period, never as the invoice window.';

-- Backfill BEFORE the NOT NULL. A historical row was a MONTH bucket, so its
-- window is that month — an honest label of what it actually measured. (It is
-- NOT reattributed to the subscription period that applied at the time; that
-- instant was never recorded. Historical rows are therefore month-granular by
-- construction, and this migration makes that explicit rather than implied.)
UPDATE public.metering_records
   SET period_start = (period || '-01T00:00:00+00:00')::timestamptz,
       -- Month arithmetic on a ``timestamptz`` runs in the SESSION TimeZone,
       -- so a non-UTC default would land the historical window off the UTC
       -- month boundary (and make the same migration produce different rows
       -- per connection). Normalise through UTC so the boundary is
       -- deterministic.
       period_end   = (((period || '-01T00:00:00+00:00')::timestamptz
                        AT TIME ZONE 'UTC') + interval '1 month')
                       AT TIME ZONE 'UTC'
 WHERE period_start IS NULL;

ALTER TABLE public.metering_records
    ALTER COLUMN period_start SET NOT NULL,
    ALTER COLUMN period_end   SET NOT NULL;

-- The PK swap: the row's IDENTITY becomes the window start, not the month.
ALTER TABLE public.metering_records
    DROP CONSTRAINT IF EXISTS metering_records_pkey;

ALTER TABLE public.metering_records
    ADD PRIMARY KEY (org_id, period_start);

-- ============================================================================
-- 3. The three increment RPCs — the window is a PARAMETER, not a month label
--
-- Each is DROPPED before recreation (not merely CREATE OR REPLACE): a
-- different argument list would create an OVERLOAD and leave the old
-- month-keyed function callable, which is exactly the silent second path this
-- issue exists to remove. The ACL is re-issued after each DROP — 20260915's own
-- comment (lines 719-721) records that a missing REVOKE/GRANT re-issue is how
-- ACLs get lost.
-- ============================================================================

-- 3.1) metering_increment — write-op increment (0014 → 20260813 → 20260915).
DROP FUNCTION IF EXISTS public.metering_increment(text, text, integer, integer);

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
    -- spend: fail-closed is the whole discipline of this ledger.
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
    DO UPDATE SET write_ops = public.metering_records.write_ops + p_n,
                  nodes_written = public.metering_records.nodes_written + p_nodes_written,
                  updated_at = now()
    RETURNING write_ops INTO v_ops;
    RETURN v_ops;
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment(text, timestamptz,
    timestamptz, integer, integer) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment(text, timestamptz,
    timestamptz, integer, integer) TO service_role;

-- 3.2) metering_increment_ask — per-query ask cost (#1987 Task 6).
DROP FUNCTION IF EXISTS public.metering_increment_ask(text, text, integer,
    integer, integer, double precision);

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

-- 3.3) metering_increment_capture_cost — measured capture spend (#3665/#3359).
DROP FUNCTION IF EXISTS public.metering_increment_capture_cost(text, text,
    integer, double precision);

CREATE FUNCTION public.metering_increment_capture_cost(
    p_org_id       text,
    p_period_start timestamptz,
    p_period_end   timestamptz,
    p_calls        integer DEFAULT 0,
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
            'metering_increment_capture_cost: period_end (%) must be after '
            'period_start (%)', p_period_end, p_period_start;
    END IF;
    INSERT INTO public.metering_records
        (org_id, period, period_start, period_end,
         capture_calls, capture_cost_usd)
    VALUES (p_org_id,
            to_char(p_period_start AT TIME ZONE 'UTC', 'YYYY-MM'),
            p_period_start, p_period_end, p_calls, p_cost_usd)
    ON CONFLICT (org_id, period_start)
    DO UPDATE SET
        capture_calls    = public.metering_records.capture_calls + p_calls,
        capture_cost_usd = public.metering_records.capture_cost_usd + p_cost_usd,
        updated_at       = now();
END;
$$;

REVOKE ALL ON FUNCTION public.metering_increment_capture_cost(text,
    timestamptz, timestamptz, integer, double precision)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment_capture_cost(text,
    timestamptz, timestamptz, integer, double precision) TO service_role;

-- ============================================================================
-- 4. The cap's cohort read — a WINDOW, not a month equality
--
-- #3780 shipped ``AND period = p_period`` (20260917000001:87). That is the
-- month bucket baked into the cap's own SQL — the harder thing to change, and
-- exactly the read #3825 must re-key.
--
-- SEMANTICS: rows that INTERSECT the half-open window. A cohort is a set of
-- orgs whose subscriptions have DIFFERENT anchors, so "the cohort's spend in
-- this window" is only well-defined per row — and the two candidate readings
-- are NOT symmetric on a spend CEILING:
--
--   * ``period_start >= S AND period_start < E`` (rows BEGINNING in the
--     window) UNDER-reads every org whose period started before S. On a spend
--     ceiling an under-read is fail-OPEN — the ceiling fires later than it
--     should. That is the one error this lane exists to prevent;
--   * overlap (`period_start < E AND period_end > S`) can OVER-read a row
--     that straddles an edge. On a spend ceiling an over-read is fail-CLOSED —
--     it can only fire earlier.
--
-- Half-open comparison means a row ending exactly at ``S`` is NOT counted (it
-- belongs to the prior window) and a row starting exactly at ``E`` is NOT
-- counted (it belongs to the next). One row per org per period, so the
-- aggregate stays bounded by the cohort size, not by capture volume.
-- ============================================================================

DROP FUNCTION IF EXISTS public.metering_cohort_spend(text[], text);

CREATE FUNCTION public.metering_cohort_spend(
    p_org_ids      text[],
    p_period_start timestamptz,
    p_period_end   timestamptz
)
RETURNS double precision
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT coalesce(sum(coalesce(ask_cost_usd, 0) + coalesce(capture_cost_usd, 0)), 0)
      FROM public.metering_records
     WHERE org_id = ANY(p_org_ids)
       AND period_start < p_period_end
       AND period_end   > p_period_start;
$$;

REVOKE ALL ON FUNCTION public.metering_cohort_spend(text[], timestamptz,
    timestamptz) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_cohort_spend(text[], timestamptz,
    timestamptz) TO service_role;
