-- ============================================================================
-- SQL-level verification for migration 20260927000001 (issue #1879)
-- Durable multi-worker mint serialization: api_key_slot_count /
-- session_key_mint / provision_api_key / recover_team_key (org-row lock).
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--
-- Every assertion RAISEs on failure; with ON_ERROR_STOP=1 any failure exits
-- non-zero. Test rows use the "-1879" suffix for safe cleanup.
--
-- HONEST LIMIT: PGlite is SINGLE-CONNECTION, so this suite can prove the
-- RPCs' FUNCTIONAL semantics and that the org-row lock is present in the
-- function bodies — it CANNOT hold two concurrent transactions and watch one
-- block on `FOR NO KEY UPDATE`. The serialization property itself is
-- established by (a) one RPC = one PostgREST request = one transaction and
-- (b) the lock mode being static, reviewable SQL. No assertion below claims
-- to have exercised contention.
-- ============================================================================

-- ── Assertion helper (tests schema; executed as the harness's superuser) ───
CREATE SCHEMA IF NOT EXISTS tests;
CREATE OR REPLACE FUNCTION tests.assert(cond boolean, msg text)
RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  IF cond IS DISTINCT FROM true THEN
    RAISE EXCEPTION 'ASSERTION FAILED: %', msg;
  END IF;
END $$;

-- ── Cleanup any prior test rows (idempotent re-runs) ────────────────────────
DELETE FROM public.api_keys WHERE org_id LIKE '%-1879%';
DELETE FROM public.org_memberships WHERE org_id LIKE '%-1879%';
DELETE FROM public.organizations WHERE id LIKE '%-1879%';

-- ============================================================================
-- SECTION 1 — grant hygiene: service_role ONLY, one overload each
-- ============================================================================
DO $$ BEGIN
  PERFORM tests.assert(
    NOT has_function_privilege('anon', 'public.api_key_slot_count(text,timestamp with time zone)', 'EXECUTE'),
    'anon must NOT EXECUTE api_key_slot_count');
  PERFORM tests.assert(
    NOT has_function_privilege('authenticated', 'public.api_key_slot_count(text,timestamp with time zone)', 'EXECUTE'),
    'authenticated must NOT EXECUTE api_key_slot_count');
  PERFORM tests.assert(
    has_function_privilege('service_role', 'public.api_key_slot_count(text,timestamp with time zone)', 'EXECUTE'),
    'service_role must EXECUTE api_key_slot_count');

  PERFORM tests.assert(
    NOT has_function_privilege('anon', 'public.session_key_mint(text,text,text,text,text,text,timestamp with time zone,timestamp with time zone,integer,integer)', 'EXECUTE'),
    'anon must NOT EXECUTE session_key_mint');
  PERFORM tests.assert(
    NOT has_function_privilege('authenticated', 'public.session_key_mint(text,text,text,text,text,text,timestamp with time zone,timestamp with time zone,integer,integer)', 'EXECUTE'),
    'authenticated must NOT EXECUTE session_key_mint');
  PERFORM tests.assert(
    has_function_privilege('service_role', 'public.session_key_mint(text,text,text,text,text,text,timestamp with time zone,timestamp with time zone,integer,integer)', 'EXECUTE'),
    'service_role must EXECUTE session_key_mint');

  PERFORM tests.assert(
    NOT has_function_privilege('anon', 'public.provision_api_key(text,text,text,text,text,text,timestamp with time zone,timestamp with time zone,text,text,jsonb,text,integer,integer,integer)', 'EXECUTE'),
    'anon must NOT EXECUTE provision_api_key');
  PERFORM tests.assert(
    NOT has_function_privilege('authenticated', 'public.provision_api_key(text,text,text,text,text,text,timestamp with time zone,timestamp with time zone,text,text,jsonb,text,integer,integer,integer)', 'EXECUTE'),
    'authenticated must NOT EXECUTE provision_api_key');
  PERFORM tests.assert(
    has_function_privilege('service_role', 'public.provision_api_key(text,text,text,text,text,text,timestamp with time zone,timestamp with time zone,text,text,jsonb,text,integer,integer,integer)', 'EXECUTE'),
    'service_role must EXECUTE provision_api_key');

  -- One overload each: a DROP/CREATE slip must not leave a stale second body
  -- live (the metering_increment overload trap the harness comments about).
  PERFORM tests.assert(
    (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'public' AND p.proname = 'session_key_mint') = 1,
    'session_key_mint must have exactly ONE overload');
  PERFORM tests.assert(
    (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'public' AND p.proname = 'provision_api_key') = 1,
    'provision_api_key must have exactly ONE overload');
  PERFORM tests.assert(
    (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'public' AND p.proname = 'api_key_slot_count') = 1,
    'api_key_slot_count must have exactly ONE overload');
  PERFORM tests.assert(
    (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
      WHERE n.nspname = 'public' AND p.proname = 'recover_team_key') = 1,
    'recover_team_key must have exactly ONE overload (re-issued, not overloaded)');
END $$;

-- ============================================================================
-- SECTION 2 — the org-row lock is present in every cap-writing RPC
-- ============================================================================
-- Static contract, stated as such: the lock is the serialization primitive,
-- and a future edit that drops it must be RED. (Contention itself is not
-- exercisable on a single connection — see the header.)
DO $$ DECLARE
  v_def text;
BEGIN
  FOR v_def IN
    SELECT pg_get_functiondef(p.oid)
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public'
       AND p.proname IN ('session_key_mint', 'provision_api_key', 'recover_team_key')
  LOOP
    PERFORM tests.assert(
      v_def ILIKE '%public.organizations%FOR NO KEY UPDATE%',
      'every cap-writing RPC must lock the organizations row FOR NO KEY UPDATE');
  END LOOP;
END $$;

-- ============================================================================
-- SECTION 3 — api_key_slot_count: the ONE SQL declaration of the predicate
-- ============================================================================
DO $$ DECLARE
  v_org text := 'org-1879-pred';
  v_now timestamptz := now();
BEGIN
  INSERT INTO public.organizations (id, name, graph_name) VALUES (v_org, 'Pred 1879', 'org_org-1879-pred');

  INSERT INTO public.api_keys (id, org_id, lookup_hash, created_via, created_by, expires_at, revoked_at) VALUES
    ('k1879-live',     v_org, 'lu1879-live',     'provisioned', 'u-1879-a', NULL,                       NULL),
    ('k1879-revoked',  v_org, 'lu1879-revoked',  'provisioned', 'u-1879-a', NULL,                       v_now - interval '1 hour'),
    ('k1879-expired',  v_org, 'lu1879-expired',  'provisioned', 'u-1879-a', v_now - interval '1 hour',   NULL),
    ('k1879-boot',     v_org, 'lu1879-boot',     'bootstrap',   'u-1879-a', v_now + interval '12 hours', NULL),
    ('k1879-legacy',   v_org, 'lu1879-legacy',   'provisioned', NULL,       NULL,                       NULL);

  -- live + legacy NULL created_via count (fail-closed); revoked, expired and
  -- bootstrap do not.
  PERFORM tests.assert(
    public.api_key_slot_count(v_org, v_now) = 2,
    'api_key_slot_count counts a legacy unowned (created_by IS NULL) durable row');
  -- An expired-but-unrevoked row must NOT hold a slot (#2426).
  PERFORM tests.assert(
    public.api_key_slot_count(v_org, v_now + interval '13 hours') = 2,
    'an expired bootstrap row still does not count (and the live durable rows remain)');

  DELETE FROM public.api_keys WHERE org_id = v_org;
  DELETE FROM public.organizations WHERE id = v_org;
END $$;

-- ============================================================================
-- SECTION 4 — session_key_mint: bootstrap cap / recovery tiers / re-check
-- ============================================================================
DO $$ DECLARE
  v_org    text := 'org-1879-sess';
  v_user   text := 'u-1879-owner';
  v_other  text := 'u-1879-other';
  v_now    timestamptz := now();
  v_res    jsonb;
  v_raised boolean;
  v_count  integer;
BEGIN
  INSERT INTO public.organizations (id, name, graph_name) VALUES (v_org, 'Sess 1879', 'org_org-1879-sess');

  -- ── Bootstrap carries its OWN 3-active cap, per user ────────────────────
  FOR i IN 1..3 LOOP
    PERFORM public.session_key_mint(
      v_org, v_user, 'bootstrap', 'k1879-b' || i, 'lu1879-b' || i, 'tt_b' || i,
      v_now, v_now + interval '24 hours', 2, 3);
  END LOOP;
  v_raised := false;
  BEGIN
    PERFORM public.session_key_mint(
      v_org, v_user, 'bootstrap', 'k1879-b4', 'lu1879-b4', 'tt_b4',
      v_now, v_now + interval '24 hours', 2, 3);
  EXCEPTION WHEN OTHERS THEN v_raised := true;
  END;
  PERFORM tests.assert(v_raised, 'a 4th active bootstrap key must fail the 3-active cap');
  -- bootstrap rows are cap-EXEMPT: they must not consume a max_api_keys slot.
  PERFORM tests.assert(
    public.api_key_slot_count(v_org, v_now) = 0,
    'bootstrap rows never consume a max_api_keys slot');

  -- ── Recovery under cap: a plain persistent mint, rotated=false ─────────
  v_res := public.session_key_mint(
    v_org, v_user, 'recovery', 'k1879-r1', 'lu1879-r1', 'tt_r1', v_now, NULL, 2, 3);
  PERFORM tests.assert(v_res->>'rotated' = 'false', 'an under-cap recovery does not rotate');
  PERFORM tests.assert(
    (SELECT expires_at IS NULL FROM public.api_keys WHERE id = 'k1879-r1'),
    'a recovery key is persistent (no expiry)');
  PERFORM tests.assert(
    (SELECT created_via FROM public.api_keys WHERE id = 'k1879-r1') = 'recovery',
    'a recovery key has created_via=recovery');
  PERFORM tests.assert(
    (SELECT created_by FROM public.api_keys WHERE id = 'k1879-r1') = v_user,
    'a recovery key is attributed to the caller');

  -- ── At cap with ANOTHER user's key: revoke the oldest OTHER, no rotation ─
  INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix, created_via, created_by, created_at)
  VALUES ('k1879-other', v_org, 'lu1879-other', 'tt_other', 'recovery', v_other, v_now - interval '2 days');
  v_res := public.session_key_mint(
    v_org, v_user, 'recovery', 'k1879-r2', 'lu1879-r2', 'tt_r2', v_now, NULL, 2, 3);
  PERFORM tests.assert(v_res->>'rotated' = 'false', 'the others branch does not report a rotation');
  PERFORM tests.assert(
    (SELECT revoked_at IS NOT NULL FROM public.api_keys WHERE id = 'k1879-other'),
    'at cap, the oldest OTHER key is revoked');
  PERFORM tests.assert(
    public.api_key_slot_count(v_org, v_now) = 2,
    'the others branch leaves the org AT its cap, never above');

  -- ── #750.10: the caller's OWN provisioned keys are never revoked ────────
  -- Fill the cap with the caller's own provisioned keys and nothing else is
  -- rotatable -> fail CLOSED (the 402 path), never mint over cap.
  DELETE FROM public.api_keys WHERE org_id = v_org;
  INSERT INTO public.api_keys (id, org_id, lookup_hash, created_via, created_by, created_at)
  VALUES ('k1879-own1', v_org, 'lu1879-own1', 'provisioned', v_user, v_now - interval '2 days'),
         ('k1879-own2', v_org, 'lu1879-own2', 'provisioned', v_user, v_now - interval '1 day');
  v_raised := false;
  BEGIN
    PERFORM public.session_key_mint(
      v_org, v_user, 'recovery', 'k1879-r3', 'lu1879-r3', 'tt_r3', v_now, NULL, 2, 3);
  EXCEPTION WHEN OTHERS THEN v_raised := true;
  END;
  PERFORM tests.assert(v_raised, 'at cap with only own provisioned keys the mint fails CLOSED');
  PERFORM tests.assert(
    (SELECT bool_and(revoked_at IS NULL) FROM public.api_keys WHERE org_id = v_org),
    'a fail-closed refusal revokes nothing');
  PERFORM tests.assert(
    public.api_key_slot_count(v_org, v_now) = 2,
    'a fail-closed refusal never overshoots the cap');

  -- ── Rotation tier 1: a LEGACY unowned key (created_by IS NULL) ──────────
  -- (#1859 P3-1: legacy rows are NOT "others" — they take the rotation branch
  -- and ARE rotatable, unlike another user's key which is revoked instead.)
  DELETE FROM public.api_keys WHERE org_id = v_org;
  INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix, created_via, created_by, created_at)
  VALUES ('k1879-legacy', v_org, 'lu1879-legacy', 'tt_legacy', 'provisioned', NULL, v_now - interval '2 days'),
         ('k1879-own3',   v_org, 'lu1879-own3',   'tt_own3',   'provisioned', v_user, v_now - interval '1 day');
  v_res := public.session_key_mint(
    v_org, v_user, 'recovery', 'k1879-r4', 'lu1879-r4', 'tt_r4', v_now, NULL, 2, 3);
  PERFORM tests.assert(v_res->>'rotated' = 'true', 'rotating a legacy key reports rotated=true');
  PERFORM tests.assert(v_res->>'rotated_key_prefix' = 'tt_legacy',
                       'the rotated key prefix names the rotated victim');
  PERFORM tests.assert(
    (SELECT revoked_at IS NOT NULL FROM public.api_keys WHERE id = 'k1879-legacy'),
    'tier 1 rotates the LEGACY unowned key');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE id = 'k1879-own3'),
    '#750.10: the caller own key is untouched while a legacy key is rotatable');

  -- ── Rotation tier 2: own recovery key, LEAST-RECENTLY-USED first ────────
  DELETE FROM public.api_keys WHERE org_id = v_org;
  INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix, created_via, created_by, created_at, last_used_at)
  VALUES ('k1879-lru',  v_org, 'lu1879-lru',  'tt_lru',  'recovery', v_user, v_now - interval '3 days', v_now - interval '1 minute'),
         ('k1879-never',v_org, 'lu1879-never','tt_never','recovery', v_user, v_now - interval '2 days', NULL),
         ('k1879-own4', v_org, 'lu1879-own4', 'tt_own4', 'provisioned', v_user, v_now - interval '1 day', NULL);
  v_res := public.session_key_mint(
    v_org, v_user, 'recovery', 'k1879-r5', 'lu1879-r5', 'tt_r5', v_now, NULL, 3, 3);
  PERFORM tests.assert(
    v_res->>'rotated_key_prefix' = 'tt_never',
    '#1854: a NEVER-USED recovery key is rotated before a live recently-used one');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE id = 'k1879-lru'),
    '#1854: a recently-used recovery credential is NOT the rotation victim');

  -- ── Rotation tier 3: an EXPIRED own bootstrap is SELECTED, and — because a
  -- bootstrap row never counted against the cap — the revoke frees no slot,
  -- so the P2-1 re-check fails the mint CLOSED instead of minting cap+1.
  DELETE FROM public.api_keys WHERE org_id = v_org;
  INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix, created_via, created_by, created_at, expires_at)
  VALUES ('k1879-expboot', v_org, 'lu1879-expboot', 'tt_expboot', 'bootstrap', v_user, v_now - interval '3 days', v_now - interval '2 days'),
         ('k1879-own5',    v_org, 'lu1879-own5',    'tt_own5',    'provisioned', v_user, v_now - interval '1 day', NULL);
  PERFORM tests.assert(public.api_key_slot_count(v_org, v_now) = 1, 'only the durable key counts (cap=1)');
  v_raised := false;
  BEGIN
    PERFORM public.session_key_mint(
      v_org, v_user, 'recovery', 'k1879-r6', 'lu1879-r6', 'tt_r6', v_now, NULL, 1, 3);
  EXCEPTION WHEN OTHERS THEN v_raised := true;
  END;
  PERFORM tests.assert(
    v_raised,
    'a bootstrap rotation that frees no durable slot must fail CLOSED (P2-1), never mint cap+1');
  PERFORM tests.assert(
    public.api_key_slot_count(v_org, v_now) = 1,
    'the fail-closed bootstrap rotation leaves the cap intact');
  PERFORM tests.assert(
    (SELECT revoked_at IS NULL FROM public.api_keys WHERE id = 'k1879-own5'),
    '#750.10: the caller own provisioned key survives the failed bootstrap rotation');

  -- ── NULL cap = UNLIMITED (never defaulted to a number in SQL) ───────────
  DELETE FROM public.api_keys WHERE org_id = v_org;
  INSERT INTO public.api_keys (id, org_id, lookup_hash, created_via, created_by, created_at)
  VALUES ('k1879-u1', v_org, 'lu1879-u1', 'provisioned', v_user, v_now),
         ('k1879-u2', v_org, 'lu1879-u2', 'provisioned', v_user, v_now),
         ('k1879-u3', v_org, 'lu1879-u3', 'provisioned', v_user, v_now);
  v_res := public.session_key_mint(
    v_org, v_user, 'recovery', 'k1879-r7', 'lu1879-r7', 'tt_r7', v_now, NULL, NULL, 3);
  PERFORM tests.assert(v_res->>'rotated' = 'false',
                       'a NULL (unlimited) cap skips the cap gate and never rotates');
  PERFORM tests.assert(
    public.api_key_slot_count(v_org, v_now) = 4,
    'a NULL cap admits the mint without revoking anything');

  -- ── Fail closed on an unknown org (zero-row lock) ───────────────────────
  v_raised := false;
  BEGIN
    PERFORM public.session_key_mint(
      'org-1879-does-not-exist', v_user, 'recovery', 'k1879-x', 'lu1879-x', 'tt_x',
      v_now, NULL, 2, 3);
  EXCEPTION WHEN OTHERS THEN v_raised := true;
  END;
  PERFORM tests.assert(v_raised, 'session_key_mint must fail closed on an unknown org');

  DELETE FROM public.api_keys WHERE org_id = v_org;
  DELETE FROM public.organizations WHERE id = v_org;
