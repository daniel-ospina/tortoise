# Archived sdk-method `membership_get` — REMOVED from the product surface (#11609).
#
# batch:   b2-sdk-retirement-wave-1
# reason:  Read one membership row by id. No PRODUCTION caller: the only call sites were two tests (test_membership_get_returns_membership / _returns_none_for_missing), DELETED with it because the empty branch they exercised went too. Beta n1_console: console plumbing, tenancy admin is not on the SDK. No recorded destination.
# source:  tortoise/sdk.py @ 32011c4dad4b42364868cb0810ad8e7317a7dfb6
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'membership_get'
ARCHIVED_BATCH = 'b2-sdk-retirement-wave-1'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def membership_get(self, membership_id: str) -> dict | None:
        """Get a membership by ID."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (m:Membership {id:$id}) RETURN properties(m)",
            params={"id": membership_id},
        ).result_set
        return rows[0][0] if rows else None
