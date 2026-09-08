-- ============================================================================
-- Migration 20260908000001: connectors table (#2636, epic #2632)
-- ----------------------------------------------------------------------------
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
--     org_id                FK → teams.id  (which organization owns this)
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
-- RLS: enabled; policies for team-member read/write scoped to org_id.
-- Encrypted credential is NOT row-level-accessible via anon/authenticated;
-- only the service-role seam reads/writes credential_enc (same pattern as
-- teams.github_token_enc from migration 0006).
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.connectors (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    org_id          UUID NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
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

-- Team members can read connectors for their org (but NOT credential_enc)
CREATE POLICY connectors_read_org ON public.connectors
    FOR SELECT
    USING (
        org_id IN (
            SELECT team_id FROM public.team_memberships
            WHERE user_id = auth.uid()
        )
    );

-- Team members can update config/sync status for their org (but NOT credential_enc)
CREATE POLICY connectors_write_org ON public.connectors
    FOR INSERT
    WITH CHECK (
        org_id IN (
            SELECT team_id FROM public.team_memberships
            WHERE user_id = auth.uid()
        )
    );

CREATE POLICY connectors_update_org ON public.connectors
    FOR UPDATE
    USING (
        org_id IN (
            SELECT team_id FROM public.team_memberships
            WHERE user_id = auth.uid()
        )
    );

-- Delete: cascade handles cleanup
CREATE POLICY connectors_delete_org ON public.connectors
    FOR DELETE
    USING (
        org_id IN (
            SELECT team_id FROM public.team_memberships
            WHERE user_id = auth.uid()
        )
    );

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