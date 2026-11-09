#!/usr/bin/env python3
"""issue_process_lint — two ADVISORY checks that make an issue's own process visible.

The defect this exists for (measured 2026-11-05)
------------------------------------------------
A lane's process lived in its **dispatch brief**. A brief is not attached to the issue and the next
lane never reads it, so an issue picked up by a lane whose dispatch forgot a step is worked without
that step — and the issue cannot say so.

The sharper half: **an issue states a PROBLEM, and its completion criteria do not exist at creation.**
They are produced by scoping, which happens *after* the issue is filed (owner ruling, 2026-11-05).
So the design is two-stage — the **body** carries the process, and scoping posts a **`## Satisfies`
comment** that is the finish line. See `docs/ops/ISSUE-WORKING-PROCESS.md`.

Which makes exactly two shapes defects, and this tool flags only those:

=========================  =========================================================================
arm                        flagged when
=========================  =========================================================================
``pr-unclaimed-issue``     a PR **claims** an issue (a ``#N`` — optionally ``owner/repo#N`` —
                           attached to a **claiming word**: ``Refs`` / ``Part of`` / ``serves`` /
                           ``addresses`` and the closing/satisfies words — ``Related`` and
                           ``See also`` are NOT claiming words (they say the PR does NOT serve the
                           issue, which is the form ``skills/code-review/SKILL.md`` mandates for a
                           non-closing reference);
                           a comma-continued list after that word is claimed in FULL, but only the
                           **first** ref of a RESOLVING word resolves — GitHub's documented
                           multi-issue form repeats the keyword (``Resolves #10, resolves #123``),
                           so a continuation is CLAIMED, never resolved) and that issue is resolved
                           by **neither** ``Satisfies #N`` **nor** a closing keyword
                           (``Closes``/``Fixes``/``Resolves``). A bare ``#N`` with no claiming word
                           is a MENTION and is never flagged — that distinction is what took the
                           measured false-positive population from 68 % to 36 %. Measured (#10534):
                           of 18 PRs naming a P0/P1, 18 used ``Refs`` and 5 of 29 used a closing
                           keyword — so delivered work never closed and the count never moved.
``wip-no-satisfies``       an issue has WIP but **no ``## Satisfies`` checklist** anywhere in its
                           body or comments — work started before the completion criteria existed.
=========================  =========================================================================

A CLAIM, NOT A MENTION — and why that distinction is the whole arm
------------------------------------------------------------------
The first version of this arm counted every ``#N`` in the body and flagged 19 of 28 live open PRs
(68 %), because a PR body is full of references that are not claims: a criterion id in prose
("reds only #10"), a PR's own number quoted from a review, an existing guard cited as precedent, a
command inside inline code, a tracking note inside an HTML comment. Each of those is a *mention*.
A claim is a reference the author is using to say **"this PR serves this issue"**, so the ref must sit
on a line that carries a claiming word, and this module reads only those — plus it excludes the PR's
own number, inline code spans, HTML comments and fenced blocks.

⛔ Three populations are DELIBERATELY NOT FLAGGED, and the tests pin them as negative controls:

* a **fresh issue with no criteria** — normal, the scoping has not run yet;
* a **PR that claims no issue** — not every change serves an issue;
* a **``Related:`` / ``See also``** reference — the repo-mandated form for saying a PR does NOT
  serve the issue (`docs/ops/ISSUE-WORKING-PROCESS.md`), so flagging it would punish an author for
  following this repo's own review instructions.

A false positive in either direction points bureaucracy at legitimate work, which is worse than the
defect. That is the whole reason this tool is advisory.

ADVISORY, NEVER A GATE — and the exit codes say so honestly
------------------------------------------------------------
``0`` nothing flagged · ``3`` findings · ``2`` unmeasurable. There is no ``--fail`` mode; nothing
mounts it; it is not in ``python-ci-gate.needs`` and not in ``.mergify.yml``. Any workflow step that
ever mounts it must carry ``continue-on-error: true`` — the shape of ``tools/closes_landing.py`` +
``.github/workflows/closes-coherence.yml`` (#10597: "not a new gate"), and
``tests/test_issue_process_lint.py`` parses the workflow YAML to enforce it per-step rather than
trusting the string to appear somewhere in the file.

WHAT THIS TOOL DOES **NOT** CONSULT — declared, so its silence is not read as health
---------------------------------------------------------------------------------
The ``wip-no-satisfies`` arm reads the **durable textual marker** the process block mandates — a
non-empty ``IN FLIGHT: <ids> …`` line in a comment — and nothing else. It does **not** enumerate
branches, worktrees or ref-claims. That population is answered by ``tools/who_is_on.py <N> --repo .``
(the supported "who is on #N" verb, which sees local-only branches and uncommitted worktrees that a
remote-only query cannot — #4256); a caller that has already run it injects its verdict with
``--wip`` / ``--no-wip``.

Building a second branch census here would be a second source of truth that can disagree with the
first, which this repo has already paid for (AGENTS.md: *"a second census path that can disagree"*).
So the arm is explicit about its bound in the OUTPUT, not only here: every run that ACTUALLY READS
MARKERS (``--issue``, ``--sweep-issues``, ``--issue-body-file``) prints how many subjects carried a
marker and how many carried none, because **``0 markers read`` and ``0 issues with WIP`` are
different claims** and a bare "NOTHING FLAGGED" would launder one into the other. ⚠️ The injected
``--wip``/``--no-wip`` forms are deliberately NOT in that list (review round 29, P3): their verdict
comes from the branches/worktrees/ref-claims population ``tools/who_is_on.py`` owns, no marker is
read, and the run says so in words — a ``0 of 0`` count there would claim a reading that never
happened. A PR-side run (``--pr``, ``--sweep``,
``--body-file``) never evaluates this arm, so it prints no WIP line at all — that is a different
bound and it is stated here rather than implied away by "every run".

Why the fence/inline-code scan
------------------------------
``tools/closes_landing.py`` already paid for the naive version of this: a fence opened on a
list-marker line was read as prose and manufactured a ``lands``. The same naive read here would
manufacture a *flag* about a line the author quoted rather than claimed.

That grammar is therefore **not re-derived here**. The first version of this module did re-derive it
— it recognised a fence only when the first non-space character of the line was a backtick/tilde —
and a quoted example written as ``- ```markdown`` (a fence ON the list-marker line) or ``> ```markdown``
(inside a blockquote) was read as prose, so ``Refs #1234`` inside the quotation became a CLAIM and arm 1
flagged a line the author quoted rather than claimed (review P2). A second copy of a grammar is how
that drift happens; the hardened copy is ``tools/closes_landing.py::strip_code``, which carries the
container rules (a fence may open ON the marker line, at the item's content column; a fence belongs
to its container and ends when that container does; containers compare by DEPTH), so this module
imports it instead of carrying a weaker third copy.

An **unterminated** fence hides the rest of the body from the scan, which is a MISS in the silent
direction, so that case is reported in the output — in EVERY mode that reads a body, including the
sweeps, rather than only in the offline ones.

Usage
-----
    uv run python tools/issue_process_lint.py --pr 11027
    uv run python tools/issue_process_lint.py --issue 4282
    uv run python tools/issue_process_lint.py --issue 4282 --wip        # issue arms only
    uv run python tools/issue_process_lint.py --sweep                  # newest 50 open PRs (a CAP)
    uv run python tools/issue_process_lint.py --sweep-issues --label P0-blocker --limit 20
    uv run python tools/issue_process_lint.py --body-file /tmp/body.md
    uv run python tools/issue_process_lint.py --issue-body-file /tmp/i.md \
        --comments-file /tmp/c.json --wip
"""

from __future__ import annotations

import sys

# #5128: refuse a <3.12 interpreter before the imports below — a module-level 3.11+-only import would
# fail first (D9 shape). Must be the FIRST statement after the docstring and `__future__`, and
# `tests/test_entry_point_python_guard.py` derives its corpus from `tools/*.py`, so a new tool
# without this guard reds a test in `main` the moment it is staged.
if sys.version_info < (3, 12):  # noqa: UP036 — intentional RUNTIME guard
    raise SystemExit(
        f"tools/issue_process_lint.py requires Python >= 3.12 (got "
        f"{sys.version_info[0]}.{sys.version_info[1]}) — run it as "
        f"`uv run python tools/issue_process_lint.py`"
    )

import argparse
import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# #5128-adjacent: the fence grammar is NOT duplicated here. `tools/closes_landing.py::strip_code`
# carries the container rules this module needs (a fence may open ON a list-marker line, at the
# item's content column; a fence belongs to its container and ends with it; containers compare by
# DEPTH), and it is the copy a real CommonMark renderer is checked against in
# `tests/test_closes_landing.py`. A second copy here was the review's P2 defect: a quoted example
# inside `- ``` … ` or `> ``` … ` was read as prose, so the quote's `Refs #N` became a claim. The
# repo-root insert is the pattern `tools/ask_spotcheck.py` uses for the same reason (this file runs
# BOTH as a loose script, where `sys.path[0]` is `tools/`, and as `tools.issue_process_lint` from a
# test, where it is the repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Both grammars are IMPORTED rather than re-derived, for the same measured reason: a weaker copy
# here is a false positive on legitimate work. `strip_code` owns the fence grammar, and
# `closes_landing`'s own history records the inline one — a QUOTED `Closes #N` written between TWO
# backticks on each side counted as a claim (#10634 review P3-2). A single-backtick regex re-derived
# in this module reproduced exactly that: ``Refs #1234`` was left as prose and flagged. CommonMark
# closes a code span with a run of the SAME length, so run-length is the grammar's question.
from tools.closes_landing import _strip_inline_code as _strip_inline_code_spans  # noqa: E402
from tools.closes_landing import strip_code as _strip_fenced_blocks  # noqa: E402

# --- exit codes (the caller's contract; see ADVISORY above) -------------------------------
RC_CLEAN = 0
RC_UNMEASURABLE = 2
RC_FINDINGS = 3

CLOSING_WORDS = ("close", "closes", "closed", "fix", "fixes", "fixed", "resolve", "resolves", "resolved")
SATISFIES_WORDS = ("satisfy", "satisfies", "satisfied")

# A claim is a reference the author attaches a **claiming word** to. Anything else is a mention.
# The window between the word and the `#` is short on purpose: "See #12 for background, and this
# closes nothing" must not read as a closing claim, and a long window is how that happens.
#
# ⚠️ The window is NOT the same for both directions, and applying the lenient one to the RESOLVING
# words was a false PASS: `mid` accepts any 16 non-`#` characters, so a PROSE sentence — "The fix
# in #12 was reviewed this morning" — manufactured a closing resolution and arm 1 reported clean,
# which is the #10534 hole the arm exists to close. A resolving word must therefore be followed by
# punctuation/whitespace only; a linking word keeps the lenient window, because that leniency is
# what makes `Refs, as agreed, #12` a claim.
#
# ⛔ "WHITESPACE ONLY" HAS TO MEAN WHITESPACE AND A COLON — round 5 narrowed the WINDOW and left the
# SEPARATOR's class carrying every clause end (`.` `;` `,` `(` `[` `—`), which re-opened the same
# false PASS by a different door: a sentence-final verb plus punctuation then read as a closing
# keyword and RESOLVED the claim. Measured at the merge head (review round 20, P1), rc 0 with
# `closes_landing.parse_closing_refs(body) == []` — GitHub performs no close and this lint said there
# was nothing to say:
#     `Part of #10543 — reverses the recorded #10119 trade.`
#     `The arm was added to close. #10543 is the issue this PR serves.`      -> NOTHING FLAGGED
# plus `Closes. #100 remains open.`, `Closes — #100 is the umbrella.`, `the gate resolves (#9957)`.
# The spelling is `tools/closes_landing.py`'s OWN separator, this repo's authoritative model of what
# GitHub acts on, so the two grammars can no longer disagree about whether a body closed a reference.
# ⚠️ WHICH spelling is documented at the token itself (`CLAIM_RE`'s `cmid`, with the doctrine): the
# LENIENT form (`_CLOSING_SEPARATORS["lenient"]` = `\s*:?\s*`) backs FINDING arms only, and the
# STRICT form backs every PASS. This lint's `NOTHING FLAGGED` is a pass, so the token is strict. That
# sentence used to read "the spelling below is ... the lenient one" AFTER the token had been narrowed
# to strict (round 27, P3) — a comment stating what the code does not do, which is the exact defect
# class this file names elsewhere, and a reader trusting it could revert the narrowing believing it
# was documented.
# The list grammar's SHARED tokens, defined here above every reader. `_LIST_SEP` is the
# emphasis/whitespace that may sit around a list separator; `_GLOSS` is the ONE balanced
# parenthetical that may sit between a reference and its separator (`#100 (the parent), #200 (the
# child)`) and which holds no `#`, so it can neither smuggle a reference into a list it is not part
# of nor run past the clause it belongs to. A token admitted in one reader and not the others is
# exactly the drift this module keeps re-discovering — the list a claim CONTINUES, the list a decline
# NAMES, and the run a plural decline CLEARS have to be the same list.
#
# ⛔ WHICH TOKEN EACH READER USES, SPELLED OUT (review round 26, P2). The sentence above said "ONE
# definition, read by `CLAIM_RE`, `_REF_LIST` and `_REF_RUN_GAP_RE`", and that was FALSE of
# `CLAIM_RE`: its continuation separator was `_SOFT_GAP`, which admits no emphasis, so the claim arm
# silently dropped an emphasised continuation the decline arm correctly consumed:
#     `Closes #100, #200`          -> closing=[100], weak=[200]                (correct)
#     `Closes #100, **#200**`      -> `#200` never even claimed, rc 0          (false PASS)
#     `Part of **#100**, #200`     -> weak=[100]          (the ref AFTER the list is dropped)
#     `Refs #100, #200 — deliberately not closing #100, **#200**.` -> declined=[100, 200] (same list)
# Emphasis around references is prose this repo writes (`#11116`: `Also closes the gap tracked by
# **#5114**`), so the claim arm's separator is the EMPHASIS-TOLERANT soft gap. It is deliberately NOT
# `_LIST_SEP` itself: `_LIST_SEP` admits unlimited newlines, and the claim arm must not continue a
# list across a BLANK line (round 22, `test_a_list_does_not_continue_across_a_blank_line`);
# `_SOFT_EMPH` admits at most ONE line break, which is exactly the wrapped-list rule. The decline's
# `_REF_LIST`/`_REF_RUN_GAP_RE` keep `_LIST_SEP` — widening a decline only widens REFUSALS, while
# widening a claim admits references, so the two arms want opposite widths here ON PURPOSE.
_LIST_SEP = r"[\s*_`]*"
# ⛔ `_GLOSS`'S LEADING GAP IS THE SOFT ONE, NOT `_LIST_SEP` (review round 23, P3). `_LIST_SEP`
# contains `\s`, so one parenthetical ahead of a conjunction quietly restored the cross-paragraph claim
# the `_SOFT_GAP` rule had just removed:
#     `Closes #100\n\nand #200 is a separate follow-up.`                -> closing=[100]        (clean)
#     `Closes #100\n\n(see the plan) and #200 is a separate follow-up.`  -> closing=[100], weak=[200]
# The blank line is the gap, not the parenthetical, so the LEADING token is a soft one; a single
# wrapped line break is still admitted, which is what keeps `#100 (the parent),\n#200` a list.
_SOFT_EMPH = r"[ \t*_`]*(?:\n[ \t*_`]*)?"
_GLOSS = rf"(?:{_SOFT_EMPH}\([^)\n#]{{0,80}}\))?"
# ⛔ THE BRIDGE'S GLOSS IS NARROWER THAN THE LIST'S, and it has to be. `_GLOSS` is opaque — any ≤ 80
# characters without a `)` or a `#` — so admitting it whole into `_DECLINE_BRIDGE_RE` let a clause
# move its SUBJECT one level into the parenthetical and go CLEAN (measured at `8c74e5fca`, review P3):
#     `Refs #1234 (the outage on the other box is) — not closed.`   -> declined, CLEAN
#     `Refs #1234 — the outage on the other box is not closed.`     -> flagged
# The bridge's closed vocabulary exists so a real subject noun (`the sprint is`, `the outage on the
# other box is`) is prose about that noun and NOT a decline, and a gloss that ENDS on an auxiliary is
# that same subject with its verb moved outside — so the content may not end on a copula/modal. The
# honest glosses this feature exists for (`(a)`, `(blind spot)`, `(latency)`, `(the parent)`) are
# unaffected: they end on a noun. Excluded by a negative LOOKAHEAD on the content (`A(?=B)`-style
# fixed-width look-behind is not expressible across a variable-length body).
_AUX_TAIL = (r"(?:is|was|are|were|does|do|did|has|have|had|will|would|should|must|could|can"
             r"|may|might)")
