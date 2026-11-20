# Archived sdk-method `query_points_by_tag` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_query_points_by_tag`; recorded destination `list_knowledge` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'query_points_by_tag'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def query_points_by_tag(self, tag: str, *,
                            _record_reads: bool = True) -> list[dict]:
        """Return Points connected via TAGGED edge to the given Tag name.

        #7983: this returns the WHOLE tag population, so recording it is right
        for the SDK caller and wrong for a paginated user that receives one
        page of it — that caller passes ``_record_reads=False`` and records the
        page it serves. Recording the population would bias ``f`` HIGH.
        """
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (p:Point)-[:TAGGED]->(t:Tag {name:$tag}) "
            "RETURN properties(p) "
            "ORDER BY p.createdAt DESC",
            params={"tag": tag},
        ).result_set
        points = [r[0] for r in rows]
        if _record_reads:
            _ledger_record("Point", points, kind="lookup")  # #7983: a product read
        return points
