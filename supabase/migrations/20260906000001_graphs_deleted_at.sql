-- ============================================================================
-- Migration 20260906000001: graphs.deleted_at / purged_at (trash-can, #2304)
-- ----------------------------------------------------------------------------
-- Epic #2083 follow-up (#2304, owner Option C): delete = quarantine with a
-- disclosed recovery window (7 days), then physical purge. Enforcing the
-- window needs the deletion instant on the tombstone — today status='deleted'
-- carries no timestamp and nothing can distinguish "just deleted" from
-- "deleted forever ago" (legacy tombstones predate everything).
--
--   deleted_at     timestamptz  — when the row was tombstoned (grace start).
--                                NULL = legacy tombstone (predates the
--                                column; purge treats it as past-grace but
--                                gated by the namespace ownership guard).
--   purged_at      timestamptz  — when the physical purge erased the graph
--                                (namespace + backup artifacts). The row is
--                                KEPT (audit tombstone) — purge is a data
--                                erasure, not a row deletion.
--   purged_residual bool        — purge finished but the namespace was
--                                retained (re-occupied by a live graph — the
--                                ownership guard tripped). Operator-review
--                                residual, per the #2304 scoping verifier P1.
--
-- Pure additive: existing rows (status='deleted', NULLs) are the LEGACY
-- tombstone class. Reversible: ALTER TABLE DROP COLUMN (all three).
--
-- Owner: epistemic-team. Issue: #2304 (scoping docs/scoping-2304-delete-semantics.md).
-- ============================================================================
ALTER TABLE public.graphs
    ADD COLUMN IF NOT EXISTS deleted_at timestamptz,
    ADD COLUMN IF NOT EXISTS purged_at timestamptz,
    ADD COLUMN IF NOT EXISTS purged_residual boolean NOT NULL DEFAULT false;

-- Surface the new columns to the same read grant set as the other management
-- metadata (PostgREST seam reads deleted_at for the trash list / purge).
GRANT SELECT (id, team_id, name, kind, namespace, status, recording,
             created_at, deleted_at, purged_at, purged_residual)
    ON public.graphs TO authenticated;
