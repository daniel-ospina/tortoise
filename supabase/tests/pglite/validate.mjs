// PGlite harness — validate Supabase control-plane migrations + SQL assertion
// suites WITHOUT Docker (issue #770). Supabase-local bootstrap (roles,
// default privileges, auth schema with auth.uid()/auth.jwt() GUC shims),
// applies migrations 0001–0010 in order, then runs BOTH assertion suites
// (0006–0009 from #769 and 0010 from #770) with ON_ERROR_STOP semantics.
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
`);

// ── Apply migrations 0001-0010 in order ──
const files = ['0001_user_teams.sql','0002_audit_events.sql','0003_team_memberships.sql',
               '0004_analytics_events.sql','0005_waitlist_subscribers.sql',
               '0006_teams.sql','0007_api_keys.sql','0008_invitations.sql',
               '0009_team_memberships_extend.sql','0010_provisioning_rpcs.sql'];
for (const f of files) {
  const sql = readFileSync(`${MIG_DIR}/${f}`, 'utf8');
  try {
    await db.exec(sql);
    console.log(`✓ migration ${f}`);
  } catch (e) {
    console.error(`✗ migration ${f} FAILED:\n  ${e.message.split('\n').slice(0,3).join('\n  ')}`);
    process.exit(1);
  }
}

// ── Run the assertion suites (0006–0009 from #769, then 0010 from #770) ──
const suites = [
  '0006-0009_schema_rls_constraints.sql',
  '0010_provisioning_rpcs.sql',
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

// ── Post-verification spot checks (independent of the test files) ──
const checks = await db.query(`SELECT
  (SELECT count(*) FROM pg_proc WHERE proname='provision_team') AS provision_team,
  (SELECT count(*) FROM pg_proc WHERE proname='update_user_team') AS update_user_team,
  (SELECT count(*) FROM pg_proc WHERE proname='reveal_api_key') AS reveal_api_key,
  (SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='team_memberships'
     AND indexname='uq_member_identity_team') AS identity_anchor_index,
  (SELECT count(*) FROM public.teams WHERE id LIKE '%-770' OR id LIKE '%-769') AS leftover_test_teams,
  (SELECT count(*) FROM public.team_memberships WHERE team_id LIKE '%-770' OR team_id LIKE '%-769') AS leftover_test_memberships;
`);
console.log('spot checks:', JSON.stringify(checks.rows[0]));

const r = checks.rows[0];
if (!(r.provision_team === 1 && r.update_user_team === 0 && r.reveal_api_key === 1
      && r.identity_anchor_index === 1 && r.leftover_test_teams === 0
      && r.leftover_test_memberships === 0)) {
  console.error('✗ spot checks failed — see JSON above');
  process.exit(1);
}
console.log('✅ ALL MIGRATIONS + BOTH TEST SUITES + SPOT CHECKS PASSED');

await db.close();
