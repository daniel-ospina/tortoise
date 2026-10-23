-- ============================================================================
-- SQL-level verification for the record's TENANCY POSTURE (R6, epic #7879).
--
-- The recorded decision this file proves (full text and evidence:
-- `docs/epics/2026-10-10-7879-postgres-record/09-record-tenancy-decision.md`,
-- encoded in `supabase/migrations/20261025000001_record_tenancy_role_and_guc.sql`):
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
-- The TENTH record table (§9.1 / R7, #9555). It is not one of the nine the
-- layer-1 migrations created, and it is seeded here for the same reason they
-- are: the cross-tenant assertions below must run against a table that HAS a row
-- per tenant, or "tenant A cannot see B's row" passes on an empty table.
INSERT INTO public.session_narrative (org_id, graph_id, session_id, narrative, turn_count) VALUES
    ('org_r6_a','g_r6_a','sess_r6_a','a narrative from A', 2),
    ('org_r6_b','g_r6_b','sess_r6_b','a narrative from B', 2)
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
                AND (granted.rolbypassrls OR granted.rolsuper)),
  'R6: the app role is not a member of a BYPASSRLS role (membership would silently re-open the bypass)');

-- ── 2. The posture: one tenant policy per table, with BOTH clauses ─────────
SELECT tests.assert(
  (SELECT count(*) FROM pg_policies
   WHERE schemaname = 'public'
     AND tablename IN ('node_identity','journal_event','source','event','session',
                       'object','subject','point','relation','session_narrative')
     AND roles = ARRAY['tortoise_record_app']::name[]
     AND cmd = 'ALL'
     AND qual       LIKE '%current_setting%'
     AND with_check LIKE '%current_setting%') = 10,
  'R6: every record table carries the app role''s tenant policy with USING AND WITH CHECK');

