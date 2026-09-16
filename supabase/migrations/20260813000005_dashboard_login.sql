-- ============================================================================
-- Migration 20260813000005: dashboard key-login flag + per-key enabled
-- ----------------------------------------------------------------------------
-- #1148: two controls the Protect-your-account journey ships:
--
--   teams.dashboard_key_login  (default true)  — whether API-key login is an
--     accepted credential for the DASHBOARD (management surface). When false,
--     key-auth calls to management endpoints (keys mint/revoke, backups
--     restore, billing) return 403 dashboard_login_disabled; graph endpoints
--     keep accepting the key. Claimed owners toggle this (session-authed).
--     Anon teams always keep it true (the Protect screen IS the bootstrap).
--
--   api_keys.enabled  (default true) — per-key on/off. A disabled key stops
--     authenticating (resolve_api_key rejects it) but stays listed so it can
--     be re-enabled. New keys are enabled by default.
--
-- Both are additive columns; re-apply is idempotent via IF NOT EXISTS.
-- ============================================================================

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS dashboard_key_login boolean NOT NULL DEFAULT true;

ALTER TABLE public.api_keys
    ADD COLUMN IF NOT EXISTS enabled boolean NOT NULL DEFAULT true;

-- registry-mode parity: the selfhost Team/APIKey nodes carry the same
-- attributes (read via the registry get_current_team path). Added here as a
-- comment anchor — registry mode stores them on the nodes at create time
-- (see hosted_api.agent_signup / _cmd_signup); no SQL needed.
