-- Migration 0012: teams billing columns — Stripe webhook parity (#771 review P1)
-- Epic: #669 plan v2 Task 10 · Issue: #771
--
-- The Stripe webhook (_webhook_apply_event) writes billing state on the
-- registry Team node: tier, stripe_customer_id, subscription_id,
-- subscription_status, customer_email, grace_until, current_period_end.
-- The teams table (0006) already carries tier/stripe_customer_id/
-- subscription_id; the remaining four columns are added here so the
-- webhook's Supabase-mode branch (PR #878) can PATCH the full state onto
-- the teams row — without them the webhook would either silently lose
-- billing state post-registry-delete or (worse) recreate the registry
-- graph via an unguarded write (code-review P1, PR #878).

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS subscription_status text;

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS customer_email text;

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS grace_until timestamptz;

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS current_period_end timestamptz;
