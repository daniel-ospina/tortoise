"""Unit tests for the tiered test selector (#1021).

Covers the fail-closed selection rules: docs-only → tier 1; surface changes →
tier 1 ∪ surface; shared modules → full; unknown paths → full; test-file
changes select their owning surface; push/schedule → full; manifest integrity
(recursive rglob since #1349 — subdir test files, tests/e2e/ exempt; tool-path
carve-out so tools/longmem_eval/ etc. select the eval surface).
"""
from __future__ import annotations

import ast
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ci_selection import (  # noqa: I001
    SOURCE_PATTERNS, SHARED_MODULES, load_manifest, select, integrity, slow_file_issues,
    unlisted_tests, register_tests, register, classify_test_file,  # noqa: F401
    surface_audit, render_surface_audit, duplicate_entries,
)


def _sel(changed, event="pull_request"):
    return select(changed, event, load_manifest())


def _tier1() -> set:
    return set(load_manifest()["tier1"])


def test_docs_only_runs_tier1():
    # A markdown-only docs change stays the pure tier-1 smoke path.
    r = _sel(["docs/README.md"])
    assert r["full"] is False
    assert r["surfaces"] == []
    assert set(r["test_files"]) == _tier1()


def test_docs_scanning_gates_stay_in_tier1():
    # #4309: a docs-only PR runs *only* tier1.
    #
    # A gate whose whole job is to stop a claim being re-scattered across the
    # doc tree is silent on the exact change it exists to catch unless it is
    # registered in `tier1` — every other surface needs a Python path to be
    # selected. #4283 registered the durability gate in `tier1` + `core` for
    # exactly this reason; the older #4179 retention gate stayed out of `tier1`
    # and so ran no part of its scan on a docs-only edit (e.g. to
    # docs/retention-and-deletion.md), letting a new unlinked retention claim
    # merge green. Pin both: dropping either from `tier1` re-opens that hole.
    tier1 = _tier1()
    assert "test_retention_promise.py" in tier1, (
        "#4179 retention/deletion anti-scatter gate must stay in tier1 — a "
        "docs-only PR runs only tier1, so without it a new unlinked retention "
        "claim in docs/ merges green (#4309)"
    )
    assert "test_durability_posture.py" in tier1, (
        "#2881 durability gate must stay in tier1 — same docs-only silent-drop "
        "class (#4309)"
    )


def test_public_site_surface_change_selects_onboarding_and_skips_slow():
    """#3332: a public *site surface* change selects `onboarding`.

    Each path listed in SOURCE_PATTERNS is a file some guard test in the
    onboarding surface reads — most importantly test_website_docs_consistency.py
    (checks each public page against its canonical source: POINT_STATUS_VALUES in
    tortoise/sdk.py, the EP mechanism, the ontology link) and test_website_static.py
    (pins product.html's pricing surface). Before #3332 every `website/` path was
    filtered out by NON_PYTHON_PREFIXES *before* SOURCE_PATTERNS was consulted, so
    changed == [] -> tier-1 smoke: those guard tests never ran for the files
    they guard.

    The #2147/#2148 cost gates are unchanged — only the fast surface tests are
    added; no slow leg, no carve-out leg.
    """
    for changed in (["website/docs.html"], ["website/faq.html"],
                    ["website/apps/dashboard/public/welcome.html"],
                    ["website/self-hosted.html"],
                    ["website/product.html"], ["website/index.html"],
                    ["website/apps/dashboard/public/signup.html"],
                    ["website/privacy.html"],
                    ["docs/README.md", "website/self-hosted.html"]):
        r = _sel(changed)
        assert r["surfaces"] == ["onboarding"], changed
        assert r["full"] is False
        assert r["slow_run"] is False
        assert r["carve_out_run"] is False
        assert r["slow_selected"] == []
        assert "test_website_docs_consistency.py" in r["test_files"], changed


def test_the_onboarding_copy_gate_is_wired_not_left_to_the_tier1_fallback():
    """#3673 indicator (2): the parity gate runs on every PR touching either copy.

    The gate is `test_onboarding_variants.py::test_m8_deploy_mirror_matches_canonical`.
    Before this entry existed, `website/apps/dashboard/public/skills/` matched NO
    SOURCE_PATTERNS entry, so an edit to the SERVED copy alone produced
    `surfaces=[]` and the parity test ran only through the tier-1 fallback —
    coverage that held by accident and that would vanish the moment the file left
    `tier1`. For the installer the consequence was worse: its guard,
    `test_installer_preserves_foreign_skill_content.py`, is on `core` and NOT in
    `tier1`, so an installer-only PR ran no guard for the installer at all — the
    #1349/#3332/#3616 silent-drop class this file exists to prevent.

    Asserted on the SURFACE, not merely on the test-file list: the tier-1
    fallback also puts `test_onboarding_variants.py` in `test_files`, so a
    test-file-only assertion passes with the wiring absent — a gate that can only
    ever pass. Watched RED before the SOURCE_PATTERNS entries existed, GREEN
    after, which is the only evidence that distinguishes the two.
    """
    cases = (
        # the two tracked copies whose byte-identity IS the parity contract
        ("tortoise/onboarding/SKILL.md", "test_onboarding_variants.py"),
        ("website/apps/dashboard/public/skills/tortoise-onboarding/SKILL.md",
         "test_onboarding_variants.py"),
        # a served sibling — the same directory, the same gate
        ("website/apps/dashboard/public/skills/how-to-use-tortoise/SKILL.md",
         "test_onboarding_variants.py"),
        # the installer whose SKILLS=(...) the dashboard's claim is pinned against
        ("website/apps/dashboard/public/install-tortoise-skills.sh",
         "test_installer_preserves_foreign_skill_content.py"),
    )
    root = Path(__file__).resolve().parents[1]
    for changed, guard in cases:
        assert (root / changed).exists(), f"guarded path is gone: {changed}"
        r = _sel([changed])
        assert r["surfaces"] == ["onboarding"], (
            f"{changed} selects {r['surfaces']} — its guard runs only via the "
            f"tier-1 fallback, which is not a wiring")
        assert guard in r["test_files"], f"{changed} does not select {guard}"


def test_every_source_pattern_is_selectable():
    """The ratchet: every SOURCE_PATTERNS entry must reach `select()`.

    This is the invariant whose absence let the same bug ship twice — #1349 for
    `tools/` (patched with the TOOL_CARVEOUTS mirror) and #3332 for `website/`
    (where the SOURCE_PATTERNS entries for welcome.html and self-hosted.html were
    dead on arrival, and the hand-maintained mirror that replaced them caught only
    4 of the 10 guarded paths). A pattern that a NON_PYTHON_PREFIXES prefix would
    filter out must be re-included by `select()`'s `_selection_relevant` rule.
    Derived from SOURCE_PATTERNS rather than enumerated, so a future entry cannot
    be added and silently not run — prior review (#2994) rejected an enumerated
    guard for exactly this reason.

    Direction: entry -> runs. This does NOT catch the reverse (a guarded page with
    no SOURCE_PATTERNS entry at all) — that direction is tracked separately.
    """
    assert "onboarding" in load_manifest()["surfaces"]
    dead = []
    for surface, pats in SOURCE_PATTERNS.items():
        if surface == "core":
            continue
        for pat in pats:
            if not _sel([pat])["surfaces"]:
                dead.append((surface, pat))
    assert not dead, (
        f"SOURCE_PATTERNS entries that select NO surface (dead on arrival — the "
        f"path is filtered by NON_PYTHON_PREFIXES before SOURCE_PATTERNS is "
        f"consulted, so no guard test for that file ever runs): {dead}"
    )


def _tracked_files(root: Path) -> list[str]:
    """The TRACKED file set — what `select()` reasons about, and what CI sees.

    `git ls-files`, deliberately not `os.walk`. A walk is both slower and WRONG:
    it counts untracked local build artifacts, and this repo carries ~148 sibling
    checkouts under `.worktrees/` (measured: ~720k files / ~23s walked, vs ~2.4k
    files / ~0.9s tracked). A dead entry could then look alive locally while
    failing in CI — a false negative in exactly the environment a developer runs
    in. `node_modules` is NOT excluded either: parts of it are tracked here, so
    excluding it would diverge from git in the other direction.
    """
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            check=False,
            capture_output=True,
        )
    except FileNotFoundError as exc:  # no git binary at all
        raise AssertionError(
            "git is not on PATH, so the tracked set is unknown — this test "
            f"cannot decide liveness: {exc}"
        ) from exc
    # check=False + an explicit assert, rather than check=True: check=True would
    # raise a bare CalledProcessError with no context, so an sdist export with no
    # `.git`, or `fatal: detected dubious ownership`, would look like every entry
    # being dead rather than like a broken environment.
    assert proc.returncode == 0, (
        "git ls-files failed, so the tracked set is unknown — this test cannot "
        "decide liveness and must not report every entry as dead: "
        + proc.stderr.decode("utf-8", "replace").strip()
    )
    return [p for p in proc.stdout.decode("utf-8").split("\0") if p]


def test_source_patterns_all_name_something_real():
    """Every SOURCE_PATTERNS entry must name a file (or directory) that EXISTS.

    Why this exists — #4171. The admin-origin move DELETED
    `website/functions/admin/[[path]].ts`, and its SOURCE_PATTERNS entry stayed.
    That is not cosmetic staleness: entries are matched with `startswith`, never
    against the filesystem, so a deleted path keeps "selecting" its surface for a
    file nobody can edit. The PR that moves a guarded file to a new path
    therefore selects NO surface for the new location, and the guard written for
    that exact file silently stops running — the #1349/#3332 silent-drop class,
    reached through a door the existing ratchet does not cover.

    `test_every_source_pattern_is_selectable` cannot see this failure: a dead
    path still matches its OWN pattern, so it "runs" its surface fine — there is
    simply nothing left that can edit it. This is a third FORWARD check
    (entry -> exists), NOT the reverse direction (guarded path -> has an entry),
    which is still hand-pinned per guard wherever an author remembered to (see
    `test_website_docs_consistency.py::test_every_guard_input_is_selectable_by_ci`).
    The reverse direction remains the open half of this class.

    Existence is checked against the tracked set, in the two shapes
    SOURCE_PATTERNS actually uses. No glob branch: no entry is a glob, and
    `select()` itself has no glob support, so a glob-shaped entry satisfied here
    would still select nothing — the test would bless a dead entry.
    """
    root = Path(__file__).resolve().parents[1]
    tracked = set(_tracked_files(root))

    def names_something_real(pattern: str) -> bool:
        # A real tracked path is always live.
        if pattern in tracked:
            return True
        # Otherwise it must name a SUBTREE, anchored on the separator. `select()`
        # matches with `startswith`, so a directory entry is live with OR without
        # its trailing slash (`tortoise/onboarding` and `tortoise/onboarding/`
        # both match). Reporting the slash-less form as "names NOTHING" would be
        # false — it IS live — and would disagree with
        # `test_every_source_pattern_is_selectable`, which accepts it.
        return any(f.startswith(pattern.rstrip("/") + "/") for f in tracked)

    dead = sorted(
        f"[{surface}] {pat}"
        for surface, pats in SOURCE_PATTERNS.items()
        for pat in pats
        if not names_something_real(pat)
    )

    assert not dead, (
        "SOURCE_PATTERNS entries naming NOTHING tracked — a deleted or moved "
        "guarded path keeps its entry, so a change to the file's NEW location "
        "selects no surface and its guard silently stops running (#4171). "
        "Repoint the entry at the new path, or delete it:\n  "
        + "\n  ".join(dead)
    )


def test_unrelated_website_change_stays_tier1():
    """SITE_CARVEOUTS is not a wholesale `website/` removal.

    A website path that owns no guard test keeps the old docs-only behavior
    (empty changed -> tier-1 smoke), so unrelated website edits do not drag the
    onboarding surface in.
    """
    r = _sel(["website/robots.txt"])
    assert r["surfaces"] == []
    assert r["full"] is False
    assert set(r["test_files"]) == _tier1()


def test_onboarding_change_selects_onboarding():
    r = _sel(["tortoise/onboarding/SKILL.md"])
    assert r["full"] is False
    assert "onboarding" in r["surfaces"]
    assert set(r["test_files"]) == ((_tier1() | set(load_manifest()["surfaces"]["onboarding"]))
                                     - set(load_manifest().get("carve_out", [])))


def test_ep_change_selects_ep():
    r = _sel(["tortoise/ranking.py"])
    assert r["full"] is False
    assert r["surfaces"] == ["ep"]
    assert "test_decide.py" in r["test_files"]


