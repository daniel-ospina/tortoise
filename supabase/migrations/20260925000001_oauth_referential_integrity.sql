-- ============================================================================
-- Migration 20260925000001: OAuth referential integrity (issue #3036)
-- ----------------------------------------------------------------------------
-- 0016 declared two cross-table OAuth columns with NO `REFERENCES` clause:
--
--   * oauth_access_tokens.refresh_token_id  → the refresh grant that issued
--                                             the access token (D5)
--   * oauth_refresh_tokens.rotated_from     → the refresh row this row
--                                             replaced on rotation (a CHAIN)
--
-- Every other cross-table column in 0016 IS constrained (client_id / team_id
-- with ON DELETE CASCADE). Without these two, reaping a refresh row leaves
-- access rows pointing at nothing, and the rotation chain is unenforced.
-- #2863's compensation deliberately SOFT-REVOKES rather than deletes precisely
-- because an ambiguous-commit delete would leave a live access row with a
-- dangling refresh_token_id. This migration removes that fragility at the
-- schema layer.
--
-- ON DELETE POLICY — `SET NULL`, deliberately, for BOTH. Neither is CASCADE:
--
--   * refresh_token_id. An access token is an INDEPENDENT bearer credential,
--     validated at the MCP boundary by its own hash + expires_at + revoked_at
--     (D6 introspection) — never by dereferencing its refresh row. Removing a
--     dead refresh row must therefore not revoke a live access token (CASCADE
--     would) nor block GC of the dead row (RESTRICT would). SET NULL drops the
--     provenance back-link and nothing else.
--
--   * rotated_from. This forms a rotation CHAIN (each new refresh row points at
--     the row it replaced), so ON DELETE CASCADE would delete the entire
--     history — including the CURRENTLY LIVE token — the moment the oldest row
--     was reaped. RESTRICT would pin every ancestor in place for as long as any
--     descendant survives, so a revoked row could never be reaped. SET NULL
--     keeps every surviving row and loses at most one back-link.
--
-- In practice a live access row never references a reap-eligible refresh row:
-- access rows expire in ACCESS_TOKEN_TTL_S and are reaped long before refresh
-- rows (REFRESH_TOKEN_TTL_S). The FK action is the safety net for a manual /
-- admin delete, not a routine path.
--
-- BACKFILL REPAIR: the FKs are added only after nulling any PRE-EXISTING
-- dangling pointer (a row whose target id no longer exists). Production should
-- have none (the delete paths soft-revoke), but `ADD CONSTRAINT` would abort
-- the deploy on the first dangling row, so the repair is unconditional and
-- idempotent. It touches no other column and moves no data.
--
-- Additive only: two NULL-able FK constraints. No column added/dropped, no row
-- inserted/updated except the dangling-pointer repair above.
-- ============================================================================

-- 1) Repair dangling pointers so the constraints can be added and validated.
UPDATE public.oauth_access_tokens AS a
   SET refresh_token_id = NULL
 WHERE a.refresh_token_id IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM public.oauth_refresh_tokens r
        WHERE r.id = a.refresh_token_id
   );

UPDATE public.oauth_refresh_tokens AS t
   SET rotated_from = NULL
 WHERE t.rotated_from IS NOT NULL
   AND NOT EXISTS (
       SELECT 1 FROM public.oauth_refresh_tokens r
        WHERE r.id = t.rotated_from
   );

-- 2) Add the constraints. DROP-then-ADD keeps a re-apply idempotent
--    (CREATE TABLE IF NOT EXISTS in 0016 makes the columns, not the FKs).
ALTER TABLE public.oauth_access_tokens
    DROP CONSTRAINT IF EXISTS fk_oauth_access_tokens_refresh_token;
ALTER TABLE public.oauth_access_tokens
    ADD CONSTRAINT fk_oauth_access_tokens_refresh_token
    FOREIGN KEY (refresh_token_id)
    REFERENCES public.oauth_refresh_tokens (id)
    ON DELETE SET NULL;

ALTER TABLE public.oauth_refresh_tokens
    DROP CONSTRAINT IF EXISTS fk_oauth_refresh_tokens_rotated_from;
ALTER TABLE public.oauth_refresh_tokens
    ADD CONSTRAINT fk_oauth_refresh_tokens_rotated_from
    FOREIGN KEY (rotated_from)
    REFERENCES public.oauth_refresh_tokens (id)
    ON DELETE SET NULL;
