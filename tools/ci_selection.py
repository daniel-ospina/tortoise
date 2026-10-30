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
test-slow should run — surface-matched on tier-2 PRs, the whole GATING leg set
= slow_gating_files() on full selections), carve_out_run (the
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
  — EXCEPT a root-level file in ROOT_NON_PYTHON_FILES (non-python-relevant by
  explicit name, #6784) or a prose file in NON_CODE_DOC_PATHS (#8785: a doc
  with no guard READER — a prose edit cannot affect the suite, so it takes the
  tier-1 smoke path exactly as `docs/` already does). There is deliberately NO
  blanket `*.md` clause and no `.github/` prefix clause: `.github/workflows/`
  YAML, `Makefile`, `pyproject.toml`, `uv.lock` and any new/unknown path still
  fail closed to FULL.
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

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level
# 3.11+-only import (`from datetime import UTC`) would fail first (D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/ci_selection.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/ci_selection.py`"
    )

import argparse
import ast
import json
import math
import os
import re
from pathlib import Path

# 1.4.0 (#6135): the fast pool is split into N shards (config `fast_shards`)
# instead of two hard-coded halves, and the push matrix is emitted as the whole
# GHA matrix object (`fast_matrix`).
SELECTION_FN_VERSION = "1.5.0"

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "config" / "ci-surfaces.yml"
TESTS_DIR = REPO / "tests"
WORKFLOW = REPO / ".github" / "workflows" / "python-ci.yml"

# #1266/#6135: EVERY fast shard must stay count-balanced within this delta of
# the others — the labels are positional letters (a, b, c, …), so this is no
# longer just the (a)/(b) pair. A tilt beyond it means someone added files to
# one shard without rebalancing (the exact drift that pushed shard (a) over the
# watchdog cap).
# #3400: this is now the FALLBACK invariant, used only when the manifest
# carries no `durations` map at all. Once measured durations exist the
# balance invariant is DURATION (below) — LPT packs by weight, and a correct
# pack can legitimately carry very different file counts: a few multi-minute
# files on one side against the long tail of sub-second ones on the other.
HALF_IMBALANCE_TOLERANCE = 3

# #3400: with measured durations, the halves must stay DURATION-balanced
# within this ratio. Index parity on the same pool leaves a tilt far above it —
# which files land on even vs odd indices has nothing to do with what they cost
# — while the LPT pack balances the same pool. 1.25 is loose enough for noise
# and tight enough that a reversion to parity reds. (Do not restate either
# figure here: both move whenever the pool does.)
HALF_DURATION_IMBALANCE_RATIO = 1.25

# #1473: weight for a fast file with no measured duration. The pack can only
# be as good as its weights, hence the coverage guard (#3400).
DEFAULT_FAST_WEIGHT = 2.0

# #3400: `durations` rotted to 15 entries for 500 fast files (97% packed at
# the flat default), which silently degenerated the duration-aware pack into
# a count-based one. Floor the coverage so it cannot rot back. The check is
# skipped entirely for an ABSENT/EMPTY map (a repo that has not adopted
# durations is not failed) and bites once the map is populated. Do not restate
# the pool size or the current percentage here — the durations map's own header
# carries the sweep that measures them, and a figure copied into this comment is
# what went stale before (it read "520 files / 96.5%" while the pool had grown).
DURATION_COVERAGE_MIN = 0.90

# #6135: the fast pool's SHARD COUNT. This is the one number that decides the
# PR gate's critical path, because the critical path is `max(shard)`.
#
# It is a REVIEWED LITERAL, read from `config/ci-surfaces.yml::fast_shards`,
# and deliberately NOT derived from the durations map per diff. A per-diff
# derivation would make the leg-name set (`test (a)`, `test (b)`, …) drift from
# PR to PR, and the merge rail's lane-parity (`admin-merge.sh`, keyed on job
# NAMES) plus every branch-protection/queue reference is a REVIEWED contract —
# a set that changes without review is exactly the hazard this number's own
# issue (`#6135`) names. Changing it is a one-line, reviewable config edit.
#
# Absence of the key keeps the historical S=2 — a repo that has not adopted the
# shard count is never failed by it.
DEFAULT_FAST_SHARDS = 2
# Labels are positional letters (`a`, `b`, …). The rail's lane-parity subtracts
# the PR's job names from main's, so an S-adding diff is comparable ONLY if the
# first two labels stay `a` and `b` — a PR whose set is a SUPERSET of main's
# holds, a rename makes both of main's names missing and refuses every merge.
# 26 = a..z is therefore also the hard ceiling on S.
MAX_FAST_SHARDS = 26

# W37: the CARVE-OUT pool's shard count (`config/ci-surfaces.yml::carve_shards`).
#
# WHY IT EXISTS. `test-carve-out` was ONE unsharded job and WAS the CI critical
# path until W37 sharded it: measured over 23 PR runs and 3 successful main runs
# (2026-09-30), its work was min 16.0m / p50 16.9m against a median run wall of
# 18.6m — 91% of the wall. Meanwhile the fast band is already split nine ways,
# so the wall was `max(9 fast shards, 1 carve-out)`, and the carve-out was the
# pole in EVERY run examined (`test (b)`, which the objective names, ends at
# +10.5m; it was never the pole). Sharding it converts that `max` into a
# comparable term.
#
# ABSENCE KEEPS THE CURRENT SHAPE, and this is the one place this key differs
# from `fast_shards`. There, absence means the historical S=2; here the
# historical shape is ONE job, so absence means 1 and an unadopted repo is
# byte-identical to today. A single shard is therefore legal (unlike
# `fast_shards >= 2`) — removing the key must always be a safe rollback.
DEFAULT_CARVE_SHARDS = 1
# Same ceiling as the fast band, for the same reason: labels are positional
# letters, and the rail's lane-parity subtracts main's names from the PR's.
MAX_CARVE_SHARDS = 26

# #9539: the SLOW pool's leg count (`config/ci-surfaces.yml::slow_shards`).
#
# `test-slow` is the merge ceiling, and the reason is arithmetic rather than a
# bad split: the pool is ~37.9m of measured test time (the legs' own junit on
# 5 main runs, 2026-10-21) and the legs run ONE serial pytest process each, so
# the best 2 legs can do is pool/2 = ~18.9m plus ~2m of setup. The committed
# split was already within noise of that optimum (per-file medians pack to
# 18.52/18.52; observed medians 18.25/18.80 across runs), so rebalancing cannot
# move it — only the LEG COUNT can, which is why this key exists.
#
# ABSENCE KEEPS THE HISTORICAL SHAPE (2 legs), exactly like `fast_shards`:
# deleting the key gives the leg count back, but it is NOT by itself a complete
# rollback — the wall guard recomputes from the map (2 legs project ~20.9m
# against `SLOW_LEG_WALL_TARGET_MIN`), so removing the key reds
# `test_slow_shards_are_duration_packed_and_meet_the_wall_target` as well. A real
# rollback moves the target with it.
# 1 is NOT legal here: an unsharded slow lane is 37.9m + setup, and a silent
# drop to 1 would be a ceiling regression nothing else here would name.
DEFAULT_SLOW_SHARDS = 2
MAX_SLOW_SHARDS = 26

# #9539: the wall the slow lane is held to, and the fixed cost a leg pays before
# its first test. Both are MEASURED constants, not hopes:
#   * `SLOW_LEG_WALL_TARGET_MIN` is the house CI guideline ("<10 min of CI" is a
#     guideline, not an absolute target — the owner's #6792 form ruling, quoted
#     in `docs/ONTOLOGY.md` §4.1), and it is `test-slow`'s share of it.
#   * `SLOW_LEG_SETUP_ALLOWANCE_MIN` is the non-pytest part of a leg measured
#     from the jobs API over 5 main runs: job wall − pytest step = 1.9–2.1 min
#     (checkout 0.12, install-cpu-torch 0.38–0.47, `pip install -e .[test,
#     embeddings]` 0.57–0.68, FalkorDB provision 0.15–0.27, coverage manifest
#     0.28–0.47). Not a guess and not zero: a shard count computed without it
#     would report a wall the job cannot hit.
# These two are what turn "more shards" from a preference into a derivation, and
# `slow_shard_entries()`/`slow_wall_projection()` are the only readers.
SLOW_LEG_WALL_TARGET_MIN = 10.0
SLOW_LEG_SETUP_ALLOWANCE_MIN = 2.0

# #6135: the per-shard WATCHDOG budget. The watchdog is PER-LEG, so a smaller
# shard needs a smaller budget — inheriting the old 55m means a hung 6.6-minute
# shard is detected 8× later than it should be, holding the REQUIRED aggregate
# (`python-ci-gate`) red and blocking every merge while it spins.
#
# The rule is the empirically-validated headroom, not a fresh guess: the 30m
# budget was hit by a ~30m shard (factor ~1.0 too tight — #798), and 55m for a
# ~30m shard (factor 1.8) has held since. 2.0 is that factor with a small
# margin. The FLOOR is 2.9× the largest single fast file, so the watchdog can
# never kill a shard below the #6133 floor.
#
# ⛔ THE FLOOR MOVES WITH THE MEASUREMENT, not with a feeling (#10026 fallout).
# It was 15m = 2.9× the then-largest fast file (310.7 s), and by the 2026-10-26
# sweep the committed map's largest was 355.0 s (`test_lane_stash.py`) — 2.54×,
# i.e. the pin was passing on 0.04 of margin. That sweep then MEASURED
# `test_calibration.py` at 390.0 s (it had carried the flat 2.0 s default, so no
# sweep had ever weighed it), which puts 15m at 2.31× and reds
# `test_watchdog_is_per_shard_and_scales_with_the_shard`. 19m = ceil(390.0 ×
# 2.9 / 60) restores the stated derivation (1140 s / 390.0 s = 2.92×).
# ⚠️ The alternative — moving that file to `slow_files` — is a SELECTIVITY
# decision (a change to it would stop running the fast shards), so it is left to
# its owner rather than taken here; the floor is the mechanical fix that keeps
# the derivation true whichever way that lands.
#
# ⛔ 1.8 IS NO LONGER ACCEPTED (#6145). The CEILING can clamp a budget BELOW
# `WATCHDOG_HEADROOM`, and `watchdog_headroom_issues` now REDS that state —
# wired into `--integrity`, so the REQUIRED `manifest-integrity` check refuses
# it. Lowering S back toward 2 is therefore an integrity FAILURE, not the
# ceiling "keeping the validated 55m"; the remedy is a DELIBERATE ceiling raise
# or a re-split (see the OVERRIDES line on #6145). The ceiling deliberately
# stays 55m so the worst-case leg stays inside the job's outer
# `timeout-minutes` — the invariant REFUSES rather than widening it.
WATCHDOG_HEADROOM = 2.0
WATCHDOG_FLOOR_MIN = 19
WATCHDOG_CEILING_MIN = 55

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
    # #4097: cross-cutting leaf — the declared env-truthiness contract. Consumed by
    # `core` (why, rerank, monitoring, model_adapters, frontmatter_validator,
    # extractor_v2, backup_config, embedded_lifecycle, cimd, projection), `api`
    # (hosted_api), `sdk` (sdk, retrieval) and `eval` (embeddings) — the `ep` and
    # `onboarding` surfaces list the guard for coverage reasons, not because they
    # import the leaf. A change to `env_flag`'s blank/garbage handling changes
    # consumer behaviour on every surface, so it runs the full matrix (same
    # rationale as exceptions.py above).
    "tortoise/env_truthy.py",
    "tortoise/tool_registry.py",
    "tortoise/mcp_server.py",
    # ⚠️ #4713: naming the package's entry point rather than the whole
    # `tortoise/projection/` directory is a deliberate coverage REDUCTION. A
    # submodule edit now runs only the surfaces registered for it below, plus the
    # `core` pins that exercise it — the sdk/api suites wait for the next main
    # push. Take the `edges.py` treatment below as the model if that is wrong.
    "tortoise/projection/__init__.py",
    "tests/conftest.py",
    "tests/fake_control_plane.py",
    # #6673: the docker-lane port/host seam, imported by 50+ test modules that
    # are registered across every docker-lane surface (core, ep, sdk, api,
    # eval) — `git grep -l _live_utils -- tests/` lists every file that touches
    # it (the importers, plus a couple of mention-only references), and the count
    # is deliberately not restated here because it drifts. A seam edit is
    # therefore cross-surface by construction: the `_embedded.py` /
    # `_tmpdir_hygiene.py` case below, at a larger blast radius, because the
    # seam is now how the whole docker lane resolves its port. Without this
    # entry a seam-only edit selects `core` and the other surfaces' consumers
    # never run on the PR that made the edit.
    "tests/_live_utils.py",
    # #4069: suite-wide test helpers re-exported by `tests/conftest.py`. Both are
    # imported at conftest MODULE level and hand their fixtures to every surface's
    # tests, and neither is a `test_*.py` file, so the manifest never classifies
    # them: without these entries a change to one selected `core` only, and a break
    # it induced in an api/eval/onboarding/ep/battery test never ran on the PR that
    # made it (#1349/#3332/#3910). The `tests.*` half of that rule is enforced by
    # `tests/test_ci_selection.py::test_every_conftest_module_level_tests_import_is_shared`.
    "tests/_tmpdir_hygiene.py",
    "tests/_embedded.py",
    # #5049: the verdict contract (`tests/_verdict.py`) is imported at conftest
    # MODULE level and hands the suite-wide per-test process-global reset to
    # every surface's tests. It is not a `test_*.py` file, so the manifest never
    # classifies it; without this entry a change to the contract would select
    # `core` only and a break it induced in an api/eval/ep test would never run
    # on the PR that made it (the #1349/#3332/#3910 under-selection class).
    "tests/_verdict.py",
    # #7655: the SIGALRM integrity guard (`tests/_signal_hygiene.py`) is imported
    # at conftest MODULE level and installs the suite-wide autouse fixture that
    # refuses an in-process `signal.alarm` / `setitimer(ITIMER_REAL)` /
    # `signal.signal(SIGALRM, …)` while the harness owns the per-test timer.
    # Same class as `_verdict.py` above: not a `test_*.py` file, so the manifest
    # never classifies it — without this entry a change to the guard would
    # select `core` only, and a break it induced in an api/eval/ep test would
    # never run on the PR that made it (#1349/#3332/#3910 under-selection class).
    "tests/_signal_hygiene.py",
    # #7357: the canonical-store guard (`tests/_canonical_store_guard.py`) is
    # imported at conftest MODULE level and REFUSES THE SESSION for any surface
    # whose `TORTOISE_DB_URI` targets the fleet's map instance (loopback
    # port 16379). Same class as `_verdict.py` / `_signal_hygiene.py` above and
    # enforced by
    # `tests/test_ci_selection.py::test_every_conftest_module_level_tests_import_is_shared`:
    # not a `test_*.py` file, so the manifest never classifies it — without this
    # entry a change to the guard would select `core` only, and a break it
    # induced in an api/eval/ep session would never run on the PR that made it
    # (#1349/#3332/#3910 under-selection class).
    "tests/_canonical_store_guard.py",
    # #9208: the degraded-change-feed attribution decision
    # (`tests/_change_feed_isolation.py`) is imported by `tests/conftest.py`'s
    # autouse `_change_feed_state_isolation` fixture, so it runs in EVERY test's
    # teardown on EVERY surface (api, ep, sdk, onboarding, eval, battery) — a
    # raise in `leaked_state_message` FAILS every one of them. Same class as
    # `_verdict.py` / `_signal_hygiene.py` / `_canonical_store_guard.py` above:
    # not a `test_*.py` file, so the manifest never classifies it — without this
    # entry a change to it would select `core` only, and a break it induced in
    # an api/eval/ep test would never run on the PR that made it
    # (#1349/#3332/#3910 under-selection class). The function-local import in
    # that fixture is deliberate (a broken helper must never stop the suite
    # importing), so the AST ratchet
    # (`tests/test_ci_selection.py::test_every_conftest_module_level_tests_import_is_shared`)
    # cannot see it and this registration has to be made by hand.
    "tests/_change_feed_isolation.py",
    # this PR (#1436 / #5084 remainder): the in-process live-skip gate
    # (`tests/_live_gate.py`) is imported at conftest MODULE level and is applied
    # from `pytest_runtest_setup` — i.e. it runs before EVERY test on EVERY
    # surface (api, ep, sdk, onboarding, eval, battery), not just `core`. Same
    # class as `_verdict.py` / `_signal_hygiene.py` / `_canonical_store_guard.py` /
    # `_change_feed_isolation.py` above, and enforced by the same AST ratchet
    # (`tests/test_ci_selection.py::test_every_conftest_module_level_tests_import_is_shared`):
    # it is not a `test_*.py` file, so the manifest never classifies it — without
    # this entry a change to the gate would select `core` only and an api/eval/ep
    # break it induced would never run on the PR that made it
    # (#1349/#3332/#3910 under-selection class). The gate's own blast radius is
    # the whole suite: it can RED any test that skips on a live-storage reason.
    "tests/_live_gate.py",
    "pyproject.toml",
    "requirements.txt",
    ".github/workflows/python-ci.yml",
    ".github/actions/",
)

