-- ============================================================================
-- Migration 20260915000001: tenancy vocabulary — team → org (#3543, slice S1)
-- ----------------------------------------------------------------------------
-- The tenant is an ORGANIZATION (model: user account → organization → graph),
-- but the control plane calls it "team". #3543 renames the surface. This is
-- the schema slice (S1).
--
-- WHY A NEW MIGRATION AND NOT AN EDIT OF 0002/0003/0006/0007/0008/0010/...:
--   `.github/scripts/check-migration-append-only` (CI job
--   `migration-append-only`, diff mode) rejects ANY edit/rename/deletion of a
--   migration present in the PR base tree — status M/D/R* is a hard fail, and
--   the only exemption (#1235) is a PURE-PREFIX rename of a provably-unapplied
--   migration (content byte-identical). A content edit is never exempt, and
--   without SUPABASE_ACCESS_TOKEN the check is fail-closed. So the rename is
--   expressed as ONE new timestamp file applied last. Earlier migrations keep
--   their historical `team` spelling and are untouched: they run first, this
--   migration renames the end state. `.github/scripts/check-migration-drift`
--   is unaffected (no new version prefix, no duplicate prefix, and this file
--   is repo-ahead exactly like every migration before the next deploy).
--
-- NO DATA MIGRATION: every statement below is a catalog rename (ALTER ...
-- RENAME). No row is inserted, updated, deleted or backfilled; no table is
-- dropped. RENAME COLUMN / RENAME TO preserve row data and column order by
-- construction. The two rename classes deliberately NOT touched are stored
-- VALUES (not identifiers), which would be data migrations:
--   · graphs.namespace values ('team_<org>_<gid>') — slice S3.
--   · api_keys.scopes values ('team:manage') and the chk_minted_key_no_
--     escalation allowlist + column COMMENT that documents them — the value is
--     minted by Python and guarded by that CHECK; renaming the value would
--     both require an UPDATE of live rows and silently hollow out the
--     escalation guard. Deferred (S2/S5 vocabulary).
--
-- RPC NAMES ARE KEPT (`provision_team`, `provision_team_with_token`,
-- `recover_team_key`, `revoke_signup_token`) — #3543 slice S1 renames RPC
-- *parameters*, not RPC names; the names are a wire contract with
-- tortoise/supabase_control.py and supabase/functions/tenant-provision/
-- index.ts, which this slice must not edit. Parameter names change
-- (`p_team_id` → `p_org_id`) — see the caller-coupling note at the end.
--
-- Owner: #3543. Scope: supabase/migrations only.
-- ============================================================================

-- ============================================================================
-- 1) Tables: teams → organizations, team_memberships → org_memberships
--    FK references are by OID and follow automatically; constraint/index
--    NAMES do not, and are handled explicitly in §3–§4.
-- ============================================================================
ALTER TABLE public.teams RENAME TO organizations;
ALTER TABLE public.team_memberships RENAME TO org_memberships;

-- ============================================================================
-- 2) Columns: team_id → org_id (every control-plane table), team_name →
--    org_name, max_teams → max_orgs
--    Column-level GRANT lists (0006/0007/0008/0009/20260813000006/
--    20260901000001/20260906000001) are keyed on attnum and follow the rename
--    — no re-GRANT is required or performed.
-- ============================================================================
ALTER TABLE public.audit_events          RENAME COLUMN team_id TO org_id;
ALTER TABLE public.analytics_events      RENAME COLUMN team_id TO org_id;
ALTER TABLE public.abuse_events          RENAME COLUMN team_id TO org_id;
ALTER TABLE public.agent_signup_tokens   RENAME COLUMN team_id TO org_id;
ALTER TABLE public.api_keys              RENAME COLUMN team_id TO org_id;
ALTER TABLE public.graphs                RENAME COLUMN team_id TO org_id;
ALTER TABLE public.invitations           RENAME COLUMN team_id TO org_id;
ALTER TABLE public.metering_records      RENAME COLUMN team_id TO org_id;
ALTER TABLE public.oauth_access_tokens   RENAME COLUMN team_id TO org_id;
ALTER TABLE public.oauth_codes           RENAME COLUMN team_id TO org_id;
ALTER TABLE public.oauth_refresh_tokens  RENAME COLUMN team_id TO org_id;
ALTER TABLE public.org_memberships       RENAME COLUMN team_id TO org_id;
ALTER TABLE public.org_memberships       RENAME COLUMN team_name TO org_name;
ALTER TABLE public.organizations         RENAME COLUMN max_teams TO max_orgs;

