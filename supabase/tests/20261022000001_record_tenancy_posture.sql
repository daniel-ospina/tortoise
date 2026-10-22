-- ============================================================================
-- SQL-level verification for the record's TENANCY POSTURE (R6, epic #7879).
--
-- The recorded decision this file proves (full text and evidence:
-- `docs/epics/2026-10-10-7879-postgres-record/09-record-tenancy-decision.md`,
-- encoded in `supabase/migrations/20261022000001_record_tenancy_role_and_guc.sql`):
--
--   · the record's application path connects as `tortoise_record_app`, which is
--     `NOBYPASSRLS` — so RLS is the boundary, not a decoration;
--   · `app.current_org_id` is set TRANSACTION-LOCALLY by
--     `record_projection.replay()` from the same `org_id` it projects;
--   · the failure signature is asymmetric BY DESIGN: a missing GUC reads as
--     ZERO ROWS (silently — indistinguishable from "no data"), and WRITES as an
--     error. Both directions are asserted below, because only the pair
--     distinguishes "the tenant boundary works" from "the table is empty".
--
-- Covers test-design #7967 surfaces:
--   S7 (tenancy / RLS): the posture's role, policies and effective privileges;
--                       a cross-tenant read returning 0 rows under that
--                       posture; a table whose policy set drifted reddens here;
--   S2 (write chokepoint): the writer's `org_id` and the connection's GUC
--                       cannot disagree — a disagreement is a refused write.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
-- (the harness applies ALL migrations, then runs this suite with ON_ERROR_STOP:
-- every assertion RAISEs on failure)
--
-- ⛔ WHAT THIS FILE IS NOT. It is not a second copy of
-- `20261012000001_record_schema.sql`'s tenancy block: that one proves the
-- `authenticated` READ surface is RLS-scoped. This one proves the posture the
-- WRITE PATH runs under — the role, its attributes, its grants, and the two
-- failure directions of the GUC. Both are needed and neither implies the other.
--
-- ⛔ NO MIGRATION-SAFETY ASSERTIONS. Owner scope ruling 2026-10-10 — there are
-- no existing customers. A dual-write / backfill / cutover / rollback test here
-- would be a defect in this file.
-- ============================================================================

