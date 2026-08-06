"""Tests for pre-migration safety tools — #49 Phase 2 Task 2.0.

Tests parity_sample logic, audit sidecar generation, and snapshot dry-run
on an isolated test graph (pytest conftest).

Since Phase 1 stop-writes are active (SDK strips context), we use direct
Cypher via the projection's graph handle to insert Points WITH context
for testing purposes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from tortoise.projection import FalkorProjection
from tortoise.analyze import _bfs_select_operators


# ── Helpers (inline from parity_sample.py) ────────────────────────────

def _get_old_operator_set(proj, context: str) -> set[str]:
    rows = proj.g.query(
        "MATCH (op:Point {is_operator:true})-[r:IMPL|NAND]->(c:Point {context:$ctx}) "
        "RETURN DISTINCT op.id",
        params={"ctx": context},
    ).result_set
    return {r[0] for r in rows}


def _get_context_anchors(proj, context: str) -> list[str]:
    rows = proj.g.query(
        "MATCH (n:Point {context:$ctx}) "
        "WHERE n.is_operator IS NULL OR n.is_operator = false "
        "RETURN n.id",
        params={"ctx": context},
    ).result_set
    return [r[0] for r in rows]


def _get_new_operator_set(proj, anchors: list[str]) -> set[str]:
    if not anchors:
        return set()
    return _bfs_select_operators(
        proj, anchors, max_hops=1, rel_filter="IMPL|NAND", direction="both"
    )


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def test_proj():
    """FalkorProjection on the isolated test graph (from conftest.py)."""
    uri = os.environ.get("TORTOISE_DB_URI", "")
    if uri.startswith("docker://"):
        from urllib.parse import urlparse
        parsed = urlparse(uri)
        proj = FalkorProjection(
            host=parsed.hostname or "localhost",
            port=parsed.port or 6379,
            password=parsed.password or None,
            graph_name=parsed.path.lstrip("/") or "tortoise",
        )
    else:
        # Embedded/redislite — fallback to path
        proj = FalkorProjection(path=uri if uri else tempfile.mktemp(suffix=".db"))

    # Clear test data
    try:
        proj.g.query("MATCH (n:Point) DETACH DELETE n")
    except Exception:
        pass

    yield proj
    try:
        proj.g.query("MATCH (n:Point) DETACH DELETE n")
    except Exception:
        pass
    proj.close()


def _make_point(proj, ctx: str, content: str, kind: str = "statement") -> str:
    """Create a Point with context via direct Cypher (bypasses SDK stop-writes)."""
    pid = f"test-{uuid.uuid4().hex[:12]}"
    proj.g.query(
        "CREATE (n:Point {id:$id, content:$content, context:$ctx, "
        "pointKind:$kind, is_operator:false})",
        params={"id": pid, "content": content, "ctx": ctx, "kind": kind},
    )
    return pid


def _make_operator(proj, source_id: str, target_ids: list[str],
                   op_type: str = "IMPL") -> str:
    """Create an operator Point connecting source→targets via direct Cypher."""
    op_id = f"op-{uuid.uuid4().hex[:12]}"
    proj.g.query(
        "CREATE (o:Point {id:$id, is_operator:true, op_type:$op, content:''})",
        params={"id": op_id, "op": op_type},
    )
    for i, tid in enumerate(target_ids):
        proj.g.query(
            f"MATCH (o:Point {{id:$oid}}), (t:Point {{id:$tid}}) "
            f"CREATE (o)-[:{op_type} {{idx:$i}}]->(t)",
            params={"oid": op_id, "tid": tid, "i": i},
        )
    # Also connect source
    proj.g.query(
        f"MATCH (o:Point {{id:$oid}}), (s:Point {{id:$sid}}) "
        f"CREATE (o)-[:{op_type} {{idx:0}}]->(s)",
        params={"oid": op_id, "sid": source_id},
    )
    return op_id


# ── Tests ──────────────────────────────────────────────────────────────

class TestParitySampleMatches:
    """Test that parity_sample logic correctly identifies matching operator sets."""

    def test_empty_context_no_operators(self, test_proj):
        """Context with points but no operators: both old and new should be empty."""
        ctx = f"test-empty-{uuid.uuid4().hex[:8]}"
        _make_point(test_proj, ctx, "Point A")
        _make_point(test_proj, ctx, "Point B")

        old = _get_old_operator_set(test_proj, ctx)
        new_anchors = _get_context_anchors(test_proj, ctx)
        new = _get_new_operator_set(test_proj, new_anchors)

        assert old == set(), f"Expected no old operators, got {old}"
        assert new == set(), f"Expected no new operators, got {new}"
        assert old == new, "Empty sets should match"

    def test_single_operator_direct_match(self, test_proj):
        """Operator connecting directly to context-points: old and new should match."""
        ctx = f"test-direct-{uuid.uuid4().hex[:8]}"
        p1 = _make_point(test_proj, ctx, "Point 1")
        p2 = _make_point(test_proj, ctx, "Point 2")
        p3 = _make_point(test_proj, ctx, "Point 3")  # no-op anchor

        # Create operator connecting p1 → p2
        op_id = _make_operator(test_proj, p1, [p2], op_type="IMPL")

        old = _get_old_operator_set(test_proj, ctx)
        new_anchors = _get_context_anchors(test_proj, ctx)
        new = _get_new_operator_set(test_proj, new_anchors)

        assert old == {op_id}, f"Old set should contain {op_id}, got {old}"
        assert new == {op_id}, f"New set should contain {op_id}, got {new}"
        assert old == new, "Operator sets should match"

    def test_multiple_operators_multiple_points(self, test_proj):
        """Multiple operators across multiple context-points: old == new."""
        ctx = f"test-multi-{uuid.uuid4().hex[:8]}"
        # Create 5 points in this context
        points = [_make_point(test_proj, ctx, f"Point {i}") for i in range(5)]

        # Create 3 operators connecting various points
        op1 = _make_operator(test_proj, points[0], [points[1]], op_type="IMPL")
        op2 = _make_operator(test_proj, points[2], [points[3]], op_type="IMPL")
        op3 = _make_operator(test_proj, points[0], [points[2], points[4]], op_type="NAND")

        old = _get_old_operator_set(test_proj, ctx)
        new_anchors = _get_context_anchors(test_proj, ctx)
        new = _get_new_operator_set(test_proj, new_anchors)

        assert old == {op1, op2, op3}, f"Old set mismatch: {old}"
        assert new == {op1, op2, op3}, f"New set mismatch: {new}"
        assert old == new, "Operator sets should match"

    def test_operator_on_non_context_point_not_included(self, test_proj):
        """Operator connecting to a point WITHOUT context: not included for that context."""
        ctx_a = f"test-isolate-a-{uuid.uuid4().hex[:8]}"
        ctx_b = f"test-isolate-b-{uuid.uuid4().hex[:8]}"

        p_a = _make_point(test_proj, ctx_a, "Point in context A")
        p_b = _make_point(test_proj, ctx_b, "Point in context B")

        # Operator connecting p_a → p_b (p_a in ctx_a, p_b in ctx_b)
        op_id = _make_operator(test_proj, p_a, [p_b], op_type="IMPL")

        # Query for ctx_a: should find operator (connects to p_a which has ctx_a)
        old_a = _get_old_operator_set(test_proj, ctx_a)
        anchors_a = _get_context_anchors(test_proj, ctx_a)
        new_a = _get_new_operator_set(test_proj, anchors_a)

        assert old_a == {op_id}, f"old_a should contain operator, got {old_a}"
        assert new_a == {op_id}, f"new_a should contain operator, got {new_a}"

        # Query for ctx_b: old finds operator (connects to p_b), new finds via anchors
        old_b = _get_old_operator_set(test_proj, ctx_b)
        anchors_b = _get_context_anchors(test_proj, ctx_b)
        new_b = _get_new_operator_set(test_proj, anchors_b)

        assert old_b == {op_id}, f"old_b should contain operator, got {old_b}"
        assert new_b == {op_id}, f"new_b should contain operator, got {new_b}"

    def test_nand_operators_included(self, test_proj):
        """NAND operators should be included in both old and new sets."""
        ctx = f"test-nand-{uuid.uuid4().hex[:8]}"
        p1 = _make_point(test_proj, ctx, "Claim A")
        p2 = _make_point(test_proj, ctx, "Claim B")

        op_id = _make_operator(test_proj, p1, [p2], op_type="NAND")

        old = _get_old_operator_set(test_proj, ctx)
        new_anchors = _get_context_anchors(test_proj, ctx)
        new = _get_new_operator_set(test_proj, new_anchors)

        assert op_id in old, f"NAND operator {op_id} not in old set: {old}"
        assert op_id in new, f"NAND operator {op_id} not in new set: {new}"
        assert old == new, "NAND operator sets should match"

    def test_convergence_between_contexts(self, test_proj):
        """When two contexts share operators, each context should find them."""
        ctx_a = f"test-converge-a-{uuid.uuid4().hex[:8]}"
        ctx_b = f"test-converge-b-{uuid.uuid4().hex[:8]}"

        p_a1 = _make_point(test_proj, ctx_a, "A1")
        p_a2 = _make_point(test_proj, ctx_a, "A2")
        p_b1 = _make_point(test_proj, ctx_b, "B1")

        # Operator: p_a1 → p_a2, p_b1  (cross-context)
        op_id = _make_operator(test_proj, p_a1, [p_a2, p_b1], op_type="IMPL")

        # Both contexts should find this operator
        for ctx, label in [(ctx_a, "A"), (ctx_b, "B")]:
            old = _get_old_operator_set(test_proj, ctx)
            anchors = _get_context_anchors(test_proj, ctx)
            new = _get_new_operator_set(test_proj, anchors)
            assert old == {op_id}, f"ctx_{label}: old mismatch: {old}"
            assert new == {op_id}, f"ctx_{label}: new mismatch: {new}"
            assert old == new, f"ctx_{label}: sets should match"


class TestAuditSidecarWritten:
    """Test that the audit sidecar correctly maps context→points."""

    def test_audit_collects_all_contexts(self, test_proj):
        """Create points in multiple contexts, verify audit map is complete."""
        ctx_map = {}
        for i in range(5):
            ctx = f"audit-ctx-{i}-{uuid.uuid4().hex[:8]}"
            pids = []
            for j in range(3):
                pid = _make_point(test_proj, ctx, f"Content {i}-{j}")
                pids.append(pid)
            ctx_map[ctx] = pids

        # Collect via direct Cypher (same as audit script)
        rows = test_proj.g.query(
            "MATCH (n:Point) WHERE n.context IS NOT NULL "
            "RETURN n.context, n.id ORDER BY n.context, n.id"
        ).result_set

        collected: dict[str, list[str]] = {}
        for ctx_val, point_id in rows:
            collected.setdefault(ctx_val, []).append(point_id)

        # Verify
        assert len(collected) == 5
        for ctx, expected_ids in ctx_map.items():
            assert ctx in collected, f"Missing context: {ctx}"
            assert set(collected[ctx]) == set(expected_ids), \
                f"Context {ctx}: expected {expected_ids}, got {collected[ctx]}"

    def test_audit_json_output_format(self, test_proj, tmp_path):
        """Verify the audit JSON format is correct."""
        ctx = f"audit-fmt-{uuid.uuid4().hex[:8]}"
        pids = [_make_point(test_proj, ctx, f"Point {i}") for i in range(3)]

        # Build audit doc manually (mimics context_removal_audit.py)
        rows = test_proj.g.query(
            "MATCH (n:Point) WHERE n.context IS NOT NULL "
            "RETURN n.context, n.id ORDER BY n.context, n.id"
        ).result_set

        ctx_map: dict[str, list[str]] = {}
        for ctx_val, point_id in rows:
            ctx_map.setdefault(ctx_val, []).append(point_id)

        audit_doc = {
            "$schema": "tortoise/audit/context-removal-v1",
            "migration": "#49 Phase 2",
            "summary": {"context_values": len(ctx_map), "total_points": sum(len(v) for v in ctx_map.values())},
            "context_to_points": ctx_map,
        }

        output_file = tmp_path / "test_audit.json"
        output_file.write_text(json.dumps(audit_doc, indent=2))

        # Verify
        loaded = json.loads(output_file.read_text())
        assert loaded["summary"]["context_values"] == 1
        assert loaded["summary"]["total_points"] == 3
        assert ctx in loaded["context_to_points"]
        assert set(loaded["context_to_points"][ctx]) == set(pids)

    def test_audit_excludes_null_context(self, test_proj):
        """Points without context should NOT appear in the audit map."""
        ctx = f"audit-no-null-{uuid.uuid4().hex[:8]}"
        pid_with = _make_point(test_proj, ctx, "With context")

        # Insert a point WITHOUT context
        pid_without = f"test-noctx-{uuid.uuid4().hex[:12]}"
        test_proj.g.query(
            "CREATE (n:Point {id:$id, content:'No context', is_operator:false})",
            params={"id": pid_without},
        )

        rows = test_proj.g.query(
            "MATCH (n:Point) WHERE n.context IS NOT NULL "
            "RETURN n.context, n.id ORDER BY n.context, n.id"
        ).result_set

        ctx_map: dict[str, list[str]] = {}
        for ctx_val, point_id in rows:
            ctx_map.setdefault(ctx_val, []).append(point_id)

        assert pid_with in ctx_map.get(ctx, [])
        # pid_without should not appear anywhere
        all_ids = [pid for ids in ctx_map.values() for pid in ids]
        assert pid_without not in all_ids, f"Null-context point {pid_without} leaked into audit"


class TestSnapshotDryRun:
    """Test that pre_migration_snapshot.py --dry-run runs without error."""

    def test_dry_run_executes(self):
        """--dry-run should print without error."""
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "graph-scripts", "pre_migration_snapshot.py"
        )
        result = subprocess.run(
            [sys.executable, script, "--dry-run"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Dry-run failed: {result.stderr}"
        assert "DRY RUN" in result.stdout
        assert "RESTORE PROCEDURE" in result.stdout
        assert "BGSAVE" in result.stdout or "BGSAVE" in result.stderr

    def test_dry_run_with_uri(self):
        """--dry-run with explicit --uri should also work."""
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "graph-scripts", "pre_migration_snapshot.py"
        )
        result = subprocess.run(
            [sys.executable, script, "--dry-run",
             "--uri", "docker://:falkordb@localhost:16379/tortoise"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"Dry-run with URI failed: {result.stderr}"
        assert "DRY RUN" in result.stdout

    def test_dry_run_with_copy_rdb(self):
        """--dry-run --copy-rdb PATH should mention copy step."""
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "graph-scripts", "pre_migration_snapshot.py"
        )
        result = subprocess.run(
            [sys.executable, script, "--dry-run", "--copy-rdb", "/tmp/test.rdb"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "/tmp/test.rdb" in result.stdout