_GLOSS_BRIDGE = (rf"(?:{_SOFT_EMPH}\((?!([^)\n#]*\b(?:{_AUX_TAIL})(?:n[’']?t|[\W_]|$)"
                 rf"|[^)\n#]*\bwo(?:n[’']?t)))"
                 rf"[^)\n#]{{0,80}}\))?")
# ⛔ THE TAIL AFTER THE AUXILIARY IS NOT JUST WHITESPACE (review rounds 23 and 24, P3). `is)` was
# refused, so `is,)`, `*is*`, `_is_`, `` `is` ``, `is;` and `is.` — the same subject with one
# character after it — read as glosses and the reference was CLEANED. Enumerating those characters
# only moved the boundary: `is…`, `is"`, `is>`, `is'`, `is%`, `is|` and fourteen more still bypassed
# (measured, 20 of 20 outside the enumerated set). An ENUMERATED class cannot answer "one character
# after the auxiliary is not a discriminator", so the tail is INVERTED: `[\W_]` — every non-word
# character plus `_` (`_` is a word character in regex, and it is exactly the emphasis this repo
# writes). Measured after the inversion: 0 of 35 characters bypass, and the honest glosses that end
# in punctuation after a NOUN (`(the parent.)`, `(P3.)`, `(2026-11-05.)`) still decline, because the
# lookahead is anchored on the auxiliary, not on the punctuation. Widening this only widens
# REFUSALS, so it cannot cost a claim.
# ⛔ THE AUXILIARY IS REFUSED ANYWHERE IN THE GLOSS, NOT ONLY AT ITS END (review round 27, P2). The
# residual this block used to declare — "an auxiliary in mid-clause still declines" — was the SAME
# class-A silent drop one word over, and it was live: `(the sprint is, apparently)` does not END on
# the auxiliary, so it sat outside the end-anchored discriminator and the gloss carried the subject
# past the bridge:
#     Refs #1234 (the sprint is, apparently) — not closed.     -> CLEAN, declined=[1234], rc 0
# A gloss whose content contains an auxiliary ANYWHERE is a clause subject with its verb parked
# somewhere, so `\b{_AUX_TAIL}(?:[\W_]|$)` now refuses it wherever it appears — the same
# `[\W_]` tail as below, so the two discriminators agree. `(blind spot)`,
# `(the parent.)` and `(2026-11-05.)` name no auxiliary and still decline. Like every other change in
# this block this only widens REFUSALS, so it cannot cost a claim — the worst case is an honest gloss
# flagged, which is the direction this module prefers.
# ⛔ A LIST DOES NOT CONTINUE ACROSS A BLANK LINE (review round 22, P3-1). The continuation's separator
# was `\s`, which includes `\n`, so a conjunction or comma in a FOLLOWING PARAGRAPH continued the
# list and a legitimate mention was claimed and flagged:
#     `Closes #100\n\nand #200 is tracked separately.`  -> closing=[100], weak=[200]  -> rc 3
#     `Refs #100,\n\n#200`                            -> weak=[100, 200]            -> rc 3
# The mirror grammar already refuses that gap (`_REF_RUN_GAP_RE`/`_LIST_SEP`: "bare whitespace is
# excluded because `#100 #200` is not an English list"), so this was the two-populations-one-grammar
# inconsistency again, in the false-positive direction. ONE newline is still admitted —
# `Part of #10085,\n#10063` is a legitimately wrapped list and stays claimed — but a BLANK line ends it, which is
# exactly the `\n?`-never-`\n\n` rule `closes_landing` states for its own separator.
_SOFT_GAP = r"[ \t]*(?:\n[ \t]*)?"
CLAIM_RE = re.compile(
    rf"\b(?:(?P<kw>refs?|part\s+of|serves?|addresses)\b"
    # A BLANKED COMMENT IS A WHITESPACE GAP, and it may be longer than the 16-character window: the
    # two alternatives below are the same window PLUS a run of HORIZONTAL whitespace of any length,
    # because that is the only thing blanking can insert. `[ \t]` and never `\s`: a claim must not
    # cross a newline (measured at `06bc97f30`, review P2 — `Refs <!-- prettier-ignore --> #1234`
    # claimed nothing once comments were blanked instead of deleted, a silent drop).
    rf"(?P<mid>[^\n#]{{0,16}}?|[ \t]+)"
    rf"|(?P<ckw>satisf(?:y|ies|ied)|close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b"
    # ⚠️ The spelling is the SEPARATOR ITSELF, not a character class that happens to admit the same
    # characters: a class also admits `::` (and `: :`), so `Closes:: #100` manufactured a resolution
    # `tools/closes_landing.py` does not credit — 33 divergences across a keyword × separator sweep,
    # ALL doubled-colon, ALL in the class-A direction (round 22, P3). A class cannot express "at most
    # one colon", so it is written the way the other grammar does.
    # ⛔ AND IT IS THE *STRICT* SPELLING, NOT THE LENIENT ONE (round 26, P2 — the #10534 false PASS).
    # `closes_landing` ships TWO separators and says which question each backs (`:212-222`): LENIENT
    # (`\s*`) backs the FINDING arms, STRICT (`[ \t]*:?[ \t]*\n?[ \t]*`) backs **every PASS**,
    # because `\s` matches a BLANK LINE and `"Closes\n\n#100"` is a shape no GitHub doc shows firing.
    # This lint's `NOTHING FLAGGED` IS a pass, so it may not rest on a shape the sibling grammar
    # refuses to pass on — spelled lenient, the two disagreed in the class-A direction, crediting a
    # close GitHub never performs:
    #     `Refs #100\n\nCloses\n\n#100 is the umbrella, not this PR.`  -> NOTHING FLAGGED, rc 0
    #     while `closes_landing.parse_closing_refs(body, strict=True) == []`
    # ONE line break is still admitted — that is a WRAPPED claim, not a paragraph break. The
    # narrowing can only cost a resolution this lint used to credit, never invent one.
    rf"(?P<cmid>[ \t]*:?[ \t]*\n?[ \t]*))"
    # A COMMA-CONTINUED list is claimed in full: `Part of #10085, #10063` claims BOTH, and
    # reading only the first dropped every subject after it (F3) — measured on three open PRs
    # (#11038, #11065, #11093). A CONJUNCTION / OXFORD-comma list is the SAME list: admitting only
    # the comma form made `Part of #100 and #200` + `Closes #100` read NOTHING FLAGGED while `#200`
    # was claimed and resolved by nothing — the #10534 false PASS (review P1). This module's own
    # decline grammar (`_REF_LIST` below) already treats `#A and #B` as one list, so before this
    # the two arms gave two different answers to "what is a list".
    # Every separator alternative requires a `#N` IMMEDIATELY after it, so a conjunction in ordinary
    # prose (`Part of #100 and the fix is …`) still claims only `#100`.
    # Every separator alternative requires a `#N` IMMEDIATELY after it — and `or` is one of them:
    # `Part of #100 or #300` claimed `#100` alone, so a `#300` resolved by nothing read CLEAN
    # (`53646f1f6`). The DECLINE shape already admitted `and|or`, and the two arms must agree about
    # what a list is.
    #
    # ⛔ A PARENTHETICAL GLOSS DOES NOT END THE LIST — its token is `_GLOSS` (defined above), shared
    # with the decline's list and the plural run. Measured at `06bc97f30` (review P2):
    #     `Closes #100 (the parent), #200 (the child).`   -> claims `#100` alone, rc 0
    # `#200` was never even evaluated, so a reference the author named under a closing keyword that
    # GitHub does not close was reported CLEAN. Not hypothetical prose: #11093's live body carries
    # the shape (`Refs #10892 (the served-but-unusable blind spot), #10602 (…).`).
    rf"(?:(?P<qual>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?#(?P<num>\d{{1,7}})\b"
    rf"(?P<more>(?:"
    rf"{_GLOSS}"
    # ⛔ `_SOFT_EMPH`, NOT `_SOFT_GAP` (review round 26, P2 — see the `_LIST_SEP` note above): the
    # emphasis a DECLINE list accepts made the CLAIM list lose the reference. The CONJUNCTION TAIL is
    # emphasis-tolerant too, for the same reason and in the same commit: `and **#200**` is the same
    # list as `, **#200**`, and fixing one half of the statement while leaving the other is this
    # file's recurring failure (rounds 13–18). `[ \t*_`]+` still requires a separator character, so
    # `and#200` is not a list, and the prose control (`Part of #100 and the fix is …`) still claims
    # only `#100` because a `#N` must follow immediately.
    rf"(?:{_SOFT_EMPH},{_SOFT_EMPH}(?:(?:and|or|&)[ \t*_`]+)?|{_SOFT_EMPH}(?:and|or|&)[ \t*_`]+)"
    rf"(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)?#\d{{1,7}}\b)*)",
    re.IGNORECASE,
)
# One entry per continued ref; a named group inside a repeated group keeps only the LAST match, so
# the continuation is re-scanned rather than captured in place.
CONTINUATION_RE = re.compile(
    r"(?:(?P<qual>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+))?#(?P<num>\d{1,7})\b",
    re.IGNORECASE,
)
# An author may say, in the body, that the reference is deliberately NOT a closing one — a
# measurement PR that serves an issue without fixing it. That sentence is the author's answer, so
# reading it as a defect would punish exactly the honesty the block asks for.
# Markdown emphasis sits *between* the words in real bodies — measured: "deliberately **not**
# `Closes`" defeats a plain `\s+` separator, so the separator admits emphasis characters.
_EMPH = r"[\s*_`]+"
# ⛔ The decline must be a statement ABOUT THE REFERENCE, not any incidental "deliberately not …"
# sharing its line. The alternative used to be a bare `deliberately{_EMPH}not`, and the decline is
# applied per LINE to EVERY ref on it — so
#     `Refs #11077 — the lint is deliberately not a gate.`
# read CLEAN: the claim was silently dropped (measured, review F1). That is a false PASS in the
# #10534 direction (delivered work never closes), the one this module ranks worst, so the phrase
# now has to END its clause: after it comes only punctuation/emphasis, or the very closing word it
# is declining.
#
# Both halves are load-bearing, and the pinned case is why the alternative cannot simply be deleted:
# in the body `Refs #10602 — deliberately **not** \`Closes\``, `strip_code` blanks the inline-code
# keyword, so what reaches this regex is `deliberately **not**   .` — punctuation only, no `clos`
# left to match; a decline that names the closing word in prose (`deliberately not a closing one`)
# must still decline, so nothing legitimate is lost by tightening.
#
# ⛔ The SAME test applies to the other four alternatives, which were left unanchored (F1, the
# unfixed remainder of the round before): `declined_lines` is computed per line and applied to
# EVERY ref on that line, so an ordinary English `does not close` sharing the line deleted the
# claim beside it. Measured at `fb2913a966`, each of these read CLEAN while the control
# `Refs #1234` alone flagged:
#     `Refs #1234 — the estimate does not close the delta.`
#     `Refs #1234 — mitigates the symptom; does not close the root.`
#     `Refs #1234 — the numbers are not close to the target.`
#     `Refs #1234 — no closing costs involved.`
#     `Refs #1234 — non-closing reference, see the review.`
# None of them is about the reference, so the closing word now has to END its clause or name the
# reference it is about. Two properties make that decidable rather than a word list:
#   * `deliberately` is the AUTHOR-FACING convention (the process block asks for exactly this
#     phrase), and it does not appear by accident on a claim line about something else — so it
#     keeps the looser rule: clause-terminal, or naming the closing word (`not a closing keyword,
#     see the plan` — the appositive continuation is the reason a comma is admitted after the
#     reference noun).
#   * the ordinary-English shapes carry no such marker, so they must be clause-terminal, and a
#     tail is admitted ONLY when it is the reference itself (`does not close the issue`, `not a
#     closing one`). A transitive object that is not one of those (`does not close the delta`, `not
#     close to the target`, `no closing costs involved`) is prose about something else.
_DECLINE_TAIL = r"[\s*_`.,;:!?()\[\]—–-]*"
_CLOSING_STEM = (r"(?:clos(?:e[sd]?|ing)|fix(?:e[sd]?|ing)?"
                 r"|resolv(?:e[sd]?|ing)?|satisf(?:y|ies|ied|ying))\b")
