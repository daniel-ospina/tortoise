-- ============================================================================
-- Migration 20261014000001: `subject.props` refuses `status` (#8263, epic #7879)
-- ----------------------------------------------------------------------------
-- `Object.status` and `Subject.status` are the SAME property — the
-- projection-owned lifecycle cache (§2.6; `ONTOLOGY.md` §2/§4.3), never record
-- authority. The semantic layer (20261012000002_record_semantic.sql, PR #8179)
-- refuses the name on `public.object` and NOT on `public.subject`:
--
--   chk_object_props_no_derived  refuses  embedding, status
--   chk_subject_props_no_derived refuses  embedding          ← the gap
--
-- §2.6 classifies the two identically, and its own `Subject` sentence carries
-- the same #1423 verdict, so the asymmetry is an oversight and not a ruling.
-- Left open, a projection faithfully replaying an `EntityMutated op=restatus`
-- record can make the cache authoritative in the record — the exact defect the
-- EP-scalar carve-out exists to close, one table over.
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
--     discipline (#8263). `refused_props_names()` reads this ALTER form, so
--     the Python side and the CHECK cannot drift apart.
--
-- `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT` in ONE statement: idempotent
-- on re-apply, and it re-declares the full refusal set (a partial set would
-- silently re-open `embedding`).
-- ============================================================================

ALTER TABLE public.subject
    DROP CONSTRAINT IF EXISTS chk_subject_props_no_derived,
    ADD CONSTRAINT chk_subject_props_no_derived CHECK (
        NOT (props ?| ARRAY['embedding', 'status']::text[])
    );
