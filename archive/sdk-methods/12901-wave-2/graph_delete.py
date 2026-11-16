# Archived sdk-method `graph_delete` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Soft-delete of a Graph node (tombstone). Body survives PRIVATE as `_graph_delete`; recorded destination `delete_memory_graph` (rows 30-33 range).
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
ARCHIVED_SYMBOL = 'graph_delete'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def graph_delete(self, org_id: str, graph_id: str) -> bool:
        """Soft-delete a Graph node (status='deleted' tombstone — the v1
        lifecycle, C2 #2111). Returns True when a non-default node was
        tombstoned; False when unknown OR the default (callers map to
        404/403). Pre-C1 nodes without status gain it on delete.

        #2304: stamps ``deleted_at`` (the trash grace window's start — the
        purge enforces the _GRAPH_PURGE_GRACE_DAYS recovery period off it; legacy tombstones
        (deleted_at absent) predate the prop and are treated as past-grace).
        """
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (g:Graph {id:$gid, org_id:$tid}) RETURN g.kind",
            params={"gid": graph_id, "tid": org_id},
        ).result_set
        if not rows:
            return False
        if rows[0][0] == "default":
            return False
        from datetime import datetime
        reg.query(
            "MATCH (g:Graph {id:$gid, org_id:$tid}) "
            "SET g.status = 'deleted', g.deleted_at = $ts",
            params={"gid": graph_id, "tid": org_id,
                    "ts": datetime.now(UTC).isoformat()},
        )
        return True
