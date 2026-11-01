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

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PYPROJECT = REPO / "pyproject.toml"
BASELINE_DOC = REPO / "docs" / "lint-baseline-1503.md"
#: The #4282 1.1 approved-surface declaration — a DATA module, exempt from two codes.
DECLARATION_PATH = "tortoise/__all__.py"


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


# ── The declaration module's exemption (#10056) ───────────────────────────────
#
# `tortoise/__all__.py` is a data declaration module (#4282 1.1 / #10034): its 40 names are
# CLASS MEMBERS of `TortoiseSDK`, deliberately not module-level bindings, listed in the
# owner-approved order of `docs/product/beta-sdk-surface.md` rows 1-40. So F822 (undefined
# name in `__all__`) can never be satisfied by an import, and RUF022 (unsorted `__all__`)
# asks for exactly the reordering the declaration must not make. The exemption is
# therefore legitimate — but only if it stays SCOPED to that one path, and only while the
# file is otherwise clean. Both halves are pinned here, because either one silently
# reverts to a red `agent-infra-ci / lint` on every PR (the cost measured in #10056/#9896).


def test_the_declaration_module_exemption_is_declared_and_scoped(pyproject: dict) -> None:
    """Exactly `tortoise/__all__.py` is exempt from exactly F822 + RUF022 (#10056)."""
    lint = pyproject["tool"]["ruff"]["lint"]
    exempt = lint.get("per-file-ignores") or {}
    assert exempt.get(DECLARATION_PATH) == ["F822", "RUF022"], (
        "the approved-surface declaration needs a per-file exemption for F822 + RUF022 "
        "(#10056): the names are class members, not module bindings, and the order is the "
        f"owner's document order. Found: {exempt.get(DECLARATION_PATH)!r}"
    )
    assert list(exempt) == [DECLARATION_PATH], (
        "the exemption must stay scoped to the one declaration path — a second entry is a "
        f"rule switched off for a real module: {sorted(exempt)}"
    )
    for code in ("F822", "RUF022"):
        assert code not in lint["ignore"], (
            f"{code} must NOT be in [tool.ruff.lint].ignore — disabling it tree-wide is how "
            "the #4282 declaration's exemption turns into a blanket blind spot"
        )


def _ruff() -> str:
    """The ruff binary CI uses (pinned in the dev group), or a skip."""
    pinned = REPO / ".venv" / "bin" / "ruff"
    if pinned.exists():
        return str(pinned)
    found = shutil.which("ruff")
    if not found:
        pytest.skip("ruff is not installed in this environment")
    return found


def test_the_declaration_module_is_otherwise_clean_under_the_repo_lint() -> None:
    """The exemption is only honest while the FILE has no other ruff error.

    This is the `agent-infra-ci / lint` command's own rule set applied to the real path:
    before #10056 the run reported 41 errors here (40 × F822 + 1 × RUF022), which reddened
    every PR in the repo.
    """
    proc = subprocess.run(
        [_ruff(), "check", "--no-cache", DECLARATION_PATH],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"{DECLARATION_PATH} must be ruff-clean under the exemption; got "
        f"rc={proc.returncode}:\n{proc.stdout}{proc.stderr}"
    )


def test_f822_and_ruf022_still_fire_for_every_other_module() -> None:
    """Scoped, not switched off: both codes still red a probe at any other path."""
    probe = (
        "def aaa() -> None: ...\n"
        "def zzz() -> None: ...\n"
        '__all__ = ("zzz", "aaa", "undefined_on_purpose")\n'
    )
    proc = subprocess.run(
        [
            _ruff(),
            "check",
            "--no-cache",
            "--config",
            str(PYPROJECT),
            "--stdin-filename",
            "tortoise/_exemption_scope_probe.py",
            "-",
        ],
        cwd=REPO,
        input=probe,
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr
    assert proc.returncode == 1, f"the probe should fail; got rc={proc.returncode}: {output}"
    assert "F822" in output, f"F822 was switched off tree-wide: {output}"
    assert "RUF022" in output, f"RUF022 was switched off tree-wide: {output}"


# ── A dead `noqa` reds the required lint gate (#10423) ────────────────────────
#
# A noqa directive naming a code is only meaningful when the select list ENABLES that
# code. Naming a rule ruff cannot raise (here `BLE001` — `BLE` is not in `select`) leaves
# a directive that suppresses nothing, and `RUF100` (unused-noqa, itself in `RUF`) then
# reds the required `agent-infra-ci / lint` gate on the whole tree. PR #10423's head
# `787b68f40` reddened exactly that way — run 41237922683, job 124646988652:
#
#   RUF100 Unused `noqa` directive (non-enabled: `BLE001`)
#     --> tools/served_skills_parity.py:823:31
#
# The parent commit was clean, so the defect was the new directive rather than the
# config. The surface this pins is the tool that carried it: a directive can only be
# dead where the rule set says it is, so this runs the real repo config over the path.
#
# Scoped to the path on purpose, NOT `ruff check .`: a whole-tree run inside a unit
# test turns every lane's unrelated uncommitted work into a red here, and the
# tree-wide pass is already CI's (`ci.yml` `lint-command: ruff check .`). This is the
# local, path-level signal for the module in question, mirroring the declaration-module
# test above.
#
# ⛔ THE SECOND PATH IS THIS MODULE, and that is not padding (#10423 review, P2): ruff
# parses a noqa sequence out of a COMMENT, so prose that spells the directive form —
# comment marker, the word noqa, a colon, then a token — is itself read as a directive and
# ruff warns that it is invalid on every run. A warning does not fail `ruff check .`
# (rc stays 0), so nothing caught it until it was measured here; the fix is to say "a noqa
# directive" in prose and never spell the parseable form. The stderr assert is what pins
# that half: rc alone stays 0 through the malformed directive.

SERVED_SKILLS_PARITY = "tools/served_skills_parity.py"
THIS_MODULE = "tests/test_lint_config.py"
#: Prose in this module is linted too, so the module that explains a dead noqa can warn about
#: one of its own.
LINT_CLEAN_PATHS = (SERVED_SKILLS_PARITY, THIS_MODULE)


@pytest.mark.parametrize("path", LINT_CLEAN_PATHS)
def test_the_paths_pinned_here_are_clean_under_the_repo_lint(path: str) -> None:
    """`tools/served_skills_parity.py` carries no dead noqa; this module emits no invalid one.

    Genuinely fails on the pre-fix head: `787b68f40` reported exactly one RUF100 on the
    tool (an unused `# noqa: BLE001`), and `7b93aa652` (its parent) plus `origin/main`
    were clean. Dropping the directive — the code was never enabled, so there was nothing
    to suppress — is what makes this pass, not a config change.

    The stderr half fails on `920fd7c64`, whose comment spelled the directive form: ruff
    warned `Invalid `# noqa` directive on tests/test_lint_config.py:179` on every run while
    the exit status stayed 0.
    """
    proc = subprocess.run(
        [_ruff(), "check", "--no-cache", path],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"{path} must be ruff-clean under the repo config; got "
        f"rc={proc.returncode}:\n{proc.stdout}{proc.stderr}"
    )
    assert "Invalid `# noqa` directive" not in proc.stderr, (
        f"{path} emits a malformed noqa directive — prose that spells the directive "
        f"form is parsed as one, and ruff warns while still exiting 0:\n{proc.stderr}"
    )