-- The posture is uniform, and that is the claim: 3 policies per table (the
-- `authenticated` read, the `service_role` bypass, and the app role's) and no
-- fourth, unexplained one.
SELECT tests.assert(
  (SELECT count(*) FROM pg_policies
   WHERE schemaname = 'public'
     AND tablename IN ('node_identity','journal_event','source','event','session',
                       'object','subject','point','relation','session_narrative')) = 30,
  'R6: every record table has exactly the three policies the decision explains');

-- ── 3. The posture: effective privileges, not the statements that made them ─
-- SELECT + INSERT on all ten: every record write reads back what it wrote, and
-- a refusal is inserted as a `journal_event` row (`record_projection` composes
-- the plan's statements from the same DSN the replay uses).
SELECT tests.assert(
  (SELECT count(*) FROM unnest(ARRAY['node_identity','journal_event','source','event',
                                     'session','object','subject','point','relation',
                                     'session_narrative']) AS t
   WHERE has_table_privilege('tortoise_record_app', 'public.' || t, 'SELECT')
     AND has_table_privilege('tortoise_record_app', 'public.' || t, 'INSERT')) = 10,
  'R6: the app role can read and write every record table (SELECT + INSERT on all ten)');

-- …and UPDATE + DELETE on the NINE that are not the journal: the plan upserts
-- (`ON CONFLICT … DO UPDATE`) the node tables, `node_identity` and
-- `session_narrative`, and composes `DELETE` for those nine.
SELECT tests.assert(
  (SELECT count(*) FROM unnest(ARRAY['node_identity','source','event','session',
                                     'object','subject','point','relation',
                                     'session_narrative']) AS t
   WHERE has_table_privilege('tortoise_record_app', 'public.' || t, 'UPDATE')
     AND has_table_privilege('tortoise_record_app', 'public.' || t, 'DELETE')) = 9,
  'R6: the nine non-journal record tables also carry UPDATE and DELETE (the plan upserts and deletes there)');

-- `journal_event` is the ONE table where all four is the wrong grant, and the
-- suite pins the narrow version so a "make it uniform" tidy-up reddens here.
-- `journal_event_is_append_only` (20261012000001) admits DELETE when
-- `app.record_journal_purge` is `on`, and any role may `set_config` that GUC
-- transaction-locally — so a DELETE grant would hand the boundary role the
-- escape hatch from its own audit trail, and `derived = replay(journal)` would
-- lose the row that proves it. Nothing in the plan updates a journal row either
-- (its one journal statement is an INSERT … ON CONFLICT DO NOTHING).
SELECT tests.assert(
  NOT has_table_privilege('tortoise_record_app', 'public.journal_event', 'UPDATE')
  AND NOT has_table_privilege('tortoise_record_app', 'public.journal_event', 'DELETE'),
  'R6: the app role can neither UPDATE nor DELETE journal rows — append-only is the boundary''s own invariant, not a trigger the boundary role can talk past');

SELECT tests.assert(
  NOT EXISTS (SELECT 1 FROM unnest(ARRAY['node_identity','journal_event','source','event',
                                         'session','object','subject','point','relation',
                                     'session_narrative']) AS t
              WHERE has_table_privilege('tortoise_record_app', 'public.' || t, 'TRUNCATE')
                 OR has_table_privilege('anon', 'public.' || t, 'SELECT')
                 OR has_table_privilege('anon', 'public.' || t, 'INSERT')
                 OR has_table_privilege('authenticated', 'public.' || t, 'INSERT')),
  'R6: nothing beyond the tenant policy is granted — no TRUNCATE for the app role, nothing for anon, no write for authenticated');

SELECT tests.assert(
  NOT has_table_privilege('tortoise_record_app', 'public.organizations', 'UPDATE'),
  'R6: the app role cannot rewrite the tenant substrate it is keyed on');

-- §3b. The IDENTITY sequence is its own ACL, and `has_table_privilege` above
-- reads TRUE while the insert still fails: `public.relation.id` is
-- `GENERATED ALWAYS AS IDENTITY`, whose `nextval` needs USAGE on the sequence
-- itself (#9600 review cycle 2, P1).
SELECT tests.assert(
  has_sequence_privilege('tortoise_record_app', 'public.relation_id_seq', 'USAGE'),
  'R6: the app role holds USAGE on relation_id_seq — a table INSERT privilege does not confer nextval');

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
                           'object','subject','point','relation','session_narrative'] LOOP
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
-- ⛔ NO `BEGIN`/`ROLLBACK` HERE. The harness executes this file as ONE
-- `db.exec` batch, i.e. inside one implicit transaction — so an explicit
-- `ROLLBACK` at the end of §7 aborted the whole file's state from line 1,
-- discarding §0's two-tenant seed AND line 49's `GRANT USAGE ON SCHEMA tests`.
-- §8 and §10 then asserted against EMPTY tables and passed vacuously (measured:
-- `public.point` held 0 rows at §10, and `has_schema_privilege(
-- 'tortoise_record_app','tests','USAGE')` was false — #9600 review cycle 7).
-- `SET LOCAL ROLE` is already transaction-local in this batch, so the sections
-- below run as the app role and §7c RESETs both settings explicitly.
SET LOCAL ROLE tortoise_record_app;
SELECT set_config('app.current_org_id', 'org_r6_a', true);

DO $$
DECLARE
  t text;
  visible bigint;
  cross_tenant bigint;
BEGIN
  FOREACH t IN ARRAY ARRAY['node_identity','journal_event','source','event','session',
                           'object','subject','point','relation','session_narrative'] LOOP
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

-- ── 7b. The ONE identity-column table: `public.relation` ────────────────────
-- `relation.id` is `GENERATED ALWAYS AS IDENTITY`, so an INSERT here needs the
-- table grant AND `USAGE` on `relation_id_seq`. A missing sequence grant
-- raises 42501 (the same code a policy refusal uses), so the POSITIVE case is
-- the decisive one: without the grant this block fails, and without the table
-- grant or the policy it fails too. `idx = 7` clears `uq_relation_identity`,
-- so a unique violation cannot masquerade as either outcome.
DO $$
BEGIN
  INSERT INTO public.relation (org_id, graph_id, rel_type, src_label, src_key,
                               tgt_label, tgt_key, idx, confidence)
  VALUES ('org_r6_a','g_r6_a','IMPL','Point','p_r6_a','Object','o_r6_a', 7, 0.5);
EXCEPTION WHEN OTHERS THEN
  RAISE EXCEPTION 'R6: an app-role INSERT into public.relation failed (%) — the '
                  'identity sequence needs its own USAGE grant; a table INSERT '
                  'privilege does not confer nextval', SQLERRM;
END $$;
SELECT tests.assert(
  (SELECT count(*) FROM public.relation WHERE idx = 7) = 1,
  'R6: the app-role relation row LANDED (the identity sequence is granted)');

-- Its cross-tenant twin, refused by the POLICY. Distinct `idx`, and every
-- FK target seeded in §0, so nothing but the WITH CHECK can refuse it.
DO $$
BEGIN
  BEGIN
    INSERT INTO public.relation (org_id, graph_id, rel_type, src_label, src_key,
                                 tgt_label, tgt_key, idx)
    VALUES ('org_r6_b','g_r6_b','IMPL','Point','p_r6_b','Object','o_r6_b', 7);
    RAISE EXCEPTION 'R6: the app role wrote a RELATION row under a tenant it is not scoped to';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;
SELECT tests.assert(
  (SELECT count(*) FROM public.relation) = 2,
  'R6: the app role sees only its own relation rows (the seeded one + the one it wrote)');

-- ── 7c. Leave the app-role scope (§6-§7) and KEEP the data ──────────────────
-- The counterpart of the removed `ROLLBACK`: clear the role and the GUC so §8's
-- "no GUC set" case is the real one, WITHOUT discarding the seeded tenants the
-- later sections assert against.
RESET ROLE;
SELECT set_config('app.current_org_id', '', true);
RESET app.current_org_id;

-- ── 8. The DOCUMENTED failure signature: a missing GUC reads as zero rows ──
-- A fresh transaction, so nothing set the GUC. This is the trap the decision
-- accepts deliberately: `current_setting(..., true)` returns NULL, the `USING`
-- predicate filters every row, and NO error is raised. Pinned so that "fixing"
-- the read side into an error becomes a red test rather than a surprise at
-- cutover — and so that a reader cannot mistake an empty result for a working
-- tenant boundary.
-- ⛔ No `BEGIN`/`ROLLBACK` (same reason as §6-§7: this batch is one
-- transaction, so a ROLLBACK here would discard §0's seed and make the very
-- assertion below vacuous). ANTI-VACUITY first: the unscoped tables must HAVE
-- rows through a role that can see them, or "zero rows" proves nothing.
SELECT tests.assert(
  (SELECT count(*) FROM public.point WHERE org_id IN ('org_r6_a','org_r6_b')) = 3,
  'R6: the seeded tenants are still present before the missing-GUC case (two seeded + the one row 7 wrote) — otherwise "zero rows" is an empty table, not a boundary');
SET LOCAL ROLE tortoise_record_app;

DO $$
DECLARE
  t text;
  visible bigint;
BEGIN
  FOREACH t IN ARRAY ARRAY['node_identity','journal_event','source','event','session',
                           'object','subject','point','relation','session_narrative'] LOOP
    EXECUTE format('SELECT count(*) FROM public.%I', t) INTO visible;
    IF visible <> 0 THEN
      RAISE EXCEPTION 'R6: public.% returned % row(s) with NO tenant GUC set — the policies '
                      'are no longer keyed on it', t, visible;
    END IF;
  END LOOP;
END $$;
RESET ROLE;

-- ── 9. …and a missing GUC makes a WRITE loud, not silent ───────────────────
-- The asymmetry is the safety property: an unscoped reader sees nothing, and an
-- unscoped writer is refused. Both halves are pinned here.
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
RESET ROLE;
-- The refused statement left no row behind: the guard must be a refusal, not a
-- filter that quietly drops the write.
SELECT tests.assert(
  (SELECT count(*) FROM public.point WHERE id = 'p_r6_unscoped') = 0,
  'R6: the refused unscoped write landed NOTHING');

-- ── 10. Clean up (the harness spot-checks for leftovers afterwards) ────────
-- Through the ONE production erasure path, as `20261012000001_record_schema.sql`
-- does: `purge_org_record` sets app.record_journal_purge transaction-locally and
-- hard-deletes the org, cascading to every record table.
-- The seed is still there (it survived §6-§9 now), so the two assertions below
-- measure a deletion rather than an empty table.
SELECT tests.assert(
  (SELECT count(*) FROM public.point WHERE org_id IN ('org_r6_a','org_r6_b')) = 3,
  'R6: both tenants are present immediately before the purge (three rows, as at 7) — a purge assertion over an empty table proves nothing');
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

-- ── 11. The RUNTIME gate's statement, EXECUTED (not merely described) ──────
-- `record_projection.TENANT_SCOPE_SQL` is the statement `replay()` and
-- `read_record_view()` refuse a connection on — the code half of "RLS is the
-- boundary". Every Python test fakes its row (`RecordingCursor.fetchone` answers
-- from the connection being modelled), so a syntax break, a dropped leg or a
-- flipped `'USAGE'` → `'MEMBER'` in that statement would redden NOTHING. This
-- section runs it, against two postures that must DISAGREE: a gate that answers
-- `false` for every role is exactly as broken as one that answers `true`.
--
-- ⚠️ The expression below is a COPY of the module's SELECT list (this harness is
-- SQL and cannot import Python). `tests/test_record_tenancy_guc.py::
-- test_the_runtime_gate_statement_is_executed_here` pins the four predicates in
-- both places, so drift reddens a Python test instead of being visible only to
-- whoever reads this file.
--
-- The assertion runs IN PLACE as the app role: line 49 grants that role USAGE on
-- schema `tests`, and since §6-§9 no longer `ROLLBACK` (which used to discard
-- that grant mid-file — #9600 review cycle 7), the grant is in effect here.
SET ROLE tortoise_record_app;
SELECT tests.assert(
  (SELECT (r.rolsuper OR r.rolbypassrls
           OR EXISTS (SELECT 1 FROM pg_roles g
                       WHERE g.oid <> r.oid
                         AND (g.rolsuper OR g.rolbypassrls)
                         AND pg_has_role(r.oid, g.oid, 'USAGE'))
           OR EXISTS (SELECT 1 FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = 'public'
                         AND c.relname = ANY(ARRAY['node_identity','journal_event',
                                                   'source','event','session','object',
                                                   'subject','point','relation',
                                                   'session_narrative'])
                         AND (c.relowner = r.oid
                              OR pg_has_role(r.oid, c.relowner, 'USAGE'))))
     FROM pg_roles r
    WHERE r.rolname = current_user) = false,
  'R6 runtime gate: the app role reads as NON-bypassing — the gate lets the write path proceed, and the policies are what scope it');
RESET ROLE;

-- The ANTI-VACUITY half: `service_role` is BYPASSRLS, so the same expression must
-- come back TRUE. Without this, an expression that had lost its legs (or returned
-- NULL) would still satisfy the assertion above.
SET ROLE service_role;
SELECT tests.assert(
  (SELECT (r.rolsuper OR r.rolbypassrls
           OR EXISTS (SELECT 1 FROM pg_roles g
                       WHERE g.oid <> r.oid
                         AND (g.rolsuper OR g.rolbypassrls)
                         AND pg_has_role(r.oid, g.oid, 'USAGE'))
           OR EXISTS (SELECT 1 FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                       WHERE n.nspname = 'public'
                         AND c.relname = ANY(ARRAY['node_identity','journal_event',
                                                   'source','event','session','object',
                                                   'subject','point','relation',
                                                   'session_narrative'])
                         AND (c.relowner = r.oid
                              OR pg_has_role(r.oid, c.relowner, 'USAGE'))))
     FROM pg_roles r
    WHERE r.rolname = current_user) = true,
  'R6 runtime gate: service_role reads as BYPASSING — the gate refuses it, which is what makes the app-role assertion non-vacuous');
RESET ROLE;

-- The unset-GUC half of the same gate is already pinned above (§8/§9: a read
-- returns ZERO ROWS and a write raises 42501), so it is not restated here — this
-- section exists to execute the STATEMENT, not to duplicate the semantics.
