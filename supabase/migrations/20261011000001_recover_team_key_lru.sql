-- Migration 20261011000001: recover_team_key revoke target is the
-- LEAST-RECENTLY-USED non-bootstrap key, and never the key it just minted.
--
-- WHY. `recover_team_key` is the keyless signup-token recovery mint. At cap it
-- revoked the OLDEST-CREATED non-bootstrap key (`ORDER BY created_at ASC NULLS
-- FIRST`). That is the exact defect #1854 fixed on the session lanes
-- (`session_key_mint`, 20261001000002 §2): "oldest created" picks a credential
-- by the wrong dimension, so an org whose oldest key is a LIVE, actively-used
-- credential loses that credential while a never-used key sits beside it. The
-- keyless lane was left out of #1854 because it has no user identity (it cannot
-- do the own-key tiering), but the LRU dimension does not depend on identity —
-- the victim is the key nothing is using. This migration brings the lane to
-- #1854 parity. Follow-up #1872 (the keyless lane's own LRU decision).
--
-- ORDER (identical to session_key_mint tier 2, and to the registry twin
-- `tortoise/sdk.py::signup_token_recover`):
--   (last_used_at IS NOT NULL) ASC,   -- never-used (NULL) keys first: an
--                                     -- unused credential is the safest victim
--   last_used_at ASC NULLS FIRST,     -- then least-recently-used
--   created_at  ASC NULLS FIRST       -- tiebreak: an all-NULL set keeps the
--                                     -- pre-#1872 oldest-created behavior
--
-- ⛔ THE SECOND CHANGE IS LOAD-BEARING, NOT TIDINESS. This RPC INSERTs the
-- recovery row BEFORE it counts and possibly revokes (the documented
-- self-healing ordering — an unlocked race still converges to <= cap). So the
-- just-minted row is INSIDE the candidate set, and its `last_used_at` is NULL by
-- construction. Under the new NULL-first order a freshly minted key is the
-- FIRST candidate, so an org at cap with every pre-existing key already used
-- would revoke the key it is about to return (a dead key handed to the caller,
-- and a recovery that freed nothing). `id IS DISTINCT FROM v_inserted_id`
-- excludes exactly that row, and only that row: every pre-existing key stays a
-- candidate, and when a no-op retry inserted nothing (`v_inserted_id IS NULL`)
-- the predicate excludes nothing at all. The COUNT is deliberately NOT
-- narrowed — it must keep including the new row, or the over-cap check stops
-- being self-healing.
--
-- ⛔ #4550 IS NOT FOLDED IN HERE. The count and the victim scan still omit the
-- `expires_at` filter that every Python twin applies; 20261001000002 §4 records
-- that as deliberately deferred to #4550. Changing the ordering axis does not
-- touch the predicate axis — do not silently widen this migration. A later
-- migration that re-states this function must carry BOTH the #4550 predicate
-- fix and this ordering, or it will revert this one.
--
-- Locking is UNCHANGED from 20261001000002 §4 (org row FOR NO KEY UPDATE, then
-- the token row FOR UPDATE): the org-wide invariant still needs the org lock,
-- because two different tokens for one org would otherwise take different row
-- locks. Body is otherwise byte-identical to that definition.
--
-- ACLs are re-issued below (service_role ONLY) as on every previous issue.

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
    -- Serialization point for the ORG-wide invariant (see 20261001000002 §4).
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
    -- revoke a live key. Count AFTER the insert (the new row counts, keeping
    -- the check self-healing); at/over cap, revoke the LEAST-RECENTLY-USED
    -- non-bootstrap key that is NOT the row we just minted (#1872). #4550: the
    -- predicate here intentionally omits the expiry filter — that issue's
    -- scope, not this one.
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
               AND id IS DISTINCT FROM v_inserted_id
             ORDER BY (last_used_at IS NOT NULL) ASC,
                      last_used_at ASC NULLS FIRST,
                      created_at ASC NULLS FIRST
             LIMIT 1;
            IF v_oldest_id IS NOT NULL THEN
                UPDATE public.api_keys SET revoked_at = now() WHERE id = v_oldest_id;
            END IF;
        END IF;
    END IF;

    RETURN p_org_id;
END;
$$;

-- Re-issued ACL (20260814000001 §5 / 20260915000001 §6.6 /
-- 20261001000002 §5) — service_role ONLY.
REVOKE ALL ON FUNCTION public.recover_team_key(text, text, text, text, integer)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.recover_team_key(text, text, text, text, integer)
    TO service_role;
