-- ============================================================================
-- Migration 20261030000001: the consent token's single-use register (#3524 §9 step 1)
-- ----------------------------------------------------------------------------
-- WHY A SHARED TABLE IS REQUIRED AT ALL
-- `tortoise/consent_token.py::ProcessLocalJtiStore` bounds replay PER PROCESS. The hosted
-- API runs several Fly machines, so a bound consent token replayed against a machine that
-- has not seen its `jti` mints a SECOND authorization code — the exact replay §2.2's `jti`
-- exists to prevent. Single-use is only a guarantee if every machine consults the same row
-- store, so §2.3 makes a shared mechanism mandatory ("Single-use needs shared state,
-- because the AS is multi-instance").
--
-- WHY *THIS* MECHANISM
-- §2.3 compares (a) a `consent_tickets` table in the session origin's D1 database, redeemed
-- over a server-to-server call, against (b) "a row in the control plane (a Supabase
-- migration) consumed by the AS directly". This is (b), the DECIDED mechanism — §2.3 now
-- records it as decided (#3524 §9 step 1); it read "the one open mechanism choice in this
-- spec … deliberately recorded rather than assumed" only while the choice was open.
--
-- (b) is chosen because the seam already exists AT THE CALL SITE: `hosted_api.py` holds a
-- control-plane client (`cp`) and passes it into `issue_auth_code`, and the entire OAuth
-- family (`oauth_clients`, `oauth_codes`, `oauth_access_tokens`, `oauth_refresh_tokens`)
-- lives in this database. (a) would add a route on the session origin, a new
-- server-to-server authentication for it, and a new failure domain on the consent path —
-- strictly more new boundary for a guarantee this table already provides.
--
-- ONLY THE AS WRITES. Nothing here is written by the minter. Single-use is bounded at
-- REDEMPTION, which is where a replay arrives, so the minting side needs no write
-- credential, no service-role key and no new secret (§2.3's redemption leg).
--
-- WHY ONE STATEMENT DECIDES, NOT A READ-THEN-WRITE
-- Across machines, `SELECT` then `INSERT` is a TOCTOU: two machines can both read "absent"
-- and both mint a code from one consent. The decision is therefore made BY THE CONSTRAINT —
-- a single `INSERT … ON CONFLICT DO NOTHING RETURNING true`, which yields a row for exactly
-- one caller and nothing for the loser. Postgres decides; neither process trusts its own
-- read. (This mirrors `redeem_oauth_code`'s single-use CAS, #3025.)
--
-- NOTHING BROWSER-READABLE: RLS is service_role-only, like every other `oauth_*` table.
--
-- App policy stays in Python (`tortoise/consent_token.py`): what a `jti` means, when it
-- expires, and what a refusal says are all the verifier's, per the repo's rule that policy
-- never lives in a migration (`20261001000002`: "pricing lives in app code, never SQL").
-- This migration owns exactly two things: the uniqueness that makes single-use real, and
-- the bounded prune that keeps the table from growing without limit.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.oauth_consent_tickets (
    -- The token's `jti` (32 random bytes, hex, per §2.1). The PRIMARY KEY is the whole
    -- mechanism: it is what turns a second redemption into zero returned rows.
    jti         text PRIMARY KEY,
    -- The instant the token it stands for can no longer verify (`exp + leeway`). Kept so a
    -- row can be pruned exactly when it stops being load-bearing — never earlier, or a live
    -- token's `jti` would be forgotten and become replayable again.
    expires_at  timestamptz NOT NULL,
    redeemed_at timestamptz NOT NULL DEFAULT now()
);

-- The prune's range predicate. Without it the prune is a full scan of an append-only table.
CREATE INDEX IF NOT EXISTS idx_oauth_consent_tickets_expires
    ON public.oauth_consent_tickets (expires_at);

-- The single-use claim + the bounded prune, in ONE transaction (#3524 §9 step 1).
--
-- Returns TRUE on the first redemption of `p_jti`, FALSE on every later one — and the
-- caller treats a non-boolean as "cannot answer" (503), never as either.
--
-- The prune is BOUNDED for the same reason `sweepExpiredSessions` is (`FLOW_SWEEP_LIMIT`,
-- `session.ts`): this runs on a request path, so its cost must be independent of how far
-- behind the backlog has fallen. `LIMIT` caps the work per call and the next call takes the
-- next slice; steady state is far below the cap, because rows expire 120 s + leeway after
-- their consent and one row is written per redemption.
--
-- The prune runs BEFORE the insert so that a first redemption is never refused by a backlog,
-- and it can only delete rows whose tokens can no longer verify, so it can never delete the
-- row this call is about: the caller passes `expires_at` on the far side of `now()` — the
-- verifier's LAST VALID INSTANT, `exp + CONSENT_TOKEN_LEEWAY_S`, not the raw `exp`.
--
-- ⛔ THAT DISTINCTION IS LOAD-BEARING AND IT IS THE CALLER'S. `verify_consent_token` refuses
-- only when `exp + LEEWAY < now`, so a token stays replayable until `exp + LEEWAY`. If the
-- caller stamped the raw `exp`, then inside `[exp, exp + LEEWAY]` this prune would delete the
-- row that fences the nonce and the INSERT would then succeed — a REPEATABLE replay for the
-- whole leeway window (one consent, N authorization codes). `ProcessLocalJtiStore` already adds
-- the leeway; the shared store must too, and
-- `tests/test_oauth_consent_jti_store.py::test_a_row_is_pruned_only_once_its_token_can_no_longer_verify`
-- drives that exact boundary rather than trusting the arithmetic.
CREATE OR REPLACE FUNCTION public.consume_oauth_consent_ticket(
    p_jti        text,
    p_expires_at timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
    claimed boolean;
BEGIN
    -- A blank nonce is refused rather than recorded: an empty PRIMARY KEY would be claimed
    -- once and then answer "replay" to every malformed token that carried it.
    IF p_jti IS NULL OR p_jti = '' THEN
        RETURN false;
    END IF;

    DELETE FROM public.oauth_consent_tickets
     WHERE jti IN (
        SELECT jti FROM public.oauth_consent_tickets
         WHERE expires_at < now()
         ORDER BY expires_at
         LIMIT 500
     );

    INSERT INTO public.oauth_consent_tickets (jti, expires_at)
         VALUES (p_jti, p_expires_at)
    ON CONFLICT (jti) DO NOTHING
      RETURNING true INTO claimed;

    -- `claimed` is NULL when the insert conflicted = the nonce was already spent.
    RETURN COALESCE(claimed, false);
END;
$$;

COMMENT ON TABLE public.oauth_consent_tickets IS
    'Spent consent-token nonces (#3524 §9 step 1). One row per REDEEMED jti; the PK is the single-use guarantee across machines. Written by the AS only.';
COMMENT ON FUNCTION public.consume_oauth_consent_ticket(text, timestamptz) IS
    'Claims a consent-token jti atomically: TRUE on first use, FALSE on replay. One INSERT .. ON CONFLICT statement, so the constraint decides the winner, not a read-then-write.';

-- ============================================================================
-- RLS + grant hygiene — service_role ONLY
-- ----------------------------------------------------------------------------
-- ⛔ WITHOUT THIS THE TABLE AND THE FUNCTION ARE ANON-WRITABLE, WHICH DEFEATS THE WHOLE
-- MECHANISM. Supabase's default privileges expose a new `public` table through PostgREST to
-- `anon`/`authenticated`, and the anon key is PUBLIC by construction — the consent page embeds
-- it in its HTML (tool/code comment §2.3 says exactly that, which is why the signing key must
-- not be it). A reader holding it could then:
--   * `DELETE /rest/v1/oauth_consent_tickets?jti=eq.<jti>` and replay a captured token into a
--     SECOND authorization code (the replay §2.2's `jti` exists to bound);
--   * INSERT rows with a far-future `expires_at` that the oldest-first bounded prune never
--     collects, growing the table without limit;
--   * call `consume_oauth_consent_ticket` directly, pre-spending arbitrary nonces (a targeted
--     denial of another visitor's consent).
-- So the table's own comment above ("service_role only") is made TRUE here rather than assumed.
-- Mirrors 0016_oauth.sql's RLS block and 20261013000002's grant pair.
-- ============================================================================
ALTER TABLE public.oauth_consent_tickets ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS oauth_consent_tickets_service_role_all ON public.oauth_consent_tickets;
CREATE POLICY oauth_consent_tickets_service_role_all ON public.oauth_consent_tickets
    FOR ALL TO service_role USING (true) WITH CHECK (true);

REVOKE ALL ON FUNCTION public.consume_oauth_consent_ticket(text, timestamptz)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.consume_oauth_consent_ticket(text, timestamptz)
    TO service_role;

-- The TABLE grants too, not just RLS. RLS alone is functionally sufficient (with no policy for
-- `anon` a DELETE matches no row), but it is a ROW filter sitting on top of a table-wide GRANT
-- that Supabase's default privileges hand to `anon` and `authenticated` — so the table would read
-- as shared and any future `USING (true)` policy would open it. Revoking the grant makes the
-- boundary the privilege, which `has_table_privilege` can also assert in
-- `supabase/tests/20261030000001_oauth_consent_tickets.sql` (RLS is invisible to it, so a
-- RLS-only posture cannot be pinned there).
REVOKE ALL ON TABLE public.oauth_consent_tickets FROM public, anon, authenticated;
GRANT ALL ON TABLE public.oauth_consent_tickets TO service_role;
