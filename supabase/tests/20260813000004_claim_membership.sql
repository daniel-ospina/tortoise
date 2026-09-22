-- ============================================================================
-- SQL-level verification for migration 20260813000004 (issue #1082, PR1)
-- Claim path: anonymous org attaches a provider-verified identifier via the
-- SECURITY DEFINER claim_membership RPC. This suite locks the FULL binding:
--
--   · catalog state (RPC, partial indexes, audit detail column, grants)
--   · link (anon owner row → user_id, identity cleared; organizations.email NOT
--     written — demotion 20260827000001)
--   · idempotent re-claim (owner by (org_id, user_id) → noop success)
--   · second-claim-409 (first-claim-wins for a DIFFERENT user)
--   · merge/promote (existing (user,org) row promoted to owner; identity
--     row DROPPED BEFORE promote — P3-FIX-R)
--   · removed-row reactivation on promote (P4)
--   · non-owner reject (anon non-owner row is never linked)
--   · null-user-row untouched (non-owner anon rows on other organizations intact)
--   · email is a USER property: claim never writes organizations.email (demotion
--     20260827000001); email_in_use is gone with uq_teams_email
--   · bootstrap key rejected (advisory 1); expired key rejected (advisory 3)
--   · RPC-grant: claim_membership REJECTED from authenticated
--   · tamper: RPC signature accepts ONLY (p_lookup_hash, p_user_id,
--     p_email) — no client-supplied org_id/identity
--   · owner ≤1 invariant: uq_member_owner rejects a 2nd active owner;
--     placeholder rows (org_id='') excluded (solution-verify P1)
--   · placeholder row dropped on claim (P3-FIX-Q tail)
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--   (applies migrations 0001–20260813000004 + runs this suite with
--   ON_ERROR_STOP semantics; the #769/#770 suites run first)
--
-- Every assertion RAISEs on failure; with ON_ERROR_STOP=1 any failure exits
-- non-zero. Test rows use the "-1082" suffix for safe cleanup.
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
DELETE FROM public.api_keys WHERE org_id LIKE '%-1082';
DELETE FROM public.org_memberships WHERE org_id LIKE '%-1082' OR identity LIKE '%1082%';
DELETE FROM public.organizations WHERE id LIKE '%-1082';
DELETE FROM auth.users WHERE email LIKE '%1082test%';

-- Fixture users (each INSERT fires handle_new_user → placeholder row)
INSERT INTO auth.users (instance_id, id, aud, role, email, encrypted_password,
                        email_confirmed_at, raw_app_meta_data, raw_user_meta_data)
VALUES ('00000000-0000-0000-0000-000000000000'::uuid,
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
        'authenticated', 'authenticated', 'user-claim-a-1082test@example.com', '',
        now(), '{"providers":["github"]}'::jsonb, '{}'::jsonb),
       ('00000000-0000-0000-0000-000000000000'::uuid,
        'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid,
        'authenticated', 'authenticated', 'user-claim-b-1082test@example.com', '',
        now(), '{"providers":["google"]}'::jsonb, '{}'::jsonb)
ON CONFLICT (id) DO NOTHING;

-- ============================================================================
-- SECTION 1 — catalog state (20260813000004 applied cleanly)
-- ============================================================================
DO $$ BEGIN
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_proc WHERE proname='claim_membership' AND pronamespace='public'::regnamespace),
    'claim_membership RPC must exist');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_proc
            WHERE proname='claim_membership'
              AND pg_get_function_arguments(oid) = 'p_lookup_hash text, p_user_id uuid, p_email text'),
    'claim_membership signature must be (p_lookup_hash text, p_user_id uuid, p_email text) — '
    'no client-supplied org_id/identity (tamper binding)');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='org_memberships'
            AND indexname='uq_member_owner'),
    'partial unique uq_member_owner (owner ≤1) must exist');
  PERFORM tests.assert(
    NOT EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='organizations'
                  AND indexname='uq_teams_email'),
    'partial unique uq_teams_email must NOT exist (demoted to contact field, 20260827000001)');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='audit_events' AND column_name='detail'
              AND data_type='jsonb'),
    'audit_events.detail JSONB column must exist');
