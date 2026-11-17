# Archived sdk-method `invitation_get_by_id` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Read one invitation row. Body survives PRIVATE as `_invitation_get_by_id`; beta: the invite UX belongs to the console; no destination.
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
ARCHIVED_SYMBOL = 'invitation_get_by_id'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def invitation_get_by_id(self, invitation_id: str) -> dict | None:
        """Get an invitation by its ULID."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (i:Invitation {id:$id}) RETURN properties(i)",
            params={"id": invitation_id},
        ).result_set
        return rows[0][0] if rows else None
