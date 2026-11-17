# Archived sdk-method `apikey_revoke` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Revoke a key (soft delete). Body survives PRIVATE as `_apikey_revoke`; recorded destination `revoke_key` (row 36).
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
ARCHIVED_SYMBOL = 'apikey_revoke'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def apikey_revoke(self, key_id: str) -> dict:
        """Revoke an API key (soft delete — sets revoked_at). Idempotent."""
        from datetime import datetime, timezone
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (k:APIKey {id:$id}) RETURN k.revoked_at, k.org_id",
            params={"id": key_id},
        ).result_set
        if not rows:
            return {"revoked": False, "reason": "not found"}
        if rows[0][0] is not None:
            return {"revoked": True, "already": True, "key_id": key_id}
        now = datetime.now(timezone.utc).isoformat()  # noqa: UP017
        reg.query(
            "MATCH (k:APIKey {id:$id}) SET k.revoked_at = $now",
            params={"id": key_id, "now": now},
        )
        self._audit(rows[0][1], None, "apikey_revoke",
                     resource_type="apikey", resource_id=key_id)
        return {"revoked": True, "key_id": key_id, "revoked_at": now}