# What a decline may name AFTER the closing word: the reference, and nothing else. Closed and short
# on purpose — every word admitted here widens the silent-dropped-claim surface, and a `closing`
# followed by one of them is a sentence about the reference by construction. `it`/`them`/`this`/
# `that` and a literal `#N` are the four most natural ways to write the same honest sentence
# (`deliberately not closing it.`, `deliberately not closing #1234.`) — measured as FLAGGED by the
# round-12 grammar, i.e. a false positive on exactly the honesty the block asks for.
_REF_NOUN = (r"(?:one|ones|ref|refs|reference|references|issue|issues|keyword|keywords"
             r"|it|them|this|that)\b")
# ⛔ `pr` IS NOT AN OBJECT NOUN (round 22, P3) — the mirror of round 20's subject fix. `the PR` names
# the PULL REQUEST, so `Refs #100 — not closing the PR.` cleared `#100` while saying nothing about it
# (`declined=[100]`, rc 0). The honest forms (`this issue`, `this ref`, `#100`) are all admitted.
#
# ⛔ `commit` IS NOT AN OBJECT NOUN EITHER (review round 25, P2) — round 22's ruling one noun over.
# A closing keyword closes an ISSUE (GitHub's own "Linking a pull request to an issue": the object
# of `close/fix/resolve` is an issue), so `does not close the commit` says nothing about `#100` and
# must not clear it. Left in the object list it was a class-A FALSE PASS, measured at `afd6de51d`:
#     Refs #100 — does not close the commit.          -> declined=[100]  (rc 0, #100 dropped)
#     Refs #100 — this PR does not close the commit.  -> declined=[100]  (rc 0)
#     Refs #100 — no closing commit.                  -> declined=[100]  (rc 0)
# while the mirror spelling `this commit does not close it` — the honest sentence, whose object IS
# the reference — was FLAGGED, because `commit` was in neither subject vocabulary. Removing it from
# the OBJECT list alone fixes the false PASS and leaves that false positive, so it is admitted where
# `pr` is: as the two-word subject whose demonstrative makes it the reference (`this commit does not
# close it` declines). Bare `commit` and `the commit` stay refused, so `does not close the commit`
# has no route. The invariant either way is this module's own: the object list, the subject
# vocabulary and the bridge must give the SAME answer to "is this sentence about the reference".
_REF_OBJ = rf"(?:{_REF_NOUN}|#\d{{1,7}})"
# A decline may name a LIST — `#100, #200`, `#100 and #200`. Captured as ONE group so every named
# ref is visible to the target logic; the continuation requires a `#N` right after the separator, so
# `#200 and Refs #300 for the follow-up` stops at `#200` (the `and` there joins CLAUSES, and
# sweeping `#300` in silently dropped a claim the author had made — measured at `2219dfe86`).
# ⛔ THE SEPARATOR IS NOT `_DECLINE_TAIL`: that class contains `.` `;` `:` and `—`, which are the
# CLAUSE ENDS. Using it made `deliberately not closing #100. #300` capture `#100. #300` as one list,
# so a ref claimed by a FOLLOWING sentence was swept into the decline and disappeared (measured at
# `0e785300b`, review P2). A list separator is a comma or a conjunction, and nothing else.
# `_LIST_SEP` and `_GLOSS` are defined above `CLAIM_RE` — the claim grammar reads them too.
_REF_LIST = (rf"(?:{_REF_OBJ})"
             rf"(?:(?:{_GLOSS},{_LIST_SEP}(?:(?:and|or|&){_LIST_SEP})?"
             rf"|{_GLOSS}(?:{_LIST_SEP}(?:and|or|&){_LIST_SEP}))#\d{{1,7}})*")
# ⛔ `&` IS ONE OF THE SEPARATORS HERE TOO (review round 24, P2). `CLAIM_RE` admitted
# `and|or|&` while this list and `_REF_RUN_GAP_RE` admitted only `and|or`, so the invariant stated
# two lines below ("ONE definition, read by CLAIM_RE, `_REF_LIST` and `_REF_RUN_GAP_RE`") did not
# hold: `Refs #100 & #200 — deliberately not closing them.` claimed #100, declined #200, and flagged
# the author, while the `and` twin was clean. Either all three readers admit `&` or none does.)
# The INVERSION of `_REF_LIST`'s separator: what may sit BETWEEN two references of the same list.
# It exists so the plural (`them`) declaration clears the run it NAMES without sweeping in a
# previous sentence's claim — see `_ref_run_back`. The separator is the list grammar's own (a
# comma or a conjunction, never a clause end and never bare whitespace), so the two directions
# cannot drift apart; bare whitespace is excluded because `#100 #200` is not an English list and
# the conservative reading of an ambiguous gap is to END the run.
_REF_RUN_GAP_RE = re.compile(
    rf"^{_GLOSS}(?:,{_LIST_SEP}(?:(?:and|or|&){_LIST_SEP})?"
    rf"|{_GLOSS}(?:{_LIST_SEP}(?:and|or|&){_LIST_SEP}))$",
    re.IGNORECASE,
)
_DET = rf"(?:an?{_EMPH}|the{_EMPH}|this{_EMPH}|these{_EMPH}|those{_EMPH})?"
# ⛔ THE OBJECT MAY NAME THE REFERENCE BY ITS KIND — `a closing one`, `the closing ref`,
# `a resolving one`. The object slot admitted `_DET` immediately followed by `_REF_NOUN`, so the
# closing WORD sitting between them had no route through the grammar, the object captured EMPTY,
# and the unreadable-object guard below suppressed the proximity path (measured at `6d8e4ed8b`,
# review P2, the one finding that round declared rather than fixed):
#     `Part of #1234 — deliberately not closing a closing one.`   -> rc 3, FLAGGED
# while `deliberately not a closing one.` (no leading stem) declined, which is what makes it a
# grammar gap and not a doctrine: the same sentence about the same reference reads two ways
# depending on where the author put the word `closing`. Flagging it is the FALSE-POSITIVE
# direction — the author is answering the question the block asks.
_CLOSING_OBJ = rf"{_DET}{_CLOSING_STEM}{_EMPH}{_REF_LIST}"
# ⛔ A DECLINE MAY CARRY A REASON, AND THE REASON MUST NAME NO REFERENCE (round 20, P2). The tail
# after the object admitted `$`, `.`, `;`, a comma, or a conjunction — so the most natural way to
# write the honest sentence, with its REASON attached, was FLAGGED (class B, the direction the
# module's doctrine says to avoid):
#     `Refs #100 — deliberately not closing this issue because it is a measurement PR.`   -> rc 3
#     `Refs #100 — deliberately not closing this issue (measurement only).`              -> rc 3
#     `Refs #100 — no close intended.`                                                   -> rc 3
# The shapes below are admitted as ONE structural rule rather than a longer word list: the remainder
# must contain NO `#`, so a reason clause can never sweep a claim into the decline —
#     `Refs #100 — deliberately not closing this issue because #300 must land.`  -> FLAGGED
# which is correct: that sentence names a live claim in the same breath as the decline.
# ⛔ AND THE REASON MAY BE AN APPOSITIVE (round 26, P3). The tail admitted an INTRODUCED reason
# (`because`/`since`/`as`/`intended`), a parenthetical, `;` and `.` — but not the appositive, which is
# how the same reason is most naturally written. So the author answered the question the block asks
# and was pointed at bureaucracy for it (the direction this module ranks worse than the defect):
#     `Refs #100 — not closing it — measurement only.`   -> rc 3, FLAGGED
#     `Refs #100 — not closing it; measurement only.`    -> clean (already admitted)
# ⛔ THE DASH FORM ONLY, AND A BARE COMMA IS DELIBERATELY NOT ADMITTED (measured, not assumed). A
# bare `,` in this token is offered by the `(?P<reason_b>…)` slot AFTER the object, so it also
# matched round 12's PINNED NEGATIVE — ordinary prose whose comma opens a NEW CLAUSE, not an
# appositive:
#     `Refs #1234 — non-closing reference, see the review.`  -> declined, rc 0  (the false PASS
#                                                              round 12 exists to prevent)
# `non-closing reference` and `not closing it` are the SAME object shape (`_REF_LIST` admits both
# `reference` and `it`), and `measurement only.` is a noun phrase while `see the review.` is a
# clause — telling those apart needs a clause/NP grammar, not a punctuation class. So the comma
# form stays a DECLARED RESIDUAL (declared here, pinned by
# `test_an_honest_decline_may_carry_its_reason_as_an_APPOSITIVE`) rather than costing a pinned
# false PASS. The dash is unambiguous: nothing in round 12's population uses it to open a clause.
# The structural rule is UNCHANGED and is what keeps this a one-shape widening: the tail may contain
# NO `#`, so a reason can never sweep a live claim into the decline
# (`Refs #100 — not closing it — #300 must land first.` stays FLAGGED, pinned).
_REASON_TAIL = (rf"(?:{_EMPH}(?:because|since|as|intended)\b"
                rf"|[ \t]*\("
                # ⛔ A COLON INTRODUCES A REASON TOO (review round 28, P3). `_DECLINE_TAIL` already
                # treats `:` as clause punctuation, but the terminator admitted only `;`, `.`, the
                # four reason words, `(` and a dash — so the SAME honest decline flipped verdict on
                # the punctuation alone and the reference was FLAGGED:
                #     Refs #100 — deliberately not closing: measurement only.    -> FLAGGED
                #     Refs #100 — deliberately not closing; measurement only.    -> declined
                # The `[^#\n]*$` guard is unchanged, so a colon-introduced reason still cannot name a
                # reference and sweep a live claim into the decline.
                rf"|[ \t]*:"
                rf"|{_EMPH}[—–-])[^#\n]*$")
# ⛔ THE NEGATION HAS CONTRACTED FORMS TOO (round 22, P2). Alternative (b) admitted `does not` / `not`
# / `no ` / `non-` and no contraction, so the most natural spelling of the honest decline was flagged
# while its uncontracted twin declined — a false positive on exactly the honesty the block asks for:
#     `Refs #100 — it isn't closed.`        -> FLAGGED   (`it is not closed.`      -> clean)
#     `Refs #100 — this doesn't close it.`  -> FLAGGED   (`this does not close it.` -> clean)
#     `Refs #100 — we can't close it.` / `cannot` / `won't` / `wasn't` / `never`  -> FLAGGED
# The bridge and the object grammar are unchanged, so a contracted sentence about ANOTHER subject is
# still prose (`the sprint isn't closed.` stays flagged — pinned). These are ALTERNATIONS of the
# negation, not a wider tail: the clause-end and reference-naming rules still apply to each.
_CONTRACTION = (r"(?:(?:is|was|are|were|does|do|did|ca|wo|has|have|had|could|would|should|must"
                r"|need)n[’']?t|cannot|never)")