END $$;

-- ============================================================================
-- SECTION 2 — anon org provisioned (identity path), then claimed
-- ============================================================================
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'anon-1082test-a',
  p_org_id     => 'org-anon-1082',
  p_org_name   => 'Anon 1082',
  p_api_key     => 'tt_plaintext_1082_a',
  p_key_hash    => 'salt:hash-1082-a',
  p_lookup_hash => 'lkp-anon-1082-a',
  p_graph_name  => 'org_org-anon-1082',
  p_key_prefix  => 'tt_plain'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-anon-1082' AND user_id IS NULL AND identity='anon-1082test-a'
        AND role='owner' AND status='active') = 1,
    'provision: anon owner row (NULL user_id + identity) exists');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys
      WHERE org_id='org-anon-1082' AND lookup_hash='lkp-anon-1082-a' AND revoked_at IS NULL) = 1,
    'provision: api_keys row exists (authoritative key→org binding)');
  PERFORM tests.assert(
    (SELECT email FROM public.organizations WHERE id='org-anon-1082') IS NULL,
    'provision: anon org has NULL email');
END $$;

-- Claim by user-claim-a
SELECT public.claim_membership(
  p_lookup_hash => 'lkp-anon-1082-a',
  p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
  p_email       => 'user-claim-a-1082test@example.com'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-anon-1082'
        AND user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid
        AND role='owner' AND status='active') = 1,
    'claim: owner row linked to verified user');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-anon-1082' AND user_id IS NULL) = 0,
    'claim: anon identity anchor cleared (user_id no longer NULL)');
  PERFORM tests.assert(
    (SELECT identity FROM public.org_memberships
      WHERE org_id='org-anon-1082'
        AND user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid) IS NULL,
    'claim: identity column cleared');
  PERFORM tests.assert(
    (SELECT email FROM public.organizations WHERE id='org-anon-1082') IS NULL,
    'claim: organizations.email NOT written (demotion — email is a user property)');
  -- the api_keys row is UNTOUCHED → same key still authenticates (indicator 1)
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys
      WHERE org_id='org-anon-1082' AND lookup_hash='lkp-anon-1082-a' AND revoked_at IS NULL) = 1,
    'claim: api_keys row untouched — same key still resolves');
END $$;

-- ============================================================================
-- SECTION 3 — idempotent re-claim (P3-FIX-Q): same user → noop success
-- ============================================================================
DO $$ BEGIN
  BEGIN
    PERFORM public.claim_membership(
      p_lookup_hash => 'lkp-anon-1082-a',
      p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
      p_email       => 'user-claim-a-1082test@example.com'
    );
  EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'ASSERTION FAILED: idempotent re-claim must succeed, got %', SQLERRM;
  END;
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-anon-1082'
        AND user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid AND role='owner') = 1,
    're-claim: exactly one owner row (no duplicate)');
END $$;

-- ============================================================================
-- SECTION 4 — second-claim-409 (first-claim-wins): a DIFFERENT user is rejected
-- ============================================================================
DO $$ BEGIN
  BEGIN
    PERFORM public.claim_membership(
      p_lookup_hash => 'lkp-anon-1082-a',
      p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid,
      p_email       => 'user-claim-b-1082test@example.com'
    );
    RAISE EXCEPTION 'ASSERTION FAILED: second claim must raise already_claimed';
  EXCEPTION WHEN OTHERS THEN
    PERFORM tests.assert(SQLERRM LIKE '%already_claimed%',
      'second claim must raise claim_membership:already_claimed, got: ' || SQLERRM);
  END;
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-anon-1082' AND user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid) = 0,
    'second claim: user-b NOT linked');
END $$;

