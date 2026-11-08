-- ============================================================================
-- SQL-level verification for migration 0010 (issue #770 — plan v2 Task 2)
-- Provisioning rewrite: atomic provision_team RPC (organizations + org_memberships +
-- api_keys in ONE transaction), handle_new_user placeholder reconciliation,
-- reveal_api_key lookup_hash retention, update_user_team removal, and the
-- agent-signup identity path (NULL user_id + identity). Section 5b adds the
-- #1716 keyless sections (all-NULL key params → NO api_keys row; partial key
-- set rejected) — they run against the post-20260825214233 RPC (the harness
-- applies every migration before the suites).
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
DELETE FROM public.api_keys WHERE org_id LIKE '%-770';
DELETE FROM public.org_memberships WHERE org_id LIKE '%-770' OR identity LIKE '%770%';
DELETE FROM public.organizations WHERE id LIKE '%-770';
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
    EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='org_memberships'
            AND indexname='uq_member_identity_org'),
    '0010: partial unique (identity, org_id) WHERE user_id IS NULL');
END $$;

-- ============================================================================
-- SECTION 2 — user path: trigger placeholder reconciled by provision_team
-- (user-a: trigger inserted the placeholder row at auth.users INSERT above)
-- ============================================================================
DO $$ BEGIN
  -- precondition: the trigger DID create exactly one placeholder
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid AND org_id='') = 1,
    'trigger: placeholder row must exist after auth.users INSERT');
END $$;

SELECT public.provision_team(
  p_user_id     => 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid,
  p_identity    => NULL,
  p_org_id     => 'org-a-770',
  p_org_name   => 'Org A 770',
  p_api_key     => 'tt_plaintext_a_770',
  p_key_hash    => 'salt:hash-a-770',
  p_lookup_hash => 'lkp-a-770',
  p_graph_name  => 'org_org-a-770',
  p_email       => 'user-a-770test@example.com',
  p_key_prefix  => 'tt_plain'
);

