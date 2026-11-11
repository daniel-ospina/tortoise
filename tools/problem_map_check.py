#!/usr/bin/env python3
"""The memory-substrate problem map's drift guard (#2820).

`docs/architecture/MEMORY-SUBSTRATE-PROBLEM-MAP.md` is the status half of issue
#2820 — 26 named defects, each marked VERIFIED-FIXED / OPEN / UNKNOWN. The map
exists because the ISSUE went stale: its body was written on 2026-09-10 and still
reads as 26 problems waiting, while the tree had already fixed 21 of them (22 now
that #2782 is re-cut). A document
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
  5. **Summary counts** — §2's three `| STATUS | n |` rows, its `Total tracked`
     row, its five per-workstream `**N of M fixed**` cells (bound to their
     workstream BY LABEL), and the page's opening abstract (`**N fixed and N
     open**`) all equal the row table. A summary that drifts from its own table is
     the same defect class as the missing `content_hash` this map's Pattern 1
     names. The read set is EXACTLY: §2's three `| STATUS | n |` rows, §2's
     `Total tracked` row, §2's five `N of M` cells, and the page's opening
     abstract above `## 1.`; plus, in `check_registration`, the index row's own
     headline. Everything else the page prints is not read — §1's census
     sentence, the issue lists inside §2's cells, §8's sentence, §10's census —
     and `check_workstreams`'s docstring and §9 name each site rather than
     implying coverage.
  6. **Registration** — the page is listed in `docs/00_index.md`, so it is
     reachable rather than merely committed — **and the index entry's own
     headline counts match §2's**, because the index is what most readers see and
     a stale count there is the same defect as a stale count here (the
     registration half only asked that the filename appear, so a headline that
     contradicted §2 passed it; found by the verification gate on this page's
     first landing).

⛔ **RESIDUALS THIS TOOL CANNOT DECIDE — stated, not implied.**

- **A BROKEN object store can read as drift.** `git cat-file -e` exits 128
  both for its real negatives (no such object / not a commit) and for a store
  it cannot read (a corrupt loose object). The second is separated by matching
  git's own failure wording (`_OBJECT_STORE_FAILURE`) and reported UNVERIFIED —
  but that is TEXT matching, so a git release that rewords those messages
  reinstates the false drift. Pinned by a real corrupted object in
  `test_a_corrupt_object_store_reads_as_no_answer_not_as_drift`.
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

import sys

# The #5128 interpreter guard MUST precede every other import: a module-level
# 3.12-only import raises ImportError on an older interpreter BEFORE a later
# guard could run, and the refusal then looks like a broken tool rather than a
# floor. Print-then-`SystemExit(int)` is the shape the #5128 census's detector
# accepts (`tests/test_entry_point_python_guard.py`), and the code is **2**, not
# 1: this tool's `1` means DRIFT, so `raise SystemExit(<message>)` (which CPython
# exits 1 with) would report *the document and the tree disagree* about a
# document that was never read — a wrong instruction, not merely a wrong label.
# An old interpreter is the same class as a missing document or a shallow clone:
# this checkout cannot answer the question, so it is a state error. Mirrors
# `tools/substrate_map_check.py` and `tools/test_design_map_check.py`.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    print(
        f"tools/problem_map_check.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/problem_map_check.py`",
        file=sys.stderr,
    )
    raise SystemExit(2)

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

DOC_REL = "docs/architecture/MEMORY-SUBSTRATE-PROBLEM-MAP.md"
INDEX_REL = "docs/00_index.md"

#: The taxonomy #2820 filed: 26 entries across four defect patterns and five
#: workstreams (WS-A…WS-E). This is the map's CLAIM about what it covers, so it is stated in
#: code rather than parsed out of the document: a row that vanished from the
#: table must be reported, and a set derived from the table itself could never
#: report its own shrinking.
#:
#: Keyed by workstream as well as flattened, because §2 prints a per-workstream
#: "**N of M fixed**" line for each of the five — and a count nothing reads is a
#: count that rots (§8's own lesson). `#2786`, `#2783`, `#2767`, `#2727`
#: (canonical ≠ consistent) and `#2766`, `#2787`, `#2818`, `#2781` (promised ≠
#: expressible) are BOTH WS-B, which is why WS-B is eight rows in the map's table
#: and two patterns there.
#:
#: ⛔ ONE ROW IS REASSIGNED FROM #2820's OWN GROUPING, and it is recorded rather
#: than silently absorbed: `#2820` lists `#2726` in its WS-B row (nine issues) AND
#: cross-references it under WS-D as "`#2726`/`#2727` (in PR #2747)". The map
#: counts it ONCE, under WS-D (the delivery workstream it actually shipped in), so
#: WS-B is eight and WS-D four here. A grouping that double-counted it would make
#: 27 rows; one that dropped it would make 25.
WORKSTREAMS: dict[str, tuple[int, ...]] = {
    "WS-A": (2813, 2795, 2814, 2788, 2742),                      # declared ≠ stored
    "WS-B": (2786, 2783, 2767, 2727,                             # canonical ≠ consistent
             2766, 2787, 2818, 2781),                            # promised ≠ expressible
    "WS-C": (2792, 2729, 2782, 2817, 2730, 2684),                # fixed vs flexible
    "WS-D": (2725, 2696, 2728, 2726),                            # venture pack delivery
    "WS-E": (2749, 2748, 2734),                                  # repo / CI hygiene
}

TRACKED: tuple[int, ...] = tuple(
    issue for issues in WORKSTREAMS.values() for issue in issues
)

STATUSES: tuple[str, ...] = ("VERIFIED-FIXED", "OPEN", "UNKNOWN")

#: A table row: `| #2813 | … | VERIFIED-FIXED | `0643bba86` | `tests/x.py` |`
#: Five cells, so a line that starts and ends with `|` splits into 7 parts.
_ROW = re.compile(r"^\|\s*#(\d+)\s*\|")
_SHA = re.compile(r"`([0-9a-f]{7,40})`")
_PATH = re.compile(r"`([^`]+)`")
_SUMMARY = re.compile(r"^\|\s*(VERIFIED-FIXED|OPEN|UNKNOWN)\s*\|\s*(\d+)\s*\|")
#: §2 owns the counts. Parsing the WHOLE document and keeping the LAST match let a
#: later `| OPEN | 5 |` row substitute for §2's own number: measured — corrupt
#: §2's `| OPEN | 5 |` to `| OPEN | 3 |` and append
#: `## Appendix` + `| OPEN | 5 |`, and the check returned NO errors while §2
#: contradicted its own table (the index count check was fooled with it, because
#: it compares against the same parse). Every count is read from §2's body and
#: nowhere else — a decoy outside it cannot be a count at all.
_SUMMARY_SECTION = "## 2."
#: The TOTAL row. It is a fourth count and nothing read it, so a document could
#: state "Total tracked 30" over a 26-row table and this tool still said CLEAN —
#: found by review on the version that already guarded the other three counts, so
#: the rule is: every count the document prints in MACHINE-READABLE form is
#: compared, or it is not a count. The rule's edge is a READ SET, not a
#: table-cell/sentence split (the abstract is a sentence and IS read; a cell can
#: narrate a list that is not): the counts read are §2's three `| STATUS | n |`
#: rows, §2's `Total tracked` row, §2's five `N of M` cells, the page's opening
#: abstract, and the index row's headline (`check_registration`). Every other
#: count the page prints — §1's census sentence, the issue lists inside §2's
#: cells, §8, §10 — is named as a residual, not claimed.
_TOTAL = re.compile(r"^\|\s*\*\*Total tracked\*\*\s*\|\s*\*\*(\d+)\*\*\s*\|",
                    re.MULTILINE)
#: §2's per-workstream row, WITH its label: `| WS-A write-path integrity — … |
#: **5 of 5 rows fixed**; …`. The label is captured because binding the count to
#: a workstream by POSITION alone was measured to be defeatable: swap only the two
#: labels (`| WS-A …` ↔ `| WS-C …`) and leave every count in place, and the
#: pre-fix parse returned NO errors while §2 attributed WS-A's `5 of 5` to WS-C —
#: the exact edit a re-cut makes.
_WS_COUNT = re.compile(
    r"^\|\s*(WS-[A-Z])\b[^|]*\|[^|]*\*\*(\d+) of (\d+) (?:rows )?fixed\*\*",
    re.MULTILINE,
)
#: The page's opening abstract: "**22 fixed and 4 open**". It lives in the body's
#: opening blockquote, NOT in the frontmatter (whose keys are `title`,
#: `measured_at_commit`/`measured_at` and their siblings — it carries no
#: headline), so it is read from the text BEFORE `## 1.` — and an abstract that
#: carries the sentence twice is reported as ambiguous rather than resolved by
#: first-match, the `_TOTAL` lesson.
_HEADLINE = re.compile(r"\*\*(\d+) fixed and (\d+) open\*\*")
_HEADLINE_SCOPE = "## 1."

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

#: The rc `_git` mints when git could not RUN (absent, or the call timed
#: out). It is a sentinel, NOT a verdict: `cat-file -e` has exactly two verdicts
#: (0 = present, 128 = no such object / not a commit) and `merge-base
#: --is-ancestor` two (0 = ancestor, 1 = not). Everything else — this sentinel, a
#: negative rc from a child killed by a signal, 129 for a usage error, a wrapper
#: that exits 1 — is "git did not answer", and every caller maps it to `None`.
#: ⚠️ One rc carries BOTH of git's 128 meanings (a real negative, and an object
#: store it cannot read), which is why `commit_exists` inspects stderr on 128 and
#: why `None` there is not identical to this sentinel.
_GIT_UNRUNNABLE = 127

#: git's own wording for an object store it cannot READ (as opposed to a
#: pid/object it looked for and did not find). Measured on a corrupted
#: loose object: `error: inflate: data stream error (incorrect header
#: check)` + `error: unable to unpack <sha> header` / `fatal: loose object <sha> …
#: is corrupt`. A miss says
#: `fatal: Not a valid object name <sha>^{commit}` instead, which is a
#: real verdict and stays `False`.
_OBJECT_STORE_FAILURE = re.compile(
    r"inflate: data stream error|unable to unpack|unable to read sha1 file"
    r"|loose object .* is corrupt|object file .* is empty",
    re.IGNORECASE,
)


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


def _section(text: str, marker: str) -> str:
    """The text from `marker` up to the next `## ` heading at column 0.

    Same rule as `tools/substrate_map_check.py::_section` — one definition of
    "which section owns these numbers", so the two guards cannot disagree.
    """
    start = text.find(marker)
    if start < 0:
        return ""
    rest = text[start + len(marker):]
    nxt = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: nxt.start()] if nxt else rest


def parse_summary(text: str) -> dict[str, int]:
    """§2's `| STATUS | n |` rows — ONLY §2's, so a later row cannot substitute."""
    counts: dict[str, int] = {}
    for line in _section(text, _SUMMARY_SECTION).splitlines():
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
    here (`_GIT_UNRUNNABLE`, 127 — the shell's "command not found") is what BOTH
    callers map to "no answer": `is_ancestor` returns `None` for any rc outside
    {0, 1}, and `commit_exists` returns `None` for this sentinel **and** for a 128
    that carries an object-store failure (`_OBJECT_STORE_FAILURE`), while a plain
    128 stays the real negative `False`. Neither could be a bare `== 0` for that
    reason: a git failure is not a document defect.
    """
    try:
        return subprocess.run(
            ["git", *args], cwd=repo, capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # FileNotFoundError (no git), PermissionError, TimeoutExpired, ...
        return subprocess.CompletedProcess(
            args=["git", *args], returncode=_GIT_UNRUNNABLE, stdout="", stderr=str(exc)
        )


def commit_exists(repo: Path, sha: str) -> bool | None:
    """True/False, or **None** when git could not be ASKED.

    The mirror of `is_ancestor`'s tri-state, and it was the remaining hole in the
    same contract: `_git` mints rc 127 for "git could not run at all" (absent, or
    the call timed out), and a bare `== 0` turned that into `False` — so with a
    READABLE shallow answer (`rev-parse` says `false`) but an unanswerable
    `cat-file`, `check_anchors` took its `shallow is False` branch and reported
    `anchor X is not a commit`, i.e. **DRIFT over a run that measured no history**.
    Measured with only `subprocess.run` faked (shallow -> `false`, `cat-file` ->
    `TimeoutExpired`): 29 DRIFT errors, exit 1, about a correct document.
    """
    res = _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")
    if res.returncode == 0:
        return True
    if res.returncode == 128:
        # 128 is BOTH of git's negatives here — "no such object" and "not a
        # commit" (`<tree>^{commit}` peels and exits 128) — and ALSO the rc a
        # BROKEN object store exits with while `rev-parse` still answers, e.g.
        # `error: inflate: data stream error` on a corrupt loose object. Reading
        # that as `False` reproduces the false DRIFT this tri-state exists to
        # stop (the shallow answer is readable, so `check_anchors` takes its
        # `shallow is False` branch and blames the document), so the store's own
        # failure messages are separated out and reported as no answer.
        # ⚠️ RESIDUAL: this is TEXT matching on git's stderr. A git release that
        # rewords these turns the arm back into `False`, i.e. into the false
        # DRIFT — which is why the arm is recorded here and in §9 rather than
        # implied to be airtight.
        if _OBJECT_STORE_FAILURE.search(res.stderr or ""):
            return None
        return False
    return None


def is_ancestor(repo: Path, sha: str) -> bool | None:
    """True/False, or **None** when git could not answer.

    An ALLOW-LIST, not a deny-list: `merge-base --is-ancestor` has exactly two
    verdicts — 0 (an ancestor) and 1 (not) — and everything else (128 for "that is
    not a commit / unborn HEAD / unreadable object store", a negative rc from a
    signalled child, a wrapper's own exit code) is "git did not answer".
    Collapsing those to `False` reported a git FAILURE as `the fix did not land on
    this branch` — a drift claim with a wrong cause — so they are separated and
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


def _path_state(path: Path, report: Report | None, label: str) -> str:
    """`missing` | `file` | `not-file` | `unreadable` — never raises.

    `Path.exists()` and `Path.is_file()` answer by STATTING, and a stat through an
    UNREADABLE parent directory raises `PermissionError`: they swallow only the
    ENOENT family. Unguarded, that escapes `run()` into python's traceback exit
    (**1**) — the code this module defines as DRIFT, "the document and the tree
    disagree", about a path nothing ever measured. `chmod 000 tests/eval` and
    `chmod 000 docs` both did exactly that (#11384 review round 14), one call
    earlier than the guarded reads the same round closed.
    """
    try:
        if not path.exists():
            return "missing"
        return "file" if path.is_file() else "not-file"
    except OSError as exc:
        message = f"{label} could not be examined ({exc}) — nothing was measured"
        if report is not None:
            report.state_errors.append(message)
        return "unreadable"


def check_pins(repo: Path, rows: list[Row], report: Report) -> list[str]:
    errors: list[str] = []
    for row in rows:
        for pin in row.pins:
            path = repo / pin
            state = _path_state(path, report, f"#{row.issue}: pin {pin!r}")
            if state == "unreadable":
                continue
            if state == "missing":
                errors.append(f"#{row.issue}: pin {pin!r} does not exist at this head")
                continue
            if state == "not-file":
                # A DIRECTORY satisfies `exists()` and pins nothing: no assertion
                # lives in it, so a row could be VERIFIED-FIXED against it and
                # never fail. Silent `continue` here until PR #11384's review round 5 found it.
                errors.append(
                    f"#{row.issue}: pin {pin!r} is not a file — a pin must be an "
                    f"artifact that fails if the fix is undone"
                )
                continue
            if pin.startswith("tests/") and pin.endswith(".py"):
                try:
                    body = path.read_text(encoding="utf-8", errors="replace")
                except (OSError, UnicodeDecodeError) as exc:
                    # The SAME channel as an unreadable document (#11384 review): the
                    # run could not READ the pin, so it has not measured it. Letting
                    # this raise escapes `run()` into python's traceback exit (**1**)
                    # — "the map disagrees with the tree" — about a pin never parsed.
                    # `chmod 000` a live pin and the pre-fix tool reported DRIFT.
                    report.state_errors.append(
                        f"#{row.issue}: pin {pin!r} could not be read ({exc}) — "
                        f"nothing was measured"
                    )
                    continue
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
            exists = commit_exists(repo, sha)
            if exists is None:
                report.unverified.append(
                    f"#{row.issue}: anchor {sha} — git could not answer whether the "
                    f"object exists, so the drift verdict is withheld"
                )
                continue
            if not exists:
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
    total = _TOTAL.search(_section(text, _SUMMARY_SECTION))
    if total is None:
        errors.append("summary is missing its `| **Total tracked** | **n** |` row")
    elif int(total.group(1)) != len(rows):
        errors.append(
            f"summary's Total tracked says {total.group(1)}; the tables contain "
            f"{len(rows)} row(s)"
        )
    return errors


def check_workstreams(text: str, rows: list[Row]) -> list[str]:
    """§2's per-workstream "**N of M fixed**" lines and the page's abstract headline.

    WHY THIS EXISTS (found by fresh review at the post-merge head, reproduced): the
    summary check above compared only the three `| STATUS | n |` rows and the
    total, so the document could print `**7 of 8 rows fixed**` over a WS-B table
    that no longer had 7 fixed rows — and the numbers the guard DID compare could
    be edited to match, leaving §2 contradicting its own tables in three places on
    a CLEAN `docs`-job run. Flip `#2783` to OPEN and move the two summary rows one each
    way (`| VERIFIED-FIXED |` down one, `| OPEN |` up one — the counts were `21`/`5`
    when this was found, `22`/`4` today), and the pre-fix guard returned NO errors.

    A workstream's rows come from `WORKSTREAMS`, not from the document's own
    tables — same reason as `TRACKED`: a set parsed out of the artifact cannot
    report its own shrink. The count is bound to its workstream BY LABEL, not by
    position (a label-only swap was measured to pass a positional bind).

    RESIDUAL, stated rather than implied. The read set is: §2's three
    `| STATUS | n |` rows + `Total tracked` (in `check_summary`), §2's five
    `N of M` cells and the opening abstract (here), and the index row's headline
    (`check_registration`). What is NOT read:

      * §1's census sentence ("four rows where …"), which is a count over a
        criterion this guard cannot evaluate — it refuses to read the tracker,
        which is what that census is about;
      * the issue numbers NARRATED inside §2's cells (`(#2787 OPEN: …)`,
        `**#2730 and #2684 remain open**`, `**#2696 (epic) remains open**`)
        — deciding "which rows does this sentence call open" means reading prose,
        and a guard that parsed those sentences would be a spelling trap: measured
        at the post-merge head, flipping `#2684` and correcting the counts it DOES
        read leaves that cell stale and every check green;
      * §8's sentence ("Four rows, and three of them are defects") and §10's
        census prose, whose second clause additionally needs to know which row is
        an epic — the tracker half this guard refuses to read.

    All three are listed in §9. Nothing here claims otherwise.
    """
    errors: list[str] = []
    section = _section(text, _SUMMARY_SECTION)
    declared = [(name, int(fixed), int(size)) for name, fixed, size in
                _WS_COUNT.findall(section)]
    if len(declared) != len(WORKSTREAMS):
        errors.append(
            f"summary declares {len(declared)} workstream count(s); the taxonomy "
            f"has {len(WORKSTREAMS)} ({', '.join(WORKSTREAMS)})"
        )
        return errors
    labels = [name for name, _, _ in declared]
    if sorted(labels) != sorted(WORKSTREAMS):
        unknown = [n for n in labels if n not in WORKSTREAMS]
        missing = [n for n in WORKSTREAMS if n not in labels]
        errors.append(
            f"summary's workstream labels do not match the taxonomy — "
            f"unknown {unknown or 'none'}, missing {missing or 'none'} (a count is "
            f"bound to its workstream BY NAME, so a mislabelled row cannot pass)"
        )
        return errors
    by_issue = {r.issue: r for r in rows}
    for name, fixed, size in declared:
        issues = WORKSTREAMS[name]
        present = [by_issue[i] for i in issues if i in by_issue]
        actual_size = len(present)
        actual_fixed = sum(1 for r in present if r.status == "VERIFIED-FIXED")
        if size != actual_size:
            errors.append(
                f"summary says {size} {name} row(s); the taxonomy tracks "
                f"{actual_size} ({', '.join(str(i) for i in issues)})"
            )
        elif fixed != actual_fixed:
            errors.append(
                f"summary says {fixed} of {size} {name} row(s) fixed; the table "
                f"has {actual_fixed} VERIFIED-FIXED"
            )
    abstract = text.split(_HEADLINE_SCOPE, 1)[0]
    headlines = _HEADLINE.findall(abstract)
    headline = _HEADLINE.search(abstract)
    if len(headlines) > 1:
        errors.append(
            f"the page's opening abstract carries {len(headlines)} "
            f"`**N fixed and N open**` headlines — which one is the claim is "
            f"ambiguous, so nothing was compared"
        )
        return errors
    if headline is None:
        if _HEADLINE.search(text):
            errors.append(
                "the `**N fixed and N open**` headline sits BELOW `## 1.`, and "
                "only the text above that marker is read (frontmatter and opening "
                "abstract alike), so a headline further down is never compared — "
                "move it into the abstract or reword the abstract to carry it"
            )
        else:
            errors.append("the page carries no `**N fixed and N open**` headline")
    else:
        want_fixed = sum(1 for r in rows if r.status == "VERIFIED-FIXED")
        want_open = sum(1 for r in rows if r.status == "OPEN")
        if (int(headline.group(1)), int(headline.group(2))) != (want_fixed, want_open):
            errors.append(
                f"the headline says {headline.group(1)} fixed and "
                f"{headline.group(2)} open; the tables contain {want_fixed} "
                f"fixed and {want_open} open"
            )
    return errors


#: The index row's headline counts. The phrasing is asserted, not guessed: a
#: rewrite that drops it is reported (see `check_registration`).
_INDEX_COUNTS = re.compile(
    r"\*\*(\d+) fixed · (\d+) open · (\d+) unknown\*\*"
)


def check_registration(
    repo: Path, doc: Path, text: str | None = None, report: Report | None = None
) -> list[str]:
    """Registered, AND the index entry's counts agree with the document's §2.

    The count half is not decoration. The index is the artifact most readers
    meet first, and a headline there that disagrees with §2 is the same defect as
    a stale count in §2 — while a filename match proves reachability and nothing
    about agreement, so `--check` was CLEAN over it. The verification gate caught
    it on this page's first landing; this check is that catch, mechanised.
    """
    index = repo / INDEX_REL
    # Dispatch on the FULL state, not just `missing`: falling through an
    # `unreadable` index into the read guard below recorded the SAME fault twice
    # (`could not be examined`, then `could not be read`), so one unreadable
    # index read as two distinct faults (#11384 review round 15).
    index_state = _path_state(index, report, INDEX_REL)
    if index_state == "missing":
        return [f"{INDEX_REL} does not exist — cannot check registration"]
    if index_state == "unreadable":
        message = f"{INDEX_REL} could not be examined — nothing was measured"
        return [] if report is not None else [message]
    try:
        body = index.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError) as exc:
        # Same state-error channel, same reason as `check_pins` and `run()`'s own
        # document read: an index that cannot be READ is not an index that
        # disagrees. `docs/00_index.md` replaced by a directory raises
        # `IsADirectoryError` here and pre-fix exited **1** with a traceback.
        message = f"{INDEX_REL} could not be read ({exc}) — nothing was measured"
        if report is not None:
            report.state_errors.append(message)
            return []
        # A caller with no `report` to record into must NOT read this as
        # agreement: an empty list is byte-identical to "registered, counts
        # agree", so a read FAILURE would have been a silent PASS (#11384
        # review round 14).
        return [message]
    if doc.name not in body:
        return [
            f"{DOC_REL} is not listed in {INDEX_REL} — a committed page nobody can "
            f"reach is not a map"
        ]
    errors: list[str] = []
    if text is None:
        # This branch defends DIRECT callers of `check_registration` only: `run()`
        # always passes `text`, so it never arrives here — an earlier revision of
        # this comment claimed the opposite (that an explicit `--doc` skips
        # `run()`'s guard and made this second read load-bearing), which is false:
        # `main()` hands `--doc` straight to `run()`, whose own guard reads it.
        state = _path_state(doc, report, str(doc))
        if state == "unreadable":
            message = f"{doc} could not be examined — nothing was measured"
            return errors if report is not None else [message]
        if state == "missing":
            # The INDEX arm fails closed on its own `missing` case, so this arm
            # must too: `[]` is byte-identical to "registered, counts agree", and
            # a document that is not there has been measured by nothing
            # (#11384 review round 16).
            message = f"{doc} does not exist — nothing was measured"
            if report is not None:
                report.state_errors.append(message)
                return errors
            return [message]
        else:
            # `not-file` deliberately falls into the read: a directory at the
            # document path must hit the read guard and be recorded, not silently
            # become `text = ""` (which reported agreement for a directory —
            # #11384 review round 15).
            try:
                text = doc.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                message = f"{doc} could not be read ({exc}) — nothing was measured"
                if report is not None:
                    report.state_errors.append(message)
                    return errors
                return [message]
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
    if _path_state(doc, report, str(doc)) == "unreadable":
        return report
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
    report.errors.extend(check_pins(repo, rows, report))
    report.errors.extend(check_anchors(repo, rows, report))
    report.errors.extend(check_summary(text, rows))
    report.errors.extend(check_workstreams(text, rows))
    report.errors.extend(check_registration(repo, doc, text, report))
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
        # Branch on the CODE first, then on why. Ordering the state-error arm
        # before the drift arm made a run holding BOTH print "NO ANSWER" while
        # returning 1 (the DRIFT code) — the printed verdict contradicting the
        # exit status a caller reads, which is the one thing this block exists to
        # keep in sync (#11384 review round 14).
        if report.code == 0:
            print("VERDICT: CLEAN — the map and the tree agree")
        elif report.code == 1:
            print(f"VERDICT: DRIFT — {len(report.errors)} disagreement(s)")
        elif report.state_errors:
            print("VERDICT: NO ANSWER — the run could not read what it measures")
        else:
            print("VERDICT: UNVERIFIED — no drift found, but history could not answer")
    return report.code


if __name__ == "__main__":
    sys.exit(main())
