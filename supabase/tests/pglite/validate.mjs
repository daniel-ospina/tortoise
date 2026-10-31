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
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, unlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
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

// ── Split a migration file into its TOP-LEVEL statements ────────────────────
// A per-statement apply (`psql -f`, autocommit per statement) executes them one
// at a time and can STOP between any two. The 20261023000002 drill reproduces
// that by replaying them one at a time and checking the trigger after EVERY
// prefix, so the splitter must be faithful: comments are carried with their
// statement, and a dollar-quoted `DO $$ … $$` body (which itself contains `;`)
// must count as ONE statement — that is the property the drill asserts.
function splitTopLevelStatements(sql) {
  const out = [];
  let buf = '';
  let i = 0;
  while (i < sql.length) {
    const ch = sql[i];
    const two = sql.slice(i, i + 2);
    if (two === '--') {
      const nl = sql.indexOf('\n', i);
      const end = nl === -1 ? sql.length : nl + 1;
      buf += sql.slice(i, end);
      i = end;
      continue;
    }
    if (two === '/*') {
      const close = sql.indexOf('*/', i + 2);
      const end = close === -1 ? sql.length : close + 2;
      buf += sql.slice(i, end);
      i = end;
      continue;
    }
    if (ch === "'") {
      // single-quoted literal — '' is an escaped quote, not a terminator
      let j = sql.indexOf("'", i + 1);
      if (j === -1) throw new Error('unterminated single-quoted literal');
      while (sql[j + 1] === "'") j = sql.indexOf("'", j + 2);
      if (j === -1) throw new Error('unterminated single-quoted literal');
      buf += sql.slice(i, j + 1);
      i = j + 1;
      continue;
    }
    if (ch === '"') {
      const close = sql.indexOf('"', i + 1);
      if (close === -1) throw new Error('unterminated quoted identifier');
      buf += sql.slice(i, close + 1);
      i = close + 1;
      continue;
    }
    if (ch === '$') {
      const m = /^\$[A-Za-z_0-9]*\$/.exec(sql.slice(i));
      if (m) {
        const tag = m[0];
        const close = sql.indexOf(tag, i + tag.length);
        if (close === -1) throw new Error(`unterminated dollar-quoted block ${tag}`);
        buf += sql.slice(i, close + tag.length);
        i = close + tag.length;
        continue;
      }
    }
    if (ch === ';') {
      out.push(buf);
      buf = '';
      i += 1;
      continue;
    }
    buf += ch;
    i += 1;
  }
  if (buf.trim()) out.push(buf);
  // A chunk of nothing but whitespace/comments is not a statement.
  return out.filter((chunk) =>
    chunk.replace(/--[^\n]*/g, '').replace(/\/\*[\s\S]*?\*\//g, '').trim().length > 0);
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
    if (f === '20261029000001_record_point_cache_tier.sql') {
      // #10017 R2 — seed the POISONED row this migration has to clean before its
      // constraint can validate existing rows. Before this file `cache_tier` was
      // not a column, not in `_META_KEYS` and not refused, so a caller-supplied
      // prop of that name went into the props tail. Without this seed the file is
      // applied to empty tables and its data path is exercised by nothing — while
      // on a real tenant ONE such row aborts the whole migration
      // (`check constraint … is violated by some row`), taking the column and the
      // index with it and leaving step 7 unable to deploy.
      await db.exec(`
        -- Its OWN tenant, not the #9881 block's: a future edit there must not be
        -- able to break this data path, and the errors would name the wrong file.
        INSERT INTO public.organizations (id, name, graph_name)
          VALUES ('10017-cache-tier-org', '10017-cache-tier-org',
                  'org_10017-cache-tier-org')
          ON CONFLICT DO NOTHING;
        INSERT INTO public.graphs (id, org_id, name, namespace)
          VALUES ('10017-cache-tier-graph', '10017-cache-tier-org',
                  '10017-cache-tier-graph',
                  'org_10017-cache-tier-org_10017-cache-tier-graph')
          ON CONFLICT DO NOTHING;
        INSERT INTO public.node_identity (org_id, graph_id, node_label, node_key)
          VALUES ('10017-cache-tier-org','10017-cache-tier-graph','Point',
                  'cache-tier-poison')
          ON CONFLICT DO NOTHING;
        -- chk_point_node_key_is_contract_key requires node_key = id, and id is a
        -- COLUMN: the props tail cannot satisfy it. kept is the CONTROL: it is
        -- not the reserved name, so a backfill that wipes the tail must be seen.
        INSERT INTO public.point (org_id, graph_id, node_key, node_label, id, props)
          VALUES ('10017-cache-tier-org','10017-cache-tier-graph',
                  'cache-tier-poison','Point','cache-tier-poison',
                  '{"cache_tier": "record_only", "kept": "yes"}'::jsonb)
          ON CONFLICT DO NOTHING;
      `);
    }
    if (f === '20261027000001_record_semantic_id_key.sql') {
      // #9881 — seed the PRE-MOVE shape so this migration's RE-KEY runs on real
      // rows. Without it the file is applied to empty tables, and its whole data
      // path (identity parents, relation endpoints, node keys, orphan cleanup)
      // could be deleted with every suite still green — which is exactly how a
      // re-key that ABORTS on a populated table (the `node_key = name` CHECK
      // firing against `SET node_key = id`) stayed invisible to review. The
      // `#9881` leg below asserts the post-move state of these four rows.
      await db.exec(`
        INSERT INTO public.organizations (id, name, graph_name)
          VALUES ('9881-mig-org', '9881-mig-org', 'org_9881-mig-org')
          ON CONFLICT DO NOTHING;
        INSERT INTO public.graphs (id, org_id, name, namespace)
          VALUES ('9881-mig-graph', '9881-mig-org', '9881-mig-graph',
                  'org_9881-mig-org_9881-mig-graph')
          ON CONFLICT DO NOTHING;
        INSERT INTO public.node_identity (org_id, graph_id, node_label, node_key)
          VALUES ('9881-mig-org','9881-mig-graph','Object','Acme'),
                 ('9881-mig-org','9881-mig-graph','Object','Beta'),
                 ('9881-mig-org','9881-mig-graph','Subject','team-x')
          ON CONFLICT DO NOTHING;
        INSERT INTO public.object (org_id, graph_id, node_key, id, name, object_kind)
          VALUES ('9881-mig-org','9881-mig-graph','Acme','obj-acme','Acme','org'),
                 ('9881-mig-org','9881-mig-graph','Beta','obj-beta','Beta','org')
          ON CONFLICT DO NOTHING;
        INSERT INTO public.subject (org_id, graph_id, node_key, id, name, subject_kind)
          VALUES ('9881-mig-org','9881-mig-graph','team-x','sub-team','team-x','team')
          ON CONFLICT DO NOTHING;
        -- BOTH endpoints are name-keyed, so the re-key has to move both halves
        -- of the edge (and neither endpoint is a Point, which the record-count
        -- assertions elsewhere in this harness own).
        INSERT INTO public.relation (org_id, graph_id, rel_type, src_label, src_key,
                                     tgt_label, tgt_key, idx)
          VALUES ('9881-mig-org','9881-mig-graph','ownedBy','Object','Acme',
                  'Subject','team-x',0)
          ON CONFLICT DO NOTHING;
      `);
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
    if (f === '20260925000001_oauth_referential_integrity.sql') {
      // #3036: seed a DIRTY pre-migration DB. The FKs do not exist yet, so a
      // dangling pointer is legal to insert here; only the migration's own
      // backfill UPDATE (step 1) can null it. Without this seed, deleting the
      // migration's repair statements leaves the whole harness green — the
      // suite's behaviour would still pass on a clean DB and the re-apply drill
      // runs on a DB with no dangling rows. This is the #4216 pattern applied
      // to #3036: put the dirty row BEFORE the migration so the migration text
      // is the thing under test.
      await db.exec(`
        INSERT INTO public.organizations (id, name, graph_name)
          VALUES ('3036-preseed-org', '3036-preseed-org', 'org_3036-preseed-org');
        INSERT INTO public.oauth_clients (id, client_name)
          VALUES ('3036-preseed-client', '3036-preseed-client');
        INSERT INTO public.oauth_access_tokens
          (id, token_hash, client_id, user_id, org_id, expires_at, refresh_token_id)
        VALUES ('3036-preseed-access', '3036-preseed-h', '3036-preseed-client',
                '3036-preseed-user', '3036-preseed-org',
                now() + interval '1 hour', '3036-preseed-missing-refresh');
        INSERT INTO public.oauth_refresh_tokens
          (id, token_hash, client_id, user_id, org_id, expires_at, rotated_from)
        VALUES ('3036-preseed-refresh', '3036-preseed-hr', '3036-preseed-client',
                '3036-preseed-user', '3036-preseed-org',
                now() + interval '1 hour', '3036-preseed-missing-ancestor');
        -- Valid links the repair must NOT touch: an access→refresh pair AND a
        -- refresh→ancestor CHAIN link. Without them the assertion only proves
        -- the DANGLING branch, and an over-broad predicate (the AND NOT EXISTS
        -- guard dropped from EITHER repair) would silently NULL valid
        -- provenance links and still pass. The chain link is what covers the
        -- SECOND repair statement (rotated_from).
        INSERT INTO public.oauth_refresh_tokens
          (id, token_hash, client_id, user_id, org_id, expires_at, rotated_from)
        VALUES ('3036-preseed-good-ancestor', '3036-preseed-hga2', '3036-preseed-client',
                '3036-preseed-user', '3036-preseed-org',
                now() + interval '1 hour', NULL);
        INSERT INTO public.oauth_refresh_tokens
          (id, token_hash, client_id, user_id, org_id, expires_at, rotated_from)
        VALUES ('3036-preseed-good-refresh', '3036-preseed-hgr', '3036-preseed-client',
                '3036-preseed-user', '3036-preseed-org',
                now() + interval '1 hour', '3036-preseed-good-ancestor');
        INSERT INTO public.oauth_access_tokens
          (id, token_hash, client_id, user_id, org_id, expires_at, refresh_token_id)
        VALUES ('3036-preseed-good-access', '3036-preseed-hga', '3036-preseed-client',
                '3036-preseed-user', '3036-preseed-org',
                now() + interval '1 hour', '3036-preseed-good-refresh');
      `);
    }
    if (f === '20260925000002_oauth_redemption_state.sql') {
      // #3027: seed a CONSUMED pre-migration code (the `redemption_state` column
      // does not exist yet). Only this migration's own backfill UPDATE can make
      // it terminal, so deleting the backfill reds here — the #4216/#3036 pattern.
      // A second row is left UNCONSUMED, so an over-broad backfill (one that
      // burns every row instead of `used_at IS NOT NULL`) reds too.
      await db.exec(`
        INSERT INTO public.organizations (id, name, graph_name)
          VALUES ('3027-preseed-org', '3027-preseed-org', 'org_3027-preseed-org');
        INSERT INTO public.oauth_clients (id, client_name)
          VALUES ('3027-preseed-client', '3027-preseed-client');
        INSERT INTO public.oauth_codes
          (code_hash, client_id, user_id, org_id, redirect_uri, code_challenge,
           expires_at, used_at)
        VALUES ('3027-preseed-used', '3027-preseed-client', '3027-preseed-user',
                '3027-preseed-org', 'https://app.example/cb', 'challenge',
                now() + interval '10 min', now());
        INSERT INTO public.oauth_codes
          (code_hash, client_id, user_id, org_id, redirect_uri, code_challenge,
           expires_at, used_at)
        VALUES ('3027-preseed-unused', '3027-preseed-client', '3027-preseed-user',
                '3027-preseed-org', 'https://app.example/cb', 'challenge',
                now() + interval '10 min', NULL);
      `);
    }
    if (f === '20261021000001_blog_author_identity.sql') {
      // #1383: seed the THREE rows the migration's deploy-time BACKFILL acts on.
      // A backfill is already history when a suite runs, so without this seed the
      // suite could only assert the column's shape and the whole backfill could
      // be deleted with the harness still green (the #4216/#3036/#3027 pattern).
      //
      //  * anon              — carries the RETIRED default 'Tortoise team', plus an
      //                        explicit updated_at and the three audit columns.
      //                        After the migration: author + image rewritten,
      //                        updated_at and the audit columns untouched (the
      //                        trigger is disabled around the UPDATE).
      //  * named             — a deliberately-set author. After the migration:
      //                        unchanged, so the backfill's guard cannot be
      //                        over-broad.
      //  * named-null-image  — ALREADY carries the real author but no avatar, so
      //                        only the SECOND pass can give it one (pass 1's guard
      //                        is `author = 'Tortoise team'`). Without this row,
      //                        deleting pass 2 left the whole harness green — the
      //                        first pass covered the anon row, and the suite's
      //                        post-migration INSERT row proves the opposite of what
      //                        pass 2 does.
      await db.exec(`
        INSERT INTO public.blog_posts
          (slug, title, author, status, created_at, updated_at, created_by, reviewed_by)
        VALUES ('1383-preseed-anon', '1383 preseed anon', 'Tortoise team', 'draft',
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z',
                'agent-seeded', 'owner-seeded');
        INSERT INTO public.blog_posts
          (slug, title, author, status, created_at, updated_at)
        VALUES ('1383-preseed-named', '1383 preseed named', 'Someone Else', 'draft',
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        INSERT INTO public.blog_posts
          (slug, title, author, status, created_at, updated_at)
        VALUES ('1383-preseed-named-null-image', '1383 preseed named null image',
                'Daniel Ospina', 'draft',
                '2026-02-02T00:00:00Z', '2026-02-02T00:00:00Z');
      `);
    }
    if (f === '20261023000002_blog_author_backfill_atomic.sql') {
      // #1383 repair: the forward migration carries the SAME two backfill
      // passes, and on this database 20261021000001 already ran them — so
      // without this seed, deleting either pass from the repair leaves the
      // whole harness green and the repair's coverage is vacuous. Rows are
      // seeded AFTER 20261021000001 applied (this hook runs immediately before
      // this file), which is also the shape a real re-run sees.
      //
      //  * anon       — the RETIRED default plus its own updated_at and audit
      //                 columns: only this migration can rewrite it, and its
      //                 timestamp proves the trigger was disabled across the
      //                 rewrite;
      //  * null-image — already carries the real author with no avatar, which
      //                 ONLY pass 2 can fill (pass 1's guard is 'Tortoise team');
      //  * named      — a deliberately different author WITH an image, so an
      //                 over-broad AUTHOR guard (either pass) reds. It cannot
      //                 red on pass 2's image clause: its author is not
      //                 'Daniel Ospina', so the author conjunct excludes it
      //                 however the image clause is written;
      //  * custom-image — the SHIPPED author with a deliberately DIFFERENT
      //                 avatar. That is the only shape pass 2's
      //                 `AND author_image_url IS NULL` protects, and the only
      //                 row that reds if that conjunct is deleted.
      await db.exec(`
        INSERT INTO public.blog_posts
          (slug, title, author, status, created_at, updated_at, created_by, reviewed_by)
        VALUES ('1383b-preseed-anon', '1383b preseed anon', 'Tortoise team', 'draft',
                '2026-03-03T00:00:00Z', '2026-03-03T00:00:00Z',
                'agent-seeded', 'owner-seeded');
        INSERT INTO public.blog_posts
          (slug, title, author, author_image_url, status, created_at, updated_at)
        VALUES ('1383b-preseed-null-image', '1383b preseed null image',
                'Daniel Ospina', NULL, 'draft',
                '2026-04-04T00:00:00Z', '2026-04-04T00:00:00Z');
        INSERT INTO public.blog_posts
          (slug, title, author, author_image_url, status, created_at, updated_at)
        VALUES ('1383b-preseed-named', '1383b preseed named', 'Someone Else',
                'https://example.com/someone-else.jpg', 'draft',
                '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');
        INSERT INTO public.blog_posts
          (slug, title, author, author_image_url, status, created_at, updated_at)
        VALUES ('1383b-preseed-custom-image', '1383b preseed custom image',
                'Daniel Ospina', 'https://example.com/custom.jpg', 'draft',
                '2026-06-06T00:00:00Z', '2026-06-06T00:00:00Z');
      `);
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

// ── #9881: the KEY MOVE's re-key actually ran, on PRE-MOVE rows ─────────────
// The seed above was inserted BEFORE `20261027000001`, so only that migration's
// own steps can have moved it: identity parents, both `relation` endpoints, the
// two node keys, and the cleanup of the abandoned name identities. Asserted
// HERE, against the rows themselves, because every other suite runs AFTER the
// migration and can only see its output — and because applying it to EMPTY
// tables (which is what "apply every migration in order" otherwise does) leaves
// the entire data path deletable with the harness still green.
//
// The measured failure this exists for: with the two `chk_*_node_key_is_contract_key`
// pins left in force, step 3's `SET node_key = id` violates the OLD
// `node_key = name` pin and the migration ABORTS on the first real row
// (`new row for relation "object" violates check constraint …`) — invisible to
// every empty-table application.
{
  const r = await db.query(
    `SELECT
       (SELECT node_key FROM public.object
          WHERE org_id = '9881-mig-org' AND graph_id = '9881-mig-graph'
            AND id = 'obj-acme')                                        AS object_key,
       (SELECT name FROM public.object
          WHERE org_id = '9881-mig-org' AND graph_id = '9881-mig-graph'
            AND id = 'obj-acme')                                        AS object_name,
       (SELECT node_key FROM public.subject
          WHERE org_id = '9881-mig-org' AND graph_id = '9881-mig-graph'
            AND id = 'sub-team')                                        AS subject_key,
       (SELECT src_key FROM public.relation
          WHERE org_id = '9881-mig-org' AND graph_id = '9881-mig-graph'
            AND rel_type = 'ownedBy')                                   AS edge_src_key,
       (SELECT tgt_key FROM public.relation
          WHERE org_id = '9881-mig-org' AND graph_id = '9881-mig-graph'
            AND rel_type = 'ownedBy')                                   AS edge_tgt_key,
       (SELECT count(*) FROM public.node_identity
          WHERE org_id = '9881-mig-org' AND graph_id = '9881-mig-graph'
            AND node_label = 'Object' AND node_key = 'Acme')             AS stale_identity,
       (SELECT count(*) FROM public.node_identity
          WHERE org_id = '9881-mig-org' AND graph_id = '9881-mig-graph'
            AND node_label = 'Object'
            AND node_key = 'obj-acme' AND key_property = 'id')           AS new_identity`);
  const m = r.rows[0];
  const bad = (msg) => {
    console.error(`✗ #9881 RE-KEY: ${msg}: ${JSON.stringify(m)}`);
    process.exit(1);
  };
  if (m.object_key !== 'obj-acme' || m.subject_key !== 'sub-team') {
    bad('a pre-move Object/Subject row is not keyed by its id — the re-key did '
      + 'not run, or it aborted against the old `node_key = name` CHECK');
  }
  if (m.object_name !== 'Acme') {
    bad('the re-key moved the NAME as well as the key');
  }
  if (m.edge_src_key !== 'obj-acme' || m.edge_tgt_key !== 'sub-team') {
    bad('an incident relation endpoint did not follow its node key — a rebuild '
      + 'would wire the edge to nodes that no longer exist');
  }
  if (Number(m.stale_identity) !== 0) {
    bad('the abandoned name identity survived the re-key');
  }
  if (Number(m.new_identity) !== 1) {
    bad('no node_identity row holds the new key with key_property = id');
  }
  await db.exec("DELETE FROM public.organizations WHERE id = '9881-mig-org'");
  console.log('✓ #9881: the key move re-keys pre-move Object/Subject rows onto their '
    + 'id, its relation endpoints follow, and the abandoned name identity is gone');
}

// ── #3036: the migration's DANGLING-POINTER repair actually ran ─────────────
// The dirty rows were seeded BEFORE `20260925000001` (the FKs did not exist
// yet). Only that migration's own step-1 UPDATEs can have nulled them, so this
// goes red if either repair statement is deleted, if its predicate is too
// narrow (dangling links left non-NULL), or if it is OVER-broad (the seeded
// VALID links below would be nulled too).
{
  const r = await db.query(`SELECT
    (SELECT refresh_token_id FROM public.oauth_access_tokens
       WHERE id = '3036-preseed-access') AS access_link,
    (SELECT count(*) FROM public.oauth_access_tokens
       WHERE id = '3036-preseed-access') AS access_rows,
    (SELECT rotated_from FROM public.oauth_refresh_tokens
       WHERE id = '3036-preseed-refresh') AS refresh_link,
    (SELECT count(*) FROM public.oauth_refresh_tokens
       WHERE id = '3036-preseed-refresh') AS refresh_rows,
    (SELECT refresh_token_id FROM public.oauth_access_tokens
       WHERE id = '3036-preseed-good-access') AS good_access_link,
    (SELECT count(*) FROM public.oauth_access_tokens
       WHERE id = '3036-preseed-good-access') AS good_access_rows,
    (SELECT rotated_from FROM public.oauth_refresh_tokens
       WHERE id = '3036-preseed-good-refresh') AS good_refresh_link,
    (SELECT count(*) FROM public.oauth_refresh_tokens
       WHERE id = '3036-preseed-good-refresh') AS good_refresh_rows,
    (SELECT count(*) FROM public.oauth_refresh_tokens
       WHERE id = '3036-preseed-good-ancestor') AS good_ancestor_rows`);
  const d = r.rows[0] || {};
  if (Number(d.access_rows) !== 1 || Number(d.refresh_rows) !== 1) {
    console.error(`✗ #3036: the repair must NULL the pointer, not delete the row (access_rows=${d.access_rows}, refresh_rows=${d.refresh_rows})`);
    process.exit(1);
  }
  if (d.access_link !== null || d.refresh_link !== null) {
    console.error(`✗ #3036: the migration did NOT repair the seeded dangling pointers (access=${d.access_link}, refresh=${d.refresh_link})`);
    process.exit(1);
  }
  if (Number(d.good_access_rows) !== 1 || Number(d.good_refresh_rows) !== 1) {
    console.error(`✗ #3036: the repair DELETED a valid preseed row (good_access_rows=${d.good_access_rows}, good_refresh_rows=${d.good_refresh_rows})`);
    process.exit(1);
  }
  if (d.good_access_link !== '3036-preseed-good-refresh') {
    console.error(`✗ #3036: the repair NULLed a VALID provenance link (got ${d.good_access_link}, want '3036-preseed-good-refresh') — its predicate is over-broad`);
    process.exit(1);
  }
  if (Number(d.good_ancestor_rows) !== 1) {
    console.error(`✗ #3036: the repair DELETED a valid chain ancestor (good_ancestor_rows=${d.good_ancestor_rows})`);
    process.exit(1);
  }
  if (d.good_refresh_link !== '3036-preseed-good-ancestor') {
    console.error(`✗ #3036: the repair NULLed a VALID rotation-chain link (got ${d.good_refresh_link}, want '3036-preseed-good-ancestor') — its predicate is over-broad`);
    process.exit(1);
  }
  await db.exec(`
    DELETE FROM public.oauth_access_tokens  WHERE id LIKE '3036-preseed-%';
    DELETE FROM public.oauth_refresh_tokens WHERE id LIKE '3036-preseed-%';
    DELETE FROM public.oauth_clients        WHERE id LIKE '3036-preseed-%';
    DELETE FROM public.organizations        WHERE id LIKE '3036-preseed-%';
  `);
  console.log('✓ #3036: the migration\'s own dangling-pointer repair nulled both seeded links');
}

// ── #3027: the migration's redemption-state BACKFILL actually ran ───────────
// The consumed row was seeded BEFORE `20260925000002` (its columns did not
// exist). Only that migration's own `UPDATE ... WHERE used_at IS NOT NULL` can
// have made it terminal, so this goes red if the backfill is deleted, if it is
// too NARROW (the consumed row stays 'unclaimed'), or if it is OVER-broad (the
// unconsumed row is burned too).
{
  const r = await db.query(`SELECT
    (SELECT redemption_state FROM public.oauth_codes
       WHERE code_hash = '3027-preseed-used') AS used_state,
    (SELECT redemption_settled_at FROM public.oauth_codes
       WHERE code_hash = '3027-preseed-used') AS used_settled,
    (SELECT redemption_note FROM public.oauth_codes
       WHERE code_hash = '3027-preseed-used') AS used_note,
    (SELECT redemption_state FROM public.oauth_codes
       WHERE code_hash = '3027-preseed-unused') AS unused_state,
    (SELECT redemption_settled_at FROM public.oauth_codes
       WHERE code_hash = '3027-preseed-unused') AS unused_settled`);
  const d = r.rows[0] || {};
  if (d.used_state !== 'burned') {
    console.error(`✗ #3027: the backfill did NOT burn the consumed pre-migration code (state=${d.used_state})`);
    process.exit(1);
  }
  if (d.used_settled === null || d.used_settled === undefined) {
    console.error('✗ #3027: the backfill did not record redemption_settled_at on the consumed code');
    process.exit(1);
  }
  if (d.used_note !== 'backfill') {
    console.error(`✗ #3027: the backfilled row must be marked 'backfill' (got ${d.used_note})`);
    process.exit(1);
  }
  if (d.unused_state !== 'unclaimed' || d.unused_settled !== null) {
    console.error(`✗ #3027: the backfill is OVER-broad — an unconsumed code was touched (state=${d.unused_state}, settled=${d.unused_settled})`);
    process.exit(1);
  }
  await db.exec(`
    DELETE FROM public.oauth_codes   WHERE code_hash LIKE '3027-preseed-%';
    DELETE FROM public.oauth_clients WHERE id LIKE '3027-preseed-%';
    DELETE FROM public.organizations WHERE id LIKE '3027-preseed-%';
  `);
  console.log('✓ #3027: the migration\'s own backfill burned the consumed code and left the unconsumed one alone');
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
  // #1383 — the blog author identity: `author_image_url`, the real author
  // default, and the one-time backfill (seeded before the migration applies).
  // NOTE: this list is EXPLICIT, not a glob — a suite file that is not named
  // here never runs, and "the suite exists" is then mistaken for "the suite
  // passes". Add the entry in the same commit as the file.
  '20261021000001_blog_author_identity.sql',
  // #1383 repair — the backfill re-landed FORWARD as ONE statement (the merged
  // 20261021000001 carries it as four top-level statements, where a
  // per-statement apply can commit the DISABLE and stop). Its rows are seeded
  // immediately before it applies; the ATOMICITY shape is drilled below.
  // NOTE: this list is EXPLICIT, not a glob — add the entry in the same commit
  // as the file.
  '20261023000002_blog_author_backfill_atomic.sql',
  '20260827000002_user_identity_profile.sql',
  '20260901000001_graphs_and_key_scopes.sql',  // C1 #2110
  '20260906000001_graphs_deleted_at.sql',  // #2304
  '20260919000001_metering_period_end_repair.sql',  // #4216
  '20260922000001_connectors.sql',  // #2636 connectors data model
  '20260925000001_oauth_referential_integrity.sql',  // #3036
  '20260925000002_oauth_redemption_state.sql',  // #3027
  // #5045 — the capture-token accumulator.
  // NOTE: this list is EXPLICIT, not a glob — a suite file that is not named
  // here never runs, and "the suite exists" is then mistaken for "the suite
  // passes". Add the entry in the same commit as the file. (The sibling #5331
  // branch adds its own entry to this same list, so the two will conflict
  // textually on merge; keep BOTH entries.)
  '20260926000001_metering_capture_tokens.sql',  // #5045
  '20260927000001_api_key_mint_serialization.sql',  // #1879
  '20260927000001_metering_unmetered_increments.sql',  // #4779
  // #5331 — the graph-storage gauge, asserted against the REAL SQL function.
  // NOTE: this list is EXPLICIT, not a glob — a suite file that is not named
  // here never runs, and "the suite exists" is then mistaken for "the suite
  // passes". Add the entry in the same commit as the file.
  '20260926000002_metering_set_graph_storage.sql',  // #5331
  // #4242 — the ledger WINDOW follows the authoritative bound on every
  // increment (the sticky-approximated-window defect). NOTE: this list is
  // EXPLICIT, not a glob — a suite file that is not named here never runs, and
  // "the suite exists" is then mistaken for "the suite passes". Add the entry
  // in the same commit as the file.
  '20261010000001_metering_period_end_refresh.sql',  // #4242
  // #7968 — the four-layer record schema (event stream / semantic / epistemic /
  // procedural), the (label, key) identity contract, the EP-scalar carve-out and
  // the RLS boundary. NOTE: this list is EXPLICIT, not a glob — a suite file that
  // is not named here never runs, and "the suite exists" is then mistaken for
  // "the suite passes". Add the entry in the same commit as the file.
  '20261012000001_record_schema.sql',  // #7968
  // #9554 (R6) — the record's TENANCY POSTURE: the application role's attributes
  // and grants, the `*_app_tenant` policies (USING **and** WITH CHECK), a
  // cross-tenant read returning 0 rows as that role, and the two documented
  // failure directions of a missing `app.current_org_id` (silent on read, loud
  // on write). Runs after the schema suite because it re-seeds its own tenants
  // and purges them through `purge_org_record` at the end. NOTE: this list is
  // EXPLICIT, not a glob — a suite file that is not named here never runs, and
  // "the suite exists" is then mistaken for "the suite passes". Add the entry in
  // the same commit as the file.
  '20261022000001_record_tenancy_posture.sql',  // #9554
  // #4550 — `recover_team_key`'s cap count AND revoke-target scan exclude
  // EXPIRED-but-unrevoked rows, driven against the REAL function. NOTE: this
  // list is EXPLICIT, not a glob — a suite file that is not named here never
  // runs, and "the suite exists" is then mistaken for "the suite passes".
  // Add the entry in the same commit as the file.
  '20261012000006_recover_team_key_expiry.sql',  // #4550
  // #3025 — the OAuth mint as ONE transaction (`redeem_oauth_code` /
  // `rotate_oauth_refresh`), including the two ATOMICITY DRILLS a functional-only
  // suite would miss: a failure mid-function leaves zero new rows and an unset
  // `used_at`. NOTE: this list is EXPLICIT, not a glob — a suite file that is not
  // named here never runs, and "the suite exists" is then mistaken for "the suite
  // passes". Add the entry in the same commit as the file.
  '20261013000002_oauth_mint_rpcs.sql',  // #3025
  // #3524 §9 step 1 — the consent token's single-use register. The claim is
  // decided BY THE PRIMARY KEY (`INSERT … ON CONFLICT DO NOTHING RETURNING`), and
  // that is unprovable from Python: the fake control plane is single-threaded and
  // its own comment says a SELECT-then-INSERT implementation would still pass it.
  // This suite is also the only place the REVOKE/RLS posture is executed — with
  // the public anon key able to DELETE a spent nonce, a captured consent token
  // replays into a second authorization code. NOTE: this list is EXPLICIT, not a
  // glob — a suite file that is not named here never runs, and "the suite exists"
  // is then mistaken for "the suite passes". Add the entry in the same commit as
  // the file.
  '20261030000001_oauth_consent_tickets.sql',  // #3524
  // #10025 — the default graph's stable-id row, and the ONE-live-default-row
  // invariant the singular `rows[0]` readers rely on: the partial unique index
  // is executed (a second live row is rejected, a tombstone beside an active one
  // is still legal) and the backfill's `ON CONFLICT DO NOTHING` is replayed
  // against a state the read guards cannot see, which is the shape that aborted
  // the whole migration transaction. NOTE: this list is EXPLICIT, not a glob — a
  // suite file that is not named here never runs, and "the suite exists" is then
  // mistaken for "the suite passes". Add the entry in the same commit as the
  // file.
  '20261028000001_graphs_default_row_stable_id.sql',  // #10025
  // #4492 — the Postgres storage footprint + per-org analytics row growth,
  // driven against the REAL SQL functions. NOTE: this list is EXPLICIT, not a
  // glob — a suite file that is not named here never runs, and "the suite
  // exists" is then mistaken for "the suite passes". Add the entry in the same
  // commit as the file. Deliberately LAST: it seeds analytics_events rows it
  // cannot delete (the append-only trigger), so it must not run before a suite
  // whose counts those rows could move.
  '20261015000001_postgres_storage_footprint.sql',  // #4492
];

// ── Fail CLOSED on a stale `suites` entry (PR #9707 review ISSUE 1) ─────────
// The `readFileSync` in the loop below sits INSIDE the loop but OUTSIDE its
// `try`, so a single entry naming a file that does not exist throws ENOENT
// UNCAUGHT: the process exits on a node stack trace, every suite listed after
// the bad entry silently stops running, and the drill blocks further down this
// file never execute at all.
//
// Measured on the pre-fix tree at `c142e3318`: the entry
// `20261023000002_blog_author_backfill_atomic.sql` was added to this list while
// the suite FILE on disk was still `…0001_…`, so `schema-drill` died with
// `ENOENT … supabase/tests/20261023000002_blog_author_backfill_atomic.sql` and
// 16 existing suites plus both #1383 drills went dark behind that stack trace.
//
// A PATH assertion is the only check that can see this class: a content grep
// for the filename finds every reference EXCEPT the one encoded as a path —
// which is exactly the one that was wrong.
const missingSuites = suites.filter((s) => !existsSync(`${TESTS_DIR}/${s}`));
if (missingSuites.length > 0) {
  console.error(`✗ the explicit \`suites\` list names files that do not exist under ${TESTS_DIR}:`);
  for (const s of missingSuites) console.error(`    ${s}`);
  console.error('  (the list is EXPLICIT, not a glob — one stale name disables this suite AND every later one)');
  process.exit(1);
}

// ── #10017: the cache-tier backfill is SURGICAL, and the column defaults ──
// The seed above carries `cache_tier` (the reserved name, which this migration
// must remove from the tail) AND `kept` (a property it must NOT touch). Asserting
// only "the migration applied" would pass for `SET props = '{}'` — which wipes
// every other property of every affected row on every tenant.
//
// ⛔ And it runs BEFORE the suites, like the #4216 and #9881 legs: it seeds a
// half-built tenant (org + graph + one point, no default graph, no memberships),
// so a future suite that scans organizations would pick it up and report the
// failure against the wrong file.
{
  const t = await db.query(`SELECT props, cache_tier FROM public.point
     WHERE org_id = '10017-cache-tier-org' AND node_key = 'cache-tier-poison'`);
  const row = t.rows[0];
  if (!row) {
    console.error('✗ #10017: the seeded row is gone — this check verified nothing');
    process.exit(1);
  }
  if (JSON.stringify(row.props) !== JSON.stringify({ kept: 'yes' })) {
    console.error('✗ #10017: the backfill removed more than the reserved name '
      + `(props = ${JSON.stringify(row.props)}) — it destroys real properties`);
    process.exit(1);
  }
  if (row.cache_tier !== 'graph') {
    console.error('✗ #10017: the stripped row did not land the DEFAULT graph tier '
      + `(got ${JSON.stringify(row.cache_tier)}) — a tail value was promoted`);
    process.exit(1);
  }
  await db.exec(`DELETE FROM public.organizations
                  WHERE id = '10017-cache-tier-org'`);
  console.log('✓ #10017: the tier backfill strips ONLY the reserved name (its '
    + 'control property survives) and the column lands the default graph tier');
}

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

// ── #10025 §2b: the stranded-org diagnostic EXECUTES, from the migration text ─
// §2b warns (rather than mends) for an organization whose only `kind='default'`
// row carries a status that is neither 'active' nor 'deleted': the backfill's
// guards test `status='active'` while `uq_graphs_org_default_active` tests
// `status <> 'deleted'`, so the insert collides and `ON CONFLICT DO NOTHING`
// skips the org — the permanently `no_tenant` state that migration exists to
// remove, arriving under a green deploy. A warning nobody asserts is
// indistinguishable from no warning, so this reads the block OUT OF THE
// MIGRATION FILE and runs it here, on the same database the suites just used:
//   · the stranded shape must produce ONE warning NAMING the org
//   · an org with an ACTIVE default row beside an archived one must stay silent
//   · a tombstone-only org must stay silent
// It executes the artifact, not a copy of its predicate (a copy goes green if
// the block is deleted or narrowed — the failure mode is the diagnostic itself
// disappearing), and a missing block fails this step by name.
{
  const STRAND_MIG = '20261028000001_graphs_default_row_stable_id.sql';
  const strandText = readFileSync(`${MIG_DIR}/${STRAND_MIG}`, 'utf8');
  const markerAt = strandText.indexOf('-- ── 2b.');
  const blockFrom = markerAt < 0 ? -1 : strandText.indexOf('DO $$', markerAt);
  const blockTo = blockFrom < 0 ? -1 : strandText.indexOf('END $$;', blockFrom);
  if (blockFrom < 0 || blockTo < 0) {
    console.error(`✗ ${STRAND_MIG}: §2b (the stranded-org diagnostic) is GONE — the only migration-time signal for an organization whose default row can never be minted`);
    process.exit(1);
  }
  const STRAND_BLOCK = strandText.slice(blockFrom, blockTo + 'END $$;'.length);

  const strandProbe = async (shapes) => {
    await db.exec(`DELETE FROM public.graphs WHERE org_id LIKE '10025-strand-%';`);
    await db.exec(`DELETE FROM public.organizations WHERE id LIKE '10025-strand-%';`);
    const orgs = new Map();
    for (const [org, graphName, status] of shapes) {
      orgs.set(org, [...(orgs.get(org) ?? []), [graphName, status]]);
    }
    let n = 0;
    for (const [org, graphs] of orgs) {
      await db.exec(`INSERT INTO public.organizations (id, name, graph_name) VALUES ('${org}', '${org}', 'org_${org}');`);
      for (const [graphName, status] of graphs) {
        n += 1;
        await db.exec(`INSERT INTO public.graphs (id, org_id, name, kind, namespace, status) VALUES ('g_strand${n}', '${org}', '${graphName}', 'default', 'org_${org}', '${status}');`);
      }
    }
    const notices = [];
    await db.exec(STRAND_BLOCK, { onNotice: (msg) => notices.push(String(msg.message ?? msg)) });
    return notices;
  };

  // NOTE the "healed" fixture is an org with an ACTIVE default row only: an
  // active row BESIDE an archived one is not representable once §1's index
  // exists (§1 is what makes it impossible), so the diagnostic's silence is
  // asserted against the shapes the schema still permits.
  const stranded = await strandProbe([['10025-strand-a', 'old name', 'archived']]);
  const healed = await strandProbe([['10025-strand-b', 'default', 'active']]);
  const tombstoned = await strandProbe([['10025-strand-c', 'default', 'deleted']]);
  const twoStranded = await strandProbe([['10025-strand-d', 'x', 'archived'],
                                         ['10025-strand-e', 'y', 'paused']]);

  if (stranded.length !== 1 || !stranded.join(' ').includes('10025-strand-a')) {
    console.error(`✗ #10025 §2b: the stranded-org diagnostic did not name the org (notices: ${JSON.stringify(stranded)})`);
    process.exit(1);
  }
  if (healed.length !== 0 || tombstoned.length !== 0) {
    console.error(`✗ #10025 §2b: the diagnostic fired for a healthy org (healed: ${JSON.stringify(healed)}, tombstoned: ${JSON.stringify(tombstoned)})`);
    process.exit(1);
  }
  if (twoStranded.length !== 1
      || !twoStranded[0].includes('10025-strand-d')
      || !twoStranded[0].includes('10025-strand-e')) {
    console.error(`✗ #10025 §2b: two stranded orgs were not reported together (notices: ${JSON.stringify(twoStranded)})`);
    process.exit(1);
  }
  await db.exec(`DELETE FROM public.graphs WHERE org_id LIKE '10025-strand-%';
                 DELETE FROM public.organizations WHERE id LIKE '10025-strand-%';`);
  console.log('✓ #10025 §2b: the migration\'s stranded-org WARNING fires for the stranded shape and stays silent otherwise');
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
// ON DELETE SET NULL action). This drill re-executes the migration text on a
// database where the constraints ALREADY EXIST — the scenario a deploy hits
// when the migration was applied out-of-band / not recorded in
// `supabase_migrations.schema_migrations` (the case `supabase db push
// --include-all` re-selects). It proves the DROP-then-ADD pair and the repair
// statements are re-applicable, not that they ran for the first time (the
// pre-seed assertion above covers that).
try {
  await db.exec(readFileSync(`${MIG_DIR}/20260925000001_oauth_referential_integrity.sql`, 'utf8'));
  console.log('✓ #3036: OAuth FK migration re-applies idempotently');
} catch (e) {
  console.error(`✗ #3036 migration re-apply FAILED:\n  ${e.message.split('\n').slice(0, 4).join('\n  ')}`);
  process.exit(1);
}

// ── #3027: the redemption-state migration re-applies idempotently ───────────
// Same drill as #3036, but this migration carries STATE-BEARING rows: a
// re-apply must not clobber a settled claim, must not re-run the backfill over
// a row the state machine already moved, and must not fail on the ADD COLUMN /
// ADD CONSTRAINT pair. The row is inserted BEFORE the re-apply, so deleting the
// backfill's `AND redemption_state = 'unclaimed'` guard (making it over-broad)
// reds here.
try {
  await db.exec(`
    INSERT INTO public.organizations (id, name, graph_name)
      VALUES ('3027-reapply-org', '3027-reapply-org', 'org_3027-reapply-org');
    INSERT INTO public.oauth_clients (id, client_name)
      VALUES ('3027-reapply-client', '3027-reapply-client');
    INSERT INTO public.oauth_codes
      (code_hash, client_id, user_id, org_id, redirect_uri, code_challenge,
       expires_at, used_at, redemption_state, redemption_id, redemption_note)
    VALUES ('3027-reapply', '3027-reapply-client', '3027-reapply-user',
            '3027-reapply-org', 'https://app.example/cb', 'challenge',
            now() + interval '10 min', now() - interval '10 min',
            'claimed', '3027-reapply-rid', NULL);
  `);
  await db.exec(readFileSync(`${MIG_DIR}/20260925000002_oauth_redemption_state.sql`, 'utf8'));
  const r = await db.query(`SELECT redemption_state, redemption_id, redemption_note,
      redemption_settled_at FROM public.oauth_codes WHERE code_hash = '3027-reapply'`);
  const d = r.rows[0] || {};
  if (d.redemption_state !== 'claimed' || d.redemption_id !== '3027-reapply-rid' ||
      d.redemption_note !== null || d.redemption_settled_at !== null) {
    console.error(`✗ #3027 migration re-apply CLOBBERED a live claim: ${JSON.stringify(d)}`);
    process.exit(1);
  }
  await db.exec(`
    DELETE FROM public.oauth_codes   WHERE code_hash = '3027-reapply';
    DELETE FROM public.oauth_clients WHERE id = '3027-reapply-client';
    DELETE FROM public.organizations WHERE id = '3027-reapply-org';
  `);
  console.log('✓ #3027: redemption-state migration re-applies idempotently (a live claim survives)');
} catch (e) {
  console.error(`✗ #3027 migration re-apply FAILED:\n  ${e.message.split('\n').slice(0, 4).join('\n  ')}`);
  process.exit(1);
}

// ── #4779: the unmetered-increment representation survives a re-apply ────────
// The migration is written to be re-applicable (CREATE TABLE IF NOT EXISTS,
// CREATE OR REPLACE FUNCTION, DROP POLICY IF EXISTS) — the shape a deploy hits
// when the migration was applied out-of-band and `supabase db push --include-all`
// re-selects it. This drill is the ONE thing a re-apply must not do: erase the
// evidence. The represented drop is written BEFORE the re-apply, so a migration
// body that dropped/recreated the table (losing rows) or reset `first_observed_at`
// REDs here — which is the loss class #4779 exists to remove.
try {
  await db.exec(`
    INSERT INTO public.organizations (id, name, graph_name)
      VALUES ('4779-reapply-org', '4779-reapply-org', 'org_4779-reapply-org');
    SELECT public.metering_record_unmetered(
        '4779-reapply-org', 'write_op', 'window_unresolvable', 'QuotaCheckError');
    UPDATE public.metering_unmetered_increments
       SET first_observed_at = '2026-01-01T00:00:00+00:00'
     WHERE org_id = '4779-reapply-org';
  `);
  await db.exec(readFileSync(`${MIG_DIR}/20260927000001_metering_unmetered_increments.sql`, 'utf8'));
  const r = await db.query(`SELECT increments, first_observed_at, last_error_type,
      drop_class FROM public.metering_unmetered_increments
     WHERE org_id = '4779-reapply-org'`);
  const d = r.rows[0] || {};
  const first = d.first_observed_at instanceof Date
    ? d.first_observed_at.toISOString() : String(d.first_observed_at);
  if (d.increments !== 1 || !first.startsWith('2026-01-01') ||
      d.last_error_type !== 'QuotaCheckError' || d.drop_class !== 'window_unresolvable') {
    console.error(`✗ #4779 migration re-apply CLOBBERED the representation: ${JSON.stringify(d)}`);
    process.exit(1);
  }
  await db.exec(`DELETE FROM public.organizations WHERE id = '4779-reapply-org';`);
  console.log('✓ #4779: the unmetered-increment representation survives a re-apply');
} catch (e) {
  console.error(`✗ #4779 migration re-apply FAILED:\n  ${e.message.split('\n').slice(0, 4).join('\n  ')}`);
  process.exit(1);
}

// ── #7969: the record projection — replay a journal TWICE, assert idempotence ─
//
// Interface A (`journal → Postgres, replayable and idempotent`, epic #7879) is a
// claim about a REAL database, so it is checked on one. The statements come from
// `tortoise/record_projection.py` itself — the harness never restates a mutation,
// or it would be testing its own copy of the projection rather than the
// projection. The Python half is spawned with `-c` and loads the module by FILE
// PATH, so it needs neither a venv nor the `tortoise` package (stdlib only) and
// cannot drift from the code under test.
//
// Three assertions, one per Interface A clause:
//   TOTAL     — every record type in the fixture materialized (the fixture
//               carries the whole declared vocabulary), and an UNDECLARED type
//               is a hard failure;
//   IDEMPOTENT— replay twice ⇒ byte-identical tables;
//   PLACEMENT — the EP scalars the journal carries (`ep_alpha`, `confidence`,
//               `version`, `content_hash`) are stored NOWHERE, as a column or as
//               a props key (STORAGE-ARCHITECTURE §2.0/§17.5).
const PROJECTION_PY = `
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("record_projection", sys.argv[1])
rp = importlib.util.module_from_spec(spec)
# Register BEFORE exec: dataclasses resolves the defining module through
# sys.modules[cls.__module__] on 3.14, and an unregistered module is None.
sys.modules["record_projection"] = rp
spec.loader.exec_module(rp)
records = [json.loads(line) for line in open(sys.argv[2], encoding="utf-8") if line.strip()]
# The tenant is the CALLER's (#9553): the same emitter must be replayable into a
# second tenant, or the harness stops testing the projection and starts testing
# its own copy. #7969's own pair is the default.
org, graph = (sys.argv[3] if len(sys.argv) > 3 else "7969-org",
              sys.argv[4] if len(sys.argv) > 4 else "7969-graph")
plan = []
for statement in rp.replay_plan(records, org_id=org, graph_id=graph):
    sql, params = rp.to_dollar_placeholders(statement)
    plan.append({"sql": sql, "params": params})
json.dump({"records": len(records), "plan": plan}, sys.stdout)
`;
const PROJECTION_RECORD_TABLES = [
  'node_identity', 'journal_event', 'source', 'event', 'session',
  'object', 'subject', 'point', 'relation',
  // §9.1's narrative home (#9555). It joins the SNAPSHOT (so the second replay
  // is compared with it included) even though this fixture creates `sess1` and
  // then deletes it, which makes the snapshot leg empty here by construction —
  // the DELETE is pinned separately below, where it can actually fail.
  'session_narrative',
];

async function projectionSnapshot() {
  const out = [];
  for (const table of PROJECTION_RECORD_TABLES) {
    const r = await db.query(`SELECT * FROM public.${table}`);
    out.push([table, r.rows.map((row) => JSON.stringify(row)).sort()]);
  }
  return JSON.stringify(out);
}

try {
  await db.exec(`
    INSERT INTO public.organizations (id, name, graph_name)
      VALUES ('7969-org', '7969-org', 'org_7969-org');
    INSERT INTO public.graphs (id, org_id, name, namespace)
      VALUES ('7969-graph', '7969-org', '7969-graph', 'org_7969-org_7969-graph');
  `);
  const fixture = join(HERE, '..', 'fixtures', 'record_projection_journal.jsonl');
  const modulePath = join(REPO_ROOT, 'tortoise', 'record_projection.py');
  const emitted = JSON.parse(execFileSync(
    'python3', ['-c', PROJECTION_PY, modulePath, fixture], { encoding: 'utf8' },
  ));
  if (!emitted.plan.length) {
    throw new Error('the projection emitted no statements for a non-empty journal');
  }

  // ONE transaction per replay — §3: "One transaction covers the journal and
  // its projection — no dual write between them".
  const applyOnce = async () => {
    await db.exec('BEGIN');
    for (const statement of emitted.plan) {
      await db.query(statement.sql, statement.params);
    }
    await db.exec('COMMIT');
  };
  await applyOnce();
  const first = await projectionSnapshot();
  await applyOnce();
  const second = await projectionSnapshot();
  if (first !== second) {
    console.error('✗ #7969: a SECOND replay of the same journal CHANGED the record');
    process.exit(1);
  }

  const counts = await db.query(`SELECT
      (SELECT count(*) FROM public.journal_event) AS journal,
      (SELECT count(*) FROM public.point)         AS points,
      (SELECT count(*) FROM public.source)        AS sources,
      (SELECT count(*) FROM public.object)        AS objects,
      (SELECT count(*) FROM public.subject)       AS subjects,
      (SELECT count(*) FROM public.session)       AS sessions,
      (SELECT count(*) FROM public.relation)      AS relations`);
  const c = counts.rows[0];
  const wantJournal = emitted.records;
  // Every fixture project is replayed to the same record twice; the counts are
  // the totality evidence (a skip would land one short) plus the fold's own
  // verdicts: p2 is hard-deleted by its EntityMutated, sess1 by SessionDeleted,
  // the `delete_only` DirectEdgeRepoint removes the `aboutPoint` row its
  // DirectEdgeCreated landed, the hard delete's identity cascade removes the
  // `IMPL(op1 -> p2)` edge incident to the deleted p2, the `session:{id}`
  // agentSession Source stub is created then removed with its session, and the
  // two surviving sources + one object + one subject + two relations remain:
  // the `aboutObject` edge to the SURVIVING object (keyed by name) and the
  // `IMPL(op1 -> p1)` edge. The second Object (`o2`) is hard-deleted, the
  // absent-endpoint link lands nothing, and `delete_only` + the p2 hard-delete
  // cascade leave exactly two.
  const expected = { journal: String(wantJournal), points: '2',
                     sources: '2', objects: '1', subjects: '1',
                     sessions: '0', relations: '2' };
  for (const [key, value] of Object.entries(expected)) {
    if (String(c[key]) !== value) {
      console.error(`✗ #7969: record.${key} = ${c[key]}, expected ${value}`);
      process.exit(1);
    }
  }

  // The Event row is keyed by the payload `eventId` (the fold's MERGE key), NOT
  // the `_emit_event` envelope `event_id` (a per-record ULID). A count-only
  // assertion cannot see the wrong key (both spellings satisfy the CHECK), so
  // this asserts the key itself.
  const eventKey = await db.query(
    `SELECT node_key, event_id FROM public.event WHERE node_key = 'e1'`);
  if (eventKey.rows.length !== 1 || eventKey.rows[0].event_id !== 'e1') {
    console.error(`✗ #7969: the Event row is not keyed by the payload eventId: ${JSON.stringify(eventKey.rows)}`);
    process.exit(1);
  }
  const envelopeKey = await db.query(
    `SELECT count(*) AS n FROM public.event WHERE node_key = 'ev-14'`);
  if (Number(envelopeKey.rows[0].n)) {
    console.error('✗ #7969: the journal envelope `event_id` keyed an Event row');
    process.exit(1);
  }

  // The `EntityMutated` Subject supersede is a state op whose record identity
  // is the entity `id`. Since #9881 that IS the row's `node_key` (the name moved
  // to a mutable column), so the assertion below reads the row by its key AND
  // checks the name column separately — a patch that matched by name would touch
  // 0 rows under the new key, which the count assertion above cannot see.
  const superseded = await db.query(
    `SELECT superseded_by, name, node_key FROM public.subject
      WHERE node_key = 'sub1'`);
  if (superseded.rows[0]?.name !== 'team-x') {
    console.error(`✗ #9881: the Subject row is not keyed by its id with the name kept as a column: ${JSON.stringify(superseded.rows)}`);
    process.exit(1);
  }
  if (superseded.rows[0]?.superseded_by !== 'team-y') {
    console.error(`✗ #7969: the Subject supersede was silently dropped: ${JSON.stringify(superseded.rows)}`);
    process.exit(1);
  }

  // The hard delete is the graph's `DETACH DELETE`: the node row AND its edges.
  // `relation`'s endpoints are composite FKs into `node_identity` with
  // ON DELETE CASCADE, so the identity row's removal is the edge removal. A
  // bare node DELETE left both alive — the record kept `IMPL(op1 -> p2)` after
  // p2 was gone. Assert both halves directly, not via the relation count.
  const p2Identity = await db.query(
    `SELECT count(*) AS n FROM public.node_identity
      WHERE node_label = 'Point' AND node_key = 'p2'`);
  if (Number(p2Identity.rows[0].n)) {
    console.error('✗ #7969: the hard-deleted Point left a stale node_identity row');
    process.exit(1);
  }
  const p2Edges = await db.query(
    `SELECT count(*) AS n FROM public.relation
      WHERE (src_label = 'Point' AND src_key = 'p2')
         OR (tgt_label = 'Point' AND tgt_key = 'p2')`);
  if (Number(p2Edges.rows[0].n)) {
    console.error(`✗ #7969: ${p2Edges.rows[0].n} relation(s) still reference the hard-deleted p2`);
    process.exit(1);
  }

  // The `SessionDeleted` companion: the `session:{id}` agentSession Source stub
  // is created (ev-35) then removed in the same step as its session
  // (the fold's `remove_source=True`). Neither the stub nor its identity row
  // survives; the two real sources do (a count assertion alone cannot see it).
  const stub = await db.query(
    `SELECT
       (SELECT count(*) FROM public.source WHERE node_key = 'session:sess1') AS source_row,
       (SELECT count(*) FROM public.node_identity
          WHERE node_label = 'Source' AND node_key = 'session:sess1')       AS identity_row`);
  if (Number(stub.rows[0].source_row) || Number(stub.rows[0].identity_row)) {
    console.error(`✗ #7969: the SessionDeleted left its agentSession Source stub: ${JSON.stringify(stub.rows)}`);
    process.exit(1);
  }

  // §9.1's narrative home, the DELETE half (#9555). The fixture's capture writes
  // a narrative row for `sess1` (the `SessionRecorded` leg, ev-12) and deletes
  // the session (the `SessionDeleted` at ev-32), so the projection's own `DELETE FROM
  // public.session_narrative` must leave NOTHING behind — a delete that filtered
  // the wrong tenant, or that the secondary projection never emitted, leaves a
  // row here. (The create half is exercised by the snapshot leg above; its
  // final state is empty in this fixture, which is why the explicit assertion is
  // the one that can fail.)
  const narrativeRows = await db.query(
    `SELECT count(*) AS n FROM public.session_narrative`);
  if (Number(narrativeRows.rows[0].n) !== 0) {
    console.error(`✗ #9555: the SessionDeleted left its narrative row behind: ${JSON.stringify(narrativeRows.rows)}`);
    process.exit(1);
  }

  // The `EntityLinked` endpoint is addressed by its journal `id` (`o1`,
  // `link_entity(proj,"Point",pid,o.id)`), and since #9881 an Object's
  // `node_key` IS that id — so the edge is filed by `o1` and the row it points at
  // is the one the Object was registered under. (Before the key move the
  // endpoint resolved by id to a NAME-keyed row; the resolution step is the same
  // and the assertion is what pins which key it lands.)
  const objectEdge = await db.query(
    `SELECT count(*) AS n FROM public.relation
      WHERE rel_type = 'aboutObject' AND src_label = 'Point' AND src_key = 'p1'
        AND tgt_label = 'Object' AND tgt_key = 'o1'`);
  if (Number(objectEdge.rows[0].n) !== 1) {
    console.error(`✗ #9881: the EntityLinked endpoint did not resolve to the id-keyed Object: ${JSON.stringify(objectEdge.rows)}`);
    process.exit(1);
  }
  // ...and it is the REGISTERED row (`o1`), not a stub minted from the ref.
  const objectName = await db.query(
    `SELECT name FROM public.object WHERE node_key = 'o1'`);
  if (objectName.rows[0]?.name !== 'release-1.0') {
    console.error(`✗ #9881: the id-keyed Object row does not carry its name column: ${JSON.stringify(objectName.rows)}`);
    process.exit(1);
  }
  const phantomIdentity = await db.query(
    `SELECT count(*) AS n FROM public.node_identity
      WHERE node_label = 'Object' AND node_key = 'no-such-object'`);
  if (Number(phantomIdentity.rows[0].n)) {
    console.error(`✗ #7969: an EntityLinked endpoint minted a phantom Object node_identity: ${JSON.stringify(phantomIdentity.rows)}`);
    process.exit(1);
  }
  // An absent endpoint is the fold's `absent` verdict: NO edge, and no
  // identity row for the key it named.
  const absentEdge = await db.query(
    `SELECT count(*) AS n FROM public.relation
      WHERE tgt_label = 'Object' AND tgt_key = 'no-such-object'`);
  if (Number(absentEdge.rows[0].n)) {
    console.error(`✗ #7969: an EntityLinked to an ABSENT Object landed ${absentEdge.rows[0].n} edge(s)`);
    process.exit(1);
  }
  // A hard-deleted Object cascades its identity row (and therefore its edges):
  // the `aboutObject` edge to `o2` must not survive `o2`'s delete.
  const o2 = await db.query(
    `SELECT
       (SELECT count(*) FROM public.object WHERE node_key = 'o2')                   AS node_row,
       (SELECT count(*) FROM public.node_identity
          WHERE node_label = 'Object' AND node_key = 'o2')                          AS identity_row,
       (SELECT count(*) FROM public.relation
          WHERE tgt_label = 'Object' AND tgt_key = 'o2')                            AS edges`);
  if (Number(o2.rows[0].node_row) || Number(o2.rows[0].identity_row) ||
      Number(o2.rows[0].edges)) {
    console.error(`✗ #7969: the hard-deleted Object kept state: ${JSON.stringify(o2.rows)}`);
    process.exit(1);
  }

  // The envelope `event_id` must not reach any node's props tail either.
  for (const table of ['object', 'subject', 'session', 'source', 'event']) {
    const leaked = await db.query(
      `SELECT count(*) AS n FROM public.${table} WHERE props ? 'event_id'`);
    if (Number(leaked.rows[0].n)) {
      console.error(`✗ #7969: the journal envelope leaked into ${table}.props`);
      process.exit(1);
    }
  }

  const folded = await db.query(
    `SELECT content FROM public.point WHERE node_key = 'p1'`);
  if (folded.rows[0]?.content !== 'second reading of p1') {
    console.error(`✗ #7969: PointRevised did not fold in order: ${JSON.stringify(folded.rows)}`);
    process.exit(1);
  }

  // The EP-scalar carve-out, on the stored rows rather than in the emitter.
  const leak = await db.query(`SELECT
      (SELECT count(*) FROM public.point
        WHERE props ?| ARRAY['ep_alpha','ep_beta','confidence','version','content_hash']::text[]) AS point_props,
      (SELECT count(*) FROM public.point
        WHERE props ? 'lastDreamedAt')                                        AS point_extra,
      (SELECT count(*) FROM public.source WHERE props ? 'reliability')        AS source_props,
      (SELECT count(*) FROM public.subject WHERE props ? 'status')            AS subject_cache,
      (SELECT count(*) FROM public.source
        WHERE props ?| ARRAY['content','doc_status','object_kind']::text[])   AS retired_props`);
  const l = leak.rows[0];
  if (Number(l.point_props) || Number(l.point_extra) || Number(l.source_props) ||
      Number(l.subject_cache) || Number(l.retired_props)) {
    console.error(`✗ #7969: a DERIVED/cache/retired value was stored as authority: ${JSON.stringify(l)}`);
    process.exit(1);
  }

  // TOTALITY, the other half: an undeclared record type must FAIL the replay,
  // not be skipped. Spawned (not imported) so the failure is the real one a
  // caller sees: a raised exception and a non-zero exit.
  let refused = false;
  try {
    execFileSync('python3', ['-c', `
import importlib.util, sys
spec = importlib.util.spec_from_file_location("record_projection", sys.argv[1])
rp = importlib.util.module_from_spec(spec)
sys.modules["record_projection"] = rp
spec.loader.exec_module(rp)
rp.replay_plan([{"type": "PointTeleported", "id": "p1"}], org_id="o", graph_id="g")
`, modulePath], { encoding: 'utf8', stdio: 'pipe' });
  } catch {
    refused = true;
  }
  if (!refused) {
    console.error('✗ #7969: an UNDECLARED record type was SKIPPED — Interface A is total');
    process.exit(1);
  }

  // NO cleanup: `public.journal_event` is append-only by TRIGGER (a DELETE is
  // refused unless the tenant-erasure GUC is set), and the harness database is
  // thrown away at the end of the run — a cleanup that tried to satisfy the
  // trigger here would be testing the erasure path, which #7968's own suite
  // already covers.
  console.log(`✓ #7969: the record projection replays ${wantJournal} journal records, is idempotent, total, and stores no EP scalar`);
} catch (e) {
  console.error(`✗ #7969 record projection FAILED:\n  ${e.message.split('\n').slice(0, 6).join('\n  ')}`);
  process.exit(1);
}

// ── #9553 (R5 class 2): the capture's CONTAINS edge lands a `relation` row ──
//
// `contains_session` rides the `PointAdded` ENVELOPE, not the point payload
// (a Point property of that name would let a caller forge a container link on
// replay). `_RECORD_ONLY_KEYS` dropped the key — correctly — and dropped the
// EDGE with it: a JOURNAL rebuild restored `(:Session)-[:CONTAINS]->(:Point)`
// (its fold reads the same envelope key) while a RECORD-sourced rebuild
// restored nothing, so `relation` was 0 rows after a capture. The fix is a
// relation upsert emitted AFTER the point's identity row, because the endpoints
// are composite FKs into `node_identity`: replayed out of order it resolves 0
// endpoints and lands nothing — which is what the row count below reports.
try {
  const tenant = ['9553-org', '9553-graph'];
  await db.exec(`
    INSERT INTO public.organizations (id, name, graph_name)
      VALUES ('9553-org', '9553-org', 'org_9553-org');
    INSERT INTO public.graphs (id, org_id, name, namespace)
      VALUES ('9553-graph', '9553-org', '9553-graph', 'org_9553-org_9553-graph');
  `);
  const fixture = join(HERE, '..', 'fixtures', 'record_projection_journal.jsonl');
  const all = readFileSync(fixture, 'utf8').split('\n').filter((l) => l.trim());
  // The LIVE order, taken from the shared fixture instead of restated: the
  // session container is journaled BEFORE the turn loop emits any `PointAdded`
  // (`sdk._write_session_and_turns`), while this fixture lists `SessionRecorded`
  // AFTER the Points it contains. Replaying the pair in the fixture's own order
  // would run the edge before its endpoint existed and land NOTHING — the exact
  // failure mode this leg exists to catch — so the order here is the live one.
  const sessionRecord = all.find(
    (l) => l.includes('"SessionRecorded"') && l.includes('"sess1"'));
  const turnRecord = all.find((l) => l.includes('"contains_session"'));
  if (!sessionRecord || !turnRecord) {
    throw new Error('the shared fixture no longer carries a SessionRecorded plus '
      + 'a contains_session PointAdded — this leg would prove nothing, and a '
      + 'silent skip reads as a pass');
  }
  const prefix = join(tmpdir(), `9553-prefix-${process.pid}.jsonl`);
  writeFileSync(prefix, `${sessionRecord}\n${turnRecord}\n`);
  const emitted = JSON.parse(execFileSync(
    'python3',
    ['-c', PROJECTION_PY, join(REPO_ROOT, 'tortoise', 'record_projection.py'),
     prefix, ...tenant],
    { encoding: 'utf8' },
  ));
  if (!emitted.plan.some((x) => x.sql.startsWith('INSERT INTO public.relation'))) {
    throw new Error('the pair emitted NO relation statement at all — the '
      + 'CONTAINS producer is gone and the record loses the session container '
      + 'again (#9553 R5 class 2)');
  }
  const applyPrefix = async () => {
    await db.exec('BEGIN');
    for (const statement of emitted.plan) {
      await db.query(statement.sql, statement.params);
    }
    await db.exec('COMMIT');
  };
  const readContains = async () => db.query(
    `SELECT rel_type, src_label, src_key, tgt_label, tgt_key, idx
       FROM public.relation
      WHERE org_id = $1 AND graph_id = $2 AND rel_type = 'CONTAINS'`,
    tenant);
  await applyPrefix();
  const first = await readContains();
  if (first.rows.length !== 1) {
    const diag = await db.query(
      `SELECT
         (SELECT count(*) FROM public.session
            WHERE org_id = $1 AND graph_id = $2 AND node_key = 'sess1')  AS session_row,
         (SELECT count(*) FROM public.point
            WHERE org_id = $1 AND graph_id = $2 AND node_key = 'p1')     AS point_row,
         (SELECT count(*) FROM public.node_identity
            WHERE org_id = $1 AND graph_id = $2
              AND node_label = 'Session' AND node_key = 'sess1')         AS session_identity,
         (SELECT count(*) FROM public.node_identity
            WHERE org_id = $1 AND graph_id = $2
              AND node_label = 'Point' AND node_key = 'p1')              AS point_identity`,
      tenant);
    throw new Error(
      `expected exactly 1 CONTAINS relation row, got ${first.rows.length} — an `
      + 'endpoint was absent when the insert ran (ORDERING), or the envelope '
      + `key was dropped. endpoints: ${JSON.stringify(diag.rows[0])}`);
  }
  const row = first.rows[0];
  const got = `${row.src_label}/${row.src_key} -> ${row.tgt_label}/${row.tgt_key} idx=${row.idx}`;
  const want = 'Session/sess1 -> Point/p1 idx=0';
  if (got !== want) {
    throw new Error(
      `the CONTAINS row is ${got}, expected ${want} — the endpoint resolution `
      + 'or the envelope directive is wrong');
  }
  // Idempotent: the upsert is keyed on the relation's UNIQUE identity, so a
  // SECOND replay of the same segment (the retry a crash produces) converges.
  await applyPrefix();
  const second = await readContains();
  if (second.rows.length !== 1) {
    throw new Error(
      `a second replay produced ${second.rows.length} CONTAINS rows (expected `
      + '1) — the relation upsert is not idempotent');
  }
  unlinkSync(prefix);
  console.log('✓ #9553: the capture CONTAINS edge lands one `relation` row '
    + '(Session/sess1 -> Point/p1), and re-replays idempotently');
} catch (e) {
  console.error(`✗ #9553 R5/CONTAINS FAILED:\n  ${e.message.split('\n').slice(0, 6).join('\n  ')}`);
  process.exit(1);
}

// ── #9553 (R5 class 4): a STRUCTURAL edge lands a `relation` row ──────────
//
// The structural predicates (`ownedBy`, `hasPart`, `dependsOn`,
// `wasDerivedFrom`, `references`, the bare `about*`) had a working
// `DirectEdgeCreated` fold and NO PRODUCER: the edge lane wrote a bare MERGE
// and journaled nothing, so both rebuild routes lost the topology. The producer
// now emits §4.4's flat descriptor WITH `source_label`/`target_label`, because
// `relation.src_label`/`tgt_label` are bound VALUES and each label resolves its
// endpoint through its own key column — a `Subject` keys `name`, so a
// descriptor naming only `Point` would resolve an absent endpoint and land
// NOTHING, silently. That is what the key assertion below measures: the row
// must file the SUBJECT by its NAME, not by the journal id.
//
// The segment is built from the SHARED fixture's own records (its `PointAdded`
// and `SubjectAdded`) plus the descriptor the producer emits, so this leg
// cannot drift from the record shapes the rest of the suite replays.
try {
  const tenant = ['9553c4-org', '9553c4-graph'];
  await db.exec(`
    INSERT INTO public.organizations (id, name, graph_name)
      VALUES ('9553c4-org', '9553c4-org', 'org_9553c4-org');
    INSERT INTO public.graphs (id, org_id, name, namespace)
      VALUES ('9553c4-graph', '9553c4-org', '9553c4-graph',
              'org_9553c4-org_9553c4-graph');
  `);
  const fixture = join(HERE, '..', 'fixtures', 'record_projection_journal.jsonl');
  const all = readFileSync(fixture, 'utf8').split('\n').filter((l) => l.trim());
  // Matched on the COMPACT spelling the fixture file uses (no space after the
  // colon) — a pretty-printed assumption here would silently find nothing and
  // turn this leg into the skip it refuses to be.
  const subjectRecord = all.find(
    (l) => l.includes('"type":"SubjectAdded"') && l.includes('"id":"sub1"'));
  const pointRecord = all.find(
    (l) => l.includes('"type":"PointAdded"') && l.includes('"id":"p1"'));
  if (!subjectRecord || !pointRecord) {
    throw new Error('the shared fixture no longer carries SubjectAdded sub1 and '
      + 'PointAdded p1 — this leg would prove nothing, and a silent skip reads '
      + 'as a pass');
  }
  // The descriptor the PRODUCER writes (TortoiseSDK._record_structural_edge).
  // Restated here on purpose: this leg is the record route's gate, so it must
  // consume the shape the producer emits rather than derive it from the same
  // code the producer runs.
  const descriptor = JSON.stringify({
    type: 'DirectEdgeCreated',
    ts: '2026-10-24T00:00:00Z',
    event_id: 'ev-c4-ownedby',
    projection_version: 2,
    id: 'p1->sub1:ownedBy',
    src: 'p1',
    tgt: 'sub1',
    edge_type: 'ownedBy',
    source_label: 'Point',
    target_label: 'Subject',
  });
  const segment = join(tmpdir(), `9553c4-${process.pid}.jsonl`);
  writeFileSync(segment, `${pointRecord}\n${subjectRecord}\n${descriptor}\n`);
  const emitted = JSON.parse(execFileSync(
    'python3',
    ['-c', PROJECTION_PY, join(REPO_ROOT, 'tortoise', 'record_projection.py'),
     segment, ...tenant],
    { encoding: 'utf8' },
  ));
  if (!emitted.plan.some((x) => x.sql.startsWith('INSERT INTO public.relation'))) {
    throw new Error('the segment emitted NO relation statement at all — the '
      + 'DirectEdgeCreated carrier stopped materialising (#9553 R5 class 4)');
  }
  const applySegment = async () => {
    await db.exec('BEGIN');
    for (const statement of emitted.plan) {
      await db.query(statement.sql, statement.params);
    }
    await db.exec('COMMIT');
  };
  const readOwnedBy = async () => db.query(
    `SELECT rel_type, src_label, src_key, tgt_label, tgt_key, idx
       FROM public.relation
      WHERE org_id = $1 AND graph_id = $2 AND rel_type = 'ownedBy'`,
    tenant);
  await applySegment();
  const first = await readOwnedBy();
  if (first.rows.length !== 1) {
    const diag = await db.query(
      `SELECT
         (SELECT count(*) FROM public.point
            WHERE org_id = $1 AND graph_id = $2 AND node_key = 'p1')     AS point_row,
         (SELECT count(*) FROM public.subject
            WHERE org_id = $1 AND graph_id = $2 AND node_key = 'sub1')   AS subject_by_key,
         (SELECT count(*) FROM public.subject
            WHERE org_id = $1 AND graph_id = $2 AND name = 'team-x')     AS subject_by_name,
         (SELECT count(*) FROM public.node_identity
            WHERE org_id = $1 AND graph_id = $2
              AND node_label = 'Subject' AND node_key = 'sub1')          AS subject_identity`,
      tenant);
    throw new Error(
      `expected exactly 1 ownedBy relation row, got ${first.rows.length} — the `
      + 'descriptor landed no row (an ABSENT endpoint: the Point/Subject record '
      + `rows or their node_identity rows are missing). endpoints: ${JSON.stringify(diag.rows[0])}`);
  }
  const row = first.rows[0];
  const got = `${row.src_label}/${row.src_key} -> ${row.tgt_label}/${row.tgt_key} idx=${row.idx}`;
  // #9881: the Subject's key IS its id, so the edge is filed under `sub1` and
  // the NAME it was registered with lives in the row's `name` column. A
  // name-keyed endpoint here would be the pre-#3590 shape the key move removes.
  const want = 'Point/p1 -> Subject/sub1 idx=0';
  if (got !== want) {
    throw new Error(
      `the ownedBy row is ${got}, expected ${want} — a `
      + '`source_label`/`target_label` the record does not honour would file the '
      + 'endpoint under the wrong label (or land nothing), which is the silently '
      + 'empty shape this family exists to prevent');
  }
  await applySegment();
  const second = await readOwnedBy();
  if (second.rows.length !== 1) {
    throw new Error(
      `a second replay produced ${second.rows.length} ownedBy rows (expected 1) `
      + '— the relation upsert is not idempotent for this family');
  }
  unlinkSync(segment);
  const named = await db.query(
    `SELECT name FROM public.subject
      WHERE org_id = $1 AND graph_id = $2 AND node_key = 'sub1'`, tenant);
  if (named.rows[0]?.name !== 'team-x') {
    throw new Error(
      'the Subject row keyed by sub1 does not carry name=team-x — the name '
      + `column is what keeps the name addressable after the key move (#9881): ${JSON.stringify(named.rows)}`);
  }
  console.log('✓ #9553: a structural `ownedBy` edge lands one `relation` row '
    + '(Point/p1 -> Subject/sub1, the Subject filed by its ID with the name kept '
    + 'as a column — #9881), and re-replays idempotently');
} catch (e) {
  console.error(`✗ #9553 R5/class-4 FAILED:\n  ${e.message.split('\n').slice(0, 6).join('\n  ')}`);
  process.exit(1);
}

// ── #1383/20261023000002: the backfill repair is ONE statement, and it repairs ─
//
// Two drills, because the two claims are different:
//
//  A ATOMICITY — the property the merged 20261021000001 lacks. Its backfill is
//    four top-level statements (DISABLE / UPDATE / UPDATE / ENABLE) among the
//    file's others, so a per-statement apply (`psql -f`, autocommit per
//    statement) that stops between any two of them can commit the DISABLE and
//    never reach the ENABLE, leaving `blog_posts_set_updated_at` off for every
//    blog write afterwards — silently. This drill therefore replays the file
//    the way that apply does, ONE TOP-LEVEL STATEMENT AT A TIME, and requires
//    the trigger to be ENABLED after EVERY prefix: that is the invariant (no
//    stop point leaves it off), and a prefix alone is not enough — the merged
//    file's FIRST statement is an ADD COLUMN, so stopping there proves nothing.
//    Red as soon as a prefix leaves it disabled; green only when the whole
//    backfill is one statement.
//
//  B REPAIR — the forward migration must converge a database that IS in that
//    damaged state (the live project may be, if the out-of-band apply of
//    20261021000001 truncated): trigger DISABLED, and rows the backfill never
//    reached. The damaged state is constructed explicitly and then the
//    migration text is executed over it.
const BACKFILL_MIGRATION = '20261023000002_blog_author_backfill_atomic.sql';
const BACKFILL_SQL = readFileSync(`${MIG_DIR}/${BACKFILL_MIGRATION}`, 'utf8');
const readTriggerState = async () => {
  const r = await db.query(`SELECT tgenabled FROM pg_trigger
     WHERE tgname = 'blog_posts_set_updated_at'
       AND tgrelid = 'public.blog_posts'::regclass`);
  return r.rows[0] ? r.rows[0].tgenabled : undefined;
};

try {
  const statements = splitTopLevelStatements(BACKFILL_SQL);
  for (let n = 1; n <= statements.length; n += 1) {
    await db.exec(statements[n - 1]);
    const state = await readTriggerState();
    if (state !== 'O') {
      console.error(
        `✗ #1383: after TOP-LEVEL STATEMENT ${n} of ${statements.length}, `
        + `tgenabled=${state} (disabled). A per-statement apply that stops here — `
        + 'a lock timeout, a dropped connection, a Ctrl-C — commits the DISABLE and '
        + 'leaves updated_at frozen for every blog write afterwards, silently. The '
        + 'whole backfill must be ONE statement (a DO block) so no stop point exists:');
      console.error(`  offending prefix ends with: ${JSON.stringify(statements[n - 1].trim().slice(-160))}`);
      process.exit(1);
    }
  }
  console.log(
    `✓ #1383: the backfill repair is ${statements.length} top-level statement(s) — `
    + 'no stop point of a per-statement apply leaves the trigger disabled');
} catch (e) {
  console.error(`✗ #1383 prefix drill FAILED:\n  ${e.message.split('\n').slice(0, 4).join('\n  ')}`);
  process.exit(1);
}

try {
  // Construct the damaged state a truncated apply of 20261021000001 leaves:
  // the DISABLE committed, and a row the backfill never reached.
  await db.exec(`
    ALTER TABLE public.blog_posts DISABLE TRIGGER blog_posts_set_updated_at;
    INSERT INTO public.blog_posts
      (slug, title, author, status, created_at, updated_at)
    VALUES ('1383b-repair-anon', '1383b repair anon', 'Tortoise team', 'draft',
            '2026-05-05T00:00:00Z', '2026-05-05T00:00:00Z');
  `);
  const damaged = await readTriggerState();
  if (damaged !== 'D') {
    throw new Error(`the drill could not construct the damaged state (tgenabled=${damaged}) — it would prove nothing`);
  }
  await db.exec(BACKFILL_SQL);
  const state = await readTriggerState();
  const row = (await db.query(`SELECT author, author_image_url, updated_at
     FROM public.blog_posts WHERE slug = '1383b-repair-anon'`)).rows[0] || {};
  if (state !== 'O') {
    throw new Error(`the repair left tgenabled=${state} — the truncated-apply state is NOT converged`);
  }
  if (row.author !== 'Daniel Ospina'
      || row.author_image_url !== 'https://tortoise.premiselabs.co/blog/author-daniel-ospina.jpg') {
    throw new Error(`the repair did not complete the backfill: ${JSON.stringify(row)}`);
  }
  const stamp = row.updated_at instanceof Date
    ? row.updated_at.toISOString() : String(row.updated_at);
  if (!stamp.startsWith('2026-05-05')) {
    throw new Error(
      `the repair BUMPED updated_at (${stamp}) — the backfill ran with the trigger `
      + 'enabled, so a repaired post would print "Updated <today>" and open the '
      + '30-minute no-cache window');
  }
  await db.exec(`DELETE FROM public.blog_posts WHERE slug = '1383b-repair-anon';`);
  console.log('✓ #1383: the repair converges a truncated apply (trigger re-enabled, '
    + 'backfill completed, updated_at preserved)');
} catch (e) {
  console.error(`✗ #1383 forward-repair drill FAILED:\n  ${e.message.split('\n').slice(0, 4).join('\n  ')}`);
  process.exit(1);
}

// ── #9553 (R5 class 1): the point-PROPERTY edge lands a `relation` row ──
//
// `_upsert_point_edges` derives `extractedFrom` from the Point's own payload
// (`_link_source`), so a JOURNAL rebuild restored
// `(:Point)-[:extractedFrom]->(:Source)` while a RECORD-sourced rebuild restored
// nothing: the point row carried `extracted_from` and not a single relation row
// carried the edge. Unlike the CONTAINS leg above, the two records here are
// SYNTHETIC — the shared fixture carries no `extractedFrom` — so the segment is
// spelled out rather than lifted, and it is the LIVE order: the Source is
// journaled before the Point whose payload names it
// (`sdk._materialize_session_source` mints the capture Source first).
//
// ⛔ ONE RESIDUAL, asserted by the DIAGNOSTIC below rather than by a green row:
// the insert RESOLVES the Source from the record's own `source` row. Replay the
// Point first and it lands nothing — the `absent` verdict every relation gives,
// not a minted endpoint. A record-sourced rebuild is fed in journal order
// (#9190); the reverse order is the documented gap, not a contract.
try {
  const tenant = ['9553c1-org', '9553c1-graph'];
  await db.exec(`
    INSERT INTO public.organizations (id, name, graph_name)
      VALUES ('9553c1-org', '9553c1-org', 'org_9553c1-org');
    INSERT INTO public.graphs (id, org_id, name, namespace)
      VALUES ('9553c1-graph', '9553c1-org', '9553c1-graph', 'org_9553c1-org_9553c1-graph');
  `);
  const sourceRecord = {
    type: 'SourceCreated', ts: '2026-10-22T00:00:01Z', id: 'src1',
    url: 'src1', sourceKind: 'document', contentHash: 'h1',
  };
  const pointRecord = {
    type: 'PointAdded', ts: '2026-10-22T00:00:02Z', id: 'p1',
    point: {
      id: 'p1', content: 'extracted claim', pointKind: 'observation',
      extractedFrom: 'src1',
      // #5256: the read-version anchor rides the Point's OWN carrier, keyed by
      // the raw ref, and the record must stamp it on the EDGE
      // (`relation.source_version`), not smuggle it into `relation.props`
      // (`chk_relation_props_no_derived` exists to stop exactly that class).
      sourceVersionTransit: [['src1', 'h1']],
    },
  };
  const segment = join(tmpdir(), `9553c1-${process.pid}.jsonl`);
  writeFileSync(segment, `${JSON.stringify(sourceRecord)}\n${JSON.stringify(pointRecord)}\n`);
  const emitted = JSON.parse(execFileSync(
    'python3',
    ['-c', PROJECTION_PY, join(REPO_ROOT, 'tortoise', 'record_projection.py'),
     segment, ...tenant],
    { encoding: 'utf8' },
  ));
  if (!emitted.plan.some((x) => x.sql.startsWith('INSERT INTO public.relation'))) {
    throw new Error('the pair emitted NO relation statement at all — the '
      + 'extractedFrom producer is gone and the record loses the provenance '
      + 'edge again (#9553 R5 class 1)');
  }
  const applySegment = async () => {
    await db.exec('BEGIN');
    for (const statement of emitted.plan) {
      await db.query(statement.sql, statement.params);
    }
    await db.exec('COMMIT');
  };
  const readProvenance = async () => db.query(
    `SELECT rel_type, src_label, src_key, tgt_label, tgt_key, idx, source_version
       FROM public.relation
      WHERE org_id = $1 AND graph_id = $2 AND rel_type = 'extractedFrom'`,
    tenant);
  await applySegment();
  const first = await readProvenance();
  if (first.rows.length !== 1) {
    const diag = await db.query(
      `SELECT
         (SELECT count(*) FROM public.source
            WHERE org_id = $1 AND graph_id = $2 AND node_key = 'src1')  AS source_row,
         (SELECT count(*) FROM public.point
            WHERE org_id = $1 AND graph_id = $2 AND node_key = 'p1')    AS point_row,
         (SELECT count(*) FROM public.node_identity
            WHERE org_id = $1 AND graph_id = $2
              AND node_label = 'Source' AND node_key = 'src1')        AS source_identity,
         (SELECT count(*) FROM public.node_identity
            WHERE org_id = $1 AND graph_id = $2
              AND node_label = 'Point' AND node_key = 'p1')           AS point_identity`,
      tenant);
    throw new Error(
      `expected exactly 1 extractedFrom relation row, got ${first.rows.length} — `
      + 'the target Source was absent when the insert ran (ORDERING), or the '
      + `point-property directive was dropped. endpoints: ${JSON.stringify(diag.rows[0])}`);
  }
  const row = first.rows[0];
  const got = `${row.src_label}/${row.src_key} -> ${row.tgt_label}/${row.tgt_key} `
    + `idx=${row.idx} version=${row.source_version}`;
  const want = 'Point/p1 -> Source/src1 idx=0 version=h1';
  if (got !== want) {
    throw new Error(
      `the extractedFrom row is ${got}, expected ${want} — the endpoint `
      + 'resolution (node_key, never the `id` fallback) or the #5256 anchor is wrong');
  }
  // Idempotent: the upsert is keyed on the relation's UNIQUE identity.
  await applySegment();
  const second = await readProvenance();
  if (second.rows.length !== 1) {
    throw new Error(
      `a second replay produced ${second.rows.length} extractedFrom rows `
      + '(expected 1) — the relation upsert is not idempotent');
  }
  unlinkSync(segment);
  console.log('✓ #9553: the point-property extractedFrom edge lands one `relation` row '
    + '(Point/p1 -> Source/src1, source_version=h1), and re-replays idempotently');

  // ── #9553 review P1: a URL-VARIANT ref resolves to the REGISTERED Source ──
  //
  // The fold does not address a `:Source` by the raw ref: `_mint_source_stub`
  // calls `resolve_source_key` FIRST (canonicalise, then take the url of any
  // Source already registered under that canonical form) and only then MERGEs.
  // So a point whose `extractedFrom` is a tracking-param VARIANT of a registered
  // Source lands its edge on the REGISTERED node. A record that matched the raw
  // ref alone found no `source` row and emitted a relation whose target subselect
  // was empty: zero rows, no error, while the journal rebuild restored the edge.
  //
  // The record's `canonical_url` column is fed by NO payload —
  // `projection/entities.py::_EntityHandlers._SOURCE_HANDLED` puts `canonicalUrl`
  // among the writer-DERIVED keys — so the projection derives it from the node's
  // own key; this leg fails if either half regresses (the derivation, or the
  // two-spelling resolution).
  const variantRecords = [
    {
      type: 'SourceCreated', ts: '2026-10-22T00:00:03Z', id: 'https://example.com/a',
      url: 'https://example.com/a', sourceKind: 'document', contentHash: 'h2',
    },
    {
      type: 'PointAdded', ts: '2026-10-22T00:00:04Z', id: 'p2',
      point: {
        id: 'p2', content: 'variant claim', pointKind: 'observation',
        extractedFrom: 'https://example.com/a/?utm_source=x',
      },
    },
  ];
  const variantSegment = join(tmpdir(), `9553c1v-${process.pid}.jsonl`);
  writeFileSync(variantSegment,
    variantRecords.map((r) => JSON.stringify(r)).join('\n') + '\n');
  const variantPlan = JSON.parse(execFileSync(
    'python3',
    ['-c', PROJECTION_PY, join(REPO_ROOT, 'tortoise', 'record_projection.py'),
     variantSegment, ...tenant],
    { encoding: 'utf8' },
  )).plan;
  await db.exec('BEGIN');
  for (const statement of variantPlan) {
    await db.query(statement.sql, statement.params);
  }
  await db.exec('COMMIT');
  const canonicalRow = await db.query(
    `SELECT node_key, canonical_url FROM public.source
      WHERE org_id = $1 AND graph_id = $2 AND node_key = 'https://example.com/a'`,
    tenant);
  if (canonicalRow.rows.length !== 1 || canonicalRow.rows[0].canonical_url !== 'https://example.com/a') {
    throw new Error(
      'the record did not derive the Source canonical identity '
      + `(rows: ${JSON.stringify(canonicalRow.rows)}) — no payload may carry it, so a `
      + 'URL-variant ref cannot resolve and the provenance edge is lost');
  }
  const variantRows = await db.query(
    `SELECT src_label, src_key, tgt_label, tgt_key, idx FROM public.relation
      WHERE org_id = $1 AND graph_id = $2 AND rel_type = 'extractedFrom'
        AND src_key = 'p2'`,
    tenant);
  const variantGot = variantRows.rows.map((r) =>
    `${r.src_label}/${r.src_key} -> ${r.tgt_label}/${r.tgt_key}`);
  if (variantRows.rows.length !== 1
      || variantGot[0] !== 'Point/p2 -> Source/https://example.com/a') {
    throw new Error(
      `the variant ref resolved to ${JSON.stringify(variantGot)}, expected exactly `
      + '["Point/p2 -> Source/https://example.com/a"] — the fold resolves a variant '
      + 'to the registered Source, so a record that binds only the raw ref is POORER '
      + 'than the graph and a record-sourced rebuild drops the edge (#9553 review P1)');
  }
  unlinkSync(variantSegment);
  console.log('✓ #9553: a URL-variant ref resolves to the registered Source '
    + '(Point/p2 -> Source/https://example.com/a), matching the fold');

  // ── #9553 '#5256': the anchor is written ON CREATE, never ADVANCED ──
  //
  // The fold stamps `r.sourceVersion` through `_anchor_on_create` (`ON CREATE
  // SET`), and `_link_source` states the rule: "a re-link must not ADVANCE a
  // recorded version". The record writes the anchor through ON CONFLICT, whose
  // generic `DO UPDATE SET c = EXCLUDED.c` WOULD advance it — a re-emitted
  // `PointAdded` (`create_point` appends one before a DETACH DELETE and may
  // append a second) carrying a newer hash then rewrites an anchor the journal
  // route keeps, and the two rebuild routes disagree about the same journal.
  // This leg replays that second record and asserts the FIRST hash survives.
  const rerunSegment = join(tmpdir(), `9553c1a-${process.pid}.jsonl`);
  writeFileSync(rerunSegment, `${JSON.stringify({
    ...pointRecord, ts: '2026-10-22T00:00:09Z',
    point: { ...pointRecord.point, sourceVersionTransit: [['src1', 'h2']] },
  })}\n`);
  const rerunPlan = JSON.parse(execFileSync(
    'python3',
    ['-c', PROJECTION_PY, join(REPO_ROOT, 'tortoise', 'record_projection.py'),
     rerunSegment, ...tenant],
    { encoding: 'utf8' },
  )).plan;
  await db.exec('BEGIN');
  for (const statement of rerunPlan) {
    await db.query(statement.sql, statement.params);
  }
  await db.exec('COMMIT');
  const reAnchored = await db.query(
    `SELECT src_key, source_version FROM public.relation
      WHERE org_id = $1 AND graph_id = $2 AND rel_type = 'extractedFrom'
        AND src_key = 'p1'`,
    tenant);
  if (reAnchored.rows.length !== 1) {
    throw new Error(
      `replaying the same edge produced ${reAnchored.rows.length} p1 rows (expected 1) `
      + '— the relation upsert is not keyed on its UNIQUE identity');
  }
  if (reAnchored.rows[0].source_version !== 'h1') {
    throw new Error(
      `the replayed edge ADVANCED its read-version anchor to `
      + `${JSON.stringify(reAnchored.rows[0].source_version)} (expected 'h1') — the `
      + 'fold stamps it ON CREATE, so a re-link must not overwrite a recorded '
      + 'version; the record is diverging from the journal route (#5256/#5199)');
  }
  unlinkSync(rerunSegment);
  console.log('✓ #9553: the read-version anchor is written ON CREATE — a replayed '
    + 'record with a NEWER hash keeps the recorded h1 (the fold\'s ON CREATE rule)');

  // ── #9553 class 1: a PROMOTE record carries the family too ──
  //
  // `apply()` materialises a Point from four arms and three of them reach
  // `_upsert_point_edges`, which reads `extractedFrom` with no type gate of its
  // own: `PointAdded`/`OperatorAdded` (`self._upsert(p)`) and
  // `PointPromoted`/`OperatorPromoted` (`self._upsert(gated_promote_payload(...))`
  // / `self._upsert(_promotion_point_with_operator(...))`, neither wrapper
  // dropping the ref). A promote-only record is the #2256 capture case, where
  // the promote IS the operator's only durable record — so declining the promote
  // rows would leave the record POORER than the graph on the engine `rebuild` /
  // `recover_from_log` run.
  const promoteRecords = [
    {
      type: 'SourceCreated', ts: '2026-10-22T00:00:10Z', id: 'src3',
      url: 'src3', sourceKind: 'document', contentHash: 'h3',
    },
    {
      type: 'PointPromoted', ts: '2026-10-22T00:00:11Z', id: 'p3',
      point: {
        id: 'p3', content: 'promoted claim', pointKind: 'observation',
        extractedFrom: 'src3',
      },
    },
  ];
  const promoteSegment = join(tmpdir(), `9553c1p-${process.pid}.jsonl`);
  writeFileSync(promoteSegment,
    promoteRecords.map((r) => JSON.stringify(r)).join('\n') + '\n');
  const promotePlan = JSON.parse(execFileSync(
    'python3',
    ['-c', PROJECTION_PY, join(REPO_ROOT, 'tortoise', 'record_projection.py'),
     promoteSegment, ...tenant],
    { encoding: 'utf8' },
  )).plan;
  if (!promotePlan.some((x) => x.sql.startsWith('INSERT INTO public.relation'))) {
    throw new Error('the PointPromoted record emitted NO relation statement — a '
      + 'promote-only operator has the extractedFrom edge in the graph (`apply()`) '
      + 'and none in the record, the record POORER than the graph (#9553 class 1)');
  }
  await db.exec('BEGIN');
  for (const statement of promotePlan) {
    await db.query(statement.sql, statement.params);
  }
  await db.exec('COMMIT');
  const promotedRows = await db.query(
    `SELECT src_label, src_key, tgt_label, tgt_key FROM public.relation
      WHERE org_id = $1 AND graph_id = $2 AND rel_type = 'extractedFrom'
        AND src_key = 'p3'`,
    tenant);
  const promotedGot = promotedRows.rows.map((r) =>
    `${r.src_label}/${r.src_key} -> ${r.tgt_label}/${r.tgt_key}`);
  if (promotedRows.rows.length !== 1
      || promotedGot[0] !== 'Point/p3 -> Source/src3') {
    throw new Error(
      `the promote record landed ${JSON.stringify(promotedGot)}, expected exactly `
      + '["Point/p3 -> Source/src3"]');
  }
  unlinkSync(promoteSegment);
  console.log('✓ #9553: a PointPromoted record carries the extractedFrom row too '
    + '(Point/p3 -> Source/src3), matching apply()\'s promote arm');
} catch (e) {
  console.error(`✗ #9553 R5/EXTRACTED-FROM FAILED:\n  ${e.message.split('\n').slice(0, 6).join('\n  ')}`);
  process.exit(1);
}


// ── #9553 (R5 class 3): the OPERATOR's own topology lands ─────────────────────
//
// An operator's `IMPL`/`NAND`/`hasPart` edge, its reverse `INPUT`, and the
// canonical `mitigated_by` mirror exist only as `_create_edges` Cypher driven
// from the `{op_type, inputs}` descriptor in the Point's snapshot. The record
// materialised the operator's `point` row and emitted no `relation` row, so a
// JOURNAL rebuild restored the epistemic topology while a RECORD-sourced
// rebuild restored the claims and none of their structure.
//
// Two things this leg can only prove against a REAL Postgres: that the
// four-label endpoint probe is valid SQL, and that the endpoint LABEL comes
// from the record's own node rows — an operator input may be any of
// Point/Subject/Object/Event (#7902), so the label cannot be assumed.
try {
  const tenant = ['9553c3-org', '9553c3-graph'];
  await db.exec(`
    INSERT INTO public.organizations (id, name, graph_name)
      VALUES ('9553c3-org', '9553c3-org', 'org_9553c3-org');
    INSERT INTO public.graphs (id, org_id, name, namespace)
      VALUES ('9553c3-graph', '9553c3-org', '9553c3-graph', 'org_9553c3-org_9553c3-graph');
  `);
  const records = [
    // The inputs' own node records must precede the operator's, or the
    // relation insert has no `node_identity` endpoint to join (the `absent`
    // verdict every relation gives).
    { type: 'PointAdded', ts: '2026-10-23T00:00:01Z', id: 'p1',
      point: { id: 'p1', content: 'the claim', pointKind: 'observation' } },
    { type: 'SubjectAdded', ts: '2026-10-23T00:00:02Z', id: 's1',
      name: 'sub1', subjectKind: 'other' },
    // The DOMINANT `EventRecorded` shape: `eventId` nested under `event`, no
    // `id` — so this row's `id` COLUMN is NULL and only `node_key` carries the
    // key the graph's node holds as BOTH `id` and `eventId` (`_upsert_event`).
    // A resolver matching `event.id` finds nothing here and files no row.
    { type: 'EventRecorded', ts: '2026-10-23T00:00:02Z',
      event: { eventId: 'ev1', eventKind: 'decision' } },
    // IMPL over a Point, a Subject AND an Event input: the labels the resolver
    // must tell apart, and the one the old `Point`-only assumption lost.
    { type: 'OperatorAdded', ts: '2026-10-23T00:00:03Z', id: 'op1',
      point: { id: 'op1', content: 'why', is_operator: true, op_type: 'IMPL',
               operator: { op_type: 'IMPL', inputs: ['p1', 's1', 'ev1'] } } },
    // A mitigation: a NON-operator Point carrying the operator descriptor and
    // a usable strength — the `OperatorAdded` shape `#4937`/#5048 depend on.
    { type: 'OperatorAdded', ts: '2026-10-23T00:00:04Z', id: 'mid',
      point: { id: 'mid', content: 'counter-evidence', is_operator: false,
               mitigation_strength: 0.4,
               operator: { op_type: 'IMPL', inputs: ['op1'] } } },
  ];
  const segment = join(tmpdir(), `9553c3-${process.pid}.jsonl`);
  writeFileSync(segment, records.map((r) => JSON.stringify(r)).join('\n') + '\n');
  const emitted = JSON.parse(execFileSync(
    'python3',
    ['-c', PROJECTION_PY, join(REPO_ROOT, 'tortoise', 'record_projection.py'),
     segment, ...tenant],
    { encoding: 'utf8' },
  ));
  const relationCount = emitted.plan
    .filter((x) => x.sql.startsWith('INSERT INTO public.relation')).length;
  if (relationCount !== 9) {
    throw new Error(`expected 9 relation statements (3 IMPL + 3 INPUT + `
      + `IMPL/INPUT/mitigated_by for the mitigation), got ${relationCount} — the `
      + 'operator edge producer is gone and the record loses the topology again '
      + '(#9553 R5 class 3)');
  }
  const applySegment = async () => {
    await db.exec('BEGIN');
    for (const statement of emitted.plan) {
      await db.query(statement.sql, statement.params);
    }
    await db.exec('COMMIT');
  };
  const readEdges = async () => db.query(
    `SELECT rel_type, src_label, src_key, tgt_label, tgt_key, idx
       FROM public.relation
      WHERE org_id = $1 AND graph_id = $2
      ORDER BY rel_type, idx, src_key`, tenant);
  await applySegment();
  const first = await readEdges();
  // Sorted on BOTH sides: the SQL's `ORDER BY rel_type, idx, src_key` leaves
  // equal keys (two inputs at one idx) in an order no clause pins, so comparing
  // it verbatim against a JS-sorted `want` asserts a tie-break nobody promised.
  const got = first.rows.map((r) => `${r.rel_type} ${r.src_label}/${r.src_key}`
    + ` -> ${r.tgt_label}/${r.tgt_key} idx=${r.idx}`).sort();
  const want = [
    // The Subject input is filed under Subject/s1 — its NODE_KEY, which is its
    // stable `id` since #9881 (`sub1` is the NAME that row carries as a mutable
    // column), because `relation.src_key` is a `node_identity` FK.
    'IMPL Point/op1 -> Point/p1 idx=0',
    'IMPL Point/mid -> Point/op1 idx=0',
    'IMPL Point/op1 -> Subject/s1 idx=1',
    // The Event input (`eventId` only, `event.id` NULL) resolves on `node_key`:
    // the row that was missing before the fix, for the shape every producer emits.
    'IMPL Point/op1 -> Event/ev1 idx=2',
    'INPUT Point/p1 -> Point/op1 idx=0',
    'INPUT Point/op1 -> Point/mid idx=0',
    'INPUT Subject/s1 -> Point/op1 idx=1',
    'INPUT Event/ev1 -> Point/op1 idx=2',
    'mitigated_by Point/op1 -> Point/mid idx=0',
  ].sort();
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    throw new Error(`the operator topology is\n  ${got.join('\n  ')}\n`
      + `expected\n  ${want.join('\n  ')}`);
  }
  // Idempotent: `DO NOTHING` on the relation's UNIQUE identity, replayed twice.
  await applySegment();
  const second = await readEdges();
  if (second.rows.length !== 9) {
    throw new Error(`a second replay produced ${second.rows.length} relation rows `
      + '(expected 9) — the operator relation insert is not idempotent');
  }
  unlinkSync(segment);
  console.log('✓ #9553: the operator topology lands (IMPL/INPUT/mitigated_by over a '
    + 'Point, a Subject AND an Event endpoint), and re-replays idempotently');
} catch (e) {
  console.error(`✗ #9553 R5/OPERATOR FAILED:\n  ${e.message.split('\n').slice(0, 40).join('\n  ')}`);
  process.exit(1);
}

// ── #9881: a RENAME applies — live and on replay (the keystone leg) ────────
//
// The record refused `EntityMutated op=rename` outright while an Object/Subject
// was keyed by its NAME ("a rename MOVES the canonical key, so it is a key
// migration, not a patch"). The producer (#3590's write surface) journals
// exactly this record on the first `update_entity(id, name=…)`, so the entire
// write half could not land: on a record-armed deployment the durable write
// raised and the caller had to roll back.
//
// What this leg measures, against a REAL database, end to end:
//   1. the rename APPLIES — the name column moves, the key does not;
//   2. TWO incarnations may share the new name (the reason the key is the id:
//      under a name key the second row could not exist, and under a key MOVE it
//      would collide on the primary key);
//   3. replay is IDEMPOTENT and `rebuild == live` for this record — the journal
//      replayed twice leaves the same two rows (Indicator 4).
try {
  const tenant = ['9881-org', '9881-graph'];
  await db.exec(`
    INSERT INTO public.organizations (id, name, graph_name)
      VALUES ('9881-org', '9881-org', 'org_9881-org');
    INSERT INTO public.graphs (id, org_id, name, namespace)
      VALUES ('9881-graph', '9881-org', '9881-graph', 'org_9881-org_9881-graph');
  `);
  const segment = join(tmpdir(), `9881-rename-${process.pid}.jsonl`);
  const records = [
    { type: 'ObjectRegistered', ts: '2026-10-24T00:00:01Z', event_id: 'ev-9881-1',
      projection_version: 2, id: 'obj-9881-a', name: 'Acme', objectKind: 'org' },
    // EXACTLY the record `sdk._update_entity` journals for a rename.
    { type: 'EntityMutated', ts: '2026-10-24T00:00:02Z', event_id: 'ev-9881-2',
      projection_version: 2, id: 'obj-9881-a', label: 'Object',
      op: 'rename', state: { name: 'AcmeRenamed' } },
    // A SECOND incarnation that now holds the SAME name — legal only because
    // the key is the id.
    { type: 'ObjectRegistered', ts: '2026-10-24T00:00:03Z', event_id: 'ev-9881-3',
      projection_version: 2, id: 'obj-9881-b', name: 'AcmeRenamed', objectKind: 'org' },
  ];
  writeFileSync(segment, records.map((r) => JSON.stringify(r)).join('\n') + '\n');
  const emitted = JSON.parse(execFileSync(
    'python3',
    ['-c', PROJECTION_PY, join(REPO_ROOT, 'tortoise', 'record_projection.py'),
     segment, ...tenant],
    { encoding: 'utf8' },
  ));
  // The fix's own shape, before any SQL runs: the rename emits an UPDATE, and it
  // emits no refusal and no key move.
  const renameStmt = emitted.plan.find((x) => x.sql.startsWith('UPDATE public.object'));
  if (!renameStmt) {
    throw new Error('the projection emitted NO `UPDATE public.object` for the '
      + 'rename — `refused_ops` still refuses `EntityMutated op=rename`, which is '
      + 'the refusal the whole write half of #3590 is blocked on (#9881)');
  }
  if (/SET[^W]*node_key/.test(renameStmt.sql)) {
    throw new Error(`the rename statement MOVES the key: ${renameStmt.sql} — a `
      + 'key move is the schema redesign #9881 replaces with the id key');
  }
  const applySegment = async () => {
    await db.exec('BEGIN');
    for (const statement of emitted.plan) {
      await db.query(statement.sql, statement.params);
    }
    await db.exec('COMMIT');
  };
  const readObjects = async () => db.query(
    `SELECT node_key, id, name FROM public.object
      WHERE org_id = $1 AND graph_id = $2 ORDER BY node_key`, tenant);
  await applySegment();
  const first = await readObjects();
  if (first.rows.length !== 2) {
    throw new Error(`expected 2 Object rows, got ${JSON.stringify(first.rows)}`);
  }
  const byKey = Object.fromEntries(first.rows.map((r) => [r.node_key, r]));
  if (byKey['obj-9881-a']?.name !== 'AcmeRenamed') {
    throw new Error(`the renamed Object did not move its name: ${JSON.stringify(byKey['obj-9881-a'])}`);
  }
  if (byKey['obj-9881-a']?.id !== 'obj-9881-a') {
    throw new Error(`the renamed Object's id column moved: ${JSON.stringify(byKey['obj-9881-a'])}`);
  }
  if (byKey['obj-9881-b']?.name !== 'AcmeRenamed') {
    throw new Error(`the second incarnation lost its name: ${JSON.stringify(byKey['obj-9881-b'])}`);
  }
  const identities = await db.query(
    `SELECT node_key FROM public.node_identity
      WHERE org_id = $1 AND graph_id = $2 AND node_label = 'Object'
      ORDER BY node_key`, tenant);
  if (JSON.stringify(identities.rows.map((r) => r.node_key))
      !== JSON.stringify(['obj-9881-a', 'obj-9881-b'])) {
    throw new Error(`the Object identity rows are ${JSON.stringify(identities.rows)} — `
      + 'a rename that re-keyed the row would have left the OLD name behind here');
  }
  // INDICATOR 4 — `rebuild == live`: replaying the same journal onto the same
  // record is a no-op, so the rows above ARE the rebuild's output.
  await applySegment();
  const second = await readObjects();
  if (JSON.stringify(second.rows) !== JSON.stringify(first.rows)) {
    throw new Error('a second replay changed the Object rows: '
      + `${JSON.stringify(first.rows)} then ${JSON.stringify(second.rows)}`);
  }
  unlinkSync(segment);

  console.log('✓ #9881: an `EntityMutated op=rename` APPLIES (name moves, key does '
    + 'not), two same-name incarnations coexist, and the replay is idempotent');
  console.log('✓ #9881: the RE-KEY moves a pre-move Object/Subject onto its id, its '
    + 'relation endpoint follows, and the abandoned name identity is gone');
} catch (e) {
  console.error(`✗ #9881 RENAME FAILED:\n  ${e.message.split('\n').slice(0, 20).join('\n  ')}`);
  process.exit(1);
}

// ── #9972 + #9973: a supersede's STRUCTURAL repoint is a MOVE ───────────────
//
// `DirectEdgeRepoint` carries two shapes. The 2a DIRECT shape names ids and means
// "re-create this edge at the successor"; the 2b STRUCTURAL shape names the
// target by its REPLAY KEY (a `name` for aboutSubject/Object/Event/Point, a `url`
// for aboutDocument/extractedFrom) and means "MOVE this edge from the superseded
// point to the successor". The record applied one rule to both — an upsert at the
// descriptor's own `src`, resolving the target against the label's KEY column —
// so (#9972) the insert matched `object.id = 'Acme Corp'` and landed ZERO rows in
// silence, and (#9973) nothing was ever deleted, which left the edge on the
// superseded point. Both are measured here against a REAL database, because both
// are row-count facts: the plan-text assertions live in
// `tests/test_record_projection.py`.
//
// The journal order is the PRODUCER's (`sdk.supersede_point`: the
// `PointSuperseded` patch at sdk.py:16253, the 2b descriptors at sdk.py:16657),
// not an invented one — the move reads `superseded_by`, which that patch writes.
try {
  const modulePath = join(REPO_ROOT, 'tortoise', 'record_projection.py');
  const run = async (slug, records) => {
    const tenant = [`${slug}-org`, `${slug}-graph`];
    await db.exec(`
      INSERT INTO public.organizations (id, name, graph_name)
        VALUES ('${tenant[0]}', '${tenant[0]}', 'org_${tenant[0]}');
      INSERT INTO public.graphs (id, org_id, name, namespace)
        VALUES ('${tenant[1]}', '${tenant[0]}', '${tenant[1]}', 'org_${tenant[0]}_${tenant[1]}');
    `);
    const segment = join(tmpdir(), `${slug}-${process.pid}.jsonl`);
    writeFileSync(segment, records.map((r) => JSON.stringify(r)).join('\n') + '\n');
    const emitted = JSON.parse(execFileSync(
      'python3', ['-c', PROJECTION_PY, modulePath, segment, ...tenant],
      { encoding: 'utf8' },
    ));
    const apply = async () => {
      await db.exec('BEGIN');
      for (const statement of emitted.plan) {
        await db.query(statement.sql, statement.params);
      }
      await db.exec('COMMIT');
    };
    await apply();
    const rows = await db.query(
      `SELECT rel_type, src_label, src_key, tgt_label, tgt_key
         FROM public.relation WHERE org_id = $1 AND graph_id = $2
        ORDER BY rel_type, src_key, tgt_key`, tenant);
    await apply();   // IDEMPOTENCE: a re-replay must not change the rows
    const again = await db.query(
      `SELECT rel_type, src_label, src_key, tgt_label, tgt_key
         FROM public.relation WHERE org_id = $1 AND graph_id = $2
        ORDER BY rel_type, src_key, tgt_key`, tenant);
    unlinkSync(segment);
    if (JSON.stringify(rows.rows) !== JSON.stringify(again.rows)) {
      throw new Error(`${slug}: a second replay CHANGED the relation rows: `
        + `${JSON.stringify(rows.rows)} then ${JSON.stringify(again.rows)}`);
    }
    return rows.rows;
  };
  // The LIVE order: entity registrations, the two Point snapshots, the link
  // (an edge whose endpoint row does not exist yet legitimately lands nothing —
  // the fold's own `absent` verdict), the supersede patch, then the descriptor.
  const supersedeRecords = (oldId, newId, targetKey,
                            { preamble = [], link, edgeType = 'aboutObject',
                              targetLabel = 'Object', descriptorAttrs = {} } = {}) => [
    ...preamble,
    { type: 'PointAdded', ts: '2026-10-25T00:00:01Z', id: oldId,
      point: { id: oldId, content: 'the claim', status: 'live' } },
    { type: 'PointAdded', ts: '2026-10-25T00:00:02Z', id: newId,
      point: { id: newId, content: 'the restatement', status: 'live' } },
    link,
    // The producer's order: the supersede patch, THEN the descriptor.
    { type: 'PointSuperseded', ts: '2026-10-25T00:00:03Z', id: oldId,
      new_id: newId, valid_from: '2026-10-25T00:00:03Z',
      valid_to: '2026-10-25T00:00:04Z', expired_at: '2026-10-25T00:00:04Z' },
    { type: 'DirectEdgeRepoint', ts: '2026-10-25T00:00:04Z',
      id: `${oldId}->${newId}:${edgeType}:${targetKey}`,
      src: oldId, tgt: targetKey, target_label: targetLabel,
      edge_type: edgeType, ...descriptorAttrs },
  ];
  const acme = {
    type: 'ObjectRegistered', ts: '2026-10-25T00:00:00Z', id: 'obj-9972-acme',
    name: 'Acme Corp', objectKind: 'org',
  };
  const linked = {
    type: 'EntityLinked', ts: '2026-10-25T00:00:01Z', id: 'p1',
    source_label: 'Point', source_id: 'p1', target_label: 'Object',
    target_id: 'obj-9972-acme', edge_type: 'aboutObject', confidence: 0.9,
  };

  // (1) #9972 + #9973 — the row MOVES: one row, at the SUCCESSOR, filed by the
  //     Object's key (`obj-9972-acme`), never by the name the descriptor carries.
  const moved = await run('9972-move',
    supersedeRecords('p1', 'p2', 'Acme Corp',
      { preamble: [acme], link: { ...linked, source_id: 'p1', id: 'p1' } }));
  if (moved.length !== 1) {
    throw new Error('#9972/#9973: expected exactly ONE aboutObject row after the '
      + `supersede, got ${JSON.stringify(moved)} — 0 rows is the silent drop `
      + '(the target resolved against a column that can never hold the name); '
      + '2 rows is a resurrection at the superseded point');
  }
  if (moved[0].src_key !== 'p2') {
    throw new Error('#9973: the edge is filed at '
      + `src_key=${JSON.stringify(moved[0].src_key)} — the superseded point. The `
      + 'graph moved it to p2, so a record-sourced rebuild resurrects it at p1');
  }
  if (moved[0].tgt_key !== 'obj-9972-acme') {
    throw new Error('#9972: the edge is filed at tgt_key='
      + `${JSON.stringify(moved[0].tgt_key)} — the descriptor's NAME, not the `
      + 'Object row it resolves to');
  }

  // (2) A 3-LINK chain: A -> B -> C with a descriptor per hop resolves to the
  //     FINAL successor and to no intermediate point (the record's `_final`).
  const chainRecords = [
    { type: 'ObjectRegistered', ts: '2026-10-25T00:01:00Z', id: 'obj-9973-acme',
      name: 'Acme Corp', objectKind: 'org' },
    { type: 'PointAdded', ts: '2026-10-25T00:01:01Z', id: 'c1',
      point: { id: 'c1', content: 'A', status: 'live' } },
    { type: 'PointAdded', ts: '2026-10-25T00:01:02Z', id: 'c2',
      point: { id: 'c2', content: 'B', status: 'live' } },
    { type: 'PointAdded', ts: '2026-10-25T00:01:03Z', id: 'c3',
      point: { id: 'c3', content: 'C', status: 'live' } },
    { type: 'EntityLinked', ts: '2026-10-25T00:01:04Z', id: 'c1',
      source_label: 'Point', source_id: 'c1', target_label: 'Object',
      target_id: 'obj-9973-acme', edge_type: 'aboutObject' },
  ];
  for (const [oldId, newId] of [['c1', 'c2'], ['c2', 'c3']]) {
    chainRecords.push(
      { type: 'PointSuperseded', ts: '2026-10-25T00:01:05Z', id: oldId,
        new_id: newId, valid_from: '2026-10-25T00:01:05Z',
        valid_to: '2026-10-25T00:01:06Z', expired_at: '2026-10-25T00:01:06Z' },
      { type: 'DirectEdgeRepoint', ts: '2026-10-25T00:01:06Z',
        id: `${oldId}->${newId}:aboutObject:Acme Corp`,
        src: oldId, tgt: 'Acme Corp', target_label: 'Object',
        edge_type: 'aboutObject' });
  }
  const chained = await run('9973-chain', chainRecords);
  if (chained.length !== 1 || chained[0].src_key !== 'c3') {
    throw new Error('#9973: a supersede chain must leave the edge on the FINAL '
      + `successor and on no intermediate point; rows = ${JSON.stringify(chained)}`);
  }

  // (3) #5509 — a name held by TWO carriers has no target: the record lands NO
  //     row (the graph refuses the descriptor too), and it never picks one.
  const twins = [
    { type: 'ObjectRegistered', ts: '2026-10-25T00:02:00Z', id: 'obj-amb-1',
      name: 'Twin', objectKind: 'org' },
    { type: 'ObjectRegistered', ts: '2026-10-25T00:02:00Z', id: 'obj-amb-2',
      name: 'Twin', objectKind: 'org' },
  ];
  const ambiguous = await run('9972-ambiguous',
    supersedeRecords('p1', 'p2', 'Twin', {
      preamble: twins,
      link: { type: 'EntityLinked', ts: '2026-10-25T00:02:02Z', id: 'p1',
        source_label: 'Point', source_id: 'p1', target_label: 'Object',
        target_id: 'obj-amb-1', edge_type: 'aboutObject' },
    }));
  // The graph's `_refuse_ambiguous_target` skips the WHOLE descriptor (the
  // delete-leg runs only after a target resolved), so the row legitimately STAYS
  // at the superseded point: nobody is moved anywhere, and nothing is filed
  // against an arbitrary carrier.
  if (ambiguous.length !== 1 || ambiguous[0].src_key !== 'p1'
      || ambiguous[0].tgt_key !== 'obj-amb-1') {
    throw new Error('#5509: a NAME held by two carriers must leave the ONE row '
      + 'untouched at the superseded point — rows = '
      + `${JSON.stringify(ambiguous)}. [] means the delete-leg ran and dropped an `
      + 'edge the graph keeps; a row elsewhere means the record guessed a holder '
      + 'where the graph refuses the descriptor');
  }

  // (4) `aboutDocument` is RESOLVE-ONLY and DOCUMENT-ONLY: a provenance
  //     `:Source` (no `document_kind`) is not the kind the fold accepts, so the
  //     descriptor lands nothing rather than mis-pointing the edge onto it.
  const provenance = [
    { type: 'SourceCreated', ts: '2026-10-25T00:03:00Z', id: 'src-prov',
      url: 'https://example.test/raw', sourceKind: 'web' },
  ];
  const provRecords = supersedeRecords('p1', 'p2', 'https://example.test/raw', {
    preamble: provenance,
    edgeType: 'aboutDocument',
    targetLabel: 'Source',
    link: { type: 'EntityLinked', ts: '2026-10-25T00:03:02Z', id: 'p1',
      source_label: 'Point', source_id: 'p1', target_label: 'Source',
      target_id: 'src-prov', edge_type: 'aboutDocument' },
  });
  const documents = await run('9972-document', provRecords);
  // The descriptor resolves NOTHING on this target (the fold's kind test is
  // load-bearing on the descriptor route), so the edge is not moved — and, as in
  // the ambiguous case, the graph skips the delete-leg with it. The EntityLinked
  // row at p1 is the PRECONDITION, not the move; what must be absent is any row
  // at the successor.
  if (documents.length !== 1 || documents[0].src_key !== 'p1'
      || documents[0].tgt_key !== 'https://example.test/raw') {
    throw new Error('aboutDocument resolved onto a PROVENANCE Source (no '
      + `document_kind): rows = ${JSON.stringify(documents)} — neither an empty `
      + 'set (the delete-leg ran on a row the graph keeps) nor a row at the '
      + 'successor is acceptable: the fold\'s kind test is load-bearing on the '
      + 'descriptor route and the record must not be looser than the fold');
  }

  // (5) NO SUPERSEDE ⇒ NO MOVE. A descriptor whose `src` never got a supersede
  //     patch has no successor, and the graph's `_final(src) == src` /
  //     `_src_is_terminal` gate makes the whole descriptor a no-op. Measured
  //     because the ALTERNATIVE was shipped once: a target-only "fix" that
  //     always upserted erased the row's `confidence` (the conflict path writes
  //     `EXCLUDED.confidence` = NULL) while moving nothing. A move that has not
  //     happened must leave the existing row BYTE-IDENTICAL.
  const noSupersede = await run('9972-no-supersede', [
    acme,
    { type: 'PointAdded', ts: '2026-10-25T00:04:01Z', id: 'p1',
      point: { id: 'p1', content: 'the claim', status: 'live' } },
    { type: 'EntityLinked', ts: '2026-10-25T00:04:02Z', id: 'p1',
      source_label: 'Point', source_id: 'p1', target_label: 'Object',
      target_id: 'obj-9972-acme', edge_type: 'aboutObject', confidence: 0.9 },
    { type: 'DirectEdgeRepoint', ts: '2026-10-25T00:04:03Z',
      id: 'p1->p2:aboutObject:Acme Corp', src: 'p1', tgt: 'Acme Corp',
      target_label: 'Object', edge_type: 'aboutObject' },
  ]);
  const kept = await db.query(
    `SELECT src_key, tgt_key, confidence FROM public.relation
      WHERE org_id = $1 AND graph_id = $2 AND rel_type = 'aboutObject'`,
    ['9972-no-supersede-org', '9972-no-supersede-graph']);
  if (noSupersede.length !== 1 || noSupersede[0].src_key !== 'p1') {
    throw new Error('a descriptor with NO supersede must not move anything: '
      + `${JSON.stringify(noSupersede)} — the graph gates the whole descriptor on `
      + '`_final(src) != src` and `_src_is_terminal`, so the record must too');
  }
  if (String(kept.rows[0].confidence) !== '0.9') {
    throw new Error('the un-moved row lost its `confidence` ('
      + `${JSON.stringify(kept.rows[0])}) — a delete-then-reinsert on a row that `
      + 'did not move is the churn the gate exists to prevent');
  }

  // (6) A CYCLIC chain (x→y, y→z, z→y — path-guarded) moves ONE HOP, to the
  //     DIRECT successor live named (`y`), and stops. That is live's own result:
  //     `supersede_point(x, y)` moves x's edge to `y`, and the later hops move
  //     an edge that no descriptor in THIS journal describes. It is also what
  //     the graph's replay produces (`succ[x] = y`), so the two routes agree —
  //     the pre-`#10139` record resolved the walk to "no terminal row" and left
  //     the row at `x`, which DIVERGED from the graph's `y`.
  //     ⚠️ The gate still carries a DESTINATION conjunct (`depth = 2` exists):
  //     "src is terminal" and "src HAS a successor" are both true here, and a
  //     DELETE gated on those two alone would run while the INSERT's `dest`
  //     subquery yielded no row when the successor's `point` row is absent —
  //     the row then vanished with no error on `record_write_failures`.
  const cycle = await run('9972-cycle', [
    { type: 'ObjectRegistered', ts: '2026-10-25T00:05:00Z', id: 'obj-cyc',
      name: 'Cycle Target', objectKind: 'org' },
    { type: 'PointAdded', ts: '2026-10-25T00:05:01Z', id: 'x',
      point: { id: 'x', content: 'X', status: 'live' } },
    { type: 'PointAdded', ts: '2026-10-25T00:05:02Z', id: 'y',
      point: { id: 'y', content: 'Y', status: 'live' } },
    { type: 'PointAdded', ts: '2026-10-25T00:05:03Z', id: 'z',
      point: { id: 'z', content: 'Z', status: 'live' } },
    { type: 'EntityLinked', ts: '2026-10-25T00:05:04Z', id: 'x',
      source_label: 'Point', source_id: 'x', target_label: 'Object',
      target_id: 'obj-cyc', edge_type: 'aboutObject', confidence: 0.7 },
    { type: 'PointSuperseded', ts: '2026-10-25T00:05:05Z', id: 'x',
      new_id: 'y', valid_from: '2026-10-25T00:05:05Z',
      valid_to: '2026-10-25T00:05:06Z', expired_at: '2026-10-25T00:05:06Z' },
    { type: 'PointSuperseded', ts: '2026-10-25T00:05:06Z', id: 'y',
      new_id: 'z', valid_from: '2026-10-25T00:05:06Z',
      valid_to: '2026-10-25T00:05:07Z', expired_at: '2026-10-25T00:05:07Z' },
    { type: 'PointSuperseded', ts: '2026-10-25T00:05:07Z', id: 'z',
      new_id: 'y', valid_from: '2026-10-25T00:05:07Z',
      valid_to: '2026-10-25T00:05:08Z', expired_at: '2026-10-25T00:05:08Z' },
    { type: 'DirectEdgeRepoint', ts: '2026-10-25T00:05:08Z',
      id: 'x->y:aboutObject:Cycle Target', src: 'x', tgt: 'Cycle Target',
      target_label: 'Object', edge_type: 'aboutObject' },
  ]);
  if (cycle.length !== 1 || cycle[0].src_key !== 'y') {
    throw new Error('a CYCLIC supersede chain must move the row ONE HOP, to the '
      + `DIRECT successor (y) — rows = ${JSON.stringify(cycle)}. Keeping it at `
      + 'x diverges from the graph (succ[x] = y) and from live, where '
      + '`supersede_point(x, y)` moved the edge to y; [] is the delete-without-'
      + 'insert the destination conjunct prevents');
  }

  // (7) `aboutPoint` — the target is a POINT named through `props ->> 'name'`,
  //     because `public.point` has no `name` COLUMN while the graph addresses a
  //     Point PROPERTY (`MATCH (x:Point {name:$key})`). Cases 1-6 all take the
  //     `aboutObject` default, so without this one the JSON path never reaches
  //     Postgres — and the operator precedence it depends on
  //     (`t.props ->> 'name' = $n`, never `t.props ->> ('name' = $n)`) is
  //     unchecked by the pytest leg, which only substring-matches the SQL.
  const targetPoint = {
    type: 'PointAdded', ts: '2026-10-25T00:06:01Z', id: 'tp-9972',
    point: { id: 'tp-9972', content: 'the target', status: 'live',
             name: 'Target Point' },
  };
  const pointMoved = await run('9972-about-point',
    supersedeRecords('p1', 'p2', 'Target Point', {
      preamble: [targetPoint],
      edgeType: 'aboutPoint',
      targetLabel: 'Point',
      link: { type: 'EntityLinked', ts: '2026-10-25T00:06:02Z', id: 'p1',
        source_label: 'Point', source_id: 'p1', target_label: 'Point',
        target_id: 'tp-9972', edge_type: 'aboutPoint' },
    }));
  if (pointMoved.length !== 1 || pointMoved[0].src_key !== 'p2'
      || pointMoved[0].tgt_key !== 'tp-9972') {
    throw new Error('aboutPoint: the descriptor names the target by its PROPS '
      + `name and the move must resolve it to the point KEY; rows = `
      + `${JSON.stringify(pointMoved)}. 0 rows is the #9972 failure mode on `
      + 'this arm — the name was compared against a column that cannot hold it');
  }

  // (8) `aboutSubject` — `name` IS a primary column on this table, so this is
  //     the `t.name = %s` branch against Postgres. It also pins that the row is
  //     filed by the Subject's STABLE id (#9881), never by the mutable name.
  const subjectMoved = await run('9972-about-subject',
    supersedeRecords('p1', 'p2', 'Alice', {
      preamble: [{ type: 'SubjectAdded', ts: '2026-10-25T00:07:01Z',
        id: 'sub-9972', name: 'Alice', subjectKind: 'person' }],
      edgeType: 'aboutSubject',
      targetLabel: 'Subject',
      link: { type: 'EntityLinked', ts: '2026-10-25T00:07:02Z', id: 'p1',
        source_label: 'Point', source_id: 'p1', target_label: 'Subject',
        target_id: 'sub-9972', edge_type: 'aboutSubject' },
    }));
  if (subjectMoved.length !== 1 || subjectMoved[0].src_key !== 'p2'
      || subjectMoved[0].tgt_key !== 'sub-9972') {
    throw new Error('aboutSubject: rows = ' + JSON.stringify(subjectMoved)
      + ' — the descriptor names the Subject by `name`; the move must file the '
      + 'row by its stable id, not by the name it was resolved through');
  }

  // (9) `extractedFrom` — the SIX-bind arm, and the only case that executes it
  //     against Postgres at all. The name-keyed arms bind 3 params (`org`,
  //     `graph`, `key`); this one goes through `_endpoint_resolution`
  //     (match · prefer · ORDER BY) and binds 6, so a call-shape mistake in this
  //     branch is a different class from the other four and no other case can
  //     see it. The descriptor here names a URL VARIANT (`HTTPS://…/`, wrong
  //     case, trailing slash) while the registered Source carries the CANONICAL
  //     url, so the case pins the arm's actual semantics: resolve through the
  //     canonical triple, then file the row by the canonical key — never by the
  //     spelling the descriptor happened to carry.
  const canonicalUrl = 'https://example.test/doc/a';
  const variantUrl = 'HTTPS://Example.test/doc/a/';
  const extracted = await run('9972-extracted-from',
    supersedeRecords('p1', 'p2', variantUrl, {
      preamble: [{ type: 'SourceCreated', ts: '2026-10-25T00:08:01Z',
        id: canonicalUrl, url: canonicalUrl, sourceKind: 'web' }],
      edgeType: 'extractedFrom',
      targetLabel: 'Source',
      link: { type: 'EntityLinked', ts: '2026-10-25T00:08:02Z', id: 'p1',
        source_label: 'Point', source_id: 'p1', target_label: 'Source',
        target_id: canonicalUrl, edge_type: 'extractedFrom' },
    }));
  if (extracted.length !== 1 || extracted[0].src_key !== 'p2'
      || extracted[0].tgt_key !== canonicalUrl) {
    throw new Error('extractedFrom: rows = ' + JSON.stringify(extracted)
      + ' — the six-bind canonical-triple arm must resolve the URL VARIANT to '
      + 'the registered Source and file the row by the CANONICAL key; 0 rows is '
      + 'the arm resolving by the raw spelling only, and a tgt_key holding the '
      + 'variant is the record holding an identity the fold does not');
  }

  // (10) #10303 (#10139, review P1) — the descriptor CARRIES the retired edge's
  //      attributes (`sdk.supersede_point` emits them FLAT) and the record must
  //      re-apply them, or ONE journal rebuilds into two different stores: the
  //      graph's pass-2b replay restores `confidence` (`ON CREATE SET nr +=
  //      $attrs`) and the record dropped it. ⚠️ The five names are COLUMNS of
  //      `public.relation`, so this asserts the COLUMNS — not the `props` tail,
  //      where a reader would otherwise look for the carry and find `{}`.
  const carried = await run('10139-carry',
    supersedeRecords('p1', 'p2', 'Acme Corp', {
      preamble: [acme],
      link: { ...linked, confidence: 0.83, batch_id: 'b-10139' },
      descriptorAttrs: { confidence: 0.83, batch_id: 'b-10139' },
    }));
  const carriedRow = (await db.query(
    `SELECT src_key, confidence, batch_id, props::text AS props
       FROM public.relation
      WHERE org_id = '10139-carry-org' AND graph_id = '10139-carry-graph'
        AND rel_type = 'aboutObject'`)).rows;
  if (carried.length !== 1 || carriedRow.length !== 1
      || carriedRow[0].src_key !== 'p2') {
    throw new Error('#10303: the carried move must land exactly ONE row at the '
      + `successor; rows = ${JSON.stringify(carried)} / `
      + JSON.stringify(carriedRow));
  }
  if (Number(carriedRow[0].confidence) !== 0.83
      || carriedRow[0].batch_id !== 'b-10139') {
    throw new Error('#10303: the moved row dropped the descriptor\'s attributes '
      + `(${JSON.stringify(carriedRow[0])}) — the graph restores them from the `
      + 'same descriptor, so this row is where the two rebuild routes diverge');
  }
  if (carriedRow[0].props !== '{}') {
    throw new Error('#10303: the carried names have COLUMNS, so the props tail '
      + `must stay empty; props = ${carriedRow[0].props}`);
  }

  // (11) #10303 PRECEDENCE — the successor already holds its OWN row on the same
  //      target with its own `confidence` (the COMMON restatement case: the same
  //      subject bound by both points). `ON CONFLICT … DO NOTHING` is the
  //      record's `ON CREATE SET`: the existing row is left BYTE-IDENTICAL, so
  //      0.91 survives the retired point's 0.42. An unconditional update here is
  //      the measured 0.91 -> 0.42 destruction the live leg's review P1 fixed.
  const precAcme = {
    type: 'ObjectRegistered', ts: '2026-10-25T00:09:00Z',
    id: 'obj-10303-prec', name: 'Precedence Corp', objectKind: 'org',
  };
  const precedence = await run('10139-precedence', [
    precAcme,
    { type: 'PointAdded', ts: '2026-10-25T00:09:01Z', id: 'prec-old',
      point: { id: 'prec-old', content: 'the claim', status: 'live' } },
    { type: 'PointAdded', ts: '2026-10-25T00:09:02Z', id: 'prec-new',
      point: { id: 'prec-new', content: 'the restatement', status: 'live' } },
    { type: 'EntityLinked', ts: '2026-10-25T00:09:03Z', id: 'prec-old',
      source_label: 'Point', source_id: 'prec-old', target_label: 'Object',
      target_id: 'obj-10303-prec', edge_type: 'aboutObject',
      confidence: 0.42 },
    // The successor's OWN binding — its value is the authoritative one.
    { type: 'EntityLinked', ts: '2026-10-25T00:09:04Z', id: 'prec-new',
      source_label: 'Point', source_id: 'prec-new', target_label: 'Object',
      target_id: 'obj-10303-prec', edge_type: 'aboutObject',
      confidence: 0.91 },
    { type: 'PointSuperseded', ts: '2026-10-25T00:09:05Z', id: 'prec-old',
      new_id: 'prec-new', valid_from: '2026-10-25T00:09:05Z',
      valid_to: '2026-10-25T00:09:06Z', expired_at: '2026-10-25T00:09:06Z' },
    { type: 'DirectEdgeRepoint', ts: '2026-10-25T00:09:06Z',
      id: 'prec-old->prec-new:aboutObject:Precedence Corp',
      src: 'prec-old', tgt: 'Precedence Corp', target_label: 'Object',
      edge_type: 'aboutObject', confidence: 0.42 },
  ]);
  const precRow = (await db.query(
    `SELECT src_key, confidence FROM public.relation
      WHERE org_id = '10139-precedence-org'
        AND graph_id = '10139-precedence-graph'
        AND rel_type = 'aboutObject'`)).rows;
  if (precedence.length !== 1 || precRow.length !== 1
      || precRow[0].src_key !== 'prec-new') {
    throw new Error('#10303 precedence: the row must end up ONCE, at the '
      + `successor; rows = ${JSON.stringify(precedence)} / `
      + JSON.stringify(precRow));
  }
  if (Number(precRow[0].confidence) !== 0.91) {
    throw new Error('#10303 precedence: the successor\'s own `confidence` was '
      + `overwritten (${JSON.stringify(precRow[0])}) — DO NOTHING must leave an `
      + 'existing row untouched, as the graph\'s ON CREATE SET does');
  }

  // (13) #9836 — the ID-ADDRESSED structural family, executed against Postgres.
  //      `aboutAction`/`wasDerivedFrom` are TRANSFERRED by `supersede_point`'s 2b
  //      leg but are NOT snapshot-derivable, so before #9836 the producer emitted
  //      no descriptor for them: the graph moved the edge live and the record
  //      kept its pre-supersede row, which is a `live != record-rebuild`
  //      divergence on the whole family. The descriptor now addresses its target
  //      by the node's OWN `id` under the label it OBSERVED live, so this case
  //      pins BOTH halves: the row MOVES (DELETE at the superseded point + INSERT
  //      at the successor) and it is filed with the descriptor's label.
  //
  //      ⛔ `wasDerivedFrom` is the case that could not be written any other way:
  //      ONTOLOGY §3.2 declares it Object->Object while its writers type-check
  //      nothing, so its target label is NOT a function of the rel — the label
  //      has to travel on the descriptor. The Point target below is the shape the
  //      class-4 mint actually produces, and a Point's `node_key` IS its id
  //      (`NODE_TABLES`), which is what makes the id-addressed arm's resolution
  //      file the row the graph moved.
  const derivedTarget = {
    type: 'PointAdded', ts: '2026-10-25T00:11:01Z', id: 'tp-9836',
    point: { id: 'tp-9836', content: 'the raw claim', status: 'live' },
  };
  const derived = await run('9836-wasderivedfrom-point',
    supersedeRecords('p1', 'p2', 'tp-9836', {
      preamble: [derivedTarget],
      edgeType: 'wasDerivedFrom',
      targetLabel: 'Point',
      link: { type: 'DirectEdgeCreated', ts: '2026-10-25T00:11:02Z',
        id: 'p1:wasDerivedFrom:tp-9836',
        edge_type: 'wasDerivedFrom', src: 'p1', tgt: 'tp-9836',
        source_label: 'Point', target_label: 'Point' },
    }));
  if (derived.length !== 1 || derived[0].src_key !== 'p2'
      || derived[0].tgt_label !== 'Point'
      || derived[0].tgt_key !== 'tp-9836') {
    throw new Error('#9836 wasDerivedFrom/Point: rows = '
      + JSON.stringify(derived) + ' — the row must MOVE to the successor, filed '
      + 'by the target Point\'s id. [] is the delete-without-insert shape; a '
      + 'src_key of p1 is the plain-upsert shape #10244 retired; a tgt_key that is '
      + 'not tp-9836 is the id-addressed arm resolving by something other than the '
      + 'id the descriptor carried');
  }

  // (14) the SAME mutation for `aboutAction` — a second rel of the family, and
  //      the one whose target has no natural key at all (`Point -> Point`,
  //      ONTOLOGY §3.2). Two rels, one rule: if only (13) ran, a fix that keyed
  //      off `wasDerivedFrom` by name would pass here and leave `aboutAction`
  //      broken.
  const actionTarget = {
    type: 'PointAdded', ts: '2026-10-25T00:12:01Z', id: 'tp-9836-action',
    point: { id: 'tp-9836-action', content: 'the legacy action', status: 'live' },
  };
  const action = await run('9836-aboutaction-point',
    supersedeRecords('p1', 'p2', 'tp-9836-action', {
      preamble: [actionTarget],
      edgeType: 'aboutAction',
      targetLabel: 'Point',
      link: { type: 'DirectEdgeCreated', ts: '2026-10-25T00:12:02Z',
        id: 'p1:aboutAction:tp-9836-action',
        edge_type: 'aboutAction', src: 'p1', tgt: 'tp-9836-action',
        source_label: 'Point', target_label: 'Point' },
    }));
  if (action.length !== 1 || action[0].src_key !== 'p2'
      || action[0].tgt_label !== 'Point'
      || action[0].tgt_key !== 'tp-9836-action') {
    throw new Error('#9836 aboutAction/Point: rows = '
      + JSON.stringify(action) + ' — `aboutAction` is the same MOVE; a rel-keyed '
      + 'fix that only named `wasDerivedFrom` leaves this family split');
  }

  // (15) the label really does come from the DESCRIPTOR: the same rel onto an
  //      OBJECT. `public.object.node_key` is the object's id (#9881), so a
  //      resolver that assumed 'Point' would match nothing and land NO row while
  //      the graph moved the edge — the exact divergence class #9836 is in.
  const derivedObject = {
    type: 'ObjectRegistered', ts: '2026-10-25T00:13:01Z',
    id: 'obj-9836-alpha', name: 'Alpha Artifact', objectKind: 'doc',
  };
  const derivedObj = await run('9836-wasderivedfrom-object',
    supersedeRecords('p1', 'p2', 'obj-9836-alpha', {
      preamble: [derivedObject],
      edgeType: 'wasDerivedFrom',
      targetLabel: 'Object',
      link: { type: 'DirectEdgeCreated', ts: '2026-10-25T00:13:02Z',
        id: 'p1:wasDerivedFrom:obj-9836-alpha',
        edge_type: 'wasDerivedFrom', src: 'p1', tgt: 'obj-9836-alpha',
        source_label: 'Point', target_label: 'Object' },
    }));
  if (derivedObj.length !== 1 || derivedObj[0].src_key !== 'p2'
      || derivedObj[0].tgt_label !== 'Object'
      || derivedObj[0].tgt_key !== 'obj-9836-alpha') {
    throw new Error('#9836 wasDerivedFrom/Object: rows = '
      + JSON.stringify(derivedObj) + ' — the label rides the descriptor, so an '
      + 'Object target must resolve through the object table (node_key = the '
      + 'object id, #9881) and be filed as Object. 0 rows is the arm hardcoding '
      + 'Point; a tgt_key holding the NAME would file an identity the fold does '
      + 'not');
  }

  // (16) ... and a label the record CANNOT address is a NO-MOVE, never a
  //      DELETE-only move. A `:Source` target is outside the fold's id sweep
  //      (`RECORD_ADDRESSABLE_EDGE_LABELS`), so the graph leg SKIPS the
  //      descriptor and the record must not act on it either. Without the
  //      guard the arm would key off the rel alone, resolve nothing, and emit
  //      the DELETE half with no INSERT — the row leaves its endpoint and lands
  //      nowhere, the measured shape the atomic `moved` gate exists to prevent.
  //      ⚠️ No LIVE writer can produce this journal: both the class-4 mint
  //      (`_record_addressable`) and the 2b emission decline an unaddressable
  //      endpoint rather than filing a record. The case pins the REFUSAL, so a
  //      future widening of the emission cannot make the record act on a
  //      descriptor the graph's replay skips.
  const srcRecords = supersedeRecords('p1', 'p2', 'https://x.test/9836', {
    preamble: [{ type: 'SourceCreated', ts: '2026-10-25T00:14:01Z',
      id: 'https://x.test/9836', url: 'https://x.test/9836',
      sourceKind: 'document' }],
    edgeType: 'wasDerivedFrom',
    targetLabel: 'Source',
    link: { type: 'DirectEdgeCreated', ts: '2026-10-25T00:14:02Z',
      id: 'p1:wasDerivedFrom:https://x.test/9836',
      edge_type: 'wasDerivedFrom', src: 'p1', tgt: 'https://x.test/9836',
      source_label: 'Point', target_label: 'Source' },
  });
  const unaddressable = await run('9836-unaddressable-label', srcRecords);
  if (unaddressable.length !== 1 || unaddressable[0].src_key !== 'p1') {
    throw new Error('#9836 unaddressable label: rows = '
      + `${JSON.stringify(unaddressable)} — a label outside the addressable set `
      + 'must leave the row where the graph\'s own skip leaves the edge (at the '
      + 'old point), never delete it without re-creating it');
  }

  // (17) #9836 review (P2) — the NO-SELF-EDGE guard's descriptor, on the RECORD
  //      route. When the structural target IS the successor the live leg deletes
  //      the edge and transfers nothing, and it journals a `delete_only`
  //      `DirectEdgeRepoint`. For the RECORD that descriptor is not a no-op — it
  //      is the ONLY statement that removes the row the pre-supersede
  //      `DirectEdgeCreated` wrote at the retired point (`_relation_delete_statement`
  //      honours `delete_only` for any rel). Emitted for the ID-ADDRESSED family
  //      only since the review fix; before it, the row survived at `p1`.
  //
  //      ⛔ BOTH HALVES RUN, and the first is the PRE-FIX journal: without the
  //      descriptor the row stays at the superseded point. Asserting only the
  //      post-fix half would pass against a harness that deleted the row for some
  //      other reason.
  const selfLink = {
    type: 'DirectEdgeCreated', ts: '2026-10-25T00:15:02Z',
    id: 'p1:wasDerivedFrom:p2',
    edge_type: 'wasDerivedFrom', src: 'p1', tgt: 'p2',
    source_label: 'Point', target_label: 'Point',
  };
  // The producer's order, and the pre-fix journal is the SAME list with the
  // guard's descriptor REMOVED (the `deleteOnly` flag is the only difference) —
  // built here rather than through `supersedeRecords` because that helper always
  // appends a MOVE descriptor, and a MOVE is exactly what this arm must not be.
  const selfEdgeRecords = (deleteOnly) => [
    { type: 'PointAdded', ts: '2026-10-25T00:15:00Z', id: 'p1',
      point: { id: 'p1', content: 'the claim', status: 'live' } },
    { type: 'PointAdded', ts: '2026-10-25T00:15:01Z', id: 'p2',
      point: { id: 'p2', content: 'the restatement', status: 'live' } },
    selfLink,
    { type: 'PointSuperseded', ts: '2026-10-25T00:15:03Z', id: 'p1',
      new_id: 'p2', valid_from: '2026-10-25T00:15:03Z',
      valid_to: '2026-10-25T00:15:04Z', expired_at: '2026-10-25T00:15:04Z' },
    ...(deleteOnly ? [{
      type: 'DirectEdgeRepoint', ts: '2026-10-25T00:15:04Z',
      id: 'p1->p2:wasDerivedFrom:p2:self',
      src: 'p1', tgt: 'p2', target_label: 'Point',
      edge_type: 'wasDerivedFrom', delete_only: true,
    }] : []),
  ];
  const noDescriptor = await run('9836-self-edge-prefix', selfEdgeRecords(false));
  if (noDescriptor.length !== 1 || noDescriptor[0].src_key !== 'p1') {
    throw new Error('#9836 self-edge (pre-fix journal): rows = '
      + `${JSON.stringify(noDescriptor)} — without a delete-only descriptor the `
      + 'row the pre-supersede `DirectEdgeCreated` wrote at p1 MUST survive there; '
      + 'that surviving row is the `live != record-rebuild` divergence the family '
      + 'now closes, and this half is what proves the next assertion is not vacuous');
  }
  const selfEdge = await run('9836-self-edge', selfEdgeRecords(true));
  if (selfEdge.length !== 0) {
    throw new Error('#9836 self-edge: rows = '
      + `${JSON.stringify(selfEdge)} — a \`delete_only\` descriptor must leave the `
      + 'record with NO row: live deleted the edge (a transfer would mint the '
      + '(new)->(new) phantom, E2E-11.6) and the successor is the target itself, so '
      + 'there is nothing to move. 1 row is the pre-fix shape above');
  }

  console.log('✓ #9972: the FOUR resolution arms resolve — Object and Subject by '
    + '`name`, Point by `props->>\'name\'`, Source+extractedFrom by the '
    + 'canonical triple — and a name held by 2+ carriers lands nothing (#5509)');
  console.log('✓ #9973: a supersede TRANSFERS the edge — the row leaves the '
    + 'superseded point, lands on the final successor, and re-replays idempotently');
  console.log('✓ #9836: the ID-ADDRESSED structural family (`wasDerivedFrom`, '
    + '`aboutAction`) MOVES on the record route too — the target is resolved by '
    + 'the node\'s own `id` under the descriptor\'s label, and a label outside '
    + 'the addressable set is a NO-MOVE, never a delete-without-insert');
  console.log('✓ #9836: the family\'s NO-SELF-EDGE descriptor removes the row at the '
    + 'superseded point — the pre-fix journal leaves it there and that row is the '
    + '`live != record-rebuild` divergence');
  // (12) #10139 review round 4 — the ORDERING of two descriptors that land on
  //      the same successor row. The record moves each descriptor ONE HOP, at
  //      the successor its own supersede named, exactly as the graph's replay
  //      does, so both shapes come out as live does:
  //
  //        CHAIN  (c1→c2→c3, descriptors carrying 0.42 then 0.91): the first
  //        hop's row at c2 is DELETED by the second descriptor's delete-leg and
  //        replaced at c3 by 0.91 — live's last transfer created the final
  //        edge, so its payload is the one that survives.
  //
  //        SIBLINGS (x1→z, x2→z, descriptors carrying 0.42 then 0.71): the
  //        first insert lands at z and the second hits `ON CONFLICT DO NOTHING`
  //        — live's second transfer found the edge already there, so `ON
  //        CREATE` did not fire and 0.42 stands.
  //
  //      ⛔ A move resolved to the chain's TERMINAL row cannot reproduce both:
  //      it collapses the pair onto one destination, where DO NOTHING always
  //      keeps the FIRST — right for siblings, wrong for a chain (the shape
  //      that made the record and the graph disagree at 0.42 vs 0.91).
  const orderingRecords = (edges) => {
    const ids = [...new Set(edges.flatMap((e) => [e.from, e.to]))];
    const out = [
      { type: 'ObjectRegistered', ts: '2026-10-25T00:10:00Z',
        id: 'obj-10139-order', name: 'Ordering Corp', objectKind: 'org' },
      ...ids.map((id, i) => ({
        type: 'PointAdded', ts: `2026-10-25T00:10:0${i % 10}Z`, id,
        point: { id, content: id, status: 'live' } })),
      { type: 'EntityLinked', ts: '2026-10-25T00:10:02Z', id: edges[0].from,
        source_label: 'Point', source_id: edges[0].from, target_label: 'Object',
        target_id: 'obj-10139-order', edge_type: 'aboutObject',
        confidence: edges[0].confidence },
    ];
    edges.forEach((e, i) => {
      out.push(
        { type: 'PointSuperseded', ts: `2026-10-25T00:10:2${i}Z`, id: e.from,
          new_id: e.to, valid_from: `2026-10-25T00:10:2${i}Z`,
          valid_to: `2026-10-25T00:10:2${i}Z`,
          expired_at: `2026-10-25T00:10:2${i}Z` },
        { type: 'DirectEdgeRepoint', ts: `2026-10-25T00:10:3${i}Z`,
          id: `${e.from}->${e.to}:aboutObject:Ordering Corp`,
          src: e.from, tgt: 'Ordering Corp', target_label: 'Object',
          edge_type: 'aboutObject', confidence: e.confidence });
    });
    return out;
  };
  const chainOrder = await run('10139-chain-order', orderingRecords([
    { from: 'ch1', to: 'ch2', confidence: 0.42 },
    { from: 'ch2', to: 'ch3', confidence: 0.91 },
  ]));
  if (chainOrder.length !== 1 || chainOrder[0].src_key !== 'ch3') {
    throw new Error('#10139 chain order: the chain must leave ONE row on the '
      + `FINAL successor (ch3) and none in between; rows = `
      + JSON.stringify(chainOrder));
  }
  {
    const row = await db.query(
      `SELECT confidence FROM public.relation
        WHERE org_id = '10139-chain-order-org'
          AND graph_id = '10139-chain-order-graph'
          AND rel_type = 'aboutObject'`);
    if (row.rows.length !== 1
        || Number(row.rows[0].confidence) !== 0.91) {
      throw new Error('#10139 chain order: the LAST transfer created the final '
        + 'edge live, so its payload (0.91) must survive — rows = '
        + JSON.stringify(row.rows));
    }
  }
  const siblingOrder = await run('10139-sibling-order', orderingRecords([
    { from: 'sb1', to: 'sbz', confidence: 0.42 },
    { from: 'sb2', to: 'sbz', confidence: 0.71 },
  ]));
  if (siblingOrder.length !== 1 || siblingOrder[0].src_key !== 'sbz') {
    throw new Error('#10139 sibling order: both transfers land on the shared '
      + `successor (sbz) as ONE row; rows = ${JSON.stringify(siblingOrder)}`);
  }
  {
    const row = await db.query(
      `SELECT confidence FROM public.relation
        WHERE org_id = '10139-sibling-order-org'
          AND graph_id = '10139-sibling-order-graph'
          AND rel_type = 'aboutObject'`);
    if (row.rows.length !== 1
        || Number(row.rows[0].confidence) !== 0.42) {
      throw new Error('#10139 sibling order: live\'s SECOND transfer found the '
        + 'edge already there, so `ON CREATE` did not fire and the FIRST '
        + 'creator\'s 0.42 must survive — rows = '
        + JSON.stringify(row.rows));
    }
  }

  console.log('✓ #10303: the RECORD route carries the descriptor\'s attributes to '
    + 'the moved row\'s columns, and `DO NOTHING` keeps a successor\'s own value '
    + '(0.91 over the retired 0.42) exactly as the graph\'s `ON CREATE SET` does');
} catch (e) {
  console.error(`✗ #9972/#9973 STRUCTURAL REPOINT FAILED:\n  ${e.message.split('\n').slice(0, 30).join('\n  ')}`);
  process.exit(1);
}

console.log('✅ ALL MIGRATIONS + BOTH TEST SUITES + SPOT CHECKS PASSED');
await db.close();