-- ============================================================================
-- 3) Indexes. `ALTER INDEX IF EXISTS` is used throughout: several of these
--    indexes are guarded by IF EXISTS at CREATE time (partial unique indexes
--    whose predicate can be absent on a given database), so a bare rename
--    would abort the migration on a database where the index was never made.
-- ============================================================================
ALTER INDEX IF EXISTS public.idx_abuse_events_team_rule_time  RENAME TO idx_abuse_events_org_rule_time;
ALTER INDEX IF EXISTS public.idx_abuse_events_team_type_time  RENAME TO idx_abuse_events_org_type_time;
ALTER INDEX IF EXISTS public.idx_analytics_team_time          RENAME TO idx_analytics_org_time;
ALTER INDEX IF EXISTS public.idx_api_keys_team_id             RENAME TO idx_api_keys_org_id;
ALTER INDEX IF EXISTS public.idx_audit_team_time              RENAME TO idx_audit_org_time;
ALTER INDEX IF EXISTS public.idx_graphs_team_id               RENAME TO idx_graphs_org_id;
ALTER INDEX IF EXISTS public.idx_oauth_access_tokens_team     RENAME TO idx_oauth_access_tokens_org;
ALTER INDEX IF EXISTS public.idx_oauth_refresh_tokens_user_team RENAME TO idx_oauth_refresh_tokens_user_org;
ALTER INDEX IF EXISTS public.idx_team_memberships_lookup_hash RENAME TO idx_org_memberships_lookup_hash;
-- 0001-era name, carried through the 0003 table rename: it has been an index on
-- the membership table since 0003 even though it still says `user_teams`.
ALTER INDEX IF EXISTS public.idx_user_teams_key_hash          RENAME TO idx_org_memberships_key_hash;

ALTER INDEX IF EXISTS public.uq_agent_signup_tokens_team       RENAME TO uq_agent_signup_tokens_org;
ALTER INDEX IF EXISTS public.uq_graphs_team_name_active        RENAME TO uq_graphs_org_name_active;
ALTER INDEX IF EXISTS public.uq_invitations_team_email_pending RENAME TO uq_invitations_org_email_pending;
ALTER INDEX IF EXISTS public.uq_member_identity_team           RENAME TO uq_member_identity_org;
ALTER INDEX IF EXISTS public.uq_team_invite_email              RENAME TO uq_org_invite_email;
ALTER INDEX IF EXISTS public.uq_teams_name                     RENAME TO uq_organizations_name;

-- ============================================================================
-- 4) Constraint names. These are PostgreSQL-AUTO-GENERATED (`<table>_<col>_
--    fkey`, and `<table>_<col>_not_null` on servers that catalogue NOT NULL
--    constraints as first-class objects) or LEGACY (names minted back when the
--    membership table was `user_teams`, carried through the 0003 rename).
--    Two consequences drive the shape of this block:
--      · Auto-generated names are NOT rewritten by RENAME COLUMN/RENAME TO
--        (verified: the catalog keeps `api_keys_team_id_fkey` after the
--        column rename), so they must be renamed explicitly.
--      · `ALTER TABLE ... RENAME CONSTRAINT` has no IF EXISTS, and whether a
--        `*_not_null` row exists at all depends on the server version — a
--        hardcoded RENAME would abort the migration on servers that do not
--        catalogue them. The guarded loop turns "absent" into "skip", which is
--        correct on every version and keeps the file idempotent under re-apply.
-- ============================================================================
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT * FROM (VALUES
            -- auto-generated FKs (the column rename left the old spelling)
            ('abuse_events',         'abuse_events_team_id_fkey',          'abuse_events_org_id_fkey'),
            ('agent_signup_tokens',  'agent_signup_tokens_team_id_fkey',   'agent_signup_tokens_org_id_fkey'),
            ('api_keys',             'api_keys_team_id_fkey',              'api_keys_org_id_fkey'),
            ('graphs',               'graphs_team_id_fkey',                'graphs_org_id_fkey'),
            ('invitations',          'invitations_team_id_fkey',           'invitations_org_id_fkey'),
            ('metering_records',     'metering_records_team_id_fkey',      'metering_records_org_id_fkey'),
            ('oauth_access_tokens',  'oauth_access_tokens_team_id_fkey',   'oauth_access_tokens_org_id_fkey'),
            ('oauth_codes',          'oauth_codes_team_id_fkey',           'oauth_codes_org_id_fkey'),
            ('oauth_refresh_tokens', 'oauth_refresh_tokens_team_id_fkey',  'oauth_refresh_tokens_org_id_fkey'),
            -- auto-generated NOT NULL constraints (server-version dependent)
            ('abuse_events',         'abuse_events_team_id_not_null',        'abuse_events_org_id_not_null'),
            ('agent_signup_tokens',  'agent_signup_tokens_team_id_not_null', 'agent_signup_tokens_org_id_not_null'),
            ('analytics_events',     'analytics_events_team_id_not_null',    'analytics_events_org_id_not_null'),
            ('api_keys',             'api_keys_team_id_not_null',            'api_keys_org_id_not_null'),
            ('audit_events',         'audit_events_team_id_not_null',        'audit_events_org_id_not_null'),
            ('graphs',               'graphs_team_id_not_null',              'graphs_org_id_not_null'),
            ('invitations',          'invitations_team_id_not_null',         'invitations_org_id_not_null'),
            ('metering_records',     'metering_records_team_id_not_null',    'metering_records_org_id_not_null'),
            ('oauth_access_tokens',  'oauth_access_tokens_team_id_not_null', 'oauth_access_tokens_org_id_not_null'),
            ('oauth_codes',          'oauth_codes_team_id_not_null',         'oauth_codes_org_id_not_null'),
            ('oauth_refresh_tokens', 'oauth_refresh_tokens_team_id_not_null','oauth_refresh_tokens_org_id_not_null'),
            -- legacy `user_teams`-era names still attached to the membership table
            ('org_memberships', 'user_teams_pkey',              'org_memberships_pkey'),
            ('org_memberships', 'user_teams_user_id_fkey',      'org_memberships_user_id_fkey'),
            ('org_memberships', 'user_teams_id_not_null',       'org_memberships_id_not_null'),
            ('org_memberships', 'user_teams_team_id_not_null',  'org_memberships_org_id_not_null'),
            ('org_memberships', 'user_teams_team_name_not_null','org_memberships_org_name_not_null'),
            ('org_memberships', 'user_teams_graph_name_not_null','org_memberships_graph_name_not_null'),
            ('org_memberships', 'user_teams_created_at_not_null','org_memberships_created_at_not_null'),
            ('org_memberships', 'user_teams_updated_at_not_null','org_memberships_updated_at_not_null'),
            ('org_memberships', 'team_memberships_role_not_null',  'org_memberships_role_not_null'),
            ('org_memberships', 'team_memberships_status_not_null','org_memberships_status_not_null'),
            ('org_memberships', 'uq_member_team',               'uq_member_org'),
            -- legacy `teams`-era names on the renamed table
            ('organizations', 'teams_pkey',                         'organizations_pkey'),
            ('organizations', 'teams_id_not_null',                  'organizations_id_not_null'),
            ('organizations', 'teams_name_not_null',                'organizations_name_not_null'),
            ('organizations', 'teams_tier_not_null',                'organizations_tier_not_null'),
            ('organizations', 'teams_graph_name_not_null',          'organizations_graph_name_not_null'),
            ('organizations', 'teams_created_at_not_null',          'organizations_created_at_not_null'),
            ('organizations', 'teams_backup_enabled_not_null',      'organizations_backup_enabled_not_null'),
            ('organizations', 'teams_onboarding_state_not_null',    'organizations_onboarding_state_not_null'),
            ('organizations', 'teams_dashboard_key_login_not_null', 'organizations_dashboard_key_login_not_null')
        ) AS v(tbl, old_name, new_name)
    LOOP
        IF EXISTS (
            SELECT 1 FROM pg_constraint c
             WHERE c.conrelid = r.tbl::regclass
               AND c.conname  = r.old_name
        ) THEN
            EXECUTE format('ALTER TABLE %I RENAME CONSTRAINT %I TO %I',
                           r.tbl, r.old_name, r.new_name);
        END IF;
    END LOOP;
