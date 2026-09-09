-- 20260909000003_dashboard_key_login_default_false.sql
--
-- Change the default for teams.dashboard_key_login from true to false.
--
-- Rationale:
--   When a human creates a team (OAuth/email signup via the dashboard), API-key
--   dashboard login should be OFF by default — the human can always log in via
--   their Google/GitHub/email session. Enabling key-to-dashboard access by
--   default is the wrong posture: it makes the API key a weaker credential
--   that can access the full dashboard.
--
--   Agent-created teams (bootstrap/provisioning/wizard flows) already have
--   no human session, so they are explicitly set to true at creation time.
--   Existing teams are NOT altered — only the default for new teams changes.
-- ============================================================================

ALTER TABLE IF EXISTS public.teams
  ALTER COLUMN dashboard_key_login SET DEFAULT false;