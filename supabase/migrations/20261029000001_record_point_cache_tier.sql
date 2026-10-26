-- #10017 R2 — declarative cache tier for `public.point`
--
-- §9.1 (D1) places raw turns OUTSIDE the graph (owner, 2026-09-23: "the same
-- rule applies to raw turns … both of the two non-entity tiers live outside the
-- graph"), and #10000 stops the capture writer minting turn nodes on a
-- record-armed deployment. The RECORD, however, still holds those turns as
-- `public.point` rows (that is what `record_read` serves).
--
-- `cache_projection.read_record_view` reads every row of every node table and
-- `record_plan` plans a `MERGE` for each — so without a tier, `rebuild_from_record`
-- would re-create the very turn nodes the journal rebuild correctly skips: two
-- rebuild routes, two different graphs.
--
-- The tier is declared in the DATA, not in a reader: a predicate in one reader
-- is the "hide, don't delete" shape #10017's ruling rejected, because the next
-- reader written (an export, a second projection, a sibling tool) would not
-- inherit it. `'graph'` is the default, so every existing row keeps exactly the
-- behaviour it has today; the column is inert until a writer sets
-- `'record_only'`.
--
-- ⚠️ `cache_tier` is ALSO added to the schema's own `props_no_derived` refusal
-- below. `cache_projection._props_from_row` has three destinations and "no
-- fourth": a column it can neither place nor refuse raises `UnknownRecordColumn`
-- and would break cache projection for every tenant. The refusal is a PLACEMENT
-- declaration (this column is not graph authority), not a claim that the value
-- is derived.

-- 1. The tier column.
ALTER TABLE public.point
  ADD COLUMN IF NOT EXISTS cache_tier text NOT NULL DEFAULT 'graph';

-- ONE statement, as `20261014000001` does and for the same reason: a per-
-- statement apply that stops between a DROP and its ADD leaves the table with NO
-- tier refusal at all (an `ALTER` batch is atomic only as a whole), and here it
-- would also mean two ACCESS EXCLUSIVE acquisitions on the record's
-- highest-volume table instead of one.
ALTER TABLE public.point
  DROP CONSTRAINT IF EXISTS chk_point_cache_tier,
  ADD CONSTRAINT chk_point_cache_tier
  CHECK (cache_tier IN ('graph', 'record_only'));

COMMENT ON COLUMN public.point.cache_tier IS
  'Which store this row is for. ''graph'' = the record and the cache both hold '
  'it. ''record_only'' = §9.1 tier: the record holds it, the graph must not '
  '(owner 2026-09-23; #10017 R2). The cache projection skips record_only rows, '
  'so rebuild_from_record and the journal rebuild agree.';

-- 2. Refuse it as a `props` key — a forward re-declaration of the SAME
--    constraint (append-only history: the CREATE TABLE is not edited), carrying
--    the full set, since this REPLACES the table's refusal rather than adding to
--    it at the SQL level. The list below was CUT FROM `refused_props_names()`;
--    nothing re-derives it at apply time, and it cannot be derived: the SQL
--    REPLACES while the parser UNIONS, so the two can drift in one direction —
--    the parser keeps reporting a name the database stopped refusing. The pin in
--    `tests/test_10017_cache_tier.py` (which now unions EVERY earlier
--    re-declaration, not just the CREATE) is what keeps it honest. Do not read
--    this list as self-maintaining.
-- The ADD below VALIDATES existing rows. Before this migration `cache_tier` was
-- not a column, not in `_META_KEYS` and not refused, so a caller-supplied prop
-- of that name went into the props tail — and a single such row aborts the whole
-- migration (`check constraint … is violated by some row`), taking the column
-- and the index with it. The sibling `20261014000001` (the `subject` refusal)
-- established the remedy: remove the name from the tail first. Idempotent, so
-- re-applying it (this file is in RECORD_REAPPLY_STEPS) is a no-op.
UPDATE public.point
   SET props = props - 'cache_tier'
 WHERE props ? 'cache_tier';

-- ONE statement again, and here the gap is worse than redundant work: between
-- the two halves the table accepts `content_hash`, `ep_alpha`, `c_cal` … while
-- `refused_props_names()` goes on reporting them refused from the FILES — and a
-- concurrent write landing in that window makes the re-ADD validate and abort
-- with the very error this file's backfill exists to prevent.
ALTER TABLE public.point
  DROP CONSTRAINT IF EXISTS chk_point_props_no_derived,
  ADD CONSTRAINT chk_point_props_no_derived
  CHECK (NOT (props ?| ARRAY[
    '_graph_id', '_nid', 'baseline_set', 'c_cal', 'cache_tier', 'confidence',
    'content_hash', 'ep_alpha', 'ep_beta', 'ep_dirty', 'ep_dirty_at',
    'inherited_at', 'lastDreamedAt', 'posterior_alpha', 'posterior_beta',
    'reason', 'version'
  ]::text[]));

-- 3. The read this column exists for: a tenant's graph-tier points.
--
-- ⛔ THE READER'S PREDICATE IS SPELLED `cache_tier = 'graph'`, NOT
-- `<> 'record_only'`, AND THIS INDEX IS WHY. PostgreSQL uses a partial index
-- only where the query's predicate IMPLIES the index predicate, so `<>` cannot
-- use this one. Measured on postgres:16 against a throwaway copy of the record
-- schema (2 graph rows, 1 record_only row, one tenant):
--
--   `= 'graph'`         -> Index Scan using idx_point_graph_tier   (no Filter)
--   `<> 'record_only'`  -> Index Scan using idx_point_scope
--                          + Filter: (cache_tier <> 'record_only')
--
-- i.e. the `<>` spelling reads every row of `public.point` — the record's
-- highest-volume table — and discards the record_only ones, which is exactly the
-- population this index exists to skip. The peer-reviewed claim that the index
-- was unjustified (`SELECT *` cannot go index-only) was right about the covering
-- question and wrong about the scan: the index narrows the scan, which is the
-- win. An earlier draft of this branch dropped the index; the measurement above
-- is why it is back.
CREATE INDEX IF NOT EXISTS idx_point_graph_tier
  ON public.point (org_id, graph_id, node_key)
  WHERE cache_tier = 'graph';
