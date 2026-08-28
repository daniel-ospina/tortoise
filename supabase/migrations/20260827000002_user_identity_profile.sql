-- ============================================================================
-- Migration 20260827000001: identity-model substrate — user profile + login
-- methods (issue #1765)
--
-- Four changes, one migration (plan Task 1):
--
--  1) user_unlink_permits + link_intents — control-plane tables for the
--     atomic unlink floor and replay-safe link intents. RLS deny-by-default
--     (service_role only), mirroring oauth_* (0016). Partial unique indexes
--     are the READ-COMMITTED isolation backstops: at most ONE pending permit
--     per user, and a nonce is consumed at most once.
--
--  2) user_identity_inventory(p_user_id) — SECURITY DEFINER RPC that answers
--     "what login methods does this user have?" from auth.identities +
--     auth.users.encrypted_password (#2085: updateUser({password}) creates no
--     email identity row, so password capability is a separate signal).
--     login_methods = count(provider NOT IN ('email')) + email_method where
--     email_method := (users.email IS NOT NULL AND email_confirmed_at IS NOT
--     NULL) OR has_password. NEVER count(provider <> 'email') — COUNT(expr)
--     counts non-NULL, and the comparison is FALSE (not NULL) for email rows
--     → overcount (plan-review P1).
--
--  3) reserve_unlink(p_user_id, p_identity_id) — atomic floor reservation:
--     TTL-ages stale permits (>5 min — sweep for crash windows, >15s GoTrue
--     timeout), verifies identity ownership, checks (login_methods - pending
--     - 1) >= 2, inserts; the partial unique index is the two-tab backstop
--     (second concurrent INSERT → 23505 → reserve_unlink:floor_violated).
--     Zero-row INSERT causes are distinguished (identity_not_found vs
--     floor_violated).
--
--  4) teams.email demotion + claim changes: drop uq_teams_email (email is a
--     USER property now, not a globally-unique team anchor); claim_membership
--     stops writing teams.email (Step-6 removed; email_in_use branch dead);
--     claim migrates anon-/reg- created_by keys to the claimer (team-scoped,
--     parenthesized predicate); new UNIQUE partial index on
--     team_memberships(identity) for unclaimed owners = signup idempotency
--     backstop (register hashes sha256(email.lower()) — the pre-scan must
--     catch any pre-existing duplicates BEFORE the index creation, or the
--     deploy fails with a cryptic constraint error).
--
-- Re-apply is idempotent: CREATE TABLE IF NOT EXISTS / CREATE OR REPLACE /
-- DROP IF EXISTS / IF NOT EXISTS throughout.
-- ============================================================================

