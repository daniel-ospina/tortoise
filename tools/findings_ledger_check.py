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
   and §6 now refuse any table row whose label cell they cannot classify, and every
   backticked `token:LINE` the citation pattern declines is refused too. The
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

#: A `path:line` occurring ANYWHERE inside a backticked span. The span-level
#: patterns above only see a span whose WHOLE body is a citation, so a legitimate
#: `out of range` red could be defused by appending one word INSIDE the span
#: (`tortoise/sdk.py:999999 (dead)`, `see …:999999 here`) — measured, rc 0.
INNER_CITATION_RE = re.compile(
    r"(?P<path>[\w~/.@+-][\w./+@-]*)\s*:\s*(?P<start>\d+)(?:-(?P<end>\d+))?"
)

#: `## N.` headings actually present, so a `§N` evidence reference can be checked
#: for existence rather than accepted as a token.
HEADING_NUMBER_RE = re.compile(r"(?m)^##\s+(\d+)\.")
SECTION_REF_TOKEN_RE = re.compile(r"§\s?(\d)")


def _has_path_marker(token: str) -> bool:
    """Is this token shaped like a path at all (`/`, `.`, or the `:LINE` shorthand)?"""
    return "/" in token or "." in token or token.startswith(":")

#: An in-page section reference (`§6`) — §1 accepts it in place of a citation for
#: a row whose evidence IS the section below it (row (l)).
SECTION_REF_RE = re.compile(r"§\s?\d")

#: A backticked command — the third evidence form §1 allows
#: (`gh issue view N`, `ls tortoise/semantic_extractor.py`).
COMMAND_RE = re.compile(
    r"^`(?:\.venv/bin/|python3?\b|gh\b|git\b|ls\b|bash\b|npm\b|npx\b|uv\b|grep\b|rg\b)"
)

#: A backticked issue/PR reference.
ISSUE_REF_RE = re.compile(r"`#\d+`")

CORRECTION_ROW_RE = re.compile(r"^\|\s*\*\*([a-z])\*\*\s*\|")

#: A markdown table's header separator (`|---|---|`).
TABLE_SEPARATOR_RE = re.compile(r"^\|[\s:|-]+\|$")
#: A row of §1's vocabulary table: `| **VERDICT** | Means | What a lane should do |`.
VOCAB_ROW_RE = re.compile(r"^\|\s*\*\*(?P<token>[A-Z][A-Z-]*)\*\*\s*\|")

#: Floor on the mutation population. `--self-test` is the arm the page's own
#: justification rests on (*"`--self-test` is what proves the rules still fire"*),
#: and `MUTATIONS = ()` is a ONE-place edit: without a floor it printed
#: "PASS (0 failing arm(s))" and exited 0 (measured), so every gate stayed green
#: over a self-test that proved nothing — a gate that reports OK when it cannot
#: run. `tools/test_design_map_check.py` ships the same floor for the same reason.
MIN_SELF_TEST_MUTATIONS = 25
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
        if (
            CITATION_RE.fullmatch(body)
            or CITATION_SHAPE_RE.match(body[1:-1])
            or COMMAND_RE.match(body)
        ):
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
    if expected not in paragraph:
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
    non_still_sentence = " ".join(
        s for s in sentences if "no longer read as written" in s
    )
    still_sentence = " ".join(
        s for s in sentences if "still read as written" in s and "no longer" not in s
    )
    named = set(re.findall(r"\*\*\(([a-z])\)\*\*", non_still_sentence))
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
    misattributed = {
        label: (attributed.get(label), verdicts.get(label))
        for label in non_still
        if attributed.get(label) != verdicts.get(label)
    }
    if misattributed:
        detail = "; ".join(
            f"({label}) stated {stated}, table says {actual}"
            for label, (stated, actual) in sorted(misattributed.items())
        )
        problems.append(
            f"§3 tally: the sentence assigns a different verdict than the row carries "
            f"— {detail}. Each class has its own handling rule in §1, so a swapped "
            "class is not a cosmetic error"
        )
    still_true = sorted(lbl for lbl, v in verdicts.items() if v == "STILL-TRUE")
    named_still = set(re.findall(r"\*\*\(([a-z])\)\*\*", still_sentence))
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
    if expected_overtaken not in paragraph:
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
    """
    section = _region(text, "## 1. What a row means", "## 2. The four defect classes")
    defined = tuple(
        m.group("token")
        for m in (VOCAB_ROW_RE.match(line) for line in section.splitlines())
        if m
    )
    if defined != VERDICTS:
        return [
            f"§1 vocabulary drift: the page defines {defined or '(none)'}, the guard "
            f"enforces {VERDICTS} — in either direction a verdict has no handling "
            "rule, and §8 names §1 as the authority"
        ]
    return []


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
    exempted = 0
    anchor: str | None = None
    for match in CITATION_RE.finditer(text):
        raw = match.group(0)[1:-1]
        start, end = int(match.group("start")), match.group("end")
        span = f"{start}{'-' + end if end else ''}"
        rel = match.group("path")
        origin = ""
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
            # `python3:11`, `localhost:6379` — PROSE, not a path. The resolver used
            # to have no marker rule while the refusal arm did, so a host:port
            # example red the required `docs` job as "does not exist in the tree"
            # (measured). The carve-out — a marker-less extension-less FILENAME
            # such as `Makefile:12` is unmeasured — is named in §8 rather than
            # implied away.
            continue
        if rel.startswith("~"):
            # A dated reading of something OUTSIDE this repository — §5's `~/.swarm`
            # tree. §8 names this as a carve-out, and `exempted` is counted
            # separately so a page made ENTIRELY of exempt citations falls through
            # to the "measured nothing" floor below instead of reporting clean.
            exempted += 1
            continue
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
        # one through by DISCARDING `root` (absolute paths are exempted above by
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
            f"measured nothing ({len(seen)} citation(s) seen, {exempted} exempt)"
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


def check_document(text: str, root: Path = REPO) -> list[str]:
    """Every arm that reads this checkout's TREE — no `git` history required.

    This is the half that survives a depth-1 clone (the `test` jobs, and the
    `core` surface `tests/test_findings_ledger_2852.py` runs in). It is not a
    weakened check: it is everything the page claims about the tree. The one
    claim it cannot answer — that `measured_at_commit` is on HEAD's history —
    lives in `check_all()`, because answering it needs the whole clone.
    """
    problems: list[str] = []
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
    (
        "a shorthand citation pointing past the end of its anchor's file",
        lambda t: t.replace("`:3115`", "`:999999`", 1),
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
