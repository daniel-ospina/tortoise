-- Migration 0010: provisioning rewrite — atomic provision_team RPC
-- Epic: #669 plan v2 Task 2 (docs/plans/2026-08-08-669-plan.md) · Issue: #770
--
-- The signup path now writes the master list into Supabase ONLY, in ONE
-- transaction, via the SECURITY DEFINER provision_team(...) RPC:
-- teams + team_memberships + api_keys atomically (E2E-1: the Edge Function
-- no longer calls /internal/provision → zero registry-graph writes).
--
-- 1) provision_team(...) — idempotent, atomic upsert:
--    - teams: INSERT ... ON CONFLICT (id) DO UPDATE → exactly one row.
--    - team_memberships: (a) any existing row for (user, team) is refreshed
--      in place (retry / resurrection / stale-placeholder race); (b) else the
--      handle_new_user placeholder is reconciled in place (sentinel:
--      team_id='' / key_hash='pending' — unchanged, so the reconcilable
--      predicate is stable) → exactly one membership row per user (M:N
--      placeholder semantics, plan §4.1 step 6); (c) else INSERT with ON
--      CONFLICT guards. A leftover placeholder is dropped at the end. The
--      user-path race (RPC before trigger) is structurally impossible:
--      team_memberships.user_id FK → auth.users(id) means the auth.users
--      INSERT always precedes any membership write; the amended trigger's
--      NOT EXISTS guard is belt-and-braces for that ordering.
--    - api_keys: INSERT ... ON CONFLICT (lookup_hash) DO NOTHING (0007
--      unique index); id = 'key_' || team_id || '_' || hash-prefix is
--      deterministic per (team, key) — same-key retries collide harmlessly,
--      a genuinely new key for the same team adds a second row (multi-key
--      teams are valid; free tier caps at 2).
--    - key_hash (salted PBKDF2 — continuity) and lookup_hash (SHA-256(pepper
--      + key)) are computed by the CALLER (tortoise/auth.py / the TS mirror),
--      NEVER in SQL — the pepper lives in app code, not the DB (plan P1-1).
-- 2) handle_new_user trigger — same placeholder shape (team_id='' /
--    key_hash='pending'), plus a NOT EXISTS guard so a placeholder never
--    appears next to an existing real membership.
-- 3) reveal_api_key — fail-closed guards added: identity-path rows (NULL
--    user_id) are not welcome-page revealable (agent keys are delivered once
--    at mint time; no Supabase session can prove an anon identity), and a
--    row WITHOUT a lookup_hash is never nulled (that would orphan the only
--    credential — auth resolves via lookup_hash after the reveal, E2E-6).
-- 4) update_user_team is REMOVED — subsumed by provision_team; the Edge
--    Function's only Supabase write is now provision_team.

-- ============================================================================
-- 1) provision_team — atomic teams + membership + api_keys (SECURITY DEFINER)
-- ============================================================================
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
       OR p_api_key IS NULL OR p_key_hash IS NULL OR p_lookup_hash IS NULL
       OR p_graph_name IS NULL OR p_graph_name = '' THEN
        RAISE EXCEPTION 'provision_team: required parameters missing';
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
    INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix,
                                 created_via, created_by)
    VALUES ('key_' || p_team_id || '_' || left(p_lookup_hash, 12),
            p_team_id, p_lookup_hash,
            COALESCE(p_key_prefix, left(p_team_id, 8)),
            'provisioned', v_created_by)
    ON CONFLICT (lookup_hash) DO NOTHING;
END;
$$;

-- provision_team mints teams + API keys — ONLY the Edge Function may call it
-- (service role; caller auth is #802 in the Edge Function). End users must
-- not execute it directly.
REVOKE ALL ON FUNCTION public.provision_team FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.provision_team TO service_role;

-- ============================================================================
-- 2) handle_new_user — same placeholder shape + NOT EXISTS guard
-- ============================================================================
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- Placeholder row: team_id='' is the placeholder sentinel (key_hash=
    -- 'pending' is the reconcilable predicate — UNCHANGED from 0003 so
    -- provision_team's reconciliation predicate stays valid). Provisioning
    -- updates THIS row (WHERE user_id = X AND team_id = '') and flips
    -- team_id to the real value in the same upsert — no second row, no
    -- phantom membership (M:N placeholder semantics, plan §4.1 step 6).
    -- Guard (0010): never insert a placeholder next to an existing real
    -- membership — belt-and-braces for out-of-order trigger firing.
    IF NOT EXISTS (
        SELECT 1 FROM public.team_memberships
        WHERE user_id = NEW.id AND team_id <> '' AND status = 'active'
    ) THEN
        INSERT INTO public.team_memberships (
            user_id, team_id, team_name, key_hash, graph_name, role
        ) VALUES (
            NEW.id, '', 'provisioning...', 'pending', '', 'owner'
        )
        ON CONFLICT (user_id, team_id) DO NOTHING;
    END IF;

    RETURN NEW;
END;
$$;

-- The on_auth_user_created trigger still references the function by name —
-- CREATE OR REPLACE keeps the trigger wired to the amended body.

-- ============================================================================
-- 3) reveal_api_key — one-time reveal + null; lookup_hash retained (E2E-6)
-- ============================================================================
CREATE OR REPLACE FUNCTION public.reveal_api_key(p_user_id uuid, p_team_id text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE k text;
BEGIN
    -- Identity-path rows (NULL user_id) are NOT welcome-page revealable:
    -- agent keys are delivered once at mint time, and no Supabase session
    -- can prove ownership of an anon identity (auth.uid() is never NULL).
    IF p_user_id IS NULL THEN
        RETURN NULL;
    END IF;
    IF auth.uid() IS NULL OR auth.uid() <> p_user_id THEN
        RETURN NULL;
    END IF;
    SELECT api_key INTO k FROM public.team_memberships
     WHERE user_id = p_user_id AND team_id = p_team_id
       AND status = 'active' AND role = 'owner';
    IF k IS NULL OR k = 'pending' THEN
        RETURN NULL;
    END IF;
    -- Fail CLOSED when lookup_hash is missing: nulling the plaintext without
    -- a lookup anchor would permanently orphan the credential (auth resolves
    -- via lookup_hash AFTER the reveal — E2E-6). Refuse instead of lock out.
    IF NOT EXISTS (
        SELECT 1 FROM public.team_memberships
        WHERE user_id = p_user_id AND team_id = p_team_id
          AND lookup_hash IS NOT NULL AND lookup_hash <> ''
    ) THEN
        RETURN NULL;
    END IF;
    UPDATE public.team_memberships SET api_key = NULL, updated_at = now()
     WHERE user_id = p_user_id AND team_id = p_team_id;
    RETURN k;  -- shown once; nulled atomically; lookup_hash retained
END;
$$;

GRANT EXECUTE ON FUNCTION public.reveal_api_key TO authenticated;

-- ============================================================================
-- 4) update_user_team — REMOVED (subsumed by provision_team; the Edge
--    Function's only Supabase write is now provision_team). If a rollback to
--    the pre-0010 flow were ever needed, restore from migration 0003.
-- ============================================================================
DROP FUNCTION IF EXISTS public.update_user_team(uuid, text, text, text, text, text);

-- ============================================================================
-- 5) Identity-path idempotency anchor (agent rows have NULL user_id, which
--    never collides under the 0003 uq_member_team unique — NULLs are
--    distinct in unique indexes — so anon re-provision needs its own guard).
-- ============================================================================
CREATE UNIQUE INDEX IF NOT EXISTS uq_member_identity_team
    ON public.team_memberships (identity, team_id)
    WHERE user_id IS NULL;
