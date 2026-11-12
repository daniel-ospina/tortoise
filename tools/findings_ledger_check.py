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
import html
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

#: The indexed population: 40 issues + 2 PRs, per §6's own three columns. ⛔ PINNED BY
#: IDENTITY, not by size (#11902 review round 3): a COUNT pin is satisfied by
#: substitution, so `#2726` replaced by a number the ledger never indexed read CLEAN —
#: every row reconciled, the sum was 42, the census was 42 distinct and there was no
#: duplicate. The set below is derived from the shipped page and each number must be
#: present in exactly one state row; `PINNED_INDEXED_ROWS` is its length so the two
#: cannot drift, the same discipline `PINNED_CORRECTION_LABELS` follows above.
PINNED_INDEXED: frozenset[int] = frozenset(
    {
        1370, 1418, 1509, 1535, 1695, 2083, 2105, 2296, 2313, 2684,
        2696, 2697, 2714, 2725, 2726, 2727, 2728, 2729, 2730, 2734,
        2742, 2747, 2748, 2749, 2766, 2767, 2781, 2782, 2783, 2786,
        2787, 2788, 2790, 2792, 2795, 2811, 2813, 2814, 2817, 2818,
        2820, 2826,
    }
)
PINNED_INDEXED_ROWS = len(PINNED_INDEXED)

#: §6's per-state rows, and the label each must carry in its first column.
#: A `\d+` run is UNBOUNDED, and Python refuses to convert an integer literal longer than
#: 4,300 digits (`sys.set_int_max_str_digits`) — so ANY `int()` on text a page controls can
#: raise `ValueError`, which escapes `main()`'s `except Unverifiable` as a traceback with
#: exit **1**: the code §8 reserves for "a named claim is false", over a page that may
#: state nothing at all. Measured at FOUR sites in this module (#11902 review rounds 3 and
#: 4 — the §6 count cell, a `§N` reference, a heading number, and a citation's line span).
#: No caller below converts a longer run: a number this long cannot be a real section,
#: line or population, so each caller REFUSES it instead of converting it.
MAX_CONVERTIBLE_DIGITS = 9


def _convertible(raw: str) -> bool:
    """Is `raw` short enough that `int(raw)` cannot raise? (`\\d+` is unbounded.)"""
    return len(raw) <= MAX_CONVERTIBLE_DIGITS


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
#: head ONE span is refused across the whole `docs/` tree — `collision_preflight.py 10984`,
#: a bare tool invocation the arm cannot tell from a path because it has no recognised
#: program prefix (the `uv run`-prefixed form IS excluded). None is on this page, and
#: the earlier delimiter sets refused 7, then 600+; each figure was re-measured rather
#: than carried forward, which is why this line states the current one.
#: THE DELIMITER IS WHITESPACE OR A STRAY `|`, and every wider version was measured
#: against a false accusation over `docs/**` before it was dropped:
#:   * `_` parsed the identifier `tests/test_findings_ledger_2852.py` as `path _ 2852`;
#:   * `-` collided with a dated slug (`2026-10-15-7879-postgres-…`) and a commit ref;
#:   * `/` parsed plain paths as citations — `docs/evidence/11083/README.md`,
#:     `2024/03/01` — 600+ spans across the whole tree then (re-derivable, not carried);
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
    # was measured for it: every code span in the 1150 tracked files under `docs/` plus
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
#: MEASURED, as every delimiter in this file must be: every code span in all 1150
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

#: Placeholder cells: a §3 content cell whose every word is one of these records nothing —
#: UNLESS the cell also carries a digit (`n/a 5`, `x = 2`), because a digit is content the guard
#: reads as such everywhere else, and refusing it here refused a cell a reader can read.
#: ⛔ This is deliberately NOT a word-count floor. A length floor on these two columns sits
#: AT the page's own minimum, not far below it — measured across the twelve rows: the
#: shortest retracted claim and the shortest correction are BOTH three words, so a
#: three-word floor reds a correct page the moment a lane compresses a legitimate cell
#: (row l's correction cut to `#2748, #1695, #1418 **closed**` names exactly what closed,
#: is ONE content word, and was refused as "records nothing" by the first draft of this
#: rule), and an ASCII-only word class counts a non-Latin correction as ZERO. So the rule
#: refuses emptiness and placeholders instead of shortness: it closes the measured bypass
#: (a blanked column) without ever accusing brief content of recording nothing.
#:
#: ⛔ IT IS A DECLARED DENYLIST, NOT A CLOSED CLASS, and §8 says so in those words. A
#: placeholder outside this set is NOT refused — that is the arm's honest limit, and
#: stating it is what stops a lane reading "placeholder" as "any placeholder".
PLACEHOLDER_WORDS = frozenset(
    {"na", "n", "tbd", "tba", "tbc", "todo", "none", "nil", "null", "xx", "x"}
)

#: A content word: a run of LETTERS, Unicode-aware (`**Ложно**` counts, `**False**` counts),
#: so a dash, a bare number or an issue ref is not a word while `_upsert_point_props` is
#: THREE (`upsert`, `point`, `props` — `_` separates, it is not part of a word).
CONTENT_WORD_RE = re.compile(r"[^\W\d_]+(?:[-'\u2019][^\W\d_]+)*", re.UNICODE)

#: A HTML tag or comment. Stripped BEFORE the words are counted, because a cell can render
#: empty while testing non-empty: `<br>`, `<span>&nbsp;</span>` and `<!-- removed -->` all
#: tokenise to letters and all read as a blank table cell. Measured at `c5990249c0`:
#: `<br>` in every §3 correction cell left `--check` at rc 0 — the fail-open the blank-cell
#: floor beside it could not see, because a tag is not whitespace.
#: ⛔ TAG-SHAPED, not "any angle-bracket span". On its own that narrowing refuses nothing the
#: wide pattern also refuses — a comparison cell carries letters or digits either way — so what
#: it buys is the WORD LIST: `<[^<>]*>` swallowed `y` in `x < y > z` (`['x', 'z']`), and the
#: word list is what the placeholder and copy tests read, so a comparison inside a §3 cell could
#: shift a copy comparison. Pinned by
#: `test_a_comparison_keeps_its_operand_words`; the comment form is matched on its own.
#: A COMMENT, with the renderer's own alternation VERBATIM (`markdown_it.common.html_re`):
#: `<!-->` and `<!--->` are the abrupt-closing empty forms; `<!---->` is the ORDINARY empty
#: comment, matched by the general rule; and a comment's text "must not end with `-`", so
#: `<!--a--->` and `<!----->` are literal TEXT rather than comments (measured by the seventh review
#: of this change). Which of them a cell may hold is the content floor's business, not this
#: matcher's: `<!--a--->` carries the letter `a`, while `<!----->` carries no letter or digit and is
#: refused by the letter-or-digit rule. ⛔ Do NOT add a special empty-form alternative: a
#: `<!---->?` branch matched the first four dashes of `<!----x-->`, cut a TERMINATED comment in
#: half, and read its body as content — a blank-rendering cell in all four §3 columns
#: (eighth review of this change).
COMMENT_AT_RE = re.compile(r"<!---?>|<!--(?:[^-]|-[^-]|--[^>])*-->", re.DOTALL)

#: A TAG, a DECLARATION, a CDATA section or a processing instruction — all of them render as
#: nothing in a table cell. ⛔ THE GRAMMAR IS THE RENDERER'S, not `[^<>]*`: a QUOTED attribute
#: value may contain `>`, a backtick or a backslash, and a loose pattern (plus per-segment
#: stripping) meant such a tag was never matched — its own text was counted as content while the
#: cell rendered blank, in all four §3 columns at once (measured by the sixth review of this
#: change: 41 of 84 tag shapes). Consumed as a UNIT by `_rendered_cell_text`, before any backtick
#: or escape inside it is classified. Comments are NOT in here: `_rendered_cell_text` scans for
#: them, because an unterminated `<!--` is literal text.
_TAG_ATTR = (
    r"(?:\s+[A-Za-z_:][A-Za-z0-9:._-]*"
    r"(?:\s*=\s*(?:[^\"'=<>`\x00-\x20]+|'[^']*'|\"[^\"]*\"))?)*"
)
TAG_RE = re.compile(
    r"</[A-Za-z][A-Za-z0-9-]*\s*>"  # a CLOSE tag carries no attributes
    r"|<[A-Za-z][A-Za-z0-9-]*" + _TAG_ATTR + r"\s*/?>"  # an OPEN tag may
    r"|<![A-Za-z][^>]*>|<!\[CDATA\[.*?\]\]>|<\?.*?\?>",
    re.DOTALL,
)

#: HTML RAW-TEXT and RCDATA element names. The BODY of one of these is not rendered at all, so a
#: cell holding `<script>**STILL-TRUE**</script>` renders blank while the tag pattern alone leaves
#: `**STILL-TRUE**` as ordinary text — a row could lose its verdict AND its citation with
#: `check_document()` clean (measured, ninth review of this change). ⛔ `<textarea>` and `<xmp>`
#: are NOT in the set: a browser SHOWS their text; and neither is `<plaintext>`, which shows
#: everything after it as text too (the ninth review read the tokenizer switch as hidden content
#: — a real browser renders `<plaintext>lorem ipsum` as the visible words, so dropping it was a
#: FALSE accusation; tenth review).
#: Elements whose content a browser PARSES as ordinary markup but never renders: `<template>`'s
#: contents go to an inert fragment. ⛔ Its close tag therefore has the opposite
#: spelling-sensitivity to a raw-text element's: `</template` inside a COMMENT, a TAG's quoted
#: attribute, a code span, an escape or a recognised link does NOT close it, and treating them as
#: closes made the guard READ text where a browser renders nothing — 9 measured fail-opens
#: (eleventh review of this change).
PARSED_BUT_HIDDEN_ELEMENTS = frozenset({"template"})

RAWTEXT_ELEMENTS = frozenset(
    {"script", "style", "title", "iframe", "noembed", "noframes", "noscript"}
) | PARSED_BUT_HIDDEN_ELEMENTS

#: The element name of a matched OPEN tag, for the raw-text switch above.
OPEN_TAG_NAME_RE = re.compile(r"<([A-Za-z][A-Za-z0-9-]*)")

#: A TERMINATED character reference. CommonMark requires the semicolon, so `&nbsp` renders five
#: visible characters — `html.unescape` applies HTML's LEGACY no-semicolon table and turned it
#: into U+00A0, refusing a cell a reader reads (measured by the sixth review of this change).
_ENTITY_RE = re.compile(r"&(?:#[0-9]+|#[xX][0-9a-fA-F]+|[A-Za-z][A-Za-z0-9]*);")

#: ASCII punctuation a backslash can escape (CommonMark's escapable set).
_ESCAPABLE_RE = re.compile(r"[!-/:-@\[-`{-~]")

#: A backslash-escaped ASCII punctuation character, which renders as the character itself.
ESCAPE_RE = re.compile(r"\\([!-/:-@\[-`{-~])")

#: What a reader actually SEES in a cell: tags and comments stripped, links reduced to their
#: label, entities decoded. The content test is "does this carry a letter or a digit?" —
#: ⛔ NOT "a letter" and not "a word": a bare date (`2026-09-10`) and an issue list
#: (`#2748, #1695, #1418`) each record something a reader can read, and a letters-only class
#: refused both while the error message claimed the cell was empty (measured by the review of
#: this change), which contradicts §8's "a brief cell passes".
VISIBLE_CHARACTER_RE = re.compile(r"[^\W_]", re.UNICODE)

#: A markdown link or image, reduced to its LABEL. The label is what renders, so a cell whose
#: label is empty renders blank while its URL still tokenises (`[](https://x)` -> `https`,
#: `x`). `[the props doc](…)` keeps `the props doc`, which is why the label is KEPT rather
#: than the whole link dropped. Measured at `c5990249c0`: `[](https://x)` in every §3
#: correction cell left `--check` at rc 0 once tags were handled.
LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")

