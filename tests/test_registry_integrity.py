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


def test_a_duplicate_key_inside_a_nested_mapping_is_refused(tmp_path: Path) -> None:
    """Depth is the point: `_walk` must RECURSE into `.durations` (#10894).

    WHICH mutant this pins, measured rather than assumed. Every other arm in this file
    duplicates the ROOT mapping or a `surfaces:` SEQUENCE, and all eleven of them pass
    on a `_walk` whose mapping branch stops descending into a nested mapping **whose
    values are all scalars** — the plausible "a dict of scalars is a leaf" shortcut
    (measured on that mutant: `1 failed, 11 passed`, the failure being this arm). A
    cruder mutant that never descends a mapping value at all also reds
    `test_duplicate_sequence_entry_is_refused` (`2 failed, 10 passed`), so this arm's
    distinct contribution is the scalar-leaf depth, not "nested-ish" in general.
    `config/ci-surfaces.yml` carries `merge=union`, and
    `.gitattributes` applies it to the whole FILE — so a retimed block appended on
    both sides of a base move lands as two `durations:` rows with the SAME key, at
    exactly the depth this suite did not reach.

    Reproduced from this repo's own history rather than quoted:
    `git merge-tree --write-tree --merge-base=76f422bd3 d99c25912 641e5f1b3` yields
    tree `a0d401c30`, and `check_registry()` on that tree's
    `config/ci-surfaces.yml` returns **12** duplicate keys, all at `.durations`
    (1,254 rows over 1,242 distinct by `yaml.compose`). The head of that same merge
    returns **0**. The required `manifest-integrity` job
    (`.github/workflows/python-ci.yml:590`) runs this validator at every depth, so
    it was RED for it — but nothing in THIS suite named the class, so a reviewer
    reading only the tests saw a green tree.

    A duplicated WEIGHT is the silent half: the file still parses, `safe_load`
    keeps the LAST value, and the shard packer then uses it. This test is
    non-vacuous in both directions — it rejects a real `.durations` duplicate and
    passes on a clean document.
    """
    victim = tmp_path / "ci-surfaces.yml"
    victim.write_text(
        "version: 1\n"
        "durations:\n"
        "  test_same.py: 2.0\n"
        "  test_other.py: 1.0\n"
        "  test_same.py: 9.0\n",
        encoding="utf-8",
    )
    problems = check_registry(victim)
    assert len(problems) == 1, problems
    assert "duplicate mapping key 'test_same.py' at .durations" in problems[0], problems

    clean = tmp_path / "clean.yml"
    clean.write_text(
        "version: 1\ndurations:\n  test_same.py: 2.0\n  test_other.py: 1.0\n",
        encoding="utf-8",
    )
    assert check_registry(clean) == [], "a clean nested mapping must NOT be refused"


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


# ─────────────────────────────────────────────────────────────────────
# The other entry point: `ci_selection.py --integrity` must SEE the class
# ─────────────────────────────────────────────────────────────────────
# `--integrity` is the check fixers actually run, and the one a PR body cites
# as proof that the durations map carries no duplicate. It reads the manifest
# through `yaml.safe_load`, so before this wiring it was structurally blind to
# a duplicate MAPPING key: #10726 reddened the REQUIRED `manifest-integrity`
# leg four times (62 durations keys, then 13, then 1, then 2) while
# `--integrity` printed its green OK line over every one of them.


def test_the_integrity_entry_point_names_a_duplicate_durations_key(tmp_path, monkeypatch) -> None:
    """Wiring: `--integrity`'s composed problems include the registry walk.

    Non-vacuous both ways — `safe_load` (what `load_manifest()` uses) keeps the
    LAST row and reports nothing, while the same file makes `--integrity` exit 1
    naming the duplicate. Remove the `duplicate_mapping_key_issues()` term from
    the composition and the message disappears from the output.

    ⛔ THE PLANTED ROW CARRIES A DIFFERENT VALUE FROM THE REAL ONE, and that is
    load-bearing (#10648 review P3). Planted with the SAME value the assertion
    below held under EITHER resolution order, so it pinned only "the key
    parses" and would have stayed green if the loader flipped to first-wins —
    the arm that matters, because a `merge=union` duplicate leaves the STALE
    row in the file and only the resolution order decides whether the shard
    packer reads the fresh weight or the superseded one. With a different value
    planted ahead of the real row, `safe_load` returning the real weight IS
    last-wins and any first-wins loader returns the planted one and reds here.

    The planted value is DERIVED from the real one instead of hard-coded, the
    shape `tests/test_ci_timing.py` uses for the same property (#10726 review
    P3). The row this test shadows is an ADOPTED CI reading — its note says
    "MEASURED by the 2026-10-26 sweep (run 40259128208)" and ends "Was 2.0."
    — so the next `--refresh-durations` sweep may rewrite its weight, and a
    hard-coded figure would then red here as a complaint about this FIXTURE
    rather than about the duplicate the test exists to catch.
    """
    from tools import ci_selection

    text = ci_selection.MANIFEST.read_text(encoding="utf-8")
    key = "test_7818_plan_record_5362.py"
    anchor = f"  {key}: "
    real_value = yaml.safe_load(text)["durations"][key]
    # derive a value that provably DIFFERS from the real one, whatever it is
    other_value = 0.9 if real_value != 0.9 else 0.8
    dup = f"  {key}: {other_value}  # duplicate planted by this test\n"
    at = text.index(anchor)
    planted = text[:at] + dup + text[at:]
    victim = tmp_path / "ci-surfaces.yml"
    victim.write_text(planted, encoding="utf-8")

    # the loader the gate uses cannot see it …
    assert sum(1 for line in planted.splitlines()
               if line.startswith(anchor)) == 2
    assert yaml.safe_load(planted)["durations"][key] == real_value
    assert real_value != other_value, (
        "the planted value must differ from the row it shadows, otherwise "
        "last-wins and first-wins are indistinguishable here and the "
        "assertion above proves nothing")

    # … but the composed `--integrity` does, by name.
    assert any(f"duplicate mapping key '{key}'" in problem
               for problem in ci_selection.duplicate_mapping_key_issues(victim))

    import contextlib
    import io

    def _run_integrity() -> tuple[int, str]:
        out, err = io.StringIO(), io.StringIO()
        monkeypatch.setattr(sys, "argv", ["ci_selection.py", "--integrity"])
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ci_selection.main()
        return rc, out.getvalue() + err.getvalue()

    rc, seen = _run_integrity()          # the REAL manifest is clean …
    assert rc == 0, seen
    assert "duplicate mapping key" not in seen

    monkeypatch.setattr(ci_selection, "MANIFEST", victim)
    rc, seen = _run_integrity()          # … and the planted one is not
    assert rc == 1, seen
    assert f"duplicate mapping key '{key}'" in seen


def test_the_integrity_entry_point_is_clean_on_the_real_manifests() -> None:
    """Positive control: the shipped manifest carries no duplicate mapping key."""
    from tools import ci_selection

    assert ci_selection.duplicate_mapping_key_issues() == []
