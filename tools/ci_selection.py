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
import re
import sys
from pathlib import Path

SELECTION_FN_VERSION = "1.1.0"

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "config" / "ci-surfaces.yml"
TESTS_DIR = REPO / "tests"
WORKFLOW = REPO / ".github" / "workflows" / "python-ci.yml"

# #1266: the test (a)/(b) halves must stay count-balanced within this delta.
# A tilt beyond it means someone added files to one half without rebalancing
# (the exact drift that pushed half (a) over the watchdog cap).
HALF_IMBALANCE_TOLERANCE = 3

# bash/heredoc-safe newline (the pi bash wrapper mangles raw \n in heredocs)
NL = chr(10)

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
    "battery": ("battery/",),
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
    return unlisted_tests(TESTS_DIR, manifest)


def unlisted_tests(tests_dir: Path, manifest: dict) -> list[str]:
    """Top-level tests/test_*.py absent from every surface in the manifest."""
    missing = []
    for f in sorted(tests_dir.glob("test_*.py")):
        if classify_test_file(f.name, manifest) is None:
            missing.append(f.name)
    return missing


def register_tests(manifest_path: Path, tests_dir: Path, surface: str,
                   manifest: dict) -> list[str]:
    """Auto-register unlisted test files under `surface` (#1429).

    Text-preserving: appends `  - name.py` in alphabetical position inside the
    surface's list block, keeping the hand-curated manifest format. Idempotent.
    Returns the files that were registered (empty when already clean). The
    default surface is `core` — the selection logic's own fallback; the shared
    / unknown-path / push-to-main rules still expand to the full matrix, so a
    misclassified new test keeps running on every broad change.
    """
    missing = unlisted_tests(tests_dir, manifest)
    if not missing:
        return []
    lines = manifest_path.read_text().splitlines(keepends=True)
    # locate the surface block: "  <surface>:" then "  - name" lines (strip
    # any trailing inline comment from the key)
    block_start = None
    for i, ln in enumerate(lines):
        key = ln.split("#", 1)[0].rstrip()
        if key == f"  {surface}:":
            block_start = i
            break
    if block_start is None:
        # append the new surface block at the end of the surfaces: mapping.
        # The surfaces are the only 2-space-indented mapping keys, followed by
        # the top-level tier1: key (column 0) — that is the reliable boundary.
        anchor = next((i for i, ln in enumerate(lines) if ln.startswith("tier1:")), len(lines))
        new_block = [f"  {surface}:{NL}"] + [f"  - {n}{NL}" for n in missing]
        lines[anchor:anchor] = new_block
    else:
        # collect existing entries in this block (until next "  x:" or non-list line)
        end = block_start + 1
        entries = []
        while end < len(lines) and (lines[end].startswith("  - ") or lines[end].lstrip().startswith("#")):
            if lines[end].startswith("  - "):
                entries.append(lines[end])
            end += 1
        names = [e.strip()[2:].split("#", 1)[0].rstrip() for e in entries]  # "  - name.py" -> "name.py" (strip trailing comments)
        # INSERT-ONLY: place each new name at its alphabetical position among
        # the existing entries; never re-order pre-existing lines (keeps the
        # diff surgical — normalizing the whole block would churn the manifest).
        to_add = [n for n in missing if n not in set(names)]
        for n in sorted(to_add):
            pos = 0
            while pos < len(names) and names[pos] < n:
                pos += 1
            lines[block_start + 1 + pos:block_start + 1 + pos] = [f"  - {n}{NL}"]
            names.insert(pos, n)
            end += 1
    manifest_path.write_text("".join(lines))
    return missing


def register(manifest_path: Path, tests_dir: Path, surface: str) -> list[str]:
    """Load + register in one call (CLI entry)."""
    import yaml
    manifest = yaml.safe_load(manifest_path.read_text())
    return register_tests(manifest_path, tests_dir, surface, manifest)


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


def parse_matrix_halves(workflow_text: str) -> dict[str, list[str]]:
    """#1266: extract the test job's (a)/(b) matrix halves from python-ci.yml.

    The halves are folded scalars (`files: >-`) with bare file names
    (no .py) — the run step maps them to `tests/<name>.py` and `bench/*`.
    Returns {"a": [...], "b": [...]} — empty when the parse fails so callers
    can fail closed on "workflow changed shape" instead of silently passing.
    """
    halves: dict[str, list[str]] = {}
    for m in re.finditer(r"- half: ([ab])\n\s+files: >-\n\s+([^\n]+)\n", workflow_text):
        halves[m.group(1)] = m.group(2).split()
    return halves


