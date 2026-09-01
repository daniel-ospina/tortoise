-- ============================================================================
-- SQL-level verification for migration 20260901000001 (epic #2083 child C1
-- #2110 — graphs table + api_keys graph scope columns + escalation CHECK).
-- NOTE: assertion messages use the 'c1' shorthand for this migration.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--   (harness applies ALL migrations incl. c1, then runs this suite with
--   ON_ERROR_STOP semantics — every assertion RAISEs on failure)
--
-- Covers test-design #2094 surfaces 1 (graphs table), 2 (api_keys columns +
-- CHECK), 13 (migration integrity) + E2E-4's data-model half (the direct
-- INSERT → chk_minted_key_no_escalation violation assertion).
-- ============================================================================

-- ── Assertion helper (matches harness convention) ──────────────────────────
CREATE SCHEMA IF NOT EXISTS tests;
CREATE OR REPLACE FUNCTION tests.assert(cond boolean, msg text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ASSERTION FAILED: %', msg;
  END IF;
END $$;
GRANT USAGE ON SCHEMA tests TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION tests.assert(boolean, text) TO anon, authenticated, service_role;
GRANT USAGE ON SCHEMA auth TO anon, authenticated, service_role;
GRANT ALL ON auth.users TO service_role;

-- ── 1. Schema presence (surface 1) ────────────────────────────────────────
SELECT tests.assert(
  EXISTS (SELECT 1 FROM information_schema.tables
          WHERE table_schema = 'public' AND table_name = 'graphs'),
  'c1: graphs table exists');
SELECT tests.assert(
  (SELECT count(*) FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'graphs'
   AND column_name IN ('id','team_id','name','kind','namespace','status','recording','created_at')) = 8,
  'c1: graphs has all 8 columns');
SELECT tests.assert(
  (SELECT count(*) FROM information_schema.columns
   WHERE table_schema = 'public' AND table_name = 'api_keys'
   AND column_name IN ('graph_id','scopes','created_by_key_id','delegation_depth')) = 4,
  'c1: api_keys has ALL 4 new columns');
SELECT tests.assert(
  (SELECT pg_get_constraintdef(oid) FROM pg_constraint
   WHERE conname = 'chk_minted_key_no_escalation'
   AND connamespace = 'public'::regnamespace)
  LIKE '%jsonb_typeof(scopes) = %array%'
  AND (SELECT pg_get_constraintdef(oid) FROM pg_constraint
       WHERE conname = 'chk_minted_key_no_escalation'
       AND connamespace = 'public'::regnamespace)
      LIKE '%graphs:create%team:manage%',
  'c1: chk_minted_key_no_escalation exists with the flat-array + escalation predicate');
SELECT tests.assert(
  EXISTS (SELECT 1 FROM pg_indexes
          WHERE schemaname = 'public' AND tablename = 'graphs'
          AND indexname = 'uq_graphs_team_name_active'),
  'c1: partial unique index uq_graphs_team_name_active exists');
SELECT tests.assert(
  (SELECT pg_get_indexdef('uq_graphs_team_name_active'::regclass))
  LIKE '%WHERE (status <> %deleted%' OR
  (SELECT pg_get_indexdef('uq_graphs_team_name_active'::regclass))
  LIKE '%WHERE status <> %deleted%',
  'c1: uq_graphs_team_name_active is the partial (active-only) index');

-- ── 2. CHECK enforcement (surface 2 + E2E-4 half) ─────────────────────────
-- Seed a team + graph first (service_role).
SET ROLE service_role;
INSERT INTO public.teams (id, name, tier, graph_name)
VALUES ('c1-team-000000000000', 'c1-team', 'free', 'team_c1-team-000000000000');
INSERT INTO public.graphs (id, team_id, name, kind, namespace, status)
VALUES ('c1-graph-000000000001', 'c1-team-000000000000', 'prod', 'custom',
        'team_c1-team-000000000000_g_c1-graph-000000000001', 'active');
INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix, created_via)
VALUES ('c1-key-legacy-000001', 'c1-team-000000000000',
        'sha256hexlegacy1', 'c1-legacy-', 'provisioned');
-- (remainder of section 2 runs as service_role: authenticated has no
-- INSERT grant on api_keys (0007 grants SELECT only); FK RI checks run as
-- the table owner, so grants are not the constraint — this mirrors the
-- backend-only-writes model).

-- Graph-bound minted key (deleg=0) with escalation scope → VIOLATION (E2E-4)
DO $$
BEGIN
  BEGIN
    INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix,
                                 graph_id, scopes, delegation_depth)
    VALUES ('c1-key-bad1', 'c1-team-000000000000', 'sha256hexbad1', 'c1-bad1-',
            'c1-graph-000000000001', '["graphs:create"]'::jsonb, 0);
    RAISE EXCEPTION 'c1: graph-bound minted escalation NOT rejected' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN
    NULL; -- expected
  END;
