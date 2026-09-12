"""Unit tests for the tiered test selector (#1021).

Covers the fail-closed selection rules: docs-only → tier 1; surface changes →
tier 1 ∪ surface; shared modules → full; unknown paths → full; test-file
changes select their owning surface; push/schedule → full; manifest integrity
(recursive rglob since #1349 — subdir test files, tests/e2e/ exempt; tool-path
carve-out so tools/longmem_eval/ etc. select the eval surface).
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ci_selection import (  # noqa: I001
    load_manifest, select, integrity, slow_file_issues,  # noqa: F401
    unlisted_tests, register_tests, register, classify_test_file,  # noqa: F401
    surface_audit, render_surface_audit,
)


def _sel(changed, event="pull_request"):
    return select(changed, event, load_manifest())


def _tier1() -> set:
    return set(load_manifest()["tier1"])


def test_docs_only_runs_tier1():
    r = _sel(["docs/README.md", "website/welcome.html"])
    assert r["full"] is False
    assert r["surfaces"] == []
    assert set(r["test_files"]) == _tier1()


def test_onboarding_change_selects_onboarding():
    r = _sel(["tortoise/onboarding/SKILL.md"])
    assert r["full"] is False
    assert "onboarding" in r["surfaces"]
    assert set(r["test_files"]) == ((_tier1() | set(load_manifest()["surfaces"]["onboarding"]))
                                     - set(load_manifest().get("carve_out", [])))


def test_ep_change_selects_ep():
    r = _sel(["tortoise/decide.py"])
    assert r["full"] is False
    assert r["surfaces"] == ["ep"]
    assert "test_decide.py" in r["test_files"]


def test_shared_module_goes_full():
    r = _sel(["tortoise/sdk.py"])
    assert r["full"] is True
    assert r["test_files"] == "ALL"
    r2 = _sel(["tests/conftest.py"])
    assert r2["full"] is True


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


def test_two_surfaces_union():
    r = _sel(["tortoise/decide.py", "tortoise/onboarding/SKILL.md"])
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

    docs = _sel(["docs/README.md", "website/welcome.html"])
    assert not (set(docs["test_files"]) & slow), "docs-only tier-1 leaks slow files"

    core = _sel(["tortoise/graph.py", "tortoise/ingest.py"])
    assert not (set(core["test_files"]) & slow), "tier-2 core leaks slow files"

    ep = _sel(["tortoise/decide.py", "tortoise/ranking.py"])
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
        (["tortoise/decide.py"], "pull_request"),
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
        (["tortoise/decide.py"], "pull_request"),
    ]:
        r = select(changed, event, m)
        for key in ("slow_run", "slow_selected", "carve_out_run"):
            assert key in r, f"missing {key} for {changed}/{event}"
        assert set(r["slow_selected"]) <= leg_set, \
            f"slow_selected must be subset of slow_files - carve_out ({changed}/{event})"


def test_docs_only_skips_slow_and_carve_out():
    """#2147/#2148: docs/website-only PRs touch no slow/carve-out surface —
    both formerly-unconditional legs (the audit's F1/F2 cost drivers) skip;
    the tier-1 smoke still runs in the fast job."""
    for changed in (["docs/README.md"], ["website/welcome.html"],
                    ["docs/README.md", "website/self-hosted.html"]):
        r = _sel(changed)
        assert r["surfaces"] == []
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
    decide.py-only PR selects exactly those (never the full 24-file leg
    set), and the carve-out job skips (ep owns no carve-out file)."""
    r = _sel(["tortoise/decide.py"])
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
    from tools.ci_selection import (TESTS_DIR, push_legs,  # noqa: I001
                                    workflow_halves_issues)
    legs = push_legs(load_manifest())
    halves = {"a": set(legs["half_a"]), "b": set(legs["half_b"])}
    issues = workflow_halves_issues(load_manifest(), halves, TESTS_DIR)
    assert issues == [], f"derived halves drift: {issues}"
    assert abs(len(halves["a"]) - len(halves["b"])) <= 3, "tilt beyond ±3"


def test_push_legs_partitions_every_classified_file():
    """#1472: every classified file lands in exactly one push leg. Epic
    #1647 Task 9: the 17-file carve-out set is its OWN leg (E2E-4) — it is
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
    # bench push_extra lands in half b
    assert any(f.startswith("bench/") for f in legs["half_b"])


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
    # a slow-file key must fail
    bad = dict(m)
    bad["durations"] = {"test_about_edges.py": 10.0}  # a slow file
    assert duration_issues(bad) != []
    # an unclassified key must fail
    bad2 = dict(m)
    bad2["durations"] = {"not_a_real_file.py": 10.0}
    assert duration_issues(bad2) != []


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
    producer = next(s for s in steps
                    if s.get("name", "").startswith("Canary producer"))
    assert producer["if"] == "github.event_name == 'push' || " \
        "github.event_name == 'schedule'", "producer must be post-merge only"
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
    """E2E-4 (Task 9 Step 5): the dedicated carve-out job runs the 17-file
    embedded set URI-UNSET (no TORTOISE_DB_URI — a URI would redirect the
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
    for row in rows:
        assert row["files"].startswith("test_"), \
            "test-slow leg rows must stay the committed literal lists (#1471)"


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
    post-merge only (push/schedule), needs [test] (matrix fan-in), consumes
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
        "the drift job must be unconditional (push/PR/schedule) — an `if:` "
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

    # Render the GitHub expression into literal results and actually RUN the
    # aggregate's script: this is what turns "the words are present" into "a
    # failed drift really exits non-zero". (Guarded: the assertion is about the
    # shell logic, which is the thing that has to be right on the runner.)
    if shutil.which("bash"):
        def _verdict(*results: str) -> int:
            rendered = script.replace("${{ join(needs.*.result, ' ') }}",
                                      " ".join(results))
            return subprocess.run(["bash", "-c", rendered],
                                  capture_output=True).returncode

        count = len(jobs["python-ci-gate"].get("needs") or [])
        green = ["success"] * count
        assert _verdict(*green) == 0, (
            "an all-green matrix must pass the required check")
        for red in ("failure", "cancelled"):
            assert _verdict(*green[:-1], red) == 1, (
                f"a `{red}` need must FAIL python-ci-gate — otherwise a drift "
                "does not block the merge (#2656)")
        assert _verdict(*green[:-1], "skipped") == 0, (
            "a skipped need is not a failure (docs-only PRs skip the matrix)")


# ── #2938: surface audit (report-only) ───────────────────────────────────
# The audit must resolve what the rejected mechanical derivation could not:
# package-level imports, `tortoise/api.py` (absent from SOURCE_PATTERNS),
# helper/fixture indirection, string path refs — and it must stay a REPORT
# (never mutate the manifest, never change select()/--integrity).

_AUDIT_SOURCES = {
    "tortoise/hosted_api.py": "",
    "tortoise/api.py": "",
    "tortoise/decide.py": "",
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
    # rule (b): `tortoise/api.py` is real api-owned source but is absent from
    # SOURCE_PATTERNS["api"] — the report must SAY so, not bin it as core
    repo = _audit_repo(tmp_path, {
        "tests/test_api_mod.py": "from tortoise.api import EventAPI\n"})
    report = _audit(_audit_manifest(api=["test_api_mod.py"]), repo)
    gaps = report["coverage_gaps"]
    assert any(g["path"] == "tortoise/api.py" and g["surface"] == "api"
               for g in gaps), gaps
    assert "tortoise/api.py" not in [r["path"] for r in report["uncovered"]]
    removal = report["surfaces"]["api"]["removal"][0]
    assert removal["gap"] == ["tortoise/api.py"]


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
        "tests/test_api_registered.py": "from tortoise.api import EventAPI\n",
        "tests/test_core_registered.py": "from tortoise.api import EventAPI\n"})
    manifest = _audit_manifest(api=["test_api_registered.py"],
                               core=["test_core_registered.py"])
    report = _audit(manifest, repo)
    gap = next(g for g in report["coverage_gaps"]
               if g["path"] == "tortoise/api.py")
    # both files are STRONG pinners (they import it); registration differs
    assert set(gap["files"]) == {"test_api_registered.py",
                                 "test_core_registered.py"}
    assert gap["files"]["test_api_registered.py"] == ["api"]
    assert gap["files"]["test_core_registered.py"] == ["core"]
    assert gap["registered"] == ["api", "core"]
    # `select(['tortoise/api.py'])` selects `core`, so only the api-registered
    # pinner is a victim; the core-registered one runs.
    assert gap["selected_surfaces"] == ["core"]
    assert gap["victims"] == ["test_api_registered.py"]
    assert gap["runs"] == ["test_core_registered.py"]
    # ... and the rendered line agrees with the data
    never_line = render_surface_audit(report).split(
        "never run on a change to tortoise/api.py:")[1].split("\n")[0]
    assert "test_api_registered.py" in never_line
    assert "test_core_registered.py" not in never_line, (
        "a core-registered pinner runs via core and must not be in the "
        "\"never run\" list")


def test_surface_audit_coverage_gap_counts_unselected_surface_as_victim(tmp_path):
    # P2: `select(['tortoise/api.py'], 'pull_request', manifest)` yields
    # `surfaces=['core']` — it does NOT select `ep`, so the ep-registered
    # pinner never runs. The previous renderer asserted the non-gap pinners
    # "run via core/ep", which was false for the ep one.
    repo = _audit_repo(tmp_path, {
        "tests/test_api_registered.py": "from tortoise.api import EventAPI\n",
        "tests/test_core_registered.py": "from tortoise.api import EventAPI\n",
        "tests/test_ep_registered.py": "from tortoise.api import EventAPI\n"})
    manifest = _audit_manifest(api=["test_api_registered.py"],
                               core=["test_core_registered.py"],
                               ep=["test_ep_registered.py"])
    report = _audit(manifest, repo)
    gap = next(g for g in report["coverage_gaps"]
               if g["path"] == "tortoise/api.py")
    assert set(gap["files"]) == {"test_api_registered.py",
                                 "test_core_registered.py",
                                 "test_ep_registered.py"}
    assert gap["files"]["test_ep_registered.py"] == ["ep"]
    assert gap["selected_surfaces"] == ["core"]
    assert gap["victims"] == ["test_api_registered.py",
                              "test_ep_registered.py"]
    assert gap["runs"] == ["test_core_registered.py"]
    never_line = render_surface_audit(report).split(
        "never run on a change to tortoise/api.py:")[1].split("\n")[0]
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
        "tests/test_fixture_data.py":
            "PATHS = ['tortoise/api.py']\n"
            "def test_x(): assert PATHS\n",
        "tests/test_fixture_elsewhere.py":
            "PATHS = ['battery/cli.py']\n"
            "def test_y(): assert PATHS\n"})
    report = _audit(_audit_manifest(api=["test_fixture_data.py"],
                                    core=["test_fixture_elsewhere.py"]), repo)
    gap = next(g for g in report["coverage_gaps"]
               if g["path"] == "tortoise/api.py")
    assert gap["files"] == {}, "a string-only reference is not a strong pinnner"
    assert list(gap["weak_files"]) == ["test_fixture_data.py"]
    out = render_surface_audit(report)
    assert '"tortoise/api.py" (string, not an import)' in out, \
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
        "tests/test_imports_api.py": "from tortoise.api import EventAPI\n"})
    report2 = _audit(_audit_manifest(api=["test_imports_api.py"]), repo2)
    gap2 = next(g for g in report2["coverage_gaps"]
                if g["path"] == "tortoise/api.py")
    assert list(gap2["files"]) == ["test_imports_api.py"]


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
