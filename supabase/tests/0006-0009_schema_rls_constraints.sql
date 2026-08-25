-- ============================================================================
-- SQL-level verification for migrations 0006–0009 (issue #769)
-- Supabase control-plane schema: teams, api_keys, invitations,
-- team_memberships extension (lookup_hash + identity), audit_events
-- actor_user_id → TEXT, and effective column-level protection.
--
-- HOW TO RUN (after `supabase db reset` so migrations 0001–0009 applied):
--   psql "postgresql://postgres:postgres@127.0.0.1:54322/postgres" \
--        -v ON_ERROR_STOP=1 -f supabase/tests/0006-0009_schema_rls_constraints.sql
-- (see run_schema_tests.sh — it handles db reset + container psql)
--
-- Every assertion RAISEs on failure; with ON_ERROR_STOP=1 any failure exits
-- non-zero. Test rows use the "-769" suffix for safe cleanup.
-- ============================================================================

-- ── Assertion helper (tests schema; execution granted to app roles) ────────
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

-- ── Cleanup any prior test rows (idempotent re-runs) ────────────────────────
DELETE FROM public.api_keys WHERE id LIKE '%-769';
DELETE FROM public.invitations WHERE id LIKE '%-769';
DELETE FROM public.team_memberships WHERE team_id LIKE 'team-%-769' OR invited_email LIKE '%769test%' OR identity LIKE '%769test%';
DELETE FROM public.teams WHERE id LIKE 'team-%-769';
DELETE FROM auth.users WHERE email LIKE '%769test%';


-- Fixture teams (created FIRST — section 2 behavioral FK/unique tests
-- reference team-a-769 before the section-4 fixtures)
INSERT INTO public.teams (id, name, tier, graph_name, github_token_enc)
VALUES ('team-a-769', 'Alpha 769', 'free', 'team_alpha', 'enc:token-a-769'),
       ('team-b-769', 'Beta 769',  'free', 'team_beta',  'enc:token-b-769')
ON CONFLICT (id) DO NOTHING;

-- ============================================================================
-- SECTION 1 — migrations applied cleanly (catalog state)
-- ============================================================================
DO $$ BEGIN
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='teams'),
    '0006: public.teams must exist');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='api_keys'),
    '0007: public.api_keys must exist');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='invitations'),
    '0008: public.invitations must exist');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='team_memberships'),
    '0003/0009: public.team_memberships must exist');
END $$;

-- Required columns present with expected types / nullability
DO $$ BEGIN
  -- teams
  PERFORM tests.assert((SELECT data_type FROM information_schema.columns
    WHERE table_schema='public' AND table_name='teams' AND column_name='id') = 'text', 'teams.id text');
  PERFORM tests.assert((SELECT is_nullable FROM information_schema.columns
    WHERE table_schema='public' AND table_name='teams' AND column_name='graph_name') = 'NO', 'teams.graph_name NOT NULL (sdk.team_create uses team_{name})');
  PERFORM tests.assert((SELECT data_type FROM information_schema.columns
    WHERE table_schema='public' AND table_name='teams' AND column_name='onboarding_state') = 'jsonb', 'teams.onboarding_state jsonb');
  PERFORM tests.assert((SELECT data_type FROM information_schema.columns
    WHERE table_schema='public' AND table_name='teams' AND column_name='github_token_enc') = 'text', 'teams.github_token_enc text');
  -- api_keys
  PERFORM tests.assert((SELECT data_type FROM information_schema.columns
    WHERE table_schema='public' AND table_name='api_keys' AND column_name='lookup_hash') = 'text', 'api_keys.lookup_hash text');
  PERFORM tests.assert((SELECT column_default FROM information_schema.columns
    WHERE table_schema='public' AND table_name='api_keys' AND column_name='created_via')
    LIKE '%provisioned%', 'api_keys.created_via default provisioned');
  -- 20260825000001: api_keys.name — nullable user-facing label
  PERFORM tests.assert((SELECT data_type FROM information_schema.columns
    WHERE table_schema='public' AND table_name='api_keys' AND column_name='name') = 'text', '20260825000001: api_keys.name text');
  PERFORM tests.assert((SELECT is_nullable FROM information_schema.columns
    WHERE table_schema='public' AND table_name='api_keys' AND column_name='name') = 'YES', '20260825000001: api_keys.name nullable');
  -- invitations
  PERFORM tests.assert((SELECT data_type FROM information_schema.columns
    WHERE table_schema='public' AND table_name='invitations' AND column_name='lookup_hash') = 'text', 'invitations.lookup_hash text');
  -- team_memberships extension (0009)
  PERFORM tests.assert((SELECT data_type FROM information_schema.columns
    WHERE table_schema='public' AND table_name='team_memberships' AND column_name='lookup_hash') = 'text', '0009: team_memberships.lookup_hash text');
  PERFORM tests.assert((SELECT data_type FROM information_schema.columns
    WHERE table_schema='public' AND table_name='team_memberships' AND column_name='identity') = 'text', '0009: team_memberships.identity text');
  PERFORM tests.assert((SELECT is_nullable FROM information_schema.columns
    WHERE table_schema='public' AND table_name='team_memberships' AND column_name='user_id') = 'YES', '0009: team_memberships.user_id nullable');
  -- audit_events actor_user_id → TEXT (0006)
  PERFORM tests.assert((SELECT data_type FROM information_schema.columns
    WHERE table_schema='public' AND table_name='audit_events' AND column_name='actor_user_id') = 'text',
    '0006: audit_events.actor_user_id must be text');