-- ============================================================================
-- SECTION 5 — merge/promote (P3-FIX-R): existing (user, org) row promoted to
-- owner; identity row DROPPED BEFORE promote; removed row reactivated (P4)
-- ============================================================================
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'anon-1082test-b',
  p_org_id     => 'org-merge-1082',
  p_org_name   => 'Merge 1082',
  p_api_key     => 'tt_plaintext_1082_b',
  p_key_hash    => 'salt:hash-1082-b',
  p_lookup_hash => 'lkp-merge-1082-b',
  p_graph_name  => 'org_org-merge-1082',
  p_key_prefix  => 'tt_plain'
);

-- user-b is already a member of org-merge (status removed — a demoted/
-- removed member must be reactivated on promote, P4)
INSERT INTO public.org_memberships (user_id, org_id, org_name, key_hash, graph_name, role, status, identity)
VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid, 'org-merge-1082', 'Merge 1082',
        'salt:hash-1082-userb', 'org_org-merge-1082', 'member', 'removed', NULL)
ON CONFLICT (user_id, org_id) DO NOTHING;

SELECT public.claim_membership(
  p_lookup_hash => 'lkp-merge-1082-b',
  p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid,
  p_email       => 'user-claim-b-1082test@example.com'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-merge-1082'
        AND user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid
        AND role='owner' AND status='active') = 1,
    'merge: existing member row PROMOTED to owner/active (reactivated)');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-merge-1082' AND user_id IS NULL) = 0,
    'merge: identity owner row DROPPED (promote-first would violate uq_member_owner)');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships WHERE org_id='org-merge-1082') = 1,
    'merge: exactly one membership row for the org');
  PERFORM tests.assert(
    (SELECT lookup_hash FROM public.org_memberships
      WHERE org_id='org-merge-1082'
        AND user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid) = 'lkp-merge-1082-b',
    'merge: lookup_hash copied from identity row (same key continuity)');
  PERFORM tests.assert(
    (SELECT email FROM public.organizations WHERE id='org-merge-1082') IS NULL,
    'merge: organizations.email NOT written (demotion)');
END $$;

-- ============================================================================
-- SECTION 6 — non-owner reject + null-user-row untouched
-- ============================================================================
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'anon-1082test-c',
  p_org_id     => 'org-nonowner-1082',
  p_org_name   => 'NonOwner 1082',
  p_api_key     => 'tt_plaintext_1082_c',
  p_key_hash    => 'salt:hash-1082-c',
  p_lookup_hash => 'lkp-nonowner-1082-c',
  p_graph_name  => 'org_org-nonowner-1082',
  p_key_prefix  => 'tt_plain'
);
-- demote the anon owner to member (simulates an anon member-only row)
UPDATE public.org_memberships SET role='member'
 WHERE org_id='org-nonowner-1082' AND user_id IS NULL;

DO $$ BEGIN
  BEGIN
    PERFORM public.claim_membership(
      p_lookup_hash => 'lkp-nonowner-1082-c',
      p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
      p_email       => 'user-claim-a-1082test@example.com'
    );
    RAISE EXCEPTION 'ASSERTION FAILED: non-owner claim must raise already_claimed';
  EXCEPTION WHEN OTHERS THEN
    PERFORM tests.assert(SQLERRM LIKE '%already_claimed%',
      'non-owner claim must raise already_claimed, got: ' || SQLERRM);
  END;
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-nonowner-1082' AND user_id IS NULL AND role='member'
        AND identity='anon-1082test-c') = 1,
    'non-owner: anon non-owner row untouched (never linked)');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-nonowner-1082' AND user_id IS NOT NULL) = 0,
    'non-owner: no user linked');
END $$;

-- ============================================================================
-- SECTION 7 — email overwrite A→B (P1-FIX-B, unconditional) + email_in_use
-- ============================================================================
-- re-provision a second anon org with an existing verified email
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'anon-1082test-d',
  p_org_id     => 'org-email-1082',
  p_org_name   => 'Email 1082',
  p_api_key     => 'tt_plaintext_1082_d',
  p_key_hash    => 'salt:hash-1082-d',
  p_lookup_hash => 'lkp-email-1082-d',
  p_graph_name  => 'org_org-email-1082',
  p_email       => 'stale-email-1082@example.com',
  p_key_prefix  => 'tt_plain'
);

