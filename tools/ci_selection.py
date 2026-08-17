#!/usr/bin/env python3
"""Tiered test selection (#1021) — the single parameterized selection function.

Consumed by python-ci.yml's `changes` job (PRs) and the nightly audit.
Emits JSON: {surfaces: [...], full: bool, test_files: [...] | "ALL"}.

Selection rules (fail-closed, conservative):
- push to main / schedule  -> full (tier 3 — the trunk backstop)
- any changed file UNKNOWN to the surface map -> full (new dirs/subsystems)
- any changed SHARED/core module -> full (cross-cutting code wants max coverage)
- otherwise -> tier 2 = core ∪ union(matched surfaces' test files)
  (docs-only PRs -> core set only — the always-on smoke)

Also:
- --integrity: fail if any tests/*.py is absent from the manifest
  (unlisted test files would silently drop out of selection — the drift trap)
- audit artifact: writes the selection record (pr_sha, changed_files, surfaces,
  selected tests, fn version) for the nightly recall audit (scope v5).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

SELECTION_FN_VERSION = "1.1.0"

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "config" / "ci-surfaces.yml"
TESTS_DIR = REPO / "tests"

# Shared/cross-cutting modules -> full matrix (conservative per scope v5).
SHARED_MODULES = (
    "tortoise/sdk.py",
    "tortoise/ep.py",
    "tortoise/tool_registry.py",
    "tortoise/mcp_server.py",
    "tortoise/projection/",
    "tests/conftest.py",
    "tests/fake_control_plane.py",
    "pyproject.toml",
    "requirements.txt",
    ".github/workflows/python-ci.yml",
    ".github/actions/",
)

# Source-path patterns that trigger each surface (surface -> test files come
# from the manifest).
SOURCE_PATTERNS = {
    "onboarding": ("tortoise/onboarding/", "website/welcome.html",
                   "website/onboarding-prompt.md", "website/self-hosted.html"),
    "ep": ("tortoise/decide.py", "tortoise/dream.py", "tortoise/analyze.py",
           "tortoise/ranking.py"),
    "sdk": ("tortoise/ids.py", "tortoise/models.py", "tortoise/crypto.py"),
    "api": ("tortoise/hosted_api.py", "tortoise/__main__.py", "tortoise/mcp_auth.py",
            "tortoise/quota.py", "tortoise/supabase_control.py",
            "tortoise/selfhost_api.py", "tortoise/session_auth.py"),
    # core is the fallback for any other python-relevant path
}

# Paths that are NOT python-relevant (docs/config PRs skip the matrix).
NON_PYTHON_PREFIXES = (
    "docs/", "website/", "product/", "legal/", "growth/", "engineering/",
    "finance-accounting/", "menu-bar/", "ux/", "data/", "operations/",
    "capability/", "services/", "integrations/", "apps/", "spike/", "tools/",
    ".ci-checks/", "supabase/",
)


def load_manifest() -> dict:
    import yaml  # local import (uv provides pyyaml via the dev group)
    return yaml.safe_load(MANIFEST.read_text())


def classify_test_file(name: str, manifest: dict) -> str | None:
    for surface, files in manifest["surfaces"].items():
        if name in files:
            return surface
    return None


def select(changed_files: list[str], event: str, manifest: dict) -> dict:
    slow = set(manifest.get("slow_files", []))
    # #1371: slow files run ONLY in the test-slow job — never in the fast
    # gate's tier-1/tier-2 selections (they are already covered there).
    # Every return path carries slow_files so the workflow's changes job can
    # always emit it (a missing key would KeyError the nightly/schedule run).
    if event in ("push", "schedule"):
        return {"surfaces": list(manifest["surfaces"]), "full": True,
                "test_files": "ALL", "slow_files": sorted(slow)}

    tier1 = set(manifest.get("tier1", [])) - slow
    changed = [c for c in changed_files if c and not c.startswith(NON_PYTHON_PREFIXES)]
    if not changed:
        # docs-only PR -> tier 1 (curated smoke) only
        return {"surfaces": [], "full": False, "test_files": sorted(tier1),
                "slow_files": sorted(slow)}

    # Shared module -> full
    if any(c.startswith(SHARED_MODULES) for c in changed):
        return {"surfaces": list(manifest["surfaces"]), "full": True,
                "test_files": "ALL", "slow_files": sorted(slow)}

    matched: set[str] = set()
    unknown: list[str] = []
    for c in changed:
        found = False
        for surface, pats in SOURCE_PATTERNS.items():
            if surface == "core":
                continue
            if any(c.startswith(p) for p in pats):
                matched.add(surface)
                found = True
        if not found:
            if c.startswith("tortoise/") or c.startswith("tests/") or \
               c.startswith("graph-scripts/") or c.startswith("config/") or \
               c.startswith("validation/") or c.startswith("packs/"):
                matched.add("core")  # engine/registry code -> core surface
                found = True
        if not found:
            unknown.append(c)
    if unknown:
        # New/unknown path -> fail-closed full matrix (scope v5 decision 1)
        return {"surfaces": list(manifest["surfaces"]), "full": True,
                "test_files": "ALL", "slow_files": sorted(slow)}

    # Test-file changes select their owning surface
    for c in changed:
        if c.startswith("tests/") and c.endswith(".py"):
            s = classify_test_file(c[len("tests/"):], manifest)
            if s:
                matched.add(s)

    if not matched:
        matched.add("core")

    surfaces = sorted(matched)
    files = set(tier1)  # tier 2 = tier 1 ∪ surface-matched (scope v5 dec 5)
    for s in surfaces:
        files.update(manifest["surfaces"].get(s, []))
    files -= slow  # #1371: slow files never run in the fast gate
    return {"surfaces": surfaces, "full": False, "test_files": sorted(files),
            "slow_files": sorted(slow)}


def integrity(manifest: dict) -> list[str]:
    """All tests/*.py must be in the manifest — the drift trap."""
    missing = []
    for f in sorted(TESTS_DIR.glob("test_*.py")):
        if classify_test_file(f.name, manifest) is None:
            missing.append(f.name)
    return missing


def slow_file_issues(manifest: dict) -> list[str]:
    """#1371: slow_files must be non-empty, classified, and never in tier1.

    Fail-closed so the fast gate can never silently drag a slow file back in
    (the #1260/#1270 drift class) or drop one from test-slow coverage.
    """
    issues: list[str] = []
    slow = manifest.get("slow_files", [])
    if not slow:
        issues.append("slow_files is empty (must list the test-slow files)")
    tier1 = set(manifest.get("tier1", []))
    for f in slow:
        if classify_test_file(f, manifest) is None:
            issues.append(f"slow file {f} is not in any surface (unclassified)")
        if f in tier1:
            issues.append(f"slow file {f} is also in tier1 (fast gate leak)")
    return issues


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--changed-files", default="", help="newline-separated changed files")
    ap.add_argument("--event", default="pull_request", choices=["push", "pull_request", "schedule"])
    ap.add_argument("--integrity", action="store_true", help="verify manifest coverage")
    args = ap.parse_args()

    manifest = load_manifest()

    if args.integrity:
        missing = integrity(manifest)
        problems = missing + slow_file_issues(manifest)
        if problems:
            print(f"❌ manifest drift: {problems}")
            return 1
        print("✅ integrity: all test files classified; slow_files consistent")
        return 0

    changed = [l.strip() for l in args.changed_files.splitlines() if l.strip()]
    if args.changed_files == "-":
        changed = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]
    result = select(changed, args.event, manifest)

    # Audit artifact (scope v5 decision 1): replayable selection record.
    artifact = {
        "pr_sha": os.environ.get("GITHUB_SHA", ""),
        "selection_fn_version": SELECTION_FN_VERSION,
        "changed_files": changed,
        "surfaces": result["surfaces"],
        "full": result["full"],
        "selected_tests": result["test_files"],
    }
    out_dir = Path(os.environ.get("CI_SELECTION_ARTIFACT_DIR", REPO / ".ci-selection"))
    out_dir.mkdir(exist_ok=True)
    (out_dir / "selection.json").write_text(json.dumps(artifact, indent=2))

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
