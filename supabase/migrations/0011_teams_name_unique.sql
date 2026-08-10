-- Migration 0011: teams.name unique — duplicate-name guard (#765 review P1)
-- Epic: #669 plan v2 Task 8 · Issue: #765
--
-- The registry path rejects duplicate team names (sdk.team_create raises
-- ControlPlaneError 'already exists'); teams.name has no unique index in
-- 0006, so Supabase mode had no atomic guard — two teams could share the
-- same graph_name (team_{name}) → the same FalkorDB namespace → interleaved
-- points/quota between distinct teams (code-review P1, PR #874).
--
-- A unique index on teams.name is the atomic parity guard; the hosted
-- create-team endpoints map the resulting PostgREST 409 to an HTTP 409
-- ('Team name already exists'). The provision_team RPC upserts on id only,
-- so a name collision surfaces as a unique-violation 409 rather than
-- silently sharing a namespace.

CREATE UNIQUE INDEX IF NOT EXISTS uq_teams_name
    ON public.teams (name);
