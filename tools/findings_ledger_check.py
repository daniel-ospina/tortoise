#!/usr/bin/env python3
"""Doc↔tree guard for `docs/architecture/MEMORY-SUBSTRATE-FINDINGS-LEDGER.md` (#2852).

Stdlib only. Reads one document, one registration file, and asks `git` one
question. No DB, no network.

Why this exists
---------------
The ledger's §3 is a **corrections log**: twelve claims the 2026-09-09/10
memory-substrate review made and then falsified or narrowed, kept so nobody
re-learns them the hard way. Re-measuring it at `f70d884b7` found **seven of the
twelve no longer read as written** (four overtaken by the very fixes they
motivated, three by a number or a tracker state that moved) — so the artifact's
whole job is to tell a reader *which* of its own rows are still live.

That makes four things load-bearing in a way that is invisible to a reader:

1. **The verdict is a closed vocabulary.** A verdict outside the defined set is a
   claim class with no handling rule — exactly how a superseded correction gets
   read as a current fact. An invented word must red, not pass.
2. **A citation that does not resolve looks identical to evidence.** The page is
   the review's record of "declared ≠ stored" (its defect pattern 1); a
   `path:line` that points at nothing is that same defect *in the artifact about
   it*. It must red, because prose cannot. The page also uses the shorthand
   `:LINE` inside a cell (`classes at tortoise/commit_schema.py:395, :792, :1002`),
   which resolves against the nearest preceding full-path citation — measured, not
   skipped: leaving it unmatched exempted the load-bearing half of rows (b), (c)
   and (e) while §8 advertised them as checked.
3. **The population must be pinned.** Deleting a correction row — and every
   mention of it — satisfies every referential check there is. So the row count
   is pinned like `#2820`'s status table is, and §6's arithmetic is re-derived
   from the table rather than trusted.
4. **An unrecognised row shape is an ERROR, not silence.** Pins and citations
   select rows by shape, so a row shaped other than expected is simply not seen —
   which made a thirteenth correction row (or a fifth §6 bucket) free: measured,
   appending one returned CLEAN with the pin still reading `abcdefghijkl`. So §3
   and §6 now refuse any table row whose label cell they cannot classify, and so
   does §1 — the table this page names as the vocabulary's authority — where a row
   that is not a `**VERDICT**` row is a claim class with no handling rule (and was
   invisible to the vocabulary comparison, which only collects the rows that
   MATCH: measured, one inserted row left the guard at rc 0). Every
   backticked `token:LINE` the citation pattern declines is refused too — apart from
   the spellings §8 names as carve-outs, which are skipped BY DECLARATION and listed
   there rather than left to be discovered. The
   evidence column is held to §1's own rule for the same reason: with no floor on
   it, blanking every §3 evidence cell left the page green while each row went on
   asserting a verdict with nothing behind it.

What this does NOT check, deliberately
--------------------------------------
The issue states in §6, the durability column in §4, and every `~/.swarm` claim
in §5 are **dated readings of things outside this repository**. A guard over them
would either red on someone else's tracker closing an issue, or pass by not
looking — and the second is worse. This follows `SUBSTRATE-REBUILD-MAP.md`'s
ruling that the tracker half is dated and carries its command instead of a pin.
The one exception is §6's *arithmetic* (counts must reconcile with the population,
with each row's own list, and — for the survivor sentence — with the bullets
printed beneath it), which is self-contained and therefore checkable.

Exit codes
----------
``0``  clean — every claim holds.
``1``  drift — a named claim is false. The message names the claim.
``2``  could not verify — the doc is unreadable, the population cannot be parsed,
       or `git` cannot answer the ancestry question (a shallow/grafted clone).
       ``2`` is never "clean": a guard that cannot run must not report OK.

Which arms need history, and why that split exists
--------------------------------------------------
Exactly ONE arm needs `git` history: `measured_at_commit` ancestry. Everything
else — §1's vocabulary definition, §3's verdicts, the citations, the registration
row, §6's arithmetic — reads this checkout's tree and nothing more.

The split is load-bearing, not tidiness. The `docs` job (the REQUIRED gate, and
the only one that runs `--check`) checks out at `fetch-depth: 0`. The `test`
jobs check out at the default depth 1, and `tests/test_findings_ledger_2852.py`
is in the `core` surface, so it runs THERE. A depth-1 clone cannot resolve
`measured_at_commit` at all — measured: `git rev-parse --verify f70d884b7^{commit}`
prints nothing and exits 1, and `rev-parse --is-shallow-repository` says `true` —
so an arm that folded ancestry into every check would make every doc-level
assertion in that file unavailable in the job that actually runs it, or worse,
red it. `check_document()` is therefore the history-free half (and what
`--self-test` drives), and `check_all()` adds the one arm that needs a full
clone. Copy the sibling precedent rather than inventing one: `tools/problem_map_check.py`
reports ancestry as UNVERIFIED in a shallow clone, and `tests/test_substrate_map.py`
skips only its ancestry assertion for the same reason (#10406).

What that split does NOT license, and the trap to avoid: the `docs` job runs **no
pytest at all**, so a test that skips in a depth-1 clone is not "covered in the
docs job" — it is inert. The live ancestry claim is proved by the `docs` job's
`--check` invocation (which IS `check_all`), and the not-ancestor BRANCH is pinned
in every pytest job by a monkeypatched-`git` case
(`test_measured_commit_not_ancestor_of_head_is_rejected`) that needs no history.
Neither the skip's docstring nor the self-test comment may claim a test coverage
that no job supplies.
"""

from __future__ import annotations

import argparse
import bisect
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
#: The path is OPTIONAL so the page's shorthand `:LINE` form is matched too — a
#: shorthand nothing resolves is scope the guard silently did not measure, and §8
#: tells the reader every `path:line` in §3/§5 is checked. A shorthand is resolved
#: against the nearest PRECEDING full-path citation (see `check_citations`); the
#: required `:` right after the backtick is what keeps a plain backticked integer
#: (`` `42` ``) out of the match.
#:
#: The path is NOT restricted to an extension allow-list. It used to be
#: (`\.(?:py|md|yml|…)`), and that allow-list was a hole with three doors: a
#: citation to `notes/thing.txt:16`, to `Makefile:12`, or to a leading-dot path
#: such as `.github/workflows/ci.yml:892` matched NOTHING, so it was never
#: resolved. Measured: substituting any of the three for a resolving citation left
#: the guard reporting CLEAN, i.e. a real drift could be defused by reformatting
#: the very citation it pointed at. Any backticked `token:LINE` is now in scope.
CITATION_RE = re.compile(
    r"`(?!\*\*)(?P<path>[\w~/.@+-][\w./+@-]*)?"
    r":(?P<start>\d+)(?:-(?P<end>\d+))?`"
)

#: A token must carry a path marker (`/` or `.`) OR be the `:LINE` shorthand to be a
#: citation at all. `word:digits` with neither (`fetch-depth: 0`, `localhost:6379`,
#: `python3:11`) is PROSE, and treating it as a path reds correct text — measured:
#: a host:port example red the required `docs` job with "localhost does not exist in
#: the tree". The carve-out this creates is named in §8: a marker-less,
#: extension-less FILENAME citation (`Makefile:12`) is not measured.
CITATION_SHAPE_RE = re.compile(r"^[^`]*:\d+(?:-\d+)?$")

#: `path: 16962` — a space AFTER the colon. Same class, same measurement: it is
#: not a resolvable citation, so it must be refused rather than skipped.
CITATION_SPACED_SHAPE_RE = re.compile(r"^[^`]*:\s+\d+(?:-\d+)?$")

#: Trailing characters a code span may carry that are NOT part of the citation.
SPAN_TRAILING_RE = re.compile(r"[\s.,;:)\]]+$")

#: A `host:port` that must NOT be resolved as a citation — §8 promises this shape does
#: not red the required context, and `_has_path_marker` sees the dot in `127.0.0.1` or
#: `api.example.com` and calls it a path. Measured before this carve-out: swapping a
#: real citation for `127.0.0.1:6379` red "127.0.0.1 does not exist in the tree"; so did
#: `0.0.0.0:8000`, `api.example.com:443` and `db.internal:5432`. The shape is narrow on
#: purpose — a dotted HOST (an IPv4 literal or a name ending in a known top-level
#: label), a colon, and a port — because `tortoise/pipeline.py:16` also has a dot before
#: its colon and IS a citation.
HOST_PORT_RE = re.compile(
    r"(?:\d{1,3}(?:\.\d{1,3}){3}"
    r"|(?:[\w-]+\.)+(?:com|net|org|io|dev|ai|co|local|internal|localhost|test|example))"
    r":\d{2,5}"
)


def _is_host_port(token: str) -> bool:
    """Is this token a `host:port` rather than a `path:line`?"""
    return bool(HOST_PORT_RE.fullmatch(token.strip("`")))


#: `path/to/file.py 999999` — a citation whose DELIMITER is not `:`. Measured:
#: rewriting a resolving `path:16` to `path 16` left the page CLEAN — including over an
#: out-of-range line and over a path that does not exist — so one character of
#: reformatting defused the red the resolver would otherwise have raised. That one
#: spelling (and its `path 16-18` range form) is what this arm refuses; the wider
#: delimiter sets are the carve-outs named immediately below, WITH the measurement
#: that removed each. The one `#` case that is NOT a carve-out is the GitHub permalink
#: (`CITATION_HASH_LINE_RE`, `#` + `L` + digits): an `owner/repo#N` issue ref carries
#: no `L`, so that spelling is refused rather than skipped.
#:
#: ⚠️ THE CARVE-OUTS ARE MEASURED, AND THEY ARE NOT NARROW. An earlier version keyed
#: on an extension allow-list and claimed the carve-out was "exactly as wide as it
#: was"; both were wrong. An allow-list re-opens the skip door for `.gitignore 16`,
#: `docs/x.sql 16` and `README.MD 16`, and the bare `path.ext <digits>` shape also
#: matches a backticked COMMAND whose last argument is a number:
#: `uv run python tools/collision_preflight.py 2852 --repo .` is a legal §1 evidence
#: form. So the shape must be a PATH (a `/` or a dot-extension, dotfiles included), a
#: span that matches `COMMAND_RE` is a command and not a citation, a number followed by
#: `--flag` is an argument rather than a line (named in §8 as a carve-out, because it
#: is indistinguishable from a command without a recognised prefix), and prose with no
#: path marker (`localhost 6379`, `fetch-depth 0`) stays out of scope exactly as the
#: `key: value` carve-out above does. The tree-wide sweep is what bounded this: at this
#: head ONE span is refused across 801 `docs/` files — `collision_preflight.py 10984`,
#: a bare tool invocation the arm cannot tell from a path because it has no recognised
#: program prefix (the `uv run`-prefixed form IS excluded). None is on this page, and
#: the earlier delimiter sets refused 7, then 600+; each figure was re-measured rather
#: than carried forward, which is why this line states the current one.
#: THE DELIMITER IS WHITESPACE OR A STRAY `|`, and every wider version was measured
#: against a false accusation over `docs/**` before it was dropped:
#:   * `_` parsed the identifier `tests/test_findings_ledger_2852.py` as `path _ 2852`;
#:   * `-` collided with a dated slug (`2026-10-15-7879-postgres-…`) and a commit ref;
#:   * `/` parsed plain paths as citations — `docs/evidence/11083/README.md`,
#:     `2024/03/01` — 600+ spans across 801 files (re-derived, not carried);
#:   * `#` parsed issue refs (`daniel-ospina/tortoise#9363`, the form used everywhere
#:     in this repo) as `path # LINE`;
#:   * `@` parsed commit refs (`origin/main@3ac6b1636`), and `,` parsed SQL and
#:     function-call prose (`coalesce(n.version, 0)+1`) as citations.
#: The residue after those exclusions was 30 spans, still mostly prose. So the
#: WHITESPACE delimiter measures ONE spelling — `path <LINE>` — and §8 names
#: `path#LINE`, `path@LINE`, `path-LINE`, `path_LINE`, `path/LINE`, `path,LINE` and
#: `path:L LINE` as shapes it does NOT refuse. A rule that refuses a page's own plain
#: paths and issue refs is the false-accusation class one arm over, so the goal is an
#: honest boundary, not breadth.
CITATION_UNCOLONED_SHAPE_RE = re.compile(
    r"(?P<path>"
    r"(?<![\w.])\.[A-Za-z][\w.-]*"
    r"|[\w~][\w./+@-]*/[\w./+@-]+"
    r"|[\w~][\w./+@-]*\.[A-Za-z]\w*"
    r")"
    # A RANGE is the resolver's own `:START-END` form with a space instead of the
    # colon, and it must be refused: without the optional range this arm left
    # `tortoise/nope_module.py 16-18` CLEAN while the previous head refused it — a
    # regression from red to green in the one class the arm exists for (measured).
    #
    # The DELIMITER is whitespace OR a stray `|`. `|` was refused before the narrowing
    # and was the one delimiter the narrowing neither kept nor named in §8 — so
    # `path|999999` went from refused to CLEAN while §8's list presented itself as
    # complete (found by review, measured). It is safe to refuse on the same terms and
    # was measured for it: every code span in the 1146 tracked files under `docs/` plus
    # the 144 other tracked `*.md` carries ZERO `path|LINE` spans, so no prose here can
    # red on it (the single apparent match was a PNG read as text).
    r"(?:\s+|\s*\|\s*)(?P<num>\d{1,7})(?:\s*[-–]\s*\d{1,7})?(?![\w.>&|/]|-\w)"
)

