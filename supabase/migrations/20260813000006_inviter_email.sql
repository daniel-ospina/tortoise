-- Migration 20260813000006: invitations.inviter_email — display identity for the
-- invite-accept page (#307/#1177).
--
-- The accept page shows the inviter's identifier in the email copy slot
-- "[inviter email or github]". The JWT always carries the inviter's email, so we
-- capture it at mint time (invited_by is the user id; email is the display form).
-- Nullable: legacy rows minted before this migration have no inviter_email; the
-- info endpoint returns a fallback ("a team member").
--
-- RLS: service_role already has table-level ALL; the column is added to the
-- authenticated SELECT grant (it is not sensitive — it is the display name shown
-- to whoever holds the invite token).

ALTER TABLE public.invitations
    ADD COLUMN IF NOT EXISTS inviter_email text;

-- email_sent_at: provider-accept stamp for the invite email (#307) — the
-- Supabase branch's on_sent callback PATCHes this; without the column the
-- stamp is a silent no-op (PostgREST 400 caught + logged per send).
ALTER TABLE public.invitations
    ADD COLUMN IF NOT EXISTS email_sent_at timestamptz;

GRANT SELECT (id, team_id, role, invited_by, inviter_email, email, status,
              accepted_at, expires_at, created_at)
    ON public.invitations TO authenticated;

-- NOTE: renamed from 20260813000005_inviter_email.sql (2026-08-14) — the
-- prefix 20260813000005 was already taken by 20260813000005_dashboard_login.sql
-- (#1148, applied in prod). Duplicate prefixes abort `supabase db push`
-- (check-migration-append-only: "0012x2/0015x2 bug") AND mask pending state in
-- check-migration-drift (sort -u dedup). Rename restores both invariants.