# Source-path patterns that trigger each surface (surface -> test files come
# from the manifest).
SOURCE_PATTERNS = {
    "battery": ("battery/",),
    "onboarding": ("tortoise/onboarding/",
                   # #4054: the auth surface moved to the app project — the BFF's
                   # pages now live in the dashboard's `public/` tree (vite copies
                   # them to `dist/`, the deployed root). `website/signin.html` was
                   # deleted outright (it 301'd to /auth and was dead).
                   "website/apps/dashboard/public/welcome.html",
                   "website/apps/dashboard/public/signup.html",
                   "website/self-hosted.html", "website/product.html",
                   # #2409: the public contact form's page. Registered here for the
                   # same reason as the rest of this tuple — a PR touching only
                   # this page must still select the guard that holds it.
                   "website/contact.html",
                   "website/index.html",
                   "website/privacy.html",
                   # #3559: the shared cross-subdomain bridge
                   # (`website/assets/supabase-session.js`) was DELETED with its
                   # entry here. Its guard
                   # (`test_cross_subdomain_cookie_sync.py`) now proves its
                   # ABSENCE, and the surviving subjects that guard holds
                   # (`signup.html`, `main.jsx`) are already listed in this
                   # surface.
                   # #3332: the public pages that own a guard test in this surface.
                   # docs.html + faq.html -> test_website_docs_consistency.py;
                   # product.html + welcome.html -> test_website_static.py;
                   # index.html + privacy.html -> test_waitlist_form.py;
                   # signup.html + signin.html -> test_signup_form_safety.py;
                   # self-hosted.html -> test_harness_mcp_config.py.
                   # Listing a path is what makes a change to it select this
                   # surface at all — otherwise its guard test never runs.
                   "website/docs.html", "website/faq.html",
                   # #3673: the served skill documents and the installer belong to
                   # this surface. The parity contract between
                   # `tortoise/onboarding/SKILL.md` and its served mirror is
                   # asserted by test_onboarding_variants.py, and the installer's
                   # `SKILLS=(...)` is what the dashboard's shipped-set claim is
                   # pinned against (test_installer_preserves_foreign_skill_
                   # content.py, dual-registered onto this surface below).
                   #
                   # Before these entries a change to the SERVED copy alone matched
                   # no pattern: `surfaces=[]` -> the parity gate ran only via the
                   # tier-1 fallback (coverage by accident, not by design, and it
                   # would vanish the moment the test left `tier1`). Worse, the
                   # installer ran NO guard at all — its guard is on `core` and is
                   # not in `tier1` — the #1349/#3332/#3616 silent-drop class.
                   "website/apps/dashboard/public/skills/",
                   "website/apps/dashboard/public/install-tortoise-skills.sh",
                   # #4643: the served-skill mirror AND its gate. `tools/` is in
                   # NON_PYTHON_PREFIXES, so without this entry a
                   # `served_skills_parity.py`-only change (which is exactly how
                   # the gate gets weakened) selects no surface of its own: it is
                   # dropped before the SOURCE_PATTERNS pass and the diff falls to
                   # the docs-only tier-1 early return. That fallback DOES carry
                   # `test_served_skills_parity.py` (it is in `tier1`), so the gate
                   # still ran — by accident, not by design, and it would vanish
                   # the moment the test left `tier1`. What this entry adds is the
                   # `onboarding` surface's own file set, so an edit to the gate is
                   # graded by the surface that owns the mirror. Same
                   # #1349/#2938/#3332 silent-drop class as the served-skill entries
                   # above. The pin
                   # (`config/served-skills-upstream.yml`) needs no entry here:
                   # config/ falls through to `core`, where the gate is also
                   # registered.
                   "tools/served_skills_parity.py",
                   # #3517: the capture disclosure's single source of truth and
                   # the non-LLM renderer. Both own guards registered below
                   # (`test_capture_disclosure_template.py`,
                   # `test_onboarding_session_recording_parity.py`) — the
                   # cross-surface "one home" scan reads the JSON, and the
                   # parity test reads the CLI the SKILL names. Without these
                   # entries an edit to either file matched no pattern and fell
                   # back to `core`, where neither guard is registered: the
                   # guard would not run on the PR that can break it (the same
                   # #3673 silent-drop class as the served-skill entries above).
                   "tortoise/capture_disclosure.py",
                   "tortoise/config/capture_disclosure.json",
                   # #3517: the false-promise grep's file set INCLUDES the 1714
                   # research briefs (the plan's own requirement — the withdrawn
                   # 403 consent contract still reads as live in this one).
                   # `docs/` is in NON_PYTHON_PREFIXES, so without this entry a
                   # docs-only PR that re-adds the dead contract selects NO
                   # surface and the gate never runs on the only change that can
                   # break it. Named as the CONCRETE file, not a glob: `select()`
                   # matches with `startswith` and has no glob support, so a
                   # `docs/research/1714-` prefix would satisfy nothing while
                   # reading as coverage (`test_source_patterns_all_name_
                   # something_real` reds on it — that is how dead entries are
                   # caught). `test_capture_disclosure_template.py::test_every_
                   # hit_carrying_brief_is_selected_by_ci` keeps this list honest
                   # as new briefs land.
                   "docs/research/1714-solution-converge.md",
                   # #3952: the blog-admin console SPA's build config and its
                   # committed build snapshot own the guard tests added in
                   # tests/test_admin_return_to.py (the build base, and
                   # document-independent resolution of the shell's asset refs).
                   # Neither path is under a Python package prefix, so without
                   # these entries a PR that reverts `base: '/admin/'` to the
                   # relative form selects NO surface (surfaces=[], full=False)
                   # and the guard never runs on the PR that owns it — the same
                   # #3616 pattern these entries sit next to, one level up.
                   "website/apps/blog-admin/vite.config.ts",
                   # The guard reads the WHOLE website tree (`WEBSITE.rglob("*")`)
                   # and the committed blog-admin artifact. Registering only
                   # dist/index.html (and then dist/assets/) left the guard unrun
                   # for a change to any OTHER file it reads — three review rounds
                   # each found another instance of that same silent drop.
                   #
                   # NOT a wholesale `website/`: `test_unrelated_website_change_
                   # stays_tier1` pins that a website path owning no guard test
                   # keeps tier-1 behavior. So each path this guard actually reads
                   # is named — the granularity the carve-out design requires.
                   "website/apps/blog-admin/",
                   "website/apps/dashboard/index.html",
                   "website/apps/dashboard/vite.config.js",
                   "website/apps/dashboard/eslint.config.js",
                   "website/apps/dashboard/scripts/copy-shared-assets.mjs",
                   "website/apps/dashboard/scripts/gen-wizard-prompts-snapshot.mjs",
                   "website/apps/dashboard/scripts/gen-wizard-rendered-copy-snapshot.mjs",
                   # File granularity, NOT `website/blog/` or `website/functions/`:
                   # test_website_docs_consistency pins that `website/blog/index.html`
                   # is NOT guard-reachable, so the directory would over-select.
                   "website/blog/blog.js",
                   "website/consent.js",
                   "website/functions/_middleware.ts",
                   "website/functions/_shared/contact-transport.ts",
                   "website/functions/_shared/security-headers.ts",
                   "website/functions/blog/_lib.ts",
                   "website/functions/blog/feed.xml.ts",
                   "website/functions/blog/sitemap.xml.ts",
                   "website/functions/blog/_shared/cloudflare-purge.ts",
                   "website/functions/blog/_shared/meta-contract.ts",
                   "website/functions/blog/_shared/seo-constraints.ts",
                   "website/functions/blog/_shared/seo-keywords.ts",
                   # #8293: the keyword split moved the DATA to `keywords.ts` and
                   # the shared agent-key check + limiter to `agent-auth.ts`.
                   # `agent-auth.ts` is now the single copy of a security
                   # primitive that `posts/[[path]].ts` executes and that
                   # `test_blog_agent_delete_guard.py` drives; `keywords.ts` is
                   # the file a keyword-research pass edits. Without these
                   # entries a lone edit to either selects NO surface
                   # (surfaces=[], full=False) and the guard never runs on the
                   # change it exists to catch — the #1349/#3332 silent-drop
                   # class the sibling entries above close. Named as individual
                   # files, not a glob: `select()` matches with `startswith`.
                   "website/functions/blog/_shared/agent-auth.ts",
                   "website/functions/blog/_shared/keywords.ts",
                   "website/functions/blog/_shared/slug.ts",
                   # #9680: the IndexNow submitter and the key file it points at.
                   # The RULE under test (`shouldSubmit`'s 24h window, the
                   # fail-soft call, the state read/write) lives ONLY in
                   # `indexnow.ts`, and a lone edit to it — a window tweak, a
                   # retry, a rotation of the key — selects NO surface
                   # (surfaces=[], full=False) without this entry, so
                   # `test_blog_indexnow.py` would never run on the change it
                   # exists to catch: the #1349/#3332/#3616 silent-drop class the
                   # entries above close. The key file is its own entry because
                   # the endpoint verifies ownership by FETCHING it: an edit to
                   # its body is exactly what V1 tests. ROTATION NOTE: rotating
                   # the key renames this file, so the new name must be added
                   # here (and in config/pages-upload-classification.txt) in the
                   # same commit, or the guard goes quiet on the new name.
                   "website/functions/blog/_shared/indexnow.ts",
                   "website/a7c41f0e9b2d63584e1c0af7d3b925e6.txt",
                   "website/functions/contact/submit.ts",
                   "website/website_architecture.md",
                   "docs/auth-architecture.md",
                   # #3329: `test_capacity_page.py` pins the public capacity page and
                   # derives its write-concurrency figure from
                   # `tests/test_embedded_concurrency.py`'s own coverage. `docs/` is in
                   # NON_PYTHON_PREFIXES and is not itself a pattern, so without this
                   # CONCRETE entry a `docs/capacity.md`-only edit selects NO surface and
                   # the guard is asleep on the one change it exists to catch (exactly
                   # the requirement #3329 states: every published number stays bound to
                   # its source).
                   "docs/capacity.md",
                   # #3329: the same guard pins the write-concurrency figure in the
                   # self-hosted quickstart (and `.env.example`, which falls through to a
                   # FULL selection). `docs/` is in NON_PYTHON_PREFIXES, so a
                   # quickstart-only diff would otherwise select NOTHING and leave the
                   # figure unpinned in the file a self-hoster actually reads.
                   "docs/quickstart-selfhosted.md",
                   # #3329: the guard DERIVES the published write-concurrency figure
                   # from this test file's own loop bound, so a change to it can
                   # invalidate the prose the guard pins. Without this entry a
                   # `tests/test_embedded_concurrency.py`-only diff matched only
                   # the `tests/` -> `core` fallback and did NOT select
                   # `test_capacity_page.py` — a PR that bumped the coverage to
                   # six writers and left the docs stale merged green, and the
                   # drift surfaced only on the post-merge `push` full run (the
                   # #3442 shape). Same #1349/#3332/#3616 class: the derived
                   # source needs the same reachability guarantee as the docs it
                   # feeds (#8492).
                   "tests/test_embedded_concurrency.py",
                   # #3524: `test_oauth_consent_bridge.py` also pins the two auth
                   # records its subject is specified in — the consent bridge spec
                   # (`docs/engineering/auth/consent-identity-bridge.md`) and the
                   # redirect-allowlist record (`docs/ops/auth-email-flows.md`).
                   # `docs/` is in NON_PYTHON_PREFIXES, so without these entries a
                   # docs-only edit to either record selects NO surface and the
                   # guard never runs on the change it exists to catch (the
                   # #1349/#3332 silent-drop class, one level up — see
                   # `docs/auth-architecture.md` above and `test_ci_selection.py::
                   # test_the_consent_bridge_guard_runs_on_its_docs_records_too`).
                   # Named as CONCRETE files, not globs: `select()` matches with
                   # `startswith` and has no glob support.
                   "docs/engineering/auth/consent-identity-bridge.md",
                   "docs/ops/auth-email-flows.md",
                   # #8334 review (P2, Finding 1): `test_csp_plan_doc_consistency.py`
                   # guards the CSP plan doc's copyable policy blocks. `docs/` is in
                   # NON_PYTHON_PREFIXES and is not itself a pattern, so without this
                   # CONCRETE entry (the `docs/auth-architecture.md` shape above) a
                   # docs-only edit to the plan doc selects NO surface and the guard never
                   # runs on the only change that can break it — which is exactly what the
                   # registration comment in `config/ci-surfaces.yml` claimed it did.
                   "docs/plans/2026-09-22-3525-csp-no-store.md",
                   # #9121 review (P1): `tests/test_9121_onboarding_residual_pins.py`
                   # pins the §17.1 escape-(3) residual in TWO records beyond the
                   # two code modules it already reached — the increment-4 tracker
                   # and §17.1's own door ledger. Reviewer-measured: both were
                   # WITHOUT the residual, and an auditor reading either concludes
                   # the onboarding class has no second writer, which the five
                   # `eager_init_query()` sites used to disprove (`#9157`; they
                   # now ride `run_eager_init` and the pin asserts that route in
                   # the AST for all five). `docs/` is in
                   # NON_PYTHON_PREFIXES and is not itself a pattern, so without
                   # these CONCRETE entries a docs-only edit to either record
                   # selects NO surface and the pin never runs on the only change
                   # that can break it — the #1349/#3332 silent-drop class, one
                   # level up. Named as CONCRETE files, not globs: `select()`
                   # matches with `startswith` and has no glob support.
                   "docs/plans/2026-10-14-7951-atomic-write-outbox.md",
                   "docs/architecture/STORAGE-ARCHITECTURE.md",
                   # #8379 (finding 2): `test_blog_authoring_doc.py` renders the
                   # blog authoring contract with a real CommonMark parser. `docs/` is
                   # in NON_PYTHON_PREFIXES and is not itself a pattern, so without this
                   # CONCRETE entry a docs-only edit to the contract selects NO surface
                   # and the guard never runs on the only change that can break it —
                   # the same silent-drop class as the two docs records above.
                   "docs/blog-authoring.md",
                   # The guards read the moved Functions themselves — and not only
                   # the gate: `test_admin_return_to.py` reads the gate by exact
                   # path and derives the console's mount path from its directory,
                   # `test_pages_bindings.py` rglob-scans the ENTIRE tree for env
                   # reads (`_env_names_read_by_the_bff`), and
                   # `test_website_docs_consistency.py` resolves routes from both
                   # function roots. The directory is therefore the correct
                   # granularity — not the two files that happened to break.
                   #
                   # #4171: this block previously named
                   # `website/functions/admin/[[path]].ts`, which the admin-origin
                   # move DELETED. An entry is matched by `startswith`, never
                   # against the filesystem, so the dead path stayed "alive": a PR
                   # touching the moved gate selected NO surface and
                   # `test_admin_return_to.py` silently stopped guarding the file it
                   # was written for (#1349/#3332 class, one level up — the ratchet
                   # caught the reverse direction only). Naming single files also
                   # left every OTHER moved Function unselectable: `auth/signup.ts`,
                   # `_shared/auth/csrf.ts`, `api/session.ts` and the rest all
                   # selected surfaces=[] — so a change adding an env read shipped
                   # green with the binding guard never running. The directory
                   # closes both holes. `test_source_patterns_all_name_something_real`
                   # (tests/test_ci_selection.py) now fails on a dead entry.
                   "website/apps/dashboard/functions/",
                   # The SPA files the migrated-surface invariant reads by exact
                   # path (`test_no_legacy_token_path.py` -> MIGRATED_SURFACES)
                   # and that `test_admin_return_to.py` opens by name. Each of
                   # these selected surfaces=[] before this commit, so the
                   # (`website/apps/dashboard/src/main.jsx` is covered by the
                   # `website/apps/dashboard/src/` directory entry below.)
                   # legacy-token invariant could not fail on the very files it
                   # exists to guard — including `main.jsx`, which THIS PR
                   # rewrites (logout teardown, CSRF content-type). Same
                   # #1349/#3332 class as the entry above; both are now covered by
                   # `test_source_patterns_all_name_something_real`.
                   "website/apps/blog-admin/src/lib/blog-api.ts",
                   "website/apps/blog-admin/src/hooks/useAuth.ts",
                   # #4171/#4178: guarded files that must select their guard.
                   # `blog/_shared/admin-auth.ts` is read by
                   # `test_no_legacy_token_path.py`'s store-fault-vs-signed-out
                   # semantics guard, and `backend.ts` (which replaced the
                   # DELETED `blog-admin/src/lib/supabase.ts`) is read by
                   # `test_cross_subdomain_cookie_sync.py` and
                   # `test_session_bridge_fragment_retention.py`. Both are
                   # non-tier-1 `onboarding` guards, so editing these files would
                   # otherwise ship green with their guard never running — the
                   # same #1349/#3332 class as the sibling entries above.
                   "website/apps/blog-admin/src/lib/backend.ts",
                   "website/functions/blog/_shared/admin-auth.ts",
                   # #3523: the dashboard's unknown-address guard
                   # (tests/test_dashboard_unknown_address.py) reads the Pages
                   # routing inputs for app.premiselabs.co plus the app's
                   # location.pathname branches. `website/` sits in
                   # NON_PYTHON_PREFIXES and neither path is under a Python
                   # package prefix, so without these entries a PR that deletes
                   # 404.html, adds a `/* / 200` catch-all, or adds an unrouted
                   # pathname branch selects NO surface (surfaces=[], full=False)
                   # and the guard never runs on the PR that owns it — the
                   # #1349/#3332/#3616 silent-drop class, which registration
                   # alone does not fix (registration only makes the file
                   # CLASSIFIED; selection is what makes it RUN). `src/` is a
                   # directory because the anti-drift check scans every non-test
                   # source module for pathname branches, not just main.jsx.
                   "website/apps/dashboard/public/_redirects",
                   "website/apps/dashboard/public/404.html",
                   "website/apps/dashboard/src/",
                   # #3048: `functions/assets/[[path]].ts` (the missing-asset 404)
                   # is already covered by the `website/apps/dashboard/functions/`
                   # directory entry above, so no entry is added for it here.
                   # #4006 review: the guard's SERVER_BUILT_ROUTES (/team carrying
                   # the Stripe ?session_id= return) are BUILT here, so a change to
                   # the server-side return path must run the guard too — otherwise
                   # public/_redirects goes stale against it and the guard stays
                   # green: the same silent-drop class this entry exists to close.
                   "tortoise/hosted_api.py",
                   # #3950: the blog-discoverability guard
                   # (test_website_docs_consistency.py
                   # ::test_every_in_scope_page_links_to_the_blog) covers EVERY
                   # public+indexable+served page, not just docs/faq — the count
                   # is deliberately not stated, because each add or retirement
                   # would re-stale the number. An unlisted
                   # page means a PR touching ONLY that page selects no surface
                   # (surfaces=[], full=False) and the guard never runs — verified
                   # before listing: `--changed-files website/tos.html` yielded
                   # surfaces=[] while website/product.html yielded ['onboarding'].
                   # That is the #1349/#3332 silent-drop class one more time: the
                   # guard silently stops covering the page it was written for.
                   # The ratchet is now two-directional, so adding a guarded page
                   # without listing it here FAILS a test instead of silently
                   # shrinking coverage: `test_every_source_pattern_is_selectable`
                   # (tests/test_ci_selection.py) checks entry -> runs, and
                   # `test_every_in_scope_page_is_selectable_by_ci`
                   # (tests/test_website_docs_consistency.py) checks the reverse —
                   # that every page in the blog guard's derived set reaches a
                   # surface through this tuple.
                   "website/security.html", "website/tos.html",
                   "website/license.html", "website/dpa.html",
                   # #3436: the remaining top-level page, listed for the
                   # same reason as every other page entry in this tuple — they
                   # are covered by the site-wide element-id uniqueness guard
                   # (tests/test_website_docs_consistency.py), whose scope is
                   # DERIVED as `website/*.html`. Their absence was verified
                   # before listing: `select(["website/invite-accept.html"])`
                   # returned surfaces=[] with the guard absent from
                   # test_files, so a duplicate id could land on the invite
                   # landing page without the guard running on the PR that
                   # added it. Both are `noindex` pages, which keeps them out of
                   # the blog guard's scope, not out of this one: a noindex page
                   # is still a served document, and duplicate ids are invalid in
                   # it.
                   # #4054: `invite-accept.html` moved to the app project with the
                   # rest of the auth pages, so its entry follows it —
                   # `website/invite-accept.html` no longer exists, and the stale
                   # entry was caught by `test_source_patterns_all_name_something_real`
                   # (tests/test_ci_selection.py) the moment this branch merged main.
                   "website/404.html",
                   "website/apps/dashboard/public/invite-accept.html",
                   # The design-partner one-pager. `noindex`, so it is out of the
                   # #3950 blog guard's scope — but NOT out of the element-id
                   # uniqueness guard, whose scope is every served document: a
                   # noindex page is still a page whose own script runs, and
                   # duplicate ids are invalid in it. Without this entry a PR
                   # touching only this page selects no surface and the id guard
                   # never executes on it.
                   "website/design-partners-intro.html",
                   # The investor one-pager (#8568). Same reasoning as the
                   # design-partner entry directly above: `noindex`, so it is out
                   # of the #3950 blog guard's scope — but NOT out of the
                   # element-id uniqueness guard, whose scope is every served
                   # document. Without this entry a PR touching only this page
                   # selects no surface and the id guard never executes on it.
                   "website/investors.html",
                   # The shared href extractor both blog-guard layers call
                   # (tests/test_website_docs_consistency.py here, and
                   # tests/e2e/test_legal_pages.py in the separate `legal-e2e`
                   # job, which CI runs on every PR regardless of selection).
                   # Without this entry, editing ONLY the extractor selects core
                   # and does NOT run the static guard — so the file implementing
                   # the guard's rule could be changed without running
                   # `test_rendered_hrefs_ignores_non_rendered_markup`, the test
                   # that pins that rule. Same #1349/#3332/#3616 class as the
                   # pages above, one level up: the helper needs the same
                   # reachability guarantee as the pages it serves.
                   # Deliberately NARROW rather than promoted to SHARED_MODULES:
                   # this file has a single matrix consumer, and the full matrix
                   # would buy nothing the E2E consumer is not already given.
                   "tests/_html_links.py",
                   # #3950 review: two more inputs the guard DERIVES its scope from,
                   # so each changes guard coverage without changing a page.
                   # `website/_redirects` is what `_canonical_redirect_targets()`
                   # reads to drop redirected pages from scope, and
                   # `website/functions/blog/[[path]].ts` is the Function
                   # `_function_serves()` resolves the blog link against. A
                   # routing-only PR could therefore silently shrink or break the
                   # guard while selecting NO surface (surfaces=[], full=False) —
                   # the same #1349/#3332/#3616 silent-drop class, applied to the
                   # derivation's own inputs rather than to its output.
                   "website/_redirects",
                   "website/functions/blog/[[path]].ts",
                   # #4316: the agent API Function (create / edit / DELETE) owns
                   # the guard in tests/test_blog_agent_delete_guard.py, which
                   # executes it under Node. Without this entry a PR touching
                   # ONLY the DELETE guard (posts/[[path]].ts) selects NO surface
                   # (surfaces=[], full=False) and the guard never runs on the
                   # very PR that owns it — the #1349/#3332/#3616 silent-drop
                   # class the entries above exist to close, one level down.
                   # Directory granularity, deliberately: the sibling Functions
                   # (generate-seo/-cover, purge) share the API's auth + env
                   # reads, and naming single files is what left every other
                   # moved Function unselectable in #4171.
                   "website/functions/blog/api/",
                   # #3616: the deploy-binding gate is a PAIR — the checker and
                   # the manifest it reads. Neither path is under a Python
                   # package prefix, so without these two entries a PR that
                   # edits the gate's logic or downgrades a binding to
                   # `recommended` selects NO surface (surfaces=[], full=False)
                   # and test_pages_bindings.py never runs on the PR that owns
                   # it. That is the #3616 pattern one level up: the thing that
                   # decides whether the gate works would not itself be gated.
                   "tools/check_pages_bindings.py",
                   "config/required-bindings.yml",
                   # #3806: the ship-test instrument and its guard. The guard test
                   # (tests/test_ship_test_onboarding.py) is registered in BOTH
                   # `core` (its generic probe helpers) and `onboarding` (the
                   # onboarding surface it measures). Without this entry a change
                   # to the instrument alone selects NO surface (`tools/` is a
                   # flat NON_PYTHON_PREFIXES entry, and the docs-only return
                   # bypasses the `core` fallback) so its guard never runs on the
                   # PR that edits it — the #3261/#3616/#3910 silent-drop class.
                   "tools/ship_test_onboarding.py",
                   # #3620: the Pages UPLOAD-ROOT gate is a pair too — the
                   # preflight checker and the reviewed classification it reads.
                   # A PR that adds a top-level entry under website/ (or edits
                   # the checker) must select this surface, or the ratchet that
                   # classifies the new entry never runs on the PR that owns it.
                   "tools/check_pages_upload_root.py",
                   # #4311: the strict tsc project + pinned toolchain that
                   # typecheck website/functions/** live in tools/ts/ (outside the
                   # Pages upload root — see tools/ts/README.md). `tools/` is a
                   # flat NON_PYTHON_PREFIXES entry, so without this an edit to
                   # the project (removing `strict`, dropping a dependency)
                   # selects NO surface (`surfaces=[]`, `full=False`) and its guard
                   # never runs on the change that disarms it — the
                   # #3261/#3616/#8334 silent-drop class.
                   "tools/ts/",
                   "config/pages-upload-classification.txt",
                   # #6870 review (P1): the mono-stack guard
                   # (`tests/test_website_static.py::
                   # TestMonospaceStackRendersAsAMonoFace`) widened its scan to
                   # `apps/` and to the Python modules that emit HTML, but this
                   # surface is the guard's ONLY registration and neither root
                   # reached it. Measured before these entries:
                   # `select(["apps/graph-viz/src/index.css"])` -> `surfaces=[]`
                   # (a graph-viz-only diff filters to `changed == []` and takes
                   # the docs-only tier-1 early return, because `apps/` is in
                   # NON_PYTHON_PREFIXES and matched no SOURCE_PATTERNS row), and
                   # `select(["tortoise/oauth.py"])` -> `['api', 'core']` with the
                   # guard absent. So a re-copy of the old Courier-rendering stack
                   # into one of the 8 graph-viz canvas/JSX declarations or into
                   # the served MCP consent page merged GREEN — the
                   # #1349/#3332/#3616 silent-drop class, on the exact surfaces
                   # the guard was widened to protect. `tortoise/oauth.py` is in
                   # CORE_ALSO, so `core` stays selected alongside. Concrete
                   # paths, not globs: `select()` matches with `startswith`.
                   # Pinned by tests/test_website_static.py::
                   # TestMonospaceStackRendersAsAMonoFace::
                   # test_every_file_it_scans_selects_this_guard (derived from the
                   # files the scan reads) and by tests/test_ci_selection.py::
                   # test_the_monospace_guard_is_selected_on_every_root_it_scans.
                   "apps/graph-viz/src/",
                   "tortoise/oauth.py",
                   # #10254 review (P2): the ELEMENT-level guard
                   # (`tests/test_website_static.py::
                   # TestARenderedCodeElementGetsAMonoFace`) declares `apps/` in its
                   # `ROOTS`, and `apps/graph-viz/index.html` is the one subject in
                   # that root the guard can DECIDE on that NO leg ran. Two files
                   # under `apps/graph-viz/` carry their own `<style>` block at this
                   # head — this one and `src/components/ontology/HierarchyChart.tsx:645`
                   # — and the second already selects this surface through the
                   # `apps/graph-viz/src/` row above, so it needs no entry of its own
                   # (#10254 review: the earlier wording called this page "the only
                   # subject the guard can DECIDE on", which the measurement
                   # contradicts). It renders no `<code>`/
                   # `<pre>` today, which is precisely why it needs naming: the
                   # moment someone re-copies a Courier-rendering snippet into that
                   # page, the guard would flag it and no leg would run it.
                   # MEASURED before this entry: `select(['apps/graph-viz/index.html'])
                   # -> surfaces=[], full=False`, `test_website_static.py` ABSENT (a
                   # graph-viz-page-only diff filters to `changed == []` — `apps/` is
                   # in NON_PYTHON_PREFIXES and matched no row — and takes the
                   # docs-only tier-1 early return). Of the 21 decidable subjects the
                   # guard scans at this head, this was the ONLY unwired one; the
                   # other 20 already select this surface. Concrete path, not the
                   # `apps/graph-viz/` directory: the same reasoning as the
                   # `package.json` entry above — a wholesale claim would pull the
                   # whole `core` set onto every source edit in that app.
                   # Pinned by tests/test_website_static.py::
                   # TestARenderedCodeElementGetsAMonoFace::
                   # test_every_page_it_can_decide_on_selects_this_guard.
                   "apps/graph-viz/index.html",
                   # The guard's other declared emitter (`MODULES`). It carries no
                   # mono declaration today, so nothing forces its wiring — which
                   # is exactly why it is named here: the guard's subject list
                   # must not be able to grow a member whose diff never runs it.
                   # Paired with `CORE_ALSO` below because a SOURCE_PATTERNS match
                   # REPLACES the `core` fallback, and `test_render.py` (the
                   # renderer's own guard) is registered `core`.
                   "tortoise/render.py",
                   # PR #9652 review (P2): `tests/test_website_static.py`'s new
                   # `TestProductRootMarkerContract` has THREE subjects, and only
                   # two of them selected this surface — `website/product.html`
                   # (a row above) and `deploy-pages.yml` (deliberately unlisted,
                   # so it takes the fail-closed FULL matrix). The third is
                   # `tests/e2e/test_legal_pages.py`, which holds the
                   # `PRODUCT_ROOT_MARKER` constant the guard parses: `tests/`
                   # matched no SOURCE_PATTERNS row, so it fell back to `core` and
                   # `test_website_static.py` — this surface only — never ran.
                   # MEASURED before this entry: `--changed-files
                   # tests/e2e/test_legal_pages.py --event pull_request` ->
                   # surfaces=["core"], test_website_static.py ABSENT. So re-cutting
                   # the marker by editing only the file the contract is named
                   # after re-created the silent-green this PR fixes, with the
                   # guard asleep — the #1349/#3332/#3616 silent-drop class, one
                   # level down from where #9652 closed it. Same shape as
                   # `tests/_html_links.py` above (the file implementing a guard's
                   # rule needs the guard's reachability guarantee). Paired with a
                   # CORE_ALSO entry below, because a named-surface match REPLACES
                   # the `tests/` -> `core` fallback and would otherwise trade one
                   # silent drop for another. Pinned by
                   # tests/test_ci_selection.py::
                   # test_product_root_marker_guard_runs_on_a_legal_pages_only_diff.
                   "tests/e2e/test_legal_pages.py",
                   # #9607: `tortoise/quadrature.py` is an input this guard
                   # DERIVES from — `test_docs_states_the_formulas_quadrature_
                   # actually_computes` reads both factors out of the module and
                   # asserts /docs states them, and its own docstring claims
                   # *"editing `quadrature.py` itself reds the page that describes
                   # it"*. That was FALSE in CI: `quadrature.py` matched no
                   # `SOURCE_PATTERNS` entry and is not in `SHARED_MODULES`, so a
                   # quadrature-only diff took the generic `core` fallback
                   # (`surfaces=['core']`, `full=False`) and landed in a pool this
                   # file is not registered in — so a QUADRATURE-ONLY diff could not
                   # run the formula pin (a PR that also touched a `docs`/website
                   # page still did, via `onboarding`). Measured on `02e18e752`
                   # before listing: `select(["tortoise/quadrature.py"])` returned
                   # `full=False`, `surfaces=['core']`, this file ABSENT from
                   # `test_files`. Same #1349/#3332/#3616 silent-drop class as the
                   # derivation inputs directly above (`website/_redirects`,
                   # `tests/_html_links.py`), and the reverse ratchet in
                   # `tests/test_website_docs_consistency.py::
                   # test_every_guard_input_is_selectable_by_ci` pins it so the
                   # entry cannot be dropped again.
                   "tortoise/quadrature.py",
                   # #9607 review P2 (round 5): the SIBLING instance of the entry
                   # above, one file over. `test_website_docs_consistency.py`
                   # imports `Point` / `point_content_id` from this module and its
                   # `test_commit_schema_requires_a_source_on_every_extracted_claim`
                   # is the evidence under the FAQ's PUBLISHED traceability
                   # sentence — so relaxing `Point.source_ref` is the one change
                   # that can falsify what `website/faq.html` ships. Measured
                   # before this entry: `select(["tortoise/commit_schema.py"])`
                   # returned `full=False`, `surfaces=['core']`, this file ABSENT
                   # from `test_files` — the same `core` fallback as quadrature,
                   # so the change that reds the contract tests could edit them and
                   # still never run the pin on the page. Paired with a `CORE_ALSO`
                   # entry below (a named-surface match REPLACES the `core`
                   # fallback, #2938) and pinned by
                   # tests/test_ci_selection.py::
                   # test_commit_schema_change_keeps_the_core_pool_and_runs_the_docs_guard.
                   "tortoise/commit_schema.py"),
    # NOTE: .github/workflows/deploy-pages.yml is deliberately NOT listed above.
    # A review pointed out that adding it would be a coverage DOWNGRADE: an
    # unlisted path falls into the unknown-path branch -> FULL matrix (fail
    # closed), whereas listing it selects only `onboarding`. Today the two tests
    # that read that workflow both live in onboarding, so nothing is lost — but
    # a future core-registered test reading it would silently stop running on
    # the PR that edits it. Fail-closed is the right default for the file that
    # owns the deploy.
    #
    # #8816 makes that dependency sharper: `tests/test_e2e_wrangler_prewarm.py`
    # is registered as CORE and now reads deploy-pages.yml (the pin and the
    # verify-before-upload shape). It is safe ONLY because the file stays
    # unlisted here — an unlisted deploy-pages.yml edit takes the FULL matrix,
    # which includes core. Listing it would drop that guard's coverage on the
    # very PR that changes the deploy spec.
    "ep": ("tortoise/dream.py", "tortoise/analyze.py",
           "tortoise/ranking.py",
           # #2786 review P2: the extraction probe and the calibration harness
           # are the two TOOLING copies of the core object-kind vocabulary (the
           # PR makes both DERIVE from `pack_registry.CANONICAL_OBJECT_KINDS`),
           # and `tests/test_calibration_tools.py` — registered `ep` — imports
           # BOTH at module level, so a tool-only edit is precisely the change
           # that can break it. `tools/` is a flat NON_PYTHON_PREFIXES entry and
           # neither file had a SOURCE_PATTERNS row, so such an edit selected NO
           # surface and fell back to tier-1 smoke, where that guard is not
           # registered (the #1349/#3332/#3616 silent-drop class). A
           # SOURCE_PATTERNS match REPLACES the `core` fallback, which is why
           # both are paired with CORE_ALSO below (their other guard,
           # `tests/test_kind_vocabulary_consistency.py`, is `core`).
           "tools/calibration_harness.py", "tools/probe_extractor.py"),
    "sdk": ("tortoise/ids.py", "tortoise/models.py", "tortoise/crypto.py",
            "tortoise/reader.py", "tortoise/retrieval.py",
            # #3849: the ask PIPELINE now lives here (moved out of
            # tortoise/sdk.py, which is a shared module -> FULL matrix). It
            # is the only home of run_ask_lane/run_ask_assembled, so an
            # ask_lane-only change must select sdk — otherwise the lane's
            # own suites (test_ask_sdk / test_assembly_sdk /
            # test_ask_regression_llm / test_d3_session_identity) silently
            # stop running (fallback to core ran NO ask tests).
            "tortoise/ask_lane.py",
            # ask-lane shared vocabulary/gating: a PR touching ONLY these
            # must select sdk so test_ask_sdk.py (+ ask reader/calibration
            # pins) run — the old fallback to core ran NO ask tests.
            # exceptions.py is SHARED (cross-cutting leaf -> full matrix);
            # transport.py is dual-wired with api: its only direct unit test
            # is test_metering.py::test_selfhost_transport_exemption.
            "tortoise/schemas.py", "tortoise/transport.py",
            # #2071: the spot-check tools are the eval-lane ask QA — a
            # spot-check-only PR selects the sdk surface (its tests live
            # there: test_ask_spotcheck_judge.py).
            "tools/ask_spotcheck.py", "tools/ask_spotcheck_consistency.py",
            "tools/ask_spotcheck_probe.py",
            # #3910: the ask-lane recall bench is the same QA family —
            # tests/test_ask_retrieval_levers.py pins the `_retrieve_pipeline`
            # it mirrors, so a bench-only PR must select `sdk` rather than
            # drop to tier-1 smoke with that guard test never running.
            # Refs #2089, whose criterion 1 this entry satisfies.
            "tools/ask_recall_bench.py",
            # #3914: gen_ask_transcripts.py OWNS the seeder whose shape the
            # committed transcript goldens and tests/test_ask_seed_shape.py
            # pin. Before this entry the flat "tools/" prefix swallowed the
            # path, so a seeder-only PR selected NO surface (surfaces=[],
            # tier-1 smoke only) and both guards ran nowhere — the same
            # #1349/#3332/#3910 silent-drop class, on the file that
            # manufactures the graph those guards read.
            "tools/gen_ask_transcripts.py",
            # B6 objective 4: the answer-shape instrument is the ask-lane's
            # shape measurement (it drives sdk.ask, build_reader_model and
            # the shipping tortoise_search/tortoise_recall handlers to
            # compute shape_rate). Same family and same silent-drop trap as
            # the entries above: the flat "tools/" prefix would swallow a
            # shape-rate-only PR into tier-1 smoke and the ask-lane tests
            # would not run where the measurement changed.
            "tools/ask_shape_rate.py",
            "tortoise/projection/edges.py"),
    "api": ("tortoise/hosted_api.py", "tortoise/hosted_backup.py",
            "tortoise/acl_graph_users.py", "tortoise/__main__.py", "tortoise/mcp_auth.py",
            # #3154: hosted_api.py imports hosted_backup.py at module level (the
            # backup/restore/import endpoints), and the boolean-index audit lives
            # there — without this entry a hosted_backup.py-only change matched
            # no pattern and fell through to `core`, skipping the api-registered
            # tests that pin it (test_graphcopy_boolean_index_3154.py,
            # test_hosted_backup.py, test_dr_endpoints.py). Paired with
            # CORE_ALSO: many core-registered tests (test_backup_sweep.py,
            # test_backup_multigraph_e2e.py, test_backup_watcher.py,
            # test_alert_store.py) also pin it.
            # #4367: email_notify.py OWNS `_build_invite_link` (and the invite
            # sender itself). `tests/test_email_integration_resend.py` —
            # registered in `api` — is the guard that pins the invite-link
            # contract and replays the recorded accept-page cassette, and
            # `test_email_notify.py` (also `api`) pins the sender. Without this
            # entry an email_notify.py-only change matched no pattern, fell
            # through to `core`, and NEITHER guard ran on the PR that can break
            # them — the regression was caught only post-merge, on push to
            # main. Same silent-drop shape as the #2938/#3154 entries above.
            "tortoise/email_notify.py",
            "tortoise/quota.py", "tortoise/supabase_control.py",
            "tortoise/selfhost_api.py", "tortoise/session_auth.py",
            # ask-lane server surfaces: test_metering.py + test_selfhost_rest.py
            # live in the api surface — a metering.py/selfhost.py-only PR must
            # select api (core runs no ask tests). transport.py is dual-wired
            # here (in addition to sdk): its only direct unit test is
            # test_metering.py::test_selfhost_transport_exemption.
            "tortoise/metering.py", "tortoise/selfhost.py",
            # #4489: the retrieval meter. Its writer/reader live in metering.py
            # (already above) but the module itself is the read-funnel wrapper
            # and the per-leg hooks' caller, and its guard
            # (tests/test_retrieval_metering.py) is registered in `api`. Without
            # this entry a retrieval_metering.py-only diff matched no pattern,
            # fell through to `core`, and skipped the guard that pins it — the
            # #7076 self-guard class the test below refuses.
            # #8544: the self-host off-box backup DRIVER. Its guard
            # (tests/test_selfhost_backup.py) is registered in `api`, and it is
            # armed from `tortoise/selfhost.py` (already an entry above) — but a
            # selfhost_backup.py-only diff would otherwise match no pattern and
            # fall through to `core`, skipping the guard that pins it (the
            # #2938/#3154/#4367 silent-drop class).
            "tortoise/selfhost_backup.py",
            "tortoise/retrieval_metering.py",
            "tortoise/transport.py",
            # #2938: EventAPI — the append surface `ingest.py`, `mining.py`,
            # `extractor.py`, `m0.py` and `__main__.py` all write through.
            # Without this entry a `tortoise/api.py`-only change matched no
            # pattern, fell through to `core`, and skipped the api-registered
            # tests that pin it (test_api.py, test_attribution_actor.py,
            # test_1162_add_operator_local_svbp.py). Paired with CORE_ALSO:
            # its pinning tests are registered across api, core AND ep, so the
            # named-surface match must not drop `core` (see CORE_ALSO).
            "tortoise/api.py",
            # #3036: tortoise/oauth.py is the hosted OAuth implementation, and
            # its pinning tests are `api`-registered (test_oauth_mcp.py,
            # test_oauth_token_fault.py, test_3036_oauth_retention.py,
            # test_attribution_actor.py, test_user_identity_authority.py) plus
            # `api`+`core` (test_control_plane_offload_3498.py). Without this
            # entry an oauth.py-only change selected no named surface and fell
            # through to `core`, silently skipping ALL of those — the
            # #2938/#3154/#4367 silent-drop class, on the file a retention- or
            # token-flow fix must change. Paired with CORE_ALSO so the
            # core-registered half is not dropped by the named-surface match.
            "tortoise/oauth.py",
            # #3524 review (P1): the consent identity bridge's VERIFIER,
            # `tortoise/consent_token.py`. Its guard
            # (`tests/test_oauth_consent_bridge.py`) is registered `api` +
            # `onboarding` (config/ci-surfaces.yml) and NOT `core`, and a
            # named-surface match REPLACES the `core` fallback — so without this
            # entry a diff that touches the verifier and nothing else selected
            # `core`, where that guard does not exist. MEASURED before this
            # entry: `--changed-files tortoise/consent_token.py --event
            # pull_request` -> surfaces ['core'], 682 test files,
            # test_oauth_consent_bridge.py ABSENT (with the test file also in the
            # diff: surfaces ['api', 'core'], guard present). A one-line fix or a
            # revert of the signature/`alg`/`bind`/`jti` logic — the security
            # half of the bridge — therefore red nowhere, and would have merged
            # green, caught only by a later PR that happened to touch
            # `hosted_api.py`. Same silent-drop class as the #2938/#3154/#4367
            # entries above. No CORE_ALSO entry: the module has no
            # core-registered guard. Pinned by tests/test_ci_selection.py::
            # test_a_consent_token_only_diff_runs_the_bridge_guard.
            "tortoise/consent_token.py",
            # #3496: the consent page pins its browser auth client to a CDN
            # specifier whose version must equal the VENDORED bundle the
            # behavioural harness executes (test_oauth_consent_pkce.py, api).
            # `website/` is in NON_PYTHON_PREFIXES, so a vendor-only bump matched
            # no pattern and fell through to tier-1 smoke — the version pin
            # would never run on the PR that can break it, and neither would the
            # harness that executes the very file being bumped (the
            # #1349/#3332/#4171 silent-drop class). `_selection_relevant()` lets
            # a SOURCE_PATTERNS match beat the prefix filter, so this entry is
            # what makes the bump select `api`.
            "website/apps/dashboard/public/vendor/",
            # #4282: `tools/bridge_table.py` GENERATES `docs/product/bridge-table.md`
            # and `test_bridge_table.py` (registered in `api`) is the drift gate
            # that keeps them honest. `tools/` is in NON_PYTHON_PREFIXES, so a
            # generator-only edit selected NO surface (`surfaces: []`, `full:
            # false`) and the gate never ran on precisely the PR that can break
            # it. Named here because a SOURCE_PATTERNS match beats the
            # non-python skip. A docs-only hand-edit of the generated file still
            # skips THIS matrix by the repo's deliberate docs-PR policy; that
            # direction is covered instead by the required `docs` job, which runs
            # `tools/bridge_table.py --check` on every PR (#4454).
            "tools/bridge_table.py",
            # #4282 Phase 0.3: `tools/mcp_rename_table.py` GENERATES
            # `docs/product/mcp-rename-table.md` and `test_mcp_rename_table.py`
            # (registered in `api` + `core`) is the drift gate. Same shape as the
            # 0.1 entry directly above and the same reason: a generator-only edit
            # is swallowed by the flat `tools/` prefix and the gate never runs on
            # the PR that can break it (a docs-only hand-edit is covered by the
            # required `docs` job's `--check` instead — #4454).
            "tools/mcp_rename_table.py",
            # #4282 Phase 0.3b: `tools/sdk_rename_table.py` GENERATES
            # `docs/product/sdk-rename-table.md`, and `test_sdk_rename_table.py`
            # (registered in `api` AND `core`) is the drift gate. Same gap as the
            # bridge table above: `tools/` is in NON_PYTHON_PREFIXES, so a
            # generator-only edit selected NO surface and the gate never ran on
            # the PR that can break it. The generated doc is NOT committed (#5373:
            # gitignored, generated on demand), so no hand-edited copy can appear
            # in a PR for the docs-PR policy to skip.
            "tools/sdk_rename_table.py",
            # #4282 Phase 0.4 + 1.1: `tools/sdk_surface.py` derives the declared
            # `TortoiseSDK` public surface and GENERATES `config/sdk-surface.json` +
            # `docs/product/sdk-surface-declaration.md`; `test_sdk_surface.py`
            # (registered in `api` AND `core`) is the drift gate. Same gap as the bridge
            # table above: `tools/` is in NON_PYTHON_PREFIXES, so a generator-only edit
            # selected NO surface and the gate never ran on the PR that can break it.
            # A docs-only hand-edit of the generated doc still skips THIS matrix by the
            # repo's deliberate docs-PR policy; the required `docs` job runs
            # `tools/sdk_surface.py --check` on every PR instead (#4454).
            "tools/sdk_surface.py",
            # #10044 review F1: `tools/sdk_surface_declaration.py` is the APPROVED-surface
            # gate (the owner's 40 in `tortoise/__all__.py` vs the owner's document, and
            # vs the live `TortoiseSDK`). Its proofs are the 17 in
            # `test_sdk_surface_declaration.py` — registered in `api` AND `core` but
            # unreachable, because `tools/` is in NON_PYTHON_PREFIXES: a gate-only diff
            # selected NO surface (`surfaces: []`, `full: false`) and the required job
            # then executed the PR's own edited gate with zero of its tests having run.
            # What that file pins is the GATE's contract (`check(contract_only=True)` reds a
            # doctored or padded declaration) — enough on its own: a neutered `check()`
            # cannot pass them. The WIRING proof (that the required lint still CALLS the
            # gate) lives in `tests/test_surface_manifest.py`, which is `core`-registered and
            # selected by the `tools/surface_manifest.py` diff that could break it — so this
            # entry does not carry it, and saying it did was wrong (round-6 F3).
            # Same silent-drop class (#1349/#3332/#3616) as the generators above, and the
            # one bypass the residual-bypass paragraph in `tools/surface_manifest.py`
            # does not cover. `test_sdk_surface_declaration.py` is already in `api`, so
            # this entry is what makes the registration bite.
            "tools/sdk_surface_declaration.py",
            # #5373: `tools/registry_integrity.py` is the fail-closed validator
            # paired with `merge=union` on the two config registries, and
            # `test_registry_integrity.py` (dual-registered in `api` AND `core`)
            # is its proof. Same gap as the generators above: `tools/` is in
            # NON_PYTHON_PREFIXES, so a validator-only edit selected NO surface
            # (`surfaces: []`, `full: false`) and the fail-closed proof never ran
            # on precisely the edit that can neuter it — the #1349/#3332/#3616
            # silent-drop class. The registry it guards is config/, not api-owned,
            # which is why the test is ALSO registered in `core`.
            "tools/registry_integrity.py"),
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
             # #3359: the per-session cost report CLI consumes the eval-owned
             # versioned PRICING_MAP (tools/longmem_eval/costing.py) and is
             # exercised by tests/test_capture_cost_measurement.py — without
             # this entry a report-CLI-only change selects NO surface
             # (surfaces=[], full=False) and that test never runs on the PR
             # that owns the launch-gate number (the #3616 pattern).
             "tools/capture_cost_report.py",
             # #9077 (E4): the operator-receipt EVIDENCE checker. `tools/` is in
             # NON_PYTHON_PREFIXES, so without this entry a change to the
             # checker itself selects no surface of its own and its regression
             # suite (`eval/write_path/test_write_path_operator_receipt.py`, 62
             # dict-fixture tests) never runs on the PR that weakens the gate —
             # the same #1349/#2938/#3616 silent-drop class as the entries
             # above. The suite is registered on `eval` below.
             "tools/operator_receipt_check.py",
             # P2-1 (code review): an embeddings.py/cross_lens.py-only PR must
             # select eval so probe/vector-arm/threshold tests run (they assert
             # the EMBEDDING_MODEL + threshold constants — drift class #1260).
             "tortoise/embeddings.py", "tortoise/cross_lens.py",
             # #7947 review P1: the write-path MEASUREMENT sources — the
             # corpus generator and its schema, the two-arm harness, and the
             # pre-registered scorer (`score_qualifiers.py`) — live under
             # `tests/eval/write_path/`, and none of them is named `test_*.py`,
             # so before this entry a `score_qualifiers.py`-only change matched
             # no pattern, fell to the `tests/` -> `core` fallback at
             # `select()`, and selected `core` — where NOT ONE of the tests that
             # pin the rule it edits is registered (`test_score_qualifiers.py`
             # and `test_write_path_corpus.py` are `eval`). A change to
             # `MARKERS`/`BAND_PP`/`classify()`/`evaluate_branches()` therefore
             # rewrote every number in §10 with every gate green, and a
             # hand-edited `gold/*.json` did not run the planted-count pin
             # either. Registering the test file alone (config/ci-surfaces.yml)
             # only fixes the manifest classification: selection is driven by
             # THIS table. Same #1349/#2938/#3616 silent-drop class as the
             # entries above. Pinned by
             # tests/test_ci_selection.py::test_write_path_measurement_source_edit_selects_eval.
             "tests/eval/write_path/",
             # #9097: same class, same fix — the SEALED ask exit benchmark's
             # measurement sources (`corpus.py` globs/hash/manifest,
             # `generate_corpus.py` renders the sealed gold) live under
             # `tests/eval/ask/` and are not named `test_*.py`. Without this
             # entry a `corpus.py`-only change fell to the `tests/` -> `core`
             # fallback and selected `core`, where NOT ONE of the tests that pin
             # the corpus contract (`eval/ask/test_ask_corpus.py`) or the exit
             # bar (`test_measure_ask_distilled.py`) is registered — so the hash
             # scope or a sealed-gold check could be rewritten with the gate
             # green. Pinned by
             # tests/test_ci_selection.py::test_ask_sealed_corpus_source_edit_selects_eval.
             "tests/eval/ask/"),
    # core is the fallback for any other python-relevant path
}

