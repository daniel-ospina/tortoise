-- ============================================================================
-- Migration 20261018000001: the money value is a `public.point` column (#2782,
-- epic #7879)
-- ----------------------------------------------------------------------------
-- `tortoise/money.py` (PR #8641) fixed the money SHAPE — `amountMinor` (integer
-- minor units), `currency` (ISO 4217, per amount), `minorUnitExponent`,
-- `verbatim` (the exact source text) and `asOf` — and #2782 declares those five
-- on the Point lane's accepted schema (`_POINT_DECLARED_PROPS`, single-sourced
-- from `MONEY_VALUE_FIELDS`). The placement table
-- (`docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md`,
-- the appendix's Point section) now carries the five rows, all class **P**:
-- they are author-supplied, non-reconstructible values carried by
-- `PointAdded.point.<field>` — nothing recomputes them from the structure.
--
-- §17.5's field contract is two-way, and `tests/test_record_schema_placement.py`
-- enforces both halves. `PRIMARY` ⇒ the record must HAVE a column (the record
-- cannot be poorer than the history — Interface A's failure mode). Left as the
-- open-set `props` tail, an amount would be a key-typed, unvalidated JSON
-- scalar where the contract demands an integer, and the query #2782's own
-- target (design §7.2 step 5: "how much is committed / released to date?" — a
-- SUM and a RATIO) would have to extract and re-cast it per row. The five
-- columns are what makes `total_minor` answerable from the record.
--
-- A FORWARD migration, never an edit of 20261012000003: migrations are
-- append-only history (the `check-migration-append-only` gate refuses an edit
-- to a landed file), and #8179 had already merged when #2782 was filed. The
-- pattern — and the schema readers that must see it — are #8263's.
--
-- `bigint` for the amount and `integer` for the exponent are the point of the
-- shape: money is never a float (design §1/D1 — binary floating point cannot
-- represent 0.10), so the column type carries the rule, not a CHECK.
--
-- The schema readers that must SEE these columns (a `CREATE TABLE`-only read
-- would report them missing):
--   * `tortoise/record_projection.py` → `table_columns()` (the projection's
--     column-vs-`props`-tail split, and `cache_projection.projected_columns()`);
--   * `tests/test_record_schema_placement.py` → `parse_sql_columns()` (the
--     §17.5 two-way pin).
-- Both parse the `ALTER TABLE … ADD COLUMN` form; the file is listed in
-- `RECORD_MIGRATIONS` and in `tools/ci_selection.py`'s `CORE_ALSO` (it sorts
-- outside the `2026101200000` prefix), so a change to this file ALONE selects
-- `core` and runs both pins.
-- `IF NOT EXISTS` per column keeps the statement idempotent on re-apply.
-- ============================================================================

ALTER TABLE public.point
    -- Integer minor units (e.g. R$ 250.000,00 = 25000000 at exponent 2).
    ADD COLUMN IF NOT EXISTS amount_minor bigint,
    -- ISO 4217 alpha-3. Explicit PER AMOUNT — never a graph-level default.
    ADD COLUMN IF NOT EXISTS currency text,
    -- The exponent travels WITH the amount, so a reader never assumes a table
    -- (JPY 0, BRL/USD 2, KWD 3).
    ADD COLUMN IF NOT EXISTS minor_unit_exponent integer,
    -- The exact source text the value was read from (drop, never round).
    ADD COLUMN IF NOT EXISTS verbatim text,
    -- ISO `YYYY-MM-DD` assertion/validity date, distinct from `when`.
    ADD COLUMN IF NOT EXISTS as_of text;

COMMENT ON COLUMN public.point.amount_minor IS
    'The money value in integer minor units (#2782, tortoise/money.py). bigint, never float — binary floating point cannot represent 0.10 (WS-C design §1/D1). Read with currency + minor_unit_exponent, which travel per amount.';
COMMENT ON COLUMN public.point.currency IS
    'ISO 4217 alpha-3 code for amount_minor (#2782) — explicit per amount, never a graph-level default.';
COMMENT ON COLUMN public.point.minor_unit_exponent IS
    'The exponent that scales amount_minor to major units (#2782): 0 JPY, 2 BRL/USD, 3 KWD. Carried with the amount so a reader never assumes a currency table.';
COMMENT ON COLUMN public.point.verbatim IS
    'The exact source text the money value was read from (#2782) — a value that cannot be represented exactly is DROPPED to this column, never rounded into amount_minor.';
COMMENT ON COLUMN public.point.as_of IS
    'ISO YYYY-MM-DD assertion/validity date of the money value (#2782), distinct from `when` (the episodic occurrence time).';
