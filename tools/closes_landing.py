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
#: The same three block starts WITHOUT the bare `<` arm, for the §4.4/§5.1 depth CARRY. ⛔ ` {0,3}<`
#: IS NOT AN HTML-BLOCK START CONDITION (the fact round 36 recorded for `_leaves_no_paragraph`, which
#: this call site had not learned). Reading any leading `<` as "this line starts something of its own"
#: dropped the carried paragraph depth onto the LAZY line's own depth instead of the paragraph's, and
#: the marker below was measured against a top-level paragraph that does not exist — a FAIL-OPEN in the
#: `strict` PASS arm (round 50 review, P1):
#:     `> Notes` + `<span>x</span>` + `    ``` ` + `2.     Closes #100`
#:         renderer: ONE `<p>` in the blockquote (`Notes <span>x</span> ```` `), then a NEW `<ol>`
#:         whose wide-gap content is `<pre><code>` -> CODE; `origin/main` `(True,False,False)`, the
#:         round-49 head credited `[100]` in `strict`. A type-7 line is INLINE in an open paragraph and
#:         starts nothing, which is why the bare-`<` arm is wrong HERE (it stays right where
#:         `_paragraph_end` uses it).
_OWN_BLOCK_START_RE = re.compile(
    r"(?: {0,3}(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)"          # a list item marker
    r"| {0,3}#{1,6}(?:[ \t]|$)"                             # an ATX heading
    r"| {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$)"   # thematic break
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

#: A list marker and the gap after it. ⛔ THE GAP MAY BE EMPTY OR ABSENT (§5.2 allows exactly two
#: spellings: "one or more spaces or tabs" after the marker, or the END OF THE LINE) — `- `, `-`, `*`,
#: `1.` all open list items, and the bare form opens an EMPTY item whose content column is the
#: marker's width + 1. Round 49 review, P1: requiring the gap read `> -` as ordinary paragraph text,
#: so the type-7 leaf under it was refused and a real close was blanked (`> <div>` + `>` + `> -` +
#: `> <br />` + the marker). ⚠️ `[ \t]*` ALONE WOULD MATCH `**bold**` AS A BARE `*` MARKER — the
#: second alternative is end-of-line ONLY, so neither group can match into content.
_LIST_MARKER_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:[-*+]|\d{1,9}[.)])(?:(?P<gap>[ \t]+)|[ \t]*$)")


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


def _consume_blockquote_markers(line: str, depth: int) -> tuple[int, str]:
    """(markers consumed, the text LEFT after at most `depth` of them).

    The ONE consumption loop behind two questions that must agree: `_fence_line_column` measures the
    indent and raw column of what is left, and §4.6's type-6/7 end condition asks whether what is
    left is BLANK. A block that lives at `depth` ends only on a line that is blank in THAT
    container — a deeper quote's marker on the same line is the block's own literal text, not a
    blank line — so the end condition must not consume every `>` in the prefix.

    Primary source (§4.6, `spec.commonmark.org/0.31.2/spec.txt`:2409): *"It ends with the first
    subsequent line that meets a matching end condition, or the last line of the document, or the
    LAST LINE OF THE CONTAINER BLOCK containing the current HTML block"* — the container is entered
    (and its markers consumed) before the leaf block sees the line, which is why the reading is at
    the block's own depth and why a marker deeper than it is TEXT.
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
    return consumed, line[i:]


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
    consumed, remainder = _consume_blockquote_markers(line, depth)
    start = len(line) - len(remainder)
    i = start
    while i < len(line) and line[i] in " \t":
        i += 1
    return consumed, i - start, i


#: ⛔ `eq=False` — IDENTITY, NOT VALUE (round-51 review, P3, and the docstring below has always
#: claimed it): every container test in this module is `is` (`container is prev_container`,
#: `prev_container is _popped`), and the one membership test (`_blanked_opens_paragraph in
#: list_items`) used to go through the generated `__eq__`, so a POPPED item whose
#: `(depth, rest_col, raw_col, has_content)` happened to match a live one read as "still open".
#: Measured: 0 of 18,276 (body, arm) answers change, so this closes a hazard, not a defect — and it
#: is what makes "nothing hashes or compares these by value" TRUE.
@dataclass(eq=False)
class _ListItem:
    """One OPEN list item, in BOTH spaces a dedent can be measured in.

    ⛔ NOT `frozen`, AND THAT IS LOAD-BEARING: `has_content` is UPDATED in place when the first
    content line arrives, and the lane's other identity tests are `container is prev_container`
    (`_lazy_indent`, `_dropped_quote_is_lazy`) — replacing the object with `dataclasses.replace`
    would keep the FLAG right and silently break the CONTAINER comparison, which reads as "this line
    left the item" and over-blanks. Nothing hashes or compares these by value.

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
    #: ⛔ AN EMPTY ITEM IS CLOSED BY THE NEXT BLANK LINE; A NON-EMPTY ONE IS NOT (round 48 review,
    #: P1 — the `blank_carried` state bound a block to an item the renderer had already closed, which
    #: ended that block one line early and LOST a real close). `> - ` + `>` + `>   <pre>` renders the
    #: `<li>` EMPTY and the `<pre>` as a SIBLING of the list, so the `<pre>` block's container is the
    #: blockquote, not the item; binding it to the item ended the block on the next `> -` line
    #: (residual 0 < the item's 2-column content) — `origin/main` `[100]`, gated head `[]`:
    #:     `> - ` + `>` + `>   <pre>` + `>` + `> -     Closes #100`   renderer `html_block`
    #: Content is taken from the MARKER LINE and from lines that stay inside the item (`>   text`),
    #: never from a LAZY line: `> - ` + `> text` renders `<li></li><p>text</p>`, because a lazy line
    #: continues a PARAGRAPH and an empty item has none — so that item is still empty here too.
    has_content: bool = False
    #: ⛔ A MARKER LINE THAT IS A PARAGRAPH CONTINUATION OPENS NO ITEM IN THE RENDERER, so an item it
    #: pushes here is PHANTOM — and a phantom item must never be recorded as the container of the
    #: paragraph above it (`dropped_paragraph_item`). §4.4: an indented block cannot interrupt a
    #: paragraph, so `Notes` + `    - x` (four columns under the paragraph's own base) is ONE `<p>`
    #: with the marker as its text, and the `<li>` a phantom push invents then makes the NEXT marker
    #: look dedented:
    #:     `Notes` + `    - x` + `<span>` + `2.     Closes #100`   renderer ONE `<p>` -> PROSE
    #: Measured against `MarkdownIt("commonmark")`: at 0-3 columns the marker DOES open a `<ul>`; at
    #: 4+ it is the paragraph's continuation text.
    real: bool = True


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
    _, residual, _ = _fence_line_column(line, item.depth)
    # ⛔ FEWER MARKERS IS NOT BY ITSELF A DEDENT (round 40 review, P2 — class A, both arms).
    # `consumed < item.depth` also matches a LAZY line, which drops its `>` and continues the
    # paragraph inside the item anyway; popping there measured the next line against no container
    # and blanked a real close (`Notes` + `> 2. x` + `    3.     Closes #100`, renderer ONE `<p>`).
    # The dedent is the COLUMN test, which is in the item's own space, and it covers the shapes the
    # marker count was there for: a line that left the item's quote entirely is at or before the
    # item's content column (`> text` against a depth-1 item's 2-column content comes out at 0),
    # while a lazy line that dropped the same marker and stayed indented into the item comes out
    # past it. A line that dedented BELOW the item still pops, which is the round-38/39 behaviour.
    return residual < item.rest_col


def _content_column(rest: str) -> int | None:
    """The list item's CONTENT column in `rest` space, or None if this line starts no item.

    ⛔ ONE COMPUTATION, TWO CALLERS (round 30 review, P2). CommonMark §5.2 measures the item's
    content column from the MARKER, and collapses a gap wider than four spaces to ONE column
    (a wider gap starts an indented code block, not a wider item). `_fence_candidate` implemented
    that; the item PUSH kept using `len(marker.group(0))`, which is marker + the WHOLE gap, so the
    two disagreed by up to four columns for exactly the lines the difference can be observed on.
    That was harmless while only the fence path read it — and became a class-A FALSE PASS the
    moment arm 2's `indented_code` threshold was read from the push:

        `-     item` + `        - [ ] x`   renderer `<pre><code>` -> the checklist must be hidden
        item-push column 6 -> 8 > 6+3 is FALSE -> the quoted box CLEARED a criteria-less heading
        CommonMark column 2 -> 8 > 2+3 is TRUE  -> hidden, as the renderer says

    Two computations of one column is how the next reader re-derives it and re-introduces the
    double count, so this is the single definition both call sites use.

    ⛔ AND IT IS NOT INERT OUTSIDE ARM 2 (round 31 review, P3 — the docstring claimed it was, and a
    sweep measured otherwise). The push's column is also read by `_item_dedented` (every pop) and by
    `_fence_candidate`'s enclosing-item branch, so collapsing it for a gap>4 line reaches the DEFAULT
    and `strict` arms too. Measured over a 5,760-body sweep (markers `-`/`*`/`1.`/`10.` x gap 1-9 x
    fence indent 0-7 x claim indent 0-4 x closer x blank): 660 divergences at the default and 440 at
    `strict`, and EVERY one is a claim the parent LOST and this keeps, confirmed against
    `MarkdownIt("commonmark")` — 0 in the other direction. ⚠️ THOSE TWO COUNTS ARE A SNAPSHOT of the
    head that measured them, before round 34's consumer-side fix moved `own_code` off this column's
    reach; the DIRECTION is the claim and the sweep is the authority
    (`tests/test_issue_process_lint.py::test_a_wide_gap_MARKER_LINE_after_an_OPEN_PARAGRAPH_is_PROSE`
    re-derives both directions on every run). The old column put a wide-gap item's
    content at the marker's raw width, so a fence opened under it was measured past the item and a
    real `Closes #N` in its prose was blanked away:
        `-     item` + blank + `  ``` Closes #100 ``` `  -> renderer keeps it as PROSE, parent dropped it
    Stated here because "additive only" was the previous claim and it was not true.
    """
    marker = _LIST_MARKER_RE.match(rest)
    if marker is None:
        return None
    m_indent = len(marker.group("indent"))
    gap = len(marker.group("gap") or "")
    marker_w = len(marker.group(0)) - m_indent - gap
    # ⛔ A MARKER WITH NOTHING AFTER IT STILL OPENS AN ITEM, AT THE MARKER'S WIDTH + 1 (§5.2's
    # "if the list item starts with an empty line, the content column is marker width + 1"). `gap == 0`
    # is exactly that spelling (`-`, `*`, `+`, `1.`), so it takes the collapse-to-one branch rather
    # than a marker-width-only column — which would put the content INSIDE the marker.
    return m_indent + marker_w + (gap if 0 < gap <= 4 else 1)


def _ordered_marker_at_least_two(rest: str) -> bool:
    """True if `rest` opens an ORDERED list item whose number is not 1.

    ⛔ THIS IS §5.2's OTHER INTERRUPTION RULE, THE ONE ON THE **CONSUMER** SIDE (round 34 review,
    P2 — a REGRESSION this file's own `own_code` introduced). The producer rule says where a marker
    line's CONTENT column is; the consumer rule says whether the line starts a list item AT ALL:
    *"a list item can interrupt a paragraph only if it is a bullet list item or an ordered list item
    that starts with 1."* So a bullet or `1.` always opens its item — and a wide gap there really does
    put the item's content in an indented code block — while an ordered marker ≥ 2 preceded by an
    OPEN PARAGRAPH is a lazy continuation of that paragraph, and its text is PROSE.

    Measured with `MarkdownIt("commonmark")` as the oracle at the parent head and on `main`:

        `Notes\n2.     Closes #100`  -> <p>Notes\n2.     Closes #100</p>   PROSE, the close is real
        head before this fix          -> parse_closing_refs -> []        the close was SILENTLY LOST
        parent / main                -> parse_closing_refs -> [100]

    That is the class-A direction (a delivered close nothing reports) AND a false positive in
    `tools/issue_process_lint.py` arm 1 (honest work flagged as claiming an issue nothing closes),
    so it is fixed rather than declared — but ONLY for the shape the rule names: the caller supplies
    the paragraph state (`prev_prose`), and nothing else about the line changes.
    """
    marker = _LIST_MARKER_RE.match(rest)
    if marker is None:
        return False
    token = marker.group(0)[len(marker.group("indent")):]
    num = token[:len(token) - len(marker.group("gap") or "")]
    if not num[0].isdigit():
        return False           # a bullet CAN interrupt a paragraph (§5.2), so this rule never fires
    return int(num.rstrip(".)")) != 1


def _is_bare_marker(rest: str) -> bool:
    """Is this line a list marker with NO content at all (`-`, `*`, `+`, `1.`) — §5.2's other spelling?

    `_LIST_MARKER_RE` accepts it (round 49) and `_content_column` gives it the spec's content column
    (marker width + 1). TWO readers need the distinction rather than the column: `_is_lazy_marker`,
    which answers §5.2's interruption rule, and the item push, which follows it.
    """
    marker = _LIST_MARKER_RE.match(rest)
    return marker is not None and not rest[_content_column(rest):].strip()


def _marker_gap(rest: str) -> int:
    """The spaces between this line's list marker and its content (0 when it opens no item).

    ⛔ NOT AN INDENTATION, A CONTENT OFFSET (round 32 review, P2). §5.2 collapses a gap wider than
    four spaces to ONE column, so `-     x` has its content at the item's content column + 4 — and §4.4
    says that IS an indented code block starting on the marker line itself. Measured, with
    `MarkdownIt("commonmark")` as the oracle: `-     [ ] x` renders as `<pre><code>[ ] x</code>`.

    This is the one place the marker-line case has to be read from, because the blanking test below
    looks at `rest_indent` — the indentation BEFORE the marker, which is 0 for such a line. Reading
    only the indent left the widest family of "quoted" lines as PROSE in both arms: a quoted
    `Closes #100`, `IN FLIGHT:` or checklist on a 5+-space marker line was credited as a claim, a
    live marker and a real criterion. Parity with the parent head rather than a regression, and no
    instance in this repo's own docs — but a false PASS is a false PASS, and the condition is already
    computed here.
    """
    marker = _LIST_MARKER_RE.match(rest)
    return len(marker.group("gap") or "") if marker else 0


#: A line that STARTS a LEAF block — one that leaves NO PARAGRAPH OPEN behind it, so the next line
#: cannot be a lazy continuation of it (§5.2's consumer side). The LIST-MARKER alternative that
#: `_BLOCK_START_RE` carries is deliberately ABSENT here: a marker line's content IS a paragraph,
#: and whether that marker opens an item at all is the question `_ordered_marker_at_least_two`
#: answers — a "block start" that treated every marker as a paragraph-ender would read
#: `- item` + `2.     Closes #100` (renderer: a NEW top-level list, i.e. CODE) as prose.
_NO_PARAGRAPH_RE = re.compile(
    r"(?: {0,3}#{1,6}(?:[ \t]|$)"                                      # an ATX heading
    r"| {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$"    # a thematic break
    r")"
)
#: A SETEXT underline is an ender ONLY under a paragraph that exists (round 36 review, P2). `===` on
#: its own is a PARAGRAPH of text, so treating it as an unconditional ender blanked the real
#: `Closes #N` two lines below it — a lost close, and a REGRESSION against `origin/main`, measured:
#:     `=` + `2.     Closes #100`   renderer `<p>= 2.     Closes #100</p>`   PROSE, main credits it
#: The `-` spelling never reaches here as a lone underline (three or more is a thematic break above).
_SETEXT_UNDERLINE_RE = re.compile(r" {0,3}(?:=+|-+)[ \t]*$")
#: ⛔ TWO HTML-BLOCK RULES, NOT ONE, BECAUSE THE SPEC HAS TWO (round 37 review, P2). §4.6 gives
#: types 1-6, which CAN interrupt a paragraph, and **type 7** — a complete tag alone on a line —
#: which explicitly CANNOT. The previous single regex accepted a tag NAME, so `<span>`, `<br />`,
#: `<custom-tag>` and `<x` all ended an open paragraph and blanked the real `Closes #N` below them;
#: measured at `origin/main`: 24 lenient + 24 strict closes lost per shape, 0 fixed.
#: `_HTML_BLOCK_INTERRUPT_RE` is types 1-6 (the sigil openers plus the spec's own TYPE-6 TAG LIST —
#: `<div>` is on it, `span`/`br`/`custom-tag` are not). Type 7 needs no regex at all: its rule is
#: "never an ender", so the predicate simply does not test it (see the comment at the call site).
_HTML_TYPE6_TAGS = (
    "address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|"
    "dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|"
    "h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|"
    "optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul"
)
#: §4.6 type 1's tag names, in ONE place: the start test, the interrupt test and the closer table all
#: read this string, so the three cannot drift apart (round 48 review, P2 — the START set had the arm
#: and the INTERRUPT set did not, which is what left `<pre>` not ending a paragraph).
_HTML_TYPE1_TAGS = "script|pre|style|textarea"
#: ⛔ §4.6 TYPE 6 STARTS AT `<tag` **AND** `</tag` (round 49 fix round — a LOST close the container
#: repair exposed). The preamble is "line begins with `<` or `</` followed by one of these names", and
#: the missing `</` arm read every closing block tag as a TYPE-7 line instead — which the module
#: treats as inline prose that never ends a paragraph, while the renderer ends it:
#:     `- a bullet` + `</div>` + `2.     Closes #100`
#:         renderer: the `</div>` closes the list and starts its OWN type-6 block, whose raw text is
#:         the marker line -> PROSE (`[100]` on `origin/main`)
#: Measured against §4.6's tag list: `</div>`, `</table>` and `</p>` interrupt a paragraph, while
#: `</pre>` and `</span>` do not (`pre` is type 1, `span` type 7 — neither is type 6), which is what
#: this arm now reproduces.
_HTML_BLOCK_INTERRUPT_RE = re.compile(
    rf" {{0,3}}(?:<!--|<\?|<![A-Za-z\[]|<(?:{_HTML_TYPE1_TAGS})(?:[ \t]|>|$)"
    rf"|</?(?:{_HTML_TYPE6_TAGS})(?:[ \t]|/?>|$))", re.IGNORECASE)

