# Archived sdk-method `create_derivation` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_create_derivation`; recorded destination `link_entities` (#12901 final batch, #4282 Phase 2).
# source:  tortoise/sdk.py @ 1231060fd2be9205b9b3bba1fd315e9b19b3602e
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'create_derivation'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def create_derivation(self, src_id: str, dst_id: str) -> dict:
        """Create a wasDerivedFrom edge: (dst)-[:wasDerivedFrom]->(src).

        PROV-O entity derivation — dst was derived from src. Distinct from
        extractedFrom (claim provenance) — wasDerivedFrom is Object→Object
        entity derivation.
        """
        proj = self._get_proj()
        ok = proj.create_edge(dst_id, src_id, "wasDerivedFrom")
        return {"derived": ok, "src": src_id, "dst": dst_id}
