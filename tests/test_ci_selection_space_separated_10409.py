"""#10409: a space-separated `--changed-files` is a LIST, not one path.

THE DEFECT. `tools/ci_selection.py` split the value with `splitlines()` only, so
`--changed-files "a.py b.py"` became ONE entry whose text was the whole list.
That entry was then classified as a path, and the tool answered confidently
about a path that does not exist. Measured on `718ee3b49`:

    --changed-files "tortoise/hosted_api.py tortoise/sdk.py tests/test_ci_selection.py"
      -> surfaces=['api','onboarding']  full=false  378 test files
    the same three paths, newline-separated
      -> full=true, test_files=ALL

The wrong answer is therefore NOT always the `full: true` the issue reported. It
is whatever the FIRST token happens to prefix-match, and when that token is a
registered path the concatenation **under-selects**: `tortoise/sdk.py` is a
`SHARED_MODULE` — it forces the FULL matrix — and it was reported as NOT changed.
378 tests ran instead of the matrix: a gate that runs the wrong tests, quietly.
When the first token matches nothing, the same defect escalates to `full: true`
and measures nothing about the change. Both directions are asserted here.

WHY THE TESTS IN THIS FILE LOOK THE WAY THEY DO. The shipped tests only ever fed
the tool a NEWLINE list (or `-` on stdin), so the separator that breaks was never
an input. Every pin below feeds the shape a shell actually produces — a
space-separated value — and compares it against the newline form it must be
indistinguishable from.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SELECTOR = REPO / "tools" / "ci_selection.py"

#: The 22-path list from the issue's target 3. Mixed on purpose: `tortoise/sdk.py`
#: is a SHARED_MODULE (forces the FULL matrix), `tools/ci_selection.py` is a
#: TOOL_CARVEOUTS entry, `tests/*` take their surface's fallback, and
#: `docs/…`/`website/…` are dropped by `_keep_changed` — so the list exercises
#: every classification arm, not just one.
PATHS = [
    "tortoise/hosted_api.py",
    "tortoise/sdk.py",
    "tortoise/audit.py",
    "tortoise/mcp_auth.py",
    "tortoise/retrieval.py",
    "tortoise/consent_token.py",
    "tortoise/raw_state.py",
    "tortoise/cypher_guard.py",
    "tortoise/projection/edges.py",
    "tortoise/projection/entities.py",
    "tortoise/supabase_control.py",
    "tools/ci_selection.py",
    "tools/ci_queue_health.py",
    "tests/test_ci_selection.py",
    "tests/test_billing.py",
    "tests/test_about_edges.py",
    "tests/test_ep_selector.py",
    "config/ci-surfaces.yml",
    "docs/00_index.md",
    "docs/plans/2026-09-14-3467-trunk-green-honest-gate.md",
    "website/docs.html",
    ".github/workflows/ci.yml",
]


def _run(args: list[str], cwd: Path, stdin: str = "",
         artifact: str = "selection") -> subprocess.CompletedProcess:
    # Inherit the ambient env and override only what this pin controls (#10012:
    # a stripped env reds the pin on the runner's own layout instead of on the
    # selection). ONE artifact dir per invocation (review P2): the default dir is
    # a single file, so a second run silently OVERWRITES the first one's record
    # and an assertion about "the comma record" would really be reading the
    # newline record.
    env = {**os.environ,
           "CI_SELECTION_ARTIFACT_DIR": str(cwd / f".ci-selection-{artifact}")}
    return subprocess.run(
        [sys.executable, str(SELECTOR), *args],
        input=stdin, capture_output=True, text=True, cwd=str(cwd), env=env,
    )


def _selection(tmp_path: Path, name: str) -> dict:
    """The replayable `selection.json` ONE invocation wrote (see `_run`)."""
    return json.loads(
        (tmp_path / f".ci-selection-{name}" / "selection.json").read_text())


def _selected(tmp_path: Path, args: list[str], stdin: str = "",
              artifact: str = "selection") -> dict:
    r = _run(args, tmp_path, stdin=stdin, artifact=artifact)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


# ── the wrong answer, asserted in both directions ────────────────────────────

def test_space_separated_list_under_selecting_is_gone(tmp_path):
    """THE RED→GREEN PIN. `tortoise/sdk.py` is a SHARED_MODULE, so the FULL
    matrix is the right answer for this diff; the pre-fix tool answered
    `full=false, surfaces=['api','onboarding']` because the concatenated string
    prefix-matched `tortoise/hosted_api.py` and the other two entries were never
    seen as changed."""
    got = _selected(tmp_path, ["--changed-files",
                               "tortoise/hosted_api.py tortoise/sdk.py"])
    assert got["full"] is True, got
    assert got["test_files"] == "ALL", got


def test_space_separated_list_selects_exactly_the_newline_selection(tmp_path):
    """Issue target 3: 22 space-joined paths select the same as the newline form."""
    space = _selected(tmp_path, ["--changed-files", " ".join(PATHS)], artifact="space")
    space_record = _selection(tmp_path, "space")
    newline = _selected(tmp_path, ["--changed-files", "-"],
                        stdin="\n".join(PATHS) + "\n", artifact="newline")
    newline_record = _selection(tmp_path, "newline")
    assert space == newline
    # ...and the RECORD of the inputs agrees too, so a replay cannot disagree
    # with the answer it produced.
    assert space_record["changed_files"] == newline_record["changed_files"] == PATHS


def test_comma_separated_list_selects_exactly_the_newline_selection(tmp_path):
    got = _selected(tmp_path, ["--changed-files", ",".join(PATHS)], artifact="comma")
    comma_record = _selection(tmp_path, "comma")
    want = _selected(tmp_path, ["--changed-files", "-"],
                     stdin="\n".join(PATHS) + "\n", artifact="newline")
    assert got == want
    assert comma_record["changed_files"] == PATHS


def test_a_mixed_separator_list_is_split_on_all_three(tmp_path):
    """A value carrying newlines AND commas AND spaces — every separator is a
    separator, so no piece of any of them can survive as a path."""
    raw = f"{PATHS[0]},\n{PATHS[1]} {PATHS[2]}\n\n  {PATHS[3]}  ,"
    got = _selected(tmp_path, ["--changed-files", raw], artifact="mixed")
    mixed_record = _selection(tmp_path, "mixed")
    want = _selected(tmp_path, ["--changed-files", "-"],
                     stdin="\n".join(PATHS[:4]) + "\n", artifact="newline")
    assert got == want
    assert mixed_record["changed_files"] == PATHS[:4]


# ── the case a NAIVE fix (split on every space) still gets wrong ──────────────

@pytest.fixture
def parser(tmp_path, monkeypatch):
    """The tool's splitter, bound to a THROWAWAY repo root.

    In-process on purpose: `_names_existing_path` resolves against `REPO` only
    (a CWD-relative base was removed — it made the parse depend on the working
    directory, review P2), so the spaced-path fixtures live under a temp root
    rather than inside the real checkout.
    """
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import ci_selection
    finally:
        sys.path.pop(0)
    monkeypatch.setattr(ci_selection, "REPO", tmp_path)
    return ci_selection._split_changed_files


def test_a_path_containing_a_space_is_kept_whole(parser, tmp_path):
    """THE DISCRIMINATOR. `value.split()` 'fixes' the list by breaking every
    path that contains a space — and the pieces then read as unknown paths, so
    the tool escalates to the FULL matrix and the answer LOOKS like caution."""
    (tmp_path / "release notes.md").write_text("one path, with a space\n")
    assert parser("release notes.md") == ["release notes.md"]


def test_a_spaced_path_beside_a_spaced_list_keeps_both_meanings(parser, tmp_path):
    """Both shapes in ONE value, which is where an existence-blind splitter
    fails most visibly: the entry is a list, and one of its entries is a path
    with a space. A splitter that splits every space yields four tokens."""
    (tmp_path / "release notes.md").write_text("x\n")
    (tmp_path / "tortoise").mkdir()
    (tmp_path / "tortoise" / "sdk.py").write_text("x\n")
    assert parser("tortoise/sdk.py release notes.md tests/test_billing.py") == [
        "tortoise/sdk.py", "release notes.md", "tests/test_billing.py",
    ]


def test_a_path_containing_a_comma_is_kept_whole(parser, tmp_path):
    """The same predicate protects the OTHER separator (review P2): a whole line
    that names an existing path is one entry before any comma is folded, so the
    comma cannot silently become a separator for a path that carries one."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a,b.md").write_text("x\n")
    assert parser("docs/a,b.md") == ["docs/a,b.md"]
    # ...and a real comma SEPARATED list is still split.
    assert parser("docs/a,b.md other.py") == ["docs/a,b.md", "other.py"]


