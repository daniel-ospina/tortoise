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
--   3. two CHECK constraints: the state vocabulary, and the invariant that the
--      new state can never drift from `used_at`
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
-- INVARIANT (enforced, ck_oauth_codes_redemption_used_at):
--   (redemption_state = 'unclaimed') = (used_at IS NULL)
-- `used_at` STAYS the claim timestamp, so every #2863/#3036 read and test keeps
-- working unchanged; the state simply cannot drift away from it.
--
-- NO DANGLING-POINTER REPAIR is needed (unlike #3036): the code_id columns are
-- NEW and NULLable, so no pre-existing row can carry a dangling value.
--
-- Lock footprint (a bare "additive" header under-counts it): each `ALTER TABLE
-- ... ADD COLUMN` takes ACCESS EXCLUSIVE; `ADD COLUMN ... DEFAULT` on PG11+ is a
-- metadata-only fast default (no rewrite) but still holds the lock. Each
-- `DROP CONSTRAINT IF EXISTS` takes ACCESS EXCLUSIVE on its table. The two CHECK
-- adds take ACCESS EXCLUSIVE on `oauth_codes` (they have no referenced table);
-- each validated `ADD FOREIGN KEY` takes SHARE ROW EXCLUSIVE on BOTH the
-- referencing and the referenced table plus a full validation scan. So both
-- `oauth_codes` CHECKs block READS and writes, while the FK adds block writes
-- only. The backfill UPDATE scans oauth_codes once. Each CREATE INDEX is
-- NON-concurrent and locks out writes (not reads) on its token table until the
-- build finishes.
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

-- 2) Backfill BEFORE the agreement CHECK: a historical row with used_at set
--    carries the new default 'unclaimed', which the biconditional CHECK below
--    would reject. Treat every already-consumed code as the fail-safe terminal
--    `burned`: they were redeemed before this state existed and we cannot prove
--    they minted, so they must never be re-armed. `redemption_settled_at`
--    records the only instant we know — the original consumption.
--    Idempotent: after the first run the state is no longer 'unclaimed'.
UPDATE public.oauth_codes
   SET redemption_state = 'burned',
       redemption_settled_at = used_at,
       redemption_note = 'backfill'
 WHERE used_at IS NOT NULL
   AND redemption_state = 'unclaimed';

-- 3) The constraints. DROP-then-ADD keeps a re-apply idempotent (ADD COLUMN
--    IF NOT EXISTS makes the columns, not the CHECKs).
ALTER TABLE public.oauth_codes
    DROP CONSTRAINT IF EXISTS ck_oauth_codes_redemption_state;
ALTER TABLE public.oauth_codes
    ADD CONSTRAINT ck_oauth_codes_redemption_state
    CHECK (redemption_state IN ('unclaimed', 'claimed', 'minted', 'burned'));

ALTER TABLE public.oauth_codes
    DROP CONSTRAINT IF EXISTS ck_oauth_codes_redemption_used_at;
ALTER TABLE public.oauth_codes
    ADD CONSTRAINT ck_oauth_codes_redemption_used_at
    CHECK ((redemption_state = 'unclaimed') = (used_at IS NULL));

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
