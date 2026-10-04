-- ============================================================================
-- Migration 20260925000002: OAuth authorization-code redemption state (#3027)
-- ----------------------------------------------------------------------------
-- 0016 gave `oauth_codes` exactly ONE consumption signal: `used_at`, a binary
-- null-or-set flag. It records that a request CLAIMED a code (the atomic CAS in
-- `_consume_code`) and nothing about that claim's OUTCOME. Two consequences:
--
--   * the server cannot durably ask "did this code already mint a pair?", so a
--     failure that lost its response is indistinguishable from a replay;
--   * no token row points back at the code that authorized it, so "did code C
--     mint a family?" is unanswerable from the grant side.
--
-- #2863 answers the reachable branches IN PROCESS (soft-revoke what it inserted
-- → confirm by SELECT → re-arm `used_at`). That confirmation DOES separate
-- "the redemption never committed" (`recovered=True`) from "it committed and the
-- compensating write also failed" (`recovered=False`) — while it can read the
-- tables, in that one request. What it cannot do is RECORD the answer: the
-- observation dies with the request, so no later attempt can replay it, and
-- inside its own read-failure window the confirmation is blind. This migration
-- supplies the durable record, so a claim `used_at` alone cannot describe
-- ("claimed, outcome unknown") becomes resolvable by a LATER request. #2863's
-- in-process confirmation is deliberately NOT removed here — the durable path is
-- introduced ALONGSIDE it and the removal is a follow-up step once the state
-- machine is proven.
--
-- WHAT THIS ADDS — additive only:
--   1. oauth_codes.redemption_state / redemption_id / redemption_settled_at /
--      redemption_note  (the state machine + its diagnostics)
--   2. a backfill: used_at IS NOT NULL → 'burned' (fail-safe terminal — a code
--      consumed before this state existed is NEVER re-armed)
--   3. two CHECK constraints: the state vocabulary, and the DIRECTIONAL
--      invariant that pins a state to `used_at` (see the boxed note below)
--   4. oauth_access_tokens.code_id / oauth_refresh_tokens.code_id — the
--      provenance link back to the authorizing code, plus one index each for
--      the reconciler's `code_id = C AND revoked_at IS NULL` probe
--
-- THE STATE MACHINE:
--   unclaimed → claimed → minted   (the pair was handed to the response)
--                       → burned   (terminal failure; never mints again)
--   claimed   → unclaimed          (a VERIFIED-CLEAN failure re-arms: this is
--                                   #2863's re-arm, now durably recorded)
--   claimed   → burned             (the reconciler resolves an outcome-unknown
--                                   claim past its grace window — revoking a
--                                   live orphan family first when one exists).
--                                   It NEVER re-arms: a past-grace claim is not
--                                   proof its owner is dead, and re-arming one
--                                   whose owner is alive mints TWO live families
--                                   for a single-use code. The no-family residue
--                                   is burned (fail safe).
--
-- ON DELETE POLICY — `SET NULL`, deliberately, for BOTH code_id FKs. This is
-- NOT a fresh choice: migration 20260925000001 (#3036) ratified `ON DELETE SET
-- NULL` for OAuth provenance FKs, and issue #3036's scoping comment carries the
-- OVERRIDES: line that explains why CASCADE is wrong here (the migration text
-- itself does not). An access token is an INDEPENDENT bearer credential
-- (D6 introspection), so CASCADE would silently revoke live tokens the moment a
-- DEAD code row was reaped by #3036's retention sweep; and a code can authorize
-- an entire rotation chain, so CASCADE would delete through it. SET NULL drops
-- only the provenance back-link. Do NOT "tidy" these back to CASCADE.
--
-- INVARIANT (enforced, chk_oauth_codes_redemption_used_at) — ONE DIRECTIONAL:
--   used_at IS NULL  ⇒  redemption_state = 'unclaimed'
-- written as `used_at IS NOT NULL OR redemption_state = 'unclaimed'`.
--
-- ⛔ DELIBERATELY NOT THE BICONDITIONAL. `(state='unclaimed') = (used_at IS NULL)`
-- was the first form, and it BREAKS THE DEPLOYED WRITER ON ITS NORMAL PATH: the
-- pre-#3027 `_consume_code` PATCHes `used_at` alone, leaving the DEFAULT
-- 'unclaimed', so `TRUE = FALSE` → 23514 on every authorization-code exchange.
-- `deploy-hosted`'s fail-closed migration-drift gate REQUIRES prod to hold this
-- migration before the new image ships, so the old writer is live against the new
-- schema for the whole rollout (and for any later app rollback), and
-- `ADD CONSTRAINT ... NOT VALID` does NOT help — PG still enforces a NOT VALID
-- check on INSERT/UPDATE (verified in PGlite).
-- The direction above is the one that MATTERS and the one the state machine can
-- rely on: no settled (`claimed`/`minted`/`burned`) row can exist without a claim
-- timestamp, so `_settle_redemption`'s `redemption_state='claimed'` CAS filter
-- always implies `used_at IS NOT NULL`. The relaxed direction is exactly the
-- shape `_observe_code` already tolerates (a row with `used_at` set and no state
-- is classified `claimed`, the reconcilable state), and such a row is inert for
-- the reconciler: its settle CAS matches nothing and returns `lost-race` without
-- revoking. The CLAIM and the RE-ARM write `used_at` and `redemption_state` in
-- ONE statement; a settle only transitions `redemption_state` on an
-- already-claimed row. So the biconditional holds for every write this codebase
-- makes.
-- ⛔ Do NOT "restore" the biconditional without an expand/contract rollout.
-- `used_at` STAYS the claim timestamp, so every #2863/#3036 read and test keeps
-- working unchanged.
--
-- NO DANGLING-POINTER REPAIR is needed (unlike #3036): the code_id columns are
-- NEW and NULLable, so no pre-existing row can carry a dangling value.
--
-- ── Lock footprint — HONEST, and worse than "additive" sounds ───────────────
-- `supabase db push` executes THIS WHOLE FILE INSIDE ONE TRANSACTION, so every
-- lock below is held until COMMIT — for every table it touches, not merely for
-- the duration of one statement:
--   * oauth_codes: `ALTER TABLE` (ADD COLUMN, DROP/ADD CONSTRAINT) takes ACCESS
--     EXCLUSIVE, so SELECTs are blocked as well as writes, for the whole file.
--     The CHECK adds additionally run a FULL VALIDATION SCAN of oauth_codes under
--     that lock (a pre-existing violating row aborts the migration).
--   * oauth_access_tokens / oauth_refresh_tokens: each `ADD COLUMN code_id` takes
--     ACCESS EXCLUSIVE on that table and holds it to COMMIT, so the MCP boundary's
--     per-request reads of these tables are blocked for the whole file — not
--     merely "writes blocked" by the index build.
--   * the FK adds take SHARE ROW EXCLUSIVE on the referencing AND the referenced
--     table (writes blocked, reads allowed) plus a full scan of the token table.
--   * `CREATE INDEX` (non-concurrent) takes SHARE: writes blocked, reads allowed.
-- So this is a short but REAL write-and-read blackout on all three OAuth tables.
-- Applying it in a quiet window is the operational requirement; there is no online
-- form for the ADD COLUMN / ADD CONSTRAINT pair.
-- ============================================================================

-- 1) The state machine + its diagnostics (additive, nullable except the state).
ALTER TABLE public.oauth_codes
    ADD COLUMN IF NOT EXISTS redemption_state text NOT NULL DEFAULT 'unclaimed';
ALTER TABLE public.oauth_codes
    ADD COLUMN IF NOT EXISTS redemption_id text;
ALTER TABLE public.oauth_codes
    ADD COLUMN IF NOT EXISTS redemption_settled_at timestamptz;
ALTER TABLE public.oauth_codes
    ADD COLUMN IF NOT EXISTS redemption_note text;

-- 2) Backfill: a historical row with used_at set carries the new default
--    'unclaimed', which the directional CHECK below PERMITS (see the box) — so
--    this UPDATE is not what makes the constraint addable. It is load-bearing as
--    DATA: those rows were redeemed before this state existed and we cannot prove
--    they minted, so they must never be READ as a live claim. Treat every
--    already-consumed code as the fail-safe terminal `burned`;
--    `redemption_settled_at` records the only instant we know — the original
--    consumption.
--    Idempotent: after the first run the state is no longer 'unclaimed'.
UPDATE public.oauth_codes
   SET redemption_state = 'burned',
       redemption_settled_at = used_at,
       redemption_note = 'backfill'
 WHERE used_at IS NOT NULL
   AND redemption_state = 'unclaimed';

-- 3) The constraints. DROP-then-ADD keeps a re-apply idempotent (ADD COLUMN
--    IF NOT EXISTS makes the columns, not the CHECKs). `chk_` is the repo's
--    CHECK-naming convention (0003, 0007-0010 and 20260915000001), and the
--    `ck_` DROPs remain so a database that already applied an earlier revision of
--    THIS file is cleaned up rather than left with a second, stricter CHECK.
ALTER TABLE public.oauth_codes
    DROP CONSTRAINT IF EXISTS ck_oauth_codes_redemption_state;
ALTER TABLE public.oauth_codes
    DROP CONSTRAINT IF EXISTS chk_oauth_codes_redemption_state;
ALTER TABLE public.oauth_codes
    ADD CONSTRAINT chk_oauth_codes_redemption_state
    CHECK (redemption_state IN ('unclaimed', 'claimed', 'minted', 'burned'));

ALTER TABLE public.oauth_codes
    DROP CONSTRAINT IF EXISTS ck_oauth_codes_redemption_used_at;
ALTER TABLE public.oauth_codes
    DROP CONSTRAINT IF EXISTS chk_oauth_codes_redemption_used_at;
ALTER TABLE public.oauth_codes
    ADD CONSTRAINT chk_oauth_codes_redemption_used_at
    CHECK (used_at IS NOT NULL OR redemption_state = 'unclaimed');

-- 4) The provenance link. Column first, then a NAMED constraint (mirrors
--    #3036's shape so a re-apply is idempotent).
ALTER TABLE public.oauth_access_tokens
    ADD COLUMN IF NOT EXISTS code_id bigint;
ALTER TABLE public.oauth_access_tokens
    DROP CONSTRAINT IF EXISTS fk_oauth_access_tokens_code;
ALTER TABLE public.oauth_access_tokens
    ADD CONSTRAINT fk_oauth_access_tokens_code
    FOREIGN KEY (code_id)
    REFERENCES public.oauth_codes (id)
    ON DELETE SET NULL;

ALTER TABLE public.oauth_refresh_tokens
    ADD COLUMN IF NOT EXISTS code_id bigint;
ALTER TABLE public.oauth_refresh_tokens
    DROP CONSTRAINT IF EXISTS fk_oauth_refresh_tokens_code;
ALTER TABLE public.oauth_refresh_tokens
    ADD CONSTRAINT fk_oauth_refresh_tokens_code
    FOREIGN KEY (code_id)
    REFERENCES public.oauth_codes (id)
    ON DELETE SET NULL;

-- 5) Reconciler indexes: "is a live family linked to code C?" is
--    `code_id = C AND revoked_at IS NULL` on both token tables.
CREATE INDEX IF NOT EXISTS idx_oauth_access_tokens_code
    ON public.oauth_access_tokens (code_id);
CREATE INDEX IF NOT EXISTS idx_oauth_refresh_tokens_code
    ON public.oauth_refresh_tokens (code_id);
