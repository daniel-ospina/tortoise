"""Tests for tortoise index github CLI command (#713).

Verifies that _cmd_index_github works with the modern extractor API
(extract_from_document + MockModel) instead of the removed ExtractionPipeline.
"""
from __future__ import annotations

import os  # noqa: F401
import sys
from pathlib import Path

import pytest

# Ensure the repo root is in sys.path (standard for Tortoise tests)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tortoise.__main__ import _cmd_index_github, _markdown_files


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


def test_index_github_local_extracts_points(markdown_repo, embedded_db_path, monkeypatch,
                                             tmp_path):
    """tortoise index github on a local dir with .md files extracts Points.

    Uses embedded FalkorDBLite (no Docker needed). Verifies the modern
    extract_from_document API is used (no ModuleNotFoundError for
    tortoise.extraction_pipeline).
    """
    # Isolate the idempotency hash file (~/.tortoise) and the EventLog
    # (gettempdir) under tmp_path — never leak /tmp dirs between runs.
    _isolated_index_env(monkeypatch, tmp_path)

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
    _isolated_index_env(monkeypatch, tmp_path)

    empty_dir = tmp_path / "empty-repo"
    empty_dir.mkdir()

    args = Args(url=str(empty_dir), db=embedded_db_path)
    exit_code = _cmd_index_github(args)
    assert exit_code == 0


def test_index_github_idempotent(markdown_repo, embedded_db_path, monkeypatch,
                                 tmp_path):
    """Re-running index github on the same content skips already-indexed files."""
    _isolated_index_env(monkeypatch, tmp_path)

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


# ── #2201: unreadable-file + non-content-dir robustness ──────────────

# Deterministic extractable body (## Decision/Observation/Goal sections — the
# MockModel mints points from it; same shape as the markdown_repo fixture).
_INDEXABLE_DOC = """# Architecture Decisions

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
"""

# Second extractable body with DIFFERENT content — same section shape, but a
# distinct idempotency hash so a repo with both files indexes them both (two
# identical bodies would dedup the second as "already indexed" in-run).
_INDEXABLE_DOC2 = """# Session Notes

## Decision: Use redislite for embedded mode

We decided to support redislite-backed embedded runs because they need no
Docker daemon and keep single-writer eval loops self-contained. This was
chosen over always-requiring FalkorDB server.

## Observation: Writes are single-writer bounded

We found that embedded graphs stay consistent when one writer owns the
journal and everyone else reads through the SDK projection layer.

## Goal: Keep embedded eval first-class

Our target is to keep the no-Docker path exercised in CI so self-hosted users
always have a runnable fallback.
"""


def _isolated_index_env(monkeypatch, tmp_path):
    """Isolate the indexer's two system-temp side effects per test.

    (1) Path.home — the idempotency hash file lives at ~/.tortoise; (2)
    tempfile.gettempdir — the EventLog lands at /tmp/tortoise-index-<repo>.
    Both move under pytest's auto-cleaned tmp_path so index tests neither leak
    /tmp dirs nor share the same EventLog when they reuse a repo name (#2201
    review: cross-test isolation).
    """
    fake_home = tmp_path / "fake-home"
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path / "logs"))


def _dangling_symlink_repo(tmp_path):
    """Minimal repo: one indexable doc + a dangling symlink named like the
    #2201 repro (data/ONTOLOGY.md -> a missing file in another dev's home)."""
    repo = tmp_path / "dangling-repo"
    repo.mkdir()
    (repo / "architecture.md").write_text(_INDEXABLE_DOC)
    (repo / "data").mkdir()
    missing_target = repo / "data" / "ONTOLOGY_v2.5.md"
    # Never create the target — the symlink dangles exactly like the tracked
    # data/ONTOLOGY.md symlink that crashed onboarding at file 13 (#2201).
    (repo / "data" / "ONTOLOGY.md").symlink_to(missing_target)
    return repo