END $$;

-- ============================================================================
-- SECTION 2 — constraints (catalog + behavioral)
-- ============================================================================
DO $$ BEGIN
  -- PKs
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.teams'::regclass AND contype='p'),
    'teams PK exists');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.api_keys'::regclass AND contype='p'),
    'api_keys PK exists');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='public.invitations'::regclass AND contype='p'),
    'invitations PK exists');
  -- FKs
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.api_keys'::regclass AND contype='f'
              AND confrelid='public.teams'::regclass AND conname LIKE '%team%'),
    'api_keys.team_id FK → teams.id');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.invitations'::regclass AND contype='f'
              AND confrelid='public.teams'::regclass AND conname LIKE '%team%'),
    'invitations.team_id FK → teams.id');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.team_memberships'::regclass AND contype='f'
              AND confrelid='auth.users'::regclass),
    'team_memberships.user_id FK → auth.users(id) kept');
  -- uniques / partial uniques
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='api_keys'
            AND indexname='uq_api_keys_lookup_hash'),
    'api_keys.lookup_hash unique index');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='invitations'
            AND indexname='uq_invitations_team_email_pending'),
    'invitations partial unique (team_id,email) WHERE pending');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='team_memberships'
            AND indexname='idx_team_memberships_lookup_hash'),
    '0009: team_memberships.lookup_hash index');
  -- amended membership CHECK includes identity path
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_constraint
            WHERE conrelid='public.team_memberships'::regclass AND conname='chk_member_or_invite'
              AND pg_get_constraintdef(oid) LIKE '%identity%'),
    '0009: chk_member_or_invite amended with identity path');
  -- RLS policies
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='teams' AND policyname='team_guc_read'),
    'teams GUC read policy');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='api_keys' AND policyname='api_keys_guc_read'),
    'api_keys GUC read policy');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='invitations' AND policyname='invitations_guc_read'),
    'invitations GUC read policy');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='teams' AND policyname='team_service_role_all'),
    'teams service_role policy');
END $$;