def test_shared_module_goes_full():
    r = _sel(["tortoise/sdk.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    r2 = _sel(["tests/conftest.py"])
    assert r2["full"] is True


def test_every_conftest_module_level_tests_import_is_shared():
    """#4069: a `tests/` helper conftest imports at MODULE level is suite-wide.

    `tests/conftest.py` re-exports suite-wide fixtures, so a `tests/` helper it imports at
    module level runs for EVERY surface's tests; the manifest never classifies it (it is
    not a `test_*.py` file), so unless it is in `SHARED_MODULES` a change to it selects
    `core` only and an api/onboarding/battery break it induces never runs on the PR that
    made it (the #1349/#3332/#3910 silent-under-selection class).

    Scope is deliberately `tests.*` only: this criterion justifies a *test helper* being
    suite-wide, not a product module (whose classification is its own path pattern plus
    the cross-cutting judgment list `SHARED_MODULES` carries). The product modules conftest
    imports at module level are therefore NOT covered here; that residual is measured on
    #4486, not asserted away.

    The import set is read from the AST and the walk is GENERIC: it recurses into every
    nested statement container (class bodies, `match` cases, `except*` blocks, with/for
    bodies, ...) and stops only at function-like nodes, whose bodies are not module level.
    Enumerating the containers to descend into is what let an earlier version of this ratchet
    be narrower than the rule it documents, so there is no such list here. Relative imports
    (`from . import _x`) are deliberately unmatched: `tests/` has no `__init__.py`, so they
    cannot appear at conftest module level today, and this test states that rather than
    pretending they are covered.
    """
    conftest_path = Path(__file__).resolve().parent / "conftest.py"
    module = ast.parse(conftest_path.read_text())
    imported: set[str] = set()

    def walk(node) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # a function-local import is not module level
            if isinstance(child, ast.Import):
                for alias in child.names:
                    if alias.name == "tests" or alias.name.startswith("tests."):
                        imported.add(alias.name)
                continue
            if isinstance(child, ast.ImportFrom):
                if child.level == 0 and child.module == "tests":
                    for alias in child.names:
                        imported.add(f"tests.{alias.name}")
                elif child.level == 0 and child.module and child.module.startswith("tests."):
                    imported.add(child.module)
                continue
            walk(child)

    walk(module)
    assert imported, "expected tests/conftest.py to import a tests.* module at module level"
    for module in sorted(imported):
        rel = module.replace(".", "/") + ".py"
        assert rel in SHARED_MODULES, (
            f"{rel} is imported at conftest MODULE level (so it runs for every "
            f"surface's tests) but is not in SHARED_MODULES — a change to it would "
            f"select core only")
        result = _sel([rel])
        assert result["full"] is True, result
        assert result["test_files"] == "ALL", result


def test_every_shared_module_entry_selects_the_full_matrix():
    """#4097: `SHARED_MODULES` is a hand-maintained list, so derive its invariant here.

    `test_shared_module_goes_full` pins two literal examples; a future entry that is
    added (or a cross-cutting leaf like `tortoise/env_truthy.py` that is REMOVED) would
    otherwise silently downgrade to `core`-only and stop running the consumer suites.
    """
    py_modules = [m for m in SHARED_MODULES if m.endswith(".py")]
    assert py_modules, "SHARED_MODULES should list python modules"
    for module in py_modules:
        result = _sel([module])
        assert result["full"] is True, f"{module} is in SHARED_MODULES but selects {result}"
        assert result["test_files"] == "ALL", module


def test_unknown_path_goes_full():
    # fail-closed: a path outside any known subtree must never silently
    # under-select (new top-level dir/subsystem → full matrix)
    r = _sel(["mystery-dir/x.py"])
    assert r["full"] is True


def test_new_engine_module_maps_to_core():
    # a new file under tortoise/ is engine code → core surface (conservative,
    # 108 files — not silent under-selection)
    r = _sel(["tortoise/brand_new_module.py"])
    assert r["full"] is False
    assert "core" in r["surfaces"]


def test_test_file_change_selects_owning_surface():
    r = _sel(["tests/test_decide.py"])
    assert "ep" in r["surfaces"]
    r2 = _sel(["tests/test_harness_mcp_config.py"])
    assert "onboarding" in r2["surfaces"]


def test_pi_hooks_change_selects_the_capture_guard():
    """#3575 P1-B: `tortoise/pi-hooks/` matches no SOURCE_PATTERNS entry, so a
    change to the extension selects `core` via the `tortoise/` fallback. The
    guard that pins the extension must be IN that selection — registered only
    under `onboarding`, a PR fixing the extension ran neither the Python guard
    nor the extension's `node --test` suite. `--integrity` cannot see this
    (the file is classified); only a selection assertion can."""
    r = _sel(["tortoise/pi-hooks/tortoise-capture.ts"])
    assert r["full"] is False
    assert "core" in r["surfaces"]
    assert "test_pi_capture_hooks.py" in r["test_files"]


def test_session_import_change_selects_the_window_guard():
    """#3575 P1-A: `tortoise/session_import/` maps to no named surface, so a
    parsers.py change selects `core` via the fallback. The window guard must be
    in that selection — registered only under `api`, it did not run for the
    change it guards."""
    r = _sel(["tortoise/session_import/parsers.py"])
    assert r["full"] is False
    assert "core" in r["surfaces"]
    assert "test_session_import_codex.py" in r["test_files"]


def test_two_surfaces_union():
    r = _sel(["tortoise/ranking.py", "tortoise/onboarding/SKILL.md"])
    assert r["full"] is False
    assert set(r["surfaces"]) == {"ep", "onboarding"}


def test_push_and_schedule_are_full():
    assert _sel([], "push")["full"] is True
    assert _sel([], "schedule")["full"] is True


def test_integrity_covers_all_test_files():
    # every tests/**/test_*.py (except tests/e2e/) must be classified — the
    # drift trap (scope v5 dec 2/5; rglob since #1349)
    assert integrity(load_manifest()) == []


def test_integrity_rglob_flags_unregistered_subdir(monkeypatch, tmp_path):
    # a subdir test file absent from the manifest is drift-flagged — the
    # non-recursive glob used to miss subdir files entirely (#1349)
    import tools.ci_selection as cs
    (tmp_path / "test_top.py").write_text("")
    (tmp_path / "bench").mkdir()
    (tmp_path / "bench" / "test_bench_x.py").write_text("")
    monkeypatch.setattr(cs, "TESTS_DIR", tmp_path)
    manifest = load_manifest()
    manifest["surfaces"]["core"].append("test_top.py")
    missing = cs.integrity(manifest)
    assert "bench/test_bench_x.py" in missing
    assert "test_top.py" not in missing


def test_integrity_rglob_registered_subdir_clean(monkeypatch, tmp_path):
    # registering the subdir file (relative-path key) clears the flag
    import tools.ci_selection as cs
    (tmp_path / "test_top.py").write_text("")
    (tmp_path / "bench").mkdir()
    (tmp_path / "bench" / "test_bench_x.py").write_text("")
    monkeypatch.setattr(cs, "TESTS_DIR", tmp_path)
    manifest = load_manifest()
    manifest["surfaces"]["core"].append("test_top.py")
    manifest["surfaces"]["eval"].append("bench/test_bench_x.py")
    assert cs.integrity(manifest) == []


def test_integrity_rglob_skips_e2e(monkeypatch, tmp_path):
    # tests/e2e/ (4 direct + 13 hosted) is deliberately NOT registered —
    # covered by welcome-e2e-monitor + legal-e2e + ENV_BROKEN_FILES
    import tools.ci_selection as cs
    (tmp_path / "e2e").mkdir()
    (tmp_path / "e2e" / "test_browser.py").write_text("")
    (tmp_path / "e2e" / "hosted").mkdir()
    (tmp_path / "e2e" / "hosted" / "test_01_signup.py").write_text("")
    monkeypatch.setattr(cs, "TESTS_DIR", tmp_path)
    assert cs.integrity(load_manifest()) == []


def test_classify_relative_path_and_basename_backcompat():
    m = load_manifest()
    # relative-path form (subdir files, #1349)
    assert classify_test_file("eval/retrieval/test_gate_1349.py", m) == "eval"
    assert classify_test_file("longmem_eval/test_vector_arm.py", m) == "eval"
    assert classify_test_file("bench/test_bench_core.py", m) == "eval"
    # basename backward-compat (legacy top-level keys)
    assert classify_test_file("test_decide.py", m) == "ep"
    assert classify_test_file("test_embedder_probe.py", m) == "eval"
    # unknown files classify to nothing
    assert classify_test_file("nope.py", m) is None
    assert classify_test_file("subdir/nope.py", m) is None


def test_tools_longmem_change_selects_eval_not_tier1():
    # tools/longmem_eval/ is carved out of NON_PYTHON_PREFIXES — a harness
    # change selects the eval surface instead of dropping to tier-1 smoke
    r = _sel(["tools/longmem_eval/run.py"])
    assert r["full"] is False
    assert r["surfaces"] == ["eval"]
    assert "eval/retrieval/test_run.py" in r["test_files"]
    assert set(r["test_files"]) != _tier1()


def test_unrelated_tools_change_still_tier1():
    # tools/kappa.py is NOT in TOOL_CARVEOUTS — it keeps the old behavior
    # (filtered as non-python-relevant → tier-1 smoke; never eval/full)
    r = _sel(["tools/kappa.py"])
    assert r["full"] is False
    assert r["surfaces"] == []
    assert set(r["test_files"]) == _tier1()


def test_activation_cohort_change_does_not_drop_to_tier1():
    # #B7 (#3674): tools/activation_cohort.py owns part of
    # tests/test_activation_scorecard.py (its roll_up cohort-summing logic).
    # Without the TOOL_CARVEOUTS entry the flat "tools/" prefix swallows it ->
    # tier-1 smoke only, and the suite that pins the cohort number never runs.
    r = _sel(["tools/activation_cohort.py"])
    assert r["surfaces"], r
    assert set(r["test_files"]) != _tier1(), r
    # Today it lands in the fail-closed unknown-path branch (FULL matrix — the
    # heaviest but safest gate for a file a reported metric depends on). If
    # that ever becomes a mapped surface, the owning suite must still run.
    if not r["full"]:
        assert "test_activation_scorecard.py" in r["test_files"], r


def test_ask_spotcheck_tools_change_selects_sdk_not_tier1():
    # #2071: tools/ask_spotcheck*.py are carved out of NON_PYTHON_PREFIXES
    # and mapped to the sdk surface — a spot-check-only change selects the
    # ask-lane tests (test_ask_spotcheck_judge.py) instead of tier-1 smoke
    for changed in ("tools/ask_spotcheck.py",
                    "tools/ask_spotcheck_consistency.py",
                    "tools/ask_spotcheck_probe.py"):
        r = _sel([changed])
        assert r["full"] is False, changed
        assert "sdk" in r["surfaces"], changed
        assert "test_ask_spotcheck_judge.py" in r["test_files"], changed
        assert set(r["test_files"]) != _tier1()
    # a test-file change selects its owning surface too
    r = _sel(["tests/test_ask_spotcheck_judge.py"])
    assert "sdk" in r["surfaces"]


def test_ask_recall_bench_change_selects_sdk_not_tier1():
    # #3910: tools/ask_recall_bench.py owns the `_retrieve_pipeline` mirror in
    # tests/test_ask_retrieval_levers.py. Before its SOURCE_PATTERNS entry the
    # flat "tools/" prefix swallowed the path, so `changed` came back empty and
    # select() took the docs-only return — surfaces=[], tier-1 smoke only — and
    # the guard test for that exact file never ran on the PR that changed it
    # (the #1349/#3332 shape the ratchet exists for).
    r = _sel(["tools/ask_recall_bench.py"])
    assert r["full"] is False
    assert "sdk" in r["surfaces"], r
    assert "test_ask_retrieval_levers.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1()


def test_gen_ask_transcripts_change_selects_sdk_not_tier1():
    # #3914: tools/gen_ask_transcripts.py owns the capture-shaped seeder the
    # committed transcript goldens are generated from, and its shape is pinned
    # by tests/test_ask_seed_shape.py. Before its SOURCE_PATTERNS entry the
    # flat "tools/" prefix swallowed the path, so a seeder-only change came
    # back with surfaces=[] and select() took the docs-only return — tier-1
    # smoke only — leaving BOTH guards unrun on the PR that changed the
    # seeder (the #1349/#3332/#3910 class the ratchet exists for).
    r = _sel(["tools/gen_ask_transcripts.py"])
    assert r["full"] is False, r
    assert "sdk" in r["surfaces"], r
    assert "test_ask_seed_shape.py" in r["test_files"], r
    assert "test_ask_regression_llm.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1()


def test_tmpdir_sweep_tool_change_selects_core_not_tier1():
    # #4069: tools/tmpdir_sweep.py owns tests/test_tmpdir_sweep.py and
    # tests/test_tmpdir_hygiene.py. The mechanism is the CORE_ALSO entry:
    # `_selection_relevant()` consults it, so the `tools/` path survives the
    # flat NON_PYTHON_PREFIXES filter, and the match loop then adds `core` and
    # marks the path found — so a tool-only change selects `core` instead of
    # tier-1 smoke or the unknown-path full matrix. Mutation check: removing
    # the CORE_ALSO entry filters the path out (docs-only early return → empty
    # surfaces, tier-1 smoke), which fails asserts 2–5 below.
    r = _sel(["tools/tmpdir_sweep.py"])
    assert r["full"] is False, r
    assert "core" in r["surfaces"], r
    assert "test_tmpdir_sweep.py" in r["test_files"], r
    assert "test_tmpdir_hygiene.py" in r["test_files"], r
    assert set(r["test_files"]) != _tier1()


def test_collision_preflight_tool_change_fails_closed_to_full():
    # #3261: tools/collision_preflight.py owns tests/test_collision_preflight.py.
    # Before its TOOL_CARVEOUTS entry the flat "tools/" prefix swallowed the
    # path: `changed` came back empty, so select() took the docs-only return
    # (surfaces=[], tier-1 smoke only) and the tool's own guard test never ran
    # on the PR that changed the tool. The early docs-only return bypasses the
    # `if not matched: matched.add("core")` fallback, so the pre-#3261 note
    # claiming such a change "falls back to core" was never true.
    # No SOURCE_PATTERNS entry matches the path, so it takes the unknown-path
    # branch -> full matrix (fail closed), exactly like tools/ci_selection.py.
    r = _sel(["tools/collision_preflight.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert "core" in r["surfaces"]


def test_run_with_eval_keys_tool_change_fails_closed_to_full():
    # #2718/#4860: tools/run-with-eval-keys.sh owns
    # tests/test_run_with_eval_keys.py. Same silent-drop class as the
    # collision-preflight carve-out above: the flat "tools/"
    # NON_PYTHON_PREFIXES entry swallows a `.sh` path, so without a
    # TOOL_CARVEOUTS entry `changed` is empty and select() takes the docs-only
    # return (surfaces=[], tier-1 smoke only) — a wrapper-only change (a new
    # managed key, a fingerprint-format edit) would ship without its guard
    # suite ever running. No SOURCE_PATTERNS entry matches a `.sh` path, so it
    # lands in the unknown-path fail-closed branch -> FULL matrix + both legs.
    r = _sel(["tools/run-with-eval-keys.sh"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert "core" in r["surfaces"]


def test_finding_provenance_tool_change_fails_closed_to_full():
    # #4290: tools/finding_provenance.py owns tests/test_finding_provenance.py.
    # Same silent-drop class as the collision-preflight carve-out above — the
    # flat "tools/" NON_PYTHON_PREFIXES entry swallows a tool-only change, so
    # without a TOOL_CARVEOUTS entry `changed` is empty and the docs-only
    # return runs tier-1 smoke only: the gate's own falsification suite would
    # never run on the PR that changes the gate. No SOURCE_PATTERNS entry
    # matches, so it lands in the unknown-path fail-closed branch -> FULL.
    r = _sel(["tools/finding_provenance.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert "core" in r["surfaces"]


def test_embedded_evidence_tool_change_fails_closed_to_full():
    # #3827: tools/embedded_evidence.py owns tests/test_embedded_evidence.py.
    # Same silent-drop class as the preflight carve-out above: the flat "tools/"
    # prefix swallowed the path, `changed` came back empty, and select() took the
    # docs-only return (surfaces=[], tier-1 smoke only) — so the harness's own
    # guard test never ran on the PR that changed the harness. That is precisely
    # the "proxy silent in the case it exists to cover" class the harness is
    # written to detect, and it applied to the harness itself.
    # No SOURCE_PATTERNS entry matches the path, so it takes the unknown-path
    # branch -> full matrix (fail closed), like tools/collision_preflight.py.
    r = _sel(["tools/embedded_evidence.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert "core" in r["surfaces"]


def test_backfill_script_only_change_selects_eval():
    # graph-scripts/backfill_embeddings.py is a SOURCE_PATTERNS["eval"]
    # path — a backfill-only PR selects the eval surface (its test,
    # test_backfill_embeddings_force.py, is registered there by T12/PR2)
    r = _sel(["graph-scripts/backfill_embeddings.py"])
    assert r["full"] is False
    assert "eval" in r["surfaces"]
    assert "test_pair_label_runner.py" in r["test_files"]
def test_slow_files_never_in_fast_gate_selections():
    # #1371: tier-1 and tier-2 selections must never contain a slow file
    # (they run only in the test-slow job).
    m = load_manifest()
    slow = set(m["slow_files"])
    assert slow, "slow_files must be non-empty"
    assert not (set(m["tier1"]) & slow), "tier1 leaks a slow file"

    docs = _sel(["docs/README.md", "website/apps/dashboard/public/welcome.html"])
    assert not (set(docs["test_files"]) & slow), "docs-only tier-1 leaks slow files"

    core = _sel(["tortoise/graph.py", "tortoise/ingest.py"])
    assert not (set(core["test_files"]) & slow), "tier-2 core leaks slow files"

    ep = _sel(["tortoise/ranking.py", "tortoise/analyze.py"])
    assert not (set(ep["test_files"]) & slow), "tier-2 ep leaks slow files"


def test_slow_files_emitted_on_every_return_path():
    # #1371: the changes job reads slow_files from every selection mode — a
    # missing key would KeyError the nightly/schedule run or empty test-slow.
    m = load_manifest()
    expected = set(m["slow_files"])
    for changed, event in [
        ([], "push"),
        ([], "schedule"),
        (["docs/README.md"], "pull_request"),
        (["tortoise/sdk.py"], "pull_request"),
        (["mystery-dir/x.py"], "pull_request"),
        (["tortoise/ranking.py"], "pull_request"),
    ]:
        r = select(changed, event, m)
        assert "slow_files" in r, f"missing slow_files for {changed}/{event}"
        assert set(r["slow_files"]) == expected, f"bad slow_files for {changed}/{event}"


def test_diff_gate_keys_emitted_on_every_return_path():
    """#2147/#2148 (2026-09-02-ci-audit F1/F2): every selection mode carries
    slow_run / slow_selected / carve_out_run — the changes job echoes them,
    so a missing key would KeyError the echo (or the nightly run) and
    silently mis-gate a leg. slow_selected must always be a subset of
    slow_files - carve_out (test-slow's committed leg set)."""
    m = load_manifest()
    leg_set = set(m["slow_files"]) - set(m["carve_out"])
    for changed, event in [
        ([], "push"),
        ([], "schedule"),
        (["docs/README.md"], "pull_request"),
        (["tortoise/sdk.py"], "pull_request"),
        (["mystery-dir/x.py"], "pull_request"),
        (["tortoise/ranking.py"], "pull_request"),
    ]:
        r = select(changed, event, m)
        for key in ("slow_run", "slow_selected", "carve_out_run"):
            assert key in r, f"missing {key} for {changed}/{event}"
        assert set(r["slow_selected"]) <= leg_set, \
            f"slow_selected must be subset of slow_files - carve_out ({changed}/{event})"


def test_docs_only_skips_slow_and_carve_out():
    """#2147/#2148: docs-only PRs touch no slow/carve-out surface —
    both formerly-unconditional legs (the audit's F1/F2 cost drivers) skip;
    the tier-1 smoke still runs in the fast job.

    #3332: a public site surface additionally selects the *fast* onboarding
    surface (see test_public_site_surface_change_selects_onboarding_and_skips_slow),
    but the slow and carve-out legs still skip — which is what this test pins."""
    for changed in (["docs/README.md"],
                    ["docs/README.md", "website/self-hosted.html"]):
        r = _sel(changed)
        assert r["full"] is False
        assert r["slow_run"] is False
        assert r["carve_out_run"] is False
        assert r["slow_selected"] == []


def test_full_selection_runs_both_legs_with_whole_slow_leg_set():
    """#2147/#2148: push/schedule/unknown-path/shared-module -> full:
    test-slow AND test-carve-out run, and slow_selected is the whole
    committed leg set (slow_files - carve_out) — the trunk + nightly
    backstop is never weakened by the PR-shape gates."""
    m = load_manifest()
    leg_set = sorted(set(m["slow_files"]) - set(m["carve_out"]))
    for changed, event in [
        ([], "push"),
        ([], "schedule"),
        (["tortoise/sdk.py"], "pull_request"),
        ([".github/workflows/python-ci.yml"], "pull_request"),
        (["mystery-dir/x.py"], "pull_request"),
    ]:
        r = select(changed, event, m)
        assert r["full"] is True, changed
        assert r["slow_run"] is True and r["carve_out_run"] is True, changed
        assert r["slow_selected"] == leg_set, changed


def test_tier2_slow_run_scoped_to_matched_surfaces():
    """#2148: tier-2 PRs run only their matched surfaces' slow files. ep
    owns test_dream / test_ep_sources / test_source_inheritance_own — a
    ranking.py-only PR selects exactly those (never the full 24-file leg
    set), and the carve-out job skips (ep owns no carve-out file)."""
    r = _sel(["tortoise/ranking.py"])
    assert r["full"] is False and r["surfaces"] == ["ep"]
    assert r["slow_run"] is True
    assert r["carve_out_run"] is False
    assert r["slow_selected"] == [
        "test_dream.py", "test_ep_sources.py", "test_source_inheritance_own.py"]


def test_tier2_carve_out_run_when_surface_owns_carve_files():
    """#2147: the carve-out job runs when a matched surface owns carve-out
    files. tortoise/embedded_lifecycle.py maps to core (embedded/daemon/
    registry/guard coverage) -> run; a carve-out TEST-file change selects
    its owning surface -> re-runs here; an onboarding-only change owns no
    carve-out file -> skip."""
    r = _sel(["tortoise/embedded_lifecycle.py"])
    assert r["full"] is False and "core" in r["surfaces"]
    assert r["carve_out_run"] is True
    assert r["slow_run"] is True  # core owns 15 slow-leg files too
    r2 = _sel(["tortoise/onboarding/AGENT_ONBOARDING.md"])
    assert r2["surfaces"] == ["onboarding"]
    assert r2["carve_out_run"] is False and r2["slow_run"] is False
    r3 = _sel(["tests/test_guard.py"])
    assert r3["carve_out_run"] is True and r3["slow_run"] is True


def test_full_mode_selection_unchanged():
    # #1371: push/schedule full mode keeps its exact contract (test_files ALL)
    # and only gains the slow_files key.
    r = _sel([], "push")
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    assert len(r["slow_files"]) == len(load_manifest()["slow_files"])

# ── #1429: auto-registration of unlisted test files ──────────────────────


def _tmp_manifest(tests_dir: Path, extra: str = "") -> Path:
    """Build a minimal manifest YAML with an api surface + tier1."""
    m = tests_dir / "ci-surfaces.yml"
    m.write_text(
        "version: 1" + "\n" +
        "surfaces:" + "\n" +
        "  api:" + "\n" +
        "  - test_existing_api.py" + "\n" +
        "  core:" + "\n" +
        "  - test_existing_core.py" + "\n" +
        extra + "\n" +
        "tier1:" + "\n" +
        "  - test_existing_api.py" + "\n"
    )
    return m


def test_register_adds_unlisted_file_under_surface():
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_new_thing.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_api.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_core.py").write_text("def test_x():\n    pass\n")
        m = _tmp_manifest(td)
        import yaml
        manifest = yaml.safe_load(m.read_text())
        added = register_tests(m, td, "api", manifest)
        assert added == ["test_new_thing.py"]
        # idempotent
        manifest2 = yaml.safe_load(m.read_text())
        assert register_tests(m, td, "api", manifest2) == []
        # file is registered + manifest still valid
        manifest3 = yaml.safe_load(m.read_text())
        assert "test_new_thing.py" in manifest3["surfaces"]["api"]
        assert classify_test_file("test_new_thing.py", manifest3) == "api"


def test_register_creates_surface_block_if_missing():
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_new_sdk.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_api.py").write_text("def test_x():\n    pass\n")
        m = _tmp_manifest(td)
        import yaml
        manifest = yaml.safe_load(m.read_text())
        added = register_tests(m, td, "sdk", manifest)
        assert added == ["test_new_sdk.py"]
        manifest2 = yaml.safe_load(m.read_text())
        assert "test_new_sdk.py" in manifest2["surfaces"]["sdk"]
        # new surface block landed BEFORE tier1:
        txt = m.read_text()
        assert txt.index("sdk:") < txt.index("tier1:")


def test_register_keeps_alphabetical_order():
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_zzz_new.py").write_text("def test_x():\n    pass\n")
        (td / "test_aaa_new.py").write_text("def test_x():\n    pass\n")
        (td / "test_mmm_new.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_api.py").write_text("def test_x():\n    pass\n")
        m = _tmp_manifest(td)
        import yaml
        manifest = yaml.safe_load(m.read_text())
        register_tests(m, td, "api", manifest)
        manifest2 = yaml.safe_load(m.read_text())
        api = manifest2["surfaces"]["api"]
        assert api == sorted(api), "surface list must stay alphabetized"
        assert api == ["test_aaa_new.py", "test_existing_api.py", "test_mmm_new.py", "test_zzz_new.py"]


def test_register_handles_manifest_without_trailing_newline():
    # #3073 item 1: a manifest whose final entry line lacks a newline
    # concatenated the appended entry onto it (`  - test_b.py  - test_c.py`,
    # malformed YAML). The terminator is normalised before insertion.
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_new_thing.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_api.py").write_text("def test_x():\n    pass\n")
        m = td / "ci-surfaces.yml"
        # the api block is LAST and its final line has NO newline
        m.write_text("surfaces:\n  api:\n  - test_existing_api.py")
        import yaml
        manifest = yaml.safe_load(m.read_text())
        assert register_tests(m, td, "api", manifest) == ["test_new_thing.py"]
        parsed = yaml.safe_load(m.read_text())  # must not raise
        assert parsed["surfaces"]["api"] == ["test_existing_api.py",
                                            "test_new_thing.py"]


def test_classify_and_select_tolerate_null_or_scalar_surface():
    # #3073 item 2: `integrity()`'s duplicate scan tolerated a None surface via
    # `files or ()`, but classification/selection did not — a bare `api:` key
    # (None) or a scalar raised `TypeError: argument of type 'NoneType' is not
    # iterable`, and a string would be iterated character-by-character. A
    # falsy/scalar value means "no members"; the drift gate reports its files.
    for bad in (None, 3, "test_x.py", {"a": 1}):
        m = load_manifest()
        m["surfaces"]["api"] = bad
        assert classify_test_file("test_api.py", m) is None
        r = select(["tortoise/api.py"], "pull_request", m)
        assert r["surfaces"] == ["api", "core"]  # CORE_ALSO still applies


def test_register_default_surface_is_core():
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_new_default.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_api.py").write_text("def test_x():\n    pass\n")
        m = _tmp_manifest(td)
        import yaml
        manifest = yaml.safe_load(m.read_text())
        register_tests(m, td, "core", manifest)
        manifest2 = yaml.safe_load(m.read_text())
        assert "test_new_default.py" in manifest2["surfaces"]["core"]


# ── #2913: comment-safe insertion + duplicate-safe registration ──────────


def test_register_never_splits_a_comment_run():
    """#2913: `pos` indexes entries (comment lines excluded) and must be
    converted to a physical line; treating it as a raw line offset dropped the
    new entry INSIDE a comment run and detached the run from the entries it
    documents."""
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_cc.py").write_text("def test_x():\n    pass\n")
        for name in ("test_a.py", "test_b.py", "test_c.py", "test_d.py"):
            (td / name).write_text("def test_x():\n    pass\n")
        (td / "test_existing_core.py").write_text("def test_x():\n    pass\n")
        m = td / "ci-surfaces.yml"
        m.write_text(
            "version: 1" + "\n" +
            "surfaces:" + "\n" +
            "  api:" + "\n" +
            "  - test_a.py" + "\n" +
            "  - test_b.py" + "\n" +
            "  # comment 1 about the group below" + "\n" +
            "  # comment 2 about the group below" + "\n" +
            "  - test_c.py" + "\n" +
            "  - test_d.py" + "\n" +
            "  core:" + "\n" +
            "  - test_existing_core.py" + "\n" +
            "tier1:" + "\n" +
            "  - test_a.py" + "\n"
        )
        import yaml
        manifest = yaml.safe_load(m.read_text())
        assert register_tests(m, td, "api", manifest) == ["test_cc.py"]
        lines = m.read_text().splitlines()
        # the comment run stays contiguous — no entry wedged between its lines
        i = lines.index("  # comment 1 about the group below")
        assert lines[i + 1] == "  # comment 2 about the group below", lines
        # the entry is among real entries and alphabetical order is preserved
        api = yaml.safe_load(m.read_text())["surfaces"]["api"]
        assert api == sorted(api)
        assert api == ["test_a.py", "test_b.py", "test_c.py",
                       "test_cc.py", "test_d.py"]


def test_register_already_present_in_surface_is_reported_noop(capsys):
    """#2913: a file already present in the target surface is a reported no-op.

    The old code did not write a duplicate line either (`to_add` filtered on
    `names`), but it RETURNED `missing`, so `--register` reported an
    already-present file as newly added. This pins the return value and the
    `already registered` report for a caller passing a stale manifest dict —
    the only way to reach that state, since the CLI always loads the manifest
    fresh from disk.
    """
    with tempfile.TemporaryDirectory() as d:
        td = Path(d)
        (td / "test_new_thing.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_api.py").write_text("def test_x():\n    pass\n")
        (td / "test_existing_core.py").write_text("def test_x():\n    pass\n")
        m = _tmp_manifest(td)
        import yaml
        stale = yaml.safe_load(m.read_text())
        assert register_tests(m, td, "api", stale) == ["test_new_thing.py"]
        assert m.read_text().count("  - test_new_thing.py\n") == 1
        # second call with the pre-registration manifest dict: the entry is
        # already in the surface text, so it must be a reported no-op.
        added = register_tests(m, td, "api", stale)
        assert added == []
        assert m.read_text().count("  - test_new_thing.py\n") == 1, \
            "a second line was written for an already-present entry"
        assert "already registered" in capsys.readouterr().out


def test_duplicate_entries_reports_same_surface_repeats():
    """#2913: a same-surface duplicate is invisible to select() (it unions
    surfaces) and to integrity() (it only asks "classified?") — the new
    duplicate_entries() check surfaces it for the --integrity note."""
    from tools.ci_selection import duplicate_entries
    m = {"surfaces": {"core": ["test_a.py", "test_a.py", "test_b.py"],
                      "api": ["test_a.py"]}}
    # cross-surface (dual) membership is deliberate — only the SAME-surface
    # repeat is reported
    assert duplicate_entries(m) == ["core: test_a.py"]
    assert duplicate_entries({"surfaces": {"core": ["test_a.py", "test_b.py"]}}) == []
    # a name repeated 3x is ONE distinct problem, not two
    assert duplicate_entries(
        {"surfaces": {"core": ["test_a.py", "test_a.py", "test_a.py"]}}
    ) == ["core: test_a.py"]
    # a value that is None (empty surface block) must not raise
    assert duplicate_entries({"surfaces": {"core": None}}) == []


# ── #1266: matrix halves ↔ manifest consistency ──────────────────────────
# The test (a)/(b) halves in python-ci.yml are a second source of truth next
# to config/ci-surfaces.yml. The #1262 integrity gate only covers manifest
# coverage — a slow file can leak into a half (fast-gate leak), a half can
# carry a file that left the manifest (unclassified drift), a half can tilt
# (rebalance drift), and files can silently fall out of BOTH halves (coverage
# hole). These tests pin the fail-closed checks on the actionable classes.

WF_HALVES_FIXTURE = """\
name: Python CI
jobs:
  test:
    strategy:
      matrix:
        half: [a, b]
        include:
          - half: a
            files: >-
              test_api test_auth test_slow_leak test_dead_entry
          - half: b
            files: >-
              test_api test_crypto bench/test_roundrobin
"""


def _halves_manifest(extra_slow: str = "") -> dict:
    return {
        "surfaces": {
            "api": ["test_api.py", "test_auth.py", "test_crypto.py"],
            "core": ["test_slow_leak.py", "test_dead_entry.py"],
        },
        "tier1": ["test_api.py"],
        "slow_files": ["test_slow_leak.py"],
    }


def test_parse_matrix_halves_extracts_both_halves():
    from tools.ci_selection import parse_matrix_halves
    halves = parse_matrix_halves(WF_HALVES_FIXTURE)
    assert set(halves) == {"a", "b"}
    assert halves["a"] == ["test_api", "test_auth", "test_slow_leak", "test_dead_entry"]
    assert halves["b"] == ["test_api", "test_crypto", "bench/test_roundrobin"]


def test_halves_slow_leak_flagged():
    from tools.ci_selection import parse_matrix_halves, workflow_halves_issues
    halves = parse_matrix_halves(WF_HALVES_FIXTURE)
    issues = workflow_halves_issues(_halves_manifest(), halves)
    assert any("test_slow_leak" in i and "leak" in i for i in issues), issues


def test_halves_unclassified_entry_flagged():
    from tools.ci_selection import parse_matrix_halves, workflow_halves_issues
    m = _halves_manifest()
    m["surfaces"]["api"] = ["test_api.py", "test_crypto.py"]  # drop test_auth
    halves = parse_matrix_halves(WF_HALVES_FIXTURE)
    issues = workflow_halves_issues(m, halves)
    assert any("test_auth" in i and "surface" in i for i in issues), issues


def test_halves_duplicate_entry_flagged():
    from tools.ci_selection import workflow_halves_issues
    halves = {"a": ["test_api"], "b": ["test_api", "test_crypto"]}
    issues = workflow_halves_issues(_halves_manifest(), halves)
    assert any("test_api" in i and "BOTH halves" in i for i in issues), issues


def test_halves_imbalance_flagged_beyond_tolerance():
    from tools.ci_selection import workflow_halves_issues
    halves = {"a": ["test_api", "test_auth", "test_crypto",
                     "test_slow_leak", "test_dead_entry"],
              "b": ["test_api"]}
    issues = workflow_halves_issues(_halves_manifest(), halves)
    assert any("imbalanced" in i for i in issues), issues


def test_halves_imbalance_within_tolerance_clean():
    from tools.ci_selection import workflow_halves_issues
    halves = {"a": ["test_api", "test_auth"], "b": ["test_crypto", "test_api"]}
    issues = workflow_halves_issues(_halves_manifest(), halves)
    assert not any("imbalanced" in i for i in issues), issues


def test_fast_files_absent_from_halves_reports_coverage_hole():
    from tools.ci_selection import fast_files_absent_from_halves
    halves = {"a": ["test_api"], "b": ["test_crypto"]}
    absent = fast_files_absent_from_halves(_halves_manifest(), halves)
    assert absent == ["test_auth.py", "test_dead_entry.py"], absent  # slow excluded


def test_real_workflow_halves_are_consistent():
    # #1472: the matrix halves are now DERIVED from the manifest
    # (space-joined matrix_* outputs) —
    # the #1266 discipline runs against the derivation. Verify the derived
    # halves carry every fast file exactly once and tilt is bounded.
    # #3400: the tilt invariant is now DURATION, not count. The full-matrix
    # halves are packed by measured weight (LPT), so a correct split is
    # duration-balanced while carrying very different file counts — the count
    # difference is the design (a few multi-minute files against the long tail
    # of sub-second ones), and the assertion below checks the balance, not the
    # count. The old `abs(count_a - count_b) <= 3` assertion encoded the
    # duration-blind parity split this issue exists to remove.
    from tools.ci_selection import (TESTS_DIR, push_legs,  # noqa: I001
                                    workflow_halves_issues,
                                    HALF_DURATION_IMBALANCE_RATIO)
    m = load_manifest()
    legs = push_legs(m)
    halves = {"a": set(legs["half_a"]), "b": set(legs["half_b"])}
    issues = workflow_halves_issues(m, halves, TESTS_DIR)
    assert issues == [], f"derived halves drift: {issues}"
    weights = {h: sum(m["durations"].get(f + ".py", 2.0) for f in fs)
               for h, fs in halves.items()}
    ratio = max(weights.values()) / min(weights.values())
    assert ratio <= HALF_DURATION_IMBALANCE_RATIO, (
        f"duration tilt beyond {HALF_DURATION_IMBALANCE_RATIO}x: "
        f"{ {h: round(w / 60, 1) for h, w in weights.items()} } min "
        f"(ratio {ratio:.2f}x)")
    # every fast file rides exactly one half (no coverage hole, no double-run)
    assert not (halves["a"] & halves["b"]), "leg overlap"


def test_push_legs_partitions_every_classified_file():
    """#1472: every classified file lands in exactly one push leg. Epic
    #1647 Task 9: the carve-out set is its OWN leg (E2E-4) — it is
    excluded from fast AND slow docker legs."""
    from tools.ci_selection import push_legs, ENV_BROKEN_FILES  # noqa: I001
    m = load_manifest()
    legs = push_legs(m)
    carve = {f.replace(".py", "") for f in m["carve_out"]}
    classified = set()
    for s, files in m["surfaces"].items():  # noqa: B007
        classified.update(files)
    classified.update(m["tier1"])
    classified.update(m["slow_files"])
    fast = {f.replace(".py", "") for f in classified if f not in m["slow_files"]}
    fast |= {f.replace(".py", "") for f in m.get("push_extra", [])}
    broken = {f.replace(".py", "") for f in ENV_BROKEN_FILES}
    assert set(legs["half_a"]) | set(legs["half_b"]) == fast - broken - carve
    assert not (set(legs["half_a"]) & set(legs["half_b"])), "leg overlap"
    # carve-out files never ride the docker legs (fast OR slow)
    assert not (set(legs["half_a"]) & carve) and not (set(legs["half_b"]) & carve)
    assert not (set(legs["slow"]) & carve), \
        "slow carve-out files run in the URI-unset carve-out job, never the slow legs"
    assert set(legs["carve_out"]) == carve, "carve_out leg must be exactly the config set"
    # #1485 / #3400: bench files are NOT pinned to a half. `push_extra` is
    # spread evenly across the halves (#1485) and a bench file registered in
    # `surfaces` is packed by measured duration (#3400 LPT), so which half a
    # given bench file lands in is a packing outcome, not an assignment. The
    # pre-#1485 form of this check required a bench file in half_b SPECIFICALLY
    # and re-staled the moment a pool change moved one (#3811: adding a single
    # classified file flipped all three bench files into half_a, reddening an
    # unrelated PR). Assert the invariant the code actually provides HERE: every
    # bench file reaches a half (the partition assertion above). The push_extra
    # spread rule is not asserted against this manifest — the shipped
    # `push_extra` is empty, so it would be vacuous — it is pinned on a
    # SYNTHETIC manifest by test_push_legs_distributes_push_extra_across_halves
    # below (#2988/#3243).
    assert any(f.startswith("bench/") for f in legs["half_a"] + legs["half_b"]), \
        "no bench file reached the push legs at all"


def test_push_legs_distributes_push_extra_across_halves():
    """#1485: ``push_extra`` is spread across the halves (even index -> half_a,
    odd -> half_b), never dumped on one.

    Pinned on a SYNTHETIC manifest: the shipped ``push_extra`` is empty (the
    bench files are classified under the `eval` surface and packed by LPT), so
    asserting this against the real manifest is vacuous — and pinning which
    half a *bench* file lands on was a function of the duration estimates, not
    a designed invariant (#2988/#3243 corrected the stale
    test_selfhost_health_probe_executor.py weight, which flipped it).
    """
    from tools.ci_selection import push_legs

    m = dict(load_manifest())
    m["push_extra"] = ["bench/synthetic_a.py", "bench/synthetic_b.py"]
    legs = push_legs(m)
    assert "bench/synthetic_a" in legs["half_a"], legs["half_a"]
    assert "bench/synthetic_b" in legs["half_b"], legs["half_b"]


def test_carve_out_mirrors_test_no_redirect_stems():
    """#4047: `carve_out:` and `TEST_NO_REDIRECT_STEMS` are one set in two homes.

    `config/ci-surfaces.yml`'s `carve_out:` routes a file to the URI-unset
    carve-out job and bars it from every docker leg; `tests/_embedded.py`'s
    `TEST_NO_REDIRECT_STEMS` is the redirect exemption that keeps a module
    embedded if it is executed with a URI set. A stem in only ONE of them is a
    silent hole, in opposite directions (see the failure message).

    Scope, stated honestly: this pin catches ONE-LIST-ONLY drift. It does NOT
    catch #4047's own shape — the fork guards were missing from BOTH lists, so
    the two sets were EQUAL then and this assertion passed on the pre-fix tree.
    That shape is caught by the source scan in
    `tests/test_markers.py::test_module_level_embedded_only_modules_are_carve_out`;
    the two guards cover different holes and neither subsumes the other.
    """
    from tests._embedded import TEST_NO_REDIRECT_STEMS
    m = load_manifest()
    # The one documented asymmetry: the bench smoke file is path-qualified in
    # the manifest (`bench/...`) and bare-stem keyed in the registry. The
    # registry/redirect mechanism is itself stem-keyed, so a collapse can only
    # happen where the stem genuinely collides.
    carve = {f.removesuffix(".py").removeprefix("bench/") for f in m["carve_out"]}
    registry = set(TEST_NO_REDIRECT_STEMS)
    assert carve == registry, (
        "carve_out and TEST_NO_REDIRECT_STEMS have drifted — a stem in only "
        "one of the two registries is a silent hole: "
        f"registry-only={sorted(registry - carve)} — in the redirect registry "
        "but NOT routed to the carve-out job, so a full (URI-set) selection "
        "collects it on a docker leg and skips its embedded_only-marked tests "
        "there; with a module-level mark that is the whole file, reported "
        "green. "
        f"carve-out-only={sorted(carve - registry)} — routed to the carve-out "
        "job but not redirect-exempt, so an out-of-band URI run would flip its "
        "embedded constructions to the server lane instead of the embedded "
        "daemon")


def test_integrity_no_matrix_drift():
    """#1472: integrity must pass with the derived matrix (no hardcoded lists)."""
    from tools.ci_selection import leg_coverage_issues, workflow_matrix_issues, REPO  # noqa: I001
    m = load_manifest()
    assert leg_coverage_issues(m) == []
    wf = REPO / ".github" / "workflows" / "python-ci.yml"
    assert workflow_matrix_issues(str(wf), m) == []


def test_split_balances_heavy_files():
    """#1473: LPT puts the heavy files on alternating halves."""
    from tools.ci_selection import split_fast_gate
    files = ["tests/test_calibration.py", "tests/test_analyze.py",
             "tests/test_main_guards.py", "tests/test_ops_safety.py"]
    durations = {"test_calibration.py": 52.1, "test_analyze.py": 3.9,
                 "test_main_guards.py": 19.5, "test_ops_safety.py": 16.8}
    a, b = split_fast_gate(files, durations)
    # LPT: calibration(52.1)->a, main_guards(19.5)->b, ops_safety(16.8)->b,
    # analyze(3.9)->b (a stays heavier after calibration).
    assert a == ["tests/test_calibration.py"]
    assert sorted(b) == ["tests/test_analyze.py", "tests/test_main_guards.py",
                         "tests/test_ops_safety.py"]
    # imbalance bounded (vs parity which could cluster 52.1+19.5 on one half)
    wa = 52.1
    wb = 19.5 + 16.8 + 3.9
    assert max(wa, wb) / min(wa, wb) < 1.5


def test_split_is_deterministic_and_ties_go_a():
    from tools.ci_selection import split_fast_gate
    files = ["tests/test_a.py", "tests/test_b.py"]
    a1, b1 = split_fast_gate(files, {"test_a.py": 1.0, "test_b.py": 1.0})
    a2, b2 = split_fast_gate(files, {"test_a.py": 1.0, "test_b.py": 1.0})
    assert a1 == a2 and b1 == b2
    assert a1 == ["tests/test_a.py"]  # tie -> a


def test_split_default_weight_for_unmeasured():
    from tools.ci_selection import split_fast_gate
    files = ["tests/test_new_a.py", "tests/test_new_b.py"]
    a, b = split_fast_gate(files, {})  # no durations -> default 2.0
    assert sorted(a + b) == sorted(files)
    assert len(a) == 1 and len(b) == 1


def test_split_rejects_non_list():
    from tools.ci_selection import split_fast_gate  # noqa: I001
    import pytest as _pytest
    with _pytest.raises(ValueError):
        split_fast_gate("ALL", {})


def test_duration_integrity():
    from tools.ci_selection import duration_issues, load_manifest
    m = load_manifest()
    assert duration_issues(m) == []
    slow_now_ok = dict(m)
    slow_now_ok["durations"] = {"test_about_edges.py": 10.0}  # a slow file
    assert duration_issues(slow_now_ok) == []
    carve_ok = dict(m)
    carve_ok["durations"] = {"test_reaper.py": 195.9}
    assert duration_issues(carve_ok) == []
    bad2 = dict(m)
    bad2["durations"] = {"not_a_real_file.py": 10.0}
    assert duration_issues(bad2) != []


# ── #3400: duration-balanced full-matrix halves + durations coverage ──────
# The push halves used to be index-parity (`fast[0::2]` / `fast[1::2]`) —
# duration-blind, so half (b) collected the slow files by luck, tilted the split
# far past the ratio the assertion below allows, and blew the 55m watchdog
# (#3400). These pin the LPT pack (#1473)
# on the full-matrix path and the coverage floor that keeps the `durations`
# map from rotting back to a handful of entries.


def _duration_manifest(heavy: dict[str, float],
                       tiny_count: int) -> dict:
    """A synthetic full-matrix manifest: a few heavy files + many 2s files,
    all in one freshly-named surface so nothing touches the real pool."""
    tiny = [f"test_tiny_{i:04d}.py" for i in range(tiny_count)]
    files = [*heavy.keys(), *tiny]
    durations = {**heavy, **{f: 2.0 for f in tiny}}
    return {"surfaces": {"core": files}, "tier1": [], "slow_files": [],
            "carve_out": [], "push_extra": [], "durations": durations}


def test_full_matrix_split_is_duration_balanced():
    """#3400: the full-matrix (push) halves are packed by measured duration.

    Four heavy files + many 2s files: parity can cluster the heavies on one
    half; LPT must not.  The assertion is the *duration* ratio, not a count
    ratio — a correct pack of the real pool carries unequal counts.
    """
    from tools.ci_selection import HALF_DURATION_IMBALANCE_RATIO, push_legs
    heavy = {"test_h0.py": 850.0, "test_h1.py": 700.0,
             "test_h2.py": 650.0, "test_h3.py": 600.0}
    m = _duration_manifest(heavy, tiny_count=200)
    legs = push_legs(m)
    a, b = set(legs["half_a"]), set(legs["half_b"])
    assert not (a & b), "leg overlap"
    assert a | b == {f[:-3] for f in m["surfaces"]["core"]}, "coverage hole"
    weights = {h: sum(m["durations"][f + ".py"] for f in fs)
               for h, fs in (("a", a), ("b", b))}
    ratio = max(weights.values()) / min(weights.values())
    assert ratio <= HALF_DURATION_IMBALANCE_RATIO, (
        f"parity-style tilt survived: { {h: round(w / 60, 1) for h, w in weights.items()} }"
        f" min (ratio {ratio:.2f}x)")
    # the heavy files must be SPREAD — the 850s file must not sit with every
    # other heavy file on one half while the other side carries only 2s files.
    a_heavy = {f for f in heavy if f[:-3] in a}
    b_heavy = {f for f in heavy if f[:-3] in b}
    assert a_heavy and b_heavy, (
        f"heavy files clustered on one half: a={sorted(a_heavy)} b={sorted(b_heavy)}")
    assert len(a_heavy) < len(heavy) and len(b_heavy) < len(heavy)
    # and the OLD parity split of the same pool is the thing being fixed
    order = sorted(m["surfaces"]["core"])
    p_a, p_b = order[0::2], order[1::2]
    p_wa = sum(m["durations"][f] for f in p_a)
    p_wb = sum(m["durations"][f] for f in p_b)
    parity_ratio = max(p_wa, p_wb) / min(p_wa, p_wb)
    assert parity_ratio > ratio, (
        f"fixture does not exercise the defect: parity {parity_ratio:.2f}x "
        f"vs LPT {ratio:.2f}x")


def test_push_legs_is_deterministic():
    """#3400: same manifest -> byte-identical halves, repeated calls."""
    from tools.ci_selection import push_legs
    m = _duration_manifest({"test_h0.py": 850.0, "test_h1.py": 700.0}, 50)
    first = push_legs(m)
    assert push_legs(m) == first
    assert push_legs(m) == first
    real = load_manifest()
    assert push_legs(real) == push_legs(real)


def test_halves_duration_imbalance_flagged():
    """#3400: a heavy file dumped on one half reds even when counts look even."""
    from tools.ci_selection import workflow_halves_issues
    m = _duration_manifest({"test_big.py": 600.0}, tiny_count=10)
    # 5 vs 6 files — a count-balanced split, duration-lopsided
    halves = {"a": ["test_big", "test_tiny_0000", "test_tiny_0002",
                     "test_tiny_0004", "test_tiny_0006"],
              "b": ["test_tiny_0001", "test_tiny_0003", "test_tiny_0005",
                     "test_tiny_0007", "test_tiny_0008", "test_tiny_0009"]}
    issues = workflow_halves_issues(m, halves)
    assert any("duration-imbalanced" in i for i in issues), issues


def test_duration_coverage_guard_fires_when_low():
    """#3400: 15 weights for 100 fast files is the rot this guard forbids."""
    from tools.ci_selection import duration_coverage_issues
    m = _duration_manifest({}, tiny_count=0)
    m["surfaces"]["core"] = [f"test_cov_{i:03d}.py" for i in range(100)]
    m["durations"] = {f: 2.0 for f in m["surfaces"]["core"][:15]}
    issues = duration_coverage_issues(m)
    assert issues, "guard did not bite at 15% coverage"
    assert any("15.0%" in i and "floor" in i for i in issues), issues


def test_duration_coverage_guard_boundary_and_realistic():
    """#3400: the floor is inclusive; realistic coverage is silent."""
    from tools.ci_selection import duration_coverage_issues, load_manifest
    files = [f"test_cov_{i:03d}.py" for i in range(100)]
    below = {"surfaces": {"core": files}, "tier1": [], "slow_files": [],
             "durations": {f: 2.0 for f in files[:89]}}
    at = {"surfaces": {"core": files}, "tier1": [], "slow_files": [],
          "durations": {f: 2.0 for f in files[:90]}}
    above = {"surfaces": {"core": files}, "tier1": [], "slow_files": [],
             "durations": {f: 2.0 for f in files[:95]}}
    assert duration_coverage_issues(below) != [], "89% must fire"
    assert duration_coverage_issues(at) == [], "90% is at the floor, not below"
    assert duration_coverage_issues(above) == [], "95% must be silent"
    assert duration_coverage_issues(load_manifest()) == []


def test_duration_coverage_guard_backwards_compatible():
    """#3400: an absent/empty durations map is never a hard failure."""
    from tools.ci_selection import duration_coverage_issues
    files = [f"test_cov_{i:03d}.py" for i in range(100)]
    base = {"surfaces": {"core": files}, "tier1": [], "slow_files": []}
    assert duration_coverage_issues(base) == []              # key absent
    assert duration_coverage_issues({**base, "durations": {}}) == []   # empty
    assert duration_coverage_issues({**base, "durations": None}) == []  # null
    # a repo with no fast files at all must not divide by zero
    assert duration_coverage_issues(
        {"surfaces": {}, "tier1": [], "slow_files": [],
         "durations": {"x.py": 1.0}}) == []


# ── #1668: the P2 flip's workflow-wiring pins (epic #1647 Task 6) ─────────
# cycle-7 P2-1 (test_expect_uri_gated_iff_uri) + cycle-8 P2-12
# (test_live_required_job_runs_only_declared_live_tests): YAML-parsing
# pins on .github/workflows/python-ci.yml so the flip's invariants cannot
# silently regress — a half-b URI decoupled from the EXPECT_URI tripwire
# signal, or a third live test added to test-concurrency-falkor without the
# audit, reds at PR time.

import re as _re  # noqa: E402


def _load_python_ci() -> dict:
    import yaml
    wf_path = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "python-ci.yml"
    return yaml.safe_load(wf_path.read_text())


def test_expect_uri_gated_iff_uri():
    """cycle-7 P2-1 + Task 9 (P3): the docker URI and the E2E-6 tripwire
    signal (TORTOISE_TEST_EXPECT_URI) are set under the SAME gate —
    full==true on BOTH halves (Task 9 flipped half a; the embedded lane is
    retired from the main test job) — and mapped onto the pytest step's env
    together. A future edit that decouples them (URI without the tripwire
    signal, the signal without the URI, or a half-specific gate) reds."""
    wf = _load_python_ci()
    steps = wf["jobs"]["test"]["steps"]
    compute = next(s for s in steps
                   if "Compute docker URI" in s.get("name", ""))
    script = compute["run"]
    # Task 9 (P3): the gate is full==true ONLY — both halves ride the
    # provisioned service; a half-specific gate would resurrect the
    # embedded canary half. Tier-2 PRs (full=false) stay URI-less.
    gate = ('if [ "${{ needs.changes.outputs.full }}" = "true" ]; then')
    assert gate in script, "the docker gate must be full==true (both halves)"
    assert "matrix.half" not in script, \
        "the URI gate must NOT be half-specific (Task 9 flipped BOTH halves)"
    then_block = script.split("then", 1)[1].split("fi", 1)[0]
    assert 'URI="docker://:falkordb@localhost:6379/tortoise_test_matrix"' in then_block
    assert 'EXPECT_URI="1"' in then_block
    assert 'echo "URI=$URI" >> "$GITHUB_ENV"' in script
    assert 'echo "EXPECT_URI=$EXPECT_URI" >> "$GITHUB_ENV"' in script
    # Iff BOTH directions: the assignments must NOT appear anywhere else in
    # the script (outside the gate's then-block), so a future edit that sets
    # URI or EXPECT_URI on any other shape reds.
    outside_then = script.split("then", 1)[1].split("fi", 1)[1]
    assert 'URI="' not in outside_then and 'EXPECT_URI="' not in outside_then, \
        "URI/EXPECT_URI must be set ONLY inside the full==true gate"
    # The pytest run step maps BOTH onto its env — iff at the YAML level.
    run = next(s for s in steps
               if s.get("name", "").startswith("Run fast test suite"))
    env = run["env"]
    assert env["TORTOISE_DB_URI"] == "${{ env.URI }}"
    assert env["TORTOISE_TEST_EXPECT_URI"] == "${{ env.EXPECT_URI }}"
    # Task 9: the coverage manifest is generated on BOTH halves (no half-b
    # `if:` gate on the manifest step — the manifest must cover both docker
    # halves), and the skip-guard step's manifest mode is gated on rc==0 AND
    # non-empty $FILES (plan-review P1-7 — the "no selected files" path
    # writes rc=0 with no junitxml; with a manifest that would false-red).
    manifest = next(s for s in steps
                    if s.get("name", "").startswith("Generate coverage manifest"))
    assert "matrix.half" not in manifest.get("if", ""), \
        "the manifest step must run on BOTH halves (P3)"
    guard = next(s for s in steps
                 if s.get("name", "").startswith("Skip-fail guard"))
    assert '-s "${RUNNER_TEMP:-/tmp}/pytest-files"' in guard["run"]
    assert "--manifest /tmp/expected-nodeids.txt" in guard["run"]
    # The canary producer is gated to half b + post-merge (cycle-5 P1-7
    # option (b): exactly ONE leg writes, no last-writer-wins clobber).
    # #4367: the nightly schedule trigger was removed, so post-merge is
    # `push` alone — the gate no longer names the retired event.
    producer = next(s for s in steps
                    if s.get("name", "").startswith("Canary producer"))
    assert producer["if"] == "github.event_name == 'push'", \
        "producer must be post-merge only"
    assert 'if [ "${{ matrix.half }}" = "b" ]; then' in producer["run"], \
        "the producer must be gated on half b (one writer)"


def test_carve_out_env_gated_inverse_of_uri():
    """Epic #1647 Task 10 (P4, P1-9): the URI-required enforcement's CI
    wiring. The compute step sets TORTOISE_TEST_CARVE_OUT=1 iff full==false
    (the tier-2 URI-less embedded shape) — the EXACT inverse of the docker
    URI gate — and the pytest run steps map it onto their env. A future
    edit that drops the tier-2 opt-in reds every tier-2 PR (the enforcement
    would fail the URI-less session); one that sets CARVE_OUT on the docker
    lane would disable the enforcement exactly where it matters."""
    wf = _load_python_ci()
    for job_name in ("test", "test-slow"):
        steps = wf["jobs"][job_name]["steps"]
        compute = next(s for s in steps
                       if "Compute docker URI" in s.get("name", ""))
        script = compute["run"]
        gate = ('if [ "${{ needs.changes.outputs.full }}" = "true" ]; then')
        assert gate in script
        # the docker then-branch runs from the gate keyword to the bash
        # `else` — CARVE_OUT must never be set there (URI satisfies the
        # enforcement on the docker lane)
        then_branch = script.split("then", 1)[1].split("else", 1)[0]
        assert 'CARVE_OUT="1"' not in then_branch, \
            f"{job_name}: CARVE_OUT must NOT be set on the docker lane " \
            "(URI is set there — the enforcement is satisfied by the URI)"
        assert script.count('CARVE_OUT="1"') == 1, \
            f"{job_name}: exactly one CARVE_OUT=\"1\" assignment (the tier-2 " \
            "else branch — the URI-less embedded shape opts in)"
        assert 'CARVE_OUT=""' in script, f"{job_name}: CARVE_OUT declared empty"
        assert 'echo "CARVE_OUT=$CARVE_OUT"' in script
        run = next(s for s in steps
                   if s.get("name", "").startswith("Run fast test suite")
                   or s.get("name", "").startswith("Run slow test suite"))
        assert run["env"]["TORTOISE_TEST_CARVE_OUT"] == "${{ env.CARVE_OUT }}", \
            f"{job_name}: the run step must map TORTOISE_TEST_CARVE_OUT"


def test_pmv_job_carries_uri_manifest_guard():
    """Epic #1647 Task 10 Step 1a (P1-9 + cycle-2 P2-14 + cycle-4 P2-11):
    post-merge-validation is now a docker lane — job-level TORTOISE_DB_URI +
    EXPECT_URI, falkordb services, a manifest-generation step BEFORE the
    pytest step replicating the run's OWN excludes (--ignore tests/e2e + the
    SLOW_IGNORES list + `-m not track_b`), the run emitting junitxml with
    -r fEs (never -rfE), and a skip-guard step gated on rc==0 with the
    junitxml-reconciled manifest. A manifest built without the run's
    excludes expects e2e/slow nodeids the run never produces and every
    merge reds on vanished nodeids."""
    import yaml as _yaml
    wf_path = (Path(__file__).resolve().parents[1] / ".github" / "workflows"
               / "post-merge-validation.yml")
    wf = _yaml.safe_load(wf_path.read_text())
    job = wf["jobs"]["validate"]
    assert job["env"]["TORTOISE_DB_URI"] == \
        "docker://:falkordb@localhost:6379/tortoise_test_matrix", \
        "pmv must run the docker lane with the test-prefixed URI path"
    assert job["env"]["TORTOISE_TEST_EXPECT_URI"] == "1"
    assert "falkordb" in job.get("services", {}) and \
        "falkordb-legacy" in job.get("services", {}), \
        "pmv must provision BOTH falkordb services (6379 URI + 16379 probes)"
    steps = job["steps"]
    run = next(s for s in steps
               if s.get("name", "").startswith("Run tests"))
    invocation = run["run"]
    assert "--junitxml=/tmp/pmv-junit.xml" in invocation
    assert "-o junit_family=xunit1" in invocation
    cmdline = next(line for line in invocation.splitlines()
                   if "python -m pytest" in line)
    assert "-r fEs" in cmdline and "-rfE" not in cmdline, \
        "pmv must use -r fEs (the skip-summary superset), never -rfE"
    assert "--ignore=tests/e2e" in cmdline
    assert "$SLOW_IGNORES" in cmdline
    assert "-m 'not track_b'" in cmdline
    assert "pmv-rc" in invocation
    manifest = next(s for s in steps
                    if s.get("name", "").startswith("Generate coverage manifest"))
    assert steps.index(manifest) < steps.index(run), \
        "the pmv manifest collect-only must run BEFORE pytest"
    mrun = manifest["run"]
    assert "--emit-manifest \"tests/\"" in mrun
    assert "--marker \"not track_b\"" in mrun
    assert "--ignore tests/e2e" in mrun
    assert "$SLOW_IGNORES" in mrun
    guard = next(s for s in steps
                 if s.get("name", "").startswith("Skip-fail guard"))
    grun = guard["run"]
    assert "--junitxml=/tmp/pmv-junit.xml" in grun
    assert "--manifest /tmp/pmv-expected-nodeids.txt" in grun
    assert '"$RC" = "0"' in grun, "the guard is gated on pytest rc==0"


def test_live_required_job_runs_only_declared_live_tests():
    """cycle-8 P2-12: test-concurrency-falkor runs EXACTLY the declared
    live-test pair — tests/test_event_store.py::test_seq_is_monotonic_under_concurrency_live_falkor
    and tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes.
    A third live test added to the job without extending this pin reds (the
    audit's executable form — the old audit named two test files but grepped
    only test_embedded_concurrency, so a new live test in test_event_store
    was invisible)."""
    wf = _load_python_ci()
    job = wf["jobs"]["test-concurrency-falkor"]
    steps = job["steps"]
    run = next(s for s in steps
               if s.get("name", "").startswith("Run live concurrency tests"))
    nodeids = _re.findall(r"tests/[A-Za-z0-9_/.]+\.py::[A-Za-z0-9_]+", run["run"])
    declared = {
        "tests/test_event_store.py::test_seq_is_monotonic_under_concurrency_live_falkor",
        "tests/test_embedded_concurrency.py::test_concurrent_writers_live_falkor_no_lost_writes",
    }
    assert set(nodeids) == declared, (
        f"test-concurrency-falkor must run exactly {sorted(declared)}, got "
        f"{sorted(set(nodeids))} (a new live test needs the audit + the "
        "test-prefixed job URI, epic #1647 cycle-8 P2-12)"
    )
    # The job-level URI stays the NON-test-prefixed path — inert because
    # both live tests pass explicit test-prefixed graph names and never
    # bulk-wipe the URI default (verified test_embedded_concurrency
    # L111-153 + test_event_store L165-185). If a future test adds a
    # path=/URI-default DETACH, the job URI must gain the P1-2 test-prefixed
    # path.
    assert job["env"]["TORTOISE_DB_URI"] == "docker://:falkordb@localhost:6379/tortoise"


def test_test_slow_job_carries_junitxml_manifest_guard():
    """cycle-7 P2-1 (int-1) + Task 9 Step 5: the test-slow legs flip to
    docker with the fast job's exact wiring — (a) the pytest invocation
    carries --junitxml=/tmp/junit.xml -o junit_family=xunit1 AND -r fEs
    (never the historical -rfE — pytest 9.1.1 replaces the report set on
    repeated -r flags, so a trailing -rfE would suppress the skip summary
    the guard depends on), (b) a manifest-generation step exists BEFORE the
    pytest step (echoing each leg's ${{ matrix.files }} to
    ${RUNNER_TEMP}/pytest-files + --collect-only -m 'not track_b' ->
    /tmp/expected-nodeids.txt), and (c) a skip-guard step gated on rc==0 AND
    non-empty $FILES runs tools/skip-guard.py with --manifest."""
    wf = _load_python_ci()
    steps = wf["jobs"]["test-slow"]["steps"]
    run = next(s for s in steps
               if s.get("name", "").startswith("Run slow test suite"))
    invocation = run["run"]
    assert "--junitxml=/tmp/junit.xml" in invocation
    assert "-o junit_family=xunit1" in invocation
    # the pytest command line itself (the comment block may mention the
    # historical flag) must carry -r fEs and never -rfE
    cmdline = next(line for line in invocation.splitlines()
                   if "python -m pytest" in line)
    assert "-r fEs" in cmdline and "-rfE" not in cmdline, \
        "test-slow must use -r fEs (the guard's skip-summary superset), never -rfE"
    assert "pytest-rc" in invocation, "the run step must persist pytest rc"
    assert "pytest-files" in invocation
    # (b) manifest generation step BEFORE the run step (step order)
    manifest = next(s for s in steps
                    if s.get("name", "").startswith("Generate coverage manifest"))
    assert steps.index(manifest) < steps.index(run), \
        "the manifest collect-only must run BEFORE pytest (off the watchdog)"
    mrun = manifest["run"]
    assert "pytest-files" in mrun
    assert "--collect-only" in mrun or "--emit-manifest" in mrun
    # the slow legs' URI gate matches the fast job (full==true only)
    uri = next(s for s in steps
               if s.get("name", "").startswith("Compute docker URI"))
    assert uri["run"].count("if [ \"${{ needs.changes.outputs.full }}\" = \"true\" ]; then") == 1
    # (c) skip-guard step gated on rc==0 AND non-empty $FILES
    guard = next(s for s in steps
                 if s.get("name", "").startswith("Skip-fail guard"))
    grun = guard["run"]
    assert '-s "${RUNNER_TEMP:-/tmp}/pytest-files"' in grun
    assert "--manifest /tmp/expected-nodeids.txt" in grun
    # the slow legs no longer carry carve-out files (E2E-4 owns them)
    leg_files = " ".join(i.get("files", "") for i in
                         wf["jobs"]["test-slow"]["strategy"]["matrix"]["include"])
    for carved in ("test_reaper", "test_flip_gate", "test_migrate_db",
                   "test_projection_lifecycle", "test_embedded_concurrency",
                   "test_hosted_backup"):
        assert carved not in leg_files.split(), \
            f"slow carve-out file {carved} must not ride the docker slow legs"


def test_carve_out_job_uri_unset_with_carve_out_flag():
    """E2E-4 (Task 9 Step 5): the dedicated carve-out job runs the embedded
    set URI-UNSET (no TORTOISE_DB_URI — a URI would redirect the
    carve-out to the server lane) with TORTOISE_TEST_CARVE_OUT=1 (the P4
    enforcement-prep escape), and consumes the changes job's carve_out
    output as its file list."""
    wf = _load_python_ci()
    job = wf["jobs"]["test-carve-out"]
    assert "TORTOISE_DB_URI" not in job.get("env", {}) or \
        not job["env"].get("TORTOISE_DB_URI"), \
        "the carve-out job must be URI-UNSET (embedded lane)"
    assert job["env"]["TORTOISE_TEST_CARVE_OUT"] == "1"
    assert "TORTOISE_TEST_EXPECT_URI" not in job.get("env", {}), \
        "EXPECT_URI on the carve-out would trip the E2E-6 tripwire (no URI)"
    run = next(s for s in job["steps"]
               if s.get("name", "").startswith("Run carve-out suite"))
    assert "needs.changes.outputs.carve_out" in run["run"], \
        "the carve-out job must consume the selector's carve_out leg"
    assert "--junitxml=/tmp/junit.xml" in run["run"]


def test_diff_gated_jobs_consume_changes_outputs():
    """#2147/#2148 (2026-09-02-ci-audit F1/F2): the workflow wiring pins.
    test-slow + test-carve-out gate their runs on the changes job's
    slow_run / carve_out_run outputs (docs/config-only PRs skip both legs);
    the changes job emits + echoes the diff-gate outputs; test-slow's
    manifest + run steps intersect the committed leg rows with
    slow_selected on the tier-2 shape (full == false) so only the matched
    surfaces' slow files run. The committed matrix rows stay literal (the
    in-workflow drift-guard step pins their union to slow_files - carve_out)."""
    wf = _load_python_ci()
    # job-level gates
    assert wf["jobs"]["test-slow"]["if"] == \
        "needs.changes.outputs.slow_run == 'true'", \
        "test-slow must diff-gate on slow_run (#2148)"
    cj_if = wf["jobs"]["test-carve-out"]["if"]
    assert "carve_out_run == 'true'" in cj_if, \
        "test-carve-out must diff-gate on carve_out_run (#2147)"
    # changes job: outputs + echoes
    outs = wf["jobs"]["changes"]["outputs"]
    for key in ("slow_run", "carve_out_run", "slow_selected"):
        assert key in outs, f"changes job must emit {key}"
    sel_step = next(s for s in wf["jobs"]["changes"]["steps"]
                    if s.get("id") == "select")
    for key in ("slow_run=", "carve_out_run=", "slow_selected="):
        assert key in sel_step["run"], f"select step must echo {key}"
    # test-slow tier-2 intersection (manifest + run steps must share it)
    for want in ("Generate coverage manifest", "Run slow test suite"):
        step = next(s for s in wf["jobs"]["test-slow"]["steps"]
                    if s.get("name", "").startswith(want))
        run = step["run"]
        assert "needs.changes.outputs.slow_selected" in run, \
            f"test-slow '{want}' must consume slow_selected (tier-2 scoping)"
        assert 'case " $SLOW_SEL " in' in run, \
            f"test-slow '{want}' must intersect with slow_selected"
    # committed matrix rows remain literal file lists (drift-guard pinned)
    rows = wf["jobs"]["test-slow"]["strategy"]["matrix"]["include"]
    assert len(rows) == 2
    from tools.ci_selection import TESTS_DIR
    _slow = set(load_manifest()["slow_files"])
    for row in rows:
        tokens = row["files"].split()
        assert tokens, "test-slow leg row must be a literal file list (#1471)"
        for token in tokens:
            rel = f"{token}.py"
            assert (TESTS_DIR / rel).exists(), \
                f"test-slow leg entry {rel} does not exist under tests/"
            assert rel in _slow, \
                f"test-slow leg entry {rel} is not declared in slow_files"


def test_slow_selected_echo_transform_roundtrips_into_legs():
    """#2159 review P2-1: the changes job's slow_selected echo strips the
    .py suffix and space-joins BARE names; test-slow's tier-2 shape then
    case-matches them against the committed leg rows (matrix.files). This
    pin reproduces the echo transform for every selection shape and asserts
    the round-trip holds — a strip/join format drift (suffix leak, JSON,
    newline) would silently empty BOTH legs to a green gate, and this
    tier-2 partial shape never runs in CI outside a real tier-2 PR."""
    m = load_manifest()
    wf = _load_python_ci()
    legs = set()
    for i in wf["jobs"]["test-slow"]["strategy"]["matrix"]["include"]:
        legs.update(i["files"].split())
    shapes = [
        ([], "push"),
        ([], "schedule"),
        (["docs/README.md"], "pull_request"),
        (["tortoise/sdk.py"], "pull_request"),
        (["tortoise/ep.py"], "pull_request"),
        (["tests/test_w4_why_enrichment.py"], "pull_request"),
        (["mystery-dir/x.py"], "pull_request"),
        (["tools/ci_selection.py"], "pull_request"),  # #2159 P2-3: full
    ]
    for changed, event in shapes:
        r = select(changed, event, m)
        # the echo transform, verbatim from python-ci.yml
        transformed = [
            x[:-3] if x.endswith(".py") else x for x in r["slow_selected"]]
        assert all(".py" not in x for x in transformed), \
            f"suffix leak in echo output ({changed}/{event}): {transformed}"
        assert set(transformed) <= legs, \
            f"slow_selected escapes the committed slow legs ({changed}/{event})"
        assert bool(r["slow_run"]) == bool(transformed), \
            f"slow_run must imply a non-empty leg intersection ({changed}/{event})"


def test_canary_streak_job_consumes_half_b_artifacts_only():
    """Task 9 Step 6 (cycle-5 P1-7/cycle-6 P1-7): the canary-streak job is
    post-merge only (push), needs [test] (matrix fan-in), consumes
    the HALF-B artifact set + the previous streak artifact via the
    classifier, and uploads the new streak. It must never read a
    steps-output value (the classifier's own pin lives in
    tests/test_canary_classify.py)."""
    wf = _load_python_ci()
    job = wf["jobs"]["canary-streak"]
    assert job["needs"] == "test"  # single-need YAML collapses to a string
    assert "always()" in job["if"], \
        "canary-streak must run on RED runs too (implicit success() would skip it" \
        " whenever a test leg fails — the streak would freeze instead of reset)"
    assert "github.event_name == 'push'" in job["if"], \
        "the streak population is post-merge full-matrix only"
    steps = job["steps"]
    dl = next(s for s in steps
              if s.get("name", "").startswith("Download half-b artifacts"))
    assert dl["with"]["name"] == "pytest-log-test-b"
    classify = next(s for s in steps
                    if s.get("name", "").startswith("Classify run"))
    crun = classify["run"]
    assert "tools/testdb_canary_classify.py" in crun
    assert "--junitxml artifacts/junit.xml" in crun
    assert "--manifest artifacts/expected-nodeids.txt" in crun
    assert "--step-wall artifacts/step_wall.txt" in crun
    assert "--divergence-log" in crun
    assert "--producer-marker artifacts/canary-producer.json" in crun
    assert "prev-streak" in crun and "--out config/testdb-canary-streak.json" in crun
    assert "$GITHUB_OUTPUT" not in crun, \
        "the classifier must never consume a steps-output value (artifacts only)"
    up = next(s for s in steps
              if s.get("name", "").startswith("Upload canary streak"))
    assert up["with"]["name"] == "testdb-canary-streak"
    assert up["with"]["path"] == "config/testdb-canary-streak.json"


def _extract_pytest_marker(run_script: str) -> str:
    """Pull the `-m <marker>` filter from a job's pytest run script. The
    docker lanes quote it (`-m 'not track_b and not live'`); the track-b
    job's is bare (`-m track_b`). The launcher's own `python -m pytest`
    module form is never a marker — the unquoted fallback skips 'pytest'."""
    quoted = _re.search(r"-m '([^']+)'", run_script)
    if quoted:
        return quoted.group(1)
    for m in _re.finditer(r"-m\s+([^\s]+)", run_script):
        if m.group(1) != "pytest":
            return m.group(1)
    raise AssertionError(f"no -m marker found in run script:\n{run_script}")


def test_run_marker_matches_manifest_marker():
    """#1787 (PR #1811): the live-probe seam raises the docker lanes' run
    filter to `-m 'not track_b and not live'` while the skip-guard manifest
    steps still carried the old `--marker "not track_b"` — skip-guard's
    contract (tools/skip-guard.py ~L107) requires the manifest's collect-only
    marker to match the run's marker EXACTLY, or the manifest expects nodeids
    the run deselects and every run reds on vanished nodeids. Pin the
    equality for ALL FOUR run jobs (fast/slow/carve-out + track-b) so a
    future edit that changes one side reds at test time instead of CI
    runtime. The track-b job (epic #902 A8/#1058) has the same vanished-
    nodeid class: its manifest collects with `--marker "track_b"` and its
    run filters with the UNQUOTED `-m track_b` — the extraction helper
    handles both quoted and bare forms."""
    wf = _load_python_ci()
    run_prefixes = {
        "test": "Run fast test suite",
        "test-slow": "Run slow test suite",
        "test-carve-out": "Run carve-out suite",
        "test-track-b": "Run Track B tests",
    }
    for job_name, run_prefix in run_prefixes.items():
        steps = wf["jobs"][job_name]["steps"]
        run = next(s for s in steps
                   if s.get("name", "").startswith(run_prefix))
        run_marker = _extract_pytest_marker(run["run"])
        manifest = next(s for s in steps
                        if s.get("name", "").startswith("Generate coverage manifest"))
        manifest_marker = _re.search(r'--marker "([^"]+)"', manifest["run"]).group(1)
        assert run_marker == manifest_marker, (
            f"{job_name}: run filter -m '{run_marker}' must equal the skip-guard "
            f"manifest marker '{manifest_marker}' (the manifest's collect-only "
            "must match the run's deselect set or every run reds on vanished "
            "nodeids — #1787)"
        )


def test_sweep_team_strays_env_gated_iff_uri():
    """#1886 (issue #1884): the journal-blind team_* stray-pass opt-in
    (TORTOISE_TEST_SWEEP_TEAM_STRAYS) is set under the SAME full==true gate
    as the docker URI on BOTH matrix jobs — the pass is safe ONLY on the
    dedicated fresh-per-job container; a future edit that sets it on a
    tier-2 embedded leg (or drops it from a docker leg) reds. Mirrors the
    EXPECT_URI iff-gate pin (cycle-7 P2-1)."""
    wf = _load_python_ci()
    for job in ("test", "test-slow"):
        steps = wf["jobs"][job]["steps"]
        compute = next(s for s in steps
                       if "Compute docker URI" in s.get("name", ""))
        script = compute["run"]
        gate = ('if [ "${{ needs.changes.outputs.full }}" = "true" ]; then')
        assert gate in script, f"{job}: the docker gate must be full==true"
        then_block = script.split("then", 1)[1].split("fi", 1)[0]
        assert 'SWEEP_TEAM_STRAYS="1"' in then_block, \
            f"{job}: opt-in must be set ONLY inside the full==true gate"
        outside_then = script.split("then", 1)[1].split("fi", 1)[1]
        assert 'SWEEP_TEAM_STRAYS="1"' not in outside_then, \
            f"{job}: opt-in must never be set outside the full==true gate"
        assert 'echo "SWEEP_TEAM_STRAYS=$SWEEP_TEAM_STRAYS" >> "$GITHUB_ENV"' \
            in script, f"{job}: opt-in must be exported"
        run = next(s for s in steps
                   if s.get("name", "").startswith(
                       "Run fast test suite" if job == "test"
                       else "Run slow test suite"))
        assert run["env"]["TORTOISE_TEST_SWEEP_TEAM_STRAYS"] \
            == "${{ env.SWEEP_TEAM_STRAYS }}", \
            f"{job}: pytest step must map the opt-in from the job env"


def test_track_b_docker_lane_sets_team_stray_opt_in():
    """#1886: test-track-b is a docker lane on a dedicated fresh-per-job
    container (TORTOISE_DB_URI + EXPECT_URI set) — the team_* stray-pass
    opt-in must be set there too, or the #1686 journal-blind closure is
    silently inert on that lane."""
    wf = _load_python_ci()
    env = wf["jobs"]["test-track-b"].get("env", {})
    assert env.get("TORTOISE_DB_URI", "").endswith("tortoise_test_matrix")
    assert env.get("TORTOISE_TEST_SWEEP_TEAM_STRAYS") == "1", \
        "test-track-b (dedicated docker lane) must set the team_* stray opt-in"


_LEGACY_OPT_IN_VAR = "TORTOISE_TEST_SWEEP_LEGACY"
_TEAM_OPT_IN_VAR = "TORTOISE_TEST_SWEEP_TEAM_STRAYS"


def _env_map(container: object, where: str) -> dict:
    """The ``env`` map of a workflow/job/step, or `{}` when absent.

    A non-mapping container (a malformed workflow shape) is a hard error, not
    a skip: this scanner backs a "the var is set by NO workflow" pin, so an
    unreadable shape must fail closed with a clear message rather than crash
    with an opaque `AttributeError` (or, worse, pass vacuously).
    """
    if not isinstance(container, dict):
        raise AssertionError(
            f"{where}: malformed workflow shape — expected a mapping, got "
            f"{type(container).__name__}"
        )
    env = container.get("env")
    if env is None:
        return {}
    if not isinstance(env, dict):
        raise AssertionError(f"{where}: `env` is not a mapping")
    return env


def _opt_in_sites(wf: dict, label: str, var: str) -> list[str]:
    """Where ``wf`` sets env var ``var`` — structured, never a raw-text scan.

    An ``env`` map key is a real setting at ANY of the three scopes GitHub
    Actions inherits through — workflow-level, job-level, step-level (a
    workflow-level token reaches every job and step, so scanning only the job
    and step maps would leave the pin green while CI armed the opt-in); a
    ``run`` script is inspected only after shell comments are stripped, so a
    YAML or shell comment that merely NAMES the variable is not a hit. Returns
    ``"<label>:<workflow>"`` / ``"<label>:<job>"`` / ``"<label>:<job> step N"``
    labels for assertion messages.
    """
    sites: list[str] = []
    if var in _env_map(wf, f"{label}:<workflow>"):
        sites.append(f"{label}:<workflow>")
    jobs = wf.get("jobs")
    if jobs is None:
        return sites
    if not isinstance(jobs, dict):
        raise AssertionError(f"{label}: `jobs` is not a mapping")
    for job_name, job in jobs.items():
        if var in _env_map(job, f"{label}:{job_name}"):
            sites.append(f"{label}:{job_name}")
        steps = job.get("steps")
        if steps is None:
            continue
        if not isinstance(steps, list):
            raise AssertionError(f"{label}:{job_name}: `steps` is not a list")
        for i, step in enumerate(steps, start=1):
            if var in _env_map(step, f"{label}:{job_name} step {i}"):
                sites.append(f"{label}:{job_name} step {i}")
            script = step.get("run")
            if isinstance(script, str):
                stripped = "\n".join(_strip_shell_comments(line)
                                     for line in script.splitlines())
                if var in stripped:
                    sites.append(f"{label}:{job_name} step {i} (run)")
    return sites


def test_legacy_residue_opt_in_is_never_set_in_ci():
    """#3634 Task 3: TORTOISE_TEST_SWEEP_LEGACY is a MANUAL operator opt-in and
    is set by NO workflow — not just by the one python-ci.yml lane.

    Contrast with the team-stray opt-in pinned just above: that pass is safe on
    a dedicated, fresh-per-job container (nothing accumulates there without it),
    so CI sets it inside the full==true docker gate. The legacy residue cohort
    lives on a LONG-LIVED dev docker whose residue may include a live eval or
    tenant name the next automated session does not own, so CI sets it on no
    lane — a future edit that exports it (any workflow, any job, any gate) reds
    by design.

    SCOPE: EVERY file in `.github/workflows/` (`.yml` and `.yaml`, via
    `_workflow_files`), parsed as YAML. The original python-ci.yml-only text pin
    was too narrow: `post-merge-validation.yml` already sets the SIBLING
    destructive opt-in (`TORTOISE_TEST_SWEEP_TEAM_STRAYS`) and was unscanned, so
    "nowhere in python-ci.yml" was not the claim the docstring made. Analysis is
    structured, not raw text: the variable must not be a workflow/job/step
    `env` key nor appear in a `run` script after comments are stripped, so a
    comment that merely NAMES it does not red.
    """
    wf_dir = Path(__file__).resolve().parents[1] / ".github" / "workflows"
    workflows = _workflow_files(wf_dir)
    assert workflows, "no workflow files found — the scan would pass vacuously"
    import yaml
    parsed = [(p.name, yaml.safe_load(p.read_text()) or {}) for p in workflows]

    # POSITIVE CONTROL — the same scanner must FIND the sibling destructive
    # opt-in that CI deliberately sets; without it, a scanner (or a glob) that
    # found nothing would satisfy this pin vacuously.
    team_sites = [s for label, wf in parsed
                  for s in _opt_in_sites(wf, label, _TEAM_OPT_IN_VAR)]
    assert team_sites, (
        "the scanner did not find TORTOISE_TEST_SWEEP_TEAM_STRAYS in any "
        "workflow, though post-merge-validation.yml sets it — the scan is "
        "vacuous, not clean"
    )

    offenders = [s for label, wf in parsed
                 for s in _opt_in_sites(wf, label, _LEGACY_OPT_IN_VAR)]
    assert not offenders, (
        "the legacy residue opt-in is a manual operator action — never a CI "
        "setting; found in " + ", ".join(offenders)
    )


def test_opt_in_scanner_reads_workflow_level_env():
    """#3634 Task 3 (N1): `_opt_in_sites` must read the WORKFLOW-level `env:`
    map, not only `jobs.<id>.env` / `jobs.<id>.steps[].env`.

    GitHub Actions inherits a workflow-level `env` into every job and step, so
    a top-level `TORTOISE_TEST_SWEEP_LEGACY: "1"` arms the destructive manual
    opt-in on every lane while a jobs-only scan stays green — the exact bypass
    the pin above exists to close. Synthetic, not the real files, so deleting
    the workflow-level branch reds HERE directly.
    """
    wf = {"env": {_LEGACY_OPT_IN_VAR: "1"},
          "jobs": {"test": {"steps": [{"run": "echo hi"}]}}}
    assert _opt_in_sites(wf, "wf", _LEGACY_OPT_IN_VAR) == ["wf:<workflow>"], \
        "the workflow-level `env` map is not scanned"
    # Keyed, not prose: a sibling var is untouched, and a `run` comment that
    # merely names the var is not a setting.
    assert _opt_in_sites(wf, "wf", _TEAM_OPT_IN_VAR) == []
    assert _opt_in_sites(
        {"jobs": {"test": {"steps":
                            [{"run": "true  # " + _LEGACY_OPT_IN_VAR}]}}},
        "wf", _LEGACY_OPT_IN_VAR) == []
    # A malformed shape fails closed and legibly — a clear AssertionError, not
    # an AttributeError from `.get`/`.items` on a non-mapping.
    import pytest as _pytest
    for bad in ({"env": "x"}, {"jobs": []}, {"jobs": {"j": "x"}},
                {"jobs": {"j": {"steps": "x"}}},
                {"jobs": {"j": {"steps": ["x"]}}}):
        with _pytest.raises(AssertionError):
            _opt_in_sites(bad, "wf", _LEGACY_OPT_IN_VAR)


def test_drift_gate_cannot_skip_the_test_matrix():
    """#2656: the manifest drift gate must never be a prerequisite of the test
    matrix.

    It used to be a step inside `changes`, and every other job has
    `needs: changes` — so a one-line manifest drift made GitHub mark the whole
    matrix "skipped" (not failed: never run). Twice in two days (#2868 parity
    files, #2911 price-basis) that hid every test signal behind a bookkeeping
    miss, and the second time it blocked verification of the #2904 fork fix.

    The gate must still BLOCK a merge (python-ci-gate, the required aggregate,
    lists it) — it just must not withhold the tests. This pins the split, and
    pins it against the ways this workflow could silently undo it: its own
    house idioms (`continue-on-error`, `|| true` / `; exit 0`, `if:`) all turn a
    FAILED job into a GREEN required check, which would restore the bug's effect
    without restoring the bug.
    """
    workflow = _load_python_ci()
    jobs = workflow["jobs"]
    _always = ("always()", "${{ always() }}")

    def _needs(name: str) -> list[str]:
        needed = jobs[name].get("needs") or []
        return [needed] if isinstance(needed, str) else list(needed)

    def _code(step: dict) -> str:
        # Comments are not invocations: this change leaves a pointer comment
        # naming the gate, and moving it inside a step must not false-red.
        return "\n".join(line for line in (step.get("run") or "").splitlines()
                         if not line.strip().startswith("#"))

    def runs_integrity(job: str) -> bool:
        # Tool name AND flag, on non-comment lines: an import-form re-nest
        # (`python3 -c "...ci_selection...integrity(m)"`) counts, and a literal
        # `--integrity` re-nest cannot slip past.
        return any("integrity" in _code(s) and "ci_selection" in _code(s)
                   for s in jobs[job].get("steps", []))

    def _defaults_shell(spec: dict) -> str | None:
        return ((spec.get("defaults") or {}).get("run") or {}).get("shell")

    # --- 1. the gate is not inside `changes` ------------------------------
    assert not runs_integrity("changes"), (
        "the drift gate must not run inside `changes`: every other job needs "
        "`changes`, so a drift there SKIPS the whole matrix instead of "
        "failing it (#2656)")

    # --- 2. it is its own unconditional, bounded, unsilenceable job -------
    assert "manifest-integrity" in jobs, \
        "the drift gate must live in its own job (#2656)"
    drift = jobs["manifest-integrity"]
    assert not _needs("manifest-integrity"), (
        "the drift job must have no upstream needs, or an unrelated failure "
        "would skip the check itself")
    assert runs_integrity("manifest-integrity"), \
        "the drift job must actually run the integrity check"
    assert drift.get("if", "always()") in _always, (
        "the drift job must be unconditional (push/PR) — an `if:` "
        "would silently drop drift enforcement on the events it excludes")
    assert not drift.get("continue-on-error"), (
        "the drift job must not be continue-on-error: a failed check would "
        "report success and the required aggregate would go green (#2656)")
    assert not _defaults_shell(workflow) and not _defaults_shell(drift), (
        "a `defaults.run.shell` can swallow the integrity exit code (#2656)")

    integrity_steps = [s for s in drift.get("steps", [])
                       if "integrity" in _code(s)]
    assert integrity_steps, "the drift job must run the integrity check"
    for step in integrity_steps:
        run = step["run"].replace("\\\n", " ")
        tokens = shlex.split(run)
        assert tokens[:2] == ["python3", "tools/ci_selection.py"] \
            and "--integrity" in tokens, (
            "the integrity step must invoke tools/ci_selection.py --integrity "
            f"directly (#2656); got {step.get('run')!r}")
        assert not any(op in run for op in ("||", "&&", ";", "`", "$(")), (
            "no shell operator may follow the integrity check — `|| true` / "
            "`; exit 0` (this workflow's most-used silencing idiom) makes a "
            f"real drift report green (#2656); got {step.get('run')!r}")
        assert not step.get("continue-on-error") and not step.get("shell"), (
            "the integrity STEP must neither be continue-on-error nor override "
            "`shell`: either one lets the job report success while the check "
            "failed (#2656)")
        assert step.get("if", "always()") in _always, (
            "the integrity step must be unconditional: `if: always()` is fine, "
            "any other condition drops drift enforcement on the events it "
            "excludes")
    _timeout = drift.get("timeout-minutes")
    assert isinstance(_timeout, int) and 0 < _timeout <= 15, (
        f"the drift job needs a tight timeout (got {_timeout!r}) — it installs "
        "pyyaml and feeds the required aggregate (#2656)")

    # --- 3. nothing that runs tests may depend on it ----------------------
    # Not just its DIRECT dependents: a helper job that a matrix job needs
    # would re-create the skip transitively (drift fails -> helper skipped ->
    # matrix job skipped).
    assert "manifest-integrity" in (jobs["python-ci-gate"].get("needs") or []), (
        "python-ci-gate is the required status check — it must include the "
        "drift job, or a drift would stop blocking merges (#2656)")
    # What makes a dependent harmful is that it lies on the path to a job that
    # runs tests: skip it and the tests do not run. A job that merely reports
    # the drift result (and that no test job needs) is fine. So intersect the
    # drift job's transitive dependents with the transitive needs-closure of
    # the matrix jobs — which also catches the indirect form (drift job ->
    # helper -> a matrix job), where a direct-only check sees nothing.
    matrix_jobs = set(jobs["python-ci-gate"].get("needs") or []) \
        - {"manifest-integrity"}
    on_the_path, frontier = set(matrix_jobs), list(matrix_jobs)
    while frontier:
        for parent in _needs(frontier.pop()):
            if parent not in on_the_path:
                on_the_path.add(parent)
                frontier.append(parent)
    skipped, frontier = {"manifest-integrity"}, ["manifest-integrity"]
    while frontier:
        current = frontier.pop()
        for name in jobs:
            if current in _needs(name) and name not in skipped:
                skipped.add(name)
                frontier.append(name)
    polluted = sorted((skipped & on_the_path) - {"manifest-integrity"})
    assert not polluted, (
        "no job needed to reach the test matrix may depend on the drift gate, "
        "directly or through a helper: a drift would SKIP it instead of "
        f"failing it (#2656); got {polluted}")

    # --- 4. the aggregate still FAILS, and never swallows it --------------
    gate = jobs["python-ci-gate"]
    assert gate.get("if") in _always, (
        "python-ci-gate must run even when the drift job fails — `if: always()`"
        " is what lets it report the failure (#2656)")
    assert not gate.get("continue-on-error"), (
        "the required aggregate must not be continue-on-error (#2656)")
    gate_steps = gate.get("steps", [])
    assert all(not s.get("continue-on-error") and not s.get("shell")
               for s in gate_steps), (
        "no gate step may be continue-on-error or override `shell` — the "
        "required check would report green on a red drift (#2656)")
    aggregate = next((s for s in gate_steps
                      if "join(needs.*.result" in (s.get("run") or "")), None)
    assert aggregate is not None, (
        "python-ci-gate must aggregate EVERY need's result — narrowing it to "
        "`needs.changes.result` (or `outcome`) silently drops drift "
        "enforcement (#2656)")
    script = aggregate["run"]
    assert "${{ join(needs.*.result, ' ') }}" in script, (
        "python-ci-gate must join `needs.*.result` — reading a subset means a "
        "red drift never reaches the check (#2656)")
    # Defence in depth must not be deletable either: the per-leg rows below cover
    # every declared leg, but the plain sweep is what catches a leg that reaches
    # `needs:` WITHOUT a row (a shape the row-set assertion below forbids, so
    # this is redundancy — deliberately kept and pinned rather than dropped).
    assert re.search(r"grep -qE 'failure\|cancelled'", script), (
        "the aggregate must keep the `failure|cancelled` sweep over "
        "`needs.*.result` as defence in depth (#5219)")

    # Render the GitHub expressions into literal results and actually RUN the
    # aggregate's script: this is what turns "the words are present" into "a
    # failed drift really exits non-zero". (Guarded: the assertion is about the
    # shell logic, which is the thing that has to be right on the runner.)
    if shutil.which("bash"):
        job_names = list(jobs["python-ci-gate"].get("needs") or [])

        # Derive each leg's selector from the WORKFLOW itself, not from a literal
        # list here: a leg whose job-level `if:` reads a selector output may
        # legitimately skip when that output is false; a leg with no diff gate
        # (its `if:` names no selector output) must therefore ALWAYS be SUCCESS.
        # Deriving it makes the rows and the jobs' own `if:`s unable to drift.
        def _selector_of(leg: str) -> str:
            outs = re.findall(r"needs\.changes\.outputs\.(\w+)",
                              str(jobs[leg].get("if") or ""))
            return outs[0] if outs else "-"

        SELECTOR = {leg: _selector_of(leg) for leg in job_names}
        DECLINABLE = {leg: sel for leg, sel in SELECTOR.items() if sel != "-"}
        ALWAYS = [leg for leg, sel in SELECTOR.items() if sel == "-"]
        # Default is "selected": an unexpected `skipped` is then a LOST shard,
        # which is the polarity #5219 is about.
        SELECTED = {out: "true" for out in DECLINABLE.values()}

        # Every leg in `needs:` must have exactly ONE row, and that row's
        # selector must be the output the job's own `if:` reads. Without this a
        # shard added to `needs:` with no row — or with the wrong selector —
        # would be certified green while `skipped` (the `lost-shard` shape).
        rows: dict[str, str] = {}
        for line in script.splitlines():
            m = re.match(
                r"^([\w-]+)\|\$\{\{\s*needs\.[\w-]+\.result\s*\}\}\|(.*)$",
                line.strip())
            if m:
                rows[m.group(1)] = m.group(2).strip()
        assert set(rows) == set(job_names), (
            "every leg in `python-ci-gate.needs` must have exactly one row in "
            "the aggregate's per-leg check; "
            f"rows={sorted(rows)} vs needs={sorted(job_names)}")
        for leg, selector in SELECTOR.items():
            if selector == "-":
                assert rows[leg] == "-", (
                    f"`{leg}` has no diff gate, so its row must use `-` (must "
                    f"always be SUCCESS); got {rows[leg]!r}")
            else:
                found = re.search(r"outputs\.(\w+)", rows[leg])
                assert found and found.group(1) == selector, (
                    f"`{leg}`'s row must gate on `{selector}` — the output its "
                    f"own `if:` reads; got {rows[leg]!r}")

        def _render(results: dict, selected: dict) -> str:
            rendered = script.replace(
                "${{ join(needs.*.result, ' ') }}",
                " ".join(results.get(name, "skipped") for name in job_names))
            # `needs.<job>.result` renders as EMPTY when <job> is absent from
            # `needs:` — GitHub's own semantics, and the reason a leg dropped
            # from the required check's list must fail closed here rather than
            # quietly vanish.
            rendered = re.sub(r"\$\{\{\s*needs\.([\w-]+)\.result\s*\}\}",
                              lambda m: results.get(m.group(1), ""), rendered)
            rendered = re.sub(
                r"\$\{\{\s*needs\.changes\.outputs\.(\w+)\s*\}\}",
                lambda m: selected.get(m.group(1), ""), rendered)
            unrendered = re.findall(r"\$\{\{[^}]*\}\}", rendered)
            assert not unrendered, (
                "this harness must render every GitHub expression the aggregate "
                f"uses, or it proves nothing; unrendered: {unrendered}")
            return rendered

        def _verdict(results: dict, selected: dict | None = None) -> int:
            sel = dict(SELECTED)
            sel.update(selected or {})
            return subprocess.run(["bash", "-c", _render(results, sel)],
                                  capture_output=True).returncode

        green = {name: "success" for name in job_names}
        assert _verdict(green) == 0, (
            "an all-green matrix must pass the required check")
        # EVERY leg, not just the last one: a required check that cannot red on
        # a given leg is a check that does not observe it (#5219).
        for red in ("failure", "cancelled"):
            for leg in job_names:
                bad = dict(green)
                bad[leg] = red
                assert _verdict(bad) == 1, (
                    f"a `{red}` for `{leg}` must FAIL python-ci-gate — a leg "
                    "the required check does not observe is a leg it cannot "
                    "block (#5219)")
        # An always-run leg has NO selector that can decline it, so a `skipped`
        # one is a lost shard. This is the half of "`skipped` is not a pass"
        # that covers the legs the gate cannot see skip: a silently skipped
        # `changes`/`manifest-integrity`/`surface-guard` must red the check.
        assert ALWAYS, "the aggregate must have always-run legs to assert on"
        for leg in ALWAYS:
            lost = dict(green)
            lost[leg] = "skipped"
            assert _verdict(lost) == 1, (
                f"`{leg}` has no diff gate, so a `skipped` {leg} is a lost "
                "shard and must red the required check")
        # A leg the selector DID select reporting `skipped` is a lost shard...
        for leg, selector in DECLINABLE.items():
            lost = dict(green)
            lost[leg] = "skipped"
            assert _verdict(lost) == 1, (
                f"`{leg}` skipped although the selector SELECTED it is a lost "
                "shard — the required check must not certify it (#5219)")
            # ... while one the selector DECLINED may skip: a diff that does not
            # touch the leg must not be blocked by the gate's own shape.
            declined = dict(green)
            declined[leg] = "skipped"
            assert _verdict(declined, {selector: "false"}) == 0, (
                f"`{leg}` skipped because `{selector}` declined it must not "
                "red the required check")
        # A docs-only diff selects nothing at all.
        declined_all = dict(green)
        for leg in DECLINABLE:
            declined_all[leg] = "skipped"
        assert _verdict(declined_all,
                        {sel: "false" for sel in DECLINABLE.values()}) == 0, (
            "an all-declined (docs-only) diff must stay green")
        # And the shape #5219 was actually about: a leg dropped from `needs:`
        # must fail closed instead of vanishing — for EVERY leg, not just one.
        for leg in job_names:
            dropped = dict(green)
            dropped.pop(leg)
            assert _verdict(dropped) == 1, (
                f"`{leg}` REMOVED from `needs:` must fail closed, not "
                "disappear — that is the exact #5219 shape")


def test_required_gate_covers_the_long_legs():
    """#5219: the required check must OBSERVE every shard it certifies.

    `python-ci-gate` is a REQUIRED status check AND the only `merge_condition`
    of the Mergify merge queue, so its `needs:` list IS its whole claim: a leg
    absent from it is a leg the gate cannot block, however red the run is.

    From 2026-09-24T15:25Z (#5017) until #5219 the list omitted `test`,
    `test-slow` and `test-carve-out`, and the hole was not theoretical: on the
    MERGED heads of #4838 (30c5b45) and #4633 (81401cc), `test (a)`, `test (b)`
    and `test-carve-out` were all `completed/failure` while `python-ci-gate`
    was `completed/success`. Across PRs at the time, the only thing separating
    a gate that reddened on a failing `test` from one that passed was WHICH
    VERSION of the workflow the PR head carried — heads with the pre-#5017 list
    failed the gate, heads with the post-#5017 list passed it.

    #5017 removed those legs DELIBERATELY to cut merge-path latency (`test (a)`
    measured 30.0–31.5m against a ~20m main-merge cadence, so heads went BEHIND
    before the merge could land — 0/41 PRs ever CLEAN). #5219 reverses that
    trade on the owner's call: the integrity of the required check over
    merge-path latency. This test pins the reversal so the shards cannot be
    quietly dropped again — and it pins the DIRECT edge, not only the closure:
    a transitive path is the `canary-streak` → `test` shape that satisfies a
    closure-only assertion while nullifying the intent.
    """
    workflow = _load_python_ci()
    jobs = workflow["jobs"]

    def _needs(name: str) -> list[str]:
        n = jobs[name].get("needs") or []
        return [n] if isinstance(n, str) else list(n)

    direct = list(_needs("python-ci-gate"))
    closure, frontier = set(direct), list(direct)
    while frontier:
        for parent in _needs(frontier.pop()):
            if parent not in closure:
                closure.add(parent)
                frontier.append(parent)

    for leg in ("test", "test-slow", "test-carve-out"):
        assert leg in jobs, (
            f"{leg} must exist — the required check must aggregate it, not "
            "replace it")
        assert leg in direct, (
            f"{leg} must be a DIRECT need of `python-ci-gate`. The required "
            "check — and the merge queue, whose only `merge_condition` it is — "
            "must observe the shard it certifies; a gate that goes green while "
            "this leg is red is the #5219 defect")
        assert leg in closure
    assert "manifest-integrity" in closure, (
        "the required aggregate must still include the manifest drift gate, "
        "or a drift stops blocking merges (#2656)")

    # The OTHER half of the dropped-shard defence (#5219). The drift test pins
    # rows -> needs; this pins needs -> the workflow's own universe of PR-lane
    # jobs. Without it, deleting a leg from `needs:` AND its row is a
    # self-consistent edit that passes every test while the shard sits outside
    # the required check — and that pair is the NATURAL edit, because dropping
    # the entry alone leaves an orphan row, which IS caught, so the author
    # deletes both.
    #
    # A push-only job is exempt by DERIVATION, not by name: on a pull request it
    # reports only `skipped`, so it carries no PR-lane signal.
    push_only = {name for name, spec in jobs.items()
                 if "github.event_name" in str(spec.get("if") or "")}
    must_aggregate = set(jobs) - set(direct) - {"python-ci-gate"} - push_only
    assert not must_aggregate, (
        "every job in this workflow that reports on the PR lane must be a "
        "`python-ci-gate` need — otherwise its failure cannot block a merge, "
        f"which is the #5219 defect. Missing: {sorted(must_aggregate)}")

    # `leg in jobs` alone only proves the leg is DEFINED. The contract leans on
    # the legs still EXECUTING on the PR lane — the `--admin` rail's lane parity
    # requires the PR lane to have run every shard main's lane runs — so pin
    # that too: both triggers must remain, and no leg may be silenced.
    triggers = workflow.get("on", workflow.get(True)) or {}
    assert "push" in triggers and "pull_request" in triggers, (
        "the long legs must still run on push (post-merge detection on main) "
        "AND on pull requests (advisory pre-merge) — dropping either trigger "
        "silently deletes a leg the disclosure depends on")
    for leg in ("test", "test-slow", "test-carve-out"):
        spec = jobs[leg]
        assert spec.get("steps"), (
            f"{leg} must still have steps — an empty job would 'run' nothing")
        assert not spec.get("continue-on-error"), (
            f"{leg} must not be continue-on-error — its failure must stay "
            "visible, or the post-merge detection is silent")
        assert spec.get("if") not in ("false", False), (
            f"{leg} must not be unconditionally disabled")
        # NOT push-only: the workflow's own comment cites the `--admin` rail's
        # lane parity as the reason these legs are not skipped on PRs, so a
        # per-leg `github.event_name` filter is the exact regression to refuse.
        # And job-level `continue-on-error` is not enough — a silenced STEP
        # inside the job produces the same missing signal.
        assert "github.event_name" not in str(spec.get("if") or ""), (
            f"{leg} must not carry an event filter (e.g. push-only): a "
            "`skipped` shard is not coverage, and the `--admin` rail's lane "
            "parity (`ADMIN_MERGE_LANE_PARITY=require`) refuses a merge when "
            "the PR lane did not execute a shard main's lane executes "
            "(#4263/#4457)")
        pytest_steps = [s for s in spec.get("steps", [])
                        if "pytest" in (s.get("run") or "")]
        assert pytest_steps, f"{leg} must still RUN pytest"
        silenced = [s.get("name") for s in pytest_steps
                    if s.get("continue-on-error")]
        assert not silenced, (
            f"{leg}'s pytest step(s) must not be continue-on-error — that "
            "silences the signal the disclosure says is still produced: "
            f"{silenced}")


# ── #2938: surface audit (report-only) ───────────────────────────────────
# The audit must resolve what the rejected mechanical derivation could not:
# package-level imports, `tortoise/api.py` (absent from SOURCE_PATTERNS),
# helper/fixture indirection, string path refs — and it must stay a REPORT
# (never mutate the manifest, never change select()/--integrity).

_AUDIT_SOURCES = {
    "tortoise/hosted_api.py": "",
    "tortoise/api.py": "",
    "tortoise/ranking.py": "",
    "tortoise/sdk.py": "",
    "tortoise/ep.py": "",
    "tortoise/exceptions.py": "",
    "battery/cli.py": "",
}


def _audit_repo(tmp_path: Path, extra: dict[str, str]) -> Path:
    """A synthetic repo: the shared source tree plus `extra` files."""
    for rel, body in {**_AUDIT_SOURCES, **extra}.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    return tmp_path


def _audit(manifest: dict, repo: Path) -> dict:
    return surface_audit(manifest, repo=repo, tests_dir=repo / "tests")


def _audit_manifest(**surfaces: list[str]) -> dict:
    """A manifest carrying every surface the real one has (empty unless
    overridden) so assertions can address any surface."""
    base: dict[str, list[str]] = {
        s: [] for s in ("api", "battery", "classify", "core", "ep",
                        "eval", "onboarding", "sdk")}
    base.update(surfaces)
    return {"surfaces": base, "tier1": []}


def test_surface_audit_reports_unregistered_surface_pin_as_addition(tmp_path):
    # requirement: a file importing a surface's source but not registered
    # under it is an addition candidate, with the import as evidence
    repo = _audit_repo(tmp_path, {
        "tests/test_imports_api.py": "from tortoise.hosted_api import app\n"})
    report = _audit(_audit_manifest(battery=["test_imports_api.py"]), repo)
    api = {e["file"]: e for e in report["surfaces"]["api"]["addition"]}
    assert "test_imports_api.py" in api
    assert api["test_imports_api.py"]["evidence"] == ["tortoise/hosted_api.py"]
    # ... and the same file is a battery removal candidate (pins nothing battery)
    assert [e["file"] for e in report["surfaces"]["battery"]["removal"]] \
        == ["test_imports_api.py"]


def test_surface_audit_reports_member_pinning_nothing_as_removal(tmp_path):
    repo = _audit_repo(tmp_path, {
        "tests/test_stray_api.py": "from battery.cli import main\n"})
    report = _audit(_audit_manifest(api=["test_stray_api.py"]), repo)
    removal = report["surfaces"]["api"]["removal"]
    assert [e["file"] for e in removal] == ["test_stray_api.py"]
    assert removal[0]["pins"] == {"battery": ["battery/cli.py"]}
    # the reverse direction is reported too: not registered under battery
    assert [e["file"] for e in report["surfaces"]["battery"]["addition"]] \
        == ["test_stray_api.py"]


def test_surface_audit_resolves_package_level_import(tmp_path):
    # rule (a): `from tortoise import X` -> tortoise/X.py (or X/__init__.py);
    # this is the gap that made 28 onboarding files read as "pins nothing"
    repo = _audit_repo(tmp_path, {
        "tortoise/onboarding/__init__.py": "",
        "tests/test_pkg_import.py": "from tortoise import onboarding\n"})
    report = _audit(_audit_manifest(api=["test_pkg_import.py"]), repo)
    ep_add = {e["file"]: e for e in report["surfaces"]["ep"]["addition"]}
    assert "test_pkg_import.py" not in ep_add  # sanity: not the wrong surface
    additions = {e["file"]: e for e in report["surfaces"]["onboarding"]["addition"]}
    assert "test_pkg_import.py" in additions
    assert "tortoise/onboarding/__init__.py" in additions["test_pkg_import.py"]["evidence"]
    # `tortoise` alone (the bare package root) is never a pin
    assert report["surfaces"]["api"]["addition"] == []


def test_surface_audit_excludes_shared_modules(tmp_path):
    # rule: SHARED_MODULES force the full matrix, so importing one is NOT a
    # pin and must not surface as an addition/uncovered mismatch
    repo = _audit_repo(tmp_path, {
        "tests/test_shared.py":
            "from tortoise.sdk import TortoiseSDK\n"
            "from tortoise.exceptions import TortoiseError\n"})
    report = _audit(_audit_manifest(sdk=["test_shared.py"]), repo)
    for surface in report["surfaces"].values():
        assert surface["addition"] == []
    assert all(r["path"] not in ("tortoise/sdk.py", "tortoise/exceptions.py")
               for r in report["uncovered"])
    assert all(r["path"] not in ("tortoise/sdk.py", "tortoise/exceptions.py")
               for r in report["coverage_gaps"])
    removal = report["surfaces"]["sdk"]["removal"]
    assert [e["file"] for e in removal] == ["test_shared.py"]
    assert set(removal[0]["shared"]) \
        >= {"tortoise/sdk.py", "tortoise/exceptions.py"}


def test_surface_audit_catches_string_path_reference(tmp_path):
    # rule (c): a test may shell out to / read a path instead of importing it
    repo = _audit_repo(tmp_path, {
        "tests/test_string_ref.py":
            "import subprocess\n"
            "\n"
            "def test_script():\n"
            "    subprocess.run(['python', 'battery/cli.py'])\n"})
    report = _audit(_audit_manifest(core=["test_string_ref.py"]), repo)
    additions = {e["file"]: e for e in report["surfaces"]["battery"]["addition"]}
    assert "test_string_ref.py" in additions
    assert additions["test_string_ref.py"]["evidence"] == ['"battery/cli.py"']


def test_surface_audit_follows_tests_helper(tmp_path):
    # rule (d): a test reaching a surface only through a tests/ helper must
    # still count, with the chain visible in the evidence
    repo = _audit_repo(tmp_path, {
        "tests/_helper.py": "from tortoise.hosted_api import app\n",
        "tests/test_uses_helper.py": "from tests._helper import app\n"})
    report = _audit(_audit_manifest(battery=["test_uses_helper.py"]), repo)
    additions = {e["file"]: e for e in report["surfaces"]["api"]["addition"]}
    assert "test_uses_helper.py" in additions
    assert additions["test_uses_helper.py"]["evidence"] \
        == ["tortoise/hosted_api.py (via tests/_helper.py)"]
    # the helper module itself is not a test file, so it is never audited
    assert all(e["file"] != "_helper.py"
               for s in report["surfaces"].values() for e in s["addition"])


def test_surface_audit_reports_uncovered_surface_named_source(tmp_path):
    # rule (b): a surface-named source absent from that surface's
    # SOURCE_PATTERNS entry is SAID, not silently binned as core. #2938 mapped
    # the real instance (`tortoise/api.py`), so this test uses a synthetic
    # unmapped one (`tortoise/eval.py`) to keep the rule covered.
    repo = _audit_repo(tmp_path, {
        "tortoise/eval.py": "",
        "tests/test_eval_mod.py": "from tortoise.eval import X\n"})
    report = _audit(_audit_manifest(eval=["test_eval_mod.py"]), repo)
    gaps = report["coverage_gaps"]
    assert any(g["path"] == "tortoise/eval.py" and g["surface"] == "eval"
               for g in gaps), gaps
    assert "tortoise/eval.py" not in [r["path"] for r in report["uncovered"]]
    removal = report["surfaces"]["eval"]["removal"][0]
    assert removal["gap"] == ["tortoise/eval.py"]


def test_surface_audit_maps_tortoise_api_source_as_api_pin(tmp_path):
    # #2938 item 1: `tortoise/api.py` is mapped to `api` now — a file importing
    # it is an api pin, not an uncovered path and not a coverage gap.
    repo = _audit_repo(tmp_path, {
        "tests/test_imports_api.py": "from tortoise.api import EventAPI\n"})
    report = _audit(_audit_manifest(api=["test_imports_api.py"]), repo)
    assert "tortoise/api.py" not in [g["path"] for g in report["coverage_gaps"]]
    assert "tortoise/api.py" not in [r["path"] for r in report["uncovered"]]


def test_tortoise_api_change_selects_api_and_core():
    # #2938 item 1: mapping `tortoise/api.py` to `api` closes the coverage gap,
    # but its pinning tests are registered across surfaces — CORE_ALSO keeps
    # `core` in the selection so the core-registered importers of EventAPI
    # (test_extractor, test_projection via the slow leg, …) still run. An
    # api-only mapping would silently drop them — the exact regression #2938
    # exists to prevent.
    r = _sel(["tortoise/api.py"])
    assert r["full"] is False
    assert r["surfaces"] == ["api", "core"]
    selected = set(r["test_files"]) | set(r["slow_selected"])
    assert "test_api.py" in selected, "api-registered pinner must run"
    assert "test_extractor.py" in selected, "core-registered pinner must run"
    assert "test_projection.py" in selected, "core slow-leg pinner must run"


def test_tortoise_oauth_change_selects_api_and_core():
    # #3036: `tortoise/oauth.py` is the hosted OAuth implementation. Its pinning
    # tests are `api`-registered (test_oauth_mcp.py, test_oauth_token_fault.py,
    # test_3036_oauth_retention.py, test_attribution_actor.py,
    # test_user_identity_authority.py) and one is api+core
    # (test_control_plane_offload_3498.py). Before #3036 mapped it, an
    # oauth.py-only change fell through to `core` and silently skipped every
    # api pinner — the #2938/#3154/#4367 silent-drop class, on the very file a
    # retention or token-flow fix must change. CORE_ALSO keeps the core half.
    r = _sel(["tortoise/oauth.py"])
    assert r["full"] is False
    assert r["surfaces"] == ["api", "core"]
    selected = set(r["test_files"]) | set(r["slow_selected"])
    assert "test_3036_oauth_retention.py" in selected, "the sweep suite must run"
    assert "test_oauth_mcp.py" in selected, "api-registered pinner must run"
    assert "test_control_plane_offload_3498.py" in selected, "api+core pinner must run"


def test_surface_audit_skips_removal_for_unmapped_surfaces(tmp_path):
    # classify/core have no SOURCE_PATTERNS entry: "pins nothing from it" is
    # undefined, so the audit must not propose emptying classify
    repo = _audit_repo(tmp_path, {
        "tests/test_classify_x.py": "import os\n",
        "tests/test_api_nothing.py": "import os\n"})
    report = _audit(_audit_manifest(classify=["test_classify_x.py"],
                                    core=["test_classify_x.py"],
                                    api=["test_api_nothing.py"]), repo)
    for surface in ("classify", "core"):
        assert report["surfaces"][surface]["source_mapped"] is False
        assert report["surfaces"][surface]["removal"] == []
        assert report["surfaces"][surface]["addition"] == []
    # non-vacuity: a source-mapped surface in the SAME repo still reports
    assert [e["file"] for e in report["surfaces"]["api"]["removal"]] \
        == ["test_api_nothing.py"]


def test_surface_audit_reports_no_surface_and_duplicates(tmp_path):
    repo = _audit_repo(tmp_path, {
        "tests/test_dup.py": "import os\n",
        "tests/test_unregistered.py": "import os\n"})
    report = _audit(_audit_manifest(core=["test_dup.py", "test_dup.py"]), repo)
    assert report["no_surface"] == ["test_unregistered.py"]
    assert report["duplicates"] == {"core": ["test_dup.py"]}


def test_surface_audit_is_deterministic_and_non_mutating(tmp_path):
    repo = _audit_repo(tmp_path, {
        "tests/test_a.py": "from tortoise.hosted_api import app\n",
        "tests/test_b.py": "from battery.cli import main\n"})
    manifest = _audit_manifest(api=["test_b.py"], battery=["test_a.py"])
    before = {s: list(f) for s, f in manifest["surfaces"].items()}
    report = _audit(manifest, repo)
    # non-vacuity: the audit actually found the two cross-surface additions
    assert [e["file"] for e in report["surfaces"]["api"]["addition"]] \
        == ["test_a.py"]
    assert [e["file"] for e in report["surfaces"]["battery"]["addition"]] \
        == ["test_b.py"]
    first = render_surface_audit(report)
    second = render_surface_audit(_audit(manifest, repo))
    assert first == second
    assert manifest["surfaces"] == before, "the audit must not mutate the manifest"


def test_surface_audit_coverage_gap_names_only_gap_surface_members(tmp_path):
    # P1-1: only files this change does not select fail to run when the
    # unmapped source changes. A core-registered pinner still runs (via core),
    # so naming it under "never run" overstated the blast radius.
    # P3: assert on the DERIVED data (gap["files"]/["victims"]), not the
    # rendered sentence, so a legitimate wording change cannot break this.
    repo = _audit_repo(tmp_path, {
        "tortoise/eval.py": "",
        "tests/test_eval_registered.py": "from tortoise.eval import X\n",
        "tests/test_core_registered.py": "from tortoise.eval import X\n"})
    manifest = _audit_manifest(eval=["test_eval_registered.py"],
                               core=["test_core_registered.py"])
    report = _audit(manifest, repo)
    gap = next(g for g in report["coverage_gaps"]
               if g["path"] == "tortoise/eval.py")
    # both files are STRONG pinners (they import it); registration differs
    assert set(gap["files"]) == {"test_eval_registered.py",
                                 "test_core_registered.py"}
    assert gap["files"]["test_eval_registered.py"] == ["eval"]
    assert gap["files"]["test_core_registered.py"] == ["core"]
    assert gap["registered"] == ["core", "eval"]
    # `select(['tortoise/eval.py'])` selects `core`, so only the eval-registered
    # pinner is a victim; the core-registered one runs.
    assert gap["selected_surfaces"] == ["core"]
    assert gap["victims"] == ["test_eval_registered.py"]
    assert gap["runs"] == ["test_core_registered.py"]
    # ... and the rendered line agrees with the data
    never_line = render_surface_audit(report).split(
        "never run on a change to tortoise/eval.py:")[1].split("\n")[0]
    assert "test_eval_registered.py" in never_line
    assert "test_core_registered.py" not in never_line, (
        "a core-registered pinner runs via core and must not be in the "
        "\"never run\" list")


def test_surface_audit_coverage_gap_counts_unselected_surface_as_victim(tmp_path):
    # P2: `select(['tortoise/eval.py'], 'pull_request', manifest)` falls back
    # to `core` — it does NOT select `ep`, so an ep-registered pinner never
    # runs. The previous renderer asserted the non-gap pinners "run via
    # core/ep", which was false for the ep one.
    repo = _audit_repo(tmp_path, {
        "tortoise/eval.py": "",
        "tests/test_eval_registered.py": "from tortoise.eval import X\n",
        "tests/test_core_registered.py": "from tortoise.eval import X\n",
        "tests/test_ep_registered.py": "from tortoise.eval import X\n"})
    manifest = _audit_manifest(eval=["test_eval_registered.py"],
                               core=["test_core_registered.py"],
                               ep=["test_ep_registered.py"])
    report = _audit(manifest, repo)
    gap = next(g for g in report["coverage_gaps"]
               if g["path"] == "tortoise/eval.py")
    assert set(gap["files"]) == {"test_eval_registered.py",
                                 "test_core_registered.py",
                                 "test_ep_registered.py"}
    assert gap["files"]["test_ep_registered.py"] == ["ep"]
    assert gap["selected_surfaces"] == ["core"]
    assert gap["victims"] == ["test_ep_registered.py",
                              "test_eval_registered.py"]
    assert gap["runs"] == ["test_core_registered.py"]
    never_line = render_surface_audit(report).split(
        "never run on a change to tortoise/eval.py:")[1].split("\n")[0]
    assert "test_ep_registered.py" in never_line, (
        "an ep-registered pinner is not selected by a change that yields core")
    assert "test_core_registered.py" not in never_line


def test_surface_audit_ignores_function_local_helper_import(tmp_path):
    # P1 (this fix): a helper module is IMPORTED, not called, so an import
    # nested in one of its function bodies never executes and must not become
    # a strong addition. The previous BFS followed `ast.walk`, fabricating 25
    # battery additions from one call-time import in
    # tools/longmem_eval/report.py::build_methodology.
    repo = _audit_repo(tmp_path, {
        "battery/parity/runner.py": "",
        "tests/_deferred.py":
            "def build():\n"
            "    from battery.parity.runner import run\n"
            "    return run\n",
        "tests/test_via_deferred.py": "from tests._deferred import build\n"})
    report = _audit(_audit_manifest(core=["test_via_deferred.py"]), repo)
    assert report["surfaces"]["battery"]["addition"] == [], (
        "a function-local import in a helper never runs on import")
    assert "test_via_deferred.py" not in {
        e["file"] for s in report["surfaces"].values()
        for e in s["addition"] if e["strong"]}


def test_surface_audit_honors_module_level_helper_import(tmp_path):
    # complement of the test above: a MODULE-LEVEL import in a helper DOES
    # execute when the helper is imported, so it stays a strong pin.
    repo = _audit_repo(tmp_path, {
        "tests/_eager.py": "from battery.cli import main\n",
        "tests/test_via_eager.py": "from tests._eager import main\n"})
    report = _audit(_audit_manifest(core=["test_via_eager.py"]), repo)
    additions = {e["file"]: e for e in report["surfaces"]["battery"]["addition"]}
    assert additions["test_via_eager.py"]["evidence"] == [
        "battery/cli.py (via tests/_eager.py)"]
    assert additions["test_via_eager.py"]["strong"] is True


def test_surface_audit_honors_root_function_local_import(tmp_path):
    # the ROOT test file's function bodies DO run when the test runs, so its
    # own call-time imports stay strong pins — only HELPERS are pruned.
    repo = _audit_repo(tmp_path, {
        "tests/test_deferred_own.py":
            "def test_x():\n"
            "    from battery.cli import main\n"
            "    assert main\n"})
    report = _audit(_audit_manifest(core=["test_deferred_own.py"]), repo)
    additions = {e["file"]: e
                 for e in report["surfaces"]["battery"]["addition"]}
    assert additions["test_deferred_own.py"]["evidence"] == ["battery/cli.py"]
    assert additions["test_deferred_own.py"]["strong"] is True


def test_surface_audit_normalises_scalar_surface_values(tmp_path):
    # P3: a hand-edited scalar (`api: 3`, `api: {...}`, `api: "x.py"`) must
    # not raise or silently become a character list; it normalises to empty.
    repo = _audit_repo(tmp_path, {
        "tests/test_scalar.py": "from tortoise.api import EventAPI\n"})
    for bad in (3, {"a": 1}, "test_scalar.py"):
        manifest = _audit_manifest(core=["test_scalar.py"])
        manifest["surfaces"]["api"] = bad
        report = _audit(manifest, repo)  # must not raise
        assert report["surfaces"]["api"]["members"] == []
        assert report["surfaces"]["api"]["removal"] == []
        render_surface_audit(report)  # must not raise


def test_surface_audit_string_only_pin_is_weak_and_labelled(tmp_path):
    # P1-2: a path STRING is fixture data, not an import. It stays visible
    # (labelled) but must not make the file a strong pinnner, or the "never
    # run" claim inflates (test_ci_selection.py's own corpus).
    repo = _audit_repo(tmp_path, {
        "tortoise/eval.py": "",
        "tests/test_fixture_data.py":
            "PATHS = ['tortoise/eval.py']\n"
            "def test_x(): assert PATHS\n",
        "tests/test_fixture_elsewhere.py":
            "PATHS = ['battery/cli.py']\n"
            "def test_y(): assert PATHS\n"})
    report = _audit(_audit_manifest(eval=["test_fixture_data.py"],
                                    core=["test_fixture_elsewhere.py"]), repo)
    gap = next(g for g in report["coverage_gaps"]
               if g["path"] == "tortoise/eval.py")
    assert gap["files"] == {}, "a string-only reference is not a strong pinnner"
    assert list(gap["weak_files"]) == ["test_fixture_data.py"]
    out = render_surface_audit(report)
    assert '"tortoise/eval.py" (string, not an import)' in out, \
        "the human must still see the string pin, labelled"
    assert "1 string-only pinnner(s), weak" in out
    # an UNREGISTERED string-only reference is a weak addition, not candidate
    weak = {e["file"]: e for e in
            report["surfaces"]["battery"]["addition"] if not e["strong"]}
    assert weak["test_fixture_elsewhere.py"]["evidence"] == ['"battery/cli.py"']
    assert report["surfaces"]["battery"]["addition"] and all(
        not e["strong"] for e in report["surfaces"]["battery"]["addition"])
    assert ("string-only references — weak evidence, not counted as pins"
            in out)
    # ... and a strong (import) pin in the SAME repo still claims the gap
    repo2 = _audit_repo(tmp_path / "strong", {
        "tortoise/eval.py": "",
        "tests/test_imports_eval.py": "from tortoise.eval import X\n"})
    report2 = _audit(_audit_manifest(eval=["test_imports_eval.py"]), repo2)
    gap2 = next(g for g in report2["coverage_gaps"]
                if g["path"] == "tortoise/eval.py")
    assert list(gap2["files"]) == ["test_imports_eval.py"]


def test_surface_audit_credits_pin_reached_through_repo_root_helper(tmp_path):
    # P2: the BFS used to follow only tests/ helpers, so a test reaching a
    # surface through tools/ read as "pins nothing" -> false removal candidate
    # (the real report called test_temporal_constraint.py an sdk removal while
    # tools/longmem_eval/retrieve.py imports tortoise.retrieval).
    repo = _audit_repo(tmp_path, {
        "tortoise/retrieval.py": "",
        "tools/reach.py": "from tortoise.retrieval import search\n",
        "tests/test_via_tool.py": "from tools.reach import search\n",
        "tests/test_control.py": "import os\n"})
    # attribution: registered elsewhere, the sdk pin is an addition candidate
    # and the evidence names the helper chain
    additions = {e["file"]: e for e in
                 _audit(_audit_manifest(battery=["test_via_tool.py"]), repo)
                 ["surfaces"]["sdk"]["addition"]}
    assert additions["test_via_tool.py"]["evidence"] == [
        "tortoise/retrieval.py (via tools/reach.py)"]
    # ... and the false removal is gone: only the genuinely pin-less file stays
    report = _audit(_audit_manifest(sdk=["test_via_tool.py",
                                         "test_control.py"]), repo)
    assert [e["file"] for e in report["surfaces"]["sdk"]["removal"]] \
        == ["test_control.py"], "the helper-reached sdk pin must suppress it"


def test_surface_audit_removal_candidate_without_surface_pin_warns(tmp_path):
    # P3: every removal candidate has NO pin for its own surface by definition,
    # so the "don't act" cue belongs on all of them — shared-only members
    # (`shared: tortoise/sdk.py`) used to render as bare removals.
    repo = _audit_repo(tmp_path, {
        "tests/test_shared_only.py": "from tortoise.sdk import TortoiseSDK\n",
        "tests/test_no_refs.py": "import os\n"})
    report = _audit(_audit_manifest(sdk=["test_shared_only.py",
                                         "test_no_refs.py"]), repo)
    assert [e["file"] for e in report["surfaces"]["sdk"]["removal"]] \
        == ["test_no_refs.py", "test_shared_only.py"]
    out = render_surface_audit(report)
    assert ("test_shared_only.py <- (shared: tortoise/sdk.py)  "
            "⚠ no `sdk` pin resolved") in out
    assert ("test_no_refs.py <- (no source references at all)  "
            "⚠ no `sdk` pin resolved") in out


def test_surface_audit_tolerates_null_surface_value(tmp_path):
    # P3: a curated manifest can carry `null` for a surface; the audit must
    # traceback-proof it the way integrity() does with `files or ()`.
    repo = _audit_repo(tmp_path, {
        "tests/test_null_surface.py": "import os\n"})
    manifest = _audit_manifest(api=None, core=["test_null_surface.py"])
    report = _audit(manifest, repo)  # must not raise TypeError
    assert report["surfaces"]["api"]["members"] == []
    assert report["surfaces"]["api"]["removal"] == []
    assert report["surfaces"]["api"]["addition"] == []
    assert report["no_surface"] == []
    assert report["duplicates"] == {}
    # the renderer must survive it too
    assert "api" in render_surface_audit(report)


def test_real_manifest_has_no_duplicate_entries():
    # #3381: two files were each registered twice — one entry added by two
    # different PRs fixing the same drift independently. A same-surface
    # duplicate is invisible to select() (surfaces are unioned) and reported
    # non-fatally by `--integrity` and `--surface-audit` — never as a gate
    # failure — so pin its absence here where CI will actually see it.
    # Call the production detector rather than reimplementing it, so this pin
    # cannot drift from the real semantics (e.g. cross-surface dual
    # registration is deliberate and must stay allowed).
    dupes = duplicate_entries(load_manifest())
    assert dupes == [], f"duplicate manifest entries: {dupes}"


def test_halves_guard_reports_single_sided_pack_without_crashing():
    # #3407 review P2: the branch written to CATCH a zero-weight half died with
    # ZeroDivisionError while formatting its own diagnosis — `hi / lo` was
    # evaluated inside the f-string after `lo <= 0` had short-circuited the
    # comparison. A single-sided pack is reachable (a 1-file pool, or an
    # all-zero measured map), and this is the only check that catches it:
    # `leg_coverage_issues()` and `fast_files_absent_from_halves()` both pass
    # when one half is empty.
    from tools.ci_selection import workflow_halves_issues
    m = {"surfaces": {"core": ["test_only.py"]}, "tier1": [], "slow_files": [],
         "durations": {"test_only.py": 5.0}}
    issues = workflow_halves_issues(m, {"a": {"test_only.py"}, "b": set()})
    assert any("duration-imbalanced" in i for i in issues), issues


def test_halves_guard_reports_all_zero_map_without_crashing():
    from tools.ci_selection import workflow_halves_issues
    files = ["test_a.py", "test_b.py", "test_c.py"]
    m = {"surfaces": {"core": files}, "tier1": [], "slow_files": [],
         "durations": {f: 0.0 for f in files}}
    issues = workflow_halves_issues(m, {"a": set(files), "b": set()})
    assert any("duration-imbalanced" in i for i in issues), issues


def test_duration_issues_flags_non_numeric_value():
    # #3407 review P2: the guards iterated KEYS only, so a hand-edit typo in the
    # now-505-line map passed `--integrity` silently and then crashed
    # `push_legs` with a TypeError inside `split_fast_gate`'s sort key.
    from tools.ci_selection import duration_issues
    m = {"surfaces": {"core": ["test_crypto.py"]}, "tier1": [], "slow_files": [],
         "durations": {"test_crypto.py": "fast"}}
    issues = duration_issues(m)
    assert any("not numeric" in i for i in issues), issues


def test_integrity_chain_names_bad_duration_instead_of_crashing():
    # #3407 review P1 (cycle 2): `duration_issues` alone is NOT the gate. The
    # real `--integrity` chain evaluates `leg_coverage_issues()` -> `push_legs()`
    # -> `split_fast_gate()`, whose sort key negates the weight — so a
    # non-numeric value raised TypeError inside the packer BEFORE the check
    # that names it had run, and the gate tracebacked instead of diagnosing.
    # This test runs the CLI's actual chain, on a real manifest with one real
    # fast-pool key poisoned, which the isolated helper test cannot see.
    from tools.ci_selection import (
        duration_coverage_issues,
        duration_issues,
        fast_pool,
        integrity,
        leg_coverage_issues,
        load_manifest,
    )
    # cycle 3 added an int beyond float range (math.isfinite raises
    # OverflowError) and a negative duration (impossible data, exited 0).
    for bad in (None, "fast", float("nan"), 10 ** 400, -5.0):
        m = load_manifest()
        key = fast_pool(m)[0]
        m = dict(m)
        m["durations"] = dict(m.get("durations") or {})
        m["durations"][key] = bad
        # Must not raise.
        problems = (integrity(m) + slow_file_issues(m) + duration_issues(m)
                    + leg_coverage_issues(m) + duration_coverage_issues(m))
        named = [p for p in problems if "durations value" in p]
        assert named, f"a bad duration value ({bad!r}) was not named: {problems}"


def test_split_fast_gate_cannot_crash_on_a_malformed_duration():
    # The packer must degrade to the default, never raise — belt and braces for
    # the ordering fix above (any consumer, any order).
    from tools.ci_selection import split_fast_gate
    files = ["tests/test_a.py", "tests/test_b.py"]
    for bad in (None, "fast", float("nan"), float("inf"), True):
        a, b = split_fast_gate(files, {"test_a.py": bad})
        assert len(a) + len(b) == 2, (bad, a, b)


def test_integrity_cli_exits_nonzero_for_a_huge_int_and_a_negative(tmp_path, monkeypatch):
    # #3407 review cycle 3: the isolated helper tests could not see an EXIT
    # CODE, and the two residual classes were both silent-green failures. This
    # drives the real CLI entry point (`main()`, which reads sys.argv and the
    # module-level MANIFEST) over a genuinely poisoned manifest.
    import sys as _sys

    import yaml

    from tools import ci_selection as cs
    for bad in (10 ** 400, -5.0, float("nan")):
        m = cs.load_manifest()
        m = dict(m)
        m["durations"] = dict(m.get("durations") or {})
        m["durations"][cs.fast_pool(m)[0]] = bad
        poisoned = tmp_path / "poisoned-ci-surfaces.yml"
        poisoned.write_text(yaml.safe_dump(m))
        monkeypatch.setattr(cs, "MANIFEST", poisoned)
        monkeypatch.setattr(_sys, "argv", ["ci_selection.py", "--integrity"])
        assert cs.main() != 0, f"a bad duration value ({bad!r}) exited 0"


def test_null_or_non_mapping_durations_reports_instead_of_tracebacking(tmp_path, monkeypatch):
    # #3407 review cycle 4 (pre-existing): `duration_issues` and `--split` read
    # `.get("durations", {})`, which returns a present-but-NULL `durations:` key
    # as None — the empty-map state `duration_coverage_issues` documents as
    # "NOT a failure". A raw TypeError traceback is not a diagnosis: it makes
    # the gate look broken rather than making it say what is wrong.
    import sys as _sys

    import yaml

    from tools import ci_selection as cs
    # `None` is NOT in this list: null/absent is the documented empty-map
    # PASS, and is asserted below.
    for bad in (0, "foo", [1.0]):
        m = dict(cs.load_manifest())
        m["durations"] = bad
        poisoned = tmp_path / "bad-durations.yml"
        poisoned.write_text(yaml.safe_dump(m))
        monkeypatch.setattr(cs, "MANIFEST", poisoned)
        monkeypatch.setattr(_sys, "argv", ["ci_selection.py", "--integrity"])
        assert cs.main() != 0, f"durations={bad!r} exited 0"
    # The documented empty-map contracts must still PASS, or the guard above
    # has simply turned one wrong answer into another.
    for empty in ({}, None):
        m = dict(cs.load_manifest())
        if empty is None:
            m.pop("durations", None)
        else:
            m["durations"] = {}
        ok = tmp_path / "empty-durations.yml"
        ok.write_text(yaml.safe_dump(m))
        monkeypatch.setattr(cs, "MANIFEST", ok)
        monkeypatch.setattr(_sys, "argv", ["ci_selection.py", "--integrity"])
        assert cs.main() == 0, f"an empty durations map ({empty!r}) was treated as a failure"


# ── #4378: changed-set diffs must disable rename detection ──────────────
#
# The predicate below is per COMMAND, not per line: `python-ci.yml`'s "Tiered
# selection" step carries TWO `git diff` invocations on ONE line joined by `||`,
# so a whole-line substring check is satisfied by the flag on either side —
# removing it from the second command reintroduces #4378 while the check still
# passes. These helpers are module-level so the parser can be exercised directly
# on synthetic shell text.

# The shell joiners that separate one command from another. The DOUBLED forms
# are listed before the single characters so a regex alternation matches `||` as
# one separator rather than two adjacent `|`s, and `&&` likewise. The single
# `|`/`&` are included because a pipeline and a backgrounded command split two
# invocations exactly as `;` does: without them `git diff --name-only A |\
# git diff --name-status B` was ONE parsed command whose ownership accounted for
# the second diff's flag, so a flagless `--name-status` read clean (#4378).
_SHELL_SEPARATORS = re.compile(r"\|\||&&|;|\||&")

# The spellings that make a `git diff` a changed-set computation. `--name-only` is
# the one `changed_set_git_diff_commands` parses; the other two are listed so the
# raw-line cross-check (`unparsed_changed_set_diff_lines`) reports a future site
# that uses them as UNPARSED — a loud failure — instead of letting it pass.
_CHANGED_SET_DIFF_FLAGS = ("--name-only", "--name-status", "--diff-filter")


def _strip_shell_comments_and_quotes(line: str) -> str:
    """Remove shell comments and the CONTENT of ordinary quoted strings.

    `#` starts a comment only OUTSIDE quotes (and, per shell, only at the start
    of a word). The content of an ordinary quoted string is removed so a
    separator, `#`, or flag written inside quotes can neither split a command nor
    satisfy/defeat the pin; a space is left in its place so adjacent tokens do
    not merge. Single-quoted text is a literal in every context and is always
    removed.

    A command substitution is the one exception: `$( … )` runs a real command, so
    its contents are EXTRACTED and returned as commands of their own — including
    when the substitution sits inside double quotes. The substitution becomes a
    space in the surrounding text, so it can neither hide nor lend a flag to the
    command around it. Without this, `CHANGED="$(git diff --name-only … )"` — the
    quoted `="$( … )"` house style this repo writes throughout its workflows —
    was invisible to the pin in BOTH directions (not counted, not flagged), while
    only the unquoted `CHANGED=$( … )` form was seen.

    Only `$( … )` is unwrapped: a backtick substitution's body is left in place
    (though `_shell_tokens` still tokenises it, so a backtick diff IS parsed),
    and this function does not resolve a diff reached through a variable,
    function or `eval`.
    """
    out: list[list[str]] = [[]]
    # Quote context to restore at each `$( … )`'s `)`; `None` = opened unquoted.
    saved_quotes: list[str | None] = []
    extracted: list[str] = []
    quote: str | None = None
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote == "'":
            if ch == "'":
                quote = None
                out[-1].append(" ")
            i += 1
            continue
        if quote == '"':
            if ch == "\\" and i + 1 < n:
                i += 2  # an escaped character inside a string is string content
                continue
            if ch == '"':
                quote = None
                out[-1].append(" ")
                i += 1
                continue
            if ch == "$" and line.startswith("$(", i):
                out[-1].append(" ")
                saved_quotes.append(quote)
                quote = None
                out.append([])
                i += 2
                continue
            i += 1
            continue
        # Unquoted, or inside a `$( … )` substitution — both are shell code.
        if ch == "\\" and i + 1 < n:
            out[-1].append(ch)
            out[-1].append(line[i + 1])
            i += 2
            continue
        if ch in ("'", '"'):
            quote = ch
            out[-1].append(" ")
            i += 1
            continue
        if ch == "#" and (i == 0 or line[i - 1].isspace()):
            break
        if ch == "$" and line.startswith("$(", i):
            out[-1].append(" ")
            saved_quotes.append(None)
            quote = None
            out.append([])
            i += 2
            continue
        if ch == ")" and len(out) > 1:
            extracted.append("".join(out.pop()))
            quote = saved_quotes.pop()
            i += 1
            continue
        out[-1].append(ch)
        i += 1
    while len(out) > 1:  # an unterminated substitution still contributes code
        extracted.append("".join(out.pop()))
    stripped = "".join(out[0])
    if extracted:
        stripped += " ; " + " ; ".join(extracted)
    return stripped


def _strip_shell_comments(line: str) -> str:
    """Drop a trailing shell comment, KEEPING quoted text and substitutions.

    `#` starts a comment only OUTSIDE quotes and at the start of a word. Unlike
    `_strip_shell_comments_and_quotes`, everything else survives: the raw-line
    cross-check (`unparsed_changed_set_diff_lines`) must be able to see a
    changed-set spelling the parser's quote stripping would hide (an `eval` or
    `sh -c` whose argument names a `git diff`), so it over-reports rather than
    misses.
    """
    quote: str | None = None
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if quote == "'":
            if ch == "'":
                quote = None
            i += 1
            continue
        if quote == '"':
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue
        if ch == "#" and (i == 0 or line[i - 1].isspace()):
            return line[:i]
        i += 1
    return line


def _logical_shell_line_spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, joined) for every backslash-joined logical line.

    Comments/quotes are stripped per physical line first. `start`/`end` are
    1-based physical line numbers (equal for a one-line invocation); the parser
    attributes a command to `start`, so the span is what the raw-line cross-check
    uses to decide whether a physical line was already accounted for.
    """
    spans: list[tuple[int, int, str]] = []
    parts: list[str] = []
    start: int | None = None
    end = 0
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = _strip_shell_comments_and_quotes(raw)
        if start is None:
            start = lineno
        end = lineno
        stripped = line.rstrip()
        if stripped.endswith("\\") and not stripped.endswith("\\\\"):
            parts.append(stripped[:-1])
            continue
        parts.append(line)
        spans.append((start, end, " ".join(parts)))
        parts = []
        start = None
    if start is not None:
        spans.append((start, end, " ".join(parts)))
    return spans


def _logical_shell_lines(text: str) -> list[tuple[int, str]]:
    """Join backslash continuations into one logical line.

    Comments/quotes are stripped per physical line first; the 1-based number of
    the line the logical line STARTED on is preserved for file:line reporting.
    """
    return [(start, joined) for start, _end, joined in _logical_shell_line_spans(text)]


def iter_shell_commands(text: str) -> list[tuple[int, str]]:
    """Split shell text into individual commands on every shell joiner.

    The joiners are `||`, `&&`, `;`, `|` and `&` (`_SHELL_SEPARATORS`), the
    doubled forms matched first so each is one separator; a newline is a joiner
    by construction, since a logical line ends there unless backslash-continued.
    Comments/quotes are stripped and backslash continuations joined first, so a
    multi-line invocation is read as ONE command. Returns (line, command).
    """
    commands: list[tuple[int, str]] = []
    for lineno, logical in _logical_shell_lines(text):
        for part in _SHELL_SEPARATORS.split(logical):
            part = part.strip()
            if part:
                commands.append((lineno, part))
    return commands


def _shell_tokens(command: str) -> list[str]:
    # Shell grouping characters bind adjacent words to each other: `$(git` is one
    # shlex token, and `HEAD)` is another. Treat them as whitespace so `git`,
    # `diff` and the flags are recognised as standalone tokens either way.
    detached = re.sub(r"[()$`]", " ", command)
    try:
        return shlex.split(detached, comments=False, posix=True)
    except ValueError:  # unbalanced quote the stripper did not catch
        return detached.split()


def changed_set_git_diff_commands(text: str) -> list[tuple[int, str]]:
    """The changed-set diff invocations in `text`.

    A command qualifies when its tokens contain `git` AND `diff` AND
    `--name-only`. Token-based, not `"git diff" in command`, so
    `git -c <cfg> diff ...` is recognised, as is a diff living in a command
    substitution — both the unquoted `CHANGED=$(git diff ...)` form and the
    quoted `CHANGED="$(git diff ...)"` house style.
    """
    found: list[tuple[int, str]] = []
    for lineno, command in iter_shell_commands(text):
        tokens = _shell_tokens(command)
        if "git" in tokens and "diff" in tokens and "--name-only" in tokens:
            found.append((lineno, command))
    return found


def _no_renames_is_honoured(tokens: list[str]) -> bool:
    """True iff every `--no-renames` token precedes the FIRST `--` separator.

    `git diff` treats every token after `--` as a PATHSPEC, so a `--no-renames`
    written there is swallowed: the command runs, rc=0, no warning, and rename
    detection stays ON. Token membership alone therefore accepts an argument git
    ignores — position is the whole rule. With no `--` there is no pathspec region
    to fall behind, so the flag is honoured wherever it sits.
    """
    if "--no-renames" not in tokens:
        return False
    if "--" not in tokens:
        return True
    first_separator = tokens.index("--")
    return all(
        index < first_separator
        for index, token in enumerate(tokens)
        if token == "--no-renames"
    )


def changed_set_git_diff_offenders(text: str) -> list[tuple[int, str]]:
    """Changed-set diff commands that do NOT actually disable rename detection.

    An offender is a parsed command where `--no-renames` is ABSENT, or present
    only after the first `--` (where git ignores it).
    """
    return [
        (lineno, command)
        for lineno, command in changed_set_git_diff_commands(text)
        if not _no_renames_is_honoured(_shell_tokens(command))
    ]


def _parsed_changed_set_flag_ownership(text: str) -> dict[int, dict[str, int]]:
    """Per logical-span START line, how many parsed commands own each flag.

    A parsed command is attributed to the START line of the logical span it was
    read from (the same attribution `iter_shell_commands` uses when reporting),
    and OWNS every `_CHANGED_SET_DIFF_FLAGS` spelling its tokens carry. Counting
    occurrences rather than recording a set is what makes the cross-check fail
    closed on co-location: if a span's raw text carries a flag N times while its
    parsed commands account for fewer than N, the surplus is unaccounted for.
    """
    ownership: dict[int, dict[str, int]] = {}
    for lineno, command in changed_set_git_diff_commands(text):
        tokens = _shell_tokens(command)
        owned = ownership.setdefault(
            lineno, {flag: 0 for flag in _CHANGED_SET_DIFF_FLAGS}
        )
        for flag in _CHANGED_SET_DIFF_FLAGS:
            if flag in tokens:
                owned[flag] += 1
    return ownership


def unparsed_changed_set_diff_lines(text: str) -> list[tuple[int, str]]:
    """Comment-stripped raw lines that carry a changed-set spelling the parser MISSED.

    This is the pin's fail-closed net. `changed_set_git_diff_commands` recognises
    exactly one spelling (`git diff … --name-only`); any other way of writing a
    changed-set diff (`--name-status`, `--diff-filter`, an `eval`/`sh -c` whose
    quoted argument names the command, a spelling added after this file was
    written) yields no parsed command, so before this check it passed silently —
    the failure mode every review cycle of #4378 kept re-closing one spelling at a
    time. Here a logical (backslash-joined) line that contains `git` AND `diff`
    AND a `_CHANGED_SET_DIFF_FLAGS` spelling the parsed commands do not account
    for is returned for the caller to fail on.

    FAIL-CLOSED ON CO-LOCATION, not merely on absence. The net used to mark a
    whole line "covered" as soon as ANY parsed command started on it, so a second,
    unparsed changed-set diff sharing that line (`… || git diff --name-status …`
    beside a parsed `--name-only`, or an unparsed `eval "git diff --name-only …"`
    beside one) was suppressed by its neighbour's parse. It now COUNTS
    OCCURRENCES per flag spelling: a span whose raw text carries a flag N times
    while its parsed commands account for fewer than N is reported. The splitter's
    joiners (`||`, `&&`, `;`, `|`, `&`, plus the newline that ends a logical span)
    each make the two invocations separate parsed candidates, so the second's flag
    cannot be absorbed by the first's parse. A same-line
    `--name-only` beside a parsed `--name-only` is still accounted for only when
    both invocations are parsed; a spelling the parser cannot tokenise on any
    logical line is never accounted for and is always reported.

    `git`/`diff`/flag are matched as SUBSTRINGS of the comment-stripped raw line,
    not through the parser's tokeniser, and the presence check spans a backslash
    continuation: it is deliberately coarser than the parser, so a spelling the
    parser mis-splits — or one whose `git`/`diff` and flag land on different
    physical lines of ONE continued command — is still reported. It can only
    SUPPRESS a report for a spelling the parser genuinely accounted for; it can
    never rescue an offender. Stated limits — do not read more into it: a spelling
    whose raw text never carries all three substrings on one logical line (a diff
    reached through a bare variable or alias, a flag assembled from string
    fragments on SEPARATE statements, a command name bound at runtime) is still
    invisible, as is any file outside the caller's scan. No changed-set `git diff`
    in `.github/workflows/` reaches the pin through any such route today.
    """
    ownership = _parsed_changed_set_flag_ownership(text)
    physical = text.splitlines()
    unparsed: list[tuple[int, str]] = []
    for start, end, _joined in _logical_shell_line_spans(text):
        raw_span = " ".join(
            _strip_shell_comments(physical[i - 1]) for i in range(start, end + 1)
        )
        if "git" not in raw_span or "diff" not in raw_span:
            continue
        owned = ownership.get(start, {})
        if any(
            raw_span.count(flag) > owned.get(flag, 0)
            for flag in _CHANGED_SET_DIFF_FLAGS
        ):
            unparsed.append((start, physical[start - 1].strip()))
    return unparsed


def _workflow_files(wf_dir: Path) -> list[Path]:
    """Every workflow file in `wf_dir` — BOTH `.yml` and `.yaml`.

    `.yaml` is a valid GitHub Actions workflow extension, so the scan's claim to
    read EVERY workflow in `.github/workflows/` is only true while this glob
    keeps both. The repo has no `.yaml` workflow today, which is exactly why the
    `.yaml` half needs its own test: without one, deleting the glob left the
    changed-set tests green. Handing every caller (the pin and the synthetic
    test) through this single function is what makes the mutation visible.
    """
    return sorted({*wf_dir.glob("*.yml"), *wf_dir.glob("*.yaml")})


def _scan_changed_set_diffs(workflows: list[Path]) -> tuple[int, list[str], list[str]]:
    """Scan `workflows` for changed-set diffs; return (checked, offenders, unparsed).

    `checked` counts PARSED changed-set commands (the non-vacuity floor's input);
    the two lists hold `file:line: raw` reports ready to embed in an assertion
    message. Kept separate from the pin so `test_every_changed_set_diff_scan_covers_yaml_workflows`
    can run the SAME scan over a tmp dir and freeze the `.yaml` glob.
    """
    offenders: list[str] = []
    unparsed: list[str] = []
    checked = 0
    for wf in workflows:
        text = wf.read_text()
        checked += len(changed_set_git_diff_commands(text))
        raw_lines = text.splitlines()
        for lineno, command in changed_set_git_diff_offenders(text):
            raw = raw_lines[lineno - 1].strip() if lineno <= len(raw_lines) else command
            offenders.append(f"{wf.name}:{lineno}: {raw}")
        for lineno, raw in unparsed_changed_set_diff_lines(text):
            unparsed.append(f"{wf.name}:{lineno}: {raw}")
    return checked, offenders, unparsed


def test_every_changed_set_diff_scan_covers_yaml_workflows(tmp_path):
    """#4378 FIX 2: the pin scans `*.yaml` workflows too, not only `*.yml`.

    The pin reads `.github/workflows/` for BOTH extensions, but this repo has no
    `.yaml` workflow, so removing the `.yaml` glob left every changed-set test
    green — the `.yaml` claim was unfrozen. This writes a synthetic `.yaml`
    workflow (a flagless changed-set diff, and a flagged `.yml` sibling) into a
    tmp dir and runs the SAME scan the pin runs: the `.yaml` offender must be
    reported. No repo fixture is added; nothing here touches `.github/`.
    """
    wf_dir = tmp_path / ".github" / "workflows"
    wf_dir.mkdir(parents=True)
    (wf_dir / "synthetic.yaml").write_text(
        "steps:\n"
        "  - run: |\n"
        '      git diff --name-only "$BASE" HEAD\n'
    )
    (wf_dir / "synthetic.yml").write_text(
        'steps:\n  - run: git diff --no-renames --name-only "$BASE" HEAD\n'
    )
    workflows = _workflow_files(wf_dir)
    assert {p.name for p in workflows} == {"synthetic.yml", "synthetic.yaml"}, workflows
    checked, offenders, unparsed = _scan_changed_set_diffs(workflows)
    assert checked == 2, (checked, offenders, unparsed)
    assert len(offenders) == 1, (offenders, unparsed)
    assert "synthetic.yaml" in offenders[0], offenders
    assert "--no-renames" not in offenders[0], offenders
    assert unparsed == [], unparsed


def test_changed_set_parser_reads_commands_not_lines():
    """#4378 FIX 1: two `git diff`s on one `||` line are TWO commands.

    `python-ci.yml`'s "Tiered selection" step carries
    `... || git diff --no-renames --name-only ...`.
    A whole-line substring predicate is satisfied by the flag on EITHER side, so
    removing it from the second command reintroduced #4378 undetected. The
    synthetic inputs below freeze that regression.
    """
    one_flagged = (
        'CHANGED=$(git diff --no-renames --name-only "$BASE...HEAD" '
        '|| git diff --name-only "$BASE" HEAD)\n'
    )
    commands = changed_set_git_diff_commands(one_flagged)
    assert len(commands) == 2, commands
    offenders = changed_set_git_diff_offenders(one_flagged)
    assert len(offenders) == 1, offenders
    assert offenders[0][0] == 1
    assert "--name-only" in offenders[0][1]
    # both flagged -> no offender
    both_flagged = (
        'CHANGED=$(git diff --no-renames --name-only "$BASE...HEAD" '
        '|| git diff --no-renames --name-only "$BASE" HEAD)\n'
    )
    assert changed_set_git_diff_offenders(both_flagged) == []


def test_changed_set_parser_splits_on_every_documented_separator():
    """#4378: `&&`, `;`, `|` and `&` split commands exactly like `||`.

    The parser's contract is that a line is split on every shell joiner — `||`,
    `&&`, `;`, `|` and `&` (`_SHELL_SEPARATORS`, doubled forms first). Each is
    exercised independently here, so dropping one from that set fails this test:
    narrowing it to `||` alone left every parser test green (proven by runtime
    monkeypatch), and a flagless second command behind any other joiner was
    silently accepted (`commands=1, offenders=0`).
    """
    flagged = 'git diff --no-renames --name-only "$BASE" HEAD'
    flagless = 'git diff --name-only "$BASE" HEAD'
    for sep in ("&&", ";", "|", "&"):
        text = f"{flagged} {sep} {flagless}\n"
        commands = changed_set_git_diff_commands(text)
        assert len(commands) == 2, (sep, commands)
        offenders = changed_set_git_diff_offenders(text)
        assert len(offenders) == 1, (sep, offenders)
        assert offenders[0][0] == 1, (sep, offenders)
        assert "--name-only" in offenders[0][1], (sep, offenders)
        assert "--no-renames" not in offenders[0][1], (sep, offenders)


def test_changed_set_parser_separates_bare_pipe_and_amp_merge():
    """#4378 FIX 1: a bare `|`/`&` keeps two invocations SEPARATE, not merged.

    A pipeline (`|`) and a backgrounded command (`&`) each split two commands
    just as `;` does, but the separator set used to carry only the doubled forms
    (`||`, `&&`) — so this shape was read as ONE command:

        git diff --no-renames --name-only A | git diff --name-status B

    The merged command carried `--no-renames`, its token list contained BOTH
    spellings, and the ownership count therefore matched the raw span exactly:
    `offenders=[]` (the one parsed command is flagged) AND `unparsed=[]` (the
    second diff's `--name-status` was accounted for by the merge) — a flagless
    changed-set diff that read CLEAN. With the bare joiners in the set the two
    halves are separate commands, the second is unparsed, and its flag is a
    surplus occurrence the cross-check reports. Both halves of the fix are
    frozen here: the split itself, and the cross-check catching the merged case.
    """
    for sep in ("|", "&"):
        # The literal bypass: the first half is flagged, the second is not, and
        # the second's spelling is one only the cross-check can see.
        merged = (
            'git diff --no-renames --name-only "$BASE" HEAD '
            f'{sep} git diff --name-status "$BASE" HEAD\n'
        )
        assert len(changed_set_git_diff_commands(merged)) == 1, (sep, merged)
        assert changed_set_git_diff_offenders(merged) == [], (sep, merged)
        unparsed = unparsed_changed_set_diff_lines(merged)
        assert [lineno for lineno, _ in unparsed] == [1], (sep, unparsed)
        assert "--name-status" in unparsed[0][1], (sep, unparsed)

        # A flagless `--name-only` second half becomes its own OFFENDER.
        two_name_only = (
            'git diff --no-renames --name-only "$BASE" HEAD '
            f'{sep} git diff --name-only "$BASE" HEAD\n'
        )
        assert len(changed_set_git_diff_commands(two_name_only)) == 2, (
            sep,
            changed_set_git_diff_commands(two_name_only),
        )
        offenders = changed_set_git_diff_offenders(two_name_only)
        assert len(offenders) == 1, (sep, offenders)
        assert "--no-renames" not in offenders[0][1], (sep, offenders)


def test_changed_set_parser_ignores_comments():
    """A comment can neither satisfy the floor nor rescue an offender."""
    # A comment mentioning the flag is NOT a parsed command.
    assert changed_set_git_diff_commands(
        "# git diff --no-renames --name-only x\n") == []
    # A trailing comment carrying the flag must not rescue the real command.
    rescued = 'git diff --name-only "$BASE" HEAD  # --no-renames is written here\n'
    assert len(changed_set_git_diff_commands(rescued)) == 1
    assert len(changed_set_git_diff_offenders(rescued)) == 1
    # A comment mentioning a bare diff is likewise inert.
    assert changed_set_git_diff_commands("# git diff --name-only x\n") == []


def test_changed_set_parser_joins_backslash_continuations():
    """A multi-line invocation is ONE command, not a silent skip."""
    missing = 'git diff --name-only \\\n  "$BASE" HEAD\n'
    commands = changed_set_git_diff_commands(missing)
    assert len(commands) == 1, commands
    assert commands[0][0] == 1
    assert len(changed_set_git_diff_offenders(missing)) == 1
    ok = 'git diff --no-renames \\\n  --name-only "$BASE" HEAD\n'
    assert len(changed_set_git_diff_commands(ok)) == 1
    assert changed_set_git_diff_offenders(ok) == []


def test_changed_set_parser_accepts_git_config_and_substitution():
    """`git -c <cfg> diff` and a `$( )` prefix still invoke git diff."""
    text = 'git -c diff.renames=true diff --name-only "$BASE" HEAD\n'
    assert len(changed_set_git_diff_commands(text)) == 1
    assert len(changed_set_git_diff_offenders(text)) == 1
    subst = 'CHANGED=$(git diff --name-only "$BASE" HEAD)\n'
    assert len(changed_set_git_diff_commands(subst)) == 1
    assert len(changed_set_git_diff_offenders(subst)) == 1


def test_changed_set_parser_sees_quoted_command_substitution():
    """#4378 FIX 1: `CHANGED="$(git diff … )"` IS a parsed changed-set diff.

    The stripper used to discard a double-quoted span wholesale, so a changed-set
    diff written in the repo's own `="$( … )"` house style (29 uses today:
    `="$(printf …)"`, `="$(gh api …)"`, `="$(unzip …)"` …) was invisible to the
    pin in BOTH directions — never counted, never flagged. These inputs freeze
    both directions, for the one-command and the two-command (`||`) shapes.
    """
    # WITHOUT the flag: one command, and it MUST be an offender.
    flagless = (
        'CHANGED="$(git diff --name-only "$BASE...HEAD" 2>/dev/null || true)"\n'
    )
    commands = changed_set_git_diff_commands(flagless)
    assert len(commands) == 1, commands
    offenders = changed_set_git_diff_offenders(flagless)
    assert len(offenders) == 1, offenders
    assert offenders[0][0] == 1, offenders

    # WITH the flag: still counted, and NOT an offender.
    flagged = (
        'CHANGED="$(git diff --no-renames --name-only "$BASE...HEAD" '
        '2>/dev/null || true)"\n'
    )
    assert len(changed_set_git_diff_commands(flagged)) == 1
    assert changed_set_git_diff_offenders(flagged) == [], changed_set_git_diff_offenders(
        flagged
    )

    # The two-command `||` shape inside the quoted substitution: the flag on the
    # FIRST command must not rescue the flagless SECOND one.
    one_flagged = (
        'CHANGED="$(git diff --no-renames --name-only "$BASE...HEAD" '
        '|| git diff --name-only "$BASE" HEAD)"\n'
    )
    assert len(changed_set_git_diff_commands(one_flagged)) == 2
    offenders = changed_set_git_diff_offenders(one_flagged)
    assert len(offenders) == 1, offenders
    assert "--no-renames" not in offenders[0][1]

    both_flagged = (
        'CHANGED="$(git diff --no-renames --name-only "$BASE...HEAD" '
        '|| git diff --no-renames --name-only "$BASE" HEAD)"\n'
    )
    assert changed_set_git_diff_offenders(both_flagged) == []

    # A `$( … )` inside SINGLE quotes is a literal, not code.
    literal = "CMD='$(git diff --name-only \"$BASE\" HEAD)'\n"
    assert changed_set_git_diff_commands(literal) == []


def test_changed_set_parser_quoted_flag_cannot_rescue_a_site():
    """Unwrapping a quoted `$( … )` must not leak into ordinary quoted text.

    Only the CONTENT of `$( … )` is code; the content of a plain double-quoted
    string is not. A `--no-renames` that a command merely *mentions* — in a
    trailing comment, in a quoted string, or as a single-quoted literal — cannot
    turn a flagless changed-set diff green. Discarding the quoted token is the
    conservative direction: the pin fails closed, never open.
    """
    decoys = (
        'git diff --name-only "$BASE" HEAD  # add --no-renames\n',
        'git diff --name-only "$BASE" HEAD "--no-renames not passed"\n',
        "git diff --name-only \"$BASE\" HEAD '--no-renames'\n",
        # A substitution's inner flag belongs to the substitution, not to the
        # diff command it is an argument of — it must not rescue that diff.
        'git diff --name-only "$BASE" HEAD "$(true --no-renames)"\n',
    )
    for text in decoys:
        commands = changed_set_git_diff_commands(text)
        assert len(commands) == 1, (text, commands)
        offenders = changed_set_git_diff_offenders(text)
        assert len(offenders) == 1, (text, offenders)


def test_changed_set_parser_requires_flag_before_pathspec_separator():
    """#4378 FIX 1: `--no-renames` after the first `--` is ignored by git.

    `git diff` treats everything after `--` as a pathspec, so a flag written
    there is swallowed (rc=0, no warning, renames still detected). The old
    predicate was token membership only, so it accepted the flag wherever it sat.
    These inputs freeze the positional rule: before `--` clean, after `--` an
    offender, and one on each side still an offender (the trailing one governs
    nothing, so git's effective flag is not what the pin claims).
    """
    after = 'git diff --name-only "$BASE" HEAD -- \'*.md\' --no-renames\n'
    commands = changed_set_git_diff_commands(after)
    assert len(commands) == 1, commands
    offenders = changed_set_git_diff_offenders(after)
    assert len(offenders) == 1, offenders
    assert "--no-renames" in offenders[0][1]

    before = 'git diff --no-renames --name-only "$BASE" HEAD -- \'*.md\'\n'
    assert len(changed_set_git_diff_commands(before)) == 1
    assert changed_set_git_diff_offenders(before) == []

    both = 'git diff --no-renames --name-only "$BASE" HEAD -- \'*.md\' --no-renames\n'
    assert len(changed_set_git_diff_offenders(both)) == 1, (
        changed_set_git_diff_offenders(both)
    )


def test_changed_set_parser_cross_check_fails_closed_on_unparsed_spelling():
    """#4378 FIX 2: a changed-set diff the parser does not recognise FAILS.

    Each review cycle closed one spelling at a time; this closes the CLASS. A
    synthetic workflow body carries an unrecognised `--name-status`, an
    unrecognised `--diff-filter`, a comment (inert), and a recognised
    `--name-only` site (covered) — only the first two are reported.
    """
    synthetic = (
        "steps:\n"
        "  - run: |\n"
        "      # a comment mentioning git diff --name-only is inert\n"
        '      git diff --name-status "$BASE" HEAD\n'
        '      CHANGED=$(git diff --diff-filter=ACMR "$BASE" HEAD)\n'
        '      FILES=$(git diff --name-only "$BASE" HEAD)\n'
    )
    unparsed = unparsed_changed_set_diff_lines(synthetic)
    assert [lineno for lineno, _ in unparsed] == [4, 5], unparsed
    # The recognised site is still parsed, so the net suppresses it (no false
    # positive) rather than reporting a line the pin already checked.
    assert changed_set_git_diff_commands(synthetic), (
        "the recognised --name-only site must still parse"
    )
    assert not any(lineno == 6 for lineno, _ in unparsed), unparsed

    # A quoted or `eval`-wrapped spelling the parser cannot unwrap is caught too.
    quoted = 'eval "git diff --name-only \\"$BASE\\" HEAD"\n'
    assert unparsed_changed_set_diff_lines(quoted), quoted
    # `--no-index --quiet` content comparisons carry no changed-set flag: their
    # existence must not trip the net (ci-timing.yml has no --name-only at all).
    no_index = (
        "if git diff --no-index --quiet docs/a generated/a \\\n"
        "   && git diff --no-index --quiet docs/b generated/b; then\n"
    )
    assert unparsed_changed_set_diff_lines(no_index) == []


def test_changed_set_parser_cross_check_fails_closed_on_colocation():
    """#4378 FIX 1: an unparsed changed-set diff cannot hide beside a parsed one.

    `unparsed_changed_set_diff_lines` used to mark a whole line "covered" as soon
    as ONE parsed command started on it, so this shape — `python-ci.yml:188`'s two
    changed-set diffs on one line, with the second written in a spelling the
    parser does not recognise (`--name-status`, no flag) — passed the pin clean
    (`offenders=[]`, `unparsed=[]`) while the second command computed a changed set
    with rename detection ON. The cross-check must count the flag spellings, not
    the lines, so the surplus is reported.
    """
    colocated = (
        'CHANGED=$(git diff --no-renames --name-only "$BASE...HEAD" 2>/dev/null '
        '|| git diff --name-status "$BASE" HEAD)\n'
    )
    # The FIRST command is parsed and correctly flagged, so the offender rule is
    # silent — the cross-check is the ONLY thing that can catch the second one.
    assert len(changed_set_git_diff_commands(colocated)) == 1, colocated
    assert changed_set_git_diff_offenders(colocated) == [], colocated
    unparsed = unparsed_changed_set_diff_lines(colocated)
    assert [lineno for lineno, _ in unparsed] == [1], unparsed
    assert "--name-status" in unparsed[0][1], unparsed

    # The SAME spelling hidden by the parser's quote stripping (`eval "…"`) beside
    # a parsed `--name-only` is caught too: the raw span counts two `--name-only`,
    # the parser accounts for one. A set-based rule would suppress this; counting
    # occurrences is what makes it fail closed.
    eval_colocated = (
        'git diff --no-renames --name-only "$BASE" HEAD; '
        'eval "git diff --name-only \\"$BASE\\" HEAD"\n'
    )
    assert len(changed_set_git_diff_commands(eval_colocated)) == 1, eval_colocated
    unparsed = unparsed_changed_set_diff_lines(eval_colocated)
    assert [lineno for lineno, _ in unparsed] == [1], unparsed

    # Neither half recognised: still reported.
    both_unrecognised = (
        'CHANGED=$(git diff --name-status "$BASE...HEAD" '
        '|| git diff --name-status "$BASE" HEAD)\n'
    )
    assert unparsed_changed_set_diff_lines(both_unrecognised), both_unrecognised

    # Control: BOTH halves parsed and flagged is clean — no false positive.
    both_parsed = (
        'CHANGED=$(git diff --no-renames --name-only "$BASE...HEAD" 2>/dev/null '
        '|| git diff --no-renames --name-only "$BASE" HEAD)\n'
    )
    assert unparsed_changed_set_diff_lines(both_parsed) == [], (
        unparsed_changed_set_diff_lines(both_parsed)
    )


def test_changed_set_parser_cross_check_sees_continuation_split_spelling():
    """#4378 FIX 2: `git`/`diff` and the flag on different physical lines of
    ONE backslash-continued command still fail.

    A `--name-status` continuation is invisible to a purely per-physical-line
    check (the first line carries no flag; the flag line carries no `git`), so the
    net joins a backslash continuation before looking for the flag — the same join
    the parser already performs. Freeze it so the join cannot be dropped.
    """
    continued = 'git diff \\\n  --name-status "$BASE" HEAD\n'
    unparsed = unparsed_changed_set_diff_lines(continued)
    assert [lineno for lineno, _ in unparsed] == [1], unparsed
    # The report is attributed to the line the logical span STARTED on, and the
    # flag lives on the continuation line — proving the join happened.
    assert "--name-status" in continued.splitlines()[1], continued
    # The recognised, correctly-flagged continuation is still clean.
    ok = 'git diff --no-renames \\\n  --name-only "$BASE" HEAD\n'
    assert unparsed_changed_set_diff_lines(ok) == [], (
        unparsed_changed_set_diff_lines(ok)
    )


def test_changed_set_parser_cross_check_reports_unrecognised_spelling_in_every_shape():
    """#4378 FIX 3: every declared in-scope spelling of an UNRECOGNISED changed-set
    diff is reported by the cross-check.

    The parser recognises only `--name-only`; `--name-status`/`--diff-filter` are
    caught as unrecognised whatever shell shape they are written in — bare, inside
    `$( … )`, inside a double-quoted `"$( … )"`, joined by `||`/`&&`/`;`/`|`/`&`,
    split across a backslash continuation, or prefixed by `git -c <cfg>`. Each
    entry is a shape whose mutation (e.g. dropping the `$(` unwrap, a per-line
    check, dropping a bare joiner) would let it pass silently, so this is the
    coverage table's executable form.
    """
    shapes = {
        "bare": 'git diff --name-status "$BASE" HEAD',
        "substitution": 'CHANGED=$(git diff --diff-filter=ACMR "$BASE" HEAD)',
        "quoted substitution": (
            'CHANGED="$(git diff --name-status "$BASE" HEAD)"'
        ),
        "or": 'true || git diff --name-status "$BASE" HEAD',
        "and": 'true && git diff --diff-filter=ACMR "$BASE" HEAD',
        "semicolon": 'true ; git diff --name-status "$BASE" HEAD',
        "pipe": 'true | git diff --name-status "$BASE" HEAD',
        "background": 'true & git diff --diff-filter=ACMR "$BASE" HEAD',
        "git -c": 'git -c diff.renames=true diff --name-status "$BASE" HEAD',
        "backslash continuation": 'git diff \\\n  --name-status "$BASE" HEAD',
    }
    for label, text in shapes.items():
        unparsed = unparsed_changed_set_diff_lines(text + "\n")
        assert unparsed, (label, text)
        assert changed_set_git_diff_commands(text) == [], (
            label,
            "an unrecognised spelling must not be a parsed command",
        )


def test_every_changed_set_diff_disables_rename_detection():
    """#4378: every changed-set selection diff must pass `--no-renames`.

    Rename detection is on by default (`diff.renames` is unset -> true), so for a
    rename git emits only the DESTINATION. The selector is handed a path set that
    never mentions the file moved away, and a guard registered against the old
    path silently does not run — the "silent in exactly the case it exists to
    cover" class this lane exists to close.

    Demonstrated on a real rename in this repo's history (371eec29f, which moved
    tests out of tortoise/shared_state/tests/ precisely so they would be collected):

        git diff --name-only 371eec29f^ 371eec29f | grep shared_state
          -> the 6 destinations under tests/ only
        git diff --no-renames --name-only 371eec29f^ 371eec29f | grep shared_state
          -> those 6 AND the 6 sources under tortoise/shared_state/tests/

    Measured cost of the flag: over main's last 200 commits, 2 commits contain any
    rename and 0 are pure moves, so the "a no-op move now selects both surfaces"
    objection does not occur in practice. Adopted on #4378.

    Scope and coverage (read before editing this test):

    * The scan reads EVERY workflow file in `.github/workflows/` — both `.yml` and
      `.yaml` — not a list of the known sites, so a new changed-set computation
      added later cannot reintroduce the hole unnoticed. The `.yaml` half is
      frozen by `test_every_changed_set_diff_scan_covers_yaml_workflows` (the repo has no
      `.yaml` workflow, so without that test the glob was unfrozen). It claims
      NOTHING about files outside `.github/workflows/`.
    * It is per COMMAND, not per line (`iter_shell_commands`): `||`/`&&`/`;`/`|`/`&`
      split a line into commands, a backslash-continued invocation is joined first,
      and comments and ordinary quoted text are dropped. `python-ci.yml`'s "Tiered
      selection" step carries TWO diffs on one line, so a line-level check would
      let the second lose the flag undetected — the regression the parser tests
      above freeze. The bare `|`/`&` joiners are load-bearing, not decorative: with
      only the doubled forms in the separator set,
      `git diff --no-renames --name-only A | git diff --name-status B` was ONE
      merged command whose token list carried both spellings, so the ownership
      count balanced and the flagless `--name-status` read clean
      (test_changed_set_parser_separates_bare_pipe_and_amp_merge).
    * A `$( … )` command substitution is NOT ordinary quoted text: its contents
      are unwrapped and parsed even inside double quotes, so the repo's own
      `CHANGED="$(git diff … )"` house style is counted and checked exactly like
      the unquoted `CHANGED=$( … )` form. A backtick substitution is parsed too
      (`_shell_tokens` treats the backtick as shell whitespace), so both
      substitution spellings are checked — not merely the `$( … )` one.
    * It FAILS CLOSED on an unrecognised spelling (`unparsed_changed_set_diff_lines`):
      a spelling the parser does not understand must not pass silently. Any
      comment-stripped logical line (backslash continuations joined) that contains
      `git` AND `diff` AND a `--name-only`/`--name-status`/`--diff-filter`
      spelling the parsed commands do not ACCOUNT FOR fails the pin with a message
      to add `--no-renames` or teach the parser the spelling. The net COUNTS flag
      occurrences instead of marking a line "covered", so an unparsed spelling
      CO-LOCATED with a parsed one is still reported — a line carrying two diffs
      is no longer rescued by the parse of its neighbour. The net is deliberately
      COARSER than the parser — it matches substrings on the comment-stripped raw
      line, not the parser's separator/quote output, and it spans a backslash
      continuation — so a spelling the parser mis-splits is still caught. It can
      only SUPPRESS a report for a spelling the parser genuinely accounted for; it
      never rescues an offender. Because it matches raw substrings, a line that
      merely QUOTES a changed-set diff (`echo "git diff --name-only"`) would
      over-report rather than slip through; that is the intended fail-closed
      direction, and no such line exists today.
    * THREAT SURFACE — the declared edge of this guard. This test is a STATIC
      SCANNER over workflow text, NOT a shell interpreter: it reads each command
      as WRITTEN and applies the rules below. The in-scope list IS the declared
      surface. A fresh reviewer that reproduces no in-scope bypass and confirms
      every in-scope class has a test terminates the review — do not chase an
      ever-more-contrived spelling outside the declared surface.

      IN SCOPE — the pin must FAIL for each of these (test named in brackets):

      - `git diff … --name-only` — the parser-recognised changed-set spelling —
        written in ANY of these shapes, when `--no-renames` is MISSING or sits
        after the first `--` (git swallows it as a pathspec; rc=0, no warning):
          · bare (test_changed_set_parser_splits_on_every_documented_separator);
          · inside `$( … )`
            (test_changed_set_parser_accepts_git_config_and_substitution);
          · inside a double-quoted `"$( … )"`
            (test_changed_set_parser_sees_quoted_command_substitution);
          · joined by `||`, `&&`, `;`, `|` or `&`
            (test_changed_set_parser_reads_commands_not_lines,
            test_changed_set_parser_splits_on_every_documented_separator,
            test_changed_set_parser_separates_bare_pipe_and_amp_merge);
          · split across a backslash continuation
            (test_changed_set_parser_joins_backslash_continuations);
          · prefixed by `git -c <cfg>`
            (test_changed_set_parser_accepts_git_config_and_substitution).
      - `--no-renames` after the first `--`
        (test_changed_set_parser_requires_flag_before_pathspec_separator).
      - `--name-status` / `--diff-filter`, and any other spelling the parser
        cannot tokenise, whenever `git` + `diff` + the flag appear on ONE
        comment-stripped logical line — bare, in a substitution, in a quoted
        substitution, `||`/`&&`/`;`/`|`/`&`-joined, `git -c`-prefixed, or split
        across a backslash continuation
        (test_changed_set_parser_cross_check_fails_closed_on_unparsed_spelling,
        test_changed_set_parser_cross_check_reports_unrecognised_spelling_in_every_shape,
        test_changed_set_parser_cross_check_sees_continuation_split_spelling).
      - CO-LOCATION: a logical line whose raw text carries a changed-set flag
        spelling MORE times than its parsed commands account for is reported, not
        covered. This states the mechanism exactly — the net counts flag
        OCCURRENCES per logical span (`raw_span.count(flag) > owned[flag]`, a
        count, not a covered/not-covered set) — so an unparsed changed-set
        spelling sharing a logical line with a parsed one is reported whenever the
        splitter keeps them as separate commands. Every shell joiner does: `||`,
        `&&`, `;`, `|` and `&` split, and a newline ends a logical line by
        construction, so a second diff on the next line is a span of its own (a
        `for`-loop body, a subshell, a `case` arm and a backtick substitution are
        each reported by the count; a newline inside a quoted string is scanned as
        two physical lines and consequently OVER-reports rather than missing).
        (test_changed_set_parser_cross_check_fails_closed_on_colocation,
        test_changed_set_parser_separates_bare_pipe_and_amp_merge).

      OUT OF SCOPE — declared, with the reason; a bypass here is a known,
      accepted gap, not a defect:

      - the command name reached DYNAMICALLY: `$GIT diff`, a shell alias, or any
        other wrapper whose raw text carries no literal `git` substring — a text
        scanner does not follow a name bound at runtime. (`/usr/bin/git diff` IS
        caught, because `/usr/bin/git` carries the `git` substring; that is the
        fail-closed direction, not a gap.)
      - fragments of one invocation split across SEPARATE statements or lines (the
        flag on one line, the command on another), or a flag assembled from
        string pieces — no single logical line then carries all three substrings
        (`git`, `diff`, the flag);
      - two changed-set spellings inside ONE parsed command with NO shell joiner
        between them (e.g. two diffs separated only by whitespace): the command's
        own token list carries both, so the raw occurrence count has no surplus
        and the net reports nothing. That shape is not two invocations — there is
        no joiner — and no site writes it. It is the ONLY same-logical-line
        co-location the count does not catch; every real joiner is handled as
        described in the CO-LOCATION entry above;
      - files outside `.github/workflows/`: the pin scans that directory only. The
        two known changed-set sites elsewhere — the `.husky/pre-commit` hook and
        the eval-drift gate — are filed as follow-ups, not covered here.
    * The non-vacuity floor (`checked >= 3`) is a FLOOR, not a pin of exactly
      three. It is counted from the PARSED commands above — measured today as
      FOUR: two on `python-ci.yml`'s "Tiered selection" step, one on `ci.yml`'s
      "Compute per-surface path gates (#2149)" step, and one on the dead step
      below. So a comment cannot satisfy it (and a comment mentioning the flag
      cannot inflate it). Because the other THREE commands satisfy the floor by
      themselves, deleting the dead step alone leaves `checked == 3` and needs NO
      floor change; the floor has to come down to 2 only if a SECOND command is
      removed, once just two remain.
    * The dead FOURTH command — from the "Get changed markdown files" step in
      `ci.yml` (cited by step name, not line number, because line numbers drift)
      — is INERT today. That step builds a lint-target list, and the `docs` job
      checks out at depth 1, so `github.event.pull_request.base.sha` is absent,
      the diff fails, `|| true` leaves `FILES` empty and the consuming
      markdownlint/lychee steps are skipped. The `docs` job's own "Conflict-marker
      check (#2802)" step comment records this. Do not let the floor drift above
      the number of live commands.
    * The same step's `--no-renames` is still deliberate and the rule applies to
      it uniformly: it IS a changed-set computation, and feeding markdownlint/lychee
      the DELETED source path of a `.md`->`.md` rename is tolerated —
      `npx markdownlint-cli <nonexistent.md>` exits 0, and plain `.md` deletions
      already put nonexistent paths into this list.
    * Do NOT generalise this rule to `.github/scripts/check-migration-append-only`.
      That script deliberately runs
      `git diff --find-renames=20% ... --name-status` because its #1235
      pure-prefix-rename repair exception is keyed on the `R*` status (recorded
      at `docs/plans/2026-08-13-1095-migration-drift-gate.md:148`); `--no-renames`
      there would emit `D`+`A` and break the gate. The rule in this pin is scoped
      to changed-set *selection* diffs; that file is a deliberate exception.
    """
    root = Path(__file__).resolve().parents[1]
    wf_dir = root / ".github" / "workflows"
    workflows = _workflow_files(wf_dir)
    assert workflows, "no workflow files found — the scan would pass vacuously"

    checked, offenders, unparsed = _scan_changed_set_diffs(workflows)

    assert checked >= 3, (
        f"expected the non-vacuity floor of 3 changed-set diff commands across "
        f".github/workflows/ (a floor, not a pin), found {checked} — either the "
        f"parser stopped recognising the sites, or a site was removed without "
        f"lowering the floor"
    )
    assert not offenders, (
        "a changed-set `git diff --name-only` without --no-renames drops the source "
        "path of a rename, so the moved file's guard never runs (#4378):\n  "
        + "\n  ".join(offenders)
    )
    assert not unparsed, (
        "a changed-set `git diff` in .github/workflows/ was not recognised by the "
        "parser, or its flag could not be accounted for beside another diff on "
        "the same line, so the --no-renames rule could not be enforced on it. "
        "Add --no-renames if it is a changed-set selection diff, or teach "
        "changed_set_git_diff_commands the new spelling (#4378):\n  "
        + "\n  ".join(unparsed)
    )


# ── #4740 review 4: the orphan-assert steps' fail-closed pgrep probe ───────
# Each of the three `Assert no redislite orphans` steps in python-ci.yml is
# the newest fail-closed control on the orphan count, and NO other test can
# see it: `orphan-bound.test.sh` reads only the gate script, and the gate
# receives an already-computed `--count`. A mutation (`-le 1` → `-lt 1`, or a
# revert to `COUNT=$(pgrep … | wc -l)`) would be undetectable. This pin reads
# the workflow text and requires, per step, that pgrep's OWN status is
# captured and the count is never read from a `pgrep | …` pipeline (whose
# status is the last command's — `tr`, always 0 — so a failed probe would
# read as a measured 0 and pass).


def _orphan_assert_steps() -> list[dict]:
    wf = _load_python_ci()
    steps = [
        s
        for job in wf["jobs"].values()
        for s in (job.get("steps") or [])
        if str(s.get("name", "")).startswith("Assert no redislite orphans")
    ]
    return steps


def test_orphan_assert_steps_capture_pgrep_status_fail_closed():
    """#4740 review 4: pgrep's own status must gate the orphan count."""
    steps = _orphan_assert_steps()
    assert len(steps) == 3, (
        f"expected the three 'Assert no redislite orphans' steps, found "
        f"{len(steps)} — this pin must not pass vacuously"
    )
    for s in steps:
        # Drop whole-line comments: they QUOTE the rejected pipeline form
        # (`COUNT=$(pgrep … | wc -l)`) and the `${PIPESTATUS[0]}` rationale, so
        # scanning raw text would flag the documentation rather than the code.
        body = "\n".join(
            line
            for line in s["run"].splitlines()
            if not line.lstrip().startswith("#")
        )
        assert "PIPESTATUS" not in body, (
            "a ${PIPESTATUS[0]} read after `COUNT=$(pgrep … | wc -l)` is the "
            "`tr` status, never pgrep's, so the guard would be inert (#4740)"
        )
        assert re.search(r"\$\(pgrep\b[^)]*\|", body) is None, (
            "COUNT must not be read from a `pgrep | …` pipeline: its status is "
            "the last command's, so a failed probe reads as a measured 0 and "
            "passes (the #4740 fail-open)"
        )
        assert 'PIDS=$(pgrep -f "redislite/bin/redis-server")' in body, (
            "pgrep must run on a bare assignment so `$?` is its own status"
        )
        assert "prc=$?" in body, "pgrep's own status must be captured"
        assert '[ "$prc" -le 1 ]' in body, (
            "rc 0 (matches) and rc 1 (none) are real measurements; anything "
            "else must fail closed (#4740)"
        )
        assert "exit 1" in body, "the failed-probe guard must exit non-zero"
        assert 'COUNT=$(printf \'%s\' "$PIDS" | wc -w | tr -d \' \')' in body, (
            "COUNT must be derived from the pgrep output `$PIDS`, not a "
            "constant — a `COUNT=0` would hand the gate a measured-zero that "
            "no later leak could ever exceed (#4740 review 10)"
        )
        assert '--count "$COUNT"' in body, (
            "the orphan gate must consume the derived COUNT (#4740 review 10)"
        )


def test_orphan_assert_no_pytest_producer_writes_the_gated_path():
    """#4740 review 5: each empty-selection block must WRITE the no-pytest
    report to the very path the orphan gate is handed.

    Cases 11-13 of orphan-bound.test.sh pin the gate's READING of that report,
    but nothing pinned the PRODUCER: deleting one `printf` left both the
    harness and the pgrep pin green, so the cycle-1 P0 (the gate parses a
    `missing` report and REDs a legitimately-empty selection) could silently
    return. This reads the real workflow via `_load_python_ci()`.
    """
    report_path = "${RUNNER_TEMP:-/tmp}/redislite-hygiene-end.json"
    producers = [
        s
        for job in _load_python_ci()["jobs"].values()
        for s in (job.get("steps") or [])
        if isinstance(s.get("run"), str) and '"skipped":"no-pytest"' in s["run"]
    ]
    assert len(producers) == 3, (
        f"expected the three empty-selection blocks that write the "
        f"'no-pytest' report, found {len(producers)} — one `printf` deleted "
        f"leaves the gate parsing a missing report and REDs a healthy skip "
        f"(the #4740 cycle-1 P0)"
    )
    for s in producers:
        printf_lines = [
            line
            for line in s["run"].splitlines()
            if '"skipped":"no-pytest"' in line
        ]
        assert len(printf_lines) == 1, (
            f"step {s.get('name')!r} must write the no-pytest report exactly "
            f"once, found {len(printf_lines)} lines carrying it"
        )
        line = printf_lines[0].strip()
        assert line.startswith("printf '"), (
            f"step {s.get('name')!r} must WRITE the report with printf, got "
            f"{line!r}"
        )
        assert line.endswith(f'> "{report_path}"'), (
            f"step {s.get('name')!r} must write the no-pytest report to "
            f"{report_path} — the exact path the orphan gate is handed, not a "
            f"different file (#4740)"
        )
    handed = [
        s
        for s in _orphan_assert_steps()
        if f'--hygiene "{report_path}"' in s["run"]
    ]
    assert len(handed) == 3, (
        f"all three orphan-assert steps must be handed {report_path}, the "
        f"path the empty-selection blocks write; found {len(handed)}"
    )
