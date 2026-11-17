# Archived sdk-method `signup_token_recover` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Re-mint the bootstrap key for a signup token. Body survives PRIVATE as `_signup_token_recover`; beta: operator-side provisioning; no destination.
# source:  tortoise/sdk.py @ d741bd4a5a8d45198ec9c8fd2324c93ca746af5e
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'signup_token_recover'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def signup_token_recover(self, token_plaintext: str) -> dict:
        """Keyless recovery: mint a NEW key on the token's org.

        Registry parity for recover_team_key (Supabase lane). Cap + revoke-
        of the least-recently-used non-bootstrap key (#750.10 semantics,
        #1854/#1872 LRU ordering — never-used first) mirror the SQL;
        created_by is token-attributable ('st_' + left(token_hash, 12)) —
        never a caller-supplied identity. Returns {api_key, org_id, org_name,
        tier, graph_name}. Raises ControlPlaneError (→ uniform 422) when the
        token is unknown/revoked or the org is soft-deleted.
        """
        from datetime import datetime, timezone as _tz  # noqa: I001
        from .exceptions import ControlPlaneError

        node = self.signup_token_lookup(token_plaintext)
        if node is None:
            raise ControlPlaneError("signup token not found or revoked")
        org_id = node.get("org_id") or ""
        org = self.org_get(org_id)
        if org is None or org.get("deleted_at"):
            raise ControlPlaneError("signup token team deleted")

        import uuid  # noqa: I001
        from tortoise.auth import hash_api_key, lookup_hash
        api_key = f"tt_{uuid.uuid4().hex}"
        key_hash = hash_api_key(api_key)
        now_iso = datetime.now(_tz.utc).isoformat()  # noqa: UP017
        token_hash = lookup_hash(token_plaintext)
        reg = self._get_registry()
        with _SIGNUP_TOKEN_RECOVER_LOCK:
            # re-verify under the lock (parity with the FOR UPDATE re-check)
            node = self.signup_token_lookup(token_plaintext)
            if node is None:
                raise ControlPlaneError("signup token not found or revoked")
            if node.get("org_id") != org_id:
                raise ControlPlaneError("signup token not found or revoked")
            # cap: active non-bootstrap keys; insert FIRST, re-count AFTER,
            # revoke-oldest only when genuinely over cap ([SECOND-MODEL-GATE]
            # P2: mirrors the SQL's self-healing ordering — an unlocked race
            # still converges to <= cap, unlike count-then-revoke-then-insert).
            max_keys = int(org.get("max_api_keys")
                           or self._default_max_api_keys())
            kid = ulid()
            # C1 (#2110) decision record: the recovery-mint is NOT extended
            # with tenancy kwargs — recovery keys are the owner-level keyless
            # path (created_via='recovery'), so by D2 they resolve as the
            # legacy/owner full-access class (deleg NULL + no scopes). This is
            # intended (E2E-5 zero behavior shift); C3 must NOT assume minted
            # keys are all deleg=0 — recovery keys are owner-class.
            reg.query(
                "CREATE (k:APIKey {id:$id, org_id:$tid, key_hash:$kh, "
                "key_prefix:$kp, created_by:$cb, created_via:'recovery', "
                "created_at:$now, expires_at:null})",
                params={"id": kid, "tid": org_id, "kh": key_hash,
                        "kp": api_key[:10], "cb": "st_" + token_hash[:12],
                        "now": now_iso},
            )
            # re-count AFTER the insert; only revoke when over cap (and a row
            # genuinely existed to revoke). #2426 code-review P2: expired-but-
            # unrevoked durables are excluded from the count AND the revoke-
            # target scan (expired keys don't count — never wedge, and never
            # let the oldest-LIVE key be the revoke collateral for expired
            # rows above the cap; matches the session-key recovery lanes).
            #
            # #1872: victim selection is #1854-LRU — never-used (NULL
            # last_used_at) first, then least-recently-used, created_at as the
            # final tiebreak — NOT oldest-created. Pre-#1872 the oldest-created
            # scan rotated a LIVE credential while a never-used key sat beside
            # it (the #1854 bug class, on the keyless-token lane).
            #
            # The just-minted key is EXCLUDED from the victim scan, and that
            # exclusion is load-bearing rather than tidiness: this lane INSERTs
            # BEFORE it counts (the self-healing ordering above), so the new row
            # is inside the candidate set with last_used_at NULL by
            # construction. Under a NULL-first order it would be the FIRST
            # candidate and revoke ITSELF whenever every pre-existing key had
            # already been used — returning a dead key to the caller. The COUNT
            # still includes the new row; that is what keeps the over-cap check
            # self-healing.
            rows = reg.query(
                "MATCH (k:APIKey {org_id:$tid}) WHERE k.revoked_at IS NULL "
                "AND (k.expires_at IS NULL OR k.expires_at > $now) "
                "AND (k.created_via IS NULL OR k.created_via <> 'bootstrap') "
                "RETURN k.id, k.last_used_at, k.created_at",
                params={"tid": org_id, "now": now_iso},
            ).result_set
            if len(rows) > max_keys:
                victims = [r for r in rows if r[0] != kid]
                victims.sort(key=lambda r: (r[1] is not None, r[1] or "",
                                            r[2] or ""))
                if victims:
                    reg.query(
                        "MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now",
                        params={"id": victims[0][0], "now": now_iso},
                    )
        self._audit(org_id, "st_" + token_hash[:12], "apikey_create",
                     resource_type="apikey", resource_id=kid)
        return {"api_key": api_key, "org_id": org_id,
                "org_name": org.get("name"), "tier": org.get("tier") or "free",
                "graph_name": org.get("graph_name") or f"org_{org_id}"}
