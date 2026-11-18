# Archived sdk-method `resolve_id` — REMOVED from the product surface (#11609).
#
# batch:   12901-final
# reason:  #12901 FINAL batch: resolve_id left the public SDK surface — privatised (body survives as `_resolve_id`), no single owner-approved replacement. Frozen here so a mistake is inspected against the code that shipped it (#11609 ruling).
# source:  tortoise/sdk.py @ 424368c05d8ee9b26aa2d77e4c5a93a4b207b58f
#
# This file IS the source of record. To restore: copy each block below back into the
# file named in ITS OWN `verbatim from <path>` banner (one symbol can span more than
# one file), then run `uv run python tools/archive_surface.py check` (which fails
# while `archive/` and the live surface disagree).
#
# ⛔ NOT part of the product: not importable from `tortoise`, not served over MCP, and
#    not pointed at by any retired-forward name. Do not import this file.

ARCHIVED_KIND = 'sdk-method'
ARCHIVED_SYMBOL = 'resolve_id'
ARCHIVED_BATCH = '12901-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '424368c05d8ee9b26aa2d77e4c5a93a4b207b58f'

# ── verbatim from tortoise/sdk.py ──

    def resolve_id(self, id_str: str) -> dict | None:
        """Resolve any Point ID (legacy / numeric / ULID) to the canonical point.

        Returns the Point dict if found, None otherwise.

        Strategy:
        1. Exact match on Point.id
        2. If the id looks like a numeric reference, search by content/properties
           (best-effort — legacy numeric IDs may not have explicit mappings yet)

        Non-destructive — read-only operation.

        Limitations:
        - For legacy prefix IDs (letta-*, op-*, etc.) with no exact match,
          there is currently no migration mapping to a canonical ULID.
          This is a known gap covered by docs/migrations/id-normalization-plan.md.
        - The resolution is exact-id-first; fuzzy matching is future work.
        """
        proj = self._get_proj()

        # 1. Exact match
        rows = proj.g.query(
            "MATCH (n:Point {id: $id}) RETURN n.id, n.content, n.pointKind, n.status",
            params={"id": id_str},
        ).result_set
        if rows:
            return self.get_point(rows[0][0])

        # 2. If numeric, try finding a point whose properties reference it
        #    (best-effort — many numeric IDs are native node IDs and would have
        #     matched in step 1; this handles edge cases like internal refs)
        if id_str.isdigit():
            # Search for points whose content or any property contains the numeric ID
            rows = proj.g.query(
                "MATCH (n:Point) WHERE n.content CONTAINS $id_str "
                "RETURN n.id, n.content LIMIT 5",
                params={"id_str": id_str},
            ).result_set
            if rows:
                _logger.info(
                    "resolve_id: numeric %r not found as direct id; "
                    "returning best-match point %r", id_str, rows[0][0]
                )
                return self.get_point(rows[0][0])

        return None
