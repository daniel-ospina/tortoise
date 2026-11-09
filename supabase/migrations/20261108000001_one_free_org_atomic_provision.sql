-- Migration 20261108000001: the DB-level backstop for the one-free-org
-- entitlement (#1981, residual of #1954/#2810)
--
-- WHY. "One free organization per person" (#1877/#2789, chokepoint
-- `hosted_api._assert_free_org_allowance`) is a read-then-write: the lane reads
-- the caller's owned free orgs and then provisions. #1954 closed the window
-- with a per-user `asyncio.Lock` — which is PER PROCESS. Every control-plane
-- statement is its own PostgREST HTTP request = its own transaction, so no
-- application-level lock can span the read and the write, and a second worker
-- (uvicorn `--workers N`, or a second app instance/machine on the same
-- Supabase project) has its own lock dict. Two workers can each read
-- count == 0 and each provision → two free orgs for one person.
--
-- WHY NOT a unique index (the mechanism the issue names first — rejected with
-- evidence, recorded here so it is not "helpfully" added later):
--   1. The rule's inputs live on TWO tables. The predicate counts
--      `org_memberships` rows but decides on `organizations.tier` /
--      `subscription_status`. A PostgreSQL index predicate cannot reference
--      another table, so a partial unique index on the membership table cannot
--      express it.
--   2. Denormalising the rule onto a column ("claim the free slot") and putting
--      a UNIQUE index on THAT is worse than wrong — it contradicts a SETTLED
--      product decision. `create A → buy B → cancel B` legitimately ends with
--      two free orgs (the org drops to tier='free', subscription_status
--      ='canceled'), pinned by
--      `tests/test_one_free_org_entitlement.py::TestCancellationLifecycleKnownHole`
--      and recorded in `docs/scoping/2026-09-10-2789-one-free-org.md` §2. A
--      hard unique index would make the `customer.subscription.deleted` webhook
--      FAIL on that transition — a billing-lifecycle break to enforce a create
--      gate. The entitlement is a rule about CREATING an org, not an invariant
--      of stored rows, so it belongs in the create path.
--
-- WHAT. The atomic CONDITIONAL provision: the whole check+write becomes ONE
-- transaction by moving the check INSIDE `provision_team`, which already is
-- that one transaction. A cross-process lock is taken first (so the check is
-- not racing a concurrent provision), then the SAME predicate the Python gate
-- reads is re-evaluated INSIDE the transaction, and a refusal aborts before any
-- row is written.
--
-- LOCK RESOURCE — `pg_advisory_xact_lock(hashtextextended(...))`. #1879
-- (20261001000002) REJECTED an advisory key derivation for the api_keys cap
-- because that cap has MULTIPLE independent writers, and "a derivation that is
-- not byte-identical in EVERY writer silently loses mutual exclusion with no
-- error". The distinction that makes it correct HERE: this RPC is the ONLY
-- writer that derives this key. It is the sole provisioning path for the
-- Supabase control plane — enumerated in full, because an audit that names five
-- lanes and omits a sixth is how a caller ends up unmapped (#1981 round-4
-- review, P2): `/v1/register`, `/v1/agent/signup`, POST /v1/organizations, the
-- onboarding sub-org create, the paid-new-org checkout, and the
-- `tenant-provision` Edge Function (`supabase/functions/tenant-provision`, a
-- server-only function reached through the dashboard's same-origin BFF since
-- #1120 deleted the `after_user_created` Auth hook and its origin allowlist —
-- it calls `provision_team` with a non-NULL
-- `p_user_id` and no `p_tier`, so THE GATE DOES ENGAGE THERE; its refusal is now
-- mapped to the product's 402 `one_free_org_limit` payload instead of an
-- unmapped 502 "try again", which was a dead end that could never succeed —
-- that is the WIRE contract, and the residual is stated so this header is not
-- read as a delivered UX: the dashboard's provision path still retries a
-- non-ok and renders "try again" without reading the body, and this function's
-- envelope is flat where the dashboard's coded-detail reader lifts
-- `body.detail.code` (#11604)).
-- `provision_team_with_token` PERFORM's THIS function rather than re-deriving
-- anything, so there is exactly one derivation site and it cannot drift out of
-- agreement with itself. A `hashtextextended` collision can
-- only OVER-serialise two unrelated users (the lock is not the gate — the
-- in-transaction re-check is), never under-serialise the same user, which is
-- the only direction that loses mutual exclusion.
--
-- ⛔ WHY THE OTHER HALF OF #1879's REJECTION DOES NOT BIND HERE, stated because
-- it is the half a future reader is most likely to re-raise (#1981 review,
-- P2). That note also calls the hash "cross-version unstable", which is true of
-- the function — and was fatal for a key some OTHER process has to compute: a
-- value that must agree across versions has to be derived OUTSIDE the server.
-- This key is never derived outside it. Every contender is a session on THIS
-- server, in the same cluster at the same major version, so all of them run
-- the identical function and produce the identical key; cross-version (or
-- cross-platform, e.g. a cross-endian cluster migration) drift cannot make two
-- LIVE contenders disagree, because contenders cannot be on different servers.
-- What that reasoning DOES pin is the invariant, and it is the one to hold on
-- to: "every writer contending for this key derives it byte-identically, HERE".
-- A future writer that derives its own key is not serialized by this lock —
-- which is exactly why `claim_membership`, the one other path that can make a
-- person an owner, is a named RESIDUAL below rather than a copy of this
-- expression, and why it must reuse THIS derivation rather than invent one.
--
-- The lock is transaction-scoped, so it is released at COMMIT/ROLLBACK — no
-- leaked lock on a crash, no lease to expire (contrast a graph/table "claim"
-- row, which would need stealing semantics).
--
-- SNAPSHOT NOTE (the guarantee, stated narrowly). PostgREST runs each request
-- as its own autocommit transaction at READ COMMITTED, and in READ COMMITTED
-- PL/pgSQL takes a NEW snapshot per statement — so the re-check below runs
-- AFTER the lock is acquired and therefore SEES a competitor's commit. Under
-- REPEATABLE READ / SERIALIZABLE the transaction snapshot predates the lock,
-- and the re-check would not see the committed competitor; callers must not
-- wrap this RPC in such a transaction (no caller does — it is invoked as a
-- single RPC over HTTP).
--
-- SCOPE — enabled only where the entitlement applies:
--   * `p_user_id IS NOT NULL`: THE GATE IS KEYED ON THE PERSON. The identity path
--     (`p_identity`, /v1/register, /v1/agent/signup) has no user row to hold an
--     allowance and no per-person row to lock, so the check cannot be expressed
--     there. That is a LIMITATION of this backstop, not a proof that the path is
--     safe — and it is the only honest reason.
--
--     ⛔ CORRECTED (#1981 round-4 review, P2). This note used to claim the
--     identity path "is idempotent on its own anchor (`uq_member_identity_org`),
--     which is what keeps one person from accumulating orgs there". Both halves
--     were wrong, and a future lane reads this header to decide whether that
--     path needs the backstop:
--       - `uq_member_identity_org` is UNIQUE (identity, org_id) WHERE
--         user_id IS NULL (renamed from `uq_member_identity_team` in
--         20260915000001). It is a SAME-ORG retry anchor. It does not bound
--         accumulation at all: a fresh identity is a fresh row.
--       - the accumulation bound is `uq_member_identity_active`, UNIQUE
--         (identity) WHERE user_id IS NULL AND role = 'owner' AND status =
--         'active' (20260827000002) — one ACTIVE OWNER org per identity. §8d of
--         `supabase/tests/0010_provisioning_rpcs.sql` names that index, so the
--         two artifacts must agree.
--       - and it does not hold for `/v1/agent/signup` at all: that lane mints a
--         FRESH `anon-{uuid4[:12]}` per request, so no per-identity anchor bounds
--         a PERSON there. The in-tree record says exactly that — see
--         `_assert_free_org_allowance` in `hosted_api.py`: "one person could mint
--         N anon orgs (/v1/agent/signup) and claim each in turn". That path is
--         closed by `/v1/claim` running the ownership gate, because an anon org
--         is not OWNED until claimed — not by this migration.
--     Residual, stated so it is not read as covered: the identity path's bounds
--     are the per-identity uniqueness above plus the claim lane's gate, and
--     neither of them is this backstop.
--   * the new org is FREE (`COALESCE(p_tier,'free') = 'free'`): a PAID
--     provision is exactly the "buy a subscription for a NEW organization"
--     exit #2789 design 2 requires, and must never be refused. The
--     paid-new-org checkout lane passes the purchased tier here — and it now
--     REFUSES a free or absent tier rather than defaulting to 'free', because
--     reaching this gate would refuse a provision the customer already paid for
--     (`hosted_api._provision_new_org_from_checkout`).
-- Those are precisely the calls the Python gate already refuses, so this
-- backstop cannot newly refuse anything a legitimate lane is allowed to do —
-- it only makes the refusal ATOMIC.
--
-- PREDICATE PARITY — the subquery below MUST stay identical to
-- `supabase_control.owned_free_org_ids` (active + role='owner' + org_id<>'' +
-- org row exists + subscription_status not in {active,past_due,trialing} and
-- not 'pending_payment'). It deliberately carries NO `tier` condition and NO
-- `deleted_at` condition because the Python twin carries neither: a backstop
-- that is STRICTER than the gate it backs off would 402 a create the product
-- rule allows, which is worse than the race it closes. `m.org_id <> p_org_id`
-- keeps a retry of an org's own provision (and the checkout replay) allowed.
-- `tests/test_free_team_entitlement.py::TestAtomicProvisionBackstop` pins the
-- two declarations against each other, and `supabase/tests/0010_provisioning_rpcs.sql`
-- §8 pins the behaviour.
--
-- RESIDUALS — stated here so this migration is not read as "the entitlement is
-- now atomic everywhere":
--   * `claim_membership` (20260813000004) is the OTHER writer that can make a
--     person the owner of an org (#2810's finding: claim an anon org → owned),
--     and it does not PERFORM this function, so it neither takes this lock nor
--     runs this predicate. Its multi-worker window is filed separately (#11579); a
--     second derivation site is exactly what the LOCK RESOURCE note above says
--     must not be added casually, so it gets its own migration rather than a
--     copy of this key.
--   * the REGISTRY lane (`TORTOISE_CONTROL_PLANE=registry`, i.e.
--     `is_supabase_enabled()` false) never reaches this function — it writes
--     through `sdk.org_create` into the GRAPH, where Postgres constraints do
--     not exist at all. It `PERFORM`s nothing here, takes no lock, and runs no
--     predicate: `_assert_free_org_allowance`'s registry arm reads the graph and
--     then writes it, still spanned only by the per-process
--     `_org_create_lock`. **#1954's window therefore REMAINS OPEN on this lane
--     under N workers / N replicas, and this migration does not narrow it.**
--     Say so plainly, because the honest answer here is "not covered", not
--     "not affected": the lane is reachable (`create_org` falls through to
--     `_create_org_registry_lane` whenever Supabase is disabled, and
--     `/internal/provision` is refused only in Supabase mode) and its own
--     docstring records the hazard — "the registry lane may run MULTI-PROCESS
--     (selfhost uvicorn workers) — the in-process lock narrows the window there
--     but is not bulletproof; DB-level enforcement is the multi-process backstop
--     … follow-up".
--     ⛔ "The shipped images are single-worker" is NOT an exemption, and must
--     not be restated as one (this bullet previously did, conflating two
--     different deployments). It is true — `Dockerfile.selfhost` runs
--     `tortoise.selfhost:app`, the MCP-only daemon with no control-plane create
--     surface, and `Dockerfile.hosted` runs a single `uvicorn` worker — and it
--     is the SAME assumption the Supabase half of this migration exists to stop
--     relying on: the hazard is defined by deployments that are not the shipped
--     single-worker image, so the default cannot bound it on either lane. A
--     graph-level backstop (a claim written with the org create, a single-writer
--     provisioner, or a fail-closed refusal of the multi-worker configuration)
--     is its own change, filed as #11603 rather than assumed covered here.
--
-- ACL: the signature is unchanged, so `CREATE OR REPLACE` PRESERVES the
-- existing grants (`REVOKE ALL ... FROM public, anon, authenticated`
-- / `GRANT ... TO service_role`, re-issued by 20260915000001 §6.3). No
-- DROP FUNCTION here on purpose — a DROP would reset the ACL to the schema
-- default (the trap that migration documents) and would also drop any prepared
-- plans for no benefit.

CREATE OR REPLACE FUNCTION public.provision_team(
    p_user_id     uuid,        -- Supabase user (user path); NULL on the identity path
    p_identity    text,        -- agent anchor (anon-xxx); NULL on the user path
    p_org_id      text,        -- 26-hex org id (minted by the Edge Function)
    p_org_name    text,
    p_api_key     text,        -- plaintext — shown once on the welcome page, then nulled
    p_key_hash    text,        -- salted PBKDF2 (tortoise/auth.py hash_api_key) — continuity
    p_lookup_hash text,        -- SHA-256(pepper + key) — caller-computed (plan P1-1)
    p_graph_name  text,        -- org_{org_id} (matches Edge Function + data plane)
    p_email       text DEFAULT NULL,
    p_key_prefix  text DEFAULT NULL,
    p_tier           text    DEFAULT 'free',
    p_max_users      integer DEFAULT 1,
    p_max_graphs     integer DEFAULT 1,
    p_ops_allowance  integer DEFAULT 10000,
    p_graph_size_cap bigint  DEFAULT 10000
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_created_by text;
    v_blocking_org_id text;
BEGIN
    IF p_org_id IS NULL OR p_org_id = '' OR p_org_name IS NULL OR p_org_name = ''
       OR p_graph_name IS NULL OR p_graph_name = '' THEN
        RAISE EXCEPTION 'provision_team: required parameters missing';
    END IF;
    -- All-or-none key guard: the three key params are either ALL provided
    -- (a minted key) or ALL NULL (keyless provision) — never a partial set.
    IF (p_api_key IS NULL) <> (p_key_hash IS NULL)
       OR (p_key_hash IS NULL) <> (p_lookup_hash IS NULL) THEN
        RAISE EXCEPTION 'provision_team: p_api_key/p_key_hash/p_lookup_hash must be all provided or all NULL (keyless)';
    END IF;
    IF (p_user_id IS NULL) = (p_identity IS NULL) THEN
        RAISE EXCEPTION 'provision_team: exactly one of p_user_id / p_identity is required';
    END IF;
    v_created_by := COALESCE(p_user_id::text, p_identity);

    -- ── #1981: the one-free-org entitlement, ATOMIC ──────────────────────
    -- Taken BEFORE any write, so a refusal leaves no row behind and the
    -- caller's 402 is indistinguishable from the (unchanged) Python gate's.
    IF p_user_id IS NOT NULL AND COALESCE(p_tier, 'free') = 'free' THEN
        -- Serialize this user's concurrent provisions ACROSS PROCESSES. The
        -- key derivation lives here and only here (see the header).
        PERFORM pg_advisory_xact_lock(
            hashtextextended('tortoise.one_free_org:' || p_user_id::text, 0));

        -- THE gate: the Python twin's predicate, re-evaluated inside this
        -- transaction while no other provision for this user can run.
        SELECT m.org_id INTO v_blocking_org_id
          FROM public.org_memberships m
          JOIN public.organizations o ON o.id = m.org_id
         WHERE m.user_id = p_user_id
           AND m.status  = 'active'
           AND m.role    = 'owner'
           AND m.org_id <> ''
           AND m.org_id <> p_org_id
           AND (o.subscription_status IS NULL
                OR (o.subscription_status <> 'active'
                    AND o.subscription_status <> 'past_due'
                    AND o.subscription_status <> 'trialing'
                    AND o.subscription_status <> 'pending_payment'))
         ORDER BY m.created_at
         LIMIT 1;

        IF v_blocking_org_id IS NOT NULL THEN
            -- The message IS the wire contract: `hosted_api` maps the
            -- `one_free_org_limit:<org_id>` marker onto the SAME structured
            -- 402 payload the Python gate raises (`code`, `message`, `org_id`
            -- — the dashboard dialog reads `code`), so a cross-worker refusal
            -- is not a 500 and not a bare string. Keep the marker in lockstep
            -- with `hosted_api._ONE_FREE_ORG_RPC_MARKER`.
            -- PT402 is PostgREST's custom-status convention (`PT<code>` ->
            -- HTTP <code>), so the RPC answers 402 like the Python gate
            -- instead of the generic 400 a bare P0001 would produce. A
            -- deployment whose PostgREST does not honour it degrades to 400 —
            -- the caller maps on the MESSAGE marker, never on the status.
            RAISE EXCEPTION 'one_free_org_limit:%', v_blocking_org_id
                USING ERRCODE = 'PT402';
        END IF;
    END IF;

    -- ── organizations row (exactly one; idempotent re-invocation) ────────
    INSERT INTO public.organizations (id, name, tier, graph_name, email,
                                      max_users, max_graphs, ops_allowance, graph_size_cap)
    VALUES (p_org_id, p_org_name, p_tier, p_graph_name, p_email,
            p_max_users, p_max_graphs, p_ops_allowance, p_graph_size_cap)
    ON CONFLICT (id) DO UPDATE
        SET name       = EXCLUDED.name,
            graph_name = EXCLUDED.graph_name,
            email      = COALESCE(EXCLUDED.email, organizations.email);

    -- ── membership: exactly one row per (user, org). Order matters ──────
    UPDATE public.org_memberships
       SET org_name    = p_org_name,
           api_key     = p_api_key,
           key_hash    = p_key_hash,
           lookup_hash = p_lookup_hash,
           graph_name  = p_graph_name,
           role        = 'owner',
           status      = 'active',
           updated_at  = now()
     WHERE user_id = p_user_id AND org_id = p_org_id;

    IF NOT FOUND THEN
        -- Reconcile the trigger placeholder (flip org_id='' in place).
        UPDATE public.org_memberships
           SET org_id      = p_org_id,
               org_name    = p_org_name,
               api_key     = p_api_key,
               key_hash    = p_key_hash,
               lookup_hash = p_lookup_hash,
               graph_name  = p_graph_name,
               role        = 'owner',
               status      = 'active',
               updated_at  = now()
         WHERE user_id = p_user_id AND org_id = ''
           AND NOT EXISTS (
               SELECT 1 FROM public.org_memberships
               WHERE user_id = p_user_id AND org_id = p_org_id
           );

        IF NOT FOUND THEN
            IF p_user_id IS NOT NULL THEN
                INSERT INTO public.org_memberships
                    (user_id, org_id, org_name, api_key, key_hash, lookup_hash,
                     graph_name, role, status)
                VALUES (p_user_id, p_org_id, p_org_name, p_api_key, p_key_hash,
                        p_lookup_hash, p_graph_name, 'owner', 'active')
                ON CONFLICT (user_id, org_id) DO NOTHING;
            ELSE
                -- Identity path: the conflict branch REFRESHES the existing row
                -- in place (uq_member_identity_org keeps re-invocation
                -- idempotent) — re-provisioning an identity with a rotated key
                -- updates the membership instead of going stale.
                INSERT INTO public.org_memberships
                    (user_id, org_id, org_name, api_key, key_hash, lookup_hash,
                     graph_name, role, status, identity)
                VALUES (NULL, p_org_id, p_org_name, p_api_key, p_key_hash,
                        p_lookup_hash, p_graph_name, 'owner', 'active', p_identity)
                ON CONFLICT (identity, org_id) WHERE user_id IS NULL
                DO UPDATE SET org_name    = EXCLUDED.org_name,
                              api_key     = EXCLUDED.api_key,
                              key_hash    = EXCLUDED.key_hash,
                              lookup_hash = EXCLUDED.lookup_hash,
                              graph_name  = EXCLUDED.graph_name,
                              role        = 'owner',
                              status      = 'active',
                              updated_at  = now();
            END IF;
        END IF;
    END IF;

    -- Any leftover placeholder for this user is stale now.
    DELETE FROM public.org_memberships
     WHERE user_id = p_user_id AND org_id = '';

    -- ── api_keys row (exactly one per (org, key); E2E-1 contract) ────────
    -- Keyless provision (all-NULL key params) writes NO api_keys row — the org
    -- stays keyless until a session-key mint.
    IF p_lookup_hash IS NOT NULL THEN
        INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix,
                                     created_via, created_by)
        VALUES ('key_' || p_org_id || '_' || left(p_lookup_hash, 12),
                p_org_id, p_lookup_hash,
                COALESCE(p_key_prefix, left(p_org_id, 8)),
                'provisioned', v_created_by)
        ON CONFLICT (lookup_hash) DO NOTHING;
    END IF;
END;
$$;
