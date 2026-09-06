-- ============================================================================
-- Migration 20260906000001: graphs.deleted_at (trash-can grace, #2304)
-- ----------------------------------------------------------------------------
-- Epic #2083 follow-up (#2304, owner Option C): delete = quarantine with a
-- disclosed recovery window (7 days), then physical purge. Enforcing the
-- window needs the deletion instant on the tombstone — today status='deleted'
-- carries no timestamp and nothing can distinguish "just deleted" from
-- "deleted forever ago" (legacy tombstones predate everything).
--
-- Pure additive: existing rows (status='deleted', deleted_at NULL) are the
-- LEGACY tombstone class — the purge treats them as past-grace, gated by the
-- namespace ownership guard. Reversible: ALTER TABLE DROP COLUMN.
--
-- Owner: epistemic-team. Issue: #2304 (scoping docs/scoping-2304-delete-semantics.md).
-- ============================================================================
ALTER TABLE public.graphs
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz;

-- Surface the new column to the same read grant set as the other management
-- metadata (PostgREST seam reads deleted_at for the trash list).
GRANT SELECT (id, team_id, name, kind, namespace, status, recording,
             created_at, deleted_at)
    ON public.graphs TO authenticated;