END $$;

-- ============================================================================
-- SECTION 5 — the OTHERS-branch fail-closed re-check (interleaving E)
-- ============================================================================
-- Deterministic model of the race, not a thread race (PGlite is single-
-- connection): rotate's claim-revoke runs in its OWN transaction and can
-- revoke this mint's target between the count and the revoke, so the revoke
-- frees no slot. A test-only AFTER UPDATE trigger inserts a LIVE replacement
-- whenever a row is revoked — i.e. the concurrent writer's already-committed
-- effect. Without the re-check the mint inserts anyway (count = cap+1); with
-- it the mint fails closed and its transaction rolls the trigger row back.
DO $$ DECLARE
  v_org    text := 'org-1879-race';
  v_user   text := 'u-1879-race-owner';
  v_other  text := 'u-1879-race-other';
  v_now    timestamptz := now();
  v_raised boolean;
  v_count  integer;
BEGIN
  INSERT INTO public.organizations (id, name, graph_name) VALUES (v_org, 'Race 1879', 'org_org-1879-race');
  INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix, created_via, created_by, created_at)
  VALUES ('k1879-race-other', v_org, 'lu1879-race-other', 'tt_race_other', 'recovery',  v_other, v_now - interval '2 days'),
         ('k1879-race-own',   v_org, 'lu1879-race-own',   'tt_race_own',   'provisioned', v_user,  v_now - interval '1 day');
  PERFORM tests.assert(public.api_key_slot_count(v_org, v_now) = 2, 'race fixture sits AT cap=2');

  -- The concurrent writer's effect, applied inside the mint's transaction.
  EXECUTE $tg$
    CREATE OR REPLACE FUNCTION tests.simulate_concurrent_claim_1879() RETURNS trigger
    LANGUAGE plpgsql AS $fn$
    BEGIN
      INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix, created_via, created_by, created_at)
      VALUES ('k1879-race-repl', NEW.org_id, 'lu1879-race-repl', 'tt_race_repl', 'provisioned', 'u-1879-race-other', now());
      RETURN NEW;
    END $fn$;
  $tg$;
  CREATE TRIGGER trg_1879_sim_claim AFTER UPDATE ON public.api_keys
    FOR EACH ROW WHEN (OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL)
    EXECUTE FUNCTION tests.simulate_concurrent_claim_1879();

  v_raised := false;
  BEGIN
    PERFORM public.session_key_mint(
      v_org, v_user, 'recovery', 'k1879-race-mint', 'lu1879-race-mint', 'tt_race_mint',
      v_now, NULL, 2, 3);
  EXCEPTION WHEN OTHERS THEN v_raised := true;
  END;
  PERFORM tests.assert(
    v_raised,
    'the others branch must RE-CHECK after its revoke: a revoke that freed no slot (the target was concurrently revoked) must fail closed, not mint');

  DROP TRIGGER trg_1879_sim_claim ON public.api_keys;
  DROP FUNCTION tests.simulate_concurrent_claim_1879();

  SELECT public.api_key_slot_count(v_org, v_now) INTO v_count;
  PERFORM tests.assert(
    v_count <= 2,
    'after the fail-closed refusal the org is still at or below its cap');

  DELETE FROM public.api_keys WHERE org_id = v_org;
  DELETE FROM public.organizations WHERE id = v_org;
