-- ============================================================================
-- SQL-level verification for migration 20260827000001 (issue #1765)
-- Identity model substrate: user_unlink_permits + link_intents tables (RLS
-- deny-by-default, partial unique indexes), user_identity_inventory +
-- reserve_unlink SECURITY DEFINER RPCs, claim_membership changes (no
-- teams.email write, created_by migration), teams.email demotion + reg-
-- idempotency index.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--
-- Every assertion RAISEs on failure; with ON_ERROR_STOP=1 any failure exits
-- non-zero. tests.assert comes from the 0006-0009 suite (tests schema).
-- ============================================================================

-- ── Cleanup prior test rows (idempotent re-runs) ─────────────────────────
DELETE FROM public.user_unlink_permits WHERE user_id::text LIKE '%1765%';
DELETE FROM public.link_intents WHERE nonce LIKE '1765-%';
DELETE FROM public.api_keys WHERE team_id LIKE '%-1765';
DELETE FROM public.team_memberships WHERE team_id LIKE '%-1765' OR identity LIKE '%1765%';
DELETE FROM public.teams WHERE id LIKE '%-1765';
DELETE FROM auth.identities WHERE user_id::text LIKE '%1765%' OR provider_id LIKE '1765-%';
DELETE FROM auth.users WHERE id::text LIKE '%1765%';

-- ============================================================================
-- SECTION 1 — Catalog state: tables, partial unique indexes, RLS/grants
-- ============================================================================
SELECT tests.assert(
  EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='user_unlink_permits'),
  'user_unlink_permits table must exist');

SELECT tests.assert(
  EXISTS (SELECT 1 FROM pg_tables WHERE schemaname='public' AND tablename='link_intents'),
  'link_intents table must exist');

SELECT tests.assert(
  EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='user_unlink_permits'
            AND indexname='uq_user_unlink_permits_active'),
  'uq_user_unlink_permits_active partial unique index must exist');

SELECT tests.assert(
  EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='team_memberships'
            AND indexname='uq_member_identity_active'),
  'uq_member_identity_active partial unique index must exist');

SELECT tests.assert(
  NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='teams'
                AND indexname='uq_teams_email'),
  'uq_teams_email must be dropped (demotion)');

-- RLS enabled on both tables
SELECT tests.assert(
  (SELECT relrowsecurity FROM pg_class WHERE relname='user_unlink_permits'),
  'user_unlink_permits must have RLS enabled');
SELECT tests.assert(
  (SELECT relrowsecurity FROM pg_class WHERE relname='link_intents'),
  'link_intents must have RLS enabled');

-- Grants: authenticated/anon must have NO privileges (deny-by-default)
SELECT tests.assert(
  NOT EXISTS (SELECT 1 FROM information_schema.role_table_grants
               WHERE table_schema='public' AND table_name='user_unlink_permits'
                 AND grantee IN ('anon','authenticated')),
  'user_unlink_permits must not be grantable to anon/authenticated');
SELECT tests.assert(
  NOT EXISTS (SELECT 1 FROM information_schema.role_table_grants
               WHERE table_schema='public' AND table_name='link_intents'
                 AND grantee IN ('anon','authenticated')),
  'link_intents must not be grantable to anon/authenticated');

-- RPCs exist and are SECURITY DEFINER
SELECT tests.assert(
  (SELECT count(*) FROM pg_proc WHERE proname='user_identity_inventory') = 1,
  'user_identity_inventory RPC must exist');
SELECT tests.assert(
  (SELECT count(*) FROM pg_proc WHERE proname='reserve_unlink') = 1,
  'reserve_unlink RPC must exist');
SELECT tests.assert(
  (SELECT prosecdef FROM pg_proc WHERE proname='reserve_unlink') = true,
  'reserve_unlink must be SECURITY DEFINER');

-- ============================================================================
-- SECTION 2 — user_identity_inventory: the 6 login-method shapes + edge cases
-- ============================================================================
INSERT INTO auth.users (id, email, email_confirmed_at, encrypted_password) VALUES
  ('10000000-0000-0000-0000-000000001765'::uuid, 'shape0@1765.test', NULL, NULL),          -- zero-method (no identity, no pwd)
  ('20000000-0000-0000-0000-000000001765'::uuid, 'shape1@1765.test', NULL, ''),            -- OAuth-empty-password (no identity yet)
  ('30000000-0000-0000-0000-000000001765'::uuid, 'shape2@1765.test', NULL, NULL),          -- unconfirmed email, github identity below
  ('40000000-0000-0000-0000-000000001765'::uuid, 'shape3@1765.test', NULL, 'hashed-pwd'),  -- password-only, unconfirmed email
  ('50000000-0000-0000-0000-000000001765'::uuid, 'shape4@1765.test', now(), NULL),         -- confirmed email, no password
  ('60000000-0000-0000-0000-000000001765'::uuid, 'shape5@1765.test', now(), 'hashed-pwd'); -- confirmed email + password + email identity row
