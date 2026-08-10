-- Migration 0007: api_keys table — control-plane API key registry
-- Epic: #669 plan v2 Task 1 (docs/plans/2026-08-08-669-plan.md)
--
-- Stored API keys are NEVER in plaintext: lookup_hash = SHA-256(pepper + key)
-- (pepper held in app/Edge code, NOT the DB — construction is enforced in
-- tortoise/auth.py + the edge function, never in SQL). Key lookup at request
-- time hashes the presented key and matches lookup_hash. key_prefix is a
-- non-secret identifier (first chars of the key) for dashboard display.
--
-- RLS + GUC pattern matches 0002/0006: backend sets app.current_team_id
-- after key verification; unset GUC = deny-by-default. lookup_hash is
-- column-protected from anon/authenticated (see 0006 header note for why
-- bare column REVOKE is insufficient).

-- ============================================================================
-- Table: api_keys
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.api_keys (
    id           text PRIMARY KEY,
    team_id      text NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
    lookup_hash  text NOT NULL,          -- SHA-256(pepper + key); unique index below
    key_prefix   text,
    created_via  text NOT NULL DEFAULT 'provisioned',  -- provisioned|bootstrap|recovery
    created_by   text,                   -- nullable: user id, or NULL for bootstrap keys
    last_used_at timestamptz,
    expires_at   timestamptz,
    revoked_at   timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now()
);

-- Constraint: created_via is a closed enum (contract: valid values
-- provisioned|bootstrap|recovery). DROP-then-ADD keeps re-apply idempotent
-- (CREATE TABLE IF NOT EXISTS skips on re-run, so ADD CONSTRAINT would
-- otherwise fail with "constraint already exists").
ALTER TABLE public.api_keys
    DROP CONSTRAINT IF EXISTS chk_api_keys_created_via;
ALTER TABLE public.api_keys
    ADD CONSTRAINT chk_api_keys_created_via
    CHECK (created_via IN ('provisioned', 'bootstrap', 'recovery'));

-- Lookup index for key verification (equality on hash at request time).
CREATE UNIQUE INDEX IF NOT EXISTS uq_api_keys_lookup_hash
    ON public.api_keys (lookup_hash);

-- Team-scoped listing (dashboard / admin queries).
CREATE INDEX IF NOT EXISTS idx_api_keys_team_id
    ON public.api_keys (team_id);

-- ============================================================================
-- RLS: GUC tenant scoping + service_role management.
-- ============================================================================
ALTER TABLE public.api_keys ENABLE ROW LEVEL SECURITY;

CREATE POLICY api_keys_guc_read ON public.api_keys
    FOR SELECT
    TO authenticated
    USING (team_id = current_setting('app.current_team_id', true));

CREATE POLICY api_keys_service_role_all ON public.api_keys
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ============================================================================
-- Column protection: hide lookup_hash from public-facing roles (effective
-- pattern — revoke table-level, re-grant safe columns). service_role keeps
-- table-level ALL and can read lookup_hash for key verification.
-- ============================================================================
REVOKE ALL ON public.api_keys FROM anon, authenticated, public;

GRANT SELECT (id, team_id, key_prefix, created_via, created_by,
              last_used_at, expires_at, revoked_at, created_at)
    ON public.api_keys TO authenticated;
