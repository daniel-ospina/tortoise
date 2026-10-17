-- ============================================================================
-- SQL-level verification for the four-layer record schema (#7968, epic #7879).
--
-- Covers test-design #7967 surfaces:
--   S1 (journal -> Postgres): the event-stream tables + the append-only journal;
--   S6 (identity):            the (label, key) contract — generated key_property,
--                             node_key pinned to that label's key column, and the
--                             relation table's COMPOSITE foreign keys;
--   S7 (tenancy):             RLS enabled + policies on every new table, and a
--                             cross-tenant read returning nothing;
-- plus the EP-scalar carve-out's enforcement (the CHECKs, not just comments).
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
-- (the harness applies ALL migrations, then runs this suite with ON_ERROR_STOP:
-- every assertion RAISEs on failure)
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
GRANT USAGE ON SCHEMA tests TO anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION tests.assert(boolean, text) TO anon, authenticated, service_role;

-- ── 0. Seed one org + graph (the tenancy substrate) ────────────────────────
INSERT INTO public.organizations (id, name, graph_name)
VALUES ('org_7968', 'record-schema-test', 'record-schema-test')
ON CONFLICT (id) DO NOTHING;
INSERT INTO public.graphs (id, org_id, name, namespace)
VALUES ('g_7968', 'org_7968', 'record-schema-test', 'org_org_7968_g_7968')
ON CONFLICT (id) DO NOTHING;

-- ── 1. The four layers have their tables (S1) ──────────────────────────────
SELECT tests.assert(
  (SELECT count(*) FROM information_schema.tables
   WHERE table_schema = 'public'
     AND table_name IN ('node_identity','journal_event','source','event','session',
                        'object','subject','point','relation')) = 9,
  '7968: the event-stream, semantic and epistemic tables all exist');

SELECT tests.assert(
  NOT EXISTS (SELECT 1 FROM information_schema.tables
              WHERE table_schema = 'public' AND table_name IN ('procedural','ep','belief')),
  '7968: the procedural layer is a VIEW, never a table (02-identity-and-placement.md §2.6)');

SELECT tests.assert(
  (SELECT count(*) FROM pg_views
   WHERE schemaname = 'public'
     AND viewname IN ('record_event_stream','object_current_status','subject_current_status')) = 3,
  '7968: the procedural views exist');

SELECT tests.assert(
  (SELECT count(*) FROM pg_class
   WHERE relname IN ('record_event_stream','object_current_status','subject_current_status')
     AND 'security_invoker=true' = ANY(coalesce(reloptions, ARRAY[]::text[]))) = 3,
  '7968: every procedural view is security_invoker (a view must not be a tenancy hole)');

-- ── 2. The EP-scalar carve-out: the names are ABSENT as columns (S5) ───────
SELECT tests.assert(
  NOT EXISTS (SELECT 1 FROM information_schema.columns
              WHERE table_schema = 'public'
                AND ((table_name = 'point' AND column_name IN
                        ('confidence','ep_alpha','ep_beta','ep_dirty','ep_dirty_at',
                         'posterior_alpha','posterior_beta','baseline_set','c_cal',
                         'last_dreamed_at','content_hash','inherited_at','version',
                         '_nid','_graph_id','reason'))
                  OR (table_name = 'event' AND column_name = 'confidence')
                  OR (table_name = 'object' AND column_name IN ('status','embedding'))
                  OR (table_name = 'subject' AND column_name = 'embedding')
                  OR (table_name = 'source' AND column_name IN
                        ('reliability','reliability_components','reliability_derived_at',
                         'previous_content_hash','__run_id'))
                  OR (table_name = 'relation' AND column_name IN
                        ('msg_alpha','msg_beta','back_msg_alpha','back_msg_beta')))),
  '7968: no DERIVED/VOLATILE property has a column (§17.5 carve-out)');

SELECT tests.assert(
  EXISTS (SELECT 1 FROM information_schema.columns
          WHERE table_schema='public' AND table_name='point'
            AND column_name IN ('baseline_source','baseline_alpha','baseline_beta')),
  '7968: the AUTHOR-SET baseline is PRIMARY (#7955 correction to §17.5)');

-- ── 3. Identity: the (label, key) contract (S6) ────────────────────────────
SELECT tests.assert(
  EXISTS (SELECT 1 FROM pg_attrdef d
          JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
          WHERE d.adrelid = 'public.node_identity'::regclass AND a.attname = 'key_property'),
  '7968: node_identity.key_property is generated (the contract cannot be written around)');

SELECT tests.assert(
  (SELECT count(*) FROM pg_constraint con
   JOIN pg_class rel ON rel.oid = con.conrelid
   JOIN pg_class ref ON ref.oid = con.confrelid
   WHERE con.contype = 'f' AND ref.relname = 'node_identity'
     AND rel.relname IN ('source','event','session','object','subject','point')) = 6,
  '7968: every layer table carries a real FK into node_identity (a foreign key is (label, key))');

SELECT tests.assert(
  (SELECT count(*) FROM pg_constraint con
   JOIN pg_class rel ON rel.oid = con.conrelid
   JOIN pg_class ref ON ref.oid = con.confrelid
   WHERE con.contype = 'f' AND rel.relname = 'relation' AND ref.relname = 'node_identity') = 2,
  '7968: relation.src and relation.tgt are BOTH (label, key) composite FKs');

SELECT tests.assert(
  (SELECT string_agg(kcu.column_name, ',' ORDER BY kcu.column_name)
     FROM information_schema.table_constraints tc
     JOIN information_schema.key_column_usage kcu
       ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
   WHERE tc.table_schema = 'public' AND tc.table_name = 'point'
     AND tc.constraint_type = 'PRIMARY KEY') = 'graph_id,node_key,org_id',
  '7968: public.point is keyed by (org_id, graph_id, node_key)');

SELECT tests.assert(
  (SELECT count(*) FROM pg_constraint con
   JOIN pg_class rel ON rel.oid = con.conrelid
   WHERE con.contype = 'c' AND rel.relname = 'source'
     AND pg_get_constraintdef(con.oid) LIKE '%node_key = url%') = 1,
  '7968: source.node_key is pinned to url (W1: :Source -> url)');

SELECT tests.assert(
  (SELECT count(*) FROM pg_constraint con
   JOIN pg_class rel ON rel.oid = con.conrelid
   WHERE con.contype = 'c' AND rel.relname = 'point'
     AND pg_get_constraintdef(con.oid) LIKE '%node_key = id%') = 1,
  '7968: point.node_key is pinned to id (W1: :Point -> id)');

-- ── 4. The carve-out CHECKS actually refuse a derived value ────────────────
INSERT INTO public.node_identity (org_id, graph_id, node_label, node_key)
VALUES ('org_7968','g_7968','Point','01HX0000000000000000000001'),
       ('org_7968','g_7968','Source','https://example.test/7968'),
       ('org_7968','g_7968','Object','rec-schema-object'),
       ('org_7968','g_7968','Subject','rec-schema-subject'),
       ('org_7968','g_7968','Event','evt_7968')
ON CONFLICT DO NOTHING;

DO $$
BEGIN
  BEGIN
    INSERT INTO public.point (org_id, graph_id, node_key, id, content, props)
    VALUES ('org_7968','g_7968','01HX0000000000000000000001',
            '01HX0000000000000000000001','a claim', '{"confidence": 0.81}'::jsonb);
    RAISE EXCEPTION '7968: chk_point_props_no_derived did NOT fire';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

-- 4a. The projection-owned lifecycle cache (`status`) is refused on BOTH
-- semantic tables. `Object` refused it from PR #8179; `Subject` did NOT until
-- the forward migration 20261014000001 (#8263) — §2.6 classifies the two
-- identically, so the asymmetry was an oversight. Each block's node_identity
-- row is seeded above, so the ONLY constraint that can reject the INSERT is the
-- props CHECK; if the CHECK does not fire, the P0002 sentinel escapes the
-- `check_violation` handler and this suite fails on the SPECIFIC assertion.
DO $$
BEGIN
  BEGIN
    INSERT INTO public.subject (org_id, graph_id, node_key, name, props)
    VALUES ('org_7968','g_7968','rec-schema-subject','rec-schema-subject',
            '{"status":"superseded"}'::jsonb);
    RAISE EXCEPTION '8263: chk_subject_props_no_derived accepted `status`' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

-- 4a(ii). The forward migration REPLACES the constraint wholesale, so the OTHER
-- name it refuses must be pinned on the LIVE constraint (not just via the Python
-- union parse, which cannot see a shrinking re-declaration). `embedding` is not
-- a column on `subject`, so this is a props-tail refusal and nothing else.
DO $$
BEGIN
  BEGIN
    INSERT INTO public.subject (org_id, graph_id, node_key, name, props)
    VALUES ('org_7968','g_7968','rec-schema-subject','rec-schema-subject',
            '{"embedding":[0.1]}'::jsonb);
    RAISE EXCEPTION '8263: the forward ALTER re-opened `embedding` on subject.props' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

DO $$
BEGIN
  BEGIN
    INSERT INTO public.object (org_id, graph_id, node_key, name, props)
    VALUES ('org_7968','g_7968','rec-schema-object','rec-schema-object',
            '{"status":"live"}'::jsonb);
    RAISE EXCEPTION '8263: chk_object_props_no_derived accepted `status`' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

DO $$
BEGIN
  BEGIN
    INSERT INTO public.point (org_id, graph_id, node_key, id, content, baseline_source)
    VALUES ('org_7968','g_7968','01HX0000000000000000000002',
            '01HX0000000000000000000002','a claim','system-default');
    RAISE EXCEPTION '7968: chk_point_baseline_author_set did NOT fire';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

DO $$
BEGIN
  BEGIN
    INSERT INTO public.source (org_id, graph_id, node_key, url)
    VALUES ('org_7968','g_7968','https://example.test/7968','https://elsewhere.test/x');
    RAISE EXCEPTION '7968: chk_source_node_key_is_contract_key did NOT fire';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

-- ── 4b. The (label, key) CHECKs refuse a NULL key column (the fail-open hole) ─
-- A bare `CHECK (node_key = <keycol>)` is SQL NULL — not FALSE — when the key
-- column is NULL, so the row was ACCEPTED and node_key resolved to nothing in
-- silence (review of PR #8179 P1). Each block seeds the matching node_identity
-- row first, so the ONLY constraint that can reject the row is the key pin —
-- if the CHECK is still fail-open the INSERT succeeds and the P0002 sentinel
-- escapes the `check_violation` handler, failing the suite.
INSERT INTO public.node_identity (org_id, graph_id, node_label, node_key)
VALUES ('org_7968','g_7968','Source','s-null-url'),
       ('org_7968','g_7968','Event','e-null-eventid'),
       ('org_7968','g_7968','Session','sess-null-id'),
       ('org_7968','g_7968','Object','obj-null-name'),
       ('org_7968','g_7968','Subject','subj-null-name'),
       ('org_7968','g_7968','Point','p-null-id')
ON CONFLICT DO NOTHING;

DO $$
BEGIN
  BEGIN
    INSERT INTO public.source (org_id, graph_id, node_key, url)
    VALUES ('org_7968','g_7968','s-null-url', NULL);
    RAISE EXCEPTION '7968: source accepted a NULL url (fail-open key pin)' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

DO $$
BEGIN
  BEGIN
    INSERT INTO public.event (org_id, graph_id, node_key, event_id)
    VALUES ('org_7968','g_7968','e-null-eventid', NULL);
    RAISE EXCEPTION '7968: event accepted a NULL event_id (fail-open key pin)' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

DO $$
BEGIN
  BEGIN
    INSERT INTO public.session (org_id, graph_id, node_key, id)
    VALUES ('org_7968','g_7968','sess-null-id', NULL);
    RAISE EXCEPTION '7968: session accepted a NULL id (fail-open key pin)' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

DO $$
BEGIN
  BEGIN
    INSERT INTO public.object (org_id, graph_id, node_key, name)
    VALUES ('org_7968','g_7968','obj-null-name', NULL);
    RAISE EXCEPTION '7968: object accepted a NULL name (fail-open key pin)' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

DO $$
BEGIN
  BEGIN
    INSERT INTO public.subject (org_id, graph_id, node_key, name)
    VALUES ('org_7968','g_7968','subj-null-name', NULL);
    RAISE EXCEPTION '7968: subject accepted a NULL name (fail-open key pin)' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

DO $$
BEGIN
  BEGIN
    INSERT INTO public.point (org_id, graph_id, node_key, id, content)
    VALUES ('org_7968','g_7968','p-null-id', NULL, 'a claim');
    RAISE EXCEPTION '7968: point accepted a NULL id (fail-open key pin)' USING ERRCODE = 'P0002';
  EXCEPTION WHEN check_violation THEN NULL;
  END;
END $$;

-- The valid rows the rest of the suite needs (as service_role: the write path).
SET ROLE service_role;
INSERT INTO public.point (org_id, graph_id, node_key, id, content)
VALUES ('org_7968','g_7968','01HX0000000000000000000001',
        '01HX0000000000000000000001','a claim')
ON CONFLICT DO NOTHING;
INSERT INTO public.source (org_id, graph_id, node_key, url)
VALUES ('org_7968','g_7968','https://example.test/7968','https://example.test/7968')
ON CONFLICT DO NOTHING;
INSERT INTO public.object (org_id, graph_id, node_key, name, object_kind)
VALUES ('org_7968','g_7968','rec-schema-object','rec-schema-object','other')
ON CONFLICT DO NOTHING;
INSERT INTO public.event (org_id, graph_id, node_key, event_id, event_kind, content)
VALUES ('org_7968','g_7968','evt_7968','evt_7968','decision','an occurrence')
ON CONFLICT DO NOTHING;
RESET ROLE;

-- A relation whose target does not exist must be refused by the composite FK.
DO $$
BEGIN
  BEGIN
    INSERT INTO public.relation (org_id, graph_id, rel_type, src_label, src_key, tgt_label, tgt_key)
    VALUES ('org_7968','g_7968','aboutObject','Point','01HX0000000000000000000001',
            'Object','no-such-object');
    RAISE EXCEPTION '7968: the relation composite FK did NOT fire';
  EXCEPTION WHEN foreign_key_violation THEN NULL;
  END;
END $$;

-- A relation whose endpoints exist is accepted, and is idempotent by key.
SET ROLE service_role;
INSERT INTO public.relation (org_id, graph_id, rel_type, src_label, src_key, tgt_label, tgt_key, confidence)
VALUES ('org_7968','g_7968','IMPL','Point','01HX0000000000000000000001',
        'Object','rec-schema-object', 0.85);
RESET ROLE;
SELECT tests.assert(
  (SELECT count(*) FROM public.relation WHERE org_id='org_7968') = 1,
  '7968: a (label, key)-endpointed relation with existing endpoints is accepted');

-- ── 5. The journal is append-only (S1) ─────────────────────────────────────
SET ROLE service_role;
INSERT INTO public.journal_event (org_id, graph_id, seq, event_type, node_label, node_key, payload)
VALUES ('org_7968','g_7968', 1, 'PointAdded', 'Point',
        '01HX0000000000000000000001', '{"type":"PointAdded","ts":"2026-10-12T00:00:00Z"}'::jsonb);
RESET ROLE;

DO $$
BEGIN
  BEGIN
    UPDATE public.journal_event SET event_type = 'Tampered' WHERE org_id = 'org_7968';
    -- The sentinel MUST carry a DIFFERENT errcode from the trigger's bare
    -- RAISE EXCEPTION (SQLSTATE P0001 = raise_exception). Catching
    -- `raise_exception` while the sentinel also raises P0001 swallowed BOTH
    -- paths and made this assertion vacuous (review of PR #8179 P1). The
    -- sentinel is P0002 (the repo convention), so it escapes the handler and
    -- the block fails when the trigger does NOT fire.
    RAISE EXCEPTION '7968: the journal_event append-only trigger did NOT fire on UPDATE' USING ERRCODE = 'P0002';
  EXCEPTION WHEN raise_exception THEN NULL;
  END;
END $$;

DO $$
BEGIN
  BEGIN
    DELETE FROM public.journal_event WHERE org_id = 'org_7968';
    RAISE EXCEPTION '7968: the journal_event append-only trigger did NOT fire on DELETE' USING ERRCODE = 'P0002';
  EXCEPTION WHEN raise_exception THEN NULL;
  END;
END $$;

SELECT tests.assert(
  (SELECT count(*) FROM public.record_event_stream WHERE org_id = 'org_7968') = 1,
  '7968: the record event stream exposes the journal relationally');

-- ── 6. Tenancy / RLS (S7) ──────────────────────────────────────────────────
SELECT tests.assert(
  (SELECT count(*) FROM pg_class
   WHERE relname IN ('node_identity','journal_event','source','event','session',
                     'object','subject','point','relation')
     AND relkind = 'r' AND relrowsecurity) = 9,
  '7968: every new data-plane table has RLS ENABLED');

SELECT tests.assert(
  (SELECT count(*) FROM pg_policies
   WHERE schemaname='public'
     AND tablename IN ('node_identity','journal_event','source','event','session',
                       'object','subject','point','relation')) = 18,
  '7968: every new table carries the org-read policy and the service_role policy');

-- auth.rsuperuser-only: SET ROLE then read with a DIFFERENT org GUC -> nothing.
SET ROLE service_role;
INSERT INTO public.organizations (id, name, graph_name)
VALUES ('org_7968_other', 'record-schema-test-other', 'other') ON CONFLICT (id) DO NOTHING;
RESET ROLE;

SET ROLE authenticated;
SET app.current_org_id = 'org_7968_other';
SELECT tests.assert(
  (SELECT count(*) FROM public.point) = 0,
  '7968: a cross-tenant read returns nothing (RLS is the boundary)');
SET app.current_org_id = 'org_7968';
SELECT tests.assert(
  (SELECT count(*) FROM public.point) = 1,
  '7968: the owning tenant reads its own row');
-- (the writes are refused for `authenticated`: SELECT-only grant + no write policy)
DO $$
BEGIN
  BEGIN
    INSERT INTO public.point (org_id, graph_id, node_key, id, content)
    VALUES ('org_7968','g_7968','01HX0000000000000000000009',
            '01HX0000000000000000000009','sneaky');
    RAISE EXCEPTION '7968: authenticated was able to write the record directly';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;
RESET ROLE;

-- ── 7. Clean up (the harness spot-checks for leftovers afterwards) ─────────
-- The journal is append-only; the ONE production path that erases a tenant
-- is the SECURITY DEFINER RPC `purge_org_record` (migration 20261012000005)
-- — exactly what `purge_org_control_plane` calls. It sets
-- app.record_journal_purge transaction-locally, so this exercises the real
-- erasure path end to end rather than hand-setting the GUC.
SET ROLE service_role;
SELECT public.purge_org_record('org_7968');
SELECT public.purge_org_record('org_7968_other');
RESET ROLE;
SELECT tests.assert(
  (SELECT count(*) FROM public.point WHERE org_id = 'org_7968') = 0,
  '7968: purge_org_record cascaded the tenant from organizations');
SELECT tests.assert(
  (SELECT count(*) FROM public.journal_event WHERE org_id = 'org_7968') = 0,
  '7968: purge_org_record cascaded the append-only journal');

-- The RPC is the ONLY caller allowed to set the purge flag: an
-- authenticated session must not be able to execute it (nor, therefore, to
-- erase a tenant).
SET ROLE authenticated;
DO $$
BEGIN
  BEGIN
    PERFORM public.purge_org_record('org_7968_other');
    RAISE EXCEPTION '7968: authenticated could execute purge_org_record' USING ERRCODE = 'P0002';
  EXCEPTION WHEN insufficient_privilege THEN NULL;
  END;
END $$;
RESET ROLE;
