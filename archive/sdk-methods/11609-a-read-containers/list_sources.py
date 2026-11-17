# Archived sdk-method `list_sources` — REMOVED from the product surface (#11609).
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
ARCHIVED_SYMBOL = 'list_sources'
ARCHIVED_BATCH = '11609-a-read-containers'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '32011c4dad4b42364868cb0810ad8e7317a7dfb6'

# ── verbatim from tortoise/sdk.py ──

    def list_sources(self) -> list[dict]:
        """All PROVENANCE Sources with point counts.

        Returns [{url, sourceKind, points}]. D10 (ONTOLOGY v3.15 §4.4): a
        run-endpoint `doc_<rel>` document is also a `:Source`, but it is NOT a
        provenance source — it carries `documentKind` (and, since #5082,
        `sourceKind='document'`; the TYPE axis, §4.6), so including it would
        (a) add a second `document` row to a `sourceKind`-keyed vocabulary and
        (b) double-count every indexed file (its corpus Source AND its document
        node). The `documentKind IS NULL` partition — not `sourceKind` — is what
        separates the two, and #5082 does not remove the need for it; filtering
        keeps the pre-D10 observable row set byte-identical.
        """
        proj = self._get_proj()
        rows = proj.g.query(
            "MATCH (s:Source) WHERE s.documentKind IS NULL "
            "OPTIONAL MATCH (p:Point)-[:extractedFrom]->(s) "
            "RETURN s.url, s.sourceKind, count(p) AS points "
            "ORDER BY points DESC"
        ).result_set
        return [
            {"url": row[0], "sourceKind": row[1], "points": row[2]}
            for row in rows
        ]
