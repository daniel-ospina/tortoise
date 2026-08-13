-- ============================================================================
-- SQL-level verification for migration 20260813000003 (issue #1082, PR1)
-- Claim path: anonymous team attaches a provider-verified identifier via the
-- SECURITY DEFINER claim_membership RPC. This suite locks the FULL binding:
--
--   · catalog state (RPC, partial indexes, audit detail column, grants)
--   · link (anon owner row → user_id, identity cleared, teams.email set)
--   · idempotent re-claim (owner by (team_id, user_id) → noop success)
--   · second-claim-409 (first-claim-wins for a DIFFERENT user)
--   · merge/promote (existing (user,team) row promoted to owner; identity
--     row DROPPED BEFORE promote — P3-FIX-R)
--   · removed-row reactivation on promote (P4)
--   · non-owner reject (anon non-owner row is never linked)
--   · null-user-row untouched (non-owner anon rows on other teams intact)
--   · email overwrite A→B inside the claim txn; email_in_use on cross-team
--     collision (P3-FIX-S)
--   · bootstrap key rejected (advisory 1); expired key rejected (advisory 3)
--   · RPC-grant: claim_membership REJECTED from authenticated
--   · tamper: RPC signature accepts ONLY (p_lookup_hash, p_user_id,
--     p_email) — no client-supplied team_id/identity
--   · owner ≤1 invariant: uq_member_owner rejects a 2nd active owner;
--     placeholder rows (team_id='') excluded (solution-verify P1)
--   · placeholder row dropped on claim (P3-FIX-Q tail)
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--   (applies migrations 0001–20260813000003 + runs this suite with
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
DELETE FROM public.api_keys WHERE team_id LIKE '%-1082';
DELETE FROM public.team_memberships WHERE team_id LIKE '%-1082' OR identity LIKE '%1082%';
DELETE FROM public.teams WHERE id LIKE '%-1082';
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
-- SECTION 1 — catalog state (20260813000003 applied cleanly)
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
    'no client-supplied team_id/identity (tamper binding)');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='team_memberships'
            AND indexname='uq_member_owner'),
    'partial unique uq_member_owner (owner ≤1) must exist');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='teams'
            AND indexname='uq_teams_email'),
    'partial unique uq_teams_email must exist');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM information_schema.columns
            WHERE table_schema='public' AND table_name='audit_events' AND column_name='detail'
              AND data_type='jsonb'),
    'audit_events.detail JSONB column must exist');
END $$;

-- ============================================================================
-- SECTION 2 — anon team provisioned (identity path), then claimed
-- ============================================================================
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'anon-1082test-a',
  p_team_id     => 'team-anon-1082',
  p_team_name   => 'Anon 1082',
  p_api_key     => 'tt_plaintext_1082_a',
  p_key_hash    => 'salt:hash-1082-a',
  p_lookup_hash => 'lkp-anon-1082-a',
  p_graph_name  => 'team_team-anon-1082',
  p_key_prefix  => 'tt_plain'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-anon-1082' AND user_id IS NULL AND identity='anon-1082test-a'
        AND role='owner' AND status='active') = 1,
    'provision: anon owner row (NULL user_id + identity) exists');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys
      WHERE team_id='team-anon-1082' AND lookup_hash='lkp-anon-1082-a' AND revoked_at IS NULL) = 1,
    'provision: api_keys row exists (authoritative key→team binding)');
  PERFORM tests.assert(
    (SELECT email FROM public.teams WHERE id='team-anon-1082') IS NULL,
    'provision: anon team has NULL email');
END $$;

-- Claim by user-claim-a
SELECT public.claim_membership(
  p_lookup_hash => 'lkp-anon-1082-a',
  p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
  p_email       => 'user-claim-a-1082test@example.com'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-anon-1082'
        AND user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid
        AND role='owner' AND status='active') = 1,
    'claim: owner row linked to verified user');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-anon-1082' AND user_id IS NULL) = 0,
    'claim: anon identity anchor cleared (user_id no longer NULL)');
  PERFORM tests.assert(
    (SELECT identity FROM public.team_memberships
      WHERE team_id='team-anon-1082'
        AND user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid) IS NULL,
    'claim: identity column cleared');
  PERFORM tests.assert(
    (SELECT email FROM public.teams WHERE id='team-anon-1082')
      = 'user-claim-a-1082test@example.com',
    'claim: teams.email overwritten with verified OAuth email');
  -- the api_keys row is UNTOUCHED → same key still authenticates (indicator 1)
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys
      WHERE team_id='team-anon-1082' AND lookup_hash='lkp-anon-1082-a' AND revoked_at IS NULL) = 1,
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
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-anon-1082'
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
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-anon-1082' AND user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid) = 0,
    'second claim: user-b NOT linked');
END $$;

-- ============================================================================
-- SECTION 5 — merge/promote (P3-FIX-R): existing (user, team) row promoted to
-- owner; identity row DROPPED BEFORE promote; removed row reactivated (P4)
-- ============================================================================
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'anon-1082test-b',
  p_team_id     => 'team-merge-1082',
  p_team_name   => 'Merge 1082',
  p_api_key     => 'tt_plaintext_1082_b',
  p_key_hash    => 'salt:hash-1082-b',
  p_lookup_hash => 'lkp-merge-1082-b',
  p_graph_name  => 'team_team-merge-1082',
  p_key_prefix  => 'tt_plain'
);

