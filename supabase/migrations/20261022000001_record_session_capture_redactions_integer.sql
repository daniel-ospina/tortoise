-- ============================================================================
-- Migration 20261022000001: `public.session.capture_redactions` is an INTEGER
-- count (#9264, epic #7879)
-- ----------------------------------------------------------------------------
-- `public.session.capture_redactions` was declared `jsonb` while every producer
-- of it emits an INTEGER COUNT:
--
--   * `hosted_api._capture_session_impl` — `_capture_redactions =
--     _capture_write["redacted_total"]`, carried on the capture receipt as
--     `"capture_redactions": <int>`; both sites are inside that one function
--     and are cited by SYMBOL, which `STORAGE-ARCHITECTURE.md` §11.1 makes the
--     authoritative form — a `file.py:NNNN` here drifted by 67 lines in the
--     merge that carried this migration to its landing base;
--   * `session_schema` — an additive INTEGER property on the Session lane
--     (`tortoise/session_schema.py:157,201`);
--   * `security.redact` — the count it reports ("records on the Session
--     (`capture_redactions`)", `tortoise/security.py:1215`).
--
-- Measured on a throwaway `postgres:16-alpine` carrying only the record
-- migrations, through the PRODUCTION path (`record_writer.write_live_record`):
--
--   psycopg2.errors.DatatypeMismatch: column "capture_redactions" is of type
--   jsonb but expression is of type integer
--   LINE 1: ..., 'session_7bbf237bf5e1', 'session_7bbf237bf5e1', 0, '{}') O...
--   HINT:  You will need to rewrite or cast the expression.
--
-- `record_projection._jsonb_or_scalar` deliberately refuses to coerce (a
-- journal/record disagreement must stay visible), and the refusal is per
-- STATEMENT, so the WHOLE `SessionRecorded` row never reaches the record: every
-- captured session's redaction count was lost, and the capture counted one
-- `record_write_failures{reason="write_failed"}` per capture while its receipt
-- still reported `ok: True`.
--
-- WHICH SIDE IS AUTHORITATIVE: the record's declared shape, narrowed to the
-- producer's own type — and the anomaly is unambiguous here. Every OTHER counter
-- on `public.session` is already `integer` (`turn_count`, `entity_links_attempted`,
-- `entity_links_created`, `commit_count`, `draft_count`, `value_nodes_created`,
-- `value_nodes_held`), so `jsonb` was a blanket choice for the table's additive
-- tail, not a decision about this field. The count stays a count: wrapping it
-- as an object (`{"count": N}`) would make the record's cheapest query —
-- sum the redactions for a tenant — a JSON extraction, the same reason the
-- money amount is a `bigint` column rather than a `props` key.
--
-- A FORWARD migration, never an edit of 20261012000001: migrations are
-- append-only history (the `check-migration-append-only` gate refuses an edit to
-- a landed file). `USING` converts the shapes a `jsonb` column could already
-- hold — a bare integer, or an object carrying the count — so a database that
-- applied the earlier migration is not left unreadable. Each arm is GATED on the
-- value literally being an integer literal (`~ '^-?[0-9]+$'`): a non-integral
-- number such as `3.7`, a word, or a nested object becomes NULL instead of
-- aborting the ALTER (a bare cast would raise `invalid input syntax for type
-- integer`) and instead of a fabricated count, because a redaction total is not
-- reconstructible from the structure. An integral literal OUTSIDE the int4 range
-- still aborts (`value "3000000000" is out of range for type integer`): the arm
-- fails closed rather than clamp, and no row can hold such a value today because
-- every producer write was refused with `DatatypeMismatch`. The table
-- is empty in every deployment that has not armed the record
-- (`TORTOISE_RECORD_DSN`, #9548), so this is a shape correction, not a data
-- migration.
--
-- The `DO` guard makes it idempotent on re-apply: the ALTER is a no-op once the
-- column is no longer `jsonb`.
--
-- The schema readers that must see the narrowed type: none of them parse
-- `ALTER COLUMN … TYPE` (`record_projection.table_columns()` and
-- `tests/test_record_schema_placement.parse_sql_columns()` both read
-- `CREATE TABLE` / `ADD COLUMN`), and nothing else needs to — the projection
-- passes a non-`dict`/`list` value through unchanged, so the column's type was
-- the only thing disagreeing. The file is listed in `RECORD_MIGRATIONS` and in
-- `tools/ci_selection.py`'s `CORE_ALSO` (it sorts outside the `2026101200000`
-- prefix), so a change to it ALONE still selects `core` and runs both pins.
-- ============================================================================

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'session'
           AND column_name = 'capture_redactions'
           AND data_type = 'jsonb'
    ) THEN
        ALTER TABLE public.session
            ALTER COLUMN capture_redactions TYPE integer
            USING (
                CASE
                    WHEN jsonb_typeof(capture_redactions) = 'number'
                         AND capture_redactions::text ~ '^-?[0-9]+$'
                        THEN (capture_redactions::text)::integer
                    WHEN jsonb_typeof(capture_redactions) = 'object'
                         AND capture_redactions ->> 'count' ~ '^-?[0-9]+$'
                        THEN (capture_redactions ->> 'count')::integer
                    ELSE NULL
                END
            );
    END IF;
END $$;

COMMENT ON COLUMN public.session.capture_redactions IS
    'How many redactions the capture pass applied to this session (#9264). An '
    'INTEGER count — the receipt''s `capture_redactions` key and the Session '
    'lane''s additive property are the same number, never an object.';