DO $$ BEGIN
  -- exactly one organizations row
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations WHERE id='org-a-770') = 1,
    'provision_team: exactly one organizations row');
  PERFORM tests.assert(
    (SELECT name FROM public.organizations WHERE id='org-a-770') = 'Org A 770',
    'provision_team: org name written');
  PERFORM tests.assert(
    (SELECT graph_name FROM public.organizations WHERE id='org-a-770') = 'org_org-a-770',
    'provision_team: organizations.graph_name = org_{org_id}');
  PERFORM tests.assert(
    (SELECT tier FROM public.organizations WHERE id='org-a-770') = 'free',
    'provision_team: tier free default');
  PERFORM tests.assert(
    (SELECT max_users FROM public.organizations WHERE id='org-a-770') = 1,
    'provision_team: free-tier max_users');
  PERFORM tests.assert(
    (SELECT max_graphs FROM public.organizations WHERE id='org-a-770') = 1,
    'provision_team: free-tier max_graphs');
  PERFORM tests.assert(
    (SELECT ops_allowance FROM public.organizations WHERE id='org-a-770') = 10000,
    'provision_team: free-tier ops_allowance');
  PERFORM tests.assert(
    (SELECT graph_size_cap FROM public.organizations WHERE id='org-a-770') = 10000,
    'provision_team: free-tier graph_size_cap');
  PERFORM tests.assert(
    (SELECT email FROM public.organizations WHERE id='org-a-770') = 'user-a-770test@example.com',
    'provision_team: org email written');

  -- exactly one membership row: the PLACEHOLDER was reconciled in place
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 1,
    'provision_team: exactly one membership row per user (no phantom)');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid AND org_id='') = 0,
    'provision_team: placeholder row reconciled (none left with org_id='''')');
  PERFORM tests.assert(
    (SELECT org_id FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'org-a-770',
    'provision_team: placeholder flipped to real org_id');
  PERFORM tests.assert(
    (SELECT api_key FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'tt_plaintext_a_770',
    'provision_team: plaintext api_key stored for one-time reveal');
  PERFORM tests.assert(
    (SELECT key_hash FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'salt:hash-a-770',
    'provision_team: salted key_hash stored (continuity)');
  PERFORM tests.assert(
    (SELECT lookup_hash FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'lkp-a-770',
    'provision_team: lookup_hash stored on membership');
  PERFORM tests.assert(
    (SELECT status FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'active'
    AND (SELECT role FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'owner',
    'provision_team: membership active + owner');

  -- exactly one api_keys row with the lookup_hash (E2E-1 contract)
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE org_id='org-a-770') = 1,
    'provision_team: exactly one api_keys row');
  PERFORM tests.assert(
    (SELECT lookup_hash FROM public.api_keys WHERE org_id='org-a-770') = 'lkp-a-770',
    'provision_team: api_keys.lookup_hash written');
  PERFORM tests.assert(
    (SELECT created_via FROM public.api_keys WHERE org_id='org-a-770') = 'provisioned',
    'provision_team: api_keys.created_via = provisioned');
  PERFORM tests.assert(
    (SELECT created_by FROM public.api_keys WHERE org_id='org-a-770') = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1',
    'provision_team: api_keys.created_by = user_id');
  PERFORM tests.assert(
    (SELECT key_prefix FROM public.api_keys WHERE org_id='org-a-770') = 'tt_plain',
    'provision_team: api_keys.key_prefix written');
END $$;

-- ============================================================================
-- SECTION 3 — idempotency: re-invocation yields exactly one row everywhere
-- ============================================================================
SELECT public.provision_team(
  p_user_id     => 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid,
  p_identity    => NULL,
  p_org_id     => 'org-a-770',
  p_org_name   => 'Org A 770',
  p_api_key     => 'tt_plaintext_a_770',
  p_key_hash    => 'salt:hash-a-770',
  p_lookup_hash => 'lkp-a-770',
  p_graph_name  => 'org_org-a-770'
);

-- Simulated race: a stale placeholder reappears AFTER provisioning (what the
-- pre-0010 trigger could do if it fired after the RPC). provision_team must
-- reconcile it again — still exactly one membership row.
INSERT INTO public.org_memberships (user_id, org_id, org_name, key_hash, graph_name, role)
VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid, '', 'provisioning...', 'pending', '', 'owner');

SELECT public.provision_team(
  p_user_id     => 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid,
  p_identity    => NULL,
  p_org_id     => 'org-a-770',
  p_org_name   => 'Org A 770',
  p_api_key     => 'tt_plaintext_a_770',
  p_key_hash    => 'salt:hash-a-770',
  p_lookup_hash => 'lkp-a-770',
  p_graph_name  => 'org_org-a-770'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations WHERE id='org-a-770') = 1,
    'idempotent: exactly one organizations row after re-invocation');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 1,
    'idempotent: exactly one membership row (stale placeholder reconciled)');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE org_id='org-a-770') = 1,
    'idempotent: exactly one api_keys row after re-invocation');
END $$;

-- ============================================================================
-- SECTION 4 — no-placeholder path (RPC ran before the trigger could)
-- user-b: delete the trigger placeholder, then provision → INSERT path
-- ============================================================================
DELETE FROM public.org_memberships
WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2'::uuid AND org_id='';

SELECT public.provision_team(
  p_user_id     => 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2'::uuid,
  p_identity    => NULL,
  p_org_id     => 'org-b-770',
  p_org_name   => 'Org B 770',
  p_api_key     => 'tt_plaintext_b_770',
  p_key_hash    => 'salt:hash-b-770',
  p_lookup_hash => 'lkp-b-770',
  p_graph_name  => 'org_org-b-770'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2'::uuid) = 1,
    'no-placeholder path: exactly one membership row');
  PERFORM tests.assert(
    (SELECT org_id FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2'::uuid) = 'org-b-770',
    'no-placeholder path: real org_id row');
  -- NOTE: the user-path race (RPC running BEFORE the trigger) is structurally
  -- impossible — org_memberships.user_id FK → auth.users(id) means the
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
  p_org_id     => 'org-c-770',
  p_org_name   => 'Agent C 770',
  p_api_key     => 'tt_plaintext_c_770',
  p_key_hash    => 'salt:hash-c-770',
  p_lookup_hash => 'lkp-c-770',
  p_graph_name  => 'org_org-c-770'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships WHERE org_id='org-c-770' AND user_id IS NULL AND identity='agent:anon-770-1') = 1,
    'identity path: membership row with NULL user_id + identity');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations WHERE id='org-c-770') = 1,
    'identity path: exactly one organizations row');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE org_id='org-c-770') = 1,
    'identity path: exactly one api_keys row');
  PERFORM tests.assert(
    (SELECT created_by FROM public.api_keys WHERE org_id='org-c-770') = 'agent:anon-770-1',
    'identity path: api_keys.created_by = identity');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-c-770' AND user_id IS NULL AND identity='agent:anon-770-1') = 1,
    'identity path: chk_member_or_invite admits NULL user_id + identity');
END $$;

-- idempotent re-invocation of the identity path (partial unique index)
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'agent:anon-770-1',
  p_org_id     => 'org-c-770',
  p_org_name   => 'Agent C 770',
  p_api_key     => 'tt_plaintext_c_770',
  p_key_hash    => 'salt:hash-c-770',
  p_lookup_hash => 'lkp-c-770',
  p_graph_name  => 'org_org-c-770'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships WHERE org_id='org-c-770') = 1,
    'identity path idempotent: exactly one membership row');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations WHERE id='org-c-770') = 1,
    'identity path idempotent: exactly one organizations row');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE org_id='org-c-770') = 1,
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
  p_org_id     => 'org-c-770',
  p_org_name   => 'Agent C 770',
  p_api_key     => 'tt_plaintext_c_770_rotated',
  p_key_hash    => 'salt:hash-c-770-rotated',
  p_lookup_hash => 'lkp-c-770-rotated',
  p_graph_name  => 'org_org-c-770'
);
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships WHERE org_id='org-c-770') = 1,
    'identity rotation: still exactly one membership row');
  PERFORM tests.assert(
    (SELECT lookup_hash FROM public.org_memberships
      WHERE org_id='org-c-770' AND user_id IS NULL AND identity='agent:anon-770-1')
      = 'lkp-c-770-rotated',
    'identity rotation: membership lookup_hash follows the rotated key');
  PERFORM tests.assert(
    (SELECT key_hash FROM public.org_memberships
      WHERE org_id='org-c-770' AND user_id IS NULL AND identity='agent:anon-770-1')
      = 'salt:hash-c-770-rotated',
    'identity rotation: membership key_hash follows the rotated key');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE org_id='org-c-770') = 2,
    'identity rotation: api_keys accumulates the rotated key row');
END $$;

-- the owner ≤1 invariant (20260813000004 uq_member_owner — P3-FIX-P): a
-- SECOND identity attempting owner co-provision on an already-owned org is
-- REJECTED (anon organizations are single-owner — the claim path needs exactly one
-- NULL-user owner row to attach a verified identifier to). M:N membership
-- for distinct identities remains valid via invitations/member role, but
-- co-OWNERSHIP is now structurally impossible (the old "two identities may
-- co-provision one org" scenario predates the invariant and is invalid).
DO $$ BEGIN
  BEGIN
    PERFORM public.provision_team(
      p_user_id     => NULL,
      p_identity    => 'agent:anon-770-2',
      p_org_id     => 'org-c-770',
      p_org_name   => 'Agent C 770',
      p_api_key     => 'tt_plaintext_c2_770',
      p_key_hash    => 'salt:hash-c2-770',
      p_lookup_hash => 'lkp-c2-770',
      p_graph_name  => 'org_org-c-770'
    );
    RAISE EXCEPTION 'FAIL: second owner co-provision must be rejected (uq_member_owner)';
  EXCEPTION WHEN unique_violation THEN NULL; END;
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships WHERE org_id='org-c-770') = 1,
    'owner≤1: second owner co-provision rejected — exactly one membership row');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE org_id='org-c-770') = 2,
    'owner≤1: rejected co-provision leaves the api_keys rows untouched');
END $$;

-- guard: exactly one of p_user_id / p_identity is required
DO $$ BEGIN
  BEGIN
    PERFORM public.provision_team(
      p_user_id => NULL, p_identity => NULL,
      p_org_id => 'org-x-770', p_org_name => 'X', p_api_key => 'k',
      p_key_hash => 'h', p_lookup_hash => 'l', p_graph_name => 'g');
    RAISE EXCEPTION 'FAIL: provision_team must reject all-NULL anchors';
  EXCEPTION WHEN others THEN NULL; END;
END $$;

-- ============================================================================
-- SECTION 5b — keyless provisioning (#1716): all-NULL key params
-- ============================================================================
-- POST /v1/onboarding/org provisions a sub-org with NO api_keys row — the
-- old per-call tt_ mint was an unrecoverable dead credential (plaintext never
-- returned, hash-only at rest). Keyless = organizations + membership, zero api_keys;
-- the org stays keyless until a session-key mint (POST /v1/session/key).
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'agent:anon-1716-1',
  p_org_id     => 'org-k-770',
  p_org_name   => 'Org Keyless 770',
  p_api_key     => NULL,
  p_key_hash    => NULL,
  p_lookup_hash => NULL,
  p_graph_name  => 'org_org-k-770'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations WHERE id='org-k-770') = 1,
    'keyless: organizations row created');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-k-770' AND user_id IS NULL
        AND identity='agent:anon-1716-1') = 1,
    'keyless: identity-path membership row created');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE org_id='org-k-770') = 0,
    'keyless: NO api_keys row attributable to the org (#1716)');
END $$;

-- keyless re-invocation stays idempotent and still writes no api_keys row
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'agent:anon-1716-1',
  p_org_id     => 'org-k-770',
  p_org_name   => 'Org Keyless 770',
  p_api_key     => NULL,
  p_key_hash    => NULL,
  p_lookup_hash => NULL,
  p_graph_name  => 'org_org-k-770'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-k-770' AND user_id IS NULL
        AND identity='agent:anon-1716-1') = 1,
    'keyless re-invocation: exactly one membership row');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE org_id='org-k-770') = 0,
    'keyless re-invocation: still no api_keys row');
END $$;

-- PARTIAL key set → rejected (all-or-none guard, #1716). Flag pattern so the
-- test actually fails if provision_team stops raising (the swallow-anything
-- pattern above cannot detect that).
DO $$ DECLARE
  _raised boolean := false;
BEGIN
  BEGIN
    PERFORM public.provision_team(
      p_user_id     => NULL,
      p_identity    => 'agent:anon-1716-2',
      p_org_id     => 'org-p-770',
      p_org_name   => 'Partial 770',
      p_api_key     => 'tt_partial_770',
      p_key_hash    => NULL,
      p_lookup_hash => 'lkp-partial-770',
      p_graph_name  => 'org_org-p-770'
    );
  EXCEPTION WHEN others THEN
    _raised := true;
  END;
  PERFORM tests.assert(_raised,
    'keyless: provision_team must reject a PARTIAL key set (all-or-none)');
  PERFORM tests.assert(
    NOT EXISTS (SELECT 1 FROM public.organizations WHERE id='org-p-770'),
    'keyless: rejected partial provision persisted nothing (subtransaction rollback)');
END $$;

-- ============================================================================
-- SECTION 6 — reveal_api_key: shown once, nulled, lookup_hash retained
-- ============================================================================
SET request.jwt.claim.sub = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1';

DO $$ BEGIN
  PERFORM tests.assert(
    public.reveal_api_key('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid, 'org-a-770') = 'tt_plaintext_a_770',
    'reveal: returns plaintext once to the row owner');
  PERFORM tests.assert(
    public.reveal_api_key('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid, 'org-a-770') IS NULL,
    'reveal: second call returns NULL (key shown once)');
  PERFORM tests.assert(
    (SELECT api_key FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) IS NULL,
    'reveal: api_key nulled atomically');
  PERFORM tests.assert(
    (SELECT lookup_hash FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'lkp-a-770',
    'reveal: lookup_hash RETAINED on the nulled row (E2E-6 auth path)');
  PERFORM tests.assert(
    (SELECT key_hash FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 'salt:hash-a-770',
    'reveal: key_hash retained');
END $$;

-- wrong owner → NULL
SET request.jwt.claim.sub = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2';
DO $$ BEGIN
  PERFORM tests.assert(
    public.reveal_api_key('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2'::uuid, 'org-a-770') IS NULL,
    'reveal: a user may not reveal another user''s org key (org mismatch)');
  PERFORM tests.assert(
    public.reveal_api_key('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid, 'org-a-770') IS NULL,
    'reveal: spoofing another user_id is denied (auth.uid() guard)');
END $$;
RESET request.jwt.claim.sub;

-- identity-path rows are NOT welcome-page revealable (no Supabase session can
-- prove an anon identity; agent keys are delivered once at mint time)
DO $$ BEGIN
  PERFORM tests.assert(
    public.reveal_api_key(NULL, 'org-c-770') IS NULL,
    'reveal: identity-path row (NULL user_id) not revealable');
END $$;

-- fail-closed: a membership with NO lookup_hash must not be nulled
-- (nulling the only credential without a lookup anchor = permanent lockout)
INSERT INTO public.org_memberships (user_id, org_id, org_name, api_key, key_hash, graph_name, role, status, lookup_hash)
VALUES ('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3'::uuid, 'org-d-770', 'Org D 770',
        'tt_legacy_no_lkp_770', 'salt:hash-d-770', 'org_org-d-770', 'owner', 'active', NULL);
INSERT INTO public.organizations (id, name, graph_name) VALUES ('org-d-770', 'Org D 770', 'org_org-d-770');

SET request.jwt.claim.sub = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3';
DO $$ BEGIN
  PERFORM tests.assert(
    public.reveal_api_key('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3'::uuid, 'org-d-770') IS NULL,
    'reveal fail-closed: missing lookup_hash → NULL');
  PERFORM tests.assert(
    (SELECT api_key FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3'::uuid AND org_id='org-d-770') = 'tt_legacy_no_lkp_770',
    'reveal fail-closed: api_key NOT nulled without a lookup anchor');
END $$;
RESET request.jwt.claim.sub;

-- ============================================================================
-- SECTION 7 — RLS + grants (defense in depth)
-- ============================================================================
-- authenticated may NOT execute provision_team (mints organizations/keys — the
-- Edge Function alone may, via service_role after #802 caller auth)
SET ROLE authenticated;
DO $$ BEGIN
  BEGIN
    PERFORM public.provision_team(
      p_user_id => 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa4'::uuid, p_identity => NULL,
      p_org_id => 'org-e-770', p_org_name => 'E', p_api_key => 'k',
      p_key_hash => 'h', p_lookup_hash => 'l', p_graph_name => 'g');
    RAISE EXCEPTION 'FAIL: authenticated must not execute provision_team';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;

-- GUC tenant scoping still denies cross-org reads (organizations + api_keys)
SET app.current_org_id = 'org-a-770';
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations WHERE id='org-a-770') = 1,
    'RLS: authenticated (GUC=org-a) reads own org');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations WHERE id='org-b-770') = 0,
    'RLS: cross-org organizations read denied');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE org_id='org-b-770') = 0,
    'RLS: cross-org api_keys read denied');
END $$;

-- membership rows: the 0003 auth.uid() policy still gates reads, and the
-- api_key column stays unreadable even through the row-owner policy
SET request.jwt.claim.sub = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1';
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE user_id='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1'::uuid) = 1,
    'RLS: owner reads own membership row');
  BEGIN
    PERFORM api_key FROM public.org_memberships LIMIT 1;
    RAISE EXCEPTION 'FAIL: authenticated must not read api_key column';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
RESET request.jwt.claim.sub;
RESET app.current_org_id;
RESET ROLE;

-- ============================================================================
-- SECTION 8 — #1981: the one-free-org entitlement is ATOMIC inside the RPC
-- ============================================================================
-- `provision_team` re-checks the entitlement in its OWN transaction, under a
-- cross-process advisory lock, so a second APP WORKER (or machine) cannot
-- provision a second free org for the same person. What is asserted here is the
-- RPC's DECISION MATRIX and the contract `hosted_api` maps:
--   * free org + an owned free org already exists  -> refused, marker
--     `one_free_org_limit:<blocking org>` (NOT a 500), and NOTHING written;
--   * paid tier (`p_tier <> 'free'`)               -> allowed (the #2789
--     "buy a subscription for a NEW organization" exit — the gap this backstop
--     must never close);
--   * re-provision of the org's OWN id (replay)    -> allowed;
--   * identity path (p_user_id NULL)               -> allowed (no person);
--   * collaborator-only, paid, and pending_payment -> allowed (the predicate
--     mirrors `supabase_control.owned_free_org_ids`, which counts OWNERSHIP of
--     an org with no active paid subscription and excludes pending_payment);
--   * past_due / trialing                          -> refused (they ARE paying).
-- Every fixture id here ends in `-1981` (cleanup at the end of this section,
-- idempotent re-runs).

DELETE FROM public.api_keys WHERE org_id LIKE '%-1981';
DELETE FROM public.org_memberships WHERE org_id LIKE '%-1981' OR identity LIKE '%1981%';
DELETE FROM public.organizations WHERE id LIKE '%-1981';
DELETE FROM auth.users WHERE email LIKE '%1981test%';

INSERT INTO auth.users (instance_id, id, aud, role, email, encrypted_password,
                        email_confirmed_at, raw_app_meta_data, raw_user_meta_data)
VALUES ('00000000-0000-0000-0000-000000000000'::uuid,
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1'::uuid,
        'authenticated', 'authenticated', 'user-e-1981test@example.com', '',
        now(), '{}'::jsonb, '{}'::jsonb),
       ('00000000-0000-0000-0000-000000000000'::uuid,
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2'::uuid,
        'authenticated', 'authenticated', 'user-f-1981test@example.com', '',
        now(), '{}'::jsonb, '{}'::jsonb),
       ('00000000-0000-0000-0000-000000000000'::uuid,
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb3'::uuid,
        'authenticated', 'authenticated', 'user-g-1981test@example.com', '',
        now(), '{}'::jsonb, '{}'::jsonb),
       ('00000000-0000-0000-0000-000000000000'::uuid,
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb4'::uuid,
        'authenticated', 'authenticated', 'user-h-1981test@example.com', '',
        now(), '{}'::jsonb, '{}'::jsonb),
       ('00000000-0000-0000-0000-000000000000'::uuid,
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb5'::uuid,
        'authenticated', 'authenticated', 'user-i-1981test@example.com', '',
        now(), '{}'::jsonb, '{}'::jsonb),
       ('00000000-0000-0000-0000-000000000000'::uuid,
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb6'::uuid,
        'authenticated', 'authenticated', 'user-j-1981test@example.com', '',
        now(), '{}'::jsonb, '{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

-- The guard must still BE in the live body: a later `CREATE OR REPLACE` that
-- re-issues provision_team from an older text would silently drop the backstop
-- and this suite would otherwise stay green.
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT prosrc FROM pg_proc
      WHERE proname='provision_team' AND pronamespace='public'::regnamespace)
      LIKE '%pg_advisory_xact_lock%',
    '#1981: provision_team must keep the cross-process advisory lock');
  PERFORM tests.assert(
    (SELECT prosrc FROM pg_proc
      WHERE proname='provision_team' AND pronamespace='public'::regnamespace)
      LIKE '%one_free_org_limit:%',
    '#1981: provision_team must keep the one_free_org_limit marker');
END $$;

-- ── 8a. a user's FIRST free org provisions; the SECOND is refused ───────────
SELECT public.provision_team(
    p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1'::uuid,
    p_identity := NULL,
    p_org_id   := 'org-e-free-1981', p_org_name := 'e-free-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-e-free-1981');

DO $$
DECLARE
  v_msg   text;
  v_state text;
BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations
      WHERE id='org-e-free-1981') = 1,
    '#1981: the first free org provisions');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-e-free-1981'
        AND user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1'::uuid
        AND role='owner' AND status='active') = 1,
    '#1981: the first free org gets an ACTIVE OWNER membership');

  BEGIN
    PERFORM public.provision_team(
        p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1'::uuid,
        p_identity := NULL,
        p_org_id   := 'org-e-blocked-1981', p_org_name := 'e-blocked-1981',
        p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
        p_graph_name := 'org_org-e-blocked-1981');
    RAISE EXCEPTION 'ASSERTION FAILED: #1981 second free org must be refused';
  EXCEPTION WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_msg = MESSAGE_TEXT, v_state = RETURNED_SQLSTATE;
    IF v_state <> 'PT402' THEN
      RAISE;  -- an unrelated failure must not read as the gate firing
    END IF;
    PERFORM tests.assert(
      v_msg = 'one_free_org_limit:org-e-free-1981',
      '#1981: the refusal must carry the marker + the BLOCKING org id, got: '
      || coalesce(v_msg, '<null>'));
  END;
END $$;

-- atomicity: the refusal wrote NOTHING (the gate runs before the first INSERT)
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations
      WHERE id='org-e-blocked-1981') = 0,
    '#1981: a refused provision must write NO organizations row');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-e-blocked-1981') = 0,
    '#1981: a refused provision must write NO membership row');
END $$;

-- ── 8b. replay of the org's OWN provision stays idempotent ──────────────────
-- The target org id is EXCLUDED from the predicate (`m.org_id <> p_org_id`), so
-- a retry that re-lands an org the user already owns is not mistaken for a
-- second allowance. (Sequenced BEFORE the paid provision below: once a user
-- owns another counted org, even a self-replay is refused — which is exactly
-- what the Python gate does, since it counts owned orgs without knowing the
-- lane's target.)
SELECT public.provision_team(
    p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1'::uuid,
    p_identity := NULL,
    p_org_id   := 'org-e-free-1981', p_org_name := 'e-free-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-e-free-1981');

-- ── 8c. the PAID exit (#2789 design 2) is never gated ───────────────────────
SELECT public.provision_team(
    p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb1'::uuid,
    p_identity := NULL,
    p_org_id   := 'org-e-paid-1981', p_org_name := 'e-paid-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-e-paid-1981',
    p_tier     := 'pro');

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations
      WHERE id IN ('org-e-paid-1981','org-e-free-1981')) = 2,
    '#1981: the paid exit + the self-replay must both land');
END $$;

-- ── 8d. the IDENTITY path has no person to gate ────────────────────────────
-- (Distinct identities on purpose: `uq_member_identity_active` already allows an
-- identity ONE active owner org — that anchor, not the entitlement, is what keeps
-- one person from accumulating orgs on this path.)
SELECT public.provision_team(
    p_user_id  := NULL, p_identity := 'anon-1981-identity-a',
    p_org_id   := 'org-ident-1981', p_org_name := 'ident-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-ident-1981');
SELECT public.provision_team(
    p_user_id  := NULL, p_identity := 'anon-1981-identity-b',
    p_org_id   := 'org-ident2-1981', p_org_name := 'ident2-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-ident2-1981');

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations
      WHERE id IN ('org-ident-1981','org-ident2-1981')) = 2,
    '#1981: the identity path is not gated (no user holds an allowance)');
END $$;

-- ── 8e. collaborator-only user CAN own their own free org (#2789's trap) ────
SELECT public.provision_team(
    p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb2'::uuid,
    p_identity := NULL,
    p_org_id   := 'org-f-free-1981', p_org_name := 'f-free-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-f-free-1981');
INSERT INTO public.org_memberships (user_id, org_id, org_name, graph_name,
                                    role, status)
VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb3'::uuid, 'org-f-free-1981',
        'f-free-1981', 'org_org-f-free-1981', 'member', 'active')
ON CONFLICT (user_id, org_id) DO NOTHING;
SELECT public.provision_team(
    p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb3'::uuid,
    p_identity := NULL,
    p_org_id   := 'org-g-free-1981', p_org_name := 'g-free-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-g-free-1981');

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations
      WHERE id='org-g-free-1981') = 1,
    '#1981: a COLLABORATOR on a free org still provisions their own');
END $$;

-- ── 8f. subscription_status decides: paid/pending allow, paying refuses ─────
-- paid (active) owned org does not consume the allowance
SELECT public.provision_team(
    p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb4'::uuid,
    p_identity := NULL,
    p_org_id   := 'org-h-free-1981', p_org_name := 'h-free-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-h-free-1981');
UPDATE public.organizations SET subscription_status='active'
 WHERE id='org-h-free-1981';
SELECT public.provision_team(
    p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb4'::uuid,
    p_identity := NULL,
    p_org_id   := 'org-h2-free-1981', p_org_name := 'h2-free-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-h2-free-1981');

-- pending_payment is not real yet — it does not consume the allowance
SELECT public.provision_team(
    p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb5'::uuid,
    p_identity := NULL,
    p_org_id   := 'org-i-free-1981', p_org_name := 'i-free-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-i-free-1981');
UPDATE public.organizations SET subscription_status='pending_payment'
 WHERE id='org-i-free-1981';
SELECT public.provision_team(
    p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb5'::uuid,
    p_identity := NULL,
    p_org_id   := 'org-i2-free-1981', p_org_name := 'i2-free-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-i2-free-1981');

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations
      WHERE id IN ('org-h2-free-1981','org-i2-free-1981')) = 2,
    '#1981: a paid org and a pending_payment org do not consume the allowance');
END $$;

-- past_due / trialing ARE paying (`_BILLING_ACTIVE_STATUSES`): like `active`
-- they do NOT consume the allowance — parity with `owned_free_org_ids` and the
-- #1877 twin, both of which exclude the whole paying window.
SELECT public.provision_team(
    p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb6'::uuid,
    p_identity := NULL,
    p_org_id   := 'org-j-pd-1981', p_org_name := 'j-pd-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-j-pd-1981');
UPDATE public.organizations SET subscription_status='past_due'
 WHERE id='org-j-pd-1981';
SELECT public.provision_team(
    p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb6'::uuid,
    p_identity := NULL,
    p_org_id   := 'org-j-tr-1981', p_org_name := 'j-tr-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-j-tr-1981');
UPDATE public.organizations SET subscription_status='trialing'
 WHERE id='org-j-tr-1981';
SELECT public.provision_team(
    p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb6'::uuid,
    p_identity := NULL,
    p_org_id   := 'org-j-3-1981', p_org_name := 'j-3-1981',
    p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
    p_graph_name := 'org_org-j-3-1981');

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations
      WHERE id IN ('org-j-pd-1981','org-j-tr-1981','org-j-3-1981')) = 3,
    '#1981: past_due / trialing are paying — they do not consume the allowance');
END $$;

-- 'canceled' is NOT paying, so it COUNTS as free: this pins that the backstop
-- preserves the acceptance ruled on #2789 §2 (create A → buy B → cancel B ends
-- with two free orgs) instead of silently closing it into a refusal.
UPDATE public.organizations SET subscription_status='canceled'
 WHERE id='org-i2-free-1981';
DO $$
DECLARE
  v_msg   text;
  v_state text;
BEGIN
  BEGIN
    PERFORM public.provision_team(
        p_user_id  := 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbb5'::uuid,
        p_identity := NULL,
        p_org_id   := 'org-i3-free-1981', p_org_name := 'i3-free-1981',
        p_api_key  := NULL, p_key_hash := NULL, p_lookup_hash := NULL,
        p_graph_name := 'org_org-i3-free-1981');
    RAISE EXCEPTION 'ASSERTION FAILED: #1981 a canceled org must still count as free';
  EXCEPTION WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS v_msg = MESSAGE_TEXT, v_state = RETURNED_SQLSTATE;
    IF v_state <> 'PT402' THEN
      RAISE;
    END IF;
    PERFORM tests.assert(
      v_msg = 'one_free_org_limit:org-i2-free-1981',
      '#1981: canceled must count as free (blocking org reported: '
      || coalesce(v_msg, '<null>') || ')');
  END;
END $$;

-- ============================================================================
-- SECTION 9 — cleanup (audit rows exempt; append-only)
-- ============================================================================
DELETE FROM public.api_keys WHERE org_id LIKE '%-1981';
DELETE FROM public.org_memberships WHERE org_id LIKE '%-1981' OR identity LIKE '%1981%';
DELETE FROM public.organizations WHERE id LIKE '%-1981';
DELETE FROM auth.users WHERE email LIKE '%1981test%';

DO $$ BEGIN
  PERFORM tests.assert(
    NOT EXISTS (SELECT 1 FROM public.organizations WHERE id LIKE '%-1981'),
    'cleanup: no #1981 fixture organizations left');
  PERFORM tests.assert(
    NOT EXISTS (SELECT 1 FROM public.org_memberships WHERE org_id LIKE '%-1981'),
    'cleanup: no #1981 fixture memberships left');
END $$;

-- ============================================================================
-- SECTION 10 — cleanup (audit rows exempt; append-only)
-- ============================================================================
DELETE FROM public.api_keys WHERE org_id LIKE '%-770';
DELETE FROM public.org_memberships WHERE org_id LIKE '%-770' OR identity LIKE '%770%';
DELETE FROM public.organizations WHERE id LIKE '%-770';
DELETE FROM auth.users WHERE email LIKE '%770test%';

DO $$ BEGIN
  PERFORM tests.assert(
    NOT EXISTS (SELECT 1 FROM public.organizations WHERE id LIKE '%-770'),
    'cleanup: no fixture organizations left');
  PERFORM tests.assert(
    NOT EXISTS (SELECT 1 FROM public.org_memberships WHERE org_id LIKE '%-770'),
    'cleanup: no fixture memberships left');
END $$;