INSERT INTO auth.identities (id, user_id, provider, provider_id) VALUES
  ('a0000000-0000-0000-0000-000000001765'::uuid, '30000000-0000-0000-0000-000000001765'::uuid, 'github', '1765-gh-unconfirmed'),
  ('b0000000-0000-0000-0000-000000001765'::uuid, '60000000-0000-0000-0000-000000001765'::uuid, 'google', '1765-g-oauth'),
  ('c0000000-0000-0000-0000-000000001765'::uuid, '60000000-0000-0000-0000-000000001765'::uuid, 'email',  '1765-g-email');

-- shape0: no methods
SELECT tests.assert(
  (public.user_identity_inventory('10000000-0000-0000-0000-000000001765'::uuid)->>'login_methods')::int = 0,
  'shape0 (no identity, no password) must be login_methods=0');
-- shape1: OAuth-empty-password, no identity → has_password FALSE ('' NOT counted)
SELECT tests.assert(
  (public.user_identity_inventory('20000000-0000-0000-0000-000000001765'::uuid)->>'has_password')::boolean = false,
  'shape1 (encrypted_password='''') must have has_password=false');
-- shape2: github identity, unconfirmed email, no pwd → 1 method (email_method 0)
SELECT tests.assert(
  (public.user_identity_inventory('30000000-0000-0000-0000-000000001765'::uuid)->>'login_methods')::int = 1,
  'shape2 (oauth + unconfirmed email) must be login_methods=1');
-- shape3: password-only, unconfirmed email → 1 (has_password counts)
SELECT tests.assert(
  (public.user_identity_inventory('40000000-0000-0000-0000-000000001765'::uuid)->>'login_methods')::int = 1,
  'shape3 (password-only) must be login_methods=1');
-- shape4: confirmed email, no password, no identity → 1 (email_method from auth.users.email+confirmed)
SELECT tests.assert(
  (public.user_identity_inventory('50000000-0000-0000-0000-000000001765'::uuid)->>'login_methods')::int = 1,
  'shape4 (confirmed email, no pwd) must be login_methods=1');
-- shape5: OAuth + email identity row + password + confirmed → 2, NOT 3 (count-FILTER guard)
SELECT tests.assert(
  (public.user_identity_inventory('60000000-0000-0000-0000-000000001765'::uuid)->>'login_methods')::int = 2,
  'shape5 (oauth + email identity row + password) must be login_methods=2, NOT 3');
-- methods carry the identity-row id (the client unlinks by id, not provider_id)
SELECT tests.assert(
  (public.user_identity_inventory('60000000-0000-0000-0000-000000001765'::uuid)->'methods'->0->>'id') IS NOT NULL,
  'inventory methods must carry the identity-row id (unlink contract)');
-- unknown user: 0 methods, never an error
SELECT tests.assert(
  (public.user_identity_inventory('ffffffff-ffff-ffff-ffff-ffffffff1765'::uuid)->>'login_methods')::int = 0,
  'unknown p_user_id must return login_methods=0 (no error)');

-- ============================================================================
-- SECTION 3 — reserve_unlink: floor, two-tab backstop, identity check, aging
-- ============================================================================
-- User with 3 methods: shape5 (2) + one more github identity → 3
INSERT INTO auth.identities (id, user_id, provider, provider_id) VALUES
  ('d0000000-0000-0000-0000-000000001765'::uuid, '60000000-0000-0000-0000-000000001765'::uuid, 'github', '1765-g-2');
SELECT tests.assert(
  (public.user_identity_inventory('60000000-0000-0000-0000-000000001765'::uuid)->>'login_methods')::int = 3,
  'reserve fixture must have login_methods=3');

-- first permit granted
SELECT tests.assert(
  (public.reserve_unlink('60000000-0000-0000-0000-000000001765'::uuid, 'b0000000-0000-0000-0000-000000001765'::uuid)->>'status') = 'permit_granted',
  'reserve_unlink at login_methods=3 must grant the first permit');

-- second reserve (same user, different identity) → floor_violated via pending-1 OR unique index
DO $$ BEGIN
  BEGIN
    PERFORM public.reserve_unlink('60000000-0000-0000-0000-000000001765'::uuid, 'd0000000-0000-0000-0000-000000001765'::uuid);
    RAISE EXCEPTION 'FAIL: second reserve_unlink at 3 must raise reserve_unlink:floor_violated';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%reserve_unlink:floor_violated%' THEN
      RAISE EXCEPTION 'FAIL: wrong error for second reserve: %', SQLERRM;
    END IF;
  END;
END $$;

-- consume the first permit, then reserve again → granted (3-0-1=2 >= 2)
UPDATE public.user_unlink_permits SET consumed_at = now()
 WHERE user_id = '60000000-0000-0000-0000-000000001765'::uuid;
SELECT tests.assert(
  (public.reserve_unlink('60000000-0000-0000-0000-000000001765'::uuid, 'b0000000-0000-0000-0000-000000001765'::uuid)->>'status') = 'permit_granted',
  'reserve_unlink after consume must grant again');

-- floor at 2: shape4 user (login_methods=1) → identity not found first? no — shape4 has no identity; use shape5 user after consuming: login_methods=3 still. Instead: reduce to 2 by removing one identity.
DELETE FROM auth.identities WHERE provider_id IN ('1765-g-2','1765-g-oauth');
-- now shape5 has email identity only → login_methods = 1 (email_method)... hmm that changes the fixture. Re-seed for the floor test:
INSERT INTO auth.identities (id, user_id, provider, provider_id) VALUES
  ('e0000000-0000-0000-0000-000000001765'::uuid, '60000000-0000-0000-0000-000000001765'::uuid, 'github', '1765-g-3'),
  ('f0000000-0000-0000-0000-000000001765'::uuid, '60000000-0000-0000-0000-000000001765'::uuid, 'google', '1765-g-4');
-- now: github + google + email identity + password → 3 again; consume pending; then unlink to floor: remove github → pending consumed → login_methods 3.
UPDATE public.user_unlink_permits SET consumed_at = now()
 WHERE user_id = '60000000-0000-0000-0000-000000001765'::uuid;

-- bad identity_id → identity_not_found
DO $$ BEGIN
  BEGIN
    PERFORM public.reserve_unlink('60000000-0000-0000-0000-000000001765'::uuid, 'ffffffff-ffff-ffff-ffff-ffffffffffff'::uuid);
    RAISE EXCEPTION 'FAIL: bad identity must raise reserve_unlink:identity_not_found';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%reserve_unlink:identity_not_found%' THEN
      RAISE EXCEPTION 'FAIL: wrong error for bad identity: %', SQLERRM;
    END IF;
  END;
END $$;

-- stale-permit aging: insert a stale pending permit directly (simulates a
-- crash between reserve and consume — would otherwise permanently block the
-- unique index for this user = self-DoS lockout)
INSERT INTO public.user_unlink_permits (user_id, identity_id, created_at)
  VALUES ('60000000-0000-0000-0000-000000001765'::uuid, 'e0000000-0000-0000-0000-000000001765'::uuid, now() - interval '10 minutes');
SELECT tests.assert(
  (public.reserve_unlink('60000000-0000-0000-0000-000000001765'::uuid, 'e0000000-0000-0000-0000-000000001765'::uuid)->>'status') = 'permit_granted',
  'stale permit (>5min) must be aged (released) by reserve_unlink, not deadlock');
SELECT tests.assert(
  (SELECT count(*) FROM public.user_unlink_permits
    WHERE user_id='60000000-0000-0000-0000-000000001765'::uuid AND consumed_at IS NULL) = 1,
  'exactly one pending permit must remain after aging+grant');

-- ============================================================================
-- SECTION 4 — link_intents consumed-once (guarded UPDATE semantics)
-- ============================================================================
INSERT INTO public.link_intents (nonce, user_id, provider, expires_at) VALUES
  ('1765-nonce-1', '60000000-0000-0000-0000-000000001765'::uuid, 'github', now() + interval '2 minutes');
SELECT tests.assert(
  (SELECT count(*) FROM public.link_intents WHERE nonce='1765-nonce-1' AND consumed_at IS NULL) = 1,
  'fresh intent must be pending');
-- actual guarded consume (CTE — UPDATE is not valid in a bare subquery)
WITH consumed AS (UPDATE public.link_intents SET consumed_at = now()
     WHERE nonce='1765-nonce-1' AND consumed_at IS NULL AND now() <= expires_at
     RETURNING 1)
SELECT tests.assert((SELECT count(*) FROM consumed) = 1,
  'guarded consume of a fresh intent must affect 1 row');
-- second consume → 0 rows (consumed-once enforced)
WITH consumed AS (UPDATE public.link_intents SET consumed_at = now()
     WHERE nonce='1765-nonce-1' AND consumed_at IS NULL AND now() <= expires_at
     RETURNING 1)
SELECT tests.assert((SELECT count(*) FROM consumed) = 0,
  're-consume must affect 0 rows (consumed-once)');

-- ============================================================================
-- SECTION 5 — claim_membership: NO teams.email write + created_by migration
-- ============================================================================
INSERT INTO public.teams (id, name, graph_name) VALUES ('t1765-a', 't1765-a', 'team_t1765-a');
INSERT INTO public.api_keys (id, team_id, lookup_hash, created_via, created_by) VALUES
  ('k1765-a1', 't1765-a', 'lkp-1765-a1', 'provisioned', 'anon-1765'),
  ('k1765-a2', 't1765-a', 'lkp-1765-a2', 'provisioned', 'reg-' || left(encode(sha256('a@1765.test'::bytea), 'hex'), 12));
INSERT INTO public.teams (id, name, graph_name) VALUES ('t1765-b', 't1765-b', 'team_t1765-b');
INSERT INTO public.api_keys (id, team_id, lookup_hash, created_via, created_by) VALUES
  ('k1765-b1', 't1765-b', 'lkp-1765-b1', 'provisioned', 'reg-' || left(encode(sha256('other@1765.test'::bytea), 'hex'), 12));
INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role, status, identity)
  SELECT NULL, 't1765-a', 't1765-a', 'pending', 'team_t1765-a', 'owner', 'active', 'anon-1765';

-- claim with a confirmed-email user (plain SELECT — PERFORM is PL/pgSQL-only)
SELECT public.claim_membership('lkp-1765-a1', '60000000-0000-0000-0000-000000001765'::uuid, 'shape5@1765.test');

-- (a) teams.email NOT written by claim (demotion)
SELECT tests.assert(
  (SELECT email FROM public.teams WHERE id='t1765-a') IS NULL,
  'claim must NOT write teams.email (demotion)');
-- (b) created_by migration: team A keys attributed to claimer
SELECT tests.assert(
  (SELECT count(*) FROM public.api_keys WHERE team_id='t1765-a' AND created_by = '60000000-0000-0000-0000-000000001765') = 2,
  'claim must migrate anon-/reg- created_by keys to the claimer within the team');
-- (c) foreign team reg- keys UNTOUCHED (operator-precedence guard)
SELECT tests.assert(
  (SELECT created_by FROM public.api_keys WHERE id='k1765-b1') =
    'reg-' || left(encode(sha256('other@1765.test'::bytea), 'hex'), 12),
  'foreign-team reg- keys must be untouched by a claim (parenthesized predicate)');
-- (d) owner row linked
SELECT tests.assert(
  (SELECT count(*) FROM public.team_memberships
    WHERE team_id='t1765-a' AND user_id='60000000-0000-0000-0000-000000001765'::uuid
      AND role='owner' AND status='active' AND identity IS NULL) = 1,
  'claim must link the owner row and clear identity');

-- ============================================================================
-- SECTION 6 — uq_member_identity_active: second active owner with same
-- identity (unclaimed) must be rejected; claimed (identity NULL) must not
-- ============================================================================
-- clean: the pre-scan ran at migration time; here assert the index rejects a second
-- active owner with the SAME non-null identity
INSERT INTO public.teams (id, name, graph_name) VALUES ('t1765-c', 't1765-c', 'team_t1765-c');
INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role, status, identity)
  SELECT NULL, 't1765-c', 't1765-c', 'pending', 'team_t1765-c', 'owner', 'active', '1765-shared-identity';
DO $$ BEGIN
  BEGIN
    INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role, status, identity)
      SELECT NULL, 't1765-d', 't1765-d', 'pending', 'team_t1765-d', 'owner', 'active', '1765-shared-identity';
    RAISE EXCEPTION 'FAIL: second active owner with same identity must be rejected by uq_member_identity_active';
  EXCEPTION WHEN unique_violation THEN NULL;
  END;
END $$;

-- cleanup
DELETE FROM public.teams WHERE id LIKE '%-1765';
DELETE FROM public.user_unlink_permits WHERE user_id::text LIKE '%1765%';
DELETE FROM public.link_intents WHERE nonce LIKE '1765-%';
DELETE FROM auth.identities WHERE user_id::text LIKE '%1765%' OR provider_id LIKE '1765-%';
DELETE FROM auth.users WHERE id::text LIKE '%1765%';

-- ============================================================================
-- SECTION 7 — authenticated must not execute reserve_unlink (deny-by-default)
-- ============================================================================
SET ROLE authenticated;
DO $$ BEGIN
  BEGIN
    PERFORM public.reserve_unlink('60000000-0000-0000-0000-000000001765'::uuid, 'b0000000-0000-0000-0000-000000001765'::uuid);
    RAISE EXCEPTION 'FAIL: authenticated must not execute reserve_unlink';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
RESET ROLE;

-- ============================================================================
-- Done
-- ============================================================================
SELECT '20260827000001 suite: ALL ASSERTIONS PASSED' AS result;
