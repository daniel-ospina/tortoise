-- ============================================================================
-- Migration 20260817000001: import idempotency ledger + points-cap override (#1230)
-- ----------------------------------------------------------------------------
-- #1230: three additive `teams` columns the import endpoint's control-plane
-- ledger and the quota read depend on. The WRITE path was already live
-- (`_stamp_import_prop` PATCHes the `teams` row in Supabase mode) but the
-- columns never existed — every PATCH failed silently and the idempotency
-- read (`fresh.last_import_sha256 == sha`) always saw None, so re-import
-- protection was broken. This migration makes the referenced schema real.
--
--   teams.last_import_sha256              (nullable text)  — sha256 of the
--     last SUCCESSFULLY imported artifact; the idempotency read returns
--     "200 already-imported" when it matches the incoming sha.
--   teams.last_import_quarantined_sha256  (nullable text)  — sha256 of the
--     last REJECTED/quarantined artifact (integrity/validation failures) so
--     the failure is observable on the team row.
--   teams.max_points                      (nullable integer) — per-team
--     points-cap override; the plan's max_points / graph_size_cap source
--     (NULL = tier default; quota.py falls back to tier limits).
--
-- All three are additive + idempotent (IF NOT EXISTS), same pattern as the
-- #1148 `enabled` / 20260825000001 `name` columns. Nullable, no default, no
-- NOT NULL — a schema missing them (drift, one migration behind) fails soft
-- on the auth seam (#1096: the ladder drops _TEAM_ADDITIVE_IMPORT_TIER
-- first) and the quota read degrades to tier limits, never a 500. The
-- control plane reads/writes via service_role, so no new grants are needed
-- (precedent: api_keys.enabled, 20260813000005).
-- ============================================================================

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS last_import_sha256 text;

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS last_import_quarantined_sha256 text;

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS max_points integer;