def _uncoloned_is_exempt(body: str, u: re.Match) -> bool:
    """Is THIS `CITATION_UNCOLONED_SHAPE_RE` match one of the declared carve-outs?

    ⛔ PER MATCH, and module-scoped rather than a closure. Both halves are a fix for
    a measured red→green: the exemptions were tested against `search()`'s FIRST match
    only, so one exempt token certified every citation beside it in the same code
    span — `docs/index.html 200 tortoise/no_such_module.py 16` left the page CLEAN at
    every head before this one, while `tortoise/no_such_module.py 16` alone was
    refused. The identical shape was fixed one arm above for `#L` permalinks.

    It takes the span body as an argument instead of closing over the caller's loop
    variable because such a nested `def` is a `B023` under this repo's own
    `ruff select` — measured: `ruff check tools/` went from clean to 1 error.
    """
    # A number followed by a FLAG is an argument, not a line.
    if re.match(r"\s+--", body[u.end("num") :]):
        return True
    # `index.html 200` — a 100-599 status beside a web-page name. The only HTTP
    # shape that qualifies; `tortoise/pipeline.py 200` is still a line.
    num = u.group("num")
    return (
        len(num) == 3
        and 100 <= int(num) <= 599
        and u.group("path").endswith((".html", ".htm"))
    )


#: `path#L16` — the **GitHub permalink** spelling, and the ONE delimiter the sweep
#: above rejected for a reason that does not apply to it (#11952 P1). `#` alone is a
#: carve-out because it parses `owner/repo#9363` issue refs — the form this repo
#: writes everywhere — but an issue ref carries no `L`, so `#L` + digits is not
#: ambiguous with one, and a human copying a GitHub permalink writes exactly this.
#: Without the arm the token was INVISIBLE: `CITATION_RE` and every shape pattern
#: above need a `:`, so an out-of-range `path#L999999` and an unverifiable
#: `path#L16` both left the page CLEAN (measured — #11952), i.e. the likeliest
#: real-world spelling was also the one shape that looked verified and was not.
#: MEASURED, as every delimiter in this file must be: every code span in all 1146
#: tracked files under `docs/` was scanned with this exact pattern — ZERO matches, so
#: refusing it cannot red this repository's own prose. The bare-number form
#: (`path#16`) stays a declared carve-out: it is the one that collides with issue refs.
CITATION_HASH_LINE_RE = re.compile(
    r"(?P<path>[\w~.][\w./+@-]*?)\s*#\s*L\s*(?P<num>\d{1,7})(?![\w.>])"
)

#: A `path:line` occurring ANYWHERE inside a backticked span. The span-level
#: patterns above only see a span whose WHOLE body is a citation, so a legitimate
#: `out of range` red could be defused by appending one word INSIDE the span
#: (`tortoise/sdk.py:999999 (dead)`, `see …:999999 here`) — measured, rc 0.
INNER_CITATION_RE = re.compile(
    r"(?P<path>[\w~/.@+-][\w./+@-]*)\s*:\s*(?P<start>\d+)(?:-(?P<end>\d+))?"
)

#: `## N.` headings actually present, so a `§N` evidence reference can be checked
#: for existence rather than accepted as a token.
#:
#: ⛔ THE NUMBER IS `\d+`, NOT `\d`. A single-digit read parsed `§10` as `§1` and
#: discarded the `0`, and since §1 exists every two-digit reference to a section the
#: page does not have was accepted as evidence (measured: an evidence cell rewritten
#: to `§10` left `check_document()` at `[]`). Existence is only a check if the whole
#: number is read, and the pattern is the only thing that decides where it ends.
HEADING_NUMBER_RE = re.compile(r"(?m)^##\s+(\d+)\.")
SECTION_REF_TOKEN_RE = re.compile(r"§\s?(\d+)")


def _has_path_marker(token: str) -> bool:
    """Is this token shaped like a path at all (`/`, `.`, or the `:LINE` shorthand)?"""
    return "/" in token or "." in token or token.startswith(":")

#: A backticked command — the third evidence form §1 allows
#: (`gh issue view N`, `ls tortoise/semantic_extractor.py`).
COMMAND_RE = re.compile(
    r"^`(?:\.venv/bin/|python3?\b|gh\b|git\b|ls\b|bash\b|npm\b|npx\b|uv\b|grep\b|rg\b)"
)

#: A backticked issue/PR reference. `owner/repo#N` MUST count: §8 calls that spelling
#: "the form this repo writes everywhere", §1 accepts "an issue" without prescribing a
#: spelling — and the bare-`#N` pattern refused the repo's own dominant form as an
#: evidence cell with nothing openable behind it (measured: an issue-ref evidence cell
#: red `names no path:line, issue, command or §N` while the identical ref in prose was
#: accepted). An issue ref is not a citation, so this is deliberately NOT routed through
#: the citation arms.
ISSUE_REF_RE = re.compile(r"`(?:(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+))?#\d+`")

CORRECTION_ROW_RE = re.compile(r"^\|\s*\*\*([a-z])\*\*\s*\|")

#: A markdown table's header separator (`|---|---|`).
TABLE_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")
#: A row of §1's vocabulary table: `| **VERDICT** | Means | What a lane should do |`.
VOCAB_ROW_RE = re.compile(r"^\|\s*\*\*(?P<token>[A-Z][A-Z-]*)\*\*\s*\|")

#: §1's table header — the FIRST `|` row, and the only row in that table allowed to
#: carry no verdict. ⛔ A shape test, not a prefix test and not a position test: the
#: arm used to skip row 1 unconditionally, which exempted whatever sat there, so
#: `| SUPERSEDED-BY-DRIFT | Means | What a lane should do |` substituted for the
#: header left `check_document()` at `[]` while the same row one line lower reded
#: (measured, #11902 review P1) — a claim class with no handling rule, in the exact
#: class this arm exists for, at the one position no other arm can see.
VOCAB_HEADER_RE = re.compile(r"^\|\s*Verdict\s*\|", re.IGNORECASE)

#: Floor on the mutation population. `--self-test` is the arm the page's own
#: justification rests on (*"`--self-test` is what proves the rules still fire"*),
#: and `MUTATIONS = ()` is a ONE-place edit: without a floor it printed
#: "PASS (0 failing arm(s))" and exited 0 (measured), so every gate stayed green
#: over a self-test that proved nothing — a gate that reports OK when it cannot
#: run. `tools/test_design_map_check.py` ships the same floor for the same reason.
#: ⛔ IT IS AN EQUALITY, NOT A FLOOR THAT CAN SLIDE DOWN: 41 as of the figure arms
#: (`a falsified §3 count beside a list-free decoy`, `a falsified overtaken figure
#: beside a compliant decoy`), which closed the paragraph-scoped presence test. Bump
#: it with any arm added or removed — the population is what the pin compares.
MIN_SELF_TEST_MUTATIONS = 41
# A verdict cell may be `**DRIFTED**`, `**DRIFTED, and much further**`, or
# `**STILL-TRUE as a rule; MOOT-BY-LOSS as an object**` — so the rule is that the
# cell must BEGIN with a verdict token, not that the whole bold span is one.
BOLD_TOKEN_RE = re.compile(r"\*\*\s*([A-Z][A-Z-]*)")
STATE_ROW_RE = re.compile(
    r"^\|\s*\**\s*(?P<label>" + "|".join(re.escape(s) for s in STATE_ROWS) + r")\b"
    r"[^|]*\|\s*\**\s*(?P<count>\d+)\s*\**\s*\|\s*(?P<which>[^|]*)\|"
)

#: The words the survivor sentence may spell its two counts with.
NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
}

#: The guidance sentence under §6's table spells BOTH of its numbers as words
#: ("of the four survivors, two are already re-measured elsewhere", and later
#: ("of the four survivors, the two survivor bullets below are already re-measured
#: elsewhere"), so the count is matched as a NUMBER WORD anywhere between the total
#: and the verb — not at a fixed offset, because the page legitimately qualifies it
#: ("the two survivor bullets below") and a rigid pattern would refuse a correct
#: sentence. ⛔ It is matched ANYWHERE in §6, which is why `check_survivor_arithmetic`
#: requires exactly one match: with a second matching sentence the guard would
#: otherwise derive its numbers from whichever came first. Both
#: numbers are *derived* claims and neither was checkable before: the unguarded
#: version shipped `three` over a bullet list naming two survivors plus one
#: CLOSED issue while §7 still said `two` — two reviewers then disagreed about
#: which side was stale, and the "correction" one proposed (`two` -> `three` on
#: §7) would have written the false number into the page.
SURVIVOR_RE = re.compile(
    r"of the (?P<total>[a-z]+) survivors,[^.]*?\b(?P<count>"
    + "|".join(NUMBER_WORDS)
    + r")\b[^.]*?are already re-measured"
)

#: A bullet beneath that sentence: ``- **`#2728`** was re-measured …``. MULTILINE,
#: because `^` must anchor at a LINE start: without it the pattern only ever tried
#: the document's first byte and reported "no bullets" on a page that has three.
SURVIVOR_BULLET_RE = re.compile(r"^-\s+\*\*`#(?P<issue>\d+)`\*\*", re.MULTILINE)
ISSUE_ID_RE = re.compile(r"`#(\d+)`")


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


def _split_row_cells(line: str) -> list[str]:
    """Split a markdown table row on UNESCAPED pipes only.

    `\\|` is legal table syntax for a literal pipe inside a cell. A plain
    `line.split("|")` splits there too, shifts every later column and reports a
    correct row as "no bolded verdict" — a false red the reader cannot act on,
    and the shape a lane learns to route around.
    """
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "|":
            cells.append("".join(current))
            current = []
            continue
        current.append(char)
    cells.append("".join(current))
    return cells


