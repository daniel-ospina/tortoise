-- ============================================================================
-- SQL-level verification for migration 20261028000001 (#10025) — the default
-- graph's stable id row, and the ONE-live-default-row invariant it relies on.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--   (harness applies ALL migrations incl. this one, then runs this suite with
--   ON_ERROR_STOP semantics — every assertion RAISEs on failure)
--
-- Why these are SQL assertions and not Python ones: the two properties pinned
-- here live in the SCHEMA, and a Python fake models neither. Everything below
-- is executed against real Postgres.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS tests;
CREATE OR REPLACE FUNCTION tests.assert(cond boolean, msg text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ASSERTION FAILED: %', msg;
  END IF;
END $$;

-- ── 1. The backstop index exists, with the predicate the invariant needs ────
-- Unique over `org_id`, scoped to LIVE default rows. `status <> 'deleted'`
-- (not `status = 'active'`) is deliberate: it keeps the defensive
-- tombstone-beside-active shape legal, which §3 below asserts.
SELECT tests.assert(
  EXISTS (SELECT 1 FROM pg_indexes
           WHERE schemaname = 'public' AND tablename = 'graphs'
             AND indexname = 'uq_graphs_org_default_active'),
  '#10025: uq_graphs_org_default_active exists on public.graphs');

SELECT tests.assert(
  (SELECT indexdef FROM pg_indexes
    WHERE schemaname = 'public' AND tablename = 'graphs'
      AND indexname = 'uq_graphs_org_default_active') LIKE 'CREATE UNIQUE INDEX%',
  '#10025: uq_graphs_org_default_active is UNIQUE');

SELECT tests.assert(
  (SELECT indexdef FROM pg_indexes
    WHERE schemaname = 'public' AND tablename = 'graphs'
      AND indexname = 'uq_graphs_org_default_active')
    LIKE '%(org_id)%kind = ''default''::text%status <> ''deleted''::text%'
  OR (SELECT indexdef FROM pg_indexes
       WHERE schemaname = 'public' AND tablename = 'graphs'
         AND indexname = 'uq_graphs_org_default_active')
     LIKE '%(org_id)%((kind = ''default''::text))%((status <> ''deleted''::text))%',
  '#10025: the index is on (org_id) WHERE kind=''default'' AND status <> ''deleted''');

-- ── 2. A SECOND live default row for one org is rejected (executed) ─────────
-- Before this index the pair was legal, and the pair is what makes every
-- `rows[0]` reader ambiguous: one org's record rows split across two ids.
INSERT INTO public.organizations (id, name, graph_name)
VALUES ('10025-one-default', 'One Default', 'org_10025_one');
INSERT INTO public.graphs (id, org_id, name, kind, namespace, status)
VALUES ('g_10025default0001', '10025-one-default', 'default', 'default',
        'org_10025_one', 'active');
SELECT tests.assert(
  (SELECT count(*) FROM public.graphs
    WHERE org_id = '10025-one-default' AND kind = 'default') = 1,
  '#10025: setup — the org has exactly the one live default row');

DO $$
BEGIN
  INSERT INTO public.graphs (id, org_id, name, kind, namespace, status)
  VALUES ('g_10025default0002', '10025-one-default', 'Team HQ', 'default',
          'org_10025_one', 'active');
  RAISE EXCEPTION
    'ASSERTION FAILED: a second LIVE kind=''default'' row for one org was accepted';
EXCEPTION
  WHEN unique_violation THEN NULL;   -- the backstop fired — what we assert
END $$;

-- ── 3. …but the DEFENSIVE tombstone-beside-active shape stays legal ─────────
-- `soft_delete_graph` refuses `kind='default'`, so no supported path mints a
-- tombstone; a raw-SQL/ops one must not turn the index into a failed deploy.
-- The readers filter `status='active'`, so the pair is unambiguous for them.
INSERT INTO public.graphs (id, org_id, name, kind, namespace, status)
VALUES ('g_10025default0003', '10025-one-default', 'default', 'default',
        'org_10025_one', 'deleted');
SELECT tests.assert(
  (SELECT count(*) FROM public.graphs
    WHERE org_id = '10025-one-default' AND kind = 'default'
      AND status <> 'deleted') = 1,
  '#10025: a tombstoned default row beside the active one is still permitted');

-- ── 4. The backfill's CONFLICT ARM, executed ───────────────────────────────
-- The statement below is the migration's own backfill (§2 of the file). It is
-- replayed here because the shape that broke it needs a row the two read
-- guards cannot see, and the guards are the thing being tested:
--
--   guards:  kind='default' AND status='active'      (no row — passes)
--            name='default' AND status <> 'deleted'  (no row — passes)
--   index:   kind='default' AND status <> 'deleted'  (THE ARCHIVED ROW)
--
-- `graphs.status` is free text with NO check constraint (20260901000001), so
-- an 'archived' row is a state the schema permits. Both guards pass, the
-- index still sees a live default row, and without `ON CONFLICT DO NOTHING`
-- the unique violation aborts the WHOLE migration transaction — for every
-- org, not just this one.
INSERT INTO public.organizations (id, name, graph_name)
VALUES ('10025-archived-guard', 'Archived Guard', 'org_10025_archived');
INSERT INTO public.graphs (id, org_id, name, kind, namespace, status)
VALUES ('g_10025archived001', '10025-archived-guard', 'old name', 'default',
        'org_10025_archived', 'archived');

INSERT INTO public.graphs (id, org_id, name, kind, namespace, status, recording)
SELECT 'g_' || substr(md5(random()::text || clock_timestamp()::text || o.id), 1, 16),
       o.id,
       'default',
       'default',
       o.graph_name,
       'active',
       NULL
  FROM public.organizations o
 WHERE o.graph_name IS NOT NULL
   AND NOT EXISTS (
        SELECT 1 FROM public.graphs g
         WHERE g.org_id = o.id AND g.kind = 'default'
           AND g.status = 'active')
   AND NOT EXISTS (
        SELECT 1 FROM public.graphs g2
         WHERE g2.org_id = o.id AND g2.name = 'default'
           AND g2.status <> 'deleted')
ON CONFLICT DO NOTHING;

SELECT tests.assert(
  (SELECT count(*) FROM public.graphs
    WHERE org_id = '10025-archived-guard' AND status = 'active') = 0,
  '#10025: the backfill skipped the org instead of aborting the migration');

-- §2b of the migration NAMES such orgs in a WARNING, because "skipped" is the
-- same outcome as "nothing to do" in a green deploy log. The predicate is
-- asserted here so the diagnostic cannot drift into naming nobody (a warning
-- that never fires is worse than none: it reads as "no stranded orgs").
SELECT tests.assert(
  (SELECT string_agg(s.org_id, ', ')
     FROM (SELECT g.org_id
             FROM public.graphs g
            WHERE g.kind = 'default'
              AND g.status NOT IN ('active', 'deleted')
              AND NOT EXISTS (
                   SELECT 1 FROM public.graphs a
                    WHERE a.org_id = g.org_id
                      AND a.kind = 'default'
                      AND a.status = 'active')
            GROUP BY g.org_id) s) = '10025-archived-guard',
  '#10025: the stranded-org diagnostic names exactly the org §2 skipped');

-- ── 5. The backfill still does its job, and is idempotent ──────────────────
INSERT INTO public.organizations (id, name, graph_name)
VALUES ('10025-needs-a-row', 'Needs A Row', 'org_10025_needs');

INSERT INTO public.graphs (id, org_id, name, kind, namespace, status, recording)
SELECT 'g_' || substr(md5(random()::text || clock_timestamp()::text || o.id), 1, 16),
       o.id,
       'default',
       'default',
       o.graph_name,
       'active',
       NULL
  FROM public.organizations o
 WHERE o.graph_name IS NOT NULL
   AND NOT EXISTS (
        SELECT 1 FROM public.graphs g
         WHERE g.org_id = o.id AND g.kind = 'default'
           AND g.status = 'active')
   AND NOT EXISTS (
        SELECT 1 FROM public.graphs g2
         WHERE g2.org_id = o.id AND g2.name = 'default'
           AND g2.status <> 'deleted')
ON CONFLICT DO NOTHING;

SELECT tests.assert(
  (SELECT count(*) FROM public.graphs
    WHERE org_id = '10025-needs-a-row' AND kind = 'default'
      AND status = 'active') = 1,
  '#10025: the backfill minted exactly one active default row');

SELECT tests.assert(
  (SELECT namespace FROM public.graphs
    WHERE org_id = '10025-needs-a-row' AND kind = 'default'
      AND status = 'active') = 'org_10025_needs',
  '#10025: the minted row''s namespace IS the org''s storage key');

SELECT tests.assert(
  (SELECT id FROM public.graphs
    WHERE org_id = '10025-needs-a-row' AND kind = 'default'
      AND status = 'active') ~ '^g_[0-9a-f]{16}$',
  '#10025: the minted id is the g_<16hex> shape set_graph_name mints');

-- Re-run: idempotent by the existence guard (the backfill is the documented
-- healing path, so a second run must be a no-op, not a duplicate).
INSERT INTO public.graphs (id, org_id, name, kind, namespace, status, recording)
SELECT 'g_' || substr(md5(random()::text || clock_timestamp()::text || o.id), 1, 16),
       o.id,
       'default',
       'default',
       o.graph_name,
       'active',
       NULL
  FROM public.organizations o
 WHERE o.graph_name IS NOT NULL
   AND NOT EXISTS (
        SELECT 1 FROM public.graphs g
         WHERE g.org_id = o.id AND g.kind = 'default'
           AND g.status = 'active')
   AND NOT EXISTS (
        SELECT 1 FROM public.graphs g2
         WHERE g2.org_id = o.id AND g2.name = 'default'
           AND g2.status <> 'deleted')
ON CONFLICT DO NOTHING;

SELECT tests.assert(
  (SELECT count(*) FROM public.graphs
    WHERE org_id = '10025-needs-a-row' AND kind = 'default') = 1,
  '#10025: a re-run of the backfill inserts nothing (idempotent)');

-- The namespace re-assert (§3 of the file) only touches kind='default' rows,
-- and only where the org still has a graph_name.
UPDATE public.graphs g
   SET namespace = o.graph_name
  FROM public.organizations o
 WHERE g.org_id = o.id
   AND g.kind = 'default'
   AND o.graph_name IS NOT NULL
   AND g.namespace IS DISTINCT FROM o.graph_name;
SELECT tests.assert(
  (SELECT count(*) FROM public.graphs g
     JOIN public.organizations o ON o.id = g.org_id
    WHERE g.kind = 'default' AND g.namespace IS DISTINCT FROM o.graph_name) = 0,
  '#10025: the namespace re-assert leaves no drifted default row');

-- ── Cleanup (the harness spot-checks for leftover fixtures) ────────────────
DELETE FROM public.graphs
 WHERE org_id LIKE '10025-%';
DELETE FROM public.organizations
 WHERE id LIKE '10025-%';