# #2938: a SOURCE_PATTERNS match REPLACES the `core` fallback in select() —
# the named surface's file list is more specific than the always-on engine
# set. That is wrong for a source whose pinning tests are registered across
# surfaces: `tortoise/api.py` (EventAPI) is imported at module level by 16
# `core`-registered tests (test_projection, test_extractor, test_m1/m2, the
# de2e* suite, …) plus the api-registered trio, and a named-surface match
# would run only the selected surface's half of them. A path listed here adds
# `core` alongside its matched surface(s) — narrower than promoting the whole
# module to SHARED_MODULES (which forces the full matrix).
# Paths listed here ADD `core` alongside whatever surface they matched.
#
# #4207/#4351: `tools/skip-guard.py` is the file the frozen-nodeid manifest is
# enforced by, and BOTH of its pinning tests (`tests/test_skip_guard.py`,
# `tests/test_ci_expected_manifests.py`) are `core`-registered. `tools/` IS in
# NON_PYTHON_PREFIXES (a tools-only change is treated as non-python-relevant), and
# the file matches no SOURCE_PATTERN either, so a follow-up change to
# `--manifest-only` alone selected only tier-1 smoke — the pin for the code being
# changed would not have run. That is the #1349/#3332/#3616 silent-drop class, on
# the file this PR modifies.
#
# #4069: the temp-dir sweep is test-infra whose guard tests
# (tests/test_tmpdir_sweep.py, tests/test_tmpdir_hygiene.py) are
# core-registered. `_selection_relevant()` consults CORE_ALSO, so
# this entry has a DUAL role: it admits a `tools/` path past the
# flat NON_PYTHON_PREFIXES filter AND, in the match loop below,
# adds `core` and marks the path found — so a tool-only change
# selects `core` instead of the unknown-path fail-closed full
# matrix, and never drops to tier-1 smoke (the #1349/#3332/#3910
# silent-drop class). No TOOL_CARVEOUTS entry is needed: that
# tuple is redundant for any path already listed here.
CORE_ALSO = ("tortoise/api.py", "tortoise/hosted_backup.py", "tools/skip-guard.py",
             "tortoise/projection/edges.py",
             # #9607: `tortoise/quadrature.py` is registered in `onboarding` (its
             # /docs formula guard lives there), and a SOURCE_PATTERNS match
             # REPLACES the `core` fallback (#2938) — so without this entry the
             # very PR that edits a formula would STOP running the whole core pool
             # it selects today (`select(["tortoise/quadrature.py"], ...)` collapses
             # to the `onboarding` set alone), i.e. the fix for one silent drop would
             # create another. The pool's size is a DATED ANCHOR, not a live count
             # (633 at `1ff23919c`; read today's with
             # `len(select(["tortoise/foo.py"], "pull_request", load_manifest())["test_files"])`)
             # — it grows with the suite, and a bare count rots.
             #   ⚠️ Deliberately NOT the whole fix: `tests/test_ep_quadrature.py`
             # is registered under `ep`, so a quadrature-only diff still drops it —
             # and so do the three other `ep`-registered suites that import the
             # module (`test_ep_calibration.py`, `test_ep_projections.py`,
             # `test_gmm_proofs.py`). That half is pre-existing and is tracked
             # separately (#9621) rather than folded in here.
             "tortoise/quadrature.py",
             # #9607 review P2 (round 5): `tortoise/commit_schema.py` is the other
             # `core`-fallback input now registered in `onboarding` — same #2938
             # trade, same remedy. Without this entry the very PR that relaxes
             # `Point.source_ref` stops running the core pool it selects today
             # (measured: `select(["tortoise/commit_schema.py"])` collapses to the
             # `onboarding` set alone), i.e. the fix for one silent drop would
             # create another. Pinned by
             # tests/test_ci_selection.py::
             # test_commit_schema_change_keeps_the_core_pool_and_runs_the_docs_guard.
             "tortoise/commit_schema.py",
             # #7947 review P1: `tests/eval/write_path/` is a SOURCE_PATTERNS
             # entry of `eval` (the measurement sources), and a named-surface
             # match REPLACES the `tests/` -> core fallback — but `core`
             # registers two tests that READ those sources:
             # `test_extractor_v2.py` imports `tests.eval.write_path.runner`,
             # and `test_salience_labels_4917.py` reads
             # `tests/eval/write_path/salience_labels.jsonl`. Without this
             # entry a write_path-only change would drop them from the
             # selection, trading one silent drop for another.
             "tests/eval/write_path/",
             "tools/tmpdir_sweep.py",
             # queue_resweep: `tools/` is in NON_PYTHON_PREFIXES, so a
             # tools/queue_resweep.py-only change was filtered out BEFORE the
             # "not matched -> core" fallback and took the docs-only early return —
             # `select(["tools/queue_resweep.py"])` yielded NO surface, so the guard
             # registered under `core` never ran on the file it guards (the
             # #1349/#3332/#3616 silent-drop class, and exactly the false "the core
             # fallback covers it" claim #3261 removed from this file). The CORE_ALSO
             # entry makes `_selection_relevant()` keep the path, so `core` (and this
             # tool's own guard) is selected. Pinned by
             # tests/test_ci_selection.py::test_queue_resweep_tool_change_selects_core_not_tier1.
             "tools/queue_resweep.py",
             # #6138 review P1: the queue-conflict census owns
             # tests/test_queue_conflict_census.py, which is `core`-registered,
             # but `tools/` is swallowed by NON_PYTHON_PREFIXES and no
             # SOURCE_PATTERNS entry matches the tool — so a census-only change
             # filtered to `changed == []`, took the docs-only early return, and
             # fell back to tier-1 smoke: the suite that pins the census never
             # ran on the PR that edits it (the #1349/#3332/#3616 silent-drop
             # class, and the same gap tools/drift-guard.py carries).
             "tools/queue_conflict_census.py",
             # #9231 review P1 (the #9188/#9078 wiring gate): tools/record_wiring_check.py
             # is the ONLY executor of the claim "the record has a production
             # caller" — its guard is
             # tests/test_9078_record_written_by_production_path.py, registered
             # `core`. But `tools/` is swallowed by NON_PYTHON_PREFIXES and no
             # SOURCE_PATTERNS entry matches the tool, so a wiring-check-only
             # change (i.e. the file whose whole purpose is to decide whether
             # the record is wired, and therefore the most likely file to be
             # weakened) filtered to `changed == []`, took the docs-only early
             # return and fell back to tier-1 smoke — where that test is NOT
             # registered, so the gate never ran on the PR that can disarm it.
             # Measured on the PR head:
             # `select(["tools/record_wiring_check.py"])` -> surfaces=[],
             # full=False, 51 tier-1 files, guard absent. Same
             # #1349/#3332/#3616/#4309 silent-drop class as the entries above.
             # Pinned by
             # tests/test_ci_selection.py::test_record_wiring_check_tool_change_selects_core_not_tier1.
             "tools/record_wiring_check.py",
             # #9790 review P1: tools/batch_review.py owns
             # tests/test_batch_review.py (registered `core`), but `tools/` is a
             # flat NON_PYTHON_PREFIXES entry and NO SOURCE_PATTERNS row matches
             # the tool — so a tool-only change filtered to `changed == []`,
             # took the docs-only early return and selected tier-1 smoke, where
             # the guard is NOT registered. Measured before this entry:
             # `select(["tools/batch_review.py"])` -> surfaces=[], full=False,
             # test_batch_review.py absent. That is the #1349/#3332/#3616
             # silent-drop class on the one diff that can break the guard: a
             # follow-up that deletes `plan_records`'s manifest-head equality
             # (`tools/batch_review.py::plan_records`) — the single line stopping a
             # drifted outcome from minting a record — would have merged with
             # the guard cases that pin it never running. The `core` row that
             # registers the file is CLASSIFICATION only (#4348); it does not
             # make a `tools/` path select `core`. CORE_ALSO (not
             # TOOL_CARVEOUTS) because the suite is hermetic and the FastAPI-free
             # core pool is the lowest-cost leg it can run in. Pinned by
             # tests/test_ci_selection.py::test_batch_review_tool_change_selects_core_not_tier1.
             "tools/batch_review.py",
             # #4174 review P1: the drift gate's suite (test_drift_guard.py) is
             # `core`-registered, but `tools/` is swallowed by
             # NON_PYTHON_PREFIXES and no SOURCE_PATTERNS entry matches
             # tools/drift-guard.py — so a guard-only fix selected NO surface,
             # dropped to tier-1 smoke, and never ran the tests that pin the
             # guard. Same silent-drop class as tools/tmpdir_sweep.py above,
             # and the same defect #4174 describes a gate having.
             "tools/drift-guard.py",
             # #5084 review P1: tools/test_lane.py owns
             # tests/test_test_lane_tool.py (registered `core`), but `tools/` is
             # swallowed by NON_PYTHON_PREFIXES and no SOURCE_PATTERNS entry
             # matches it — so a tool-only edit selected NO surface, took the
             # docs-only early return and fell back to tier-1 smoke: the tests
             # that pin the tool's fail-closed ownership guard (it must never
             # remove a shared instance, and must refuse a peer lane's
             # container) would not have run on the PR editing that guard.
             # Same #1349/#3332/#3616 silent-drop class as the entries above.
             # Pinned by
             # tests/test_ci_selection.py::test_test_lane_tool_change_selects_core_not_tier1.
             "tools/test_lane.py",
             # #7435: the docs-lint baseline differ owns
             # tests/test_docs_lint_baseline.py, which is `core`-registered, but
             # `tools/` is swallowed by NON_PYTHON_PREFIXES and no
             # SOURCE_PATTERNS entry matches the tool — so a differ-only change
             # selected NO surface and dropped to tier-1 smoke, and the 58 cases
             # covering the differ's fail-closed paths never ran on the very PR
             # that can make the differ fail OPEN. Same #1349/#3332/#3616
             # silent-drop class as tools/queue_resweep.py above. Pinned by
             # tests/test_ci_selection.py::test_docs_lint_baseline_tool_change_selects_core_not_tier1.
             "tools/docs_lint_baseline.py",
             # #9695 review P1: tools/ax_write_eval.py owns
             # tests/test_ax_write_eval.py (registered `core`), but `tools/` is
             # swallowed by NON_PYTHON_PREFIXES and no SOURCE_PATTERNS entry
             # matches it — so a tool-only edit selected NO surface, took the
             # docs-only early return and fell back to tier-1 smoke, where the
             # file pinning the instrument's P0/P1 rules is NOT registered. The
             # eval's whole acceptance is "the delta, per input" read off this
             # instrument, so weakening a rule had to be the one diff that ran
             # its pins. Same #1349/#3332/#3616 silent-drop class as the entries
             # above; MEASURED before this entry: `--changed-files
             # tools/ax_write_eval.py` -> surfaces=[], full=False,
             # test_ax_write_eval.py ABSENT. Pinned by
             # tests/test_ci_selection.py::test_ax_write_eval_tool_change_selects_core_not_tier1.
             "tools/ax_write_eval.py",
             # #9943: tools/inbound_relay_sweep.py owns
             # tests/test_inbound_relay_sweep.py (registered `core`), and the
             # same silent drop bit it: `tools/` is swallowed by
             # NON_PYTHON_PREFIXES and no SOURCE_PATTERNS row matches the tool,
             # so a TOOL-ONLY change selected NO surface, took the docs-only
             # early return and landed on tier-1 smoke, where that file is not
             # registered — the very PR that can weaken the sweep's rails would
             # be the one that never ran the tests pinning them, and the
             # 15-minute cron would then close contributors' items with the rail
             # removed. MEASURED before this entry: `--changed-files
             # tools/inbound_relay_sweep.py --event pull_request` ->
             # surfaces=[], full=False, n=64, test_inbound_relay_sweep.py ABSENT;
             # after -> surfaces=['core'], n=669, present. ⚠️ n is a SNAPSHOT taken
             # at the head that measured it: it moves with `main`'s surface
             # registrations (it has read 663 and 667 at earlier heads), so treat
             # the two LOAD-BEARING halves — `surfaces=[], ABSENT` before vs
             # `surfaces=['core'], present` after — as the claim, and re-measure
             # the count rather than trusting it. Same
             # #1349/#3332/#3616 class as the entries above. Pinned by
             # tests/test_ci_selection.py::
             # test_inbound_relay_sweep_tool_change_selects_core_not_tier1.
             "tools/inbound_relay_sweep.py",
             # #10220: tools/worktree_report.py owns tests/test_worktree_report.py
             # (registered `core`), and the same silent drop bit it: `tools/` is
             # swallowed by NON_PYTHON_PREFIXES and no SOURCE_PATTERNS row matches
             # the tool, so a TOOL-ONLY change selected NO surface, took the
             # docs-only early return and landed on tier-1 smoke, where that file
             # is not registered. The very PR that can weaken the report's
             # anti-fabrication rules (re-hash the log, re-derive the counts, drop a
             # rule) would then be the one that never ran the cases pinning them.
             # MEASURED before this entry: `select(["tools/worktree_report.py"],
             # "pull_request", load_manifest())` -> surfaces=[], full=False, n=64,
             # test_worktree_report.py ABSENT; after -> surfaces=['core'], present.
             # ⚠️ n is a SNAPSHOT taken at the head that measured it — re-measure, do
             # not trust the count. Same #1349/#3332/#3616 class as the entries above.
             # Pinned by
             # tests/test_ci_selection.py::test_worktree_report_tool_change_selects_core_not_tier1.
             "tools/worktree_report.py",
             # #7814 review P2: the queue-reconcile schedule installer owns
             # tests/test_install_queue_reconcile_schedule.py, registered
             # `core`, but `tools/` is swallowed by NON_PYTHON_PREFIXES and no
             # SOURCE_PATTERNS entry matches an installer path — so an
             # INSTALLER-ONLY change selected NO surface and took the docs-only
             # early return: the hermetic cases pinning that the scheduled run
             # is the SAFE run (armed with `--apply`, the validated queue passed
             # EXPLICITLY, gh's directory on the job's PATH, XML/cron escaping,
             # and the install-only preflight) would not have run on the PR
             # editing the installer. Same #1349/#3332/#3616 silent-drop class
             # as tools/queue_resweep.py above. Pinned by
             # tests/test_ci_selection.py::test_install_queue_reconcile_schedule_tool_change_selects_core_not_tier1.
             "tools/install-queue-reconcile-schedule.sh",
             # #9639: the x86_64 runner installer owns
             # tests/test_ci_runner_x64_install.py, which is registered `core` (and `carve_out`),
             # but `tools/` is swallowed by NON_PYTHON_PREFIXES and no SOURCE_PATTERNS entry
             # matches an installer path — so an INSTALLER-ONLY change selected NO surface, took
             # the docs-only early return and fell back to tier-1 smoke, where that test is not
             # registered. The label invariant (this arch's marker only, no `ubuntu-<version>`
             # pin, `auto` reads the gate's state instead of assuming it) and the headroom
             # guards would then never run on the PR that edits them. Same #1349/#3332/#3616
             # silent-drop class as the queue-reconcile installer above. Pinned by
             # tests/test_ci_selection.py::test_install_ci_runner_x64_tool_change_selects_core_not_tier1.
             "tools/install-ci-runner-x64.sh",
             # PR #8427 review Finding 4: tools/graph_write_repair.py and its
             # launchd installer own tests/test_graph_write_repair.py (registered
             # `core`) -- the probe classification that authorises a repair, the
             # `--quiet` count contract, and the installer's XML escaping /
             # interval validation / lint-before-move / load verification.
             # `tools/` is swallowed by NON_PYTHON_PREFIXES and no SOURCE_PATTERNS
             # entry matches either path, so a tool-only or INSTALLER-only change
             # selected NO surface and fell back to tier-1 smoke: the guard would
             # never run on the very change that can weaken it. Same
             # #1349/#3332/#3616 silent-drop class as the queue-reconcile installer
             # entry above. Pinned by
             # tests/test_ci_selection.py::test_graph_write_repair_tool_change_selects_core_not_tier1.
             "tools/graph_write_repair.py",
             "tools/install-graph-write-repair-schedule.sh",
             # #8882 review P1: tools/retype_snapshot.py is the CLI for the
             # offline re-typing mechanics, and its guard
             # (tests/test_retype_8882.py) is `core`-registered — but `tools/`
             # is swallowed by NON_PYTHON_PREFIXES and no SOURCE_PATTERNS entry
             # matches the tool, so a CLI-only change selected NO surface, took
             # the docs-only early return and fell back to tier-1 smoke: the
             # file's own CLI-contract cases (`--decisions requires --out`, the
             # round trip) would never run on the PR editing the CLI. Same
             # #1349/#3332/#3616 silent-drop class as tools/queue_resweep.py
             # above. `tortoise/retype.py` needs no entry (it matches no
             # SOURCE_PATTERN, so a module-only change already falls through to
             # `core`). Pinned by
             # tests/test_ci_selection.py::test_retype_snapshot_tool_change_selects_core_not_tier1.
             "tools/retype_snapshot.py",
             # #8882 review P1 (census): tools/graph_census.py is the CLI for the
             # measured census, and its guard (tests/test_graph_census_8882.py) is
             # `core`-registered — but `tools/` is swallowed by
             # NON_PYTHON_PREFIXES and no SOURCE_PATTERNS entry matches the tool,
             # so a CLI-only change selected NO surface, took the docs-only early
             # return and fell back to tier-1 smoke: the file's own CLI-contract
             # cases (--out/--samples, the exit-code split 0/1/2, --diff) would
             # never run on the PR editing the CLI. `tools/edge_census.py` and
             # `tools/retype_snapshot.py` above carry the same lesson: an
             # instrument that makes an uncounted class visible must not be
             # invisible to CI selection itself. `tortoise/graph_census.py` needs
             # no entry (it matches no SOURCE_PATTERN, so a module-only change
             # already falls through to `core`). Pinned by
             # tests/test_ci_selection.py::test_graph_census_tool_change_selects_core_not_tier1.
             "tools/graph_census.py",
             # #9523 review P1: tools/legacy_id_normalise.py is the CLI for the
             # legacy-id normaliser, and its guard
             # (tests/test_8607_legacy_id_normaliser.py) is `core`-registered —
             # but `tools/` is a flat NON_PYTHON_PREFIXES entry and no
             # SOURCE_PATTERNS row matches the tool, so a CLI-only change was
             # DROPPED by `_keep_changed` and took the docs-only early return:
             # `select(["tools/legacy_id_normalise.py"])` returned
             # surfaces=[], full=False, with the guard absent from test_files.
             # Registering the test under `core` does not help — a name in a
             # surface's test_files only runs when that surface is already
             # selected — so the file whose ENTIRE content is the dry-run
             # default, the `--apply`/`--revert`-requires-`--out` rule and the
             # 0/1/2 exit contract (`test_cli_dry_run_writes_the_report_and_no_
             # snapshot`, `test_cli_apply_then_revert_round_trips`,
             # `test_cli_refusal_exits_2_and_writes_nothing`,
             # `test_cli_usage_errors_exit_1_so_2_means_only_refused`) would run
             # none of its own cases on the PR editing it. Same
             # #1349/#3332/#3910 silent-drop class as
             # tools/retype_snapshot.py and tools/graph_census.py above (both
             # dump-transform CLIs in this family). `tortoise/legacy_ids.py`
             # needs no entry (it matches no SOURCE_PATTERN, so a module-only
             # change already falls through to `core`). Pinned by
             # tests/test_ci_selection.py::test_legacy_id_normalise_tool_change_selects_core_not_tier1.
             "tools/legacy_id_normalise.py",
             # #4495 review P2: tools/write_op_reachability.py is the CLI that
             # reports the write-op allowance's stock/flow conversion, and its
             # guard (tests/test_write_op_reachability.py) is `core`+`api`
             # registered — but `tools/` is swallowed by NON_PYTHON_PREFIXES and
             # no SOURCE_PATTERNS entry matches the tool, so a CLI-only change
             # selected NO surface, took the docs-only early return and fell back
             # to tier-1 smoke: the exit-code contract (0 stated-yield / 1
             # unreachable / 2 not-measurable / 3 measured-uncertified), the
             # UNCERTIFIED rule and the absence rule would never run on the PR
             # editing the CLI — the instrument that makes an uncounted class
             # visible must not be invisible to CI selection itself. Same
             # #1349/#3332/#3616 silent-drop class as tools/graph_census.py
             # above. CORE_ALSO (not TOOL_CARVEOUTS) because the guard is
             # hermetic (synthetic books, a fake SDK) and `core` is where its
             # `core` arm lives. Pinned by
             # tests/test_ci_selection.py::test_write_op_reachability_tool_change_selects_core_not_tier1.
             # #3990: tools/ is in NON_PYTHON_PREFIXES, so a
             # `tools/why_layer_rate.py`-only change filtered to `changed == []`
             # and took the docs-only early return — the why-layer instrument's
             # own guard (tests/test_why_layer_rate.py, `core`-registered) never
             # ran on the file it pins. Same silent-drop class as the
             # queue_resweep / write_op_reachability entries above.
             "tools/why_layer_rate.py",
             "tools/write_op_reachability.py",
             # #9206 review P1 (the DERIVED-RECOMPUTE lane): the lane's own
             # disclosure points operators at
             # `tools/source_dedup_report.py --backfill` as the repair path for a
             # corpus whose `urlAliases` matter, and that tool is a DECLARED
             # WRITER of the lane (`_derived_recompute_write_scope`) — so it is
             # held by the SOURCE CENSUS in
             # tests/test_9206_derived_recompute_lane.py, which is `core`-
             # registered. `tools/` is a flat NON_PYTHON_PREFIXES entry and no
             # SOURCE_PATTERNS row matches the tool, so a tool-only change came
             # back with `changed == []` and took the docs-only early return:
             # the census that pins the tool's lane would never run on the PR
             # that changes it, and an undeclared write added there would go
             # unnoticed (the #1349/#3332/#3616 silent-drop class). CORE_ALSO
             # (not TOOL_CARVEOUTS) because the census is hermetic and `core` is
             # where the suite is registered. Pinned by
             # tests/test_ci_selection.py::test_9206_lane_suites_run_on_the_diffs_that_can_break_them.
             "tools/source_dedup_report.py",
             # #2786 review P2: `tools/probe_extractor.py` (the classification
             # prompt put in front of the model) and `tools/calibration_harness.py`
             # (the harness whose `VOCAB` the `minted` metric reads) are the two
             # tooling copies of the core object-kind vocabulary, and the guard
             # that pins both to `pack_registry.CANONICAL_OBJECT_KINDS` —
             # tests/test_kind_vocabulary_consistency.py — is `core`-registered.
             # MEASURED before these entries: `select(["tools/probe_extractor.py",
             # "tools/calibration_harness.py"])` -> surfaces=[], full=False, 52
             # tier-1 files, guard absent — so the exact drift the drift-gate
             # exists to catch (someone retyping a stale `core:<kind>` into a
             # tool) stays green on the very PR that introduces it. These entries
             # also keep `core` selected for the `ep` SOURCE_PATTERNS rows both
             # files now carry (a named-surface match replaces the `core`
             # fallback otherwise). Same #1349/#3332/#3616 silent-drop class as
             # tools/test_lane.py above. Pinned by
             # tests/test_ci_selection.py::test_extractor_tooling_change_selects_core_and_ep_not_tier1.
             "tools/probe_extractor.py",
             "tools/calibration_harness.py",
             # #3036: oauth.py is pinned by BOTH api-registered tests
             # (test_oauth_mcp.py, test_oauth_token_fault.py, ...) and core
             # (test_control_plane_offload_3498.py), so the SOURCE_PATTERNS
             # `api` match must not drop the core half.
             "tortoise/oauth.py",
             # #6870 review (P1/P3): the mono-stack guard declares
             # `tortoise/render.py` in its `MODULES` and scans it, so its diff
             # must select that guard (`SOURCE_PATTERNS["onboarding"]`). A
             # named-surface match REPLACES the `core` fallback, and
             # `tests/test_render.py` — the renderer's own guard — is registered
             # `core`. Without this entry a renderer-only change would select
             # `onboarding` and silently stop running the test that guards the
             # renderer.
             "tortoise/render.py",
             # #5581: the source-currency derivation helper owns
             # tests/test_source_currency_5581.py, registered `core` — but
             # `tools/` is swallowed by NON_PYTHON_PREFIXES and no
             # SOURCE_PATTERNS entry matches the tool, so a HELPER-ONLY change
             # selected NO surface, took the docs-only early return and fell
             # back to tier-1 smoke: the cases pinning §4.6's aggregate rule
             # (notably that an EMPTY link set reads `unknown`, never the
             # vacuously-`current` false-fresh) would not have run on the PR
             # that edits the aggregation. Same #1349/#3332/#3616 silent-drop
             # class as tools/test_lane.py above. Pinned by
             # tests/test_ci_selection.py::test_source_currency_tool_change_selects_core_not_tier1.
             "tools/source_currency.py",
             # #7968 / PR #8179 review P2: the schema <-> placement-TABLE pin
             # (`tests/test_record_schema_placement.py`, registered `core`) reads
             # the record migrations AND
             # docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md.
             # BOTH paths sit under NON_PYTHON_PREFIXES (`supabase/`, `docs/`)
             # and match no SOURCE_PATTERNS row, so a migration-only or
             # placement-doc-only change selected NO surface — the pin's own
             # header claim ("editing either side alone turns the test red") was
             # untestable in CI and the edit landed with the pin never running.
             # CORE_ALSO keeps both paths and selects `core`, so the pin runs on
             # the change it exists to catch (the #1349/#3332/#3616 silent-drop
             # class). Pinned by
             # tests/test_ci_selection.py::test_record_schema_pin_runs_on_migration_or_doc_change.
             "supabase/migrations/2026101200000",
             # #8263: the FORWARD record migration that adds `status` to
             # `subject.props`'s refusal. It sorts OUTSIDE the
             # `2026101200000` prefix above, and `supabase/` is in
             # NON_PYTHON_PREFIXES, so without its own entry a
             # forward-migration-only change would select NO surface and the two
             # readers (`test_record_projection.py`,
             # `test_record_schema_placement.py`) would not run on the very
             # change that can drift them — the same #1349/#3332/#3616
             # silent-drop class as the entry above. Pinned by
             # tests/test_ci_selection.py::test_record_schema_pin_runs_on_migration_or_doc_change.
             "supabase/migrations/20261014000001_record_subject_props_no_derived.sql",
             # #2782: the FORWARD record migration that ADDS the five money
             # COLUMNS to `public.point` (`ALTER TABLE … ADD COLUMN`). Same gap
             # as the #8263 entry above and it sorts outside the
             # `2026101200000` prefix: a forward-migration-only diff selects NO
             # surface, so neither reader (`test_record_projection.py`,
             # `test_record_schema_placement.py`) would run on the change that
             # can drift them. Pinned by
             # tests/test_ci_selection.py::test_record_schema_pin_runs_on_migration_or_doc_change.
             "supabase/migrations/20261018000001_record_point_money_value_2782.sql",
             # #9264: the FORWARD record migration that NARROWS
             # `public.session.capture_redactions` from `jsonb` to `integer`
             # (`ALTER TABLE … ALTER COLUMN … TYPE`). Same gap as the #8263 and
             # #2782 entries above, and it sorts outside the
             # `2026101200000` prefix: a migration-only diff selects NO surface,
             # so neither reader (`test_record_projection.py`,
             # `test_record_schema_placement.py`) would run on the change that
             # can drift them. Pinned by
             # tests/test_ci_selection.py::test_record_schema_pin_runs_on_migration_or_doc_change.
             "supabase/migrations/20261022000001_record_session_capture_redactions_integer.sql",
             # #9555/R7: the FORWARD record migration that CREATES
             # `public.session_narrative`. Same gap as the #8263/#2782 entries
             # above and it sorts outside the `2026101200000` prefix: a
             # forward-migration-only diff selects NO surface, so the legs that
             # read this migration's TEXT (`test_record_projection.py`,
             # `test_record_schema_placement.py`, `test_9555_session_narrative.py`,
             # `test_7950_capstone_probe.py`) would not run on the very change
             # that can drift them — the same #1349/#3332/#3616 silent-drop
             # class. Pinned by
             # tests/test_ci_selection.py::test_record_schema_pin_runs_on_migration_or_doc_change.
             "supabase/migrations/20261023000001_record_session_narrative.sql",
             # #9555 / PR #9862 review round 4: R7's CARRIER migration
             # (`20261024000001`, `COMMENT ON` only). Measured: a diff touching
             # ONLY this file selected `surfaces: []`, so
             # `test_9555_session_narrative.py` — which reads this file's text and
             # pins the schema's narrative claim by name — did not run on the one
             # change that can make its pin false. No `2026101*` prefix applies
             # (same reason as the tenancy row below: `supabase/` is a
             # NON_PYTHON_PREFIX), so without this entry the arm is a silent drop
             # of the #1349/#3332/#3616 class.
             "supabase/migrations/20261024000001_record_session_narrative_carrier.sql",
             # #9554 / PR #9600 review cycle 1 (P2): the TENANCY POSTURE migration —
             # the app role, its grants (including `relation_id_seq`'s USAGE) and
             # the `*_app_tenant` policies, one per record table (ten, since
             # #9555's `session_narrative`). It sorts outside the
             # `2026101200000` prefix and `supabase/` is a NON_PYTHON_PREFIX, so a
             # migration-only edit (dropping a `WITH CHECK`, flipping `is_local`,
             # re-adding `BYPASSRLS`, removing the sequence grant) selected NO
             # surface: `tests/test_record_tenancy_guc.py` — the suite that reads
             # this migration's TEXT and pins it — did not run on the change that
             # DEFINES the boundary. (The PGlite posture suite is NOT the gap: it
             # is selected by the workflow's own `supabase/migrations/` prefix
             # output and runs on every migration edit. The PYTHON pin is what
             # had no entry.) Pinned by
             # tests/test_ci_selection.py::test_the_tenancy_migration_selects_the_posture_pins.
             "supabase/migrations/20261025000001_record_tenancy_role_and_guc.sql",
             # #9967 review cycle 2 (P1-follow-through): the INDEX migration behind
             # the `:Source` resolver's `canonical_url` arm. It adds no column and
             # no policy — so no schema reader changes what it reports — but it is
             # the file that makes the resolver's third arm a seek, and
             # `tortoise/record_projection.py`'s own cost comment plus
             # `tests/test_record_projection.py`'s resolver pin are the tests that
             # would go stale if the arm were dropped or the index renamed. It
             # sorts outside every `2026101200000` prefix and `supabase/` is a
             # NON_PYTHON_PREFIX, so a diff touching ONLY this file selected NO
             # surface (`surfaces: []`, `full: false`) — the same
             # #1349/#3332/#3616 silent-drop class as its two neighbours here.
             # Pinned by tests/test_ci_selection.py::
             # test_the_source_canonical_index_migration_selects_the_resolver_pins.
             "supabase/migrations/20261025000002_record_source_canonical_index.sql",
             # #9881 / PR #9948 review P2: the FORWARD migration that
             # RE-DECLARES the Object/Subject key contract — the
             # `node_identity.key_property` CASE and both
             # `chk_*_node_key_is_contract_key` pins. It is the one file that can
             # re-declare what `record_projection.key_properties()` now reads
             # (LAST declaration wins), and it sorts outside the
             # `2026101200000` prefix while `supabase/` is a NON_PYTHON_PREFIX,
             # so a diff touching only this file selected NO surface. Measured on
             # the pre-fix head: `_sel([<this path>])` -> `surfaces: []`,
             # `full: false`, and every reader of the contract —
             # `test_record_projection.py`,
             # `test_record_schema_placement.py`, `test_cache_projection.py`,
             # `test_read_ledger.py` — absent from `test_files`. The
             # #1349/#3332/#3616 silent-drop class, and the reason a `core`
             # registration alone cannot fix it (a surface's test_files only run
             # when that surface is ALREADY selected). Pinned by
             # tests/test_ci_selection.py::test_record_schema_pin_runs_on_migration_or_doc_change
             # and ……::test_the_semantic_id_key_migration_selects_every_reader_of_the_contract.
             "supabase/migrations/20261027000001_record_semantic_id_key.sql",
             # #10017 R2 — the cache-tier migration is a FORWARD RE-DECLARATION
             # of `chk_point_props_no_derived`, which replaces the constraint at
             # the SQL level while the parser UNIONS. So this is the one file
             # that can silently SHRINK `point`'s refusal set with the parser
             # still reporting every name refused. A migration-only diff must
             # run `test_record_projection.py` / `test_cache_projection.py` /
             # `test_10017_cache_tier.py`, and a `core` registration alone
             # cannot do it (#1349/#3332/#3616).
             "supabase/migrations/20261029000001_record_point_cache_tier.sql",
             # Re-stated at the migration pair, NOT replaced by it (#9638 review
             # P1): the DOC side is an independent member of this set. The
             # #9555/R7 entry above was inserted over this path, so a
             # placement-doc-ONLY change — the edit that silently desynchronised
             # the pin from the table in the first place — fell back to the
             # docs-only early return and ran neither reader. Both halves must
             # be present; the pair's #7968 comment above names both paths and is
             # false without this entry.
             "docs/epics/2026-10-10-7879-postgres-record/02-identity-and-placement.md",
             # #9555 / #9638 review P3: the SECOND document the same pin now
             # reads. `test_record_schema_placement.py::
             # test_the_prerequisites_a1_census_matches_the_record_migrations`
             # pins `08-hosted-prerequisites.md` §A1 — the epic's own "what
             # EXISTS" ledger — against the migrations on disk. That path is
             # under `docs/` (a NON_PYTHON_PREFIXES entry) and matches no
             # SOURCE_PATTERNS row, so without this entry a §A1-only edit took
             # the docs-only early return and selected NO surface: the census
             # could be left claiming nine tables on the PR that creates the
             # tenth, with the pin that exists to catch it never running — the
             # #1349/#3332/#3616 silent-drop class, and the exact shape of the
             # defect it pins. Pinned by
             # tests/test_ci_selection.py::test_record_schema_pin_runs_on_migration_or_doc_change.
             "docs/epics/2026-10-10-7879-postgres-record/08-hosted-prerequisites.md",
             # #9638 review P1: the THIRD document a record pin now reads, and
             # the one whose absence made the PR's own newest commit
             # (`e92a78099`, "the routing index's census, and the pin that keeps
             # it true") unpinnable. `test_record_schema_placement.py::
             # test_the_routing_index_census_matches_the_record_migrations`
             # reads `docs/00_index.md`'s #7879 row for its census
             # ("N record tables + M procedural views")
             # AND for its negative ("no narrative/raw-text table"). That path
             # is under `docs/` (a NON_PYTHON_PREFIXES entry) and matches no
             # SOURCE_PATTERNS row, so an index-only edit filtered to
             # `changed == []` and took the docs-only early return: the census
             # could go stale again — exactly the drift the commit exists to
             # catch — with the pin that guards it never selected. The
             # `core` registration cannot fix this on its own: a surface's
             # test_files only run when that surface is ALREADY selected.
             # Same #1349/#3332/#3616 silent-drop class as the two entries
             # above. Pinned by
             # tests/test_ci_selection.py::test_record_schema_pin_runs_on_migration_or_doc_change.
             "docs/00_index.md",
             # RETIRED 2026-10-19: the orchestrator harness (and therefore
             # tests/test_orchestrator_harness.py) moved to
             # agent-infra/scripts/orchestrator-harness/. This repo keeps only a pointer
             # README plus the two scripts whose fixes are UNPORTED (`merge-one.sh`,
             # `merged-not-closed.sh`); a harness script beyond those is a fork, which
             # tools/check-harness-fork.sh now fails on.
             #
             # #9217 review: the directory is STILL registered, because the two retained
             # scripts are live code with hermetic guards
             # (tests/test_orchestrator_harness_retained.py) — a change to either, or to the
             # guard itself, must run them. That is the #1349/#3332/#3616 silent-drop class:
             # `operations/` and `tools/` are flat NON_PYTHON_PREFIXES entries, so an
             # operations-only or tools-only diff filtered to `changed == []`, took the
             # docs-only early return and fell to tier-1 smoke, which does not carry these
             # files. A name in a surface's test_files cannot fix that on its own — it only
             # runs when the surface is ALREADY selected. Pinned by
             # tests/test_ci_selection.py::test_retired_orchestrator_harness_selects_its_retained_guards.
             "operations/orchestrator-harness/",
             "tools/check-harness-fork.sh",
             # #3554 review (P2, Finding 2): the :Session declaration's own doc
             # subjects — `docs/ONTOLOGY.md` §4.8 (the owner table the machine
             # declaration mirrors) and
             # `docs/plans/2026-08-25-1714-memory-capture-onboarding.md` (the
             # `#3554 amendment` the D9 verdicts cite) — own
             # tests/test_session_schema_3554.py, registered `core`. `docs/` is a
             # flat NON_PYTHON_PREFIXES entry and neither path is a concrete
             # SOURCE_PATTERNS row, so a docs-only edit of either filtered to
             # `changed == []`, took the docs-only early return and fell back to
             # tier-1 smoke — whose set does NOT contain that file. The §4.8
             # owner rows could then drop an owner (or the plan amendment lose a
             # decision) and merge green against the declaration it claims to be
             # pinned to. Same #1349/#3332/#3616 silent-drop class as
             # `docs/auth-architecture.md` and the #3524 consent-bridge records;
             # pinned by
             # tests/test_ci_selection.py::test_the_session_declaration_guard_runs_on_its_docs_subjects_too.
             "docs/ONTOLOGY.md",
             "docs/plans/2026-08-25-1714-memory-capture-onboarding.md",
             # #3554 review (P2): the guard's THIRD docs subject —
             # `docs/ONTOLOGY-CHANGELOG.md`, which
             # `test_section_4_8_changelog_entry_sits_under_its_own_version_block`
             # reads — was left out of this tuple while the other two were
             # admitted, so the same silent drop stayed open for it: a docs-only
             # edit moving the `§4.8 (added by #3554` bullet back under the wrong
             # `Changelog vX.Y` header selected NO surface and fell back to
             # tier-1 smoke, whose 45-file set does not contain the guard. The
             # sibling changelog readers (test_5142/7818/8474_ontology_record.py)
             # are covered only because they happen to sit in tier1 — the new
             # module is not, and a `core` registration alone never helps (a
             # surface name only runs when that surface is already selected).
             # Same #1349/#3332/#3616 class; pinned alongside the other two in
             # tests/test_ci_selection.py::test_the_session_declaration_guard_runs_on_its_docs_subjects_too.
             "docs/ONTOLOGY-CHANGELOG.md",
             # PR #8594 review P2: the fail-closed dispatcher's review copy
             # (tools/agent-infra-queue-dispatch.py) owns
             # tests/test_queue_dispatch_fail_closed.py, and tools/fleet_state.py
             # owns tests/test_fleet_state.py — BOTH `core`-registered. `tools/` is a
             # flat NON_PYTHON_PREFIXES entry and neither path matches a
             # SOURCE_PATTERNS row, so each one's own change came back with
             # `changed == []`, took the docs-only early return and fell back to
             # tier-1 smoke: the guards pinning the lane-read fail-closed and the
             # registry-provenance refusals never ran on the very files that can
             # weaken them. Same #1349/#3332/#3616 silent-drop class as
             # tools/queue_resweep.py above. Pinned by
             # tests/test_ci_selection.py::test_queue_dispatch_and_fleet_state_change_select_core_not_tier1.
             "tools/agent-infra-queue-dispatch.py",
             "tools/fleet_state.py",
             # #7950 / PR #8609 review P1: the capstone end-to-end probe
             # (docs/evidence/7950-capstone/probes/e2e_chain_probe.py) owns
             # tests/test_7950_capstone_probe.py, registered `core` — the DB-free
             # pin asserting the probe's two reconciliation readers map the REAL
             # property sets (not `{}` placeholders, which make
             # `field_mismatches` structurally zero). `docs/` is a flat
             # NON_PYTHON_PREFIXES entry and no SOURCE_PATTERNS row matches the
             # probe, so a PROBE-ONLY change came back with `changed == []` and
             # took the docs-only early return: `select()` returned NO surface
             # and the pin never ran on the exact file it guards — reverting the
             # readers to the pre-fix `{}` shape landed with the pin silent (the
             # #1349/#3332/#3616 silent-drop class, the same shape as the
             # migrations/placement-doc entries above and
             # docs/research/1714-solution-converge.md). A SOURCE_PATTERNS entry
             # could not fix it: `_selection_relevant()` deliberately skips the
             # `core` surface, and the probe's guard is `core`-registered.
             # Pinned by
             # tests/test_ci_selection.py::test_capstone_probe_change_selects_core_not_tier1.
             #
             # PR #9196 review P1 — WIDENED from `probes/` to the whole bundle:
             # the probe is no longer the only artifact the guard pins. The same
             # PR added `test_the_s2_census_log_is_a_LIVE_measurement`, which
             # re-runs the committed census probe and compares its output to
             # `s2-wiring-census.log`, and
             # `test_the_evidence_readme_quotes_the_log_it_cites_verbatim`, which
             # pins the README's quotes to `e2e-chain-probe.log`. Both LOGS and
             # the README are hand-editable committed text — so a one-file PR
             # that re-cuts a number in them took the docs-only early return and
             # merged with the pin that guards them NEVER RUN (measured:
             # `select(['docs/evidence/7950-capstone/s2-wiring-census.log'])` →
             # surfaces=[], probe absent). That is the same #1349/#3332/#3616
             # shape one level out: the artifact a verdict REST on must not be
             # editable without the pin that re-measures it running. Pinned by
             # tests/test_ci_selection.py::test_the_capstone_evidence_bundle_selects_core_not_tier1.
             "docs/evidence/7950-capstone/",
             # #9076: the E1+E2 evidence bundle
             # (`docs/evidence/7949-capstone/` — the probe, the corpus JSON and its
             # builder, the evidence log) and its structural checker
             # (`tools/e1_e2_census_check.py`) own
             # `tests/test_e1_e2_measurement.py`, registered `core`.
             #
             # MEASURED before these entries (2026-10-18, `select()` in-process):
             # a diff touching ANY of them returned `surfaces=[]`, `full=False`,
             # 50 test files — i.e. `docs/` and `tools/` are flat
             # NON_PYTHON_PREFIXES entries, the changed list came back empty and
             # select() took the docs-only early return. The pin still RAN, but
             # only because this PR also dual-registers it in `tier1` (the same
             # `core`=home / `tier1`=docs-only pairing the extractor-v4 guards
             # above use). That is the fragile state these entries remove: `tier1`
             # is a CURATED list, so the guard's owning surface (`core`) was
             # nominal and one tier1 edit from dropping the pin silently — the
             # #1349/#3332/#3616 class, reached through the early return rather
             # than through `_selection_relevant`. After: `surfaces=['core']`,
             # `full=False`, pin present — the same shape and remedy as
             # `docs/evidence/7950-capstone/probes/` above, and (unlike that
             # entry) it is a cost/home fix rather than a hole fix, which is
             # stated so the polarity is not misread later.
             "docs/evidence/7949-capstone/",
             # #9695: the Step-1 eval's EVIDENCE DOC
             # (`docs/ax/how-to-use-tortoise-step1-eval.md`) publishes every
             # measured number of that eval — the aggregate headline, all ten
             # per-input P0/P1 rows, the class census and every `kind` ×N
             # multiplier — and the only thing that re-derives them from the
             # committed `runs/*/score.json` records is
             # `tests/test_ax_write_eval.py`, registered on `core` only.
             #
             # MEASURED before this entry (`select()` in-process):
             # `select(["docs/ax/how-to-use-tortoise-step1-eval.md"])` →
             # `surfaces=[]`, `full=False`, pin ABSENT. `docs/` is a
             # flat NON_PYTHON_PREFIXES entry and `_is_non_code_doc` recognises
             # four exact paths, so the changed list comes back empty and
             # select() takes the docs-only early return — tier-1 smoke, which
             # does not carry the pin. A one-file PR could therefore re-cut the
             # headline to any number and merge with the guard that re-measures
             # it NEVER RUN: the #1349/#3332/#3616 class, reached through the
             # early return rather than `_selection_relevant`, and the same
             # shape recorded for `docs/evidence/7950-capstone/` above.
             # Pinned by tests/test_ci_selection.py::
             # test_the_step1_eval_evidence_doc_selects_core_not_tier1.
             "docs/ax/how-to-use-tortoise-step1-eval.md",
             # `tools/` is a flat NON_PYTHON_PREFIXES entry too, so the checker was
             # swallowed by the same early return; its pins are `core`-registered
             # and hermetic, so `core` is the right lane (the shape
             # `tools/lane_stash.py` above takes).
             "tools/e1_e2_census_check.py",
             # #9080 (#9089 review): the capstone gate itself
             # (`tools/capstone_verdict_check.py`) — the closing rule for epic
             # #7949 — owns `tests/test_7949_capstone_verdict_check.py`, which is
             # registered `core`. Without this row a GATE-ONLY diff came back
             # `surfaces=[], full=False`: the pin ran only because it also sits in
             # the `tier1` curated list, so `core` — the surface the comment in
             # `config/ci-surfaces.yml` names as its home — was nominal, and one
             # `tier1` edit would have dropped the pin silently (measured before
             # this entry: `select(['tools/capstone_verdict_check.py'])` →
             # surfaces=[], pin present only via tier1). Same shape and remedy as
             # `tools/e1_e2_census_check.py` above, and the same polarity note:
             # this is a cost/home fix, not a hole fix. Pinned by
             # tests/test_ci_selection.py::test_the_capstone_gate_change_selects_core_not_tier1.
             "tools/capstone_verdict_check.py",
             # #4030: `supabase/config.toml` is the committed LOCAL-stack config,
             # and every env var it resolves by `env(NAME)` substitution must be
             # declared in `.env.example` — the guard for that pair
             # (tests/test_env_contract_covers_supabase_config.py) is
             # `core`-registered. `supabase/` is a flat NON_PYTHON_PREFIXES entry
             # and the path matches no SOURCE_PATTERNS row, so a CONFIG-ONLY
             # change came back with `changed == []` and took the docs-only early
             # return: `select()` returned NO surface and the guard never ran on
             # the half of the pair it exists to hold (the #1349/#3332/#3616
             # silent-drop class, the same shape as the migrations/placement-doc
             # entries above). A SOURCE_PATTERNS entry cannot fix it —
             # `_selection_relevant()` deliberately skips the `core` surface.
             # Measured before this entry: `select(["supabase/config.toml"])` ->
             # surfaces=[], full=False, 40 test files, the guard absent. `core` is
             # the right surface rather than a cost concern: config.toml is edited
             # rarely (9 commits ever as of 2026-10-14). Pinned by
             # tests/test_ci_selection.py::test_supabase_config_change_selects_core_not_tier1.
             "supabase/config.toml",

             # PR #8744 review P2: the SQL↔Python contract guard
             # (`tests/test_postgres_storage.py`, registered `core`) reads the
             # migration TEXT to pin the two RPC names and the `p_since` /
             # `p_until` parameter names — a rename on either side makes
             # PostgREST 404 the wrapper at runtime, and the PGlite suite cannot
             # see it (it calls the functions positionally). BOTH paths sit
             # under NON_PYTHON_PREFIXES (`supabase/`, `docs/`) and match no
             # SOURCE_PATTERNS row, so before these entries a migration-only or
             # doc-only change came back with `changed == []`, took the
             # docs-only early return and fell back to tier-1 smoke — the guard
             # never ran on the very change that can rename the contract (the
             # #1349/#3332/#3616 silent-drop class). A name in a surface's
             # test_files only runs when that surface is ALREADY selected, so
             # the `core` registration could not fix it. Pinned by
             # tests/test_ci_selection.py::test_postgres_storage_guard_runs_on_migration_or_doc_change.
             "supabase/migrations/20261015000001_postgres_storage_footprint.sql",
             "docs/ops/postgres-storage-measurement.md",
             # #8051: `tools/` is a flat NON_PYTHON_PREFIXES entry, so a
             # `tools/lane_stash.py`-only change was filtered out BEFORE the
             # "not matched -> core" fallback: `select(["tools/lane_stash.py"])`
             # returned NO surface and the pin registered under `core`
             # (`tests/test_lane_stash.py`) never ran on the file it guards — the
             # #1349/#3332/#3616 silent-drop class, the same shape as the
             # `tools/tmpdir_sweep.py` entry above. `CORE_ALSO` (not a
             # SOURCE_PATTERNS row) is deliberate: the guard is a lane-hygiene
             # contract about a TOOL, and `core` is the surface that already
             # carries the other `tools/` pins (`test_collision_preflight.py`,
             # `test_test_lane_tool.py`). Pinned by
             # tests/test_ci_selection.py::test_lane_stash_guard_runs_on_a_tools_only_change.
             "tools/lane_stash.py",
             # #5612: the journal-growth measurement (`tools/journal_growth.py`)
             # and its probe
             # (`docs/evidence/5612-journal-growth/probes/corpus_growth_probe.py`)
             # own `tests/test_journal_growth.py`, registered `core`. BOTH paths
             # sit under NON_PYTHON_PREFIXES (`tools/`, `docs/`) and match no
             # SOURCE_PATTERNS row, so before these entries a tool-only or
             # probe-only change came back with `changed == []`, took the
             # docs-only early return, and the accounting pins
             # (`bytes + other_bytes == file size`, malformed counted not
             # raised) never ran on the file they guard — the
             # #1349/#3332/#3616 silent-drop class, the same shape as
             # `tools/lane_stash.py` above and
             # `docs/evidence/7950-capstone/probes/` above. Pinned by
             # tests/test_ci_selection.py::test_journal_growth_guard_runs_on_a_tool_or_probe_change.
             "tools/journal_growth.py",
             "docs/evidence/5612-journal-growth/probes/",
             # ⛔ NO `docs/evidence/7949-capstone/probes/` ROW. #9080 did add
             # one, and it was DEAD: `"docs/evidence/7949-capstone/"` above is
             # the whole bundle prefix and matching is `path.startswith()`, so
             # a narrower prefix inside it can never admit a path the broader
             # one does not. Measured (2026-10-20, `select()` in-process): with
             # the narrow row deleted, a probe-only diff still returns
             # `surfaces=['core']` and carries
             # tests/test_7949_capstone_verdict_check.py — the broad row is what
             # makes the probe select `core`, and it landed with #9076. A dead
             # row is not harmless: it reads as the reason a path is covered, so
             # the next lane re-adds it and the real reason (the broad row) goes
             # unexamined. Pinned by
             # tests/test_ci_selection.py::test_no_core_also_entry_is_shadowed_by_a_parent_prefix
             # and ::test_7949_capstone_probe_change_selects_core_not_tier1.
             # #5612: `tests/test_journal_growth.py` hard-codes the CORPUS's
             # shape (`records == 39`, 29 distinct types), so an edit to the
             # fixture alone must select `core` too — it sits under
             # NON_PYTHON_PREFIXES (`supabase/`) and matches no SOURCE_PATTERNS
             # row, so without this entry a fixture-only diff (e.g. adding a
             # 30th record type — the reason such a corpus exists) came back
             # `changed == []`, took the docs-only early return, and the pins
             # never ran on the bytes they hard-code. Pinned by
             # tests/test_ci_selection.py::test_journal_growth_guard_runs_on_a_tool_or_probe_change.
             "supabase/tests/fixtures/record_projection_journal.jsonl",
             # agent-infra#2344 (owner ruling): `tools/runner_autoscale.py` is this
             # box's runner-capacity guard and `tests/test_runner_autoscale.py`
             # owns its pins (ceiling arithmetic + 25 mutation gates). `tools/` is a
             # flat NON_PYTHON_PREFIXES entry, so a tool-only change came back with
             # `changed == []` and the guard's own tests never ran on the file they
             # guard — the #1349/#3332/#3616 silent-drop class, the same shape as
             # `tools/lane_stash.py` above. `CORE_ALSO` (not a SOURCE_PATTERNS row)
             # is deliberate: this is a lane/fleet-hygiene contract about a TOOL, and
             # `core` already carries the other `tools/` pins.
             "tools/runner_autoscale.py",
             # PR #9685 review round 9, P1-2: the INSTALLER arms the guard, and it was
             # missing while its `.py` was listed — so an installer-only diff selected
             # NO surface. That is the same silent-drop class one file over: the 825
             # lines that decide whether the timer is armed at all were verified by
             # nothing. Pinned by
             # tests/test_ci_selection.py::test_runner_autoscale_installer_change_selects_core_not_tier1.
             "tools/install-runner-autoscale-schedule.sh",
             # PR #8852 review P2: `website/apps/dashboard/deploy.sh` is the
             # documented manual deploy, and the #8816 guard
             # (tests/test_e2e_wrangler_prewarm.py, registered `core`) both reads
             # it and EXECUTES it from a scratch tree. `website/` is a flat
             # NON_PYTHON_PREFIXES entry and the script matches no SOURCE_PATTERNS
             # row, so a deploy.sh-only diff came back with `changed == []`, took
             # the docs-only early return and fell back to tier-1 smoke — where
             # that guard is NOT selected, so the arm that would catch a broken
             # path never ran on the only PR that can break it. Measured before
             # this entry: `select(["website/apps/dashboard/deploy.sh"])` ->
             # surfaces=[], full=False, 46 test files, guard absent. Same
             # #1349/#3332/#3616 silent-drop class as the `tools/` entries above;
             # a name in a surface's test_files cannot fix it, because it only
             # runs when that surface is ALREADY selected. Pinned by
             # tests/test_ci_selection.py::
             # test_dashboard_deploy_script_change_selects_core_not_tier1.
             "website/apps/dashboard/deploy.sh",
             # #10017: `tools/turn_parity_census.py` is the PURGE GATE for the
             # pre-#10000 turn nodes — read-only parity census, graph turn
             # nodes vs record turn rows, with refusal arms its test
             # (`tests/test_10017_turn_parity_census.py`, registered `core`)
             # pins. `tools/` is a flat NON_PYTHON_PREFIXES entry and the tool
             # matches no SOURCE_PATTERNS row, so a census-only diff was
             # filtered out BEFORE the "not matched -> core" fallback and the
             # gate's own refusal pins never ran on the file they hold (the
             # #1349/#3332/#3616 silent-drop class, the same shape as
             # `tools/lane_stash.py` above). `CORE_ALSO` (not a
             # SOURCE_PATTERNS row) is deliberate: `core` already carries the
             # other `tools/` pins. Pinned by
             # tests/test_ci_selection.py::
             # test_turn_parity_census_guard_runs_on_a_tools_only_change.
             "tools/turn_parity_census.py",
             # #8491: the meeting→CRM bridge. `integrations/` is a flat
             # NON_PYTHON_PREFIXES entry and bridge.py matches no
             # SOURCE_PATTERNS row, so a bridge.py-only change was filtered out
             # BEFORE the "not matched -> core" fallback and took the
             # docs-only early return — measured
             # `select(["integrations/crm/twenty/bridge.py"])` -> surfaces=[]
             # (45 tier-1 files), so the guard for the reader of
             # `~/.minutes/cal-trigger-state.json`
             # (tests/test_8491_cal_attendee_grounding.py, registered under
             # `core`) never ran on the file it guards — the
             # #1349/#3332/#6138 silent-drop class. Same shape as the
             # `tools/queue_resweep.py` entry above. Pinned by
             # tests/test_8491_cal_attendee_grounding.py::
             # test_bridge_change_selects_core_so_this_guard_runs.
             "integrations/crm/twenty/bridge.py",
             # #5045 review P1: `tools/cost_coverage_audit.py` is the INSTRUMENT the
             # whole cost-dimension registry rests on, and its guard
             # (tests/test_cost_dimensions.py, `core`+`api` registered) is the only
             # thing that pins it — but `tools/` is a flat NON_PYTHON_PREFIXES entry
             # and no SOURCE_PATTERNS row matches the tool, so a TOOL-ONLY change was
             # filtered out BEFORE the "not matched -> core" fallback and took the
             # docs-only early return. Measured before this entry:
             # `select(["tools/cost_coverage_audit.py"])` -> surfaces=[], full=False,
             # 46 tier-1 files, test_cost_dimensions.py ABSENT — so deleting a check
             # from `audit()`, widening `_NOT_PRODUCTION` back over `tools/`, or
             # restoring a predicate the module argues against merged green, and the
             # registry quietly stopped being verified (the #1349/#3332/#3616
             # silent-drop class, same shape as tools/drift-guard.py above). Pinned
             # by tests/test_ci_selection.py::
             # test_cost_coverage_audit_tool_change_selects_core_not_tier1.
             "tools/cost_coverage_audit.py",
             # #5045 review P1 (the ledger half): `check_ledger` verifies the
             # registry's declared columns and SQL functions against the migration
             # TEXT, and `supabase/` is a flat NON_PYTHON_PREFIXES entry too — so a
             # MIGRATION-ONLY change that renames a metering column or an RPC
             # selected NO surface and the ledger check never ran on the file
             # carrying the DDL it verifies (same shape as the #8744
             # postgres-storage entries above). These are the tracked migrations that
             # CREATE a declared cost column or DEFINE a declared RPC. A migration
             # edited elsewhere is still caught by the next `core`/`api` PR — an
             # accepted delay, stated in docs/ops/cost-dimensions.md §"What the audit
             # enforces". Pinned by tests/test_ci_selection.py::
             # test_cost_dimension_ledger_migrations_select_core.
             "supabase/migrations/0014_metering_records.sql",
             "supabase/migrations/20260813000002_metering_nodes_written.sql",
             "supabase/migrations/20260829000001_metering_ask_columns.sql",
             "supabase/migrations/20260915000001_tenancy_team_to_org.sql",
             "supabase/migrations/20260917000001_metering_capture_cost.sql",
             "supabase/migrations/20260918000001_metering_period_window.sql",
             "supabase/migrations/20260927000001_metering_unmetered_increments.sql",
             "supabase/migrations/20261001000001_metering_capture_tokens.sql",
             "supabase/migrations/20261001000004_metering_embedding_columns.sql",
             "supabase/migrations/20261001000005_metering_graph_storage.sql",
             "supabase/migrations/20261010000001_metering_period_end_refresh.sql",
             "supabase/migrations/20261013000001_metering_retrieval_columns.sql",
             # #5045 review P1 (the doc half): `docs/ops/cost-dimensions.md` is one of
             # the registry's own subjects — it states what the audit enforces, and a
             # docs-only edit of it selected NO surface, so the audit's claims could
             # drift from the audit's code with every pin green (the same
             # `docs/ops/postgres-storage-measurement.md` shape above). Pinned by
             # tests/test_ci_selection.py::
             # test_cost_dimension_ledger_migrations_select_core.
             "docs/ops/cost-dimensions.md",
             # PR #8986 review (P1/P2, four independent reviews): the two
             # served-identity DRIVERS are the files whose one-copy edit
             # `tests/test_surface_manifest.py` exists to catch. That suite is
             # `core`-registered AND a `slow_files` member
             # (config/ci-surfaces.yml), so it can only run on the `test-slow`
             # leg — and `tools/` is a flat NON_PYTHON_PREFIXES entry matching
             # no SOURCE_PATTERNS row, so a driver-only diff came back with
             # `changed == []`, took the docs-only early return and returned
             # `surfaces=[]`, `slow_run=False`: the parity net was ASLEEP on the
             # exact diff class it was written for. The required `surface-guard`
             # CI job cannot backstop it either — it OVERWRITES
             # `tools/surface-guard.py` with the base ref's copy before running,
             # so a PR-side edit to that copy is discarded before measurement,
             # and its `surface_manifest.py check` step reads the UNCHANGED
             # manifest copy. Measured before this entry: `select([
             # "tools/surface-guard.py"])` -> surfaces=[], full=False, 46
             # tier-1 files, `test_surface_manifest.py` absent. Same
             # #1349/#3332/#3616 silent-drop class as `tools/lane_stash.py`
             # above; `CORE_ALSO` (not `TOOL_CARVEOUTS`) is the documented seam
             # because the guard is `core`-registered, and a name in a surface's
             # test_files only runs when that surface is ALREADY selected.
             # Pinned by
             # tests/test_ci_selection.py::test_surface_tools_change_selects_core_not_tier1.
             "tools/surface-guard.py",
             "tools/surface_manifest.py",
             # #9115: the repo-level CI queue instrument. Its suite
             # (tests/test_ci_queue_health.py) is `core`-registered, and that
             # registration covers the TEST FILE's edits only — no SOURCE_PATTERNS
             # row matches a `tools/` path, so without this entry a
             # `tools/ci_queue_health.py`-only diff comes back `changed == []`,
             # takes the docs-only early return and selects NO surface: the
             # FLOOD classification, the governor's bounds (never a PR-event run,
             # never a workflow outside TRIM_SAFE) and the truncation labelling
             # would not run on the very diff that can widen them. Measured
             # before this entry: `--changed-files tools/ci_queue_health.py` ->
             # surfaces=[], full=False, 49 tier-1 files, test_ci_queue_health.py
             # ABSENT. `CORE_ALSO` (not `TOOL_CARVEOUTS`) is the documented seam:
             # the suite is hermetic and `core`-registered, so `core` is the
             # surface that can actually run it. Pinned by
             # tests/test_ci_selection.py::test_ci_queue_health_tool_change_selects_core_not_tier1.
             "tools/ci_queue_health.py",
             # #10017 review P1 (the second time this class was measured on a
             # CENSUS): tools/backfill_graph_turns.py owns
             # tests/test_10017_backfill_census.py, registered `core`. `tools/`
             # is a flat NON_PYTHON_PREFIXES entry matching no SOURCE_PATTERNS
             # row, so a tool-only diff came back with `changed == []`, took the
             # docs-only early return and selected NO surface — the census that
             # gates an irreversible turn delete would never run its own suite
             # (nor `tests/test_bounded_raw_clients_3065.py`, which pins its
             # read bound, nor `tests/test_entry_point_python_guard.py`, which
             # pins its #5128 guard) on the PR that edits it. Measured before
             # this entry: `--changed-files tools/backfill_graph_turns.py` ->
             # surfaces=[], full=False, test_10017_backfill_census.py ABSENT.
             # `CORE_ALSO` (not `TOOL_CARVEOUTS`) is the documented seam, for the
             # same reason as `tools/graph_census.py` above: the suite is
             # hermetic (a fake graph + a fake client, no DB), so `core` is the
             # surface that can actually run it — fail-closed FULL would add the
             # whole matrix for a DB-free instrument. Pinned by
             # tests/test_ci_selection.py::test_backfill_graph_turns_tool_change_selects_core_not_tier1.
             "tools/backfill_graph_turns.py",
             # #10017 review P1 (the THIRD time this class was measured on this
             # chain): tools/backfill_copy_turns.py is the COPY phase — the write
             # whose turns the parity gate then certifies before an IRREVERSIBLE
             # delete — and it owns tests/test_10017_backfill_copy.py, registered
             # `core`. Same mechanism as its two siblings above: `tools/` is a flat
             # NON_PYTHON_PREFIXES entry matching no SOURCE_PATTERNS row, so a
             # tool-only diff came back with `changed == []`, took the docs-only
             # early return and selected NO surface. Measured before this entry:
             # `--changed-files tools/backfill_copy_turns.py` -> surfaces=[],
             # full=False, test_10017_backfill_copy.py ABSENT (tier-1 smoke only).
             # That diff also loses the file's own repo guard — the #5128
             # interpreter guard in tests/test_entry_point_python_guard.py, which
             # DID red this tool at 4cdad4be7 — so the one diff that can break the
             # copy ran nothing that pins it.
             # `CORE_ALSO` (not `TOOL_CARVEOUTS`) for the same reason as the
             # census: the suite is hermetic (a fake graph + a fake journal, no
             # DB), so `core` is the surface that can actually run it. Pinned by
             # tests/test_ci_selection.py::
             # test_backfill_copy_turns_tool_change_selects_core_not_tier1.
             "tools/backfill_copy_turns.py",
             # #9085 review P1: `tests/test_runner_ports_6673.py` (registered
             # `core`) carries the two #8124 guards that pin the ENGINE, and both
             # read a file the selector did not keep:
             #   * `apps/graph-viz/docker-compose.yml` — scanned by the compose
             #     sweep (`COMPOSE_GLOBS` includes `apps/*/docker-compose*.yml`),
             #     but `apps/` is in NON_PYTHON_PREFIXES and matches no
             #     SOURCE_PATTERNS row, so a revert of that pin filtered to
             #     `changed == []`, took the docs-only early return, and ran
             #     tier-1 smoke only (measured: surfaces=[]).
             #   * `docs/quickstart-selfhosted.md` — Option B's bare-container
             #     hand-out, pinned by
             #     test_the_selfhost_quickstart_hands_out_the_compose_pin_not_latest.
             #     It matched the `#3329` `docs/` SOURCE_PATTERN that carries
             #     `test_capacity_page.py` (onboarding), NOT `core`, so the
             #     quickstart's engine pin was unguarded on the one docs diff that
             #     can break it (measured: surfaces=['onboarding'], the guard
             #     absent).
             # Either way a one-line revert merged green and reddened `main` on
             # the post-merge push — the #1349/#3332/#3616 silent-drop class, on
             # the exact files this PR pins. Named individually because
             # `select()` matches with `startswith`. Pinned by
             # tests/test_ci_selection.py::
             # test_the_engine_pin_guards_are_selected_on_the_files_they_pin.
             "apps/graph-viz/docker-compose.yml",
             "docs/quickstart-selfhosted.md",
             # PR #9541 review P1: tools/link_agent_infra_scripts.py is the ONLY
             # executor of #9475's fix — it decides whether the repo-root
             # `scripts` pointer exists at all, and it is the file every
             # documented `scripts/<tool>.sh` (admin-merge.sh, the merge rail,
             # included) depends on. Its guard,
             # tests/test_link_agent_infra_scripts.py, is `core`-registered — but
             # `tools/` is swallowed by NON_PYTHON_PREFIXES and no
             # SOURCE_PATTERNS entry matches the tool, so a MATERIALISER-ONLY
             # change filtered to `changed == []`, took the docs-only early
             # return and fell back to tier-1 smoke: the cases pinning that the
             # pointer stays gitignored, that a dangling link is repaired, that a
             # RELATIVE --infra still writes a resolving target, and that an
             # explicit --infra is never discarded would never run on the PR that
             # can break them — while `--integrity` stays green, because the test
             # file IS classified. Measured before this entry: `--changed-files
             # tools/link_agent_infra_scripts.py --event pull_request` ->
             # surfaces=[], full=False, 57 tier-1 files,
             # test_link_agent_infra_scripts.py ABSENT. Same #1349/#3332/#3616
             # silent-drop class as tools/test_lane.py above. `CORE_ALSO` (not
             # `TOOL_CARVEOUTS`) is the documented seam: the suite is hermetic (a
             # tmp_path repo root plus a fake agent-infra checkout) and `core` is
             # where it is registered. Pinned by
             # tests/test_ci_selection.py::test_link_agent_infra_scripts_tool_change_selects_core_not_tier1.
             "tools/link_agent_infra_scripts.py",
             # PR #9541 review (round 6 P1): tools/agent_infra_drift_check.py is the CI
             # entry point of the SAME fix — it is what `.github/workflows/ci.yml`'s
             # `drift-check` job passes as `check-command`, so it decides whether the
             # drift gate verifies anything at all (or, mis-quoted, reds forever with
             # "produced NO result"). Its guard lives in the same core-registered suite
             # (`tests/test_link_agent_infra_scripts.py`), and exactly the same
             # silent-drop seam applies: `tools/` is swallowed by NON_PYTHON_PREFIXES, so
             # a WRAPPER-ONLY change filtered to `changed == []`, took the docs-only early
             # return, and the interpolation/exit-code cases that pin it never ran while
             # `--integrity` stayed green. Pinned by
             # tests/test_ci_selection.py::test_agent_infra_drift_check_tool_change_selects_core_not_tier1.
             "tools/agent_infra_drift_check.py",
             # #9524 review I1/I3: the SPA `overrides` record. `tests/
             # test_spa_dependency_overrides.py` (registered `core` AND
             # `onboarding`) reads `apps/graph-viz/` and
             # `website/apps/dashboard/` `package.json` +
             # `package-lock.json`. Neither app's manifest matches a
             # SOURCE_PATTERNS row AND `apps/` + `website/` are both in
             # NON_PYTHON_PREFIXES, so such a diff was filtered out BEFORE any
             # surface was matched, took the docs-only early return and ran
             # tier-1 smoke only — measured on this branch:
             # `select(['apps/graph-viz/package.json'])` -> surfaces=[] with the
             # guard absent, i.e. the exact dependency-manifest diff the guard
             # exists for would never run it (the #1349/#3332/#3616 silent-drop
             # class). Named per file, not per directory: the guard reads these
             # two files only, and a wholesale `apps/graph-viz/` claim would
             # pull the whole `core` set onto every source edit in that app.
             # blog-admin needs no entry — `website/apps/blog-admin/` is already
             # a SOURCE_PATTERNS row of `onboarding`, where the guard's other
             # arm is registered.
             "apps/graph-viz/package.json",
             "apps/graph-viz/package-lock.json",
             "website/apps/dashboard/package.json",
             "website/apps/dashboard/package-lock.json",
             # PR #9652 review (P2): `tests/e2e/test_legal_pages.py` is the third
             # subject of the served-root marker contract in
             # `tests/test_website_static.py::TestProductRootMarkerContract` (it
             # holds the `PRODUCT_ROOT_MARKER` literal that guard parses). The
             # `onboarding` SOURCE_PATTERNS row above is what makes that guard run
             # on an e2e-only edit — but a named-surface match REPLACES the
             # `tests/` -> `core` fallback, so without this entry the same diff
             # would lose the `core` half: every core-registered test (including
             # the ones reading this e2e harness) would silently drop out of the
             # selection, trading one silent drop for another (the #7947 lesson on
             # `tests/eval/write_path/`). Both halves are asserted by
             # tests/test_ci_selection.py::
             # test_product_root_marker_guard_runs_on_a_legal_pages_only_diff.
             "tests/e2e/test_legal_pages.py",
             # CLI-extraction lane (#9915): the flat `integrations/` prefix in
             # NON_PYTHON_PREFIXES swallows the extractor, so an
             # extractor-only diff comes back `changed=[]`, select() takes the
             # early return and bypasses the `matched.add("core")` fallback —
             # not one of the lane's guards (`tests/test_cli_extraction.py`,
             # registered under `surfaces: core:` and `carve_out:`) would run
             # on the very change that can break them (#6138-review-P1 class).
             # Kept here so `_selection_relevant` claims it in `_keep_changed`
             # and the core fallback stays alive.
             "integrations/cli-extraction/extract_turns.py",
             # #5086 review P1-2: the substrate map's drift guard
             # (`tests/test_substrate_map.py`, registered `core` + `tier1`) has TWO
             # arms a filter can swallow, and this entry closes the tool half.
             # `tools/` is a flat NON_PYTHON_PREFIXES entry and nothing in
             # SOURCE_PATTERNS matches the tool, so a `tools/substrate_map_check.py`
             # -only diff comes back `changed == []` and takes the docs-only early
             # return, which emits the `tier1` set — so the tool's own test DOES
             # run without this entry, but only at tier-1 BREADTH, and nothing in
             # the `core` set does. MEASURED at the cycle-10 head (`select()`
             # in-process, entry deleted): surfaces=[], full=False, and the pin
             # PRESENT from the `tier1` row. ⛔ An earlier wording here claimed the
             # pin went ABSENT; that was measured in the same pass that ADDED the
             # `tier1` row, so it described a state the row had already retired
             # (#5086 review P3, cycle 10 — a past state carried as a current one).
             # So this entry buys the `core` surface on a tool-only diff, not the
             # pin. After it: surfaces=['core'], pin present. The DOC half
             # (`docs/architecture/SUBSTRATE-REBUILD-MAP.md`) is deliberately NOT
             # claimed here: a docs-only diff must stay on the tier-1 path, which is
             # why the file is dual-registered there — see the `tier1` row's comment
             # and config/ci-surfaces.yml's `core`-home row.
             # Pinned by tests/test_ci_selection.py::
             # test_substrate_map_guard_runs_on_the_diffs_it_guards.
             "tools/substrate_map_check.py")
