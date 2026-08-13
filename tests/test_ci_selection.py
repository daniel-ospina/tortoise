"""Unit tests for the tiered test selector (#1021).

Covers the fail-closed selection rules: docs-only → tier 1; surface changes →
tier 1 ∪ surface; shared modules → full; unknown paths → full; test-file
changes select their owning surface; push/schedule → full; manifest integrity.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ci_selection import load_manifest, select, integrity


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
