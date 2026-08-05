"""Shock propagation and confidence computation for FalkorProjection."""
from __future__ import annotations

from collections import deque


class _PropagationMixin:
    """Mixin: shock propagation + confidence helper methods."""

    def propagate_shock(self, epicenter_id: str, *, max_depth: int = 2,
                        damping: float = 0.5, threshold: float = 0.05
                        ) -> dict[str, tuple[float, float]]:
        """BFS shock propagation through IMPL (supports) and NAND (contradicts)
        edges.  Ported from /Users/home/eldato/operations/memory/epistemic.py.

        Each BFS step carries the parent's computed confidence into the child's
        _compute_confidence — the parent signal blends with local edge-ratio
        evidence BEFORE the inertia damping is applied.  Without this, _compute_
        confidence sees only static edge counts and the shock never flows."""
        changed: dict[str, tuple[float, float]] = {}
        visited: set[str] = set()

        # Skip if epicenter is deprecated/superseded
        status = self._node_status(epicenter_id)
        if status and status != 'live':
            return {}

        queue: deque[tuple[str, int, float | None]] = deque(
            [(epicenter_id, 0, None)]
        )

        while queue:
            node_id, depth, parent_conf = queue.popleft()
            if depth > max_depth or node_id in visited:
                continue
            visited.add(node_id)

            old = self._confidence(node_id)
            new = self._compute_confidence(node_id, parent_conf)
            if depth > 0:
                new = old * damping + new * (1 - damping)
            new = round(new, 4)

            if abs(new - old) > threshold:
                self.g.query(
                    "MATCH (n:Point {id:$id}) SET n.confidence=$v",
                    params={"id": node_id, "v": new},
                )
                changed[node_id] = (old, new)

            if depth < max_depth:
                for nid in self._neighbors(node_id):
                    if nid not in visited:
                        queue.append((nid, depth + 1, new))

        return changed

    def _compute_confidence(self, node_id: str,
                            parent_confidence: float | None = None) -> float:
        """Weighted confidence from neighbor beliefs, not just edge counts.

        Each IMPL edge contributes the neighbor's current confidence;
        each NAND edge contributes (1 - neighbor's confidence) — so a
        confident contradiction hurts more than a tentative one. Default 0.5
        when no edges or no neighbors have confidence set.
        """
        s_rows = self.g.query(
            "MATCH (n:Point {id:$id})-[r:IMPL]-(m:Point) "
            "RETURN coalesce(m.confidence, 0.5)",
            params={"id": node_id},
        ).result_set
        c_rows = self.g.query(
            "MATCH (n:Point {id:$id})-[r:NAND]-(m:Point) "
            "RETURN coalesce(m.confidence, 0.5)",
            params={"id": node_id},
        ).result_set

        s_weighted = sum(r[0] for r in s_rows)
        c_weighted = sum(r[0] for r in c_rows)
        total = s_weighted + c_weighted
        base = s_weighted / total if total else 0.5
        if parent_confidence is None:
            return base
        return base * 0.5 + parent_confidence * 0.5

    def _confidence(self, node_id: str) -> float:
        r = self.g.query(
            "MATCH (n:Point {id:$id}) RETURN coalesce(n.confidence, 0.5)",
            params={"id": node_id},
        ).result_set
        return float(r[0][0]) if r else 0.5

    def _node_status(self, node_id: str) -> str | None:
        """Return the status of a Point node, or None if not found."""
        r = self.g.query(
            "MATCH (n:Point {id:$id}) RETURN n.status",
            params={"id": node_id},
        ).result_set
        return r[0][0] if r else None

    def _neighbors(self, node_id: str) -> list[str]:
        """All Points reachable via IMPL or NAND edges (both directions).

        Hops through operator nodes: (claim)-[r]-(op:Point {is_operator:true})-[r2]-(other).
        A point's neighbors are the other endpoints of any operator it participates in,
        regardless of source/target role — this is what makes EP affected-set expansion
        work for directional IMPL and bidirectional hasPart/NAND alike (#86).
        """
        rows = self.g.query(
            "MATCH (n:Point {id:$id})-[r]-(op:Point {is_operator:true})-[r2]-(m:Point) "
            "WHERE m.id <> $id RETURN DISTINCT m.id",
            params={"id": node_id},
        ).result_set
        return [r[0] for r in rows]
