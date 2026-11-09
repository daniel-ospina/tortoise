-- ============================================================================
-- Migration 20261109000001: the vector index and its payload move to Postgres
--                             (`pgvector`)              (#11642, #4051, #5090)
-- ----------------------------------------------------------------------------
-- THE DECISION THIS IMPLEMENTS, NOT ONE IT MAKES. `docs/architecture/
-- SEARCH-ARCHITECTURE.md` (owner, 2026-11-07, PR #11481) item 1: **the vector
-- index AND the embedding payload move to Postgres** (`pgvector`) — which
-- resolves `STORAGE-ARCHITECTURE.md` §14.3 **V1** (the vector index does NOT
-- live in FalkorDB). Items 2-4 are NOT this file's: the TEXT index stays in
-- FalkorDB (a measured regression, 8-23 nDCG@10 — deferred behind M3), and
-- traversal, writes and EP stay in the graph, so the ~1,900-statement Cypher
-- surface is untouched. Do not read this migration as licence to move any of
-- those.
--
-- WHY THE MOVE IS WORTH A MIGRATION AT ALL, measured rather than asserted:
-- FalkorDB's `vectorset` index is NOT carried in a graph dump, so every cold
-- restore REBUILT it — **436.5 s at 20k nodes, 191.8 s tuned** — and the
-- rebuild DEADLOCKED the instance (`#7846`). After this file there is no
-- in-graph vector index to rebuild: `projection/__init__.py`'s
-- `_ensure_vector_index_api` no longer creates one, and the ANN index is an
-- ordinary Postgres index (page-cached, disk-backed, need not fit in RAM —
-- `pgvector`'s own README, quoted in `STORAGE-ARCHITECTURE.md` §2.1).
--
-- THE SHAPE, AND WHY THE PAYLOAD COLUMN IS NOT CONVERTED
-- ------------------------------------------------------
-- `public.point/source/event.embedding` is `jsonb` today and STAYS `jsonb`.
-- These tables are the record, the projection binds that column verbatim from
-- the journal payload, and `record_read` serves it back — so a
-- `jsonb -> vector(384)` TYPE change would rewrite the record's write and read
-- seams and could not represent a caller-supplied embedding of any other width
-- (PR #3018 stores those VERBATIM). Instead this file adds a **GENERATED
-- ALWAYS AS … STORED** projection column beside it:
--
--   embedding_vec public.vector(384) GENERATED ALWAYS AS
--       (public.record_embedding_vector(embedding)) STORED
--
-- Three properties follow, and each one is a defect that does NOT need fixing:
--
--   1. it CANNOT DRIFT from `embedding` — there is no trigger and no second
--      writer to keep in step, so "the vector column disagrees with the record"
--      is not a reachable state;
--   2. it CANNOT FAIL a write. `public.record_embedding_vector` returns NULL
--      for anything that is not a 384-long all-numeric JSON array, including
--      the `[null, …]` shape a plain `(embedding::text)::vector` cast RAISES on
--      (measured on pgvector 0.8.7: `invalid input syntax for type vector:
--      "[null, null, …]"`, and the raise loses the whole INSERT, not the row).
--      So a non-384 embedding is stored and NOT ann-indexed — exactly the
--      index semantics the graph had (`required_embedding_dim`), with no new
--      failure mode on the record's write path;
--   3. it BACKFILLS ITSELF: `ADD COLUMN … GENERATED … STORED` rewrites the
--      table once and computes every existing row. Measured on 20,000 rows:
--      **6.9 s**.
--
-- ⛔ THE FUNCTION BODY IS SCHEMA-QUALIFIED, AND THAT IS NOT STYLE. `pg_restore`
-- starts every session with an EMPTY `search_path` (pg_dump emits
-- `set_config('search_path', '', false)`), and `CREATE TABLE` re-parses the
-- generation expression through the function body. Measured on a real
-- `pg_dump`/`pg_restore` cycle with an unqualified `vector(384)` inside the
-- body:
--     ERROR: type "vector" does not exist
--     CONTEXT: SQL function "record_embedding_vector" during inlining
--     ... CREATE TABLE public.<t> ...
-- i.e. an unqualified body RESTORES NOTHING — the table is never created and
-- every later statement in the restore fails behind it. Hence
-- `public.vector(384)` in the body AND in the column type, and hence the
-- COMMENT ON below recording why.
--
-- THE INDEX IS `hnsw` + `vector_cosine_ops`, because the leg it replaces
-- scored on COSINE: `db.idx.vector.queryNodes` was created with
-- `similarityFunction: 'cosine'` and `search_engine.run_vector_query` converts
-- its `1 - cosine` distance back to a similarity. `vector_l2_ops` here would
-- silently change the ranking, which is the one thing the issue forbids.
--
-- SIZED AGAINST `maintenance_work_mem` (20,000 x 384 measured on pgvector
-- 0.8.7 / Postgres 16.15):
--   * raw vector bytes: 20,000 x 1,536 B = **30.7 MB**;
--   * `maintenance_work_mem` = 64 MB (the DEFAULT): build **4.0-4.2 s** over
--     three rebuilds, no spill notice. Index 39 MB;
--   * `maintenance_work_mem` = 4 MB: pgvector warns `hnsw graph no longer fits
--     into maintenance_work_mem after 1866 tuples` and the build takes
--     **37.4 s** — 9x slower, and it COMPLETES. It does not OOM.
-- ⇒ the build degrades in TIME, not into a failure, and the rule for operators
-- is `maintenance_work_mem` >= ~2x the raw vector bytes (≈62 MB at 20k,
-- ≈619 MB at 200k) to stay on the fast path. Create the index on a table that
-- already holds the rows (as below) rather than before a bulk load: a
-- maintained index costs a graph insert per row.
-- ============================================================================

-- The extension. `pgvector` is available on Supabase Postgres (the live
-- extensions list carries `vector`; §14.3 V1's decision rests on it) and on the
-- `pgvector/pgvector:pg16` image the CI job uses. `pgvectorscale`/DiskANN is
-- NOT available there, which is why this is HNSW and not DiskANN.
--
-- ⛔ `SCHEMA public` IS STATED, AND THE QUALIFICATION ABOVE DEPENDS ON IT. Every
-- type/opclass reference in this file and in the converter's body is
-- `public.vector…` (that qualification is not optional — see the pg_restore
-- note at the top), so the extension MUST live in `public`:
--   * `IF NOT EXISTS` against an install in ANOTHER schema (Supabase's
--     documented pattern is `create extension vector with schema extensions`)
--     is a silent no-op, after which every `public.vector(384)` below fails to
--     resolve — a deploy that aborts on a TYPE ERROR rather than on a clear
--     sentence;
--   * `SCHEMA public` makes a FRESH install deterministic. It cannot repair an
--     existing install elsewhere, which is why the settling check is stated
--     rather than implied: on a record database
--     `SELECT extnamespace::regnamespace FROM pg_extension WHERE extname='vector'`
--     must answer `public` (measured on `pgvector/pgvector:pg16` — it does).
--     ⚠️ If it answers something else, the fix is to ALIGN the two — move the
--     extension, or re-qualify this file's three references — NOT to drop the
--     clause and rely on `search_path`, which `pg_restore` empties.
CREATE EXTENSION IF NOT EXISTS vector SCHEMA public;

-- ── the one converter: jsonb payload -> indexable vector, NULL when unusable ──
--
-- IMMUTABLE + STRICT + PARALLEL SAFE because a generated column requires all
-- three: IMMUTABLE is the contract (the stored value must depend on the row
-- alone), STRICT makes a NULL `embedding` yield a NULL vector without a
-- `CASE`, and PARALLEL SAFE keeps a parallel table rewrite legal.
--
-- ⛔ The all-numeric test is the whole reason this is a function instead of an
-- inline cast: `jsonb_array_length(...) = 384` passes for a 384-long array of
-- `null`s and strings, and `'[null, …]'::vector` RAISES. Every element is
-- checked, so the value is a vector or it is NULL — never an exception on the
-- record's write path.
CREATE OR REPLACE FUNCTION public.record_embedding_vector(doc jsonb)
RETURNS public.vector(384)
LANGUAGE sql
IMMUTABLE
STRICT
PARALLEL SAFE
AS $$
    SELECT CASE
        WHEN jsonb_typeof(doc) = 'array'
         AND jsonb_array_length(doc) = 384
         AND NOT EXISTS (SELECT 1
                           FROM jsonb_array_elements(doc) AS e
                          WHERE jsonb_typeof(e) <> 'number')
        THEN (doc::text)::public.vector(384)
    END
$$;

COMMENT ON FUNCTION public.record_embedding_vector(jsonb) IS
    'The record''s ONE jsonb-embedding -> vector(384) converter (#11642). Returns NULL for anything that is not a 384-long all-numeric JSON array, so a non-384 or null-bearing embedding is STORED but not ann-indexed instead of raising on the record''s write path. The body schema-qualifies the type (public.vector) because pg_restore runs with an empty search_path and an unqualified body makes CREATE TABLE of the generated column fail with "type vector does not exist" (measured).';

-- ── the three labels whose vector the RECORD itself carries ──────────────────
--
-- Point, Source and Event are the labels whose `embedding` is a record COLUMN
-- (20261012000003/…0001). `public.object` and `public.subject` are NOT given
-- one here, and that is a DECISION with a reason rather than an omission —
-- see the closing comment block.
ALTER TABLE public.point
    ADD COLUMN IF NOT EXISTS embedding_vec vector(384)
    GENERATED ALWAYS AS (public.record_embedding_vector(embedding)) STORED;

ALTER TABLE public.source
    ADD COLUMN IF NOT EXISTS embedding_vec vector(384)
    GENERATED ALWAYS AS (public.record_embedding_vector(embedding)) STORED;

ALTER TABLE public.event
    ADD COLUMN IF NOT EXISTS embedding_vec vector(384)
    GENERATED ALWAYS AS (public.record_embedding_vector(embedding)) STORED;

-- ── the ANN indexes ──────────────────────────────────────────────────────────
--
-- `vector_cosine_ops` on every one of them: the graph index this replaces was
-- COSINE (`similarityFunction: 'cosine'`), and the leg converts its distance
-- back with `1 - d`. A different operator class is a different ranking.
--
-- `m` / `ef_construction` are pgvector's DEFAULTS (16 / 64) and are left
-- unstated on purpose: a non-default value here would be a tuning decision
-- with no measurement behind it, and the measured build above was taken at the
-- defaults.
--
-- ⛔ These are NOT partial indexes, deliberately. A `WHERE embedding_vec IS NOT
-- NULL` predicate looks free and is not: pgvector's HNSW walk needs the
-- predicate satisfied for EVERY entry it visits, and a partial index whose
-- predicate the planner cannot prove makes the index unusable for a query that
-- does not restate it. The NULL rows cost the graph nothing (a NULL is never an
-- entry), so the plain index is both simpler and faster.
CREATE INDEX IF NOT EXISTS idx_point_embedding_vec_hnsw
    ON public.point USING hnsw (embedding_vec public.vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_source_embedding_vec_hnsw
    ON public.source USING hnsw (embedding_vec public.vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_event_embedding_vec_hnsw
    ON public.event USING hnsw (embedding_vec public.vector_cosine_ops);

-- No grant changes: `embedding_vec` rides the tables that already carry the
-- SELECT grant and the RLS policies, and every write goes through the
-- service_role write path (#7951). The column is GENERATED, so `INSERT`/
-- `UPDATE` naming it is refused by Postgres itself — the second lock on the
-- same door as the projection's own refusal to bind it
-- (`record_projection.GENERATED_COLUMNS`).

COMMENT ON COLUMN public.point.embedding_vec IS
    'The ANN-indexed projection of public.point.embedding (#11642, #4051 V1). GENERATED ALWAYS — never written by the record projection, never written by a caller; NULL when the payload is not a 384-long all-numeric array. The HNSW index on it (idx_point_embedding_vec_hnsw) is the leg that replaced CALL db.idx.vector.queryNodes.';
COMMENT ON COLUMN public.source.embedding_vec IS
    'The ANN-indexed projection of public.source.embedding (#11642). See public.point.embedding_vec for the generated-column contract.';
COMMENT ON COLUMN public.event.embedding_vec IS
    'The ANN-indexed projection of public.event.embedding (#11642). See public.point.embedding_vec for the generated-column contract.';

-- ── ⛔ WHY `public.object` AND `public.subject` GET NO VECTOR COLUMN ─────────
--
-- The served set (`ENTITY_TYPE_LABELS` → `tortoise/security.py`) includes
-- `Object` and `Subject`, so this is the one place a reader will look for a
-- gap. It is a decision, stated so the next lane does not re-derive it.
--
-- Their vectors are DERIVED IN THE GRAPH FOLD AND RIDE NO JOURNAL RECORD:
-- `projection/entities.py::_upsert_object` and `_upsert_subject` both call
-- `encode_for_row(name)` and write the result straight onto the node (#7845).
-- Nothing puts that vector in an `ObjectAdded`/`SubjectAdded` payload, so the
-- RECORD — which is a pure fold of the journal, `#5285`'s totality rule, "this
-- content came FROM the record" — has no source for it. Two candidates were
-- available and neither is taken:
--
--   (a) ADD the column. It would be populated by NOTHING — a declared column
--       no writer fills reads as coverage and is worse than an absence. Making
--       it real means either encoding in the projection (a second embedding
--       deriver beside `encode_for_row`, the §12.2a "second rule set" defect)
--       or copying the vector out of the CACHE with a graph→Postgres pass,
--       which INVERTS the authority direction (Postgres is the record; the
--       graph is the derived copy, §2.0/§3) and is how a cache value gets
--       mistaken for a record.
--   (b) A separate `public.embedding` table covering all six labels. It has
--       the same population problem for these two, and it adds a SECOND row
--       per vector — a row the tenant-erasure cascade must reach, i.e. the
--       "erasure spans both stores" defect, created by this change. As a
--       COLUMN the vector lives in the node's own row, so `DELETE` of the node
--       deletes the vector and the residual scan has one place to look.
--
-- ⇒ NOTHING IS LOST BY LEAVING THEM OUT, and this is the measured part: today
-- `CALL db.indexes()` holds exactly ONE vector index — `Point.embedding` — so
-- `Object` and `Subject` vectors were ALREADY unindexed and their dense leg
-- ALREADY full-scanned (`#4997`, `#11087`). They keep that same brute-force
-- graph scan after this move; only `Point`/`Source`/`Event` change mechanism,
-- and the leg's own trace says which ran. Whether those two labels should
-- carry embeddings AT ALL is `STORAGE-ARCHITECTURE.md` §14.3 **V3**, which is
-- still OPEN and which V1 did not spend — this migration does not spend it
-- either.