END $$;

-- ============================================================================
-- SECTION 6 — provision_api_key: cap gate, rotate credit, NULL cap, collision
-- ============================================================================
DO $$ DECLARE
  v_org    text := 'org-1879-prov';
  v_user   text := 'u-1879-prov';
  v_now    timestamptz := now();
  v_raised boolean;
BEGIN
  INSERT INTO public.organizations (id, name, graph_name) VALUES (v_org, 'Prov 1879', 'org_org-1879-prov');
  INSERT INTO public.api_keys (id, org_id, lookup_hash, created_via, created_by, created_at)
  VALUES ('k1879-p1', v_org, 'lu1879-p1', 'provisioned', v_user, v_now - interval '1 day'),
         ('k1879-p2', v_org, 'lu1879-p2', 'provisioned', v_user, v_now);

  -- AT cap=2 -> refuse (this is the plain POST /v1/team/keys 402 path).
  v_raised := false;
  BEGIN
    PERFORM public.provision_api_key(
      v_org, 'k1879-p3', 'lu1879-p3', 'tt_p3', 'provisioned', v_user, v_now, NULL,
      'label', NULL, '[]'::jsonb, NULL, NULL, 2, 0);
  EXCEPTION WHEN OTHERS THEN v_raised := true;
  END;
  PERFORM tests.assert(v_raised, 'provision_api_key must refuse at the cap');
  PERFORM tests.assert(public.api_key_slot_count(v_org, v_now) = 2,
                       'a refused provisioned mint inserts nothing');

  -- #4355: cap_slot_credit=1 admits a 1-for-1 replacement at N/N.
  PERFORM public.provision_api_key(
    v_org, 'k1879-p4', 'lu1879-p4', 'tt_p4', 'provisioned', v_user, v_now, NULL,
    'replacement', NULL, '[]'::jsonb, NULL, NULL, 2, 1);
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE id = 'k1879-p4') = 1,
    'cap_slot_credit=1 admits the replacement-aware rotate at cap');
  PERFORM tests.assert(
    (SELECT scopes FROM public.api_keys WHERE id = 'k1879-p4') = '[]'::jsonb,
    'provision_api_key stores the scopes jsonb verbatim');

  -- C1 columns round-trip (a minted deleg=0 child satisfies the escalation CHECK).
  PERFORM public.provision_api_key(
    v_org, 'k1879-p5', 'lu1879-p5', 'tk_p5', 'provisioned', 'api', v_now, v_now + interval '30 days',
    'child', NULL, '["graphs:read"]'::jsonb, 'k1879-p4', 0, NULL, 0);
  PERFORM tests.assert(
    (SELECT delegation_depth FROM public.api_keys WHERE id = 'k1879-p5') = 0,
    'provision_api_key stores delegation_depth');
  PERFORM tests.assert(
    (SELECT created_by_key_id FROM public.api_keys WHERE id = 'k1879-p5') = 'k1879-p4',
    'provision_api_key stores the mint lineage');
  PERFORM tests.assert(
    (SELECT name FROM public.api_keys WHERE id = 'k1879-p5') = 'child',
    'provision_api_key stores the key label');
  PERFORM tests.assert(
    (SELECT expires_at IS NOT NULL FROM public.api_keys WHERE id = 'k1879-p5'),
    'provision_api_key stores expiry');

  -- A NULL cap is UNLIMITED.
  PERFORM public.provision_api_key(
    v_org, 'k1879-p6', 'lu1879-p6', 'tt_p6', 'provisioned', v_user, v_now, NULL,
    NULL, NULL, '[]'::jsonb, NULL, NULL, NULL, 0);
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE id = 'k1879-p6') = 1,
    'a NULL (unlimited) cap admits the mint');

  -- A lookup_hash collision must RAISE (never a silent no-op / orphan plaintext).
  v_raised := false;
  BEGIN
    PERFORM public.provision_api_key(
      v_org, 'k1879-p7', 'lu1879-p1', 'tt_p7', 'provisioned', v_user, v_now, NULL,
      NULL, NULL, '[]'::jsonb, NULL, NULL, NULL, 0);
  EXCEPTION WHEN OTHERS THEN v_raised := true;
  END;
  PERFORM tests.assert(v_raised, 'provision_api_key must RAISE on a lookup_hash collision');

  v_raised := false;
  BEGIN
    PERFORM public.provision_api_key(
      'org-1879-nope', 'k1879-p8', 'lu1879-p8', 'tt_p8', 'provisioned', v_user, v_now, NULL,
      NULL, NULL, '[]'::jsonb, NULL, NULL, NULL, 0);
  EXCEPTION WHEN OTHERS THEN v_raised := true;
  END;
  PERFORM tests.assert(v_raised, 'provision_api_key must fail closed on an unknown org');

  DELETE FROM public.api_keys WHERE org_id = v_org;
  DELETE FROM public.organizations WHERE id = v_org;
