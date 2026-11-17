# Archived sdk-method `trash_graphs` — REMOVED from the product surface (#11609).
#
# batch:   12901-wave-2
# reason:  Tombstoned custom graphs (the trash view). Body survives PRIVATE as `_trash_graphs`; beta rules it OUR MAINTENANCE, never product surface; no destination.
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
ARCHIVED_SYMBOL = 'trash_graphs'
ARCHIVED_BATCH = '12901-wave-2'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = 'd741bd4a5a8d45198ec9c8fd2324c93ca746af5e'

# ── verbatim from tortoise/sdk.py ──

    def trash_graphs(self, org_id: str) -> list[dict]:
        """#2304: tombstoned custom nodes of an org (the trash list) — the
        owner restore surface. ``deleted_at`` absent = legacy tombstone
        (predates the prop; purge treats it as past-grace). Purged nodes
        (purged_at set) are excluded — data is physically gone."""
        reg = self._get_registry()
        rows = reg.query(
            "MATCH (g:Graph {org_id:$tid, status:'deleted'}) "
            "WHERE coalesce(g.kind, 'custom') <> 'default' "
            "AND g.purged_at IS NULL "
            "RETURN g.id, g.name, g.namespace, g.deleted_at",
            params={"tid": org_id},
        ).result_set
        return [
            {"graph_id": r[0], "name": r[1], "namespace": r[2],
             "deleted_at": r[3]}
            for r in rows
        ]
