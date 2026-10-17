-- Migration 20261012000006: `recover_team_key`'s cap count and revoke-target
-- scan exclude EXPIRED-but-unrevoked rows — the expiry axis of the ONE cap
-- predicate, on the last lane that was missing it (#4550).
--
-- WHY. Every other cap reader excludes a row whose `expires_at` has passed:
--   * `public.api_key_slot_count` (20261001000002 §1) — the ONE SQL
--     declaration of the predicate, used by `session_key_mint` and
--     `provision_api_key`;
--   * `quota._count_resource("api_keys")` — both lanes (Supabase via
--     `active_api_keys`; registry via the Cypher expiry arm), the predicate
--     the standalone mint gates (`hosted_api._mint_key`) count with;
--   * `hosted_api.session_key` / `_session_key_supabase`;
--   * the registry twin `sdk.signup_token_recover` — expiry in BOTH its count
--     AND its victim scan.
-- `recover_team_key` was the outlier on both halves of its cap section.
--
-- WHY IT IS NOT COSMETIC. The RPC is not read-only at the cap: on the
-- over-cap branch it REVOKES a row. An org whose counted rows include
-- expired-but-unrevoked durables therefore reads as over-cap for rows that
-- occupy no slot, and the never-used-first victim scan then revokes a LIVE
-- credential as collateral for them. Worked example — cap=2, one LIVE
-- never-used key, two expired-but-unrevoked keys last used recently: the
-- minted recovery row makes the expiry-BLIND count 4 > 2, and the
-- `(last_used_at IS NOT NULL) ASC` order makes the never-used LIVE key the
-- first candidate (`id IS DISTINCT FROM v_inserted_id` only spares the row
-- just minted). The live key dies; both expired rows survive. It is also
-- non-self-healing in that shape: revoking one expired row frees no slot, so
-- the org stays over cap.
--
-- ⛔ THE COUNT AND THE VICTIM SCAN MUST CARRY THE SAME PREDICATE. Adding the
-- filter to the count alone would be a NEW defect, not a smaller one: a
-- victim picked from a wider set than the count used frees a slot the count
-- never charged. That is the exact reason both halves now read through
-- `api_key_slot_count` (count) and the identical expiry arm (scan) instead of
-- the count/scan pair keeping two hand-written copies of one rule — which is
-- how this lane drifted while its siblings did not.
--
-- ⛔ THIS MIGRATION ALSO CARRIES THE #1872 LRU ORDER, DELIBERATELY.
-- 20261011000001 warns that "a later migration that re-states this function
-- must carry BOTH the #4550 predicate fix and this ordering, or it will
-- revert this one" — so the `(last_used_at IS NOT NULL) ASC,
-- last_used_at ASC NULLS FIRST, created_at ASC NULLS FIRST` order and the
-- `id IS DISTINCT FROM v_inserted_id` exclusion are reproduced verbatim from
-- that migration. The body is otherwise byte-identical to it: this change is
-- the PREDICATE axis only.
--
-- ⛔ SELF-HEALING IS PRESERVED. The RPC INSERTs the recovery row BEFORE it
-- counts, and the new row (`expires_at` NOT NULL is not passed → NULL;
-- `created_via='recovery'`) is LIVE and COUNTED, so `v_count > p_max_api_keys`
-- still fires for an org genuinely above cap, and the victim scan still
-- excludes only the row this call minted rather than narrowing the count. A
-- no-op retry (`v_inserted_id IS NULL`) still skips the entire section.
--
-- SUPERSEDES, in the deferral notes only — both files stay append-only and
-- unedited: 20261001000002's "KNOWN, RECORDED RESIDUALS → #4550" bullet and
-- 20261011000001's "⛔ #4550 IS NOT FOLDED IN HERE" note. After this
-- migration the "one predicate" claim in `hosted_api._mint_key`'s #2481 audit
-- comment holds without an exception: no cap lane omits the expiry axis.
--
-- Locking is UNCHANGED (org row `FOR NO KEY UPDATE`, then the token row
-- `FOR UPDATE`), and the ACLs are re-issued below (service_role ONLY).

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
    -- the check self-healing) through the ONE SQL declaration of the
    -- predicate (`api_key_slot_count`: non-revoked, non-expired, non-bootstrap
    -- — #4550 folds the expiry axis in for THIS lane, so the count can no
    -- longer drift from `quota._count_resource`).
    IF v_inserted_id IS NOT NULL THEN
        v_count := public.api_key_slot_count(p_org_id, now());
        IF v_count > p_max_api_keys THEN
            -- At/over cap, revoke the LEAST-RECENTLY-USED non-bootstrap key
            -- that is NOT the row we just minted (#1872). The scan carries the
            -- SAME expiry arm as the count (#4550): a wider scan would free a
            -- slot the count never charged, and a victim that is itself expired
            -- frees nothing at all.
            SELECT id INTO v_oldest_id
              FROM public.api_keys
             WHERE org_id = p_org_id
               AND revoked_at IS NULL
               AND (expires_at IS NULL OR expires_at > now())
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
-- 20261001000002 §5 / 20261011000001) — service_role ONLY.
REVOKE ALL ON FUNCTION public.recover_team_key(text, text, text, text, integer)
    FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.recover_team_key(text, text, text, text, integer)
    TO service_role;