#: ⛔ §4.6's SIX TYPES DO NOT END THE SAME WAY, AND THE START SET IS NOT ONE REGEX (round 44 review,
#: P2 — a fail-open: crediting a CODE line). Types 2-5 end at their OWN closing token (`-->`, `?>`,
#: `>`, `]]>`), type 1 at its closing tag, and only type 6 at a blank line. A state machine that
#: ended every block at a blank line kept `<!-- c -->` open over the NEXT line, so
#: `<!-- c -->` + `-     Closes #100` (renderer `code_block`) was CREDITED in both arms — 600
#: fail-open bodies over one grid. And `_HTML_BLOCK_INTERRUPT_RE` had no type-1 arm at all (its
#: `<!` sigil is not `<script`), so `<pre>` + `-     Closes #100` stayed blanked while the renderer
#: says `html_block` and `origin/main` credits it. One root, both halves, one table.
#: ⛔ …AND THE INTERRUPT SET WAS STILL A TYPE SHORT (round 48 review, P2 — a LOST close, and it is
#: the reason the `blank_carried` state lost one). The table above fixes the START set; the
#: paragraph question reads the OTHER regex, and type 1 does interrupt a paragraph (§4.6: types 1-6
#: do, only type 7 does not), so `<pre>` left a paragraph "open" that the renderer had already
#: ended. The next line — a type-7 `<span>` — was then refused as a block opener it could not be,
#: and the marker under it was blanked:
#:     `> - item` + `>` + `>   <pre>` + `> <span>` + `> -     Closes #100`
#:     renderer: an `html_block` (`<span>`'s) whose raw text is the marker line — PROSE
#:     `origin/main` `[100]`, round-48 head `[]`
#: The tag list is now ONE constant shared by all three readers.
#: ⛔ …AND §4.6 TYPE 1 ENDS AT **ANY** OF THE FOUR CLOSERS, NOT AT THE OPENER'S OWN (round 49 review,
#: P2 — a fail-open in `strict`, the PASS arm). The end condition is stated as a LINE CONTAINING
#: `</script>`, `</pre>`, `</style>` or `</textarea>`, with no pairing to the opener: `<pre>` …
#: `</style>` ends the block, so the marker under it is NOT that block's text. Pairing each opener
#: with its own closer kept 12 of the 16 combinations open — `<pre>` + `x` + `</style>` + blank +
#: `    Closes #N` (renderer `code_block`) was CREDITED while the matched pairings were refused.
#: This set is DERIVED from `_HTML_TYPE1_TAGS`, which is what the comment above has claimed since
#: round 48: the old independent dict let an editor add a tag to the constant alone and get a
#: `KeyError` from inside `strip_code` (the crash class that aborts `--sweep` for every PR).
#: ⛔ …AND A TYPE-1 START IS NOT A SELF-CLOSING TAG (round 50 review — a fail-open in `strict`, the
#: PASS arm). §4.6 type 1's start condition is the tag name "followed by a space, a tab, the string
#: `>`, or the end of the line" — `/` is NONE of those, and `/>` belongs to type 6, whose tag list
#: does not carry these four. `<pre/>` is therefore a COMPLETE tag alone on a line: type 7, which
#: hides nothing and ends at the blank line. Accepting `/>` opened a type-1 block that ran to EOF or
#: to a closer, and the 4-column line below the blank line was credited:
#:     `<pre/>` + `` + `    Closes #100`   renderer `<pre/>` then `<pre><code>` -> CODE
#:     `origin/main` `[]`; the head credited `[100]` in `strict` — 4 of 4 tags, measured.
#: The `/>` spelling stays on the TYPE-6 arm of `_HTML_BLOCK_INTERRUPT_RE`, where the spec allows it.
_HTML_TYPE1_TAG_RE = re.compile(rf" {{0,3}}<({_HTML_TYPE1_TAGS})(?:[ \t]|>|$)", re.IGNORECASE)
_HTML_TYPE1_END_TOKENS = frozenset(f"</{tag}>" for tag in _HTML_TYPE1_TAGS.split("|"))


def _html_block_ended(closer: frozenset[str], rest: str) -> bool:
    """Does `rest` carry the block's OWN end condition? (an empty `closer` = a blank line ends it)"""
    if not closer:
        return False
    lowered = rest.lower()
    return any(token in lowered for token in closer)


def _html_block_open(rest: str) -> frozenset[str] | None:
    """§4.6's html-block START for types 1-6, with the tokens that END it (empty = a blank line).

    `None` means "this line opens no HTML block". A NON-EMPTY set is "a line carrying ANY of these
    ends the block" — one token for types 2-5, and all four for type 1, whose end condition §4.6
    states over the whole tag family rather than per opener. Type 7 is deliberately absent: it
    cannot interrupt a paragraph, and the paragraph rule tests only this function's types (see
    `_HTML_BLOCK_INTERRUPT_RE`'s own note).
    """
    if _HTML_TYPE1_TAG_RE.match(rest):
        return _HTML_TYPE1_END_TOKENS   # type 1 — ANY of `</script>`/`</pre>`/`</style>`/`</textarea>`
    if re.match(r" {0,3}<!--", rest):
        return frozenset({"-->"})     # type 2 (§4.6: the block ends at the `-->`)
    if re.match(r" {0,3}<\?", rest):
        return frozenset({"?>"})      # type 3
    if re.match(r" {0,3}<!\[CDATA\[", rest, re.IGNORECASE):
        return frozenset({"]]>"})     # type 5 — BEFORE the `<!` arm below, which also matches it
    if re.match(r" {0,3}<![A-Za-z]", rest):
        return frozenset({">"})       # type 4 (a declaration: `<!DOCTYPE html>`)
    if _HTML_BLOCK_INTERRUPT_RE.match(rest):
        return frozenset()             # type 6 — the only one a blank line ends
    return None


_HTML_TYPE7_RE = re.compile(r" {0,3}</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>[ \t]*$")


def _html_block_leaf(rest: str, prev_prose: bool) -> frozenset[str] | None:
    """Type 7 — a COMPLETE tag alone on a line — as a block whose text is prose (an EMPTY end set).

    ⛔ TYPE 7 IS NOT AN ENDER, BUT IT IS STILL A BLOCK (round 45 review, P2 — 1,680 lost closes
    against `origin/main`). §4.6 type 7 cannot INTERRUPT a paragraph, which is why the ender
    predicate must not test it; that says nothing about whether its own text is read, and the state
    machine's question is the second one. A bullet after `<br />`/`<span>`/`</div>` is the block's
    raw text (renderer `html_block`), so blanking it lost closes that `origin/main` credits:

        `<br />` + `-     Closes #100`      renderer `html_block`, head `[]`, `origin/main` `[100]`

    The `prev_prose` guard is the spec's own condition: after an open paragraph the line continues
    that paragraph (`<span>x</span>` is not a lone tag at all, and neither is a line following one).
    """
    if prev_prose or not _HTML_TYPE7_RE.match(rest):
        return None
    return frozenset()


def _is_lazy_marker(rest: str, prefix: str, prev_prose: bool,
                    prev_quote_depth: int, container: _ListItem | None,
                    prev_container: _ListItem | None,
                    line_col: int, rest_indent: int, raw_indent: int,
                    dropped_paragraph_item: _ListItem | None = None) -> bool:
    """Is this ordered marker ≥ 2 a LAZY CONTINUATION of the open paragraph?

    ⛔ §5.2's "an ordered marker ≥ 2 cannot interrupt a paragraph" IS CONDITIONAL ON THERE BEING A
    PARAGRAPH TO INTERRUPT (round 39 review, P2 — class B, both arms). A line that starts a NEW
    CONTAINER interrupts one (§5.1: a blockquote can), so the marker inside it is a real item:

        `Notes` + `> 2. x` + `> 3.      Closes #100`
            renderer `<p>Notes</p><blockquote><ol start="2">…</ol></blockquote>` — a real `<ol>`,
            whose wide-gap content is `<pre><code>`; applying the rule anyway left the item unpushed
            and CREDITED the quotation in both arms.

    Both callers — the item push and the leaf-block question — ask THIS function, because two
    answers to one question is how round 37's fix re-opened round 36's hole.

    ⛔ AND THE QUESTION HAS THREE ANSWERS, NOT TWO (round 40 review, P2 — class A, both arms).
    Answering "the line's prefix depth is shallower than the paragraph's" with a single `<=` read a
    line that DROPPED its `>` as a continuation even when the `>` it dropped belonged to a quote
    nested INSIDE the item (`- > q` + `  2.     Closes #100`: the nested quote ended, the marker
    opens its own item, renderer `code_block`). Answering it with `==` did the opposite and lost the
    real close on a line that dropped the ITEM'S OWN `>` while staying inside the item
    (`> 2. x` then `    3.     Closes #100`: renderer keeps one paragraph). So "the line dropped
    its `>`" and "the line dedented out of its item" are TWO facts:

    * depth == the paragraph's — the same container, a continuation (§5.2 in both directions);
    * depth >  the paragraph's — a new container, which interrupts (§5.1);
    * depth <  the paragraph's — the line dropped a `>`. That is a continuation ONLY when the
      dropped markers were the ITEM's own (`prev_quote_depth == container.depth`) and the line is
      still inside that item (`rest_indent > container.rest_col`); a nested quote's markers
      dropped, or no item left to hold the paragraph, means the paragraph's container ENDED.

    `line_col` is the RAW content column and `container.rest_col` its `rest`-space twin, which is
    the same pair round 39 established for the `>=` term below.
    """
    if not (prev_prose
            # ⛔ AND A **BLANK** LIST ITEM CANNOT INTERRUPT A PARAGRAPH EITHER (§5.2: "in order for a
            # list to interrupt a paragraph, it must start with a non-blank list item"). A bare
            # marker — the end-of-line spelling `_LIST_MARKER_RE` now accepts — therefore opens no item
            # after prose and is that paragraph's own text:
            #     `Notes` + `1.` + `<br />` + `1.     Closes #100`
            #         renderer ONE `<p>` for the first three lines, so `<br />` is inline text (no
            #         type-7 block) and the wide-gap `1.` below opens the `<ol>` whose content is
            #         `<pre><code>` — CODE. Reading `1.` as an item ended the paragraph, opened a
            #         phantom html block on `<br />`, and its `not _html_here` term then CREDITED the
            #         marker line in all three arms (round 49, the bare-marker rule's own sibling).
            # ⚠️ A BARE `-` IS EXCLUDED: under an open paragraph it is a SETEXT UNDERLINE (§4.3), so
            # the paragraph becomes a HEADING rather than surviving as lazy text (`**bold**` + `-` +
            # `<span>` + `1.     Closes #N` — renderer `<h2>`, the `<span>` then OPENS a type-7 block
            # and the marker line is that block's text, i.e. PROSE; reading the `-` as lazy kept a
            # paragraph open, refused the leaf and blanked the real close).
            and (_ordered_marker_at_least_two(rest)
                 or (_is_bare_marker(rest) and not _SETEXT_UNDERLINE_RE.match(rest.strip())))
            # ⛔ AND WHEN THE POP JUST RECORDED THAT THE PARAGRAPH'S ITEM ENDED, THE QUESTION IS
            # ANSWERABLE AGAINST THAT ITEM (round 50 review, P1 — a LOST CLOSE in all three arms).
            # The identity test below is right to refuse a STACK item that is not the paragraph's,
            # but `dropped_paragraph_item` is not a stack item: it is the fact "the item that held
            # this paragraph has ENDED", and the renderer answers that line as the paragraph's LAZY
            # continuation when it sits in an indented-code position (§4.4):
            #     `  1.` + `        x` + `    2.      Closes #100`   renderer ONE `<li>` -> PROSE
            #     `origin/main` `(True,False,False)`; the round-49 head `(False,False,False)`.
            and (container is prev_container
                 or (container is None and dropped_paragraph_item is not None))):
        return False
    depth = _quote_depth(prefix)
    if depth > prev_quote_depth:
        return False                     # a DEEPER container interrupts the paragraph
    if depth == prev_quote_depth:
        # ⛔ "NO CONTAINER HERE" IS NOT "NO CONTAINER ABOVE" (round 49 review, P1 — a FAIL-OPEN in
        # `strict=True`, the `issue_process_lint` PASS arm). The `container is None` arm below is the
        # top-level paragraph: with no item at all, a marker that cannot interrupt a paragraph is
        # that paragraph's lazy continuation. It is answered from the STACK, and the stack drops an
        # item on a line the column test reads as a dedent even when the renderer keeps the paragraph
        # open as a LAZY continuation — so by the marker line there is no item left to compare with,
        # `container` and `prev_container` are both `None`, and the guard above is vacuously true:
        #     `- a bullet` + `Summary of the change.` + `    indented code` + `2.     Closes #100`
        #         renderer: ONE `<li><p>` for the first three lines, then a NEW `<ol start="2">`
        #         whose wide-gap content is `<pre><code>` -> CODE; `origin/main` `[]`, head `[100]`
        # The paragraph those lines describe lives INSIDE an item, so the fact that is missing is not
        # "is there a container" but "the item that held the paragraph has ENDED" — recorded where it
        # ended (`dropped_paragraph_item`, set by the pop) and measured in that item's own column, the
        # way every other in-item line is. ⚠️ Two obvious guards are FALSIFIED at this head and must
        # not be re-tried (measured on a 32,760-body grid): `container is not None and container is
        # prev_container` and keeping `prev_container` across a lazy line each close ~610 fail-opens
        # and hand back 870-2,170 real closes, because they move EVERY reader of `prev_container`,
        # not just this one question.
        if container is None and dropped_paragraph_item is not None:
            # ⛔ …AND A LINE AT AN INDENTED-CODE POSITION IS STILL THAT PARAGRAPH'S TEXT (round 50
            # review, P1 — a LOST CLOSE in all three arms, the silent case #10597 was filed for).
            # §5.2's lazy continuation does NOT have to reach the content column, and the pop is a
            # COLUMN test that runs before this question is asked — so the item it dropped is the
            # one to measure in, and 4+ columns is §4.4's "an indented code block cannot interrupt a
            # paragraph", whatever marker the line carries:
            #     `  1.` + `        x` + `    2.      Closes #100`
            #         renderer ONE `<li>` holding both lines -> PROSE
            #         `origin/main` `(True,False,False)`; the round-49 head `(False,False,False)`.
            # The term is the SAME one `_dropped_quote_is_lazy` carries, for the same reason: at a
            # column <= 3 the line can START A BLOCK instead (`  1.` + `        x` +
            # `2.     Closes #100` opens a sibling item whose wide-gap content is `<pre><code>`).
            return (line_col >= dropped_paragraph_item.rest_col
                    or line_col > _MAX_FENCE_INDENT)
        return container is None or line_col >= container.rest_col
    # depth < prev_quote_depth: the line dropped a `>` — see the docstring's third bullet.
    return _dropped_quote_is_lazy(prefix, prev_quote_depth, container, prev_container,
                                 rest_indent, raw_indent)


def _dropped_quote_is_lazy(prefix: str, prev_quote_depth: int,
                          container: _ListItem | None, prev_container: _ListItem | None,
                          rest_indent: int, raw_indent: int) -> bool:
    """Did this line drop a `>` and remain a LAZY continuation of the paragraph inside the item?

    ⛔ "FEWER MARKERS" IS NOT "THE ITEM ENDED" (round 40 review, P2 — class A, both arms). A lazy
    line drops its blockquote marker and continues the open paragraph anyway, because it is still
    indented into the item that holds that paragraph:

        `Notes` + `> 2. x` + `    3.     Closes #100`     renderer ONE `<p>` — the close is REAL
        `Notes` + `> 2. x` + `    -     Closes #100`      renderer ONE `<p>` as well

    The pop and the wide-gap rule both read the missing `>` as a dedent, so the item ended for the
    tool and the line was blanked as code (`[]` at HEAD, `[100]` on `origin/main`). THREE facts say
    otherwise, and each is needed (round 40 measured the pair):

    * the markers were the ITEM's own (`prev_quote_depth == container.depth`) — a quote nested
      INSIDE the item ends when its markers go, and `- > q` + `  2.     Closes #100` must stay CODE;
    * the line is still INSIDE the item (`rest_indent > container.rest_col`) — at the item's content
      column or before it the renderer opens a new block, so `> - item` + `lazy` + `  2.     Closes
      #100` stays CODE as well;
    * the container is unchanged, so there is still an item to hold the paragraph (`> Notes` +
      `lazy` + `2.     Closes #100` has none, and stays CODE).

    A line that answers this is PROSE whatever its marker is: the marker cannot open an item out of
    a lazy paragraph continuation (§5.2's consumer rule), so a wide gap after it is text, not code.
    """
    return (container is not None
            and container is prev_container
            and _quote_depth(prefix) < prev_quote_depth
            and prev_quote_depth == container.depth
            and rest_indent > container.rest_col
            # …and the line cannot START A NEW BLOCK once that container is gone (round 40).
            # This is why `> - item` + `   2.     Closes #100` (marker at raw column 3) is CODE while
            # `> 2. x` + `    3.     Closes #100` (raw column 4) is prose: a marker at column <= 3
            # opens a top-level list, which DOES interrupt a paragraph, so nothing is lazy there;
            # at column 4+ it would be an indented code block, which cannot interrupt one.
            # The raw column is the indent the line has left once its dropped markers are gone, and
            # it is the `indent` the caller measured before splitting the prefix.
            and raw_indent > _MAX_FENCE_INDENT)


