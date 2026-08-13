-- ============================================================================
-- SQL-level verification for migration 0010 (issue #770 — plan v2 Task 2)
-- Provisioning rewrite: atomic provision_team RPC (teams + team_memberships +
-- api_keys in ONE transaction), handle_new_user placeholder reconciliation,
-- reveal_api_key lookup_hash retention, update_user_team removal, and the
-- agent-signup identity path (NULL user_id + identity).
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--   (applies migrations 0001–0010 + runs this file with ON_ERROR_STOP
--   semantics; the #769 suite runs first in the same harness)
--
-- Every assertion RAISEs on failure; with ON_ERROR_STOP=1 any failure exits
-- non-zero. Test rows use the "-770" suffix for safe cleanup.
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
DELETE FROM public.api_keys WHERE team_id LIKE '%-770';
DELETE FROM public.team_memberships WHERE team_id LIKE '%-770' OR identity LIKE '%770%';
DELETE FROM public.teams WHERE id LIKE '%-770';
DELETE FROM auth.users WHERE email LIKE '%770test%';

-- Fixture users (each INSERT fires handle_new_user → placeholder row)
INSERT INTO auth.users (instance_id, id, aud, role, email, encrypted_password,
                        email_confirmed_at, raw_app_meta_data, raw_user_meta_data)
VALUES ('00000000-0000-0000-0000-000000000000'::uuid,
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid,
        'authenticated', 'authenticated', 'user-a-770test@example.com', '',
        now(), '{}'::jsonb, '{}'::jsonb),
       ('00000000-0000-0000-0000-000000000000'::uuid,
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2'::uuid,
        'authenticated', 'authenticated', 'user-b-770test@example.com', '',
        now(), '{}'::jsonb, '{}'::jsonb),
       ('00000000-0000-0000-0000-000000000000'::uuid,
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3'::uuid,
        'authenticated', 'authenticated', 'user-c-770test@example.com', '',
        now(), '{}'::jsonb, '{}'::jsonb),
       ('00000000-0000-0000-0000-000000000000'::uuid,
        'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa4'::uuid,
        'authenticated', 'authenticated', 'user-d-770test@example.com', '',
        now(), '{}'::jsonb, '{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

-- ============================================================================
-- SECTION 1 — catalog state (0010 applied cleanly)
-- ============================================================================
DO $$ BEGIN
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_proc WHERE proname='provision_team' AND pronamespace='public'::regnamespace),
    '0010: provision_team RPC must exist');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_proc WHERE proname='reveal_api_key' AND pronamespace='public'::regnamespace),
    '0010: reveal_api_key RPC must exist (rebuilt)');
  PERFORM tests.assert(
    NOT EXISTS (SELECT 1 FROM pg_proc WHERE proname='update_user_team' AND pronamespace='public'::regnamespace),
    '0010: update_user_team must be REMOVED (subsumed by provision_team)');
  -- the trigger function was rebuilt with the placeholder guard
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_proc WHERE proname='handle_new_user' AND pronamespace='public'::regnamespace),
    '0010: handle_new_user trigger fn must exist (rebuilt)');
  -- partial unique index for the identity path (idempotent anon re-provision)
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='team_memberships'
            AND indexname='uq_member_identity_team'),
    '0010: partial unique (identity, team_id) WHERE user_id IS NULL');
END $$;

-- ============================================================================
-- SECTION 2 — user path: trigger placeholder reconciled by provision_team
-- (user-a: trigger inserted the placeholder row at auth.users INSERT above)
-- ============================================================================
DO $$ BEGIN
  -- precondition: the trigger DID create exactly one placeholder
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid AND team_id='') = 1,
    'trigger: placeholder row must exist after auth.users INSERT');
END $$;

SELECT public.provision_team(
  p_user_id     => 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid,
  p_identity    => NULL,
  p_team_id     => 'team-a-770',
  p_team_name   => 'Team A 770',
  p_api_key     => 'tt_plaintext_a_770',
  p_key_hash    => 'salt:hash-a-770',
  p_lookup_hash => 'lkp-a-770',
  p_graph_name  => 'team_team-a-770',
  p_email       => 'user-a-770test@example.com',
  p_key_prefix  => 'tt_plain'
);