#: Floor on the mutation population. `--self-test` is the arm the page's own
#: justification rests on (*"`--self-test` is what proves the rules still fire"*),
#: and `MUTATIONS = ()` is a ONE-place edit: without a floor it printed
#: "PASS (0 failing arm(s))" and exited 0 (measured), so every gate stayed green
#: over a self-test that proved nothing — a gate that reports OK when it cannot
#: run. `tools/test_design_map_check.py` ships the same floor for the same reason.
#: ⛔ WHAT THE CODE ENFORCES IS A LOWER BOUND (`len(MUTATIONS) < MIN_SELF_TEST_MUTATIONS`
#: reds), so only a SHRINKING population is caught — ADDING an arm without bumping this
#: constant passes silently, and the two are kept equal by HAND, not by the comparison.
#: 47 as of the figure arms
#: (`a falsified §3 count beside a list-free decoy`, `a falsified overtaken figure
#: beside a compliant decoy`), which closed the paragraph-scoped presence test, plus
#: the #11902-review arms — a §6 row naming the same issue twice, a §3 row whose
#: claim cell is blank, a §3 row whose correction cell is blank, then the second
#: round's re-listing across rows and leading-zero re-spelling, then round 3's
#: replacement by a number the index never carried, then round 8's two group-orientation
#: mutants (a governed complement behind an adverb, a marked cross-reference certifying a
#: member), then THIRTEEN content-column arms (below) — an entity, a tag, a comment, an
#: EMPTY-LABEL link, three placeholders (`n/a` joined, `TBC` whose twin was listed, and a
#: MULTI-WORD pair that only the `all(...)` half sees), a correction that copies its claim in
#: either case, a raw-text element's body (in mixed case, which also pins the case-insensitive
#: matching), and the three columns beside them (a verdict and an evidence cell commented out,
#: and a verdict hidden behind a commented-out one). Bump it with any arm added or removed —
#: the population is what the pin compares.
MIN_SELF_TEST_MUTATIONS = 62
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
    # ⛔ `UnicodeDecodeError` is a `ValueError`, NOT an `OSError` (#11902 review P1):
    # catching `OSError` alone let undecodable bytes escape this arm entirely, so the
    # traceback reached `main()`'s top-level handler and the tool exited **1** — the code
    # it documents as "drift, a named claim is false" — over a document nobody faulted and
    # with no claim named. §8 promises exit 2 UNVERIFIED here, never a verdict, and
    # `MEMORY.md`'s `[unicode-decode-is-not-oserror]` row records this exact shape.
    except (OSError, UnicodeDecodeError) as exc:  # unreadable is not clean
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
            # ⛔ THE BACKSLASH IS KEPT. Suppressing the SPLIT is this function's whole job; if
            # it also DELETED the backslash it rewrote the cell's markdown before every reader
            # saw it, and one character defeated all four render-blank families: `\``<br>`` `
            # reached the guard as a clean two-backtick span (accepted, CLEAN) while the
            # renderer showed an escaped backtick and a real `<br>` element — a blank cell
            # (measured by the fifth review of this change). The same rewrite refused the
            # idiomatic literal-tag spelling `\<br>`, which §8 promises still counts.
            # `_rendered_cell_text` collapses the escape the way the renderer does.
            current.append(char)
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
        # ⛔ Rendered text, not the raw cell: `<!-- **STILL-TRUE** -->` left
        # `check_document()` at `[]` (measured by the review of this change) — a verdict
        # inside a comment renders as nothing on the page. BOTH readers agree, because
        # this is the same `_rendered_cell_text` the prose floor uses.
        tokens = BOLD_TOKEN_RE.findall(_rendered_cell_text(verdict_cell))
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
    # ⛔ Reads what a READER sees, not the raw cell: an evidence cell holding
    # `<!-- `tortoise/sdk.py:17109` -->` left `check_document()` at `[]` (measured by the
    # review of this change) — the citation was there for a grep and invisible on the page.
    cell = _rendered_cell_text(cell)
    if ISSUE_REF_RE.search(cell):
        return True
    # `_convertible` first: `int()` on an unbounded `\d+` raises `ValueError`, and the
    # short-circuit is what keeps a hostile `§9999…` from reaching the conversion.
    if any(
        _convertible(n) and int(n) in sections
        for n in SECTION_REF_TOKEN_RE.findall(cell)
    ):
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


def _content_cell_words(cell: str) -> list[str]:
    """The content words in a §3 cell, casefolded, for the emptiness, placeholder and copy tests.

    ⛔ HTML ENTITIES ARE DECODED BEFORE TOKENISING, and that is a measured fix rather than
    tidiness: `&nbsp;` tokenises to `['nbsp']`, `&mdash;` to `['mdash']` and `&emsp;` to
    `['emsp']`, so every one of them clears both the emptiness branch (one "word") and the
    placeholder set — measured at `c5990249c0`: a correction cell of `&nbsp;` left
    `check_document()` at `[]` while it RENDERS as a blank table cell to every reader.
    `html.unescape` maps them to U+00A0 / U+2014 / U+2003, which carry no letter, so the
    emptiness clause refuses them. A legitimate `R&amp;D` still works: the entity decodes to
    `&` and the words beside it are untouched — a cell that is ONLY `&amp;` decodes to a
    bare `&` and carries no word, so it is refused like any other empty cell (the boundary
    is one word a reader can read, not "looks non-blank").

    ⛔ TAGS AND COMMENTS ARE STRIPPED TOO, and that is the same measured lesson one spelling
    out: decoding the ENTITY left the CLASS, so `<br>`, `<span>&nbsp;</span>` and
    `<!-- removed -->` still tokenised to letters while rendering as an empty cell —
    `<br>` in every §3 correction cell left `check_document()` at `[]` (measured at
    `c5990249c0`). Tags go first, then entities, so `<span>&nbsp;</span>` collapses to a
    non-breaking space and carries no word, while an ESCAPED tag (`&lt;br&gt;`) decodes to
    the literal text a reader sees and still counts — the direction that must not
    over-refuse a cell quoting markup.

    A markdown link is reduced to its LABEL for the same reason: the label is what renders,
    so `[](https://x)` is a blank cell whose URL still tokenises (measured at
    `c5990249c0`, once tags were handled).
    """
    return [
        w.casefold()
        for w in CONTENT_WORD_RE.findall(_rendered_cell_text(cell).replace("`", " "))
    ]


def _copy_tokens(cell: str) -> list[str]:
    """COPY-test tokens: letters AND digits, casefolded — what a row DERIVES from its claim.

    ⛔ `CONTENT_WORD_RE` is letters-only, so using it here compared two cells with their
    NUMBERS DELETED: a correction that fixes a count, a line number or a section number
    (`has 8 fields` -> `has 10 fields`, `entities.py:2679` -> `entities.py:3162`) reduced to
    its claim's own word list and was refused as "a COPY of its claim" (measured by the review
    of this change). That is the dominant correction shape in this very page — rows (b), (k)
    and (l) all correct a number — so the copy test reads digits and the placeholder test,
    which is about the ABSENCE of content, keeps the letters-only reader.
    """
    return [
        token.casefold()
        for token in re.findall(r"[^\W_]+", _rendered_cell_text(cell).replace("`", " "))
    ]


def _rendered_cell_text(cell: str) -> str:
    """What a READER sees in a cell: tags stripped, comments removed, links to their label, entities decoded.

    One function, so the content test and the verdict/evidence readers cannot disagree
    about what a cell renders as. ⛔ The verdict and evidence arms read the RAW cell until
    the review of this change measured the hole: a verdict cell holding
    `<!-- **STILL-TRUE** -->` and an evidence cell holding a citation wrapped in the same
    comment each left `check_document()` at `[]` — a row that loses its verdict AND its
    openable citation still passes §1 and §8's promise.

    ⛔ IT SCANS LEFT TO RIGHT, and that is what makes both of these correct at once:

    * a CODE SPAN is LITERAL — markdown renders `` `<br>` `` as the TEXT `<br>`, so nothing
      inside one is a tag, a comment or an entity (refusing them was a false accusation:
      `` `&nbsp;` `` is readable content, and the ESCAPED spelling `&lt;br&gt;` is accepted for
      exactly that reason);
    * a COMMENT renders as nothing, EVEN WHEN IT WRAPS A CODE SPAN — stripping comments
      globally first re-opened the commented-out-citation hole, and protecting spans first
      re-opened it from the other side.

    Neither fixed order is right: whatever starts FIRST wins, which is what the renderer does.
    So:

    * an unmatched backtick run is LITERAL TEXT (CommonMark does not open a span without a
      closing run), and treating it as code let `` ` <br> `` — a plain typo of the accepted
      `` `<br>` `` — hide a tag from the strip and pass as content (measured by the fourth
      review of this change);
    * a run of N backticks closes only on a run of exactly N, so `` ``<br>`` `` is one span
      (splitting on single backticks read its content as ordinary text and refused it);
    * an unterminated `<!--` is literal text too, for the same reason as the unmatched run.
    """
    out: list[str] = []
    i, n = 0, len(cell)
    while i < n:
        if cell[i] == "\\":
            # A backslash escapes the ASCII punctuation after it, and BOTH characters render
            # (as the punctuation). Consuming them together is what stops `\`` from opening a
            # span and `\<` from opening a tag — the two shapes the splitter's retired rewrite
            # used to hide (measured by the fifth review of this change).
            if i + 1 < n and _ESCAPABLE_RE.match(cell[i + 1]):
                out.append(cell[i + 1])
                i += 2
                continue
            out.append(cell[i])
            i += 1
            continue
        link = _link_at(cell, i)
        if link is not None:
            # ⛔ A LINK IS A UNIT, and its LABEL is what renders. Reducing links only inside
            # each scanned chunk left them behind the moment cycle 6 moved tags out of the
            # chunks: `[` starts no construct, so `[<br>](https://x)` reached `LINK_RE` as a
            # fragment ending at the `<`, the link was never reduced to its label, and the
            # DESTINATION's letters were counted as content — 109 of 156 measured
            # fail-opens (seventh review of this change), inside the very family
            # `[](https://x)` had already closed.
            out.append(_rendered_cell_text(link[0]))
            i = link[1]
            continue
        comment = COMMENT_AT_RE.match(cell, i)
        if comment is not None:
            # ⛔ The renderer's grammar, including HTML5's abrupt-closing empty forms
            # (`<!-->` and `<!--->` are COMPLETE) and the rule that a comment's text may not
            # end with `-`, so `<!--a--->` is literal text and stays readable.
            out.append(" ")
            i = comment.end()
            continue
        if cell.startswith("<!--", i):
            # Unterminated: literal text — but LATER constructs still apply, so only the four
            # characters are literal and the scan continues (handing the remainder to
            # `_rendered_inline` ate the tags inside a code span that follows it).
            out.append("<!--")
            i += 4
            continue
        tag = TAG_RE.match(cell, i)
        if tag is not None:
            # Consumed WHOLE, before any backtick, escape or comment marker inside it — a
            # tag's quoted attribute may contain all three.
            out.append(" ")
            i = tag.end()
            opened = OPEN_TAG_NAME_RE.match(tag.group(0))
            name = opened.group(1).casefold() if opened else ""
            if name in RAWTEXT_ELEMENTS:
                # The BODY is not rendered: skip to the close tag the browser ACTUALLY sees,
                # or to the end of the cell when there is none.
                i = _raw_text_end(cell, i, name)
                continue
            continue
        if cell[i] == "`":
            span_end = _code_span_end(cell, i)
            if span_end < 0:  # unmatched run: literal text
                run_end = i
                while run_end < n and cell[run_end] == "`":
                    run_end += 1
                out.append(cell[i:run_end])
                i = run_end
                continue
            # A span's content is LITERAL, and the delimiters are KEPT: the evidence reader
            # finds its citations by their backticks, and the content reader replaces the
            # delimiters with spaces, so keeping them is what lets one rendered text serve
            # both without either weakening the other.
            out.append(cell[i:span_end])
            i = span_end
            continue
        j = i
        while j < n and cell[j] not in "`\\<[!" and not cell.startswith("<!--", j):
            j += 1
        if j == i:  # a `<`, `[` or `!` that opens no construct is literal text
            out.append(cell[i])
            i += 1
            continue
        out.append(_rendered_inline(cell[i:j]))
        i = j
    return "".join(out)


def _code_span_end(cell: str, i: int) -> int:
    """End of the code span starting at `i`, or -1 when its run is UNMATCHED.

    A run of N backticks closes only on a run of exactly N, so `` ``<br>`` `` is one span; an
    unmatched run opens nothing and renders as literal text (CommonMark). One function, because
    the cell scanner and the raw-text body walk must agree about what a span is — the tenth
    review of this change measured the body walk searching the RAW cell, so a `` `</script>` ``
    inside a span closed a `<script>` that a browser keeps open (fail-open on all four §3
    columns).
    """
    n = len(cell)
    j = i
    while j < n and cell[j] == "`":
        j += 1
    width = j - i
    while j < n:
        if cell[j] != "`":
            j += 1
            continue
        k = j
        while k < n and cell[k] == "`":
            k += 1
        if k - j == width:
            return k
        j = k
    return -1


#: The close tag a browser acts on inside a raw-text body: the element name, optional
#: WHITESPACE, then `>`. ⛔ NOT `</name[^>]*>`: the tenth review of this change measured
#: `</script foo=">**STILL-TRUE**">` opening a fail-open in every column, because a browser (and
#: markdown-it, which escapes the spelling) never treats an attributed `</script …>` as the close
#: — the body stays hidden while the guard read the attribute text as content.
_RAW_TEXT_CLOSE = r"</{name}\s*>"


#: How a raw-text body starts keeping state, in the tokenizer's own vocabulary: `<script>` has an
#: ESCAPED state entered by `<!--` and a DOUBLE-escaped state entered by a nested `<script`, and a
#: `</script` closes the element only from the first two. ⛔ Measured with a real browser (eleventh
#: review of this change): `<script><!-- </script>AFTER -->` SHOWS `AFTER -->` (escaped state, so the
#: close tag ends the element), while `<script><!--<script></script>-->lorem</script>` renders BLANK
#: (double-escaped, so the inner close tag only returns to the escaped state).
_SCRIPT_START_RE = re.compile(r"<script(?=[\s/>])", re.IGNORECASE)
_COMMENT_CLOSE = "-->>"



