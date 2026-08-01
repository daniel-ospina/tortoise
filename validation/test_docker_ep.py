"""End-to-end: EP on Docker FalkorDB personal instance (port 16379)."""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tortoise.projection import FalkorProjection
from tortoise.ep import TortoiseEP


def test_docker_connection():
    """Can we connect to the Docker FalkorDB instance?"""
    proj = FalkorProjection(host="localhost", port=16379, graph_name="endometriosis_melasma")
    try:
        # Verify connection works
        rows = proj.g.query("MATCH (n) RETURN count(n)").result_set
        count = rows[0][0] if rows else 0
        assert count > 0, f"Graph should have nodes, got {count}"
        print(f"  Connected to endometriosis_melasma: {count} nodes")
    finally:
        proj.close()


def test_docker_ep_run():
    """Run TortoiseEP on the Docker personal graph."""
    proj = FalkorProjection(host="localhost", port=16379, graph_name="endometriosis_melasma")
    try:
        # Get all operator IDs
        op_rows = proj.g.query(
            "MATCH (o:Point) WHERE o.is_operator = true RETURN o.id"
        ).result_set
        op_ids = [r[0] for r in op_rows]
        print(f"  Found {len(op_ids)} operators")

        if not op_ids:
            print("  No operators — skipping EP run")
            return

        ep = TortoiseEP(proj, damping=0.5, max_iter=100)
        n_iter, converged = ep.run(op_ids, max_hops=3)
        print(f"  EP: {'converged' if converged else 'max iter'} in {n_iter} iters")

        # Verify some posteriors
        claim_rows = proj.g.query(
            "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN n.id LIMIT 5"
        ).result_set
        for (cid,) in claim_rows:
            conf = ep.compute_confidence(cid)
            print(f"    {cid}: mean={conf['mean']:.4f} var={conf['variance']:.4f}")
            assert 0.01 < conf['mean'] < 0.99, f"Posterior out of bounds for {cid}"

    finally:
        proj.close()


def test_docker_ep_vs_bfs():
    """Compare EP posteriors against existing BFS confidence values."""
    proj = FalkorProjection(host="localhost", port=16379, graph_name="endometriosis_melasma")
    try:
        op_rows = proj.g.query(
            "MATCH (o:Point) WHERE o.is_operator = true RETURN o.id"
        ).result_set
        op_ids = [r[0] for r in op_rows]

        if not op_ids:
            return

        # Read existing BFS confidence values
        bfs_rows = proj.g.query(
            "MATCH (n:Point) WHERE (n.is_operator IS NULL OR n.is_operator = false) "
            "RETURN n.id, coalesce(n.confidence, 0.5) LIMIT 10"
        ).result_set
        bfs_confs = {r[0]: float(r[1]) for r in bfs_rows}

        # Run EP
        ep = TortoiseEP(proj, damping=0.5, max_iter=100)
        ep.run(op_ids, max_hops=3)

        # Compare
        print(f"  Comparing EP vs BFS confidence:")
        for cid, bfs_conf in bfs_confs.items():
            ep_conf = ep.compute_confidence(cid)
            print(f"    {cid}: BFS={bfs_conf:.4f} → EP={ep_conf['mean']:.4f} "
                  f"(Δ={abs(ep_conf['mean']-bfs_conf):.4f})")

    finally:
        proj.close()


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
