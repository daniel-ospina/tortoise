-- ============================================================================
-- SQL-level verification for migration 20261102000001 (#10180) — the verb that
-- MOVES the default graph, the invariant it moves under, and the atomicity of
-- the move.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--   (the harness applies ALL migrations incl. this one, then runs this suite
--   with ON_ERROR_STOP semantics — every assertion RAISEs on failure)
--
-- Why these are SQL assertions and not Python ones: the one-live-default
-- invariant is a PARTIAL UNIQUE INDEX and the move is a plpgsql FUNCTION, i.e.
-- two things a Python double can only MODEL. Everything below executes against
-- real Postgres — the index's own verdict, and the function's transaction.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS tests;
CREATE OR REPLACE FUNCTION tests.assert(cond boolean, msg text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ASSERTION FAILED: %', msg;
  END IF;
END $$;

-- ── 0. Fixtures ────────────────────────────────────────────────────────────
-- One org whose DEFAULT graph is graph 0 (namespace = graph_name, the shape
-- `_provision_graph`/`ensure_default_graph_row` mint) plus a CUSTOM graph in
-- its own namespace — the exact pair a promotion moves between.
INSERT INTO public.organizations (id, name, graph_name)
VALUES ('10180-promote', 'Promote Org', 'org_10180_promote'),
       ('10180-other', 'Other Org', 'org_10180_other');

INSERT INTO public.graphs (id, org_id, name, kind, namespace, status)
VALUES ('g_10180default0001', '10180-promote', 'default', 'default',
        'org_10180_promote', 'active'),
       ('g_10180promote0002', '10180-promote', 'new-extractor', 'custom',
        'org_10180_promote_g_10180promote0002', 'active'),
       ('g_10180foreign0003', '10180-other', 'theirs', 'custom',
        'org_10180_other_theirs', 'active');

SELECT tests.assert(
  (SELECT count(*) FROM public.graphs
    WHERE org_id = '10180-promote' AND kind = 'default'
      AND status <> 'deleted') = 1,
  '#10180: setup — one live default row before the move');

-- ── 1. The move: kind follows the target, the ORG NAMESPACE follows with it ─
-- Both halves in one call. The second assertion is the one that catches the
-- attractive wrong implementation (flip `kind` only): `graph_metadata` and the
-- per-graph backup sweep derive the default from `organizations.graph_name`, so
-- a promotion that left it behind would publish the default with the OLD
-- namespace while `stable_id` named the NEW row.
SELECT tests.assert(
  (public.promote_default_graph('10180-promote', 'g_10180promote0002')
     ->> 'already_default')::boolean = false,
  '#10180: a promotion of a custom graph is a MOVE, not an already-default no-op');

SELECT tests.assert(
  (SELECT kind FROM public.graphs WHERE id = 'g_10180promote0002') = 'default',
  '#10180: the promoted graph now carries kind=''default''');

SELECT tests.assert(
  (SELECT kind FROM public.graphs WHERE id = 'g_10180default0001') = 'custom',
  '#10180: the previous default was DEMOTED to custom (the old graph is KEPT, not deleted)');

SELECT tests.assert(
  (SELECT status FROM public.graphs WHERE id = 'g_10180default0001') = 'active',
  '#10180: the demoted graph is still active and addressable');

SELECT tests.assert(
  (SELECT namespace FROM public.graphs WHERE id = 'g_10180default0001')
    = 'org_10180_promote',
  '#10180: the demoted graph keeps its OWN namespace — no data moves');

SELECT tests.assert(
  (SELECT graph_name FROM public.organizations WHERE id = '10180-promote')
    = 'org_10180_promote_g_10180promote0002',
  '#10180: the org''s default NAMESPACE follows the promoted graph');

-- §3 of 20261028000001: a `kind='default'` row's namespace must BE the org's
-- graph name. Re-asserted here because the promotion is the first operation
-- able to break it.
SELECT tests.assert(
  (SELECT g.namespace = o.graph_name
     FROM public.graphs g JOIN public.organizations o ON o.id = g.org_id
    WHERE g.org_id = '10180-promote' AND g.kind = 'default'
      AND g.status = 'active'),
  '#10180: the default row''s namespace still IS the org''s graph_name after a move');

SELECT tests.assert(
  (SELECT count(*) FROM public.graphs
    WHERE org_id = '10180-promote' AND kind = 'default'
      AND status <> 'deleted') = 1,
  '#10180: the org has EXACTLY one live default row after the move');

-- ── 2. The invariant, EXECUTED: a second live default is impossible ────────
-- The backstop is `uq_graphs_org_default_active` (20261028000001), not this
-- function's discipline. Drive it directly, on the post-promotion state.
DO $$
BEGIN
  INSERT INTO public.graphs (id, org_id, name, kind, namespace, status)
  VALUES ('g_10180duplicate04', '10180-promote', 'dupe', 'default',
          'org_10180_dupe', 'active');
  RAISE EXCEPTION
    'ASSERTION FAILED: a SECOND live kind=''default'' row was accepted after a promotion';
EXCEPTION
  WHEN unique_violation THEN NULL;   -- the index fired — what we assert
END $$;

SELECT tests.assert(
  (SELECT count(*) FROM public.graphs
    WHERE org_id = '10180-promote' AND kind = 'default'
      AND status <> 'deleted') = 1,
  '#10180: the failed second default left exactly one live default row');

-- ── 3. Idempotence: promoting the CURRENT default writes nothing ───────────
-- A retry must not be an error, and must not demote-and-re-promote (churn).
-- Captured as a before/after snapshot of the rows the move touches.
CREATE TEMP TABLE _10180_before AS
  SELECT g.id, g.kind, g.namespace, o.graph_name
    FROM public.graphs g JOIN public.organizations o ON o.id = g.org_id
   WHERE g.org_id = '10180-promote';

SELECT tests.assert(
  (public.promote_default_graph('10180-promote', 'g_10180promote0002')
     ->> 'already_default')::boolean,
  '#10180: promoting the current default reports already_default');

SELECT tests.assert(
  NOT EXISTS (
    SELECT 1 FROM public.graphs g
      JOIN public.organizations o ON o.id = g.org_id
      JOIN _10180_before b ON b.id = g.id
     WHERE g.org_id = '10180-promote'
       AND (g.kind, g.namespace, o.graph_name)
           IS DISTINCT FROM (b.kind, b.namespace, b.graph_name)),
  '#10180: the idempotent arm moved no graph row (kind, namespace and org pointer unchanged)');

-- ── 3b. …and it REPAIRS a drifted org namespace ──────────────────────────
-- §3 of 20261028000001 pins "a kind='default' row's namespace IS the org's
-- graph_name". An org whose pair drifted (a hand-fixed row, a half-applied
-- backfill) used to be told `ok` by the ONE call whose contract is that they
-- agree — the drift then survived the very verb that exists to converge them.
-- Re-assert the post-state, because this arm is the only one that writes
-- without moving a default.
UPDATE public.organizations SET graph_name = 'org_10180_promote'
 WHERE id = '10180-promote';

SELECT tests.assert(
  (public.promote_default_graph('10180-promote', 'g_10180promote0002')
     ->> 'namespace_repaired')::boolean,
  '#10180: promoting the current default REPORTS the namespace repair');

SELECT tests.assert(
  (SELECT graph_name FROM public.organizations WHERE id = '10180-promote')
    = 'org_10180_promote_g_10180promote0002',
  '#10180: the idempotent arm converged organizations.graph_name onto the default row''s namespace');

SELECT tests.assert(
  (SELECT kind FROM public.graphs WHERE id = 'g_10180promote0002') = 'default'
  AND (SELECT kind FROM public.graphs WHERE id = 'g_10180default0001') = 'custom',
  '#10180: …without moving either graph row (the repair is not a re-promotion)');

-- ── 4. Refusals name the state, and roll the move back ────────────────────
-- Each refusal is driven with a TWO-step promotion available so the failure
-- path is the interesting one: a raise must leave the default where it was,
-- never with none (the whole point of doing this in one transaction).

-- (a) unknown / foreign graph — no existence oracle: same error either way.
DO $$
BEGIN
  PERFORM public.promote_default_graph('10180-promote', 'g_10180nothere00');
  RAISE EXCEPTION 'ASSERTION FAILED: an unknown graph id was accepted';
EXCEPTION WHEN others THEN
  IF SQLERRM NOT LIKE 'promote_default_graph: graph not found%' THEN
    RAISE;
  END IF;
END $$;

DO $$
BEGIN
  PERFORM public.promote_default_graph('10180-promote', 'g_10180foreign0003');
  RAISE EXCEPTION 'ASSERTION FAILED: another org''s graph was promoted';
EXCEPTION WHEN others THEN
  IF SQLERRM NOT LIKE 'promote_default_graph: graph not found%' THEN
    RAISE;
  END IF;
END $$;

-- (b) a TRASHED target — refused by name, with a different remedy than 404.
DO $$
BEGIN
  INSERT INTO public.graphs (id, org_id, name, kind, namespace, status)
  VALUES ('g_10180trashed005', '10180-promote', 'trashed', 'custom',
          'org_10180_promote_trashed', 'deleted');
  PERFORM public.promote_default_graph('10180-promote', 'g_10180trashed005');
  RAISE EXCEPTION 'ASSERTION FAILED: a tombstoned graph was promoted';
EXCEPTION WHEN others THEN
  IF SQLERRM NOT LIKE 'promote_default_graph: graph is in the trash%' THEN
    RAISE;
  END IF;
END $$;

-- (c) a non-active status (`graphs.status` is free text with no CHECK).
DO $$
BEGIN
  INSERT INTO public.graphs (id, org_id, name, kind, namespace, status)
  VALUES ('g_10180archived06', '10180-promote', 'archived', 'custom',
          'org_10180_promote_archived', 'archived');
  PERFORM public.promote_default_graph('10180-promote', 'g_10180archived06');
  RAISE EXCEPTION 'ASSERTION FAILED: a non-active graph was promoted';
EXCEPTION WHEN others THEN
  IF SQLERRM NOT LIKE 'promote_default_graph: graph is not active%' THEN
    RAISE;
  END IF;
END $$;

-- (d) an unknown organization.
DO $$
BEGIN
  PERFORM public.promote_default_graph('10180-nobody', 'g_10180promote0002');
  RAISE EXCEPTION 'ASSERTION FAILED: an unknown organization was accepted';
EXCEPTION WHEN others THEN
  IF SQLERRM NOT LIKE 'promote_default_graph: unknown organization%' THEN
    RAISE;
  END IF;
END $$;

-- After all four refusals the state is EXACTLY as the successful move left it:
-- still one live default, still the promoted graph, org pointer unchanged.
SELECT tests.assert(
  (SELECT count(*) FROM public.graphs
    WHERE org_id = '10180-promote' AND kind = 'default'
      AND status <> 'deleted') = 1,
  '#10180: the refusals left exactly one live default (never zero)');

SELECT tests.assert(
  (SELECT id FROM public.graphs
    WHERE org_id = '10180-promote' AND kind = 'default'
      AND status <> 'deleted') = 'g_10180promote0002',
  '#10180: a refused promotion did not disturb the current default');

SELECT tests.assert(
  (SELECT graph_name FROM public.organizations WHERE id = '10180-promote')
    = 'org_10180_promote_g_10180promote0002',
  '#10180: a refused promotion did not disturb the org namespace');

-- ── 5. Atomicity: a rollback mid-move cannot leave ZERO defaults ──────────
-- The one failure mode the "never two" assertion cannot see. Force it: perform
-- the SAME demote/promote the function performs, inside a subtransaction that
-- then raises, and prove the outer state is untouched. (The function itself is
-- one transaction, which is why its own refusals above are already safe — this
-- pins the MECHANISM the file's header claims, not just the outcome.)
DO $$
DECLARE
  d_before text;
BEGIN
  BEGIN
    UPDATE public.graphs SET kind = 'custom'
     WHERE org_id = '10180-promote' AND kind = 'default'
       AND status <> 'deleted';
    UPDATE public.graphs SET kind = 'default'
     WHERE id = 'g_10180default0001';
    UPDATE public.organizations SET graph_name = 'org_10180_promote'
     WHERE id = '10180-promote';
    RAISE EXCEPTION 'drill: abort after the demote';
  EXCEPTION WHEN others THEN
    IF SQLERRM <> 'drill: abort after the demote' THEN RAISE; END IF;
  END;
  SELECT id INTO d_before FROM public.graphs
   WHERE org_id = '10180-promote' AND kind = 'default' AND status <> 'deleted';
  IF d_before IS NULL THEN
    RAISE EXCEPTION
      'ASSERTION FAILED: the aborted move left the org with ZERO default rows';
  END IF;
END $$;

SELECT tests.assert(
  (SELECT id FROM public.graphs
    WHERE org_id = '10180-promote' AND kind = 'default'
      AND status <> 'deleted') = 'g_10180promote0002',
  '#10180: an aborted mid-move transaction rolled BOTH statements back');

-- ── 6. The serialization point is in the function, not in the caller ──────
-- Two callers must never read the same "current default" concurrently: the
-- second would demote it, then its own target, and one of the two would hit
-- the unique index (a 500 for a caller who did nothing wrong). The lock is
-- `FOR UPDATE` on the ORGANIZATION row, taken BEFORE the target is read.
-- PGlite is ONE session, so a real two-transaction race is not expressible
-- here; `tests/test_10180_promote_default_graph.py` pins the shape of the
-- statement and this asserts the effect that matters at the DB level: a SECOND
-- promotion of another graph still lands on exactly one default.
INSERT INTO public.graphs (id, org_id, name, kind, namespace, status)
VALUES ('g_10180third00007', '10180-promote', 'third', 'custom',
        'org_10180_promote_third', 'active');

SELECT public.promote_default_graph('10180-promote', 'g_10180third00007');
SELECT tests.assert(
  (SELECT count(*) FROM public.graphs
    WHERE org_id = '10180-promote' AND kind = 'default'
      AND status <> 'deleted') = 1,
  '#10180: a second, sequential promotion still leaves exactly ONE live default');

SELECT tests.assert(
  (SELECT id FROM public.graphs
    WHERE org_id = '10180-promote' AND kind = 'default'
      AND status <> 'deleted') = 'g_10180third00007',
  '#10180: the second promotion took effect (the lock did not strand it)');

SELECT tests.assert(
  (SELECT kind FROM public.graphs WHERE id = 'g_10180promote0002') = 'custom',
  '#10180: the previously promoted graph was demoted in turn');

-- ── 7. Cleanup (the harness is shared; this suite owns only its own rows) ──
DELETE FROM public.graphs WHERE org_id LIKE '10180-%';
DELETE FROM public.organizations WHERE id LIKE '10180-%';
DROP TABLE IF EXISTS _10180_before;