def _mask_escapes_and_spans(
    cell: str, start: int, *, structured: bool = False, preserve: re.Pattern[str] | None = None
) -> str:
    r"""`cell[start:]` with ESCAPES and CODE SPANS blanked in place, so their characters cannot
    match a tag.

    With `structured=True` a COMMENT, a TAG (whole, with its quoted attributes) and a RECOGNISED
    link are blanked too — the model `<template>` needs, because its content is parsed as markup.
    ⛔ `preserve` keeps the close tag this body is looking for: blanking it made
    `<template></template>VISIBLE` read as one long body and refused a cell a reader can read
    (measured, eleventh review of this change).

    markdown-it emits both entity-escaped (`\</script>` and `` `</script>` `` become
    `&lt;/script&gt;`), so a close tag inside either is not one the browser ever sees — measured.
    Nothing else is masked: a COMMENT and a LINK are emitted with their content, so a `</script`
    inside them IS seen by the tokenizer (also measured — refusing those cells was this guard's
    own false accusation until the eleventh review).
    """
    chars = list(cell[start:])
    i, n = 0, len(chars)
    while i < n:
        if chars[i] == "\\" and i + 1 < n and _ESCAPABLE_RE.match(chars[i + 1]):
            chars[i] = chars[i + 1] = " "
            i += 2
            continue
        if chars[i] == "`":
            span_end = _code_span_end(cell, start + i)
            if span_end > 0:
                for k in range(i, span_end - start):
                    chars[k] = " "
                i = span_end - start
                continue
            while i < n and chars[i] == "`":
                i += 1
            continue
        if structured and (preserve is None or preserve.match(cell, start + i) is None):
            comment = COMMENT_AT_RE.match(cell, start + i)
            if comment is not None:
                for k in range(i, comment.end() - start):
                    chars[k] = " "
                i = comment.end() - start
                continue
            tag = TAG_RE.match(cell, start + i)
            if tag is not None:
                for k in range(i, tag.end() - start):
                    chars[k] = " "
                i = tag.end() - start
                continue
            link = _link_at(cell, start + i)
            if link is not None and _RECOGNISED_LINK_TAIL.match(cell[link[2]:link[1]]):
                for k in range(i, link[1] - start):
                    chars[k] = " "
                i = link[1] - start
                continue
        i += 1
    return "".join(chars)


def _raw_text_end(cell: str, start: int, name: str) -> int:
    """End of the body of the raw-text element `name` whose content begins at `start`.

    Three facts, each measured against a real renderer (tenth and eleventh reviews of this
    change):

    * an ESCAPE and a CODE SPAN are entity-escaped by markdown-it, so a close tag inside one is not
      a close tag the browser sees — hence `_mask_escapes_and_spans`;
    * a COMMENT and a LINK are NOT protected — `<script><!-- </script>AFTER -->` and
      `<script>[a](</script>VISIBLE)` both SHOW text, so treating either as opaque refused a cell a
      reader can read;
    * `<script>` alone has the tokenizer's ESCAPED and DOUBLE-escaped states, so the first
      `</script` after `<!--` closes while the one after a nested `<script` does not.
    """
    close_re = re.compile(_RAW_TEXT_CLOSE.format(name=re.escape(name)), re.IGNORECASE)
    structured = name in PARSED_BUT_HIDDEN_ELEMENTS
    body = _mask_escapes_and_spans(cell, start, structured=structured, preserve=close_re)
    state = "data"
    i, n = 0, len(body)
    while i < n:
        link = _link_at(cell, start + i)
        if link is not None and _RECOGNISED_LINK_TAIL.match(cell[link[2]:link[1]]):
            # A recognised link's destination is re-emitted as an href, so a close tag spelled
            # inside it never reaches the tokenizer — but only when markdown-it parses the link
            # at all, which is what `_RECOGNISED_LINK_TAIL` decides.
            i = link[1] - start
            continue
        if name == "script":
            if state == "data" and body.startswith("<!--", i):
                state = "escaped"
                i += 4
                continue
            if state == "escaped":
                if body.startswith("-->", i):
                    state = "data"
                    i += 3
                    continue
                if _SCRIPT_START_RE.match(body, i) is not None:
                    state = "double"
                    i += 1
                    continue
            if state == "double":
                close = close_re.match(body, i)
                if close is not None:
                    state = "escaped"
                    i = close.end()
                    continue
        close = close_re.match(body, i)
        if close is not None:
            return start + close.end()
        i += 1
    return len(cell)


#: What markdown-it requires after a link's label: a DESTINATION (angle-bracketed with no `<` or
#: `>` inside, or a bare one with no `<`, `(` or space) and an optional title, then `)`. ⛔ The
#: distinction matters inside a raw-text body, where the destination's characters decide whether a
#: `</script` ever reaches the tokenizer: `[b](</script>)` IS a link (markdown-it emits
#: `<a href="/script">`, so nothing closes the element) while `[a](</script>VISIBLE)` is NOT (the
#: angle destination ends at the first `>`, so the stray `VISIBLE)` leaves it literal and the
#: browser DOES close — both measured, eleventh review of this change).
_RECOGNISED_LINK_TAIL = re.compile(
    r"^\("
    r"\s*(?:<[^<>\n]*>|[^()<\s][^()<]*?)"
    r"(?:\s+(?:\"[^\"]*\"|'[^']*'|\([^()]*\)))?"
    r"\s*\)$"
)


def _link_at(cell: str, i: int) -> tuple[str, int, int] | None:
    """`(label, end, destination_start)` for a markdown link or image at `i`, else None.

    Bracket- and paren-depth matched, because a label may contain brackets
    (`[![badge](img)](url)`) and a destination may contain balanced parens. A REFERENCE-style
    link (`[label][ref]`) returns None: this page defines no reference definitions, so such a
    cell renders its literal text (and accepting it is correct, not a limit).
    """
    j = i + 1 if cell.startswith("![", i) else i
    if j >= len(cell) or cell[j] != "[":
        return None
    depth, k = 0, j
    while k < len(cell):
        char = cell[k]
        if char == "\\":
            k += 2
            continue
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                break
        k += 1
    else:
        return None
    if depth != 0 or k + 1 >= len(cell) or cell[k + 1] != "(":
        return None
    label = cell[j + 1:k]
    m, parens = k + 2, 1
    while m < len(cell):
        char = cell[m]
        if char == "\\":
            m += 2
            continue
        if char == "(":
            parens += 1
        elif char == ")":
            parens -= 1
            if parens == 0:
                return label, m + 1, k + 1
        m += 1
    return None


def _rendered_inline(text: str) -> str:
    """Ordinary markdown text: tags stripped, links reduced to their label, entities decoded.

    ⛔ Only TERMINATED character references are decoded (`_ENTITY_RE`): CommonMark requires the
    semicolon, and the no-semicolon legacy table decoded `&nbsp` (five visible characters) into
    U+00A0 and refused the cell as empty.
    """
    stripped = LINK_RE.sub(r"\1", TAG_RE.sub(" ", ESCAPE_RE.sub(r"\1", text)))
    return _ENTITY_RE.sub(lambda m: html.unescape(m.group(0)), stripped)


def check_evidence_cells(text: str) -> list[str]:
    """§3: every row must carry a claim, a correction, and openable evidence.

    Without this arm the whole evidence column can be EMPTIED into greenness: the
    verdict vocabulary still passes (it reads a different cell) and a citation
    check over zero citations has nothing to red — measured: blanking every §3
    evidence cell left the guard reporting CLEAN while each row went on asserting
    a verdict with no evidence at all. That is precisely the shape §1 calls "not
    evidence; it is a memory", and the bypass is the one that matters here: a real
    drift reds the REQUIRED `docs` job, and deleting the citation turns the red
    green.

    ⛔ The floor covers the PROSE columns too, not only the citation (#11902 review
    P2). With it on the evidence cell alone a row could still be reduced to
    `| **e** |  |  | **ADOPTED** | <citation> |` — a verdict with neither the claim
    that was retracted nor the correction that replaced it, which is the same
    "a verdict with nothing behind it" hole the evidence floor exists to close, one
    and two columns to the left (measured: blanking either cell left the page CLEAN).

    ⛔ AND THE PROSE FLOOR IS NOT A WHITESPACE TEST (#11902, second pass). `str.strip()`
    sees a cell that is not blank; a READER sees a cell that renders blank. Measured at
    `c5990249c0`, after the whitespace floor had landed: `&nbsp;`, `<br>`, `<!-- x -->`,
    `[](https://x)`, `n/a` and `tbc` in a content cell each left `--check` at rc 0, and a
    correction replaced by a verbatim copy of its own claim did too — the row goes on
    asserting a verdict beside two columns that record nothing, which is the same hole one
    dialect out. So the cell is read through `_content_cell_words` (tags stripped, links
    reduced to their label, entities decoded) and refused when it carries nothing a reader can
    read, when every word is a declared placeholder AND the cell carries no digit (a digit is
    content everywhere else in this arm, so `n/a 5` passes), and — for the correction only —
    when its token sequence equals its claim's (letters AND digits, so a derived count is a
    correction rather than a copy).

    ⛔ THE RULE IS EMPTINESS, NOT LENGTH. A three-word floor was written first and measured
    false: the page's own shortest cells ARE three words, so it sat at the minimum instead
    of below it, and a legitimate compression — `#2748, #1695, #1418 **closed**`, which
    names exactly what closed — was refused as "records nothing". Over-refusal is the
    false-accusation class this arm must not create (a guard that reds correct prose is a
    guard a lane routes around, which is how a real drift later merges green), so a BRIEF
    cell passes: the boundary is one character a reader can read. The EMPTINESS reader is
    `VISIBLE_CHARACTER_RE` (a letter or a digit, Unicode-aware) — making IT ASCII-only reds the
    non-Latin case at once — while `CONTENT_WORD_RE` is the WORD reader that the placeholder and
    copy tests use, and it is Unicode-aware so a non-Latin word is a word (`x Ложно` is a
    placeholder beside a non-Latin word, not a placeholder alone).

    The copy test compares the WORDS, not the raw cells: a correction that repeats its claim
    with different markup (`**Props are not durable by construction**`) or different case
    (`PROPS ARE NOT DURABLE BY CONSTRUCTION`) is a copy in every reading that matters, and
    raw equality passes both. Equality only, never containment: a correction that quotes the
    claim and ADDS to it (`**False.** Props are not durable by construction`) passes.
    """
    problems: list[str] = []
    section = _region(text, "## 3. The corrections log", "## 4. The artefact index")
    sections = {int(n) for n in HEADING_NUMBER_RE.findall(text) if _convertible(n)}
    for offset, line in _table_rows(section):
        match = CORRECTION_ROW_RE.match(line)
        if match is None:
            continue  # check_verdicts owns the shape refusal
        cells = _split_row_cells(line)
        # Cells: 0 = the empty cell before the leading `|`, 1 = label, 2 = the
        # retracted claim, 3 = the correction, 4 = verdict, 5 = evidence.
        claim = cells[2] if len(cells) > 2 else ""
        correction = cells[3] if len(cells) > 3 else ""
        for index, column, absence in (
            (2, "claim", "names no retracted claim"),
            (3, "correction", "states no correction"),
        ):
            value = cells[index] if len(cells) > index else ""
            words = _content_cell_words(value)
            # ⛔ A LETTER OR A DIGIT, not a word: a bare date and an issue list each
            # record something a reader can read (measured by the review of this change —
            # a letters-only class refused both and said the cell was empty).
            rendered = _rendered_cell_text(value)
            if not VISIBLE_CHARACTER_RE.search(rendered):
                problems.append(
                    f"row ({match.group(1)}) at §3 line {offset}: its {column} cell "
                    f"carries nothing a reader can read — it {absence} once tags, "
                    "comments, links and entities are stripped, so the row asserts a "
                    "verdict with nothing beside it to have changed"
                )
            elif (
                words
                and not any(ch.isdigit() for ch in rendered)
                and (
                    all(w in PLACEHOLDER_WORDS for w in words)
                    or "".join(words) in PLACEHOLDER_WORDS
                )
            ):
                # `n/a` tokenises to ['n', 'a'] because `/` separates, so the JOINED form is
                # tested too — otherwise the most likely placeholder spelling of all is the
                # one the set does not see (measured: the self-test arm for it went uncaught
                # until this line read the joined form).
                problems.append(
                    f"row ({match.group(1)}) at §3 line {offset}: its {column} cell is a "
                    f"placeholder ({' '.join(words)!r}) — it {absence}, and a placeholder "
                    "records nothing beside a verdict that still asserts a change"
                )
        claim_words = _copy_tokens(claim)
        if claim_words and claim_words == _copy_tokens(correction):
            problems.append(
                f"row ({match.group(1)}) at §3 line {offset}: its correction is a COPY "
                "of its claim — a row that restates the claim it exists to correct "
                "records no correction at all"
            )
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


