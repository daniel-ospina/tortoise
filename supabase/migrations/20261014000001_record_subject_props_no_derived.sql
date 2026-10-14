-- ============================================================================
-- Migration 20261014000001: `subject.props` refuses `status` (#8263, epic #7879)
-- ----------------------------------------------------------------------------
-- `Object.status` and `Subject.status` are the SAME property — the
-- projection-owned lifecycle cache (`ONTOLOGY.md` §4.2 "Subject … status …
-- Projection-owned since #1423 — the Subject twin of the Object status cache",
-- §4.3 "Object"; §2.6's deciding test classifies `status` a DERIVED cache),
-- never record authority. The semantic layer (20261012000002_record_semantic.sql,
-- PR #8179) refuses the name on `public.object` and NOT on `public.subject`:
--
--   chk_object_props_no_derived  refuses  embedding, status
--   chk_subject_props_no_derived refuses  embedding          ← the gap
--
-- `docs/epics/2026-10-10-7879-postgres-record/03-schema.md` §4 records the
-- `#1423` Subject verdict ("`Subject.status` is *not* added … §2.6 and
-- `ONTOLOGY.md` §4.2 both name it a cache"), and `02-identity-and-placement.md`
-- §2.6's deciding test classifies it the same way, so the asymmetry is an
-- oversight and not a ruling. Left open, a projection faithfully replaying an
-- `EntityMutated op=restatus` record can make the cache authoritative in the
-- record — the exact defect the EP-scalar carve-out exists to close, one table
-- over.
--
-- A FORWARD migration, never an edit of 20261012000002: migrations are
-- append-only history (the `check-migration-append-only` gate refuses an edit
-- to a landed file), and #8179 had already merged when #8263 was filed.
--
-- The refusal is carried on BOTH sides, exactly as `object` carries it:
--   * this CHECK — any writer, including a raw SQL caller; and
--   * `record_projection.OpDispatch.never_store['subject']` (the mutation
--     path) plus the schema read in `refused_props_names()` (the snapshot
--     path), so the projection DROPS the name rather than emitting a props
--     tail the constraint refuses — a crash handed to a caller is not a
--     discipline (#8263). `refused_props_names()` reads this ALTER form
--     (`_ALTER_DERIVED_CHECK_RE`); the migration list it reads
--     (`RECORD_MIGRATIONS`) and the CI selection list (`CORE_ALSO`) are
--     hand-maintained, so the drift is pinned by
--     `tests/test_record_projection.py::test_every_record_schema_migration_on_disk_is_read`
--     and
--     `tests/test_ci_selection.py::test_record_schema_pin_runs_on_migration_or_doc_change`.
--
-- `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT` in ONE statement: idempotent
-- on re-apply, and it re-declares the full refusal set rather than a partial
-- one (a partial set would silently re-open `embedding`; the pgTAP suite pins
-- the LIVE constraint, which the Python union parse cannot).
-- ============================================================================

-- Backfill BEFORE the ADD: `ADD CONSTRAINT` validates existing rows by
-- default, so a single pre-existing `status` in `subject.props` — written before
-- this refusal existed, via the snapshot path the projection only drops
-- post-migration — would abort the migration on a non-empty table. The value is
-- DERIVED and must not have been stored as authority in the first place, so
-- removing it is the correct backfill, not a data migration.
UPDATE public.subject SET props = props - 'status' WHERE props ? 'status';

ALTER TABLE public.subject
    DROP CONSTRAINT IF EXISTS chk_subject_props_no_derived,
    ADD CONSTRAINT chk_subject_props_no_derived CHECK (
        NOT (props ?| ARRAY['embedding', 'status']::text[])
    );
