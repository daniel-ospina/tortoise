-- ============================================================================
-- Migration 20261013000002: the OAuth token mint is ONE transaction (#3025)
-- ----------------------------------------------------------------------------
-- 0016 gave the OAuth writer a sequence of PostgREST calls, and every
-- `cp.query` is its own HTTP request = its own transaction
-- (`tortoise/supabase_control.py`). A successful authorization-code exchange
-- was therefore FOUR independent writes across THREE tables:
--
--   1. claim   oauth_codes.used_at / redemption_state='claimed'
--   2. INSERT  oauth_refresh_tokens
--   3. INSERT  oauth_access_tokens
--   4. settle  oauth_codes.redemption_state='minted'
--
-- A failure in the middle left partial state: a live orphan refresh row with
-- the code burned; two live token families; or a live-but-undelivered pair
-- whose presented refresh token was already revoked. #2863 answered that with
-- an IN-PROCESS compensation protocol (soft-revoke what it inserted, re-read to
-- confirm, re-arm `used_at`). That is not atomicity: the compensation runs
-- against the same control plane that just failed, so a compensation failure
-- reproduces the partial state.
--
-- 20260925000002 (#3027) added the durable `redemption_state` machine + the
-- reconciler for the one failure a transaction CANNOT fix (a committed mint
-- whose response was lost), and recorded that #2863's in-process confirmation
-- would be removed "once the state machine is proven". #3025 is that removal.
--
-- WHAT THIS ADDS — two functions, no schema change:
--
--   * `redeem_oauth_code`  — claims the authorization code (the single-use CAS)
--     AND inserts the refresh + access rows, in ONE transaction. A zero-row CAS
--     returns {'status':'not_claimable'} with NOTHING written; any error rolls
--     the whole call back, so a failure leaves zero new rows and an unset
--     `used_at`.
--   * `rotate_oauth_refresh` — claims the presented refresh token
--     (`WHERE revoked_at IS NULL`), inserts the new pair, and revokes the
--     previous access row(s), in ONE transaction. The concurrent-rotator LOSER
--     gets a zero-row claim and returns {'status':'not_claimable'} — with NO
--     orphan pair to roll back, because the pair was never inserted.
--
-- VALIDATION DELIBERATELY STAYS IN PYTHON (`tortoise/oauth.py`). The code row's
-- identity fields (client_id, redirect_uri, code_challenge, scope) are written
-- once by `issue_auth_code` and never updated, so validating a read of them
-- carries no TOCTOU; and keeping scope/error-ordering policy out of SQL matches
-- the repo's existing rule that app policy never lives in a migration
-- (`20261001000002`: "pricing lives in app code, never SQL"). What MUST be in
-- the transaction is the CLAIM + the WRITES, and that is what moved.
--
-- `oauth_codes.redemption_state` stays 'claimed' after this function commits —
-- the DELIVERY GATE (`_settle_redemption`, #3027) is the write that records
-- 'minted', and it is deliberately still a separate statement: a committed mint
-- whose response is lost must remain distinguishable from a replay, which is the
-- whole point of the durable state. So a process death between this function's
-- COMMIT and the settle leaves `claimed` + a live, never-delivered family, which
-- is exactly the residue `_reconcile_claimed_redemption` resolves. The state
-- machine is NOT shrunk by this migration.
--
-- SECURITY DEFINER + `SET search_path = ''` with fully-qualified names, and
-- service_role-only EXECUTE — the same shape as every other mint primitive
-- (`20261001000002` §5). These are token-mint primitives; anon/authenticated
-- must never reach them.
-- ============================================================================

-- ============================================================================
-- 1) redeem_oauth_code — claim the code AND mint the pair, one transaction
-- ============================================================================
-- The CAS filter is the single-use gate and is UNCHANGED from
-- `_consume_code`'s, so single-use semantics (including the loser of a
-- concurrent double-redeem) are identical to what #1264/#3027 ratified. `id`
-- and `code_hash` are BOTH required and both must match, so a caller holding a
-- stale row cannot claim a different code.
--
-- The INSERTs carry `code_id` (the #3027 provenance link) so a code that
-- committed a mint whose response was lost is still discoverable from its
-- grants by the reconciler.
--
-- RETURN CONTRACT: jsonb, `{'status': 'minted' | 'burned' | 'not_claimable'}`.
-- `p_burn` selects the refusal path above; a RAISE (any other error) aborts the
-- transaction — that is the atomicity the issue asks for, and it is pinned by the
-- PGlite suite.
CREATE OR REPLACE FUNCTION public.redeem_oauth_code(
    p_code_id             bigint,
    p_code_hash           text,
    p_client_id           text,
    p_user_id             text,
    p_org_id              text,
    p_scope               text,
    p_redemption_id       text,
    p_refresh_id          text,
    p_refresh_hash        text,
    p_access_id           text,
    p_access_hash         text,
    p_now                 timestamptz,
    p_refresh_expires_at  timestamptz,
    p_access_expires_at   timestamptz,
    p_burn                boolean DEFAULT false
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_id bigint;
BEGIN
    -- The single-use CAS. `chk_oauth_codes_redemption_used_at` permits the new
    -- row shape (used_at NOT NULL, state 'claimed'), and the state transition is
    -- written in the SAME statement as `used_at` so the two can never disagree.
    UPDATE public.oauth_codes
       SET used_at               = p_now,
           redemption_state      = 'claimed',
           redemption_id         = p_redemption_id,
           redemption_settled_at = NULL,
           redemption_note       = NULL
     WHERE id = p_code_id
       AND code_hash = p_code_hash
       AND client_id = p_client_id
       -- Identity is bound in the CAS too, not merely inserted: the caller reads
       -- these two from the same row, so a match is free on every real path, and
       -- binding them makes a caller that passed a MISMATCHED identity fail the
       -- claim (nothing written, `not_claimable`) instead of minting a pair bound
       -- to the wrong org. `rotate_oauth_refresh` already derives identity from
       -- the claimed row; this is the same invariant stated as a predicate.
       AND user_id = p_user_id
       AND org_id = p_org_id
       AND used_at IS NULL
       AND redemption_state = 'unclaimed'
       AND expires_at > p_now
    RETURNING id INTO v_id;

    IF NOT FOUND THEN
        -- A replay, a concurrent claim, or an expired code. NOTHING was
        -- written; the caller re-reads the row READ-ONLY to classify why
        -- (`_observe_code`) and to drive the lazy reconcile.
        RETURN jsonb_build_object('status', 'not_claimable');
    END IF;

    IF p_burn THEN
        -- A refusal the CALLER detected (bad PKCE, client/redirect mismatch,
        -- unsupported scope, unusable org, resource mismatch). The code is
        -- CLAIMED and then BURNED in this one transaction, with no token row
        -- inserted — the durable `claimed -> burned` transition (20260925000002)
        -- is preserved exactly, and `redemption_note='terminal'` keeps the
        -- documented note vocabulary. The reason is logged by the caller; it is
        -- deliberately NOT stored, because the note's job is to separate a
        -- terminal burn from a `backfill` burn in an incident (see 20260925000002).
        UPDATE public.oauth_codes
           SET redemption_state      = 'burned',
               redemption_settled_at = p_now,
               redemption_note       = 'terminal'
         WHERE id = v_id;
        RETURN jsonb_build_object('status', 'burned');
    END IF;

    INSERT INTO public.oauth_refresh_tokens
        (id, token_hash, client_id, user_id, org_id, scope, expires_at,
         revoked_at, rotated_from, created_at, code_id)
    VALUES
        (p_refresh_id, p_refresh_hash, p_client_id, p_user_id, p_org_id,
         p_scope, p_refresh_expires_at, NULL, NULL, p_now, p_code_id);

    INSERT INTO public.oauth_access_tokens
        (id, token_hash, client_id, user_id, org_id, scope, expires_at,
         revoked_at, refresh_token_id, created_at, code_id)
    VALUES
        (p_access_id, p_access_hash, p_client_id, p_user_id, p_org_id,
         p_scope, p_access_expires_at, NULL, p_refresh_id, p_now, p_code_id);

    RETURN jsonb_build_object('status', 'minted');
END;
$$;

-- ============================================================================
-- 2) rotate_oauth_refresh — claim the presented token, mint the pair, revoke
--    the previous access row(s), one transaction
-- ============================================================================
-- The presented row is claimed with a conditional UPDATE (`revoked_at IS NULL`),
-- which is the SAME CAS `_issue_tokens` used, so exactly one concurrent rotator
-- wins. The difference is that the loser no longer has to roll an orphan pair
-- back: the pair is inserted only AFTER the claim wins, inside the same
-- transaction.
--
-- The new rows INHERIT client_id / user_id / org_id / scope / code_id from the
-- claimed row, so the family's identity and its #3027 provenance link cannot
-- drift between the rotation and its parent — and `rotated_from` records the
-- chain as before.
--
-- The previous access row(s) are revoked by `refresh_token_id = <presented>`
-- (ALL live siblings, not just one id the caller happened to read): that is the
-- row set the previous SELECT could only approximate, and a leftover live
-- sibling would authenticate after its refresh token was rotated away.
--
-- DELIBERATE CONSEQUENCE of folding the revoke in: a failure while revoking the
-- previous access row now ABORTS the rotation instead of being logged and
-- tolerated (`_issue_tokens`'s old "lane 3"). That is strictly safer — the
-- presented refresh token stays unrevoked, so the client's retry simply
-- succeeds, instead of leaving a live-but-undelivered pair and a rotated-away
-- grant. The old behaviour is a #2863-era artifact of the writes being
-- independent, and it is pinned by the updated suite.
--
-- RETURN CONTRACT: same jsonb shape as `redeem_oauth_code`, plus the family
-- fields it derived, so the caller can assert the identity it asked for.
CREATE OR REPLACE FUNCTION public.rotate_oauth_refresh(
    p_prev_refresh_id     text,
    p_refresh_id          text,
    p_refresh_hash        text,
    p_access_id           text,
    p_access_hash         text,
    p_now                 timestamptz,
    p_refresh_expires_at  timestamptz,
    p_access_expires_at   timestamptz
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_client_id text;
    v_user_id   text;
    v_org_id    text;
    v_scope     text;
    v_code_id   bigint;
BEGIN
    UPDATE public.oauth_refresh_tokens
       SET revoked_at = p_now
     WHERE id = p_prev_refresh_id
       AND revoked_at IS NULL
    RETURNING client_id, user_id, org_id, scope, code_id
      INTO v_client_id, v_user_id, v_org_id, v_scope, v_code_id;

    IF NOT FOUND THEN
        RETURN jsonb_build_object('status', 'not_claimable');
    END IF;

    INSERT INTO public.oauth_refresh_tokens
        (id, token_hash, client_id, user_id, org_id, scope, expires_at,
         revoked_at, rotated_from, created_at, code_id)
    VALUES
        (p_refresh_id, p_refresh_hash, v_client_id, v_user_id, v_org_id,
         v_scope, p_refresh_expires_at, NULL, p_prev_refresh_id, p_now,
         v_code_id);

    INSERT INTO public.oauth_access_tokens
        (id, token_hash, client_id, user_id, org_id, scope, expires_at,
         revoked_at, refresh_token_id, created_at, code_id)
    VALUES
        (p_access_id, p_access_hash, v_client_id, v_user_id, v_org_id,
         v_scope, p_access_expires_at, NULL, p_refresh_id, p_now, v_code_id);

    -- Revoke the PREVIOUS family's live access rows. Filtered on the PREVIOUS
    -- refresh id, so the row just inserted (which points at the NEW id) is not
    -- touched.
    UPDATE public.oauth_access_tokens
       SET revoked_at = p_now
     WHERE refresh_token_id = p_prev_refresh_id
       AND revoked_at IS NULL;

    RETURN jsonb_build_object(
        'status',    'rotated',
        'client_id', v_client_id,
        'user_id',   v_user_id,
        'org_id',    v_org_id,
        'scope',     v_scope,
        'code_id',   v_code_id
    );
END;
$$;

-- ============================================================================
-- 3) Grant hygiene — service_role ONLY (Supabase's ALTER DEFAULT PRIVILEGES
--    grants EXECUTE to anon/authenticated, and these are mint primitives)
-- ============================================================================
REVOKE ALL ON FUNCTION public.redeem_oauth_code(
    bigint, text, text, text, text, text, text, text, text, text, text,
    timestamptz, timestamptz, timestamptz, boolean)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.redeem_oauth_code(
    bigint, text, text, text, text, text, text, text, text, text, text,
    timestamptz, timestamptz, timestamptz, boolean)
    TO service_role;

REVOKE ALL ON FUNCTION public.rotate_oauth_refresh(
    text, text, text, text, text, timestamptz, timestamptz, timestamptz)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.rotate_oauth_refresh(
    text, text, text, text, text, timestamptz, timestamptz, timestamptz)
    TO service_role;
