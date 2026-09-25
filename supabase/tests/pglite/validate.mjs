// PGlite harness — validate Supabase control-plane migrations + SQL assertion
// suites WITHOUT Docker (issue #770). Supabase-local bootstrap (roles,
// default privileges, auth schema with auth.uid()/auth.jwt() GUC shims),
// applies EVERY migration in supabase/migrations (filename order), then runs ALL assertion suites
// (0006–0009 from #769, 0010 from #770, 2026 token suites, 20260827000001 blog CMS) with ON_ERROR_STOP semantics.
//
// Run:   npm install   (once, in this directory)
//        npm run validate
// Exit:  0 = all migrations applied + all assertions passed; 1 = any failure.
import { PGlite } from '@electric-sql/pglite';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = join(HERE, '..', '..', '..');
const MIG_DIR = join(REPO_ROOT, 'supabase', 'migrations');
const TESTS_DIR = join(REPO_ROOT, 'supabase', 'tests');

// Migration files are immutable history. The C8 rollback drill below REPLAYS
// the C1 migration text, which predates the #3543 tenancy rename
// (teams→organizations, team_id→org_id, the tenant GUC, the graphs indexes) —
// so on the renamed schema its raw text no longer applies. Map only the
// executable vocabulary; the `team:manage` scope VALUE is deliberately NOT
// touched (renaming it would need an UPDATE of live rows — the forbidden
// data migration).
function applyPostRenameVocabulary(sql) {
  return sql
    .replace(/public\.teams\b/g, 'public.organizations')
    .replace(/\bidx_graphs_team_id\b/g, 'idx_graphs_org_id')
    .replace(/\buq_graphs_team_name_active\b/g, 'uq_graphs_org_name_active')
    .replace(/\bcurrent_team_id\b/g, 'current_org_id')
    .replace(/\bteam_id\b/g, 'org_id');
}

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
    last_sign_in_at timestamptz,
    raw_app_meta_data jsonb DEFAULT '{}',
    raw_user_meta_data jsonb DEFAULT '{}'
  );
  CREATE TABLE auth.identities (
    id uuid PRIMARY KEY,
    user_id uuid NOT NULL,
    provider text NOT NULL,
    provider_id text NOT NULL,
    identity_data jsonb,
    email text,
    created_at timestamptz NOT NULL DEFAULT now(),
    last_sign_in_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
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
// ENUMERATED FROM DISK, filename order — Supabase's own migration order.
// A hand-pinned array is what silently drifted: at #3543 it listed 31 of the
// 38 migrations on disk, so 20260817000001 / 20260829000001 / 20260830000001 /
// 20260907000001 / 2026090900000{1,2,3} were never applied by the harness,
// and a migration added later would be silently uncovered again. Enumerating
// the directory makes "every migration on disk is applied" a structural
// property — a new migration cannot ship uncovered.
const files = readdirSync(MIG_DIR).filter((f) => f.endsWith('.sql')).sort();
for (const f of files) {
  const sql = readFileSync(`${MIG_DIR}/${f}`, 'utf8');
  try {
    if (f === '20260813000002_metering_nodes_written.sql') {
      await db.exec('DROP FUNCTION IF EXISTS public.metering_increment(text, text, integer);');
    }
    if (f === '20260919000001_metering_period_end_repair.sql') {
      // #4216: seed a HALF-KNOWN subscription anchor so this migration's
      // DEPLOY-TIME one-time SELECT is exercised. The repair suites test the
      // FUNCTION, not the wiring — without this seed, deleting the migration's
      // trailing `SELECT public.metering_repair_period_bounds();` leaves the
      // whole harness green while every pre-existing half-written row stays
      // unmeterable (the symptom #4216 exists to remove).
      await db.exec(`INSERT INTO public.organizations
          (id, name, graph_name, subscription_id,
           current_period_start, current_period_end)
        VALUES ('4216-migrate-seed', '4216-migrate-seed',
                'org_4216-migrate-seed', 'sub_migrate_seed',
                '2026-11-15T00:00:00+00:00', NULL);`);
    }
    await db.exec(sql);
    console.log(`✓ migration ${f}`);
  } catch (e) {
    console.error(`✗ migration ${f} FAILED:\n  ${e.message.split('\n').slice(0,3).join('\n  ')}`);
    process.exit(1);
  }
}

// ── #4216: the migration's DEPLOY-TIME repair actually ran ──────────────────
// The seed above was inserted BEFORE `20260919000001`, so only the migration's
// own one-time `SELECT` can have completed it. Asserted HERE — before any
// suite — because the repair suite's own function call would otherwise mask a
// missing SELECT.
{
  const r = await db.query(
    "SELECT current_period_end FROM public.organizations WHERE id = '4216-migrate-seed'");
  const got = r.rows[0] ? r.rows[0].current_period_end : undefined;
  if (got === null || got === undefined) {
    console.error('✗ #4216: the migration did NOT repair the seeded NULL-end org — its deploy-time SELECT did not run');
    process.exit(1);
  }
  if (new Date(got).getTime() !== new Date('2026-12-15T00:00:00Z').getTime()) {
    console.error(`✗ #4216: deploy-time repair derived ${got}, expected 2026-12-15T00:00:00Z`);
    process.exit(1);
  }
  await db.exec("DELETE FROM public.organizations WHERE id = '4216-migrate-seed'");
  console.log('✓ #4216: the migration\'s deploy-time repair completed the seeded NULL-end org');
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
  '20260827000002_user_identity_profile.sql',
  '20260901000001_graphs_and_key_scopes.sql',  // C1 #2110
  '20260906000001_graphs_deleted_at.sql',  // #2304
  '20260919000001_metering_period_end_repair.sql',  // #4216
  '20260925000001_oauth_referential_integrity.sql',  // #3036
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
// The unique index must reject a second org with the same name (registry
// sdk.org_create parity — two orgs sharing org_{name} would share a
// FalkorDB namespace).
try {
  await db.exec(`INSERT INTO public.organizations (id, name, graph_name)
                 VALUES ('dup-a', 'dup-name', 'org_dup-name');`);
  await db.exec(`INSERT INTO public.organizations (id, name, graph_name)
                 VALUES ('dup-b', 'dup-name', 'org_dup-name');`);
  console.error('✗ 0011: duplicate org name NOT rejected');
  process.exit(1);
} catch (e) {
  console.log('✓ 0011: duplicate org name rejected (unique index active)');
} finally {
  await db.exec(`DELETE FROM public.organizations WHERE id IN ('dup-a', 'dup-b');`);
}

// ── Post-verification spot checks (independent of the test files) ──
const checks = await db.query(`SELECT
  (SELECT count(*) FROM pg_proc WHERE proname='provision_team') AS provision_team,
  (SELECT count(*) FROM pg_proc WHERE proname='update_user_team') AS update_user_team,
  (SELECT count(*) FROM pg_proc WHERE proname='reveal_api_key') AS reveal_api_key,
  (SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='org_memberships'
     AND indexname='uq_member_identity_org') AS identity_anchor_index,
  (SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='organizations'
     AND indexname='uq_organizations_name') AS orgs_name_unique,
  (SELECT count(*) FROM public.organizations WHERE id LIKE '%-770' OR id LIKE '%-769' OR id LIKE 'dup-%' OR id LIKE '%-1709') AS leftover_test_orgs,
  (SELECT count(*) FROM public.org_memberships WHERE org_id LIKE '%-770' OR org_id LIKE '%-769' OR org_id LIKE '%-1709') AS leftover_test_memberships,
  (SELECT count(*) FROM pg_indexes WHERE schemaname='public' AND tablename='agent_signup_tokens'
     AND indexname='uq_agent_signup_tokens_org') AS signup_tokens_org_unique,
  (SELECT count(*) FROM pg_proc WHERE proname='recover_team_key') AS recover_team_key,
  (SELECT count(*) FROM pg_proc WHERE proname='resolve_signup_token') AS resolve_signup_token,
  (SELECT count(*) FROM pg_proc WHERE proname='provision_team_with_token') AS provision_team_with_token,
  (SELECT count(*) FROM pg_proc WHERE proname='revoke_signup_token') AS revoke_signup_token;
`);
console.log('spot checks:', JSON.stringify(checks.rows[0]));

const r = checks.rows[0];
if (!(r.provision_team === 1 && r.update_user_team === 0 && r.reveal_api_key === 1
      && r.identity_anchor_index === 1 && r.orgs_name_unique === 1
      && r.leftover_test_orgs === 0
      && r.leftover_test_memberships === 0
      && r.signup_tokens_org_unique === 1 && r.recover_team_key === 1
      && r.resolve_signup_token === 1 && r.provision_team_with_token === 1
      && r.revoke_signup_token === 1)) {
  console.error('✗ spot checks failed — see JSON above');
  process.exit(1);
}
// ── C8 #2117 rollback drill (apply → rollback → re-apply) ──────────────────
// The epic's reversibility invariant (R18): migration 20260901000001 (C1)
// drops CLEANLY — dropping the graphs table + the api_keys graph columns
// restores the pre-multi-graph shape (legacy keys resolve to the default
// graph; graph_metadata derives the default from organizations.graph_name — no
// backfill, no forced action, E2E-5). This drill proves the round trip on
// the SAME database the suites just verified:
//   1. rollback: drop the C1 additions
//   2. assert the pre-C1 shape (graphs gone; api_keys columns gone)
//   3. re-apply migration 20260901000001
//   4. re-run the C1 assertion suite → invariants hold after re-apply
const C1_MIGRATION = '20260901000001_graphs_and_key_scopes.sql';
const C1_SUITE = '20260901000001_graphs_and_key_scopes.sql';
console.log('\n── C8 rollback drill (apply → rollback → re-apply) ──');

// 1. rollback path — the documented drop-columns/drop-table restore.
try {
  await db.exec(`
    ALTER TABLE public.api_keys
      DROP COLUMN IF EXISTS graph_id,
      DROP COLUMN IF EXISTS scopes,
      DROP COLUMN IF EXISTS created_by_key_id,
      DROP COLUMN IF EXISTS delegation_depth;
    DROP INDEX IF EXISTS idx_api_keys_graph_id;
    DROP TABLE IF EXISTS public.graphs CASCADE;
  `);
  console.log('✓ drill 1: C1 additions dropped (rollback path)');
} catch (e) {
  console.error(`✗ drill 1 (rollback drop) FAILED:\n  ${e.message.split('\n').slice(0, 4).join('\n  ')}`);
  process.exit(1);
}

// 2. assert the pre-C1 shape — legacy behavior restored (graph_metadata
// derives the default from organizations.graph_name; legacy api_keys rows
// have no graph columns).
try {
  const afterDrop = await db.query(`SELECT
    (SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename='graphs') AS graphs_tbl,
    (SELECT count(*) FROM information_schema.columns
       WHERE table_schema='public' AND table_name='api_keys'
         AND column_name IN ('graph_id','scopes','delegation_depth','created_by_key_id')) AS graph_cols,
    (SELECT count(*) FROM information_schema.columns
       WHERE table_schema='public' AND table_name='organizations' AND column_name='graph_name') AS graph_name_col;`);
  const d = afterDrop.rows[0];
  if (!(d.graphs_tbl === 0 && d.graph_cols === 0 && d.graph_name_col === 1)) {
    console.error(`✗ drill 2: pre-C1 shape NOT restored (graphs=${d.graphs_tbl} cols=${d.graph_cols}) — see JSON above`);
    process.exit(1);
  }
  console.log('✓ drill 2: pre-C1 shape restored (graphs gone, api_keys columns gone, organizations.graph_name intact)');
} catch (e) {
  console.error(`✗ drill 2 (shape assert) FAILED:\n  ${e.message.split('\n').slice(0, 4).join('\n  ')}`);
  process.exit(1);
}

// 3. re-apply the C1 migration (with the #3543 tenancy vocabulary mapped —
//    see applyPostRenameVocabulary; the migration text is frozen history and
//    predates the rename).
try {
  await db.exec(applyPostRenameVocabulary(readFileSync(`${MIG_DIR}/${C1_MIGRATION}`, 'utf8')));
  console.log('✓ drill 3: C1 migration re-applied');
} catch (e) {
  console.error(`✗ drill 3 (re-apply) FAILED:\n  ${e.message.split('\n').slice(0, 4).join('\n  ')}`);
  process.exit(1);
}

// 4. re-run the C1 assertion suite — invariants hold after re-apply.
try {
  await db.exec(readFileSync(`${TESTS_DIR}/${C1_SUITE}`, 'utf8'));
  console.log('✓ drill 4: C1 assertion suite passes after re-apply');
} catch (e) {
  console.error(`✗ drill 4 (post-reapply suite) FAILED:\n  ${e.message.split('\n').slice(0, 8).join('\n  ')}`);
  process.exit(1);
}

console.log('✅ ROLLBACK DRILL PASSED (apply → rollback → re-apply round trip)');

// ── #3036: OAuth FK migration re-applies idempotently ──────────────────────
// The suite above proves the constraints BEHAVE (dangling refs rejected, the
// ON DELETE SET NULL action). Re-executing the migration text on a database
// that already has both constraints proves the DROP-then-ADD pair plus the
// dangling-pointer repair statements are idempotent — the deploy path runs
// this file again on any environment where it was already applied.
try {
  await db.exec(readFileSync(`${MIG_DIR}/20260925000001_oauth_referential_integrity.sql`, 'utf8'));
  console.log('✓ #3036: OAuth FK migration re-applies idempotently');
} catch (e) {
  console.error(`✗ #3036 migration re-apply FAILED:\n  ${e.message.split('\n').slice(0, 4).join('\n  ')}`);
  process.exit(1);
}

console.log('✅ ALL MIGRATIONS + BOTH TEST SUITES + SPOT CHECKS PASSED');

await db.close();