SELECT public.claim_membership(
  p_lookup_hash => 'lkp-email-1082-d',
  p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
  p_email       => 'fresh-email-1082@example.com'
);

DO $$ BEGIN
  -- claim no longer writes organizations.email: the provision-time contact value
  -- (sanctioned allowlisted write) survives the claim untouched.
  PERFORM tests.assert(
    (SELECT email FROM public.organizations WHERE id='org-email-1082') = 'stale-email-1082@example.com',
    'claim must NOT write organizations.email — provision contact value survives (A→B overwrite gone)');
END $$;

-- cross-org email collision → NO LONGER a thing: claim never writes
-- organizations.email, so the same email on two organizations is legal (uq_teams_email
-- dropped). The claim SUCCEEDS and links the owner.
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'anon-1082test-e',
  p_org_id     => 'org-email2-1082',
  p_org_name   => 'Email2 1082',
  p_api_key     => 'tt_plaintext_1082_e',
  p_key_hash    => 'salt:hash-1082-e',
  p_lookup_hash => 'lkp-email2-1082-e',
  p_graph_name  => 'org_org-email2-1082',
  p_key_prefix  => 'tt_plain'
);
DO $$ BEGIN
  PERFORM public.claim_membership(
    p_lookup_hash => 'lkp-email2-1082-e',
    p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
    p_email       => 'user-claim-a-1082test@example.com'
  );
  -- no email_in_use: the claim succeeds and links the owner
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-email2-1082' AND user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid
        AND role='owner' AND status='active') = 1,
    'claim with a shared email must SUCCEED (no organizations.email write → no collision)');
  PERFORM tests.assert(
    (SELECT email FROM public.organizations WHERE id='org-email2-1082') IS NULL,
    'org-email2: organizations.email stays NULL (claim never writes it)');
END $$;

-- ============================================================================
-- SECTION 8 — bootstrap key rejected (advisory 1); expired key rejected (3)
-- ============================================================================
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'anon-1082test-f',
  p_org_id     => 'org-boot-1082',
  p_org_name   => 'Boot 1082',
  p_api_key     => 'tt_plaintext_1082_f',
  p_key_hash    => 'salt:hash-1082-f',
  p_lookup_hash => 'lkp-boot-1082-f',
  p_graph_name  => 'org_org-boot-1082',
  p_key_prefix  => 'tt_plain'
);
-- a bootstrap session key for the SAME org must NOT claim (session keys
-- live only in api_keys with created_via='bootstrap', never owner rows)
INSERT INTO public.api_keys (id, org_id, lookup_hash, created_via, created_by)
VALUES ('key-boot-1082', 'org-boot-1082', 'lkp-boot-session-1082', 'bootstrap', 'anon-1082test-f')
ON CONFLICT (lookup_hash) DO NOTHING;

DO $$ BEGIN
  BEGIN
    PERFORM public.claim_membership(
      p_lookup_hash => 'lkp-boot-session-1082',
      p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
      p_email       => 'user-claim-a-1082test@example.com'
    );
    RAISE EXCEPTION 'ASSERTION FAILED: bootstrap key claim must be rejected';
  EXCEPTION WHEN OTHERS THEN
    PERFORM tests.assert(SQLERRM LIKE '%key_not_claimable%',
      'bootstrap key must raise claim_membership:key_not_claimable, got: ' || SQLERRM);
  END;
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-boot-1082' AND user_id IS NOT NULL) = 0,
    'bootstrap: no user linked via session key');
END $$;

-- expired provisioned key rejected
INSERT INTO public.api_keys (id, org_id, lookup_hash, created_via, created_by, expires_at)
VALUES ('key-exp-1082', 'org-boot-1082', 'lkp-expired-1082', 'provisioned', 'anon-1082test-f',
        now() - interval '1 hour')
