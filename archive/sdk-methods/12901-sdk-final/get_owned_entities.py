# Archived sdk-method `get_owned_entities` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_get_owned_entities`; recorded destination `get_entity` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'get_owned_entities'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def get_owned_entities(self, subject_id: str) -> list:
        """Return all entities owned by a Subject (governance query)."""
        proj = self._get_proj()
        # Issue #327: start from the labeled, indexed Subject and traverse
        # ownedBy inward. Narrowing: ownedBy/memberOf targets are canonically
        # Subject (#216); non-Subject targets are out of contract.
        # #3633 route-then-refuse: the `s.id = $sid OR s.name = $sid` start
        # unioned BOTH same-name carriers' owned entities. Resolve to exactly
        # one live id (>=2 -> AmbiguousEntityName), then traverse from that id.
        resolved_sid = resolve_entity_id_same_entity(
            proj.g, "Subject", subject_id)
        r = proj.g.query(
            # #6976: load-bearing `WITH s` — without it FalkorDB 6.0.0 drops the
            # id predicate at the re-binding MATCH below (foreign rows).
            # (#3633 removed the name arm that made this an id/name predicate.)
            "MATCH (s:Subject) WHERE s.id = $sid "
            "WITH s "
            "MATCH (s)<-[:ownedBy]-(e) RETURN properties(e) LIMIT 100",
            params={"sid": resolved_sid},
        )
        return [dict(row[0]) for row in r.result_set]
