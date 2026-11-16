-- ============================================================================
-- Migration 20261116000002: the byte meter's EXECUTE grant (#12894, stage C).
-- ----------------------------------------------------------------------------
-- WHY THIS IS ITS OWN STEP, AND NOT THE TAIL OF `20261116000001`. It used to
-- live at the end of `20261116000001_record_cursor_allocator.sql`, and in that
-- position it was UNREACHABLE on exactly the containers that need it:
-- `_ensure_record_schema` re-applies `20261116000001` only where
-- `public.record_cursor` is ABSENT (`creates`-conditional — the file's
-- `CREATE POLICY`s and its `GRANT`s take an ACCESS EXCLUSIVE lock on the
-- counter table, and re-taking it inside a live allocator transaction is the
-- #12399 deadlock). So after the FIRST apply — which is the only apply a
-- container ever does, and which normally happens BEFORE the metering
-- migration exists — the allocator file is skipped forever, and a grant at its
-- tail can never fire.
--
-- ⛔ MEASURED, on the required zero-skip `test-record-postgres` leg: the live
-- suite's fixture applies the record schema, then the metering schema, then the
-- record schema again, and expected the third call to reach that grant. It
-- could not. `has_function_privilege('tortoise_record_app',
-- 'public.journal_event_bytes_for_rows(text,text,bigint[])', 'EXECUTE')` stayed
-- `false`, the meter returned `None` (TOTAL by contract), the org was charged
-- **0 bytes** for a write that stored real rows, and the leg reddened on
-- `assert granted is True`.
--
-- So the grant is a SEPARATE, ALWAYS-APPLIED step: it creates no table, takes no
-- lock on `public.record_cursor`, and is idempotent by construction (`GRANT` on
-- an already-granted privilege is a no-op). It is listed in
-- `tests/test_9078_record_written_by_production_path.py`'s
-- `RECORD_REAPPLY_STEPS` with `creates=None` for that reason, and in
-- `record_projection.RECORD_MIGRATIONS` (the two directions of that registry
-- are pinned by `test_9231`).
--
-- WHY THE GRANT EXISTS AT ALL. `20261114000001` grants EXECUTE on the meter to
-- `service_role` ONLY, while `record_projection.scope_connection` REFUSES
-- `service_role` (it is BYPASSRLS) — so on the only role allowed to write the
-- record, the meter raised `InsufficientPrivilege`, returned `None` (TOTAL by
-- contract), and the org was charged **0 bytes** for a write that stored real
-- rows.
--
-- MEASURED on a container migrated by this tree:
--   has_function_privilege('tortoise_record_app',
--     'public.journal_event_bytes_for_rows(text,text,bigint[])', 'EXECUTE')  -> false
--   has_function_privilege('service_role', (same))                          -> true
--
-- It is needed because #12894's falsifier — "the byte meter charges the written
-- bytes and not zero" — cannot pass on a production-shaped connection without
-- it (#12762's artifact). The guard keeps this file applyable on a database
-- where the metering migration has not run (the record schema and the metering
-- schema are applied by different steps).
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
        RAISE NOTICE 'record_cursor_meter_grant: byte meter absent, so the '
            'EXECUTE grant to tortoise_record_app was SKIPPED. Apply the '
            'metering migration (20261114000001) and RE-APPLY this file, or '
            'every armed record write will be charged 0 bytes by a meter that '
            'cannot run.';
    END IF;
END $$;