def _table_rows(section: str) -> list[tuple[int, str]]:
    """Every DATA row of the section's first table, with its 1-based line offset.

    The first `|`-line is the header and the next is the separator; every `|`-line
    after them, up to the first line that is not a table row, is data.

    Returning the rows AS A POPULATION is the whole point. Selecting rows by "does
    it match the shape I expect" made an unrecognised row invisible rather than
    refused, which is how a THIRTEENTH correction row could be appended to a page
    whose §8 advertises a population pinned to twelve — measured: appending
    `| **M** | … | **FIXED-IT** | `tortoise/pipeline.py:16` |` returned CLEAN,
    exit 0, with the population pin still reading `abcdefghijkl`. The same hole
    existed in §6: a fifth state row was simply not seen, so the sum stayed at the
    pinned 42 while a bucket moved.
    """
    rows: list[tuple[int, str]] = []
    header_seen = False
    separator_seen = False
    for offset, line in enumerate(section.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("|"):
            if separator_seen:
                break
            continue
        if not header_seen:
            header_seen = True
            continue
        if not separator_seen:
            separator_seen = True
            if TABLE_SEPARATOR_RE.match(stripped):
                continue
        rows.append((offset, line))
    return rows


def check_verdicts(text: str) -> list[str]:
    """§3: exactly the pinned labels, each carrying a member of the closed set."""
    problems: list[str] = []
    section = _region(text, "## 3. The corrections log", "## 4. The artefact index")
    labels: list[str] = []

    for offset, line in _table_rows(section):
        match = CORRECTION_ROW_RE.match(line)
        if match is None:
            cells = _split_row_cells(line)
            first = next((c.strip() for c in cells if c.strip()), line.strip())
            problems.append(
                f"§3 row {offset}: label cell {first!r} is not `**<lowercase letter>**` "
                "— every row of this table is in scope, so a row shaped any other way "
                "is an undeclared correction the twelve-row population pin cannot see. "
                "Label it `**a**`..`**l**`, or delete it."
            )
            continue
        label = match.group(1)
        labels.append(label)
        cells = _split_row_cells(line)
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

    if tuple(labels) != PINNED_CORRECTION_LABELS:
        problems.append(
            f"§3 population drift: expected rows {''.join(PINNED_CORRECTION_LABELS)}, "
            f"found {''.join(labels) or '(none)'} — a row that is deleted takes with "
            "it the only record that its claim was falsified"
        )
    return problems


def _has_evidence(cell: str, sections: set[int]) -> bool:
    """§1's rule for an evidence cell: a `path:line`, an issue, a command, or a §N.

    `§N` must name a heading that EXISTS. Accepting any `§<digit>` let all twelve
    evidence cells be replaced with a reference to a section the page does not
    have (`§9`) — measured, rc 0 — which is the "emptied evidence column" bypass
    wearing a section number.
    """
    if ISSUE_REF_RE.search(cell):
        return True
    if any(int(n) in sections for n in SECTION_REF_TOKEN_RE.findall(cell)):
        return True
    for match in re.finditer(r"`[^`\n]+`", cell):
        body = match.group(0)
        # ⛔ A BARE shorthand does not name what can be opened — its path lives in
        # another cell or row (#11902 review P2). Measured: replacing §3 row (e)'s
        # evidence cell with `:5` satisfied every arm, because the marker test
        # accepted `:5` and `check_citations` resolved it against row (d)'s path.
        # §1 says the tool "refuses the page if an evidence cell names none of
        # `path:line`"; an anchor in a NEIGHBOUR is not this cell naming one.
        if body.startswith("`:"):
            continue
        # `CITATION_RE`'s path group is OPTIONAL, so `fullmatch` alone accepted the
        # marker-less `word:digits` spelling — measured: replacing an evidence cell with
        # `username:1234` (or `see row (c):3115`) left every gate green, i.e. the
        # evidence column could be filled with English. The marker test is the same one
        # `check_citations` applies, so the two cannot disagree about what a citation is.
        citation = CITATION_RE.fullmatch(body)
        if citation and _has_path_marker(citation.group("path") or ""):
            return True
        if COMMAND_RE.match(body):
            return True
        # `CITATION_SHAPE_RE` alone accepted ANY marker-less `word:digits` as a
        # `path:line` — measured, all twelve §3 evidence cells replaced with
        # `username:1234` left `--check` at rc 0, so the whole evidence column could be
        # made unopenable while §1 and §8 promise every cell names something a reader
        # can open. `check_citations` already requires the same marker on this shapes
        # family; this is the same test, applied to the evidence floor.
        if CITATION_SHAPE_RE.match(body[1:-1]) and _has_path_marker(body[1:-1]):
            return True
    return False


def check_evidence_cells(text: str) -> list[str]:
    """§3: every row's evidence cell names something that can be opened.

    Without this arm the whole evidence column can be EMPTIED into greenness: the
    verdict vocabulary still passes (it reads a different cell) and a citation
    check over zero citations has nothing to red — measured: blanking every §3
    evidence cell left the guard reporting CLEAN while each row went on asserting
    a verdict with no evidence at all. That is precisely the shape §1 calls "not
    evidence; it is a memory", and the bypass is the one that matters here: a real
    drift reds the REQUIRED `docs` job, and deleting the citation turns the red
    green.
    """
    problems: list[str] = []
    section = _region(text, "## 3. The corrections log", "## 4. The artefact index")
    sections = {int(n) for n in HEADING_NUMBER_RE.findall(text)}
    for offset, line in _table_rows(section):
        match = CORRECTION_ROW_RE.match(line)
        if match is None:
            continue  # check_verdicts owns the shape refusal
        cells = _split_row_cells(line)
        cell = cells[5] if len(cells) > 5 else ""
        if _has_evidence(cell, sections):
            continue
        problems.append(
            f"row ({match.group(1)}) at §3 line {offset}: its evidence cell names no "
            "`path:line`, issue, command or §N — §1 says a row whose evidence cannot "
            "be opened is not evidence, it is a memory"
        )
    return problems


#: Frontmatter/paragraph numbers are spelled out in the page's prose.
COUNT_WORDS: tuple[str, ...] = (
    "zero", "one", "two", "three", "four", "five", "six",
    "seven", "eight", "nine", "ten", "eleven", "twelve",
)

#: The paragraph whose tally the page states in words.
TAKEAWAY_MARKER = "The one thing to take away"

#: The two DERIVED FIGURES §3 states in words. Each must live in exactly one sentence, so
#: the comparison is made against the sentence a lane quotes rather than against the
#: paragraph (a decoy sentence otherwise satisfies it while the real one is stale).
_NUMBER_WORDS = "one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
FIGURE_RE = re.compile(
    rf"\b(?:{_NUMBER_WORDS}) of the (?:{_NUMBER_WORDS}) rows no longer read as written",
    re.IGNORECASE,
)
OVERTAKEN_FIGURE_RE = re.compile(
    rf"\b(?:{_NUMBER_WORDS}) of the (?:{_NUMBER_WORDS}) were\b", re.IGNORECASE
)


def _verdicts_by_label(text: str) -> dict[str, str]:
    """label -> first bolded verdict token, for every §3 row (empty on no token)."""
    section = _region(text, "## 3. The corrections log", "## 4. The artefact index")
    out: dict[str, str] = {}
    for _offset, line in _table_rows(section):
        match = CORRECTION_ROW_RE.match(line)
        if match is None:
            continue
        cells = _split_row_cells(line)
        verdict_cell = cells[4] if len(cells) > 4 else ""
        tokens = BOLD_TOKEN_RE.findall(verdict_cell)
        out[match.group(1)] = tokens[0] if tokens else ""
    return out


#: A letter a sentence names as a LIST MEMBER, in every spelling the candidate filter
#: accepts — plus the two markdown shapes it does not use for candidacy but a writer
#: does use for a list member: a bolded letter WITHOUT parentheses (`**a**`). ⛔ ONE
#: extractor for all of them, never `parens or plain`: `set(findall(parens)) or
#: set(findall(plain))` short-circuits, and the shipped tally sentence always carries
#: `**(c)**`, so the plain branch was DEAD — measured: appending `, and a, f too` to the
#: tally sentence left `--check` at rc 0 over a page naming two STILL-TRUE rows as
#: overtaken. The same dead-branch shape hid `**a**` and the complement arm entirely
#: (which read `\*\*\(x\)\*\*` only).
#:
#: Letters are restricted to `[a-l]` — the page's own twelve rows — which is what keeps
#: ordinary prose out: `is and`, `or a` and `to and fro` contain no `a`..`l` single-letter
#: form. The plain alternative is matched by its SEPARATOR (`,\s*` or `and\s+`) rather
#: than by lookahead so that the LAST member of a list (`… and a.`) is caught too.
#: The cue set is the words that INTRODUCE a member (`and`, `or`, `also`, `plus`, or a
#: comma): a bare letter elsewhere in prose is not collected, which is what keeps
#: `as a rule` out while still reaching `, also c`.
#:
#: ⛔ CODE AND ITALIC ARE MARKUP THE CANDIDATE GATE ACCEPTS, so the extractor must read
#: them too — otherwise a sentence can QUALIFY on its markup and then contribute no
#: letters, and the comparison silently under-counts. Measured at `96cfa33c86` and
#: `b12cd02f33` (independent probes, same result): appending `, and `a` too` or
#: `, and *a* too` to the tally sentence left `--check` at rc 0 over a page naming a
#: STILL-TRUE row as overtaken, and `**(j)**, and `c` still read as written` did the
#: same on the complement arm. §8 of the page says a letter may be written "bold, code,
#: italic or bare", so the doc promised a comparison the code did not make.
_NAMED_LETTER_RE = re.compile(
    r"\*\*\(([a-l]{1,2})\)\*\*"  # **(a)**
    r"|\*\*([a-l]{1,2})\*\*"  # **a**
    r"|`\(?([a-l]{1,2})\)?`"  # `a` / `(a)`
    r"|\*\(([a-l]{1,2})\)\*"  # *(a)*
    r"|\*([a-l]{1,2})\*"  # *a*
    r"|\(([a-l]{1,2})\)"  # (a) / (a), (b)
    r"|(?:,\s*|\b(?:and|or|also|plus|&)\s+|&\s+|,\s*also\s+)([a-l])\b"  # ", f" / "and f" / "also f" / "& f"
    # ⛔ THE HEAD OF A PLAIN LIST. Matching a member only by its SEPARATOR (`, f` /
    # `and f`) never reads the FIRST member, so an honest `Only a, f, h, i and j still
    # read as written.` was compared as `['f','h','i','j']` — a false accusation that
    # names the wrong set, which §8 declares is an accepted spelling (#11902 review,
    # round 4). The head rule pairs the first member with the separator after it, and
    # letters are read case-insensitively, because `**(A)**` is the same claim.
    r"|\b([a-l])\b\s*(?:,\s*(?:also\s+)?|(?:,?\s+)(?:and|or|also|plus|&)\s*)\b([a-l])\b"
    , re.IGNORECASE
)


def _named_letters(sentence: str) -> set[str]:
    """The letters a sentence names as list members, in any accepted spelling."""
    # ⛔ EVERY GROUP, NOT THE FIRST: the plain head rule is ONE match that carries BOTH
    # members (`j or a`), so taking only the first non-empty group read a two-member
    # list as one member and the candidacy gate (`>= 2 named letters`) never fired —
    # measured, `Note: j or a no longer read as written.` left `--check` at rc 0.
    out: set[str] = set()
    for m in _NAMED_LETTER_RE.finditer(sentence):
        out.update(g.lower() for g in m.groups() if g)
    return out


def check_verdict_tally(text: str) -> list[str]:
    """§3's headline tally must be DERIVED from the verdict cells it summarises.

    The paragraph is a summary of the table beside it, and a summary is the one
    thing a reader quotes without re-counting. Unguarded it is also the cheapest
    thing to make false: flipping a single verdict cell to `STILL-TRUE` left the
    page asserting "seven of the twelve … **(b)**, **(k)** and **(l)** are
    **DRIFTED**" with **rc 0 CLEAN** (measured). The same number is repeated in
    the tool's docstring, `ci.yml`, `config/ci-surfaces.yml`, `docs/00_index.md`
    and two test docstrings, so a wrong one propagates well beyond the page.

    This is the sibling precedent, not a new idea: `tools/problem_map_check.py`
    already checks its own summary against its own table.
    """
    problems: list[str] = []
    #: Quoted spans, removed before each PHRASE test in this function. A sentence that
    #: only QUOTES a phrase is naming the words, not asserting them — the rule the letter
    #: arms have always carried, and it was missing from both FIGURE arms: measured,
    #: `They wrote "seven of the twelve rows no longer read as written" verbatim.` red the
    #: figure arm with "2 sentence(s) state a figure" over prose that asserts nothing.
    #: The apostrophe-aware form matters: treating every `'` as a delimiter blanked the
    #: span between two possessives and hid a whole second tally (measured).
    quoted = re.compile(r'"[^"]*"|(?<![A-Za-z])\x27[^\x27]*\x27(?![A-Za-z])')
    verdicts = _verdicts_by_label(text)
    if not verdicts:
        return ["§3: no correction rows — the tally has nothing to be derived from"]

    non_still = sorted(lbl for lbl, v in verdicts.items() if v != "STILL-TRUE")
    overtaken = sorted(
        lbl for lbl, v in verdicts.items() if v in ("SUPERSEDED-BY-FIX", "ADOPTED")
    )

    # ⛔ EXACTLY ONE SUBJECT, located by pattern — the same rule the §6 survivor
    # sentence carries, for the same measured reason: a `text.find()` takes the
    # FIRST paragraph containing the marker anywhere in the document, so a decoy
    # inserted above §3 becomes the guard's subject. Measured: a decoy carrying a
    # correct-looking tally plus a falsified §3 sentence left `--check` at rc 0 —
    # the arm compared a sentence nobody quotes.
    markers = list(re.finditer(re.escape(TAKEAWAY_MARKER), text))
    if not markers:
        raise Unverifiable(
            f"§3: the {TAKEAWAY_MARKER!r} paragraph is absent — the one summary a lane "
            "quotes cannot be checked"
        )
    if len(markers) > 1:
        return [
            f"§3 carries {len(markers)} paragraphs starting {TAKEAWAY_MARKER!r} — the "
            "guard cannot tell which one a lane reads, and a decoy can take the "
            "match and report OK over the real one; keep exactly one"
        ]
    idx = markers[0].start()
    end = text.find("\n\n", idx)
    paragraph = text[idx : end if end > 0 else len(text)]
    # The page is hard-wrapped, so a phrase can straddle a newline. Normalise
    # whitespace before matching phrases, or the arm reds on correct prose —
    # measured: `seven of the twelve rows no longer read as\nwritten`.
    paragraph = re.sub(r"\s+", " ", paragraph)

    total = len(verdicts)
    total_word = COUNT_WORDS[total] if total < len(COUNT_WORDS) else str(total)
    expected = f"{COUNT_WORDS[len(non_still)]} of the {total_word} rows no longer read as written"
    # ⛔ A DERIVED FIGURE MUST BE ASSERTED BY THE SENTENCE A LANE QUOTES — a presence
    # test over the whole PARAGRAPH is defeatable by a decoy sentence, which is the same
    # defect the letter lists were cured of one arm up (#11902 review P1, third round on
    # this paragraph). Measured before this arm: falsifying the real sentence's figure
    # (`seven of the twelve` -> `six of the twelve`) and appending
    # `Note that seven of the twelve rows no longer read as written.` left `--check` at
    # rc 0, because `expected in paragraph` was still satisfied by the sentence nobody
    # quotes — and that figure is repeated in `ci.yml`, `config/ci-surfaces.yml`,
    # `docs/00_index.md` and both module docstrings, so a false one propagates past the
    # page. Both figures therefore have to live in EXACTLY ONE sentence.
    figure_sentences = [
        s
        for s in re.split(r"(?<=\.)\s+", paragraph)
        if FIGURE_RE.search(quoted.sub(" ", s))
    ]
    if len(figure_sentences) != 1:
        problems.append(
            f"§3 tally: {len(figure_sentences)} sentence(s) state a 'N of the M rows no "
            "longer read as written' figure — the guard cannot tell which one a lane "
            "quotes, and a decoy can satisfy the comparison while the real sentence goes "
            "stale; keep exactly one"
        )
    elif expected not in figure_sentences[0]:
        problems.append(
            f"§3 tally: the paragraph does not say {expected!r} — the table has "
            f"{len(non_still)} row(s) whose verdict is not STILL-TRUE "
            f"({', '.join(non_still)}), so the stated count is not derived from it"
        )

    # Split into sentences: the non-STILL-TRUE letters live in the "no longer read
    # as written" sentence, its complement in the "still read as written" one.
    # Partitioning on the phrase instead does not work — "Only **(a)** … **(j)** "
    # sits BEFORE the words it introduces, so the head swallowed the complement and
    # the arm named all twelve (measured).
    sentences = re.split(r"(?<=\.)\s+", paragraph)
    # ⛔ EXACTLY ONE SENTENCE, not "every sentence that matches". Joining them let a
    # second compliant sentence anywhere in the paragraph supply the letter set, the
    # per-letter verdict attribution and the overtaken count while the sentence a lane
    # actually quotes stayed stale: measured, appending
    # `Note: seven of the twelve rows no longer read as written — **(a)**, … **(j)**.`
    # and falsifying the real sentence's letter list in the same edit left `--check` at
    # rc 0. This is the §6 survivor arm's rule ("keep exactly one") one level down from
    # the paragraph-level decoy check above, and for the same reason: the arm locates
    # its subject BY PATTERN, so more than one candidate means it can report OK over a
    # sentence nobody reads.
    # ⛔ THE CANDIDATES ARE THE SENTENCES THAT NAME A LETTER (#11902 review P3) — IN
    # EITHER SPELLING (#11902 review P1). Counting every sentence that merely CONTAINS
    # the phrase red an edit that quoted it — measured: appending `(The phrase "still
    # read as written" names the complement.)` produced `§3 carries 2 sentences
    # saying 'still read as written'` while only one sentence asserted anything. But
    # narrowing to a BOLDED `**(x)**` token swung the same arm fail-OPEN: a second
    # genuine tally written without emphasis became no candidate at all, so the
    # de-bolded stale list plus one compliant bolded decoy — the historical exploit,
    # re-shaped — read CLEAN where the parent refused it (measured). A letter list is
    # a letter list however it is marked up, and the page's own §3 spells its overtaken
    # group in plain text, so the markup does not decide candidacy — what counts is a
    # LIST of two or more single letters, comma-joined (`b, c, d`) or in a pair
    # (`b and c`), with each letter written bold, code, italic or bare. ⛔ EVERY
    # alternative is bounded to the page's own single-letter alphabet `a`–`l`, the only
    # letters §3's table can name: unbounded, the pair form matched ordinary prose
    # (`as and in`, `to and fro`) and red the arm on a legitimate sentence; dropping the
    # form instead re-opened the hole it covers (a stale `c and d …` list read CLEAN
    # beside one compliant decoy); and requiring MARKUP (`**(x)**` only) let a
    # de-bolded or backticked stale list ride out of the count. All three measured.
    # A single stray parenthetical `(c)` in prose is still NOT a list, which is what
    # keeps a sentence that merely QUOTES the phrase out of the count.
    #: One letter. ⛔ MARKUP AROUND THE PARENTHESES DOES NOT DECIDE CANDIDACY, and that
    #: is why there is no separate `**(a)**` branch: `PAREN` already matches the `(a)`
    #: inside it, so a bolded letter and a bare one are the same item — which is also
    #: what stops a `**` around a letter from being the difference between a red and a
    #: green (the fail-open this filter was widened for). What MARKED adds is the
    #: PAREN-LESS spellings the page and this repo also use: code `` `a` `` and italic
    #: `*a*`. ⛔ A SINGLE bare `(c)` is still not a candidate — `row (c) still read as
    #: written` is ordinary prose, and making it one red a legitimate sentence (found by
    #: review, measured) — so PAREN is a LIST ITEM only, never a standalone match.
    MARKED = r"(?:`\(?[a-l]\)?`|\*\(?[a-l]\)?\*)"
    PAREN = r"\([a-l]\)"  # a parenthesised letter — a LIST ITEM only
    BARE = r"\b[a-l]\b"
    ITEM = r"(?:" + MARKED + r"|" + PAREN + r"|" + BARE + r")"
    #: ⛔ CANDIDACY IS THE EXTRACTOR, NOT A SECOND REGEX. `letter_sentence` below is a
    #: NARROWER four-form gate than `_NAMED_LETTER_RE`, so a second tally spelled in a
    #: form the extractor reads but the gate did not was no candidate at all — measured,
    #: `Note: **a**, **f** and **h** no longer read as written.` (and the plain-run,
    #: `or`/`also`/`plus`/`&`, code-span, italic and uppercase spellings) left `--check`
    #: at rc 0 while the page asserted a list the table contradicts. A sentence is a
    #: candidate when the extractor finds TWO OR MORE named letters in it (one letter is
    #: not a list), which is the predicate the comparison itself uses.
    def _is_letter_list(sentence: str) -> bool:
        return bool(letter_sentence.search(sentence)) or len(_named_letters(sentence)) >= 2

    letter_sentence = re.compile(
        MARKED
        + r"|" + ITEM + r"(?:\s*,\s*" + ITEM + r")+"  # a list: b, c, d
        + r"|" + ITEM + r"\s+and\s+" + ITEM  # a two-item list: b and c
    )
    # ⛔ A SENTENCE THAT ONLY **QUOTES** THE PHRASE IS NOT ASSERTING IT. The page
    # discusses the phrase in §8 and in this very comment, so a sentence can contain it
    # inside quotation marks while asserting nothing — and once a plain letter list
    # also counts (above), such a sentence became a candidate and produced a FALSE
    # ACCUSATION: measured, appending `(The phrase "still read as written" names the
    # complement.)` red the complement arm here while the parent stayed clean. Quoted
    # spans are therefore removed before the phrase test, which is what the phrase's own
    # meaning requires: a quote names the words, it does not claim any letter.
    # ⛔ AND THE DELIMITER IS NOT `'` ITSELF. Treating every apostrophe as a quote
    # delimiter blanked everything between two of them, which in English prose is the
    # span between two POSSESSIVES: measured, appending `The review's summary — seven of
    # the twelve rows no longer read as written, **(c)** is STILL-TRUE — is what the
    # ledger's author repeats.` left `--check` at rc 0 and `check_verdict_tally` CLEAN,
    # while the parent refused the identical page — a second stale tally AND a falsified
    # verdict, hidden by two apostrophes. A single quote therefore delimits a quoted
    # span only when it does NOT sit inside a word (`(?<![A-Za-z])` / `(?![A-Za-z])`), and
    # the double quote needs no such guard because prose does not use `"` as an
    # apostrophe. Pinned by
    # `test_a_possessive_apostrophe_cannot_hide_a_second_tally`.
    # ⛔ THE STRIP APPLIES TO THE PHRASE, NOT TO THE LETTERS. Removing quoted spans from
    # the LETTER test as well let a quoted letter list ride out of the count — measured:
    # `Six of the twelve rows no longer read as written: "**(b)**, **(k)** and **(l)**".`
    # read CLEAN while the parent refused it, i.e. the same fail-open this widening
    # exists to close, one quoting step to the left. A quote around the PHRASE names the
    # words; a quote around a LETTER still names that letter.
    tally_sentences = [
        s for s in sentences
        if "no longer read as written" in quoted.sub(" ", s) and _is_letter_list(s)
    ]
    if len(tally_sentences) > 1:
        return [
            f"§3 carries {len(tally_sentences)} sentences saying 'no longer read as "
            "written' — the guard cannot tell which one a lane quotes, and a second "
            "one can supply the letters while the real sentence goes stale; keep "
            "exactly one"
        ]
    non_still_sentence = tally_sentences[0] if tally_sentences else ""
    # ⛔ THE COMPLEMENT NEEDS THE SAME RULE, and it did not have it (#11902 review P2):
    # the `still read as written` list was joined across every matching sentence and
    # then compared as a SET-UNION, so appending one contradicting still-sentence left
    # the page saying `seven of the twelve … no longer read as written` AND `Only
    # **(a)** still read as written` (7 + 1 != 12) at `--check` rc 0 — measured.
    # Falsifying the real still-list beside a compliant decoy did the same. One
    # sentence, or a red.
    still_sentences = [
        s for s in sentences
        if "still read as written" in quoted.sub(" ", s)
        and "no longer" not in quoted.sub(" ", s)
        and _is_letter_list(s)
    ]
    if len(still_sentences) > 1:
        problems.append(
            f"§3 carries {len(still_sentences)} sentences saying 'still read as "
            "written' — the guard cannot tell which one a lane quotes, so a second "
            "one can assert a letter the real sentence does not; keep exactly one"
        )
    still_sentence = still_sentences[0] if still_sentences else ""
    named = _named_letters(non_still_sentence)
    if named != set(non_still):
        problems.append(
            f"§3 tally: the paragraph names {sorted(named) or '(none)'} as no longer "
            f"reading as written, but the table's verdicts name {non_still} — a "
            "letter list that disagrees with the cells beside it is a reader trap"
        )

    # WHICH verdict each letter is given, not just that it is mentioned. Comparing
    # the letters as a SET and the overtaken rows as a COUNT let a cell move between
    # two verdicts with the sentence unchanged — measured: (c) SUPERSEDED-BY-FIX ->
    # ADOPTED, (e) ADOPTED -> SUPERSEDED-BY-FIX and (k) DRIFTED -> MOOT-BY-LOSS all
    # left rc 0, while §1 gives those classes different handling rules ("do not
    # re-derive it" vs "treat the new behaviour as the norm" vs "do not assert it
    # either way"), so the sentence sent the reader to the wrong action.
    attributed: dict[str, str] = {}
    before: dict[str, str] = {}
    following: dict[str, str] = {}
    previous = 0
    tokens = [
        tok
        for tok in re.finditer(r"\*\*([A-Z][A-Z-]+)\*\*", non_still_sentence)
        if tok.group(1) in VERDICTS
    ]
    for pos, token in enumerate(tokens):
        for label in re.findall(
            r"\*\*\(([a-z])\)\*\*", non_still_sentence[previous : token.start()]
        ):
            before[label] = token.group(1)
        previous = token.start()
        stop = tokens[pos + 1].start() if pos + 1 < len(tokens) else len(non_still_sentence)
        for label in re.findall(
            r"\*\*\(([a-z])\)\*\*", non_still_sentence[token.end() : stop]
        ):
            following.setdefault(label, token.group(1))

    # ⛔ TWO LEGAL ORDERINGS, and the arm must accept BOTH without going blind.
    # Letters-first (`(c), (d) and (g) are **SUPERSEDED-BY-FIX**`) is the shipped
    # form, read by "letters preceding the class token". Class-first
    # (`**SUPERSEDED-BY-FIX**: (c), (d) and (g)`) is the identical assignment
    # written the other way round; reading only "letters before" RED the REQUIRED
    # `docs` job with a message ACCUSING the page of a swap it had not made
    # (measured) — the false-accusation class one arm over.
    #
    # The choice is FULL-COVERAGE consistency, not a per-label preference: the
    # class-first reading is adopted only when it assigns EVERY non-STILL-TRUE row
    # AND agrees with the table. A genuine swap still reds, because its class-first
    # reading covers only the few letters that happen to follow a class token, so it
    # is never adopted and the letters-first map is reported.
    def _consistent(mapping: dict[str, str]) -> bool:
        return set(non_still) <= set(mapping) and all(
            mapping.get(label) == verdicts.get(label) for label in non_still
        )

    attributed = following if _consistent(following) else before

    # ⛔ AND A REPEATED MENTION MUST AGREE WITH THE ONE THE MAP KEPT. Both maps above
    # hold ONE verdict per letter, so the LAST (or first) mention silently wins: measured, the sentence
    # `… **(e)** is **DRIFTED**, and **(e)** is **ADOPTED** …` returned `[]` (clean)
    # while it contradicted itself and the table, and only the reverse order red — the
    # outcome depended on which mention a half-applied edit left second.
    #
    # A mention is read here by ADJACENCY, which is what keeps BOTH legal orderings
    # working:
    #   * a VERDICT token claims the nearest LETTER before it when a copula or an
    #     adverb sits between (`(l) are **DRIFTED**`, `(c) is also **DRIFTED**`).
    #     The CONNECTOR must carry one of those words: a rule that let them be
    #     optional accepted a bare `, ` too, so class-first groups joined by a comma
    #     red as a swap they do not make (measured) — and that is the shape a hard
    #     wrap produces. A copula-ONLY rule was the first attempt and it opened a
    #     false pass instead (`is also`/`was`/`remains` matched nothing);
    #   * a VERDICT token claims the LETTER right after it when only a colon sits
    #     between (`**ADOPTED**: **(e)**`) — the class-first form.
    # ⚠️ What this does NOT reconcile is stated rather than implied, and the axis is
    # POSITIONAL, not only lexical: a contradicting repeat that sits BEFORE the class
    # group it belongs to passes CLEAN when its connector is outside the copula/adverb
    # list (`**(d)** has become **ADOPTED**, **(c)**, **(d)** … are
    # **SUPERSEDED-BY-FIX** …`), because the maps above keep one verdict per letter and
    # the last assignment is the group's. The same connector reds when the repeat sits
    # AFTER the group IN THE SAME SENTENCE (a later sentence is not reconciled either,
    # measured). Widening the word list alone closes none of this.
    COPULA_OR_ADVERB = re.compile(
        r"[\s,]*(?:"
        r"(?:is|are|was|were|remains|stays|reads|stood)(?:[\s,]+(?:also|still))?"
        r"|(?:also|still)"
        r")[\s,]*"
    )
    COLON_ONLY = re.compile(r"[\s:]*")
    # ⛔ Tokenise the two kinds SEPARATELY rather than pairing the bold markers: the
    # subject paragraph starts mid-bold (`take away:** **seven of …`), so a
    # `\*\*([^*]+)\*\*` walk pairs the wrong markers and every letter lands in a
    # connector instead of being seen (measured — the first version of this block
    # returned clean because of exactly that).
    LETTER_TOKEN = re.compile(r"\*\*\(([a-z])\)\*\*")
    VERDICT_TOKEN = re.compile(r"\*\*([A-Z][A-Z-]+)\*\*")
    # The mention scan covers the WHOLE paragraph, not only the sentence that carries
    # the letter list: a contradicting repeat in the closing sentence or in the
    # parenthetical one is still part of the "one thing to take away" a reader quotes,
    # and reading only the letter-list sentence dropped it (measured: `… are
    # **DRIFTED**. Note **(d)** is **ADOPTED**.` returned `[]`). Only labels the table
    # calls non-STILL-TRUE can be misattributed, so the complement sentence
    # contributes no claims and needs no special case.
    letters = list(LETTER_TOKEN.finditer(paragraph))
    tokens = [t for t in VERDICT_TOKEN.finditer(paragraph) if t.group(1) in VERDICTS]
    mentions: dict[str, set[str]] = {}
    for token in tokens:
        before = [lt for lt in letters if lt.end() <= token.start()]
        if before:
            nearest = before[-1]
            # A DATED reading is not a statement of the row's current verdict, and this
            # page's idiom is exactly that: "**(d)** was **STILL-TRUE** as written on
            # 2026-09-10." Measured: appending it red the arm with "(d) stated
            # STILL-TRUE+SUPERSEDED-BY-FIX" over a page whose table is untouched — a
            # false accusation on correct prose, which is the class this arm must not
            # create. The date marker is what distinguishes the two.
            # The date may sit on EITHER side of the claim: "(d) was STILL-TRUE as
            # written on 2026-09-10" and "As of 2026-09-10 (d) was STILL-TRUE" are the
            # same historical statement, and guarding only the trailing form red the
            # leading one with a false accusation (measured).
            dated = re.match(
                r"\s+(?:as written\s+|as of\s+)?(?:on\s+)?(?:the\s+)?\d{4}-\d{2}-\d{2}",
                paragraph[token.end() :],
            ) or re.search(
                r"(?i:as of|on)\s+(?:the\s+)?\d{4}-\d{2}-\d{2}\s*[,;—–-]?\s*$",
                paragraph[: nearest.start()],
            )
            if not dated and COPULA_OR_ADVERB.fullmatch(
                paragraph[nearest.end() : token.start()]
            ):
                mentions.setdefault(nearest.group(1), set()).add(token.group(1))
        after = [lt for lt in letters if lt.start() >= token.end()]
        if after:
            nearest = after[0]
            if COLON_ONLY.fullmatch(paragraph[token.end() : nearest.start()]):
                mentions.setdefault(nearest.group(1), set()).add(token.group(1))
    # The verdict the adopted map assigns is one more mention, so the agreement test
    # is never weaker than the arm was before this block.
    for label in non_still:
        if attributed.get(label):
            mentions.setdefault(label, set()).add(attributed[label])

    misattributed: dict[str, tuple[list[str], str | None]] = {
        label: (sorted(mentions.get(label, set())), verdicts.get(label))
        for label in non_still
        if mentions.get(label, set()) != {verdicts.get(label)}
    }
    if misattributed:
        detail = "; ".join(
            f"({label}) stated {'+'.join(stated) if stated else 'no verdict'}, table "
            f"says {actual}"
            for label, (stated, actual) in sorted(misattributed.items())
        )
        problems.append(
            f"§3 tally: the sentence assigns a different verdict than the row carries "
            f"— {detail}. Each class has its own handling rule in §1, so a swapped "
            "class is not a cosmetic error"
        )
    still_true = sorted(lbl for lbl, v in verdicts.items() if v == "STILL-TRUE")
    # The sentence may spell the COMPLEMENT too ("**(h)**, **(i)** and **(j)** still read
    # as written — **(c)**, **(d)**, **(e)** and **(g)** do not"), and reading past its
    # own negation accused correct prose of a wrong STILL-TRUE list (measured). Only the
    # letters BEFORE the negation are the still-list.
    negation = re.search(r"\b(?:do|does|did) not\b|\bno longer\b", still_sentence)
    still_clause = still_sentence[: negation.start()] if negation else still_sentence
    # Drop the letters the negation CLAUSE governs, which sit just BEFORE the verb they
    # negate: "**(h)**, **(i)** and **(j)** still read as written — **(c)**, **(d)**,
    # **(e)** and **(g)** do not". Slicing at the verb alone keeps `(c)…(g)` (measured),
    # so the governed group is stripped from the end, separator and dash included.
    while True:
        governed = re.search(
            r"(?:\s*(?:,|and|—|–|-)\s*)?\*\*\([a-z]\)\*\*\s*$", still_clause
        )
        if not governed:
            break
        still_clause = still_clause[: governed.start()]
    named_still = _named_letters(still_clause)
    if named_still != set(still_true):
        problems.append(
            f"§3 tally: the paragraph's STILL-TRUE list names "
            f"{sorted(named_still) or '(none)'}, but the table's STILL-TRUE rows are "
            f"{still_true}"
        )

    expected_overtaken = (
        f"{COUNT_WORDS[len(overtaken)].capitalize()} of the "
        f"{COUNT_WORDS[len(non_still)]} were"
    )
    # The same rule as the count above, for the same measured reason: the figure is
    # compared against the ONE sentence that states it, never against the paragraph.
    overtaken_sentences = [
        s
        for s in re.split(r"(?<=\.)\s+", paragraph)
        if OVERTAKEN_FIGURE_RE.search(quoted.sub(" ", s))
    ]
    if len(overtaken_sentences) != 1:
        problems.append(
            f"§3 tally: {len(overtaken_sentences)} sentence(s) state a 'N of the M were' "
            "overtaken figure — the guard cannot tell which one a lane quotes, and a "
            "decoy can satisfy the comparison while the real sentence goes stale; keep "
            "exactly one"
        )
    elif expected_overtaken not in overtaken_sentences[0]:
        problems.append(
            f"§3 tally: the paragraph does not say {expected_overtaken!r}, though "
            f"{len(overtaken)} row(s) are SUPERSEDED-BY-FIX/ADOPTED "
            f"({', '.join(overtaken)})"
        )
    return problems


def check_citation_shapes(text: str) -> list[str]:
    """A backticked `token:LINE` the resolver declines is an ERROR, not silence.

    §8 tells the reader every `path:line` on this page is checked. That claim is
    only true if an unrecognised shape is refused, because a shape the pattern
    does not match is indistinguishable from a shape the page does not contain.

    The scan is the WHOLE document, not just §3/§5. Restricting it to the two
    sections §8 names left the same hole one section up: §2's own
    `tortoise/pipeline.py:16` could be mangled into `tortoise/pipeline .py:16` …
    which matches neither the resolver nor a §3/§5-scoped refusal, so it went
    unchecked and the page reported CLEAN (measured).
    """
    problems: list[str] = []
    for match in re.finditer(r"`[^`\n]+`", text):
        # Trailing whitespace or punctuation is STRIPPED before the shape test but
        # NOT before the resolver: `…/sdk.py:16 ` matches no resolver, so it must be
        # refused, not skipped — measured, a single trailing space on row (e)'s
        # anchor, on the self-test's own `:792` target, and a period after an
        # out-of-range number ALL left the page at rc 0 while a whole row's
        # evidence became unverified (the `:27` / `:66` shorthands then re-anchor
        # to the previous row's path and resolve). CommonMark strips a space that
        # sits on both sides of a code span, so the mangled form does not look
        # mangled to a reader either.
        body = SPAN_TRAILING_RE.sub("", match.group(0)[1:-1])
        # The GitHub permalink spelling FIRST: it is the likeliest real-world form and
        # the one shape nothing else in this module could see (measured, #11952 P1).
        #
        # ⛔ EVERY match in the span, not just the first: testing only `search()`'s
        # first match let a second permalink in the same span ride behind the first
        # one, a red→green in exactly the class this arm exists for (found by review).
        #
        # ⛔ This arm had a `~`-rooted carve-out until #11902's review measured it
        # open: the exemption skipped `~/…` in BOTH spellings, so
        # `~/.swarm/x.md#L999999` was refused nowhere and an out-of-range `~/`
        # permalink read CLEAN, while §8 justified the carve-out as protecting *"a §5
        # reading that §8 declares exempt"* — a citation form no revision of this page
        # has ever carried (measured over all 37 commits that touch it, both
        # spellings, zero matches). §5's own first consequence already says a
        # `~/.swarm` citation is a BROKEN citation: the page's doctrine and the
        # guard's exemption contradicted each other, and the exemption was the side
        # that was wrong. A `~`-rooted path is now refused here like any other.
        permalinks = list(CITATION_HASH_LINE_RE.finditer(body))
        if permalinks and not COMMAND_RE.match(match.group(0)):
            problems.append(
                f"`{match.group(0)[1:-1]}` is not a recognised citation — it carries "
                f"{permalinks[0].group(0)!r}, the GitHub permalink spelling, which the "
                "resolver does not read; §8 claims every `path:line` here is checked, "
                "so an unreadable shape must be refused rather than skipped"
            )
            continue
        # ⛔ EVERY match, and the exemptions are PER MATCH
        # (`_uncoloned_is_exempt`). They used to be tested on `search()`'s first
        # match only, so one exempt token certified the whole span: measured,
        # `index.html <status>` or a numeric argument in front of `path|999999` (or
        # of a citation to a file that does not exist) left the page CLEAN, where the
        # same citation alone was REFUSED — the identical red→green the permalink arm
        # was fixed for (found by review).
        uncoloneds = [
            u
            for u in CITATION_UNCOLONED_SHAPE_RE.finditer(body)
            if not _uncoloned_is_exempt(body, u)
        ]
        if uncoloneds and (
            not CITATION_RE.fullmatch(match.group(0))
            # A span that carries a REAL `path:line` is the resolver's business, and a
            # marker-less `word:digits` beside it (`a host:6379 note`) must not buy the
            # whole span an exemption — measured: that left an out-of-range
            # `tortoise/pipeline.py 999999` unchecked.
            and not any(
                _has_path_marker(tok.group("path"))
                for tok in INNER_CITATION_RE.finditer(body)
            )
            # A backticked COMMAND is a legal §1 evidence form, and its trailing
            # number is an argument: `uv run python tools/collision_preflight.py 2852
            # --repo .`. Measured over `docs/**` — 7 legitimate spans, all commands.
            # This one IS span-scoped: the PROGRAM decides what its arguments mean.
            and not COMMAND_RE.match(match.group(0))
        ):
            uncoloned = uncoloneds[0]
            problems.append(
                f"`{match.group(0)[1:-1]}` is not a recognised citation — it carries "
                f"{uncoloned.group(0)!r}, which uses a delimiter the resolver does not "
                "read; §8 claims every `path:line` here is checked, so an unreadable "
                "shape must be refused rather than skipped"
            )
            continue
        if _has_path_marker(body) and (
            CITATION_SHAPE_RE.match(body) or CITATION_SPACED_SHAPE_RE.match(body)
        ) and not CITATION_RE.fullmatch(match.group(0)):
            problems.append(
                f"`{body}` is not a recognised citation — it ends in a line number "
                "the resolver cannot read from this span, and §8 claims every "
                "`path:line` here is checked"
            )
            continue
        # A citation EMBEDDED in a longer span is still a citation: the span-level
        # test above only sees a body whose WHOLE text is `path:line`, so appending
        # one word inside the backticks used to be enough to defuse a red
        # (measured: `…:999999 (dead)`, `see …:999999 here`).
        if CITATION_RE.fullmatch(match.group(0)):
            continue
        for token in INNER_CITATION_RE.finditer(match.group(0)[1:-1]):
            if not _has_path_marker(token.group("path")):
                continue
            problems.append(
                f"`{match.group(0)[1:-1]}` is not a recognised citation — it "
                f"carries the `path:line` {token.group(0)!r}, which the resolver "
                "cannot read from this span; §8 claims every one here is checked"
            )
            break
    return problems


def check_vocabulary(text: str) -> list[str]:
    """§1's table must define EXACTLY the closed vocabulary the guard enforces.

    The page tells the reader (§8) that every verdict in §3 is a member of the
    vocabulary "in §1". `check_verdicts()` alone compared §3 against this module's
    constant, so the two could drift: deleting §1's `ADOPTED` row left
    `check_document()` empty while §3 still used it and §8 still claimed §1 was the
    authority (measured). The page would then define a vocabulary narrower than its
    own table uses — the "claim class with no handling rule" §1 exists to prevent.

    ⛔ AND THE ROWS THAT DO NOT MATCH ARE REFUSED TOO. The comparison above only
    collects rows that MATCH, so a row shaped any other way is invisible to it:
    measured, inserting `| SUPERSEDED-BY-DRIFT | … |` left `check_vocabulary()`
    empty and `--check` at rc 0, so §1 could carry a sixth claim class with no
    handling rule — exactly the state §1 exists to prevent — while §8 went on naming
    §1 as the authority. This is §3's and §6's rule ("an unrecognised row shape is
    refused, not skipped") applied to the one table that DEFINES the vocabulary the
    others are checked against.
    """
    problems: list[str] = []
    section = _region(text, "## 1. What a row means", "## 2. The four defect classes")
    defined = tuple(
        m.group("token")
        for m in (VOCAB_ROW_RE.match(line) for line in section.splitlines())
        if m
    )
    if defined != VERDICTS:
        problems.append(
            f"§1 vocabulary drift: the page defines {defined or '(none)'}, the guard "
            f"enforces {VERDICTS} — in either direction a verdict has no handling "
            "rule, and §8 names §1 as the authority"
        )
    # ⛔ THE HEADER IS THE FIRST `|` ROW *AND ITS FIRST CELL IS `Verdict`*
    # (#11902 review P2, then P1). The skip used to be
    # `row.startswith("| Verdict")`, which exempted EVERY row whose first cell merely
    # begins with that word — so the arm below was defeated by a plural: measured,
    # `| Verdicts | a | b |`, `| Verdict-ish | a | b |` and `| Verdict-and-more | … |`
    # each left `check_document()` at `[]` while the plain non-verdict row reded.
    #
    # ⛔ AND POSITION ALONE IS NOT ENOUGH EITHER — that was a RED→GREEN regression, and
    # the review measured it: exempting `vocab_rows[0]` unconditionally made the FIRST
    # row content-blind, so REPLACING the header with an unbolded invented class
    # (`| FIXED-IT | a | b |`, `| SUPERSEDED-BY-DRIFT | Means | …`) went from refused
    # (1 problem on the parent) to CLEAN, in the one table §8 calls the authority. So
    # row 0 must BE the header, and what that means is exactly `VOCAB_HEADER_RE` below:
    # the cell reads `Verdict`, case-insensitively, with whitespace around it — and
    # NOTHING is stripped from it. Marked-up spellings (`| **Verdict** |`,
    # ``| `Verdict` |``) are refused rather than normalised, deliberately: the shipped
    # header is the plain word, and a guard that silently accepts variants is the shape
    # that let the prefix test through. A reworded header therefore reds — also
    # deliberately, in the same two-places spirit as `MIN_SELF_TEST_MUTATIONS`: update
    # the page and this test together rather than let the first row drift out of the
    # arm.
    vocab_rows = [
        row for row in (ln.strip() for ln in section.splitlines())
        if row.startswith("|") and not TABLE_SEPARATOR_RE.match(row)
    ]
    if vocab_rows and VOCAB_ROW_RE.match(vocab_rows[0]):
        problems.append(
            "§1's table has no header row — its first `|` row is already a "
            "`**VERDICT**` row, so the table a reader sees is not the one this arm "
            "enumerates"
        )
    elif vocab_rows and not VOCAB_HEADER_RE.match(vocab_rows[0]):
        # ⛔ THE FIRST ROW IS THE HEADER, SO IT HAS A SHAPE TOO (#11902 review P1).
        # Skipping it by POSITION exempted whatever stood there: measured, substituting
        # `| SUPERSEDED-BY-DRIFT | Means | What a lane should do |` for the shipped
        # header left `check_document()` at `[]`, while the same row placed one line
        # lower reded — the header slot was the one position a claim class could
        # occupy without a handling rule, and no named test or `--self-test` arm could
        # see it. The exemption is now the header's SHAPE (`| Verdict |`), which is
        # what the page actually starts its table with.
        problems.append(
            f"§1's first `|` row is not the table header: {vocab_rows[0][:80]} — the "
            "first row is the header (`| Verdict | … |`), so a row shaped any other way "
            "there is a claim class with no handling rule"
        )
    for row in vocab_rows[1:]:
        if VOCAB_ROW_RE.match(row):
            continue
        problems.append(
            f"§1 carries a table row that is not a `**VERDICT**` row: {row[:80]} — "
            "the closed vocabulary is the rows of this one table, so a row shaped any "
            "other way is a claim class with no handling rule"
        )
    return problems


def _line_count(path: Path) -> int | None:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return None


def check_citations(text: str, root: Path = REPO) -> list[str]:
    """Every backticked `path:line` must resolve, and the line must be in range.

    A shorthand `:LINE` is resolved against the nearest PRECEDING full-path
    citation, which is how the page reads (`classes at <path>:395`, then `:792`).
    It is not skipped: skipping it is what let row (b)'s only two commitments about
    `tortoise/commit_schema.py` point at nothing while §8 promised they were
    checked. A shorthand with no preceding full path at all is reported — the
    guard will not invent an anchor for it.
    """
    problems: list[str] = []
    seen: set[str] = set()
    n_resolved = 0
    anchor: str | None = None
    section_ends = [m.start() for m in re.finditer(r"^## ", text, re.MULTILINE)]
    section = None
    for match in CITATION_RE.finditer(text):
        # ⛔ A shorthand resolves only within its own SECTION (#11902 review P2).
        # Document-global anchoring is what let §5's in-prose full path be replaced
        # by `:9` and still resolve — against a §3 row's test file that §5 never
        # names. Per-ROW scoping was measured too and is too strict for this page:
        # §4 row 2 says "**Cited by** the same WS-C plan, `:558-559`", whose anchor
        # is deliberately the row above. A section is the unit the page's prose
        # actually uses; a cell that names no path of its own is caught by the
        # evidence floor instead (`_is_openable_evidence`), which is where §1 puts
        # that rule.
        here = bisect.bisect_right(section_ends, match.start())
        if here != section:
            section, anchor = here, None
        if _is_host_port(match.group(0)):
            continue
        raw = match.group(0)[1:-1]
        start, end = int(match.group("start")), match.group("end")
        span = f"{start}{'-' + end if end else ''}"
        rel = match.group("path")
        origin = ""
        if rel and not _has_path_marker(rel):
            # `python3:11`, `localhost:6379` — PROSE, not a path. The resolver used
            # to have no marker rule while the refusal arm did, so a host:port
            # example red the required `docs` job as "does not exist in the tree"
            # (measured). The carve-out — a marker-less extension-less FILENAME
            # such as `Makefile:12` is unmeasured — is named in §8 rather than
            # implied away.
            #
            # ⛔ AND IT MUST NOT BECOME THE ANCHOR (#11902 review P2). This test
            # used to run BELOW the anchor assignment, so a marker-less token was
            # installed as `anchor` first and every `:LINE` shorthand after it was
            # then skipped by the same test at the bottom — silently, with no
            # problem recorded, while §8 promises a shorthand resolves against the
            # nearest preceding full-path citation or is refused. Measured: row
            # (b)'s `` `:792` `` rewritten to `` `:999999` `` with
            # `` (`localhost:6379`) `` inserted before it returned `[]` (green);
            # without the inserted token the same edit reported "out of range".
            # A red must not be defusable by inserting a prose example.
            continue
        if rel:
            anchor = rel
        elif anchor is None:
            problems.append(
                f"citation {raw}: shorthand with no preceding `path:line` to resolve "
                "it against — write the path, or the guard cannot tell what it points at"
            )
            continue
        else:
            rel = anchor
            origin = f" (shorthand {raw} resolved against {rel})"
        label = f"{rel}:{span}"
        if label in seen:
            continue
        seen.add(label)
        if not _has_path_marker(rel):
            # Already excluded as the ANCHOR above (#11902 review P2): a shorthand
            # resolved against an anchor is always a marked path, so this arm is
            # unreachable for shorthands and remains for a full path that lost its
            # marker. Kept rather than deleted so the two orders cannot drift back.
            continue
        # ⛔ A `~`-rooted path is REFUSED, not exempt. This inherited a carve-out that
        # skipped such paths as "a dated reading of something outside this
        # repository", justified by §8 as protecting a §5 reading — but no revision of
        # the page carries a `~`-rooted `path:line` in either spelling (measured over
        # all 37 commits that touch it: zero), so the exemption protected nothing and
        # DEFUSED a red: an out-of-range `~/tortoise/pipeline.py:999999` read CLEAN
        # (measured). §5's own first consequence already says a `~/.swarm` citation is
        # now a BROKEN citation, so a `~`-rooted `path:line` is a dangling citation —
        # the class this arm exists to catch. No branch is needed to say so: falling
        # through to the resolve/exists path below resolves `root / "~/…"` to a literal
        # `~` directory this tree does not have, and reports it.
        if rel.startswith("/"):
            # An absolute path is NOT auto-exempt: rewriting every citation to
            # `/tortoise/…` used to leave the page CLEAN with zero citations
            # resolved (measured). Such a path is only evidence if it points INSIDE
            # this tree; otherwise it is a dangling reference.
            target = root / rel.lstrip("/")
            if not target.is_file():
                problems.append(
                    f"citation {label}{origin}: {rel} is an absolute path outside "
                    "this repository — evidence the repo does not contain is not "
                    "evidence"
                )
                continue
        else:
            target = root / rel
        # ⛔ A `../..` path walks OUT of the tree, and pathlib would let an absolute
        # one through by DISCARDING `root` (absolute paths are handled above by
        # design, so this arm is about relative escapes). An existence check alone
        # then certifies a file the repository does not contain — the "looks exactly
        # like evidence" class this page is about (measured: `../../..` was clean).
        resolved_root = root.resolve()
        try:
            resolved = target.resolve()
        except (OSError, RuntimeError):
            # Unreadable component or a symlink loop (`resolve()` raises
            # RuntimeError for that, not OSError): fall through to the existence
            # check rather than escaping `main()` with a traceback.
            resolved = None
        if resolved is not None and not resolved.is_relative_to(resolved_root):
            problems.append(
                f"citation {label}{origin}: {rel} resolves outside the repository — "
                "evidence the repo does not contain is not evidence"
            )
            continue
        if not target.is_file():
            problems.append(f"citation {label}{origin}: {rel} does not exist in the tree")
            continue
        n_resolved += 1
        total = _line_count(target)
        if total is None:
            problems.append(f"citation {label}{origin}: {rel} exists but cannot be read")
            continue
        last = int(end) if end else start
        if start < 1 or last > total or last < start:
            problems.append(
                f"citation {label}{origin}: line {span} is out "
                f"of range for {rel} ({total} lines)"
            )
    if n_resolved == 0 and not problems:
        raise Unverifiable(
            "no `path:line` citation resolved to a file in this tree — the guard "
            f"measured nothing ({len(seen)} citation(s) seen)"
        )
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
    for offset, line in _table_rows(section):
        match = STATE_ROW_RE.match(line)
        if not match:
            cells = _split_row_cells(line)
            first = next((cell.strip() for cell in cells if cell.strip()), line.strip())
            problems.append(
                f"§6 row {offset}: state label {first!r} is not one of "
                f"{', '.join(STATE_ROWS)} — a row the arithmetic cannot name is a "
                "count it silently drops, so the sum stays pinned to the indexed "
                "population while a bucket has moved"
            )
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


def check_survivor_arithmetic(text: str) -> list[str]:
    """§6: the survivor sentence's counts must be DERIVED from the table it sits under.

    The sentence and its three bullets are the page's guidance on what a lane must
    not re-derive, so both of its numbers are derived claims: `four` must be the
    OPEN row's own count, and `two` must be the number of BULLETS that name an
    OPEN issue (the third bullet, `#2730`, is CLOSED and says so — it is there
    because *issue closure is not evidence of a fix*, not because it survives).

    Checked here rather than left to prose because the unguarded sentence was
    FALSE in the shipped page and the guard reported OK over it — a gate that
    passes by not looking, on the one page whose declared defect pattern is
    *declared ≠ stored*. Measured: `three` sat above two survivor bullets plus
    one CLOSED issue while §7 said `two`; two reviewers then disagreed about
    which side was stale, and the fix one of them proposed would have replaced a
    true `two` with a false `three`.
    """
    problems: list[str] = []
    section = _region(text, "## 6. The issue index", "## 7. What this changes")
    # ⛔ EXACTLY ONE MATCH, because the subject is located BY PATTERN (#11902 review, P2). A
    # `search()` takes the FIRST sentence in §6 that fits, so ANY second sentence written above the
    # real one silently becomes the guard's subject: measured, adding
    # `Note first: of the four survivors, two are already re-measured elsewhere.` and falsifying the
    # ⚠️ sentence to `three bullets below` left `--check` at rc=0 AND `--self-test` at PASS — the
    # decoy absorbed the self-test's own mutation too, so the arm caught its own edit and never
    # looked at the sentence a lane reads. More than one match is therefore a page DEFECT (rc=1),
    # not an Unverifiable: the page can always keep one.
    matches = list(SURVIVOR_RE.finditer(section))
    if not matches:
        raise Unverifiable(
            "§6: the survivor sentence is absent — its arithmetic cannot be derived"
        )
    if len(matches) > 1:
        return [
            f"§6 carries {len(matches)} sentences matching the survivor pattern "
            "('of the N survivors, … are already re-measured') — the guard cannot tell which one "
            "owns the bullets, and a second sentence takes the match and reports OK over the one a "
            "lane reads; keep exactly one"
        ]
    match = matches[0]
    total = NUMBER_WORDS.get(match.group("total"))
    count = NUMBER_WORDS.get(match.group("count"))
    if total is None or count is None:
        return [
            "§6 survivor sentence: a count is not a spelled number this guard can "
            f"derive ({match.group('total')!r} / {match.group('count')!r})"
        ]

    open_ids: set[str] = set()
    for line in section.splitlines():
        row = STATE_ROW_RE.match(line)
        if row and row.group("label") == "OPEN":
            open_ids = set(ISSUE_ID_RE.findall(row.group("which")))
            break
    if not open_ids:
        raise Unverifiable("§6: the OPEN row names no issues — the survivor count cannot be derived")

    bullets = SURVIVOR_BULLET_RE.findall(section[match.end():])
    if not bullets:
        raise Unverifiable("§6: the survivor sentence has no bullets — their count cannot be derived")

    if total != len(open_ids):
        problems.append(
            f"§6 survivor sentence: says there are {total} survivors, but the OPEN row "
            f"names {len(open_ids)} issue(s) ({', '.join('#' + i for i in sorted(open_ids))}) "
            "— the total is not derived from the table it sits under"
        )
    survivors = [issue for issue in bullets if issue in open_ids]
    if len(survivors) != count:
        problems.append(
            f"§6 survivor sentence: says {count} of them are already re-measured, but "
            f"{len(survivors)} of its {len(bullets)} bullet(s) name an OPEN issue "
            f"({', '.join('#' + i for i in survivors) or 'none'}) — the count is not "
            "derived from the bullets beside it"
        )
    # ⛔ THE BULLETS THAT ARE INVARIANT TO THE ARITHMETIC ARE ALSO PINNED (#11902
    # review, P2). Both derived numbers are functions of the bullets that name an
    # OPEN issue, so the CLOSED bullet could be DELETED with both counts unchanged
    # — measured, rc 0. That bullet is `#2730`, whose entire job is "issue closure
    # is not evidence of a fix", the §6 guidance a lane most needs; an arm that
    # cannot notice its absence is a pin that only holds the half it measures.
    closed_bullets = [issue for issue in bullets if issue not in open_ids]
    if not closed_bullets:
        problems.append(
            "§6 survivor bullets: none of them names a CLOSED issue — the "
            "'issue closure is not evidence of a fix' case has been dropped, and "
            "both of the sentence's derived counts stay correct without it"
        )
    return problems


def check_table_population(text: str) -> list[str]:
    """§3 and §6 must each carry exactly ONE table, or the pins measure a subset.

    `_table_rows()` reads the section's FIRST table and stops at the first
    non-`|` line, so a SECOND table in either section was never enumerated: no
    verdict check, no evidence floor, no population pin, no arithmetic. Measured
    before this arm existed — a thirteenth correction row appended in a fresh table
    left `--check` at rc=0, `--self-test` at PASS and the whole suite green, while
    §8 states the §3 population is pinned to twelve. Refusing the SHAPE is the
    honest option: the page only ever needs one table per section, so this is
    actionable drift (rc=1), not an unanswerable UNVERIFIED.
    """
    problems: list[str] = []
    for start, end, name in (("## 3.", "## 4.", "§3"), ("## 6.", "## 7.", "§6")):
        try:
            section = _region(text, start, end)
        except Unverifiable:
            continue  # the section-boundary arm reports the absence itself
        # One separator per table — a second table brings its own header and separator.
        tables = sum(
            1 for line in section.splitlines() if TABLE_SEPARATOR_RE.match(line.strip())
        )
        if tables > 1:
            problems.append(
                f"{name} carries {tables} tables — only the first is enumerated by "
                "the population pin, the verdict check and the arithmetic, so a row "
                "added to a second table is silently unchecked; keep exactly one "
                "table per section"
            )
    return problems


def _stray_table_lines(section: str) -> list[tuple[int, str]]:
    """Every `|`-line in the section that is NOT part of its FIRST table.

    `_table_rows()` stops at the first non-`|` line, and `check_table_population()`
    counts SEPARATORS, so a second table is caught only when it brings its own
    header and separator. A bare `|` row appended after a blank line — no
    separator, still a row a reader sees as data and Markdown renders as a table
    fragment where the header allows — is counted by neither: measured on the
    previous head, appending
    `| **Z** | second | <citation> | **FIXED-IT** | <citation> |` under §3 left
    `check_document()` at `[]`, i.e. an invented verdict the closed vocabulary in
    §1 has no handling rule for. Counting the SHAPE rather than the separators
    closes it: a section's population is the rows of its one table, and any other
    `|` line is a shape this guard does not classify, so it is refused.
    """
    counted = {offset for offset, _line in _table_rows(section)}
    header_and_separator: list[int] = []
    for offset, line in enumerate(section.splitlines(), start=1):
        if line.strip().startswith("|"):
            header_and_separator.append(offset)
            if len(header_and_separator) == 2:
                break
    counted |= set(header_and_separator)
    return [
        (offset, line)
        for offset, line in enumerate(section.splitlines(), start=1)
        if line.strip().startswith("|") and offset not in counted
    ]


def check_stray_table_lines(text: str) -> list[str]:
    """§3 and §6 must not carry a `|` row outside their single table.

    The separator-counting arm refuses a second TABLE; this refuses a stray ROW,
    which is the shape that carries no separator and therefore had no arm at all.
    """
    problems: list[str] = []
    for start, end, name in (("## 3.", "## 4.", "§3"), ("## 6.", "## 7.", "§6")):
        try:
            section = _region(text, start, end)
        except Unverifiable:
            continue  # the section-boundary arm reports the absence itself
        for offset, line in _stray_table_lines(section):
            problems.append(
                f"{name} carries a `|` row outside its single table (offset "
                f"{offset}) — rows are read from ONE table per section, so this row "
                f"is neither counted nor classified: {line.strip()[:80]}"
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
        try:
            return subprocess.run(
                ["git", "-C", str(root), *args],
                capture_output=True, text=True, timeout=60, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # `FileNotFoundError` (no `git` on PATH), `PermissionError`, and
            # `TimeoutExpired` are NOT a verdict about this document. Letting them
            # escape `main()` returns Python's traceback exit **1**, which this
            # module defines as DRIFT — "a named claim is false, and the message
            # names the claim" — over a document nothing ever faulted (measured:
            # `env PATH=/tmp/nogit python3 tools/findings_ledger_check.py --check`
            # exited 1 with a traceback and no claim). The ancestry question is
            # exactly the one `tools/problem_map_check.py`'s `_git` exists to
            # answer with a refusal, so this maps to the UNVERIFIED code.
            raise Unverifiable(
                f"`git {' '.join(args)}` could not be run ({type(exc).__name__}: "
                f"{exc}) — this clone cannot answer the ancestry question"
            ) from exc

    resolved = git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if resolved.returncode != 0:
        raise Unverifiable(
            f"commit {sha} is not present in this clone — a grafted or shallow "
            "checkout cannot answer this claim (the `docs` job checks out at "
            "fetch-depth: 0 for exactly this question)"
        )
    verdict = git("merge-base", "--is-ancestor", resolved.stdout.strip(), "HEAD")
    if verdict.returncode == 1:
        # ⛔ ONLY `False` CERTIFIES DRIFT — `tools/substrate_map_check.py`'s rule,
        # which the module docstring above says to copy rather than invent.
        # `merge-base --is-ancestor` exits 1 BOTH for "an unrelated tree" and for
        # "the graft boundary cut the walk", so in a shallow/deepened-shallow
        # checkout a REAL ancestor reads as not-ancestor. Accusing the page of "a
        # cut it did not make" on that evidence is a false red over a correct
        # document (the same defect `tests/test_findings_ledger_2852.py`'s
        # `_history_available()` guards the TEST against, #10356). An
        # unanswerable probe is UNVERIFIED — exit 2, never a verdict.
        shallow = git("rev-parse", "--is-shallow-repository")
        if shallow.returncode != 0 or shallow.stdout.strip() != "false":
            state = shallow.stdout.strip() or "unanswered"
            raise Unverifiable(
                "git answered 'not an ancestor', but this checkout is shallow or "
                f"grafted (`rev-parse --is-shallow-repository` → {state}), so the "
                "graft boundary cut the path to it and a truncated walk exits with "
                "the same status as an unrelated tree — drift cannot be certified "
                "here (the `docs` job checks out at fetch-depth: 0 for exactly this "
                "question)"
            )
        return [f"`measured_at_commit: {sha}` is NOT an ancestor of HEAD — the page claims a cut it did not make"]
    if verdict.returncode != 0:
        raise Unverifiable(f"git merge-base --is-ancestor exited {verdict.returncode}: {verdict.stderr.strip()}")
    return []


#: The page's sections, in the order they must appear. A section is one of the
#: things this guard reads, so an ABSENT one must never check nothing quietly.
#: §1–§4 and §6–§7 were already bounded by a `_region()` pair and raised UNVERIFIED
#: when renumbered away; §5 and §8 were bounded by nothing at all, so deleting
#: either whole returned CLEAN (measured: `§5 deleted -> check_document() == []`,
#: `§8 deleted -> []`, while the same deletions of §2/§4/§7 raised Unverifiable).
#: §5 is one of the two halves the review's own conclusion rests on and prose
#: outside it cites it; §8 is the page's statement of what IS checked, so its
#: absence silently widens every other claim. Same verdict as a missing §3: an
#: absent section is the strongest form of drift and must not certify anything.
SECTION_HEADINGS = (
    "## 1. What a row means",
    "## 2. The four defect classes",
    "## 3. The corrections log",
    "## 4. The artefact index",
    "## 5. The at-risk inventory",
    "## 6. The issue index",
    "## 7. What this changes",
    "## 8. What is machine-checked",
)


def check_section_presence(text: str) -> list[str]:
    """Every section in `SECTION_HEADINGS` must exist, in order, with a body.

    Raises `Unverifiable` (exit 2) rather than reporting drift, matching the
    verdict `_region()` already returns for a section it cannot locate — the two
    mechanisms must not disagree about the same condition.
    """
    positions: list[int] = []
    for heading in SECTION_HEADINGS:
        at = text.find(heading)
        if at < 0:
            raise Unverifiable(
                f"section {heading!r} is absent — an absent section is the strongest "
                "form of drift, and checking nothing over it would report clean"
            )
        positions.append(at)
    if positions != sorted(positions):
        raise Unverifiable(
            "the sections are out of order: "
            + ", ".join(
                f"{h.split('.')[0]}.@{p}"
                for h, p in zip(SECTION_HEADINGS, positions, strict=True)
            )
        )
    for index, heading in enumerate(SECTION_HEADINGS):
        body_end = positions[index + 1] if index + 1 < len(positions) else len(text)
        if not text[positions[index] + len(heading) : body_end].strip():
            raise Unverifiable(
                f"section {heading!r} has an empty body — a heading with nothing "
                "under it asserts a section the reader cannot read"
            )
    return []


def check_document(text: str, root: Path = REPO) -> list[str]:
    """Every arm that reads this checkout's TREE — no `git` history required.

    This is the half that survives a depth-1 clone (the `test` jobs, and the
    `core` surface `tests/test_findings_ledger_2852.py` runs in). It is not a
    weakened check: it is everything the page claims about the tree. The one
    claim it cannot answer — that `measured_at_commit` is on HEAD's history —
    lives in `check_all()`, because answering it needs the whole clone.
    """
    problems: list[str] = []
    # FIRST: a section that is not there must not be quietly unmeasured by every
    # arm below that would have read it.
    problems.extend(check_section_presence(text))
    problems.extend(check_vocabulary(text))
    problems.extend(check_verdicts(text))
    problems.extend(check_verdict_tally(text))
    problems.extend(check_evidence_cells(text))
    problems.extend(check_citations(text, root))
    problems.extend(check_citation_shapes(text))
    problems.extend(check_registration(root))
    problems.extend(check_state_arithmetic(text))
    problems.extend(check_survivor_arithmetic(text))
    problems.extend(check_table_population(text))
    problems.extend(check_stray_table_lines(text))
    return problems


def check_all(root: Path = REPO) -> list[str]:
    """`check_document()` plus the one claim that needs history.

    Kept as the `--check` entry point so the REQUIRED `docs` job (fetch-depth 0)
    still refuses to certify an ancestry claim it never proved.
    """
    text = read_doc(root)
    problems = check_document(text, root)
    problems.extend(check_measured_commit(text, root))
    return problems


# ── self-test ──────────────────────────────────────────────────────────────
# Each arm injects one mutation into a COPY of the real document and asserts the
# checker reports it. A guard whose mutations are not proven to fire is decoration.
#
# The arms drive `check_document()`, not `check_all()`: every mutation below
# targets a doc-level rule, so dragging ancestry in would make `--self-test`
# unrunnable in a depth-1 clone while proving nothing extra — no arm here mutates
# `measured_at_commit`. `MIN_SELF_TEST_MUTATIONS` above keeps the population from
# being emptied; the ancestry arm is covered where it CAN be answered (the
# `--check` invocation in the REQUIRED `docs` job, which exits 2 rather than 0
# when it cannot resolve the commit) and its not-ancestor branch by
# `tests/test_findings_ledger_2852.py::test_measured_commit_not_ancestor_of_head_is_rejected`,
# which stubs `git` and needs no history at all.

def _empty_evidence_cells(text: str) -> str:
    """Blank the §3 evidence column, keeping every verdict — the fail-open under test."""
    lines = text.splitlines(keepends=True)
    try:
        start = next(
            i for i, ln in enumerate(lines) if ln.startswith("## 3. The corrections log")
        )
        end = next(
            i for i, ln in enumerate(lines) if ln.startswith("## 4. The artefact index")
        )
    except StopIteration:  # pragma: no cover - the region arms cover this
        return text
    for i in range(start, end):
        row = lines[i].rstrip("\n")
        if not CORRECTION_ROW_RE.match(row) or "\\|" in row:
            continue
        cells = _split_row_cells(row)
        if len(cells) > 5:
            cells[5] = " "
            lines[i] = "|".join(cells) + "\n"
    return "".join(lines)


MUTATIONS: tuple[tuple[str, Callable[[str], str], str], ...] = (
    (
        "an invented verdict",
        lambda t: t.replace("| **STILL-TRUE** | `tortoise/sdk.py:17109`",
                            "| **PROBABLY-TRUE** | `tortoise/sdk.py:17109`", 1)
                  .replace("**STILL-TRUE**", "**PROBABLY-TRUE**", 1),
        "closed vocabulary",
    ),
    (
        "a citation separated from its line by a space",
        lambda t: t.replace("`tortoise/pipeline.py:16`", "`tortoise/pipeline.py 16`", 1),
        "not a recognised citation",
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
        "a stray `|` row under §3 carrying no separator",
        # `check_table_population()` counts separators, so a lone row appended after
        # a blank line was counted by NOTHING: measured, it left `check_document()`
        # at `[]` with the invented verdict `FIXED-IT` in the page.
        lambda t: t.replace(
            "\n## 4. The artefact index",
            "\n| **Z** | A stray row | `tortoise/sdk.py:17109` | **FIXED-IT** | "
            "`tortoise/sdk.py:17109` |\n\n## 4. The artefact index",
            1,
        ),
        "outside its single table",
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
    (
        # The EXACT shipped defect (#11902 review P2): a marker-less `word:digits`
        # token used to be installed as the shorthand ANCHOR *before* the marker
        # test ran, so inserting one before a shorthand made that shorthand
        # silently skipped instead of resolved — a red defused by reformatting a
        # citation. Re-injecting the prose token must leave the bad shorthand
        # still caught.
        "a shorthand defused by a preceding prose host:port token",
        lambda t: t.replace(
            "`tortoise/commit_schema.py:395`, `:792`",
            "`tortoise/commit_schema.py:395` (`localhost:6379`), `:999999`",
            1,
        ),
        "out of range",
    ),
    (
        "a falsified §3 count beside a list-free decoy",
        # The figure was compared with `expected in paragraph`, so a decoy sentence
        # satisfied it while the sentence a lane quotes went stale — measured before
        # the figure arms existed. Both mutations must be present for the arm to be
        # the subject of the test: the falsified real sentence AND the decoy.
        lambda t: t.replace(
            "**seven of the twelve rows no longer read as\nwritten**",
            "**six of the twelve rows no longer read as\nwritten**",
            1,
        ).replace(
            "will find the fix and call it a regression.",
            "will find the fix and call it a regression. Note that seven of the twelve "
            "rows no longer read as written.",
            1,
        ),
        "rows no longer read as written' figure",
    ),
    (
        "a falsified overtaken figure beside a compliant decoy",
        lambda t: t.replace("Four of the seven were", "Five of the seven were", 1).replace(
            "will find the fix and call it a regression.",
            "will find the fix and call it a regression. Four of the seven were overtaken, "
            "per §1.",
            1,
        ),
        "'N of the M were' overtaken figure",
    ),
    (
        "a shorthand citation pointing past the end of its anchor's file",
        # ⛔ The literal tracks the DOCUMENT: `main`'s `43db5c0f55` shifted
        # `_persist_extra_props`'s call site by 16 lines, so row (c)'s shorthand
        # moved to `:3144` and this fixture stopped matching — the arm reported
        # "did not change the document" rather than going quiet, which is how it was
        # caught. `9958281e7c` shifted it again (+18, to `:3162` — verified:
        # `9958281e7c^` has the call at 3144, `9958281e7c` at 3162; `e4703a286a`
        # touches only `tortoise/search_engine.py` and moves neither) and the same
        # report caught it a second time. Retarget, never delete.
        lambda t: t.replace("`:3162`", "`:999999`", 1),
        "out of range",
    ),
    (
        "a removed §1 vocabulary row",
        # §1 is what the page tells the reader is the authority (§8). Deleting one
        # of its five rows used to leave every arm green while §3 kept using the
        # verdict — the page then defines less than it enforces.
        lambda t: "\n".join(
            ln for ln in t.splitlines() if not ln.startswith("| **ADOPTED** |")
        ),
        "§1 vocabulary drift",
    ),
    (
        "a thirteenth correction row shaped so the population pin cannot see it",
        lambda t: t.replace(
            "| **l** |",
            "| **M** | An undeclared claim | **False** | **FIXED-IT** | `tortoise/pipeline.py:16` |\n| **l** |",
            1,
        ),
        "is not `**<lowercase letter>**`",
    ),
    (
        "every §3 evidence cell emptied",
        _empty_evidence_cells,
        "names no `path:line`, issue, command or §N",
    ),
    (
        "a §6 row the arithmetic cannot name",
        lambda t: t.replace(
            "| MERGED (both were PRs) |",
            "| CLOSED NOT_PLANNED | 0 | |\n| MERGED (both were PRs) |",
            1,
        ),
        "is not one of",
    ),
    (
        "a citation shape the resolver declines",
        lambda t: t.replace(
            "`tests/eval/write_path/test_write_path_relation_lane.py:9`",
            "`tests/eval/write_path/test_write_path_relation_lane.py%20:9`",
            1,
        ),
        "not a recognised citation",
    ),
    (
        "a citation with a space inserted into its path",
        lambda t: t.replace(
            "`tortoise/pipeline.py:16`", "`tortoise/pipeline .py:16`", 1
        ),
        "not a recognised citation",
    ),
    (
        "a citation carrying a trailing space",
        lambda t: t.replace(
            "`tortoise/pipeline.py:16`", "`tortoise/pipeline.py:16 `", 1
        ),
        "not a recognised citation",
    ),
    (
        "a shorthand carrying a trailing space or full stop",
        lambda t: t.replace("`:792`", "`:792 `", 1),
        "not a recognised citation",
    ),
    (
        "a verdict moved between classes with the headline sentence unchanged",
        lambda t: t.replace(
            "| **SUPERSEDED-BY-FIX** — the Point fold now persists",
            "| **ADOPTED** — the Point fold now persists",
            1,
        ),
        "assigns a different verdict",
    ),
    (
        "a §3 headline tally that no longer matches the verdict cells",
        lambda t: t.replace(
            "| **DRIFTED** — `Entity` 3", "| **STILL-TRUE** — `Entity` 3", 1
        ),
        "the paragraph does not say",
    ),
    (
        # The EXACT shipped defect, re-injected: the pre-fix page said `three`
        # above two survivor bullets plus one CLOSED issue. ⛔ The target is the
        # sentence's OPENING clause (`of the four survivors,`) — the clause
        # `SURVIVOR_RE` keys on — and NOT the qualifier that follows it. Why it
        # matters, measured: a target that quotes the qualifier
        # (`the two survivor bullets below`) becomes a no-op the moment that phrase
        # is reworded, so the mutation stops changing the document and the arm dies;
        # an earlier revision of this arm had exactly that shape while its comment
        # claimed the lead-in. `self_test` reports a dead arm LOUDLY ("did not change
        # the document — the arm is dead", FAIL), so the hazard is maintenance rather
        # than a silent false PASS — and the opposite claim would be a false one about
        # this gate (#11902 review).
        "a survivor count that does not match the bullets beneath it",
        lambda t: t.replace("of the four survivors,",
                            "of the four survivors, three are already re-measured, and", 1),
        "says 3 of them are already re-measured, but 2 of its 3 bullet(s) name an OPEN issue",
    ),
    (
        # ⛔ THIS ARM IS THE PIN FOR THE `len(matches) > 1` BRANCH, and nothing else is
        # (#11902 review, P2): the branch is invisible to a test of the real page (which
        # has exactly one match), so deleting it left `--check`, `--self-test` and every
        # test green — measured. A second sentence is what a future edit would plausibly
        # add, and before the branch existed it took the match while the guard reported
        # OK over the sentence a lane actually reads.
        "a second survivor sentence that could take the match",
        lambda t: t.replace(
            "**⚠️ of the four survivors",
            "Note first: of the four survivors, two are already re-measured elsewhere.\n\n"
            "**⚠️ of the four survivors",
            1,
        ),
        "carries 2 sentences matching the survivor pattern",
    ),
    (
        "a survivor total that does not match the OPEN row",
        lambda t: t.replace("of the four survivors", "of the five survivors", 1),
        "says there are 5 survivors, but the OPEN row names 4 issue(s)",
    ),
    (
        "a citation embedded in a longer span",
        lambda t: t.replace(
            "`tortoise/pipeline.py:16`", "`tortoise/pipeline.py:999999 (dead)`", 1
        ),
        "is not a recognised citation",
    ),
    (
        "an absolute-path citation outside the tree",
        lambda t: t.replace(
            "`tortoise/pipeline.py:16`", "`/nope/tortoise/pipeline.py:16`", 1
        ),
        "absolute path outside",
    ),
    (
        "an evidence cell pointing at a section that does not exist",
        lambda t: t.replace("| §6 below, measured per issue |", "| §9 below, measured per issue |", 1),
        "names no `path:line`, issue, command or §N",
    ),
    (
        "a decoy paragraph that could take the tally match",
        lambda t: t.replace(
            "**Read §3 first if you are about to fix something.**",
            "**The one thing to take away:** whatever the table says.\n\n"
            "**Read §3 first if you are about to fix something.**",
            1,
        ),
        "paragraphs starting",
    ),
    (
        "the CLOSED survivor bullet deleted",
        lambda t: t.replace(
            "- **`#2730`** is CLOSED, but the problem map deliberately **excludes** it",
            "- **`#2730` was dropped by a maintenance edit, but the problem map deliberately **excludes** it",
            1,
        ).replace("- **`#2730` was dropped", "- dropped", 1),
        "none of them names a CLOSED issue",
    ),
    (
        # #11952 P1: the GitHub permalink spelling. THIS ARM IS THE PIN for
        # `CITATION_HASH_LINE_RE` — deleting that refusal takes the self-test to
        # `FAIL (1 failing arm(s))` (measured), which is what §8's "the self-test
        # proves those rules still fire" rests on.
        "a citation written as a GitHub permalink",
        lambda t: t.replace(
            "`tortoise/pipeline.py:16`", "`tortoise/pipeline.py#L999999`", 1
        ),
        "not a recognised citation",
    ),
    (
        # The pipe delimiter's pin. §8 names three measured spellings, so the
        # self-test — which §8 calls the proof that "those rules still fire" — has to
        # fire for each one: deleting `\s*\|\s*` from the shape arm left `--self-test`
        # PASS while only the pytest boundary test noticed (found by review).
        "a citation separated from its line by a pipe",
        lambda t: t.replace(
            "`tortoise/pipeline.py:16`", "`tortoise/pipeline.py|999999`", 1
        ),
        "not a recognised citation",
    ),
    (
        # The `~` carve-out's pin, in the shape that FAILED: the deferral was once
        # span-scoped, so an exempt `~`-rooted permalink certified everything beside it
        # and an out-of-range permalink went REFUSED → CLEAN (found by review,
        # measured). This arm fails if the deferral ever goes back to per-span.
        "an exempt permalink hiding a refused one in the same span",
        lambda t: t.replace(
            "`tortoise/pipeline.py:16`",
            "`~/.swarm/x.md#L1 and tortoise/pipeline.py#L999999`",
            1,
        ),
        "not a recognised citation",
    ),
    (
        "a second table in a pinned section",
        lambda t: t.replace(
            "## 4. The artefact index",
            "| # | claim | correction | verdict | evidence |\n"
            "|---|---|---|---|---|\n"
            "| **m** | an undeclared claim | **False.** | **STILL-TRUE** | nothing |\n"
            "\n## 4. The artefact index",
            1,
        ),
        "carries 2 tables",
    ),
    (
        # The `~` carve-out's WIDTH. It exempted every `~`-rooted path, so the whole
        # resolver could be skipped with one keystroke: measured, `~tortoise/nope.py:16`
        # and `~tortoise/pipeline.py:999999` both returned `[]`. Narrowing the
        # exemption to `~/.swarm/` was tried and measured to still exempt a citation of
        # a file §5 records as GONE, so §8's carve-out is now empty and EVERY
        # `~`-rooted citation is refused — this page carries none in either spelling
        # (measured over all 37 commits that touch it), which is why refusal reds
        # nothing real. This arm fails if `~` starts defusing the resolver again.
        "a `~`-rooted citation",
        lambda t: t.replace(
            "`tortoise/pipeline.py:16`", "`~tortoise/pipeline.py:999999`", 1
        ),
        "does not exist in the tree",
    ),
    (
        # The two-digit §N. `SECTION_REF_TOKEN_RE` matched ONE digit and discarded the
        # rest, so `§10` parsed as `§1` — a section the page has — and every two-digit
        # reference to a section that does not exist passed as evidence (measured).
        # This arm is the pin for `\d+`: narrowing it back to `\d` reds the self-test.
        "a §N evidence reference two digits wide that names no heading",
        lambda t: t.replace("| §6 below, measured per issue |",
                            "| §10 below, measured per issue |", 1),
        "names no `path:line`, issue, command or §N",
    ),
    (
        # §1's unrecognised row. `check_vocabulary()` compared only the rows that
        # MATCH, so a sixth claim class shaped any other way was invisible while §8
        # went on naming §1 as the authority (measured: rc 0). §3 and §6 already
        # refuse an unrecognised row shape; this is the same rule on the table that
        # DEFINES the vocabulary they are checked against.
        "a row in §1 that is not a `**VERDICT**` row",
        lambda t: t.replace(
            "| **ADOPTED** |",
            "| SUPERSEDED-BY-DRIFT | whatever |\n| **ADOPTED** |",
            1,
        ),
        "is not a `**VERDICT**` row",
    ),

    (
        # The header SLOT. Skipping row 1 by position exempted whatever stood there, so
        # substituting a claim class for the shipped header left `check_document()` at
        # `[]` while the same row one line lower reded (measured, #11902 review P1).
        "a §1 first row that is not the table header",
        lambda t: t.replace(
            "| Verdict | Means | What a lane should do |",
            "| SUPERSEDED-BY-DRIFT | Means | What a lane should do |",
            1,
        ),
        "is not the table header",
    ),
    (
        # The tally arm joined EVERY sentence matching its phrase, so a second one
        # inside the same paragraph could carry the letters while the sentence a lane
        # quotes went stale (measured on the pre-fix tool: `[]`). This is the §6
        # survivor arm's "keep exactly one" rule, one level down. ⛔ The decoy carries a
        # `**(x)**` token on purpose: a sentence that only QUOTES the phrase is not a
        # candidate at all now (it names no letter to disagree with), so a decoy
        # without one would leave this arm dead — measured.
        "a second tally sentence inside the same paragraph",
        lambda t: t.replace(
            "stated both ways.)",
            "stated both ways.) Only **(a)** no longer read as written.",
            1,
        ),
        "sentences saying 'no longer read as written'",
    ),
    (
        # The FIRST-MATCH pin, one arm over from the permalink one: an exempt token (an
        # HTTP status beside a page name) used to certify everything beside it in the
        # same span, so a citation to a file that does not exist went CLEAN (measured,
        # found by review). The exemptions are per MATCH now; this arm fails if they
        # ever go back to `search()`'s first.
        "an exempt HTTP status hiding a citation to a missing file in the same span",
        lambda t: t.replace(
            "`tortoise/pipeline.py:16`",
            "`docs/index.html 200 tortoise/no_such_module.py 16`",
            1,
        ),
        "not a recognised citation",
    ),
    (
        # #11902 review P2: the §1 header skip was a prefix test on the word
        # `Verdict`, so EVERY row whose first cell merely began with it was exempt —
        # defeating the arm above with a plural (measured: `| Verdicts | a | b |` left
        # `check_document()` at `[]`). The header is the FIRST `|` row and nothing
        # else now, and this arm fails if the skip widens back into a prefix test.
        "a §1 row whose first cell begins with `Verdict`",
        lambda t: t.replace(
            "| **ADOPTED** |", "| Verdicts | a | b |\n| **ADOPTED** |", 1
        ),
        "is not a `**VERDICT**` row",
    ),
    (
        # #11902 review P2: the COMPLEMENT list was still joined across every matching
        # sentence and compared as a set-union, so the page could assert
        # `seven of the twelve … no longer read as written` beside
        # `Only **(a)** still read as written` (7 + 1 != 12) at rc 0 — measured.
        "a second `still read as written` sentence",
        lambda t: t.replace(
            "stated both ways.)",
            "stated both ways.) Only **(a)** still read as written.",
            1,
        ),
        "sentences saying 'still read as written'",
    ),
)


def self_test(root: Path = REPO) -> int:
    if len(MUTATIONS) < MIN_SELF_TEST_MUTATIONS:
        print(
            f"self-test: {len(MUTATIONS)} mutation arm(s) registered, at least "
            f"{MIN_SELF_TEST_MUTATIONS} required — a shrunk mutation population proves nothing"
        )
        return 1
    original = read_doc(root)
    base = check_document(original, root)
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
    return check_document(text, root)


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
