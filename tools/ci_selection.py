#!/usr/bin/env python3
"""Tiered test selection (#1021) — the single parameterized selection function.

Consumed by python-ci.yml's `changes` job (PRs) and the nightly audit.
Emits JSON: {surfaces: [...], full: bool, test_files: [...] | "ALL",
slow_files: [...], slow_run: bool, slow_selected: [...],
carve_out_run: bool}.

#2147/#2148: the test-slow + test-carve-out jobs were the two diff-unaware
python-ci legs (2026-09-02-ci-audit F1/F2 — a docs-only PR paid ~59 runner-
min slow + 23.5 min carve-out). select() now emits the diff-gate contract on
every return path: slow_run (test-slow runs), slow_selected (the slow files
test-slow should run — surface-matched on tier-2 PRs, the whole committed
leg set = slow_files - carve_out on full selections), carve_out_run (the
carve-out job runs — full selections or a matched surface owns carve-out
files). The workflow's changes job echoes these; docs/website-only PRs
(surfaces == []) skip both legs, trunk (push) + nightly (schedule) +
unknown/shared-module PRs keep full coverage. NOTE: config/* is NOT a
docs-only path — it falls through to the core surface, so config-only PRs
run both legs (conservative: manifest edits are exactly when the slow split
should be exercised; the workflow header comment says the same).

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

SELECTION_FN_VERSION = "1.3.0"

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
    # cross-cutting leaf: exception classes consumed by sdk, api, core, AND ep
    # surfaces (test_divergence_conformance, test_epic903_modes,
    # test_ingest_*, test_calibration) — a change here runs the full matrix.
    "tortoise/exceptions.py",
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
    "sdk": ("tortoise/ids.py", "tortoise/models.py", "tortoise/crypto.py",
            "tortoise/reader.py", "tortoise/retrieval.py",
            # ask-lane shared vocabulary/gating: a PR touching ONLY these
            # must select sdk so test_ask_sdk.py (+ ask reader/calibration
            # pins) run — the old fallback to core ran NO ask tests.
            # exceptions.py is SHARED (cross-cutting leaf -> full matrix);
            # transport.py is dual-wired with api: its only direct unit test
            # is test_metering.py::test_selfhost_transport_exemption.
            "tortoise/schemas.py", "tortoise/transport.py",
            # #2071: the spot-check tools are the product ask-lane QA — a
            # spot-check-only PR selects the sdk surface (its tests live
            # there: test_ask_spotcheck_judge.py).
            "tools/ask_spotcheck.py", "tools/ask_spotcheck_consistency.py",
            "tools/ask_spotcheck_probe.py"),
    "api": ("tortoise/hosted_api.py", "tortoise/__main__.py", "tortoise/mcp_auth.py",
            "tortoise/quota.py", "tortoise/supabase_control.py",
            "tortoise/selfhost_api.py", "tortoise/session_auth.py",
            # ask-lane server surfaces: test_metering.py + test_selfhost_rest.py
            # live in the api surface — a metering.py/selfhost.py-only PR must
            # select api (core runs no ask tests). transport.py is dual-wired
            # here (in addition to sdk): its only direct unit test is
            # test_metering.py::test_selfhost_transport_exemption.
            "tortoise/metering.py", "tortoise/selfhost.py",
            "tortoise/transport.py"),
    # eval (#1349): the probe, LongMemEval/mini-BEIR harnesses, threshold
    # tools, benchmark infra, and the backfill script all produce gate
    # evidence — their tests live in the eval surface (config/ci-surfaces.yml).
    # W2-b (#2098) BPRE trigger (plan §2.1) is wired at the TEST-FILE level
    # instead of here: the write-path benchmark gate file is registered on
    # the api (hosted capture/provenance), core (session_import fallback),
    # ep (dream EP pass), and eval surfaces, so a PR touching any of those
    # source paths runs the replay gate via the matched surface's files
    # without displacing the surface's existing selection.
    "eval": ("tools/longmem_eval/", "tools/mini_beir/",
             "tools/embedder_probe.py", "tools/calibrate_thresholds.py",
             "tools/pair_label_runner.py", "benchmarks/",
             "graph-scripts/backfill_embeddings.py",
             # P2-1 (code review): an embeddings.py/cross_lens.py-only PR must
             # select eval so probe/vector-arm/threshold tests run (they assert
             # the EMBEDDING_MODEL + threshold constants — drift class #1260).
             "tortoise/embeddings.py", "tortoise/cross_lens.py"),
    # core is the fallback for any other python-relevant path
}

# Paths that are NOT python-relevant (docs/config PRs skip the matrix).
NON_PYTHON_PREFIXES = (
    "docs/", "website/", "product/", "legal/", "growth/", "engineering/",
    "finance-accounting/", "menu-bar/", "ux/", "data/", "operations/",
    "capability/", "services/", "integrations/", "apps/", "spike/", "tools/",
    ".ci-checks/", "supabase/",
)

# tools/ paths that ARE python-relevant for selection (#1349). The flat
# NON_PYTHON_PREFIXES tuple above includes "tools/", which would swallow
# every tools change before SOURCE_PATTERNS matching (a tools-only PR would
# yield empty changed -> tier-1 smoke). These carve-out paths are re-included
# by the filter expression in select() so tools/longmem_eval/run.py etc. can
# select the eval surface. NOT wholesale tools/ removal: unrelated tools
# changes (e.g. tools/kappa.py) must keep failing closed to the old behavior.
TOOL_CARVEOUTS = (
    "tools/longmem_eval/",
    "tools/mini_beir/",
    "tools/embedder_probe.py",
    "tools/calibrate_thresholds.py",
    "tools/pair_label_runner.py",
    # #2071: the product-lane QA spot-check tools (ask_spotcheck + the
    # consistency/probe harnesses) use the eval judge and own the
    # test_ask_spotcheck_judge.py suite — a spot-check-only change must
    # select the sdk ask-lane surface, not drop to tier-1 smoke.
    "tools/ask_spotcheck.py",
    "tools/ask_spotcheck_consistency.py",
    "tools/ask_spotcheck_probe.py",
    # #2159 review P2-3: the diff-gate selector itself must never classify
    # as docs-only (the two gated legs would skip AND the wiring pins in
    # tests/test_ci_selection.py would never run on the PR that owns them).
    # No source pattern matches tools/ci_selection.py, so a selector-only PR
    # lands in the unknown-path fail-closed branch -> FULL matrix + both
    # legs — the heaviest but safest gate for the file that owns gating.
    "tools/ci_selection.py",
)


def load_manifest() -> dict:
    import yaml  # local import (uv provides pyyaml via the dev group)
    return yaml.safe_load(MANIFEST.read_text())


def classify_test_file(name: str, manifest: dict) -> str | None:
    """Return the surface owning a test file.

    ``name`` is the ``tests/``-relative path (e.g. ``longmem_eval/test_vector_arm.py``)
    or a bare basename (backward-compat: the manifest was basename-keyed
    before subdir registration). Both forms are checked against each
    surface's entries, so subdir files classify correctly and legacy
    top-level basename entries keep working.
    """
    base = name.rsplit("/", 1)[-1]
    for surface, files in manifest["surfaces"].items():
        if name in files or base in files:
            return surface
    return None


# #2147/#2148 (2026-09-02-ci-audit F1/F2): surface -> slow-leg / carve-out
# file ownership maps. The test-slow and test-carve-out jobs were the two
# diff-unaware python-ci legs (docs-only PR #2132 paid 29m43+29m08 slow +
# 23m31 carve-out); select() now emits slow_run / slow_selected /
# carve_out_run on every return path so the workflow can diff-gate them.
def slow_leg_by_surface(manifest: dict) -> dict[str, set[str]]:
    """Surface -> slow files that run in test-slow (slow minus carve-out).

    The 6 slow carve-out files (test_reaper et al.) run in the dedicated
    URI-unset carve-out job (epic #1647 E2E-4), never the docker slow legs
    — the test-slow committed leg set is exactly slow_files - carve_out
    (pinned by the workflow's drift-guard step, #1471).

    First-match semantics (#2159 review P2-2): classify_test_file returns
    the FIRST surface whose patterns match (dict order api, battery, core,
    ep, onboarding, sdk, classify, eval). A slow/carve file registered
    under two surfaces therefore owns to the first — a tier-2 PR touching
    ONLY the second surface will not run it (full runs stay the backstop).
    test_diff_gate_keys_emitted_on_every_return_path pins every
    slow_selected subset to slow_files - carve_out; the tier-2 intersection
    tests pin the per-surface splits."""
    slow = set(manifest.get("slow_files", []))
    carve = set(manifest.get("carve_out", []))
    by_surface: dict[str, set[str]] = {}
    for f in sorted(slow - carve):
        by_surface.setdefault(classify_test_file(f, manifest), set()).add(f)
    return by_surface


def carve_out_by_surface(manifest: dict) -> dict[str, set[str]]:
    """Surface -> carve-out files it owns (the E2E-4 embedded set)."""
    by_surface: dict[str, set[str]] = {}
    for f in sorted(manifest.get("carve_out", [])):
        by_surface.setdefault(classify_test_file(f, manifest), set()).add(f)
    return by_surface


def _full_selection(manifest: dict, slow: set[str]) -> dict:
    """push/schedule/shared-module/unknown-path selection (tier 3).

    The trunk + nightly backstop: full matrix AND both diff-gated legs run
    (test-slow + test-carve-out) — main can never lose slow/carve-out
    coverage to a PR-shape gate."""
    carve = set(manifest.get("carve_out", []))
    return {"surfaces": list(manifest["surfaces"]), "full": True,
            "test_files": "ALL", "slow_files": sorted(slow),
            # #2147/#2148: full selection always runs both legs; slow_selected
            # carries the whole committed leg set (slow - carve-out).
            "slow_run": True, "carve_out_run": True,
            "slow_selected": sorted(slow - carve)}


def select(changed_files: list[str], event: str, manifest: dict) -> dict:
    slow = set(manifest.get("slow_files", []))
    # #1371: slow files run ONLY in the test-slow job — never in the fast
    # gate's tier-1/tier-2 selections (they are already covered there).
    # Every return path carries slow_files so the workflow's changes job can
    # always emit it (a missing key would KeyError the nightly/schedule run).
    # #2147/#2148: every return path ALSO carries slow_run / slow_selected /
    # carve_out_run (the test-slow + test-carve-out diff-gate contract).
    if event in ("push", "schedule"):
        return _full_selection(manifest, slow)

    tier1 = set(manifest.get("tier1", [])) - slow
    # Filter out non-python-relevant paths, but RE-INCLUDE the tools carve-out
    # paths so they reach SOURCE_PATTERNS (see TOOL_CARVEOUTS).
    changed = [c for c in changed_files
               if c and (not c.startswith(NON_PYTHON_PREFIXES)
                         or c.startswith(TOOL_CARVEOUTS))]
    if not changed:
        # docs-only PR -> tier 1 (curated smoke) only; no slow/carve surface
        # is touched, so both diff-gated legs skip (#2147/#2148).
        return {"surfaces": [], "full": False, "test_files": sorted(tier1),
                "slow_files": sorted(slow),
                "slow_run": False, "carve_out_run": False,
                "slow_selected": []}

    # Shared module -> full
    if any(c.startswith(SHARED_MODULES) for c in changed):
        return _full_selection(manifest, slow)

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
        if not found:  # noqa: SIM102
            if c.startswith("tortoise/") or c.startswith("tests/") or \
               c.startswith("graph-scripts/") or c.startswith("config/") or \
               c.startswith("validation/") or c.startswith("packs/"):
                matched.add("core")  # engine/registry code -> core surface
                found = True
        if not found:
            unknown.append(c)
    if unknown:
        # New/unknown path -> fail-closed full matrix (scope v5 decision 1)
        return _full_selection(manifest, slow)

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
    # #1988: carve-out (embedded-only) files run in the dedicated carve-out
    # job — on tier-2 PR legs the fast-matrix process runs everything embedded
    # (URI unset) and exhausts its redislite spawn budget before the late
    # embedded suites (RedisLiteServerStartError); the carve-out job gives
    # them a fresh process. The carve-out job now runs on PRs too.
    carve = set(manifest.get("carve_out", []))
    files -= carve
    # #2147/#2148 tier-2: test-slow runs the slow files owned by the matched
    # surfaces (slow_selected — carve-out files excluded: they run in the
    # carve-out job when it triggers, never the docker slow legs); test-carve-
    # out runs when a matched surface owns any carve-out file (embedded /
    # daemon / registry / guard coverage). Docs/website/config-only PRs
    # (surfaces == []) already returned above with both legs skipped.
    slow_by_surface = slow_leg_by_surface(manifest)
    carve_by_surface = carve_out_by_surface(manifest)
    slow_selected: set[str] = set()
    carve_out_run = False
    for s in surfaces:
        slow_selected.update(slow_by_surface.get(s, set()))
        if s in carve_by_surface:
            carve_out_run = True
    return {"surfaces": surfaces, "full": False, "test_files": sorted(files),
            "slow_files": sorted(slow),
            "slow_run": bool(slow_selected), "carve_out_run": carve_out_run,
            "slow_selected": sorted(slow_selected)}


def integrity(manifest: dict) -> list[str]:
    """All tests/**/test_*.py must be in the manifest — the drift trap.

    Recursive (rglob) so subdir test files are drift-checked too; the
    tests/e2e/ prefix is deliberately SKIPPED (#1349 — 4 direct + 13 hosted
    files, covered by welcome-e2e-monitor + legal-e2e jobs + ENV_BROKEN_FILES).
    Files are classified by their tests/-relative path so subdir files match
    the manifest's subdir keys.
    """
    missing = []
    for f in sorted(TESTS_DIR.rglob("test_*.py")):
        rel = f.relative_to(TESTS_DIR)
        if rel.parts[0] == "e2e":
            continue
        if classify_test_file(str(rel), manifest) is None:
            missing.append(str(rel))
    return missing


def unlisted_tests(tests_dir: Path, manifest: dict) -> list[str]:
    """Top-level tests/test_*.py absent from every surface in the manifest.

    Kept from origin/main's refactor — callers (workflow matrix checks)
    use the top-level glob; :func:`integrity` uses the recursive rglob form.
    """
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


# #1472: files excluded from the fast push legs by construction (they cannot
# run in THIS job — no live redis on 6379 for test_agent_signup; tests/e2e is
# excluded by construction since unlisted_tests only globs top-level files).
ENV_BROKEN_FILES = {"test_agent_signup.py"}


def carve_out_files(manifest: dict) -> set[str]:
    """Epic #1647 Task 9 (P3): the 17-file embedded carve-out set.

    The carve-out tests run embedded BY DESIGN (E2E-4) in the dedicated
    URI-unset job (TORTOISE_TEST_CARVE_OUT=1) — they are excluded from the
    docker fast legs AND the docker test-slow legs, so the docker lanes
    create ~zero redislite servers (E2E-7 orphan assert ≈ 0). The set
    mirrors tests/_embedded.TEST_NO_REDIRECT_STEMS (pinned by
    tests/test_ci_selection.py + tests/test_markers.py)."""
    return set(manifest.get("carve_out", []))


def push_legs(manifest: dict) -> dict:
    """#1472: partition every manifest-classified file into exactly one push
    leg (half_a / half_b / slow / env_broken / carve_out), parity-split the
    fast set.

    Single source of truth for the workflow's push matrix: registration in
    the manifest is sufficient — no manual matrix edit. Returns .py-less
    names (the workflow's matrix format). push_extra (bench files, not
    classifiable as top-level surfaces) appends to half b. Epic #1647
    Task 9: carve_out files are excluded from every docker leg and emitted
    as their own leg (the URI-unset carve-out job's file list).
    """
    slow = set(manifest.get("slow_files", []))
    carve_out = carve_out_files(manifest)
    classified = set()
    for s, files in manifest["surfaces"].items():  # noqa: B007
        classified.update(files)
    classified.update(manifest.get("tier1", []))
    classified.update(slow)
    fast = sorted(f for f in classified
                  if f not in slow and f not in ENV_BROKEN_FILES
                  and f not in carve_out)
    half_a = fast[0::2]
    half_b = fast[1::2]
    # #1485: distribute push_extra (bench files) EVENLY so the halves stay
    # within the #1266 ±3 tolerance (all-bench-in-half-b caused 135 vs 139).
    for i, f in enumerate(manifest.get("push_extra", [])):
        (half_a if i % 2 == 0 else half_b).append(f.replace(".py", ""))
    strip = lambda xs: sorted(x.replace(".py", "") for x in xs)  # noqa: E731
    return {"half_a": strip(half_a), "half_b": strip(half_b),
            # Epic #1647 Task 9: slow carve-out files (test_reaper et al.)
            # run in the URI-unset carve-out job, never the docker slow legs.
            "slow": strip(slow - carve_out),
            "env_broken": sorted(ENV_BROKEN_FILES),
            "carve_out": strip(carve_out)}


def leg_coverage_issues(manifest: dict) -> list[str]:
    """#1472 reverse drift: every classified file in exactly one push leg."""
    slow = set(manifest.get("slow_files", []))
    carve_out = carve_out_files(manifest)
    legs = push_legs(manifest)
    half_a = {f + ".py" for f in legs["half_a"]}
    half_b = {f + ".py" for f in legs["half_b"]}
    fast = half_a | half_b
    issues = []
    overlap = fast & slow
    if overlap:
        issues.append(f"fast/slow overlap: {sorted(overlap)}")
    if fast & ENV_BROKEN_FILES:
        issues.append(f"fast/env-broken overlap: {sorted(fast & ENV_BROKEN_FILES)}")
    if slow & ENV_BROKEN_FILES:
        issues.append(f"slow/env-broken overlap: {sorted(slow & ENV_BROKEN_FILES)}")
    # Epic #1647 Task 9: carve-out files must never ride the docker legs
    # (fast OR slow) — they are the E2E-4 embedded surface only. The config
    # sets MAY overlap (test_reaper et al. are slow AND carve-out); the LEGS
    # must not.
    if fast & carve_out:
        issues.append(f"carve-out file leaked into a fast leg: {sorted(fast & carve_out)}")
    slow_leg = {f + ".py" for f in legs["slow"]}
    if slow_leg & carve_out:
        issues.append(f"carve-out file leaked into the slow legs: {sorted(slow_leg & carve_out)}")
    if carve_out & ENV_BROKEN_FILES:
        issues.append(f"carve-out/env-broken overlap: {sorted(carve_out & ENV_BROKEN_FILES)}")
    classified = set()
    for s, files in manifest["surfaces"].items():  # noqa: B007
        classified.update(files)
    classified.update(manifest.get("tier1", []))
    classified.update(slow)
    for f in manifest.get("push_extra", []):
        if f in classified:
            issues.append(f"push_extra {f} is a classified top-level file (move it into a surface)")
    for f in sorted(ENV_BROKEN_FILES):
        if f not in classified:
            issues.append(f"env-broken {f} is not classified in the manifest")
    # carve-out files must be classified (they run in the carve-out job, which
    # keys off the config list) and must exist on disk (dead entries drift).
    for f in sorted(carve_out):
        if f not in classified:
            issues.append(f"carve-out {f} is not classified in any surface")
        if f in slow:
            continue  # slow carve-out files (test_reaper et al.) are legit
        if not (TESTS_DIR / f).exists():
            issues.append(f"carve-out {f} has no tests/{f} (dead entry)")
    return issues


def workflow_matrix_issues(workflow_path: str, manifest: dict) -> list[str]:
    """#1472: the workflow's matrix rows must come from the derivation, never
    re-hardcoded lists; ENV_BROKEN_FILES must not be duplicated in env."""
    import yaml
    issues = []
    try:
        wf = yaml.safe_load(open(workflow_path))  # noqa: SIM115
    except Exception as exc:
        return [f"cannot parse workflow {workflow_path}: {exc}"]
    inc = wf.get("jobs", {}).get("test", {}).get("strategy", {}).get("matrix", {}).get("include", [])
    for row in inc:
        files = str(row.get("files", ""))
        # #1472 regression fix: the matrix rows consume the SPACE-JOINED
        # matrix_a/matrix_b outputs directly (fromJSON(...) yields a JS array
        # that renders as "Array" in the shell `for f in ${{ matrix.files }}`
        # loop — every full-matrix run collected tests/Array.py).
        if not files.startswith("${{ needs.changes.outputs.matrix_"):
            issues.append(f"matrix row {row.get('half')} files is not derived (matrix_* output)")
    if "ENV_BROKEN_FILES" in wf.get("env", {}):
        issues.append("workflow env re-defines ENV_BROKEN_FILES (single source is ci_selection.py)")
    return issues


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
    full-matrix coverage hole. Slow files, bench/*, and the epic #1647
    carve-out set (their leg is `carve_out`) are excluded. Kept as
    a warning (not fail-closed): closing it would push 100+ files into the
    fast gate and blow the watchdog budget (see the scoping doc).
    """
    slow = set(manifest.get("slow_files", []))
    carve_out = carve_out_files(manifest)
    fast = set()
    for fs in manifest["surfaces"].values():
        fast.update(fs)
    fast -= slow
    fast -= carve_out
    halfset = {f for fs in halves.values() for f in fs}
    return sorted(f for f in fast if f[:-3] not in halfset)


def split_fast_gate(files, durations: dict, default_weight: float = 2.0):
    """#1473: LPT greedy pack of the selected fast-gate files across halves
    a/b by measured duration — deterministic (ties -> a; assignment order).
    Raises ValueError on non-list input (guards the 'ALL' full-mode string).
    """
    if not isinstance(files, list):
        raise ValueError(f"split_fast_gate expects a list, got {type(files).__name__}")
    weighted = []
    for f in files:
        name = f[len("tests/"):] if f.startswith("tests/") else f
        weighted.append((name, durations.get(name, default_weight)))
    a, b = [], []
    ta = tb = 0.0
    for name, w in sorted(weighted, key=lambda x: (-x[1], x[0])):
        if ta <= tb:
            a.append("tests/" + name)
            ta += w
        else:
            b.append("tests/" + name)
            tb += w
    return a, b


def duration_issues(manifest: dict) -> list[str]:
    """#1473: every durations key must be classified and non-slow."""
    issues = []
    durations = manifest.get("durations", {})
    slow = set(manifest.get("slow_files", []))
    classified = set()
    for s, files in manifest["surfaces"].items():  # noqa: B007
        classified.update(files)
    classified.update(manifest.get("tier1", []))
    for name in durations:
        if name in slow:
            issues.append(f"durations key {name} is a slow file (must be fast-gate)")
        if name not in classified:
            issues.append(f"durations key {name} is not classified in the manifest")
    return issues


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
    ap.add_argument("--emit-push-matrix", action="store_true",
                    help="#1472: print the derived push halves {half_a, half_b}")
    ap.add_argument("--split", action="store_true",
                    help="#1473: LPT-pack the stdin JSON test-file list into {a, b}")
    args = ap.parse_args()

    manifest = load_manifest()

    if args.integrity:
        missing = integrity(manifest)
        problems = missing + slow_file_issues(manifest) \
            + leg_coverage_issues(manifest) + duration_issues(manifest)
        # #1472: the matrix rows must come from the selector derivation
        # (space-joined matrix_* outputs) — when they do, the #1266
        # halves-parse tie check is
        # subsumed (the derivation guarantees no slow leaks / dupes / dead
        # files by construction). Legacy hardcoded halves keep the #1266 check.
        wf_issues = workflow_matrix_issues(
            REPO / ".github" / "workflows" / "python-ci.yml", manifest)
        problems += wf_issues
        if not wf_issues:
            # derived rows: feed the derived halves so the #1266 tie checks
            # still run against the ACTUAL execution split.
            legs = push_legs(manifest)
            halves = {"a": set(legs["half_a"]), "b": set(legs["half_b"])}
            problems += workflow_halves_issues(manifest, halves)
        else:
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

    if args.emit_push_matrix:
        legs = push_legs(manifest)
        print(json.dumps({"half_a": legs["half_a"], "half_b": legs["half_b"],
                          "carve_out": legs["carve_out"]}))
        return 0

    if args.split:
        # #1492: an empty stdin (the split step's output expression resolving
        # to "") must degrade to an empty selection — NOT crash --split for
        # every tier-2 PR (json.loads('') raises).
        raw = sys.stdin.read().strip()
        files = json.loads(raw) if raw else []
        a, b = split_fast_gate(files, manifest.get("durations", {}))
        result = {"a": a, "b": b}
        out_dir = Path(os.environ.get("CI_SELECTION_ARTIFACT_DIR", REPO / ".ci-selection"))
        out_dir.mkdir(exist_ok=True)
        (out_dir / "split.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        return 0

    changed = [l.strip() for l in args.changed_files.splitlines() if l.strip()]  # noqa: E741
    if args.changed_files == "-":
        changed = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]  # noqa: E741
    result = select(changed, args.event, manifest)

    # Audit artifact (scope v5 decision 1): replayable selection record.
    artifact = {
        "pr_sha": os.environ.get("GITHUB_SHA", ""),
        "selection_fn_version": SELECTION_FN_VERSION,
        "changed_files": changed,
        "surfaces": result["surfaces"],
        "full": result["full"],
        "selected_tests": result["test_files"],
        # #2147/#2148: the slow/carve-out diff-gate decisions ride the
        # artifact so the nightly recall audit can replay what ran.
        "slow_run": result["slow_run"],
        "slow_selected": result["slow_selected"],
        "carve_out_run": result["carve_out_run"],
    }
    out_dir = Path(os.environ.get("CI_SELECTION_ARTIFACT_DIR", REPO / ".ci-selection"))
    out_dir.mkdir(exist_ok=True)
    (out_dir / "selection.json").write_text(json.dumps(artifact, indent=2))

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