DECLINE_RE = re.compile(
    # (a) the author's convention: `deliberately not …`
    rf"deliberately{_EMPH}not"
    rf"(?={_DECLINE_TAIL}$"
    rf"|{_EMPH}{_DET}(?P<stem_a>{_CLOSING_STEM})"
    rf"(?P<obj_a>{_EMPH}(?:{_DET}{_REF_LIST}|{_CLOSING_OBJ}))?"
    rf"{_DECLINE_TAIL}(?:$|[.;]|,(?!{_LIST_SEP}#\d{{1,7}})|{_EMPH}(?:and|or){_EMPH}"
    rf"|(?P<reason_a>{_REASON_TAIL})))"
    # (b) the ordinary-English shapes: the closing word must END its clause or name the reference.
    # ⛔ A COMMA MAY INTRODUCE THE REASON, AND ONLY THE REASON (round 23, P2). Admitting the plain
    # comma as a clause end made the module's own "a decline may carry its reason" rule hold for ONE
    # spelling: `not closing it, because …` was FLAGGED while `deliberately not closing it, because …`
    # declined — 3 of the 4 cells of that 2×2 were green, which is how the gap survived four rounds of
    # fixes to this grammar. The comma is admitted ONLY as the preamble to `_REASON_TAIL`, not as a
    # bare clause end: a bare comma is what round 12's pinned negative needs to stay FLAGGED
    # (`Refs #1234 — non-closing reference, see the review.`), and `_REASON_TAIL`'s `[^#\n]*$` keeps a
    # reason that names a reference from buying the decline.
    rf"|\b(?:does{_EMPH}not|not|no{_EMPH}|non-|{_CONTRACTION})(?:{_EMPH})?{_DET}(?P<stem_b>{_CLOSING_STEM})"
    rf"(?P<obj_b>{_EMPH}(?:{_DET}{_REF_LIST}|{_CLOSING_OBJ}))?(?:{_DECLINE_TAIL}(?:$|[.;]|,{_REASON_TAIL})"
    rf"|(?P<reason_b>{_REASON_TAIL}))",
    re.IGNORECASE,
)
# ⛔ A DECLINE IS ABOUT *ITS OWN* REFERENCE — the two properties the grammar alone cannot express,
# both measured as false PASSes (claim silently dropped, arm 1 CLEAN) at `d580a3401`:
#
#   1. WHICH reference. `declined_lines` was line-scoped and cleared EVERY ref on the line, so
#      `Refs #100, #200 — #200 is deliberately not Closes.` read CLEAN while `#100` was claimed and
#      resolved by nothing. The decline now clears only the REF its clause is about — the nearest
#      reference PRECEDING it (a decline before any reference is about something else, and absent
#      evidence is the flag, never the clear).
#   2. WHETHER the clause is about it at all. Anchoring the negation's TAIL left its SUBJECT free:
#      `Refs #1234 — the sprint is not closed.` still read CLEAN, because any `<not|no|non-> <verb>.`
#      at end of line declined the line whatever preceded it. The bridge between the reference and
#      the negation now admits a SHORT closed vocabulary — subject words that can only be the
#      reference (`this`, `it`, `that`, `which`, `this PR`, `we`, `the author`), a copula, and the
#      convention word `deliberately` in its idiomatic position — and NOTHING ELSE. A real subject
#      noun there (`the sprint is`, `the outage on the other box is`) is prose about that noun, so
#      the reference is NOT declined and the claim stays flagged.
#
# ⛔ A SUBJECT IS NOT AN OUTCOME (round 20, P3). The vocabulary above admitted bare `pr`, so a
# sentence about the PULL REQUEST's own merge state declined the reference and the claim vanished:
#     `Refs #100 — the PR is not closed.`     -> CLEAN, declined=[100], nothing closes #100
# `the PR is not closed` says the PR is not merged; it says NOTHING about whether #100 is closed, and
# it is not the honest convention, which names an ACTION and its object (`this PR does not close the
# issue`). Two narrowings close it without touching any pinned decline:
#   * `pr`/`prs` are admitted only as the two-word subject `this PR` / `these PRs` — never bare, and
#     never behind `the`, so `the PR is not closed` has no route through the bridge;
#   * the STATE copulas (`is`/`was`/`are`/`were`) are admitted ONLY after a reference-only subject
#     (`this`/`it`/`that`/`which`), as their own bridge shape — `it is not closed` IS a statement
#     about the reference and still declines, while `this PR is not closed` does not.
# Both are NARROWINGS of the decline direction, so the worst case is a false positive (an honest
# decline flagged) and never a new false PASS. The pinned controls below cover both sides.
#
# ⛔ WHY AN ALLOW-LIST AND NOT `\w+`-TOLERANCE. The measured false-negative pair at `e7dd41058`
# (review, P2) was `Refs #1234 — deliberately does not close the issue.` and
# `Refs #1234 — this PR does not close the issue.` — both the honest convention, both FLAGGED
# because the bridge admitted ONE subject word and ONE copula, so the adverb or the two-word
# subject broke it. Admitting `pr`/`we`/`author`/`deliberately` fixes those without re-admitting
# `the sprint is` / `the estimate does` (the round-12 negatives), which still fail the bridge.
_DECLINE_TAIL_BRIDGE = _DECLINE_TAIL
_SUBJECT_WORD = r"(?:this|it|that|which|we|i|they|author)"
# `pr` is NOT a subject word: it is admitted only in the two-word form whose demonstrative makes it
# the reference (`this PR`), so `the PR is not closed` cannot reach the decline.
# `commit` is admitted the same way, and for the mirror reason (review round 25): it left the OBJECT
# list because a closing keyword's object is an issue, and the demonstrative form is how an author
# says the honest decline about the reference — `this commit does not close it`. Bare and
# `the`-prefixed `commit` remain refused, so `does not close the commit` stays prose.
_SUBJECT_PHRASE = rf"(?:this|these){_EMPH}(?:pr|prs|commit|commits)"
# An ACT (do-support or a modal): the subject may be a human agent and the verb takes an object.
_COPULA = (r"(?:does|do|did|has|have|had|will|would|should|must|could|can"
           r"|may|might)")
# A STATE of the subject (`is`/`was`/`are`/`were`): admitted ONLY behind a reference-only subject,
# because `it is not closed` is a statement about the reference while `the PR is not closed` is a
# statement about the pull request. See the comment above the bridge for the measured shape.
_STATE_COPULA = r"(?:is|was|are|were)"
_REF_ONLY_SUBJECT = r"(?:this|it|that|which)"
# Every word the bridge may contain: the human subjects/phrases/copulas above, plus the article that
# can only introduce `the author`, plus `deliberately` (which `DECLINE_RE`'s ordinary-English
# alternatives do NOT start on, so it lands here: `— deliberately does not close the issue.`).
_BRIDGE_WORD = rf"(?:{_SUBJECT_WORD}|{_SUBJECT_PHRASE}|{_COPULA}|the|a|an|deliberately)"
_BRIDGE_WORDS_MAX = 3
_DECLINE_BRIDGE_RE = re.compile(
    # A parenthetical GLOSS may sit between the reference and the sentence that answers it —
    # `Refs #10892 (blind spot), #10602 (latency) — deliberately not closing them.` It is
    # `_GLOSS_BRIDGE`, NOT `_GLOSS`: a gloss whose content ends on an auxiliary is a clause SUBJECT
    # with its verb parked outside, and admitting it whole made
    # `Refs #1234 (the outage on the other box is) — not closed.` decline while the inlined form of
    # the same sentence flags (measured at `8c74e5fca`, review P3). What follows the gloss is still
    # only tail punctuation or the closed vocabulary.
    # (i) a STATE of the reference itself: `— it is not closed`, `— this is not closed`.
    rf"^{_DECLINE_TAIL_BRIDGE}{_GLOSS_BRIDGE}"
    rf"{_REF_ONLY_SUBJECT}{_EMPH}{_STATE_COPULA}{_DECLINE_TAIL_BRIDGE}$"
    # (ii) the reference IS the subject, with no separate subject word: `#200 is deliberately not
    #      Closes.`, `Part of #11000 — #11000 is not closed.` — the bridge is the copula alone.
    rf"|^{_DECLINE_TAIL_BRIDGE}{_GLOSS_BRIDGE}{_STATE_COPULA}{_DECLINE_TAIL_BRIDGE}$"
    # (iii) an ACT by a named subject: `— this PR does not close the issue`,
    #       `— the author deliberately does not close the issue`.
    rf"|^{_DECLINE_TAIL_BRIDGE}{_GLOSS_BRIDGE}"
    rf"(?:{_BRIDGE_WORD}{_EMPH}){{0,{_BRIDGE_WORDS_MAX}}}"
    rf"{_DECLINE_TAIL_BRIDGE}$",
    re.IGNORECASE,
)
# Ref TOKENS with positions, for deciding which reference a decline is about. Deliberately dumber
# than CLAIM_RE: this answers "where is the reference", never "does it claim".
_REF_POS_RE = re.compile(r"#(?P<num>\d{1,7})\b")
# A conjunction that INTRODUCES a reference (`and #200`, `or owner/repo#200`) — the discriminator the
# unreadable-object guard uses to tell a continuing clause from a blanked object plus prose.
_CONJ_REF_RE = re.compile(
    rf"[ \t]+(?:and|or){_EMPH}(?:[A-Za-z0-9_.\-/]+/)?#\d{{1,7}}", re.IGNORECASE)
SATISFIES_HEADING_RE = re.compile(
    r"^[ \t]{0,3}(?P<hashes>#{2,3})[ \t]*Satisfies\b", re.IGNORECASE | re.MULTILINE)
# The task-list marker SET is the sibling grammar's (`closes_landing._TASK_ITEM_RE`), including
# `+` and ordered markers: GitHub renders all four as task lists, so counting only `-`/`*` reads a
# legitimately scoped issue as having NO checklist and flags it (a false positive on finished
# scoping). Not imported verbatim because that pattern also requires trailing whitespace after the
# bracket, which would miss a bare `- [ ]` at end of line.
CHECKBOX_RE = re.compile(r"^[ \t]*(?:[-*+]|\d{1,9}[.)])[ \t]+\[[ xX]\]", re.MULTILINE)
# ⛔ `TICKED_RE` USED TO SIT HERE and is deliberately NOT replaced (review round 26, P2). Its only
# reader was `has_satisfies_checklist`'s "tick in a LATER comment" arm, which is where the false PASS
# was: any finished task list anywhere after the heading cleared a `## Satisfies` with no criteria.
# A constant kept "just in case" reads as a live second spelling of "is this a task item", which is
# the drift this module keeps re-discovering — so it is removed with its reader, not parked.
# Any ATX heading at line start — used by `_satisfies_sections` to end a `## Satisfies` SECTION.
# ⚠️ ANY LEVEL, INCLUDING A DEEPER ONE (round 28, P2 — this was tried the other way and it cost a
# FALSE PASS). Ending the section only at the same-or-higher level makes every box under any deeper
# heading this section's criteria, so an unrelated list one level down clears a criteria-less heading:
#     `## Satisfies` + `To be derived.` + `### Verification` + `- [ ] x`  -> TRUE, rc 0
# That is round 25/26's exact population re-opened, and a false PASS outranks the false positive on
# the other side (`## Satisfies` + `### 1. Guard holds` + `- [ ] a` reads as no criteria). A structured
# criterion-per-`###` checklist should therefore not put boxes under the sub-heading; the safer error
# is to flag it than to clear an issue that has no criteria at all.
_ATX_ANY_RE = re.compile(r"^[ \t]{0,3}(?P<hashes>#{1,6})[ \t]", re.MULTILINE)
IN_FLIGHT_RE = re.compile(
    r"^[ \t]*(?:[-*+][ \t]+)?[\s*_`]*IN[ -]FLIGHT\s*:\s*(?P<rest>.*)$", re.IGNORECASE
)
# A marker whose remainder is only a placeholder (`<ids> — branch <b> …`) is the CONVENTION being
# quoted, not a claim; a remainder that says the issue was released is a release, not a claim.
PLACEHOLDER_RE = re.compile(r"^[\s<>a-zA-Z0-9_./—-]*$")
RELEASE_WORDS = ("none", "released", "unclaimed", "free", "—", "-")
# The unambiguous release words, but only in an ANNOTATION POSITION (F6). An unanchored anywhere-
# scan is a silent MISS in the flagging direction: the marker the process block mandates carries
# author text after the ids (`IN FLIGHT: 4282 — branch fix/unclaimed-issue-arm2 — box cell-3`), and
# `unclaimed` is this tool's OWN vocabulary, so a lane named after the arm it is working on would
# clear its own WIP marker.
#
# Two properties are load-bearing: the delimiter class must NOT contain a bare `-` (a hyphen is the
# in-word separator of every branch slug, so `feat/retrieval-none-mode` would read as released), and
# the word must be TERMINAL — followed by a date, a closing bracket, a delimiter or end of line — so
# `(none pending)` is a status note, not a release. A `:` or `-` separating the DATE (`released: …`,
# `released - …`) is admitted only directly in front of the date: accepting it on its own would turn
# `released: pending` into a release, a silent miss. `free` is excluded entirely: it heads a release
# but also appears in ordinary work (`free tier`), so it stays a FIRST-TOKEN-only word.
RELEASE_ANYWHERE_RE = re.compile(
    r"(?:^|[—–:;,.(\[]|\s-\s)\s*(?:none|released|unclaimed)\b(?![\w-])"
    r"(?=\s*(?:(?:[-:]\s*)?\(?\s*(?:on\s+)?\d{4}-\d{2}-\d{2}|[)\].,;]|$|—|–))",
    re.IGNORECASE)

ARM_PR = "pr-unclaimed-issue"
ARM_ISSUE = "wip-no-satisfies"


@dataclass
class Finding:
    arm: str
    subject: str
    detail: str
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"arm": self.arm, "subject": self.subject, "detail": self.detail,
                "evidence": self.evidence}

    def annotate(self) -> str:
        return f"::warning title=issue-process-lint {self.arm}::{self.subject} — {self.detail}"


class Unmeasurable(Exception):
    """The question was not answered (gh missing, rate limited, no body read). Never a pass."""


