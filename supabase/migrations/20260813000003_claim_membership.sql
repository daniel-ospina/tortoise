-- Migration 20260813000003: claim_membership RPC — anonymous team attaches a
-- provider-verified identifier (#1082, PR1 — indicators 1,2,3,5).
--
-- Zero-email teams (anon-/reg- identity rows, NULL user_id) can't complete
-- setup: no verified identifier, no dashboard session access, no recovery.
-- claim_membership attaches a genuinely verified identifier (Supabase user
-- uuid) to the EXISTING owner membership row — same key, same team, memories
-- intact — and gates the PR2 ceiling raise on that link.
--
-- ⛔ Authorization binding (solution-verify P1): the RPC NEVER accepts
-- team_id/identity from the request body. reg- identities are deterministic
-- (reg-<sha256(email)[:12]>) and anon identities surface in
-- api_keys.created_by — client-supplied team_id/identity would let any key
-- + any session JWT claim ANY team. The team is resolved INSIDE the RPC from
-- api_keys.lookup_hash (unique, 0007) + revoked_at IS NULL — the
-- authoritative key→team binding (NOT team_memberships.lookup_hash, which is
-- non-unique and revocation-blind). The key's team is the only thing
-- claimable. api_keys.created_by carries the anon identity server-side for
-- provenance.
--
-- Security posture:
--  - SECURITY DEFINER, service_role ONLY, NO auth.uid() inside (P2-FIX-J):
--    the session JWT is verified server-side in hosted_api; the RPC is
--    service-role — mirroring the tenant-provision pattern (0010).
--  - Caller auth at the API layer: hosted_api validates the session JWT +
--    the provider-verified-email invariant externally before the RPC.
--
-- Body order (implementer advisory 3 — P4): resolve team → noop-check →
-- claim-or-409 → merge-or-409 → email upsert-or-409.
--
-- New partial indexes:
--  - uq_member_owner ON team_memberships(team_id) WHERE role='owner' AND
--    team_id <> '' AND status='active' (P3-FIX-P + solution-verify P1: the
--    handle_new_user placeholder rows all share team_id='' + role='owner' —
--    WITHOUT the team_id <> '' exclusion, the 2nd+ signup raises a unique
--    violation inside the trigger → platform-wide signup breakage on deploy.
--    `AND status='active'` matches the anon predicate).
--  - uq_teams_email ON teams(email) WHERE email IS NOT NULL (P3-FIX-S:
--    cross-team email collision → 409 email_in_use).
--  - audit_events.detail JSONB — the team_claim audit detail (provider,
--    email, user_id; 0002 has no provider/email columns).
--
-- Pre-scan/abort (solution-verify P1): the migration aborts with a report
-- BEFORE creating the indexes when ANY existing team has 2 active owner rows
-- (uq_member_owner would fail) or 2 teams share an email (uq_teams_email
-- would fail) — fail loudly on deploy rather than break the signup path or
-- silently duplicate data.

-- ============================================================================
-- 1) audit_events.detail — JSONB detail column for team_claim audit
-- ============================================================================
ALTER TABLE public.audit_events
    ADD COLUMN IF NOT EXISTS detail jsonb;

-- ============================================================================
-- 2) Pre-scan/abort — duplicate owners or duplicate emails abort the deploy
--    with a report BEFORE the indexes are created (a raw index failure would
--    surface a cryptic constraint error; the pre-scan names the teams). On a
--    clean DB both counts are 0 and the migration proceeds.
-- ============================================================================
DO $$
DECLARE
    v_dup_owners int;
    v_dup_emails int;
