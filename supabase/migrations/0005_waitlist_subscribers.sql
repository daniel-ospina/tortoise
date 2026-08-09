-- Migration 0005: waitlist_subscribers table for the premiselabs.co landing
-- page waitlist form (issue #373).
--
-- Written by the waitlist-subscribe edge function (service role) when a
-- visitor submits the form: valid non-bot submission → INSERT with consent
-- stamping (consented_at = when consent was given by submitting the form).
-- Emails are captured for launch notifications only; every confirmation
-- email includes an unsubscribe option (mailto, manually processed).
--
-- Defensive reconciliation: a dashboard-created waitlist_subscribers table
-- may pre-exist in the premise-labs project (the half-built function era).
-- CREATE TABLE IF NOT EXISTS silently skips in that case, so this migration
-- (a) adds any missing columns, (b) dedupes legacy rows, and (c) adds the
-- UNIQUE constraint — all idempotent, so `supabase db push` cannot abort on
-- the exact pre-existing state the feature depends on.

-- ============================================================================
-- Table: waitlist_subscribers
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.waitlist_subscribers (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL UNIQUE,
    source        text NOT NULL DEFAULT 'landing_page',
    consented_at  timestamptz NOT NULL DEFAULT now(),
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Column reconciliation for a pre-existing dashboard table (the function's
-- INSERT contract: {email, source} + relied-upon defaults).
ALTER TABLE public.waitlist_subscribers
    ADD COLUMN IF NOT EXISTS source       text NOT NULL DEFAULT 'landing_page';
ALTER TABLE public.waitlist_subscribers
    ADD COLUMN IF NOT EXISTS consented_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE public.waitlist_subscribers
    ADD COLUMN IF NOT EXISTS created_at   timestamptz NOT NULL DEFAULT now();

-- Dedupe legacy rows BEFORE adding the UNIQUE constraint — a pre-existing
-- unconstrained table can hold duplicate emails (the half-built function
-- only ever string-matched "duplicate key" errors), and ADD CONSTRAINT would
-- abort the whole migration on them. Keep the earliest row per email.
DELETE FROM public.waitlist_subscribers a
USING public.waitlist_subscribers b
WHERE a.email = b.email AND a.id > b.id;

-- Defensive UNIQUE constraint (any unique constraint on email; not just the
-- auto-named one — PostgREST on_conflict=email needs an arbiter index).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.waitlist_subscribers'::regclass
          AND contype  = 'u'
          AND pg_get_constraintdef(oid) LIKE 'UNIQUE (email)%'
    ) THEN
        ALTER TABLE public.waitlist_subscribers
            ADD CONSTRAINT waitlist_subscribers_email_key UNIQUE (email);
    END IF;
END
$$;

-- ============================================================================
-- RLS: writes happen only through the edge function's service role. The
-- explicit service_role policy is documentation-convention only (service_role
-- bypasses RLS by default, matching the 0001/0003/0004 style); the real gate
-- is RLS enabled with NO anon/authenticated policy (deny-by-default).
-- ============================================================================
ALTER TABLE public.waitlist_subscribers ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role can manage waitlist subscribers"
    ON public.waitlist_subscribers;
CREATE POLICY "Service role can manage waitlist subscribers"
    ON public.waitlist_subscribers
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