-- Behavioral constraint tests
DO $$ BEGIN
  -- FK: unknown team rejected
  BEGIN
    INSERT INTO public.api_keys (id, team_id, lookup_hash)
    VALUES ('key-fk-769', 'no-such-team-769', 'h');
    RAISE EXCEPTION 'FAIL: api_keys FK must reject unknown team_id';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;

  BEGIN
    INSERT INTO public.invitations (id, team_id, lookup_hash, email)
    VALUES ('inv-fk-769', 'no-such-team-769', 'h', 'x@example.com');
    RAISE EXCEPTION 'FAIL: invitations FK must reject unknown team_id';
  EXCEPTION WHEN foreign_key_violation THEN NULL; END;

  -- teams PK duplicate
  BEGIN
    INSERT INTO public.teams (id, name, graph_name) VALUES ('dup-teams-769','x','g');
    INSERT INTO public.teams (id, name, graph_name) VALUES ('dup-teams-769','y','h');
    RAISE EXCEPTION 'FAIL: teams PK must reject duplicate id';
  EXCEPTION WHEN unique_violation THEN NULL; END;

  -- api_keys lookup_hash unique
  BEGIN
    INSERT INTO public.api_keys (id, team_id, lookup_hash) VALUES ('key-u1-769','team-a-769','hash-dup-769');
    INSERT INTO public.api_keys (id, team_id, lookup_hash) VALUES ('key-u2-769','team-a-769','hash-dup-769');
    RAISE EXCEPTION 'FAIL: api_keys.lookup_hash must be unique';
  EXCEPTION WHEN unique_violation THEN NULL; END;

  -- invitations partial unique: duplicate pending rejected
  BEGIN
    INSERT INTO public.invitations (id, team_id, lookup_hash, email, status)
    VALUES ('inv-u1-769','team-a-769','h1','dup-invite@example.com','pending');
    INSERT INTO public.invitations (id, team_id, lookup_hash, email, status)
    VALUES ('inv-u2-769','team-a-769','h2','dup-invite@example.com','pending');
    RAISE EXCEPTION 'FAIL: duplicate pending invite (team,email) must be rejected';
  EXCEPTION WHEN unique_violation THEN NULL; END;

  -- ...but a non-pending row with the same (team, email) is fine
  INSERT INTO public.invitations (id, team_id, lookup_hash, email, status)
  VALUES ('inv-u3-769','team-a-769','h3','dup-invite@example.com','accepted');

  -- created_via CHECK
  BEGIN
    INSERT INTO public.api_keys (id, team_id, lookup_hash, created_via)
    VALUES ('key-cv-769','team-a-769','h-cv','hacker');
    RAISE EXCEPTION 'FAIL: api_keys.created_via CHECK must reject unknown value';
  EXCEPTION WHEN check_violation THEN NULL; END;

  -- invitations role CHECK
  BEGIN
    INSERT INTO public.invitations (id, team_id, lookup_hash, email, role)
    VALUES ('inv-role-769','team-a-769','h-r','role@example.com','superadmin');
    RAISE EXCEPTION 'FAIL: invitations.role CHECK must reject unknown value';
  EXCEPTION WHEN check_violation THEN NULL; END;

  -- team_memberships amended CHECK: all three anchors NULL → rejected
  BEGIN
    INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role)
    VALUES (NULL, 'team-a-769', 'n', 'k', 'g', 'member');
    RAISE EXCEPTION 'FAIL: membership CHECK must reject all-NULL (user_id, invited_email, identity)';
  EXCEPTION WHEN check_violation THEN NULL; END;
END $$;

