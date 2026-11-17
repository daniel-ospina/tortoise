# Archived sdk-method `org_get` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Control-plane org read. Body survives PRIVATE as `_org_get`; recorded destination `get_organisation_account` (beta row 27). No hint: beta never names org_get.
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
ARCHIVED_SYMBOL = 'org_get'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def org_get(self, org_id: str) -> dict | None:
        """Get an org by ID. Returns None if not found."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (t:Team {id:$id}) RETURN properties(t)",
            params={"id": org_id},
        ).result_set
        return rows[0][0] if rows else None
