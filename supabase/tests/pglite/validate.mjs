// PGlite harness — validate Supabase control-plane migrations + SQL assertion
// suites WITHOUT Docker (issue #770). Supabase-local bootstrap (roles,
// default privileges, auth schema with auth.uid()/auth.jwt() GUC shims),
// applies migrations 0001-20260827000001 in order, then runs ALL assertion suites
// (0006–0009 from #769, 0010 from #770, 2026 token suites, 20260827000001 blog CMS) with ON_ERROR_STOP semantics.
//
// Run:   npm install   (once, in this directory)
//        npm run validate
// Exit:  0 = all migrations applied + all assertions passed; 1 = any failure.
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, '..', '..', '..');
const MIG_DIR = join(REPO_ROOT, 'supabase', 'migrations');
const TESTS_DIR = join(REPO_ROOT, 'supabase', 'tests');

const db = new PGlite();

// ── Supabase-local bootstrap (roles, default privileges, auth schema) ──
await db.exec(`
  CREATE ROLE anon NOLOGIN;
  CREATE ROLE authenticated NOLOGIN;
  CREATE ROLE service_role NOLOGIN BYPASSRLS;
  GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO anon, authenticated, service_role;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO anon, authenticated, service_role;
  ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO anon, authenticated, service_role;
  CREATE SCHEMA auth;
  CREATE TABLE auth.users (
    instance_id uuid DEFAULT '00000000-0000-0000-0000-000000000000',
    id uuid PRIMARY KEY,
    aud varchar(255) DEFAULT 'authenticated',
    role varchar(255) DEFAULT 'authenticated',
    email varchar(255) UNIQUE,
    encrypted_password varchar(255) DEFAULT '',
    email_confirmed_at timestamptz,
    raw_app_meta_data jsonb DEFAULT '{}',
    raw_user_meta_data jsonb DEFAULT '{}'
  );
  CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid LANGUAGE sql STABLE AS
    $$ SELECT coalesce(nullif(current_setting('request.jwt.claim.sub', true), ''), '00000000-0000-0000-0000-000000000000')::uuid $$;
  CREATE OR REPLACE FUNCTION auth.jwt() RETURNS jsonb LANGUAGE sql STABLE AS
    $$ SELECT coalesce(nullif(current_setting('request.jwt.claims', true), ''), '{}')::jsonb $$;

  -- Minimal Supabase storage schema (for migrations/policies that touch buckets)
  CREATE SCHEMA storage;
  CREATE TABLE storage.buckets (
    id text PRIMARY KEY,
    name text NOT NULL,
    public boolean NOT NULL DEFAULT false
  );
  CREATE TABLE storage.objects (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    bucket_id text NOT NULL,
    name text NOT NULL,
    owner uuid,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
  );
  GRANT USAGE ON SCHEMA storage TO anon, authenticated, service_role;
  GRANT ALL ON storage.buckets, storage.objects TO anon, authenticated, service_role;
  ALTER TABLE storage.objects ENABLE ROW LEVEL SECURITY;
  ALTER TABLE storage.buckets ENABLE ROW LEVEL SECURITY;
  CREATE POLICY storage_buckets_public_read ON storage.buckets FOR SELECT TO anon, authenticated
    USING (public = true);
`);

// ── Apply migrations 0001-20260813000005 in order ──
// NOTE (harness-local): 0014 ships metering_increment(text, text, integer);
// 20260813000002 replaces it with a 4-arg variant WITH defaults. CREATE
// OR REPLACE cannot match across different arg lists → an overload is
// created, and the migration's unqualified `GRANT ... ON FUNCTION
// metering_increment` becomes ambiguous ("function name is not unique").
// Real deployments hit the same path; the end state is the single 4-arg
// function. Drop the stale 3-arg overload before applying so the harness
// mirrors that end state exactly.
const files = ['0001_user_teams.sql','0002_audit_events.sql','0003_team_memberships.sql',
               '0004_analytics_events.sql','0005_waitlist_subscribers.sql',
               '0006_teams.sql','0007_api_keys.sql','0008_invitations.sql',
               '0009_team_memberships_extend.sql','0010_provisioning_rpcs.sql',
               '0011_teams_name_unique.sql','0012_teams_billing_columns.sql',
               '0013_webhook_events.sql','0014_metering_records.sql',
               '0015_abuse_events.sql',
               '0016_oauth.sql',
               '20260813000001_teams_deleted_at.sql',
               '20260813000002_metering_nodes_written.sql',
               '20260813000003_audit_ip_time_index.sql',
               '20260813000004_claim_membership.sql',
               '20260813000005_dashboard_login.sql',
               '20260813000006_inviter_email.sql',
               '20260814000001_agent_signup_tokens.sql',
               '20260825000001_api_key_names.sql',
               '20260825214233_provision_team_keyless.sql',
               '20260826000001_revoke_signup_token.sql',
               '20260827000001_blog_cms.sql'];  // appended last: timestamp prefix sorts after the 2026 batch (fresh-DB safe)
