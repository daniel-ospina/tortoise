-- Migration 0016: OAuth 2.1 server tables — remote MCP auth (#524)
-- Issue: #524 (OAuth 2.1 for remote MCP auth, hosted) · Scoping:
-- docs/scoping/2026-08-15-524-oauth-mcp-scoping.md (locked decisions D1-D6)
--
-- Storage substrate for the authorization-code + PKCE flow, Dynamic Client
-- Registration (RFC 7591), RFC 8707 resource indicators, and rotating
-- refresh tokens. Accessed ONLY via the PostgREST service-role seam
-- (tortoise/oauth.py) — the same control-plane pattern as api_keys.
--
--   * oauth_clients          — DCR registrations (client_id, metadata,
--                              client_secret_hash for confidential clients)
--   * oauth_codes            — single-use, PKCE-bound authorization codes
--   * oauth_access_tokens    — oat_ bearer tokens (introspected at the MCP
--                              boundary, D6); revoked_at is authoritative
--   * oauth_refresh_tokens   — per (user, team) rotating grants (D5);
--                              revoked_at is authoritative; family revocation
--                              on team suspension (D5)
--
-- Secrets are stored hashed only (SHA-256 hexdigest — mirrors
-- api_keys.lookup_hash). Nothing here is browser-readable (RLS: service_role
-- only, like abuse_events).

CREATE TABLE IF NOT EXISTS public.oauth_clients (
    id                          text PRIMARY KEY,
    -- SHA-256(client_secret) — issued once at registration (RFC 7591 §4.3);
    -- NULL for public clients (token_endpoint_auth_method='none').
    client_secret_hash          text,
    client_name                 text NOT NULL,
    redirect_uris               jsonb NOT NULL DEFAULT '[]'::jsonb,
    grant_types                 jsonb NOT NULL DEFAULT '["authorization_code"]'::jsonb,
    response_types              jsonb NOT NULL DEFAULT '["code"]'::jsonb,
    token_endpoint_auth_method  text NOT NULL DEFAULT 'none',
    scope                       text NOT NULL DEFAULT 'mcp',
    created_at                  timestamptz NOT NULL DEFAULT now(),
    revoked_at                  timestamptz
);

CREATE TABLE IF NOT EXISTS public.oauth_codes (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code_hash               text NOT NULL UNIQUE,
    client_id               text NOT NULL REFERENCES public.oauth_clients(id) ON DELETE CASCADE,
    user_id                 text NOT NULL,
    team_id                 text NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
    redirect_uri            text NOT NULL,
    code_challenge          text NOT NULL,
    code_challenge_method   text NOT NULL DEFAULT 'S256',
    scope                   text NOT NULL DEFAULT 'mcp',
    -- RFC 8707 resource indicator the grant was bound to (NULL = default).
    resource                text,
    expires_at              timestamptz NOT NULL,
    used_at                 timestamptz,
    created_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.oauth_access_tokens (
    id                  text PRIMARY KEY,
    token_hash          text NOT NULL UNIQUE,
    client_id           text NOT NULL REFERENCES public.oauth_clients(id) ON DELETE CASCADE,
    user_id             text NOT NULL,
    team_id             text NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
    scope               text NOT NULL DEFAULT 'mcp',
    expires_at          timestamptz NOT NULL,
    revoked_at          timestamptz,
    refresh_token_id    text,
    created_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.oauth_refresh_tokens (
    id                  text PRIMARY KEY,
    token_hash          text NOT NULL UNIQUE,
    client_id           text NOT NULL REFERENCES public.oauth_clients(id) ON DELETE CASCADE,
    user_id             text NOT NULL,
    team_id             text NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
    scope               text NOT NULL DEFAULT 'mcp',
    expires_at          timestamptz NOT NULL,
    revoked_at          timestamptz,
    rotated_from        text,
    created_at          timestamptz NOT NULL DEFAULT now()
);

-- Lookup indexes for the O(1) hash lookups at auth time.
CREATE INDEX IF NOT EXISTS idx_oauth_codes_client
    ON public.oauth_codes (client_id);
CREATE INDEX IF NOT EXISTS idx_oauth_access_tokens_team
    ON public.oauth_access_tokens (team_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_oauth_refresh_tokens_user_team
    ON public.oauth_refresh_tokens (user_id, team_id, revoked_at);
CREATE INDEX IF NOT EXISTS idx_oauth_refresh_tokens_rotated_from
    ON public.oauth_refresh_tokens (rotated_from);

-- RLS: service_role manages all; no browser surface reads OAuth material.
ALTER TABLE public.oauth_clients ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.oauth_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.oauth_access_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.oauth_refresh_tokens ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS oauth_clients_service_role_all ON public.oauth_clients;
CREATE POLICY oauth_clients_service_role_all ON public.oauth_clients
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS oauth_codes_service_role_all ON public.oauth_codes;
CREATE POLICY oauth_codes_service_role_all ON public.oauth_codes
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS oauth_access_tokens_service_role_all ON public.oauth_access_tokens;
CREATE POLICY oauth_access_tokens_service_role_all ON public.oauth_access_tokens
    FOR ALL TO service_role USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS oauth_refresh_tokens_service_role_all ON public.oauth_refresh_tokens;
CREATE POLICY oauth_refresh_tokens_service_role_all ON public.oauth_refresh_tokens
    FOR ALL TO service_role USING (true) WITH CHECK (true);
