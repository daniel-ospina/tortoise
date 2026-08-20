#!/usr/bin/env python3
"""redis-guard — unified pre-commit/CI regression hook (issue #176, Task 13).

Blocks reintroduction of the redislite process leak patterns:
  1. FalkorProjection(<relative-path>) calls (per-CWD Category-3 leaks)
  2. Direct `from redislite.falkordb_client import FalkorDB` imports
     (incl. module-import and wildcard forms) that bypass the choke-point
  3. `from redislite import Redis` / `redislite.Redis(` (parent class bypass)
  4. `Path("tortoise.db")` argparse defaults (invisible to pattern 1)

Honors `# noqa: redis-guard` inline annotations and built-in allowlists
(tests/, validation/, and the choke-point modules that legitimately import
redislite). Used by .pre-commit-config.yaml and CI (must BLOCK merge).

Usage:
    python scripts/redis-guard.py            # scan repo, exit 1 on violations
    python scripts/redis-guard.py <files...> # scan specific files
"""
from __future__ import annotations  # noqa: I001

import re
import sys
from pathlib import Path

# Repo root via git (robust to worktrees, symlinks, relative __file__)
import subprocess as _sp
_REPO_ROOT = _sp.run(
    ["git", "rev-parse", "--show-toplevel"],
    capture_output=True, text=True).stdout.strip()
REPO_ROOT = Path(_REPO_ROOT) if _REPO_ROOT else Path(__file__).resolve().parent.parent

ALLOWED_DIRS = ("tests/", "validation/")
# Fixtures under tests/fixtures/redis-guard/ MUST be checked (not exempt) —
# they exercise the hook's reject/accept behavior.
CHECKED_FIXTURES_PREFIX = "tests/fixtures/redis-guard/"
ALLOWED_FILES = (
    "tortoise/test_cross_ontology.py",
    "tortoise/projection/__init__.py",
    "tortoise/__init__.py",
    "tortoise/embedded_reaper.py",  # reaper legitimately connects via redislite
    "graph-scripts/smoke_test.py",  # documented intentional bypass
)

# Pattern 1: relative-path FalkorProjection calls — match a quoted path that
# is NOT absolute (/...), NOT tilde (~/...), and NOT explicitly ./ or ../.
# The absolute-path exclusion must reject ANY leading '/' (e.g. /tmp/x.db),
# not just '/~'. Runtime _abs() choke-point (tortoise/config.py) rejects all
# relative forms incl. './'; this hook is source-level belt-and-suspenders.
RELATIVE_PROJECTION = re.compile(
    r"FalkorProjection\(\s*['\"](?!/|~|\./|\.\./)"
)
# Pattern 2: direct redislite.falkordb_client imports (all forms)
DIRECT_IMPORT = re.compile(
    r"(?:from\s+redislite\.falkordb_client\s+import\s+\*?)"
    r"|(?:^\s*import\s+redislite\.falkordb_client)"
    r"|(?:from\s+redislite\.falkordb_client\s+import\s+FalkorDB)"
)
# Pattern 3: redislite.Redis parent-class bypass
REDIS_PARENT = re.compile(r"(?:from\s+redislite\s+import\s+.*\bRedis\b)|(?:redislite\.Redis\()")
# Pattern 4: Path('tortoise.db') argparse defaults
PATH_DEFAULT = re.compile(r"default\s*=\s*Path\(\s*['\"]tortoise\.db['\"]\s*\)")


def is_allowed(path: Path) -> bool:
    try:
        rel = str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        rel = str(path)
    # redis-guard fixtures are NOT exempt (they must exercise the hook)
    if rel.startswith(CHECKED_FIXTURES_PREFIX):
        return False
    if any(rel.startswith(d) for d in ALLOWED_DIRS):
        return True
    return rel in ALLOWED_FILES


def check_file(path: Path) -> list[str]:
    """Return list of violation descriptions for a file."""
    if is_allowed(path):
        return []
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    violations = []
    for lineno, line in enumerate(content.splitlines(), 1):
        if "# noqa: redis-guard" in line:
            continue
        if RELATIVE_PROJECTION.search(line):
            violations.append(f"{path}:{lineno}: relative-path FalkorProjection call")
        if DIRECT_IMPORT.search(line):
            violations.append(f"{path}:{lineno}: direct redislite.falkordb_client import")
        if REDIS_PARENT.search(line):
            violations.append(f"{path}:{lineno}: redislite.Redis bypass")
        if PATH_DEFAULT.search(line):
            violations.append(f"{path}:{lineno}: Path('tortoise.db') default")
    return violations


def main(argv: list[str] | None = None) -> int:
    files = argv or [str(p) for p in (REPO_ROOT / "tortoise").rglob("*.py")]
    if not argv:
        files += [str(p) for p in (REPO_ROOT / "graph-scripts").rglob("*.py")]
    violations = []
    for f in files:
        p = Path(f)
        if not p.exists() or p.suffix != ".py":
            continue
        violations.extend(check_file(p))
    if violations:
        print("redis-guard: BLOCKED — redislite leak patterns found:")
        for v in violations:
            print(f"  ✗ {v}")
        print("Fix: route through FalkorProjection() canonical, or add"
              " '# noqa: redis-guard' only for documented intentional bypasses.")
        return 1
    print("redis-guard: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