-- ============================================================================
-- SECTION 3 — column-level protection (effective pattern)
-- ============================================================================
DO $$ BEGIN
  -- github_token_enc hidden from public-facing roles; service_role keeps it
  PERFORM tests.assert(
    has_column_privilege('authenticated', 'public.teams', 'github_token_enc', 'SELECT') = false,
    'github_token_enc: authenticated must NOT have SELECT');
  PERFORM tests.assert(
    has_column_privilege('anon', 'public.teams', 'github_token_enc', 'SELECT') = false,
    'github_token_enc: anon must NOT have SELECT');
  PERFORM tests.assert(
    has_column_privilege('service_role', 'public.teams', 'github_token_enc', 'SELECT') = true,
    'github_token_enc: service_role must keep SELECT');
  -- other teams columns remain readable by authenticated
  PERFORM tests.assert(
    has_column_privilege('authenticated', 'public.teams', 'name', 'SELECT') = true,
    'teams.name: authenticated must keep SELECT');
  -- api_key on team_memberships hidden (0009 repair of the 0003 no-op revoke)
  PERFORM tests.assert(
    has_column_privilege('authenticated', 'public.team_memberships', 'api_key', 'SELECT') = false,
    'team_memberships.api_key: authenticated must NOT have SELECT');
  PERFORM tests.assert(
    has_column_privilege('anon', 'public.team_memberships', 'api_key', 'SELECT') = false,
    'team_memberships.api_key: anon must NOT have SELECT');
  PERFORM tests.assert(
    has_column_privilege('service_role', 'public.team_memberships', 'api_key', 'SELECT') = true,
    'team_memberships.api_key: service_role must keep SELECT');
  PERFORM tests.assert(
    has_column_privilege('authenticated', 'public.team_memberships', 'team_name', 'SELECT') = true,
    'team_memberships.team_name: authenticated keeps SELECT');
  -- lookup_hash hidden on api_keys + invitations
  PERFORM tests.assert(
    has_column_privilege('authenticated', 'public.api_keys', 'lookup_hash', 'SELECT') = false,
    'api_keys.lookup_hash hidden from authenticated');
  PERFORM tests.assert(
    has_column_privilege('authenticated', 'public.invitations', 'lookup_hash', 'SELECT') = false,
    'invitations.lookup_hash hidden from authenticated');
  PERFORM tests.assert(
    has_column_privilege('service_role', 'public.api_keys', 'lookup_hash', 'SELECT') = true,
    'api_keys.lookup_hash: service_role keeps SELECT');
  PERFORM tests.assert(
    has_column_privilege('anon', 'public.api_keys', 'lookup_hash', 'SELECT') = false,
    'api_keys.lookup_hash: anon must NOT have SELECT');
  PERFORM tests.assert(
    has_column_privilege('anon', 'public.invitations', 'lookup_hash', 'SELECT') = false,
    'invitations.lookup_hash: anon must NOT have SELECT');
  -- anon has NO table-level access to any new control-plane table
  PERFORM tests.assert(has_table_privilege('anon', 'public.teams', 'SELECT') = false,
    'anon: no table-level SELECT on teams');
  PERFORM tests.assert(has_table_privilege('anon', 'public.api_keys', 'SELECT') = false,
    'anon: no table-level SELECT on api_keys');
  PERFORM tests.assert(has_table_privilege('anon', 'public.invitations', 'SELECT') = false,
    'anon: no table-level SELECT on invitations');
  -- team_memberships.id (uuid PK) stays readable by authenticated
  PERFORM tests.assert(
    has_column_privilege('authenticated', 'public.team_memberships', 'id', 'SELECT') = true,
    'team_memberships.id: authenticated keeps SELECT');
END $$;

-- ============================================================================
-- SECTION 4 — RLS + GUC tenant scoping (behavioral)
-- ============================================================================
-- Fixture data (as postgres — bypasses RLS)
INSERT INTO public.api_keys (id, team_id, lookup_hash, key_prefix, created_via)
VALUES ('key-a-769', 'team-a-769', 'hash-key-a-769', 'tt_ab12', 'provisioned'),
       ('key-b-769', 'team-b-769', 'hash-key-b-769', 'tt_cd34', 'provisioned')
ON CONFLICT (id) DO NOTHING;

INSERT INTO public.invitations (id, team_id, lookup_hash, role, email, status)
VALUES ('inv-a-769', 'team-a-769', 'hash-inv-a-769', 'member', 'a-769test@example.com', 'pending'),
       ('inv-b-769', 'team-b-769', 'hash-inv-b-769', 'member', 'b-769test@example.com', 'pending')
ON CONFLICT (id) DO NOTHING;

-- auth.users fixtures (minimal Supabase-compatible insert; triggers the
-- 0001/0003 handle_new_user placeholder membership, which is fine)
INSERT INTO auth.users (instance_id, id, aud, role, email, encrypted_password,
                        email_confirmed_at, raw_app_meta_data, raw_user_meta_data)
