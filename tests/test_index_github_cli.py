"""Tests for tortoise index github CLI command (#713).

Verifies that _cmd_index_github works with the modern extractor API
(extract_from_document + MockModel) instead of the removed ExtractionPipeline.
"""
from __future__ import annotations

import os  # noqa: F401
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure the repo root is in sys.path (standard for Tortoise tests)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.__main__ import _cmd_index_github


class Args:
    """Minimal args object matching what _cmd_index_github expects."""

    def __init__(self, url: str, db: str, branch: str = "main", background: bool = False):
        self.url = url
        self.db = db
        self.branch = branch
        self.background = background


@pytest.fixture
def embedded_db_path(tmp_path):
    """Return a path for an embedded FalkorDBLite database."""
    db_path = tmp_path / "test-index-github.db"
    return str(db_path)


@pytest.fixture
def markdown_repo(tmp_path):
    """Create a minimal markdown repo with a few .md files.

    Returns the path to the repo directory.
    """
    repo = tmp_path / "test-repo"
    repo.mkdir()

    # File with ## headers (document-style) — should extract points
    doc1 = repo / "architecture.md"
    doc1.write_text("""# Architecture Decisions

## Decision: Use FalkorDB for graph storage

We decided to use FalkorDB as the primary graph database because it supports
Cypher queries and has a Redis-compatible protocol. This was chosen over Neo4j
for its simpler deployment model.

## Observation: EP propagation converges quickly

We found that belief propagation typically converges within 3-5 iterations
for graphs under 10,000 nodes. The damping factor of 0.85 works well.

## Goal: Support 100K nodes in 2026

Our target is to handle 100,000 nodes per graph by Q4 2026 with sub-second
query latency for the suggest_entry_points path.
""")

    # File without ## headers — should be skipped (no sections, no points)
    simple = repo / "README.md"
    simple.write_text("# Test Repo\n\nThis is a test repository for index github.\n")

    return str(repo)


def test_index_github_local_extracts_points(markdown_repo, embedded_db_path, monkeypatch):
    """tortoise index github on a local dir with .md files extracts Points.

    Uses embedded FalkorDBLite (no Docker needed). Verifies the modern
    extract_from_document API is used (no ModuleNotFoundError for
    tortoise.extraction_pipeline).
    """
    # Prevent the idempotency hash file from leaking across tests
    fake_home = tempfile.mkdtemp(prefix="tortoise-test-home-")
    monkeypatch.setattr(Path, "home", lambda: Path(fake_home))

    # --db is a file path → embedded directly; no Docker connection attempted

    args = Args(url=markdown_repo, db=embedded_db_path)
    exit_code = _cmd_index_github(args)

    assert exit_code == 0, f"_cmd_index_github returned {exit_code}"

    # Verify points were written to the embedded DB
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection(path=embedded_db_path)
    try:
        result = proj.g.query("MATCH (p:Point) RETURN count(p) AS cnt").result_set
        point_count = result[0][0] if result else 0
        assert point_count > 0, f"Expected >0 Points in graph, got {point_count}"
    finally:
        proj.close()


def test_index_github_no_markdown_graceful(embedded_db_path, tmp_path, monkeypatch):
    """tortoise index github on a dir with no .md files exits cleanly."""
    fake_home = tempfile.mkdtemp(prefix="tortoise-test-home-")
    monkeypatch.setattr(Path, "home", lambda: Path(fake_home))

    empty_dir = tmp_path / "empty-repo"
    empty_dir.mkdir()

    args = Args(url=str(empty_dir), db=embedded_db_path)
    exit_code = _cmd_index_github(args)
    assert exit_code == 0


def test_index_github_idempotent(markdown_repo, embedded_db_path, monkeypatch):
    """Re-running index github on the same content skips already-indexed files."""
    fake_home = tempfile.mkdtemp(prefix="tortoise-test-home-")
    monkeypatch.setattr(Path, "home", lambda: Path(fake_home))

    args = Args(url=markdown_repo, db=embedded_db_path)

    # First run — should index
    exit_code1 = _cmd_index_github(args)
    assert exit_code1 == 0

    # Second run — should skip all (idempotent)
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection(path=embedded_db_path)
    try:
        result = proj.g.query("MATCH (p:Point) RETURN count(p) AS cnt").result_set
        first_count = result[0][0] if result else 0
    finally:
        proj.close()

    exit_code2 = _cmd_index_github(args)
    assert exit_code2 == 0

    # Point count should not have doubled — idempotency works
    proj2 = FalkorProjection(path=embedded_db_path)
    try:
        result2 = proj2.g.query("MATCH (p:Point) RETURN count(p) AS cnt").result_set
        second_count = result2[0][0] if result else 0
        assert second_count == first_count, (
            f"Idempotency failed: first run created {first_count} points, "
            f"second run created {second_count} (should be equal)"
        )
    finally:
        proj2.close()