END $$;

-- Team-wide minted key (deleg=0, graph_id NULL) with escalation → VIOLATION
-- (the CHECK is table-level — covers graph-bound AND team-wide minted keys)
DO $$
BEGIN
  BEGIN
    INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix,
                                 scopes, delegation_depth)
    VALUES ('c1-key-bad2', 'c1-team-000000000000', 'sha256hexbad2', 'c1-bad2-',
            '["keys:manage"]'::jsonb, 0);
    RAISE EXCEPTION 'c1: team-wide minted escalation NOT rejected' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN
    NULL; -- expected
  END;
END $$;

-- team:manage is escalation too (full team-admin capability) → VIOLATION
DO $$
BEGIN
  BEGIN
    INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix,
                                 scopes, delegation_depth)
    VALUES ('c1-key-bad4', 'c1-team-000000000000', 'sha256hexbad4', 'c1-bad4-',
            '["team:manage"]'::jsonb, 0);
    RAISE EXCEPTION 'c1: team:manage on minted key NOT rejected' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN
    NULL; -- expected
  END;
END $$;

-- NESTED shape ({"graphs":["create"]}) on a minted key → VIOLATION (the
-- jsonb_typeof guard enforces FLAT storage — a nested object would make
-- `?|` vacuous and the escalation invariant would silently die)
DO $$
BEGIN
  BEGIN
    INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix,
                                 scopes, delegation_depth)
    VALUES ('c1-key-bad5', 'c1-team-000000000000', 'sha256hexbad5', 'c1-bad5-',
            '{"graphs":["create"]}'::jsonb, 0);
    RAISE EXCEPTION 'c1: nested scopes on minted key NOT rejected' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN
    NULL; -- expected
  END;
END $$;

-- Escalation scope on team-wide minted key with graph_id NULL + deleg=0 → VIOLATION
DO $$
BEGIN
  BEGIN
    INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix,
                                 scopes, delegation_depth)
    VALUES ('c1-key-bad3', 'c1-team-000000000000', 'sha256hexbad3', 'c1-bad3-',
            '["graphs:delete"]'::jsonb, 0);
    RAISE EXCEPTION 'c1: graphs:delete on minted key NOT rejected' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN
    NULL; -- expected
  END;
END $$;

-- Owner-minted (deleg NULL) escalation scope → ALLOWED (only owners may hold)
INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix,
                             scopes, delegation_depth)
VALUES ('c1-key-owner1', 'c1-team-000000000000', 'sha256hexown1', 'c1-own1-',
        '["graphs:create"]'::jsonb, NULL);

-- Minted key with NON-escalation scope → ALLOWED (read/write fine)
INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix,
                             scopes, delegation_depth)
VALUES ('c1-key-mint1', 'c1-team-000000000000', 'sha256hexmnt1', 'c1-mnt1-',
        '["graphs:read","graphs:write"]'::jsonb, 0);

-- scopes default is the empty allowlist (all-off)
SELECT tests.assert(
  (SELECT scopes FROM public.api_keys WHERE id = 'c1-key-legacy-000001') = '[]'::jsonb,
  'c1: scopes defaults to empty allowlist on pre-existing keys (legacy class)');
SELECT tests.assert(
  (SELECT delegation_depth IS NULL FROM public.api_keys WHERE id = 'c1-key-legacy-000001'),
  'c1: pre-existing keys keep delegation_depth NULL (owner/legacy class)');
RESET ROLE;

-- ── 3. Partial unique index + name reuse (surface 1) ──────────────────────
SET ROLE service_role;
DO $$
BEGIN
  BEGIN
    INSERT INTO public.graphs (id, team_id, name, kind, namespace)
    VALUES ('c1-graph-000000000002', 'c1-team-000000000000', 'prod', 'custom',
            'team_c1-team-000000000000_g_c1-graph-000000000002');
    RAISE EXCEPTION 'c1: duplicate active (team_id,name) NOT rejected' USING ERRCODE = 'P0002';
  EXCEPTION WHEN unique_violation THEN
    NULL; -- expected
  END;
END $$;
RESET ROLE;

