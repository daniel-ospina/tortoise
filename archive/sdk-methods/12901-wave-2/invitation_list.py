# Archived sdk-method `invitation_list` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  List an org's pending invitations. Body survives PRIVATE as `_invitation_list`; beta: the invite UX belongs to the console; no destination.
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
ARCHIVED_SYMBOL = 'invitation_list'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def invitation_list(self, org_id: str) -> list[dict]:
        """List invitations for an org (no token hashes)."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (i:Invitation {org_id:$tid}) "
            "RETURN i.id, i.email, i.role, i.created_by, i.created_at, "
            "i.expires_at, i.accepted_at, i.status",
            params={"tid": org_id},
        ).result_set
        invs = []
        for r in rows:
            invs.append({
                "id": r[0], "email": r[1], "role": r[2],
                "created_by": r[3], "created_at": r[4],
                "expires_at": r[5], "accepted_at": r[6], "status": r[7],
            })
        return invs
