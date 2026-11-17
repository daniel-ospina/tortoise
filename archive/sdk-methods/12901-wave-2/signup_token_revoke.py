# Archived sdk-method `signup_token_revoke` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Revoke a signup token. Body survives PRIVATE as `_signup_token_revoke`; beta: operator-side provisioning; no destination.
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
ARCHIVED_SYMBOL = 'signup_token_revoke'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def signup_token_revoke(self, token_plaintext: str, org_id: str) -> dict:
        """Revoke a signup token (set revoked_at) — registry parity (#1715).

        Org-scoped: the SignupToken node's org_id must match ``org_id`` or
        the revoke is refused (a caller can only kill their own org's
        token). Idempotent: an unknown / other-org / already-revoked token
        is a no-op, never an error. Returns
        ``{"org_id": str, "status": "revoked" | "already" |
        "not_found" | "not_owned"}`` — the endpoint maps status to
        200/404/403 (the no-oracle uniform-422 contract is preserved for
        malformed tokens upstream; an authenticated caller probing a valid
        token learns only whether it is THEIR org's).

        #1754: (a) the revoke WRITE is atomically org-scoped (parity with
        the SQL lane's UPDATE ... AND org_id = p_org_id) — the pre-read
        can never be raced by a foreign-org node; (b) a node carrying only
        token_hash (no lookup_key) is found via the PBKDF2 fallback (mirror
        signup_token_lookup) so it is REVOCABLE, not just recoverable.
        """
        from tortoise.auth import lookup_hash as _lookup_hash
        lk = _lookup_hash(token_plaintext)
        rows = self._get_registry().query(
            "MATCH (n:SignupToken {lookup_key:$lk}) RETURN properties(n)",
            params={"lk": lk},
        ).result_set
        matches = [r[0] for r in rows]
        if not matches:
            # #1754 (b): PBKDF2 fallback parity with signup_token_lookup — a
            # node carrying only the salted token_hash (minted before
            # lookup_key, or a legacy row) resolves here, so it can be
            # revoked instead of silently no-op'ing as "not_found".
            matches = list(self._verify_hashed_lookup(
                "SignupToken", "token_hash", token_plaintext))
        if not matches:
            return {"org_id": org_id, "status": "not_found"}
        node = matches[0]
        if node.get("org_id") != org_id:
            return {"org_id": org_id, "status": "not_owned"}
        if node.get("revoked_at") is not None:
            return {"org_id": org_id, "status": "already"}
        from datetime import datetime, timezone as _tz  # noqa: I001
        now_iso = datetime.now(_tz.utc).isoformat()  # noqa: UP017
        # #1754 (a): the MATCH is org-scoped so the write itself can never
        # revoke a foreign org's node. Hash-only fallback nodes (no
        # lookup_key) are targeted by their stored salted hash — unique per
        # token (random salt per mint) and org-scoped.
        if node.get("lookup_key"):
            self._get_registry().query(
                "MATCH (n:SignupToken {lookup_key:$lk, org_id:$tid}) "
                "SET n.revoked_at = $now",
                params={"lk": lk, "tid": org_id, "now": now_iso},
            )
        else:
            self._get_registry().query(
                "MATCH (n:SignupToken {token_hash:$th, org_id:$tid}) "
                "SET n.revoked_at = $now",
                params={"th": node["token_hash"], "tid": org_id,
                        "now": now_iso},
            )
        return {"org_id": org_id, "status": "revoked"}
