-- Migration 20260919000001: repair the metering window anchor for subscription
-- orgs the 20260918000001 backfill could not complete (#4216, lane B7).
--
-- WHY. D10 (adopted, `B7-ROADMAP-2026-09-17.md` §6) fixes the cost meter's
-- window as the subscription's own billing period, read by
-- ``metering._current_period`` as the half-open interval
-- ``[current_period_start, current_period_end)``. The resolver is deliberately
-- intolerant of an UNUSABLE anchor: an org carrying a ``subscription_id`` whose
-- interval cannot be resolved — values missing, unparseable, naive, or inverted
-- (``start >= end``) — RAISES :class:`~tortoise.quota.QuotaCheckError` rather
-- than falling back to the calendar month (a month bucket would put a paying
-- org's spend on a row the cap's windowed read never looks at — the false PASS
-- this lane exists to prevent). Per #3981 that raise is a SIGNAL, not
-- enforcement: every caller absorbs it, alerts the operator, and serves. The
-- CONSEQUENCE, though, is real: such an org's increments are dropped and the
-- pre-spend cohort cost cap CANNOT be enforced for it.
--
-- The 20260918000001 backfill derived only ``current_period_start`` from a
-- known ``current_period_end`` (``WHERE ... current_period_end IS NOT NULL``).
-- An org left NULL-end was therefore SKIPPED — and silently, because the
-- migration succeeded either way. That is the state this migration removes.
--
-- WHAT THIS ADDS. ``public.metering_repair_period_bounds()`` — an IDEMPOTENT,
-- re-runnable repair (not merely a one-shot backfill):
--
--   1. ``end`` known, ``start`` missing → ``start = end - 1 month`` (the
--      20260918000001 derivation, repeated here so the function is
--      self-contained and safe to re-invoke);
--   2. ``start`` known, ``end`` missing → ``end = start + 1 month`` — the
--      direction the original backfill lacked. Its producer ON
--      ``organizations`` is a PARTIAL subscription payload from the
--      ``customer.subscription.updated`` webhook that carried one bound and
--      not the other. (``mirror_subscription`` can leave the same half-known
--      shape on the REGISTRY ``:Team`` twin, which this migration cannot
--      reach — that twin is completed by the next authoritative push and
--      reported by the #3981 runtime alert, never repaired here.) NOTE: since
--      Stripe API ``2025-03-31.basil`` the period
--      fields live on the subscription ITEMS; the webhook readers use
--      ``billing.subscription_period_bounds`` (top-level, then item) so a
--      Basil-or-later account still writes the anchor.
--   3. an org whose interval is UNUSABLE and NOT DERIVABLE from stored data —
--      **both bounds missing** (the shape ``checkout.session.completed`` left
--      behind: it wrote ``subscription_id`` and no period at all), or **both
--      present but inverted/empty** (``start >= end``) — is **reported LOUDLY**
--      (``RAISE WARNING`` per org + the org ids RETURNED to the caller), never
--      silently skipped and never invented.
--
-- The month arithmetic is normalised through ``AT TIME ZONE 'UTC'`` for the
-- same reason 20260918000001 does it: ``timestamptz + interval`` runs in the
-- SESSION TimeZone, so a non-UTC default would land the window off the UTC
-- boundary and make the same migration produce different rows per connection.
--
-- ⚠️ ``+/- 1 month`` is exactly right for a MONTHLY plan and WRONG for an
-- annual one (20260918000001's own caveat). Both directions are therefore a
-- STABLE APPROXIMATION that the next authoritative write supersedes — NOT the
-- design. Two notes on that supersession:
--   * the derivation is superseded on ``organizations`` by the next
--     ``customer.subscription.updated`` (or the repaired
--     ``checkout.session.completed`` path for a new org);
--   * the already-written ``metering_records.period_end`` is NOT refreshed by
--     the increment RPCs' ``ON CONFLICT`` upsert (they update the counters
--     only), so a row written under an approximated window keeps that window
--     until its ``period_start`` advances. Filed as a follow-up to #4216.
--
-- WHY (3) IS NOT INVENTED. ``organizations`` stores no instant that is the
-- subscription's period (no ``updated_at``; ``created_at`` is the ORG's age,
-- not the billing cycle). Deriving a window from an unrelated instant would be
-- a WORSE defect than the one this migration fixes: it would silently
-- misattribute spend for months and read as authoritative. The honest answer is
-- that the DB cannot derive it, so the repair names it. Those orgs are repaired
-- the moment the authoritative bounds arrive — the repaired
-- ``checkout.session.completed`` path for new checkouts or a later
-- ``customer.subscription.updated`` — and the runtime
-- ``cohort_cost.report_unenforceable_cap`` alert (#3981) is the OVERALL detector
-- for every unusable-anchor class.
--
-- ⚠️ The deploy-time report is BEST-EFFORT, not durable: the returned set lives
-- only for that statement, ``db push`` does not print result sets, and this
-- schema-drill's ``db.exec`` discards notices — so in CI the returned rows ARE
-- the evidence and the WARNING may not surface. The durable, ongoing detector
-- is the #3981 runtime alert. (``reconcile_org`` is NOT a repair path here: it
-- has no production caller and is registry-lane only, so it can never repair a
-- Supabase ``organizations`` row.)
--
-- OPERATIONAL NOTE. The function is granted to ``service_role`` only and is safe
-- to run at any time: every UPDATE is guarded on the missing bound, so a re-run
-- is a no-op for rows a previous run (or a later webhook) already completed —
-- and it never touches a row outside the two derivation branches.
--
-- BLANK-SUBSCRIPTION PREDICATE. Every population guard tests
-- ``btrim(subscription_id, blank_chars) <> ''``, where ``blank_chars`` is the
-- FULL whitespace set Python's ``str.strip()`` removes in the runtime
-- authority ``metering._current_period`` (``not str(sub_id).strip()``): the
-- ASCII whitespace, the C0 separators (U+001C–U+001F), NEL (U+0085), NBSP
-- (U+00A0), and the Unicode space separators (U+1680, U+2000–U+200A, U+2028,
-- U+2029, U+202F, U+205F, U+3000). A bare ``btrim(x)`` trims ASCII SPACES
-- ONLY, so, e.g., a tab- or U+2003-only ``subscription_id`` would be treated
-- as a real subscription here while the meter treats the org as free
-- (calendar month, cap enforceable), letting this migration write onto a row
-- outside its two derivation branches. ``blank_chars`` is declared ONCE so
-- the three guards cannot drift. (#4216 review.)
-- ============================================================================

CREATE OR REPLACE FUNCTION public.metering_repair_period_bounds()
RETURNS TABLE (org_id text, reason text)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    -- The whitespace set Python's ``str.strip()`` (``str.isspace()``) removes:
    -- ASCII whitespace + the C0 separators + NEL/NBSP + the Unicode space
    -- separators. Held in ONE place so the three guards below cannot drift
    -- from the runtime authority ``metering._current_period``.
    blank_chars constant text := E' \t\n\v\f\r\u001C\u001D\u001E\u001F\u0085\u00A0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200A\u2028\u2029\u202F\u205F\u3000';
    r record;
BEGIN
    -- 1) end known, start missing → derive the start.
    UPDATE public.organizations
       SET current_period_start =
             (current_period_end AT TIME ZONE 'UTC' - interval '1 month')
             AT TIME ZONE 'UTC'
     WHERE subscription_id IS NOT NULL
       AND btrim(subscription_id, blank_chars) <> ''
       AND current_period_end IS NOT NULL
       AND current_period_start IS NULL;

    -- 2) start known, end missing → derive the end. The direction the
    --    20260918000001 backfill lacked; without it a partially-written anchor
    --    stays unusable.
    UPDATE public.organizations
       SET current_period_end =
             (current_period_start AT TIME ZONE 'UTC' + interval '1 month')
             AT TIME ZONE 'UTC'
     WHERE subscription_id IS NOT NULL
       AND btrim(subscription_id, blank_chars) <> ''
       AND current_period_start IS NOT NULL
       AND current_period_end IS NULL;

    -- 3) an UNUSABLE interval nothing above can complete → report it LOUDLY
    --    (never a silent skip, never an invented window). Two classes:
    --      * both bounds NULL (the checkout shape);
    --      * both bounds present but start >= end (inverted/empty).
    FOR r IN
        SELECT o.id,
               (o.current_period_start IS NULL
                AND o.current_period_end IS NULL) AS both_null
          FROM public.organizations AS o
         WHERE o.subscription_id IS NOT NULL
           AND btrim(o.subscription_id, blank_chars) <> ''
           AND (
                (o.current_period_start IS NULL
                 AND o.current_period_end IS NULL)
             OR (o.current_period_start IS NOT NULL
                 AND o.current_period_end IS NOT NULL
                 AND o.current_period_start >= o.current_period_end)
           )
         ORDER BY o.id
    LOOP
        org_id := r.id;
        reason := CASE
            WHEN r.both_null THEN
                'subscription present but neither current_period_start nor '
                'current_period_end is stored'
            ELSE
                'subscription present but the stored interval is inverted or '
                'empty (current_period_start >= current_period_end)'
        END;
        RAISE WARNING 'metering window UNUSABLE for org %: % (#4216) — the '
                      'meter refuses a calendar-month fallback, so this org''s '
                      'increments are dropped and the cohort cap is '
                      'unenforceable until an authoritative write supplies a '
                      'usable interval', r.id, reason;
        RETURN NEXT;
    END LOOP;

    RETURN;
END;
$$;

COMMENT ON FUNCTION public.metering_repair_period_bounds() IS
    '#4216: idempotent repair of the metering window anchor. Completes a '
    'half-known interval (start from end, or end from start — the direction '
    '20260918000001 lacked), and RETURNS + WARNs for a subscription org whose '
    'interval is unusable and not derivable (both bounds NULL, or inverted) — '
    'never silently skipped and never invented. service_role only. The '
    'deploy-time report is best-effort; the ongoing detector is the #3981 '
    'runtime alert.';

REVOKE ALL ON FUNCTION public.metering_repair_period_bounds()
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_repair_period_bounds()
    TO service_role;

-- One-time run on deploy. Rows that are already complete are untouched; an
-- unusable subscription row raises the WARNING above and is NAMED in the
-- returned set.
SELECT public.metering_repair_period_bounds();