NON_PYTHON_PREFIXES = (
    "docs/", "website/", "product/", "legal/", "growth/", "engineering/",
    "finance-accounting/", "menu-bar/", "ux/", "data/", "operations/",
    "capability/", "services/", "integrations/", "apps/", "spike/", "tools/",
    ".ci-checks/", "supabase/",
)

# ROOT-LEVEL files with a VERIFIED ZERO-READER census (#6784).
#
# ⛔ THE HEADLINE CASE OF #6784 IS NOT FIXABLE THIS WAY. The issue assumed a
# root-level prose file is "not python-relevant". In THIS repo it usually is:
# the root files are deliberately PINNED by tests across several surfaces, so
# allowlisting one makes its guard skip on exactly the PR that edits it (measured
# on README.md and `test_embedded_durability_claim.py`, review cycle 1), and a
# single-surface CORE_ALSO claim is no safer because its readers span `core`,
# `api` and `tests/bench`.
#
# Membership is ONLY a root file with NO reader. The rule is enforced by
# `tests/test_ci_selection.py::test_no_allowlisted_root_file_has_a_reader`, which
# derives the readers per member rather than listing names.
#
# ⛔ SCOPE OF THAT GUARD, stated rather than overclaimed (review cycle 4, P2):
# it is a LITERAL-NAME grep over the roots `tests tools scripts/ .github
# tortoise`. It does NOT see a reader that finds a root file by GLOB or walk
# (`REPO.glob("*.md")`), nor one outside those roots (e.g. `docs/`). Both were
# demonstrated GREEN against it. So this tuple is safe against the readers the
# census searched, NOT against every conceivable reader — before adding a name,
# grep for it yourself across the WHOLE tracked tree.
#
# A name that earns a guard belongs in SOURCE_PATTERNS/CORE_ALSO instead — which
# is also why `_keep_changed` tests a CLAIM before this tuple.
#
# These three are stray committed artifacts under a dot-prefix or an obvious
# scratch name, not project files a reader could be pinned to.
ROOT_NON_PYTHON_FILES = frozenset({
    ".scope-1894.md", ".scope-comment-2578.md", "pr-body.md",
})


def _is_safe_root_file(path: str) -> bool:
    """True only for a ROOT-level path NAMED in the #6784 allowlist.

    Root-level only (any path containing "/" is never safe here, so a
    subdirectory doc keeps whatever behaviour its prefix/fallback already had),
    and NAME-only: an earlier revision also accepted any root `*.md`, which
    admitted README.md — a file a guard READS
    (`tests/test_embedded_durability_claim.py:62`) and which is therefore NOT
    exempt by NON_CODE_DOC_PATHS either — so the pattern clause was removed.
    Root PROSE with no content reader is handled by NON_CODE_DOC_PATHS below
    instead (#8785); the two lists are disjoint, and both fail SAFE (a missing
    entry keeps the full matrix).
    """
    return "/" not in path and path in ROOT_NON_PYTHON_FILES


# NON-CODE DOCUMENTATION paths that reserve no test surface (#8785).
#
# A prose file OUTSIDE every NON_PYTHON_PREFIXES directory — at the repo root or
# under `.github/` — matches no prefix and no SOURCE_PATTERNS entry, so before
# this it fell through to the unknown-path branch and reserved the FULL matrix:
# a one-line `CONTRIBUTING.md` or `.github/pull_request_template.md` edit ran
# `test (a)…(i)`, `test-carve-out (a)(b)(c)`, `test-slow (a)(b)` and more
# (measured on PR #8726: 2 files, 0 Python, ~17 test jobs).
#
# This is the DISTINCTION #8785 asks for: "no surface matched because the file is
# not code" (drop it -> the tier-1 smoke, as `docs/` already does) versus "no
# surface matched because the path is unclassified and might carry code" (the
# unknown-path branch below, which still fails closed to FULL). Membership is
# EXACT PATH — deliberately NOT a `*.md` rule and NOT a `.github/` prefix rule:
# `.github/workflows/*.yml`, `Makefile`, `pyproject.toml`, `uv.lock` and any
# new/unknown path keep the full matrix.
#
# The membership test is the CONTENT-READER census, NOT a mention: a doc a guard
# READS stays out. `README.md` (tests/test_embedded_durability_claim.py:62,
# tests/test_capacity_page.py:40), `AGENTS.md` (the base-subset pin),
# `.gitignore` (tests/test_kind_index.py:290) and `.env.example` are therefore
# NOT here. `CONTRIBUTING.md` was left out of #6786's two-file reprieve on a
# MENTION census ("the surface tooling"); re-derived here, every mention is a
# docstring, a generated-doc link, or `scripts/record-review.sh`'s basename case
# — no test or tool reads its CONTENT, so the mention was not a reader.
#
# Ratchets: `test_non_code_doc_paths_exist` (liveness) and
# `test_non_code_doc_paths_have_no_content_reader` (the read census).
NON_CODE_DOC_PATHS = frozenset({
    ".github/pull_request_template.md",
    "CHANGELOG.md",
    "CLAUDE.md",
    "CONTRIBUTING.md",
})


def _is_non_code_doc(path: str) -> bool:
    """True only for an exact path in NON_CODE_DOC_PATHS.

    A named seam (rather than an inline `in NON_CODE_DOC_PATHS`) so tests can
    monkeypatch it — that is how the non-vacuity check proves the same path
    selected the FULL matrix before this tuple existed.
    """
    return path in NON_CODE_DOC_PATHS


# Linter-policy files, by BASENAME at ANY DEPTH (#7435).
#
# cli2 reads a `.markdownlint*` config from any directory on the path to a linted
# file, and a more specific config OVERRIDES the repo one — so a nested
# `docs/.markdownlint.json` turns a rule off for every doc beneath it. That makes
# such a file a POLICY change wherever it sits, but the prefix fallback below
# dropped it (`docs/` is in NON_PYTHON_PREFIXES): a policy-only PR then selected
# tier-1, where `test_committed_policy_map_matches_the_checkout` never runs, AND
# the `docs` job's differ steps were skipped for want of a changed `.md` — so the
# change landed with the snapshot unmoved and moved no ledger.
#
# A PREFIX, not the differ's exact basename set: a future `.markdownlint-*` name
# cannot silently fall outside the rule, which is the drift the hand-maintained
# duplicate tuples elsewhere in this file exist to warn about.
_LINTER_POLICY_PREFIXES = (".markdownlint", ".lycheeignore")


def _is_linter_policy_path(path: str) -> bool:
    """Whether a path is a linter-policy file, at any depth, by basename."""
    name = path.rsplit("/", 1)[-1]
    return name.startswith(_LINTER_POLICY_PREFIXES) or name == "lychee.toml"