def _count_word(n: int) -> str:
    """`COUNT_WORDS[n]`, or the digits when `n` is past the end of the tuple.

    ⛔ NEVER index `COUNT_WORDS` bare. `CORRECTION_ROW_RE` accepts labels `a`-`z` while
    this tuple stops at `twelve`, so appending a thirteenth correction row made
    `COUNT_WORDS[13]` raise `IndexError` out of `check_document()` — and `main()` catches
    only `Unverifiable`, so the CLI exited **1**, the code §8 reserves for "a named claim
    is false", with a traceback over a page edit (measured, review round 5). One site was
    already guarded inline and three were not, which is exactly how the hole survived: a
    guard that is written per-expression is a guard that gets forgotten. Every site goes
    through here now.
    """
    return COUNT_WORDS[n] if n < len(COUNT_WORDS) else str(n)

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
        # ⛔ Rendered text, not the raw cell: `<!-- **STILL-TRUE** -->` left
        # `check_document()` at `[]` (measured by the review of this change) — a verdict
        # inside a comment renders as nothing on the page. BOTH readers agree, because
        # this is the same `_rendered_cell_text` the prose floor uses.
        tokens = BOLD_TOKEN_RE.findall(_rendered_cell_text(verdict_cell))
        out[match.group(1)] = tokens[0] if tokens else ""
    return out


#: A letter a sentence names as a list member IN UNAMBIGUOUS MARKUP — bold, code,
#: italic, or parentheses. Here the MARKUP IS THE CLAIM: nothing about the surrounding
#: prose decides whether `**(a)**` or `` `a` `` is a member, so these forms are read
#: unconditionally. ⛔ ONE extractor for all of them, never `parens or plain`:
#: `set(findall(parens)) or set(findall(plain))` short-circuits, and the shipped tally
#: sentence always carries `**(c)**`, so the plain branch was DEAD — measured: appending
#: `, and a, f too` to the tally sentence left `--check` at rc 0 over a page naming two
#: STILL-TRUE rows as overtaken. The same dead-branch shape hid `**a**` and the
#: complement arm entirely (which read `\*\*\(x\)\*\*` only).
#:
#: ⛔ CODE AND ITALIC ARE MARKUP THE CANDIDATE GATE ACCEPTS, so the extractor must read
#: them too — otherwise a sentence can QUALIFY on its markup and then contribute no
#: letters, and the comparison silently under-counts. Measured at `96cfa33c86` and
#: `b12cd02f33` (independent probes, same result): appending `, and `a` too` or
#: `, and *a* too` to the tally sentence left `--check` at rc 0 over a page naming a
#: STILL-TRUE row as overtaken, and `**(j)**, and `c` still read as written` did the
#: same on the complement arm. §8 of the page says a letter may be written "bold, code,
#: italic or bare", so the doc promised a comparison the code did not make.
#:
#: Letters are restricted to `[a-l]` — the page's own twelve rows.
#: ⛔ THE ONE SPELLING LIST. Every reader of a row letter — the extractor
#: (`_NAMED_LETTER_RE`, built from THIS string), verdict ATTRIBUTION
#: (`_letters_in_span`), the MENTION scan and the negated-COMPLEMENT strip — reads the
#: spellings from here, so a spelling added in one place cannot be missing from another.
#: The four ARM readers used to carry their own `\*\*\(([a-z])\)\*\*` — `**(x)**`
#: ONLY — while the extractor and §8 accept four spellings, so `**` was the difference
#: between a red and a green in the very arm whose job is to refuse a contradiction.
#: Measured: `Note (d) is **ADOPTED**.` beside a `(d)` row read CLEAN (the fail-open);
#: the class-first spelling the checker's own comment blesses was REFUSED as a swap of
#: all seven rows; the documented negated complement was COUNTED into the STILL-TRUE
#: list. ⚠️ Deriving the extractor from this string (rather than keeping two literals in
#: step by hand) is what makes that permanent — a first attempt at this fix left a
#: SECOND literal beside it, and a review measured the drift it permitted: a spelling
#: added to one side alone silently re-opened the fail-open with a green suite.
#: `test_the_span_readers_and_the_extractor_share_one_spelling_list` pins the single
#: source, so that class of drift now reds instead of shipping.
_LETTER_ALT_MARKED = (
    r"\*\*\(([a-l])\)\*\*"  # **(a)**
    r"|\*\*([a-l])\*\*"  # **a**
    r"|`\(?([a-l])\)?`"  # `a` / `(a)`
    r"|\*\(([a-l])\)\*"  # *(a)*
    r"|\*([a-l])\*"  # *a*
    r"|\(([a-l])\)"  # (a)
)
_NAMED_LETTER_RE = re.compile(_LETTER_ALT_MARKED, re.IGNORECASE)
#: The marked spellings PLUS the bare one. Bare is safe ONLY where a group is ANCHORED to
#: the clause end (the negated complement), which is why attribution does not use this:
#: there, a bare letter is prose, and `a`/`i` are English words (#12028's own finding).
_LETTER_ITEM_ANY = "(?:" + _LETTER_ALT_MARKED + r"|\b[a-l]\b)"
#: A span that is NOTHING BUT one list item, separators and whitespace aside. Used by the
#: governed-complement arm: a lone MARKED letter inside a span that carries other words is
#: this page's cross-reference idiom (`(see correction **(i)**)`), while a span that IS the
#: item is a one-member list. The distinction is the difference between a fail-open and a
#: false accusation — both were measured (#11902 review round 8).
_LIST_SEPARATOR_ALT = r"(?:,|;|&|—|–|-|\band\b|\bor\b|\balso\b|\bplus\b|\bas\s+well\s+as\b|\bfollowed\s+by\b|\bnamely\b)"
_SOLE_ITEM_RE = re.compile(
    rf"\s*(?:{_LIST_SEPARATOR_ALT}\s*)*"
    rf"(?:{_LETTER_ITEM_ANY})"
    rf"(?:\s*{_LIST_SEPARATOR_ALT})*\s*",
    re.IGNORECASE,
)


def _letters_in_span(span: str) -> list[str]:
    """The letters named in `span`, in every spelling the extractor accepts.

    Used by verdict ATTRIBUTION, where each span is the prose between two verdict
    tokens. Marked spellings are read unconditionally (the markup IS the claim); a BARE
    letter is read only when it is a member of a list RUN, which is the same rule
    `_named_letters` applies — so `, and **(j)**` assigns nothing to `j` twice while a
    bare letter in `d, e are **ADOPTED**` is still read.
    """
    out: list[str] = []

    def _take(match: re.Match[str]) -> str:
        out.extend(g.lower() for g in match.groups() if g)
        return _MEMBER_MASK

    masked = _NAMED_LETTER_RE.sub(_take, span)
    out.extend(sorted(_bare_list_members(masked)))
    return out

#: The placeholder that stands in for a member `_NAMED_LETTER_RE` already read, so the
#: BARE scan below cannot re-read the letter inside `(a)` and so a marked member still
#: COUNTS as one item of a list run (it is a `\u0000`, not a letter — it contributes no
#: member itself, it only holds the run together).
_MEMBER_MASK = "\u0000"

#: One item of a list run: a masked member, or a BARE single letter — no markup at all,
#: outside any word and outside any markup.
_RUN_ITEM_RE = re.compile(r"\u0000|(?<![\w*`])([a-l])(?![\w*`])", re.IGNORECASE)

#: What may stand BETWEEN two items of the SAME list — separators and whitespace only,
#: the whole span. A word of any other kind ends the run, which is what keeps prose out:
#: in `… and **(j)** still read as written, and I think so.` the span between the last
#: member and the `I` carries `still read as written, and`, so the `I` starts a run of its
#: own — and its run has ONE item, so it contributes nothing (a run of two or more
#: contributes wherever it sits; see the FIRST over-reading below, which states that cost).
_LIST_SEP_RE = re.compile(
    r"\s*(?:(?:,|;|&)\s*"
    r"|\b(?:and|or|also|plus|as\s+well\s+as|followed\s+by|namely)\b\s*)+",
    re.IGNORECASE,
)

#: A bare `a`/`i` at the END of a run needs the text AFTER it to say so, and the only
#: endings that do are the sentence's own: a full stop that does not begin an
#: abbreviation, the end of the string, a CLOSING PARENTHESIS (`)` — the class is
#: `[):—–]`, so `]` and `}` are NOT accepted), a colon or a dash, or one of the arm's own
#: predicates as a whole word (`(?![\w-])`: `still read` yes, `still-unsolved` no).
#: ⛔ NEITHER A COMMA NOR A SEMICOLON IS AN ENDING HERE, for the same reason: both are RUN
#: SEPARATORS (`_LIST_SEP_RE`), so a separator after the run's last item means the run had
#: NOT ended there — if no item follows it, the list is over and prose continues. So
#: `Only a, f, h, i, and then some.` and `Only a, f, h, i; the rest` are prose continuing the
#: sentence, and accepting either read a phantom `i` (the comma case measured by the
#: verification gate at this head). `(?![A-Za-z])`
#: refuses the `.` of `i.e.`.
_PROSELIKE_LETTERS = frozenset("ai")
_MEMBER_END_RE = re.compile(
    r"\s*(?:[):\u2014\u2013]"
    r"|\.(?![A-Za-z])"
    r"|$"
    r"|(?:still|only|no\s+longer|read|reads|remain|remains|stand|stands|stood"
    r"|are|is|was|were|do|does|did|have|has|had)(?![-\w]))",
    re.IGNORECASE,
)

#: A separator that is ONE PUNCTUATION MARK (`\s*[,;&]\s*`) or ONE BARE CONNECTOR
#: (`and`, `or`, `also`, `plus`, `as well as`, `followed by`, `namely`) — each regex
#: matches exactly one, so a doubled join (`a,; i`, `a and or i`) is not matched either.
#: ⛔ WHAT AN INTERIOR `a`/`i` NEEDS IS A HOMOGENEOUS SEPARATOR — one mark or one
#: connector — because both are documented list separators (`a, f, h, i, j` and
#: `a and f and h and i and j` must each read every member). A MIXTURE (`a, f, h, and i, j`)
#: is the DECLARED OVER-REFUSAL, and it is deliberate: `, and i,` is byte-identical to
#: prose, so with the same bytes we can either read a phantom (`…, and I, **(j)** …` closed
#: a stale complement list — measured) or drop a member of a mixed-separator list. A false
#: PASS outranks the friction of a false accusation (AGENTS.md A/B: fail-open is A,
#: over-block is B), so the mixture is refused and the loss is stated. The TAIL is NOT
#: affected: the Oxford-comma spelling `a, f, h, and i.` reads its `i`, because a run's last
#: item is decided by what FOLLOWS it, not by its left separator.
_PUNCTUATION_SEP_RE = re.compile(r"\s*[,;&]\s*")
_CONNECTOR_SEP_RE = re.compile(
    r"\s*\b(?:and|or|also|plus|as\s+well\s+as|followed\s+by|namely)\b\s*",
    re.IGNORECASE,
)


def _bare_list_members(masked: str) -> set[str]:
    """Bare letters that are members of a LIST — a run of two or more items.

    ⛔ MEMBERSHIP IS RUN MEMBERSHIP, NOT A LOOKAHEAD. The earlier shape tried to decide
    a bare letter's membership from the characters immediately after it, and that rule
    was wrong in BOTH directions at once (#12028): too tight, it dropped a member the
    sentence writes (`Only a, f, h, i, j still read as written.` → `['a','f','h','i']`,
    a false accusation on a spelling §8 accepts); too loose, it took any `.` as a list
    ending, so `, i.e. today.` supplied `i` and closed a STALE complement list — and the
    connector branch had no ending rule at all, so `, and I think so.` supplied `i` and
    `, and a claim §1 supports.` supplied `a`. Both were measured CLEAN over a page whose
    complement list contradicts its own table.

    A list is a RUN: items joined by separators and nothing else. English prose appended
    to a sentence starts a new run (`still read as written, and I …` has words between),
    and a run of one item is not a list. `masked` must already have every marked member
    replaced by `_MEMBER_MASK`; those are counted by the caller and are item anchors here.
    """
    out: set[str] = set()
    items = [(m.start(), m.end(), m.group(1)) for m in _RUN_ITEM_RE.finditer(masked)]
    runs: list[list[tuple[int, int, str | None]]] = []
    for item in items:
        if runs and _LIST_SEP_RE.fullmatch(masked[runs[-1][-1][1] : item[0]]):
            runs[-1].append(item)
        else:
            runs.append([item])
    # ⛔ EVERY RUN CONTRIBUTES, and that is a stated OVER-READING rather than an oversight:
    # membership is sentence-GLOBAL, so a two-item letter run in prose elsewhere in the
    # sentence supplies members too. Measured: appending `; see h, i.` to a complement list
    # that OMITS `i` reads CLEAN, because `h, i` is a second run whose tail (`i`, followed by
    # a full stop) satisfies the ending rule. Scoping the arm to the sentence's FIRST run
    # would close it, and was rejected: `test_a_letter_added_to_either_tally_list_is_compared_in_every_spelling`
    # and `test_a_code_or_italic_letter_is_read_like_every_other_markup` both require a letter
    # added in a LATER clause (`… **(l)** are **DRIFTED**, and a, f too.`) to be compared, so
    # the same mechanism is also a contract. Bytes are identical in both directions; the
    # residual census below states the cost.
    for run in runs:
        if len(run) < 2:
            continue
        for pos, (start, end, letter) in enumerate(run):
            if letter is None:  # a masked marked member — already counted
                continue
            if letter.lower() in _PROSELIKE_LETTERS and not _proselike_has_evidence(
                masked, run, pos, start, end
            ):
                continue
            out.add(letter.lower())
    return out