# --- normalisation --------------------------------------------------------------------------
def strip_code(body: str, *, strict: bool = False,
               indented_code: bool = False) -> tuple[str, int]:
    """Return (text with fenced blocks, inline-code spans and HTML comments removed, hidden_lines).

    `hidden_lines` is the number of lines an UNTERMINATED fence hid from the scan: that is a miss in
    the silent direction, so the caller reports it instead of passing over it.

    The FENCE grammar is `tools/closes_landing.py::strip_code`'s — imported, not copied (see the
    module docstring): it knows that a fence may open ON a list-marker line or inside a blockquote,
    and that a fence ends with its container. This wrapper adds the two things that are this
    module's own concern: HTML comments (which may span lines) are removed, and inline code spans
    are blanked last, because `gh pr view #1234` is a command being quoted, not a claim.

    ⛔ FENCES FIRST, THEN COMMENTS (round 21, P3). The order was the other way, and the mirror of
    its own rationale was unhandled: the comment strip argued that a COMMENT may contain a fence,
    but a FENCE may contain an unterminated `<!--`, which then paired with a `-->` in the PROSE
    after the fence and deleted every line between them — including a real claim:
        ```markdown\n<!--\n```\n\nPart of #100\n\n-->      -> NOTHING FLAGGED, rc 0
    With the fence grammar first, that `<!--` is code and is gone before the comment strip runs, so
    the prose survives. The documented direction still works: a well-formed fence inside a comment
    is stripped as code, and the remaining comment text is removed afterwards. An UNTERMINATED
    fence still hides the rest of the body — but it does so VISIBLY, through `hidden_lines`, which
    is the whole point of reporting that count.

    ⛔ AND A COMMENT IS BLANKED, NOT DELETED (round 21, the peer finding). Deletion collapsed
    `does not close<!-- #11078 -->` into `does not close ` — ONE space — which the
    unreadable-object guard reads as an honest reason and clears the reference before it, the
    silent drop that guard exists to stop. Equal-length spaces keep every offset AND leave a
    whitespace run of two or more wherever a comment stood, which is the discriminator that
    separates a blanked OBJECT from a sentence that simply names none.

    ⛔ AND A BLANK LINE INSIDE A COMMENT IS STILL A PARAGRAPH BOUNDARY (round 37, P2). Blanking
    every character with `.` under `DOTALL` also swallowed `\n`, so a comment that spanned a blank
    line SPLICED the two paragraphs around it into one — and this module's own rule that a claim
    list does not cross a blank line then stopped applying wherever the blank line sat in a
    comment (measured at `23d7a77ab`):
        `Closes #100 <!-- x` / blank / `y --> and #200 is separate.`   -> arm 1 flagged #200
    while the renderer says that body is TWO paragraphs and flags nothing — the false-positive
    direction this module ranks as worse than the defect it would catch. So a comment is blanked
    per character EXCEPT its newlines, and then a SOFT break (a lone `\n`) collapses to one space
    (the claim direction above needs `Refs <!-- an explanation` / `over two lines --> #1234` to
    read as one line) while a blank line survives. Equal length, so every offset is kept.
    """
    stats: dict[str, int] = {}
    # ⛔ `strict` IS THE PASS DIRECTION AND ONLY ARM 1 ASKS FOR IT (round 28, P2 — the first attempt
    # applied it to BOTH arms and broke arm 2). It additionally treats an INDENTED block as code and
    # refuses to let an inline span cross a block boundary, which is what
    # `closes_landing.parse_closing_refs(..., strict=True)` does — so arm 1's CLEAN verdict stops
    # resting on shapes the sibling grammar refuses to pass on:
    #     `Refs #100\n\n    Closes #100\n`                    -> must NOT credit a close
    #     an inline span across a soft line break (`#10085,\n#10063` in backticks) -> must be blanked
    # ⛔ BUT THE TWO ARMS ASK DIFFERENT QUESTIONS, AND `strict` IS WRONG FOR ARM 2. Arm 2's own
    # predicates deliberately admit leading whitespace (`CHECKBOX_RE` / `IN_FLIGHT_RE` both start
    # `^[ \t]*`), and a line indented 4 spaces INSIDE A LIST ITEM is PROSE, not code (CommonMark
    # measures the content column from the list marker): `1. Criteria` + `    - [ ] a` renders as a
    # NESTED LIST. Blanking it made arm 2 report `wip-no-satisfies` for an issue whose criteria exist
    # (false positive) and drop an indented `IN FLIGHT:` marker entirely (silent miss — the direction
    # this module forbids). `closes_landing.strip_code`'s own docstring records that indented-blanking
    # "loses a REAL claim"; that is a PASS-arm concern, not a WIP-detection one.
    # So the caller chooses: `claims()` passes `strict=True`; the arm-2 predicates ask for
    # `indented_code` instead (round 29, P2 — see the next paragraph for why that is NOT the default).
    # ⛔ AND ARM 2 STILL NEEDS *SOME* INDENT BLANKING (round 29, P2). Turning `strict` off entirely
    # un-blanked the OTHER CommonMark way to quote an example — a top-level INDENTED CODE BLOCK —
    # which is a class-A false PASS and a regression against the parent head:
    #     `## Satisfies` + `To be derived.` + `    - [ ] an example from the template`
    #       -> parent FALSE;  this branch TRUE  -> `classify_issue []` -> rc 0
    #     `    IN FLIGHT: quoted example — branch b`  -> parent [];  this branch ["quoted example …"]
    # The justification for the default stated ONE CommonMark fact (a list item's continuation is
    # measured from its content column) and assumed it covered indentation; it does not, because
    # `strict` blanks on the WIDTH alone. `indented_code` blanks the block-grammar rule instead, so a
    # quoted example is code while `1. Criteria` + `    - [ ] a` (a NESTED LIST) stays prose.
    text = _strip_fenced_blocks(body, strict=strict, stats=stats, indented_code=indented_code)
    # ⚠️ DECLARED RESIDUAL (round 27, P3) — a SETEXT heading (`Verification` + `---`) does not end a
    # `## Satisfies` SECTION, so an unrelated list under one can still clear arm 2. A `---` underline
    # is indistinguishable from a thematic break without a full block grammar, and guessing it wrong
    # re-opens the false-positive direction; an ATX heading (`## Verification`) is the shape the
    # docs use and is handled.
    # HTML comments, now that code regions are already blanked. ⛔ BLANKED, NOT DELETED: deletion
    # collapsed `does not close<!-- #11078 -->` to `does not close ` (ONE space), which the
    # unreadable-object guard reads as an honest reason and clears the ref before it — the silent
    # drop it exists to stop. Equal-length spaces keep every offset AND leave the whitespace run of
    # two or more that separates a blanked OBJECT from a sentence that names none.
    # ⛔ NEWLINES ARE NOT BLANKED WITH THE REST (round 37, P2): `.` under `DOTALL` matched `\n`, so
    # a comment spanning a blank line spliced the paragraphs around it and arm 1 claimed a ref the
    # author had put in a SEPARATE paragraph (`Closes #100 <!-- x` / blank / `y --> and #200 is
    # separate.` flagged #200 at `23d7a77ab`) — the false-positive direction. A lone newline is a
    # SOFT break and becomes one space (so a wrapped comment still lets `Refs <!-- a` / `b --> #1234`
    # claim); a blank line is a PARAGRAPH boundary and stays one. Both substitutions are 1-for-1.
    def _blank_comment(m: re.Match[str]) -> str:
        blanked = re.sub(r"[^\n]", " ", m.group(0))
        return re.sub(r"(?<!\n)\n(?!\n)", " ", blanked)

    text = re.sub(r"<!--.*?-->", _blank_comment, text, flags=re.DOTALL)
    # Inline code spans: `gh pr view #1234` is a command being quoted, not a claim. Imported, not
    # re-derived — a single-backtick regex here read a ``Refs #1234`` span as prose (F1).
    # ⛔ AND THE SPAN SCANNER TAKES THE SAME FLAG — but only when the caller asked for it (round 28,
    # P2). `strict` also makes a code span honour a SOFT LINE BREAK, which is the renderer's own
    # boundary: `MarkdownIt("commonmark")` renders a span that wraps one line break as `<code>`. The
    # lenient scan searched only to the END OF THE LINE, so a QUOTED reference in a wrapped span was
    # read as a CLAIM (`--pr 11078` flagged this PR's own body on its two quoted examples). Forwarding
    # the keyword is the whole fix — the boundary already ships in `closes_landing`; a second copy of
    # the pattern here was the round-27 mistake. Still caller-chosen: arm 2 must not get it.
    text = _strip_inline_code_spans(text, strict=strict)
    return text, stats.get("hidden_lines", 0)


def claims(body: str, own_number: int | None = None,
           repo: str | None = None) -> dict[str, list[int]]:
    """The refs this body CLAIMS, grouped by what the claim does.

    `closing` / `satisfies` resolve an issue; `weak` (`Refs`, `Part of`, `serves`, `addresses`)
    links nothing and closes nothing — that is the #10534 hole.
    """
    return _claims_in(strip_code(body, strict=True)[0], own_number, repo)


def _ref_run_back(line: str,
                  refs: list[tuple[int, int, int]]) -> list[tuple[int, int, int]]:
    """The contiguous LIST run ending at the LAST of `refs` (which must be ascending).

    Used by the plural (`them`) decline: the list a plural pronoun names is the run of references
    joined by the list grammar's own separators, NOT every reference earlier on the physical line.
    A full stop, a semicolon, a dash, a heading or any prose ends the run, so
    `Refs #100. Refs #200, #300 — deliberately not closing them.` is about `#200, #300` only and
    `#100`'s claim stays flagged.

    The gap grammar is `_REF_RUN_GAP_RE` — the INVERSION of `_REF_LIST`'s separator — so the run
    this clears and the list a decline NAMES cannot be defined differently.
    """
    if not refs:
        return []
    run = [refs[-1]]
    for ref in reversed(refs[:-1]):
        if not _REF_RUN_GAP_RE.match(line[ref[1]:run[-1][0]]):
            break
        run.append(ref)
    return run


def _decline_targets(text: str) -> dict[int, set[int]]:
    """The references each decline is ABOUT, keyed by line — `DECLINE_RE` alone is not enough.

    A decline clears the refs it names and NO OTHERS. Computed here, once, so arm 1 and `claims()`
    cannot disagree about which claim an author answered (a second copy of this rule would be a
    second definition of "the author answered", and the disagreement would show up as a false PASS).

    The tests are stated on `_DECLINE_BRIDGE_RE` and its comment block; the shape here is:
      * a decline that NAMES its objects owns them, and nothing else. The objects are every `#N`
        the clause names (one, two, or a comma/`and` list) — never the ref that merely happens to
        sit before the sentence;
      * otherwise the decline is about the nearest reference PRECEDING it, whose bridge to the
        negation must be the short closed vocabulary on `_DECLINE_BRIDGE_RE` — anything else means
        the sentence has a SUBJECT OF ITS OWN. No preceding reference at all -> no target, because
        a decline that precedes every reference on its line is not evidence about any of them;
      * an alternative-(a) object that the scan CANNOT READ (a blanked inline code span or HTML
        comment, which `strip_code` turns into whitespace) is not evidence either: it falls to no
        target rather than to proximity. Measured at `049548f2c` (review, P3):
        ``Part of #11000 — deliberately not closing `#11078`.`` read CLEAN that way.
    """
    out: dict[int, set[int]] = {}
    for i, line in enumerate(text.splitlines()):
        refs = [(m.start(), m.end(), int(m.group("num"))) for m in _REF_POS_RE.finditer(line)]
        if not refs:
            continue
        targets: set[int] = set()
        for m in DECLINE_RE.finditer(line):
            # The OBJECT the sentence names, when it names one. Both alternatives capture it into
            # a group (`obj_a` lives inside alternative (a)'s lookahead — a group in a lookahead is
            # still captured), because the match TEXT of `deliberately not …` stops at `not` and
            # searching the match for a ref would find nothing.
            obj = (m.group("obj_a") or m.group("obj_b") or "")
            # ⛔ A DECLINE THAT NAMES ITS OBJECT OWNS IT. `_REF_OBJ` admits a literal `#N`, so
            # `Part of #11000 — deliberately not closing #11078.` declined `#11000` — the ref
            # BEFORE the sentence — and cleared it. Measured at `e7dd41058` (review, P2): the claim
            # `#11000` was silently dropped and arm 1 read CLEAN. A named object is the strongest
            # possible statement of what the sentence is about, so it OVERRIDES the proximity rule.
            #
            # EVERY object the clause names, not just the first, and by the GRAMMAR: `obj` is
            # captured through `_REF_LIST`, whose continuation must be a `#N` right after `,`,
            # `and`, `or` or `&`, so (`deliberately not closing #100, #200.`) names BOTH refs.
            # ⛔ DO NOT REINTRODUCE A "REGION"/"ZONE" SCAN. Two rounds measured it failing in both
            # directions, which is why what runs here is a captured list and not a text scan:
            #   * a scan to the CLAUSE END swept a LATER clause's refs into the decline and moved a
            #     claim from `weak` to `declined` (`Closes #100 — deliberately not closing #200 and
            #     Refs #300 for the follow-up.` read NOTHING FLAGGED, measured at `2219dfe86`, P2);
            #   * making the separator the clause-end class swept a FOLLOWING sentence's refs in
            #     (`… deliberately not closing #100. #300` lost `#300`, measured at `0e785300b`, P2).
            # The list therefore stops at its own last `, #N` / `and #N` / `or #N` / `& #N`.
            named = [int(x) for x in _REF_POS_RE.findall(obj)]
            if named:
                targets.update(named)
                continue
            # ⛔ AN UNREADABLE OBJECT IS NOT EVIDENCE. `strip_code` blanks an inline-code span or an
            # HTML comment to WHITESPACE, so `deliberately not closing `#11078`.` reaches this
            # function with its object GONE (the stem survives, the ref does not). Falling through
            # to proximity then cleared the ref BEFORE the sentence — the silent drop this rule
            # exists to stop (measured at `049548f2c`). An object region that STARTS with whitespace
            # and captured nothing readable is therefore no target at all. The clause-terminal
            # convention (`deliberately not Closes.`, `deliberately not closing.`) has punctuation
            # straight after the stem, so it keeps the proximity path and stays a decline.
            #
            # ⛔ THE TEST IS ON THE CAPTURED OBJECT, NOT ON THE REST OF THE LINE (measured at
            # `2219dfe86`): searching `_REF_NOUN` over `after_stem` matched `one\b` inside `none`,
            # `pr\b` inside `PR` and `it\b` inside `commit`, so appending any ordinary clause
            # (`… `#11078`, the review issue.`) re-opened the drop.
            # ⛔ AN UNREADABLE OBJECT IS NOT EVIDENCE — IN EITHER SPELLING, AND NOT EVEN WITH A
            # REASON ATTACHED. The guard used to be gated on `stem_a`, so the SAME sentence read two
            # ways depending on which spelling the author used (measured at `7e3fe379d`'s
            # predecessor, review P3):
            #     `Part of #11000 — deliberately not closing `#11078`.`   -> flagged (guard fires)
            #     `Part of #11000 — does not close `#11078`.`             -> CLEAN, `#11000` dropped
            # Both stems are captured so the guard covers both.
            #
            # ⛔ AND A REASON MUST NOT RESCUE A BLANKED OBJECT. `_REASON_TAIL` lets an honest
            # decline carry WHY, and its capture (`reason_a`/`reason_b`) is the one mechanical
            # signal that separates a reason from prose. The two are distinguishable because
            # `strip_code` blanks an inline-code SPAN **and an HTML COMMENT** to SPACES (length
            # preserved, newlines included): a blanked object leaves a whitespace run of two or
            # more characters between the stem and the reason, while an honest sentence has exactly
            # one space there.
            #     `does not close `#11078` because …`   -> 10 spaces  -> unreadable -> FLAGGED
            #     `no close intended.`                  ->  1 space   -> honest     -> declines
            # Both directions are pinned as a pair, so neither half can be "fixed" by tweaking the
            # other; without the run test the reason reading wins in both and the blanked-object
            # silent drop comes back through a new door.
            stem_end = (m.end("stem_a") if m.group("stem_a") is not None
                        else m.end("stem_b") if m.group("stem_b") is not None
                        else None)
            reason = m.group("reason_a") or m.group("reason_b")
            honest_reason = (reason is not None and stem_end is not None
                             and not re.match(r"[ \t]{2,}", line[stem_end:]))
            if stem_end is not None and not obj.strip() and not honest_reason:
                after_stem = line[stem_end:]
                # …but a clause that CONTINUES (`Closes and #200 is deliberately not Closes.`) also
                # begins with a space, and there the space is the conjunction's, not a blanked span's.
                # ⛔ A CONJUNCTION IS ADMITTED ONLY WHEN IT INTRODUCES THE NEXT REFERENCE
                # (`_CONJ_REF_RE`: a `#N` follows it). Admitting the bare word `and`/`or` was a
                # SILENT DROP one word over — a blanked object followed by `and <prose>`
                #     Part of #11000 — deliberately not closing `#3300` and this one is separate.
                # read CLEAN, rc 0, with `#11000` claimed and resolved by nothing (measured at
                # `6d8e4ed8b`, the residual the round before declared). A `#N` after the conjunction
                # is the one mechanical signal that it joins CLAUSES ABOUT REFERENCES rather than
                # continuing prose about the blanked object. Any OTHER word after the gap — a
                # conjunction that names no ref included — is the appositive or the rest of the
                # sentence, which cannot tell us what was blanked, so the claim stays flagged.
                head = re.match(r"[ \t]+([A-Za-z]+)?", after_stem)
                if head:
                    is_conj = (head.group(1) or "").lower() in ("and", "or")
                    if not is_conj or not _CONJ_REF_RE.match(after_stem):
                        continue
            before = [r for r in refs if r[1] <= m.start()]
            if not before:
                continue
            _, end, num = max(before, key=lambda r: r[1])
            if not _DECLINE_BRIDGE_RE.match(line[end:m.start()]):
                continue
            # `them` is PLURAL by construction, so it is about every reference in the list it
            # follows — `Refs #100, #200 — deliberately not closing them.` declined only `#200`,
            # leaving `#100` flagged on a sentence that answers both (measured at `e7dd41058`,
            # review P3). A singular object keeps the proximity rule.
            #
            # ⛔ THE RUN IS THE LIST, NOT THE LINE. Taking every ref before the decline on the line
            # swept a DIFFERENT SENTENCE's claim into the decline and arm 1 read CLEAN on a claim
            # nothing resolved — the direction this module ranks worst (measured at `0b6cb52ca`,
            # review P2):
            #     Refs #100. Refs #200, #300 — deliberately not closing them.   -> CLEAN, `#100`
            # `them` cannot refer to `#100` there: the two are separated by a full stop, not by a
            # list separator. The run walks back from the nearest preceding reference and stops at
            # the first gap that is not the list grammar's own separator (`_ref_run_back`).
            if re.search(r"\bthem\b", obj, re.IGNORECASE):
                targets.update(r[2] for r in _ref_run_back(line, before))
            else:
                targets.add(num)
        if targets:
            out[i] = targets
    return out


