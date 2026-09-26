-- ============================================================================
-- Migration 20260922000001: connectors table (#2636, epic #2632)
-- ----------------------------------------------------------------------------
-- Timestamp note: this file targets the POST-rename tenancy schema
-- (teams→organizations, team_memberships→org_memberships, team_id→org_id,
-- 20260915000001) and must therefore sort AFTER that rename. There is no
-- compatibility view named `teams`; a fresh database applies migrations in
-- filename order, so an earlier timestamp would reference `organizations`
-- before it exists.
--
-- Universal source-connector storage for the Tortoise Connector system.
-- Follows the Onyx/Danswer pattern: a single table with source-type enum,
-- encrypted credential store, JSON scope config, and sync status.
--
-- The pattern is reused across all source types (GitHub → issues/PRs/code,
-- Slack → threads/messages, Linear → issues, Google Drive → docs, etc.):
-- each gets a row here with its own source_type, credential, and scope config.
--
--   connectors:
--     id                    UUID PK
--     org_id                TEXT — FK → organizations.id (which org owns this).
--                                  NOT uuid: organizations.id is text (26-hex ids
--                                  minted by the Edge Function), so a uuid FK is
--                                  un-implementable and `uuid IN (SELECT text)`
--                                  raises `operator does not exist`.
--     source_type           TEXT — 'github' | 'slack' | 'linear' | 'google_drive'
--                                   | 'confluence' | 'notion' | 'asana'
--     config                JSONB — source-specific scope: repo names, folder
--                                   paths, channel IDs, etc.
--     credential_enc        TEXT — JSON-dumped + encrypted credential object:
--                                   {access_token, refresh_token, expires_at,
--                                    token_type, scopes?} — encrypted at rest
--                                   via tortoise.crypto.
--     sync_status           TEXT — 'idle' | 'syncing' | 'error' | 'completed'
--     sync_cursor           JSONB — last checkpoint for incremental sync
--                                   (e.g. {updated_at, page}, {issue_number})
--     last_sync_at          timestamptz — last successful sync completion
--     last_error            TEXT — last error message (cleared on success)
--     created_at            timestamptz — set once
--     updated_at            timestamptz — updated on any change
--
-- RLS: enabled; policies for org-member reads scoped to org_id. There is no
-- `authenticated` write grant (SELECT-only, like every other control-plane
-- table) — every write goes through the service-role seam.
-- credential_enc is protected at the COLUMN level (not by RLS): table-level
-- grants are revoked from anon/authenticated and every column EXCEPT
-- credential_enc is re-granted — the 0006 pattern. Only the service-role seam
-- reads/writes the credential.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.connectors (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          TEXT NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    source_type     TEXT NOT NULL CHECK (source_type IN (
                        'github', 'slack', 'linear', 'google_drive',
                        'confluence', 'notion', 'asana'
                    )),
    config          JSONB NOT NULL DEFAULT '{}',
    credential_enc  TEXT,
    sync_status     TEXT NOT NULL DEFAULT 'idle' CHECK (sync_status IN (
                        'idle', 'syncing', 'error', 'completed'
                    )),
    sync_cursor     JSONB,
    last_sync_at    timestamptz,
    last_error      TEXT,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Unique: one connector per source_type per org
CREATE UNIQUE INDEX IF NOT EXISTS idx_connectors_org_source
    ON public.connectors(org_id, source_type);

-- Index for listing connectors by org
CREATE INDEX IF NOT EXISTS idx_connectors_org
    ON public.connectors(org_id);

-- Index for sync-eligible connectors (idle, or error with retry)
CREATE INDEX IF NOT EXISTS idx_connectors_sync_eligible
    ON public.connectors(sync_status)
    WHERE sync_status IN ('idle', 'error');

-- Enable RLS
ALTER TABLE public.connectors ENABLE ROW LEVEL SECURITY;

-- Org members can read connectors for their org (but NOT credential_enc)
CREATE POLICY connectors_read_org ON public.connectors
    FOR SELECT
    USING (
        org_id IN (
            SELECT org_id FROM public.org_memberships
            WHERE user_id = auth.uid()
        )
    );

-- Org members can insert config/sync status for their org (but NOT credential_enc)
CREATE POLICY connectors_write_org ON public.connectors
    FOR INSERT
    WITH CHECK (
        org_id IN (
            SELECT org_id FROM public.org_memberships
            WHERE user_id = auth.uid()
        )
    );

CREATE POLICY connectors_update_org ON public.connectors
    FOR UPDATE
    USING (
        org_id IN (
            SELECT org_id FROM public.org_memberships
            WHERE user_id = auth.uid()
        )
    );

-- Delete: cascade handles cleanup
CREATE POLICY connectors_delete_org ON public.connectors
    FOR DELETE
    USING (
        org_id IN (
            SELECT org_id FROM public.org_memberships
            WHERE user_id = auth.uid()
        )
    );

-- ============================================================================
-- Column-level protection (effective pattern, per 0006_teams.sql):
-- a bare `REVOKE SELECT (credential_enc)` is a NO-OP in Postgres while the role
-- holds table-level SELECT (attacl stays NULL → table ACL fallback; verified
-- against REL_17_STABLE aclchk.c). Revoke table-level access, then re-grant the
-- non-secret columns explicitly. credential_enc is excluded, so a default
-- PostgREST `select=*` is DENIED for anon/authenticated and the column stays
-- readable only through the service-role seam.
--
-- SELECT-only, matching every other control-plane table in the tree
-- (0006_teams, 0007_api_keys, 0008_invitations, 0009_team_memberships_extend,
-- 20260901000001_graphs_and_key_scopes): `authenticated` gets NO INSERT /
-- UPDATE / DELETE grant. This table is not the one exception — all writes go
-- through the service-role seam (supabase_control.connector_*), which holds
-- table-level ALL + BYPASSRLS from Supabase's default privileges.
--
-- The four RLS policies above are the author's declaration of intent and are
-- RETAINED. The read policy still governs the column-scoped SELECT that IS
-- granted. The write/update/delete policies are satisfied via the service-role
-- path: with no table-level write privilege for `authenticated`, they have no
-- direct client and are inert by design, not by omission.
-- ============================================================================
REVOKE ALL ON public.connectors FROM anon, authenticated, public;

GRANT SELECT (id, org_id, source_type, config, sync_status, sync_cursor,
              last_sync_at, last_error, created_at, updated_at)
    ON public.connectors TO authenticated;

-- Updated-at trigger
CREATE OR REPLACE FUNCTION public.update_connectors_updated_at()
RETURNS trigger AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_connectors_updated_at ON public.connectors;
CREATE TRIGGER trg_connectors_updated_at
    BEFORE UPDATE ON public.connectors
    FOR EACH ROW
    EXECUTE FUNCTION public.update_connectors_updated_at();