# website/ paths that ARE selection-relevant (#3332).
#
# Superseded by the generic rule in select() (`_selection_relevant`): a path that
# SOURCE_PATTERNS already matches is selection-relevant whatever prefix it sits
# under, so it no longer needs a second hand-maintained tuple. Kept empty as a
# documented tombstone rather than deleted, so the next reader finds the reason
# instead of re-inventing the same broken mirror.
#
# Why the mirror was the wrong shape (twice: #1349 for tools/, #3332 for
# website/): the mirror is only as complete as whoever last edited it, and a
# missing entry fails SILENTLY — the path is filtered to `changed == []`, the PR
# drops to tier-1 smoke, and the guard test written for that exact file never
# runs. SOURCE_PATTERNS is the single source of truth; the ratchet that keeps
# this true is tests/test_ci_selection.py::test_every_source_pattern_is_selectable.
SITE_CARVEOUTS: tuple[str, ...] = ()

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
    # #2071: the eval-lane ask QA spot-check tools (ask_spotcheck + the
    # consistency/probe harnesses) use the eval judge and own the
    # test_ask_spotcheck_judge.py suite — a spot-check-only change must
    # select the sdk ask-lane surface, not drop to tier-1 smoke.
    "tools/ask_spotcheck.py",
    "tools/ask_spotcheck_consistency.py",
    "tools/ask_spotcheck_probe.py",
    # #3914: the transcript-golden generator's seeder is what
    # tests/test_ask_seed_shape.py + tests/test_ask_regression_llm.py pin —
    # same carve-out as the spot-check harnesses above.
    "tools/gen_ask_transcripts.py",
    # B6 objective 4: the answer-shape instrument — same carve-out reason
    # as gen_ask_transcripts.py above (without it the flat "tools/" prefix
    # silently drops the path to tier-1 smoke).
    "tools/ask_shape_rate.py",
    # #2159 review P2-3: the diff-gate selector itself must never classify
    # as docs-only (the two gated legs would skip AND the wiring pins in
    # tests/test_ci_selection.py would never run on the PR that owns them).
    # No source pattern matches tools/ci_selection.py, so a selector-only PR
    # lands in the unknown-path fail-closed branch -> FULL matrix + both
    # legs — the heaviest but safest gate for the file that owns gating.
    "tools/ci_selection.py",
    # #B7 (#3674, restated by #3676): the activation-scorecard cohort client.
    # Its transport guards (https-only, no-redirect, credential hygiene) are
    # security controls pinned by tests/test_activation_scorecard.py; without
    # this carve-out a client-only change selects NO surface and the suite that
    # pins them never runs. No SOURCE_PATTERN matches this path, so it lands in
    # the unknown-path fail-closed branch -> FULL matrix + both legs, which is
    # the safe outcome for a metrics number and for the platform's most
    # privileged credential. (The cohort FOLD moved out of this file and into
    # tortoise/activation_scorecard.py in #3676 — which the `core` surface
    # already covers, so the carve-out's reason narrowed rather than vanished.)
    "tools/activation_cohort.py",
    # #3261: the pre-dispatch collision check (#3061) owns
    # tests/test_collision_preflight.py. Without this carve-out a
    # preflight-only change is swallowed by the flat "tools/" prefix,
    # `changed` comes back empty, and select() takes the docs-only path
    # (surfaces=[] -> tier-1 smoke) — so the file's own guard test never
    # runs on the PR that changes it. The assumption that such a change
    # already "falls back to core" was never true: the early docs-only
    # return bypasses the `if not matched: matched.add("core")` fallback
    # entirely.
    # No SOURCE_PATTERNS entry matches it, so like tools/ci_selection.py
    # it lands in the unknown-path branch -> FULL matrix (fail closed).
    # A narrower core-only mapping is possible but not needed: a
    # collision-check change is rare and fail-closed is the safe default.
    "tools/collision_preflight.py",
    # #4408: the local-branch reaper (tools/branch_reaper.py) owns
    # tests/test_branch_reaper.py. Same silent-drop class as the preflight
    # carve-out above: the flat "tools/" prefix would swallow a reaper-only
    # change, `changed` comes back empty, select() takes the docs-only path and
    # the reaper's mutation tests never run on the PR that changes it. No
    # SOURCE_PATTERNS entry matches it, so it lands in the unknown-path branch
    # -> FULL matrix (fail closed) — the safe default for a destructive-ref tool.
    "tools/branch_reaper.py",
    # #7017: the container-engine probe (tools/engine_probe.py) owns
    # tests/test_engine_probe.py. Exact same silent-drop class as the two
    # reaper carve-outs below: the flat "tools/" prefix in
    # NON_PYTHON_PREFIXES would swallow an engine_probe-only change, `changed`
    # comes back empty, select() takes the docs-only path, and the probe's
    # suite never runs on the PR that changes it. No SOURCE_PATTERNS entry
    # matches it, so it lands in the unknown-path branch -> FULL matrix
    # (fail closed) — the safe default for the tool that decides whether a
    # graph failure is a graph failure at all.
    "tools/engine_probe.py",
    # #6868: the wedged-CI-run reaper (tools/run_reaper.py) owns
    # tests/test_run_reaper.py. Exact same silent-drop class as the
    # branch-reaper carve-out above: the flat "tools/" prefix in
    # NON_PYTHON_PREFIXES would swallow a reaper-only change, `changed` comes
    # back empty, select() takes the docs-only path, and the run-reaper's
    # mutation tests never run on the PR that changes it. No SOURCE_PATTERNS
    # entry matches it, so it lands in the unknown-path branch -> FULL matrix
    # (fail closed) — the safe default for a tool that CANCELS CI runs.
    "tools/run_reaper.py",
    # #2573: the CI embedder gate (tools/embedder_provision.py) owns
    # tests/test_embedder_provision.py. Same silent-drop class as the
    # preflight carve-out above: no SOURCE_PATTERNS entry matches it, so a
    # change to the gate alone would classify as docs-only (surfaces=[] ->
    # tier-1 smoke) and its own wiring/behaviour tests would never run on the
    # PR that edits it.
    "tools/embedder_provision.py",
    # #4290: the finding-provenance gate (tools/finding_provenance.py) owns
    # tests/test_finding_provenance.py. Same silent-drop class as the
    # collision-preflight carve-out above: the flat "tools/" prefix in
    # NON_PYTHON_PREFIXES swallows the path, `changed` comes back empty, and
    # select() takes the docs-only return — so the gate's own falsification
    # suite would never run on the PR that changes the gate. No SOURCE_PATTERNS
    # entry matches it, so it lands in the unknown-path branch -> FULL matrix
    # (fail closed). Pinned by
    # test_ci_selection.test_finding_provenance_tool_change_fails_closed_to_full.
    "tools/finding_provenance.py",
    # #3827: the embedded-lane evidence harness owns
    # tests/test_embedded_evidence.py. Same silent-drop class as the preflight
    # carve-out above: no SOURCE_PATTERNS entry matches the path, so a
    # harness-only change is swallowed by the flat "tools/" prefix, `changed`
    # comes back empty, and select() takes the docs-only return — the harness's
    # own guard test never runs on the PR that edits the harness. That is the
    # exact "proxy silent in the case it exists to cover" class this harness is
    # written to detect, so it must not apply to the harness itself.
    "tools/embedded_evidence.py",
    # #2718/#4860: the eval-key isolation launcher owns
    # tests/test_run_with_eval_keys.py. Same silent-drop class as the
    # collision-preflight carve-out above: the flat "tools/" prefix in
    # NON_PYTHON_PREFIXES swallows `tools/run-with-eval-keys.sh`, so a
    # wrapper-only change (e.g. a fingerprint-format edit, or a change to the
    # managed-key set) would come back as `changed=[]`, select() would take the
    # docs-only return, and the wrapper's own guard suite would never run on
    # the PR that changed the wrapper. No SOURCE_PATTERNS entry matches a `.sh`
    # path, so it lands in the unknown-path branch -> FULL matrix (fail closed)
    # — the safe default for the file that owns provider-key isolation.
    # Pinned by
    # test_ci_selection.test_run_with_eval_keys_tool_change_fails_closed_to_full.
    "tools/run-with-eval-keys.sh",
    # #4503: the edge census/probe (tools/edge_census.py) owns
    # tests/test_4503_edge_relationship_accounting.py. Same silent-drop class
    # as every carve-out above: the flat "tools/" prefix in
    # NON_PYTHON_PREFIXES swallows the path, `changed` comes back empty, and
    # select() takes the docs-only return (surfaces=[] -> tier-1 smoke) — so
    # the instrument's own guard tests would never run on the PR that changes
    # the instrument. Verified live before adding: `--changed-files
    # tools/edge_census.py` returned `"surfaces": []`. No SOURCE_PATTERNS
    # entry matches the path, so it lands in the unknown-path branch -> FULL
    # matrix (fail closed), the same treatment tools/finding_provenance.py and
    # tools/embedded_evidence.py get. Pinned by
    # test_ci_selection.test_edge_census_tool_change_fails_closed_to_full.
    "tools/edge_census.py",
    # #5050: the measured-durations-map validator (tools/ci_manifest.py) owns
    # tests/test_ci_manifest.py. Same silent-drop class as the
    # collision-preflight carve-out above: no SOURCE_PATTERNS entry matches the
    # path, so a validator-only change is swallowed by the flat "tools/" prefix
    # in NON_PYTHON_PREFIXES, `changed` comes back empty, and select() takes the
    # docs-only return — the validator's own verdict-boundary tests would never
    # run on the PR that changes the validator. Registering
    # tests/test_ci_manifest.py under `surfaces: core` does NOT cover this: the
    # early docs-only return bypasses the `matched.add("core")` fallback, so a
    # tool-only change selects NO surface at all. It lands in the unknown-path
    # branch -> FULL matrix (fail closed). Pinned by
    # test_ci_selection.test_ci_manifest_tool_change_fails_closed_to_full.
    "tools/ci_manifest.py",
    # #5050: the sole writer of the measured `durations` map (tools/ci_timing.py)
    # is pinned by the same suites as the validator it feeds:
    # tests/test_ci_manifest.py holds the `VALUE_FLOOR ==
    # ci_timing.DURATIONS_VALUE_FLOOR_S` pin and the one-decimal render pin
    # (`test_the_floor_is_the_writers_own`,
    # `test_the_writers_precision_is_the_one_this_check_assumes`) — the two
    # contracts the validator ASSUMES about the writer — and
    # tests/test_ci_timing.py covers the writer's refresh path. A writer-only
    # change matches no SOURCE_PATTERNS entry, so without this carve-out the
    # flat "tools/" prefix swallows it, `changed` comes back empty and select()
    # takes the docs-only return — a writer drift would merge green and red
    # `main` with neither suite having run. It lands in the unknown-path branch
    # -> FULL matrix (fail closed). Pinned by
    # test_ci_selection.test_ci_timing_tool_change_fails_closed_to_full.
    "tools/ci_timing.py",
    # #6139: the PR lead-time decomposition owns tests/test_pr_lead_time.py,
    # whose guards pin its residence rule (a zero-length `Mergify Merge Queue`
    # run is a queue EVALUATION, not residence — measured 6.6x on PR #6106), its
    # gate clamp into the PR's own life (a gate earlier than created_at made
    # seconds_a negative and aborted the WHOLE run on PR #5137), and its
    # exit-code contract (an unobserved read is UNKNOWN, never 0). Same
    # silent-drop class as the carve-outs above: the flat "tools/" prefix in
    # NON_PYTHON_PREFIXES swallows `tools/pr_lead_time.py`, so a tool-only edit
    # comes back as `changed=[]`, select() takes the docs-only early return —
    # which bypasses the `if not matched: matched.add("core")` fallback
    # entirely — and NOT ONE of those guards runs on the very change that can
    # break them. An earlier attempt relied on the ci-surfaces.yml `core`
    # registration alone and was wrong for exactly this reason (the registration
    # covers the TEST file's own edits, not the tool's). No SOURCE_PATTERNS
    # entry matches this path, so it lands in the unknown-path branch -> FULL
    # matrix (fail closed).
    # Pinned by test_ci_selection.test_pr_lead_time_tool_change_fails_closed_to_full.
    "tools/pr_lead_time.py",
    # #4256: the "who is on #N?" verb (tools/who_is_on.py + its wrapper
    # tools/who-is-on.sh) owns tests/test_who_is_on.py. Same silent-drop class
    # as the collision_preflight carve-out above: no SOURCE_PATTERNS entry
    # matches `tools/who_is_on.py`, so a change to the tool alone would be
    # swallowed by the flat "tools/" prefix, classify as docs-only, and its own
    # guard test would never run on the PR that changes it. Fail-closed (FULL
    # matrix) is the safe default for a tool whose answer drives dispatch.
    "tools/who_is_on.py",
    "tools/who-is-on.sh",
    # #5052/#9487 review P1: the dispatch-time CLAIM on the graph
    # (tools/work_claim.py) owns tests/test_work_claim.py — the 87 hermetic arms
    # that pin the fail-closed refusal contract (`acquire`/`status`/`release`
    # refuse an unset store, a hosted-only target, an embedded target, a
    # box-local target, an unreadable store and a failed read-back) plus the
    # MERGE-token CAS. Same silent-drop class as the collision_preflight /
    # who_is_on carve-outs above: `tools/` is a flat NON_PYTHON_PREFIXES entry, so
    # a tool-only edit came back with `changed=[]`, select() took the docs-only
    # early return — which bypasses the `if not matched: matched.add("core")`
    # fallback entirely — and NOT ONE of the 87 arms ran on the most likely
    # change to weaken the dispatch gate. Registering tests/test_work_claim.py
    # under `core` in ci-surfaces.yml does NOT cover this: it applies to edits of
    # the TEST file, not of the tool (measured before this entry:
    # `select(["tools/work_claim.py"])` -> surfaces=[], full=False, tier-1 smoke,
    # guard absent). Fail-closed (FULL matrix) is the safe default for a tool
    # whose answer drives dispatch, exactly like the two entries above.
    # Pinned by test_ci_selection.test_work_claim_tool_change_fails_closed_to_full.
    #
    # PREFIX, not the exact file (review of #9487 P2): `_keep_changed` matches with
    # `startswith`, so the exact `tools/work_claim.py` string failed closed for that ONE path
    # and nothing else — a rename, or the store split out as `tools/work_claim_store.py`,
    # re-opened the same silent drop with no failing test (measured: `tools/work_claim_extra.py`
    # → surfaces=[], full=False, tier-1 smoke). The store seam (`SDKClaimStore`,
    # `_require_store_configured`) is the half whose breakage must never ship unreviewed, so
    # the carve-out covers the family. Over-matching costs extra CI; under-matching ships a
    # dispatch gate with no arms.
    "tools/work_claim",
    # #3554: the `:Session` writer inventory + D9 retrofit instrument
    # (tools/session_retrofit_audit.py) owns
    # tests/test_session_schema_3554.py. Same silent-drop class as the
    # carve-outs above: the flat "tools/" prefix in NON_PYTHON_PREFIXES
    # swallows the path, so a tool-only change comes back as `changed=[]`,
    # select() takes the docs-only early return — which bypasses the
    # `if not matched: matched.add("core")` fallback entirely — and the
    # inventory/D9 guard never runs on the very change that can widen or
    # narrow the predicate. Registering tests/test_session_schema_3554.py
    # under `core` in ci-surfaces.yml does NOT cover this: it applies to
    # edits of the TEST file, not of the tool. No SOURCE_PATTERNS entry
    # matches the path, so it lands in the unknown-path branch -> FULL
    # matrix (fail closed).
    # Pinned by test_ci_selection.
    # test_session_retrofit_audit_tool_change_fails_closed_to_full.
    "tools/session_retrofit_audit.py",
    # #8375 item 1: the required-check repair. The workflow and the launchd
    # fallback both run `tools/ci_check_repair.py` with `--apply` on a schedule,
    # unattended, with the ability to CLOSE and REOPEN pull requests and to re-run
    # workflow runs. Its suite (tests/test_ci_check_repair.py) is registered in the
    # `ci-surfaces.yml` `core` surface — and that registration covers the TEST
    # FILE's own edits, NOT the tool's: no SOURCE_PATTERNS entry matches
    # `tools/ci_check_repair.py`, so a tool-only diff is swallowed by the flat
    # "tools/" prefix, `changed` comes back empty, select() takes the docs-only
    # early return (which bypasses the `if not matched: matched.add("core")`
    # fallback entirely), and NOT ONE of the 74 guards runs on the very change
    # that can break them. Measured: `--changed-files tools/ci_check_repair.py`
    # returned surfaces=[] full=False before this carve-out. `--integrity` cannot
    # catch it — it only asks whether the test file is CLASSIFIED. The same
    # applies to the launchd entry point (the `bash -s`/TCC handoff), the local
    # fallback, and the installer whose plist decides whether the job is armed.
    # None of the four matches a SOURCE_PATTERNS row, so they land in the
    # unknown-path branch -> FULL matrix (fail closed), which is the safe default
    # for a tool that mutates other PRs.
    # Pinned by test_ci_selection.test_ci_check_repair_tool_change_fails_closed_to_full.
    "tools/ci_check_repair.py",
    "tools/ci_check_repair_launchd.py",
    "tools/ci-check-repair-local.sh",
    "tools/install-ci-check-repair-schedule.sh",
)


def _surface_members(value) -> list:
    """A manifest surface value as a member list (#3073).

    ``None`` means "empty surface" (a plausible hand-edit or bad-merge
    artifact) and a non-list scalar is malformed. Both coerce to ``[]``
    rather than crashing every consumer with
    ``TypeError: argument of type 'NoneType' is not iterable`` or, worse,
    iterating a string character-by-character. The drift gate still reports
    the orphans, so an empty surface fails loudly later rather than silently
    at selection time.
    """
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _normalize_surfaces(manifest: dict) -> dict:
    """Coerce every surface block to a member list, in place (#3073)."""
    surfaces = manifest.get("surfaces")
    if isinstance(surfaces, dict):
        manifest["surfaces"] = {
            s: _surface_members(v) for s, v in surfaces.items()}
    return manifest


def load_manifest() -> dict:
    import yaml  # local import (uv provides pyyaml via the dev group)
    return _normalize_surfaces(yaml.safe_load(MANIFEST.read_text()))


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
        members = _surface_members(files)
        if name in members or base in members:
            return surface
    return None


# #2147/#2148 (2026-09-02-ci-audit F1/F2): surface -> slow-leg / carve-out
# file ownership maps. The test-slow and test-carve-out jobs were the two
# diff-unaware python-ci legs (docs-only PR #2132 paid 29m43+29m08 slow +
# 23m31 carve-out); select() now emits slow_run / slow_selected /
# carve_out_run on every return path so the workflow can diff-gate them.
def slow_leg_by_surface(manifest: dict) -> dict[str, set[str]]:
    """Surface -> slow files that run in test-slow (the GATING slow pool).

    The slow carve-out files (test_reaper et al.) run in the dedicated
    URI-unset carve-out job (epic #1647 E2E-4), never the docker slow legs
    — the test-slow leg set is exactly `slow_gating_files()`: slow_files minus
    carve_out minus the non-gating lane (#10194) (pinned by the workflow's
    drift-guard step, #1471).

    First-match semantics (#2159 review P2-2): classify_test_file returns
    the FIRST surface whose patterns match (dict order api, battery, core,
    ep, onboarding, sdk, classify, eval). A slow/carve file registered
    under two surfaces therefore owns to the first — a tier-2 PR touching
    ONLY the second surface will not run it (full runs stay the backstop).
    test_diff_gate_keys_emitted_on_every_return_path pins every
    slow_selected subset to slow_gating_files(); the tier-2 intersection
    tests pin the per-surface splits."""
    # #6884: the test-slow legs are URI-empty on a tier-2 PR too (python-ci.yml
    # :1367-1368; :600-602 for the fast job — URI is set only when full==true),
    # and they build their collect-only input from `slow_selected`. A
    # `uri_requiring` file in `slow_selected` therefore contributes no tests to
    # that step, and when it is the leg's ONLY file the collect step exits 5 and
    # fails unattributably — the same shape as the fast lane, which is what
    # stranded #6390. Subtracting HERE is single-definition: this function has
    # exactly one consumer (the tier-2 selection), while `push_legs` builds its
    # own legs, so the push lane still runs these files (asserted in
    # tests/test_uri_requiring_selection.py). No file is in both lanes today;
    # the guard test pins that, and handles a future relocation rather than
    # forbidding it.
    # #10194: the POOL is `slow_gating_files()` — the single definition that also
    # drops the non-gating lane — so a lane file can never ride the tier-2 slow
    # leg even when the surface the PR touched is the one that owns it. The
    # `slow`/`carve` bindings that used to be here are GONE rather than chained
    # (three expressions of the same pool is the drift this consolidates), which
    # is why the two local assignments above them were deleted, not re-pointed:
    # a leftover `slow` would be an unused binding that ruff rejects (F841) and
    # a second, silently-wrong definition for the next reader to reuse.
    uri_requiring = uri_requiring_files(manifest)
    by_surface: dict[str, set[str]] = {}
    for f in sorted(slow_gating_files(manifest) - uri_requiring):
        by_surface.setdefault(classify_test_file(f, manifest), set()).add(f)
    return by_surface


def carve_out_by_surface(manifest: dict) -> dict[str, set[str]]:
    """Surface -> carve-out files it owns (the E2E-4 embedded set)."""
    by_surface: dict[str, set[str]] = {}
    for f in sorted(manifest.get("carve_out", [])):
        by_surface.setdefault(classify_test_file(f, manifest), set()).add(f)
    return by_surface


def _full_selection(manifest: dict) -> dict:
    """push/schedule/shared-module/unknown-path selection (tier 3).

    The trunk + nightly backstop: full matrix AND both diff-gated legs run
    (test-slow + test-carve-out) — main can never lose slow/carve-out
    coverage to a PR-shape gate."""
    # #10194: the full selection's slow leg is `slow_gating_files()` — the ONE
    # definition of the gating slow pool (slow - carve-out - non-gating lane) —
    # so "evals paused" is true on the push/trunk path too, not only on a PR.
    # There is deliberately NO pool parameter: the caller's `slow` set is the
    # pre-exclusion declaration, and threading it in here is how this function
    # would grow a second, quietly-different pool (#10206 review).
    gating_slow = slow_gating_files(manifest)
    return {"surfaces": list(manifest["surfaces"]), "full": True,
            "test_files": "ALL", "slow_files": sorted(gating_slow),
            # #2147/#2148: full selection always runs both legs; slow_selected
            # carries the whole GATING leg set — `slow_gating_files()` (slow minus
            # carve-out minus the non-gating lane), which is the same pool the
            # `slow_files` key above emits.
            "slow_run": True, "carve_out_run": True,
            "slow_selected": sorted(gating_slow)}


def select(changed_files: list[str], event: str, manifest: dict,
           explain: dict | None = None) -> dict:
    """Selection for a changed-path list.

    *explain*, when passed a dict, is filled with WHY a fail-closed answer was
    chosen, so `main()` can say it on stderr. The contract is:

        {"reasons": [<cause>, ...]}

    — EVERY cause of THIS call's FULL matrix, in scan order — plus
    `"reason": "unknown-paths"` and `"unknown": [...]` ONLY when
    `"unknown-paths"` is among them. `reasons` is the authoritative field: a
    legitimate FULL selection with no unknown path at all (a `SHARED_MODULES`
    entry the surface patterns also claim, a linter-policy file) records
    `reasons` and nothing else, so a caller that reads `explain["reason"]` once
    `explain` is non-empty raises `KeyError` — e.g. `tortoise/sdk.py`, a
    `SHARED_MODULES` entry the `tortoise/` prefix fallback claims so it never
    reaches `unknown`, or `battery/.markdownlint.json`, a policy file under a
    claimed surface (a dependency-bump diff like `pyproject.toml` DOES carry
    `reason`, because that path is unknown to the surface patterns as well and
    both causes are recorded). Read `reasons`.

    A fourth key, `unknown_all_blast`, appears at the same time as `reason` and is
    INTERNAL to the diagnostic: it says whether every unrecognised path is also a
    blast-radius path (which decides whether the lead-in reads "the same path(s)"
    or "additionally, …"). Branch on `reasons`/`unknown`; treat
    `unknown_all_blast` as private and never assert the dict verbatim while it is
    present.

    It is a SIDE CHANNEL on purpose: the returned dict is the artifact schema
    (`.ci-selection/selection.json`) and is asserted verbatim up and down this
    suite, so a reason field inside it would be a migration; and the reason must
    not be re-derived by the caller, which is how the diagnostic would drift
    from the decision it describes (#9109). It is CLEARED on entry, so a caller
    may reuse one dict across calls.
    """
    if explain is not None:
        # A caller may reuse one dict across calls, and what it holds is only
        # ever about THIS call — so empty it here. That makes "empty unless THIS
        # call failed closed, in which case it names every cause" true by
        # construction, whatever a previous call recorded.
        explain.clear()
    slow = set(manifest.get("slow_files", []))
    # #10194: `slow_gating` is what every return path EMITS as `slow_files`, and
    # it is the same pool the legs are built from (`slow_gating_files()`: slow
    # minus carve-out minus the non-gating lane). Before #10194 the two were
    # equal, so emitting the raw config list was harmless; now they differ, and
    # emitting the raw list would hand the `changes` job a candidate pool whose
    # lane members the legs then refuse — a matrix that disagrees with the
    # selection, which is the #1266 drift class. `slow` is kept separately for
    # the two places that mean "is this file DECLARED slow" (the tier-1
    # subtraction and the full-vs-partial decision).
    slow_gating = slow_gating_files(manifest)
    # #1371: slow files run ONLY in the test-slow job — never in the fast
    # gate's tier-1/tier-2 selections (they are already covered there).
    # Every return path carries slow_files so the workflow's changes job can
    # always emit it (a missing key would KeyError the nightly/schedule run).
    # #2147/#2148: every return path ALSO carries slow_run / slow_selected /
    # carve_out_run (the test-slow + test-carve-out diff-gate contract).
    if event in ("push", "schedule"):
        return _full_selection(manifest)

    tier1 = set(manifest.get("tier1", [])) - slow
    # Filter out non-python-relevant paths — but a path that a SOURCE_PATTERNS
    # entry already matches is selection-relevant BY DEFINITION, whatever prefix
    # it sits under (#3332). Mirroring those paths into a second hand-maintained
    # tuple (the #1349 TOOL_CARVEOUTS shape, and this PR's own first attempt at
    # SITE_CARVEOUTS) drifts: a missing entry is silent, and the guard test
    # written for that exact file never runs. SOURCE_PATTERNS is the single
    # source of truth; test_every_source_pattern_is_selectable is the ratchet.
    def _selection_relevant(path: str) -> bool:
        return any(
            path.startswith(p)
            for surface, pats in SOURCE_PATTERNS.items()
            if surface != "core"
            for p in pats
        ) or path.startswith(CORE_ALSO)

    def _keep_changed(c: str) -> bool:
        """#6784: whether a changed path participates in surface selection.

        ORDER IS THE INVARIANT. A path that something else CLAIMS comes first:
        the carve-outs and `_selection_relevant` (SOURCE_PATTERNS + CORE_ALSO)
        win, and `_is_safe_root_file` is only a FALLBACK for a path nothing
        claims. Testing `_is_safe_root_file` first — which an earlier revision
        did — silently disabled the repo's own documented remedy for the
        "the guard for file X never runs when X changes" class
        (#1349/#3332/#3485, and the #6138 review P1 that put
        `tools/tmpdir_sweep.py` in CORE_ALSO): listing X in SOURCE_PATTERNS or
        CORE_ALSO could no longer rescue it, and any future root `.md` guard
        pin would have inherited the same trap.
        """
        if c.startswith(SHARED_MODULES):
            return True
        if c.startswith(TOOL_CARVEOUTS) or c.startswith(SITE_CARVEOUTS):
            return True
        if _selection_relevant(c):
            return True
        # #7435: a linter-policy file is a CLAIM too (see
        # `_is_linter_policy_path`), so it is kept BEFORE the
        # `_is_safe_root_file` fallback below.
        if _is_linter_policy_path(c):
            return True
        # #8785: a prose doc no guard READS is not python-relevant. Dropping it
        # here means a docs-only diff reaches the docs-only early return
        # (tier-1 smoke) instead of the unknown-path fail-closed branch. Ordered
        # AFTER every CLAIM above, so a SOURCE_PATTERNS/CORE_ALSO entry still
        # rescues a doc that gains a reader (the repo's documented remedy,
        # #6138 review P1).
        if _is_non_code_doc(c):
            return False
        if _is_safe_root_file(c):
            return False
        return not c.startswith(NON_PYTHON_PREFIXES)

    changed = [c for c in changed_files
               if c and _keep_changed(c)]
    if not changed:
        # docs-only PR -> tier 1 (curated smoke) only; no slow/carve surface
        # is touched, so both diff-gated legs skip (#2147/#2148).
        # #6884: the `uri_requiring` subtraction applies HERE TOO. This returns
        # before the tier-2 subtraction below, and a URI-less leg cannot run a
        # module-skipping file no matter which exit selected it. The tier-1
        # smoke set contains none today, so this is behaviour-neutral — it keeps
        # the rule in ONE place instead of depending on that staying true, and
        # the guard test pins it by injecting one into tier-1 (a plain arm on a
        # docs-only vector could not fail: with tier1 ∩ uri_requiring == []
        # the output is byte-identical with and without the subtraction).
        return {"surfaces": [], "full": False,
                "test_files": sorted(set(tier1) - uri_requiring_files(manifest)
                                     - on_demand_files(manifest)),
                "slow_files": sorted(slow_gating),
                "slow_run": False, "carve_out_run": False,
                "slow_selected": []}

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
        # #2938: a CORE_ALSO path keeps `core` even though a named surface
        # matched above (see CORE_ALSO) — otherwise its core-registered
        # pinners silently drop out of the selection.
        if any(c.startswith(p) for p in CORE_ALSO):
            matched.add("core")
            found = True
        if not found:  # noqa: SIM102
            if c.startswith("tortoise/") or c.startswith("tests/") or \
               c.startswith("graph-scripts/") or c.startswith("config/") or \
               c.startswith("validation/") or c.startswith("packs/"):
                matched.add("core")  # engine/registry code -> core surface
                found = True
        if not found:
            unknown.append(c)
    # EVERY cause of a fail-closed FULL matrix is recorded, not just the first
    # one found (#9109 review P1 on this branch). A `SHARED_MODULES` member or a
    # linter-policy file is FULL because its blast radius IS the whole matrix, so
    # labelling one of those "unknown to every SOURCE_PATTERN … NOT a statement
    # that this diff touches everything" inverts its meaning — and several
    # shared-module entries (`pyproject.toml`, `requirements.txt`,
    # `.github/workflows/python-ci.yml`, `.github/actions/`) are genuinely unknown
    # to the surface patterns, so the mislabel is reachable from the most
    # ordinary legit-full diff. (`len(SHARED_MODULES)` is the count; do not
    # restate it here, it decays.)
    shared_cause = any(c.startswith(SHARED_MODULES) for c in changed)
    # Linter POLICY is a FULL cause of its own (#7435 review P1), recorded here
    # rather than in a return of its own: a `.markdownlint*`/`.lycheeignore`/
    # `lychee.toml` file changes the linting OUTCOME of every doc beneath it, so
    # the selection must be the WHOLE matrix — the only selection containing
    # `tests/test_docs_lint_baseline.py`, whose committed-policy pin is what
    # makes a config edit move the snapshot. `_keep_changed` only stops such a
    # path being DROPPED as an unknown prefix; it does NOT stop the match loop
    # above CLAIMING it. Measured: with the claim alone,
    # `battery/.markdownlint.json` -> surfaces=['battery'] and
    # `tools/longmem_eval/.markdownlint.json` -> ['eval'], and neither leg runs
    # the pin — so the policy edit landed with the snapshot unmoved, which is
    # the very hole the pin exists to close. A shared module means the same
    # thing ("this path's blast radius is not its own directory"), and BOTH are
    # fulfilled by the single `if reasons:` return below.
    lint_cause = any(_is_linter_policy_path(c) for c in changed)
    reasons: list[str] = []
    if shared_cause:
        reasons.append("shared-module")
    if lint_cause:
        reasons.append("linter-policy")
    if unknown:
        reasons.append("unknown-paths")
    if reasons:
        # The ANSWER is right and deliberate (scope v5 decision 1); the DEFECT
        # (#9109) is that it is indistinguishable from a legitimate full
        # selection, so a caller who passed a list FILE as a changed file reads
        # "this diff selects everything" instead of "my argument was wrong".
        # Record EVERY reason so `main()` can say it out loud; the selection
        # itself is unchanged.
        if explain is not None:
            explain["reasons"] = reasons
            if unknown:
                # `reason`/`unknown` stay the unknown-path pair the diagnostic
                # keys on; `reasons` is what keeps the message truthful when
                # another cause is also present.
                explain["reason"] = "unknown-paths"
                explain["unknown"] = list(unknown)
                # Whether EVERY unrecognised path is also a blast-radius path.
                # `unknown` is NOT a subset of those (`tests/conftest.py` beside
                # `tools/ci_selection.py` leaves the latter unknown), and the
                # diagnostic's lead-in must say which shape it is looking at.
                blast = {c for c in changed
                         if c.startswith(SHARED_MODULES)
                         or _is_linter_policy_path(c)}
                explain["unknown_all_blast"] = set(unknown) <= blast
        return _full_selection(manifest)

    # No separate `SHARED_MODULES` / linter-policy returns here: both causes were
    # appended to `reasons` above, so the `if reasons:` return already answered
    # for them. Two such branches used to sit right here and were unreachable —
    # `reasons` is non-empty exactly when one of those causes holds — so they
    # read as live decisions when the decision had already been taken.

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
        files.update(_surface_members(manifest["surfaces"].get(s)))
    files -= slow  # #1371: slow files never run in the fast gate
    # #1988: carve-out (embedded-only) files run in the dedicated carve-out
    # job — on tier-2 PR legs the fast-matrix process runs everything embedded
    # (URI empty) and exhausts its redislite spawn budget before the late
    # embedded suites (RedisLiteServerStartError); the carve-out job gives
    # them a fresh process. The carve-out job now runs on PRs too.
    carve = set(manifest.get("carve_out", []))
    files -= carve
    # #6884: the MIRROR of the carve-out subtraction above. A `carve_out` file
    # cannot run in a URI-SET leg; these cannot run in a URI-EMPTY one, and the
    # tier-2 PR legs ARE URI-empty BY DESIGN — both the URI and EXPECT_URI are
    # emptied, which is what keeps the E2E-6 tripwire INERT on the tier-2 shape
    # (it arms only when full==true, where both are set; python-ci.yml:600-602
    # and 1367-1368; epic #1647 Task 9, cycle-6 P2-8), so 'provision the URI' is
    # a reversal of a recorded
    # decision, not an available fix. Handed to such a leg each of these
    # module-skips at import, so it collects ZERO tests and `pytest
    # --collect-only` exits 5; the fail-closed manifest guard then kills the leg
    # BEFORE any test runs, and because that is outside the test step there is
    # no `FAILED <nodeid>` for the merge rail to attribute — it refuses for want
    # of a failure identity (the #6798 shape; #6390 was clean-reviewed AT HEAD,
    # `mergeable=true`, `behind=0`, and still unlandable). A shard left with no
    # files hits the empty-shard branch at python-ci.yml:652 ('no selected files
    # for this shard - no manifest (guard skips)') and greys out green having run
    # nothing — honest only because a tier-2 selection cannot get that small
    # (`tier1` is 31 files against `fast_shards: 9`), which is a margin, not a
    # guarantee this comment should claim.
    #
    # Tier-2 ONLY: the `full` path returned above and keeps these files, because
    # a full leg HAS the URI.
    files -= uri_requiring_files(manifest)
    # #10194: and the NON-GATING LANE, on the tier-2 path too. The five eval
    # files in `slow_files` and the 37 in the fast pool are all still REGISTERED
    # in a surface, so without this subtraction a PR that touches the `eval`
    # surface re-selects all of them and they gate again — the exclusion would
    # hold on `push` and silently fail on every PR, which is the direction that
    # reads green. (`slow_selected` is covered by `slow_gating_files()`.)
    files -= on_demand_files(manifest)
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
            "slow_files": sorted(slow_gating),
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
    on_demand = on_demand_files(manifest)
    for f in sorted(TESTS_DIR.rglob("test_*.py")):
        rel = f.relative_to(TESTS_DIR)
        if rel.parts[0] == "e2e":
            continue
        # The on-demand lane is a CLASSIFICATION: the file is deliberately
        # absent from every surface because it does not gate the merge, so
        # asking "which surface owns it?" is the wrong question. Without this
        # the lane reads as drift and `--integrity` reds on a correct manifest.
        if str(rel) in on_demand or rel.name in on_demand:
            continue
        if classify_test_file(str(rel), manifest) is None:
            missing.append(str(rel))
    return missing


def duplicate_entries(manifest: dict) -> list[str]:
    """#2913: names listed more than once WITHIN one surface.

    `select()` unions surfaces, so a same-surface duplicate is invisible to
    selection and to :func:`integrity` (which only asks "is it classified?").

    #5373: this is a GATE FAILURE, not a note. `config/ci-surfaces.yml` carries
    `merge=union`, which keeps BOTH sides' lines for a conflicting hunk — so a
    duplicate same-surface entry is exactly what union emits when two lanes append
    the same registration, and a note would let it in silently. Cross-surface (dual)
    registration is still deliberate; only same-surface repeats are reported.

    Each offending name is reported ONCE however many times it repeats, so the
    count is a count of distinct problems.
    """
    dupes: list[str] = []
    for surface, files in manifest.get("surfaces", {}).items():
        seen: set[str] = set()
        reported: set[str] = set()
        for f in files or ():
            if f in seen and f not in reported:
                dupes.append(f"{surface}: {f}")
                reported.add(f)
            seen.add(f)
    return dupes


