"""Hermetic tests for .github/scripts/check-migration-append-only (#1095).

Builds fixture git repos and drives the SHARED script via subprocess so the
test exercises the shipped logic (not a Python copy). Two modes:
- prefix: duplicate prefixes + non-conforming filenames
- diff: append-only (M/R/D rejected, A allowed) against a base SHA
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".github" / "scripts" / "check-migration-append-only"
_FIXTURE_TMP = None


def _tmp() -> Path:
    global _FIXTURE_TMP
    if _FIXTURE_TMP is None:
        _FIXTURE_TMP = Path(tempfile.mkdtemp(prefix="migration-append-only-"))
    return _FIXTURE_TMP


FIXTURES = _tmp()

MIG = 'supabase/migrations'


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _make_repo(base_migrations: list[str]) -> Path:
    """Create a fixture repo with an initial commit containing base migrations."""
    d = FIXTURES / "repo"
    if d.exists():
        subprocess.run(["rm", "-rf", str(d)], check=True)
    d.mkdir(parents=True)
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (d / MIG).mkdir(parents=True)
    for f in base_migrations:
        (d / MIG / f).write_text("-- base\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "base")
    return d


def _run_script(mode: str, repo: Path, base_sha: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DRIFT_REPO"] = str(repo)
    if base_sha:
        env["DRIFT_BASE_SHA"] = base_sha
    return subprocess.run(
        ["bash", str(SCRIPT), mode],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )


def test_prefix_duplicates_rejected():
    d = _make_repo(["0012_a.sql", "0012_b.sql"])
    r = _run_script("prefix", d)
    assert r.returncode == 1, r.stdout
    assert "0012" in r.stdout


def test_prefix_unique_ok():
    d = _make_repo(["0001_a.sql", "20260813000001_b.sql"])
    r = _run_script("prefix", d)
    assert r.returncode == 0, r.stderr


def test_prefix_non_conforming_rejected():
    d = _make_repo(["0001_a.sql", "scratch.sql"])
    r = _run_script("prefix", d)
    assert r.returncode == 1, r.stdout
    assert "scratch.sql" in r.stdout


def test_diff_modified_base_migration_rejected():
    d = _make_repo(["0001_a.sql"])
    base = _git_sha(d)
    (d / MIG / "0001_a.sql").write_text("-- changed\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "edit")
    r = _run_script("diff", d, base)
    assert r.returncode == 1, r.stdout
    assert "0001_a.sql" in r.stdout


def test_diff_deleted_base_migration_rejected():
    d = _make_repo(["0001_a.sql"])
    base = _git_sha(d)
    (d / MIG / "0001_a.sql").unlink()
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "delete")
    r = _run_script("diff", d, base)
    assert r.returncode == 1, r.stdout
    assert "0001_a.sql" in r.stdout


def test_diff_renamed_base_migration_rejected():
    d = _make_repo(["0001_a.sql"])
    base = _git_sha(d)
    (d / MIG / "0001_a.sql").rename(d / MIG / "20260813000099_a.sql")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "rename")
    r = _run_script("diff", d, base)
    assert r.returncode == 1, r.stdout


def test_diff_added_migration_allowed():
    d = _make_repo(["0001_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_new.sql").write_text("-- new\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "add")
    r = _run_script("diff", d, base)
    assert r.returncode == 0, r.stderr


def test_diff_same_pr_added_then_edited_allowed():
    # A file ADDED in this PR (not in base) may be edited pre-merge.
    d = _make_repo(["0001_a.sql"])
    base = _git_sha(d)
    (d / MIG / "20260813000099_new.sql").write_text("-- new v1\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "add v1")
    (d / MIG / "20260813000099_new.sql").write_text("-- new v2 (fix)\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "fix own new migration")
    r = _run_script("diff", d, base)
    assert r.returncode == 0, r.stderr


def test_diff_missing_base_sha_exit_2():
    d = _make_repo(["0001_a.sql"])
    r = _run_script("diff", d)
    assert r.returncode == 2, r.stdout


def _git_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
