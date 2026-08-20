"""Lint-config contract tests (#1503).

Guards the ruff/mypy baseline that enables the shared agent-infra full lint
(ci.yml `agent-infra-ci` job). If the config is removed or the baseline lists
are dropped, CI's lint job still gates, but this test catches silent drift in
the repo-local contract: the rule surface must stay explicit (version-stable,
not ruff's changing default), the deferred style rules must be declared, and
the mypy baseline must be a documented, non-empty list.

Pure stdlib (tomllib) — no ruff/mypy required to run.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = REPO / "pyproject.toml"
BASELINE_DOC = REPO / "docs" / "lint-baseline-1503.md"


@pytest.fixture(scope="module")
def pyproject() -> dict:
    with PYPROJECT.open("rb") as fh:
        return tomllib.load(fh)


def test_ruff_section_present(pyproject: dict) -> None:
    """[tool.ruff] exists — the agent-infra convention is pyproject.toml."""
    ruff = pyproject.get("tool", {}).get("ruff")
    assert ruff is not None, "missing [tool.ruff] in pyproject.toml"


def test_ruff_line_length_matches_codebase(pyproject: dict) -> None:
    """line-length 100 — measured against the codebase (#1503)."""
    assert pyproject["tool"]["ruff"]["line-length"] == 100
    assert pyproject["tool"]["ruff"]["target-version"] == "py312"


def test_ruff_rule_surface_is_explicit(pyproject: dict) -> None:
    """Explicit select — ruff 0.16 changed its default surface to 413 rules.

    Relying on the default would drift the baseline with every ruff bump.
    """
    lint = pyproject["tool"]["ruff"]["lint"]
    assert lint["select"] == ["E", "F", "B", "UP", "I", "SIM", "RUF"]


def test_ruff_deferred_style_rules_declared(pyproject: dict) -> None:
    """E501 + RUF001/2/3 are the only never-enforced rules (#1503)."""
    lint = pyproject["tool"]["ruff"]["lint"]
    assert set(lint["ignore"]) == {"E501", "RUF001", "RUF002", "RUF003"}


def test_mypy_section_present(pyproject: dict) -> None:
    """[tool.mypy] exists with the agent-infra workflow flags in config."""
    mypy = pyproject["tool"]["mypy"]
    assert mypy["ignore_missing_imports"] is True
    assert mypy["check_untyped_defs"] is True
    assert mypy["python_version"] == "3.12"


def test_mypy_baseline_disabled_codes_documented(pyproject: dict) -> None:
    """disable_error_code is the mypy baseline — must be non-empty + documented.

    mypy has no `# type: ignore` auto-add; the 406-error first pass is recorded
    as a config baseline. Dropping it silently (or emptying it) would either
    re-break CI or hide the baseline — both fail here.
    """
    codes = pyproject["tool"]["mypy"]["disable_error_code"]
    assert codes, "disable_error_code must not be empty (baseline under #1503)"
    doc = BASELINE_DOC.read_text(encoding="utf-8")
    for code in codes:
        assert code in doc, (
            f"mypy baseline code {code} missing from docs/lint-baseline-1503.md "
            "— every disabled code must be documented with its count"
        )