BEGIN
    SELECT count(*) INTO v_dup_owners FROM (
        SELECT team_id
          FROM public.team_memberships
         WHERE role = 'owner' AND team_id <> '' AND status = 'active'
         GROUP BY team_id
        HAVING count(*) > 1
    ) d;
    IF v_dup_owners > 0 THEN
        RAISE EXCEPTION
            '20260813000003 abort: % team(s) have MULTIPLE active owner rows '
            '(uq_member_owner would fail) — reconcile owners before applying '
            '(see docs/plans/2026-08-13-1082-claim-path.md)',
            v_dup_owners;
    END IF;

    SELECT count(*) INTO v_dup_emails FROM (
        SELECT email
          FROM public.teams
         WHERE email IS NOT NULL
         GROUP BY email
        HAVING count(*) > 1
    ) d;
    IF v_dup_emails > 0 THEN
        RAISE EXCEPTION
            '20260813000003 abort: % duplicate team email(s) (uq_teams_email '
            'would fail) — reconcile before applying',
            v_dup_emails;
    END IF;
END $$;

-- ============================================================================
-- 3) Owner ≤1 invariant (P3-FIX-P + solution-verify P1) — placeholder
--    exclusion (team_id <> '') is CRITICAL, see header note.
-- ============================================================================
CREATE UNIQUE INDEX IF NOT EXISTS uq_member_owner
    ON public.team_memberships (team_id)
    WHERE role = 'owner' AND team_id <> '' AND status = 'active';

-- ============================================================================
-- 4) Verified-email uniqueness (P3-FIX-S) — cross-team collision → 409.
-- ============================================================================
CREATE UNIQUE INDEX IF NOT EXISTS uq_teams_email
    ON public.teams (email)
    WHERE email IS NOT NULL;