def test_a_comma_inside_a_space_separated_list_is_still_a_separator(tmp_path):
    """THE P1 THE REVIEW FOUND IN THE FIRST CUT OF THIS FIX, pinned.

    A token carrying a comma, inside a line that also carries a space
    (`"tortoise/hosted_api.py,tortoise/sdk.py tests/test_ci_selection.py"`), is a
    LIST. Kept whole it PREFIX-MATCHES the `tortoise/hosted_api.py` pattern, so it
    is not an unknown path, nothing warns, and `tortoise/sdk.py` — a SHARED_MODULE
    — is silently dropped: measured `full=false`, 885 tests instead of the matrix.
    """
    got = _selected(
        tmp_path,
        ["--changed-files", "tortoise/hosted_api.py,tortoise/sdk.py tests/test_ci_selection.py"],
        artifact="mixed_comma_space",
    )
    want = _selected(tmp_path, ["--changed-files", "-"],
                     stdin="tortoise/hosted_api.py\ntortoise/sdk.py\ntests/test_ci_selection.py\n",
                     artifact="newline")
    assert got == want
    assert got["full"] is True, got
    assert _selection(tmp_path, "mixed_comma_space")["changed_files"] == [
        "tortoise/hosted_api.py", "tortoise/sdk.py", "tests/test_ci_selection.py",
    ]


