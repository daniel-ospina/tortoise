#!/usr/bin/env python3
"""closes_landing — a `Closes #N` on a PR whose base is NOT the default branch is
INERT: it closes nothing and lands nothing, while every surface says it did
(#10597).

Why this exists
---------------
GitHub interprets a closing keyword (`Closes`/`Fixes`/`Resolves #N`) **only when
the pull request targets the repository's default branch**
(https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue).
When the base is another lane's branch — a stacked PR, which is the *correct* way
to keep a reviewable, non-overlapping diff — the keyword is ignored. Two things
then go wrong at once, and neither is visible from outside:

1. the issue is never closed. It keeps a `Closes` in its timeline and stays
   `open`, so the next reader sees it as unstarted work;
2. the work is not on the default branch. It lives on the sibling branch and
   only lands when *that* branch's own PR merges — and that PR may carry no
   closing keyword at all.

Measured on 2026-11-02 (#10597, re-measured at `origin/main`
`63981d1529e1c2dbea0db3ffe22f675bfd9e32d7`): `PR #10568` (base `act-10465`,
body `Closes #10541`) merged at 14:07:51Z, `merge_commit_sha`
`ebea1fe84f193a78009d8faa6bf4bead345499db` — which is **not** an ancestor of
main — while `#10541` is still `open` with `closed_at: null`. `act-10465` is the
head of `PR #10519`, whose base IS `main` and whose body has **no** `Closes
#10541`. So `#10541`'s work rides `#10519` to main and the issue stays open
anyway: two lanes of work, and the issue looks unstarted.

What this tool answers
----------------------
The question no existing surface asked: **for every closing keyword in this PR's
body, which PR will actually carry it to the default branch, and does THAT PR
carry the same keyword?**

* base is the default branch            -> `lands`     (GitHub fires on merge)
* base is a sibling branch, and the PR that terminates the stack carries the
  same closing ref                      -> `lands`     (it rides in and closes)
* base is a sibling branch, and the terminating PR does NOT carry it
                                        -> `inert`     (nothing will close it)
* the base branch has no PR at all      -> `unresolved` (cannot be measured)

`unresolved` is deliberately NOT `lands` and NOT `inert`: a base branch with no
PR is a stack whose carrier has not been opened yet, and calling that a pass is
how the silent case was manufactured in the first place.

The check walks the WHOLE stack (`a <- b <- c`), not one hop: the keyword only
fires on the PR whose base is the default branch, so a two-hop chain with the
keyword on the middle PR is just as inert as a one-hop one.

Scope — what this is NOT
------------------------
It is a **warning**, not a gate (#10597: "Not a new gate"). Stacking is
legitimate and blocking a legitimately-stacked PR would be worse than the defect;
so the caller decides what to do with exit 3. `--annotate` emits plain GitHub
Actions `::warning` lines for a warn-only workflow step, and the JSON output is
for anything that wants to reason instead of read prose.

It never writes: no comments, no labels, no closures. Reading is the whole
surface.

Why a regex, when this repo already ruled to prefer the source
--------------------------------------------------------------
`tools/collision_preflight.py` records this repo's ruling on exactly this
grammar (#3504): **"prefer the source over the regex"** — it reads GitHub's own
`closingIssuesReferences` rather than re-deriving which issues a PR closes.
This tool deliberately does NOT, and the reason is the question it asks. That
field is GitHub's answer to *"what WILL this PR close"*, and for the shape this
tool exists for it is **empty**: `#10568` (base `act-10465`, body `Closes
#10541`) reports `closingIssuesReferences: []`, because GitHub has already
decided the keyword will not fire. What is needed here is the opposite query —
*what does this PR CLAIM to close* — and no API surface answers it, so the
grammar is parsed. That is a second copy of a grammar GitHub owns, which is
exactly the drift #3504 predicts, and it drifted: review finding P1 was a fence
shape (a fence opened on a list-marker line) this copy read as prose, which
manufactured a `lands`. The mitigation is that this copy is checked against a
SOURCE too — a real CommonMark renderer (`markdown_it`, a test dependency),
shape by shape, in `test_the_parser_agrees_with_a_real_commonmark_renderer` — so
a divergence between what this tool calls code and what a renderer calls code
reds a test rather than a reader. That test is the mitigation, and it is the only
thing that is: an earlier version of this paragraph claimed the cross-check while
no test in the file imported the renderer at all, which is the re-staleable
"checked by hand" claim this repo keeps paying for. It imports the renderer at
MODULE scope (`tests/test_closes_landing.py`), never through
`pytest.importorskip`, so a lost install reds the leg instead of leaving the
oracle `skipped` and the leg green — a mitigation that can silently stop running
is the same re-staleable claim one layer down. Do not "simplify" it back to
`closingIssuesReferences`: for this question the source is empty precisely when it
matters.

Second question, same grammar: MERGED-BUT-OPEN (#10698)
------------------------------------------------------
Everything above asks *"will this keyword fire?"*. `--merged-but-open` asks the
NEXT question, about the merge that already happened: **"is the issue this merge
belongs to actually closed — and if not, what does the evidence support?"**

It exists because `lands` is a claim about the KEYWORD, not about the issue. A
merged-on-main `Closes #N` reports `lands` whether or not `#N` closed, and a
`Refs #N` body produces **no finding at all** — so the drift this mode measures
is invisible to every arm above. Measured on this repository at #10698
(2026-11-03, the default 48 h window): of 43 open P0/P1 issues, **0** appear in
any merged-on-default PR's `closingIssuesReferences`, **31** are referenced by a
merged PR and carry no closure record (18 by a timeline mention, 12 by a
`Refs`-only body, 1 by a `Closes` stranded on a non-default base), and **12** are
referenced by nothing at all. A `Refs` closes nothing
(https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/linking-a-pull-request-to-an-issue
lists exactly `close(s|d) fix(es|ed) resolve(s|d)`), so the issue stays open while
the work sits on `main`.

The three verdicts, and what each one is allowed to rest on:

* `done`     — a merged-on-DEFAULT-branch PR lists the issue in its
                `closingIssuesReferences`. That is GitHub's OWN closure record:
                the keyword fired. It is the only positive evidence this mode
                accepts, because a keyword that merged into the default branch
                *closes the issue* — so an issue that is still open beside such
                a PR is a deliberate reopen or a repo with automatic closing
                turned off, never something this tool may read as "done" on its
                own say-so. (In the measured 48 h window the `done` population
                is 0 — but that is the WINDOW, not the rule: over all merged
                history back to 2026-10-12 exactly 3 of the 43 carry a closure
                record — `#4282 <- PR #8174`, `#9548 <- PR #9585`,
                `#10000 <- PR #10011` — and a human reopened ALL THREE. That is
                why `done` also requires a timeline with no reopen — see
                `Timeline`.)
* `partial`  — no closure record, but a merged-on-default PR references the
                issue: a `Closes` that did not fire, a `Refs`-only body, or a
                bare mention in the timeline. The work may be on `main`; nothing
                demonstrates it is finished.
* `not-done` — no merged-on-default PR references the issue by any route,
                INCLUDING its timeline. This is a measured statement, so it is
                emitted only after the timeline was read.

Checkboxes are an ANNOTATION, never a gate. Measured over that same population:
37 of 43 issue bodies carry no task list at all, and none of the 6 that do has
every box checked — so gating on them would pass the majority vacuously and
block the largest P0/P1s permanently. `acceptance=k/n` is printed and nothing
depends on it.

The line formats are the fleet-readable contract:

    MERGED-BUT-OPEN(<merge-sha> <pr#> -> #<issue> verdict=<v> reason=<r> acceptance=<k/n|none>)
    NO-MERGE-REF(#<issue> verdict=<v> reason=<r> acceptance=<k/n|none>)

`NO-MERGE-REF` carries placeholders because TWO verdicts legitimately print
there: `verdict=not-done reason=no-merge-ref-in-window` on a measured absence,
and `verdict=partial reason=unmeasured-timeline` under `--no-timeline` (where
neither a reopen nor an absence can be ruled out). What defines the kind is the
ABSENCE of a merge sha, not the verdict.

TWO kinds, deliberately: a `not-done` issue has no merge, so printing a `<sha>
<pr#>` for it would assert a merge that never happened. A `grep MERGED-BUT-OPEN`
therefore collects only lines backed by a real merge sha.

Like everything above, this mode **never writes** — no comments, no labels, no
closures. `done` is the PRECONDITION for a human (or a lane) closing an issue,
never the authorisation to do it.

Usage
-----
    uv run python tools/closes_landing.py --pr 10568                # one PR
    uv run python tools/closes_landing.py --sweep                   # every open PR on a non-default base
    uv run python tools/closes_landing.py --pr 10568 --annotate     # ::warning:: lines
    uv run python tools/closes_landing.py --merged-but-open         # merge -> issue state
    uv run python tools/closes_landing.py --merged-but-open \
        --label P0-blocker --label P1-severe --since-hours 48       # the P0/P1 drift
    uv run python tools/closes_landing.py --body-file body.md --base act-10465 \
        --carrier-body-file carrier.md          # offline: no network at all

Exit codes: 0 = nothing inert · 3 = at least one inert OR unresolved closing
ref · 2 = the question could not be measured at all (gh/API failed).
`--merged-but-open` keeps the same shape: 0 = every issue has a closure record ·
3 = at least one `partial` or `not-done` · 2 = UNMEASURABLE.
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter BEFORE the imports below — a module-level
# 3.12-only construct would fail first, with an error that blames the import
# rather than the interpreter (the D9 shape).
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/closes_landing.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/closes_landing.py`"
    )

import argparse
import json
import re
import subprocess
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path

# ── core: pure, network-free, and the part the tests drive ────────────────────

# GitHub's closing keywords, in the spelling that actually fires:
#   close/closes/closed · fix/fixes/fixed (and the common "fixe"? no) ·
#   resolve/resolves/resolved
# The optional `:`, one space, and the optional `owner/repo` prefix are GitHub's
# own accepted forms.
#
# ⚠️ TWO separator spellings, because the two parses ask DIFFERENT questions
# (#10634 review P3):
#
# * LENIENT (`\s*`) backs the FINDING arms, where a missed claim is a closure
#   that silently does not fire — the defect this tool exists to catch — so a
#   keyword one paragraph away from its reference is still worth reporting.
# * STRICT (`[ \t]*`) backs every PASS (`lands`). `\s` matches a BLANK LINE, so
#   `"Closes\n\n#100"` used to be a claim there and the `lands` arm passed on a
#   keyword whose reference is a SEPARATE PARAGRAPH — a shape no GitHub doc
#   shows and this tool cannot demonstrate fires. Same rule as the indented
#   case (cycle 5): a pass may not rest on a shape the tool cannot show GitHub
#   acts on. A blank-line-separated claim is still REPORTED (lenient arm), it
#   just cannot buy a pass, so nothing real is dropped by the narrowing. ONE
#   line break is allowed — that is not a paragraph break, and only the BLANK
#   line is the undemonstrable shape (`\n?`, never `\n\n`).
_CLOSING_SEPARATORS = {
    "lenient": r"\s*:?\s*",
    "strict": r"[ \t]*:?[ \t]*\n?[ \t]*",
}


def _closing_re(sep: str) -> re.Pattern[str]:
    """The keyword pattern with ONE separator spelling (`lenient`/`strict`)."""
    return re.compile(
        r"(?i)\b(?P<keyword>close[sd]?|fix(?:es|ed)?|resolve[sd]?)"
        + sep
        + r"(?:(?P<slug>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+))?"
        r"#(?P<number>\d+)\b"
    )


_CLOSING_RE = _closing_re(_CLOSING_SEPARATORS["lenient"])
_CLOSING_RE_ONELINE = _closing_re(_CLOSING_SEPARATORS["strict"])

#: A line that STARTS a new block, tested on the line's text AFTER its blockquote
#: prefix. A span is inline content and cannot cross a block boundary, and most
#: blocks end the paragraph WITHOUT a blank line — so `_paragraph_end` needs this
#: AS WELL AS its own blank-line test (`if not line.strip()`), which is inline
#: there because it is already positioned at a line start and needs no pattern.
#: ⚠️ There is deliberately no `_BLANK_LINE_RE` constant beside this one: one
#: existed and was READ BY NOTHING while two comments said it bound the strict
#: scan, so a maintainer could "restore" it into `_paragraph_end` and reinstate
#: the blank-line-only bound that let an unmatched backtick pair with the OPENER
#: of a real span in a later block and expose the keyword that span held — a
#: false `lands`, the fail-open direction this module forbids (#10634 review P2).
#: Deleting it is the fix for that claim (#10634 review P3); splicing it back in
#: is not, because its pattern is anchored on a LEADING newline and would match
#: nothing at a line start.
_BLOCK_START_RE = re.compile(
    r"(?: {0,3}(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)"          # a list item marker
    r"| {0,3}#{1,6}(?:[ \t]|$)"                             # an ATX heading
    r"| {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$"  # thematic break
    r"| {0,3}<)"                                            # an HTML block
)


def _paragraph_end(text: str, start: int) -> int:
    """Where the PARAGRAPH containing `start` ends, per CommonMark's block rules.

    This is the STRICT arm's bound for a span's closing run, and it must be the
    renderer's own: a code span is INLINE content, so it cannot cross a block
    boundary — and a blockquote line, a list item, an ATX heading, a thematic
    break or an HTML block all end the paragraph WITHOUT a blank line. Bounding
    the search at the next blank line alone therefore let an unmatched backtick
    pair with the OPENER of a genuine span in a later block, blank the wrong
    region, and expose the keyword that span held:

        Use `foo in the old version
        > note
        `Closes #100`

    renders `<p>Use `foo in the old version</p><blockquote><p>note
    <code>Closes #100</code></p></blockquote>`, i.e. CODE — and the carrier arm
    read it as carrying the keyword: `lands`, exit 0, no annotation, on the arm
    the whole tool exists to protect (#10634 review P2, fail-open).

    Stopping EARLY can only cost a pass that was never demonstrable (a warning),
    never a false closure, so the boundary tests err toward stopping: the
    container is compared by DEPTH (never by prefix string) and any line that
    could start a block ends the span.

    ⚠️ "Err toward stopping" has a HARD floor, and it is §5.1's LAZY
    CONTINUATION: a paragraph inside a block quote continues on the next line
    whether or not that line carries a `>` marker, so a line at a LOWER depth
    than the opener is still the same paragraph — the renderer keeps opener and
    closer in ONE paragraph and puts the keyword in `<code>`:

        > The old body said `Closes
        #100` and nothing fired.

    stops nothing. Requiring an equal depth there ended the span early, left the
    backticks unmatched, and the keyword survived as "prose" in BOTH arms —
    `lands`, exit 0, no annotation, on the arm the whole tool exists to protect
    (#10634 review P1, fail-open). Only a DEPTH INCREASE or a new block ends the
    paragraph, and the direction is the safe one: reading a line as a lazy
    continuation can only blank MORE, i.e. cost a pass.
    """
    line_start = text.rfind("\n", 0, start) + 1
    first_end = text.find("\n", start)
    if first_end < 0:
        return len(text)
    # The opener's own container: a span MAY cross lines inside ONE block (a
    # continuation of the same quote or list item, or a LAZY continuation line
    # carrying no marker at all), and must not cross into another —
    # `> a `Closes\n> #100` b` is one span, `a `x\n> `Closes #100`` is not.
    depth = _quote_depth(_split_prefix(text[line_start:first_end])[0])
    pos = first_end + 1
    while pos < len(text):
        end = text.find("\n", pos)
        if end < 0:
            end = len(text)
        line = text[pos:end]
        if not line.strip():
            return pos                      # a blank line ends the paragraph
        prefix, rest = _split_prefix(line)
        if _quote_depth(prefix) > depth:
            return pos                      # a DEEPER container: a new block
        if _BLOCK_START_RE.match(rest):
            return pos                      # a new block type
        pos = end + 1
    return len(text)


def _span_limit(text: str, start: int, strict: bool) -> int:
    """Where the search for a code span's CLOSING run stops.

    §6.1: a code span is INLINE, and "line endings are converted to spaces" —
    so a span whose opener and closer sit on two lines of the SAME paragraph is
    ONE span, and the renderer agrees:

        "Example: `Closes\n#10541` is the quoted form."
        -> <p>Example: <code>Closes #10541</code> is the quoted form.</p>

    A span cannot cross a BLANK line (§6.1's content stays inside one paragraph)
    or any other BLOCK boundary (§6.1: a span is inline content), so the strict
    scan stops at `_paragraph_end` — the renderer's own boundary. Bounding it at
    the next blank line alone paired a stray run with the opener of a real span
    in a later block and exposed the keyword that span held (#10634 review P2).
    The two callers want OPPOSITE things from an unsure case, and this is the one
    place the module's asymmetry has to be spelled out in code:

    * `strict` is the PASS arms (the carrier body, and the subject's own `lands`
      arm). Under-stripping there is a `lands` on a shape GitHub ignores — the
      one outcome this module forbids — so it searches to the end of the
      PARAGRAPH: an over-strip only costs a pass that was never demonstrable
      (`unresolved`, a warning), never a false closure.
    * lenient is the FINDING arms. Blanking between two stray runs that a
      renderer keeps literal (an indented code line pairing with a tick in a
      later paragraph, `    a `b\nprose `c Closes #100`) would LOSE a real
      closure — the harmful direction for a warning — so it refuses to pair
      across a line break and over-reports the multi-line span instead.
      ⚠️ Refusing to pair is not the same as ignoring the renderer: a run left
      literal by the line bound is still closed by the next run of its length in
      the paragraph (`_strip_inline_code`'s `pending` set), so the under-report
      that rule used to cost — `` a` `` / `` `Closes #100` `` is prose to the
      renderer and was blanked away here (#10634 review P3) — is gone rather
      than recorded. That set can only blank LESS, so it cannot cost a claim.
    """
    if strict:
        return _paragraph_end(text, start)
    line_end = text.find("\n", start)
    return len(text) if line_end < 0 else line_end


def _strip_inline_code(text: str, *, strict: bool = False) -> str:
    """Blank every INLINE code span, whatever the delimiter's run length.

    CommonMark opens a code span with a run of N backticks and closes it with a
    run of exactly N, so a two-backtick delimited keyword is code exactly as a
    one-backtick delimited keyword is. A single-backtick pattern left the wider
    delimiters intact, so a QUOTED `Closes #N` written between two backticks on
    each side counted as a claim — and on the CARRIER side that buys a `lands`
    (#10634 review P3-2).

    An unmatched run is literal text (CommonMark, not a span). How far the closer
    search may travel is `_span_limit`'s question, and it differs per arm: a
    span that crosses a SOFT LINE BREAK (one paragraph, §6.1) was left unstripped
    by a per-line bound, so `` `Closes\n#10541` `` bought a `lands` in both pass
    arms (#10634 review P2, fail-open).

    ⚠️ The LENIENT arm searches only to the end of the line, which means a run it
    leaves literal here is not the last word: the renderer pairs it with the next
    run of the SAME length anywhere in the paragraph, and that run may be the
    opener of a pair on the very next line. `` a` `` / `` `Closes #100` `` is
    therefore PROSE to the renderer while a per-line scan blanks the keyword and
    reports nothing — a lost claim, the harmful direction for the FINDING arms
    (#10634 review P3). `pending` closes it the honest way: a length left literal
    in this paragraph is remembered, and the next run of that length CLOSES it
    instead of opening a span. It can only ever blank LESS, so it cannot cost a
    claim — the one direction this arm must not move.
    """
    out: list[str] = []
    #: Run lengths left LITERAL earlier in this paragraph (lenient arm only):
    #: the next run of that length closes one, exactly as the renderer pairs
    #: them. Cleared at a paragraph break, because spans cannot cross one.
    pending: set[int] = set()
    i, n = 0, len(text)
    while i < n:
        if text[i] == "\n":
            nxt = text.find("\n", i + 1)
            if not text[i + 1:(n if nxt < 0 else nxt)].strip():
                pending.clear()          # a blank line ends the paragraph
            out.append("\n")
            i += 1
            continue
        if text[i] != "`":
            out.append(text[i])
            i += 1
            continue
        opener = i
        while i < n and text[i] == "`":
            i += 1
        run = i - opener
        if _escaped(text, opener):
            # ⛔ A BACKSLASH-ESCAPED BACKTICK IS NOT A DELIMITER. GFM renders
            # ``\`Closes #100\``` as PROSE carrying a live `Closes #100` — the
            # backslashes escape the backticks, so there is no code span at all.
            # Blanking it anyway reported nothing and exited 0 over a `Closes`
            # the renderer honours, which on a non-default base is exactly the
            # missed-warning this module exists to prevent (#10841 review, P2).
            # It is not a run either, so it neither opens a span nor closes a
            # `pending` one: emitted verbatim, and the scan continues past it.
            out.append(text[opener:i])
            continue
        if not strict and run in pending:
            # This run CLOSES a literal one earlier in the paragraph, so it is
            # not an opener and nothing on this line is inside a span opened
            # here — blanking the rest of the line is what lost the claim.
            pending.discard(run)
            out.append(text[opener:i])
            continue
        limit = _span_limit(text, i, strict)
        closer = -1
        j = i
        while j < limit:
            if text[j] != "`":
                j += 1
                continue
            k = j
            while k < limit and text[k] == "`":
                k += 1
            if k - j == run:
                closer = j
                break
            j = k
        if closer < 0:
            out.append(text[opener:i])  # unmatched: a literal run, not a span
            if not strict:
                pending.add(run)
            continue
        # Replace the span with spaces, keeping NEWLINES so the line structure
        # the strict parse reasons about is untouched.
        out.append("".join(
            ch if ch == "\n" else " "
            for ch in text[opener:closer + run]
        ))
        i = closer + run
    return "".join(out)


def _escaped(text: str, pos: int) -> bool:
    """Is ``text[pos]`` backslash-ESCAPED — an ODD number of backslashes before it?

    Odd, not simply "there is a backslash": CommonMark's escape is ONE backslash,
    so a doubled backslash escapes the BACKSLASH and the character after it is a
    real delimiter, while a single one escapes the delimiter itself. Counting
    parity separates the two; a bare ``text[pos - 1] == "\\"`` test reads the
    first case right and the second backwards.

    ⚠️ It is applied to an OPENER only, and the asymmetry is the renderer's, not
    an oversight: the inline scan consumes ``\\`` + ``x`` as one literal
    character, so an escaped backtick never reaches the code-span rule — while a
    span's CLOSER is found by matching backtick strings in the raw remainder, a
    search that knows nothing about escapes. Measured against markdown-it:
    ``\\`Closes #100\\``` renders as prose, `` `Closes #100\\` `` renders as
    ``<code>Closes #100\\</code>``. Guarding the closer too would leave a span the
    renderer honours unblanked — a false warning, which is the direction this
    arm must not move (it would also disagree with
    `test_the_parser_agrees_with_a_real_commonmark_renderer`).
    """
    backslashes = 0
    k = pos - 1
    while k >= 0 and text[k] == "\\":
        backslashes += 1
        k -= 1
    return backslashes % 2 == 1


MAX_STACK_DEPTH = 20


@dataclass(frozen=True)
class PullRequest:
    """The four fields the question needs. Anything else is not read."""

    number: int
    base: str
    body: str = ""
    head: str = ""
    state: str = "OPEN"
    merged_at: str | None = None

    @property
    def merged(self) -> bool:
        return bool(self.merged_at) or self.state.upper() == "MERGED"

    @property
    def closed_unmerged(self) -> bool:
        """Closed WITHOUT ever merging. Such a PR can never carry anything, so
        it must not be confused with an open one — see `_pick_carrier`."""
        return self.state.upper() == "CLOSED" and not self.merged


@dataclass(frozen=True)
class Finding:
    issue: int
    pr: int
    base: str
    verdict: str  # lands | inert | unresolved
    carrier: int | None
    detail: str

    def as_dict(self) -> dict:
        return asdict(self)

    def annotation(self) -> str:
        """A GitHub Actions `::warning` line. Newlines are percent-escaped
        because an annotation is ONE line — a raw newline truncates it."""
        msg = self.detail.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        # The TITLE states the actual verdict: `unresolved` is not `inert` (it is
        # "could not be measured"), and the title is the only text a reader sees
        # before opening the log.
        return (f"::warning title={self.verdict.capitalize()} Closes "
                f"#{self.issue}::{msg}")


#: A blockquote marker may be preceded by AT MOST THREE columns (§5.1), and four
#: columns make an INDENTED CODE BLOCK — so `    > Closes #N` is not a quote at
#: all, it is `<pre><code>&gt; Closes #N</code></pre>` and the keyword never
#: fires. This was `\s*`, i.e. unbounded, so the prefix ate the four columns,
#: `rest_indent` came out 0, the strict arm could not fire, and the line was
#: emitted as PROSE — `lands`, exit 0, no annotation, on exactly the shape the
#: module forbids a pass on (#10634 review P2, fail-open). The bound is what
#: leaves those four columns in `rest`, where the strict arm measures them.
_CONTAINER_PREFIX_RE = re.compile(r"^(?: {0,3}>[ \t]?)*")


def _quote_depth(prefix: str) -> int:
    """How many blockquote markers (`>`) a CONTAINER PREFIX carries."""
    return prefix.count(">")


def _split_prefix(line: str) -> tuple[str, str]:
    """(blockquote prefix, the rest of the line).

    The prefix consumes only markers a CommonMark renderer would OPEN — up to
    three columns of allowance each — so a line too deeply indented to be a quote
    keeps its indentation in `rest` instead of having it silently absorbed.
    """
    m = _CONTAINER_PREFIX_RE.match(line)
    return m.group(0), line[m.end():]


_MAX_FENCE_INDENT = 3  # a 4+ column ``` is an INDENTED CODE BLOCK, not a fence

# A list item's content column, so a fence opened inside one can END when the
# item ends. Without it, `- item\n  ```\nCloses #N\n` blanked the column-0 prose
# and the tool reported nothing at all (review c10-3).
_LIST_MARKER_RE = re.compile(r"^(?P<indent>[ \t]*)(?:[-*+]|\d{1,9}[.)])(?P<gap>[ \t]+)")


def _fence_open(rest: str, content_col: int = 0) -> tuple[str, int] | None:
    """(character, run length) if `rest` opens a fence — else None.

    CommonMark, and each clause below is a shape a review cycle found:

    * an opener indented more than 3 columns past the CONTAINING BLOCK's content
      column (`content_col`) is not a fence at all — §4.5: "A fenced code block
      begins with a code fence, preceded by up to three spaces of indentation",
      and inside a list item that budget starts at the item's content column, so
      a four-space-from-column-0 run IS a fence there. Measuring from column 0
      refused a real fence and blanked the block's content as prose — a `lands`
      on a quoted keyword (#10634 review P1). A tab is 4 columns, hence
      `expandtabs`;
    * a backtick opener whose info string CONTAINS a backtick is not a fence.
    """
    body = rest.expandtabs(4)
    if len(body) - len(body.lstrip(" ")) - content_col > _MAX_FENCE_INDENT:
        return None
    s = body.lstrip(" ")
    m = re.match(r"(`{3,}|~{3,})", s)
    if not m:
        return None
    ch, n = m.group(1)[0], len(m.group(1))
    if ch == "`" and "`" in s[n:]:
        return None
    return (ch, n)


def _is_closer(rest: str, opener: tuple[str, int]) -> bool:
    """A closer carries the same character, a run at least as long, and NOTHING
    but whitespace after it (an "info string" on a closing fence is content).

    `rest` is the line from the CONTAINING BLOCK's content column — the caller
    slices there and enforces §4.5's "up to three spaces of indentation" itself:
    "The closing code fence may be preceded by up to three spaces of indentation,
    and may be followed only by spaces or tabs … their indentation need not match
    that of the opening fence". The allowance belongs to the CONTENT COLUMN, NEVER
    to the opening run's own indent: a top-level fence opened at two columns does
    not license a four-space closer (the renderer keeps both lines inside
    `<pre><code>`), while a fence inside a list item whose content column is 2
    does. Measuring from the run's indent accepted a closer the renderer calls
    CONTENT, which left the block open exactly one line too few — a `lands` on a
    quoted keyword.
    """
    ch, n = opener
    m = re.match(r"(`{3,}|~{3,})\s*$", rest.expandtabs(4))
    return bool(m) and m.group(1)[0] == ch and len(m.group(1)) >= n


@dataclass(frozen=True)
class _Fence:
    """An OPEN fenced block: the things its container needs to end it.

    * `prefix` — the blockquote prefix it opened under (the quote ending ends it);
    * `close_base` — the CONTAINING BLOCK's content column in `rest` coordinates
      (after the blockquote prefix). A dedent is a line whose residual indent
      falls below it, and a closing fence is measured from it (§4.5 allows a
      closer up to three spaces past the containing block's content column, and
      its indent need not match the opener's). It is the ITEM's content column
      when one is open — never the fence run's own indent: §4.5 ends the block at
      "the end of the containing block", and an item's content line may sit BELOW
      the opener's column and still be inside the block;
    * `in_list` — whether a list item was open, so a dedent can end the block.

    There is deliberately no raw-coordinate item column: two review rounds
    carried one alongside this field and ended up comparing a `rest` indent with
    a raw column (and then the reverse), each time buying a false `lands`.
    Everything a dedent or a closer is measured against is therefore ONE of the
    fields above, in the space that field already holds — `close_base` against a
    `residual` indent (the item's column past the prefix), `item_col_raw` against
    a RAW `indent` (an item that sits outside the quote). There is deliberately
    no DERIVED third number either: the only derived column this class ever
    carried (`len(prefix) + close_base`, the raw column of the container's
    content) existed solely for a subtraction that charged the marker's
    allowance twice, and it was deleted with that subtraction (#10841 review,
    P2). A derived column kept beside the two real ones is how the next reader
    re-derives it and re-introduces the double count.

    `item_col_raw` is the enclosing item's content column in RAW coordinates,
    and is set ONLY when that item sits OUTSIDE the fence's blockquote (its
    content column is at or before the quote's markers). CommonMark matches the
    OUTER container first, so such an item ends on a RAW dedent — consuming a
    `>` marker before measuring it read a column-0 `>` as the container's own,
    missed the dedent, and kept the fence open over top-level prose (#10634
    review). A marker-line fence has its item INSIDE the quote, so it leaves
    this 0 and is measured after the prefix via `close_base`.

    `item_depth` is the depth the enclosing ITEM was opened at, and it is what
    selects WHICH of the two numbers above applies — not whether `item_col_raw`
    happens to be non-zero. An item at the fence's own depth is measured in `rest`
    space; a shallower one in raw space. Choosing by "is the field set" read a
    quoted item's `rest` column against a raw indent, which kept the block open
    over a dedent (a false `lands`) once quoted markers started being tracked at
    all (#10634 review P1).
    """

    opener: tuple[str, int]
    prefix: str
    close_base: int
    in_list: bool
    item_col_raw: int = 0
    item_depth: int = 0


def _fence_line_column(line: str, depth: int) -> tuple[int, int, int]:
    """(markers consumed, indent after them, RAW column of the content).

    A fenced block takes everything up to its closer LITERALLY, so a `>` inside
    it is text, not a marker — the only markers that say anything about the
    container are the ones the block itself lives under. Consuming at most
    `depth` of them is what makes `> - ```\n>   Closes #N` (a marker-line fence
    INSIDE a quote: the `>` is the quote's, and the item sees the two spaces
    after it) and `- ```\n   > Closes #N` (a depth-0 item fence: no marker is
    consumed, so the line is literal content indented 3 columns into the item)
    BOTH come out right. Two things differ and both are returned:

    * the INDENT is measured after the consumed markers, because that is the
      only space an enclosing list item's content column is expressed in —
      spaces BEFORE a `>` are the marker's own allowance (up to 3) and belong to
      the quote, not to the item, which is why `   > Closes #N` inside `> - ``` `
      ends the item (the renderer makes it a paragraph of the quote) while
      `>   Closes #N` stays inside it;
    * the RAW COLUMN is where content starts, because §4.5 measures a closing
      fence's "up to three spaces" from the containing block's content column in
      raw columns.

    Reading the prefix with `_split_prefix` — which consumes every `>` it finds
    — got one family of shapes wrong in each coordinate it was compared in, and
    each way of getting it wrong is a false `lands` on a quoted keyword (#10634
    review).
    """
    i = 0
    consumed = 0
    while consumed < depth:
        j = i
        while j < len(line) and line[j] in " \t":
            j += 1
        if j < len(line) and line[j] == ">":
            j += 1
            if j < len(line) and line[j] == " ":
                j += 1
            consumed += 1
            i = j
            continue
        break
    start = i
    while i < len(line) and line[i] in " \t":
        i += 1
    return consumed, i - start, i


@dataclass(frozen=True)
class _ListItem:
    """One OPEN list item, in BOTH spaces a dedent can be measured in.

    * `depth` — the blockquote depth the marker line was written at;
    * `rest_col` — the item's CONTENT column after that line's blockquote prefix,
      i.e. in the space `rest` lives in;
    * `raw_col` — the same column in RAW coordinates (`len(prefix) + rest_col`),
      which is where an item that sits OUTSIDE the fence's blockquote is matched:
      CommonMark matches the OUTER container first, so such an item ends on a raw
      dedent.

    Both spellings of ONE number, computed once at push time, so a later
    comparison cannot silently use a `rest` column against a raw indent — the
    mixing that bought a false `lands` on one family of shapes and lost a real
    claim on another in two earlier review rounds (#10634 review).
    """

    depth: int
    rest_col: int
    raw_col: int


def _item_dedented(line: str, item: _ListItem) -> bool:
    """Has `line` dedented out of `item` — so the item, and its fence, END?

    Measured after the item's OWN blockquote markers, which is the space its
    content column is expressed in: `_fence_line_column(line, item.depth)`
    consumes at most that many markers, so `>   text` against a depth-1 item's
    2-column content comes out at 2 (inside) while `> text` at column 0 comes out
    at 0 (outside). A line carrying FEWER markers than the item lives under has
    left the item's quote entirely, and so dedents out of everything inside it.

    This is the ONE rule, applied at every depth. Before it, only markers on the
    RAW line were tracked, so a marker inside a quote (`> - item`) was invisible,
    the item list stayed empty, and a fence opened under that item had no edge at
    all: the renderer ended the containing block there and opened a FRESH fence,
    while the tool kept the old one open and read the keyword inside the new
    block as prose. `lands` on a body GitHub renders into `<pre><code>` (#10634
    review P1, fail-open).
    """
    consumed, residual, _ = _fence_line_column(line, item.depth)
    if consumed < item.depth:
        return True                      # the item's own quote closed
    return residual < item.rest_col


def _fence_candidate(prefix: str, rest: str,
                     list_items: list[_ListItem]) -> _Fence | None:
    """The fence `(prefix, rest)` opens, or None if the line is prose.

    A fence may open ON a list-marker line itself (`- ``` `, `1. ``` `):
    CommonMark puts the code block at the item's CONTENT column and the marker
    is not part of it. Emitting such a line as prose made every line of the
    block prose too, so a QUOTED `Closes #N` inside it became a claim — and on a
    default base a `lands`, the one outcome this module forbids (#10634 review
    P1: `- ```\n  Closes #100\n  ``` ` reported `lands` for a block the renderer
    shows as `<pre><code>`).

    The content column is CommonMark's: the marker's width plus the spaces after
    it — or ONE column when that gap is wider than four, because such a line
    starts an INDENTED code block rather than a fence, which `_fence_open` then
    refuses on its own (a 4+ column `` ``` `` is not a fence).
    """
    # The content column the fence is measured from: the item already open (or
    # the one this very line opens), else column 0. It is passed INTO the opener
    # test as well as stored, because `content_col + 3` is where CommonMark's
    # "up to three spaces" budget ends (#10634 review P1).
    #
    # ⚠️ `rest` is already post-prefix, so the column handed to `_fence_open` is a
    # `rest` column. Passing a RAW one measured the run against the wrong columns
    # and read a 4-column indented block (inside a quote inside an item) as a
    # fence, blanking a real claim (#10634 review).
    item = _LIST_MARKER_RE.match(rest) if rest.strip() else None
    if item:
        m_indent = len(item.group("indent"))
        gap = len(item.group("gap"))
        marker_w = len(item.group(0)) - m_indent - gap
        base = m_indent + marker_w + (gap if gap <= 4 else 1)
        opener = _fence_open(rest[base:])
        if opener is None:
            return None
        # A marker on THIS line opens the item in `rest` space; its raw column
        # differs only by the prefix, and both are carried (see `_ListItem`).
        return _Fence(opener, prefix, base, True, len(prefix) + base,
                      _quote_depth(prefix))
    depth = _quote_depth(prefix)
    if not list_items:
        opener = _fence_open(rest, 0)
        if opener is None:
            return None
        return _Fence(opener, prefix, 0, False)
    enclosing = list_items[-1]
    if enclosing.depth == depth:
        # SAME container: the item's column is already a `rest` column, so the
        # dedent is measured with it directly and no raw number is needed.
        opener = _fence_open(rest, enclosing.rest_col)
        if opener is None:
            return None
        return _Fence(opener, prefix, enclosing.rest_col, True, 0,
                      enclosing.depth)
    # The item is OUTSIDE the fence's blockquote, so it is matched BEFORE the
    # quote's markers and its edge is the RAW column (`item_col_raw`).
    residual_col = max(0, enclosing.rest_col - len(prefix))
    opener = _fence_open(rest, residual_col)
    if opener is None:
        return None
    return _Fence(opener, prefix, residual_col, True, enclosing.raw_col,
                  enclosing.depth)


def strip_code(body: str, strict: bool = False) -> str:
    """Blank out the parts of a PR body that are CODE, not prose.

    ONLY fenced blocks are stripped. An INDENTED block is deliberately NOT: where
    a 4-space line is code and where it is a list item's continuation or a
    multi-paragraph bullet depends on the enclosing container's content column,
    and getting that wrong in the stripping direction loses a REAL claim — a
    false pass, the worst class here (two review cycles found it: first every
    indented line, then every indented line after a blank line). A warn-only
    instrument loses nothing by over-reporting a quoted keyword; it loses the
    reader by under-reporting a real one.

    A fence belongs to its CONTAINER, and ends when that container ends:

    * a depth-0 fence has no container, so nothing but a matching closer (or EOF)
      ends it — a `> ``` ` line inside it is CONTENT, and treating it as a closer
      blanked the prose after the block (review c9-2(i));
    * a QUOTED fence ends as soon as a line drops below its depth — including on a
      blank line, which ends the blockquote (review c8-1) — and the line that ends
      it is re-examined as a fresh line, because it may itself open a fence
      (review c10-2);
    * a fence opened inside a LIST ITEM ends when the ITEM's content ends — a
      line with real content indents below the item's CONTENT COLUMN, not below
      the fence run's own indent (review c10-3, corrected in #10634 review P1:
      §4.5 ends the block at "the end of the containing block", and an item's
      content line may sit BELOW the opener's column and still be inside the
      block) — and a fence may open ON the marker line itself, at the item's
      content column (#10634 review P1). That comparison is made in ONE space:
      the raw line with at most the fence's OWN blockquote depth consumed, so a
      `>` inside the block stays literal and a marker cannot move the item's
      edge (see `_fence_line_column`);
    * containers are compared by DEPTH, never by the raw prefix string: `>`, `> `,
      `  > ` are one container to CommonMark, and comparing strings meant a legal
      closer was never recognised (review c10-1);
    * `strict` (the CARRIER body, and the subject's own `lands` arm) additionally
      blanks indented lines, because there a lenient-only match would be a PASS.
      The indentation is measured on `rest` — the line AFTER its blockquote
      prefix — because measuring it on the raw line missed an indented code block
      that sits inside a quote, and that body bought a `lands` (#10634 review
      P2). A line that OPENS a fence is never blanked by this arm: a fence's
      content column is its problem, and blanking the opener lost the whole
      block's content as prose (#10634 review P1).

    What it does NOT do, deliberately: a keyword in blockquote PROSE (`> Closes
    #N`) stays a claim. GitHub acts on a closing keyword in a quoted paragraph —
    only CODE is ignored — so blanking it would be the under-reporting direction
    that loses real closures.
    """
    out: list[str] = []
    fence: _Fence | None = None
    list_items: list[_ListItem] = []
    for raw in body.splitlines():
        line = raw.expandtabs(4)
        indent = len(line) - len(line.lstrip(" "))
        prefix, rest = _split_prefix(line)
        # The line's indent AFTER its blockquote prefix: `close_base` is measured
        # there, which is the space the strict arm below reports in.
        rest_indent = len(rest) - len(rest.lstrip(" "))
        if fence is not None:
            # The container can END the block: the quote it lived in closed, or
            # (for a fence opened inside a list item) the item did.
            #
            # Both tests are measured in ONE coordinate space — the RAW line,
            # with at most the fence's own blockquote depth consumed — so each
            # compares against the column the field it names already holds
            # (`close_base` / `item_col_raw`), never a derived raw column. Two
            # review cycles did
            # this in two spaces at once (a raw indent against a `rest` column,
            # then a `rest` indent against a raw column) and each way bought a
            # false `lands` on one family of shapes while losing a real claim on
            # the other (#10634 review): `> - ```\n>   Closes #N` (a marker-line
            # fence inside a quote) read every content line as a dedent, and
            # `- ```\n   > Closes #N` ended an item fence on literal content.
            fence_depth = _quote_depth(fence.prefix)
            consumed, residual, column = _fence_line_column(line, fence_depth)
            ended = consumed < fence_depth
            if not ended and fence.in_list and line.strip():
                if fence.item_depth < fence_depth:
                    # The item is OUTSIDE the fence's blockquote (its content
                    # column sits at or before the quote's markers), so the item
                    # is matched BEFORE them and its edge is the RAW column.
                    # Measuring this dedent after consuming a `>` marker read a
                    # column-0 `>` as the container's own and missed the dedent:
                    # the item (and its fence) stayed "open" over top-level
                    # prose, losing a real claim (#10634 review).
                    ended = indent < fence.item_col_raw
                elif line[column:].strip():
                    # …and an item at THIS depth is measured in `rest` space,
                    # which is the space `close_base` holds for it. The branch is
                    # selected by the item's DEPTH, never by whether the raw
                    # column happens to be set: a quoted item's `rest` column
                    # compared against a raw indent reads as "dedented" on every
                    # content line, ends the block on the first one, and exposes
                    # the keyword inside it (#10634 review P1, fail-open).
                    ended = residual < fence.close_base
            if not ended:
                # A closer is measured from the CONTAINING BLOCK's content
                # column (§4.5: up to three spaces past it, and its indentation
                # need not match the opener's) — so the run has to sit where the
                # container's content starts, and the text after that column is
                # what must be nothing but fence and whitespace.
                #
                # ⚠️ BOTH sides are in ONE space. `residual` is the indent after
                # this line's own markers, which is the space `close_base` holds
                # (the item's column past the prefix). Subtracting a RAW column
                # from `residual` instead charged the MARKER'S allowance
                # twice — the opener's `  > ` is 4 raw columns, the closer's `> `
                # only 2, so a 4-space-indented run `>     ``` ` came out 2 columns
                # past the container and closed a fence the renderer keeps open:
                # `lands`, exit 0, no annotation (#10634, found by a sweep against
                # the CommonMark reference implementation, not by a reviewer).
                past = residual - fence.close_base
                if (past <= _MAX_FENCE_INDENT
                        and _is_closer(line[column:], fence.opener)):
                    fence = None
                out.append("")
                continue
            # The container ended, so this line is FRESH: it may open a new fence
            # (review c10-2), and — outside the block — it is also where the list
            # item stack is tracked again.
            fence = None
        # LIST-ITEM TRACKING happens only on lines that are NOT inside a fence. A
        # marker line inside the block is CONTENT; counting it as an item opened a
        # phantom item that then "ended" the block on a dedented claim it should
        # have blanked, and the claim became a `lands` (#10634 review P2). The
        # dedent-pop runs FIRST, on every non-blank line, so a marker that dedents
        # past an outer item pops it before pushing its own.
        #
        # The marker is matched on `rest`, not on the raw line: a marker inside a
        # blockquote (`> - item`) is a real item, and matching the raw line never
        # saw it — the block it opened under then had no edge and the keyword
        # inside the block the renderer calls CODE read as prose (#10634 review
        # P1, fail-open). `_item_dedented` is the matching pop, in the same space.
        if line.strip():
            while list_items and _item_dedented(line, list_items[-1]):
                list_items.pop()
            marker = _LIST_MARKER_RE.match(rest)
            if marker:
                rest_col = len(marker.group(0))
                list_items.append(
                    _ListItem(_quote_depth(prefix), rest_col,
                              len(prefix) + rest_col))
        fence = _fence_candidate(prefix, rest, list_items)
        if fence:
            out.append("")
            continue
        # `rest_indent` is the ONLY indentation test: `line` was `expandtabs(4)`d
        # a few lines up, so a tab can never survive into `rest` — the
        # `rest.startswith("\t")` this clause used to carry was unreachable and
        # read as a live guard (#10634 review P3, twice). A tab-indented line is
        # already a `rest_indent >= 4` line by the time it gets here.
        if strict and rest_indent > _MAX_FENCE_INDENT:
            out.append("")
            continue
        out.append(raw)   # prose: keep it, keywords and all
    return "\n".join(out)


def parse_closing_refs(body: str, repo: str | None = None,
                       strict: bool = False) -> list[int]:
    """Issue numbers this body claims to close, in body order, deduped.

    Code spans and fenced blocks (``` and `~~~`) are stripped first, because a
    keyword inside one is a *quotation* of a keyword, not a claim (GitHub does
    not act on it either, so treating it as a claim would manufacture a finding).
    Indented blocks are stripped ONLY when `strict` — see `strip_code`. Two
    callers use it, and both for the same reason (a lenient-only match there
    would be a PASS): the CARRIER body when asking whether it restates the
    keyword, and the subject's OWN `lands` arm in `classify` (which refuses to
    pass on a ref that survives only the lenient parse). Finding arms stay
    lenient, where the cost of leniency is a false warning, not a pass.

    `repo` (``owner/name``) decides what an `owner/repo#N` reference means. A
    slug naming a DIFFERENT repo is a closure elsewhere, not a claim about an
    issue here — but a slug naming THIS repo is exactly as much a claim as the
    bare `#N` form, and dropping it would be a false pass by non-detection
    (#10597 review P2-3). With no `repo`, a slug is treated as foreign: the
    function cannot tell, and inventing a finding is worse than missing one.
    """
    text = _strip_inline_code(strip_code(body or "", strict), strict=strict)
    seen: list[int] = []
    # The separator spelling follows the QUESTION: a pass (strict) refuses a
    # keyword whose reference sits across a blank line; a finding (lenient)
    # still reports it (see `_CLOSING_SEPARATORS`).
    pattern = _CLOSING_RE_ONELINE if strict else _CLOSING_RE
    for m in pattern.finditer(text):
        slug = m.group("slug")
        if slug and (repo is None or slug.lower() != repo.lower()):
            continue
        n = int(m.group("number"))
        if n not in seen:
            seen.append(n)
    return seen


def resolve_carrier(pr: PullRequest, by_head: dict[str, PullRequest],
                    default_branch: str) -> PullRequest | None:
    """The PR that will actually land this stack on the default branch.

    Returns `pr` itself when it already targets the default branch. Returns
    None when the stack cannot be followed to a default-branch PR (a base
    branch with no PR, a cycle, or a stack deeper than MAX_STACK_DEPTH) — the
    caller reports that as `unresolved`, never as a pass. WHICH of the three it
    was is `_carrier_walk`'s second return value, because the message must name
    the cause that happened.
    """
    path = carrier_path(pr, by_head, default_branch)
    return path[-1] if path else None


def carrier_path(pr: PullRequest, by_head: dict[str, PullRequest],
                 default_branch: str) -> list[PullRequest] | None:
    """`resolve_carrier`, but keeping EVERY hop: [pr, …, the default-base PR].

    `classify` needs the intermediate links, not just the top: a subject's
    commits reach the default branch only THROUGH them, so "the work did land"
    cannot be judged from the two endpoints alone (#10634 cycle 3).

    None means the walk could not reach the default branch; `_carrier_walk` is
    the same walk with the REASON attached, which is what a message needs —
    "no PR exists" and "the walk stopped" are different facts.
    """
    path, why = _carrier_walk(pr, by_head, default_branch)
    return None if why else path


def _carrier_walk(pr: PullRequest, by_head: dict[str, PullRequest],
                  default_branch: str) -> tuple[list[PullRequest], str | None]:
    """([pr, …, deepest hop], why the walk stopped | None).

    THREE distinct causes return no carrier, and a caller that emits ONE message
    for all of them asserts a fact it cannot see (#10634 review P3):

    * `no-pr`  — the deepest base has no PR on it at all, so nothing can be shown
      to carry the work yet. This is the only cause where a PR is MISSING;
    * `cycle`  — the bases point back at a PR the walk already visited. Every PR
      in it exists, and telling its author to open one is the single action that
      cannot repair a cycle;
    * `depth`  — the stack is deeper than `MAX_STACK_DEPTH`. Every PR in it
      exists too: measurement STOPPED, evidence is not missing.

    The path is always returned, so the caller can NAME the hops it did see.
    """
    seen: set[int] = {id(pr)}
    path = [pr]
    cur = pr
    for _ in range(MAX_STACK_DEPTH):
        if cur.base == default_branch:
            return path, None
        nxt = by_head.get(cur.base)
        if nxt is None:
            return path, "no-pr"
        # IDENTITY, not the PR number: the offline mode's records carry no
        # number (they are all 0), so a number comparison would report a cycle
        # on the very first hop and turn every offline judgement into
        # `unresolved`. Identity is also the correct cycle test in the live
        # mode, where every record comes from `by_head` and is therefore stable.
        if id(nxt) in seen:
            return path, "cycle"
        seen.add(id(nxt))
        path.append(nxt)
        cur = nxt
    return path, "depth"


def _no_carrier_detail(pr: PullRequest, issue: int, default_branch: str,
                       path: list[PullRequest], why: str,
                       by_head: dict[str, PullRequest]) -> str:
    """The `carrier is None` message, for the cause that actually happened.

    The verdict is `unresolved` either way and it is fail-closed; what differs is
    the CAUSE and the remedy the reader infers from it. The module states the
    rule in two other arms (`classify`'s lenient-only arm and
    `_closure_tense`) — a message must not name a cause the tool cannot
    distinguish — and this arm was the one place it was not applied: all three
    causes emitted "no PR with that head branch exists", which is FALSE for a
    cycle (the PR exists, it is the other half of the cycle) and false for the
    depth cap (every PR exists; measurement stopped). The remedy the false
    sentence implies — open a PR on that base — is the one action that cannot
    repair either (#10634 review P3).
    """
    head = (f"PR #{pr.number} says `Closes #{issue}` but targets "
            f"`{pr.base}`, and ")
    if why == "cycle":
        cur = path[-1]
        back = by_head.get(cur.base)
        if back is not None:
            pair = (f"PR #{cur.number} targets `{cur.base}` and PR #{back.number} "
                    f"targets `{back.base}` — each is the other's head branch")
        else:                                    # unreachable, but never a lie
            pair = f"PR #{cur.number} targets `{cur.base}`, a PR already visited"
        return (head +
                f"the stack it sits in is a CYCLE: {pair} — so no PR in it "
                f"targets `{default_branch}`. NO PR IS MISSING here: nothing can "
                f"be shown to carry #{issue} to `{default_branch}` because the "
                f"bases point at each other. Re-target one of them at "
                f"`{default_branch}` (or at a branch whose PR targets it) and the "
                f"keyword fires on that merge.")
    if why == "depth":
        return (head +
                f"its stack is deeper than this tool follows "
                f"(`MAX_STACK_DEPTH = {MAX_STACK_DEPTH}` hops): measurement "
                f"stopped one hop short of the PR whose head is "
                f"`{path[-1].base}`. Every PR in the stack exists — this is a cap "
                f"on the walk, not a missing PR — so nothing can be shown to "
                f"carry #{issue} to `{default_branch}` YET. Re-state the keyword "
                f"on a PR closer to `{default_branch}` in the chain, or raise the "
                f"cap deliberately.")
    return (head + f"no PR with that head branch exists — nothing can be shown "
            f"to carry #{issue} to `{default_branch}` yet.")


def classify(pr: PullRequest, by_head: dict[str, PullRequest],
             default_branch: str, repo: str | None = None) -> list[Finding]:
    """Every closing ref in `pr`'s body, judged against where it will land."""
    refs = parse_closing_refs(pr.body, repo)
    if not refs:
        return []
    path, why = _carrier_walk(pr, by_head, default_branch)
    carrier = None if why else path[-1]
    # Only the `lands` arm needs this, and it is the SAME body for every ref, so
    # it is computed once (review c6).
    strict_refs = set(parse_closing_refs(pr.body, repo, strict=True))
    findings: list[Finding] = []
    for issue in refs:
        if carrier is None:
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="unresolved",
                carrier=None,
                detail=_no_carrier_detail(pr, issue, default_branch, path,
                                          why or "no-pr", by_head),
            ))
            continue
        # A SUBJECT that was CLOSED WITHOUT MERGING never fired the keyword — but
        # ONLY when its base IS the default branch: on a sibling base the question
        # is whether the CARRIER carries the keyword, which the arms below answer,
        # and claiming the subject "targets main" would be false (review c3-1).
        if pr.closed_unmerged and pr.base == default_branch:
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="unresolved",
                carrier=pr.number,
                detail=(f"PR #{pr.number} says `Closes #{issue}` and targets "
                        f"`{default_branch}`, but it was CLOSED WITHOUT "
                        f"MERGING — GitHub never fired the keyword, so "
                        f"#{issue} stays open and its work is not on "
                        f"`{default_branch}`. Re-open it, or carry the change "
                        f"(and the keyword) on the PR that does land."),
            ))
            continue
        # IDENTITY, not the PR number: the offline mode has no numbers, and a
        # number comparison would make 0 == 0 read as "this PR lands" — a false
        # pass on exactly the shape this tool exists to catch.
        if carrier is pr:
            # A PASS may not rest on a lenient-only match: `strict_refs` is the
            # same body parsed strictly, so a claim at column 0 passes and an
            # ambiguous one downgrades to a warning (review c5-2).
            if issue not in strict_refs:
                # The message must not assert WHAT GitHub thinks, and it must not
                # name a CAUSE it cannot distinguish. The strict parse refuses THREE
                # shapes and the reader can see none of them from here: a keyword on
                # a four-space-indented line, a keyword separated from its `#N` by a
                # blank line (#10634 review), and a keyword inside a code span that
                # crosses a soft line break (added by the span fix). Naming two of
                # the three told the third body's author that a cause was present
                # that was not (#10634 review P3) — so the requirement is stated in
                # a form ALL of them can act on. The indented case is still a CODE
                # BLOCK in one context and a list item's continuation — where GitHub
                # DOES fire — in another (see `strip_code`, and the list-item cases
                # pinned below), so this states what THIS tool can show, not a fact
                # it cannot.
                #
                # The remedy must also not CREATE a closure. A body that QUOTES the
                # keyword (the multi-line code span above is exactly that) is being
                # ignored CORRECTLY, and telling its author to "state `Closes #N` at
                # column 0" would talk a quotation into becoming a real closure.
                findings.append(Finding(
                    issue=issue, pr=pr.number, base=pr.base, verdict="unresolved",
                    carrier=pr.number,
                    detail=(f"PR #{pr.number} targets `{default_branch}`, but its "
                            f"`Closes #{issue}` survives only the LENIENT parse: "
                            f"the reference is there in the body, but on a shape "
                            f"this tool cannot show GitHub acts on — inside a code "
                            f"span, on a four-space-indented line (a code block in "
                            f"one context, a list item's continuation in "
                            f"another), or across a blank line. None of those is "
                            f"counted as a pass. If the reference is a "
                            f"QUOTATION, it is being ignored correctly and there "
                            f"is nothing to fix; if it is meant as a claim, state "
                            f"`Closes #{issue}` on one line, in plain prose at "
                            f"column 0, outside any backticks."),
                ))
                continue
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="lands",
                carrier=pr.number,
                detail=(f"PR #{pr.number} says `Closes #{issue}` and targets "
                        f"`{default_branch}` — GitHub fires the keyword on merge."),
            ))
            continue
        # A CARRIER that was CLOSED WITHOUT MERGING can never carry anything.
        # Reporting `lands` here would be the worst failure this tool has: the
        # remedy it itself printed ("re-state the keyword on PR #X") produces
        # exactly this state when #X is later closed or superseded, so the tool
        # would then bless every stacked PR on that base for ever.
        if carrier.closed_unmerged:
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="unresolved",
                carrier=carrier.number,
                detail=(f"PR #{pr.number} says `Closes #{issue}` with base "
                        f"`{pr.base}`; PR #{carrier.number} targets "
                        f"`{default_branch}` but was CLOSED WITHOUT MERGING, so "
                        f"nothing will carry #{issue} to `{default_branch}`. "
                        f"Re-state the keyword on whatever PR now targets "
                        f"`{default_branch}` for this work."),
            ))
            continue
        carried = issue in parse_closing_refs(carrier.body, repo, strict=True)
        broken_below = _path_break(path)
        if carried and broken_below:
            # The keyword FIRES, but the work does not ride: `lands` means "it
            # rides in and closes" (module docstring), so a closure over a stranded
            # branch is not a pass — it is the silent case this tool exists to
            # catch, with a green exit code on top (#10634 cycle 8).
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="unresolved",
                carrier=carrier.number,
                detail=(f"PR #{pr.number} says `Closes #{issue}` with base "
                        f"`{pr.base}`; PR #{carrier.number} targets "
                        f"`{default_branch}` and carries the same keyword, so "
                        f"{_closure_tense(carrier, issue)} — but {broken_below}. "
                        f"{_closure_consequence(pr, carrier, issue, default_branch)}"
                        ),
            ))
            continue
        if carried and pr.closed_unmerged:
            # The keyword FIRES on the carrier, but the subject was CLOSED WITHOUT
            # MERGING, so its own commits are in no branch the carrier carries up
            # (#10634 cycle 10). `lands` means the work rides in as well, so this
            # cannot be a pass — and `_path_break` cannot see it, because the
            # subject is only ever a LOWER link and the carrier link is the one
            # being skipped while the carrier is open.
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="unresolved",
                carrier=carrier.number,
                detail=(f"PR #{pr.number} says `Closes #{issue}` with base "
                        f"`{pr.base}`; PR #{carrier.number} targets "
                        f"`{default_branch}` and carries the same keyword, so "
                        f"{_closure_tense(carrier, issue)} — but PR #{pr.number} "
                        f"was CLOSED WITHOUT MERGING, so its own work is in no "
                        f"branch PR #{carrier.number} carries up. "
                        f"{_closure_consequence(pr, carrier, issue, default_branch)}"
                        ),
            ))
            continue
        if carried:
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="lands",
                carrier=carrier.number,
                detail=(f"PR #{pr.number} says `Closes #{issue}` with base "
                        f"`{pr.base}`; PR #{carrier.number} targets "
                        f"`{default_branch}` and carries the same keyword, so "
                        f"{_closure_tense(carrier, issue)}."),
            ))
        else:
            # A broken LINK blocks the ride even while the carrier is still OPEN
            # (#10634 cycles 4-7): an intermediate that was CLOSED WITHOUT MERGING,
            # or a hop that merged BEFORE the PR whose base it carries up, means
            # the subject's commits never reached the branch the carrier merges —
            # so "only lands when PR #N merges" promises a ride the record set
            # refutes. `unresolved` (nothing CAN carry it), never a pass.
            #
            # The remedy names ONLY what the record set supports. Re-opening a
            # closed link restores the path ABOVE it, not the commits below it, so
            # it is offered as a partial repair rather than "the repair"; editing
            # an OPEN carrier's body cannot help (the arm's premise is that the
            # commits are not in the branch it merges), and a MERGED carrier cannot
            # be updated at all — both were prescribed here before this cycle.
            closed_links = [link for link in path[1:-1] if link.closed_unmerged]
            if closed_links or (not carrier.merged and _path_break(path)):
                tail = _reopen_tail(pr, path, closed_links, issue,
                                    default_branch)
                findings.append(Finding(
                    issue=issue, pr=pr.number, base=pr.base,
                    verdict="unresolved", carrier=carrier.number,
                    detail=(f"PR #{pr.number} says `Closes #{issue}` with base "
                            f"`{pr.base}`; PR #{carrier.number} targets "
                            f"`{default_branch}`, but "
                            f"{_not_landed_why(pr, carrier, path)} — so #{issue}'s "
                            f"work is not on `{default_branch}`, and nothing in "
                            f"this stack AS IT STANDS will carry it there. "
                            f"{tail}"),
                ))
                continue
            # The remedy depends on a state the reader cannot see from the
            # finding alone: an OPEN carrier can still be amended, a MERGED one
            # cannot — telling a lane to "re-state the keyword on" an already
            # merged PR prescribes an impossible action and hides the real one.
            #
            # ⚠️ "the work did land" is a claim about the SUBJECT, and a merged
            # CARRIER does not establish it (#10634 review P1). A carrier merges
            # the branch it was opened from, so a subject that is still OPEN has
            # no work in it — and one that merged into its sibling base AFTER the
            # carrier landed is stranded there. Telling that reader to "Close #N
            # by hand" marks an issue done whose work is nowhere near the default
            # branch: the exact silent-stranding this tool exists to surface.
            subject_landed = _subject_landed(pr, path)
            if carrier.merged and not subject_landed:
                findings.append(Finding(
                    issue=issue, pr=pr.number, base=pr.base,
                    verdict="unresolved", carrier=carrier.number,
                    detail=(f"PR #{pr.number} says `Closes #{issue}` with base "
                            f"`{pr.base}`; PR #{carrier.number} targets "
                            f"`{default_branch}` and has ALREADY merged, but "
                            f"{_not_landed_why(pr, carrier, path)} — so #{issue}'s work "
                            f"is NOT on `{default_branch}`. GitHub has not fired "
                            f"the keyword, and nothing in the current stack "
                            f"will: {_stranded_remedy(pr, issue, default_branch)}"),
                ))
                continue
            # A SUBJECT closed WITHOUT merging can never put its own work in the
            # base branch, so the sibling-base arm must not promise that the
            # carrier lands it (#10634 cycle 2, P2). GitHub still does not fire
            # the keyword, so the verdict stays `inert`; only the CLAIM and the
            # remedy change. The `carried` arm above is deliberately untouched: a
            # carrier that restates the keyword is right whatever the subject did.
            if pr.closed_unmerged:
                findings.append(Finding(
                    issue=issue, pr=pr.number, base=pr.base, verdict="inert",
                    carrier=carrier.number,
                    detail=(f"PR #{pr.number} says `Closes #{issue}` but its base "
                            f"is `{pr.base}`, NOT `{default_branch}` — GitHub "
                            f"will NOT close #{issue}. PR #{pr.number} was "
                            f"CLOSED WITHOUT MERGING, so nothing this tool can "
                            f"show puts its work in `{pr.base}`, and PR "
                            f"#{carrier.number} carries no `Closes #{issue}` this "
                            f"tool can show fires. {_stranded_remedy(pr, issue, default_branch)}"),
                ))
                continue
            if carrier.merged:
                remedy = (f"PR #{carrier.number} has ALREADY merged, so the "
                          f"work did land — only the closure did not fire. "
                          f"Close #{issue} by hand, citing PR "
                          f"#{carrier.number}.")
                lands = (f"#{issue}'s work already landed with PR "
                         f"#{carrier.number}")
            else:
                remedy = (f"Re-state the keyword on PR #{carrier.number}.")
                lands = (f"#{issue}'s work only lands when PR #{carrier.number} "
                         f"merges")
            findings.append(Finding(
                issue=issue, pr=pr.number, base=pr.base, verdict="inert",
                carrier=carrier.number,
                detail=(f"PR #{pr.number} says `Closes #{issue}` but its base is "
                        f"`{pr.base}`, NOT `{default_branch}` — GitHub will NOT "
                        f"close #{issue}, and {lands}, which carries no `Closes "
                        f"#{issue}` this tool can show fires. {remedy}"),
            ))
    return findings


def _closure_tense(carrier: PullRequest, issue: int) -> str:
    """The closure in the tense the record set supports (#10634 cycle 9).

    A MERGED carrier already fired the keyword, so "it closes #N when it lands"
    promises an event in the past. When the path is healthy that is simply the
    truth (the issue closed); when it is broken the consequence below says what to
    do about a CLOSED issue.
    """
    if carrier.merged:
        return (f"the closure ALREADY FIRED when PR #{carrier.number} merged, so "
                f"#{issue} is closed")
    return f"it closes #{issue} when it lands"


def _closure_consequence(pr: PullRequest, carrier: PullRequest, issue: int,
                         default_branch: str) -> str:
    """What to DO when the closure fired (or will) over stranded work."""
    if carrier.merged:
        return (f"#{issue} is therefore closed even though its work is not on "
                f"`{default_branch}`: re-open #{issue} first, then carry the "
                f"stranded commits to `{default_branch}`, and close it when they "
                f"land.")
    return (f"So #{issue} would be closed over work that is not on "
            f"`{default_branch}`. {_stranded_remedy(pr, issue, default_branch)}")


def _reopen_tail(pr: PullRequest, path: list[PullRequest],
                 closed_links: list[PullRequest], issue: int,
                 default_branch: str) -> str:
    """The trailing text of the blocked arm: the repair, exactly once.

    When the first closed LINK's head branch already holds the subject's commits —
    which `_subject_landed` decides over the links BELOW it, so an OPEN link between
    the subject and the closed one correctly fails the test — re-opening that link
    and retargeting it at the default branch IS the repair, and saying "that only
    restores the path above it" contradicts the remedy in the same sentence
    (#10634 cycle 11). When the links below it DID break, the weaker wording is the
    true one and `_stranded_remedy` supplies the action. With no closed link the
    remedy is simply `_stranded_remedy`.
    """
    if not closed_links:
        return _stranded_remedy(pr, issue, default_branch)
    link = closed_links[0]
    # IDENTITY, like every other comparison in the walk: offline records all carry
    # number=0, so value equality can resolve to the wrong index.
    at = next(i for i, hop in enumerate(path) if hop is link)
    if _subject_landed(pr, path[:at]):
        return (f"PR #{link.number} can be re-opened and retargeted at "
                f"`{default_branch}` — that carries #{pr.number}'s commits up with "
                f"it, and `Closes #{issue}` still has to be stated on the PR that "
                f"lands.")
    return (f"PR #{link.number} can be re-opened and retargeted at "
            f"`{default_branch}`, but that only restores the path above it: the "
            f"commits must reach `{link.head}` too. "
            f"{_stranded_remedy(pr, issue, default_branch)}")


def _path_break(path: list[PullRequest]) -> str | None:
    """The FIRST link BELOW the carrier that provably breaks the ride, or None.

    ONE predicate for the whole question, because a broken path means the same
    thing whether it is used to REFUSE a `lands` or to explain a remedy (#10634
    cycle 8). Three shapes, each provable from the record set and none needing
    ancestry:

    * an intermediate that was never merged (its branch's commits were not carried
      up at all);
    * an upper link merged while the PR BELOW it never merged — the lower PR's
      commits enter a branch that had ALREADY been carried up, so they are on
      neither side (this is the shape a timestamp test cannot see: an OPEN hop has
      no `merged_at`);
    * an upper link that merged BEFORE the lower one (the same harm, by ORDER).

    The carrier's own link is skipped ONLY while the carrier is unmerged: an open
    stack is in progress, not broken, and an open carrier is never blamed for a
    break below it. Once the carrier HAS merged its link counts like any other —
    excluding it let a merged, keyword-carrying carrier pass `lands` over work that
    never rode in (#10634 cycle 9: 18 configurations were false passes, all of them
    this one link).
    """
    carrier = path[-1]
    for lower, upper in pairwise(path):
        if upper is carrier and not carrier.merged:
            # "The carrier has not merged yet" is the normal, healthy state of an
            # open stack — never a break, and never blamed on the carrier.
            #
            # ⚠️ BELT-AND-BRACES: the `not upper.merged` branch below reaches the
            # same verdict whenever the carrier is unmerged, so deleting this block
            # changes no verdict and no text (only the discarded
            # `carrier.closed_unmerged` edge differs, and `classify` answers that
            # earlier). Kept as an explicit statement of the healthy case (#10634
            # cycle 10).
            continue
        if not upper.merged:
            # A CLOSED-unmerged link will never merge, so it is a break whatever the
            # carrier is doing.
            if upper.closed_unmerged:
                return (f"PR #{upper.number} (base `{upper.base}`) was CLOSED "
                        f"WITHOUT MERGING, so nothing from PR #{path[0].number} "
                        f"ever reached `{carrier.base}`, the branch PR "
                        f"#{carrier.number} "
                        f"{'merged' if carrier.merged else 'merges'} into")
            # An OPEN intermediate is only a break once the carrier has landed
            # WITHOUT it: while the carrier is still open the stack is simply in
            # progress, which is the normal state of a live stack — refusing that
            # would turn every multi-hop PR into a warning (cycle 8).
            if not carrier.merged:
                continue
            return (f"PR #{upper.number} (base `{upper.base}`) was not merged, so "
                    f"nothing from PR #{path[0].number} ever reached "
                    f"`{carrier.base}`, the branch PR #{carrier.number} merged "
                    f"into")
        if not lower.merged:
            return (f"PR #{upper.number} (base `{upper.base}`) has ALREADY merged "
                    f"while PR #{lower.number} is {_state_words(lower)}, so "
                    f"#{lower.number}'s commits were never in the branch "
                    f"#{upper.number} carried up")
        if upper.merged_at and lower.merged_at and upper.merged_at < lower.merged_at:
            return (f"PR #{upper.number} (base `{upper.base}`) merged at "
                    f"{upper.merged_at}, BEFORE PR #{lower.number} merged into "
                    f"`{lower.base}` at {lower.merged_at}, so #{lower.number}'s "
                    f"commits were never in the branch PR #{upper.number} "
                    f"carried up")
    return None


def _state_words(pr: PullRequest) -> str:
    """The module's own wording for a PR's own state."""
    if pr.closed_unmerged:
        return "CLOSED WITHOUT MERGING"
    return f"still {pr.state.upper()}"


def _subject_landed(pr: PullRequest, path: list[PullRequest]) -> bool:
    """Whether the SUBJECT's own work rode the carrier to the default branch.

    Neither fact needed here is ancestry, so this is deliberately conservative:
    `merged` says each PR merged into ITS OWN base, and the `merged_at` stamps
    say in which ORDER — checked PAIRWISE up the stack, because a PR's commits
    reach the default branch only through every hop above it (#10634 cycles 3-4).
    Both weaker versions claimed "the work did land" for work that was stranded:
    judging the two ENDPOINTS alone passed a stack whose middle never merged, and
    comparing every link with the CARRIER alone passed a subject that merged into
    its base AFTER that base had already been carried up (12:00 subject, 11:00
    mid, 14:00 carrier). A hop that merged later than the PR it carries is the
    same harm as a hop that never merged. With no stamps (offline records) the
    merged states are all there is.
    """
    carrier = path[-1]
    if not (carrier.merged and pr.merged):
        return False
    for lower, upper in pairwise(path):
        if not upper.merged:
            return False
        if lower.merged_at and upper.merged_at and upper.merged_at < lower.merged_at:
            return False
    return True


def _stranded_remedy(pr: PullRequest, issue: int, default_branch: str) -> str:
    """The action that actually EXISTS for the subject's own state.

    One arm, three states, and "re-open PR #N" is only possible for one of them
    (#10634 cycle 2, P2): an OPEN PR has nothing to re-open — it needs its BASE
    retargeted, and the keyword then fires normally — and a MERGED one cannot be
    re-opened at all. Prescribing either is the impossible-action class this whole
    branch exists to avoid, and it reads as the tool being broken.
    """
    if pr.closed_unmerged:
        return (f"Re-open PR #{pr.number}, retarget it at `{default_branch}`, "
                f"and state `Closes #{issue}` on it")
    if pr.merged:
        return (f"PR #{pr.number} has merged and cannot be re-opened: open a PR "
                f"from `{pr.base}`'s current tip that targets "
                f"`{default_branch}` for the stranded commits, and state "
                f"`Closes #{issue}` on it")
    return (f"Retarget PR #{pr.number}'s base at `{default_branch}` and state "
            f"`Closes #{issue}` on it — it is still open, so there is nothing "
            f"to re-open")


def _not_landed_why(pr: PullRequest, carrier: PullRequest,
                    path: list[PullRequest]) -> str:
    """The LINK that breaks the chain, in the words the reader can check.

    The link half is `_path_break` — the SAME predicate the `lands` arm refuses on,
    so the diagnosis and the refusal cannot disagree (#10634 cycle 8). When no link
    below the carrier is broken, the SUBJECT's own state is the reason; a
    closed-unmerged subject is reported in the module's OWN wording for that state
    ("CLOSED WITHOUT MERGING", see the carrier arm above), because "still CLOSED"
    describes a transient state when it is a final one and it hides that re-opening
    is an option (#10634 review).
    """
    link = _path_break(path)
    if link:
        return link
    if not pr.merged:
        return (f"PR #{pr.number} itself is {_state_words(pr)}, so its commits "
                f"were not in the branch PR #{carrier.number} "
                f"{'merged' if carrier.merged else 'merges'} into")
    # Only a BREAK at a link reaches here now that the predicate sees every pair —
    # and it may name an ORDER only when the stamps show one (#10634 cycle 9: the
    # unguarded version printed "AFTER" over a LATER carrier stamp, and over two
    # identical stamps).
    #
    # ⚠️ BELT-AND-BRACES, and the one documented exception to "a guard must be seen
    # to fail" in this module: `_path_break` now evaluates every pair, so no stamp
    # comparison can reach this line with a NON-increasing order — mutating the
    # condition away leaves the suite green, because the shape is unreachable, not
    # because the guard is wrong. It is kept so the sentence cannot start lying if
    # `_path_break` ever narrows again (it did, twice, in cycles 8 and 9).
    if pr.merged_at and carrier.merged_at and pr.merged_at > carrier.merged_at:
        return (f"PR #{pr.number} merged into `{pr.base}` at {pr.merged_at}, AFTER "
                f"PR #{carrier.number} merged at {carrier.merged_at}, so its "
                f"commits landed on the sibling branch too late to ride it")
    # Unreachable in principle (the predicate sees every pair), and phrased so it
    # stays TRUE if it ever is reached: it asserts only what put us in this
    # function, never a state word that could read "is still MERGED".
    return (f"PR #{pr.number} did not ride PR #{carrier.number} to "
            f"`{carrier.base}`")


def worst_verdict(findings: list[Finding]) -> str:
    """The single verdict for a set of findings, order worst-first."""
    for verdict in ("inert", "unresolved", "lands"):
        if any(f.verdict == verdict for f in findings):
            return verdict
    return "none"


# ── MERGED-BUT-OPEN core (#10698): pure, network-free ────────────────────────

#: The NON-closing reference verbs. GitHub does not act on any of these — the
#: closing vocabulary is exactly `close(s|d) fix(es|ed) resolve(s|d)` — which is
#: the whole reason they need their own class: a `Refs #N` is a real claim about
#: an issue that can never close one.
#:
#: `See` is deliberately NOT included. It fires on ordinary prose ("see #100 for
#: context"), so it would make `partial` near-universal and the fleet signal
#: worthless. Bare mentions — including the commit-message path — come from the
#: issue's own timeline instead, which is the source, not a guess.
_REFERENCE_VERBS = r"refs?|references?"

def _reference_re(sep: str) -> re.Pattern[str]:
    """The reference pattern with ONE separator spelling (`lenient`/`strict`).

    Same asymmetry as `_closing_re`, for the same reason: a PASS arm may not
    rest on a shape the tool cannot demonstrate GitHub acts on.
    """
    return re.compile(
        r"(?i)\b(?P<keyword>" + _REFERENCE_VERBS + r")"
        + sep
        + r"(?:(?P<slug>[A-Za-z0-9._-]+/[A-Za-z0-9._-]+))?"
        r"#(?P<number>\d+)\b"
    )


_REFERENCE_RE = _reference_re(_CLOSING_SEPARATORS["lenient"])
_REFERENCE_RE_ONELINE = _reference_re(_CLOSING_SEPARATORS["strict"])


def parse_reference_refs(body: str, repo: str | None = None,
                         strict: bool = False) -> list[int]:
    """Issue numbers this body REFERENCES without claiming to close.

    Same code-stripping as `parse_closing_refs`, and that is not a detail: a
    QUOTED reference is not a reference. Measured on this repository (2026-11-03),
    a naive regex read `PR #10519` as carrying `Closes #10541` because that exact
    phrase sits inside a backtick span in its prose — the shape this function
    would reproduce if it parsed the body itself.
    """
    text = _strip_inline_code(strip_code(body or "", strict), strict=strict)
    seen: list[int] = []
    pattern = _REFERENCE_RE_ONELINE if strict else _REFERENCE_RE
    for m in pattern.finditer(text):
        slug = m.group("slug")
        if slug and (repo is None or slug.lower() != repo.lower()):
            continue
        n = int(m.group("number"))
        if n not in seen:
            seen.append(n)
    return seen


#: A markdown TASK LIST item. GitHub renders `-`, `*`, `+` and `N.` alike as task
#: lists, so all four markers are accepted — counting only `-` would report
#: `total=0` on a `* [ ]` checklist and let that axis pass vacuously.
_TASK_ITEM_RE = re.compile(
    r"^[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+\[([ xX])\][ \t]", re.MULTILINE)


@dataclass(frozen=True)
class Acceptance:
    """The issue's own task-list completion: an ANNOTATION, never a gate.

    Why it cannot gate: measured over the 43 open P0/P1 issues at #10698, 37 carry
    no task list at all (so the axis would pass vacuously) and none of the 6 that
    do has every box checked (so it would block the largest issues permanently).
    Ticks are also self-attestation by whoever wrote the body. It is printed
    because a reader asked for it, and `done` does not depend on it.
    """

    total: int = 0
    checked: int = 0

    @property
    def all_checked(self) -> bool:
        return self.total > 0 and self.checked == self.total

    def label(self) -> str:
        return "none" if self.total == 0 else f"{self.checked}/{self.total}"


def acceptance_from_body(body: str) -> Acceptance:
    """Count the issue body's task-list items. Code spans/fences do not count.

    `strict=True` on the strip: a QUOTED checklist (`- [ ]` inside a code block,
    e.g. a template being discussed) is not this issue's acceptance.
    """
    text = _strip_inline_code(strip_code(body or "", True), strict=True)
    boxes = _TASK_ITEM_RE.findall(text)
    return Acceptance(total=len(boxes),
                      checked=sum(1 for b in boxes if b.lower() == "x"))


@dataclass(frozen=True)
class IssueRecord:
    """The issue's own state — the half of the link this module never read."""

    number: int
    state: str = ""
    body: str = ""

    @property
    def open(self) -> bool:
        return self.state.upper() == "OPEN"

    @property
    def acceptance(self) -> Acceptance:
        return acceptance_from_body(self.body)


@dataclass(frozen=True)
class MergedPR:
    """A MERGED pull request, with the two fields the link needs.

    Separate from `PullRequest` on purpose: that record documents itself as "the
    four fields the question needs" for the stack question, and adding a merge
    sha to it would have to be fetched on every `--pr`/`--sweep` run that does use
    it. Here `merge_sha` and `closing_issues` are the question.
    """

    number: int
    base: str
    body: str = ""
    merge_sha: str = ""
    merged_at: str | None = None
    closing_issues: tuple[int, ...] = ()

    @property
    def merged(self) -> bool:
        return bool(self.merged_at) or bool(self.merge_sha)

    def on_default(self, default_branch: str) -> bool:
        return bool(default_branch) and self.base == default_branch

    def merged_into_default(self, default_branch: str) -> bool:
        """MERGED **and** into the default branch — the only state a reason may
        rest on.

        Both halves are load-bearing, and neither is decoration. A keyword on an
        OPEN PR has not failed to fire, it has not fired YET; calling that
        `closes-did-not-fire` would tell a reader the merge happened and lost the
        closure. And a `Closes` on an unmerged sibling base is the *live* form of
        #10597 — a warning about something still fixable — not this mode's claim
        about a merge that already happened.
        """
        return self.merged and self.on_default(default_branch)


#: The reasons a link carries, so the fleet's comment can be worded from the
#: reason instead of collapsing four unrelated situations into `partial`.
REASON_CLOSURE = "closure-record"      # done: GitHub's own closure link fired
REASON_CLOSES_DID_NOT_FIRE = "closes-did-not-fire"
REASON_REFS_ONLY = "refs-only"
REASON_MENTION_ONLY = "mention-only"
REASON_CLOSES_NON_DEFAULT = "closes-on-non-default-base"
REASON_UNMEASURED_TIMELINE = "unmeasured-timeline"
REASON_REOPENED = "reopened"
REASON_NO_MERGE_REF = "no-merge-ref-in-window"

MERGE_LINK_VERDICTS = ("done", "partial", "not-done")


@dataclass(frozen=True)
class MergeLink:
    """One issue's link to the merges that touched it, and the verdict."""

    issue: int
    verdict: str
    reason: str
    pr: int = 0
    sha: str = ""
    acceptance: Acceptance = Acceptance()

    def line(self) -> str:
        """The fleet-readable contract line (see the module header).

        Two kinds because a `not-done` issue has NO merge: printing a sha and a
        PR number for it would assert a merge that never happened, and a
        `grep MERGED-BUT-OPEN` would then collect rows nothing backs.
        """
        if self.sha:
            return (f"MERGED-BUT-OPEN({self.sha} {self.pr} -> #{self.issue} "
                    f"verdict={self.verdict} reason={self.reason} "
                    f"acceptance={self.acceptance.label()})")
        return (f"NO-MERGE-REF(#{self.issue} verdict={self.verdict} "
                f"reason={self.reason} acceptance={self.acceptance.label()})")

    def as_dict(self) -> dict:
        return {"issue": self.issue, "verdict": self.verdict,
                "reason": self.reason, "pr": self.pr, "sha": self.sha,
                "acceptance_total": self.acceptance.total,
                "acceptance_checked": self.acceptance.checked}


@dataclass(frozen=True)
class Timeline:
    """What the issue's own timeline says: what referenced it, and whether a
    HUMAN REOPENED it.

    The reopen half is the one that protects a `done`, and it is not a nicety.
    Measured on this repository (2026-11-03, 1000 merged PRs back to 2026-10-12):
    exactly **3** of the 43 open P0/P1 issues appear in a merged PR's
    `closingIssuesReferences` — `#4282 <- PR #8174` (merged 2026-10-12T00:31:20Z,
    reopened 13:21:12Z), `#9548 <- PR #9585` (merged 2026-10-23T23:58:07Z,
    reopened 2026-10-24T12:12:06Z) and `#10000 <- PR #10011` (merged
    2026-10-26T02:11:20Z, reopened 02:48:58Z). All three are OPEN. The keyword
    fired and the issue closed; a human then reopened it. Reporting `done` from
    the closure record alone would close those three over the top of that
    judgement — the single most expensive thing this tool could do, and the
    reason `done` is not allowed to rest on the record by itself.

    (`reopened_after` is deliberately conservative: a reopen with no comparable
    merge timestamp counts as AFTER.)
    """

    refs: tuple[MergedPR, ...] = ()
    last_reopened_at: str | None = None

    def reopened_after(self, stamp: str | None) -> bool:
        if not self.last_reopened_at:
            return False
        if not stamp:
            return True
        return self.last_reopened_at > stamp


def judge_issue(issue: IssueRecord, prs: list[MergedPR], *,
                default_branch: str, repo: str | None = None,
                timeline: Timeline | None = None) -> MergeLink:
    """Judge ONE open issue against the merges that touched it.

    Order is FIRST-MATCH-WINS and worst-last, and every step is evidence the tool
    can point at — never an inference:

    1. `done`     — GitHub's OWN closure record (`closingIssuesReferences`) on a
                    PR merged into the default branch, AND the timeline shows no
                    reopen after it. Nothing else may buy it.
    2. `partial`  — a merged-on-default PR names the issue in its body, or the
                    issue's timeline records a reference from such a PR.
    3. `not-done` — nothing above, AND the timeline was actually read. The guard
                    lives HERE rather than only in `judge_population`, so a
                    direct caller cannot footgun it either: `timeline=None` means
                    "not measured", and an unmeasured absence degrades to the
                    weaker `partial`/`unmeasured-timeline`. Only a READ timeline
                    (`Timeline(refs=())`, i.e. read and found nothing) supports
                    `not-done` — and the same rule protects `done`, because a
                    reopen can only be ruled out once the timeline was read.
    """
    acceptance = issue.acceptance

    def link(verdict: str, reason: str, pr: MergedPR | None) -> MergeLink:
        return MergeLink(
            issue=issue.number, verdict=verdict, reason=reason,
            pr=pr.number if pr else 0,
            sha=pr.merge_sha if pr else "",
            acceptance=acceptance)

    # Newest merge first, so the cited sha is the most recent evidence rather
    # than whichever PR GitHub happened to list first. `merged_at` is
    # ISO-8601, so a plain string sort is chronological.
    ordered = sorted(prs, key=lambda p: p.merged_at or "", reverse=True)

    # 1 — GitHub's own closure record. Only a DEFAULT-branch merge can carry one
    #     (`closingIssuesReferences` is empty on a sibling base, by definition) —
    #     and only the TIMELINE can say whether a human then reopened the issue.
    for pr in ordered:
        if not (pr.merged_into_default(default_branch)
                and issue.number in pr.closing_issues):
            continue
        if timeline is None:
            # The record is GitHub's, but "was this reopened?" cannot be answered
            # from a timeline nobody read — so the stronger verdict is withheld.
            return link("partial", REASON_UNMEASURED_TIMELINE, pr)
        if timeline.reopened_after(pr.merged_at):
            return link("partial", REASON_REOPENED, pr)
        return link("done", REASON_CLOSURE, pr)

    # 2 — a claim that did not fire, a claim stranded on a sibling base, a
    #     `Refs`-only body, or a timeline mention — strongest first. STRICT for
    #     `Closes` (a lenient-only claim cannot even be shown to be a claim),
    #     lenient for `Refs` (that class exists to catch what the closing grammar
    #     cannot see, so under-reporting it is the harmful direction here).
    #
    #     `closes-on-non-default-base` is not a courtesy rank. It is #10597's
    #     measured shape: PR #10568 carried `Closes #10541` and merged into
    #     `act-10465`, so the keyword was ignored and the work only rode to main
    #     through a carrier that named a DIFFERENT issue. Reporting that as a bare
    #     `mention-only` would hide the mechanism `closes_landing` exists to name.
    rank_of: dict[str, int] = {
        REASON_CLOSES_DID_NOT_FIRE: 4,
        REASON_CLOSES_NON_DEFAULT: 3,
        REASON_REFS_ONLY: 2,
        REASON_MENTION_ONLY: 1,
    }
    best: tuple[int, str, MergedPR] | None = None
    for pr in ordered:
        if not pr.merged:
            continue          # an unmerged PR cannot have failed to close anything
        strict_closes = issue.number in parse_closing_refs(pr.body, repo,
                                                           strict=True)
        refs = issue.number in parse_reference_refs(pr.body, repo)
        on_default = pr.on_default(default_branch)
        if strict_closes and on_default:
            tier: str | None = REASON_CLOSES_DID_NOT_FIRE
        elif strict_closes:
            tier = REASON_CLOSES_NON_DEFAULT
        elif refs and on_default:
            tier = REASON_REFS_ONLY
        else:
            tier = None
        if tier is None:
            continue
        score = rank_of[tier]
        if best is None or score > best[0]:
            best = (score, tier, pr)
    if best is not None:
        return link("partial", best[1], best[2])

    if timeline is None:
        # NOT MEASURED. `not-done` is a claim about an ABSENCE, and this is the
        # only place the absence could have come from.
        return link("partial", REASON_UNMEASURED_TIMELINE, None)

    for pr in reversed(list(timeline.refs)):
        if pr.merged_into_default(default_branch):
            return link("partial", REASON_MENTION_ONLY, pr)

    return link("not-done", REASON_NO_MERGE_REF, None)


def judge_population(issues: Iterable[IssueRecord], prs: list[MergedPR], *,
                     default_branch: str, repo: str | None = None,
                     timelines: dict[int, Timeline] | None = None,
                     ) -> list[MergeLink]:
    """Judge the whole population, and never emit `not-done` from an unread one.

    `timelines` maps an issue to what its own timeline said. An issue MISSING
    from that mapping had no timeline read, so its row degrades to
    `partial`/`unmeasured-timeline` — `judge_issue` enforces that, and this
    function's only job is to pass `None` (not an empty `Timeline`) for an issue
    it has no measurement for. That distinction IS the guard: an empty `Timeline`
    says "read it, found nothing", `None` says "did not read it" — and both
    `done` and `not-done` depend on it.
    """
    links: list[MergeLink] = []
    for issue in issues:
        tl = timelines.get(issue.number) if timelines is not None else None
        links.append(judge_issue(issue, prs, default_branch=default_branch,
                                 repo=repo, timeline=tl))
    return links


def merge_link_worst(links: list[MergeLink]) -> str:
    """The single verdict for a set of links, worst-first — its OWN vocabulary.

    `worst_verdict` is deliberately not reused: it iterates the stack questions'
    tokens and would return `"none"` for every `partial`/`not-done` link, i.e. a
    false PASS on the whole report (plan review, finding 7).
    """
    for verdict in ("not-done", "partial", "done"):
        if any(link.verdict == verdict for link in links):
            return verdict
    return "none"


# ── live layer: `gh`, no writes, each call self-describing ────────────────────

class Unmeasurable(RuntimeError):
    """The question could not be answered. Never degraded into a pass."""


_GH_FIELDS = "number,baseRefName,body,headRefName,state,mergedAt"

#: The MERGED-BUT-OPEN field set. `mergeCommit` gives the sha a report line must
#: cite, and `closingIssuesReferences` is GitHub's own closure record — both come
#: back in the SAME `gh pr list` call as the bodies, so a 200-PR window costs one
#: request rather than 200. `mergeCommit` is null on an open PR, which is why the
#: record treats `merged_at` as the primary signal.
_MERGED_GH_FIELDS = (
    "number,baseRefName,body,state,mergedAt,mergeCommit,closingIssuesReferences"
)

#: The issue field set the link needs — the issue's OWN state, which no arm above
#: has ever read.
_ISSUE_GH_FIELDS = "number,state,body"

#: A `gh` call that never returns must not hang the run (#10634 review P2). This
#: tool runs inside a small advisory CI job (`.github/workflows/
#: closes-coherence.yml`), and a job-level timeout kills the WHOLE job —
#: `continue-on-error: true` covers a step's exit status, not a step that never
#: finishes — so an unreachable API would take the rest of that job down instead
#: of reporting UNMEASURED. Bounded here, at the one place every live call goes
#: through.
_GH_TIMEOUT_SECONDS = 60

#: …and the bound is PER CALL, which is not enough for the run (#10634 review P2):
#: one `--pr` run issues `2 + L` sequential calls (identity, the PR, one per base
#: branch up the stack) and `collect_stack` follows the stack as far as it goes,
#: so `L` is not capped by `MAX_STACK_DEPTH` — thirteen calls at the per-call cap
#: are 780s, and the ONLY workflow that runs this tool,
#: `.github/workflows/closes-coherence.yml`, caps its job at `timeout-minutes: 10`
#: (600s). So there is also an aggregate budget: once it is gone the run reports
#: UNMEASURED instead of spending the job, which is the same contract the
#: per-call bound has (exit 2, never a pass, never a red the advisory step
#: cannot cover).
_GH_RUN_BUDGET_SECONDS = 300

_run_budget_started: float | None = None


def _reset_run_budget() -> None:
    """Start a fresh aggregate deadline (called by `main`, and by tests)."""
    global _run_budget_started
    _run_budget_started = None


def _budget_remaining() -> float:
    """Seconds left in the run's budget; the first call starts the clock."""
    global _run_budget_started
    now = time.monotonic()
    if _run_budget_started is None:
        _run_budget_started = now
    return _GH_RUN_BUDGET_SECONDS - (now - _run_budget_started)


def _gh(args: list[str]) -> str:
    budget = _budget_remaining()
    if budget <= 0:
        # The run has already spent its whole budget: no further call can
        # change the answer, and making one would spend the job instead.
        raise Unmeasurable(
            f"the run exceeded its {_GH_RUN_BUDGET_SECONDS}s budget before "
            f"`gh {' '.join(args)}` could be asked")
    timeout = min(_GH_TIMEOUT_SECONDS, budget)
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True,
                             check=False, timeout=timeout)
    except FileNotFoundError as exc:
        raise Unmeasurable(f"`gh` is not installed: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        # UNMEASURED, never a pass and never a hang: the caller's exit 2 is the
        # contract the workflow's dead-man reads.
        raise Unmeasurable(
            f"`gh {' '.join(args)}` did not answer within {timeout:.0f}s"
            f"{' (the run\'s remaining budget)' if timeout < _GH_TIMEOUT_SECONDS else ''}"
        ) from exc
    if out.returncode != 0:
        raise Unmeasurable(
            f"`gh {' '.join(args)}` exited {out.returncode}: "
            f"{(out.stderr or out.stdout).strip()[:400]}")
    return out.stdout


def _to_pr(raw: dict) -> PullRequest:
    return PullRequest(
        number=int(raw["number"]),
        base=raw.get("baseRefName") or "",
        body=raw.get("body") or "",
        head=raw.get("headRefName") or "",
        state=raw.get("state") or "OPEN",
        merged_at=raw.get("mergedAt"),
    )


def fetch_identity(repo: str | None) -> tuple[str, str]:
    """(default branch, ``owner/name``) in ONE read.

    The second half is not decoration: when `--repo` is omitted the caller has no
    repo string, and `parse_closing_refs` treats every `owner/repo#N` as foreign
    without one — so a same-repo QUALIFIED claim was silently dropped and the
    sweep reported "nothing to report" over a real offender (review cycle 2,
    finding 5). `gh repo view` already knows the name; this keeps it.

    `gh repo view` takes the repo POSITIONALLY (it has no `--repo` flag), which
    also stops the read answering for a selector it was not asked about.
    """
    raw = _gh(["repo", "view", *([repo] if repo else []), "--json",
               "defaultBranchRef,nameWithOwner"])
    try:
        payload = json.loads(raw)
        name = payload["defaultBranchRef"]["name"]
        slug = payload.get("nameWithOwner") or ""
    except (KeyError, TypeError, ValueError) as exc:
        raise Unmeasurable(f"the repo identity could not be read: {raw[:200]}") from exc
    if not name:
        raise Unmeasurable("the repo reported an empty default branch name")
    # An EMPTY slug must not degrade quietly into `None`: that would drop every
    # same-repo qualified claim as foreign, with no signal — the exact false pass
    # this read exists to remove, and the module's doctrine is Unmeasurable, never
    # a quiet pass (review c3-5).
    if not slug:
        raise Unmeasurable("the repo reported no nameWithOwner")
    return name, slug


def fetch_pr(number: int, repo: str | None) -> PullRequest:
    repo_args = ["--repo", repo] if repo else []
    raw = _gh(["pr", "view", str(number), *repo_args, "--json", _GH_FIELDS])
    try:
        return _to_pr(json.loads(raw))
    except (KeyError, TypeError, ValueError) as exc:
        raise Unmeasurable(f"PR #{number} could not be read: {raw[:200]}") from exc


def fetch_prs_by_head(branch: str, repo: str | None) -> list[PullRequest]:
    """Every PR whose HEAD is `branch` — the candidates for a stack carrier."""
    repo_args = ["--repo", repo] if repo else []
    raw = _gh(["pr", "list", *repo_args, "--state", "all", "--head", branch,
               "--json", _GH_FIELDS, "--limit", "50"])
    try:
        return [_to_pr(r) for r in json.loads(raw)]
    except (KeyError, TypeError, ValueError) as exc:
        raise Unmeasurable(
            f"the PRs with head `{branch}` could not be read: {raw[:200]}") from exc


def _pick_carrier(cands: list[PullRequest]) -> PullRequest | None:
    """The carrier candidate for one base branch.

    OPEN outranks MERGED — an open carrier can still be amended with the
    keyword and is the one that will land next — and a CLOSED-WITHOUT-MERGING
    PR ranks LAST, not first. `not pr.merged` was the original test and it was
    wrong: `merged` is False for a CLOSED PR, so a closed-and-abandoned PR was
    picked ahead of a real carrier and `classify` then reported `lands` over an
    issue nothing would ever close (#10597 review P0-1).
    """
    if not cands:
        return None
    for pr in cands:
        if pr.state.upper() == "OPEN":
            return pr
    for pr in cands:
        if pr.merged:
            return pr
    return cands[0]  # closed-unmerged: `classify` reports it as `unresolved`


def collect_stack(roots: list[PullRequest], repo: str | None,
                  default_branch: str) -> dict[str, PullRequest]:
    """Resolve the stack above each root, fetching each base branch's PR once."""
    by_head: dict[str, PullRequest] = {}
    pending: list[str] = []
    for pr in roots:
        if pr.head:
            by_head.setdefault(pr.head, pr)
        if pr.base and pr.base != default_branch:
            pending.append(pr.base)
    resolved: set[str] = set()
    while pending:
        branch = pending.pop()
        if branch in resolved or branch in by_head:
            continue
        resolved.add(branch)
        pr = _pick_carrier(fetch_prs_by_head(branch, repo))
        if pr is None:
            continue
        by_head[branch] = pr
        if pr.base and pr.base != default_branch and pr.base not in resolved:
            pending.append(pr.base)
    return by_head


def sweep(repo: str | None, default_branch: str) -> list[Finding]:
    """Every OPEN PR whose base is not the default branch, classified.

    This is the population #10597 was measured in: the offending PR is visible
    here for as long as it is open — which is the whole window in which the
    keyword can still be fixed.
    """
    repo_args = ["--repo", repo] if repo else []
    raw = _gh(["pr", "list", *repo_args, "--state", "open", "--limit", "1000",
               "--json", _GH_FIELDS])
    try:
        prs = [_to_pr(r) for r in json.loads(raw)]
    except (KeyError, TypeError, ValueError) as exc:
        raise Unmeasurable(f"the open PRs could not be read: {raw[:200]}") from exc
    roots = [p for p in prs if p.base and p.base != default_branch
             and parse_closing_refs(p.body, repo)]
    if not roots:
        return []
    by_head = collect_stack(roots, repo, default_branch)
    findings: list[Finding] = []
    for pr in roots:
        findings.extend(classify(pr, by_head, default_branch, repo))
    return findings


# ── MERGED-BUT-OPEN live layer (#10698): reads only ───────────────────────────


def _ref_slug(node: dict) -> str:
    """The `owner/name` a GitHub ref names, from any of the shapes it appears in.

    ⛔ There are FOUR, and a guard that reads only the first two is INERT. Measured
    on this box against the live API (2026-11-03):

        gh pr view 8174 --json closingIssuesReferences
        {"number":4282,
         "repository":{"id":"R_kgDOToroqw","name":"tortoise",
                       "owner":{"id":"MDQ6VXNlcjgxNTYwNDkx","login":"daniel-ospina"}},
         "url":"https://github.com/daniel-ospina/tortoise/issues/4282"}

    — no `nameWithOwner`, no `repository_url`. A first version of this function
    read exactly those two, fell through to "assume local", and a unit test built
    from a shape production never produces passed while the guard did nothing at
    all. That is why the shapes are enumerated here rather than guessed.
    """
    repo = node.get("repository") or {}
    slug = repo.get("nameWithOwner") or ""
    if slug:
        return str(slug)
    owner = (repo.get("owner") or {}).get("login") or ""
    name = repo.get("name") or ""
    if owner and name:
        return f"{owner}/{name}"
    # `repository_url` is the REST field; `url` is what `gh pr view --json
    # closingIssuesReferences` emits (a github.com URL ending `/issues/<n>`).
    for url in (node.get("repository_url") or "", node.get("url") or ""):
        for marker in ("/repos/", "github.com/"):
            if marker not in url:
                continue
            tail = url.split(marker, 1)[1].strip("/")
            if "/issues/" in tail:
                tail = tail.split("/issues/", 1)[0]
            elif "/pull/" in tail:
                tail = tail.split("/pull/", 1)[0]
            parts = tail.split("/")
            if len(parts) >= 2 and parts[0] and parts[1]:
                return f"{parts[0]}/{parts[1]}"
    return ""


def ref_is_local(node: dict | None, repo: str | None) -> bool:
    """Is this GitHub ref about OUR repository?

    ⛔ `#4282` in another repository is not `#4282` here, and the closing/linking
    arms are the wrong place to learn that. `closingIssuesReferences` and a
    timeline `cross-referenced` source both carry the owning repository, and a PR
    body may legitimately say `Closes other/repo#123` — so a number compared
    without its owner mints a `done` (the precondition for CLOSING an issue) for
    a local issue that a foreign commit merely shares a number with. The body
    parsers in this module already scope by slug (`parse_reference_refs`); this
    is the same rule for the record-based arms.

    When `repo` is unknown there is nothing to compare against, so the ref is
    taken as local: that is the module's default behaviour everywhere else, and
    guessing "foreign" would turn every real closure into a `partial`.

    ⛔ Residual, stated rather than glossed: a ref that names no repository at all
    is treated as FOREIGN (see the body). `gh` always sends one — `repository` in
    the GraphQL shape, `repository_url` in the REST shape — so this fires only on
    a payload this code has never seen, and it fails in the direction that leaves
    an issue open.
    """
    if not repo:
        return True
    if not isinstance(node, dict):
        return False
    slug = _ref_slug(node)
    if not slug:
        # NO identification at all. Fail CLOSED: this arm decides whether `done`
        # may be claimed, and `done` is the precondition for closing an issue —
        # so an unattributable reference costs a `partial` (the issue stays
        # open), never a `done`. The module's own doctrine: a wrong close is
        # worse than an open issue.
        return False
    return slug.lower() == repo.lower()


def _to_merged_pr(raw: dict, repo: str | None = None) -> MergedPR:
    commit = raw.get("mergeCommit") or {}
    refs = raw.get("closingIssuesReferences") or []
    return MergedPR(
        number=int(raw["number"]),
        base=raw.get("baseRefName") or "",
        body=raw.get("body") or "",
        merge_sha=(commit.get("oid") or "") if isinstance(commit, dict) else "",
        merged_at=raw.get("mergedAt"),
        closing_issues=tuple(
            int(r["number"]) for r in refs
            if isinstance(r, dict) and r.get("number") is not None
            and ref_is_local(r, repo)),
    )


def fetch_merged_prs(repo: str | None, since_hours: int,
                     limit: int = 200) -> list[MergedPR]:
    """Merged PRs in a MERGE-TIME window, paged by day.

    ⛔ The window is pinned with `--search "merged:<start>..<end>"` on purpose.
    `gh pr list --state merged` orders by `createdAt`, NOT by merge time —
    measured at #10698: #10624 (created 18:52) merged at 07:12, i.e. the NEWEST
    merge, and it prints fifth behind PRs merged hours earlier. A client-side
    `mergedAt` filter over that list would sample created-order and silently drop
    a PR merged an hour ago because it was opened a week ago.

    ⛔ …and it is paged by DAY, not fetched as one range, because this one call
    carries every body in the window and its cost is super-linear in the row
    count: 200 rows with the full field set measured 1.6 s, while 157 rows in a
    seven-day window blew past the 60 s per-call bound on the same API (measured
    twice, on an idle token). Seven day-sized pages cost the same total bytes but
    never put a week of bodies in one response — so a week-long census stops being
    the thing that reports UNMEASURED.

    A page that comes back FULL is UNMEASURABLE: a truncated page cannot tell
    "nothing references this issue" from "the reference is in the part I did not
    read".
    """
    now = datetime.now(UTC)
    since = now - timedelta(hours=since_hours)
    repo_args = ["--repo", repo] if repo else []
    found: dict[int, MergedPR] = {}
    cursor = now
    while cursor > since:
        chunk_start = max(since, cursor - timedelta(days=1))
        query = (f"merged:{chunk_start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
                 f"..{cursor.strftime('%Y-%m-%dT%H:%M:%SZ')} sort:updated-desc")
        raw = _gh(["pr", "list", *repo_args, "--state", "merged",
                   "--search", query, "--limit", str(limit),
                   "--json", _MERGED_GH_FIELDS])
        try:
            rows = json.loads(raw)
            if not isinstance(rows, list):
                raise TypeError("not a JSON array")
            page = [_to_merged_pr(r, repo) for r in rows]
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            # A 200 carrying `{"message": "Bad credentials"}` is the same failure
            # as a non-zero exit, and must land on the documented exit 2 rather
            # than escaping `main()` as a traceback (exit 1).
            raise Unmeasurable(
                f"the merged PRs could not be read: {raw[:200]}") from exc
        if len(rows) >= limit:
            raise Unmeasurable(
                f"one day-sized page is FULL ({limit} rows for {query}) — a "
                f"truncated page would report `no-merge-ref` over merges it never "
                f"read; narrow --since-hours")
        for pr in page:
            found[pr.number] = pr
        cursor = chunk_start
    return sorted(found.values(), key=lambda pr: pr.merged_at or "")


def fetch_open_issues(labels: Iterable[str], repo: str | None,
                      limit: int = 500) -> list[IssueRecord]:
    """The open-issue population, one call per label.

    Two calls for the P0/P1 census, never 43: the bodies arrive with the list.
    """
    repo_args = ["--repo", repo] if repo else []
    found: dict[int, IssueRecord] = {}
    for label in labels:
        raw = _gh(["issue", "list", *repo_args, "--state", "open",
                   "--label", label, "--limit", str(limit),
                   "--json", _ISSUE_GH_FIELDS])
        try:
            rows = json.loads(raw)
            if not isinstance(rows, list):
                raise TypeError("not a JSON array")
            for r in rows:
                int(r["number"])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise Unmeasurable(
                f"the open issues labelled {label!r} could not be read: "
                f"{raw[:200]}") from exc
        if len(rows) >= limit:
            raise Unmeasurable(
                f"the open-issue population for {label!r} is FULL ({limit} rows) "
                f"— a truncated population is not a census")
        for r in rows:
            n = int(r["number"])
            found.setdefault(n, IssueRecord(
                number=n, state=r.get("state") or "",
                body=r.get("body") or ""))
    return [found[n] for n in sorted(found)]


def fetch_timeline(issue: int, slug: str,
                   prs_by_number: dict[int, MergedPR]) -> Timeline:
    """The merged PRs this issue's OWN timeline records a reference from.

    Two event kinds, and both are the SOURCE rather than a guess:

    * `cross-referenced` — a PR or issue that linked this one. `source.issue.number`
      identifies it directly.
    * `referenced` — a COMMIT that mentions this one, carrying only `commit_id`.
      This is the documented path by which a closing keyword in a **squash or
      merge commit message** closes an issue (GitHub: "You can also use closing
      keywords in a commit message. The issue will be closed when you merge the
      commit into the default branch, but the pull request that contains the
      commit will not be listed as a linked pull request") — invisible to any
      body-only parse. It is attributed here by matching `commit_id` against the
      PR's own `merge_commit_sha`, which IS that commit for a squash merge and for
      a merge commit.

    ⚠️ Residual, stated rather than glossed: a `referenced` event naming an
    arbitrary BRANCH commit (not the merge commit) is not attributed — that would
    need a commit→PR lookup per event. Such a mention is missed, which costs a
    `mention-only` (reported as `no-merge-ref-in-window` instead); it never
    manufactures a claim.

    It also returns the LAST `reopened` timestamp, because that is the only
    evidence that can tell a fired keyword from a closure a human rejected — see
    `Timeline`.

    An unreadable timeline is UNMEASURABLE, never "no references": that absence is
    what a `not-done` verdict rests on.
    """
    if not slug:
        raise Unmeasurable(
            "the reference timeline needs an `owner/name` slug and none was "
            "resolved — `not-done` would rest on an unread timeline")
    # `--paginate --slurp` so a busy issue (one measured issue carried 63
    # `referenced` + 27 `cross-referenced` events) is read to the end instead of
    # its first page.
    raw = _gh(["api", "--paginate", "--slurp",
               f"repos/{slug}/issues/{issue}/timeline?per_page=100"])
    try:
        pages = json.loads(raw)
    except (KeyError, TypeError, ValueError) as exc:
        raise Unmeasurable(
            f"the timeline of #{issue} could not be read: {raw[:200]}") from exc
    if not isinstance(pages, list) or any(not isinstance(page, list)
                                         for page in pages):
        # FAIL CLOSED. An empty `Timeline` means "read to the end, found
        # nothing" — which is exactly the state `not-done` rests on — so a body
        # this code cannot parse must never collapse into one. `_gh` already
        # raises on a non-zero exit, so this arm is reachable only on a 200 with
        # an unexpected shape (a proxy, a `gh` shim, a non-`--slurp` response):
        # rare, and the one place where "I do not know" would otherwise resolve
        # to "nothing is there".
        raise Unmeasurable(
            f"the timeline of #{issue} did not come back as an array of pages: "
            f"{raw[:200]} — an unreadable timeline is UNMEASURED, never 'no "
            f"references', because an empty one is what `not-done` rests on")
    events = [e for page in pages for e in page]
    by_sha = {pr.merge_sha: pr for pr in prs_by_number.values() if pr.merge_sha}
    seen: list[MergedPR] = []
    last_reopened: str | None = None
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("event")
        if kind == "reopened":
            stamp = event.get("created_at")
            if isinstance(stamp, str) and (last_reopened is None
                                           or stamp > last_reopened):
                last_reopened = stamp
            continue
        if kind not in ("referenced", "cross-referenced"):
            continue
        source = (event.get("source") or {}).get("issue") or {}
        number = source.get("number")
        pr = (prs_by_number.get(int(number))
              if number is not None and ref_is_local(source, slug) else None)
        if pr is None:
            sha = event.get("commit_id")
            pr = by_sha.get(sha) if isinstance(sha, str) else None
        if pr is not None and pr not in seen:
            seen.append(pr)
    return Timeline(refs=tuple(seen), last_reopened_at=last_reopened)


def fetch_issues_by_number(numbers: Iterable[int],
                           repo: str | None) -> list[IssueRecord]:
    """Read a specific set of issues — used when no label selects the population.

    One call per issue, so the caller must bound the set. That is fine here: the
    set is what the merged PRs themselves named, which is the population the
    question is about.
    """
    repo_args = ["--repo", repo] if repo else []
    out: list[IssueRecord] = []
    for n in sorted(set(numbers)):
        raw = _gh(["issue", "view", str(n), *repo_args,
                   "--json", _ISSUE_GH_FIELDS])
        try:
            r = json.loads(raw)
            if not isinstance(r, dict):
                raise TypeError("not a JSON object")
            int(r["number"])
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise Unmeasurable(
                f"issue #{n} could not be read: {raw[:200]}") from exc
        out.append(IssueRecord(number=int(r["number"]),
                               state=r.get("state") or "",
                               body=r.get("body") or ""))
    return out


def merged_but_open(repo: str | None, default_branch: str, *,
                    labels: Iterable[str] = (), since_hours: int,
                    prs: list[MergedPR] | None = None,
                    timelines: bool = True) -> list[MergeLink]:
    """The MERGED-BUT-OPEN census over a population of OPEN issues.

    The timeline is read for every issue whose verdict can still depend on it —
    that is, every issue the bodies did NOT already settle — because `not-done`
    is a claim about an ABSENCE and an unread timeline cannot support one, and a
    reopen can only be ruled out from a timeline that was read.

    Population, two ways:
      * labels given  — the labelled OPEN issues (a census: unreferenced issues
                        are `not-done` rows, which is the point);
      * no labels     — the OPEN issues a merged PR itself named. A `not-done`
                        row about an issue nothing ever referenced is noise here,
                        and there is no label to bound `gh issue list`.
    """
    slug = repo or ""
    labels = list(labels)
    if prs is None:
        prs = fetch_merged_prs(repo, since_hours)
    if not prs:
        raise Unmeasurable(
            f"no merged PRs were returned for the last {since_hours}h window — "
            f"an empty window is UNMEASURED, never a clean census")
    by_number = {pr.number: pr for pr in prs}
    if labels:
        issues = fetch_open_issues(labels, repo)
    else:
        named: set[int] = set()
        for pr in prs:
            named.update(pr.closing_issues)
            named.update(parse_closing_refs(pr.body, repo))
            named.update(parse_reference_refs(pr.body, repo))
        issues = fetch_issues_by_number(named, repo) if named else []
        issues = [i for i in issues if i.open]
    if not issues:
        # An EMPTY census is UNMEASURED, never clean — the module's own doctrine.
        # With no labels that is a legitimate "no merged PR named any open issue";
        # with labels it is almost always a typo (`--label P9-nonsense`), and
        # exiting 0 on it would report a clean sweep over nothing at all.
        if labels:
            raise Unmeasurable(
                f"the open-issue population is EMPTY for labels={labels} in a "
                f"{since_hours}h window — an empty census is UNMEASURED, never "
                f"clean; check the label names")
        return []
    if not timelines:
        # No timeline read ⇒ no `done` and no `not-done` (neither a reopen nor an
        # absence can be ruled out on a timeline nobody read);
        # `judge_population` degrades every such row to `partial`.
        return judge_population(issues, prs, default_branch=default_branch,
                                repo=repo, timelines=None)

    # The timeline is read ONLY for the issues it can still change the answer for:
    # the ones a body-based arm already resolves are `partial` either way. Two
    # classes still need it — a `done` candidate (only the timeline can rule out a
    # reopen) and the `not-done` boundary (an absence must be measured). Reading
    # all 43 cost 2m14s for a 48 h window (measured), most of the module's 300 s
    # budget spent answering a question the bodies had already settled — and an
    # UNMEASURED run is the one outcome a drift census cannot afford.
    pre = judge_population(issues, prs, default_branch=default_branch,
                           repo=repo, timelines=None)
    need = [link.issue for link in pre if link.reason == "unmeasured-timeline"]
    linked: dict[int, Timeline] = {}
    for number in need:
        linked[number] = fetch_timeline(number, slug, by_number)
    return judge_population(issues, prs, default_branch=default_branch,
                            repo=repo, timelines=linked)


def _run_merged_but_open(ap: argparse.ArgumentParser,
                         args: argparse.Namespace) -> int:
    """The `--merged-but-open` mode (#10698): its own fetch, verdict and exit map.

    Deliberately NOT sharing `worst_verdict`: that function iterates the stack
    vocabulary (`inert`/`unresolved`/`lands`) and returns `none` for every
    `partial`/`not-done` link, which would exit 0 on a report that is entirely
    drift — a false PASS wearing the other mode's contract.

    The aggregate `gh` budget is shared with the stack mode (module state), so it
    is reset here too: a census is a fresh question, and inheriting a spent clock
    from something that ran earlier in the same process would be UNMEASURED for a
    reason the reader cannot see.
    """
    _reset_run_budget()
    try:
        # When the caller names BOTH the repo and the default branch, nothing is
        # left to measure — and `fetch_identity` costs a `gh repo view`, which is
        # a GraphQL round trip that has been measured at >60 s under fleet load
        # (it then reports UNMEASURED over a question the arguments already
        # answered). Supplying both is the documented way to spend no budget on
        # identity.
        if args.repo and args.default_branch:
            default_branch, slug = args.default_branch, args.repo
        else:
            measured_default, slug = fetch_identity(args.repo)
            default_branch = args.default_branch or measured_default
        links = merged_but_open(args.repo or slug or None, default_branch,
                                labels=args.label, since_hours=args.since_hours,
                                timelines=not args.no_timeline)
    except Unmeasurable as exc:
        print(f"closes_landing: UNMEASURED — {exc}", file=sys.stderr)
        return 2

    verdict = merge_link_worst(links)
    counts = {v: sum(1 for link in links if link.verdict == v)
              for v in MERGE_LINK_VERDICTS}
    if args.json:
        print(json.dumps({"default_branch": default_branch,
                          "since_hours": args.since_hours,
                          "population": len(links), "verdict": verdict,
                          "counts": counts,
                          "links": [link.as_dict() for link in links]}, indent=2))
    elif args.annotate:
        for link in links:
            if link.verdict != "done":
                msg = link.line().replace("%", "%25").replace("\r", "").replace(
                    "\n", "%0A")
                print(f"::warning title={link.verdict} #{link.issue}::{msg}")
    else:
        # The WHOLE census, `done` rows included: the line format carries
        # `verdict=done`, so a reader must be able to grep it. `--annotate` stays
        # drift-only — an annotation is a warning, and "this one is fine" is not.
        for link in links:
            print(link.line())
        # The summary goes to stdout so a reader who greps only the lines still
        # sees the denominator — "nothing printed" must never be mistakable for
        # "nothing measured".
        print(f"closes_landing: population={len(links)} "
              f"done={counts['done']} partial={counts['partial']} "
              f"not-done={counts['not-done']} verdict={verdict} "
              f"window={args.since_hours}h")

    return 3 if verdict in ("partial", "not-done") else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Report `Closes #N` refs that will never fire because the PR's "
                    "base is not the default branch (#10597).")
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--pr", type=int, help="classify one PR by number")
    which.add_argument("--sweep", action="store_true",
                       help="classify every open PR on a non-default base")
    which.add_argument("--body-file", type=Path,
                       help="offline: read the PR body from a file")
    which.add_argument("--merged-but-open", action="store_true",
                       help="(#10698) for every open issue a MERGED PR touched, "
                            "report whether anything closed it: "
                            "done | partial | not-done")
    ap.add_argument("--repo", default=None, help="owner/name (default: ambient)")
    ap.add_argument("--base", default=None,
                    help="offline/--body-file only: the PR's base branch")
    ap.add_argument("--head", default=None, help="offline/--body-file only: the PR's head branch")
    ap.add_argument("--default-branch", default=None,
                    help="the default branch (measured when omitted)")
    ap.add_argument("--carrier-body-file", type=Path, default=None,
                    help="offline/--body-file only: the body of the PR at the top of the stack")
    ap.add_argument("--annotate", action="store_true",
                    help="emit GitHub Actions ::warning lines instead of prose")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--label", action="append", default=[],
                    help="--merged-but-open only: restrict the population to this "
                         "label (repeatable, e.g. --label P0-blocker --label P1-severe)")
    ap.add_argument("--since-hours", type=int, default=48,
                    help="--merged-but-open only: the MERGE-TIME window (default 48)")
    ap.add_argument("--no-timeline", action="store_true",
                    help="--merged-but-open only: skip the per-issue timeline read. "
                         "`not-done` is then never emitted (an unread timeline "
                         "cannot support a claim of absence)")
    args = ap.parse_args(argv)
    _reset_run_budget()

    if args.merged_but_open:
        return _run_merged_but_open(ap, args)

    try:
        if args.body_file is not None:
            if not args.base:
                ap.error("--body-file needs --base (the question is meaningless without it)")
            default_branch = args.default_branch or "main"
            pr = PullRequest(number=0, base=args.base,
                             body=args.body_file.read_text(encoding="utf-8"),
                             head=args.head or "")
            by_head: dict[str, PullRequest] = {}
            # No `--carrier-body-file` means the caller knows of NO PR at the top
            # of the stack. That is `unresolved`, not `inert`: inventing an empty
            # carrier would report a fact about a PR that was never described.
            if args.base != default_branch and args.carrier_body_file:
                carrier = PullRequest(
                    number=0, base=default_branch,
                    body=args.carrier_body_file.read_text(encoding="utf-8"))
                by_head[args.base] = carrier
            findings = classify(pr, by_head, default_branch, args.repo)
        else:
            measured_default, slug = fetch_identity(args.repo)
            default_branch = args.default_branch or measured_default
            # Fall back to the repo gh just named: with no `--repo` the caller had
            # no string, and a same-repo `owner/repo#N` claim would be dropped as
            # foreign — a false pass by non-detection (review cycle 2, finding 5).
            classify_repo = args.repo or slug or None
            if args.sweep:
                findings = sweep(classify_repo, default_branch)
            else:
                pr = fetch_pr(args.pr, args.repo)
                by_head = collect_stack([pr], args.repo, default_branch)
                findings = classify(pr, by_head, default_branch, classify_repo)
    except Unmeasurable as exc:
        print(f"closes_landing: UNMEASURED — {exc}", file=sys.stderr)
        return 2

    verdict = worst_verdict(findings)
    if args.json:
        print(json.dumps({"default_branch": default_branch, "verdict": verdict,
                          "findings": [f.as_dict() for f in findings]}, indent=2))
    elif args.annotate:
        for f in findings:
            if f.verdict != "lands":
                print(f.annotation())
    else:
        if not findings:
            # NOT "on a non-default base": this branch is also reached by `--pr` on
            # a PR whose base IS the default branch, and claiming otherwise puts a
            # false statement about the PR in front of the reader.
            print(f"closes_landing: no closing refs to judge — nothing to report "
                  f"(default branch: {default_branch})")
        for f in findings:
            print(f"[{f.verdict:10s}] #{f.issue} via PR #{f.pr} (base {f.base}) "
                  f"-> carrier #{f.carrier}")
            print(f"             {f.detail}")

    return 3 if verdict in ("inert", "unresolved") else 0


if __name__ == "__main__":
    sys.exit(main())