END $$;

-- ============================================================================
-- 5) RLS policies: names and the tenant GUC.
--    A column rename does NOT need a policy rebuild (policies store attribute
--    numbers), but the tenant GUC literal and the policy NAMES do change, so
--    each affected policy is dropped and recreated with the org vocabulary.
--    The GUC is `app.current_org_id` from here on.
-- ============================================================================
DROP POLICY IF EXISTS audit_team_isolation ON public.audit_events;
CREATE POLICY audit_org_isolation ON public.audit_events
    FOR SELECT
    USING (org_id = current_setting('app.current_org_id', true));

DROP POLICY IF EXISTS api_keys_guc_read ON public.api_keys;
CREATE POLICY api_keys_guc_read ON public.api_keys
    FOR SELECT
    TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));

DROP POLICY IF EXISTS invitations_guc_read ON public.invitations;
CREATE POLICY invitations_guc_read ON public.invitations
    FOR SELECT
    TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));

DROP POLICY IF EXISTS graph_guc_read ON public.graphs;
CREATE POLICY graph_guc_read ON public.graphs
    FOR SELECT
    TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));

DROP POLICY IF EXISTS team_guc_read ON public.organizations;
CREATE POLICY org_guc_read ON public.organizations
    FOR SELECT
    TO authenticated
    USING (id = current_setting('app.current_org_id', true));

DROP POLICY IF EXISTS team_service_role_all ON public.organizations;
CREATE POLICY org_service_role_all ON public.organizations
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- The two 0001-era membership policies survived 0003's table rename with their
-- original `teams` wording. Bodies are unchanged (user_id-keyed, not org-keyed)
-- — only the names move to the org vocabulary. The three 0003 policies carry no
-- `team` identifier and are left alone.
DROP POLICY IF EXISTS "Users can view own teams" ON public.org_memberships;
CREATE POLICY "Users can view own org memberships" ON public.org_memberships
    FOR SELECT
    TO authenticated
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Service role can manage all teams" ON public.org_memberships;
CREATE POLICY "Service role can manage all org memberships" ON public.org_memberships
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ============================================================================
-- 6) Functions. Every body that named a renamed table/column is replaced (a
--    plpgsql body is stored as text and is NOT rewritten by a column rename —
--    leaving one un-replaced would be a runtime failure on the next call).
--
--    TWO shapes here, and the difference is not cosmetic:
--      · Bodies needing NO parameter rename use CREATE OR REPLACE. Same pg_proc
--        OID → ownership AND the existing REVOKE/GRANT surface carry over
--        untouched.
--      · Bodies that RENAME an input parameter must be DROPped first:
--        PostgreSQL rejects CREATE OR REPLACE when an input parameter changes
--        name ("cannot change name of input parameter"). DROP + CREATE mints a
--        NEW OID, which DISCARDS the ACL — so each of those functions re-issues
--        the exact REVOKE/GRANT pair its original migration established
--        (0010 §1, 0015, 20260814000001 §5, 20260826000001 §2,
--        20260829000001). A missed re-issue would silently leave the RPC at the
--        schema default (EXECUTE to PUBLIC/anon/authenticated) — for
--        provision_team / recover_team_key / revoke_signup_token that is a
--        privilege escalation, not a cosmetic regression.
--
--    Argument TYPES are unchanged everywhere (only names move), so each DROP
--    targets the one existing signature. No trigger depends on a dropped
--    function — the two trigger bodies (handle_new_user, trg_api_keys_abuse_fn)
--    take no parameters and are replaced in place.
--
--    RAISE message strings are kept VERBATIM — they carry the (unchanged)
--    function name and are matched as substrings by the Python callers.
-- ============================================================================

