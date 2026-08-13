#!/usr/bin/env python3
"""Smoke test for Tortoise — validates FalkorDB + EP infrastructure.

8-step verification with NO LLM dependency:
  1. Connect to FalkorDB (server or embedded)
  2. Create graph "_smoke_test"
  3. Add 2 points via EventAPI (tortoise.api)
  4. Add 1 NAND operator connecting the 2 points
  5. Run EP propagation via tortoise.ep.TortoiseEP
  6. Verify confidences: finite floats in [0,1]
  7. Query MATCH (n:Point) RETURN count(n) → assert >= 2
  8. Delete graph "_smoke_test"

Usage:
  python scripts/smoke_test.py

Exit 0 on PASS, 1 on FAIL with reason on stderr.
"""
from __future__ import annotations

import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from tortoise.api import EventAPI, provenance
from tortoise.ep import TortoiseEP
from tortoise.log import EventLog


# ── Minimal projection (duck-types FalkorProjection) ──────────────

class _SmokeProj:
    """Thin projection that creates nodes/edges for PointAdded and
    OperatorAdded events.  Provides .g and ._neighbors() so EP works."""
    def __init__(self, g):
        self.g = g

    def apply(self, event):
        ev = event
        if ev.get("point"):
            ev = {**ev, **ev["point"]}
        t = ev["type"]
        if t in ("PointAdded", "OperatorAdded"):
            p = ev["point"]
            op = p.get("operator")
            self.g.query(
                "MERGE (n:Point {id:$id}) "
                "SET n.content=$c, n.context=$x, "
                "    n.is_operator=$io, n.op_type=$ot",
                params={"id": p["id"], "c": p.get("content", ""),
                        "x": p.get("context", ""),
                        "io": bool(op), "ot": op["op_type"] if op else None},
            )
            if op:
                rel = op["op_type"]
                for src in op["inputs"]:
                    self.g.query(
                        f"MATCH (o:Point {{id:$oid}}) "
                        f"MATCH (s:Point {{id:$sid}}) "
                        f"MERGE (o)-[:{rel}]->(s)",
                        params={"oid": p["id"], "sid": src},
                    )

    def _neighbors(self, node_id: str) -> list[str]:
        rows = self.g.query(
            "MATCH (n:Point {id:$id})-[r:IMPL|NAND]-(m:Point) "
            "RETURN DISTINCT m.id",
            params={"id": node_id},
        ).result_set
        return [r[0] for r in rows]

    def close(self) -> None:
        pass


# ── Connection ─────────────────────────────────────────────────────

def _connect(graph_name: str, embedded_path: str | None = None) -> tuple[str, object, object]:
    """Try FalkorDB server (localhost:16379), fall back to FalkorDBLite.

    Returns (mode, db, graph) where mode is 'server' or 'embedded'.
    embedded_path is required for embedded mode (caller manages lifecycle).
    """
    # Try server first
    try:
        from falkordb import FalkorDB as ServerDB
        db = ServerDB(host='localhost', port=16379)
        g = db.select_graph(graph_name)
        g.query("MATCH (n) RETURN count(n) LIMIT 1")  # liveness check
        return 'server', db, g
    except Exception:
        pass
    # Fall back to embedded
    from redislite.falkordb_client import FalkorDB as EmbeddedDB  # noqa: redis-guard — intentional bypass (issue #176)
    db = EmbeddedDB(embedded_path)
    g = db.select_graph(graph_name)
    return 'embedded', db, g


def _delete_graph(db, graph_name: str, mode: str) -> None:
    """Best-effort cleanup of the test graph."""
    try:
        if mode == 'server':
            db.delete_graph(graph_name)
        else:
            g = db.select_graph(graph_name)
            g.query("MATCH (n) DETACH DELETE n")
    except Exception as e:
        print(f"Non-fatal cleanup error: {e}", file=sys.stderr)


# ── Main ───────────────────────────────────────────────────────────

