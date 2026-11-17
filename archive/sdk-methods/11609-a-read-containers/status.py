# Archived sdk-method `status` — REMOVED from the product surface (#11609).
#
# batch:   11609-a-read-containers
# reason:  retired into graph_overview(section='status') (#11609 Phase 2, #4282 row 9): the public name fails and names the container
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
ARCHIVED_SYMBOL = 'status'
ARCHIVED_BATCH = '11609-a-read-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def status(self) -> dict:
        """Graph health + entity counts + FalkorDB connectivity.

        Returns {connected, counts: {Point, Event, ...}, total_entities}.
        """
        proj = self._get_proj()
        connected = False
        try:
            proj.g.query("MATCH (n) RETURN count(n) LIMIT 1")
            connected = True
        except Exception:
            pass
        counts = self.taxonomy()
        total = sum(counts.values())
        result = {"connected": connected, "counts": counts, "total_entities": total}
        if self._namespace:
            result["namespace"] = self._namespace
        return result
