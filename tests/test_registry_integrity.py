"""The `merge=union` registries must fail closed on what union can emit (#5373).

`config/ci-surfaces.yml` and `config/surface-manifest.yml` carry `merge=union` in
`.gitattributes`. `union` is a git BUILT-IN, so it needs no driver config in any
clone — but it is a LINE-level rule, and two lanes appending to the same anchor can
make it emit a duplicate mapping key, a duplicate list entry, or a malformed
document. `yaml.safe_load` then silently keeps the LAST duplicate key and no
downstream reader can tell.

This file is the proof that the validator paired with union is fail-closed, not the
proof that union is clever:

  * `safe_load` is shown to SWALLOW a duplicate key, while `check_registry` catches
    it — so the validator is load-bearing, not decorative;
  * each hazard union can produce is asserted to be refused;
  * the set of files the validator guards is pinned to the set `.gitattributes`
    unions, so a registry added to one and not the other is a red test;
  * the generated rename table is asserted NOT to be unioned and NOT to be tracked
    (its conflict class is removed by not committing it, not by merging it).

Registered in `config/ci-surfaces.yml` (`manifest-integrity` fails on an
unregistered test file).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.registry_integrity import (  # noqa: E402
    REGISTRIES,
    check,
    check_registry,
)

GITATTRIBUTES = ROOT / ".gitattributes"
GENERATED = "docs/product/sdk-rename-table.md"


def _union_patterns() -> set[str]:
    """The paths `.gitattributes` gives `merge=union`, parsed here (independent oracle)."""
    unioned: set[str] = set()
    for raw in GITATTRIBUTES.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        pattern, attrs = parts[0], parts[1:]
        if any(a == "merge=union" for a in attrs):
            unioned.add(pattern)
    return unioned


# ─────────────────────────────────────────────────────────────────────
# The mechanism: union's damage is INVISIBLE to the loader the repo uses
# ─────────────────────────────────────────────────────────────────────
def test_safe_load_swallows_the_duplicate_key_the_validator_catches(tmp_path: Path) -> None:
    """The core argument for this tool, asserted rather than asserted-about.

    Two lanes each add a top-level `slow_files:` block; union keeps both. PyYAML's
    own `safe_load` returns a document with ONE `slow_files` key and no error, so
    every existing consumer (which all use `safe_load`) sees a valid, silently
    truncated registry. `check_registry` is what turns that into a refusal.
    """
    victim = tmp_path / "ci-surfaces.yml"
    victim.write_text(
        "version: 1\n"
        "slow_files:\n"
        "- lane_a.py\n"
        "slow_files:\n"
        "- lane_b.py\n",
        encoding="utf-8",
    )

    loaded = yaml.safe_load(victim.read_text(encoding="utf-8"))
    assert loaded == {"version": 1, "slow_files": ["lane_b.py"]}, (
        "if safe_load ever starts rejecting duplicates this test is obsolete — but "
        "until then the raw load HIDES the defect"
    )

    problems = check_registry(victim)
    assert len(problems) == 1
    assert "duplicate mapping key 'slow_files'" in problems[0]


def test_duplicate_sequence_entry_is_refused(tmp_path: Path) -> None:
    """Two lanes appending the SAME test file — union keeps both lines."""
    victim = tmp_path / "ci-surfaces.yml"
    victim.write_text(
        "version: 1\nsurfaces:\n  core:\n  - test_a.py\n  - test_a.py\n",
        encoding="utf-8",
    )
    problems = check_registry(victim)
    assert len(problems) == 1 and "duplicate entry 'test_a.py'" in problems[0]


def test_duplicate_name_in_the_frozen_baseline_is_refused(tmp_path: Path) -> None:
    """A name-keyed comparison drops all but the last row — unverified content."""
    victim = tmp_path / "surface-manifest.yml"
    victim.write_text(
        "manifest_version: 1\nrows:\n- name: tortoise_x\n  served: true\n"
        "- name: tortoise_x\n  served: false\n",
        encoding="utf-8",
    )
    problems = check_registry(victim)
    assert len(problems) == 1 and "duplicate `name` 'tortoise_x'" in problems[0]


def test_malformed_yaml_is_refused(tmp_path: Path) -> None:
    """Union spliced a comment or an out-of-place line: refuse, never parse-leniently."""
    victim = tmp_path / "ci-surfaces.yml"
    victim.write_text("version: 1\nsurfaces:\n  core:\n  - a.py\n :oops\n", encoding="utf-8")
    problems = check_registry(victim)
    assert len(problems) == 1 and "MALFORMED YAML" in problems[0]


def test_missing_file_is_refused(tmp_path: Path) -> None:
    """A check that cannot read its evidence must not report success."""
    problems = check_registry(tmp_path / "absent.yml")
    assert len(problems) == 1 and "unreadable" in problems[0]


def test_clean_document_passes(tmp_path: Path) -> None:
    good = tmp_path / "ci-surfaces.yml"
    good.write_text(
        "version: 1\nsurfaces:\n  core:\n  - test_a.py\n  - test_b.py\n"
        "slow_files:\n- test_a.py\n",
        encoding="utf-8",
    )
    assert check_registry(good) == []


def test_the_real_registries_are_clean() -> None:
    """The gate must start green on the tree it lands on."""
    assert check() == []


def test_cli_exit_codes(tmp_path: Path, capsys) -> None:
    from tools.registry_integrity import main

    assert main([str(ROOT / "config" / "ci-surfaces.yml")]) == 0
    assert "clean" in capsys.readouterr().out

    bad = tmp_path / "bad.yml"
    bad.write_text("a: 1\na: 2\n", encoding="utf-8")
    assert main([str(bad)]) == 1
    assert "duplicate mapping key" in capsys.readouterr().err


# ─────────────────────────────────────────────────────────────────────
# The invariants that keep the pairing honest
# ─────────────────────────────────────────────────────────────────────
def test_validated_set_equals_unioned_set() -> None:
    """Every file `.gitattributes` unions is a file the validator guards.

    Adding a registry to one side and not the other is the silent failure mode this
    pins: a unioned file with no validator is exactly the hazard the tool exists to
    remove.
    """
    assert _union_patterns() == {str(p) for p in REGISTRIES}


def test_generated_artifacts_are_not_unioned() -> None:
    """The generated rename table is NOT unioned — union there duplicates rows.

    Both sides regenerate it against different `sdk.py` line numbers, so a union
    produces a table matching neither side. Its conflict class is removed by not
    committing it (see the next test), never by merging it.
    """
    for pattern in _union_patterns():
        assert not re.fullmatch(pattern, GENERATED), (
            f"`{GENERATED}` is generated; unioning it duplicates rows"
        )
    assert "rename-table" not in " ".join(_union_patterns())


@pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
def test_the_generated_rename_table_is_not_tracked() -> None:
    """The file is generated on demand — it must be ignored and untracked (#5373)."""
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "--", GENERATED],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert tracked == "", f"{GENERATED} is still committed; its conflicts are not removed"

    ignored = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "-q", "--", GENERATED],
    )
    assert ignored.returncode == 0, f"{GENERATED} is untracked but not ignored"
