-- Migration: deck_views — who opened a link-delivered deck.
--
-- The /investors page gates the deck behind an email (a DocSend-style cover:
-- an address is the price of reading it). The address given there is recorded
-- HERE and nowhere else, because it is a view record and not a subscription.
-- Joining the mailing list stays a separate, explicit act on the same page —
-- the "Stay in touch" form, which writes `waitlist_subscribers`. Keeping the
-- two apart is the point: a reader who traded an address for access has not
-- asked to be mailed, and merging them would put someone on a list they never
-- joined.
--
-- Append-only: one row per recorded view, so the row count answers "how often".
-- The page's gate asks once per BROWSER (the unlock is remembered in
-- localStorage), which bounds how often a reader is asked, not how many rows
-- they can produce — a cleared or separate browser asks again.
--
-- Idempotent (CREATE … IF NOT EXISTS; the policy is dropped before it is
-- recreated) so a re-run cannot abort, matching the 0005 convention for this
-- schema. Unlike 0005 there is no column/constraint reconciliation to do: that
-- migration had to repair a table a dashboard may have pre-created, whereas
-- deck_views has no pre-existing era to reconcile.

CREATE TABLE IF NOT EXISTS public.deck_views (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email      text NOT NULL,
    deck       text NOT NULL DEFAULT 'investors',
    viewed_at  timestamptz NOT NULL DEFAULT now()
);

-- The read an operator actually runs: one deck, newest first.
CREATE INDEX IF NOT EXISTS deck_views_deck_viewed_at_idx
    ON public.deck_views (deck, viewed_at DESC);

-- RLS with no anon/authenticated policy: writes happen only through the
-- deck-view edge function's service role (which bypasses RLS). The explicit
-- service_role policy is documentation-convention, matching 0005.
ALTER TABLE public.deck_views ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Service role can manage deck views" ON public.deck_views;
CREATE POLICY "Service role can manage deck views"
    ON public.deck_views
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);
