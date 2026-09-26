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
-- Every other cross-table column in 0016 IS constrained (client_id / org_id
-- — the latter renamed from team_id by 20260915000001 — with ON DELETE
-- CASCADE). Without these two, reaping a refresh row leaves
-- access rows pointing at nothing, and the rotation chain is unenforced.
-- #2863's compensation deliberately SOFT-REVOKES rather than deletes precisely
-- because an ambiguous-commit delete would leave a live access row with a
-- dangling refresh_token_id. This migration removes that fragility at the
-- schema layer.
--
-- ON DELETE POLICY — `SET NULL`, deliberately, for BOTH. Neither is CASCADE:
--
--   * refresh_token_id. An access token is an INDEPENDENT bearer credential at
--     the MCP boundary, validated by its own hash + expires_at + revoked_at (D6
--     introspection). One path DOES dereference this link in the other
--     direction: refresh_grant looks up the live access row by
--     refresh_token_id in order to revoke it on rotation. SET NULL is still the
--     right action — the sweep deletes ACCESS rows before the refresh rows they
--     point at, and rotation can only run while the refresh row still exists —
--     but the safety of that order rests on an explicit invariant:
--     ACCESS_TOKEN_TTL_S + the access window <= REFRESH_TOKEN_TTL_S + the
--     refresh window. The DEFAULTS satisfy it; it is NOT enforced, so an
--     operator override that inverts the two TTLs relative to the two windows
--     can NULL a LIVE access row's back-link (a provenance loss only — see the
--     ``sweep_oauth_retention`` docstring). Removing a dead refresh row must
--     not revoke a live access token (CASCADE would) nor block GC of the dead
--     row (RESTRICT would). SET NULL drops the provenance back-link and
--     nothing else.
--
--   * rotated_from. This forms a rotation CHAIN (each new refresh row points at
--     the row it replaced), so ON DELETE CASCADE would delete the entire
--     history — including the CURRENTLY LIVE token — the moment the oldest row
--     was reaped. RESTRICT would pin every ancestor in place for as long as any
--     descendant survives, so a revoked row could never be reaped. SET NULL
--     keeps every surviving row and loses at most one back-link.
--
-- In practice a live access row never references a reap-eligible refresh row
-- with the shipped DEFAULTS: access rows expire in ACCESS_TOKEN_TTL_S and are
-- reaped long before refresh rows (REFRESH_TOKEN_TTL_S). The FK action is the
-- safety net for a manual / admin delete, not a routine path.
--
-- BACKFILL REPAIR: the FKs are added only after nulling any PRE-EXISTING
-- dangling pointer (a row whose target id no longer exists). Production should
-- have none (the delete paths soft-revoke), but `ADD CONSTRAINT` would abort
-- the deploy on the first dangling row, so the repair is unconditional and
-- idempotent. It touches no other column and moves no data.
--
-- Additive only: two NULL-able FK constraints + three `expires_at` indexes.
-- No column added/dropped, no row inserted/updated except the dangling-pointer
-- repair above. Lock footprint, because a bare "additive" header under-counts
-- it: each `DROP CONSTRAINT IF EXISTS` takes ACCESS EXCLUSIVE on its table, and
-- each validated `ADD CONSTRAINT` takes SHARE ROW EXCLUSIVE on BOTH the
-- referencing and the referenced table (blocking concurrent writes for the
-- duration) plus a full validation scan of the REFERENCING table, with index
-- lookups on the referenced primary key; each CREATE INDEX is NON-concurrent
-- and locks out writes (not reads) on its token table until the build finishes.
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

-- 3) Retention-sweep indexes (issue #3036). The sweep's predicate is
--    `expires_at < cutoff` on all three tables; 0016 indexed the lookup columns
--    but never `expires_at`, so each hourly sweep would sequentially scan the
--    tables. Plain btree is enough for the ordered comparison.
CREATE INDEX IF NOT EXISTS idx_oauth_codes_expires
    ON public.oauth_codes (expires_at);
CREATE INDEX IF NOT EXISTS idx_oauth_access_tokens_expires
    ON public.oauth_access_tokens (expires_at);
CREATE INDEX IF NOT EXISTS idx_oauth_refresh_tokens_expires
    ON public.oauth_refresh_tokens (expires_at);
