-- ============================================================================
-- SQL-level verification for migration 20261027120001 (#3524 §9 step 1)
-- The consent token's single-use register: `consume_oauth_consent_ticket`.
--
-- WHAT THIS SUITE IS FOR. The migration's whole value is that the SINGLE-USE
-- DECISION IS MADE BY THE PRIMARY KEY, not by a read-then-write, and that the
-- table/function are not anon-writable. Both are invisible to the Python tests:
-- `tests/fake_control_plane.py` emulates the claim in ONE thread, and its own
-- comment concedes that "a lane that re-implemented the store as
-- SELECT-then-INSERT would still pass here". So the two assertions that carry
-- this suite are:
--
--   * SECTION 2 — the claim: first redemption true, every later one false, and
--     exactly ONE row. Deleting `ON CONFLICT` (or its `RETURNING`) reds it.
--   * SECTION 1 — the privileges: `anon`/`authenticated` must hold neither
--     EXECUTE on the function nor DELETE on the table, RLS must be on, and the
--     service_role policy must exist. The anon key is PUBLIC (the consent page
--     embeds it in HTML), so a reader holding it could otherwise delete a spent
--     nonce and replay the token into a SECOND authorization code — the exact
--     replay §2.2's `jti` exists to bound.
--
-- HONEST LIMIT: PGlite is SINGLE-CONNECTION, so this suite cannot stage two
-- genuinely concurrent claimants. The atomicity that matters is structural
-- (`INSERT … ON CONFLICT DO NOTHING RETURNING` is one statement, so the loser
-- gets NULL) and is covered functionally by the repeat/replay assertions below.
-- The same limit is recorded by the `redeem_oauth_code` suite (#3025), whose CAS
-- this migration mirrors.
--
-- HOW TO RUN (no Docker — PGlite harness):
--   npm --prefix supabase/tests/pglite run validate
--
-- Every assertion RAISEs on failure; with ON_ERROR_STOP=1 any failure exits
-- non-zero. Test rows use the '3524-' prefix for safe cleanup.
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

-- ── Cleanup any prior rows (idempotent re-runs) ────────────────────────────
DELETE FROM public.oauth_consent_tickets WHERE jti LIKE '3524-%';

-- ============================================================================
-- SECTION 1 — privilege hygiene: service_role ONLY, and RLS actually ON
-- ----------------------------------------------------------------------------
-- `has_table_privilege` reports the GRANT and does NOT see RLS, so both halves
-- are asserted separately: the grants (which this migration REVOKEs) and
-- `relrowsecurity`. Asserting only one of them would leave the other removable
-- with the suite green.
-- ============================================================================
DO $$ BEGIN
  PERFORM tests.assert(
    NOT has_function_privilege(
      'anon', 'public.consume_oauth_consent_ticket(text,timestamptz)', 'EXECUTE'),
    'anon must NOT EXECUTE consume_oauth_consent_ticket — it could pre-spend nonces');
  PERFORM tests.assert(
    NOT has_function_privilege(
      'authenticated', 'public.consume_oauth_consent_ticket(text,timestamptz)', 'EXECUTE'),
    'authenticated must NOT EXECUTE consume_oauth_consent_ticket');
  PERFORM tests.assert(
    has_function_privilege(
      'service_role', 'public.consume_oauth_consent_ticket(text,timestamptz)', 'EXECUTE'),
    'service_role must EXECUTE consume_oauth_consent_ticket (the AS is the only caller)');

  PERFORM tests.assert(
    NOT has_table_privilege('anon', 'public.oauth_consent_tickets', 'DELETE'),
    'anon must NOT DELETE from oauth_consent_tickets — deleting a spent nonce replays it');
  PERFORM tests.assert(
    NOT has_table_privilege('anon', 'public.oauth_consent_tickets', 'INSERT'),
    'anon must NOT INSERT into oauth_consent_tickets (far-future rows never pruned)');
  PERFORM tests.assert(
    NOT has_table_privilege('authenticated', 'public.oauth_consent_tickets', 'DELETE'),
    'authenticated must NOT DELETE from oauth_consent_tickets');
  PERFORM tests.assert(
    has_table_privilege('service_role', 'public.oauth_consent_tickets', 'INSERT'),
    'service_role must INSERT into oauth_consent_tickets');

  PERFORM tests.assert(
    (SELECT relrowsecurity FROM pg_class
      WHERE relname = 'oauth_consent_tickets'
        AND relnamespace = 'public'::regnamespace),
    'RLS must be ENABLED on oauth_consent_tickets');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM pg_policies
             WHERE schemaname = 'public'
               AND tablename = 'oauth_consent_tickets'
               AND 'service_role' = ANY (roles)),
    'a service_role policy must exist, or every AS read/write is refused');
END $$;

-- The behavioural half: with the grants revoked, an anon DELETE must be refused
-- by Postgres itself rather than silently affecting zero rows (a silent 0 is
-- also "blocked", but it would hide a table-wide GRANT that RLS happened to cap).
DO $$
DECLARE
  refused boolean := false;
  seeded  boolean;
