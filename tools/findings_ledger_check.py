#!/usr/bin/env python3
"""Doc↔tree guard for `docs/architecture/MEMORY-SUBSTRATE-FINDINGS-LEDGER.md` (#2852).

Stdlib only. Reads one document, one registration file, and asks `git` one
question. No DB, no network.

Why this exists
---------------
The ledger's §3 is a **corrections log**: twelve claims the 2026-09-09/10
memory-substrate review made and then falsified or narrowed, kept so nobody
re-learns them the hard way. Re-measuring it at `f70d884b7` found **five of the
twelve had been overtaken by the very fixes they motivated** — so the artifact's
whole job is to tell a reader *which* of its own rows are still live.

That makes three things load-bearing in a way that is invisible to a reader:

1. **The verdict is a closed vocabulary.** A verdict outside the defined set is a
   claim class with no handling rule — exactly how a superseded correction gets
   read as a current fact. An invented word must red, not pass.
2. **A citation that does not resolve looks identical to evidence.** The page is
   the review's record of "declared ≠ stored" (its defect pattern 1); a
   `path:line` that points at nothing is that same defect *in the artifact about
   it*. It must red, because prose cannot.
3. **The population must be pinned.** Deleting a correction row — and every
   mention of it — satisfies every referential check there is. So the row count
   is pinned like `#2820`'s status table is, and §6's arithmetic is re-derived
   from the table rather than trusted.

What this does NOT check, deliberately
--------------------------------------
The issue states in §6, the durability column in §4, and every `~/.swarm` claim
in §5 are **dated readings of things outside this repository**. A guard over them
would either red on someone else's tracker closing an issue, or pass by not
looking — and the second is worse. This follows `SUBSTRATE-REBUILD-MAP.md`'s
ruling that the tracker half is dated and carries its command instead of a pin.
The one exception is §6's *arithmetic* (counts must reconcile with the population
and with each row's own list), which is self-contained and therefore checkable.

Exit codes
----------
``0``  clean — every claim holds.
``1``  drift — a named claim is false. The message names the claim.
``2``  could not verify — the doc is unreadable, the population cannot be parsed,
       or `git` cannot answer the ancestry question (a shallow/grafted clone).
       ``2`` is never "clean": a guard that cannot run must not report OK.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DOC = "docs/architecture/MEMORY-SUBSTRATE-FINDINGS-LEDGER.md"
INDEX = "docs/00_index.md"

#: The correction rows are `a`..`l`. PINNED: a deleted row removes the only
#: record that a claim was falsified, and every referential check still passes.
PINNED_CORRECTION_LABELS: tuple[str, ...] = tuple("abcdefghijkl")

#: The closed verdict vocabulary. A verdict is the cell's first bolded token.
VERDICTS: tuple[str, ...] = (
    "STILL-TRUE",
    "SUPERSEDED-BY-FIX",
    "ADOPTED",
    "DRIFTED",
    "MOOT-BY-LOSS",
)

#: The indexed population: 40 issues + 2 PRs, per §6's own three columns.
PINNED_INDEXED_ROWS = 42

#: §6's per-state rows, and the label each must carry in its first column.
STATE_ROWS: tuple[str, ...] = (
    "OPEN",
    "CLOSED COMPLETED",
    "CLOSED other",
    "MERGED",
)

#: `path/to/file.ext:LINE` or `path/to/file.ext:START-END`, inside backticks.
CITATION_RE = re.compile(
    r"`(?!\*\*)([\w][\w./+-]*\.(?:py|md|yml|yaml|json|jsonc|ts|sh))"
    r":(\d+)(?:-(\d+))?`"
)

CORRECTION_ROW_RE = re.compile(r"^\|\s*\*\*([a-z])\*\*\s*\|")
# A verdict cell may be `**DRIFTED**`, `**DRIFTED, and much further**`, or
# `**STILL-TRUE as a rule; MOOT-BY-LOSS as an object**` — so the rule is that the
# cell must BEGIN with a verdict token, not that the whole bold span is one.
BOLD_TOKEN_RE = re.compile(r"\*\*\s*([A-Z][A-Z-]*)")
STATE_ROW_RE = re.compile(
    r"^\|\s*\**\s*(?P<label>" + "|".join(re.escape(s) for s in STATE_ROWS) + r")\b"
    r"[^|]*\|\s*\**\s*(?P<count>\d+)\s*\**\s*\|\s*(?P<which>[^|]*)\|"
)


class Unverifiable(Exception):
    """Cannot answer — exit 2, never a pass."""


def read_doc(root: Path = REPO) -> str:
    path = root / DOC
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:  # unreadable is not clean
        raise Unverifiable(f"cannot read {DOC}: {exc}") from exc


def _region(text: str, start: str, end: str) -> str:
    """The text between two headings, or Unverifiable if either is missing.

    A section that cannot be found must not silently check nothing: an absent
    section is the strongest form of drift and would otherwise pass.
    """
    i = text.find(start)
    j = text.find(end)
    if i < 0 or j < 0 or j <= i:
        raise Unverifiable(f"cannot locate section boundaries {start!r} .. {end!r}")
    return text[i:j]


def check_verdicts(text: str) -> list[str]:
    """§3: exactly the pinned labels, each carrying a member of the closed set."""
    problems: list[str] = []
    section = _region(text, "## 3. The corrections log", "## 4. The artefact index")
    rows = [ln for ln in section.splitlines() if CORRECTION_ROW_RE.match(ln)]

    labels = [CORRECTION_ROW_RE.match(ln).group(1) for ln in rows]
    if tuple(labels) != PINNED_CORRECTION_LABELS:
        problems.append(
            f"§3 population drift: expected rows {''.join(PINNED_CORRECTION_LABELS)}, "
            f"found {''.join(labels) or '(none)'} — a row that is deleted takes with "
            "it the only record that its claim was falsified"
        )

    for label, line in zip(labels, rows, strict=True):
        cells = line.split("|")
        # | # | claim | correction | verdict | evidence | -> cells[0] == ''
        verdict_cell = cells[4] if len(cells) > 4 else ""
        tokens = BOLD_TOKEN_RE.findall(verdict_cell)
        if not tokens:
            problems.append(
                f"row ({label}): no bolded verdict — every correction row must "
                f"state one of {', '.join(VERDICTS)}"
            )
        elif tokens[0] not in VERDICTS:
            problems.append(
                f"row ({label}): verdict {tokens[0]!r} is outside the closed "
                f"vocabulary {', '.join(VERDICTS)} — an undefined verdict is a "
                "claim class with no handling rule"
            )
    return problems


def _line_count(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return None


def check_citations(text: str, root: Path = REPO) -> list[str]:
    """Every backticked `path:line` must resolve, and the line must be in range."""
    problems: list[str] = []
    seen: set[str] = set()
    for match in CITATION_RE.finditer(text):
        rel, start, end = match.group(1), int(match.group(2)), match.group(3)
        token = match.group(0)[1:-1]
        if token in seen:
            continue
        seen.add(token)
        target = root / rel
        if not target.is_file():
            problems.append(f"citation {token}: {rel} does not exist in the tree")
            continue
        total = _line_count(target)
        if total is None:
            problems.append(f"citation {token}: {rel} exists but cannot be read")
            continue
        last = int(end) if end else start
        if start < 1 or last > total or last < start:
            problems.append(
                f"citation {token}: line {start}{'-' + end if end else ''} is out "
                f"of range for {rel} ({total} lines)"
            )
    if not seen:
        raise Unverifiable("no `path:line` citations found — the guard measured nothing")
    return problems


def check_registration(root: Path = REPO) -> list[str]:
    """`docs/00_index.md` must carry a row naming this document."""
    try:
        index = (root / INDEX).read_text(encoding="utf-8")
    except OSError as exc:
        raise Unverifiable(f"cannot read {INDEX}: {exc}") from exc
    if DOC.split("/")[-1] not in index:
        return [
            f"{INDEX} carries no row naming {DOC} — an unregistered architecture "
            "doc is one a lane cannot find"
        ]
    return []


def check_state_arithmetic(text: str) -> list[str]:
    """§6: counts must match each row's own list, and sum to the pinned population."""
    problems: list[str] = []
    section = _region(text, "## 6. The issue index", "## 7. What this changes")
    found: dict[str, int] = {}
    for line in section.splitlines():
        match = STATE_ROW_RE.match(line)
        if not match:
            continue
        label, count, which = match.group("label"), int(match.group("count")), match.group("which")
        if label in found:
            problems.append(f"§6: state row {label!r} appears twice — ambiguates the arithmetic")
            continue
        found[label] = count
        listed = len(re.findall(r"`#\d+`", which))
        if listed != count:
            problems.append(
                f"§6 row {label!r}: declares {count} but its own list names {listed} "
                "issue(s) — the count is not derived from the list it sits beside"
            )

    missing = [s for s in STATE_ROWS if s not in found]
    if missing:
        raise Unverifiable(f"§6: state row(s) {missing} absent — arithmetic cannot be derived")
    total = sum(found.values())
    if total != PINNED_INDEXED_ROWS:
        problems.append(
            f"§6 arithmetic: the state rows sum to {total}, but the pinned indexed "
            f"population is {PINNED_INDEXED_ROWS} (40 issues + 2 PRs) — a row moved "
            "state without the population being reconciled"
        )
    return problems