-- 6.1) handle_new_user — the auth.users INSERT trigger body.
CREATE OR REPLACE FUNCTION public.handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- Placeholder row: org_id='' is the placeholder sentinel (key_hash=
    -- 'pending' is the reconcilable predicate). Provisioning updates THIS row
    -- (WHERE user_id = X AND org_id = '') and flips org_id to the real value in
    -- the same upsert — no second row, no phantom membership.
    IF NOT EXISTS (
        SELECT 1 FROM public.org_memberships
        WHERE user_id = NEW.id AND org_id <> '' AND status = 'active'
    ) THEN
        INSERT INTO public.org_memberships (
            user_id, org_id, org_name, key_hash, graph_name, role
        ) VALUES (
            NEW.id, '', 'provisioning...', 'pending', '', 'owner'
        )
        ON CONFLICT (user_id, org_id) DO NOTHING;
    END IF;

    RETURN NEW;
END;
$$;

-- 6.2) reveal_api_key — one-time reveal + null; lookup_hash retained.
DROP FUNCTION IF EXISTS public.reveal_api_key(uuid, text);
CREATE OR REPLACE FUNCTION public.reveal_api_key(p_user_id uuid, p_org_id text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE k text;
BEGIN
    IF p_user_id IS NULL THEN
        RETURN NULL;
    END IF;
    IF auth.uid() IS NULL OR auth.uid() <> p_user_id THEN
        RETURN NULL;
    END IF;
    SELECT api_key INTO k FROM public.org_memberships
     WHERE user_id = p_user_id AND org_id = p_org_id
       AND status = 'active' AND role = 'owner';
    IF k IS NULL OR k = 'pending' THEN
        RETURN NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.org_memberships
        WHERE user_id = p_user_id AND org_id = p_org_id
          AND lookup_hash IS NOT NULL AND lookup_hash <> ''
    ) THEN
        RETURN NULL;
    END IF;
    UPDATE public.org_memberships SET api_key = NULL, updated_at = now()
     WHERE user_id = p_user_id AND org_id = p_org_id;
    RETURN k;  -- shown once; nulled atomically; lookup_hash retained
END;
$$;

-- Re-issued ACL (0003 §5; the DROP above reset it to the schema default).
GRANT EXECUTE ON FUNCTION public.reveal_api_key(uuid, text) TO authenticated;

-- 6.3) provision_team — atomic organizations + membership + api_keys.
--      (Newest body: 20260825214233 keyless mode.)
DROP FUNCTION IF EXISTS public.provision_team(uuid, text, text, text, text,
    text, text, text, text, text, text, integer, integer, integer, bigint);
