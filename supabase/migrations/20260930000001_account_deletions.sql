-- Migration 20260930000001: user-account soft-delete ledger (#4029)
--
-- Backs the self-service account-deletion flow. Deleting a personal account is
-- two-phase, mirroring the team path (#302): the request stamps `deleted_at` +
-- the grace window PROMISED at schedule time, and the boot + hourly purge
-- erases the account (GoTrue auth-user delete) once the STORED window elapses.
--
-- Why a ledger table rather than a column on an existing row: an account can
-- own zero orgs, so there is no `org_memberships`/`organizations` row to stamp.
-- `user_id` is the primary key, so a concurrent schedule can only land ONE row
-- (INSERT ... ON CONFLICT is not needed — the API read-then-inserts and the PK
-- turns a lost race into a 409 the caller treats as already-scheduled).
--
-- Deliberately NO foreign key to `auth.users`: this row is the purge's RETRY
-- ANCHOR. The purge deletes the auth user FIRST and this row LAST, so a partial
-- failure leaves the row in place for the next sweep; an ON DELETE CASCADE from
-- auth.users would silently drop the anchor the moment the auth user is erased.
-- The anchor has TWO retry shapes and the sweep handles both (#4029 review
-- P1-4): a STAMPED row past its stored grace is erased, while a STALE UNSTAMPED
-- row — the request died between the INSERT and the stamp — is COMPLETED
-- (re-derive, claim, cascade, then stamp) and left for a later sweep, because
-- the promised window starts at the stamp. So an un-stamped row is the anchor
-- for the CASCADE, not only for the erasure, and `deleted_at lte now` — which
-- SQL excludes NULL from — is NOT a sufficient selection on its own.
--
-- TWO-PHASE STAMP (code review of #4029): the row is INSERTed BEFORE the org
-- cascade runs, carrying `org_ids` — the set this deletion intends to cascade.
-- The cascade itself REMOVES the owner memberships `sole_owned_org_ids`
-- discovers by, so if it failed after that removal but before the org stamp the
-- retry could no longer rediscover the org by membership: the org would be left
-- un-stamped AND undiscoverable, its graph intact and its pending invitations
-- stranded. `deleted_at` / `grace_hours` stay NULL until the cascade COMPLETES
-- (stamped LAST), so a partial failure leaves the account un-stamped while the
-- intended org set survives — the same fail-closed ordering the per-org
-- cascade uses. An un-stamped row that outlives the in-flight bound
-- (`hosted_api.ACCOUNT_DELETION_IN_FLIGHT_SECONDS`) is therefore a COMPLETABLE
-- schedule, not a dead one: the sweep finishes the cascade and stamps it.
--
-- INTENT vs CLAIM (cycle-2 review of #4029). `org_ids` is a CACHE of ownership
-- — a fact that changes. Two review cycles produced one defect in each
-- direction from trusting it on the replay path: too narrow (a cascade that
-- removed the owner memberships left the org undiscoverable AND, if the anchor
-- write itself was the thing that failed, unanchored → orphaned) and too wide
-- (an org that lost sole ownership after the anchor was written was still
-- cascaded, stamping a team the caller does not solely own). `claimed_org_ids`
-- is the missing seam: the durable record of the orgs THIS deletion has already
-- BEGUN cascading, appended atomically by `account_deletion_claim_org`
-- immediately BEFORE each org's access-kill. A replay cascades ONLY orgs that
-- are still solely owned at replay time OR present in `claimed_org_ids`.
-- `org_ids` stays as the historical intent record; it never drives a cascade
-- on its own.
--
-- Additive only. Service-role reads/writes only (RLS deny-by-default); the
-- window itself is never stated here — the sole authority is
-- `tortoise/retention.py` (see docs/retention-and-deletion.md).

CREATE TABLE IF NOT EXISTS public.account_deletions (
    user_id     uuid PRIMARY KEY,
    -- The intended cascade set, persisted BEFORE the cascade. jsonb (not
    -- uuid[]) so the anchor survives an org id the control plane did not mint
    -- and never fails a write on a shape the rest of the row accepts.
    org_ids     jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- The CLAIM set: orgs whose cascade THIS deletion has durably begun.
    -- Appended atomically (DB-side union) before that org's access-kill.
    claimed_org_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- NULL until the cascade completes: written LAST so a partial cascade
    -- leaves the account un-stamped for a retry (see the header).
    deleted_at  timestamptz,
    grace_hours numeric,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Idempotent for a dev/CI DB that already ran an earlier revision of this
-- (unreleased) migration and therefore created the table without the column.
ALTER TABLE public.account_deletions
    ADD COLUMN IF NOT EXISTS claimed_org_ids jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE public.account_deletions ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.account_deletions FROM anon, authenticated, public;
GRANT ALL ON public.account_deletions TO service_role;

-- ============================================================================
-- account_deletion_claim_org — the atomic per-org claim (#4029, cycle 2).
--
-- Called immediately BEFORE one org's access-kill. Appends the org to BOTH
-- `claimed_org_ids` (the durable cascade gate a replay reads) and `org_ids`
-- (the historical intent record) in ONE statement, so two concurrent writers
-- can never drop each other's id — a read-then-PATCH computes each union from
-- an earlier read and silently loses one (the reproduced cycle-2 P2: stored
-- `[X]` read, A widens to `[X,W]` and access-kills W, B — holding its stale
-- `[X]` read — writes `[X,Z]`, dropping W with W now undiscoverable).
--
-- NOT guarded on `deleted_at`: the ERASURE sweep (`_purge_deleted_accounts`) is
-- the last chance to reach an org acquired inside the grace window, and it runs
-- only on rows whose `deleted_at` is already stamped — so a `deleted_at IS NULL`
-- guard made the claim structurally impossible at the one moment it is needed,
-- leaving a fault after membership removal to orphan the org while the account
-- was erased (#4029 cycle-3 P1). Widening `claimed_org_ids` cannot move the
-- promise: this function never touches `deleted_at` or `grace_hours`.
-- Idempotent per org.
-- SECURITY DEFINER + service_role only, mirroring the other control-plane RPCs.
-- ============================================================================
CREATE OR REPLACE FUNCTION public.account_deletion_claim_org(
    p_user_id uuid,
    p_org_id  text
)
RETURNS void
LANGUAGE sql
SECURITY DEFINER
SET search_path = ''
AS $$
    UPDATE public.account_deletions
       SET claimed_org_ids = claimed_org_ids || to_jsonb(p_org_id),
           org_ids = CASE WHEN org_ids ? p_org_id
                          THEN org_ids
                          ELSE org_ids || to_jsonb(p_org_id) END
     WHERE user_id = p_user_id
       AND p_org_id IS NOT NULL
       AND btrim(p_org_id) <> ''
       AND NOT (claimed_org_ids ? p_org_id);
$$;

REVOKE ALL ON FUNCTION public.account_deletion_claim_org(uuid, text)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.account_deletion_claim_org(uuid, text)
    TO service_role;