-- ============================================================================
-- 1) Tables: user_unlink_permits + link_intents
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.user_unlink_permits (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     uuid NOT NULL,
    identity_id uuid NOT NULL,
    consumed_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.link_intents (
    nonce      text PRIMARY KEY,
    user_id    uuid NOT NULL,
    provider   text NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Isolation backstops (READ COMMITTED: a second concurrent INSERT cannot see
-- the first's uncommitted row, so the predicate alone would double-grant;
-- the partial unique index rejects it → 23505).
CREATE UNIQUE INDEX IF NOT EXISTS uq_user_unlink_permits_active
    ON public.user_unlink_permits (user_id) WHERE consumed_at IS NULL;

-- consumed-once for link intents (locked decision: SQL enforcement — the
-- guarded UPDATE + this index make replay atomic).
CREATE UNIQUE INDEX IF NOT EXISTS uq_link_intents_nonce_active
    ON public.link_intents (nonce) WHERE consumed_at IS NULL;

-- ============================================================================
-- RLS + grants: deny-by-default (service_role manages; the API touches these
-- only through SECURITY DEFINER RPCs, so this is defense-in-depth).
-- ============================================================================
ALTER TABLE public.user_unlink_permits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.link_intents ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.user_unlink_permits FROM anon, authenticated, public;
REVOKE ALL ON public.link_intents FROM anon, authenticated, public;
GRANT ALL ON public.user_unlink_permits TO service_role;
GRANT ALL ON public.link_intents TO service_role;

-- ============================================================================
-- 2) user_identity_inventory — login-method inventory (SECURITY DEFINER)
-- ============================================================================
CREATE OR REPLACE FUNCTION public.user_identity_inventory(p_user_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_email            text;
    v_email_confirmed  timestamptz;
    v_encrypted_pwd    text;
    v_has_password     boolean;
    v_email_method     boolean;
    v_oauth_methods    int;
    v_login_methods    int;
    v_methods          jsonb;
    v_keys_tier        int;
BEGIN
    SELECT email, email_confirmed_at, encrypted_password
      INTO v_email, v_email_confirmed, v_encrypted_pwd
      FROM auth.users WHERE id = p_user_id;

    IF v_email IS NULL AND v_email_confirmed IS NULL AND v_encrypted_pwd IS NULL
       AND NOT EXISTS (SELECT 1 FROM auth.identities i WHERE i.user_id = p_user_id) THEN
        -- Unknown user (or zero-method user) — never an error; 0 methods.
        RETURN jsonb_build_object(
            'methods', '[]'::jsonb, 'has_password', false,
            'email_method', false, 'login_methods', 0,
            'keys_tier', 0, 'banner', jsonb_build_object('show', false));
    END IF;

    -- has_password: OAuth-created users have encrypted_password = '' OR NULL
    -- (hosted observed: NULL) — either must be FALSE.
    v_has_password := v_encrypted_pwd IS NOT NULL AND v_encrypted_pwd <> '';

    -- email_method: recovery capability off auth.users.email + confirmation
    -- (NOT the email identity row — hosted check (b) = NO: OAuth signups have
    -- no email identity rows; the identity-row conjunct would undercount).
    v_email_method := (v_email IS NOT NULL AND v_email_confirmed IS NOT NULL) OR v_has_password;

    -- login_methods: OAuth/phone methods + email_method. NEVER
    -- count(provider <> 'email') — COUNT(expr) counts non-NULL rows and the
    -- comparison is FALSE (not NULL) for email rows → double-count.
    SELECT count(*) INTO v_oauth_methods
      FROM auth.identities i
     WHERE i.user_id = p_user_id AND i.provider NOT IN ('email');

    v_login_methods := v_oauth_methods + CASE WHEN v_email_method THEN 1 ELSE 0 END;

    SELECT COALESCE(jsonb_agg(jsonb_build_object(
               'id', i.id,
               'provider', i.provider,
               'provider_id', i.provider_id,
               'email_confirmed_at', v_email_confirmed)
             ORDER BY i.created_at), '[]'::jsonb)
      INTO v_methods
      FROM auth.identities i
     WHERE i.user_id = p_user_id;

    -- keys tier: human-minted keys (created_by = this user uuid), non-revoked
    -- and enabled. anon-/st_/reg-/client/NULL attribution is EXCLUDED (agent
    -- principals + bootstrap keys are not a human login method).
    SELECT count(*) INTO v_keys_tier
      FROM public.api_keys k
     WHERE k.created_by = p_user_id::text AND k.revoked_at IS NULL AND k.enabled;

    RETURN jsonb_build_object(
        'methods', v_methods,
        'has_password', v_has_password,
        'email_method', v_email_method,
        'login_methods', v_login_methods,
        'keys_tier', v_keys_tier,
        'banner', jsonb_build_object(
            'show', v_login_methods <= 1
                    AND NOT (v_email IS NOT NULL AND v_email_confirmed IS NOT NULL AND v_has_password)));
END;
$$;

-- ============================================================================
-- 3) reserve_unlink — atomic floor reservation (SECURITY DEFINER)
-- ============================================================================
CREATE OR REPLACE FUNCTION public.reserve_unlink(p_user_id uuid, p_identity_id uuid)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_login_methods int;
    v_pending       int;
    v_owns          boolean;
BEGIN
    -- 1) TTL aging: release permits stranded by a crash between reserve and
    --    consume (TTL > GoTrue DELETE timeout 15s → 5 min). Without this the
    --    unique index would permanently block the user (self-DoS lockout).
    UPDATE public.user_unlink_permits
       SET consumed_at = now()
     WHERE user_id = p_user_id AND consumed_at IS NULL
       AND created_at < now() - interval '5 minutes';

    -- 2) Identity must belong to the user (zero-row INSERT cause #1).
    SELECT EXISTS (SELECT 1 FROM auth.identities i
                    WHERE i.id = p_identity_id AND i.user_id = p_user_id)
      INTO v_owns;
    IF NOT v_owns THEN
        RAISE EXCEPTION 'reserve_unlink:identity_not_found';
    END IF;

    -- 3) Floor: post-unlink login methods >= 2 ("never below 2 ways in" —
    --    stricter than GoTrue's native identity-row floor, which counts
    --    identity rows only, not password capability).
    SELECT (public.user_identity_inventory(p_user_id)->>'login_methods')::int
      INTO v_login_methods;
    SELECT count(*) INTO v_pending
      FROM public.user_unlink_permits
     WHERE user_id = p_user_id AND consumed_at IS NULL;

    IF v_login_methods - v_pending - 1 < 2 THEN
        RAISE EXCEPTION 'reserve_unlink:floor_violated';
    END IF;

    -- 4) Insert. The partial unique index is the READ-COMMITTED backstop:
    --    a concurrent reserve's uncommitted row is invisible to the pending
    --    count, but the second INSERT hits the index → 23505 → floor_violated.
    BEGIN
        INSERT INTO public.user_unlink_permits (user_id, identity_id)
        VALUES (p_user_id, p_identity_id);
    EXCEPTION WHEN unique_violation THEN
        RAISE EXCEPTION 'reserve_unlink:floor_violated';
    END;

    RETURN jsonb_build_object('status', 'permit_granted',
                              'identity_id', p_identity_id);
