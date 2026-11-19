# Archived sdk-method `list_batch` — REMOVED from the product surface (#11609).
#
# batch:   12901-sdk-final
# reason:  Body survives PRIVATE as `_list_batch`; recorded destination `list_knowledge` (#12901 final batch, #4282 Phase 2).
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
ARCHIVED_SYMBOL = 'list_batch'
ARCHIVED_BATCH = '12901-sdk-final'
ARCHIVED_FROM = 'tortoise/sdk.py'
ARCHIVED_AT = '1231060fd2be9205b9b3bba1fd315e9b19b3602e'

# ── verbatim from tortoise/sdk.py ──

    def list_batch(self, batch_id: str) -> dict:
        """Audit the stamped artifacts of ONE bundle (epic #902 A13).

        Returns the bundle's STAMPED set — every Point carrying this
        ``batch_id`` (created OR ADOPTED via dedup — E2E-10 row 14: a
        batch-less pre-existing point acquires the bundle's batch_id on its
        first dedup hit) and every operator-less direct edge carrying it.
        Operator/mitigation Points are ordinary Points (stamped the same
        way, §4.2). Entities and sources are OUT of stamp scope on the
        INGEST path (bundle entities/sources are never stamped — a bundle
        item carrying ``batch_id`` is rejected at Phase-1; the raw-SDK
        props-passthrough surface can still stamp an entity via
        ``create_subject(batch_id=...)`` — a DOCUMENTED interim gap until
        #785 adopts the shared ``_sanitize_props`` batch_id rejection, the
        A4-gated final sub-step). Editorial supersede artifacts are outside
        audit: the superseding (editorial) point is not stamped with the
        originating bundle's batch_id, and repointed edges KEEP their
        originating batch_id (E2E-11.6).

        Completeness across ``rebuild_all``: batch_id on Points is restored
        from the pre-wipe live-graph snapshot (projection pass-1b tail) —
        a rebuild of the SAME store keeps the POINT half of the audit
        intact, and since #8499 the DIRECT-EDGE half survives too: the
        ``DirectEdgeCreated`` descriptors that used to be journaled-but-never-
        replayed now have a real pass-2b fold (``direct_edge_created_events``).
        The remaining A10 pass-2b gap is ``BatchIdStamped`` on the
        journal-only leg (a fresh-store rebuild from the journal alone) — NOT
        yet landed, so journal-only rebuilds lose batch_id today (recorded
        deferral).

        Returns ``{"batch_id", "points": [{id, pointKind, status,
        is_operator, op_type, content}], "direct_edges": [{direct_edge,
        from, to, direction, confidence, weight, label}], "counts":
        {"points": n, "direct_edges": m}}``.
        """
        if not isinstance(batch_id, str) or not batch_id:
            raise ValueError("list_batch: batch_id must be a non-empty string")
        proj = self._get_proj()
        point_rows = proj.g.query(
            "MATCH (n:Point {batch_id:$bid}) "
            "RETURN n.id, n.pointKind, n.status, "
            "       (n.is_operator = true), n.op_type, n.content "
            "ORDER BY n.id",
            params={"bid": batch_id},
        ).result_set
        # #7902: a direct IMPL/NAND edge may connect ANY two plain epistemic
        # nodes (:Point/:Subject/:Object/:Event). `list_batches` counts the
        # edge label-agnostically (`MATCH ()-[r:IMPL|NAND]->()`), so a
        # hand-spelled (a:Point)/(b:Point) here made the two surfaces
        # disagree: the batch summary reported the edge and this audit detail
        # reported none.
        edge_rows = proj.g.query(
            "MATCH (a)-[r:IMPL|NAND]->(b) "
            f"WHERE {epistemic_disjunction('a')} "
            f"AND {epistemic_disjunction('b')} "
            "AND r.batch_id = $bid "
            "AND coalesce(a.is_operator, false) = false "
            "AND a.op_type IS NULL "
            "AND coalesce(b.is_operator, false) = false "
            "AND b.op_type IS NULL "
            "RETURN type(r), a.id, b.id, "
            "       coalesce(r.direction, 'bidirectional'), "
            "       r.confidence, r.weight, r.label "
            "ORDER BY a.id",
            params={"bid": batch_id},
        ).result_set
        points = [{"id": r[0], "pointKind": r[1], "status": r[2],
                   "is_operator": bool(r[3]), "op_type": r[4],
                   "content": r[5]} for r in point_rows]
        edges = [{"direct_edge": r[0], "from": r[1], "to": r[2],
                  "direction": r[3], "confidence": r[4], "weight": r[5],
                  "label": r[6]} for r in edge_rows]
        return {"batch_id": batch_id, "points": points,
                "direct_edges": edges,
                "counts": {"points": len(points),
                            "direct_edges": len(edges)}}
