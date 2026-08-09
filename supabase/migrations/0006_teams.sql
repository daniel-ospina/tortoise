-- Migration 0006: teams table — Supabase control-plane source of truth
-- Epic: #669 plan v2 Task 1 (docs/plans/2026-08-08-669-plan.md)
--
-- Postgres now holds the canonical Team record (mirrors the FalkorDB
-- control_plane registry). The platform backend (FastAPI, service role)
-- reads/writes teams; browser clients read their own team through the
-- tenant-scoped GUC pattern established by migration 0002 (audit_events):
-- the backend verifies the API key, resolves the team, sets
-- `app.current_team_id` on its own DB session, then queries as a tenant
-- role. PostgREST cannot set custom GUCs, so an unset GUC = 0 rows
-- (deny-by-default for direct browser access).
--
-- Column-level protection: github_token_enc is the encrypted GitHub token
-- for import flows — never exposed to anon/authenticated. NOTE: a bare
-- `REVOKE SELECT (col)` is a no-op in Postgres when the role holds
-- table-level SELECT (attacl stays NULL → table ACL fallback; verified
-- against REL_17_STABLE aclchk.c + empirically). The effective pattern is
-- revoke table-level access, then re-grant the safe columns explicitly.

-- ============================================================================
-- Table: teams
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.teams (
    id                  text PRIMARY KEY,  -- ULID or 26-hex (NO format CHECK —
                                            -- 0005-era provisioning mints
                                            -- 26-hex ids; future ULIDs must not
                                            -- be rejected)
    name                text NOT NULL,
    tier                text NOT NULL DEFAULT 'free',
    created_at          timestamptz NOT NULL DEFAULT now(),
    stripe_customer_id  text,
    subscription_id     text,
    backup_enabled      boolean NOT NULL DEFAULT false,
    backup_latest_at    timestamptz,
    backup_restored_at  timestamptz,
    max_users           integer,
    max_teams           integer,
    max_graphs          integer,
    ops_allowance       integer,
    graph_size_cap      bigint,
    graph_name          text NOT NULL,  -- sdk.team_create uses team_{name}, NOT team_{id}
    email               text,
    onboarding_state    jsonb NOT NULL DEFAULT '{}'::jsonb,
    github_token_enc    text,  -- column-protected below (revoked from anon/authenticated)
    github_org          text
);

-- ============================================================================
-- RLS: tenant-scoped reads via GUC (precedent: 0002 audit_team_isolation);
-- service_role manages all (writes go through the platform backend).
-- ============================================================================
ALTER TABLE public.teams ENABLE ROW LEVEL SECURITY;

CREATE POLICY team_guc_read ON public.teams
    FOR SELECT
    TO authenticated
    USING (id = current_setting('app.current_team_id', true));

CREATE POLICY team_service_role_all ON public.teams
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ============================================================================
-- Column-level protection (effective pattern — see header note):
-- kill table-level grants for public-facing roles, re-grant every column
-- EXCEPT github_token_enc. service_role keeps table-level ALL (Supabase
-- default privileges) and BYPASSRLS, so the platform can still read the
-- encrypted token.
-- ============================================================================
REVOKE ALL ON public.teams FROM anon, authenticated, public;

GRANT SELECT (id, name, tier, created_at, stripe_customer_id,
              subscription_id, backup_enabled, backup_latest_at,
              backup_restored_at, max_users, max_teams, max_graphs,
              ops_allowance, graph_size_cap, graph_name, email,
              onboarding_state, github_org)
    ON public.teams TO authenticated;

-- ============================================================================
-- audit_events.actor_user_id: UUID → TEXT
-- 0002 declared UUID, but the Python audit logger writes TEXT actors
-- (non-UUID actors like 'service-bootstrap' or agent signups must work).
-- Idempotent: ALTER COLUMN TYPE text on an already-text column is a no-op.
-- ============================================================================
ALTER TABLE public.audit_events
    ALTER COLUMN actor_user_id TYPE text USING actor_user_id::text;