def _claims_in(text: str, own_number: int | None = None,
               repo: str | None = None,
               skipped: list[int] | None = None) -> dict[str, list[int]]:
    """`claims` over ALREADY-normalised text, so a caller that needs `hidden_lines` from the same
    pass does not strip the body twice (two passes of a pure function cannot disagree, but a caller
    that reported a count from one pass and classified from another would be reading two bodies).

    `skipped`, when supplied, receives every `owner/repo#N` claim dropped because the repo was
    UNKNOWN. Without that sink the drop is invisible: the same body then measures a SMALLER
    population offline than with `--repo`, and reports clean for content it did not read — which is
    the shape this module refuses everywhere else (`Unmeasurable`, never a pass).
    """
    found: dict[str, list[int]] = {"closing": [], "satisfies": [], "weak": [], "declined": [],
                                   "continued_resolving": []}
    decline_targets = _decline_targets(text)
    line_starts = [0]
    for line in text.splitlines(keepends=True):
        line_starts.append(line_starts[-1] + len(line))
    for m in CLAIM_RE.finditer(text):
        line_no = sum(1 for st in line_starts if st <= m.start()) - 1
        # Every line the CLAIM MATCH covers, so a decline written on a continuation ref's own line is
        # seen (see the note at the ref loop).
        last_line_no = sum(1 for st in line_starts if st <= m.end()) - 1
        declined_here_default: set[int] = set()
        for ln in range(line_no, last_line_no + 1):
            declined_here_default |= set(decline_targets.get(ln, ()))
        word = re.sub(r"\s+", " ", (m.group("kw") or m.group("ckw")).lower())
        # The claiming word governs its WHOLE same-line list (F3) — but a list continuation is a
        # CLAIM, never a RESOLUTION. GitHub credits only the FIRST ref of a closing keyword (its
        # documented multi-issue form is `Resolves #10, resolves #123` — the keyword repeated), and
        # `closes_landing.parse_closing_refs` credits exactly one. Crediting `#13` in
        # `Closes #12, #13` reported CLEAN on work nothing closed, which is the #10534 hole this
        # arm exists to shut (review P2). A continuation of a resolving word is still CLAIMED, so
        # it lands in `weak` and is still flagged.
        # ⛔ THE DECLINE IS LOOKED UP ON THE REF'S OWN LINE, NOT THE KEYWORD'S (review round 24, P2).
        # `line_no` is where the CLAIMING WORD sits; a continuation ref can be on the next line, and a
        # decline written THERE was discarded — so a markdown reflow flipped the verdict on the same
        # sentence: `Part of #10085,\n#10063 — deliberately not closing them.` flagged both refs while
        # the unwrapped one-liner declined them. Each ref now resolves its own line.
        refs = [(m.group("qual"), int(m.group("num")), False, m.start("num"))]
        more_start = m.start("more") if m.group("more") else m.end()
        refs += [(c.group("qual"), int(c.group("num")), True, more_start + c.start("num"))
                 for c in CONTINUATION_RE.finditer(m.group("more") or "")]
        for qual, num, continued, pos in refs:
            # The ref's OWN line, UNION every line the claim match covers: a named decline on the
            # continuation line (`Refs #100,\n#200 — #100 is deliberately not Closes.`) names a ref
            # that sits on the line above, and looking at only one of the two lines drops it.
            declined_here = decline_targets.get(
                sum(1 for st in line_starts if st <= pos) - 1, set()) | declined_here_default
            if own_number is not None and num == own_number:
                continue  # a PR quoting its own number from a review is not claiming an issue
            if qual and repo is None:
                # It MIGHT be this repo — we simply do not know — so it is neither claimed nor
                # silently forgotten; the caller reports the narrower population (F3 of round 3).
                if skipped is not None:
                    skipped.append(num)
                continue
            if qual and qual.lower() != repo.lower():
                continue  # `owner/repo#N` for another repo is not this PR's business
            if num in declined_here:
                found["declined"].append(num)
            elif word in CLOSING_WORDS:
                (found["weak"] if continued else found["closing"]).append(num)
                if continued:
                    found["continued_resolving"].append(num)
            elif word in SATISFIES_WORDS:
                (found["weak"] if continued else found["satisfies"]).append(num)
                if continued:
                    found["continued_resolving"].append(num)
            else:
                # A LINKING word never resolves anything, so its list continuation is not a
                # "first ref of a keyword" question at all — the FIRST-ref rule is about CLOSING
                # keywords only, and `Part of #A, #B` / `Refs #A, #B` are the DOMINANT shapes in
                # the measured population (review P3).
                found["weak"].append(num)
    for key in found:
        found[key] = sorted(set(found[key]))
    return found


def claim_issues(body: str, own_number: int | None = None) -> list[int]:
    """Every issue this body claims, whatever the strength of the claim."""
    c = claims(body, own_number)
    return sorted((set(c["closing"]) | set(c["satisfies"]) | set(c["weak"])) - set(c["declined"]))


def has_satisfies_checklist(comment_bodies: list[str]) -> bool:
    """A `## Satisfies` heading, plus a checklist THAT BELONGS TO IT.

    ⛔ ONE POPULATION-WIDE PREDICATE PAIR WAS A FALSE PASS (review round 25, P1). It was
    `any(headings) and any(boxes)` over the whole population, so a task list ANYWHERE — most often a
    `## Verification` check-list in the issue's own BODY, which 8 of the 40 newest open issue bodies
    carry — satisfied the checklist half for a `## Satisfies` comment that had NO criteria at all:

        [body with `- [ ]`s, IN FLIGHT, "## Satisfies\n\nTo be derived."]     -> NOTHING FLAGGED, rc 0
        [body WITHOUT them, IN FLIGHT, "## Satisfies\n\nTo be derived."]     -> wip-no-satisfies, rc 3

    The only difference is a task list written BEFORE scoping happened — i.e. the arm cleared exactly
    the defect it exists to report ("work started before the completion criteria existed"), which is
    the false-PASS direction this module ranks worst.

    ⛔ THE "TICKED BOX IN A LATER COMMENT" ARM IS GONE (review round 26, P2). It was justified as
    "criteria are ticked in a follow-up comment once proved, and that must not re-flag a finished
    issue" — and that justification is measurably false: a finished issue's criteria are `[x]` boxes
    in the HEADING's own comment, which the first arm already accepts. The arm only ever fired on the
    case its comment does not describe — a heading with NO criteria at all plus any later tick:

        [INV, "## Satisfies\n\nTo be derived.", "## Verification\n\n- [x] x"]  -> rc 0  (false PASS)
        [INV, "## Satisfies\n\nTo be derived.", "- [ ] unrelated"]           -> wip-no-satisfies

    i.e. a finished `## Verification` list cleared the exact defect this arm exists to report, which
    is the same false PASS round 25 closed one layer up. Both cases that motivate co-location-tolerance
    (`test_a_tick_in_a_later_comment_still_counts`,
    `test_the_checklist_must_belong_to_the_heading_and_a_LATER_tick_does_not_count`) keep their heading
    comment's own `- [ ]` boxes, so they stay green with the arm removed — verified by disabling the
    arm and re-running the file.

    The predicate is now: a box in the HEADING's own **SECTION**. An UNticked box after the heading
    is some other checklist, not this one — and so is a TICKED one.

    ⛔ AND "THE HEADING'S OWN COMMENT" IS STILL TOO COARSE (round 26, P2, measured in a parallel
    lane against the same head). `classify_issue` passes the issue's OWN BODY as ONE comment, so a
    body carrying an unrelated task list AND a criteria-less `## Satisfies` heading is a single
    element and `boxes[i]` was TRUE for it — the same false PASS one layer down, on round 25's own
    stated population ("most often a `## Verification` check-list in the issue's own BODY, which 8
    of the 40 newest open issue bodies carry"):

        ONE comment: `## Verification` + `- [ ]`s + `## Satisfies` + `To be derived.`  -> rc 0

    So the box must belong to the `## Satisfies` SECTION — the heading to the next ATX heading of
    any level (`_satisfies_sections`). The two narrowings compose: the later-tick arm is gone, and
    the co-located box must be in the heading's own section rather than anywhere in its comment.
    """
    # Read through `strip_code`, exactly as `in_flight_markers` does. A comment that SHOWS the
    # format inside a fence OR an indented code block is an example, not a checklist: matched against
    # the raw bodies it silently cleared the arm it exists to raise, on an issue with WIP and no real
    # criteria (F2). `indented_code` — not `strict` — is what hides the example without also hiding a
    # real criterion indented under a list item (round 29, P2).
    stripped = [strip_code(b, indented_code=True)[0] for b in comment_bodies]
    for text in stripped:
        sections = _satisfies_sections(text)
        if not sections:
            continue
        if any(CHECKBOX_RE.search(section) for section in sections):
            return True
    return False


def _satisfies_sections(text: str) -> list[str]:
    """Every `## Satisfies` SECTION of ONE comment: from the heading to the next ATX heading.

    ⛔ "THE HEADING'S OWN COMMENT" IS VACUOUS AT BODY GRANULARITY (round 26, P2). Round 25 replaced
    a population-wide `any(headings) and any(boxes)` with "a box in the HEADING's own comment",
    which closed the CROSS-comment case — but `classify_issue` passes the issue's OWN BODY as ONE
    element, and a body carrying both an unrelated task list and a criteria-less `## Satisfies`
    heading is a single comment. So the arm still cleared the defect it exists to report:

        `## Verification` + `- [ ]`s + `## Satisfies` + `To be derived.`   (ONE comment)
        -> has_satisfies_checklist TRUE -> classify_issue [] -> rc 0

    That is the round-25 note's own population ("most often a `## Verification` check-list in the
    issue's own BODY, which 8 of the 40 newest open issue bodies carry"), so this is the same fix
    completed rather than a second rule. A section ends at the next ATX heading of ANY level: a
    box under a LATER heading is that section's list, not this one's criteria.
    """
    sections: list[str] = []
    for match in SATISFIES_HEADING_RE.finditer(text):
        remainder = text[match.end():]
        # ⛔ THE SECTION ENDS AT THE NEXT ATX HEADING OF **ANY** LEVEL (round 28, P2). This was tried
        # as "same or higher level only" and that is a CLASS-A FALSE PASS: a box under any deeper
        # heading then became this section's criteria, so an unrelated `### Verification` list one
        # level down cleared a criteria-less `## Satisfies` — round 25/26's population re-opened. The
        # cost on the other side is a false positive on a `###`-per-criterion layout; a false PASS
        # outranks it, so ANY heading ends the section and the comment says so.
        nxt = _ATX_ANY_RE.search(remainder)
        sections.append(remainder[: nxt.start()] if nxt else remainder)
    return sections


