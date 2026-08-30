-- ============================================================================
-- Migration 20260830000001: import post-swap pack-failure marker (#2040)
-- ----------------------------------------------------------------------------
-- #2040 (code-review round 2, PR #2054): the import already-fast-path must
-- refuse `{"already": true}` when the MOST RECENT attempt at a sha failed
-- AFTER the graph swap (pack application) — the live graph holds that dump
-- but its vocabulary may not be live. A dedicated marker column the
-- post-swap 422/503 branches stamp (alongside the ledger clear) and the
-- success path clears.
--
--   teams.last_import_pack_failed_sha256  (nullable text) — sha256 of the
--     last artifact that failed POST-SWAP (pack application). Consulted by
--     the import in-lock idempotency short-circuit: `already` fires only
--     when last_import_sha256 == sha AND the marker is NULL/empty. Cleared
--     on successful application. Pre-restore rejections never set it (the
--     graph is untouched, so a prior applied sha legitimately
--     short-circuits).
--
-- Additive + idempotent (IF NOT EXISTS), same pattern as 20260817000001.
-- Nullable, no default, no NOT NULL — a schema missing it (drift, one
-- migration behind) fails soft: the #1096 ladder drops the marker's OWN
-- tier (_TEAM_ADDITIVE_2040_TIER, newest dropped first) so only the marker
-- degrades to unset — the already-fast-path re-validates (convergent,
-- never a lie) while the #1230 ledger + max_points reads stay intact.
-- The control plane reads/writes via service_role, so no new grants are
-- needed (precedent: 20260817000001).
-- ============================================================================

ALTER TABLE public.teams
    ADD COLUMN IF NOT EXISTS last_import_pack_failed_sha256 text;
