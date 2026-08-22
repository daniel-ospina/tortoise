"""Unit tests for the tiered test selector (#1021).

Covers the fail-closed selection rules: docs-only → tier 1; surface changes →
tier 1 ∪ surface; shared modules → full; unknown paths → full; test-file
changes select their owning surface; push/schedule → full; manifest integrity
(recursive rglob since #1349 — subdir test files, tests/e2e/ exempt; tool-path
carve-out so tools/longmem_eval/ etc. select the eval surface).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ci_selection import (  # noqa: I001
    load_manifest, select, integrity, slow_file_issues,  # noqa: F401
    unlisted_tests, register_tests, register, classify_test_file,  # noqa: F401
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
    r = _sel(["tortoise/onboarding/AGENT_ONBOARDING.md"])
    assert r["full"] is False
    assert "onboarding" in r["surfaces"]
    assert set(r["test_files"]) == _tier1() | set(load_manifest()["surfaces"]["onboarding"])


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
    r = _sel(["tortoise/decide.py", "tortoise/onboarding/AGENT_ONBOARDING.md"])
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
    """#1472: every classified file lands in exactly one push leg."""
    from tools.ci_selection import push_legs, ENV_BROKEN_FILES  # noqa: I001
    m = load_manifest()
    legs = push_legs(m)
    slow = {f.replace(".py", "") for f in m["slow_files"]}  # noqa: F841
    classified = set()
    for s, files in m["surfaces"].items():  # noqa: B007
        classified.update(files)
    classified.update(m["tier1"])
    classified.update(m["slow_files"])
    fast = {f.replace(".py", "") for f in classified if f not in m["slow_files"]}
    fast |= {f.replace(".py", "") for f in m.get("push_extra", [])}
    broken = {f.replace(".py", "") for f in ENV_BROKEN_FILES}
    assert set(legs["half_a"]) | set(legs["half_b"]) == fast - broken
    assert not (set(legs["half_a"]) & set(legs["half_b"])), "leg overlap"
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