def test_a_path_containing_a_comma_in_a_whitespace_entry_is_kept_whole(parser, tmp_path):
    """The comma twin of the spaced-path case, at the TOKEN level: a token that
    names an existing path keeps its comma, and the solo form of the same value
    agrees with the in-list form (they disagreed while the token was stripped
    before the existence check)."""
    (tmp_path / "release, notes.md").write_text("x\n")
    assert parser("release, notes.md") == ["release, notes.md"]
    assert parser("release, notes.md tortoise/sdk.py") == [
        "release, notes.md", "tortoise/sdk.py",
    ]


# ── the forms that already worked must not move ──────────────────────────────

def test_the_documented_single_path_inline_form_is_unchanged(tmp_path):
    """`config/ci-surfaces.yml` documents `--changed-files tortoise/hosted_api.py`
    dozens of times. The split must not change what it selects."""
    got = _selected(tmp_path, ["--changed-files", "tortoise/hosted_api.py"],
                    artifact="single")
    assert got["surfaces"] == ["api", "onboarding"], got
    assert got["full"] is False, got
    assert _selection(tmp_path, "single")["changed_files"] == ["tortoise/hosted_api.py"]


def test_an_empty_value_still_means_an_empty_diff(tmp_path):
    got = _selected(tmp_path, ["--changed-files", ""], artifact="empty")
    assert got["full"] is False, got
    assert _selection(tmp_path, "empty")["changed_files"] == []


# ── the mutation this file exists to catch ───────────────────────────────────

def test_the_pre_fix_parse_would_fail_these_pins():
    """A pinned statement of the mutation, so a revert is a NAMED failure rather
    than a silently greener suite: the pre-fix parser (`splitlines()` only) maps
    the two paths onto ONE entry, which is exactly the wrong answer the pins
    above refuse."""
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import ci_selection
    finally:
        sys.path.pop(0)
    value = "tortoise/hosted_api.py tortoise/sdk.py"
    # what the tool does now
    assert ci_selection._split_changed_files(value) == [
        "tortoise/hosted_api.py", "tortoise/sdk.py",
    ]
    # what it did before #10409 — the mutation
    pre_fix = [l.strip() for l in value.splitlines() if l.strip()]  # noqa: E741
    assert pre_fix == [value]
    assert pre_fix != ci_selection._split_changed_files(value)