CREATE OR REPLACE FUNCTION public.provision_team(
    p_user_id     uuid,        -- Supabase user (user path); NULL on the identity path
    p_identity    text,        -- agent anchor (anon-xxx); NULL on the user path
    p_org_id      text,        -- 26-hex org id (minted by the Edge Function)
    p_org_name    text,
    p_api_key     text,        -- plaintext — shown once on the welcome page, then nulled
    p_key_hash    text,        -- salted PBKDF2 (tortoise/auth.py hash_api_key) — continuity
    p_lookup_hash text,        -- SHA-256(pepper + key) — caller-computed (plan P1-1)
    p_graph_name  text,        -- org_{org_id} (matches Edge Function + data plane)
    p_email       text DEFAULT NULL,
    p_key_prefix  text DEFAULT NULL,
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
    IF p_org_id IS NULL OR p_org_id = '' OR p_org_name IS NULL OR p_org_name = ''
       OR p_graph_name IS NULL OR p_graph_name = '' THEN
        RAISE EXCEPTION 'provision_team: required parameters missing';
    END IF;
    -- All-or-none key guard: the three key params are either ALL provided
    -- (a minted key) or ALL NULL (keyless provision) — never a partial set.
    IF (p_api_key IS NULL) <> (p_key_hash IS NULL)
       OR (p_key_hash IS NULL) <> (p_lookup_hash IS NULL) THEN
        RAISE EXCEPTION 'provision_team: p_api_key/p_key_hash/p_lookup_hash must be all provided or all NULL (keyless)';
    END IF;
    IF (p_user_id IS NULL) = (p_identity IS NULL) THEN
        RAISE EXCEPTION 'provision_team: exactly one of p_user_id / p_identity is required';
    END IF;
    v_created_by := COALESCE(p_user_id::text, p_identity);

    -- ── organizations row (exactly one; idempotent re-invocation) ────────
    INSERT INTO public.organizations (id, name, tier, graph_name, email,
                                      max_users, max_graphs, ops_allowance, graph_size_cap)
    VALUES (p_org_id, p_org_name, p_tier, p_graph_name, p_email,
            p_max_users, p_max_graphs, p_ops_allowance, p_graph_size_cap)
    ON CONFLICT (id) DO UPDATE
        SET name  = EXCLUDED.name,
            email = COALESCE(EXCLUDED.email, organizations.email);

    -- ── membership: exactly one row per (user, org). Order matters ──────
    UPDATE public.org_memberships
       SET org_name    = p_org_name,
           api_key     = p_api_key,
           key_hash    = p_key_hash,
           lookup_hash = p_lookup_hash,
           graph_name  = p_graph_name,
           role        = 'owner',
           status      = 'active',
           updated_at  = now()
     WHERE user_id = p_user_id AND org_id = p_org_id;

    IF NOT FOUND THEN
        -- Reconcile the trigger placeholder (flip org_id='' in place).
        UPDATE public.org_memberships
           SET org_id      = p_org_id,
               org_name    = p_org_name,
               api_key     = p_api_key,
               key_hash    = p_key_hash,
               lookup_hash = p_lookup_hash,
               graph_name  = p_graph_name,
               role        = 'owner',
               status      = 'active',
               updated_at  = now()
         WHERE user_id = p_user_id AND org_id = ''
           AND NOT EXISTS (
               SELECT 1 FROM public.org_memberships
               WHERE user_id = p_user_id AND org_id = p_org_id
           );

        IF NOT FOUND THEN
            IF p_user_id IS NOT NULL THEN
                INSERT INTO public.org_memberships
                    (user_id, org_id, org_name, api_key, key_hash, lookup_hash,
                     graph_name, role, status)
                VALUES (p_user_id, p_org_id, p_org_name, p_api_key, p_key_hash,
                        p_lookup_hash, p_graph_name, 'owner', 'active')
                ON CONFLICT (user_id, org_id) DO NOTHING;
            ELSE
                -- Identity path: the conflict branch REFRESHES the existing row
                -- in place (uq_member_identity_org keeps re-invocation
                -- idempotent) — re-provisioning an identity with a rotated key
                -- updates the membership instead of going stale.
                INSERT INTO public.org_memberships
                    (user_id, org_id, org_name, api_key, key_hash, lookup_hash,
                     graph_name, role, status, identity)
                VALUES (NULL, p_org_id, p_org_name, p_api_key, p_key_hash,
                        p_lookup_hash, p_graph_name, 'owner', 'active', p_identity)
                ON CONFLICT (identity, org_id) WHERE user_id IS NULL
                DO UPDATE SET org_name    = EXCLUDED.org_name,
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

    -- Any leftover placeholder for this user is stale now.
    DELETE FROM public.org_memberships
     WHERE user_id = p_user_id AND org_id = '';

    -- ── api_keys row (exactly one per (org, key); E2E-1 contract) ────────
    -- Keyless provision (all-NULL key params) writes NO api_keys row — the org
    -- stays keyless until a session-key mint.
    IF p_lookup_hash IS NOT NULL THEN
        INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix,
                                     created_via, created_by)
        VALUES ('key_' || p_org_id || '_' || left(p_lookup_hash, 12),
                p_org_id, p_lookup_hash,
                COALESCE(p_key_prefix, left(p_org_id, 8)),
                'provisioned', v_created_by)
        ON CONFLICT (lookup_hash) DO NOTHING;
    END IF;
END;
$$;

-- Re-issued ACL (0010 §1; provision_team mints orgs + API keys — service_role
-- ONLY, never PUBLIC/anon/authenticated).
REVOKE ALL ON FUNCTION public.provision_team(uuid, text, text, text, text,
    text, text, text, text, text, text, integer, integer, integer, bigint)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.provision_team(uuid, text, text, text, text,
    text, text, text, text, text, text, integer, integer, integer, bigint)
    TO service_role;

-- 6.4) provision_team_with_token — the signup mint wrapper. The PERFORM uses
--      NAMED-ARG notation, so the parameter renames are carried here too.
DROP FUNCTION IF EXISTS public.provision_team_with_token(uuid, text, text,
    text, text, text, text, text, text, text, text, integer, integer, integer,
    bigint, text);