def _leaves_no_paragraph(rest: str, rest_indent: int,
                        container: _ListItem | None, prev_prose: bool,
                        lazy_marker: bool) -> bool:
    """Does this PROSE-EMITTED line leave NO PARAGRAPH OPEN behind it?

    ⛔ "THE PREVIOUS LINE WAS EMITTED AS PROSE" IS NOT "a paragraph is open" (round 34 review, P2 —
    the half that `_ordered_marker_at_least_two` needed and did not get). Four families are written
    to `out` as prose yet END the paragraph, and each one left the consumer-side gate above treating
    the NEXT line as a continuation, so a wide-gap ordered marker after it was KEPT — the fail-open
    direction, a quoted `Closes #N` credited as a claim in BOTH arms. Measured, `MarkdownIt("commonmark")"
    as the oracle (each body is `<line>\n` + `2.     Closes #100`):

        `# H`        -> `<h1>H</h1>` then a NEW `<ol>` whose content is `<pre><code>`   CODE
        `***`        -> `<hr />` then the same                                      CODE
        `Notes`/`---`-> a SETEXT heading, then the same                              CODE
        `    Notes`  -> an INDENTED CODE BLOCK, then the same                        CODE

    ⛔ AN HTML BLOCK IS AN ENDER, BUT ` {0,3}<` IS NOT AN HTML-BLOCK START CONDITION (round 36 review,
    P2). `_BLOCK_START_RE` counts any leading `<`, which is right where `_paragraph_end` uses it and
    was a REGRESSION here: `<3 items` and `<https://e.com>` are ordinary PARAGRAPHS, so ending the
    paragraph at them blanked a real `Closes #N` below — measured at 24 lenient + 24 strict closes
    lost on each shape, with 0 fixed. `_HTML_BLOCK_START_RE` is the start condition instead (a tag
    name followed by `[ \t]`, `/>` or end of line, or one of the four sigil openers), so a genuine
    `<div>` still ends the paragraph, while `<3` and an autolink do not. ⛔ WHAT AN HTML BLOCK DOES
    NOT DO IS HIDE ITS OWN TEXT: `parse_closing_refs("<div>\nCloses #100\n</div>")` still returns
    `[100]`, so the ender changes what the NEXT line is measured as, not whether the block's own
    content is read. That is parity with every head before this one (only FENCED content is blanked),
    and the sentence above used to imply otherwise — this file does not claim GitHub's behaviour for
    raw HTML either way, it only decides the block boundary.

    The INDENTED case is measured against the CONTAINER, which is the block grammar's test and not
    `strict`'s: `- item` + `    cont` is a paragraph INSIDE the item (4 <= 2 + 3), so it does open
    one — the renderer keeps `- item` + `    cont` + `2.     x` in one `<li>`.
    """
    # ⛔ THE QUESTION IS ABOUT THE LINE'S OWN CONTENT, WHICH ON A MARKER LINE IS NOT `rest` (round 36
    # review, P2 — a false PASS in both arms). A leaf block may sit ON a bullet-marker line
    # (`- # H`, `- ***`), and testing only `rest` read that as prose-that-leaves-a-paragraph-open, so
    # a quoted wide-gap `Closes #N` under it was credited:
    #     `- # H` + `  2.     Closes #100`   renderer `<li><h1>H</h1><ol start="2"><li><pre><code>`
    # The content column is the one `_content_column` already defines for the fence path and the item
    # push, so this is a THIRD reader of one definition rather than a fourth definition.
    col = _content_column(rest)
    # ⛔ A BARE MARKER LEAVES NO PARAGRAPH OPEN, and it arrives here without a gap (round 49 review, P1
    # — a LOST close; the trigger is the `prev_prose` state a quote-only line leaves behind, not the
    # empty item itself). `_LIST_MARKER_RE` now matches the END-OF-LINE spelling (§5.2's second form),
    # so `_content_column` answers it and the content is the empty string — no special case needed:
    #     `> <div>` + `>` + `> -` + `> <br />` + `> -     Closes #100`
    #         renderer: the `>` line ends the type-6 block, `> -` is `<li></li>`, `<br />` then opens a
    #         type-7 `html_block` whose raw text is the marker -> PROSE
    #         `origin/main` `[100]`, round-48 head `[]`
    # The blank-only line above is load-bearing only because it leaves the paragraph OPEN; the answer
    # this function owes is about the MARKER line's own content, which is nothing.
    body = rest[col:] if col is not None else rest
    # ⛔ AN ITEM WHOSE OWN CONTENT IS ITSELF A BARE MARKER LEAVES NO PARAGRAPH EITHER (round-51
    # review, P0 — a FAIL-OPEN in BOTH arms, the worst direction). `_content_column` answers about
    # ONE marker and `- -` nests two, so the content of the outer item was the string `-`: it is not
    # empty (the round-39 rule does not fire), it matches no leaf test, and the fall-through said "a
    # paragraph is open". That was harmless while a bare marker opened nothing — but round 49 made
    # `-` an ITEM (§5.2's end-of-line form), so the line after it is measured against a paragraph the
    # renderer closed, and the wide-gap marker under it was credited:
    #     `-` + `- -` + `  2.     Closes #100`
    #         renderer: `<li></li>`, then the nested empty item, then `<ol start="2">` whose
    #         wide-gap content is `<pre><code>` -> CODE, in BOTH arms
    #         `origin/main` `[]` in both arms, the round-50 head `[100]` in both
    # ⛔ AN EMPTY CONTENT OPENS NO PARAGRAPH (round 39 review, P2 — class B, both arms). `- ` on its
    # own renders `<ul><li></li></ul>`: there is no paragraph to continue, so the next wide-gap
    # marker opens its own list and its content is CODE. Every leaf test below reads "no ender
    # matched" as "a paragraph is open", which is right for text and wrong for nothing-at-all:
    #     `- ` + `  2.      Closes #100`   renderer `<li><ol start="2"><li><pre><code>`   CODE
    # …and the same for `* `, `> - `, and for a quote line whose content is only `>`.
    # ⛔ THE LAZY ANSWER COMES FIRST, BEFORE THE EMPTY-CONTENT ANSWER (round 40 review, P2 — class A,
    # both arms, and the caller had ALREADY answered it). Forgetting the order re-opened it the other
    # way round: `2. ` (an ordered marker ≥ 2 with EMPTY content) is a lazy continuation of the open
    # paragraph, so the paragraph STAYS open and the next line continues it:
    #     `Notes` + `2. ` + `2.     Closes #100`   renderer ONE `<p>`, `[100]` on `origin/main`
    # The empty-content test fired first and reported "no paragraph open", so the wide-gap marker
    # below opened its own list and the real close was blanked. The round-39 empty-content rule still
    # holds for a BULLET (`- ` + `  2.      Closes #100`), which `_lazy_push` never answers true for.
    # ⛔ …BUT NOT WHEN AN ITEM HOLDS THE PARAGRAPH *AND* THE LINE CARRIES ITS OWN CONTENT. The
    # lazy test's `line_col >= container.rest_col` arm measures against the OUTER item's content
    # column, so an item whose own content is itself a bare marker answers "continuation" and the
    # ladder below — the only thing that knows that item holds no paragraph — never runs:
    #     `* - x` + `   2. -` + `  2.     Closes #100`
    #         renderer: the `- x` list is nested INSIDE the `*` item, so its paragraph lives at the
    #         nested content column; the marker line below is a `code_block` -> CODE
    #         `origin/main` `[]` both arms; the round-52 head credited `[100]` in both, and
    #         `issue_process_lint.claims()` returned `closing: [100]` — the arm-1 false PASS
    # ⛔ THE DISCRIMINATOR IS THE LINE'S OWN CONTENT, NOT THE CONTAINER (round-55 review, P1 — the
    # `container is None` form I measured first was FALSIFIED: it disables the lazy answer for EVERY
    # item-held paragraph, so a BLANK item — which §5.2 says cannot interrupt a paragraph — stopped
    # being a continuation and a real close was dropped in ALL FOUR arms, where `origin/main`
    # credits it and the renderer says PROSE):
    #     `- item` + `  1.` + `  2.     Closes #100`      renderer ONE `<p>`; `origin/main` `[100]`
    # 33 such bodies on a 3,520-body grid, 10 of them in all four arms. Gating on "this line has
    # content of its own" keeps those lazy (the empty-content rule is what answers them, as round 40
    # recorded) while an item whose content IS a marker falls through to the ladder, which is the
    # fact the round-51 P0 needed.
    # ⛔ WHAT THIS GATE DOES **NOT** FIX, MEASURED (round-55 re-review, P1 — do not read the counts
    # below as "no regression"): the gate answers ONE question — does this line carry content of its
    # own — and it OVERRIDES the caller's `lazy_marker` answer to do it. So an item-held lazy marker
    # whose content merely LOOKS like a leaf is now read by the ladder, contradicting this function's
    # own rule 50 lines below ("A MARKER LINE THAT OPENS NO ITEM HAS NO CONTENT COLUMN TO INSPECT …
    # The answer is the CALLER's (`lazy_marker`)"):
    #     `- item` + `  2. # H` + `  2.     Closes #100`
    #         renderer ONE `<p>` (an ordered marker >= 2 cannot interrupt a paragraph, so `# H` is
    #         TEXT) -> PROSE; `origin/main` `[100]` in all four arms, `claims()` `[100]`; THIS HEAD
    #         `[]` in all four arms, `claims()` `[]` — a LOST close
    # 332 such bodies on a 43,548-body sweep (25 of them reaching `issue_process_lint.claims()`), none
    # of them newly blanked by the re-review commit but all of them new against `origin/main`. The
    # right fix is the COLUMN, not this gate: `_is_lazy_marker`'s `line_col >= container.rest_col` arm
    # should be measured against the item that actually HOLDS the open paragraph (the nested content
    # column), and then `lazy_marker` can decide un-overridden — filed rather than attempted here,
    # because every guard in `_is_lazy_marker` is measured and pinned.
    # ⚠️ DECLARED REGRESSION — F1/F2, and deliberately NOT "at parity with `origin/main`" (round-57
    # and round-58 reviews, P1 + P2; re-measured at `e870d7437` and again at `446e0f107e` for this
    # declaration). The grids the sentence below names compose NEITHER a TYPE-7 LINE (`<br />`,
    # `<span>`) NOR a PLAIN PROSE LINE (`Notes`, `x`) between a bare-marker line and the wide-gap
    # marker, and EITHER KIND moves both columns the wrong way against `origin/main` — tip
    # `23d227c534`, whose `tools/closes_landing.py` is byte-identical to `a022f03ce4` (#11330, a
    # descendant of the #11186 squash `193bbfd9a2`) — which gets F1 RIGHT because it runs the content
    # ladder at the TOP of this function, exactly where the early return below pre-empts it. (It does
    # NOT get F2 right: the row below records that `origin/main` loses that close in both strict
    # arms. "Main is right here" is only ever true of F1, and a lane must not read it as a revert
    # target.) ⛔ BOTH KINDS ARE MEASURED, and the round-59 review was right to
    # refuse the narrower wording: the prose member is NOT a subset of the type-7 one.
    #     `* - x` + `Notes` (or `x`) + `   2. -` + `  2.     Closes #100`
    #         renderer `<pre><code>` -> CODE
    #         `origin/main` `[]` in all four arms, `claims()` `[]`
    #         THIS HEAD `[100]` in all four arms, `claims()` `[100]` — CLASS A, identical to F1
    #     `* - x` + `<br />` + `   2. -` + `  2.     Closes #100`
    #         renderer `<pre><code>Closes #100` -> CODE
    #         `193bbfd9a2` and `origin/main` `[]` in all four arms, `claims()` `[]`
    #         THIS HEAD `[100]` in all four arms, `claims()` `[100]` — CLASS A: a false PASS in arm 1
    #         (`strict`) of `issue_process_lint`, the gate this module feeds. `<span>` in place of
    #         `<br />` measures identically. The gate answers `container is None` -> False before the
    #         ladder runs: the `<br />` line dedented the item in the MODEL while the renderer kept
    #         the paragraph inside it.
    #     `Notes` + `2. -` + `<br />` + `     - x` + `  2.     Closes #100`
    #         renderer ONE `<p>` -> PROSE
    #         `origin/main` `[100]` in the two LENIENT arms, `[]` in both `strict` arms
    #         THIS HEAD `[]` in all four arms — a LOST close (P2). At parity with the BRANCH POINT
    #         `e3108f2bc9` (the parent of this branch's first commit `c733aaf548`; `80c912244` is an
    #         older main commit NOT on that path, and happens to measure the same): a `main` fix this
    #         branch does not carry, not a blank invented here.
    # ⛔ F2's PREDICATE, stated because this row cost a round: `issue_process_lint.claims()` is `[100]`
    # at THIS HEAD on this body too — the gate loses NOTHING here. The loss is on the module's LENIENT
    # DEFAULT path (what `closes_landing` itself reports), where head `n` and `origin/main` `Y`.
    # (The round-58 record's F2 row read `n n Y Y` at this head, and that was CORRECT — it measured
    # `'#100' in strip_code(body, **arm)`, i.e. raw text RETENTION. Re-measured here it is `n n n n`,
    # and that is also correct — for `100 in parse_closing_refs(strip_code(body, **arm))`, i.e. whether
    # a claim is CREDITED. Two predicates, one body; this row records the second because crediting is
    # what the module is for. Neither measurement was wrong, so no correction is owed.)
    # ⛔ WHY THIS IS DECLARED AND NOT FIXED HERE: three candidate terms were built and measured and
    # EVERY ONE trades a family (#11369) — the `deep_col` column fix buys F1 with 1 lenient + 2
    # strict lost closes and still cannot separate `   2. -` at col 3 from `  1.` at col 2; gating
    # the pop record on `dropped_paragraph_item` buys F1 with a close lost in ALL FOUR arms
    # (`- x` + `Notes` + `   2. -` + `   2. -` + the marker: `head` credits it `Y Y Y Y` and
    # `origin/main` BLANKS it `n n n n`, and the renderer says PROSE — so `head` is right here and
    # that candidate would move `head` ONTO `main`'s wrong answer); and the tip commit `e870d7437`
    # is the REVERT of a third. The discriminator is the
    # missing state fact "which item held the open paragraph", not a column — that is #11369's root.
    # #11349 is ADJACENT, not this half: it owns a LENIENT-arm false CREDIT on a `>`-interrupt shape,
    # which is the opposite direction from F2's lost close. `tests/test_closes_landing.py::
    # test_the_TYPE_7_neighbour_of_the_round_55_gate_is_a_DECLARED_REGRESSION` pins both rows, so a
    # fix reddens a named test instead of silently moving the family.
    # On the two composed grids this gate is clean (0 new fail-opens, 0 new lost closes vs
    # `origin/main`, all four arms) — those grids compose NEITHER a leaf-shaped lazy marker's content
    # NOR an interposed line between a bare-marker line and the wide-gap marker of EITHER kind
    # (type-7 HTML or plain prose), which is why the claim must name its grid and must never be
    # stated bare.
    if lazy_marker and (container is None or not body.strip()):
        return False
    # ⛔ §4.4 COMES BEFORE THE LEAF TESTS, NOT AFTER THEM (round 50 — the ordering half of the
    # finding that kept the WIP arm from blanking a paragraph's own text). "An indented code block
    # cannot interrupt a paragraph" is a fact about the LINE, so when it holds nothing below this
    # point can apply: a 4+-column line at a code position IS that paragraph's lazy continuation
    # whatever the line looks like — and the empty-content answer read a 4-column bare `-` as an empty
    # ITEM (which it is not; the renderer keeps it as text) and ended a paragraph that is open:
    #     `Notes` + `    -` + `2.     Closes #100`   renderer ONE `<p>` -> PROSE, `origin/main` credits
    # Whichever of the two answers runs first IS the verdict, which is why round 40 put `lazy_marker`
    # first for the same reason.
    # ⚠️ AND A CONTENT-BLANK LINE IS NOT PROTECTED: `rest.strip()` — `>     ` is a BLANK line to the
    # block grammar, so it ENDS the paragraph rather than continuing it.
    if rest.strip() and rest_indent > _MAX_FENCE_INDENT and (
            container is None or rest_indent > container.rest_col + _MAX_FENCE_INDENT):
        return not prev_prose
    if not body.strip():
        return True
    # ⛔ …AND THE SAME LADDER APPLIED TO THE CONTENT, for an item whose own content is ITSELF a bare
    # marker (round-51 review, P0). It sits HERE, AFTER `lazy_marker` and AFTER §4.4, because both of
    # those guards answer about the LINE and this one about what the item holds: the answer was
    # `return True` at the top of the function, where it pre-empted the round-40 ordering ("THE LAZY
    # ANSWER COMES FIRST") and §4.4, and blanked real closes in both arms (round-52 review, P1):
    #     `Notes` + `2. -` + `2.     Closes #100`   renderer ONE `<p>` -> PROSE, `[100]` both arms
    # Also why the ROUND-51 STATE FIX cannot simply be un-gated: this answer clears `prev_prose`, so
    # the indent-pop below must record the dropped item from the POP rather than from `prev_prose`.
    # ⛔ UNCONDITIONAL, AND THAT IS A MEASURED CHOICE (round-52 review, P0 — the `not prev_prose`
    # gate this block carried for one commit RE-OPENED, in BOTH arms, the fail-open the ladder exists
    # to close, for every body with a prose line above the item):
    #     `x` + `- 1.` + `  2.     Closes #100`   renderer `code_block` -> CODE
    #         `origin/main` `[]` both arms; the gated head `[100]` in LENIENT and `strict`
    #     (`strict` is the `issue_process_lint` PASS arm, so that is a false PASS in the gate)
    # Measured on a 9,560-body grid: gated = 31 new fail-opens in each arm, un-gated = 0, with
    # 3 new lenient lost closes at the gate and 0 without it. The ONLY body the gate protected is
    # `- Text` + `> <br />` + `> <span>` + `> - *` + `>   2.     Closes #100`, where this module's
    # `_html_here` is False on every line — i.e. a shape it already models wrongly — so a
    # paragraph-state discriminator here protects nothing real. The ordering guards above
    # (`lazy_marker`, §4.4) are what the `Notes` + `2. -` control needs, and they are untouched.
    _inner = body
    while True:
        _inner_col = _content_column(_inner)
        if _inner_col is None:
            break
        _inner = _inner[_inner_col:]
        if not _inner.strip():
            return True
    # ⛔ A BARE MARKER OPENS AN EMPTY ITEM (round 49 review, P1). §5.2 lets a list marker be followed
    # by the END OF THE LINE, and such an item has no content, so it has no paragraph — the same
    # answer the test above gives the `- ` spelling. `_content_column` returns the marker's width + 1
    # for it, so `body` is empty here and this is the ONE place that fact needs no special case.
    # ⛔ A MARKER LINE THAT OPENS NO ITEM HAS NO CONTENT COLUMN TO INSPECT (round 37 review, P2). An
    # ordered marker ≥ 2 that CONTINUES an open paragraph is a lazy continuation, so its `# H` / `***`
    # is INLINE TEXT, not a leaf block — reading it as one ended the paragraph and blanked the real
    # close below (`Notes` + `2. # H` + `3.     Closes #100` renders as ONE `<p>`; `origin/main`
    # credits it). The answer is the CALLER's (`lazy_marker`), because it is the same question the
    # item push asks — and asking it in two places is how the two answers drifted apart (round 39).
    # It is asked ABOVE, before the empty-content test, for the reason stated there.
    if _NO_PARAGRAPH_RE.match(body) or _HTML_BLOCK_INTERRUPT_RE.match(body):
        return True
    # §4.6 type 7 (a complete tag alone on a line) NEVER ends a paragraph, in either direction:
    # it cannot interrupt one, and with none open the line after it is still inline content rather
    # than a fresh block — measured, `<span>x</span>` + `2.     Closes #100` renders as ONE `<p>`
    # in the renderer. So a type-7 line leaves the paragraph OPEN unconditionally (the marker below
    # it is then prose, and `origin/main` credits it).
    if _SETEXT_UNDERLINE_RE.match(body):
        return prev_prose
    return False


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
        base = _content_column(rest)
        opener = _fence_open(rest[base:])
        if opener is None:
            return None
        # A marker on THIS line opens the item in `rest` space; its raw column
        # differs only by the prefix, and both are carried (see `_ListItem`).
        return _Fence(opener, prefix, base, True, len(prefix) + base,
                      _quote_depth(prefix))
    depth = _quote_depth(prefix)
    rest_indent = len(rest) - len(rest.lstrip(" "))
    raw_col = len(prefix) + rest_indent
    # ⛔ THE CONTAINER IS THE INNERMOST ITEM THAT STILL HOLDS THIS LINE, NOT THE TOP OF THE STACK
    # (round 49 review, P1 — a LOST close, a regression against `origin/main`). The dropped-marker
    # branch below answered "the top item is DEEPER than this line, so the fence has NO container" —
    # which is right only when no item is open at or above the fence's own depth. With an OUTER item
    # still open, the fence is INSIDE it and ends with it (§4.5: an unclosed fence ends at "the end of
    # its containing block"):
    #     `- outer` + `  > - inner` + `  ``` ` + `Closes #100`
    #         renderer: the fence is inside the OUTER `<li>`; the column-0 line dedents out of it, so
    #         the fence ends there and `Closes #100` is a top-level paragraph -> PROSE
    #         `origin/main` `[100]`, round-48 head `[]` — the fence ran to EOF and blanked it.
    # Only a stack with NO item at or above this line's depth is the no-container case.
    #
    # ⛔ AND AN ITEM ONLY HOLDS A FENCE THAT SITS INSIDE IT: a fence opener at or before the item's
    # CONTENT column has dedented out of the item (the renderer closes the `<li>` and opens a
    # top-level fence), so binding it to the item would measure the NEXT line as a dedent and free
    # the block's content as prose — the fail-open `_Fence`'s own docstring warns about. Each item is
    # measured in the space its column holds: `rest_col` against a `rest` indent when it lives at this
    # line's depth, `raw_col` against a raw column when it sits outside the fence's blockquote.
    enclosing = None
    for candidate in reversed(list_items):
        if candidate.depth > depth:
            continue            # the fence dropped this item's markers: a fence is never LAZY (§4.5)
        if candidate.depth == depth:
            if rest_indent < candidate.rest_col:
                continue        # the opener is at or before the item's content column
        elif raw_col < candidate.raw_col:
            continue            # …the same test, in RAW space (item outside the fence's quote)
        enclosing = candidate
        break
    if enclosing is None:
        opener = _fence_open(rest, 0)
        if opener is None:
            return None
        return _Fence(opener, prefix, 0, False)
    if enclosing.depth == depth:
        # SAME container: the item's column is already a `rest` column, so the
        # dedent is measured with it directly and no raw number is needed.
        opener = _fence_open(rest, enclosing.rest_col)
        if opener is None:
            return None
        return _Fence(opener, prefix, enclosing.rest_col, True, 0,
                      enclosing.depth)
    # ⛔ AN ITEM AT A SHALLOWER DEPTH THAN THIS LINE IS OUTSIDE THE FENCE'S BLOCKQUOTE, so it is
    # matched BEFORE the quote's markers and its edge is the RAW column (`item_col_raw`). This is the
    # surviving half of round 48's dropped-marker fix: the item still holds a fence opened in a
    # quote NESTED INSIDE it, whose edge really is the raw column. (The other half — the item the
    # fence DROPPED — is the stack walk above: it is skipped, and the next item that still holds the
    # line is used, or none is.)
    residual_col = max(0, enclosing.rest_col - len(prefix))
    opener = _fence_open(rest, residual_col)
    if opener is None:
        return None
    return _Fence(opener, prefix, residual_col, True, enclosing.raw_col,
                  enclosing.depth)