def _listed_letters(sentence: str, *, lone_marked: bool = True) -> set[str]:
    """The letters `sentence` LISTS — its members of a list RUN, in any spelling.

    ⛔ THIS IS *NOT* `_named_letters`, AND THE DIFFERENCE IS A MEASURED FAIL-OPEN (#11902
    review round 7, P1). `_named_letters` reads a MARKED letter UNCONDITIONALLY, which is
    correct where the markup IS the claim (verdict ATTRIBUTION: `row (c) still read as
    written` names `c`), but the COMPLEMENT comparison compares a LIST, and ordinary prose
    marks letters too: `(i)` is the standard roman-numeral enumerator and `(a)`/`(c)` are
    this page's own cross-reference idiom. So with a marker read unconditionally, a list
    that OMITS `i` read CLEAN beside a tail of prose:

        `**(a)**, **(f)**, **(h)** and **(j)** still read as written (see (i)).`  -> CLEAN
        `**(f)**, **(h)**, **(i)** and **(j)** still read as written, unlike (a).` -> CLEAN

    Both were measured against the shipped page before this function existed; both RED now.
    ⛔ AND `_bare_list_members` ALONE IS NOT THE FIX: it SKIPS masked items, so it would
    drop every member of the page's own `**(a)**, **(f)**, **(h)** and **(j)**` spelling —
    the false-accusation class this arm must not create. Membership here is RUN membership
    for EVERY spelling: a marked member holds its run together and is counted with it.

    ⛔ THE ONE-MEMBER CASE, STATED RATHER THAN IMPLIED. A run of ONE contributes its item
    ONLY IF the item is MARKED (bold, code or italic) — never if it is PARENTHESISED. That
    is not a new rule, it is the page's own candidacy rule applied to the comparison:
    `PAREN` has always been "a LIST ITEM only, never a standalone match", because a single
    `row (c) …` in prose is not a list. The consequence is the DECLARED OVER-REFUSAL side
    of the A/B trade (fail-open is A, over-block is B): a one-member complement written as
    a lone `(a)` is no longer read, so a table with exactly one STILL-TRUE row must spell
    it marked (`` `a` `` / `**a**` / `*a*`) rather than parenthesised. The alternative —
    reading a lone parenthesised letter — is the phantom above, and a false PASS outranks
    an over-block.
    """
    marked: list[tuple[str, bool]] = []

    def _take(match: re.Match[str]) -> str:
        letter = next(g for g in match.groups() if g).lower()
        # A PARENTHESISED spelling is the only alternative in `_LETTER_ALT_MARKED` that
        # begins with `(`; every other one begins with `*` or a backtick.
        marked.append((letter, match.group(0).startswith("(")))
        return _MEMBER_MASK

    masked = _NAMED_LETTER_RE.sub(_take, sentence)
    # ⛔ NOT AN OFFSET MAP. A masked item is ONE character wherever it sat, so `masked`'s
    # offsets are not the sentence's; `sub` walks its matches left to right, so the k-th
    # placeholder is the k-th recorded spelling, and the counter below is that pairing.
    marked_at: dict[int, tuple[str, bool]] = {}
    seen = 0
    for pos, char in enumerate(masked):
        if char == _MEMBER_MASK:
            marked_at[pos] = marked[seen]
            seen += 1
    items = [(m.start(), m.end(), m.group(1)) for m in _RUN_ITEM_RE.finditer(masked)]
    runs: list[list[tuple[int, int, str | None]]] = []
    for item in items:
        if runs and _LIST_SEP_RE.fullmatch(masked[runs[-1][-1][1] : item[0]]):
            runs[-1].append(item)
        else:
            runs.append([item])
    out: set[str] = set()
    for run in runs:
        for pos, (start, end, letter) in enumerate(run):
            if letter is None:  # a masked item — its letter is in `marked_at`
                marked_letter, paren_only = marked_at[start]
                if len(run) >= 2 or (lone_marked and not paren_only):
                    out.add(marked_letter)
                continue
            # A BARE member keeps `_bare_list_members`'s two rules verbatim: it is a
            # member only inside a run of two or more, and `a`/`i` additionally need
            # separator-side evidence (they are English words).
            if len(run) < 2:
                continue
            if letter.lower() in _PROSELIKE_LETTERS and not _proselike_has_evidence(
                masked, run, pos, start, end
            ):
                continue
            out.add(letter.lower())
    return out


def _proselike_has_evidence(
    masked: str,
    run: list[tuple[int, int, str | None]],
    pos: int,
    start: int,
    end: int,
) -> bool:
    """Is this bare `a`/`i` — an English word — EVIDENCE of list membership here?

    ⛔ BEING IN A RUN IS NOT ENOUGH FOR `a` AND `i`. Every other bare letter of the
    page's alphabet is unambiguous, but `a` (article) and `i` (pronoun) are ordinary
    English, so a run POSITION tells us nothing on its own — measured, #11902's review:
    with `(i)` removed from the complement list,
    `Only **(a)**, **(f)**, **(h)**, and I, **(j)** still read as written.` read CLEAN,
    because `I` sat in an INTERIOR run position and the ending test was applied only to
    the run's last item. The same held for `, and a, **(j)**`.

    ⛔ WHICH SIDE IS EVIDENCE DEPENDS ON THE POSITION, and conflating them re-opens the
    hole (measured by the verification gate at this head): testing `punctuation on the
    left` FIRST returned True for a run TAIL whose left neighbour is a comma, so
    `Only a, f, h, i foo bar.` and `Only a, f, h, i, and then some.` supplied a phantom
    `i` — the left side of a tail is the list it just came from, never evidence about
    what follows it:

    1. **HEAD** (`pos == 0`) — a run's FIRST item. There is no item before it, so there is
       no separator-side evidence to consult and the code reads it; in the documented bare
       spelling the first member sits exactly here (`Only a, f, …`). ⛔ THIS IS A CHOICE,
       NOT A PROOF — nothing to a head's left is examined at all, so prose can supply an
       `a`/`i` here (see the FIFTH residual). It is also NOT true that the span before a head is
       "prose ending in a word": a head can sit at the start of the string, after `(`, or
       after a comma (`then x, I, j …`), and none of that is consulted.
    2. **TAIL** (`pos == len(run) - 1`) — the RIGHT side decides, via `_MEMBER_END_RE`:
       it is prose once the list is over.
    3. **INTERIOR** — both sides are separators, and the LEFT one must be HOMOGENEOUS:
       exactly one punctuation mark (`,` `;` `&`) or exactly one bare connector (`and`,
       `or`, `also`, `plus`, `as well as`, `followed by`, `namely`). Both are documented
       list separators, so both are evidence (`a, f, h, i, j` and `a and f and h and i and j`
       must each read every member — refusing the connector form reded a CORRECT page,
       measured by #11902's review round 3). A MIXTURE is refused, and that is the
       DECLARED OVER-REFUSAL: `a, f, h, and i, j` loses its `i`. It is deliberate — see
       the residual below — because the mixture's bytes are the prose bytes.

    ⚠️ RESIDUALS, stated rather than implied — FIVE OVER-READINGS (prose read as a member)
    and ONE AMBIGUOUS OVER-REFUSAL (a member not read). ⛔ THE REASON NONE IS CLOSABLE IS THE
    BYTE-IDENTITY, and nothing wider than that: each is byte-identical to the other side of the
    same ambiguity, so closing one means choosing the other side's cost. For the first
    over-reading that cost is a broken pin (its scope fix is refused for exactly that reason);
    for the predicate-tailed one it is a WIDER predicate list, i.e. accepting the over-block
    (stated in that bullet) — not a pin. Do not compress the two into one blanket reason:
    * OVER-READING — a SECOND RUN LATER IN THE SAME SENTENCE: membership is sentence-global,
      so a two-item letter run in prose contributes its members. Measured: a complement list
      that OMITS `i`, closed with `; see h, i.`, reads CLEAN (the run's tail `i` sees a full
      stop). Scoping the arm to the first run closes it and is REFUSED, because
      `test_a_letter_added_to_either_tally_list_is_compared_in_every_spelling` and
      `test_a_code_or_italic_letter_is_read_like_every_other_markup` both pin a letter added
      in a later clause (`… **(l)** are **DRIFTED**, and a, f too.`) as compared — the same
      mechanism, the opposite verdict. Pinned from the reading side by
      `test_a_second_run_in_the_same_sentence_supplies_members_too`, so a change that scopes
      the arm must move that pin and this list together.
    * OVER-READING — INTERIOR adjacency: `…, a, …` / `…, I, …` inside a punctuation-joined
      run, `… and a and …` inside a connector-joined one. A bare list item and an English
      word beside a homogeneous separator are the same bytes.
    * OVER-READING — TAIL preceded by a connector, followed by an accepted ending:
      `, and a.`, `, and I — the rest …`, `, and I: the rest …`. ⛔ THIS IS THE
      OXFORD-COMMA SPELLING: `a, f, h, and i.` must read `i`, so refusing the
      connector-preceded tail would be the false-accusation class #12028 rejects.
    * OVER-READING — TAIL followed by one of the arm's own predicates can still be a verb
      phrase (`, and a read of §4 shows …`); closing it needs a WIDER predicate list, the
      same false-accusation trade.
    * OVER-READING — HEAD: prose `I`/`a` at a run's head is read (`I, j still read as
      written.`) — a run begins at its first item, and nothing to its left can distinguish
      the two.
    * OVER-REFUSAL — a MIXED left separator on an INTERIOR `a`/`i` (`a, f, h, and i, j`) drops
      that member. Only `a`/`i` are subject to the rule, because `_proselike_has_evidence` is
      consulted only for `_PROSELIKE_LETTERS`: measured, `a, f, h, and c, j` KEEPS its `c`. Deliberate: those bytes are prose's bytes (`, and I,` closed a stale
      complement list — measured), and a false PASS outranks the friction of an over-block
      (AGENTS.md A/B). Pinned by
      `test_a_mixed_separator_interior_join_is_the_declared_over_refusal`, so a change that
      flips the trade must update this list rather than drift from it.
    Every over-reading also needs the phantom to be exactly a row the stale list OMITS.
    """
    if pos == 0:  # a run's FIRST item — no separator-side evidence exists, so it is read
        return True
    if pos == len(run) - 1:  # the list's TAIL — what follows it is the evidence
        return bool(_MEMBER_END_RE.match(masked[end:]))
    # INTERIOR: the left separator must be homogeneous — punctuation alone or a bare
    # connector, never a mixture (`, and I,` is prose).
    left_sep = masked[run[pos - 1][1] : start]
    return bool(
        _PUNCTUATION_SEP_RE.fullmatch(left_sep) or _CONNECTOR_SEP_RE.fullmatch(left_sep)
    )


