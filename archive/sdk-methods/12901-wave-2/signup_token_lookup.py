# Archived sdk-method `signup_token_lookup` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Resolve an agent self-signup token. Body survives PRIVATE as `_signup_token_lookup`; beta: operator-side provisioning, not product surface; no destination.
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
ARCHIVED_SYMBOL = 'signup_token_lookup'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def signup_token_lookup(self, token_plaintext: str) -> dict | None:
        """Resolve a signup token → its node props; None when unknown/revoked.

        Registry parity for resolve_signup_token (Supabase lane). The caller
        (hosted_api) maps None to the UNIFORM 422 invalid_signup_token.
        """
        if not isinstance(token_plaintext, str) or not token_plaintext:
            # #1709 fixer P2.1: a non-str token must be "unknown" (None →
            # uniform 422), NEVER an AttributeError from the registry scan
            # (verify_api_key encodes the plaintext) — defense-in-depth
            # behind hosted_api's format gate.
            return None
        # [SECOND-MODEL-GATE] P2: exact-match on the deterministic lookup_key
        # (SHA-256+pepper, stored at mint) FIRST — avoids the O(keys) PBKDF2
        # full-scan per probe (a distributed-IP DoS vector on multi-org
        # selfhosts). PBKDF2 verify below is defense-in-depth (the minted
        # node carries both hashes).
        from tortoise.auth import lookup_hash as _lookup_hash
        lk = _lookup_hash(token_plaintext)
        rows = self._get_registry().query(
            "MATCH (n:SignupToken {lookup_key:$lk}) RETURN properties(n)",
            params={"lk": lk},
        ).result_set
        matches = [r[0] for r in rows if r[0].get("revoked_at") is None]
        if not matches:
            matches = [
                p for p in self._verify_hashed_lookup("SignupToken", "token_hash", token_plaintext)
                if p.get("revoked_at") is None
            ]
        return matches[0] if matches else None