-- user-b is already a member of team-merge (status removed — a demoted/
-- removed member must be reactivated on promote, P4)
INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role, status, identity)
VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid, 'team-merge-1082', 'Merge 1082',
        'salt:hash-1082-userb', 'team_team-merge-1082', 'member', 'removed', NULL)
ON CONFLICT (user_id, team_id) DO NOTHING;

SELECT public.claim_membership(
  p_lookup_hash => 'lkp-merge-1082-b',
  p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid,
  p_email       => 'user-claim-b-1082test@example.com'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-merge-1082'
        AND user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid
        AND role='owner' AND status='active') = 1,
    'merge: existing member row PROMOTED to owner/active (reactivated)');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-merge-1082' AND user_id IS NULL) = 0,
    'merge: identity owner row DROPPED (promote-first would violate uq_member_owner)');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships WHERE team_id='team-merge-1082') = 1,
    'merge: exactly one membership row for the team');
  PERFORM tests.assert(
    (SELECT lookup_hash FROM public.team_memberships
      WHERE team_id='team-merge-1082'
        AND user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid) = 'lkp-merge-1082-b',
    'merge: lookup_hash copied from identity row (same key continuity)');
  PERFORM tests.assert(
    (SELECT email FROM public.teams WHERE id='team-merge-1082')
      = 'user-claim-b-1082test@example.com',
    'merge: teams.email overwritten');
END $$;

-- ============================================================================
-- SECTION 6 — non-owner reject + null-user-row untouched
-- ============================================================================
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'anon-1082test-c',
  p_team_id     => 'team-nonowner-1082',
  p_team_name   => 'NonOwner 1082',
  p_api_key     => 'tt_plaintext_1082_c',
  p_key_hash    => 'salt:hash-1082-c',
  p_lookup_hash => 'lkp-nonowner-1082-c',
  p_graph_name  => 'team_team-nonowner-1082',
  p_key_prefix  => 'tt_plain'
);
-- demote the anon owner to member (simulates an anon member-only row)
UPDATE public.team_memberships SET role='member'
 WHERE team_id='team-nonowner-1082' AND user_id IS NULL;

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
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-nonowner-1082' AND user_id IS NULL AND role='member'
        AND identity='anon-1082test-c') = 1,
    'non-owner: anon non-owner row untouched (never linked)');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-nonowner-1082' AND user_id IS NOT NULL) = 0,
    'non-owner: no user linked');
END $$;

-- ============================================================================
-- SECTION 7 — email overwrite A→B (P1-FIX-B, unconditional) + email_in_use
-- ============================================================================
-- re-provision a second anon team with an existing verified email
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'anon-1082test-d',
  p_team_id     => 'team-email-1082',
  p_team_name   => 'Email 1082',
  p_api_key     => 'tt_plaintext_1082_d',
  p_key_hash    => 'salt:hash-1082-d',
  p_lookup_hash => 'lkp-email-1082-d',
  p_graph_name  => 'team_team-email-1082',
  p_email       => 'stale-email-1082@example.com',
  p_key_prefix  => 'tt_plain'
);

SELECT public.claim_membership(
  p_lookup_hash => 'lkp-email-1082-d',
  p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
  p_email       => 'fresh-email-1082@example.com'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT email FROM public.teams WHERE id='team-email-1082') = 'fresh-email-1082@example.com',
    'email overwrite: stored email replaced by verified OAuth email (A→B)');
END $$;

-- cross-team email collision → email_in_use
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'anon-1082test-e',
  p_team_id     => 'team-email2-1082',
  p_team_name   => 'Email2 1082',
  p_api_key     => 'tt_plaintext_1082_e',
  p_key_hash    => 'salt:hash-1082-e',
  p_lookup_hash => 'lkp-email2-1082-e',
  p_graph_name  => 'team_team-email2-1082',
  p_key_prefix  => 'tt_plain'
);
-- team-anon-1082 already holds fresh-email-1082@example.com? No — it holds
-- user-claim-a-1082test@example.com. Use THAT email for the collision.
DO $$ BEGIN
  BEGIN
    PERFORM public.claim_membership(
      p_lookup_hash => 'lkp-email2-1082-e',
      p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid,
      p_email       => 'user-claim-a-1082test@example.com'
    );
    RAISE EXCEPTION 'ASSERTION FAILED: cross-team email collision must raise email_in_use';
  EXCEPTION WHEN OTHERS THEN
    PERFORM tests.assert(SQLERRM LIKE '%email_in_use%',
      'cross-team email collision must raise claim_membership:email_in_use, got: ' || SQLERRM);
  END;
  -- the claim txn rolled back: the owner row must still be anon-claimed-free
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-email2-1082' AND user_id IS NULL AND role='owner'
        AND identity='anon-1082test-e') = 1,
    'email_in_use: txn rolled back — anon owner row intact');
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-email2-1082' AND user_id IS NOT NULL) = 0,
    'email_in_use: no user linked (atomic rollback)');