-- ============================================================================
-- 5) claim_membership — SECURITY DEFINER, service_role ONLY
-- ============================================================================
CREATE OR REPLACE FUNCTION public.claim_membership(
    p_lookup_hash text,
    p_user_id     uuid,
    p_email       text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_team_id         text;
    v_key_created_via text;
    v_key_expires_at  timestamptz;
    -- team_memberships.id is uuid (0001: `id uuid PRIMARY KEY DEFAULT
    -- gen_random_uuid()`) — the row-id anchors must match.
    v_owner_row_id    uuid;
    v_owner_lookup    text;
    v_owner_key_hash  text;
    v_existing_row_id uuid;
    v_placeholder_id  uuid;
    v_idempotent      boolean := false;
BEGIN
    IF p_lookup_hash IS NULL OR p_lookup_hash = '' THEN
        RAISE EXCEPTION 'claim_membership:key_required';
    END IF;
    IF p_user_id IS NULL THEN
        RAISE EXCEPTION 'claim_membership:user_required';
    END IF;

    -- ── Step 1: resolve the team from api_keys (authoritative key→team
    -- binding — unique lookup_hash, 0007; revocation-aware). Reject
    -- bootstrap/session keys (implementer advisory 1: future-proofs the
    -- merge/promote path against member-key→owner conversion) and expired
    -- keys (advisory 3, #742 parity — provisioned keys have NULL, impact
    -- ~0).
    SELECT team_id, created_via, expires_at
      INTO v_team_id, v_key_created_via, v_key_expires_at
      FROM public.api_keys
     WHERE lookup_hash = p_lookup_hash AND revoked_at IS NULL
     LIMIT 1;

    IF v_team_id IS NULL THEN
        RAISE EXCEPTION 'claim_membership:key_not_found';
    END IF;
    IF v_key_created_via = 'bootstrap' THEN
        RAISE EXCEPTION 'claim_membership:key_not_claimable';
    END IF;
    IF v_key_expires_at IS NOT NULL AND v_key_expires_at <= now() THEN
        RAISE EXCEPTION 'claim_membership:key_expired';
    END IF;

    -- ── Step 2 (P3-FIX-Q): idempotent re-claim — the owner row is ALREADY
    -- linked to this user → noop success (email still overwritten —
    -- P1-FIX-B, unconditional). v_idempotent is reported to the caller.
    IF EXISTS (
        SELECT 1 FROM public.team_memberships
         WHERE team_id = v_team_id AND user_id = p_user_id
           AND role = 'owner' AND status = 'active'
    ) THEN
        v_idempotent := true;
    ELSE

    -- ── Step 3: find the NULL-user_id owner row (the anon anchor).
    SELECT id, lookup_hash, key_hash
      INTO v_owner_row_id, v_owner_lookup, v_owner_key_hash
      FROM public.team_memberships
     WHERE team_id = v_team_id AND role = 'owner'
       AND user_id IS NULL AND status = 'active'
     LIMIT 1;

    IF v_owner_row_id IS NULL THEN
        -- 0 rows → already claimed by a DIFFERENT user (first-claim-wins,
        -- concurrent-safe via the MVCC row lock on the UPDATE below).
        RAISE EXCEPTION 'claim_membership:already_claimed';
    END IF;

    -- ── Step 4: does a membership row already exist for (p_user_id,
    -- v_team_id)? uq_member_team (0003) would reject the plain UPDATE, so
    -- merge/promote: DROP the identity row FIRST (P3-FIX-R — promote-first
    -- violates the new uq_member_owner index mid-transaction), copy the
    -- identity row's key material, and promote the existing row to
    -- owner/active (a 'removed' row promoted to owner must reactivate —
    -- P4).
    SELECT id INTO v_existing_row_id
      FROM public.team_memberships
     WHERE user_id = p_user_id AND team_id = v_team_id
     LIMIT 1;

    IF v_existing_row_id IS NOT NULL THEN
        DELETE FROM public.team_memberships WHERE id = v_owner_row_id;
        UPDATE public.team_memberships
           SET role        = 'owner',
               status      = 'active',
               identity    = NULL,
               lookup_hash = COALESCE(lookup_hash, v_owner_lookup),
               key_hash    = COALESCE(key_hash, v_owner_key_hash),
               updated_at  = now()
         WHERE id = v_existing_row_id;
    ELSE
        -- ── Step 5: plain claim — link the verified user to the owner row
        -- and clear the anon identity anchor. The `user_id IS NULL` conjunct
        -- is the race guard (#1082 review P1-1): under READ COMMITTED a
        -- concurrent claim's UPDATE re-evaluates EPQ against the new row
        -- version — with only `id` it would match again and silently
        -- overwrite (last-writer-wins defeats first-claim-wins). With the
        -- conjunct, the loser's UPDATE affects 0 rows → already_claimed.
        UPDATE public.team_memberships
           SET user_id   = p_user_id,
               identity  = NULL,
               updated_at = now()
         WHERE id = v_owner_row_id AND user_id IS NULL;

        IF NOT FOUND THEN
            -- Lost the row to a concurrent claim — first-claim-wins.
            RAISE EXCEPTION 'claim_membership:already_claimed';
        END IF;
    END IF;
    END IF;  -- end of idempotent-re-claim IF/ELSE

    -- ── Step 6: email overwrite INSIDE the same transaction (P2-FIX-K).
    -- teams.email = verified OAuth email, unconditional (P1-FIX-B). The
    -- unique index uq_teams_email raises 23505 on a cross-team collision →
    -- caught below → 409 email_in_use (P3-FIX-S).
    UPDATE public.teams SET email = p_email
     WHERE id = v_team_id;

    -- ── Step 7: drop any leftover placeholder row for this user
    -- (team_id='' sentinel; P3-FIX-Q tail — mirror of provision_team step 4).
    -- Runs on EVERY success path (fresh claim, merge, AND idempotent re-claim).
    SELECT id INTO v_placeholder_id
      FROM public.team_memberships
     WHERE user_id = p_user_id AND team_id = ''
     LIMIT 1;
    IF v_placeholder_id IS NOT NULL THEN
        DELETE FROM public.team_memberships WHERE id = v_placeholder_id;
    END IF;

    RETURN jsonb_build_object('status', 'claimed', 'team_id', v_team_id,
                              'idempotent', v_idempotent);
EXCEPTION
    WHEN unique_violation THEN
        IF SQLERRM LIKE '%uq_teams_email%' THEN
            RAISE EXCEPTION 'claim_membership:email_in_use';
        END IF;
        RAISE;
END;
$$;

REVOKE ALL ON FUNCTION public.claim_membership FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_membership TO service_role;
