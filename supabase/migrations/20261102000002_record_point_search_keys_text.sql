-- ============================================================================
-- Migration 20261102000002: `public.point.search_keys` is TEXT — the graph's
-- own flat string (#10620, epic #7879 · R4's remaining half)
-- ----------------------------------------------------------------------------
-- `public.point.search_keys` was declared `jsonb` (the table's blanket type for
-- its additive tail, `20261012000003_record_epistemic.sql`) while every producer
-- of it emits the SPACE-JOINED STRING the graph's fulltext index requires:
--
--   * `projection/entities.py::flatten_search_keys_prop` — the CANONICAL
--     flattener, which turns a list-valued `search_keys` into
--     `" ".join(...)` precisely because FalkorDB's `("Point", ["content",
--     "search_keys"])` index does NOT index array-valued properties (verified on
--     server v4.16.7). Every write path runs it before the node is written;
--   * `PointAdded` / `PointPromoted` journal snapshots — the record's ONLY input
--     — therefore carry the flattened STRING as the property;
--   * `sparse.py` states the contract in words: *"writes `search_keys` as a flat
--     space-joined STRING … raw graph values may still be arrays"*.
--
-- `record_projection._jsonb_or_scalar` deliberately refuses to coerce (a
-- journal/record disagreement must stay visible), and Postgres's `jsonb` input
-- parser rejects a bare string — so EVERY point record carrying search keys was
-- refused with:
--
--   psycopg2.errors.InvalidTextRepresentation:
--   invalid input syntax for type json
--   DETAIL:  Token "err4032" is invalid.
--
-- one `record_write_failures{reason="write_failed"}` per record, the ROW DROPPED,
-- and the whole `relation` layer starved with it (its endpoints are those same
-- point rows). Measured on the hosted deployment: 32 at ~14:24Z → **302 by
-- ~19:17Z**, with `/health` reporting `ok: true` throughout (#10620).
--
-- WHICH SIDE IS AUTHORITATIVE: the PRODUCER's form, and the column narrows to it
-- — the same ruling, and the same reasoning, as `20261022000001`'s
-- `session.capture_redactions` (`jsonb` → `integer`). `jsonb` here was a blanket
-- choice for the table's additive tail, not a decision about this field:
--
--   * NOTHING reads the record's `search_keys` as a jsonb structure. Measured by
--     `rg -n 'search_keys' tortoise/record_*.py` at this head: the read paths
--     (`record_read.py`, `record_serve.py`, `record_restore.py`,
--     `record_reconcile.py`, `record_backup.py`, `record_text.py`) contain ZERO
--     references — not a single `?`, `@>`, `->>` or `jsonb_array_elements`
--     against it. It is carried, never queried.
--   * The graph is the source of truth for the VALUE, and its form is the flat
--     string. A `jsonb` column able to hold only the flat string is the SECOND
--     representation of one datum in two stores — which is how this bug happened.
--   * The alternative (keep `jsonb`, serialize the string as a JSON SCALAR) was
--     rejected for that reason: it makes the record hold `"err4032 release"` —
--     a JSON string literal — where the graph holds `err4032 release`. Every
--     future reader would have to know to unwrap it (`#>> '{}'`), and the record
--     would still be *unable* to accept the array form the graph can hand it.
--
-- `flatten_search_keys_prop` remains the ONLY flattener in the WRITE path: this
-- migration does not introduce a second one. The `USING` below is bootstrap
-- arithmetic on rows that already exist, and for an ARRAY it applies the SAME
-- rule (per-element `strip`, blank elements dropped, joined by one space, order
-- preserved — see the function body) so a value that predates the ruling lands
-- in the one canonical form rather than being dropped or reshaped into a second
-- one.
--
-- ⛔ WHERE THE CONVERSION IS DELIBERATELY BROADER THAN THE WRITE PATH
-- (review P3, #10648). The array arm is the write path's rule exactly; the
-- OBJECT / NUMBER / BOOLEAN arms are not, and the difference is stated here
-- rather than left for a reader to reconcile:
--
--   * `_record_column_value("point", "search_keys", <dict>)` REFUSES
--     (`RecordProjectionError`: the graph leaves a dict as-is, and `text` cannot
--     hold it), so no NEW row can arrive as an object. A pre-existing one can
--     only have come from the old `_jsonb_or_scalar` path (which `json.dumps`ed
--     a list) or from a hand-written row — and for those the choice is between
--     keeping the value's text and DROPPING the value. This file keeps it
--     (`$1::text`, the jsonb rendering) because a bootstrap that silently
--     discards data is the failure mode this migration exists to end; the write
--     path's refusal is what keeps that lenience from becoming a second
--     representation going forward.
--   * `number` / `boolean` are the same trade one step milder: their text form
--     (`42`, `true`) is a value, and `ELSE NULL` would have dropped it. The write
--     path never produces them either (a bool bound to a `text` column is
--     `42804`), so again the arms only ever fire on rows that already exist.
--
-- The arms are therefore converted-and-preserved, not converted-and-shaped, and
-- `tests/test_record_searchkeys_10620.py` executes each one (plus the writer's
-- refusal of the object shape) so a rewrite of any arm goes red.
--
-- A FORWARD migration, never an edit of 20261012000003: migrations are
-- append-only history (the `check-migration-append-only` gate refuses an edit to
-- a landed file).
--
-- LOCK AND TIME COST. `jsonb` → `text` is not binary-coercible, so this is a
-- full table REWRITE: `ALTER TABLE` takes an ACCESS EXCLUSIVE lock on
-- `public.point` for the duration. At the deployment this fixes, `public.point`
-- holds a few hundred rows (302 refused writes are the reason it is that small),
-- so the rewrite is sub-second. It is NOT safe to run unplanned once the table
-- is large — at ~10^6 rows expect seconds-to-minutes of write unavailability.
-- Above that, the two-step expand/contract (add `search_keys_text`, backfill in
-- batches, cut over, drop) is the correct shape; it is deliberately NOT used
-- here because a `DROP COLUMN` on the record's only copy of a point's search
-- keys is a data-loss path, and a small table does not earn that risk.
--
-- REVERSIBLE. The `Down` block at the foot of this file restores `jsonb` with
-- `to_jsonb(...)` and loses NO value: the flattened string comes back as a JSON
-- string. What it does NOT restore is the ARRAY SHAPE of a pre-existing array —
-- it comes back as the joined string, which is the graph's canonical form and
-- the value it already meant. Both directions were run against a throwaway
-- `postgres:16` (#10620; the up conversion over NULL / JSON-string / JSON-array
-- / JSON-object rows, then the down, then the up again).
--
-- REHEARSAL NOTE. `USING` cannot contain a subquery ("cannot use subquery in
-- transform expression", measured), so the array arm needs the helper function
-- below. It is created and dropped inside this file, so it never becomes part of
-- the schema an operator sees; `public` is required (not `pg_temp`) because the
-- migration runner's session is not ours to assume.
-- ============================================================================

CREATE OR REPLACE FUNCTION public.record_flatten_search_keys(value jsonb)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    -- One datum, one form: the graph's flat space-joined string, by the SAME
    -- rule `projection/entities.py::flatten_search_keys_prop` (and its restatement
    -- at `record_projection._record_column_value`) applies per element:
    --
    --     " ".join(str(s).strip() for s in value if str(s).strip())
    --
    -- `string_agg(element, ' ')` alone is NOT that rule: it keeps a blank
    -- element (so `["a", "", "b"]` would land as `a  b`, a double space) and
    -- it does not trim (so `["  x  "]` would land as `  x  `). Either is a
    -- SECOND shape of the datum, which is the defect this migration exists to
    -- remove — the record is `replay(journal)` and `rebuild_from_record` copies
    -- this column back into the graph. So each element is trimmed and blanks are
    -- DROPPED; the character set is Python's ASCII whitespace, and a non-ASCII
    -- whitespace element is the one residual (`str.strip()` is Unicode-aware,
    -- `btrim` here is explicit). With no surviving element the aggregate is NULL,
    -- which is the graph's own "no value" state (its flattener POPS a blank key).
    SELECT CASE jsonb_typeof($1)
               WHEN 'string' THEN $1 #>> '{}'
               WHEN 'array'  THEN (
                   SELECT string_agg(btrim(element, E' \t\n\r\v\f'), ' '
                                     ORDER BY ordinality)
                     FROM jsonb_array_elements_text($1)
                          WITH ORDINALITY AS t(element, ordinality)
                    WHERE btrim(element, E' \t\n\r\v\f') <> ''
               )
               WHEN 'object' THEN $1::text
               -- A number/boolean is not a search-key set, but it IS a value:
               -- its own text form, never a silent NULL. A jsonb `null` is the
               -- absence of a value (nothing to preserve), so it is the ONE arm
               -- that maps to SQL NULL.
               WHEN 'boolean' THEN $1 #>> '{}'
               WHEN 'number' THEN $1 #>> '{}'
               ELSE NULL
           END
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = 'point'
           AND column_name = 'search_keys'
           AND data_type = 'jsonb'
    ) THEN
        ALTER TABLE public.point
            ALTER COLUMN search_keys TYPE text
            USING public.record_flatten_search_keys(search_keys);
    END IF;
END $$;

DROP FUNCTION IF EXISTS public.record_flatten_search_keys(jsonb);

COMMENT ON COLUMN public.point.search_keys IS
    'The point''s retrieval keys as ONE space-joined STRING (#10620) — the same '
    'form the graph stores and its fulltext index requires '
    '(projection/entities.py::flatten_search_keys_prop is the only flattener). '
    'Declared TEXT, not jsonb: the producers emit a string, nothing reads the '
    'record''s copy as a JSON structure, and a jsonb column refused every record '
    'that carried one (302 rows dropped while /health read ok).';

-- ============================================================================
-- Down (run by hand; migrations are forward-only here). Verified on a throwaway
-- `postgres:16` in #10620 — the column goes back to `jsonb` holding a JSON
-- string, and the up migration applies again cleanly afterwards:
--
--   ALTER TABLE public.point
--       ALTER COLUMN search_keys TYPE jsonb
--       USING to_jsonb(search_keys);
--
--   COMMENT ON COLUMN public.point.search_keys IS NULL;
--
-- It preserves every VALUE and does not restore an array's shape (see
-- REVERSIBLE above). It is NOT runnable by the migration runner, by design.
-- ============================================================================
