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
    """#1472: every classified file lands in exactly one push leg. Epic
    #1647 Task 9: the 17-file carve-out set is its OWN leg (E2E-4) — it is
    excluded from fast AND slow docker legs."""
    from tools.ci_selection import push_legs, ENV_BROKEN_FILES  # noqa: I001
    m = load_manifest()
    legs = push_legs(m)
    slow = {f.replace(".py", "") for f in m["slow_files"]}
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
    cmdline = next(l for l in invocation.splitlines()
                   if "python -m pytest" in l)
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
    cmdline = next(l for l in invocation.splitlines()
                   if "python -m pytest" in l)
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
