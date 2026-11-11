"""#11810 review (P2) — `platform-seams.md` and the shipped install message cannot drift apart.

WHY THIS PIN EXISTS. The `#2122` Cursor section of
`docs/research/2026-09-01-gbrain-learnings/platform-seams.md`
cites the `tortoise install cursor` message as the evidence that the cloud-agent
exclusion is **HOSTED**-scoped, and that record is what a future lane cites for
#11807. The r6 review measured the drift in the DOC direction: the record quoted
*"a session running in a cloud agent VM"* — the UNSCOPED universal #11806 carried
and this PR removes — while the shipped message says *"in a HOSTED cloud agent VM"*.
A fragment in double quotes that is not in its source reads as evidence FOR the
over-assertion it sits next to.

WHY ITS OWN FILE, REGISTERED IN `tier1` (as well as `api` and `core`). The guarded
artifact is under `docs/`, and `docs/` is in `NON_PYTHON_PREFIXES`, so a
**docs-only** diff selects NO surface and takes the tier1-only early return. The pin
originally shipped inside `tests/test_capture_install.py` — which is registered on
`api` + `core` and NOT in `tier1` — so on a docs-only edit of this very record it ran
**zero times**: a lane doing #11807's doc half could restore the unscoped quote,
merge green, and only red `main` post-merge (the `push` build takes the full
selection). That is the #4309/#8474/#8614 class, and every sibling doc pin
(`test_10000_cache_projection_doc_pin.py`, `test_10191_record_move_doc_pin.py`,
`test_8093_registry_namespace_doc.py`, …) is dual/triple-registered for exactly this
reason.

`api` is registered for the message-direction arm: a change to
`tortoise/__main__.py` matches `SOURCE_PATTERNS["api"]`, which REPLACES the `core`
fallback — measured, `printf 'tortoise/__main__.py\\n' | tools/ci_selection.py
--changed-files -` → `surfaces: ['api']`.

⚠️ Not claimed: that `api` (or `core`) is what makes the pin RUN. Review r10 (P3)
measured the opposite and this file now states it: `select()` unions `tier1` into
**every** PR selection (`files = set(tier1)`, then the surface's members), so this
file's `tier1` row ALONE selects the pin on both the docs-only diff and the
`__main__.py` diff, and the two surface rows add no coverage. They are kept
belt-and-braces as the durable home — `tier1` is a curated smoke list, and one
edit there would drop the pin silently — not because either row carries an arm.
`tests/test_ci_selection.py` measures both facts instead of asserting them in prose.

WHAT IT ASSERTS, in BOTH directions:

  (1) the record quotes the HOSTED-scoped fragment, and the unscoped WORDS do not
      reappear **in any form** — the r6 defect;
  (2) the shipped message really contains the fragment the record quotes — so
      dropping `hosted` from the message (or restoring the unscoped quote in the
      record) REDs instead of passing silently;
  (3) the record's OWN restatements of the same scoping — the `#2122` matrix row
      and the two items of that section (`Hosted cloud agents get NEITHER hook`
      and `"IDE-only" is NOT the right absolute`), which state the claim in words
      that are NOT the quoted fragment — are each still HOSTED-scoped. Without this
      arm every one of them could be reverted to #11806's universal with the whole
      file GREEN (measured at r18 for four of them, and at r19 for the three
      `"IDE-only" is NOT the right absolute` sentences r18 missed).
  (4) the customer promise #11806 added and this PR retracted — *"can get BOTH
      seams by checking a PROJECT-level `.cursor/hooks.json` into their
      repository"* — does not come back, AND the record keeps the reason it is
      false (the scripts register by absolute path and read `$HOME`). Pinned in
      both directions because r19 measured it pinned in NEITHER: re-instating the
      sentence left the whole file GREEN.
  (2b) the record's restatements of the **three hook sources** — project, team
      and enterprise-managed — each still name all three. Item 2 of this PR
      exists because #11806 named only the PROJECT-level source and that reads as
      exclusive; r21 measured two sites revertible to the one-source reading with
      the whole file GREEN, and found the PR body already claiming a
      `_HOOK_SOURCE_RESTATEMENTS` arm that did not exist. r22 then measured TWO
      MORE such sites (the quoted evidence, and the record's own summary of it).
      `_HOOK_SOURCE_RESTATEMENTS` now holds one site-unique needle per site, and
      the claim "all of them" is checkable against that tuple rather than stated
      as a number here.

`_SHIPPED_FRAGMENT` is the single string both arms bind to, so the two cannot be
corrected in opposite directions without this file noticing.

⛔ BOTH ARMS MATCH ON `_normalised()` — ONLY THE WORDS ARE THE CLAIM. Three
successive rounds shipped a form-exactness hole here, each one level deeper: r7
matched `("…")` only (a re-emphasis walked through); r8's bare-words fix was
still whitespace- and case-exact (a hard wrap inside the phrase walked through —
this record is hard-wrapped, and the `"IDE-only" is NOT the right absolute` sentence
"the same scoping applies to the
exclusion itself" is split across two lines; the 95-column reading is head-bound
and moves whenever that section gains a line above it); r9 stripped whitespace and
case but not PUNCTUATION, so a delimiter INSIDE the phrase walked through. Every
one of these is the same words and the same claim.

So `_normalised()` reduces text to its word sequence — HTML tags are stripped,
HTML entities are decoded, anything else that is not a letter or digit becomes a
separator, and case is folded. That closes the SEPARATOR-VALUED and
ENTITY/TAG-VALUED spellings of the same words **where the separator or the markup
falls BETWEEN words** (or wraps the phrase): reflow, tab/`\r\n`, literal nbsp,
zero-width space, case, hyphenation, markdown emphasis around or between words
(whole-phrase `*"…"*`, or in-phrase `**cloud agent VM**`, `` `cloud agent VM` ``,
`*cloud*`, `~~…~~`, `_…_`), and `&nbsp;` / `&#32;` / `&mdash;` / `<span>` / `<b>`
— the entity and tag forms render as the identical sentence, so a pin that only
blocked the literal characters would call the rendered artifact safe.
⚠️ "Between words" is load-bearing, not a hedge: a separator or tag INSIDE a word
(`clou<ZWSP>d`, `clou-`+`d`, `cl<b>ou</b>d`) does not respell the phrase, it
SPLITS it, so it is missed — the residual bullet below states this accurately and
this paragraph defers to it (review r12 P3 #3, which measured the unconditional
reading as the over-claim).

The PRESENCE arms read that ONE rendering. The ABSENCE arm reads `_renderings()`
— a union over every strip — because no single tag strip is complete and a single
one can be made to HIDE the banned phrase (both directions measured; see
`_renderings`).

WHAT IT STILL DOES NOT CATCH, stated so the docstring is not the next over-claim:

  * a PARAPHRASE, a synonym, a different word order, or a JOINED token
    (`cloudagent`) — beyond any string guard. `test_capture_install.py` keeps an
    ENUMERATED list for that and says so; #11812 holds the structural form.
  * a delimiter INSIDE a word (`clou\u200bd`, `clou\u00add`, `cl<wbr>oud`) splits
    the word rather than separating it, so the fragment is no longer a word
    subsequence. No editor produces these; measured as missed, recorded rather
    than claimed.
  * a HOMOGLYPH (`\u0441loud` rendering as `cloud`). That needs a confusables
    table; it is out of proportion for a docs pin, so it is a recorded residual
    rather than a fix. (Measured: entities/tags now caught, homoglyphs not.)
  * ⚠️ The PRESENCE arm reads ONE rendering (`_TAG`), so it CAN false-RED a
    truthful, render-identical record whose quoted fragment is split by a
    bare-attribute tag: `'a session running in a <span data-x>hosted</span>
    cloud agent VM'` → the fragment is NOT found, because that tag's words
    interpose (measured, review r15 P3). The ABSENCE leg is protected by the
    union; the presence leg cannot join it without turning arm 1 fail-open, so
    this is recorded rather than fixed — and no editor produces this shape.
  * this arm is deliberately OVER-BROAD in the safe direction: it bans the words
    wherever they appear in any order-adjacent form, so an HONEST correction that
    quotes the retracted wording to reject it — this record's own house style,
    see the `"IDE-only" is NOT the right absolute` sentence that names the retracted
    IDE-only label in order to
    reject it — REDs. Reword such an entry instead of reproducing the phrase
    verbatim. Likewise the presence arm answers "are these words in this file",
    not "is this an actual quote", so it cannot tell a quote from prose.
  * (3) pins the seven restatements as WORD SEQUENCES, so a legitimate RE-WORDING of
    the matrix row or of either item — even one that KEEPS the hosted scoping — REDs.
    That is the same trade arms (1)/(2) already take (the claim IS the words):
    reword such a sentence only together with its `_RESTATEMENTS` row, in one edit.
  * (4)'s PRESENCE needles are that SAME word-sequence trade: re-wording any of
    the four — even into a STRONGER sentence ("can never point at") — REDs, and
    the failure message reports it as "the record dropped WHY …", which
    mis-describes a re-word as a deletion. Reword it together with
    `_RETRACTION_CAUSE`, in one edit.
  * (2b) is the same trade again: each needle is the WHOLE three-source clause, so
    re-ordering it or swapping "managed" for a synonym REDs even when all three
    sources survive. That is deliberate — the one-source reading it guards against
    ("PROJECT-level" alone) is exactly a shortening of this sequence — and the
    remedy is the same: reword the record together with its
    `_HOOK_SOURCE_RESTATEMENTS` row.
  * (4)'s NEGATION guard is an ENUMERATED list, not a rule: it catches a negator
    prefixed to a `_RETRACTION_CAUSE` needle in the five forms listed, and MISSES any
    other wording of it ("fails to register by absolute path", "registers by a
    relative path", a negation AFTER the needle). Measured (r22): the enumerated
    forms are caught; a re-worded inversion is not. Same trade as the rest of this
    file — a closed list that states its bound beats a clever parser nobody owns.
  * …and it is OVER-BROAD too (r23): an honest CONTRAST reds — "which do not read
    their state from a repository, but from `$HOME`" is truthful and is reported as
    a negation of the code. One reword, or extend the needle with the object it
    negates. Recorded because a bound stated in one direction only is the r15-P3
    "guard rationale vs guard lines" class.

MUTATION (each verified RED at this head, restoring byte-identical):
  * restore the unscoped quote at the `"IDE-only" is NOT the right absolute`
    "settled for the **hosted VM**" sentence                   → (1) RED
  * re-add with OTHER delimiters, whole-phrase (`*"…"*`)      → (1) RED (r8)
  * re-add with a LINE BREAK / double space / capital           → (1) RED (r8)
  * re-add with in-phrase `**`, `` ` ``, `*`, `~~`, `_`         → (1) RED (r9)
  * re-add with a zero-width space or a hyphen                 → (1) RED (r9)
  * re-add with `&nbsp;`/`&#32;`/`&mdash;`/`<span>`/`<b>`        → (1) RED (r10)
  * correct quote with `**hosted**` emphasised → now PASSES    → was RED (P3)
  * drop `hosted` from `tortoise/__main__.py`'s message         → (2) RED
  * delete the record's quoted sentence                         → (1) RED
  * widen the tag strip back to `<[^>]+>` and inject one
    "a hook count of < 5 is suspect" before the record's quote → new tag test RED
  * `_renderings` → `[_normalised(text)]` (the `_TAG` reading only)
    → the bare-attribute sample REDs               (r12 P3 #1)
  * `_renderings` → the bare-attribute reading only
    → the prose-forgery sample REDs                (r12 P3 #1)
  * `_renderings` → both tag strips, no-strip reading dropped
    → the attribute-value sample REDs              (r13 P3)
  * strip the `PYTHONPATH` line out of `_install_cursor_stdout`'s env
    → direction 2 grades the installed package: RED on a worktree
    lane, false-PASS wherever the ambient tree carries the fragment
                                                   (r15 P1)
  * restore the orphan `'` after `agents` in `tortoise/__main__.py`
    → direction 2's raw-stdout punctuation assert REDs  (r15 P3 #7)
  * revert `_TAG` to the permissive `_TAG_WITH_BARE_ATTRS` form
    → the forged-tag PRESENCE assert REDs          (r12 P3 #1)
  * drop `hosted` from the record's `#2122` matrix row
    ("A hosted cloud agent runs neither")            → (3) RED   (r18 P2)
  * drop `Hosted ` from the `Hosted cloud agents get NEITHER hook` heading
                                                      → (3) RED   (r18 P2)
  * drop `**hosted**` from that item's "so a hosted Cursor cloud agent
    runs no Tortoise hook at all"                     → (3) RED   (r18 P2)
  * drop `Hosted-` from the "…pull only" consequence in that same item
                                                      → (3) RED   (r18 P2)
  * `it is settled for the **hosted VM**` → `**cloud agents**`
                                                      → (3) RED   (r19 P2)
  * `this row ("a hosted cloud agent")` → `("a cloud agent")`
                                                      → (3) RED   (r19 P2)
  * `scopes the exclusion to the` / `hosted VM` → `cloud agents`
                                                      → (3) RED   (r19 P2)
  * restore #11806's "can get BOTH seams by checking a PROJECT-level
    `.cursor/hooks.json` into their repository"        → (4) RED   (r19 P2)
  * delete the record's "repo-committed `hooks.json` cannot …" clause
                                                      → (4) RED   (r19 P2)
  * keep that bare "cannot" but delete the EVIDENCE clause it rests
    on (absolute path / `$HOME` / does not resolve)   → (4) RED   (r20 P2)
  * delete `plus team and enterprise-managed hooks on Enterprise` from the
    `#2122` matrix row                                 → (2b) RED  (r21 P2)
  * delete `(plus team and enterprise-managed hooks on Enterprise — the page's
    own gate)` from the `"IDE-only" is NOT the right absolute` item
                                                      → (2b) RED  (r21 P2)
  * reduce the quoted evidence to `command-based project hooks.`
                                                      → (2b) RED  (r22 P2)
  * reduce that item's own summary to `(the page names project hooks there)`
                                                      → (2b) RED  (r22 P2)
  * prefix a negator to a `_RETRACTION_CAUSE` needle ("which do **not** register
    by **absolute** path")                            → (4) RED  (r22 P3)
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import sys
from pathlib import Path

from markdown_it import MarkdownIt

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PLATFORM_SEAMS_DOC = (_REPO_ROOT / "docs" / "research"
                       / "2026-09-01-gbrain-learnings" / "platform-seams.md")
#: The fragment the record quotes AND the message must carry. One constant, so a
#: correction applied to one side only cannot satisfy both arms.
_SHIPPED_FRAGMENT = "a session running in a hosted cloud agent VM"
_UNSCOPED_FRAGMENT = "a session running in a cloud agent VM"
_PY = sys.executable

#: (3) — the record RESTATES the scoping in its own words SEVEN times, and THIS PR
#: scoped each of them. None of those words is `_SHIPPED_FRAGMENT`, so none was
#: covered until r18: deleting the single word that scopes any one of them restored
#: the pre-PR universal with the whole file GREEN (measured: four at r18, and the
#: three `"IDE-only" is NOT the right absolute` sentences the first arm missed, each
#: at r19).
#:
#: The SITE column names the record's own BOLD ITEM HEADING, never a section number.
#: This legend used to carry `§3.2`/`§3.3`, and those resolve NOWHERE: the record has
#: no such section (its only `§` literals point at OTHER documents), so the labels were
#: guidance that could not be followed — and two of the seven named the WRONG item even
#: where the numbering read plausibly (the "pull only" consequence sits in the
#: `Hosted cloud agents get NEITHER hook` item, while the three sentences beside it sit
#: in the `"IDE-only" is NOT the right absolute` item). A heading-named site cannot be
#: renumbered by a later edit, and `_RESTATEMENT_SITES` (index-aligned with the tuple
#: below) makes it checkable — see
#: `test_every_restatement_site_label_names_the_item_that_states_it` (review r27 P3 #1).
#:
#:   #  site                                   needle
#:   1. `#2122` matrix row                    A hosted cloud agent runs neither
#:   2. Hosted cloud agents get NEITHER hook   Hosted cloud agents get NEITHER hook
#:   3. Hosted cloud agents get NEITHER hook   a **hosted** Cursor cloud agent runs no …
#:   4. Hosted cloud agents get NEITHER hook   Hosted-cloud-agent users' … pull only
#:   5. "IDE-only" is NOT the right absolute   it is settled for the **hosted VM**
#:   6. "IDE-only" is NOT the right absolute   this row ("a hosted cloud agent")
#:   7. "IDE-only" is NOT the right absolute   scopes the exclusion to the hosted VM
#:
#: Rows 1/5/6/7 carry an ABSENCE leg as well; 2/3/4 do not. An absence leg is sound
#: only where the unscoped form is NOT a word subsequence of the scoped one —
#: `a cloud agent runs neither` breaks the sequence at `a _ cloud`, whereas
#: `a hosted cursor cloud agent runs no tortoise hook` CONTAINS `cursor cloud agent
#: runs no tortoise hook`, so banning the latter would RED the CORRECT text — the
#: precise false-RED this file's own review rounds are about. Hence `None` there
#: rather than a guess. Each verdict below was measured with `_normalised()` over
#: the pair before it was written, not assumed from the words.
_RESTATEMENTS: tuple[tuple[str, str | None], ...] = (
    ("a hosted cloud agent runs neither", "a cloud agent runs neither"),
    ("hosted cloud agents get neither hook", None),
    ("a hosted cursor cloud agent runs no tortoise hook", None),
    ("hosted cloud agent users volunteering memory is therefore pull only", None),
    ("it is settled for the hosted vm", "it is settled for cloud agents"),
    ('this row ("a hosted cloud agent")', 'this row ("a cloud agent")'),
    ("scopes the exclusion to the hosted vm", "scopes the exclusion to cloud agents"),
)

#: The SITE of each `_RESTATEMENTS` row, aligned BY INDEX with the tuple above (so a
#: row added without a site reds the length assert in the label guard rather than
#: silently inheriting its neighbour's). Each site is the record's own BOLD ITEM
#: HEADING, matched as WORDS against a SINGLE LINE, so a re-WORDING dies visibly —
#: but a REFLOW that moves part of the phrase onto the next line does NOT survive
#: it: the heading would stop resolving and the guard would RED, misreporting an
#: honest re-wrap as stale guidance (measured: both item sites return "" after an
#: in-phrase wrap). That bound is recorded at `_record_site_block`; it fails
#: CLOSED, so it can over-block but never pass a moved sentence off as found.
#: `_MATRIX_ROW` is the contracts-table row, which is not inside any numbered item.
_MATRIX_ROW = "matrix row"
_ITEM_NEITHER_HOOK = "Hosted cloud agents get NEITHER hook"
_ITEM_NOT_ABSOLUTE = '"IDE-only" is NOT the right absolute'
_RESTATEMENT_SITES: tuple[str, ...] = (
    _MATRIX_ROW,           # A hosted cloud agent runs neither
    _ITEM_NEITHER_HOOK,    # the heading itself
    _ITEM_NEITHER_HOOK,    # the item's result sentence
    _ITEM_NEITHER_HOOK,    # the item's consequence ("pull only")
    _ITEM_NOT_ABSOLUTE,    # "it is settled for the **hosted VM**"
    _ITEM_NOT_ABSOLUTE,    # "this row (\"a hosted cloud agent\")"
    _ITEM_NOT_ABSOLUTE,    # "scopes the exclusion to the hosted VM"
)

#: The `_RESTATEMENTS` rows (1-based, index-aligned with the tuple above) whose
#: restatement needle is ALREADY CONTAINED in their own site phrase. For those the label
#: guard's SECOND assert cannot vary: `_record_site_block` returns a block only when
#: some item line already carries the site's word sequence, so for row 2 —
#: `_ITEM_NEITHER_HOOK` naming the very heading it restates — a passing `assert block`
#: has already proved the sentence is "in" the block (`review r30 P3 #1`, measured: for
#: every other row a moved sentence or a re-pointed label reds the second assert, and
#: for row 2 NO mutation can red it while the first passes). ⛔ `site != _MATRIX_ROW` there
#: is DEFENSIVE, not load-bearing: the `_MATRIX_ROW` block is located by its TABLE PREFIX
#: (`| #2122`), never by the site's words, so the entailment this paragraph relies on does
#: not hold for row 1 — deriving its membership from containment would silently drop that
#: row's independent content check (review r34 P3 #2). ⚠️ Measured DECORATION today, and
#: described as such: row 1's needle is not contained in the site string `matrix row`, so
#: dropping the clause yields the same `{2}` (review r36 P3 #3). It is kept because it is
#: what makes the reason above TRUE of the code, not because a current row needs it. The predicate is CONTAINMENT
#: (`needle ⊆ site`), not equality: an equal phrase is the case that occurs today, but a
#: needle that is a proper part of its site is entailed just the same, and an equality
#: test would let such a new row inherit the dead leg silently — precisely the guarantee
#: this derivation exists to give. Such a row is not dead weight — the FIRST assert is
#: its whole job, and it reds the moment that heading is re-worded or the item deleted —
#: but the second assert would be an assertion whose value cannot vary, which is the dead
#: weight this file's own review rounds remove (r19 P2: the second assert removed beside
#: `test_no_single_tag_strip_can_hide_the_unscoped_phrase`, which was unfalsifiable by
#: construction — named by CONSTRUCT, not by line: a line number here went stale the
#: next time this file grew, the rule `tests/test_markers.py` states for its own table).
#: It is skipped BY DERIVATION below and the derived set is asserted, so a NEW circular
#: row cannot inherit the dead leg silently, and a row that STOPS being circular cannot
#: lose its content check.
_CIRCULAR_SITE_ROWS = frozenset({2})

#: (4) — the customer promise THIS PR RETRACTED, and the correction that replaced it.
#:
#: #11806's correction log told a customer that *"an Enterprise self-hosted-pool
#: user can get BOTH seams by checking a PROJECT-level `.cursor/hooks.json` into
#: their repository"*. That is false about this code: the shipped scripts are
#: registered by ABSOLUTE path out of `~/.cursor/hooks/`
#: (`tortoise/hook_install.py`, `absolute_command=True`) and read their state from
#: `$HOME` (`tortoise/cursor-hooks/session-start.sh`), so a repo-committed
#: `hooks.json` cannot name another machine's home, the command does not resolve on
#: the worker — and Cursor hooks fail OPEN, so the customer would believe they
#: shipped a seam that never fires (the unshipped-seam claim #2122 forbids).
#:
#: Pinned in BOTH directions, because r19 measured the record pinned in NEITHER:
#: restoring that exact sentence left the whole file GREEN — the absence arms banned
#: only `_UNSCOPED_FRAGMENT` and the four `_RESTATEMENTS` sequences, and
#: `test_capture_install.py` reads the message's stdout, never the record. That is
#: item 4 of this PR one claim over: a retraction with no absence guard.
#:
#: ⚠️ Same over-broad trade as arm (1): an honest entry that quotes the retracted
#: wording verbatim REDs. Reword such an entry instead of reproducing the phrase.
_RETRACTED_PROMISE = "get both seams by checking a project level"

#: …and the record must keep SAYING why the promise is false — ALL FOUR parts, not
#: just the "cannot". A single needle that stops at `cannot` was measured GREEN on a
#: record reduced to "a repo-committed `hooks.json` cannot simply point at this
#: install's scripts." with the whole evidence clause deleted (review r20 P2): the
#: arm then pins a bare assertion with no ground, which is exactly the drift the
#: comment above names. Without the reason the retraction reads as an arbitrary
#: exclusion and the next lane re-adds the promise as an attractive route.
_RETRACTION_CAUSE: tuple[str, ...] = (
    "repo committed hooks json cannot",   # the claim itself
    "register by absolute path",          # hook_install.py absolute_command=True
    "read their state from home",         # session-start.sh ${HOME}/.tortoise/…
    "would not resolve on a worker",      # …so the command does not resolve there
)

#: ⚠️ Each cause needle is a CONTIGUOUS word run, so a negator inserted BEFORE it is
#: invisible to the presence arm: measured at r22, "which do **not** register by
#: **absolute** path" / "and do **not** read their state from `$HOME`" left the whole
#: file GREEN while the record asserted the OPPOSITE of `absolute_command=True` —
#: the one claim arm (4) exists to protect. Enumerated, not general: each is the
#: direct negation of a `_RETRACTION_CAUSE` needle, because a closed list is honest
#: about what it covers and a cleverer rule would be a parser nobody maintains.
#: A differently-worded negation is a recorded residual (see the module docstring).
#:
#: ⚠️ It is also OVER-BROAD in the other direction, and that bound is stated rather
#: than hidden: an honest CONTRAST reds — "which do not read their state from a
#: repository, but from `$HOME`" and "which do not register by absolute path on the
#: worker" are truthful and are reported as a negation of the code. Measured (r23).
#: The remedy is one reword, or extending the needle with the object it negates;
#: the trade is accepted because the alternative is a negation parser nobody owns.
_RETRACTION_NEGATIONS: tuple[str, ...] = (
    "do not register by absolute path",
    "do not read their state from",
    "does not register by absolute path",
    "never register by absolute path",
    "does not read their state from",
)

#: (2b) — the THREE hook sources, at EVERY site where the record restates them.
#:
#: Item 2 of this PR exists because #11806's correction named only the
#: PROJECT-level source, and naming one of three reads as EXCLUSIVE. Every site
#: that states all three was measured revertible to the one-source reading with the
#: whole file GREEN: two at r21 (the `#2122` matrix row and the
#: `"IDE-only" is NOT the right absolute` item's consequence sentence, while the PR
#: body meanwhile claimed this very arm existed), and two
#: more at r22 (the quoted evidence itself, and the record's own summary of it —
#: sites the r21 arm's "both places" claim had missed). The counts live in that
#: history, never in a standing claim: r21 said "two" and r22 measured four.
#:
#: Site-UNIQUE needles: a shared one would let a revert at one site hide behind
#: another still naming all three. The tuple IS the completeness claim — there is
#: deliberately no count here, so adding a site means adding a needle rather than
#: editing a number that can drift.
_HOOK_SOURCE_RESTATEMENTS: tuple[str, ...] = (
    "project hooks plus team and enterprise managed hooks on enterprise",           # #2122 matrix row
    "command based project hooks and on enterprise also run team and enterprise managed hooks",  # quoted evidence
    "the page names project team and enterprise managed hooks there",               # record's own summary
    "registration plus team and enterprise managed hooks on enterprise",            # that item's consequence
)

#: A tag's attributes AS MARKUP — `name=value`, quoted or bare. NOT `[^<>\n]*`:
#: a bare word list is PROSE, and `if a<b and c>d then …` is an ordinary sentence,
#: so a permissive attribute group forges a "tag" out of it and deletes
#: everything between the two comparison signs. The ABSENCE leg survives that either
#: way (it unions three strips, so no single one can hide a phrase), but the
#: PRESENCE legs read ONE strip, and with the permissive form a record whose quote
#: sits between a prose `<` and `>` false-REDs — a correct record failing the pin.
#: Measured both ways; pinned by
#: `test_a_stray_angle_bracket_cannot_swallow_the_quoted_sentence`.
_TAG_ATTRS_ASSIGNED = (r"(?:\s+[A-Za-z_:][-A-Za-z0-9_:.]*\s*=\s*"
                       r"(?:\"[^\"]*\"|'[^']*'|[^\s\"'=<>`]+))*")

#: …and a TAG-SHAPED strip, deliberately not `<[^>]+>`. The fully loose form spans
#: any `<` … `>` anywhere in a 50 KB document, so one ordinary sentence — "a hook
#: count of < 5 is suspect" — swallows everything up to the next `>`. Measured on
#: THIS record with that sentence inserted as its own line IMMEDIATELY BEFORE the
#: quote in the `"IDE-only" is NOT the right absolute` item: `<[^>]+>` matches ONE span of 2,485 characters / 2,498 bytes that
#: CONTAINS the quoted words, so it deletes them and false-REDs the presence arm on
#: a record that states the right thing; `_TAG` matches nothing containing the
#: quote. ⚠️ That figure is RECIPE-dependent and must not be hand-copied as a
#: constant: an earlier revision of this comment carried a `2,481` that does NOT
#: reproduce at this head under the recipe above — and the record blob is
#: byte-identical to the revision that wrote it, so the cause is a mis-stated or
#: differently-injected measurement, NOT record growth (review r17 P3 measured
#: this after an earlier draft of this very sentence blamed a smaller record).
#: ⚠️ The measured reason `_TAG` is preferable is NOT that it keeps prose
#: angle-brackets — it does not. `<harness>` and `<ws>` are tag-SHAPED, so BOTH
#: forms strip them. Over this record `<[^>]+>` strips 314 bytes (28 spans) and
#: `_TAG` 179 (23 spans), and the whole 135-byte difference is five spans that
#: are not tags: `<state.db>`, `<session_id>`, `<lookup_key>`, `<message shown to
#: user when blocked>` and `<CustomMessage, "customType"|…>`.
#: `_TAG`'s bound is TAG-SHAPED — not bracket-free and not line-bounded. Its
#: quoted attribute values are OPAQUE (`"[^"]*"` / `'[^']*'`), so a `<`, a `>` and
#: a newline are all admissible INSIDE one. Measured at this head (review r15 P2,
#: corrected by this round's VGATE — the first draft of THIS comment repeated
#: r13's error in a new form):
#:   * inside a quoted value: `"<a b=\"x > y\">"`, `"<a b=\"x < y\">"` and
#:     `"<a b=\"line1\nline2\">"` ALL match — so "cannot cross a `<` or a `>`" is
#:     false as an absolute;
#:   * between attributes: a newline is crossable only where the next token is an
#:     attribute ASSIGNMENT (`\s+` precedes the name, and `\s` includes LF), so
#:     `"<a\nb=\"1\">"` matches while `"<a\nb>"` and `"<a\r\nb>"` do not;
#:   * …and the tag TAIL also admits whitespace (`\s*/?>`), so `"<a\n>"` and
#:     `"<a b=\"1\"\n/>"` match too — a newline before the closing `>` is
#:     crossable with no assignment. Both of these are whitespace INSIDE markup;
#:     neither is a span across prose, which is the property below.
#:   * it can never run from a `<` in ordinary PROSE to a later `>`: the name must
#:     start with a LETTER and every attribute must be an assignment, so in
#:     `a hook count of < 5 is suspect and <b>bold</b>` the matches are `<b>` and
#:     `</b>` and nothing spans from the `< 5` (measured; `re.match(_TAG, "< 5 …")`
#:     is None). That is the whole reason this strip cannot eat a quoted sentence.
#: The third property — not line-boundedness, and not bracket-freedom — is the
#: bound it buys; `test_a_stray_angle_bracket_cannot_swallow_the_quoted_sentence`
#: is where it is pinned. (r13's commit message also quoted
#: `_TAG_WITH_BARE_ATTRS`'s `(\s[^<>\n]*)?` group as if it were `_TAG`'s — the two
#: forms do not share an attribute grammar, and that conflation is where the
#: original over-claim came from.)
_TAG = rf"</?[A-Za-z][A-Za-z0-9-]*{_TAG_ATTRS_ASSIGNED}\s*/?>"
#: A strip that ADMITS bare (boolean) attributes, e.g. `<span hidden>`. Kept as a
#: second absence rendering, never as the only one — see `_renderings`.
#: ⚠️ Its bound is NOT `_TAG`'s (r13, measured): it cannot cross a `<` or a `>`, and
#: its attribute part consumes at most ONE **newline**, and only immediately after
#: the tag name — after that one `\s`, `[^<>\n]*` accepts any amount of HORIZONTAL
#: whitespace (`<a  b>`, `<a b c d>` and `<a\tb\tc>` all match), so "one
#: whitespace character" would be the wrong quantifier. `re.fullmatch` matches
#: `"<a\nb>"`, does not match `"<a\r\nb>"`, and
#: `"<b\nhere is a session running in a hosted cloud agent VM >"` matches with the
#: quoted words INSIDE the match. So it is tag-shaped, not line-bounded, and it can
#: swallow a line. That is tolerable only because the absence leg unions it with
#: `_TAG` and a no-strip reading: this form can never be the sole judge. To forbid
#: the newline here too, narrow `\s` to `[ \t]` — a behaviour change, deliberately
#: not made, and no longer load-bearing for the absence leg.
_TAG_WITH_BARE_ATTRS = r"</?[A-Za-z][A-Za-z0-9-]*(\s[^<>\n]*)?/?>"


def _normalised(text: str, tag_pattern: str | None = _TAG) -> str:
    """The WORDS of `text`, for BOTH arms — markup, punctuation and case gone.

    `tag_pattern=None` strips NO markup (the third absence rendering).

    HTML tags are removed and HTML entities decoded BEFORE the word collapse, so
    `&nbsp;`, `&#32;`, `&mdash;`, `<span>` and `<b>` — which RENDER as the same
    sentence — cannot carry a banned phrase past the guard. Everything that is
    then not a letter or digit becomes a separator, and case is folded, which
    makes reflow, tab/`\\r\\n`/literal nbsp, zero-width space, hyphenation and
    markdown emphasis INDISTINGUISHABLE from the plain words, including a
    delimiter in the MIDDLE of the phrase.

    What it deliberately does NOT do, so this docstring cannot become the next
    over-claim: no accent folding, no synonym or word-order handling (a
    paraphrase is out of reach of any string guard), and no HOMOGLYPH folding —
    `\\u0441loud` renders as `cloud` but needs a confusables table, which is out of
    proportion here and is a recorded residual.

    One function for every arm, so they cannot drift apart — the failure this
    file exists to catch, one level up. The PRESENCE arms read it once; the
    ABSENCE arm reads it through `_renderings`, which applies it to each of three
    tag strips and takes the union.
    """
    if tag_pattern is not None:
        text = re.sub(tag_pattern, " ", text)
    return re.sub(r"[\W_]+", " ", html.unescape(text).lower()).strip()


def _renderings(text: str) -> list[str]:
    """Every render-identical reading of `text` — the ABSENCE leg judges ALL of them.

    ⚠️ No single tag strip is complete, and each one is fail-OPEN in the opposite
    direction from the other (both measured, review r12 P3 #1):

      * a strip admitting BARE attributes reads prose as a tag — `if a<b and … c>d`
        loses everything between the comparison signs, banned phrase included;
      * `_TAG`, whose attributes must be ASSIGNED, keeps that prose but leaves a
        bare-attribute tag's words INTERPOSED, so
        `A <span hidden>session running in a cloud agent VM` is never adjacent to
        the words that make it the banned phrase.

    The UNION closes both: a phrase counts as present if ANY reading shows it, so
    no single strip can hide it. The third reading strips nothing at all, which
    covers prose that forges a tag no pattern anticipates AND a phrase carried in
    an ATTRIBUTE VALUE — `<b title="…">` — which both tag strips legitimately
    remove (measured; pinned by the `attribute-value` sample, r13 P3). A cost of 3
    normalisations over a 50 KB record, paid once per phrase checked — deliberately
    NOT stated as a number of tests, because a hand-written count re-stales on the
    next case this file adds (review r19 P3, and this repo's own recorded lesson).
    """
    return [_normalised(text, p) for p in (_TAG, _TAG_WITH_BARE_ATTRS, None)]


def _absent(fragment: str, text: str) -> bool:
    """True only if `fragment` survives in NO rendering — the fail-closed question.

    `any`, not `all`: the absence leg must RED if any reading reveals the words,
    which is why over-stripping one rendering can no longer manufacture a PASS.
    """
    needle = _normalised(fragment)
    return not any(needle in rendering for rendering in _renderings(text))


#: The RECORD's item markers, matched once and used by the LOCATOR — and nothing else,
#: since the item's EXTENT is the parser's (below). Every shape the record can spell an
#: item as must be here, because a locator that MISSES a shape leaves the site
#: unresolvable, and a locator that matches a line the parser does not make an item gets
#: no extent — `_parser_item_end` returns `None`, the block is "", and the first assert
#: REDs. ⛔ Both of those fail CLOSED. That is the opposite of the pre-parser rule, where
#: a locator wider than the hand-computed boundary was a fail-OPEN that let a MOVED
#: sentence read as "in" the labelled item (review r28 P3 #1 measured a bold-numbered
#: `**3.** …` pseudo-item leaving `8 passed`; r41 P3 #1 caught this line still justifying
#: itself with that deleted boundary). Hence `\d+[.]` AND `\d+)` AND a `**`-wrapped number.
_ITEM_START = re.compile(r"^(?:\*\*)?\(?\d+[.)](?:\*\*)?\s")

#: ⛔ The item's EXTENT comes from the CommonMark parser, not from a boundary this file
#: computes. Six review rounds are the evidence for why a hand-rolled terminator loses:
#: every rule that enumerated marker shapes let a shape through (r34 P3 #1 column-0
#: bullets, r35 P3 #1 indented furniture and table rows, r36 P2 #1 blockquotes, fences,
#: setext rules and plain paragraphs), and every rule that replaced the enumeration with
#: an INDENTATION test traded one error for the other: column-0-only swallowed a
#: blank-separated de-indent (r38 P3 #1), terminating on any less-indented line falsely
#: reddened LAZY continuations and a pure renumber (r39 P2 #1), and gating on a blank
#: line let a paragraph-INTERRUPTING construct at 1-2 spaces stay inside the block while
#: `markdown_it` renders it OUTSIDE the item (r40 P2 #1).
#:
#: `markdown-it-py` is already a declared test dependency (`pyproject.toml`'s `test`
#: extra, added so `tests/test_blog_authoring_doc.py` could import it), and it IS the
#: CommonMark reference implementation the review rounds kept measuring against. So the
#: block is the `list_item_open` token's own `map`, and there is no boundary left for a
#: future edit to disagree with. Measured identical to every boundary this file has
#: produced on the current record: `764 / 1393 x3 / 2309 x3`.
#:
#: ⚠️ The LOCATOR below stays a local regex: an item is found by its BOLD HEADING's
#: words, which the parser cannot answer for us without the same needle match.


def _parser_item_end(doc: str, start: int) -> int | None:
    """The line index one past the end of the list item that BEGINS on `start`.

    The parser is the authority for where an item ends (see the note above
    `_ITEM_START`): a hand-rolled boundary reddened honest edits and passed moved
    sentences in the same six rounds. `None` when no `list_item_open` token starts on
    that line — the caller treats that as "cannot locate", which REDs, so an unexpected
    document shape fails CLOSED.
    """
    for token in MarkdownIt("commonmark").parse(doc):
        if token.type == "list_item_open" and token.map and token.map[0] == start:
            return token.map[1]
    return None


def _record_site_block(doc: str, site: str) -> str:
    """The record text `site` names — the contracts-table row, or one numbered item.

    An item runs from its own first line (`2. **Heading …**`) to the line the COMMONMARK
    PARSER ends it on (`_parser_item_end`). Nothing here computes a boundary: six review
    rounds measured a hand-rolled one failing in both directions (see the note above
    `_ITEM_START`), and the parser is already a declared test dependency. An item is
    still LOCATED by its BOLD HEADING's words, not by its NUMBER — a renumbering cannot
    make a label point at a neighbour.

    Returns "" when the site cannot be located, so a re-worded heading REDs the
    label guard. Returning the whole document instead would make every label pass
    wherever its sentence had MOVED to, which is the false PASS this guard exists
    to prevent (review r27 P3 #1: two rows named the wrong item, and nothing could
    tell).

    ⚠️ Recorded bound: the site is matched against ONE LINE, not a reflowed block. An
    honest re-wrap that moves part of the heading's phrase onto the next line leaves
    no single line carrying the needle, so the block is "" and the label guard REDs —
    misreporting a pure reflow as a re-worded heading. Measured on both item sites
    (1393/2309 chars before, 0 after an in-phrase wrap). It fails CLOSED: the wrong
    direction here is a false block, never a sentence found wherever it moved.

    ⚠️ Recorded bound: the `_MATRIX_ROW` block is the FIRST `| #2122` table row (the
    contracts row today — it is the only one). A second `| #2122` row added elsewhere
    would not widen it, but would also not be reachable as a site; the row's needle is
    the only thing pinned there either way.
    """
    # `split("\n")`, not `splitlines()`: the parser counts `\n` only, and a
    # document carrying a `\x0c`/`\u2028` would otherwise shift every index after
    # it — a false RED on an intact item, reported as "the site does not resolve"
    # (review r42 P3 #1, measured).
    lines = doc.split("\n")
    if site == _MATRIX_ROW:
        return next((ln for ln in lines if ln.startswith("| #2122")), "")
    needle = _normalised(site)
    start = next((i for i, ln in enumerate(lines)
                  if _ITEM_START.match(ln) and needle in _normalised(ln)), None)
    if start is None:
        return ""
    end = _parser_item_end(doc, start)
    if end is None:
        return ""
    return "\n".join(lines[start:end])


def _install_cursor_stdout(tmp_path) -> str:
    """The REAL `tortoise install cursor` output, on an isolated HOME.

    Not a source scrape: the pin is on what a customer is told, so it reads the
    same stdout `test_capture_install.py` reads. `TORTOISE_DB_URI=""` keeps it
    DB-free so the tier1 (URI-less) legs can run it.
    """
    root = tmp_path / "proj"
    home = tmp_path / "home"
    root.mkdir()
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "TORTOISE_DB_URI": "",
        "TORTOISE_SECRET_PEPPER": "test-static-pepper",
    }
    # ⛔ THE TREE UNDER TEST MUST BE WHAT `-m tortoise` IMPORTS (review r15 P1).
    # `cwd` is `tmp_path`, so `-m tortoise` resolves from `sys.path`; without this
    # the subprocess grades whatever `tortoise` sits in the interpreter's
    # environment. In CI that is the checkout — right, but by ACCIDENT — while in a
    # worktree lane it is the HUB checkout, so this arm returned a verdict about a
    # tree nobody had reviewed (measured: RED here against a real defect, and a
    # silent false-PASS whenever the ambient tree happened to carry the fragment).
    # Prepended, not replaced, so an outer PYTHONPATH's support paths still resolve.
    env["PYTHONPATH"] = str(_REPO_ROOT) + (
        os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    r = subprocess.run(
        [_PY, "-m", "tortoise", "install", "cursor", "--dir", str(root)],
        env=env,
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def test_the_record_quotes_the_HOSTED_scoped_fragment():
    """Direction 1 — the record's quote, not the message's.

    Mutation: drop `hosted` from the quoted fragment (the r6 drift), or restore
    the unscoped quote beside it — both RED here."""
    doc = _PLATFORM_SEAMS_DOC.read_text(encoding="utf-8")
    normalised_doc = _normalised(doc)
    assert _normalised(_SHIPPED_FRAGMENT) in normalised_doc, (
        "the platform-seams record must quote the HOSTED-scoped message")
    # …and the unscoped WORDS may not reappear at all, in ANY form. Four rounds
    # shipped a form-exact leg here and each false-passed the mutation it exists to
    # catch — r7 a whole-phrase re-emphasis (`*"…"*`), r8 a hard wrap inside the
    # phrase on this 95-column-wrapped record, r9 a delimiter in the MIDDLE of the
    # phrase (`**cloud agent VM**`, `` `cloud agent VM` ``, `*cloud*`, `~~…~~`,
    # `_…_`, a zero-width space, a hyphen), r10 render-identical markup
    # (`&nbsp;`, `&#32;`, `&mdash;`, `<span>`, `<b>`). Matching on the word sequence
    # closes the spelling class instead of enumerating it; the docstring's residual
    # list names what it still cannot see (paraphrase, homoglyph) and the two places
    # it is deliberately over-broad. The assert goes through `_absent`, not a single
    # rendering, so a tag strip that swallows prose cannot hide the words either.
    assert _absent(_UNSCOPED_FRAGMENT, doc), (
        "the record quotes the unscoped universal as though it were the message")


def test_every_restatement_of_the_scoping_in_the_record_is_HOSTED_scoped():
    """(3) — the record states the claim SEVEN times, not only where it quotes the message.

    The `#2122` matrix row, and the two items of that section (`Hosted cloud agents
    get NEITHER hook` and `"IDE-only" is NOT the right absolute`), put the same
    scoping in their own words, and THIS PR edited every one of them:

      `A cloud agent runs neither`            → `A hosted cloud agent runs neither`
      `Cloud agents get NEITHER hook`         → `Hosted cloud agents get NEITHER hook`
      `a Cursor cloud agent runs no …`        → `a **hosted** Cursor cloud agent runs no …`
      `Cloud-agent users' …`                  → `Hosted-cloud-agent users' …`
      `it is settled for **cloud agents**`    → `it is settled for the **hosted VM**`
      `this row ("a cloud agent")`            → `this row ("a hosted cloud agent")`
      `scopes the exclusion to cloud agents`  → `scopes the exclusion to the hosted VM`

    Unpinned, any one of those seven reverts #11806's over-assertion inside the
    record — the very artifact a future lane cites for #11807 — while this file
    stays GREEN. That is the same false-PASS class as the message's missing
    absence leg, one surface over (review r18 P2 measured the first four; review
    r19 P2 measured the last three, which r18's arm did not reach).

    Mutation: drop the scoping word from any of the seven — each REDs here, and
    rows 1/5/6/7 RED both their presence and their absence leg."""
    doc = _PLATFORM_SEAMS_DOC.read_text(encoding="utf-8")
    normalised_doc = _normalised(doc)
    for scoped, unscoped in _RESTATEMENTS:
        assert _normalised(scoped) in normalised_doc, (
            f"the record no longer states this restatement as HOSTED-scoped: "
            f"{scoped!r} — #11806's universal is back on that line")
        if unscoped is not None:
            assert _absent(unscoped, doc), (
                f"the record states the UNSCOPED result again: {unscoped!r}")


def test_the_item_extent_is_the_parsers_map():
    """`_parser_item_end` IS the boundary, so it is pinned hermetically (r42 P2 #1).

    The whole guard rests on this one expression (`token.map[1]`), and before this test a
    ONE-CHARACTER edit to it (`map[1] + 1`) kept every assertion in this file green while
    re-opening the fail-OPEN six rounds closed: the block grows past the item, a sentence
    that moved onto the NEXT item's first line reads as "in" the labelled one (measured).

    Small inline documents, not the record: a pin on the record would still pass with an
    off-by-one, because the record's items are separated by `###` headings whose text the
    needles do not occur in.
    """
    plain = "1. **A** first\n   cont\n\n2. **B** second\n"
    assert _parser_item_end(plain, 0) == 3      # includes the trailing blank line
    assert _parser_item_end(plain, 3) == 4      # the last item ends at EOF

    nested = "1. **A** first\n   - sub point\n     sub cont\n\n2. **B** second\n"
    assert _parser_item_end(nested, 0) == 4     # an indented sub-bullet is INSIDE

    outside = "1. **A** first\n\nout of the item\n\n2. **B** second\n"
    assert _parser_item_end(outside, 0) == 2    # blank-separated de-indent is OUTSIDE

    lazy = "1. **A** first\ncol0 lazy line\n\n2. **B** second\n"
    assert _parser_item_end(lazy, 0) == 3       # a lazy continuation stays INSIDE

    assert _parser_item_end("not a list\n", 0) is None


def test_every_restatement_site_label_names_the_item_that_states_it():
    r"""(3, labels) — the legend's SITE must resolve AND hold the sentence it claims.

    The `_RESTATEMENTS` legend is the map a next rewriter follows to find the seven
    places this PR scoped. It carried `§3.2`/`§3.3` labels, and the record has no
    such section at all — so the map could not be followed, and two rows named the
    WRONG item while reading perfectly plausible (review r27 P3 #1: the "pull only"
    consequence is stated in the `Hosted cloud agents get NEITHER hook` item, the
    three sentences beside it in `"IDE-only" is NOT the right absolute`). Guidance
    that misdirects is how the NEXT round re-derives what this one already measured.

    So the labels are pinned twice over, and each half is a distinct failure:

      * a re-worded or removed heading leaves `_record_site_block` with no block →
        the first assert, because a label that names nothing is the defect itself;
      * a sentence MOVED to another item resolves to a block that does not contain
        it → the second assert, which the "whole document" reading this guard
        deliberately avoids (`_record_site_block` never falls back to the full text)
        is the only thing that can catch.

    ⚠️ The halves are distinct for SIX of the seven rows. Row 2's site IS its
    restatement needle (`Hosted cloud agents get NEITHER hook`), so locating the block by
    that phrase already proves the phrase is in it — its second assert can never vary,
    and is skipped by derivation (`_CIRCULAR_SITE_ROWS`), with the DERIVED set asserted so
    a new row whose needle is contained in its site cannot inherit the same dead leg
    (review r30 P3 #1). Row 2 keeps its whole job: a re-worded heading reds the first
    assert.

    Mutation (measured): move any restatement's sentence into the sibling item →
    the second assert REDs naming that needle and the site; re-word either heading
    → the first assert REDs naming the site; move a sentence into a **BOLD-NUMBERED**
    pseudo-item (`**3.** …`) inserted before the real item → RED here, at every head:
    the extent is the parser's `map`, so there is no boundary to narrow. (Narrowing the
    LOCATOR to `^\d+\.\s` also REDs, by a different route: the pseudo-item becomes
    unlocatable, so the real item is used and the sentence is measured outside it —
    measured, `1291` chars and the needle absent. The old "GREEN if narrowed" clause
    described the hand-computed boundary deleted at r40, review r41 P3 #2.) The LOCATOR
    still has to cover every shape the record can spell an item as.

    ⛔ The EXTENT is the parser's. Six rounds measured a hand-rolled boundary failing in
    BOTH directions: it swallowed a sentence moved into a column-0 bullet, a table row, a
    blockquote, a fence, a setext rule, a plain paragraph, and a blank-separated
    de-indent (r34 P3 #1, r35 P3 #1, r36 P2 #1, r38 P3 #1, r40 P2 #1 — every one a FALSE
    PASS), and it falsely REDded an honest sub-bullet added inside an item and a pure
    RENUMBER (r36 P2 #2, r39 P2 #1 — a FALSE FAIL, the direction this guard must not
    have). The block is now the `list_item_open` token's own `map` from `markdown-it-py`,
    the CommonMark implementation those rounds measured against.

    ⚠️ DECLARED RESIDUAL, not covered and deliberately so: a sentence moved into a
    sub-bullet (or any indented furniture) that is INSIDE the labelled item still reads
    GREEN — it IS item content, and ending the block at an indented marker is exactly the
    false RED r36 P2 #2 filed (measured: block 1395, needle present, identical to the
    pre-parser answer). The guard catches a sentence that LEAVES the item, not one that
    moves within it.

    ⚠️ The extent is only as good as `_parser_item_end`, so THAT is pinned by its own
    hermetic test (`test_the_item_extent_is_the_parsers_map`): before the pin existed,
    an off-by-one there kept every OTHER assertion in this file green while re-opening
    the fail-OPEN (r42 P2 #1, measured then, on the then-8-test file: `map[1] + 1` →
    all 8 tests still passed, and the moved sentence read GREEN). At this head the same
    mutation reds that one pin instead — `RED: ['test_the_item_extent_is_the_parsers_map']`
    with the other 8 passing — which is the whole point of it.

    Measured identical on the current record: `764 / 1393 x3 / 2309 x3`."""
    doc = _PLATFORM_SEAMS_DOC.read_text(encoding="utf-8")
    assert len(_RESTATEMENT_SITES) == len(_RESTATEMENTS), (
        "every `_RESTATEMENTS` row needs a site label — the legend's map is one "
        "row per restatement")
    circular = frozenset(
        row for row, ((scoped, _unscoped), site) in enumerate(
            zip(_RESTATEMENTS, _RESTATEMENT_SITES, strict=True), start=1)
        if _normalised(scoped) in _normalised(site) and site != _MATRIX_ROW)
    assert circular == _CIRCULAR_SITE_ROWS, (
        f"the rows whose restatement needle is already contained in their site are "
        f"{sorted(circular)}, not {sorted(_CIRCULAR_SITE_ROWS)} — a row that became "
        f"circular just lost its independent content check, and a row that stopped "
        f"being circular needs that check back; re-derive `_CIRCULAR_SITE_ROWS` from "
        f"the two tuples")
    for row, ((scoped, _unscoped), site) in enumerate(
            zip(_RESTATEMENTS, _RESTATEMENT_SITES, strict=True), start=1):
        block = _record_site_block(doc, site)
        assert block, (
            f"the site {site!r} that the `_RESTATEMENTS` legend names does not resolve "
            f"in the record — a re-worded heading or a deleted item leaves the legend "
            f"pointing at nothing")
        if row in circular:
            # Locating the block BY this row's own words already proved they are in it;
            # see `_CIRCULAR_SITE_ROWS`. Asserting it again would be an assertion whose
            # value cannot vary, and this file removes those rather than carrying them.
            continue
        assert _normalised(scoped) in _normalised(block), (
            f"{scoped!r} is not stated in the item the legend names ({site!r}) — the "
            f"label is stale, or the sentence MOVED to another item")


def test_the_retracted_project_level_promise_cannot_come_back():
    """(4) — the false customer promise is gone, and the correction survives it.

    Review r19 P2 measured this record pinned in NEITHER direction: re-instating
    #11806's sentence — *"an Enterprise self-hosted-pool user can get BOTH seams by
    checking a PROJECT-level `.cursor/hooks.json` into their repository"* — left the
    whole file GREEN. The absence arms cover `_UNSCOPED_FRAGMENT` and the
    `_RESTATEMENTS` word sequences; none of them matches this claim, and the
    message arm reads stdout rather than the record. So the one statement in this
    PR that was **wrong about the code** had no guard at all, which is precisely
    the defect item 4 of this PR fixes one surface over.

    Mutation: restore the retracted sentence anywhere in the record → the ABSENCE
    leg REDs (measured at this head). Drop ANY one of the four
    `_RETRACTION_CAUSE` needles — including deleting the whole evidence clause
    while keeping the bare "cannot" → the PRESENCE leg REDs (measured). Insert a
    negator BEFORE a cause needle ("which do **not** register by **absolute**
    path") → the `_RETRACTION_NEGATIONS` ABSENCE leg REDs (measured, r22)."""
    doc = _PLATFORM_SEAMS_DOC.read_text(encoding="utf-8")
    assert _absent(_RETRACTED_PROMISE, doc), (
        "the record promises the seams again via a repo-committed PROJECT-level "
        "hooks.json — the shipped scripts register by ABSOLUTE path under "
        "~/.cursor/hooks/ and read their state from $HOME, so that command does not "
        "resolve on a worker and Cursor hooks fail OPEN: the user would believe "
        "they shipped a seam that never fires")
    normalised_doc = _normalised(doc)
    for needle in _RETRACTION_CAUSE:
        assert _normalised(needle) in normalised_doc, (
            f"the record dropped WHY a repo-committed hooks.json cannot reach "
            f"these seams ({needle!r} is gone) — a bare 'cannot' with no ground "
            f"reads as an arbitrary exclusion, and the next lane re-adds the "
            f"promise as an attractive route")
    for negated in _RETRACTION_NEGATIONS:
        assert _absent(negated, doc), (
            f"the record carries a NEGATED form of the ground the retraction "
            f"rests on ({negated!r}) — if this is an honest CONTRAST (\"do not read "
            f"their state from a repository, but from $HOME\"), reword it; if it "
            f"negates the shipped behaviour, drop it")


def test_every_hook_source_restatement_names_all_three_sources():
    """(2b) — item 2 of this PR, at EVERY site where the record restates it.

    #11806's correction named only the PROJECT-level source, and naming one of
    three reads as EXCLUSIVE — which is the defect item 2 of this PR fixes, in the
    message and in the record. Review r21 measured two record sites unpinned:
    deleting the "plus team and enterprise-managed hooks on Enterprise" clause from
    EITHER the `#2122` matrix row or that item's consequence sentence left the whole file
    GREEN, while the PR body already claimed a `_HOOK_SOURCE_RESTATEMENTS` arm that
    did not exist at this head. Review r22 then measured TWO MORE — the quoted
    evidence, and the record's own summary of it — so r21's "both places" claim was
    itself the over-claim it was fixing, one level down. That is why the tuple, not
    a number, is the completeness claim.

    Site-UNIQUE needles: a shared one would let a revert at one site hide behind
    another still naming all three.

    Mutation: delete the three-source clause at ANY site → this REDs (measured at
    this head, one site at a time, all four)."""
    normalised_doc = _normalised(_PLATFORM_SEAMS_DOC.read_text(encoding="utf-8"))
    for needle in _HOOK_SOURCE_RESTATEMENTS:
        assert _normalised(needle) in normalised_doc, (
            f"a restatement of the three hook sources no longer names all three: "
            f"{needle!r} is gone — naming only the PROJECT-level source reads as "
            f"exclusive, which is the defect item 2 of this PR fixes")


def test_a_stray_angle_bracket_cannot_swallow_the_quoted_sentence():
    """The strip itself, pinned — review r10 measured this fail-open direction.

    `_normalised` is the only thing between the record and both arms, so its tag
    strip is load-bearing: the loose `<[^>]+>` runs from ANY `<` to the next `>`,
    so one ordinary half-sentence ("a hook count of < 5 is suspect") deletes
    everything up to the following `>`, including a correctly quoted sentence,
    and the pin would RED a record that is right. Hermetic: no file, no install.

    Mutation: widen `_TAG` back to `<[^>]+>` — this REDs (measured; the loose
    strip removes the quoted words from the sample below)."""
    sample = ('a hook count of < 5 is suspect. §3 says '
              '"a session running in a hosted cloud agent VM" and <b>bold</b>.')
    assert _normalised(_SHIPPED_FRAGMENT) in _normalised(sample), (
        "a stray `<` in ordinary prose must not eat the quoted sentence")
    # ⚠️ `_TAG_WITH_BARE_ATTRS` does NOT bite on this sample — measured (r15 P3 #5):
    # its tag name must start with a LETTER, so `< 5` cannot open a match. That is
    # why `_TAG`'s ASSIGNED-attribute narrowing is the presence arm and the
    # permissive form is never used as one.
    #
    # ⛔ A second assert lived here until r19 and is REMOVED, not reworded. It read
    # `not _absent(_UNSCOPED_FRAGMENT, prose_forgery)` on a sample that spelled the
    # banned phrase out, which is unfalsifiable BY CONSTRUCTION: `_absent` is
    # `not any(needle in rendering …)` and `_renderings` always includes the
    # no-strip reading, so a fragment that literally appears in the text is always
    # seen. Measured (r19 P2): GREEN under the loose `<[^>]+>` mutant it was
    # credited to, and GREEN even with the no-strip reading dropped — an assert no
    # mutation can redden, credited as coverage (the r15 P3 #5 class again). The
    # property it named is carried by the attribute-value sample in
    # `test_no_single_tag_strip_can_hide_the_unscoped_phrase`, which exercises a
    # genuine single-reading blind spot (r15 P3 #6 precedent: remove, do not
    # reword, a sample no reading demonstrates).
    #
    # …and the PRESENCE leg reads ONE strip (`_TAG`), so an `a<b … c>d` forgery must
    # not delete the quote there either. This is what makes `_TAG`'s
    # ASSIGNED-attribute narrowing load-bearing rather than decorative: the absence
    # union survives the permissive form, this assert does not.
    forged_around_the_quote = ('if a<b and §3 says "a session running in a hosted cloud agent VM" '
                               'c>d, the quote must survive.')
    assert _normalised(_SHIPPED_FRAGMENT) in _normalised(forged_around_the_quote), (
        "a prose `a<b … c>d` must not be read as a tag and eat the quote")


def test_no_single_tag_strip_can_hide_the_unscoped_phrase():
    """Review r12 P3 #1 — the attribute group, in EVERY fail-open direction.

    The absence leg takes the union (`_renderings`) precisely because each single
    reading hides the banned phrase in a case another catches. Each sample below is
    MISSED by at least one reading, and the three mutants are chosen so that each
    one reddens a DIFFERENT sample:

      * PROSE FORGERY — `if a<b and … c>d` is an ordinary sentence, but a strip
        admitting bare attributes reads `<b and … c>` as a tag and deletes the
        banned phrase between the comparison signs → false PASS;
      * BARE ATTRIBUTE — `A <span hidden>session running in a cloud agent VM`
        renders as the banned sentence, and `_TAG` alone leaves `<span hidden>`
        interposed, so the words are never adjacent → false PASS;
      * ATTRIBUTE VALUE — `<b title="a session running in a cloud agent VM">` is
        removed by BOTH tag strips (the `title` value is advisory UI, not page
        text, so a strip legitimately drops it), and ONLY the no-strip reading
        sees the words. This is the sample that makes the THIRD reading
        load-bearing; neither sample above reddens without it.

    Mutation, one per sample, so the loop demonstrates its own coverage:

      * `_renderings` → `[_normalised(text)]` (the `_TAG` reading only)
        → the bare-attribute sample REDs;
      * `_renderings` → the bare-attribute reading only
        → the prose-forgery sample REDs;
      * `_renderings` → both tag strips, no-strip reading dropped
        → the attribute-value sample REDs.

    Two corrections shaped this paragraph. Review r13 P3 measured the PREVIOUS
    wording FALSE — it claimed the no-strip mutant reddened the prose arm of the
    stray-`<` test above, which stays GREEN because `_TAG` alone sees that phrase;
    the attribute-value sample is what gives the third reading a sample it actually
    carries. Review r15 P3 #6 then measured the former `tag-wrapped` sample as
    coverage this file cannot demonstrate — EVERY rendering, strips included,
    already sees it, so no single-reading mutant could ever redden it — and it is
    REMOVED rather than left in the loop. What remains is one sample per mutant."""
    prose_forgery = ('if a<b and the record says "a session running in a cloud agent VM" c>d, '
                     'the pin false-PASSes.')
    bare_attribute = ('A <span hidden>session running in a cloud agent VM</span> is still the '
                      'unscoped universal, rendered.')
    attribute_value = ('A badge: <b title="a session running in a cloud agent VM">x</b> is the '
                       'unscoped universal carried in an attribute value.')
    for name, sample in (("prose-forgery", prose_forgery),
                         ("bare-attribute", bare_attribute),
                         ("attribute-value", attribute_value)):
        assert not _absent(_UNSCOPED_FRAGMENT, sample), (
            f"the absence leg must see the unscoped phrase in the {name} sample")


def test_the_shipped_message_carries_the_fragment_the_record_quotes(tmp_path):
    """Direction 2 — the message the record cites.

    Mutation: drop `hosted` from `tortoise/__main__.py`'s install message — the
    record's quoted fragment is then no longer produced, and this REDs.

    ⛔ AND THE r13 PUNCTUATION FIX IS PINNED HERE (review r15 P3 #7). This PR's
    copy once read `…in cloud agents', and that 'User-level hooks…` — three
    apostrophes, so the `'` after `agents` closed a quotation that was never
    opened. Nothing pinned it: `test_capture_install.py` asserts
    `"that 'sessionStart' is not available in cloud agents"`, which is a substring
    of BOTH the broken and the balanced text, and the arms above strip punctuation.
    A revert therefore merged green. These two asserts read RAW `stdout` —
    punctuation IS the claim here, so `_normalised` is the wrong lens.
    """
    stdout = _install_cursor_stdout(tmp_path)
    assert _normalised(_SHIPPED_FRAGMENT) in _normalised(stdout), stdout
    assert "agents, and that 'User-level hooks" in stdout, stdout
    assert "agents', and that 'User-level hooks" not in stdout, stdout
