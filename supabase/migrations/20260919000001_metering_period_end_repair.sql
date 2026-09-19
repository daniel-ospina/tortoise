-- Migration 20260919000001: repair the metering window anchor for subscription
-- orgs the 20260918000001 backfill could not complete (#4216, lane B7).
--
-- WHY. D10 (adopted, `B7-ROADMAP-2026-09-17.md` §6) fixes the cost meter's
-- window as the subscription's own billing period, read by
-- ``metering._current_period`` as the half-open interval
-- ``[current_period_start, current_period_end)``. The resolver is deliberately
-- intolerant of a HALF-KNOWN anchor: an org that carries a ``subscription_id``
-- but whose interval cannot be resolved RAISES
-- :class:`~tortoise.quota.QuotaCheckError` rather than falling back to the
-- calendar month (a month bucket would put a paying org's spend on a row the
-- cap's windowed read never looks at — the false PASS this lane exists to
-- prevent). Per #3981 that raise is a SIGNAL, not enforcement: every caller
-- absorbs it, alerts the operator, and serves. The CONSEQUENCE, though, is
-- real: such an org's increments are dropped and the pre-spend cohort cost cap
-- CANNOT be enforced for it.
--
-- The 20260918000001 backfill derived only ``current_period_start`` from a
-- known ``current_period_end`` (``WHERE ... current_period_end IS NOT NULL``).
-- An org left with a NULL end by ``checkout.session.completed`` (which wrote
-- ``subscription_id`` and no period at all) was therefore SKIPPED — and
-- silently, because the migration succeeded either way. That is the state this
-- migration removes.
--
-- WHAT THIS ADDS. ``public.metering_repair_period_bounds()`` — an IDEMPOTENT,
-- re-runnable repair (not merely a one-shot backfill):
--
--   1. ``end`` known, ``start`` missing → ``start = end - 1 month`` (the
--      20260918000001 derivation, repeated here so the function is
--      self-contained and safe to re-invoke);
--   2. ``start`` known, ``end`` missing → ``end = start + 1 month`` — the
--      direction the original backfill lacked, and the one that makes a
--      CHECKOUT org metered;
--   3. neither bound known → **underivable from stored data**, and reported
--      LOUDLY (``RAISE WARNING`` per org + the org ids RETURNED to the caller).
--      A row is never silently skipped.
--
-- The month arithmetic is normalised through ``AT TIME ZONE 'UTC'`` for the
-- same reason 20260918000001 does it: ``timestamptz + interval`` runs in the
-- SESSION TimeZone, so a non-UTC default would land the window off the UTC
-- boundary and make the same migration produce different rows per connection.
--
-- WHY (3) IS NOT INVENTED. ``organizations`` stores no instant that is the
-- subscription's period (no ``updated_at``; ``created_at`` is the ORG's age,
-- not the billing cycle). Deriving a window from an unrelated instant would be
-- a WORSE defect than the one this migration fixes: it would silently
-- misattribute spend for months and read as authoritative. The honest answer is
-- that the DB cannot derive it, so the repair names it. Those orgs are repaired
-- the moment the authoritative bounds arrive — the repaired
-- ``checkout.session.completed`` path for new checkouts, a later
-- ``customer.subscription.updated``, or ``billing.reconcile_org`` — and the
-- runtime ``cohort_cost.report_unenforceable_cap`` alert (#3981) already fires
-- for them in the meantime.
--
-- OPERATIONAL NOTE. The function is granted to ``service_role`` only (the
-- ledger repair is not a user surface) and is safe to run at any time: every
-- UPDATE is guarded on the missing bound, so a re-run is a no-op for rows the
-- previous run (or a later webhook) already completed.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.metering_repair_period_bounds()
RETURNS TABLE (org_id text, reason text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    r record;
BEGIN
    -- 1) end known, start missing → derive the start.
    UPDATE public.organizations
       SET current_period_start =
             (current_period_end AT TIME ZONE 'UTC' - interval '1 month')
             AT TIME ZONE 'UTC'
     WHERE subscription_id IS NOT NULL
       AND current_period_end IS NOT NULL
       AND current_period_start IS NULL;

    -- 2) start known, end missing → derive the end. The direction the
    --    20260918000001 backfill lacked; without it a checkout org (which
    --    wrote subscription_id and nothing else) stays unmeterable.
    UPDATE public.organizations
       SET current_period_end =
             (current_period_start AT TIME ZONE 'UTC' + interval '1 month')
             AT TIME ZONE 'UTC'
     WHERE subscription_id IS NOT NULL
       AND current_period_start IS NOT NULL
       AND current_period_end IS NULL;

    -- 3) neither bound known → underivable. Report it LOUDLY; never a silent
    --    skip. The RETURNED rows are the durable record; the WARNING is the
    --    log-visible one.
    FOR r IN
        SELECT o.id
          FROM public.organizations AS o
         WHERE o.subscription_id IS NOT NULL
           AND o.current_period_start IS NULL
           AND o.current_period_end IS NULL
         ORDER BY o.id
    LOOP
        org_id := r.id;
        reason := 'subscription present but neither current_period_start nor '
                  'current_period_end is stored — the subscription window is '
                  'not derivable from the org row';
        RAISE WARNING 'metering window UNDERIVABLE for org %: % (#4216) — the '
                      'meter refuses a calendar-month fallback, so this org''s '
                      'increments are dropped and the cohort cap is '
                      'unenforceable until a checkout/subscription webhook or '
                      'billing.reconcile_org writes the authoritative bounds',
                      r.id, reason;
        RETURN NEXT;
    END LOOP;
END;
$$;

COMMENT ON FUNCTION public.metering_repair_period_bounds() IS
    '#4216: idempotent repair of the metering window anchor. Completes the '
    'half-known interval (start from end, or end from start — the direction '
    '20260918000001 lacked), and RETURNS + WARNs for a subscription org whose '
    'row carries neither bound (underivable from stored data; never silently '
    'skipped). service_role only.';

REVOKE ALL ON FUNCTION public.metering_repair_period_bounds()
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_repair_period_bounds()
    TO service_role;

-- One-time run on deploy. Rows that are already complete are untouched; a
-- both-NULL subscription row raises the WARNING above and is NAMED in the
-- returned set (visible in the migration's own output).
SELECT public.metering_repair_period_bounds();