CREATE OR REPLACE FUNCTION public.provision_team_with_token(
    p_user_id     uuid,
    p_identity    text,
    p_org_id      text,
    p_org_name    text,
    p_api_key     text,
    p_key_hash    text,
    p_lookup_hash text,
    p_graph_name  text,
    p_email       text DEFAULT NULL,
    p_key_prefix  text DEFAULT NULL,
    p_tier           text    DEFAULT 'free',
    p_max_users      integer DEFAULT 1,
    p_max_graphs     integer DEFAULT 1,
    p_ops_allowance  integer DEFAULT 10000,
    p_graph_size_cap bigint  DEFAULT 10000,
    p_signup_token_hash text DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- NAMED-ARG notation: immune to param reordering (all 15). provision_team
    -- RAISEs on every failure mode (RETURNS void, no internal EXCEPTION
    -- handler) → the token INSERT below never runs and the whole mint rolls
    -- back (1 org + 1 token atomic).
    PERFORM public.provision_team(
        p_user_id => p_user_id,
        p_identity => p_identity,
        p_org_id => p_org_id,
        p_org_name => p_org_name,
        p_api_key => p_api_key,
        p_key_hash => p_key_hash,
        p_lookup_hash => p_lookup_hash,
        p_graph_name => p_graph_name,
        p_email => p_email,
        p_key_prefix => p_key_prefix,
        p_tier => p_tier,
        p_max_users => p_max_users,
        p_max_graphs => p_max_graphs,
        p_ops_allowance => p_ops_allowance,
        p_graph_size_cap => p_graph_size_cap
    );
    IF p_signup_token_hash IS NOT NULL THEN
        -- One live token per org is enforced by uq_agent_signup_tokens_org; a
        -- token_hash collision (the same token presented twice) is a no-op.
        INSERT INTO public.agent_signup_tokens (token_hash, org_id)
        VALUES (p_signup_token_hash, p_org_id)
        ON CONFLICT (token_hash) DO NOTHING;
    END IF;
END;
$$;

-- Re-issued ACL (20260814000001 §5).
REVOKE ALL ON FUNCTION public.provision_team_with_token(uuid, text, text,
    text, text, text, text, text, text, text, text, integer, integer, integer,
    bigint, text) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.provision_team_with_token(uuid, text, text,
    text, text, text, text, text, text, text, text, integer, integer, integer,
    bigint, text) TO service_role;

-- 6.5) resolve_signup_token — token → org id (revocation-aware).
--      No parameter rename → CREATE OR REPLACE keeps the existing ACL
--      (20260814000001 §5); deliberately not re-issued.
CREATE OR REPLACE FUNCTION public.resolve_signup_token(p_token_hash text)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_org_id text;
BEGIN
    SELECT org_id INTO v_org_id
      FROM public.agent_signup_tokens
     WHERE token_hash = p_token_hash AND revoked_at IS NULL;
    -- Single tx: token-verify + last_used_at touch are atomic; the caller
    -- checks org suspended/deleted state after. The touch is GATED on a live
    -- token: an unknown OR revoked token must leave no write trace.
    IF v_org_id IS NOT NULL THEN
        UPDATE public.agent_signup_tokens
           SET last_used_at = now()
         WHERE token_hash = p_token_hash;
    END IF;
    RETURN v_org_id;  -- NULL = unknown or revoked (caller maps to uniform 422)
END;
$$;

-- 6.6) recover_team_key — keyless recovery mint (FOR UPDATE serialized).
DROP FUNCTION IF EXISTS public.recover_team_key(text, text, text, text, integer);
CREATE OR REPLACE FUNCTION public.recover_team_key(
    p_token_hash    text,
    p_org_id        text,
    p_lookup_hash   text,
    p_key_prefix    text,
    p_max_api_keys  integer DEFAULT 2   -- tier-derived cap (caller; free = 2)
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_org_id      text;
    v_count       integer;
    v_oldest_id   text;
    v_inserted_id text;
BEGIN
    -- ⛔ Row lock: serializes concurrent recoveries for the SAME token. Zero
    -- rows → fail closed (revoke race or org mismatch) — NEVER mint on a
    -- zero-row lock.
    SELECT org_id INTO v_org_id
      FROM public.agent_signup_tokens
     WHERE token_hash = p_token_hash
       AND revoked_at IS NULL
       AND org_id = p_org_id
     FOR UPDATE;
    IF v_org_id IS NULL THEN
        RAISE EXCEPTION 'recover_team_key: token not found or revoked';
    END IF;

    -- Soft-deleted orgs are unrecoverable (caller maps to the uniform 422 —
    -- indistinguishable from never-existed). Suspension is a CALLER-side 403.
    IF EXISTS (SELECT 1 FROM public.organizations
                WHERE id = p_org_id AND deleted_at IS NOT NULL) THEN
        RAISE EXCEPTION 'recover_team_key: team deleted';
    END IF;

    -- Recovery mint FIRST: idempotent — ON CONFLICT (lookup_hash) DO NOTHING
    -- makes a retried recovery with the same key material a NO-OP (no second
    -- key). created_via='recovery'; created_by is DERIVED inside the RPC
    -- ('st_' + token-hash prefix) — never caller-supplied.
    INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix,
                                 created_via, created_by)
    VALUES ('key_' || p_org_id || '_' || left(p_lookup_hash, 12),
            p_org_id, p_lookup_hash, p_key_prefix,
            'recovery', 'st_' || left(p_token_hash, 12))
    ON CONFLICT (lookup_hash) DO NOTHING
    RETURNING id INTO v_inserted_id;

    -- Cap: ONLY when a new key was actually minted — a no-op retry must never
    -- revoke a live key. Count AFTER the insert; at/over cap, revoke the
    -- OLDEST non-bootstrap key (deterministic, #750.10 semantics).
    IF v_inserted_id IS NOT NULL THEN
        SELECT count(*) INTO v_count
          FROM public.api_keys
         WHERE org_id = p_org_id AND revoked_at IS NULL
           AND (created_via IS NULL OR created_via <> 'bootstrap');
        IF v_count > p_max_api_keys THEN
            SELECT id INTO v_oldest_id
              FROM public.api_keys
             WHERE org_id = p_org_id AND revoked_at IS NULL
               AND (created_via IS NULL OR created_via <> 'bootstrap')
             ORDER BY created_at ASC NULLS FIRST
             LIMIT 1;
            IF v_oldest_id IS NOT NULL THEN
                UPDATE public.api_keys SET revoked_at = now() WHERE id = v_oldest_id;
            END IF;
        END IF;
    END IF;

    RETURN p_org_id;
END;
$$;

-- Re-issued ACL (20260814000001 §5).
REVOKE ALL ON FUNCTION public.recover_team_key(text, text, text, text, integer)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.recover_team_key(text, text, text, text, integer)
    TO service_role;

-- 6.7) revoke_signup_token — user-facing revocation RPC.
DROP FUNCTION IF EXISTS public.revoke_signup_token(text, text);
CREATE OR REPLACE FUNCTION public.revoke_signup_token(
    p_token_hash text,
    p_org_id     text
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    UPDATE public.agent_signup_tokens
       SET revoked_at = now()
     WHERE token_hash = p_token_hash
       AND org_id = p_org_id
       AND revoked_at IS NULL;
END;
$$;

-- Re-issued ACL (20260826000001 §2).
REVOKE ALL ON FUNCTION public.revoke_signup_token(text, text)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.revoke_signup_token(text, text) TO service_role;

-- 6.8) metering_increment — atomic dual-column write-op increment.
DROP FUNCTION IF EXISTS public.metering_increment(text, text, integer, integer);
CREATE OR REPLACE FUNCTION public.metering_increment(
    p_org_id         text,
    p_period         text,
    p_n              integer DEFAULT 1,
    p_nodes_written  integer DEFAULT 0
)
RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE v_ops integer;
BEGIN
    INSERT INTO public.metering_records (org_id, period, write_ops, nodes_written)
    VALUES (p_org_id, p_period, p_n, p_nodes_written)
    ON CONFLICT (org_id, period)
    DO UPDATE SET write_ops = public.metering_records.write_ops + p_n,
                  nodes_written = public.metering_records.nodes_written + p_nodes_written,
                  updated_at = now()
    RETURNING write_ops INTO v_ops;
    RETURN v_ops;
