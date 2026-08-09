-- Migration 0005: waitlist_subscribers table for the premiselabs.co landing
-- page waitlist form (issue #373).
--
-- Written by the waitlist-subscribe edge function (service role) when a
-- visitor submits the form: valid non-bot submission → INSERT with consent
-- stamping (consented_at = when consent was given by submitting the form).
-- Emails are captured for launch notifications only; every confirmation
-- email includes an unsubscribe option.
--
-- NOTE: a dashboard-created waitlist_subscribers table may pre-exist in the
-- premise-labs project (the half-built function era). CREATE TABLE IF NOT
-- EXISTS would silently skip the inline UNIQUE in that case, so the UNIQUE
-- constraint is ALSO added defensively via a DO block (idempotent).

-- ============================================================================
-- Table: waitlist_subscribers
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.waitlist_subscribers (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email         text NOT NULL UNIQUE,
    source        text NOT NULL DEFAULT 'landing_page',
    consented_at  timestamptz NOT NULL DEFAULT now(),
    created_at    timestamptz NOT NULL DEFAULT now(),
    unsubscribed_at timestamptz
);

-- Defensive UNIQUE constraint: a pre-existing dashboard table (created
-- without the constraint) must still get email dedup — the function's
-- on_conflict=email dedup depends on it.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.waitlist_subscribers'::regclass
          AND conname  = 'waitlist_subscribers_email_key'
    ) THEN
        ALTER TABLE public.waitlist_subscribers
            ADD CONSTRAINT waitlist_subscribers_email_key UNIQUE (email);
    END IF;
END
$$;

-- ============================================================================
-- RLS: writes happen only through the edge function's service role (bypasses
-- RLS); anon/authenticated have NO access. Matches the 0003/0004 convention
-- of an explicit service_role policy.
-- ============================================================================
ALTER TABLE public.waitlist_subscribers ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role manages waitlist subscribers"
    ON public.waitlist_subscribers;
CREATE POLICY "Service role manages waitlist subscribers"
    ON public.waitlist_subscribers
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
