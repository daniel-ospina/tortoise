-- Migration 20260927000001: durable multi-worker mint serialization (#1879)
--
-- WHY. The per-org `max_api_keys` cap is a check-then-act that every writer of
-- `api_keys` performs independently: the Supabase session mint
-- (hosted_api._session_key_supabase), the registry session mint, `_mint_key`
-- (POST /v1/team/keys + per-graph mints + rotate), and the signup-token
-- `recover_team_key` RPC. Every control-plane statement is its OWN PostgREST
-- HTTP request = its own transaction, so no application-level lock can span
-- the section, and the in-process `_org_mint_lock` is per worker PROCESS. Two
-- workers — or an app lane racing a DB-side RPC — can each observe a below-cap
-- count and each insert, leaving live non-bootstrap keys at cap+N.
--
-- WHY NOT an advisory-lock WRAPPER RPC (#1855 / #1881, re-verified): PostgREST
-- is stateless per request, so a `pg_advisory_xact_lock` taken by a *wrapper*
-- RPC releases at THAT request's commit — before the rest of the section runs —
-- and a session-level `pg_advisory_lock` binds to one pooled connection the
-- next request may not reuse. The lock must be taken INSIDE the same
-- transaction as the whole section, i.e. the section itself must be one RPC.
--
-- LOCK RESOURCE / MODE — `organizations.id`, `FOR NO KEY UPDATE`:
--   * `api_keys.org_id` REFERENCES `organizations(id)` (`api_keys_org_id_fkey`),
--     so every `api_keys` INSERT takes `FOR KEY SHARE` on the parent row.
--     `FOR UPDATE` CONFLICTS with `FOR KEY SHARE` and would queue the org's
--     entire child-write surface behind every mint. `FOR NO KEY UPDATE` does
--     NOT conflict with `FOR KEY SHARE`, but DOES conflict with itself — which
--     is exactly what is needed: mint writers serialize among themselves and
--     unrelated child inserts do not block.
--   * A row lock (not `pg_advisory_xact_lock(hashtext(org_id))`) is deliberate:
--     an advisory key derivation that is not byte-identical in EVERY writer
--     silently loses mutual exclusion with no error (`hashtext` is 32-bit and
--     cross-version unstable). A row lock cannot fail that way, and the
--     zero-row case is a fail-closed existence check.
--   * Zero-row lock => RAISE. NEVER mint without the lock.
--
-- SCOPE OF THE LOCK: only SQL inside the RPC transaction (counts, revokes, the
-- INSERT). No network calls, no audit/abuse writes, no role/suspension gates
-- (those carry no SQL authority and stay caller-side in Python), so the lock is
-- held for microseconds rather than across an HTTP round trip.
--
-- KNOWN, RECORDED RESIDUALS — do not silently widen:
--   * #4550: `recover_team_key`'s own count still omits the expiry filter that
--     every Python lane applies. This migration DOES add the org lock to it (a
--     separate token row lock cannot serialize an org-wide invariant) but
--     deliberately leaves its predicate untouched — a distinct defect with its
--     own issue.
--   * `provision_team` / `provision_team_with_token` insert an org's FIRST key
--     with no cap gate (new-org path: the org cannot have keys yet), and a
--     `FOR NO KEY UPDATE` lock does not block a raw un-routed INSERT. A future
--     `api_keys` writer MUST route through `public.session_key_mint` /
--     `public.provision_api_key`.
--   * `rotate_api_key`'s claim-revoke runs in a SECOND transaction (its own
--     request), so it cannot join this lock. That is safe for the cap: a
--     concurrent revoke only ever LOWERS the count this RPC re-reads. The
--     reverse — a concurrent ADD by a writer that did not take the lock — is
--     what the fail-closed re-checks inside `session_key_mint` convert into a
--     refusal instead of a lasting cap+1.

-- ============================================================================
-- 1) api_key_slot_count — the ONE SQL declaration of the cap predicate
-- ============================================================================
-- Mirrors `quota._count_resource(org_id, "api_keys")` exactly: a REVOKED row is
-- an audit tombstone, an EXPIRED row never authenticates (#742/#2426), and a
-- BOOTSTRAP row is cap-exempt (#4140/R13). The bootstrap exclusion is written
-- NULL-TOLERANT on purpose: a legacy row with a NULL `created_via` is DURABLE
-- and MUST count (fail-closed) — SQL `<>` would drop it, which is the exact
-- over-exemption direction #4140's adversarial T4 covers.
CREATE OR REPLACE FUNCTION public.api_key_slot_count(
    p_org_id text,
    p_now    timestamptz
)
RETURNS integer
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
    SELECT count(*)::integer
      FROM public.api_keys
     WHERE org_id = p_org_id
       AND revoked_at IS NULL
       AND (expires_at IS NULL OR expires_at > p_now)
       AND (created_via IS NULL OR created_via <> 'bootstrap');
$$;

-- ============================================================================
-- 2) session_key_mint — the whole session-mint critical section, one tx
-- ============================================================================
-- Ports the EXACT semantics of `hosted_api._session_key_supabase`'s in-lock
-- section: bootstrap 3-active check; recovery cap count -> oldest-OTHER revoke
-- (#750.10 never the caller's own key; #1859 P3-1 legacy `created_by IS NULL`
-- rows are NOT "others" — the registry predicate `created_by <> $uid` excludes
-- them by NULL semantics, so they fall to the rotation branch) -> fail-closed
-- RE-CHECK -> 3-tier rotation (legacy unowned -> own recovery by #1854 LRU
-- order -> own bootstrap #1828; the candidate scan carries NO expiry filter, so
-- an expired own bootstrap is still rotatable — #742 auth is unaffected) ->
-- fail-closed RE-CHECK -> INSERT.
--
-- ONE DELIBERATE DEVIATION, and it is a fix rather than a port: the OTHERS
-- branch carries the same fail-closed re-check the rotation branch already has.
-- Why the window exists even here: READ COMMITTED re-snapshots per statement,
-- and the row lock only excludes writers that TAKE it. A raw, un-routed
-- `api_keys` INSERT (the residual named below) can commit between the count
-- and the revoke, so the revoke frees a slot that is already consumed and the
-- count is still at the cap. Without the re-check the mint inserts anyway
-- (cap+1); with it, the mint fails CLOSED (402) instead of overshooting. In
-- the REGISTRY lane the same re-check is genuinely reachable today (that
-- lane's lock is per-process and `_mint_key`'s callers take no lock at all).
--
-- The cap value is CALLER-SUPPLIED (pricing lives in app code, never SQL —
-- same contract as `recover_team_key`'s p_max_api_keys), and NULL means
-- UNLIMITED (the tier has no cap), never a default.
CREATE OR REPLACE FUNCTION public.session_key_mint(
    p_org_id        text,
    p_user_id       text,
    p_purpose       text,
    p_key_id        text,
    p_lookup_hash   text,
    p_key_prefix    text,
    p_created_at    timestamptz,
    p_expires_at    timestamptz,
    p_max_api_keys  integer,
    p_bootstrap_cap integer DEFAULT 3
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_org_id     text;
    v_live       integer;
    v_recheck    integer;
    v_other_id   text;
    v_rot_id     text;
    v_rot_prefix text;
    v_rotated    boolean := false;
    v_boot       integer;
BEGIN
    -- ── Serialization point (fail closed on a zero-row lock) ──────────────
    SELECT id INTO v_org_id
      FROM public.organizations
     WHERE id = p_org_id
     FOR NO KEY UPDATE;
    IF v_org_id IS NULL THEN
        RAISE EXCEPTION 'session_key_mint: org not found';
    END IF;

    IF p_purpose = 'bootstrap' THEN
        -- Fail closed on a missing expiry: a bootstrap row is CAP-EXEMPT, so a
        -- NULL `expires_at` would mint a permanent key outside every cap — the
        -- one shape that defeats the invariant this migration exists to hold.
        -- The Python lane always passes now()+24h; this guards a future caller.
        IF p_expires_at IS NULL THEN
            RAISE EXCEPTION 'session_key_mint: bootstrap requires an expiry';
        END IF;
        SELECT count(*) INTO v_boot
          FROM public.api_keys
         WHERE org_id = p_org_id
           AND created_via = 'bootstrap'
           AND created_by = p_user_id
           AND revoked_at IS NULL
           AND (expires_at IS NULL OR expires_at > p_created_at);
        -- COALESCE, not a bare comparison: `v_boot >= NULL` is NULL and a
        -- plpgsql `IF NULL` is NOT taken, so a NULL operand would silently
        -- BYPASS the cap. Fail-closed operand handling, same polarity as the
        -- NULL-tolerant predicate above.
        IF v_boot >= COALESCE(p_bootstrap_cap, 3) THEN
            RAISE EXCEPTION 'session_key_mint: bootstrap cap reached';
        END IF;
    ELSE
        IF p_max_api_keys IS NOT NULL THEN
            v_live := public.api_key_slot_count(p_org_id, p_created_at);
            IF v_live >= p_max_api_keys THEN
                -- #750.10 / #1859 P3-1: only a live, non-bootstrap row owned by
                -- a DIFFERENT user is an "other".
                SELECT id INTO v_other_id
                  FROM public.api_keys
                 WHERE org_id = p_org_id
                   AND revoked_at IS NULL
                   AND created_by IS NOT NULL
                   AND created_by <> p_user_id
                   AND (created_via IS NULL OR created_via <> 'bootstrap')
                   AND (expires_at IS NULL OR expires_at > p_created_at)
                 ORDER BY created_at ASC NULLS FIRST
                 LIMIT 1;
                IF v_other_id IS NOT NULL THEN
                    UPDATE public.api_keys
                       SET revoked_at = p_created_at
                     WHERE id = v_other_id;
                    -- Fail-closed re-check: the count is re-read AFTER the
                    -- revoke (READ COMMITTED re-snapshots per statement). A
                    -- concurrent ADD that consumed this slot — the row lock
                    -- does not stop a raw un-routed INSERT (see residuals) —
                    -- leaves the count at the cap, and then this mint must NOT
                    -- insert. A revoke that freed no counted slot must not
                    -- mint.
                    v_recheck := public.api_key_slot_count(p_org_id, p_created_at);
                    IF v_recheck >= p_max_api_keys THEN
                        RAISE EXCEPTION 'session_key_mint: key limit reached';
                    END IF;
                ELSE
                    -- Tier 1: a LEGACY org-scoped unowned key.
                    SELECT id, key_prefix INTO v_rot_id, v_rot_prefix
                      FROM public.api_keys
                     WHERE org_id = p_org_id
                       AND revoked_at IS NULL
                       AND created_by IS NULL
                       AND (created_via IS NULL OR created_via <> 'bootstrap')
                     ORDER BY created_at ASC NULLS FIRST
                     LIMIT 1;
                    -- Tier 2: the user's own LEAST-RECENTLY-USED recovery key
                    -- (#1830; #1854 order: never-used (NULL) first, then LRU,
                    -- then oldest-created).
                    IF v_rot_id IS NULL THEN
                        SELECT id, key_prefix INTO v_rot_id, v_rot_prefix
                          FROM public.api_keys
                         WHERE org_id = p_org_id
                           AND revoked_at IS NULL
                           AND created_by = p_user_id
                           AND created_via = 'recovery'
                         ORDER BY (last_used_at IS NOT NULL) ASC,
                                  last_used_at ASC NULLS FIRST,
                                  created_at ASC NULLS FIRST
                         LIMIT 1;
                    END IF;
                    -- Tier 3: the user's own OLDEST bootstrap key (#1828).
                    -- No expiry filter on this scan: an expired own bootstrap
                    -- is rotatable too.
                    IF v_rot_id IS NULL THEN
                        SELECT id, key_prefix INTO v_rot_id, v_rot_prefix
                          FROM public.api_keys
                         WHERE org_id = p_org_id
                           AND revoked_at IS NULL
                           AND created_by = p_user_id
                           AND created_via = 'bootstrap'
                         ORDER BY created_at ASC NULLS FIRST
                         LIMIT 1;
                    END IF;
                    IF v_rot_id IS NULL THEN
                        RAISE EXCEPTION 'session_key_mint: key limit reached';
                    END IF;
                    UPDATE public.api_keys
                       SET revoked_at = p_created_at
                     WHERE id = v_rot_id;
                    -- Review P2-1: only mint when a persistent slot actually
                    -- opened (a modern bootstrap was never counted).
                    v_recheck := public.api_key_slot_count(p_org_id, p_created_at);
                    IF v_recheck >= p_max_api_keys THEN
                        RAISE EXCEPTION 'session_key_mint: key limit reached';
                    END IF;
                    v_rotated := true;
                END IF;
            END IF;
        END IF;
    END IF;

    INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix, created_via,
                                 created_by, created_at, revoked_at, expires_at)
    VALUES (p_key_id, p_org_id, p_lookup_hash, p_key_prefix,
            CASE WHEN p_purpose = 'bootstrap' THEN 'bootstrap' ELSE 'recovery' END,
            p_user_id, p_created_at, NULL, p_expires_at);

    RETURN jsonb_build_object(
        'rotated', v_rotated,
        'rotated_key_prefix', v_rot_prefix
    );
END;
$$;

-- ============================================================================
-- 3) provision_api_key — the atomic provisioned/rotate mint (single tx)
-- ============================================================================
-- The `_mint_key` Supabase insert + its cap gate in ONE transaction under the
-- SAME org lock as `session_key_mint`, so a provisioned mint and a session mint
-- (or two provisioned mints) cannot both pass the gate. `p_cap_slot_credit`
-- (#4355) admits a replacement-aware rotate against the POST-release count: the
-- displaced row is proven to occupy a counted slot by
-- `quota.api_key_occupies_slot` and is revoke-claimed right after this call.
--
-- No ON CONFLICT: a `lookup_hash` collision must RAISE (parity with the table
-- POST it replaces — the caller must never receive a plaintext for a row that
-- was not inserted).
CREATE OR REPLACE FUNCTION public.provision_api_key(
    p_org_id            text,
    p_key_id            text,
    p_lookup_hash       text,
    p_key_prefix        text,
    p_created_via       text,
    p_created_by        text,
    p_created_at        timestamptz,
    p_expires_at        timestamptz,
    p_name              text,
    p_graph_id          text,
    p_scopes            jsonb,
    p_created_by_key_id text,
    p_delegation_depth  integer,
    p_max_api_keys      integer,
    p_cap_slot_credit   integer DEFAULT 0
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_org_id text;
    v_live   integer;
BEGIN
    SELECT id INTO v_org_id
      FROM public.organizations
     WHERE id = p_org_id
     FOR NO KEY UPDATE;
    IF v_org_id IS NULL THEN
        RAISE EXCEPTION 'provision_api_key: org not found';
    END IF;

    IF p_max_api_keys IS NOT NULL THEN
        v_live := public.api_key_slot_count(p_org_id, p_created_at);
        -- COALESCE: `v_live - NULL >= cap` is NULL, and a plpgsql `IF NULL` is
        -- NOT taken — a NULL credit would silently BYPASS the cap gate. A NULL
        -- credit means "nothing displaced", i.e. 0.
        IF v_live - COALESCE(p_cap_slot_credit, 0) >= p_max_api_keys THEN
            RAISE EXCEPTION 'provision_api_key: key cap reached';
        END IF;
    END IF;

    INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix, created_via,
                                 created_by, created_at, revoked_at, expires_at,
                                 name, graph_id, scopes, created_by_key_id,
                                 delegation_depth)
    VALUES (p_key_id, p_org_id, p_lookup_hash, p_key_prefix, p_created_via,
            p_created_by, p_created_at, NULL, p_expires_at,
            p_name, p_graph_id, COALESCE(p_scopes, '[]'::jsonb),
            p_created_by_key_id, p_delegation_depth);
END;
$$;

-- ============================================================================
-- 4) recover_team_key — same org lock, so the signup-token mint shares the
--    serialization resource (it previously locked only its TOKEN row, which
--    cannot serialize an org-wide invariant: two different tokens for one org
--    took DIFFERENT row locks and both could insert and "free" the same oldest
--    row -> lasting cap+1)
-- ============================================================================
-- Lock order is org -> token, uniformly (the two new RPCs take only the org
-- row), so no deadlock cycle exists. Body is otherwise unchanged from
-- 20260915000001 §6.6; #4550 (its expiry-blind predicate) is NOT folded in here.
CREATE OR REPLACE FUNCTION public.recover_team_key(
    p_token_hash    text,
    p_org_id        text,
    p_lookup_hash   text,
    p_key_prefix    text,
    p_max_api_keys  integer DEFAULT 2
)
RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_org_id      text;
    v_token_org   text;
    v_count       integer;
    v_oldest_id   text;
    v_inserted_id text;
BEGIN
    -- Serialization point for the ORG-wide invariant (see the header).
    SELECT id INTO v_org_id
      FROM public.organizations
     WHERE id = p_org_id
     FOR NO KEY UPDATE;
    IF v_org_id IS NULL THEN
        RAISE EXCEPTION 'recover_team_key: token not found or revoked';
    END IF;

    -- Row lock: serializes concurrent recoveries for the SAME token
    -- (READ COMMITTED check-then-insert race closed). Zero rows -> fail closed
    -- (revoke race or org mismatch) — NEVER mint on a zero-row lock.
    SELECT org_id INTO v_token_org
      FROM public.agent_signup_tokens
     WHERE token_hash = p_token_hash
       AND revoked_at IS NULL
       AND org_id = p_org_id
     FOR UPDATE;
    IF v_token_org IS NULL THEN
        RAISE EXCEPTION 'recover_team_key: token not found or revoked';
    END IF;

    -- Soft-deleted orgs are unrecoverable (caller maps to the uniform 422 —
    -- indistinguishable from never-existed). Suspension is a CALLER-side 403.
    IF EXISTS (SELECT 1 FROM public.organizations
                WHERE id = p_org_id AND deleted_at IS NOT NULL) THEN
        RAISE EXCEPTION 'recover_team_key: team deleted';
    END IF;

    -- Recovery mint FIRST: idempotent — ON CONFLICT (lookup_hash) DO NOTHING
    -- makes a retried recovery with the same key material a NO-OP (no second
    -- key). created_via='recovery'; created_by is DERIVED inside the RPC.
    INSERT INTO public.api_keys (id, org_id, lookup_hash, key_prefix,
                                 created_via, created_by)
    VALUES ('key_' || p_org_id || '_' || left(p_lookup_hash, 12),
            p_org_id, p_lookup_hash, p_key_prefix,
            'recovery', 'st_' || left(p_token_hash, 12))
    ON CONFLICT (lookup_hash) DO NOTHING
    RETURNING id INTO v_inserted_id;

    -- Cap: ONLY when a new key was actually minted — a no-op retry must never
    -- revoke a live key. Count AFTER the insert; at/over cap, revoke the
    -- OLDEST non-bootstrap key (deterministic, #750.10 semantics). #4550: the
    -- predicate here intentionally omits the expiry filter (predicate fix is
    -- that issue's scope, not this one).
    IF v_inserted_id IS NOT NULL THEN
        SELECT count(*) INTO v_count
          FROM public.api_keys
         WHERE org_id = p_org_id AND revoked_at IS NULL
           AND (created_via IS NULL OR created_via <> 'bootstrap');
        IF v_count > p_max_api_keys THEN
            SELECT id INTO v_oldest_id
              FROM public.api_keys
             WHERE org_id = p_org_id AND revoked_at IS NULL
               AND (created_via IS NULL OR created_via <> 'bootstrap')
             ORDER BY created_at ASC NULLS FIRST
             LIMIT 1;
            IF v_oldest_id IS NOT NULL THEN
                UPDATE public.api_keys SET revoked_at = now() WHERE id = v_oldest_id;
            END IF;
        END IF;
    END IF;

    RETURN p_org_id;
END;
$$;

-- ============================================================================
-- 5) Grant hygiene — service_role ONLY (Supabase's ALTER DEFAULT PRIVILEGES
--    grants EXECUTE to anon/authenticated, and these are mint primitives)
-- ============================================================================
REVOKE ALL ON FUNCTION public.api_key_slot_count(text, timestamptz)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.api_key_slot_count(text, timestamptz)
    TO service_role;

REVOKE ALL ON FUNCTION public.session_key_mint(text, text, text, text, text,
                                               text, timestamptz, timestamptz,
                                               integer, integer)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.session_key_mint(text, text, text, text, text,
                                                  text, timestamptz, timestamptz,
                                                  integer, integer)
    TO service_role;

REVOKE ALL ON FUNCTION public.provision_api_key(text, text, text, text, text,
                                                text, timestamptz, timestamptz,
                                                text, text, jsonb, text, integer,
                                                integer, integer)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.provision_api_key(text, text, text, text, text,
                                                   text, timestamptz, timestamptz,
                                                   text, text, jsonb, text, integer,
                                                   integer, integer)
    TO service_role;

-- Re-issued ACL (20260814000001 §5 / 20260915000001 §6.6).
REVOKE ALL ON FUNCTION public.recover_team_key(text, text, text, text, integer)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.recover_team_key(text, text, text, text, integer)
    TO service_role;