def _marker_rest(line: str) -> str:
    """The text after the `IN FLIGHT:` marker, with markdown emphasis/bullets stripped.

    The remainder is the marker's OWN captured group, never `line.split(":", 1)[1]`. Splitting on
    the line's FIRST colon read a colon that was not the marker's, so a real marker sharing its line
    with other prose had its remainder truncated to the text between the two colons (review P1).
    The group is also the single definition of "after the marker", so the anchor in `IN_FLIGHT_RE`
    and this helper cannot disagree about where the marker ends.
    """
    match = IN_FLIGHT_RE.search(line)
    rest = match.group("rest") if match else ""
    rest = rest.strip()
    rest = rest.strip("*_`").strip()          # **IN FLIGHT:** / _IN FLIGHT:_
    rest = rest.strip("-–— ").strip()
    return rest


def in_flight_markers(comment_bodies: list[str]) -> list[str]:
    """Non-empty `IN FLIGHT: <ids>` markers — the durable WIP signal the process block mandates.

    Three shapes are NOT markers, because each is the line surviving into a state that is not WIP:
    an empty remainder, a remainder that is only the convention's placeholders (`<ids> — branch <b>
    …`, present whenever a comment *quotes* the block), and a released/none remainder.
    """
    markers: list[str] = []
    for body in comment_bodies:
        text, _ = strip_code(body, indented_code=True)
        for line in text.splitlines():
            if not IN_FLIGHT_RE.search(line):
                continue
            rest = _marker_rest(line)
            if not rest:
                continue
            if rest.lower().split()[0].strip(":.,;") in RELEASE_WORDS:
                continue
            # The marker is often ANNOTATED rather than replaced: `IN FLIGHT: 3.2c — released
            # 2026-11-05` / `IN FLIGHT: 3.2c (released)`. Reading only the FIRST token left both
            # as live WIP and flagged finished work (F6). Matched in an ANNOTATION POSITION only,
            # so a release word inside a branch slug cannot clear a real marker.
            # EMPHASIS wraps the word (`**released**`, `_released_`) and neither side's delimiter
            # class can admit it: `_` is a word character, so `\\b` never matches there.
            # Flattening the markers first makes both spellings one shape, and it cannot
            # manufacture a release because the word must still be a standalone token in an
            # annotation position.
            #
            # ⚠️ A CODE SPAN is deliberately NOT covered: `` `released` `` is a QUOTED word, and
            # `strip_code` has already blanked it before this line — the same doctrine that makes
            # `gh pr view #1234` a quotation rather than a claim (pinned by a test).
            if RELEASE_ANYWHERE_RE.search(rest.replace("*", "").replace("_", "").replace("`", "")):
                continue
            if "<" in rest and ">" in rest and PLACEHOLDER_RE.match(rest):
                continue  # the unsubstituted template
            markers.append(rest)
    return markers


# --- the two arms ---------------------------------------------------------------------------
def _subject(kind: str, number: int | str) -> str:
    """`PR #11078` for a real number, `PR body-file` for an OFFLINE source label.

    ⛔ A LABEL IS NOT A NUMBER (round 44 review, P3). The offline modes pass a source name
    (`"body-file"`, `"issue-body-file"`) through the same parameter, so the subject line printed
    `PR #body-file` — a name formatted as a number, which reads as an issue id nobody can open.
    """
    return f"{kind} #{number}" if isinstance(number, int) else f"{kind} {number}"


def classify_pr(body: str, number: int | str, repo: str | None = None,
                labels: list[str] | None = None,
                notes: list[str] | None = None,
                repo_unknown: str | None = None) -> list[Finding]:
    """ARM 1 — a PR that claims an issue which no keyword resolves.

    `notes`, when supplied, receives the UNTERMINATED-FENCE note for this body. It lives here rather
    than in each CLI branch because only two of the four body-reading modes collected it: a
    `--sweep` silently swallowed the one thing that says the scan could not see the rest of a body —
    and a sweep is the one mode that reads a POPULATION (review P2, adjacent).
    """
    # ARM 1 ONLY — `strict=True` (round 28, P2). This is the PASS path (`classify_pr`'s CLEAN
    # verdict), so it must not rest on a shape `closes_landing.parse_closing_refs(..., strict=True)`
    # refuses to pass on: an INDENTED block is code, and an inline span may cross a soft line break.
    # `claims()` above and `classify_pr` here are the two arm-1 readers; `has_satisfies_checklist` and
    # `in_flight_markers` deliberately do NOT take it — they pass `indented_code` instead, because a
    # 4-space line inside a list item is PROSE (a real checklist item, a real `IN FLIGHT:` marker)
    # while a top-level indented code block is a QUOTED example. Named by symbol, not by offset: the
    # offsets this comment used to carry (":1063"/":1130") rotted on the next commit.
    text, hidden = strip_code(body, strict=True)
    if hidden and notes is not None:
        notes.append(f"an unterminated fence hid {hidden} line(s) from the scan in {_subject('', number).strip()}")
    own = number if isinstance(number, int) and number > 0 else None
    skipped: list[int] = []
    c = _claims_in(text, own, repo, skipped)
    if skipped and notes is not None:
        why = repo_unknown or "the repo is unknown; pass --repo to count them"
        notes.append(f"{len(skipped)} owner/repo#N claim(s) in {_subject('', number).strip()} were NOT read — {why}")
    claimed = sorted((set(c["closing"]) | set(c["satisfies"]) | set(c["weak"]))
                     - set(c["declined"]))
    if not claimed:
        return []  # negative control: a PR that claims no issue is legitimate
    resolved = set(c["closing"]) | set(c["satisfies"])
    unresolved = [n for n in claimed if n not in resolved]
    if not unresolved:
        return []
    detail = (f"claims #{', #'.join(str(n) for n in unresolved)} with no closing keyword of its "
              f"own: neither `Satisfies #N: <ids>` nor a closing keyword resolves it.")
    if set(unresolved) & set(c["continued_resolving"]):
        # Only where it APPLIES: the FIRST-ref rule is a CLOSING-keyword rule (GitHub's documented
        # multi-issue form repeats the keyword), so it is irrelevant for a plain `Refs #N` claim AND
        # for a linking-word list (`Part of #A, #B`) — the dominant shapes in the measured
        # population (review P3).
        detail += (" GitHub closes only the FIRST ref of a keyword (`Resolves #10, resolves #123`), "
                   "so a comma-continued ref does not close it either.")
    detail += " Delivered work then cannot close what it serves."
    labels = labels or []
    if "owner-decision" in labels:
        # An owner-authored BODY is a record; the PR body is still the author's own, so the label
        # changes nothing. Stated so nobody "fixes" this by exempting it.
        detail += " (an owner-authored body is not exempt: the PR body is the author's)"
    return [Finding(arm=ARM_PR, subject=_subject("PR", number), detail=detail,
                    evidence={"claimed": claimed, "unresolved": unresolved,
                              "resolved_by_closing": c["closing"],
                              "resolved_by_satisfies": c["satisfies"]})]


def classify_issue(comment_bodies: list[str], number: int | str,
                   wip: bool | None = None,
                   notes: list[str] | None = None) -> tuple[list[Finding], dict]:
    """ARM 2 — WIP with no `## Satisfies` checklist.

    Returns (findings, measured) where `measured` carries the marker count, because "0 markers read"
    and "0 issues with WIP" are different claims and the caller must print the first.

    `notes`, when supplied, receives the UNTERMINATED-FENCE note for this issue's bodies. Arm 1 got
    this first (a `--sweep` swallowed it); the issue modes kept swallowing it, so the one thing that
    says "the scan could not see the rest of this body" was reported for a PR and silently dropped
    for the same content read as an issue — the same one-population-two-measurements shape as the
    discarded issue body below.
    """
    hidden = sum(strip_code(body)[1] for body in comment_bodies)
    if hidden and notes is not None:
        notes.append(f"an unterminated fence hid {hidden} line(s) from the scan in {_subject('', number).strip()}")
    markers = in_flight_markers(comment_bodies)
    if wip is None:
        is_wip = bool(markers)
        source = "comment-marker"
    else:
        is_wip = wip
        source = "injected"
    measured = {"wip_source": source, "markers": markers[:3], "marker_count": len(markers),
                "had_satisfies": has_satisfies_checklist(comment_bodies)}
    if not is_wip:
        return [], measured  # negative control: a fresh issue has no criteria yet, by design
    if measured["had_satisfies"]:
        return [], measured
    detail = ("has WIP but no `## Satisfies` checklist in its body or comments — work started "
              "before the completion criteria existed, so nothing can tell this issue it is "
              "finished (SCOPING posts that comment)")
    populations = ([] if source == "injected"
                   else ["branches", "worktrees", "ref-claims"])
    return [Finding(arm=ARM_ISSUE, subject=_subject("issue", number), detail=detail,
                    evidence={**measured, "populations_not_consulted": populations})], measured


