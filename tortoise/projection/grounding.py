"""Grounding computation for FalkorProjection — solve (I - lam*M)g = a."""
from __future__ import annotations

import numpy as np


class _GroundingMixin:
    """Mixin: grounding + staleness methods."""

    def compute_grounding(self, lam: float = 0.6) -> dict[str, float]:
        """Solve (I - lam*M)g = a and write n.grounding on every :Point.

        M is the row-normalized adjacency from :IMPL and :NAND edges (symmetric).
        a_i = 1.0 for resolution-event / resolution-vector / humanApproval Points,
        0 otherwise.

        Returns {point_id: grounding_value}."""
        # 1. Read all Point IDs → index mapping
        rows = self.g.query("MATCH (n:Point {status: 'live'}) RETURN n.id ORDER BY n.id").result_set
        ids = [r[0] for r in rows]
        n = len(ids)
        idx = {pid: i for i, pid in enumerate(ids)}
        if n == 0:
            return {}

        # 2. Build sparse symmetric adjacency from :IMPL and :NAND edges
        try:
            from scipy.sparse import coo_matrix
            from scipy.sparse.linalg import spsolve
            from scipy.sparse import eye as speye
        except ImportError:
            raise ImportError(
                "scipy required for compute_grounding; install with: pip install scipy"
            )
        rows_list, cols_list = [], []
        for rel in ("IMPL", "NAND"):
            edges = self.g.query(
                f"MATCH (a:Point {{status: 'live'}})-[:{rel}]->(b:Point {{status: 'live'}}) RETURN a.id, b.id"
            ).result_set
            for src, tgt in edges:
                if src in idx and tgt in idx:
                    i, j = idx[src], idx[tgt]
                    rows_list.extend([i, j])
                    cols_list.extend([j, i])  # symmetric: relevance is undirected

        # 3. Row-normalize → M (sparse)
        A = coo_matrix(([1.0] * len(rows_list), (rows_list, cols_list)), shape=(n, n)).tocsr()
        rowsum = np.array(A.sum(axis=1)).ravel()
        rowsum[rowsum == 0] = 1.0
        D_inv = coo_matrix(
            (1.0 / rowsum, (range(n), range(n))), shape=(n, n)
        ).tocsr()
        M = D_inv @ A

        # 4. Activity vector a from resolution events, resolution vectors, and
        #    human approvals (#531). Exclude operator points — they propagate,
        #    they don't originate.
        #    ponytail: uniform activity (no timestamps to EWMA over); add
        #    EWMA decay (alpha=0.3) when resolution events carry timestamps.
        res = self.g.query(
            "MATCH (n:Point) WHERE n.pointKind IN ['resolution-event','resolution-vector','humanApproval'] "
            "AND n.is_operator = false RETURN n.id"
        ).result_set
        a = np.zeros(n)
        for (pid,) in res:
            if pid in idx:
                a[idx[pid]] = 1.0

        # 5. g = (I - lam*M)^-1 a  (sparse linear system)
        g = spsolve(speye(n, format='csr') - lam * M, a)

        # 6. Write back to each :Point node
        for pid, i in idx.items():
            self.g.query(
                "MATCH (n:Point {id:$id}) SET n.grounding=$g",
                params={"id": pid, "g": float(g[i])},
            )

        return {pid: float(g[idx[pid]]) for pid in ids}

    def stale_points(self, days: int = 30, limit: int = 50) -> list[dict]:
        """Find Points not updated in N days (older createdAt as fallback)."""
        from datetime import datetime, timezone, timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        rows = self.g.query(
            "MATCH (n:Point) "
            "WHERE n.is_operator = false "
            "  AND coalesce(n.updatedAt, n.createdAt, '') < $cutoff "
            "RETURN n.id, n.content, n.pointKind, "
            "  coalesce(n.updatedAt, n.createdAt) as lastUpdate "
            "ORDER BY lastUpdate ASC LIMIT $limit",
            params={"cutoff": cutoff, "limit": limit},
        ).result_set
        return [{"id": r[0], "content": r[1], "pointKind": r[2],
                 "lastUpdate": r[3]} for r in rows]
