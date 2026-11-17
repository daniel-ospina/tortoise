# Archived sdk-method `invitation_revoke` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Revoke a pending invitation. Body survives PRIVATE as `_invitation_revoke`; beta: the invite UX belongs to the console; no destination.
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
ARCHIVED_SYMBOL = 'invitation_revoke'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def invitation_revoke(self, invitation_id: str) -> dict:
        """Revoke an invitation (soft delete). Idempotent."""
        inv = self.invitation_get_by_id(invitation_id)
        if inv is None:
            return {"revoked": False, "reason": "not found"}
        if inv.get("status") == "revoked":
            return {"revoked": True, "already": True,
                    "invitation_id": invitation_id}
        reg = self._get_registry()
        reg.query(
            "MATCH (i:Invitation {id:$id}) SET i.status = 'revoked'",
            params={"id": invitation_id},
        )
        self._audit(inv["org_id"], None, "invitation_revoke",
                     resource_type="invitation", resource_id=invitation_id)
        return {"revoked": True, "invitation_id": invitation_id}
