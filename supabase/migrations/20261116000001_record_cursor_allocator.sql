-- ============================================================================
-- Migration 20261116000001: the record's ordinal ALLOCATOR (#12894, stage C).
-- ----------------------------------------------------------------------------
-- WHAT THIS ADDS. `public.record_cursor` — one row per graph holding the LAST
-- journal position the record has allocated. The record insert and the
-- allocation land in ONE transaction, so `derived = replay(journal)` stops
-- depending on a file's read order and the journal's ordinal is CARRIED by the
-- record instead of derived by replay.
--
-- THE RULING THIS IMPLEMENTS, and its boundary. `#9190` was reopened by owner
-- ruling (2026-11-15, comment 6902871297): *"the record allocates the ordinal
-- and the journal record CARRIES it; replay READS the number instead of
-- computing it."* It authorises ONE thing. It does NOT authorise `:GraphEvent`
-- to change coordinate (issued cursors are `:GraphEvent.seq`) and it does NOT
-- authorise any bound on `journal_event` — which the reviews established is the
-- only carrier of `Object.status`/`Subject.status`. Neither is touched here.
--
-- WHY A COUNTER ROW AND NOT `nextval()`. A sequence is not transactional: a
-- rollback burns the value, and allocation order is not commit order, so two
-- committers can publish 6 before 5. The row lock held to COMMIT is what makes
-- allocation order equal commit order, and the increment rolls back with the
-- transaction (no position burned, none reused-after-being-seen).
--
-- ⛔ `seq >= 0`, NOT `seq > 0` — this is a corrected signature, not a typo.
-- `journal_event` keeps `CHECK (seq > 0)` because a POSITION is always >= 1.
-- The CURSOR is the LAST ALLOCATED position, and "none allocated yet" must be
-- representable: `GREATEST(MAX(journal_event.seq), <enumeration floor>)` is
-- `0` for a brand-new graph, and `CHECK (seq > 0)` would make the emptiest
-- graph in the system — the one the cutover is easiest for — raise `23514` on
-- its own seed. The reviews named this as a blocking defect (round 2, B1), so
-- the constraint is stated against the value the seed actually writes.
--
-- ⛔ THE COMPOSITE FK IS NOT DECORATION. Every record table carries
-- `FOREIGN KEY (org_id, graph_id) REFERENCES public.graphs (org_id, id)`,
-- because tenancy is checked against the graph the row belongs to in the SAME
-- constraint. A counter row without it could outlive its graph and be reused by
-- a new graph with the same id.
--
-- ⛔ THE APP ROLE MAY UPDATE THIS TABLE, AND ONLY THIS ONE. It is the first
-- record table with an UPDATE grant (`journal_event` is deliberately
-- SELECT+INSERT only). The rationale is mechanical: an allocator that cannot
-- UPDATE cannot allocate. DELETE stays withheld — the counter is never erased
-- per-row; it goes with the graph via the FK's ON DELETE CASCADE, which is the
-- tenant-erasure path.
--
-- Re-apply safe by construction: `CREATE TABLE IF NOT EXISTS`, `DROP … IF
-- EXISTS` before every `CREATE POLICY`, and `CREATE INDEX IF NOT EXISTS`. It is
-- listed in `tests/test_9078_record_written_by_production_path.py`'s
-- `RECORD_REAPPLY_STEPS` and in `record_projection.RECORD_MIGRATIONS`, and the
-- two directions of that registry are pinned by `test_9231`.
-- ============================================================================

CREATE TABLE IF NOT EXISTS public.record_cursor (
    org_id     text   NOT NULL,
    graph_id   text   NOT NULL,
    --: The LAST allocated journal position. 0 = nothing allocated yet (see the
    --: header: an empty graph seeds at 0, and that must be representable).
    seq        bigint NOT NULL DEFAULT 0 CHECK (seq >= 0),
    --: The allocator's state. `seeded` and `halted` BOTH mean "nobody
    --: allocates" and carry OPPOSITE remedies, so the mode is a column rather
    --: than the presence of a row: a writer that picks the wrong one silently
    --: moves a graph between "not cut over yet" and "stopped after a defect".
    mode       text   NOT NULL DEFAULT 'seeded'
                      CHECK (mode IN ('seeded', 'active', 'halted')),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (org_id, graph_id),
    FOREIGN KEY (org_id, graph_id) REFERENCES public.graphs (org_id, id) ON DELETE CASCADE
);
COMMENT ON TABLE public.record_cursor IS
    'One row per graph: the LAST journal position the RECORD allocated (#12894). The increment is transactional and the row lock is held to COMMIT, so allocation order equals commit order; a rollback restores the counter. mode: seeded = not cut over, active = allocating, halted = stopped after a defect (counter kept). NOT a bound on journal_event, and it does not move :GraphEvent.seq.';
