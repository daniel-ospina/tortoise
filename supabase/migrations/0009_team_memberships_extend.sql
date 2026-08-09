-- Migration 0009: team_memberships extension — lookup_hash, identity,
-- agent-signup path, and a REAL api_key column-REVOKE
-- Epic: #669 plan v2 Task 1 (docs/plans/2026-08-08-669-plan.md)
--
-- 1) lookup_hash — SHA-256(pepper + key) for key lookup parity with api_keys.
-- 2) identity — agent-signup rows for anon users (NULL user_id until the
--    agent completes signup; the row is anchored by identity, not auth.users).
-- 3) chk_member_or_invite is replaced with a CHECK that ALSO admits the
--    identity path: user_id IS NOT NULL OR invited_email IS NOT NULL
--    OR identity IS NOT NULL (0003's constraint rejected agent rows).
-- 4) user_id becomes nullable (FK to auth.users(id) KEPT — the constraint
--    stays, only NOT NULL is dropped).
--
-- NOTE — this migration also REPAIRS the api_key column protection from
-- 0003. A bare `REVOKE SELECT (api_key) FROM authenticated, anon` is a
-- no-op in Postgres when those roles hold table-level SELECT: the column
-- ACL stays NULL and the table ACL is consulted (verified against
-- REL_17_STABLE aclchk.c + empirically) — the one-time-reveal api_key was
-- still readable via direct SELECT, defeating the reveal_api_key RPC
-- (0003 P1). The effective pattern: revoke table-level access, re-grant the
-- safe columns explicitly. The api_key column, RLS policies, and the
-- reveal_api_key SECURITY DEFINER RPC are otherwise UNCHANGED.

-- ============================================================================
-- 1) New columns
-- ============================================================================
ALTER TABLE public.team_memberships
    ADD COLUMN IF NOT EXISTS lookup_hash text;

CREATE INDEX IF NOT EXISTS idx_team_memberships_lookup_hash
    ON public.team_memberships (lookup_hash);

ALTER TABLE public.team_memberships
    ADD COLUMN IF NOT EXISTS identity text;

-- ============================================================================
-- 2) Amended membership CHECK (0003 chk_member_or_invite + identity path)
-- ============================================================================
ALTER TABLE public.team_memberships
    DROP CONSTRAINT IF EXISTS chk_member_or_invite;

ALTER TABLE public.team_memberships
    ADD CONSTRAINT chk_member_or_invite
    CHECK (user_id IS NOT NULL OR invited_email IS NOT NULL OR identity IS NOT NULL);

-- ============================================================================
-- 3) user_id nullable (FK to auth.users(id) preserved)
-- ============================================================================
ALTER TABLE public.team_memberships
    ALTER COLUMN user_id DROP NOT NULL;

-- ============================================================================
-- 4) api_key column protection — effective pattern (see header note).
--    api_key stays one-time-reveal only (reveal_api_key RPC, SECURITY
--    DEFINER as table owner → unaffected by grants/RLS).
-- ============================================================================
REVOKE ALL ON public.team_memberships FROM anon, authenticated, public;

GRANT SELECT (id, user_id, team_id, team_name, key_hash, graph_name, role,
              status, invited_email, lookup_hash, identity,
              created_at, updated_at)
    ON public.team_memberships TO authenticated;

-- RLS policies from 0001/0003 are untouched (still enforced):
--   "Users view own memberships"  — SELECT TO authenticated (user_id = auth.uid())
--   "Invitee views own invite"    — SELECT TO authenticated (invited_email = auth.jwt()->>'email')
--   "Service role manages all memberships" — ALL TO service_role
