-- Migration 0011: team soft-delete columns (issue #302 E2E-6-D)
--
-- Owner-only team deletion is two-phase: soft delete (teams.deleted_at
-- stamp + immediate key/membership/invitation revocation) then hard
-- delete after a grace window. The columns added here back the soft-delete
-- marker and the PERSISTED grace window:
--
--   deleted_at  — set by DELETE /v1/teams/{team_id}; the boot + hourly
--                 purge sweep hard-deletes teams whose deleted_at is past
--                 the grace window. team_by_id selects it so export/delete
--                 return 410 while pending.
--   grace_hours — the grace window AS PROMISED at schedule time. The purge
--                 and the idempotent replay honor the stored value (falling
--                 back to TORTOISE_TEAM_DELETE_GRACE_HOURS only for legacy
--                 rows), so a config change mid-grace can never hard-delete
--                 a team before the hard_delete_after the API returned.
--
-- Additive only — no drops, no data migration. Service-role client
-- (tortoise.supabase_control) is the only reader/writer; authenticated/
-- anon column grants are intentionally NOT extended.

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS grace_hours numeric;
