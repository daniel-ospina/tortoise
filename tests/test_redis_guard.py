"""redis-guard hook tests (plan Task 13)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUARD = REPO / "tools" / "redis-guard.py"


def _run_guard(*files):
    proc = subprocess.run(
        [sys.executable, str(GUARD), *[str(f) for f in files]],
        capture_output=True, text=True, timeout=30,
    )
    return proc.returncode, proc.stdout


def test_bad_relative_path_rejected():
    """Fixture with relative FalkorProjection -> hook REJECTS (rc=1)."""
    rc, out = _run_guard(REPO / "tests/fixtures/redis-guard/bad_relative_path.py")
    assert rc == 1
    assert "relative-path" in out


def test_good_absolute_path_accepted():
    """Fixture with absolute path -> hook ACCEPTS (rc=0)."""
    rc, out = _run_guard(REPO / "tests/fixtures/redis-guard/good_absolute_path.py")
    assert rc == 0


def test_real_test_dir_import_allowed():
    """Real test files (allowlisted) with direct redislite imports pass."""
    rc, out = _run_guard(REPO / "tests" / "test_guard.py")
    assert rc == 0


def test_whole_repo_clean():
    """Whole-repo scan: 0 violations (migrations eliminated the patterns)."""
    rc, out = _run_guard()
    assert rc == 0, out


def test_path_default_pattern_caught():
    """Path('tortoise.db') argparse default is caught (pattern 4)."""
    from pathlib import Path as P
    tmp = P(REPO) / "tests/fixtures/redis-guard" / "bad_path_default.py"
    tmp.write_text('import argparse\nfrom pathlib import Path\n'
                   'p = argparse.ArgumentParser()\n'
                   'p.add_argument("--db", type=Path, default=Path("tortoise.db"))\n')
    try:
        rc, out = _run_guard(tmp)
        assert rc == 1, f"Path-default not caught: {out}"
        assert "Path('tortoise.db')" in out or "tortoise.db" in out
    finally:
        tmp.unlink(missing_ok=True)


def test_noqa_annotation_allows():
    """# noqa: redis-guard permits a documented intentional bypass."""
    from pathlib import Path as P
    tmp = P(REPO) / "tests/fixtures/redis-guard" / "good_noqa.py"
    tmp.write_text(
        'from tortoise.projection import FalkorProjection\n'
        "proj = FalkorProjection('tortoise.db')  # noqa: redis-guard\n")
    try:
        rc, out = _run_guard(tmp)
        assert rc == 0, f"noqa not honored: {out}"
    finally:
        tmp.unlink(missing_ok=True)