-- ── Assertion helper (matches the harness convention; idempotent) ──────────
CREATE SCHEMA IF NOT EXISTS tests;
CREATE OR REPLACE FUNCTION tests.assert(cond boolean, msg text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ASSERTION FAILED: %', msg;
  END IF;
END $$;
GRANT USAGE ON SCHEMA tests TO anon, authenticated, service_role, tortoise_record_app;
GRANT EXECUTE ON FUNCTION tests.assert(boolean, text)
    TO anon, authenticated, service_role, tortoise_record_app;

-- ── 0. Seed TWO tenants, one row per record table each ─────────────────────
-- The pair is what makes every assertion below non-vacuous: a table with only
-- tenant A's rows cannot distinguish "RLS hid tenant B" from "tenant B has
-- nothing here", and that ambiguity is exactly how a tenancy test passes while
-- proving nothing.
INSERT INTO public.organizations (id, name, graph_name)
VALUES ('org_r6_a', 'record-tenancy-a', 'record-tenancy-a'),
       ('org_r6_b', 'record-tenancy-b', 'record-tenancy-b')
ON CONFLICT (id) DO NOTHING;
INSERT INTO public.graphs (id, org_id, name, namespace)
VALUES ('g_r6_a', 'org_r6_a', 'record-tenancy-a', 'org_org_r6_a_g_r6_a'),
       ('g_r6_b', 'org_r6_b', 'record-tenancy-b', 'org_org_r6_b_g_r6_b')
ON CONFLICT (id) DO NOTHING;

-- The identity rows the layer tables' composite FKs point at. Seeded for BOTH
-- tenants so that a refused cross-tenant write below can only be refused by the
-- POLICY — a missing FK target would make it pass for the wrong reason.
INSERT INTO public.node_identity (org_id, graph_id, node_label, node_key)
VALUES ('org_r6_a','g_r6_a','Point','p_r6_a'),
       -- The key the correctly-scoped WRITE in §7 lands on. Seeded here (as the
       -- owner) so that insert is refused — or accepted — by the POLICY alone:
       -- a missing identity row would make its FK refusal look like a boundary.
       ('org_r6_a','g_r6_a','Point','p_r6_a_new'),
       ('org_r6_a','g_r6_a','Object','o_r6_a'),
       ('org_r6_a','g_r6_a','Subject','s_r6_a'),
       ('org_r6_a','g_r6_a','Source','https://example.test/r6a'),
       ('org_r6_a','g_r6_a','Event','e_r6_a'),
       ('org_r6_a','g_r6_a','Session','sess_r6_a'),
       ('org_r6_b','g_r6_b','Point','p_r6_b'),
       -- A key tenant B does NOT hold a row for yet: §7's cross-tenant write uses
       -- it so the only thing that can refuse that INSERT is the POLICY (a
       -- reused key would refuse it as a duplicate instead, and the two are
       -- indistinguishable in a caught error).
       ('org_r6_b','g_r6_b','Point','p_r6_b_new'),
       ('org_r6_b','g_r6_b','Object','o_r6_b'),
       ('org_r6_b','g_r6_b','Subject','s_r6_b'),
       ('org_r6_b','g_r6_b','Source','https://example.test/r6b'),
       ('org_r6_b','g_r6_b','Event','e_r6_b'),
       ('org_r6_b','g_r6_b','Session','sess_r6_b')
ON CONFLICT DO NOTHING;

INSERT INTO public.point (org_id, graph_id, node_key, id, content) VALUES
    ('org_r6_a','g_r6_a','p_r6_a','p_r6_a','a claim from A'),
    ('org_r6_b','g_r6_b','p_r6_b','p_r6_b','a claim from B')
ON CONFLICT DO NOTHING;
INSERT INTO public.object (org_id, graph_id, node_key, name, object_kind) VALUES
    ('org_r6_a','g_r6_a','o_r6_a','o_r6_a','other'),
    ('org_r6_b','g_r6_b','o_r6_b','o_r6_b','other')
ON CONFLICT DO NOTHING;
INSERT INTO public.subject (org_id, graph_id, node_key, name) VALUES
    ('org_r6_a','g_r6_a','s_r6_a','s_r6_a'),
    ('org_r6_b','g_r6_b','s_r6_b','s_r6_b')
ON CONFLICT DO NOTHING;
INSERT INTO public.source (org_id, graph_id, node_key, url) VALUES
    ('org_r6_a','g_r6_a','https://example.test/r6a','https://example.test/r6a'),
    ('org_r6_b','g_r6_b','https://example.test/r6b','https://example.test/r6b')
ON CONFLICT DO NOTHING;
INSERT INTO public.event (org_id, graph_id, node_key, event_id, event_kind, content) VALUES
    ('org_r6_a','g_r6_a','e_r6_a','e_r6_a','decision','an occurrence from A'),
    ('org_r6_b','g_r6_b','e_r6_b','e_r6_b','decision','an occurrence from B')
ON CONFLICT DO NOTHING;
INSERT INTO public.session (org_id, graph_id, node_key, id) VALUES
    ('org_r6_a','g_r6_a','sess_r6_a','sess_r6_a'),
    ('org_r6_b','g_r6_b','sess_r6_b','sess_r6_b')
ON CONFLICT DO NOTHING;
INSERT INTO public.relation (org_id, graph_id, rel_type, src_label, src_key,
                             tgt_label, tgt_key, confidence) VALUES
    ('org_r6_a','g_r6_a','IMPL','Point','p_r6_a','Object','o_r6_a', 0.85),
    ('org_r6_b','g_r6_b','IMPL','Point','p_r6_b','Object','o_r6_b', 0.85)
ON CONFLICT DO NOTHING;
INSERT INTO public.journal_event (org_id, graph_id, seq, event_type, node_label,
                                  node_key, payload) VALUES
    ('org_r6_a','g_r6_a', 1, 'PointAdded', 'Point', 'p_r6_a',
     '{"type":"PointAdded","ts":"2026-10-22T00:00:00Z"}'::jsonb),
    ('org_r6_b','g_r6_b', 1, 'PointAdded', 'Point', 'p_r6_b',
     '{"type":"PointAdded","ts":"2026-10-22T00:00:00Z"}'::jsonb)
ON CONFLICT DO NOTHING;

-- ── 1. The posture: the role ────────────────────────────────────────────────
SELECT tests.assert(
  (SELECT count(*) FROM pg_roles
   WHERE rolname = 'tortoise_record_app'
     AND NOT rolbypassrls AND NOT rolsuper AND NOT rolcreatedb AND NOT rolcreaterole) = 1,
  'R6: tortoise_record_app exists and is NOBYPASSRLS (RLS is the boundary, not a decoration)');

SELECT tests.assert(
  NOT EXISTS (SELECT 1 FROM pg_auth_members m
              JOIN pg_roles granted ON granted.oid = m.roleid
              JOIN pg_roles member  ON member.oid  = m.member
              WHERE member.rolname = 'tortoise_record_app'
                AND granted.rolname IN ('service_role','postgres','supabase_admin')),
  'R6: the app role is not a member of a BYPASSRLS role (membership would silently re-open the bypass)');

-- ── 2. The posture: one tenant policy per table, with BOTH clauses ─────────
SELECT tests.assert(
  (SELECT count(*) FROM pg_policies
   WHERE schemaname = 'public'
     AND tablename IN ('node_identity','journal_event','source','event','session',
                       'object','subject','point','relation')
     AND roles = ARRAY['tortoise_record_app']::name[]
     AND cmd = 'ALL'
     AND qual       LIKE '%current_setting%'
     AND with_check LIKE '%current_setting%') = 9,
  'R6: every record table carries the app role''s tenant policy with USING AND WITH CHECK');

-- The posture is uniform, and that is the claim: 3 policies per table (the
-- `authenticated` read, the `service_role` bypass, and the app role's) and no
-- fourth, unexplained one.
SELECT tests.assert(
  (SELECT count(*) FROM pg_policies
   WHERE schemaname = 'public'
     AND tablename IN ('node_identity','journal_event','source','event','session',
                       'object','subject','point','relation')) = 27,
  'R6: every record table has exactly the three policies the decision explains');

-- ── 3. The posture: effective privileges, not the statements that made them ─
SELECT tests.assert(
  (SELECT count(*) FROM unnest(ARRAY['node_identity','journal_event','source','event',
                                     'session','object','subject','point','relation']) AS t
   WHERE has_table_privilege('tortoise_record_app', 'public.' || t, 'SELECT')
     AND has_table_privilege('tortoise_record_app', 'public.' || t, 'INSERT')
     AND has_table_privilege('tortoise_record_app', 'public.' || t, 'UPDATE')
     AND has_table_privilege('tortoise_record_app', 'public.' || t, 'DELETE')) = 9,
  'R6: the app role can read and write every record table (the write path needs all four)');

SELECT tests.assert(
  NOT EXISTS (SELECT 1 FROM unnest(ARRAY['node_identity','journal_event','source','event',
                                         'session','object','subject','point','relation']) AS t
              WHERE has_table_privilege('tortoise_record_app', 'public.' || t, 'TRUNCATE')
                 OR has_table_privilege('anon', 'public.' || t, 'SELECT')
                 OR has_table_privilege('anon', 'public.' || t, 'INSERT')
                 OR has_table_privilege('authenticated', 'public.' || t, 'INSERT')),
  'R6: nothing beyond the tenant policy is granted — no TRUNCATE for the app role, nothing for anon, no write for authenticated');

SELECT tests.assert(
  NOT has_table_privilege('tortoise_record_app', 'public.organizations', 'UPDATE'),
  'R6: the app role cannot rewrite the tenant substrate it is keyed on');

-- ── 4. The control plane is untouched (S8, regression only) ─────────────────
SELECT tests.assert(
  has_table_privilege('service_role', 'public.organizations', 'SELECT'),
  'R6 regression: the control plane''s own grants are unchanged');

-- ── 5. Every tenant has its OWN rows — the non-vacuous precondition ────────
-- Read as the owner (BYPASSRLS via ownership), so this is a fact about the
-- SEED, not about the posture.
DO $$
DECLARE
  t text;
  n bigint;
BEGIN
  FOREACH t IN ARRAY ARRAY['node_identity','journal_event','source','event','session',
                           'object','subject','point','relation'] LOOP
    EXECUTE format('SELECT count(*) FROM public.%I', t) INTO n;
    IF n < 2 THEN
      RAISE EXCEPTION 'R6 setup: % holds % row(s) — both tenants must be present or every '
                      'cross-tenant assertion below is vacuous', t, n;
    END IF;
  END LOOP;
END $$;

-- ── 6. The chosen posture: a cross-tenant read returns 0 rows ──────────────
-- The app role, transaction-locally scoped to tenant A. Every record table must
-- report tenant A's rows and nothing else. `count(*) = 1` per table is the
-- paired assertion: "the other tenant's row is invisible" AND "my own row is
-- visible" — a test that only asserted 0 rows would pass on an empty table.
BEGIN;
SET LOCAL ROLE tortoise_record_app;
SELECT set_config('app.current_org_id', 'org_r6_a', true);

DO $$
DECLARE
  t text;
  visible bigint;
  cross_tenant bigint;
BEGIN
  FOREACH t IN ARRAY ARRAY['node_identity','journal_event','source','event','session',
                           'object','subject','point','relation'] LOOP
    EXECUTE format('SELECT count(*) FROM public.%I', t) INTO visible;
    EXECUTE format('SELECT count(*) FROM public.%I WHERE org_id = %L', t, 'org_r6_b')
      INTO cross_tenant;
    IF cross_tenant <> 0 THEN
      RAISE EXCEPTION 'R6: tenant A read % row(s) of tenant B from public.% — RLS is not the boundary',
        cross_tenant, t;
    END IF;
    IF visible < 1 THEN
      RAISE EXCEPTION 'R6: tenant A sees NO rows of its own in public.% — the boundary '
                      'is deny-all, not tenant-scoped', t;
    END IF;
  END LOOP;
END $$;

-- ── 7. The writer cannot file under another tenant (S2) ────────────────────
-- Scoped to A, writing a row for B. Every FK target for B EXISTS (seeded in §0),
-- so the ONLY thing that can refuse this is the policy's `WITH CHECK`.
-- ERRCODE 42501 is PostgreSQL's `insufficient_privilege`, which is what a
-- `WITH CHECK` violation raises — asserted by code, so a FK refusal (23503) or a
-- CHECK refusal (23514) cannot be mistaken for the tenant boundary firing.
DO $$
BEGIN
  BEGIN
    INSERT INTO public.point (org_id, graph_id, node_key, id, content)
    VALUES ('org_r6_b','g_r6_b','p_r6_b_new','p_r6_b_new','filed under the wrong tenant');
    RAISE EXCEPTION 'R6: the app role wrote a row under a tenant it is not scoped to';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;

-- The SAME insert, correctly scoped, succeeds — so §7 fails for the tenant and
-- not for the shape of the row.
DO $$
BEGIN
  INSERT INTO public.point (org_id, graph_id, node_key, id, content)
  VALUES ('org_r6_a','g_r6_a','p_r6_a_new','p_r6_a_new','a claim scoped to the caller')
  ON CONFLICT DO NOTHING;
END $$;
SELECT tests.assert(
  (SELECT count(*) FROM public.point) = 2,
  'R6: a correctly scoped write through the app role lands (and tenant B is still hidden)');
ROLLBACK;

-- ── 8. The DOCUMENTED failure signature: a missing GUC reads as zero rows ──
-- A fresh transaction, so nothing set the GUC. This is the trap the decision
-- accepts deliberately: `current_setting(..., true)` returns NULL, the `USING`
-- predicate filters every row, and NO error is raised. Pinned so that "fixing"
-- the read side into an error becomes a red test rather than a surprise at
-- cutover — and so that a reader cannot mistake an empty result for a working
-- tenant boundary.
BEGIN;
SET LOCAL ROLE tortoise_record_app;

DO $$
DECLARE
  t text;
  visible bigint;
BEGIN
  FOREACH t IN ARRAY ARRAY['node_identity','journal_event','source','event','session',
                           'object','subject','point','relation'] LOOP
    EXECUTE format('SELECT count(*) FROM public.%I', t) INTO visible;
    IF visible <> 0 THEN
      RAISE EXCEPTION 'R6: public.% returned % row(s) with NO tenant GUC set — the policies '
                      'are no longer keyed on it', t, visible;
    END IF;
  END LOOP;
END $$;
ROLLBACK;

-- ── 9. …and a missing GUC makes a WRITE loud, not silent ───────────────────
-- The asymmetry is the safety property: an unscoped reader sees nothing, and an
-- unscoped writer is refused. Both halves are pinned here.
BEGIN;
SET LOCAL ROLE tortoise_record_app;

DO $$
BEGIN
  BEGIN
    INSERT INTO public.point (org_id, graph_id, node_key, id, content)
    VALUES ('org_r6_a','g_r6_a','p_r6_unscoped','p_r6_unscoped','an unscoped write');
    RAISE EXCEPTION 'R6: an unscoped write SUCCEEDED — a missing GUC is silent on write too';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;
ROLLBACK;

-- ── 10. Clean up (the harness spot-checks for leftovers afterwards) ────────
-- Through the ONE production erasure path, as `20261012000001_record_schema.sql`
-- does: `purge_org_record` sets app.record_journal_purge transaction-locally and
-- hard-deletes the org, cascading to every record table.
SET ROLE service_role;
SELECT public.purge_org_record('org_r6_a');
SELECT public.purge_org_record('org_r6_b');
RESET ROLE;

SELECT tests.assert(
  (SELECT count(*) FROM public.point WHERE org_id IN ('org_r6_a','org_r6_b')) = 0,
  'R6: purge_org_record cascaded both tenants out of the record');
SELECT tests.assert(
  (SELECT count(*) FROM public.journal_event WHERE org_id IN ('org_r6_a','org_r6_b')) = 0,
  'R6: purge_org_record cascaded both tenants out of the append-only journal');