def strip_code(body: str, *, strict: bool = False, stats: dict[str, int] | None = None,
               indented_code: bool = False) -> str:
    """Blank out the parts of a PR body that are CODE, not prose.

    THREE RULES, and the docstring used to claim only the first (round 34 review, P3 — the claim
    outlived the code by three rounds):

    * **fences** — always stripped, in every mode.
    * **a marker line's OWN content** — a gap wider than four spaces after the marker puts that
      content in an indented code block starting on the marker line itself (§5.2 + §4.4), blanked
      in every mode. ⚠️ ONLY where the marker line really opens an item: an ordered marker ≥ 2
      that continues an open paragraph does not (§5.2's consumer rule) and stays PROSE.
    * **a leading indent past `_MAX_FENCE_INDENT`** — `strict` blanks all of it; `indented_code`
      blanks only what the block grammar calls code (past the enclosing item's content column
      plus four). The lenient default blanks neither, because where a 4-space line is code and
      where it is a list item's continuation depends on the enclosing container's content column,
      and getting that wrong in the stripping direction loses a REAL claim — a false pass, the
      worst class here (two review cycles found it: first every indented line, then every
      indented line after a blank line). A warn-only instrument loses nothing by over-reporting a
      quoted keyword; it loses the reader by under-reporting a real one.

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

    `stats` (additive, never consulted by this module) is an OUT parameter for
    callers that must report what the scan could not see. It receives ONE key:
    `hidden_lines` — the number of lines left inside an UNTERMINATED fence, i.e.
    lines this function blanked and nothing closed. That is a miss in the SILENT
    direction (the rest of the body is never examined), so a caller whose
    contract is "say when you could not measure" reports it instead of passing
    over it. It is `0` when every block closed, and the count stops at a block's
    own closer so a well-formed body never reports a hidden line. This is the
    seam `tools/issue_process_lint.py` reads; it is deliberately NOT a return
    type change, so the existing single-value callers are untouched.

    `indented_code` (keyword-only, additive) opts into the OTHER half of the same
    question: blank a line only where the CommonMark block grammar says it is an
    indented CODE block — past 3 columns AND past the enclosing list item's content
    column plus 4. `strict` blanks every indented line instead. The two exist
    together because the callers fail in opposite directions; see the branch at the
    end of the loop.

    ⛔ `strict` IS KEYWORD-ONLY HERE TOO (round 31 review, P3). The round-30 record
    said it was, and it was not — three in-module callers passed it positionally, so
    the record and the artifact disagreed in the one direction that breaks the next
    lane who makes them match. A positional call now raises `TypeError`, which the
    suite pins.
    """
    out: list[str] = []
    fence: _Fence | None = None
    list_items: list[_ListItem] = []
    # The list item the CURRENT line sits inside (None at top level). Set per line, before the
    # line can push its own item — see the `indented_code` branch at the end of the loop.
    container: _ListItem | None = None
    # ⛔ A BLANK LINE DOES NOT CLOSE A LIST ITEM — THE NEXT NON-BLANK LINE DOES (round 47 review: the
    # P1 fail-open the round-46 gate was reaching for, WITHOUT the regressions that gate introduced).
    # `_item_dedented` is a COLUMN test, and a quote-only line's residual is 0 in the item's own
    # space, so the pop below fires on a BLANK line — one line EARLIER than the renderer closes the
    # `<li>`. Popping there is harmless for the fence and marker paths (they see exactly what
    # `origin/main` sees), but the item was then FORGOTTEN, so an HTML block opening on the very next
    # line bound to `html_item = None` and a type-1 block lost the only end-condition it has:
    #     `> - item` + `>` + `>   <pre>` + `>` + `> -     Closes #N`   renderer `code_block`
    # This is NOT the pop guard re-added: the item stays OFF `list_items`, it is carried only when
    # the blank line stayed INSIDE the item's own quote (a deeper marker opens a container the item
    # cannot hold, and then the item really has closed — `- a` + `>`), and it is consulted for
    # exactly ONE non-blank line, to bind a block that opens while still inside the item. That is
    # the renderer's own rule for when the `<li>` ends, and the spec states it outright (§4.6: an
    # HTML block also ends at "the LAST LINE OF THE CONTAINER BLOCK containing the current HTML
    # block", which is why a type-1 `<pre>` with no `</pre>` still ends with its item).
    blank_carried: _ListItem | None = None
    # The spaces after this line's own list marker (0 = not a marker line, or a blank one).
    own_gap = 0
    # Whether THIS line opened a list item. Reset per line: the block below sets it only on the
    # non-fence path, so a line that continues a fence would otherwise carry the previous line's
    # answer into the paragraph-state write at the bottom of the loop.
    pushed = False
    # PARAGRAPH STATE, for §5.2's consumer-side rule (round 34 review, P2). A line is a lazy
    # continuation of the PREVIOUS paragraph only when that paragraph is still open AND in the SAME
    # container — so this is not "the previous line was prose", it is "the previous line was prose,
    # in this blockquote depth, inside this same list item". Measured: both of the narrower readings
    # over-keep, and the renderer says the marker line is CODE in each:
    #     `- Notes\n2.     Closes #100`  -> the ul CLOSES and a new top-level <ol> opens (col 0 is
    #                                       left of the item's content column) -> <pre><code>
    #     `> Notes\n2.     Closes #100`  -> the blockquote closes at depth 0 -> <pre><code>
    # Stored as the last EMITTED line's own container, because `out` keeps no columns.
    prev_prose = False
    prev_quote_depth = 0
    #: The depth of the container the OPEN PARAGRAPH lives in — NOT the last line's own depth
    #: (`prev_quote_depth`, which a `pushed` lazy continuation resets below the paragraph's real
    #: depth). The type-7 leaf veto is asked against this one (round-54 review, P1).
    prev_para_depth = 0
    prev_container: _ListItem | None = None
    # The item that HELD the still-open paragraph when the pop loop dropped it (see
    # `_is_lazy_marker`'s `container is None` note). `prev_container` cannot carry this: it is also
    # read by the dropped-`>` rule, the lazy-indent rule and the item-identity guard, all of which
    # answer correctly off the LIVE stack, and moving it for every one of them costs real closes.
    dropped_paragraph_item: _ListItem | None = None
    # ⛔ AN HTML BLOCK'S OWN TEXT IS READ, NOT HIDDEN (round 43 review, P2 — a LOST close this module
    # introduced AGAINST `origin/main`). §4.6 makes an HTML block a LEAF BLOCK: a `- ` written inside
    # it opens no list, so the line is raw HTML text, not code — the same rule this module already
    # states for the paragraph question (`_leaves_no_paragraph`: "WHAT AN HTML BLOCK DOES NOT DO IS
    # HIDE ITS OWN TEXT … only FENCED content is blanked"). `own_code` did not know that, so any
    # wide-gap marker line inside the block was blanked:
    #     `<div>` + `-     Closes #100`      renderer `html_block` (text), `origin/main` `[100]`, head `[]`
    # MEASURED at this head over the 32-opener grid (indents 0-8, every marker, gaps 1-9): 90 lost
    # closes per opener family (`<div>`, `<p>`, `<table>`, `<details><summary>`), none of them a code
    # block. ⛔ THE STATE CARRIES THE BLOCK'S OWN CLOSER, NOT JUST "OPEN" (round 44 review, P2 — a
    # fail-open the first version created by ending every block at a blank line): types 2-5 end at
    # `-->`, `?>`, `>` and `]]>`, type 1 at its closing tag, and only type 6 at a blank line, so
    # `<!-- c -->` + `-     Closes #100` (renderer `code_block`) was CREDITED. `_html_block_open`
    # owns both the start set (types 1-6, type 1 included) and the closer.
    html_closer: frozenset[str] | None = None
    # ⛔ THE BLOCK LIVES IN A CONTAINER, NOT IN A COLUMN (round 45 review, P2 — a lost close this
    # state machine introduced one commit earlier by binding the block to the OPENER'S INDENT). §4.6
    # gives an html block no indentation end-condition at all, and a top-level block may legally open
    # at 0-3 spaces, so "the next line is at a smaller column" is not "dedented out of a container":
    # `"   <div>"` + a column-0 marker line lost the close against both the parent head and
    # `origin/main` (1,440 such bodies). The block is bound to the ITEM it opened in (None at top
    # level, and then it can never be dedented out of one) plus the quote depth it opened at.
    html_item: _ListItem | None = None
    html_depth = 0
    # Whether the LAST line ENDED a fence — by a closer or by dedenting out of its container
    # (see the read site below).
    #: Set by a line this pass BLANKED as code that was nevertheless a list marker with prose
    #: content INSIDE an open item — the ITEM, not a bare flag, because a paragraph only survives for
    #: a line that has not dedented out of that item yet (a column-0 line after a list ends it and
    #: opens its own block). Loop-carried: the NEXT line's html-state block asks the question.
    _blanked_opens_paragraph = None
    prev_fence_end = False
    # Lines the CURRENTLY OPEN block has hidden from the scan. Reset when a block
    # opens or closes, so the value at the end is the unterminated block's tail.
    hidden_lines = 0
    for raw in body.splitlines():
        line = raw.expandtabs(4)
        indent = len(line) - len(line.lstrip(" "))
        # The dropped-item fact describes exactly ONE open paragraph, so it is stale the moment that
        # paragraph ends — clear it here, once, rather than at every `prev_prose = False` write.
        if not prev_prose:
            dropped_paragraph_item = None
        prefix, rest = _split_prefix(line)
        # The line's indent AFTER its blockquote prefix: `close_base` is measured
        # there, which is the space the strict arm below reports in.
        rest_indent = len(rest) - len(rest.lstrip(" "))
        # ⛔ THE ANSWER BELONGS TO THE LINE ABOVE, SO IT IS READ AND CLEARED HERE (round 49 review,
        # P1). `prev_fence_end` is set only by the two places a fence can END, and a fence line
        # `continue`s, so reading it per line — rather than at the point of use — keeps it from
        # leaking past the one line it describes.
        after_fence_end = prev_fence_end
        prev_fence_end = False
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
                    # ⛔ A LINE THAT CLOSES A CONTAINER-LESS FENCE LEAVES THE NEXT ONE AT TOP LEVEL
                    # (round 49 review, P1 — the LENIENT arm of the fence-closer regression). The
                    # default arm does not blank indented code, deliberately (see `strip_code`), and
                    # that decision's REASON — "whether a 4-space line is code depends on the
                    # enclosing container's content column" — does not reach this position: the
                    # fence had no container, so the line after it has none either, and §4.4's
                    # paragraph protection needs a paragraph an open fence cannot be:
                    #     `> - item` + `` ``` `` + `` ``` `` + `    Closes #100`
                    #         renderer: TWO top-level blocks, `<pre><code></code></pre>` then
                    #         `<pre><code>Closes #100</code></pre>`. `origin/main` blanked the marker
                    #         line through the phantom fence it re-opened HERE; the round-48 binding
                    #         made this a closer instead, so that accident went away.
                    prev_fence_end = True
                    _blanked_opens_paragraph = None
                    fence = None
                # A closer is the LAST line the block hides; content before it
                # counts (it is unmeasurable until the block ends), a closed
                # block's tail does not.
                hidden_lines = 0 if fence is None else hidden_lines + 1
                out.append("")
                prev_prose = False
                continue
            # The container ended, so this line is FRESH: it may open a new fence
            # (review c10-2), and — outside the block — it is also where the list
            # item stack is tracked again.
            # …and THIS line is the fresh one, so the flag is set for it as well as for the next —
            # but ONLY when the fence had a CONTAINER to lose. A fence that ended by dropping its own
            # `>` (in_list False) leaves the line inside a container it never owned, and blanking a
            # 4-space line there is a LOST CLOSE the module ranks second-worst:
            #     `**bold**` + `</div>` + `- outer` + `  ~~~` + `> ``` ` + `    Closes #100`
            #         the dedent lands on a line the renderer keeps (an html block's raw text);
            #         `origin/main` credits it and the closer path below is what the F1 shape needs.
            prev_fence_end = fence.in_list
            _blanked_opens_paragraph = None
            after_fence_end = fence.in_list
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
        # ⛔ THE PER-LINE STATE MUST BE RESET ON A WHITESPACE-ONLY LINE (round 38 review, P1 — a
        # CRASH, and a regression against `origin/main`). `pushed`, `container` and `own_gap` were
        # assigned only inside `if line.strip():`, so a whitespace-only line carried the PREVIOUS
        # line's `pushed=True` forward — and the `indent_code` branch had already popped the items
        # the flag referred to. Two shapes then raise IndexError, both of which `main` answers:
        #     `- [ ] b` + `        ` (8 spaces) + blank          strict=True  -> pop from empty list
        #     `- [ ] criterion` + `     ` + `     ` + `Closes #N` strict=True -> list index out of range
        # A crash is not a narrow blast radius in this tool: `sweep()` has no per-PR guard, so one
        # such body aborts `--sweep` for EVERY PR and the run exits 1 against a documented 0/3/2
        # contract. Reset at the top of the iteration, which is where "this line's own" begins.
        if not rest.strip():
            # ⛔ A BLANK LINE CLOSES AN EMPTY ITEM (round 49 review, P1 — the `indented_code` arm of
            # the bare-marker parity). §5.2 gives an item's content column as marker width + 1 when the
            # item is EMPTY, and a blank line then ends the list: the renderer's own answer is
            #     `- ` + `` + `    Closes #100`   -> `<ul><li></li></ul>` then `<pre><code>`
            # so the 4-space line is a TOP-LEVEL code block, not the item's continuation. Leaving the
            # empty item on the stack made `container` a 2-column item the renderer had closed, and the
            # `+ _MAX_FENCE_INDENT` allowance then credited the block. An item WITH content is
            # untouched: a blank line inside it does not end it, and the following line may still be
            # its paragraph (`- item` + `` + `    Closes #100` renders a second `<p>` inside the
            # `<li>` — `origin/main` credits it and this must not shadow that).
            if list_items and not list_items[-1].has_content:
                list_items.pop()
            pushed = False
            container = None
            own_gap = 0
            _lazy_push = False
            # The item a quote-only line leaves is still open in the renderer until the NEXT
            # non-blank line — but only if the line stayed inside that item's own quote, and only if
            # it carries a marker at all: a wholly empty line closes the blockquote, so it carries
            # nothing.
            blank_carried = (list_items[-1]
                             if (line.strip() and list_items
                                 and list_items[-1].has_content
                                 and _quote_depth(prefix) <= list_items[-1].depth)
                             else None)
        # ⛔ A QUOTE-ONLY LINE IS BLANK CONTENT, AND THE END CONDITION — NOT THE POP — IS WHAT MUST
        # LEARN IT (round 47 review, F1/F2 — the fail-open this branch fixes, without the two
        # regressions its first version introduced). `>`, `>  ` and `> >` are NON-EMPTY as raw lines
        # and empty of content after their container prefixes, so §4.6's type-6/7 blank-line
        # end-condition never fired and the block stayed open over the next marker line. That is
        # fixed where the content is MEASURED (the `_consume_blockquote_markers` test below), not by
        # suppressing the pop: a blank line does not end an item, but `_item_dedented` is a COLUMN
        # test and gating the pop broke the shapes next door in both directions —
        #   * `- a` + `>` + `  ``` ` + `Closes #100` (renderer `fence`) was newly CREDITED: the item
        #     stayed on the stack, `_fence_candidate` bound the fence to it (`in_list=True`), and the
        #     next COLUMN-0 line read as a fence-ending dedent and was freed as prose — 43 bodies of
        #     the review's 1,296-body grid;
        #   * `- a` + `>     ` + `>     ` raised `IndexError: pop from empty list` in `strict=True`
        #     and `indented_code=True` (336 (body, mode) pairs), because the gate also skipped the
        #     per-line ASSIGNMENT of `pushed` / `own_gap` / `container` / `_lazy_push` without
        #     resetting them — `sweep()` has no per-PR guard, so one such body aborts `--sweep`.
        # The pop stays `origin/main`'s; `blank_carried` is what keeps the type-1 repro fixed.
        if line.strip():
            while list_items and (
                    _item_dedented(line, list_items[-1])
                    # ⛔ AN EMPTY ITEM HAS NO PARAGRAPH, SO NOTHING IN IT CAN BE LAZY (round 48
                    # review, P1's sibling — see `_ListItem.has_content`). `_item_dedented` is a
                    # COLUMN test, so a line that DROPS the item's `>` marker while staying indented
                    # past its content column does not dedent by that rule, and the item stayed open
                    # over a line the renderer had already closed the `<li>` for. §5.2's lazy
                    # continuation continues an open PARAGRAPH; an empty item has none, so a dropped
                    # marker leaves the item outright — which is why round 40's "fewer markers is not
                    # itself a dedent" does not reach this case (it was about a lazy line).
                    #     `> - ` + `    ``` ` + `<span>` + `-     Closes #100`
                    #         renderer: the blockquote closes on line 2, `    ``` ` is a top-level
                    #         indented CODE block and `<span>` a fresh type-7 `html_block` ->
                    #         `[100]`. With the item still on the stack, line 2 read as the item's own
                    #         continuation (`prev_prose=True`), the leaf was refused, and the wide-gap
                    #         marker below was blanked: `[]` — 14 lost closes, all of this shape.
                    or (not list_items[-1].has_content
                        and _consume_blockquote_markers(line, list_items[-1].depth)[0]
                        < list_items[-1].depth)):
                _popped = list_items.pop()
                # …and if THAT item was holding the paragraph open above this line, the fact has to
                # outlive the pop: the renderer keeps the paragraph (and the `<li>`) on a LAZY
                # continuation, so the marker below is measured against a container the stack no
                # longer has. See `_is_lazy_marker`'s `container is None` note (round 49 review, P1).
                # ⛔ …AND THE PARAGRAPH'S OWN ITEM IS NOT ALWAYS THE LAST ONE ON THE STACK (round-51
                # review, P0). The identity term `prev_container is _popped` was written for the pop
                # that takes the paragraph's own item, but a line can dedent out of a DROPPED item
                # one line after a blanked nested marker abandoned it — `prev_container` then points
                # at the abandoned item and the pop takes its PARENT, so the fact was not recorded and
                # `_is_lazy_marker` fell back to the vacuous `container is None` arm:
                #     `- a bullet` + `     - x` + `Summary.` + `2.     Closes #100`
                #         renderer `<ul><li>a bullet<ul><li>x Summary.</li></ul></li></ul>` then an
                #         `<ol start="2">` whose wide-gap content is `<pre><code>` -> CODE
                #         (pinned by `test_a_PHANTOM_item_needs_FOUR_columns_and_the_SETEXT…`)
                # `prev_prose` is what makes that safe: the record only claims a paragraph was open
                # above this line, which is the condition the record is read under anyway.
                # ⚠️ GATED ON `prev_prose`, AND THE GATE IS LOAD-BEARING HERE (measured: un-gating it
                # changes 93 of 60,000 fuzz bodies, +98 lenient / +39 strict lost closes). The record
                # only says anything while a paragraph is open above this line, which is what the
                # gate states.
                # ⛔ THIS GATE IS ALSO THE ONE LEVER THAT FIXES THE `- 2.` RESIDUAL, AND THE COST ABOVE
                # IS ITS COST. Un-gating this pop MEASURED takes that body from `[F, F, T, T]` to
                # `[F, F, F, F]`, and `issue_process_lint.claims()` from `[100]` to `[]` — the arm-1
                # false PASS is closed at the price of the +98 lenient / +39 strict lost closes
                # quoted above. So the residual is DECLARED OPEN AS A TRADE, not blocked for want of
                # a mechanism: #11358 owns it, and its fixer must evaluate that trade rather than
                # assume the record is inert. (What IS inert is the INDENT pop's gate — 0 of
                # 60,000. And dropping THIS pop's `dropped_paragraph_item` assignment moves the body
                # the WRONG way — to `[T, T, T, T]`, a false PASS in all four arms. Removing the
                # INDENT pop's assignment changes none of the arms, per #11358.)
                if prev_prose and prev_container is _popped and _popped.real:
                    dropped_paragraph_item = _popped
            # An item that was empty on its marker line gains CONTENT from the first line that stays
            # inside it — and only then: a lazy line never reaches here (it dedents by the column
            # test above and pops the item), which is exactly the distinction the renderer makes.
            # `blank_carried` reads this to decide whether the `<li>` survives the blank line.
            if rest.strip() and list_items and not list_items[-1].has_content:
                list_items[-1].has_content = True
            # The container THIS line sits in, captured BEFORE the line can push its own
            # item: an indented code block's first line is very often itself a list marker
            # (`    - [ ] x`), and reading the stack after the push measures that line
            # against ITSELF — so the quoted example looked like a continuation of an item
            # it had just opened (#11078 round 29 review, P2: the fix did nothing for the
            # shape that motivated it).
            container = list_items[-1] if list_items else None
            rest_col = _content_column(rest)
            # ⛔ A LAZY CONTINUATION OPENS NO ITEM, SO IT MUST NOT PUSH ONE (round 37 review, P2 —
            # the second half of the same fact). Leaving the push in made the NEXT line be measured
            # against a content column the renderer never created, which blanked a real close even
            # once the leaf predicate was fixed: `Notes` + `2. # H` + `3.     Closes #100` needs both
            # changes, and the reviewer measured the pair (predicate only: `[]`; both: `[100]`).
            # The RAW column, for `container.rest_col`'s `>=` term (round 39); the RESIDUAL one is
            # the `rest`-space twin §4.4's distance is measured in (round 41).
            _, _, line_col_raw = (
                _fence_line_column(line, container.depth) if container is not None
                else (0, rest_indent, rest_indent))
            _lazy_push = (_is_lazy_marker(rest, prefix, prev_prose, prev_quote_depth,
                                          container, prev_container, line_col_raw, rest_indent,
                                          indent, dropped_paragraph_item)
                          or _dropped_quote_is_lazy(prefix, prev_quote_depth, container,
                                                    prev_container, rest_indent, indent))
            pushed = (rest_col is not None and not _lazy_push
                      # ⛔ A BARE `-` UNDER AN OPEN PARAGRAPH IS A SETEXT UNDERLINE, NOT AN ITEM
                      # (§4.3, and §5.2's "the first item must not be blank"). It ends the
                      # PARAGRAPH — the line above becomes a heading — but it opens no `<li>`, so
                      # pushing one measured the next line against a content column the renderer
                      # never created and credited an indented code block:
                      #     `**bold**` + `-` + `    Closes #100`   renderer `<h2>` then
                      #     `<pre><code>`; `origin/main` blanked it, this head credited it.
                      # …and only in the SAME container: a `-` that dropped out of an item (or a
                      # quote) has no paragraph above it to underline, so it IS an item —
                      #     `> - item` + `- ` + `    Closes #N`   renderer `<li>Closes #N</li>`
                      # ⛔ "THE SAME CONTAINER" IS AN ITEM IDENTITY **AND** A DEPTH (round 49 review, P1 —
                      # the exclusion moved BOTH ways: a lost close in both arms, and a fail-open in
                      # both arms). `container is prev_container` is vacuous when both are `None`,
                      # which is exactly a bare marker that DROPPED a blockquote — so a `- ` under a
                      # quoted paragraph read as that paragraph's underline while the renderer had
                      # already closed the quote (§4.3: a setext underline cannot be a lazy
                      # continuation), and the line under it lost its container:
                      #     `> Notes` + `- ` + `  ``` ` + `Closes #100`
                      #         renderer: quote, then `<ul><li><pre><code></code></pre></li></ul>`,
                      #         then a `<p>` — the marker line is PROSE (`[100]` on `origin/main`)
                      #         head `[]`: the fence became top-level and ran to EOF
                      #     `> Notes` + `- ` + `  <pre>` + `2.     Closes #100`
                      #         renderer `<code_block>`; `origin/main` `[]`, head `[100]`
                      # The paragraph's depth under the `>` rule is `prev_quote_depth` when the last
                      # line stayed at it (round 38's own rule). ⛔ NOT when a `pushed` lazy continuation
                      # reset it below the paragraph — that is what `prev_para_depth` carries (round-54),
                      # and this term is deliberately still the LINE's, because an underline is a fact
                      # about the line.
                      and not (prev_prose and container is prev_container
                               and _quote_depth(prefix) == prev_quote_depth
                               and _SETEXT_UNDERLINE_RE.match(rest.strip())))
            own_gap = _marker_gap(rest)
            if pushed:
                # …and whether that item is REAL: a marker line that is a paragraph continuation
                # (4+ columns past the paragraph's own base) opens no `<li>` (§4.4), so the item is
                # a phantom the state machine needs for its columns and nothing else. See `real`.
                # ⛔ 4+, NOT 3+ (round-51 review, P2 — a FAIL-OPEN in BOTH arms at the boundary
                # column). `_MAX_FENCE_INDENT` is 3 and §4.4's "cannot interrupt a paragraph" needs
                # an INDENTED CODE BLOCK, i.e. FOUR columns past the content column; at exactly 3 the
                # renderer still opens a real `<li>`. Marking it phantom suppressed the
                # `dropped_paragraph_item` record mechanism 11 relies on, and `_is_lazy_marker` then
                # fell back to `container is None -> True`, crediting the wide-gap claim below it:
                #     `Notes` + `   - x` + `Summary.` + `2.     Closes #100`
                #         renderer: `<p>Notes</p><ul><li>x Summary.</li></ul>` then an `<ol>` whose
                #         wide-gap content is `<pre><code>` -> CODE; credited in both arms, so also
                #         in `strict` — the `issue_process_lint` PASS arm.
                # Every other reader of this threshold spells it `>` (`_leaves_no_paragraph`,
                # `indent_code`) — this was the only `>=`, and `real`'s own docstring already said
                # "at 0-3 columns the marker DOES open a `<ul>`".
                _continuation = bool(
                    prev_prose and rest_indent > _MAX_FENCE_INDENT
                    and (container is None
                         or rest_indent > container.rest_col + _MAX_FENCE_INDENT))
                list_items.append(
                    _ListItem(_quote_depth(prefix), rest_col,
                              len(prefix) + rest_col,
                              bool(rest[rest_col:].strip()),
                              not _continuation))
        # ── §4.6 html-block state: the state FOR THIS LINE, advanced once per line ────────────────
        # Computed here, before every `continue` below, so the fence branch and the code branches all
        # read the same answer (a state advanced inside a branch is a bug waiting for the next shape).
        # ⛔ A LINE THAT ENDS ONE BLOCK CAN OPEN THE NEXT (round 47 review, P2 — the opener scan below
        # used to run only on a line that found NO block open, so a line that LEFT the block's
        # container while carrying a new opener opened nothing, and the marker line under it was
        # blanked by the wide-gap rule):
        #     `> <div>` + `> >` + `  <div>` + `-     Closes #100`   renderer `html_block` (the
        #     surviving block is the `  <div>`), `origin/main` `[]`, gated head `[100]`, and the
        #     gated head was right by accident — its blank-line test ended the first block a line
        #     early. 21 such bodies in the review grid, all in the LOST-CLOSE direction.
        # The scan stays OUT of the two in-block endings on purpose: a closer line (types 1-5) and a
        # blank line (types 6/7) end the leaf block AT that line, so anything after it still belongs
        # to it and a new block may only start on the NEXT line.
        _scan_for_opener = False
        #: Did THIS line open the leaf block (`_opened` answered)? A block's OPENER line is its own
        #: raw text, so it must survive `strict` — see the note at `indent_code`.
        _html_opened_here = False
        # ⛔ A LINE INSIDE AN ALREADY-OPEN LEAF BLOCK IS THAT BLOCK'S RAW TEXT, SO IT OPENS NO
        # PARAGRAPH (round 49 review, P1 — a FAIL-OPEN in `strict=True`, the PASS arm). The prose
        # write below asks `_leaves_no_paragraph`, which answers about BLOCK BOUNDARIES: an html
        # block does not hide its own text (round 43), so a ` ``` ` line written inside one is read
        # and reported as prose-leaving-a-paragraph-OPEN. The block is a LEAF — there is no paragraph
        # in it — and the phantom then survived the very line that left the block's container:
        #     `> <!--` + `> ``` ` + `    - x` + `> 2.     Closes #100`
        #         renderer: the quote's type-2 block (its raw text is ` ``` `), then a top-level
        #         `<pre><code>` whose content is the marker line -> CODE
        #         `origin/main` `[]`, head `[100]` in `strict`
        # The OPENER line is the opposite case and is deliberately NOT flagged: `<br />` +
        # `-     Closes #100` is pinned `[100]`, and there the line is the block's start, not its text.
        _html_leaf_text = False
        if html_closer is not None:
            if (_quote_depth(prefix) < html_depth
                    or (html_item is not None and _item_dedented(line, html_item))):
                _html_here = False            # left the container that held the block
                html_closer = None
                _scan_for_opener = True       # …so this line is FRESH, and may open its own
            else:
                _html_here = True
                _html_leaf_text = True        # …and it is a LEAF block's own raw text, not prose
                # ⛔ AN HTML BLOCK IS A LEAF, SO NO ITEM OPENS INSIDE ITS RAW TEXT (round 49 review —
                # the round-43/48 decisions, applied to the ITEM STACK and not only to the fence
                # path). A marker written in the block's text was counted as an item, and its content
                # column then held the NEXT real block:
                #     ` ` + `    - x` + `<br />` + `~~~` + `  > - inner` + `` + `>     Closes #100`
                #         renderer: the last line is a `<pre><code>` inside a new blockquote — CODE —
                #         and the phantom 2-column item made the tool credit it. Popping HERE (rather
                #         than skipping the push) is what keeps a marker line that OPENED a block
                #         (`- <div>`) intact: that line's block survives across iterations.
                if pushed and list_items:
                    list_items.pop()
                    pushed = False
                if html_closer:
                    # ⛔ THE CLOSER IS TESTED IN THE BLOCK'S OWN SPACE, NOT AFTER EVERY `>` IN THE
                    # PREFIX (round-51 review, P0 — a FAIL-OPEN in `strict`, the PASS arm, and the
                    # same term the blank-line arm one screen down already takes). §4.6 type 4's end
                    # condition is "a line containing the string `>`", and `rest` has already consumed
                    # the `>` that IS that string: `_html_block_open('<!DOCTYPE x')` returns
                    # `frozenset({'>'})` and `_html_block_ended({'>'}, '')` is False, so the block never
                    # ended and `    Closes #100` below it was credited while the renderer put it in
                    # `<pre><code>`:
                    #     `<!DOCTYPE x` + `>` + `    Closes #100`
                    #         renderer `<pre><code>` -> CODE; `origin/main` `[]`, the round-50 head
                    #         `[100]` in `strict`
                    # Consuming at most the `html_depth` the block OPENED at is the rule the blank
                    # line uses, and it keeps the case that made the raw reading wrong: a `>` deeper
                    # than the block's own depth is LITERAL TEXT (`> <!DOCTYPE` + `> more` keeps the
                    # block open, because `more` carries no `>`).
                    if _html_block_ended(
                            html_closer,
                            _consume_blockquote_markers(line, html_depth)[1]):
                        html_closer = None    # types 1-5: this line carries the block's own closer
                # ⛔ "BLANK" IS MEASURED AT THE BLOCK'S OWN DEPTH, NOT AFTER EVERY `>` IN THE PREFIX
                # (round 47 review, P2 — 180 LOST closes against `origin/main`, every one of them a
                # body the renderer keeps as prose). §4.6's leaf block ends at a BLANK line, and a `>`
                # that is LITERAL TEXT inside the block is not one — the round-43 decision one screen
                # up ("an HTML block's own TEXT is read, not hidden") says the same thing from the
                # content side. Consuming every marker in the prefix (`rest.strip()`) made a `>`
                # written at the block's own depth or deeper read as an end condition:
                #     `<div>` + `>` + `-     Closes #100`            renderer `html_block` (one block),
                #         `origin/main` `[100]`, gated head `[]`
                #     `> <div>` + `> >` + `> -     Closes #100`     same, `origin/main` `[100]`, head `[]`
                # The distinction the fence path already makes (`_fence_line_column`, "consume at most
                # `depth` markers — a `>` inside a block is text") is the one that is right here: a
                # line is blank for THIS block only when it carries no marker beyond the `html_depth`
                # the block opened at. At depth 0 only a raw-block line ends it; at depth 1 `> >` does
                # not, `>` does. The ITEM term needs nothing extra — a line that dedents out of the
                # item already ended the block in the branch above.
                elif not _consume_blockquote_markers(line, html_depth)[1].strip():
                    _html_here = False        # types 6/7: a blank line AT THIS DEPTH ends the block
                    html_closer = None
        else:
            _scan_for_opener = True
        if _scan_for_opener:
            _html_here = False
            _html_rest = (rest[container.rest_col:]
                          if (container is not None and rest_indent >= container.rest_col
                              and _quote_depth(prefix) >= container.depth)
                          else rest)
            _opened = (_html_block_open(_html_rest) if line.strip() else None)
            if _opened is None and line.strip():
                # …and the item must still HOLD this line: a line that dedented out of it ended
                # that paragraph, which is how a column-0 `</div>` after a list opens its own block
                # (`- item` + `- ` + `    - x` + `</div>` + `- ` + a wide-gap marker is an
                # `html_block` in the renderer, so the marker is its TEXT — 8 closes measured).
                _blanked_still_open = (_blanked_opens_paragraph is not None
                                       and _blanked_opens_paragraph in list_items)
                # ⛔ A LINE THAT ADDS A `>` STARTS A NEW CONTAINER, SO IT INTERRUPTS THE PARAGRAPH
                # (round-54 review, P0 — a FAIL-OPEN in the lenient arm, introduced by moving the
                # content ladder below the ordering guards). §5.1 lets a blockquote opener interrupt
                # a paragraph, so the type-7 veto ("type 7 cannot interrupt a paragraph") has nothing
                # to protect here; passing the paragraph state anyway refused the block and left the
                # paragraph open across it, so the wide-gap marker below read as its lazy
                # continuation:
                #     `Notes` + `2. -` + `> <br />` + `     -     Closes #100`
                #         renderer: `<p>` then a blockquote whose `html_block` is `<br />`, then a
                #         `code_block` -> CODE; `[]` at `3861f0a10` (the tree `c83cd2803` merged) and
                #         at `origin/main` `d69faaf38`; `[100]` at the BRANCH POINT `80c912244`, and the
                #         round-54 head credited it
                # The depth term is the rule `_is_lazy_marker`'s docstring already states, applied
                # where the leaf question is asked. Measured: that body `[]` in both arms, the
                # controls (`Notes` + `2. -` + `> x` + …, `Notes` + `2. -` + `2.     Closes #100`,
                # `x` + `- 1.` + `2.     Closes #100`) unchanged, and a 60,000-body sweep against
                # the previous head moving 25 bodies with 0 new fail-opens and 0 new lost closes — a
                # claim about THAT 60,000-body grid only; round 55's gate has a residual the grid does
                # not compose, declared at `_leaves_no_paragraph`.
                _opened = _html_block_leaf(
                    _html_rest,
                    (prev_prose or _blanked_still_open)
                    and _quote_depth(prefix) <= prev_para_depth)
            if _opened is not None:
                _html_opened_here = True
                html_item = container
                # ⛔ A BLOCK OPENED ON A LAZY LINE IS STILL INSIDE THE `<li>` THE RENDERER KEPT, SO
                # THAT ITEM IS ITS CONTAINER (round 51 review, P1 — a FAIL-OPEN in all three arms,
                # and the reason the §4.6 `</tag>` arm was unsafe). `container` is the live STACK
                # top, and the column test that pops a LAZY continuation empties it before this
                # line is read — but the renderer keeps the `<li>` open, so §4.6's "last line of the
                # CONTAINER block" end condition is the item's last line, not EOF:
                #     `- a` + `x` + `  </ul>` + `> > 2.     Closes #100`
                #         renderer: `<li>` holds the paragraph and an `html_block` whose raw text
                #         is `</ul>`; the block ENDS with the item, so the last line is a NEW
                #         blockquote whose wide-gap item is `<pre><code>` -> CODE.
                #         `origin/main` `[]`; the head credited `[100]` in ALL THREE arms.
                # `dropped_paragraph_item` is exactly the fact "the paragraph above is still in that
                # `<li>`" (the pop records it, `_is_lazy_marker` already reads it), and the
                # `blank_carried` term below asks the SAME question for the blank-line spelling — so
                # one loop asks it once and neither term can drift from the other. A line that
                # DEDENTS out of the candidate is not inside it, which is what keeps the type-6
                # spelling that legitimately ends the list (`<div>` at column 0, renderer
                # `html_block`, `origin/main` `[100]`) reading as the block's own text.
                if html_item is None:
                    for _candidate in (blank_carried, dropped_paragraph_item):
                        if _candidate is None:
                            continue
                        _carried_consumed, _ = _consume_blockquote_markers(line, _candidate.depth)
                        if (_carried_consumed >= _candidate.depth
                                and not _item_dedented(line, _candidate)):
                            html_item = _candidate  # the block opened inside the item after all
                            break
                html_depth = _quote_depth(prefix)
                _html_here = True
                _html_leaf_text = True
                # ⛔ THE CLOSER IS TESTED IN THE BLOCK'S OWN SPACE, NOT AFTER EVERY `>` IN THE
                # PREFIX (round-51 review, P0 — a FAIL-OPEN in `strict`, the PASS arm, and the same
                # term the blank-line arm one screen down already takes). §4.6 type 4's end condition
                # is "a line containing the string `>`", and `rest` has already consumed the `>` that
                # IS that string: `_html_block_open('<!DOCTYPE x')` returns `frozenset({'>'})` and
                # `_html_block_ended({'>'}, '')` is False, so the block never ended and the 4-column
                # line below it was credited while the renderer put it in `<pre><code>`:
                #     `<!DOCTYPE x` + `>` + `    Closes #100`
                #         renderer `<pre><code>` -> CODE; `origin/main` `[]`, the round-50 head
                #         `[100]` in `strict`
                # Consuming at most the `html_depth` the block OPENED at is the rule the blank-line
                # arm uses, and it keeps the case that made a raw reading wrong: a `>` deeper than the
                # block's own depth is LITERAL TEXT (`> <!DOCTYPE` + `> more` keeps it open, because
                # `more` carries no `>`).
                html_closer = (None if _html_block_ended(
                    _opened, _consume_blockquote_markers(line, html_depth)[1]) else _opened)
        if rest.strip():
            blank_carried = None    # the carry covers exactly ONE non-blank line
        # ⛔ AN HTML BLOCK IS A LEAF, SO A LINE INSIDE ONE OPENS NO FENCE (round 48 review — the
        # round-43 decision "an HTML block's own TEXT is READ, not hidden" applied to the FENCE path).
        # §4.6 makes types 1-7 leaf blocks: ` ``` ` written inside one is the block's raw TEXT, and the
        # renderer opens no fence there. Opening one anyway blanked every line after it — a fence runs
        # to its closer or to EOF — so a body the renderer reads as ONE `html_block` lost its close:
        #     `<span>` + `>   <pre>` + `> - ` + `   ``` ` + `2.     Closes #100`
        #         renderer ONE `html_block` (the type-7 `<span>` has no blank line to end it), so
        #         `origin/main` credits `[100]`; the fence blanked the marker instead.
        # `_html_here` is this line's own answer, computed above, so the two state machines agree.
        fence = None if _html_here else _fence_candidate(prefix, rest, list_items)
        if fence:
            # ⛔ AN ITEM THE FENCE ABANDONED MUST LEAVE THE STACK — DECLARING IT GONE IS NOT ENOUGH
            # (round 49 review, P1 — the FAIL-OPEN the round-48 fence binding introduced). The
            # `_fence_candidate` branches above decide the fence's CONTAINER; this pops every item
            # DEEPER than the line's own blockquote, because a line that dropped the item's `>`
            # cannot be inside it. Leaving them behind made the line AFTER the closer be measured
            # against a stale item, which suppressed `own_code` and credited an indented code block:
            #     `> - item` + `` ``` `` + `` ``` `` + `    Closes #100`
            #         renderer `<pre><code>`; `origin/main` `[]`; the round-48 head `[100]`
            while list_items and list_items[-1].depth > _quote_depth(prefix):
                list_items.pop()
            hidden_lines = 0
            out.append("")
            prev_prose = False
            continue
        # `rest_indent` is the ONLY indentation test: `line` was `expandtabs(4)`d
        # a few lines up, so a tab can never survive into `rest` — the
        # `rest.startswith("\t")` this clause used to carry was unreachable and
        # read as a live guard (#10634 review P3, twice). A tab-indented line is
        # already a `rest_indent >= 4` line by the time it gets here.
        #
        # ⛔ TWO STRICTNESSES, BECAUSE THE TWO CALLERS FAIL IN OPPOSITE DIRECTIONS.
        # `strict` blanks EVERY line indented past the allowance. That is the right
        # answer for a PASS arm (`issue_process_lint`'s arm 1): a claim may only rest
        # on a shape that survives as prose at a content column, and an indented
        # `Closes #N` under a `Refs #N` must not resolve it.
        # `indented_code` blanks only what the CommonMark BLOCK grammar calls an
        # indented CODE block — past 3 columns AND past the enclosing list item's
        # content column + 4 (§5.2: a continuation line is measured from the content
        # column, not from the margin). A WIP arm needs exactly this and nothing more:
        # a checklist QUOTED in an indented code block is an example and must not
        # clear the heading it sits under, while `1. Criteria` + `    - [ ] a` is a
        # NESTED LIST — prose, and a real criterion. Blanking on the width alone (the
        # `strict` rule) drops that real criterion; not blanking at all reads the
        # quoted example as one. Neither default is safe, so the caller chooses.
        # ⚠️ DECLARED RESIDUAL (round 30 review, P3) — this is the block grammar's
        # INDENTATION test, not all of §4.4: an indented code block cannot interrupt a
        # PARAGRAPH, and this rule blanks one that does (`text\n    - [ ] a` renders as
        # prose here and is hidden). That is the false-positive / silent-miss direction,
        # and it is at PARITY with the parent head rather than a regression, so it is
        # declared rather than fixed: teaching the rule about paragraph interruption
        # needs the block state the fence pass does not keep, and guessing it the other
        # way re-opens the class-A false PASS this mode exists to close.
        # ⚠️ DECLARED RESIDUAL 2 (round 33 review, P3) — §5.2's OTHER interruption rule, the one on the
        # CONSUMER side: a list item can interrupt a paragraph only if it is a bullet or starts at 1,
        # so `To be derived.\n2. [ ] x` renders as a PARAGRAPH and `CHECKBOX_RE` still counts the box.
        # Measured at 160 of 160 such bodies, identical at `ee01b70f6e`; declared for the same reason as
        # residual 1 — deciding it needs paragraph state this pass does not keep.
        # ⛔ A LINE THIS FUNCTION BLANKS AS CODE OPENS NO ITEM, SO THE ITEM IT JUST PUSHED IS
        # UNDONE (round 31 review, P2 — a class-A false PASS). The `container` capture above kept this
        # CODE LINE from being measured against the item it opens itself, but the item stayed on the
        # stack, so the NEXT line of the same block was measured against a PHANTOM content column and
        # came out as prose:
        #     `- item` + blank + `      - [ ] example` + `        - [ ] nested example`
        #   renderer: ONE `<pre><code>` holding BOTH boxes (the blank line is load-bearing: WITHOUT it
        #             the boxes are a paragraph continuation, §4.4 — the round-31 comment printed the
        #             body without it and claimed a code block it does not get)
        #   before:   the first line was blanked, the second was kept -> arm 2 saw an issue with WIP and
        #             no criteria as CLEAN (rc 0), and `in_flight_markers` read a QUOTED marker as live
        #   after:    both blanked
        # The sweep lives in the test, not here, because a prose number is exactly what rotted twice
        # already: `test_a_two_line_indented_example_is_hidden_by_BOTH_of_its_lines` enumerates the
        # family (markers `-`/`*`/`1.`/`10.` x gaps 1,2,4,5,8 x first-line indents 4,5,6,7,8,10 x
        # second line +2/+3/+4) and asserts, over all of it, that no body the PARSER puts in a
        # `code_block` is counted and that no prose body is over-hidden — so the property is checked
        # on every run instead of being quoted. (Two earlier revisions of this comment carried other
        # lanes' figures — "224 of 720", then "177 of 177" — that do not reproduce from the
        # enumeration they name; the numbers were never the evidence, the sweep is.)
        # The FENCE branch above deliberately KEEPS its push — a marker line that opens a fence really
        # does open its item.
        # ⛔ AND THE MARKER LINE ITSELF CAN BE CODE, WHICH NO INDENT TEST CAN SEE (round 32 review,
        # P2). `rest_indent` is the indentation BEFORE the marker, so it is 0 for `-     [ ] x` — but
        # §5.2 collapses that 5-space gap to ONE column, so the content sits 4 columns past the item's
        # content column and §4.4 makes it an indented CODE block, starting on the marker line:
        #     `-     [ ] x`                 renderer `<pre><code>[ ] x</code>`
        #     `-     IN FLIGHT: 3.2c`       renderer `<pre><code>…</code>`
        #     `-     Closes #100`           renderer `<pre><code>…</code>`
        # All three were read as PROSE — a quoted checklist cleared a criteria-less `## Satisfies`, a
        # quoted marker read as live, a quoted close credited (in BOTH arms, so `strict` did not save
        # it either). Arm-independent: this is a fact about the renderer, not a strictness choice.
        # `gap <= 4` stays prose, which is the control the pin asserts.
        # ⛔ AND THE MARKER LINE'S OWN CONTENT IS ONLY CODE IF THE MARKER LINE OPENS AN ITEM AT ALL
        # (round 34 review, P2 — a REGRESSION `own_code` introduced). The producer rule above says
        # where a 5+-space gap puts the content; it says nothing about whether the line is a list
        # item, and §5.2's CONSUMER rule does: an ordered marker ≥ 2 cannot interrupt a paragraph.
        # The sweep in the test used `…\n\n<line>`, i.e. a paragraph that had already ENDED, so the
        # one shape where the two rules disagree was structurally invisible to it:
        #     `Notes\n2.     Closes #100`   renderer <p>Notes\n2.     Closes #100</p>  PROSE
        #     measured before this fix       parse_closing_refs -> []   (parent and `main`: [100])
        # `prev_prose` is read from the line we last EMITTED, which is the only definition that stays
        # true across the three blanks above: a fence line and a blanked code line both append `""`,
        # a blank body line emits its own blank text, and a paragraph line emits `raw` — so a code
        # line or a blank line can never pose as an open paragraph.
        # ⛔ TWO WAYS A MARKER LINE FAILS TO OPEN AN ITEM, AND ONLY ONE WAS KNOWN (round 36 review,
        # P2). §5.2's consumer rule is the first (`an ordered marker ≥ 2 cannot interrupt`). The
        # second is §4.4's: an INDENTED CODE BLOCK cannot interrupt a paragraph either — so a marker
        # line indented past the fence allowance under an open paragraph is a lazy continuation too,
        # whatever its marker is. Missing that kept blanking a real close (`Notes` + `    -     Closes
        # #100` renders as PROSE and `origin/main` credits it, so it was a REGRESSION, not parity).
        # ⛔ THE INDENT HALF IS MEASURED FROM THE CONTAINER'S CONTENT COLUMN, NOT "no container"
        # (round 37 review, P2). §4.4's rule is about the PARAGRAPH, not about the outermost
        # container: at content-column+4 inside an item — or on a lazy line that dropped its `>` —
        # an open paragraph makes the line its continuation, and bounding the arm to
        # `container is None` blanked the real close (`- Notes` + `      -     Closes #100` renders as
        # `<p>`, and `origin/main` credits it). The flip is at 4 columns PAST the content column and
        # stays there at every indent 0-12, so it is a distance, not an absolute column.
        # The two halves also need different container terms, which is why they are two terms:
        # a lazy INDENT continuation may drop its blockquote marker (it is indentation that continues
        # the paragraph), while the MARKER half must keep the depth term (a marker at another depth
        # opens its own list).
        # ⛔ ONE COORDINATE SPACE, NOT TWO (round 39 review, P2 — a regression against
        # `origin/main` in the warn direction). The container's `rest_col` is expressed in the space
        # of the line that OPENED the item, so comparing it against this line's `rest_indent` (which
        # is measured after this line's own quote prefix) mixes the two spaces and blanks real
        # closes: `- > q` + `  > 2.     Closes #100` renders as PROSE inside the item, and the item
        # is still open — 0 >= 2 was simply the wrong pair of numbers. `_fence_line_column` is the
        # existing conversion between them (it consumes at most the container's own markers), which
        # is why this is a third reader of one definition rather than a fourth formula.
        # ⛔ THE TWO TERMS BELOW MEASURE IN DIFFERENT SPACES, SO THEY CANNOT SHARE ONE NUMBER
        # (round 41 review, P2 — class A, fail-open, arm 2 of `issue_process_lint` included).
        # `_fence_line_column` returns BOTH spellings, and they differ by the marker width the
        # container's own prefix consumed — so for any `container.depth >= 1` the previous single
        # `[2]` read (the RAW content column) asked §4.4's distance question against
        # `container.rest_col`, which is expressed in the space `rest` lives in (`_ListItem`).
        # The comparison then fired `len(prefix)` columns EARLY and suppressed the rule that blanks a
        # marker line whose own content sits 4+ columns past the item's content column — the
        # fail-open direction, where a quoted `Closes #N` was credited, a quoted box cleared a
        # criteria-less `## Satisfies`, and a quoted `IN FLIGHT:` read as live. Measured on
        # `> - item` + `>      -     Closes #100` (rest indent 5, item content column 2 = exactly the
        # §4.4 distance, renderer `code_block`): `[100]` before, `[]` after.
        # `_lazy_indent` gets the RESIDUAL indent (index 1, the same coordinate `rest_col` holds and
        # the same one the fence branch compares against `close_base`), while `_lazy_marker` keeps
        # the RAW column (index 2), which is what round 39's `- > q` + `  > 2.     Closes #100` pin
        # needs (`0 >= 2` was the wrong pair of numbers there, and it stays fixed).
        if container is not None:
            _, _line_residual, _line_col = _fence_line_column(line, container.depth)
        else:
            _line_residual = _line_col = rest_indent
        _indent_base = container.rest_col if container is not None else 0
        # ⛔ THE PARAGRAPH'S CONTAINER IS NOT ALWAYS THE ITEM (round 42 review — a lost close this
        # module introduced AGAINST `origin/main`). When the open paragraph lives in a blockquote
        # NESTED INSIDE the list item, the item is not what holds it, and `container.depth` (the
        # item's) stops the marker consumption BEFORE that quote's `>`. The distance is then measured
        # to the wrong column and a lazy paragraph continuation is blanked as code:
        #     `- > q` + `  >     -     Closes #100`
        #         renderer ONE `<p>` inside the inner quote (`q\n    -     Closes #100`)
        #         origin/main `[100]`; head `[]` — 60 such bodies per marker family, all lost
        # The paragraph's depth here is `prev_quote_depth` — it already carries lazy continuation, and
        # ⛔ that is exactly the case it does NOT cover: a `pushed` lazy continuation resets it BELOW
        # the paragraph it continues, which is why `prev_para_depth` exists (round-54 review, P1).
        # Where the question is genuinely the PARAGRAPH's depth, `prev_para_depth` is the term; here
        # the question is where the line's own markers placed it.
        # it is DEEPER than the item and the line is written at or one marker below that depth, the
        # line is inside that quote: consume the line's own markers and measure from the quote's
        # content column, which in that space is 0. Swept over 22,176 + 9,072 bodies against
        # `MarkdownIt("commonmark")`: lost closes 120 -> 0 and 45 -> 0, no fail-open moved, 0
        # regressions.
        # ⛔ ONE LEVEL OF NESTING WAS ONE LEVEL SHORT (round 43 review, P2 — a lost close against
        # `origin/main` again, in the family this branch was written for). The equality above only
        # covered a paragraph ONE quote deep inside the item. At TWO (`- > > q`) the real lazy
        # continuation drops ONE `>` — the renderer keeps it in the same `<p>` — and neither this
        # branch nor `_dropped_quote_is_lazy` (which requires the dropped markers to be the ITEM's
        # own) answered for it, so `own_code` blanked it:
        #     `- > > q` + `  >     -     Closes #100`
        #         renderer ONE `<p>` (`q\n    -     Closes #100`); `origin/main` `[100]`, head `[]`
        # Measured at this head over 47,250 bodies of that family: 1,025 lost closes, every one
        # credited by `origin/main` and every one an `inline` token.
        # ⛔ THE ONE-MARKER BOUND IS LOAD-BEARING. Dropping ANY number of markers credits bodies the
        # renderer puts in `<pre><code>` (measured: +650 fail-opens on `- > > > q` + a depth-1 line),
        # while `> container.depth` keeps the item's own `>` from being re-measured.
        _line_depth = _quote_depth(prefix)
        if (container is not None
                and prev_quote_depth > container.depth
                and container.depth < _line_depth <= prev_quote_depth
                and _line_depth >= prev_quote_depth - 1):
            _line_residual = _fence_line_column(line, _line_depth)[1]
            _indent_base = 0
        _lazy_indent = (prev_prose
                        and _line_residual > _indent_base + _MAX_FENCE_INDENT
                        and container is prev_container
                        # ⛔ A DEEPER CONTAINER IS NOT A LAZY INDENT CONTINUATION (round 40). A lazy
                        # indent line may DROP its `>`; one that ADDS a marker starts a new block
                        # (§5.1), and reading it as a continuation credited a keyword the renderer
                        # puts in `<pre><code>` (`Notes` + `2. ` + `>     -     Closes #100`).
                        and _quote_depth(prefix) <= prev_quote_depth)
        # ONE OWNER FOR THE MARKER QUESTION (round 40 review, P3 — the docstring claimed a single
        # owner while this line computed a second, differently-shaped answer; that second copy is
        # what hid round 40's finding: it diverged from the push on a dropped-`>` lazy line).
        _lazy_marker = _is_lazy_marker(rest, prefix, prev_prose, prev_quote_depth,
                                       container, prev_container, _line_col, rest_indent, indent,
                                       dropped_paragraph_item)
        ordered_continuation = (_lazy_indent or _lazy_marker
                                or _dropped_quote_is_lazy(prefix, prev_quote_depth, container,
                                                          prev_container, rest_indent, indent))
        own_code = own_gap > 4 and not ordered_continuation and not _html_here
        # ⛔ `indent_code` NO LONGER TAKES `_html_here` WHEN `strict` IS SET (P1 — a FAIL-OPEN in
        # the `strict` PASS arm, the arm `issue_process_lint` refuses `lands` with). `_html_here` is
        # this module's own HTML-block state machine, and `strict` reads the CARRIER body where a
        # lenient-only match is a PASS — and an OVER-OPEN guess (the unsafe direction: it believes a
        # leaf block is open when the renderer has none) must not be able to EXEMPT a line from the
        # arm whose whole contract is "blank every indented line" (this file states that contract
        # two screens down: "The `strict` half deliberately does NOT take the term"). It became a
        # shared prefix with the leaf-block work, and the consequence is the arm crediting a keyword
        # the renderer puts in `<pre><code>`:
        #     `-` + `    code` + `<br />` + `     -     Closes #100`
        #         renderer `<ul><li>code<br /><ul><li><pre><code>Closes #100` -> CODE
        #         `[]` at the branch point `origin/main` `80c912244`; `[100]` in `strict` AND
        #         `indented_code` at `1177c6708` (still at the tip `3861f0a10`) — a fail-open `main`
        #         carried until it fixed `strict` there in #11330 (`a022f03ce`)
        # `strict` blanked `    code` (its own declared over-blank), which left no paragraph open, so
        # `<br />` opened a PHANTOM type-7 block and `_html_here` then exempted the line below it.
        # ⛔ THIS TERM IS NARROWER THAN "STRICT IS UNCONDITIONAL", AND THE DIFFERENCE IS A DECLARED
        # TRADE, NOT A RESTORE: an html block's own INDENTED text (a visible `<div>`/`<span>` raw
        # text, renderer PROSE) is blanked by `strict` again — exactly pre-#11186's answer, and
        # round 30's recorded "a PASS arm blanks every indented line" choice. The close is still
        # READ by the lenient and `indented_code` arms, so `classify` downgrades it to a warning
        # rather than hiding it. Widening "read, not hidden" (round 44/45) to a block's INDENTED
        # text is a REOPEN of that choice, homed on #11156 — not a behaviour to change here.
        # ⛔ The SAME over-open exposure still exists on `own_code` above (a column-0 wide-gap marker
        # under a phantom block), where the term is load-bearing for a REAL block's own text; that
        # residue is #11287/#11156's, and this comment must not be read as "no `_html_here`
        # exemption remains in `strict`".
        # The lenient and `indented_code` branches keep the term: there `_html_here` protects the
        # #11156 class — a leaf block's own TEXT is read, which is a fail-open only for the
        # preformatted tag family and is the declared, round-44/45-pinned residual.
        indent_code = rest_indent > _MAX_FENCE_INDENT and (
            # ⛔ the block's OWN OPENER line is its raw text and is never blanked by `strict`: it is
            # where the state below is LEARNED, so blanking it loses the block (and the `_html_here`
            # answer every later line depends on). Every line INSIDE the block is still `strict`'s.
            (strict and not _html_opened_here) or (not _html_here and indented_code
                       # ⛔ §4.4's PROTECTION IS THE ARM'S OWN RULE, NOT `strict`'s (round 50 review,
                       # P1 — LOST CONTENT in the one arm `in_flight_markers` and the comment-body
                       # read go through). "An indented code block cannot interrupt a paragraph", so a
                       # 4-column line under an OPEN paragraph is that paragraph's continuation and
                       # the renderer keeps it — the arm blanked it anyway, and a live `IN FLIGHT:`
                       # marker or a real checklist criterion in it then vanished SILENTLY:
                       #     `Notes` + `1. ` + `    IN FLIGHT: 3.2c — branch fix/x`
                       #         renderer ONE `<p>` -> PROSE; `origin/main` KEEPS the line, the
                       #         round-49 head returned `Notes\n1. \n` (the marker is dropped).
                       # `rest.strip()` is part of the term: a CONTENT-BLANK line (`>     `) is a
                       # BLANK line to the block grammar and ends the paragraph instead of
                       # continuing it. And the paragraph must be in the container this line falls
                       # into, not one it has dedented out of, and the line must NOT be past that
                       # container's OWN code threshold (§5.2 measures from the content column) —
                       # `-     Closes #100` + `    code` + `    ---` + `      2.      Closes #100`
                       # renders the marker inside `<pre><code>`, and the quoted exemplars this arm
                       # exists to hide stay hidden. The `strict` half deliberately does NOT take
                       # the term (a PASS arm blanks every indented line — round 30's choice).
                       and not (prev_prose and rest.strip()
                                and container is prev_container
                                and _quote_depth(prefix) == prev_quote_depth
                                and (container is None
                                     or rest_indent <= container.rest_col + _MAX_FENCE_INDENT))
                       and (container is None
                            # ⛔ AN EMPTY ITEM HAS NO PARAGRAPH TO CONTINUE, SO IT DOES NOT EXTEND
                            # §5.2's ALLOWANCE (round 49 review, P1). The `+ _MAX_FENCE_INDENT`
                            # term is "this line is the content column's continuation", and an item
                            # with no content has nothing to continue — the renderer ends it at the
                            # blank line and the 4-space line below is a TOP-LEVEL code block:
                            #     `- ` + `` + `    Closes #100`   renderer `<ul><li></li></ul>` then
                            #     `<pre><code>`; this was a PRE-EXISTING fail-open for the `- `
                            #     spelling and is the parity the bare-marker rule above now gives `-`
                            or not container.has_content
                            or rest_indent > container.rest_col + _MAX_FENCE_INDENT))
            # ⛔ WITH NO CONTAINER AND NO OPEN PARAGRAPH, §4.4's PROTECTION HAS NOTHING TO PROTECT —
            # AND DIRECTLY AFTER A CONTAINER-LESS FENCE NOTHING CAN EXPLAIN A 4-SPACE LINE EXCEPT
            # INDENTED CODE (round 49 review, P1 — the LENIENT arm of the fence-closer regression).
            # The default arm deliberately does NOT blank indented code (see `strip_code`: losing a
            # real claim is worse than over-reporting), and that decision stands for the shapes it
            # was measured on — an indented block after a paragraph or a blank line still reports
            # (`note:` + `` + `    Closes #N` is pinned by its own test). This term is the narrower
            # position the decision's reason does not reach: the fence that just closed had no
            # container, so the renderer's next block is a TOP-LEVEL `<pre><code>` and nothing else:
            #     `> - item` + `` ``` `` + `` ``` `` + `    Closes #100`
            #         renderer `<pre><code>`; `origin/main` blanked it through the phantom fence it
            #         re-opened on the closer line, so the round-48 binding left the LENIENT arm
            #         crediting it alone — the one regression the fence-closer fix does not settle.
            # `not prev_prose` is §4.4's own protection: a 4-column line under an OPEN paragraph is
            # that paragraph's lazy continuation, not code (`> ``` ` + `  text` + `    Closes #N` is
            # one `<p>` in the renderer, `origin/main` credits it, and the paragraph survives the
            # fence that ended above it).
            or (container is None and after_fence_end and not _html_here and not prev_prose))
        blank = own_code or indent_code
        if blank:
            # ⛔ WHETHER THE LINE OPENS AN ITEM DEPENDS ON WHICH RULE BLANKED IT (rounds 31-33).
            # * `indent_code` — the line is code INSIDE THE CONTAINER ALREADY OPEN, or indented past
            #   the allowance with NO container at all (`    -     item` at top level is a top-level
            #   indented code block, so the renderer opens no `<li>`). Either way it opens no item: the
            #   push is UNDONE, or the next line of the same block is measured against a phantom
            #   content column and comes out as prose — the class-A false PASS round 31 closed, which
            #   round 32 RE-OPENED for the sub-case where both rules fire on one line.
            # * `own_code` alone — a marker line at a LEGAL position whose own CONTENT is code
            #   (`-     item`). The renderer DOES open the `<li>` (content column 2, the code inside it),
            #   so the push STAYS and the following line is measured against a real item: popping it
            #   made a genuine nested list look like a top-level indented block and blanked a real
            #   criterion (`-     item` + `     - [ ] x` renders as a nested `<ul>`).
            if pushed and indent_code:
                _popped_by_indent = list_items.pop()
                # ⛔ THE POPPED ITEM IS WHERE THE PARAGRAPH LIVES, AND THE BLANK BRANCH `continue`s
                # — so `prev_container` is updated HERE rather than at the end of the loop (round-51
                # review, P0). `strict` blanks a 4+-column MARKER line, and §5.2 says such a marker
                # at up to three columns past the content column opens a REAL item that INTERRUPTS
                # the paragraph above it. Leaving `prev_container` at the parent made the next line's
                # wide-gap marker read as that paragraph's lazy continuation:
                #     `- outer` + `    - x` + `  2.     Closes #100`
                #         renderer `<li>outer<ul><li>x</li></ul><ol start="2"><li><pre><code>`
                #         -> CODE; `origin/main` `[]`, the round-50 head `[100]` in `strict=True`
                # The `real and prev_prose` terms keep the fact to its own case: a 4+-column marker
                # with NOTHING open is an indented block, and its pop moves no paragraph.
                # ⛔ THE RECORD IS STILL GATED ON `prev_prose` AT BOTH POPS, AND THAT IS THE STATE
                # THIS HEAD SHIPS — the `- 2.` family below is NOT fixed here. What the round-52 fix
                # changed was WHERE the fact is recorded (at the POP that owns the paragraph), NOT
                # the gate itself: both pops are still gated on `prev_prose`, and for THIS one that
                # gate is measured INERT (un-gating it changed 0 of 60,000 fuzz bodies — see the
                # note at the end of this block):
                #     `- 2.` + `    - x` + `x` + `2.     Closes #100`
                #         renderer `<pre><code>` -> CODE; `[]` lenient / `[100]` in `strict` at this
                #         head, and `issue_process_lint.claims()` returns `closing: [100]` — STILL a
                #         false PASS in the arm-1 gate. It is the residual #11358 owns (OPEN; its
                #         repro is this exact body, and it measures the root as `own_code`'s
                #         `not ordered_continuation` term, not the record), and `origin/main`
                #         carries the same `strict` answer. ⚠️ THE MECHANISM THIS NOTE USED TO
                #         PROPOSE — "the record would have to survive the one-line clearing at the
                #         top of the loop" — is FALSIFIED for this body: disabling that clearing
                #         leaves it at `[F, F, T, T]` (measured), and #11358 reports the same for
                #         dropping THIS pop's record assignment. Do not re-derive from that
                #         sentence. ⛔ BUT ONE LEVER DOES WORK, AND IT IS NOT IN THIS POP: un-gating
                #         the DEDENT pop's `prev_prose` gate takes this body to `[F, F, F, F]`
                #         (`claims()` `[]`) — measured — at the +98 lenient / +39 strict lost-close
                #         price its own note quotes. So #11358 is a TRADE to be evaluated, not a
                #         mechanism still to be discovered.
                #         PINNED at `tests/test_closes_landing.py::test_the_INDENT_pops_DECLARED_
                #         strict_false_PASS_is_PINNED`, so it cannot move silently. The mutation
                #         table measured against this body is recorded on **#11358** — read it
                #         there, once, at the issue that owns the fix — and do NOT read a green on
                #         that pin as evidence that a candidate term works.
                # ⚠️ BOTH TERMS ARE GATED ON `prev_prose`, MEASURED: un-gating this one changes 0 of
                # 60,000 fuzz bodies (it is unobservable — the record only matters when a paragraph
                # was open above, which is what the gate says), and un-gating the DEDENT pop at the
                # top of the loop costs +98 lenient / +39 strict lost closes (round-52 review, P2).
                if _popped_by_indent.real and prev_prose:
                    prev_container = _popped_by_indent
                    dropped_paragraph_item = _popped_by_indent
            out.append("")
            # ⛔ AN INDENTED CODE BLOCK CANNOT INTERRUPT A PARAGRAPH (§4.4), SO A LINE BLANKED ONLY BY
            # THE INDENTATION RULE DOES NOT CLOSE THE PARAGRAPH OPENED ABOVE IT (round 48 review — the
            # companion the fence/HTML-block fix needs to stay on the fail-closed side). Clearing the
            # state made the NEXT type-7 leaf look like a fresh block, and a ` ``` ` inside that phantom
            # block then re-opened a fence over the rest of the body:
            #     `> Notes` + `> Notes` + `    ``` ` + `<br />` + ` ``` ` + `2.     Closes #100`
            #         renderer: ONE `<p>` (lines 0-3) then a FENCE holding the marker -> the renderer
            #         says CODE, and the phantom `<br />` block credited it — a FAIL-OPEN in `strict`.
            # A CONTENT-BLANK line (`>     `) really is a blank line for the block grammar, so it does
            # end the paragraph — hence `rest.strip()`. `own_code` is excluded because a marker whose
            # own content is code does start a block (§5.2's wide gap).
            # ⛔ …AND A BLANKED LINE THAT OPENED ITS OWN CONTAINER STILL ENDS IT (round 49 review, P1 — a
            # FAIL-OPEN in `strict=True`, the `issue_process_lint` PASS arm). §4.4's "an indented code
            # block cannot interrupt a paragraph" holds only when the paragraph is IN THE SAME
            # CONTAINER (§5.1: a blockquote opener does interrupt one), so the carry needs a container
            # term — without it a quote opener was read as a paragraph continuation and the new list's
            # wide-gap content was CREDITED as prose:
            #     `Notes` + `>     code` + `2.     Closes #100`
            #         renderer: `<p>Notes</p>`, then the quote's code block, then a NEW `<ol start="2">`
            #         whose wide-gap content is `<pre><code>` -> CODE (both arms)
            #         `origin/main` `[]`, round-48 head `[100]` in `strict=True`
            # A DEEPER line is a new container; a line at the same or a shallower depth may still be
            # the paragraph's lazy continuation, which is what the round-48 carry is for.
            # ⛔ …AND A LINE THAT OPENED AN ITEM OF ITS OWN ENDED IT (round-51 review, P0 — a
            # FAIL-OPEN in `strict=True`, and it made the PASS arm MORE permissive than the finding
            # arm). §5.2 lets a bullet item interrupt a paragraph, so a 4-column MARKER line under an
            # open one both ends that paragraph and opens a nested item — `strict` blanks it (the
            # indent rule), and the carry then kept the outer paragraph "open" across the very line
            # the renderer used to close it. The wide-gap marker below was read as a lazy
            # continuation, so `strip_code` kept it and credited it:
            #     `- outer` + `    - x` + `  2.     Closes #100`
            #         renderer `<li>outer<ul><li>x</li></ul><ol start="2"><li><pre><code>` -> CODE
            #         `origin/main` `[]`, round-50 head `[100]` in `strict=True`
            # The item's own push is popped above (the `pushed and indent_code` arm), which is why the
            # term is the LINE's fact and not the stack's: it says "this line started a block", which
            # is exactly what a paragraph continuation is not.
            prev_prose = bool(prev_prose and rest.strip() and indent_code and not own_code
                              and _quote_depth(prefix) <= prev_quote_depth)
            if not prev_prose:
                # …and the state must describe the LAST line measured, which is this one: the
                # paragraph's depth is only carried while a paragraph is actually open above it.
                prev_quote_depth = _quote_depth(prefix)
                prev_container = container
                # ⛔ …BUT A BLANKED MARKER LINE STILL OPENS ITS ITEM'S PARAGRAPH, AND A TYPE-7 LEAF
                # MAY NOT INTERRUPT IT (round 49 review, P2 — a strict-arm FAIL-OPEN, and the
                # narrowest form the fact takes). `strict` blanks EVERY indented line, so a nested
                # MARKER whose content is ordinary prose is blanked too — while the renderer puts
                # that content in a `<li>` as a PARAGRAPH. The tool then answered "no paragraph
                # open", and the next `<br />` was read as a legal type-7 opener (§4.6: type 7
                # cannot interrupt a paragraph, so with one open it is INLINE HTML) and swallowed
                # the rest of the body as the block's raw text — crediting a wide-gap marker:
                #     `*` + `    - x` + `<br />` + `---` + `1.     Closes #100`
                #         renderer `<ul><li><ul><li>x<br /></li></ul></li></ul>` + `<hr />` +
                #         `<ol><li><pre><code>` -> CODE; the strict arm credited `[100]`.
                # This is a FLAG and not a `prev_prose` write on purpose: the shape it protects is
                # one narrow question (may the next line open a TYPE-7 LEAF?), while `prev_prose`
                # drives the code/prose verdicts — and writing it here cost real closes (measured,
                # `- outer` + `- item` + `- ` + `    - x` + `</div>` + `- ` + a wide-gap marker).
                # ⚠️ DECLARED RESIDUAL (round 49, P3, `strict` arm only, AT PARITY WITH `origin/main`):
                # the flag is dropped when the line dedents out of the item that holds the paragraph,
                # because a column-0 block opener really does end it — but a TYPE-7 line (`<br />`) is
                # INLINE in an open paragraph and does not dedent at all, so
                # `*` + `    - x` + `<br />` + `---` + a wide-gap marker is still credited here while
                # the renderer holds it in `<pre><code>`. `origin/main` credits it too; fixing it
                # needs the item POP to know the line is type 7, which is the deeper change this round
                # reports rather than guesses (1 of 5,920 random bodies; 0 in the enumerated grid).
                # The gap term is §5.2's: content past the fourth column opens a CODE block, so
                # nothing is open there.
                # ⛔ …AND THE MARKER MUST BE ONE THE RENDERER REALLY OPENS, which is the CONTAINER
                # term: at top level a 4-column marker is an INDENTED CODE BLOCK (`    - x` renders
                # `<pre><code>- x</code></pre>`), so it leaves no paragraph — and claiming one made a
                # type-7 leaf refuse to open where the renderer opens it, blanking a real close:
                #     `    - x` + `</div>` + `1.     Closes #100`
                #         renderer `<pre><code>` then an `html_block` holding the marker -> PROSE
                #         `origin/main` credits it; claiming a paragraph here lost 8 such closes.
                _col = _content_column(rest)
                if (container is not None and _col is not None and rest[_col:].strip()
                        and _marker_gap(rest) <= _MAX_FENCE_INDENT):
                    _blanked_opens_paragraph = container
            continue
        _blanked_opens_paragraph = None
        out.append(raw)   # prose: keep it, keywords and all
        # A blank line emits a blank `raw`, and a blank line ENDS a paragraph — so the state is
        # `raw.strip()`, not "we took the prose branch".
        # ⛔ AND "PROSE-EMITTED" IS NOT "A PARAGRAPH IS OPEN": a heading, a thematic break, a setext
        # underline, an HTML block and an INDENTED CODE BLOCK are all written to `out` and all end
        # the paragraph, so the next line cannot lazily continue it (`_leaves_no_paragraph`). Four
        # families of credited quotations came from reading only `raw.strip()` here.
        # ⛔ THE DEPTH CARRIED FORWARD IS THE DEPTH OF A PARAGRAPH THAT WAS **ALREADY** OPEN (round 49
        # review, P1 — a FAIL-OPEN the honest blank-line reset introduced). The carry below exists to
        # keep a LAZY line at its paragraph's depth; a line that OPENS a fresh paragraph has no
        # previous paragraph to keep, so carrying the previous line's depth onto it invents a
        # container that is not there — the same class round 39 closed from the other side. It only
        # looked right while the per-line state was STALE (a blanked line never wrote it):
        #     `>     ` + `Notes` + `> 2.     Closes #100`
        #         renderer: the quote's code block, a top-level `<p>Notes</p>`, then a NEW
        #         `<ol start="2">` whose wide-gap content is `<pre><code>` -> CODE
        #         `origin/main` `[]`; reading `Notes` as a continuation of a depth-1 paragraph made the
        #         depth-1 marker below look like the same container -> `[100]` in `strict=True`.
        _paragraph_was_open = prev_prose
        # ⛔ …AND A LEAF BLOCK'S OWN RAW TEXT LEAVES NO PARAGRAPH EITHER (see `_html_leaf_text`).
        prev_prose = (bool(raw.strip()) and not _html_leaf_text
                      and not _leaves_no_paragraph(
                          rest, rest_indent, container, prev_prose,
                          _lazy_push if line.strip() else False))
        # ⛔ THE PARAGRAPH'S CONTAINER DEPTH, WHICH IS NOT THE LINE'S OWN PREFIX DEPTH (round 38
        # review, P2 — a false PASS). Two shapes put a paragraph at a depth its line's prefix does
        # not show, and both made `_lazy_marker`'s depth term answer "same container" when the
        # renderer opens a NEW list:
        #     `- > q` + `  2.     Closes #100`   the `> ` sits AFTER the marker, so the paragraph is
        #                                        at depth 1 while the prefix reads 0 -> CODE
        #     `> Notes` + `lazy` + `2.     …`   the lazy line has NO `>` prefix, so recording depth 0
        #                                        lost the open quote -> CODE
        # So: a marker line contributes the depth of its own CONTENT, and a line shallower than the
        # paragraph it continues keeps the paragraph's depth (that is what "lazy" means).
        # ⛔ A LINE THAT OPENS A CONTAINER OR AN ITEM ENDS THE OLD ONE'S PARAGRAPH (round 39 review,
        # P2 — class A, both arms). Carrying the depth on `prev_prose` alone kept a blockquote's
        # depth past the line that CLOSED it, so `- > q` + `- x` + `    2.      Closes #100` measured
        # the claim at depth 0 against a remembered depth 1, failed the "same container" term and
        # blanked a line the renderer keeps as plain inline text (`[100]` on `origin/main`).
        # A LAZY line still keeps the paragraph's depth — that is what lazy means — so the carry is
        # gated on the line NOT having started something of its own.
        # ⛔ AND THE CONTENT'S `>` IS ONLY A CONTAINER WHEN THE LINE OPENED AN ITEM (round 39 review,
        # P2). `Notes` + `2. > q` is ONE paragraph (`2.` cannot interrupt), so the `>` there is
        # INLINE TEXT: counting it made the claim line below look shallower than the "container" and
        # blanked it (`[100]` at `2a76c68888` and on `origin/main`), while the sibling that really does
        # open a quote — `Notes` + `2. > q` + `> 2.      Closes #100` — must stay CODE.
        _inner = rest[_content_column(rest):] if _content_column(rest) is not None else ""
        _own_depth = _quote_depth(prefix)
        if pushed:
            _own_depth += _quote_depth(_split_prefix(_inner)[0])
        # ⛔ AND THE CARRY IS ONLY FOR PARAGRAPH-CONTINUATION TEXT (round 39 review, P1). An
        # unconditional `max()` lost 2,040 closes `origin/main` credits: §4.6's lazy continuation
        # applies to TEXT only, so a shallower line that STARTS ITS OWN BLOCK closes the quote and
        # carrying the depth onto it invents a container that is gone —
        #     `> q` + `2. x` + `   2.     Closes #100`  line 2 opens an <ol start="2">, so line 3's
        #                                               paragraph belongs to the NEW item (PROSE)
        # — while `> Notes` + `lazy` + `2.     Closes #100` must still read CODE, because `lazy`
        # starts nothing and the quote is still open. `not pushed` and `_OWN_BLOCK_START_RE` are the
        # two spellings of "this line started something of its own"; both are required, because a
        # line can start a heading or a break without `_content_column` seeing an item. ⛔ IT IS NOT
        # `_BLOCK_START_RE`: that pattern counts ANY leading `<`, so a lazy type-7 line
        # (`<span>x</span>`) reset the carried depth to its own (0) — a `strict`-arm fail-open.
        # ⛔ THE DROPPED-ITEM FACT BELONGS TO THE PARAGRAPH THAT WAS OPEN BEFORE THIS LINE, and a
        # line in a DEEPER container opens a new one (§5.1: a blockquote opener does interrupt a
        # paragraph), so the fact is stale from here on. Without this, a quote opened after an item
        # was abandoned inherited the item's column and refused a genuine lazy continuation:
        #     `- Notes` + `> Notes` + `> 2.     Closes #100`
        #         renderer: `<li>Notes</li>` then ONE `<blockquote><p>` holding both of the last two
        #         lines -> PROSE; `origin/main` and the round-49 head both credit it.
        if _own_depth > prev_quote_depth:
            dropped_paragraph_item = None
        prev_quote_depth = (max(_own_depth, prev_quote_depth)
                            if (_paragraph_was_open and prev_prose and not pushed
                                and not _OWN_BLOCK_START_RE.match(rest))
                            else _own_depth)
        # ⛔ THE PARAGRAPH'S OWN CONTAINER DEPTH IS NOT THE LAST LINE'S (round-54 review, P1).
        # `prev_quote_depth` is reset by a `pushed` line, and a LAZY continuation that the model
        # records as an item (`> Notes` + `     - x`) resets it BELOW the paragraph it continues —
        # so the next line at the paragraph's own depth then read as "a new container" and the
        # type-7 veto was dropped although the renderer needs it:
        #     `> Notes` + `     - x` + `> <br />` + `     -     Closes #100`
        #         renderer ONE `<p>` in the blockquote -> PROSE; `c83cd2803` credited it, the
        #         depth-checked head blanked it (4 bodies in a 200k sweep)
        # The carry is the same `max()` the line-depth rule uses, WITHOUT the `pushed` / own-block
        # resets — those describe the LINE, and this variable describes the PARAGRAPH.
        prev_para_depth = (max(_own_depth, prev_para_depth)
                           if (_paragraph_was_open and prev_prose)
                           else _own_depth)
        # The paragraph lives in the item this line OPENED when it opened one (`- Notes` puts its
        # text inside the `<li>`), not in the item it sat in before. ⛔ AND WHEN THIS LINE'S PUSH WAS
        # UNDONE BY THE INDENT RULE, THE ITEM IT OPENED IS THE ONE THAT HOLDS IT (round-51 review,
        # P0 — the other half of the lazy-marker finding): `strict` blanks a 4+-column MARKER line,
        # and the paragraph the renderer opens there is the `<li>` that line created, so the next
        # line continues THAT item even though the stack no longer has it:
        #     `1. x` + `    - x` + `<span>` + `>     Closes #100`
        #         renderer: the `<span>` cannot interrupt the nested item's paragraph, so it is
        #         INLINE text and the marker line is a top-level code block -> CODE
        #         (`origin/main` `[]`; recording the outer item instead let the type-7 leaf open and
        #         credited the marker as the block's raw text)
        prev_container = (list_items[-1] if pushed else container)
    if stats is not None:
        stats["hidden_lines"] = hidden_lines if fence is not None else 0
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
    text = _strip_inline_code(strip_code(body or "", strict=strict), strict=strict)
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
    text = _strip_inline_code(strip_code(body or "", strict=strict), strict=strict)
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
    text = _strip_inline_code(strip_code(body or "", strict=True), strict=True)
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