VALUES ('00000000-0000-0000-0000-000000000000'::uuid,
        '11111111-1111-1111-1111-111111111111'::uuid,
        'authenticated', 'authenticated', 'owner-a-769test@example.com', '',
        now(), '{}'::jsonb, '{}'::jsonb),
       ('00000000-0000-0000-0000-000000000000'::uuid,
        '22222222-2222-2222-2222-222222222222'::uuid,
        'authenticated', 'authenticated', 'owner-b-769test@example.com', '',
        now(), '{}'::jsonb, '{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

-- Owner memberships + one invited-email row + one agent-signup (identity) row
INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role, status)
VALUES ('11111111-1111-1111-1111-111111111111'::uuid, 'team-a-769', 'Alpha 769', 'k-a', 'team_alpha', 'owner', 'active'),
       ('22222222-2222-2222-2222-222222222222'::uuid, 'team-b-769', 'Beta 769',  'k-b', 'team_beta',  'owner', 'active')
ON CONFLICT (user_id, team_id) DO NOTHING;

INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role, status, invited_email)
VALUES (NULL, 'team-b-769', 'Beta 769', 'k-ib', 'team_beta', 'member', 'invited', 'invitee-769test@example.com');

-- Agent-signup row: NULL user_id + identity → must satisfy the amended CHECK
INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role, status, identity)
VALUES (NULL, 'team-a-769', 'Alpha 769', 'k-ag', 'team_alpha', 'member', 'active', 'agent:anon-769test')
ON CONFLICT DO NOTHING;

DO $$ BEGIN
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM public.team_memberships
            WHERE team_id='team-a-769' AND user_id IS NULL AND identity='agent:anon-769test'),
    'agent-signup row (NULL user_id + identity) must be insertable');
END $$;

-- ── authenticated + GUC = team-a: owner/member reads OK, cross-team denied ─
SET ROLE authenticated;
SET app.current_team_id = 'team-a-769';

DO $$ BEGIN
  -- owner/member read OK (own team)
  PERFORM tests.assert(
    (SELECT count(*) FROM public.teams WHERE id='team-a-769') = 1,
    'RLS: authenticated (GUC=team-a) reads team-a');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE team_id='team-a-769') = 1,
    'RLS: authenticated (GUC=team-a) reads team-a api_keys');
  -- NB: section 2's partial-unique test leaves an extra ACCEPTED invite for
  -- team-a (inv-u3-769) — scope to pending for a deterministic count.
  PERFORM tests.assert(
    (SELECT count(*) FROM public.invitations WHERE team_id='team-a-769' AND status='pending') = 1,
    'RLS: authenticated (GUC=team-a) reads team-a pending invitations');
  -- cross-team denied
  PERFORM tests.assert(
    (SELECT count(*) FROM public.teams WHERE id='team-b-769') = 0,
    'RLS: cross-team teams read denied');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE team_id='team-b-769') = 0,
    'RLS: cross-team api_keys read denied');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.invitations WHERE team_id='team-b-769') = 0,
    'RLS: cross-team invitations read denied');
END $$;

-- column protection behavioral: safe column readable, secret column denied
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.teams WHERE id='team-a-769' AND name='Alpha 769') = 1,
    'RLS+grants: safe teams column (name) readable');
  BEGIN
    PERFORM github_token_enc FROM public.teams WHERE id='team-a-769';
    RAISE EXCEPTION 'FAIL: authenticated must not be able to read github_token_enc';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    PERFORM api_key FROM public.team_memberships LIMIT 1;
    RAISE EXCEPTION 'FAIL: authenticated must not be able to read team_memberships.api_key';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  -- id (uuid PK) readable via the 0003 auth.uid()-based policy: set the
  -- JWT sub claim so the "Users view own memberships" policy matches the
  -- owner row (proves both that policy survived 0009 AND that id is granted)
  SET request.jwt.claim.sub = '11111111-1111-1111-1111-111111111111';
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-a-769'
        AND user_id='11111111-1111-1111-1111-111111111111'::uuid
        AND id IS NOT NULL) = 1,
    '0003 membership RLS intact: owner reads own membership incl. id');
  RESET request.jwt.claim.sub;
