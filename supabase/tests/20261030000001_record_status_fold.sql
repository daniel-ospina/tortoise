-- ============================================================================
-- SQL-level verification for the PROCEDURAL `status` FOLD (#9974, epic #7879).
--
-- The defect this file pins, in one sentence: `Object.status` / `Subject.status`
-- are declared a projection-owned CACHE of the lifecycle event stream (§2.6;
-- `ONTOLOGY.md` §2/§4.3), and the procedural view that computes them folded
-- exactly ONE writer — supersession, via the row's own PRIMARY halves
-- (`superseded_by` / `superseded_at`). Every other status a writer can set
-- (`EntityMutated op=restatus`: `deprecated`, `archived`, `retracted`, …; and the
-- Subject supersession, which journals `EntityMutated op=supersede` and has no
-- `ObjectSuperseded` twin) read `live` — and absence-turned-live is what makes a
-- TERMINAL node served as CURRENT (`tortoise/live.py::_terminal_excluded`).
--
-- `supabase/migrations/20261030000001_record_procedural_status_fold.sql` extends
-- the fold over the record's OWN event stream (`public.journal_event`, layer 1).
-- This suite proves the four properties that make the extension faithful rather
-- than plausible, one assertion each:
--
--   1. CREATION carries the status the fold seeded, and a RE-registration does
--      NOT clobber it (`_upsert_object` writes `status` in `ON CREATE` only — the
--      #1350 clobber guard).
--   2. `restatus` MOVES it (`deprecated`), and a LATER writer wins by journal
--      `seq` — including a `restatus` AFTER a supersession, which is the shape
--      `rebuild_all` resolves with its #4743 re-fold.
--   3. SUPERsession — through `ObjectSuperseded`, and through the Subject's
--      `EntityMutated op=supersede` — reads `superseded`.
--   4. The `superseded_by` FALLBACK survives UNDER the fold, so a row whose
--      stream the record does not hold still answers the one transition it can
--      prove (rather than defaulting to `live`).
--
-- ⛔ WHAT THIS FILE IS NOT. It is not a test of the graph fold: the graph's own
-- `_fold_object_superseded` / `_fold_object_status` behaviour is pinned on the
-- Python side. It is also NOT a claim that the fold is total — the pack/connector
-- lifecycle vocabulary (`status_folds.py`) is DECLARED BY PACKS AT RUNTIME and
-- is named as an open gap in the migration (tracked as #10188), not asserted here.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
-- ============================================================================

-- ── Assertion helper (matches the harness convention; idempotent) ──────────
CREATE SCHEMA IF NOT EXISTS tests;
CREATE OR REPLACE FUNCTION tests.assert(cond boolean, msg text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'assertion failed: %', msg USING ERRCODE = 'raise_exception';
  END IF;
END $$;

-- ── Fixture: one tenant, one graph, six nodes ──────────────────────────────
-- Objects:
--   obj-create   — created with status='live' only                    → live
--   obj-restatus — created, then restated to 'deprecated'             → deprecated
--   obj-supersede— created, superseded via ObjectSuperseded           → superseded
--   obj-order    — superseded, THEN restated to 'archived'            → archived
--   obj-recreate — created 'live', restatus 'deprecated', RE-registered
--                  (a second ObjectRegistered) → still 'deprecated'
--   obj-fallback — no journal rows at all, row carries superseded_by  → superseded
-- Subjects:
--   sub-supersede — superseded through `EntityMutated op=supersede`   → superseded
INSERT INTO public.organizations (id, name, graph_name)
  VALUES ('org_9974', 'org_9974', 'org_org_9974') ON CONFLICT DO NOTHING;
INSERT INTO public.graphs (id, org_id, name, namespace)
  VALUES ('g_9974', 'org_9974', 'g_9974', 'org_org_9974_g_9974')
  ON CONFLICT (id) DO NOTHING;

INSERT INTO public.node_identity (org_id, graph_id, node_label, node_key)
  VALUES ('org_9974','g_9974','Object','obj-create'),
         ('org_9974','g_9974','Object','obj-restatus'),
         ('org_9974','g_9974','Object','obj-supersede'),
         ('org_9974','g_9974','Object','obj-order'),
         ('org_9974','g_9974','Object','obj-recreate'),
         ('org_9974','g_9974','Object','obj-fallback'),
         ('org_9974','g_9974','Subject','sub-supersede')
  ON CONFLICT DO NOTHING;

-- `node_key = id` since 20261027000001 (both columns, same value).
INSERT INTO public.object (org_id, graph_id, node_key, id, name, object_kind,
                           superseded_by, superseded_at)
  VALUES ('org_9974','g_9974','obj-create','obj-create','Created','org',NULL,NULL),
         ('org_9974','g_9974','obj-restatus','obj-restatus','Restated','org',NULL,NULL),
         ('org_9974','g_9974','obj-supersede','obj-supersede','Superseded','org',
          'Created', '2026-10-30T00:00:00Z'),
         ('org_9974','g_9974','obj-order','obj-order','Ordered','org',
          'Created', '2026-10-30T00:00:00Z'),
         ('org_9974','g_9974','obj-recreate','obj-recreate','Recreated','org',NULL,NULL),
         ('org_9974','g_9974','obj-fallback','obj-fallback','Fallback','org',
          'Created', '2026-10-30T00:00:00Z')
  ON CONFLICT DO NOTHING;
INSERT INTO public.subject (org_id, graph_id, node_key, id, name, subject_kind,
                            superseded_by, superseded_at)
  VALUES ('org_9974','g_9974','sub-supersede','sub-supersede','Superseded','person',
          'Successor', '2026-10-30T00:00:00Z')
  ON CONFLICT DO NOTHING;

-- The record's OWN event stream. `seq` is the journal position and the fold's
-- only ordering key, so the fixture numbers these by hand in the order the
-- writers would have emitted them. `payload` is the RECORD minus its envelope
-- (`record_projection._journal_row` does exactly that subtraction), which is
-- why the snapshot carriers (`ObjectRegistered`/`SubjectAdded`) sit at the top
-- level and `EntityMutated` nests its applied map under `state`.
INSERT INTO public.journal_event (org_id, graph_id, seq, event_type, payload)
  VALUES
    -- 1. creation, no explicit status: the fold's `coalesce(…, 'live')` seed.
    ('org_9974','g_9974',1,'ObjectRegistered',
     '{"id":"obj-create","name":"Created","objectKind":"org"}'::jsonb),
    -- 2/3. creation then restatus → deprecated.
    ('org_9974','g_9974',2,'ObjectRegistered',
     '{"id":"obj-restatus","name":"Restated","objectKind":"org"}'::jsonb),
    ('org_9974','g_9974',3,'EntityMutated',
     '{"id":"obj-restatus","label":"Object","op":"restatus","state":{"status":"deprecated"}}'::jsonb),
    -- 4/5. creation then ObjectSuperseded → superseded.
    ('org_9974','g_9974',4,'ObjectRegistered',
     '{"id":"obj-supersede","name":"Superseded","objectKind":"org"}'::jsonb),
    ('org_9974','g_9974',5,'ObjectSuperseded',
     '{"id":"obj-supersede","name":"Superseded","supersedes_by":"Created"}'::jsonb),
    -- 6/7/8. superseded, THEN restated → the LATER writer wins (archived).
    ('org_9974','g_9974',6,'ObjectRegistered',
     '{"id":"obj-order","name":"Ordered","objectKind":"org"}'::jsonb),
    ('org_9974','g_9974',7,'ObjectSuperseded',
     '{"id":"obj-order","name":"Ordered","supersedes_by":"Created"}'::jsonb),
    ('org_9974','g_9974',8,'EntityMutated',
     '{"id":"obj-order","label":"Object","op":"restatus","state":{"status":"archived"}}'::jsonb),
    -- 9/10/11. restatus then a RE-registration: `ON MATCH` never moves status,
    -- so the second `ObjectRegistered` is not a writer.
    ('org_9974','g_9974',9,'ObjectRegistered',
     '{"id":"obj-recreate","name":"Recreated","objectKind":"org"}'::jsonb),
    ('org_9974','g_9974',10,'EntityMutated',
     '{"id":"obj-recreate","label":"Object","op":"restatus","state":{"status":"deprecated"}}'::jsonb),
    ('org_9974','g_9974',11,'ObjectRegistered',
     '{"id":"obj-recreate","name":"Recreated","objectKind":"org","status":"live"}'::jsonb),
    -- 12/13. the Subject twin: created, then superseded through the ENTITY
    -- surface (`state.status='superseded'`; there is no `SubjectSuperseded`
    -- record type — #1423 journals `EntityMutated op=supersede`).
    ('org_9974','g_9974',12,'SubjectAdded',
     '{"id":"sub-supersede","name":"Superseded","subject_kind":"person"}'::jsonb),
    ('org_9974','g_9974',13,'EntityMutated',
     '{"id":"sub-supersede","label":"Subject","op":"supersede","state":{"status":"superseded","supersededBy":"Successor"}}'::jsonb)
  ON CONFLICT DO NOTHING;

-- ── 1. The fold computes the value the record refuses to store ─────────────
SELECT tests.assert(
  (SELECT status FROM public.object_current_status
    WHERE org_id = 'org_9974' AND graph_id = 'g_9974' AND object_key = 'obj-create')
    = 'live',
  '9974: a created Object with no explicit status folds to live (the fold''s own ON CREATE seed)');

SELECT tests.assert(
  (SELECT status FROM public.object_current_status
    WHERE org_id = 'org_9974' AND graph_id = 'g_9974' AND object_key = 'obj-restatus')
    = 'deprecated',
  '9974: an EntityMutated op=restatus MOVES the folded status (deprecated) — the arm that read live before this migration');

SELECT tests.assert(
  (SELECT status FROM public.object_current_status
    WHERE org_id = 'org_9974' AND graph_id = 'g_9974' AND object_key = 'obj-supersede')
    = 'superseded',
  '9974: ObjectSuperseded folds to superseded');

SELECT tests.assert(
  (SELECT status FROM public.object_current_status
    WHERE org_id = 'org_9974' AND graph_id = 'g_9974' AND object_key = 'obj-order')
    = 'archived',
  '9974: a restatus AFTER a supersession wins by journal seq (the #4743 re-fold order)');

SELECT tests.assert(
  (SELECT status FROM public.object_current_status
    WHERE org_id = 'org_9974' AND graph_id = 'g_9974' AND object_key = 'obj-recreate')
    = 'deprecated',
  '9974: a RE-registration does not clobber the stored status (the fold''s ON CREATE-only rule)');

SELECT tests.assert(
  (SELECT status FROM public.object_current_status
    WHERE org_id = 'org_9974' AND graph_id = 'g_9974' AND object_key = 'obj-fallback')
    = 'superseded',
  '9974: the superseded_by PRIMARY half still answers when the record holds no event for the row');

SELECT tests.assert(
  (SELECT status FROM public.subject_current_status
    WHERE org_id = 'org_9974' AND graph_id = 'g_9974' AND subject_key = 'sub-supersede')
    = 'superseded',
  '9974: the Subject twin folds its EntityMutated op=supersede to superseded');

-- ── 2. The view still refuses to be a write path ──────────────────────────
-- §2.6's capstone S5: the derived field is not record authority. The view is
-- SELECT-only for every real role — a write through it must be impossible, or
-- "computed, never stored" is a claim with no enforcement.
SELECT tests.assert(
  (SELECT count(*) FROM pg_class
    WHERE relname IN ('object_current_status','subject_current_status')
      AND relkind = 'v') = 2,
  '9974: the procedural layer is still a VIEW (never a fourth table)');

SELECT tests.assert(
  NOT has_table_privilege('authenticated', 'public.object_current_status', 'INSERT')
  AND NOT has_table_privilege('authenticated', 'public.subject_current_status', 'UPDATE'),
  '9974: the fold is READ-ONLY for the read role — a cache must not become a write path');

-- ── 3. The fold leaves rows the record cannot speak for ALONE ─────────────
-- A row with no journal rows and no superseded_by is the legacy shape: the
-- view answers 'live' (the pre-existing answer), and it must not invent a
-- NON-default status for it.
INSERT INTO public.node_identity (org_id, graph_id, node_label, node_key)
  VALUES ('org_9974','g_9974','Object','obj-silent') ON CONFLICT DO NOTHING;
INSERT INTO public.object (org_id, graph_id, node_key, id, name, object_kind)
  VALUES ('org_9974','g_9974','obj-silent','obj-silent','Silent','org')
  ON CONFLICT DO NOTHING;

SELECT tests.assert(
  (SELECT status FROM public.object_current_status
    WHERE org_id = 'org_9974' AND graph_id = 'g_9974' AND object_key = 'obj-silent')
    = 'live',
  '9974: an Object with no event stream and no superseded_by stays live (no invented status)');

-- ── 4. Leave no trace — this suite must not move the harness's global counts ──
-- The #7969 replay check in `validate.mjs` asserts GLOBAL `count(*)` over
-- `journal_event` and the five layer tables, so a suite that seeds rows and
-- walks away silently changes another suite's denominator. The erasure is done
-- through the RECORD'S OWN erasure path (`purge_org_record`), which is the one
-- production door that sets `app.record_journal_purge` — using the GUC directly
-- here would be a second declaration of the append-only exception.
SELECT public.purge_org_record('org_9974');

SELECT tests.assert(
  (SELECT count(*) FROM public.journal_event WHERE org_id = 'org_9974') = 0,
  '9974: the fixture purged its own journal rows (the append-only trigger allows it only through purge_org_record)');

SELECT tests.assert(
  (SELECT count(*) FROM public.object WHERE org_id = 'org_9974') = 0
  AND (SELECT count(*) FROM public.subject WHERE org_id = 'org_9974') = 0,
  '9974: the fixture purged its own Object/Subject rows (a residual row would move another suite''s global count)');