def check_measured_commit(text: str, root: Path = REPO) -> list[str]:
    """`measured_at_commit` must be an ancestor of HEAD; exit 2 when git cannot say."""
    match = re.search(r"^measured_at_commit:\s*(\S+)\s*$", text, re.MULTILINE)
    if not match:
        return ["frontmatter carries no `measured_at_commit` — an undated measurement is a memory"]
    sha = match.group(1).strip()
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return [f"`measured_at_commit: {sha}` is not a hex commit id"]

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=60, check=False,
        )

    resolved = git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if resolved.returncode != 0:
        raise Unverifiable(
            f"commit {sha} is not present in this clone — a grafted or shallow "
            "checkout cannot answer this claim (the `docs` job checks out at "
            "fetch-depth: 0 for exactly this question)"
        )
    verdict = git("merge-base", "--is-ancestor", resolved.stdout.strip(), "HEAD")
    if verdict.returncode == 1:
        return [f"`measured_at_commit: {sha}` is NOT an ancestor of HEAD — the page claims a cut it did not make"]
    if verdict.returncode != 0:
        raise Unverifiable(f"git merge-base --is-ancestor exited {verdict.returncode}: {verdict.stderr.strip()}")
    return []


def check_all(root: Path = REPO) -> list[str]:
    text = read_doc(root)
    problems: list[str] = []
    problems.extend(check_verdicts(text))
    problems.extend(check_citations(text, root))
    problems.extend(check_registration(root))
    problems.extend(check_state_arithmetic(text))
    problems.extend(check_measured_commit(text, root))
    return problems


