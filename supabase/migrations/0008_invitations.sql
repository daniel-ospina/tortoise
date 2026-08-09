-- Migration 0008: invitations table — team invite registry
-- Epic: #669 plan v2 Task 1 (docs/plans/2026-08-08-669-plan.md)
--
-- A pending invitation is redeemed by a token (lookup_hash = SHA-256(pepper
-- + token); token never stored plaintext). Partial unique index on
-- (team_id, email) WHERE status='pending' preserves the uq_team_invite_email
-- semantics from 0003: at most ONE pending invite per (team, email); accepted
-- or revoked invites don't block a fresh re-invite (NULLs are distinct in a
-- plain unique index, so the partial predicate is what enforces this).
--
-- RLS + GUC pattern matches 0002/0006/0007. lookup_hash is column-protected
-- from anon/authenticated (see 0006 header note for why bare column REVOKE
-- is insufficient).

-- ============================================================================
-- Table: invitations
-- ============================================================================
CREATE TABLE IF NOT EXISTS public.invitations (
    id          text PRIMARY KEY,
    team_id     text NOT NULL REFERENCES public.teams(id) ON DELETE CASCADE,
    lookup_hash text NOT NULL,            -- SHA-256(pepper + token); indexed below
    role        text NOT NULL DEFAULT 'member',  -- admin | member
    invited_by  text,                     -- nullable: user id of the inviter
    email       text NOT NULL,
    status      text NOT NULL DEFAULT 'pending',  -- pending|accepted|revoked|expired
    accepted_at timestamptz,
    expires_at  timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Constraint: role is a closed enum (contract: admin|member).
-- DROP-then-ADD keeps re-apply idempotent (see 0007 note).
ALTER TABLE public.invitations
    DROP CONSTRAINT IF EXISTS chk_invitations_role;
ALTER TABLE public.invitations
    ADD CONSTRAINT chk_invitations_role
    CHECK (role IN ('admin', 'member'));

-- Token lookup at acceptance time.
CREATE INDEX IF NOT EXISTS idx_invitations_lookup_hash
    ON public.invitations (lookup_hash);

-- One pending invite per (team, email) — uq_team_invite_email semantics.
CREATE UNIQUE INDEX IF NOT EXISTS uq_invitations_team_email_pending
    ON public.invitations (team_id, email)
    WHERE status = 'pending';

-- ============================================================================
-- RLS: GUC tenant scoping + service_role management.
-- ============================================================================
ALTER TABLE public.invitations ENABLE ROW LEVEL SECURITY;

CREATE POLICY invitations_guc_read ON public.invitations
    FOR SELECT
    TO authenticated
    USING (team_id = current_setting('app.current_team_id', true));

CREATE POLICY invitations_service_role_all ON public.invitations
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- ============================================================================
-- Column protection: hide lookup_hash (acceptance-token hash) from
-- public-facing roles; service_role keeps table-level ALL.
-- ============================================================================
REVOKE ALL ON public.invitations FROM anon, authenticated, public;

GRANT SELECT (id, team_id, role, invited_by, email, status,
              accepted_at, expires_at, created_at)
    ON public.invitations TO authenticated;
