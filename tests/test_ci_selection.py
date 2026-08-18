"""Unit tests for the tiered test selector (#1021).

Covers the fail-closed selection rules: docs-only → tier 1; surface changes →
tier 1 ∪ surface; shared modules → full; unknown paths → full; test-file
changes select their owning surface; push/schedule → full; manifest integrity.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ci_selection import (
    load_manifest, select, integrity, slow_file_issues,
    unlisted_tests, register_tests, register, classify_test_file,
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
    # every tests/*.py must be classified — the drift trap (scope v5 dec 2/5)
    assert integrity(load_manifest()) == []


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