END;
$$;

-- Re-issued ACL (20260813000002; 0014's grant rode on that CREATE OR REPLACE,
-- which the DROP above reset).
REVOKE ALL ON FUNCTION public.metering_increment(text, text, integer, integer)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment(text, text, integer, integer)
    TO service_role;

-- 6.9) metering_increment_ask — per-query ask metering.
DROP FUNCTION IF EXISTS public.metering_increment_ask(text, text, integer,
    integer, integer, double precision);
CREATE OR REPLACE FUNCTION public.metering_increment_ask(
    p_org_id     text,
    p_period     text,
    p_calls      integer DEFAULT 1,
    p_tokens_in  integer DEFAULT 0,
    p_tokens_out integer DEFAULT 0,
    p_cost_usd   double precision DEFAULT 0
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.metering_records
        (org_id, period, ask_calls, ask_tokens_in, ask_tokens_out, ask_cost_usd)
    VALUES (p_org_id, p_period, p_calls, p_tokens_in, p_tokens_out, p_cost_usd)
    ON CONFLICT (org_id, period)
    DO UPDATE SET
        ask_calls      = public.metering_records.ask_calls + p_calls,
        ask_tokens_in  = public.metering_records.ask_tokens_in + p_tokens_in,
        ask_tokens_out = public.metering_records.ask_tokens_out + p_tokens_out,
        ask_cost_usd   = public.metering_records.ask_cost_usd + p_cost_usd,
        updated_at     = now();
END;
$$;

-- Re-issued ACL (20260829000001).
REVOKE ALL ON FUNCTION public.metering_increment_ask(text, text, integer,
    integer, integer, double precision) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.metering_increment_ask(text, text, integer,
    integer, integer, double precision) TO service_role;

-- 6.10) trg_api_keys_abuse_fn — key_create telemetry trigger body. Runs with
--       an EMPTY search_path (parent RPC is SECURITY DEFINER SET search_path
--       = ''), so every reference stays fully schema-qualified.
CREATE OR REPLACE FUNCTION public.trg_api_keys_abuse_fn()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.abuse_events (org_id, event_type, key_id)
    VALUES (NEW.org_id, 'key_create', NEW.id);
    RETURN NEW;
END;
$$;

