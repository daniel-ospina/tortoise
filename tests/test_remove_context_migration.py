"""Tests for #49 Phase 2: remove_context_migration.py

Uses conftest-isolated FalkorDB graph (tortoise_test_*) and tests the
migration script's gates, dry-run, removal, and AST preflight.

Run::

    python -m pytest tests/test_remove_context_migration.py -v
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest

# Path helpers
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "graph-scripts" / "remove_context_migration.py"
sys.path.insert(0, str(_REPO_ROOT))

# Import functions under test via importlib (graph-scripts has a dash in name)
import importlib.util as _importlib_util

_spec = _importlib_util.spec_from_file_location(
    "remove_context_migration", str(_SCRIPT)
)
_migration = _importlib_util.module_from_spec(_spec)
_spec.loader.exec_module(_migration)

_ast_preflight = _migration._ast_preflight
_dry_run = _migration._dry_run
_run_removal = _migration._run_removal
_verify_zero = _migration._verify_zero
_check_phase2_flag = _migration._check_phase2_flag
from tortoise.sdk import TortoiseSDK


# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def sdk():
    """SDK against the conftest-isolated test graph."""
    ns = f"test_rmctx_{uuid.uuid4().hex[:8]}"
    sdk = TortoiseSDK(namespace=ns)
    yield sdk
    sdk.close()


@pytest.fixture
def graph_with_context(sdk):
    """Create a few points WITH context on the isolated test graph."""
    proj = sdk._get_proj()
    # Create points with a context property set
    ids = []
    import uuid as _uuid
    for i in range(5):
        pid = f"test-ctx-node-{i}-{_uuid.uuid4().hex[:6]}"
        proj.g.query(
            "CREATE (n:Point {id: $id}) SET n.content = $content, n.context = $ctx",
            params={"id": pid, "content": f"Test point {i}", "ctx": f"test-domain-{i % 2}"},
        )
        ids.append(pid)
    yield proj, ids
    # Cleanup
    for pid in ids:
        try:
            proj.g.query("MATCH (n:Point {id: $id}) DETACH DELETE n", params={"id": pid})
        except Exception:
            pass


# ── Tests ─────────────────────────────────────────────────────────────────


class TestGate0Phase2Flag:
    """Gate 0: TORTOISE_PHASE2=1 env guard."""

    def test_refuses_without_phase2_flag(self):
        """Script exits 1 when TORTOISE_PHASE2 is not set."""
        env = os.environ.copy()
        env.pop("TORTOISE_PHASE2", None)
        result = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            capture_output=True, text=True, env=env,
        )
        assert result.returncode == 1, f"Expected exit 1, got {result.returncode}: {result.stderr}"
        assert "TORTOISE_PHASE2" in result.stderr, (
            f"Expected 'TORTOISE_PHASE2' in stderr, got: {result.stderr}"
        )

    def test_accepts_with_phase2_flag(self, monkeypatch, sdk):
        """When TORTOISE_PHASE2=1 and gates pass, script should not exit on env guard."""
        monkeypatch.setenv("TORTOISE_PHASE2", "1")
        # Just verify the env guard function doesn't raise — it calls sys.exit(1) on failure
        _check_phase2_flag()  # Should not call sys.exit


class TestDryRun:
    """Dry-run: count + sample, no removal."""

    def test_dry_run_no_removal(self, graph_with_context):
        """--dry-run counts nodes but does NOT remove context."""
        proj, ids = graph_with_context

        # Verify context exists
        before = proj.g.query(
            "MATCH (n:Point) WHERE n.context IS NOT NULL RETURN count(n)"
        ).result_set[0][0]
        assert before >= 5, f"Expected >=5 context-bearing nodes, got {before}"

        # Run dry-run
        result = _dry_run(proj)
        assert result["context_bearing_nodes"] >= 5

        # Verify context STILL exists
        after = proj.g.query(
            "MATCH (n:Point) WHERE n.context IS NOT NULL RETURN count(n)"
        ).result_set[0][0]
        assert after == before, f"Dry-run should not remove context: {before} → {after}"


class TestRemoval:
    """Live removal: REMOVE n.context from all Point nodes."""

    def test_removes_context(self, sdk):
        """TORTOISE_PHASE2=1 + clean preflight → context removed, remaining=0."""
        proj = sdk._get_proj()
        import uuid as _uuid

        # Create nodes with context
        ids = []
        for i in range(5):
            pid = f"test-rmctx-live-{i}-{_uuid.uuid4().hex[:6]}"
            proj.g.query(
                "CREATE (n:Point {id: $id}) SET n.content = $content, n.context = $ctx",
                params={"id": pid, "content": f"Test {i}", "ctx": f"live-domain-{i % 2}"},
            )
            ids.append(pid)

        try:
            # Verify context exists
            before = proj.g.query(
                "MATCH (n:Point) WHERE n.context IS NOT NULL RETURN count(n)"
            ).result_set[0][0]
            assert before >= 5

            # Run removal
            summary = _run_removal(proj)
            assert summary["removed"] == before
            assert summary["remaining"] == 0

            # Post-migration verification
            assert _verify_zero(proj) is True
        finally:
            for pid in ids:
                try:
                    proj.g.query("MATCH (n:Point {id: $id}) DETACH DELETE n", params={"id": pid})
                except Exception:
                    pass

    def test_idempotent(self, sdk):
        """Second run of REMOVE modifies 0 nodes."""
        proj = sdk._get_proj()
        import uuid as _uuid

        # Create nodes with context
        ids = []
        for i in range(3):
            pid = f"test-rmctx-idem-{i}-{_uuid.uuid4().hex[:6]}"
            proj.g.query(
                "CREATE (n:Point {id: $id}) SET n.content = $content, n.context = $ctx",
                params={"id": pid, "content": f"Idem {i}", "ctx": f"idem-domain"},
            )
            ids.append(pid)

        try:
            # First run
            summary1 = _run_removal(proj)
            assert summary1["removed"] >= 3, f"First run removed {summary1['removed']}, expected >= 3"
            assert summary1["remaining"] == 0

            # Second run — idempotent
            summary2 = _run_removal(proj)
            assert summary2["removed"] == 0, (
                f"Second run should remove 0 (idempotent), got {summary2['removed']}"
            )
            assert summary2["remaining"] == 0
        finally:
            for pid in ids:
                try:
                    proj.g.query("MATCH (n:Point {id: $id}) DETACH DELETE n", params={"id": pid})
                except Exception:
                    pass


class TestASTPreflight:
    """AST preflight detects context READ paths in tortoise source files."""

    def test_ast_preflight_detects_violation_param(self):
        """A function parameter named 'context' is flagged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = Path(tmpdir) / "bad_module.py"
            bad_file.write_text("def foo(context=None):\n    pass\n")
            violations = _ast_preflight(scan_dir=Path(tmpdir))
            assert len(violations) > 0, "Expected violations for 'context' param"
            assert any("foo" in v and "context" in v for v in violations), (
                f"Expected violation about 'foo' and 'context', got: {violations}"
            )

    def test_ast_preflight_detects_violation_cypher(self):
        """A string literal containing 'n.context' is flagged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = Path(tmpdir) / "query_module.py"
            bad_file.write_text(
                'QUERY = "MATCH (n:Point) WHERE n.context = $ctx RETURN n"\n'
            )
            violations = _ast_preflight(scan_dir=Path(tmpdir))
            assert len(violations) > 0, (
                f"Expected violations for n.context in string, got: {violations}"
            )
            assert any("n.context" in v for v in violations)

    def test_ast_preflight_detects_violation_dataclass_field(self):
        """A dataclass field named 'context' is flagged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            bad_file = Path(tmpdir) / "model_module.py"
            bad_file.write_text("""
from dataclasses import dataclass
from typing import Optional

@dataclass
class PointModel:
    id: str
    context: Optional[str] = None
""")
            violations = _ast_preflight(scan_dir=Path(tmpdir))
            assert len(violations) > 0, (
                f"Expected violations for dataclass context field, got: {violations}"
            )
            assert any("PointModel" in v and "context" in v for v in violations)

    def test_ast_preflight_exempts_session_context(self):
        """session_context and tortoise_session_context params are exempt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            good_file = Path(tmpdir) / "good_module.py"
            good_file.write_text(
                "def handle(session_context=None, tortoise_session_context=None):\n    pass\n"
            )
            violations = _ast_preflight(scan_dir=Path(tmpdir))
            assert len(violations) == 0, (
                f"session_context should be exempt, got: {violations}"
            )

    def test_ast_preflight_exempts_docstring(self):
        """n.context in a docstring is exempt (not a Cypher reference)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            good_file = Path(tmpdir) / "doc_module.py"
            good_file.write_text(
                '"""Module docs about n.context migration."""\n'
                "def foo():\n    pass\n"
            )
            violations = _ast_preflight(scan_dir=Path(tmpdir))
            # The docstring is at module level — our heuristic catches it
            # We expect 0 violations because '"""' at start of line is a docstring
            assert len(violations) == 0, (
                f"Docstring n.context should be exempt, got: {violations}"
            )

    def test_ast_preflight_clean_on_no_violations(self):
        """Empty/safe files produce no violations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            good_file = Path(tmpdir) / "clean_module.py"
            good_file.write_text(
                "def hello(name: str) -> str:\n    return f'Hello {name}'\n"
            )
            violations = _ast_preflight(scan_dir=Path(tmpdir))
            assert violations == [], f"Expected no violations, got: {violations}"


class TestGuard99:
    """Verify #99 guard does NOT block REMOVE (and still blocks DETACH DELETE)."""

    def test_remove_passes_bulk_wipe_guard(self):
        """REMOVE n.context passes through the guarded graph (no block).

        The guard lives in _GuardedGraph.query() (projection/__init__.py) and
        only blocks bulk DETACH DELETE. REMOVE n.context is a property removal
        — it must execute. Verify via a real SDK on the conftest graph: set a
        context property directly, then REMOVE it through the guarded path.
        """
        import os as _os
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()
        try:
            proj = sdk._get_proj()
            # Create a point, then set context directly (bypassing create_point
            # which no longer accepts it) — simulate a legacy node.
            p = sdk.create_point("statement", "guard-remove-test")
            proj.g.query(
                "MATCH (n:Point {id:$id}) SET n.context='legacy'",
                params={"id": p["id"]},
            )
            # REMOVE via the guarded graph — must NOT raise
            proj.g.query(
                "MATCH (n:Point {id:$id}) REMOVE n.context",
                params={"id": p["id"]},
            )
            node = sdk.get_point(p["id"])
            assert "context" not in node or node.get("context") is None, \
                "REMOVE did not remove the context property"
        finally:
            try:
                sdk.close()
            except Exception:
                pass

    def test_delete_is_blocked(self):
        """Bulk DETACH DELETE still triggers the guard (sanity check)."""
        from tortoise.sdk import TortoiseSDK
        sdk = TortoiseSDK()
        try:
            proj = sdk._get_proj()
            # Bulk DETACH DELETE on a non-test graph must raise RuntimeError
            import pytest
            with pytest.raises(RuntimeError):
                # Force a non-test graph name to trigger the guard
                from tortoise.projection import FalkorProjection
                from_uri = FalkorProjection.from_uri(
                    "docker://:@localhost:16379/tortoise")
                try:
                    from_uri.g.query("MATCH (n) DETACH DELETE n")
                finally:
                    from_uri.close()
        finally:
            try:
                sdk.close()
            except Exception:
                pass