def workflow_halves_issues(manifest: dict, halves: dict[str, list[str]],
                           tests_dir: Path | None = None) -> list[str]:
    """#1266: fail-closed checks tying the workflow's matrix halves to the
    manifest — the fast gate must never leak a slow file, carry a file that
    left the manifest, list a dead file, run a file twice, or tilt.

    bench/* entries are exempt (they live outside tests/ and the manifest).
    """
    issues: list[str] = []
    if not halves:
        return ["no matrix halves found in python-ci.yml (workflow parse failure)"]
    tests_dir = tests_dir or TESTS_DIR
    slow = set(manifest.get("slow_files", []))
    all_manifest = set()
    for fs in manifest["surfaces"].values():
        all_manifest.update(fs)
    # bare name -> .py name (halves store bare names, manifest stores .py)
    by_bare = {f[:-3]: f for f in all_manifest}
    slow_bare = {f[:-3] for f in slow}
    seen: set[str] = set()
    for half, files in halves.items():
        for f in files:
            if f.startswith("bench/"):
                continue
            if f in slow_bare:
                issues.append(f"slow file {f}.py leaked into half {half} (fast gate leak — test-slow only, #1266)")
            elif f not in by_bare:
                issues.append(f"half {half} entry {f} is not in any manifest surface (unclassified drift, #1266)")
            if not (tests_dir / f"{f}.py").exists():
                issues.append(f"half {half} entry {f} has no tests/{f}.py (dead entry, #1266)")
            if f in seen:
                issues.append(f"half entry {f} is in BOTH halves (double-run, #1266)")
            seen.add(f)
    counts = {h: len(fs) for h, fs in halves.items()}
    if abs(counts.get("a", 0) - counts.get("b", 0)) > HALF_IMBALANCE_TOLERANCE:
        issues.append(
            f"matrix halves imbalanced: a={counts.get('a', 0)} vs "
            f"b={counts.get('b', 0)} (tolerance ±{HALF_IMBALANCE_TOLERANCE}) — "
            "rebalance before adding more files (#1266)")
    return issues


def fast_files_absent_from_halves(manifest: dict, halves: dict[str, list[str]]) -> list[str]:
    """#1266 (informational): manifest fast files that are in NO half — the
    full-matrix coverage hole. Slow files and bench/* are excluded. Kept as
    a warning (not fail-closed): closing it would push 100+ files into the
    fast gate and blow the watchdog budget (see the scoping doc).
    """
    slow = set(manifest.get("slow_files", []))
    fast = set()
    for fs in manifest["surfaces"].values():
        fast.update(fs)
    fast -= slow
    halfset = {f for fs in halves.values() for f in fs}
    return sorted(f for f in fast if f[:-3] not in halfset)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--changed-files", default="", help="newline-separated changed files")
    ap.add_argument("--event", default="pull_request", choices=["push", "pull_request", "schedule"])
    ap.add_argument("--integrity", action="store_true", help="verify manifest coverage")
    ap.add_argument("--register", action="store_true",
                    help="auto-register unlisted test files in the manifest (#1429)")
    ap.add_argument("--surface", default="core",
                    choices=["api", "core", "ep", "onboarding", "sdk"],
                    help="surface for --register (default: core — the selection fallback)")
    ap.add_argument("--dry-run", action="store_true", help="preview --register without writing")
    args = ap.parse_args()

    manifest = load_manifest()

    if args.integrity:
        missing = integrity(manifest)
        problems = missing + slow_file_issues(manifest)
        # #1266: the workflow's matrix halves are a second source of truth —
        # fail closed on slow leaks / unclassified / dead / duplicate / tilt.
        halves = parse_matrix_halves(WORKFLOW.read_text())
        problems += workflow_halves_issues(manifest, halves)
        if problems:
            print(f"❌ manifest drift: {problems}")
            return 1
        absent = fast_files_absent_from_halves(manifest, halves)
        if absent:
            sample = ", ".join(absent[:8])
            print(f"⚠️  {len(absent)} manifest fast files are in NO half "
                  f"(full-matrix coverage hole, #1266): {sample} …")
        print("✅ integrity: all test files classified; slow_files consistent; halves consistent")
        return 0

    if args.register:
        if args.dry_run:
            missing = unlisted_tests(TESTS_DIR, manifest)
            if missing:
                print(f"ℹ️  would register under {args.surface}: {missing}")
            else:
                print("✅ nothing to register")
            return 0
        added = register(MANIFEST, TESTS_DIR, args.surface)
        if added:
            print(f"✅ registered {len(added)} test file(s) under {args.surface}: {added}")
            print("   review: config/ci-surfaces.yml — move a file to another surface if the default is wrong.")
        else:
            print("✅ manifest already covers all test files")
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
