# Archived sdk-method `diary_read` — REMOVED from the product surface (#11609).
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
ARCHIVED_SYMBOL = 'diary_read'
ARCHIVED_BATCH = '11609-a-read-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def diary_read(self, agent_name: str, last_n: int = 10,
                   wing: str | None = None) -> list[dict]:
        """Read recent diary entries for an agent, newest first."""
        proj = self._get_proj()
        if wing:
            rows = proj.g.query(
                "MATCH (n:Point {pointKind:'diary', authoredBy:$agent, wing:$wing}) "
                "WHERE n.is_operator = false "
                "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $lim",
                params={"agent": agent_name, "wing": wing, "lim": last_n},
            ).result_set
        else:
            rows = proj.g.query(
                "MATCH (n:Point {pointKind:'diary', authoredBy:$agent}) "
                "WHERE n.is_operator = false "
                "RETURN properties(n) ORDER BY n.createdAt DESC LIMIT $lim",
                params={"agent": agent_name, "lim": last_n},
            ).result_set
        return [r[0] for r in rows]