END $$;

-- ============================================================================
-- SECTION 7 — recover_team_key still works with the added org lock
-- ============================================================================
DO $$ DECLARE
  v_token text := 'ab' || repeat('ef', 31);
  v_org   text := 'org-1879-rec';
  v_count integer;
BEGIN
  INSERT INTO public.organizations (id, name, graph_name) VALUES (v_org, 'Rec 1879', 'org_org-1879-rec');
  INSERT INTO public.agent_signup_tokens (token_hash, org_id) VALUES (v_token, v_org);
  INSERT INTO public.api_keys (id, org_id, lookup_hash, created_via, created_by, created_at)
  VALUES ('k1879-rb1', v_org, 'lu1879-rb1', 'provisioned', 'u-1879-rec', now() - interval '2 days'),
         ('k1879-rb2', v_org, 'lu1879-rb2', 'provisioned', 'u-1879-rec', now() - interval '1 day');

  PERFORM tests.assert(
    public.recover_team_key(v_token, v_org, 'lu1879-rk1', 'tt_rk1', 2) = v_org,
    'recover_team_key still mints with the org lock in place');
  SELECT count(*) INTO v_count FROM public.api_keys
   WHERE org_id = v_org AND revoked_at IS NULL
     AND (created_via IS NULL OR created_via <> 'bootstrap');
  PERFORM tests.assert(v_count <= 2, 'recover_team_key cannot overshoot the cap');

  -- Idempotent retry: the same lookup_hash is a NO-OP and revokes nothing.
  PERFORM public.recover_team_key(v_token, v_org, 'lu1879-rk1', 'tt_rk1', 2);
  PERFORM tests.assert(
    (SELECT count(*) FROM public.api_keys WHERE org_id = v_org) = 3,
    'a retried recovery with the same lookup_hash inserts no second row');

  DELETE FROM public.agent_signup_tokens WHERE org_id = v_org;
  DELETE FROM public.api_keys WHERE org_id = v_org;
  DELETE FROM public.organizations WHERE id = v_org;