BEGIN
  seeded := public.consume_oauth_consent_ticket('3524-anon-target', now() + interval '1 hour');
  PERFORM tests.assert(seeded, 'failed to seed the anon-delete probe row');
  BEGIN
    SET LOCAL ROLE anon;
    DELETE FROM public.oauth_consent_tickets WHERE jti = '3524-anon-target';
  EXCEPTION WHEN insufficient_privilege THEN
    refused := true;
  END;
  PERFORM tests.assert(refused,
    'anon DELETE was NOT refused — a public anon key can replay a captured consent token');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM public.oauth_consent_tickets WHERE jti = '3524-anon-target'),
    'the anon DELETE removed the row');
END $$;

-- ============================================================================
-- SECTION 2 — the single-use claim
-- ============================================================================
DO $$
DECLARE
  first_claim  boolean;
  second_claim boolean;
  third_claim  boolean;
  rows_        integer;
BEGIN
  first_claim := public.consume_oauth_consent_ticket('3524-single-use', now() + interval '1 hour');
  PERFORM tests.assert(first_claim, 'the FIRST redemption of a jti must answer true');

  second_claim := public.consume_oauth_consent_ticket('3524-single-use', now() + interval '1 hour');
  PERFORM tests.assert(NOT second_claim,
    'a REPLAY answered true — one consent would yield a second authorization code');

  third_claim := public.consume_oauth_consent_ticket('3524-single-use', now() + interval '2 hours');
  PERFORM tests.assert(NOT third_claim,
    'a replay with a LATER expiry answered true — the reservation must survive a re-stamp');

  SELECT count(*) INTO rows_ FROM public.oauth_consent_tickets WHERE jti = '3524-single-use';
  PERFORM tests.assert(rows_ = 1,
    format('exactly one row must be recorded per jti, found %s', rows_));
END $$;

-- A blank or NULL nonce is refused rather than recorded: an empty PRIMARY KEY
-- would be claimed once and then answer "replay" to every malformed token.
DO $$
BEGIN
  PERFORM tests.assert(
    NOT public.consume_oauth_consent_ticket('', now() + interval '1 hour'),
    'a blank jti must be refused');
  PERFORM tests.assert(
    NOT public.consume_oauth_consent_ticket(NULL, now() + interval '1 hour'),
    'a NULL jti must be refused');
  PERFORM tests.assert(
    NOT EXISTS (SELECT 1 FROM public.oauth_consent_tickets WHERE jti = ''),
    'a blank jti was recorded — every later malformed token would read as a replay');
END $$;

-- ============================================================================
-- SECTION 3 — the prune boundary, driven rather than assumed
-- ----------------------------------------------------------------------------
-- The prune runs BEFORE the insert and deletes `expires_at < now()`. It must
-- therefore (a) leave a row whose token can still verify, (b) collect one whose
-- token cannot, and (c) never collect the row the same call is inserting.
-- (c) is why the store stamps `exp + LEEWAY` — see the migration's own note.
-- ============================================================================
DO $$
DECLARE
  live_row    boolean;
  future_row  boolean;
  expired_row boolean;
  probe_row   boolean;
BEGIN
  -- (a) A row stamped INSIDE the leeway window (the verifier still accepts it).
  live_row := public.consume_oauth_consent_ticket('3524-live', now() + interval '30 seconds');
  PERFORM tests.assert(live_row, 'a jti inside the leeway window must be claimable');

  future_row := public.consume_oauth_consent_ticket('3524-probe-a', now() + interval '1 hour');
  PERFORM tests.assert(future_row, 'a far-future jti must be claimable');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM public.oauth_consent_tickets WHERE jti = '3524-live'),
    'the prune deleted a row whose token can still verify — its jti is replayable again');

  -- (b) A row stamped in the past: its token can no longer verify, so the NEXT
  -- call's prune must collect it.
  expired_row := public.consume_oauth_consent_ticket('3524-dead', now() - interval '1 second');
  PERFORM tests.assert(expired_row, 'a past-stamped jti must still be claimable once');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM public.oauth_consent_tickets WHERE jti = '3524-dead'),
    'the prune ran before the insert and collected the row it was about to write');

  probe_row := public.consume_oauth_consent_ticket('3524-probe-b', now() + interval '1 hour');
  PERFORM tests.assert(probe_row, 'the prune must not refuse a first redemption');
  PERFORM tests.assert(
    NOT EXISTS (SELECT 1 FROM public.oauth_consent_tickets WHERE jti = '3524-dead'),
    'an expired row was kept — the register grows without bound');
  PERFORM tests.assert(
    EXISTS (SELECT 1 FROM public.oauth_consent_tickets WHERE jti = '3524-probe-b'),
    'the prune collected the row this same call inserted — the ordering is backwards');
END $$;

-- ── Cleanup ────────────────────────────────────────────────────────────────
DELETE FROM public.oauth_consent_tickets WHERE jti LIKE '3524-%';
