"""`MEMORY.md` is an INDEX with a cap and one home per lesson — pinned, not assumed.

Hermetic: reads one file from the repo, no DB, no docker, no network, no model.

The defect this guards, measured at `75400a23a` (PR #10841, review Finding 1): the
fold's forward-merge union carried `[text-scanning-pin]:` **twice**, at
`MEMORY.md:136` and `MEMORY.md:138`, and `cmp` on the two lines was BYTE-IDENTICAL
(`sha256 f5b10174cc6849ba5f167d48e0bb34a78c6e9bbc1e2edb9285160f61eb287fb9`) — the
constituent added it once and the merge re-added main's copy next to it. Nothing
redded: no test in the tree read `MEMORY.md` as an index, so a merge-union
duplicate landed silently in an artifact whose `AGENTS.md` contract is

    `MEMORY.md` must stay under 150 lines.
    `MEMORY.md` = raw coding gotchas only … Format: `[category]: [what broke] → …

Two identical rows spend two cap slots (the file sat at 143 with the duplicate)
and give one lesson two homes, so "where is that gotcha recorded?" has two
answers that can drift apart independently. This is exactly the class the fold's
own last commit fixed for a code table (`a pin for the DUPLICATED unit row the
merge union left`) — and it sat outside that check's denominator because the
check enumerated test names, not documentation.

The two falsification directions, because a detector that fires on the wrong
shape gets deleted and takes the real finding with it:

  (a) IT FIRES on a byte-identical duplicate entry line — the merge-union shape.
  (b) IT DOES NOT FIRE on a repeated CATEGORY TAG with a different body. That is
      the normal state of this index (`[ci]`, `[git]`, `[test]` … each carry
      several distinct lessons), and flagging it would red the file for being
      what `AGENTS.md` asks it to be.

Run standalone:      python3 tests/test_memory_md_hygiene.py
Run under pytest:    python3 -m pytest tests/test_memory_md_hygiene.py -q
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MEMORY = ROOT / "MEMORY.md"

#: `AGENTS.md` → Memory Hygiene: "must stay under 150 lines". Strictly under, so
#: 149 is the largest legal size and this is the number the file is measured at.
LINE_CAP = 150

#: An index ENTRY: `[category]: …`. Deliberately permissive about the tag body
#: (the index already carries `[record tests]` / `[merge-lag red]`), because this
#: pin is about duplicates and the cap, not about tag spelling — a stricter regex
#: would red the file for a pre-existing shape and teach the next author to
#: weaken the pin.
_ENTRY = re.compile(r"^\[.*?\]: ")


def entry_lines(text: str) -> list[str]:
    """The index entries in `text`, in file order (blank lines and the header excluded)."""
    return [ln for ln in text.splitlines() if _ENTRY.match(ln)]


def duplicate_entries(text: str) -> dict[str, list[int]]:
    """Map each BYTE-IDENTICAL repeated entry line to its 1-based line numbers.

    `repr` of the whole line is the key: two rows that differ by a single
    character are two lessons, and `AGENTS.md` gives no uniqueness contract over
    a tag. Only an exact repeat is the merge-union artifact.
    """
    seen: dict[str, list[int]] = {}
    for n, line in enumerate(text.splitlines(), start=1):
        if _ENTRY.match(line):
            seen.setdefault(line, []).append(n)
    return {line: ns for line, ns in seen.items() if len(ns) > 1}


# ── the real artifact ────────────────────────────────────────────────────────


def test_memory_md_exists_at_the_repo_root():
    assert MEMORY.is_file(), f"{MEMORY} is the index `AGENTS.md` mandates; it is missing"


def test_no_entry_appears_twice_byte_identically():
    """The pre-fix tree fails HERE: at `75400a23a` this reported lines 136 and 138."""
    dups = duplicate_entries(MEMORY.read_text(encoding="utf-8"))
    assert not dups, (
        "MEMORY.md carries a BYTE-IDENTICAL duplicate entry — the merge-union "
        "artifact (a constituent added the row once and a forward-merge re-added "
        "main's copy beside it), which spends two cap slots on one lesson and "
        "gives one tag two homes:\n"
        + "\n".join(
            f"  lines {ns} — identical: {line[:120]}…"
            for line, ns in dups.items()
        )
        + "\nDelete all but one copy."
    )


def test_memory_md_stays_under_the_line_cap():
    text = MEMORY.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert len(lines) < LINE_CAP, (
        f"MEMORY.md is {len(lines)} lines; `AGENTS.md` → Memory Hygiene caps it "
        f"at under {LINE_CAP}. Fold the lesson into its existing tag or retire a "
        "row — the cap is what keeps this index an index."
    )


# ── the detector itself, so neither arm above can pass vacuously ────────────

_A = "[text-scanning-pin]: a pin that reads a file as TEXT also matches its own comment"
_B = "[ep-operator-promotion]: a draft operator whose endpoints are all already live"


def test_detector_fires_on_the_byte_identical_pair_this_pr_carried():
    """(a) Non-vacuity: the pristine shape that landed at `75400a23a`."""
    tree = "# MEMORY.md — coding gotchas\n\n" + f"{_A}\n{_B}\n{_A}\n"
    dups = duplicate_entries(tree)
    assert list(dups) == [_A], dups
    assert dups[_A] == [3, 5], dups[_A]


def test_detector_does_not_fire_on_a_repeated_tag_with_a_different_body():
    """(b) The counter-direction, and the state the real file is in."""
    tree = (
        "[ci]: a test green locally and red only in CI is a dependency drift\n"
        "[ci]: a workflow that reads `git describe` breaks on a shallow clone\n"
                + _A.split(": ", 1)[1] + "\n"
    )
    assert duplicate_entries(tree) == {}, duplicate_entries(tree)


def test_detector_ignores_the_header_and_blank_lines():
    """A duplicated PROSE line is not a duplicate LESSON — the header is not an entry."""
    tree = "# MEMORY.md — coding gotchas\n# MEMORY.md — coding gotchas\n\n\n"
    assert entry_lines(tree) == []
    assert duplicate_entries(tree) == {}


def test_line_cap_arm_reds_on_a_file_one_line_over():
    """(b') Non-vacuity for the cap: `LINE_CAP` is a real bound, not a constant."""
    over = "\n".join(f"[tag-{i}]: lesson {i}" for i in range(LINE_CAP)) + "\n"
    assert len(over.splitlines()) == LINE_CAP
    assert not len(over.splitlines()) < LINE_CAP
    at = "\n".join(f"[tag-{i}]: lesson {i}" for i in range(LINE_CAP - 1)) + "\n"
    assert len(at.splitlines()) < LINE_CAP


if __name__ == "__main__":
    import sys

    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
            except AssertionError as exc:  # pragma: no cover - standalone runner
                failures += 1
                print(f"FAIL {name}: {exc}")
            else:
                print(f"ok   {name}")
    sys.exit(1 if failures else 0)