def test_index_github_skips_dangling_symlink(embedded_db_path, monkeypatch,
                                             capsys, tmp_path):
    """#2201 regression: a dangling symlink must be skipped with a warning —
    never abort the whole index run with FileNotFoundError — and indexing
    resumes with the files that sort after it (in the real repro the crash
    hit at file 13 of 652; the readable files after it must still index)."""
    _isolated_index_env(monkeypatch, tmp_path)

    repo = _dangling_symlink_repo(tmp_path)
    # Sorts AFTER data/ONTOLOGY.md (data < docs) — so it is processed only if
    # the loop keeps going past the unreadable symlink.
    (repo / "docs").mkdir()
    (repo / "docs" / "tail.md").write_text(_INDEXABLE_DOC2)

    args = Args(url=str(repo), db=embedded_db_path)
    exit_code = _cmd_index_github(args)

    assert exit_code == 0, (
        f"index must complete on a repo with a dangling symlink, got {exit_code}")
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "unreadable" in captured.out, "skip must be reported as unreadable"
    assert "ONTOLOGY.md" in captured.out
    # The doc after the symlink is still indexed — skip-and-stop regressions
    # (break/abort on the first unreadable) must fail this assertion.
    assert "Done: 2 indexed, 0 skipped, 1 unreadable, 0 errors" in captured.out, \
        captured.out
    from tortoise.projection import FalkorProjection
    proj = FalkorProjection(path=embedded_db_path)
    try:
        result = proj.g.query("MATCH (p:Point) RETURN count(p) AS cnt").result_set
        assert result[0][0] >= 2, (
            f"expected >=2 Points (both indexable docs), got {result[0][0]}")
    finally:
        proj.close()


def test_index_github_excludes_non_content_dirs(embedded_db_path, monkeypatch,
                                                capsys, tmp_path):
    """#2201: the 'Found N markdown files' count (and the walk) exclude .venv,
    venv, .git, node_modules and __pycache__ — files there are never repo
    content. Only architecture.md + the dangling data/ONTOLOGY.md (a content-
    path file, discoverable but unreadable → skipped at read) remain."""
    _isolated_index_env(monkeypatch, tmp_path)

    repo = _dangling_symlink_repo(tmp_path)
    for junk_dir in (".venv", "venv", ".git", "node_modules", "__pycache__"):
        d = repo / junk_dir
        d.mkdir(exist_ok=True)
        (d / "junk.md").write_text("# junk\n\n## Decision: ignore\n\nbody\n")

    args = Args(url=str(repo), db=embedded_db_path)
    exit_code = _cmd_index_github(args)

    assert exit_code == 0
    out = capsys.readouterr().out
    # 2 discovered (architecture.md + the dangling symlink), NOT 7 — the five
    # junk trees must not inflate the announced count.
    assert "Found 2 markdown files. Indexing…" in out, out
    assert "junk.md" not in out, "non-content files must not be walked"
    assert "Done: 1 indexed, 0 skipped, 1 unreadable, 0 errors" in out, out


def test_markdown_files_shared_discovery_excludes_non_content(tmp_path):
    """#2201: the shared discovery helper used by init/onboard counts AND the
    indexer walk drops non-content dirs by exact component name — junk dirs
    (.venv/venv/.git/node_modules/__pycache__) are excluded while near-miss
    content names (docs/venv-setup/, node_modules_notes.md) are KEPT (no
    substring matching) and a dangling *.md symlink stays discoverable,
    matching the walk."""
    repo = _dangling_symlink_repo(tmp_path)
    for junk_dir in (".venv", "venv", ".git", "node_modules", "__pycache__"):
        d = repo / junk_dir
        d.mkdir(exist_ok=True)
        (d / "junk.md").write_text("junk")
    # Near-miss names embedding an excluded token — must be KEPT (the exact-
    # component match is what separates real content from junk).
    (repo / "docs" / "venv-setup").mkdir(parents=True)
    (repo / "docs" / "venv-setup" / "setup.md").write_text("# setup")
    (repo / "docs" / "nested").mkdir(parents=True)
    (repo / "docs" / "nested" / "real.md").write_text("# real")
    (repo / "node_modules_notes.md").write_text("# notes")

    found = _markdown_files(repo)
    rels = sorted(p.relative_to(repo).as_posix() for p in found)

    assert rels == [
        "architecture.md",
        "data/ONTOLOGY.md",
        "docs/nested/real.md",
        "docs/venv-setup/setup.md",
        "node_modules_notes.md",
    ], rels
