#!/usr/bin/env python3
"""The memory-substrate problem map's drift guard (#2820).

`docs/architecture/MEMORY-SUBSTRATE-PROBLEM-MAP.md` is the status half of issue
#2820 — 26 named defects, each marked VERIFIED-FIXED / OPEN / UNKNOWN. The map
exists because the ISSUE went stale: its body was written on 2026-09-10 and still
reads as 26 problems waiting, while the tree had fixed 20 of them. A document
whose whole purpose is *"is this still broken?"* is worthless the moment nothing
can tell that its answers have aged out — that is the drift this tool measures.

What it re-measures, offline, from the tree in front of it:

  1. **Taxonomy completeness** — every tracked issue appears exactly once, and no
     row names an issue outside the tracked set. A silently dropped row is a
     CLEAN over nothing: the page would report 25 problems while claiming 26.
  2. **Status vocabulary** — each row's status is exactly one of the three
     tokens. A fourth spelling ("FIXED?", "partially") is how a map stops being
     countable.
  3. **Anchors resolve** — every anchor is a hex object `git cat-file -e` can
     read, and — when history is available — is an **ancestor of `HEAD`**. The
     ancestor half distinguishes a **merged** commit from one that is only on a
     branch; ⛔ it does NOT certify that the commit is *the fix*, and a lane that
     reads it that way ships a false PASS (this page did, on #2749, where the
     anchor was an unrelated merge that mentioned neither the issue nor the
     failing suite — the fix is to read the anchor, and #2749 now cites the two
     commits that actually closed its two root causes).
  4. **Pins resolve** — every pin path exists, resolves to a FILE (a directory
     satisfies `exists()` and pins nothing), and a pin that is a test module
     contains a `def test_`. A pin whose file survives but whose assertions were
     deleted pins nothing, which is the failure mode the pin column exists for.
  5. **Summary counts** — the numbers in §2 equal the rows that follow. A summary
     that drifts from its own table is the same defect class as the missing
     `content_hash` this map's Pattern 1 names.
  6. **Registration** — the page is listed in `docs/00_index.md`, so it is
     reachable rather than merely committed — **and the index entry's own
     headline counts match §2's**, because the index is what most readers see and
     a stale count there is the same defect as a stale count here (found by the
     verification gate on this page's first landing: the index said
     `22 fixed · 4 open` while the map said `21 fixed · 5 open`, and the filename
     check was happy).

⛔ **TWO RESIDUALS THIS TOOL CANNOT DECIDE — stated, not implied:**

- **Anchor relevance** (see check 3): existence + ancestry, never "is this the
  fix". Reading the commit is the reviewer's job.
- **Pin relevance**: a pin resolves and — for a `tests/*.py` module — declares at
  least one test function (the `_DEF_TEST` floor, which a FIXTURE named `test_*`
  also satisfies). It cannot prove those tests assert *this* defect: a pin whose
  assertions were deleted still passes, and a pin on a *prose* claim (#2730's
  brief-wording test is the real instance) passes as a pin for a behaviour that
  was never built. `#2730` shipped as VERIFIED-FIXED for exactly that reason
  until a reviewer read the anchor and the file; it is now OPEN.

⚠️ **Shallow clones.** `actions/checkout` fetches ONE commit by default. The
anchors' objects are then absent and the ancestor question is unanswerable
*THERE*. That is reported as `UNVERIFIED` — a state error (exit 2), never drift,
because the document is not wrong, the clone is shallow. The ancestor claim is
proved by the required `docs` job (full history), which is where this guard's
own registration points.

Usage:
    python tools/problem_map_check.py [--check] [--doc PATH] [--repo PATH] [--json]

`--check` is the verify-only spelling the `docs` job uses for this family of
guards (`tools/substrate_map_check.py --check`). Verify-only is the ONLY mode
this tool has, so the flag selects nothing and exists so the required `docs` job
can invoke its guards in one uniform shape — a bare invocation would be a
second, differently-spelled rail if it meant something else.

Exit codes: 0 = the document and the tree agree; 1 = drift; 2 = a state error the
run could not answer from (missing document, no git, shallow clone).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

DOC_REL = "docs/architecture/MEMORY-SUBSTRATE-PROBLEM-MAP.md"
INDEX_REL = "docs/00_index.md"

#: The taxonomy #2820 filed: 26 entries across four defect patterns and five
#: workstreams (WS-A…WS-E). This is the map's CLAIM about what it covers, so it is stated in
#: code rather than parsed out of the document: a row that vanished from the
#: table must be reported, and a set derived from the table itself could never
#: report its own shrinking.
TRACKED: tuple[int, ...] = (
    2813, 2795, 2814, 2788, 2742,                      # WS-A — declared ≠ stored
    2786, 2783, 2767, 2727,                            # WS-B — canonical ≠ consistent
    2766, 2787, 2818, 2781,                            # WS-B — promised ≠ expressible
    2792, 2729, 2782, 2817, 2730, 2684,                # WS-C — fixed vs flexible
    2725, 2696, 2728, 2726,                            # WS-D — venture pack delivery
    2749, 2748, 2734,                                  # WS-E — repo / CI hygiene
)

STATUSES: tuple[str, ...] = ("VERIFIED-FIXED", "OPEN", "UNKNOWN")

#: A table row: `| #2813 | … | VERIFIED-FIXED | `0643bba86` | `tests/x.py` |`
#: Five cells, so a line that starts and ends with `|` splits into 7 parts.
_ROW = re.compile(r"^\|\s*#(\d+)\s*\|")
_SHA = re.compile(r"`([0-9a-f]{7,40})`")
_PATH = re.compile(r"`([^`]+)`")
_SUMMARY = re.compile(r"^\|\s*(VERIFIED-FIXED|OPEN|UNKNOWN)\s*\|\s*(\d+)\s*\|")
#: The TOTAL row. It is a fourth count and nothing read it, so a document could
#: state "Total tracked 30" over a 26-row table and this tool still said CLEAN —
#: found by review on the version that already guarded the other three counts, so
#: the rule is: EVERY count the document prints is compared, or it is not a count.
_TOTAL = re.compile(r"^\|\s*\*\*Total tracked\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|",
                    re.MULTILINE)

#: A test function, INDENTED OR NOT: most of this repo's tests are methods on a
#: class, so a bare `^def test_` anchor would call 12 of this map's own pins
#: "pins nothing" (measured on the first run of the anchor, which is why it is
#: written this way). `async def` is included — an async test is still collected.
#: ⚠️ RESIDUAL, stated rather than hidden: a pytest FIXTURE named `test_*` at
#: column 0 satisfies this floor too (`tests/conftest.py:1048` defines
#: `def test_user`), so the check proves the module DECLARES a test function —
#: not that pytest COLLECTS one. Tighter would mean running a collector against
#: every pin on every guard run; the floor catches the case that actually
#: happens (a pin moved onto a helper/`conftest`/`__init__` module with no tests
#: at all), and the residual is a naming convention violation, not a stale map.
_DEF_TEST = re.compile(r"^\s*(?:async\s+)?def test_", re.MULTILINE)


@dataclass
class Row:
    issue: int
    defect: str
    status: str
    anchors: list[str]
    pins: list[str]
    line: int

    @property
    def is_fixed(self) -> bool:
        return self.status == "VERIFIED-FIXED"


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)
    #: "The run could not ask the question at all" — a missing document, no git.
    #: Kept apart from `errors` (DRIFT, a claim the document is WRONG) for the
    #: reason the shallow-clone arm exists: a tool that cannot read its input has
    #: not measured it, so the verdict it owes is "no answer" (exit 2) — never
    #: "the map disagrees with the tree", which is the diagnosis a reader acts on.
    state_errors: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)

    @property
    def code(self) -> int:
        if self.errors:
            return 1
        if self.state_errors or self.unverified:
            return 2
        return 0


# ── parsing ──────────────────────────────────────────────────────────────────


def parse_rows(text: str) -> tuple[list[Row], list[str]]:
    """Every `| #N | … |` table row in the document, plus per-line complaints."""
    rows: list[Row] = []
    bad: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if not _ROW.match(line):
            continue
        cells = line.split("|")
        # ['', item, defect, status, anchor, pin, ''] — a cell containing a
        # literal pipe would break this, and it must break LOUDLY rather than
        # silently shift the status into the pin column.
        if len(cells) != 7:
            bad.append(
                f"line {lineno}: table row has {len(cells) - 2} cells, expected 5 "
                f"(a `|` inside a cell?) — {line.strip()[:90]}"
            )
            continue
        issue = int(_ROW.match(line).group(1))
        anchor_cell, pin_cell = cells[4], cells[5]
        rows.append(
            Row(
                issue=issue,
                defect=cells[2].strip(),
                status=cells[3].strip(),
                anchors=_SHA.findall(anchor_cell),
                pins=[p for p in _PATH.findall(pin_cell) if p.strip()],
                line=lineno,
            )
        )
    return rows, bad


def parse_summary(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in text.splitlines():
        m = _SUMMARY.match(line)
        if m:
            counts[m.group(1)] = int(m.group(2))
    return counts


# ── git ──────────────────────────────────────────────────────────────────────


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """A git call that cannot CRASH the run out of its documented exit contract.

    `git` absent (a scratch image, a box whose PATH was replaced) and a hung call
    both used to raise (`FileNotFoundError`, `TimeoutExpired`) straight out of
    `main()`, so python's own traceback exit — **1** — was returned: the DRIFT
    code, over a document that was never read. Both are state errors. The rc used
    here (127, the shell's "command not found") makes every caller's existing
    "could not answer" arm fire.
    """
    try:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # FileNotFoundError (no git), PermissionError, TimeoutExpired, ...
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=127, stdout="", stderr=str(exc)
        )


def commit_exists(repo: Path, sha: str) -> bool:
    return _git(repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


def is_ancestor(repo: Path, sha: str) -> bool | None:
    """True/False, or **None** when git could not answer.

    `merge-base --is-ancestor` exits 1 for "not an ancestor" and 128 for "that is
    not a commit / HEAD is unborn / the repo is unreadable". Collapsing both to
    `False` reported a git FAILURE as `the fix did not land on this branch` — a
    drift claim with a wrong cause — so the unanswerable case is separated and
    routed to UNVERIFIED, the same direction `is_shallow`'s `None` is.
    """
    res = _git(repo, "merge-base", "--is-ancestor", sha, "HEAD")
    if res.returncode == 0:
        return True
    if res.returncode == 1:
        return False
    return None


def is_shallow(repo: Path) -> bool | None:
    """True/False, or None when git cannot answer (never defaulted to False).

    A `False` here certifies the ancestor check, so an unreadable answer must not
    be allowed to produce one.
    """
    res = _git(repo, "rev-parse", "--is-shallow-repository")
    if res.returncode != 0:
        return None
    answer = res.stdout.strip().lower()
    if answer == "true":
        return True
    if answer == "false":
        return False
    return None


# ── checks ───────────────────────────────────────────────────────────────────


def check_rows(rows: list[Row]) -> list[str]:
    errors: list[str] = []
    seen: dict[int, int] = {}
    for row in rows:
        seen[row.issue] = seen.get(row.issue, 0) + 1
        if row.status not in STATUSES:
            errors.append(
                f"#{row.issue}: status {row.status!r} is not one of {', '.join(STATUSES)}"
            )
        if row.is_fixed:
            if not row.anchors:
                errors.append(
                    f"#{row.issue}: VERIFIED-FIXED with no anchor — a fix claim needs "
                    f"a commit reachable from HEAD"
                )
            if not row.pins:
                errors.append(
                    f"#{row.issue}: VERIFIED-FIXED with no pin — nothing in the tree "
                    f"would fail if the fix were undone"
                )
    for issue, count in sorted(seen.items()):
        if count > 1:
            errors.append(f"#{issue}: appears in {count} rows (exactly one expected)")
    tracked = set(TRACKED)
    for issue in sorted(tracked - set(seen)):
        errors.append(f"#{issue}: tracked by the map but MISSING from every table")
    for issue in sorted(set(seen) - tracked):
        errors.append(
            f"#{issue}: row names an issue outside the tracked taxonomy "
            f"(#2820's 26 entries) — add it to TRACKED deliberately, or drop the row"
        )
    return errors


def check_pins(repo: Path, rows: list[Row]) -> list[str]:
    errors: list[str] = []
    for row in rows:
        for pin in row.pins:
            path = repo / pin
            if not path.exists():
                errors.append(f"#{row.issue}: pin {pin!r} does not exist at this head")
                continue
            if not path.is_file():
                # A DIRECTORY satisfies `exists()` and pins nothing: no assertion
                # lives in it, so a row could be VERIFIED-FIXED against it and
                # never fail. Silent `continue` here until PR #11384's review round 5 found it.
                errors.append(
                    f"#{row.issue}: pin {pin!r} is not a file — a pin must be an "
                    f"artifact that fails if the fix is undone"
                )
                continue
            if pin.startswith("tests/") and pin.endswith(".py"):
                body = path.read_text(encoding="utf-8", errors="replace")
                if not _DEF_TEST.search(body):
                    errors.append(
                        f"#{row.issue}: pin {pin!r} is a test module with no `def test_` "
                        f"— it pins nothing"
                    )
    return errors


def check_anchors(repo: Path, rows: list[Row], report: Report) -> list[str]:
    errors: list[str] = []
    if not rows:
        return errors
    shallow = is_shallow(repo)
    for row in rows:
        for sha in row.anchors:
            if not commit_exists(repo, sha):
                if shallow is not False:
                    report.unverified.append(
                        f"#{row.issue}: anchor {sha} is not in this clone "
                        f"(shallow or unknown — history unavailable here)"
                    )
                else:
                    errors.append(f"#{row.issue}: anchor {sha} is not a commit")
                continue
            if shallow is True:
                report.unverified.append(
                    f"#{row.issue}: anchor {sha} — ancestor-ness unverifiable in a "
                    f"shallow clone"
                )
                continue
            ancestor = is_ancestor(repo, sha)
            if ancestor is None:
                report.unverified.append(
                    f"#{row.issue}: anchor {sha} — git could not answer the ancestry "
                    f"question, so the drift verdict is withheld"
                )
                continue
            if shallow is None:
                # An unreadable shallow answer must not certify drift: report it.
                report.unverified.append(
                    f"#{row.issue}: anchor {sha} — could not read the clone's "
                    f"completeness, so the drift verdict is withheld"
                )
                continue
            if not ancestor:
                errors.append(
                    f"#{row.issue}: anchor {sha} is NOT an ancestor of HEAD — the fix "
                    f"did not land on this branch"
                )
    return errors


def check_summary(text: str, rows: list[Row]) -> list[str]:
    errors: list[str] = []
    counts = parse_summary(text)
    if not counts:
        return ["no summary table found (§2's `| STATUS | n |` rows)"]
    for status in STATUSES:
        declared = counts.get(status)
        if declared is None:
            errors.append(f"summary is missing a row for {status}")
            continue
        actual = sum(1 for r in rows if r.status == status)
        if declared != actual:
            errors.append(
                f"summary says {declared} {status} row(s); the tables contain {actual}"
            )
    total = _TOTAL.search(text)
    if total is None:
        errors.append("summary is missing its `| **Total tracked** | **n** |` row")
    elif int(total.group(1)) != len(rows):
        errors.append(
            f"summary's Total tracked says {total.group(1)}; the tables contain "
            f"{len(rows)} row(s)"
        )
    return errors


#: The index row's headline counts. The phrasing is asserted, not guessed: a
#: rewrite that drops it is reported (see `check_registration`).
_INDEX_COUNTS = re.compile(
    r"\*\*(\d+) fixed · (\d+) open · (\d+) unknown\*\*"
)


def check_registration(repo: Path, doc: Path, text: str | None = None) -> list[str]:
    """Registered, AND the index entry's counts agree with the document's §2.

    The count half is not decoration. The index is the artifact most readers
    meet first, and on this page's first landing it said `22 fixed · 4 open`
    while §2 said `21 fixed · 5 open` — `--check` was CLEAN, because a filename
    match proves reachability and nothing about agreement. The verification gate
    caught it; this check is that catch, mechanised.
    """
    index = repo / INDEX_REL
    if not index.exists():
        return [f"{INDEX_REL} does not exist — cannot check registration"]
    body = index.read_text(encoding="utf-8", errors="replace")
    if doc.name not in body:
        return [
            f"{DOC_REL} is not listed in {INDEX_REL} — a committed page nobody can "
            f"reach is not a map"
        ]
    errors: list[str] = []
    if text is None:
        text = doc.read_text(encoding="utf-8") if doc.exists() else ""
    summary = parse_summary(text)
    if not summary:
        return errors  # `check_summary` owns the missing-summary complaint
    # ⛔ BIND THE COUNT TO THIS DOCUMENT'S OWN ROW. `_INDEX_COUNTS.search(body)`
    # over the whole index was the first version, and a fresh reviewer broke it in
    # one move: an EARLIER row carrying the same phrasing satisfied the check while
    # this document's row was stale — a FALSE PASS, in the check written to stop
    # exactly that. `docs/00_index.md` is a file of long summary rows and the
    # sibling maps are the kind that could carry these words first, so the regex
    # is applied to the ONE line that names this file.
    # The ENTRY, not a mention: an index row ends with the document it registers
    # (``… | `docs/architecture/X.md` |``), so the path must sit in the LAST cell.
    # Two earlier versions failed here and both were caught by review, not by the
    # suite: searching the whole file let ANY row's counts answer (#1), and taking
    # the FIRST line naming the file let a "supersedes `X.md`" cross-reference
    # answer (#2). `docs/00_index.md` rows already cross-reference other docs by
    # bare filename, so a mention is one "see also" away from a false PASS.
    entry = [
        line for line in body.splitlines()
        if line.rstrip().endswith(f"`{doc.name}` |")
        or line.rstrip().endswith(f"`{DOC_REL}` |")
    ]
    if len(entry) != 1:
        errors.append(
            f"{INDEX_REL} has {len(entry)} row(s) ENDING with `{DOC_REL}` — the "
            f"registration must be exactly one row whose last cell is the path "
            f"(a mention elsewhere is not a registration, and two rows are ambiguous)"
        )
        return errors
    declared = _INDEX_COUNTS.search(entry[0])
    if declared is None:
        errors.append(
            f"{INDEX_REL}'s entry for {doc.name} states no `**N fixed · N open · "
            f"N unknown**` headline — the counts a skimming reader takes away are "
            f"unverifiable"
        )
        return errors
    got = {s: int(n) for s, n in zip(STATUSES, declared.groups(), strict=True)}
    for status in STATUSES:
        if got[status] != summary.get(status):
            errors.append(
                f"{INDEX_REL} says {got[status]} {status}; the map's §2 says "
                f"{summary.get(status)} — the two disagree, and the index is the "
                f"half most readers see"
            )
    return errors


def run(repo: Path, doc: Path) -> Report:
    report = Report()
    if not doc.exists():
        # A state error, not drift: the document was not read, so nothing about it
        # has been measured. Reported as DRIFT, a renamed page reads as "the map
        # disagrees with the tree" — the wrong diagnosis, on the exit code a
        # reader is told means "the document is wrong".
        report.state_errors.append(f"{doc} does not exist — nothing was measured")
        return report
    try:
        text = doc.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # The SAME channel as a missing document, because it is the same state:
        # the run could not READ what it measures. Reachable with `--doc` pointed
        # at a directory (`IsADirectoryError`), an unreadable file
        # (`PermissionError`) or a non-UTF-8 file (`UnicodeDecodeError`) — each
        # raised straight out of here, so python's traceback exit (**1**) was the
        # answer: "the map disagrees with the tree", about a file never parsed.
        report.state_errors.append(
            f"{doc} could not be read ({exc}) — nothing was measured"
        )
        return report
    rows, bad = parse_rows(text)
    report.rows = rows
    report.errors.extend(bad)
    if not rows:
        report.errors.append("the document contains no `| #N |` table rows")
        return report
    report.errors.extend(check_rows(rows))
    report.errors.extend(check_pins(repo, rows))
    report.errors.extend(check_anchors(repo, rows, report))
    report.errors.extend(check_summary(text, rows))
    report.errors.extend(check_registration(repo, doc, text))
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--doc", default=None)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify-only (the only mode; present so the docs job's guard block "
        "invokes every guard the same way)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    doc = Path(args.doc).resolve() if args.doc else repo / DOC_REL
    report = run(repo, doc)

    if args.json:
        print(
            json.dumps(
                {
                    "code": report.code,
                    "rows": len(report.rows),
                    "errors": report.errors,
                    "state_errors": report.state_errors,
                    "unverified": report.unverified,
                },
                indent=2,
            )
        )
    else:
        print(f"{doc} — {len(report.rows)} row(s) parsed")
        for err in report.errors:
            print(f"  DRIFT     {err}")
        for err in report.state_errors:
            print(f"  NO ANSWER {err}")
        for note in report.unverified:
            print(f"  UNVERIFIED {note}")
        if report.code == 0:
            print("VERDICT: CLEAN — the map and the tree agree")
        elif report.state_errors:
            print("VERDICT: NO ANSWER — the run could not read what it measures")
        elif report.code == 2:
            print("VERDICT: UNVERIFIED — no drift found, but history could not answer")
        else:
            print(f"VERDICT: DRIFT — {len(report.errors)} disagreement(s)")
    return report.code


if __name__ == "__main__":
    sys.exit(main())
