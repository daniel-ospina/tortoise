# Archived sdk-method `list_batches` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_list_batches`; recorded destination `list_knowledge` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'list_batches'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def list_batches(self, limit: int = 20) -> list[dict]:
        """Batch discovery — the most recent distinct batch_ids (A13).

        Returns ``[{batch_id, points, direct_edges}]`` ordered by the newest
        stamped point's ``createdAt`` descending (a batch whose ONLY stamped
        artifacts are direct edges sorts by the edge's newest timestamp),
        capped at ``limit`` (default 20). Entities/sources are out of stamp
        scope (never counted). The ``points``/``direct_edges`` counts are the
        aggregate counts per batch (not the full rows — use ``list_batch``
        for the audit detail). Edge-only batches ARE discoverable (the
        transport-death recovery path must not lose them).
        """
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("list_batches: limit must be a positive int")
        proj = self._get_proj()
        # point-stamped batches: (bid, newest point createdAt, point count)
        rows = proj.g.query(
            "MATCH (n:Point) WHERE n.batch_id IS NOT NULL "
            "WITH n.batch_id AS bid, max(n.createdAt) AS newest, count(n) AS c "
            "RETURN bid, newest, c",
        ).result_set
        # edge-stamped batches: edge-only batches (endpoints carry no
        # batch_id) must ALSO be discoverable — the transport-death recovery
        # path must not lose them
        edge_rows = proj.g.query(
            "MATCH ()-[r:IMPL|NAND]->() "
            "WHERE r.batch_id IS NOT NULL "
            "WITH r.batch_id AS bid, max(r.createdAt) AS newest, count(r) AS c "
            "RETURN bid, newest, c",
        ).result_set
        newest_by_bid: dict[str, str] = {}
        by_bid: dict[str, dict] = {}
        for (bid, newest, c) in rows:
            by_bid[bid] = {"batch_id": bid, "points": c, "direct_edges": 0}
            if newest is not None and (bid not in newest_by_bid
                                       or newest > newest_by_bid[bid]):
                newest_by_bid[bid] = newest
        for (bid, newest, c) in edge_rows:
            if bid in by_bid:
                by_bid[bid]["direct_edges"] = c
            else:
                by_bid[bid] = {"batch_id": bid, "points": 0,
                               "direct_edges": c}
            if newest is not None and (bid not in newest_by_bid
                                       or newest > newest_by_bid[bid]):
                newest_by_bid[bid] = newest
        items = list(by_bid.values())
        items.sort(key=lambda b: newest_by_bid.get(b["batch_id"], ""),
                   reverse=True)
        return items[:limit]
