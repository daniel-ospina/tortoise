-- ============================================================================
-- Migration 20261117000001: `object`/`subject` get the record's embedding
--                             column + generated vector + HNSW index
--                             (#13220; owner ruling 2026-11-17)
-- ----------------------------------------------------------------------------
-- THE DECISION THIS IMPLEMENTS, NOT ONE IT MAKES. Owner ruling, 2026-11-17
-- (recorded on #13220, relayed from the roadmap #13082):
--
--   *"if indexing improves search for Object/Subject of course we want that,
--    and we just need it in Postgres and the search aligned with that and
--    everything armed and working"* … *"we should likely also index source
--    also in postgres"*
--
-- That answers `docs/architecture/STORAGE-ARCHITECTURE.md` §14.3 **V3**'s
-- remaining half (are `:Object`/`:Subject` indexed? — yes, in **Postgres**) and
-- **V2** (does a `:Source` get a vector? — yes, in Postgres; `source` already
-- had the column and index, `20261109000001`). `#11901`'s *"not earned"* verdict
-- was about **FalkorDB** indexes (four further resident HNSWs on the resource
-- that costs money, §12.1c) and does not bind a pgvector index.
--
-- ── ⛔ WHAT THIS FILE OVERRIDES, NAMED SO IT IS NOT TIDIED AWAY LATER ────────
--
-- **OVERRIDES:** §17.5's rule that a DERIVED/VOLATILE property never becomes a
-- record COLUMN (`docs/epics/2026-10-10-7879-postgres-record/
-- 02-identity-and-placement.md`, enforced for these two labels by
-- `supabase/tests/20261012000001_record_schema.sql`'s "no DERIVED/VOLATILE
-- property has a column" assertion and by
-- `chk_object_props_no_derived`/`chk_subject_props_no_derived`) — the rule
-- exists so a RECOMPUTED value never becomes record AUTHORITY, and it is
-- overridden for exactly one property on exactly two labels because the owner
-- requires those vectors to be *indexed in Postgres*, which is impossible
-- without storing them. The rule's PURPOSE is preserved rather than spent: the
-- column is declared RECOMPUTABLE (see "why this is not authority" below), the
-- `props` refusal is NOT relaxed (a payload still cannot smuggle an `embedding`
-- into the open property tail), and `object.status` — the other member of that
-- refusal — keeps its ban untouched.
--
-- ── THE SECOND REVERSAL, AND THE ONE RULING THIS FILE DOES **NOT** TOUCH ──
--
-- `20261109000001_record_embedding_vector.sql` carries a recorded decision —
-- its block "WHY `public.object` AND `public.subject` GET NO VECTOR COLUMN"
-- rejects its own candidate (a), a column on either label, and is marked "stated
-- so the next lane does not re-derive it". Migrations are append-only, so that
-- block stays exactly as it is; this file is the later declaration, and this
-- line is what makes the reversal findable instead of looking like an accident
-- of history:
--
-- **OVERRIDES:** `20261109000001_record_embedding_vector.sql`'s decision that
-- `object`/`subject` get NO vector column (its candidate (a)) — the owner's
-- 2026-11-17 ruling spends V3's remaining half and requires both vectors to be
-- INDEXED in Postgres, and candidate (a)'s stated objection ("nothing would fill
-- it, so it would be an empty column") is refuted by a WRITER rather than by the
-- objection being wrong: `record_writer.derived_embedding_columns` fills it on
-- the LIVE write path, through the same `embeddings.encode_for_row` seam the
-- graph writes call. ⚠️ NOT on a `record_runner` replay — see "THE COLUMN
-- ARRIVES EMPTY" below, which states exactly which routes fill it and which do
-- not.
--
-- ⛔ AND WHAT THIS FILE DOES **NOT** OVERRIDE: the **2026-11-10 KEEP ruling**
-- (#11395, comment `6781921075`) forbids any pass from removing
-- `:Object`/`:Subject` embeddings **from the graph** or from stopping their
-- write there. This file touches Postgres only. `projection/entities.py::
-- _upsert_object` / `_upsert_subject` still call `encode_for_row(name)` and
-- still write `o.embedding`/`s.embedding`; a change that stopped that would be a
-- REOPEN of #11395, not a consequence of this migration.
--
-- ── WHY THIS IS NOT AUTHORITY (the objection §17.5 exists to raise) ─────────
--
-- `Object.embedding`/`Subject.embedding` are DERIVED — "recomputed from the
-- carried `name`" (§2.4/§2.6 of the placement doc, the correction that moved
-- both from PRIMARY to DERIVED). This file does not make them authority and
-- does not create a second rule for producing them:
--
--   * the RECORD's `name` is the carried PRIMARY content, and the vector is a
--     pure function of it through the SAME seam the graph fold uses
--     (`embeddings.encode_for_row`, `#5008`'s EMBED gate). There is no second
--     embedder and no second text composition — the writer's text expression is
--     per-label and is pinned against the graph fold's own
--     (`tests/test_13220_record_label_embeddings.py`);
--   * therefore a record-based rebuild can RESTORE it by recomputing, which is
--     the durability gap #13220 names as gap 1 ("their fingerprints exist only
--     in FalkorDB today … a record-based rebuild cannot restore them") — the
--     column is what closes that gap, not what re-opens it;
--   * `embedding_vec` stays GENERATED, so "the indexed vector disagrees with the
--     stored payload" is not a reachable state (the same property
--     `20261109000001` took for point/source/event).
--
-- ── THE SHAPE, DELIBERATELY IDENTICAL TO `point`/`source`/`event` ───────────
--
--   embedding      jsonb                    — the payload, written by the record
--                                             writer (`record_writer.write`'s
--                                             derived-embedding seam)
--   embedding_vec  vector(384) GENERATED    — via the ONE converter,
--                                             `public.record_embedding_vector`,
--                                             declared by `20261109000001`
--   idx_*.embedding_vec_hnsw                — `vector_cosine_ops`, the ranking
--                                             the graph's cosine index produced
--
-- ⛔ NO NEW CONVERTER AND NO NEW DIMENSION. `public.record_embedding_vector`
-- already exists (IMMUTABLE/STRICT/PARALLEL SAFE, schema-qualified body because
-- `pg_restore` runs with an empty `search_path` — see that file's note, measured
-- on a real `pg_dump`/`pg_restore` cycle). It returns NULL for anything that is
-- not a 384-long all-numeric JSON array, so a wider or null-bearing embedding is
-- STORED and not ann-indexed instead of raising on the record's write path.
--
-- ── ⚠️ LOCK POSTURE (operator note, the same shape as #11660's) ─────────────
--
-- `ADD COLUMN … GENERATED … STORED` REWRITES `object` and `subject` under
-- `ACCESS EXCLUSIVE`; `CREATE INDEX` (not CONCURRENTLY — Supabase runs
-- migrations inside a transaction, and `CONCURRENTLY` cannot run in one) takes
-- `SHARE` for the duration of the HNSW build. `object` is the larger of the two
-- (7,865 vectors on the graph measured 2026-11-17, #13220) and both tables are
-- small relative to `point`, but the window is a rewrite + a build per table and
-- is NOT online. `maintenance_work_mem` >= ~2x the raw vector bytes keeps the
-- build on pgvector's fast path rather than spilling for ~9x the time
-- (`20261109000001`'s measured figures: 4.0-4.2 s vs 37.4 s at 20,000 rows).
--
-- ⚠️ THE COLUMN ARRIVES EMPTY, AND THAT IS THE HONEST STATE. The rows written
-- BEFORE this file land carry no vector: the record is a journal fold, and the
-- journal never carried one for these labels (`_upsert_object` derives it in the
-- graph fold and writes it straight onto the node — #7845). The writer added
-- with this file fills every row written AFTER it ON THE LIVE PATH
-- (`record_writer.RecordWriter.write` is `derived_embedding_columns`' only
-- caller). ⛔ AND WHAT DOES **NOT** FILL IT — stated because a previous cut of
-- this paragraph claimed the opposite and was wrong:
--
--   * `record_projection.replay()` does NOT, on its own. `derived=` is a
--     KEYWORD DEFAULTING TO `None`, and `tools/record_runner.py:761` (the
--     `record_runner` bulk/backfill fold, #13210) calls `replay(batch, conn,
--     org_id=…, graph_id=…, start_seq=…, allocate=…)` without it. So a
--     `record_runner` re-fold of a historic journal lands `object.embedding` /
--     `subject.embedding` / `event.embedding` NULL and the two HNSW indexes
--     below stay empty for that population. Threading `derived=` through the
--     runner (which needs an embedder in a tool that is deliberately
--     embedder-free) is a FOLLOW-UP, not something this file delivers.
--   * the same `replay()` divergence is why `mutations_for`'s long-standing
--     "a replay in journal order builds the same row the live path built"
--     invariant now holds only WHEN the caller passes `derived=` — that
--     docstring says so explicitly at HEAD.
--
-- ⚠️ AND THE PRICE OF DERIVING IT TWICE, disclosed rather than buried: the
-- record derives its vector from the same text the graph fold then encodes
-- (`projection/entities.py::_upsert_object`/`_upsert_subject`/
-- `_upsert_document`/`_upsert_event`), and `embeddings.encode_for_row` memoises
-- nothing — so on an ARMED record a live `ObjectRegistered`/`SubjectAdded`/
-- `EventRecorded`/`DocumentCreated` write now issues TWO model calls for one
-- text. That is a real cost increase on the capture path (the record write
-- path's own per-record cost was measured to the millisecond in
-- `docs/evidence/9558-record-write-cost/`, and an encode is orders of magnitude
-- above it). It is NOT measured here and NOT mitigated here; sharing one encode
-- across both writers is a follow-up. This file claims no cost figure.
-- ============================================================================

-- ⛔ THIS FILE DECLARES ITS OWN pgvector DEPENDENCY, on purpose.
--
-- It cannot be applied without `public.vector` and
-- `public.record_embedding_vector(jsonb)`, which `20261109000001` creates. It must
-- therefore be treated exactly as that file is by the PGlite drill, whose skip
-- predicate (`supabase/tests/pglite/validate.mjs`, `PGLITE_MISSING_EXTENSIONS`)
-- decides "does this migration need pgvector" by looking for a
-- `CREATE EXTENSION … vector` statement in the file ITSELF.
--
-- ⚠️ MEASURED DEFECT this line fixes: a first cut of this file had no
-- `CREATE EXTENSION` (correctly — `20261109000001` already made one), so the
-- drill APPLIED it while SKIPPING the file that defines everything it calls, and
-- `schema-drill` (a REQUIRED context — `docs/ci/required-contexts.json`) failed
-- with `type "vector" does not exist` / `function public.record_embedding_vector
-- does not exist`. `IF NOT EXISTS` makes this a no-op wherever the extension is
-- already installed, and `SCHEMA public` matches `20261109000001` so the type
-- resolves the same way. The declaration is the fix rather than a wider
-- detector: a migration knows what it needs, and this is how the harness is told.
CREATE EXTENSION IF NOT EXISTS vector SCHEMA public;

-- ── object ──────────────────────────────────────────────────────────────────
ALTER TABLE public.object
    ADD COLUMN IF NOT EXISTS embedding jsonb;

ALTER TABLE public.object
    ADD COLUMN IF NOT EXISTS embedding_vec vector(384)
    GENERATED ALWAYS AS (public.record_embedding_vector(embedding)) STORED;

CREATE INDEX IF NOT EXISTS idx_object_embedding_vec_hnsw
    ON public.object USING hnsw (embedding_vec public.vector_cosine_ops);

-- ── subject ─────────────────────────────────────────────────────────────────
ALTER TABLE public.subject
    ADD COLUMN IF NOT EXISTS embedding jsonb;

ALTER TABLE public.subject
    ADD COLUMN IF NOT EXISTS embedding_vec vector(384)
    GENERATED ALWAYS AS (public.record_embedding_vector(embedding)) STORED;

CREATE INDEX IF NOT EXISTS idx_subject_embedding_vec_hnsw
    ON public.subject USING hnsw (embedding_vec public.vector_cosine_ops);

COMMENT ON COLUMN public.object.embedding IS
    'The Object fingerprint, in the record (#13220, owner ruling 2026-11-17). DERIVED — a pure function of the carried `name` through the SAME seam the graph fold uses (embeddings.encode_for_row), so a record-based rebuild restores it by recomputing; it is not authority and carries no independent write path. The graph copy (`o.embedding`) is NOT retired: the 2026-11-10 KEEP ruling (#11395) forbids that.';
COMMENT ON COLUMN public.object.embedding_vec IS
    'The ANN-indexed projection of public.object.embedding (#13220). GENERATED ALWAYS — never written by the record projection or a caller; NULL when the payload is not a 384-long all-numeric JSON array. idx_object_embedding_vec_hnsw is what makes the label pgvector-served (V3''s remaining half, §14.3).';
COMMENT ON COLUMN public.subject.embedding IS
    'The Subject fingerprint, in the record (#13220). See public.object.embedding for the derived-column contract; the KEEP ruling (#11395) keeps the graph copy too.';
COMMENT ON COLUMN public.subject.embedding_vec IS
    'The ANN-indexed projection of public.subject.embedding (#13220). See public.object.embedding_vec for the generated-column contract.';

-- No grant changes: both columns ride tables that already carry the SELECT grant
-- and the RLS policies, and every write goes through the service_role write path
-- (#7951). `embedding_vec` is GENERATED, so Postgres refuses an INSERT/UPDATE
-- naming it — the second lock on the same door as `record_projection.
-- GENERATED_COLUMNS`, which keeps the projection from ever trying.
--
-- ⛔ THE `props` REFUSALS ARE DELIBERATELY UNCHANGED. `chk_object_props_no_derived`
-- (embedding, status) and `chk_subject_props_no_derived` (embedding, status) still
-- refuse those names in the open property tail: the COLUMN is the home of the
-- fingerprint, the props tail is not.
--
-- ⛔ AND THE ORDERING IS THE OTHER WAY ROUND FROM WHAT A READER MIGHT ASSUME, so
-- it is stated from the code rather than from intent: a payload that NAMES
-- `embedding` is **DROPPED**, not resolved to the column.
-- `record_projection._split_properties` tests the new DERIVED arm
-- (`elif column in DERIVED_COLUMNS.get(table, …): continue`) BEFORE
-- `elif column in cols`, so a carried `embedding` never reaches the column — the
-- column is written ONLY from `replay`'s `derived=` mapping, which is what makes
-- a recomputed value non-authority. The props CHECK is therefore the SECOND lock
-- on the same door, not the first: it stops a payload that bypassed the
-- projection entirely (a direct INSERT through the raw door), while the
-- projection stops every other path by construction.