def _named_letters(sentence: str) -> set[str]:
    """The letters a sentence names, in any accepted spelling.

    ⛔ TWO DIFFERENT RULES, and the caller must know which: a MARKED letter is read
    UNCONDITIONALLY (the markup IS the claim — `row (c) still read as written` yields `c`,
    which is not a list member at all), while a BARE one is read only as a member of a list
    RUN. So this is "the letters the sentence names", NOT "the letters it lists" — candidacy
    is the wider reading, membership the narrower one.
    """
    out: set[str] = set()

    def _take(match: re.Match[str]) -> str:
        out.update(g.lower() for g in match.groups() if g)
        return _MEMBER_MASK

    masked = _NAMED_LETTER_RE.sub(_take, sentence)
    out |= _bare_list_members(masked)
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
    total_word = _count_word(total)
    expected = f"{_count_word(len(non_still))} of the {total_word} rows no longer read as written"
    # Split into sentences: the non-STILL-TRUE letters live in the "no longer read
    # as written" sentence, its complement in the "still read as written" one.
    # Partitioning on the phrase instead does not work — "Only **(a)** … **(j)** "
    # sits BEFORE the words it introduces, so the head swallowed the complement and
    # the arm named all twelve (measured).
    # ⛔ SPLIT ON A QUOTED FULL STOP *LAST*. A sentence that only QUOTES the phrase is
    # already exempt (the `quoted` strips below), but the split ran FIRST, so a full stop
    # INSIDE the quotation cut the sentence there and left the left-hand fragment with no
    # closing quote — `quoted` could not see it, and the fragment became a second
    # assertion: measured, `They wrote "seven of the twelve rows no longer read as written.
    # It is wrong." verbatim.` red both the figure arm and the letter arm. Masking the
    # periods INSIDE quoted spans before splitting keeps every letter (a quoted LETTER
    # list is still a list) and every quote (so the phrase strips still work), and the
    # sentence boundaries the arms split on are the ones outside quotations.
    sentences = re.split(
        r"(?<=\.)\s+", quoted.sub(lambda m: m.group(0).replace(".", "\u2024"), paragraph)
    )
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
        for s in sentences
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
    #: One letter. ⛔ MARKUP AROUND THE PARENTHESES DOES NOT DECIDE LIST MEMBERSHIP, and
    #: that is why there is no separate `**(a)**` branch: `PAREN` already matches the
    #: `(a)` inside it, so within a RUN a bolded letter and a paren one are the same
    #: item, and a `**` cannot change which ITEMS a list has. ⛔ BUT IT *DOES* DECIDE
    #: CANDIDACY, and saying otherwise would be false: `letter_sentence`'s standalone
    #: `MARKED` branch admits `**(c)**` and `` `c` `` while `PAREN` is a LIST ITEM only,
    #: so for a ONE-member sentence `**` is exactly the difference between a red and a
    #: green — measured, `Note **(c)** still read as written.` appended to §3's takeaway
    #: paragraph is a candidate ("carries 2 sentences") while `Note (c) still read as
    #: written.` is not (CLEAN). For a TWO-or-more list the extractor disjunct makes it
    #: neutral. What MARKED adds is the PAREN-LESS spellings the page and this repo also
    #: use: code `` `c` `` and italic `*c*`. ⛔ A SINGLE bare `(c)` is still not a
    #: candidate — `row (c) still read as written` is ordinary prose, and making it one
    #: red a legitimate sentence (found by review, measured) — so PAREN is a LIST ITEM
    #: only, never a standalone match.
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
    #: candidate when EITHER `letter_sentence` (the four-form gate below) matches — which
    #: admits a SINGLE marked letter: measured, `Note `c` still read as written.` is a
    #: candidate on one letter — OR the extractor finds TWO OR MORE named letters, in any
    #: spelling and any join: `Note **(c)** is fine and **(d)** is fine, so both still read
    #: as written.` qualifies with no comma and no connector between them. So candidacy is
    #: DELIBERATELY WIDER than "a list of two or more members", and the disjunct is what
    #: makes it wide: one letter is not a list, but a MARKED one is a candidate, in either
    #: case (`letter_sentence` is compiled IGNORECASE, like the extractor).
    def _is_letter_list(sentence: str) -> bool:
        return bool(letter_sentence.search(sentence)) or len(_named_letters(sentence)) >= 2

    #: ⛔ IGNORECASE, BECAUSE THE EXTRACTOR IS. `MARKED`/`PAREN`/`BARE` are written
    #: lowercase `[a-l]`, while `_NAMED_LETTER_RE` and `_RUN_ITEM_RE` compile with
    #: `re.IGNORECASE` — so without this flag a single UPPERCASE marked letter was read by
    #: the extractor and still was NOT a candidate (the extractor disjunct needs two), and a
    #: second tally spelled `Note **C** still read as written.` was silently ignored while
    #: the page documents uppercase as a read spelling (measured: it returned 0 problems,
    #: the lowercase form returned the second-tally problem). One flag closes it because
    #: both sides then read the same alphabet.
    letter_sentence = re.compile(
        MARKED
        + r"|" + ITEM + r"(?:\s*,\s*" + ITEM + r")+"  # a list: b, c, d
        + r"|" + ITEM + r"\s+and\s+" + ITEM,  # a two-item list: b and c
        re.IGNORECASE,
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
    # ⛔ THE PHRASE DECIDES THE GROUP, NOT THE WORD `no longer` (#11902 review round 8).
    # The filter used to drop any still-sentence CONTAINING `no longer`, which made the
    # negation governor below unreachable for that word: `… still read as written, while the
    # rest no longer do.` was not a candidate at all, so the natural spelling of a STALE
    # second sentence rode out of the "exactly one" rule (measured: a compliant decoy plus
    # that stale sentence read CLEAN), and the CORRECT complement spelled the same way red
    # with "STILL-TRUE list names (none)". Only the non-still PHRASE belongs to the other
    # arm, so only it is excluded here.
    still_sentences = [
        s for s in sentences
        if "still read as written" in quoted.sub(" ", s)
        and "no longer read as written" not in quoted.sub(" ", s)
        and _is_letter_list(s)
    ]
    if len(still_sentences) > 1:
        problems.append(
            f"§3 carries {len(still_sentences)} sentences saying 'still read as "
            "written' — the guard cannot tell which one a lane quotes, so a second "
            "one can assert a letter the real sentence does not; keep exactly one"
        )
    still_sentence = still_sentences[0] if still_sentences else ""
    # ⛔ ONE PARAGRAPH IS NOT ONE SECTION (#11902 review, `ab6ce5d6ab` P2 — open through
    # rounds 7, 8 and this one). Every arm above takes its subject from ONE PARAGRAPH, so a
    # second tally one blank line away was CLEAN while the same-paragraph form reds:
    # measured, appending a `\n\n`-separated
    # `Only **(a)** and **(f)** still read as written.` after the takeaway paragraph left
    # `--check` at exit 0. §3's PROSE carries exactly one sentence naming either phrase (the
    # takeaway paragraph states both halves); the table rows are excluded because they carry
    # verdicts, not the tally. Anything past that is an assertion the arms never see. A
    # paragraph that only QUOTES either phrase stays exempt — `quoted` runs first.
    section_prose = " ".join(
        line
        for line in _region(
            text, "## 3. The corrections log", "## 4. The artefact index"
        ).splitlines()
        if not line.lstrip().startswith("|")
    )
    section_prose = re.sub(r"\s+", " ", section_prose)
    # ⛔ REMOVE THE PARAGRAPH BY CONTENT, NOT BY COMPARING SENTENCE-CHUNKS (#11902 review,
    # round 9). The first shape compared each section chunk against the paragraph's chunks
    # by string equality, and the two streams are chunked at DIFFERENT offsets: the
    # paragraph subject starts at the MARKER TEXT (inside `**The one thing to take
    # away:**`), while the section stream inherits whatever precedes the paragraph. Any
    # terminator other than `.` + space — a trailing `:`, `;`, `—`, `?`, `…`, or no
    # preceding prose at all — joined the paragraph's first sentence to the PREVIOUS text,
    # so the page's own single tally chunked differently and was reported as a stray.
    # Measured on a correct page whose intro paragraph ends `… since overtaken:` — rc 1,
    # "carries 1 tally sentence(s) OUTSIDE the takeaway paragraph". Deleting the
    # paragraph's own text removes the offset dependence entirely: what remains is by
    # construction everything that is NOT the subject.
    remainder = section_prose.replace(re.sub(r"\s+", " ", paragraph), " ")
    # ⛔ MASK QUOTED FULL STOPS BEFORE THE SPLIT, exactly as the paragraph path does
    # (#11902 review, round 10). Splitting `remainder` raw cut a QUOTED sentence at its
    # own full stop, so the fragment asserting nothing was reported as a second tally:
    # measured, inserting `They wrote "seven of the twelve rows no longer read as
    # written. It is wrong." verbatim.` after the takeaway paragraph reds rc 1 — the
    # false-accusation class this arm's own comment says is exempt, on a REQUIRED job.
    stray_tallies = [
        s
        for s in re.split(
            r"(?<=\.)\s+",
            quoted.sub(lambda m: m.group(0).replace(".", "\u2024"), remainder),
        )
        if "still read as written" in quoted.sub(" ", s)
        or "no longer read as written" in quoted.sub(" ", s)
    ]
    if stray_tallies:
        problems.append(
            f"§3 prose carries {len(stray_tallies)} tally sentence(s) OUTSIDE the "
            "takeaway paragraph — the guard reads ONE paragraph, so a second tally a "
            "blank line away can supply or contradict the list unwatched; keep exactly "
            "one"
        )
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
        for label in _letters_in_span(non_still_sentence[previous : token.start()]):
            before[label] = token.group(1)
        previous = token.start()
        stop = tokens[pos + 1].start() if pos + 1 < len(tokens) else len(non_still_sentence)
        for label in _letters_in_span(non_still_sentence[token.end() : stop]):
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
    # ⛔ THE MENTION SCAN READS EVERY SPELLING THE EXTRACTOR DOES. This was
    # `\*\*\(([a-z])\)\*\*` — `**(x)**` ONLY — so a contradicting repeat written
    # `(d)`, `` `d` `` or `*d*` was invisible and the page read CLEAN: measured at this
    # head on a `(d)` SUPERSEDED-BY-FIX row, `Note (d) is **ADOPTED**.` returned `[]`
    # while the `**(d)**` spelling red. §8 blesses all four spellings, so `**` must not
    # be what decides whether a contradiction is seen. See `_LETTER_ALT_MARKED`.
    VERDICT_TOKEN = re.compile(r"\*\*([A-Z][A-Z-]+)\*\*")
    # The mention scan covers the WHOLE paragraph, not only the sentence that carries
    # the letter list: a contradicting repeat in the closing sentence or in the
    # parenthetical one is still part of the "one thing to take away" a reader quotes,
    # and reading only the letter-list sentence dropped it (measured: `… are
    # **DRIFTED**. Note **(d)** is **ADOPTED**.` returned `[]`). Only labels the table
    # calls non-STILL-TRUE can be misattributed, so the complement sentence
    # contributes no claims and needs no special case.
    #: `(letter, start, end)` — one group per alternative, so the letter is flattened
    #: here rather than read as `group(1)`, which was the bold-only assumption.
    letters = [
        (group.lower(), match.start(), match.end())
        for match in _NAMED_LETTER_RE.finditer(paragraph)
        for group in match.groups()
        if group
    ]
    tokens = [t for t in VERDICT_TOKEN.finditer(paragraph) if t.group(1) in VERDICTS]
    mentions: dict[str, set[str]] = {}
    for token in tokens:
        before = [lt for lt in letters if lt[2] <= token.start()]
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
                paragraph[: nearest[1]],
            )
            if not dated and COPULA_OR_ADVERB.fullmatch(
                paragraph[nearest[2] : token.start()]
            ):
                mentions.setdefault(nearest[0], set()).add(token.group(1))
        after = [lt for lt in letters if lt[1] >= token.end()]
        if after:
            nearest = after[0]
            if COLON_ONLY.fullmatch(paragraph[token.end() : nearest[1]]):
                mentions.setdefault(nearest[0], set()).add(token.group(1))
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
    # ⛔ THE SENTENCE IS SPLIT ON ITS OWN PHRASE, NOT PEELED FROM ITS END (#11902 review
    # round 8 — three measured fail-opens, one measured false accusation, all one cause).
    # The page's documented shape is `<still list> still read as written [— <governed list>
    # <negation>]`, so the PHRASE is the boundary between the two groups and the position
    # is all that is needed. The code used to peel trailing list items off the clause
    # instead, and every rule it encoded was narrower than English:
    #
    #   `… — **(a)** and **(f)** (both re-measured) do not.`   peel stopped at the adverb
    #                                                          -> the governed group was
    #                                                             never validated  (CLEAN)
    #   `… — **(b)** & **(k)** & **(l)** do not.`               `&`/`or`/`also`/`plus` are
    #                                                        documented separators but not
    #                                                        peel separators -> the correct
    #                                                        complement reded as still-true
    #
    # Both directions are the same defect: membership was decided by characters ADJACENT to
    # the end of the string rather than by the run the page wrote. Anchoring on the phrase
    # makes the separator set irrelevant — a run is a run.
    phrase = re.search(r"still read as written", still_sentence, re.IGNORECASE)
    still_clause = still_sentence[: phrase.start()] if phrase else still_sentence
    # Everything after the phrase is the page's SECOND group — its complement — and it is
    # only a governed group when a negation governs it.
    after_phrase = still_sentence[phrase.end() :] if phrase else ""
    negation = re.search(r"\b(?:do|does|did) not\b|\bno longer\b", after_phrase)
    # ⛔ THE PHRASE, NOT THE WORD `no longer`, DECIDES WHICH GROUP A SENTENCE IS — see the
    # candidacy filter in `check_verdict_tally`'s still-sentence list. Reading the governed
    # group out of `still_clause` (the pre-phrase half) is what makes the split sound.
    # The still-list is the RUN the page wrote, read by LIST membership — never every marked
    # letter in the clause. A marked letter in prose (`(see correction **(i)**)`, `(see `i`)`)
    # is a CROSS-REFERENCE, and reading it unconditionally let a stale list that omits `i`
    # read CLEAN while a correct page that says `unlike **(b)**` reded (both measured, round
    # 8) — the same fail-open and the same false accusation in two spellings.
    named_still = _listed_letters(still_clause)
    if named_still != set(still_true):
        problems.append(
            f"§3 tally: the paragraph's STILL-TRUE list names "
            f"{sorted(named_still) or '(none)'}, but the table's STILL-TRUE rows are "
            f"{still_true}"
        )
    # ⛔ STRIPPING IS NOT VALIDATING, AND IT WAS NOT (#11902 review round 7, P1). The
    # governed group is the COMPLEMENT of the still-list, so every letter it names must be a
    # row the table does NOT call STILL-TRUE — a check with no new ambiguity, since the
    # table is right there. Discarding it instead let the sentence contradict both the table
    # and ITSELF at rc 0 (measured before this block existed):
    #
    #   `… **(a)**, **(f)**, **(h)**, **(i)** and **(j)** still read as written — **(a)** and
    #   **(f)** do not.`                                -> CLEAN
    #   `… **(a)**… **(j)** still read as written — **(a)**, **(f)**, **(h)**, **(i)** and
    #   **(j)** do not.`                                -> CLEAN (contradicts itself, twice)
    #
    # ⚠️ A LONE MARKED ITEM IS READ HERE ONLY WHEN THE SPAN *IS* THAT ITEM. In the governed
    # half a marked letter is also how this page cross-references a row, so reading a lone
    # marked item inside a span that carries other words would accuse a CORRECT page of
    # naming a still-true row as overtaken (the mirror of the fail-open, measured round 8).
    # A multi-item run is a list claim wherever it sits and is always read.
    governed_letters: set[str] = set()
    if negation:
        governed_span = after_phrase[: negation.start()]
        sole_item = bool(_SOLE_ITEM_RE.fullmatch(governed_span.strip()))
        governed_letters = _listed_letters(governed_span, lone_marked=sole_item)
    # ⛔ AND THE GROUP IS OFTEN ITS OWN SENTENCE (#11902 review, round 10 P2). The page's
    # ellipsis closes the complement AT the phrase and spells the governed group separately —
    # `… still read as written. — **(i)** and **(j)** do not.`, or wedged between the
    # non-still tally and the still one — and such a fragment carries NEITHER tally phrase,
    # so no phrase-driven arm above sees it: measured, it read CLEAN in BOTH positions while
    # the identical text inside the sentence reds. So every sentence in the paragraph is
    # asked one question: is everything before its negation verb NOTHING BUT letter items
    # and separators? If yes it is the governed group and is validated; anything carrying a
    # real word (`Rows (i) and (j) do not apply here.`) is prose and is never accused.
    # ⛔ AND THE NEGATION MUST GOVERN *READING* (#11902 review, round 12). Matching any
    # negation verb read a TRUE sentence as its own contradiction: measured,
    # `… — **(a)**, **(f)**, **(h)**, **(i)** and **(j)** do not need re-reading.` RED with
    # "names ['a','f','h','i','j'] as NOT still reading as written" — the page saying exactly
    # what its table says. So the text AFTER the verb must be the ellipsis (`do not.`), or a
    # form of `read` — `do not read`, `no longer read as written`, `read as written any more` —
    # or `do` again (`no longer do.`). ⚠️ NOTHING ELSE IS READ, AND THAT IS A STATED RESIDUAL,
    # not an oversight: `no longer hold.` and `do not overlap the fix.` name rows too, and so
    # does `do not, unlike the rest.` — but `did not move.`, `did not, however, move.` and
    # `do not, in fact, need re-reading.` are TRUE sentences of the IDENTICAL shape (and the
    # punctuation does not separate them: an independent continuation made the verb requirement
    # moot and re-red the very sentence #11902's round 12 removed). A miss on this page is
    # visible on the page it reds nowhere; a false accusation reds a REQUIRED job. So the verb
    # span after the negation must be nothing but a form of `read` / `do` and punctuation.
    # ⛔ AND `Only` IS THIS PAGE'S OWN LIST LABEL (`Only **(a)** … still read as written`), so
    # it may open the governed span: without that, the natural counterpart spelling
    # `Only **(i)** and **(j)** do not.` still read CLEAN (measured).
    for candidate in sentences:
        verb = re.search(r"\b(?:do|does|did) not\b|\bno longer\b", candidate)
        if not verb or candidate is still_sentence:
            continue
        tail = candidate[verb.end() :].strip()
        if not re.fullmatch(
            r"(?:(?:do|read|reads)(?:\s+as\s+written)?"
            r"(?:\s+any\s+(?:more|longer))?)?[\W_]*",
            tail,
            re.IGNORECASE,
        ):
            continue
        head = re.sub(
            r"^[\W_]*(?:only|just)\b", "", candidate[: verb.start()], flags=re.IGNORECASE
        )
        if not head.strip():
            continue
        residue = re.sub(_LETTER_ITEM_ANY, " ", head, flags=re.IGNORECASE)
        residue = re.sub(
            r"\b(?:and|or|also|plus|as\s+well\s+as|followed\s+by|namely)\b"
            r"|[^\w\s]",
            " ",
            residue,
            flags=re.IGNORECASE,
        )
        if not residue.split():
            # ⛔ A FRAGMENT'S LONE MARKED ITEM IS THE SPAN, exactly as in the governed-span arm
            # above (#11902 review, round 15 P3): with `lone_marked=False` a one-member marked
            # fragment (`… — **(i)** do not.`) contributed NO letters and read CLEAN, while the
            # identical claim inside the sentence reds two lines up. The residue test has
            # already established that the head is nothing but letter items and separators, so
            # every marked item here IS a governed member, a lone one included.
            governed_letters |= _listed_letters(
                head, lone_marked=bool(_SOLE_ITEM_RE.fullmatch(head.strip()))
            )
    wrong_governed = sorted(governed_letters & set(still_true))
    if wrong_governed:
        problems.append(
            f"§3 tally: the negated complement clause names {wrong_governed} as NOT still "
            f"reading as written, but the table calls those row(s) STILL-TRUE "
            f"({still_true}) — the sentence contradicts the cells beside it"
        )

    expected_overtaken = (
        f"{_count_word(len(overtaken)).capitalize()} of the "
        f"{_count_word(len(non_still))} were"
    )
    # The same rule as the count above, for the same measured reason: the figure is
    # compared against the ONE sentence that states it, never against the paragraph.
    # ⛔ THE SAME SENTENCES TOO (#12029): this arm re-split the RAW `paragraph`, so the
    # quote mask that ended the figure arm's false accusation was live in only one of two
    # structural twins. Measured, appending `They wrote "four of the seven were overtaken.
    # It is wrong." verbatim.` red this arm with "2 sentence(s) state a 'N of the M were'
    # overtaken figure" while the no-full-stop decoy stayed CLEAN — and §3 of the page
    # asserts "a quoted full stop does not end a sentence", which the arm contradicted.
    overtaken_sentences = [
        s
        for s in sentences
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


def _is_file(path: Path) -> bool:
    """`path.is_file()`, treating an unresolvable path as "not a file".

    ⛔ `Path.is_file()` is NOT total. `pathlib` swallows only
    `(ENOENT, ENOTDIR, EBADF, ELOOP)`, so `ENAMETOOLONG` and `EACCES` are re-raised —
    and a citation path is page-controlled, so a path longer than `PATH_MAX` raised
    `OSError` out of `check_document()` and the CLI exited 1 (measured, review round 5).
    The two neighbouring arms already handled this (`target.resolve()` catches
    `(OSError, RuntimeError)`, `_line_count` catches `OSError`); these two existence
    checks were the outliers. A path the filesystem refuses is a path the tree does not
    contain, which is what the caller reports.
    """
    try:
        return path.is_file()
    except OSError:
        return False


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
        start_raw, end_raw = match.group("start"), match.group("end")
        if not _convertible(start_raw) or (end_raw and not _convertible(end_raw)):
            # Refuse BEFORE converting (see `MAX_CONVERTIBLE_DIGITS`): `int()` on either
            # end of the span raised out of the guard, and the CLI exited 1 — "a named
            # claim is false" — over a line number that simply cannot be resolved.
            longest = max(len(start_raw), len(end_raw or ""))
            # `raw` is the WHOLE token, so it carries the 4,301-digit run — truncate it,
            # or the bounded-message guarantee this refuses for is broken by the message.
            shown = raw if len(raw) <= 80 else f"{raw[:60]}…({len(raw)} chars)"
            problems.append(
                f"citation {shown}: line {start_raw[:12]}…"
                + (f"-{end_raw[:12]}…" if end_raw else "")
                + f" ({longest} digits) is out of range — a line number that long cannot "
                "resolve, so the citation names nothing that can be opened"
            )
            continue
        start, end = int(start_raw), end_raw
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
            if not _is_file(target):
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
        if not _is_file(target):
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
    # The same `UnicodeDecodeError`-is-not-`OSError` arm as `read_doc` (#11902 review
    # P1, measured: an undecodable `docs/00_index.md` exited **1** with a traceback).
    except (OSError, UnicodeDecodeError) as exc:
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
    row_refs: dict[str, list[str]] = {}
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
        label, raw_count, which = match.group("label"), match.group("count"), match.group("which")
        if not _convertible(raw_count):
            # Refuse BEFORE converting: `int()` here raised out of the guard and the CLI
            # exited 1, which reads as "a named claim is false" over a page that may state
            # nothing (measured). A population this large cannot be reconciled, so it is
            # reported as drift and treated as zero — which also reds the sum arm.
            problems.append(
                f"§6 row {label!r}: its count cell names a {len(raw_count)}-digit number "
                "— no indexed population is that large, and a count too long to convert "
                "is a substituted claim rather than a reconciled one"
            )
            raw_count = "0"
        count = int(raw_count)
        if label in found:
            problems.append(f"§6: state row {label!r} appears twice — ambiguates the arithmetic")
            continue
        found[label] = count
        refs = re.findall(r"`#\d+`", which)
        listed = len(refs)
        # ⛔ NORMALISE every ref to its ISSUE NUMBER, and let the census below judge the
        # four rows as ONE population (#11902 review rounds 1 and 2). Two separate
        # fail-opens lived here, and each was measured on the shipped document:
        #
        #   1. counting OCCURRENCES let a substitution stay green — swapping `#2725` for
        #      a second `#2714` inside one row kept `count=32 listed=32` over 31 distinct
        #      issues;
        #   2. judging each row IN ISOLATION and comparing raw STRINGS let the same
        #      substitution move one level up, and let a re-spelling slip through: moving
        #      `#2787` out of `CLOSED other` while duplicating a `#2714` already listed in
        #      `CLOSED COMPLETED` reconciles EVERY row, and writing `#02714` beside
        #      `#2714` is a different string — both left `check_document()` at `[]` over a
        #      census the pinned population could no longer account for.
        #
        # This is the shape `MEMORY.md`'s `[pin-defensive-claim]` names: pin the list by
        # SHAPE, not by size, because a size or equality pin is replaced by substitution.
        row_refs[label] = [ref.strip("`#").lstrip("0") or "0" for ref in refs]
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

    # ⛔ MEMBERSHIP, not just size and uniqueness (#11902 review round 3). Pinning the SET'S
    # SIZE and demanding uniqueness still admits a substitution: `#2726` replaced by `#9999`
    # — a number the ledger never indexed — leaves every row reconciling, the sum at 42, the
    # census at 42 distinct and no duplicate anywhere, so `--check` exited 0 while an indexed
    # issue had left the population (measured). The count arm this replaces could not see it,
    # which is the whole point of `MEMORY.md`'s `[pin-defensive-claim]` rule: pin the list by
    # SHAPE, because a size or equality pin is replaced by substitution.
    #
    # Refs are compared as NORMALISED DIGIT STRINGS, never through `int()`: `int()` raises
    # `ValueError` on a 4,301-digit ref (Python's conversion limit), which escaped `main()`'s
    # `except Unverifiable` as a traceback with exit **1** — the "drift" code — over a page
    # that states no falsified claim. That is the same class as the round-1
    # `UnicodeDecodeError` hole, so nothing here may convert untrusted text.
    seen: dict[str, list[str]] = {}
    for label, nums in row_refs.items():
        for number in nums:
            seen.setdefault(number, []).append(label)
    duplicated = {number: labels for number, labels in seen.items() if len(labels) > 1}

    def _ref(number: str) -> str:
        """Render a ref for a message, truncated so a hostile input cannot flood the log.

        A ref is `\\d+` by construction, so a 4,301-digit one is reachable and must be
        SAFE to print as well as safe to parse (that is why nothing here uses `int()`).
        """
        return f"#{number}" if len(number) <= 12 else f"#{number[:12]}…({len(number)} digits)"

    if duplicated:
        named = ", ".join(
            f"{_ref(number)} ({' and '.join(sorted(set(labels)))})"
            for number, labels in sorted(duplicated.items(), key=lambda kv: (len(kv[0]), kv[0]))
        )
        problems.append(
            f"§6: {named} named more than once across the state rows — a duplicated ref "
            "stands in for a dropped issue while each row's count still reconciles, so "
            "the census can lose an issue in silence"
        )
    pinned_text = {str(number) for number in PINNED_INDEXED}
    missing_from_census = sorted(pinned_text - set(seen), key=lambda s: (len(s), s))
    unexpected_in_census = sorted(set(seen) - pinned_text, key=lambda s: (len(s), s))
    if missing_from_census:
        problems.append(
            f"§6 census: {', '.join(_ref(n) for n in missing_from_census)} left the "
            "counted population — an indexed issue is no longer named by any state row, "
            "while every row's own count still reconciles with its own list"
        )
    if unexpected_in_census:
        problems.append(
            f"§6 census: {', '.join(_ref(n) for n in unexpected_in_census)} is counted "
            "but is not in the pinned indexed population — a number the ledger never "
            "indexed is standing in for one it dropped"
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


def _rewrite_first_correction_cell(text: str, cell: int, replacement: str) -> str:
    """Put `replacement` in ONE prose cell of the first §3 row, keeping the row's SHAPE.

    EVERY content-cell arm below comes through here, so the region lookup, the escaped-pipe
    skip rule and the shape-preserving join cannot disagree between them. The shape is
    deliberately preserved (`"|".join(cells)` round-trips the row exactly), because a
    reshaped row would be caught by the row-shape arm instead — and an arm that reds a
    NEIGHBOUR's assert rides a red it did not earn, which is how a rule with no killing
    mutant reads as coverage (`MEMORY.md`'s `[pin-defensive-claim]`).
    """
    lines = text.splitlines(keepends=True)
    for i, line in enumerate(lines):
        row = line.rstrip("\n")
        if not CORRECTION_ROW_RE.match(row) or "\\|" in row:
            continue
        cells = _split_row_cells(row)
        if len(cells) > cell:
            cells[cell] = replacement
            lines[i] = "|".join(cells) + "\n"
            return "".join(lines)
        return text
    return text  # pragma: no cover - the §3 population pin refuses an empty region


def _blank_cell_of_first_correction_row(text: str, cell: int) -> str:
    """Blank ONE prose cell of the first §3 row, keeping the row's SHAPE.

    Used by the two #11902-review arms that close the claim/correction floor, and by
    `test_a_blank_prose_cell_is_rejected`. A thin wrapper over
    `_rewrite_first_correction_cell` so every content-cell arm shares ONE traversal.
    """
    return _rewrite_first_correction_cell(text, cell, " ")


def _first_correction_cell(text: str, cell: int) -> str:
    """Read ONE prose cell of the first §3 row — the input side of the copy arms."""
    for line in text.splitlines():
        row = line.rstrip("\n")
        if not CORRECTION_ROW_RE.match(row) or "\\|" in row:
            continue
        cells = _split_row_cells(row)
        return cells[cell] if len(cells) > cell else ""
    return ""  # pragma: no cover - the §3 population pin refuses an empty region


def _content_cell_replacement(cell: int, replacement: str):
    """A mutation arm that puts `replacement` in ONE prose cell of the first §3 row."""
    def mutate(text: str) -> str:
        return _rewrite_first_correction_cell(text, cell, replacement)

    return mutate


#: `&nbsp;` — a placeholder that RENDERS blank. It cleared the whitespace floor and then
#: the word reader until entities were decoded (measured at `c5990249c0`).
_claim_is_an_html_entity = _content_cell_replacement(2, " &nbsp; ")

#: `<br>` — a TAG that renders blank while tokenising to letters: the same class one
#: spelling out from the entity. `<!-- x -->` and `<span>&nbsp;</span>` are the sibling
#: spellings `test_a_blank_rendering_cell_is_rejected` pins.
_correction_is_markup_only = _content_cell_replacement(3, " <br> ")

#: `<!-- x -->` — a COMMENT, which renders as nothing and tokenises to a letter.
_correction_is_a_comment_only = _content_cell_replacement(3, " <!-- x --> ")

#: `[](https://x)` — a markdown link with an EMPTY LABEL: it renders as nothing and its URL
#: still tokenises — one dialect out from the tag family (measured at `c5990249c0`).
_correction_is_an_empty_label_link = _content_cell_replacement(3, " [](https://x) ")

#: `n/a` — an emptied cell backfilled with a placeholder, which is what a lane reaches for
#: when the guard reds a blank one. `/` separates in the word class, so this tokenises to
#: `['n', 'a']`; the joined-form test is what catches it.
_correction_is_a_placeholder = _content_cell_replacement(3, " — n/a ")

#: `TBC` — a placeholder whose twin `tba` WAS in the denylist while `tbc` was not, so a
#: `TBC` correction cell left `--check` at rc 0; `tbc` is listed now, so this arm pins the
#: LIST as it stands rather than the unlisted class (the class is §8's DECLARED limit, and
#: `test_an_unlisted_placeholder_is_the_declared_limit` pins the acceptance it implies).
_correction_is_a_twinned_placeholder = _content_cell_replacement(3, " TBC ")

#: `<SCRIPT>lorem ipsum</SCRIPT>` — a raw-text element's body, in a mixed CASE. Two separate rules
#: are needed and until the tenth review of this change neither had an arm: the RAW-TEXT switch
#: itself (a body a browser never renders, so its letters are not content) and its CASE-INSENSITIVE
#: matching (`casefold()` on the open tag and `re.IGNORECASE` on the close, because `<SCRIPT>` is
#: the same element to a browser). Deleting either left the arm set green while a §3 correction
#: cell holding this `<SCRIPT>` body passed as content.
_correction_is_a_raw_text_body = _content_cell_replacement(3, " <SCRIPT>lorem ipsum</SCRIPT> ")

#: ` x nil ` — MULTI-WORD placeholders. Every single-token placeholder above is caught by the
#: JOINED form (`''.join(words) in PLACEHOLDER_WORDS`), so with only those the `all(...)`
#: half of the rule could be deleted and every arm still passed (measured by the review of
#: this change: a joined-only build left `--self-test` at PASS). This arm is the one that
#: separates the two halves.
_correction_is_only_placeholders = _content_cell_replacement(3, " x nil ")


def _correction_copies_claim(text: str) -> str:
    """`**False.** The props are durable` — the correction is the claim, restated."""
    claim = _first_correction_cell(text, 2)
    return _rewrite_first_correction_cell(text, 3, f" {claim.strip()} ")


def _correction_copies_claim_shouted(text: str) -> str:
    """The same copy with the CASE changed: an arm that does not case-fold passes it."""
    claim = _first_correction_cell(text, 2)
    return _rewrite_first_correction_cell(text, 3, f" {claim.strip().upper()} ")


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
    (
        # #11902 review P1: §6 counted OCCURRENCES in each row's own list, so a
        # SUBSTITUTION stayed green — swapping one issue for a duplicate of another
        # keeps the total, and a census silently lost an issue while "the row's count
        # reconciles" still held (measured: `#2725` -> a second `#2714` read
        # `count=32 listed=32` over 31 distinct issues, `check_state_arithmetic` `[]`).
        # This arm fails if the count goes back to occurrences instead of distinct refs.
        "a §6 state row naming the same issue twice",
        lambda t: t.replace("incl. `#2714`, `#2725`", "incl. `#2714`, `#2714`", 1),
        "more than once",
    ),
    (
        # #11902 review round 2: the round-1 fix judged each state row IN ISOLATION, so the
        # same substitution moved one level up — dropping `#2787` from `CLOSED other` while
        # duplicating a `#2714` already listed in `CLOSED COMPLETED` reconciles EVERY row
        # (4/4, 32/32, 4/4, 2/2) and lost an issue from the census in silence (measured:
        # `check_document()` `[]`). The four rows are one population; this arm fails if the
        # check goes back to per-row only.
        "a §6 issue re-listed inside ANOTHER state row",
        lambda t: t.replace(
            "`#1509` (`NOT_PLANNED`) · `#2787` (`NOT_PLANNED`)",
            "`#1509` (`NOT_PLANNED`) · `#2714` (`NOT_PLANNED`)",
            1,
        ),
        "more than once",
    ),
    (
        # The same hole, spelled differently: `#2714` and `#02714` are distinct STRINGS, so a
        # duplicate test on the raw match saw nothing while the census was one issue short
        # (measured: `check_document()` `[]`). Refs are compared as ISSUE NUMBERS now.
        "a §6 issue re-spelled with a leading zero",
        lambda t: t.replace("incl. `#2714`, `#2725`", "incl. `#2714`, `#02714`", 1),
        "more than once",
    ),
    (
        # #11902 review round 3: pinning the census by SIZE and UNIQUENESS still admitted a
        # substitution — `#2726` -> `#9999` kept every row reconciling, the sum at 42, the
        # census at 42 distinct and NO duplicate, so `--check` exited 0 while an indexed issue
        # had left the population (measured). The population is pinned by IDENTITY now, so a
        # number the ledger never carried cannot stand in for one it dropped.
        "a §6 issue replaced by a number the index never carried",
        lambda t: t.replace("`#2726`", "`#9999`", 1),
        "never indexed",
    ),
    (
        # #11902 review P2: the §3 floor held only the EVIDENCE cell, so a row could be
        # reduced to `| **e** |  |  | **ADOPTED** | <citation> |` — a verdict with neither
        # the claim that was retracted nor the correction that replaced it — and the page
        # stayed CLEAN (measured). One arm per prose column, so each rule has its own
        # killing mutant rather than riding its neighbour's red.
        "a §3 row whose claim cell is blank",
        lambda t: _blank_cell_of_first_correction_row(t, 2),
        "names no retracted claim",
    ),
    (
        "a §3 row whose correction cell is blank",
        lambda t: _blank_cell_of_first_correction_row(t, 3),
        "states no correction",
    ),
    # #11902 review, second pass: the floor above refuses a cell that TESTS empty; a cell
    # that RENDERS empty still passed. One arm per family, each the spelling a real author
    # writes, so each rule has its own killing mutant rather than riding a neighbour's red.
    (
        "a §3 claim cell carrying only an HTML entity",
        _claim_is_an_html_entity,
        "carries nothing a reader can read",
    ),
    (
        "a §3 correction cell carrying only markup",
        _correction_is_markup_only,
        "carries nothing a reader can read",
    ),
    (
        "a §3 correction cell carrying only a comment",
        _correction_is_a_comment_only,
        "carries nothing a reader can read",
    ),
    (
        "a §3 correction cell carrying an empty-label link",
        _correction_is_an_empty_label_link,
        "carries nothing a reader can read",
    ),
    (
        "a §3 correction cell carrying a placeholder",
        _correction_is_a_placeholder,
        "is a placeholder",
    ),
    (
        "a §3 correction cell carrying a placeholder whose twin was listed",
        _correction_is_a_twinned_placeholder,
        "is a placeholder",
    ),
    (
        "a §3 correction cell carrying only multi-word placeholders",
        _correction_is_only_placeholders,
        "is a placeholder",
    ),
    (
        "a §3 correction holding only a raw-text element's body",
        _correction_is_a_raw_text_body,
        "carries nothing a reader can read",
    ),
    (
        "a §3 correction that copies its claim",
        _correction_copies_claim,
        "is a COPY of its claim",
    ),
    (
        "a §3 correction that copies its claim in another case",
        _correction_copies_claim_shouted,
        "is a COPY of its claim",
    ),
    # The OTHER two columns of the same row read the RAW cell until the review of this
    # change measured it: `<!-- **STILL-TRUE** -->` in the verdict cell and
    # `<!-- `tortoise/sdk.py:17109` -->` in the evidence cell each left `check_document()`
    # at `[]`, so a row could lose its verdict AND its openable citation while §1 and §8
    # promised both. One arm each, so neither reader can be reverted by itself.
    (
        "a §3 verdict cell commented out",
        _content_cell_replacement(4, " <!-- **STILL-TRUE** --> "),
        "no bolded verdict",
    ),
    (
        # The TALLY reader is the third `_rendered_cell_text` site, and the two arms above pin
        # the other two: a verdict hidden BEHIND a rendered one makes the tally disagree with the
        # paragraph, and a reader that took the RAW token would see the hidden `STILL-TRUE`
        # first and agree (measured by the third review of this change: reverting this reader
        # left `--self-test` at PASS).
        "a §3 verdict hidden behind a commented-out one",
        _content_cell_replacement(4, " <!-- **STILL-TRUE** --> **DRIFTED** "),
        "assigns a different verdict than the row carries",
    ),
    (
        "a §3 evidence cell commented out",
        _content_cell_replacement(5, " <!-- `tortoise/sdk.py:17109` --> "),
        "names no `path:line`, issue, command or §N",
    ),
    # #11902 review round 8: the group-orientation arms had no killing mutant, so
    # `--self-test` would have stayed green over the exact fail-opens that review found.
    # One mutant per direction, each the shape a real author writes.
    (
        "a governed complement naming a STILL-TRUE row, behind an adverb",
        lambda t: t.replace(
            "still read as written.",
            "still read as written — **(a)** and **(f)** certainly do not.",
            1,
        ),
        "negated complement",
    ),
    (
        "a marked cross-reference certifying a missing complement member",
        lambda t: t.replace(
            "**(h)**, **(i)** and **(j)** still read as written.",
            "**(h)** and **(j)** still read as written (see correction **(i)**).",
            1,
        ),
        "STILL-TRUE list names",
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