# ── self-test ──────────────────────────────────────────────────────────────
# Each arm injects one mutation into a COPY of the real document and asserts the
# checker reports it. A guard whose mutations are not proven to fire is decoration.

MUTATIONS: tuple[tuple[str, Callable[[str], str], str], ...] = (
    (
        "an invented verdict",
        lambda t: t.replace("| **STILL-TRUE** | `tortoise/sdk.py:16962`",
                            "| **PROBABLY-TRUE** | `tortoise/sdk.py:16962`", 1)
                  .replace("**STILL-TRUE**", "**PROBABLY-TRUE**", 1),
        "closed vocabulary",
    ),
    (
        "a citation pointing at a file that does not exist",
        lambda t: t.replace("`tortoise/pipeline.py:16`", "`tortoise/no_such_module.py:16`", 1),
        "does not exist",
    ),
    (
        "a citation line past the end of its file",
        lambda t: t.replace("`tortoise/pipeline.py:16`", "`tortoise/pipeline.py:999999`", 1),
        "out of range",
    ),
    (
        "a deleted correction row",
        lambda t: "\n".join(
            ln for ln in t.splitlines() if not ln.startswith("| **l** |")
        ),
        "population drift",
    ),
    (
        "a §6 count that no longer matches its own list",
        lambda t: t.replace("| **OPEN** | **4** |", "| **OPEN** | **5** |", 1),
        "declares 5 but its own list names 4",
    ),
)


def self_test(root: Path = REPO) -> int:
    original = read_doc(root)
    base = check_all(root)
    if base:
        print("self-test: the UNMUTATED document already reports problems:")
        for problem in base:
            print("  -", problem)
        return 1

    failures = 0
    for name, mutate, expect in MUTATIONS:
        mutated = mutate(original)
        if mutated == original:
            print(f"self-test: mutation {name!r} did not change the document — the arm is dead")
            failures += 1
            continue
        try:
            problems = _check_mutated(mutated, root)
        except Unverifiable as exc:
            print(f"self-test: mutation {name!r} raised Unverifiable ({exc}) — expected drift, not a refusal")
            failures += 1
            continue
        if not any(expect in p for p in problems):
            print(f"self-test: mutation {name!r} was NOT caught (expected {expect!r}); got {problems}")
            failures += 1
        else:
            print(f"self-test: caught {name!r}")

    # Negative control: a cosmetic edit must leave the verdict unchanged. An arm
    # that reds on a rewrapped paragraph is a guard a lane learns to route around.
    cosmetic = original.replace("**Read §3 first", "**Read § 3 first", 1)
    if cosmetic == original:
        print("self-test: negative control did not change the document")
        failures += 1
    else:
        try:
            problems = _check_mutated(cosmetic, root)
        except Unverifiable as exc:
            print(f"self-test: negative control raised Unverifiable ({exc})")
            failures += 1
        else:
            if problems:
                print(f"self-test: negative control CHANGED the verdict: {problems}")
                failures += 1
            else:
                print("self-test: negative control left the verdict unchanged")

    print(f"self-test: {'FAIL' if failures else 'PASS'} ({failures} failing arm(s))")
    return 1 if failures else 0


def _check_mutated(text: str, root: Path) -> list[str]:
    problems: list[str] = []
    problems.extend(check_verdicts(text))
    problems.extend(check_citations(text, root))
    problems.extend(check_registration(root))
    problems.extend(check_state_arithmetic(text))
    problems.extend(check_measured_commit(text, root))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify the ledger against the tree (default)")
    parser.add_argument("--self-test", action="store_true", help="prove the checker still catches its mutations")
    parser.add_argument("--root", default=str(REPO), help="repository root (tests pass a tmp tree)")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        if args.self_test:
            return self_test(root)
        problems = check_all(root)
    except Unverifiable as exc:
        print(f"findings-ledger: UNVERIFIED — {exc}", file=sys.stderr)
        return 2

    if problems:
        for problem in problems:
            print(f"findings-ledger: {problem}")
        print(f"findings-ledger: {len(problems)} problem(s) in {DOC}")
        return 1
    print(f"findings-ledger: ok — {DOC} agrees with the tree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
