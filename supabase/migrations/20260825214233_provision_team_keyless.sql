-- Migration 20260825214233: provision_team keyless mode — no api_keys row
-- Issue: #1716 (onboarding sub-team orphan key)
--
-- POST /v1/onboarding/team (create_onboarding_team) minted a tt_ key per
-- call whose plaintext is never returned — an unrecoverable dead credential
-- (hash-only at rest) that counts against max_api_keys and can never be
-- claimed (#1082 claim needs the pasted key). Decision (option b): make key
-- material OPTIONAL in the provisioning path so the onboarding sub-team is
-- created WITHOUT an api_keys row — keyless until a session-key mint
-- (POST /v1/session/key, which writes the row itself).
--
-- Changes to public.provision_team (CREATE OR REPLACE, SAME 15-arg
-- signature — NOT a new overload):
-- 1) The required-params guard drops p_api_key/p_key_hash/p_lookup_hash —
--    an all-NULL key set provisions a team with NO api_keys row.
-- 2) NEW all-or-none guard: the three key params must be all provided or
--    all NULL — a PARTIAL set (e.g. plaintext without hashes) is rejected
--    rather than persisted half-broken (the membership + api_keys rows
--    would otherwise diverge on which auth anchor is authoritative).
-- 3) The api_keys INSERT is conditional on a lookup_hash being present —
--    keyless provisions skip it entirely. api_keys.lookup_hash is NOT NULL
--    (0007) + unique-indexed, so there is no keyless row shape to store;
--    the team simply has zero api_keys rows until a session-key mint.
--
-- register_user / create_team / agent_signup are UNCHANGED — they keep
-- passing real key material (their mint + token flows are untouched).

-- ============================================================================
-- keyless membership shape: team_memberships.key_hash NOT NULL → nullable
-- ============================================================================
-- 0001 made key_hash NOT NULL (single-team user_teams era); the M:N + identity
-- rewrite (0003/0009) kept it. Only the handle_new_user placeholder uses a
-- literal ('pending' sentinel — unchanged); nothing predicates on
-- key_hash IS NOT NULL for auth (resolution is api_keys.lookup_hash, reveal is
-- team_memberships.api_key/lookup_hash). A keyless provision writes NULL here,
-- so relax the constraint — placeholder semantics are untouched.
ALTER TABLE public.team_memberships ALTER COLUMN key_hash DROP NOT NULL;