-- 6.11) abuse_suspend / abuse_unsuspend — enforcement RPCs.
DROP FUNCTION IF EXISTS public.abuse_suspend(text);
DROP FUNCTION IF EXISTS public.abuse_unsuspend(text);
CREATE OR REPLACE FUNCTION public.abuse_suspend(p_org_id text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    UPDATE public.organizations
       SET suspended_at = now()
     WHERE id = p_org_id AND suspended_at IS NULL;
    INSERT INTO public.abuse_events (org_id, event_type)
    VALUES (p_org_id, 'suspend');
END;
$$;

CREATE OR REPLACE FUNCTION public.abuse_unsuspend(p_org_id text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    -- Clear BOTH enforcement and stage-1 staging state: an un-suspended org
    -- starts clean (a lingering flagged_at would let the next single-window
    -- breach escalate to suspension without a fresh flag).
    UPDATE public.organizations
       SET suspended_at = NULL, flagged_at = NULL
     WHERE id = p_org_id;
    INSERT INTO public.abuse_events (org_id, event_type)
    VALUES (p_org_id, 'unsuspend');
    -- End every flag episode: without this, the first post-recovery burst
    -- would auto-suspend on the stale flag row (delta-13 regression).
    INSERT INTO public.abuse_events (org_id, event_type, rule)
    VALUES (p_org_id, 'flag_clear', 'point_create'),
           (p_org_id, 'flag_clear', 'key_create');
END;
$$;

-- Re-issued ACL (0015).
REVOKE ALL ON FUNCTION public.abuse_suspend(text) FROM public, anon, authenticated;
REVOKE ALL ON FUNCTION public.abuse_unsuspend(text) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.abuse_suspend(text) TO service_role;
GRANT EXECUTE ON FUNCTION public.abuse_unsuspend(text) TO service_role;

-- 6.12) claim_membership — anon→user claim. Post-demotion contract
--       (20260827000002). No parameter rename → CREATE OR REPLACE keeps the
--       existing ACL (20260827000002 §4c). The returned jsonb key `team_id`
--       → `org_id`: the
--       Python wrapper discards the body (PostgREST return=minimal), so the
--       key is not read anywhere.
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
    v_org_id          text;
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

    -- Step 1: resolve the org from api_keys (authoritative key→org binding).
    SELECT org_id, created_via, expires_at
      INTO v_org_id, v_key_created_via, v_key_expires_at
      FROM public.api_keys
     WHERE lookup_hash = p_lookup_hash AND revoked_at IS NULL
     LIMIT 1;

    IF v_org_id IS NULL THEN
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
        SELECT 1 FROM public.org_memberships
         WHERE org_id = v_org_id AND user_id = p_user_id
           AND role = 'owner' AND status = 'active'
    ) THEN
        v_idempotent := true;
    ELSE

    -- Step 3: find the NULL-user_id owner row (the anon anchor) + its key
    -- material (the merge path copies it for key continuity — same key, same
    -- org, memories intact).
    SELECT id, lookup_hash, key_hash
      INTO v_owner_row_id, v_owner_lookup, v_owner_key_hash
      FROM public.org_memberships
     WHERE org_id = v_org_id AND role = 'owner'
       AND user_id IS NULL AND status = 'active'
     LIMIT 1;

    IF v_owner_row_id IS NULL THEN
        RAISE EXCEPTION 'claim_membership:already_claimed';
    END IF;

    -- Step 4: does a membership row already exist for (p_user_id, v_org_id)?
    SELECT id INTO v_existing_row_id
      FROM public.org_memberships
     WHERE user_id = p_user_id AND org_id = v_org_id
     LIMIT 1;

    IF v_existing_row_id IS NOT NULL THEN
        DELETE FROM public.org_memberships
         WHERE id = v_owner_row_id AND user_id IS NULL;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'claim_membership:already_claimed';
        END IF;
        UPDATE public.org_memberships
           SET role        = 'owner',
               status      = 'active',
               identity    = NULL,
               lookup_hash = COALESCE(lookup_hash, v_owner_lookup),
               key_hash    = COALESCE(key_hash, v_owner_key_hash),
               updated_at  = now()
         WHERE id = v_existing_row_id;
    ELSE
        -- Step 5: plain claim (race guard: user_id IS NULL conjunct).
        UPDATE public.org_memberships
           SET user_id   = p_user_id,
               identity  = NULL,
               updated_at = now()
         WHERE id = v_owner_row_id AND user_id IS NULL;

        IF NOT FOUND THEN
            RAISE EXCEPTION 'claim_membership:already_claimed';
        END IF;
    END IF;
    END IF;  -- end of idempotent re-claim IF/ELSE

    -- Step 6: created_by attribution migration — anon-/reg- keys in the
    -- claiming org belong to the claimer now. Parenthesized: the reg- branch
    -- MUST be org-scoped (a bare `A AND B OR C` would rewrite every org's
    -- reg- keys globally — plan-review P1).
    UPDATE public.api_keys
       SET created_by = p_user_id::text
     WHERE org_id = v_org_id
       AND (created_by LIKE 'anon-%' OR created_by LIKE 'reg-%');

    -- Step 7: drop any leftover placeholder row for this user.
    SELECT id INTO v_placeholder_id
      FROM public.org_memberships
     WHERE user_id = p_user_id AND org_id = ''
     LIMIT 1;
    IF v_placeholder_id IS NOT NULL THEN
        DELETE FROM public.org_memberships WHERE id = v_placeholder_id;
    END IF;

    RETURN jsonb_build_object('status', 'claimed', 'org_id', v_org_id,
                              'idempotent', v_idempotent);
END;
$$;

-- ============================================================================
-- 7) Caller coupling — READ BEFORE LANDING (see the PR body / slice report)
-- ----------------------------------------------------------------------------
-- A column rename is not separable from the code that names the columns. This
-- migration renames `team_memberships`→`org_memberships`, `teams`→
-- `organizations`, `team_id`→`org_id` and `p_team_id`→`p_org_id`, so the
-- following surfaces MUST move in the same landing window or the control plane
-- fails closed (every tenant-scoped read returns 0 rows):
--   · tortoise/supabase_control.py — 231 `team_id` sites: PostgREST
--     select/filter lists, table names ("teams", "team_memberships") and the
--     p_team_id / p_team_name RPC params.
--   · tortoise/hosted_api.py, tortoise/abuse.py, tortoise/backup_sweep.py,
--     tortoise/hosted_backup.py, tortoise/oauth.py, tortoise/billing.py.
--   · supabase/functions/tenant-provision/index.ts — rpc("provision_team",
--     { p_team_id, p_team_name }).
--   · supabase/tests/*.sql + supabase/tests/pglite/validate.mjs — the schema
--     drill asserts on `teams` / `team_memberships` / `team_id` and sets the
--     `app.current_team_id` GUC (now `app.current_org_id`); validate.mjs also
--     pins the migration file list and its spot-check queries.
-- ============================================================================