END $$;

-- deny-by-default: no GUC set → 0 rows everywhere
RESET app.current_team_id;
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.teams) = 0,
    'RLS: unset GUC → no teams rows');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys) = 0,
    'RLS: unset GUC → no api_keys rows');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.invitations) = 0,
    'RLS: unset GUC → no invitations rows');
END $$;

-- authenticated cannot write (no write policy, no write grants)
DO $$ BEGIN
  BEGIN
    INSERT INTO public.teams (id, name, graph_name) VALUES ('team-hack-769','h','g');
    RAISE EXCEPTION 'FAIL: authenticated INSERT into teams must be denied';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
  BEGIN
    UPDATE public.teams SET name='hacked' WHERE id='team-a-769';
    RAISE EXCEPTION 'FAIL: authenticated UPDATE of teams must be denied';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;

RESET ROLE;

-- ── service_role: full management (write OK, reads everything) ─────────────
SET ROLE service_role;
DO $$ BEGIN
  INSERT INTO public.teams (id, name, tier, graph_name, github_token_enc)
  VALUES ('team-svc-769', 'Service 769', 'pro', 'team_svc', 'enc:token-svc-769');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.teams WHERE id='team-svc-769') = 1,
    'service_role write + read OK');
  PERFORM tests.assert(
    (SELECT github_token_enc FROM public.teams WHERE id='team-svc-769') = 'enc:token-svc-769',
    'service_role reads github_token_enc');
  UPDATE public.teams SET name='Service 769 v2' WHERE id='team-svc-769';
  PERFORM tests.assert(
    (SELECT name FROM public.teams WHERE id='team-svc-769') = 'Service 769 v2',
    'service_role update OK');
  INSERT INTO public.api_keys (id, team_id, lookup_hash, created_via)
  VALUES ('key-svc-769', 'team-svc-769', 'hash-svc-769', 'bootstrap');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE id='key-svc-769') = 1,
    'service_role writes api_keys');
  INSERT INTO public.invitations (id, team_id, lookup_hash, email, status)
  VALUES ('inv-svc-769', 'team-svc-769', 'hash-inv-svc-769', 'svc-769test@example.com', 'pending');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.invitations WHERE id='inv-svc-769') = 1,
    'service_role writes invitations');
  INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role, status, identity)
  VALUES (NULL, 'team-svc-769', 'Service 769', 'k-svc', 'team_svc', 'member', 'active', 'agent:svc-769test');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships WHERE team_id='team-svc-769' AND identity='agent:svc-769test') = 1,
    'service_role writes team_memberships (identity row)');
END $$;
RESET ROLE;

-- ============================================================================
-- SECTION 5 — audit_events TEXT actor (0006 ALTER)
-- ============================================================================
INSERT INTO public.audit_events (id, team_id, actor_user_id, operation)
VALUES ('evt-769-0001', 'team-a-769', 'agent-signup-abc-769', 'test_text_actor')
ON CONFLICT (id) DO NOTHING;

DO $$ BEGIN
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM public.audit_events
            WHERE id='evt-769-0001' AND actor_user_id='agent-signup-abc-769'),
    'audit_events accepts non-UUID (text) actor_user_id');
END $$;

-- ============================================================================
-- SECTION 6 — cleanup (postgres; audit_events is append-only → left in place)
-- ============================================================================
DELETE FROM public.api_keys WHERE id LIKE '%-769';
DELETE FROM public.invitations WHERE id LIKE '%-769';
DELETE FROM public.team_memberships WHERE team_id LIKE 'team-%-769';
DELETE FROM public.teams WHERE id LIKE 'team-%-769';
DELETE FROM auth.users WHERE email LIKE '%769test%';

-- Final gate: assert cleanup removed our fixture teams (audit row exempt)
DO $$ BEGIN
  PERFORM tests.assert(
    NOT EXISTS (SELECT 1 FROM public.teams WHERE id LIKE 'team-%-769'),
    'cleanup: no fixture teams left');
END $$;