END $$;

-- ── Final cleanup (idempotent re-runs) ─────────────────────────────────────
DELETE FROM public.api_keys WHERE org_id LIKE '%-1879%';
DELETE FROM public.org_memberships WHERE org_id LIKE '%-1879%';
DELETE FROM public.organizations WHERE id LIKE '%-1879%';

-- ============================================================================
-- SECTION 8 — fail-closed OPERANDS (review P2): a NULL operand inside a cap
-- gate must not bypass it, and a bootstrap row must carry an expiry
-- ============================================================================
-- plpgsql semantics: `x >= NULL` is NULL and `IF NULL` is NOT taken, so a bare
-- comparison against a NULL operand SILENTLY SKIPS the gate — the wrong
-- polarity for a cap. COALESCE closes it; these assertions pin that.
DO $$ DECLARE
  v_org  text := 'org-1879-nullop';
  v_rows integer;
BEGIN
  INSERT INTO public.organizations (id, name, graph_name)
  VALUES (v_org, 'NullOp 1879', 'org_org-1879-nullop');
  INSERT INTO public.api_keys (id, org_id, lookup_hash, created_via, created_by, created_at)
  VALUES ('k1879-np1', v_org, 'lu1879-np1', 'recovery', 'u-1879-np', now()),
         ('k1879-np2', v_org, 'lu1879-np2', 'recovery', 'u-1879-np', now());

  -- (a) NULL p_bootstrap_cap must still enforce the DEFAULT 3-active cap, and a
  --     bootstrap row with no expiry must RAISE (a cap-exempt key with no
  --     expiry is outside every cap forever).
  BEGIN
    PERFORM public.session_key_mint(v_org, 'u-1879-np', 'bootstrap',
        'k1879-np-b1', 'lu1879-np-b1', 'tt_npb1', now(), NULL, NULL, NULL);
    PERFORM tests.assert(false, 'bootstrap with NULL expiry must RAISE');
  EXCEPTION WHEN others THEN
    PERFORM tests.assert(SQLERRM LIKE '%bootstrap requires an expiry%',
                         'bootstrap NULL-expiry RAISE carries its reason: ' || SQLERRM);
  END;

  -- (b) NULL p_cap_slot_credit must mean 0 (not "skip the gate"): at cap, a
  --     provisioned mint with a NULL credit is still REFUSED.
  BEGIN
    PERFORM public.provision_api_key(v_org, 'k1879-np-p1', 'lu1879-np-p1',
        'tt_npp1', 'provisioned', 'u-1879-np', now(), NULL, NULL, NULL, '[]'::jsonb,
        NULL, NULL, 2, NULL);
    PERFORM tests.assert(false, 'NULL cap_slot_credit must not bypass the gate');
  EXCEPTION WHEN others THEN
    PERFORM tests.assert(SQLERRM LIKE '%provision_api_key: key cap reached%',
                         'NULL credit still refuses at cap: ' || SQLERRM);
  END;

  -- (c) ...and with the credit honoured the same call succeeds (so (b) is not
  --     passing for an unrelated reason). #4355's contract: the credit says
  --     "the caller is revoking one counted row right after this call", so the
  --     insert lands at count+1 and the caller's revoke-claim brings it back.
  PERFORM public.provision_api_key(v_org, 'k1879-np-p2', 'lu1879-np-p2',
      'tt_npp2', 'provisioned', 'u-1879-np', now(), NULL, NULL, NULL, '[]'::jsonb,
      NULL, NULL, 2, 1);
  SELECT count(*) INTO v_rows FROM public.api_keys
   WHERE org_id = v_org AND revoked_at IS NULL
     AND (created_via IS NULL OR created_via <> 'bootstrap');
  PERFORM tests.assert(v_rows = 3,
      'a credit-aware provision inserts (count + 1) — the caller owes a revoke');
  -- the caller's displaced-row revoke (the #4355 rotate tail)
  UPDATE public.api_keys SET revoked_at = now() WHERE id = 'k1879-np1';
  SELECT count(*) INTO v_rows FROM public.api_keys
   WHERE org_id = v_org AND revoked_at IS NULL
     AND (created_via IS NULL OR created_via <> 'bootstrap');
  PERFORM tests.assert(v_rows <= 2,
      'credit-aware provision + the owed revoke ends at or under the cap');

  DELETE FROM public.api_keys WHERE org_id = v_org;
  DELETE FROM public.organizations WHERE id = v_org;
END $$;