def unlisted_tests(tests_dir: Path, manifest: dict) -> list[str]:
    """Top-level tests/test_*.py absent from every surface in the manifest.

    Kept from origin/main's refactor — callers (workflow matrix checks)
    use the top-level glob; :func:`integrity` uses the recursive rglob form.
    """
    lane = on_demand_files(manifest)
    missing = []
    for f in sorted(tests_dir.glob("test_*.py")):
        if f.name in lane:
            # A lane file is classified — into a LANE, not a surface. Without
            # this, `--register` would "fix" the apparent gap by registering it
            # into a surface, silently putting it back on the merge gate. The
            # currently-laned file is in a subdir so it never reaches this glob;
            # the guard is here for the top-level file that eventually joins.
            continue
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

    Insertion lands *between real entry lines* (#2913): `pos` indexes the entry
    list (comment lines excluded), so it is converted to a physical line via the
    entry immediately before it — never used as a raw line offset, which would
    drop the new entry inside a comment run and detach the run from the entries
    it documents.
    """
    missing = unlisted_tests(tests_dir, manifest)
    if not missing:
        return []
    text = manifest_path.read_text()
    # #3073: a manifest whose final line lacks a newline would concatenate the
    # appended entry onto it (`  - test_b.py  - test_c.py`, malformed YAML).
    # Normalise the terminator before splitting so every insertion point is a
    # line boundary.
    if text and not text.endswith(NL):
        text += NL
    lines = text.splitlines(keepends=True)
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
        manifest_path.write_text("".join(lines))
        return missing

    # Scan the block (until the next "  x:" / non-list line), tracking each
    # real entry's PHYSICAL line index. Comments are prose about the group that
    # follows and must never be split by an insertion (#2913).
    end = block_start + 1
    entry_lines: list[int] = []
    while end < len(lines) and (lines[end].startswith("  - ") or lines[end].lstrip().startswith("#")):
        if lines[end].startswith("  - "):
            entry_lines.append(end)
        end += 1
    names = [lines[i].strip()[2:].split("#", 1)[0].rstrip() for i in entry_lines]
    # A file already present in THIS surface is a no-op, reported: writing it
    # again would duplicate the entry, and select() unions surfaces so a
    # same-surface duplicate is invisible downstream (#2913).
    already = [n for n in missing if n in set(names)]
    to_add = [n for n in missing if n not in set(names)]
    if already:
        print(f"ℹ️  already registered under {surface}: {already}")
    # INSERT-ONLY: place each new name at its alphabetical position among
    # the existing entries; never re-order pre-existing lines (keeps the
    # diff surgical — normalizing the whole block would churn the manifest).
    for n in sorted(to_add):
        pos = 0
        while pos < len(names) and names[pos] < n:
            pos += 1
        # `pos` indexes the entry list; convert to a PHYSICAL line by inserting
        # right after the preceding entry (or at the top of the block when the
        # name sorts first). This keeps comment runs contiguous and attached to
        # the entries they describe — the bug was `block_start + 1 + pos`, which
        # counted comment lines and landed inside a run.
        insert_at = block_start + 1 if pos == 0 else entry_lines[pos - 1] + 1
        lines[insert_at:insert_at] = [f"  - {n}{NL}"]
        names.insert(pos, n)
        entry_lines.insert(pos, insert_at)
        for j in range(pos + 1, len(entry_lines)):
            entry_lines[j] += 1
    if to_add:
        manifest_path.write_text("".join(lines))
    return to_add


def register(manifest_path: Path, tests_dir: Path, surface: str) -> list[str]:
    """Load + register in one call (CLI entry)."""
    import yaml
    manifest = _normalize_surfaces(yaml.safe_load(manifest_path.read_text()))
    return register_tests(manifest_path, tests_dir, surface, manifest)


# #1472: files excluded from the fast push legs by construction (they cannot
# run in THIS job — no live redis on 6379 for test_agent_signup; tests/e2e is
# excluded by construction since unlisted_tests only globs top-level files).
ENV_BROKEN_FILES = {"test_agent_signup.py"}


def carve_out_files(manifest: dict) -> set[str]:
    """Epic #1647 Task 9 (P3): the embedded carve-out set.

    The carve-out tests run embedded BY DESIGN (E2E-4) in the dedicated
    URI-unset job (TORTOISE_TEST_CARVE_OUT=1) — they are excluded from the
    docker fast legs AND the docker test-slow legs, so the docker lanes
    create ~zero redislite servers (E2E-7 orphan assert ≈ 0). The set
    mirrors tests/_embedded.TEST_NO_REDIRECT_STEMS (pinned by
    tests/test_ci_selection.py + tests/test_markers.py)."""
    return set(manifest.get("carve_out", []))


# #10194: the SUBTREE form of an `on_demand:` entry — `dir/**`. The lane was
# built for one FILE at a time, and a subtree of 42 eval files registered that
# way is 42 lines a lane has to keep re-adding (a new eval test file would
# silently re-enter the gate, which is the failure the owner decision exists to
# stop). An entry ending in `/**` means "every test file under this directory",
# expanded against DISK by `on_demand_subtree_files()`.
ON_DEMAND_SUBTREE_SUFFIX = "/**"


def on_demand_subtree_prefixes(manifest: dict) -> tuple[str, ...]:
    """The lane's SUBTREE entries (`dir/**`), as `dir/` prefixes, in order."""
    prefixes = []
    for entry in manifest.get("on_demand") or []:
        if isinstance(entry, str) and entry.endswith(ON_DEMAND_SUBTREE_SUFFIX):
            prefixes.append(entry[:-len(ON_DEMAND_SUBTREE_SUFFIX)] + "/")
    return tuple(prefixes)


def on_demand_subtree_files(manifest: dict) -> set[str]:
    """Every `test_*.py` under a subtree lane entry, tests/-relative (#10194).

    Disk-derived on purpose: the lane's meaning is "this SUBTREE does not gate",
    so a file added to `tests/eval/` tomorrow is off the gate without a config
    edit — the opposite of the literal form, where the new file silently
    re-enters the gating shards until someone notices. The expansion happens
    inside `on_demand_files()`, so every consumer (the leg producers, the
    coverage guards, the runners) sees ONE set.

    A prefix with no directory yields nothing rather than raising: the dead-entry
    report in `leg_coverage_issues()` is what NAMES it.
    """
    found: set[str] = set()
    for prefix in on_demand_subtree_prefixes(manifest):
        base = TESTS_DIR / prefix
        if not base.is_dir():
            continue
        found.update(str(p.relative_to(TESTS_DIR))
                     for p in sorted(base.rglob("test_*.py")))
    return found


def on_demand_files(manifest: dict) -> set[str]:
    """Files excluded from every PRE-MERGE leg — a lane with two runners.

    Runs from `.github/workflows/evals-on-demand.yml` and (for the perf lane)
    `.github/workflows/perf-lane.yml`; the callers below only need "this file
    is in a lane, not on the merge gate", so both runners read through this
    ONE membership test. The accessor exists so `fast_pool`, `push_legs` and
    `leg_coverage_issues` read the lane through ONE definition: three
    independent `manifest.get("on_demand", [])` reads are three places for the
    lane to be half-adopted, and a half-adopted lane is worse than no lane —
    the file is excluded from the gate and claimed by nothing, so it silently
    stops running anywhere at all."""
    # `on_demand:` with NO entries parses to None, not [] — YAML's empty value.
    # `manifest.get("on_demand", [])` returns that None (the key IS present),
    # and `set(None)` raises TypeError, so the guard that exists to NAME an
    # empty lane instead crashed in every caller before it could report. The
    # `or []` is the fix; a test pins the None form specifically, because the
    # obvious `on_demand=[]` spelling in a test is a Python list and never
    # reproduces the state a human actually types into the YAML.
    #
    # #10194: a `dir/**` entry is expanded to the test files UNDER it, so the
    # accessor keeps answering the question every consumer asks — "is this FILE
    # in the lane?" — and no caller needs a second, pattern-aware membership
    # test. A caller that compared raw entries would silently see `eval/**` as a
    # filename that matches nothing.
    entries = manifest.get("on_demand") or []
    literals = {e for e in entries
                if not (isinstance(e, str)
                        and e.endswith(ON_DEMAND_SUBTREE_SUFFIX))}
    return literals | on_demand_subtree_files(manifest)


def on_demand_literal_runner_files(manifest: dict) -> set[str]:
    """Lane files the EMBEDDED-shape runner owns (`evals-on-demand.yml`).

    #10194 moved the two eval subtrees to their own docker-shaped runner
    (`.github/workflows/eval-suite-manual.yml`), so the lane now has two runners
    with different environments. What is left for the embedded one is the lane's
    LITERAL entries — and a literal that merely happens to live INSIDE a subtree
    stays here, because its own `on_demand` entry is what keeps this runner its
    runner.

    ⛔ Subtracting the subtree wholesale (`on_demand_files() -
    on_demand_subtree_files()`) silently dropped `eval/retrieval/
    test_integration.py` — the file `evals-on-demand.yml` was created for
    (#4711) — and left the lane resolving to `test_perf_lane_volunteer_context.py`
    alone, a file `perf-lane.yml` already runs. The workflow, the config comment
    and `docs/ci/affected-test-selection.md` all still said the #4711 file ran
    here, so nothing reddened: the resolver is in a `run:` heredoc, where no test
    can see it. That is why the expression lives HERE, where it can be pinned
    (#10206 review).
    """
    literals = {e for e in (manifest.get("on_demand") or [])
                if isinstance(e, str)
                and not e.endswith(ON_DEMAND_SUBTREE_SUFFIX)}
    return (on_demand_files(manifest)
            - on_demand_subtree_files(manifest)) | literals


def slow_gating_files(manifest: dict) -> set[str]:
    """The slow files a GATING leg may run: `slow_files - carve_out - lane`.

    #10194: this is the ONE definition of the slow pool's gating membership.
    Before it, three producers each computed it themselves — `push_legs()`
    (`slow - carve_out`), `slow_leg_by_surface()` (`slow - carve - uri`), and
    `_full_selection()` (`slow - carve`) — so a new exclusion had to be added in
    three places or it was half-adopted (the failure `on_demand_files()`'s own
    docstring names). They all read this now.

    Subtracting the non-gating lane here is what lets the lane OVERLAP
    `slow_files`: the config keeps the declaration (the file still needs a
    `durations` weight and a classification), and the LEG drops it — exactly the
    way slow carve-out files are handled.
    """
    return (set(manifest.get("slow_files") or [])
            - carve_out_files(manifest)
            - on_demand_files(manifest))


def uri_requiring_files(manifest: dict) -> set[str]:
    """#6884: modules that cannot COLLECT in a URI-EMPTY leg.

    Each module-skips at import when `TORTOISE_DB_URI` is empty — present and
    empty, the shape the tier-2 leg actually sets — so in a
    tier-2 PR leg (URI-empty by design) it collects ZERO tests and
    `pytest --collect-only` exits 5 — the fail-closed manifest guard then kills
    the leg before any test runs, with no `FAILED <nodeid>` for the merge rail
    to attribute. Read through ONE definition, like `on_demand_files`: the
    subtraction happens on two tier-2 paths (the docs-only early return and the
    main surface path) plus the slow lane, and three independent
    `manifest.get("uri_requiring", [])` reads are three places for the lane to
    be half-adopted.

    The `or []` is REQUIRED, not defensive: `uri_requiring:` with no entries
    parses to None, not [] — YAML's empty value — and `set(None)` raises
    TypeError in the `changes` job, i.e. on the single path all PR CI goes
    through. That is reachable at the lane's terminal state: the day the last
    URI-gated module is fixed, a lane empties the list. (Same trap and same fix
    as `on_demand_files`; the None form is pinned by
    tests/test_uri_requiring_selection.py.)
    """
    return set(manifest.get("uri_requiring") or [])


def fast_pool(manifest: dict) -> list[str]:
    """#3400: the full-matrix fast pool — every manifest-classified file that
    is not slow, env-broken, or carve-out. Single source of truth for the
    push halves (push_legs) AND the durations-coverage guard, so the two can
    never disagree about which files need a weight."""
    slow = set(manifest.get("slow_files", []))
    carve_out = carve_out_files(manifest)
    on_demand = on_demand_files(manifest)
    classified = set()
    for s, files in manifest["surfaces"].items():  # noqa: B007
        classified.update(files)
    classified.update(manifest.get("tier1", []))
    classified.update(slow)
    classified.update(on_demand)
    return sorted(f for f in classified
                  if f not in slow and f not in ENV_BROKEN_FILES
                  and f not in carve_out and f not in on_demand)


def fast_shard_count(manifest: dict) -> int:
    """#6135: the configured fast-pool shard count.

    An ABSENT key keeps the historical 2 (a repo that has not adopted the
    config is never failed by it). A present-but-unusable value is not silently
    coerced into a shard count — it collapses to 2 here so a producer path
    cannot crash, and `fast_shard_issues()` NAMES it for `--integrity`.
    """
    raw = manifest.get("fast_shards", DEFAULT_FAST_SHARDS)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return DEFAULT_FAST_SHARDS
    if raw < 2 or raw > MAX_FAST_SHARDS:
        return DEFAULT_FAST_SHARDS
    return raw


def fast_shard_issues(manifest: dict) -> list[str]:
    """#6135: fail-closed validation of the `fast_shards` declaration.

    A shard count that is not an int in [2, MAX_FAST_SHARDS] is a malformed
    declaration, not a default: silently reading it as 2 would leave the
    workflow running a shard count nobody chose, which is the exact
    hand-written-literal drift this key exists to end.

    ABSENCE is the "not adopted" state (a repo that never set the key keeps the
    historical 2, consistently on BOTH lanes). An EXPLICIT null is NOT absence:
    it is a typo that would silently drop the PR lane to 2 while main runs the
    configured S, and the merge rail's lane-parity would then refuse every merge
    (main's `test (c)`… "missing" from the PR) with a green manifest gate.
    """
    if "fast_shards" not in manifest:
        return []
    raw = manifest.get("fast_shards")
    if raw is None:
        return ["fast_shards is explicitly null — remove the key to mean "
                "'not adopted', or set an integer in [2, "
                f"{MAX_FAST_SHARDS}] (a null silently drops the lane to 2)"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        return [f"fast_shards must be an integer, got {raw!r}"]
    if raw < 2:
        return [f"fast_shards must be >= 2 (a single shard is not a shard "
                f"pool; the merge rail's lane-parity forbids removing one), got {raw}"]
    if raw > MAX_FAST_SHARDS:
        return [f"fast_shards must be <= {MAX_FAST_SHARDS} (the a..z label "
                f"ceiling), got {raw}"]
    return []


def carve_shard_count(manifest: dict) -> int:
    """W37: the carve-out pool's shard count, floored to 1 (the unsharded
    job) when the declaration is absent or malformed. Same fail-safe polarity
    as `fast_shard_count` — an unreadable count falls back to the SHAPE THAT
    EXISTS TODAY, never to a number nobody chose."""
    raw = manifest.get("carve_shards", DEFAULT_CARVE_SHARDS)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return DEFAULT_CARVE_SHARDS
    if raw < 1 or raw > MAX_CARVE_SHARDS:
        return DEFAULT_CARVE_SHARDS
    return raw


def carve_shard_issues(manifest: dict) -> list[str]:
    """W37: fail-closed validation of the `carve_shards` declaration.

    `fast_shards` demands >= 2 because a shard pool of one is not a pool. Here
    1 is MEANINGFUL: it is the unsharded job this repo ran until now, and it is
    what removing the key must give back, so a rollback is a key deletion rather
    than a rewrite. Absence is 'not adopted'; an EXPLICIT null is a typo, not an
    absence — same distinction, and the same reason, as `fast_shard_issues`.
    """
    if "carve_shards" not in manifest:
        return []
    raw = manifest.get("carve_shards")
    if raw is None:
        return ["carve_shards is explicitly null — remove the key to mean "
                "'one unsharded job', or set an integer in [1, "
                f"{MAX_CARVE_SHARDS}] (a null silently drops the pool to 1)"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        return [f"carve_shards must be an integer, got {raw!r}"]
    if raw < 1:
        return [f"carve_shards must be >= 1 (1 is the unsharded job; 0 would "
                f"delete the lane), got {raw}"]
    if raw > MAX_CARVE_SHARDS:
        return [f"carve_shards must be <= {MAX_CARVE_SHARDS} (the a..z label "
                f"ceiling), got {raw}"]
    return []


def slow_shard_count(manifest: dict) -> int:
    """#9539: the configured `test-slow` leg count.

    Absence keeps the historical 2. A present-but-unusable declaration is NOT
    silently coerced into a shard count — it collapses to 2 here so a producer
    path cannot crash, and `slow_shard_issues()` NAMES it for `--integrity`.
    """
    raw = manifest.get("slow_shards", DEFAULT_SLOW_SHARDS)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return DEFAULT_SLOW_SHARDS
    if raw < 2 or raw > MAX_SLOW_SHARDS:
        return DEFAULT_SLOW_SHARDS
    return raw


def slow_shard_issues(manifest: dict) -> list[str]:
    """#9539: fail-closed validation of the `slow_shards` declaration.

    >= 2 because this lane has always had two legs and a drop to one is a
    silent ceiling regression (37.9m + setup in a single job); > MAX_SLOW_SHARDS
    because the labels are positional letters and the merge rail's lane-parity
    subtracts main's job names from the PR's, so the label alphabet is the
    ceiling. Absence is 'not adopted'; an EXPLICIT null is a typo — same
    distinction and same reason as `fast_shard_issues`.
    """
    if "slow_shards" not in manifest:
        return []
    raw = manifest.get("slow_shards")
    if raw is None:
        return ["slow_shards is explicitly null — remove the key to mean "
                "'the historical 2 legs', or set an integer in [2, "
                f"{MAX_SLOW_SHARDS}] (a null silently drops the pool to 2)"]
    if isinstance(raw, bool) or not isinstance(raw, int):
        return [f"slow_shards must be an integer, got {raw!r}"]
    if raw < 2:
        return [f"slow_shards must be >= 2 (a single leg carries the whole "
                f"~37.9m pool plus setup, which is the ceiling this key "
                f"exists to remove), got {raw}"]
    if raw > MAX_SLOW_SHARDS:
        return [f"slow_shards must be <= {MAX_SLOW_SHARDS} (the a..z label "
                f"ceiling), got {raw}"]
    return []


def slow_leg_files(manifest: dict) -> list[str]:
    """#9539: the slow lane's pool as bare names — exactly `slow_files` minus
    `carve_out`, the set the in-workflow drift guard has always pinned.

    Slow carve-out files run in the URI-unset carve-out job (epic #1647
    E2E-4), never in a docker slow leg, so they are removed here rather than
    filtered per leg. Sorted for determinism: the LPT pack's tie-break reads
    the input order.
    """
    # #10194: read through `slow_gating_files()`, the single definition of the
    # gating slow pool, so the in-workflow drift guard that compares the expanded
    # legs against THIS function cannot disagree with the producer that built
    # them. (Bare names: the workflow's matrix format.)
    return sorted(f[:-3] if f.endswith(".py") else f
                  for f in slow_gating_files(manifest))


def slow_shard_entries(manifest: dict) -> list[dict]:
    """#9539: `slow_leg_files()` LPT-packed into `slow_shards` labelled legs.

    Routes through `build_shard_entries` — the SAME pack the tier-2 fast lane
    and the carve-out lane use — so the slow lane gets the established
    mechanism and its per-leg `watchdog_minutes`/`watchdog_headroom` rather
    than a second implementation. Those two fields stay on the ENTRY:
    `slow_matrix_include()` publishes only `half`/`files`, so the job's 40m/60m
    budgets are still literals (deliberate, #9539) and a reader should not
    expect them in the matrix. Labels are positional (`a`, `b`, …), which
    keeps the job-name set a SUPERSET of main's two legs: the merge rail's
    lane-parity subtracts names, so an added `test-slow (c)` is fine while a
    rename would refuse every merge.
    """
    files = [f"tests/{n}.py" for n in slow_leg_files(manifest)]
    return build_shard_entries(files, _durations_map(manifest),
                               slow_shard_count(manifest))


def slow_matrix_include(entries: list[dict]) -> dict:
    """#9539: the GitHub Actions matrix the `test-slow` job expands.

    Emitted COMPLETE, like `fast_matrix_include`/`carve_matrix_include`: the
    leg set is the selector's contract, never a workflow literal. `files` is
    SPACE-JOINED (the run step's `for f in ${{ matrix.files }}` shape) and the
    names are BARE — the workflow re-adds `tests/<name>.py`.
    """
    return {"include": [
        {"half": e["name"], "files": " ".join(e["files"])}
        for e in entries
    ]}


def slow_wall_projection(manifest: dict,
                         entries: list[dict] | None = None) -> dict:
    """#9539: the projected slow-leg wall, from the committed `durations` map.

    `projected_wall_min = max(leg est) + SLOW_LEG_SETUP_ALLOWANCE_MIN`. This is
    the number the wall guard holds to `SLOW_LEG_WALL_TARGET_MIN`; it is a
    PROJECTION from the map (never a measurement), which is why the setup
    allowance is a measured constant rather than an assumption inside it.
    """
    entries = slow_shard_entries(manifest) if entries is None else entries
    legs = sorted(((e["est_seconds"] / 60.0) for e in entries), reverse=True)
    pool = sum(e["est_seconds"] for e in entries) / 60.0
    return {"legs_min": [round(x, 2) for x in legs],
            "pool_min": round(pool, 2),
            "max_leg_min": round(legs[0], 2) if legs else 0.0,
            "setup_allowance_min": SLOW_LEG_SETUP_ALLOWANCE_MIN,
            "projected_wall_min": round((legs[0] if legs else 0.0)
                                        + SLOW_LEG_SETUP_ALLOWANCE_MIN, 2),
            "target_min": SLOW_LEG_WALL_TARGET_MIN}


def carve_shard_entries(manifest: dict) -> list[dict]:
    """W37: the carve-out set packed into `carve_shards` LPT bins.

    Uses `build_shard_entries`, the SAME pack the tier-2 fast lane uses
    (`split_fast_gate` + `shard_watchdog_minutes`) — the carve-out gets the
    established mechanism rather than a second one.

    ⛔ THE FIRST SHARD'S JOB NAME IS THE BARE `test-carve-out`, and that is
    load-bearing, not cosmetic. The merge rail's lane-parity subtracts main's
    job names from the PR's, so an unadopted main holds `test-carve-out` and a
    PR that renamed it to `test-carve-out (a)` would be missing one of main's
    names and refuse EVERY merge until it landed. Keeping the bare name on
    shard 0 (and `(b)`, `(c)`, … on the rest) makes the shard set a strict
    SUPERSET of main's names — the same property `fast_shards` relies on when
    it keeps `a` and `b`.
    """
    count = carve_shard_count(manifest)
    carve = sorted(carve_out_files(manifest))
    entries = build_shard_entries(carve, _durations_map(manifest), count)
    labels = shard_labels(len(entries))
    for i, e in enumerate(entries):
        # `suffix` is the workflow's `name:` tail: '' for shard 0 (so the check
        # is exactly `test-carve-out`), ' (b)'/ ' (c)' after that.
        e["suffix"] = "" if i == 0 else f" ({labels[i]})"
        # `label` is the ARTIFACT-name tail, and it must NOT be `suffix`: suffix
        # carries a leading space and parentheses, and `github.job` is the BASE
        # job id for every matrix leg, so an upload named `pytest-log-${{ github.job }}`
        # is the SAME name for every shard — upload-artifact@v4 then 409s and,
        # because the step is continue-on-error, the shard logs vanish silently
        # (#6263 pins exactly this).
        e["label"] = labels[i]
    return entries


def carve_matrix_include(entries: list[dict]) -> dict:
    """W37: the GHA matrix the `test-carve-out` job expands.

    Emitted COMPLETE, like `fast_matrix_include`: the shard set is the
    selector's contract, never a workflow literal. `files` is SPACE-JOINED
    (the workflow's `for f in ${{ matrix.files }}` shape) and, like the fast
    band, the names are BARE (no `.py`) — the workflow re-adds the extension.
    """
    return {"include": [
        {
            "suffix": e["suffix"],
            # Distinct per leg, and filesystem-safe — the artifact name tail.
            "label": e["label"],
            "files": " ".join(e["files"]),
            "watchdog_minutes": e["watchdog_minutes"],
        }
        for e in entries
    ]}


def shard_labels(count: int) -> list[str]:
    """#6135: positional shard labels `a`, `b`, `c`, …

    LETTERS, never digits, and always from the start of the alphabet: the
    merge rail's lane-parity is a NAME subtraction (`main's names - PR's
    names`), so an S-adding diff is comparable only while `test (a)` and
    `test (b)` survive as names. The labels are what makes the added shards
    a SUPERSET rather than a rename.
    """
    import string
    return list(string.ascii_lowercase[:count])


def shard_watchdog_minutes(est_seconds: float) -> int:
    """#6135: the per-shard pytest watchdog, derived from the shard's own
    packed estimate.

    Correctness, not tuning: the watchdog is per-leg, so inheriting the old
    55m lets a hung ~7-minute shard hold the REQUIRED aggregate red for 55
    minutes. The factor behind the budget and both clamps are owned by the
    constants block above (which carries their provenance and the #6145 rule) —
    this function only applies them; the floor keeps a small shard above the
    #6133 floor (the largest single file).

    ⛔ The CEILING does NOT "keep the validated budget" (#6145): it deliberately
    stays 55m so the worst-case leg fits the job's outer `timeout-minutes`, and a
    clamp that drops the budget below `WATCHDOG_HEADROOM` is an integrity FAILURE
    (`watchdog_headroom_issues`), not a retained one. The remedy is a deliberate
    ceiling raise or a re-split — see the OVERRIDES record on #6145.
    """
    try:
        minutes = float(est_seconds) / 60.0
    except (TypeError, ValueError):
        minutes = 0.0
    # `inf`/`nan` survive float() but explode inside math.ceil (OverflowError /
    # ValueError). They are impossible from the current producers (weights are
    # coerced finite), but this is the seam a future caller passes a raw sum to,
    # so the finiteness is checked HERE rather than assumed.
    if not math.isfinite(minutes) or minutes < 0:
        minutes = 0.0
    raw = math.ceil(minutes * WATCHDOG_HEADROOM)
    return int(min(max(raw, WATCHDOG_FLOOR_MIN), WATCHDOG_CEILING_MIN))


def watchdog_headroom(est_seconds: float, watchdog_minutes: int) -> float:
    """#6145: the headroom the EMITTED budget actually retains (`budget / est`).

    `shard_watchdog_minutes()` builds the budget from `WATCHDOG_HEADROOM`, but the
    FLOOR and CEILING clamps can deliver a different multiple than the one it was
    built from: upward at the floor (intended — the floor exists to clear the
    #6133 single-file time) and DOWNWARD at the ceiling, which is the hazard.

    Returns 0.0 for an unusable estimate: the budget is then the floor, and
    "headroom over an unknown estimate" is not a number anyone should act on.
    """
    try:
        minutes = float(est_seconds) / 60.0
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(minutes) or minutes <= 0:
        return 0.0
    return float(watchdog_minutes) / minutes


def watchdog_headroom_issues(manifest: dict) -> list[str]:
    """#6145 / #4819 option 2: NAMED issues where a shard's emitted budget retains
    LESS headroom than the factor the budget is built from.

    This closes the CEILING half of the gap #4819 recorded and nobody checked —
    "the watchdog value and the shard count: nothing checks that [they are
    consistent]". A budget that silently drifts TIGHTER than the factor it is
    built from is the state in which a shard that fits its own declared estimate
    is killed, and it is reachable today: at a 30.62-minute estimate
    `ceil(30.62 * 2) = 62` is clamped to the 55-minute ceiling — an EFFECTIVE
    1.80x — which the ceiling's own comment treated as "the validated budget"
    rather than as a breach, and which
    `tests/test_ci_selection.py::test_watchdog_is_per_shard_and_scales_with_the_shard`
    asserts without ever computing the margin.

    ⛔ SCOPE — ONE direction, and it is not the direction the observed kills came
    from. The two axes are independent and only ONE is visible here:

    - What the check sees: the DECLARED `est_seconds` and the emitted budget. It
      fires iff the ceiling clamped the budget below that factor AT THE
      PUBLISHED PRECISION — `round(budget / est_min, 2) < 2.0`, i.e. a declared
      estimate above ~27.57 min. A shard whose DECLARED estimate exceeds that is
      flagged even when the estimate is perfectly accurate, and one whose
      declared estimate stays within it passes even when the estimate is far too
      low. A clamp inside the 2-dp quantum (est in ~(27.500, 27.569] min) passes
      by design, because the value the matrix publishes reads a compliant 2.0.
    - What it cannot see: the ACTUAL runtime. The observed kills are the other
      axis — a shard that ran ~2.11x its declared cost (the shape #6145
      measured) — and a shard like that passes if its DECLARED value is in
      range. So a green `manifest-integrity` means "the emitted budgets are
      consistent with their own declared factors", NOT "the watchdog sizing is
      sound", and this change covers neither the kills nor the runner-pool
      capacity deficit behind them (#5215 / #6792).

    The floor direction is intentionally not an issue: the floor binding is the
    normal, intended case (it clears the #6133 single-file time), not a breach.

    Fail-closed and NAMED, so a shard that cannot fit its budget is flagged at
    SELECTION time instead of being killed at run time (#6145's sizing contract:
    "a shard unable to fit is flagged rather than silently killed"). The estimate
    and the budget therefore move together: raising an estimate (or lowering the
    shard count) past the ceiling's reach REDS this check, and the remedy is a
    deliberate ceiling raise or a re-split — never a silent kill. "Fail-closed"
    includes the extreme: an estimate so large that the ratio rounds to `0.00`
    still fires, because the SENTINEL is read from the UNROUNDED value. A `0.00`
    on a PASSING row therefore means one thing only — that the leg has no usable
    estimate (an empty leg) — and never that its margin was too small to report.
    """
    issues = []
    for s in push_legs(manifest)["shards"]:
        budget = s["watchdog_minutes"]
        est = s["est_seconds"]
        # #6145: decide on the SAME value the matrix PUBLISHES (2 dp, the same
        # rounding the emitters apply). A gate comparing an exact ratio while
        # emitting a rounded one would refuse a manifest whose own emitted row
        # read a compliant 2.0 — and its message would say "2.00x, below the
        # 2.0x", contradicting itself at exactly the boundary this check exists
        # to police. Decision, message and emission are therefore ONE value; the
        # cost is that a sub-0.005 excursion is inside the reporting quantum.
        #
        # The SENTINEL test reads the UNROUNDED value. `raw == 0.0` is
        # `watchdog_headroom`'s "no usable estimate" answer (est <= 0, or a
        # non-finite one), and it is NOT the same thing as a genuine ratio small
        # enough to round to 0.00: testing the ROUNDED value for truthiness would
        # FAIL OPEN on an estimate large enough to collapse the ratio onto the
        # sentinel — the guard would skip the very leg it exists to name, and
        # would publish `0.00` on a row it passed.
        raw = watchdog_headroom(est, budget)
        headroom = round(raw, 2)
        if raw and headroom < WATCHDOG_HEADROOM:
            issues.append(
                f"shard {s['name']}: its emitted budget retains "
                f"{headroom:.2f}x, below the {WATCHDOG_HEADROOM:.1f}x it is built "
                f"from — the shard may still fit the {budget}m wall, but the "
                f"budget no longer holds that factor (est={est:.1f}s = "
                f"{est / 60.0:.2f} min; the {WATCHDOG_CEILING_MIN}m ceiling "
                f"clamped it). Raise the ceiling deliberately or re-split the "
                f"shards (#6145/#4819)")
    return issues


def push_legs(manifest: dict) -> dict:
    """#1472/#6135: partition every manifest-classified file into exactly one
    push leg (fast shards / slow / env_broken / carve_out), duration-balanced
    across the fast shards (#3400 LPT, #6135 N shards).

    Single source of truth for the workflow's push matrix: registration in
    the manifest is sufficient — no manual matrix edit. Returns .py-less
    names (the workflow's matrix format). `push_extra` (bench files, not
    classifiable as top-level surfaces) is spread round-robin across the
    shards. Epic #1647 Task 9: carve_out files are excluded from every docker
    leg and emitted as their own leg (the URI-unset carve-out job's file list).

    Each shard entry carries its `est_seconds` (the LPT bin weight, the
    declared test time), the `watchdog_minutes` derived from it, and the
    `watchdog_headroom` that budget actually retains relative to that estimate
    (#6145) — the per-leg budget is a property of the SHARD, not a workflow
    literal.
    """
    # #10194: `slow` is the GATING pool — `slow_gating_files()` already removed
    # carve-out and the non-gating lane, so the subtraction below is idempotent
    # and kept only because the emitted `slow` key's shape is pinned.
    slow = slow_gating_files(manifest)
    carve_out = carve_out_files(manifest)
    on_demand = on_demand_files(manifest)
    fast = fast_pool(manifest)
    # #3400: pack the fast shards by measured duration (#1473 LPT) instead of
    # the duration-blind index-parity split this used to be (`fast[0::2]` /
    # `fast[1::2]`). Parity on the real pool leaves a tilt far above the ratio
    # below — the pool's cost is not index-uniform — and blew the 55m watchdog;
    # LPT is deterministic (ties and the empty-bin choice go to the LOWEST
    # label — the historical "ties -> a") and balances the same pool.
    durations = _durations_map(manifest)
    bins = split_fast_gate(fast, durations, shards=fast_shard_count(manifest))
    labels = shard_labels(len(bins))
    # #1485: distribute push_extra (bench files) round-robin across the shards.
    # Durations now drive the balance (#3400), but even spreading keeps this
    # neutral rather than dumping the whole bench set on one shard.
    extras = [f.replace(".py", "") for f in manifest.get("push_extra", [])]
    shards = []
    for i, (label, bin_files) in enumerate(zip(labels, bins, strict=True)):
        # BARE names (no `.py`): the workflow's run step maps them with
        # `tests/<name>.py`, and `workflow_halves_issues` keys the manifest on
        # bare names too. A `.py` left here would be read as a file named
        # `test_x.py.py`.
        names = [f[len("tests/"):].replace(".py", "") for f in bin_files]
        names += extras[i::len(labels)]
        est = sum(_duration_weight(durations.get(f + ".py")) for f in names)
        # #6145: derive the budget from the ROUNDED estimate — the value the
        # matrix publishes — so the estimate, the budget and the emitted
        # headroom are mutually consistent. The builder and the integrity check
        # must not read the same quantity at two different precisions; that
        # mismatch is how a boundary value slips through the gate, and the
        # ceiling boundary is exactly what the gate polices.
        est = round(est, 1)
        budget = shard_watchdog_minutes(est)
        shards.append({"name": label, "files": sorted(names),
                       "est_seconds": est,
                       "watchdog_minutes": budget,
                       "watchdog_headroom": round(watchdog_headroom(est, budget), 2)})
    strip = lambda xs: sorted(x.replace(".py", "") for x in xs)  # noqa: E731
    return {"shards": shards,
            # Epic #1647 Task 9: slow carve-out files (test_reaper et al.)
            # run in the URI-unset carve-out job, never the docker slow legs.
            "slow": strip(slow - carve_out),
            "env_broken": sorted(ENV_BROKEN_FILES),
            "carve_out": strip(carve_out),
            # W37: the same set, packed. `carve_out` is KEPT above because the
            # `changes` job publishes it as its `carve_out` output and several
            # pins read this emitted key; `leg_coverage_issues` enforces the
            # coverage contract over the same set, read from the manifest rather
            # than from this leg. The partition itself — the shards must cover
            # `carve_out` exactly, never re-derive it — is pinned by
            # `tests/test_ci_selection.py::test_carve_out_matrix_partitions_the_leg`;
            # no check in THIS module enforces it.
            "carve_shards": carve_shard_entries(manifest),
            # #9539: the slow pool, packed. `slow` above is KEPT because the
            # `changes` job publishes it and existing pins read the emitted
            # key; the matrix below is what `test-slow` expands.
            "slow_shards": slow_shard_entries(manifest),
            # Not a push leg: emitted so leg_coverage_issues() can account for
            # the file. Nothing in python-ci.yml consumes this key — that is
            # the POINT of the lane.
            "on_demand": strip(on_demand)}


def all_shard_files(legs: dict) -> list[str]:
    """#6135: every fast file across every shard. Consumers MUST read the
    union of `shards`, never a single shard or a fixed prefix of them — a
    consumer that assumed `half_a`/`half_b` grew a silent coverage hole the
    moment S rose."""
    return [f for shard in legs["shards"] for f in shard["files"]]


def build_shard_entries(files: list[str], durations: dict,
                        shards: int) -> list[dict]:
    """#6135: an LPT pack of `files` into `shards` labelled entries, each
    carrying its packed estimate, the per-shard watchdog derived from it, and
    the headroom that budget retains (#6145).

    Used by the tier-2 `--split` path AND by `carve_shard_entries()` (the W37
    carve-out lane, which `push_legs()` routes through). `push_legs()` itself
    inlines the same pack for the fast shards (it must round-robin `push_extra`
    across the shards in the same pass), so the shared seam that keeps every
    lane's shard budgets in agreement is `shard_watchdog_minutes()`, NOT this
    function — but editing the packing here DOES change both the tier-2 `--split`
    lane and the carve-out lane, not the fast shards.
    """
    bins = split_fast_gate(files, durations, shards=shards)
    labels = shard_labels(len(bins))
    entries = []
    for label, bin_files in zip(labels, bins, strict=True):
        # BARE names — see push_legs: the workflow maps them with `tests/<n>.py`.
        names = sorted((f[len("tests/"):] if f.startswith("tests/") else f)
                       .replace(".py", "") for f in bin_files)
        est = sum(_duration_weight(durations.get(n + ".py")) for n in names)
        est = round(est, 1)  # #6145: the budget follows the published estimate
        budget = shard_watchdog_minutes(est)
        entries.append({"name": label, "files": names,
                        "est_seconds": est,
                        "watchdog_minutes": budget,
                        "watchdog_headroom": round(watchdog_headroom(est, budget), 2)})
    return entries


def non_gating_report(manifest: dict) -> str:
    """#10194: the OBSERVABILITY line for the gating run.

    A membership change is invisible in a CI log unless the log says what left:
    "the evals are not running" and "the evals are excluded on purpose" look
    identical from a green run. This prints, on every run, which lane entries are
    non-gating, how many files each one resolves to, and the shard → file-count
    map the run is about to expand — so a reader can SEE that the 42 files are
    absent from all 9 shards rather than infer it from a total.
    """
    lane = on_demand_files(manifest)
    prefixes = on_demand_subtree_prefixes(manifest)
    lines = [f"non-gating lane: {len(lane)} file(s) excluded from every gating leg"]
    literals = sorted(e for e in manifest.get("on_demand") or []
                      if not (isinstance(e, str)
                              and e.endswith(ON_DEMAND_SUBTREE_SUFFIX)))
    # Attribute every file to exactly ONE entry, so the itemization RECONCILES
    # with the total. A literal that also sits inside a subtree (`eval/
    # retrieval/test_integration.py` inside `eval/**`) was listed twice and the
    # lines summed to 44 against a 43-file total — in the one feature whose
    # whole purpose is an auditable count (#10206 review).
    accounted = 0
    for entry in literals:
        inside = next((p for p in prefixes if entry.startswith(p)), None)
        if inside:
            lines.append(f"  {entry} -> covered by {inside}** below")
        else:
            lines.append(f"  {entry} -> 1 file")
            accounted += 1
    for prefix in prefixes:
        n = sum(1 for f in lane if f.startswith(prefix))
        lines.append(f"  {prefix}** -> {n} file(s)")
        accounted += n
    if accounted != len(lane):
        lines.append(f"  ⚠️ itemization does not reconcile: {accounted} of "
                     f"{len(lane)} file(s) attributed — an `on_demand` entry "
                     f"resolved to files no line above claims")
    legs = push_legs(manifest)
    lines.append(f"gating fast shards: {len(legs['shards'])} "
                 f"(file counts below EXCLUDE the lane above)")
    for shard in legs["shards"]:
        lines.append(f"  test ({shard['name']}): {len(shard['files'])} files, "
                     f"est {shard['est_seconds']:.0f}s, "
                     f"watchdog {shard['watchdog_minutes']:.0f}m")
    lines.append(f"gating slow legs: {len(legs['slow_shards'])} "
                 f"({len(legs['slow'])} file(s))")
    lines.append(f"gating carve-out legs: {len(legs['carve_shards'])} "
                 f"({len(legs['carve_out'])} file(s))")
    total = sum(len(s["files"]) for s in legs["shards"])
    lines.append(f"gating fast-pool total: {total} file(s); "
                 f"non-gating total: {len(lane)} file(s)")
    return "\n".join(lines)


# #6897 (owner ruling 2026-10-27: use the compute capacity wherever it is,
# prioritise THROUGHPUT over runner class): the two runner classes the fast
# matrix can split across, as the LABEL ARRAYS `runs-on` wants. Emitted as a
# JSON ARRAY STRING because a matrix value is always a string, and `runs-on`
# needs an array — the workflow `fromJSON`s it back (`python-ci.yml`, `test:`).
#
# x64 CELLS ONLY for the pool half, never the ARM64 VMs: measured per-test cost
# on the SAME nodeids splits by class — arm64 legs are 4.46x hosted, x64 cells
# cheaper than that (654 common testcases, run 40238275534 vs hosted
# 40249711042; the figures live in the `test` job's "WHICH RUNNER CLASS CARRIES
# THIS LEG" header). Routing a fast shard onto ARM64 would buy queue time and
# pay it back in test time — and naming `self-hosted-x64` EXPLICITLY, rather
# than inheriting a generic label, is what makes that guarantee hold.
#
# ⛔ THE SPLIT IS **OFF BY DEFAULT**, AND THAT IS A RECORDED DECISION, NOT A
# PREFERENCE. `docs/ops/ci-runner-x64.md` -> "Contradiction test" and
# `scripts/ci_runner_gate.sh:1-30` record the local pool as **fallback-only**:
# jobs landing on local hardware compete with the agent fleet and the Tortoise
# graph (a local pytest run crashed FalkorDB, 178 restarts, 2026-10-12). An
# earlier cut of this lane made the pool a PRIMARY class for 4 of 9 gated shards
# BY DEFAULT, which is an adoption over that decision — not a lane's to take.
# #6897's "use the capacity wherever it is" is therefore served by making the
# split AVAILABLE (an explicit opt-in), never by making it the default; the
# route to change the default is to reopen `fallback-only` in its own home
# (#6897) with the measurement below attached.
#
# THE MEASUREMENT THAT KEEPS IT OFF (PR #10186 review, 2026-10-30, and this
# branch's own head): on run 40981781885 (main, plain `ubuntu-latest`) the pool
# won 5 of 9 legs by the fallback race and **5 of 5 died at their watchdog**
# while 4 of 4 hosted legs passed in 10-12 m of a 20 m budget — x64 cells
# measured ~2.7x hosted at job level, not the 1.16x an older run suggested. On
# head 5a2ae6aa1 (run 40986603502) 3 of 4 pool legs failed, and two of those
# were not budget kills at all: `redis.exceptions.ConnectionError: Error 111
# ... localhost:6379` — the pool host was not serving the FalkorDB every docker
# -lane shard needs. A class that cannot complete the work is not capacity, so
# the default stays exactly where main is.
#
# ⛔ The hosted half is `ubuntu-latest` — MAIN'S CURRENT LABEL, and deliberately
# NOT described here as "GitHub-hosted". On this repo that label is not
# hosted-only: `scripts/ci_runner_gate.sh` attaches `ubuntu-latest` to the
# self-hosted pool as a FALLBACK, and #6897 measured it attached WHILE
# GitHub-hosted was healthy (the gate logged `set_labels ok=16 fail=0`, a false
# PASS), so a `ubuntu-latest` job can land on the ARM64 class — that is the
# mechanism behind the 39.6m `test-slow (a)` kill on run 39309984832. It reads as
# hosted today (probe 40548658598: no runner carries the label, and the
# `ubuntu-latest` probe job queued 3m50s and was then CANCELLED without ever
# being assigned a runner — it never landed on the pool), which is why "today"
# is a measurement, not a guarantee. `ubuntu-24.04` is the hosted-ONLY label
# (`test-slow`, `workflow-lint`, finding-provenance pin it for that reason);
# moving this half to it is a SEPARATE decision, not taken here, because it would
# also drop the outage fallback and break comparability with the canary streak's
# history.
SHARD_RUNNER_HOSTED = ["ubuntu-latest"]
SHARD_RUNNER_X64 = ["self-hosted", "self-hosted-x64"]

# THE OPERATOR LEVER, and the mode is resolved in ONE place (Python, not a
# workflow expression) so it is testable. Anything that is not the explicit
# opt-in resolves to hosted-only: an unset variable, a typo, an emptied value,
# or a fork where `vars` are unavailable all land on MAIN'S behaviour instead of
# routing a required check onto a contended class. Fail-safe, never fail-open —
# an operator should never discover the split because a leg died.
SHARD_ROUTING_ENV = "CI_SHARD_ROUTING"
SHARD_ROUTING_HOSTED_ONLY = "hosted-only"
SHARD_ROUTING_POOL_SPLIT = "pool-split"


def resolve_shard_routing(value: str | None) -> str:
    """#6897: map a `CI_SHARD_ROUTING` value to a routing mode.

    `pool-split` is the ONLY value that enables the split (exact match, case and
    surrounding space ignored — a hand-typed Settings value should not need to
    be byte-exact). Everything else, INCLUDING the documented `hosted-only`, is
    hosted-only: that value is the explicit safe setting, and being the default
    must not make it illegal.
    """
    if (value or "").strip().lower() == SHARD_ROUTING_POOL_SPLIT:
        return SHARD_ROUTING_POOL_SPLIT
    return SHARD_ROUTING_HOSTED_ONLY


def fast_shard_runner(index: int, count: int) -> list[str]:
    """#6897: which runner class carries shard `index` of `count` IN POOL-SPLIT MODE.

    ⛔ This is the OPT-IN rule only: under the default (`hosted-only`)
    `fast_matrix_include()` never calls it, and every row keeps
    `SHARD_RUNNER_HOSTED` — main's label, main's behaviour.

    Two rules, in this order:

    1. The LAST shard is ALWAYS the HOSTED LABEL — it is the `canary_producer`,
       whose artifacts feed the canary streak, and that streak is compared
       across runs. Keeping the producer on the same label it has always used
       keeps the comparison a comparison of TESTS. (That label's resolving class
       is main's ambiguity, carried over unchanged — see the constants above; it
       is not a promise this function can make.)
    2. Everything else ALTERNATES by position (even -> the hosted label, odd ->
       the x64 pool) so the work splits across BOTH runner classes and the
       matrix actually adds throughput instead of queueing every leg on one.

    At `count == 2` rule 2 has no row to give (index 0 is even, index 1 is the
    pinned last one), so the split degrades to all-hosted-label — main's
    pre-#6897 behaviour, never a half-applied one. Any S >= 3 uses both classes.

    Positional, never content-based: the same property #6135 uses for the
    labels, so the routing does not have to be re-derived when the pack moves a
    file between shards.
    """
    if index == count - 1:
        return list(SHARD_RUNNER_HOSTED)
    return list(SHARD_RUNNER_HOSTED if index % 2 == 0 else SHARD_RUNNER_X64)


def fast_matrix_include(shards: list[dict], routing: str | None = None) -> dict:
    """#6135: the GitHub Actions matrix the `test` job expands.

    Emitted COMPLETE (the workflow does no shell composition), because the
    shard set is the selector's contract, not the workflow's. `canary_producer`
    marks the ONE shard whose artifacts feed the canary streak: the last shard,
    chosen positionally so the marker never has to name a half (`half == 'b'`
    stopped existing the moment S rose — the exact coupling #6135 removes).

    #6897: `runner` carries the label array (as a JSON string) for `runs-on` —
    the workflow's ONLY consumer of it, so the routing is decided HERE and not
    in a shell expression. `routing` is the mode: `pool-split` opts into the
    positional split via `fast_shard_runner()`, anything else (the default,
    including `routing=None`, which reads `CI_SHARD_ROUTING` from the
    environment) gives EVERY row `SHARD_RUNNER_HOSTED` — main's label, main's
    behaviour. See the block above the constants for why the default is off.
    """
    mode = resolve_shard_routing(
        os.environ.get(SHARD_ROUTING_ENV) if routing is None else routing)
    include = []
    for i, s in enumerate(shards):
        # #6145: carry the headroom alongside the budget so the margin a leg was
        # actually given is VISIBLE in the matrix — the ceiling can silently
        # deliver less than the factor the budget was built from, and an
        # implicit margin cannot be audited.
        headroom = s.get("watchdog_headroom")
        if headroom is None:
            headroom = watchdog_headroom(s.get("est_seconds", 0.0),
                                         s["watchdog_minutes"])
        include.append({
            "half": s["name"],
            "files": " ".join(s["files"]),
            "watchdog_minutes": s["watchdog_minutes"],
            "watchdog_headroom": round(float(headroom), 2),
            "canary_producer": i == len(shards) - 1,
            "runner": json.dumps(
                fast_shard_runner(i, len(shards))
                if mode == SHARD_ROUTING_POOL_SPLIT else SHARD_RUNNER_HOSTED),
        })
    return {"include": include}


def leg_coverage_issues(manifest: dict) -> list[str]:
    """#1472 reverse drift: every classified file in exactly one push leg."""
    slow = set(manifest.get("slow_files", []))
    carve_out = carve_out_files(manifest)
    on_demand = on_demand_files(manifest)
    legs = push_legs(manifest)
    # #6135: the union of EVERY shard, never a fixed pair of them — a consumer
    # that read two shards would grow a silent coverage hole the moment S rose.
    fast = {f + ".py" for f in all_shard_files(legs)}
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
    # #9539: the slow SHARDS must cover the slow leg set exactly once. The pack
    # is derived, so a packer defect (a bin dropped, or a file landing in two
    # bins) would silently shrink the lane — and the in-workflow drift guard
    # reads the SAME derivation, so it cannot be the independent check. This is
    # the manifest-vs-legs one.
    covered = [f + ".py" for s in legs["slow_shards"] for f in s["files"]]
    if len(covered) != len(set(covered)):
        dupes = sorted({f for f in covered if covered.count(f) > 1})
        issues.append(f"slow shard pack runs a file twice: {dupes}")
    if set(covered) != slow_leg:
        issues.append(
            f"slow shards do not cover slow_gating_files() "
            f"(slow_files - carve_out - the non-gating lane, #10194) exactly: "
            f"missing={sorted(slow_leg - set(covered))} "
            f"extra={sorted(set(covered) - slow_leg)}")
    if carve_out & ENV_BROKEN_FILES:
        issues.append(f"carve-out/env-broken overlap: {sorted(carve_out & ENV_BROKEN_FILES)}")
    # ABSENT is not the same as EMPTY. A repo that has not adopted the lane
    # has no `on_demand:` key and is fine; a repo that declares the key and
    # leaves it empty has a no-op exclusion — the expensive file is back on
    # the merge path with a lane that reads as a saving. Same polarity as
    # `duration_issues`' durations guard (absent = not adopted = PASS, a
    # present-but-unusable declaration = RED), so adopting the tool without
    # the lane is not a portability regression.
    if "on_demand" in manifest and not on_demand:
        issues.append("on_demand lane is present but EMPTY — it excludes nothing")
    # #10194: the lane may now legitimately OVERLAP the config's `slow_files`
    # (five eval files are declared slow AND in the lane), the same way slow
    # carve-out files do — `slow_files` keeps the declaration and the LEG drops
    # the file. So the invariant is about the LEG: a lane file that reaches a
    # gating slow leg is still on the merge path, and `slow_gating_files()` is
    # the single place that subtracts it. Reading this as a config-set overlap
    # would red a correct manifest and invite deleting the lane entry.
    gating_slow = set(slow_gating_files(manifest))
    if on_demand & gating_slow:
        issues.append("on-demand file leaked into a gating slow leg: "
                      f"{sorted(on_demand & gating_slow)}")
    # REACHABLE but DORMANT with the current config: `fast_pool()` filters the
    # lane out, and `push_extra` is EMPTY today, so this branch does not fire on
    # the real manifest. It fires when a `push_extra` entry names a lane file —
    # `push_legs()` appends `push_extra` into the halves AFTER `fast_pool`'s
    # filter, so such an entry lands in a fast leg and is caught here.
    #
    # (This comment has now been wrong in BOTH directions: first it claimed the
    # branch was "structurally unreachable in every constructible state" — it is
    # not — and then, over-corrected, that it "fires today" — it does not. A
    # claim about reachability is checkable, and both versions were checked.
    # State reachability precisely, and do not restate the history twice: an
    # earlier draft of this very paragraph concatenated a second copy of itself
    # mid-line.)
    if fast & on_demand:
        issues.append(f"on-demand file leaked into a fast leg: {sorted(fast & on_demand)}")
    if on_demand & carve_out:
        issues.append(f"on-demand/carve-out overlap: {sorted(on_demand & carve_out)}")
    if on_demand & ENV_BROKEN_FILES:
        issues.append(f"on-demand/env-broken overlap: {sorted(on_demand & ENV_BROKEN_FILES)}")
    classified = set()
    for s, files in manifest["surfaces"].items():  # noqa: B007
        classified.update(files)
    classified.update(manifest.get("tier1", []))
    classified.update(slow)
    # DEAD-ENTRY check, mirroring the carve-out lane's. The membership test
    # that used to be here was VACUOUS: `classified` had just been updated with
    # the lane itself, so `f not in classified` was tautologically False and the
    # branch could never fire. (Its comment also justified the placement with a
    # NameError that could not happen — `on_demand` is bound at the top of the
    # function. A guard that cannot fail plus a rationale that cannot be checked
    # is two defects wearing one.) What actually needs catching is a lane entry
    # naming a file that does not exist: the exclusion would look healthy while
    # excluding nothing that runs.
    for f in sorted(on_demand):
        if not (TESTS_DIR / f).exists():
            issues.append(f"on-demand {f} has no tests/{f} (dead entry)")
    # #10194: a SUBTREE entry gets the same dead-entry treatment, in the form
    # that can actually be wrong for it — the directory is gone, or it is there
    # and holds no test file, in which case the exclusion reads as a saving while
    # excluding nothing (the same false assurance the literal case guards).
    for prefix in on_demand_subtree_prefixes(manifest):
        if not (TESTS_DIR / prefix).is_dir():
            issues.append(f"on-demand subtree {prefix}** has no tests/{prefix} "
                          "directory (dead entry)")
        elif not any(f.startswith(prefix) for f in on_demand):
            issues.append(f"on-demand subtree {prefix}** holds no test files "
                          "(dead entry — it excludes nothing)")
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
    """#1472/#6135: the workflow's fast matrix must come from the derivation,
    never a re-hardcoded list or a hand-written shard count; ENV_BROKEN_FILES
    must not be duplicated in env.

    #6135: the `test` job's matrix is the WHOLE derived matrix object
    (`${{ fromJSON(needs.changes.outputs.fast_matrix) }}`), so there are no
    `include:` rows left to inspect. The properties that mattered are now: the
    matrix is the selector's output (not a literal), the row count is not
    pinned in the workflow, and the aggregate still observes the `test` job.
    """
    import yaml
    issues = []
    try:
        wf = yaml.safe_load(open(workflow_path))  # noqa: SIM115
    except Exception as exc:
        return [f"cannot parse workflow {workflow_path}: {exc}"]
    test_job = wf.get("jobs", {}).get("test", {})
    matrix = test_job.get("strategy", {}).get("matrix")
    derived = "${{ fromJSON(needs.changes.outputs.fast_matrix) }}"
    if derived not in str(matrix):
        issues.append(
            "the `test` job matrix is not the derived fast_matrix output "
            f"(got {matrix!r}; expected {derived!r}) — a hand-written shard "
            "count is the drift #6135 exists to end")
    # A frozen `include:` row count is the old hand-written-literal shape: the
    # matrix must carry NO literal rows in the workflow.
    if isinstance(matrix, dict) and matrix.get("include"):
        issues.append("the `test` job matrix carries literal include rows "
                      "(the shard set must come from the selector, #6135)")
    # The aggregate's `needs:` is its whole claim (#5219) — if the derived
    # matrix's job id drifted, the required check would certify nothing.
    gate_needs = wf.get("jobs", {}).get("python-ci-gate", {}).get("needs", [])
    if "test" not in gate_needs:
        issues.append("python-ci-gate does not need the `test` job — the "
                      "required aggregate would not observe the fast shards")
    if "ENV_BROKEN_FILES" in wf.get("env", {}):
        issues.append("workflow env re-defines ENV_BROKEN_FILES (single source is ci_selection.py)")
    # #9539: the slow legs follow the same shape — their matrix is the
    # selector's `slow_matrix`, never committed rows. Before this, the two
    # hand-maintained rows were the lane's only source of truth and they rot
    # with every `durations` refresh/ new slow file (#9017 re-cut them after a
    # 6.4:1 drift had gone unnoticed); a literal row here is that drift class.
    slow_job = wf.get("jobs", {}).get("test-slow", {})
    slow_matrix = slow_job.get("strategy", {}).get("matrix")
    slow_derived = "${{ fromJSON(needs.changes.outputs.slow_matrix) }}"
    if slow_derived not in str(slow_matrix):
        issues.append(
            "the `test-slow` job matrix is not the derived slow_matrix output "
            f"(got {slow_matrix!r}; expected {slow_derived!r}) — hand-committed "
            "slow legs are the drift #9539 ends")
    if isinstance(slow_matrix, dict) and slow_matrix.get("include"):
        issues.append("the `test-slow` job matrix carries literal include rows "
                      "(the slow legs must come from the selector, #9539)")
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
    """#1266: extract LEGACY literal `- half:` / `files: >-` matrix rows from
    python-ci.yml — the pre-#6135 `test`-job shape.

    #6135 left the `test` job with no literal rows (its matrix is
    `fromJSON(...fast_matrix)`) and #9539 removed the last ones (`test-slow` is
    `fromJSON(needs.changes.outputs.slow_matrix)` too), so on the current tree
    this regex matches NOTHING and the parse is `{}`. That is harmless on the
    normal path, and not merely by luck: both callers reach it only on the
    integrity gate's FAILURE fallback — `ci_selection.py --integrity`, and the
    same composition in `ci_timing.integrity_problems()` (used by
    `--refresh-durations`) — i.e. only once `workflow_matrix_issues()` has
    ALREADY reported the workflow drifted off the derived shape. The normal path
    feeds the derived shards from `push_legs()` instead, and
    `workflow_matrix_issues()`'s own message ("the `test-slow` job matrix is not
    the derived slow_matrix output …") is what names WHICH job drifted. What
    this fallback still adds is the #1266 tie checks against a RE-INTRODUCED
    literal block in the legacy shape (`- half: [ab]` + `files: >-`); for any
    other drift it matches nothing and the `{}` arm of `workflow_halves_issues()`
    fails closed with "no matrix halves found in python-ci.yml (workflow parse
    failure)" rather than passing silently. The halves are folded scalars with
    bare file names (no .py) — the run step maps them to `tests/<name>.py` and
    `bench/*`. Returns {} when the parse fails so callers fail closed on
    "workflow changed shape" instead of silently passing.
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
                issues.append(f"shard entry {f} is in MORE THAN ONE shard (double-run, #1266)")
            seen.add(f)
    counts = {h: len(fs) for h, fs in halves.items()}
    # #3400: the balance invariant is DURATION once measured weights exist.
    # LPT packs by weight, so a heavy file dumped entirely on one half is
    # caught even when the counts look even — and a correct duration pack may
    # legitimately carry very different counts.
    # The ±3 count check would red that correct split, so it now applies only
    # to manifests with no durations map at all (e.g. the small test
    # fixtures, or a repo that has not adopted durations).
    durations = _durations_map(manifest)
    if durations:
        weights = {h: sum(_duration_weight(durations.get(
                            f if f.endswith(".py") else f + ".py"))
                          for f in fs)
                   for h, fs in halves.items()}
        lo, hi = min(weights.values()), max(weights.values())
        # P2 (#3407 review): compute the ratio BEFORE the f-string. `lo <= 0`
        # short-circuits the comparison but the message still evaluated
        # `hi / lo`, so the one branch written to CATCH a zero-weight half died
        # with ZeroDivisionError while formatting its own diagnosis. A
        # single-sided pack is reachable (a 1-file pool, or an all-zero
        # measured map) and this is the only check that catches it —
        # `leg_coverage_issues()` and `fast_files_absent_from_halves()` both
        # pass when one half is empty.
        # #6145, same root as `watchdog_headroom_issues`: decide at the
        # precision the message PRINTS. Comparing the exact ratio and then
        # rendering both operands at 2 dp let a fire inside (1.25, 1.255) print
        # "ratio 1.25x, tolerance 1.25x" — a diagnosis that reads as compliant
        # while the gate refuses. Decision and message are one value; the cost
        # is that a sub-0.005 excursion is inside the reporting quantum.
        ratio = float("inf") if lo <= 0 else round(hi / lo, 2)
        if lo <= 0 or ratio > HALF_DURATION_IMBALANCE_RATIO:
            issues.append(
                f"matrix halves duration-imbalanced: "
                f"{ {h: round(w / 60, 1) for h, w in weights.items()} } min "
                f"(ratio {ratio:.2f}x, tolerance "
                f"{HALF_DURATION_IMBALANCE_RATIO:.2f}x) — rebalance the "
                f"durations map (#3400)")
    else:
        # #6135: over EVERY shard, not just a/b — with N shards a tilt between
        # c and i is as much a watchdog risk as one between a and b.
        lo_c, hi_c = min(counts.values()), max(counts.values())
        if hi_c - lo_c > HALF_IMBALANCE_TOLERANCE:
            issues.append(
                f"matrix shards imbalanced: {counts} "
                f"(tolerance ±{HALF_IMBALANCE_TOLERANCE}) — "
                "rebalance before adding more files (#1266)")
    return issues


def fast_files_absent_from_halves(manifest: dict, halves: dict[str, list[str]]) -> list[str]:
    """#1266 (informational): manifest fast files that are in NO half — the
    full-matrix coverage hole. Slow files, bench/*, the epic #1647
    carve-out set (their leg is `carve_out`), and ENV_BROKEN_FILES are
    excluded. Kept as a warning (not fail-closed): closing it would push
    100+ files into the fast gate and blow the watchdog budget (see the
    scoping doc).

    ENV_BROKEN_FILES is excluded for the SAME reason as `slow` and
    `carve_out`: `fast_pool()` drops those groups from the push/full-matrix
    fast pool, and the halves this function is called with are derived from
    that pool (`push_legs`). A group the pool omits can therefore never appear
    in `halfset`, so leaving ENV_BROKEN_FILES out of this subtraction reported
    its members as a coverage hole on EVERY run — structurally, not because of
    the data. Empirically the set intersected with the surfaces is exactly one
    file, which is why the warning was always the same one. It is not a hole:
    the file needs a live environment the shard jobs do not have, and it is
    executed by `.github/scripts/verify-cutover` instead.

    Two boundaries, stated because the obvious generalisations of the sentence
    above are each FALSE:
      * This is the PUSH pool's exclusion, not every lane's. `select()`'s
        tier-2 path does not subtract ENV_BROKEN_FILES, so a PR touching a
        kept file can still place a member of the set in a tier-2 fast shard.
        That is a separate question and is NOT changed here.
      * `fast_pool()` also omits `on_demand`. That WAS unsubtracted here, and
        LATENT rather than live only while no on_demand file was a member of any
        surface; #10194 made the lane hold 42 surface-registered files, so it is
        subtracted above (see the #10194 note).

    The durable rule: this subtraction must stay in sync with `fast_pool`'s own
    filter, since the two are complements of the same pool.

    #10194: `on_demand` is now subtracted HERE too. The paragraph above called
    that case LATENT "only because no on_demand file is currently a member of
    any surface" — and #10194 ends exactly that premise: the 42 eval files stay
    registered in their surfaces (that registration is what classifies them) and
    are excluded from the gate by the LANE, so without this subtraction
    `--integrity` prints a permanent 36-file "coverage hole" that is not one.
    The stale-warning cost the paragraph describes is the reason it is fixed with
    the change rather than after a confused reader reports it.

    The cost of leaving it in was not cosmetic: `--integrity` printed
    `1 manifest fast files are in NO shard (full-matrix coverage hole, #1266)`
    on every run, and two independent readings of a shard-split PR took that
    warning at face value and reported that the split had dropped a test file
    from the matrix. A permanent warning that is always false trains its
    readers to discount the warning that is sometimes true — which is the
    whole #1266 check.
    """
    slow = set(manifest.get("slow_files", []))
    carve_out = carve_out_files(manifest)
    fast = set()
    for fs in manifest["surfaces"].values():
        fast.update(fs)
    fast -= slow
    fast -= carve_out
    fast -= ENV_BROKEN_FILES
    fast -= on_demand_files(manifest)
    halfset = {f for fs in halves.values() for f in fs}
    return sorted(f for f in fast if f[:-3] not in halfset)


def _duration_weight(value, default: float = DEFAULT_FAST_WEIGHT) -> float:
    """#3407 review: a malformed `durations` value must never crash a consumer.

    `split_fast_gate`'s sort key negates the weight, so a `None`/string value
    raised `TypeError: bad operand type for unary -` deep inside the sort —
    which killed `--integrity` in `leg_coverage_issues()` *before*
    `duration_issues()` was ever called, so the gate that exists to NAME the bad
    entry tracebacked instead. A `NaN` was worse: every comparison is False, so
    it passed both the value check and the imbalance check and silently
    produced a maximally single-sided pack with a green exit.

    Coercing to the default here means every consumer degrades safely, while
    `duration_issues()` still names the offending entry and fails the gate.
    `bool` is excluded explicitly (`isinstance(True, int)` is True).

    #3407 review cycle 3: the finiteness probe must be TOTAL. `math.isfinite`
    converts to float, so an int beyond float range (>=309 digits) raised
    `OverflowError` — i.e. the probe introduced to stop a crash could itself
    crash. A negative duration is impossible data and is likewise coerced.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    try:
        finite = math.isfinite(value)
    except OverflowError:  # an int beyond float range
        finite = False
    if not finite or value < 0:
        return default
    return value


def split_fast_gate(files, durations: dict, shards: int = DEFAULT_FAST_SHARDS,
                    default_weight: float = DEFAULT_FAST_WEIGHT):
    """#1473/#6135: LPT greedy pack of the selected fast-gate files across N
    shards by measured duration — deterministic (ties and the empty-bin choice
    go to the LOWEST label; assignment order is the sorted weight order).

    Returns a list of N file lists (position -> `shard_labels`), each entry
    prefixed with `tests/`. Raises ValueError on non-list input (guards the
    'ALL' full-mode string).

    #6135: N comes from the manifest's `fast_shards`, not a literal here, and is
    NOT capped by the file count — a shard with no files is fine (it exits
    early), while a MISSING shard makes the event's job-name set a non-superset
    of the push lane's and the merge rail refuses NOT COMPARABLE. The only cap is
    MAX_FAST_SHARDS (the a..z label space). Note the `--integrity` balance
    check runs over the PUSH shards, not these: at any S <= MAX_FAST_SHARDS the
    push pool is far larger, so the push shards are never empty and that
    check's zero-weight arm is unreachable there.
    """
    if not isinstance(files, list):
        raise ValueError(f"split_fast_gate expects a list, got {type(files).__name__}")
    try:
        n = int(shards)
    except (TypeError, ValueError):
        n = DEFAULT_FAST_SHARDS
    weighted = []
    for f in files:
        name = f[len("tests/"):] if f.startswith("tests/") else f
        weighted.append((name, _duration_weight(
            durations.get(name, default_weight), default_weight)))
    # #6135: the shard COUNT is NOT capped by the file count. It must be the
    # SAME count as the push matrix on every event, because the merge rail's
    # lane-parity compares job-NAME SETS: a tier-2 PR that emitted fewer shards
    # than main runs would be refused `NOT COMPARABLE` (main's extra `test (c)`…
    # would be "missing" from the PR). A shard with no files exits early with an
    # honest report; a shard that does not EXIST cannot be compared at all.
    # The floor of 1 keeps an empty selection from expanding to zero matrix
    # rows (GitHub refuses an empty matrix).
    n = max(1, min(n, MAX_FAST_SHARDS))
    bins: list[list[str]] = [[] for _ in range(n)]
    totals = [0.0] * n
    for name, w in sorted(weighted, key=lambda x: (-x[1], x[0])):
        # ties and the initial all-zero state resolve to the lowest index
        # (the historical "ties -> a").
        i = totals.index(min(totals))
        bins[i].append("tests/" + name)
        totals[i] += w
    return bins


def _durations_map(manifest: dict) -> dict:
    """`durations` as a mapping, or `{}` — never a non-mapping. (#3407 c4)

    `None`/absent is the documented "this repo has not adopted the duration
    gate" state and collapses to `{}` (a PASS, per `duration_coverage_issues`).
    A non-mapping is malformed and is NAMED by `duration_issues` before it gets
    here; this exists so a PRODUCER path (`--split`) can never crash either.
    """
    raw = manifest.get("durations")
    return raw if isinstance(raw, dict) else {}


def duration_issues(manifest: dict) -> list[str]:
    """#1473/#4712: every durations key must be a CLASSIFIED test file.

    Slow and carve-out lane keys carry their measured cost here too, so a cost
    regression in a lane that exists *because* it is expensive is visible.
    Values must additionally be numeric and finite.
    """
    issues = []
    # #3407 review cycle 4 (pre-existing): this site and `--split` below used
    # `.get("durations", {})`, which returns a present-but-NULL `durations:` key
    # as `None` — the empty-map state `duration_coverage_issues` documents as
    # "NOT a failure" — and crashed with a raw TypeError instead.
    #
    # The precise predicate: `None`/absent means "this repo has not adopted the
    # duration gate" and is a PASS. ANY other non-mapping (`0`, a string, a
    # list) is a malformed declaration and must be NAMED — collapsing it into
    # the empty case with `or {}` would have turned a wrong crash into a silent
    # wrong pass.
    raw = manifest.get("durations")
    if raw is not None and not isinstance(raw, dict):
        return [f"durations is not a mapping: {type(raw).__name__}"]
    durations = _durations_map(manifest)
    classified = set()
    for s, files in manifest["surfaces"].items():  # noqa: B007
        classified.update(files)
    classified.update(manifest.get("tier1", []))
    # Same reason as `integrity()`: an on-demand file is classified into a LANE
    # rather than a surface, and its measured cost belongs here precisely
    # because it is the lane's whole justification.
    classified.update(on_demand_files(manifest))
    for name in durations:
        if name not in classified:
            issues.append(f"durations key {name} is not classified in the manifest")
        # P2 (#3407 review): validate the VALUE, not just the key. Both guards
        # iterated keys only, so a hand-edit typo in a now-505-line map passed
        # `--integrity` silently and then crashed `push_legs` with a TypeError
        # inside `split_fast_gate`'s sort key — the gate's whole job is to name
        # the bad entry instead of tracebacking on it. `bool` is excluded
        # explicitly: it is an `int` subclass and would slip through.
        v = durations[name]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            issues.append(f"durations value for {name} is not numeric: {v!r}")
        else:
            # #3407 review P2: a NaN passed the type check AND was invisible to
            # the imbalance guard (every comparison is False), so it produced a
            # maximally single-sided pack with a green exit. Type-checking is
            # necessary but not sufficient — finiteness is the real predicate.
            # Cycle 3: the probe must be TOTAL (`math.isfinite` raises
            # OverflowError on an int beyond float range), and a negative
            # duration is impossible data that otherwise exited 0.
            try:
                finite = math.isfinite(v)
            except OverflowError:
                finite = False
            if not finite:
                issues.append(f"durations value for {name} is not finite: {v!r}")
            elif v < 0:
                issues.append(f"durations value for {name} is negative: {v!r}")
    return issues


def duration_coverage_issues(manifest: dict,
                             threshold: float = DURATION_COVERAGE_MIN) -> list[str]:
    """#3400: the `durations` map must cover (almost) the whole fast pool.

    A fast file with no measured duration is packed at DEFAULT_FAST_WEIGHT,
    so a mostly-empty map silently turns `split_fast_gate` back into a
    count-based pack — the exact rot that left 15 weights for 500 fast files
    (#1266/#1473) and left the push halves duration-blind. Fail-closed once
    the map is populated; an ABSENT or EMPTY map is NOT a failure, so a repo
    that has not adopted durations is never hard-failed by this guard.
    """
    durations = _durations_map(manifest)
    if not durations:
        return []
    fast = fast_pool(manifest)
    if not fast:
        return []
    missing = sorted(f for f in fast if f not in durations)
    coverage = (len(fast) - len(missing)) / len(fast)
    if coverage < threshold:
        return [
            f"durations coverage {coverage:.1%} "
            f"({len(fast) - len(missing)}/{len(fast)} fast files) is below the "
            f"{threshold:.0%} floor — {len(missing)} file(s) pack at the flat "
            f"{DEFAULT_FAST_WEIGHT}s default, so the push split is effectively "
            f"count-based (#3400). Refresh config/ci-surfaces.yml `durations` "
            f"from tools/ci_timing.py / the CI junit artifacts. Example: "
            f"{missing[:5]}"
        ]
    return []


# ── #2938: surface audit (report-only, non-blocking) ─────────────────────
#
# `config/ci-surfaces.yml` membership is hand-curated: a surface's members are
# the test files that should RUN when that surface's source changes, which is
# not the same thing as "the files that reference that surface's source".
# Deriving membership mechanically was rejected (#2938: it proposed 326/562
# file moves, 28 out of `onboarding`, and emptied `classify`) because a naive
# import scan cannot resolve `from tortoise import X`, a source path no
# SOURCE_PATTERNS entry maps, fixture/HTTP indirection, or human intent. This
# mode derives nothing: it prints the mismatches, with evidence, for a human.
#
# Pin-resolution rules — each gap the mechanical attempt hit is handled (or
# explicitly surfaced):
#   (a) `from tortoise import X` / `import tortoise.X` -> `tortoise/X.py` or
#       `tortoise/X/__init__.py`, accepted only when the file exists on disk
#       (a class re-exported from the package root is not a submodule). The
#       bare package roots `tortoise` / `tests` are never pins themselves:
#       the root re-exports many surfaces and every test imports it.
#   (b) a pinned source path that no SOURCE_PATTERNS entry maps (the #2938
#       `tortoise/api.py` case, since fixed by mapping it) is reported under
#       "uncovered source paths" rather than silently binned as `core` (the
#       selection fallback), naming the pinning files and the surfaces they
#       are registered under. A path named after a surface
#       (`tortoise/<surface>.py`) is additionally called out under
#       "SOURCE_PATTERNS coverage gaps".
#   (c) string references: every non-docstring string literal is scanned for
#       path-like tokens (a subprocess argv, a Path(...) literal, a path read
#       from disk); evidence is rendered quoted (`file <- "path/string"`).
#   (d) fixture/helper indirection: a test that imports another module under
#       tests/ inherits that module's pins transitively (cycle-safe), EXCEPT
#       helpers in SHARED_MODULES (conftest.py, fake_control_plane.py) —
#       those already force the full matrix, so following them would make
#       every test "pin" everything. Helper-derived evidence is tagged
#       `(via tests/helper.py)` so direct and indirect evidence are distinct.
# SHARED_MODULES are excluded from pins entirely (a change to them forces the
# full matrix, so a file importing sdk.py is not a mismatch).
#
# Still undecided by construction (kept visible, never hidden):
#   - `classify` and `core` have no SOURCE_PATTERNS entry, so "pins nothing
#     from this surface" is undefined for them; the report says so and skips
#     removal analysis instead of proposing to empty `classify`.
#   - intent ("this suite should run when this area changes") is not derivable
#     from references; a removal candidate whose only pins are uncovered or
#     helper-derived is labelled rather than asserted.
#   - seams the path scan cannot resolve at all (a pytest marker, a fixture
#     name, an HTTP route string with no source path) stay invisible.

# Bare package roots that are not pins: `from tortoise import X` / `import
# tortoise` mean "the package", and the root re-exports many surfaces.
_AUDIT_NAMESPACE_ROOTS = frozenset({"tortoise", "tests"})

# Mirror of select()'s per-file fallback branch: engine/config paths with no
# SOURCE_PATTERNS entry select the `core` surface.
_AUDIT_CORE_FALLBACK = ("tortoise/", "graph-scripts/", "config/",
                        "validation/", "packs/")

# Source trees worth naming in the uncovered report (tests/ is test-internal
# scaffolding, not a selectable source surface).
_AUDIT_SOURCE_TREES = ("tortoise/", "tools/", "battery/", "graph-scripts/",
                       "benchmarks/", "config/", "packs/", "validation/",
                       "website/", "services/", "integrations/")

# Source trees whose modules are followed TRANSITIVELY when a test imports
# them (rule (d) extension, #2938 review P2). A test reaching a surface only
# through a repo-root helper (tools/longmem_eval/retrieve.py ->
# tortoise/retrieval.py) must not read as "pins nothing". `tortoise/` is
# deliberately excluded: it IS the surface source, and its internals are not
# helper indirection.
_AUDIT_HELPER_TREES = ("tests/", "tools/", "graph-scripts/", "battery/",
                       "benchmarks/", "services/", "integrations/")

_AUDIT_PATH_TOKEN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.\\/-]*")
_AUDIT_EVIDENCE_CAP = 6


def _audit_is_shared(path: str) -> bool:
    return any(path == s or (s.endswith("/") and path.startswith(s))
               for s in SHARED_MODULES)


def _audit_module_files(module: str, repo: Path, tests_dir: Path) -> set[str]:
    """Existing repo files a dotted module may resolve to.

    Checked under BOTH import roots: the repo root and tests/ (pytest puts
    tests/ on sys.path, so `eval.harness.schema` lives at
    tests/eval/harness/schema.py while `tortoise.embeddings` lives at
    tortoise/embeddings.py).
    """
    rel = module.replace(".", "/")
    out: set[str] = set()
    for root in (repo, tests_dir):
        for cand in (root / f"{rel}.py", root / rel / "__init__.py"):
            if cand.is_file():
                out.add(cand.relative_to(repo).as_posix())
    return out


def _audit_classify(path: str) -> tuple[frozenset[str], bool, str]:
    """(surfaces, covered, reason) for a repo-relative source path.

    ``reason`` is ``pattern`` (matched SOURCE_PATTERNS), ``shared``
    (SHARED_MODULES -> full matrix), ``core-fallback`` (select()'s core
    branch) or ``outside`` (not selection-relevant).
    """
    if _audit_is_shared(path):
        return frozenset(), False, "shared"
    surfaces = {s for s, pats in SOURCE_PATTERNS.items()
                if any(path == p or path.startswith(p) for p in pats)}
    if surfaces:
        return frozenset(surfaces), True, "pattern"
    if any(path.startswith(p) for p in _AUDIT_CORE_FALLBACK):
        return frozenset({"core"}), False, "core-fallback"
    return frozenset(), False, "outside"


def _audit_parse(abs_path: Path) -> ast.AST | None:
    try:
        return ast.parse(abs_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def _audit_relative_base(node: ast.ImportFrom, pkg: list[str]) -> str:
    """Resolve a (possibly relative) ImportFrom base module name."""
    if node.level == 0:
        return node.module or ""
    base = list(pkg)
    drop = node.level - 1
    if drop:
        base = base[:-drop] if drop <= len(base) else []
    if node.module:
        base = base + node.module.split(".")
    return ".".join(base)


def _audit_import_nodes(tree: ast.AST, module_level_only: bool):
    """Import nodes to honor, optionally skipping deferred (call-time) ones.

    #2938 review P1: `ast.walk` visits every nested import, so an import
    inside a function body reads exactly like a module-level one. A helper
    module that is only IMPORTED (never called) never executes its
    function-local imports, so those must not become strong pins. Descend
    through statements that execute at import time (if/try/with/class
    bodies); stop at function/lambda scopes.
    """
    if not module_level_only:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                yield node
        return
    stack = [tree]
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda)):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node
            continue
        stack.extend(ast.iter_child_nodes(node))


def _audit_import_paths(tree: ast.AST, module_name: str, repo: Path,
                        tests_dir: Path,
                        module_level_only: bool = False) -> list[str]:
    """Repo-relative files the file's import statements resolve to.

    `from A import n` descends into `A.n` as well as `A`, so
    `from tortoise import onboarding` pins tortoise/onboarding/__init__.py
    and `from tortoise.api import EventAPI` pins tortoise/api.py (api is a
    module, not a package, so the name descent finds nothing and the base
    does).

    ``module_level_only`` skips imports nested in function/lambda bodies
    (see :func:`_audit_import_nodes`): true for a transitively followed
    helper, false for the root test file (whose function bodies DO run).
    """
    pkg = module_name.split(".")[:-1]
    out: set[str] = set()
    for node in _audit_import_nodes(tree, module_level_only):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in _AUDIT_NAMESPACE_ROOTS:
                    continue
                out.update(_audit_module_files(alias.name, repo, tests_dir))
        elif isinstance(node, ast.ImportFrom):
            base = _audit_relative_base(node, pkg)
            if base and base not in _AUDIT_NAMESPACE_ROOTS:
                out.update(_audit_module_files(base, repo, tests_dir))
            for alias in node.names:
                if alias.name == "*":
                    continue
                sub = f"{base}.{alias.name}" if base else alias.name
                out.update(_audit_module_files(sub, repo, tests_dir))
    return sorted(out)


def _audit_string_paths(tree: ast.AST) -> list[str]:
    """Path-like tokens from non-docstring string literals (rule (c))."""
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstrings:
            continue
        for raw in _AUDIT_PATH_TOKEN.findall(node.value):
            token = raw.replace("\\", "/")
            while token.startswith("./"):
                token = token[2:]
            out.add(token)
    return sorted(out)


def _audit_own_refs(abs_path: Path, module_name: str, repo: Path,
                    tests_dir: Path,
                    cache: dict[tuple[str, bool], tuple] | None = None,
                    module_level_only: bool = False):
    """Direct reference scan of one file.

    Returns ``(surface_pins, uncovered, shared, helpers)`` where
    surface_pins maps a SOURCE_PATTERNS surface to evidence strings,
    uncovered maps a pinned-but-unmapped source path to evidence, shared is
    the referenced SHARED_MODULES set, and helpers is the set of repo-relative
    modules imported and worth following (rule (d): tests/ helpers plus any
    module in ``_AUDIT_HELPER_TREES``). ``cache`` memoizes the scan (a helper
    reached by many test roots is parsed once); the cache key carries
    ``module_level_only`` because the same file can be scanned both as a
    test root (all imports) and as a helper (module-level only).
    """
    key = (abs_path.as_posix(), module_level_only)
    if cache is not None and key in cache:
        return cache[key]
    surface_pins: dict[str, set[str]] = {}
    uncovered: dict[str, set[str]] = {}
    shared: set[str] = set()
    helpers: set[str] = set()
    tree = _audit_parse(abs_path)
    if tree is None:
        result = (surface_pins, uncovered, shared, helpers)
        if cache is not None:
            cache[key] = result
        return result

    def record(path: str, evidence: str) -> None:
        surfaces, covered, reason = _audit_classify(path)
        if reason == "shared":
            shared.add(path)
        elif covered:
            for s in surfaces:
                surface_pins.setdefault(s, set()).add(evidence)
        elif reason == "core-fallback" or any(
                path.startswith(p) for p in _AUDIT_SOURCE_TREES):
            uncovered.setdefault(path, set()).add(evidence)

    for path in _audit_import_paths(tree, module_name, repo, tests_dir,
                                    module_level_only):
        if path.startswith("tests/") and path.endswith(".py"):
            # test-internal module: a helper to follow, not a source pin.
            if _audit_is_shared(path):
                shared.add(path)
            else:
                helpers.add(path)
            continue
        record(path, path)
        # #2938 review P2: a repo-root helper (tools/, graph-scripts/, …) is
        # both a pin of its own surface AND indirection — follow it so a test
        # reaching a surface only through it is not read as pinning nothing.
        if path.endswith(".py") and path.startswith(_AUDIT_HELPER_TREES) \
                and not _audit_is_shared(path):
            helpers.add(path)
    for token in _audit_string_paths(tree):
        # rule (c) precision: a string naming no file/dir on disk is data,
        # not a source reference (e.g. an MCP method name "tools/call").
        if not (repo / token).exists():
            continue
        record(token, f'"{token}"')
    result = (surface_pins, uncovered, shared, helpers)
    if cache is not None:
        cache[key] = result
    return result


def _audit_file_refs(abs_path: Path, rel: str, repo: Path, tests_dir: Path,
                     cache: dict[tuple[str, bool], tuple] | None = None):
    """Pins for one test file, following its imported helper modules.

    Cycle-safe BFS over repo-relative paths: each helper contributes its own
    pins tagged ``(via tests/helper.py)`` (or ``(via tools/…)`` for a
    repo-root helper); SHARED_MODULES helpers are never followed. Helpers are
    scanned module-level only (#2938 review P1): a helper is imported, not
    called, so its function-local imports never execute — and a helper's own
    deferred import of another helper is not followed either.
    """
    surface_pins: dict[str, set[str]] = {}
    uncovered: dict[str, set[str]] = {}
    shared: set[str] = set()
    seen: set[str] = set()
    root = rel if rel.startswith("tests/") else f"tests/{rel}"
    queue = [root]
    while queue:
        cur = queue.pop(0)
        if cur in seen:
            continue
        seen.add(cur)
        cur_abs = repo / cur
        if not cur_abs.is_file():
            continue
        module_name = cur[:-3].replace("/", ".")
        sp, unc, sh, helpers = _audit_own_refs(
            cur_abs, module_name, repo, tests_dir, cache,
            module_level_only=(cur != root))
        shared |= sh
        tag = "" if cur == root else f" (via {cur})"
        for s, evs in sp.items():
            for ev in evs:
                surface_pins.setdefault(s, set()).add(ev + tag)
        for p, evs in unc.items():
            for ev in evs:
                uncovered.setdefault(p, set()).add(ev + tag)
        for helper in sorted(helpers):
            if helper not in seen:
                queue.append(helper)
    return surface_pins, uncovered, shared


def _audit_coverage_gaps(repo: Path) -> dict[str, str]:
    """Surface-named source paths absent from that surface's SOURCE_PATTERNS.

    The #2938 case (b): a source named after a surface (`tortoise/<s>.py`,
    e.g. `tortoise/api.py` before this PR mapped it) exists, but that
    surface's `SOURCE_PATTERNS` entry does not list it — so a change to it
    selects `core` (or nothing), and the surface-registered tests pinning it
    never run. Reporting this explicitly is the whole point of the audit:
    silently binning the path as `core` would make the numbers lie.
    """
    gaps: dict[str, str] = {}
    for surface in sorted(SOURCE_PATTERNS):
        pats = SOURCE_PATTERNS[surface]
        for cand in (f"tortoise/{surface}.py", f"tortoise/{surface}",
                     surface):
            if not (repo / cand).exists() or _audit_is_shared(cand):
                continue
            covered = any(cand == p or cand.startswith(p) or p == cand + "/"
                          for p in pats)
            if not covered:
                gaps[cand] = surface
                break
    return gaps


def _audit_is_string_evidence(evidence: str) -> bool:
    """True when the evidence came from a path *string*, not an import."""
    return evidence.startswith('"')


def _audit_entries(entries) -> list:
    """Manifest surface value as a list.

    #2938 review P3: a curated manifest can carry `null` for a surface, and a
    hard-edited one a scalar. `None` means empty (like integrity()'s
    `files or ()`); a non-list scalar is malformed and is treated as empty
    rather than silently iterated (`api: "test_x.py"` used to become a list
    of characters).
    """
    if entries is None:
        return []
    return _surface_members(entries)


def _audit_gap_selection(rec: dict, manifest: dict) -> tuple[list[str], list[str], list[str]]:
    """(selected_surfaces, victims, runners) for one coverage-gap record.

    #2938 review P2: the previous renderer asserted the non-gap pinners "run
    via {others}", but `others` is just the union of non-gap registrations —
    `select([unmapped_path], ...)` selects only the fallback `core` surface
    and does NOT select `ep`, so a pinner registered solely under `ep` does
    not run. A pinner RUNS when it is in the change's fast-gate `test_files`,
    in the selected surfaces' slow leg, or in a triggered carve-out job;
    every other pinner is a victim. Callers pass a manifest whose surface
    values are already normalised via :func:`_audit_entries`.
    """
    selection = select([rec["path"]], "pull_request", manifest)
    if selection["full"] or selection["test_files"] == "ALL":
        return sorted(manifest["surfaces"]), [], sorted(rec["files"])
    runs: set[str] = set(selection["test_files"])
    runs |= set(selection.get("slow_selected") or [])
    if selection.get("carve_out_run"):
        runs |= set(manifest.get("carve_out") or [])
    runners, victims = [], []
    for f in sorted(rec["files"]):
        base = f.rsplit("/", 1)[-1]
        (runners if (f in runs or base in runs) else victims).append(f)
    return sorted(selection["surfaces"]), victims, runners


def surface_audit(manifest: dict, repo: Path | None = None,
                  tests_dir: Path | None = None) -> dict:
    """#2938: report-only manifest/source mismatch audit.

    Never mutates the manifest and never feeds selection: it returns a
    structured report consumed by :func:`render_surface_audit`. ``repo`` /
    ``tests_dir`` are injectable for synthetic-manifest tests. Surface values
    are normalised with :func:`_audit_entries` (``null``/scalars -> empty),
    so a curated manifest with a malformed value still audits without raising.
    """
    repo = Path(repo) if repo is not None else REPO
    tests_dir = Path(tests_dir) if tests_dir is not None else TESTS_DIR
    owners: dict[str, list[str]] = {
        s: _audit_entries(entries)
        for s, entries in manifest["surfaces"].items()
    }
    # select() gets the normalised surfaces (a scalar value would crash its
    # `files.update(...)`); the original manifest is left untouched.
    audit_manifest = {**manifest, "surfaces": owners}
    source_mapped = set(SOURCE_PATTERNS)
    gaps = _audit_coverage_gaps(repo)
    cache: dict[tuple[str, bool], tuple] = {}

    disk: list[str] = []
    for f in sorted(tests_dir.rglob("test_*.py")):
        rel_path = f.relative_to(tests_dir)
        if rel_path.parts and rel_path.parts[0] == "e2e":
            continue
        disk.append(rel_path.as_posix())

    def registered_in(rel: str) -> list[str]:
        base = rel.rsplit("/", 1)[-1]
        return sorted(s for s, entries in owners.items()
                      if rel in entries or base in entries)

    by_surface = {
        s: {"members": list(entries),
            "source_mapped": s in source_mapped,
            "removal": [], "addition": []}
        for s, entries in owners.items()
    }
    uncovered_index: dict[str, dict] = {}
    for rel in disk:
        surface_pins, uncovered, shared = _audit_file_refs(
            tests_dir / rel, rel, repo, tests_dir, cache)
        registered = registered_in(rel)
        for s in sorted(surface_pins):
            if s in by_surface and s not in registered:
                evs = sorted(surface_pins[s])
                # #2938 review P1-2: a path STRING is weak evidence — it may
                # be fixture data (test_ci_selection.py's own corpus names six
                # surfaces). Keep it visible (the renderer labels it), but only
                # an import-derived reference makes the file an addition
                # candidate a human should act on.
                by_surface[s]["addition"].append({
                    "file": rel, "evidence": evs,
                    "strong": any(not _audit_is_string_evidence(e)
                                   for e in evs),
                    "registered": registered})
        for s in registered:
            if s not in source_mapped or surface_pins.get(s):
                continue
            by_surface[s]["removal"].append({
                "file": rel, "surface": s,
                "pins": {t: sorted(v)
                         for t, v in sorted(surface_pins.items())},
                "uncovered": sorted(uncovered), "shared": sorted(shared),
                "gap": sorted(p for p in uncovered if p in gaps)})
        for path, evs in uncovered.items():
            rec = uncovered_index.setdefault(
                path, {"path": path, "files": {}, "weak_files": {},
                       "evidence": set()})
            evs = sorted(evs)
            # #2938 review P1-2: only a file with an import-derived reference
            # to this path counts as a STRONG pinnner. A string-only pinnner
            # (test_ci_selection.py names tortoise/api.py as fixture data) is
            # kept in weak_files so the "never run" claim is not inflated.
            bucket = (rec["files"]
                      if any(not _audit_is_string_evidence(e) for e in evs)
                      else rec["weak_files"])
            bucket[rel] = sorted(registered)
            rec["evidence"].update(evs)

    uncovered = []
    for rec in uncovered_index.values():
        rec["files"] = {f: rec["files"][f] for f in sorted(rec["files"])}
        rec["weak_files"] = {f: rec["weak_files"][f]
                             for f in sorted(rec["weak_files"])}
        rec["registered"] = sorted(
            {s for regs in rec["files"].values() for s in regs}
            | {s for regs in rec["weak_files"].values() for s in regs})
        rec["evidence"] = sorted(rec["evidence"])
        rec["surface"] = gaps.get(rec["path"])
        uncovered.append(rec)
    uncovered.sort(key=lambda r: (r["surface"] is None, r["path"]))

    coverage_gaps = [r for r in uncovered if r["surface"] is not None]
    for rec in coverage_gaps:
        sel, victims, runners = _audit_gap_selection(rec, audit_manifest)
        rec["selected_surfaces"] = sel
        rec["victims"] = victims
        rec["runs"] = runners

    # #2938 review P3: a curated manifest can carry `null` for a surface;
    # a bare `entries.count()` would traceback. `owners` is normalised above.
    duplicates: dict[str, list[str]] = {}
    for s, entries in owners.items():
        dupes = sorted({e for e in entries if entries.count(e) > 1})
        if dupes:
            duplicates[s] = dupes

    return {
        "surfaces": by_surface,
        # A lane file is unregistered ON PURPOSE — that is what the lane means.
        # Reporting it under "files in NO surface" reads as drift, and the
        # obvious fix for that reading is to re-register the file into a
        # surface, which silently puts it back on the merge gate. The two
        # states are separated instead of merged.
        "no_surface": sorted(r for r in disk
                             if not registered_in(r)
                             and r not in on_demand_files(manifest)),
        "on_demand": sorted(r for r in disk
                            if not registered_in(r)
                            and r in on_demand_files(manifest)),
        "duplicates": {s: d for s, d in duplicates.items() if d},
        "coverage_gaps": coverage_gaps,
        "uncovered": [r for r in uncovered if r["surface"] is None],
        "disk_files": len(disk),
    }


def _audit_fmt_paths(paths: list[str], cap: int = _AUDIT_EVIDENCE_CAP) -> str:
    shown = ", ".join(paths[:cap])
    if len(paths) > cap:
        shown += f", … (+{len(paths) - cap} more)"
    return shown


def _audit_label_evidence(evidence: str) -> str:
    """Mark string-derived evidence so a human sees it is not an import."""
    if not _audit_is_string_evidence(evidence):
        return evidence
    core, sep, via = evidence.partition(" (via ")
    if sep:
        return f"{core} (string, not an import; via {via}"
    return f"{core} (string, not an import)"


def _audit_fmt_evidence(evs: list[str],
                        cap: int = _AUDIT_EVIDENCE_CAP) -> str:
    return _audit_fmt_paths([_audit_label_evidence(e) for e in evs], cap)


def _audit_removal_line(entry: dict) -> str:
    parts = [f"{s}: {_audit_fmt_evidence(v)}"
             for s, v in sorted(entry["pins"].items())]
    if entry["shared"]:
        parts.append(f"shared: {_audit_fmt_paths(entry['shared'])}")
    if entry["uncovered"]:
        parts.append(f"uncovered: {_audit_fmt_paths(entry['uncovered'])}")
    if entry["gap"]:
        parts.append(f"⚑ coverage gap: {_audit_fmt_paths(entry['gap'])}")
        warn = ("  ⚑ surface-named source is unmapped — fix SOURCE_PATTERNS "
                "before removing")
    else:
        # #2938 review P3: every entry in this list has NO pin for its own
        # surface BY DEFINITION, so the "don't act" cue belongs on all of
        # them — shared-only members (`shared: tortoise/sdk.py`) used to
        # render as bare removal candidates.
        warn = (f"  ⚠ no `{entry['surface']}` pin resolved — helper/dynamic "
                "indirection unresolved; confirm before removing")
    body = "; ".join(parts) if parts else "no source references at all"
    return f"  {entry['file']} <- ({body}){warn}"


def render_surface_audit(report: dict) -> str:
    """Human-readable rendering of :func:`surface_audit` (deterministic)."""
    lines = [
        "CI surface audit (#2938) — report only; select() / --integrity are "
        "unchanged.",
        f"scanned tests/**/test_*.py (tests/e2e/ exempt): {report['disk_files']} files",
        "",
        "Summary (surface: members / no-pin removal candidates / addition "
        "candidates):",
    ]
    for s in sorted(report["surfaces"]):
        v = report["surfaces"][s]
        if v["source_mapped"]:
            n_add = sum(1 for e in v["addition"] if e["strong"])
            lines.append(f"  {s:<11} {len(v['members']):>4} members   "
                         f"{len(v['removal']):>3} no-pin   "
                         f"{n_add:>3} additions")
        else:
            lines.append(f"  {s:<11} {len(v['members']):>4} members   "
                         "  n/a (no SOURCE_PATTERNS entry)")
    unmapped = sorted(s for s, v in report["surfaces"].items()
                      if not v["source_mapped"])
    if unmapped:
        lines += [
            "",
            f"⚠ {', '.join(unmapped)}: no SOURCE_PATTERNS entry — nothing maps a "
            "source change to this surface, so \"pins",
            "  nothing from it\" is undefined and removal analysis is skipped "
            "(a naive scan would propose",
            "  emptying `classify`; the rejected mechanical rule did exactly "
            "that).",
        ]
    lines.append("")
    for s in sorted(report["surfaces"]):
        v = report["surfaces"][s]
        if not v["source_mapped"]:
            continue
        lines.append(f"── {s} " + "─" * max(0, 58 - len(s)))
        if v["removal"]:
            lines.append(f"removal candidates — members pinning nothing from "
                         f"`{s}` ({len(v['removal'])}):")
            lines += [_audit_removal_line(e) for e in v["removal"]]
        else:
            lines.append(f"removal candidates — none (every member pins `{s}`)")
        strong_add = [e for e in v["addition"] if e["strong"]]
        weak_add = [e for e in v["addition"] if not e["strong"]]
        if strong_add:
            lines.append(f"addition candidates — files pinning `{s}` but not "
                         f"registered ({len(strong_add)}):")
            for e in strong_add:
                reg = ", ".join(e["registered"]) or "none"
                lines.append(f"  {e['file']} <- "
                             f"{_audit_fmt_evidence(e['evidence'])}"
                             f"  (registered: {reg})")
        else:
            lines.append("addition candidates — none")
        if weak_add:
            lines.append(f"string-only references — weak evidence, not counted "
                         f"as pins ({len(weak_add)}):")
            for e in weak_add:
                reg = ", ".join(e["registered"]) or "none"
                lines.append(f"  {e['file']} <- "
                             f"{_audit_fmt_evidence(e['evidence'])}"
                             f"  (registered: {reg})")
        lines.append("")

    gaps = report["coverage_gaps"]
    if gaps:
        lines.append(f"⚑ SOURCE_PATTERNS coverage gaps ({len(gaps)}) — surface-named "
                     "source that no pattern maps:")
        for rec in gaps:
            # #2938 review P2: do NOT assert which files run from the union of
            # non-gap registrations — `select()` decides. Victims are the
            # pinners this change does not select (e.g. a pinner registered
            # only under a surface the change does not select).
            victims = rec["victims"]
            selected = "/".join(rec["selected_surfaces"]) or "none"
            weak_victims = sorted(
                f for f, regs in rec["weak_files"].items()
                if rec["surface"] in regs)
            total = len(rec["files"]) + len(rec["weak_files"])
            weak_note = (f" (+{len(weak_victims)} string-only pinnner(s), "
                         "weak)" if weak_victims else "")
            lines.append(f"  {rec['path']} is not in "
                         f"SOURCE_PATTERNS[{rec['surface']!r}] — a change to "
                         f"it selects `{selected}`,")
            if victims:
                lines.append(f"    never run on a change to {rec['path']}: "
                             f"{_audit_fmt_paths(victims)}")
            else:
                lines.append(f"    every pinning file still runs on a change "
                             f"to {rec['path']} (via `{selected}`)")
            lines.append(f"    (pinned by {total} file(s) in total; "
                         f"{len(victims)} never run, {len(rec['runs'])} run via "
                         f"`{selected}`{weak_note}; evidence: "
                         f"{_audit_fmt_evidence(rec['evidence'])})")
        lines.append("")

    nosurf = report["no_surface"]
    lines.append(f"files in NO surface ({len(nosurf)})"
                 + (":" if nosurf else " — none"))
    for f in nosurf:
        lines.append(f"  {f} <- (unregistered; a tests/ change cannot select it)")
    # Reported from the report's own `on_demand` key, populated by
    # `surface_audit()` — which is the function that has the manifest. (An
    # earlier version of this line read `report["manifest"]`, a key the report
    # does not carry, so it silently evaluated to an empty list and the note
    # never appeared. A fix that cannot fail is not a fix.)
    lane = report.get("on_demand", [])
    if lane:
        lines.append(f"in the on_demand lane ({len(lane)}) — unregistered on "
                     f"purpose; they RUN but do not gate the merge:")
        for f in lane:
            lines.append(f"  {f}")
        lines.append("")
    lines.append("")

    dupes = report["duplicates"]
    total_dupes = sum(len(v) for v in dupes.values())
    lines.append(f"same-surface duplicate entries ({total_dupes})"
                 + (":" if total_dupes else " — none"))
    for s in sorted(dupes):
        for e in dupes[s]:
            lines.append(f"  {s}: {e} (listed "
                         f"{report['surfaces'][s]['members'].count(e)}x)")
    lines.append("")

    unc = report["uncovered"]
    lines.append(f"other pinned paths with no SOURCE_PATTERNS entry ({len(unc)}) "
                 "— these select `core` (the fallback):")
    for rec in unc:
        reg = ", ".join(rec["registered"]) or "none"
        weak = (f" (+{len(rec['weak_files'])} string-only)"
                if rec["weak_files"] else "")
        lines.append(f"  {rec['path']}  (pinned by {len(rec['files'])}{weak}, "
                     f"registered: {reg})")
    return NL.join(lines)


def _ci_manifest_module():
    """The ``tools.ci_manifest`` module, reusing an already-loaded copy.

    ``ci_manifest`` imports this module back for the manifest helpers, so the
    import is lazy and must not create a second copy under a different name
    (which would split module state under pytest). The RUNNING module is probed
    FIRST: ``uv run python tools/ci_manifest.py`` executes that file as ``__main__``,
    which is under neither ``tools.ci_manifest`` nor ``ci_manifest``, so a
    name-only lookup imported a SECOND copy of the same file.
    """
    import sys as _sys
    main = _sys.modules.get("__main__")
    if main is not None and hasattr(main, "check"):
        _file = getattr(main, "__file__", None)
        if _file:
            try:
                if Path(_file).resolve() == (REPO / "tools" / "ci_manifest.py").resolve():
                    return main
            except OSError:  # pragma: no cover - defensive
                pass
    for _name in ("tools.ci_manifest", "ci_manifest"):
        mod = _sys.modules.get(_name)
        if mod is not None and hasattr(mod, "check"):
            return mod
    for path in (str(REPO), str(REPO / "tools")):
        if path not in _sys.path:
            _sys.path.insert(0, path)
    try:
        from tools import ci_manifest as mod
    except ImportError:  # pragma: no cover - module shipped with the repo
        import ci_manifest as mod
    return mod


def _manifest_contract_issues(manifest: dict) -> list[str]:
    """#5050: the measured-durations-map contract, as RED issues only.

    Delegated to ``tools/ci_manifest.py`` so the map's checks live in ONE place
    and cannot be half-wired: this call replaces the three direct
    ``duration_issues``/``leg_coverage_issues``/``duration_coverage_issues``
    calls that used to sit in the ``--integrity`` composition, and adds the
    plausibility/freshness checks the validator contributes (any weight the
    writer could not have rendered — sub-floor, finer precision, or negative —
    a non-empty map whose every weight is the `0.0` sentinel, and a stale
    capture date).

    `ci_manifest.map_issues` also carries main's TWO newer manifest checks —
    `fast_shard_issues` (#6135, the top-level `fast_shards` declaration) and
    `duplicate_entries` (#2913/#5373, the same-surface `merge=union` gate) —
    because they landed beside this change and the union of the two pipelines
    must not drop either. Composing them here keeps the branch's invariant
    (every check reachable from ONE place) while keeping main's coverage.

    An ImportError is reported as an issue, never swallowed: returning ``[]``
    would leave ``--integrity`` GREEN with none of these checks having run —
    the "silent omission with every gate green" class they exist to kill.

    The validator's UNKNOWN verdict is printed as a NOTICE rather than added to
    ``problems`` — for every reason EXCEPT the one observed defect below. The
    states are genuinely different: a RED map is observed to be wrong, while
    UNKNOWN means the map's fidelity could not be observed. The notice keeps
    the genuinely-UNKNOWN cases visible instead of silent and preserves this
    gate's documented polarity (an absent map must not hard-fail a repo that
    never adopted it; pinned by
    ``test_null_or_non_mapping_durations_reports_instead_of_tracebacking``):
    failing on absence would red ``manifest-integrity`` repo-wide until a weekly
    data refresh landed — refusing honest merges for a state no lane owns.

    ABSENCE ONLY is softened, and absence is the KEY'S. A
    ``durations_captured_at`` that is PRESENT but unparseable is a MALFORMED
    manifest value — an OBSERVED defect — and is added to ``problems`` (RED).
    The predicate is structural: ``durations_captured_at not in manifest`` is
    the sole non-gating case; otherwise ``ci_manifest._parse_captured_at``
    decides, and a ``None`` verdict there is RED. So ``""``, an explicit
    ``null``, ``"   "``, ``"not-a-date"``, ``0``, ``[]`` and ``{}`` are ALL
    red, while a MISSING key stays the notice. Softening any present
    unparseable value would invert fail-closed: a stale-but-parseable stamp is
    RED, so degrading that same value to ``'not-a-date'`` would turn exit 1
    into exit 0 — strictly worse data, strictly better verdict — and a
    one-character typo would silently disable the staleness control at this,
    the only CI-invoked, entry point. The strict exit-2 verdict for the
    genuinely-UNKNOWN states lives in ``tools/ci_manifest.py``, which no
    workflow invokes yet: this enforcing entry point exits 0 on an ABSENT
    stamp until #6091 lets a refresh carry a real date. The promotion is
    decided and worded in ONE place — ``ci_manifest.unparseable_stamp_issue`` —
    so this gate and ``ci_timing.integrity_problems`` cannot disagree.
    """
    try:
        module = _ci_manifest_module()
        red, unknown = module.check(manifest)
    except ImportError as exc:  # pragma: no cover - module shipped with the repo
        return [
            "the #5050 durations-map validator (tools/ci_manifest.py) could "
            f"not be imported — the map's value/coverage/partition/staleness "
            f"checks DID NOT RUN: {exc}"
        ]
    # #6243 review cycle 3: the promotion is STRUCTURAL. `unparseable_stamp_issue`
    # decides from the PARSER's verdict plus the KEY's presence — never a
    # hand-rolled value tuple (which exempted `""` and an explicit `null`, so a
    # blanked stamp read as absence and exited 0 while the same stale value
    # exited 1), and never a substring of another module's reason string (which
    # a reword silently disabled). It is computed FIRST, because it also decides
    # which UNKNOWN reasons stay non-gating.
    stamp_issue = module.unparseable_stamp_issue(manifest)
    promoted: set[str] = set()
    if stamp_issue is not None:
        red = [*red, stamp_issue]
        # One condition must not be reported as BOTH "not gating this run" and
        # RED, and an OBSERVED defect must not travel the soft channel.
        # `staleness` owns the stamp's reason, so it names exactly the ones this
        # promotion replaces — by IDENTITY, not by their wording. (An explicit
        # `null` reaches `staleness`'s absent branch, so that reason is
        # suppressed here too; the missing-key case never promotes.)
        promoted = set(module.staleness(manifest)[1])
    for reason in unknown:
        if reason in promoted:
            continue
        print(f"⚠ durations map UNKNOWN (not gating this run): {reason}")
    return red


def _warn_unknown_paths(explain: dict) -> None:
    """Say on STDERR why a fail-closed FULL matrix was chosen (#9109).

    The failure this prevents is a MEASUREMENT one. `--changed-files` takes the
    newline-separated LIST, not a path to it; passing a path made the path
    itself a changed file, which no SOURCE_PATTERN knows, so the selection took
    the deliberate fail-closed FULL-matrix branch (scope v5 decision 1) and
    reported `full=true, surfaces=[all]`. That answer is indistinguishable from
    a legitimate full selection — a push, a schedule, a shared module — so it
    was read as "a docs-only diff selects FULL, not tier1-only" in a live PR's
    CI-registration rationale, and that statement is simply false for the diff
    (the same list on stdin selects `core`).

    WHY A WARNING AND NOT A REFUSAL (the issue's first proposal): the single
    path form is an ALLOWED, DOCUMENTED inline invocation — `config/ci-surfaces.yml`
    documents several of them (`--changed-files tortoise/hosted_api.py`,
    `--changed-files tortoise/__main__.py`, `--changed-files tortoise/audit.py`,
    and the same shape for other modules; grep the file for
    `--changed-files tortoise/`), and
    `docs/plans/2026-09-14-3467-trunk-green-honest-gate.md` records one. A
    refusal keyed on "the argument is an existing file" refuses all of them,
    which is the issue's OWN target 2 ("100% of the documented invocations
    unchanged") failing. The ambiguity between `a one-item list` and `a path to
    a list` has no syntactic answer, so this keeps the argument contract, keeps
    stdout byte-identical, and makes the fail-closed answer SELF-DESCRIBING
    instead. A caller that ignores stderr still gets exactly the selection it
    got before.

    WHY THE LIST-VS-PATH HINT IS CONFINED TO A PATH OUTSIDE THE CHECKOUT: an
    unknown path is mostly a LEGITIMATE diff — most of the tracked tree that
    reaches FULL through this branch alone is real repo content
    (`.github/workflows/ci.yml`, `uv.lock`, `Makefile`, `.mergify.yml`, and a
    `TOOL_CARVEOUTS`-registered `tools/*.py` that no `SOURCE_PATTERNS` row matches,
    such as `tools/ci_selection.py` — an UNREGISTERED `tools/*.py` is dropped by
    `_keep_changed`, and a REGISTERED-AND-claimed one is matched by the surface
    loop, so neither reaches this branch) — and every added or modified path in a
    checked-out PR is an
    existing file. A hint keyed on "this path exists" therefore fires on nearly
    every real fail-closed selection and asserts a malformed invocation that did
    not happen: a new plausible-but-false reading, which is the class this issue
    is about. The hint is confined instead to the shape an operator's list file
    actually takes — an existing file OUTSIDE the checkout (`/tmp/c.txt` in the
    measured case) — and it is attached to the MARKED path on its own line, so a
    mixed list (one repo path + one outside file) cannot be read as "all of
    these are outside". A repo path is a changed path by definition, so the
    generic explanation is all it gets.

    KNOWN RESIDUAL — this branch speaks only for the paths that SURVIVED
    `_keep_changed`. The other resolutions of "I passed a path, not a list" are
    still silent, and the two under-selecting ones are the worse direction: a
    list file under a `NON_PYTHON_PREFIXES` directory (`docs/changed.txt`,
    `tools/changed.txt`, `website/…`) is dropped there, so `changed == []` and
    the docs-only early return answers tier-1 smoke with `full=false`; a list
    file under `tests/` or `config/` takes that prefix's fallback surface. Both
    predate #9109 and are NOT fixed here; they are recorded on #9109 so this
    shape is not read as closed. The remedy is the same for all of them: pass
    the list on stdin.
    """
    reasons = explain.get("reasons") or ([explain["reason"]]
                                        if explain.get("reason") else [])
    if not reasons:
        return
    unknown = explain.get("unknown") or []
    # A cause OTHER than the unknown-path branch also produced this FULL matrix,
    # and it must not be described as a mistake: those paths are FULL because
    # their blast radius IS everything (#9109 review P1). Stated FIRST, so the
    # unknown-path sentence is never borrowed to explain them — and the inverted
    # "NOT a statement that this diff touches everything" is suppressed entirely
    # when such a cause is present, because for these paths the diff DOES touch
    # everything.
    other = [r for r in reasons if r != "unknown-paths"]
    causes = {
        "shared-module": (
            "a SHARED_MODULES path changed — that file is read by the whole "
            "matrix, so the FULL selection is the BLAST RADIUS, not a malformed "
            "argument"),
        "linter-policy": (
            "a linter-POLICY file changed — it changes the lint outcome of every "
            "doc beneath it, so the FULL selection is the BLAST RADIUS, not a "
            "malformed argument"),
    }
    for r in other:
        print(f"ci_selection: FULL matrix because {causes.get(r, r)} (#9109).",
              file=sys.stderr)
    if unknown:
        if other:
            # Two shapes, and the lead-in must not confuse them. When every
            # unrecognised path IS a blast-radius path (pyproject.toml, a root
            # `.markdownlint*`), the list repeats the path line 1 explained, so
            # "additionally" would invite a hunt for a second path that does not
            # exist. When even ONE unrecognised path is something else
            # (`tests/conftest.py` beside `tools/ci_selection.py`), "the same
            # path(s)" would instead DENY a second path that is right there — the
            # mirror of the same error, and the worse direction, because the
            # unrecognised path is the only thing this diagnostic exists to name
            # (#9109 review).
            if explain.get("unknown_all_blast"):
                print(
                    f"ci_selection: the same path(s) are also not matched by any "
                    f"SOURCE_PATTERN ({len(unknown)} changed path(s), which would "
                    "force the FULL selection on their own):", file=sys.stderr)
            else:
                print(
                    f"ci_selection: additionally, {len(unknown)} changed path(s) "
                    "are not matched by any SOURCE_PATTERN:", file=sys.stderr)
        else:
            print(
                f"ci_selection: FULL matrix because {len(unknown)} changed path(s) "
                "are unknown to every SOURCE_PATTERN — fail-closed by design (the "
                "unknown branch returns _full_selection(); see this module's "
                "docstring), NOT a statement that this diff touches everything:",
                file=sys.stderr)
    outside: list[str] = []
    for c in unknown:
        mark = ""
        if _is_outside_repo_file(c):
            outside.append(c)
            # Marked ON the path's own line, never as a blanket claim under the
            # list: a mixed list (one repo path + one outside file) would then
            # assert that the repo path is outside too, which is exactly the
            # plausible-but-false reading this diagnostic exists to prevent.
            mark = ("  [OUTSIDE the checkout — if this is a file CONTAINING the "
                    "list, pass its contents instead]")
        print(f"  - {c}{mark}", file=sys.stderr)
    if outside:
        print(
            "  ^ the path(s) marked above are OUTSIDE the checkout. "
            "`--changed-files` takes the newline-separated LIST, not a path to "
            "it — if one of them is a file CONTAINING the list, pass its "
            "contents instead (`--changed-files -` with the list on stdin) "
            "(#9109).",
            file=sys.stderr)


def _is_outside_repo_file(c: str) -> bool:
    """True when *c* is an existing file OUTSIDE the checkout.

    That is the shape of an operator's list file (`/tmp/c.txt`), and the only
    evidence this tool has that the argument might have been meant as a path to
    a list. A repo-relative path is a changed path whatever else it is, so it is
    deliberately NOT eligible — that discrimination is what keeps the hint off
    the legitimate fail-closed case.

    BOTH operands are absolute before they are compared, and each is anchored to
    where the argument actually means: `c` is a shell argument, so a relative one
    is relative to the process CWD, while `REPO` is derived from `__file__` and
    is therefore checkout-anchored whatever the CWD is. A caller that runs this
    tool from a subdirectory therefore gets `False` (never `True`) for a
    repo-relative name — it fails QUIET, it cannot invent a list-file hint.
    """
    p = Path(c)
    if not p.is_file():
        return False
    # abspath FIRST, and deliberately not on its own: a repo-relative symlink
    # that points OUTSIDE the checkout is still a repo path, and `resolve()`
    # alone would relabel it as an outside file and hint at it (pin exists). But
    # abspath alone is not enough either — an ABSOLUTE argument routed through a
    # symlinked ALIAS of the checkout (`/tmp/linkrepo -> .../checkout`) is never
    # under the resolved REPO string, so the repo's own `uv.lock` was marked
    # "OUTSIDE the checkout" and told to pass its contents instead. Inside is
    # therefore EITHER form: abspath keeps the repo-symlink-to-outside behaviour,
    # realpath closes the alias case (#9109 review P2).
    repo = Path(os.path.abspath(REPO))
    for candidate in (os.path.abspath(p), os.path.realpath(p)):
        try:
            Path(candidate).relative_to(repo)
        except ValueError:
            continue
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--changed-files", default="", help="newline-separated changed files")
    ap.add_argument("--event", default="pull_request", choices=["push", "pull_request", "schedule"])
    ap.add_argument("--integrity", action="store_true", help="verify manifest coverage")
    ap.add_argument("--surface-audit", action="store_true",
                    help="#2938: report manifest/source membership mismatches "
                         "(non-blocking, never changes selection)")
    ap.add_argument("--register", action="store_true",
                    help="auto-register unlisted test files in the manifest (#1429)")
    ap.add_argument("--surface", default="core",
                    choices=["api", "battery", "classify", "core", "ep",
                             "eval", "onboarding", "sdk"],
                    help="surface for --register (default: core — the selection fallback)")
    ap.add_argument("--dry-run", action="store_true", help="preview --register without writing")
    ap.add_argument("--emit-push-matrix", action="store_true",
                    help="#1472/#6135: print the derived push fast_matrix + shards "
                         "(and the derived slow_matrix, #9539). #6897: the fast "
                         f"rows' `runner` follows ${SHARD_ROUTING_ENV} — "
                         f"`{SHARD_ROUTING_POOL_SPLIT}` splits across the x64 "
                         "pool, anything else (the default) is hosted-only")
    ap.add_argument("--split", action="store_true",
                    help="#1473/#6135: LPT-pack the stdin JSON test-file list "
                         "into the configured number of shards")
    ap.add_argument("--non-gating-report", action="store_true",
                    help="#10194: print the non-gating lane and the gating "
                         "shard → file-count map (observability, exit 0)")
    ap.add_argument("--non-gating-subtree-files", action="store_true",
                    help="#10194: print the SUBTREE entries' files as "
                         "pytest-pathable `tests/…` entries, one per line — the "
                         "resolver the manual eval workflow feeds straight to "
                         "pytest")
    args = ap.parse_args()

    manifest = load_manifest()

    if args.integrity:
        missing = integrity(manifest)
        # #3407 review P1: `duration_issues` must run BEFORE `leg_coverage_issues`
        # — the latter calls `push_legs()` -> `split_fast_gate()`, so a malformed
        # durations value used to raise inside the packer before the check that
        # names it had run (fail-closed, but with no diagnosis; `_duration_weight`
        # now coerces as well, so the packer cannot raise at all). That order is
        # preserved inside `ci_manifest.map_issues`, which owns the composition
        # as of #5050: the map's checks (`duration_issues`, `leg_coverage_issues`,
        # `duration_coverage_issues`), main's two newer manifest checks
        # (`fast_shard_issues` for the top-level `fast_shards` declaration and
        # `duplicate_entries` for the same-surface `merge=union` gate — both
        # landed beside this one while it was open, so BOTH were folded into the
        # same one place rather than being re-added here), plus the
        # plausibility/freshness checks the validator adds (any weight the writer
        # could not have rendered — sub-floor, finer precision, or negative — a
        # non-empty map whose every weight is the `0.0` sentinel, and a stale
        # capture date) are called from ONE place, so they cannot be half-wired
        # into one entry point and missing from the other.
        #
        # `watchdog_headroom_issues` is the EXCEPTION, and deliberately composed
        # here instead: `ci_manifest` does not own it (main added it after this
        # validator was cut, and `ci_manifest.map_issues` composes only the five
        # above). It is re-added at this call site so the union of the two
        # pipelines drops neither — the same rule the two folded-in checks
        # followed. Both entry points compose it, so it stays single-sourced.
        problems = missing + slow_file_issues(manifest) \
            + _manifest_contract_issues(manifest) \
            + carve_shard_issues(manifest) \
            + slow_shard_issues(manifest) \
            + watchdog_headroom_issues(manifest)
        # #1472: the matrix must come from the selector derivation — when it
        # does, the #1266 halves-parse tie check is
        # subsumed (the derivation guarantees no slow leaks / dupes / dead
        # files by construction). Legacy hardcoded halves keep the #1266 check.
        wf_issues = workflow_matrix_issues(
            REPO / ".github" / "workflows" / "python-ci.yml", manifest)
        problems += wf_issues
        if not wf_issues:
            # derived matrix: feed the derived shards so the #1266 tie checks
            # still run against the ACTUAL execution split (all N of them).
            legs = push_legs(manifest)
            halves = {s["name"]: set(s["files"]) for s in legs["shards"]}
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
            print(f"⚠️  {len(absent)} manifest fast files are in NO shard "
                  f"(full-matrix coverage hole, #1266): {sample} …")
        print("✅ integrity: all test files classified; slow_files consistent; shards consistent")
        return 0

    if args.surface_audit:
        # #2938: report-only. Always exits 0 — a mismatch is a curation
        # decision, never a gate; `--integrity` keeps its own exit code.
        print(render_surface_audit(surface_audit(manifest)))
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

    if args.non_gating_report:
        print(non_gating_report(manifest))
        return 0

    if args.non_gating_subtree_files:
        # #10194: the SUBTREE entries only — the literal entries have their own
        # runners (evals-on-demand / perf-lane) and their own environments, so a
        # workflow that ran the whole lane would run files it does not own, in a
        # shape it was not built for (the embedded redislite lane vs the docker
        # one). Printed empty rather than failing here: the caller fails closed
        # on an empty list, which is the state that must never read as a green
        # "ran nothing" (same rule as evals-on-demand's step).
        #
        # ⛔ The output is `tests/<relative>`, NOT `on_demand_subtree_files()`'s
        # raw tests/-relative form. The caller feeds these straight to pytest
        # from the repo root, where a bare `eval/ask/test_ask_corpus.py` does not
        # exist — pytest exits 4 with "file or directory not found" and the lane
        # runs ZERO files while its own guard reports green. Verified live: the
        # first cut of this verb printed the bare form and every dispatch of
        # `eval-suite-manual.yml` would have reddened on path validation.
        # `run_affected.sh` prints the same pytest-pathable shape for the same
        # reason; the accessor stays tests/-relative because every other
        # consumer (the leg producers, the guards) indexes the manifest that way.
        for f in sorted(on_demand_subtree_files(manifest)):
            print(f if f.startswith("tests/") else f"tests/{f}")
        return 0

    if args.emit_push_matrix:
        legs = push_legs(manifest)
        print(json.dumps({
            "fast_matrix": fast_matrix_include(legs["shards"]),
            "shards": legs["shards"],
            "carve_out": legs["carve_out"],
            "carve_matrix": carve_matrix_include(legs["carve_shards"]),
            # #9539: the slow lane's matrix, the same derivation shape — the
            # `changes` job publishes it and `test-slow` expands it.
            "slow_matrix": slow_matrix_include(legs["slow_shards"]),
        }))
        return 0

    if args.split:
        # #1492: an empty stdin (the split step's output expression resolving
        # to "") must degrade to an empty selection — NOT crash --split for
        # every tier-2 PR (json.loads('') raises).
        raw = sys.stdin.read().strip()
        files = json.loads(raw) if raw else []
        shards = build_shard_entries(files, _durations_map(manifest),
                                     fast_shard_count(manifest))
        result = {"fast_matrix": fast_matrix_include(shards),
                  "shards": shards}
        out_dir = Path(os.environ.get("CI_SELECTION_ARTIFACT_DIR", REPO / ".ci-selection"))
        out_dir.mkdir(exist_ok=True)
        (out_dir / "split.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        return 0

    changed = [l.strip() for l in args.changed_files.splitlines() if l.strip()]  # noqa: E741
    if args.changed_files == "-":
        changed = [l.strip() for l in sys.stdin.read().splitlines() if l.strip()]  # noqa: E741
    explain: dict = {}
    result = select(changed, args.event, manifest, explain=explain)
    _warn_unknown_paths(explain)

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