DO $$ BEGIN
  -- exactly one teams row
  PERFORM tests.assert(
    (SELECT count(*) FROM public.teams WHERE id='team-a-770') = 1,
    'provision_team: exactly one teams row');
  PERFORM tests.assert(
    (SELECT name FROM public.teams WHERE id='team-a-770') = 'Team A 770',
    'provision_team: team name written');
  PERFORM tests.assert(
    (SELECT graph_name FROM public.teams WHERE id='team-a-770') = 'team_team-a-770',
    'provision_team: teams.graph_name = team_{team_id}');
  PERFORM tests.assert(
    (SELECT tier FROM public.teams WHERE id='team-a-770') = 'free',
    'provision_team: tier free default');
  PERFORM tests.assert(
    (SELECT max_users FROM public.teams WHERE id='team-a-770') = 1,
    'provision_team: free-tier max_users');
  PERFORM tests.assert(
    (SELECT max_graphs FROM public.teams WHERE id='team-a-770') = 1,
    'provision_team: free-tier max_graphs');
  PERFORM tests.assert(
    (SELECT ops_allowance FROM public.teams WHERE id='team-a-770') = 10000,
    'provision_team: free-tier ops_allowance');
  PERFORM tests.assert(
    (SELECT graph_size_cap FROM public.teams WHERE id='team-a-770') = 10000,
    'provision_team: free-tier graph_size_cap');
  PERFORM tests.assert(
    (SELECT email FROM public.teams WHERE id='team-a-770') = 'user-a-770test@example.com',
    'provision_team: team email written');

  -- exactly one membership row: the PLACEHOLDER was reconciled in place
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 1,
    'provision_team: exactly one membership row per user (no phantom)');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid AND team_id='') = 0,
    'provision_team: placeholder row reconciled (none left with team_id='''')');
  PERFORM tests.assert(
    (SELECT team_id FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'team-a-770',
    'provision_team: placeholder flipped to real team_id');
  PERFORM tests.assert(
    (SELECT api_key FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'tt_plaintext_a_770',
    'provision_team: plaintext api_key stored for one-time reveal');
  PERFORM tests.assert(
    (SELECT key_hash FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'salt:hash-a-770',
    'provision_team: salted key_hash stored (continuity)');
  PERFORM tests.assert(
    (SELECT lookup_hash FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'lkp-a-770',
    'provision_team: lookup_hash stored on membership');
  PERFORM tests.assert(
    (SELECT status FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'active'
    AND (SELECT role FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'owner',
    'provision_team: membership active + owner');

  -- exactly one api_keys row with the lookup_hash (E2E-1 contract)
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE team_id='team-a-770') = 1,
    'provision_team: exactly one api_keys row');
  PERFORM tests.assert(
    (SELECT lookup_hash FROM public.api_keys WHERE team_id='team-a-770') = 'lkp-a-770',
    'provision_team: api_keys.lookup_hash written');
  PERFORM tests.assert(
    (SELECT created_via FROM public.api_keys WHERE team_id='team-a-770') = 'provisioned',
    'provision_team: api_keys.created_via = provisioned');
  PERFORM tests.assert(
    (SELECT created_by FROM public.api_keys WHERE team_id='team-a-770') = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1',
    'provision_team: api_keys.created_by = user_id');
  PERFORM tests.assert(
    (SELECT key_prefix FROM public.api_keys WHERE team_id='team-a-770') = 'tt_plain',
    'provision_team: api_keys.key_prefix written');
END $$;

-- ============================================================================
-- SECTION 3 — idempotency: re-invocation yields exactly one row everywhere
-- ============================================================================
SELECT public.provision_team(
  p_user_id     => 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid,
  p_identity    => NULL,
  p_team_id     => 'team-a-770',
  p_team_name   => 'Team A 770',
  p_api_key     => 'tt_plaintext_a_770',
  p_key_hash    => 'salt:hash-a-770',
  p_lookup_hash => 'lkp-a-770',
  p_graph_name  => 'team_team-a-770'
);

-- Simulated race: a stale placeholder reappears AFTER provisioning (what the
-- pre-0010 trigger could do if it fired after the RPC). provision_team must
-- reconcile it again — still exactly one membership row.
INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role)
VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid, '', 'provisioning...', 'pending', '', 'owner');

SELECT public.provision_team(
  p_user_id     => 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid,
  p_identity    => NULL,
  p_team_id     => 'team-a-770',
  p_team_name   => 'Team A 770',
  p_api_key     => 'tt_plaintext_a_770',
  p_key_hash    => 'salt:hash-a-770',
  p_lookup_hash => 'lkp-a-770',
  p_graph_name  => 'team_team-a-770'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.teams WHERE id='team-a-770') = 1,
    'idempotent: exactly one teams row after re-invocation');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 1,
    'idempotent: exactly one membership row (stale placeholder reconciled)');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE team_id='team-a-770') = 1,
    'idempotent: exactly one api_keys row after re-invocation');
END $$;

-- ============================================================================
-- SECTION 4 — no-placeholder path (RPC ran before the trigger could)
-- user-b: delete the trigger placeholder, then provision → INSERT path
-- ============================================================================
DELETE FROM public.team_memberships
WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2'::uuid AND team_id='';

SELECT public.provision_team(
  p_user_id     => 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2'::uuid,
  p_identity    => NULL,
  p_team_id     => 'team-b-770',
  p_team_name   => 'Team B 770',
  p_api_key     => 'tt_plaintext_b_770',
  p_key_hash    => 'salt:hash-b-770',
  p_lookup_hash => 'lkp-b-770',
  p_graph_name  => 'team_team-b-770'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2'::uuid) = 1,
    'no-placeholder path: exactly one membership row');
  PERFORM tests.assert(
    (SELECT team_id FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2'::uuid) = 'team-b-770',
    'no-placeholder path: real team_id row');
  -- NOTE: the user-path race (RPC running BEFORE the trigger) is structurally
  -- impossible — team_memberships.user_id FK → auth.users(id) means the
  -- auth.users INSERT (and its trigger) always precedes any membership write.
  -- The amended trigger's NOT EXISTS guard (0010) is belt-and-braces for that
  -- ordering; the stale-placeholder reconciliation is proven in SECTION 3.
END $$;

-- ============================================================================
-- SECTION 5 — identity path (agent signups: NULL user_id + identity)
-- ============================================================================
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'agent:anon-770-1',
  p_team_id     => 'team-c-770',
  p_team_name   => 'Agent C 770',
  p_api_key     => 'tt_plaintext_c_770',
  p_key_hash    => 'salt:hash-c-770',
  p_lookup_hash => 'lkp-c-770',
  p_graph_name  => 'team_team-c-770'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships WHERE team_id='team-c-770' AND user_id IS NULL AND identity='agent:anon-770-1') = 1,
    'identity path: membership row with NULL user_id + identity');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.teams WHERE id='team-c-770') = 1,
    'identity path: exactly one teams row');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE team_id='team-c-770') = 1,
    'identity path: exactly one api_keys row');
  PERFORM tests.assert(
    (SELECT created_by FROM public.api_keys WHERE team_id='team-c-770') = 'agent:anon-770-1',
    'identity path: api_keys.created_by = identity');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-c-770' AND user_id IS NULL AND identity='agent:anon-770-1') = 1,
    'identity path: chk_member_or_invite admits NULL user_id + identity');
END $$;

-- idempotent re-invocation of the identity path (partial unique index)
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'agent:anon-770-1',
  p_team_id     => 'team-c-770',
  p_team_name   => 'Agent C 770',
  p_api_key     => 'tt_plaintext_c_770',
  p_key_hash    => 'salt:hash-c-770',
  p_lookup_hash => 'lkp-c-770',
  p_graph_name  => 'team_team-c-770'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships WHERE team_id='team-c-770') = 1,
    'identity path idempotent: exactly one membership row');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.teams WHERE id='team-c-770') = 1,
    'identity path idempotent: exactly one teams row');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE team_id='team-c-770') = 1,
    'identity path idempotent: exactly one api_keys row');
END $$;

-- rotation refresh (migration-review WARNING fix, PR #847): re-provisioning
-- an identity with a NEW key must refresh the membership row in place — the
-- membership's key_hash/lookup_hash follow the rotated key (symmetric with
-- the user path step-1 refresh; api_keys accumulates both rows, and the
-- api_keys row remains the canonical auth anchor).
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'agent:anon-770-1',
  p_team_id     => 'team-c-770',
  p_team_name   => 'Agent C 770',
  p_api_key     => 'tt_plaintext_c_770_rotated',
  p_key_hash    => 'salt:hash-c-770-rotated',
  p_lookup_hash => 'lkp-c-770-rotated',
  p_graph_name  => 'team_team-c-770'
);
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships WHERE team_id='team-c-770') = 1,
    'identity rotation: still exactly one membership row');
  PERFORM tests.assert(
    (SELECT lookup_hash FROM public.team_memberships
      WHERE team_id='team-c-770' AND user_id IS NULL AND identity='agent:anon-770-1')
      = 'lkp-c-770-rotated',
    'identity rotation: membership lookup_hash follows the rotated key');
  PERFORM tests.assert(
    (SELECT key_hash FROM public.team_memberships
      WHERE team_id='team-c-770' AND user_id IS NULL AND identity='agent:anon-770-1')
      = 'salt:hash-c-770-rotated',
    'identity rotation: membership key_hash follows the rotated key');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE team_id='team-c-770') = 2,
    'identity rotation: api_keys accumulates the rotated key row');
END $$;

-- the owner ≤1 invariant (20260813000003 uq_member_owner — P3-FIX-P): a
-- SECOND identity attempting owner co-provision on an already-owned team is
-- REJECTED (anon teams are single-owner — the claim path needs exactly one
-- NULL-user owner row to attach a verified identifier to). M:N membership
-- for distinct identities remains valid via invitations/member role, but
-- co-OWNERSHIP is now structurally impossible (the old "two identities may
-- co-provision one team" scenario predates the invariant and is invalid).
DO $$ BEGIN
  BEGIN
    PERFORM public.provision_team(
      p_user_id     => NULL,
      p_identity    => 'agent:anon-770-2',
      p_team_id     => 'team-c-770',
      p_team_name   => 'Agent C 770',
      p_api_key     => 'tt_plaintext_c2_770',
      p_key_hash    => 'salt:hash-c2-770',
      p_lookup_hash => 'lkp-c2-770',
      p_graph_name  => 'team_team-c-770'
    );
    RAISE EXCEPTION 'FAIL: second owner co-provision must be rejected (uq_member_owner)';
  EXCEPTION WHEN unique_violation THEN NULL; END;
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships WHERE team_id='team-c-770') = 1,
    'owner≤1: second owner co-provision rejected — exactly one membership row');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE team_id='team-c-770') = 2,
    'owner≤1: rejected co-provision leaves the api_keys rows untouched');
END $$;

-- guard: exactly one of p_user_id / p_identity is required
DO $$ BEGIN
  BEGIN
    PERFORM public.provision_team(
      p_user_id => NULL, p_identity => NULL,
      p_team_id => 'team-x-770', p_team_name => 'X', p_api_key => 'k',
      p_key_hash => 'h', p_lookup_hash => 'l', p_graph_name => 'g');
    RAISE EXCEPTION 'FAIL: provision_team must reject all-NULL anchors';
  EXCEPTION WHEN others THEN NULL; END;
END $$;

-- ============================================================================
-- SECTION 6 — reveal_api_key: shown once, nulled, lookup_hash retained
-- ============================================================================
SET request.jwt.claim.sub = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1';

DO $$ BEGIN
  PERFORM tests.assert(
    public.reveal_api_key('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid, 'team-a-770') = 'tt_plaintext_a_770',
    'reveal: returns plaintext once to the row owner');
  PERFORM tests.assert(
    public.reveal_api_key('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid, 'team-a-770') IS NULL,
    'reveal: second call returns NULL (key shown once)');
  PERFORM tests.assert(
    (SELECT api_key FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) IS NULL,
    'reveal: api_key nulled atomically');
  PERFORM tests.assert(
    (SELECT lookup_hash FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'lkp-a-770',
    'reveal: lookup_hash RETAINED on the nulled row (E2E-6 auth path)');
  PERFORM tests.assert(
    (SELECT key_hash FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'salt:hash-a-770',
    'reveal: key_hash retained');
END $$;

-- wrong owner → NULL
SET request.jwt.claim.sub = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2';
DO $$ BEGIN
  PERFORM tests.assert(
    public.reveal_api_key('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2'::uuid, 'team-a-770') IS NULL,
    'reveal: a user may not reveal another user''s team key (team mismatch)');
  PERFORM tests.assert(
    public.reveal_api_key('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid, 'team-a-770') IS NULL,
    'reveal: spoofing another user_id is denied (auth.uid() guard)');
END $$;
RESET request.jwt.claim.sub;

-- identity-path rows are NOT welcome-page revealable (no Supabase session can
-- prove an anon identity; agent keys are delivered once at mint time)
DO $$ BEGIN
  PERFORM tests.assert(
    public.reveal_api_key(NULL, 'team-c-770') IS NULL,
    'reveal: identity-path row (NULL user_id) not revealable');
END $$;

-- fail-closed: a membership with NO lookup_hash must not be nulled
-- (nulling the only credential without a lookup anchor = permanent lockout)
INSERT INTO public.team_memberships (user_id, team_id, team_name, api_key, key_hash, graph_name, role, status, lookup_hash)
VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3'::uuid, 'team-d-770', 'Team D 770',
        'tt_legacy_no_lkp_770', 'salt:hash-d-770', 'team_team-d-770', 'owner', 'active', NULL);
INSERT INTO public.teams (id, name, graph_name) VALUES ('team-d-770', 'Team D 770', 'team_team-d-770');

SET request.jwt.claim.sub = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3';
DO $$ BEGIN
  PERFORM tests.assert(
    public.reveal_api_key('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3'::uuid, 'team-d-770') IS NULL,
    'reveal fail-closed: missing lookup_hash → NULL');
  PERFORM tests.assert(
    (SELECT api_key FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3'::uuid AND team_id='team-d-770') = 'tt_legacy_no_lkp_770',
    'reveal fail-closed: api_key NOT nulled without a lookup anchor');
END $$;
RESET request.jwt.claim.sub;

-- ============================================================================
-- SECTION 7 — RLS + grants (defense in depth)
-- ============================================================================
-- authenticated may NOT execute provision_team (mints teams/keys — the
-- Edge Function alone may, via service_role after #802 caller auth)
SET ROLE authenticated;
DO $$ BEGIN
  BEGIN
    PERFORM public.provision_team(
      p_user_id => 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa4'::uuid, p_identity => NULL,
      p_team_id => 'team-e-770', p_team_name => 'E', p_api_key => 'k',
      p_key_hash => 'h', p_lookup_hash => 'l', p_graph_name => 'g');
    RAISE EXCEPTION 'FAIL: authenticated must not execute provision_team';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;

-- GUC tenant scoping still denies cross-team reads (teams + api_keys)
SET app.current_team_id = 'team-a-770';
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.teams WHERE id='team-a-770') = 1,
    'RLS: authenticated (GUC=team-a) reads own team');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.teams WHERE id='team-b-770') = 0,
    'RLS: cross-team teams read denied');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE team_id='team-b-770') = 0,
    'RLS: cross-team api_keys read denied');
END $$;

-- membership rows: the 0003 auth.uid() policy still gates reads, and the
-- api_key column stays unreadable even through the row-owner policy
SET request.jwt.claim.sub = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1';
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 1,
    'RLS: owner reads own membership row');
  BEGIN
    PERFORM api_key FROM public.team_memberships LIMIT 1;
    RAISE EXCEPTION 'FAIL: authenticated must not read api_key column';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
RESET request.jwt.claim.sub;
RESET app.current_team_id;
RESET ROLE;

-- ============================================================================
-- SECTION 8 — cleanup (audit rows exempt; append-only)
-- ============================================================================
DELETE FROM public.api_keys WHERE team_id LIKE '%-770';
DELETE FROM public.team_memberships WHERE team_id LIKE '%-770' OR identity LIKE '%770%';
DELETE FROM public.teams WHERE id LIKE '%-770';
DELETE FROM auth.users WHERE email LIKE '%770test%';

DO $$ BEGIN
  PERFORM tests.assert(
    NOT EXISTS (SELECT 1 FROM public.teams WHERE id LIKE '%-770'),
    'cleanup: no fixture teams left');
  PERFORM tests.assert(
    NOT EXISTS (SELECT 1 FROM public.team_memberships WHERE team_id LIKE '%-770'),
    'cleanup: no fixture memberships left');
END $$;
