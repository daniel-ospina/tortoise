# Archived sdk-method `list_pointkinds` — REMOVED from the product surface (#11609).
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
ARCHIVED_SYMBOL = 'list_pointkinds'
ARCHIVED_BATCH = '11609-a-read-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def list_pointkinds(self) -> list[dict]:
        """All pointKinds present in the graph with counts. Returns [{kind, count, pack}].

        #2205: same non-operator predicate as summarize_structure (absence-or-
        false + op_type IS NULL) so per-kind stats never drop legacy Points
        that predate the is_operator property — a bare `n.is_operator = false`
        made per-kind stats lie on imported graphs.
        """
        proj = self._get_proj()
        not_operator = ("(n.is_operator IS NULL OR n.is_operator = false) "
                        "AND n.op_type IS NULL")
        rows = proj.g.query(
            "MATCH (n:Point) "
            f"WHERE {not_operator} "
            "AND n.pointKind IS NOT NULL "
            "RETURN n.pointKind, count(n) ORDER BY count(n) DESC"
        ).result_set
        result: list[dict] = []
        for row in rows:
            kind = row[0]
            count = row[1]
            pack = kind.split(":", 1)[0] if ":" in kind else ""
            result.append({"kind": kind, "count": count, "pack": pack})
        return result