END;
$$;

REVOKE ALL ON FUNCTION public.user_identity_inventory(uuid) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.user_identity_inventory(uuid) TO service_role;
REVOKE ALL ON FUNCTION public.reserve_unlink(uuid, uuid) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reserve_unlink(uuid, uuid) TO service_role;

-- ============================================================================
-- 4a) teams.email demotion — drop the globally-unique team anchor
-- ============================================================================
DROP INDEX IF EXISTS public.uq_teams_email;

-- ============================================================================
-- 4b) signup idempotency re-anchor — UNIQUE partial index on the reg-
-- identity, with a pre-scan/abort (20260813000004:44-105 pattern). register
-- hashes sha256(email.lower()) but team_by_email compares case-sensitively,
-- so "A@x.com"/"a@x.com" can already coexist as two unclaimed teams with the
-- SAME identity — the index would fail to create with a cryptic error.
-- Scan ALL identities (not just reg-) among unclaimed active owners.
-- ============================================================================
DO $$
DECLARE
    v_dups int;
    v_sample text;
BEGIN
    SELECT count(*) INTO v_dups FROM (
        SELECT identity
          FROM public.team_memberships
         WHERE user_id IS NULL AND role = 'owner' AND status = 'active'
           AND identity IS NOT NULL
         GROUP BY identity
        HAVING count(*) > 1
    ) d;
    IF v_dups > 0 THEN
        SELECT string_agg(identity, ', ' ORDER BY identity) INTO v_sample FROM (
            SELECT identity
              FROM public.team_memberships
             WHERE user_id IS NULL AND role = 'owner' AND status = 'active'
               AND identity IS NOT NULL
             GROUP BY identity
            HAVING count(*) > 1
            LIMIT 5
        ) s;
        RAISE EXCEPTION
            '20260827000001 abort: % duplicate unclaimed owner identity(ies) '
            '(uq_member_identity_active would fail) — reconcile before '
            'applying. Sample: %', v_dups, v_sample;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_member_identity_active
    ON public.team_memberships (identity)
    WHERE user_id IS NULL AND role = 'owner' AND status = 'active';