-- Soft-delete frees the name → reuse allowed (tombstone doesn't squat)
SET ROLE service_role;
UPDATE public.graphs SET status = 'deleted' WHERE id = 'c1-graph-000000000001';
INSERT INTO public.graphs (id, team_id, name, kind, namespace)
VALUES ('c1-graph-000000000003', 'c1-team-000000000000', 'prod', 'custom',
        'team_c1-team-000000000000_g_c1-graph-000000000003');
RESET ROLE;

-- ── 4. FK cascade (surface 1) ─────────────────────────────────────────────
SET ROLE service_role;
INSERT INTO public.graphs (id, team_id, name, kind, namespace)
VALUES ('c1-graph-000000000004', 'c1-team-000000000000', 'cascade-graph', 'custom',
        'team_c1-team-000000000000_g_c1-graph-000000000004');
INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix,
                             graph_id, scopes, delegation_depth)
VALUES ('c1-key-gbound1', 'c1-team-000000000000', 'sha256hexgb1', 'c1-gb1-',
        'c1-graph-000000000004', '["graphs:read"]'::jsonb, 0);
DELETE FROM public.graphs WHERE id = 'c1-graph-000000000004';
SELECT tests.assert(
  NOT EXISTS (SELECT 1 FROM public.api_keys WHERE id = 'c1-key-gbound1'),
  'c1: deleting a graph cascades to its bound keys (ON DELETE CASCADE)');
-- Team delete cascades graphs (and thus their keys)
DELETE FROM public.teams WHERE id = 'c1-team-000000000000';
SELECT tests.assert(
  NOT EXISTS (SELECT 1 FROM public.graphs WHERE team_id = 'c1-team-000000000000'),
  'c1: team delete cascades graphs');
SELECT tests.assert(
  NOT EXISTS (SELECT 1 FROM public.api_keys WHERE team_id = 'c1-team-000000000000'),
  'c1: team delete cascades keys');
RESET ROLE;

-- ── 5. RLS — tenant GUC (surface 1, mirrors 0006 pattern) ─────────────────
SET ROLE service_role;
INSERT INTO public.teams (id, name, tier, graph_name)
VALUES ('c1-team-000000000001', 'c1-team-b', 'free', 'team_c1-team-000000000001');
INSERT INTO public.graphs (id, team_id, name, kind, namespace, status)
VALUES ('c1-graph-000000000010', 'c1-team-000000000001', 'b-graph', 'custom',
        'team_c1-team-000000000001_g_c1-graph-000000000010', 'active');
-- A FOREIGN team's graph MUST be invisible to the GUC-scoped reader — the
-- cross-tenant exclusion assertion (code-review P1: a policy that only
-- checks the GUC is set, not that it matches team_id, would pass the
-- count-only assertions).
INSERT INTO public.teams (id, name, tier, graph_name)
VALUES ('c1-team-000000000002', 'c1-team-foreign', 'free', 'team_c1-team-000000000002');
INSERT INTO public.graphs (id, team_id, name, kind, namespace, status)
VALUES ('c1-graph-000000000011', 'c1-team-000000000002', 'foreign-graph', 'custom',
        'team_c1-team-000000000002_g_c1-graph-000000000011', 'active');
RESET ROLE;

-- GUC unset → 0 rows (deny-by-default for direct browser access)
SET ROLE authenticated;
SELECT tests.assert(
  (SELECT count(id) FROM public.graphs) = 0,
  'c1 RLS: authenticated with unset GUC sees 0 graphs (deny-by-default)');
RESET ROLE;

-- GUC set to own team → own graphs only, FOREIGN team's graph excluded
SET ROLE authenticated;
SET app.current_team_id = 'c1-team-000000000001';
SELECT tests.assert(
  (SELECT count(id) FROM public.graphs) = 1
  AND (SELECT id FROM public.graphs LIMIT 1) = 'c1-graph-000000000010',
  'c1 RLS: GUC-scoped read sees own team graphs only (foreign excluded)');
RESET ROLE;
RESET app.current_team_id;

-- anon has NO access at all (REVOKE ALL) — assert privilege absence, not a
-- query (anon can't even SELECT, which is the stronger correct behavior).
SELECT tests.assert(
  has_table_privilege('anon', 'public.graphs', 'SELECT') = false
  AND has_table_privilege('anon', 'public.graphs', 'INSERT') = false,
  'c1 RLS: anon has no SELECT/INSERT on graphs');

-- service_role → all (backend management)
SET ROLE service_role;
SELECT tests.assert(
  (SELECT count(id) FROM public.graphs WHERE team_id = 'c1-team-000000000001') >= 1,
  'c1 RLS: service_role sees all graphs');
RESET ROLE;

-- ── Cleanup test rows ──────────────────────────────────────────────────────
SET ROLE service_role;
DELETE FROM public.teams WHERE id IN
  ('c1-team-000000000000','c1-team-000000000001','c1-team-000000000002');
DELETE FROM public.graphs WHERE team_id LIKE 'c1-team-%';
DELETE FROM public.api_keys WHERE team_id LIKE 'c1-team-%';
RESET ROLE;