ON CONFLICT (lookup_hash) DO NOTHING;

DO $$ BEGIN
  BEGIN
    PERFORM public.claim_membership(
      p_lookup_hash => 'lkp-expired-1082',
      p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
      p_email       => 'user-claim-a-1082test@example.com'
    );
    RAISE EXCEPTION 'ASSERTION FAILED: expired key claim must be rejected';
  EXCEPTION WHEN OTHERS THEN
    PERFORM tests.assert(SQLERRM LIKE '%key_expired%',
      'expired key must raise claim_membership:key_expired, got: ' || SQLERRM);
  END;
END $$;

-- ============================================================================
-- SECTION 9 — RPC grant: claim_membership REJECTED from authenticated
-- ============================================================================
SET ROLE authenticated;
DO $$ BEGIN
  BEGIN
    PERFORM public.claim_membership(
      p_lookup_hash => 'lkp-anon-1082-a',
      p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
      p_email       => 'user-claim-a-1082test@example.com'
    );
    RAISE EXCEPTION 'FAIL: authenticated must not execute claim_membership';
  EXCEPTION WHEN insufficient_privilege THEN NULL; END;
END $$;
RESET ROLE;

-- ============================================================================
-- SECTION 10 — owner ≤1 invariant (P3-FIX-P): uq_member_owner rejects a 2nd
-- active owner; placeholder rows (org_id='') are EXCLUDED (solution-verify
-- P1 — the handle_new_user trigger inserts placeholder rows all sharing
-- org_id='' + role='owner'; WITHOUT the exclusion every signup would raise)
-- ============================================================================
-- two placeholder owner rows (org_id='') — must be legal
INSERT INTO public.org_memberships (user_id, org_id, org_name, key_hash, graph_name, role, status)
VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid, '', 'provisioning...', 'pending', '', 'owner', 'active')
ON CONFLICT (user_id, org_id) DO NOTHING;
INSERT INTO public.org_memberships (user_id, org_id, org_name, key_hash, graph_name, role, status)
VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid, '', 'provisioning...', 'pending', '', 'owner', 'active')
ON CONFLICT (user_id, org_id) DO NOTHING;

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='' AND role='owner' AND status='active') >= 2,
    'placeholder exclusion: multiple placeholder owner rows (org_id='''') are legal');
END $$;

-- a second ACTIVE owner on a real org must be rejected by the partial index
-- (org-anon-1082 already has user-claim-a as its active owner)
DO $$ BEGIN
  BEGIN
    INSERT INTO public.org_memberships (user_id, org_id, org_name, key_hash, graph_name, role, status, identity)
    VALUES (NULL, 'org-anon-1082', 'Anon 1082', 'k', 'g', 'owner', 'active', 'anon-1082test-z');
    RAISE EXCEPTION 'FAIL: uq_member_owner must reject a 2nd active owner';
  EXCEPTION WHEN unique_violation THEN NULL; END;
END $$;

-- a second owner with status <> 'active' is allowed (the partial index
-- matches status='active' only — mirrors the anon predicate)
INSERT INTO public.org_memberships (user_id, org_id, org_name, key_hash, graph_name, role, status, identity)
VALUES (NULL, 'org-nonowner-1082', 'NonOwner 1082', 'k', 'g', 'owner', 'removed', 'anon-1082test-z')
ON CONFLICT DO NOTHING;
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE org_id='org-nonowner-1082' AND role='owner' AND status='removed') = 1,
    'uq_member_owner: non-active owner rows are not constrained (predicate match)');
END $$;

-- ============================================================================
-- SECTION 11 — organizations.email demoted: cross-org duplicate email ALLOWED
-- (uq_teams_email dropped by 20260827000001 — email is a user property)
-- ============================================================================
DO $$ BEGIN
  UPDATE public.organizations SET email='user-claim-a-1082test@example.com'
   WHERE id='org-email2-1082';
  PERFORM tests.assert(
    (SELECT count(*) FROM public.organizations WHERE id='org-email2-1082' AND email='user-claim-a-1082test@example.com') = 1,
    'duplicate verified email across organizations must be ALLOWED (uq_teams_email dropped)');
END $$;

-- ============================================================================
-- SECTION 12 — placeholder row dropped on claim (P3-FIX-Q tail)
-- ============================================================================
-- user-claim-b currently holds a placeholder row from the auth.users INSERT
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid AND org_id='') = 1,
    'precondition: user-b placeholder row exists');
END $$;

SELECT public.claim_membership(
  p_lookup_hash => 'lkp-merge-1082-b',  -- already owned by user-b → noop path
  p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid,
  p_email       => 'user-claim-b-1082test@example.com'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.org_memberships
      WHERE user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid AND org_id='') = 0,
    'claim: leftover placeholder row dropped');
END $$;


-- ============================================================================
-- SECTION 12.5 — concurrent-claim race guard (#1082 review P1-1)
-- ----------------------------------------------------------------------------
-- Two claims for the SAME org/key by different users: the second must raise
-- already_claimed, never silently overwrite (the RPC's Step-5 UPDATE carries
-- `AND user_id IS NULL`, so under READ COMMITTED the loser's EPQ re-check
-- finds 0 rows). Simulated sequentially here; the user_id IS NULL conjunct is
-- what makes the interleaving safe.
DO $$
DECLARE v_org text := 'org-race-1082';
        v_owner uuid;
BEGIN
  -- provision an anon org via the real provision_team RPC (identity path)
  PERFORM public.provision_team(
    p_user_id => NULL, p_identity => 'anon-race-1082',
    p_org_id => v_org, p_org_name => 'race-1082',
    p_api_key => 'tt_race_1082_key', p_key_hash => 'race-hash-1082',
    p_lookup_hash => 'lkp-race-1082', p_graph_name => 'org_race_1082',
    p_tier => 'free', p_key_prefix => 'tt_race_108',
    p_max_users => 1, p_max_graphs => 1,
    p_ops_allowance => 10000, p_graph_size_cap => 10000);

  -- claim #1 by user-01 (wins; fixture exists in auth.users)
  PERFORM public.claim_membership(
    p_lookup_hash => 'lkp-race-1082',
    p_user_id => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
    p_email => 'race-a-1082test@example.com');

  -- claim #2 by user-02 (must FAIL — user_id IS NULL conjunct gone;
  -- fixture exists in auth.users so the FK is satisfied and the RPC's own
  -- already_claimed guard is what rejects)
  BEGIN
    PERFORM public.claim_membership(
      p_lookup_hash => 'lkp-race-1082',
      p_user_id => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid,
      p_email => 'race-b-1082test@example.com');
    RAISE EXCEPTION 'ASSERTION FAILED: concurrent second claim must raise already_claimed';
  EXCEPTION WHEN others THEN
    IF SQLERRM NOT LIKE '%already_claimed%' THEN
      RAISE EXCEPTION 'ASSERTION FAILED: expected already_claimed, got: %', SQLERRM;
    END IF;
  END;

  PERFORM tests.assert(
    (SELECT user_id FROM public.org_memberships
      WHERE org_id=v_org AND role='owner' AND status='active') =
      'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
    'race: first-claim-wins — owner stays user-01');
  PERFORM tests.assert(
    (SELECT email FROM public.organizations WHERE id=v_org) IS NULL,
    'race: organizations.email stays NULL — claim never writes it (demotion)');
END $$;

-- ============================================================================
-- SECTION 13 — cleanup (audit rows exempt; append-only)
-- ============================================================================
DELETE FROM public.api_keys WHERE org_id LIKE '%-1082';
DELETE FROM public.org_memberships WHERE org_id LIKE '%-1082' OR identity LIKE '%1082%';
DELETE FROM public.organizations WHERE id LIKE '%-1082';
DELETE FROM auth.users WHERE email LIKE '%1082test%';