for (const f of files) {
  const sql = readFileSync(`${MIG_DIR}/${f}`, 'utf8');
  try {
    if (f === '20260813000002_metering_nodes_written.sql') {
      await db.exec('DROP FUNCTION IF EXISTS public.metering_increment(text, text, integer);');
    }
    await db.exec(sql);
    console.log(`✓ migration ${f}`);
  } catch (e) {
    console.error(`✗ migration ${f} FAILED:\n  ${e.message.split('\n').slice(0,3).join('\n  ')}`);
    process.exit(1);
  }
}

// ── Run the assertion suites (0006–0009 from #769, 0010 from #770, then
// the 0010 suite's #1716 keyless sections against the post-keyless RPC) ──
const suites = [
  '0006-0009_schema_rls_constraints.sql',
  '0010_provisioning_rpcs.sql',
  '20260813000004_claim_membership.sql',
  '20260814000001_agent_signup_tokens.sql',
  '20260826000001_revoke_signup_token.sql',
  '20260827000001_blog_cms.sql',
];
for (const suite of suites) {
  const sql = readFileSync(`${TESTS_DIR}/${suite}`, 'utf8');
  console.log(`\nRunning test suite ${suite}...`);
  try {
    await db.exec(sql);
    console.log(`✅ ${suite} PASSED (no exceptions)`);
  } catch (e) {
    console.error(`✗ ${suite} FAILED:\n  ${e.message.split('\n').slice(0,8).join('\n  ')}`);
    process.exit(1);
  }
}

// ── 0011 duplicate-name guard (PR #874 review P1) ──
// The unique index must reject a second team with the same name (registry
// sdk.team_create parity — two teams sharing team_{name} would share a
// FalkorDB namespace).
try {
  await db.exec(`INSERT INTO public.teams (id, name, graph_name)
                 VALUES ('dup-a', 'dup-name', 'team_dup-name');`);
  await db.exec(`INSERT INTO public.teams (id, name, graph_name)
                 VALUES ('dup-b', 'dup-name', 'team_dup-name');`);
  console.error('✗ 0011: duplicate team name NOT rejected');
  process.exit(1);
} catch (e) {
  console.log('✓ 0011: duplicate team name rejected (unique index active)');
} finally {
  await db.exec(`DELETE FROM public.teams WHERE id IN ('dup-a', 'dup-b');`);
}

// ── Post-verification spot checks (independent of the test files) ──
const checks = await db.query(`SELECT
  (SELECT count(*) FROM pg_proc WHERE proname='provision_team') AS provision_team,
  (SELECT count(*) FROM pg_proc WHERE proname='update_user_team') AS update_user_team,
  (SELECT count(*) FROM pg_proc WHERE proname='reveal_api_key') AS reveal_api_key,
  (SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='team_memberships'
     AND indexname='uq_member_identity_team') AS identity_anchor_index,
  (SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='teams'
     AND indexname='uq_teams_name') AS teams_name_unique,
  (SELECT count(*) FROM public.teams WHERE id LIKE '%-770' OR id LIKE '%-769' OR id LIKE 'dup-%' OR id LIKE '%-1709') AS leftover_test_teams,
  (SELECT count(*) FROM public.team_memberships WHERE team_id LIKE '%-770' OR team_id LIKE '%-769' OR team_id LIKE '%-1709') AS leftover_test_memberships,
  (SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='agent_signup_tokens'
     AND indexname='uq_agent_signup_tokens_team') AS signup_tokens_team_unique,
  (SELECT count(*) FROM pg_proc WHERE proname='recover_team_key') AS recover_team_key,
  (SELECT count(*) FROM pg_proc WHERE proname='resolve_signup_token') AS resolve_signup_token,
  (SELECT count(*) FROM pg_proc WHERE proname='provision_team_with_token') AS provision_team_with_token,
  (SELECT count(*) FROM pg_proc WHERE proname='revoke_signup_token') AS revoke_signup_token;
`);
console.log('spot checks:', JSON.stringify(checks.rows[0]));

const r = checks.rows[0];
if (!(r.provision_team === 1 && r.update_user_team === 0 && r.reveal_api_key === 1
      && r.identity_anchor_index === 1 && r.teams_name_unique === 1
      && r.leftover_test_teams === 0
      && r.leftover_test_memberships === 0
      && r.signup_tokens_team_unique === 1 && r.recover_team_key === 1
      && r.resolve_signup_token === 1 && r.provision_team_with_token === 1
      && r.revoke_signup_token === 1)) {
  console.error('✗ spot checks failed — see JSON above');
  process.exit(1);
}
console.log('✅ ALL MIGRATIONS + BOTH TEST SUITES + SPOT CHECKS PASSED');

await db.close();