-- ============================================================================
-- 4c) claim_membership — replace with the post-demotion contract:
--     · Step-6 teams.email overwrite REMOVED (email is a user property now)
--     · email_in_use exception branch REMOVED (uq_teams_email is gone)
--     · created_by migration: anon-/reg- keys in the claiming team are
--       attributed to the claimer (parenthesized predicate — a bare
--       `A AND B OR C` would rewrite EVERY team's reg- keys globally)
--     · signature (p_lookup_hash, p_user_id, p_email) kept verbatim — the
--       SQL suite asserts it; p_email is retained but unused (contact write
--       decision: register/tenant-provision own the sanctioned contact field)
--     · the providers=['email'] 403 lift is API-layer (hosted_api.py) — the
--       RPC never checked providers; see Task 3
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

    -- Step 1: resolve the team from api_keys (authoritative key→team binding).
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

    -- Step 2: idempotent re-claim.
    IF EXISTS (
        SELECT 1 FROM public.team_memberships
         WHERE team_id = v_team_id AND user_id = p_user_id
           AND role = 'owner' AND status = 'active'
    ) THEN
        v_idempotent := true;
    ELSE

    -- Step 3: find the NULL-user_id owner row (the anon anchor) + its key
    -- material (the merge path copies it for key continuity — same key,
    -- same team, memories intact).
    SELECT id, lookup_hash, key_hash
      INTO v_owner_row_id, v_owner_lookup, v_owner_key_hash
      FROM public.team_memberships
     WHERE team_id = v_team_id AND role = 'owner'
       AND user_id IS NULL AND status = 'active'
     LIMIT 1;

    IF v_owner_row_id IS NULL THEN
        RAISE EXCEPTION 'claim_membership:already_claimed';
    END IF;

    -- Step 4: does a membership row already exist for (p_user_id, v_team_id)?
    SELECT id INTO v_existing_row_id
      FROM public.team_memberships
     WHERE user_id = p_user_id AND team_id = v_team_id
     LIMIT 1;

    IF v_existing_row_id IS NOT NULL THEN
        DELETE FROM public.team_memberships
         WHERE id = v_owner_row_id AND user_id IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'claim_membership:already_claimed';
        END IF;
        UPDATE public.team_memberships
           SET role        = 'owner',
               status      = 'active',
               identity    = NULL,
               lookup_hash = COALESCE(lookup_hash, v_owner_lookup),
               key_hash    = COALESCE(key_hash, v_owner_key_hash),
               updated_at  = now()
         WHERE id = v_existing_row_id;
    ELSE
        -- Step 5: plain claim (race guard: user_id IS NULL conjunct).
        UPDATE public.team_memberships
           SET user_id   = p_user_id,
               identity  = NULL,
               updated_at = now()
         WHERE id = v_owner_row_id AND user_id IS NULL;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'claim_membership:already_claimed';
        END IF;
    END IF;
    END IF;  -- end of idempotent re-claim IF/ELSE

    -- Step 6 (NEW): created_by attribution migration — anon-/reg- keys in
    -- the claiming team belong to the claimer now. Parenthesized: the reg-
    -- branch MUST be team-scoped (a bare `A AND B OR C` rewrites every
    -- team's reg- keys globally — plan-review P1).
    UPDATE public.api_keys
       SET created_by = p_user_id::text
     WHERE team_id = v_team_id
       AND (created_by LIKE 'anon-%' OR created_by LIKE 'reg-%');

    -- (Step 6 legacy teams.email overwrite REMOVED — email is a user property)

    -- Step 7: drop any leftover placeholder row for this user.
    SELECT id INTO v_placeholder_id
      FROM public.team_memberships
     WHERE user_id = p_user_id AND team_id = ''
     LIMIT 1;
    IF v_placeholder_id IS NOT NULL THEN
        DELETE FROM public.team_memberships WHERE id = v_placeholder_id;
    END IF;

    RETURN jsonb_build_object('status', 'claimed', 'team_id', v_team_id,
                              'idempotent', v_idempotent);
END;
$$;

REVOKE ALL ON FUNCTION public.claim_membership(text, uuid, text) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.claim_membership(text, uuid, text) TO service_role;