COMMENT ON COLUMN public.record_cursor.seq IS
    'Last allocated position; 0 means none allocated yet. Deliberately `>= 0` while journal_event requires `> 0`: an empty graph seeds at GREATEST(0,0).';

-- `authenticated` reads, exactly like every other record table (#7968's shape).
ALTER TABLE public.record_cursor ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS record_cursor_read_org ON public.record_cursor;
CREATE POLICY record_cursor_read_org ON public.record_cursor
    FOR SELECT TO authenticated
    USING (org_id = current_setting('app.current_org_id', true));
DROP POLICY IF EXISTS record_cursor_service_role_all ON public.record_cursor;
CREATE POLICY record_cursor_service_role_all ON public.record_cursor
    FOR ALL TO service_role
    USING (true) WITH CHECK (true);
REVOKE ALL ON public.record_cursor FROM anon, authenticated, public;
GRANT SELECT ON public.record_cursor TO authenticated;

-- The tenancy boundary (#9554 / R6). `USING` AND `WITH CHECK` read the SAME
-- predicate as every other record table — the census in
-- `tests/test_record_tenancy_guc.py` derives its list from `THE_RECORD_TABLES`,
-- and this file is that census's source for this table.
--
-- ⛔ `current_setting(..., true)`: without the `true` a missing GUC raises
-- instead of returning NULL. The seed therefore runs through
-- `record_projection.scope_connection` (which SETS the GUC and READS IT BACK)
-- before it touches this table — a seed on a connection with no tenant raises
-- `42501` (RLS WITH CHECK), which is a configuration refusal, not a
-- divergence to be repaired by re-seeding.
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tortoise_record_app') THEN
        -- The tenancy migration creates it; a scratch database migrated by a
        -- hand-picked subset may not have run that file. Fail closed rather
        -- than silently skipping the grant: a table with no app-role policy is
        -- a tenancy hole the census cannot see.
        RAISE EXCEPTION 'tortoise_record_app does not exist — apply 20261025000001_record_tenancy_role_and_guc.sql before this file';
    END IF;
END $$;

GRANT SELECT, INSERT, UPDATE ON public.record_cursor TO tortoise_record_app;

DROP POLICY IF EXISTS record_cursor_app_tenant ON public.record_cursor;
CREATE POLICY record_cursor_app_tenant ON public.record_cursor
    FOR ALL TO tortoise_record_app
    USING (org_id = current_setting('app.current_org_id', true))
    WITH CHECK (org_id = current_setting('app.current_org_id', true));

-- ── The byte meter's EXECUTE grant, for the role that can actually write ────
-- ⛔ NOT DECORATION, and not this change's own table. `20261114000001` grants
-- EXECUTE on the meter to `service_role` ONLY, while
-- `record_projection.scope_connection` REFUSES `service_role` (it is BYPASSRLS)
-- — so on the only role allowed to write the record, the meter raised
-- `InsufficientPrivilege`, returned `None` (TOTAL by contract), and the org was
-- charged **0 bytes** for a write that stored real rows.
--
-- MEASURED on a container migrated by this tree:
--   has_function_privilege('tortoise_record_app',
--     'public.journal_event_bytes_for_rows(text,text,bigint[])', 'EXECUTE')  -> false
--   has_function_privilege('service_role', (same))                          -> true
--
-- It is needed HERE because #12894's falsifier — "the byte meter charges the
-- written bytes and not zero" — cannot pass on a production-shaped connection
-- without it (#12762's artifact). The guard keeps this file applyable on a
-- database where the metering migration has not run (the record schema and the
-- metering schema are applied by different steps).
DO $$
BEGIN
    IF to_regprocedure(
            'public.journal_event_bytes_for_rows(text,text,bigint[])') IS NOT NULL THEN
        GRANT EXECUTE ON FUNCTION public.journal_event_bytes_for_rows(
                text, text, bigint[]) TO tortoise_record_app;
    ELSE
        -- ⛔ A SKIP HERE IS NOT SILENT, because a silent one is the FAIL-OPEN this
        -- guard would otherwise become: the grant is absent, the meter returns
        -- `None` (TOTAL by contract), and the org is charged 0 bytes with no
        -- error anywhere. The NOTICE names WHICH grant is missing and WHY, so a
        -- reader of a migration log is not left to discover it as a zero-byte
        -- meter three assertions away. It cannot be an error: the record schema
        -- and the metering schema are applied by different steps, and this file
        -- must stay applyable where the metering migration has not run (the
        -- statement order is the deploy's, not this file's, to decide).
        RAISE NOTICE 'record_cursor_allocator: byte meter absent, so the EXECUTE '
            'grant to tortoise_record_app was SKIPPED. Apply the metering '
            'migration (20261114000001) and RE-APPLY this file, or every armed '
            'record write will be charged 0 bytes by a meter that cannot run.';
    END IF;
END $$;
