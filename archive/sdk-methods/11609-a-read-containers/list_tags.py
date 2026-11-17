# Archived sdk-method `list_tags` — REMOVED from the product surface (#11609).
#
# batch:   11609-a-read-containers
# reason:  retired into the reader container that absorbs its question (#11609 Phase 2, #4282 rows 3-10); the public name fails and names the container
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
ARCHIVED_SYMBOL = 'list_tags'
ARCHIVED_BATCH = '11609-a-read-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def list_tags(self) -> list[dict]:
        """All Tag names with count of tagged Points. Returns [{name, count}].

        Orphaned :Tag nodes (no TAGGED edges) are garbage-collected when
        edges are removed (update_point tag sync + delete_point, #485), so
        count 0 entries should not normally appear.
        """
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (t:Tag) "
            "OPTIONAL MATCH (p:Point)-[:TAGGED]->(t) "
            "RETURN t.name AS name, count(p) AS count "
            "ORDER BY name"
        ).result_set
        return [{"name": row[0], "count": row[1]} for row in rows]