END $$;

-- ============================================================================
-- SECTION 8 — bootstrap key rejected (advisory 1); expired key rejected (3)
-- ============================================================================
SELECT public.provision_team(
  p_user_id     => NULL,
  p_identity    => 'anon-1082test-f',
  p_team_id     => 'team-boot-1082',
  p_team_name   => 'Boot 1082',
  p_api_key     => 'tt_plaintext_1082_f',
  p_key_hash    => 'salt:hash-1082-f',
  p_lookup_hash => 'lkp-boot-1082-f',
  p_graph_name  => 'team_team-boot-1082',
  p_key_prefix  => 'tt_plain'
);
-- a bootstrap session key for the SAME team must NOT claim (session keys
-- live only in api_keys with created_via='bootstrap', never owner rows)
INSERT INTO public.api_keys (id, team_id, lookup_hash, created_via, created_by)
VALUES ('key-boot-1082', 'team-boot-1082', 'lkp-boot-session-1082', 'bootstrap', 'anon-1082test-f')
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
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-boot-1082' AND user_id IS NOT NULL) = 0,
    'bootstrap: no user linked via session key');
END $$;

-- expired provisioned key rejected
INSERT INTO public.api_keys (id, team_id, lookup_hash, created_via, created_by, expires_at)
VALUES ('key-exp-1082', 'team-boot-1082', 'lkp-expired-1082', 'provisioned', 'anon-1082test-f',
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
-- active owner; placeholder rows (team_id='') are EXCLUDED (solution-verify
-- P1 — the handle_new_user trigger inserts placeholder rows all sharing
-- team_id='' + role='owner'; WITHOUT the exclusion every signup would raise)
-- ============================================================================
-- two placeholder owner rows (team_id='') — must be legal
INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role, status)
VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb01'::uuid, '', 'provisioning...', 'pending', '', 'owner', 'active')
ON CONFLICT (user_id, team_id) DO NOTHING;
INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role, status)
VALUES ('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid, '', 'provisioning...', 'pending', '', 'owner', 'active')
ON CONFLICT (user_id, team_id) DO NOTHING;

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='' AND role='owner' AND status='active') >= 2,
    'placeholder exclusion: multiple placeholder owner rows (team_id='''') are legal');
END $$;

-- a second ACTIVE owner on a real team must be rejected by the partial index
-- (team-anon-1082 already has user-claim-a as its active owner)
DO $$ BEGIN
  BEGIN
    INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role, status, identity)
    VALUES (NULL, 'team-anon-1082', 'Anon 1082', 'k', 'g', 'owner', 'active', 'anon-1082test-z');
    RAISE EXCEPTION 'FAIL: uq_member_owner must reject a 2nd active owner';
  EXCEPTION WHEN unique_violation THEN NULL; END;
END $$;

-- a second owner with status <> 'active' is allowed (the partial index
-- matches status='active' only — mirrors the anon predicate)
INSERT INTO public.team_memberships (user_id, team_id, team_name, key_hash, graph_name, role, status, identity)
VALUES (NULL, 'team-nonowner-1082', 'NonOwner 1082', 'k', 'g', 'owner', 'removed', 'anon-1082test-z')
ON CONFLICT DO NOTHING;
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE team_id='team-nonowner-1082' AND role='owner' AND status='removed') = 1,
    'uq_member_owner: non-active owner rows are not constrained (predicate match)');
END $$;

-- ============================================================================
-- SECTION 11 — uq_teams_email: cross-team duplicate verified email rejected
-- ============================================================================
DO $$ BEGIN
  BEGIN
    UPDATE public.teams SET email='user-claim-a-1082test@example.com'
     WHERE id='team-email2-1082';
    RAISE EXCEPTION 'FAIL: uq_teams_email must reject a duplicate verified email';
  EXCEPTION WHEN unique_violation THEN NULL; END;
END $$;

-- ============================================================================
-- SECTION 12 — placeholder row dropped on claim (P3-FIX-Q tail)
-- ============================================================================
-- user-claim-b currently holds a placeholder row from the auth.users INSERT
DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid AND team_id='') = 1,
    'precondition: user-b placeholder row exists');
END $$;

SELECT public.claim_membership(
  p_lookup_hash => 'lkp-merge-1082-b',  -- already owned by user-b → noop path
  p_user_id     => 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid,
  p_email       => 'user-claim-b-1082test@example.com'
);

DO $$ BEGIN
  PERFORM tests.assert(
    (SELECT count(*) FROM public.team_memberships
      WHERE user_id='bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbb02'::uuid AND team_id='') = 0,
    'claim: leftover placeholder row dropped');
END $$;

-- ============================================================================
-- SECTION 13 — cleanup (audit rows exempt; append-only)
-- ============================================================================
DELETE FROM public.api_keys WHERE team_id LIKE '%-1082';
DELETE FROM public.team_memberships WHERE team_id LIKE '%-1082' OR identity LIKE '%1082%';
DELETE FROM public.teams WHERE id LIKE '%-1082';
DELETE FROM auth.users WHERE email LIKE '%1082test%';