def main() -> int:
    start = time.time()
    graph_name = f"_smoke_test_{os.getpid()}"

    with tempfile.TemporaryDirectory(prefix="tortoise_smoke_") as tmpdir:
        # ── Step 1: Connect ──────────────────────────────────────────
        mode, db, g = _connect(graph_name, os.path.join(tmpdir, "falkor.db"))
        label = 'localhost:16379' if mode == 'server' else 'FalkorDBLite'
        print(f"[1/8] Connected: {mode} ({label})", file=sys.stderr)

        # ── Step 2: Clear graph ──────────────────────────────────────
        try:
            g.query("MATCH (n) DETACH DELETE n")
        except Exception as e:
            print(f"Non-fatal clear error: {e}", file=sys.stderr)
        print(f"[2/8] Graph '{graph_name}' ready", file=sys.stderr)

        # ── Step 3: Add 2 points via EventAPI ────────────────────────
        log = EventLog(os.path.join(tmpdir, "events.jsonl"))
        proj = _SmokeProj(g)
        api = EventAPI(log, initiated_by="extractor", agent_id="smoke_test",
                       projection=proj)
        prov = provenance("smoke_test", [0, 1], "test", extracted_by="smoke@0")
        p1 = api.add_point("Earth is round", "ctx", prov)
        p2 = api.add_point("Earth is flat", "ctx", prov)
        print(f"[3/8] 2 points: {p1[:8]}…, {p2[:8]}…", file=sys.stderr)

        # Verify they exist in the graph
        node_count = g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
        assert node_count == 2, f"expected 2 Point nodes, got {node_count}"

        # ── Step 4: Add NAND operator ────────────────────────────────
        op_id = api.add_operator("NAND", [{"id": p1}, {"id": p2}], "ctx", prov)
        nand_count = g.query(
            "MATCH ()-[r:NAND]->() RETURN count(r)"
        ).result_set[0][0]
        assert nand_count == 2, f"expected 2 NAND edges, got {nand_count}"
        print(f"[4/8] NAND operator: {op_id[:8]}…, {nand_count} edges", file=sys.stderr)

        # ── Step 5: Run EP propagation ───────────────────────────────
        ep = TortoiseEP(proj, damping=0.5, max_iter=50, tol=1e-3)
        iters, converged = ep.run([op_id])
        print(f"[5/8] EP: {iters} iters, converged={converged}", file=sys.stderr)

        # ── Step 6: Verify confidences ───────────────────────────────
        rows = g.query(
            "MATCH (n:Point) "
            "WHERE n.is_operator = false "
            "RETURN n.id, coalesce(n.ep_alpha, 1.0), coalesce(n.ep_beta, 1.0)"
        ).result_set
        assert len(rows) == 2, f"expected 2 statement points, got {len(rows)}"

        for pid, a, b in rows:
            a, b = float(a), float(b)
            assert math.isfinite(a), f"{pid}: ep_alpha={a} not finite"
            assert math.isfinite(b), f"{pid}: ep_beta={b} not finite"
            assert a >= 0, f"{pid}: ep_alpha={a} < 0"
            assert b >= 0, f"{pid}: ep_beta={b} < 0"
            if a + b == 0:
                print(f"FAIL: {pid} has zero total evidence", file=sys.stderr)
                sys.exit(1)
            conf = a / (a + b)
            assert 0.0 <= conf <= 1.0, f"{pid}: confidence={conf} not in [0,1]"
        print(f"[6/8] Confidences valid: {len(rows)} points", file=sys.stderr)

        # ── Step 7: Query count ──────────────────────────────────────
        total = g.query("MATCH (n:Point) RETURN count(n)").result_set[0][0]
        assert total >= 2, f"expected >= 2 total points, got {total}"
        print(f"[7/8] Point count: {total} (>= 2 ✓)", file=sys.stderr)

        # ── Step 8: Delete graph ─────────────────────────────────────
        _delete_graph(db, graph_name, mode)
        print(f"[8/8] Graph '{graph_name}' deleted", file=sys.stderr)

        elapsed = time.time() - start
        print(f"PASS in {elapsed:.1f}s")
        return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as e:
        print(f"FAIL: {e}", file=sys.stderr)
        sys.exit(1)