CREATE OR REPLACE FUNCTION public.provision_team(
    p_user_id     uuid,        -- Supabase user (user path); NULL on the identity path
    p_identity    text,        -- agent anchor (anon-xxx); NULL on the user path
    p_team_id     text,        -- 26-hex team id (minted by the Edge Function)
    p_team_name   text,
    p_api_key     text,        -- plaintext — shown once on the welcome page, then nulled
    p_key_hash    text,        -- salted PBKDF2 (tortoise/auth.py hash_api_key) — continuity
    p_lookup_hash text,        -- SHA-256(pepper + key) — caller-computed (plan P1-1)
    p_graph_name  text,        -- team_{team_id} (matches Edge Function + data plane)
    p_email       text DEFAULT NULL,
    p_key_prefix  text DEFAULT NULL,
    -- Free-tier limits (mirror tortoise/pricing.py tier_limits('free') —
    -- signups always provision at tier 'free'; upgrades are a separate flow).
    p_tier           text    DEFAULT 'free',
    p_max_users      integer DEFAULT 1,
    p_max_graphs     integer DEFAULT 1,
    p_ops_allowance  integer DEFAULT 10000,
    p_graph_size_cap bigint  DEFAULT 10000
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_created_by text;
BEGIN
    IF p_team_id IS NULL OR p_team_id = '' OR p_team_name IS NULL OR p_team_name = ''
       OR p_graph_name IS NULL OR p_graph_name = '' THEN
        RAISE EXCEPTION 'provision_team: required parameters missing';
    END IF;
    -- #1716: all-or-none key guard — the three key params are either ALL
    -- provided (a minted key) or ALL NULL (keyless provision) — never a
    -- partial set (a persisted half-broken auth anchor). p_key_prefix is
    -- display-only metadata for the api_keys row and is NOT part of the set.
    IF (p_api_key IS NULL) <> (p_key_hash IS NULL)
       OR (p_key_hash IS NULL) <> (p_lookup_hash IS NULL) THEN
        RAISE EXCEPTION 'provision_team: p_api_key/p_key_hash/p_lookup_hash must be all provided or all NULL (keyless)';
    END IF;
    -- Exactly one anchor: a real user OR an agent identity (0009 CHECK admits
    -- the identity path; all-NULL would violate chk_member_or_invite).
    IF (p_user_id IS NULL) = (p_identity IS NULL) THEN
        RAISE EXCEPTION 'provision_team: exactly one of p_user_id / p_identity is required';
    END IF;
    v_created_by := COALESCE(p_user_id::text, p_identity);

    -- ── teams row (exactly one; idempotent re-invocation) ────────────────
    INSERT INTO public.teams (id, name, tier, graph_name, email,
                              max_users, max_graphs, ops_allowance, graph_size_cap)
    VALUES (p_team_id, p_team_name, p_tier, p_graph_name, p_email,
            p_max_users, p_max_graphs, p_ops_allowance, p_graph_size_cap)
    ON CONFLICT (id) DO UPDATE
        SET name  = EXCLUDED.name,
            email = COALESCE(EXCLUDED.email, teams.email);

    -- ── membership: exactly one row per (user, team). Order matters ────
    -- 1) Any existing real membership for this team is REFRESHED in place
    --    (idempotent retry with the same key; resurrection of a previously
    --    removed row; stale-placeholder race with a pre-existing real row).
    UPDATE public.team_memberships
       SET team_name   = p_team_name,
           api_key     = p_api_key,
           key_hash    = p_key_hash,
           lookup_hash = p_lookup_hash,
           graph_name  = p_graph_name,
           role        = 'owner',
           status      = 'active',
           updated_at  = now()
     WHERE user_id = p_user_id AND team_id = p_team_id;

    IF NOT FOUND THEN
        -- 2) Reconcile the trigger placeholder (M:N placeholder semantics —
        --    flip team_id='' to the real team_id in place; plan §4.1 step 6).
        --    NOT EXISTS guard: never flip when a real row appeared concurrently.
        UPDATE public.team_memberships
           SET team_id     = p_team_id,
               team_name   = p_team_name,
               api_key     = p_api_key,
               key_hash    = p_key_hash,
               lookup_hash = p_lookup_hash,
               graph_name  = p_graph_name,
               role        = 'owner',
               status      = 'active',
               updated_at  = now()
         WHERE user_id = p_user_id AND team_id = ''
           AND NOT EXISTS (
               SELECT 1 FROM public.team_memberships
               WHERE user_id = p_user_id AND team_id = p_team_id
           );

        IF NOT FOUND THEN
            -- 3) No placeholder (identity path, or trigger never fired):
            --    insert the real row; ON CONFLICT keeps re-invocation a no-op.
            IF p_user_id IS NOT NULL THEN
                INSERT INTO public.team_memberships
                    (user_id, team_id, team_name, api_key, key_hash, lookup_hash,
                     graph_name, role, status)
                VALUES (p_user_id, p_team_id, p_team_name, p_api_key, p_key_hash,
                        p_lookup_hash, p_graph_name, 'owner', 'active')
                ON CONFLICT (user_id, team_id) DO NOTHING;
            ELSE
                -- Identity path: NULL user_id + identity (uq_member_identity_team
                -- partial unique index keeps re-invocation idempotent). Unlike
                -- the user path's step-1 refresh, steps 1-2 can never match a
                -- NULL user_id, so the conflict branch REFRESHES the existing
                -- row in place — symmetric with the user path: re-provisioning
                -- an identity with a rotated key updates the membership's
                -- key_hash/lookup_hash instead of silently going stale
                -- (migration-review WARNING, PR #847).
                INSERT INTO public.team_memberships
                    (user_id, team_id, team_name, api_key, key_hash, lookup_hash,
                     graph_name, role, status, identity)
                VALUES (NULL, p_team_id, p_team_name, p_api_key, p_key_hash,
                        p_lookup_hash, p_graph_name, 'owner', 'active', p_identity)
                ON CONFLICT (identity, team_id) WHERE user_id IS NULL
                DO UPDATE SET team_name   = EXCLUDED.team_name,
                              api_key     = EXCLUDED.api_key,
                              key_hash    = EXCLUDED.key_hash,
                              lookup_hash = EXCLUDED.lookup_hash,
                              graph_name  = EXCLUDED.graph_name,
                              role        = 'owner',
                              status      = 'active',
                              updated_at  = now();
            END IF;
        END IF;
    END IF;

    -- 4) Any leftover placeholder for this user is stale now — drop it so
    --    the invariant "exactly one membership row per provisioned user"
    --    holds even under the placeholder/real-row race. (Identity path:
    --    p_user_id IS NULL → matches nothing.)
    DELETE FROM public.team_memberships
     WHERE user_id = p_user_id AND team_id = '';

    -- ── api_keys row (exactly one per (team, key); E2E-1 contract) ───────
    -- #1716: keyless provision (all-NULL key params) writes NO api_keys row
    -- — the team stays keyless until a session-key mint. The row shape
    -- requires a lookup_hash (NOT NULL + unique index in 0007), so there is
    -- nothing to upsert when keyless; skipping the insert also means the
    -- 0015 key_create abuse trigger never fires for a keyless team.
    IF p_lookup_hash IS NOT NULL THEN
        INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix,
                                     created_via, created_by)
        VALUES ('key_' || p_team_id || '_' || left(p_lookup_hash, 12),
                p_team_id, p_lookup_hash,
                COALESCE(p_key_prefix, left(p_team_id, 8)),
                'provisioned', v_created_by)
        ON CONFLICT (lookup_hash) DO NOTHING;
    END IF;
END;
$$;

-- provision_team mints teams + API keys — ONLY the Edge Function may call it
-- (service role; caller auth is #802 in the Edge Function). End users must
-- not execute it directly. CREATE OR REPLACE preserves the existing
-- REVOKE/GRANT from migration 0010 — the grant surface is unchanged.