# --- measurement (gh) ---------------------------------------------------------------------
def _gh(args: list[str], timeout: int = 60) -> str:
    try:
        proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise Unmeasurable(f"gh not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise Unmeasurable(f"gh {' '.join(args)} timed out after {timeout}s") from exc
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise Unmeasurable(f"gh {' '.join(args)} -> rc={proc.returncode}: {err[0] if err else ''}")
    return proc.stdout


def resolve_repo(repo: str | None) -> str | None:
    """The ambient `owner/name` — without it every self-qualified `owner/repo#N` claim is dropped."""
    if repo:
        return repo
    try:
        return json.loads(_gh(["repo", "view", "--json", "nameWithOwner"]))["nameWithOwner"]
    except (Unmeasurable, KeyError, json.JSONDecodeError):
        return None


def fetch_pr(number: int, repo: str | None) -> tuple[str, list[str]]:
    args = ["pr", "view", str(number), "--json", "body,labels"]
    if repo:
        args += ["--repo", repo]
    data = json.loads(_gh(args))
    return data.get("body") or "", [lb.get("name", "") for lb in data.get("labels") or []]


def fetch_issue(number: int, repo: str | None) -> tuple[str, list[str]]:
    args = ["issue", "view", str(number), "--json", "body,comments"]
    if repo:
        args += ["--repo", repo]
    data = json.loads(_gh(args))
    return data.get("body") or "", [(c.get("body") or "") for c in data.get("comments") or []]


def sweep_prs(repo: str | None, limit: int) -> list[tuple[int, str, list[str]]]:
    args = ["pr", "list", "--state", "open", "--json", "number,body,labels", "--limit", str(limit)]
    if repo:
        args += ["--repo", repo]
    return [(p["number"], p.get("body") or "", [lb.get("name", "") for lb in p.get("labels") or []])
            for p in json.loads(_gh(args))]


def sweep_issues(repo: str | None, limit: int, label: str | None) -> list[tuple[int, list[str]]]:
    args = ["issue", "list", "--state", "open", "--json", "number", "--limit", str(limit)]
    if label:
        args += ["--label", label]
    if repo:
        args += ["--repo", repo]
    out = []
    for row in json.loads(_gh(args)):
        # The issue's own BODY is a comment-shaped input, exactly as the offline
        # `--issue-body-file` branch says: a checklist or a claim may live there. Discarding it here
        # measured a strictly smaller population than the offline path for the SAME content, so an
        # issue whose `## Satisfies` checklist sits in its body read as *WIP with no checklist*
        # through `--sweep-issues` and *clean* through `--issue-body-file` (review P2). One
        # population, one measurement.
        body, bodies = fetch_issue(row["number"], repo)
        out.append((row["number"], [body, *bodies]))
    return out


# --- CLI ----------------------------------------------------------------------------------
def _note_truncation(notes: list[str], rows: list, limit: int, noun: str) -> None:
    """Report a sweep that stopped at its CAP instead of at the end of the population.

    `--sweep`/`--sweep-issues` make ONE `gh … list --limit N` call, so a result set of exactly N is
    indistinguishable from "there are exactly N" unless it is said out loud — and `gh` lists
    newest-first, so the subjects skipped are the OLDEST, which is precisely where arm 2's defect
    (work started before the completion criteria existed) concentrates. Every other narrowed
    population in this module is already named (`Unmeasurable`, the unread `owner/repo#N` claims,
    the unterminated fence, the "carried none" count); the truncation that can hide 97 % of the
    subjects was the one that was silent. `>=` rather than `==` so an off-by-one in `gh`'s own
    accounting can only over-report, never hide the bound.
    """
    if len(rows) < limit:
        return
    notes.append(
        f"--limit {limit} stopped this sweep at its CAP with {len(rows)} {noun}(s) read — `gh` "
        f"lists newest-first, so OLDER {noun}s were NOT read; this is a sample, not the population "
        f"(raise --limit to widen it)"
    )


def _comments_from_file(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return [raw]  # a bare markdown file is one comment body
    if isinstance(data, list):
        return [c if isinstance(c, str) else (c.get("body") or "") for c in data]
    if isinstance(data, dict):
        return [(c.get("body") or "") for c in data.get("comments") or []]
    return [raw]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Advisory: flag a PR claiming an issue that nothing resolves, and an issue with "
                    "WIP but no `## Satisfies` checklist. Never a gate (rc 0 clean / 3 findings / "
                    "2 unmeasured).")
    which = ap.add_mutually_exclusive_group(required=True)
    which.add_argument("--pr", type=int, help="classify one PR by number")
    which.add_argument("--issue", type=int, help="classify one issue by number")
    # ⚠️ NOT "every" open PR/issue: each sweep makes ONE `gh … list --limit N` call, so it reads
    # the newest `--limit` subjects and NOT the population. Calling that "every" (and `--limit` a
    # "page size") described a pagination loop that does not exist, and the truncation ran in the
    # HARMFUL direction — `gh` lists newest-first, so the oldest open subjects, exactly where
    # "WIP started before the criteria existed" lives, were the ones silently skipped.
    which.add_argument("--sweep", action="store_true",
                       help="classify the newest --limit open PRs (NOT the whole population)")
    which.add_argument("--sweep-issues", action="store_true",
                       help="classify the newest --limit open issues (NOT the whole population)")
    which.add_argument("--body-file", type=Path, help="offline: a PR body")
    which.add_argument("--issue-body-file", type=Path, help="offline: an issue body")
    ap.add_argument("--repo", default=None,
                    help="owner/name (default: resolved from gh in the LIVE modes only; pass it "
                         "explicitly to count owner/repo#N claims in the offline file modes)")
    ap.add_argument("--comments-file", type=Path, default=None,
                    help="offline issue mode: comment bodies (JSON list, gh-shaped, or markdown)")
    ap.add_argument("--wip", action="store_true",
                    help="inject a WIP verdict (e.g. tools/who_is_on.py found a branch/worktree) — "
                         "single-issue modes only")
    ap.add_argument("--no-wip", action="store_true", help="inject the opposite verdict")
    ap.add_argument("--label", default=None, help="--sweep-issues only: restrict the population")
    ap.add_argument("--limit", type=int, default=None,
                    help="--sweep/--sweep-issues: how many open subjects to read, newest-first "
                         "(one `gh` list call — a CAP, not a page count, and a run that hits it "
                         "says so). Refused elsewhere: no other mode has a population to cap "
                         "(default 50)")
    ap.add_argument("--annotate", action="store_true",
                    help="emit GitHub Actions ::warning lines (for a warn-only workflow step) "
                         "— mutually exclusive with --json")
    ap.add_argument("--json", action="store_true", help="emit JSON (mutually exclusive with --annotate)")
    args = ap.parse_args(argv)
    # `--limit` is read by the two sweeps and by NOTHING else, so on every other mode it used to be
    # accepted and silently ignored — the caller read the output as a capped population it never
    # asked for (review round 25, P4). The refusal below tests `args.limit` — the RAW flag — and
    # never `limit`: refusing on the RESOLVED value would fire on every run, because the default
    # 50 makes every run look explicit. (An earlier cut of this comment claimed the resolution
    # happened "AFTER the refusal below"; it happens here, and a comment that states what the code
    # does not do is the exact defect class this module's own rails call out.)
    limit = 50 if args.limit is None else args.limit

    wip: bool | None = None
    if args.wip and args.no_wip:
        ap.error("--wip and --no-wip are mutually exclusive")
    if args.wip:
        wip = True
    if args.no_wip:
        wip = False
    if wip is not None and (args.sweep or args.sweep_issues):
        # One issue's verdict cannot speak for a population — that would flag (or clear) every
        # subject on a state measured for one of them.
        ap.error("--wip/--no-wip answer ONE issue; they cannot be applied to --sweep/--sweep-issues")
    if wip is not None and not (args.issue is not None or args.issue_body_file is not None):
        # `wip` is consumed by `classify_issue` ALONE — the two PR modes run arm 1 only. So
        # `--pr 11027 --wip` (the line this module's own Usage block used to carry) injected a
        # verdict that was then DISCARDED, and because `measured_rows` stayed empty the run printed
        # no WIP line at all: the operator read arm 1's `rc 0` as covering the issue arm too. This
        # module refuses a flag it cannot honour rather than silently measuring a different
        # population than the one the flags name (the doctrine `--label`/`--sweep` and the
        # population modes already follow).
        ap.error("--wip/--no-wip answer ONE issue: they apply to --issue and --issue-body-file only")
    if args.label and not args.sweep_issues:
        # `--label` filters the ISSUE population (`sweep_issues`); `--sweep` reads PRs and the
        # single-subject modes read one subject, so neither has a label filter — so the combination
        # silently ran unfiltered while the caller believed the population was restricted (F4 for
        # `--sweep`; the same no-op on `--pr`/`--issue`/the two file modes). Refuse it everywhere it
        # cannot be honoured, as `--wip` is refused above.
        ap.error("--label applies to --sweep-issues only; the other modes read a fixed subject or "
                 "an unfiltered population, so the filter would be dropped in silence")
    if args.comments_file is not None and args.issue_body_file is None:
        # `--comments-file` supplies the COMMENT BODIES of the offline issue mode, and that is the
        # one mode that reads it. Elsewhere the file was dropped without a word, and `--issue` —
        # the mode a caller would reach for — then reported `0 comment(s)` (a count of gh comments)
        # with nothing saying the operator's file had been ignored. Same doctrine as `--wip` above:
        # a flag that cannot be honoured is refused, never read as a smaller population.
        ap.error("--comments-file supplies the comment bodies of --issue-body-file only; "
                 "the other modes read their comments themselves (or have none)")
    if args.limit is not None and not (args.sweep or args.sweep_issues):
        # `--limit` caps a SWEEP, and the two sweeps are the only modes with a population to cap —
        # every other mode reads one subject (or one file), so a `--limit 3` there was accepted and
        # dropped without a word while the caller believed the run had been narrowed. Same doctrine
        # as `--label` above: a flag that cannot be honoured is refused, never read as a population
        # the caller did not ask for.
        ap.error("--limit applies to --sweep/--sweep-issues only; the other modes read a fixed "
                 "subject, so the cap would be dropped in silence")
    if args.json and args.annotate:
        # `--annotate` is the mode a workflow step uses, and `--json` the machine mode; `main()`
        # dispatches `if args.json: … elif args.annotate: …`, so BOTH flags together ran the JSON
        # mode and the requested ::warning/::notice lines were never emitted — a step that asked
        # for annotations silently emitted none (review round 25, P4). There is no meaningful union
        # of the two surfaces, so they are refused together rather than resolved in silence.
        ap.error("--json and --annotate are mutually exclusive: --json emits one JSON document, "
                 "--annotate emits ::warning/::notice lines — passing both silently dropped the "
                 "annotations")

    findings: list[Finding] = []
    measured_rows: list[dict] = []
    notes: list[str] = []
    try:
        # Resolve the repo through `gh` ONLY in the modes that reach GitHub. The `--body-file` and
        # `--issue-body-file` modes are documented as OFFLINE, and making an unannounced network
        # call from them falsified that (and, on a box without `gh`, silently dropped every
        # self-qualified `owner/repo#N` claim — a smaller population measured without refusing).
        # An explicit `--repo` is still honoured offline; it just is not looked up.
        live_mode = bool(args.pr or args.issue or args.sweep or args.sweep_issues)
        repo = resolve_repo(args.repo) if live_mode else args.repo
        # WHY the repo is unknown decides the note's wording: in a file mode it is expected and
        # `--repo` fixes it; in a LIVE mode a gh failure made this population NARROWER than the
        # one the caller asked for, and saying "offline" there named a mode they were not in.
        repo_unknown = None if repo else (
            "the repo could not be resolved (gh failed) — this population is NARROWER than the "
            "one you asked for; pass --repo to count them"
            if live_mode else
            "the repo is unknown offline; pass --repo to count them")
        if args.body_file is not None:
            body = args.body_file.read_text(encoding="utf-8")
            findings += classify_pr(body, "body-file", repo, notes=notes, repo_unknown=repo_unknown)
            population = "1 body (offline)"
        elif args.issue_body_file is not None:
            bodies = _comments_from_file(args.comments_file) if args.comments_file else []
            body = args.issue_body_file.read_text(encoding="utf-8")
            # The issue's own body is a comment-shaped input: a checklist or a claim may live there.
            found, measured = classify_issue([body, *bodies], "issue-body-file", wip, notes)
            findings += found
            measured_rows.append(measured)
            population = f"1 issue body + {len(bodies)} comment(s) (offline)"
        elif args.pr is not None:
            body, labels = fetch_pr(args.pr, repo)
            findings += classify_pr(body, args.pr, repo, labels, notes=notes, repo_unknown=repo_unknown)
            population = "1 PR"
        elif args.issue is not None:
            # The body is a comment-shaped input on the LIVE path too — the offline sibling twelve
            # lines above has always said so; this one discarded it, so identical content got two
            # verdicts depending on whether it was read from gh or from a file (review P2).
            body, bodies = fetch_issue(args.issue, repo)
            found, measured = classify_issue([body, *bodies], args.issue, wip, notes)
            findings += found
            measured_rows.append(measured)
            population = f"1 issue ({len(bodies)} comment(s) + body)"
        elif args.sweep:
            rows = sweep_prs(repo, limit)
            for number, body, labels in rows:
                findings += classify_pr(body, number, repo, labels, notes=notes, repo_unknown=repo_unknown)
            population = f"{len(rows)} open PR(s)"
            _note_truncation(notes, rows, limit, "open PR")
        else:  # --sweep-issues
            rows = sweep_issues(repo, limit, args.label)
            for number, bodies in rows:
                found, measured = classify_issue(bodies, number, wip, notes)
                findings += found
                measured_rows.append(measured)
            population = f"{len(rows)} open issue(s)"
            _note_truncation(notes, rows, limit, "open issue")
    except Unmeasurable as exc:
        print(f"issue_process_lint: UNMEASURED — {exc}", file=sys.stderr)
        return RC_UNMEASURABLE

    marker_reads = sum(1 for m in measured_rows if m["marker_count"])
    markerless = sum(1 for m in measured_rows if not m["marker_count"] and m["wip_source"] == "comment-marker")

    if args.json:
        # ⛔ NO `wip_read` FOR AN ARM THAT NEVER RAN (review round 28, P3). The docstring says a
        # PR-side run "prints NO WIP line at all", and the human/`--annotate` modes keep that promise
        # via `if measured_rows:` — this branch did not, so `--body-file … --json` emitted
        # `"wip_read": {"subjects": 0, "with_marker": 0, "markerless": 0, …}`, which a machine
        # consumer cannot tell from an issue-mode run over a markerless population, plus a
        # `populations_not_consulted` sentence about an arm that was never evaluated at all.
        print(json.dumps({"population": population, "notes": notes,
                          "wip_read": ({"subjects": len(measured_rows),
                                       "with_marker": marker_reads,
                                       "markerless": markerless,
                                       "populations_not_consulted":
                                           # An INJECTED verdict CAME from that population, so
                                           # claiming it was not consulted is false (F7) — derive
                                           # it, do not hard-code it.
                                           ([] if measured_rows and all(
                                               m["wip_source"] == "injected" for m in measured_rows)
                                            else ["branches", "worktrees", "ref-claims"])}
                                       if measured_rows else None),
                          "findings": [f.as_dict() for f in findings]}, indent=2))
    elif args.annotate:
        for note in notes:
            print(f"::notice title=issue-process-lint::{note}")
        for f in findings:
            print(f.annotate())
        if measured_rows:
            # The annotation note must describe THIS run, exactly as the human/`--json` modes do —
            # an injected verdict CAME from the population, so calling it "not consulted" made one
            # run describe itself two incompatible ways (F7).
            if all(m["wip_source"] == "injected" for m in measured_rows):
                flag = "--wip" if wip else "--no-wip"
                print(f"::notice title=issue-process-lint::WIP verdict INJECTED ({flag}) from the "
                      f"branches/worktrees/ref-claims population tools/who_is_on.py owns")
            else:
                print(f"::notice title=issue-process-lint::WIP arm read {marker_reads} subject(s) "
                      f"carrying an `IN FLIGHT:` marker; {markerless} carried none "
                      f"(branches/worktrees NOT consulted — tools/who_is_on.py owns that population)")
        if not findings:
            print(f"::notice title=issue-process-lint::nothing flagged over {population}")
    else:
        print(f"issue_process_lint (advisory) — population: {population}")
        for note in notes:
            print(f"  ! {note}")
        if measured_rows and all(m["wip_source"] == "injected" for m in measured_rows):
            # ⛔ NO MARKER COUNT UNDER AN INJECTED VERDICT (review round 29, P3). `markerless` counts
            # only rows read from a `<!-- p01-status -->` COMMENT, so with an injected `--wip` verdict
            # the sentence read `1 subject(s) read — 0 carried an `IN FLIGHT:` marker, 0 carried
            # none`, a population that does not add up. That sentence exists precisely so "0 markers
            # read" and "0 with WIP" cannot be laundered into each other, so it may not print numbers
            # that describe an arm the caller did not run — the injection notice below says where the
            # verdict came from, and there is no marker count to report beside it.
            flag = "--wip" if wip else "--no-wip"
            print(f"  WIP arm: {len(measured_rows)} subject(s), verdict INJECTED ({flag}) from the "
                  "branches/worktrees/ref-claims population tools/who_is_on.py owns — no marker "
                  "count: this run did not read markers.")
        elif measured_rows:
            print(f"  WIP arm: {len(measured_rows)} subject(s) read — {marker_reads} carried an "
                  f"`IN FLIGHT:` marker, {markerless} carried none.")
            print("           branches/worktrees/ref-claims NOT consulted "
                  "(tools/who_is_on.py owns that population) — so 'no marker' is not 'no WIP'.")
        if not findings:
            print("  NOTHING FLAGGED")
        for f in findings:
            print(f"  ⚠️  {f.subject} [{f.arm}] — {f.detail}")
        print(f"  advisory only (rc {RC_CLEAN}/{RC_FINDINGS}/{RC_UNMEASURABLE}); "
              f"never a required check.")
    return RC_FINDINGS if findings else RC_CLEAN


if __name__ == "__main__":
    raise SystemExit(main())
